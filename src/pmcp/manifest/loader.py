"""Manifest loader - parse and provide access to manifest.yaml."""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import pickle
import re
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from pmcp.project_consent import log_refusal, read_and_gate
from pmcp.validation import (
    NPM_FILE_TYPE_RE,
    is_valid_package_version,
    parse_package_spec,
)

logger = logging.getLogger(__name__)


_SHIPPED_MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"


def _trusted_yaml_loader() -> Any:
    """libyaml's ``CSafeLoader`` when PyYAML was built with it, else ``SafeLoader``.

    Both use the same ``SafeConstructor`` and ``Resolver``; only the scanner and
    parser differ, so pmcp's own shipped manifest yields the same data either way
    (a test pins that). Overlays are operator- and repository-supplied and keep
    the pure-Python ``SafeLoader``: the two parsers do disagree on some input (a
    tab after ``key:``, deep nesting), and a performance change must not change
    which overlays are accepted (Consiliency/pmcp#233).
    """
    return getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader


def _parse_trusted_yaml(content: bytes) -> Any:
    """Parse pmcp's OWN shipped manifest bytes, with libyaml when available."""
    return yaml.load(content, Loader=_trusted_yaml_loader())  # noqa: S506 - safe loaders only


# The parsed shipped document, by sha256 of its bytes: a pure function of the
# bytes, so it is never stale and is not reset between tests. Pickled, so each
# reader gets its own copy of the raw data.
_trusted_document: tuple[bytes, bytes] | None = None


def _parse_trusted_document(content: bytes) -> Any:
    """``_parse_trusted_yaml(content)``, parsed once per distinct ``content``."""
    global _trusted_document
    digest = hashlib.sha256(content).digest()
    cached = _trusted_document
    if cached is not None and cached[0] == digest:
        data = _deserialize(cached[1])
        if data is not None:
            return data
    data = _parse_trusted_yaml(content)
    blob = _serialize(data)
    if blob is not None:
        _trusted_document = (digest, blob)
    return data


@functools.lru_cache(maxsize=1)
def _shipped_manifest_entries() -> dict[str, dict[str, Any]]:
    """pmcp's OWN shipped ``manifest.yaml``, read directly.

    Never through ``load_manifest``, which applies overlays: no user, project
    or ``$PMCP_MANIFEST_PATH`` overlay can add a name or a declared key here.
    The file ships with pmcp and does not change under a running process.
    """
    path = _SHIPPED_MANIFEST_PATH
    try:
        servers = (_parse_trusted_yaml(path.read_bytes()) or {}).get("servers") or {}
    except (OSError, yaml.YAMLError, AttributeError):
        return {}
    return {
        name: entry
        for name, entry in servers.items()
        if isinstance(name, str) and isinstance(entry, dict)
    }


def _server_label(name: object) -> str:
    """How a version-pin log line names a server (Consiliency/pmcp#294 piece 1).

    A name pmcp's SHIPPED manifest defines is shown; any other name is an
    overlay's own key, which could be anything an operator pasted (a token),
    so it is never shown. No grammar decides this: only the shipped list does.
    """
    if isinstance(name, str) and name in _shipped_manifest_entries():
        return f"server '{name}'"
    return "an overlay server (name not shown)"


def _shipped_declared_keys(name: str) -> frozenset[str]:
    """The env keys pmcp's SHIPPED manifest declares for *name*: its
    ``env_var`` and its ``api_key_optional_when`` relaxers. An overlay's
    declarations exempt nothing."""
    entry = _shipped_manifest_entries().get(name) or {}
    keys: set[str] = set()
    if isinstance(entry.get("env_var"), str):
        keys.add(entry["env_var"])
    relaxers = entry.get("api_key_optional_when") or []
    if isinstance(relaxers, list):
        keys.update(k for k in relaxers if isinstance(k, str))
    return frozenset(keys)


# Env keys an entry may put in the child's environment without changing what
# npm fetches or runs (an ALLOWLIST: every other key -- `npm_config_package`,
# `npm_config_registry`, `NODE_OPTIONS`, `NODE_PATH`, `PATH`, `HOME`,
# `PREFIX`, `XDG_*`, `nvm_*`, a proxy, a CA file, an unknown name -- may).
# Locale, terminal and colour keys change how output looks, never what runs.
_NPM_INERT_ENV_KEYS = frozenset(
    {"LANG", "LANGUAGE", "TERM", "TZ", "NO_COLOR", "FORCE_COLOR"}
)
_NPM_INERT_ENV_PREFIXES = ("LC_",)
# npm config keys (as npm reads them from `npm_config_*`, any case) that change
# output, logging, network timing or the install prompt, never which package
# or version runs. `cache` is NOT here: npx runs a cached package on its
# package.json's word (Consiliency/pmcp#295 board, round 5).
_NPM_CONFIG_KEYS_THAT_CANNOT_REDIRECT = frozenset(
    {
        "loglevel",
        "color",
        "progress",
        "timing",
        "unicode",
        "logs-dir",
        "logs-max",
        "fund",
        "audit",
        "update-notifier",
        "yes",
        "fetch-retries",
        "fetch-retry-factor",
        "fetch-retry-maxtimeout",
        "fetch-retry-mintimeout",
        "fetch-timeout",
        "maxsockets",
    }
)


def _npm_config_key(env_key: str) -> str:
    """The config key npm reads from an ``npm_config_*`` variable: npm's own
    ``loadEnv`` rule (``@npmcli/config``, npm 10 and 11) -- strip the prefix
    case-insensitively; unless the rest starts with ``//``, replace every ``_``
    except a leading one with ``-`` and lowercase."""
    key = env_key[len("npm_config_") :]
    if key.startswith("//"):
        return key
    return (key[:1] + key[1:].replace("_", "-")).lower()


def npm_env_may_redirect(server_name: str, keys: Iterable[str]) -> bool:
    """Could an entry-supplied env key change what npm runs for *server_name*?

    True unless EVERY key is proven inert: declared by pmcp's shipped manifest
    for this server, a locale/terminal key, or an npm logging/timing config
    key. Values are not consulted: an empty value of a non-inert key still
    changes npm's reading (an empty ``PATH`` searches the cwd).
    """
    declared = _shipped_declared_keys(server_name)
    for key in keys:
        key = str(key)
        if key in declared:
            continue
        upper = key.upper()
        if upper in _NPM_INERT_ENV_KEYS or upper.startswith(_NPM_INERT_ENV_PREFIXES):
            continue
        if key.lower().startswith("npm_config_") and (
            _npm_config_key(key) in _NPM_CONFIG_KEYS_THAT_CANNOT_REDIRECT
        ):
            continue
        return True
    return False


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
    # Env-store key under which this server's credential is persisted. Defaults
    # to env_var when unset. Declare a namespaced value (e.g. BRIGHTDATA_API_TOKEN)
    # for servers whose runtime env_var is a generic name like API_TOKEN, so two
    # servers sharing that runtime name do not collide in the flat secret store.
    secret_key: str | None = None
    env_instructions: str | None = None
    # Non-secret environment variables passed to the server process alongside its
    # credential — typically a base URL selecting a self-hosted deployment (e.g.
    # FIRECRAWL_API_URL). Secrets belong in env_var/secret_key, which are resolved
    # from the env store and always win over a colliding extra_env key.
    extra_env: dict[str, str] = field(default_factory=dict)
    # Names of extra_env variables whose presence (with a usable value) relaxes
    # requires_api_key for this server — e.g. a self-hosted base URL that makes
    # the vendor credential unnecessary. See credential_requirement() below for
    # the full contract (Consiliency/pmcp#114). Empty by default, which is
    # today's behaviour byte-for-byte: requires_api_key alone still gates.
    api_key_optional_when: list[str] = field(default_factory=list)
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
    # The exact client version this entry runs (Consiliency/pmcp#294). Set by
    # `version:` on an entry or by an overlay's `server_version:` patch, and
    # materialised by `load_manifest` into the npx package slot of `args` and
    # of every `install` argv. ``None`` means unpinned -- including a pin that
    # was refused, so "version is set" always means "the argv is pinned".
    version: str | None = None


def credential_storage_key(server: Any) -> str | None:
    """Env-store key under which *server*'s credential is persisted.

    Uses the namespaced ``secret_key`` when the server declares one, otherwise
    the runtime ``env_var``. Accepts any object exposing those attributes
    (manifest ``ServerConfig`` or a discovered-server config), returning
    ``None`` when neither is set.
    """
    if server is None:
        return None
    return getattr(server, "secret_key", None) or getattr(server, "env_var", None)


def credential_lookup_keys(server: Any) -> list[str]:
    """Ordered secret-store keys to try when resolving *server*'s credential.

    The namespaced storage key comes first, followed by the runtime ``env_var``
    as a backward-compatible fallback so installs that stored the credential
    under the legacy (un-namespaced) key keep working after an upgrade.
    """
    if server is None:
        return []
    keys: list[str] = []
    for key in (
        getattr(server, "secret_key", None),
        getattr(server, "env_var", None),
    ):
        if key and key not in keys:
            keys.append(key)
    return keys


_PLACEHOLDER_RE = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")


def is_usable_credential_value(value: Any) -> bool:
    """True when *value* is a real, expanded value — not empty and not an
    unexpanded ``${VAR}``/``$VAR`` placeholder token.

    Local stdio env is passed to child processes verbatim, with no shell
    expansion (see config/loader.py's env-merge comments), so a literal
    ``${FIRECRAWL_API_URL}`` reaching the child is a dead string, not a real
    URL, and must not relax a credential gate. Public (not underscore-
    prefixed) because it is also used to recognize a concrete credential
    literal placed directly in a configured entry's own env block
    (Consiliency/pmcp#114 board review finding 2) — the same "is this a real
    value" rule applies to both a relaxer and a credential.
    """
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if _PLACEHOLDER_RE.match(stripped):
        return False
    return True


@dataclass(frozen=True)
class CredentialRequirement:
    """Result of evaluating whether *server* still needs a credential.

    Frozen per IF-0-P5-1 (plans/phase-plan-v11-P5.md) — every field name and
    meaning below is depended on unchanged by all seven gate consumers.
    """

    required: bool  # effective requirement after relaxation
    declared: bool  # server.requires_api_key as shipped
    relaxed_by: str | None  # extra_env var name that relaxed it, else None


def credential_requirement(
    server: Any, *, child_env: Mapping[str, str] | None = None
) -> CredentialRequirement:
    """Evaluate the effective credential requirement for *server*.

    ``server`` is duck-typed over ``requires_api_key``, ``env_var``,
    ``secret_key``, ``extra_env``, and ``api_key_optional_when`` — this accepts
    manifest ``ServerConfig``, discovered-server configs, and ``None`` (treated
    as not-required).

    This function **never reads ``os.environ``**, the secret store, per-request
    arguments, or a URL's shape — see the env-strip inversion analysis in
    plans/phase-plan-v11-P5.md. ``sanitized_subprocess_env`` strips managed
    secret keys from ``os.environ`` before a child process is spawned, so a
    predicate reading ``os.environ`` could relax a gate for a variable the
    child never actually receives — silently redirecting a "self-hosted"
    server to the vendor endpoint with no credential. Restricting the source to
    ``extra_env`` (and the caller-supplied ``child_env``, which must itself
    trace back to ``extra_env``, never to ``os.environ``) keeps "the gate
    relaxed" and "the child receives the variable" a structural invariant.

    ``child_env``, when given, must be the post-merge environment the child
    process will actually receive (e.g. ``ResolvedServerConfig.config.env``
    after ``_merge_manifest_defaults``) — **never** ``os.environ`` or a copy of
    it. Passing ``os.environ`` is a contract violation (guarded against in
    ``tests/test_credential_predicate_guard.py``). When ``child_env`` is
    ``None``, the source is ``server.extra_env``, which is correct for every
    manifest-only call site because the two are identical by construction
    there.

    A server with a ``url`` (remote/HTTP transport) is never relaxed, even if
    ``api_key_optional_when`` names a variable present in ``extra_env``.
    ``extra_env`` is carried to a spawned local subprocess's environment; a
    remote connection has no such subprocess and authenticates via headers,
    so a relaxer set there can never actually reach the connection. Without
    this, a remote entry with a declared relaxer would be classified
    not-required and connected to the vendor URL with no Authorization
    header (Consiliency/pmcp#114 board review finding 2).
    """
    declared = bool(getattr(server, "requires_api_key", False)) if server else False
    if not declared:
        return CredentialRequirement(required=False, declared=False, relaxed_by=None)

    if getattr(server, "url", None):
        return CredentialRequirement(required=True, declared=True, relaxed_by=None)

    own_env_var = getattr(server, "env_var", None)
    own_secret_key = getattr(server, "secret_key", None)
    optional_when = getattr(server, "api_key_optional_when", None) or []

    if child_env is not None:
        source: Mapping[str, str] = child_env
    else:
        source = getattr(server, "extra_env", None) or {}

    for candidate in optional_when:
        if candidate == own_env_var or candidate == own_secret_key:
            # Self-relaxation is impossible — also enforced at parse time, but
            # a duck-typed non-ServerConfig object may not have gone through
            # that parse step.
            continue
        value = source.get(candidate) if hasattr(source, "get") else None
        if is_usable_credential_value(value):
            return CredentialRequirement(
                required=False, declared=True, relaxed_by=candidate
            )

    return CredentialRequirement(required=True, declared=True, relaxed_by=None)


def requires_credential(
    server: Any, *, child_env: Mapping[str, str] | None = None
) -> bool:
    """Convenience wrapper: ``credential_requirement(server, ...).required``."""
    return credential_requirement(server, child_env=child_env).required


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


def _parse_extra_env(
    name: str, raw: Any, field_label: str = "extra_env"
) -> dict[str, str]:
    """Parse an ``extra_env``-shaped mapping, fail-soft.

    Non-mappings and unusable entries are dropped with a warning rather than
    raising, so one bad key never costs the whole server entry. YAML scalars are
    coerced to ``str`` because values such as a port or a boolean flag are
    naturally written unquoted.

    ``field_label`` names the source field in warnings, so a bad overlay patch
    reports ``server_env`` rather than the shared ``extra_env`` shape it reuses.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        logger.warning(f"Ignoring '{field_label}' for server '{name}': not a mapping")
        return {}

    parsed: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            logger.warning(
                f"Skipping non-string '{field_label}' key for server '{name}'"
            )
            continue
        if isinstance(value, bool):
            parsed[key] = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            parsed[key] = str(value)
        else:
            logger.warning(
                f"Skipping '{field_label}' key '{key}' for server '{name}': "
                f"unsupported value type {type(value).__name__}"
            )
    return parsed


def _parse_api_key_optional_when(
    name: str, raw: Any, env_var: str | None, secret_key: str | None
) -> list[str]:
    """Parse the ``api_key_optional_when`` list, fail-soft and fail-closed.

    Absent key -> ``[]`` (today's behaviour unchanged). A non-list value drops
    to ``[]`` with a warning. Non-string members are dropped. A member equal to
    the server's own ``env_var`` or ``secret_key`` is dropped with a warning —
    self-relaxation is impossible by construction (a server cannot declare its
    own credential as the thing that makes the credential unnecessary).
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning(
            f"Ignoring 'api_key_optional_when' for server '{name}': not a list"
        )
        return []

    parsed: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            logger.warning(
                f"Skipping non-string 'api_key_optional_when' entry for server '{name}'"
            )
            continue
        if item == env_var or item == secret_key:
            logger.warning(
                f"Server '{name}' names its own credential variable "
                f"('{item}') in 'api_key_optional_when'; ignoring — a "
                f"credential cannot relax itself"
            )
            continue
        parsed.append(item)
    return parsed


def _parse_version_pin(name: str, raw: Any, field_label: str) -> str | None:
    """Parse a client version pin, fail-soft and fail-closed.

    One exact SemVer version (``is_valid_package_version``, the grammar the
    provision gate already requires of an argv pin) or nothing. Refused, with
    a warning: a range (``^3.25.5``, ``3.x``) and a dist-tag (``latest``) --
    both re-resolve at every ``npx -y`` spawn, which is the drift a pin exists
    to stop -- a ``v`` prefix, a YAML number, and anything carrying a package
    name, whitespace or a flag. The value never names a package: the name is
    always taken from the entry's own argv (see `_materialize_version_pin`).
    """
    if raw is None:
        return None
    # SemVer build metadata (the `+...` segment) is refused too: npm ignores it
    # when resolving, so `3.25.5+x` would run 3.25.5 while every report echoed
    # a label that names nothing (Consiliency/pmcp#295 board, N2).
    if isinstance(raw, str) and is_valid_package_version(raw) and "+" not in raw:
        return raw
    # What happens to an EARLIER pin depends on the field, never on a value
    # (Consiliency/pmcp#322). The merge order decides it: sources apply in
    # order (shipped, user, project, PMCP_MANIFEST_PATH); within one source
    # `servers:` entries replace whole entries first, then `server_version`
    # patches apply; every pin is materialised once, after the last source.
    # So an overlay's `server_version` leaves an earlier pin in place unless a
    # `servers:` entry in this or a later source replaced the entry, or a
    # later source set another pin; a `version:` rides on a whole `servers:`
    # entry, which replaced the earlier entry and any pin with it, so only
    # this source's `server_version` or a later source can pin it again.
    if field_label == "server_version":
        consequence = (
            "any pin from an earlier source stands, unless a 'servers:' entry "
            "for this server in this or a later source replaced it, or a later "
            "source set another pin"
        )
    else:
        consequence = (
            "the whole entry that carries it replaced any earlier pin, so the "
            "server is unpinned unless this source's 'server_version' or a "
            "later source pins it"
        )
    logger.warning(
        f"Ignoring a '{field_label}' pin for {_server_label(name)}: a version "
        'pin must be one exact version such as "3.25.5" -- not a range, a '
        'dist-tag such as "latest", build metadata (+...), a name npm reads as '
        "a local tarball (.tgz/.tar/.tar.gz), or a package spec. This pin is "
        f"ignored (the value is not logged); {consequence}"
    )
    return None


# npm-package-arg's classification, restated as the ALLOWLIST a pin may
# rewrite (Consiliency/pmcp#295 board rounds 1-2). npa decides a spec's class
# in a fixed order, and only its final branch, `fromRegistry`, fetches `name`
# from the registry; every earlier branch (URL, git, alias, file, directory,
# hosted git) names something else. The plan's class table maps each branch to
# the clause below that refuses it.
#
# npa `isFileType`, as `validation.NPM_FILE_TYPE_RE` defines it (reused, never
# restated here): a selector -- or an UNSCOPED bare name -- matching it is a
# tarball FILE to npm. npa checks it BEFORE the registry branch, so before
# "is this a version" too: `1.0.0-x.tgz` is valid SemVer and still a file,
# which is why the selector is tested before `is_valid_package_version`
# (itself tarball-aware) is consulted (round 3, B1').
_NPM_FILE_TYPE_RE = NPM_FILE_TYPE_RE
# validate-npm-package-name's exclusionList, compared case-insensitively as it
# does: npa refuses these as names (round 3, N-c).
_NPM_EXCLUDED_NAMES = frozenset({"node_modules", "favicon.ico"})
# A dist-tag: npa's registry branch accepts any encodeURIComponent-safe word
# that is neither a version nor a range; this is the letter-led subset of it
# (fullmatch, so no trailing newline).
_TAG_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
# A letter-led word npm may read as a VERSION or RANGE, not a tag: semver's
# loose grammar allows ANY run of leading `v`/`=` (`[v=\s]*`), then a partial
# version whose parts are numbers or x/X/* wildcards, then a loose prerelease
# with or without a hyphen (`x`, `X.x`, `v1`, `vv1.2.3`, `vvX`, `v1.X.xbeta`).
# Refused like every range: rewriting it would be harmless to package identity
# (still `fromRegistry`), but a range is not the version-or-tag class a pin
# replaces. Deliberately broad -- any tail -- so a real tag it also matches
# (`xyz`) only loses its pin, with a warning (round 3, N-a).
_PARTIAL_VERSION_WORD_RE = re.compile(
    r"[vV=]*(?:[0-9]+|[xX*])(?:\.(?:[0-9]+|[xX*])){0,2}.*"
)


def split_plain_registry_spec(arg: str) -> tuple[str, str | None] | None:
    """``(name, selector)`` if npm would fetch *arg* from the registry as
    ``name`` at an exact version or a dist-tag; else ``None``.

    Accepted: ``name`` (npa: registry range ``*``), ``name@<exact SemVer>``
    (npa: ``version``) and ``name@<dist-tag>`` (npa: ``tag``). Refused, by
    class: anything ``parse_package_spec`` rejects (URLs, aliases, git, paths,
    flags: their name half is not a package name, or they have no name); an
    unscoped name ending in a tarball suffix (npa: ``file``); a selector that
    is not an exact version or a tag word (``:`` / ``/`` / ``~`` / ``.``-led
    forms, i.e. alias, git, URL, file, directory, and every range); a tag word
    ending in a tarball suffix (npa: ``file``); and a tag word that npm reads as
    a version or range (``x``, ``v1``, ``vvX``); a SemVer-shaped selector that
    ends in a tarball suffix (npa: ``file``, checked first); and the names
    npm excludes (``node_modules``, ``favicon.ico``). Pure grammar: it never asks the
    resolver, so it works while npm package identity is disabled.
    """
    try:
        name, selector = parse_package_spec(arg)
    except (ValueError, TypeError, AttributeError):
        return None
    if not name.startswith("@") and _NPM_FILE_TYPE_RE.search(name):
        return None
    if name.lower() in _NPM_EXCLUDED_NAMES:
        return None
    if selector is None:
        return name, None
    # npa's order: `isFileType` on the selector BEFORE any registry reading, so
    # a SemVer-shaped tarball (`3.25.5-corp.tgz`) is a file, not a version.
    if _NPM_FILE_TYPE_RE.search(selector):
        return None
    if is_valid_package_version(selector):
        return name, selector
    if _TAG_WORD_RE.fullmatch(selector) and not _PARTIAL_VERSION_WORD_RE.fullmatch(
        selector
    ):
        return name, selector
    return None


def _pin_npx_args(args: list[str], version: str) -> tuple[list[str], str] | None:
    """*args* with the npx package slot pinned to *version*, and the name.

    The slot is the provision gate's (`provision_gate._package_slot`): the
    first argument that is not an allowlisted leading flag. It must be a plain
    registry spec (`split_plain_registry_spec`); its NAME is kept and only its
    version suffix is replaced, so a pin can select a version of the package
    the entry already runs and never a different one. ``None`` otherwise:
    ``myalias@npm:firecrawl-mcp@3.25.5`` -> ``myalias@3.25.5`` would be the
    registry package ``myalias`` (codex P1), and
    ``firecrawl-mcp@corp-mcp.TGZ`` -> ``firecrawl-mcp@3.25.5`` would turn a
    local tarball into a public-registry fetch (round-2 B1).
    """
    # Local import: provision_gate is a consumer of this module's ServerConfig.
    from pmcp.provision_gate import _NPX_LEADING_FLAGS

    for index, arg in enumerate(args):
        if arg in _NPX_LEADING_FLAGS:
            continue
        plain = split_plain_registry_spec(arg)
        if plain is None:
            return None
        name, _selector = plain
        return [*args[:index], f"{name}@{version}", *args[index + 1 :]], name
    return None


def _on_windows() -> bool:
    return os.name == "nt"


def _is_bare_npx(command: object) -> bool:
    """Is *command* npx spelled BARE -- the launcher the PATH resolves?

    ``npx`` (and, on Windows only, ``npx.cmd`` / ``npx.exe``, any case). A
    path (``/tmp/x/npx``, ``./npx``, ``node_modules/.bin/npx``,
    ``/proc/self/cwd/npx``, ``~/bin/npx.cmd``) names some other program that
    is merely called npx, so a pin written into its argv would not hold
    (Consiliency/pmcp#294 piece 1, round 2). The basename is never evidence.
    """
    if not isinstance(command, str):
        return False
    if _on_windows():
        return command.lower() in {"npx", "npx.cmd", "npx.exe"}
    return command == "npx"


def _materialize_version_pin(server: ServerConfig) -> ServerConfig:
    """Write ``server.version`` into every argv that spawns the server.

    Both ``args`` (what ``client/manager.py`` spawns) and every ``install``
    argv (what ``start_install`` runs): pinning one and not the other approves
    X and runs latest (`provision_gate._config_runs_exactly`). All or nothing:
    if any argv cannot be pinned to the same package, the pin is dropped with
    a warning and the entry is returned unpinned with ``version=None``.

    The argv pin holds only if nothing the ENTRY puts in the child's
    environment can make npm run something else: an ``npm_config_package``
    turns the pinned spec into a shell command, an ``npm_config_registry``
    fetches it from another registry (measured on npm 10 and 11). So any
    ``extra_env`` key -- which also carries the overlays' ``server_env``
    patches -- that ``npm_env_may_redirect`` cannot prove inert refuses the
    pin, as does an ``env_var`` credential key the shipped manifest does not
    declare. The host's own environment and npm configuration are trusted.
    """
    version = server.version
    if version is None:
        return server

    def refuse(reason: str) -> ServerConfig:
        # Fixed text: the reason CATEGORY and the server name only -- never
        # the pin, an argv, a command or a package (piece 1).
        logger.warning(
            f"Ignoring the version pin for {_server_label(server.name)}: "
            f"{reason}; the server stays unpinned"
        )
        return replace(server, version=None)

    if server.url:
        return refuse("it is a remote server, so there is no local client to pin")
    if not _is_bare_npx(server.command):
        return refuse(
            "'version'/'server_version' pins servers launched as bare `npx` "
            "only (not uvx/pip/cargo/docker, and not a path to a program named "
            "npx); pin any other server with explicit command and args in "
            ".mcp.json or .pmcp.json instead"
        )
    env_keys = [*server.extra_env, *([server.env_var] if server.env_var else [])]
    if npm_env_may_redirect(server.name, env_keys):
        return refuse(
            "the entry's env may redirect npm (only keys pmcp's shipped manifest "
            "declares for it, locale and terminal keys, and npm logging/timing "
            "keys are allowed next to a pin)"
        )
    pinned = _pin_npx_args(list(server.args), version)
    if pinned is None:
        return refuse(
            "its args name no plain registry package (name or name@version/tag) "
            "to pin -- an alias, URL, git, file or range spec could change which "
            "package runs"
        )
    args, package = pinned
    install: dict[Platform, list[str]] = {}
    for platform, argv in server.install.items():
        if not argv:
            install[platform] = argv
            continue
        if not _is_bare_npx(argv[0]):
            return refuse("an install command is not bare `npx`")
        pinned_install = _pin_npx_args(list(argv[1:]), version)
        if pinned_install is None or pinned_install[1] != package:
            return refuse(
                "an install command does not run the plain registry package "
                "its args run"
            )
        install[platform] = [argv[0], *pinned_install[0]]
    return replace(server, args=args, install=install)


def _materialize_version_pin_soft(server: ServerConfig) -> ServerConfig:
    """`_materialize_version_pin`, contained to one entry.

    Overlay entries are only shape-checked where a field is parsed, so an argv
    can still carry a non-string (``args: ["-y", 123]``) or a non-string
    ``command``. HEAD loads such an entry untouched; a pin on it must cost that
    entry its pin, never the whole manifest (Consiliency/pmcp#295 board,
    codex P3).
    """
    try:
        return _materialize_version_pin(server)
    except Exception as exc:
        logger.warning(
            f"Ignoring the version pin for {_server_label(server.name)}: "
            f"its command/args/install could not be read "
            f"({type(exc).__name__}); the server stays unpinned"
        )
        return replace(server, version=None)


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

    extra_env = _parse_extra_env(name, data.get("extra_env"))
    env_var = data.get("env_var")
    secret_key = data.get("secret_key")
    api_key_optional_when = _parse_api_key_optional_when(
        name, data.get("api_key_optional_when"), env_var, secret_key
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
        env_var=env_var,
        secret_key=secret_key,
        env_instructions=data.get("env_instructions"),
        extra_env=extra_env,
        api_key_optional_when=api_key_optional_when,
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
        version=_parse_version_pin(name, data.get("version"), "version"),
    )


def _find_project_manifest() -> Path | None:
    """Walk up from cwd for the nearest ancestor containing .pmcp/manifest.yaml.

    Replicates config.loader.find_project_root's marker-based walk locally to
    avoid a circular import (config/loader imports load_manifest). Stops at the
    filesystem root; at the temp directory, so test fixtures under tempdir do not
    accidentally pick up an unrelated overlay; and at $HOME, whose
    `.pmcp/manifest.yaml` is the user-scoped overlay rather than a project one.

    Keep these stopping conditions in step with `find_project_root`. This docstring
    once listed only the first two, and the code had drifted the same way: the
    replica lost the $HOME stop its original has, which is what #243 fixes.
    """
    try:
        current = Path.cwd().resolve()
    except OSError:
        return None
    temp_root = Path(tempfile.gettempdir()).resolve()
    home_root = Path.home().resolve()

    while current != current.parent:
        if current == temp_root:
            return None
        # $HOME's `.pmcp/manifest.yaml` IS the user-scoped overlay, already loaded
        # (ungated) by `_overlay_manifest_paths`. Treating home as a project root
        # double-attributes it -- and since CONSENT gates project sources, every
        # startup from a subdirectory of $HOME with no closer overlay logged a
        # refusal telling the operator to `pmcp trust approve` their OWN home
        # config. That is the most common setup there is, and a false approval
        # prompt trains operators to approve reflexively, which is the one habit
        # consent depends on them not having.
        #
        # This walk replicates `config.loader.find_project_root` locally to avoid
        # an import cycle, and that function already stops here with the same
        # reason; the replica had dropped the guard. Keep the two in step.
        if current == home_root:
            return None
        candidate = current / ".pmcp" / "manifest.yaml"
        if candidate.exists():
            # Don't follow an overlay that a symlink points outside this tree:
            # resolve the candidate and require it to stay within the ancestor
            # directory that contains it.
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = None
            if resolved is not None and resolved.is_relative_to(current):
                return candidate
        current = current.parent

    return None


def _overlay_manifest_paths(
    notices: list[str] | None = None,
) -> list[tuple[str, Path]]:
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

    # ``$PMCP_MANIFEST_PATH`` is a trust-bearing redirect: it chooses the
    # highest-precedence overlay, which can ADD a server and REPLACE a shipped
    # one. It is ungated on the assumption that it is the operator speaking --
    # true only when the operator EXPORTED it. A checkout can set it through a
    # dotenv file pmcp loads on the operator's behalf (``cli.load_startup_env``
    # reads ``.env.pmcp``; ``_check_api_key_available`` reads ``.env``), and then
    # the redirect is the repository's, not the operator's (S-03). Honour it only
    # when provenance says the operator supplied it; otherwise skip it exactly as
    # if it were unset and tell the operator their project file was ignored.
    #
    # Imported here, not at module scope: ``env_store`` imports
    # ``config.loader``, which imports ``load_manifest`` from this module, so a
    # top-level import would close that cycle.
    from pmcp.env_store import (
        describe_ignored_trust_env_var,
        env_key_is_operator_supplied,
    )

    env_value = os.environ.get("PMCP_MANIFEST_PATH")
    if env_value:
        if env_key_is_operator_supplied("PMCP_MANIFEST_PATH"):
            env_path = Path(env_value).expanduser()
            if env_path.exists():
                paths.append(("env", env_path))
        else:
            notice = describe_ignored_trust_env_var("PMCP_MANIFEST_PATH", env_value)
            if notices is None:
                logger.warning(notice)
            else:
                notices.append(notice)

    return paths


_OverlayDocument = tuple[
    dict[str, ServerConfig],
    dict[str, CLIAlternative],
    dict[str, dict[str, str]],
    dict[str, str],
]


def _parse_overlay_document(
    path: Path, content: bytes, failures: list[str] | None = None
) -> _OverlayDocument:
    """Parse overlay bytes, fail-soft. ``path`` is for messages only.

    Returns ``(servers, cli_alternatives, server_env, server_version)``. A YAML error or a
    non-mapping top-level document logs a warning naming the file and returns
    empty dicts. Each entry is parsed in its own try/except so one malformed
    entry is skipped without dropping siblings.

    Takes bytes rather than a path so a gated caller can hand over the exact
    bytes its consent decision was made about.

    ``server_env`` patches ``extra_env`` on a server that already exists, so an
    operator can point a shipped server at a self-hosted endpoint without
    restating its command, args, and install block. It deliberately cannot
    create a server: ``servers:`` remains whole-entry replace.

    ``server_version`` is the same kind of patch for ``version``: it pins an
    existing server's client without restating its install matrix, and it
    cannot create a server either (Consiliency/pmcp#294).
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
        if failures is not None:
            failures.append("parse")
        return {}, {}, {}, {}

    if not isinstance(data, dict):
        logger.warning(
            f"Skipping manifest overlay {path}: top-level document is not a mapping"
        )
        if failures is not None:
            failures.append("not-a-mapping")
        return {}, {}, {}, {}

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

    server_env: dict[str, dict[str, str]] = {}
    raw_server_env = data.get("server_env", {})
    if isinstance(raw_server_env, dict):
        for name, patch in raw_server_env.items():
            if not isinstance(name, str) or not name:
                logger.warning(
                    f"Skipping non-string 'server_env' key in overlay {path}"
                )
                continue
            parsed_patch = _parse_extra_env(name, patch, field_label="server_env")
            if parsed_patch:
                server_env[name] = parsed_patch
    elif raw_server_env:
        logger.warning(f"Skipping 'server_env' in overlay {path}: not a mapping")

    server_version: dict[str, str] = {}
    raw_server_version = data.get("server_version", {})
    if isinstance(raw_server_version, dict):
        for name, raw_version in raw_server_version.items():
            if not isinstance(name, str) or not name:
                logger.warning(
                    f"Skipping non-string 'server_version' key in overlay {path}"
                )
                continue
            version = _parse_version_pin(name, raw_version, "server_version")
            if version is not None:
                server_version[name] = version
    elif raw_server_version:
        logger.warning(f"Skipping 'server_version' in overlay {path}: not a mapping")

    return servers, cli_alternatives, server_env, server_version


# A parsed manifest, cached by EVERYTHING it was built from (Consiliency/pmcp#233).
# The key is the bytes of every source (sha256), not their mtimes: each call
# re-reads every source anyway -- the consent gate must judge the bytes it
# hands over -- so a content key costs one hash and cannot be fooled by a
# same-size rewrite inside the filesystem's timestamp granularity or by an
# mtime set back. Values are pickled bytes, so no caller can reach the cached
# state, and every call gets its own deep copy (0.3 ms, against 2.4 ms for
# copy.deepcopy). The pickles are made in this process from pmcp's own
# Manifest objects and never leave it: nothing untrusted is ever unpickled.
_MANIFEST_CACHE_SLOTS = 8
_manifest_cache: OrderedDict[tuple[Any, ...], bytes] = OrderedDict()
_manifest_cache_lock = threading.RLock()
# The key of the state the previous call served, per kind of call (default
# load with overlays, or an explicit path). Warnings are owed per TRANSITION,
# not per retained entry: a call whose key differs from its slot's is rebuilt
# (about 1.2 ms with the shipped document cached) even when its key is still in
# the LRU, so returning to an earlier state -- trust revoked again, a pin broken
# again with the same bytes -- warns exactly as main does. Separate slots keep
# an explicit-path caller from turning every default load into a transition.
_last_served_keys: dict[bool, tuple[Any, ...]] = {}
# Whether this process has already said, at WARNING, that the cache could not
# store or read back a result for a reason other than recursion depth.
_cache_failure_reported = False


def clear_manifest_cache() -> None:
    """Drop every cached manifest (tests; never needed for correctness)."""
    global _cache_failure_reported
    with _manifest_cache_lock:
        _manifest_cache.clear()
        _last_served_keys.clear()
        _cache_failure_reported = False


def _report_cache_failure(what: str, exc: BaseException) -> None:
    """Log a cache failure by exception class only, never a value.

    ``RecursionError`` is expected -- a deeply aliased overlay nests deeper
    than pickle recurses on some Pythons -- and only bypasses the cache for that
    result: DEBUG. Anything else means the cache may be off for every load, so
    the first one in the process is a WARNING an operator can see.
    """
    global _cache_failure_reported
    name = type(exc).__name__
    if isinstance(exc, RecursionError) or _cache_failure_reported:
        logger.debug("Manifest cache: %s failed (%s); not cached", what, name)
        return
    _cache_failure_reported = True
    logger.warning(
        "Manifest cache: %s failed (%s); manifests load uncached when this "
        "happens. Further failures are logged at DEBUG.",
        what,
        name,
    )


def _serialize(value: Any) -> bytes | None:
    """``pickle.dumps(value)``, or None when it cannot be serialized.

    A result that loads fine must never fail because of the cache: a deeply
    aliased overlay (500 chained YAML anchors, 11 KB) parses with SafeLoader
    but exceeds pickle's recursion limit on Python 3.10/3.11. Such a result is
    returned uncached. The log line names only the exception class.
    """
    try:
        return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:  # noqa: BLE001 - any failure means "do not cache"
        _report_cache_failure("storing a result", exc)
        return None


def _deserialize(blob: bytes) -> Any | None:
    """``pickle.loads(blob)``, or None when it fails (the caller rebuilds)."""
    try:
        return pickle.loads(blob)
    except Exception as exc:  # noqa: BLE001 - any failure means "rebuild"
        _report_cache_failure("reading back a result", exc)
        return None


@dataclass(frozen=True)
class _OverlaySource:
    label: str
    path: Path
    content: bytes | None
    # "read" | "approved" | a consent refusal reason | "unreadable"
    state: str
    decision: Any = None
    error: str | None = None
    # sha256 of the bytes the consent gate read, refused or not. A refused
    # file's content is never handed over, but an edit to it is a new state
    # that owes the operator a fresh refusal.
    gated_digest: str | None = None


def _gather_overlay_sources(notices: list[str]) -> list[_OverlaySource]:
    """Read every overlay source ONCE, in precedence order.

    The bytes read here are both the cache key and what gets parsed: a source is
    never re-opened, so a rewrite between the read and the parse cannot put
    bytes in the cache under another version's key.
    """
    sources: list[_OverlaySource] = []
    for label, overlay_path in _overlay_manifest_paths(notices):
        if label == "project":
            # A repository-supplied overlay is gated: unapproved, it must
            # contribute nothing at all -- not a replacement, not an
            # insertion, not a server_env patch -- so that
            # `get_server("<added>")`, the manifest-backed predicate in
            # tools/handlers.py, still answers None for it. User and env
            # scope are the operator's own files and stay ungated. The gate
            # reads the file once; its bytes are the ones keyed and parsed.
            content, decision = read_and_gate(overlay_path, "project_manifest")
            sources.append(
                _OverlaySource(
                    label,
                    overlay_path,
                    content,
                    "approved" if content is not None else str(decision.reason),
                    decision,
                    gated_digest=decision.content_sha256,
                )
            )
            continue
        try:
            content = overlay_path.read_bytes()
        except OSError as exc:
            sources.append(
                _OverlaySource(label, overlay_path, None, "unreadable", error=str(exc))
            )
            continue
        sources.append(_OverlaySource(label, overlay_path, content, "read"))
    return sources


def _digest(content: bytes | None) -> bytes | None:
    return None if content is None else hashlib.sha256(content).digest()


def _source_key(source: _OverlaySource) -> tuple[Any, ...]:
    """One overlay's part of the cache key.

    A project overlay's consent decision is keyed by its identity -- the
    resolved path the gate judged, the reason and the remediation -- not only
    its state: retargeting a symlink from one unapproved file to another is a
    new refusal with a new remediation, and must be told (SECURITY C-12).
    """
    decision = source.decision
    identity = (
        None
        if decision is None
        else (str(decision.path), str(decision.reason), decision.remediation)
    )
    return (
        source.label,
        str(source.path),
        source.state,
        _digest(source.content),
        source.gated_digest,
        identity,
    )


def load_manifest(manifest_path: Path | None = None) -> Manifest:
    """Load and parse the manifest.yaml file.

    When called with no ``manifest_path`` (all internal callers), private/custom
    overlay manifests are merged over the shipped manifest: user
    (``~/.pmcp/manifest.yaml``) < project (``<project>/.pmcp/manifest.yaml``) <
    ``$PMCP_MANIFEST_PATH``, each overriding the shipped entry of the same name
    (whole-entry replace). An overlay's ``server_env`` additionally patches
    ``extra_env`` on an existing server without replacing it. An explicit
    ``manifest_path`` loads only that file and applies no overlays. Overlay
    parsing is fail-soft and never raises.

    The result is cached by the bytes of every source, each overlay's consent
    decision (with the refusal's identity), the ignored-redirect notice and the
    platform; a change to any of them is a miss. Warnings are emitted once per
    transition: whenever the inputs differ from those of the previous call,
    even if that state is still cached. A result in which a source could not be
    read or parsed, or that cannot be serialized, is never cached, and an
    exception is never cached. Every call returns its own deep copy.
    """
    apply_overlays = manifest_path is None
    base_path = _SHIPPED_MANIFEST_PATH if manifest_path is None else manifest_path
    base = base_path.read_bytes()
    notices: list[str] = []
    overlays = _gather_overlay_sources(notices) if apply_overlays else []
    key = (
        str(base_path),
        apply_overlays,
        _digest(base),
        tuple(_source_key(o) for o in overlays),
        tuple(notices),
        _on_windows(),
    )
    with _manifest_cache_lock:
        steady = key == _last_served_keys.get(apply_overlays)
        _last_served_keys[apply_overlays] = key
        blob = _manifest_cache.get(key)
        if blob is not None and steady:
            _manifest_cache.move_to_end(key)
            cached = _deserialize(blob)
            if cached is not None:
                return cast(Manifest, cached)
        # A miss, or a transition into a state the LRU still holds: build, so
        # every warning for this state is emitted once, now.
        _manifest_cache.pop(key, None)
        for notice in notices:
            logger.warning(notice)
        manifest, cacheable = _build_manifest(
            base_path, base, overlays, trusted=manifest_path is None
        )
        stored = _serialize(manifest) if cacheable else None
        if stored is not None:
            _manifest_cache[key] = stored
            while len(_manifest_cache) > _MANIFEST_CACHE_SLOTS:
                _manifest_cache.popitem(last=False)
        return manifest


def _build_manifest(
    manifest_path: Path,
    base: bytes,
    overlays: list[_OverlaySource],
    *,
    trusted: bool,
) -> tuple[Manifest, bool]:
    """Build a Manifest from bytes already read. Returns (manifest, cacheable)."""
    cacheable = True
    apply_overlays = trusted
    logger.info(f"Loading manifest from {manifest_path}")

    data = _parse_trusted_document(base) if trusted else yaml.safe_load(base)

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
        for source in overlays:
            label, overlay_path = source.label, source.path
            if source.content is None:
                if source.label == "project":
                    # One WARNING for one refusal: returning before the parser
                    # runs keeps an unreadable overlay from also logging
                    # "Skipping unreadable manifest overlay".
                    log_refusal(source.decision, logger)
                else:
                    logger.warning(
                        f"Skipping unreadable manifest overlay {overlay_path}: "
                        f"{source.error}"
                    )
                if source.state == "unreadable":
                    cacheable = False
                continue
            failures: list[str] = []
            # Parse the bytes that were read (and, for the project overlay,
            # judged by the gate). Re-opening `overlay_path` here would apply
            # content nobody approved.
            (
                overlay_servers,
                overlay_clis,
                overlay_server_env,
                overlay_server_version,
            ) = _parse_overlay_document(overlay_path, source.content, failures)
            if failures:
                cacheable = False
            if (
                overlay_servers
                or overlay_clis
                or overlay_server_env
                or overlay_server_version
            ):
                logger.info(
                    f"Applying manifest overlay ({label}) from {overlay_path}: "
                    f"{len(overlay_servers)} servers, "
                    f"{len(overlay_clis)} CLI alternatives, "
                    f"{len(overlay_server_env)} server_env patches, "
                    f"{len(overlay_server_version)} server_version pins"
                )
            for name in overlay_servers:
                if name in servers:
                    logger.warning(
                        f"Manifest overlay ({label}) from {overlay_path} overrides "
                        f"existing server '{name}'"
                    )
            servers.update(overlay_servers)
            cli_alternatives.update(overlay_clis)

            # Applied AFTER this source's whole-entry replaces, so a patch can
            # refine an entry the same overlay just replaced. Patching merges
            # per key, leaving other extra_env values from the base intact.
            for name, patch in overlay_server_env.items():
                existing = servers.get(name)
                if existing is None:
                    logger.warning(
                        f"Manifest overlay ({label}) from {overlay_path} has a "
                        f"'server_env' patch for unknown server '{name}': skipped"
                    )
                    continue
                servers[name] = replace(
                    existing, extra_env={**existing.extra_env, **patch}
                )

            # Same rules as server_env: after this source's replaces, and never
            # for a server the manifest does not already define.
            for name, version in overlay_server_version.items():
                existing = servers.get(name)
                if existing is None:
                    logger.warning(
                        f"Manifest overlay ({label}) from {overlay_path} has a "
                        f"'server_version' pin for a server it does not "
                        f"define ({_server_label(name)}): skipped"
                    )
                    continue
                servers[name] = replace(existing, version=version)

    # Materialise every pin once, after all overlays: a later source's
    # whole-entry replace or server_version patch must be what gets written
    # into argv, not an earlier one's.
    servers = {
        name: _materialize_version_pin_soft(entry) for name, entry in servers.items()
    }

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

    return manifest, cacheable
