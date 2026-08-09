"""SL-5.3 — EC-P3B-3 over the deployed HTTP wire.

Measured spike 2b (`plans/phase-plan-v11-P3B.md`) found that on pre-P3B
code, `GET /mcp` with `Accept: text/event-stream` hangs indefinitely rather
than answering -- pmcp's own rmcp-compat keep-alive shim intercepted it
before the request ever reached the session manager. IF-0-P3B-3 retires
that shim entirely: `/mcp`'s `Route` no longer lists `GET`, so Starlette's
own router answers `405` with `Allow: POST, DELETE` before any pmcp
handler code runs at all.

Every GET here uses a bounded `httpx` timeout -- the bound itself is part
of the assertion, exactly as the plan's V2 shell step does with
`curl --max-time 5`: on today's (pre-P3B) code this would time out rather
than return promptly.
"""

from __future__ import annotations

import httpx
from mcp.types import CallToolResult

from pmcp import __version__
from tests.runtime.harness import BootedGateway, modern_post

_GET_TIMEOUT = 5.0


class TestGetRetired:
    """`GET /mcp` answers 405 promptly, in every header combination a real
    client might send, rather than hanging."""

    def test_get_with_sse_accept_and_protocol_version_is_405(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        response = httpx.get(
            gateway_on_spare_port.mcp_url,
            headers={
                "Accept": "text/event-stream",
                "MCP-Protocol-Version": "2026-07-28",
            },
            timeout=_GET_TIMEOUT,
        )
        assert response.status_code == 405
        assert "POST" in response.headers.get("allow", "")

    def test_get_with_no_accept_header_is_405(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        response = httpx.get(gateway_on_spare_port.mcp_url, timeout=_GET_TIMEOUT)
        assert response.status_code == 405
        assert "POST" in response.headers.get("allow", "")

    def test_get_with_an_mcp_session_id_header_is_405(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        """The retired pre-session shim only fired for a GET with *no*
        session id (a real session-bound GET was legacy-only and reached
        the session manager); after retirement, GET is 405 regardless."""
        response = httpx.get(
            gateway_on_spare_port.mcp_url,
            headers={
                "Accept": "text/event-stream",
                "mcp-session-id": "does-not-exist",
            },
            timeout=_GET_TIMEOUT,
        )
        assert response.status_code == 405
        assert "POST" in response.headers.get("allow", "")

    def test_get_without_session_id_or_accept_is_405(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        response = httpx.get(
            gateway_on_spare_port.mcp_url,
            headers={"Accept": "application/json"},
            timeout=_GET_TIMEOUT,
        )
        assert response.status_code == 405
        assert "POST" in response.headers.get("allow", "")


class TestHealthAndMetricsSurviveRetirement:
    """`/health` and `/metrics` are separate `Route`s and are untouched by
    the `/mcp` GET retirement."""

    def test_health_reports_the_running_version(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        response = httpx.get(
            f"{gateway_on_spare_port.base_url}/health", timeout=_GET_TIMEOUT
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["version"] == __version__

    def test_metrics_reports_requests_total(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        response = httpx.get(
            f"{gateway_on_spare_port.base_url}/metrics", timeout=_GET_TIMEOUT
        )
        assert response.status_code == 200
        assert "text/plain" in response.headers.get("content-type", "")
        assert "pmcp_requests_total" in response.text


class TestPostStillWorksAfterRetirement:
    """The route-table change did not break the live POST method path."""

    def test_tools_call_still_succeeds(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        raw = modern_post(
            gateway_on_spare_port.base_url,
            "tools/call",
            {"name": "gateway.health", "arguments": {}},
            name="gateway.health",
        )
        assert "error" not in raw, raw
        result = CallToolResult.model_validate(raw["result"])
        assert result.is_error is False
