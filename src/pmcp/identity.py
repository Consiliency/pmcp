"""Gateway identity detection to prevent recursive spawning.

This module provides functions to detect if a server configuration would
spawn another instance of the gateway, preventing infinite recursion.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from pmcp.atomic_write import PlainFileRefused, check_plain_file, open_plain_file
from pmcp.types import LocalMcpServerConfig

if TYPE_CHECKING:
    from pmcp.types import ResolvedServerConfig

logger = logging.getLogger(__name__)

# Known gateway command patterns
GATEWAY_COMMANDS = frozenset({"pmcp", "mcp-gateway"})

# Package managers that might invoke the gateway
PACKAGE_MANAGERS = frozenset({"uvx", "pipx", "uv", "pip", "python", "python3"})


def get_own_identity() -> tuple[str, str]:
    """Get the current process's command identity.

    Returns:
        Tuple of (executable_name, module_name)
    """
    executable = Path(sys.argv[0]).name if sys.argv else ""
    # Handle both direct invocation and module invocation
    module = "pmcp"
    return executable, module


def is_self_reference(config: ResolvedServerConfig) -> bool:
    """Detect if a config would spawn another instance of this gateway.

    This prevents recursive spawning by checking if the command being
    configured would result in another gateway process.

    Args:
        config: The server configuration to check

    Returns:
        True if this config would spawn another gateway instance
    """
    field_name = _self_reference_field(config)
    if field_name is not None:
        logger.debug(
            f"Self-reference detected: {config_field_diagnostic(config, field_name)}"
        )
    return field_name is not None


def _self_reference_field(config: ResolvedServerConfig) -> str | None:
    """The config field that makes *config* spawn this gateway, or ``None``.

    Returns a field NAME (``command``, ``args`` or ``name``), never its value:
    a ``.mcp.json`` entry can inherit ``command`` and ``args`` from a manifest
    overlay, and an overlay's values are never logged (Consiliency/pmcp#342 D9).
    """
    # Handle both nested config (ResolvedServerConfig) and flat config (mock/test)
    if hasattr(config, "config") and config.config is not None:
        nested_config = config.config
        if isinstance(nested_config, LocalMcpServerConfig):
            command = nested_config.command.lower()
            args_lower = [a.lower() for a in nested_config.args]
        else:
            command = ""
            args_lower = []
    else:
        # Fallback for flat config objects (e.g., in tests)
        command = getattr(config, "command", "").lower()
        args_lower = [a.lower() for a in getattr(config, "args", [])]

    # Direct gateway command (e.g., command: pmcp)
    command_base = Path(command).name
    if command_base in GATEWAY_COMMANDS:
        return "command"

    # Check if command is a package manager invoking the gateway
    if command_base in PACKAGE_MANAGERS:
        # Check args for gateway module/package
        for arg in args_lower:
            # Handle: uvx pmcp, pipx run pmcp, python -m pmcp
            if arg in GATEWAY_COMMANDS:
                return "args"
            # Handle paths like /path/to/pmcp
            if Path(arg).name in GATEWAY_COMMANDS:
                return "args"

    # Check config name as fallback (legacy behavior)
    if config.name.lower() in GATEWAY_COMMANDS or config.name.lower() == "mcp-gateway":
        return "name"

    return None


def config_field_diagnostic(config: ResolvedServerConfig, field_name: str) -> str:
    """How a diagnostic names one field of a configured server: never its value.

    The one renderer for a config field on the load, discovery, startup and
    refresh paths (Consiliency/pmcp#342 rev 7, D9). The server is named by
    ``entry_log_name`` (a manifest-derived entry only if pmcp ships its name)
    and the field by its name, because any field of a ``.mcp.json`` entry may
    have been inherited from a manifest overlay.
    """
    from pmcp.manifest.loader import entry_log_name

    who = entry_log_name(
        config.name, manifest_derived=getattr(config, "source", None) == "manifest"
    )
    return f"server {who} (field '{field_name}')"


def filter_self_references(
    configs: list[ResolvedServerConfig],
    suppress_warnings: bool = False,
) -> list[ResolvedServerConfig]:
    """Filter out configs that would cause recursive gateway spawning.

    Args:
        configs: List of server configurations

    Returns:
        Filtered list with self-referential configs removed
    """
    filtered = []
    for config in configs:
        field_name = _self_reference_field(config)
        if field_name is not None:
            if not suppress_warnings:
                logger.warning(
                    f"Excluding {config_field_diagnostic(config, field_name)} "
                    "to prevent recursive spawning: it invokes the gateway"
                )
        else:
            filtered.append(config)
    return filtered


# Singleton lock support. The lock FILE is a persistent inode: it is never
# removed (Consiliency/pmcp#372 round 32). A lock dies with the process that
# holds it, so a leftover file never blocks a start; removing it at shutdown
# let a successor that had just locked the same inode lose its lock to a third
# gateway creating a fresh file.
_LOCK_FILE: Path | None = None
_LOCK_FD = None
#: Attempts to lock the file the path names, when it is unlinked or replaced
#: between the open and the lock.
_ACQUIRE_ATTEMPTS = 5
#: How long a starting gateway waits for a CONTENDED lock before deciding
#: another gateway runs: long enough that ``pmcp doctor``'s momentary probe
#: never makes a start fail (Consiliency/pmcp#372 round 33).
_CONTENDED_WAIT_SECONDS = 1.0
_CONTENDED_POLL_SECONDS = 0.05
_LOCK_NAME = "gateway.lock"
#: Opening the lock file: never following a link, never blocking on a fifo,
#: never leaking into a child. Never O_CREAT: an absent file is created
#: exclusively (``atomic_write.open_plain_file``), never through a link on any
#: platform (Consiliency/pmcp#372 round 34).
_OPEN_FLAGS = (
    os.O_RDWR
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
)
_PROBE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
)


class LockFileRefused(OSError):
    """The lock path names something that is not a plain regular file."""


def _lock_fd_exclusive(fd: TextIO) -> None:
    """Take a non-blocking exclusive advisory lock on an open file.

    Raises ``OSError``/``BlockingIOError`` if another process holds the lock.
    Cross-platform: ``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows —
    PMCP supports native Windows, where importing ``fcntl`` would fail (#84).
    The literal ``sys.platform`` check (not a module-level alias) lets type
    checkers narrow the platform-only ``msvcrt``/``fcntl`` imports.
    """
    if sys.platform == "win32":
        import msvcrt

        fd.seek(0)
        msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fd: TextIO) -> None:
    """Release a lock taken by :func:`_lock_fd_exclusive` (best-effort).

    Closing the descriptor also releases the lock on both platforms, so failures
    here are non-fatal.
    """
    if sys.platform == "win32":
        import msvcrt

        try:
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _lock_dir_fd(lock_dir: Path) -> int | None:
    """The lock directory, opened through THE directory helper
    (``atomic_write.open_directory``: O_PATH where it exists, so a folder the
    user may write and search but not list -- 0300, 0311 -- still opens), where
    directory descriptors are supported (``atomic_write._DIR_FD_SUPPORTED``).
    ``None`` -- the pathname form, every check made by path -- on Windows, on
    a platform without descriptor operations, or when the open fails
    (Consiliency/pmcp#372 round 38)."""
    from pmcp import atomic_write

    if not atomic_write._DIR_FD_SUPPORTED:
        return None
    try:
        return atomic_write.open_directory(lock_dir)
    except OSError:
        return None


#: A reparse point that names another entry (symlink, junction, mount point).
_NAME_SURROGATE = 0x20000000


def _is_link(seen: os.stat_result) -> bool:
    tag: int = getattr(seen, "st_reparse_tag", 0) or 0
    return stat.S_ISLNK(seen.st_mode) or bool(tag & _NAME_SURROGATE)


def _physical_lock_dir(lock_dir: Path, home_scoped: bool) -> Path | None:
    """The directory the lock lives in. An explicit ``--lock-dir`` is used as
    spelled, and a link there is refused later (a repository can supply that
    path). The default ``~/.pmcp`` is judged by THE rule for home-scoped state
    (``trust_store.home_scoped_location``, the trust store's own), so a
    dotfiles-linked ``~/.pmcp`` is accepted exactly where a dotfiles-linked
    ``~/.config/pmcp`` is (Consiliency/pmcp#372 round 36); the lock lives in
    the physical FOLDER that rule resolves to. The leaf ``gateway.lock`` is
    never resolved: it is opened in that folder without following a link
    (round 37). ``None``: refused."""
    if not home_scoped:
        return lock_dir
    from pmcp.trust_store import TrustStoreError, home_scoped_location

    try:
        where = home_scoped_location(
            lock_dir.name, _LOCK_NAME, label="Singleton lock", resolve_leaf=False
        )
    except TrustStoreError:
        return None
    # home_scoped_location has already required the folder it names to be the
    # one the system opens for the operator's own spelling of ~/.pmcp (round
    # 39: a lexically collapsed `file/../secret` is refused, never followed).
    return Path(os.path.dirname(where))


def _directory_at(lock_dir: Path, directory: int | None) -> tuple[int, int] | None:
    """The lock directory at its PATHNAME: a real directory (not a link), and
    -- where one is held -- the very directory ``directory`` is. Its identity,
    or ``None`` when the pathname names something else now."""
    seen = os.lstat(lock_dir)
    if _is_link(seen) or not stat.S_ISDIR(seen.st_mode):
        raise LockFileRefused(f"{lock_dir.name} is not a plain directory")
    if directory is not None:
        held = os.fstat(directory)
        if (held.st_dev, held.st_ino) != (seen.st_dev, seen.st_ino):
            return None
    return (seen.st_dev, seen.st_ino)


def _still_the_lock(
    fd: TextIO,
    lock_dir: Path,
    lock_file: Path,
    directory: int | None,
    directory_seen: tuple[int, int],
) -> bool:
    """After the lock is held and before anything is written, every
    precondition again, from scratch: the directory at its pathname is the one
    opened and not a link; the entry at the path is no link and is the locked
    file; the file is regular with one name."""
    try:
        if _directory_at(lock_dir, directory) != directory_seen:
            return False
        check_plain_file(fd.fileno(), _LOCK_NAME, str(lock_dir), directory)
    except OSError:
        return False
    return True


def _lock_waiting_out_a_probe(fd: TextIO) -> None:
    """Take the lock, retrying a contended one for a short while: a probe
    (``pmcp doctor``) holds it only for an instant; a running gateway keeps
    holding it, and the last attempt's error is raised."""
    deadline = time.monotonic() + _CONTENDED_WAIT_SECONDS
    while True:
        try:
            _lock_fd_exclusive(fd)
            return
        except (BlockingIOError, OSError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(_CONTENDED_POLL_SECONDS)


def acquire_singleton_lock(lock_dir: Path | str | None = None) -> bool:
    """Ensure only one gateway instance runs per user.

    Args:
        lock_dir: Directory for lock file (default: ~/.pmcp)

    Returns:
        True if lock acquired, False if another instance is running or the
        lock path is not a plain regular file in a plain directory
    """
    global _LOCK_FILE

    # Already holding a lock
    if _LOCK_FD is not None:
        logger.debug("Already holding singleton lock")
        return False

    home_scoped = lock_dir is None
    if lock_dir is None:
        # Home-scoped (Consiliency/pmcp#372 round 22): refused while a
        # checkout controls the home directory.
        from pmcp.home_identity import home_path

        lock_dir = home_path(".pmcp")
    elif isinstance(lock_dir, str):
        lock_dir = Path(lock_dir)

    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # ~/.pmcp exists but is not a directory, or cannot be made: refused,
        # value-free, like any other unusable lock path.
        if os.path.islink(lock_dir) and not os.path.exists(lock_dir):
            # A link to a folder not created yet: say so, naming the link only.
            logger.warning(
                f"Refusing the singleton lock directory {lock_dir}: it is a link to "
                "a folder that does not exist; create the folder it points to"
            )
        else:
            logger.warning(
                f"Refusing the singleton lock directory {lock_dir}: {e.strerror}"
            )
        return False
    lock_file = lock_dir / _LOCK_NAME
    _LOCK_FILE = lock_file
    for _attempt in range(_ACQUIRE_ATTEMPTS):
        physical = _physical_lock_dir(lock_dir, home_scoped)
        if physical is None:
            logger.warning(
                f"Refusing the singleton lock directory {lock_dir}: it leads into "
                "a checkout, or cannot be resolved"
            )
            return False
        # Every attempt starts again from opening the DIRECTORY: a retry never
        # trusts a directory descriptor an earlier attempt opened.
        directory = _lock_dir_fd(physical)
        try:
            outcome = _attempt_acquire(
                physical,
                physical / _LOCK_NAME,
                directory,
                lambda: _physical_lock_dir(lock_dir, home_scoped) == physical,
            )
        finally:
            if directory is not None:
                os.close(directory)
        if outcome is not None:
            return outcome
    logger.warning(f"Could not lock {lock_file}: it kept changing on every attempt")
    return False


def _attempt_acquire(
    lock_dir: Path,
    lock_file: Path,
    directory: int | None,
    still_leads_here: Callable[[], bool] = lambda: True,
) -> bool | None:
    """One attempt: True/False decided, ``None`` to start again."""
    global _LOCK_FD

    try:
        directory_seen = _directory_at(lock_dir, directory)
    except OSError as e:
        logger.warning(f"Refusing the singleton lock directory {lock_dir}: {e}")
        return False
    if directory_seen is None:
        return None
    # Opened -- or created, only if absent and exclusively -- never through a
    # link (Consiliency/pmcp#372 rounds 33-34). Nothing is truncated or
    # written until the lock is held and every precondition re-checked.
    try:
        raw = open_plain_file(
            _LOCK_NAME,
            parent=str(lock_dir),
            dir_fd=directory,
            flags=_OPEN_FLAGS,
            mode=0o600,
        )
    except FileExistsError:
        return None  # lost a creation race: start again
    except PlainFileRefused as e:
        logger.warning(f"Refusing the singleton lock file {lock_file}: {e}")
        return False
    except OSError as e:
        logger.warning(f"Could not open singleton lock file {lock_file}: {e}")
        return False
    fd = os.fdopen(raw, "r+")

    try:
        _lock_waiting_out_a_probe(fd)
    except (BlockingIOError, OSError) as e:
        pid_info = ""
        try:
            fd.seek(0)
            existing = fd.read(32).strip()
            if existing.isdigit():
                pid_info = f" PID {existing},"
        except Exception:
            pass
        logger.warning(
            f"Another gateway instance is running ({pid_info} lock: {lock_file}): {e}"
        )
        fd.close()
        return False
    except ImportError as e:
        # No platform locking primitive available (e.g. an exotic Windows
        # build without msvcrt). Don't crash startup -- proceed without
        # single-instance protection rather than re-introducing the #84
        # import-crash class.
        logger.warning(
            f"Singleton lock primitive unavailable ({e}); proceeding without "
            "single-instance protection."
        )
        fd.close()
        return True

    if not (
        still_leads_here()
        and _still_the_lock(fd, lock_dir, lock_file, directory, directory_seen)
    ):
        # Held a lock on something the path no longer names, in a
        # directory the path no longer names, or on a file that gained a
        # name: not the singleton. Start again from the directory.
        try:
            fd.close()
        except Exception:
            pass
        return None

    _LOCK_FD = fd
    try:
        os.ftruncate(fd.fileno(), 0)
        os.lseek(fd.fileno(), 0, os.SEEK_SET)
        os.write(fd.fileno(), str(os.getpid()).encode())
    except Exception:
        pass
    logger.debug(f"Acquired singleton lock: {lock_file}")
    return True


def release_singleton_lock() -> None:
    """Release the singleton lock: unlock and close, nothing else.

    The lock file stays (Consiliency/pmcp#372 round 32): removing it, even by
    identity, lets a successor that locked the same inode in between lose its
    lock to a third gateway creating a fresh file.
    """
    global _LOCK_FD, _LOCK_FILE

    fd, _LOCK_FD, _LOCK_FILE = _LOCK_FD, None, None
    if fd:
        # Unlock and close independently so a failing unlock cannot skip
        # close() (closing the fd releases the OS lock regardless).
        try:
            _unlock_fd(fd)
        except Exception:
            pass
        try:
            fd.close()
        except Exception:
            pass


def singleton_lock_held(lock_dir: Path, *, home_scoped: bool = False) -> str:
    """The lock in ``lock_dir``, for ``pmcp doctor``: ``"absent"``, ``"free"``,
    ``"held"`` by a running gateway, or ``"unusable"`` (not a plain regular
    file in a plain directory; a gateway refuses it).

    Read-only: opens the existing file only -- never creating it, never
    following a link, never writing -- tries a non-blocking lock released at
    once, and checks the same preconditions a gateway does.
    """
    physical = _physical_lock_dir(lock_dir, home_scoped)
    if physical is None:
        return "absent" if not os.path.lexists(lock_dir) else "unusable"
    lock_dir = physical
    lock_file = lock_dir / _LOCK_NAME
    try:
        directory_seen = _directory_at(lock_dir, None)
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unusable"
    if directory_seen is None:  # pragma: no cover - no descriptor to compare
        return "unusable"
    try:
        raw = open_plain_file(
            _LOCK_NAME,
            parent=str(lock_dir),
            dir_fd=None,
            flags=_PROBE_FLAGS,
            create=False,
        )
    except FileNotFoundError:
        # Absent -- unless a name is there (a link to nothing, followed where
        # O_NOFOLLOW is unavailable): that is no lock file.
        try:
            os.lstat(lock_file)
        except FileNotFoundError:
            return "absent"
        except OSError:
            pass
        return "unusable"
    except OSError:
        return "unusable"
    fd = os.fdopen(raw, "r")
    try:
        try:
            _lock_fd_exclusive(fd)
        except (BlockingIOError, OSError):
            return "held"
        except ImportError:
            return "absent"
        try:
            if not _still_the_lock(fd, lock_dir, lock_file, None, directory_seen):
                return "unusable"
            return "free"
        finally:
            try:
                _unlock_fd(fd)
            except Exception:
                pass
    finally:
        fd.close()
