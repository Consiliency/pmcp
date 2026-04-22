"""Phase 4 and v3 release-gate end-to-end smoke tests."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pmcp.client.manager import ClientManager, ManagedClient
from pmcp.config.loader import make_tool_id
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from pmcp.types import (
    LocalMcpServerConfig,
    ResolvedServerConfig,
    RiskHint,
    ServerStatus,
    ServerStatusEnum,
    ToolInfo,
)


import site as _site

_SRC_DIR = str(Path(__file__).parent.parent / "src")
# User site-packages may not be loaded when HOME is overridden; pin the absolute path.
_USER_SITE = _site.getusersitepackages()


def _run_pmcp(
    args: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    """Run pmcp CLI as a subprocess and capture text output."""
    env = dict(env)
    existing = env.get("PYTHONPATH", "")
    extra = f"{_SRC_DIR}:{_USER_SITE}"
    env["PYTHONPATH"] = f"{extra}:{existing}" if existing else extra
    return subprocess.run(
        [sys.executable, "-m", "pmcp", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


@pytest.mark.asyncio
async def test_phase4_task_gateway_smoke() -> None:
    """Task-aware invocation can be observed and resolved through gateway surfaces."""
    manager = ClientManager()
    tool_id = make_tool_id("task-server", "slow")
    manager._tools[tool_id] = ToolInfo(
        tool_id=tool_id,
        server_name="task-server",
        tool_name="slow",
        description="Slow task",
        short_description="Slow task",
        input_schema={"type": "object"},
        execution={"taskSupport": "optional"},
        tags=[],
        risk_hint=RiskHint.LOW,
    )
    manager._clients["task-server"] = ManagedClient(
        config=ResolvedServerConfig(
            name="task-server",
            source="custom",
            config=LocalMcpServerConfig(command="task-server"),
        ),
        is_remote=True,
        write_stream=MagicMock(),
        status=ServerStatus(
            name="task-server",
            status=ServerStatusEnum.ONLINE,
            tool_count=1,
            server_capabilities={"tasks": {}},
        ),
    )
    manager._send_request = AsyncMock(
        side_effect=[
            {"task": {"taskId": "task-smoke", "status": "working"}},
            {"tasks": [{"taskId": "task-smoke", "status": "working"}]},
            {
                "result": {"ok": True},
                "task": {"taskId": "task-smoke", "status": "completed"},
            },
        ]
    )
    gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())

    invoked = await gateway.invoke({"tool_id": tool_id, "arguments": {}, "task": {}})
    listed = await gateway.tasks_list({"server_name": "task-server"})
    result = await gateway.tasks_result(
        {"server_name": "task-server", "task_id": "task-smoke"}
    )

    assert invoked.ok is True
    assert invoked.task is not None
    assert invoked.task.task_id == "task-smoke"
    assert listed.ok is True
    assert listed.tasks[0].task_id == "task-smoke"
    assert result.ok is True
    assert result.result == {"ok": True}
    assert result.task is not None
    assert result.task.status == "completed"


@pytest.mark.asyncio
async def test_conformance_release_gate_gateway_smoke() -> None:
    """Protocol, task, trace, auth, and startup state surface together."""
    manager = ClientManager()
    tool_id = make_tool_id("current-server", "slow")
    manager._tools[tool_id] = ToolInfo(
        tool_id=tool_id,
        server_name="current-server",
        tool_name="slow",
        title="Slow Tool",
        description="Slow current-protocol tool",
        short_description="Slow current-protocol tool",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        annotations={"readOnlyHint": True},
        execution={"taskSupport": "optional"},
        schema_dialect="https://json-schema.org/draft/2020-12/schema",
        raw_metadata={"x-release": "kept"},
        tags=[],
        risk_hint=RiskHint.LOW,
    )
    current_status = ServerStatus(
        name="current-server",
        status=ServerStatusEnum.ONLINE,
        tool_count=1,
        protocol_version="2025-11-25",
        server_capabilities={"tasks": {}, "tools": {"listChanged": True}},
    )
    manager._clients["current-server"] = ManagedClient(
        config=ResolvedServerConfig(
            name="current-server",
            source="custom",
            config=LocalMcpServerConfig(command="current-server"),
        ),
        is_remote=True,
        write_stream=MagicMock(),
        status=current_status,
    )
    manager._servers["current-server"] = current_status
    manager._send_request = AsyncMock(
        side_effect=[
            {"task": {"taskId": "release-task", "status": "working"}},
            {
                "result": {"ok": True},
                "task": {"taskId": "release-task", "status": "completed"},
            },
        ]
    )
    gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())

    invoked = await gateway.invoke(
        {
            "tool_id": tool_id,
            "arguments": {},
            "task": {"metadata": {"release": "conform"}},
            "_meta": {
                "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
            },
        }
    )
    described = await gateway.describe({"tool_id": tool_id})
    result = await gateway.tasks_result(
        {"server_name": "current-server", "task_id": "release-task"}
    )
    health = await gateway.health()

    assert invoked.ok is True
    assert invoked.task is not None
    assert invoked.task.task_id == "release-task"
    assert described.title == "Slow Tool"
    assert described.execution == {"taskSupport": "optional"}
    assert described.schema_dialect == "https://json-schema.org/draft/2020-12/schema"
    assert result.result == {"ok": True}
    current_health = next(
        server for server in health.servers if server.name == "current-server"
    )
    assert current_health.protocol_version == "2025-11-25"
    assert health.audit_events is not None
    assert health.audit_events[-1].task_id == "release-task"


@pytest.mark.asyncio
async def test_phase4_lifecycle_refuses_and_forces_active_tasks() -> None:
    """Default refresh refuses active task work; forced refresh cancels it first."""
    manager = ClientManager()
    manager._clients["task-server"] = ManagedClient(
        config=ResolvedServerConfig(
            name="task-server",
            source="custom",
            config=LocalMcpServerConfig(command="task-server"),
        ),
        is_remote=True,
        write_stream=MagicMock(),
        status=ServerStatus(
            name="task-server",
            status=ServerStatusEnum.ONLINE,
            tool_count=0,
            server_capabilities={"tasks": {}},
        ),
    )
    manager._send_request = AsyncMock(
        return_value={"task": {"taskId": "active", "status": "cancelled"}}
    )
    manager._record_task(
        "task-server",
        manager._task_info_from_payload({"taskId": "active", "status": "working"}),
    )
    gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())

    refused = await gateway.refresh({})
    forced = await gateway.refresh({"force": True})

    assert refused.ok is False
    assert refused.mcp_tasks_refused == 1
    assert forced.mcp_tasks_cancelled == 1


def test_phase4_setup_writes_opencode_sse_config(tmp_path: Path) -> None:
    """pmcp setup writes OpenCode SSE config and exits successfully."""
    home = tmp_path / "home"
    home.mkdir(parents=True)

    env = os.environ.copy()
    env["HOME"] = str(home)

    result = _run_pmcp(
        ["setup", "--client", "opencode", "--mode", "sse", "--write"],
        env=env,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "Wrote PMCP setup to:" in result.stdout

    config_path = home / ".config" / "opencode" / "opencode.json"
    parsed = json.loads(config_path.read_text())
    assert parsed["mcp"]["pmcp"] == {
        "type": "remote",
        "url": "http://127.0.0.1:3344/mcp",
        "enabled": True,
    }


def test_phase4_doctor_handles_stale_lock_gracefully(tmp_path: Path) -> None:
    """pmcp doctor warns on lock file and keeps successful exit."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    lock_file = home / ".pmcp" / "gateway.lock"

    project.mkdir(parents=True)
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("99999")

    env = os.environ.copy()
    env["HOME"] = str(home)

    result = _run_pmcp(["doctor", "--project", str(project)], env=env, cwd=project)

    assert result.returncode == 0, result.stderr
    assert "PMCP Doctor" in result.stdout
    assert "[WARN] lock:" in result.stdout
    assert "[FAIL]" not in result.stdout


def test_phase4_doctor_warns_for_unreachable_http_health(tmp_path: Path) -> None:
    """pmcp doctor warns when gateway /health is unreachable."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir(parents=True)
    home.mkdir(parents=True)

    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PMCP_GATEWAY_URL"] = "http://127.0.0.1:9/mcp"

    result = _run_pmcp(
        ["doctor", "--project", str(project), "--timeout", "0.2"],
        env=env,
        cwd=project,
    )

    assert result.returncode == 0
    assert "[WARN] http:" in result.stdout
    assert "http://127.0.0.1:9/health" in result.stdout


def test_phase4_secrets_reports_missing_and_success_paths(tmp_path: Path) -> None:
    """pmcp secrets handles set/check/sync outcomes with stable exit codes."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir(parents=True)
    project.mkdir(parents=True)

    config = {
        "mcpServers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {
                    "OPENAI_API_KEY": "${OPENAI_API_KEY}",
                    "GITHUB_TOKEN": "$GITHUB_TOKEN",
                },
            }
        }
    }
    (project / ".mcp.json").write_text(json.dumps(config))

    env = os.environ.copy()
    env["HOME"] = str(home)

    set_result = _run_pmcp(
        [
            "secrets",
            "set",
            "OPENAI_API_KEY",
            "sk-test",
            "--scope",
            "project",
            "--project",
            str(project),
        ],
        env=env,
        cwd=project,
    )
    assert set_result.returncode == 0, set_result.stderr
    set_output = json.loads(set_result.stdout)
    assert set_output["ok"] is True
    assert set_output["command"] == "secrets.set"

    env_path = project / ".env.pmcp"
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600

    check_result = _run_pmcp(
        ["secrets", "check", "--project", str(project)],
        env=env,
        cwd=project,
    )
    assert check_result.returncode == 0, check_result.stderr
    check_output = json.loads(check_result.stdout)
    assert check_output["ok"] is False
    assert "OPENAI_API_KEY" not in check_output["missing_keys"]
    assert "GITHUB_TOKEN" in check_output["missing_keys"]

    sync_result = _run_pmcp(
        ["secrets", "sync", "--from-scope", "project", "--to-scope", "project"],
        env=env,
        cwd=project,
    )
    assert sync_result.returncode == 0, sync_result.stderr
    sync_output = json.loads(sync_result.stdout)
    assert sync_output["ok"] is False
    assert "must differ" in sync_output["error"]
