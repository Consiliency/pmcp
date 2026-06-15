"""Authorization metadata, elicitation, and redaction helpers."""

from __future__ import annotations

import json
import re
import time
import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse, urlunparse
from urllib.request import Request, urlopen

import aiohttp
import jwt
from jwt import PyJWKSet

from pmcp.types import AuthChallengeInfo, AuthMetadataInfo, UrlElicitationInfo

AUTH_SECRET_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "auth_code",
    "authorization",
    "bearer",
    "client_secret",
    "code",
    "id_token",
    "assertion",
    "key",
    "password",
    "refresh_token",
    "saml",
    "secret",
    "session",
    "sid",
    "ticket",
    "token",
    "jwt",
}

AUTH_DIAGNOSTIC_SECRET_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "assertion",
    "client_secret",
    "code",
    "cookie",
    "id_token",
    "jwt",
    "password",
    "refresh_token",
    "saml",
    "secret",
    "session",
    "set-cookie",
    "sid",
    "tenant-id",
    "tenant_id",
    "token",
}

_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r"(?![A-Za-z0-9_-])"
)


def redact_auth_url(url: str) -> str:
    """Strip URL userinfo and redact auth-bearing query values."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return str(url).split("#", 1)[0][:400]
    netloc = parsed.hostname or ""
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port:
        netloc = f"{netloc}:{port}"
    query_parts = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in AUTH_SECRET_QUERY_KEYS:
            query_parts.append((key, "[REDACTED]"))
        else:
            query_parts.append((key, value))
    query = "&".join(f"{quote(k)}={quote(v)}" for k, v in query_parts)
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, query, ""))


def _is_loopback_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_public_auth_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return False
    try:
        address = ip_address(hostname)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
    )


def sanitize_public_auth_url(url: str, *, allow_loopback_http: bool = False) -> str:
    """Validate and redact a public absolute auth metadata or elicitation URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid public auth URL.") from exc

    if parsed.scheme not in {"https", "http"} or not parsed.netloc or not hostname:
        raise ValueError("Public auth URL must be an absolute HTTP(S) URL.")

    if parsed.scheme == "http" and (
        not allow_loopback_http or not _is_loopback_host(hostname)
    ):
        raise ValueError("Public auth URL only allows http:// URLs for loopback hosts.")
    if not (
        allow_loopback_http and parsed.scheme == "http" and _is_loopback_host(hostname)
    ):
        if not _is_public_auth_host(hostname):
            raise ValueError("Public auth URL host must be public.")

    return redact_auth_url(url)


@dataclass(frozen=True)
class ResourceServerTokenClaims:
    """Validated non-secret Resource Server token claims."""

    issuer: str
    subject: str | None
    audience: list[str]
    scopes: list[str]
    claims: Mapping[str, Any]


class ResourceServerAuthError(Exception):
    """Raised for failed Resource Server token validation."""

    def __init__(self, error: str, description: str) -> None:
        self.error = error
        self.description = sanitize_auth_diagnostic(description)
        super().__init__(self.description)


class ResourceServerJWKSUnavailable(ResourceServerAuthError):
    """Raised when Resource Server JWKS cannot be fetched."""

    def __init__(self, description: str) -> None:
        super().__init__("temporarily_unavailable", description)


class AsyncJWKS:
    """Async TTL-cached JWKS fetcher for Resource Server token validation."""

    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: float = 300,
        max_bytes: int = 512 * 1024,
    ) -> None:
        self.url = sanitize_public_auth_url(url)
        self._raw_url = url
        self._ttl_seconds = ttl_seconds
        self._max_bytes = max_bytes
        self._lock: asyncio.Lock | None = None
        self._jwks: Mapping[str, Any] | None = None
        self._expires_at = 0.0

    @property
    def _refresh_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def get(self, *, force_refresh: bool = False) -> Mapping[str, Any]:
        now = time.monotonic()
        if not force_refresh and self._jwks is not None and now < self._expires_at:
            return self._jwks
        async with self._refresh_lock:
            now = time.monotonic()
            if not force_refresh and self._jwks is not None and now < self._expires_at:
                return self._jwks
            jwks = await self._fetch()
            self._jwks = jwks
            self._expires_at = time.monotonic() + self._ttl_seconds
            return jwks

    async def get_for_token(self, token: str) -> Mapping[str, Any]:
        jwks = await self.get()
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.InvalidTokenError:
            return jwks
        if isinstance(kid, str) and not _jwks_has_kid(jwks, kid):
            jwks = await self.get(force_refresh=True)
        return jwks

    async def _fetch(self) -> Mapping[str, Any]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self._raw_url) as response:
                    response.raise_for_status()
                    content = await response.content.read(self._max_bytes + 1)
        except Exception as exc:
            raise ResourceServerJWKSUnavailable(
                f"JWKS fetch failed for {self.url}."
            ) from exc
        if len(content) > self._max_bytes:
            raise ResourceServerJWKSUnavailable(
                f"JWKS response too large for {self.url}."
            )
        try:
            jwks = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResourceServerJWKSUnavailable(
                f"Invalid JWKS JSON from {self.url}."
            ) from exc
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise ResourceServerJWKSUnavailable(f"Invalid JWKS object from {self.url}.")
        return jwks


def _jwks_has_kid(jwks: Mapping[str, Any], kid: str) -> bool:
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        return False
    return any(isinstance(key, Mapping) and key.get("kid") == kid for key in keys)


def _select_jwk_key(token: str, jwks: Mapping[str, Any]) -> Any:
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    key_set = PyJWKSet.from_dict(dict(jwks))
    keys = key_set.keys
    if kid:
        for key in keys:
            if key.key_id == kid:
                return key.key
    if len(keys) == 1:
        return keys[0].key
    raise ResourceServerAuthError("invalid_token", "No matching JWK found.")


def _claim_scopes(claims: Mapping[str, Any]) -> list[str]:
    raw_scope = claims.get("scope")
    scopes: set[str] = set()
    if isinstance(raw_scope, str):
        scopes.update(part for part in raw_scope.split() if part)
    raw_scp = claims.get("scp")
    if isinstance(raw_scp, list):
        scopes.update(part for part in raw_scp if isinstance(part, str) and part)
    return sorted(scopes)


def validate_resource_server_token(
    token: str,
    *,
    issuer: str,
    audience: str,
    required_scopes: list[str] | None = None,
    jwks: Mapping[str, Any] | None = None,
    allowed_algorithms: tuple[str, ...] = ("RS256", "ES256"),
) -> ResourceServerTokenClaims:
    """Validate an AS-issued JWT for PMCP Resource Server mode."""
    if not token:
        raise ResourceServerAuthError("invalid_token", "Missing bearer token.")
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if not isinstance(algorithm, str) or algorithm.lower() == "none":
            raise ResourceServerAuthError(
                "invalid_token", "Unsupported token algorithm."
            )
        if jwks is None:
            raise ResourceServerAuthError("invalid_token", "JWKS URL is required.")
        signing_key = _select_jwk_key(token, jwks)
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=list(allowed_algorithms),
            audience=audience,
            issuer=issuer,
            options={"require": ["iss", "exp", "nbf", "aud"]},
        )
    except ResourceServerAuthError:
        raise
    except jwt.InvalidAudienceError as exc:
        raise ResourceServerAuthError("invalid_token", "Invalid audience.") from exc
    except jwt.InvalidTokenError as exc:
        raise ResourceServerAuthError("invalid_token", str(exc)) from exc

    scopes = _claim_scopes(claims)
    missing_scopes = sorted(set(required_scopes or []) - set(scopes))
    if missing_scopes:
        raise ResourceServerAuthError(
            "insufficient_scope",
            "Missing required scope(s): " + " ".join(missing_scopes),
        )
    raw_audience = claims.get("aud")
    audiences = raw_audience if isinstance(raw_audience, list) else [raw_audience]
    return ResourceServerTokenClaims(
        issuer=str(claims.get("iss", "")),
        subject=claims.get("sub") if isinstance(claims.get("sub"), str) else None,
        audience=[str(value) for value in audiences if value],
        scopes=scopes,
        claims=claims,
    )


def sanitize_url_elicitation_url(url: str) -> str:
    """Validate and redact a URL-mode elicitation URL."""
    try:
        return sanitize_public_auth_url(url, allow_loopback_http=True)
    except ValueError as exc:
        raise ValueError("Invalid URL-mode elicitation URL.") from exc


def sanitize_auth_diagnostic(value: object, *, max_length: int | None = 400) -> str:
    """Return a display-safe diagnostic string for auth failures."""
    text = str(value)

    def redact_url_match(match: re.Match[str]) -> str:
        raw_url = match.group(0)
        suffix = ""
        while raw_url and raw_url[-1] in ").,;":
            suffix = raw_url[-1] + suffix
            raw_url = raw_url[:-1]
        return redact_auth_url(raw_url) + suffix

    text = re.sub(r"https?://[^\s\"'<>]+", redact_url_match, text)
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)(\bbearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    secret_keys = "|".join(
        [
            *[re.escape(key) for key in AUTH_DIAGNOSTIC_SECRET_KEYS],
            r"api[_-]?key",
        ]
    )
    text = re.sub(
        rf"(?i)\b([A-Za-z0-9_-]*(?:{secret_keys})[A-Za-z0-9_-]*)"
        r"([\s:=]+)([A-Za-z0-9._~+/=-]{3,})",
        r"\1\2[REDACTED]",
        text,
    )
    text = _JWT_RE.sub("[REDACTED]", text)
    return text if max_length is None else text[:max_length]


def _parse_www_auth_params(raw: str) -> dict[str, str]:
    params: dict[str, str] = {}
    index = 0
    length = len(raw)
    while index < length:
        while index < length and raw[index] in " \t,":
            index += 1
        key_start = index
        while index < length and (raw[index].isalnum() or raw[index] in "_-"):
            index += 1
        key = raw[key_start:index].lower()
        while index < length and raw[index].isspace():
            index += 1
        if not key or index >= length or raw[index] != "=":
            break
        index += 1
        while index < length and raw[index].isspace():
            index += 1
        if index < length and raw[index] == '"':
            index += 1
            value_parts: list[str] = []
            while index < length:
                char = raw[index]
                if char == "\\" and index + 1 < length:
                    value_parts.append(raw[index + 1])
                    index += 2
                    continue
                if char == '"':
                    index += 1
                    break
                value_parts.append(char)
                index += 1
            params[key] = "".join(value_parts)
        else:
            value_start = index
            while index < length and raw[index] not in ", \t":
                index += 1
            params[key] = raw[value_start:index]
        while index < length and raw[index] not in ",":
            index += 1
    return params


def parse_www_authenticate(header: str) -> AuthChallengeInfo | None:
    """Parse a WWW-Authenticate challenge for non-secret MCP auth hints."""
    if not header.strip():
        return None

    # Split only on commas that start another auth scheme or parameter boundary.
    challenge = header.strip()
    scheme, _, rest = challenge.partition(" ")
    if not scheme:
        return None

    params = _parse_www_auth_params(rest)

    scope = params.get("scope")
    missing = params.get("missing_scope") or scope or ""
    missing_scopes = [part for part in missing.split() if part]
    resource_metadata_url = None
    if params.get("resource_metadata"):
        try:
            resource_metadata_url = sanitize_public_auth_url(
                params["resource_metadata"]
            )
        except ValueError:
            resource_metadata_url = None

    return AuthChallengeInfo(
        scheme=scheme,
        resource_metadata_url=resource_metadata_url,
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
    normalized_diagnostics = [sanitize_auth_diagnostic(d) for d in (diagnostics or [])]

    def public_url(raw_url: str | None, field_name: str) -> str | None:
        if not raw_url:
            return None
        try:
            return sanitize_public_auth_url(raw_url)
        except ValueError as exc:
            normalized_diagnostics.append(
                f"{field_name} ignored: {sanitize_auth_diagnostic(exc)} "
                f"({redact_auth_url(raw_url)})"
            )
            return None

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
        protected_resource_metadata_url=public_url(
            protected_resource_metadata_url, "protected_resource_metadata_url"
        ),
        authorization_server_metadata_url=public_url(
            auth_server_url, "authorization_server_metadata_url"
        ),
        oidc_issuer_url=public_url(issuer_url, "oidc_issuer_url")
        if isinstance(issuer_url, str)
        else None,
        oidc_discovery_url=public_url(oidc_discovery_url, "oidc_discovery_url"),
        client_id_metadata_document_url=public_url(
            client_id_doc, "client_id_metadata_document_url"
        ),
        declared_scopes=list(
            dict.fromkeys([*(declared_scopes or []), *inferred_scopes])
        ),
        granted_scopes=granted_scopes or [],
        missing_scopes=missing_scopes or [],
        diagnostics=normalized_diagnostics,
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
        try:
            safe_url = sanitize_url_elicitation_url(url)
        except ValueError:
            continue
        message = entry.get("message")
        parsed_entries.append(
            UrlElicitationInfo(
                elicitation_id=elicitation_id,
                url=safe_url,
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
    try:
        safe_url = sanitize_public_auth_url(url, allow_loopback_http=True)
    except ValueError as exc:
        return None, sanitize_auth_diagnostic(exc)
    try:
        request = Request(safe_url, headers={"Accept": "application/json"})
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
