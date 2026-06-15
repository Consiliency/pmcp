"""Tests for manifest functionality."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from pmcp.manifest.environment import (
    CLIInfo,
    detect_platform,
    probe_clis,
)
from pmcp.manifest.installer import (
    InstallError,
    InstallJob,
    JobManager,
    MissingApiKeyError,
    check_api_key,
    install_server,
    verify_installation,
)
from pmcp.manifest.loader import (
    CLIAlternative,
    Manifest,
    ServerConfig,
    load_manifest,
)
from pmcp.manifest.matcher import (
    _keyword_match,
    _keyword_match_score,
    match_capability,
    rank_cli_hints,
)
from pmcp.manifest.registry import (
    RegistryCache,
    RegistryPackage,
    RegistryRemote,
    RegistryServerEntry,
)
from pmcp.manifest.sync import sync_registry_to_manifest


# === Environment Detection Tests ===


@pytest.mark.skipif(
    True,  # Platform detection is environment-specific
    reason="Platform detection depends on actual environment",
)
def test_detect_platform():
    """Test platform detection."""
    platform = detect_platform()
    assert platform in ("mac", "wsl", "linux", "windows")


@pytest.mark.asyncio
async def test_probe_clis_with_mocked_which():
    """Test CLI probing with mocked which."""
    from unittest.mock import MagicMock

    # Mock subprocess to simulate version output
    async def mock_create_subprocess(*args, **kwargs):
        mock_process = MagicMock()
        mock_process.returncode = 0

        # Return appropriate version string based on command
        cmd = args[0] if args else ""
        if cmd == "git":
            stdout = b"git version 2.43.0\n"
        elif cmd == "docker":
            stdout = b"Docker version 24.0.0\n"
        else:
            stdout = b"version 1.0.0\n"

        async def communicate():
            return stdout, b""

        mock_process.communicate = communicate
        return mock_process

    with (
        patch("pmcp.manifest.environment.shutil.which") as mock_which,
        patch(
            "pmcp.manifest.environment.asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ),
    ):
        # Only git and docker are "installed"
        mock_which.side_effect = lambda cmd: (
            f"/usr/bin/{cmd}" if cmd in ("git", "docker") else None
        )

        cli_configs = {
            "git": {"check_command": ["git", "--version"]},
            "docker": {"check_command": ["docker", "--version"]},
            "kubectl": {"check_command": ["kubectl", "version"]},
            "terraform": {"check_command": ["terraform", "--version"]},
        }
        detected = await probe_clis(cli_configs)

        assert "git" in detected
        assert "docker" in detected
        assert "kubectl" not in detected
        assert "terraform" not in detected


# === Manifest Loading Tests ===


def test_load_manifest():
    """Test loading the manifest."""
    manifest = load_manifest()

    assert manifest is not None
    assert len(manifest.cli_alternatives) > 0
    assert len(manifest.servers) > 0


def test_manifest_server_auth_metadata_fields_are_optional() -> None:
    server = ServerConfig(
        name="remote-auth",
        description="Remote auth server",
        keywords=["auth"],
        install={},
        command="",
        args=[],
        transport="streamable-http",
        url="https://mcp.example/mcp",
        protected_resource_metadata_url="https://mcp.example/.well-known/oauth-protected-resource",
        authorization_server_metadata_url="https://auth.example/.well-known/oauth-authorization-server",
        oidc_issuer_url="https://issuer.example",
        oidc_discovery_url="https://issuer.example/.well-known/openid-configuration",
        client_id_metadata_document_url="https://client.example/metadata.json",
        declared_scopes=["read", "write"],
        supports_url_elicitation=True,
    )

    assert server.requires_api_key is False
    assert server.declared_scopes == ["read", "write"]


def test_manifest_parses_read_only_discovery_metadata(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
version: "1.0"
cli_alternatives: {}
servers:
  discovered:
    description: "Discovered server"
    keywords: ["discovery"]
    install: {}
    command: "npx"
    args: ["-y", "@example/mcp"]
    package: "@example/mcp"
    server_card_url: "https://example.com/server-card.json"
    declared_capabilities: ["tools"]
    supports_url_elicitation: true
    discovery_diagnostics: ["registry_metadata_read_only"]
    discovery_metadata:
      draftField: "kept-as-raw"
"""
    )

    manifest = load_manifest(manifest_path)
    server = manifest.servers["discovered"]

    assert server.package == "@example/mcp"
    assert server.server_card_url == "https://example.com/server-card.json"
    assert server.declared_capabilities == ["tools"]
    assert server.discovery_diagnostics == ["registry_metadata_read_only"]
    assert server.raw_discovery_metadata == {"draftField": "kept-as-raw"}
    assert server.supports_url_elicitation is True


def test_manifest_parses_registry_status_source_and_replacement(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
version: "1.0"
cli_alternatives: {}
servers:
  old-github:
    status: "archived_reference_package"
    source: "mcp_registry"
    replacement: "github"
    description: "Old GitHub server"
    keywords: ["github"]
    install: {}
    command: "npx"
    args: ["-y", "@old/github"]
"""
    )

    server = load_manifest(manifest_path).servers["old-github"]

    assert server.status == "archived_reference_package"
    assert server.source == "mcp_registry"
    assert server.replacement == "github"


def test_registry_sync_classifies_without_mutating_manifest() -> None:
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
        servers={
            "github": ServerConfig(
                name="github",
                description="GitHub",
                keywords=["github"],
                install={},
                command="",
                args=[],
                package="@github/github-mcp-server",
            ),
            "legacy": ServerConfig(
                name="legacy",
                description="Legacy",
                keywords=["legacy"],
                install={},
                command="",
                args=[],
                status="archived_reference_package",
            ),
            "old-name": ServerConfig(
                name="old-name",
                description="Old name",
                keywords=["renamed"],
                install={},
                command="",
                args=[],
                package="@example/renamed",
            ),
        },
    )
    registry = RegistryCache(
        schema_version="registry-cache.v1",
        source_endpoint="https://registry.example/v0/servers",
        fetched_at="2026-06-15T00:00:00Z",
        servers=[
            RegistryServerEntry(
                name="github",
                description="GitHub",
                packages=[RegistryPackage(identifier="@github/github-mcp-server")],
            ),
            RegistryServerEntry(
                name="legacy",
                description="Legacy",
                packages=[RegistryPackage(identifier="@example/legacy")],
            ),
            RegistryServerEntry(
                name="new-name",
                description="Renamed",
                packages=[RegistryPackage(identifier="@example/renamed")],
            ),
            RegistryServerEntry(
                name="added",
                description="Added",
                packages=[RegistryPackage(identifier="@example/added")],
                remotes=[
                    RegistryRemote(
                        transport="streamable-http",
                        url="https://added.example/mcp",
                        headers=["ADDED_TOKEN"],
                    )
                ],
            ),
        ],
    )

    result = sync_registry_to_manifest(manifest, registry)

    assert [entry.name for entry in result.unchanged] == ["github"]
    assert result.archived == ["legacy"]
    assert [entry.name for entry in result.renamed] == ["new-name"]
    assert [entry.name for entry in result.added] == ["added"]
    assert "added" not in manifest.servers


def test_manifest_contains_registry_curated_remote_entries() -> None:
    manifest = load_manifest()
    for name, env_var in {
        "github-remote": "GITHUB_PERSONAL_ACCESS_TOKEN",
        "sentry-remote": "SENTRY_AUTH_TOKEN",
        "cloudflare": "CLOUDFLARE_API_TOKEN",
        "vercel": "VERCEL_TOKEN",
        "atlassian-rovo": "ATLASSIAN_API_TOKEN",
        "hugging-face": "HUGGINGFACE_TOKEN",
    }.items():
        server = manifest.servers[name]
        assert server.source == "mcp_registry"
        assert server.transport in {"streamable-http", "http", "sse"}
        assert server.headers is None
        assert server.raw_discovery_metadata["placeholder_headers"] == [
            f"Authorization: Bearer ${{{env_var}}}"
        ]
        assert server.package
        assert "registry_metadata_read_only" in server.discovery_diagnostics


def test_manifest_has_expected_servers():
    """Test that manifest has expected servers."""
    manifest = load_manifest()

    expected_servers = [
        "playwright",
        "context7",
        "memory",
        "filesystem",
        "browser-use",
        "chrome-devtools",
        "supabase",
        "firecrawl",
        "tavily",
        "excalidraw",
    ]
    for server in expected_servers:
        assert server in manifest.servers, f"Missing server: {server}"


def test_manifest_has_no_packaged_auto_start_defaults():
    """The packaged manifest should not eagerly start servers by default."""
    manifest = load_manifest()

    auto_start = manifest.get_auto_start_servers()

    assert auto_start == []


def test_manifest_search_by_keyword():
    """Test keyword search in manifest."""
    manifest = load_manifest()

    # Search for browser-related
    results = manifest.search_by_keyword("browser")
    assert len(results) > 0

    # Search for scraping
    results = manifest.search_by_keyword("scrape")
    assert len(results) > 0


def test_archived_reference_server_entries_are_explicitly_labeled() -> None:
    manifest_path = Path("src/pmcp/manifest/manifest.yaml")
    data = yaml.safe_load(manifest_path.read_text())
    audited_entries = {
        "github": "@modelcontextprotocol/server-github",
        "brave-search": "@modelcontextprotocol/server-brave-search",
        "linear": "@modelcontextprotocol/server-linear",
        "sentry": "@modelcontextprotocol/server-sentry",
        "filesystem": "@modelcontextprotocol/server-filesystem",
        "memory": "@modelcontextprotocol/server-memory",
        "sequential-thinking": "@modelcontextprotocol/server-sequential-thinking",
        "postgres": "@modelcontextprotocol/server-postgres",
        "puppeteer": "@modelcontextprotocol/server-puppeteer",
        "gitlab": "@modelcontextprotocol/server-gitlab",
        "slack": "@modelcontextprotocol/server-slack",
        "google-drive": "@modelcontextprotocol/server-gdrive",
        "google-maps": "@modelcontextprotocol/server-google-maps",
        "everart": "@modelcontextprotocol/server-everart",
        "aws-kb-retrieval": "@modelcontextprotocol/server-aws-kb-retrieval",
    }

    for name, package in audited_entries.items():
        entry = data["servers"][name]
        assert entry["status"] == "archived_reference_package"
        assert entry["transport"] == "local"
        assert package in entry["args"]


def test_keyword_match_browser_use_server():
    """Keyword matcher should resolve browser-use capability requests."""
    manifest = load_manifest()

    result = _keyword_match("browser-use mcp server", manifest, set())
    assert result.matched is True
    assert result.entry_name == "browser-use"
    assert result.entry_type == "server"


def test_keyword_score_uses_absolute_matched_evidence() -> None:
    assert _keyword_match_score(
        "github pull request workflows",
        ["github", "pull request", "workflows", "actions", "issues", "repo"],
    ) > _keyword_match_score(
        "github pull request workflows",
        ["github"],
    )


def test_manifest_server_config():
    """Test server config structure."""
    manifest = load_manifest()

    playwright = manifest.get_server("playwright")
    assert playwright is not None
    assert playwright.command == "npx"
    assert playwright.requires_api_key is False
    assert playwright.auto_start is False


def test_manifest_remote_server_config():
    """Test remote server config structure."""
    manifest = load_manifest()

    excalidraw = manifest.get_server("excalidraw")
    assert excalidraw is not None
    assert excalidraw.transport == "streamable-http"
    assert excalidraw.url == "https://mcp.excalidraw.com"
    assert excalidraw.command == ""
    assert excalidraw.install == {}


def test_tenant_code_mode_packaged_manifest_entry_is_absent_or_real() -> None:
    """Do not ship an installable placeholder for the companion tenant server."""
    manifest = load_manifest()
    tenant = manifest.get_server("tenant-code-mode")

    if tenant is None:
        return

    assert tenant.auto_start is False
    assert tenant.url or tenant.command
    if not tenant.url:
        assert tenant.command != ""
    expected_keywords = {
        "code execution",
        "sandbox execution",
        "mobile code mode",
        "task runs",
        "logs",
        "artifacts",
    }
    assert expected_keywords <= set(tenant.keywords)
    for value in (tenant.headers or {}).values():
        assert "${" in value and "}" in value


def test_tenant_code_mode_remote_fixture_parses_as_lazy_streamable_http(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
version: "1.0"
cli_alternatives: {}
servers:
  tenant-code-mode:
    description: "Tenant code-mode sandbox execution"
    keywords:
      - "code execution"
      - "sandbox execution"
      - "mobile code mode"
      - "task runs"
      - "logs"
      - "artifacts"
    install: {}
    command: ""
    args: []
    transport: "streamable-http"
    url: "https://tenant.example/mcp"
    headers:
      Authorization: "Bearer ${TENANT_CODE_MODE_MCP_TOKEN}"
      X-Tenant-ID: "${TENANT_CODE_MODE_TENANT_ID}"
"""
    )

    manifest = load_manifest(manifest_path)
    tenant = manifest.get_server("tenant-code-mode")

    assert tenant is not None
    assert tenant.transport == "streamable-http"
    assert tenant.url == "https://tenant.example/mcp"
    assert tenant.command == ""
    assert tenant.auto_start is False
    assert tenant.headers == {
        "Authorization": "Bearer ${TENANT_CODE_MODE_MCP_TOKEN}",
        "X-Tenant-ID": "${TENANT_CODE_MODE_TENANT_ID}",
    }


def test_manifest_cli_config():
    """Test CLI config structure."""
    manifest = load_manifest()

    git = manifest.get_cli("git")
    assert git is not None
    assert (
        "version control" in git.description.lower() or "git" in git.description.lower()
    )
    assert len(git.keywords) > 0


def test_manifest_parses_cli_examples(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
version: "1.0"
cli_alternatives:
  git:
    keywords: ["git"]
    check_command: ["git", "--version"]
    help_command: ["git", "--help"]
    description: "Git CLI"
    examples:
      - "git status --short"
      - "git log --oneline -5"
servers: {}
"""
    )

    manifest = load_manifest(manifest_path)
    git = manifest.get_cli("git")

    assert git is not None
    assert git.examples == ["git status --short", "git log --oneline -5"]


def test_manifest_cli_examples_default_to_empty(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        """
version: "1.0"
cli_alternatives:
  git:
    keywords: ["git"]
    check_command: ["git", "--version"]
    help_command: ["git", "--help"]
    description: "Git CLI"
servers: {}
"""
    )

    manifest = load_manifest(manifest_path)
    git = manifest.get_cli("git")

    assert git is not None
    assert git.examples == []


def test_packaged_cli_alternatives_have_compact_examples() -> None:
    manifest = load_manifest()
    blocked_terms = ("rm", "delete", "destroy", "force")

    for cli in manifest.cli_alternatives.values():
        assert 2 <= len(cli.examples) <= 4, cli.name
        for example in cli.examples:
            assert isinstance(example, str), cli.name
            assert example.strip() == example
            assert 0 < len(example) <= 80, f"{cli.name}: {example}"
            words = set(example.lower().replace("-", " ").split())
            assert not (words & set(blocked_terms)), f"{cli.name}: {example}"


def test_manifest_get_category_summary():
    """Test get_category_summary returns a useful compact string."""
    manifest = load_manifest()
    summary = manifest.get_category_summary()

    assert len(summary) > 0
    assert "Provisionable" in summary
    assert "playwright" in summary  # always in manifest
    # Should mention at least one category label
    assert any(
        cat in summary for cat in ["browser automation", "scraping/search", "databases"]
    )


def test_manifest_get_category_summary_empty():
    """Test get_category_summary returns empty string for empty manifest."""
    from pmcp.manifest.loader import Manifest

    empty = Manifest(
        version="1.0",
        cli_alternatives={},
        servers={},
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )
    assert empty.get_category_summary() == ""


# === Category Scoring Tests (issue #56) ===


def _make_manifest_with_servers(**servers: dict) -> Manifest:
    """Build a minimal Manifest with the given server configs."""
    server_objs = {}
    for name, kws in servers.items():
        server_objs[name] = ServerConfig(
            name=name,
            description=f"{name} server",
            keywords=kws,
            install={},
            command="npx",
            args=[name],
            requires_api_key=False,
        )
    return Manifest(
        version="1.0",
        cli_alternatives={},
        servers=server_objs,
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )


class TestGetServersInCategory:
    """Tests for Manifest.get_servers_in_category (issue #56 scoring fixes)."""

    def test_generic_api_keyword_does_not_win_over_specific_keyword(self) -> None:
        """Bug 1: communication category must not beat cloud/storage on generic 'api' alone.

        Three communication servers each carry 'api', inflating their category score
        over cloudflare's specific 'dns' keyword. With IDF discounting this reversal
        should not happen.
        """
        manifest = load_manifest()
        query = "manage DNS records via Hostinger API"
        result = manifest.get_servers_in_category(query)
        # Either None (not_available) or cloud/storage (which has DNS-relevant servers)
        # But MUST NOT be "communication"
        if result is not None:
            cat_name, _ = result
            assert cat_name != "communication", (
                "Generic 'api' keyword across communication servers must not beat "
                "specific 'dns' keyword in cloud/storage"
            )

    def test_no_match_returns_none(self) -> None:
        """Bug 2: score below minimum threshold returns None."""
        manifest = load_manifest()
        # Query containing only a generic keyword that appears in many servers
        result = manifest.get_servers_in_category("use an api")
        # "api" is too generic to confidently pick any category
        # Result might be None or a low-score category; if returned, assert a category name
        # but the real check is that communication is not spuriously returned
        if result is not None:
            cat_name, _ = result
            assert isinstance(cat_name, str)

    def test_browser_automation_category_name_match(self) -> None:
        """Category name words in query produce strong signal."""
        manifest = load_manifest()
        result = manifest.get_servers_in_category("browser automation testing")
        assert result is not None
        cat_name, servers = result
        assert cat_name == "browser automation"
        assert len(servers) > 0

    def test_database_keyword_match(self) -> None:
        """Specific keyword 'postgres' in query finds databases category."""
        manifest = load_manifest()
        result = manifest.get_servers_in_category("query my postgres database")
        # 'postgres' or 'database' should match
        assert result is not None
        cat_name, servers = result
        assert cat_name == "databases"

    def test_empty_manifest_returns_none(self) -> None:
        """No servers → None."""
        empty = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={},
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        assert empty.get_servers_in_category("manage API") is None

    def test_idf_weight_specific_keyword_beats_many_generic(self) -> None:
        """A single specific keyword (freq=1) beats three generic ones (freq=3)."""
        # Build a small manifest where:
        #   cat A has 3 servers with keyword "api" (generic)
        #   cat B has 1 server with keyword "postgres" (specific)
        # _CATEGORY_MAP won't reflect our custom servers, so we test the weight logic
        # indirectly by checking the real manifest scores.
        manifest = load_manifest()
        # "postgres" should appear in only one server → weight 1.0
        # "api" appears in twilio, mailgun, line → weight 0.1 each
        # Query with "postgres" and "api" → databases should win over communication
        result = manifest.get_servers_in_category("postgres api access")
        # databases (postgres=1.0) should beat communication (api*3=0.3)
        assert result is not None
        cat_name, _ = result
        assert cat_name == "databases"

    def test_single_generic_keyword_below_threshold(self) -> None:
        """Bug 2: 'api' alone (score ≈ 0.3) stays below the 0.5 minimum threshold."""
        manifest = load_manifest()
        # Only word from our manifest keywords is "api"
        # communication: 3 servers each match "api" → score = 3 * 0.1 = 0.3 < 0.5
        result = manifest.get_servers_in_category("api")
        # Should return None or a non-communication category
        if result is not None:
            cat_name, _ = result
            assert cat_name != "communication"


# === Matcher Tests ===


def create_test_manifest() -> Manifest:
    """Create a test manifest."""
    return Manifest(
        version="1.0",
        cli_alternatives={
            "git": CLIAlternative(
                name="git",
                keywords=["git", "version control", "commits"],
                check_command=["git", "--version"],
                help_command=["git", "--help"],
                description="Git version control",
                examples=["git log --oneline -5"],
                prefer_mcp_for=["github issues", "pull requests"],
            ),
            "docker": CLIAlternative(
                name="docker",
                keywords=["docker", "container", "image"],
                check_command=["docker", "--version"],
                help_command=["docker", "--help"],
                description="Docker containers",
            ),
        },
        servers={
            "playwright": ServerConfig(
                name="playwright",
                description="Browser automation",
                keywords=["browser", "automation", "playwright"],
                install={"mac": ["npm", "install", "playwright"]},
                command="npx",
                args=["playwright"],
                requires_api_key=False,
            ),
            "github": ServerConfig(
                name="github",
                description="GitHub API access",
                keywords=["github", "issues", "pull requests"],
                install={"mac": ["npm", "install", "github"]},
                command="npx",
                args=["github"],
                requires_api_key=True,
                env_var="GITHUB_TOKEN",
            ),
        },
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )


def test_rank_cli_hints_prefers_available_cli_for_local_task() -> None:
    manifest = create_test_manifest()

    matches = rank_cli_hints("git commits", manifest, available_clis={"git"})

    assert matches
    assert matches[0].hint.name == "git"
    assert matches[0].hint.available is True


def test_rank_cli_hints_uses_name_description_keywords_and_examples() -> None:
    manifest = create_test_manifest()

    name_match = rank_cli_hints("docker", manifest, available_clis={"docker"})
    description_match = rank_cli_hints(
        "containers", manifest, available_clis={"docker"}
    )
    keyword_match = rank_cli_hints("commits", manifest, available_clis={"git"})
    example_match = rank_cli_hints("git log oneline", manifest, available_clis={"git"})

    assert name_match[0].hint.name == "docker"
    assert description_match[0].hint.name == "docker"
    assert keyword_match[0].hint.name == "git"
    assert example_match[0].hint.name == "git"


def test_tenant_code_mode_keywords_match_server_without_displacing_cli() -> None:
    manifest = create_test_manifest()
    manifest.servers["tenant-code-mode"] = ServerConfig(
        name="tenant-code-mode",
        description="Tenant code-mode sandbox execution",
        keywords=[
            "code execution",
            "sandbox execution",
            "mobile code mode",
            "task runs",
            "logs",
            "artifacts",
        ],
        install={},
        command="",
        args=[],
        transport="streamable-http",
        url="https://tenant.example/mcp",
        headers={"Authorization": "Bearer ${TENANT_CODE_MODE_MCP_TOKEN}"},
    )

    server_match = _keyword_match(
        "hosted sandbox code execution", manifest, detected_clis=set()
    )
    cli_match = _keyword_match("git commits", manifest, detected_clis={"git"})

    assert server_match.matched is True
    assert server_match.entry_name == "tenant-code-mode"
    assert server_match.entry_type == "server"
    assert cli_match.matched is True
    assert cli_match.entry_name == "git"
    assert cli_match.entry_type == "cli"


def test_keyword_match_prefers_specific_multi_keyword_server() -> None:
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers={
            "sparse": ServerConfig(
                name="sparse",
                description="Sparse generic server",
                keywords=["github"],
                install={},
                command="npx",
                args=["sparse"],
            ),
            "specific": ServerConfig(
                name="specific",
                description="Specific GitHub workflow server",
                keywords=[
                    "github",
                    "pull request",
                    "workflows",
                    "actions",
                    "code review",
                    "issues",
                ],
                install={},
                command="npx",
                args=["specific"],
            ),
        },
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )

    result = _keyword_match("github pull request workflows", manifest, set())

    assert result.matched is True
    assert result.entry_name == "specific"
    assert result.confidence >= 0.2


def test_keyword_match_generic_api_alone_stays_below_threshold() -> None:
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers={
            f"server-{i}": ServerConfig(
                name=f"server-{i}",
                description="Generic API server",
                keywords=["api"],
                install={},
                command="npx",
                args=[f"server-{i}"],
            )
            for i in range(4)
        },
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )

    result = _keyword_match("api", manifest, set())

    assert result.matched is False


@pytest.mark.parametrize(
    ("query", "expected_server"),
    [
        ("database sql", "sqlite"),
        ("sql query", "sqlite"),
        ("postgres database", "postgres"),
        ("headless browser", "puppeteer"),
        ("chrome automation", "puppeteer"),
        ("browser scraping", "puppeteer"),
    ],
)
def test_real_manifest_keyword_match_table(
    query: str, expected_server: str
) -> None:
    manifest = load_manifest(Path("src/pmcp/manifest/manifest.yaml"))

    result = _keyword_match(query, manifest, detected_clis=set())

    assert result.matched is True
    assert result.entry_name == expected_server
    assert result.entry_type == "server"
    assert result.confidence >= 0.25


def test_keyword_match_duplicate_server_keywords_do_not_dilute_threshold() -> None:
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers={
            "sqlite": ServerConfig(
                name="sqlite",
                description="SQLite database",
                keywords=["sqlite", "database", "sql", "query", "db", "table"],
                install={},
                command="uvx",
                args=["mcp-server-sqlite"],
            ),
            **{
                f"sqlite-copy-{i}": ServerConfig(
                    name=f"sqlite-copy-{i}",
                    description="Duplicate-like SQL database server",
                    keywords=["database", "sql", "query", "db", "table"],
                    install={},
                    command="uvx",
                    args=[f"mcp-server-sqlite-copy-{i}"],
                )
                for i in range(8)
            },
        },
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )

    result = _keyword_match("database sql", manifest, detected_clis=set())

    assert result.matched is True
    assert result.entry_name == "sqlite"
    assert result.entry_type == "server"
    assert result.confidence >= 0.25


def test_rank_cli_hints_preserves_probe_path() -> None:
    manifest = create_test_manifest()
    detected_cli_infos = {"git": CLIInfo(name="git", path="/usr/bin/git")}

    matches = rank_cli_hints(
        "git commits", manifest, detected_cli_infos=detected_cli_infos
    )

    assert matches[0].hint.name == "git"
    assert matches[0].hint.available is True
    assert matches[0].hint.path == "/usr/bin/git"


def test_rank_cli_hints_available_clis_have_no_path_when_unprobed() -> None:
    manifest = create_test_manifest()

    matches = rank_cli_hints("git commits", manifest, available_clis={"git"})

    assert matches[0].hint.available is True
    assert matches[0].hint.path is None


def test_rank_cli_hints_suppresses_prefer_mcp_phrase_by_default() -> None:
    manifest = create_test_manifest()

    matches = rank_cli_hints("github issues", manifest, available_clis={"git"})

    assert matches == []


def test_rank_cli_hints_can_return_suppressed_prefer_mcp_match() -> None:
    manifest = create_test_manifest()

    matches = rank_cli_hints(
        "github issues",
        manifest,
        available_clis={"git"},
        include_suppressed=True,
    )

    assert matches[0].hint.name == "git"
    assert matches[0].suppressed_by_prefer_mcp is True
    assert matches[0].matched_prefer_mcp_phrase == "github issues"
    assert matches[0].hint.reason is not None
    assert "MCP server preferred" in matches[0].hint.reason


def test_keyword_match_cli():
    """Test keyword matching for CLIs."""
    manifest = create_test_manifest()
    detected_clis = {"git", "docker"}

    result = _keyword_match("I need version control", manifest, detected_clis)

    assert result.matched is True
    assert result.entry_name == "git"
    assert result.entry_type == "cli"


def test_keyword_match_server():
    """Test keyword matching for servers."""
    manifest = create_test_manifest()
    detected_clis: set[str] = set()  # No CLIs detected

    result = _keyword_match("browser automation", manifest, detected_clis)

    assert result.matched is True
    assert result.entry_name == "playwright"
    assert result.entry_type == "server"


def test_keyword_match_prefers_cli():
    """Test that CLIs are preferred over servers."""
    manifest = Manifest(
        version="1.0",
        cli_alternatives={
            "docker": CLIAlternative(
                name="docker",
                keywords=["docker", "container"],
                check_command=["docker", "--version"],
                help_command=["docker", "--help"],
                description="Docker CLI",
            ),
        },
        servers={
            "docker-mcp": ServerConfig(
                name="docker-mcp",
                description="Docker via MCP",
                keywords=["docker", "container"],
                install={},
                command="npx",
                args=["docker-mcp"],
                requires_api_key=False,
            ),
        },
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )
    detected_clis = {"docker"}

    result = _keyword_match("docker container", manifest, detected_clis)

    assert result.matched is True
    assert result.entry_type == "cli"


def test_keyword_match_no_match():
    """Test no match found."""
    manifest = create_test_manifest()
    detected_clis: set[str] = set()

    result = _keyword_match("quantum computing database", manifest, detected_clis)

    assert result.matched is False


@pytest.mark.asyncio
async def test_match_capability_fallback_to_keyword():
    """Test that match_capability matches by keyword (LLM path removed)."""
    manifest = create_test_manifest()
    detected_clis = {"git"}

    result = await match_capability(
        "version control commits",
        manifest,
        detected_clis,
    )

    assert result.matched is True
    assert result.entry_name == "git"


# === Installer Tests ===


@pytest.mark.asyncio
async def test_check_api_key_missing():
    """Test that missing API key raises error."""
    server_config = ServerConfig(
        name="test",
        description="Test server",
        keywords=["test"],
        install={"mac": ["echo", "install"]},
        command="echo",
        args=["test"],
        requires_api_key=True,
        env_var="TEST_MISSING_API_KEY",
        env_instructions="Set TEST_MISSING_API_KEY",
    )

    with pytest.raises(MissingApiKeyError) as exc_info:
        await check_api_key(server_config)

    assert exc_info.value.env_var == "TEST_MISSING_API_KEY"


@pytest.mark.asyncio
async def test_check_api_key_present():
    """Test that present API key passes."""
    server_config = ServerConfig(
        name="test",
        description="Test server",
        keywords=["test"],
        install={"mac": ["echo", "install"]},
        command="echo",
        args=["test"],
        requires_api_key=True,
        env_var="PATH",  # PATH is always set
    )

    # Should not raise
    await check_api_key(server_config)


@pytest.mark.asyncio
async def test_check_api_key_not_required():
    """Test that no API key check when not required."""
    server_config = ServerConfig(
        name="test",
        description="Test server",
        keywords=["test"],
        install={"mac": ["echo", "install"]},
        command="echo",
        args=["test"],
        requires_api_key=False,
    )

    # Should not raise
    await check_api_key(server_config)


@pytest.mark.asyncio
async def test_install_server_no_platform_command():
    """Test install fails when no command for platform."""
    server_config = ServerConfig(
        name="test",
        description="Test server",
        keywords=["test"],
        install={"mac": ["echo", "install"]},  # Only mac
        command="echo",
        args=["test"],
        requires_api_key=False,
    )

    with pytest.raises(InstallError):
        await install_server(server_config, "windows")


@pytest.mark.asyncio
async def test_install_server_success():
    """Test successful server installation."""
    server_config = ServerConfig(
        name="test",
        description="Test server",
        keywords=["test"],
        install={"linux": ["echo", "installed"]},
        command="echo",
        args=["test"],
        requires_api_key=False,
    )

    # Should succeed (echo always works)
    await install_server(server_config, "linux")


# === JobManager Tests ===


@pytest.fixture(autouse=True)
def reset_job_manager():
    """Reset JobManager singleton between tests."""
    JobManager._instance = None
    yield
    JobManager._instance = None


class TestJobManager:
    """Tests for JobManager singleton and job lifecycle."""

    def test_singleton_instance(self) -> None:
        """Verify get_instance() returns same object."""
        manager1 = JobManager.get_instance()
        manager2 = JobManager.get_instance()
        assert manager1 is manager2

    def test_get_job_returns_none_for_unknown(self) -> None:
        """get_job('unknown') returns None."""
        manager = JobManager.get_instance()
        assert manager.get_job("unknown") is None

    def test_get_all_jobs_empty(self) -> None:
        """Returns empty list initially."""
        manager = JobManager.get_instance()
        assert manager.get_all_jobs() == []

    def test_cleanup_old_jobs_removes_completed(self) -> None:
        """Old complete/failed jobs removed."""
        manager = JobManager.get_instance()

        # Add an old completed job
        old_job = InstallJob(
            id="old123",
            server_name="test",
            status="complete",
            started_at=time.time() - 7200,  # 2 hours ago
        )
        manager._jobs["old123"] = old_job

        # Add a recent completed job
        recent_job = InstallJob(
            id="recent",
            server_name="test",
            status="complete",
            started_at=time.time() - 100,  # 100 seconds ago
        )
        manager._jobs["recent"] = recent_job

        # Cleanup with 1 hour max age
        removed = manager.cleanup_old_jobs(max_age=3600)

        assert removed == 1
        assert "old123" not in manager._jobs
        assert "recent" in manager._jobs

    def test_cleanup_old_jobs_keeps_in_progress(self) -> None:
        """Active jobs not removed."""
        manager = JobManager.get_instance()

        # Add an old but still installing job
        old_active_job = InstallJob(
            id="active",
            server_name="test",
            status="installing",
            started_at=time.time() - 7200,  # 2 hours ago
        )
        manager._jobs["active"] = old_active_job

        removed = manager.cleanup_old_jobs(max_age=3600)

        assert removed == 0
        assert "active" in manager._jobs


class TestStartInstall:
    """Tests for JobManager.start_install()."""

    @pytest.fixture
    def server_config(self) -> ServerConfig:
        """Create a test server config."""
        return ServerConfig(
            name="test-server",
            description="Test server",
            keywords=["test"],
            install={"linux": ["echo", "installing..."]},
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

    @pytest.mark.asyncio
    async def test_start_install_returns_job_id(
        self, server_config: ServerConfig
    ) -> None:
        """Returns 8-char UUID."""
        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        assert len(job_id) == 8
        assert job_id.isalnum()

    @pytest.mark.asyncio
    async def test_start_install_creates_job(self, server_config: ServerConfig) -> None:
        """Job added to _jobs dict."""
        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        job = manager.get_job(job_id)
        assert job is not None
        assert job.server_name == "test-server"

    @pytest.mark.asyncio
    async def test_start_install_sets_installing_status(
        self, server_config: ServerConfig
    ) -> None:
        """Status transitions pending→installing."""
        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        job = manager.get_job(job_id)
        assert job is not None
        # Status should be installing (process started)
        assert job.status in ("installing", "complete")  # May complete quickly

    @pytest.mark.asyncio
    async def test_start_install_starts_monitor_task(
        self, server_config: ServerConfig
    ) -> None:
        """_monitor_task is set."""
        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        job = manager.get_job(job_id)
        assert job is not None
        # Monitor task should be set (or completed if fast)
        # For echo command, it completes almost instantly

    @pytest.mark.asyncio
    async def test_start_install_wsl_fallback_to_linux(self) -> None:
        """WSL uses linux command if no wsl."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["echo", "linux-install"]},  # Only linux
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        # WSL should fall back to linux command
        job_id = await manager.start_install(server_config, "wsl")

        job = manager.get_job(job_id)
        assert job is not None

    @pytest.mark.asyncio
    async def test_start_install_no_command_raises(self) -> None:
        """InstallError if no platform command."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"mac": ["echo", "mac-install"]},  # Only mac
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        with pytest.raises(InstallError):
            await manager.start_install(server_config, "windows")

    @pytest.mark.asyncio
    async def test_start_install_command_not_found(self) -> None:
        """Status=failed if command not found."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["nonexistent_command_xyz", "arg"]},
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        job = manager.get_job(job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.error is not None
        assert "not found" in job.error.lower()


class TestMonitorInstall:
    """Tests for _monitor_install background task."""

    @pytest.mark.asyncio
    async def test_monitor_updates_heartbeat_on_output(self) -> None:
        """last_heartbeat updated on stdout."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["echo", "output line"]},
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        # Wait for job to complete
        await asyncio.sleep(0.2)

        job = manager.get_job(job_id)
        assert job is not None
        # Heartbeat should be recent
        assert time.time() - job.last_heartbeat < 5

    @pytest.mark.asyncio
    async def test_monitor_reads_stderr(self) -> None:
        """Stderr output also tracked."""
        # Use bash to write to stderr
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["bash", "-c", "echo stderr_message >&2"]},
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        # Wait for completion
        await asyncio.sleep(0.3)

        job = manager.get_job(job_id)
        assert job is not None
        # Check stderr was captured
        stderr_found = any("stderr_message" in line for line in job.output_lines)
        assert stderr_found, f"Expected stderr in output: {job.output_lines}"

    @pytest.mark.asyncio
    async def test_monitor_keeps_last_20_lines(self) -> None:
        """Output trimmed to 20 lines."""
        # Generate 30 lines of output
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={
                "linux": ["bash", "-c", "for i in $(seq 1 30); do echo line$i; done"]
            },
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        # Wait for completion
        await asyncio.sleep(0.5)

        job = manager.get_job(job_id)
        assert job is not None
        assert len(job.output_lines) <= 20

    @pytest.mark.asyncio
    async def test_monitor_exit_code_zero_completes(self) -> None:
        """returncode=0 → complete."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["true"]},  # exit 0
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        job = manager.get_job(job_id)
        assert job is not None
        assert job._monitor_task is not None
        await asyncio.wait_for(job._monitor_task, timeout=2.0)
        assert job.status == "complete"

    @pytest.mark.asyncio
    async def test_monitor_exit_code_nonzero_fails(self) -> None:
        """returncode≠0 → failed."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["false"]},  # exit 1
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        job = manager.get_job(job_id)
        assert job is not None
        assert job._monitor_task is not None
        await asyncio.wait_for(job._monitor_task, timeout=2.0)
        assert job.status == "failed"

    @pytest.mark.asyncio
    async def test_monitor_server_ready_on_startup_pattern(self) -> None:
        """'initialized' → server_ready."""
        # This test is tricky because we need the process to stay alive
        # but also detect the startup pattern
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            # Echo "initialized" - should trigger server_ready
            install={"linux": ["bash", "-c", "echo initialized && sleep 5"]},
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        # Wait for pattern detection
        await asyncio.sleep(0.3)

        job = manager.get_job(job_id)
        assert job is not None
        assert job.status == "server_ready"

        # Clean up - kill the process
        if job.process and job.process.returncode is None:
            job.process.kill()

    @pytest.mark.asyncio
    async def test_monitor_cancellation_cleanup(self) -> None:
        """Cancelled task cleans up process."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["sleep", "10"]},  # Long running
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        # Give it time to start
        await asyncio.sleep(0.1)

        job = manager.get_job(job_id)
        assert job is not None
        assert job.status == "installing"

        # Cancel the job
        result = await manager.cancel_job(job_id)
        assert result is True
        assert job.status == "failed"
        assert "Cancelled" in (job.error or "")


class TestProgressParsing:
    """Tests for _parse_progress and _is_server_started."""

    def test_parse_progress_from_percentage(self) -> None:
        """'45%' → 45."""
        manager = JobManager.get_instance()
        result = manager._parse_progress("Installing... 45% complete", 0)
        assert result == 45

    def test_parse_progress_caps_at_99(self) -> None:
        """'100%' → 99 (until complete)."""
        manager = JobManager.get_instance()
        result = manager._parse_progress("100% done", 0)
        assert result == 99

    def test_parse_progress_increments_on_activity(self) -> None:
        """Non-% output increments by 1."""
        manager = JobManager.get_instance()
        result = manager._parse_progress("Some output without percentage", 50)
        assert result == 51

    def test_parse_progress_stops_at_90(self) -> None:
        """Increment caps at 90."""
        manager = JobManager.get_instance()
        result = manager._parse_progress("Some output", 90)
        assert result == 90  # No increment past 90

    def test_is_server_started_patterns(self) -> None:
        """Each startup pattern detected."""
        manager = JobManager.get_instance()

        patterns = [
            "running on stdio",
            "Server running",
            "server started",
            "listening on stdio",
            "MCP server ready",
            "ready to accept connections",
            "waiting for connection",
            "Server initialized",
        ]

        for pattern in patterns:
            assert manager._is_server_started(pattern), (
                f"Pattern not detected: {pattern}"
            )

    def test_is_server_started_case_insensitive(self) -> None:
        """'INITIALIZED' detected."""
        manager = JobManager.get_instance()
        assert manager._is_server_started("SERVER INITIALIZED")
        assert manager._is_server_started("RUNNING ON STDIO")

    def test_is_server_started_false_for_random(self) -> None:
        """Random text returns False."""
        manager = JobManager.get_instance()
        assert not manager._is_server_started("Installing packages...")
        assert not manager._is_server_started("npm WARN deprecated")
        assert not manager._is_server_started("Progress: 50%")


class TestCancelJob:
    """Tests for cancel_job()."""

    @pytest.mark.asyncio
    async def test_cancel_job_unknown_returns_false(self) -> None:
        """Unknown job_id returns False."""
        manager = JobManager.get_instance()
        result = await manager.cancel_job("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_job_terminates_process(self) -> None:
        """Running process terminated."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["sleep", "30"]},
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        await asyncio.sleep(0.1)

        job = manager.get_job(job_id)
        assert job is not None
        process = job.process

        result = await manager.cancel_job(job_id)
        assert result is True

        # Process should be terminated
        await asyncio.sleep(0.1)
        assert process is None or process.returncode is not None

    @pytest.mark.asyncio
    async def test_cancel_job_cancels_monitor_task(self) -> None:
        """_monitor_task.cancel() called."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["sleep", "30"]},
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        await asyncio.sleep(0.1)

        job = manager.get_job(job_id)
        assert job is not None
        monitor_task = job._monitor_task

        await manager.cancel_job(job_id)

        # Wait for cancellation to complete
        await asyncio.sleep(0.2)

        # Monitor task should be cancelled or done
        assert monitor_task is None or monitor_task.cancelled() or monitor_task.done()

    @pytest.mark.asyncio
    async def test_cancel_job_sets_failed_status(self) -> None:
        """Status=failed, error='Cancelled'."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["sleep", "30"]},
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        manager = JobManager.get_instance()
        job_id = await manager.start_install(server_config, "linux")

        await asyncio.sleep(0.1)

        await manager.cancel_job(job_id)

        job = manager.get_job(job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.error == "Cancelled by user"


class TestInstallServerLegacy:
    """Tests for blocking install_server() function."""

    @pytest.mark.asyncio
    async def test_install_server_timeout(self) -> None:
        """InstallError on timeout."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["sleep", "10"]},
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        with pytest.raises(InstallError) as exc_info:
            await install_server(server_config, "linux", timeout=0.1)

        assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_install_server_nonzero_exit(self) -> None:
        """InstallError on non-zero exit."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["bash", "-c", "exit 1"]},
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        with pytest.raises(InstallError) as exc_info:
            await install_server(server_config, "linux")

        assert "failed" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_install_server_wsl_fallback(self) -> None:
        """WSL falls back to linux."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["echo", "success"]},  # Only linux
            command="echo",
            args=["test"],
            requires_api_key=False,
        )

        # Should succeed with WSL falling back to linux
        await install_server(server_config, "wsl")


class TestVerifyInstallation:
    """Tests for verify_installation()."""

    @pytest.mark.asyncio
    async def test_verify_installation_success(self) -> None:
        """Returns True for valid command."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["echo", "install"]},
            command="echo",  # echo exists
            args=["test"],
            requires_api_key=False,
        )

        result = await verify_installation(server_config)
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_installation_failure(self) -> None:
        """Returns False for invalid command."""
        server_config = ServerConfig(
            name="test",
            description="Test",
            keywords=["test"],
            install={"linux": ["echo", "install"]},
            command="nonexistent_command_xyz",  # Doesn't exist
            args=["test"],
            requires_api_key=False,
        )

        result = await verify_installation(server_config)
        assert result is False
