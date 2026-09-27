"""The additive redaction rules (Consiliency/pmcp#234).

`sanitize_auth_diagnostic` and `PolicyManager.redact_secrets` run the
redactor's own rules unchanged, then the additive rules
(`pmcp.redaction_additive`, rev 10's) over that output. Four guarantees:

1. **Floor, by construction.** The additive rules only replace text with the
   marker, so every piece the redactor removed stays removed. Checked against
   an independent copy of the redactor (`tests/_main_redactor.py`): the base
   pass is that redactor exactly, and every stretch of the final output
   outside a marker appears, in order, in the base output.
2. **JSON.** When the base output is a JSON document, the final output is
   one too, and a structured result that came back a dict still does.
3. **Linear time and memory** of the additive pass on every entry point, on a
   deterministic work count and tracemalloc peaks -- never on the clock.
4. **Markers** are never split, doubled into one another, or re-spelled.

Plus what the additive rules are for: credentials the base pass keeps are
removed, and prose the base pass keeps survives.
"""

from __future__ import annotations

import json
import multiprocessing
import random
import re
import string
import tracemalloc
import uuid
from collections.abc import Callable

import pytest

import pmcp.redaction_additive as A
from pmcp.auth import AUTH_SECRET_QUERY_KEYS, _sanitize_base, sanitize_auth_diagnostic
from pmcp.policy.policy import DEFAULT_REDACTION_PATTERNS, PolicyManager
from tests import _main_redactor as M
from tests import _redaction_grammar as G

MAIN_PATTERNS = M.compiled_default_patterns()


def _ours_e(text: str) -> str:
    return sanitize_auth_diagnostic(text, max_length=None)


def _ours_p(text: str) -> str:
    return PolicyManager().redact_secrets(text)


def _main_e(text: str) -> str:
    return M.sanitize_auth_diagnostic(text, max_length=None)


def _main_p(text: str) -> str:
    return M.redact_secrets(text, MAIN_PATTERNS)


def _ours_process(obj: object) -> object:
    return PolicyManager().process_output(obj, redact=True, max_bytes=G.BIG)["result"]


def _main_process(obj: object) -> object:
    if isinstance(obj, str):
        return _main_p(obj)
    out = _main_p(json.dumps(obj, indent=2))
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


# ================================================================ corpora ==== #

PROSE = """\
The token bucket rate limiter refused the call; the secret ingredient is
patience. Your session expired, so the cookie consent banner reappeared and the
password reset flow sent a status code 401 back. The tokenizer choked on unicode
input and the encoded payload was base64. Set the PMCP_FEEDBACK_TOKEN environment
variable to enable submission; the API key is read from the env store. Keys are
rotated weekly. A secret to good code is small functions. Missing bearer token:
the bearer of bad news said a Bearer Token is required. Session re-use is off.

Traceback (most recent call last):
  File "/home/u/.venv/lib/python3.10/site-packages/httpx/_client.py", line 1013, in send
    response = self._send_handling_auth(
  File "/home/u/code/pmcp/src/pmcp/client/manager.py", line 2641, in _run_transport
    raise ResourceServerAuthError("invalid_token", str(exc)) from exc
UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0
httpx.HTTPStatusError: Client error '401 Unauthorized' for url 'https://api.example.com/v1/tools'
<pmcp.client.manager.ClientManager object at 0x7f3a2b1c4d50> pod pmcp-7d9f8b6c5-x2k9q
JSONDecodeError SSLCertVerificationError IPv6Address Ed25519PrivateKey X509Cert
Rsa2048Key Oauth2ClientError Base64UrlEncoder Sha256HashAlgorithm parseJSON2Dict

status_code=401 error_code=invalid_grant token_type=Bearer expires_in=3600
token_endpoint=https://auth.example/oauth/token code=404 token v2 is out
{"code": -32601, "message": "Method not found", "data": {"code": "not_found"}}
{"code": "not_found", "message": "no such tool", "request_id": "550e8400-e29b-41d4-a716-446655440000"}
WWW-Authenticate: Bearer realm="api", error="insufficient_scope", scope="read write"
secret_arn=arn:aws:secretsmanager:us-east-1:123456789012:secret:MySecret-a1b2c3
exit code 137; error code 0x80070005; zip code 94105; status code 503
error_code=AADSTS50011 sqlstate_code=42P01 error_codes=[50011] reason_code=E-1234

commit 3843d2f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6 (HEAD -> main, origin/main)
Author: Example <dev@example.test>
Date:   2026-09-23T04:12:00+00:00
    docs(plans): plan CONSENT and PKGID (#239)
sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
Successfully installed pmcp-1.19.2 httpx-0.27.0 anyio-4.4.0 exceptiongroup-1.2.1
Downloading pmcp-1.19.2-py3-none-any.whl (312 kB) for x86_64-linux-gnu
TLS: ECDHE-RSA-AES256-GCM-SHA384 / TLS_AES_128_GCM_SHA256 negotiated at 20260923T041200Z
tests/test_redaction.py::test_s11_an_approved_project_policy_cannot_widen_the_user_policy PASSED
detailed-234-redactor-shape-based-20260922-1130.md build-20260922123456 s3bucket2024
[REDACTED] [REDACTED_EMAIL] ghp_ sk- xoxb- AKIA
"""

# --------------------------------------------------------------------------- #
# The credential corpus: (label, text, must_vanish, must_survive). The label is
# the CLASS the entry stands for, not the vendor; a vendor nobody listed is
# still caught if its shape is here. The four the issue measured come first.
#
# Samples deliberately do NOT match GitHub secret-scanning detectors (a Stripe
# key with 24 body characters, a DigitalOcean token with 64 hex, a Slack token
# with a numeric segment and a mixed-case body): push protection rejects a
# commit that carries one, however synthetic. Each sample keeps the SHAPE the
# rule needs and nothing more; the Slack rule is exercised by the issue's own
# `xoxb-2444-2444-abcdefghijklmnop`, which the detector leaves alone.
# --------------------------------------------------------------------------- #

CREDENTIALS: list[tuple[str, str, str, list[str]]] = [
    (
        "credential-in-url-path",
        "failed to fetch https://api.example.com/v1/sk-live-abc123def456/status",
        "sk-live-abc123def456",
        ["failed to fetch https://api.example.com/v1/", "/status"],
    ),
    (
        "aws-access-key-id",
        "unexpected value AKIAIOSFODNN7EXAMPLE in the request",
        "AKIAIOSFODNN7EXAMPLE",
        ["unexpected value", "in the request"],
    ),
    (
        "prefixed-random-body",
        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        "16C7e42F292c6912E7710c838347Ae178B4a",
        [],
    ),
    (
        "slack-documented-shape",
        "xoxb-2444-2444-abcdefghijklmnop",
        "2444-2444-abcdefghijklmnop",
        [],
    ),
    (
        "bare-random-alnum",
        "bare 4eC39HqLyjWDarjtT1zdp7dc here",
        "4eC39HqLyjWDarjtT1zdp7dc",
        ["bare", "here"],
    ),
    (
        "base64-with-slashes",
        "aws secret wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY leaked",
        "wJalrXUtnFEMI",
        ["aws secret", "leaked"],
    ),
    (
        "base64-padded",
        "basic dXNlcjpwYXNzd29yZA== auth",
        "dXNlcjpwYXNzd29yZA",
        ["basic", "auth"],
    ),
    (
        "prefixed-hex-body",
        "dop_v1_9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822c",
        "9f86d081884c7d659a2feaa0c55ad015",
        [],
    ),
    (
        "prefixed-short-sample",
        "sk_test_4eC39HqLyjWDar7dc",
        "4eC39HqLyjWDar7dc",
        [],
    ),
    (
        "prefixed-low-entropy-with-digits",
        "sk-abcdef123456",
        "abcdef123456",
        [],
    ),
    (
        "prefixed-long-body",
        "sk-proj-Ab3dEf6GhI9jKl2MnO5pQr8StU1vWx4Yz7AbCdEfGhIjKlMnOpQrStUvWxYz",
        "Ab3dEf6GhI9jKl2MnO5pQr8StU1vWx4Yz7AbCdEfGhIjKlMnOpQrStUvWxYz",
        [],
    ),
    (
        "google-api-key",
        "AIzaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBxY",
        "9tSrke72PouQMnMX",
        [],
    ),
    (
        "webhook-path-keeps-route",
        "https://hooks.example/services/T0123ABCD/B0123ABCD/a1B2c3D4e5F6g7H8i9J0k1L2",
        "a1B2c3D4e5F6g7H8i9J0k1L2",
        ["https://hooks.example/services/"],
    ),
    (
        "pem-block",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQ\n-----END RSA PRIVATE KEY-----",
        "MIIEow",
        [],
    ),
    (
        "json-quoted-value",
        '{"password": "hunter2"}',
        "hunter2",
        ['"password"'],
    ),
    (
        "flag-with-shaped-value",
        "--token abc123def456 --password hunter2",
        "abc123def456",
        ["--token", "--password"],
    ),
    (
        "flag-with-passphrase",
        "--password correct-horse-battery-staple",
        "correct-horse-battery-staple",
        ["--password"],
    ),
    (
        "camelcase-key",
        "accessToken=AbC123dEf456GhI",
        "AbC123dEf456GhI",
        ["accessToken="],
    ),
    (
        "oauth-code-bare",
        "code=super-secret",
        "super-secret",
        ["code="],
    ),
    (
        "oauth-code-qualified",
        "auth_code=SplxlOBeZQQYbYS6WxSbIA",
        "SplxlOBeZQQYbYS6WxSbIA",
        ["auth_code="],
    ),
    (
        "oauth-code-hex",
        "code=a1b2c3d4e5f6a7b8c9d0",
        "a1b2c3d4e5f6a7b8c9d0",
        ["code="],
    ),
    (
        "keyword-colon-low-entropy",
        "password: hunter2",
        "hunter2",
        ["password:"],
    ),
    (
        "header-with-qualifier",
        "X-Auth-Token: abc123",
        "abc123",
        ["X-Auth-Token:"],
    ),
    (
        "cookie-header",
        "Set-Cookie: session=abc; Path=/",
        "abc",
        ["Set-Cookie:", "Path=/"],
    ),
    (
        "session-id",
        "session=013G8iK4noj1iNbSTqJVFEX6",
        "013G8iK4noj1iNbSTqJVFEX6",
        ["session="],
    ),
    (
        "bearer-header",
        "Authorization: Bearer abc.def",
        "abc.def",
        ["Authorization:"],
    ),
    (
        "bearer-bare",
        "sent Bearer test-token",
        "test-token",
        ["sent Bearer"],
    ),
    (
        "jwt",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1",
        "eyJzdWIiOiJzZWNyZXQifQ",
        [],
    ),
]


_PROSE_WORDS = (
    "the token bucket secret ingredient session expired password reset cookie "
    "consent bearer of bad news rate limit refused call key keys rotated weekly "
    "code status exit error invalid grant not found request failed because "
    "timeout retry server client scope read write admin user tenant api jwt "
    "saml assertion ticket sid tokens secrets passwords sessions cookies"
).split()
_PROSE_IDENTIFIERS = (
    "ResourceServerAuthError UnicodeDecodeError IPv6Address Ed25519PrivateKey "
    "X509Cert Rsa2048Key Oauth2ClientError parseJSON2Dict toHTML5String sha256sum"
).split()


def _random_prose(rng: random.Random) -> str:
    """Prose and diagnostic text made of plain words, numbers, identifiers,
    digests, ids and timestamps. A keyword is followed only by a word or a
    number: the design says a digit-bearing non-word after a bare keyword is
    a credential, and this corpus is the other half of that contract."""

    def words() -> str:
        return " ".join(rng.choice(_PROSE_WORDS) for _ in range(rng.randint(2, 8)))

    def number() -> str:
        return str(rng.randint(0, 99999))

    def hexdigits(n: int) -> str:
        return "".join(rng.choice("0123456789abcdef") for _ in range(n))

    clauses = [
        words,
        lambda: f"{words()} {number()}",
        lambda: f"status_code={number()} error_code={rng.choice(_PROSE_WORDS)}_{rng.choice(_PROSE_WORDS)}",
        lambda: f"exit code {number()}",
        lambda: f"in {rng.choice(_PROSE_IDENTIFIERS)} at 0x{hexdigits(12)}",
        lambda: f"commit {hexdigits(40)}",
        lambda: f"request_id {uuid.UUID(int=rng.getrandbits(128))}",
        lambda: f"at 2026-09-{rng.randint(10, 28)}T{rng.randint(10, 23)}:{rng.randint(10, 59)}:00+00:00",
        lambda: f"installed pmcp-1.{rng.randint(0, 30)}.{rng.randint(0, 9)}",
        lambda: f"Bearer {rng.choice(_PROSE_WORDS).capitalize()}",
        lambda: f"code={number()}",
        lambda: f"--{rng.choice(_PROSE_WORDS)} {rng.choice(_PROSE_WORDS)}",
        lambda: f'{{"code": -{rng.randint(32000, 32768)}, "message": "{words()}"}}',
    ]
    return rng.choice([" ", ", ", "; ", ". ", "\n"]).join(
        rng.choice(clauses)() for _ in range(rng.randint(3, 6))
    )


_DIFF_FUZZ_KEYS = [
    "password",
    "token",
    "api_key",
    "secret",
    "client_secret",
    "authorization",
    "code",
    "auth",
    "credentials",
    "Authorization",
    "pwd",
    "session",
    "cookie",
    "private_key",
    "msg",
    "text",
    "note",
]
_DIFF_FUZZ_PIECES = [
    *"abcXYZ0129 _-/+=.:;,&!@#$%^*()[]{}<>|~`'\"\\\n\t",
    "é",
    "\u3000",
    "\xa0",
    "password=",
    "token: ",
    "Bearer ",
    "https://h/?token=x&",
    '"password": "',
    "[",
    "]",
]


def _json_fuzz_corpus() -> list[dict]:
    """The claude seat's random-JSON fuzz (seed 7), committed: 1 500 random
    objects whose keys are credential and neutral names and whose values are
    random strings of delimiters, quotes, backslashes, whitespace (U+3000,
    NBSP) and redactor trigger words, nested lists and objects up to three
    deep, and JSON scalars."""
    rng = random.Random(7)

    def value(depth: int = 0) -> object:
        roll = rng.random()
        if roll < 0.6 or depth > 2:
            return "".join(
                rng.choice(_DIFF_FUZZ_PIECES) for _ in range(rng.randint(0, 14))
            )
        if roll < 0.8:
            return [value(depth + 1) for _ in range(rng.randint(0, 3))]
        if roll < 0.9:
            return {
                rng.choice(_DIFF_FUZZ_KEYS): value(depth + 1)
                for _ in range(rng.randint(1, 3))
            }
        return rng.choice([123, -32601, True, None, 1.5])

    return [
        {rng.choice(_DIFF_FUZZ_KEYS): value() for _ in range(rng.randint(1, 4))}
        for _ in range(1500)
    ]


_PRINTABLE = (
    "".join(c for c in string.printable if c not in "\t\n\r\x0b\x0c") + "éßñ日本語🙂"
)
_DELIMITERS = frozenset(" \t\"',;&()[]{}")


def _random_passwords(rng: random.Random, count: int) -> list[str]:
    return [
        "".join(rng.choice(_PRINTABLE) for _ in range(rng.randint(4, 24)))
        for _ in range(count)
    ]


def _single_quoted(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _keyed_forms(password: str) -> list[tuple[str, str, str]]:
    """(text, encoded value as it appears in the text, probe).

    The probe is the first four characters of the value as encoded -- what a
    leak would show. Bare forms (`password=x`, a header) exist only for
    passwords without delimiters: an unquoted syntax cannot carry a space or
    a quote at all.
    """
    forms: list[tuple[str, str, str]] = []
    for encoded, text in [
        (json.dumps(password), json.dumps({"password": password})),
        (
            json.dumps(password, ensure_ascii=False),
            json.dumps({"password": password}, ensure_ascii=False),
        ),
        (_single_quoted(password), "{'password': " + _single_quoted(password) + "}"),
    ]:
        forms.append((text, encoded, encoded[1:5]))
    if not any(c in _DELIMITERS for c in password) and not password.startswith("="):
        # `key==x` reads as a comparison (`if token == expected`), so a bare
        # value cannot begin with `=`; it can carry one anywhere else.
        forms.append((f"password={password}", password, password[:4]))
        forms.append((f"X-Api-Key: {password}", password, password[:4]))
    return forms


# ============================================================ the base pass ==== #


def test_the_constants_are_the_redactors() -> None:
    assert A.QUERY_SECRET_KEYS == AUTH_SECRET_QUERY_KEYS
    assert tuple(DEFAULT_REDACTION_PATTERNS) == tuple(M.DEFAULT_REDACTION_PATTERNS)


def _texts(rows: list[G.Row]) -> list[str]:
    out: list[str] = []
    for row in rows:
        if "o" in row:
            out.append(json.dumps(row["o"], indent=2))
            continue
        out.append(row["t"])
        out.extend(
            json.dumps({"t": row["t"]}, ensure_ascii=a, indent=i)
            for _, a, i in G._SPELLINGS
        )
    return out


def test_the_base_pass_is_the_redactor_unchanged() -> None:
    """`_sanitize_base` is the vendored redactor, output for output, on every
    tier-1 text, the prose, the credentials and the JSON fuzz."""
    texts = _texts(G.corpus(1)) + [PROSE] + [c[1] for c in CREDENTIALS]
    texts += [json.dumps(o, indent=2) for o in _json_fuzz_corpus()]
    assert [t for t in texts if _sanitize_base(t) != _main_e(t)] == []


# ================================================================== floor ==== #

_MARKER_SPLIT_RE = re.compile(r"\[REDACTED\]|%5BREDACTED%5D")


def _is_additive_of(ours: str, base: str) -> bool:
    """Is ``ours`` ``base`` with some stretches replaced by the marker (or
    removed)? Every stretch of ``ours`` outside a marker must appear in
    ``base``, in order, after the previous one."""
    position = 0
    for piece in _MARKER_SPLIT_RE.split(ours):
        if not piece:
            continue
        found = base.find(piece, position)
        if found < 0:
            return False
        position = found + len(piece)
    return True


def _floor_violations(texts: list[str]) -> list[str]:
    bad = []
    policy = PolicyManager()
    for text in texts:
        for label, ours, base in (
            ("E", _ours_e(text), _main_e(text)),
            ("P", policy.redact_secrets(text), _main_p(text)),
        ):
            if not _is_additive_of(ours, base):
                bad.append(f"{label} {text[:120]!r}")
    return bad


def test_floor_the_output_only_adds_markers_to_the_redactors_output() -> None:
    """On half the tier-1 texts (every spelling), the board rows of four
    review rounds, the prose, the credentials and the JSON fuzz; the slow
    tier runs a quarter of tier 2."""
    texts = _texts(G.corpus(1)[::2]) + _board_texts() + [PROSE]
    texts += [c[1] for c in CREDENTIALS] + [json.dumps(o) for o in _json_fuzz_corpus()]
    assert _floor_violations(texts) == []


def test_floor_nothing_main_removes_survives_on_any_surface() -> None:
    """The piece-level differential, all 12 surfaces and the dict path, on a
    third of tier 1 (the slow tier runs the rest and a quarter of tier 2):
    every piece of a value the redactor removed is removed here too, and
    every dict it kept a dict stays one."""
    bad = []
    for row in G.corpus(1)[::3]:
        main = G.observe(row, _main_e, _main_p, _main_process)
        ours = G.observe(row, _ours_e, _ours_p, _ours_process)
        for surface in G.worse_surfaces(row, main, ours):
            bad.append(f"{surface}: {row.get('t', row.get('o'))!r}")
    assert bad == [], bad[:20]


def test_floor_with_the_default_400_character_cut() -> None:
    """`sanitize_auth_diagnostic` cuts after both passes. The cut output is
    still the redactor's output with markers written in: every stretch of
    it outside a marker occurs, in order, in the redactor's FULL output.
    (It can show text past the redactor's own 400-character window when an
    additive marker is shorter than what it replaced -- text the redactor
    kept, only cut.)"""
    texts = _texts(G.corpus(1)[::5]) + _board_texts()
    texts.append(
        "-----BEGIN RSA PRIVATE KEY-----\n"
        + "A" * 300
        + "\n-----END RSA PRIVATE KEY-----"
        + " word" * 120
        + " TAIL"
    )
    bad = [
        t[:80]
        for t in texts
        if not _is_additive_of(sanitize_auth_diagnostic(t), _main_e(t))
    ]
    assert bad == [], bad[:10]


@pytest.mark.slow
def test_floor_on_tier_2() -> None:
    rows = [row for i, row in enumerate(G.corpus(1)) if i % 3] + G.corpus(2)[
        len(G.corpus(1)) :: 4
    ]
    assert _floor_violations(_texts(rows)) == []
    bad = [
        row.get("t", row.get("o"))
        for row in rows
        if G.worse_surfaces(
            row,
            G.observe(row, _main_e, _main_p, _main_process),
            G.observe(row, _ours_e, _ours_p, _ours_process),
        )
    ]
    assert bad == [], bad[:20]


def test_floor_mutant_a_restoring_pass_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the additive pass ever put back text the base pass removed, the
    floor check fails (here: an additive pass that returns the raw input)."""
    import pmcp.auth

    monkeypatch.setattr(
        pmcp.auth, "redact_additive", lambda text, **_: "password=hunter2x"
    )
    assert _floor_violations(["password=hunter2x"]) != []


# =================================================================== JSON ==== #


def test_json_documents_stay_documents() -> None:
    """Wherever the base output of a JSON text parses, the final output
    parses too, on both surfaces; and a structured result the base
    `process_output` returned as a dict still comes back a dict."""
    bad = []
    policy = PolicyManager()
    for obj in _json_fuzz_corpus():
        for indent in (None, 2):
            text = json.dumps(obj, indent=indent)
            for label, ours, base in (
                ("E", _ours_e(text), _main_e(text)),
                ("P", policy.redact_secrets(text), _main_p(text)),
            ):
                try:
                    json.loads(base)
                except json.JSONDecodeError:
                    continue
                try:
                    json.loads(ours)
                except json.JSONDecodeError:
                    bad.append(f"{label} {text[:100]!r}")
        if isinstance(_main_process(obj), dict) and not isinstance(
            _ours_process(obj), dict
        ):
            bad.append(f"POd {obj!r}"[:120])
    assert bad == [], bad[:10]


def test_a_span_across_a_string_boundary_is_confined_to_the_string() -> None:
    """The clipping itself: a span that crosses a delimiting quote and a
    separator (no additive rule makes one on the corpora -- they stop at
    quotes -- so the guard is exercised directly) is applied inside the
    string only, and the document stays a document."""
    text = '{"a": "xsecretx", "b": 1}'
    start = text.index("secretx")
    span = [(start, text.index('"b"') + 1, A.REDACTED)]
    assert json.loads(A.redact_additive(text, extra=span)) == {
        "a": "x[REDACTED]",
        "b": 1,
    }


def test_json_mutant_without_clipping_breaks_a_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(A, "_clip_to_json_strings", lambda text, spans: spans)
    with pytest.raises(json.JSONDecodeError):
        test_a_span_across_a_string_boundary_is_confined_to_the_string()


def test_object_keys_are_left_alone_so_entries_never_collide() -> None:
    """Two random-looking keys redacted to the same marker collapsed a map to
    its last entry (rev 15's board). The additive pass writes only into
    string VALUES of a document."""
    obj = {"Xk9mQ2vLp3RtY7wBaaQ1": 1, "Pq7rS2tUv9WxY3zAbC4d": 2}
    assert _ours_process(obj) == obj
    doc = json.dumps({"Xk9mQ2vLp3RtY7wBaaQ1": "Pq7rS2tUv9WxY3zAbC4d"})
    assert json.loads(_ours_e(doc)) == {"Xk9mQ2vLp3RtY7wBaaQ1": "[REDACTED]"}


def test_object_keys_mutant_clipping_into_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rev 15's clipping, which let spans into object keys, collides them."""
    monkeypatch.setattr(A, "_is_object_key", lambda text, end: False)
    with pytest.raises(AssertionError):
        test_object_keys_are_left_alone_so_entries_never_collide()


# ================================================================ markers ==== #


def _four_surfaces() -> dict[str, tuple[Callable[[str], str], Callable[[str], str]]]:
    """(ours, the redactor's) for E, P, POs and POd, each as text."""
    policy = PolicyManager()

    def ours_pod(text: str) -> str:
        return json.dumps(_ours_process({"t": text}))

    def main_pod(text: str) -> str:
        return json.dumps(_main_process({"t": text}))

    return {
        "E": (_ours_e, _main_e),
        "P": (policy.redact_secrets, _main_p),
        "POs": (lambda t: str(_ours_process(t)), lambda t: str(_main_process(t))),
        "POd": (ours_pod, main_pod),
    }


_MARKER_TEXTS = [
    "see https://h.example/cb?token=abc123def456&page=2 now",
    "https://api.example.com/v1/items?token=abc&page=3&limit=50",
    "https://h/?api_key=k1&password=p2&secret=s3&pwd=p4&passwd=p5&x=1",
    "password=[REDACTED] then token: [REDACTED] and Bearer [REDACTED]",
    "token=%5BREDACTED%5D&next=https://h/?q=1",
    "[REDACTED]abc123def456 [REDACTED] ghp_16C7e42F292c6912E7710c838347Ae178B4a",
]


def _marker_problems(texts: list[str]) -> list[str]:
    """On all four surfaces: every marker of the redactor's output is still
    there, whole and with its spelling, in order; no `REDACTED` appears
    outside a whole marker; and (E, P) the additive pass leaves its own
    output as it is -- it never marks a marker again."""
    bad = []
    for text in texts:
        for label, (ours, main) in _four_surfaces().items():
            base, out = main(text), ours(text)
            base_markers = _MARKER_SPLIT_RE.findall(base)
            out_markers = _MARKER_SPLIT_RE.findall(out)
            position = 0
            for marker in base_markers:
                found = out.find(marker, position)
                if found < 0:
                    bad.append(
                        f"{label}: lost or re-spelled {marker} in {text!r} -> {out!r}"
                    )
                    break
                position = found + len(marker)
            if "REDACTED" in _MARKER_SPLIT_RE.sub("", out):
                bad.append(f"{label}: a split marker in {out!r}")
            if len(out_markers) < len(base_markers):
                bad.append(f"{label}: fewer markers in {out!r}")
            again = (
                A.redact_additive(out)
                if label == "E"
                else PolicyManager()._redact_additive(out)
                if label == "P"
                else out
            )
            if again != out:
                bad.append(f"{label}: the additive pass changes its own output {out!r}")
    return bad


def test_markers_are_kept_whole_and_spelled_as_written_on_every_surface() -> None:
    texts = _MARKER_TEXTS + _texts(G.corpus(1)[::7]) + _board_texts()
    assert _marker_problems(texts) == [], _marker_problems(texts)[:10]


def test_markers_mutant_a_policy_value_run_through_a_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rev 15's policy pass (no marker skip, no stop at `&` in a query, and
    a merge that swallowed markers) re-spelled the URL marker and lost the
    rest of the query."""
    import pmcp.policy.policy as P

    monkeypatch.setattr(P, "starts_with_marker", lambda value: False)
    monkeypatch.setattr(P, "_url_query_ranges", lambda text: [])
    monkeypatch.setattr(A, "_MARKER_RE", re.compile(r"(?!)"))
    assert _marker_problems(_MARKER_TEXTS[:2]) != []


def test_a_policy_value_in_a_query_ends_at_the_next_parameter() -> None:
    policy = PolicyManager()
    out = policy.redact_secrets("x https://h/?q=1&token=abc123def456zz&page=2#top y")
    assert "abc123def456zz" not in out and "&page=2" in out, out


# ============================================================= what it adds ==== #


@pytest.mark.parametrize(("label", "text", "secret", "survive"), CREDENTIALS)
def test_the_additive_rules_remove_each_credential(
    label: str, text: str, secret: str, survive: list[str]
) -> None:
    for surface in (_ours_e, _ours_p):
        out = surface(text)
        assert secret not in out, (label, out)


def test_prose_the_redactor_keeps_the_additive_rules_keep_too() -> None:
    """The additive rules' own false positives: none on the prose corpus or
    on 2 000 generated prose lines (the base pass's own stand)."""
    assert _ours_e(PROSE) == _main_e(PROSE)
    assert _ours_p(PROSE) == _main_p(PROSE)
    rng = random.Random(9234)
    for _ in range(2000):
        text = _random_prose(rng)
        assert _ours_e(text) == _main_e(text), text
        assert _ours_p(text) == _main_p(text), text


def test_keyed_passwords_from_every_printable_character_are_removed() -> None:
    rng = random.Random(1234)
    for password in _random_passwords(rng, 300):
        for text, encoded, probe in _keyed_forms(password):
            if len(probe) < 4 or probe in text.replace(encoded, "", 1):
                continue
            for surface in (_ours_e, _ours_p):
                assert probe not in surface(text), (text, surface(text))


def test_an_upper_case_scheme_does_not_hide_userinfo() -> None:
    for text in ("HTTPS://user:s3cr3tpass@h.example/p", "Http://u:hunter2x@h.example/"):
        for surface in (_ours_e, _ours_p):
            out = surface(text)
            assert "s3cr3tpass" not in out and "hunter2x" not in out, out


def test_operator_pattern_padding_is_kept_whole() -> None:
    """The first-separator split kept a base64 secret and replaced its
    padding; the additive split redacts the whole match."""
    policy = PolicyManager()
    policy._redaction_regexes = [re.compile(r"[A-Za-z0-9+/]{16,}={1,2}")]
    assert "abcdabcdabcdabcdabcd" not in policy.redact_secrets(
        "x abcdabcdabcdabcdabcd== y"
    )


# ========================================================= linear, counted ==== #


def _entry_points() -> dict[str, Callable[[str], object]]:
    policy = PolicyManager()
    return {
        "E": lambda t: sanitize_auth_diagnostic(t, max_length=None),
        "P": policy.redact_secrets,
        "POs": lambda t: policy.process_output(t, redact=True, max_bytes=G.BIG),
        "POd": lambda t: policy.process_output({"t": t}, redact=True, max_bytes=G.BIG),
    }


def _work(run: Callable[[str], object], text: str) -> int:
    A.WORK = [0]
    try:
        run(text)
        return A.WORK[0]
    finally:
        A.WORK = None


def _peak(run: Callable[[str], object], text: str) -> int:
    tracemalloc.start()
    try:
        run(text)
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


#: Growth allowed per 4x of input: linear, plus the n log n of sorting
#: spans, plus 5%. A quadratic term gives 16x.
_PER_4X = 4 * (16 / 14) * 1.05

#: Every shape four review rounds found (the additive rules' share of
#: them), and the families derived from the rules' loops.
LINEAR_SHAPES: dict[str, Callable[[int], str]] = {
    "closers after a Bearer value": lambda n: "Bearer x" + ")" * n + "a",
    "escaped quotes after Authorization": lambda n: "Authorization: x" + '\\"' * (n // 2) + "a",
    "line breaks before a Bearer value": lambda n: "x Bearer" + "\n" * n + " abc",
    "line breaks before a keyword value": lambda n: "token" + "\n" * n + " abc12",
    "keyword matches after resource names": lambda n: "arn:x " * (n // 12) + "password=abc123 " * (n // 32),
    "keyword lists after resource names": lambda n: "arn:x " * (n // 12) + 'tokens: ["a1b2c3d4"] ' * (n // 42),
    "keys inside resource names": lambda n: "arn:x:secret=abc123 " * (n // 20),
    "backslash run before escapes": lambda n: "\\" * (n // 2) + "\\u00e9password=x" * 8,
    "empty query pairs": lambda n: "https://h/?" + "&" * n + "a",
    "secret-keyed query pairs": lambda n: "https://h/?" + "password=x&" * (n // 11),
    "re-encoded query value": lambda n: "https://h/?q=+" + "sk-abcdef/" * (n // 10),
    "key words after Bearer": lambda n: "x Bearer" + "api_key" * (n // 7) + '"',
    "joined key run": lambda n: "token-" * (n // 6) + "=abc123def",
    "word boundaries": lambda n: "a-" * (n // 2),
    "operator run": lambda n: "password" + ":=" * (n // 2),
    "markers in the input": lambda n: "password=" + "[REDACTED]" * (n // 10),
    "Bearer values in a JSON document": lambda n: json.dumps({f"k{i}": "Bearer x" for i in range(n // 20)}),
    "tokens in a JSON document": lambda n: json.dumps({f"k{i}": "ghp_abcdefghij1234" for i in range(n // 30)}),
    "long joined keys": lambda n: ("a" * 170 + "_token" * 14 + "=Hunter2abc9 ") * (n // 267),
    "an unterminated keyword list": lambda n: "tokens: [" + " " * n + "x",
    "a list in a JSON leaf": lambda n: json.dumps({"t": "tokens: [" + " " * n}),
}  # fmt: skip


def _work_superlinear(shape: Callable[[int], str], sizes: tuple[int, ...]) -> list[str]:
    found = []
    for label, run in _entry_points().items():
        counts = [_work(run, shape(n)) for n in sizes]
        for small, large in zip(counts, counts[1:]):
            if large > _PER_4X * max(small, 1):
                found.append(f"{label}: {counts}")
    return found


@pytest.mark.parametrize("name", sorted(LINEAR_SHAPES))
def test_the_additive_pass_does_linear_work(name: str) -> None:
    """16 KB -> 64 KB on every entry point: the work count grows linearly.
    Deterministic, so one step of 4x shows a quadratic term (16x); the slow
    tier adds the step to 256 KB."""
    assert _work_superlinear(LINEAR_SHAPES[name], (16_384, 65_536)) == []


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(LINEAR_SHAPES))
def test_the_additive_pass_does_linear_work_to_256_kb(name: str) -> None:
    assert _work_superlinear(LINEAR_SHAPES[name], (16_384, 65_536, 262_144)) == []


def _memory_superlinear(
    shape: Callable[[int], str], labels: tuple[str, ...], sizes: tuple[int, ...]
) -> list[str]:
    found = []
    for label in labels:
        run = _entry_points()[label]
        run(shape(1_024))  # first-use allocations out of the way
        peaks = [_peak(run, shape(n)) for n in sizes]
        for small, large in zip(peaks, peaks[1:]):
            # a constant allowance for list over-allocation at small sizes;
            # a quadratic term is megabytes over it
            if large > _PER_4X * small + 512 * 1024:
                found.append(f"{label}: {peaks}")
    return found


@pytest.mark.parametrize("name", sorted(LINEAR_SHAPES))
def test_every_entry_point_uses_linear_memory(name: str) -> None:
    """Peak traced memory of the whole engine call, 8 KB -> 32 KB: linear,
    up to a fixed 512 KiB allowance. The slow tier adds 128 KB and the
    dict path."""
    assert _memory_superlinear(LINEAR_SHAPES[name], ("E",), (8_192, 32_768)) == []


@pytest.mark.slow
@pytest.mark.parametrize("name", sorted(LINEAR_SHAPES))
def test_every_entry_point_uses_linear_memory_to_128_kb(name: str) -> None:
    assert (
        _memory_superlinear(LINEAR_SHAPES[name], ("E", "POd"), (8_192, 32_768, 131_072))
        == []
    )


def _rev12_in_resource_name(text: str) -> Callable[[int], bool]:
    ranges = [(m.start(), m.end()) for m in A._RESOURCE_NAME_RE.finditer(text)]

    def inside(position: int) -> bool:
        A.work(len(ranges))  # asking every name
        return any(a <= position < b for a, b in ranges)

    return inside


def test_linearity_mutant_asking_every_resource_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shape = LINEAR_SHAPES["keyword lists after resource names"]
    run = _entry_points()["P"]
    monkeypatch.setattr(A, "_in_resource_name", _rev12_in_resource_name)
    small, large = _work(run, shape(2_048)), _work(run, shape(8_192))
    assert large > _PER_4X * small


_SWEEP_CHUNKS = 8


@pytest.mark.slow
@pytest.mark.parametrize("chunk", range(_SWEEP_CHUNKS))
def test_generated_shapes_do_linear_work(chunk: int) -> None:
    """Every 8th shape derived from the redactor's regular expressions and
    the additive rules' loops (`tests/_redaction_shapes.py`), screened on the
    work count at 4 KB -> 16 KB on every entry point in worker processes; a
    flagged shape is re-measured at 16 KB -> 256 KB and must be linear."""
    names = sorted(_all_shapes())[chunk :: _SWEEP_CHUNKS * 8]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(min(20, multiprocessing.cpu_count())) as pool:
        flagged = [
            n
            for found in pool.imap_unordered(_screen, names, chunksize=25)
            for n in found
        ]
    shapes = _all_shapes()
    bad = {}
    for name in flagged:
        for label, run in _entry_points().items():
            counts = [_work(run, shapes[name](n)) for n in (16_384, 65_536, 262_144)]
            if any(b > _PER_4X * max(a, 1) for a, b in zip(counts, counts[1:])):
                bad[name] = (label, counts)
    assert bad == {}, bad


def _all_shapes() -> dict[str, Callable[[int], str]]:
    from tests import _redaction_shapes as S

    return {**S.shapes(S.redactor_patterns()), **S.compositions(), **S.atom_repeats()}


_SCREEN: dict[str, Callable[[int], str]] = {}


def _screen(name: str) -> list[str]:
    if not _SCREEN:
        _SCREEN.update(_all_shapes())
    bound = 4 * (14 / 12) * 1.05
    for run in _entry_points().values():
        if _work(run, _SCREEN[name](16_384)) > bound * max(
            _work(run, _SCREEN[name](4_096)), 1
        ):
            return [name]
    return []


# ============================================================ board inputs ==== #


def _board_texts() -> list[str]:
    """Inputs four review rounds used against the replay design, kept as a
    differential corpus: multi-pair and wide-separator grids, joiner-glued
    schemes, punctuation inside header values, URLs, OAuth bodies, resource
    names, long keys and numbers."""
    texts: list[str] = []
    for k1 in (
        "password",
        "api_key",
        "token",
        "secret",
        "code",
        "Authorization",
        "cookie",
    ):
        for op in ("=", ":", ": "):
            for brk in ("\n", "\t", "\r\n", "\n\n", " \n"):
                for k2 in ("password", "token", "session"):
                    texts.append(f"{k1}{op}{brk}{k2}: hunter2x")
    for pre in ("--", "-", "x-", "X-", "x_", "access_", ""):
        for v in ("hunter2x", "s3cr3tvalue", "abc-def_1.2", "Zm9vOmJhcg=="):
            texts.append(f"mycli {pre}bearer {v} --verbose")
    for head in ("abcdef", "q7Zp2Lk9Wx4R"):
        for ch in "\"'()[]{}<>=|\\!@#$%^&*`?/:":
            texts.append(f"x Bearer {head}{ch}SECRETPART end")
            texts.append(f"Authorization: Bearer {head}{ch}SECRETPART end")
    texts += [
        "see https://user:pa@ssw0rdXq@host.example/p ok",
        "https://h.example:99999/" + "a" * 420 + "?q=MYSECRETVAL",
        "https://h/?q=+" + "aaaaaaaaaa.bbbbbbbbbb.cccccccccc/" * 20,
        "https://H.EXAMPLE:0443/p?token=abc",
        "grant_type=urn:ietf:params:oauth:grant-type:token-exchange&client_secret=Hunter2abcX9",
        "connect urn:db;user=admin;password=Hunter2abcX9",
        "urn:secret:Hunter2abcX9",
        "DATABASE_PASSWORD_FOR_REPLICATION_USER_ACCOUNT=Hunter2abcX9",
        "passwordForTheProductionDatabaseServer=Hunter2abcX9",
        "Bearer " + "1" * 24,
        "Bearer 0x" + "ab" * 32,
        "code=482913",
        "x Bearer abcdef=SECRETPART end",
        "token_type=Bearer expires_in=3600",
    ]
    return texts


def _fold_variants() -> list[str]:
    """Every key word with each of its letters replaced by every character
    Python's `re.IGNORECASE` treats as equal to it, plus the multi-character
    folds (`ß` for `ss`, `ẞ`, `ﬁ`/`ﬂ`/`ﬀ` ligatures) that full case
    folding has and simple folding does not."""
    ascii_class = re.compile("[a-z0-9_-]", re.IGNORECASE)
    fold: dict[str, set[str]] = {}
    for letter in string.ascii_lowercase:
        fold[letter] = {letter.upper()}
    for i in range(0x80, 0x110000):
        char = chr(i)
        if ascii_class.fullmatch(char):
            for letter in string.ascii_lowercase:
                if re.fullmatch(letter, char, re.IGNORECASE):
                    fold[letter].add(char)
    texts = []
    for key in sorted(A.ADDITIVE_SECRET_KEYS) + ["api_key", "apikey", "private_key"]:
        for index, letter in enumerate(key):
            for other in sorted(fold.get(letter.lower(), set())):
                texts.append(key[:index] + other + key[index + 1 :])
        texts.append(key.replace("ss", "\u00df").replace("s", "\u017f", 1))
        texts.append(key.replace("ss", "\u1e9e"))
        texts.append(key.replace("fi", "\ufb01").replace("ff", "\ufb00"))
    return texts


def test_the_key_prefilter_never_hides_a_keyword_match() -> None:
    """The keyword passes are skipped only when the prefilter finds no key.
    The prefilter IS their key alternation under their flag, so it can only
    miss what they miss: checked here against the passes' own patterns, on
    every key with every case-fold variant of each letter and the
    multi-character folds, each in a separator and a whitespace context."""
    for key in _fold_variants():
        for text in (f"{key}=hunter22x", f"{key} abc123def456", f'{key}: ["a1b2c3d4"]'):
            matched = any(
                pattern.search(text) is not None
                for pattern in (A._KEYWORD_SEP_RE, A._KEYWORD_LIST_RE, A._KEYWORD_WS_RE)
            )
            if matched:
                assert A._may_hold_a_key(text), text


def test_multi_character_folds_do_not_match_on_this_interpreter() -> None:
    """Python's `re` applies simple case folding only: `(?i)password` does not
    match `paßword` (nor `paẞword`), so neither the redactor's keyword rule nor
    the additive rules read it as a key -- the prefilter changes nothing
    there (a reported multi-character-fold gap, checked and not present)."""
    assert re.search("(?i)password", "pa\u00dfword") is None
    assert re.search("(?i)password", "pa\u1e9eword") is None
    for surface in (_ours_e, _main_e):
        assert "hunter2" in surface("pa\u00dfword=@hunter2")


# ============================================= regex quantifier structure ==== #


def _quantifier_shapes() -> dict[str, Callable[[int], str]]:
    from tests import _redaction_shapes as S

    return S.quantifier_shapes(S.redactor_patterns())


_CAPS = {4_096: 1.0, 16_384: 2.0, 65_536: 5.0}


def _over_cap(name: str, sizes: tuple[int, ...], leaf: bool = True) -> list[str]:
    import time

    shape = _QSHAPES[name] if _QSHAPES else _quantifier_shapes()[name]
    over = []
    surfaces = dict(_entry_points())
    if leaf:
        surfaces["E-leaf"] = lambda t: sanitize_auth_diagnostic(
            json.dumps({"t": t}), max_length=None
        )
    for size in sizes:
        text = shape(size)
        for label, run in surfaces.items():
            started = time.perf_counter()
            run(text)
            elapsed = time.perf_counter() - started
            if elapsed > _CAPS[size]:
                over.append(f"{name} {label} {size}: {elapsed:.2f}s")
    return over


_QSHAPES: dict[str, Callable[[int], str]] = {}


def _screen_quantifier(name: str) -> list[str]:
    if not _QSHAPES:
        _QSHAPES.update(_quantifier_shapes())
    return _over_cap(name, (4_096,), leaf=False)


def test_no_regex_backtracks_on_its_own_quantifier_structure() -> None:
    """Every quantifier of every redactor regex, reached by the shortest text
    that leads to it, one unit repeated to 4 KB, and each of six failing
    tails: on the engine, in worker processes, under a 1 s cap (rev 15's
    list body took 30 s here; now every shape takes milliseconds). The work
    counter cannot see a regex's own backtracking; this can."""
    names = sorted(_quantifier_shapes())
    assert len(names) > 500
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(min(20, multiprocessing.cpu_count())) as pool:
        over = [
            o
            for found in pool.imap_unordered(_screen_quantifier, names, chunksize=10)
            for o in found
        ]
    assert over == [], over[:10]


@pytest.mark.slow
@pytest.mark.parametrize("chunk", range(4))
def test_no_regex_backtracks_at_64_kb_on_any_surface(chunk: int) -> None:
    """The same shapes at 4, 16 and 64 KB on every entry point and inside a
    JSON string leaf, under generous caps (1, 2 and 5 s)."""
    names = sorted(_quantifier_shapes())[chunk::4]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(min(20, multiprocessing.cpu_count())) as pool:
        over = [
            o
            for found in pool.imap_unordered(_full_quantifier, names, chunksize=5)
            for o in found
        ]
    assert over == [], over[:10]


def _full_quantifier(name: str) -> list[str]:
    if not _QSHAPES:
        _QSHAPES.update(_quantifier_shapes())
    return _over_cap(name, (4_096, 16_384, 65_536))


def test_list_backtracking_mutant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rev 15's list body (three `\\s*` that could split one run) restored:
    `tokens: [` + 2 KB of spaces and no `]` is over the cap again."""
    old_body = (
        r"(?P<list>\[\s*(?:(?:\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|-?[0-9][0-9.eE+-]*"
        r"|null|true|false)\s*,\s*)*(?:(?:\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'"
        r"|-?[0-9][0-9.eE+-]*|null|true|false))?\s*,?\s*\])"
    )
    monkeypatch.setattr(
        A, "_KEYWORD_LIST_RE", re.compile(A._KEYWORD_KEY_SEP + old_body, re.IGNORECASE)
    )
    import time

    started = time.perf_counter()
    sanitize_auth_diagnostic("tokens: [" + " " * 2048 + "x", max_length=None)
    assert time.perf_counter() - started > 1.0
