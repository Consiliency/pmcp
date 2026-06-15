"""Tests for manifest-layer MCP Registry parsing and caching."""

from __future__ import annotations

from pathlib import Path

from pmcp.manifest.registry import (
    RegistryCache,
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
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        import json

        return json.dumps(RECORDED_PAYLOAD).encode()


def test_fetch_registry_servers_parses_preview_schema(monkeypatch) -> None:
    calls = []

    def fake_urlopen(url: str, timeout: float) -> _Response:
        calls.append((url, timeout))
        return _Response()

    monkeypatch.setattr("pmcp.manifest.registry.urlopen", fake_urlopen)

    cache = fetch_registry_servers("https://registry.example/v0/servers", timeout=1.5)

    assert calls == [("https://registry.example/v0/servers", 1.5)]
    assert cache.source_endpoint == "https://registry.example/v0/servers"
    assert cache.servers[0].packages[0].transport == "streamable-http"
    assert cache.servers[0].packages[0].env_vars == ["GITHUB_TOKEN"]
    assert cache.servers[0].packages[0].raw["unknownPreviewField"] == {"kept": True}
    assert cache.servers[0].raw["server"]["unknownServerField"] == "kept"
    assert all("TOKEN" not in diag for server in cache.servers for diag in server.diagnostics)


def test_fetch_registry_servers_timeout_returns_diagnostic(monkeypatch) -> None:
    def fake_urlopen(url: str, timeout: float) -> _Response:
        raise TimeoutError("network unavailable")

    monkeypatch.setattr("pmcp.manifest.registry.urlopen", fake_urlopen)

    cache = fetch_registry_servers("https://registry.example/v0/servers", timeout=0.1)

    assert cache.servers == []
    assert cache.diagnostics == ["registry_fetch_failed:TimeoutError"]


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
