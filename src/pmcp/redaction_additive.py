"""The additive redaction rules (Consiliency/pmcp#234).

`sanitize_auth_diagnostic` and `PolicyManager.redact_secrets` first run the
redactor exactly as it was (its URL, Authorization, Bearer, keyword and JWT
rules, then the policy patterns), and then apply these rules to that
redactor's OUTPUT. A rule here can only replace text with the redaction
marker; it never restores anything. So every piece the redactor removed
stays removed, by construction, and these rules can only add.

What they add: separator-anchored keyword rules over a wider key set,
keyword lists, whitespace keywords with a credential-shaped value, Bearer
and Authorization values, secrets in URL query values (percent-decoded),
and shapes that need no keyword -- JWTs, vendor token shapes, prefixed
tokens, PEM private-key blocks and high-entropy runs.

Three guarantees, each tested (`tests/test_redaction_additive.py`):

* the floor above;
* JSON: when the text is a JSON document, a replacement is confined to
  string contents, whole escapes at a time, so a document stays a
  document (`_clip_to_json_strings`);
* linear time and memory: every loop and string build adds what it
  iterates over or copies to a test-only work counter (`WORK`).
"""

from __future__ import annotations

import bisect
import json
import re
from collections.abc import Callable, Iterator
from urllib.parse import unquote

#: Test-only work counter. None in production; the complexity tests set it to
#: `[0]`, and every loop and string build below adds what it iterates over
#: or copies, so linear time is asserted on a deterministic count.
WORK: list[int] | None = None


def work(amount: int) -> None:
    if WORK is not None:
        WORK[0] += amount


def unindented_break(text: str) -> bool:
    """Does ``text`` hold a line break with no space, tab or no-break space
    after it? The last break decides; one reverse search."""
    work(len(text))
    last = max(text.rfind("\r"), text.rfind("\n"))
    return last >= 0 and not any(c in " \t\xa0" for c in text[last + 1 :])


#: The query keys whose values the URL rule redacts (the same set as
#: `pmcp.auth.AUTH_SECRET_QUERY_KEYS`; a test pins them equal).
QUERY_SECRET_KEYS = {
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


ADDITIVE_SECRET_KEYS = {
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
#: redacted. Every other key in `ADDITIVE_SECRET_KEYS` is strong: its
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
    # The body never scans past the next BEGIN: `.*?` alone made every
    # unterminated BEGIN scan to the end of the text, which is quadratic in
    # the number of BEGINs (rev 9: 5.6 s on 264 KB of them; F9 of rev 9's
    # board).
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:(?!-----BEGIN ).)*?"
        r"-----END [A-Z ]*PRIVATE KEY-----",
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


def _counted(matches: Iterator[re.Match[str]]) -> Iterator[re.Match[str]]:
    """Each match of a pass, counted as the work of reading it (the
    test-only work counter, `work`)."""
    for match in matches:
        work(match.end() - match.start() + 1)
        yield match


def _run_spans(start: int, run: str) -> list[Span]:
    work(len(run))
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
    work(len(text) * (3 + len(_VENDOR_SHAPE_RES)))  # one scan per pattern
    spans: list[Span] = [
        (m.start(), m.end(), REDACTED) for m in _counted(_JWT_RE.finditer(text))
    ]
    for vendor_re in _VENDOR_SHAPE_RES:
        spans.extend((m.start(), m.end(), REDACTED) for m in vendor_re.finditer(text))
    spans.extend(
        (m.start(), m.end(), REDACTED) for m in _PREFIXED_TOKEN_RE.finditer(text)
    )
    for m in _counted(_OPAQUE_RUN_RE.finditer(text)):
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


#: `code` names a credential in the OAuth sense -- bare (`code=`, the
#: callback parameter) or under one of these qualifiers (`auth_code=`,
#: `device_code=`) -- and is then a weak key: a plain word or number is kept
#: (`{"code": -32601}` is every JSON-RPC error and `{"code": "not_found"}`
#: every REST one), anything else is redacted.
_CODE_QUALIFIERS = frozenset({"", "auth", "authorization", "oauth", "device", "user"})
#: Under a status or descriptive qualifier (`status_code=401`,
#: `error_code=invalid_grant`, `exit_code=137`, `zip_code=94105`,
#: `sqlstate_code=42P01`) `code` names a status, and its value is left to the
#: shape rules, which still catch an opaque one. Under any OTHER qualifier
#: (`otp_code=`, `mfa_code=`, `verification_code=`, `recovery_code=`) it is
#: a weak key: a credential-shaped value is redacted, as main redacted it.
#: An unknown qualifier therefore fails closed. Matched against the
#: qualifier's last `_`/`-` segment. Nothing here names a credential or a
#: redeemable value: `key_code`, `promo_code`, `coupon_code` and
#: `discount_code` are weak keys (tests/_redaction_grammar.py states this set
#: as an accepted class and a test pins the two equal).
_STATUS_CODE_QUALIFIERS = frozenset(
    {
        "status",
        "error",
        "exit",
        "http",
        "response",
        "return",
        "result",
        "reason",
        "zip",
        "postal",
        "country",
        "lang",
        "language",
        "currency",
        "iso",
        "area",
        "region",
        "locale",
        "event",
        "op",
        "opcode",
        "char",
        "byte",
        "source",
        "color",
        "colour",
        "product",
        "item",
        "sku",
        "sqlstate",
        "state",
        "exception",
        "fault",
        "ret",
        "rc",
        "err",
        "errno",
    }
)


def _last_segment(qualifier: str) -> str:
    return re.split(r"[_-]", qualifier.rstrip("_-").lower())[-1]


#: Where a key may start in addition to after a non-identifier character:
#: right after a JSON escape (`\\n`, `\\t`, `\\b`, `\\f`, `\\"`, `\\\\`, `\\/`,
#: `\\u00a0`). `process_output` serialises a dict leaf with
#: `json.dumps(ensure_ascii=True)`, so every control character, every
#: non-ASCII character and 25 of the 29 `str.isspace()` characters reach the
#: redactor as an escape whose last character is alphanumeric
#: (`\\u00a0password=`), which would otherwise read as a glued prefix. Main's
#: `\\b[A-Za-z0-9_-]*` swallowed the escape's tail and redacted (F3 of rev 9's
#: board).
_JSON_ESCAPE_BOUNDARY = r"(?<=\\[nrtbf/\"\\])|(?<=\\u[0-9a-fA-F]{4})"
#: ... and what a qualifier or a glued prefix may NOT start on: the tail of
#: such an escape (the `u00a0` of `\\u00a0password`, the `n` of
#: `\\npassword`), which would otherwise be read as part of the key. The key
#: NAME may start there (`\\token=x` is `token` in raw text), and anything
#: else after a backslash may be a qualifier or glued prefix
#: (`DOMAIN\\password=`, `C:\\secret=`, `\\dbpassword=`), as `\\b` made it on
#: main (F8 of rev 9's board).
_NOT_ON_AN_ESCAPE_TAIL = r"(?!(?<=\\)(?:[nrtbf]|u[0-9a-fA-F]{4}))"
#: A whitespace character as `json.dumps` spells it: `\t \n \r \f`, or a
#: `\uXXXX` escape of one of the other `str.isspace()` characters. Between
#: `Bearer`/`Authorization:` and a value in a serialised leaf, main's
#: `\s+[^\s,;]+` took such an escape as part of the value and redacted it
#: with the token (`Bearer \u2006(tok)`); here it is part of the separator.
_JSON_SPACE_ESCAPE = (
    r"\\(?:[tnrf]|u(?:000[bB]|001[c-fC-F]|0085|00[aA]0|1680|200[0-9aA]"
    r"|202[89fF]|205[fF]|3000))"
)


def _secret_key_alternation() -> str:
    return "|".join(
        [
            *sorted(re.escape(key) for key in ADDITIVE_SECRET_KEYS),
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
#: ours to guess at -- except an UNTERMINATED opening quote followed by a
#: bare run to whitespace, a list separator or the end (`password="hunter2`,
#: `api_key='abc`): main's policy defaults took the run after the quote, so
#: this does too (N5). Bare whitespace is NOT a separator here (see
#: `_KEYWORD_WS_RE`). A key starts at a non-identifier character, at a single
#: `:` (`Database:Password=`, .NET configuration; not `::`, a path, and not
#: inside an `arn:`/`urn:` resource name, see `_RESOURCE_NAME_RE`), after a
#: backslash (`DOMAIN\\password=`), or right after a JSON escape
#: (`_JSON_ESCAPE_BOUNDARY`) -- `main`'s containing match covered all of
#: these, and the bar is never worse than `main`. The separator is `:` or
#: `=`, Ruby's `=>`, httpie's `:=`, a run of up to four `:`/`=` (`===`,
#: `=:`), or `==` when nothing but the value follows it (`password==hunter2`
#: is httpie's query syntax); a separator holding `==` or `::` (`if token ==
#: expected`, `token::Type`) is a comparison or a path unless the value is
#: credential-shaped. The value
#: may sit on the NEXT line when that line is indented (YAML block style,
#: pretty-printed JSON) or starts with a quote, or -- unindented -- when the
#: value is credential-shaped (`password:\r\nhunter2`, as main redacted it);
#: `token:\nthe bearer of` and `token:\n  - a bullet` are prose. The next
#: line never opens on a `--` flag or a lone `-`/`*`/`#`/`>` marker followed
#: by whitespace; a marker glued to the value is part of it
#: (`password:\n  -hunter22`, as main redacted it). Horizontal whitespace is
#: every `str.isspace()` character but `\r`/`\n`; a line break is any run of
#: `\r`/`\n` with whitespace between (blank lines, a bare CR, HTTP header
#: folding, Windows dumps), before or after the operator -- rev 9 took
#: exactly `\r?\n` after it and regressed against main's `[\s:=]+` (F4 of
#: rev 9's board). A bare value ends at whitespace, a quote or a list
#: separator (`,`, `;`, or `&` -- a query string's) and at nothing else,
#: except that it never STARTS on `[` or `{` (`"password": [\n  "x"\n]` is a
#: list -- `_keyword_list_spans` redacts its quoted elements instead; the
#: one `[` allowed is the marker's own, so `token=[REDACTED]abc123def456`
#: is still one value) and
#: never ENDS on a closing bracket or a backslash (the `\\`
#: before a quote in a JSON-serialised string escapes that quote; eating it
#: breaks the document): `password=(Xk9mQ2vL)` is one value, punctuation and all,
#: while the `}` of `{"password": [REDACTED]}` stays with the object.
#: The opening quote of what looks like a quoted value, when the content
#: then starts with a JSON structural character: `…token: ", "next": …` is a
#: key at the END of a JSON string, and this quote closes that string. Only
#: consulted after an UNQUOTED key -- after `"password": "` the quote always
#: opens a value, whatever it holds (`", secret"` is a valid password).
_STRADDLE_RE = re.compile(r"[^\S\r\n]*[,:\]}]")

#: Every bare scalar `json.dumps` emits (`allow_nan=True` is its default):
#: after a quoted key it is a JSON literal, and it is left alone, as main left
#: it -- redacting it would make the document invalid (`NaN`, `Infinity`) or
#: change the leaf's type (`{"max_tokens": 1024}`). Numeric secrets under a
#: quoted key are Consiliency/pmcp#290's scope.
_JSON_SCALAR_RE = re.compile(
    r"null|true|false|NaN|-?Infinity"
    r"|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
)

#: A line break inside a separator: any run of `\r`/`\n`, with any whitespace
#: around it, that ends before a visible character.
_BREAK = r"[^\S\r\n]*[\r\n]\s*"

_KEYWORD_KEY_SEP = (
    r"(?P<key>(?P<qualifier>(?:(?<![A-Za-z0-9])(?<!::)|"
    + _JSON_ESCAPE_BOUNDARY
    + r")(?:"
    + _NOT_ON_AN_ESCAPE_TAIL
    + r"(?:[A-Za-z0-9]+[_-]){1,8})?"
    r"(?:(?-i:[A-Za-z][a-z]*(?=[A-Z])))?)"
    r"(?P<glued>(?:" + _NOT_ON_AN_ESCAPE_TAIL + r"[A-Za-z0-9]{1,24}?)?)"
    rf"(?P<name>{_secret_key_alternation()})(?:[_-]?(?:id|key)|s)?"
    r"(?P<extra>(?:[_-]*[A-Za-z0-9]){0,24}[_-]*))"
    r"(?P<sep>[\"']?\s*(?:=>|:=(?![:=])|[:=]{2,4}(?![:=])|[:=](?!=))"
    r"(?:" + _BREAK + r"(?=\S)(?!--|[-*#>](?:\s|$))|[^\S\r\n]*))"
)
#: The run a bare value is made of.
_BARE_RUN = r"[^\s\"',;&]*[^\s\"',;&)\]}\\]"
_KEYWORD_SEP_RE = re.compile(
    _KEYWORD_KEY_SEP
    + r"(?P<value>\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"(?![A-Za-z0-9_])|'[^'\\\n]*(?:\\.[^'\\\n]*)*'(?![A-Za-z0-9_])"
    # an unterminated opening quote, then a bare run to whitespace, a list
    # separator, the end, or a double quote (the end of the JSON string a
    # single-quoted value sits in)
    r"|[\"'](?=" + _BARE_RUN + r"(?:[\s,;&\"]|$))" + _BARE_RUN + r"(?!')"
    r"|(?!\{)(?!\[(?!REDACTED\]))" + _BARE_RUN + r")",
    re.IGNORECASE,
)
#: An AWS ARN or a URN: a key inside one names a resource
#: (`arn:aws:secretsmanager:…:secret:Name`,
#: `urn:ietf:params:oauth:token-type:access_token`), it is not one.
_RESOURCE_NAME_RE = re.compile(r"\b[au]rn:[^\s\"'<>]*", re.IGNORECASE)

#: The same keyword and separator followed by a flat list (`"password":
#: ["hunter2"]`, pretty-printed or not): each quoted element is a value of the
#: key. No nested brackets -- a list of objects carries its own keys.
#: A key that is itself declared (`secret_access_key` is one key, not
#: `secret` + a suffix): the suffixed-key gate does not apply to it.
_DECLARED_KEY_RE = re.compile(
    rf"(?:{_secret_key_alternation()})(?:[_-]?(?:id|key)|s)?", re.IGNORECASE
)
#: A literal array: quoted strings or JSON scalars separated by commas --
#: nothing bare. `[x", "b": "]` after `token: ` inside a string leaf is not an
#: array (the scan would run past the string's closing quote), while
#: `["first]", "hunter2"]` is one (a `]` inside a quoted element is text).
_LIST_ELEMENT = r"(?:\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"|'[^'\\\n]*(?:\\.[^'\\\n]*)*'|-?[0-9][0-9.eE+-]*|null|true|false)"
#: Unambiguous: each run of whitespace has exactly one place to go (a
#: missing `]` backtracks over it once, not over every way of splitting it
#: between three `\s*`, which was cubic -- rev 15's board).
_LIST_BODY = (
    r"(?P<list>\[\s*(?:"
    + _LIST_ELEMENT
    + r"\s*(?:,\s*"
    + _LIST_ELEMENT
    + r"\s*)*(?:,\s*)?)?\])"
)
_KEYWORD_LIST_RE = re.compile(
    _KEYWORD_KEY_SEP + _LIST_BODY,
    re.IGNORECASE,
)
_QUOTED_RE = re.compile(
    r"\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"|'[^'\\\n]*(?:\\.[^'\\\n]*)*'"
)

#: A bare keyword (or `--keyword` flag) followed by whitespace and a value that
#: could be a credential (`_value_could_be_a_credential`). This is what keeps
#: `--token abc123def456` redacted without redacting the second word of
#: `token bucket`, `secret ingredient` or `session expired`. A `param=value`
#: after the keyword (`token expires_in=3600`) is not its value either. `code`
#: never fires here: `exit code 137`, `status code 401`, `zip code 94105`.
#: The whitespace may include a newline into an indented line (a folded
#: header: `x-api-key\n  abc123def456`), gated by the same value test. A
#: value is never a flag or a bullet: not `--…` (`secret --bucket` is a flag
#: after a word) and not a lone `-`, `*`, `#` or `>` followed by whitespace or
#: the end (`token:\n  - item` is a bullet). A single marker glued to the
#: value is part of it: `token -abc123def`, `password\n  -hunter22` (a
#: base64url secret can start with `-`) are redacted, as on main. The flag
#: may be `--`, a single `-`, `_` or any run of them (`java -jar x.jar
#: -password hunter22x`; N9 of the rev-10 audit), and the qualifier may have
#: any number of joined segments, as main's `[A-Za-z0-9_-]*` did: the match
#: can only start where an identifier starts (the lookbehind), so an
#: unbounded qualifier stays linear here, unlike the keyed rule's, which may
#: restart after every joiner and is bounded instead.
_KEYWORD_WS_RE = re.compile(
    r"(?P<key>(?:(?<![A-Za-z0-9_-])|"
    + _JSON_ESCAPE_BOUNDARY
    + r")[_-]*(?:"
    + _NOT_ON_AN_ESCAPE_TAIL
    + r"(?:[A-Za-z0-9]+[_-]+)+)?"
    r"(?:(?-i:[A-Za-z][a-z]*(?=[A-Z])))?"
    r"(?P<glued>(?:" + _NOT_ON_AN_ESCAPE_TAIL + r"[A-Za-z0-9]{1,24}?)?)"
    rf"(?P<name>{_secret_key_alternation()})(?:[_-]?(?:id|key)|s)?"
    r"(?:[_-]*[A-Za-z0-9]){0,24}[_-]*)"
    r"(?P<sep>[^\S\r\n]+|" + _BREAK + r")"
    r"(?![A-Za-z_-]+=[^=])(?!--|[-*#>](?:\s|$))(?P<value>[^\s\"',;()\[\]{}]*[^\s\"',;()\[\]{}\\])",
    re.IGNORECASE,
)

#: `Bearer <token>` -- the HTTP scheme, so anything after it that is not a word
#: is a token, wherever it stands: `X-Auth: Bearer x`, `session=Bearer x`
#: (rev 9 excluded a preceding `:`/`=` and leaked those; F2 of its board).
#: Bearer as a VALUE is already excluded by the rest: `token_type=Bearer
#: expires_in=3600` (the lookahead: a `param=value` is not a token),
#: `token_type: Bearer` and `{"token_type": "Bearer"}` (nothing follows but
#: a quote, a comma or the end). Not `Bearer realm="x"` (a challenge's own
#: parameters, the lookahead), not `Missing bearer token` or `the bearer of
#: bad news` (plain words, the callback). `(?<![A-Za-z0-9_-])` rather than
#: `\b`: on main `\bbearer` fired inside `secret-bearer failed` and
#: redacted `failed`; a JSON escape before it is a boundary too
#: (`\u00a0Bearer x` in a serialised leaf). A token may be wrapped in a quote
#: or bracket that closes right after it (`Bearer "x"`, `Bearer (x)`, N7 of
#: the rev-10 audit): the inside is redacted and the wrapping kept. Otherwise
#: the value stops at a quote or bracket: `{"password": "hunter2 Bearer x"}`
#: must keep its closing quote for the keyword pass, not lose it to this one.
#: It never ends on a backslash either: in a serialised leaf the token reads
#: `Bearer hunter2tok\"`, and eating the `\` un-escapes the quote.
_BEARER_RE = re.compile(
    r"(?:(?<![A-Za-z0-9_-])|" + _JSON_ESCAPE_BOUNDARY + r")"
    r"(?P<key>bearer(?:(?:[^\S\r\n]|" + _JSON_SPACE_ESCAPE + r")+|" + _BREAK + r"))"
    r"(?![A-Za-z_-]+=[^=])"
    r"(?:[\"'(\[{<](?=[^\s,;\"'()\[\]{}<>\\]+[\"')\]}>]))?"
    # a value never starts on a whitespace escape: the separator's own `+`
    # would give one back and read it as the value (`Bearer\u3000[...]`).
    # One character of lookahead; asking whether a run of escapes is
    # followed by a value instead rescanned the run at every split of the
    # separator -- quadratic on `bearer` + many `\n` escapes, which the
    # quantifier sweep caught. So `Bearer\r\na-b=x` inside a JSON string
    # (a pair after the scheme, escapes between) is left to the base pass.
    r"(?!" + _JSON_SPACE_ESCAPE + r")"
    r"(?P<value>[^\s,;\"'()\[\]{}<>]*[^\s,;\"'()\[\]{}<>\\])",
    re.IGNORECASE,
)


#: `Authorization: Bearer x`, `Authorization=x`, and JSON `"authorization":
#: "Bearer x"` (the key may be quoted). A quoted value is redacted WHOLE
#: between its quotes -- a header value can hold spaces (`Basic dXNl cg==`
#: is malformed but leaks the same) and punctuation -- and a bare value is
#: an optional HTTP auth scheme word (`Basic dXNl…` goes whole, as on main)
#: then a run to whitespace, a quote or a list separator -- unless it is a plain word
#: or number (`authorization: none`, `Authorization: required`), which is
#: prose (`Set the Authorization:\nheader first` is a sentence). A value
#: wrapped in a bracket or quote after the scheme (`Authorization: [x]`,
#: `Authorization: Bearer "x"`, N6 of the rev-10 audit) has its inside
#: redacted -- except after a quoted key, where `"Authorization": [1.5]` is
#: JSON structure. The separator may hold line breaks on either side of the
#: operator, as main's `\s*[:=]\s*` did.
_AUTHORIZATION_RE = re.compile(
    r"authorization[\"']?(?:\s|" + _JSON_SPACE_ESCAPE + r")*[:=]"
    r"(?:" + _BREAK + r"(?=\S)(?!--|[-*#>](?:\s|$))"
    r"|(?:[^\S\r\n]|" + _JSON_SPACE_ESCAPE + r")*)"
    r"(?:(?P<quoted>\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"(?![A-Za-z0-9_])|'[^'\\\n]*(?:\\.[^'\\\n]*)*'(?![A-Za-z0-9_]))"
    r"|(?:(?:bearer|basic|digest|negotiate|ntlm|token)"
    r"(?:[^\S\r\n]|" + _JSON_SPACE_ESCAPE + r")+)?"
    r"[\"'(\[{<](?P<inner>[^\s,;\"'()\[\]{}<>\\]+)[\"')\]}>]"
    r"|(?P<bare>(?![\[{])(?:(?:bearer|basic|digest|negotiate|ntlm|token)[^\S\r\n]+)?"
    r"[^\s,;\"']*[^\s,;\"')\]}\\]))",
    re.IGNORECASE,
)

#: A URL in free text; trailing sentence punctuation is handed back.
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


#: A separator whose (last) line break is followed by no indentation:
#: `unindented_break`, one reverse search (a regex
#: anchored at the end restarts at every break).


def _in_resource_name(text: str) -> Callable[[int], bool]:
    """Is a position inside an `arn:`/`urn:` resource name? The names are
    disjoint and ascending (one `finditer`), so each question is one binary
    search: asking every range for every keyword match was quadratic (B-4 of
    rev 12's board)."""
    ranges = [(m.start(), m.end()) for m in _RESOURCE_NAME_RE.finditer(text)]
    starts = [a for a, _ in ranges]
    work(len(text))

    def inside(position: int) -> bool:
        work(1)
        i = bisect.bisect_right(starts, position) - 1
        return i >= 0 and position < ranges[i][1]

    return inside


def _keyword_sep_spans(text: str) -> list[Span]:
    work(len(text))  # the pass's scan
    spans: list[Span] = []
    in_resource_name = _in_resource_name(text)
    for match in _counted(_KEYWORD_SEP_RE.finditer(text)):
        if in_resource_name(match.start()):
            continue  # `arn:…:secret:Name` names a secret, it is not one
        name = match.group("name").lower()
        if name in WEAK_SECRET_KEYS and _is_plain_word_or_number(match.group("value")):
            continue
        if unindented_break(match.group("sep")) and not (
            match.group("value")[0] in "\"'"
            or _value_could_be_a_credential(match.group("value"))
        ):
            continue  # `token:\nthe bearer of`: a sentence, not a value
        if name == "code":
            qualifier = (
                (match.group("qualifier") + match.group("glued")).rstrip("_-").lower()
            )
            if qualifier not in _CODE_QUALIFIERS and (
                _last_segment(qualifier) in _STATUS_CODE_QUALIFIERS
                or not _value_could_be_a_credential(match.group("value"))
            ):
                continue  # `status_code=401`; `otp_code=abc123def456` is redacted
        start, end = match.start("value"), match.end("value")
        value = match.group("value")
        sep = match.group("sep")
        if ("==" in sep or "::" in sep) and not _value_could_be_a_credential(
            value.strip("\"'")
        ):
            # `if token == expected:` compares, `token::Type` is a path;
            # `password == hunter2` assigns
            continue
        if match.group("glued") or (
            match.group("extra")
            and not _DECLARED_KEY_RE.fullmatch(
                text, match.start("name"), match.end("extra")
            )
        ):
            # `CLIENTSECRET=`, `dbpassword=` (a prefix glued on with no case
            # or separator boundary) and suffixed keys: a secret only when
            # the value looks like one
            # `password_confirmation=`, `passwordHash=`, `secret_value=`: a
            # suffixed key is only a secret when its value looks like one
            # (`token_type=bearer`, `password_length=12` are not)
            inner = value[1:-1] if value[0] in "\"'" else value
            if (
                "://" in inner
                or inner.lower().startswith("arn:")
                or not _value_could_be_a_credential(inner)
            ):
                continue  # `token_endpoint=https://…`, `secret_arn=arn:…` name things
        if (
            match.group("sep")[:1] in "\"'"
            and value[0] not in "\"'"
            and _JSON_SCALAR_RE.fullmatch(value)
        ):
            # A quoted key -- JSON (or a Python/JS literal): a bare scalar is
            # a JSON literal, left as main left it (`_JSON_SCALAR_RE`)
            continue
        if (
            value[0] in "\"'"
            and match.group("sep")[:1] not in "\"'"
            and (
                _STRADDLE_RE.match(value, 1)
                # the OTHER quote, unescaped, inside: the value runs across a
                # JSON string boundary (`…password='x"], "k": "y'`)
                or ('"' if value[0] == "'" else "'") in value[1:-1].replace("\\\\", "")
            )
        ):
            continue  # the quote closes the string this key sits in
        if value[0] in "\"'" and (len(value) == 1 or value[-1] != value[0]):
            # An unterminated opening quote: the run after it. Ended by a
            # double quote, it may be the end of the JSON string a
            # single-quoted value sits in (`{"a": "password='x", …}`): only
            # a credential-shaped run is a value there.
            if text[end : end + 1] == '"' and not _value_could_be_a_credential(
                value[1:]
            ):
                continue
            spans.append((start + 1, end, REDACTED))
            continue
        if value[0] in "\"'":
            # Redact INSIDE the quotes: `{"password": "[REDACTED]"}` is still
            # JSON, so a structured result round-trips as a dict (main's did).
            start, end = start + 1, end - 1
        spans.append((start, end, REDACTED))
    return spans


def _keyword_list_spans(text: str) -> list[Span]:
    work(len(text))  # the pass's scan
    spans: list[Span] = []
    in_resource_name = _in_resource_name(text)
    for match in _counted(_KEYWORD_LIST_RE.finditer(text)):
        if in_resource_name(match.start()):
            continue
        name = match.group("name").lower()
        if (
            name == "code"
            and _last_segment(match.group("qualifier")) in _STATUS_CODE_QUALIFIERS
        ):
            continue  # `error_codes: [...]` are diagnostics, as in the scalar pass
        base = match.start("list")
        for element in _QUOTED_RE.finditer(match.group("list")):
            inner = element.group()[1:-1]
            # An empty element needs no case: a zero-length span is a no-op.
            if name in WEAK_SECRET_KEYS and _is_plain_word_or_number(inner):
                continue
            spans.append(
                (base + element.start() + 1, base + element.end() - 1, REDACTED)
            )
    return spans


def _is_single_case(word: str) -> bool:
    """`CLIENTSECRET`, `clientsecret`, `Clientsecret` -- one word, case-folded.
    Not `Ed25519PrivateKey` (`str.istitle` would accept that)."""
    return (
        word.isupper() or word.islower() or (word[:1].isupper() and word[1:].islower())
    )


#: An acronym glued to a Titlecase word: `PGPassword`, `DBPassword`,
#: `APIToken` (N4b of the rev-10 audit). Not `Ed25519PrivateKey`.
_ACRONYM_TITLE_RE = re.compile(r"[A-Z0-9]{1,8}[A-Z][a-z]+")


def _keyword_ws_spans(text: str) -> list[Span]:
    work(len(text))  # the pass's scan
    return [
        (match.start("value"), match.end("value"), REDACTED)
        for match in _counted(_KEYWORD_WS_RE.finditer(text))
        if match.group("name").lower() != "code"
        and _value_could_be_a_credential(match.group("value"))
        # a glued prefix (`CLIENTSECRET abc…`) only on a single-case key or
        # an acronym + Titlecase one (`PGPassword abc…`): any other
        # mixed-case identifier (`Ed25519PrivateKey X509Cert`) is prose
        and (
            not match.group("glued")
            or _is_single_case(match.group("key").lstrip("-_"))
            or _ACRONYM_TITLE_RE.fullmatch(match.group("key").lstrip("-_")) is not None
        )
        and "://" not in match.group("value")
        and not match.group("value").lower().startswith("arn:")
    ]


def _bearer_spans(text: str) -> list[Span]:
    work(len(text))  # the pass's scan
    return [
        (match.start("value"), match.end("value"), REDACTED)
        for match in _counted(_BEARER_RE.finditer(text))
        if not _is_plain_word_or_number(match.group("value"))
        and not (
            unindented_break(match.group("key"))
            and (
                # a flag or a lone bullet on the next line (the value holds no
                # whitespace, so a one-character marker is the bullet case)
                match.group("value").startswith("--")
                or (
                    match.group("value")[0] in "-*#>" and len(match.group("value")) == 1
                )
                or not _value_could_be_a_credential(match.group("value"))
            )
        )
    ]


def _authorization_spans(text: str) -> list[Span]:
    work(len(text))  # the pass's scan
    spans: list[Span] = []
    for match in _counted(_AUTHORIZATION_RE.finditer(text)):
        key_end = match.start() + len("authorization")
        quoted_key = text[key_end : key_end + 1] in ('"', "'")
        quoted = match.group("quoted")
        if quoted is not None:
            if not quoted_key and _STRADDLE_RE.match(quoted, 1):
                continue  # the quote closes the string this key sits in
            spans.append((match.start("quoted") + 1, match.end("quoted") - 1, REDACTED))
            continue
        if match.group("inner") is not None:
            if not quoted_key:  # `"Authorization": [1.5]` is JSON structure
                spans.append((match.start("inner"), match.end("inner"), REDACTED))
            continue
        bare = match.group("bare")
        if quoted_key and _JSON_SCALAR_RE.fullmatch(bare):
            continue  # a JSON literal after a quoted key: same rule as above
        if not _is_plain_word_or_number(bare):
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
    `QUERY_SECRET_KEYS` key (`[REDACTED]`), and the fragment (dropped).

    Spans, not a rewrite: a rewritten URL would be one span containing the
    query, and every keyed or shape span inside that query (`?pwd=hunter2`,
    `?access=ghp_…`, a JWT under a harmless key) would be dropped as
    "contained" -- rev 5's regression against main. The path is left to the
    shape pass, which sees it like any other text.
    """
    work(len(raw_url))
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
        # the userinfo, as a marker: the additive rules only ever write one
        spans.append((base + netloc_start, base + at, REDACTED))
    fragment = raw_url.find("#")
    if fragment >= 0:
        spans.append((base + fragment + 1, base + len(raw_url), REDACTED))
    query_start = raw_url.find("?")
    if query_start >= 0:
        query_end = fragment if fragment > query_start else len(raw_url)
        position = query_start + 1
        for pair in raw_url[query_start + 1 : query_end].split("&"):
            key, equals, value = pair.partition("=")
            value_start = position + len(key) + 1
            if equals and value and unquote(key).lower() in QUERY_SECRET_KEYS:
                spans.append(
                    (base + value_start, base + value_start + len(value), REDACTED)
                )
            elif (
                equals
                and value
                and "%" in key
                and _covers_anything(f"{unquote(key)}={unquote(value)}", depth, covers)
            ):
                # `?api%2Dkey=hunter2`: the encoded key hides it from the
                # keyword passes, which read the raw text
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
    for match in _counted(URL_RE.finditer(text)):
        raw_url = match.group(0)
        # Trailing sentence punctuation is handed back, and so is a trailing
        # backslash: in a serialised leaf it escapes the closing quote
        # (`…?sid=x\\"`), and a query-value span that ate it broke the JSON.
        raw_url = raw_url.rstrip(").,;\\")  # in one pass, as main does
        spans.extend(_url_component_spans(match.start(), raw_url, depth, covers))
    return spans


def collect_redaction_spans(
    text: str, *, _depth: int = 0, covers: Covers | None = None
) -> list[Span]:
    """Every redaction the additive rules would make to ``text``, as spans
    over it. Each rule reads the same, unmodified ``text``; the order of the
    list does not matter (`apply_redaction_spans` merges overlaps).
    ``covers`` lets the policy surface add its own patterns to the question
    asked of a percent-decoded query value."""
    return _additive_spans(text, _depth, covers)


#: Any key word, as the keyword patterns read it: the same alternation under
#: the same flag (`re.IGNORECASE`), so every case fold the patterns apply is
#: applied here too -- it is their `name` group on its own.
_ANY_KEY_RE = re.compile(rf"(?:{_secret_key_alternation()})", re.IGNORECASE)


def _may_hold_a_key(text: str) -> bool:
    """A necessary condition for a keyword match: the patterns' own key
    alternation occurs somewhere (one search)."""
    work(len(text))
    return _ANY_KEY_RE.search(text) is not None


def _additive_spans(text: str, depth: int, covers: Covers | None) -> list[Span]:
    """The additive rules, each over the same text. The three keyword
    passes need a key word in the text; when there is none they are skipped
    (their patterns try a qualifier at every position, which costs a scan
    of the whole text for nothing)."""
    keyed = _may_hold_a_key(text)
    spans = [
        *_url_spans(text, depth, covers),
        *(_keyword_sep_spans(text) if keyed else ()),
        *(_keyword_list_spans(text) if keyed else ()),
        *_authorization_spans(text),
        *_bearer_spans(text),
        *(_keyword_ws_spans(text) if keyed else ()),
        *_shape_spans(text),
    ]
    return widen_over_escapes(text, spans)


def widen_over_escapes(text: str, spans: list[Span]) -> list[Span]:
    """Start no span in the middle of a backslash escape.

    A serialised result reaches the redactor as JSON text, where
    `json.dumps` spells non-ASCII whitespace as `\\u2003`: a span that starts
    at the `u` would leave a lone `\\` -- invalid JSON, so a dict result
    silently became a string. A span preceded by an escaping backslash (an
    odd run of them) takes the backslash too, so the whole escape goes.
    """
    # the length of the backslash run ending just before each position, in
    # one pass: scanning back from every span start was quadratic in a long
    # run of backslashes with spans inside it
    work(len(text) + len(spans))
    runs = [0] * (len(text) + 1)
    for i, char in enumerate(text):
        runs[i + 1] = runs[i] + 1 if char == "\\" else 0
    widened: list[Span] = []
    for start, end, replacement in spans:
        if runs[start] % 2 == 1 and start < end:
            start -= 1
        widened.append((start, end, replacement))
    return widened


#: The redaction marker as the text may already hold it.
_MARKERS = (REDACTED, "%5BREDACTED%5D")
_MARKER_RE = re.compile(r"\[REDACTED\]|%5BREDACTED%5D")
#: Replacements that cover whatever they contain.
_COVERING = frozenset({*_MARKERS, ""})


#: What ends the run glued to a marker's end: whitespace, a query or
#: fragment delimiter, a quote, a backslash (an escape in a JSON string).
_GLUED_STOPS = frozenset("&#\"'\\,;([{<")


def _glued_run_end(text: str, start: int, end: int) -> int:
    """Where the run glued to a marker's end (at ``start``) stops: at
    whitespace, `&`, `#`, a quote, a backslash, the next marker or ``end``."""
    position = start
    while (
        position < end
        and not text[position].isspace()
        and text[position] not in _GLUED_STOPS
        and _MARKER_RE.match(text, position) is None
    ):
        position += 1
    work(position - start + 1)
    return position


def _is_a_tail(run: str) -> bool:
    """A glued run is the rest of a value only if it holds a letter or a
    digit. Punctuation alone (`token=[REDACTED]!` after a word the redactor
    took, `[REDACTED]:` before the next key) is left as written."""
    work(len(run) + 1)
    return any(c.isalnum() for c in run)


#: An ARN's partition right after the marker the redactor wrote over its
#: literal `arn`, under a key that names one (`secret_arn=[REDACTED]:aws:
#: secretsmanager:...`, `"SecretArn": "[REDACTED]:aws:..."`): the rest is the
#: resource name, not the rest of a secret.
_ARN_TAIL_RE = re.compile(r":aws(?:-[a-z]+)*:", re.IGNORECASE)
_ARN_KEY_BEFORE_RE = re.compile(
    r"arn\\?[\"']?[ \t]*[:=][ \t]*\\?[\"']?\Z", re.IGNORECASE
)
_ARN_KEY_REACH = 16


#: An ARN's body after the marker the redactor wrote over its literal
#: `arn`, under any key (`SECRET_ID=[REDACTED]:aws:secretsmanager:…`), also
#: percent-encoded in a query (`%3Aaws%3A…`): the partition, a service and
#: a colon, then either nothing more (the redactor's next marker follows)
#: or a region, a 12-digit account or none, and the resource. A tail with
#: no service field (`[REDACTED]:aws:tailtail`) is not one.
_ARN_COLON = r"(?::|%3A)"
_ARN_BODY_RE = re.compile(
    _ARN_COLON
    + r"aws(?:-[a-z]+)*"
    + _ARN_COLON
    + r"[a-z][a-z0-9-]*"
    + _ARN_COLON
    + r"(?:[a-z0-9-]*"
    + _ARN_COLON
    + r"(?:[0-9]{12})?"
    + _ARN_COLON
    + r".*)?",
    re.IGNORECASE | re.DOTALL,
)


def _is_resource_name_tail(text: str, marker: tuple[int, int], glued: int) -> bool:
    """The run glued to ``marker`` (ending at ``glued``) is an ARN's body,
    not the rest of a secret: an AWS partition under an `…arn` key, or a
    whole ARN body under any key."""
    work(glued - marker[1] + _ARN_KEY_REACH)
    if _ARN_BODY_RE.fullmatch(text, marker[1], glued) is not None:
        return True
    before = text[max(0, marker[0] - _ARN_KEY_REACH) : marker[0]]
    return (
        _ARN_TAIL_RE.match(text, marker[1]) is not None
        and _ARN_KEY_BEFORE_RE.search(before) is not None
    )


def _key_before_the_next_marker(text: str, glued: int) -> bool:
    """The run ends in a key's `=`, glued to the next marker."""
    return text[glued - 1] == "="


def _runs_on_into(markers: list[tuple[int, int]], i: int, glued: int, end: int) -> bool:
    """The glued run after marker ``i`` ends AT the next marker, inside the
    span: the value goes on after that marker."""
    return glued < end and i + 1 < len(markers) and markers[i + 1][0] == glued


def merge_redaction_spans(text: str, spans: list[Span]) -> list[Span]:
    """The disjoint spans `apply_redaction_spans` applies, ascending.

    Every marker already in the text -- `[REDACTED]`, and the URL rule's
    `%5BREDACTED%5D` -- is left exactly as it is:

    * a span that starts ON a marker keeps the marker and replaces only the
      run glued to its end, up to whitespace, `&`, `#`, a quote, a
      backslash, `,`, `;`, `(`, `[`, `{`, `<` or the next marker, and on
      across a marker it ends at, up to the span's end -- except after an
      ARN body, which it goes past only when the body ends in a key's `=`
      (so a key inside an ARN's resource after the redactor's marker in the
      ARN keeps its tail; disclosed): the redactor's keyword rule stops at the
      first character outside its value class, so `token=qVwYS81V!7Hb1DX8pP`
      reached this pass as `token=[REDACTED]!7Hb1DX8pP` (rev 17 left the
      tail). The run is replaced only if it holds a letter or a digit:
      punctuation alone after a marker (`token=[REDACTED]!`) stays. The rest of a query (`&page=2`), the next
      field or argument (`f(token=[REDACTED],page=2)`) and an ARN's body
      after the redacted `arn` (`:aws:secretsmanager:…`) are not glued;
    * a span that starts BEFORE a marker keeps its reach: each stretch of it
      outside the markers it overlaps is replaced, and each marker stays as
      written (`{"password": "Secret [REDACTED] 2024!"}` -- the redactor's
      keyword rule wrote a marker inside a strong key's value -- becomes
      `{"password": "[REDACTED][REDACTED][REDACTED]"}`; rev 16 dropped the
      whole span and left `Secret` and `2024!`). A stretch of only
      whitespace between markers is left.

    So a marker is never split, swallowed or re-spelled; run again over its
    own output, the pass keeps every marker and only adds. The rest merge: overlapping or nested spans
    become one `[REDACTED]`, except that a span inside one whose replacement
    covers it (the marker, or the empty string) is dropped.
    """
    work(len(text) + len(spans) * max(1, len(spans).bit_length()))  # scan, sort
    markers = [m.span() for m in _MARKER_RE.finditer(text)]
    marker_starts = [start for start, _ in markers]
    pieces: list[Span] = []
    for start, end, replacement in spans:
        i = bisect.bisect_right(marker_starts, start) - 1
        if i >= 0 and markers[i][1] > start:
            # starts on a marker: keep it, and replace only the run glued to
            # its end -- the rest of a value the redactor cut short at a
            # character outside its value class (`token=[REDACTED]!7Hb1DX8pP`)
            # When the run ends AT the next marker (the redactor wrote two
            # markers into one value: `password=KtJ0R$secret=KOmx@Zq9JTe`),
            # the run glued to that marker is the same value: follow it, up
            # to the span's end. One step per adjacent marker. An ARN body
            # stops it unless the body ends in a key's `=` (below).
            while True:
                glued = _glued_run_end(text, markers[i][1], end)
                if glued > markers[i][1] and _is_resource_name_tail(
                    text, markers[i], glued
                ):
                    # an ARN's body is kept. The chain goes on past it only
                    # when a key's `=` is glued to the next marker
                    # (`token=[REDACTED]:aws:s3:::x/token=[REDACTED]!TAIL`):
                    # that marker starts another value. Otherwise (the
                    # redactor's marker inside the ARN, `…:secretsmanager:`
                    # then the region) the rest is the ARN's own.
                    if _key_before_the_next_marker(text, glued) and _runs_on_into(
                        markers, i, glued, end
                    ):
                        i += 1
                        continue
                    break
                if glued > markers[i][1] and _is_a_tail(text[markers[i][1] : glued]):
                    pieces.append((markers[i][1], glued, replacement))
                if _runs_on_into(markers, i, glued, end):
                    i += 1
                    continue
                break
            continue
        position = start
        i += 1
        while i < len(markers) and markers[i][0] < end:
            work(1)
            if markers[i][0] > position:
                pieces.append((position, markers[i][0], replacement))
            position = max(position, markers[i][1])
            i += 1
        if position < end:
            pieces.append((position, end, replacement))
    return _merge_unmarked(text, pieces)


def _merge_unmarked(text: str, pieces: list[Span]) -> list[Span]:
    """Merge spans that overlap no marker (see `merge_redaction_spans`)."""
    ordered = sorted(
        (
            piece
            for piece in pieces
            if piece[0] < piece[1] and not text[piece[0] : piece[1]].isspace()
        ),
        key=lambda span: (span[0], -span[1], span[2] != REDACTED),
    )
    merged: list[Span] = []
    for start, end, replacement in ordered:
        if merged and start < merged[-1][1]:
            previous_start, previous_end, previous_replacement = merged[-1]
            if end <= previous_end and previous_replacement in _COVERING:
                continue
            merged[-1] = (previous_start, max(end, previous_end), REDACTED)
            continue
        merged.append((start, end, replacement))
    return merged


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
    merged = merge_redaction_spans(text, spans)
    work(len(text) + len(merged))
    pieces: list[str] = []
    position = 0
    for start, end, replacement in merged:
        pieces.append(text[position:start])
        pieces.append(replacement)
        position = end
    pieces.append(text[position:])
    return "".join(pieces)


# --------------------------------------------------------------- JSON text

#: A JSON string token, the loop unrolled: a run of plain characters is one
#: step, not one per character (the per-character alternation kept engine
#: state for every character of a long string).
_JSON_STRING_RE = re.compile(r"\"[^\"\\]*(?:\\.[^\"\\]*)*\"")
_JSON_ESCAPE_RE = re.compile(r"\\u[0-9a-fA-F]{4}|\\.", re.DOTALL)


def _is_object_key(text: str, end: int) -> bool:
    """Is the string token ending at ``end`` an object key (the next
    non-whitespace character is `:`)?"""
    rest = end
    while rest < len(text) and text[rest] in " \t\r\n":
        rest += 1
    return rest < len(text) and text[rest] == ":"


def _clip_to_json_strings(text: str, spans: list[Span]) -> list[Span]:
    """When ``text`` is a JSON document, confine every span to the contents
    of string VALUES, whole escapes at a time: a replacement then never
    touches a delimiting quote, a bracket, a separator, a bare scalar or an
    object key, and never splits an escape. So the document stays a
    document, and no two keys of an object can collide into one (rev 15's
    board: random-looking keys redacted to the same marker collapsed a map
    to its last entry). Anything a span covered outside a string value is
    left as it is."""
    head = text.lstrip()[:1]
    if head not in ("{", "[", '"') or not spans:
        return spans
    try:
        json.loads(text)
    except (ValueError, RecursionError):
        return spans
    work(3 * len(text))
    values: list[tuple[int, int]] = []
    for match in _JSON_STRING_RE.finditer(text):
        start, end = match.span()
        if _is_object_key(text, end):
            continue
        values.append((start, end))
    starts = [start for start, _ in values]
    # a position inside a string is a boundary unless it is inside an escape
    # (backslashes only occur in strings in a valid document)
    boundary = bytearray(b"\x01") * (len(text) + 1)
    for escape in _JSON_ESCAPE_RE.finditer(text):
        boundary[escape.start() + 1 : escape.end()] = bytes(
            escape.end() - escape.start() - 1
        )
    clipped: list[Span] = []
    for a, b, replacement in spans:
        i = max(0, bisect.bisect_right(starts, a) - 1)
        while i < len(values) and values[i][0] < b:
            start, end = values[i]
            low, high = max(a, start + 1), min(b, end - 1)
            work(1)
            if low < high:
                while not boundary[low]:
                    low -= 1
                while not boundary[high]:
                    high += 1
                clipped.append((low, high, replacement))
            i += 1
    return clipped


def redact_additive(
    text: str, *, covers: Covers | None = None, extra: list[Span] | None = None
) -> str:
    """Apply the additive rules (and ``extra`` spans) to ``text``: the
    output of the redactor that ran before them."""
    spans = collect_redaction_spans(text, covers=covers)
    if extra:
        spans.extend(extra)
    return apply_redaction_spans(text, _clip_to_json_strings(text, spans))
