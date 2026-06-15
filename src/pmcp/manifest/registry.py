"""MCP Registry cache and parser."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_ENDPOINT = "https://registry.modelcontextprotocol.io/v0/servers"
REGISTRY_CACHE_TTL_SECONDS = 300.0
_IN_PROCESS_CACHE: dict[tuple[Any, ...], tuple[float, "RegistryCache"]] = {}
_IN_PROCESS_TASKS: dict[tuple[Any, ...], "asyncio.Task[RegistryCache]"] = {}


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
class RegistryRemote:
    """Remote transport metadata from a registry server entry."""

    transport: str | None = None
    url: str | None = None
    headers: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegistryServerMeta:
    """Outer registry metadata for server status and latest-version hints."""

    status: str | None = None
    is_latest: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegistryServerEntry:
    """Normalized MCP Registry server metadata."""

    name: str
    description: str
    packages: list[RegistryPackage] = field(default_factory=list)
    remotes: list[RegistryRemote] = field(default_factory=list)
    registry_meta: RegistryServerMeta = field(default_factory=RegistryServerMeta)
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


def default_registry_cache_path() -> Path:
    """Return PMCP's stable registry cache path, independent of Path.cwd()."""
    cache_home = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_home).expanduser() if cache_home else Path.home() / ".cache"
    return base / "pmcp" / "registry-cache.json"


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


def _parse_header_placeholders(value: Any) -> list[str]:
    headers: list[str] = []
    candidate: str | None
    if isinstance(value, dict):
        items: Any = value.items()
    elif isinstance(value, list):
        items = enumerate(value)
    else:
        return headers
    for key, item in items:
        if isinstance(item, str):
            candidate = item.strip()
            if candidate.startswith("${") and candidate.endswith("}"):
                candidate = candidate[2:-1].strip()
            elif not candidate.isidentifier():
                candidate = str(key).strip()
        elif isinstance(item, dict):
            candidate = _first_str(
                item,
                "env",
                "envVar",
                "env_var",
                "name",
                "placeholder",
            )
        else:
            candidate = str(key).strip()
        if candidate and candidate not in headers:
            headers.append(candidate)
    return headers


def _parse_remote(data: Any) -> RegistryRemote | None:
    if not isinstance(data, dict):
        return None
    transport_data = data.get("transport")
    transport = None
    url = _first_str(data, "url", "remoteUrl", "remote_url")
    if isinstance(transport_data, dict):
        transport = _first_str(transport_data, "type")
        url = url or _first_str(transport_data, "url")
    elif isinstance(transport_data, str):
        transport = transport_data
    if not transport and not url:
        return None
    headers = _parse_header_placeholders(
        data.get("headers")
        or data.get("headerPlaceholders")
        or data.get("header_placeholders")
    )
    return RegistryRemote(
        transport=transport,
        url=url,
        headers=headers,
        raw=dict(data),
    )


def _parse_meta(entry: dict[str, Any], server: dict[str, Any]) -> RegistryServerMeta:
    raw_meta = entry.get("_meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    official = meta.get("official") or entry.get("official") or server.get("official")
    official_meta = official if isinstance(official, dict) else {}
    is_latest = official_meta.get("isLatest")
    if not isinstance(is_latest, bool):
        direct = meta.get("isLatest") or server.get("isLatest")
        is_latest = direct if isinstance(direct, bool) else None
    return RegistryServerMeta(
        status=_first_str(meta, "status") or _first_str(server, "status"),
        is_latest=is_latest,
        raw=dict(meta),
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
    remotes = [
        remote
        for remote in (_parse_remote(item) for item in server.get("remotes", []) or [])
        if remote is not None
    ]
    diagnostics: list[str] = ["registry_metadata_read_only"]
    if not packages and not remotes:
        diagnostics.append("registry_entry_without_package")
    raw_auth = server.get("auth")
    auth: dict[str, Any] = raw_auth if isinstance(raw_auth, dict) else {}
    return RegistryServerEntry(
        name=name,
        description=_first_str(server, "description") or "",
        packages=packages,
        remotes=remotes,
        registry_meta=_parse_meta(raw, server),
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


def _server_identity(entry: RegistryServerEntry) -> str:
    if entry.packages:
        return entry.packages[0].identifier
    if entry.remotes and entry.remotes[0].url:
        return entry.remotes[0].url or entry.name
    return entry.name


def _dedupe_latest(servers: list[RegistryServerEntry]) -> list[RegistryServerEntry]:
    deduped: dict[str, RegistryServerEntry] = {}
    for entry in servers:
        key = _server_identity(entry)
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = entry
            continue
        if entry.registry_meta.is_latest and not existing.registry_meta.is_latest:
            deduped[key] = entry
    return list(deduped.values())


def _with_query(endpoint: str, params: dict[str, str]) -> str:
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    split = urlsplit(endpoint)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit(split._replace(query=urlencode(query)))


async def _fetch_registry_servers_uncached(
    endpoint: str,
    *,
    timeout: float,
    max_pages: int,
    max_response_bytes: int,
) -> RegistryCache:
    diagnostics: list[str] = []
    servers: list[RegistryServerEntry] = []
    next_cursor: str | None = None
    current_endpoint = _with_query(endpoint, {"version": "latest"})
    try:
        timeout_config = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_config) as session:
            for page in range(max_pages):
                url = (
                    _with_query(current_endpoint, {"cursor": next_cursor})
                    if next_cursor
                    else current_endpoint
                )
                async with session.get(url) as resp:
                    body = await resp.read()
                if len(body) > max_response_bytes:
                    diagnostics.append("registry_response_size_cap")
                    break
                payload = json.loads(body.decode("utf-8"))
                cache = _parse_cache_payload(payload, endpoint)
                servers.extend(cache.servers)
                diagnostics.extend(cache.diagnostics)
                metadata = (
                    payload.get("metadata") if isinstance(payload, dict) else None
                )
                next_value = (
                    metadata.get("nextCursor") if isinstance(metadata, dict) else None
                )
                next_cursor = (
                    next_value if isinstance(next_value, str) and next_value else None
                )
                if not next_cursor:
                    break
            else:
                diagnostics.append("registry_page_cap")
    except Exception as exc:
        logger.info("MCP Registry fetch failed: %s", type(exc).__name__)
        return RegistryCache(
            schema_version="registry-cache.v1",
            source_endpoint=endpoint,
            fetched_at=_now_iso(),
            diagnostics=[f"registry_fetch_failed:{type(exc).__name__}"],
        )
    latest_servers = [
        entry for entry in servers if entry.registry_meta.is_latest is not False
    ]
    return RegistryCache(
        schema_version="registry-cache.v1",
        source_endpoint=endpoint,
        fetched_at=_now_iso(),
        servers=_dedupe_latest(latest_servers),
        diagnostics=diagnostics,
    )


async def fetch_registry_servers(
    endpoint: str = DEFAULT_REGISTRY_ENDPOINT,
    *,
    timeout: float = 5.0,
    max_pages: int = 5,
    max_response_bytes: int = 2_000_000,
    use_in_process_cache: bool = True,
) -> RegistryCache:
    """Fetch and parse the MCP Registry without leaking network errors."""
    key = (endpoint, timeout, max_pages, max_response_bytes)
    now = time.monotonic()
    if use_in_process_cache:
        cached = _IN_PROCESS_CACHE.get(key)
        if cached is not None and now - cached[0] < REGISTRY_CACHE_TTL_SECONDS:
            return cached[1]
        task = _IN_PROCESS_TASKS.get(key)
        if task is not None:
            return await task
        task = asyncio.create_task(
            _fetch_registry_servers_uncached(
                endpoint,
                timeout=timeout,
                max_pages=max_pages,
                max_response_bytes=max_response_bytes,
            )
        )
        _IN_PROCESS_TASKS[key] = task
        try:
            cache = await task
        finally:
            _IN_PROCESS_TASKS.pop(key, None)
        _IN_PROCESS_CACHE[key] = (time.monotonic(), cache)
        return cache
    return await _fetch_registry_servers_uncached(
        endpoint,
        timeout=timeout,
        max_pages=max_pages,
        max_response_bytes=max_response_bytes,
    )


def _cache_path(cache_path: Path | None) -> Path:
    return cache_path or default_registry_cache_path()


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
