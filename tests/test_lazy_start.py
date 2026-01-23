"""Tests for lazy-start server semantics (Phase 2)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.types import McpServerConfig, ResolvedServerConfig, ServerStatusEnum


class TestServerStatusEnumLazy:
    """Test ServerStatusEnum.LAZY value exists."""

    def test_lazy_status_exists(self) -> None:
        """ServerStatusEnum should have LAZY value."""
        assert hasattr(ServerStatusEnum, "LAZY")
        assert ServerStatusEnum.LAZY.value == "lazy"

    def test_lazy_is_distinct_from_offline(self) -> None:
        """LAZY should be distinct from OFFLINE."""
        assert ServerStatusEnum.LAZY != ServerStatusEnum.OFFLINE

    def test_lazy_is_distinct_from_error(self) -> None:
        """LAZY should be distinct from ERROR."""
        assert ServerStatusEnum.LAZY != ServerStatusEnum.ERROR


class TestClientManagerLazyConfigs:
    """Test ClientManager lazy config registration."""

    def test_register_lazy_configs_stores_configs(self) -> None:
        """register_lazy_configs() should store configs in _lazy_configs."""
        from pmcp.client.manager import ClientManager

        manager = ClientManager()
        config = ResolvedServerConfig(
            name="test-server",
            source="project",
            config=McpServerConfig(command="echo", args=["hello"]),
        )

        manager.register_lazy_configs([config])

        assert "test-server" in manager._lazy_configs
        assert manager._lazy_configs["test-server"] == config

    def test_register_lazy_configs_creates_lazy_status(self) -> None:
        """register_lazy_configs() should create LAZY status entry."""
        from pmcp.client.manager import ClientManager

        manager = ClientManager()
        config = ResolvedServerConfig(
            name="test-server",
            source="project",
            config=McpServerConfig(command="echo", args=["hello"]),
        )

        manager.register_lazy_configs([config])

        assert "test-server" in manager._servers
        assert manager._servers["test-server"].status == ServerStatusEnum.LAZY

    def test_register_lazy_configs_skips_already_connected(self) -> None:
        """register_lazy_configs() should skip servers that are already connected."""
        from pmcp.client.manager import ClientManager
        from pmcp.types import ServerStatus

        manager = ClientManager()
        # Simulate already connected server
        manager._clients["test-server"] = MagicMock()
        manager._servers["test-server"] = ServerStatus(
            name="test-server",
            status=ServerStatusEnum.ONLINE,
            tool_count=5,
        )

        config = ResolvedServerConfig(
            name="test-server",
            source="project",
            config=McpServerConfig(command="echo", args=["hello"]),
        )

        manager.register_lazy_configs([config])

        # Should NOT be in lazy configs since already connected
        assert "test-server" not in manager._lazy_configs
        # Status should remain ONLINE
        assert manager._servers["test-server"].status == ServerStatusEnum.ONLINE

    def test_is_lazy_server_returns_true_for_lazy(self) -> None:
        """is_lazy_server() should return True for registered lazy servers."""
        from pmcp.client.manager import ClientManager

        manager = ClientManager()
        config = ResolvedServerConfig(
            name="test-server",
            source="project",
            config=McpServerConfig(command="echo", args=["hello"]),
        )

        manager.register_lazy_configs([config])

        assert manager.is_lazy_server("test-server") is True
        assert manager.is_lazy_server("other-server") is False

    def test_get_lazy_server_names(self) -> None:
        """get_lazy_server_names() should return list of lazy server names."""
        from pmcp.client.manager import ClientManager

        manager = ClientManager()
        configs = [
            ResolvedServerConfig(
                name="server-a",
                source="project",
                config=McpServerConfig(command="echo"),
            ),
            ResolvedServerConfig(
                name="server-b",
                source="user",
                config=McpServerConfig(command="echo"),
            ),
        ]

        manager.register_lazy_configs(configs)

        names = manager.get_lazy_server_names()
        assert set(names) == {"server-a", "server-b"}


class TestEnsureConnected:
    """Test ClientManager.ensure_connected() method."""

    @pytest.mark.asyncio
    async def test_returns_true_if_already_online(self) -> None:
        """ensure_connected() should return True if server is already online."""
        from pmcp.client.manager import ClientManager
        from pmcp.types import ServerStatus

        manager = ClientManager()
        # Simulate already connected server
        manager._servers["test-server"] = ServerStatus(
            name="test-server",
            status=ServerStatusEnum.ONLINE,
            tool_count=5,
        )

        result = await manager.ensure_connected("test-server")

        assert result is True

    @pytest.mark.asyncio
    async def test_triggers_connection_for_lazy_server(self) -> None:
        """ensure_connected() should trigger connection for lazy servers."""
        from pmcp.client.manager import ClientManager

        manager = ClientManager()
        config = ResolvedServerConfig(
            name="lazy-server",
            source="project",
            config=McpServerConfig(command="echo", args=["hello"]),
        )
        manager.register_lazy_configs([config])

        async def mock_connect_impl(cfg):
            # Simulate successful connection by setting status to ONLINE
            manager._servers["lazy-server"].status = ServerStatusEnum.ONLINE

        with patch.object(
            manager, "_connect_with_retry", new_callable=AsyncMock
        ) as mock_connect:
            mock_connect.side_effect = mock_connect_impl

            result = await manager.ensure_connected("lazy-server")

        assert result is True
        mock_connect.assert_called_once_with(config)
        # Should be removed from lazy configs after success
        assert "lazy-server" not in manager._lazy_configs

    @pytest.mark.asyncio
    async def test_returns_false_on_connection_failure(self) -> None:
        """ensure_connected() should return False if connection fails."""
        from pmcp.client.manager import ClientManager

        manager = ClientManager()
        config = ResolvedServerConfig(
            name="lazy-server",
            source="project",
            config=McpServerConfig(command="echo"),
        )
        manager.register_lazy_configs([config])

        with patch.object(
            manager, "_connect_with_retry", new_callable=AsyncMock
        ) as mock_connect:
            mock_connect.side_effect = Exception("Connection failed")

            result = await manager.ensure_connected("lazy-server")

        assert result is False
        # Status should be ERROR
        assert manager._servers["lazy-server"].status == ServerStatusEnum.ERROR

    @pytest.mark.asyncio
    async def test_raises_for_unknown_server(self) -> None:
        """ensure_connected() should raise ValueError for unknown servers."""
        from pmcp.client.manager import ClientManager

        manager = ClientManager()

        with pytest.raises(ValueError, match="Unknown server"):
            await manager.ensure_connected("nonexistent-server")

    @pytest.mark.asyncio
    async def test_returns_false_for_offline_non_lazy_server(self) -> None:
        """ensure_connected() should return False for offline (non-lazy) servers."""
        from pmcp.client.manager import ClientManager
        from pmcp.types import ServerStatus

        manager = ClientManager()
        # Server exists but is offline (was connected before)
        manager._servers["offline-server"] = ServerStatus(
            name="offline-server",
            status=ServerStatusEnum.OFFLINE,
            tool_count=0,
        )

        result = await manager.ensure_connected("offline-server")

        assert result is False


class TestGatewayServerInitializeLazy:
    """Test GatewayServer.initialize() separates auto-start from lazy configs."""

    @pytest.mark.asyncio
    async def test_registers_mcp_json_configs_as_lazy(self) -> None:
        """initialize() should register .mcp.json configs as lazy."""
        from pmcp.server import GatewayServer

        with (
            patch("pmcp.server.load_configs") as mock_load,
            patch("pmcp.server.load_manifest") as mock_manifest,
            patch("pmcp.server.filter_self_references", side_effect=lambda x: x),
            patch("pmcp.server.load_disabled_auto_start", return_value=set()),
            patch("pmcp.server.load_descriptions_cache", return_value=None),
            patch("pmcp.server.get_cache_path", return_value=None),
        ):
            # .mcp.json config (should be lazy)
            mock_load.return_value = [
                ResolvedServerConfig(
                    name="config-server",
                    source="project",
                    config=McpServerConfig(command="echo"),
                )
            ]
            # No manifest auto-start servers
            mock_manifest.return_value.get_auto_start_servers.return_value = []

            server = GatewayServer()

            with (
                patch.object(
                    server._client_manager, "register_lazy_configs"
                ) as mock_register,
                patch.object(
                    server._client_manager,
                    "connect_all",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
                patch.object(server._client_manager, "start_health_monitor"),
                patch.object(
                    server._client_manager, "get_all_server_statuses", return_value=[]
                ),
                patch.object(server._client_manager, "get_all_tools", return_value=[]),
                patch(
                    "pmcp.server.generate_capability_summary",
                    new_callable=AsyncMock,
                    return_value="",
                ),
            ):
                await server.initialize()

                # .mcp.json config should be registered as lazy
                mock_register.assert_called_once()
                lazy_configs = mock_register.call_args[0][0]
                assert len(lazy_configs) == 1
                assert lazy_configs[0].name == "config-server"

    @pytest.mark.asyncio
    async def test_connects_manifest_auto_start_servers(self) -> None:
        """initialize() should eagerly connect manifest auto-start servers."""
        from pmcp.server import GatewayServer

        with (
            patch("pmcp.server.load_configs") as mock_load,
            patch("pmcp.server.load_manifest") as mock_manifest,
            patch("pmcp.server.manifest_server_to_config") as mock_convert,
            patch("pmcp.server.filter_self_references", side_effect=lambda x: x),
            patch("pmcp.server.load_disabled_auto_start", return_value=set()),
            patch("pmcp.server.load_descriptions_cache", return_value=None),
            patch("pmcp.server.get_cache_path", return_value=None),
        ):
            mock_load.return_value = []  # No .mcp.json configs

            # Manifest auto-start server
            auto_server = MagicMock()
            auto_server.name = "auto-server"
            auto_server.requires_api_key = False
            mock_manifest.return_value.get_auto_start_servers.return_value = [
                auto_server
            ]

            auto_config = ResolvedServerConfig(
                name="auto-server",
                source="manifest",
                config=McpServerConfig(command="echo"),
            )
            mock_convert.return_value = auto_config

            server = GatewayServer()

            with (
                patch.object(server._client_manager, "register_lazy_configs"),
                patch.object(
                    server._client_manager,
                    "connect_all",
                    new_callable=AsyncMock,
                    return_value=[],
                ) as mock_connect,
                patch.object(server._client_manager, "start_health_monitor"),
                patch.object(
                    server._client_manager, "get_all_server_statuses", return_value=[]
                ),
                patch.object(server._client_manager, "get_all_tools", return_value=[]),
                patch(
                    "pmcp.server.generate_capability_summary",
                    new_callable=AsyncMock,
                    return_value="",
                ),
            ):
                await server.initialize()

                # Auto-start server should be eagerly connected
                mock_connect.assert_called_once()
                connected_configs = mock_connect.call_args[0][0]
                assert len(connected_configs) == 1
                assert connected_configs[0].name == "auto-server"

    @pytest.mark.asyncio
    async def test_lazy_configs_registered_before_auto_start_connect(self) -> None:
        """Lazy configs should be registered before auto-start servers connect."""
        from pmcp.server import GatewayServer

        call_order = []

        with (
            patch("pmcp.server.load_configs") as mock_load,
            patch("pmcp.server.load_manifest") as mock_manifest,
            patch("pmcp.server.manifest_server_to_config") as mock_convert,
            patch("pmcp.server.filter_self_references", side_effect=lambda x: x),
            patch("pmcp.server.load_disabled_auto_start", return_value=set()),
            patch("pmcp.server.load_descriptions_cache", return_value=None),
            patch("pmcp.server.get_cache_path", return_value=None),
        ):
            # Both lazy and auto-start configs
            mock_load.return_value = [
                ResolvedServerConfig(
                    name="lazy-server",
                    source="project",
                    config=McpServerConfig(command="echo"),
                )
            ]

            auto_server = MagicMock()
            auto_server.name = "auto-server"
            auto_server.requires_api_key = False
            mock_manifest.return_value.get_auto_start_servers.return_value = [
                auto_server
            ]

            mock_convert.return_value = ResolvedServerConfig(
                name="auto-server",
                source="manifest",
                config=McpServerConfig(command="echo"),
            )

            server = GatewayServer()

            def track_register(configs):
                call_order.append("register_lazy")

            async def track_connect(configs):
                call_order.append("connect_all")
                return []

            with (
                patch.object(
                    server._client_manager,
                    "register_lazy_configs",
                    side_effect=track_register,
                ),
                patch.object(
                    server._client_manager, "connect_all", side_effect=track_connect
                ),
                patch.object(server._client_manager, "start_health_monitor"),
                patch.object(
                    server._client_manager, "get_all_server_statuses", return_value=[]
                ),
                patch.object(server._client_manager, "get_all_tools", return_value=[]),
                patch(
                    "pmcp.server.generate_capability_summary",
                    new_callable=AsyncMock,
                    return_value="",
                ),
            ):
                await server.initialize()

                # register_lazy should be called before connect_all
                assert call_order == ["register_lazy", "connect_all"]


class TestGatewayToolsLazyStart:
    """Test GatewayTools handlers trigger lazy-start."""

    @pytest.mark.asyncio
    async def test_invoke_triggers_lazy_start_for_unknown_tool(self) -> None:
        """invoke() should trigger lazy-start for tools from lazy servers."""
        from pmcp.tools.handlers import GatewayTools
        from pmcp.types import ToolInfo, RiskHint

        mock_client_manager = MagicMock()
        mock_policy_manager = MagicMock()
        mock_policy_manager.is_tool_allowed.return_value = True
        mock_policy_manager.process_output.return_value = {
            "result": {"data": "ok"},
            "truncated": False,
            "summary": None,
            "raw_size": 100,
        }

        # Tool not found initially, then found after lazy-start
        tool_info = ToolInfo(
            tool_id="lazy-server::some_tool",
            server_name="lazy-server",
            tool_name="some_tool",
            description="A tool",
            short_description="A tool",
            input_schema={"type": "object", "properties": {}},
            tags=["lazy-server"],
            risk_hint=RiskHint.LOW,
        )
        mock_client_manager.get_tool.side_effect = [None, tool_info]
        mock_client_manager.is_lazy_server.return_value = True
        mock_client_manager.ensure_connected = AsyncMock(return_value=True)
        mock_client_manager.is_server_online.return_value = True
        mock_client_manager.call_tool = AsyncMock(return_value={"result": "ok"})

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
        )

        result = await tools.invoke(
            {"tool_id": "lazy-server::some_tool", "arguments": {}}
        )

        # Should have triggered lazy-start
        mock_client_manager.ensure_connected.assert_called()
        assert result.ok is True

    @pytest.mark.asyncio
    async def test_invoke_returns_error_if_lazy_start_fails(self) -> None:
        """invoke() should return error if lazy-start fails."""
        from pmcp.tools.handlers import GatewayTools

        mock_client_manager = MagicMock()
        mock_policy_manager = MagicMock()

        # Tool not found, lazy-start fails
        mock_client_manager.get_tool.return_value = None
        mock_client_manager.is_lazy_server.return_value = True
        mock_client_manager.ensure_connected = AsyncMock(return_value=False)
        mock_client_manager.is_server_online.return_value = False

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
        )

        result = await tools.invoke(
            {"tool_id": "lazy-server::some_tool", "arguments": {}}
        )

        assert result.ok is False
        assert result.errors is not None

    @pytest.mark.asyncio
    async def test_describe_triggers_lazy_start(self) -> None:
        """describe() should trigger lazy-start for tools from lazy servers."""
        from pmcp.tools.handlers import GatewayTools
        from pmcp.types import ToolInfo, RiskHint

        mock_client_manager = MagicMock()
        mock_policy_manager = MagicMock()
        mock_policy_manager.is_tool_allowed.return_value = True

        # Tool not found initially, found after connection
        mock_tool = ToolInfo(
            tool_id="lazy-server::some_tool",
            server_name="lazy-server",
            tool_name="some_tool",
            description="A tool",
            short_description="A tool",
            input_schema={"type": "object", "properties": {}},
            tags=["lazy-server"],
            risk_hint=RiskHint.LOW,
        )

        mock_client_manager.get_tool.side_effect = [None, mock_tool]
        mock_client_manager.is_lazy_server.return_value = True
        mock_client_manager.ensure_connected = AsyncMock(return_value=True)
        mock_client_manager.is_server_online.return_value = True

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
        )

        result = await tools.describe({"tool_id": "lazy-server::some_tool"})

        # Should have triggered lazy-start
        mock_client_manager.ensure_connected.assert_called()
        assert result.tool_name == "some_tool"

    @pytest.mark.asyncio
    async def test_invoke_skips_lazy_start_for_connected_server(self) -> None:
        """invoke() should not trigger lazy-start if server is already connected."""
        from pmcp.tools.handlers import GatewayTools
        from pmcp.types import ToolInfo, RiskHint

        mock_client_manager = MagicMock()
        mock_policy_manager = MagicMock()
        mock_policy_manager.is_tool_allowed.return_value = True
        mock_policy_manager.process_output.return_value = {
            "result": {"data": "ok"},
            "truncated": False,
            "summary": None,
            "raw_size": 100,
        }

        # Tool found immediately (server already connected)
        tool_info = ToolInfo(
            tool_id="connected-server::some_tool",
            server_name="connected-server",
            tool_name="some_tool",
            description="A tool",
            short_description="A tool",
            input_schema={"type": "object", "properties": {}},
            tags=["connected-server"],
            risk_hint=RiskHint.LOW,
        )
        mock_client_manager.get_tool.return_value = tool_info
        mock_client_manager.is_lazy_server.return_value = False
        mock_client_manager.is_server_online.return_value = True
        mock_client_manager.call_tool = AsyncMock(return_value={"result": "ok"})

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
        )

        result = await tools.invoke(
            {"tool_id": "connected-server::some_tool", "arguments": {}}
        )

        # Should NOT have triggered lazy-start since server is not lazy
        mock_client_manager.ensure_connected.assert_not_called()
        assert result.ok is True


class TestHealthOutputWithLazyServers:
    """Test that health output includes lazy servers with correct status."""

    @pytest.mark.asyncio
    async def test_health_shows_lazy_servers(self) -> None:
        """health() should include lazy servers with LAZY status."""
        from pmcp.tools.handlers import GatewayTools
        from pmcp.types import ServerStatus

        mock_client_manager = MagicMock()
        mock_policy_manager = MagicMock()

        # One online, one lazy
        statuses = [
            ServerStatus(name="online-server", status=ServerStatusEnum.ONLINE, tool_count=5),
            ServerStatus(name="lazy-server", status=ServerStatusEnum.LAZY, tool_count=0),
        ]
        mock_client_manager.get_all_server_statuses.return_value = statuses
        mock_client_manager.get_registry_meta.return_value = ("rev-123", 1234567890.0)

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
        )

        result = await tools.health()

        assert len(result.servers) == 2
        server_dict = {s.name: s for s in result.servers}
        assert server_dict["online-server"].status == "online"
        assert server_dict["lazy-server"].status == "lazy"
