"""HTTP transport for MCP Gateway.

Uses the MCP streamable-HTTP transport instead of the legacy SSE transport.
Clients connect with a single POST to /mcp; the server upgrades to an SSE
stream for the response when needed. GET /mcp is not served: it answers
405 Method Not Allowed with `Allow: POST, DELETE`. There is no persistent
GET/SSE connection of any kind, pre-session or otherwise. Server-initiated
notifications are delivered over `subscriptions/listen` instead -- a
long-lived POST stream reachable only at protocol version 2026-07-28 (see
`pmcp.subscriptions`) -- not over a standalone GET channel.

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
import re
import uuid
from collections.abc import AsyncIterator, Mapping, MutableMapping
from typing import TYPE_CHECKING, Any, Callable, Literal
from urllib.parse import urlparse

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from pmcp import __version__
from pmcp.auth import (
    AsyncJWKS,
    AuthMessage,
    AuthText,
    ResourceServerAuthError,
    ResourceServerJWKSUnavailable,
    check_auth_config,
    normalize_auth_metadata,
    render_auth_message,
    sanitize_public_auth_url,
    validate_resource_server_token,
)
from pmcp.argument_errors import exception_text
from pmcp.parsing import load_json
from pmcp.types import GatewayDiagnosticsInfo
from pmcp.waits import bounded_wait

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


def reset_request_metrics() -> None:
    """Zero the fallback request counters. **Tests only.**

    Assigns zero to each existing key rather than ``clear()``-ing the dict, and
    that is load-bearing in two places: ``_inc`` does ``_metrics[key] += 1`` and
    would raise ``KeyError`` on the next request, and the fallback renderer in
    ``handle_metrics`` iterates ``_metrics.items()`` to emit every
    ``pmcp_requests_*`` series -- a cleared dict silently drops series that
    scrapers expect to exist.

    ``_prom_counters`` is deliberately **not** touched. Those are
    ``prometheus_client.Counter`` objects registered into the default
    ``CollectorRegistry`` at import; re-creating them raises
    ``ValueError: Duplicated timeseries``, and clearing the dict would break
    ``_inc``'s lookup. No test asserts an absolute Prometheus value -- they
    assert presence, format, or a ``_metrics`` delta -- so leaving the registry
    alone costs nothing. Do not "fix" this.
    """
    for key in _metrics:
        _metrics[key] = 0


class _NullResponse(Response):
    """Sentinel returned when session_manager.handle_request already sent the response.

    Starlette's request_response wrapper always calls ``await response(scope, receive, send)``
    after the endpoint returns. When the session manager has already written to the ASGI send
    callable directly, a second call to send would raise "response already completed". This
    no-op subclass prevents that double-send.
    """

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
        pass  # response was already sent by session_manager.handle_request


def _auth_response(
    status_code: int, body: AuthText, *, headers: dict[str, str] | None = None
) -> Response:
    """A 401/403/503 auth response. ``body`` must be an `AuthMessage`
    member: it goes through the registry's one guard, `render_auth_message`
    (identity, not type), so anything else is a `TypeError`. ``headers`` is
    keyword-only and must be a mapping, so no second message can ride along
    as an extra argument (Consiliency/pmcp#326)."""
    if headers is not None and not isinstance(headers, Mapping):
        raise TypeError("auth response headers must be a mapping")
    return Response(
        render_auth_message(body), status_code=status_code, headers=headers or {}
    )


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


def reset_rate_limit_state() -> None:
    """Empty the rate-limit buckets and drop the shared lock. **Tests only.**

    ``_rl_store`` keeps a per-IP deque of request timestamps in a sliding 60 s
    window. Every ``TestClient`` request arrives from the same synthetic IP
    (``testclient``), so one test that fills a bucket leaves the next test over
    the limit before it sends anything -- an ordering-dependent 429 that looks
    like a rate-limit bug in whichever test happens to run second.

    The lock is reset with it. ``_rl_lock`` is a module-global ``asyncio.Lock``
    created lazily in ``_check_rate_limit``; it binds to a loop on its first
    *contended* acquire, and pytest-asyncio gives each test a fresh loop. Today's
    sequential ``TestClient`` posts never contend, so this is latent rather than
    an observed failure -- but a later contended acquire on a different loop
    raises ``RuntimeError: ... is bound to a different event loop``. Setting it
    to ``None`` lets the next request build one on the current loop; do not
    construct a ``Lock`` here, since this runs in a synchronous fixture with no
    running loop.
    """
    global _rl_lock
    _rl_store.clear()
    _rl_lock = None


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
    """Return (hostname, port) for an Origin header value, or None if unparseable.

    ``urlparse`` and ``.port`` raise ``ValueError`` on a malformed authority
    (``Port could not be cast to integer value as '<port>'``), quoting the
    caller's header; an unparseable Origin is a rejected one, never a 500
    (Consiliency/pmcp#297).
    """
    try:
        parsed = urlparse(origin)
        hostname = parsed.hostname
        explicit_port = parsed.port
    except ValueError:
        return None
    if not parsed.scheme or not hostname:
        return None
    default_port = "443" if parsed.scheme == "https" else "80"
    port = str(explicit_port) if explicit_port is not None else default_port
    return hostname, port


# --- the SDK's transport rejections, value-free (Consiliency/pmcp#297 rev 19) --
#
# The MCP SDK's streamable-HTTP transport answers a request it cannot accept
# before any pmcp handler runs. Three of its rejections are built from the
# request itself (round-17 grok F001, claude N1):
# - a body that is not JSON: `"Parse error: {str(e)}"`, the parser's text;
# - a JSON body that is not a JSON-RPC message: `"Validation error:
#   {str(e)}"`, pydantic's text, which quotes every rejected `input_value`;
# - an unsupported protocol version on the per-request-envelope path:
#   `data.requested`, the caller's string.
# `tests/test_http_transport.py` enumerates every non-literal message the
# SDK's server-transport modules can put in a rejection, and fails on one
# that is not reviewed here. Every other rejection text is an SDK literal or
# is built from SDK constants and pmcp's own schema.

_PROTOCOL_REVISION = re.compile(r"\d{4}-\d{2}-\d{2}")


def _envelope_problem(request_body: bytes) -> str:
    """Why `request_body` is not a JSON-RPC message, from its structure: the
    parse error's format, position and class, or the validation error's
    paths and phrases -- never the body's text."""
    from mcp_types import jsonrpc_message_adapter
    from pydantic import ValidationError

    try:
        raw = load_json(request_body, source="request body")
    except ValueError as error:
        return exception_text(error)
    try:
        jsonrpc_message_adapter.validate_python(raw, by_name=False)
    except ValidationError as error:
        return exception_text(error)
    return "the request is not a JSON-RPC message"


def value_free_rejection(body: bytes, request_body: bytes | None) -> bytes:
    """`body` (a JSON-RPC error the SDK's transport sent), with each part built
    from the request replaced by its structural description. Any other body
    is returned unchanged."""
    from mcp_types import INVALID_PARAMS, PARSE_ERROR, UNSUPPORTED_PROTOCOL_VERSION

    try:
        payload = load_json(body, source="transport rejection")
    except ValueError:
        return body
    error = payload.get("error") if isinstance(payload, dict) else None
    if (
        not isinstance(error, dict)
        or payload.get("id") is not None
        and not (error.get("code") == UNSUPPORTED_PROTOCOL_VERSION)
    ):
        return body
    code, message = error.get("code"), error.get("message")
    changed = dict(error)
    if code == PARSE_ERROR and isinstance(message, str):
        changed["message"] = "Parse error: " + (
            _envelope_problem(request_body)
            if request_body is not None
            else "the request body is not JSON"
        )
    elif code == INVALID_PARAMS and isinstance(message, str):
        changed["message"] = "Validation error: " + (
            _envelope_problem(request_body)
            if request_body is not None
            else "the request is not a JSON-RPC message"
        )
    elif code == UNSUPPORTED_PROTOCOL_VERSION and isinstance(changed.get("data"), dict):
        data = dict(changed["data"])
        requested = data.get("requested")
        data["requested"] = (
            requested
            if isinstance(requested, str) and _PROTOCOL_REVISION.fullmatch(requested)
            else ""
        )
        changed["data"] = data
    else:
        return body
    payload = {**payload, "error": changed}
    return json.dumps(payload, separators=(",", ":")).encode()


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
        raise ValueError(render_auth_message(AuthMessage.UNSUPPORTED_AUTH_MODE))
    if effective_auth_mode == "shared-secret" and auth_token is None:
        raise ValueError(render_auth_message(AuthMessage.SHARED_SECRET_NEEDS_TOKEN))
    resource_jwks: AsyncJWKS | None = None
    if effective_auth_mode == "resource-server":
        if (
            not resource_server_issuer
            or not resource_server_jwks_url
            or not resource_server_audience
        ):
            raise ValueError(
                render_auth_message(AuthMessage.RESOURCE_SERVER_NEEDS_CONFIG)
            )
        sanitize_public_auth_url(resource_server_jwks_url)
        resource_jwks = AsyncJWKS(resource_server_jwks_url)
    # Every value a rejection may carry in a registry field must have that
    # field's shape, checked here at startup by the renderer's own validators
    # (Consiliency/pmcp#326): a request can then never fail to build its
    # 401/403/503. The metadata URL is checked as configured, not as
    # normalised: normalisation turns a relative or non-http(s) URL into None
    # and the route would be silently omitted (codex round 7).
    check_auth_config(
        metadata_url=protected_resource_metadata_url or None,
        # read only in resource-server mode, so checked only there (round 8)
        required_scopes=(
            required_scopes if effective_auth_mode == "resource-server" else None
        ),
    )
    diagnostics = GatewayDiagnosticsInfo(
        transport="http",
        header_compatibility={
            "MCP-Protocol-Version": "accepted",
            "Mcp-Method": "accepted",
            "Mcp-Name": "accepted",
        },
        session_compatibility={
            "get_stream": "retired",
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

    def _canonical_resource() -> str:
        """The resource this gateway publishes in its protected-resource metadata.

        Operator configuration only, never the request (S-10, see
        Consiliency/pmcp#231): a Host-derived value would advertise whatever Host
        an attacker sent. ``resource_server_audience`` (the RFC 8707 canonical
        identifier tokens are validated against) wins; otherwise the origin of
        the normalized metadata URL. Its ``netloc`` is already userinfo-free and
        keeps IPv6 brackets (``redact_auth_url``) -- rebuilding from
        ``hostname``/``port`` would drop the brackets.
        """
        if resource_server_audience:
            return resource_server_audience
        metadata_url = auth_metadata.protected_resource_metadata_url
        if metadata_url:
            parsed_metadata = urlparse(metadata_url)
            if parsed_metadata.scheme and parsed_metadata.netloc:
                netloc = parsed_metadata.netloc
                # Drop a port that is the scheme's default: `:443` names the
                # same origin as none, so publish the canonical form.
                default_port = {"https": 443, "http": 80}.get(parsed_metadata.scheme)
                if default_port is not None and parsed_metadata.port == default_port:
                    netloc = netloc.rsplit(":", 1)[0]
                # `/mcp` with no path prefix: this app routes MCP at `/mcp`
                # and the metadata route at the URL's literal path, so a
                # prefix in the metadata URL is not a prefix of `/mcp`.
                return f"{parsed_metadata.scheme}://{netloc}/mcp"
        return ""

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
        status_code: int, body: AuthText, *, headers: dict[str, str] | None = None
    ) -> Response:
        _inc(f"requests_{status_code}")
        return _auth_response(status_code, body, headers=headers)

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
            "resource": _canonical_resource(),
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
            return _reject(403, AuthMessage.FORBIDDEN)

        if _host_rejected(request):
            logger.debug("handle_mcp [%s]: 403 invalid host", request_id)
            return _reject(403, AuthMessage.FORBIDDEN)

        if effective_auth_mode == "shared-secret":
            incoming = request.headers.get("authorization", "")
            # Compare BYTES: `hmac.compare_digest` raises TypeError on `str`
            # containing non-ASCII, so an unauthenticated caller could turn any
            # request into a 500 with one non-ASCII header byte (S-12 review
            # finding S-09, Consiliency/pmcp#231). `surrogateescape` keeps any
            # undecodable header bytes representable instead of raising here.
            if not hmac.compare_digest(
                incoming.encode("utf-8", "surrogateescape"),
                f"Bearer {auth_token}".encode(),
            ):
                logger.debug("handle_mcp [%s]: 401 unauthorized", request_id)
                return _reject(
                    401, AuthMessage.UNAUTHORIZED, headers=_auth_headers(request)
                )
        elif effective_auth_mode == "resource-server":
            token = _bearer_token(request)
            if token is None:
                logger.debug("handle_mcp [%s]: 401 missing bearer", request_id)
                return _reject(
                    401, AuthMessage.UNAUTHORIZED, headers=_auth_headers(request)
                )
            try:
                if resource_jwks is None:
                    raise ResourceServerAuthError(
                        "invalid_token", AuthMessage.RS_JWKS_NOT_CONFIGURED
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
                    AuthMessage.SERVICE_UNAVAILABLE,
                    headers=_auth_headers(request, error=exc.error),
                )
            except ResourceServerAuthError as exc:
                if exc.error == "insufficient_scope":
                    scope = " ".join(required_scopes or [])
                    logger.debug("handle_mcp [%s]: 403 insufficient scope", request_id)
                    return _reject(
                        403,
                        AuthMessage.FORBIDDEN,
                        headers=_auth_headers(request, error=exc.error, scope=scope),
                    )
                logger.debug("handle_mcp [%s]: 401 invalid token", request_id)
                return _reject(
                    401,
                    AuthMessage.UNAUTHORIZED,
                    headers=_auth_headers(request, error=exc.error),
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

        # The JSON-RPC method of this POST body, parsed once below and used
        # for two independent purposes: the rmcp-compat notifications/initialized
        # workaround (session-less only), and — the reason it lives at this
        # scope rather than nested inside that check — deciding whether this
        # request is exempt from the request_timeout wrap further down.
        # Stays None for DELETE and for any POST whose body doesn't parse.
        body_method: str | None = None

        # Read the POST body up front, counting bytes against _MAX_BODY_BYTES so
        # a chunked / mislabeled request cannot bypass the header-only cap above.
        # Bound the read by request_timeout so a slow-trickle client cannot pin a
        # connection open (slow-read DoS) — the session manager would otherwise
        # read the body inside the wait_for wrapper further down, but we now read
        # it here for every POST, so we reproduce that bound.
        if request.method == "POST":
            original_receive = request._receive
            try:
                body_bytes, body_too_large = await bounded_wait(
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

            try:
                body_method = load_json(body_bytes, source="request body").get("method")
            except Exception:
                pass

            # Workaround for rmcp clients (e.g., Codex) that send the
            # notifications/initialized message without the mcp-session-id header.
            # The MCP initialize response includes the session ID, but rmcp does
            # not propagate it to the immediately-following initialized
            # notification. PMCP normally returns 400 for session-less POSTs that
            # aren't initialize, which causes the rmcp worker to abort. Accept
            # this specific notification as a no-op when no session ID is present.
            if (
                not request.headers.get("mcp-session-id")
                and body_method == "notifications/initialized"
            ):
                logger.debug(
                    "handle_mcp [%s]: rmcp-compat accepted"
                    " notifications/initialized without session ID",
                    request_id,
                )
                return Response(status_code=202)

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
        request_body = body_bytes if request.method == "POST" else None
        held_start: MutableMapping[str, Any] | None = None
        held_body: list[bytes] = []

        async def tracking_send(message: MutableMapping[str, Any]) -> None:
            # A JSON response the SDK sends with an error status is held until
            # complete and passed through `value_free_rejection` (rev 19).
            nonlocal response_started, held_start
            kind = message.get("type")
            if kind == "http.response.start":
                headers = dict(message.get("headers") or [])
                if int(message.get("status", 200)) >= 400 and headers.get(
                    b"content-type", b""
                ).startswith(b"application/json"):
                    held_start = message
                    return
                response_started = True
            elif kind == "http.response.body" and held_start is not None:
                held_body.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                body = value_free_rejection(b"".join(held_body), request_body)
                start = dict(held_start)
                start["headers"] = [
                    (key, value)
                    for key, value in held_start.get("headers") or []
                    if key.lower() != b"content-length"
                ] + [(b"content-length", str(len(body)).encode())]
                held_start = None
                response_started = True
                await original_send(start)
                await original_send(
                    {"type": "http.response.body", "body": body, "more_body": False}
                )
                return
            await original_send(message)

        # subscriptions/listen opens a long-lived stream by design (a
        # subscription outlives request_timeout on any real client — that is
        # the point of it). Wrapping it in the same wait_for as ordinary
        # request/response POSTs silently truncates every subscription at
        # request_timeout seconds: the client sees a healthy ack and
        # notifications, then "ASGI callable returned without completing
        # response" and an incomplete chunked body, with no
        # SubscriptionsListenResult, on every default deployment (measured,
        # IF-0-P3B-3). ListenHandler's own concurrency and per-stream backlog
        # caps bound the exposure instead; the absolute-lifetime cap this
        # exemption removes is deliberately not replaced (CHANGELOG.md).
        if body_method == "subscriptions/listen":
            await session_manager.handle_request(
                request.scope, request.receive, tracking_send
            )
        else:
            try:
                # A cancelled request (in practice, server shutdown) does not
                # wait for `handle_request` to unwind -- `wait_for` did; a
                # cancelled caller waits on nothing (Consiliency/pmcp#324).
                await bounded_wait(
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
        Route("/mcp", endpoint=handle_mcp, methods=["POST", "DELETE"], name="mcp"),
        Route("/health", endpoint=handle_health, methods=["GET"]),
        Route("/metrics", endpoint=handle_metrics, methods=["GET"]),
    ]
    if auth_metadata.protected_resource_metadata_url:
        metadata_path = urlparse(auth_metadata.protected_resource_metadata_url).path
        if metadata_path:
            # RFC 9728 makes `resource` REQUIRED, so never serve the route with
            # an empty one. Unreachable today -- a normalized metadata URL is
            # always absolute -- so this fails closed at startup, not per
            # request, if a later change makes it reachable (Consiliency/pmcp#326).
            if not _canonical_resource():
                raise ValueError(
                    render_auth_message(AuthMessage.METADATA_NEEDS_RESOURCE)
                )
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
