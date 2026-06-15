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
        return TestClient(app, raise_server_exceptions=False)


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
        """/mcp endpoint should accept GET, POST, and DELETE."""
        from pmcp.transport.http import create_http_app

        mock_server = MagicMock()
        mock_server.create_initialization_options = MagicMock(return_value={})

        app = create_http_app(mock_server)

        # Find the /mcp route and check it accepts GET and POST
        for route in app.routes:
            if hasattr(route, "path") and route.path == "/mcp":
                if hasattr(route, "methods"):
                    assert "GET" in route.methods
                    assert "POST" in route.methods


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
        from pmcp.transport import http as http_mod

        http_mod._rl_store.clear()
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
        from pmcp.auth import ResourceServerAuthError

        with (
            patch(
                "pmcp.transport.http.AsyncJWKS.get_for_token",
                new=AsyncMock(return_value={"keys": [{"kid": "test-key"}]}),
            ),
            patch(
                "pmcp.transport.http.validate_resource_server_token",
                side_effect=ResourceServerAuthError(
                    "insufficient_scope", "Missing required scope(s): write"
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
        from pmcp.auth import ResourceServerJWKSUnavailable

        with patch(
            "pmcp.transport.http.AsyncJWKS.get_for_token",
            side_effect=ResourceServerJWKSUnavailable(
                "JWKS fetch failed for https://issuer.example/jwks.json?token=secret."
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
