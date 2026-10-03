"""Authorization metadata, elicitation, and redaction helpers."""

from __future__ import annotations

import json
import re
import string
import time
import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from itertools import product
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import aiohttp
import jwt
from jwt import PyJWKSet

from pmcp.keyword_matcher import key_start_pattern, redact_keyword_values
from pmcp.redaction_additive import redact_additive
from pmcp.types import AuthChallengeInfo, AuthMetadataInfo, UrlElicitationInfo


class AuthText(str):
    """One fixed operator-facing auth/HTTP message. Instances exist only as
    `AuthMessage` attributes (Consiliency/pmcp#326): the auth error classes
    and the HTTP 401/403/503 response helper refuse anything else, so a
    message cannot be written inline in a shape a review cannot see."""

    __slots__ = ()


_PYJWT_MINT = object()  # held only by `pyjwt_text`


class PyJwtText(str):
    """pyjwt's own text for an error class pmcp keeps verbatim
    (`_FIXED_TEXT_CLAIM_ERRORS`). Minted only by `pyjwt_text`: the
    constructor wants a private token, and `render_auth_message` refuses an
    instance that does not carry it (so `str.__new__(PyJwtText, ...)` does
    not get through either)."""

    _minted: object

    def __new__(cls, text: str, *, _mint: object = None) -> "PyJwtText":
        if _mint is not _PYJWT_MINT:
            raise TypeError("PyJwtText is minted only by pyjwt_text()")
        obj = super().__new__(cls, text)
        obj._minted = _PYJWT_MINT
        return obj


class AuthMessage:
    """The registry of every fixed operator-facing message that `pmcp.auth`
    and `pmcp.transport.http` raise or send as an auth rejection -- the auth
    error descriptions, the 401/403/503 bodies, the startup refusals and the
    redirect `HTTPError` (Consiliency/pmcp#326). Out of scope, as the plan's
    non-goals say: the metadata diagnostics lists, `UNVERIFIED_URL_CAVEAT`,
    `fetch_json_metadata`'s strings, the plain 413/429/504 bodies and the log
    templates.

    Each one comes through `sanitize_auth_diagnostic` -- the redactor's own
    rules and the additive rules (#234) -- unchanged; the test module checks
    every member. `{url}` and `{scopes}` are filled from configuration by the
    constructor that takes them (`ResourceServerAuthError(..., url=...)`).
    Add a message here, never inline: the auth error classes and
    `_auth_response` raise `TypeError` for anything that is not a member, and
    a static check in the test module requires every raise and `_reject`
    site to name one.
    """

    # -- token validation (401 `invalid_token` / 403 `insufficient_scope`) --
    # Four texts were reworded because the sanitiser rewrote them (#326):
    # "Missing bearer token." -> "Missing bearer [REDACTED]" (Bearer rule);
    # "Unsupported token algorithm." -> "Unsupported token [REDACTED]";
    # "Token could not be verified with the published key." -> "Token
    # [REDACTED] not be verified..." (keyword rule, `token <word>`); and
    # "shared-secret auth mode requires auth_token." -> "shared-secret
    # [REDACTED] mode..." (keyword rule, `secret <word>`).
    EMPTY_TOKEN = AuthText("Empty token.")
    TOKEN_ALGORITHM_UNSUPPORTED = AuthText("The token's algorithm is not supported.")
    JWKS_URL_REQUIRED = AuthText("JWKS URL is required.")
    KEY_CANNOT_VERIFY_TOKEN = AuthText("The published key cannot verify this token.")
    NO_MATCHING_JWK = AuthText("No matching JWK found.")
    INVALID_AUDIENCE = AuthText("Invalid audience.")
    INVALID_TOKEN = AuthText("Invalid token.")
    MISSING_SCOPES = AuthText("Missing required scope(s): {scopes}")
    RS_JWKS_NOT_CONFIGURED = AuthText("Resource Server JWKS is not configured.")
    # -- JWKS availability (503 `temporarily_unavailable`) --
    JWKS_BACKING_OFF = AuthText("JWKS refresh recently failed for {url}; backing off.")
    JWKS_FETCH_FAILED = AuthText("JWKS fetch failed for {url}.")
    JWKS_REDIRECT_REFUSED = AuthText(
        "JWKS endpoint returned a redirect for {url}; refusing to follow."
    )
    JWKS_TOO_LARGE = AuthText("JWKS response too large for {url}.")
    JWKS_INVALID_JSON = AuthText("Invalid JWKS JSON from {url}.")
    JWKS_INVALID_OBJECT = AuthText("Invalid JWKS object from {url}.")
    JWKS_NO_USABLE_KEYS = AuthText("JWKS contains no usable signing keys.")
    # -- public auth URL validation --
    REDIRECTS_NOT_ALLOWED = AuthText("Redirects are not allowed.")
    PUBLIC_URL_INVALID = AuthText("Invalid public auth URL.")
    PUBLIC_URL_NOT_ABSOLUTE = AuthText(
        "Public auth URL must be an absolute HTTP(S) URL."
    )
    PUBLIC_URL_HTTP_LOOPBACK_ONLY = AuthText(
        "Public auth URL only allows http:// URLs for loopback hosts."
    )
    PUBLIC_URL_NOT_PUBLIC = AuthText(
        "Public auth URL host is a non-public IP literal or loopback name."
    )
    ELICITATION_URL_INVALID = AuthText("Invalid URL-mode elicitation URL.")
    # -- HTTP transport startup refusals --
    UNSUPPORTED_AUTH_MODE = AuthText("Unsupported auth mode.")
    SHARED_SECRET_NEEDS_TOKEN = AuthText(
        "auth_token is required when auth_mode is shared-secret."
    )
    RESOURCE_SERVER_NEEDS_CONFIG = AuthText(
        "resource-server auth mode requires issuer, JWKS URL, and audience."
    )
    JWKS_URL_NOT_USABLE = AuthText(
        "The JWKS URL must be an absolute http(s) URL without whitespace."
    )
    METADATA_URL_NOT_USABLE = AuthText(
        "The protected-resource metadata URL must be an absolute http(s) URL "
        "without whitespace."
    )
    REQUIRED_SCOPE_INVALID = AuthText(
        "Each required scope must be a single RFC 6749 scope (printable ASCII, "
        "no space, quote or backslash)."
    )
    METADATA_NEEDS_RESOURCE = AuthText(
        "Protected-resource metadata needs a canonical resource: set "
        "resource_server_audience (--oauth-audience) or an absolute "
        "protected_resource_metadata_url."
    )
    # -- HTTP 401/403/503 response bodies (`_auth_response`) --
    UNAUTHORIZED = AuthText("Unauthorized")
    FORBIDDEN = AuthText("Forbidden")
    SERVICE_UNAVAILABLE = AuthText("Service Unavailable")


def auth_messages() -> dict[str, AuthText]:
    """Every registry member, by name."""
    return {
        name: value
        for name, value in vars(AuthMessage).items()
        if isinstance(value, AuthText)
    }


# Membership is identity, not type: an `AuthText` minted anywhere else --
# `AuthText("...")` or `str.__new__(AuthText, ...)` -- is not a member.
_MEMBER_IDS = frozenset(id(value) for value in auth_messages().values())
# Each member's placeholder names, which its fields must match exactly.
_MEMBER_FIELDS = {
    id(value): frozenset(f for _, f, _, _ in string.Formatter().parse(value) if f)
    for value in auth_messages().values()
}
# Note: `_MEMBER_IDS` holds the identities of THIS import's members. A test
# that `importlib.reload`s `pmcp.auth` must reload `pmcp.transport.http` too,
# or http.py keeps passing the old registry's members and every auth
# response is refused.

# RFC 6749 section 3.3: scope = scope-token *( SP scope-token ),
# scope-token = 1*NQCHAR, NQCHAR = %x21 / %x23-5B / %x5D-7E.
_SCOPE_LIST = re.compile(r"[\x21\x23-\x5B\x5D-\x7E]+(?: [\x21\x23-\x5B\x5D-\x7E]+)*")


def _is_absolute_http(parsed: Any) -> bool:
    """The absolute-HTTP(S) rule `sanitize_public_auth_url` applies to every
    configured auth URL: an http(s) scheme, a netloc and a hostname."""
    return (
        parsed.scheme in {"https", "http"}
        and bool(parsed.netloc)
        and bool(parsed.hostname)
    )


def _url_field(value: object) -> bool:
    """A `{url}` field: an absolute http(s) URL with no whitespace -- what a
    configured JWKS URL is. Prose ("Token expired.") is not."""
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlparse(value)
        _ = parsed.port
    except ValueError:
        return False
    return _is_absolute_http(parsed)


def _scopes_field(value: object) -> bool:
    """A `{scopes}` field: an RFC 6749 scope list (space-separated
    scope-tokens), what `required_scopes` minus the token's scopes is."""
    return isinstance(value, str) and _SCOPE_LIST.fullmatch(value) is not None


# Every placeholder the registry uses, and the shape its value must have.
# Code-introduced prose in a field fails here at construction, wherever the
# value came from (a literal, a variable, a constant).
_FIELD_VALIDATORS: dict[str, Callable[[object], bool]] = {
    "url": _url_field,
    "scopes": _scopes_field,
}


def _stored_url_ok(url: str) -> bool:
    """True if `url`, sanitised the way every caller stores it, has the
    renderer's `{url}` shape. A value `sanitize_public_auth_url` refuses
    raises ITS registry message (non-public IP literal, plain http, not
    absolute ...), which is more specific than ours (round 9, claude N1)."""
    return _url_field(sanitize_public_auth_url(url))


def check_auth_config(
    *,
    jwks_url: str | None = None,
    metadata_url: str | None = None,
    required_scopes: Iterable[str] | None = None,
) -> None:
    """Refuse at startup any configuration value that a rejection would later
    have to carry in a registry field, if it lacks that field's shape -- with
    the SAME validators the renderer applies (Consiliency/pmcp#326). Without
    this, a JWKS URL with a raw space or a required scope such as `a"b`
    passed startup and made every rejection that names it fail to build (a
    500 instead of the 503/403). One validator, two callers: here, and
    `render_auth_message`.

    A URL is checked as it will be *stored*: `sanitize_public_auth_url`
    first (as `AsyncJWKS` and `normalize_auth_metadata` do), so a value they
    would clean -- a trailing newline from a file-backed secret, a leading
    space -- starts as it does on main, and a value they would refuse or
    silently drop (relative, non-http(s), a non-public IP literal) is
    refused here instead (round 8)."""
    if jwks_url is not None and not _stored_url_ok(jwks_url):
        raise ValueError(render_auth_message(AuthMessage.JWKS_URL_NOT_USABLE))
    if metadata_url is not None and not _stored_url_ok(metadata_url):
        raise ValueError(render_auth_message(AuthMessage.METADATA_URL_NOT_USABLE))
    for scope in required_scopes or ():
        if not _scopes_field(scope) or " " in scope:
            raise ValueError(render_auth_message(AuthMessage.REQUIRED_SCOPE_INVALID))


def _check_registry_fields() -> None:
    """Fail at import if a registry member uses a placeholder that has no
    validator: every field's shape must be checked."""
    unvalidated = set().union(*_MEMBER_FIELDS.values()) - set(_FIELD_VALIDATORS)
    if unvalidated:  # pragma: no cover - a registry edit without a validator
        raise TypeError(f"AuthMessage placeholders without a validator: {unvalidated}")


_check_registry_fields()


def pyjwt_text(exc: BaseException) -> PyJwtText:
    """The one narrow pass-through: pyjwt's text for a class in
    `_FIXED_TEXT_CLAIM_ERRORS`, whose texts are fixed (the test module
    generates and checks every one). Anything else is a `TypeError`."""
    if not isinstance(exc, _FIXED_TEXT_CLAIM_ERRORS):
        raise TypeError(
            f"pyjwt_text() takes a fixed-text pyjwt error, not {type(exc).__name__}"
        )
    return PyJwtText(str(exc), _mint=_PYJWT_MINT)


def render_auth_message(message: AuthText | PyJwtText, **fields: str) -> str:
    """The text of a registry member with its configuration fields filled,
    or a pyjwt pass-through. `TypeError` for anything else: a non-member
    (checked by identity), an unminted `PyJwtText`, or fields that are not
    exactly the member's placeholders -- a missing field would publish a
    literal `{url}`, a misnamed one would raise `KeyError` deep in a fetch."""
    if isinstance(message, PyJwtText):
        if getattr(message, "_minted", None) is not _PYJWT_MINT or fields:
            raise TypeError("PyJwtText must come from pyjwt_text(), with no fields")
        return str(message)
    if id(message) not in _MEMBER_IDS:
        raise TypeError(
            f"auth messages must be an AuthMessage member, not {type(message).__name__}"
        )
    expected = _MEMBER_FIELDS[id(message)]
    if set(fields) != expected:
        raise TypeError(
            f"AuthMessage fields must be exactly {sorted(expected)}, "
            f"got {sorted(fields)}"
        )
    bad = sorted(k for k, v in fields.items() if not _FIELD_VALIDATORS[k](v))
    if bad:
        raise TypeError(f"AuthMessage field(s) {bad} do not have the expected shape")
    return message.format(**fields)


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse HTTP redirects so a public URL cannot 3xx to an internal host."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        raise HTTPError(
            newurl,
            code,
            render_auth_message(AuthMessage.REDIRECTS_NOT_ALLOWED),
            headers,
            fp,
        )


_NO_REDIRECT_OPENER = build_opener(_NoRedirectHandler)
# Metadata fetches go through a no-redirect opener; tests patch this name.
urlopen = _NO_REDIRECT_OPENER.open

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

#: Where a secret key starts, for the keyword rule in `sanitize_auth_diagnostic`.
_KEYWORD_KEY_START = key_start_pattern(AUTH_DIAGNOSTIC_SECRET_KEYS)

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


# Every IPv6 format that carries an IPv4 address in its low 32 bits. The set is
# closed and RFC-specified, so enumerating it is defensible -- but the list must
# not live only here. The matrix in tests/test_auth.py names each format with its
# RFC so that a seventh format is a visible gap rather than a silent one.
_V4_EMBEDDING_NETWORKS = (
    ip_network("::ffff:0:0/96"),  # RFC 4291 IPv4-mapped
    ip_network("::/96"),  # RFC 4291 IPv4-compatible (deprecated)
    ip_network("64:ff9b::/96"),  # RFC 6052 NAT64 well-known prefix
)
# RFC 5214 §6.1: an ISATAP interface identifier is the full 32 bits
# `00-00-5E-FE` -- or `02-00-5E-FE` with the u/g bit set -- immediately followed
# by the IPv4 address in the low 32 bits. Matching only the `5efe` hextet is not
# enough to identify ISATAP: `2606:4700::1234:5efe:a00:5` is an ordinary global
# address that merely happens to carry `5efe` there, and unwrapping it would
# reject a genuinely public host.
# RFC 3056 6to4 (2002::/16) and RFC 4380 Teredo (2001::/32) need no unwrapping
# because neither prefix is global.
_ISATAP_INTERFACE_IDS = (0x00005EFE, 0x02005EFE)

# inet_aton part grammar. A part is hex, octal, or decimal; a leading zero is
# read as octal by glibc but as decimal by stricter resolvers, so both readings
# are produced and the host is rejected if either one is non-public.
_HEX_PART = re.compile(r"0[xX][0-9a-fA-F]+")
_OCTAL_PART = re.compile(r"0[0-7]*")
_DECIMAL_PART = re.compile(r"[0-9]+")


def _unwrap_embedded_v4(
    address: IPv4Address | IPv6Address,
) -> IPv4Address | IPv6Address:
    """Return the IPv4 address an IPv6 literal embeds, or the address unchanged."""
    if isinstance(address, IPv6Address):
        for network in _V4_EMBEDDING_NETWORKS:
            if address in network:
                return IPv4Address(int(address) & 0xFFFFFFFF)
        if ((int(address) >> 32) & 0xFFFFFFFF) in _ISATAP_INTERFACE_IDS:
            return IPv4Address(int(address) & 0xFFFFFFFF)
    return address


def _is_public_ip(address: IPv4Address | IPv6Address) -> bool:
    """Classify an address positively, after unwrapping any embedded IPv4.

    Classifying positively (what *is* public) rather than subtracting a list of
    bad properties is deliberate: the subtractive form missed RFC 6598 CGNAT and
    RFC 3879 site-local. `is_global` alone is not enough -- it is True for
    ``fec0::1`` and, on Python 3.10, for multicast.
    """
    unwrapped = _unwrap_embedded_v4(address)
    return bool(
        unwrapped.is_global
        and not getattr(unwrapped, "is_site_local", False)
        and not unwrapped.is_multicast
    )


def _numeric_part_values(part: str) -> set[int]:
    """Every integer a resolver could plausibly read one inet_aton part as."""
    if _HEX_PART.fullmatch(part):
        return {int(part, 16)}
    values: set[int] = set()
    if _DECIMAL_PART.fullmatch(part):
        values.add(int(part, 10))
    if _OCTAL_PART.fullmatch(part):
        values.add(int(part, 8))
    return values


def _legacy_numeric_addresses(hostname: str) -> set[IPv4Address]:
    """Read a host as a legacy numeric IPv4 literal, without resolving anything.

    ``ip_address()`` rejects the inet_aton forms that every stock resolver still
    accepts, so ``2852039166``, ``0xA9FEA9FE`` and ``0177.0.0.1`` reach
    169.254.169.254 and 127.0.0.1 with no DNS lookup involved. Treating "did not
    parse" as "must be a DNS name" therefore hands a literal the benefit of the
    doubt owed only to a name.

    Returns every reading; an empty set means the host is genuinely not numeric
    and belongs on the name path.
    """
    parts = hostname.split(".")
    if not 1 <= len(parts) <= 4:
        return set()
    readings: list[list[int]] = []
    for part in parts:
        values = _numeric_part_values(part)
        if not values:
            return set()
        readings.append(sorted(values))

    addresses: set[IPv4Address] = set()
    for combination in product(*readings):
        # inet_aton: every leading part is one byte and the final part fills the
        # remaining low bytes, so `169.254.43518` and `5` are addresses too.
        *head, tail = combination
        if any(value > 0xFF for value in head):
            continue
        if tail >= 1 << (8 * (4 - len(head))):
            continue
        packed = tail
        for index, value in enumerate(head):
            packed |= value << (8 * (3 - index))
        addresses.add(IPv4Address(packed))
    return addresses


def _is_public_auth_host(hostname: str) -> bool:
    """Report whether an auth URL's host is a public IP literal or a DNS name.

    Only IP literals are actually classified. **A DNS name is accepted without
    being resolved**, so this function cannot tell that ``metadata.example.com``
    points at 169.254.169.254. That limitation is real and tracked by #211; it is
    stated here rather than implied because the caller's error message used to
    claim more than this check performs.

    Legacy numeric forms are *not* names: anything readable as a number is
    canonicalised and classified as the literal it is.
    """
    if hostname.lower() == "localhost":
        return False
    # Only the parse is guarded: a ValueError escaping the classifier would
    # otherwise be misread as "not a literal" and fail open.
    address: IPv4Address | IPv6Address | None
    try:
        address = ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        return _is_public_ip(address)
    numeric = _legacy_numeric_addresses(hostname)
    if numeric:
        return all(_is_public_ip(address) for address in numeric)
    return True


def _is_verified_public_auth_host(hostname: str) -> bool:
    """Report whether PMCP itself classified this host as a public literal.

    This is the honest half of :func:`_is_public_auth_host`. That function
    returns True for a DNS name, because rejecting unresolvable names would
    refuse every legitimate ``auth.vendor.com``. But "not rejected" is not
    "checked", and presenting the two identically is the vouch #211 reports.

    A name therefore returns **False** here: PMCP does not resolve names -- a
    deliberate non-goal, since a lookup is TOCTOU-vulnerable and is not SSRF
    defence without connection-time IP pinning -- so it does not know where one
    points and must not imply that it does.
    """
    if hostname.lower() == "localhost":
        return False
    address: IPv4Address | IPv6Address | None
    try:
        address = ip_address(hostname)
    except ValueError:
        address = None
    if address is not None:
        return _is_public_ip(address)
    numeric = _legacy_numeric_addresses(hostname)
    if numeric:
        return all(_is_public_ip(candidate) for candidate in numeric)
    return False


def is_verified_public_auth_url(url: str) -> bool:
    """Report whether PMCP verified where an auth URL points.

    True only for an absolute ``https://`` URL whose host PMCP classified as a
    public IP literal. False for every DNS name, every non-public literal, and
    every ``http://`` URL -- a cleartext hop's destination is not authenticated
    even when the address is public.

    Callers use this to decide what to *claim*, not what to accept: a relay path
    keeps passing an unverified URL through, it just stops vouching for it.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.netloc or not hostname:
        return False
    return _is_verified_public_auth_host(hostname)


# One provenance-neutral sentence, used on both the downstream relay path and
# the operator acknowledgement path. The acknowledge path echoes a URL the
# operator typed, so the copy must not attribute it to "the server".
UNVERIFIED_URL_CAVEAT = (
    "PMCP has not verified where this URL points -- it checks the URL's form "
    "and rejects private address literals, but it does not resolve host names. "
    "Confirm the destination yourself before opening it."
)


def sanitize_public_auth_url(url: str, *, allow_loopback_http: bool = False) -> str:
    """Validate and redact a public absolute auth metadata or elicitation URL."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_INVALID)) from exc

    if not _is_absolute_http(parsed) or not hostname:
        raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_NOT_ABSOLUTE))

    if parsed.scheme == "http" and (
        not allow_loopback_http or not _is_loopback_host(hostname)
    ):
        raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_HTTP_LOOPBACK_ONLY))
    if not (
        allow_loopback_http and parsed.scheme == "http" and _is_loopback_host(hostname)
    ):
        if not _is_public_auth_host(hostname):
            raise ValueError(render_auth_message(AuthMessage.PUBLIC_URL_NOT_PUBLIC))

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
    """Raised for failed Resource Server token validation.

    ``description`` must be an `AuthMessage` member (with its ``url`` /
    ``scopes`` fields as keywords) or a `pyjwt_text` pass-through; anything
    else is a `TypeError` at construction (Consiliency/pmcp#326), which a
    subclass cannot avoid either -- whatever it passes up arrives here.
    """

    def __init__(
        self, error: str, description: AuthText | PyJwtText, **fields: str
    ) -> None:
        self.error = error
        self.description = sanitize_auth_diagnostic(
            render_auth_message(description, **fields)
        )
        super().__init__(self.description)


class ResourceServerJWKSUnavailable(ResourceServerAuthError):
    """Raised when Resource Server JWKS cannot be fetched."""

    def __init__(self, description: AuthText, **fields: str) -> None:
        super().__init__("temporarily_unavailable", description, **fields)


class AsyncJWKS:
    """Async TTL-cached JWKS fetcher for Resource Server token validation."""

    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: float = 300,
        max_bytes: int = 512 * 1024,
        forced_refresh_cooldown_seconds: float = 10.0,
        fetch_timeout_seconds: float = 5.0,
        refresh_failure_backoff_seconds: float = 5.0,
    ) -> None:
        self.url = sanitize_public_auth_url(url)
        # The display URL is what a 503 carries in `{url}`: refuse here, not
        # per request, a URL that could not be rendered (#326).
        check_auth_config(jwks_url=self.url)
        self._raw_url = url
        self._ttl_seconds = ttl_seconds
        self._max_bytes = max_bytes
        # S-07 (Consiliency/pmcp#231): at most one *forced* (unknown-kid) refresh
        # per window, so an unauthenticated caller sending random kids cannot
        # drive an outbound fetch per request.
        self._forced_refresh_cooldown_seconds = forced_refresh_cooldown_seconds
        # S-08: a total bound on one fetch (connect + headers + body) ...
        self._fetch_timeout_seconds = fetch_timeout_seconds
        # ... and a shared failure window, so the waiters queued behind a failed
        # fetch fail at once instead of each re-attempting it in turn.
        self._refresh_failure_backoff_seconds = refresh_failure_backoff_seconds
        self._last_forced_refresh: float = float("-inf")
        self._last_refresh_failure: float = float("-inf")
        self._lock: asyncio.Lock | None = None
        self._jwks: Mapping[str, Any] | None = None
        self._expires_at = 0.0

    @property
    def _refresh_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _cached(self, now: float) -> Mapping[str, Any] | None:
        """The cached JWKS while it is unexpired, else ``None``. Expired keys are
        never served."""
        if self._jwks is not None and now < self._expires_at:
            return self._jwks
        return None

    def _serve_cache_instead(
        self, now: float, *, force_refresh: bool
    ) -> Mapping[str, Any] | None:
        """The unexpired cache when it answers this call without a fetch --
        always for a plain call, and for a forced one while the cooldown is
        active -- else ``None``."""
        cached = self._cached(now)
        if cached is None or not force_refresh:
            return cached
        if now - self._last_forced_refresh < self._forced_refresh_cooldown_seconds:
            return cached
        return None

    async def get(self, *, force_refresh: bool = False) -> Mapping[str, Any]:
        now = time.monotonic()
        # Fast path only: keeps on-cooldown callers off the lock. Not
        # authoritative -- the stamp is written under the lock, so only a check
        # made under the lock is ordered against every stamp (S-07 concurrency).
        served = self._serve_cache_instead(now, force_refresh=force_refresh)
        if served is not None:
            return served
        async with self._refresh_lock:
            now = time.monotonic()
            # (1) Authoritative cache/cooldown re-check: a waiter that queued
            # before another forced caller stamped sees that stamp and its keys.
            served = self._serve_cache_instead(now, force_refresh=force_refresh)
            if served is not None:
                return served
            # (2) S-08 backoff gate: a refresh failed moments ago, so do not
            # stack another fetch behind it. Never stamps the forced-refresh
            # window -- nothing was fetched, so no cooldown is consumed.
            if now - self._last_refresh_failure < self._refresh_failure_backoff_seconds:
                cached = self._cached(now)
                if cached is not None:
                    return cached
                raise ResourceServerJWKSUnavailable(
                    AuthMessage.JWKS_BACKING_OFF, url=self.url
                )
            # (3) A fetch is about to be attempted: the only place the
            # forced-refresh window advances (success or failure alike).
            if force_refresh:
                self._last_forced_refresh = now
            # (4) Fetch; a failure opens the shared backoff window.
            try:
                jwks = await self._fetch()
            except ResourceServerJWKSUnavailable:
                self._last_refresh_failure = time.monotonic()
                raise
            except (TypeError, KeyError):
                # A programming error (e.g. a refused auth message), not an
                # endpoint failure: never a 503, never a backoff window
                # (Consiliency/pmcp#326).
                raise
            except Exception as exc:
                # Any other failure is still a failed refresh: it opens the
                # shared backoff and is the same value-free 503, never a 500
                # that each queued waiter re-earns with its own fetch.
                # Cancellation (a BaseException) is deliberately not caught:
                # it says the caller went away, not that the endpoint failed,
                # so it must not 503 everyone else for the backoff window. The
                # lock and cache are left as they were; the cooldown stamp
                # stays, because the attempt was made.
                self._last_refresh_failure = time.monotonic()
                raise ResourceServerJWKSUnavailable(
                    AuthMessage.JWKS_FETCH_FAILED, url=self.url
                ) from exc
            self._last_refresh_failure = float("-inf")
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
            # get() decides whether this forced refresh actually fetches (the
            # S-07 cooldown and the S-08 backoff both live there, under the lock).
            jwks = await self.get(force_refresh=True)
        return jwks

    async def _fetch(self) -> Mapping[str, Any]:
        timeout = aiohttp.ClientTimeout(total=self._fetch_timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    self._raw_url, allow_redirects=False
                ) as response:
                    if 300 <= response.status < 400:
                        raise ResourceServerJWKSUnavailable(
                            AuthMessage.JWKS_REDIRECT_REFUSED, url=self.url
                        )
                    response.raise_for_status()
                    content = await response.content.read(self._max_bytes + 1)
        except (ResourceServerJWKSUnavailable, TypeError, KeyError):
            # TypeError/KeyError: a programming error, not a fetch failure
            # (Consiliency/pmcp#326).
            raise
        except Exception as exc:
            raise ResourceServerJWKSUnavailable(
                AuthMessage.JWKS_FETCH_FAILED, url=self.url
            ) from exc
        if len(content) > self._max_bytes:
            raise ResourceServerJWKSUnavailable(
                AuthMessage.JWKS_TOO_LARGE, url=self.url
            )
        try:
            jwks = json.loads(content.decode("utf-8"))
        except (ValueError, RecursionError) as exc:
            # ValueError covers JSONDecodeError and UnicodeDecodeError; a
            # deeply nested body under the size cap raises RecursionError.
            raise ResourceServerJWKSUnavailable(
                AuthMessage.JWKS_INVALID_JSON, url=self.url
            ) from exc
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise ResourceServerJWKSUnavailable(
                AuthMessage.JWKS_INVALID_OBJECT, url=self.url
            )
        return jwks


def _jwks_has_kid(jwks: Mapping[str, Any], kid: str) -> bool:
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        return False
    return any(isinstance(key, Mapping) and key.get("kid") == kid for key in keys)


def _select_jwk_key(token: str, jwks: Mapping[str, Any]) -> Any:
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    try:
        key_set = PyJWKSet.from_dict(dict(jwks))
    except jwt.PyJWKSetError as exc:
        # An empty set, or one with no usable key, is a key-set availability
        # problem (503), not a bad token -- and PyJWKSetError is not an
        # InvalidTokenError, so unmapped it escaped as a 500 (see
        # Consiliency/pmcp#320). Fixed text: never echo pyjwt's message or any
        # JWKS content.
        raise ResourceServerJWKSUnavailable(AuthMessage.JWKS_NO_USABLE_KEYS) from exc
    keys = key_set.keys
    if kid:
        for key in keys:
            if key.key_id == kid:
                return key.key
    if len(keys) == 1:
        return keys[0].key
    raise ResourceServerAuthError("invalid_token", AuthMessage.NO_MATCHING_JWK)


def _claim_scopes(claims: Mapping[str, Any]) -> list[str]:
    raw_scope = claims.get("scope")
    scopes: set[str] = set()
    if isinstance(raw_scope, str):
        scopes.update(part for part in raw_scope.split() if part)
    raw_scp = claims.get("scp")
    if isinstance(raw_scp, list):
        scopes.update(part for part in raw_scp if isinstance(part, str) and part)
    return sorted(scopes)


# pyjwt claim-validation errors whose message is fixed text.
_FIXED_TEXT_CLAIM_ERRORS: tuple[type[jwt.InvalidTokenError], ...] = (
    jwt.ExpiredSignatureError,
    jwt.ImmatureSignatureError,
    jwt.InvalidIssuerError,
    jwt.InvalidIssuedAtError,
    jwt.MissingRequiredClaimError,
    jwt.InvalidAlgorithmError,
)


def _decode_with_key(
    token: str,
    signing_key: Any,
    *,
    algorithms: list[str],
    audience: str,
    issuer: str,
) -> dict[str, Any]:
    """``jwt.decode`` with key-preparation failures mapped to ``invalid_token``.

    The token's header picks the algorithm and its ``kid`` picks the key, so an
    unauthenticated caller can pair any allowed algorithm with any published
    key. When they do not fit, pyjwt/cryptography raise from key preparation or
    verification -- ``TypeError`` ("Expecting a PEM-formatted key."),
    ``ValueError``, or ``jwt.InvalidKeyError`` (a ``PyJWTError`` that is not an
    ``InvalidTokenError``) -- which escaped as a 500 (see
    Consiliency/pmcp#231, PR review F1). The ``try`` wraps only this one call,
    whose inputs are the attacker's token and a published key, so a ``TypeError``
    or ``ValueError`` from PMCP's own code elsewhere still surfaces.
    ``InvalidTokenError`` passes through for the caller's own mapping.
    """
    try:
        return jwt.decode(
            token,
            signing_key,
            algorithms=algorithms,
            audience=audience,
            issuer=issuer,
            options={"require": ["iss", "exp", "nbf", "aud"]},
        )
    except jwt.InvalidTokenError:
        raise
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise ResourceServerAuthError(
            "invalid_token", AuthMessage.KEY_CANNOT_VERIFY_TOKEN
        ) from exc


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
        raise ResourceServerAuthError("invalid_token", AuthMessage.EMPTY_TOKEN)
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if not isinstance(algorithm, str) or algorithm.lower() == "none":
            raise ResourceServerAuthError(
                "invalid_token", AuthMessage.TOKEN_ALGORITHM_UNSUPPORTED
            )
        if jwks is None:
            raise ResourceServerAuthError(
                "invalid_token", AuthMessage.JWKS_URL_REQUIRED
            )
        signing_key = _select_jwk_key(token, jwks)
        claims = _decode_with_key(
            token,
            signing_key,
            algorithms=list(allowed_algorithms),
            audience=audience,
            issuer=issuer,
        )
    except ResourceServerAuthError:
        raise
    except jwt.InvalidAudienceError as exc:
        raise ResourceServerAuthError(
            "invalid_token", AuthMessage.INVALID_AUDIENCE
        ) from exc
    except _FIXED_TEXT_CLAIM_ERRORS as exc:
        # pyjwt's text for these is fixed (or names a claim from PMCP's own
        # required list), so it is safe to keep as the description.
        raise ResourceServerAuthError("invalid_token", pyjwt_text(exc)) from exc
    except jwt.InvalidTokenError as exc:
        # Every other token error may quote the token back (pyjwt names an
        # unknown `crit` extension, for one), so the description is fixed.
        raise ResourceServerAuthError(
            "invalid_token", AuthMessage.INVALID_TOKEN
        ) from exc

    scopes = _claim_scopes(claims)
    missing_scopes = sorted(set(required_scopes or []) - set(scopes))
    if missing_scopes:
        scope_names = " ".join(missing_scopes)
        raise ResourceServerAuthError(
            "insufficient_scope", AuthMessage.MISSING_SCOPES, scopes=scope_names
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


def sanitize_url_elicitation_url(
    url: str, *, provenance: Literal["remote", "operator"] = "remote"
) -> str:
    """Validate and redact a URL-mode elicitation URL.

    This helper is shared by two call sites whose inputs have very different
    provenance, and one permissiveness cannot be right for both (#211):

    ``remote`` (the default)
        The URL came out of a downstream server's error payload. A loopback
        ``http://`` URL from there is an attempt to steer the operator at
        something on the operator's own machine, so it is refused.

    ``operator``
        The URL was typed by the operator into ``gateway.auth_connect``. Local
        OAuth redirects back to ``http://127.0.0.1``, and refusing that would
        break the frozen local-consent flow, so loopback HTTP stays allowed.

    The default is the strict side deliberately: a call site this change misses
    should lose loopback, not silently keep it. Returns ``str`` -- the frozen
    IF-0-ELICIT-1 shape. Ask :func:`is_verified_public_auth_url` separately for
    whether PMCP actually verified the destination.
    """
    try:
        return sanitize_public_auth_url(
            url, allow_loopback_http=provenance == "operator"
        )
    except ValueError as exc:
        raise ValueError(
            render_auth_message(AuthMessage.ELICITATION_URL_INVALID)
        ) from exc


def sanitize_auth_diagnostic(value: object, *, max_length: int | None = 400) -> str:
    """Return a display-safe diagnostic string for auth failures.

    The redactor's own rules run first, unchanged (`_sanitize_base`); the
    additive rules (`pmcp.redaction_additive`) then run over that output and
    can only replace more of it with the marker (Consiliency/pmcp#234). The
    cut is taken last, as before.
    """
    text = redact_additive(_sanitize_base(str(value)))
    return text if max_length is None else text[:max_length]


def _sanitize_base(text: str) -> str:
    """The redactor's own rules, in order: URLs, Authorization, Bearer, the
    keyword rule, JWTs."""

    def redact_url_match(match: re.Match[str]) -> str:
        whole = match.group(0)
        raw_url = whole.rstrip(").,;")  # trailing sentence punctuation, in one pass
        return redact_auth_url(raw_url) + whole[len(raw_url) :]

    text = re.sub(r"https?://[^\s\"'<>]+", redact_url_match, text)
    text = re.sub(
        r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)(\bbearer\s+)[^\s,;]+", r"\1[REDACTED]", text)
    text = redact_keyword_values(text, _KEYWORD_KEY_START)
    text = _JWT_RE.sub("[REDACTED]", text)
    return text


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
    resource_metadata_url_verified = False
    if params.get("resource_metadata"):
        try:
            resource_metadata_url = sanitize_public_auth_url(
                params["resource_metadata"]
            )
        except ValueError:
            resource_metadata_url = None
        else:
            resource_metadata_url_verified = is_verified_public_auth_url(
                params["resource_metadata"]
            )

    return AuthChallengeInfo(
        scheme=scheme,
        resource_metadata_url=resource_metadata_url,
        resource_metadata_url_verified=resource_metadata_url_verified,
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
    # Per-field, not per-object: these five URLs are independent, so one flag
    # cannot be honest about a mix of a literal and a name (#211).
    verified_urls: list[str] = []

    def public_url(raw_url: str | None, field_name: str) -> str | None:
        if not raw_url:
            return None
        try:
            safe_url = sanitize_public_auth_url(raw_url)
        except ValueError as exc:
            normalized_diagnostics.append(
                f"{field_name} ignored: {sanitize_auth_diagnostic(exc)} "
                f"({redact_auth_url(raw_url)})"
            )
            return None
        if is_verified_public_auth_url(raw_url):
            verified_urls.append(field_name)
        else:
            normalized_diagnostics.append(
                f"{field_name} is relayed unverified: PMCP did not resolve "
                f"{safe_url} and cannot confirm where it points."
            )
        return safe_url

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
        verified_urls=verified_urls,
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
            safe_url = sanitize_url_elicitation_url(url, provenance="remote")
        except ValueError:
            continue
        message = entry.get("message")
        verified = is_verified_public_auth_url(url)
        # `next_step` is the string an agent actually follows, so the caveat has
        # to live inside it. A `url_verified: false` field alongside an
        # unqualified "Open the URL" instruction still gets the URL opened.
        follow_up = (
            "open the URL out of band, complete consent, then call "
            f"gateway.auth_connect(auth_mode='url_elicitation', "
            f"server_name='<server>', elicitation_id='{elicitation_id}', "
            "consent_acknowledged=true)."
        )
        parsed_entries.append(
            UrlElicitationInfo(
                elicitation_id=elicitation_id,
                url=safe_url,
                url_verified=verified,
                message=message if isinstance(message, str) else None,
                next_step=(
                    f"{follow_up[0].upper()}{follow_up[1:]}"
                    if verified
                    else f"{UNVERIFIED_URL_CAVEAT} Once confirmed, {follow_up}"
                ),
            )
        )
    return parsed_entries


def fetch_json_metadata(
    url: str, *, timeout: float = 5.0
) -> tuple[dict[str, object] | None, str | None]:
    """Fetch public auth metadata without forwarding credentials.

    This is the fail-closed side of #211. PMCP *retrieves* this URL itself, so
    "accepted but unverified" is not good enough here the way it is on a relay
    path: the host must be one PMCP classified as a public IP literal. Loopback
    HTTP and unresolved names are both refused, and refused **before** the
    opener is reached, so a rejected URL is never requested.

    This gates the interface rather than removing it: ``fetch_json_metadata`` is
    frozen by IF-0-SAFEURL-4 (``plans/phase-plan-v4-safeurl.md``), so deleting
    it would break that freeze even though it has no production caller today.
    """
    try:
        safe_url = sanitize_public_auth_url(url)
    except ValueError as exc:
        return None, sanitize_auth_diagnostic(exc)
    if not is_verified_public_auth_url(url):
        return None, (
            f"{safe_url}: refused before fetching -- PMCP retrieves only URLs "
            "whose host it verified as a public IP literal, and it does not "
            "resolve host names."
        )
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
