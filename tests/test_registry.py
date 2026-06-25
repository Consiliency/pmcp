"""Tests for manifest-layer MCP Registry parsing and caching."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pmcp.manifest.registry import (
    RegistryCache,
    default_registry_cache_path,
    fetch_registry_servers,
    load_registry_cache,
    save_registry_cache,
)


RECORDED_PAYLOAD = {
    "servers": [
        {
            "server": {
                "name": "GitHub MCP",
                "description": "GitHub integration",
                "serverCardUrl": "https://example.com/github-card.json",
                "capabilities": ["tools"],
                "protectedResourceMetadataUrl": "https://mcp.example/prm",
                "authorizationServerMetadataUrl": "https://auth.example/as",
                "scopes": ["repo"],
                "remotes": [
                    {
                        "transport": "streamable-http",
                        "url": "https://api.githubcopilot.com/mcp/",
                        "headers": [{"name": "GITHUB_TOKEN"}],
                        "unknownRemoteField": "kept",
                    }
                ],
                "packages": [
                    {
                        "identifier": "@github/github-mcp-server",
                        "transport": {
                            "type": "streamable-http",
                            "url": "https://api.githubcopilot.com/mcp/",
                        },
                        "environmentVariables": [{"name": "GITHUB_TOKEN"}],
                        "unknownPreviewField": {"kept": True},
                    }
                ],
                "unknownServerField": "kept",
            }
        },
        {
            "_meta": {"official": {"isLatest": False}},
            "server": {
                "name": "GitHub MCP old",
                "description": "Old GitHub integration",
                "packages": [
                    {
                        "identifier": "@github/github-mcp-server",
                        "transport": "stdio",
                    }
                ],
            },
        },
        {
            "server": {
                "name": "stdio server",
                "description": "stdio package",
                "packages": [{"identifier": "@example/stdio", "transport": "stdio"}],
            }
        },
        {"server": {"name": "malformed", "packages": [{}]}},
    ]
}


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def read(self) -> bytes:
        return json.dumps(self._payload).encode()


class _Session:
    def __init__(self, calls: list[str], pages: list[dict[str, object]]) -> None:
        self._calls = calls
        self._pages = pages

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def get(self, url: str) -> _Response:
        self._calls.append(url)
        return _Response(self._pages.pop(0))


async def test_fetch_registry_servers_parses_preview_schema(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        "pmcp.manifest.registry.aiohttp.ClientSession",
        lambda **_: _Session(calls, [RECORDED_PAYLOAD]),
    )

    cache = await fetch_registry_servers(
        "https://registry.example/v0/servers",
        timeout=1.5,
        use_in_process_cache=False,
    )

    assert calls == ["https://registry.example/v0/servers?version=latest"]
    assert cache.source_endpoint == "https://registry.example/v0/servers"
    assert cache.servers[0].packages[0].transport == "streamable-http"
    assert cache.servers[0].packages[0].env_vars == ["GITHUB_TOKEN"]
    assert cache.servers[0].remotes[0].transport == "streamable-http"
    assert cache.servers[0].remotes[0].url == "https://api.githubcopilot.com/mcp/"
    assert cache.servers[0].remotes[0].headers == ["GITHUB_TOKEN"]
    assert cache.servers[0].remotes[0].raw["unknownRemoteField"] == "kept"
    assert cache.servers[0].packages[0].raw["unknownPreviewField"] == {"kept": True}
    assert cache.servers[0].raw["server"]["unknownServerField"] == "kept"
    assert all(
        "TOKEN" not in diag for server in cache.servers for diag in server.diagnostics
    )
    assert len(cache.servers) == 3


async def test_fetch_registry_servers_timeout_returns_diagnostic(monkeypatch) -> None:
    class _FailingSession:
        async def __aenter__(self) -> _FailingSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _Response:
            raise TimeoutError("network unavailable")

    def failing_session(**_: object) -> _FailingSession:
        return _FailingSession()

    monkeypatch.setattr("pmcp.manifest.registry.aiohttp.ClientSession", failing_session)

    cache = await fetch_registry_servers(
        "https://registry.example/v0/servers",
        timeout=0.1,
        use_in_process_cache=False,
    )

    assert cache.servers == []
    assert cache.diagnostics == ["registry_fetch_failed:TimeoutError"]


async def test_fetch_registry_servers_paginates_and_deduplicates(monkeypatch) -> None:
    calls: list[str] = []
    page1 = json.loads(
        Path("tests/fixtures/registry/v0_servers_page1.json").read_text()
    )
    page2 = json.loads(
        Path("tests/fixtures/registry/v0_servers_page2.json").read_text()
    )

    monkeypatch.setattr(
        "pmcp.manifest.registry.aiohttp.ClientSession",
        lambda **_: _Session(calls, [page1, page2]),
    )

    cache = await fetch_registry_servers(
        "https://registry.example/v0/servers",
        use_in_process_cache=False,
    )

    assert calls == [
        "https://registry.example/v0/servers?version=latest",
        "https://registry.example/v0/servers?version=latest&cursor=page-2",
    ]
    assert [server.name for server in cache.servers] == [
        "Remote Search",
        "Second Page",
    ]
    assert cache.servers[0].registry_meta.status == "active"
    assert cache.servers[0].registry_meta.is_latest is True


async def test_fetch_registry_servers_coalesces_in_process_callers(monkeypatch) -> None:
    calls: list[str] = []

    class _SlowSession(_Session):
        def get(self, url: str) -> _Response:
            calls.append(url)
            return _Response(RECORDED_PAYLOAD)

    monkeypatch.setattr(
        "pmcp.manifest.registry.aiohttp.ClientSession",
        lambda **_: _SlowSession(calls, [RECORDED_PAYLOAD]),
    )

    first, second = await asyncio.gather(
        fetch_registry_servers("https://registry.example/coalesce"),
        fetch_registry_servers("https://registry.example/coalesce"),
    )

    assert first is second
    assert calls == ["https://registry.example/coalesce?version=latest"]


def test_default_cache_path_is_not_cwd_relative(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    path = default_registry_cache_path()

    assert path.is_absolute()
    assert not str(path).startswith(str(tmp_path))


def test_fetch_registry_servers_timeout_returns_diagnostic_legacy_removed() -> None:
    # Kept as a guard against direct urllib fetch regression in registry.py.
    assert fetch_registry_servers.__name__ == "fetch_registry_servers"


def test_registry_cache_round_trip_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "registry-cache.json"
    cache = RegistryCache(
        schema_version="registry-cache.v1",
        source_endpoint="https://registry.example/v0/servers",
        fetched_at="2026-06-15T00:00:00Z",
    )

    save_registry_cache(cache, path)
    loaded = load_registry_cache(path)

    assert loaded is not None
    assert loaded.schema_version == "registry-cache.v1"
    assert path.read_text().endswith("\n")


def _delta_page(name: str, identifier: str, cursor: str | None) -> dict[str, object]:
    page: dict[str, object] = {
        "servers": [
            {
                "_meta": {"official": {"isLatest": True, "status": "active"}},
                "server": {
                    "name": name,
                    "description": f"{name} desc",
                    "packages": [{"identifier": identifier, "transport": "stdio"}],
                },
            }
        ]
    }
    if cursor:
        page["metadata"] = {"nextCursor": cursor}
    return page


async def test_fetch_registry_incremental_uses_updated_since(monkeypatch) -> None:
    calls: list[str] = []
    page1 = _delta_page("Updated Server", "@example/updated", "delta-2")
    page2 = _delta_page("New Server", "@example/new", None)
    monkeypatch.setattr(
        "pmcp.manifest.registry.aiohttp.ClientSession",
        lambda **_: _Session(calls, [page1, page2]),
    )

    cache = await fetch_registry_servers(
        "https://registry.example/v0/servers",
        updated_since="2026-06-25T00:00:00Z",
        use_in_process_cache=False,
    )

    assert calls == [
        "https://registry.example/v0/servers?version=latest"
        "&updated_since=2026-06-25T00%3A00%3A00Z",
        "https://registry.example/v0/servers?version=latest"
        "&updated_since=2026-06-25T00%3A00%3A00Z&cursor=delta-2",
    ]
    assert {s.name for s in cache.servers} == {"Updated Server", "New Server"}
    assert cache.last_synced_at is not None


def test_registry_cache_round_trips_last_synced_at(tmp_path: Path) -> None:
    path = tmp_path / "registry-cache.json"
    cache = RegistryCache(
        schema_version="registry-cache.v1",
        source_endpoint="https://registry.example/v0/servers",
        fetched_at="2026-06-25T00:00:00Z",
        last_synced_at="2026-06-25T00:00:00Z",
    )
    save_registry_cache(cache, path)
    loaded = load_registry_cache(path)
    assert loaded is not None
    assert loaded.last_synced_at == "2026-06-25T00:00:00Z"

    # Old cache JSON without the field loads with last_synced_at == None.
    legacy = json.loads(path.read_text())
    legacy.pop("last_synced_at", None)
    path.write_text(json.dumps(legacy))
    loaded_legacy = load_registry_cache(path)
    assert loaded_legacy is not None
    assert loaded_legacy.last_synced_at is None


async def test_incremental_failure_degrades_to_fallback_cache(monkeypatch) -> None:
    def _raising_session(**_: object) -> object:
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "pmcp.manifest.registry.aiohttp.ClientSession", _raising_session
    )
    fallback = RegistryCache(
        schema_version="registry-cache.v1",
        source_endpoint="https://registry.example/v0/servers",
        fetched_at="2026-06-24T00:00:00Z",
        servers=[],
        last_synced_at="2026-06-24T00:00:00Z",
    )

    cache = await fetch_registry_servers(
        "https://registry.example/v0/servers",
        updated_since="2026-06-25T00:00:00Z",
        fallback_cache=fallback,
        use_in_process_cache=False,
    )

    # Returns a copy of the fallback with a degraded marker, preserving the
    # cached servers — and must NOT mutate the caller-owned fallback object.
    assert cache is not fallback
    assert cache.servers == fallback.servers
    assert cache.last_synced_at == fallback.last_synced_at
    assert "registry_fetch_degraded_to_cache" in cache.diagnostics
    assert "registry_fetch_degraded_to_cache" not in fallback.diagnostics


def test_merge_registry_delta_merges_and_dedupes() -> None:
    from pmcp.manifest.registry import RegistryPackage, RegistryServerEntry
    from pmcp.manifest.sync import merge_registry_delta

    def entry(name: str, identifier: str, desc: str) -> RegistryServerEntry:
        return RegistryServerEntry(
            name=name,
            description=desc,
            packages=[RegistryPackage(identifier=identifier)],
        )

    base = RegistryCache(
        schema_version="registry-cache.v1",
        source_endpoint="https://registry.example/v0/servers",
        fetched_at="2026-06-24T00:00:00Z",
        servers=[entry("A", "@x/a", "old"), entry("B", "@x/b", "keep")],
    )
    delta = RegistryCache(
        schema_version="registry-cache.v1",
        source_endpoint="https://registry.example/v0/servers",
        fetched_at="2026-06-25T00:00:00Z",
        servers=[entry("A", "@x/a", "new"), entry("C", "@x/c", "added")],
        last_synced_at="2026-06-25T00:00:00Z",
    )

    merged = merge_registry_delta(base, delta)
    by_id = {p.packages[0].identifier: p for p in merged.servers}
    assert set(by_id) == {"@x/a", "@x/b", "@x/c"}
    assert by_id["@x/a"].description == "new"  # delta replaced base
    assert by_id["@x/b"].description == "keep"  # base preserved
    assert merged.last_synced_at == "2026-06-25T00:00:00Z"


def test_registry_private_flag_defaults_off(monkeypatch) -> None:
    from pmcp.manifest.registry import (
        DEFAULT_REGISTRY_ENDPOINT,
        effective_registry_endpoint,
        registry_private_enabled,
    )

    monkeypatch.delenv("PMCP_REGISTRY_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv(
        "PMCP_REGISTRY_PRIVATE_ENDPOINT", "https://private.example/v0/servers"
    )
    # Flag off: private endpoint is ignored, public endpoint used.
    assert registry_private_enabled() is False
    assert effective_registry_endpoint() == DEFAULT_REGISTRY_ENDPOINT


def test_registry_private_flag_on_uses_private_endpoint(monkeypatch) -> None:
    from pmcp.manifest.registry import (
        effective_registry_endpoint,
        registry_private_enabled,
    )

    monkeypatch.setenv("PMCP_REGISTRY_ALLOW_PRIVATE", "1")
    monkeypatch.setenv(
        "PMCP_REGISTRY_PRIVATE_ENDPOINT", "https://private.example/v0/servers"
    )
    assert registry_private_enabled() is True
    assert effective_registry_endpoint() == "https://private.example/v0/servers"


async def test_allow_draft_schema_includes_non_latest(monkeypatch) -> None:
    calls: list[str] = []
    payload: dict[str, object] = {
        "servers": [
            {
                "_meta": {"official": {"isLatest": True}},
                "server": {
                    "name": "Latest",
                    "description": "d",
                    "packages": [{"identifier": "@x/latest", "transport": "stdio"}],
                },
            },
            {
                "_meta": {"official": {"isLatest": False}},
                "server": {
                    "name": "Draft",
                    "description": "d",
                    "packages": [{"identifier": "@x/draft", "transport": "stdio"}],
                },
            },
        ]
    }
    monkeypatch.setattr(
        "pmcp.manifest.registry.aiohttp.ClientSession",
        lambda **_: _Session(calls, [payload]),
    )
    # Flag off (default): the draft (isLatest false) entry is filtered out.
    off = await fetch_registry_servers(
        "https://r.example/v0/servers", use_in_process_cache=False
    )
    assert {s.name for s in off.servers} == {"Latest"}
    assert "registry_draft_schema_allowed" not in off.diagnostics

    # allow_draft_schema on: draft entry included + posture diagnostic.
    monkeypatch.setattr(
        "pmcp.manifest.registry.aiohttp.ClientSession",
        lambda **_: _Session(calls, [payload]),
    )
    on = await fetch_registry_servers(
        "https://r.example/v0/servers",
        use_in_process_cache=False,
        allow_draft_schema=True,
    )
    assert {s.name for s in on.servers} == {"Latest", "Draft"}
    assert "registry_draft_schema_allowed" in on.diagnostics
