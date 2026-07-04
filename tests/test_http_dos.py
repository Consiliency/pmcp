"""Transport DoS hardening tests for the HTTP transport (Phase P5A).

Covers:
- pre-session keepalive SSE stream concurrency cap (503 when full) and slot
  release when a stream closes;
- pre-session keepalive stream absolute lifetime (deadline) so it cannot live
  forever;
- request-body size cap enforced *during the read* so a chunked / mislabeled
  POST cannot bypass the header-only Content-Length check.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient


def _make_contract_client(
    auth_token: str | None = None,
    rate_limit_rpm: int = 0,
    **kwargs: object,
) -> TestClient:
    """Create a minimal HTTP app client with the session manager mocked out.

    Mirrors the helper in test_http_transport.py: base_url is loopback so the
    Host header is always allow-listed and Origin/Host checks stay deterministic.
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

        app = create_http_app(
            mock_server,
            auth_token=auth_token,
            rate_limit_rpm=rate_limit_rpm,
            **kwargs,
        )
        return TestClient(
            app, base_url="http://127.0.0.1", raise_server_exceptions=False
        )


class TestKeepaliveStreamCap:
    """Concurrency cap and slot release for pre-session keepalive SSE streams."""

    def test_pre_session_stream_rejected_when_cap_reached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pmcp.transport import http as http_mod

        monkeypatch.setenv("PMCP_MAX_KEEPALIVE_STREAMS", "2")
        http_mod._keepalive_active["count"] = 2  # simulate two open streams
        try:
            client = _make_contract_client()
            response = client.get("/mcp")  # pre-session GET (no mcp-session-id)
            assert response.status_code == 503
        finally:
            http_mod._keepalive_active["count"] = 0

    def test_stream_releases_slot_after_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pmcp.transport import http as http_mod

        # Tiny lifetime so the infinite stream terminates quickly and the
        # TestClient (which consumes the whole body) does not hang.
        monkeypatch.setenv("PMCP_KEEPALIVE_MAX_SECONDS", "0.3")
        http_mod._keepalive_active["count"] = 0

        client = _make_contract_client()
        response = client.get("/mcp")

        assert response.status_code == 200
        assert "keep-alive" in response.text
        # finally-block in the generator ran during body consumption.
        assert http_mod._keepalive_active["count"] == 0


class TestBodySizeCap:
    """Body-size cap must be enforced during the read, not header-only."""

    def test_chunked_post_over_cap_rejected_during_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pmcp.transport import http as http_mod

        monkeypatch.setattr(http_mod, "_MAX_BODY_BYTES", 1024)
        client = _make_contract_client(auth_token="secret")

        def gen() -> object:
            # 2048 bytes total, streamed with no Content-Length (chunked).
            for _ in range(4):
                yield b"x" * 512

        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer secret"},
            content=gen(),
        )

        assert response.status_code == 413

    def test_content_length_over_cap_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pmcp.transport import http as http_mod

        monkeypatch.setattr(http_mod, "_MAX_BODY_BYTES", 1024)
        client = _make_contract_client(auth_token="secret")

        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer secret"},
            content=b"x" * 2048,
        )

        assert response.status_code == 413

    def test_small_chunked_post_still_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The capped read must not break normal chunked request bodies."""
        client = _make_contract_client(auth_token="secret")

        def gen() -> object:
            yield b'{"jsonrpc":"2.0","method":"notifications/initialized"}'

        response = client.post(
            "/mcp",
            headers={"Authorization": "Bearer secret"},
            content=gen(),
        )

        assert response.status_code == 202
