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
import ipaddress
import json
import logging
import os
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
# Transport DoS guards for unauthenticated pre-session traffic
# ---------------------------------------------------------------------------
# The pre-session keepalive SSE stream (rmcp compatibility, see handle_mcp) is
# infinite by design. Without bounds an unauthenticated client can open an
# unlimited number of long-lived connections and exhaust the server
# (connection-exhaustion DoS). We cap both the number of concurrent streams and
# each stream's absolute lifetime. Both are env-tunable for operators.
_DEFAULT_MAX_KEEPALIVE_STREAMS: int = 64
_DEFAULT_KEEPALIVE_MAX_SECONDS: float = 300.0
_KEEPALIVE_HEARTBEAT_SECONDS: float = 30.0

# Live count of open pre-session keepalive streams. Mutated only from the event
# loop thread; check-then-increment in handle_mcp has no await in between and is
# therefore atomic. Held in a dict so nested closures can mutate without a
# module-level ``global`` declaration (mirrors the _rl_store pattern).
_keepalive_active: dict[str, int] = {"count": 0}


def _env_int(name: str, default: int, *, minimum: int) -> int:
    """Read a positive int from the environment, clamped to ``minimum``."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float, *, minimum: float) -> float:
    """Read a positive float from the environment, clamped to ``minimum``."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


async def _read_body_capped(
    receive: Callable[[], Any], max_bytes: int
) -> tuple[bytes, bool]:
    """Read the full ASGI request body, enforcing ``max_bytes`` as chunks arrive.

    Returns ``(body, exceeded)``. Counting bytes during the read (rather than
    trusting the advertised Content-Length) means a chunked or mislabeled
    request cannot bypass the size cap. Once the cap is exceeded the read stops
    immediately and any buffered data is dropped.
    """
    chunks: list[bytes] = []
    total = 0
    more_body = True
    while more_body:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunk = message.get("body", b"")
        if chunk:
            total += len(chunk)
            if total > max_bytes:
                return b"", True
            chunks.append(chunk)
        more_body = message.get("more_body", False)
    return b"".join(chunks), False


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


def _is_loopback_host(hostname: str) -> bool:
    """Return True for loopback host names (localhost, 127.0.0.0/8, ::1)."""
    if not hostname:
        return False
    hostname = hostname.strip("[]")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _split_host_port(value: str, default_port: str) -> tuple[str, str]:
    """Split a ``host[:port]`` authority into (hostname, port).

    Handles bracketed IPv6 literals (``[::1]:3344``). ``default_port`` is
    returned when no explicit port is present.
    """
    value = value.strip()
    if value.startswith("["):
        host, sep, rest = value[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else default_port
        return host, (port or default_port)
    if value.count(":") == 1:
        host, _, port = value.partition(":")
        return host, (port or default_port)
    return value, default_port


def _origin_host_port(origin: str) -> tuple[str, str] | None:
    """Return (hostname, port) for an Origin header value, or None if unparseable."""
    parsed = urlparse(origin)
    if not parsed.scheme or not parsed.hostname:
        return None
    default_port = "443" if parsed.scheme == "https" else "80"
    port = str(parsed.port) if parsed.port is not None else default_port
    return parsed.hostname, port


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

    # Host allowlist for DNS-rebinding defense. Enforced only when the operator
    # opts in by configuring allowed_origins; the set is derived from the origins
    # plus the gateway's own canonical resource host (audience / metadata URL) so
    # a reverse proxy forwarding the public Host is not rejected.
    _allowed_host_names: set[str] = set()
    if allowed_origins is not None:
        for _origin in allowed_origins:
            parsed = _origin_host_port(_origin)
            if parsed is not None:
                _allowed_host_names.add(parsed[0])
        for _url in (resource_server_audience, protected_resource_metadata_url):
            if _url:
                parsed_url = urlparse(_url)
                if parsed_url.hostname:
                    _allowed_host_names.add(parsed_url.hostname)

    def _origin_rejected(request: Request) -> bool:
        """Return True if a browser Origin header should be rejected (403).

        Runs by default (even without a configured allowlist) as DNS-rebinding
        defense: a non-loopback, non-same-origin Origin that is not explicitly
        allow-listed is rejected. Requests with no Origin (normal MCP clients)
        always pass.
        """
        origin = request.headers.get("origin")
        if origin is None:
            return False
        parsed = _origin_host_port(origin)
        if parsed is None:
            return True
        origin_host, origin_port = parsed
        if _is_loopback_host(origin_host):
            return False
        if allowed_origins is not None and origin in allowed_origins:
            return False
        # Same-origin: Origin host:port matches the request Host header.
        default_port = "443" if request.url.scheme == "https" else "80"
        host_header = request.headers.get("host", "")
        host_name, host_port = _split_host_port(host_header, default_port)
        if origin_host == host_name and origin_port == host_port:
            return False
        return True

    def _host_rejected(request: Request) -> bool:
        """Return True if the request Host header is not allow-listed (403).

        Enforced only when allowed_origins is configured; loopback Hosts are
        always accepted so local clients keep working.
        """
        if allowed_origins is None:
            return False
        host_header = request.headers.get("host", "")
        if not host_header:
            return False
        host_name, _ = _split_host_port(host_header, "")
        if _is_loopback_host(host_name):
            return False
        return host_name not in _allowed_host_names

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

        if _origin_rejected(request):
            logger.debug("handle_mcp [%s]: 403 invalid origin", request_id)
            return _reject(403, "Forbidden")

        if _host_rejected(request):
            logger.debug("handle_mcp [%s]: 403 invalid host", request_id)
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

        # Input size guard — fast-path reject when the advertised Content-Length
        # already exceeds the cap. This is only a hint: a chunked or mislabeled
        # request has no (or a false) Content-Length, so the body is additionally
        # counted while reading below (see _read_body_capped).
        if request.method == "POST":
            cl = request.headers.get("content-length")
            if cl:
                try:
                    declared = int(cl)
                except ValueError:
                    declared = 0
                if declared > _MAX_BODY_BYTES:
                    logger.debug(
                        "handle_mcp [%s]: 413 Content-Length over cap", request_id
                    )
                    return Response("Payload Too Large", status_code=413)

        # Workaround for rmcp clients (e.g., Codex) that open the GET common
        # stream before completing the initialize handshake (and therefore have
        # no session ID yet). The MCP session manager returns 400 for session-less
        # GETs; rmcp treats that as fatal and never establishes the common stream,
        # so server-sent notifications and tool responses routed through that
        # channel are lost. Return a minimal keep-alive SSE stream instead; rmcp
        # will re-open the GET with a real session ID once it has one.
        if request.method == "GET" and not request.headers.get("mcp-session-id"):
            # DoS guard: cap concurrent pre-session streams and give each an
            # absolute lifetime so an unauthenticated client cannot open an
            # unbounded number of infinite connections.
            max_streams = _env_int(
                "PMCP_MAX_KEEPALIVE_STREAMS",
                _DEFAULT_MAX_KEEPALIVE_STREAMS,
                minimum=1,
            )
            if _keepalive_active["count"] >= max_streams:
                logger.debug(
                    "handle_mcp [%s]: 503 keepalive stream cap reached (%d)",
                    request_id,
                    max_streams,
                )
                return _reject(503, "Service Unavailable")
            # No await between the check above and this increment: atomic on the
            # event loop. The finally in the generator releases the slot.
            _keepalive_active["count"] += 1
            max_seconds = _env_float(
                "PMCP_KEEPALIVE_MAX_SECONDS",
                _DEFAULT_KEEPALIVE_MAX_SECONDS,
                minimum=0.1,
            )

            async def _keepalive_sse() -> AsyncIterator[bytes]:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + max_seconds
                try:
                    while loop.time() < deadline:
                        yield b": keep-alive\n\n"
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            break
                        await asyncio.sleep(
                            min(_KEEPALIVE_HEARTBEAT_SECONDS, remaining)
                        )
                except asyncio.CancelledError:
                    pass
                finally:
                    _keepalive_active["count"] -= 1

            logger.debug(
                "handle_mcp [%s]: rmcp-compat serving pre-session GET as keep-alive"
                " SSE (deadline=%ss, active=%d)",
                request_id,
                max_seconds,
                _keepalive_active["count"],
            )
            return StreamingResponse(
                _keepalive_sse(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        # Read the POST body up front, counting bytes against _MAX_BODY_BYTES so
        # a chunked / mislabeled request cannot bypass the header-only cap above.
        # Bound the read by request_timeout so a slow-trickle client cannot pin a
        # connection open (slow-read DoS) — the session manager would otherwise
        # read the body inside the wait_for wrapper further down, but we now read
        # it here for every POST, so we reproduce that bound.
        if request.method == "POST":
            original_receive = request._receive
            try:
                body_bytes, body_too_large = await asyncio.wait_for(
                    _read_body_capped(original_receive, _MAX_BODY_BYTES),
                    timeout=request_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "handle_mcp [%s]: body read timed out after %ss",
                    request_id,
                    request_timeout,
                )
                return Response("Gateway Timeout", status_code=504)
            if body_too_large:
                logger.debug(
                    "handle_mcp [%s]: 413 body exceeded cap during read", request_id
                )
                return Response("Payload Too Large", status_code=413)

            # Workaround for rmcp clients (e.g., Codex) that send the
            # notifications/initialized message without the mcp-session-id header.
            # The MCP initialize response includes the session ID, but rmcp does
            # not propagate it to the immediately-following initialized
            # notification. PMCP normally returns 400 for session-less POSTs that
            # aren't initialize, which causes the rmcp worker to abort. Accept
            # this specific notification as a no-op when no session ID is present.
            if not request.headers.get("mcp-session-id"):
                try:
                    body = json.loads(body_bytes)
                    if body.get("method") == "notifications/initialized":
                        logger.debug(
                            "handle_mcp [%s]: rmcp-compat accepted"
                            " notifications/initialized without session ID",
                            request_id,
                        )
                        return Response(status_code=202)
                except Exception:
                    pass

            # Replay the already-consumed body through the receive callable so the
            # session manager can still read it (for both session-bearing POSTs
            # and session-less ones such as the initialize request itself).
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
