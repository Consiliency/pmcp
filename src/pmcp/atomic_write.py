"""One atomic-write primitive for every user-owned file PMCP rewrites.

Every PMCP-owned file that is rewritten whole -- the credential stores
(``pmcp.env``, ``.env.pmcp``), the trust store, package approvals, the registry
cache, and the client config ``pmcp setup --write`` emits -- goes through
:func:`atomic_write`. The bytes go to a temporary file in the destination's
directory, are ``fsync``-ed, and are ``os.replace``-d over the destination, so
a write that fails partway leaves the old file byte-intact (Consiliency/pmcp#248).

The module does NOT reimplement the kernel's path resolution. Two rules, each
small enough to leave every hard case to the kernel:

**Operator-owned files (``confine_to=None``): follow the final link chain, hop
by hop.** Users keep ``~/.config/pmcp/pmcp.env`` and friends in a dotfiles
repository and symlink them into place; replacing the link would stop the
dotfiles copy from receiving updates (the #248 regression against 2.7.3). So
only the final component's chain is followed: ``lstat`` it; if it is a link,
``readlink`` it relative to the descriptor of the directory holding it and open
the directory part of its text, spelled exactly as the link spells it, relative
to that descriptor -- no ``normpath``, no ``realpath`` of any strictness, and no
pathname longer than one link's own text, so a chain the kernel resolves is
never refused for ``ENAMETOOLONG``; up to 40 hops (``ELOOP``). Where ``dir_fd``
is unavailable, or opening a directory for the walk needs read permission the
kernel's lookup does not (no ``O_PATH``), the texts are joined into one
pathname instead, with ``PATH_MAX`` as that fallback's residual. Every syscall
gets the user's own spelling, so
the kernel applies its own rules to ``missing/..``, ``file/..``, ``//``,
mode-000 and search-only directories, and to a target that ends in a
separator. Before any of that the whole path is ``stat``-ed once, so the
kernel's whole-lookup verdicts -- the 40-link limit counted across the entire
lookup, permissions, ``ENOTDIR`` -- come first and only ``ENOENT`` (a target
not created yet) lets the write go on. The temporary is created in the
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

    The kernel decides first: ``os.stat`` of the whole path as given. Any
    failure but ``ENOENT`` is raised as is -- the kernel's own whole-lookup rules
    (``ELOOP`` counted over EVERY link in the lookup, symlinked directories on
    each hop included; ``EACCES``; ``ENOTDIR``, a target ending in a separator
    among them) -- so a path the kernel refuses is never written. Only then is
    the final component's chain followed, by ``readlink`` and plain
    ``os.path.join`` onto the link's own directory, never normalised, to find
    the NAME to replace; every directory on the way is still the kernel's to
    resolve. ``ENOENT`` (a dangling link, or no file yet) is allowed: the
    target may be absent and is then created, if its directory exists. Raises
    ``IsADirectoryError`` for a directory.
    """
    current = os.fspath(path)
    try:
        os.stat(current)
    except FileNotFoundError:
        pass  # absent target: the chain below names the file to create
    for _hop in range(_MAX_LINK_HOPS + 1):
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


def open_final_directory(path: Path | str) -> tuple[int, str]:
    """``(directory descriptor, name)`` of the file an unconfined write replaces.

    The kernel decides first (``os.stat`` of the whole path, as in
    :func:`resolve_write_target`). Then the final link chain is followed HOP BY
    HOP through descriptors: the directory holding the current link is held open,
    the link is read relative to it, and the directory part of its text -- spelled
    exactly as the link spells it -- is opened relative to that descriptor (an
    absolute one from ``/``). No pathname ever grows past one link's own text, so
    a chain the kernel resolves is never refused for ``ENAMETOOLONG``
    (Consiliency/pmcp#366 round 10). The caller owns the descriptor.
    """
    spelled = os.fspath(path)
    try:
        os.stat(spelled)
    except FileNotFoundError:
        pass  # absent target: the chain below names the file to create
    fd = open_directory(os.path.dirname(spelled) or os.curdir)
    name = os.path.basename(spelled)
    handed_over = False
    try:
        for _hop in range(_MAX_LINK_HOPS + 1):
            if name == "":
                raise IsADirectoryError(errno.EISDIR, "Is a directory", spelled)
            try:
                st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                break
            if stat.S_ISLNK(st.st_mode):
                text = os.readlink(name, dir_fd=fd)
                head, name = os.path.dirname(text), os.path.basename(text)
                if head:
                    following = open_directory(head, dir_fd=fd)
                    os.close(fd)
                    fd = following
                continue
            if stat.S_ISDIR(st.st_mode):
                raise IsADirectoryError(errno.EISDIR, "Is a directory", spelled)
            break
        else:
            raise OSError(errno.ELOOP, "Too many levels of symbolic links", spelled)
        handed_over = True
        return fd, name
    finally:
        if not handed_over:
            os.close(fd)


def _where(fd: int, name: str, fallback: Path | str) -> str:
    """A display path for what was written: the descriptor's real path where the
    platform can name it, else the path as given. Never used for a syscall."""
    try:
        return os.path.join(os.readlink(f"/proc/self/fd/{fd}"), name)
    except OSError:
        return os.fspath(fallback)


def is_absent(path: Path | str) -> bool:
    """True only when the kernel says ``path`` does not exist (``ENOENT``/``ENOTDIR``).

    Every "is the store there?" decision uses this, never ``Path.exists()`` or
    ``os.path.exists``: those answer ``False`` for EVERY failed lookup, so a
    store the kernel refuses (``ELOOP`` after too many links in one lookup,
    ``EACCES``) read as empty and the next write dropped its keys
    (Consiliency/pmcp#366 round 9). Any other failure is raised.
    """
    try:
        os.stat(path)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return True
        raise
    return False


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


def open_directory(
    path: Path | str, *, dir_fd: int | None = None, nofollow: bool = False
) -> int:
    """THE way pmcp opens a directory to walk through it or to anchor an op on it.

    ``O_PATH`` where it exists (search permission, as the kernel's own lookup
    needs), else ``O_RDONLY`` (which also needs READ permission). Every caller --
    the writer, the readers, both approval stores, the residency walk -- opens
    through here and applies one fallback rule, :func:`falls_back_to_pathname`.
    A test forbids opening a directory any other way in these modules.
    """
    flags = _walk_flags() | (os.O_NOFOLLOW if nofollow else 0)
    if dir_fd is None:
        return os.open(path, flags)
    return os.open(path, flags, dir_fd=dir_fd)


def falls_back_to_pathname(exc: BaseException) -> bool:
    """Should a walk that hit ``exc`` retry by pathname?

    Only without ``O_PATH``, and only for the ``EACCES`` an ``O_RDONLY``
    directory open gets from a directory the user may search but not list
    (0300/0311): the pathname form needs only search, as the kernel does.
    Every other error -- and any error where ``O_PATH`` exists -- stands.
    """
    return (
        not _O_PATH
        and isinstance(exc, PermissionError)
        and not isinstance(exc, ConfinedWriteError)
        and exc.errno == errno.EACCES
    )


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
    """:func:`_walk_confined_by` on descriptors, or by pathname where that is
    unavailable or the descriptor walk hit a directory it may search but not
    list without ``O_PATH`` (:func:`falls_back_to_pathname`)."""
    if _DIR_FD_SUPPORTED:
        try:
            return _walk_confined_by(path, confine_to, label, verb, by_fd=True)
        except OSError as exc:
            if not falls_back_to_pathname(exc):
                raise
    return _walk_confined_by(path, confine_to, label, verb, by_fd=False)


def _walk_confined_by(
    path: Path, confine_to: Path, label: str, verb: str, *, by_fd: bool
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
    held: list[int] = []
    handed_over = False
    at: int | str
    if by_fd:
        at = open_directory(confine_to)
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
                at = open_directory(name, dir_fd=at, nofollow=True)
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
        if _DIR_FD_SUPPORTED:
            try:
                fd, name = open_final_directory(path)
            except OSError as exc:
                # Without O_PATH, opening a directory needs READ permission the
                # kernel's own lookup does not (a 0300/0311 directory): write by
                # pathname instead, which needs only search, as the kernel does.
                if not falls_back_to_pathname(exc):
                    raise
            else:
                try:
                    _write_in_dir(
                        fd, name, data, mode=mode, prefix=prefix, suffix=suffix
                    )
                    return Path(_where(fd, name, path))
                finally:
                    os.close(fd)
        # No dir_fd: the pathname join, whose residual is PATH_MAX on a chain of
        # long relative link texts (the descriptor walk above has none).
        target = resolve_write_target(path)
        _write_by_name(
            os.path.dirname(target) or os.curdir,
            os.path.basename(target),
            data,
            mode=mode,
            prefix=prefix,
            suffix=suffix,
        )
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
    if is_absent(confine_to):
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
    and os.readlink in os.supports_dir_fd
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)


def _write_by_path(
    target: str, data: bytes, *, mode: int, prefix: str, suffix: str
) -> None:
    """The confined write where ``dir_fd`` is unavailable: by name, unnormalised.

    Never ``tempfile`` (``mkstemp`` runs ``abspath`` on its directory, which
    collapses ``jump/..`` lexically); see :func:`_write_by_name`.
    """
    _write_by_name(
        os.path.dirname(target) or os.curdir,
        os.path.basename(target),
        data,
        mode=mode,
        prefix=prefix,
        suffix=suffix,
    )


def _write_by_name(
    parent: str, name: str, data: bytes, *, mode: int, prefix: str, suffix: str
) -> None:
    """The no-``dir_fd`` form of :func:`_write_in_dir`: plain joins, never normalised."""
    tmp = os.path.join(parent, f"{prefix}{secrets.token_hex(8)}{suffix}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags | getattr(os, "O_NOFOLLOW", 0), mode)
    created = os.fstat(fd)
    committed = False
    try:
        with os.fdopen(fd, "wb") as handle:
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            written = os.fstat(handle.fileno())
        current = os.lstat(tmp)
        if stat.S_ISLNK(current.st_mode) or not _same_file(current, written):
            raise ConfinedWriteError(
                f"refusing to write {name}: its temporary file changed "
                "before it could be committed"
            )
        os.replace(tmp, os.path.join(parent, name))
        committed = True
    finally:
        if not committed:
            # Only the temp this call created: a name swapped since is left
            # alone (Consiliency/pmcp#372 round 31).
            try:
                if _same_file(os.lstat(tmp), created):
                    os.unlink(tmp)
            except OSError:
                pass


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
    created = os.fstat(fd)
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
            # Only the temp this call created (Consiliency/pmcp#372 round 31).
            try:
                seen = os.stat(tmp_name, dir_fd=dir_fd, follow_symlinks=False)
                if _same_file(seen, created):
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


# --------------------------------------------------------------------------- #
# A plain regular file, opened -- and created only if absent -- never through
# a link (Consiliency/pmcp#372 round 34). Used for lock files: the singleton
# lock and the stores' sidecar locks.
# --------------------------------------------------------------------------- #


class PlainFileRefused(OSError):
    """The name is not a plain regular file: a link, a reparse point, another
    file type, or a file with other names."""


def _entry(name: str, parent: str, dir_fd: int | None) -> os.stat_result:
    if dir_fd is not None:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    return os.lstat(os.path.join(parent, name))


def check_plain_file(fd: int, name: str, parent: str, dir_fd: int | None) -> None:
    """``fd`` is a regular file with one name, and the entry ``name`` is that
    same file itself -- not a link, not a reparse point. Raises
    :class:`PlainFileRefused` (value-free) otherwise."""
    held = os.fstat(fd)
    if not stat.S_ISREG(held.st_mode) or held.st_nlink != 1:
        raise PlainFileRefused(f"{name} is not a plain regular file")
    try:
        entry = _entry(name, parent, dir_fd)
    except OSError as exc:
        raise PlainFileRefused(f"{name} changed while it was opened") from exc
    tag: int = getattr(entry, "st_reparse_tag", 0) or 0
    if stat.S_ISLNK(entry.st_mode) or tag:
        raise PlainFileRefused(f"{name} is a link")
    if not _same_file(entry, held):
        raise PlainFileRefused(f"{name} changed while it was opened")


# --------------------------------------------------------------------------- #
# Windows: the platform's own no-follow open (Consiliency/pmcp#372 round 35).
# Windows has no O_NOFOLLOW, and CPython's os.open there neither opens a
# reparse point as itself nor lets the file be deleted while open. CreateFileW
# does both, per Microsoft's CreateFileW reference
# (learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-createfilew):
#   * FILE_FLAG_OPEN_REPARSE_POINT: "Normal reparse point processing will not
#     occur; CreateFile will attempt to open the reparse point" -- a symlink or
#     junction at the name is opened as itself, never followed ("If the file is
#     not a reparse point, then this flag is ignored").
#   * CREATE_NEW: "Creates a new file, only if it does not already exist. If the
#     specified file exists, the function fails ... ERROR_FILE_EXISTS (80)."
#     With the reparse-point flag the name itself is the object, so a dangling
#     link or junction there exists and the call fails instead of creating its
#     target.
#   * FILE_SHARE_DELETE: "Enables subsequent open operations ... to request
#     delete access" -- so holding the lock never stops another process from
#     renaming or deleting the file.
# The calls go through a small layer (kernel32, the last error, the handle to
# fd conversion) that tests replace with a model of Windows.
# --------------------------------------------------------------------------- #

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
FILE_SHARE_DELETE = 0x4
CREATE_NEW = 1
OPEN_EXISTING = 3
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_TYPE_DISK = 0x1
ERROR_FILE_NOT_FOUND = 2
ERROR_FILE_EXISTS = 80
ERROR_ALREADY_EXISTS = 183

#: A replacement for the Windows layer (tests); ``None``: the real one, on Windows.
_WINDOWS_FILES: Any = None
_REAL_WINDOWS_FILES: Any = None


def _by_handle_information() -> Any:
    import ctypes
    from ctypes import wintypes

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    return ByHandleFileInformation()


def _windows_files() -> Any:
    """The Windows file layer in use, or ``None`` off Windows."""
    global _REAL_WINDOWS_FILES
    if _WINDOWS_FILES is not None:
        return _WINDOWS_FILES
    if getattr(os, "name", "") != "nt":
        return None
    if _REAL_WINDOWS_FILES is None:  # pragma: no cover - Windows only
        import ctypes
        import msvcrt
        from ctypes import wintypes
        from types import SimpleNamespace

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
        ]
        kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        kernel32.GetFileType.argtypes = [wintypes.HANDLE]
        kernel32.GetFileType.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        _REAL_WINDOWS_FILES = SimpleNamespace(
            kernel32=kernel32,
            get_last_error=ctypes.get_last_error,  # type: ignore[attr-defined]
            open_osfhandle=msvcrt.open_osfhandle,  # type: ignore[attr-defined]
            invalid_handle=ctypes.c_void_p(-1).value,
        )
    return _REAL_WINDOWS_FILES


def _windows_error(code: int, name: str) -> OSError:
    if code == ERROR_FILE_NOT_FOUND:
        return FileNotFoundError(errno.ENOENT, f"{name} does not exist")
    if code in (ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS):
        return FileExistsError(errno.EEXIST, f"{name} exists")
    return OSError(errno.EACCES, f"{name} could not be opened (Windows error {code})")


def _open_windows(
    layer: Any, path: str, name: str, *, write: bool, create: bool
) -> int:
    """Open ``path`` -- creating it only if absent -- as itself, never through a
    reparse point; return a CRT descriptor for a plain regular file with one
    name. ``FileExistsError`` when a creation race is lost."""
    import ctypes

    kernel32 = layer.kernel32
    access = GENERIC_READ | (GENERIC_WRITE if write else 0)
    share = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
    flags = FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT
    invalid = (None, layer.invalid_handle)

    handle = kernel32.CreateFileW(path, access, share, None, OPEN_EXISTING, flags, None)
    if handle in invalid:
        code = layer.get_last_error()
        if code != ERROR_FILE_NOT_FOUND or not create:
            raise _windows_error(code, name)
        handle = kernel32.CreateFileW(
            path, access, share, None, CREATE_NEW, flags, None
        )
        if handle in invalid:
            raise _windows_error(layer.get_last_error(), name)
    try:
        info = _by_handle_information()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise _windows_error(layer.get_last_error(), name)
        if (
            info.dwFileAttributes
            & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)
            or kernel32.GetFileType(handle) != FILE_TYPE_DISK
            or info.nNumberOfLinks != 1
        ):
            raise PlainFileRefused(f"{name} is not a plain regular file")
        fd: int = layer.open_osfhandle(handle, os.O_RDWR if write else os.O_RDONLY)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
    return fd


def open_plain_file(
    name: str,
    *,
    parent: str,
    dir_fd: int | None,
    flags: int,
    mode: int = 0o600,
    create: bool = True,
) -> int:
    """Open the regular file ``name`` in ``parent`` (relative to ``dir_fd``
    where given) and return its descriptor -- creating it only if absent, and
    never through a link on any platform.

    ``flags`` are the access flags (never ``O_CREAT``).
      * Windows: ``CreateFileW`` with ``FILE_FLAG_OPEN_REPARSE_POINT`` --
        ``OPEN_EXISTING``, else ``CREATE_NEW`` (:func:`_open_windows`).
      * POSIX: the existing entry is opened with ``O_NOFOLLOW`` (added here
        whatever ``flags`` say); an absent one is created with
        ``O_CREAT|O_EXCL``, which POSIX requires to fail on ANY existing name,
        a symbolic link -- dangling or not -- included.
      * Anything else: never created (refused).
    Losing a creation race raises ``FileExistsError``: the caller starts again.
    Anything but a plain regular file with one name raises
    :class:`PlainFileRefused`.
    """
    path = os.path.join(parent, name)
    windows = _windows_files()
    if windows is not None:
        fd = _open_windows(
            windows,
            path,
            name,
            write=bool(flags & (os.O_WRONLY | os.O_RDWR)),
            create=create,
        )
        dir_fd = None
    else:
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            if dir_fd is not None:
                fd = os.open(name, flags, mode, dir_fd=dir_fd)
            else:
                fd = os.open(path, flags, mode)
        except FileNotFoundError:
            if not create:
                raise
            if getattr(os, "name", "") != "posix":
                raise PlainFileRefused(
                    f"{name} cannot be created without following a link on this platform"
                ) from None
            exclusive = flags | os.O_CREAT | os.O_EXCL
            if dir_fd is not None:
                fd = os.open(name, exclusive, mode, dir_fd=dir_fd)
            else:
                fd = os.open(path, exclusive, mode)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENXIO, errno.EMLINK):
                raise PlainFileRefused(f"{name} is not a plain regular file") from exc
            raise
    try:
        check_plain_file(fd, name, parent, dir_fd)
    except BaseException:
        os.close(fd)
        raise
    return fd
