"""HTTP/SSE transport for MCP Gateway.

This module provides HTTP transport using Starlette and SSE (Server-Sent Events)
for the MCP Gateway, allowing multiple clients to connect to a single long-lived
gateway process.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Mount, Route

if TYPE_CHECKING:
    from mcp.server import Server
    from starlette.requests import Request

logger = logging.getLogger(__name__)


def create_http_app(mcp_server: Server) -> Starlette:
    """Create Starlette ASGI app with SSE transport for MCP server.

    Args:
        mcp_server: The MCP Server instance to run.

    Returns:
        Starlette application with SSE endpoints configured.
    """
    # Create SSE transport with /messages/ path for POST requests
    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> Response:
        """Handle SSE connection requests.

        This endpoint establishes a Server-Sent Events connection with the client.
        The client receives events from the MCP server through this connection.
        """
        logger.debug("New SSE connection from %s", request.client)

        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp_server.run(
                streams[0],
                streams[1],
                mcp_server.create_initialization_options(),
            )

        return Response()

    # Define routes:
    # - GET /sse - SSE connection endpoint
    # - POST /messages/ - Message endpoint (handled by SseServerTransport)
    routes = [
        Route("/sse", endpoint=handle_sse, methods=["GET"]),
        Mount("/messages", app=sse_transport.handle_post_message),
    ]

    return Starlette(routes=routes)
