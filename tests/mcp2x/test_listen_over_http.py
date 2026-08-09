"""SL-4.2 — IF-0-P3B-4 over the deployed HTTP wire, plus the two regressions
measured spike 2 found and IF-0-P3B-3 exists to fix.

Runs pmcp's own ``create_http_app`` under uvicorn's programmatic ``Server``
API as an in-process background task -- mirrors
``tests/runtime/fake_remote.py``'s ``run_fake_remote`` (SL-5-owned; read
only, not imported, since it serves a downstream ``MCPServer`` rather than
``create_http_app``) -- with a real ``ListenHandler`` + real
``InMemorySubscriptionBus`` the test also holds a reference to, so it can
drive ``bus.publish(...)`` directly without needing a ``ClientManager``
(SL-3's job) in the loop.

Two tests are the regression evidence and use the exact function names the
acceptance commands select with ``-k``:

- ``test_timeout_exemption_keeps_stream_alive`` — the app is built with
  ``request_timeout=3``; on today's code (before SL-4.3) the stream is
  killed at exactly 3s with a truncated chunked body (measured spike 2).
  This asserts the stream is still alive and still delivering past t>8s.
- ``test_client_close_ends_subscription`` — EC-P3B-2's HTTP client-close
  half, proven observably: with ``max_subscriptions=1``, closing the first
  subscription's connection must free its slot so a second subscription is
  acked. Asserted through public wire behaviour (the second subscription's
  ack), never by reading the SDK-private ``ListenHandler._streams`` — a
  rename there would give a false red rather than a true one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
import uvicorn
from mcp.server import Server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.inbound import MCP_METHOD_HEADER, MCP_PROTOCOL_VERSION_HEADER
from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY, ToolsListChanged
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY
from mcp.types.version import LATEST_MODERN_VERSION

from pmcp.transport.http import create_http_app

pytestmark = pytest.mark.asyncio


def _alloc_port() -> int:
    """A real ephemeral-port allocator — never a hardcoded literal."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _listen_envelope(
    *, notifications: dict[str, bool], request_id: int = 1
) -> tuple[dict[str, str], dict]:
    """Build the IF-0-P3B-4 wire envelope for a subscriptions/listen POST.

    ``notifications`` is passed in wire (camelCase) form, per IF-0-P3B-4 —
    only JSON test payloads use the alias; pmcp's own Python source
    constructs ``SubscriptionFilter`` with field names.
    """
    headers = {
        "Content-Type": "application/json",
        # Both media types required: subscriptions/listen 406s without SSE
        # accept regardless of json_response (IF-0-P3B-4).
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


@dataclass
class RunningListenApp:
    base_url: str
    bus: InMemorySubscriptionBus
    listen_handler: ListenHandler


@contextlib.asynccontextmanager
async def _run_listen_app(
    *, request_timeout: int = 60, max_subscriptions: int = 1024
) -> AsyncIterator[RunningListenApp]:
    """Serve ``create_http_app(<Server with on_subscriptions_listen>)`` on a
    spare port as a background asyncio task on the caller's own event loop,
    for the duration of the ``async with`` block."""
    bus = InMemorySubscriptionBus()
    listen_handler = ListenHandler(bus, max_subscriptions=max_subscriptions)
    server = Server("sl4-listen-test", on_subscriptions_listen=listen_handler)
    app = create_http_app(server, request_timeout=request_timeout)
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
        yield RunningListenApp(
            base_url=f"http://127.0.0.1:{port}", bus=bus, listen_handler=listen_handler
        )
    finally:
        uv_server.should_exit = True
        await task


async def _next_data_frame(lines: AsyncIterator[str], *, timeout: float = 10.0) -> dict:
    """Read the next ``data:`` frame from an SSE line iterator, skipping
    ``: ping`` comment lines and blank lines (IF-0-P3B-4)."""

    async def _read() -> dict:
        async for line in lines:
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())  # noqa: E203
        raise AssertionError("stream ended before a data: frame arrived")

    return await asyncio.wait_for(_read(), timeout=timeout)


async def _first_message(response: httpx.Response, *, timeout: float = 10.0) -> dict:
    """The first decoded JSON-RPC message of a listen response, regardless
    of whether it committed to SSE (a live stream) or resolved as plain
    JSON (an immediate kernel-dispatch error, e.g. subscription limit)."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        return await _next_data_frame(response.aiter_lines(), timeout=timeout)
    content = await asyncio.wait_for(response.aread(), timeout=timeout)
    return json.loads(content)


class TestListenDuplexOverHttp:
    """IF-0-P3B-4: ack first, then a matching bus.publish is delivered,
    stamped with the request's subscriptionId."""

    async def test_ack_is_first_frame_with_subscription_id(self) -> None:
        async with _run_listen_app() as running:
            headers, body = _listen_envelope(
                notifications={"toolsListChanged": True}, request_id=42
            )
            async with (
                httpx.AsyncClient(timeout=None) as client,
                client.stream(
                    "POST", f"{running.base_url}/mcp", headers=headers, json=body
                ) as response,
            ):
                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]
                frame = await _next_data_frame(response.aiter_lines())
                assert frame["method"] == "notifications/subscriptions/acknowledged"
                assert frame["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 42, frame

    async def test_bus_publish_delivers_stamped_notification(self) -> None:
        async with _run_listen_app() as running:
            headers, body = _listen_envelope(
                notifications={"toolsListChanged": True}, request_id=7
            )
            async with (
                httpx.AsyncClient(timeout=None) as client,
                client.stream(
                    "POST", f"{running.base_url}/mcp", headers=headers, json=body
                ) as response,
            ):
                lines = response.aiter_lines()
                ack = await _next_data_frame(lines)
                assert ack["method"] == "notifications/subscriptions/acknowledged"

                await running.bus.publish(ToolsListChanged())

                frame = await _next_data_frame(lines)
                assert frame["method"] == "notifications/tools/list_changed"
                assert frame["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 7

    async def test_accept_json_only_is_406(self) -> None:
        async with _run_listen_app() as running:
            headers, body = _listen_envelope(notifications={"toolsListChanged": True})
            headers["Accept"] = "application/json"
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.post(
                    f"{running.base_url}/mcp", headers=headers, json=body
                )
            assert response.status_code == 406


class TestTimeoutExemption:
    """The measured spike 2 regression: request_timeout must not truncate
    a subscriptions/listen stream."""

    async def test_timeout_exemption_keeps_stream_alive(self) -> None:
        """On today's (pre-SL-4.3) code this app kills the stream at exactly
        3s with a truncated chunked body. With the exemption, the stream
        must still be alive and still delivering a notification published
        well past that timeout — asserted at t > 8s, per IF-0-P3B-3."""
        async with _run_listen_app(request_timeout=3) as running:
            headers, body = _listen_envelope(notifications={"toolsListChanged": True})
            start = time.monotonic()
            async with (
                httpx.AsyncClient(timeout=None) as client,
                client.stream(
                    "POST", f"{running.base_url}/mcp", headers=headers, json=body
                ) as response,
            ):
                assert response.status_code == 200
                lines = response.aiter_lines()
                ack = await _next_data_frame(lines)
                assert ack["method"] == "notifications/subscriptions/acknowledged"

                # Sleep well past request_timeout=3 before publishing, so a
                # notification delivered afterward can only be explained by
                # the stream having survived the timeout wrapper.
                await asyncio.sleep(8.2)
                await running.bus.publish(ToolsListChanged())

                frame = await _next_data_frame(lines, timeout=5)
                elapsed = time.monotonic() - start
                assert elapsed > 8, elapsed
                assert frame["method"] == "notifications/tools/list_changed"


class TestClientCloseReleasesSlot:
    """EC-P3B-2's HTTP client-close half, proven observably."""

    async def test_client_close_ends_subscription(self) -> None:
        async with _run_listen_app(max_subscriptions=1) as running:
            headers_a, body_a = _listen_envelope(
                notifications={"toolsListChanged": True}, request_id=1
            )
            client_a = httpx.AsyncClient(timeout=None)
            try:
                async with client_a.stream(
                    "POST", f"{running.base_url}/mcp", headers=headers_a, json=body_a
                ) as response_a:
                    assert response_a.status_code == 200
                    ack = await _next_data_frame(response_a.aiter_lines())
                    assert ack["method"] == "notifications/subscriptions/acknowledged"
            finally:
                # A real disconnect, not just returning the connection to a
                # pool: closing the whole client tears down the TCP
                # connection so the server's watch_disconnect actually sees
                # http.disconnect and cancels A's handler task, which is
                # what frees the slot in ListenHandler's finally block.
                await client_a.aclose()

            headers_b, body_b = _listen_envelope(
                notifications={"toolsListChanged": True}, request_id=2
            )
            deadline = time.monotonic() + 10.0
            last: object = None
            async with httpx.AsyncClient(timeout=None) as client_b:
                while time.monotonic() < deadline:
                    # Streamed, not `client_b.post(...)`: a successful ack
                    # opens a live SSE stream that never completes on its
                    # own, so a non-streaming call here would hang forever
                    # rather than let this retry loop observe the ack and
                    # move on. `_first_message` reads only the first frame.
                    async with client_b.stream(
                        "POST",
                        f"{running.base_url}/mcp",
                        headers=headers_b,
                        json=body_b,
                    ) as response_b:
                        message = await _first_message(response_b, timeout=3.0)
                        if (
                            message.get("method")
                            == "notifications/subscriptions/acknowledged"
                        ):
                            return
                        last = message
                    await asyncio.sleep(0.2)
            pytest.fail(
                "subscription B was never acked after A's client disconnect "
                f"(max_subscriptions=1); last response was {last!r}"
            )
