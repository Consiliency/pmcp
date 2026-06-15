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

    monkeypatch.setattr(
        "pmcp.manifest.registry.aiohttp.ClientSession", failing_session
    )

    cache = await fetch_registry_servers(
        "https://registry.example/v0/servers",
        timeout=0.1,
        use_in_process_cache=False,
    )

    assert cache.servers == []
    assert cache.diagnostics == ["registry_fetch_failed:TimeoutError"]


async def test_fetch_registry_servers_paginates_and_deduplicates(monkeypatch) -> None:
    calls: list[str] = []
    page1 = json.loads(Path("tests/fixtures/registry/v0_servers_page1.json").read_text())
    page2 = json.loads(Path("tests/fixtures/registry/v0_servers_page2.json").read_text())

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
