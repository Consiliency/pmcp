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

**One resolver, the kernel's rules, and the path checked is the path written.**
A confined write resolves the link chain one component at a time from an
``O_DIRECTORY`` descriptor of the confinement root, exactly as the kernel does:
each component is ``lstat``-ed relative to the directory descriptor before it;
a directory is opened ``O_NOFOLLOW`` relative to that descriptor and pushed; a
symlink is read with ``readlink`` and its text spliced in -- a relative link
continues from the link's own directory, an absolute one from the root -- and
``..`` pops back to the directory the walk actually came from, *after* every
link before it was resolved. There is no lexical shortcut (no ``normpath``, no
``abspath``) anywhere on an unresolved path: ``hop/../x`` with ``hop -> a/b``
is ``a/x``, never ``x``. Link text is split on ``/`` and on ``os.sep`` and
``os.altsep``, so a git-recorded ``../.bashrc`` is two components everywhere.
The temporary is then created, ``fsync``-ed and renamed *relative to the same
directory descriptor the walk ended on* (``dir_fd``): nothing is re-resolved
between the check and the write. A directory swapped for a symlink after it was
opened cannot redirect the write (the descriptor is held); one swapped before it
is opened fails the ``O_NOFOLLOW`` open; a final component swapped for a link is
replaced as a directory entry -- ``rename`` never follows its destination.

The walk is refused (``ConfinedWriteError``) the moment it would leave the
root: a ``..`` from the root itself, or an absolute link that does not spell
the root's real path, component for component, with no ``..`` and no symlinked
directory on the way. An absolute link that reaches the project through a
symlinked parent (a ``$WORKTREE_ROOT`` that is itself a link, say) is refused
too, with a hint to use a relative link. A link out, an absolute link out, a
dangling link out and a chain that leaves and comes back are all refused rather
than replaced: silently replacing the link would destroy the operator's link
and hide the problem. The residual is the root itself, opened by its path (its
ancestors are not the repository's), and a directory moved OUT of the root
between being opened and written by a process already running as the user --
which could write the user's files directly anyway.

**Where traversal cannot be protected, a confined write fails closed.** Without
``dir_fd`` support (Windows), there is no descriptor walk, so a confined write
is accepted only for a direct child of the root that is not a symlink or
reparse point, and is otherwise refused with the same value-free refusal; it is
never resolved by pathname and written by pathname. Unconfined (operator-owned)
writes keep the path-based follow on every platform.

Two shapes are decided here rather than left to fall out:

* **A dangling link creates its target**, at the requested mode. That is what
  2.7.3 did, it is the ordinary dotfiles shape (a committed link to a
  git-ignored secrets file that does not exist yet on a fresh machine), and it
  agrees with the read side, which treats a store whose link target is gone as
  *not there* (``env_store._read_env_file_strict``). Only the target FILE is
  created: if the target's *directory* does not exist the write is refused with
  ``FileNotFoundError``.
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
import re
import secrets
import stat
import tempfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path, PurePath

#: Same bound the kernel applies to one path resolution (Linux MAXSYMLINKS).
_MAX_LINK_HOPS = 40

#: FILE_ATTRIBUTE_REPARSE_POINT: a Windows symlink, junction or mount point.
_REPARSE_POINT = 0x400


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


def _separators() -> str:
    seps = {"/", os.sep}
    if os.altsep:
        seps.add(os.altsep)
    return "".join(sorted(seps))


def split_link_text(text: str) -> list[str]:
    """Split symlink text into components on EVERY separator the platform reads.

    Git records a link target with ``/``; Windows also reads ``\\``. Splitting on
    ``os.sep`` alone would leave ``../.bashrc`` one component there, and the
    ``..`` rule would never see it. Empty components (``a//b``, a leading ``/``)
    are kept as ``""`` and skipped by the walk, as the kernel skips them.
    """
    return re.split("[" + re.escape(_separators()) + "]", text)


def _is_absolute_link(text: str) -> bool:
    return bool(text) and (text[0] in _separators() or PurePath(text).is_absolute())


def _inside(candidate: str, root: str) -> bool:
    """Message-only helper: is the real path ``candidate`` at or under ``root``."""
    return candidate == root or candidate.startswith(root.rstrip(os.sep) + os.sep)


@dataclass
class _Walk:
    """The end of a confined walk: an open directory and the name to replace in it."""

    dir_fd: int
    name: str
    target: Path
    _fds: list[int] = field(default_factory=list)

    def close(self) -> None:
        while self._fds:
            try:
                os.close(self._fds.pop())
            except OSError:
                pass


def _walk_confined(path: Path, confine_to: Path, label: str) -> _Walk:
    """Resolve ``path`` inside ``confine_to`` as the kernel would, holding descriptors.

    See the module docstring. The caller owns the returned descriptors and must
    ``close()`` the walk. Raises ``ConfinedWriteError`` for any step that leaves
    the root, ``OSError(ELOOP)`` for a loop, ``IsADirectoryError`` when the path
    resolves to a directory, ``NotADirectoryError`` when a non-directory sits
    where a directory is needed, and ``FileNotFoundError`` for a missing one.
    """
    refusal = ConfinedWriteError(
        f"refusing to write {path.name}: it is a symlink that leaves the {label}"
    )
    try:
        rel = PurePath(path).relative_to(PurePath(confine_to))
    except ValueError:
        raise ConfinedWriteError(
            f"refusing to write {path.name}: it is outside the {label}"
        ) from None

    root_real = os.path.realpath(confine_to)
    root_parts = [p for p in split_link_text(root_real) if p not in ("", os.curdir)]
    root_fd = os.open(confine_to, os.O_RDONLY | os.O_DIRECTORY)
    stack: list[int] = [root_fd]
    names: list[str] = []
    handed_over = False
    try:
        pending: deque[str] = deque(rel.parts)
        hops = 0
        final: str | None = None
        while pending:
            name = pending.popleft()
            last = not pending
            final = None
            if name in ("", os.curdir):
                continue
            if name == os.pardir:
                if len(stack) == 1:
                    raise refusal
                os.close(stack.pop())
                names.pop()
                continue
            try:
                st = os.stat(name, dir_fd=stack[-1], follow_symlinks=False)
            except FileNotFoundError:
                if last:
                    final = name
                    break
                raise FileNotFoundError(
                    errno.ENOENT,
                    f"Cannot write {path.name}: a directory on its symlink path "
                    "does not exist",
                ) from None
            if stat.S_ISLNK(st.st_mode):
                hops += 1
                if hops > _MAX_LINK_HOPS:
                    raise OSError(errno.ELOOP, "Symlink loop; refusing to write")
                text = os.readlink(name, dir_fd=stack[-1])
                parts = split_link_text(text)
                if _is_absolute_link(text):
                    kept = [p for p in parts if p not in ("", os.curdir)]
                    if kept[: len(root_parts)] != root_parts:
                        if _inside(os.path.realpath(text), root_real):
                            # Lands inside, but spelled through a symlinked
                            # directory or `..`. Still refused -- the spelling
                            # leaves the root -- with how to write it instead.
                            # realpath shapes the MESSAGE only, never the decision.
                            raise ConfinedWriteError(
                                f"{refusal}; it is an absolute link spelled "
                                "through a symlinked directory, so use a "
                                "relative link instead"
                            )
                        raise refusal
                    parts = kept[len(root_parts) :]
                    while len(stack) > 1:
                        os.close(stack.pop())
                    names.clear()
                pending.extendleft(reversed(parts))
                continue
            if stat.S_ISDIR(st.st_mode):
                if last:
                    break  # resolves to a directory: final stays None
                fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=stack[-1],
                )
                stack.append(fd)
                names.append(name)
                continue
            if last:
                final = name
                break
            raise NotADirectoryError(
                errno.ENOTDIR, f"Cannot write {path.name}: not a directory"
            )
        if final is None:
            raise IsADirectoryError(errno.EISDIR, f"Cannot write {path.name}")
        walk = _Walk(
            dir_fd=stack[-1],
            name=final,
            target=Path(root_real, *names, final),
            _fds=stack,
        )
        handed_over = True
        return walk
    finally:
        # On any failure the descriptors opened so far are closed here; on
        # success the caller owns them through `_Walk.close()`.
        if not handed_over:
            for fd in stack:
                try:
                    os.close(fd)
                except OSError:
                    pass


def resolve_confined_target(path: Path, confine_to: Path, label: str) -> Path:
    """Where a confined write of ``path`` would land, by the same walk the write uses."""
    walk = _walk_confined(path, confine_to, label)
    walk.close()
    return walk.target


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

    if not _DIR_FD_SUPPORTED:
        return _write_confined_without_dir_fd(
            path,
            confine_to,
            confine_label,
            data,
            mode=mode,
            prefix=prefix,
            suffix=suffix,
        )

    walk = _walk_confined(path, confine_to, confine_label)
    try:
        _write_in_dir(
            walk.dir_fd, walk.name, data, mode=mode, prefix=prefix, suffix=suffix
        )
    finally:
        walk.close()
    return walk.target


# `os.replace` is not listed in `os.supports_dir_fd` even where its dir_fd
# arguments work: it shares rename(2)/renameat(2) with `os.rename`, which is.
_DIR_FD_SUPPORTED = (
    os.open in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and os.readlink in os.supports_dir_fd
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)


def _is_link_or_reparse_point(path: str) -> bool:
    if os.path.islink(path):
        return True
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT) or (
        stat.S_ISLNK(st.st_mode)
    )


def _write_confined_without_dir_fd(
    path: Path,
    confine_to: Path,
    label: str,
    data: bytes,
    *,
    mode: int,
    prefix: str,
    suffix: str,
) -> Path:
    """A confined write where traversal cannot be protected: fail closed.

    Only a direct child of the root that is not a symlink or reparse point is
    written. Anything else -- a link of any kind, a path below a subdirectory --
    would be resolved by pathname and written by pathname, and a directory or
    link swapped in between would redirect the write outside the root.
    """
    text = os.fspath(path)
    name = os.path.basename(text)
    root = os.fspath(confine_to).rstrip(_separators()) or os.fspath(confine_to)
    if os.path.dirname(text) != root:
        raise ConfinedWriteError(
            f"refusing to write {name}: it is not directly inside the {label}"
        )
    if _is_link_or_reparse_point(text):
        raise ConfinedWriteError(
            f"refusing to write {name}: it is a symlink in the {label}, which "
            "cannot be followed safely on this platform"
        )
    _write_by_path(Path(text), data, mode=mode, prefix=prefix, suffix=suffix)
    return Path(text)


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


def _write_in_dir(
    dir_fd: int, name: str, data: bytes, *, mode: int, prefix: str, suffix: str
) -> None:
    """Create, fsync and rename a temp onto ``name``, all relative to ``dir_fd``."""
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
        # planted there after the walk is replaced as an entry, in-root.
        os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
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
