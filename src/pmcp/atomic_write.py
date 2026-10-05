"""One atomic-write primitive for every user-owned file PMCP rewrites.

Every PMCP-owned file that is rewritten whole -- the credential stores
(``pmcp.env``, ``.env.pmcp``), the trust store, package approvals, the registry
cache, and the client config ``pmcp setup --write`` emits -- goes through
:func:`atomic_write`. The bytes go to a temporary file in the destination's
directory, are ``fsync``-ed, and are ``os.replace``-d over the destination, so
a write that fails partway leaves the old file byte-intact (Consiliency/pmcp#248).

The module does NOT reimplement the kernel's path resolution. Two rules, each
small enough to leave every hard case to the kernel:

**Operator-owned files (``confine_to=None``): follow the final link chain by
pathname.** Users keep ``~/.config/pmcp/pmcp.env`` and friends in a dotfiles
repository and symlink them into place; replacing the link would stop the
dotfiles copy from receiving updates (the #248 regression against 2.7.3). So
only the final component's chain is followed: ``lstat`` it; if it is a link,
``readlink`` and join the text to the link's directory with plain
``os.path.join`` -- no ``normpath``, no ``realpath`` of any strictness -- and
loop, up to 40 hops (``ELOOP``). Every syscall gets the user's own spelling, so
the kernel applies its own rules to ``missing/..``, ``file/..``, ``//``,
mode-000 and search-only directories. A target that ends in a separator is
refused (``ENOTDIR``, as the kernel would). The temporary is created in the
target's directory and renamed over the target; the link is left alone.

**Repository-controlled files (``confine_to=<project root>``): no symlinks at
all.** ``<project>/.env.pmcp`` ships with a checkout, so a clone could link it
anywhere the user can write. The walk starts at an ``O_DIRECTORY`` descriptor of
the root and opens each component ``O_NOFOLLOW`` relative to the previous one;
every component is ``lstat``-ed and ANY symlink -- intermediate or final, out of
the project or inside it -- is refused, as is any ``.``, ``..`` or empty
component (pmcp builds these paths itself). The final component must be a
regular file or absent. The temporary is created, ``fsync``-ed and renamed
relative to the directory descriptor the walk ended on, so the path checked is
the path written. Kernel behaviour relied on: ``O_NOFOLLOW``, ``fstat`` type
bits and ``renameat`` at a directory descriptor. Where ``dir_fd`` is
unavailable (Windows) the same rules run over pathnames, also refusing a reparse
point. Refusals are ``ConfinedWriteError`` with a value-free message naming
only the file.

**What is guaranteed, and against whom.** The guarantees are about what a
repository SHIPS: links, directories and file types present before the command
runs. A process already running as the user and rewriting these directories
WHILE a command runs is out of scope -- it can write the user's files directly.
Against it the read's identity check (the descriptor must be the file its
``lstat`` saw) and the rename-time check of the temporary's name narrow the
window as a best effort; neither closes it (POSIX has no rename-by-descriptor).
For operator-owned files the pathname chain is re-resolved by the kernel at
each step, the same residual.

**A dangling link creates its target**, at the requested mode, when the
target's directory exists (2.7.3 did; the fresh-machine dotfiles shape); it
never creates a missing target DIRECTORY (callers that create directories --
:func:`make_store_dirs` -- create only a plain tail of names that do not exist
yet). The temporary is a hidden ``.pmcp-*`` / ``*.tmp`` name, removed on every
exception; a process killed outright between the write and the rename can leave
it behind in the target's directory.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat
import tempfile
from pathlib import Path, PurePath

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
    """A repository-controlled file pmcp refuses to read or write.

    The message names the file only -- never a link target, which a repository
    chose. Built from the message alone, so ``str()`` is exactly that sentence
    (no ``[Errno ..]`` prefix) and entry points can hand it to the operator;
    ``errno`` is still ``EACCES``, as for any other permission refusal.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.errno = errno.EACCES
        self.strerror = message

    def __str__(self) -> str:
        return str(self.strerror)


# --------------------------------------------------------------------------- #
# Operator-owned files: the final link chain, by pathname.
# --------------------------------------------------------------------------- #


def resolve_write_target(path: Path | str) -> str:
    """The pathname an unconfined write of ``path`` replaces: its final link chain.

    Only the final component's chain is followed, by ``readlink`` and plain
    ``os.path.join`` onto the link's own directory -- never normalised -- so the
    kernel resolves every directory on the way by its own rules. Raises
    ``OSError(ELOOP)`` after 40 hops, ``NotADirectoryError`` for a target that
    ends in a separator, ``IsADirectoryError`` for a directory; the target may
    be absent (a dangling link creates it).
    """
    current = os.fspath(path)
    for _hop in range(_MAX_LINK_HOPS + 1):
        if os.path.basename(current) == "":
            raise NotADirectoryError(errno.ENOTDIR, "Not a directory", current)
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            return current
        if stat.S_ISLNK(st.st_mode):
            text = os.readlink(current)
            current = os.path.join(os.path.dirname(current), text)
            continue
        if stat.S_ISDIR(st.st_mode):
            raise IsADirectoryError(errno.EISDIR, "Is a directory", current)
        return current
    raise OSError(errno.ELOOP, "Too many levels of symbolic links", os.fspath(path))


def make_store_dirs(directory: Path | str, mode: int = 0o700) -> list[str]:
    """Create the plain tail of ``directory`` that does not exist yet; nothing else.

    The kernel decides: ``stat`` the directory; while it is ABSENT, step to its
    parent by ``os.path.dirname``, but only across a plain name -- a ``.``,
    ``..`` or empty component in the missing part is refused (``mkdir -p`` of
    ``missing/../x`` would create ``missing`` and land on ``x``). Any other
    ``stat`` failure (``file/..``, a loop, no permission) is raised, never
    treated as absent. The tail is then created one name at a time, each at
    ``mode``. Returns the directories created.
    """
    current = os.fspath(directory)
    tail: list[str] = []
    while True:
        try:
            st = os.stat(current)
        except FileNotFoundError:
            name = os.path.basename(current)
            if name in ("", os.curdir, os.pardir):
                raise
            tail.append(name)
            current = os.path.dirname(current)
            continue
        if not stat.S_ISDIR(st.st_mode):
            raise NotADirectoryError(errno.ENOTDIR, "Not a directory", current)
        break
    created: list[str] = []
    for name in reversed(tail):
        current = os.path.join(current, name)
        try:
            os.mkdir(current, mode)
        except FileExistsError:
            continue
        created.append(current)
    return created


# --------------------------------------------------------------------------- #
# Repository-controlled files: no symlink anywhere, walked by descriptor.
# --------------------------------------------------------------------------- #

#: Directories are walked through with ``O_PATH`` where it exists (search
#: permission is all the kernel requires for a lookup; every lookup IN the
#: directory is still checked by the kernel itself), else ``O_RDONLY``.
_O_PATH = getattr(os, "O_PATH", 0)


def _walk_flags() -> int:
    return (_O_PATH or os.O_RDONLY) | os.O_DIRECTORY


def _is_link(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & _REPARSE_POINT
    )


class _Confined:
    """The end of a confined walk: the directory reached and the final entry.

    ``at`` is a directory descriptor, or a pathname where ``dir_fd`` is
    unavailable. ``entry`` is the final component's ``lstat`` (``None`` if
    absent); it is a regular file when present.
    """

    def __init__(
        self, at: int | str, name: str, entry: os.stat_result | None, held: list[int]
    ) -> None:
        self.at, self.name, self.entry, self._held = at, name, entry, held

    @property
    def dir_fd(self) -> int:
        assert isinstance(self.at, int)
        return self.at

    def close(self) -> None:
        while self._held:
            try:
                os.close(self._held.pop())
            except OSError:
                pass


def _walk_confined(
    path: Path, confine_to: Path, label: str, verb: str = "write"
) -> _Confined:
    """Walk ``path`` from ``confine_to``, refusing every symlink on the way.

    See the module docstring. Raises ``ConfinedWriteError`` for a symlink (or
    reparse point), a ``.``/``..``/empty component, a path outside the root and
    a final entry that is not a regular file; ``OSError`` for anything the
    kernel refuses (a missing or non-directory component, no permission).
    """
    name_of = os.path.basename(os.fspath(path))
    try:
        parts = PurePath(path).relative_to(PurePath(confine_to)).parts
    except ValueError:
        raise ConfinedWriteError(
            f"{refusing(verb)} {name_of}: it is outside the {label}"
        ) from None
    if not parts or any(p in ("", os.curdir, os.pardir) for p in parts):
        raise ConfinedWriteError(
            f"{refusing(verb)} {name_of}: it is outside the {label}"
        )
    is_symlink = ConfinedWriteError(f"{refusing(verb)} {name_of}: it is a symlink")
    by_fd = _DIR_FD_SUPPORTED
    held: list[int] = []
    handed_over = False
    at: int | str
    if by_fd:
        at = os.open(confine_to, _walk_flags())
        held.append(at)
    else:
        at = os.fspath(confine_to)
    try:
        *dirs, final = parts
        for name in dirs:
            st = (
                os.stat(name, dir_fd=at, follow_symlinks=False)
                if isinstance(at, int)
                else os.lstat(os.path.join(at, name))
            )
            if _is_link(st):
                raise is_symlink
            if not stat.S_ISDIR(st.st_mode):
                raise NotADirectoryError(
                    errno.ENOTDIR, f"Cannot write {name_of}: not a directory"
                )
            if isinstance(at, int):
                # O_NOFOLLOW: a directory swapped for a link after its lstat
                # fails here instead of being followed.
                at = os.open(name, _walk_flags() | os.O_NOFOLLOW, dir_fd=at)
                held.append(at)
            else:
                at = os.path.join(at, name)
        try:
            entry: os.stat_result | None = (
                os.stat(final, dir_fd=at, follow_symlinks=False)
                if isinstance(at, int)
                else os.lstat(os.path.join(at, final))
            )
        except FileNotFoundError:
            entry = None
        if entry is not None:
            if _is_link(entry):
                raise is_symlink
            if stat.S_ISDIR(entry.st_mode):
                raise IsADirectoryError(errno.EISDIR, f"Cannot write {name_of}")
            if not stat.S_ISREG(entry.st_mode):
                raise ConfinedWriteError(
                    f"{refusing(verb)} {name_of}: it is not a regular file"
                )
        walk = _Confined(at, final, entry, held)
        handed_over = True
        return walk
    finally:
        # On failure release what was opened; on success the caller owns it.
        if not handed_over:
            for fd in held:
                try:
                    os.close(fd)
                except OSError:
                    pass


def resolve_confined_target(path: Path, confine_to: Path, label: str) -> Path:
    """The file a confined write of ``path`` would replace (it IS ``path``)."""
    _walk_confined(path, confine_to, label).close()
    return Path(path)


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
    """Atomically replace the file at ``path`` (or its link target) with ``data``.

    ``confine_to`` is required: ``None`` for a file the operator owns (its final
    link chain is followed), the repository root for a file a repository
    controls (no symlink anywhere; ``ConfinedWriteError`` otherwise).
    ``confine_label`` names that root in a refusal. The file is created or
    replaced at ``mode``, ``fsync``-ed, renamed over the destination, and the
    directory ``fsync``-ed best effort. Returns the path actually written.
    """
    if confine_to is None:
        target = resolve_write_target(path)
        _write_by_path(target, data, mode=mode, prefix=prefix, suffix=suffix)
        return Path(target)
    walk = _walk_confined(path, confine_to, confine_label)
    try:
        if isinstance(walk.at, int):
            _write_in_dir(
                walk.at, walk.name, data, mode=mode, prefix=prefix, suffix=suffix
            )
        else:
            _write_by_path(
                os.path.join(walk.at, walk.name),
                data,
                mode=mode,
                prefix=prefix,
                suffix=suffix,
            )
    finally:
        walk.close()
    return Path(path)


def read_confined(
    path: Path, confine_to: Path, label: str = "project", verb: str = "write"
) -> bytes | None:
    """Read a repository-controlled file by the same walk a write takes, or refuse.

    ``None`` when it does not exist yet (or the root does not). Any symlink, a
    non-regular file, a file swapped between its ``lstat`` and its open: a
    ``ConfinedWriteError`` -- nothing is read. ``verb`` names what the caller
    was doing ("write", "read", "load") in the refusal.
    """
    if not os.path.lexists(confine_to):
        return None
    walk = _walk_confined(path, confine_to, label, verb)
    try:
        if walk.entry is None:
            return None
        changed = ConfinedWriteError(
            f"{refusing(verb)} {walk.name}: it changed while it was being read"
        )
        flags = (
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            fd = (
                os.open(walk.name, flags, dir_fd=walk.at)
                if isinstance(walk.at, int)
                else os.open(os.path.join(walk.at, walk.name), flags)
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EMLINK):  # swapped for a link
                raise changed from None
            raise
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or not _same_file(walk.entry, opened):
                raise changed
            return handle.read()
    finally:
        walk.close()


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


_DIR_FD_SUPPORTED = (
    os.open in os.supports_dir_fd
    and os.rename in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)


def _write_by_path(
    target: str, data: bytes, *, mode: int, prefix: str, suffix: str
) -> None:
    # Strings, never Path: the user's spelling reaches the kernel unnormalised.
    parent = os.path.dirname(target) or os.curdir
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
                f"refusing to write {os.path.basename(target)}: its temporary file changed "
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
    _fsync_dir(dir_fd)


def _fsync_dir(dir_fd: int) -> None:
    """Make the rename durable, best effort.

    An ``O_PATH`` descriptor cannot be ``fsync``-ed (``EBADF``), so the directory
    is reopened through it for reading; where that is not allowed (a
    search-only directory) or not supported (macOS ``EINVAL``, some network and
    overlay mounts), the replace has already landed and is not reported failed.
    """
    try:
        os.fsync(dir_fd)
        return
    except OSError:
        pass
    try:
        readable = os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=dir_fd)
    except OSError:
        return
    try:
        os.fsync(readable)
    except OSError:
        pass
    finally:
        os.close(readable)
