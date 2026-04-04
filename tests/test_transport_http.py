"""Tests for HTTP transport — /health, /metrics, auth guard, rate limiting."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from pmcp import __version__
from pmcp.transport.http import _metrics, create_http_app


def _make_app(auth_token: str | None = None, rate_limit_rpm: int = 0) -> TestClient:
    """Create a TestClient wrapping a minimal create_http_app instance."""
    mcp_server = MagicMock()
    mcp_server.list_tools = AsyncMock(return_value=[])

    # Patch out the StreamableHTTPSessionManager lifespan so TestClient doesn't
    # need a real MCP server running.
    with patch(
        "pmcp.transport.http.StreamableHTTPSessionManager",
        autospec=True,
    ) as MockManager:
        instance = MockManager.return_value
        instance.run.return_value.__aenter__ = AsyncMock(return_value=None)
        instance.run.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.handle_request = AsyncMock(return_value=None)

        app = create_http_app(mcp_server, auth_token=auth_token, rate_limit_rpm=rate_limit_rpm)
        return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_200(self) -> None:
        client = _make_app()
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["version"] == __version__
        assert data["transport"] == "http"

    def test_health_unauthenticated_even_when_auth_configured(self) -> None:
        """Health endpoint must not require auth — load balancers won't have tokens."""
        client = _make_app(auth_token="secret")
        r = client.get("/health")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /metrics
# ---------------------------------------------------------------------------

class TestMetricsEndpoint:
    def test_metrics_returns_200(self) -> None:
        client = _make_app()
        r = client.get("/metrics")
        assert r.status_code == 200
        # Valid Prometheus text format has TYPE lines regardless of which registry is used
        assert "# TYPE" in r.text

    def test_metrics_fallback_without_prometheus_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When prometheus_client is not installed, fallback renders pmcp_* counters."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name == "prometheus_client":
                raise ImportError("mocked absence")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        client = _make_app()
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "pmcp_requests_total" in r.text

    def test_metrics_unauthenticated_even_when_auth_configured(self) -> None:
        """Metrics endpoint must not require auth — Prometheus scrapers won't have tokens."""
        client = _make_app(auth_token="secret")
        r = client.get("/metrics")
        assert r.status_code == 200

    def test_metrics_prometheus_text_format(self) -> None:
        client = _make_app()
        r = client.get("/metrics")
        assert "# TYPE" in r.text


# ---------------------------------------------------------------------------
# Auth guard on /mcp
# ---------------------------------------------------------------------------

class TestAuthGuardHttp:
    def test_no_token_returns_401(self) -> None:
        client = _make_app(auth_token="mysecret")
        r = client.post("/mcp", content=b"{}")
        assert r.status_code == 401

    def test_wrong_token_returns_401(self) -> None:
        client = _make_app(auth_token="mysecret")
        r = client.post("/mcp", content=b"{}", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_correct_token_does_not_return_401(self) -> None:
        client = _make_app(auth_token="mysecret")
        r = client.post(
            "/mcp",
            content=b'{"jsonrpc":"2.0","method":"initialize","id":1,"params":{}}',
            headers={"Authorization": "Bearer mysecret"},
        )
        assert r.status_code != 401

    def test_no_auth_configured_does_not_return_401(self) -> None:
        client = _make_app()
        r = client.post("/mcp", content=b"{}")
        assert r.status_code != 401

    def test_metrics_counter_increments_on_401(self) -> None:
        before = _metrics["requests_401"]
        client = _make_app(auth_token="secret")
        client.get("/mcp")  # GET with no session ID returns keep-alive SSE, skip
        client.post("/mcp", content=b"{}")  # no auth → 401
        assert _metrics["requests_401"] > before


# ---------------------------------------------------------------------------
# Rate limiting on /mcp
# ---------------------------------------------------------------------------

class TestRateLimitHttp:
    def test_rate_limit_allows_under_limit(self) -> None:
        client = _make_app(rate_limit_rpm=10)
        for _ in range(5):
            r = client.post("/mcp", content=b"{}")
            assert r.status_code != 429

    def test_rate_limit_blocks_over_limit(self) -> None:
        from pmcp.transport import http as http_mod

        # Reset store to ensure a clean slate for this IP
        http_mod._rl_store.clear()

        client = _make_app(rate_limit_rpm=3)
        statuses = [
            client.post("/mcp", content=b"{}").status_code for _ in range(5)
        ]
        assert 429 in statuses

    def test_rate_limit_zero_disables(self) -> None:
        client = _make_app(rate_limit_rpm=0)
        for _ in range(20):
            r = client.post("/mcp", content=b"{}")
            assert r.status_code != 429
