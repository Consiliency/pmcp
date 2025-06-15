"""The additive redaction rules (Consiliency/pmcp#234).

`sanitize_auth_diagnostic` and `PolicyManager.redact_secrets` run the
redactor's own rules with the Consiliency/pmcp#401 URL boundary correction, then the additive rules
(`pmcp.redaction_additive`, rev 10's) over that output. Four guarantees:

1. **Floor, by construction.** The additive rules only replace text with the
   marker, so every piece the redactor removed stays removed. Checked against
   an independent copy of the redactor (`tests/_main_redactor.py`): the base
   pass only tightens that independent corrected snapshot. The historical
   corpus retains its ordered-stretch floor; collision suffixes on protected
   mapping-key markers are checked independently with field-preservation tests.
2. **JSON.** When the base output is a JSON document, the final output is
   one too, and a structured result that came back a dict still does.
3. **Linear time and memory** of the additive pass on every entry point, on a
   deterministic work count and tracemalloc peaks -- never on the clock.
4. **Markers** are preserved by generic value rules. A protected mapping key
   or whole qualified query value is replaced atomically, including markers it held.

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
from collections.abc import Callable, Iterator

import pytest

import pmcp.redaction_additive as A
from pmcp.auth import (
    AUTH_DIAGNOSTIC_SECRET_KEYS,
    AUTH_SECRET_QUERY_KEYS,
    _sanitize_base,
    sanitize_auth_diagnostic,
)
from pmcp.policy.policy import DEFAULT_REDACTION_PATTERNS, PolicyManager
from tests import _main_redactor as M
from tests import _redaction_grammar as G

MAIN_PATTERNS = M.compiled_default_patterns()


_POLICY: PolicyManager | None = None


@pytest.fixture(autouse=True)
def _one_policy_per_test() -> Iterator[None]:
    """Each test builds its PolicyManager once, under its own HOME: building
    one per input re-discovered the project and the operator's policy files
    hundreds of thousands of times, and redaction only reads the compiled
    patterns (Consiliency/pmcp#372: suite time)."""
    global _POLICY
    _POLICY = None
    yield
    _POLICY = None


def _policy() -> PolicyManager:
    global _POLICY
    if _POLICY is None:
        _POLICY = PolicyManager()
    return _POLICY


def _ours_e(text: str) -> str:
    return sanitize_auth_diagnostic(text, max_length=None)


def _ours_p(text: str) -> str:
    return _policy().redact_secrets(text)


def _main_e(text: str) -> str:
    return M.sanitize_auth_diagnostic(text, max_length=None)


def _main_p(text: str) -> str:
    return M.redact_secrets(text, MAIN_PATTERNS)


def _ours_process(obj: object) -> object:
    return _policy().process_output(obj, redact=True, max_bytes=G.BIG)["result"]


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
    tier-1 text, the prose, the credentials and the JSON fuzz. The newly
    tightened key/value/suffix cases have independent regressions below."""
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


@pytest.mark.parametrize("depth", [0, 1, 3])
@pytest.mark.parametrize(
    "href", ["", "https://example.test/watch", "https://example.test/watch?v=abc"]
)
def test_url_escape_credential_keys_keep_distinct_entries(
    depth: int, href: str
) -> None:
    keys = [
        "AKIAABCDEFGHIJKLMNOP",
        "xoxb-" + "123456789012-abcdefghijklmnopqrstuvwx",
        "AIza" + "A1b2" * 9,
        "xoxb-" + '987654321098-abcdefghijklmnopqrstuvwx\nquoted"suffix',
    ]
    rows = {
        "keys": {
            "[REDACTED]": "reserved",
            **{key: f"entry-{i}" for i, key in enumerate(keys)},
        },
        "snippet": f'<Card href="{href}">Person</Card>',
    }
    nested = json.dumps(rows)
    for _ in range(depth):
        nested = json.dumps(nested)
    payload = {
        "content": [{"type": "text", "text": nested}],
        "structuredContent": rows,
        "isError": False,
    }
    serialized = json.dumps(payload)
    policy = _policy()
    processed = policy.process_output(payload, redact=True, max_bytes=200000)
    assert processed["raw_size"] == len(json.dumps(payload, indent=2).encode("utf-8"))
    assert processed["truncated"] is False
    assert processed["summary"] is None
    assert (
        policy.process_output(payload, redact=False, max_bytes=200000)["result"]
        == payload
    )
    outputs = [
        json.loads(_ours_e(serialized)),
        json.loads(policy.redact_secrets(serialized)),
        json.loads(
            policy.process_output(serialized, redact=True, max_bytes=200000)["result"]
        ),
        processed["result"],
    ]
    for result in outputs:
        assert isinstance(result, dict)
        assert all(key.split("\n")[0] not in json.dumps(result) for key in keys)
        decoded = result["content"][0]["text"]
        for _ in range(depth + 1):
            decoded = json.loads(decoded)
        assert decoded == result["structuredContent"]
        assert decoded["keys"]["[REDACTED]"] == "reserved"
        assert sorted(decoded["keys"].values()) == [
            *[f"entry-{i}" for i in range(len(keys))],
            "reserved",
        ]


# Derive the regression grammar from every public rule family, rather than
# mirroring the callback's decision. Separator aliases are grammar literals.
_ROUND3_QUERY_NAMES = sorted(
    AUTH_SECRET_QUERY_KEYS
    | AUTH_DIAGNOSTIC_SECRET_KEYS
    | A.ADDITIVE_SECRET_KEYS
    | A.QUERY_SECRET_KEYS
    | A.WEAK_SECRET_KEYS
    | {"api-key", "private-key", "privatekey"}
)
_ROUND3_KEYWORD_NAMES = sorted(
    # These three are query-only; Bearer uses whitespace, not `=`/`:`.
    set(_ROUND3_QUERY_NAMES) - {"key", "ticket", "bearer"}
)


def _round3_payloads_on_surfaces(rows: dict, depth: int) -> list:
    nested = json.dumps(rows)
    for _ in range(depth):
        nested = json.dumps(nested)
    payload = {"content": [{"type": "text", "text": nested}], "structuredContent": rows}
    serialized = json.dumps(payload)
    policy = _policy()
    return [
        _ours_e(serialized),
        policy.redact_secrets(serialized),
        policy.process_output(serialized, redact=True, max_bytes=10**6)["result"],
        policy.process_output(payload, redact=True, max_bytes=10**6)["result"],
    ]


def _round3_rows_on_surfaces(rows: dict, depth: int) -> list[dict]:
    return [
        _round3_decode_payload(output, depth)
        for output in _round3_payloads_on_surfaces(rows, depth)
    ]


def _round3_decode_payload(output: object, depth: int) -> dict:
    if isinstance(output, str):
        output = json.loads(output)
    assert isinstance(output, dict)
    text = output["content"][0]["text"]
    for _ in range(depth + 1):
        text = json.loads(text)
    assert text == output["structuredContent"]
    return text


# These earlier base/default-pattern rules corrupt nested JSON before the
# additive key pass runs (Consiliency/pmcp#290/409/410). Every family still has
# the unconditional secrecy assertion; structured agreement is additionally
# required for the additive-only corrections, with no parse-error catch.
_ROUND3_LEGACY_STRUCTURE_FAILURES = {
    "api-key",
    "api_key",
    "apikey",
    "authorization",
    "aws_access",
    "aws_secret",
    "client_secret",
    "passwd",
    "password",
    "pwd",
    "secret",
}


@pytest.mark.parametrize("name", _ROUND3_QUERY_NAMES)
@pytest.mark.parametrize("depth", range(4))
@pytest.mark.parametrize("tail", ["\\", "\\\\", ".\\).,;\\", "\\\\)."])
@pytest.mark.parametrize("core", ["hunter2xKP", ""])
def test_round3_query_grammar_has_no_secret_suffix(
    name: str, depth: int, tail: str, core: str
) -> None:
    from urllib.parse import unquote

    spelling = name.upper() if depth == 1 else name
    if depth == 2:
        spelling = f"%{ord(name[0]):02x}" + name[1:]
    href = f"https://example.test/p?{spelling}={core}{tail}"
    free = f"see {href} next"
    policy = _policy()
    for output in (
        _ours_e(free),
        policy.redact_secrets(free),
        policy.process_output(free, redact=True, max_bytes=10**6)["result"],
    ):
        value = output.split("=", 1)[1].rsplit(" next", 1)[0].strip()
        assert unquote(value) == "[REDACTED]"
    rows = {
        "snippet": f'<Card href="{href}">😀 Person</Card>',
        "status": {"code": "not_found", "credentials": "include", "auth": "basic"},
    }
    payloads = _round3_payloads_on_surfaces(rows, depth)
    for output in payloads:
        assert "hunter2xKP" not in str(output)
        text = output if isinstance(output, str) else json.dumps(output)
        # The two copies have independently known syntax-escape depths.
        # Earlier default patterns may destroy syntax, but may not leave
        # MORE escapes than those boundaries or any punctuation of the value.
        runs = re.findall(r"(?:\[REDACTED\]|%5BREDACTED%5D)([\\).,;]*)", text)
        assert len(runs) == 2
        for run, syntax in zip(runs, (2 ** (depth + 2) - 1, 1)):
            assert not run.strip("\\")
            assert len(run) <= syntax
    for index, raw in enumerate(payloads):
        if name in _ROUND3_LEGACY_STRUCTURE_FAILURES and (
            index != 0 or name == "authorization"
        ):
            continue
        output = _round3_decode_payload(raw, depth)
        href_out = output["snippet"].split('href="', 1)[1].split('"', 1)[0]
        assert unquote(href_out.split("=", 1)[1]) == "[REDACTED]"
        assert output["snippet"].endswith('">😀 Person</Card>')
        assert output["status"] == rows["status"]


@pytest.mark.parametrize(
    "name", ["credential", "credentials", "Credential", "cred%65ntial"]
)
@pytest.mark.parametrize("tail", [".", "..", ").", ";;;", ",)"])
@pytest.mark.parametrize("depth", range(4))
def test_round3_weak_value_punctuation_is_secret(
    name: str, tail: str, depth: int
) -> None:
    from urllib.parse import unquote

    rows = {
        "snippet": f'<a href="https://example.test/p?{name}=hunterword{tail}">P</a>'
    }
    for output in _round3_rows_on_surfaces(rows, depth):
        href = output["snippet"].split('href="', 1)[1].split('"', 1)[0]
        assert unquote(href.split("=", 1)[1]) == "[REDACTED]"


@pytest.mark.parametrize("name", _ROUND3_KEYWORD_NAMES)
@pytest.mark.parametrize("separator", ["=", ": "])
@pytest.mark.parametrize("depth", [0, 1, 3])
def test_round3_keyword_mapping_keys_are_secret(
    name: str, separator: str, depth: int
) -> None:
    key = f"{name}{separator}hunter2xKP"
    for href in ("", "https://example.test/watch", "https://example.test/watch?v=abc"):
        rows = {
            "keys": {"[REDACTED]": "keep", key: "protected"},
            "snippet": f'<Card href="{href}">P</Card>',
        }
        payloads = _round3_payloads_on_surfaces(rows, depth)
        for output in payloads:
            assert "hunter2xKP" not in str(output)
        if name in _ROUND3_LEGACY_STRUCTURE_FAILURES:
            continue
        for raw in payloads:
            output = _round3_decode_payload(raw, depth)
            assert "hunter2xKP" not in json.dumps(output)
            assert output["keys"] == {"[REDACTED]": "keep", "[REDACTED]#2": "protected"}


@pytest.mark.parametrize("name", _ROUND3_QUERY_NAMES)
@pytest.mark.parametrize("depth", [0, 1, 3])
def test_round3_url_mapping_keys_are_secret(name: str, depth: int) -> None:
    key = f"https://example.test/p?{name}=hunter2xKP"
    for href in ("", "https://example.test/watch?v=abc"):
        rows = {"keys": {key: "protected"}, "snippet": f'<Card href="{href}">P</Card>'}
        payloads = _round3_payloads_on_surfaces(rows, depth)
        for output in payloads:
            assert "hunter2xKP" not in str(output)
        if name in _ROUND3_LEGACY_STRUCTURE_FAILURES:
            continue
        for raw in payloads:
            output = _round3_decode_payload(raw, depth)
            assert output["keys"] == {"[REDACTED]": "protected"}


@pytest.mark.parametrize("depth", [0, 1, 3])
def test_round3_key_markers_are_value_independent(depth: int) -> None:
    mine = "AKIA" + "M" + "0" * 15
    outputs = []
    for secret in ("AKIA" + "C" + "7" * 15, "AKIA" + "T" + "7" * 15):
        rows = {mine: "mine", secret: "theirs", "[REDACTED]": "keep"}
        outputs.append(_round3_rows_on_surfaces(rows, depth))
    assert outputs[0] == outputs[1]
    assert (
        outputs[0]
        == [{"[REDACTED]#2": "mine", "[REDACTED]#3": "theirs", "[REDACTED]": "keep"}]
        * 4
    )


def test_round3_fully_escaped_credential_key_is_not_dependent_on_other_spans() -> None:
    key = "AKIA" + "Q7RZ" + "X2PL" * 3
    escaped = "".join(f"\\u{ord(char):04x}" for char in key)
    for extra in ("", ', "note": "ordinary"'):
        doc = '{"' + escaped + '": 1' + extra + "}"
        assert A.collect_redaction_spans(doc) == []
        for redact in (_ours_e, _ours_p):
            result = json.loads(redact(doc))
            assert key not in result
            assert result["[REDACTED]"] == 1


@pytest.mark.parametrize("depth", range(4))
@pytest.mark.parametrize("authority", ["example.test", "example.test:99999"])
def test_round3_suffix_on_case_and_port_fallback(depth: int, authority: str) -> None:
    from urllib.parse import unquote

    for prefix in ("", "GET ", "url="):
        rows = {
            "snippet": f'<a href="{prefix}https://{authority}/p?token=tokPieceQ7\\\\">😀</a>'
        }
        for output in _round3_rows_on_surfaces(rows, depth):
            href = output["snippet"].split('href="', 1)[1].split('"', 1)[0]
            assert unquote(href.split("?token=", 1)[1]) == "[REDACTED]"


def test_postcap_case_variant_benign_keys_keep_their_entries() -> None:
    rows = {
        "snippet": '<a href="HTTPS://Example.test/p?q=a+b&x=%41">P</a>',
        "keys": {
            "HTTPS://Example.test/p?q=a+b": "first",
            "HTTPS://example.test/p?q=a%20b": "second",
            "HTTPS://Example.test/x": "third",
            "https://example.test/x": "fourth",
        },
    }
    for output in _round3_rows_on_surfaces(rows, 1):
        assert output == rows


@pytest.mark.parametrize("scheme", ["HTTPS://", "Https://"])
@pytest.mark.parametrize("name", ["q", "state"])
def test_postcap_case_variant_opaque_plus_value_stays_secret(
    scheme: str, name: str
) -> None:
    pieces = ("BNVOQ9Al", "x7Kp2QzR")
    url = f"{scheme}example.test/cb?{name}={'+'.join(pieces)}=&x=1"
    policy = _policy()
    for raw in (
        _ours_e("see " + url + " next"),
        policy.redact_secrets("see " + url + " next"),
        policy.process_output("see " + url + " next", redact=True)["result"],
        *_round3_payloads_on_surfaces({"link": url}, 1),
    ):
        assert all(piece not in str(raw) for piece in pieces)


_POSTCAP_QUERY_FAMILIES = sorted(
    AUTH_SECRET_QUERY_KEYS
    | AUTH_DIAGNOSTIC_SECRET_KEYS
    | A.ADDITIVE_SECRET_KEYS
    | A.QUERY_SECRET_KEYS
    | A.WEAK_SECRET_KEYS
    | {"api-key", "private-key", "privatekey"}
)


def test_postcap_query_grammar_covers_every_existing_family() -> None:
    assert A._QUERY_SECRET_KEYS | {"api-key", "private-key", "privatekey"} == set(
        _POSTCAP_QUERY_FAMILIES
    )
    assert A.WEAK_SECRET_KEYS <= A._QUERY_SECRET_KEYS


@pytest.mark.parametrize("family", _POSTCAP_QUERY_FAMILIES)
@pytest.mark.parametrize("form", ["x_{}", "my{}", "{}_suffix", "scope-{}-value"])
def test_postcap_qualified_query_values_are_wholly_secret(
    family: str, form: str
) -> None:
    from urllib.parse import quote, unquote

    name = form.format(family)
    # Grammar-derived names, not the production classifier's result.
    assert re.fullmatch(r"[A-Za-z0-9_-]+", name)
    for depth, tail in enumerate(("", "\\\\", ").,;", ";;;\\")):
        spelling = (
            name,
            name.upper(),
            "%" + f"{ord(name[0]):02x}" + name[1:],
            quote(name).replace("_", "%5F").replace("-", "%2D"),
        )[depth]
        scheme = ("https://", "HTTPS://", "Https://", "https://")[depth]
        authority = "example.test:99999" if depth == 3 else "example.test"
        href = f"{scheme}{authority}/p?{spelling}=hunterword{tail}"
        policy = _policy()
        for output in (
            _ours_e(f"see {href} next"),
            policy.redact_secrets(f"see {href} next"),
            policy.process_output(f"see {href} next", redact=True)["result"],
        ):
            value = output.split("=", 1)[1].rsplit(" next", 1)[0].strip()
            assert unquote(value) == "[REDACTED]", output
        # A suffix on the name avoids the preexisting default patterns that
        # consume JSON escapes after names such as `password=`. All families
        # still require complete secrecy on the free-text surfaces above.
        structural_name = name + "_field"
        rows = {
            "link": f"{scheme}{authority}/p?{structural_name}=hunterword{tail}",
            "status": {"auth": "basic", "credentials": "include", "code": "not_found"},
        }
        for output in _round3_rows_on_surfaces(rows, depth):
            assert unquote(output["link"].split("=", 1)[1]) == "[REDACTED]"
            assert output["status"] == rows["status"]


@pytest.mark.parametrize("name", ["prıvate_Key_suffix", "x_ſecret", "x_apİ_key"])
def test_postcap_query_names_keep_existing_ignorecase_partners(name: str) -> None:
    from urllib.parse import quote

    for spelling in (name, quote(name)):
        for output in _round3_rows_on_surfaces(
            {"link": f"HTTPS://example.test/p?{spelling}=hunterword\\\\"}, 1
        ):
            assert "hunterword" not in output["link"]
            assert "\\" not in output["link"]


@pytest.mark.parametrize("name", ["q", "state", "trace", "offset", "mode", "auth!name"])
def test_postcap_nonmatching_query_names_and_prose_are_kept(name: str) -> None:
    rows = {
        "link": f"HTTPS://example.test/p?{name}=plainword\\\\",
        "status": {"auth": "basic", "credentials": "include", "code": "not_found"},
    }
    for output in _round3_rows_on_surfaces(rows, 1):
        assert output == rows


def test_final_identifier_api_dot_key_is_a_whole_query_value() -> None:
    rows = {"link": "HTTPS://example.test/p?api.key=plainword\\\\", "note": "keep"}
    assert json.loads(_main_e(json.dumps(rows))) == rows
    assert json.loads(_main_p(json.dumps(rows))) == rows
    expected = {"link": "HTTPS://example.test/p?api.key=[REDACTED]", "note": "keep"}
    for output in _round3_rows_on_surfaces(rows, 1):
        assert output == expected


_FINAL_IDENTIFIER_JOINERS = sorted(set(string.punctuation) - set("_-&#=\"'<>\\%")) + [
    "%20",
    "%2E",
    "%2B",
    "%5C",
    "é",
    "%C3%A9",
]


@pytest.mark.parametrize("joiner", _FINAL_IDENTIFIER_JOINERS)
def test_final_identifier_joiners_withhold_complete_query_values(joiner: str) -> None:
    from urllib.parse import unquote

    for depth, family in enumerate(("credential", "auth_suffix", "private_key_suffix")):
        scheme = "https://" if depth == 0 and joiner.isascii() else "HTTPS://"
        href = f"{scheme}example.test/p?scope{joiner}{family}=hiddenword\\\\).;"
        policy = _policy()
        for output in (
            _ours_e("see " + href + " next"),
            policy.redact_secrets("see " + href + " next"),
            policy.process_output("see " + href + " next", redact=True)["result"],
        ):
            assert (
                unquote(output.split("=", 1)[1].rsplit(" next", 1)[0]) == "[REDACTED]"
            )
        rows = {"link": f'<a href="{href}">😀</a>', "note": "keep"}
        for output in _round3_rows_on_surfaces(rows, depth):
            url = output["link"].split('href="', 1)[1].split('"', 1)[0]
            assert unquote(url.split("=", 1)[1]) == "[REDACTED]"
            assert output["link"].endswith('">😀</a>')
            assert output["note"] == "keep"


@pytest.mark.parametrize("family", _POSTCAP_QUERY_FAMILIES)
def test_final_identifier_existing_families_at_name_end(family: str) -> None:
    from urllib.parse import quote, unquote

    name = "scope.pre" + family + "_field"
    for depth, spelling in enumerate(
        (name, name.upper(), quote(name).replace(".", "%2E"))
    ):
        href = f"HTTPS://example.test/p?{spelling}=firstword\\secondword\\\\"
        rows = {"link": href, "sibling": "plainword"}
        for output in _round3_rows_on_surfaces(rows, depth):
            assert unquote(output["link"].split("=", 1)[1]) == "[REDACTED]"
            assert "firstword" not in str(output)
            assert "secondword" not in str(output)
            assert output["sibling"] == "plainword"


@pytest.mark.parametrize("ascii_only", [True, False])
@pytest.mark.parametrize("depth", [0, 1, 3])
def test_final_identifier_semantic_names_and_quote_boundaries(
    ascii_only: bool, depth: int
) -> None:
    from urllib.parse import quote, unquote

    for name in ("scope.credentıal", "x:prıvate_Key_suffix"):
        for scheme, spelling in (("HTTPS://", name), ("https://", quote(name))):
            rows = {
                "link": f'<a href="{scheme}example.test/p?{spelling}=hiddenword\\\\).;">😀</a>',
                "benign": "HTTPS://example.test/p?q=plainword\\\\",
            }
            nested = json.dumps(rows, ensure_ascii=ascii_only)
            for _ in range(depth):
                nested = json.dumps(nested, ensure_ascii=ascii_only)
            payload = {"content": [{"text": nested}], "structuredContent": rows}
            doc = json.dumps(payload, ensure_ascii=ascii_only)
            policy = _policy()
            for raw in (
                _ours_e(doc),
                policy.redact_secrets(doc),
                policy.process_output(doc, redact=True, max_bytes=10**6)["result"],
                policy.process_output(payload, redact=True, max_bytes=10**6)["result"],
            ):
                output = _round3_decode_payload(raw, depth)
                url = output["link"].split('href="', 1)[1].split('"', 1)[0]
                assert unquote(url.split("=", 1)[1]) == "[REDACTED]"
                assert output["link"].endswith('">😀</a>')
                assert output["benign"] == rows["benign"]


@pytest.mark.parametrize(
    "name", ["user.credential", "x:auth", "scope.private_key_suffix"]
)
def test_final_identifier_url_keys_keep_siblings_and_secret_pieces(name: str) -> None:
    key = f"HTTPS://example.test/p?{name}=hiddenword"
    rows = {"keys": {key: "protected", "[REDACTED]": "keep"}, "sibling": "keep"}
    for output in _round3_rows_on_surfaces(rows, 1):
        assert output == {
            "keys": {"[REDACTED]#2": "protected", "[REDACTED]": "keep"},
            "sibling": "keep",
        }
    for limit in (32, 400, None):
        raw = "log " * 65 + key + "\\ next"
        uncut = _ours_e(raw)
        cut = sanitize_auth_diagnostic(raw, max_length=limit)
        assert "hiddenword" not in uncut
        assert "hidden" not in uncut
        assert cut == (uncut if limit is None else uncut[:limit])


@pytest.mark.parametrize(
    "name", ["credential.user", "auth:mode", "private_key.scope", "credential."]
)
def test_final_identifier_family_before_last_segment_is_not_widened(name: str) -> None:
    rows = {
        "link": f"HTTPS://example.test/p?{name}=plainword",
        "auth": "basic",
        "credentials": "include",
    }
    expected = dict(rows)
    if name == "auth:mode":
        # Existing generic `auth:` handling still applies outside the new
        # final-segment query classifier.
        expected["link"] = "HTTPS://example.test/p?auth:[REDACTED]"
    for output in _round3_rows_on_surfaces(rows, 1):
        assert output == expected
    for text in ("auth: basic", "credential: include", "X-Mode: plainword"):
        assert _ours_e(text) == _main_e(text)
        assert _ours_p(text) == _main_p(text)


def test_final_identifier_restoration_mutant_is_red_then_restored_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pmcp.auth as auth

    with monkeypatch.context() as patch:
        old_name = re.compile(r"\A[A-Za-z0-9_-]+\Z", re.IGNORECASE)
        patch.setattr(A, "_QUERY_IDENTIFIER_RE", old_name)
        patch.setattr(auth, "_QUERY_IDENTIFIER_RE", old_name)
        with pytest.raises(AssertionError):
            test_final_identifier_joiners_withhold_complete_query_values(".")
    test_final_identifier_joiners_withhold_complete_query_values(".")


def test_final_identifier_long_nonmatching_segments_with_punctuation() -> None:
    for size in (512, 2048, 8192):
        name = "credential" + "x" * size + "." + "n" * size + "!"
        assert A._QUERY_IDENTIFIER_RE.search(name) is None
        text = f"HTTPS://example.test/p?{name}=plainword"
        assert _ours_e(text) == text
        assert _ours_p(text) == text


def test_postcap_query_coverage_mutant_restores_weak_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pmcp.keyword_matcher import key_start_pattern

    monkeypatch.setattr(A, "_QUERY_KEY_START", key_start_pattern(()))
    with pytest.raises(AssertionError):
        test_postcap_qualified_query_values_are_wholly_secret("auth", "my{}")


@pytest.mark.parametrize("ascii_only", [True, False])
@pytest.mark.parametrize("depth", range(4))
def test_postcap_semantic_query_names_keep_value_boundaries(
    ascii_only: bool, depth: int
) -> None:
    rows = {
        "link": '<a href="HTTPS://example.test/p?myprıvate_Key=hunterword\\\\).;">😀</a>',
        "benign": '<a href="HTTPS://example.test/p?q=plainword\\\\">😀</a>',
    }
    nested = json.dumps(rows, ensure_ascii=ascii_only)
    for _ in range(depth):
        nested = json.dumps(nested, ensure_ascii=ascii_only)
    payload = {"content": [{"text": nested}], "structuredContent": rows}
    doc = json.dumps(payload, ensure_ascii=ascii_only)
    policy = _policy()
    for raw in (
        _ours_e(doc),
        policy.redact_secrets(doc),
        policy.process_output(doc, redact=True, max_bytes=10**6)["result"],
        policy.process_output(payload, redact=True, max_bytes=10**6)["result"],
    ):
        output = _round3_decode_payload(raw, depth)
        assert (
            output["link"]
            == '<a href="HTTPS://example.test/p?myprıvate_Key=[REDACTED]">😀</a>'
        )
        assert output["benign"] == rows["benign"]
        assert (
            policy.process_output(output, redact=True, max_bytes=10**6)["result"]
            == output
        )


def test_postcap_semantic_query_mutant_restores_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = A._json_string_context
    monkeypatch.setattr(A, "_json_string_context", lambda text, **kwargs: context(text))
    with pytest.raises(AssertionError):
        test_postcap_semantic_query_names_keep_value_boundaries(True, 1)


def test_postcap_atomic_query_mutant_restores_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    merge = A.merge_redaction_spans
    monkeypatch.setattr(
        A,
        "merge_redaction_spans",
        lambda text, spans: merge(
            text, [(a, b, str(replacement)) for a, b, replacement in spans]
        ),
    )
    with pytest.raises(AssertionError):
        test_postcap_qualified_query_values_are_wholly_secret("token", "my{}")


def test_postcap_keyword_classifier_mutant_restores_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pmcp.auth as auth

    from pmcp.keyword_matcher import key_start_pattern

    text = "see https://example.test/p?scope.csrftoken=hunterword\\\\ next"
    redact = auth.redact_keyword_values
    with monkeypatch.context() as patch:
        # Both query routes now recognize this dotted name independently.
        patch.setattr(auth, "_QUERY_KEY_START", key_start_pattern(()))
        patch.setattr(A, "_QUERY_KEY_START", key_start_pattern(()))
        patch.setattr(
            auth,
            "redact_keyword_values",
            lambda text, keys: text
            if text.startswith("scope.csrftoken=")
            else redact(text, keys),
        )
        with pytest.raises(AssertionError):
            assert "\\" not in _ours_e(text)
    assert "\\" not in _ours_e(text)
    assert "hunterword" not in _ours_e(text)


@pytest.mark.parametrize(
    "name",
    [
        "myauth",
        "x_credential",
        "private_key_suffix",
        "x_token",
        "private-key",
        "privatekey",
    ],
)
@pytest.mark.parametrize(
    "value", ["\\", "\\\\", ").,;", "[REDACTED]\\", "%5BREDACTED%5D;;\\"]
)
def test_postcap_qualified_query_suffix_only_and_marked_values(
    name: str, value: str
) -> None:
    from urllib.parse import unquote

    for scheme in ("https://", "HTTPS://", "hTTp://"):
        for depth in (0, 1, 3):
            rows = {"link": f"{scheme}example.test:99999/p?{name}={value}"}
            for output in _round3_rows_on_surfaces(rows, depth):
                assert unquote(output["link"].split("=", 1)[1]) == "[REDACTED]"


@pytest.mark.parametrize("family", ["auth", "token", "private-key"])
def test_postcap_query_identifier_qualifiers_have_no_prose_length_gate(
    family: str,
) -> None:
    name = "x" * 128 + family + "_right" * 20
    for output in _round3_rows_on_surfaces(
        {"link": f"HTTPS://example.test/p?{name}=hunterword\\\\"}, 1
    ):
        assert "hunterword" not in output["link"]
        assert "\\" not in output["link"]


@pytest.mark.parametrize("name", ["private-key", "privatekey"])
@pytest.mark.parametrize("depth", [0, 1, 3])
def test_postcap_exact_alias_query_names_through_json_escapes(
    name: str, depth: int
) -> None:
    rows = {"link": f"HTTPS://example.test/p?{name}=[REDACTED]\\\\).;"}
    escaped = "".join(f"\\u{ord(char):04x}" for char in name)
    nested = json.dumps(rows).replace(name, escaped)
    for _ in range(depth):
        nested = json.dumps(nested)
    payload = {"content": [{"text": nested}], "structuredContent": rows}
    policy = _policy()
    for raw in (
        _ours_e(json.dumps(payload)),
        policy.redact_secrets(json.dumps(payload)),
        policy.process_output(payload, redact=True, max_bytes=10**6)["result"],
    ):
        output = _round3_decode_payload(raw, depth)
        assert output == {"link": f"HTTPS://example.test/p?{name}=[REDACTED]"}


@pytest.mark.parametrize("depth", [0, 1, 3])
@pytest.mark.parametrize("href", ["", "https://example.test/watch?v=abc"])
def test_round3_policy_url_keys_keep_existing_decoded_value_coverage(
    depth: int, href: str
) -> None:
    policy = _policy()
    policy._redaction_regexes = [re.compile("probeWord@S7")]
    key = "https://example.test/p?q=probeWord%40S7"
    # The preexisting URL value rule already consults these policy patterns.
    as_value = policy.redact_secrets(json.dumps({"value": key}))
    assert "probeWord" not in as_value
    rows = {
        "keys": {key: "protected", "[REDACTED]": "keep"},
        "snippet": f'<a href="{href}">P</a>',
    }
    for raw in _round3_payloads_on_surfaces(rows, depth)[1:]:
        output = _round3_decode_payload(raw, depth)
        assert output["keys"] == {"[REDACTED]#2": "protected", "[REDACTED]": "keep"}
        assert "probeWord" not in json.dumps(output)


def test_round3_mutant_losing_policy_key_coverage_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clip = A._clip_to_json_strings
    monkeypatch.setattr(
        A,
        "_clip_to_json_strings",
        lambda text, spans, **kwargs: clip(text, spans, covers=None),
    )
    with pytest.raises(AssertionError):
        test_round3_policy_url_keys_keep_existing_decoded_value_coverage(
            1, "https://example.test/watch?v=abc"
        )


@pytest.mark.parametrize("name", ["q", "%71", "return"])
@pytest.mark.parametrize("depth", range(4))
@pytest.mark.parametrize("tail", ["\\\\", ".\\).,;\\\\"])
def test_round3_policy_value_coverage_keeps_no_suffix(
    name: str, depth: int, tail: str
) -> None:
    from urllib.parse import unquote

    policy = _policy()
    policy._redaction_regexes = [re.compile("probeWord@S7")]
    href = f"https://example.test/p?{name}=probeWord%40S7{tail}"
    free = f"see {href} next"
    for output in (
        policy.redact_secrets(free),
        policy.process_output(free, redact=True, max_bytes=10**6)["result"],
    ):
        assert (
            unquote(output.split("=", 1)[1].rsplit(" next", 1)[0].strip())
            == "[REDACTED]"
        )
    rows = {"snippet": f'<Card href="{href}">😀 Person</Card>'}
    for raw in _round3_payloads_on_surfaces(rows, depth)[1:]:
        output = _round3_decode_payload(raw, depth)
        target = output["snippet"].split('href="', 1)[1].split('"', 1)[0]
        assert unquote(target.split("=", 1)[1]) == "[REDACTED]"
        assert output["snippet"].endswith('">😀 Person</Card>')


def test_round3_mutant_losing_base_policy_context_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pmcp.policy.policy as policy_module

    base = policy_module._sanitize_base
    monkeypatch.setattr(
        policy_module, "_sanitize_base", lambda text, **kwargs: base(text)
    )
    with pytest.raises(AssertionError):
        test_round3_policy_value_coverage_keeps_no_suffix("q", 1, "\\\\")


def test_round3_independent_mutants_restore_newly_protected_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pmcp.auth

    original = pmcp.auth._covers_anything
    with monkeypatch.context() as patch:
        from pmcp.keyword_matcher import key_start_pattern

        # The new query-only classifier independently covers this name. A
        # restoration mutant must disable both routes to expose the old tail.
        patch.setattr(pmcp.auth, "_QUERY_KEY_START", key_start_pattern(()))
        patch.setattr(A, "_QUERY_KEY_START", key_start_pattern(()))
        patch.setattr(
            pmcp.auth,
            "_covers_anything",
            lambda text, depth, covers: False
            if text.lower().startswith("private_key=")
            else original(text, depth, covers),
        )
        with pytest.raises(AssertionError):
            test_round3_query_grammar_has_no_secret_suffix(
                "private_key", 1, "\\", "hunter2xKP"
            )
    test_round3_query_grammar_has_no_secret_suffix("private_key", 1, "\\", "hunter2xKP")
    with monkeypatch.context() as patch:
        patch.setattr(
            A, "_covers_anything", lambda text, *args: bool(A._shape_spans(text))
        )
        with pytest.raises(AssertionError):
            test_round3_keyword_mapping_keys_are_secret("private_key", "=", 1)
    test_round3_keyword_mapping_keys_are_secret("private_key", "=", 1)


@pytest.mark.parametrize("depth", [0, 1, 3])
@pytest.mark.parametrize("escaped", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_url_escape_keys_with_existing_markers_are_atomic(
    depth: int, escaped: bool, reverse: bool
) -> None:
    key = "AKIAABCDEFGHIJKLMNOP [REDACTED]"
    rows = {
        key: "protected",
        "[REDACTED][REDACTED]": "benign",
        "[REDACTED]": "reserved",
    }
    if reverse:
        rows = dict(reversed(list(rows.items())))
    doc = json.dumps(rows)
    if escaped:
        doc = doc.replace("[REDACTED]", r"\u005bREDACTED\u005d")
    nested = doc
    for _ in range(depth):
        nested = json.dumps(nested)
    payload = {"text": nested, "structuredContent": json.loads(doc)}
    for redact in (_ours_e, _ours_p):
        result = json.loads(redact(json.dumps(payload)))
        decoded = result["text"]
        for _ in range(depth + 1):
            decoded = json.loads(decoded)
        assert (
            decoded
            == result["structuredContent"]
            == {
                "[REDACTED][REDACTED]": "benign",
                "[REDACTED]": "reserved",
                "[REDACTED]#2": "protected",
            }
        )
        assert "AKIAABCDEFGHIJKLMNOP" not in json.dumps(result)


def test_url_escape_atomic_key_mutant_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    merge = A.merge_redaction_spans
    monkeypatch.setattr(
        A,
        "merge_redaction_spans",
        lambda text, spans: merge(
            text, [(a, b, str(replacement)) for a, b, replacement in spans]
        ),
    )
    with pytest.raises(AssertionError):
        test_url_escape_keys_with_existing_markers_are_atomic(1, False, False)


@pytest.mark.parametrize("key", ["credential", "credentials", "auth", "code"])
@pytest.mark.parametrize("depth", [0, 1, 3])
def test_url_escape_weak_query_values_are_secrets(key: str, depth: int) -> None:
    snippet = f'<a href="https://example.test/watch?{key}=hunter">Person</a>'
    nested = snippet
    for _ in range(depth):
        nested = json.dumps(nested)
    rows = {
        "snippet": snippet,
        "nested": nested,
        "status": {"code": "not_found", "credentials": "include", "auth": "basic"},
    }
    payload = {
        "content": [{"type": "text", "text": json.dumps(rows)}],
        "structuredContent": rows,
    }
    result = _ours_process(payload)
    assert isinstance(result, dict)
    assert "hunter" not in json.dumps(result)
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert result["structuredContent"]["status"] == rows["status"]
    for redact in (_ours_e, _ours_p):
        assert "hunter" not in redact(json.dumps(payload))


@pytest.mark.parametrize("depth", [0, 1, 3])
def test_url_escape_escaped_benign_keys_reserve_markers(depth: int) -> None:
    key = "AKIAABCDEFGHIJKLMNOP"
    doc = (
        '{"\\u005bREDACTED\\u005d": "keep", "\\u005bREDACTED\\u005d#2": "also keep", "'
        + key
        + '": "protected"}'
    )
    nested = doc
    for _ in range(depth):
        nested = json.dumps(nested)
    payload = {"text": nested, "structuredContent": json.loads(doc)}
    for redact in (_ours_e, _ours_p):
        result = json.loads(redact(json.dumps(payload)))
        decoded = result["text"]
        for _ in range(depth + 1):
            decoded = json.loads(decoded)
        assert (
            decoded
            == result["structuredContent"]
            == {
                "[REDACTED]": "keep",
                "[REDACTED]#2": "also keep",
                "[REDACTED]#3": "protected",
            }
        )
        assert key not in json.dumps(result)
        assert json.loads(redact(doc)) == decoded


@pytest.mark.parametrize("depth", [0, 1, 3])
@pytest.mark.parametrize("value", ["tokPieceO.\\", "\\\\", ";;;\\"])
@pytest.mark.parametrize(
    "component",
    [
        "?token=",
        "?credential=",
        "#access_token=",
        "?q=ghp_abcdefghijklmnopqrstuvwx1234567890",
    ],
)
def test_url_escape_policy_secret_suffix_is_not_returned(
    depth: int, value: str, component: str
) -> None:
    snippet = f'<a href="https://example.test/p{component}{value}">Person</a>'
    nested = snippet
    for _ in range(depth):
        nested = json.dumps(nested)
    rows = {"snippet": snippet, "nested": nested}
    payload = {
        "content": [{"type": "text", "text": json.dumps(rows)}],
        "structuredContent": rows,
    }
    result = _ours_process(payload)
    assert isinstance(result, dict)
    rows = result["structuredContent"]
    assert json.loads(result["content"][0]["text"]) == rows
    assert "\\" not in rows["snippet"]
    assert "tokPieceO" not in rows["snippet"]
    assert "ghp_abcdefghijklmnopqrstuvwx1234567890" not in rows["snippet"]
    decoded = rows["nested"]
    for _ in range(depth):
        decoded = json.loads(decoded)
    assert decoded == rows["snippet"]
    for redact in (_ours_e, _ours_p):
        raw = redact(f"see https://example.test/p{component}{value} next")
        assert "\\" not in raw
        assert "tokPieceO" not in raw
        assert "ghp_abcdefghijklmnopqrstuvwx1234567890" not in raw


def test_url_escape_mutants_restoring_newly_protected_bytes_are_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pmcp.auth

    with monkeypatch.context() as patch:
        patch.setattr(A, "_json_string_context", lambda text: ([], {}))
        with pytest.raises(AssertionError):
            test_url_escape_credential_keys_keep_distinct_entries(
                1, "https://example.test/watch?v=abc"
            )
    with monkeypatch.context() as patch:
        patch.setattr(A, "_url_component_spans", lambda *args: [])
        with pytest.raises(AssertionError):
            test_url_escape_weak_query_values_are_secrets("credential", 1)
    with monkeypatch.context() as patch:
        patch.setattr(pmcp.auth, "_sanitize_base", _main_e)
        with pytest.raises(AssertionError):
            test_url_escape_policy_secret_suffix_is_not_returned(
                1, "tokPieceO.\\", "?token="
            )
    # The ordered relation also rejects restoring these bytes when the
    # comparison output actually withheld them. The historical oracle did
    # not withhold these new cases, hence the separate regressions above.
    for protected, restored in (
        (
            '{"[REDACTED]": 1, "[REDACTED]#2": 2}',
            '{"AKIAABCDEFGHIJKLMNOP": 1, "[REDACTED]#2": 2}',
        ),
        ("credential=[REDACTED]", "credential=hunter"),
        ("token=[REDACTED] next", "token=[REDACTED]\\ next"),
    ):
        assert not _is_additive_of(restored, protected)


@pytest.mark.parametrize("depth", [0, 2, 4])
@pytest.mark.parametrize(
    ("url", "suffix", "label", "secrets"),
    [
        ("https://example.test/watch?v=abc", "", "Person", ()),
        (r"https://example.test/a\b?v=%5C", "\\\\", "人 café", ()),
        ("https://example.test/watch?v=abc", "\\).,;\\", "人 café", ()),
        (
            r"https://userPiece:passPiece@example.test/watch?token=leftPiece\rightPiece&code=codePiece&v=abc",
            "\\\\",
            "人 café",
            (
                "userPiece",
                "passPiece",
                "leftPiece",
                "rightPiece",
                "codePiece",
            ),
        ),
    ],
)
def test_url_escape_structured_result_regression(
    depth: int, url: str, suffix: str, label: str, secrets: tuple[str, ...]
) -> None:
    snippet = f'<Card href="{url}{suffix}">{label}</Card>'
    rows = {"results": [{"file": "example.dart", "snippet": snippet}]}
    nested = snippet
    for _ in range(depth):
        nested = json.dumps(nested, ensure_ascii=False)
    if depth:
        rows["results"][0]["nested"] = nested
    payload = {
        "content": [{"type": "text", "text": json.dumps(rows)}],
        "structuredContent": rows,
        "isError": False,
    }
    policy = _policy()
    processed = policy.process_output(payload, redact=True, max_bytes=200000)
    assert processed["truncated"] is False
    assert processed["summary"] is None
    assert processed["raw_size"] == len(json.dumps(payload, indent=2).encode("utf-8"))
    result = processed["result"]
    assert isinstance(result, dict)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    results = [
        result,
        json.loads(sanitize_auth_diagnostic(serialized, max_length=None)),
        json.loads(policy.redact_secrets(serialized)),
    ]
    for result in results:
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        assert result["isError"] is False
        if not secrets:
            assert result == payload
        for secret in secrets:
            assert secret not in json.dumps(result)
        row = result["structuredContent"]["results"][0]
        if depth:
            decoded = row["nested"]
            for _ in range(depth):
                decoded = json.loads(decoded)
            assert decoded == row["snippet"]


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
    monkeypatch.setattr(A, "_clip_to_json_strings", lambda text, spans, **kwargs: spans)
    with pytest.raises(json.JSONDecodeError):
        test_a_span_across_a_string_boundary_is_confined_to_the_string()


def test_credential_keys_are_redacted_without_colliding() -> None:
    """Credential keys are protected, and both entries survive (rev 15's
    board). Ordinary keys retain their spelling and value."""
    obj = {"Xk9mQ2vLp3RtY7wBaaQ1": 1, "Pq7rS2tUv9WxY3zAbC4d": 2}
    result = _ours_process({**obj, "ordinary": 3})
    assert result == {"[REDACTED]": 1, "[REDACTED]#2": 2, "ordinary": 3}
    doc = json.dumps({"Xk9mQ2vLp3RtY7wBaaQ1": "Pq7rS2tUv9WxY3zAbC4d"})
    assert json.loads(_ours_e(doc)) == {"[REDACTED]": "[REDACTED]"}


def test_object_keys_mutant_clipping_into_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rev 15's clipping, which let spans into object keys, collides them."""
    monkeypatch.setattr(A, "_is_object_key", lambda text, end: False)
    with pytest.raises(AssertionError):
        test_credential_keys_are_redacted_without_colliding()


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
    outside a whole marker; and (E, P) a second additive pass over the
    output keeps every one of its markers and only adds. (It is monotone,
    not idempotent: next to a marker it wrote, a second pass may mark a
    scheme word or a value prefix the first pass left.)"""
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
                else _policy()._redact_additive(out)
                if label == "P"
                else out
            )
            # run again, the additive pass only adds: it keeps every marker
            # of its own output whole, in order and in its spelling, and
            # every other stretch it keeps comes from that output
            position = 0
            for marker in out_markers:
                found = again.find(marker, position)
                if found < 0:
                    bad.append(f"{label}: a second pass lost {marker} in {out!r}")
                    break
                position = found + len(marker)
            if not _is_additive_of(again, out):
                bad.append(f"{label}: a second pass restored text in {out!r}")
    return bad


def test_markers_are_kept_whole_and_spelled_as_written_on_every_surface() -> None:
    texts = _MARKER_TEXTS + _texts(G.corpus(1)[::7]) + _board_texts()
    assert _marker_problems(texts) == [], _marker_problems(texts)[:10]


def test_markers_mutant_a_policy_value_run_through_a_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rev 15's policy pass (no stop at `&` in a query, and a merge that
    swallowed markers) re-spelled the URL marker and lost the rest of the
    query."""
    import pmcp.policy.policy as P

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


#: A key word, a plain word the redactor takes as its value, then
#: punctuation: `token bucket?`, `secret=sauce!`. The glued run must leave
#: that punctuation as the redactor wrote it.
_KEYWORD_PUNCTUATION_LINES = [
    f"{lead}{word}{sep}{follow}{mark}{trail}"
    for word in ("secret", "token", "password", "key", "auth", "credential", "session")
    for follow in ("sauce", "bucket", "reset", "rotation", "expired", "holder")
    for mark in "!?.:;,)"
    for lead in ("", "the ")
    for trail in ("", " done")
    for sep in (" ", "=")
]


def _prose_differences() -> list[str]:
    bad = [
        label
        for label, same in (
            ("E", _ours_e(PROSE) == _main_e(PROSE)),
            ("P", _ours_p(PROSE) == _main_p(PROSE)),
        )
        if not same
    ]
    rng = random.Random(9234)
    for text in [_random_prose(rng) for _ in range(2000)] + _KEYWORD_PUNCTUATION_LINES:
        if _ours_e(text) != _main_e(text) or _ours_p(text) != _main_p(text):
            bad.append(text)
    return bad


def test_prose_the_redactor_keeps_the_additive_rules_keep_too() -> None:
    """The additive rules' own false positives: none on the prose corpus, on
    2 000 generated prose lines, or on key words followed by a word and
    punctuation (the base pass's own stand)."""
    bad = _prose_differences()
    assert bad == [], bad[:10]


def test_prose_mutant_a_glued_run_without_a_letter_or_digit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rev 18's first glued run (punctuation alone replaced too) marks the
    punctuation after the redactor's own false positives."""
    monkeypatch.setattr(A, "_is_a_tail", lambda run: True)
    assert _prose_differences() != []


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
    "final query segment ending in punctuation": lambda n: "HTTPS://example.test/p?credential" + "x" * (n // 2) + "." + "n" * (n // 2) + "!=plainword",
    "qualified Unicode query values": lambda n: json.dumps({str(i): 'HTTPS://example.test/p?myprıvate_Key=plainword\\\\).;' for i in range(n // 100)}),
    "nested encoded URL keys": lambda n: json.dumps({f"https://h.example/?q=https%3A%2F%2Fh.example%2F%3Fq%3Dhttps%253A%252F%252Fh.example%252F%253Ftoken%253Dpiece{i}": i for i in range(n // 160)}),
    "credential keys and escaped reserved marker": lambda n: json.dumps({"text": '{"\\u005bREDACTED\\u005d": 1, "AKIAABCDEFGHIJKLMNOP": 2}', "padding": "x " * (n // 2)}),
    "backslash run alongside credential keys": lambda n: json.dumps({"AKIAABCDEFGHIJKLMNOP": 1, "text": "\\" * (n // 2)}),
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


# ======================================= values the redactor partly marked ==== #
#
# The redactor's keyword rule accepts whitespace as a separator, so a key word
# it lists, followed by a word, writes a marker INSIDE a strong key's value
# (`{"password": "Secret Garden 2024!"}` -> `"Secret [REDACTED] 2024!"`). Rev 16
# dropped any additive span overlapping a marker, and the rest of the value
# showed (rev 16's board, F1). Derived from the redactor's own key list.

_STRONG_KEYS = sorted(
    k for k in A.ADDITIVE_SECRET_KEYS if k not in A.WEAK_SECRET_KEYS and k != "code"
)
_BASE_KEY_WORDS = sorted({*M.AUTH_DIAGNOSTIC_SECRET_KEYS, "api_key"})
_VALUE_TEMPLATES = ("{w} Garden 2024!", "the {w} was 1999 mild", "x9 {w}=q7Zp2 tail")


def _value_leftovers(value: str) -> str:
    return _MARKER_SPLIT_RE.sub("", value).strip()


def _partly_marked_failures() -> list[str]:
    bad = []
    policy = PolicyManager()
    for key in _STRONG_KEYS:
        for word in _BASE_KEY_WORDS:
            for template in _VALUE_TEMPLATES:
                value = template.format(w=word)
                if _MARKER_SPLIT_RE.search(_main_e(json.dumps({key: value}))) is None:
                    continue  # the redactor wrote no marker in it: not this class
                doc = json.dumps({key: value})
                for label, out in (
                    ("E", _ours_e(doc)),
                    ("P", policy.redact_secrets(doc)),
                    ("POd", json.dumps(_ours_process({key: value}))),
                ):
                    left = _value_leftovers(json.loads(out)[key])
                    if left:
                        bad.append(f"{label} {doc} -> {left!r}")
                plain = f'{key}: "{value}"'
                out = _ours_e(plain)
                inner = out[out.index('"') + 1 : out.rindex('"')]
                if _value_leftovers(inner):
                    bad.append(f"E {plain} -> {out}")
    return bad


def test_a_value_the_redactor_partly_marked_is_redacted_whole() -> None:
    """For every strong key and every key word the redactor reacts to, a
    value holding that word (then a word or `=`) keeps no character outside
    the markers, on E, P and the structured path; the markers stay as
    written."""
    bad = _partly_marked_failures()
    assert bad == [], bad[:10]


def _rev16_merge(text: str, spans: list[A.Span]) -> list[A.Span]:
    """Rev 16's merge: a span overlapping any marker was dropped."""
    markers = [m.span() for m in A._MARKER_RE.finditer(text)]
    kept = [
        (start, end, r)
        for start, end, r in spans
        if not any(m0 < end and start < m1 for m0, m1 in markers)
    ]
    return A._merge_unmarked(text, kept)


def test_partly_marked_mutant_rev_16_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(A, "merge_redaction_spans", _rev16_merge)
    assert _partly_marked_failures() != []


# ================================================= the end of a value ======== #
#
# The redactor's keyword rule replaces a value only up to the first character
# outside its value class, so `token=qVwYS81V!7Hb1DX8pP` reaches the additive
# pass as `token=[REDACTED]!7Hb1DX8pP`. Rev 17 skipped an additive span that
# started on that marker and the tail survived (rev 17's board, F-A); rev 18
# replaces the run glued to the marker's end. A probe of a value's first
# characters cannot see this -- the redactor always takes those -- so these
# tests read the END of the value.
#
# What still survives, disclosed in the plan: a tail that begins at one of
# the delimiters the keyed grammars end a value at (`;`, `,`, `&`, `#`: `a=1;
# b=2`, a form body, a fragment), or at a bracket or parenthesis; and the
# tail of a value under a whitespace-keyed flag (`--password x`), whose
# additive rule cannot start on the marker (pinned below).

_TAIL_STOPS = frozenset(";,&#()[]{}<")
_TAIL_PUNCT = "!@$%^*()[]{}|;:,<>?#&~+=/._-"
_TAIL_FORMS = (
    "password={v}",
    "token={v} end",
    "X-Api-Key: {v}",
    "client_secret={v}&x=1",
    "https://h.example:99999/cb?token={v}",
    "Authorization: Bearer {v}",
    '{{"password": "{v}"}}',
    '{{"client_secret": "{v}", "n": 1}}',
)


def _tail_value(rng: random.Random, punct: str) -> str:
    """A value of letters and digits, punctuation from ``punct`` and `%xx`
    escapes, beginning and ending on a run of letters and digits (what the
    redactor's keyword rule takes before the first other character)."""
    alnum = string.ascii_letters + string.digits
    parts = ["".join(rng.choice(alnum) for _ in range(rng.randint(4, 8)))]
    for _ in range(rng.randint(2, 5)):
        roll = rng.random()
        if roll < 0.15:
            parts.append(
                "%" + rng.choice("0123456789ABCDEF") + rng.choice("0123456789ABCDEF")
            )
        elif roll < 0.45 and punct:
            parts.append(rng.choice(punct))
        else:
            parts.append(
                "".join(
                    rng.choice(string.ascii_letters + string.digits)
                    for _ in range(rng.randint(3, 8))
                )
            )
    return "".join(parts) + "Z9q"


def _tail_left(value: str, text: str, out: str) -> int | None:
    """Where the longest suffix of ``value`` (3 characters or more) that
    ``out`` still holds begins, or None; a suffix found in the text outside
    the value does not count."""
    rest = text.replace(value, "", 1)
    return next(
        (
            i
            for i in range(len(value) - 2)
            if value[i:] in out and value[i:] not in rest
        ),
        None,
    )


def _tail_surfaces(text: str) -> dict[str, str]:
    """The four entry points. A JSON form goes to the structured path as the
    object it spells (a JSON document inside a JSON string is a class of its
    own, disclosed in the plan)."""
    policy = PolicyManager()
    obj = json.loads(text) if text.startswith("{") else {"t": text}
    return {
        "E": _ours_e(text),
        "P": policy.redact_secrets(text),
        "POs": str(policy.process_output(text, redact=True, max_bytes=G.BIG)["result"]),
        "POd": json.dumps(_ours_process(obj)),
    }


def _tail_failures(punct: str, count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    bad = []
    for _ in range(count):
        value = _tail_value(rng, punct)
        for form in _TAIL_FORMS:
            text = form.format(v=value)
            for label, out in _tail_surfaces(text).items():
                k = _tail_left(value, text, out)
                if k is not None and not _after_a_stop(value, k):
                    bad.append(f"{label} {text!r} -> {out!r}")
    return bad


def _after_a_stop(value: str, k: int) -> bool:
    """What survives from ``k`` on begins at or after the value's first
    stop, or only punctuation lies between ``k`` and that stop (a glued
    run of punctuation alone is left, D1: `[REDACTED]!(rest`)."""
    first = next((j for j, c in enumerate(value) if c in _TAIL_STOPS), None)
    if first is None:
        return False
    return first <= k or not any(c.isalnum() for c in value[k:first])


def test_the_end_of_a_value_is_removed_on_every_surface() -> None:
    """Values from letters, digits, `%xx` and every punctuation character
    but the disclosed stops: on E, P and both structured paths no three
    characters of the value's end survive, in any form."""
    punct = "".join(c for c in _TAIL_PUNCT if c not in _TAIL_STOPS)
    bad = _tail_failures(punct, 150, 18)
    assert bad == [], bad[:10]


def test_a_value_tail_survives_only_after_a_disclosed_stop() -> None:
    """With the stops in the mix too, whatever survives of a value begins
    at or after one of them."""
    bad = _tail_failures(_TAIL_PUNCT, 150, 1834)
    assert bad == [], bad[:10]


@pytest.mark.parametrize(
    ("text", "tail"),
    [
        ("token=qVwYS81V!7Hb1DX8pP", "7Hb1DX8pP"),
        ("https://h.example:99999/cb?token=abcd%41%48XYZ123", "XYZ123"),
        ("password=Summer2024!Sunny", "Sunny"),
    ],
)
def test_rev_17_board_tails(text: str, tail: str) -> None:
    for label, out in _tail_surfaces(text).items():
        assert tail not in out, (label, out)


def test_the_rest_of_a_query_is_not_glued_to_a_marker() -> None:
    for label, out in _tail_surfaces(
        "see https://h.example/cb?token=abc123def456&page=2 now"
    ).items():
        assert "&page=2 now" in out, (label, out)


def test_an_arn_under_an_arn_key_keeps_its_resource_name() -> None:
    """The redactor writes over an ARN's literal `arn` (a key word it lists);
    the partition after that marker is the resource name, not a tail -- but
    only under a key that names an ARN."""
    arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:MySecret-a1b2c3"
    for text in (f"secret_arn={arn}", json.dumps({"SecretArn": arn})):
        for label, out in _tail_surfaces(text).items():
            assert ":aws:secretsmanager:" in out, (label, out)
    for label, out in _tail_surfaces("password=Pa55wd:aws:tailtail").items():
        assert "tailtail" not in out, (label, out)


def test_arn_exemption_mutant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the exemption the prose corpus's ARN line differs from the
    redactor's output (rev 17 board: 2 of 2 002 prose lines)."""
    monkeypatch.setattr(A, "_is_resource_name_tail", lambda text, marker, glued: False)
    assert _ours_e(PROSE) != _main_e(PROSE)


def test_disclosed_a_whitespace_keyed_value_keeps_its_tail() -> None:
    """Pins a disclosed residual: the additive whitespace-keyword rule's
    value class has no `[`, so it never starts on the redactor's marker and
    no glued run is replaced. Extending it is a maintainer decision (the
    plan: it also marks punctuation after the redactor's own whitespace
    false positives, `secret sauce!`)."""
    for label, out in _tail_surfaces(
        "mycli --password qVwYS81V!7Hb1DX8pP --verbose"
    ).items():
        assert "[REDACTED]!7Hb1DX8pP" in out, (label, out)


def test_disclosed_a_punctuation_only_tail_stays() -> None:
    """Pins a disclosed residual: a glued run of punctuation alone is not
    replaced, so after a word the redactor took (`token=bucket!`) the output
    is the redactor's, and a password's punctuation-only tail stays."""
    for label, out in _tail_surfaces("token=bucket!").items():
        assert out.endswith(("token=[REDACTED]!", 'token=[REDACTED]!"}')), (label, out)
    for label, out in _tail_surfaces("token=abcdef!").items():
        assert "[REDACTED]!" in out and "abcdef" not in out, (label, out)


# ---------------------------------------------- code and log text (rev 19) --- #
#
# The glued run must not extend the redactor's own false positives in code
# and delimited logs, nor take the next field (rev 18.1's board, F-1):
# `f(token=[REDACTED],page=2)` keeps `,page=2`. It stops at `,`, `;`, `(`,
# `[`, `{` and `<` besides whitespace, `&`, `#`, quotes and backslashes.

_CODE_LINES = (
    "tokens = self._tokenize(query)",
    "f(token=abcdef,page=2)",
    "(token=abcdef),next",
    "tokens = tokenizer.encode(text)[0]",
    "secret = load_secret(path)",
    "Set-Cookie: session=abc123def;Path=/;HttpOnly",
    'api_key = os.environ["API_KEY"]',
    "let token = await getToken({ user })",
    "INFO Token=bvp1Q11NE8</td><td>ok</td> done",
    "auth_token=deadbeefcafe,user=bob,ip=10.0.0.1",
)
_LOG_KEYS = (
    "token",
    "access_token",
    "id_token",
    "refresh_token",
    "auth_token",
    "session_token",
    "csrf_token",
    "api_key",
    "apikey",
    "password",
    "secret",
    "client_secret",
    "Token",
    "TOKEN",
)
#: (what follows the value, the field that must stay): each begins at a
#: stop, or at a closing bracket and then a stop.
_LOG_TAILS = (
    (",page=2", "page=2"),
    (",next", "next"),
    ("),next", "next"),
    (";Path=/;HttpOnly", "Path=/"),
    (";path=/", "path=/"),
    ("</td><td>ok</td>", "<td>ok"),
    ("],user=bob", "user=bob"),
    ("},{id:2}", "id:2"),
    (",expires_in=3600", "expires_in=3600"),
    (",user=bob,ip=10.0.0.1", "user=bob"),
    ("<br>next", "<br>next"),
    (";expires=Wed, 21 Oct 2026 07:28:00 GMT", "expires=Wed"),
)
_LOG_PREFIXES = (
    "",
    "f(",
    "[",
    "user=bob,",
    "ts=1 level=info ",
    "Set-Cookie: ",
    "cookie: a=1;",
    "INFO ",
    "(",
    "{",
)


def _log_value(rng: random.Random) -> str:
    alnum = string.ascii_letters + string.digits
    roll = rng.random()
    if roll < 0.5:
        return "".join(rng.choice(alnum) for _ in range(rng.randint(8, 24)))
    if roll < 0.7:
        return "".join(rng.choice("0123456789abcdef") for _ in range(32))
    if roll < 0.8:
        return str(uuid.UUID(int=rng.getrandbits(128)))
    return (
        "".join(rng.choice(alnum) for _ in range(6))
        + rng.choice("!@$*~^")
        + "".join(rng.choice(alnum) for _ in range(6))
    )


def _code_and_log_failures() -> list[str]:
    """Code lines must equal the redactor's output. In a generated log
    line whose value the redactor marked, the field after it stays wherever
    the redactor keeps it."""
    bad = [
        line
        for line in _CODE_LINES
        if _ours_e(line) != _main_e(line) or _ours_p(line) != _main_p(line)
    ]
    rng = random.Random(1918)
    for i in range(1500):
        tail, keep = rng.choice(_LOG_TAILS)
        sep = rng.choice(["=", "=", ":", ": "])
        line = f"{rng.choice(_LOG_PREFIXES)}{rng.choice(_LOG_KEYS)}{sep}{_log_value(rng)}{tail} done"
        texts = [line, json.dumps({"log": line})] if i % 3 == 0 else [line]
        for text in texts:
            for label, ours, main in (("E", _ours_e, _main_e), ("P", _ours_p, _main_p)):
                base = main(text)
                if "[REDACTED]" in base and keep in base and keep not in ours(text):
                    bad.append(f"{label} {text!r} -> {ours(text)!r}")
    return bad


def test_code_and_log_text_keep_the_next_field() -> None:
    bad = _code_and_log_failures()
    assert bad == [], bad[:10]


def test_code_and_log_mutant_rev_18_1_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rev 18.1's stops (whitespace, `&`, `#`, quotes, backslash)."""
    monkeypatch.setattr(A, "_GLUED_STOPS", frozenset("&#\"'\\"))
    assert _code_and_log_failures() != []


# ------------------------------------ a value the redactor marked twice ------ #
#
# When the redactor writes two markers into one value (a key word inside
# it: `password=KtJ0R$secret=KOmx@Zq9JTe`), the run glued to the first
# marker ends at the second; the glued run follows on after it (rev 18.1's
# board, F-2). The claim is narrower next to an ARN body: the follow-on
# goes past one only when it ends in a key's `=` (tested below); a key in
# an ARN's resource after the redactor's marker inside the ARN keeps its
# tail (pinned below, disclosed).

_INNER_KEYS = ("secret", "password", "code", "token", "api_key")
_INNER_PUNCT = "!@$%^*|:~+/._-"


def _marked_twice_failures(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    alnum = string.ascii_letters + string.digits
    bad = []
    for _ in range(count):
        tail = "Zq9" + "".join(rng.choice(alnum) for _ in range(4))
        value = (
            "".join(rng.choice(alnum) for _ in range(rng.randint(4, 8)))
            + rng.choice(_INNER_PUNCT)
            + rng.choice(_INNER_KEYS)
            + rng.choice("=:")
            + "".join(rng.choice(alnum) for _ in range(rng.randint(4, 8)))
            + rng.choice(_INNER_PUNCT)
            + tail
        )
        for form in ("password={v}", "token={v} end", "X-Api-Key: {v}"):
            text = form.format(v=value)
            for label, out in _tail_surfaces(text).items():
                if tail in out:
                    bad.append(f"{label} {text!r} -> {out!r}")
    return bad


def test_a_value_marked_twice_keeps_no_tail() -> None:
    bad = _marked_twice_failures(300, 19)
    assert bad == [], bad[:10]


def test_a_value_marked_twice_mutant_rev_18_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rev 18.1's run, which stopped at the next marker."""
    monkeypatch.setattr(A, "_runs_on_into", lambda markers, i, glued, end: False)
    assert _marked_twice_failures(300, 19) != []


# ------------------------------------------------ ARN under any key --------- #


@pytest.mark.parametrize(
    "text",
    [
        "SECRET_ID=arn:aws:secretsmanager:us-east-1:123456789012:secret:MySecret-a1b2c3",
        "secret=arn:aws:secretsmanager:us-east-1:123456789012:secret:x",
        "token=arn:aws-cn:s3:::bucket/key",
    ],
)
def test_an_arn_under_any_key_is_the_redactors_output(text: str) -> None:
    """An ARN's body after the marker the redactor wrote over `arn` is kept
    under non-query keys (rev 18.1's board, F-3)."""
    assert _ours_e(text) == _main_e(text)
    assert _ours_p(text) == _main_p(text)


def test_qualified_query_withholds_the_entire_arn_value() -> None:
    text = (
        "see https://h.example/cb?secret_arn=arn%3Aaws%3Asecretsmanager%3Aus-east-1"
        "%3A123456789012%3Asecret%3Ax&page=2"
    )
    previous = (
        "see https://h.example/cb?secret_arn=[REDACTED]%3Aaws%3Asecretsmanager"
        "%3Aus-east-1%3A123456789012%3Asecret%3Ax&page=2"
    )
    assert _main_e(text) == previous
    assert _main_p(text) == previous
    expected = "see https://h.example/cb?secret_arn=[REDACTED]&page=2"
    assert _ours_e(text) == expected
    assert _ours_p(text) == expected


def test_an_arn_body_needs_a_service_field() -> None:
    """Narrow: a partition with no service field after it is a tail."""
    for label, out in _tail_surfaces("password=Pa55wd:aws:tailtail").items():
        assert "tailtail" not in out, (label, out)
    for label, out in _tail_surfaces("token=Pa55wd:aws:tailtail end").items():
        assert "tailtail" not in out, (label, out)


@pytest.mark.parametrize(
    ("text", "tail"),
    [
        ("token=Ab3cd:aws:s3:::x/token=Zz9!TAIL77", "TAIL77"),
        ("secret=arn:aws:s3:::b/password=Ab3!cdTAIL9x", "cdTAIL9x"),
        ("token=arn:aws-cn:s3:::bucket/api_key=Qq7$rrTAIL55 end", "rrTAIL55"),
    ],
)
def test_the_follow_on_goes_past_an_arn_body_to_the_next_key(
    text: str, tail: str
) -> None:
    """An ARN body glued between two markers is kept, and when it ends in a
    key's `=` the next marker starts another value: its tail is removed
    (the implementation board's N-1)."""
    for label, out in _tail_surfaces(text).items():
        assert tail not in out, (label, out)
    assert ":aws" in _ours_e(text), _ours_e(text)


def test_the_follow_on_past_an_arn_body_mutant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rev 19 rule: the chain always stopped at an ARN body."""
    monkeypatch.setattr(A, "_key_before_the_next_marker", lambda text, glued: False)
    out = _ours_e("token=Ab3cd:aws:s3:::x/token=Zz9!TAIL77")
    assert "TAIL77" in out, out


def test_disclosed_a_key_inside_an_arn_after_its_inner_marker_keeps_its_tail() -> None:
    """Pins a disclosed residual: when the redactor's marker sits INSIDE the
    ARN (the region), the chain stops at the ARN's own colon, and a key in
    the resource after a later marker keeps its tail on E."""
    text = "SECRET_ID=arn:aws:secretsmanager:us-east-1:123456789012:secret:x/password=Kt!J0RTAIL9"
    assert "!J0RTAIL9" in _ours_e(text)


def test_arn_body_mutant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(A, "_ARN_BODY_RE", re.compile(r"(?!)"))
    text = "SECRET_ID=arn:aws:secretsmanager:us-east-1:123456789012:secret:x"
    assert _ours_e(text) != _main_e(text)


def test_the_end_of_a_value_mutant_rev_17_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rev 17's rule (a span that starts on a marker is dropped) restored."""
    monkeypatch.setattr(A, "_glued_run_end", lambda text, start, end: start)
    punct = "".join(c for c in _TAIL_PUNCT if c not in _TAIL_STOPS)
    assert _tail_failures(punct, 30, 18) != []


_HEX_TICKET = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


@pytest.mark.parametrize("header", ["Cookie", "cookie", "Set-Cookie", "set-cookie"])
@pytest.mark.parametrize(
    "first", ["csrftoken=abc123", "sid=abc123", "session=abc123", "token=x1y2z3"]
)
@pytest.mark.parametrize("name", [".ASPXAUTH", "auth", "ticket", "remember_me", "ID"])
def test_a_cookie_header_keeps_no_ticket(header: str, first: str, name: str) -> None:
    value = f"{first}; {name}={_HEX_TICKET}; Path=/"
    obj = {"headers": {header: value}}
    for out in (
        _ours_e(json.dumps(obj)),
        _ours_p(json.dumps(obj)),
        json.dumps(_ours_process(obj)),
    ):
        assert _HEX_TICKET not in out, out


def test_a_pem_block_with_a_marker_inside_is_redacted_whole() -> None:
    body = "\n".join(
        [
            "MIIEpAIBAAKCAQEA3Tz2mr7SZiAMfQyuvBjM9Oi",
            "Zq1x0sid",
            "eXno5n8Iq7Zp2Lk9Wx4RAbCdEf01",
        ]
    )
    block = f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----"
    assert _MARKER_SPLIT_RE.search(_main_e(block)) is not None  # the case at issue
    for surface in (_ours_e, _ours_p):
        out = surface(block)
        for line in body.split("\n"):
            assert line not in out, out
        assert "eXno5n8I" not in out, out


def test_bearer_value_lookahead_mutant() -> None:
    """A lookahead that asks whether a run of whitespace escapes is followed
    by a value rescans the run at every split of the separator: quadratic on
    `bearer` + many `\\n` escapes in a JSON string (the slow-tier sweep found
    it during rev 17). The one-character form is linear."""
    import time

    text = json.dumps({"t": "bearer" + "\n" * 16_384})
    started = time.perf_counter()
    sanitize_auth_diagnostic(text, max_length=None)
    assert time.perf_counter() - started < 1.0
    one_char = "(?!" + A._JSON_SPACE_ESCAPE + ")"
    assert one_char in A._BEARER_RE.pattern
    slow = re.compile(
        A._BEARER_RE.pattern.replace(
            one_char,
            "(?!(?:" + A._JSON_SPACE_ESCAPE + r")+(?:[\s,;\"'()\[\]{}<>]|$))",
        ),
        A._BEARER_RE.flags,
    )
    started = time.perf_counter()
    list(slow.finditer(json.dumps({"t": "bearer" + "\n" * 16_384})))
    assert time.perf_counter() - started > 0.3
