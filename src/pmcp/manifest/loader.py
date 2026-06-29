"""Manifest loader - parse and provide access to manifest.yaml."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

logger = logging.getLogger(__name__)

Platform = Literal["mac", "wsl", "linux", "windows"]
ServerTransport = Literal["local", "remote", "sse", "http", "streamable-http"]

# Private/custom manifest overlay locations (mirrors config.loader's
# DEFAULT_USER_CONFIG_PATHS). The user path is recomputed from Path.home() at
# call time in _overlay_manifest_paths() so HOME monkeypatching works in tests;
# this constant documents the default location.
DEFAULT_USER_MANIFEST_PATHS = [Path.home() / ".pmcp" / "manifest.yaml"]


@dataclass
class CLIAlternative:
    """Configuration for a CLI alternative."""

    name: str
    keywords: list[str]
    check_command: list[str]
    help_command: list[str]
    description: str
    examples: list[str] = field(default_factory=list)
    prefer_mcp_for: list[str] = field(default_factory=list)


@dataclass
class ServerConfig:
    """Configuration for an MCP server in the manifest."""

    name: str
    description: str
    keywords: list[str]
    install: dict[Platform, list[str]]
    command: str
    args: list[str]
    requires_api_key: bool = False
    env_var: str | None = None
    env_instructions: str | None = None
    auto_start: bool = False
    transport: ServerTransport = "local"
    url: str | None = None
    headers: dict[str, str] | None = None
    protected_resource_metadata_url: str | None = None
    authorization_server_metadata_url: str | None = None
    oidc_issuer_url: str | None = None
    oidc_discovery_url: str | None = None
    client_id_metadata_document_url: str | None = None
    declared_scopes: list[str] = field(default_factory=list)
    supports_url_elicitation: bool = False
    package: str | None = None
    server_card_url: str | None = None
    declared_capabilities: list[str] = field(default_factory=list)
    discovery_diagnostics: list[str] = field(default_factory=list)
    raw_discovery_metadata: dict[str, Any] = field(default_factory=dict)
    status: str | None = None
    source: str | None = None
    replacement: str | None = None


# Category taxonomy used by Manifest.get_category_summary() and get_servers_in_category()
_CATEGORY_MAP: dict[str, list[str]] = {
    "browser automation": [
        "playwright",
        "puppeteer",
        "browserbase",
        "browser-use",
        "chrome-devtools",
        "hyperbrowser",
    ],
    "scraping/search": [
        "brightdata",
        "brave-search",
        "exa",
        "fetch",
        "firecrawl",
        "tavily",
        "perplexity",
        "apify",
        "jina",
    ],
    "APIs": [
        "github",
        "gitlab",
        "slack",
        "notion",
        "linear",
        "discord",
        "google-maps",
        "mapbox",
        "coinmarketcap",
    ],
    "databases": [
        "postgres",
        "sqlite",
        "supabase",
        "qdrant",
        "mysql",
        "mongodb",
        "clickhouse",
        "neo4j",
        "meilisearch",
    ],
    "developer tools": [
        "context7",
        "git",
        "sequential-thinking",
        "sentry",
        "postman",
        "eslint",
        "index-it-mcp",
        "circleci",
        "argocd",
        "kubernetes",
    ],
    "cloud/storage": [
        "google-drive",
        "filesystem",
        "aws-kb-retrieval",
        "memory",
        "cloudflare",
        "heroku",
        "railway",
        "neon",
        "azure",
    ],
    "CRM & sales": ["hubspot", "salesforce"],
    "payments": ["stripe", "xero", "paypal"],
    "project management": [
        "jira",
        "confluence",
        "asana",
        "clickup",
        "todoist",
        "plane",
    ],
    "design & media": ["figma", "miro", "excalidraw", "mux", "elevenlabs"],
    "monitoring": ["datadog", "grafana", "dynatrace", "langfuse"],
    "CMS & content": [
        "airtable",
        "contentful",
        "webflow",
        "wordpress",
        "obsidian",
    ],
    "communication": ["twilio", "mailgun", "line"],
}


@dataclass
class Manifest:
    """Parsed manifest with CLI alternatives and MCP servers."""

    version: str
    cli_alternatives: dict[str, CLIAlternative]
    servers: dict[str, ServerConfig]
    discovery_queue_path: str

    def get_auto_start_servers(self) -> list[ServerConfig]:
        """Get servers configured for auto-start."""
        return [s for s in self.servers.values() if s.auto_start]

    def get_server(self, name: str) -> ServerConfig | None:
        """Get server config by name."""
        return self.servers.get(name)

    def get_cli(self, name: str) -> CLIAlternative | None:
        """Get CLI alternative by name."""
        return self.cli_alternatives.get(name)

    def get_category_summary(self) -> str:
        """Return compact category summary of provisionable servers."""
        total = len(self.servers)
        if not total:
            return ""

        parts = []
        for cat_name, server_names in _CATEGORY_MAP.items():
            matched = [n for n in server_names if n in self.servers]
            if not matched:
                continue
            if len(matched) <= 2:
                names_str = ", ".join(matched)
            else:
                names_str = ", ".join(matched[:2]) + f" +{len(matched) - 2}"
            parts.append(f"{cat_name} ({names_str})")

        if not parts:
            return ""
        return f"Provisionable ({total} servers): {'; '.join(parts)}"

    def get_servers_in_category(
        self, query: str
    ) -> tuple[str, list[ServerConfig]] | None:
        """Find the best-matching category for a query and return its servers.

        Uses category-span IDF discounting: keywords that appear across many
        distinct categories (e.g. "api" spans communication, APIs, developer tools)
        get lower weight than category-specific terms (e.g. "dns" only in
        cloud/storage). A minimum score of 0.5 is required to prevent spurious
        matches from generic-only keyword overlap.

        Returns (category_name, [ServerConfig, ...]) for the best-scoring category
        above the threshold, or None if no category qualifies.
        """
        query_lower = query.lower()
        query_words = set(query_lower.replace("-", " ").replace("_", " ").split())

        # Build keyword → set-of-categories map for IDF discounting.
        # A keyword that appears in servers across many different categories is
        # considered generic; one confined to a single category is specific.
        kw_cats: dict[str, set[str]] = {}
        for cat_name, server_names in _CATEGORY_MAP.items():
            for sname in server_names:
                server = self.servers.get(sname)
                if not server:
                    continue
                for kw in server.keywords:
                    kw_norm = kw.lower().replace("-", " ").replace("_", " ")
                    kw_cats.setdefault(kw_norm, set()).add(cat_name)

        def _kw_weight(kw_norm: str) -> float:
            n_cats = len(kw_cats.get(kw_norm, set()))
            if n_cats >= 4:
                return 0.1  # Appears across 4+ categories → very generic
            if n_cats == 3:
                return 0.3  # Spans three categories → somewhat generic
            if n_cats == 2:
                return 0.7  # Two-category overlap — still useful signal
            return 1.0  # Confined to one category → highly specific

        best_cat: str | None = None
        best_score: float = 0.0

        for cat_name, server_names in _CATEGORY_MAP.items():
            score: float = 0.0

            # Score: category name words that appear in query (strong signal, ×2)
            cat_words = set(cat_name.lower().replace("/", " ").split())
            score += len(cat_words & query_words) * 2.0

            # Score: keyword hits across servers in this category, category-span weighted
            for sname in server_names:
                server = self.servers.get(sname)
                if not server:
                    continue
                for kw in server.keywords:
                    kw_norm = kw.lower().replace("-", " ").replace("_", " ")
                    if set(kw_norm.split()).issubset(query_words):
                        score += _kw_weight(kw_norm)

            if score > best_score:
                best_score = score
                best_cat = cat_name

        # Minimum score prevents spurious matches from generic-keyword-only overlap.
        # 0.5 requires at least one moderately-specific keyword or a category-name hit.
        _MIN_SCORE = 0.5
        if not best_cat or best_score < _MIN_SCORE:
            return None

        servers = [
            self.servers[n] for n in _CATEGORY_MAP[best_cat] if n in self.servers
        ]
        return (best_cat, servers)

    def search_by_keyword(
        self, keyword: str
    ) -> tuple[list[CLIAlternative], list[ServerConfig]]:
        """Search CLIs and servers by keyword."""
        keyword_lower = keyword.lower()

        matching_clis = [
            cli
            for cli in self.cli_alternatives.values()
            if any(keyword_lower in kw.lower() for kw in cli.keywords)
        ]

        matching_servers = [
            server
            for server in self.servers.values()
            if any(keyword_lower in kw.lower() for kw in server.keywords)
        ]

        return matching_clis, matching_servers


def _parse_cli_alternative(name: str, data: dict[str, Any]) -> CLIAlternative:
    """Parse a CLI alternative from raw YAML data."""
    return CLIAlternative(
        name=name,
        keywords=data.get("keywords", []),
        check_command=data.get("check_command", [name, "--version"]),
        help_command=data.get("help_command", [name, "--help"]),
        description=data.get("description", ""),
        examples=data.get("examples", []),
        prefer_mcp_for=data.get("prefer_mcp_for", []),
    )


def _parse_server_config(name: str, data: dict[str, Any]) -> ServerConfig:
    """Parse a server config from raw YAML data."""
    install_data = data.get("install", {})
    install: dict[Platform, list[str]] = {}

    for platform in ["mac", "wsl", "linux", "windows"]:
        if platform in install_data:
            install[platform] = install_data[platform]  # type: ignore

    transport = cast(
        ServerTransport,
        data.get("transport", "streamable-http" if data.get("url") else "local"),
    )

    raw_discovery_metadata = data.get("discovery_metadata", {})
    if not isinstance(raw_discovery_metadata, dict):
        raw_discovery_metadata = {}
    discovery_diagnostics = data.get("discovery_diagnostics", [])
    if not isinstance(discovery_diagnostics, list):
        discovery_diagnostics = ["invalid_discovery_diagnostics"]

    return ServerConfig(
        name=name,
        description=data.get("description", ""),
        keywords=data.get("keywords", []),
        install=install,
        command=data.get("command", ""),
        args=data.get("args", []),
        requires_api_key=data.get("requires_api_key", False),
        env_var=data.get("env_var"),
        env_instructions=data.get("env_instructions"),
        auto_start=data.get("auto_start", False),
        transport=transport,
        url=data.get("url"),
        headers=data.get("headers"),
        protected_resource_metadata_url=data.get("protected_resource_metadata_url"),
        authorization_server_metadata_url=data.get("authorization_server_metadata_url"),
        oidc_issuer_url=data.get("oidc_issuer_url"),
        oidc_discovery_url=data.get("oidc_discovery_url"),
        client_id_metadata_document_url=data.get("client_id_metadata_document_url"),
        declared_scopes=data.get("declared_scopes", []),
        supports_url_elicitation=data.get("supports_url_elicitation", False),
        package=data.get("package"),
        server_card_url=data.get("server_card_url"),
        declared_capabilities=data.get("declared_capabilities", []),
        discovery_diagnostics=discovery_diagnostics,
        raw_discovery_metadata=raw_discovery_metadata,
        status=data.get("status"),
        source=data.get("source"),
        replacement=data.get("replacement"),
    )


def _find_project_manifest() -> Path | None:
    """Walk up from cwd for the nearest ancestor containing .pmcp/manifest.yaml.

    Replicates config.loader.find_project_root's marker-based walk locally to
    avoid a circular import (config/loader imports load_manifest). Stops at the
    filesystem root and at the temp directory so test fixtures under tempdir do
    not accidentally pick up an unrelated overlay.
    """
    try:
        current = Path.cwd().resolve()
    except OSError:
        return None
    temp_root = Path(tempfile.gettempdir()).resolve()

    while current != current.parent:
        if current == temp_root:
            return None
        candidate = current / ".pmcp" / "manifest.yaml"
        if candidate.exists():
            return candidate
        current = current.parent

    return None


def _overlay_manifest_paths() -> list[tuple[str, Path]]:
    """Return existing overlay manifest paths in precedence order (low → high).

    Order: user (``~/.pmcp/manifest.yaml``), then project
    (``<project>/.pmcp/manifest.yaml``), then ``$PMCP_MANIFEST_PATH``. Later
    entries override earlier ones, so the env path wins over project, which wins
    over user, which wins over the shipped manifest. Only files that exist are
    returned. The user path is computed from ``Path.home()`` here (not the frozen
    module constant) so HOME isolation in tests takes effect.
    """
    paths: list[tuple[str, Path]] = []

    user_path = Path.home() / ".pmcp" / "manifest.yaml"
    if user_path.exists():
        paths.append(("user", user_path))

    project_path = _find_project_manifest()
    if project_path is not None:
        paths.append(("project", project_path))

    env_value = os.environ.get("PMCP_MANIFEST_PATH")
    if env_value:
        env_path = Path(env_value).expanduser()
        if env_path.exists():
            paths.append(("env", env_path))

    return paths


def _load_overlay_file(
    path: Path,
) -> tuple[dict[str, ServerConfig], dict[str, CLIAlternative]]:
    """Parse an overlay manifest file, fail-soft.

    Returns ``(servers, cli_alternatives)`` parsed from ``path``. A missing file,
    OSError, YAML error, or non-mapping top-level document logs a warning naming
    the file and returns empty dicts. Each server/CLI entry is parsed in its own
    try/except so one malformed entry is skipped without dropping its siblings.
    """
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
        return {}, {}

    if not isinstance(data, dict):
        logger.warning(
            f"Skipping manifest overlay {path}: top-level document is not a mapping"
        )
        return {}, {}

    servers: dict[str, ServerConfig] = {}
    raw_servers = data.get("servers", {})
    if isinstance(raw_servers, dict):
        for name, server_data in raw_servers.items():
            try:
                servers[name] = _parse_server_config(name, server_data)
            except Exception as exc:
                logger.warning(
                    f"Skipping invalid server entry '{name}' in overlay {path}: {exc}"
                )
    elif raw_servers:
        logger.warning(f"Skipping 'servers' in overlay {path}: not a mapping")

    cli_alternatives: dict[str, CLIAlternative] = {}
    raw_clis = data.get("cli_alternatives", {})
    if isinstance(raw_clis, dict):
        for name, cli_data in raw_clis.items():
            try:
                cli_alternatives[name] = _parse_cli_alternative(name, cli_data)
            except Exception as exc:
                logger.warning(
                    f"Skipping invalid cli_alternative '{name}' in overlay "
                    f"{path}: {exc}"
                )
    elif raw_clis:
        logger.warning(f"Skipping 'cli_alternatives' in overlay {path}: not a mapping")

    return servers, cli_alternatives


def load_manifest(manifest_path: Path | None = None) -> Manifest:
    """Load and parse the manifest.yaml file.

    When called with no ``manifest_path`` (all internal callers), private/custom
    overlay manifests are merged over the shipped manifest: user
    (``~/.pmcp/manifest.yaml``) < project (``<project>/.pmcp/manifest.yaml``) <
    ``$PMCP_MANIFEST_PATH``, each overriding the shipped entry of the same name
    (whole-entry replace). An explicit ``manifest_path`` loads only that file
    and applies no overlays. Overlay parsing is fail-soft and never raises.
    """
    apply_overlays = manifest_path is None
    if manifest_path is None:
        # Default to manifest.yaml in the same directory as this module
        manifest_path = Path(__file__).parent / "manifest.yaml"

    logger.info(f"Loading manifest from {manifest_path}")

    with open(manifest_path, "r") as f:
        data = yaml.safe_load(f)

    # Parse CLI alternatives
    cli_alternatives: dict[str, CLIAlternative] = {}
    for name, cli_data in data.get("cli_alternatives", {}).items():
        cli_alternatives[name] = _parse_cli_alternative(name, cli_data)

    # Parse servers
    servers: dict[str, ServerConfig] = {}
    for name, server_data in data.get("servers", {}).items():
        servers[name] = _parse_server_config(name, server_data)

    # Merge private/custom overlays over the shipped manifest (default path only).
    if apply_overlays:
        for label, overlay_path in _overlay_manifest_paths():
            overlay_servers, overlay_clis = _load_overlay_file(overlay_path)
            if overlay_servers or overlay_clis:
                logger.info(
                    f"Applying manifest overlay ({label}) from {overlay_path}: "
                    f"{len(overlay_servers)} servers, "
                    f"{len(overlay_clis)} CLI alternatives"
                )
            servers.update(overlay_servers)
            cli_alternatives.update(overlay_clis)

    manifest = Manifest(
        version=data.get("version", "1.0"),
        cli_alternatives=cli_alternatives,
        servers=servers,
        discovery_queue_path=data.get(
            "discovery_queue_path", ".mcp-gateway/discovery_queue.json"
        ),
    )

    logger.info(
        f"Loaded manifest: {len(cli_alternatives)} CLI alternatives, "
        f"{len(servers)} servers ({len(manifest.get_auto_start_servers())} auto-start)"
    )

    return manifest
