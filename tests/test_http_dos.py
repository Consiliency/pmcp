"""Transport DoS hardening tests for the HTTP transport (Phase P5A / P3B).

Covers:
- subscriptions/listen stream concurrency cap (rejected before ack when full)
  and slot release when a stream's client connection closes -- the P3B
  (SL-4) one-for-one replacement for the retired pre-session keepalive
  concurrency cap this module used to test (IF-0-P3B-3: the pre-session GET
  keep-alive shim, including its `PMCP_MAX_KEEPALIVE_STREAMS` concurrency
  cap and `PMCP_KEEPALIVE_MAX_SECONDS` absolute lifetime, is retired
  entirely -- see `plans/phase-plan-v11-P3B.md`, IF-0-P3B-3. The lifetime
  cap is *deliberately not* replaced: a subscription is long-lived by
  design, so there is no lifetime-deadline test here to re-point -- what
  bounds exposure now is this concurrency cap, the SDK's own per-stream
  `max_buffered_events` backlog cap, and `/mcp` auth-gating);
- request-body size cap enforced *during the read* so a chunked / mislabeled
  POST cannot bypass the header-only Content-Length check.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import uvicorn
from mcp.server import Server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.inbound import MCP_METHOD_HEADER, MCP_PROTOCOL_VERSION_HEADER
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY
from mcp.types.version import LATEST_MODERN_VERSION
from starlette.testclient import TestClient

from pmcp.transport.http import create_http_app


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


def _alloc_port() -> int:
    """A real ephemeral-port allocator -- never a hardcoded literal."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _listen_envelope(
    *, notifications: dict[str, bool], request_id: int
) -> tuple[dict[str, str], dict]:
    """IF-0-P3B-4 wire envelope for a subscriptions/listen POST. Wire
    (camelCase) `notifications` -- only JSON test payloads use the alias."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        MCP_PROTOCOL_VERSION_HEADER: LATEST_MODERN_VERSION,
        MCP_METHOD_HEADER: "subscriptions/listen",
    }
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "subscriptions/listen",
        "params": {
            "notifications": notifications,
            "_meta": {
                PROTOCOL_VERSION_META_KEY: LATEST_MODERN_VERSION,
                CLIENT_CAPABILITIES_META_KEY: {},
            },
        },
    }
    return headers, body


async def _next_data_frame(lines: AsyncIterator[str], *, timeout: float = 10.0) -> dict:
    """Next `data:` frame from an SSE line iterator, skipping `: ping`
    comment lines and blanks (IF-0-P3B-4)."""

    async def _read() -> dict:
        async for line in lines:
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())  # noqa: E203
        raise AssertionError("stream ended before a data: frame arrived")

    return await asyncio.wait_for(_read(), timeout=timeout)


@contextlib.asynccontextmanager
async def _run_listen_app(*, max_subscriptions: int) -> AsyncIterator[str]:
    """Serve `create_http_app` over a real `ListenHandler(max_subscriptions=...)`
    -- the deliberate SL-4 replacement for the retired pre-session keepalive
    concurrency guard -- as a background asyncio task on a spare port, for
    the duration of the `async with` block. Yields the base URL."""
    bus = InMemorySubscriptionBus()
    listen_handler = ListenHandler(bus, max_subscriptions=max_subscriptions)
    server = Server("http-dos-test", on_subscriptions_listen=listen_handler)
    app = create_http_app(server)
    port = _alloc_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    uv_server = uvicorn.Server(config)
    task = asyncio.create_task(uv_server.serve())
    try:
        for _ in range(200):
            if uv_server.started:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("listen app never started")
        yield f"http://127.0.0.1:{port}"
    finally:
        uv_server.should_exit = True
        await task


class TestListenStreamCap:
    """Concurrency cap and slot release for `subscriptions/listen` streams
    (`PMCP_MAX_LISTEN_STREAMS` -> `ListenHandler(max_subscriptions=...)`),
    the one-for-one SL-4 replacement for this class's former pre-session
    keepalive stream cap coverage -- see IF-0-P3B-3."""

    async def test_listen_stream_rejected_when_cap_reached(self) -> None:
        """With the cap at 1, a second concurrent listen -- opened while the
        first is still live -- is rejected (a JSON-RPC error, never an ack)
        over the real deployed HTTP wire, not just the in-process duplex."""
        async with _run_listen_app(max_subscriptions=1) as base_url:
            headers_a, body_a = _listen_envelope(
                notifications={"toolsListChanged": True}, request_id=1
            )
            async with (
                httpx.AsyncClient(timeout=None) as client_a,
                client_a.stream(
                    "POST", f"{base_url}/mcp", headers=headers_a, json=body_a
                ) as response_a,
            ):
                assert response_a.status_code == 200
                ack = await _next_data_frame(response_a.aiter_lines())
                assert ack["method"] == "notifications/subscriptions/acknowledged"

                headers_b, body_b = _listen_envelope(
                    notifications={"toolsListChanged": True}, request_id=2
                )
                async with httpx.AsyncClient(timeout=10.0) as client_b:
                    response_b = await client_b.post(
                        f"{base_url}/mcp", headers=headers_b, json=body_b
                    )
                payload = response_b.json()
                assert "error" in payload, (
                    f"second listen must be rejected while the cap is full, not acked: {payload}"
                )

    async def test_listen_stream_releases_slot_after_client_close(self) -> None:
        """Closing the first subscription's connection frees its slot, so a
        second subscription -- previously rejected while the cap was full --
        is acked once the first disconnects."""
        async with _run_listen_app(max_subscriptions=1) as base_url:
            headers_a, body_a = _listen_envelope(
                notifications={"toolsListChanged": True}, request_id=1
            )
            client_a = httpx.AsyncClient(timeout=None)
            try:
                async with client_a.stream(
                    "POST", f"{base_url}/mcp", headers=headers_a, json=body_a
                ) as response_a:
                    assert response_a.status_code == 200
                    ack = await _next_data_frame(response_a.aiter_lines())
                    assert ack["method"] == "notifications/subscriptions/acknowledged"
            finally:
                # A real disconnect (not a pooled return) so the server's
                # watch_disconnect sees http.disconnect and frees the slot.
                await client_a.aclose()

            headers_b, body_b = _listen_envelope(
                notifications={"toolsListChanged": True}, request_id=2
            )
            deadline = time.monotonic() + 10.0
            async with httpx.AsyncClient(timeout=None) as client_b:
                while time.monotonic() < deadline:
                    async with client_b.stream(
                        "POST", f"{base_url}/mcp", headers=headers_b, json=body_b
                    ) as response_b:
                        try:
                            message = await asyncio.wait_for(
                                _next_data_frame(response_b.aiter_lines()), timeout=3.0
                            )
                        except (TimeoutError, AssertionError):
                            message = None
                        if (
                            message is not None
                            and message.get("method")
                            == "notifications/subscriptions/acknowledged"
                        ):
                            return
                    await asyncio.sleep(0.2)
            pytest.fail(
                "subscription B was never acked after A's client disconnect "
                "(max_subscriptions=1)"
            )


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
