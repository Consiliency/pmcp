"""SL-4.1 — EC-P3B-3 route-table unit coverage: GET /mcp is retired.

Covers IF-0-P3B-3's route-table half: the ``/mcp`` ``Route``'s ``methods``
drops ``"GET"`` (``DELETE`` retained), Starlette answers a bare ``GET /mcp``
with ``405`` and ``Allow: POST, DELETE`` rather than the hang measured spike
2b found (pmcp's own rmcp-compat pre-session keep-alive branch answering a
session-less GET with an infinite SSE stream before the session manager ever
saw the request). ``/health`` and ``/metrics`` are separate ``Route``
objects and must be structurally untouched by the retirement.

This module drives ``create_http_app`` directly with Starlette's
``TestClient`` (synchronous, in-process) — it does not need a real session
manager or a live event loop, unlike ``tests/mcp2x/test_listen_over_http.py``
(SL-4.2), which proves the timeout-exemption and client-close halves of
IF-0-P3B-3 over a real uvicorn server.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from starlette.routing import Route
from starlette.testclient import TestClient


def _make_client() -> TestClient:
    """A ``create_http_app`` TestClient with the session manager mocked out.

    Mirrors ``tests/test_http_dos.py``'s ``_make_contract_client`` — this
    module owns no production code, only route-table and diagnostics
    evidence for ``src/pmcp/transport/http.py`` (SL-4-owned).
    """
    from pmcp.transport.http import create_http_app

    mock_server = MagicMock()
    mock_server.create_initialization_options = MagicMock(return_value={})

    with patch(
        "pmcp.transport.http.StreamableHTTPSessionManager",
        autospec=True,
    ) as mock_manager:
        instance = mock_manager.return_value
        instance.run.return_value.__aenter__ = AsyncMock(return_value=None)
        instance.run.return_value.__aexit__ = AsyncMock(return_value=False)
        instance.handle_request = AsyncMock(return_value=None)

        app = create_http_app(mock_server)
        client = TestClient(
            app, base_url="http://127.0.0.1", raise_server_exceptions=False
        )
        client.app = app  # type: ignore[attr-defined]  # stash for route-table assertions
        return client


class TestGetRetired:
    """GET /mcp answers 405 with Allow rather than hanging."""

    def test_get_mcp_is_405(self) -> None:
        client = _make_client()
        response = client.get(
            "/mcp",
            headers={
                "Accept": "text/event-stream",
                "MCP-Protocol-Version": "2026-07-28",
            },
        )
        assert response.status_code == 405

    def test_get_mcp_allow_header_names_post_and_delete_not_get(self) -> None:
        client = _make_client()
        response = client.get("/mcp")
        allow = response.headers["allow"]
        allowed = {method.strip() for method in allow.split(",")}
        assert "POST" in allowed, allow
        assert "DELETE" in allowed, allow
        assert "GET" not in allowed, allow

    def test_mcp_route_methods_is_exactly_post_delete(self) -> None:
        """Not ``{"POST", "DELETE", "HEAD"}`` — Starlette only synthesises
        HEAD alongside GET, so once GET is gone HEAD goes with it. Asserting
        the exact set (not just "GET absent from a list") is what proves GET
        is actually gone rather than merely unlisted."""
        client = _make_client()
        mcp_route = next(
            route
            for route in client.app.routes  # type: ignore[attr-defined]
            if isinstance(route, Route) and route.path == "/mcp"
        )
        assert mcp_route.methods == {"POST", "DELETE"}, mcp_route.methods


class TestHealthAndMetricsSurviveRetirement:
    """/health and /metrics are separate Route objects, untouched by SL-4."""

    def test_health_still_200_with_expected_shape(self) -> None:
        client = _make_client()
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert "version" in body
        assert body["transport"] == "http"
        assert "gateway_diagnostics" in body

    def test_metrics_still_200_prometheus_content_type(self) -> None:
        client = _make_client()
        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]


class TestDiagnosticsLiteral:
    """IF-0-P3B-3's session_compatibility diagnostics key change."""

    def test_pre_session_get_key_gone_get_stream_retired(self) -> None:
        client = _make_client()
        diagnostics = client.app.state.gateway_diagnostics  # type: ignore[attr-defined]
        assert "pre_session_get" not in diagnostics.session_compatibility
        assert diagnostics.session_compatibility["get_stream"] == "retired"

    def test_health_body_reflects_the_same_diagnostics(self) -> None:
        client = _make_client()
        body = client.get("/health").json()
        session_compat = body["gateway_diagnostics"]["session_compatibility"]
        assert "pre_session_get" not in session_compat
        assert session_compat["get_stream"] == "retired"


class TestKeepaliveSymbolsRemoved:
    """The deleted DoS-guard module-level state must actually be gone, not
    just unreferenced — a rename or a stray reintroduction would defeat a
    grep-only check."""

    def test_keepalive_module_symbols_no_longer_exist(self) -> None:
        from pmcp.transport import http as http_mod

        for name in (
            "_keepalive_active",
            "_DEFAULT_MAX_KEEPALIVE_STREAMS",
            "_DEFAULT_KEEPALIVE_MAX_SECONDS",
            "_KEEPALIVE_HEARTBEAT_SECONDS",
        ):
            assert not hasattr(http_mod, name), (
                f"{name} still exists on pmcp.transport.http"
            )
