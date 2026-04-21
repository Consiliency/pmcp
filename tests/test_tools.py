"""Tests for gateway tools."""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pmcp.manifest.loader import CLIAlternative, Manifest, ServerConfig
from pmcp.config.guidance import GuidanceConfig
from pmcp.config.loader import StartupObservation
from pmcp.errors import GatewayException
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools, get_gateway_tool_definitions
from pmcp.types import (
    LocalMcpServerConfig,
    ResolvedServerConfig,
    RequestState,
    RiskHint,
    ServerStatus,
    ServerStatusEnum,
    ToolInfo,
)


class MockClientManager:
    """Mock client manager for testing."""

    def __init__(self, tools: list[ToolInfo] | None = None) -> None:
        self._tools = {t.tool_id: t for t in (tools or [])}
        self._online_servers: set[str] = set()
        self._lazy_servers: set[str] = set()
        self._server_statuses: list[ServerStatus] = []
        self._revision_id = "test-rev"
        self._last_refresh_ts = 1234567890.0
        self.connected_configs: list[Any] = []
        self.refreshed_configs: list[Any] = []
        self.lazy_configs: list[Any] = []
        self.disconnected = False
        self.events: list[str] = []
        self.ensure_connected_calls: list[str] = []
        self.pending_requests: list[Any] = []

    def get_all_tools(self) -> list[ToolInfo]:
        return list(self._tools.values())

    def get_tool(self, tool_id: str) -> ToolInfo | None:
        return self._tools.get(tool_id)

    def is_server_online(self, name: str) -> bool:
        return name in self._online_servers

    def is_lazy_server(self, name: str) -> bool:
        return name in self._lazy_servers

    def get_lazy_server_names(self) -> list[str]:
        return list(self._lazy_servers)

    def add_lazy_server(self, name: str) -> None:
        self._lazy_servers.add(name)

    def set_server_online(self, name: str) -> None:
        self._online_servers.add(name)

    async def ensure_connected(self, server_name: str) -> bool:
        self.ensure_connected_calls.append(server_name)
        if server_name in self._online_servers:
            return True
        if server_name in self._lazy_servers:
            self._lazy_servers.remove(server_name)
            self._online_servers.add(server_name)
            return True
        raise ValueError(f"Unknown server: {server_name}")

    def get_all_server_statuses(self) -> list[Any]:
        return self._server_statuses

    def get_server_status(self, name: str) -> ServerStatus | None:
        for status in self._server_statuses:
            if status.name == name:
                return status
        if name in self._online_servers:
            return ServerStatus(name=name, status=ServerStatusEnum.ONLINE, tool_count=0)
        if name in self._lazy_servers:
            return ServerStatus(name=name, status=ServerStatusEnum.LAZY, tool_count=0)
        return None

    def set_server_statuses(self, statuses: list[ServerStatus]) -> None:
        self._server_statuses = statuses

    def get_registry_meta(self) -> tuple[str, float]:
        return (self._revision_id, self._last_refresh_ts)

    async def call_tool(
        self, tool_id: str, args: dict[str, Any], timeout_ms: int
    ) -> Any:
        return {"content": [{"type": "text", "text": "result"}]}

    async def refresh(self, configs: list[Any]) -> list[str]:
        self.refreshed_configs = list(configs)
        return []

    async def disconnect_all(self) -> None:
        self.events.append("disconnect")
        self.disconnected = True

    def register_lazy_configs(self, configs: list[Any]) -> None:
        self.events.append("register_lazy")
        self.lazy_configs = list(configs)
        for config in configs:
            self._lazy_servers.add(config.name)

    async def connect_all(self, configs: list[Any], retry: bool = True) -> list[str]:
        self.events.append("connect")
        self.connected_configs.extend(configs)
        for config in configs:
            self._online_servers.add(config.name)
        return []

    async def connect_server(self, config: Any, retry: bool = True) -> list[str]:
        self.events.append(f"connect_server:{config.name}")
        self.connected_configs.append(config)
        if config.name == "fails-connect":
            return [f"Failed to connect to {config.name}: boom"]
        self._online_servers.add(config.name)
        self._lazy_servers.discard(config.name)
        self._server_statuses = [
            status for status in self._server_statuses if status.name != config.name
        ]
        self._server_statuses.append(
            ServerStatus(name=config.name, status=ServerStatusEnum.ONLINE, tool_count=0)
        )
        return []

    async def disconnect_server(
        self, name: str, force: bool = False
    ) -> tuple[bool, int, str | None]:
        self.events.append(f"disconnect_server:{name}:{force}")
        pending = self.get_pending_requests(name)
        if pending and not force:
            return (
                False,
                0,
                "Disconnect refused because this server has pending requests. "
                "Use force=true to cancel them.",
            )
        cancelled = self.cancel_pending_requests(name) if pending else 0
        self._online_servers.discard(name)
        self._lazy_servers.add(name)
        self._server_statuses = [
            status for status in self._server_statuses if status.name != name
        ]
        self._server_statuses.append(
            ServerStatus(name=name, status=ServerStatusEnum.LAZY, tool_count=0)
        )
        return (True, cancelled, None)

    async def restart_server(
        self, config: Any, force: bool = False
    ) -> tuple[bool, int, list[str]]:
        self.events.append(f"restart_server:{config.name}:{force}")
        disconnected, cancelled, error = await self.disconnect_server(
            config.name, force=force
        )
        if not disconnected:
            return (False, cancelled, [error or "Restart refused."])
        errors = await self.connect_server(config)
        return (not errors, cancelled, errors)

    def add_pending_request(self, server_name: str = "server") -> Any:
        request_id = len(self.pending_requests) + 1
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        request = types.SimpleNamespace(
            request_id=request_id,
            server_name=server_name,
            tool_id=f"{server_name}::tool",
            started_at=time.time(),
            last_heartbeat=time.time(),
            timeout_ms=30000,
            future=future,
        )
        self.pending_requests.append(request)
        return request

    def get_pending_requests(self, server: str | None = None) -> list[Any]:
        if server is None:
            return list(self.pending_requests)
        return [
            request
            for request in self.pending_requests
            if request.server_name == server
        ]

    def get_request_state(self, request: Any) -> RequestState:
        if request.future.cancelled():
            return RequestState.CANCELLED
        if request.future.done():
            return RequestState.COMPLETED
        return RequestState.PENDING

    def cancel_all_pending_requests(self) -> int:
        self.events.append("cancel_all")
        cancelled = 0
        for request in list(self.pending_requests):
            if not request.future.done():
                request.future.cancel()
                cancelled += 1
        self.pending_requests = []
        return cancelled

    def cancel_pending_requests(self, server: str) -> int:
        self.events.append(f"cancel_pending:{server}")
        cancelled = 0
        remaining = []
        for request in self.pending_requests:
            if request.server_name == server:
                if not request.future.done():
                    request.future.cancel()
                    cancelled += 1
            else:
                remaining.append(request)
        self.pending_requests = remaining
        return cancelled


def create_manifest_for_request_tests() -> Manifest:
    return Manifest(
        version="1.0",
        cli_alternatives={
            "git": CLIAlternative(
                name="git",
                keywords=["git", "version control"],
                check_command=["git", "--version"],
                help_command=["git", "--help"],
                description="Git CLI",
            )
        },
        servers={
            "playwright": ServerConfig(
                name="playwright",
                description="Browser automation",
                keywords=["browser", "automation", "playwright"],
                install={},
                command="npx",
                args=["@playwright/mcp"],
                requires_api_key=False,
            )
        },
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )


def create_mock_tools() -> list[ToolInfo]:
    """Create mock tools for testing."""
    return [
        ToolInfo(
            tool_id="github::create_issue",
            server_name="github",
            tool_name="create_issue",
            description="Create a new issue in a GitHub repository",
            short_description="Create a new issue in a GitHub repository",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Issue title"},
                    "body": {"type": "string", "description": "Issue body"},
                },
                "required": ["title"],
            },
            tags=["github", "git"],
            risk_hint=RiskHint.HIGH,
        ),
        ToolInfo(
            tool_id="github::list_issues",
            server_name="github",
            tool_name="list_issues",
            description="List issues in a repository",
            short_description="List issues in a repository",
            input_schema={"type": "object", "properties": {}},
            tags=["github", "git", "search"],
            risk_hint=RiskHint.LOW,
        ),
        ToolInfo(
            tool_id="jira::search_issues",
            server_name="jira",
            tool_name="search_issues",
            description="Search for Jira issues using JQL",
            short_description="Search for Jira issues using JQL",
            input_schema={"type": "object", "properties": {}},
            tags=["jira", "search"],
            risk_hint=RiskHint.LOW,
        ),
    ]


def patch_refresh_config_sources(
    monkeypatch: pytest.MonkeyPatch,
    configured: list[ResolvedServerConfig],
) -> None:
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers={},
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )
    monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_enabled_auto_start",
        lambda **_: set(),
    )
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_disabled_auto_start",
        lambda **_: set(),
    )


class TestRefreshCompatibility:
    @pytest.mark.asyncio
    async def test_refresh_registers_configured_manifest_and_provisioned_lazy_by_default(
        self, monkeypatch
    ) -> None:
        client_manager = MockClientManager()
        policy_manager = PolicyManager()
        policy_manager.is_server_allowed = lambda name: name != "denied"  # type: ignore[method-assign]
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        configured = [
            ResolvedServerConfig(
                name="configured",
                source="project",
                config=LocalMcpServerConfig(command="configured-cmd"),
            ),
            ResolvedServerConfig(
                name="denied",
                source="project",
                config=LocalMcpServerConfig(command="denied-cmd"),
            ),
        ]
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "legacy-auto": ServerConfig(
                    name="legacy-auto",
                    description="Legacy auto-start",
                    keywords=["legacy"],
                    install={},
                    command="legacy-cmd",
                    args=[],
                    auto_start=True,
                ),
                "provisioned": ServerConfig(
                    name="provisioned",
                    description="Provisioned",
                    keywords=["provisioned"],
                    install={},
                    command="provisioned-cmd",
                    args=[],
                ),
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_disabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            gateway_tools,
            "_load_provisioned_registry",
            lambda: {"provisioned": None},
        )

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert client_manager.disconnected is True
        assert client_manager.refreshed_configs == []
        assert [config.name for config in client_manager.lazy_configs] == [
            "configured",
            "legacy-auto",
            "provisioned",
        ]
        assert client_manager.connected_configs == []
        assert result.servers_seen == 4
        assert result.pending_requests_seen == 0
        assert result.pending_requests_cancelled == 0
        assert result.pending_requests_refused == 0
        assert result.pending_requests_remaining == 0

    @pytest.mark.asyncio
    async def test_refresh_connects_only_configured_auto_start(
        self, monkeypatch
    ) -> None:
        client_manager = MockClientManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        configured = [
            ResolvedServerConfig(
                name="configured",
                source="project",
                config=LocalMcpServerConfig(command="configured-cmd"),
            ),
            ResolvedServerConfig(
                name="lazy",
                source="project",
                config=LocalMcpServerConfig(command="lazy-cmd"),
            ),
        ]
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={},
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: {"configured"},
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_disabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert [config.name for config in client_manager.lazy_configs] == ["lazy"]
        assert [config.name for config in client_manager.connected_configs] == [
            "configured"
        ]

    @pytest.mark.asyncio
    async def test_refresh_legacy_manifest_auto_start_env_connects_manifest(
        self, monkeypatch
    ) -> None:
        client_manager = MockClientManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "legacy-auto": ServerConfig(
                    name="legacy-auto",
                    description="Legacy auto-start",
                    keywords=["legacy"],
                    install={},
                    command="legacy-cmd",
                    args=[],
                    auto_start=True,
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setenv("PMCP_LEGACY_MANIFEST_AUTOSTART", "1")
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_disabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert client_manager.lazy_configs == []
        assert [config.name for config in client_manager.connected_configs] == [
            "legacy-auto"
        ]

    @pytest.mark.asyncio
    async def test_refresh_provisioned_servers_remain_lazy_unless_auto_start(
        self, monkeypatch
    ) -> None:
        client_manager = MockClientManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "provisioned": ServerConfig(
                    name="provisioned",
                    description="Provisioned",
                    keywords=["provisioned"],
                    install={},
                    command="provisioned-cmd",
                    args=[],
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_disabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            gateway_tools,
            "_load_provisioned_registry",
            lambda: {"provisioned": None},
        )

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert [config.name for config in client_manager.lazy_configs] == [
            "provisioned"
        ]
        assert client_manager.connected_configs == []

    @pytest.mark.asyncio
    async def test_refresh_refuses_pending_requests_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        client_manager.add_pending_request("active")
        client_manager.set_server_statuses(
            [
                ServerStatus(
                    name="active",
                    status=ServerStatusEnum.ONLINE,
                    tool_count=1,
                )
            ]
        )
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        gateway_tools.set_startup_observations(
            {
                "stale": StartupObservation(
                    name="stale",
                    startup_policy="skipped",
                    startup_source="auto_start",
                )
            }
        )
        patch_refresh_config_sources(
            monkeypatch,
            [
                ResolvedServerConfig(
                    name="fresh",
                    source="project",
                    config=LocalMcpServerConfig(command="fresh-cmd"),
                )
            ],
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is False
        assert result.pending_requests_seen == 1
        assert result.pending_requests_refused == 1
        assert result.pending_requests_remaining == 1
        assert "force=true" in (result.errors or [""])[0]
        assert client_manager.disconnected is False
        assert client_manager.lazy_configs == []
        assert client_manager.connected_configs == []
        assert "stale" in gateway_tools._startup_observations
        assert "fresh" not in gateway_tools._startup_observations

    @pytest.mark.asyncio
    async def test_refresh_force_cancels_before_disconnect_and_reconnect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        request = client_manager.add_pending_request("active")
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        configured = [
            ResolvedServerConfig(
                name="eager",
                source="project",
                config=LocalMcpServerConfig(command="eager-cmd"),
            )
        ]
        patch_refresh_config_sources(monkeypatch, configured)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: {"eager"},
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test", "force": True})

        assert result.ok is True
        assert request.future.cancelled()
        assert result.pending_requests_seen == 1
        assert result.pending_requests_cancelled == 1
        assert result.pending_requests_remaining == 0
        assert client_manager.events == [
            "cancel_all",
            "disconnect",
            "register_lazy",
            "connect",
        ]

    @pytest.mark.asyncio
    async def test_refresh_schema_force_is_optional(self) -> None:
        refresh_tool = next(
            tool
            for tool in get_gateway_tool_definitions()
            if tool.name == "gateway.refresh"
        )

        schema = refresh_tool.inputSchema
        assert "force" in schema["properties"]
        assert schema["properties"]["force"]["type"] == "boolean"
        assert schema.get("required", []) == []

    @pytest.mark.asyncio
    async def test_list_pending_survives_refused_refresh_and_clears_after_force(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        client_manager.add_pending_request("active")
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        patch_refresh_config_sources(monkeypatch, [])
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        before = await gateway_tools.list_pending({})
        refused = await gateway_tools.refresh({"reason": "test"})
        after_refused = await gateway_tools.list_pending({})
        forced = await gateway_tools.refresh({"reason": "test", "force": True})
        after_forced = await gateway_tools.list_pending({})

        assert before.total_pending == 1
        assert refused.ok is False
        assert after_refused.total_pending == 1
        assert forced.ok is True
        assert forced.pending_requests_cancelled == 1
        assert after_forced.total_pending == 0

    @pytest.mark.asyncio
    async def test_refresh_refuses_pending_lazy_start_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        pending = client_manager.add_pending_request("lazy")
        pending.tool_id = ""
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        patch_refresh_config_sources(monkeypatch, [])
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        refused = await gateway_tools.refresh({"reason": "lazy start race"})
        forced = await gateway_tools.refresh(
            {"reason": "lazy start race", "force": True}
        )

        assert refused.ok is False
        assert refused.pending_requests_refused == 1
        assert client_manager.events == [
            "cancel_all",
            "disconnect",
            "register_lazy",
            "connect",
        ]
        assert forced.ok is True
        assert forced.pending_requests_cancelled == 1

    @pytest.mark.asyncio
    async def test_soak_health_and_pending_survive_refused_refresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        pending = client_manager.add_pending_request("active")
        client_manager.set_server_statuses(
            [
                ServerStatus(
                    name="active",
                    status=ServerStatusEnum.ONLINE,
                    tool_count=1,
                    pending_request_count=1,
                )
            ]
        )
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        gateway_tools.set_startup_observations(
            {
                "active": StartupObservation(
                    name="active",
                    startup_policy="eager",
                    startup_source="project",
                )
            }
        )
        patch_refresh_config_sources(monkeypatch, [])
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        before_health = await gateway_tools.health()
        before_pending = await gateway_tools.list_pending({})
        refused = await gateway_tools.refresh({"reason": "soak"})
        after_health = await gateway_tools.health()
        after_pending = await gateway_tools.list_pending({})

        assert before_health.servers[0].name == "active"
        assert before_health.servers[0].startup_policy == "eager"
        assert before_pending.requests[0].request_id == "active::1"
        assert refused.ok is False
        assert refused.pending_requests_seen == 1
        assert refused.pending_requests_refused == 1
        assert pending.future.cancelled() is False
        assert after_health.revision_id == "test-rev"
        assert after_health.servers[0].status == "online"
        assert after_pending.requests[0].request_id == "active::1"
        assert client_manager.events == []


class TestCatalogSearch:
    """Tests for catalog_search."""

    @pytest.fixture
    def gateway_tools(self) -> GatewayTools:
        client_manager = MockClientManager(create_mock_tools())
        client_manager.set_server_online("github")
        client_manager.set_server_online("jira")

        policy_manager = PolicyManager()

        return GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

    @pytest.mark.asyncio
    async def test_returns_all_tools_with_no_filters(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.catalog_search({})

        assert len(result.results) == 3
        assert result.total_available == 3
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_filters_by_server_name(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.catalog_search({"filters": {"server": "github"}})

        assert len(result.results) == 2
        assert all(r.server == "github" for r in result.results)

    @pytest.mark.asyncio
    async def test_filters_by_query(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.catalog_search({"query": "search"})

        assert len(result.results) > 0
        assert any(
            "search" in r.tool_name.lower() or "search" in r.tags
            for r in result.results
        )

    @pytest.mark.asyncio
    async def test_filters_by_risk_level(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.catalog_search({"filters": {"risk_max": "low"}})

        assert all(r.risk_hint == "low" for r in result.results)

    @pytest.mark.asyncio
    async def test_respects_limit(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.catalog_search({"limit": 1})

        assert len(result.results) == 1
        assert result.truncated is True

    @pytest.mark.asyncio
    async def test_returns_capability_cards_without_schemas(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.catalog_search({})

        for card in result.results:
            assert hasattr(card, "tool_id")
            assert hasattr(card, "short_description")
            # Should not have full schema
            assert not hasattr(card, "input_schema")


class TestServerLifecycleTools:
    """Tests for gateway server lifecycle tools."""

    @pytest.fixture
    def gateway_tools(self, monkeypatch: pytest.MonkeyPatch) -> GatewayTools:
        client_manager = MockClientManager()
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )
        configured = [
            ResolvedServerConfig(
                name="configured",
                source="project",
                config=LocalMcpServerConfig(command="configured-cmd"),
            ),
            ResolvedServerConfig(
                name="denied",
                source="project",
                config=LocalMcpServerConfig(command="denied-cmd"),
            ),
            ResolvedServerConfig(
                name="fails-connect",
                source="project",
                config=LocalMcpServerConfig(command="fail-cmd"),
            ),
        ]
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "manifest": ServerConfig(
                    name="manifest",
                    description="Manifest server",
                    keywords=["manifest"],
                    install={},
                    command="manifest-cmd",
                    args=[],
                ),
                "needs-key": ServerConfig(
                    name="needs-key",
                    description="Auth server",
                    keywords=["auth"],
                    install={},
                    command="auth-cmd",
                    args=[],
                    requires_api_key=True,
                    env_var="PMCP_TEST_KEY",
                ),
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_dotenv", lambda *a, **kw: False)
        policy_manager.is_server_allowed = lambda name: name != "denied"  # type: ignore[method-assign]
        return gateway_tools

    @pytest.mark.asyncio
    async def test_lifecycle_tool_schemas_are_additive(self) -> None:
        tools = {tool.name: tool for tool in get_gateway_tool_definitions()}

        assert "gateway.connect_server" in tools
        assert "gateway.disconnect_server" in tools
        assert "gateway.restart_server" in tools
        assert tools["gateway.connect_server"].inputSchema["required"] == [
            "server_name"
        ]
        assert "force" not in tools["gateway.connect_server"].inputSchema["properties"]
        assert (
            tools["gateway.disconnect_server"].inputSchema["properties"]["force"][
                "type"
            ]
            == "boolean"
        )
        assert (
            tools["gateway.restart_server"].inputSchema["properties"]["force"]["type"]
            == "boolean"
        )
        assert "force" not in tools["gateway.refresh"].inputSchema.get("required", [])

    @pytest.mark.asyncio
    async def test_connect_server_already_online(
        self, gateway_tools: GatewayTools
    ) -> None:
        manager = cast(Any, gateway_tools._client_manager)
        manager.set_server_online("configured")

        result = await gateway_tools.connect_server({"server_name": "configured"})

        assert result.ok is True
        assert result.prior_status == "online"
        assert result.new_status == "online"
        assert result.cancelled_request_count == 0
        assert manager.connected_configs == []

    @pytest.mark.asyncio
    async def test_connect_server_starts_configured_and_manifest(
        self, gateway_tools: GatewayTools
    ) -> None:
        configured = await gateway_tools.connect_server({"server_name": "configured"})
        manifest = await gateway_tools.connect_server({"server_name": "manifest"})

        assert configured.ok is True
        assert manifest.ok is True
        connected_names = [
            config.name
            for config in cast(Any, gateway_tools._client_manager).connected_configs
        ]
        assert connected_names == ["configured", "manifest"]

    @pytest.mark.asyncio
    async def test_connect_server_reports_unknown_policy_and_missing_auth(
        self, gateway_tools: GatewayTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PMCP_TEST_KEY", raising=False)

        unknown = await gateway_tools.connect_server({"server_name": "unknown"})
        denied = await gateway_tools.connect_server({"server_name": "denied"})
        missing_auth = await gateway_tools.connect_server({"server_name": "needs-key"})

        assert unknown.ok is False
        assert unknown.errors == ["Unknown server: unknown"]
        assert denied.ok is False
        assert "blocked by policy" in denied.message
        assert missing_auth.ok is False
        assert "PMCP_TEST_KEY" in (missing_auth.errors or [""])[0]

    @pytest.mark.asyncio
    async def test_connect_server_uses_registered_discovered_server(
        self, gateway_tools: GatewayTools
    ) -> None:
        gateway_tools._discovered_server_configs["discovered"] = ServerConfig(
            name="discovered",
            description="Discovered",
            keywords=["discovered"],
            install={},
            command="discovered-cmd",
            args=[],
        )

        result = await gateway_tools.connect_server({"server_name": "discovered"})

        assert result.ok is True
        assert cast(Any, gateway_tools._client_manager).connected_configs[-1].name == (
            "discovered"
        )

    @pytest.mark.asyncio
    async def test_disconnect_server_success_and_pending_policy(
        self, gateway_tools: GatewayTools
    ) -> None:
        manager = cast(Any, gateway_tools._client_manager)
        manager.set_server_online("configured")
        stopped = await gateway_tools.disconnect_server({"server_name": "configured"})
        manager.set_server_online("configured")
        request = manager.add_pending_request("configured")
        refused = await gateway_tools.disconnect_server({"server_name": "configured"})
        assert request.future.cancelled() is False
        forced = await gateway_tools.disconnect_server(
            {"server_name": "configured", "force": True}
        )

        assert stopped.ok is True
        assert stopped.new_status == "lazy"
        assert refused.ok is False
        assert "force=true" in refused.message
        assert forced.ok is True
        assert forced.cancelled_request_count == 1
        assert request.future.cancelled()

    @pytest.mark.asyncio
    async def test_restart_server_success_failure_and_pending_policy(
        self, gateway_tools: GatewayTools
    ) -> None:
        manager = cast(Any, gateway_tools._client_manager)
        manager.set_server_online("configured")
        restarted = await gateway_tools.restart_server({"server_name": "configured"})
        manager.set_server_online("configured")
        request = manager.add_pending_request("configured")
        refused = await gateway_tools.restart_server({"server_name": "configured"})
        assert request.future.cancelled() is False
        forced = await gateway_tools.restart_server(
            {"server_name": "configured", "force": True}
        )
        failed = await gateway_tools.restart_server({"server_name": "fails-connect"})

        assert restarted.ok is True
        assert restarted.new_status == "online"
        assert refused.ok is False
        assert forced.ok is True
        assert forced.cancelled_request_count == 1
        assert failed.ok is False
        assert failed.errors == ["Failed to connect to fails-connect: boom"]

    @pytest.mark.asyncio
    async def test_soak_force_lifecycle_cancels_only_target_server(
        self, gateway_tools: GatewayTools
    ) -> None:
        manager = cast(Any, gateway_tools._client_manager)
        manager.set_server_online("configured")
        manager.set_server_online("other")
        target = manager.add_pending_request("configured")
        other = manager.add_pending_request("other")

        refused = await gateway_tools.disconnect_server({"server_name": "configured"})

        assert refused.ok is False
        assert target.future.cancelled() is False
        assert other.future.cancelled() is False
        forced = await gateway_tools.disconnect_server(
            {"server_name": "configured", "force": True}
        )

        assert forced.ok is True
        assert forced.cancelled_request_count == 1
        assert target.future.cancelled()
        assert other.future.cancelled() is False
        assert [p.request_id for p in manager.get_pending_requests("other")] == [2]

    @pytest.mark.asyncio
    async def test_health_keeps_runtime_stopped_startup_observation(
        self, gateway_tools: GatewayTools
    ) -> None:
        manager = cast(Any, gateway_tools._client_manager)
        manager.set_server_online("configured")
        gateway_tools.set_startup_observations(
            {
                "configured": StartupObservation(
                    name="configured",
                    startup_policy="lazy",
                    startup_source="project",
                )
            }
        )

        await gateway_tools.disconnect_server({"server_name": "configured"})
        result = await gateway_tools.health()

        by_name = {server.name: server for server in result.servers}
        assert by_name["configured"].status == "lazy"
        assert by_name["configured"].startup_policy == "lazy"


class TestDescribe:
    """Tests for describe."""

    @pytest.fixture
    def gateway_tools(self) -> GatewayTools:
        client_manager = MockClientManager(create_mock_tools())
        client_manager.set_server_online("github")
        policy_manager = PolicyManager()

        return GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

    @pytest.mark.asyncio
    async def test_returns_schema_card(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.describe({"tool_id": "github::create_issue"})

        assert result.server == "github"
        assert result.tool_name == "create_issue"
        assert "GitHub" in result.description
        assert len(result.args) == 2
        assert any(a.name == "title" and a.required for a in result.args)

    @pytest.mark.asyncio
    async def test_raises_for_unknown_tool(self, gateway_tools: GatewayTools) -> None:
        with pytest.raises(GatewayException) as exc_info:
            await gateway_tools.describe({"tool_id": "unknown::tool"})
        assert exc_info.value.code.value == "E301"  # TOOL_NOT_FOUND


class TestInvoke:
    """Tests for invoke."""

    @pytest.fixture
    def gateway_tools(self) -> GatewayTools:
        client_manager = MockClientManager(create_mock_tools())
        client_manager.set_server_online("github")
        policy_manager = PolicyManager()

        return GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

    @pytest.mark.asyncio
    async def test_calls_tool_and_returns_result(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.invoke(
            {"tool_id": "github::list_issues", "arguments": {}}
        )

        assert result.ok is True
        assert result.tool_id == "github::list_issues"

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_tool(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.invoke(
            {"tool_id": "unknown::tool", "arguments": {}}
        )

        assert result.ok is False
        assert "Tool not found" in (result.errors or [])[0]

    @pytest.mark.asyncio
    async def test_soak_concurrent_lazy_invokes_share_one_connect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        client_manager.add_lazy_server("lazy")
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        monkeypatch.setattr(
            gateway_tools, "_get_update_warning", AsyncMock(return_value=None)
        )

        started = asyncio.Event()
        release = asyncio.Event()
        connect_task: asyncio.Task[None] | None = None
        connect_calls = 0
        connect_lock = asyncio.Lock()

        async def do_connect() -> None:
            nonlocal connect_calls
            connect_calls += 1
            started.set()
            await release.wait()
            client_manager._lazy_servers.discard("lazy")
            client_manager._online_servers.add("lazy")
            client_manager._tools["lazy::echo"] = ToolInfo(
                tool_id="lazy::echo",
                server_name="lazy",
                tool_name="echo",
                description="Echo",
                short_description="Echo",
                input_schema={},
                tags=[],
                risk_hint=RiskHint.LOW,
            )

        async def ensure_connected(server_name: str) -> bool:
            nonlocal connect_task
            async with connect_lock:
                if client_manager.is_server_online(server_name):
                    return True
                if connect_task is None:
                    connect_task = asyncio.create_task(do_connect())
                task = connect_task
            await task
            return True

        async def call_tool(
            tool_id: str, args: dict[str, Any], timeout_ms: int
        ) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": tool_id}]}

        client_manager.ensure_connected = ensure_connected  # type: ignore[method-assign]
        client_manager.call_tool = call_tool  # type: ignore[method-assign]

        first = asyncio.create_task(
            gateway_tools.invoke({"tool_id": "lazy::echo", "arguments": {}})
        )
        second = asyncio.create_task(
            gateway_tools.invoke({"tool_id": "lazy::echo", "arguments": {}})
        )
        await started.wait()
        assert connect_calls == 1
        release.set()

        results = await asyncio.gather(first, second)

        assert [result.ok for result in results] == [True, True]
        assert connect_calls == 1
        assert client_manager.is_server_online("lazy") is True


class TestHealth:
    """Tests for health."""

    @pytest.fixture
    def gateway_tools(self) -> GatewayTools:
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()

        return GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

    @pytest.mark.asyncio
    async def test_returns_health_status(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.health()

        assert result.revision_id == "test-rev"
        assert isinstance(result.servers, list)

    @pytest.mark.asyncio
    async def test_includes_error_details_for_error_servers(
        self, gateway_tools: GatewayTools
    ) -> None:
        cast(Any, gateway_tools._client_manager).set_server_statuses(
            [
                ServerStatus(
                    name="playwright",
                    status=ServerStatusEnum.ERROR,
                    tool_count=0,
                    last_error="Connection refused",
                )
            ]
        )

        result = await gateway_tools.health()

        assert result.servers[0].status == "error"
        assert result.servers[0].error == "Connection refused"

    @pytest.mark.asyncio
    async def test_merges_startup_observations_into_health(
        self, gateway_tools: GatewayTools
    ) -> None:
        cast(Any, gateway_tools._client_manager).set_server_statuses(
            [
                ServerStatus(
                    name="eager",
                    status=ServerStatusEnum.ONLINE,
                    tool_count=2,
                ),
                ServerStatus(
                    name="lazy",
                    status=ServerStatusEnum.LAZY,
                    tool_count=0,
                ),
            ]
        )
        gateway_tools.set_startup_observations(
            {
                "eager": StartupObservation(
                    name="eager",
                    startup_policy="eager",
                    startup_source="project",
                ),
                "lazy": StartupObservation(
                    name="lazy",
                    startup_policy="lazy",
                    startup_source="manifest",
                ),
            }
        )

        result = await gateway_tools.health()

        by_name = {server.name: server for server in result.servers}
        assert by_name["eager"].startup_policy == "eager"
        assert by_name["eager"].startup_source == "project"
        assert by_name["lazy"].startup_policy == "lazy"
        assert by_name["lazy"].startup_source == "manifest"

    @pytest.mark.asyncio
    async def test_health_includes_skipped_startup_entries(
        self, gateway_tools: GatewayTools
    ) -> None:
        gateway_tools.set_startup_observations(
            {
                "unknown": StartupObservation(
                    name="unknown",
                    startup_policy="skipped",
                    startup_source="auto_start",
                    startup_skip_reason="unknown_auto_start",
                ),
                "denied": StartupObservation(
                    name="denied",
                    startup_policy="skipped",
                    startup_source="configured",
                    startup_skip_reason="policy_denied",
                ),
                "needs-key": StartupObservation(
                    name="needs-key",
                    startup_policy="skipped",
                    startup_source="manifest",
                    startup_skip_reason="missing_auth",
                    startup_env_var="PMCP_TEST_KEY",
                ),
            }
        )

        result = await gateway_tools.health()

        by_name = {server.name: server for server in result.servers}
        assert by_name["unknown"].status == "offline"
        assert by_name["unknown"].startup_skip_reason == "unknown_auto_start"
        assert by_name["denied"].startup_skip_reason == "policy_denied"
        assert by_name["needs-key"].startup_skip_reason == "missing_auth"
        assert by_name["needs-key"].startup_env_var == "PMCP_TEST_KEY"

    @pytest.mark.asyncio
    async def test_health_merges_provisioned_startup_observation(
        self, gateway_tools: GatewayTools
    ) -> None:
        gateway_tools.set_startup_observations(
            {
                "provisioned": StartupObservation(
                    name="provisioned",
                    startup_policy="lazy",
                    startup_source="manifest",
                )
            }
        )
        gateway_tools._load_provisioned_registry = lambda: {"provisioned": None}  # type: ignore[method-assign]

        result = await gateway_tools.health()

        assert len(result.servers) == 1
        assert result.servers[0].name == "provisioned"
        assert result.servers[0].status == "offline"
        assert result.servers[0].startup_policy == "lazy"

    @pytest.mark.asyncio
    async def test_refresh_replaces_startup_observations_in_health(
        self, gateway_tools: GatewayTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured = [
            ResolvedServerConfig(
                name="fresh",
                source="project",
                config=LocalMcpServerConfig(command="fresh-cmd"),
            )
        ]
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={},
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        gateway_tools.set_startup_observations(
            {
                "stale": StartupObservation(
                    name="stale",
                    startup_policy="skipped",
                    startup_source="auto_start",
                    startup_skip_reason="unknown_auto_start",
                )
            }
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_disabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        await gateway_tools.refresh({"reason": "test"})
        cast(Any, gateway_tools._client_manager).set_server_statuses(
            [
                ServerStatus(
                    name="fresh",
                    status=ServerStatusEnum.LAZY,
                    tool_count=0,
                )
            ]
        )

        result = await gateway_tools.health()

        by_name = {server.name: server for server in result.servers}
        assert "stale" not in by_name
        assert by_name["fresh"].startup_policy == "lazy"


class TestCapabilityAndProvision:
    @pytest.mark.asyncio
    async def test_request_capability_name_match_returns_single_candidate(
        self, monkeypatch
    ):
        """Tier-1: explicit name in query → single candidate, status='candidates'."""
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.request_capability(
            {"query": "use playwright for browser automation", "available_clis": []}
        )

        assert result.status == "candidates"
        assert result.candidates is not None
        assert len(result.candidates) == 1
        assert result.candidates[0].name == "playwright"
        assert result.candidates[0].candidate_type == "server"

    @pytest.mark.asyncio
    async def test_request_capability_category_match_returns_all_servers(
        self, monkeypatch
    ):
        """Tier-2: category query → pick_from_category with all category servers."""
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.request_capability(
            {"query": "browser automation", "available_clis": []}
        )

        assert result.status == "pick_from_category"
        assert result.category_name == "browser automation"
        assert result.candidates is not None
        assert len(result.candidates) >= 1

    @pytest.mark.asyncio
    async def test_provision_starts_configured_lazy_server(self, monkeypatch):
        tool = ToolInfo(
            tool_id="custom-browser::snapshot",
            server_name="custom-browser",
            tool_name="snapshot",
            description="Take browser snapshot",
            short_description="Take browser snapshot",
            input_schema={"type": "object", "properties": {}},
            tags=["browser"],
            risk_hint=RiskHint.LOW,
        )
        client_manager = MockClientManager([tool])
        client_manager.add_lazy_server("custom-browser")
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        configured = [
            ResolvedServerConfig(
                name="custom-browser",
                source="project",
                config=LocalMcpServerConfig(command="npx", args=["custom-browser-mcp"]),
            )
        ]

        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)

        result = await gateway_tools.provision({"server_name": "custom-browser"})

        assert result.ok is True
        assert result.status == "complete"
        assert "started from .mcp.json configuration" in result.message

    @pytest.mark.asyncio
    async def test_concurrent_provision_for_configured_lazy_server_reuses_running(
        self, monkeypatch
    ):
        tool = ToolInfo(
            tool_id="custom-browser::snapshot",
            server_name="custom-browser",
            tool_name="snapshot",
            description="Take browser snapshot",
            short_description="Take browser snapshot",
            input_schema={"type": "object", "properties": {}},
            tags=["browser"],
            risk_hint=RiskHint.LOW,
        )
        client_manager = MockClientManager([tool])
        client_manager.add_lazy_server("custom-browser")
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        configured = [
            ResolvedServerConfig(
                name="custom-browser",
                source="project",
                config=LocalMcpServerConfig(command="npx", args=["custom-browser-mcp"]),
            )
        ]

        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)

        first, second = await asyncio.gather(
            gateway_tools.provision({"server_name": "custom-browser"}),
            gateway_tools.provision({"server_name": "custom-browser"}),
        )

        assert first.ok is True
        assert second.ok is True
        assert {first.status, second.status} <= {"complete", "already_running"}
        assert client_manager.is_server_online("custom-browser") is True

    @pytest.mark.asyncio
    async def test_request_then_provision_for_configured_server(self, monkeypatch):
        """Tier-1 name match for a .mcp.json-configured server → provision succeeds."""
        tool = ToolInfo(
            tool_id="custom-browser::snapshot",
            server_name="custom-browser",
            tool_name="snapshot",
            description="Take browser snapshot",
            short_description="Take browser snapshot",
            input_schema={"type": "object", "properties": {}},
            tags=["browser"],
            risk_hint=RiskHint.LOW,
        )
        client_manager = MockClientManager([tool])
        client_manager.add_lazy_server("custom-browser")
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        configured = [
            ResolvedServerConfig(
                name="custom-browser",
                source="project",
                config=LocalMcpServerConfig(command="npx", args=["custom-browser-mcp"]),
            )
        ]

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)

        # Tier-1 sliding-window name match: "custom-browser" appears verbatim in query
        capability = await gateway_tools.request_capability(
            {"query": "use custom-browser for automation", "available_clis": []}
        )
        assert capability.status == "candidates"
        assert capability.candidates is not None
        assert capability.candidates[0].name == "custom-browser"

        provision = await gateway_tools.provision({"server_name": "custom-browser"})
        assert provision.ok is True
        assert provision.status == "complete"
        assert client_manager.is_server_online("custom-browser") is True

    @pytest.mark.asyncio
    async def test_request_capability_returns_search_guidance_when_not_found(
        self, monkeypatch
    ):
        """Tier-3: unknown query → not_available with search_registry guidance."""
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.request_capability({"query": "openbrowser mcp"})

        assert result.status == "not_available"
        assert result.search_guidance is not None
        assert "gateway.search_registry" in result.search_guidance

    @pytest.mark.asyncio
    async def test_provision_reports_auth_metadata_for_missing_key(self, monkeypatch):
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "browser-use": ServerConfig(
                    name="browser-use",
                    description="Browser Use",
                    keywords=["browser-use"],
                    install={},
                    command="uvx",
                    args=["--from", "browser-use[cli]", "browser-use", "--mcp"],
                    requires_api_key=True,
                    env_var="OPENAI_API_KEY",
                    env_instructions="Set OPENAI_API_KEY or ANTHROPIC_API_KEY",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Prevent _check_api_key_available from loading env vars out of pmcp files on disk
        monkeypatch.setattr("pmcp.tools.handlers.load_dotenv", lambda *a, **kw: False)

        result = await gateway_tools.provision({"server_name": "browser-use"})

        assert result.ok is False
        assert result.needs_api_key is True
        assert result.auth_required is True
        assert result.auth_mode == "api_key"
        assert result.alternative_env_vars == ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]

    @pytest.mark.asyncio
    async def test_auth_connect_stores_credential(self, monkeypatch):
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "browser-use": ServerConfig(
                    name="browser-use",
                    description="Browser Use",
                    keywords=["browser-use"],
                    install={},
                    command="uvx",
                    args=["browser-use"],
                    requires_api_key=True,
                    env_var="OPENAI_API_KEY",
                    env_instructions="Set OPENAI_API_KEY",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            gateway_tools,
            "_write_secret",
            lambda scope, key, value: Path("/tmp/pmcp-test.env"),
        )

        result = await gateway_tools.auth_connect(
            {
                "server_name": "browser-use",
                "credential": "test-token",
                "scope": "user",
            }
        )

        assert result.ok is True
        assert result.env_var == "OPENAI_API_KEY"
        assert "gateway.provision" in (result.next_step or "")

    @pytest.mark.asyncio
    async def test_provision_uses_discovered_server_config(self, monkeypatch):
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={},
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        gateway_tools._discovered_server_configs["@acme/openbrowser-mcp"] = (
            ServerConfig(
                name="@acme/openbrowser-mcp",
                description="Discovered package",
                keywords=["mcp"],
                install={
                    "linux": ["npx", "-y", "@acme/openbrowser-mcp"],
                    "mac": ["npx", "-y", "@acme/openbrowser-mcp"],
                    "wsl": ["npx", "-y", "@acme/openbrowser-mcp"],
                    "windows": ["npx", "-y", "@acme/openbrowser-mcp"],
                },
                command="npx",
                args=["-y", "@acme/openbrowser-mcp"],
                requires_api_key=False,
            )
        )

        class FakeJobManager:
            async def start_install(self, server_config, platform):
                return "job-123"

        monkeypatch.setattr(
            "pmcp.tools.handlers.get_job_manager", lambda: FakeJobManager()
        )
        monkeypatch.setattr("pmcp.tools.handlers.detect_platform", lambda: "linux")

        result = await gateway_tools.provision({"server_name": "@acme/openbrowser-mcp"})

        assert result.ok is True
        assert result.status == "started"
        assert result.job_id == "job-123"

    @pytest.mark.asyncio
    async def test_update_server_success(self, monkeypatch):
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "playwright": ServerConfig(
                    name="playwright",
                    description="Browser automation",
                    keywords=["browser"],
                    install={},
                    command="npx",
                    args=["@playwright/mcp"],
                    requires_api_key=False,
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            gateway_tools,
            "_run_update_probe_command",
            lambda command: __import__("asyncio").sleep(0, result=(True, "ok")),
        )
        monkeypatch.setattr(
            gateway_tools,
            "refresh",
            lambda input_data: __import__("asyncio").sleep(
                0,
                result=types.SimpleNamespace(ok=True),
            ),
        )

        async def fake_get_package_version(command, args, timeout=5.0):
            return ("1.2.3", "npm")

        monkeypatch.setattr(
            "pmcp.tools.handlers.get_package_version", fake_get_package_version
        )

        result = await gateway_tools.update_server({"server_name": "playwright"})

        assert result.ok is True
        assert result.server == "playwright"
        assert result.package_type == "npm"
        assert result.latest_version == "1.2.3"

    @pytest.mark.asyncio
    async def test_invoke_includes_update_warning(self, monkeypatch):
        tools = [
            ToolInfo(
                tool_id="playwright::snapshot",
                server_name="playwright",
                tool_name="snapshot",
                description="Take snapshot",
                short_description="Take snapshot",
                input_schema={"type": "object", "properties": {}},
                tags=["browser"],
                risk_hint=RiskHint.LOW,
            )
        ]
        client_manager = MockClientManager(tools)
        client_manager.set_server_online("playwright")
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        async def fake_update_warning(server_name: str):
            return "Update available for 'playwright': 0.1.0 -> 0.2.0"

        monkeypatch.setattr(gateway_tools, "_get_update_warning", fake_update_warning)

        result = await gateway_tools.invoke(
            {"tool_id": "playwright::snapshot", "arguments": {}}
        )

        assert result.ok is True
        assert result.update_warning is not None

    @pytest.mark.asyncio
    async def test_submit_feedback_preview_includes_telemetry_and_scrubs(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        result = await gateway_tools.submit_feedback(
            {
                "title": "Context7 failed to resolve docs",
                "description": "Error with token sk-abcdef12345678901234567890 and details.",
                "issue_type": "bug",
                "subordinate_server": "context7",
                "failed_tool_call": "context7::resolve-library-id",
                "confirm_submission": False,
            }
        )

        assert result.ok is True
        assert result.submitted is False
        assert "subordinate_server: context7" in result.issue_body
        assert "failed_tool_call: context7::resolve-library-id" in result.issue_body
        assert "pmcp_version" in result.issue_body
        assert "REDACTED_API_KEY" in result.issue_body
        assert "consent" in result.message.lower()

    @pytest.mark.asyncio
    async def test_submit_feedback_disabled(self):
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
            guidance_config=GuidanceConfig(enable_telemetry=False),
        )

        result = await gateway_tools.submit_feedback(
            {
                "title": "Failure while invoking tool",
                "description": "test",
            }
        )

        assert result.ok is False
        assert result.submitted is False
        assert "disabled" in result.message.lower()


class TestSearchRegistryAndRegister:
    """Tests for gateway.search_registry and gateway.register_discovered_server."""

    @pytest.fixture
    def gateway_tools(self) -> GatewayTools:
        client_manager = MockClientManager()
        policy_manager = PolicyManager()
        return GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

    @pytest.mark.asyncio
    async def test_search_registry_returns_results_from_mcp_registry(
        self, gateway_tools: GatewayTools, monkeypatch
    ) -> None:
        fake_entries = [
            {
                "server": {
                    "name": "GitHub MCP",
                    "description": "GitHub integration",
                    "packages": [
                        {
                            "identifier": "@modelcontextprotocol/server-github",
                            "transport": {"type": "stdio"},
                            "environmentVariables": [{"name": "GITHUB_TOKEN"}],
                        }
                    ],
                }
            },
            # Duplicate package — should be deduplicated
            {
                "server": {
                    "name": "GitHub MCP (duplicate)",
                    "description": "GitHub integration duplicate",
                    "packages": [
                        {
                            "identifier": "@modelcontextprotocol/server-github",
                            "transport": {"type": "stdio"},
                            "environmentVariables": [{"name": "GITHUB_TOKEN"}],
                        }
                    ],
                }
            },
        ]
        monkeypatch.setattr(
            gateway_tools, "_query_mcp_registry", lambda q, limit=8: fake_entries
        )

        result = await gateway_tools.search_registry({"query": "github"})

        assert len(result.results) == 1  # duplicate filtered out
        assert result.results[0].package == "@modelcontextprotocol/server-github"
        assert result.results[0].env_vars == ["GITHUB_TOKEN"]
        assert "gateway.register_discovered_server" in result.next_step

    @pytest.mark.asyncio
    async def test_search_registry_handles_empty_results(
        self, gateway_tools: GatewayTools, monkeypatch
    ) -> None:
        monkeypatch.setattr(gateway_tools, "_query_mcp_registry", lambda q, limit=8: [])

        result = await gateway_tools.search_registry({"query": "nonexistent-xyz-tool"})

        assert result.results == []
        assert result.query == "nonexistent-xyz-tool"

    @pytest.mark.asyncio
    async def test_register_discovered_server_stores_config(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.register_discovered_server(
            {
                "package": "@modelcontextprotocol/server-github",
                "server_name": "github",
                "env_vars": ["GITHUB_TOKEN"],
                "description": "GitHub MCP server",
            }
        )

        assert result.ok is True
        assert result.registered is True
        assert result.server_name == "github"
        assert "github" in gateway_tools._discovered_server_configs
        config = gateway_tools._discovered_server_configs["github"]
        assert config.command == "npx"
        assert "@modelcontextprotocol/server-github" in config.args
        assert config.requires_api_key is True
        assert config.env_var == "GITHUB_TOKEN"
        assert "gateway.provision" in (result.next_step or "")

    @pytest.mark.asyncio
    async def test_register_then_provision_flow(
        self, gateway_tools: GatewayTools, monkeypatch
    ) -> None:
        """Full agentic discovery flow: register → provision."""
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest",
            lambda: Manifest(
                version="1.0",
                cli_alternatives={},
                servers={},
                discovery_queue_path=".mcp-gateway/discovery_queue.json",
            ),
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        await gateway_tools.register_discovered_server(
            {
                "package": "@modelcontextprotocol/server-github",
                "server_name": "github-ext",
                "env_vars": ["GITHUB_TOKEN"],
            }
        )

        result = await gateway_tools.provision({"server_name": "github-ext"})

        # Should fail because GITHUB_TOKEN is not set
        assert result.ok is False
        assert result.needs_api_key is True
        assert result.env_var == "GITHUB_TOKEN"

    @pytest.mark.asyncio
    async def test_remote_manifest_server_provisions_without_installer(
        self, gateway_tools: GatewayTools, monkeypatch
    ) -> None:
        """Remote manifest servers should connect directly instead of starting jobs."""
        client_manager = cast(MockClientManager, gateway_tools._client_manager)
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "excalidraw": ServerConfig(
                    name="excalidraw",
                    description="Excalidraw whiteboard",
                    keywords=["excalidraw", "whiteboard"],
                    install={},
                    command="",
                    args=[],
                    requires_api_key=False,
                    transport="streamable-http",
                    url="https://mcp.excalidraw.com",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        fake_jm = types.SimpleNamespace(start_install=pytest.fail)

        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.get_job_manager", lambda: fake_jm)
        monkeypatch.setattr(gateway_tools, "_save_provisioned_registry", lambda: None)

        result = await gateway_tools.provision({"server_name": "excalidraw"})

        assert result.ok is True
        assert result.status == "complete"
        assert len(client_manager.connected_configs) == 1
        connected = client_manager.connected_configs[0]
        assert connected.name == "excalidraw"
        assert connected.config.type == "streamable-http"
        assert connected.config.url == "https://mcp.excalidraw.com"


class TestStaleIndexer:
    """Tests for background stale-version indexer and catalog_search stale_updates."""

    def _make_gateway_tools(self, descriptions_cache=None):
        client_manager = MockClientManager()
        policy_manager = PolicyManager()
        gt = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
            descriptions_cache=descriptions_cache,
        )
        return gt

    def _make_manifest_with_playwright(self, monkeypatch):
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "playwright": ServerConfig(
                    name="playwright",
                    description="Browser automation",
                    keywords=["browser"],
                    install={},
                    command="npx",
                    args=["-y", "@playwright/mcp"],
                    requires_api_key=False,
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        return manifest

    @pytest.mark.asyncio
    async def test_run_stale_index_populates_cache(self, monkeypatch):
        """_run_stale_index populates _stale_check_cache without a warm cache."""
        from pmcp.types import DescriptionsCache, GeneratedServerDescriptions

        cache = DescriptionsCache(
            generated_at="2024-01-01T00:00:00",
            gateway_version="1.0.0",
            servers={
                "playwright": GeneratedServerDescriptions(
                    package="@playwright/mcp",
                    version="0.1.0",
                    generated_at="2024-01-01T00:00:00",
                    capability_summary="Browser automation",
                    tools=[],
                )
            },
        )
        gt = self._make_gateway_tools(descriptions_cache=cache)
        self._make_manifest_with_playwright(monkeypatch)

        async def fake_get_package_version(command, args, timeout=5.0):
            return ("0.2.0", "npm")

        monkeypatch.setattr(
            "pmcp.tools.handlers.get_package_version", fake_get_package_version
        )

        await gt._run_stale_index()

        assert "playwright" in gt._stale_check_cache
        _, current, latest = gt._stale_check_cache["playwright"]
        assert current == "0.1.0"
        assert latest == "0.2.0"

    @pytest.mark.asyncio
    async def test_run_stale_index_skips_fresh_cache(self, monkeypatch):
        """_run_stale_index skips servers whose cache entry is still fresh."""
        import time
        from pmcp.types import DescriptionsCache, GeneratedServerDescriptions

        cache = DescriptionsCache(
            generated_at="2024-01-01T00:00:00",
            gateway_version="1.0.0",
            servers={
                "playwright": GeneratedServerDescriptions(
                    package="@playwright/mcp",
                    version="0.1.0",
                    generated_at="2024-01-01T00:00:00",
                    capability_summary="Browser automation",
                    tools=[],
                )
            },
        )
        gt = self._make_gateway_tools(descriptions_cache=cache)
        self._make_manifest_with_playwright(monkeypatch)

        # Pre-populate cache as fresh
        gt._stale_check_cache["playwright"] = (time.time(), "0.1.0", "0.1.0")

        network_called = []

        async def fake_get_package_version(command, args, timeout=5.0):
            network_called.append(True)
            return ("0.2.0", "npm")

        monkeypatch.setattr(
            "pmcp.tools.handlers.get_package_version", fake_get_package_version
        )

        await gt._run_stale_index()

        assert not network_called, "Network should not be called when cache is fresh"

    @pytest.mark.asyncio
    async def test_run_stale_index_no_descriptions_cache(self):
        """_run_stale_index is a no-op when no descriptions cache is present."""
        gt = self._make_gateway_tools(descriptions_cache=None)
        # Should not raise
        await gt._run_stale_index()

    @pytest.mark.asyncio
    async def test_catalog_search_includes_stale_updates(self, monkeypatch):
        """catalog_search returns stale_updates when indexer has detected stale servers."""
        import time

        tools = [
            ToolInfo(
                tool_id="playwright::snapshot",
                server_name="playwright",
                tool_name="snapshot",
                description="Take snapshot",
                short_description="Take snapshot",
                input_schema={"type": "object", "properties": {}},
                tags=["browser"],
                risk_hint=RiskHint.LOW,
            )
        ]
        client_manager = MockClientManager(tools)
        client_manager.set_server_online("playwright")
        gt = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        # Pre-populate stale check cache with a stale entry
        gt._stale_check_cache["playwright"] = (time.time(), "0.1.0", "0.2.0")

        result = await gt.catalog_search({})

        assert result.stale_updates is not None
        assert len(result.stale_updates) == 1
        assert "playwright" in result.stale_updates[0]
        assert "0.1.0" in result.stale_updates[0]
        assert "0.2.0" in result.stale_updates[0]

    @pytest.mark.asyncio
    async def test_catalog_search_no_stale_updates_when_up_to_date(self):
        """catalog_search returns None for stale_updates when all servers are up to date."""
        import time

        gt = self._make_gateway_tools()
        gt._stale_check_cache["playwright"] = (time.time(), "0.2.0", "0.2.0")

        result = await gt.catalog_search({})

        assert result.stale_updates is None

    @pytest.mark.asyncio
    async def test_start_stop_stale_indexer(self):
        """start_stale_indexer creates a task; stop_stale_indexer cancels it."""
        gt = self._make_gateway_tools()

        gt.start_stale_indexer()
        assert gt._stale_index_task is not None
        assert not gt._stale_index_task.done()
        gt.stop_stale_indexer()
        assert gt._stale_index_task is None


class TestInvokeErrorPaths:
    """Tests for gateway.invoke error handling paths."""

    def _make_tool(self, required_fields: list[str] | None = None) -> ToolInfo:
        return ToolInfo(
            tool_id="svc::do_thing",
            server_name="svc",
            tool_name="do_thing",
            description="A test tool",
            short_description="A test tool",
            input_schema={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": required_fields or [],
            },
            tags=[],
            risk_hint=RiskHint.LOW,
        )

    def _make_gateway_tools(
        self, tool: ToolInfo | None = None, call_tool_side_effect: Any = None
    ) -> GatewayTools:
        t = tool or self._make_tool()
        cm = MockClientManager([t])
        cm.set_server_online("svc")
        if call_tool_side_effect is not None:

            async def _raise(*args: Any, **kwargs: Any) -> Any:
                raise call_tool_side_effect

            cm.call_tool = _raise  # type: ignore[method-assign]
        return GatewayTools(
            client_manager=cm,  # type: ignore
            policy_manager=PolicyManager(),
        )

    @pytest.mark.asyncio
    async def test_invoke_timeout(self) -> None:
        gt = self._make_gateway_tools(call_tool_side_effect=TimeoutError())
        result = await gt.invoke({"tool_id": "svc::do_thing", "arguments": {}})
        assert result.ok is False
        assert "E303" in (result.errors or [""])[0]

    @pytest.mark.asyncio
    async def test_invoke_connection_error(self) -> None:
        gt = self._make_gateway_tools(call_tool_side_effect=ConnectionError("refused"))
        result = await gt.invoke({"tool_id": "svc::do_thing", "arguments": {}})
        assert result.ok is False
        assert "E201" in (result.errors or [""])[0]

    @pytest.mark.asyncio
    async def test_invoke_generic_exception(self) -> None:
        gt = self._make_gateway_tools(call_tool_side_effect=RuntimeError("boom"))
        result = await gt.invoke({"tool_id": "svc::do_thing", "arguments": {}})
        assert result.ok is False
        assert "E302" in (result.errors or [""])[0]
        # Message must not contain raw traceback — just the short error text
        assert len((result.errors or [""])[0]) < 2000

    @pytest.mark.asyncio
    async def test_invoke_missing_required_arg(self) -> None:
        tool = self._make_tool(required_fields=["q"])
        gt = self._make_gateway_tools(tool=tool)
        # call_tool should never be called; the check is pre-dispatch
        called = False

        async def _should_not_be_called(*args: Any, **kwargs: Any) -> Any:
            nonlocal called
            called = True
            return {}

        gt._client_manager.call_tool = _should_not_be_called  # type: ignore[method-assign]
        result = await gt.invoke({"tool_id": "svc::do_thing", "arguments": {}})
        assert result.ok is False
        assert "E304" in (result.errors or [""])[0]
        assert not called

    @pytest.mark.asyncio
    async def test_invoke_policy_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gt = self._make_gateway_tools()
        monkeypatch.setattr(gt._policy_manager, "is_tool_allowed", lambda _: False)
        result = await gt.invoke({"tool_id": "svc::do_thing", "arguments": {}})
        assert result.ok is False
        assert "E402" in (result.errors or [""])[0]


class TestSanitizeError:
    """Unit tests for GatewayTools._sanitize_error."""

    def _err(self, msg: str) -> Exception:
        return Exception(msg)

    def test_strips_absolute_path(self) -> None:
        e = self._err(
            "/home/user/.local/lib/python3.10/site-packages/pkg/mod.py: no module"
        )
        result = GatewayTools._sanitize_error(e)
        assert "/home" not in result
        assert "mod.py" in result

    def test_truncates_at_400(self) -> None:
        e = self._err("x" * 500)
        result = GatewayTools._sanitize_error(e)
        assert len(result) == 400

    def test_no_path_unchanged_except_truncation(self) -> None:
        e = self._err("simple error message")
        result = GatewayTools._sanitize_error(e)
        assert result == "simple error message"

    def test_multiple_paths_all_stripped(self) -> None:
        e = self._err("/tmp/foo.py and /var/run/bar.sock failed")
        result = GatewayTools._sanitize_error(e)
        assert "/tmp" not in result
        assert "/var" not in result
        assert "foo.py" in result
        assert "bar.sock" in result

    def test_returns_string(self) -> None:
        result = GatewayTools._sanitize_error(ValueError("boom"))
        assert isinstance(result, str)
