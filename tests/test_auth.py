"""Tests for auth discovery, URL elicitation, and redaction helpers."""

from __future__ import annotations

from pmcp.auth import (
    normalize_auth_metadata,
    parse_url_elicitation_error,
    parse_www_authenticate,
    protected_resource_metadata_urls,
    redact_auth_url,
    sanitize_auth_diagnostic,
)
from pmcp.types import AuthConnectInput, ProvisionOutput, ServerHealthInfo


def test_legacy_auth_models_validate_without_new_fields() -> None:
    provision = ProvisionOutput(ok=False, server="x", message="needs key")
    health = ServerHealthInfo(name="x", status="offline", tool_count=0)
    auth_input = AuthConnectInput(server_name="x", credential="secret")

    assert provision.auth_state == "none"
    assert health.auth_state == "none"
    assert auth_input.auth_mode == "api_key"


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
                        }
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
