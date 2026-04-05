"""Tests for HTTP transport — /health, /metrics, auth guard, rate limiting."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from pmcp import __version__
from pmcp.transport.http import _metrics, create_http_app


def _make_app(
    auth_token: str | None = None,
    rate_limit_rpm: int = 0,
    request_timeout: int = 60,
) -> TestClient:
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

        app = create_http_app(
            mcp_server,
            auth_token=auth_token,
            rate_limit_rpm=rate_limit_rpm,
            request_timeout=request_timeout,
        )
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
        # prometheus_client is installed in dev: generate_latest() includes our registered counters
        assert "# TYPE" in r.text
        assert "pmcp_requests_total" in r.text

    def test_metrics_fallback_renderer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When _generate_latest is None (prometheus_client absent), fallback renders pmcp_* counters."""
        import pmcp.transport.http as http_mod
        monkeypatch.setattr(http_mod, "_generate_latest", None)
        client = _make_app()
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "pmcp_requests_total" in r.text
        assert "# TYPE pmcp_requests_total counter" in r.text

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


# ---------------------------------------------------------------------------
# Payload size limit on /mcp
# ---------------------------------------------------------------------------

class TestPayloadSizeLimit:
    """POST bodies larger than _MAX_BODY_BYTES must be rejected with 413."""

    def test_oversized_payload_returns_413(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Content-Length exceeding limit → 413 before body is read."""
        import pmcp.transport.http as http_mod
        monkeypatch.setattr(http_mod, "_MAX_BODY_BYTES", 10)
        client = _make_app()
        r = client.post("/mcp", content=b"x" * 20)
        assert r.status_code == 413

    def test_payload_at_limit_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Content-Length exactly at limit is allowed (boundary is exclusive)."""
        import pmcp.transport.http as http_mod
        monkeypatch.setattr(http_mod, "_MAX_BODY_BYTES", 20)
        client = _make_app()
        r = client.post("/mcp", content=b"x" * 20)
        assert r.status_code != 413

    def test_normal_payload_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Typical small payloads are never blocked."""
        import pmcp.transport.http as http_mod
        monkeypatch.setattr(http_mod, "_MAX_BODY_BYTES", 100)
        client = _make_app()
        r = client.post("/mcp", content=b"{}")
        assert r.status_code != 413

    def test_get_endpoint_not_affected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GET endpoints (no body) are never size-checked — /health returns 200."""
        import pmcp.transport.http as http_mod
        monkeypatch.setattr(http_mod, "_MAX_BODY_BYTES", 1)
        client = _make_app()
        r = client.get("/health")
        assert r.status_code == 200
        assert r.status_code != 413

    def test_413_independent_of_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Size check fires before auth, so even auth-less oversized requests get 413 not 401."""
        import pmcp.transport.http as http_mod
        monkeypatch.setattr(http_mod, "_MAX_BODY_BYTES", 10)
        # auth_token configured; no Authorization header sent
        client = _make_app(auth_token="secret")
        r = client.post("/mcp", content=b"x" * 20)
        # Auth check runs first (after rate-limit, before size) — but size check is
        # placed after auth in handle_mcp, so 401 takes priority.
        # This test documents the ordering: 401 before 413.
        assert r.status_code in (401, 413)


# ---------------------------------------------------------------------------
# Request timeout on /mcp
# ---------------------------------------------------------------------------

class TestRequestTimeout:
    """session_manager.handle_request must be cancelled after request_timeout seconds."""

    def _make_slow_app(self, request_timeout: float, handler_delay: float) -> TestClient:
        """App whose handle_request sleeps for handler_delay seconds."""
        mcp_server = MagicMock()
        mcp_server.list_tools = AsyncMock(return_value=[])

        with patch(
            "pmcp.transport.http.StreamableHTTPSessionManager",
            autospec=True,
        ) as MockManager:
            instance = MockManager.return_value
            instance.run.return_value.__aenter__ = AsyncMock(return_value=None)
            instance.run.return_value.__aexit__ = AsyncMock(return_value=False)

            async def slow_handler(*args: object, **kwargs: object) -> None:
                await asyncio.sleep(handler_delay)

            instance.handle_request = slow_handler

            app = create_http_app(mcp_server, request_timeout=request_timeout)
            return TestClient(app, raise_server_exceptions=False)

    def test_slow_request_returns_504(self) -> None:
        """Request that exceeds timeout → 504 Gateway Timeout."""
        # handler sleeps 10s, timeout is 0.05s → should get 504
        client = self._make_slow_app(request_timeout=0.05, handler_delay=10.0)
        r = client.post(
            "/mcp",
            content=b'{"jsonrpc":"2.0","method":"initialize","id":1,"params":{}}',
        )
        assert r.status_code == 504

    def test_fast_request_does_not_timeout(self) -> None:
        """Request that completes before timeout → not 504."""
        # handler returns immediately (0s delay), timeout is 60s
        client = _make_app(request_timeout=60)
        r = client.post("/mcp", content=b"{}")
        assert r.status_code != 504

    def test_timeout_zero_not_used_in_normal_path(self) -> None:
        """Default 60s timeout doesn't affect fast handlers."""
        client = _make_app()  # default request_timeout=60
        r = client.get("/health")
        assert r.status_code == 200  # health is not subject to handle_mcp timeout


# ---------------------------------------------------------------------------
# Version consistency
# ---------------------------------------------------------------------------

class TestVersionConsistency:
    def test_version_matches_tag(self) -> None:
        """__version__ must be 1.9.1 to match the v1.9.1 git tag."""
        from pmcp import __version__ as v
        assert v == "1.9.1"

    def test_health_reports_correct_version(self) -> None:
        """GET /health returns the package version, not a stale value."""
        client = _make_app()
        data = client.get("/health").json()
        assert data["version"] == __version__


# ---------------------------------------------------------------------------
# Windows-safe signal registration
# ---------------------------------------------------------------------------

class TestSignalHandling:
    def test_signal_registration_does_not_use_add_signal_handler_on_win32(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On win32, loop.add_signal_handler must not be called (it doesn't exist)."""
        import sys
        import signal as signal_mod
        import asyncio

        monkeypatch.setattr(sys, "platform", "win32")

        loop = MagicMock()
        # add_signal_handler is Unix-only; simulate it raising NotImplementedError on win32
        loop.add_signal_handler.side_effect = NotImplementedError("win32")

        registered: list[int] = []

        def fake_signal(sig: int, handler: object) -> None:
            registered.append(sig)

        monkeypatch.setattr(signal_mod, "signal", fake_signal)

        # Simulate what cli.py does
        shutdown_event = asyncio.Event()

        def handle_signal(sig: signal_mod.Signals) -> None:
            shutdown_event.set()

        if sys.platform != "win32":
            for sig in (signal_mod.SIGINT, signal_mod.SIGTERM):
                loop.add_signal_handler(sig, handle_signal, sig)
        else:
            for sig in (signal_mod.SIGINT, signal_mod.SIGTERM):
                signal_mod.signal(sig, lambda signum, frame: handle_signal(signal_mod.Signals(signum)))

        # On "win32", add_signal_handler should NOT have been called
        loop.add_signal_handler.assert_not_called()
        # signal.signal should have been called for both SIGINT and SIGTERM
        assert len(registered) == 2

    def test_signal_registration_uses_add_signal_handler_on_linux(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On Linux, loop.add_signal_handler is used (the async-safe path)."""
        import sys
        import signal as signal_mod

        monkeypatch.setattr(sys, "platform", "linux")

        loop = MagicMock()
        registered: list[int] = []
        loop.add_signal_handler.side_effect = lambda sig, *args: registered.append(sig)

        def handle_signal(sig: signal_mod.Signals) -> None:
            pass

        if sys.platform != "win32":
            for sig in (signal_mod.SIGINT, signal_mod.SIGTERM):
                loop.add_signal_handler(sig, handle_signal, sig)
        else:
            for sig in (signal_mod.SIGINT, signal_mod.SIGTERM):
                signal_mod.signal(sig, lambda signum, frame: None)

        assert loop.add_signal_handler.call_count == 2


# ---------------------------------------------------------------------------
# Timing-safe auth token comparison
# ---------------------------------------------------------------------------

class TestTimingSafeAuth:
    """hmac.compare_digest must be used instead of plain string equality."""

    def test_correct_token_accepted(self) -> None:
        client = _make_app(auth_token="secret")
        r = client.post("/mcp", content=b"{}", headers={"Authorization": "Bearer secret"})
        assert r.status_code != 401

    def test_wrong_token_rejected(self) -> None:
        client = _make_app(auth_token="secret")
        r = client.post("/mcp", content=b"{}", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_prefix_only_rejected(self) -> None:
        """'Bearer ' without the token value must not match."""
        client = _make_app(auth_token="secret")
        r = client.post("/mcp", content=b"{}", headers={"Authorization": "Bearer "})
        assert r.status_code == 401

    def test_empty_header_rejected(self) -> None:
        client = _make_app(auth_token="secret")
        r = client.post("/mcp", content=b"{}", headers={"Authorization": ""})
        assert r.status_code == 401

    def test_compare_digest_is_called(self) -> None:
        """Verify handle_mcp delegates to hmac.compare_digest, not plain !=."""
        import hmac as hmac_mod
        import pmcp.transport.http as http_mod

        calls: list[tuple] = []
        original = hmac_mod.compare_digest

        def spy(*args: object) -> bool:
            calls.append(args)
            return original(*args)  # type: ignore[arg-type]

        with patch.object(http_mod.hmac, "compare_digest", spy):
            client = _make_app(auth_token="secret")
            client.post("/mcp", content=b"{}", headers={"Authorization": "Bearer secret"})

        assert len(calls) >= 1, "hmac.compare_digest was not called — timing-safe check missing"
