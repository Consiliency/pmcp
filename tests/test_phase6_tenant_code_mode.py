"""Phase 6 tenant code-mode host integration soak tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from pmcp.client.manager import ClientManager, ManagedClient
from pmcp.config.loader import make_tool_id
from pmcp.manifest.loader import Manifest, ServerConfig
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


TENANT_SERVER = "tenant-code-mode"


def _tenant_tool(name: str, description: str, risk_hint: RiskHint) -> ToolInfo:
    required = ["script"] if name == "run_script" else ["task_id"]
    return ToolInfo(
        tool_id=make_tool_id(TENANT_SERVER, name),
        server_name=TENANT_SERVER,
        tool_name=name,
        title=name.replace("_", " ").title(),
        description=description,
        short_description=description,
        input_schema={
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "Sandbox script submitted to the tenant server",
                },
                "task_id": {
                    "type": "string",
                    "description": "Opaque downstream tenant task ID",
                },
            },
            "required": required,
        },
        output_schema={"type": "object"},
        execution={"taskSupport": "required" if name == "run_script" else "optional"},
        annotations={"readOnlyHint": name == "get_result"},
        tags=["tenant-code-mode", "sandbox", "code", "hosted"],
        risk_hint=risk_hint,
    )


def _tenant_manifest() -> Manifest:
    return Manifest(
        version="1.0",
        cli_alternatives={},
        servers={
            TENANT_SERVER: ServerConfig(
                name=TENANT_SERVER,
                description="Hosted tenant code-mode sandbox MCP server",
                keywords=["tenant", "code", "mode", "sandbox", "hosted"],
                install={"linux": ["npx", "@tenant/code-mode-mcp"]},
                command="tenant-code-mode",
                args=[],
                transport="streamable-http",
                url="https://tenant.example.com/mcp",
                package="@tenant/code-mode-mcp",
            )
        },
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )


def _tenant_gateway(policy_manager: PolicyManager | None = None) -> GatewayTools:
    manager = ClientManager()
    for tool in (
        _tenant_tool(
            "run_script", "Submit hosted sandbox code for execution", RiskHint.HIGH
        ),
        _tenant_tool("get_result", "Fetch hosted sandbox task results", RiskHint.LOW),
        _tenant_tool(
            "cancel_run", "Cancel hosted sandbox task execution", RiskHint.MEDIUM
        ),
    ):
        manager._tools[tool.tool_id] = tool

    status = ServerStatus(
        name=TENANT_SERVER,
        status=ServerStatusEnum.ONLINE,
        tool_count=3,
        server_capabilities={"tasks": {"listChanged": True}},
        protocol_version="2025-11-25",
    )
    manager._clients[TENANT_SERVER] = ManagedClient(
        config=ResolvedServerConfig(
            name=TENANT_SERVER,
            source="custom",
            config=LocalMcpServerConfig(command="tenant-code-mode"),
        ),
        is_remote=True,
        write_stream=MagicMock(),
        status=status,
    )
    manager._servers[TENANT_SERVER] = status

    tasks: dict[str, dict[str, Any]] = {}

    async def fake_send_request(
        managed: ManagedClient,
        method: str,
        params: dict[str, Any],
        **_: Any,
    ) -> dict[str, Any]:
        assert managed.config.name == TENANT_SERVER
        if method == "tools/call":
            script = params["arguments"].get("script", "")
            task_id = "tenant-input-1" if script == "needs input" else "tenant-run-1"
            status_value = "input_required" if script == "needs input" else "working"
            tasks[task_id] = {
                "taskId": task_id,
                "status": status_value,
                "statusMessage": "waiting for approval"
                if status_value == "input_required"
                else "queued",
                "createdAt": "2026-01-02T03:04:05Z",
                "lastUpdatedAt": "2026-01-02T03:04:06Z",
                "ttl": 300,
                "pollInterval": 0.1,
            }
            return {"task": tasks[task_id]}
        if method == "tasks/list":
            return {"tasks": list(tasks.values())}
        if method == "tasks/get":
            return {"task": tasks[params["taskId"]]}
        if method == "tasks/result":
            task_id = params["taskId"]
            tasks[task_id] = {
                **tasks[task_id],
                "status": "completed",
                "statusMessage": "completed",
                "lastUpdatedAt": "2026-01-02T03:04:07Z",
            }
            return {
                "task": tasks[task_id],
                "result": {
                    "stdout": "hello from hosted sandbox",
                    "diagnostic": "api_key=sk-secret artifact_token=tenant-secret",
                },
            }
        if method == "tasks/cancel":
            task_id = params["taskId"]
            tasks[task_id] = {
                **tasks[task_id],
                "status": "cancelled",
                "statusMessage": "cancelled by client",
                "lastUpdatedAt": "2026-01-02T03:04:08Z",
            }
            return {"task": tasks[task_id]}
        raise AssertionError(f"unexpected method {method}")

    manager._send_request = fake_send_request  # type: ignore[method-assign]
    return GatewayTools(
        client_manager=manager,
        policy_manager=policy_manager or PolicyManager(),
    )


@pytest.mark.asyncio
async def test_hostsoak_discovers_describes_invokes_and_tracks_tenant_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PMCP brokers hosted tenant code-mode tasks without local CLI execution."""
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", _tenant_manifest)
    monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
    gateway = _tenant_gateway()

    capability = await gateway.request_capability(
        {
            "query": "tenant-code-mode server for hosted sandbox code",
            "available_clis": [],
        }
    )
    catalog = await gateway.catalog_search(
        {"query": "hosted sandbox code", "include_offline": True}
    )
    described = await gateway.describe(
        {"tool_id": make_tool_id(TENANT_SERVER, "run_script")}
    )
    invoked = await gateway.invoke(
        {
            "tool_id": make_tool_id(TENANT_SERVER, "run_script"),
            "arguments": {"script": "print('hello')"},
            "task": {
                "metadata": {"purpose": "hostsoak"},
                "ttl": 300,
                "poll_interval": 0.1,
                "requestor_context": {"client": "mobile"},
            },
            "_meta": {
                "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
            },
        }
    )
    input_required = await gateway.invoke(
        {
            "tool_id": make_tool_id(TENANT_SERVER, "run_script"),
            "arguments": {"script": "needs input"},
            "task": {"metadata": {"purpose": "hostsoak-input"}},
        }
    )
    listed = await gateway.tasks_list({"server_name": TENANT_SERVER})
    fetched = await gateway.tasks_get(
        {"server_name": TENANT_SERVER, "task_id": "tenant-input-1"}
    )
    result = await gateway.tasks_result(
        {
            "server_name": TENANT_SERVER,
            "task_id": "tenant-run-1",
            "options": {"redact_secrets": True, "max_output_chars": 200},
        }
    )
    cancelled = await gateway.tasks_cancel(
        {"server_name": TENANT_SERVER, "task_id": "tenant-input-1"}
    )

    assert capability.status == "candidates"
    assert capability.candidates
    assert capability.candidates[0].name == TENANT_SERVER
    assert any(card.tool_id.endswith("::run_script") for card in catalog.results)
    assert described.execution == {"taskSupport": "required"}
    assert described.invoke_as == "gateway.invoke"
    assert invoked.ok is True
    assert invoked.task is not None
    assert invoked.task.task_id == "tenant-run-1"
    assert invoked.task.status == "working"
    assert input_required.task is not None
    assert input_required.task.status == "input_required"
    assert {task.status for task in listed.tasks} >= {"working", "input_required"}
    assert fetched.task is not None
    assert fetched.task.status == "input_required"
    assert result.ok is True
    assert result.task is not None
    assert result.task.status == "completed"
    dumped_result = json.dumps(result.model_dump())
    assert "hello from hosted sandbox" in dumped_result
    assert "sk-secret" not in dumped_result
    assert "tenant-secret" not in dumped_result
    assert cancelled.ok is True
    assert cancelled.task is not None
    assert cancelled.task.status == "cancelled"


@pytest.mark.asyncio
async def test_hostsoak_policy_denies_tenant_tool_before_dispatch(
    tmp_path: Path,
) -> None:
    """Tenant policy denial is enforced before downstream dispatch."""
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps({"tools": {"denylist": [make_tool_id(TENANT_SERVER, "run_script")]}})
    )
    gateway = _tenant_gateway(policy_manager=PolicyManager(policy_path))

    denied = await gateway.invoke(
        {
            "tool_id": make_tool_id(TENANT_SERVER, "run_script"),
            "arguments": {"script": "print('blocked')"},
            "task": {"metadata": {"purpose": "hostsoak-denied"}},
        }
    )

    assert denied.ok is False
    assert denied.auth_state == "policy_denied"
    assert denied.errors is not None
    assert '"code":"E402"' in denied.errors[0]
