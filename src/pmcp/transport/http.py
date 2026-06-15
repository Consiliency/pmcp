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
import collections
import contextlib
import hmac
import json
import logging
import uuid
from collections.abc import AsyncIterator, MutableMapping
from typing import TYPE_CHECKING, Any, Callable, Literal
from urllib.parse import urlparse

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from pmcp import __version__
from pmcp.auth import (
    AsyncJWKS,
    ResourceServerAuthError,
    ResourceServerJWKSUnavailable,
    normalize_auth_metadata,
    sanitize_public_auth_url,
    validate_resource_server_token,
)
from pmcp.types import GatewayDiagnosticsInfo

if TYPE_CHECKING:
    from mcp.server import Server

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process request counters (Prometheus text format fallback)
# ---------------------------------------------------------------------------
_metrics: dict[str, int] = {
    "requests_total": 0,
    "requests_401": 0,
    "requests_403": 0,
    "requests_503": 0,
    "requests_429": 0,
    "requests_ok": 0,
}

# ---------------------------------------------------------------------------
# Rate-limit state (lazy-initialized to avoid event-loop issues at import time)
# ---------------------------------------------------------------------------
_rl_store: dict[str, collections.deque] = {}
_rl_lock: asyncio.Lock | None = None

_MAX_BODY_BYTES: int = 10 * 1024 * 1024  # 10 MB

# ---------------------------------------------------------------------------
# Prometheus counter registration (optional — falls back to _metrics dict)
# ---------------------------------------------------------------------------
_prom_counters: dict = {}
_generate_latest: Callable[..., bytes] | None = None

try:
    from prometheus_client import Counter as _PCounter
    from prometheus_client import generate_latest as _prom_generate_latest

    _prom_counters = {
        "requests_total": _PCounter(
            "pmcp_requests_total", "Total /mcp requests handled"
        ),
        "requests_401": _PCounter(
            "pmcp_requests_401", "Requests rejected 401 Unauthorized"
        ),
        "requests_403": _PCounter(
            "pmcp_requests_403", "Requests rejected 403 Forbidden"
        ),
        "requests_503": _PCounter(
            "pmcp_requests_503", "Requests rejected 503 Service Unavailable"
        ),
        "requests_429": _PCounter(
            "pmcp_requests_429", "Requests rejected 429 Too Many Requests"
        ),
        "requests_ok": _PCounter("pmcp_requests_ok", "Requests completed successfully"),
    }
    _generate_latest = _prom_generate_latest
except ImportError:
    pass


def _inc(key: str) -> None:
    """Increment a metric counter in both the fallback dict and the prometheus registry."""
    _metrics[key] += 1
    if c := _prom_counters.get(key):
        c.inc()


class _NullResponse(Response):
    """Sentinel returned when session_manager.handle_request already sent the response.

    Starlette's request_response wrapper always calls ``await response(scope, receive, send)``
    after the endpoint returns. When the session manager has already written to the ASGI send
    callable directly, a second call to send would raise "response already completed". This
    no-op subclass prevents that double-send.
    """

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        pass  # response was already sent by session_manager.handle_request


async def _check_rate_limit(client_ip: str, max_rpm: int) -> bool:
    """Return True if the request is allowed, False if rate-limited.

    Uses a sliding 60-second window per client IP.
    """
    global _rl_lock
    if _rl_lock is None:
        _rl_lock = asyncio.Lock()

    import time

    now = time.monotonic()
    window = 60.0
    async with _rl_lock:
        if client_ip not in _rl_store:
            _rl_store[client_ip] = collections.deque()
        q = _rl_store[client_ip]
        while q and now - q[0] > window:
            q.popleft()
        if not q:
            del _rl_store[client_ip]
            _rl_store[client_ip] = collections.deque()
            q = _rl_store[client_ip]
        if len(q) >= max_rpm:
            return False
        q.append(now)
        return True


def create_http_app(
    mcp_server: Server,
    auth_token: str | None = None,
    auth_mode: Literal["none", "shared-secret", "resource-server"] | None = None,
    rate_limit_rpm: int = 0,
    request_timeout: int = 60,
    protected_resource_metadata_url: str | None = None,
    authorization_server_metadata_url: str | None = None,
    oidc_issuer_url: str | None = None,
    oidc_discovery_url: str | None = None,
    client_id_metadata_document_url: str | None = None,
    declared_scopes: list[str] | None = None,
    resource_server_issuer: str | None = None,
    resource_server_jwks_url: str | None = None,
    resource_server_audience: str | None = None,
    resource_server_allowed_algorithms: tuple[str, ...] = ("RS256", "ES256"),
    required_scopes: list[str] | None = None,
    allowed_origins: list[str] | None = None,
) -> Starlette:
    """Create Starlette ASGI app with streamable-HTTP transport for MCP server.

    Args:
        mcp_server: The MCP Server instance to run.
        auth_token: If set, require ``Authorization: Bearer <token>`` on every /mcp request.
        rate_limit_rpm: If > 0, limit each client IP to this many requests per minute on /mcp.

    Returns:
        Starlette application with /mcp, /health, and /metrics endpoints.
    """
    session_manager = StreamableHTTPSessionManager(
        app=mcp_server,
        json_response=False,  # Use SSE stream for responses (standard)
        stateless=False,  # Maintain session state across requests
    )
    auth_metadata = normalize_auth_metadata(
        protected_resource_metadata_url=protected_resource_metadata_url,
        authorization_server_metadata_url=authorization_server_metadata_url,
        oidc_issuer_url=oidc_issuer_url,
        oidc_discovery_url=oidc_discovery_url,
        client_id_metadata_document_url=client_id_metadata_document_url,
        declared_scopes=declared_scopes,
    )
    effective_auth_mode = auth_mode
    if effective_auth_mode is None:
        effective_auth_mode = "shared-secret" if auth_token is not None else "none"
    if effective_auth_mode not in {"none", "shared-secret", "resource-server"}:
        raise ValueError("Unsupported auth mode.")
    if effective_auth_mode == "shared-secret" and auth_token is None:
        raise ValueError("shared-secret auth mode requires auth_token.")
    resource_jwks: AsyncJWKS | None = None
    if effective_auth_mode == "resource-server":
        if (
            not resource_server_issuer
            or not resource_server_jwks_url
            or not resource_server_audience
        ):
            raise ValueError(
                "resource-server auth mode requires issuer, JWKS URL, and audience."
            )
        sanitize_public_auth_url(resource_server_jwks_url)
        resource_jwks = AsyncJWKS(resource_server_jwks_url)
    diagnostics = GatewayDiagnosticsInfo(
        transport="http",
        header_compatibility={
            "MCP-Protocol-Version": "accepted",
            "Mcp-Method": "accepted",
            "Mcp-Name": "accepted",
        },
        session_compatibility={
            "pre_session_get": "rmcp_keepalive",
            "initialized_without_session": "accepted",
        },
        auth_metadata_present=bool(auth_metadata.protected_resource_metadata_url),
        rate_limit_enabled=rate_limit_rpm > 0,
        rate_limit_rpm=rate_limit_rpm if rate_limit_rpm > 0 else None,
    )

    def _resource_audience() -> str:
        return resource_server_audience or ""

    def _auth_headers(
        request: Request | None = None,
        *,
        error: str | None = None,
        scope: str | None = None,
    ) -> dict[str, str]:
        parts: list[str] = []
        if not auth_metadata.protected_resource_metadata_url:
            if request is not None and effective_auth_mode == "resource-server":
                parts.append(f'resource="{_resource_audience()}"')
        else:
            parts.append(
                f'resource_metadata="{auth_metadata.protected_resource_metadata_url}"'
            )
        if error:
            parts.append(f'error="{error}"')
        if scope:
            parts.append(f'scope="{scope}"')
        return {"WWW-Authenticate": "Bearer " + ", ".join(parts)} if parts else {}

    def _bearer_token(request: Request) -> str | None:
        value = request.headers.get("authorization", "")
        scheme, _, token = value.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        return token

    def _reject(
        status_code: int, body: str, headers: dict[str, str] | None = None
    ) -> Response:
        _inc(f"requests_{status_code}")
        return Response(body, status_code=status_code, headers=headers or {})

    async def handle_health(request: Request) -> Response:
        """Unauthenticated health check — safe for load-balancers and container probes."""
        return JSONResponse(
            {
                "ok": True,
                "version": __version__,
                "transport": "http",
                "gateway_diagnostics": diagnostics.model_dump(exclude_none=True),
            }
        )

    async def handle_metrics(request: Request) -> Response:
        """Unauthenticated Prometheus-compatible metrics endpoint."""
        if _generate_latest is not None:
            return Response(
                _generate_latest(),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )
        # Fallback: prometheus_client not installed — render _metrics dict
        lines: list[str] = []
        for key, val in _metrics.items():
            metric_name = f"pmcp_{key}"
            lines.append(f"# TYPE {metric_name} counter")
            lines.append(f"{metric_name} {val}")
        return Response(
            "\n".join(lines) + "\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    async def handle_protected_resource_metadata(request: Request) -> Response:
        """Public OAuth protected-resource metadata for this PMCP endpoint."""
        payload: dict[str, object] = {
            "resource": str(request.url_for("mcp")),
        }
        if auth_metadata.authorization_server_metadata_url:
            payload["authorization_servers"] = [
                auth_metadata.authorization_server_metadata_url
            ]
        if auth_metadata.oidc_issuer_url:
            payload["issuer"] = auth_metadata.oidc_issuer_url
        if auth_metadata.client_id_metadata_document_url:
            payload["client_id_metadata_document"] = (
                auth_metadata.client_id_metadata_document_url
            )
        if auth_metadata.declared_scopes:
            payload["scopes_supported"] = auth_metadata.declared_scopes
        return JSONResponse(payload)

    async def handle_mcp(request: Request) -> Response:
        """Delegate all MCP traffic to the session manager."""
        request_id = uuid.uuid4().hex[:8]
        _inc("requests_total")

        session_id_short = (request.headers.get("mcp-session-id") or "")[:8] or "<none>"
        logger.debug(
            "handle_mcp [%s]: %s method=%s session=%s accept=%r",
            request_id,
            request.url.path,
            request.method,
            session_id_short,
            request.headers.get("accept", ""),
        )
        request.scope["pmcp.trace_context"] = {
            key: value
            for key in ("traceparent", "tracestate", "baggage")
            if (value := request.headers.get(key))
        }
        request.scope["pmcp.header_compatibility"] = {
            key: "present"
            for key in ("mcp-protocol-version", "mcp-method", "mcp-name")
            if request.headers.get(key)
        }

        if allowed_origins is not None:
            origin = request.headers.get("origin")
            if origin is not None and origin not in allowed_origins:
                logger.debug("handle_mcp [%s]: 403 invalid origin", request_id)
                return _reject(403, "Forbidden")

        if effective_auth_mode == "shared-secret":
            incoming = request.headers.get("authorization", "")
            if not hmac.compare_digest(incoming, f"Bearer {auth_token}"):
                logger.debug("handle_mcp [%s]: 401 unauthorized", request_id)
                return _reject(401, "Unauthorized", _auth_headers(request))
        elif effective_auth_mode == "resource-server":
            token = _bearer_token(request)
            if token is None:
                logger.debug("handle_mcp [%s]: 401 missing bearer", request_id)
                return _reject(401, "Unauthorized", _auth_headers(request))
            try:
                if resource_jwks is None:
                    raise ResourceServerAuthError(
                        "invalid_token", "Resource Server JWKS is not configured."
                    )
                jwks = await resource_jwks.get_for_token(token)
                claims = validate_resource_server_token(
                    token,
                    issuer=resource_server_issuer or "",
                    jwks=jwks,
                    audience=_resource_audience(),
                    required_scopes=required_scopes,
                    allowed_algorithms=resource_server_allowed_algorithms,
                )
                request.scope["pmcp.auth"] = {
                    "issuer": claims.issuer,
                    "subject": claims.subject,
                    "audience": claims.audience,
                    "scopes": claims.scopes,
                }
            except ResourceServerJWKSUnavailable as exc:
                logger.debug("handle_mcp [%s]: 503 jwks unavailable", request_id)
                return _reject(
                    503,
                    "Service Unavailable",
                    _auth_headers(request, error=exc.error),
                )
            except ResourceServerAuthError as exc:
                if exc.error == "insufficient_scope":
                    scope = " ".join(required_scopes or [])
                    logger.debug("handle_mcp [%s]: 403 insufficient scope", request_id)
                    return _reject(
                        403,
                        "Forbidden",
                        _auth_headers(request, error=exc.error, scope=scope),
                    )
                logger.debug("handle_mcp [%s]: 401 invalid token", request_id)
                return _reject(
                    401,
                    "Unauthorized",
                    _auth_headers(request, error=exc.error),
                )

        # Per-IP rate limiting (optional — only when rate_limit_rpm > 0)
        if rate_limit_rpm > 0:
            client_ip = request.client.host if request.client else "unknown"
            if not await _check_rate_limit(client_ip, rate_limit_rpm):
                _inc("requests_429")
                logger.debug(
                    "handle_mcp [%s]: 429 rate limited ip=%s", request_id, client_ip
                )
                return Response("Too Many Requests", status_code=429)

        # Input size guard — reject oversized POST bodies before reading them
        if request.method == "POST":
            cl = request.headers.get("content-length")
            if cl and int(cl) > _MAX_BODY_BYTES:
                return Response("Payload Too Large", status_code=413)

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

            logger.debug(
                "handle_mcp [%s]: rmcp-compat serving pre-session GET as keep-alive SSE",
                request_id,
            )
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
                        "handle_mcp [%s]: rmcp-compat accepted notifications/initialized"
                        " without session ID",
                        request_id,
                    )
                    return Response(status_code=202)
            except Exception:
                pass

            # For other session-less POSTs (e.g., the initialize request itself),
            # replay the already-consumed body through the receive callable so
            # the session manager can still read it.
            original_receive = request._receive
            body_replayed = False

            async def replay_receive() -> Any:
                nonlocal body_replayed
                if not body_replayed:
                    body_replayed = True
                    return {
                        "type": "http.request",
                        "body": body_bytes,
                        "more_body": False,
                    }
                return await original_receive()

            request._receive = replay_receive  # type: ignore[method-assign]

        response_started = False
        original_send = request._send

        async def tracking_send(message: MutableMapping[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await original_send(message)

        try:
            await asyncio.wait_for(
                session_manager.handle_request(
                    request.scope, request.receive, tracking_send
                ),
                timeout=request_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "handle_mcp [%s]: request timed out after %ss",
                request_id,
                request_timeout,
            )
            if response_started:
                return _NullResponse()
            return Response("Gateway Timeout", status_code=504)
        _inc("requests_ok")
        return _NullResponse()

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            logger.info("Streamable-HTTP session manager started")
            yield
        logger.info("Streamable-HTTP session manager stopped")

    routes = [
        Route(
            "/mcp", endpoint=handle_mcp, methods=["GET", "POST", "DELETE"], name="mcp"
        ),
        Route("/health", endpoint=handle_health, methods=["GET"]),
        Route("/metrics", endpoint=handle_metrics, methods=["GET"]),
    ]
    if auth_metadata.protected_resource_metadata_url:
        metadata_path = urlparse(auth_metadata.protected_resource_metadata_url).path
        if metadata_path:
            routes.append(
                Route(
                    metadata_path,
                    endpoint=handle_protected_resource_metadata,
                    methods=["GET"],
                    name="protected-resource-metadata",
                )
            )

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.gateway_diagnostics = diagnostics
    return app
