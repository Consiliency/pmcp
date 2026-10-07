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


# Singleton lock support
_LOCK_FILE: Path | None = None
_LOCK_FD = None


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
    try:
        _LOCK_FD.seek(0)
        _LOCK_FD.truncate(0)
        _LOCK_FD.write(str(os.getpid()))
        _LOCK_FD.flush()
    except Exception:
        pass
    logger.debug(f"Acquired singleton lock: {_LOCK_FILE}")
    return True


def release_singleton_lock() -> None:
    """Release the singleton lock."""
    global _LOCK_FD

    if _LOCK_FD:
        # Unlock and close independently so a failing unlock cannot skip close()
        # (closing the fd releases the OS lock regardless).
        try:
            _unlock_fd(_LOCK_FD)
        except Exception:
            pass
        try:
            _LOCK_FD.close()
        except Exception:
            pass
        _LOCK_FD = None

    if _LOCK_FILE and _LOCK_FILE.exists():
        try:
            _LOCK_FILE.unlink()
        except Exception:
            pass
