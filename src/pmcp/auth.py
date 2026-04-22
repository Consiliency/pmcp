"""Authorization metadata, elicitation, and redaction helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from urllib.parse import parse_qsl, quote, urlparse, urlunparse
from urllib.request import Request, urlopen

from pmcp.types import AuthChallengeInfo, AuthMetadataInfo, UrlElicitationInfo

AUTH_SECRET_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "auth_code",
    "authorization",
    "client_secret",
    "code",
    "id_token",
    "key",
    "password",
    "refresh_token",
    "secret",
    "token",
}


def redact_auth_url(url: str) -> str:
    """Strip URL userinfo and redact auth-bearing query values."""
    parsed = urlparse(url)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    query_parts = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in AUTH_SECRET_QUERY_KEYS:
            query_parts.append((key, "[REDACTED]"))
        else:
            query_parts.append((key, value))
    query = "&".join(f"{quote(k)}={quote(v)}" for k, v in query_parts)
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, query, ""))


def sanitize_auth_diagnostic(value: object) -> str:
    """Return a display-safe diagnostic string for auth failures."""
    text = str(value)
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(bearer|token|api[_-]?key|apikey|secret|password|code)"
        r"([\s:=]+)([A-Za-z0-9._~+/=-]{6,})",
        r"\1\2[REDACTED]",
        text,
    )
    text = re.sub(
        r"https?://[^\s\"'<>]+",
        lambda match: redact_auth_url(match.group(0).rstrip(").,;")),
        text,
    )
    return text[:400]


def parse_www_authenticate(header: str) -> AuthChallengeInfo | None:
    """Parse a WWW-Authenticate challenge for non-secret MCP auth hints."""
    if not header.strip():
        return None

    # Split only on commas that start another auth scheme or parameter boundary.
    challenge = header.strip()
    scheme, _, rest = challenge.partition(" ")
    if not scheme:
        return None

    params: dict[str, str] = {}
    for match in re.finditer(r'([A-Za-z_][A-Za-z0-9_-]*)=("[^"]*"|[^,\s]+)', rest):
        key = match.group(1).lower()
        raw_value = match.group(2).strip()
        if raw_value.startswith('"') and raw_value.endswith('"'):
            raw_value = raw_value[1:-1]
        params[key] = raw_value

    scope = params.get("scope")
    missing = params.get("missing_scope") or scope or ""
    missing_scopes = [part for part in missing.split() if part]

    return AuthChallengeInfo(
        scheme=scheme,
        resource_metadata_url=redact_auth_url(params["resource_metadata"])
        if params.get("resource_metadata")
        else None,
        scope=scope,
        missing_scopes=missing_scopes,
        error=params.get("error"),
        error_description=sanitize_auth_diagnostic(params["error_description"])
        if params.get("error_description")
        else None,
    )


def protected_resource_metadata_urls(endpoint_url: str) -> list[str]:
    """Return candidate OAuth protected-resource metadata URLs for an endpoint."""
    parsed = urlparse(endpoint_url)
    if not parsed.scheme or not parsed.netloc:
        return []
    root = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
    candidates = [f"{root}/.well-known/oauth-protected-resource"]
    path = parsed.path.rstrip("/")
    if path:
        candidates.append(f"{root}/.well-known/oauth-protected-resource{path}")
    return candidates


def normalize_auth_metadata(
    metadata: Mapping[str, object] | None = None,
    *,
    protected_resource_metadata_url: str | None = None,
    authorization_server_metadata_url: str | None = None,
    oidc_issuer_url: str | None = None,
    oidc_discovery_url: str | None = None,
    client_id_metadata_document_url: str | None = None,
    declared_scopes: list[str] | None = None,
    granted_scopes: list[str] | None = None,
    missing_scopes: list[str] | None = None,
    diagnostics: list[str] | None = None,
) -> AuthMetadataInfo:
    """Normalize untrusted authorization metadata into PMCP's public shape."""
    metadata = metadata or {}
    scopes_supported = metadata.get("scopes_supported")
    scope_text = metadata.get("scope")
    inferred_scopes: list[str] = []
    if isinstance(scopes_supported, list):
        inferred_scopes = [s for s in scopes_supported if isinstance(s, str)]
    elif isinstance(scope_text, str):
        inferred_scopes = [s for s in scope_text.split() if s]

    issuer = metadata.get("issuer")
    authorization_servers = metadata.get("authorization_servers")
    auth_server_url = authorization_server_metadata_url
    if not auth_server_url and isinstance(authorization_servers, list):
        auth_server_url = next(
            (s for s in authorization_servers if isinstance(s, str)), None
        )

    client_id_doc = client_id_metadata_document_url
    if not client_id_doc and isinstance(
        metadata.get("client_id_metadata_document"), str
    ):
        client_id_doc = str(metadata["client_id_metadata_document"])

    issuer_url = oidc_issuer_url or issuer

    return AuthMetadataInfo(
        protected_resource_metadata_url=redact_auth_url(protected_resource_metadata_url)
        if protected_resource_metadata_url
        else None,
        authorization_server_metadata_url=redact_auth_url(auth_server_url)
        if auth_server_url
        else None,
        oidc_issuer_url=redact_auth_url(issuer_url)
        if isinstance(issuer_url, str)
        else None,
        oidc_discovery_url=redact_auth_url(oidc_discovery_url)
        if oidc_discovery_url
        else None,
        client_id_metadata_document_url=redact_auth_url(client_id_doc)
        if client_id_doc
        else None,
        declared_scopes=list(
            dict.fromkeys([*(declared_scopes or []), *inferred_scopes])
        ),
        granted_scopes=granted_scopes or [],
        missing_scopes=missing_scopes or [],
        diagnostics=[sanitize_auth_diagnostic(d) for d in (diagnostics or [])],
    )


def parse_url_elicitation_error(payload: object) -> list[UrlElicitationInfo]:
    """Parse JSON-RPC URLElicitationRequiredError payloads."""
    if isinstance(payload, BaseException):
        payload = payload.args[0] if payload.args else str(payload)
    if isinstance(payload, str):
        payload_text = payload
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\})", payload_text)
            if not match:
                return []
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                return []
    if not isinstance(payload, Mapping):
        return []

    error = payload.get("error", payload)
    if not isinstance(error, Mapping) or error.get("code") != -32042:
        return []
    data = error.get("data")
    if not isinstance(data, Mapping):
        data = error

    entries = data.get("elicitations")
    if not isinstance(entries, list):
        entries = [data]

    parsed_entries: list[UrlElicitationInfo] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        elicitation_id = entry.get("elicitationId") or entry.get("elicitation_id")
        url = entry.get("url")
        if not isinstance(elicitation_id, str) or not isinstance(url, str):
            continue
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"https", "http"} or not parsed_url.netloc:
            continue
        message = entry.get("message")
        parsed_entries.append(
            UrlElicitationInfo(
                elicitation_id=elicitation_id,
                url=redact_auth_url(url),
                message=message if isinstance(message, str) else None,
                next_step=(
                    "Open the URL out of band, complete consent, then call "
                    f"gateway.auth_connect(auth_mode='url_elicitation', "
                    f"server_name='<server>', elicitation_id='{elicitation_id}', "
                    "consent_acknowledged=true)."
                ),
            )
        )
    return parsed_entries


def fetch_json_metadata(
    url: str, *, timeout: float = 5.0
) -> tuple[dict[str, object] | None, str | None]:
    """Fetch public auth metadata without forwarding credentials."""
    safe_url = redact_auth_url(url)
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            body = response.read(1024 * 256)
        if "json" not in content_type.lower():
            return None, f"{safe_url} returned non-JSON content"
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            return None, f"{safe_url} returned JSON that was not an object"
        return data, None
    except Exception as exc:
        return None, f"{safe_url}: {sanitize_auth_diagnostic(exc)}"
