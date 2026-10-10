"""Tests for HTTP transport (Phase 1)."""

from __future__ import annotations

import ast
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
    """A result, any id-bearing error and an SDK literal pass unchanged; the
    two request-built out-of-session rejections are rewritten."""
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
    for body in (validation, parse):
        rewritten = value_free_rejection(body, request)
        assert b"SECRETzz" not in rewritten, rewritten
        assert (
            json.loads(rewritten)["error"]["code"] == json.loads(body)["error"]["code"]
        )
    # Each says why from the body's structure, not a bare phrase (rev 20).
    assert json.loads(value_free_rejection(validation, request))["error"][
        "message"
    ].startswith("Validation error: 6 validation errors for "), validation
    assert json.loads(value_free_rejection(parse, b'{"jsonrpc": '))["error"][
        "message"
    ] == (
        "Parse error: could not parse JSON request body at line 1, column 13 "
        "(JSONDecodeError)"
    )
    # An id-bearing error is the write side's (`pmcp.sdk_rejections`): this
    # layer forwards it as sent.
    assert value_free_rejection(version, request) == version


# --- rev 20: the SDK's rejections are rebuilt where they are made, for every
# transport (round-18 claude F001); its server-side logs are masked (N2). -----

_MODERN = "2026-07-28"
_META = {
    "io.modelcontextprotocol/protocolVersion": _MODERN,
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _sdk_rejection_frames(s: str) -> dict[str, dict[str, object]]:
    """Requests the SDK itself refuses on a modern connection, each with the
    sentinel where the caller controls the content (never in the id)."""
    meta_bad_version = {**_META, "io.modelcontextprotocol/protocolVersion": s}
    return {
        "unknown-method": {
            "jsonrpc": "2.0",
            "id": 2,
            "method": s,
            "params": {"_meta": _META},
        },
        "unknown-method-path": {
            "jsonrpc": "2.0",
            "id": 2,
            "method": f"tools/{s}",
            "params": {"_meta": _META},
        },
        "unsupported-version": {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {"_meta": meta_bad_version},
        },
        "initialize-on-modern": {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {
                "protocolVersion": s,
                "capabilities": {},
                "clientInfo": {"name": s, "version": s},
            },
        },
        "envelope-missing-key": {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {
                "_meta": {"io.modelcontextprotocol/protocolVersion": _MODERN, s: s}
            },
        },
        "invalid-params": {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"_meta": _META, "name": {s: s}, "arguments": s},
        },
        "unknown-notification": {
            "jsonrpc": "2.0",
            "method": f"notifications/{s}",
            "params": {"_meta": _META},
        },
    }


def _stdio_exchange(
    tmp_path: object, frames: list[dict[str, object]]
) -> tuple[str, str]:
    """Drive the real `pmcp` entry point over stdio at DEBUG: send `frames`,
    read until every request among them is answered, then close stdin."""
    import json
    import subprocess
    import sys
    import threading
    from pathlib import Path

    root = Path(str(tmp_path))
    home = root / "home"
    home.mkdir(exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PMCP_", "npm_config_", "pnpm_config_"))
    }
    env["HOME"] = str(home)
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from pmcp.cli import main; main()",
            "--log-level",
            "debug",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=root,
        env=env,
    )
    assert process.stdin and process.stdout and process.stderr
    err_chunks: list[bytes] = []
    stderr = process.stderr
    reader = threading.Thread(target=lambda: err_chunks.append(stderr.read()))
    reader.start()
    waiting = {frame["id"] for frame in frames if "id" in frame}
    out_lines: list[str] = []
    try:
        for frame in frames:
            process.stdin.write((json.dumps(frame) + "\n").encode())
        process.stdin.flush()
        while waiting:
            line = process.stdout.readline().decode()
            if not line:
                break
            out_lines.append(line)
            try:
                waiting.discard(json.loads(line).get("id"))
            except ValueError:
                pass
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=60)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
        reader.join(timeout=60)
    assert not waiting, ("unanswered", waiting, "".join(out_lines)[-400:])
    return "".join(out_lines), b"".join(err_chunks).decode(errors="replace")


@pytest.mark.parametrize("sentinel", sorted(_GRID_SENTINELS))
def test_an_sdk_rejection_over_stdio_echoes_nothing_of_the_request(
    tmp_path: object, sentinel: str
) -> None:
    """pmcp's default transport, at DEBUG: every request the SDK refuses on a
    modern connection is answered with its id and an error, and neither the
    answers nor stderr carry any form of the sentinel."""
    import json

    from tests.test_argument_error_echo import _forbidden

    s = _GRID_SENTINELS[sentinel]
    opening = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"_meta": _META},
    }
    frames: list[dict[str, object]] = [opening]
    for index, frame in enumerate(_sdk_rejection_frames(s).values()):
        frame = dict(frame)
        if "id" in frame:
            frame["id"] = 100 + index
        frames.append(frame)
    out, err = _stdio_exchange(tmp_path, frames)
    replies = {
        reply["id"]: reply
        for reply in map(json.loads, out.splitlines())
        if isinstance(reply, dict) and "id" in reply
    }
    for frame in frames[1:]:
        if "id" in frame:
            assert "error" in replies[frame["id"]], replies[frame["id"]]

    def leaked(text: str) -> bool:
        if sentinel == "short":
            return s in text
        return any(form in text for form in _forbidden(s))

    assert not leaked(out), [line for line in out.splitlines() if leaked(line)]
    assert not leaked(err), [line for line in err.splitlines() if leaked(line)][:5]
    assert "[DEBUG]" in err  # the logs were on


def test_a_round_18_stdio_rejection_echoes_nothing_of_the_request(
    tmp_path: object,
) -> None:
    """Round-18 claude F001's three stdio cases as filed, with stdin held open
    until each is answered (closing it at once races the answer)."""
    secret = "sk-live-SECRETREJECTEDVALUE_31"
    opening = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"_meta": _META},
    }
    for frame in (
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {
                "_meta": {**_META, "io.modelcontextprotocol/protocolVersion": secret}
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {
                "protocolVersion": secret,
                "capabilities": {},
                "clientInfo": {"name": "c", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "id": 2, "method": secret, "params": {"_meta": _META}},
    ):
        out, _ = _stdio_exchange(tmp_path, [opening, frame])
        assert '"id":2' in out.replace(" ", ""), out[-500:]
        assert secret not in out


@pytest.mark.parametrize("sentinel", sorted(_GRID_SENTINELS))
def test_an_sdk_rejection_over_http_echoes_nothing_of_the_request(
    sentinel: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The same requests on `/mcp`, both SDK paths, at DEBUG: the modern
    per-request path (a 4xx JSON body) and the legacy session path (an SSE
    frame after the initialize handshake). Body, headers and every log
    record carry no form of the sentinel."""
    import json
    import logging

    from mcp.server.lowlevel import Server

    from pmcp.transport.http import create_http_app
    from tests.test_argument_error_echo import _forbidden, _record_text

    s = _GRID_SENTINELS[sentinel]
    caplog.set_level(logging.DEBUG)
    seen: list[str] = []
    app = create_http_app(Server("rev20"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for name, frame in _sdk_rejection_frames(s).items():
            if name == "unknown-notification":
                continue
            headers = {
                **_MCP_HEADERS,
                "mcp-protocol-version": _MODERN,
                "mcp-method": str(frame["method"]),
            }
            if name == "unsupported-version":
                headers["mcp-protocol-version"] = s
            response = client.post("/mcp", content=json.dumps(frame), headers=headers)
            seen.append(
                f"{name} {response.status_code} {response.text} {dict(response.headers)}"
            )
        legacy = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "c", "version": "1"},
            },
        }
        opened = client.post("/mcp", content=json.dumps(legacy), headers=_MCP_HEADERS)
        session = opened.headers.get("mcp-session-id")
        assert session, opened.text
        session_headers = {
            **_MCP_HEADERS,
            "mcp-session-id": session,
            "mcp-protocol-version": "2025-06-18",
        }
        client.post(
            "/mcp",
            content=json.dumps(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            ),
            headers=session_headers,
        )
        for frame in (
            {"jsonrpc": "2.0", "id": 7, "method": s, "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {"name": {s: s}},
            },
            {"jsonrpc": "2.0", "method": f"notifications/{s}"},
        ):
            response = client.post(
                "/mcp", content=json.dumps(frame), headers=session_headers
            )
            seen.append(f"legacy {response.status_code} {response.text}")
    logs = "\n".join(_record_text(record) for record in caplog.records)

    def leaked(text: str) -> bool:
        if sentinel == "short":
            return s in text
        return any(form in text for form in _forbidden(s))

    assert not [item for item in seen if leaked(item)], [
        item for item in seen if leaked(item)
    ]
    assert not leaked(logs), [line for line in logs.splitlines() if leaked(line)][:5]
    assert sum("-32601" in item for item in seen) >= 3, seen
    assert any(record.name.startswith("sse_starlette") for record in caplog.records)


def test_a_pmcp_handler_error_passes_the_write_side_unchanged() -> None:
    """The rewrite never touches what pmcp's own handler raised: it carries
    `PMCP_HANDLER_MARK` and is mapped as the SDK maps it. An unmarked error
    keeps its code; its message only if reviewed; its `data` only in a
    reviewed shape."""
    import mcp.shared.jsonrpc_dispatcher as dispatcher
    from mcp.shared.exceptions import MCPError

    from pmcp.sdk_rejections import PMCP_HANDLER_MARK

    mapping = dispatcher.handler_exception_to_error_data
    mine = MCPError(code=-32602, message="Unknown tool: x", data={"k": "v"})
    setattr(mine, PMCP_HANDLER_MARK, True)
    error = mapping(mine)
    assert (error.code, error.message, error.data) == (
        -32602,
        "Unknown tool: x",
        {"k": "v"},
    )
    error = mapping(MCPError(code=-32601, message="Method not found", data="SECRETzz"))
    assert (error.code, error.message, error.data) == (-32601, "Method not found", None)
    error = mapping(MCPError(code=-32602, message="bad SECRETzz", data="SECRETzz"))
    assert (error.code, error.message, error.data) == (-32602, "Invalid params", None)
    error = mapping(
        MCPError(
            code=-32022,
            message="Unsupported protocol version",
            data={"supported": ["2026-07-28"], "requested": "SECRETzz"},
        )
    )
    assert error.data == {"supported": ["2026-07-28"], "requested": ""}
    error = mapping(
        MCPError(
            code=-32022,
            message="Unsupported protocol version",
            data={"supported": ["2026-07-28"], "requested": "2099-01-01"},
        )
    )
    assert error.data["requested"] == "2099-01-01"


#: Every construction anywhere in the installed `mcp` package that can put a
#: non-literal message or `data` into a JSON-RPC error, and how pmcp treats
#: it. Keyed by (module, call, argument, source). Derived by AST from the
#: whole package (round-18 ruling), exact both ways: an SDK upgrade that adds
#: one fails here until it is classified.
_WRITE = "server, in an exchange: rebuilt by pmcp.sdk_rejections"
_PRE = "server, out of session (id null): rewritten by value_free_rejection"
_PLUMB = "plumbing: carries an error classified where it is built"
_CLIENT = (
    "client side: withheld by origin unless a literal; a relayed peer message "
    "is kept unless it matches a template the SDK builds (rev 26)"
)
_UNUSED = "unreachable: pmcp serves the low-level Server over JSONRPCDispatcher"
_SDK_ERROR_CONSTRUCTIONS: dict[tuple[str, str, str, str], str] = {
    ("mcp.client.session", "MCPError", "data", "method"): _CLIENT,
    (
        "mcp.client.session_group",
        "MCPError",
        "message",
        "f'{matching_prompts} already exist in group prompts.'",
    ): _CLIENT,
    (
        "mcp.client.session_group",
        "MCPError",
        "message",
        "f'{matching_resources} already exist in group resources.'",
    ): _CLIENT,
    (
        "mcp.client.session_group",
        "MCPError",
        "message",
        "f'{matching_tools} already exist in group tools.'",
    ): _CLIENT,
    ("mcp.client.streamable_http", "ErrorData", "message", "message"): _CLIENT,
    (
        "mcp.client.streamable_http",
        "ErrorData",
        "message",
        "f'Failed to parse JSON response: {exc}'",
    ): _CLIENT,
    (
        "mcp.client.streamable_http",
        "ErrorData",
        "message",
        "f'Failed to parse SSE message: {exc}'",
    ): _CLIENT,
    (
        "mcp.client.streamable_http",
        "ErrorData",
        "message",
        "f'Unexpected content type: {content_type}'",
    ): _CLIENT,
    (
        "mcp.client.subscriptions",
        "MCPError",
        "message",
        "f'subscription backlog exceeded {_MAX_PENDING_EVENTS} unconsumed events; "
        "re-listen and refetch'",
    ): _CLIENT,
    ("mcp.client.subscriptions", "MCPError", "message", "str(error)"): _CLIENT,
    (
        "mcp.server._streamable_http_modern",
        "ErrorData",
        "message",
        "rejection.message",
    ): _WRITE,
    (
        "mcp.server._streamable_http_modern",
        "ErrorData",
        "data",
        "rejection.data",
    ): _WRITE,
    (
        "mcp.server._streamable_http_modern",
        "InboundLadderRejection",
        "message",
        "f'{duplicated} header appears more than once'",
    ): _WRITE,
    (
        "mcp.server.mcpserver.resolve",
        "MCPError",
        "message",
        "f'Client did not declare the {name} capability required by resolver {key!r}'",
    ): _UNUSED,
    (
        "mcp.server.mcpserver.resolve",
        "MCPError",
        "data",
        "data.model_dump(by_alias=True, mode='json', exclude_none=True)",
    ): _UNUSED,
    (
        "mcp.server.mcpserver.server",
        "MCPError",
        "message",
        "f'Client did not declare required extension {identifier!r}'",
    ): _UNUSED,
    (
        "mcp.server.mcpserver.server",
        "MCPError",
        "data",
        "data.model_dump(by_alias=True, mode='json', exclude_none=True)",
    ): _UNUSED,
    ("mcp.server.mcpserver.server", "MCPError", "data", "method.method"): _UNUSED,
    ("mcp.server.mcpserver.server", "MCPError", "message", "str(err)"): _UNUSED,
    (
        "mcp.server.mcpserver.server",
        "MCPError",
        "data",
        "{'uri': str(params.uri)}",
    ): _UNUSED,
    (
        "mcp.server.request_state",
        "MCPError",
        "data",
        "{'reason': 'invalid_request_state'}",
    ): _WRITE,
    (
        "mcp.server.runner",
        "MCPError",
        "data",
        "_initialize_after_modern_data(params)",
    ): _WRITE,
    ("mcp.server.runner", "MCPError", "message", "route.message"): _WRITE,
    ("mcp.server.runner", "MCPError", "data", "route.data"): _WRITE,
    ("mcp.server.runner", "MCPError", "data", "method"): _WRITE,
    ("mcp.server.runner", "MCPError", "message", "error.message"): _WRITE,
    ("mcp.server.runner", "MCPError", "data", "error.data"): _WRITE,
    ("mcp.server.streamable_http", "ErrorData", "message", "error_message"): _PLUMB,
    (
        "mcp.server.streamable_http",
        "_create_error_response",
        "error_message",
        "f'Parse error: {str(e)}'",
    ): _PRE,
    (
        "mcp.server.streamable_http",
        "_create_error_response",
        "error_message",
        "f'Validation error: {str(e)}'",
    ): _PRE,
    (
        "mcp.shared.direct_dispatcher",
        "MCPError",
        "message",
        "f\"Timed out after {opts.get('timeout')}s waiting for {method!r}\"",
    ): _UNUSED,
    ("mcp.shared.direct_dispatcher", "MCPError", "message", "str(e)"): _UNUSED,
    ("mcp.shared.exceptions", "ErrorData", "message", "message"): _PLUMB,
    ("mcp.shared.exceptions", "ErrorData", "data", "data"): _PLUMB,
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "message",
        "f'params._meta must be an object carrying the required "
        "{PROTOCOL_VERSION_META_KEY!r} and {CLIENT_CAPABILITIES_META_KEY!r} "
        "envelope keys'",
    ): _WRITE,
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "message",
        "f\"params._meta is missing the required envelope key(s): {', '.join(missing)}\"",
    ): _WRITE,
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "data",
        "UnsupportedProtocolVersionErrorData(supported=list(supported_modern_versions), "
        "requested=protocol_version).model_dump(mode='json')",
    ): _WRITE,
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "message",
        'f"{MCP_PROTOCOL_VERSION_HEADER} header does not match the request '
        "envelope's protocol version\"",
    ): _WRITE,
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "message",
        'f"{MCP_METHOD_HEADER} header does not match the request body\'s method"',
    ): _WRITE,
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "message",
        "f\"{MCP_NAME_HEADER} header does not match the request body's {name_key!r} "
        'parameter"',
    ): _WRITE,
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "message",
        "f'{header_name} header appears more than once'",
    ): _WRITE,
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "message",
        "f\"{header_name} header is present but the request body's {argument!r} "
        'argument is absent"',
    ): _WRITE,
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "message",
        "f\"{header_name} header does not match the request body's {argument!r} "
        'argument"',
    ): _WRITE,
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "message",
        "f\"{header_name} header is missing but the request body's {argument!r} "
        'argument is present"',
    ): _WRITE,
    (
        "mcp.shared.inbound",
        "InboundLadderRejection",
        "message",
        "f'{header_name} header carries a malformed base64 sentinel value'",
    ): _WRITE,
    (
        "mcp.shared.jsonrpc_dispatcher",
        "MCPError",
        "message",
        "outcome.message",
    ): _CLIENT,
    ("mcp.shared.jsonrpc_dispatcher", "MCPError", "data", "outcome.data"): _CLIENT,
    (
        "mcp.shared.jsonrpc_dispatcher",
        "MCPError",
        "message",
        "f'Request {method!r} timed out'",
    ): _CLIENT,
    ("mcp.shared.jsonrpc_dispatcher", "ErrorData", "message", "str(e)"): _WRITE,
}
_DATA_CALLS = {"ErrorData", "MCPError", "McpError", "InboundLadderRejection"}


def _sdk_error_constructions() -> set[tuple[str, str, str, str]]:
    """Every non-literal message or `data` argument of a JSON-RPC error
    construction in the installed `mcp` package."""
    import ast
    from pathlib import Path

    import mcp

    from pmcp.sdk_rejections import _MESSAGE_ARGUMENTS

    root = Path(mcp.__file__).parent
    found: set[tuple[str, str, str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        module = (
            path.relative_to(root.parent).with_suffix("").as_posix().replace("/", ".")
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", None)
            )
            if name not in _MESSAGE_ARGUMENTS:
                continue
            keyword, index = _MESSAGE_ARGUMENTS[name]
            arguments: list[tuple[str, ast.expr]] = [
                (str(item.arg), item.value)
                for item in node.keywords
                if item.arg == keyword or (item.arg == "data" and name in _DATA_CALLS)
            ]
            if index is not None and len(node.args) > index:
                arguments.append((keyword, node.args[index]))
            if name in ("MCPError", "McpError") and len(node.args) > 2:
                arguments.append(("data", node.args[2]))
            for label, value in arguments:
                if isinstance(value, ast.Constant):
                    continue
                found.add((module, name, label, ast.unparse(value)))
    return found


def test_every_sdk_error_construction_is_classified() -> None:
    """The whole `mcp` package, exact both ways (round-18 ruling)."""
    found = _sdk_error_constructions()
    assert found == set(_SDK_ERROR_CONSTRUCTIONS), (
        sorted(found - set(_SDK_ERROR_CONSTRUCTIONS)),
        sorted(set(_SDK_ERROR_CONSTRUCTIONS) - found),
    )


#: Every other exception the SDK builds from non-literal text -- `ValueError`,
#: `RuntimeError`, its own error classes -- anywhere in the package, client
#: transports included (rev 26, round-24 ruling). Each is withheld by origin
#: (`pmcp.sdk_rejections.withheld_sdk_error`): raised from the SDK's frames,
#: it renders as its class (and code), never its text. Exact both ways.
_SDK_EXCEPTION_CONSTRUCTIONS: frozenset[tuple[str, str, str]] = frozenset(
    {
        (
            "mcp.client._input_required",
            "InputRequiredRoundsExceededError",
            "max_rounds",
        ),
        (
            "mcp.client.auth.extensions.identity_assertion",
            "OAuthFlowError",
            "f'No authorization server metadata at configured issuer {self._issuer}'",
        ),
        (
            "mcp.client.auth.extensions.identity_assertion",
            "OAuthFlowError",
            "f'Token endpoint {token_endpoint} is not on the configured issuer origin {self._issuer}'",
        ),
        (
            "mcp.client.auth.extensions.identity_assertion",
            "OAuthTokenError",
            "f'Token exchange failed ({token_response.status_code}): {body}'",
        ),
        (
            "mcp.client.auth.oauth2",
            "OAuthFlowError",
            "f'Protected Resource Metadata request failed: {response.status_code}'",
        ),
        (
            "mcp.client.auth.oauth2",
            "OAuthFlowError",
            "f'Protected resource {prm_resource} does not match expected {default_resource}'",
        ),
        (
            "mcp.client.auth.oauth2",
            "OAuthFlowError",
            "f'State parameter mismatch: {result.state} != {state}'",
        ),
        (
            "mcp.client.auth.oauth2",
            "OAuthRegistrationError",
            "f'Authorization server registered the client for {method!r} but issued no client_secret'",
        ),
        (
            "mcp.client.auth.oauth2",
            "OAuthRegistrationError",
            "f'Authorization server registered the client with unsupported token_endpoint_auth_method {method!r}'",
        ),
        (
            "mcp.client.auth.oauth2",
            "OAuthTokenError",
            "f'Registered client uses unsupported token_endpoint_auth_method {auth_method!r}'",
        ),
        (
            "mcp.client.auth.oauth2",
            "OAuthTokenError",
            "f'Token exchange failed ({response.status_code}): {body_text}'",
        ),
        (
            "mcp.client.auth.oauth2",
            "ValueError",
            "f'client_metadata_url must be a valid HTTPS URL with a non-root pathname, got: {client_metadata_url}'",
        ),
        (
            "mcp.client.auth.utils",
            "OAuthFlowError",
            "f'Authorization response iss mismatch: {iss} != {expected}'",
        ),
        (
            "mcp.client.auth.utils",
            "OAuthFlowError",
            "f'Authorization server metadata issuer mismatch: {oauth_metadata.issuer} != {expected_issuer}'",
        ),
        (
            "mcp.client.auth.utils",
            "OAuthRegistrationError",
            "f'Invalid registration response: {e}'",
        ),
        (
            "mcp.client.auth.utils",
            "OAuthRegistrationError",
            "f'Registration failed: {response.status_code} {response.text}'",
        ),
        ("mcp.client.auth.utils", "OAuthTokenError", "f'Invalid token response: {e}'"),
        (
            "mcp.client.caching",
            "ValueError",
            "f'default_ttl_ms must be >= 0, got {self.default_ttl_ms}'",
        ),
        (
            "mcp.client.caching",
            "ValueError",
            "f'max_entries must be >= 0, got {max_entries}'",
        ),
        (
            "mcp.client.client",
            "ValueError",
            "f\"mode must be 'legacy', 'auto', or one of {list(MODERN_PROTOCOL_VERSIONS)}; got {self.mode!r}{hint}\"",
        ),
        (
            "mcp.client.client",
            "ValueError",
            "f'extension identifier {identifier!r} is passed more than once'",
        ),
        (
            "mcp.client.client",
            "ValueError",
            "f'{both} notification method {binding.method!r}; a method can have only one observer'",
        ),
        (
            "mcp.client.client",
            "ValueError",
            "f'{both} resultType {tag!r}; a wire tag can have only one resolver'",
        ),
        (
            "mcp.client.client",
            "ValueError",
            "f'{type(extension).__name__} has no `identifier`; a ClientExtension must set the `identifier` class attribute (or assign one in `__init__`) before it can be used'",
        ),
        (
            "mcp.client.extension",
            "ValueError",
            "f'claims attach to {sorted(_CLAIM_METHODS)} only; got method {self.method!r}'",
        ),
        (
            "mcp.client.extension",
            "ValueError",
            "f'protocol_versions {unrecognized} are not modern protocol revisions; claimed shapes cannot be delivered on a legacy wire (None means every modern version)'",
        ),
        (
            "mcp.client.extension",
            "ValueError",
            "f'resultType {self.result_type!r} is core protocol vocabulary'",
        ),
        (
            "mcp.client.extension",
            "ValueError",
            "f'{self.model.__name__} must subclass mcp_types.Result'",
        ),
        (
            "mcp.client.extension",
            "ValueError",
            "f'{self.model.__name__}.result_type must be Literal[{self.result_type!r}]'",
        ),
        (
            "mcp.client.extension",
            "ValueError",
            "f'{self.model.__name__}.{name} aliases {clash!r}, a typed field of the core result surface; a colliding value would fail core validation before the claim adapter runs'",
        ),
        (
            "mcp.client.session",
            "RuntimeError",
            "f'Invalid schema for tool {name}: {e}'",
        ),
        (
            "mcp.client.session",
            "RuntimeError",
            "f'Invalid structured content returned by tool {name}: {error}'",
        ),
        (
            "mcp.client.session",
            "RuntimeError",
            "f'No mutually supported modern protocol version (server: {result.supported_versions}, client: {list(MODERN_PROTOCOL_VERSIONS)})'",
        ),
        (
            "mcp.client.session",
            "RuntimeError",
            "f'Server returned InputRequiredResult; pass allow_input_required=True to receive it and retry {method}(..., input_responses=..., request_state=result.request_state).'",
        ),
        (
            "mcp.client.session",
            "RuntimeError",
            "f'Tool {name} has an output schema but did not return structured content'",
        ),
        (
            "mcp.client.session",
            "RuntimeError",
            "f'Unsupported protocol version from the server: {result.protocol_version}'",
        ),
        (
            "mcp.client.session",
            "ValueError",
            'f"result_claims key {identifier!r} has no extensions entry; a claim is only advertised through its extension\'s capability ad"',
        ),
        (
            "mcp.client.session",
            "ValueError",
            "f'duplicate notification binding for method {binding.method!r}'",
        ),
        (
            "mcp.client.session",
            "ValueError",
            "f'duplicate result claim for resultType {claim.result_type!r}'",
        ),
        (
            "mcp.client.session",
            "ValueError",
            "f'result_claims[{identifier!r}] is empty and would drop the extension from the capability ad at every version. Omit the key instead'",
        ),
        (
            "mcp.client.session",
            "ValueError",
            "f'{method} requires params[{key!r}] for Mcp-Name'",
        ),
        ("mcp.client.sse", "ValueError", "error_msg"),
        (
            "mcp.client.subscriptions",
            "ListenNotSupportedError",
            "session.protocol_version",
        ),
        ("mcp.os.win32.utilities", "OSError", "f'SetStdHandle failed for fd {fd}'"),
        ("mcp.server._streamable_http_modern", "NoBackChannelError", "method"),
        (
            "mcp.server.apps",
            "ValueError",
            "f'Apps tool {tool.fn.__name__!r} binds resource_uri {uri!r}, but no such resource is registered; add it with add_html_resource() or add_resource()'",
        ),
        (
            "mcp.server.apps",
            "ValueError",
            "f'MCP Apps URIs must use the ui:// scheme, got {uri!r}'",
        ),
        (
            "mcp.server.apps",
            "ValueError",
            "f'MCP Apps resources are served as {APP_MIME_TYPE!r}, got {resource.mime_type!r}'",
        ),
        (
            "mcp.server.auth.middleware.client_auth",
            "AuthenticationError",
            "f'Unsupported auth method: {client.token_endpoint_auth_method}'",
        ),
        (
            "mcp.server.caching",
            "TypeError",
            "f'cache_hints[{method!r}] must be a CacheHint, got {type(hint).__name__}'",
        ),
        (
            "mcp.server.caching",
            "ValueError",
            "f\"cache_hints keys must be cacheable methods (see CacheableMethod); got: {', '.join(unknown)}\"",
        ),
        (
            "mcp.server.caching",
            "ValueError",
            "f\"scope must be 'public' or 'private', got {self.scope!r}\"",
        ),
        (
            "mcp.server.caching",
            "ValueError",
            "f'ttl_ms must be >= 0, got {self.ttl_ms}'",
        ),
        ("mcp.server.connection", "NoBackChannelError", "method"),
        (
            "mcp.server.elicitation",
            "TypeError",
            "f'Elicitation schema field {field_name!r} rendered as {prop!r}, which is not a valid PrimitiveSchemaDefinition'",
        ),
        (
            "mcp.server.elicitation",
            "ValueError",
            "f'Unexpected elicitation action: {result.action}'",
        ),
        (
            "mcp.server.extension",
            "ValueError",
            "f'MethodBinding cannot bind spec method {self.method!r}; extension methods are additive — use Extension.intercept_tool_call or Server.middleware to wrap core behaviour'",
        ),
        (
            "mcp.server.extension",
            "ValueError",
            "f'MethodBinding for {self.method!r} has an empty protocol_versions set, so it could never be served; use None to admit every version'",
        ),
        (
            "mcp.server.mcpserver.prompts.base",
            "ValueError",
            "f'Could not convert prompt result to message: {msg}'",
        ),
        (
            "mcp.server.mcpserver.prompts.base",
            "ValueError",
            "f'Error rendering prompt {self.name}: {e}'",
        ),
        (
            "mcp.server.mcpserver.prompts.base",
            "ValueError",
            "f'Missing required arguments: {missing}'",
        ),
        (
            "mcp.server.mcpserver.prompts.manager",
            "ValueError",
            "f'Unknown prompt: {name}'",
        ),
        (
            "mcp.server.mcpserver.resolve",
            "ToolError",
            "f'Resolver for parameter {name!r} could not resolve: elicitation was {outcome.action}'",
        ),
        (
            "mcp.server.mcpserver.resolve",
            "ToolError",
            "f'Resolver {key!r} received a non-elicitation response'",
        ),
        (
            "mcp.server.mcpserver.resolve",
            "ToolError",
            "f'Resolver {key!r} received a response of the wrong kind'",
        ),
        (
            "mcp.server.mcpserver.resolve",
            "ToolError",
            "f'Resolver {key!r} received an accepted elicitation whose content does not match the requested schema'",
        ),
        (
            "mcp.server.mcpserver.resolve",
            "ToolError",
            "f'Resolver {key!r} received an accepted elicitation with no content'",
        ),
        (
            "mcp.server.mcpserver.resources.resource_manager",
            "ResourceNotFoundError",
            "f'Unknown resource: {uri}'",
        ),
        (
            "mcp.server.mcpserver.resources.templates",
            "ResourceError",
            "f'Error creating resource from template {uri}'",
        ),
        (
            "mcp.server.mcpserver.resources.templates",
            "ResourceSecurityError",
            "self.uri_template",
        ),
        (
            "mcp.server.mcpserver.resources.types",
            "FileNotFoundError",
            "f'Directory not found: {self.path}'",
        ),
        (
            "mcp.server.mcpserver.resources.types",
            "NotADirectoryError",
            "f'Not a directory: {self.path}'",
        ),
        (
            "mcp.server.mcpserver.resources.types",
            "ValueError",
            "f'Error listing directory {self.path}: {e}'",
        ),
        (
            "mcp.server.mcpserver.resources.types",
            "ValueError",
            "f'Error reading directory {self.path}: {e}'",
        ),
        (
            "mcp.server.mcpserver.resources.types",
            "ValueError",
            "f'Error reading file {self.path}: {e}'",
        ),
        (
            "mcp.server.mcpserver.resources.types",
            "ValueError",
            "f'Error reading resource {self.uri}: {e}'",
        ),
        ("mcp.server.mcpserver.resources.types", "ValueError", "str(e)"),
        (
            "mcp.server.mcpserver.server",
            "ResourceError",
            "f'Error reading resource {uri}'",
        ),
        ("mcp.server.mcpserver.server", "ValueError", "_MISSING_AUDIENCE"),
        (
            "mcp.server.mcpserver.server",
            "ValueError",
            "f'Extension {identifier!r} binds method {method.method!r}, which is already registered; extension methods are additive and cannot replace another handler'",
        ),
        (
            "mcp.server.mcpserver.server",
            "ValueError",
            "f'Extension {identifier!r} is already registered'",
        ),
        (
            "mcp.server.mcpserver.server",
            "ValueError",
            "f'Mismatch between URI parameters {uri_params} and function parameters {func_params}'",
        ),
        (
            "mcp.server.mcpserver.server",
            "ValueError",
            "f'Resource {uri!r} has no URI template variables, but the handler declares a Context parameter. Context injection for static resources is not supported. Add a template variable to the URI or remove the Context parameter.'",
        ),
        (
            "mcp.server.mcpserver.server",
            "ValueError",
            "f'Resource {uri!r} has no URI template variables, but the handler declares parameters {func_params}. Add matching {{...}} variables to the URI or remove the parameters.'",
        ),
        (
            "mcp.server.mcpserver.server",
            "ValueError",
            "f'Resource {uri!r}: query parameter(s) {missing_defaults} have no default value. A client may omit a {{?...}}/{{&...}} query parameter, so the matching handler parameter must declare a default.'",
        ),
        ("mcp.server.mcpserver.server", "ValueError", "f'Unknown prompt: {name}'"),
        (
            "mcp.server.mcpserver.server",
            "ValueError",
            "f'Unknown transport: {transport}'",
        ),
        ("mcp.server.mcpserver.server", "ValueError", "str(e)"),
        (
            "mcp.server.mcpserver.tools.base",
            "ToolError",
            "f'Error executing tool {self.name}: {e}'",
        ),
        (
            "mcp.server.mcpserver.tools.tool_manager",
            "ToolError",
            "f'Unknown tool: {name}'",
        ),
        (
            "mcp.server.mcpserver.utilities.func_metadata",
            "ValueError",
            "f'JSON schema warning: {kind} - {detail}'",
        ),
        (
            "mcp.server.request_state",
            "TypeError",
            "f'request-state keys must be bytes, bytearray, or str; keys[{i}] is {type(key).__name__}'",
        ),
        (
            "mcp.server.request_state",
            "ValueError",
            "f'keys[{i}] duplicates an earlier ring key'",
        ),
        (
            "mcp.server.request_state",
            "ValueError",
            "f'request-state keys must be at least 32 bytes of secret randomness; keys[{i}] is {len(k)} bytes. Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"'",
        ),
        (
            "mcp.server.request_state",
            "ValueError",
            "f'request-state ttl must be a positive finite number, got {ttl!r}'",
        ),
        ("mcp.server.runner", "NoBackChannelError", "method"),
        (
            "mcp.server.runner",
            "TypeError",
            "f'handler returned {type(result).__name__}; expected BaseModel, dict, or None'",
        ),
        (
            "mcp.server.sse",
            "ValueError",
            "f\"Given endpoint: {endpoint} is not a relative path (e.g., '/messages/'), expecting a relative path (e.g., '/messages/').\"",
        ),
        (
            "mcp.server.stdio",
            "OSError",
            "f'duplicate of fd {fd} landed in the standard range'",
        ),
        (
            "mcp.server.stdio",
            "RuntimeError",
            "f'another stdio_server() in this process has already claimed fd {fd}'",
        ),
        ("mcp.server.streamable_http", "Exception", "err"),
        (
            "mcp.shared.auth",
            "InvalidRedirectUriError",
            "f\"Redirect URI '{redirect_uri}' not registered for client\"",
        ),
        (
            "mcp.shared.auth",
            "InvalidScopeError",
            "f'Client was not registered with scope {scope}'",
        ),
        ("mcp.shared.direct_dispatcher", "NoBackChannelError", "method"),
        (
            "mcp.shared.direct_dispatcher",
            "ValueError",
            "f'request id {request_id!r} is already in flight'",
        ),
        (
            "mcp.shared.exceptions",
            "ValueError",
            "f'Expected error code {URL_ELICITATION_REQUIRED}, got {error.code}'",
        ),
        (
            "mcp.shared.extension",
            "TypeError",
            "f'{owner}.identifier must be a `vendor-prefix/name` string (reverse-DNS prefix required), got {identifier!r}'",
        ),
        ("mcp.shared.jsonrpc_dispatcher", "NoBackChannelError", "method"),
        (
            "mcp.shared.jsonrpc_dispatcher",
            "ValueError",
            "f'request id {request_id!r} is already in flight'",
        ),
        (
            "mcp.shared.path_security",
            "PathEscapeError",
            "f'Path component contains a null byte; refusing to join onto {base_resolved}'",
        ),
        (
            "mcp.shared.path_security",
            "PathEscapeError",
            "f'Path component {part!r} is absolute; refusing to join onto {base_resolved}'",
        ),
        (
            "mcp.shared.path_security",
            "PathEscapeError",
            "f'Path {target} escapes base {base_resolved}'",
        ),
        (
            "mcp.shared.uri_template",
            "TypeError",
            "f'Variable {var.name!r} must be str or a sequence of str, got {type(value).__name__}'",
        ),
    }
)


def test_every_sdk_exception_construction_is_withheld() -> None:
    """The whole `mcp` package, exact both ways (rev 26)."""
    import ast
    from pathlib import Path

    import mcp

    from pmcp.sdk_rejections import _MESSAGE_ARGUMENTS, error_message_arguments

    root = Path(mcp.__file__).parent
    found: set[tuple[str, str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        module = (
            path.relative_to(root.parent).with_suffix("").as_posix().replace("/", ".")
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name, argument in error_message_arguments(tree):
            if name in _MESSAGE_ARGUMENTS or isinstance(argument, ast.Constant):
                continue
            found.add((module, name, ast.unparse(argument)))
    assert found == set(_SDK_EXCEPTION_CONSTRUCTIONS), (
        sorted(found - set(_SDK_EXCEPTION_CONSTRUCTIONS)),
        sorted(set(_SDK_EXCEPTION_CONSTRUCTIONS) - found),
    )


def test_the_received_error_sites_are_the_sdks_relays() -> None:
    """`RECEIVED_ERROR_SITES` is exactly the SDK's raises of an `MCPError`
    that relays an `ErrorData` built elsewhere -- a peer's response, or
    pmcp's own handler or callback result -- and not one that formats
    `str(e)` (rev 26)."""
    import ast
    from pathlib import Path

    import mcp

    from pmcp.sdk_rejections import RECEIVED_ERROR_SITES

    root = Path(mcp.__file__).parent
    relays: set[str] = set()
    formats: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
                continue
            call = node.exc
            func = call.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            message: ast.expr | None = None
            if name in ("from_error_data", "from_jsonrpc_error"):
                message = call.args[0] if call.args else None
            elif name in ("MCPError", "McpError"):
                message = next(
                    (k.value for k in call.keywords if k.arg == "message"),
                    call.args[1] if len(call.args) > 1 else None,
                )
            if message is None or isinstance(message, (ast.Constant, ast.JoinedStr)):
                continue
            owner = min(
                (
                    f
                    for f in functions
                    if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)
                ),
                key=lambda f: (f.end_lineno or f.lineno) - f.lineno,
            )
            site = f"{path.relative_to(root).as_posix()}::{owner.name}"
            if isinstance(message, ast.Call):  # `str(e)`: the SDK formats it
                formats.add(site)
            else:  # a received `ErrorData`, or its `.message`
                relays.add(site)
    assert relays == set(RECEIVED_ERROR_SITES), (relays, formats)
    assert not relays & formats, relays & formats


def test_no_sdk_template_is_too_general_to_recognise() -> None:
    """A relayed message is recognised as the SDK's by its template's
    literal text; a template with less than that would match anything."""
    from pmcp.sdk_rejections import (
        _MIN_TEMPLATE_LITERAL,
        _literal_length,
        _sdk_message_texts,
    )

    _literals, patterns = _sdk_message_texts()
    assert patterns
    short = [p.pattern for p in patterns if _literal_length(p) < _MIN_TEMPLATE_LITERAL]
    assert not short, short


def test_every_sdk_logger_is_masked() -> None:
    """Each logger the SDK creates -- by `__name__` (under `mcp`) or by a
    literal name (`"client"`) -- is one :func:`scrub_sdk_record` masks
    (rev 26: the client's loggers too)."""
    import ast
    from pathlib import Path

    import mcp

    from pmcp.sdk_rejections import is_sdk_logger, logger_name_arguments

    root = Path(mcp.__file__).parent
    unmasked = []
    for path in sorted(root.rglob("*.py")):
        module = (
            path.relative_to(root.parent).with_suffix("").as_posix().replace("/", ".")
        )
        for argument in logger_name_arguments(
            ast.parse(path.read_text(encoding="utf-8"))
        ):
            if isinstance(argument, ast.Name) and argument.id == "__name__":
                name = module
            elif isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                name = argument.value
            elif isinstance(argument, ast.Name) and argument.id == "name":
                continue  # `get_logger(name)`'s own body: its callers are checked
            else:
                unmasked.append((module, ast.unparse(argument)))
                continue
            if not is_sdk_logger(name):
                unmasked.append((module, name))
    assert not unmasked, unmasked
    assert is_sdk_logger("client") and is_sdk_logger("mcp.client.streamable_http")
    assert not is_sdk_logger("pmcp.client.manager")


def test_the_reviewed_templates_are_the_ones_the_sdk_builds() -> None:
    """Each reviewed message template exists in the SDK as written, so none
    is a dead entry; and each is one this table classifies as written inside
    an exchange."""
    from pmcp.sdk_rejections import REVIEWED_MESSAGE_TEMPLATES

    built = {
        source
        for (_module, _call, label, source), how in _SDK_ERROR_CONSTRUCTIONS.items()
        if label == "message" and how == _WRITE
    }
    assert set(REVIEWED_MESSAGE_TEMPLATES) <= built


@pytest.mark.parametrize(
    ("logger_name", "msg", "args"),
    [
        ("mcp.server.runner", "no handler for notification %s", ("SECRETzz",)),
        ("mcp.shared.jsonrpc_dispatcher", "handler for %r raised", ("SECRETzz",)),
        ("sse_starlette.sse", "chunk: %s", (b"data: SECRETzz",)),
        (
            "mcp.server.streamable_http_manager",
            "Rejected request with unknown or expired session ID: SECRETzz",
            (),
        ),
        (
            "mcp.server.streamable_http",
            "Session terminated with request SECRETzz in flight; no response to send",
            (),
        ),
    ],
)
def test_an_sdk_server_log_record_carries_no_request_text(
    logger_name: str, msg: str, args: tuple[object, ...]
) -> None:
    """An SDK server-side or sse_starlette record (rev 20, round-18 N2): text
    arguments are masked, and an f-string message the SDK pre-formatted has
    its placeholders masked -- whatever level it is logged at."""
    import logging

    import pmcp  # noqa: F401 - installs the record scrubber

    record = logging.getLogRecordFactory()(
        logger_name, logging.DEBUG, __file__, 1, msg, args, None
    )
    assert "SECRETzz" not in record.getMessage(), record.getMessage()
    other = logging.getLogRecordFactory()(
        "pmcp.server", logging.DEBUG, __file__, 1, msg, args, None
    )
    assert "SECRETzz" in other.getMessage()  # pmcp's own loggers are not masked


def test_a_pmcp_handler_error_reaches_the_caller_as_pmcp_wrote_it(
    tmp_path: object,
) -> None:
    """End to end over stdio, on a handshake-era connection (where the SDK
    sends a handler's error text; the modern era sends `Internal server
    error` for any non-MCP error, as it always has): an error pmcp's own
    handler raises keeps its text through the SDK-side rewrite (it carries
    `PMCP_HANDLER_MARK`); an SDK rejection on the same connection is
    rebuilt (rev 20)."""
    import json

    frames = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "c", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "resources/read",
            "params": {"uri": "x://no-such-resource"},
        },
        {"jsonrpc": "2.0", "id": 3, "method": "SECRETzzMETHOD", "params": {}},
    ]
    out, _ = _stdio_exchange(tmp_path, frames)
    replies = {reply.get("id"): reply for reply in map(json.loads, out.splitlines())}
    assert replies[2]["error"]["message"] == "Unknown resource: x://no-such-resource", (
        replies[2]
    )
    assert replies[3]["error"] == {"code": -32601, "message": "Method not found"}, (
        replies[3]
    )


def test_a_reviewed_template_is_bound_to_its_code_and_vocabulary() -> None:
    """Round-19 claude N2: a template keeps a message only under the code its
    site raises, and each placeholder only as one of its reviewed words (an
    SDK constant, a routing-header name, an envelope key, pmcp's own
    `x-mcp-header` token). Anything else falls back to the code's phrase."""
    from mcp_types import ErrorData

    from pmcp.sdk_rejections import value_free_error_data

    def kept(code: int, message: str) -> str:
        return value_free_error_data(ErrorData(code=code, message=message)).message

    assert (
        kept(-32020, "mcp-method header appears more than once")
        == "mcp-method header appears more than once"
    )
    assert kept(-32020, "SECRETzz header appears more than once") == "Header mismatch"
    assert kept(-32602, "mcp-method header appears more than once") == "Invalid params"
    key = "io.modelcontextprotocol/clientCapabilities"
    assert kept(
        -32602, f"params._meta is missing the required envelope key(s): {key}"
    ) == (f"params._meta is missing the required envelope key(s): {key}")
    assert (
        kept(-32602, "params._meta is missing the required envelope key(s): SECRETzz")
        == "Invalid params"
    )
    # pmcp declares no x-mcp-header token, so these templates match nothing.
    assert (
        kept(
            -32020,
            "Mcp-Param-SECRETzz header carries a malformed base64 sentinel value",
        )
        == "Header mismatch"
    )


# --- request headers are caller content (rev 29, round-27 codex F001) --------

_HEADER_S = "callerheadersentinelvaluezulu"

#: Every request header pmcp's transport or the SDK's reads, plus one it
#: does not, each carrying the sentinel in a shape the reader accepts or
#: rejects.
_SENTINEL_HEADERS: dict[str, str] = {
    "accept": f"text/{_HEADER_S}",
    "content-type": f"application/{_HEADER_S}",
    "mcp-session-id": _HEADER_S,
    "mcp-protocol-version": _HEADER_S,
    "last-event-id": _HEADER_S,
    "origin": f"http://{_HEADER_S}.example",
    "host": f"{_HEADER_S}.example",
    "authorization": f"Bearer {_HEADER_S}",
    "x-extra-header": _HEADER_S,
    # A header whose *name* is the caller's choice (rev 29).
    f"x-{_HEADER_S}": "1",
}

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "grid", "version": "1"},
    },
}


@pytest.mark.parametrize(
    "config",
    ["none", "shared-secret", "allowed-origins", "rate-limited"],
)
@pytest.mark.parametrize("header", sorted(_SENTINEL_HEADERS))
def test_no_request_header_reaches_any_output(
    header: str, config: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Each header the transport reads, and one it does not, with the
    sentinel as its value, on an `initialize` POST, a session POST, a GET
    and a DELETE, under each auth and origin configuration: no form of it
    in any log record at DEBUG (traceback included), response body or
    response header (rev 29)."""
    import logging
    from typing import Any

    import pmcp  # noqa: F401 - installs the scrubbers
    from mcp.server.lowlevel import Server
    from starlette.testclient import TestClient

    from pmcp.transport.http import create_http_app
    from tests.test_argument_error_echo import _forbidden, _record_text

    caplog.set_level(logging.DEBUG)
    kwargs: dict[str, Any] = {}
    base = {"accept": "application/json, text/event-stream"}
    if config == "shared-secret":
        kwargs = {"auth_token": "configured-secret", "auth_mode": "shared-secret"}
        base["authorization"] = "Bearer configured-secret"
    elif config == "allowed-origins":
        kwargs = {"allowed_origins": ["https://allowed.example"]}
    elif config == "rate-limited":
        kwargs = {"rate_limit_rpm": 1}
    app = create_http_app(Server("header-grid"), **kwargs)
    headers = {**base, header: _SENTINEL_HEADERS[header]}
    outputs: list[str] = []
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for method, body in (
            ("POST", _INITIALIZE),
            ("POST", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            ("GET", None),
            ("DELETE", None),
        ):
            request_headers = dict(headers)
            if method == "GET" and header != "accept":
                request_headers["accept"] = "text/event-stream"
            try:
                response = client.request(
                    method, "/mcp", headers=request_headers, json=body
                )
            except Exception as error:  # noqa: BLE001 -- inspected
                from pmcp.argument_errors import exception_text, safe_traceback_text

                outputs.append(exception_text(error) + safe_traceback_text(error))
                continue
            outputs.append(response.text)
            outputs.append(repr(sorted(response.headers.items())))
    outputs.append("\n".join(_record_text(record) for record in caplog.records))
    # No vacuous pass: the transport saw each request.
    assert any(
        r.name == "pmcp.transport.http" and "handle_mcp" in r.getMessage()
        for r in caplog.records
    )
    # Every 8-character window too: rev 28's entry log kept a session id's
    # first 8 characters, below `_forbidden`'s 12-character windows.
    forbidden = _forbidden(_HEADER_S) | {
        _HEADER_S[i : i + 8] for i in range(len(_HEADER_S) - 7)
    }
    leaked = [text[:400] for text in outputs if any(f in text for f in forbidden)]
    assert not leaked, leaked


def test_a_rejected_accept_header_is_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 27 codex F001, as filed: an `initialize` offering
    `Accept: text/<S>` is refused 406, and the transport's entry log does not
    carry the value."""
    import logging

    from mcp.server.lowlevel import Server
    from starlette.testclient import TestClient

    from pmcp.transport.http import create_http_app

    sentinel = "rejectedacceptvaluezulu"
    caplog.set_level(logging.DEBUG)
    app = create_http_app(Server("accept-header-review"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/mcp", headers={"accept": f"text/{sentinel}"}, json=_INITIALIZE
        )
    assert response.status_code == 406, response.text
    messages = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "pmcp.transport.http"
    )
    assert "accept=other" in messages, messages
    assert sentinel not in messages, messages


#: The types whose parameters are request objects (rev 30, round-28 claude
#: N1): a parameter annotated with one of these, and an ASGI callable's
#: `scope` and `receive` (by position), are taint sources -- by type and
#: position, never by variable name.
_REQUEST_TYPES = frozenset(
    {"Request", "HTTPConnection", "WebSocket", "Scope", "Receive", "Message"}
)

#: Callees whose result carries no caller value (a decision or a fixed
#: classification), each reviewed (rev 30). Passing request metadata to one
#: is not a sink, and its result is not tainted.
_REQUEST_CLASSIFIERS: dict[str, str] = {
    "_method_class": "the method from a fixed set, else 'other'",
    "_accept_class": "how Accept classifies, from a fixed set",
    "_header_names": "known header names, else 'other'",
    "_origin_rejected": "a bool: the Origin check",
    "_host_rejected": "a bool: the Host check",
    "hmac.compare_digest": "a bool: the shared-secret check",
    "bool": "a bool",
    "len": "a count",
    "isinstance": "a bool",
    "_jwks_has_kid": "a bool: whether the issuer's keys hold the token's key id",
    "value_free_rejection": "the SDK's rejection rebuilt from the body's structure (rev 19)",
}

#: Callees that receive request metadata and are not sinks, each reviewed:
#: their result stays tainted (rev 30).
_REQUEST_PROPAGATORS: dict[str, str] = {
    "_bearer_token": "returns the token, used only for validation",
    "_split_host_port": "splits a Host for the comparison above",
    "_origin_host_port": "parses an Origin for the comparison above",
    "_is_loopback_host": "a bool on a parsed host",
    "int": "a Content-Length for the cap comparison",
    "str": "text for a comparison",
    "_check_rate_limit": "keys a per-client counter; never logged",
    "_read_body_capped": "reads the body against the cap",
    "bounded_wait": "awaits the body read or the SDK under a timeout",
    "load_json": "parses the body for its method (a decision)",
    "resource_jwks.get_for_token": "fetches the issuer's keys for the token",
    "validate_resource_server_token": "validates the token; its errors are pmcp's",
    "session_manager.handle_request": "the SDK's own handling (revs 20, 26)",
    "dict": "a copy of a header mapping, for a decision",
    # Calls inside the propagators' own bodies, scanned since rev 31:
    "ip_address": "parses a host for the loopback decision; its error is caught",
    "ipaddress.ip_address": "parses a host for the loopback decision",
    "urlparse": "splits an Origin for the comparison",
    "b''.join": "joins the chunks read under the cap",
    "original_send": "the ASGI send of the SDK's response, passed through",
    "asyncio.ensure_future": "schedules the awaited read under the timeout",
    "asyncio.wait": "awaits it under the timeout",
    "_retrieve": "retrieves a finished task's outcome, discarding it",
    "jwt.get_unverified_header": "PyJWT's header parse, for the key id",
    "_select_jwk_key": "picks the issuer's key for the token",
    "_decode_with_key": "PyJWT's signature check; its errors are pmcp's",
    "_claim_scopes": "the validated token's scopes, for the scope decision",
    "ResourceServerTokenClaims": "the validated claims, kept for decisions",
    "list": "a copy, for the decision it feeds",
    "bytes": "the body bytes, for the parse",
    "json.loads": "the body parse, classified by `load_json`",
    "PyJWKSet.from_dict": "the issuer's published key set",
    "sorted": "an ordered copy, for the decision it feeds",
    "jwt.decode": "PyJWT's verification; the caller maps its errors",
}


def _callee(node: ast.Call) -> str:
    return ast.unparse(node.func)


def _taint_sources(function: ast.AST) -> set[str]:
    """The parameters of ``function`` that are request objects: annotated
    with a request type, or an ASGI callable's `scope` and `receive` (its
    first two positional parameters after `self`, when it takes three)."""
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    params = [
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ]
    sources = {
        p.arg
        for p in params
        if p.annotation is not None
        and ast.unparse(p.annotation).split(".")[-1].strip("'\"") in _REQUEST_TYPES
    }
    positional = [
        p for p in [*function.args.posonlyargs, *function.args.args] if p.arg != "self"
    ]
    if (
        isinstance(function, ast.AsyncFunctionDef)
        and len(positional) == 3
        and (
            function.name == "__call__" or all(p.annotation is None for p in positional)
        )
    ):
        sources |= {positional[0].arg, positional[1].arg}
    return sources


def _tainted(node: ast.AST | None, names: set[str]) -> bool:
    """Whether ``node`` carries a request value, given the tainted names."""
    if node is None or isinstance(node, (ast.Constant, ast.Lambda)):
        return False
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Compare):
        return False  # a decision
    if isinstance(node, ast.Attribute) and ast.unparse(node) in names:
        return True  # a request held as an attribute (`self._request`)
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
        and node.slice.value.startswith("pmcp.")
        and isinstance(node.ctx, ast.Load)
    ):
        return True  # a value pmcp stored into the ASGI scope from the request
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.startswith("pmcp.")
    ):
        return True
    if isinstance(node, ast.IfExp):
        return _tainted(node.body, names) or _tainted(node.orelse, names)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        # A comprehension's targets are its own scope: they shadow an
        # enclosing name, and are tainted only by their iterable.
        local = set(names)
        for generator in node.generators:
            bound: set[str] = set()
            _bind(generator.target, bound)
            iter_tainted = _tainted(generator.iter, local)
            local -= bound
            if iter_tainted:
                local |= bound
        parts = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        return any(_tainted(part, local) for part in parts)
    if isinstance(node, ast.Call):
        callee = _callee(node)
        if (
            callee in _REQUEST_CLASSIFIERS
            or callee.split(".")[-1] in _REQUEST_CLASSIFIERS
        ):
            return False
        if isinstance(node.func, ast.Attribute) and _tainted(node.func.value, names):
            return True
        return any(
            _tainted(value, names)
            for value in [*node.args, *(k.value for k in node.keywords)]
        )
    return any(_tainted(child, names) for child in ast.iter_child_nodes(node))


def _bind(target: ast.AST, names: set[str]) -> None:
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            _bind(element, names)
    elif isinstance(target, ast.Starred):
        _bind(target.value, names)


def _own_nodes(function: ast.AST) -> list[ast.AST]:
    """The nodes of ``function``'s body, without nested functions'."""
    out: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        out.append(node)
        pending.extend(ast.iter_child_nodes(node))
    return out


#: Container methods that put a value into the object they are called on: a
#: request value passed to one taints the container instead (rev 31).
_CONTAINER_STORES = frozenset(
    {"append", "extend", "add", "update", "insert", "setdefault", "appendleft"}
)


def _route_endpoints(tree: ast.AST) -> set[str]:
    """Every function registered as a route endpoint in ``tree``: Starlette's
    `Route`/`WebSocketRoute` (`endpoint=` or the second positional), `Mount`
    (`app=`), and `add_route`/`add_api_route`/`add_websocket_route` (rev 31,
    round-29 ruling: the endpoint's first parameter is the request, typed or
    not)."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node).split(".")[-1]
        values: list[ast.expr] = []
        if name in ("Route", "WebSocketRoute"):
            values = [k.value for k in node.keywords if k.arg == "endpoint"]
            values += node.args[1:2]
        elif name == "Mount":
            values = [k.value for k in node.keywords if k.arg == "app"]
        elif name in ("add_route", "add_api_route", "add_websocket_route"):
            values = [
                *node.args[1:2],
                *(k.value for k in node.keywords if k.arg in ("route", "endpoint")),
            ]
        for value in values:
            if isinstance(value, ast.Name):
                found.add(value.id)
            elif isinstance(value, ast.Attribute):
                found.add(value.attr)
    return found


def _request_metadata_sinks(tree: ast.AST) -> list[int]:
    """Every place a request value reaches a call, a store or a raise that is
    not a reviewed classifier or propagator: a log, `print`, a response, a
    header assignment, a metric label, an exception, a helper (rev 30:
    fail-closed taint walk, flow-insensitive, through assignments,
    attributes, `.get`, subscripts, f-strings, `+=`, walrus, `for` targets,
    and nested functions)."""
    found: list[int] = []
    endpoints = _route_endpoints(tree)
    propagators = {name.split(".")[-1] for name in _REQUEST_PROPAGATORS}
    #: `self.<attr>` (or `cls.<attr>`) assigned a request value anywhere in
    #: the module: tracked in every function (rev 31).
    held: set[str] = set()
    report = False

    def sources(function: ast.AST) -> set[str]:
        assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        out = _taint_sources(function)
        positional = [
            p.arg
            for p in [*function.args.posonlyargs, *function.args.args]
            if p.arg not in ("self", "cls")
        ]
        if function.name in endpoints and positional:
            out.add(positional[0])  # a route endpoint's request
        if function.name in propagators:
            # A reviewed propagator's own body is scanned with every
            # positional parameter a request value (rev 31, round-29 ruling;
            # its keyword-only parameters are pmcp's labels and limits).
            out |= set(positional)
        return out

    def visit(function: ast.AST, inherited: set[str]) -> None:
        names = set(inherited) | sources(function) | held
        nodes = _own_nodes(function)
        changed = True
        while changed:
            before = len(names)
            for node in nodes:
                if isinstance(node, ast.Assign) and _tainted(node.value, names):
                    for target in node.targets:
                        _bind(target, names)
                elif isinstance(
                    node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)
                ) and (_tainted(node.value, names)):
                    _bind(node.target, names)
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)) and (
                    _tainted(node.iter, names)
                ):
                    _bind(node.target, names)
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    for item in node.items:
                        if item.optional_vars is not None and _tainted(
                            item.context_expr, names
                        ):
                            _bind(item.optional_vars, names)
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _CONTAINER_STORES
                    and isinstance(node.func.value, ast.Name)
                    and any(
                        _tainted(v, names)
                        for v in [*node.args, *(k.value for k in node.keywords)]
                    )
                ):
                    names.add(node.func.value.id)
                if (
                    isinstance(node, (ast.Assign, ast.AnnAssign))
                    and node.value is not None
                ):
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    for target in targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id in ("self", "cls")
                            and _tainted(node.value, names)
                        ):
                            key = ast.unparse(target)
                            names.add(key)
                            held.add(key)
            changed = len(names) != before
        if not report:
            for node in ast.walk(function):
                if (
                    node is not function
                    and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node in _own_children(function)
                ):
                    visit(node, names)
            return
        for node in nodes:
            if isinstance(node, ast.Call):
                callee = _callee(node)
                short = callee.split(".")[-1]
                values = [*node.args, *(k.value for k in node.keywords)]
                if not any(_tainted(v, names) for v in values):
                    continue
                if isinstance(node.func, ast.Attribute) and _tainted(
                    node.func.value, names
                ):
                    continue  # a method of the request value itself
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in _CONTAINER_STORES
                    and isinstance(node.func.value, ast.Name)
                ):
                    continue  # into a local container, which is now tracked
                if (
                    callee in _REQUEST_CLASSIFIERS
                    or short in _REQUEST_CLASSIFIERS
                    or callee in _REQUEST_PROPAGATORS
                ):
                    continue
                found.append(node.lineno)
            elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                value = node.value
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    held_attribute = (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id in ("self", "cls")
                    )
                    if (
                        isinstance(target, (ast.Subscript, ast.Attribute))
                        and not held_attribute
                        and _tainted(value, names)
                        and not _tainted(target.value, names)
                    ):
                        found.append(node.lineno)  # stored into another object
            elif isinstance(node, ast.Raise) and _tainted(node.exc, names):
                found.append(node.lineno)
        for node in ast.walk(function):
            if node is not function and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                if node in _own_children(function):
                    visit(node, names)

    def _own_children(function: ast.AST) -> list[ast.AST]:
        out: list[ast.AST] = []
        pending = list(ast.iter_child_nodes(function))
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(node)
                continue
            if isinstance(node, ast.ClassDef):
                pending.extend(ast.iter_child_nodes(node))
                continue
            pending.extend(ast.iter_child_nodes(node))
        return out

    # Collect the held attributes to a fixed point, then report.
    while True:
        before = len(held)
        for top in _own_children(tree):
            visit(top, set())
        if len(held) == before:
            break
    report = True
    for top in _own_children(tree):
        visit(top, set())
    return found


def test_no_request_metadata_reaches_a_log_or_a_response() -> None:
    """No request value -- a header, the query, path, method, client
    address, a cookie, the URL, the ASGI scope or a received message --
    reaches a call, a store into another object, or a raise in `src/pmcp`,
    outside the reviewed classifiers and propagators (rev 29; fail-closed
    taint walk since rev 30)."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    found = []
    for path in sorted(src.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        for line in _request_metadata_sinks(ast.parse(path.read_text())):
            found.append(f"{path.relative_to(src)}:{line}")
    assert not found, found


_R = "from starlette.requests import Request\n"

#: Round-28 claude N1's 22 shapes, and a comprehension (rev 30): each must
#: be flagged.
_REQUEST_SINK_SHAPES: dict[str, str] = {
    "f-string": _R
    + "def f(request: Request):\n    logger.debug(f\"{request.headers['a']}\")\n",
    "two hops": _R
    + "def f(request: Request):\n    a = request.headers\n    b = a.get('x')\n    logger.info(b)\n",
    "any name": _R + "def f(r: Request):\n    logger.info(r.headers['a'])\n",
    "attribute": _R
    + "class C:\n    def f(self, http_request: Request):\n        logger.info(http_request.headers.get('a'))\n",
    "self attr": _R
    + "class C:\n    def f(self, req: Request):\n        self._request = req\n        logger.info(self._request.headers)\n",
    "scope.get": "async def app(scope, receive, send):\n    logger.info(scope.get('headers'))\n",
    "Headers(scope)": "async def app(scope, receive, send):\n    h = Headers(scope=scope)\n",
    "state across": _R
    + "def g(request: Request):\n    logger.info(request.state.origin)\n",
    "items loop": _R
    + "def f(request: Request):\n    for k, v in request.headers.items():\n        logger.info('%s=%s', k, v)\n",
    "helper returns": _R + "def f(request: Request):\n    v = pick(request)\n",
    "helper logs": _R + "def f(request: Request):\n    log_it(request.headers['a'])\n",
    "walrus": _R
    + "def f(request: Request):\n    if (v := request.headers.get('a')):\n        logger.info(v)\n",
    "aug-assign": _R
    + "def f(request: Request):\n    s = ''\n    s += request.headers['a']\n    logger.info(s)\n",
    "logger alias": _R + "def f(request: Request):\n    LOG.info(request.url)\n",
    "bound method": _R
    + "def f(request: Request):\n    log_it = logger.debug\n    log_it(request.url)\n",
    "raise": _R
    + "def f(request: Request):\n    raise ValueError(f\"bad {request.headers['origin']}\")\n",
    "HTTPException": _R
    + "def f(request: Request):\n    raise HTTPException(400, detail=request.headers['a'])\n",
    "CORS echo": _R
    + "def f(request: Request, response):\n    response.headers['access-control-allow-origin'] = request.headers['origin']\n",
    "redirect": _R
    + "def f(request: Request):\n    return RedirectResponse(str(request.url))\n",
    "print": _R + "def f(request: Request):\n    print(request.headers)\n",
    "stderr": _R
    + "def f(request: Request):\n    sys.stderr.write(request.headers['a'])\n",
    "metric": _R
    + "def f(request: Request):\n    _inc(f\"requests_{request.headers['a']}\")\n",
    "comprehension": _R
    + "def f(request: Request):\n    logger.info([v for _, v in request.headers.items()])\n",
    # Round-29 ruling (rev 31): a route endpoint's untyped request, a value
    # read back from `scope["pmcp.*"]`, a request held as an attribute and
    # used in another method, and a reviewed propagator's own body.
    "route endpoint": "async def handle(req):\n    logger.info(req.headers)\n"
    + "routes = [Route('/x', endpoint=handle)]\n",
    "scope read": "def f(scope):\n    logger.info(scope['pmcp.trace_context'])\n",
    "scope get": "def f(ctx):\n    logger.info(ctx.scope.get('pmcp.auth'))\n",
    "held attribute": _R
    + "class C:\n    def keep(self, request: Request):\n        self._req = request\n"
    + "    def use(self):\n        logger.info(self._req.url)\n",
    "propagator body": "def _split_host_port(host, default):\n    logger.info(host)\n",
}

#: Shapes that must not be flagged: decisions and reviewed classifiers.
_REQUEST_SAFE_SHAPES: dict[str, str] = {
    "classifier": _R
    + "def f(request: Request):\n    logger.debug('%s', _accept_class(request.headers.get('accept', '')))\n",
    "presence": _R
    + "def f(request: Request):\n    logger.debug('%s', 'present' if request.headers.get('x') else 'absent')\n",
    "comparison": _R
    + "def f(request: Request):\n    if request.headers.get('a') == 'b':\n        logger.debug('fixed')\n",
    "store into request": _R
    + "def f(request: Request):\n    request.scope['k'] = request.headers.get('x')\n",
    "unrelated": "def f(request):\n    logger.debug('%s', request)\n",
    "comprehension shadow": _R
    + "def f(request: Request, other):\n    if (value := request.headers.get('a')):\n"
    + "        pass\n    logger.debug('%s', [value for value in other])\n",
}


@pytest.mark.parametrize("shape", sorted(_REQUEST_SINK_SHAPES))
def test_the_request_metadata_check_flags_each_shape(shape: str) -> None:
    assert _request_metadata_sinks(ast.parse(_REQUEST_SINK_SHAPES[shape])), shape


@pytest.mark.parametrize("shape", sorted(_REQUEST_SAFE_SHAPES))
def test_the_request_metadata_check_passes_decisions(shape: str) -> None:
    assert not _request_metadata_sinks(ast.parse(_REQUEST_SAFE_SHAPES[shape])), shape


def test_every_reviewed_request_callee_is_used() -> None:
    """No reviewed classifier or propagator is a dead entry: each is called
    somewhere in `src/pmcp` (since rev 31 the propagators' own bodies, in
    any module, are scanned too)."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    called: set[str] = set()
    for path in sorted(src.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                called.add(_callee(node))
    unused = sorted(
        name
        for name in [*_REQUEST_CLASSIFIERS, *_REQUEST_PROPAGATORS]
        if name not in called
    )
    assert not unused, unused
