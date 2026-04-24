"""Integration tests for MCP Gateway.

These tests require MCP servers available via config or manifest.
Skip with: pytest tests/test_integration.py -v --skip-integration
"""

from __future__ import annotations

import os
import pytest
from typing import Any

from pmcp.config.loader import load_configs, manifest_server_to_config
from pmcp.client.manager import ClientManager
from pmcp.manifest.loader import load_manifest
from pmcp.policy.policy import PolicyManager
from pmcp.summary import generate_capability_summary
from pmcp.summary.template_fallback import template_summary
from pmcp.server import GatewayServer
from pmcp.tools.handlers import GatewayTools
from pmcp.types import McpTaskRecord, RiskHint, ServerStatus, ServerStatusEnum, ToolInfo


def get_available_servers() -> list:
    """Get all available servers from config and manifest auto-start."""
    # Load user configs
    configs = load_configs()
    # Filter out gateway itself
    configs = [c for c in configs if c.name != "mcp-gateway"]
    seen_servers = {c.name for c in configs}

    # Add manifest auto-start servers
    manifest = load_manifest()
    if manifest:
        for server in manifest.get_auto_start_servers():
            if server.name in seen_servers:
                continue
            # Skip if requires API key that's not set
            if server.requires_api_key and server.env_var:
                if not os.environ.get(server.env_var):
                    continue
            configs.append(manifest_server_to_config(server))

    return configs


def has_mcp_servers() -> bool:
    """Check if there are MCP servers available (config or manifest)."""
    return len(get_available_servers()) > 0


skip_no_servers = pytest.mark.skipif(
    not has_mcp_servers(), reason="No MCP servers available (config or manifest)"
)


class DeterministicTenantTaskManager:
    def __init__(self) -> None:
        self.tasks: dict[tuple[str, str], McpTaskRecord] = {}
        self._tools = {
            "tenant-code-mode::run_script": ToolInfo(
                tool_id="tenant-code-mode::run_script",
                server_name="tenant-code-mode",
                tool_name="run_script",
                description="Submit sandbox code for execution",
                short_description="Run sandbox code",
                input_schema={"type": "object", "properties": {}},
                tags=["tenant", "sandbox"],
                risk_hint=RiskHint.MEDIUM,
                execution={"taskSupport": "optional"},
            )
        }

    def get_tool(self, tool_id: str) -> ToolInfo | None:
        return self._tools.get(tool_id)

    def is_lazy_server(self, name: str) -> bool:
        return False

    def get_server_status(self, name: str) -> ServerStatus | None:
        if name == "tenant-code-mode":
            return ServerStatus(
                name=name,
                status=ServerStatusEnum.ONLINE,
                tool_count=1,
                server_capabilities={"tasks": {}},
            )
        return None

    async def call_tool(
        self,
        tool_id: str,
        args: dict[str, Any],
        timeout_ms: int,
        *,
        task: Any = None,
        trace_context: Any = None,
    ) -> dict[str, Any]:
        record = McpTaskRecord(
            task_id="tenant-run-1",
            status="working",
            status_message="queued",
            raw={"taskId": "tenant-run-1", "status": "working"},
            server_name="tenant-code-mode",
            tool_id=tool_id,
            requestor_context=getattr(task, "requestor_context", None),
        )
        self.tasks[("tenant-code-mode", "tenant-run-1")] = record
        return {"task": {"taskId": "tenant-run-1", "status": "working"}}

    def get_task_record(self, server_name: str, task_id: str) -> McpTaskRecord | None:
        return self.tasks.get((server_name, task_id))

    async def list_tasks(
        self, server_name: str | None = None, cursor: str | None = None
    ) -> dict[str, Any]:
        record = self.tasks[("tenant-code-mode", "tenant-run-1")]
        record.status = "input_required"
        record.raw = {"taskId": record.task_id, "status": "input_required"}
        return {"tasks": [record.model_dump()], "nextCursor": None}

    async def get_task(self, server_name: str, task_id: str) -> McpTaskRecord:
        record = self.tasks[(server_name, task_id)]
        record.status = "completed"
        record.raw = {"taskId": record.task_id, "status": "completed"}
        return record

    async def get_task_result(self, server_name: str, task_id: str) -> dict[str, Any]:
        record = self.tasks[(server_name, task_id)]
        record.status = "completed"
        record.raw = {"taskId": record.task_id, "status": "completed"}
        return {
            "task": record.raw,
            "result": {
                "summary": "completed",
                "diagnostics": "stdout: ok\nsecret=sk-integration-secret",
            },
        }

    async def cancel_task(
        self, server_name: str, task_id: str, force: bool = False
    ) -> tuple[bool, McpTaskRecord | None, str]:
        record = self.tasks.get((server_name, task_id))
        if record is None:
            return (False, None, "Task not found")
        record.status = "cancelled"
        record.raw = {"taskId": record.task_id, "status": "cancelled"}
        return (True, record, "Task cancelled")


@pytest.mark.asyncio
async def test_tenant_code_mode_task_lifecycle_is_deterministic() -> None:
    manager = DeterministicTenantTaskManager()
    gateway = GatewayTools(
        client_manager=manager,  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
    )

    invoked = await gateway.invoke(
        {
            "tool_id": "tenant-code-mode::run_script",
            "arguments": {"language": "python"},
            "task": {"metadata": {"run_kind": "integration"}},
        }
    )
    assert invoked.task is not None
    invoked_status = invoked.task.status
    listed = await gateway.tasks_list({"server_name": "tenant-code-mode"})
    listed_status = listed.tasks[0].status
    got = await gateway.tasks_get(
        {"server_name": "tenant-code-mode", "task_id": "tenant-run-1"}
    )
    assert got.task is not None
    got_status = got.task.status
    result = await gateway.tasks_result(
        {
            "server_name": "tenant-code-mode",
            "task_id": "tenant-run-1",
            "options": {"redact_secrets": True, "max_output_chars": 100},
        }
    )
    cancelled = await gateway.tasks_cancel(
        {"server_name": "tenant-code-mode", "task_id": "tenant-run-1"}
    )

    assert invoked_status == "working"
    assert listed_status == "input_required"
    assert got_status == "completed"
    assert result.ok is True
    assert "sk-integration-secret" not in str(result.result)
    assert cancelled.ok is True
    assert cancelled.task is not None
    assert cancelled.task.status == "cancelled"


class TestConfigLoading:
    """Test config loading from real files and manifest."""

    def test_loads_available_servers(self) -> None:
        """Verify we can discover servers from config and manifest."""
        configs = get_available_servers()
        # Should find servers (from config or manifest auto-start)
        assert isinstance(configs, list)

        # Print what was found for debugging
        for cfg in configs:
            print(f"  Found: {cfg.name} ({cfg.source})")


@pytest.mark.live
@skip_no_servers
class TestServerConnection:
    """Test connecting to real MCP servers."""

    @pytest.mark.asyncio
    async def test_connects_to_servers(self) -> None:
        """Test connecting to available MCP servers."""
        configs = get_available_servers()
        policy = PolicyManager()

        allowed = [c for c in configs if policy.is_server_allowed(c.name)]
        assert len(allowed) > 0, "No allowed servers"

        manager = ClientManager()
        try:
            errors = await manager.connect_all(allowed)

            # Check what connected
            statuses = manager.get_all_server_statuses()
            for status in statuses:
                print(
                    f"  {status.name}: {status.status.value} ({status.tool_count} tools)"
                )

            # At least some should connect (network might be slow)
            online = [s for s in statuses if s.status.value == "online"]
            assert len(online) > 0 or len(errors) > 0, "No servers online and no errors"

        finally:
            await manager.disconnect_all()

    @pytest.mark.asyncio
    async def test_lists_tools_from_servers(self) -> None:
        """Test listing tools from connected servers."""
        configs = get_available_servers()
        policy = PolicyManager()

        allowed = [c for c in configs if policy.is_server_allowed(c.name)]
        manager = ClientManager()

        try:
            errors = await manager.connect_all(allowed)
            tools = manager.get_all_tools()

            print(f"  Found {len(tools)} tools total")
            for tool in tools[:10]:  # First 10
                print(f"    {tool.tool_id}: {tool.short_description[:50]}...")

            if not tools and errors:
                pytest.skip(
                    "No tools discovered because all server connections failed: "
                    + "; ".join(errors)
                )

            # Should have at least some tools when at least one server connected
            assert len(tools) > 0, "No tools found from connected servers"

        finally:
            await manager.disconnect_all()


@pytest.mark.live
@skip_no_servers
class TestSummaryGeneration:
    """Test summary generation with real tools."""

    @pytest.mark.asyncio
    async def test_template_summary_with_real_tools(self) -> None:
        """Test template fallback generates summary for real tools."""
        configs = get_available_servers()
        policy = PolicyManager()

        allowed = [c for c in configs if policy.is_server_allowed(c.name)]
        manager = ClientManager()

        try:
            await manager.connect_all(allowed)
            tools = manager.get_all_tools()

            if not tools:
                pytest.skip("No tools available")

            summary = template_summary(tools)

            print(f"\nTemplate Summary:\n{summary}")

            # Check for MCP Gateway header (format changed with L0 guidance)
            assert "MCP Gateway:" in summary
            assert "gateway.catalog_search" in summary

        finally:
            await manager.disconnect_all()

    @pytest.mark.asyncio
    async def test_generate_capability_summary_fallback(self) -> None:
        """Test generate_capability_summary falls back to template."""
        configs = get_available_servers()
        policy = PolicyManager()

        allowed = [c for c in configs if policy.is_server_allowed(c.name)]
        manager = ClientManager()

        try:
            await manager.connect_all(allowed)
            tools = manager.get_all_tools()

            if not tools:
                pytest.skip("No tools available")

            # With use_llm=False, should use template
            summary = await generate_capability_summary(tools, use_llm=False)

            print(f"\nFallback Summary:\n{summary}")

            assert len(summary) > 0
            assert "gateway" in summary.lower()

        finally:
            await manager.disconnect_all()


@pytest.mark.live
@skip_no_servers
class TestGatewayServer:
    """Test full gateway server initialization."""

    @pytest.mark.asyncio
    async def test_gateway_initializes(self) -> None:
        """Test that gateway server initializes successfully."""
        server = GatewayServer()

        try:
            await server.initialize()

            # Check that capability summary was generated
            assert server._capability_summary, "No capability summary generated"
            print(f"\nCapability Summary:\n{server._capability_summary}")

            # Check that MCP server was created with instructions
            assert server._server is not None, "MCP server not created"

        finally:
            await server.shutdown()
