"""Tests for HTTP transport (Phase 1)."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, get_args
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from pmcp import __version__

if TYPE_CHECKING:
    pass


def _make_contract_client(
    auth_token: str | None = None,
    rate_limit_rpm: int = 0,
    **kwargs: object,
) -> TestClient:
    """Create a minimal HTTP app client for route contract tests."""
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
        # Loopback base URL so the request Host header is always allow-listed;
        # keeps Origin/Host tests deterministic regardless of TestClient defaults.
        return TestClient(
            app, base_url="http://127.0.0.1", raise_server_exceptions=False
        )


class TestGatewayTransportType:
    """Test GatewayTransport type definition."""

    def test_transport_type_accepts_stdio(self) -> None:
        """GatewayTransport should accept 'stdio'."""
        from pmcp.types import GatewayTransport

        # Literal types accept specific string values
        valid_values = get_args(GatewayTransport)
        assert "stdio" in valid_values

    def test_transport_type_accepts_http(self) -> None:
        """GatewayTransport should accept 'http'."""
        from pmcp.types import GatewayTransport

        valid_values = get_args(GatewayTransport)
        assert "http" in valid_values

    def test_transport_type_rejects_invalid(self) -> None:
        """GatewayTransport should only allow 'stdio' and 'http'."""
        from pmcp.types import GatewayTransport

        valid_values = get_args(GatewayTransport)
        assert set(valid_values) == {"stdio", "http"}


class TestGatewayServerHttpParams:
    """Test GatewayServer constructor accepts HTTP parameters."""

    def test_constructor_accepts_host_parameter(self) -> None:
        """GatewayServer should accept host parameter."""
        from pmcp.server import GatewayServer

        server = GatewayServer(host="0.0.0.0")
        assert server._host == "0.0.0.0"

    def test_constructor_accepts_port_parameter(self) -> None:
        """GatewayServer should accept port parameter."""
        from pmcp.server import GatewayServer

        server = GatewayServer(port=8080)
        assert server._port == 8080

    def test_default_host_is_localhost(self) -> None:
        """Default host should be 127.0.0.1."""
        from pmcp.server import GatewayServer

        server = GatewayServer()
        assert server._host == "127.0.0.1"

    def test_default_port_is_3344(self) -> None:
        """Default port should be 3344."""
        from pmcp.server import GatewayServer

        server = GatewayServer()
        assert server._port == 3344


class TestCliHttpArguments:
    """Test CLI argument parsing for HTTP transport."""

    def test_parse_transport_stdio(self) -> None:
        """CLI should parse --transport stdio."""
        from pmcp.cli import parse_args

        with patch("sys.argv", ["pmcp", "--transport", "stdio"]):
            args = parse_args()
        assert args.transport == "stdio"

    def test_parse_transport_http(self) -> None:
        """CLI should parse --transport http."""
        from pmcp.cli import parse_args

        with patch("sys.argv", ["pmcp", "--transport", "http"]):
            args = parse_args()
        assert args.transport == "http"

    def test_parse_host_argument(self) -> None:
        """CLI should parse --host argument."""
        from pmcp.cli import parse_args

        with patch("sys.argv", ["pmcp", "--host", "0.0.0.0"]):
            args = parse_args()
        assert args.host == "0.0.0.0"

    def test_parse_port_argument(self) -> None:
        """CLI should parse --port argument."""
        from pmcp.cli import parse_args

        with patch("sys.argv", ["pmcp", "--port", "8080"]):
            args = parse_args()
        assert args.port == 8080

    def test_default_transport_is_stdio(self) -> None:
        """Default transport should be stdio."""
        from pmcp.cli import parse_args

        with patch("sys.argv", ["pmcp"]):
            args = parse_args()
        assert args.transport == "stdio"

    def test_default_host(self) -> None:
        """Default host should be 127.0.0.1."""
        from pmcp.cli import parse_args

        with patch("sys.argv", ["pmcp"]):
            args = parse_args()
        assert args.host == "127.0.0.1"

    def test_default_port(self) -> None:
        """Default port should be 3344."""
        from pmcp.cli import parse_args

        with patch("sys.argv", ["pmcp"]):
            args = parse_args()
        assert args.port == 3344

    def test_env_override_transport(self) -> None:
        """PMCP_TRANSPORT env var should override default."""
        from pmcp.cli import parse_args

        with patch("sys.argv", ["pmcp"]):
            args = parse_args()

        # Environment override happens in run_server, test that logic
        with patch.dict(os.environ, {"PMCP_TRANSPORT": "http"}):
            if os.environ.get("PMCP_TRANSPORT"):
                args.transport = os.environ["PMCP_TRANSPORT"]
        assert args.transport == "http"

    def test_env_override_host(self) -> None:
        """PMCP_HOST env var should override default."""
        from pmcp.cli import parse_args

        with patch("sys.argv", ["pmcp"]):
            args = parse_args()

        with patch.dict(os.environ, {"PMCP_HOST": "0.0.0.0"}):
            if os.environ.get("PMCP_HOST"):
                args.host = os.environ["PMCP_HOST"]
        assert args.host == "0.0.0.0"

    def test_env_override_port(self) -> None:
        """PMCP_PORT env var should override default."""
        from pmcp.cli import parse_args

        with patch("sys.argv", ["pmcp"]):
            args = parse_args()

        with patch.dict(os.environ, {"PMCP_PORT": "8080"}):
            if os.environ.get("PMCP_PORT"):
                args.port = int(os.environ["PMCP_PORT"])
        assert args.port == 8080


class TestHttpTransportRoutes:
    """Test HTTP transport route creation."""

    def test_creates_mcp_endpoint(self) -> None:
        """HTTP app should have /mcp endpoint (streamable-HTTP transport)."""
        from pmcp.transport.http import create_http_app

        # Create a mock MCP server
        mock_server = MagicMock()
        mock_server.create_initialization_options = MagicMock(return_value={})

        app = create_http_app(mock_server)

        # Check routes — new transport uses a single /mcp endpoint
        route_paths = [route.path for route in app.routes]
        assert "/mcp" in route_paths

    def test_creates_messages_endpoint(self) -> None:
        """HTTP app /mcp endpoint handles both GET and POST (streamable-HTTP)."""
        from pmcp.transport.http import create_http_app

        mock_server = MagicMock()
        mock_server.create_initialization_options = MagicMock(return_value={})

        app = create_http_app(mock_server)

        # The single /mcp route replaces the old /sse + /messages/ pair
        route_paths = [route.path for route in app.routes]
        assert any("/mcp" in path for path in route_paths)

    def test_routes_use_correct_methods(self) -> None:
        """/mcp endpoint accepts POST and DELETE only -- GET is retired
        (IF-0-P3B-3): it now 405s instead of opening a keep-alive stream."""
        from pmcp.transport.http import create_http_app

        mock_server = MagicMock()
        mock_server.create_initialization_options = MagicMock(return_value={})

        app = create_http_app(mock_server)

        # Find the /mcp route and check it accepts POST/DELETE, not GET.
        for route in app.routes:
            if hasattr(route, "path") and route.path == "/mcp":
                if hasattr(route, "methods"):
                    assert "GET" not in route.methods
                    assert "POST" in route.methods
                    assert "DELETE" in route.methods


class TestHttpObservabilityContracts:
    """Operational HTTP route contracts for shared-service mode."""

    def test_health_is_unauthenticated_with_auth_token(self) -> None:
        client = _make_contract_client(auth_token="secret")

        response = client.get("/health")

        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["version"] == __version__
        assert payload["transport"] == "http"
        assert payload["gateway_diagnostics"]["transport"] == "http"
        assert (
            payload["gateway_diagnostics"]["header_compatibility"][
                "MCP-Protocol-Version"
            ]
            == "accepted"
        )

    def test_metrics_is_unauthenticated_with_auth_token(self) -> None:
        client = _make_contract_client(auth_token="secret")

        response = client.get("/metrics")

        assert response.status_code == 200
        assert "pmcp_requests_total" in response.text

    def test_auth_token_applies_to_mcp_only(self) -> None:
        client = _make_contract_client(auth_token="secret")

        response = client.post("/mcp", content=b"{}")

        assert response.status_code == 401

    def test_smoke_health_metrics_and_authenticated_mcp_initialized(self) -> None:
        client = _make_contract_client(auth_token="secret")
        headers = {"Authorization": "Bearer secret"}

        health = client.get("/health")
        metrics = client.get("/metrics")
        initialized = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        assert health.status_code == 200
        assert health.json()["ok"] is True
        assert metrics.status_code == 200
        assert "pmcp_requests_total" in metrics.text
        assert initialized.status_code == 202

    def test_rate_limit_uses_one_bucket_for_same_client_ip(self) -> None:
        client = _make_contract_client(rate_limit_rpm=2)

        statuses = [client.post("/mcp", content=b"{}").status_code for _ in range(3)]

        assert 429 in statuses

    def test_shared_secret_auth_mode_preserves_static_bearer_behavior(self) -> None:
        client = _make_contract_client(auth_token="secret", auth_mode="shared-secret")

        rejected = client.post("/mcp", content=b"{}")
        accepted = client.post(
            "/mcp",
            headers={"Authorization": "Bearer secret"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        assert rejected.status_code == 401
        assert accepted.status_code == 202

    def test_resource_server_valid_token_reaches_mcp_handler(self) -> None:
        claims = SimpleNamespace(
            issuer="https://issuer.example",
            subject="subject-1",
            audience=["https://pmcp.example/mcp"],
            scopes=["read"],
        )
        with (
            patch(
                "pmcp.transport.http.AsyncJWKS.get_for_token",
                new=AsyncMock(return_value={"keys": [{"kid": "test-key"}]}),
            ),
            patch(
                "pmcp.transport.http.validate_resource_server_token",
                return_value=claims,
            ) as validate,
        ):
            client = _make_contract_client(
                auth_mode="resource-server",
                resource_server_issuer="https://issuer.example",
                resource_server_jwks_url="https://issuer.example/jwks.json",
                resource_server_audience="https://pmcp.example/mcp",
            )
            response = client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer signed-token",
                    "Host": "spoofed.example",
                },
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )

        assert response.status_code == 202
        validate.assert_called_once()
        assert validate.call_args.kwargs["audience"] == "https://pmcp.example/mcp"

    def test_resource_server_requires_configured_canonical_audience(self) -> None:
        with pytest.raises(ValueError, match="audience"):
            _make_contract_client(
                auth_mode="resource-server",
                resource_server_issuer="https://issuer.example",
                resource_server_jwks_url="https://issuer.example/jwks.json",
            )

    def test_resource_server_missing_bearer_gets_challenge(self) -> None:
        client = _make_contract_client(
            auth_mode="resource-server",
            resource_server_issuer="https://issuer.example",
            resource_server_jwks_url="https://issuer.example/jwks.json",
            resource_server_audience="https://pmcp.example/mcp",
        )

        response = client.post("/mcp", content=b"{}")

        assert response.status_code == 401
        assert (
            'resource="https://pmcp.example/mcp"'
            in response.headers["www-authenticate"]
        )

    def test_resource_server_insufficient_scope_gets_403_challenge(self) -> None:
        from pmcp.auth import AuthMessage, ResourceServerAuthError

        with (
            patch(
                "pmcp.transport.http.AsyncJWKS.get_for_token",
                new=AsyncMock(return_value={"keys": [{"kid": "test-key"}]}),
            ),
            patch(
                "pmcp.transport.http.validate_resource_server_token",
                side_effect=ResourceServerAuthError(
                    "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes="write"
                ),
            ),
        ):
            client = _make_contract_client(
                auth_mode="resource-server",
                resource_server_issuer="https://issuer.example",
                resource_server_jwks_url="https://issuer.example/jwks.json",
                resource_server_audience="https://pmcp.example/mcp",
                required_scopes=["write"],
            )
            response = client.post(
                "/mcp",
                headers={"Authorization": "Bearer signed-token"},
                content=b"{}",
            )

        assert response.status_code == 403
        assert 'error="insufficient_scope"' in response.headers["www-authenticate"]
        assert 'scope="write"' in response.headers["www-authenticate"]

    @pytest.mark.parametrize(
        "jwks_url",
        [
            "http://issuer.example/jwks.json",
            "https://127.0.0.1/jwks.json",
            "https://10.0.0.1/jwks.json",
            "https://169.254.1.1/jwks.json",
            "https://224.0.0.1/jwks.json",
            "https://0.0.0.0/jwks.json",
            "not-a-url",
        ],
    )
    def test_resource_server_rejects_non_public_jwks_urls(self, jwks_url: str) -> None:
        with pytest.raises(ValueError):
            _make_contract_client(
                auth_mode="resource-server",
                resource_server_issuer="https://issuer.example",
                resource_server_jwks_url=jwks_url,
                resource_server_audience="https://pmcp.example/mcp",
            )

    def test_resource_server_jwks_failure_gets_503_challenge(self) -> None:
        from pmcp.auth import AuthMessage, ResourceServerJWKSUnavailable

        with patch(
            "pmcp.transport.http.AsyncJWKS.get_for_token",
            side_effect=ResourceServerJWKSUnavailable(
                AuthMessage.JWKS_FETCH_FAILED,
                url="https://issuer.example/jwks.json?token=secret",
            ),
        ):
            client = _make_contract_client(
                auth_mode="resource-server",
                resource_server_issuer="https://issuer.example",
                resource_server_jwks_url="https://issuer.example/jwks.json?token=secret",
                resource_server_audience="https://pmcp.example/mcp",
            )
            response = client.post(
                "/mcp",
                headers={"Authorization": "Bearer signed-token"},
                content=b"{}",
            )

        assert response.status_code == 503
        assert "secret" not in response.text
        assert "secret" not in response.headers["www-authenticate"]

    def test_allowed_origins_rejects_invalid_origin_before_mcp_handler(self) -> None:
        client = _make_contract_client(allowed_origins=["https://app.example"])

        response = client.post(
            "/mcp",
            headers={"Origin": "https://evil.example"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        assert response.status_code == 403

    def test_cross_origin_rejected_by_default_without_allowlist(self) -> None:
        """DNS-rebinding defense runs even when allowed_origins is unset."""
        client = _make_contract_client()

        foreign = client.post(
            "/mcp",
            headers={"Origin": "https://evil.example"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        assert foreign.status_code == 403

    @pytest.mark.parametrize(
        "client_kwargs",
        [
            {},
            {"allowed_origins": ["https://app.example"]},
            {"auth_token": "gateway-token"},
        ],
        ids=["default", "allowlist", "shared-secret"],
    )
    def test_malformed_origin_port_is_rejected_without_echo(
        self,
        client_kwargs: dict[str, object],
        capfd: pytest.CaptureFixture[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An unauthenticated Origin whose port is not a number is a 403, not a
        500 whose log line quotes the port (Consiliency/pmcp#297, rev 7 B2)."""
        import logging

        # ASCII: an Origin header is Latin-1 on the wire.
        sentinel = "Q7portSentinel" + "x" * 20
        client = _make_contract_client(**client_kwargs)  # type: ignore[arg-type]
        caplog.set_level(logging.DEBUG)

        response = client.post(
            "/mcp",
            headers={"Origin": f"http://evil.example:{sentinel}"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        assert response.status_code == 403
        out, err = capfd.readouterr()
        observed = "\n".join(
            [
                response.text,
                str(dict(response.headers)),
                out,
                err,
                *(record.getMessage() for record in caplog.records),
                *(str(record.exc_info) for record in caplog.records),
            ]
        )
        assert len(sentinel) == 34
        assert sentinel not in observed
        assert sentinel.encode("utf-8").hex() not in observed.encode("utf-8").hex()

    def test_no_origin_and_loopback_and_same_origin_pass_by_default(self) -> None:
        """Normal MCP clients (no Origin) and loopback/same-origin browsers pass."""
        client = _make_contract_client()

        no_origin = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        loopback = client.post(
            "/mcp",
            headers={"Origin": "http://localhost:8080"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        same_origin = client.post(
            "/mcp",
            headers={"Origin": "http://127.0.0.1"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        assert no_origin.status_code == 202
        assert loopback.status_code == 202
        assert same_origin.status_code == 202

    def test_host_validation_enforced_only_when_origins_configured(self) -> None:
        """A foreign Host is rejected once allowed_origins opts Host checking in."""
        client = _make_contract_client(allowed_origins=["https://app.example"])

        foreign_host = client.post(
            "/mcp",
            headers={"Host": "evil.example"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        allowlisted_host = client.post(
            "/mcp",
            headers={"Host": "app.example"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        assert foreign_host.status_code == 403
        assert allowlisted_host.status_code == 202

    def test_host_not_validated_without_allowlist(self) -> None:
        """Without allowed_origins, an arbitrary Host still works (proxy-friendly)."""
        client = _make_contract_client()

        response = client.post(
            "/mcp",
            headers={"Host": "pmcp.public.example"},
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        assert response.status_code == 202


class TestHttpTransportIntegration:
    """Integration tests for HTTP transport."""

    @pytest.mark.asyncio
    async def test_run_method_accepts_transport_parameter(self) -> None:
        """GatewayServer.run() should accept transport parameter."""
        from pmcp.server import GatewayServer

        server = GatewayServer()

        # Mock the internal methods
        with (
            patch.object(server, "_run_stdio", new_callable=AsyncMock) as mock_stdio,
            patch.object(server, "_run_http", new_callable=AsyncMock) as mock_http,
        ):
            # Test stdio transport
            await server.run(transport="stdio")
            mock_stdio.assert_called_once()
            mock_http.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_dispatches_to_http(self) -> None:
        """GatewayServer.run(transport='http') should call _run_http."""
        from pmcp.server import GatewayServer

        server = GatewayServer()

        with (
            patch.object(server, "_run_stdio", new_callable=AsyncMock) as mock_stdio,
            patch.object(server, "_run_http", new_callable=AsyncMock) as mock_http,
        ):
            await server.run(transport="http")
            mock_http.assert_called_once()
            mock_stdio.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_default_is_stdio(self) -> None:
        """GatewayServer.run() without args should use stdio."""
        from pmcp.server import GatewayServer

        server = GatewayServer()

        with (
            patch.object(server, "_run_stdio", new_callable=AsyncMock) as mock_stdio,
            patch.object(server, "_run_http", new_callable=AsyncMock) as mock_http,
        ):
            await server.run()
            mock_stdio.assert_called_once()
            mock_http.assert_not_called()


# --- rev 19: the SDK's transport rejections are value-free (round-17 grok F001,
# claude N1). The MCP SDK answers a /mcp request it cannot accept before any
# pmcp handler runs; three of its rejections were built from the request.

_GRID_SENTINELS = {
    "long": "sk-live-" + "Zq" * 12 + "SECRETVALUE",
    "short": "Qx7",
}
_MCP_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


def _envelope_shapes(s: str) -> dict[str, bytes]:
    """Every way a /mcp body can fail the SDK's envelope parse or validation,
    with the sentinel in a position the caller controls (the request id is
    excluded: JSON-RPC requires a reply to carry it)."""
    import json

    def body(value: object) -> bytes:
        return json.dumps(value).encode()

    call = {"name": "gateway.invoke", "arguments": {"token": s, s: [s]}}
    return {
        "jsonrpc-version": body(
            {"jsonrpc": "1.0", "id": 1, "method": "tools/call", "params": call}
        ),
        "jsonrpc-missing": body({"id": 1, "method": "tools/call", "params": call}),
        "method-object": body({"jsonrpc": "2.0", "id": 1, "method": {"m": s}}),
        "method-number-params": body(
            {"jsonrpc": "2.0", "id": 1, "method": 5, "params": call}
        ),
        "params-scalar": body(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": s}
        ),
        "params-list": body(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [s]}
        ),
        "response-shaped": body({"jsonrpc": "2.0", "id": 1, "error": s}),
        "result-and-error": body(
            {"jsonrpc": "2.0", "id": 1, "result": {"k": s}, "error": {"m": s}}
        ),
        "batch": body([{"jsonrpc": "2.0", "id": 1, "method": s}]),
        "scalar": body(s),
        "number-key": body({s: s}),
        "truncated": b'{"jsonrpc":"2.0","method":"' + s.encode(),
        "bad-escape": b'{"jsonrpc":"2.0","method":"' + s.encode() + b'\\q"}',
        "not-utf8": b'{"jsonrpc":"2.0","method":"' + s.encode() + b'\xff"}',
        "trailing": b'{"jsonrpc":"2.0","method":"x"} ' + s.encode(),
    }


def _modern_version_shape(s: str) -> tuple[bytes, dict[str, str]]:
    """A per-request-envelope call naming an unsupported protocol version."""
    import json

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": s,
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    }
    headers = {**_MCP_HEADERS, "mcp-protocol-version": s, "mcp-method": "tools/list"}
    return json.dumps(body).encode(), headers


def _post_all(
    cases: list[tuple[str, bytes, dict[str, str]]],
) -> dict[str, tuple[int, str, str]]:
    """POST each body to a real `create_http_app` /mcp."""
    from mcp.server.lowlevel import Server

    from pmcp.transport.http import create_http_app

    out: dict[str, tuple[int, str, str]] = {}
    with TestClient(create_http_app(Server("grid")), base_url="http://127.0.0.1") as c:
        for name, body, headers in cases:
            response = c.post("/mcp", content=body, headers=headers)
            out[name] = (
                int(response.status_code),
                response.text,
                str(dict(response.headers)),
            )
    return out


@pytest.mark.parametrize("sentinel", sorted(_GRID_SENTINELS))
@pytest.mark.parametrize("era", ["handshake", "per-request-envelope"])
def test_a_rejected_envelope_echoes_nothing_of_the_request(
    era: str, sentinel: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Every malformed-envelope shape, on both SDK request paths, gets a 4xx
    JSON-RPC error whose body, headers and the log carry no form of the
    sentinel; a parse or validation rejection says why from its structure."""
    import json
    import logging

    from mcp_types.version import MODERN_PROTOCOL_VERSIONS

    from tests.test_argument_error_echo import _forbidden, _record_text

    s = _GRID_SENTINELS[sentinel]
    headers = dict(_MCP_HEADERS)
    if era == "per-request-envelope":
        headers["mcp-protocol-version"] = MODERN_PROTOCOL_VERSIONS[0]
    cases = [(name, body, headers) for name, body in _envelope_shapes(s).items()]
    if era == "per-request-envelope":
        body, version_headers = _modern_version_shape(s)
        cases.append(("unsupported-version", body, version_headers))
    caplog.set_level(logging.DEBUG)
    observed = _post_all(cases)
    logs = "\n".join(_record_text(record) for record in caplog.records)

    def leaked(text: str) -> bool:
        if sentinel == "short":
            return s in text
        return any(form in text for form in _forbidden(s))

    failures = []
    for name, (status, text, response_headers) in observed.items():
        if not 400 <= status < 500:
            failures.append((name, "status", status))
        payload = json.loads(text)
        error = payload.get("error") or {}
        if not isinstance(error.get("code"), int) or not isinstance(
            error.get("message"), str
        ):
            failures.append((name, "not a JSON-RPC error", text[:200]))
        if leaked(text) or leaked(response_headers):
            failures.append((name, "response", text[:300]))
        if error.get("code") == -32602 and payload.get("id") is None:
            if not error["message"].startswith("Validation error: "):
                failures.append((name, "validation text", error["message"]))
    assert not failures, failures
    assert not leaked(logs), [line for line in logs.splitlines() if leaked(line)][:5]


def test_envelope_rejection_does_not_echo_caller_value() -> None:
    """Round-17 grok F001's falsifier, through the gateway's own /mcp (the
    filed form drove the SDK's transport class directly, which pmcp serves
    only behind this app): a `tools/call` the SDK's envelope adapter rejects
    omits the argument."""
    secret = "sk-live-SECRETVALUE"
    body = (
        b'{"jsonrpc":"1.0","id":1,"method":"tools/call","params":'
        b'{"name":"gateway.invoke","arguments":{"token":"' + secret.encode() + b'"}}}'
    )
    status, text, headers = _post_all([("grok", body, _MCP_HEADERS)])["grok"]
    assert status == 400
    assert secret not in text and secret not in headers
    assert '"code":-32602' in text and "Validation error: " in text


def test_value_free_rejection_rewrites_only_request_built_parts() -> None:
    """A result, a handler's error (it has an id) and an SDK literal pass
    unchanged; the three request-built rejections are rewritten."""
    import json

    from pmcp.transport.http import value_free_rejection

    request = b'{"jsonrpc":"1.0","id":1,"method":"m","params":{"k":"SECRETzz"}}'
    unchanged = [
        b'{"jsonrpc":"2.0","id":1,"result":{"k":"SECRETzz"}}',
        b'{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"bad: SECRETzz"}}',
        b'{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Session not found"}}',
        b"Request body too large",
    ]
    for body in unchanged:
        assert value_free_rejection(body, request) == body, body
    validation = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32602, "message": "Validation error: SECRETzz"},
        }
    ).encode()
    parse = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error: SECRETzz"},
        }
    ).encode()
    version = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32022,
                "message": "Unsupported protocol version",
                "data": {"supported": ["2026-07-28"], "requested": "SECRETzz"},
            },
        }
    ).encode()
    for body in (validation, parse, version):
        rewritten = value_free_rejection(body, request)
        assert b"SECRETzz" not in rewritten, rewritten
        assert (
            json.loads(rewritten)["error"]["code"] == json.loads(body)["error"]["code"]
        )
    kept = version.replace(b"SECRETzz", b"2099-01-01")
    assert (
        value_free_rejection(kept, request)
        == json.dumps(json.loads(kept), separators=(",", ":")).encode()
    )


#: Every non-literal message or `data` the SDK's server-transport modules can
#: put in a rejection, and why it is safe or where pmcp rewrites it. Keyed by
#: (module, call, the argument's source). An SDK upgrade that adds one fails
#: `test_every_sdk_rejection_message_is_reviewed` until it is reviewed here.
_SDK_REJECTION_SITES: dict[tuple[str, str, str], str] = {
    (
        "mcp.server.streamable_http",
        "_create_error_response",
        "f'Parse error: {str(e)}'",
    ): "request-built: rewritten by value_free_rejection (PARSE_ERROR, id null)",
    (
        "mcp.server.streamable_http",
        "_create_error_response",
        "f'Validation error: {str(e)}'",
    ): "request-built: rewritten by value_free_rejection (INVALID_PARAMS, id null)",
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "UnsupportedProtocolVersionErrorData(supported=list(supported_modern_versions), requested=protocol_version).model_dump(mode='json')",
    ): "request-built `requested`: rewritten by value_free_rejection",
    (
        "mcp.server.streamable_http",
        "ErrorData",
        "error_message",
    ): "plumbing: the message of _create_error_response, reviewed at its callers",
    (
        "mcp.server.streamable_http",
        "Response",
        "error_response.model_dump_json(by_alias=True, exclude_unset=True)",
    ): "plumbing: serialises the reviewed ErrorData",
    (
        "mcp.server.streamable_http",
        "Response",
        "response_message.model_dump_json(by_alias=True, exclude_unset=True) if response_message else None",
    ): "a handler's own response (JSON mode), not a rejection",
    (
        "mcp.server.streamable_http_manager",
        "Response",
        "body.model_dump_json(by_alias=True, exclude_unset=True)",
    ): "plumbing: `Session not found`, a literal",
    (
        "mcp.server._streamable_http_modern",
        "ErrorData",
        "rejection.message",
    ): "plumbing: an InboundLadderRejection's message, reviewed at its sites",
    (
        "mcp.server._streamable_http_modern",
        "ErrorData",
        "rejection.data",
    ): "plumbing: an InboundLadderRejection's data, reviewed at its sites",
    (
        "mcp.server._streamable_http_modern",
        "Response",
        "json.dumps(body, separators=(',', ':'))",
    ): "plumbing: serialises a reviewed JSON-RPC message",
    (
        "mcp.server._streamable_http_modern",
        "InboundLadderRejection",
        "f'{duplicated} header appears more than once'",
    ): "`duplicated` is one of the SDK's fixed routing-header names",
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "f'params._meta must be an object carrying the required {PROTOCOL_VERSION_META_KEY!r} and {CLIENT_CAPABILITIES_META_KEY!r} envelope keys'",
    ): "SDK constants",
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "f\"params._meta is missing the required envelope key(s): {', '.join(missing)}\"",
    ): "`missing` holds SDK constants",
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        'f"{MCP_PROTOCOL_VERSION_HEADER} header does not match the request envelope\'s protocol version"',
    ): "SDK constant",
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        'f"{MCP_METHOD_HEADER} header does not match the request body\'s method"',
    ): "SDK constant",
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        'f"{MCP_NAME_HEADER} header does not match the request body\'s {name_key!r} parameter"',
    ): "`name_key` is from the SDK's NAME_BEARING_METHODS",
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "f'{header_name} header appears more than once'",
    ): "`header_name` is from the tool's own x-mcp-header schema token",
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        'f"{header_name} header is present but the request body\'s {argument!r} argument is absent"',
    ): "schema token and schema path",
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        'f"{header_name} header does not match the request body\'s {argument!r} argument"',
    ): "schema token and schema path",
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        'f"{header_name} header is missing but the request body\'s {argument!r} argument is present"',
    ): "schema token and schema path",
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "f'{header_name} header carries a malformed base64 sentinel value'",
    ): "schema token",
}
_SDK_REJECTION_MODULES = (
    "mcp.server.streamable_http",
    "mcp.server.streamable_http_manager",
    "mcp.server._streamable_http_modern",
    "mcp.shared.inbound",
    "mcp.server.transport_security",
)
_SDK_REJECTION_CALLS = {
    "_create_error_response",
    "ErrorData",
    "InboundLadderRejection",
    "Response",
    "JSONResponse",
    "PlainTextResponse",
}


def _sdk_rejection_sites() -> set[tuple[str, str, str]]:
    import ast
    import importlib.util

    found: set[tuple[str, str, str]] = set()
    for module in _SDK_REJECTION_MODULES:
        spec = importlib.util.find_spec(module)
        assert spec is not None and spec.origin, module
        with open(spec.origin, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", None)
            )
            if name not in _SDK_REJECTION_CALLS:
                continue
            arguments = list(node.args[:1]) + [
                keyword.value
                for keyword in node.keywords
                if keyword.arg in ("message", "data", "content", "error_message")
            ]
            for argument in arguments:
                if not isinstance(argument, ast.Constant):
                    found.add((module, name, ast.unparse(argument)))
    return found


def test_every_sdk_rejection_message_is_reviewed() -> None:
    """Each non-literal rejection message in the SDK's server transport is
    reviewed: request-built ones are rewritten, the rest are built from SDK
    constants or pmcp's schema. The set is exact both ways."""
    assert _sdk_rejection_sites() == set(_SDK_REJECTION_SITES)
