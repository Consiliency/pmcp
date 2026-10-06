"""Environment detection - platform and CLI availability."""

from __future__ import annotations

import asyncio
from pmcp.waits import bounded_wait
import logging
import os
import platform
import shutil
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

Platform = Literal["mac", "wsl", "linux", "windows"]


@dataclass
class CLIInfo:
    """Information about a detected CLI."""

    name: str
    path: str
    version: str | None = None
    help_output: str | None = None


@dataclass
class EnvironmentInfo:
    """Detected environment information."""

    platform: Platform
    path: str
    cwd: str
    detected_clis: dict[str, CLIInfo] = field(default_factory=dict)


def detect_platform() -> Platform:
    """Detect the current platform."""
    system = platform.system().lower()

    if system == "darwin":
        return "mac"
    elif system == "linux":
        # Check for WSL
        try:
            with open("/proc/version", "r") as f:
                version = f.read().lower()
                if "microsoft" in version or "wsl" in version:
                    return "wsl"
        except (FileNotFoundError, PermissionError):
            pass
        return "linux"
    elif system == "windows":
        return "windows"
    else:
        # Default to linux for unknown systems
        return "linux"


def _label(name: object) -> str:
    """A CLI's name in a log line: shown only if pmcp ships it (Consiliency/pmcp#342)."""
    from pmcp.manifest.loader import _cli_label

    return _cli_label(name)


async def check_cli(name: str, check_command: list[str]) -> CLIInfo | None:
    """Check if a CLI is available and get its info."""
    # First check if command exists in PATH
    path = shutil.which(check_command[0])
    if not path:
        return None

    try:
        # Run version check command
        process = await asyncio.create_subprocess_exec(
            *check_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await bounded_wait(process.communicate(), timeout=5.0)

        if process.returncode == 0:
            version = stdout.decode().strip() or stderr.decode().strip()
            # Take first line only
            version = version.split("\n")[0][:100]
            return CLIInfo(name=name, path=path, version=version)
        else:
            # Command exists but version check failed - still usable
            return CLIInfo(name=name, path=path)

    except asyncio.TimeoutError:
        logger.debug(f"Timeout checking CLI: {_label(name)}")
        return CLIInfo(name=name, path=path)
    except Exception as e:
        logger.debug(f"Error checking CLI {_label(name)}: {type(e).__name__}")
        return None


async def get_cli_help(
    name: str, help_command: list[str], max_lines: int = 50
) -> str | None:
    """Get help output for a CLI."""
    try:
        process = await asyncio.create_subprocess_exec(
            *help_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await bounded_wait(process.communicate(), timeout=10.0)

        output = stdout.decode() or stderr.decode()
        lines = output.strip().split("\n")[:max_lines]
        return "\n".join(lines)

    except asyncio.TimeoutError:
        logger.debug(f"Timeout getting help for: {_label(name)}")
        return None
    except Exception as e:
        logger.debug(f"Error getting help for {_label(name)}: {type(e).__name__}")
        return None


async def probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
    """Probe for all configured CLIs in parallel."""
    detected: dict[str, CLIInfo] = {}

    async def check_one(name: str, config: dict) -> tuple[str, CLIInfo | None]:
        # One unusable entry (an empty or non-string command) is "not
        # detected", never an exception that fails every probe
        # (Consiliency/pmcp#342).
        try:
            check_cmd = config.get("check_command", [name, "--version"])
            result = await check_cli(name, check_cmd)
        except Exception as exc:
            logger.warning(
                f"probe_clis: skipping an unusable check_command: {type(exc).__name__}"
            )
            return name, None
        return name, result

    # Check all CLIs in parallel
    tasks = [check_one(name, config) for name, config in cli_configs.items()]
    results = await asyncio.gather(*tasks)

    for name, info in results:
        if info:
            detected[name] = info
            logger.debug(f"Detected CLI: {_label(name)}")

    logger.info(
        f"Detected {len(detected)} CLIs: {', '.join(_label(n) for n in detected)}"
    )
    return detected


def get_environment_info(
    detected_clis: dict[str, CLIInfo] | None = None,
) -> EnvironmentInfo:
    """Get current environment information."""
    return EnvironmentInfo(
        platform=detect_platform(),
        path=os.environ.get("PATH", ""),
        cwd=os.getcwd(),
        detected_clis=detected_clis or {},
    )
