"""Tests for auth discovery, URL elicitation, and redaction helpers."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from pmcp.auth import (
    fetch_json_metadata,
    normalize_auth_metadata,
    parse_url_elicitation_error,
    parse_www_authenticate,
    protected_resource_metadata_urls,
    redact_auth_url,
    sanitize_auth_diagnostic,
    sanitize_public_auth_url,
    sanitize_url_elicitation_url,
)
from pmcp.env_store import read_env_file, validate_env_var_name, write_env_file
from pmcp.remote_auth import (
    MissingRemoteHeaderAuthError,
    build_remote_header_env_lookup,
    resolve_remote_headers,
)
from pmcp.types import AuthConnectInput, ProvisionOutput, ServerHealthInfo


def test_legacy_auth_models_validate_without_new_fields() -> None:
    provision = ProvisionOutput(ok=False, server="x", message="needs key")
    health = ServerHealthInfo(name="x", status="offline", tool_count=0)
    auth_input = AuthConnectInput(server_name="x", credential="secret")

    assert provision.auth_state == "none"
    assert provision.missing_env_vars == []
    assert health.auth_state == "none"
    assert health.missing_env_vars == []
    assert auth_input.auth_mode == "api_key"


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


def test_sanitize_url_elicitation_url_allows_loopback_http() -> None:
    assert (
        sanitize_url_elicitation_url("http://localhost:3000/cb?code=secret")
        == "http://localhost:3000/cb?code=%5BREDACTED%5D"
    )
    assert (
        sanitize_url_elicitation_url("http://127.0.0.1/cb?token=secret")
        == "http://127.0.0.1/cb?token=%5BREDACTED%5D"
    )
    assert (
        sanitize_url_elicitation_url("http://[::1]/cb?refresh_token=secret")
        == "http://[::1]/cb?refresh_token=%5BREDACTED%5D"
    )


@pytest.mark.parametrize(
    "url",
    [
        "/relative/path",
        "ftp://auth.example/consent",
        "http://auth.example/consent",
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
        "ftp://auth.example/meta",
        "http://auth.example/meta",
    ]:
        with pytest.raises(ValueError):
            sanitize_public_auth_url(url)


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

    data, error = fetch_json_metadata("https://auth.example/meta?token=secret")

    assert data == {"issuer": "https://issuer.example"}
    assert error is None
    assert seen["url"] == "https://auth.example/meta?token=%5BREDACTED%5D"
    assert seen["accept"] == "application/json"
    assert seen["authorization"] is None
    assert seen["cookie"] is None


def test_fetch_json_metadata_rejects_non_public_urls() -> None:
    data, error = fetch_json_metadata("http://auth.example/meta?token=secret")

    assert data is None
    assert error is not None
    assert "secret" not in error


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
