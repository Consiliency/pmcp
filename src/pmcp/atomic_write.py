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
and hide the problem.

**What is guaranteed, and against whom.** The guarantees are about what a
repository SHIPS: links, directories and file types present in the checkout
before the command runs. Against those, nothing is read or written outside the
root, and no fifo or device is read. A process already running as the user and
writing into the store's directory WHILE the command runs is out of scope: it
can write the user's files directly. The swap checks below -- the read's
identity check and the rename-time check of the temporary's name -- narrow
that window as a best effort; they do not close it. In particular POSIX has no
rename-by-descriptor, so a temporary's NAME swapped between its last check and
the ``rename`` is moved into place (the store can end as whatever was swapped
in -- a link, say -- and the submitted value is lost); even then nothing is
written outside the root. The root itself is opened by its path (its ancestors
are not the repository's).

**Where traversal cannot be protected, a confined write fails closed.** Without
``dir_fd`` support (Windows) the same walk runs over pathnames, and for a
confined write it refuses every link or reparse point on the way: a path under
the root is accepted only when every component is a real directory and the
final one is not a link, and a read must then open exactly the file it checked
(``st_dev``/``st_ino``). Nothing is resolved by pathname and then written
through a link.

**Unconfined (operator-owned) writes use the same walk, unconfined.** The
operator's own links are followed anywhere, but in the kernel's order: a
``hop/../x`` whose ``hop`` is missing, a regular file or a loop is refused as
the kernel refuses it, never collapsed lexically onto ``x`` (which non-strict
``os.path.realpath`` does). On POSIX the unconfined write also happens in the
directory descriptor the walk ended on.

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
from typing import Any

#: Same bound the kernel applies to one path resolution (Linux MAXSYMLINKS).
_MAX_LINK_HOPS = 40

#: FILE_ATTRIBUTE_REPARSE_POINT: a Windows symlink, junction or mount point.
_REPARSE_POINT = 0x400


#: The refusal's opening words, by what the caller was doing to the file. Spelled
#: out as literals rather than interpolating the verb, so every message the
#: code can emit stays a checkable literal (tests/test_migration_doc.py matches
#: quoted messages against the source's templates).
_REFUSING = {
    "write": "refusing to write",
    "read": "refusing to read",
    "load": "refusing to load",
}


def refusing(verb: str) -> str:
    """``"refusing to write"`` / ``"refusing to read"`` / ``"refusing to load"``."""
    return _REFUSING[verb]


class ConfinedWriteError(PermissionError):
    """A confined write whose path, or a step of its link chain, leaves the root.

    The message names the file and the root kind only -- never the link target,
    which a repository chose and which could be anything. Built from the message
    alone, so ``str()`` is exactly that sentence (no ``[Errno ..]`` prefix) and
    entry points can hand it to the operator verbatim.
    """


def resolve_write_target(path: Path) -> Path:
    """The file an unconfined atomic write of ``path`` replaces: links followed.

    Resolved by the SAME kernel-order walk as a confined write (:func:`_walk`),
    with no confinement: every intermediate component must exist and be a
    directory (or a link to one) when it is reached, ``..`` applies after the
    links before it are resolved, and only the final component may be missing
    (a dangling link creates its target). Never ``os.path.realpath``: non-strict
    realpath collapses ``hop/../x`` lexically when ``hop`` is missing, a regular
    file or a loop -- shapes the kernel refuses -- and would overwrite ``x``.
    Raises ``OSError(ELOOP)`` for a loop, ``FileNotFoundError`` when a directory
    on the way does not exist, ``NotADirectoryError`` for a non-directory on the
    way, ``IsADirectoryError`` when the path resolves to a directory.
    """
    walk = _walk(path, confine_to=None, label="", verb="write", backend=_backend())
    walk.close()
    return walk.target


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


class _FdBackend:
    """Walk by directory descriptors: what is checked is what is written."""

    no_links = False

    def start(self, where: str) -> int:
        return os.open(where, os.O_RDONLY | os.O_DIRECTORY)

    def lstat(self, at: int, name: str) -> os.stat_result:
        return os.stat(name, dir_fd=at, follow_symlinks=False)

    def readlink(self, at: int, name: str) -> str:
        return os.readlink(name, dir_fd=at)

    def enter(self, at: int, name: str) -> int:
        # O_NOFOLLOW: a directory swapped for a link after its lstat fails here.
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=at)

    def close(self, at: object) -> None:
        if isinstance(at, int):
            try:
                os.close(at)
            except OSError:
                pass


class _PathBackend:
    """Walk by pathnames, where ``dir_fd`` is unavailable (Windows).

    Pathnames are only as good as the moment they were checked, so for a
    CONFINED walk ``no_links`` is set: any link or reparse point on the way is
    refused rather than followed, and every directory must be a real one.
    """

    def __init__(self, *, no_links: bool) -> None:
        self.no_links = no_links

    def start(self, where: str) -> str:
        if not stat.S_ISDIR(os.stat(where).st_mode):
            raise NotADirectoryError(errno.ENOTDIR, "Not a directory", where)
        return where

    def lstat(self, at: str, name: str) -> os.stat_result:
        return os.lstat(os.path.join(at, name))

    def readlink(self, at: str, name: str) -> str:
        return os.readlink(os.path.join(at, name))

    def enter(self, at: str, name: str) -> str:
        return os.path.join(at, name)

    def close(self, at: object) -> None:
        return None


def _backend(*, confined: bool = False) -> _FdBackend | _PathBackend:
    if _DIR_FD_SUPPORTED:
        return _FdBackend()
    return _PathBackend(no_links=confined)


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & _REPARSE_POINT
    )


@dataclass
class _Walk:
    """The end of a walk: the directory reached and the name to replace in it.

    ``at`` is a directory descriptor (``_FdBackend``) or a pathname
    (``_PathBackend``).
    """

    at: Any
    name: str
    target: Path
    backend: Any
    _held: list[Any] = field(default_factory=list)

    @property
    def dir_fd(self) -> int:
        return int(self.at)

    def close(self) -> None:
        while self._held:
            self.backend.close(self._held.pop())


def _walk(
    path: Path,
    *,
    confine_to: Path | None,
    label: str,
    verb: str,
    backend: _FdBackend | _PathBackend,
) -> _Walk:
    """THE resolver: ``path`` resolved one component at a time, as the kernel does.

    Each component is ``lstat``-ed relative to the directory reached so far; a
    directory is entered; a link's text is read and spliced in -- a relative
    link continues from the link's own directory, an absolute one from its
    anchor -- and ``..`` returns to the directory the walk actually came from,
    AFTER every link before it was resolved. Nothing is resolved lexically.
    Every intermediate component must exist and be a directory when reached;
    only the final one may be missing.

    With ``confine_to`` the walk starts at that root and is refused
    (``ConfinedWriteError``) the moment it would leave it: a ``..`` from the
    root, or an absolute link that does not spell the root's real path. Without
    it the walk starts at the path's own anchor (``/``; a drive on Windows) and
    follows links anywhere, ``/..`` staying at ``/`` as in the kernel.

    The caller owns what the walk holds and must ``close()`` it. Raises
    ``OSError(ELOOP)`` for a loop, ``IsADirectoryError`` when the path resolves
    to a directory, ``NotADirectoryError`` for a non-directory on the way, and
    ``FileNotFoundError`` for a missing directory on the way.
    """
    confined = confine_to is not None
    refusal = ConfinedWriteError(
        f"{refusing(verb)} {path.name}: it is a symlink that leaves the {label}"
    )
    unsafe_link = ConfinedWriteError(
        f"{refusing(verb)} {path.name}: it is a symlink in the {label}, which "
        "cannot be followed safely on this platform"
    )
    missing_dir_message = (
        f"Cannot write {path.name}: a directory on its symlink path does not exist"
        if confined
        else f"Cannot write {path}: the directory of its symlink target does not "
        "exist; create it, or point the link somewhere that exists"
    )

    if confine_to is not None:
        try:
            parts = list(PurePath(path).relative_to(PurePath(confine_to)).parts)
        except ValueError:
            raise ConfinedWriteError(
                f"{refusing(verb)} {path.name}: it is outside the {label}"
            ) from None
        anchor = os.path.realpath(confine_to, strict=True)
        root_parts = [p for p in split_link_text(anchor) if p not in ("", os.curdir)]
        start = os.fspath(confine_to)
    else:
        whole = PurePath(path)
        if not whole.is_absolute():
            # Joined, never normalised: the walk applies `..` itself.
            whole = PurePath(os.getcwd()) / whole
        anchor = whole.anchor
        parts = list(whole.parts[1:])
        root_parts = []
        start = anchor

    held: list[Any] = [backend.start(start)]
    names: list[str] = []
    handed_over = False
    try:
        pending: deque[str] = deque(parts)
        hops = 0
        final: str | None = None
        while pending:
            name = pending.popleft()
            last = not pending
            final = None
            if name in ("", os.curdir):
                continue
            if name == os.pardir:
                if len(held) == 1:
                    if confined:
                        raise refusal
                    continue  # `/..` is `/`
                backend.close(held.pop())
                names.pop()
                continue
            try:
                st = backend.lstat(held[-1], name)
            except FileNotFoundError:
                if last:
                    final = name
                    break
                raise FileNotFoundError(errno.ENOENT, missing_dir_message) from None
            if _is_link(st):
                if backend.no_links:
                    raise unsafe_link
                hops += 1
                if hops > _MAX_LINK_HOPS:
                    raise OSError(errno.ELOOP, "Symlink loop; refusing to write")
                text = backend.readlink(held[-1], name)
                link_parts = split_link_text(text)
                if _is_absolute_link(text):
                    kept = [p for p in link_parts if p not in ("", os.curdir)]
                    if confined:
                        if kept[: len(root_parts)] != root_parts:
                            # realpath shapes the MESSAGE only, never the decision.
                            if _inside(os.path.realpath(text), anchor):
                                raise ConfinedWriteError(
                                    f"{refusal}; it is an absolute link spelled "
                                    "through a symlinked directory, so use a "
                                    "relative link instead"
                                )
                            raise refusal
                        link_parts = kept[len(root_parts) :]
                        new_start = start
                    else:
                        link_anchor = PurePath(text).anchor or anchor
                        link_parts = list(PurePath(text).parts[1:])
                        anchor = link_anchor
                        new_start = link_anchor
                    while held:
                        backend.close(held.pop())
                    names.clear()
                    held.append(backend.start(new_start))
                pending.extendleft(reversed(link_parts))
                continue
            if stat.S_ISDIR(st.st_mode):
                if last:
                    break  # resolves to a directory: final stays None
                held.append(backend.enter(held[-1], name))
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
            at=held[-1],
            name=final,
            target=Path(anchor, *names, final),
            backend=backend,
            _held=held,
        )
        handed_over = True
        return walk
    finally:
        # On any failure what was opened so far is released here; on success
        # the caller owns it through `_Walk.close()`.
        if not handed_over:
            for at in held:
                backend.close(at)


def _walk_confined(
    path: Path, confine_to: Path, label: str, verb: str = "write"
) -> _Walk:
    """:func:`_walk` confined to ``confine_to``, on this platform's backend."""
    return _walk(
        path,
        confine_to=confine_to,
        label=label,
        verb=verb,
        backend=_backend(confined=True),
    )


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
    backend = _backend(confined=confine_to is not None)
    walk = _walk(
        path,
        confine_to=confine_to,
        label=confine_label,
        verb="write",
        backend=backend,
    )
    try:
        if isinstance(backend, _FdBackend):
            _write_in_dir(
                walk.dir_fd, walk.name, data, mode=mode, prefix=prefix, suffix=suffix
            )
        else:  # no dir_fd (Windows): a confined walk refused every link on the way
            _write_by_path(
                Path(os.path.join(walk.at, walk.name)),
                data,
                mode=mode,
                prefix=prefix,
                suffix=suffix,
            )
    finally:
        walk.close()
    return walk.target


def read_confined(
    path: Path, confine_to: Path, label: str = "project", verb: str = "write"
) -> bytes | None:
    """Read ``path`` through the SAME confined walk a write would take, or refuse.

    A command that rewrites a repository-controlled store reads it first; that
    read must not follow a link the write would refuse. So the walk decides
    first -- a link that leaves ``confine_to`` is refused here, before anything
    is opened -- and the final component is opened ``O_NOFOLLOW`` relative to
    the directory the walk ended on, non-blocking, and refused unless it is a
    REGULAR file (a fifo, ``/dev`` node or socket would hang or misbehave the
    read). Returns ``None`` when the file does not exist yet (a fresh store, a
    dangling link that stays inside, or a root directory not created yet).
    Raises ``ConfinedWriteError`` / ``OSError`` exactly as :func:`atomic_write`
    would; ``verb`` ("write", "read", "load") names what the caller was doing
    in the refusal, so a store read only as a SOURCE is not called a write.
    """
    not_regular = ConfinedWriteError(
        f"{refusing(verb)} {path.name}: it is not a regular file"
    )
    if not os.path.lexists(confine_to):
        # Nothing to read under a root that does not exist yet: the write
        # creates it (a fresh `--project` directory). An empty read, not a
        # refusal.
        return None
    walk = _walk_confined(path, confine_to, label, verb)
    if not isinstance(walk.backend, _FdBackend):
        try:
            return _read_by_path(
                path, os.path.join(walk.at, walk.name), not_regular, verb
            )
        finally:
            walk.close()
    try:
        # Refuse a non-regular file from its directory entry BEFORE opening it:
        # opening a socket fails ENXIO and a device may have side effects. The
        # fstat after the O_NOFOLLOW open re-checks against a swap in between.
        try:
            entry = os.stat(walk.name, dir_fd=walk.dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(entry.st_mode):
            raise not_regular
        try:
            fd = os.open(
                walk.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=walk.dir_fd,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            _raise_if_swapped_to_a_link(exc, path, verb)
            raise
        with os.fdopen(fd, "rb") as handle:
            _require_same_regular_file(
                entry, os.fstat(handle.fileno()), path, verb, not_regular
            )
            return handle.read()
    finally:
        walk.close()


def _read_by_path(
    path: Path, text: str, not_regular: ConfinedWriteError, verb: str
) -> bytes | None:
    """The no-``dir_fd`` read of a confined walk's end (every link already refused)."""
    try:
        entry = os.lstat(text)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(entry.st_mode):
        raise not_regular
    # The pathname is not trusted between the lstat and the open: O_NOFOLLOW
    # where the platform has it, and -- everywhere -- the opened descriptor must
    # be the very file the lstat saw (same st_dev/st_ino), a regular file. A
    # swap to a link (or anything else) in between is refused, never read.
    try:
        fd = os.open(
            text,
            os.O_RDONLY
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_BINARY", 0),
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        _raise_if_swapped_to_a_link(exc, path, verb)
        raise
    with os.fdopen(fd, "rb") as handle:
        _require_same_regular_file(
            entry, os.fstat(handle.fileno()), path, verb, not_regular
        )
        return handle.read()


#: What an ``O_NOFOLLOW`` open of a symlink fails with: ``ELOOP`` on Linux and
#: macOS, ``EMLINK`` on FreeBSD.
_NOFOLLOW_ON_A_LINK = {errno.ELOOP, errno.EMLINK}


def _raise_if_swapped_to_a_link(exc: OSError, path: Path, verb: str) -> None:
    """The entry was checked as a regular file; a link at open means it changed."""
    if exc.errno in _NOFOLLOW_ON_A_LINK:
        raise ConfinedWriteError(
            f"{refusing(verb)} {path.name}: it changed while it was being read"
        ) from None


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _require_same_regular_file(
    entry: os.stat_result,
    opened: os.stat_result,
    path: Path,
    verb: str,
    not_regular: ConfinedWriteError,
) -> None:
    """The descriptor must be the regular file the directory entry showed."""
    if not stat.S_ISREG(opened.st_mode):
        raise not_regular
    if not _same_file(entry, opened):
        raise ConfinedWriteError(
            f"{refusing(verb)} {path.name}: it changed while it was being read"
        )


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
            written = os.fstat(handle.fileno())
        # Best-effort narrowing, not a guarantee: the temporary is renamed by
        # NAME, so check that the entry is still the file just written (not a
        # link or another file swapped in) before renaming it. A concurrent
        # same-user writer can still swap it between this check and the rename
        # -- POSIX has no rename-by-descriptor -- which is out of scope (module
        # docstring); even then nothing is written outside. The destination
        # needs no check: rename replaces a link there as an entry.
        current = os.lstat(tmp_name)
        if stat.S_ISLNK(current.st_mode) or not _same_file(current, written):
            raise ConfinedWriteError(
                f"refusing to write {target.name}: its temporary file changed "
                "before it could be committed"
            )
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
            written = os.fstat(handle.fileno())
        # Best-effort narrowing of a concurrent swap of the temp's name; see
        # the comment in _write_by_path and the module docstring.
        current = os.stat(tmp_name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISLNK(current.st_mode) or not _same_file(current, written):
            raise ConfinedWriteError(
                f"refusing to write {name}: its temporary file changed "
                "before it could be committed"
            )
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
