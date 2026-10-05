"""One atomic-write primitive for every user-owned file PMCP rewrites.

Every PMCP-owned file that is rewritten whole -- the credential stores
(``pmcp.env``, ``.env.pmcp``), the trust store, package approvals, the registry
cache, and the client config ``pmcp setup --write`` emits -- goes through
:func:`atomic_write`. The write is atomic: the bytes go to a temporary file in
the destination's directory, are ``fsync``-ed, and are ``os.replace``-d over
the destination, so a write that fails partway leaves the old file byte-intact
(Consiliency/pmcp#248).

**A symlinked destination is written through, not replaced.** Users keep these
files in a dotfiles repository and symlink them into place. ``os.replace`` over
the *link* would swap the link for a regular file, after which the dotfiles copy
is never updated again and the two drift apart silently -- the regression the
#248 atomic write introduced against 2.7.3, whose plain ``open(path, "w")``
followed the link. So the destination is resolved first (following a chain of
links and relative links), the temporary is created in the **target's**
directory (``os.replace`` is only atomic within one filesystem), and the replace
lands on the **target**. The link itself is never touched.

**A path a repository controls is confined to that repository.** Following a
link is only safe when the operator made the link. ``<project>/.env.pmcp`` lives
inside a checkout, and a cloned repository can ship it as a symlink to
``../.bashrc`` or to any path the user can write; following it would let the
repository choose where PMCP writes the user's secrets. Every caller therefore
states ``confine_to`` -- keyword-only with no default, so a new call site has to
decide -- and a confined write is refused (``ConfinedWriteError``) when the path,
or ANY step of its link chain, leaves ``confine_to``: a link out, an absolute
link out, a dangling link out, and a chain that leaves and comes back. It is
refused rather than replaced: silently replacing the link would destroy the
operator's link and hide the problem. A link that stays inside is followed.

A confined write has no check-then-use window. The chain is resolved one
component at a time, then the write walks from an ``O_DIRECTORY`` descriptor of
the confinement root down to the target's directory with ``O_NOFOLLOW`` at
every step, and creates, ``fsync``-s and renames the temporary *relative to that
directory descriptor* (``dir_fd``). A component swapped for a symlink after the
check fails that walk (``ELOOP``/``ENOTDIR``) instead of being followed, and a
final component swapped for a link is replaced as a directory entry -- rename
never follows it -- so nothing outside the root can be written. The residual is
the root itself, which is opened by its resolved path: its ancestors are not
the repository's to change. Where ``dir_fd`` is unsupported (Windows) the write
falls back to the resolved path after the same chain check.

Two shapes are decided here rather than left to fall out:

* **A dangling link creates its target**, at the requested mode. That is what
  2.7.3 did, it is the ordinary dotfiles shape (a committed link to a
  git-ignored secrets file that does not exist yet on a fresh machine), and it
  agrees with the read side, which treats a store whose link target is gone as
  *not there* (``env_store._read_env_file_strict``). Only the target FILE is
  created: if the target's *directory* does not exist the write is refused with
  ``FileNotFoundError`` naming the target.
* **A symlink loop is refused** with ``ELOOP``. On Python 3.10-3.12
  ``os.path.realpath`` does not raise on a loop; it returns a path that is still
  a link, and replacing that would silently break the loop.

The temporary is a hidden ``.pmcp-*`` / ``*.tmp`` name and is removed on every
exception. A process killed outright (SIGKILL, power loss) between the write and
the rename can still leave it behind, in the TARGET's directory -- for a
dotfiles user that may be a git checkout, where it holds the full file content.

Callers keep their own directory-creation policy (each tightens only the
directories PMCP itself creates); a link's own directory necessarily exists.
The one deliberate exception is ``config.loader._atomic_write_json``: it writes
a project ``.mcp.json`` to a key pinned by an ``O_NOFOLLOW`` open, refuses a
symlinked ``.mcp.json`` outright (``symlinked_config``), and must not re-resolve
the path, so it does not use this helper.
"""

from __future__ import annotations

import errno
import os
import secrets
import tempfile
from collections import deque
from pathlib import Path

#: Same bound the kernel applies to one path resolution (Linux MAXSYMLINKS).
_MAX_LINK_HOPS = 40


class ConfinedWriteError(PermissionError):
    """A confined write whose path, or a step of its link chain, leaves the root.

    The message names the file and the root kind only -- never the link target,
    which a repository chose and which could be anything. Built from the message
    alone, so ``str()`` is exactly that sentence (no ``[Errno ..]`` prefix) and
    entry points can hand it to the operator verbatim.
    """


def resolve_write_target(path: Path) -> Path:
    """The file an unconfined atomic write of ``path`` replaces: links followed.

    Follows a chain of symlinks, relative or absolute, to the final target, which
    may not exist yet (a dangling link). Raises ``OSError(ELOOP)`` for a loop and
    ``FileNotFoundError`` when the target's directory does not exist.
    """
    target = os.path.realpath(path)
    if os.path.islink(target):
        # realpath gave up inside a loop and handed back a path that is still a
        # link (3.10-3.12 do not raise). Replacing it would break the loop.
        raise OSError(errno.ELOOP, "Symlink loop; refusing to write", str(path))
    _require_target_dir(path, target)
    return Path(target)


def _require_target_dir(path: Path, target: str) -> None:
    target_parent = os.path.dirname(target)
    if not os.path.isdir(target_parent):
        raise FileNotFoundError(
            errno.ENOENT,
            f"Cannot write {path}: the directory of its symlink target {target} "
            "does not exist; create it, or point the link somewhere that exists",
            target_parent,
        )


def _inside(candidate: str, root: str) -> bool:
    return candidate == root or candidate.startswith(root.rstrip(os.sep) + os.sep)


def resolve_confined_target(path: Path, confine_to: Path, label: str) -> Path:
    """Resolve ``path`` one component at a time, refusing any step outside ``confine_to``.

    ``path`` must lie lexically under ``confine_to``. Every link met on the way
    is expanded in place, and every intermediate location -- each ``..``, each
    absolute link, each relative link's landing point -- must stay inside the
    resolved root, so a chain that leaves and re-enters is refused too. Returns
    the resolved target (which may not exist yet).
    """
    root = os.path.realpath(confine_to)
    refusal = ConfinedWriteError(
        f"refusing to write {path.name}: it is a symlink that leaves the {label}"
    )
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(confine_to))
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        raise ConfinedWriteError(
            f"refusing to write {path.name}: it is outside the {label}"
        )

    pending: deque[str] = deque(rel.split(os.sep))
    current = root
    hops = 0
    while pending:
        name = pending.popleft()
        if name in ("", os.curdir):
            continue
        if name == os.pardir:
            current = os.path.dirname(current)
            if not _inside(current, root):
                raise refusal
            continue
        candidate = os.path.join(current, name)
        if os.path.islink(candidate):
            hops += 1
            if hops > _MAX_LINK_HOPS:
                raise OSError(errno.ELOOP, "Symlink loop; refusing to write", str(path))
            link = os.readlink(candidate)
            if os.path.isabs(link):
                # Re-express an absolute link relative to the root: one that
                # points outside becomes a leading `..`, which the `..` step
                # above refuses, so there is one rule for every way out.
                current = root
                link = os.path.relpath(os.path.normpath(link), root)
            pending.extendleft(reversed(link.split(os.sep)))
            continue
        current = candidate
    if current == root:
        raise IsADirectoryError(errno.EISDIR, "Cannot write a directory", str(path))
    _require_target_dir(path, current)
    return Path(current)


def atomic_write(
    path: Path,
    data: bytes,
    *,
    confine_to: Path | None,
    confine_label: str = "project",
    mode: int = 0o600,
    prefix: str = ".pmcp-",
    suffix: str = ".tmp",
) -> Path:
    """Atomically replace the file at ``path`` (or its symlink target) with ``data``.

    ``confine_to`` is required: ``None`` for a path the operator owns (under
    ``~/.config/pmcp``, ``~`` or ``$XDG_*``), whose links are followed anywhere;
    the repository root for a path a repository controls, whose links are
    followed only while every step stays inside it (``ConfinedWriteError``
    otherwise). ``confine_label`` names that root in the refusal.

    The file is created or replaced at ``mode``; the temporary is created at
    that mode, so the content is never readable at a looser one. The file is
    ``fsync``-ed before the replace; the directory is ``fsync``-ed after it,
    best effort (macOS returns ``EINVAL``, some network and overlay mounts
    ``ENOTSUP``, Windows cannot open a directory) -- the replace has already
    landed by then. On any failure before the replace the destination is
    untouched and the temporary is removed.

    Returns the path actually written: the symlink target when ``path`` is a link.
    """
    if confine_to is None:
        target = resolve_write_target(path)
        _write_by_path(target, data, mode=mode, prefix=prefix, suffix=suffix)
        return target

    target = resolve_confined_target(path, confine_to, confine_label)
    if _DIR_FD_SUPPORTED:
        _write_confined_by_fd(
            Path(os.path.realpath(confine_to)),
            target,
            data,
            mode=mode,
            prefix=prefix,
            suffix=suffix,
        )
    else:  # pragma: no cover - Windows: no dir_fd; chain already checked
        _write_by_path(target, data, mode=mode, prefix=prefix, suffix=suffix)
    return target


# `os.replace` is not listed in `os.supports_dir_fd` even where its dir_fd
# arguments work: it shares rename(2)/renameat(2) with `os.rename`, which is.
_DIR_FD_SUPPORTED = (
    os.open in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)


def _write_by_path(
    target: Path, data: bytes, *, mode: int, prefix: str, suffix: str
) -> None:
    parent = target.parent
    fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=prefix, suffix=suffix)
    committed = False
    try:
        with os.fdopen(fd, "wb") as handle:
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), mode)
            else:  # pragma: no cover - Windows has no fchmod
                os.chmod(tmp_name, mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
        committed = True
    finally:
        if not committed:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    try:
        dir_fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def _write_confined_by_fd(
    root: Path, target: Path, data: bytes, *, mode: int, prefix: str, suffix: str
) -> None:
    """Write ``target`` relative to a descriptor walked down from ``root``, never by path."""
    rel_dir = os.path.relpath(target.parent, root)
    components = [] if rel_dir == os.curdir else rel_dir.split(os.sep)
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    dir_fd = os.open(root, dir_flags)
    try:
        for component in components:
            # O_NOFOLLOW at every step: a directory swapped for a link after the
            # chain check fails here (ELOOP/ENOTDIR) instead of being followed.
            next_fd = os.open(component, dir_flags, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd

        tmp_name = f"{prefix}{secrets.token_hex(8)}{suffix}"
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            mode,
            dir_fd=dir_fd,
        )
        committed = False
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), mode)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            # rename(2) never follows the destination's final component: a link
            # planted there after the check is replaced as an entry, in-root.
            os.replace(tmp_name, target.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            committed = True
        finally:
            if not committed:
                try:
                    os.unlink(tmp_name, dir_fd=dir_fd)
                except OSError:
                    pass
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
    finally:
        os.close(dir_fd)
