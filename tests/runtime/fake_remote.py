"""SL-4.4 — a real mcp 2.x Streamable HTTP downstream, for EC-P2-7.

Every existing remote-downstream test in this repo patches
`pmcp.client.manager.streamable_http_client`; Execution Notes >
"EC-P2-7 is greenfield" explicitly disqualifies that as evidence here — the
whole point is proving the *rebuilt* transport really carries headers,
really follows redirects, and really doesn't leak clients across
reconnects, none of which a patched transport can show. This module is a
genuine `mcp.server.MCPServer` served over Streamable HTTP by uvicorn's
programmatic `Server` API, run in-process as a background task on the
current event loop so a real, in-process `ClientManager` can connect to it
within the same test.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

import uvicorn
from mcp.server import MCPServer
from sse_starlette.sse import AppStatus
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

AUTH_HEADER = "authorization"


class _AuthGate:
    """ASGI middleware: 401s any `/mcp` request whose `Authorization` header
    doesn't match exactly. Wraps the MCPServer's own Starlette app rather
    than replacing it, so the real Streamable HTTP session-manager lifespan
    (background tasks, event store) is untouched."""

    def __init__(self, app: ASGIApp, *, expected_value: str) -> None:
        self.app = app
        self._expected = expected_value.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == "/mcp":
            headers = dict(scope["headers"])
            actual = headers.get(AUTH_HEADER.encode(), b"")
            if actual != self._expected:
                response = PlainTextResponse("Unauthorized", status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_fake_remote_app(*, expected_auth_value: str) -> Starlette:
    """One real tool behind the auth gate, plus `/relocated`, which
    307-redirects to `/mcp` (307 preserves method + body, unlike 301/302/303
    — required for a POST-based MCP session to still resolve past it, and
    the property that proves IF-0-P2-2's `follow_redirects=True`)."""
    mcp = MCPServer("fake-remote")

    @mcp.tool()
    def fr_echo(text: str) -> str:
        """Echo the supplied text back with a fixed, greppable prefix."""
        return f"fr-echo:{text}"

    app = mcp.streamable_http_app()

    async def redirect_to_mcp(request: Request) -> RedirectResponse:
        return RedirectResponse(url="/mcp", status_code=307)

    app.add_route("/relocated", redirect_to_mcp, methods=["POST", "GET"])
    app.add_middleware(_AuthGate, expected_value=expected_auth_value)
    return app


@dataclass
class RunningFakeRemote:
    port: int
    base_url: str
    mcp_url: str
    redirect_url: str


@contextlib.asynccontextmanager
async def run_fake_remote(
    port: int, *, expected_auth_value: str
) -> AsyncIterator[RunningFakeRemote]:
    """Serve `build_fake_remote_app()` in-process on `port`, as a background
    asyncio task on the caller's own event loop, for the duration of the
    `async with` block."""
    app = build_fake_remote_app(expected_auth_value=expected_auth_value)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)
    # Clear the latch on the way IN, not only on the way out
    # (Consiliency/pmcp#158). The teardown below resets it after this server
    # stops, but that only protects against OUR OWN shutdown -- it does nothing
    # about a server started elsewhere in this interpreter that latched the flag
    # and never cleared it (tests/mcp2x/test_listen_over_http.py and
    # tests/test_http_dos.py both stop uvicorn servers). `tests/mcp2x` sorts
    # immediately before `tests/runtime`, so in a full-suite run this server
    # would start with the flag already True and every SSE stream it served
    # would end instantly -- "SSE stream ended without a response", the exact
    # error that made test_ec_p2_7_reconnect_does_not_leak_transports flaky.
    # Verified by latching the flag directly: connect fails with that message
    # and succeeds without it.
    AppStatus.should_exit = False
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("fake remote server never started")
        yield RunningFakeRemote(
            port=port,
            base_url=f"http://127.0.0.1:{port}",
            mcp_url=f"http://127.0.0.1:{port}/mcp",
            redirect_url=f"http://127.0.0.1:{port}/relocated",
        )
    finally:
        server.should_exit = True
        await task
        # sse_starlette.sse.AppStatus.should_exit is a process-global class
        # attribute, latched True by its uvicorn-shutdown signal handler and
        # never reset. Left alone, every SSE stream created afterwards — in
        # any event loop, against any server — terminates immediately, which
        # poisons whichever test runs next. This is the one site that resets
        # it; nothing else in the repo should. It is cleared on entry too, so a
        # server started elsewhere in this interpreter cannot poison this one.
        AppStatus.should_exit = False
