"""Gateway identity detection to prevent recursive spawning.

This module provides functions to detect if a server configuration would
spawn another instance of the gateway, preventing infinite recursion.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

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


def _holds_the_file_at(fd: TextIO, path: Path) -> bool:
    """Is the locked descriptor the file ``path`` names now? A lock on an
    unlinked or replaced file guards nothing: another process can create and
    lock the file at the path."""
    try:
        held = os.fstat(fd.fileno())
        named = os.stat(path)
    except OSError:
        return False
    return (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino)


def acquire_singleton_lock(lock_dir: Path | str | None = None) -> bool:
    """Ensure only one gateway instance runs per user.

    Args:
        lock_dir: Directory for lock file (default: ~/.pmcp)

    Returns:
        True if lock acquired, False if another instance is running
    """
    global _LOCK_FILE, _LOCK_FD

    # Already holding a lock
    if _LOCK_FD is not None:
        logger.debug("Already holding singleton lock")
        return False

    if lock_dir is None:
        # Home-scoped (Consiliency/pmcp#372 round 22): refused while a
        # checkout controls the home directory.
        from pmcp.home_identity import home_path

        lock_dir = home_path(".pmcp")
    elif isinstance(lock_dir, str):
        lock_dir = Path(lock_dir)

    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "gateway.lock"
    _LOCK_FILE = lock_file

    for _attempt in range(_ACQUIRE_ATTEMPTS):
        # Open WITHOUT truncating ("r+" on an ensured-existing file) so a losing
        # second instance cannot wipe the holder's PID before its lock attempt
        # fails. We truncate + write our own PID only after we win the lock.
        try:
            lock_file.touch(exist_ok=True)
            fd = open(lock_file, "r+")
        except OSError as e:
            logger.warning(f"Could not open singleton lock file {lock_file}: {e}")
            return False

        try:
            _lock_fd_exclusive(fd)
        except (BlockingIOError, OSError) as e:
            pid_info = ""
            try:
                fd.seek(0)
                existing = fd.read().strip()
                if existing:
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

        if _holds_the_file_at(fd, lock_file):
            break
        # Locked a file the path no longer names (unlinked or replaced between
        # the open and the lock): not the singleton. Start again from the open.
        try:
            fd.close()
        except Exception:
            pass
    else:
        logger.warning(f"Could not lock {lock_file}: it was replaced on every attempt")
        return False

    _LOCK_FD = fd
    try:
        _LOCK_FD.seek(0)
        _LOCK_FD.truncate(0)
        _LOCK_FD.write(str(os.getpid()))
        _LOCK_FD.flush()
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


def singleton_lock_held(lock_dir: Path) -> bool | None:
    """Does a gateway hold the lock in ``lock_dir``? ``None``: no lock file.

    A probe for ``pmcp doctor``: opens the existing file (never creates it),
    tries a non-blocking exclusive lock and, if that succeeds, releases it at
    once. A file nobody holds is a leftover and harmless.
    """
    lock_file = lock_dir / "gateway.lock"
    try:
        fd = open(lock_file, "r+")
    except FileNotFoundError:
        return None
    except OSError:
        return True  # cannot probe: report it as possibly held
    try:
        try:
            _lock_fd_exclusive(fd)
        except (BlockingIOError, OSError):
            return True
        except ImportError:
            return None
        try:
            _unlock_fd(fd)
        except Exception:
            pass
        return False
    finally:
        fd.close()
