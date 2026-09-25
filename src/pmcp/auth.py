"""Authorization metadata, elicitation, and redaction helpers."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
import time
import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from itertools import product
from typing import Any, Literal
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, urlparse, urlunparse, unquote
from urllib.request import HTTPRedirectHandler, Request, build_opener

import aiohttp
import jwt
from jwt import PyJWKSet

from pmcp.types import AuthChallengeInfo, AuthMetadataInfo, UrlElicitationInfo


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
        raise HTTPError(newurl, code, "Redirects are not allowed.", headers, fp)


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
    "auth",
    "aws_access",
    "aws_secret",
    "client_secret",
    "code",
    "cookie",
    "credential",
    "credentials",
    "id_token",
    "jwt",
    "passwd",
    "password",
    "private_key",
    "pwd",
    "refresh_token",
    "saml",
    "secret",
    "secret_access_key",
    "session",
    "set-cookie",
    "sid",
    "tenant-id",
    "tenant_id",
    "token",
}

#: Keys that name a credential only sometimes. A plain word or number after
#: them is kept: `credentials: include` (a fetch mode), `auth=basic`,
#: `auth: none`, `{"code": -32601}` (every JSON-RPC error), `{"code":
#: "not_found"}`. Anything else -- a token, `user:pass`, a path -- is
#: redacted. Every other key in `AUTH_DIAGNOSTIC_SECRET_KEYS` is strong: its
#: value is redacted whatever it looks like. `tests/test_redaction.py`
#: derives its key-coverage property from both sets, so a key named here but
#: not redacted in every form fails that test.
WEAK_SECRET_KEYS = frozenset({"auth", "code", "credential", "credentials"})

_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    r"(?![A-Za-z0-9_-])"
)

# --- shape-based redaction (Consiliency/pmcp#234) -----------------------------
#
# The keyword rules below only fire on `<key><sep><value>`; everything else a
# credential can look like is caught by *shape*: a run of token characters whose
# character classes alternate the way random bytes do and English, identifiers,
# hex digests and timestamps do not. tests/test_redaction.py holds the two
# corpora this is tuned against: prose that must survive byte-identical, and
# credentials that must not survive at all. Both are acceptance criteria; a
# redactor tested on only one of them drifts toward redacting everything or
# nothing.

#: A maximal run of token characters. `.` is excluded on purpose so module
#: paths, versions and hostnames split into short words (JWTs, which are
#: dot-joined, have their own rule above). `/` and `+` are included so a classic
#: base64 blob stays one run and is scored whole.
_OPAQUE_RUN_RE = re.compile(r"[A-Za-z0-9_+/-]+={0,2}")
#: Uniform hex, optionally `0x`-prefixed: git SHAs, digests, UUIDs, request ids
#: and the `<Foo object at 0x7f3a2b1c4d50>` in every traceback. Kept.
_UNIFORM_HEX_RE = re.compile(r"^(?:0[xX])?(?:[0-9a-f]+|[0-9A-F]+)$")
_SEGMENT_SPLIT_RE = re.compile(r"[_+/-]+")
#: `-` and `_` join identifiers (`pmcp-7d9f8b6c5-x2k9q`, `x86_64-linux-gnu`);
#: a run containing them is scored segment by segment, never as a whole, or
#: the concatenation of short hyphenated pieces reads as random.
_IDENTIFIER_JOINER_RE = re.compile(r"[_-]")

#: A segment (or a whole run) is "opaque" when it is at least this long ...
_OPAQUE_MIN_SEGMENT = 10
_OPAQUE_MIN_RUN = 16
#: ... and at most this long. Longer is a payload, not a credential: base64
#: image data and encoded files come back through `process_output` with
#: redaction on, and no vendor issues a single unbroken token this long (JWTs
#: are dot-joined and have their own rule; private keys are PEM blocks).
_OPAQUE_MAX_RUN = 256
#: ... and its lower/upper/digit classes change hands at least this many times,
#: at more than this fraction of adjacent positions. camelCase identifiers
#: (`ResourceServerAuthError`: 3/22) and timestamps (`20260922T101500Z`: 3/15)
#: sit under the ratio; random alphanumerics sit far above it.
_OPAQUE_MIN_TRANSITIONS = 3
_OPAQUE_MIN_TRANSITION_RATIO = 0.25

#: Short lowercase prefix, up to two short qualifiers, then a body of 12-256
#: alphanumerics that mixes letters and digits: `sk-live-…`, `ghp_…`,
#: `glpat-…`, `hf_…`, `npm_…`. A prefix is evidence in itself, so the body is
#: held to a weaker test than a bare run (a letter and a digit rather than the
#: transition score). Whole-alpha and whole-numeric bodies are NOT matched:
#: `ghp_abcdefghijklmnop` is the probe tests/test_project_source_consent_policy.py
#: uses to prove the *policy* defaults still apply, and it must stay invisible
#: to this engine or that proof goes vacuous.
_PREFIXED_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"[a-z]{2,8}(?:[_-][a-z0-9]{1,8}){0,2}[_-]"
    r"(?=[A-Za-z0-9]*[0-9])(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{12,256}"
    r"(?![A-Za-z0-9_-])"
)

#: Documented fixed vendor shapes the transition score cannot see. This list is
#: a supplement to the shape rules, not the defence: a prefix nobody listed is
#: still caught above if its body is random. Each entry says why it is here.
_VENDOR_SHAPE_RES = (
    # AWS access key ids: a 4-letter family prefix + 16 upper-alnum. Uniformly
    # upper-case with few digits, so the transition score does not fire.
    re.compile(
        r"(?<![A-Za-z0-9])(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|APKA)"
        r"[A-Z0-9]{16}(?![A-Za-z0-9])"
    ),
    # Slack tokens: `xox[abeprs]-` then numeric segments; only the last segment
    # carries transitions, and on a short one the score misses.
    re.compile(r"(?<![A-Za-z0-9])xox[abeprs]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9])"),
    # Google API keys: `AIza` + 35 base64url; caught by shape too, listed so the
    # whole key is one replacement.
    re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"),
    # PEM private-key blocks: one replacement for the block, not one per line.
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


#: camelCase never puts two capitals after a lower-case letter; random
#: mixed-case text does so within a few characters.
_CAMEL_BREAKER_RE = re.compile(r"[a-z][A-Z]{2}")
_DIGIT_RUN_RE = re.compile(r"[0-9]+")
_LOWER_RUN_RE = re.compile(r"[a-z]+")


def _char_class(char: str) -> int:
    if char.isdigit():
        return 2
    return 1 if char.isupper() else 0


def _looks_opaque(alnum: str, minimum: int) -> bool:
    """Does an alphanumeric string read as random bytes rather than a name?

    Each clause names the false positive it exists to prevent; the corpora in
    tests/test_redaction.py hold the evidence.
    """
    length = len(alnum)
    if length < minimum or _UNIFORM_HEX_RE.match(alnum):
        return False
    transitions = sum(
        1 for a, b in zip(alnum, alnum[1:]) if _char_class(a) != _char_class(b)
    )
    ratio = transitions / (length - 1)
    if transitions < _OPAQUE_MIN_TRANSITIONS or ratio <= _OPAQUE_MIN_TRANSITION_RATIO:
        return False  # `ResourceServerAuthError`, `20260922T101500Z`, `sha256sum`
    digit_runs = len(_DIGIT_RUN_RE.findall(alnum))
    if digit_runs < 2:
        # Identifiers embed ONE number (`X509Cert`, `Rsa2048Key`,
        # `Oauth2ClientError`, `UnicodeDecodeError`); random text scatters
        # digits. With at most one, demand the mixed-case signature camelCase
        # cannot produce, and a ratio above what acronym-bearing names reach
        # (`parseJSON2Dict`: 0.31, `toHTML5String`: 0.33).
        return ratio > 0.35 and _CAMEL_BREAKER_RE.search(alnum) is not None
    if length < _OPAQUE_MIN_RUN:
        # Short, with two numbers: `s3bucket2024` still carries a whole word.
        longest_word = max(len(run) for run in _LOWER_RUN_RE.findall(alnum + "a"))
        return longest_word <= 4
    return True


def _run_is_opaque(run: str) -> bool:
    if len(run) > _OPAQUE_MAX_RUN:
        return False  # a payload (base64 image data, an encoded file)
    alnum = "".join(c for c in run if c.isalnum())
    if not alnum or _UNIFORM_HEX_RE.match(alnum):
        return False
    if _IDENTIFIER_JOINER_RE.search(run) is None and _looks_opaque(
        alnum, _OPAQUE_MIN_RUN
    ):
        return True  # a bare alphanumeric or classic-base64 run, scored whole
    return any(
        _looks_opaque(segment, _OPAQUE_MIN_SEGMENT)
        for segment in _SEGMENT_SPLIT_RE.split(run.rstrip("="))
    )


def _reads_as_route(run: str) -> bool:
    """Is a long `/`-joined run a URL path rather than a base64 payload?

    A route is short pieces, at least two of them plain words
    (`/api/v1/organizations/acme/secrets/<id>/versions/latest`). Base64 puts
    a `/` every ~64 characters at random, so a payload of any length has
    pieces over `_ROUTE_MAX_PIECE` and, in practice, no two word pieces.
    """
    pieces = run.split("/")
    if max(len(piece) for piece in pieces) > _ROUTE_MAX_PIECE:
        return False
    return sum(1 for piece in pieces if _ROUTE_WORD_RE.fullmatch(piece)) >= 2


#: One replacement: ``text[start:end]`` becomes ``replacement``. Every pass
#: reads the ORIGINAL text and returns spans; nothing is rewritten until
#: `apply_redaction_spans` applies them all at once, so no pass can consume a
#: boundary -- a closing quote, an escaping backslash -- that another pass
#: needs (Consiliency/pmcp#234, revs 2-3: the `Bearer` pass ate a closing
#: quote, the URL pass ate the backslash that escaped one).
Span = tuple[int, int, str]

REDACTED = "[REDACTED]"


def _run_spans(start: int, run: str) -> list[Span]:
    if len(run) > _OPAQUE_MAX_RUN and not _reads_as_route(run):
        # A payload (JPEG base64 starts `/9j/` and carries a `/` every ~64
        # characters): the bound applies to the whole run BEFORE any path
        # splitting, or every piece between two slashes is scored on its own
        # and an ordinary image comes back as `[REDACTED]/[REDACTED]/...`.
        return []
    if "/" in run and (run.startswith("/") or _WORDY_PIECES_RE.search(run)):
        # A path: keep the route and replace only the credential-shaped
        # pieces (`/v1/[REDACTED]/status`), so the reader still learns which
        # endpoint failed. Base64 with `/` in it (an AWS secret key) has no
        # leading slash and no words, and is scored -- and replaced -- whole.
        spans: list[Span] = []
        offset = start
        for piece in run.split("/"):
            if _run_is_opaque(piece):
                spans.append((offset, offset + len(piece), REDACTED))
            offset += len(piece) + 1
        return spans
    return [(start, start + len(run), REDACTED)] if _run_is_opaque(run) else []


#: Two `/`-separated pieces that are plain lower-case words: a route, not a blob.
#: For a run past `_OPAQUE_MAX_RUN`: the longest piece a route may have and
#: the plain-word piece shape `_reads_as_route` counts.
_ROUTE_MAX_PIECE = 64
_ROUTE_WORD_RE = re.compile(r"[a-z]{3,}")
_WORDY_PIECES_RE = re.compile(r"(?:^|/)[a-z]{3,}/(?:[^/]*/)*[a-z]{3,}(?:/|$)")


def _shape_spans(text: str) -> list[Span]:
    """Spans of every credential-shaped run in ``text``: JWTs, the vendor
    supplement, prefixed tokens and scored opaque runs."""
    spans: list[Span] = [(m.start(), m.end(), REDACTED) for m in _JWT_RE.finditer(text)]
    for vendor_re in _VENDOR_SHAPE_RES:
        spans.extend((m.start(), m.end(), REDACTED) for m in vendor_re.finditer(text))
    spans.extend(
        (m.start(), m.end(), REDACTED) for m in _PREFIXED_TOKEN_RE.finditer(text)
    )
    for m in _OPAQUE_RUN_RE.finditer(text):
        spans.extend(_run_spans(m.start(), m.group(0)))
    return spans


#: A word (`bucket`, `Token`, `not_found`, `invalid_grant`) or a number (`401`,
#: `-32601`, `0x80070005`), sentence punctuation allowed after it (`token:`,
#: `bucket.`): the values prose and status payloads put after a keyword, and
#: never what a credential looks like.
_PLAIN_WORD_RE = re.compile(r"[A-Za-z_]+")
_NUMBER_RE = re.compile(r"[+-]?[0-9]+|0[xX][0-9a-fA-F]+")


def _is_plain_word_or_number(value: str) -> bool:
    value = value.strip("\"'").rstrip(".:!?")
    return bool(_PLAIN_WORD_RE.fullmatch(value) or _NUMBER_RE.fullmatch(value))


def _value_could_be_a_credential(value: str) -> bool:
    """The test a whitespace-separated keyword value must pass to be redacted.

    Not a plain word or number, and either digit-bearing and 6+ characters
    (`abc123def456`, `hunter2`) or punctuated and 8+ (`secret-bearer`,
    `correct-horse-battery`). `token bucket`, `session expired`, `token v2`
    and `code 401` all fail it; `--token abc123def456` passes.
    """
    value = value.strip("\"'")
    if _is_plain_word_or_number(value):
        return False
    if any(c.isdigit() for c in value):
        return len(value) >= 6
    return len(value) >= 8


#: `code` names a credential only in the OAuth sense -- bare (`code=`, the
#: callback parameter) or under one of these qualifiers (`auth_code=`,
#: `device_code=`). Under any other qualifier (`status_code=401`,
#: `error_code=invalid_grant`, `exit_code=137`) it names a status, and its
#: value is left to the shape rules, which still catch an opaque one. Even in
#: the OAuth sense a plain word or number is kept: `{"code": -32601}` is every
#: JSON-RPC error and `{"code": "not_found"}` every REST one.
_CODE_QUALIFIERS = frozenset({"", "auth", "authorization", "oauth", "device", "user"})


def _secret_key_alternation() -> str:
    return "|".join(
        [
            *sorted(re.escape(key) for key in AUTH_DIAGNOSTIC_SECRET_KEYS),
            r"api[_-]?key",
            r"private[_-]?key",
        ]
    )


#: `<identifier><sep><value>` where the identifier's LAST segment is a secret
#: key. Last, not any: `token_type=Bearer`, `token_endpoint=` and `secret_arn=`
#: name a type, an endpoint and an ARN, and the pre-#234 containing match
#: (`unicode`, `encoded`, `tokenizer`) is how prose got mangled. Segments are
#: `_`/`-` joined or camelCase (`accessToken`). The separator is `:` or `=`
#: with optional quotes around key and value so JSON (`"password": "x"`) is
#: covered -- a quoted value runs to its CLOSING quote, past any escaped one
#: (`"a\\"hunter2"`), or the tail after the escape survives, and never past
#: a newline (a JSON string cannot hold one). It needs its closing quote:
#: the redactor sees whole values because every surface redacts BEFORE it
#: cuts (`sanitize_auth_diagnostic`, and `PolicyManager.process_output` over a
#: bounded window), so an unterminated value is the server's own text, not
#: ours to guess at. Bare whitespace is NOT a separator here (see
#: `_KEYWORD_WS_RE`). A key starts at a non-identifier character or right
#: after a JSON-escaped line break or tab (`\\r\\nsecret: hunter2` inside a
#: serialised leaf -- `main`'s containing match covered that by accident, and
#: the bar is never worse than `main`). The separator is `:` or `=`, Ruby's `=>`, httpie's `:=`,
#: or `==` when nothing but the value follows it (`password==hunter2` is
#: httpie's query syntax; `if token == expected` is a comparison). The value
#: may sit on the NEXT line when that line is indented (YAML block style,
#: pretty-printed JSON) or starts with a quote, or -- unindented -- when the
#: value is credential-shaped (`password:\r\nhunter2`, as main redacted it);
#: `token:\nthe bearer of` and `token:\n  - a bullet` are prose. Horizontal whitespace is ` `, tab or
#: no-break space (`\xa0`, which `\s` matched on main); a line break is
#: `\n` or `\r\n` (HTTP header folding, Windows dumps) -- rev 7 dropped `\r`
#: and `\xa0` and regressed against main. A bare value ends at whitespace, a quote or a list
#: separator (`,`, `;`, or `&` -- a query string's) and at nothing else,
#: except that it never STARTS on `[` or `{` (`"password": [\n  "x"\n]` is a
#: list, whose elements carry no key -- a stated residual, main's too; the
#: one `[` allowed is the marker's own, so `token=[REDACTED]abc123def456`
#: is still one value) and
#: never ENDS on a closing bracket or a backslash (the `\\`
#: before a quote in a JSON-serialised string escapes that quote; eating it
#: breaks the document): `password=(Xk9mQ2vL)` is one value, punctuation and all,
#: while the `}` of `{"password": [REDACTED]}` stays with the object.
_KEYWORD_SEP_RE = re.compile(
    r"(?P<key>(?P<qualifier>(?:(?<![A-Za-z0-9:.])|(?<=\\[nrt]))(?:[A-Za-z0-9]+[_-])*"
    r"(?:(?-i:[a-z]+(?=[A-Z])))?)"
    rf"(?P<name>{_secret_key_alternation()})(?:[_-]?(?:id|key)|s)?)"
    r"(?P<sep>[\"']?[ \t\xa0]*(?:=>|:=|==(?![ \t=])|[:=](?!=))"
    r"(?:[ \t\xa0]*\r?\n[ \t\xa0]*(?=[^\s\-*#>])|[ \t\xa0]*))"
    r"(?P<value>\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'"
    r"|(?!\{)(?!\[(?!REDACTED\]))[^\s\"',;&]*[^\s\"',;&)\]}\\])",
    re.IGNORECASE,
)

#: A bare keyword (or `--keyword` flag) followed by whitespace and a value that
#: could be a credential (`_value_could_be_a_credential`). This is what keeps
#: `--token abc123def456` redacted without redacting the second word of
#: `token bucket`, `secret ingredient` or `session expired`. A `param=value`
#: after the keyword (`token expires_in=3600`) is not its value either. `code`
#: never fires here: `exit code 137`, `status code 401`, `zip code 94105`.
#: The whitespace may include a newline into an indented line (a folded
#: header: `x-api-key\n  abc123def456`), gated by the same value test. A
#: value never starts with `-`, `*`, `#` or `>`: `secret --bucket` is a flag
#: after a word, `token:\n  - item` a bullet.
_KEYWORD_WS_RE = re.compile(
    r"(?P<key>(?:(?<![A-Za-z0-9_-])|(?<=\\[nrt]))(?:--)?(?:[A-Za-z0-9]+[_-])*"
    rf"(?P<name>{_secret_key_alternation()})s?)"
    r"(?P<sep>[ \t\xa0]+|[ \t\xa0]*\r?\n[ \t\xa0]*)"
    r"(?![A-Za-z_-]+=[^=])(?![-*#>])(?P<value>[^\s\"',;()\[\]{}]+)",
    re.IGNORECASE,
)

#: `Bearer <token>` -- the HTTP scheme, so anything after it that is not a word
#: is a token. Not `token_type=Bearer expires_in=3600` (bearer as a VALUE, the
#: lookbehinds -- which do NOT exclude a quote: `{"text": "Bearer x"}` is how
#: every JSON-serialised string arrives, and rev 6 let it through), not `Bearer realm="x"` (a challenge's own parameters, the
#: lookahead), not `Missing bearer token` or `the bearer of bad news` (plain
#: words, the callback). `(?<![A-Za-z0-9_-])` rather than `\b`: on main
#: `\bbearer` fired inside `secret-bearer failed` and redacted `failed`. The
#: value stops at a quote or bracket: `{"password": "hunter2 Bearer x"}` must
#: keep its closing quote for the keyword pass, not lose it to this one.
#: It never ends on a backslash either: in a serialised leaf the token reads
#: `Bearer hunter2tok\"`, and eating the `\` un-escapes the quote.
_BEARER_RE = re.compile(
    r"(?<![=:])(?<![=:] )(?<![A-Za-z0-9_-])"
    r"(?P<key>bearer(?:[ \t\xa0]+|[ \t\xa0]*\r?\n[ \t\xa0]+))(?![A-Za-z_-]+=[^=])(?P<value>[^\s,;\"'()\[\]{}]*[^\s,;\"'()\[\]{}\\])",
    re.IGNORECASE,
)


#: `Authorization: Bearer x`, `Authorization=x`, and JSON `"authorization":
#: "Bearer x"` (the key may be quoted). A quoted value is redacted WHOLE
#: between its quotes -- a header value can hold spaces (`Basic dXNl cg==`
#: is malformed but leaks the same) and punctuation -- and a bare value is
#: an optional HTTP auth scheme word (`Basic dXNl…` goes whole, as on main)
#: then a run to whitespace, a quote or a list separator -- unless it is a plain word
#: or number (`authorization: none`, `Authorization: required`), which is
#: prose. The separator is on the same line: `Set the Authorization:\nheader
#: first` is a sentence, not a header.
_AUTHORIZATION_RE = re.compile(
    r"authorization[\"']?[ \t\xa0]*[:=]"
    r"(?:[ \t\xa0]*\r?\n[ \t\xa0]*(?=[^\s\-*#>])|[ \t\xa0]*)"
    r"(?:(?P<quoted>\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*')"
    r"|(?P<bare>(?:(?:bearer|basic|digest|negotiate|ntlm|token)[ \t]+)?"
    r"[^\s,;\"']*[^\s,;\"')\]}]))",
    re.IGNORECASE,
)

#: A URL in free text; trailing sentence punctuation is handed back.
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


#: A separator whose line break is followed by no indentation.
_UNINDENTED_BREAK_RE = re.compile(r"\r?\n[^ \t\xa0]*$")


def _keyword_sep_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for match in _KEYWORD_SEP_RE.finditer(text):
        name = match.group("name").lower()
        if name in WEAK_SECRET_KEYS and _is_plain_word_or_number(match.group("value")):
            continue
        if _UNINDENTED_BREAK_RE.search(match.group("sep")) and not (
            match.group("value")[0] in "\"'"
            or _value_could_be_a_credential(match.group("value"))
        ):
            continue  # `token:\nthe bearer of`: a sentence, not a value
        if name == "code":
            qualifier = match.group("qualifier").rstrip("_-").lower()
            if qualifier not in _CODE_QUALIFIERS:
                continue
        spans.append((match.start("value"), match.end("value"), REDACTED))
    return spans


def _keyword_ws_spans(text: str) -> list[Span]:
    return [
        (match.start("value"), match.end("value"), REDACTED)
        for match in _KEYWORD_WS_RE.finditer(text)
        if match.group("name").lower() != "code"
        and _value_could_be_a_credential(match.group("value"))
    ]


def _bearer_spans(text: str) -> list[Span]:
    return [
        (match.start("value"), match.end("value"), REDACTED)
        for match in _BEARER_RE.finditer(text)
        if not _is_plain_word_or_number(match.group("value"))
    ]


def _authorization_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    for match in _AUTHORIZATION_RE.finditer(text):
        if match.group("quoted") is not None:
            spans.append((match.start("quoted") + 1, match.end("quoted") - 1, REDACTED))
        elif not _is_plain_word_or_number(match.group("bare")):
            spans.append((match.start("bare"), match.end("bare"), REDACTED))
    return spans


#: Percent-decoding a query value and asking the engine about it can meet
#: another encoded URL inside; three levels is more than any real URL nests
#: (a redirect inside a redirect), and past that the value is treated as
#: covered -- redacted -- rather than decoded again. Downstream-controlled
#: text must not be able to recurse the diagnostic path to death.
_MAX_DECODE_DEPTH = 3

Covers = Callable[[str], bool]


def _url_component_spans(
    base: int, raw_url: str, depth: int, covers: Covers | None
) -> list[Span]:
    """The parts of one URL `redact_auth_url` would strip or redact, as spans
    over the text the URL sits in: the userinfo (dropped), each value under an
    `AUTH_SECRET_QUERY_KEYS` key (`[REDACTED]`), and the fragment (dropped).

    Spans, not a rewrite: a rewritten URL would be one span containing the
    query, and every keyed or shape span inside that query (`?pwd=hunter2`,
    `?access=ghp_…`, a JWT under a harmless key) would be dropped as
    "contained" -- rev 5's regression against main. The path is left to the
    shape pass, which sees it like any other text.
    """
    spans: list[Span] = []
    scheme_end = raw_url.find("://")
    netloc_start = scheme_end + 3 if scheme_end >= 0 else 0
    netloc_end = len(raw_url)
    for stop in "/?#":
        index = raw_url.find(stop, netloc_start)
        if index >= 0:
            netloc_end = min(netloc_end, index)
    at = raw_url.rfind("@", netloc_start, netloc_end)
    if at >= 0:
        spans.append((base + netloc_start, base + at + 1, ""))
    fragment = raw_url.find("#")
    if fragment >= 0:
        spans.append((base + fragment, base + len(raw_url), ""))
    query_start = raw_url.find("?")
    if query_start >= 0:
        query_end = fragment if fragment > query_start else len(raw_url)
        position = query_start + 1
        for pair in raw_url[query_start + 1 : query_end].split("&"):
            key, equals, value = pair.partition("=")
            value_start = position + len(key) + 1
            if equals and value and unquote(key).lower() in AUTH_SECRET_QUERY_KEYS:
                spans.append(
                    (base + value_start, base + value_start + len(value), REDACTED)
                )
            elif (
                equals
                and "%" in value
                and _covers_anything(unquote(value), depth, covers)
            ):
                # A percent-encoded value hides its shape from every other
                # pass (`%67%68%70%5F…` is `ghp_…`): decode it, ask the whole
                # engine, and redact the encoded value whole if anything in
                # the decoded text would be.
                spans.append(
                    (base + value_start, base + value_start + len(value), REDACTED)
                )
            position += len(pair) + 1
    return spans


def _covers_anything(text: str, depth: int, covers: Covers | None) -> bool:
    """Would the engine (or the policy surface's patterns, via ``covers``)
    redact anything in the decoded ``text``? Past the decode depth: yes."""
    if depth >= _MAX_DECODE_DEPTH:
        return True
    if any(
        replacement in (REDACTED, "")
        for _, _, replacement in collect_redaction_spans(
            text, _depth=depth + 1, covers=covers
        )
    ):
        return True
    return covers is not None and covers(text)


def _url_spans(text: str, depth: int, covers: Covers | None) -> list[Span]:
    spans: list[Span] = []
    for match in _URL_RE.finditer(text):
        raw_url = match.group(0)
        while raw_url and raw_url[-1] in ").,;":
            raw_url = raw_url[:-1]
        spans.extend(_url_component_spans(match.start(), raw_url, depth, covers))
    return spans


def collect_redaction_spans(
    text: str, *, _depth: int = 0, covers: Covers | None = None
) -> list[Span]:
    """Every redaction the engine would make to ``text``, as spans over it.

    Each pass reads the same, unmodified ``text``. The order of the list does
    not matter: `apply_redaction_spans` merges overlaps. ``covers`` lets the
    policy surface add its own patterns to the question asked of a decoded
    query value (a raw encoded value evades a pattern written for the
    decoded shape; main decoded before matching, and so does this).
    """
    return [
        *_url_spans(text, _depth, covers),
        *_keyword_sep_spans(text),
        *_authorization_spans(text),
        *_bearer_spans(text),
        *_keyword_ws_spans(text),
        *_shape_spans(text),
    ]


def apply_redaction_spans(text: str, spans: list[Span]) -> str:
    """Apply ``spans`` to ``text`` in one pass.

    Overlaps are resolved conservatively. A span inside another is dropped
    only when the outer replacement *covers* it -- `[REDACTED]` or the empty
    string (a URL inside a quoted password becomes `[REDACTED]` with the
    password); under any other replacement the two are merged and their union
    becomes `[REDACTED]`, as are two spans that partially overlap. For
    identical ranges `[REDACTED]` beats any other replacement.

    Every `[REDACTED]` already in the text is itself a span. An existing
    marker therefore merges with whatever touches it -- `password=hunter2
    [REDACTED]` becomes `password=[REDACTED]` rather than surviving because a
    downstream server wrote the marker -- and a marker nothing touches is
    replaced by itself, which is what keeps every surface idempotent
    (`password=[REDACTED]` is a fixed point on both surfaces; on the policy
    surface `password= [REDACTED]` becomes `password=[REDACTED]` once, then
    stays).
    """
    markers: list[Span] = [
        (m.start(), m.end(), REDACTED) for m in re.finditer(re.escape(REDACTED), text)
    ]
    candidates = [*spans, *markers]
    ordered = sorted(
        (span for span in candidates if span[0] < span[1]),
        key=lambda span: (span[0], -span[1], span[2] != REDACTED),
    )
    merged: list[Span] = []
    for start, end, replacement in ordered:
        if merged and start < merged[-1][1]:
            previous_start, previous_end, previous_replacement = merged[-1]
            if end <= previous_end and previous_replacement in (REDACTED, ""):
                continue
            merged[-1] = (previous_start, max(end, previous_end), REDACTED)
            continue
        merged.append((start, end, replacement))
    pieces: list[str] = []
    position = 0
    for start, end, replacement in merged:
        pieces.append(text[position:start])
        pieces.append(replacement)
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


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
            raise ValueError(
                "Public auth URL host is a non-public IP literal or loopback name."
            )

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
                async with session.get(
                    self._raw_url, allow_redirects=False
                ) as response:
                    if 300 <= response.status < 400:
                        raise ResourceServerJWKSUnavailable(
                            f"JWKS endpoint returned a redirect for {self.url}; "
                            "refusing to follow."
                        )
                    response.raise_for_status()
                    content = await response.content.read(self._max_bytes + 1)
        except ResourceServerJWKSUnavailable:
            raise
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
        raise ValueError("Invalid URL-mode elicitation URL.") from exc


def sanitize_auth_diagnostic(value: object, *, max_length: int | None = 400) -> str:
    """Return a display-safe diagnostic string for auth failures."""
    text = str(value)
    # Every pass reads the same text; the replacements land in one step
    # (Consiliency/pmcp#234). The cut is taken afterwards, so truncation can
    # only ever shorten `[REDACTED]`, never expose a prefix.
    text = apply_redaction_spans(text, collect_redaction_spans(text))
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
