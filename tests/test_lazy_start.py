"""Tests for lazy-start server semantics (Phase 2)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.manifest.loader import Manifest, ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig, ServerStatusEnum


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
            config=LocalMcpServerConfig(command="echo", args=["hello"]),
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
            config=LocalMcpServerConfig(command="echo", args=["hello"]),
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
            config=LocalMcpServerConfig(command="echo", args=["hello"]),
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
            config=LocalMcpServerConfig(command="echo", args=["hello"]),
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
                config=LocalMcpServerConfig(command="echo"),
            ),
            ResolvedServerConfig(
                name="server-b",
                source="user",
                config=LocalMcpServerConfig(command="echo"),
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
            config=LocalMcpServerConfig(command="echo", args=["hello"]),
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
            config=LocalMcpServerConfig(command="echo"),
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

    @pytest.mark.asyncio
    async def test_concurrent_lazy_start_is_single_flight(self) -> None:
        """Concurrent ensure_connected calls should trigger one lazy connect."""
        from pmcp.client.manager import ClientManager

        manager = ClientManager()
        config = ResolvedServerConfig(
            name="lazy-server",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        manager.register_lazy_configs([config])
        started = asyncio.Event()
        release = asyncio.Event()

        async def mock_connect_impl(cfg: ResolvedServerConfig) -> None:
            started.set()
            await release.wait()
            manager._servers[cfg.name].status = ServerStatusEnum.ONLINE

        with patch.object(
            manager, "_connect_with_retry", new_callable=AsyncMock
        ) as mock_connect:
            mock_connect.side_effect = mock_connect_impl
            calls = [
                asyncio.create_task(manager.ensure_connected("lazy-server"))
                for _ in range(5)
            ]
            await started.wait()
            release.set()
            results = await asyncio.gather(*calls)

        assert results == [True, True, True, True, True]
        mock_connect.assert_awaited_once_with(config)
        assert "lazy-server" not in manager._lazy_configs

    @pytest.mark.asyncio
    async def test_concurrent_lazy_start_failure_is_single_flight(self) -> None:
        """Concurrent lazy failures should share one failed connection attempt."""
        from pmcp.client.manager import ClientManager

        manager = ClientManager()
        config = ResolvedServerConfig(
            name="lazy-server",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        manager.register_lazy_configs([config])
        started = asyncio.Event()
        release = asyncio.Event()

        async def mock_connect_impl(cfg: ResolvedServerConfig) -> None:
            started.set()
            await release.wait()
            raise RuntimeError("Connection failed")

        with patch.object(
            manager, "_connect_with_retry", new_callable=AsyncMock
        ) as mock_connect:
            mock_connect.side_effect = mock_connect_impl
            calls = [
                asyncio.create_task(manager.ensure_connected("lazy-server"))
                for _ in range(3)
            ]
            await started.wait()
            release.set()
            results = await asyncio.gather(*calls)

        assert results == [False, False, False]
        mock_connect.assert_awaited_once_with(config)
        assert manager._servers["lazy-server"].status == ServerStatusEnum.ERROR

    @pytest.mark.asyncio
    async def test_successful_lazy_start_removes_config_once(self) -> None:
        """After a successful lazy start, later ensure_connected returns online."""
        from pmcp.client.manager import ClientManager

        manager = ClientManager()
        config = ResolvedServerConfig(
            name="lazy-server",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        manager.register_lazy_configs([config])

        async def mock_connect_impl(cfg: ResolvedServerConfig) -> None:
            manager._servers[cfg.name].status = ServerStatusEnum.ONLINE

        with patch.object(
            manager, "_connect_with_retry", new_callable=AsyncMock
        ) as mock_connect:
            mock_connect.side_effect = mock_connect_impl

            assert await manager.ensure_connected("lazy-server") is True
            assert await manager.ensure_connected("lazy-server") is True

        mock_connect.assert_awaited_once_with(config)
        assert "lazy-server" not in manager._lazy_configs


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
            patch("pmcp.server.load_enabled_auto_start", return_value=set()),
            patch("pmcp.server.load_disabled_auto_start", return_value=set()),
            patch("pmcp.server.load_descriptions_cache", return_value=None),
            patch("pmcp.server.get_cache_path", return_value=None),
        ):
            # .mcp.json config (should be lazy)
            mock_load.return_value = [
                ResolvedServerConfig(
                    name="config-server",
                    source="project",
                    config=LocalMcpServerConfig(command="echo"),
                )
            ]
            # No manifest auto-start servers
            mock_manifest.return_value.servers = {}

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
    async def test_initialize_connects_configured_auto_start(self) -> None:
        """initialize() should eagerly connect configured servers listed in autoStart."""
        from pmcp.server import GatewayServer

        with (
            patch("pmcp.server.load_configs") as mock_load,
            patch("pmcp.server.load_manifest") as mock_manifest,
            patch("pmcp.server.filter_self_references", side_effect=lambda x: x),
            patch(
                "pmcp.server.load_enabled_auto_start",
                return_value={"configured-auto"},
            ),
            patch("pmcp.server.load_disabled_auto_start", return_value=set()),
            patch("pmcp.server.load_descriptions_cache", return_value=None),
            patch("pmcp.server.get_cache_path", return_value=None),
        ):
            mock_load.return_value = [
                ResolvedServerConfig(
                    name="configured-auto",
                    source="project",
                    config=LocalMcpServerConfig(command="echo"),
                )
            ]
            mock_manifest.return_value.servers = {}

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

                lazy_configs = mock_register.call_args[0][0]
                assert lazy_configs == []
                mock_connect.assert_called_once()
                eager_configs = mock_connect.call_args[0][0]
                assert [config.name for config in eager_configs] == ["configured-auto"]

    @pytest.mark.asyncio
    async def test_manifest_auto_start_servers_are_lazy_by_default(self) -> None:
        """initialize() should register manifest auto_start servers lazily by default."""
        from pmcp.server import GatewayServer

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "auto-server": ServerConfig(
                    name="auto-server",
                    description="Auto server",
                    keywords=["auto"],
                    install={},
                    command="echo",
                    args=[],
                    auto_start=True,
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        with (
            patch("pmcp.server.load_configs") as mock_load,
            patch("pmcp.server.load_manifest", return_value=manifest),
            patch("pmcp.server.filter_self_references", side_effect=lambda x: x),
            patch("pmcp.server.load_enabled_auto_start", return_value=set()),
            patch("pmcp.server.load_disabled_auto_start", return_value=set()),
            patch("pmcp.server.load_descriptions_cache", return_value=None),
            patch("pmcp.server.get_cache_path", return_value=None),
        ):
            mock_load.return_value = []  # No .mcp.json configs

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

                lazy_configs = mock_register.call_args[0][0]
                assert [config.name for config in lazy_configs] == ["auto-server"]
                mock_connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_legacy_manifest_auto_start_env_connects_manifest_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PMCP_LEGACY_MANIFEST_AUTOSTART=1 keeps old manifest eager behavior."""
        from pmcp.server import GatewayServer

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "auto-server": ServerConfig(
                    name="auto-server",
                    description="Auto server",
                    keywords=["auto"],
                    install={},
                    command="echo",
                    args=[],
                    auto_start=True,
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        monkeypatch.setenv("PMCP_LEGACY_MANIFEST_AUTOSTART", "1")

        with (
            patch("pmcp.server.load_configs", return_value=[]),
            patch("pmcp.server.load_manifest", return_value=manifest),
            patch("pmcp.server.filter_self_references", side_effect=lambda x: x),
            patch("pmcp.server.load_enabled_auto_start", return_value=set()),
            patch("pmcp.server.load_disabled_auto_start", return_value=set()),
            patch("pmcp.server.load_descriptions_cache", return_value=None),
            patch("pmcp.server.get_cache_path", return_value=None),
        ):
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

                assert mock_register.call_args[0][0] == []
                eager_configs = mock_connect.call_args[0][0]
                assert [config.name for config in eager_configs] == ["auto-server"]

    @pytest.mark.asyncio
    async def test_missing_auth_eager_manifest_server_is_skipped(self) -> None:
        """Missing-auth eager manifest servers should not abort startup."""
        from pmcp.server import GatewayServer

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "needs-key": ServerConfig(
                    name="needs-key",
                    description="Needs key",
                    keywords=["auth"],
                    install={},
                    command="echo",
                    args=[],
                    requires_api_key=True,
                    env_var="PMCP_TEST_MISSING_KEY",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        with (
            patch("pmcp.server.load_configs", return_value=[]),
            patch("pmcp.server.load_manifest", return_value=manifest),
            patch("pmcp.server.filter_self_references", side_effect=lambda x: x),
            patch("pmcp.server.load_enabled_auto_start", return_value={"needs-key"}),
            patch("pmcp.server.load_disabled_auto_start", return_value=set()),
            patch("pmcp.server.load_descriptions_cache", return_value=None),
            patch("pmcp.server.get_cache_path", return_value=None),
            patch.dict("os.environ", {"PMCP_TEST_MISSING_KEY": ""}, clear=False),
        ):
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

                assert mock_register.call_args[0][0] == []
                mock_connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_records_startup_observations(self) -> None:
        """initialize() should preserve resolver outcomes for gateway.health."""
        from pmcp.server import GatewayServer
        from pmcp.types import ServerStatus

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={},
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        with (
            patch(
                "pmcp.server.load_configs",
                return_value=[
                    ResolvedServerConfig(
                        name="lazy-server",
                        source="project",
                        config=LocalMcpServerConfig(command="echo"),
                    ),
                    ResolvedServerConfig(
                        name="eager-server",
                        source="project",
                        config=LocalMcpServerConfig(command="echo"),
                    ),
                ],
            ),
            patch("pmcp.server.load_manifest", return_value=manifest),
            patch("pmcp.server.filter_self_references", side_effect=lambda x: x),
            patch("pmcp.server.load_enabled_auto_start", return_value={"eager-server"}),
            patch(
                "pmcp.server.load_disabled_auto_start",
                return_value=set(),
            ),
            patch("pmcp.server.load_descriptions_cache", return_value=None),
            patch("pmcp.server.get_cache_path", return_value=None),
        ):
            server = GatewayServer()

            with (
                patch.object(
                    server._client_manager,
                    "connect_all",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
                patch.object(server._client_manager, "start_health_monitor"),
                patch.object(server._client_manager, "get_all_tools", return_value=[]),
                patch(
                    "pmcp.server.generate_capability_summary",
                    new_callable=AsyncMock,
                    return_value="",
                ),
            ):
                await server.initialize()

        server._client_manager._servers["eager-server"] = ServerStatus(
            name="eager-server",
            status=ServerStatusEnum.ONLINE,
            tool_count=0,
        )
        health = await server._gateway_tools.health()
        by_name = {entry.name: entry for entry in health.servers}
        assert by_name["lazy-server"].startup_policy == "lazy"
        assert by_name["eager-server"].startup_policy == "eager"

    @pytest.mark.asyncio
    async def test_lazy_configs_registered_before_auto_start_connect(self) -> None:
        """Lazy configs should be registered before auto-start servers connect."""
        from pmcp.server import GatewayServer

        call_order = []

        with (
            patch("pmcp.server.load_configs") as mock_load,
            patch("pmcp.server.load_manifest") as mock_manifest,
            patch("pmcp.server.filter_self_references", side_effect=lambda x: x),
            patch("pmcp.server.load_enabled_auto_start", return_value={"auto-server"}),
            patch("pmcp.server.load_disabled_auto_start", return_value=set()),
            patch("pmcp.server.load_descriptions_cache", return_value=None),
            patch("pmcp.server.get_cache_path", return_value=None),
        ):
            # Both lazy and auto-start configs
            mock_load.return_value = [
                ResolvedServerConfig(
                    name="lazy-server",
                    source="project",
                    config=LocalMcpServerConfig(command="echo"),
                )
            ]

            mock_manifest.return_value.servers = {
                "auto-server": ServerConfig(
                    name="auto-server",
                    description="Auto server",
                    keywords=["auto"],
                    install={},
                    command="echo",
                    args=[],
                )
            }

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
            ServerStatus(
                name="online-server", status=ServerStatusEnum.ONLINE, tool_count=5
            ),
            ServerStatus(
                name="lazy-server", status=ServerStatusEnum.LAZY, tool_count=0
            ),
        ]
        mock_client_manager.get_all_server_statuses.return_value = statuses
        mock_client_manager.get_registry_meta.return_value = ("rev-123", 1234567890.0)

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
        )

        with patch.object(tools, "_load_provisioned_registry", return_value={}):
            result = await tools.health()

        assert len(result.servers) == 2
        server_dict = {s.name: s for s in result.servers}
        assert server_dict["online-server"].status == "online"
        assert server_dict["lazy-server"].status == "lazy"
