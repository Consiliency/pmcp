"""Wiring tests for P2: connect auth-mode / OAuth / Origin params end-to-end.

These prove the previously-dead resource-server and Origin paths are now reachable
through ``GatewayServer`` and the CLI, not just via a direct ``create_http_app`` call.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

ISSUER = "https://issuer.example"
AUDIENCE = "https://pmcp.example/mcp"
JWKS_URL = "https://issuer.example/.well-known/jwks.json"


def _signed_token(
    *,
    issuer: str = ISSUER,
    audience: str | list[str] = AUDIENCE,
    scope: str = "read write",
) -> tuple[str, dict[str, object]]:
    """Return a signed RS256 JWT plus the matching JWKS document."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk["kid"] = "test-key"
    jwks = {"keys": [jwk]}
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": issuer,
            "sub": "subject-1",
            "aud": audience,
            "scope": scope,
            "exp": now + 300,
            "nbf": now - 10,
        },
        key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    return token, jwks


def _resource_server_client(**overrides: Any) -> TestClient:
    """Build a create_http_app TestClient in resource-server mode with a mocked
    session manager. The JWKS fetch is patched per-request (see _jwks_patch); the
    token validator is the REAL one so acceptance/rejection is proven end-to-end."""
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

        kwargs: dict[str, Any] = {
            "auth_mode": "resource-server",
            "resource_server_issuer": ISSUER,
            "resource_server_jwks_url": JWKS_URL,
            "resource_server_audience": AUDIENCE,
        }
        kwargs.update(overrides)
        app = create_http_app(mock_server, **kwargs)
        return TestClient(
            app, base_url="http://127.0.0.1", raise_server_exceptions=False
        )


def _jwks_patch(jwks: dict[str, object]) -> Any:
    """Patch the JWKS fetch to return a fixed document (no network)."""
    return patch(
        "pmcp.transport.http.AsyncJWKS.get_for_token",
        new=AsyncMock(return_value=jwks),
    )


class TestResourceServerModeEnforcedEndToEnd:
    """Prove the resource-server validation path is reachable through the app."""

    def test_valid_jwt_is_accepted(self) -> None:
        token, jwks = _signed_token()
        client = _resource_server_client()

        with _jwks_patch(jwks):
            response = client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {token}"},
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )

        assert response.status_code == 202

    def test_wrong_audience_jwt_is_401(self) -> None:
        token, jwks = _signed_token(audience="https://other.example/mcp")
        client = _resource_server_client()

        with _jwks_patch(jwks):
            response = client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {token}"},
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )

        assert response.status_code == 401
        assert "www-authenticate" in response.headers

    def test_unsigned_token_is_401(self) -> None:
        _token, jwks = _signed_token()
        unsigned = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE}, key="", algorithm="none"
        )
        client = _resource_server_client()

        with _jwks_patch(jwks):
            response = client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {unsigned}"},
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )

        assert response.status_code == 401


class TestGatewayServerWiring:
    """GatewayServer must pass the auth/Origin params through to create_http_app."""

    def test_init_stores_auth_and_origin_params(self) -> None:
        from pmcp.server import GatewayServer

        server = GatewayServer(
            auth_mode="resource-server",
            resource_server_issuer=ISSUER,
            resource_server_jwks_url=JWKS_URL,
            resource_server_audience=AUDIENCE,
            required_scopes=["pmcp.invoke"],
            allowed_origins=["https://app.example"],
        )

        assert server._auth_mode == "resource-server"
        assert server._resource_server_issuer == ISSUER
        assert server._resource_server_jwks_url == JWKS_URL
        assert server._resource_server_audience == AUDIENCE
        assert server._required_scopes == ["pmcp.invoke"]
        assert server._allowed_origins == ["https://app.example"]

    @pytest.mark.asyncio
    async def test_run_http_forwards_params_to_create_http_app(self) -> None:
        import uvicorn

        from pmcp.server import GatewayServer

        server = GatewayServer(
            auth_mode="resource-server",
            resource_server_issuer=ISSUER,
            resource_server_jwks_url=JWKS_URL,
            resource_server_audience=AUDIENCE,
            required_scopes=["pmcp.invoke"],
            allowed_origins=["https://app.example"],
        )
        server._server = MagicMock()

        with (
            patch("pmcp.server.acquire_singleton_lock", return_value=True),
            patch(
                "pmcp.transport.http.create_http_app", return_value=MagicMock()
            ) as create_app,
            patch.object(server, "initialize", new=AsyncMock()),
            patch.object(server, "shutdown", new=AsyncMock()),
            patch.object(uvicorn, "Config", MagicMock()),
            patch.object(uvicorn, "Server") as uvicorn_server,
        ):
            uvicorn_server.return_value.serve = AsyncMock()
            await server._run_http()

        kwargs = create_app.call_args.kwargs
        assert kwargs["auth_mode"] == "resource-server"
        assert kwargs["resource_server_issuer"] == ISSUER
        assert kwargs["resource_server_jwks_url"] == JWKS_URL
        assert kwargs["resource_server_audience"] == AUDIENCE
        assert kwargs["required_scopes"] == ["pmcp.invoke"]
        assert kwargs["allowed_origins"] == ["https://app.example"]


class TestCliWiresAuthParams:
    """CLI flags and env vars must reach the GatewayServer constructor."""

    @pytest.mark.asyncio
    async def test_cli_flags_populate_gateway_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pmcp.cli import parse_args, run_server

        monkeypatch.setattr(
            "sys.argv",
            [
                "pmcp",
                "--transport",
                "stdio",
                "--auth-mode",
                "resource-server",
                "--oauth-issuer",
                ISSUER,
                "--oauth-jwks-url",
                JWKS_URL,
                "--oauth-audience",
                AUDIENCE,
                "--required-scope",
                "pmcp.invoke",
                "--required-scope",
                "pmcp.read",
                "--allowed-origin",
                "https://app.example",
                "--allowed-origin",
                "https://admin.example",
            ],
        )
        args = parse_args()

        assert args.auth_mode == "resource-server"
        assert args.required_scopes == ["pmcp.invoke", "pmcp.read"]
        assert args.allowed_origins == ["https://app.example", "https://admin.example"]

        with patch("pmcp.server.GatewayServer") as gs:
            gs.return_value.run = AsyncMock()
            await run_server(args)

        kwargs = gs.call_args.kwargs
        assert kwargs["auth_mode"] == "resource-server"
        assert kwargs["resource_server_issuer"] == ISSUER
        assert kwargs["resource_server_jwks_url"] == JWKS_URL
        assert kwargs["resource_server_audience"] == AUDIENCE
        assert kwargs["required_scopes"] == ["pmcp.invoke", "pmcp.read"]
        assert kwargs["allowed_origins"] == [
            "https://app.example",
            "https://admin.example",
        ]

    @pytest.mark.asyncio
    async def test_env_vars_populate_gateway_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pmcp.cli import parse_args, run_server

        monkeypatch.setattr("sys.argv", ["pmcp", "--transport", "stdio"])
        monkeypatch.setenv("PMCP_AUTH_MODE", "resource-server")
        monkeypatch.setenv("PMCP_OAUTH_ISSUER", ISSUER)
        monkeypatch.setenv("PMCP_OAUTH_JWKS_URL", JWKS_URL)
        monkeypatch.setenv("PMCP_OAUTH_AUDIENCE", AUDIENCE)
        monkeypatch.setenv("PMCP_REQUIRED_SCOPES", "pmcp.invoke, pmcp.read")
        monkeypatch.setenv(
            "PMCP_ALLOWED_ORIGINS", "https://app.example, https://admin.example"
        )
        args = parse_args()

        with patch("pmcp.server.GatewayServer") as gs:
            gs.return_value.run = AsyncMock()
            await run_server(args)

        kwargs = gs.call_args.kwargs
        assert kwargs["auth_mode"] == "resource-server"
        assert kwargs["resource_server_issuer"] == ISSUER
        assert kwargs["resource_server_jwks_url"] == JWKS_URL
        assert kwargs["resource_server_audience"] == AUDIENCE
        assert kwargs["required_scopes"] == ["pmcp.invoke", "pmcp.read"]
        assert kwargs["allowed_origins"] == [
            "https://app.example",
            "https://admin.example",
        ]
