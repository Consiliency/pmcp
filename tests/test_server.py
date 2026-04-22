"""Tests for GatewayServer MCP handlers."""

from __future__ import annotations

from pathlib import Path
import json

import pytest
from mcp.types import CallToolRequest, ListToolsRequest

from pmcp.server import GatewayServer
from pmcp.types import (
    PromptArgumentInfo,
    PromptInfo,
    ResourceInfo,
    ServerStatus,
    ServerStatusEnum,
)


class TestGatewayServerInit:
    """Tests for GatewayServer initialization."""

    def test_creates_with_defaults(self) -> None:
        """Test server creates with default values."""
        server = GatewayServer()
        assert server._project_root is None
        assert server._custom_config_path is None
        assert server._server is None

    def test_creates_with_custom_paths(self, tmp_path: Path) -> None:
        """Test server creates with custom paths."""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text("servers:\n  denylist: []\n")

        server = GatewayServer(
            project_root=tmp_path,
            policy_path=policy_file,
        )
        assert server._project_root == tmp_path


class TestResourceHandlers:
    """Tests for resource MCP handlers."""

    @pytest.fixture
    def server_with_resources(self) -> GatewayServer:
        """Create a server with mocked resources."""
        server = GatewayServer()

        # Mock resources in client manager
        server._client_manager._resources = {
            "test::file:///readme.md": ResourceInfo(
                resource_id="test::file:///readme.md",
                server_name="test",
                uri="file:///readme.md",
                name="README",
                description="Project readme",
                mime_type="text/markdown",
            ),
            "test::file:///secret.env": ResourceInfo(
                resource_id="test::file:///secret.env",
                server_name="test",
                uri="file:///secret.env",
                name="Secrets",
                description="Secret config",
                mime_type="text/plain",
            ),
        }

        return server

    def test_get_all_resources(self, server_with_resources: GatewayServer) -> None:
        """Test getting all resources."""
        resources = server_with_resources._client_manager.get_all_resources()
        assert len(resources) == 2

    def test_resource_policy_allows_by_default(
        self, server_with_resources: GatewayServer
    ) -> None:
        """Test that resources are allowed by default."""
        assert server_with_resources._policy_manager.is_resource_allowed(
            "test::file:///readme.md"
        )

    def test_resource_policy_blocks_denylist(self, tmp_path: Path) -> None:
        """Test that resources on denylist are blocked."""
        import json

        policy_file = tmp_path / "policy.json"
        policy_file.write_text(
            json.dumps({"resources": {"denylist": ["*::file:///*.env"]}})
        )

        server = GatewayServer(policy_path=policy_file)
        assert not server._policy_manager.is_resource_allowed(
            "test::file:///secret.env"
        )
        assert server._policy_manager.is_resource_allowed("test::file:///readme.md")


class TestPromptHandlers:
    """Tests for prompt MCP handlers."""

    @pytest.fixture
    def server_with_prompts(self) -> GatewayServer:
        """Create a server with mocked prompts."""
        server = GatewayServer()

        # Mock prompts in client manager
        server._client_manager._prompts = {
            "test::greeting": PromptInfo(
                prompt_id="test::greeting",
                server_name="test",
                name="greeting",
                description="Generate a greeting",
                arguments=[
                    PromptArgumentInfo(
                        name="name",
                        description="Name to greet",
                        required=True,
                    )
                ],
            ),
            "admin::dangerous": PromptInfo(
                prompt_id="admin::dangerous",
                server_name="admin",
                name="dangerous",
                description="A dangerous prompt",
                arguments=None,
            ),
        }

        return server

    def test_get_all_prompts(self, server_with_prompts: GatewayServer) -> None:
        """Test getting all prompts."""
        prompts = server_with_prompts._client_manager.get_all_prompts()
        assert len(prompts) == 2

    def test_prompt_policy_allows_by_default(
        self, server_with_prompts: GatewayServer
    ) -> None:
        """Test that prompts are allowed by default."""
        assert server_with_prompts._policy_manager.is_prompt_allowed("test::greeting")

    def test_prompt_policy_blocks_denylist(self, tmp_path: Path) -> None:
        """Test that prompts on denylist are blocked."""
        import json

        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps({"prompts": {"denylist": ["admin::*"]}}))

        server = GatewayServer(policy_path=policy_file)
        assert not server._policy_manager.is_prompt_allowed("admin::dangerous")
        assert server._policy_manager.is_prompt_allowed("test::greeting")


class TestServerCreation:
    """Tests for MCP server creation."""

    def test_create_server_with_instructions(self) -> None:
        """Test server creates with capability instructions."""
        server = GatewayServer()
        server._create_server(instructions="Test instructions")

        assert server._server is not None
        # Server is created, we just verify it's not None
        # (internal _instructions attribute may not be exposed)

    def test_setup_handlers_requires_server(self) -> None:
        """Test setup_handlers raises if server not initialized."""
        server = GatewayServer()

        with pytest.raises(RuntimeError, match="Server not initialized"):
            server._setup_handlers()

    @pytest.mark.asyncio
    async def test_lifecycle_tools_are_routed_through_call_tool_handler(self) -> None:
        """Lifecycle tool names should dispatch through the JSON response path."""
        server = GatewayServer()
        server._create_server()

        called: list[str] = []

        async def fake_connect(arguments: dict) -> object:
            called.append("connect")
            return {"ok": True, "server": arguments["server_name"]}

        async def fake_disconnect(arguments: dict) -> object:
            called.append("disconnect")
            return {"ok": True, "server": arguments["server_name"]}

        async def fake_restart(arguments: dict) -> object:
            called.append("restart")
            return {"ok": True, "server": arguments["server_name"]}

        server._gateway_tools.connect_server = fake_connect  # type: ignore[method-assign]
        server._gateway_tools.disconnect_server = fake_disconnect  # type: ignore[method-assign]
        server._gateway_tools.restart_server = fake_restart  # type: ignore[method-assign]

        assert server._server is not None
        handler = server._server.request_handlers[CallToolRequest]

        for tool_name in [
            "gateway.connect_server",
            "gateway.disconnect_server",
            "gateway.restart_server",
        ]:
            result = await handler(
                CallToolRequest(
                    params={"name": tool_name, "arguments": {"server_name": "test"}}
                )
            )
            payload = json.loads(result.root.content[0].text)
            assert payload == {"ok": True, "server": "test"}

        assert called == ["connect", "disconnect", "restart"]

    @pytest.mark.asyncio
    async def test_task_tools_are_routed_through_call_tool_handler(self) -> None:
        """Task tool names should dispatch through the JSON response path."""
        server = GatewayServer()
        server._create_server()

        called: list[str] = []

        async def fake_tasks_list(arguments: dict) -> object:
            called.append("list")
            return {"ok": True, "args": arguments}

        async def fake_tasks_get(arguments: dict) -> object:
            called.append("get")
            return {"ok": True, "args": arguments}

        async def fake_tasks_result(arguments: dict) -> object:
            called.append("result")
            return {"ok": True, "args": arguments}

        async def fake_tasks_cancel(arguments: dict) -> object:
            called.append("cancel")
            return {"ok": True, "args": arguments}

        server._gateway_tools.tasks_list = fake_tasks_list  # type: ignore[method-assign]
        server._gateway_tools.tasks_get = fake_tasks_get  # type: ignore[method-assign]
        server._gateway_tools.tasks_result = fake_tasks_result  # type: ignore[method-assign]
        server._gateway_tools.tasks_cancel = fake_tasks_cancel  # type: ignore[method-assign]

        assert server._server is not None
        handler = server._server.request_handlers[CallToolRequest]

        for tool_name in [
            "gateway.tasks_list",
            "gateway.tasks_get",
            "gateway.tasks_result",
            "gateway.tasks_cancel",
        ]:
            result = await handler(
                CallToolRequest(
                    params={
                        "name": tool_name,
                        "arguments": {"server_name": "test", "task_id": "task-1"},
                    }
                )
            )
            payload = json.loads(result.root.content[0].text)
            assert payload["ok"] is True

        assert called == ["list", "get", "result", "cancel"]

    @pytest.mark.asyncio
    async def test_unknown_task_tool_uses_unknown_tool_error_path(self) -> None:
        """Unknown task-ish tool names remain unknown tools."""
        server = GatewayServer()
        server._create_server()

        assert server._server is not None
        handler = server._server.request_handlers[CallToolRequest]
        result = await handler(
            CallToolRequest(params={"name": "gateway.tasks_delete", "arguments": {}})
        )
        payload = json.loads(result.root.content[0].text)
        assert payload["error"] is True
        assert "Unknown tool" in payload["message"]

    @pytest.mark.asyncio
    async def test_conformance_tool_listing_is_deterministic_and_includes_admin_routes(
        self,
    ) -> None:
        server = GatewayServer()
        server._create_server()

        assert server._server is not None
        handler = server._server.request_handlers[ListToolsRequest]
        result = await handler(ListToolsRequest(params={}))
        names = [tool.name for tool in result.root.tools]

        assert names == sorted(names)
        assert {
            "gateway.config_status",
            "gateway.get_startup_policy",
            "gateway.set_startup_policy",
            "gateway.tasks_list",
            "gateway.tasks_get",
            "gateway.tasks_result",
            "gateway.tasks_cancel",
            "gateway.connect_server",
            "gateway.disconnect_server",
            "gateway.restart_server",
        } <= set(names)

    @pytest.mark.asyncio
    async def test_conformance_config_and_lifecycle_tools_route_json(self) -> None:
        server = GatewayServer()
        server._create_server()

        async def fake_config_status() -> object:
            return {"ok": True, "surface": "config_status"}

        async def fake_get_startup_policy() -> object:
            return {"ok": True, "surface": "get_startup_policy"}

        async def fake_set_startup_policy(arguments: dict) -> object:
            return {"ok": True, "surface": "set_startup_policy", "args": arguments}

        server._gateway_tools.config_status = fake_config_status  # type: ignore[method-assign]
        server._gateway_tools.get_startup_policy = fake_get_startup_policy  # type: ignore[method-assign]
        server._gateway_tools.set_startup_policy = fake_set_startup_policy  # type: ignore[method-assign]

        assert server._server is not None
        handler = server._server.request_handlers[CallToolRequest]
        routed: dict[str, str] = {}
        for tool_name in [
            "gateway.config_status",
            "gateway.get_startup_policy",
            "gateway.set_startup_policy",
        ]:
            result = await handler(
                CallToolRequest(
                    params={
                        "name": tool_name,
                        "arguments": {"operation": "add", "names": ["svc"]},
                    }
                )
            )
            payload = json.loads(result.root.content[0].text)
            routed[tool_name] = payload["surface"]
            assert payload["ok"] is True

        assert routed == {
            "gateway.config_status": "config_status",
            "gateway.get_startup_policy": "get_startup_policy",
            "gateway.set_startup_policy": "set_startup_policy",
        }


class TestPolicyIntegration:
    """Tests for policy integration with handlers."""

    def test_combined_policy_filters(self, tmp_path: Path) -> None:
        """Test that policy filters work together."""
        import json

        policy_file = tmp_path / "policy.json"
        policy_file.write_text(
            json.dumps(
                {
                    "servers": {"denylist": ["blocked-server"]},
                    "tools": {"denylist": ["*::delete_*"]},
                    "resources": {"denylist": ["*::*.env"]},
                    "prompts": {"denylist": ["admin::*"]},
                }
            )
        )

        server = GatewayServer(policy_path=policy_file)

        # Check all policies apply
        assert not server._policy_manager.is_server_allowed("blocked-server")
        assert not server._policy_manager.is_tool_allowed("github::delete_repo")
        assert not server._policy_manager.is_resource_allowed("test::file.env")
        assert not server._policy_manager.is_prompt_allowed("admin::dangerous")

        # Check allowed ones still work
        assert server._policy_manager.is_server_allowed("github")
        assert server._policy_manager.is_tool_allowed("github::create_issue")
        assert server._policy_manager.is_resource_allowed("test::readme.md")
        assert server._policy_manager.is_prompt_allowed("test::greeting")


class TestServerStatus:
    """Tests for server status tracking."""

    def test_server_status_fields(self) -> None:
        """Test ServerStatus has all required fields."""
        status = ServerStatus(
            name="test",
            status=ServerStatusEnum.ONLINE,
            tool_count=5,
            resource_count=2,
            prompt_count=1,
            pending_request_count=0,
        )

        assert status.name == "test"
        assert status.status == ServerStatusEnum.ONLINE
        assert status.tool_count == 5
        assert status.resource_count == 2
        assert status.prompt_count == 1
        assert status.pending_request_count == 0
