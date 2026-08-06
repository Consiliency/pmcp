"""P5 gate tests: handler gates and capability discovery (consumers 3, 4, 6).

Consumers 1, 2, 5, 7 live in tests/test_credential_gates_startup.py (SL-2); the
seven-consumer end-to-end proof lives in tests/test_credential_optionality_e2e.py
(SL-4). Also pins the producer invariants — the registry candidate, the
synthesized `.mcp.json` entry, and `register_discovered_server` must report
requires_api_key exactly as before; this lane does not touch producers.
"""

from __future__ import annotations

from typing import Any

import pytest

from pmcp.manifest.loader import Manifest, ServerConfig
from pmcp.manifest.registry import RegistryPackage, RegistryServerEntry
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig, ServerStatus


class MockClientManager:
    """Minimal client manager stub covering what connect_server/provision need."""

    def __init__(self) -> None:
        self._online: set[str] = set()
        self.connected_configs: list[Any] = []

    def is_server_online(self, name: str) -> bool:
        return name in self._online

    def is_lazy_server(self, name: str) -> bool:
        return False

    def get_server_status(self, name: str) -> ServerStatus | None:
        return None

    def get_all_server_statuses(self) -> list[ServerStatus]:
        return []

    def get_registry_meta(self) -> tuple[str, float]:
        return ("test-rev", 0.0)

    async def connect_server(self, config: Any) -> list[str]:
        self.connected_configs.append(config)
        return []

    def get_all_tools(self) -> list[Any]:
        return []

    async def ensure_connected(self, name: str) -> bool:
        self._online.add(name)
        return True


def _relaxable_server(
    *,
    name: str = "firecrawl",
    requires_api_key: bool = True,
    env_var: str | None = "FIRECRAWL_API_KEY",
    api_key_optional_when: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    keywords: list[str] | None = None,
) -> ServerConfig:
    return ServerConfig(
        name=name,
        description=f"{name} server",
        keywords=keywords or [name],
        install={},
        command=f"{name}-mcp",
        args=[],
        requires_api_key=requires_api_key,
        env_var=env_var,
        api_key_optional_when=api_key_optional_when or [],
        extra_env=extra_env or {},
    )


def _manifest(*servers: ServerConfig) -> Manifest:
    return Manifest(
        version="1.0",
        cli_alternatives={},
        servers={s.name: s for s in servers},
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )


def _gateway_tools(manifest: Manifest, monkeypatch: pytest.MonkeyPatch) -> GatewayTools:
    return _gateway_tools_with_configured(manifest, [], monkeypatch)


def _gateway_tools_with_configured(
    manifest: Manifest,
    configured_configs: list[ResolvedServerConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> GatewayTools:
    client_manager = MockClientManager()
    policy_manager = PolicyManager()
    tools = GatewayTools(
        client_manager=client_manager,  # type: ignore[arg-type]
        policy_manager=policy_manager,
    )
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_configs", lambda **_: configured_configs
    )
    monkeypatch.setattr("pmcp.tools.handlers.load_dotenv", lambda *a, **kw: False)
    return tools


# ---------------------------------------------------------------------------
# Gate 3 — lifecycle connect (gateway.connect_server)
# ---------------------------------------------------------------------------


class TestGate3ConnectServer:
    @pytest.mark.asyncio
    async def test_relaxed_server_does_not_report_missing_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(
            _relaxable_server(
                api_key_optional_when=["FIRECRAWL_API_URL"],
                extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
            )
        )
        tools = _gateway_tools(manifest, monkeypatch)

        result = await tools.connect_server({"server_name": "firecrawl"})

        assert result.auth_state != "missing_auth"

    @pytest.mark.asyncio
    async def test_required_server_still_reports_missing_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(_relaxable_server(api_key_optional_when=[]))
        tools = _gateway_tools(manifest, monkeypatch)

        result = await tools.connect_server({"server_name": "firecrawl"})

        assert result.ok is False
        assert result.auth_state == "missing_auth"
        assert result.next_step == "gateway.auth_connect(server_name='firecrawl')"


# ---------------------------------------------------------------------------
# Gate 4 — provisioning (gateway.provision)
# ---------------------------------------------------------------------------


class TestGate4Provision:
    @pytest.mark.asyncio
    async def test_relaxed_server_does_not_need_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(
            _relaxable_server(
                api_key_optional_when=["FIRECRAWL_API_URL"],
                extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
            )
        )
        tools = _gateway_tools(manifest, monkeypatch)

        result = await tools.provision({"server_name": "firecrawl"})

        assert result.needs_api_key is not True
        assert result.auth_state != "missing_auth"

    @pytest.mark.asyncio
    async def test_required_server_still_needs_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(_relaxable_server(api_key_optional_when=[]))
        tools = _gateway_tools(manifest, monkeypatch)

        result = await tools.provision({"server_name": "firecrawl"})

        assert result.ok is False
        assert result.needs_api_key is True
        assert result.auth_state == "missing_auth"


# ---------------------------------------------------------------------------
# Gate 6 — capability discovery (_get_server_env_metadata and its consumers)
# ---------------------------------------------------------------------------


class TestGate6CapabilityDiscovery:
    @pytest.mark.asyncio
    async def test_catalog_search_candidate_reports_no_key_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(
            _relaxable_server(
                keywords=["firecrawl", "scrape", "crawl"],
                api_key_optional_when=["FIRECRAWL_API_URL"],
                extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
            )
        )
        tools = _gateway_tools(manifest, monkeypatch)

        # catalog_search's manifest-provision candidates are built by
        # _manifest_candidates_for_query (consumer 6's other call site);
        # exercise that builder directly to keep this gate test independent
        # of unrelated tool/keyword ranking.
        candidates = tools._manifest_candidates_for_query(
            "firecrawl scrape",
            manifest=manifest,
            configured_servers={},
            exclude_servers=set(),
        )
        assert candidates
        assert candidates[0].requires_api_key is False

    @pytest.mark.asyncio
    async def test_request_capability_name_match_relaxed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(
            _relaxable_server(
                api_key_optional_when=["FIRECRAWL_API_URL"],
                extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
            )
        )
        tools = _gateway_tools(manifest, monkeypatch)

        result = await tools.request_capability({"query": "firecrawl"})

        assert result.status == "candidates"
        assert result.candidates[0].requires_api_key is False
        assert "No API key required" in result.message
        assert "auth_connect" not in result.message
        assert "auth_connect" not in (result.recommendation or "")

    @pytest.mark.asyncio
    async def test_request_capability_name_match_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(_relaxable_server(api_key_optional_when=[]))
        tools = _gateway_tools(manifest, monkeypatch)

        result = await tools.request_capability({"query": "firecrawl"})

        assert result.status == "candidates"
        assert result.candidates[0].requires_api_key is True
        assert "Requires API key" in result.message
        assert "gateway.auth_connect" in result.message

    @pytest.mark.asyncio
    async def test_request_capability_category_tiering_sorts_relaxed_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """firecrawl and tavily are both in the 'scraping/search' category
        (manifest/loader.py's _CATEGORY_MAP). A relaxed firecrawl must sort
        into the no-key-required tier ahead of a still-required tavily."""
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        manifest = _manifest(
            _relaxable_server(
                keywords=["firecrawl", "scraping", "crawl"],
                api_key_optional_when=["FIRECRAWL_API_URL"],
                extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
            ),
            _relaxable_server(
                name="tavily",
                env_var="TAVILY_API_KEY",
                keywords=["tavily", "search"],
                api_key_optional_when=[],
            ),
        )
        tools = _gateway_tools(manifest, monkeypatch)

        result = await tools.request_capability({"query": "scraping search"})

        assert result.status == "pick_from_category"
        names_in_order = [c.name for c in result.candidates]
        assert names_in_order.index("firecrawl") < names_in_order.index("tavily")
        by_name = {c.name: c for c in result.candidates}
        assert by_name["firecrawl"].requires_api_key is False
        assert by_name["tavily"].requires_api_key is True
        assert "No API key required: firecrawl." in result.message
        assert "Requires API key (not set): tavily" in result.message


# ---------------------------------------------------------------------------
# Configured-duplicate credential gate (board review finding 1)
#
# A .mcp.json entry duplicating a manifest server was previously never
# credential-gated in connect_server (_resolve_lifecycle_config), provision,
# or capability discovery (_get_server_env_metadata) — each of those code
# paths branched on "is this name configured?" and never consulted the
# manifest server's requires_api_key/api_key_optional_when at all for that
# branch, regardless of what the predicate would say.
# ---------------------------------------------------------------------------


def _configured_local(
    name: str, env: dict[str, str] | None = None
) -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name,
        source="project",
        config=LocalMcpServerConfig(command=f"{name}-mcp", env=env),
    )


class TestConfiguredDuplicateCredentialGate:
    @pytest.mark.asyncio
    async def test_connect_configured_duplicate_required_no_credential_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(_relaxable_server(api_key_optional_when=[]))
        configured = _configured_local("firecrawl")
        tools = _gateway_tools_with_configured(manifest, [configured], monkeypatch)

        result = await tools.connect_server({"server_name": "firecrawl"})

        assert result.ok is False
        assert result.auth_state == "missing_auth"

    @pytest.mark.asyncio
    async def test_connect_configured_duplicate_relaxed_via_own_env_proceeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(
            _relaxable_server(api_key_optional_when=["FIRECRAWL_API_URL"])
        )
        configured = _configured_local(
            "firecrawl", env={"FIRECRAWL_API_URL": "http://localhost:3002"}
        )
        tools = _gateway_tools_with_configured(manifest, [configured], monkeypatch)

        result = await tools.connect_server({"server_name": "firecrawl"})

        assert result.auth_state != "missing_auth"

    @pytest.mark.asyncio
    async def test_provision_configured_duplicate_required_no_credential_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(_relaxable_server(api_key_optional_when=[]))
        configured = _configured_local("firecrawl")
        tools = _gateway_tools_with_configured(manifest, [configured], monkeypatch)

        result = await tools.provision({"server_name": "firecrawl"})

        assert result.ok is False
        assert result.needs_api_key is True
        assert result.auth_state == "missing_auth"

    @pytest.mark.asyncio
    async def test_provision_configured_duplicate_relaxed_via_own_env_proceeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(
            _relaxable_server(api_key_optional_when=["FIRECRAWL_API_URL"])
        )
        configured = _configured_local(
            "firecrawl", env={"FIRECRAWL_API_URL": "http://localhost:3002"}
        )
        tools = _gateway_tools_with_configured(manifest, [configured], monkeypatch)

        result = await tools.provision({"server_name": "firecrawl"})

        assert result.needs_api_key is not True
        assert result.auth_state != "missing_auth"

    @pytest.mark.asyncio
    async def test_capability_discovery_judges_configured_child_env_not_manifest_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The manifest's own extra_env has a usable relaxer, but the
        configured duplicate overrides it to an empty string — the child
        gets a dead literal, so the effective requirement must be True."""
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        manifest = _manifest(
            _relaxable_server(
                api_key_optional_when=["FIRECRAWL_API_URL"],
                extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
            )
        )
        configured = _configured_local("firecrawl", env={"FIRECRAWL_API_URL": ""})
        tools = _gateway_tools_with_configured(manifest, [configured], monkeypatch)
        configured_servers = {"firecrawl": configured}

        requires_api_key, _env_var, _instructions = tools._get_server_env_metadata(
            "firecrawl", manifest, configured_servers
        )

        assert requires_api_key is True


# ---------------------------------------------------------------------------
# Producer invariants — untouched by this lane
# ---------------------------------------------------------------------------


class TestProducerInvariants:
    """These construct requires_api_key rather than read a manifest server's
    declared value; SL-3 must not have touched them (plans/phase-plan-v11-P5.md
    Execution Notes: 'Do not edit :2977, :3336, :4954, or :4979')."""

    @pytest.mark.asyncio
    async def test_registry_candidate_reports_requires_api_key_from_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest = _manifest()
        tools = _gateway_tools(manifest, monkeypatch)

        entry_with_key = RegistryServerEntry(
            name="needs-key-registry",
            description="test",
            packages=[
                RegistryPackage(identifier="@example/mcp", env_vars=["SOME_TOKEN"])
            ],
        )
        entry_without_key = RegistryServerEntry(
            name="no-key-registry",
            description="test",
            packages=[RegistryPackage(identifier="@example/other-mcp")],
        )

        candidate_with_key = tools._registry_candidate_for_entry(entry_with_key)
        candidate_without_key = tools._registry_candidate_for_entry(entry_without_key)

        assert candidate_with_key is not None
        assert candidate_with_key.requires_api_key is True
        assert candidate_without_key is not None
        assert candidate_without_key.requires_api_key is False

    @pytest.mark.asyncio
    async def test_synthesized_configured_entry_is_always_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest = _manifest()
        tools = _gateway_tools(manifest, monkeypatch)
        configured = {
            "plain-configured": ResolvedServerConfig(
                name="plain-configured",
                source="project",
                config=LocalMcpServerConfig(command="plain-cmd"),
            )
        }

        merged = tools._build_manifest_with_config_servers(manifest, configured)

        assert merged.servers["plain-configured"].requires_api_key is False

    @pytest.mark.asyncio
    async def test_register_discovered_server_derives_from_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manifest = _manifest()
        tools = _gateway_tools(manifest, monkeypatch)

        result = await tools.register_discovered_server(
            {
                "server_name": "my-discovered",
                "package": "@example/discovered-mcp",
                "env_vars": ["DISCOVERED_TOKEN"],
            }
        )

        assert result.ok is True
        assert (
            tools._discovered_server_configs["my-discovered"].requires_api_key is True
        )

        result_no_key = await tools.register_discovered_server(
            {
                "server_name": "my-discovered-no-key",
                "package": "@example/discovered-mcp-nokey",
                "env_vars": [],
            }
        )
        assert result_no_key.ok is True
        assert (
            tools._discovered_server_configs["my-discovered-no-key"].requires_api_key
            is False
        )
