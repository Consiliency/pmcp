"""SSRF and registry-hardening tests for P5B.

Covers redirect refusal in JWKS/metadata fetches, the registry response-size
guard enforced during the read, private-endpoint validation, and atomic,
owner-only registry cache writes.
"""

from __future__ import annotations

import http.client
import io
import stat
from collections.abc import AsyncIterator
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from pmcp.auth import (
    _NO_REDIRECT_OPENER,
    AsyncJWKS,
    ResourceServerJWKSUnavailable,
    _NoRedirectHandler,
    fetch_json_metadata,
)
from pmcp.manifest.registry import (
    DEFAULT_REGISTRY_ENDPOINT,
    RegistryCache,
    effective_registry_endpoint,
    fetch_registry_servers,
    load_registry_cache,
    save_registry_cache,
)

INTERNAL_HOST = "169.254.169.254"


# --------------------------------------------------------------------------
# 1. JWKS redirect is not followed (aiohttp path)
# --------------------------------------------------------------------------
class _RedirectResponse:
    def __init__(self, status: int, headers: dict[str, str]) -> None:
        self.status = status
        self.headers = headers

    async def __aenter__(self) -> _RedirectResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:  # pragma: no cover - not reached on 3xx
        raise AssertionError("raise_for_status must not run on a refused redirect")


class _RecordingSession:
    def __init__(self, calls: list[tuple[str, dict[str, object]]]) -> None:
        self._calls = calls

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> _RedirectResponse:
        self._calls.append((url, kwargs))
        return _RedirectResponse(302, {"Location": f"http://{INTERNAL_HOST}/"})


async def test_jwks_fetch_refuses_redirect_to_internal_host(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        "pmcp.auth.aiohttp.ClientSession",
        lambda **_: _RecordingSession(calls),
    )

    jwks = AsyncJWKS("https://issuer.example/jwks.json")
    with pytest.raises(ResourceServerJWKSUnavailable):
        await jwks.get()

    # Exactly one request, to the public host, with redirects disabled.
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert kwargs.get("allow_redirects") is False
    assert INTERNAL_HOST not in url


# --------------------------------------------------------------------------
# 2. metadata redirect is not followed (urllib path)
# --------------------------------------------------------------------------
def test_no_redirect_handler_raises_on_redirect() -> None:
    handler = _NoRedirectHandler()
    req = Request("https://public.example/meta")
    with pytest.raises(HTTPError):
        handler.redirect_request(
            req,
            io.BytesIO(b""),
            302,
            "Found",
            http.client.HTTPMessage(),
            f"http://{INTERNAL_HOST}/latest/meta-data/",
        )


def test_metadata_opener_replaces_default_redirect_handler() -> None:
    redirect_handlers = [
        handler
        for handler in _NO_REDIRECT_OPENER.handlers
        if isinstance(handler, _NoRedirectHandler)
    ]
    # Our no-redirect handler is wired, and no permissive default remains.
    assert redirect_handlers
    from urllib.request import HTTPRedirectHandler

    assert all(
        isinstance(handler, _NoRedirectHandler)
        for handler in _NO_REDIRECT_OPENER.handlers
        if isinstance(handler, HTTPRedirectHandler)
    )


def test_fetch_json_metadata_does_not_follow_redirect(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def fake_urlopen(request: object, timeout: float) -> object:
        seen["url"] = getattr(request, "full_url")
        raise HTTPError(
            seen["url"],
            302,
            "Redirects are not allowed.",
            http.client.HTTPMessage(),
            None,
        )

    monkeypatch.setattr("pmcp.auth.urlopen", fake_urlopen)

    data, error = fetch_json_metadata("https://auth.example/meta?token=secret")

    assert data is None
    assert error is not None
    assert "secret" not in error
    # Only the initial public host is ever contacted.
    assert INTERNAL_HOST not in seen["url"]


# --------------------------------------------------------------------------
# 3. Registry response-size guard is enforced during the read
# --------------------------------------------------------------------------
class _SizedContent:
    def __init__(self, chunks: list[bytes], consumed: list[bytes]) -> None:
        self._chunks = chunks
        self._consumed = consumed

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self._consumed.append(chunk)
            yield chunk


class _SizedResponse:
    def __init__(
        self,
        *,
        content_length: int | None,
        chunks: list[bytes],
        consumed: list[bytes],
    ) -> None:
        self.content_length = content_length
        self.content = _SizedContent(chunks, consumed)

    async def __aenter__(self) -> _SizedResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def read(self) -> bytes:  # pragma: no cover - must not be called
        raise AssertionError("must not buffer the whole oversized body")


class _SizedSession:
    def __init__(self, response: _SizedResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _SizedSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def get(self, url: str) -> _SizedResponse:
        return self._response


async def test_registry_rejects_oversized_content_length(monkeypatch) -> None:
    consumed: list[bytes] = []
    response = _SizedResponse(
        content_length=5_000_000, chunks=[b"x" * 1000], consumed=consumed
    )
    monkeypatch.setattr(
        "pmcp.manifest.registry.aiohttp.ClientSession",
        lambda **_: _SizedSession(response),
    )

    cache = await fetch_registry_servers(
        "https://registry.example/v0/servers",
        max_response_bytes=2_000_000,
        use_in_process_cache=False,
    )

    assert "registry_response_size_cap" in cache.diagnostics
    assert cache.servers == []
    # An advertised over-cap length short-circuits before any body is streamed.
    assert consumed == []


async def test_registry_aborts_streamed_body_over_cap(monkeypatch) -> None:
    consumed: list[bytes] = []
    chunks = [b"aa", b"bb", b"cc", b"dd"]
    response = _SizedResponse(content_length=None, chunks=chunks, consumed=consumed)
    monkeypatch.setattr(
        "pmcp.manifest.registry.aiohttp.ClientSession",
        lambda **_: _SizedSession(response),
    )

    cache = await fetch_registry_servers(
        "https://registry.example/v0/servers",
        max_response_bytes=3,
        use_in_process_cache=False,
    )

    assert "registry_response_size_cap" in cache.diagnostics
    assert cache.servers == []
    # Aborted mid-stream: not every chunk was consumed.
    assert len(consumed) < len(chunks)


# --------------------------------------------------------------------------
# 4. Private registry endpoint validation
# --------------------------------------------------------------------------
def _enable_private(monkeypatch, endpoint: str) -> None:
    monkeypatch.setenv("PMCP_REGISTRY_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("PMCP_REGISTRY_PRIVATE_ENDPOINT", endpoint)


def test_private_endpoint_allows_https(monkeypatch) -> None:
    _enable_private(monkeypatch, "https://private.example/v0/servers")
    assert effective_registry_endpoint() == "https://private.example/v0/servers"


def test_private_endpoint_allows_loopback_http(monkeypatch) -> None:
    _enable_private(monkeypatch, "http://localhost:8080/v0/servers")
    assert effective_registry_endpoint() == "http://localhost:8080/v0/servers"


def test_private_endpoint_allows_loopback_ip_http(monkeypatch) -> None:
    _enable_private(monkeypatch, "http://127.0.0.1:8080/v0/servers")
    assert effective_registry_endpoint() == "http://127.0.0.1:8080/v0/servers"


def test_private_endpoint_rejects_non_https_public(monkeypatch) -> None:
    _enable_private(monkeypatch, "http://private.example/v0/servers")
    assert effective_registry_endpoint() == DEFAULT_REGISTRY_ENDPOINT


def test_private_endpoint_rejects_link_local_metadata(monkeypatch) -> None:
    _enable_private(monkeypatch, f"https://{INTERNAL_HOST}/v0/servers")
    assert effective_registry_endpoint() == DEFAULT_REGISTRY_ENDPOINT


def test_private_endpoint_rejects_missing_scheme(monkeypatch) -> None:
    _enable_private(monkeypatch, "private.example/v0/servers")
    assert effective_registry_endpoint() == DEFAULT_REGISTRY_ENDPOINT


# --------------------------------------------------------------------------
# 5. Registry cache is written atomically with 0600 permissions
# --------------------------------------------------------------------------
def _sample_cache() -> RegistryCache:
    return RegistryCache(
        schema_version="registry-cache.v1",
        source_endpoint="https://registry.example/v0/servers",
        fetched_at="2026-01-01T00:00:00Z",
    )


def test_save_registry_cache_is_atomic_and_owner_only(tmp_path) -> None:
    path = tmp_path / "sub" / "registry-cache.json"
    save_registry_cache(_sample_cache(), path)

    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # No temp file is left behind after the atomic replace.
    leftovers = [p.name for p in path.parent.iterdir() if p.name != path.name]
    assert leftovers == []
    # Content is complete and round-trips.
    loaded = load_registry_cache(path)
    assert loaded is not None
    assert loaded.source_endpoint == "https://registry.example/v0/servers"


def test_save_registry_cache_tightens_existing_perms(tmp_path) -> None:
    path = tmp_path / "registry-cache.json"
    path.write_text("{}")
    path.chmod(0o644)

    save_registry_cache(_sample_cache(), path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
