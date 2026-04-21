"""Phase 0: Baseline Constraint Tests.

These tests document and verify the baseline invariants of the MCP Gateway.
They serve as regression guards to ensure future phases don't break core constraints.

Constraints verified:
1. Gateway tool surface (`gateway.*`) must be the only exposed tools
2. Stdio transport must remain the default
3. Singleton lock prevents duplicate gateway instances
4. Self-reference detection prevents fork bombs
5. Policy enforcement applies to gateway tools/resources/prompts
6. Client manager behavior is preserved
7. Initialization sequence is maintained
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from pmcp.tools.handlers import get_gateway_tool_definitions
from pmcp.identity import (
    acquire_singleton_lock,
    filter_self_references,
    is_self_reference,
    release_singleton_lock,
)
from pmcp.policy.policy import PolicyManager
from pmcp.client.manager import ClientManager
from pmcp.server import GatewayServer

if TYPE_CHECKING:
    from pmcp.types import ResolvedServerConfig


# === Test Class 1: Gateway Tool Surface ===


class TestGatewayToolSurface:
    """Verifies the gateway tool API surface remains stable.

    The gateway exposes exactly 19 tools, all prefixed with `gateway.`.
    This test class ensures future phases don't accidentally remove or rename tools.
    """

    # The canonical list of gateway tools
    EXPECTED_TOOL_NAMES = frozenset(
        [
            "gateway.catalog_search",
            "gateway.describe",
            "gateway.invoke",
            "gateway.refresh",
            "gateway.connect_server",
            "gateway.disconnect_server",
            "gateway.restart_server",
            "gateway.health",
            "gateway.request_capability",
            "gateway.sync_environment",
            "gateway.provision",
            "gateway.update_server",
            "gateway.auth_connect",
            "gateway.submit_feedback",
            "gateway.provision_status",
            "gateway.list_pending",
            "gateway.cancel",
            "gateway.search_registry",
            "gateway.register_discovered_server",
        ]
    )

    def test_gateway_tool_count_is_nineteen(self) -> None:
        """Verify exactly 19 gateway tools are defined."""
        tools = get_gateway_tool_definitions()
        assert len(tools) == 19, (
            f"Expected 19 gateway tools, got {len(tools)}. "
            f"Tools: {[t.name for t in tools]}"
        )

    def test_all_tools_prefixed_with_gateway(self) -> None:
        """Verify all tools use the `gateway.` namespace prefix."""
        tools = get_gateway_tool_definitions()
        for tool in tools:
            assert tool.name.startswith("gateway."), (
                f"Tool '{tool.name}' does not have 'gateway.' prefix"
            )

    def test_gateway_tool_names_complete_set(self) -> None:
        """Verify all expected tool names are present."""
        tools = get_gateway_tool_definitions()
        actual_names = {t.name for t in tools}

        # Check for missing tools
        missing = self.EXPECTED_TOOL_NAMES - actual_names
        assert not missing, f"Missing expected tools: {missing}"

        # Check for unexpected tools
        unexpected = actual_names - self.EXPECTED_TOOL_NAMES
        assert not unexpected, f"Unexpected tools found: {unexpected}"

    def test_tools_have_valid_input_schemas(self) -> None:
        """Verify each tool has a valid inputSchema."""
        tools = get_gateway_tool_definitions()
        for tool in tools:
            assert tool.inputSchema is not None, (
                f"Tool '{tool.name}' has no inputSchema"
            )
            assert isinstance(tool.inputSchema, dict), (
                f"Tool '{tool.name}' inputSchema is not a dict"
            )
            # All schemas should be objects
            assert tool.inputSchema.get("type") == "object", (
                f"Tool '{tool.name}' inputSchema type is not 'object'"
            )

    def test_tools_have_non_empty_descriptions(self) -> None:
        """Verify each tool has a non-empty description."""
        tools = get_gateway_tool_definitions()
        for tool in tools:
            assert tool.description, f"Tool '{tool.name}' has no description"
            assert len(tool.description) >= 10, (
                f"Tool '{tool.name}' description is too short: '{tool.description}'"
            )


# === Test Class 2: Transport Constraints ===


class TestTransportConstraints:
    """Verifies stdio transport remains the default.

    The gateway must use stdio transport for MCP communication.
    This constraint ensures compatibility with Claude Desktop and other MCP clients.
    """

    def test_server_run_uses_stdio_server(self) -> None:
        """Verify default transport uses mcp.server.stdio.stdio_server."""
        # Create a gateway server
        server = GatewayServer()

        # Check that _run_stdio uses stdio_server (the default transport path)
        import inspect

        run_stdio_source = inspect.getsource(server._run_stdio)

        assert "from mcp.server.stdio import stdio_server" in run_stdio_source, (
            "GatewayServer._run_stdio() must use mcp.server.stdio.stdio_server"
        )
        assert "async with stdio_server()" in run_stdio_source, (
            "GatewayServer._run_stdio() must use 'async with stdio_server()'"
        )

        # Also verify run() defaults to stdio transport
        run_source = inspect.getsource(server.run)
        assert 'transport: str = "stdio"' in run_source, (
            "GatewayServer.run() must default to 'stdio' transport"
        )

    def test_server_name_is_mcp_gateway(self) -> None:
        """Verify server is created with name 'mcp-gateway'."""
        import inspect
        from pmcp.server import GatewayServer

        # Check the _create_server method source
        source = inspect.getsource(GatewayServer._create_server)

        assert '"mcp-gateway"' in source, (
            "Server must be created with name 'mcp-gateway'"
        )


# === Test Class 3: Singleton Lock Constraints ===


class TestSingletonLockConstraints:
    """Verifies singleton lock prevents duplicate gateway instances.

    Only one gateway should run at a time to prevent recursive spawning
    and resource conflicts.
    """

    def setup_method(self) -> None:
        """Ensure lock is released before each test."""
        release_singleton_lock()

    def teardown_method(self) -> None:
        """Release lock after each test."""
        release_singleton_lock()

    def test_acquire_lock_succeeds_first_time(self, tmp_path: Path) -> None:
        """Verify first lock acquisition succeeds."""
        result = acquire_singleton_lock(tmp_path)
        assert result is True, "First lock acquisition should succeed"

    def test_acquire_lock_fails_if_held(self, tmp_path: Path) -> None:
        """Verify second lock acquisition fails while first is held."""
        # First acquisition
        result1 = acquire_singleton_lock(tmp_path)
        assert result1 is True

        # Second acquisition should fail
        result2 = acquire_singleton_lock(tmp_path)
        assert result2 is False, (
            "Second lock acquisition should fail while first is held"
        )

    def test_release_allows_reacquisition(self, tmp_path: Path) -> None:
        """Verify lock can be reacquired after release."""
        # Acquire
        result1 = acquire_singleton_lock(tmp_path)
        assert result1 is True

        # Release
        release_singleton_lock()

        # Reacquire should succeed
        result2 = acquire_singleton_lock(tmp_path)
        assert result2 is True, "Lock should be reacquirable after release"

    def test_lock_uses_default_home_pmcp(self) -> None:
        """Verify default lock location is ~/.pmcp/gateway.lock."""
        import inspect
        from pmcp.identity import acquire_singleton_lock

        source = inspect.getsource(acquire_singleton_lock)

        assert 'Path.home() / ".pmcp"' in source, (
            "Default lock directory should be ~/.pmcp"
        )
        assert '"gateway.lock"' in source, "Lock file should be named 'gateway.lock'"

    def test_server_uses_global_lock_by_default(self) -> None:
        """Verify GatewayServer uses global lock (None) by default, not cache_dir."""
        import inspect
        from pmcp.server import GatewayServer

        # Check that _run_stdio passes self._lock_dir to acquire_singleton_lock
        source = inspect.getsource(GatewayServer._run_stdio)

        # Should NOT pass self._cache_dir to acquire_singleton_lock
        assert "acquire_singleton_lock(self._cache_dir)" not in source, (
            "GatewayServer should use global lock by default, not cache_dir"
        )
        # Should use self._lock_dir instead
        assert "acquire_singleton_lock(self._lock_dir)" in source, (
            "GatewayServer should pass self._lock_dir to acquire_singleton_lock"
        )

    def test_server_accepts_lock_dir_parameter(self) -> None:
        """Verify GatewayServer constructor accepts lock_dir parameter."""
        import inspect
        from pmcp.server import GatewayServer

        sig = inspect.signature(GatewayServer.__init__)
        assert "lock_dir" in sig.parameters, (
            "GatewayServer.__init__ must accept lock_dir parameter"
        )

    def test_global_lock_location_is_home_pmcp(self) -> None:
        """Verify global lock defaults to ~/.pmcp/gateway.lock."""
        from pmcp.identity import acquire_singleton_lock
        import inspect

        # Verify the default path logic in identity.py
        source = inspect.getsource(acquire_singleton_lock)
        assert 'Path.home() / ".pmcp"' in source, (
            "acquire_singleton_lock should default to ~/.pmcp"
        )


# === Test Class 4: Self-Reference Constraints ===


class TestSelfReferenceConstraints:
    """Verifies fork bomb prevention via self-reference detection.

    The gateway must detect and filter out configurations that would
    spawn another gateway instance, preventing infinite recursion.
    """

    def _make_config(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
    ) -> ResolvedServerConfig:
        """Create a mock server config for testing."""
        from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

        return ResolvedServerConfig(
            name=name,
            source="project",  # Must be one of: project, user, custom, manifest
            config=LocalMcpServerConfig(
                command=command,
                args=args or [],
            ),
        )

    def test_detects_pmcp_command(self) -> None:
        """Verify direct 'pmcp' command is detected as self-reference."""
        config = self._make_config("my-gateway", "pmcp")
        assert is_self_reference(config) is True, (
            "command: pmcp should be detected as self-reference"
        )

    def test_detects_uvx_pmcp(self) -> None:
        """Verify 'uvx pmcp' is detected as self-reference."""
        config = self._make_config("my-gateway", "uvx", ["pmcp"])
        assert is_self_reference(config) is True, (
            "uvx pmcp should be detected as self-reference"
        )

    def test_detects_mcp_gateway_command(self) -> None:
        """Verify 'mcp-gateway' command is detected as self-reference."""
        config = self._make_config("my-gateway", "mcp-gateway")
        assert is_self_reference(config) is True, (
            "command: mcp-gateway should be detected as self-reference"
        )

    def test_does_not_detect_other_commands(self) -> None:
        """Verify non-gateway commands are not flagged."""
        config = self._make_config(
            "github", "npx", ["-y", "@modelcontextprotocol/server-github"]
        )
        assert is_self_reference(config) is False, (
            "npx server-github should not be detected as self-reference"
        )

    def test_filter_removes_self_references(self) -> None:
        """Verify filter_self_references removes self-referential configs."""
        configs = [
            self._make_config(
                "github", "npx", ["-y", "@modelcontextprotocol/server-github"]
            ),
            self._make_config("recursive-gateway", "pmcp"),
            self._make_config(
                "filesystem", "npx", ["-y", "@modelcontextprotocol/server-filesystem"]
            ),
            self._make_config("uvx-gateway", "uvx", ["pmcp"]),
        ]

        filtered = filter_self_references(configs)

        assert len(filtered) == 2, (
            f"Expected 2 configs after filtering, got {len(filtered)}"
        )
        names = [c.name for c in filtered]
        assert "github" in names
        assert "filesystem" in names
        assert "recursive-gateway" not in names
        assert "uvx-gateway" not in names


# === Test Class 5: Policy Enforcement Constraints ===


class TestPolicyEnforcementConstraints:
    """Verifies policy enforcement applies throughout the gateway.

    Policy manager must filter tools, resources, and prompts based on
    allow/deny lists.
    """

    def test_gateway_server_creates_policy_manager(self) -> None:
        """Verify GatewayServer creates PolicyManager in __init__."""
        server = GatewayServer()
        assert hasattr(server, "_policy_manager"), (
            "GatewayServer must have _policy_manager attribute"
        )
        assert isinstance(server._policy_manager, PolicyManager), (
            "GatewayServer._policy_manager must be a PolicyManager instance"
        )

    def test_policy_manager_allows_by_default(self) -> None:
        """Verify PolicyManager allows everything by default (no policy file)."""
        manager = PolicyManager()

        assert manager.is_tool_allowed("any::tool") is True
        assert manager.is_server_allowed("any-server") is True
        assert manager.is_resource_allowed("any::resource") is True
        assert manager.is_prompt_allowed("any::prompt") is True

    def test_policy_denies_matching_patterns(self, tmp_path: Path) -> None:
        """Verify PolicyManager denies tools matching denylist patterns."""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text("""
tools:
  denylist:
    - "*::delete_*"
    - "dangerous-server::*"
""")
        manager = PolicyManager(policy_file)

        # Denied tools
        assert manager.is_tool_allowed("fs::delete_file") is False
        assert manager.is_tool_allowed("dangerous-server::any_tool") is False

        # Allowed tools
        assert manager.is_tool_allowed("fs::read_file") is True
        assert manager.is_tool_allowed("safe-server::any_tool") is True

    def test_policy_allowlist_restricts(self, tmp_path: Path) -> None:
        """Verify PolicyManager restricts to allowlist when specified."""
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text("""
servers:
  allowlist:
    - "github"
    - "filesystem"
""")
        manager = PolicyManager(policy_file)

        # Allowed servers
        assert manager.is_server_allowed("github") is True
        assert manager.is_server_allowed("filesystem") is True

        # Denied servers (not in allowlist)
        assert manager.is_server_allowed("jira") is False
        assert manager.is_server_allowed("unknown") is False


# === Test Class 6: Client Manager Constraints ===


class TestClientManagerConstraints:
    """Verifies client manager behavior is preserved.

    The client manager handles connections to downstream MCP servers
    and maintains the tool registry.
    """

    def test_gateway_tools_receives_descriptions_cache(self) -> None:
        """Verify GatewayTools can receive descriptions_cache."""
        import inspect
        from pmcp.tools.handlers import GatewayTools

        sig = inspect.signature(GatewayTools.__init__)
        assert "descriptions_cache" in sig.parameters, (
            "GatewayTools must accept descriptions_cache for offline discovery"
        )

    def test_connect_all_enables_retry_by_default(self) -> None:
        """Verify connect_all has retry=True by default."""
        import inspect
        from pmcp.client.manager import ClientManager

        sig = inspect.signature(ClientManager.connect_all)
        retry_param = sig.parameters.get("retry")

        assert retry_param is not None, "connect_all must have retry parameter"
        assert retry_param.default is True, (
            f"connect_all retry should default to True, got {retry_param.default}"
        )

    def test_maintains_tool_registry(self) -> None:
        """Verify ClientManager maintains tool registry indexed by tool_id."""
        manager = ClientManager()

        # Verify internal _tools dict exists
        assert hasattr(manager, "_tools"), "ClientManager must have _tools registry"
        assert isinstance(manager._tools, dict), "_tools must be a dict"

        # Verify accessor methods exist
        assert hasattr(manager, "get_tool"), "Must have get_tool method"
        assert hasattr(manager, "get_all_tools"), "Must have get_all_tools method"

    def test_maintains_server_status_tracking(self) -> None:
        """Verify ClientManager tracks server statuses."""
        manager = ClientManager()

        # Verify internal _servers dict exists
        assert hasattr(manager, "_servers"), "ClientManager must have _servers registry"
        assert isinstance(manager._servers, dict), "_servers must be a dict"

        # Verify accessor methods exist
        assert hasattr(manager, "get_server_status"), (
            "Must have get_server_status method"
        )
        assert hasattr(manager, "get_all_server_statuses"), (
            "Must have get_all_server_statuses method"
        )
        assert hasattr(manager, "is_server_online"), "Must have is_server_online method"

    def test_max_tools_per_server_configurable(self) -> None:
        """Verify max_tools_per_server can be configured."""
        manager = ClientManager(max_tools_per_server=50)
        assert manager._max_tools_per_server == 50, (
            "max_tools_per_server should be configurable"
        )


# === Test Class 7: Initialization Sequence ===


class TestInitializationSequence:
    """Verifies initialization order is preserved.

    The gateway must follow a specific initialization sequence:
    1. Load configs from discovery paths
    2. Filter self-references
    3. Connect to servers
    4. Start health monitor
    """

    @pytest.mark.asyncio
    async def test_initialize_loads_configs(self) -> None:
        """Verify initialize() loads configs from discovery paths."""
        with patch("pmcp.server.load_configs") as mock_load:
            mock_load.return_value = []

            server = GatewayServer()

            # Mock other dependencies
            with patch.object(
                server._client_manager, "connect_all", new_callable=AsyncMock
            ) as mock_connect:
                mock_connect.return_value = []
                with patch.object(server._client_manager, "start_health_monitor"):
                    with patch.object(
                        server._client_manager,
                        "get_all_server_statuses",
                        return_value=[],
                    ):
                        with patch.object(
                            server._client_manager, "get_all_tools", return_value=[]
                        ):
                            with patch(
                                "pmcp.server.generate_capability_summary",
                                new_callable=AsyncMock,
                            ) as mock_summary:
                                mock_summary.return_value = "test summary"
                                await server.initialize()

            mock_load.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_filters_self_references(self) -> None:
        """Verify initialize() calls filter_self_references."""
        with patch("pmcp.server.load_configs") as mock_load:
            mock_load.return_value = []

            with patch("pmcp.server.filter_self_references") as mock_filter:
                mock_filter.return_value = []

                server = GatewayServer()

                with patch.object(
                    server._client_manager, "connect_all", new_callable=AsyncMock
                ) as mock_connect:
                    mock_connect.return_value = []
                    with patch.object(server._client_manager, "start_health_monitor"):
                        with patch.object(
                            server._client_manager,
                            "get_all_server_statuses",
                            return_value=[],
                        ):
                            with patch.object(
                                server._client_manager, "get_all_tools", return_value=[]
                            ):
                                with patch(
                                    "pmcp.server.generate_capability_summary",
                                    new_callable=AsyncMock,
                                ) as mock_summary:
                                    mock_summary.return_value = "test summary"
                                    await server.initialize()

                mock_filter.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_skips_connect_all_without_eager_servers(self) -> None:
        """Verify initialize() does not connect when startup policy has no eager servers."""
        with (
            patch("pmcp.server.load_configs") as mock_load,
            patch("pmcp.server.load_manifest") as mock_manifest,
            patch("pmcp.server.load_enabled_auto_start", return_value=set()),
            patch("pmcp.server.load_disabled_auto_start", return_value=set()),
        ):
            mock_load.return_value = []
            mock_manifest.return_value.servers = {}

            server = GatewayServer()

            with patch.object(
                server._client_manager, "register_lazy_configs"
            ) as mock_register:
                with patch.object(
                    server._client_manager, "connect_all", new_callable=AsyncMock
                ) as mock_connect:
                    mock_connect.return_value = []
                    with patch.object(server._client_manager, "start_health_monitor"):
                        with patch.object(
                            server._client_manager,
                            "get_all_server_statuses",
                            return_value=[],
                        ):
                            with patch.object(
                                server._client_manager, "get_all_tools", return_value=[]
                            ):
                                with patch(
                                    "pmcp.server.generate_capability_summary",
                                    new_callable=AsyncMock,
                                ) as mock_summary:
                                    mock_summary.return_value = "test summary"
                                    await server.initialize()

                mock_register.assert_called_once_with([])
                mock_connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_connects_eager_servers(self) -> None:
        """Verify initialize() calls connect_all for autoStart servers."""
        from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

        eager_config = ResolvedServerConfig(
            name="eager",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        with (
            patch("pmcp.server.load_configs", return_value=[eager_config]),
            patch("pmcp.server.load_manifest") as mock_manifest,
            patch("pmcp.server.load_enabled_auto_start", return_value={"eager"}),
            patch("pmcp.server.load_disabled_auto_start", return_value=set()),
        ):
            mock_manifest.return_value.servers = {}
            server = GatewayServer()

            with patch.object(
                server._client_manager, "connect_all", new_callable=AsyncMock
            ) as mock_connect:
                mock_connect.return_value = []
                with patch.object(server._client_manager, "start_health_monitor"):
                    with patch.object(
                        server._client_manager,
                        "get_all_server_statuses",
                        return_value=[],
                    ):
                        with patch.object(
                            server._client_manager, "get_all_tools", return_value=[]
                        ):
                            with patch(
                                "pmcp.server.generate_capability_summary",
                                new_callable=AsyncMock,
                            ) as mock_summary:
                                mock_summary.return_value = "test summary"
                                await server.initialize()

                mock_connect.assert_called_once()
                assert mock_connect.call_args.args[0] == [eager_config]

    @pytest.mark.asyncio
    async def test_initialize_starts_health_monitor(self) -> None:
        """Verify initialize() starts the health monitor."""
        with patch("pmcp.server.load_configs") as mock_load:
            mock_load.return_value = []

            server = GatewayServer()

            with patch.object(
                server._client_manager, "connect_all", new_callable=AsyncMock
            ) as mock_connect:
                mock_connect.return_value = []
                with patch.object(
                    server._client_manager, "start_health_monitor"
                ) as mock_health:
                    with patch.object(
                        server._client_manager,
                        "get_all_server_statuses",
                        return_value=[],
                    ):
                        with patch.object(
                            server._client_manager, "get_all_tools", return_value=[]
                        ):
                            with patch(
                                "pmcp.server.generate_capability_summary",
                                new_callable=AsyncMock,
                            ) as mock_summary:
                                mock_summary.return_value = "test summary"
                                await server.initialize()

                    mock_health.assert_called_once()


# === Test Class 8: README Documentation Consistency ===


class TestReadmeToolCount:
    """Verify README tool count matches implementation.

    These tests ensure documentation stays in sync with the actual
    tool definitions in the codebase.
    """

    def test_readme_tool_count_matches_implementation(self) -> None:
        """README should state the correct number of gateway tools."""
        # Get actual tool count from implementation
        actual_tools = get_gateway_tool_definitions()
        actual_count = len(actual_tools)

        # Read README
        readme_path = Path(__file__).parent.parent / "README.md"
        readme_content = readme_path.read_text()

        # Find tool count claims in README (e.g., "9 meta-tools", "11 meta-tools")
        # Pattern matches: "X meta-tools" or "X stable meta-tools"
        import re

        pattern = r"(\d+)\s+(?:stable\s+)?meta-tools"
        matches = re.findall(pattern, readme_content, re.IGNORECASE)

        assert len(matches) > 0, "README should mention meta-tools count"

        for match in matches:
            claimed_count = int(match)
            assert claimed_count == actual_count, (
                f"README claims {claimed_count} meta-tools but implementation has {actual_count}. "
                f"Update README to reflect actual tool count."
            )

    def test_all_gateway_tools_documented_in_readme(self) -> None:
        """All gateway tools should be mentioned in README."""
        # Get actual tool names
        actual_tools = get_gateway_tool_definitions()
        tool_names = [t.name for t in actual_tools]

        # Read README
        readme_path = Path(__file__).parent.parent / "README.md"
        readme_content = readme_path.read_text()

        undocumented = []
        for tool_name in tool_names:
            # Tool should appear in README (in table or text)
            if tool_name not in readme_content:
                undocumented.append(tool_name)

        assert len(undocumented) == 0, (
            f"The following tools are not documented in README: {undocumented}"
        )

    def test_gateway_tools_table_complete(self) -> None:
        """Gateway Tools tables should include all tools."""
        actual_tools = get_gateway_tool_definitions()
        tool_names = {t.name for t in actual_tools}

        readme_path = Path(__file__).parent.parent / "README.md"
        readme_content = readme_path.read_text()

        # Extract tools mentioned in markdown tables (| `gateway.xxx` |)
        import re

        table_pattern = r"\|\s*`(gateway\.[a-z_]+)`\s*\|"
        documented_in_tables = set(re.findall(table_pattern, readme_content))

        missing_from_tables = tool_names - documented_in_tables
        assert len(missing_from_tables) == 0, (
            f"The following tools are missing from README tables: {missing_from_tables}"
        )
