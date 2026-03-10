"""Tests for gateway tools."""

from __future__ import annotations

from typing import Any, cast
import types
import sys
from pathlib import Path

import pytest

from pmcp.manifest.loader import CLIAlternative, Manifest, ServerConfig
from pmcp.config.guidance import GuidanceConfig
from pmcp.errors import GatewayException
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


class MockClientManager:
    """Mock client manager for testing."""

    def __init__(self, tools: list[ToolInfo] | None = None) -> None:
        self._tools = {t.tool_id: t for t in (tools or [])}
        self._online_servers: set[str] = set()
        self._lazy_servers: set[str] = set()
        self._server_statuses: list[ServerStatus] = []
        self._revision_id = "test-rev"
        self._last_refresh_ts = 1234567890.0

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
        if server_name in self._lazy_servers:
            self._lazy_servers.remove(server_name)
            self._online_servers.add(server_name)
            return True
        raise ValueError(f"Unknown server: {server_name}")

    def get_all_server_statuses(self) -> list[Any]:
        return self._server_statuses

    def set_server_statuses(self, statuses: list[ServerStatus]) -> None:
        self._server_statuses = statuses

    def get_registry_meta(self) -> tuple[str, float]:
        return (self._revision_id, self._last_refresh_ts)

    async def call_tool(
        self, tool_id: str, args: dict[str, Any], timeout_ms: int
    ) -> Any:
        return {"content": [{"type": "text", "text": "result"}]}

    async def refresh(self, configs: list[Any]) -> list[str]:
        return []


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


class TestCapabilityAndProvision:
    @pytest.mark.asyncio
    async def test_request_capability_includes_configured_server(self, monkeypatch):
        client_manager = MockClientManager(create_mock_tools())
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

        async def fake_match_capability(**kwargs):
            return types.SimpleNamespace(
                matched=True,
                entry_name="custom-browser",
                entry_type="server",
                confidence=0.82,
                reasoning="Keyword match for server: custom-browser",
            )

        monkeypatch.setattr(
            "pmcp.tools.handlers.match_capability",
            fake_match_capability,
        )

        # Force BAML import failure so fallback matcher is used deterministically.
        monkeypatch.setitem(
            sys.modules, "pmcp.baml_client", types.ModuleType("pmcp.baml_client")
        )

        result = await gateway_tools.request_capability(
            {"query": "browser automation", "available_clis": []}
        )

        assert result.status == "candidates"
        assert result.candidates is not None
        assert result.candidates[0].name == "custom-browser"
        assert result.candidates[0].candidate_type == "server"

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
    async def test_request_then_provision_for_configured_server(self, monkeypatch):
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

        async def fake_match_capability(**kwargs):
            return types.SimpleNamespace(
                matched=True,
                entry_name="custom-browser",
                entry_type="server",
                confidence=0.9,
                reasoning="Keyword match for server: custom-browser",
            )

        monkeypatch.setattr(
            "pmcp.tools.handlers.match_capability", fake_match_capability
        )
        monkeypatch.setitem(
            sys.modules, "pmcp.baml_client", types.ModuleType("pmcp.baml_client")
        )

        capability = await gateway_tools.request_capability(
            {"query": "browser automation", "available_clis": []}
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

        async def fake_match_capability(**kwargs):
            return types.SimpleNamespace(
                matched=False,
                entry_name="",
                entry_type="",
                confidence=0.0,
                reasoning="No matching capability found in manifest",
            )

        monkeypatch.setattr(
            "pmcp.tools.handlers.match_capability", fake_match_capability
        )
        monkeypatch.setitem(
            sys.modules, "pmcp.baml_client", types.ModuleType("pmcp.baml_client")
        )

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
        monkeypatch.setattr(gateway_tools, "_query_mcp_registry", lambda q, limit=8: fake_entries)

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
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: Manifest(
            version="1.0",
            cli_alternatives={},
            servers={},
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        ))
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
