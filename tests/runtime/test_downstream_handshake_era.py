"""SL-4.5 — EC-P2-4: a genuine mcp 1.x downstream connected through the 2.x
gateway.

A session-scoped fixture builds a throwaway venv once (`uv venv <tmp>/mcp1
&& VIRTUAL_ENV=<tmp>/mcp1 uv pip install "mcp==1.25.0"` — `1.25.0` is the
version this worktree's own venv resolved before the P2 bump, the known-good
1.x peer) and registers a real 1.x `FastMCP` stdio server as a downstream.
The gateway's own interpreter stays on 2.x — `ClientManager` is real,
in-process, the same class `pmcp.server.GatewayServer` uses — only the
*subprocess* is pinned to 1.x. That is enough to prove the property EC-P2-4
names: `ServerStatus.protocol_version` is an internal model
(`src/pmcp/types.py`), not a wire-only detail, so in-process access through
`ClientManager` is a legitimate way to "connect through the 2.x gateway"
without a booted-subprocess gateway in between.

**No skip path**: if `uv` is unavailable or the 1.x install fails, the
fixture calls `pytest.fail`, not `pytest.skip` — a skipped test exits 0,
which for a CI acceptance gate *is* passing silently, the exact false-green
shape this repo keeps getting burned by (Execution Notes). There is no
legitimate environment to accommodate: `uv sync --all-extras` is already a
precondition of every command in this plan and every CI job, so a runner
without `uv` cannot execute the phase at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from mcp.types.version import HANDSHAKE_PROTOCOL_VERSIONS, LATEST_MODERN_VERSION

from pmcp.client.manager import ClientManager
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

DOWNSTREAM_1X_SRC = '''\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("p2-downstream-1x")


@mcp.tool()
def legacy_echo(text: str) -> str:
    """Echo the supplied text back with a fixed, greppable prefix."""
    return f"legacy-echo:{text}"


if __name__ == "__main__":
    mcp.run()
'''


@dataclass
class Legacy1xDownstream:
    python: Path
    script: Path


@pytest.fixture(scope="session")
def legacy_1x_downstream(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Legacy1xDownstream]:
    uv = shutil.which("uv")
    if uv is None:
        pytest.fail(
            "EC-P2-4 requires `uv` to build a throwaway mcp==1.25.0 venv "
            "for the downstream fixture -- and `uv sync --all-extras` is "
            "already a precondition of every command in this plan and "
            "every CI job, so a runner without `uv` cannot execute this "
            "phase at all. This is pytest.fail, not pytest.skip: a skip "
            "would let EC-P2-4 go green having proven nothing."
        )

    work = tmp_path_factory.mktemp("pmcp-rt-mcp1x")
    venv_dir = work / "venv"

    venv_result = subprocess.run(
        [uv, "venv", str(venv_dir), "--python", "3.11"],
        capture_output=True,
        text=True,
        check=False,
    )
    if venv_result.returncode != 0:
        pytest.fail(
            f"EC-P2-4 fixture: `uv venv` failed "
            f"(exit {venv_result.returncode}):\n{venv_result.stderr}"
        )

    install_result = subprocess.run(
        [uv, "pip", "install", "mcp==1.25.0"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "VIRTUAL_ENV": str(venv_dir)},
    )
    if install_result.returncode != 0:
        pytest.fail(
            f"EC-P2-4 fixture: `uv pip install mcp==1.25.0` failed "
            f"(exit {install_result.returncode}):\n{install_result.stderr}"
        )

    python = venv_dir / "bin" / "python"
    if not python.exists():
        pytest.fail(f"EC-P2-4 fixture: no python at {python} after venv creation")

    script = work / "p2_downstream_1x.py"
    script.write_text(DOWNSTREAM_1X_SRC)

    yield Legacy1xDownstream(python=python, script=script)


@pytest.mark.asyncio
async def test_mcp_1x_downstream_negotiates_a_handshake_version_through_2x_gateway(
    legacy_1x_downstream: Legacy1xDownstream,
) -> None:
    manager = ClientManager()
    config = ResolvedServerConfig(
        name="legacy-1x",
        source="custom",
        config=LocalMcpServerConfig(
            command=str(legacy_1x_downstream.python),
            args=[str(legacy_1x_downstream.script)],
        ),
    )
    try:
        errors = await manager.connect_server(config)
        assert errors == [], errors
        assert manager.is_server_online("legacy-1x") is True

        status = manager._servers["legacy-1x"]
        assert status.protocol_version in HANDSHAKE_PROTOCOL_VERSIONS
        assert status.protocol_version != LATEST_MODERN_VERSION

        # A real tool call, not just a successful handshake.
        gateway_tools = GatewayTools(
            client_manager=manager, policy_manager=PolicyManager()
        )
        result = await gateway_tools.invoke(
            {"tool_id": "legacy-1x::legacy_echo", "arguments": {"text": "1x-proof"}}
        )
        assert result.ok is True
        content = result.result["content"]  # type: ignore[index]
        assert content[0]["text"] == "legacy-echo:1x-proof"
    finally:
        await manager.disconnect_all()
