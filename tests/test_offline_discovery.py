"""Tests for cache-backed offline discovery.

These tests verify that:
1. GatewayTools receives descriptions_cache during initialization
2. catalog_search includes cached tools when include_offline=True
3. Cached tools are properly merged with live tools
4. Online-only default behavior is preserved
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from pmcp.manifest.environment import CLIInfo
from pmcp.tools.handlers import GatewayTools
from pmcp.types import (
    DescriptionsCache,
    GeneratedServerDescriptions,
    PrebuiltToolInfo,
    RiskHint,
    ToolInfo,
)


class TestGatewayToolsCacheInjection:
    """Verify GatewayTools accepts descriptions_cache."""

    def test_gateway_tools_accepts_descriptions_cache_parameter(self) -> None:
        """GatewayTools.__init__ must accept descriptions_cache parameter."""
        sig = inspect.signature(GatewayTools.__init__)
        assert "descriptions_cache" in sig.parameters, (
            "GatewayTools.__init__ must accept descriptions_cache parameter"
        )

    def test_gateway_tools_stores_descriptions_cache(self) -> None:
        """GatewayTools should store descriptions_cache as attribute."""
        mock_client_manager = MagicMock()
        mock_policy_manager = MagicMock()
        mock_cache = MagicMock(spec=DescriptionsCache)

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
            descriptions_cache=mock_cache,
        )

        assert tools._descriptions_cache is mock_cache, (
            "GatewayTools should store descriptions_cache"
        )

    def test_gateway_tools_cache_defaults_to_none(self) -> None:
        """GatewayTools should default descriptions_cache to None."""
        mock_client_manager = MagicMock()
        mock_policy_manager = MagicMock()

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
        )

        assert tools._descriptions_cache is None, (
            "descriptions_cache should default to None"
        )


class TestServerPassesCache:
    """Verify GatewayServer passes cache to GatewayTools."""

    def test_server_passes_cache_to_gateway_tools(self) -> None:
        """GatewayServer should pass _descriptions_cache to GatewayTools."""
        from pmcp.server import GatewayServer

        # Check that initialize() sets the cache on gateway_tools after loading
        init_source = inspect.getsource(GatewayServer.initialize)

        # Should set the cache on gateway_tools after loading
        assert "_descriptions_cache" in init_source, (
            "GatewayServer.initialize should reference _descriptions_cache"
        )
        assert (
            "_gateway_tools._descriptions_cache" in init_source
            or "_gateway_tools" in init_source
            and "_descriptions_cache" in init_source
        ), "GatewayServer.initialize should pass cache to GatewayTools"


class TestCatalogSearchOfflineDiscovery:
    """Verify catalog_search merges cached tools when include_offline=True."""

    @pytest.fixture
    def mock_cache(self) -> DescriptionsCache:
        """Create a mock descriptions cache with offline server tools."""
        return DescriptionsCache(
            generated_at="2026-01-22T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                "offline-server": GeneratedServerDescriptions(
                    package="@test/offline-server",
                    version="1.0.0",
                    generated_at="2026-01-22T00:00:00Z",
                    capability_summary="offline-server (2 tools): test",
                    tools=[
                        PrebuiltToolInfo(
                            name="offline_tool_1",
                            description="A tool from offline server",
                            short_description="Offline tool 1",
                            tags=["test"],
                            risk_hint="low",
                        ),
                        PrebuiltToolInfo(
                            name="offline_tool_2",
                            description="Another tool from offline server",
                            short_description="Offline tool 2",
                            tags=["test"],
                            risk_hint="medium",
                        ),
                    ],
                ),
            },
        )

    @pytest.fixture
    def gateway_tools_with_cache(self, mock_cache: DescriptionsCache) -> GatewayTools:
        """Create GatewayTools with mock cache and no live tools."""
        mock_client_manager = MagicMock()
        mock_client_manager.get_all_tools.return_value = []  # No live tools
        mock_client_manager.is_server_online.return_value = False

        mock_policy_manager = MagicMock()
        mock_policy_manager.is_tool_allowed.return_value = True
        mock_policy_manager.is_server_allowed.return_value = True

        return GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
            descriptions_cache=mock_cache,
        )

    @pytest.mark.asyncio
    async def test_include_offline_returns_cached_tools(
        self, gateway_tools_with_cache: GatewayTools
    ) -> None:
        """catalog_search with include_offline=True returns cached tools."""
        result = await gateway_tools_with_cache.catalog_search(
            {
                "include_offline": True,
            }
        )

        # Should find tools from offline server
        assert result.total_available >= 2, (
            f"Should include cached tools from offline server, got {result.total_available}"
        )

        tool_ids = [t.tool_id for t in result.results]
        assert any("offline_tool_1" in tid for tid in tool_ids), (
            f"Should include offline_tool_1 from cache, got {tool_ids}"
        )
        assert any("offline_tool_2" in tid for tid in tool_ids), (
            f"Should include offline_tool_2 from cache, got {tool_ids}"
        )
        assert result.cli_hints == []

    @pytest.mark.asyncio
    async def test_include_offline_cli_hints_do_not_count_as_available_tools(
        self, gateway_tools_with_cache: GatewayTools
    ) -> None:
        """include_offline MCP cards can coexist with separate CLI hints."""
        gateway_tools_with_cache._detected_cli_infos = {
            "git": CLIInfo(name="git", path="/usr/bin/git")
        }

        result = await gateway_tools_with_cache.catalog_search(
            {
                "query": "git test",
                "include_offline": True,
            }
        )

        assert result.cli_hints
        assert result.cli_hints[0].name == "git"
        assert result.total_available == 2
        assert len(result.results) == 2
        assert all("::" in card.tool_id for card in result.results)

    @pytest.mark.asyncio
    async def test_default_excludes_cached_tools(
        self, gateway_tools_with_cache: GatewayTools
    ) -> None:
        """catalog_search without include_offline excludes cached-only tools."""
        result = await gateway_tools_with_cache.catalog_search({})

        # Default include_offline=False should not show offline tools
        assert result.total_available == 0, (
            f"Default should not include cached-only tools, got {result.total_available}"
        )

    @pytest.mark.asyncio
    async def test_cached_tools_marked_as_offline(
        self, gateway_tools_with_cache: GatewayTools
    ) -> None:
        """Cached tools should be marked as from offline server."""
        result = await gateway_tools_with_cache.catalog_search(
            {
                "include_offline": True,
            }
        )

        # Tools from cache should have availability="offline"
        for tool in result.results:
            assert tool.availability == "offline", (
                f"Cached tool {tool.tool_id} should be marked as offline"
            )

    @pytest.mark.asyncio
    async def test_cached_tools_have_correct_server_name(
        self, gateway_tools_with_cache: GatewayTools
    ) -> None:
        """Cached tools should have correct server name in tool_id."""
        result = await gateway_tools_with_cache.catalog_search(
            {
                "include_offline": True,
            }
        )

        for tool in result.results:
            assert tool.server == "offline-server", (
                f"Cached tool should have server 'offline-server', got {tool.server}"
            )
            assert tool.tool_id.startswith("offline-server::"), (
                f"Tool ID should be prefixed with server name, got {tool.tool_id}"
            )


class TestCacheMergeWithLiveTools:
    """Verify cached and live tools merge correctly."""

    @pytest.mark.asyncio
    async def test_live_tools_take_precedence(self) -> None:
        """Live tools should override cached versions for same server."""
        # Create cache with server "test-server"
        mock_cache = DescriptionsCache(
            generated_at="2026-01-22T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                "test-server": GeneratedServerDescriptions(
                    package="@test/server",
                    version="1.0.0",
                    generated_at="2026-01-22T00:00:00Z",
                    capability_summary="cached",
                    tools=[
                        PrebuiltToolInfo(
                            name="tool_a",
                            description="Cached version",
                            short_description="Cached",
                            tags=["test"],
                            risk_hint="low",
                        ),
                    ],
                ),
            },
        )

        # Create live tool from same server
        mock_live_tool = ToolInfo(
            tool_id="test-server::tool_a",
            server_name="test-server",
            tool_name="tool_a",
            description="Live version description",
            short_description="Live version",
            input_schema={},
            tags=["test"],
            risk_hint=RiskHint.LOW,
        )

        mock_client_manager = MagicMock()
        mock_client_manager.get_all_tools.return_value = [mock_live_tool]
        mock_client_manager.is_server_online.return_value = True

        mock_policy_manager = MagicMock()
        mock_policy_manager.is_tool_allowed.return_value = True

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
            descriptions_cache=mock_cache,
        )

        result = await tools.catalog_search({"include_offline": True})

        # Should have only 1 tool (live takes precedence)
        assert len(result.results) == 1, (
            f"Live tool should replace cached, got {len(result.results)}"
        )
        assert result.results[0].short_description == "Live version", (
            f"Live tool data should be used, not cached. Got: {result.results[0].short_description}"
        )

    @pytest.mark.asyncio
    async def test_offline_server_tools_added_when_server_not_online(self) -> None:
        """Cached tools from offline servers should be added to results."""
        # Create cache with two servers
        mock_cache = DescriptionsCache(
            generated_at="2026-01-22T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                "online-server": GeneratedServerDescriptions(
                    package="@test/online",
                    version="1.0.0",
                    generated_at="2026-01-22T00:00:00Z",
                    capability_summary="online",
                    tools=[
                        PrebuiltToolInfo(
                            name="online_tool",
                            description="Online tool",
                            short_description="Online",
                            tags=["test"],
                            risk_hint="low",
                        ),
                    ],
                ),
                "offline-server": GeneratedServerDescriptions(
                    package="@test/offline",
                    version="1.0.0",
                    generated_at="2026-01-22T00:00:00Z",
                    capability_summary="offline",
                    tools=[
                        PrebuiltToolInfo(
                            name="offline_tool",
                            description="Offline tool",
                            short_description="Offline",
                            tags=["test"],
                            risk_hint="low",
                        ),
                    ],
                ),
            },
        )

        # Create live tool from online server
        mock_live_tool = ToolInfo(
            tool_id="online-server::online_tool",
            server_name="online-server",
            tool_name="online_tool",
            description="Live online tool",
            short_description="Live online",
            input_schema={},
            tags=["test"],
            risk_hint=RiskHint.LOW,
        )

        mock_client_manager = MagicMock()
        mock_client_manager.get_all_tools.return_value = [mock_live_tool]
        # Only online-server is online
        mock_client_manager.is_server_online.side_effect = lambda name: (
            name == "online-server"
        )

        mock_policy_manager = MagicMock()
        mock_policy_manager.is_tool_allowed.return_value = True
        mock_policy_manager.is_server_allowed.return_value = True

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
            descriptions_cache=mock_cache,
        )

        result = await tools.catalog_search({"include_offline": True})

        # Should have 2 tools: live online + cached offline
        assert len(result.results) == 2, (
            f"Should have 2 tools, got {len(result.results)}"
        )

        tool_ids = {t.tool_id for t in result.results}
        assert "online-server::online_tool" in tool_ids
        assert "offline-server::offline_tool" in tool_ids


class TestCachePolicyEnforcement:
    """Verify policy is enforced for cached tools."""

    @pytest.mark.asyncio
    async def test_denied_servers_excluded_from_cache(self) -> None:
        """Cached tools from denied servers should not be included."""
        mock_cache = DescriptionsCache(
            generated_at="2026-01-22T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                "allowed-server": GeneratedServerDescriptions(
                    package="@test/allowed",
                    version="1.0.0",
                    generated_at="2026-01-22T00:00:00Z",
                    capability_summary="allowed",
                    tools=[
                        PrebuiltToolInfo(
                            name="allowed_tool",
                            description="Allowed tool",
                            short_description="Allowed",
                            tags=["test"],
                            risk_hint="low",
                        ),
                    ],
                ),
                "denied-server": GeneratedServerDescriptions(
                    package="@test/denied",
                    version="1.0.0",
                    generated_at="2026-01-22T00:00:00Z",
                    capability_summary="denied",
                    tools=[
                        PrebuiltToolInfo(
                            name="denied_tool",
                            description="Denied tool",
                            short_description="Denied",
                            tags=["test"],
                            risk_hint="high",
                        ),
                    ],
                ),
            },
        )

        mock_client_manager = MagicMock()
        mock_client_manager.get_all_tools.return_value = []
        mock_client_manager.is_server_online.return_value = False

        mock_policy_manager = MagicMock()
        mock_policy_manager.is_tool_allowed.return_value = True
        # Deny "denied-server"
        mock_policy_manager.is_server_allowed.side_effect = lambda name: (
            name != "denied-server"
        )

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
            descriptions_cache=mock_cache,
        )

        result = await tools.catalog_search({"include_offline": True})

        # Should only have allowed server tool
        assert len(result.results) == 1
        assert result.results[0].tool_id == "allowed-server::allowed_tool"

    @pytest.mark.asyncio
    async def test_denied_tools_excluded_from_cache(self) -> None:
        """Cached tools that are denied by policy should not be included."""
        mock_cache = DescriptionsCache(
            generated_at="2026-01-22T00:00:00Z",
            gateway_version="1.0.0",
            servers={
                "test-server": GeneratedServerDescriptions(
                    package="@test/server",
                    version="1.0.0",
                    generated_at="2026-01-22T00:00:00Z",
                    capability_summary="test",
                    tools=[
                        PrebuiltToolInfo(
                            name="allowed_tool",
                            description="Allowed tool",
                            short_description="Allowed",
                            tags=["test"],
                            risk_hint="low",
                        ),
                        PrebuiltToolInfo(
                            name="delete_dangerous",
                            description="Dangerous delete tool",
                            short_description="Dangerous",
                            tags=["delete"],
                            risk_hint="high",
                        ),
                    ],
                ),
            },
        )

        mock_client_manager = MagicMock()
        mock_client_manager.get_all_tools.return_value = []
        mock_client_manager.is_server_online.return_value = False

        mock_policy_manager = MagicMock()
        mock_policy_manager.is_server_allowed.return_value = True
        # Deny tools with "delete" in the name
        mock_policy_manager.is_tool_allowed.side_effect = lambda tool_id: (
            "delete" not in tool_id
        )

        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
            descriptions_cache=mock_cache,
        )

        result = await tools.catalog_search({"include_offline": True})

        # Should only have allowed tool
        assert len(result.results) == 1
        assert result.results[0].tool_id == "test-server::allowed_tool"


class TestNoCacheScenario:
    """Verify behavior when no cache is available."""

    @pytest.mark.asyncio
    async def test_include_offline_with_no_cache_returns_only_live(self) -> None:
        """With no cache, include_offline should still work (just no cached tools)."""
        mock_live_tool = ToolInfo(
            tool_id="live-server::live_tool",
            server_name="live-server",
            tool_name="live_tool",
            description="Live tool",
            short_description="Live",
            input_schema={},
            tags=["test"],
            risk_hint=RiskHint.LOW,
        )

        mock_client_manager = MagicMock()
        mock_client_manager.get_all_tools.return_value = [mock_live_tool]
        mock_client_manager.is_server_online.return_value = True

        mock_policy_manager = MagicMock()
        mock_policy_manager.is_tool_allowed.return_value = True

        # No cache provided
        tools = GatewayTools(
            client_manager=mock_client_manager,
            policy_manager=mock_policy_manager,
            descriptions_cache=None,
        )

        result = await tools.catalog_search({"include_offline": True})

        # Should return only live tools
        assert len(result.results) == 1
        assert result.results[0].tool_id == "live-server::live_tool"
