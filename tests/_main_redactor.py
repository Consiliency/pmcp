"""`main`'s redactor, vendored VERBATIM from `origin/main` (1fb36f2) for the
floor's fidelity tests (Consiliency/pmcp#234): `pmcp.redaction_floor` replays
main's rules with origin tracking, and its intermediate text must equal what
these functions return, byte for byte. Test-only: main's keyword rule is
quadratic on joiner-rich runs (`a-a-a-...`), which is why production replays
it with a linear matcher instead of calling it.

Only `redact_secrets` is adapted: it is a method on main, here a function of
the text and the effective compiled patterns.
"""

# ruff: noqa: E501
from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, urlparse, urlunparse

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


DEFAULT_REDACTION_PATTERNS = [
    # Common secret patterns (case-insensitive)
    r"(api[_-]?key|apikey)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
    r"(secret|password|passwd|pwd)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
    r"(bearer|token)[\s]+[a-zA-Z0-9._-]+",
    r"(aws_secret|aws_access)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
    r"\bsk-[A-Za-z0-9_-]{6,}\b",
    r"\bghp_[A-Za-z0-9_]{10,}\b",
    r"\bgithub_pat_[A-Za-z0-9_]{10,}\b",
]


def compiled_default_patterns() -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in DEFAULT_REDACTION_PATTERNS]


def redact_secrets(
    output: str, redaction_regexes: list[re.Pattern[str]] | None = None
) -> str:
    """Redact secrets from output."""
    result = sanitize_auth_diagnostic(output, max_length=None)

    if redaction_regexes is None:
        redaction_regexes = compiled_default_patterns()
    for regex in redaction_regexes:

        def replace_match(match: re.Match[str]) -> str:
            full_match = match.group(0)
            # Find the separator (: or =)
            for i, char in enumerate(full_match):
                if char in ":=":
                    return full_match[: i + 1] + " [REDACTED]"
            return "[REDACTED]"

        result = regex.sub(replace_match, result)

    return result
