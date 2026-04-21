"""Detect how `pmcp` was installed on the current host.

Supports two install paths used in practice:
- `uv tool install pmcp` — lives under `~/.local/share/uv/tools/pmcp`
- `pip install --user pmcp` — lives under the Python user-site tree

Both typically shim `~/.local/bin/pmcp`, so having a binary on PATH does
not tell you which installer owns it. We inspect the running interpreter
and the filesystem layout instead.
"""

from __future__ import annotations

import os
import site
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


InstallMethod = Literal["uv", "pip", "unknown"]


def _uv_tool_dir() -> Path:
    """Return the directory uv puts tool installs under."""
    env = os.environ.get("UV_TOOL_DIR")
    if env:
        return Path(env).expanduser().resolve()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return (base / "uv" / "tools").resolve()


def _has_uv_tool_pmcp() -> bool:
    return (_uv_tool_dir() / "pmcp").is_dir()


def _has_pip_user_pmcp() -> bool:
    """True when pmcp is present in the Python user-site tree."""
    try:
        user_site = site.getusersitepackages()
    except Exception:
        return False
    if not user_site:
        return False
    root = Path(user_site)
    if not root.exists():
        return False
    # pip creates either pmcp/ (pre-PEP 420) or pmcp-*.dist-info
    if (root / "pmcp").is_dir():
        return True
    return any(root.glob("pmcp-*.dist-info"))


def detect_install_method() -> InstallMethod:
    """Best-effort detection of which installer owns the running pmcp.

    Logic:
    - If ``sys.executable`` lives under the uv tool directory, it's uv.
    - Else if pmcp is present in the user-site tree, it's pip --user.
    - Else if the uv tool directory has a pmcp copy, fall back to uv.
    - Otherwise unknown (system pip, pipx, editable dev install, etc.).
    """
    exe = Path(sys.executable).resolve()
    uv_root = _uv_tool_dir()
    try:
        exe.relative_to(uv_root)
        return "uv"
    except ValueError:
        pass

    if _has_pip_user_pmcp():
        return "pip"
    if _has_uv_tool_pmcp():
        return "uv"
    return "unknown"


@dataclass(frozen=True)
class InstallDrift:
    has_drift: bool
    uv_path: Path | None
    pip_user_site: Path | None


def detect_install_drift() -> InstallDrift:
    """Detect the ``uv tool`` + ``pip --user`` dual-install case.

    When both installers have placed a copy of pmcp, whichever shim wins
    ``PATH`` masks the other. Upgrading one leaves the other stale and
    can cause ``pmcp`` to silently run an older version later if PATH
    order changes (e.g., on a fresh login or after a distro upgrade).
    """
    uv_path = _uv_tool_dir() / "pmcp" if _has_uv_tool_pmcp() else None
    pip_site = None
    if _has_pip_user_pmcp():
        try:
            pip_site = Path(site.getusersitepackages())
        except Exception:
            pip_site = None
    has_drift = uv_path is not None and pip_site is not None
    return InstallDrift(has_drift=has_drift, uv_path=uv_path, pip_user_site=pip_site)
