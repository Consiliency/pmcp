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
    from pmcp.home_identity import HomePin
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


# Singleton lock support
_LOCK_FILE: Path | None = None
_LOCK_FD = None
#: (st_dev, st_ino) of the lock file this process created and holds: shutdown
#: removes an entry only if it is still that file (Consiliency/pmcp#372 round
#: 31: a remembered path, acted on later, must not reach anything else).
_LOCK_IDENTITY: tuple[int, int] | None = None
#: The lock's directory, held open since acquisition where ``dir_fd`` is
#: supported, so the removal happens in that directory whatever its path
#: names by then.
_LOCK_DIR_FD: int | None = None
#: The HOME the default lock directory was taken under, judged again before
#: any removal (``home_identity.HomePin``).
_LOCK_PIN: HomePin | None = None


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


def acquire_singleton_lock(lock_dir: Path | str | None = None) -> bool:
    """Ensure only one gateway instance runs per user.

    Args:
        lock_dir: Directory for lock file (default: ~/.pmcp)

    Returns:
        True if lock acquired, False if another instance is running
    """
    global _LOCK_FILE, _LOCK_FD, _LOCK_IDENTITY, _LOCK_DIR_FD, _LOCK_PIN

    # Already holding a lock
    if _LOCK_FD is not None:
        logger.debug("Already holding singleton lock")
        return False

    pin = None
    if lock_dir is None:
        # Home-scoped (Consiliency/pmcp#372 round 22): refused while a
        # checkout controls the home directory -- now, and again before the
        # lock is removed at shutdown (round 31).
        from pmcp.home_identity import HomePin

        pin = HomePin(".pmcp")
        lock_dir = pin.path()
    elif isinstance(lock_dir, str):
        lock_dir = Path(lock_dir)

    lock_dir.mkdir(parents=True, exist_ok=True)
    _LOCK_FILE = lock_dir / "gateway.lock"

    # Open WITHOUT truncating ("r+" on an ensured-existing file) so a losing
    # second instance cannot wipe the holder's PID before its lock attempt
    # fails. We truncate + write our own PID only after we win the lock.
    try:
        _LOCK_FILE.touch(exist_ok=True)
        fd = open(_LOCK_FILE, "r+")
    except OSError as e:
        logger.warning(f"Could not open singleton lock file {_LOCK_FILE}: {e}")
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
            f"Another gateway instance is running ({pid_info} lock: {_LOCK_FILE}): {e}"
        )
        fd.close()
        return False
    except ImportError as e:
        # No platform locking primitive available (e.g. an exotic Windows build
        # without msvcrt). Don't crash startup — proceed without single-instance
        # protection rather than re-introducing the #84 import-crash class.
        logger.warning(
            f"Singleton lock primitive unavailable ({e}); proceeding without "
            "single-instance protection."
        )
        fd.close()
        return True

    _LOCK_FD = fd
    held = os.fstat(fd.fileno())
    _LOCK_IDENTITY = (held.st_dev, held.st_ino)
    _LOCK_PIN = pin
    _LOCK_DIR_FD = _hold_lock_directory(lock_dir, _LOCK_FILE.name, _LOCK_IDENTITY)
    try:
        _LOCK_FD.seek(0)
        _LOCK_FD.truncate(0)
        _LOCK_FD.write(str(os.getpid()))
        _LOCK_FD.flush()
    except Exception:
        pass
    logger.debug(f"Acquired singleton lock: {_LOCK_FILE}")
    return True


def _hold_lock_directory(
    lock_dir: Path, name: str, identity: tuple[int, int]
) -> int | None:
    """Open the lock's directory and keep it, if it is the one the lock is in.

    ``None`` where ``dir_fd`` is unsupported, or when the directory opened is
    not the one holding the file this process just locked.
    """
    if not (os.unlink in os.supports_dir_fd and os.stat in os.supports_dir_fd):
        return None
    try:
        directory = os.open(lock_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return None
    try:
        seen = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except OSError:
        seen = None
    if seen is None or (seen.st_dev, seen.st_ino) != identity:
        os.close(directory)
        return None
    return directory


def _remove_own_lock(
    path: Path | None,
    identity: tuple[int, int] | None,
    directory: int | None,
    pin: HomePin | None,
) -> None:
    """Remove the lock file -- only the one this process created, and only
    while the HOME its default location was taken under is still the
    operator's. Anything else at that name is left alone."""
    if path is None or identity is None:
        return
    if pin is not None:
        from pmcp.home_identity import HomeInsideCheckoutError

        try:
            pin.path()
        except HomeInsideCheckoutError:
            return  # HOME refused: no removal by name at all
    try:
        if directory is not None:
            seen = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            if (seen.st_dev, seen.st_ino) == identity:
                os.unlink(path.name, dir_fd=directory)
        else:
            seen = os.lstat(path)
            if (seen.st_dev, seen.st_ino) == identity:
                path.unlink()
    except OSError:
        pass


def release_singleton_lock() -> None:
    """Release the singleton lock: always close what was held; remove the
    lock file only if it is still the one this process created."""
    global _LOCK_FILE, _LOCK_FD, _LOCK_IDENTITY, _LOCK_DIR_FD, _LOCK_PIN

    fd, path, identity = _LOCK_FD, _LOCK_FILE, _LOCK_IDENTITY
    directory, pin = _LOCK_DIR_FD, _LOCK_PIN
    _LOCK_FD = _LOCK_FILE = _LOCK_IDENTITY = _LOCK_DIR_FD = _LOCK_PIN = None
    try:
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
        _remove_own_lock(path, identity, directory, pin)
    finally:
        if directory is not None:
            try:
                os.close(directory)
            except OSError:
                pass
