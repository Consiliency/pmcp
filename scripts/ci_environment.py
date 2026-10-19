"""Record a fixed list of tool and dependency versions, without host paths."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PACKAGES = (
    "pytest",
    "pytest-xdist",
    "pytest-cov",
    "coverage",
    "mcp",
    "anyio",
    "uvicorn",
    "sse-starlette",
)


def environment() -> dict[str, object]:
    versions = {"python": ".".join(map(str, sys.version_info[:3]))}
    for tool, pattern in (
        ("uv", r"uv ([0-9]+\.[0-9]+\.[0-9]+)(?: .*)?"),
        ("node", r"v([0-9]+\.[0-9]+\.[0-9]+)"),
        ("npm", r"([0-9]+\.[0-9]+\.[0-9]+)"),
    ):
        output = subprocess.check_output(
            [tool, "--version"], text=True, stderr=subprocess.DEVNULL, timeout=10
        ).strip()
        match = re.fullmatch(pattern, output)
        if not match:
            raise ValueError("unexpected tool version")
        versions[tool] = match[1]
    packages = {}
    for name in PACKAGES:
        value = importlib.metadata.version(name)
        if not re.fullmatch(r"[0-9][0-9a-z.+!-]{0,63}", value):
            raise ValueError("unexpected dependency version")
        packages[name] = value
    runner = {}
    for key in ("ImageOS", "ImageVersion"):
        value = os.environ.get(key, "unknown")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", value):
            raise ValueError("unexpected runner version")
        runner[key] = value
    return {"schema": 1, "tools": versions, "dependencies": packages, "runner": runner}


if __name__ == "__main__":
    try:
        Path(sys.argv[1]).write_text(json.dumps(environment(), sort_keys=True) + "\n")
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        importlib.metadata.PackageNotFoundError,
    ):
        raise SystemExit(
            "Environment identity unavailable (raw diagnostics suppressed)"
        )
