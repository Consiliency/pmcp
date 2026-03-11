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

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response

if TYPE_CHECKING:
    from mcp.server import Server

logger = logging.getLogger(__name__)


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
        stateless=False,       # Maintain session state across requests
    )

    async def handle_mcp(request: Request) -> Response:
        """Delegate all MCP traffic to the session manager."""
        await session_manager.handle_request(
            request.scope, request.receive, request._send
        )
        return Response()

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
