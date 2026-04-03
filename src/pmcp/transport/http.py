"""HTTP transport for MCP Gateway.

Uses the MCP streamable-HTTP transport (introduced in MCP spec 2025-03-26) instead
of the legacy SSE transport.  Clients connect with a single POST to /mcp; the
server upgrades to an SSE stream for the response when needed.  No persistent
GET /sse connection is required, which eliminates the race-condition where tool
calls arrived before the legacy SSE session completed its initialize handshake.

Claude Code config (.mcp.json):
    { "mcpServers": { "pmcp": { "type": "http", "url": "http://127.0.0.1:3344/mcp" } } }
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

if TYPE_CHECKING:
    from mcp.server import Server

logger = logging.getLogger(__name__)


class _NullResponse(Response):
    """Sentinel returned when session_manager.handle_request already sent the response.

    Starlette's request_response wrapper always calls ``await response(scope, receive, send)``
    after the endpoint returns. When the session manager has already written to the ASGI send
    callable directly, a second call to send would raise "response already completed". This
    no-op subclass prevents that double-send.
    """

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        pass  # response was already sent by session_manager.handle_request


def create_http_app(mcp_server: Server) -> Starlette:
    """Create Starlette ASGI app with streamable-HTTP transport for MCP server.

    Args:
        mcp_server: The MCP Server instance to run.

    Returns:
        Starlette application with /mcp endpoint.
    """
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        json_response=False,  # Use SSE stream for responses (standard)
        stateless=False,  # Maintain session state across requests
    )

    async def handle_mcp(request: Request) -> Response:
        """Delegate all MCP traffic to the session manager."""
        session_id_short = (request.headers.get("mcp-session-id") or "")[:8] or "<none>"
        logger.debug(
            "handle_mcp: %s method=%s session=%s accept=%r",
            request.url.path,
            request.method,
            session_id_short,
            request.headers.get("accept", ""),
        )
        # Workaround for rmcp clients (e.g., Codex) that open the GET common
        # stream before completing the initialize handshake (and therefore have
        # no session ID yet). The MCP session manager returns 400 for session-less
        # GETs; rmcp treats that as fatal and never establishes the common stream,
        # so server-sent notifications and tool responses routed through that
        # channel are lost. Return a minimal keep-alive SSE stream instead; rmcp
        # will re-open the GET with a real session ID once it has one.
        if request.method == "GET" and not request.headers.get("mcp-session-id"):

            async def _keepalive_sse() -> AsyncIterator[bytes]:
                try:
                    while True:
                        yield b": keep-alive\n\n"
                        await asyncio.sleep(30)
                except asyncio.CancelledError:
                    pass

            logger.debug("rmcp-compat: serving pre-session GET as keep-alive SSE")
            return StreamingResponse(
                _keepalive_sse(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        # Workaround for rmcp clients (e.g., Codex) that send the
        # notifications/initialized message without the mcp-session-id header.
        # The MCP initialize response includes the session ID, but rmcp does not
        # propagate it to the immediately-following initialized notification.
        # PMCP normally returns 400 for session-less POSTs that aren't initialize,
        # which causes the rmcp worker to abort. Accept this specific notification
        # as a no-op when no session ID is present.
        if request.method == "POST" and not request.headers.get("mcp-session-id"):
            body_bytes = await request.body()
            try:
                body = json.loads(body_bytes)
                if body.get("method") == "notifications/initialized":
                    logger.debug(
                        "rmcp-compat: accepted notifications/initialized without session ID"
                    )
                    return Response(status_code=202)
            except Exception:
                pass

            # For other session-less POSTs (e.g., the initialize request itself),
            # replay the already-consumed body through the receive callable so
            # the session manager can still read it.
            original_receive = request._receive
            body_replayed = False

            async def replay_receive() -> dict:
                nonlocal body_replayed
                if not body_replayed:
                    body_replayed = True
                    return {"type": "http.request", "body": body_bytes, "more_body": False}
                return await original_receive()

            request._receive = replay_receive  # type: ignore[method-assign]

        await session_manager.handle_request(
            request.scope, request.receive, request._send
        )
        return _NullResponse()

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            logger.info("Streamable-HTTP session manager started")
            yield
        logger.info("Streamable-HTTP session manager stopped")

    routes = [
        Route("/mcp", endpoint=handle_mcp, methods=["GET", "POST", "DELETE"]),
    ]

    return Starlette(routes=routes, lifespan=lifespan)
