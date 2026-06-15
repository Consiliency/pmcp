"""MCP Registry cache and parser."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import urlopen

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_ENDPOINT = "https://registry.modelcontextprotocol.io/v0/servers"
DEFAULT_REGISTRY_CACHE = Path(".mcp-gateway") / "registry-cache.json"


@dataclass
class RegistryPackage:
    """Package metadata from a registry server entry."""

    identifier: str
    registry_type: str | None = None
    transport: str | None = None
    runtime_hint: str | None = None
    env_vars: list[str] = field(default_factory=list)
    url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegistryServerEntry:
    """Normalized MCP Registry server metadata."""

    name: str
    description: str
    packages: list[RegistryPackage] = field(default_factory=list)
    server_card_url: str | None = None
    declared_capabilities: list[str] = field(default_factory=list)
    protected_resource_metadata_url: str | None = None
    authorization_server_metadata_url: str | None = None
    declared_scopes: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class RegistryCache:
    """A deterministic cache of MCP Registry server metadata."""

    schema_version: str
    source_endpoint: str
    fetched_at: str
    servers: list[RegistryServerEntry] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _first_str(mapping: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_package(data: Any) -> RegistryPackage | None:
    if not isinstance(data, dict):
        return None
    identifier = _first_str(data, "identifier", "package", "name")
    if not identifier:
        return None
    transport_data = data.get("transport")
    transport = None
    url = _first_str(data, "url", "remoteUrl", "remote_url")
    if isinstance(transport_data, dict):
        transport = _first_str(transport_data, "type")
        url = url or _first_str(transport_data, "url")
    elif isinstance(transport_data, str):
        transport = transport_data
    env_vars = _string_list(data.get("env_vars") or data.get("envVars"))
    for item in data.get("environmentVariables", []) or []:
        if isinstance(item, dict):
            name = _first_str(item, "name")
            if name and name not in env_vars:
                env_vars.append(name)
    return RegistryPackage(
        identifier=identifier,
        registry_type=_first_str(data, "registryType", "registry_type"),
        transport=transport,
        runtime_hint=_first_str(data, "runtimeHint", "runtime_hint", "runtime"),
        env_vars=env_vars,
        url=url,
        raw=dict(data),
    )


def _entry_payload(entry: Any) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not isinstance(entry, dict):
        return None, {}
    server = entry.get("server")
    if isinstance(server, dict):
        return server, entry
    return entry, entry


def _parse_entry(entry: Any) -> RegistryServerEntry | None:
    server, raw = _entry_payload(entry)
    if server is None:
        return None
    name = _first_str(server, "name", "id", "displayName", "display_name")
    if not name:
        return None
    packages = [
        package
        for package in (_parse_package(pkg) for pkg in server.get("packages", []) or [])
        if package is not None
    ]
    diagnostics: list[str] = ["registry_metadata_read_only"]
    if not packages:
        diagnostics.append("registry_entry_without_package")
    raw_auth = server.get("auth")
    auth: dict[str, Any] = raw_auth if isinstance(raw_auth, dict) else {}
    return RegistryServerEntry(
        name=name,
        description=_first_str(server, "description") or "",
        packages=packages,
        server_card_url=_first_str(server, "serverCardUrl", "server_card_url"),
        declared_capabilities=_string_list(server.get("capabilities")),
        protected_resource_metadata_url=_first_str(
            server,
            "protectedResourceMetadataUrl",
            "protected_resource_metadata_url",
        )
        or _first_str(
            auth, "protectedResourceMetadataUrl", "protected_resource_metadata_url"
        ),
        authorization_server_metadata_url=_first_str(
            server,
            "authorizationServerMetadataUrl",
            "authorization_server_metadata_url",
        )
        or _first_str(
            auth,
            "authorizationServerMetadataUrl",
            "authorization_server_metadata_url",
        ),
        declared_scopes=_string_list(
            server.get("scopes") or server.get("declared_scopes")
        ),
        raw=dict(raw),
        diagnostics=diagnostics,
    )


def _parse_cache_payload(payload: Any, endpoint: str) -> RegistryCache:
    diagnostics: list[str] = []
    if not isinstance(payload, dict):
        return RegistryCache(
            schema_version="registry-cache.v1",
            source_endpoint=endpoint,
            fetched_at=_now_iso(),
            diagnostics=["registry_payload_not_object"],
        )
    raw_servers = payload.get("servers", [])
    if not isinstance(raw_servers, list):
        raw_servers = []
        diagnostics.append("registry_servers_not_list")
    servers = []
    for item in raw_servers:
        parsed = _parse_entry(item)
        if parsed is None:
            diagnostics.append("registry_entry_skipped")
            continue
        servers.append(parsed)
    return RegistryCache(
        schema_version="registry-cache.v1",
        source_endpoint=endpoint,
        fetched_at=_now_iso(),
        servers=servers,
        diagnostics=diagnostics,
        raw=dict(payload),
    )


def fetch_registry_servers(
    endpoint: str = DEFAULT_REGISTRY_ENDPOINT, *, timeout: float = 5.0
) -> RegistryCache:
    """Fetch and parse the MCP Registry without leaking network errors."""
    try:
        with urlopen(endpoint, timeout=timeout) as resp:  # nosec B310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.info("MCP Registry fetch failed: %s", type(exc).__name__)
        return RegistryCache(
            schema_version="registry-cache.v1",
            source_endpoint=endpoint,
            fetched_at=_now_iso(),
            diagnostics=[f"registry_fetch_failed:{type(exc).__name__}"],
        )
    return _parse_cache_payload(payload, endpoint)


def _cache_path(cache_path: Path | None) -> Path:
    return cache_path or DEFAULT_REGISTRY_CACHE


def load_registry_cache(cache_path: Path | None = None) -> RegistryCache | None:
    """Load a deterministic registry cache, returning None on failure."""
    path = _cache_path(cache_path)
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    cache = _parse_cache_payload(payload, str(payload.get("source_endpoint", "")))
    cache.schema_version = str(payload.get("schema_version", "registry-cache.v1"))
    cache.fetched_at = str(payload.get("fetched_at", cache.fetched_at))
    cache.diagnostics = _string_list(payload.get("diagnostics")) + cache.diagnostics
    return cache


def save_registry_cache(cache: RegistryCache, cache_path: Path | None = None) -> None:
    """Save registry cache JSON with stable ordering."""
    path = _cache_path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cache), indent=2, sort_keys=True) + "\n")
