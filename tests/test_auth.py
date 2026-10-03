"""Tests for auth discovery, URL elicitation, and redaction helpers."""

from __future__ import annotations

import base64
import json
import stat
import time
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from pmcp.auth import (
    AsyncJWKS,
    AuthMessage,
    ResourceServerAuthError,
    ResourceServerJWKSUnavailable,
    fetch_json_metadata,
    is_verified_public_auth_url,
    normalize_auth_metadata,
    parse_url_elicitation_error,
    parse_www_authenticate,
    protected_resource_metadata_urls,
    redact_auth_url,
    sanitize_auth_diagnostic,
    sanitize_public_auth_url,
    sanitize_url_elicitation_url,
    validate_resource_server_token,
)
from pmcp.env_store import read_env_file, validate_env_var_name, write_env_file
from pmcp.remote_auth import (
    MissingRemoteHeaderAuthError,
    build_remote_header_env_lookup,
    resolve_remote_headers,
    resolve_remote_headers_for_tenant,
)
from pmcp.types import (
    DEFAULT_AUTH_STATE_SEMANTICS,
    AuthChallengeInfo,
    AuthConnectInput,
    AuthMetadataInfo,
    GatewayAuditEvent,
    GatewayDiagnosticsInfo,
    InvokeOutput,
    ProvisionOutput,
    ServerHealthInfo,
    UrlElicitationInfo,
)
from pmcp.types import AuthState


def test_legacy_auth_models_validate_without_new_fields() -> None:
    provision = ProvisionOutput(ok=False, server="x", message="needs key")
    health = ServerHealthInfo(name="x", status="offline", tool_count=0)
    invoke = InvokeOutput(
        tool_id="x::tool",
        ok=False,
        truncated=False,
        raw_size_estimate=0,
    )
    auth_input = AuthConnectInput(server_name="x", credential="secret")

    assert provision.auth_state == "none"
    assert provision.missing_env_vars == []
    assert health.auth_state == "none"
    assert health.missing_env_vars == []
    assert invoke.auth_state == "none"
    assert invoke.missing_env_vars == []
    assert auth_input.auth_mode == "api_key"


def test_auth_state_semantics_cover_every_state_with_operator_action() -> None:
    assert set(DEFAULT_AUTH_STATE_SEMANTICS) == set(get_args(AuthState))
    for info in DEFAULT_AUTH_STATE_SEMANTICS.values():
        assert info.meaning
        assert info.primary_next_action
        assert isinstance(info.evidence_fields, list)


def test_gateway_diagnostics_includes_auth_semantics_by_default() -> None:
    diagnostics = GatewayDiagnosticsInfo()

    assert set(diagnostics.auth_state_semantics) == set(get_args(AuthState))
    assert (
        diagnostics.auth_state_semantics["missing_auth"].primary_next_action
        == DEFAULT_AUTH_STATE_SEMANTICS["missing_auth"].primary_next_action
    )


def test_gateway_audit_event_auth_event_is_additive() -> None:
    minimal = GatewayAuditEvent(
        timestamp=1.0,
        method="gateway.health",
        action="health",
        outcome="success",
        latency_ms=0,
    )
    categorized = GatewayAuditEvent(
        timestamp=1.0,
        method="gateway.invoke",
        action="invoke",
        outcome="failure",
        latency_ms=0,
        auth_state="missing_auth",
        auth_event="missing_credential",
    )

    assert minimal.auth_event is None
    assert categorized.auth_event == "missing_credential"


def test_remote_header_resolves_embedded_placeholders_without_metadata_leaks() -> None:
    resolution = resolve_remote_headers(
        {
            "Authorization": "Bearer ${REMOTE_API_TOKEN}",
            "X-Static": "literal-value",
        },
        lambda name: {"REMOTE_API_TOKEN": "secret-token"}.get(name),
    )

    assert resolution.resolved_headers == {
        "Authorization": "Bearer secret-token",
        "X-Static": "literal-value",
    }
    assert resolution.missing_env_vars == []
    assert resolution.referenced_env_vars_by_header == {
        "Authorization": ["REMOTE_API_TOKEN"]
    }


def test_remote_header_missing_vars_are_sorted_deduped_and_non_secret() -> None:
    resolution = resolve_remote_headers(
        {
            "Authorization": "Bearer ${REMOTE_API_TOKEN}",
            "X-Api-Key": "${REMOTE_API_TOKEN}:${REMOTE_OTHER_TOKEN}",
            "X-Static": "literal-value",
        },
        lambda _name: "",
    )
    error = MissingRemoteHeaderAuthError("remote", resolution.missing_env_vars)

    assert resolution.missing_env_vars == ["REMOTE_API_TOKEN", "REMOTE_OTHER_TOKEN"]
    assert resolution.resolved_headers["X-Static"] == "literal-value"
    assert "REMOTE_API_TOKEN" in str(error)
    assert "secret-token" not in str(error)


def test_tenant_code_mode_remote_headers_resolve_without_diagnostic_leaks() -> None:
    resolution = resolve_remote_headers(
        {
            "Authorization": "Bearer ${TENANT_CODE_MODE_MCP_TOKEN}",
            "X-Tenant-ID": "${TENANT_CODE_MODE_TENANT_ID}",
        },
        lambda name: {
            "TENANT_CODE_MODE_MCP_TOKEN": "tenant-secret-token",
            "TENANT_CODE_MODE_TENANT_ID": "tenant-id-123",
        }.get(name),
    )

    assert resolution.resolved_headers == {
        "Authorization": "Bearer tenant-secret-token",
        "X-Tenant-ID": "tenant-id-123",
    }
    assert resolution.referenced_env_vars_by_header == {
        "Authorization": ["TENANT_CODE_MODE_MCP_TOKEN"],
        "X-Tenant-ID": ["TENANT_CODE_MODE_TENANT_ID"],
    }
    assert resolution.missing_env_vars == []


def test_tenant_code_mode_missing_remote_headers_are_non_secret() -> None:
    resolution = resolve_remote_headers(
        {
            "Authorization": "Bearer ${TENANT_CODE_MODE_MCP_TOKEN}",
            "X-Tenant-ID": "${TENANT_CODE_MODE_TENANT_ID}",
        },
        lambda _name: None,
    )
    error = MissingRemoteHeaderAuthError(
        "tenant-code-mode",
        resolution.missing_env_vars + ["TENANT_CODE_MODE_MCP_TOKEN"],
    )

    assert resolution.missing_env_vars == [
        "TENANT_CODE_MODE_MCP_TOKEN",
        "TENANT_CODE_MODE_TENANT_ID",
    ]
    assert "tenant-code-mode" in str(error)
    assert "TENANT_CODE_MODE_MCP_TOKEN" in str(error)
    assert "TENANT_CODE_MODE_TENANT_ID" in str(error)
    assert "tenant-secret-token" not in str(error)


def test_remote_header_env_lookup_reads_process_project_and_user_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PROCESS_TOKEN", "process-secret")
    write_env_file(
        home / ".config" / "pmcp" / "pmcp.env", {"USER_TOKEN": "user-secret"}
    )
    write_env_file(project / ".env.pmcp", {"PROJECT_TOKEN": "project-secret"})

    lookup = build_remote_header_env_lookup(project)

    assert lookup("PROCESS_TOKEN") == "process-secret"
    assert lookup("PROJECT_TOKEN") == "project-secret"
    assert lookup("USER_TOKEN") == "user-secret"
    assert lookup("MISSING_TOKEN") is None


def test_tenant_code_mode_env_lookup_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TENANT_CODE_MODE_MCP_TOKEN", "process-secret")
    write_env_file(
        home / ".config" / "pmcp" / "pmcp.env",
        {
            "TENANT_CODE_MODE_MCP_TOKEN": "user-secret",
            "TENANT_CODE_MODE_TENANT_ID": "user-tenant",
        },
    )
    write_env_file(
        project / ".env.pmcp",
        {
            "TENANT_CODE_MODE_TENANT_ID": "project-tenant",
        },
    )

    lookup = build_remote_header_env_lookup(project)

    assert lookup("TENANT_CODE_MODE_MCP_TOKEN") == "process-secret"
    assert lookup("TENANT_CODE_MODE_TENANT_ID") == "project-tenant"


def test_remote_headers_for_tenant_reads_only_tenant_scope(tmp_path: Path) -> None:
    tenant_a = tmp_path / ".pmcp" / "tenants" / "tenant-a" / "pmcp.env"
    tenant_b = tmp_path / ".pmcp" / "tenants" / "tenant-b" / "pmcp.env"
    write_env_file(tenant_a, {"REMOTE_TOKEN": "tenant-a-secret"})
    write_env_file(tenant_b, {"REMOTE_TOKEN": "tenant-b-secret"})

    resolution = resolve_remote_headers_for_tenant(
        {"Authorization": "Bearer ${REMOTE_TOKEN}"},
        server_name="remote",
        tenant_id="tenant-a",
        project_root=tmp_path,
        include_process_env=False,
    )

    assert resolution.resolved_headers == {"Authorization": "Bearer tenant-a-secret"}
    assert resolution.missing_env_vars == []


def test_remote_headers_for_tenant_missing_does_not_fallback_to_other_tenant(
    tmp_path: Path,
) -> None:
    write_env_file(
        tmp_path / ".pmcp" / "tenants" / "tenant-b" / "pmcp.env",
        {"REMOTE_TOKEN": "tenant-b-secret"},
    )

    resolution = resolve_remote_headers_for_tenant(
        {"Authorization": "Bearer ${REMOTE_TOKEN}"},
        server_name="remote",
        tenant_id="tenant-a",
        project_root=tmp_path,
        include_process_env=False,
    )

    assert resolution.missing_env_vars == ["REMOTE_TOKEN"]
    assert "tenant-b-secret" not in str(
        MissingRemoteHeaderAuthError("remote", resolution.missing_env_vars)
    )


def _signed_token_fixture(
    *,
    issuer: str = "https://issuer.example",
    audience: str | list[str] = "https://pmcp.example/mcp",
    expires_delta: int = 300,
    not_before_delta: int = -10,
) -> tuple[str, dict[str, object]]:
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
            "scope": "read write",
            "exp": now + expires_delta,
            "nbf": now + not_before_delta,
        },
        key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    return token, jwks


def test_validate_resource_server_token_accepts_signed_jwks_token() -> None:
    token, jwks = _signed_token_fixture()

    claims = validate_resource_server_token(
        token,
        issuer="https://issuer.example",
        audience="https://pmcp.example/mcp",
        required_scopes=["read"],
        jwks=jwks,
    )

    assert claims.issuer == "https://issuer.example"
    assert claims.subject == "subject-1"
    assert claims.audience == ["https://pmcp.example/mcp"]
    assert claims.scopes == ["read", "write"]


def test_validate_resource_server_token_rejects_header_alg_outside_allowlist() -> None:
    token, jwks = _signed_token_fixture()

    with pytest.raises(ResourceServerAuthError) as exc_info:
        validate_resource_server_token(
            token,
            issuer="https://issuer.example",
            audience="https://pmcp.example/mcp",
            jwks=jwks,
            allowed_algorithms=("ES256",),
        )

    assert exc_info.value.error == "invalid_token"


@pytest.mark.parametrize(
    "token_audience",
    [
        ["https://pmcp.example/mcp", "https://other.example/mcp"],
        "https://other.example/mcp",
    ],
)
def test_validate_resource_server_token_uses_configured_canonical_audience(
    token_audience: str | list[str],
) -> None:
    token, jwks = _signed_token_fixture(audience=token_audience)

    if token_audience == "https://other.example/mcp":
        with pytest.raises(ResourceServerAuthError) as exc_info:
            validate_resource_server_token(
                token,
                issuer="https://issuer.example",
                audience="https://pmcp.example/mcp",
                jwks=jwks,
            )
        assert exc_info.value.error == "invalid_token"
    else:
        claims = validate_resource_server_token(
            token,
            issuer="https://issuer.example",
            audience="https://pmcp.example/mcp",
            jwks=jwks,
        )
        assert "https://pmcp.example/mcp" in claims.audience


@pytest.mark.asyncio
async def test_async_jwks_caches_and_coalesces_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwks = AsyncJWKS("https://issuer.example/jwks.json", ttl_seconds=30)
    calls = 0

    async def fake_fetch() -> dict[str, object]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"keys": [{"kid": "test-key"}]}

    monkeypatch.setattr(jwks, "_fetch", fake_fetch)

    first, second = await asyncio.gather(jwks.get(), jwks.get())
    cached = await jwks.get()

    assert first == second == cached == {"keys": [{"kid": "test-key"}]}
    assert calls == 1


@pytest.mark.asyncio
async def test_async_jwks_unknown_kid_forces_one_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token, _ = _signed_token_fixture()
    jwks = AsyncJWKS("https://issuer.example/jwks.json", ttl_seconds=30)
    calls = 0

    async def fake_fetch() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"keys": [{"kid": "other-key"}]}

    monkeypatch.setattr(jwks, "_fetch", fake_fetch)

    await jwks.get_for_token(token)

    assert calls == 2


@pytest.mark.asyncio
async def test_async_jwks_fetch_failures_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwks = AsyncJWKS("https://issuer.example/jwks.json?token=secret")

    async def fake_fetch() -> dict[str, object]:
        raise ResourceServerJWKSUnavailable(
            AuthMessage.JWKS_FETCH_FAILED,
            url="https://issuer.example/jwks.json?token=secret",
        )

    monkeypatch.setattr(jwks, "_fetch", fake_fetch)

    with pytest.raises(ResourceServerJWKSUnavailable) as exc_info:
        await jwks.get(force_refresh=True)

    assert "secret" not in str(exc_info.value)
    assert "REDACTED" in str(exc_info.value)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"audience": "https://other.example/mcp"}, "Invalid audience"),
        ({"issuer": "https://other.example"}, "Invalid issuer"),
        ({"expires_delta": -10}, "expired"),
        ({"not_before_delta": 300}, "not yet valid"),
    ],
)
def test_validate_resource_server_token_rejects_invalid_claims(
    kwargs: dict[str, object], expected: str
) -> None:
    fixture_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key in {"expires_delta", "not_before_delta"}
    }
    token, jwks = _signed_token_fixture(**fixture_kwargs)

    with pytest.raises(ResourceServerAuthError) as exc_info:
        validate_resource_server_token(
            token,
            issuer=str(kwargs.get("issuer", "https://issuer.example")),
            audience=str(kwargs.get("audience", "https://pmcp.example/mcp")),
            jwks=jwks,
        )

    assert expected in str(exc_info.value)


def test_validate_resource_server_token_rejects_missing_required_scope() -> None:
    token, jwks = _signed_token_fixture()

    with pytest.raises(ResourceServerAuthError) as exc_info:
        validate_resource_server_token(
            token,
            issuer="https://issuer.example",
            audience="https://pmcp.example/mcp",
            required_scopes=["admin"],
            jwks=jwks,
        )

    assert exc_info.value.error == "insufficient_scope"


def test_parse_www_authenticate_resource_metadata_and_scopes() -> None:
    challenge = (
        'Bearer resource_metadata="https://auth.example/.well-known/pr", '
        'scope="read write", error="insufficient_scope"'
    )

    parsed = parse_www_authenticate(challenge)

    assert parsed is not None
    assert parsed.scheme == "Bearer"
    assert parsed.resource_metadata_url == "https://auth.example/.well-known/pr"
    assert parsed.missing_scopes == ["read", "write"]
    assert parsed.error == "insufficient_scope"


def test_protected_resource_metadata_url_candidates_include_path_scope() -> None:
    urls = protected_resource_metadata_urls("https://mcp.example/v1/mcp")

    assert urls == [
        "https://mcp.example/.well-known/oauth-protected-resource",
        "https://mcp.example/.well-known/oauth-protected-resource/v1/mcp",
    ]


def test_normalize_auth_metadata_preserves_only_public_fields() -> None:
    metadata = normalize_auth_metadata(
        {"issuer": "https://issuer.example", "scopes_supported": ["read"]},
        protected_resource_metadata_url="https://mcp.example/pr?token=secret",
        client_id_metadata_document_url="https://client.example/doc",
    )

    assert metadata.oidc_issuer_url == "https://issuer.example"
    assert metadata.declared_scopes == ["read"]
    assert "secret" not in metadata.protected_resource_metadata_url


def test_sanitize_url_elicitation_url_accepts_https_and_redacts_query_secrets() -> None:
    url = sanitize_url_elicitation_url(
        "https://auth.example/consent?code=oauth-code&token=access-token"
        "&refresh_token=refresh-secret&state=ok"
    )

    assert url == (
        "https://auth.example/consent?code=%5BREDACTED%5D&token=%5BREDACTED%5D"
        "&refresh_token=%5BREDACTED%5D&state=ok"
    )
    assert "oauth-code" not in url
    assert "access-token" not in url
    assert "refresh-secret" not in url


def test_sanitize_url_elicitation_url_allows_loopback_http_for_operator() -> None:
    """Operator provenance keeps loopback HTTP -- local OAuth needs it.

    This is the surviving half of the original
    `test_sanitize_url_elicitation_url_allows_loopback_http`. Removing loopback
    globally to close #211 would have broken the frozen local-consent flow at
    `gateway.auth_connect`, where the operator types the redirect URL.
    """
    assert (
        sanitize_url_elicitation_url(
            "http://localhost:3000/cb?code=secret", provenance="operator"
        )
        == "http://localhost:3000/cb?code=%5BREDACTED%5D"
    )
    assert (
        sanitize_url_elicitation_url(
            "http://127.0.0.1/cb?token=secret", provenance="operator"
        )
        == "http://127.0.0.1/cb?token=%5BREDACTED%5D"
    )
    assert (
        sanitize_url_elicitation_url(
            "http://[::1]/cb?refresh_token=secret", provenance="operator"
        )
        == "http://[::1]/cb?refresh_token=%5BREDACTED%5D"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:3000/cb?code=secret",
        "http://127.0.0.1/cb?token=secret",
        "http://[::1]/cb?refresh_token=secret",
    ],
)
def test_sanitize_url_elicitation_url_refuses_loopback_http_from_remote(
    url: str,
) -> None:
    """#211: the same URL is refused when a downstream server supplied it.

    A loopback `http://` URL in a server's error payload is an attempt to point
    the operator at something on the operator's own machine. The inverse of the
    operator case above -- the provenance is the whole difference.
    """
    with pytest.raises(ValueError):
        sanitize_url_elicitation_url(url, provenance="remote")


def test_sanitize_url_elicitation_url_defaults_to_the_strict_remote_policy() -> None:
    """A caller that forgets `provenance` must lose loopback, not keep it."""
    with pytest.raises(ValueError):
        sanitize_url_elicitation_url("http://127.0.0.1/cb")


@pytest.mark.parametrize(
    "url",
    [
        "/relative/path",
        "ftp://auth.example/consent",
        "http://auth.example/consent",
        "https://127.0.0.1/consent",
        "https://[::1",
    ],
)
def test_sanitize_url_elicitation_url_rejects_non_loopback_http_and_invalid_urls(
    url: str,
) -> None:
    with pytest.raises(ValueError):
        sanitize_url_elicitation_url(url)


def test_parse_url_elicitation_error_redacts_url() -> None:
    parsed = parse_url_elicitation_error(
        {
            "error": {
                "code": -32042,
                "data": {
                    "elicitations": [
                        {
                            "elicitationId": "consent-1",
                            "url": "https://auth.example/consent?code=abc123",
                            "message": "Authorize access",
                        },
                        {
                            "elicitationId": "consent-2",
                            "url": "http://auth.example/consent?code=def456",
                        },
                    ]
                },
            }
        }
    )

    assert len(parsed) == 1
    assert parsed[0].elicitation_id == "consent-1"
    assert "abc123" not in parsed[0].url


def test_auth_redaction_covers_headers_userinfo_and_query_secrets() -> None:
    raw = (
        "Authorization: Bearer abc.def "
        "https://user:pass@example.test/cb?code=oauth-code&state=ok "
        "api_key=sk-secret"
    )

    redacted = sanitize_auth_diagnostic(raw)

    assert "abc.def" not in redacted
    assert "user:pass" not in redacted
    assert "oauth-code" not in redacted
    assert "sk-secret" not in redacted
    assert redact_auth_url("https://u:p@example.test/path?token=x").startswith(
        "https://example.test/path"
    )


def test_auth_redaction_covers_roadmap_query_keys_fragments_and_jwts() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1"
    raw = (
        "Bearer bearer-token "
        "api-key: sk-live-secret "
        f"auth_code={jwt} "
        "https://user:pass@example.test/cb?session=s1&sid=s2&jwt=j1"
        "&assertion=a1&saml=saml1&ticket=t1#frag "
        f"standalone {jwt}"
    )

    redacted = sanitize_auth_diagnostic(raw)
    safe_url = redact_auth_url(
        "https://user:pass@example.test/cb?session=s1&sid=s2&jwt=j1"
        "&assertion=a1&saml=saml1&ticket=t1#frag"
    )

    for leaked in [
        "bearer-token",
        "sk-live-secret",
        jwt,
        "user:pass",
        "s1",
        "s2",
        "j1",
        "a1",
        "saml1",
        "t1",
        "frag",
    ]:
        assert leaked not in redacted
        assert leaked not in safe_url


def test_auth_diagnostic_redacts_roadmap_keyword_values_in_free_text() -> None:
    raw = (
        "session=sess-123 sid: sid-123 cookie=chocolate "
        "set-cookie: pmcp=secret-cookie refresh_token=refresh-123 "
        "client_secret: client-123 access_token=access-123 "
        "id_token=id-123 jwt=jwt-123 assertion=assert-123 saml=saml-123"
    )

    redacted = sanitize_auth_diagnostic(raw)

    for leaked in [
        "sess-123",
        "sid-123",
        "chocolate",
        "secret-cookie",
        "refresh-123",
        "client-123",
        "access-123",
        "id-123",
        "jwt-123",
        "assert-123",
        "saml-123",
    ]:
        assert leaked not in redacted


def test_tenant_code_mode_auth_redaction_covers_callbacks_and_artifacts() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZW5hbnQifQ.N2QwODhmM2I4OTc1"
    raw = (
        "Authorization: Bearer tenant-secret "
        f"https://tenant.example/callback?code=oauth-code&id_token={jwt} "
        "https://tenant.example/artifacts/run-1?token=artifact-token "
        "TENANT_CODE_MODE_MCP_TOKEN=stored-token "
        "TENANT_CODE_MODE_TENANT_ID=tenant-123"
    )

    redacted = sanitize_auth_diagnostic(raw)

    for leaked in [
        "tenant-secret",
        "oauth-code",
        jwt,
        "artifact-token",
        "stored-token",
        "tenant-123",
    ]:
        assert leaked not in redacted
    assert "TENANT_CODE_MODE_MCP_TOKEN" in redacted
    assert "TENANT_CODE_MODE_TENANT_ID" in redacted


@pytest.mark.parametrize(
    ("raw_url", "secret_fragments"),
    [
        (
            "https://user:pass@auth.example/cb?code=oauth-code&state=ok",
            ["user:pass", "oauth-code"],
        ),
        (
            "https://auth.example/cb?bearer=secret-bearer&token=access-token",
            ["secret-bearer", "access-token"],
        ),
        (
            "https://auth.example/cb?jwt=eyJhbGciOiJIUzI1NiJ9.payload.sig",
            ["eyJhbGciOiJIUzI1NiJ9.payload.sig"],
        ),
        (
            "https://auth.example/cb?assertion=saml-secret&ticket=ticket-secret",
            ["saml-secret", "ticket-secret"],
        ),
    ],
)
def test_auth_url_matrix_redacts_secret_inputs_across_surfaces(
    raw_url: str, secret_fragments: list[str]
) -> None:
    metadata = normalize_auth_metadata(
        {"scopes_supported": ["read"]},
        protected_resource_metadata_url=raw_url,
        diagnostics=[f"provider returned {raw_url}"],
    )
    challenge = parse_www_authenticate(
        f'Bearer resource_metadata="{raw_url}", error="insufficient_scope", '
        'scope="read write", error_description="token secret-bearer failed"'
    )
    elicitation_url = sanitize_url_elicitation_url(raw_url)

    combined = " ".join(
        [
            metadata.model_dump_json(),
            challenge.model_dump_json() if challenge else "",
            elicitation_url,
        ]
    )
    for fragment in secret_fragments + ["secret-bearer"]:
        assert fragment not in combined
    assert metadata.declared_scopes == ["read"]
    assert challenge is not None
    assert challenge.missing_scopes == ["read", "write"]


def test_sanitize_public_auth_url_rejects_invalid_and_non_public_urls() -> None:
    assert (
        sanitize_public_auth_url("https://auth.example/meta?ticket=secret")
        == "https://auth.example/meta?ticket=%5BREDACTED%5D"
    )
    assert (
        sanitize_public_auth_url(
            "http://localhost:3000/meta?session=secret",
            allow_loopback_http=True,
        )
        == "http://localhost:3000/meta?session=%5BREDACTED%5D"
    )

    for url in [
        "/relative",
        "https://[::1",
        "https://127.0.0.1/meta",
        "https://10.0.0.1/meta",
        "https://169.254.169.254/meta",
        "https://224.0.0.1/meta",
        "https://0.0.0.0/meta",
        # #210: the two ranges the subtractive classifier let through.
        "https://100.64.0.1/meta",
        "https://[fec0::1]/meta",
        "ftp://auth.example/meta",
        "http://auth.example/meta",
    ]:
        with pytest.raises(ValueError):
            sanitize_public_auth_url(url)


# --------------------------------------------------------------------------- #
# Public-host classification matrix (#210)
#
# `_is_public_auth_host` classifies IP *literals* only. What follows is the
# specification for that classification, not a sample of it.
#
# Every IPv6 format that can carry an IPv4 address in its low 32 bits is named
# here with its RFC. The list is closed and standards-defined, which is what
# makes enumerating it defensible -- but it must not live only in the
# implementation. If a seventh embedding format is standardised, its absence
# from this matrix is the visible gap that catches it. Do not shorten or
# de-duplicate these labels.
#
#   RFC 4291  IPv4-mapped         ::ffff:0:0/96    unwrapped
#   RFC 4291  IPv4-compatible     ::/96            unwrapped (deprecated form)
#   RFC 6052  NAT64 well-known    64:ff9b::/96     unwrapped
#   RFC 5214  ISATAP              ..:0:5efe:a.b.c.d unwrapped by interface id
#             -- the interface id is the full 32 bits 00-00-5E-FE, or
#             02-00-5E-FE with the u/g bit set (RFC 5214 section 6.1). The
#             `5efe` hextet alone does NOT identify ISATAP; see the
#             over-rejection trap in _MUST_ACCEPT_HOSTS.
#   RFC 3056  6to4                2002::/16        already non-global
#   RFC 4380  Teredo              2001::/32        already non-global
#
# The last two pass today only because neither prefix is global -- luck, not
# design. They are pinned below so that stays true.
# --------------------------------------------------------------------------- #

_MUST_ACCEPT_HOSTS = [
    ("8.8.8.8", "public IPv4"),
    ("93.184.216.34", "public IPv4"),
    ("2001:4860:4860::8888", "public IPv6"),
    # Over-rejection traps: a rule adding `not is_reserved` fails both, because
    # every IPv4-mapped address is is_reserved.
    ("::ffff:8.8.8.8", "RFC 4291 IPv4-mapped, wrapping a public address"),
    ("64:ff9b::808:808", "RFC 6052 NAT64, wrapping a public address"),
    # Over-rejection trap for ISATAP: an ordinary global address that merely
    # carries `5efe` in that hextet. Matching the marker alone rather than the
    # full RFC 5214 interface identifier unwraps this to 10.0.0.5 and rejects a
    # genuinely public host. No other must-accept host carries an incidental
    # `5efe`, which is why the whole suite stayed green through that bug.
    (
        "2606:4700::1234:5efe:a00:5",
        "global address with an incidental 5efe hextet -- NOT RFC 5214 ISATAP",
    ),
]

_MUST_REJECT_HOSTS = [
    # --- Fixed by #210: ranges the subtractive classifier missed -------------
    ("100.64.0.1", "RFC 6598 CGNAT shared address space"),
    ("::ffff:100.64.0.1", "RFC 4291 IPv4-mapped, wrapping RFC 6598 CGNAT"),
    ("fec0::1", "RFC 3879 deprecated IPv6 site-local"),
    # --- Fixed by #210: IPv4 embedded in an IPv6 literal --------------------
    ("64:ff9b::a00:5", "RFC 6052 NAT64, embedding 10.0.0.5"),
    ("64:ff9b::7f00:1", "RFC 6052 NAT64, embedding 127.0.0.1"),
    ("::10.0.0.5", "RFC 4291 IPv4-compatible, embedding 10.0.0.5"),
    ("::127.0.0.1", "RFC 4291 IPv4-compatible, embedding 127.0.0.1"),
    ("::0:5efe:a00:5", "RFC 5214 ISATAP (00-00-5E-FE), embedding 10.0.0.5"),
    ("::0:5efe:7f00:1", "RFC 5214 ISATAP (00-00-5E-FE), embedding 127.0.0.1"),
    ("::200:5efe:a00:5", "RFC 5214 ISATAP with the u/g bit set (02-00-5E-FE)"),
    # --- Pinned, not fixed: these are already non-global ---------------------
    ("2002:0a00:0005::1", "RFC 3056 6to4, embedding 10.0.0.5"),
    ("2001:0:0:0:0:0:0a00:0005", "RFC 4380 Teredo"),
    # --- Fixed by #210: legacy numeric forms, which ip_address() cannot parse
    # and which the resolver reads directly -- no DNS lookup is involved.
    ("2852039166", "legacy decimal -> 169.254.169.254 (cloud metadata)"),
    ("0xA9FEA9FE", "legacy hex -> 169.254.169.254 (cloud metadata)"),
    ("0xa9fea9fe", "legacy hex, lowercased by urlparse"),
    ("0177.0.0.1", "legacy octal -> 127.0.0.1"),
    ("167772161", "legacy decimal -> 10.0.0.1"),
    ("2130706433", "legacy decimal -> 127.0.0.1"),
    ("0xa9.0xfe.0xa9.0xfe", "dotted hex -> 169.254.169.254"),
    ("0251.0376.0251.0376", "dotted octal -> 169.254.169.254"),
    ("169.254.43518", "3-part inet_aton -> 169.254.169.254"),
    ("169.16689662", "2-part inet_aton -> 169.254.169.254"),
    ("0x7f.1", "2-part dotted hex -> 127.0.0.1"),
    # glibc reads a leading zero as octal (8.0.0.1) but stricter resolvers read
    # it as decimal (10.0.0.1). Ambiguous means rejected: over-rejection is the
    # safe direction, and no must-accept host has a leading zero.
    ("010.0.0.1", "ambiguous leading zero; the decimal reading is 10.0.0.1"),
    ("5", "bare integer -> 0.0.0.5"),
    # --- Already rejected before #210; must not regress ----------------------
    ("224.0.0.1", "IPv4 multicast -- a bare is_global rule regresses this"),
    ("ff02::1", "IPv6 multicast -- a bare is_global rule regresses this"),
    ("169.254.169.254", "IPv4 link-local cloud metadata"),
    ("10.0.0.5", "RFC 1918 private"),
    ("::ffff:10.0.0.5", "RFC 4291 IPv4-mapped, wrapping RFC 1918"),
    ("127.0.0.1", "loopback"),
    ("localhost", "loopback name"),
    ("fc00::1", "RFC 4193 unique local"),
    ("2001:db8::1", "RFC 3849 documentation range"),
    ("::", "unspecified"),
]


def _auth_url(host: str) -> str:
    # An IPv6 literal must be bracketed, or urlparse reads the ':' as a port
    # separator and the URL is rejected for a reason that has nothing to do
    # with host classification.
    return f"https://{f'[{host}]' if ':' in host else host}/meta"


@pytest.mark.parametrize(
    "host,rationale", _MUST_ACCEPT_HOSTS, ids=[h for h, _ in _MUST_ACCEPT_HOSTS]
)
def test_public_auth_url_accepts_public_ip_literals(host: str, rationale: str) -> None:
    assert sanitize_public_auth_url(_auth_url(host)) == _auth_url(host), rationale


@pytest.mark.parametrize(
    "host,rationale", _MUST_REJECT_HOSTS, ids=[h for h, _ in _MUST_REJECT_HOSTS]
)
def test_public_auth_url_rejects_non_public_ip_literals(
    host: str, rationale: str
) -> None:
    with pytest.raises(ValueError) as excinfo:
        sanitize_public_auth_url(_auth_url(host))
    # Guard against passing for the wrong reason -- a malformed URL raises the
    # same exception type from a different branch.
    assert "non-public IP literal" in str(excinfo.value), rationale


def test_public_auth_url_error_message_does_not_claim_the_host_was_verified() -> None:
    """The old message promised far more than the check performs (#210)."""
    with pytest.raises(ValueError) as excinfo:
        sanitize_public_auth_url("https://10.0.0.5/meta")
    message = str(excinfo.value)
    assert "must be public" not in message
    assert "verified" not in message
    assert "non-public IP literal" in message


def test_public_auth_url_accepts_dns_names_unresolved_known_limitation_of_211() -> None:
    """KNOWN LIMITATION, tracked by #211 -- not an assertion of correctness.

    A DNS name is accepted without being resolved, so a name pointing at
    169.254.169.254 passes this check. This test pins the limitation so that
    closing #211 has to change it deliberately; #210 deliberately does not fix
    it, because resolving here would introduce a TOCTOU gap of its own.
    """
    assert (
        sanitize_public_auth_url("https://auth.example.com/meta")
        == "https://auth.example.com/meta"
    )


def test_public_auth_url_still_accepts_non_numeric_hosts_after_canonicalisation() -> (
    None
):
    """Canonicalising numeric hosts must not swallow the DNS path (#210)."""
    for host in [
        "auth.example.com",
        "metadata.google.internal",
        "1.example.com",  # a numeric label, but the host is not a number
        "999.999.999.999",  # numeric-looking, but no valid reading exists
        "0xdeadbeefcafe",  # exceeds 32 bits, so it is not an address
        "1.2.3.4.5",  # too many parts for inet_aton
    ]:
        assert sanitize_public_auth_url(_auth_url(host)) == _auth_url(host)


def test_public_auth_host_does_not_read_python_int_quirks_as_addresses() -> None:
    """`int()` accepts separators, signs, and non-ASCII digits; the parser must not.

    `int("1_0")` is 10 and `int("١٢٧")` is 127, so canonicalising with a bare
    `int(part)` would turn these hostnames into addresses. No resolver reads them
    that way, so they stay on the name path. The parser matches ASCII character
    classes before converting, which is what keeps that true.
    """
    for host in ["1_0", "١٢٧", "+2852039166"]:
        assert sanitize_public_auth_url(_auth_url(host)) == _auth_url(host)


def test_normalize_auth_metadata_omits_invalid_urls_with_safe_diagnostics() -> None:
    metadata = normalize_auth_metadata(
        {"issuer": "https://issuer.example", "scopes_supported": ["read", "write"]},
        protected_resource_metadata_url="http://auth.example/pr?session=secret",
        authorization_server_metadata_url="https://auth.example/as",
        client_id_metadata_document_url="/relative?ticket=secret",
    )

    assert metadata.protected_resource_metadata_url is None
    assert metadata.authorization_server_metadata_url == "https://auth.example/as"
    assert metadata.oidc_issuer_url == "https://issuer.example"
    assert metadata.client_id_metadata_document_url is None
    assert metadata.declared_scopes == ["read", "write"]
    assert metadata.diagnostics
    assert "secret" not in " ".join(metadata.diagnostics)


def test_fetch_json_metadata_uses_safe_request_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class FakeResponse:
        headers = {"content-type": "application/json"}

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, _limit: int) -> bytes:
            return b'{"issuer": "https://issuer.example"}'

    def fake_urlopen(request: object, timeout: float) -> FakeResponse:
        seen["url"] = getattr(request, "full_url")
        seen["accept"] = request.get_header("Accept")
        seen["authorization"] = request.get_header("Authorization")
        seen["cookie"] = request.get_header("Cookie")
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("pmcp.auth.urlopen", fake_urlopen)

    # A verified public literal -- since #211 this is the only shape that gets
    # fetched at all, so the happy path has to use one.
    data, error = fetch_json_metadata("https://93.184.216.34/meta?token=secret")

    assert data == {"issuer": "https://issuer.example"}
    assert error is None
    assert seen["url"] == "https://93.184.216.34/meta?token=%5BREDACTED%5D"
    assert seen["accept"] == "application/json"
    assert seen["authorization"] is None
    assert seen["cookie"] is None


def test_fetch_json_metadata_rejects_non_public_urls() -> None:
    data, error = fetch_json_metadata("http://auth.example/meta?token=secret")

    assert data is None
    assert error is not None
    assert "secret" not in error


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://127.0.0.1/x", "loopback HTTP -- reached urlopen before #211"),
        ("http://localhost/x", "loopback by name"),
        (
            "https://metadata.google.internal/x",
            "unresolved hostname -- reached urlopen before #211",
        ),
        ("https://auth.vendor.com/.well-known/x", "an ordinary unresolved name"),
        ("https://10.0.0.1/x", "private literal -- already rejected on main"),
    ],
)
def test_fetch_json_metadata_refuses_unverified_hosts_without_opening_them(
    monkeypatch: pytest.MonkeyPatch, url: str, why: str
) -> None:
    """#211: a refused fetch must never reach the network.

    `fetch_json_metadata` is the fail-closed side: pmcp retrieves this URL
    itself, so "accepted but unresolved" is not good enough the way it is on a
    relay path. Asserting on the return value alone is not sufficient -- an
    implementation that fetched first and judged afterwards would still return
    an error while the request had already left. So the opener is patched with
    one that fails the test if it is called at all.
    """
    calls: list[object] = []

    def forbidden_urlopen(request: object, timeout: float) -> object:
        calls.append(request)
        raise AssertionError(f"fetch_json_metadata opened a refused URL: {url}")

    monkeypatch.setattr("pmcp.auth.urlopen", forbidden_urlopen)

    data, error = fetch_json_metadata(url)

    assert calls == [], why
    assert data is None
    assert error is not None


def test_parse_www_authenticate_handles_quoted_edges_and_safe_metadata() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1"
    challenge = (
        'Bearer resource_metadata="https://auth.example/pr?ticket=secret", '
        'scope="read write", missing_scope="admin", '
        'error="insufficient_scope", '
        f'error_description="needs \\"admin\\" scope, token {jwt}"'
    )

    parsed = parse_www_authenticate(challenge)

    assert parsed is not None
    assert parsed.resource_metadata_url == (
        "https://auth.example/pr?ticket=%5BREDACTED%5D"
    )
    assert parsed.scope == "read write"
    assert parsed.missing_scopes == ["admin"]
    assert parsed.error == "insufficient_scope"
    assert parsed.error_description is not None
    assert jwt not in parsed.error_description
    assert '"admin"' in parsed.error_description


def test_parse_www_authenticate_omits_invalid_resource_metadata() -> None:
    parsed = parse_www_authenticate(
        'Bearer resource_metadata="http://auth.example/pr?token=secret", '
        'error_description="code=super-secret"'
    )

    assert parsed is not None
    assert parsed.resource_metadata_url is None
    assert parsed.error_description == "code=[REDACTED]"


def test_env_store_validates_env_var_names() -> None:
    valid_names = ["OPENAI_API_KEY", "_PMCP_TOKEN"]
    invalid_names = ["1TOKEN", "BAD-NAME", "BAD.NAME", "BAD NAME", "", "GOOD=bad"]

    for name in valid_names:
        assert validate_env_var_name(name) == name

    for name in invalid_names:
        with pytest.raises(ValueError):
            validate_env_var_name(name)


def test_env_store_round_trips_shell_significant_values(tmp_path: Path) -> None:
    env_path = tmp_path / "nested" / "pmcp.env"
    values = {
        "SPACES": "token with spaces",
        "HASH": "token#fragment",
        "SINGLE_QUOTE": "token'value",
        "DOUBLE_QUOTE": 'token"value',
        "BACKSLASH": r"token\value",
        "EQUALS": "token=value",
    }

    write_env_file(env_path, values)

    assert read_env_file(env_path) == values
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_env_store_rejects_injection_before_write(tmp_path: Path) -> None:
    env_path = tmp_path / "pmcp.env"

    with pytest.raises(ValueError):
        write_env_file(env_path, {"GOOD": "first\nINJECTED=second"})
    with pytest.raises(ValueError):
        write_env_file(env_path, {"GOOD=bad": "secret"})

    assert not env_path.exists()


# --- #211: pmcp must not present a relayed URL as one it verified ----------
#
# The fix is deliberately *not* "reject unverifiable names". Two board rounds
# established that doing so would refuse a well-behaved server's
# `https://auth.vendor.com/...` and kill URL-mode elicitation outright.
# Rejecting is right for a fetch and wrong for a relay. So these tests pin the
# other half: the URL still goes through, pmcp just stops vouching for it.


def _elicitation_payload(url: str) -> dict[str, object]:
    return {
        "error": {
            "code": -32042,
            "data": {"elicitationId": "el-1", "url": url, "message": "consent"},
        }
    }


def test_downstream_elicitation_relays_a_vendor_name_and_marks_it_unverified() -> None:
    """The feature survives: a name is relayed, but not vouched for."""
    parsed = parse_url_elicitation_error(
        _elicitation_payload("https://auth.vendor.com/consent")
    )

    assert len(parsed) == 1
    assert parsed[0].url == "https://auth.vendor.com/consent"
    assert parsed[0].url_verified is False


def test_downstream_elicitation_marks_a_public_literal_verified() -> None:
    """The signal has to distinguish, not label everything unverified."""
    parsed = parse_url_elicitation_error(
        _elicitation_payload("https://93.184.216.34/consent")
    )

    assert len(parsed) == 1
    assert parsed[0].url_verified is True


def test_downstream_elicitation_rejects_loopback_http() -> None:
    """#211: this payload was accepted and shown to the operator before."""
    assert (
        parse_url_elicitation_error(_elicitation_payload("http://127.0.0.1/cb")) == []
    )


def test_url_elicitation_next_step_carries_the_caveat_for_a_name() -> None:
    """`next_step` is the string an agent follows, so the caveat lives there.

    A JSON `url_verified: false` beside an unqualified "Open the URL" is not a
    fix: the agent reads the instruction. This asserts on the emitted text.
    """
    parsed = parse_url_elicitation_error(
        _elicitation_payload("https://metadata.google.internal/consent")
    )

    next_step = parsed[0].next_step
    assert next_step is not None
    assert "not verified where this URL points" in next_step
    assert "does not resolve host names" in next_step
    # The actionable instruction must survive the qualification.
    assert "gateway.auth_connect" in next_step
    assert "consent_acknowledged=true" in next_step


def test_url_elicitation_next_step_is_unqualified_for_a_verified_literal() -> None:
    parsed = parse_url_elicitation_error(
        _elicitation_payload("https://93.184.216.34/consent")
    )

    next_step = parsed[0].next_step
    assert next_step is not None
    assert "not verified" not in next_step
    assert next_step.startswith("Open the URL out of band")


def test_url_elicitation_info_defaults_to_unverified() -> None:
    """Constructed without the signal, the type must under-claim.

    A default of "verified" would reintroduce the vouch at every call site this
    change missed.
    """
    info = UrlElicitationInfo(elicitation_id="el-1", url="https://auth.vendor.com/x")

    assert info.url_verified is False


def test_auth_challenge_marks_an_unresolved_name_unverified() -> None:
    """`parse_www_authenticate` relays a header field from a remote server."""
    parsed = parse_www_authenticate(
        'Bearer resource_metadata="https://metadata.google.internal/meta"'
    )

    assert parsed is not None
    assert parsed.resource_metadata_url == "https://metadata.google.internal/meta"
    assert parsed.resource_metadata_url_verified is False
    assert '"resource_metadata_url_verified":false' in parsed.model_dump_json()


def test_auth_challenge_marks_a_public_literal_verified() -> None:
    parsed = parse_www_authenticate(
        'Bearer resource_metadata="https://93.184.216.34/meta"'
    )

    assert parsed is not None
    assert parsed.resource_metadata_url_verified is True


def test_auth_challenge_info_defaults_to_unverified() -> None:
    assert AuthChallengeInfo(scheme="Bearer").resource_metadata_url_verified is False


def test_auth_metadata_reports_a_literal_and_a_name_differently() -> None:
    """A single object-level flag cannot pass this.

    `AuthMetadataInfo` carries five independent URLs. One bool would have to
    describe a public literal and an unresolved name at once, and would be a
    lie about one of them whichever way it fell.
    """
    metadata = normalize_auth_metadata(
        protected_resource_metadata_url="https://93.184.216.34/pr",
        authorization_server_metadata_url="https://auth.vendor.com/as",
    )

    assert metadata.protected_resource_metadata_url == "https://93.184.216.34/pr"
    assert metadata.authorization_server_metadata_url == "https://auth.vendor.com/as"
    assert metadata.url_verified("protected_resource_metadata_url") is True
    assert metadata.url_verified("authorization_server_metadata_url") is False
    assert metadata.verified_urls == ["protected_resource_metadata_url"]
    assert any(
        "authorization_server_metadata_url is relayed unverified" in diagnostic
        for diagnostic in metadata.diagnostics
    )


def test_auth_metadata_url_fields_stay_plain_strings() -> None:
    """transport/http.py interpolates one of these into a WWW-Authenticate
    header, so wrapping them would be a silent contract break."""
    metadata = normalize_auth_metadata(
        protected_resource_metadata_url="https://auth.vendor.com/pr"
    )

    assert isinstance(metadata.protected_resource_metadata_url, str)
    assert f'resource_metadata="{metadata.protected_resource_metadata_url}"' == (
        'resource_metadata="https://auth.vendor.com/pr"'
    )


def test_auth_metadata_defaults_to_no_verified_urls() -> None:
    assert AuthMetadataInfo().verified_urls == []
    assert AuthMetadataInfo().url_verified("protected_resource_metadata_url") is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://93.184.216.34/x", True),
        ("https://[2606:4700::1111]/x", True),
        ("https://auth.vendor.com/x", False),
        ("https://metadata.google.internal/x", False),
        # Name-shaped but numeric: #210 classifies it as the literal it is.
        ("https://0xA9FEA9FE/x", False),
        ("https://169.254.169.254./x", False),
        ("https://127.0.0.1/x", False),
        ("http://93.184.216.34/x", False),
        ("not-a-url", False),
        ("https://[::1", False),
    ],
)
def test_is_verified_public_auth_url(url: str, expected: bool) -> None:
    """The predicate answers "did pmcp check this?", not "is this allowed?".

    A DNS name is False even though `sanitize_public_auth_url` accepts it --
    that gap between accepted and checked is the whole of #211.
    """
    assert is_verified_public_auth_url(url) is expected


# ---------------------------------------------------------------------------
# S-07 / S-08 (see Consiliency/pmcp#231): JWKS refresh amplifier and fetch bound
# ---------------------------------------------------------------------------

_JWKS_URL = "https://issuer.example/jwks.json"


def _kid_token(kid: str) -> str:
    """An unsigned-in-effect token whose only job is to carry a ``kid`` header."""
    return jwt.encode({}, "x" * 32, algorithm="HS256", headers={"kid": kid})


class _FakeClock:
    """A monotonic clock the test moves by hand. Patched onto ``pmcp.auth.time``
    (the module attribute), never onto ``time.monotonic`` itself, which the
    event loop also reads."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_s07_random_unknown_kids_do_not_each_force_a_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwks = AsyncJWKS(_JWKS_URL, ttl_seconds=30)
    calls = 0

    async def fake_fetch() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"keys": [{"kid": "known"}]}

    monkeypatch.setattr(jwks, "_fetch", fake_fetch)

    # Two distinct random kids: the attacker's shape. The first unknown kid
    # may force one refresh; the second, inside the cooldown, must not.
    await jwks.get_for_token(_kid_token("random-1"))
    await jwks.get_for_token(_kid_token("random-2"))

    assert calls <= 2, f"unknown kids each forced a fetch: calls={calls}"


@pytest.mark.asyncio
async def test_s07_concurrent_unknown_kids_share_one_forced_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwks = AsyncJWKS(_JWKS_URL, ttl_seconds=300)
    fetched = {"keys": [{"kid": "known"}]}
    release_first_fetch = asyncio.Event()
    fetches = 0

    async def fake_fetch() -> dict[str, object]:
        nonlocal fetches
        fetches += 1
        if fetches == 1:
            # Hold the cache-filling fetch until the whole burst is queued.
            await release_first_fetch.wait()
        return fetched

    monkeypatch.setattr(jwks, "_fetch", fake_fetch)

    n = 5
    tasks = [
        asyncio.create_task(jwks.get_for_token(_kid_token(f"unknown-{i}")))
        for i in range(n)
    ]
    for _ in range(10):
        await asyncio.sleep(0)
    assert fetches == 1 and jwks._refresh_lock.locked(), (
        "the burst was not queued behind the first fetch"
    )

    release_first_fetch.set()
    results = await asyncio.gather(*tasks)

    assert all(result is fetched for result in results)
    assert fetches <= 2, (
        "concurrent unknown kids amplified the refresh: "
        f"fetches={fetches} (1 initial + {fetches - 1} forced for {n} "
        "concurrent requests)"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_duration", [0.0, 5.0], ids=["immediate", "timeout"])
async def test_s07_s08_recovery_after_failed_forced_refresh_waits_out_the_cooldown(
    monkeypatch: pytest.MonkeyPatch, failure_duration: float
) -> None:
    clock = _FakeClock()
    monkeypatch.setattr("pmcp.auth.time", SimpleNamespace(monotonic=clock))
    cooldown, backoff = 10.0, 5.0  # the default relationship, C > B, on purpose
    jwks = AsyncJWKS(
        _JWKS_URL,
        ttl_seconds=300,
        forced_refresh_cooldown_seconds=cooldown,
        refresh_failure_backoff_seconds=backoff,
    )
    old = {"keys": [{"kid": "old"}]}
    rotated = {"keys": [{"kid": "rotated"}]}
    jwks._jwks = old
    jwks._expires_at = clock() + 300
    endpoint_down = True
    fetches = 0

    async def fake_fetch() -> dict[str, object]:
        nonlocal fetches
        fetches += 1
        if endpoint_down:
            clock.advance(failure_duration)
            raise ResourceServerJWKSUnavailable(
                AuthMessage.JWKS_FETCH_FAILED, url="https://issuer.example/jwks.json"
            )
        return rotated

    monkeypatch.setattr(jwks, "_fetch", fake_fetch)
    token = _kid_token("rotated")

    t0 = clock()
    with pytest.raises(ResourceServerJWKSUnavailable):
        await jwks.get_for_token(token)
    assert fetches == 1
    endpoint_down = False

    eligible_at = t0 + max(cooldown, failure_duration + backoff)
    clock.now = eligible_at - 1.0
    served = await jwks.get_for_token(token)
    assert fetches == 1, (
        f"a forced fetch ran at t={clock.now - t0:.1f}s, before "
        f"max(C, d+B)={eligible_at - t0:.1f}s: fetches={fetches}"
    )
    assert served is old

    clock.now = eligible_at
    served = await jwks.get_for_token(token)
    assert fetches == 2, f"no forced fetch at max(C, d+B): fetches={fetches}"
    assert served is rotated


@pytest.mark.asyncio
async def test_s07_s08_backoff_rejection_does_not_consume_the_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    monkeypatch.setattr("pmcp.auth.time", SimpleNamespace(monotonic=clock))
    # Backoff outlasting the cooldown, deliberately: the composition bug only
    # exists when d + B > C.
    jwks = AsyncJWKS(
        _JWKS_URL,
        ttl_seconds=300,
        forced_refresh_cooldown_seconds=0.4,
        refresh_failure_backoff_seconds=0.5,
    )
    old = {"keys": [{"kid": "old"}]}
    rotated = {"keys": [{"kid": "rotated"}]}
    jwks._jwks = old
    jwks._expires_at = clock() + 300
    endpoint_down = True
    fetches = 0

    async def fake_fetch() -> dict[str, object]:
        nonlocal fetches
        fetches += 1
        if endpoint_down:
            raise ResourceServerJWKSUnavailable(
                AuthMessage.JWKS_FETCH_FAILED, url="https://issuer.example/jwks.json"
            )
        return rotated

    monkeypatch.setattr(jwks, "_fetch", fake_fetch)
    token = _kid_token("rotated")

    t0 = clock()
    with pytest.raises(ResourceServerJWKSUnavailable):
        await jwks.get_for_token(token)
    assert fetches == 1
    endpoint_down = False

    clock.now = t0 + 0.45  # cooldown elapsed, backoff still active
    served = await jwks.get_for_token(token)
    assert fetches == 1, f"a fetch ran inside the backoff window: fetches={fetches}"
    assert served is old

    clock.now = t0 + 0.55  # first forced attempt after backoff expiry
    served = await jwks.get_for_token(token)
    assert fetches == 2, (
        f"rotated key not accepted at backoff expiry: fetches={fetches}"
    )
    assert served is rotated


@pytest.mark.asyncio
async def test_s08_fetch_is_bounded_by_a_timeout() -> None:
    assert AsyncJWKS(_JWKS_URL)._fetch_timeout_seconds == 5.0

    release = asyncio.Event()

    async def hang(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Accept the connection and never answer.
        await release.wait()
        writer.close()

    server = await asyncio.start_server(hang, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        jwks = AsyncJWKS(_JWKS_URL, fetch_timeout_seconds=0.5)
        jwks._raw_url = f"http://127.0.0.1:{port}/jwks"  # bypass URL policy
        # The internal 0.5 s total timeout must fire well before the 5 s
        # outer bound; without it aiohttp's 300 s default would win.
        with pytest.raises(ResourceServerJWKSUnavailable):
            await asyncio.wait_for(jwks._fetch(), timeout=5)
    finally:
        release.set()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_s08_concurrent_get_bounds_the_last_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Deterministic form of "the last of N waiters waits ~one timeout, not
    # N x timeout": every fetch is (already) timeout-bounded, so the bound is
    # the number of fetches the burst performs. One failure must be shared.
    jwks = AsyncJWKS(_JWKS_URL, ttl_seconds=300)
    release_first_fetch = asyncio.Event()
    fetches = 0

    async def fake_fetch() -> dict[str, object]:
        nonlocal fetches
        fetches += 1
        if fetches == 1:
            await release_first_fetch.wait()
        raise ResourceServerJWKSUnavailable(
            AuthMessage.JWKS_FETCH_FAILED, url="https://issuer.example/jwks.json"
        )

    monkeypatch.setattr(jwks, "_fetch", fake_fetch)

    n = 5
    tasks = [asyncio.create_task(jwks.get()) for _ in range(n)]
    for _ in range(10):
        await asyncio.sleep(0)
    assert fetches == 1 and jwks._refresh_lock.locked(), (
        "the waiters were not queued behind the first fetch"
    )

    release_first_fetch.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(isinstance(r, ResourceServerJWKSUnavailable) for r in results)
    assert fetches == 1, (
        "last waiter not bounded: each waiter re-attempted the failed fetch: "
        f"fetches={fetches} for {n} waiters (~{fetches} x timeout)"
    )


# ---------------------------------------------------------------------------
# A JWKS with no usable keys is "key set unavailable", not a crash
# (see Consiliency/pmcp#320)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "jwks_doc",
    [
        {"keys": []},
        {"keys": [{"kty": "RSA", "kid": "malformed-key-kid"}]},
    ],
    ids=["empty", "malformed_only"],
)
def test_jwks_no_usable_keys_is_unavailable_not_a_crash(
    jwks_doc: dict[str, object],
) -> None:
    token, _ = _signed_token_fixture()

    with pytest.raises(ResourceServerJWKSUnavailable) as exc_info:
        validate_resource_server_token(
            token,
            issuer="https://issuer.example",
            audience="https://pmcp.example/mcp",
            jwks=jwks_doc,
        )

    assert exc_info.value.error == "temporarily_unavailable"
    message = str(exc_info.value)
    for leak in ("malformed-key-kid", "RSA", "cryptography", token):
        assert leak not in message


# ---------------------------------------------------------------------------
# S-08 failure accounting (PR review F2/F3, see Consiliency/pmcp#231): every
# non-cancellation fetch failure opens the shared backoff; cancellation does not.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s08_any_fetch_exception_opens_the_shared_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwks = AsyncJWKS(_JWKS_URL, ttl_seconds=300)
    release_first_fetch = asyncio.Event()
    fetches = 0

    async def fake_fetch() -> dict[str, object]:
        nonlocal fetches
        fetches += 1
        if fetches == 1:
            await release_first_fetch.wait()
        raise RecursionError("maximum recursion depth exceeded SENTINEL-F3")

    monkeypatch.setattr(jwks, "_fetch", fake_fetch)

    n = 5
    tasks = [asyncio.create_task(jwks.get()) for _ in range(n)]
    for _ in range(10):
        await asyncio.sleep(0)
    assert fetches == 1 and jwks._refresh_lock.locked()

    release_first_fetch.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    kinds = sorted({type(r).__name__ for r in results})
    assert all(isinstance(r, ResourceServerJWKSUnavailable) for r in results), (
        f"a non-JWKSUnavailable fetch failure escaped: {kinds}"
    )
    assert all("SENTINEL-F3" not in str(r) for r in results)
    assert fetches == 1, (
        f"the failure did not open the shared backoff: fetches={fetches} "
        f"for {n} waiters"
    )


@pytest.mark.asyncio
async def test_s08_deeply_nested_jwks_body_is_unavailable() -> None:
    body = b"[" * 100_000 + b"]" * 100_000  # under the 512 KiB cap

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        jwks = AsyncJWKS(_JWKS_URL, fetch_timeout_seconds=5)
        jwks._raw_url = f"http://127.0.0.1:{port}/jwks"  # bypass URL policy
        with pytest.raises(ResourceServerJWKSUnavailable) as exc_info:
            await jwks._fetch()
    finally:
        server.close()
        await server.wait_closed()

    assert "Invalid JWKS JSON" in str(exc_info.value)


@pytest.mark.asyncio
async def test_s08_cancelled_fetch_does_not_open_the_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Decision: cancellation means the caller went away, not that the endpoint
    # failed, so it must not 503 the other waiters for the backoff window. The
    # lock is released and the cache is untouched; the next waiter fetches.
    jwks = AsyncJWKS(_JWKS_URL, ttl_seconds=300)
    fetched = {"keys": [{"kid": "known"}]}
    hang_first_fetch = asyncio.Event()  # never set
    fetches = 0

    async def fake_fetch() -> dict[str, object]:
        nonlocal fetches
        fetches += 1
        if fetches == 1:
            await hang_first_fetch.wait()
        return fetched

    monkeypatch.setattr(jwks, "_fetch", fake_fetch)

    holder = asyncio.create_task(jwks.get())
    for _ in range(10):
        await asyncio.sleep(0)
    waiter = asyncio.create_task(jwks.get())
    for _ in range(10):
        await asyncio.sleep(0)
    assert fetches == 1 and jwks._refresh_lock.locked()

    holder.cancel()
    holder_result, waiter_result = await asyncio.gather(
        holder, waiter, return_exceptions=True
    )

    assert waiter_result is fetched, (
        f"a cancelled fetch opened the backoff: waiter got {waiter_result!r}"
    )
    assert isinstance(holder_result, asyncio.CancelledError)
    assert fetches == 2
    assert jwks._last_refresh_failure == float("-inf")
    assert not jwks._refresh_lock.locked()


# ---------------------------------------------------------------------------
# Forged-token matrix (PR review F1, see Consiliency/pmcp#231): whatever an
# unauthenticated caller puts in a token, validation ends in invalid_token
# with a value-free description -- never another exception.
# ---------------------------------------------------------------------------

_SENTINEL = "SENTINELF1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _forge_token(
    header: object = None,
    *,
    raw_header: bytes | None = None,
    raw_payload: bytes | None = None,
) -> str:
    """An unsigned token with an arbitrary header/payload and a junk signature;
    the sentinel is planted in the payload and signature of every case."""
    h = raw_header if raw_header is not None else json.dumps(header).encode()
    p = (
        raw_payload
        if raw_payload is not None
        else json.dumps({"sub": _SENTINEL, "iss": "https://issuer.example"}).encode()
    )
    return f"{_b64(h)}.{_b64(p)}.{_b64(b'sig-' + _SENTINEL.encode())}"


def _forged_token_keys() -> dict[str, dict[str, object]]:
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519
    from jwt.algorithms import ECAlgorithm, OKPAlgorithm, RSAAlgorithm

    def jwk(algorithm: Any, public_key: Any, kid: str) -> dict[str, object]:
        data = algorithm.to_jwk(public_key, as_dict=True)
        data["kid"] = kid
        return data

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return {
        "rsa": jwk(RSAAlgorithm, rsa_key.public_key(), f"rsa-{_SENTINEL}"),
        "ec256": jwk(
            ECAlgorithm,
            ec.generate_private_key(ec.SECP256R1()).public_key(),
            f"ec256-{_SENTINEL}",
        ),
        "ec384": jwk(
            ECAlgorithm,
            ec.generate_private_key(ec.SECP384R1()).public_key(),
            f"ec384-{_SENTINEL}",
        ),
        "okp": jwk(
            OKPAlgorithm,
            ed25519.Ed25519PrivateKey.generate().public_key(),
            f"okp-{_SENTINEL}",
        ),
        "oct": {"kty": "oct", "kid": f"oct-{_SENTINEL}", "k": _b64(b"k" * 32)},
    }


FORGED_TOKEN_KEYS = _forged_token_keys()
FORGED_TOKEN_ALGS = [
    "RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
    "ES256", "ES256K", "ES384", "ES512", "EdDSA", "Ed25519", "HS256",
    "none", "NONE", "bogus", _SENTINEL,
]  # fmt: skip
# The default allow-list, and every algorithm pyjwt can verify with a public key.
FORGED_TOKEN_ALLOW_LISTS = {
    "default": ("RS256", "ES256"),
    "wide": (
        "RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
        "ES256", "ES256K", "ES384", "ES512", "EdDSA",
    ),
}  # fmt: skip


def forged_tokens_for(key: dict[str, object]) -> dict[str, str]:
    """Every allowed-or-not alg against this key, with and without its kid,
    plus the header/payload shapes the validator parses before decoding."""
    kid = key["kid"]
    tokens: dict[str, str] = {}
    for alg in FORGED_TOKEN_ALGS:
        tokens[f"{alg}/kid"] = _forge_token({"alg": alg, "typ": "JWT", "kid": kid})
        tokens[f"{alg}/no-kid"] = _forge_token({"alg": alg, "typ": "JWT"})
    odd_headers: dict[str, object] = {
        "kid-int": {"alg": "RS256", "kid": 5},
        "kid-list": {"alg": "RS256", "kid": [_SENTINEL]},
        "kid-object": {"alg": "RS256", "kid": {"x": _SENTINEL}},
        "alg-list": {"alg": ["RS256"], "kid": kid},
        "alg-missing": {"kid": kid},
        "huge-header": {"alg": "RS256", "kid": kid, "x": _SENTINEL * 20_000},
        "crit-unknown": {"alg": "RS256", "kid": kid, "crit": [_SENTINEL]},
        "crit-not-list": {"alg": "RS256", "kid": kid, "crit": _SENTINEL},
        "crit-b64": {"alg": "RS256", "kid": kid, "crit": ["b64"], "b64": False},
        "jku": {"alg": "RS256", "kid": kid, "jku": f"https://evil/{_SENTINEL}"},
        "x5u": {"alg": "RS256", "kid": kid, "x5u": f"https://evil/{_SENTINEL}"},
        "embedded-jwk": {"alg": "RS256", "jwk": key},
    }
    for name, header in odd_headers.items():
        tokens[name] = _forge_token(header)
    raw_headers = {
        "header-array": b"[1, 2]",
        "header-string": b'"RS256"',
        "header-not-json": b"{" + _SENTINEL.encode(),
        "header-not-utf8": b"\xff\xfe" + _SENTINEL.encode(),
        "header-nested": b"[" * 5_000 + b"]" * 5_000,
        "header-nested-object": b'{"a":' * 3_000 + b"1" + b"}" * 3_000,
    }
    for name, raw in raw_headers.items():
        tokens[name] = _forge_token(raw_header=raw)
    good_header = json.dumps({"alg": "RS256", "kid": kid}).encode()
    raw_payloads = {
        "payload-array": b"[1]",
        "payload-string": b'"x"',
        "payload-not-json": _SENTINEL.encode(),
        "payload-nested": b"[" * 5_000 + b"]" * 5_000,
    }
    for name, raw in raw_payloads.items():
        tokens[name] = _forge_token(raw_header=good_header, raw_payload=raw)
    tokens["two-segments"] = f"{_SENTINEL}.{_SENTINEL}"
    tokens["four-segments"] = f"a.b.c.{_SENTINEL}"
    tokens["bad-base64"] = f"!!!.???.{_SENTINEL}"
    tokens["empty-segments"] = ".."
    return tokens


@pytest.mark.parametrize("allow_list", sorted(FORGED_TOKEN_ALLOW_LISTS))
@pytest.mark.parametrize("key_name", [*sorted(FORGED_TOKEN_KEYS), "all"])
def test_forged_tokens_are_invalid_token_and_value_free(
    allow_list: str, key_name: str
) -> None:
    keys = (
        list(FORGED_TOKEN_KEYS.values())
        if key_name == "all"
        else [FORGED_TOKEN_KEYS[key_name]]
    )
    probe_key = keys[0]
    escaped: list[str] = []
    leaked: list[str] = []
    for case, token in forged_tokens_for(probe_key).items():
        try:
            validate_resource_server_token(
                token,
                issuer="https://issuer.example",
                audience="https://pmcp.example/mcp",
                jwks={"keys": keys},
                allowed_algorithms=FORGED_TOKEN_ALLOW_LISTS[allow_list],
            )
        except ResourceServerJWKSUnavailable as exc:
            escaped.append(f"{case}: JWKS unavailable ({exc})")
        except ResourceServerAuthError as exc:
            if exc.error != "invalid_token":
                escaped.append(f"{case}: error={exc.error}")
            if _SENTINEL in str(exc) or _SENTINEL in exc.description:
                leaked.append(case)
        except Exception as exc:  # noqa: BLE001 -- the assertion target
            escaped.append(f"{case}: {type(exc).__name__}")
        else:
            escaped.append(f"{case}: ACCEPTED")

    assert not escaped, f"forged tokens not mapped to invalid_token: {escaped}"
    assert not leaked, f"token/key content in the error description: {leaked}"
