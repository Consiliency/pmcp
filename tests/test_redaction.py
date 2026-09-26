"""Both directions of secret redaction, pinned together (Consiliency/pmcp#234).

The redactor stands between a downstream server's error text and a
prompt-injectable context window. A false negative puts a credential into that
window; a false positive teaches readers that `[REDACTED]` means nothing. So a
redactor tested only on secrets gets tuned until it redacts everything, and one
tested only on prose gets tuned until it redacts nothing. This file holds both
corpora and ranks them equally: `PROSE` must survive byte-identical, and every
`CREDENTIALS` entry must vanish with its surroundings intact.

Both surfaces are covered -- `sanitize_auth_diagnostic` (the engine, used
directly by the client manager, the CLI and the doctor) and
`PolicyManager.redact_secrets` (the engine plus the operator's patterns).
"""

from __future__ import annotations

import base64
import gzip
import json
import random
import re
import string
import uuid
from pathlib import Path

import pytest

from pmcp.auth import (
    AUTH_DIAGNOSTIC_SECRET_KEYS,
    AUTH_SECRET_QUERY_KEYS,
    REDACTED,
    WEAK_SECRET_KEYS,
    apply_redaction_spans,
    collect_redaction_spans,
    redact_auth_url,
    sanitize_auth_diagnostic,
)
from pmcp.policy.policy import DEFAULT_REDACTION_PATTERNS, PolicyManager

# --------------------------------------------------------------------------- #
# The prose corpus. Every line is text a downstream server, pip, git, httpx or
# the interpreter has produced or could produce, and none of it is a credential.
# Each keyword the pre-#234 rule mangled appears at least once in the position
# that mangled it. Extend it when a false positive is found; never trim it to
# make a rule pass.
# --------------------------------------------------------------------------- #

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

TOKEN = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"


def _engine(text: str) -> str:
    return sanitize_auth_diagnostic(text, max_length=None)


def _policy(text: str) -> str:
    return PolicyManager().redact_secrets(text)


# === the prose direction ================================================== #


def test_prose_survives_the_engine_byte_identical() -> None:
    assert _engine(PROSE) == PROSE


def test_prose_survives_the_policy_surface_byte_identical() -> None:
    assert _policy(PROSE) == PROSE


def test_the_keyword_rule_needs_a_separator_or_a_credential_shaped_value() -> None:
    """`token bucket` is prose; `--token abc123def456` is a flag with a secret.

    The pre-#234 rule treated whitespace as a separator, so any 3+ letter word
    after a keyword vanished. `the secret to good code` survived only because
    `to` is two letters -- an accident, not a guard.
    """
    assert _engine("token bucket rate limiting is enabled") == (
        "token bucket rate limiting is enabled"
    )
    assert _engine("the secret ingredient is love") == "the secret ingredient is love"
    assert _engine("session expired, password reset sent") == (
        "session expired, password reset sent"
    )
    assert _engine("--token abc123def456") == "--token [REDACTED]"
    assert _engine("token secret-bearer failed") == "token [REDACTED] failed"


def test_bearer_is_a_scheme_not_a_word() -> None:
    """`Bearer <token>` is redacted; `bearer token` (the phrase) is not.

    On main `\\bbearer` also fired *inside* `secret-bearer failed` and redacted
    `failed` -- the word after the credential rather than the credential. A
    hyphenated word that ends in `bearer` is a word, not the scheme: what
    follows `secret-bearer` or `non-bearer` is not a token.
    """
    assert _engine("Missing bearer token") == "Missing bearer token"
    assert _engine("the bearer of bad news") == "the bearer of bad news"
    assert _engine("Bearer Token is required") == "Bearer Token is required"
    assert _engine("Bearer test-token") == "Bearer [REDACTED]"
    assert _engine("Bearer hunter2") == "Bearer [REDACTED]"
    assert _engine("secret-bearer hunter2") == "secret-bearer hunter2"
    assert _engine("non-bearer 2024-01-01 report") == "non-bearer 2024-01-01 report"
    assert _engine('Bearer realm="api", error="x"') == 'Bearer realm="api", error="x"'
    assert _engine("token_type=Bearer expires_in=3600") == (
        "token_type=Bearer expires_in=3600"
    )


def test_code_is_a_credential_only_in_the_oauth_sense() -> None:
    """Bare `code=` is the OAuth callback parameter; `status_code=` is a status.

    JSON-RPC (`{"code": -32601}`) is every MCP error and REST (`{"code":
    "not_found"}`) every other one, so even bare `code` keeps a word or number.
    """
    assert _engine('{"code": -32601, "message": "x"}') == (
        '{"code": -32601, "message": "x"}'
    )
    assert _engine('{"code": "not_found"}') == '{"code": "not_found"}'
    assert _engine("status_code=401 error_code=invalid_grant") == (
        "status_code=401 error_code=invalid_grant"
    )
    assert _engine("code=404 exit code 137") == "code=404 exit code 137"
    assert _engine("code=super-secret") == "code=[REDACTED]"
    assert _engine("auth_code=SplxlOBeZQQYbYS6WxSbIA") == "auth_code=[REDACTED]"
    assert _engine("device_code=a1b2c3d4e5f6a7b8c9d0") == "device_code=[REDACTED]"


def test_the_policy_defaults_probe_stays_invisible_to_the_engine() -> None:
    """`ghp_abcdefghijklmnop` proves the policy DEFAULTS apply (SECURITY.md C-13).

    Those proofs (`tests/test_project_source_consent_policy.py`,
    `tests/test_trust_boundaries_e2e.py`) assert the probe is redacted *because
    a default pattern is still present*. If the engine ever caught it too they
    would pass with the defaults dropped. The probe is deliberately whole-alpha
    after its prefix; keep it that way and keep the engine blind to it.
    """
    assert _engine("ghp_abcdefghijklmnop") == "ghp_abcdefghijklmnop"
    assert "ghp_abcdefghijklmnop" not in _policy("ghp_abcdefghijklmnop")


# === the credential direction ============================================= #


@pytest.mark.parametrize(
    ("text", "must_vanish", "must_survive"),
    [entry[1:] for entry in CREDENTIALS],
    ids=[entry[0] for entry in CREDENTIALS],
)
def test_the_engine_redacts_the_credential_and_keeps_its_surroundings(
    text: str, must_vanish: str, must_survive: list[str]
) -> None:
    out = _engine(text)
    assert must_vanish not in out, out
    assert "[REDACTED]" in out
    for kept in must_survive:
        assert kept in out, out


@pytest.mark.parametrize(
    ("text", "must_vanish", "must_survive"),
    [entry[1:] for entry in CREDENTIALS],
    ids=[entry[0] for entry in CREDENTIALS],
)
def test_the_policy_surface_redacts_the_credential_and_keeps_its_surroundings(
    text: str, must_vanish: str, must_survive: list[str]
) -> None:
    out = _policy(text)
    assert must_vanish not in out, out
    for kept in must_survive:
        assert kept in out, out


def test_a_url_path_credential_is_redacted_in_diagnostics_not_in_the_url() -> None:
    """The engine reaches a path segment; `redact_auth_url` deliberately does not.

    `redact_auth_url` is what `sanitize_url_elicitation_url` returns -- the URL
    the operator must OPEN to authorize -- and an IdP's authorization-server id
    in that path (`/oauth2/aus1a2b3c4D5e6F7g8h9/v1/authorize`) is exactly the
    shape a credential has. A diagnostic can lose it; the login flow cannot.
    """
    webhook = (
        "https://hooks.example/services/T0123ABCD/B0123ABCD/a1B2c3D4e5F6g7H8i9J0k1L2"
    )
    assert _engine(f"failed: {webhook}") == (
        "failed: https://hooks.example/services/T0123ABCD/B0123ABCD/[REDACTED]"
    )
    authorize = (
        "https://dev-1.okta.com/oauth2/aus1a2b3c4D5e6F7g8h9/v1/authorize?state=ok"
    )
    assert redact_auth_url(authorize) == authorize


def test_identifiers_that_are_not_credentials_are_kept() -> None:
    """The shape rule's named exemptions, one probe each.

    hex digests and UUIDs (uniform hex), `0x` addresses (uniform hex behind a
    prefix), camelCase (no `xAB` signature), identifiers with one embedded
    number (`X509Cert`), timestamps (low transition ratio), and hyphen-joined
    short pieces (`pmcp-7d9f8b6c5-x2k9q`: scored per segment, never whole).
    """
    for kept in [
        "3843d2f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
        "550e8400-e29b-41d4-a716-446655440000",
        "0x7f3a2b1c4d50",
        "UnicodeDecodeError",
        "IPv6Address",
        "Ed25519PrivateKey",
        "20260923T041200Z",
        "x86_64-linux-gnu",
        "ECDHE-RSA-AES256-GCM-SHA384",
        "pmcp-7d9f8b6c5-x2k9q",
    ]:
        assert _engine(kept) == kept


def test_payload_sized_runs_are_data_not_credentials() -> None:
    """A base64 image or encoded file survives; a 200-character token does not.

    `gateway.tasks_result` redacts by default and `process_output` JSON-dumps
    the whole result, so `ImageContent.data` goes through this engine. No
    vendor issues an unbroken token past 256 characters (JWTs are dot-joined
    and have their own rule; private keys are PEM blocks).
    """
    blob = base64.b64encode(bytes(range(256)) * 24).decode()
    assert len(blob) > 8000
    assert _engine(blob) == blob
    assert _policy(blob) == blob
    rng = random.Random(234)
    token = "".join(
        rng.choice(string.ascii_letters + string.digits) for _ in range(200)
    )
    assert _engine(f"leaked {token} here") == "leaked [REDACTED] here"


def test_a_slash_leading_payload_is_not_split_into_scored_pieces() -> None:
    """The payload bound applies to the WHOLE run, before any path splitting.

    JPEG base64 always starts `/9j/` and carries a `/` every ~64 characters;
    scoring each piece between slashes on its own turned an ordinary image into
    `[REDACTED]/[REDACTED]/...` (board finding on the first revision). A long
    run that reads as a route -- short pieces, two of them plain words -- is
    still split, so a credential segment deep in a REST path is still caught.
    """
    leading = "/" + "4eC39HqLyjWDarjtT1zdp7dc" + "/" + "A" * 8190
    assert _engine(leading) == leading
    assert _policy(leading) == leading
    rng = random.Random(234)
    body = base64.b64encode(bytes(rng.getrandbits(8) for _ in range(4500))).decode()
    jpeg = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgH" + body
    assert body.count("/") > 50  # the shape that broke: a slash every ~64 chars
    assert _engine(jpeg) == jpeg
    assert _policy(jpeg) == jpeg
    route = (
        "/api/v1/organizations/acme-corp/projects/"
        + "/".join(f"segment{i}" for i in range(30))
        + "/secrets/4eC39HqLyjWDarjtT1zdp7dc/versions/latest"
    )
    assert len(route) > 256
    assert _engine(route) == route.replace("4eC39HqLyjWDarjtT1zdp7dc", "[REDACTED]")


# === composition ========================================================== #


def test_redaction_is_idempotent() -> None:
    """`[REDACTED]` must not itself be re-matched; surfaces apply the engine twice."""
    for text in [PROSE, *[entry[1] for entry in CREDENTIALS]]:
        once = _engine(text)
        assert _engine(once) == once, text
        twice = _policy(text)
        assert _policy(twice) == twice, text


def test_redact_secrets_keeps_a_key_but_never_splits_inside_a_secret() -> None:
    """The post-pass splits `key=value` at the separator, and only there.

    On main the split took the FIRST `:`/`=` in the match, so an operator
    pattern for a base64 shape kept the secret and replaced its padding:
    `dXNlcjpwYXNzd29yZA= [REDACTED]`. The probe value is deliberately
    low-entropy (uniform hex letters) so the engine's shape pass leaves it
    alone and only the operator pattern -- and therefore the split -- is
    exercised; a real base64 secret would be gone before the post-pass ran.
    """
    manager = PolicyManager()
    manager._redaction_regexes = [re.compile(r"[A-Za-z0-9+/]{16,}={1,2}")]
    assert (
        sanitize_auth_diagnostic("abcdabcdabcdabcdabcd==") == "abcdabcdabcdabcdabcd=="
    )
    assert manager.redact_secrets("basic abcdabcdabcdabcdabcd== auth") == (
        "basic [REDACTED] auth"
    )
    manager._redaction_regexes = [re.compile(r"mykey\s*[:=]\s*\S+")]
    assert manager.redact_secrets("mykey: abcdef") == "mykey: [REDACTED]"


def test_the_engine_redacts_before_it_truncates() -> None:
    """The cut lands on `[REDACTED]`, never on a token prefix.

    The token starts at offset 391 and `max_length` is 400: were the cut taken
    first, nine characters of it would survive and no longer have a redactable
    shape.
    """
    text = "x" * 390 + " " + TOKEN
    out = sanitize_auth_diagnostic(text, max_length=400)
    assert len(out) == 400
    assert "ghp_" not in out
    assert out.endswith(" [REDACTED")


def test_process_output_never_ends_on_a_partial_token() -> None:
    """The byte cut is taken BEFORE redaction, so it must not split a token.

    With `max_bytes=300` the cut falls 200 bytes in, nine characters into the
    token; `ghp_16C7e` has no shape any rule recognises. Backing the cut up to
    the preceding boundary removes the exposed prefix.
    """
    policy = PolicyManager()
    output = "a" * 190 + " " + TOKEN + " " + "c" * 100
    processed = policy.process_output(output, redact=True, max_bytes=300)
    assert processed["truncated"] is True
    assert "ghp_" not in processed["result"], processed["result"]
    assert processed["result"].startswith("a" * 190)


def test_process_output_never_ends_on_a_partial_token_after_a_path() -> None:
    """The trailing run is the PATH plus the token, and the whole run is backed
    out of (board finding on the first revision: a 64-character bound measured
    on the run left `/bbb.../ghp_16C7e` in the output). Past 256 characters the
    run is a payload or a long path, and its last `/`-segment -- where a
    credential in a path sits -- is still backed out of.
    """
    policy = PolicyManager()
    output = "a" * 120 + " /" + "b" * 68 + "/" + TOKEN + " " + "c" * 100
    processed = policy.process_output(output, redact=True, max_bytes=300)
    assert processed["truncated"] is True
    assert "ghp_" not in processed["result"], processed["result"]
    assert processed["result"].startswith("a" * 120 + " ")
    long_path = "a" * 20 + " /" + "b" * 300 + "/" + TOKEN + " " + "c" * 100
    processed = policy.process_output(long_path, redact=True, max_bytes=450)
    assert processed["truncated"] is True
    assert "ghp_" not in processed["result"], processed["result"]
    assert processed["result"].startswith("a" * 20 + " /" + "b" * 300)


def test_process_output_still_truncates_a_single_giant_run() -> None:
    """A run of `b`s is not a credential, and the cut lands where it always
    did (`max_bytes - 100`): redaction ran BEFORE the cut, so nothing has to
    be backed out of (revs 2-3 backed the cut up to a run boundary; rev 4
    redacts the window first and the back-up is gone)."""
    processed = PolicyManager().process_output("b" * 1000, redact=True, max_bytes=600)
    assert processed["truncated"] is True
    assert processed["result"].startswith("b" * 500)
    processed = PolicyManager().process_output("b" * 400, redact=True, max_bytes=300)
    assert processed["truncated"] is True
    assert processed["result"].startswith("b" * 200 + "\n\n[... OUTPUT TRUNCATED")


def test_a_quoted_value_runs_to_its_closing_quote() -> None:
    """An escaped quote inside a JSON string does not end the value.

    `"[^"]*"` stopped at the `\\"` in `{"password": "a\\"hunter2"}` and left
    `hunter2"` behind on both surfaces (board finding on the first revision).
    """
    for text, expected in [
        ('{"password": "a\\"hunter2"}', '{"password": "[REDACTED]"}'),
        ("{'password': 'a\\'hunter2'}", "{'password': '[REDACTED]'}"),
        (
            '{"password": "hunter2", "user": "bob"}',
            '{"password": "[REDACTED]", "user": "bob"}',
        ),
    ]:
        assert _engine(text) == expected
        assert _policy(text) == expected


def test_an_earlier_pass_never_eats_the_boundary_a_later_pass_needs() -> None:
    """Keyed values are redacted first, and the looser passes stop at quotes.

    Rev 2 ran the `Bearer` pass before the keyword pass with a value class of
    `[^\\s,;]+`, so on `{"password": "hunter2 Bearer test-token"}` it consumed
    the closing quote and brace and the keyword rule could no longer match:
    `{"password": "hunter2 Bearer [REDACTED]` on both surfaces, `hunter2`
    visible (board finding on rev 2). The `Authorization` rule had the same
    value class and also missed a JSON-quoted key entirely.
    """
    for text, expected in [
        ('{"password": "hunter2 Bearer test-token"}', '{"password": "[REDACTED]"}'),
        ("{'password': 'hunter2 Bearer x'}", "{'password': '[REDACTED]'}"),
        (
            '{"authorization": "Bearer abc123def456", "x": 1}',
            '{"authorization": "[REDACTED]", "x": 1}',
        ),
        ('Authorization: "Bearer abc.def"', 'Authorization: "[REDACTED]"'),
        ('{"note": "see Bearer abc123def456"}', '{"note": "see Bearer [REDACTED]"}'),
        ('token="Bearer abc"', 'token="[REDACTED]"'),
    ]:
        assert _engine(text) == expected, text
        assert "hunter2" not in _policy(text) and "abc" not in _policy(text), text


def test_truncation_never_leaks_a_quoted_multi_word_password() -> None:
    """Sweep the cut across quoted passwords that contain spaces.

    The byte cut can land inside a quoted value after a space: no trailing
    token run ends there, and with the closing quote gone the rev-2 keyword
    rule could not match, so the first word of the password stood in the
    output (board finding on rev 2: 128 of 1 452 cuts). A quoted value with
    no closing quote on its line now runs to the end of the line. The sweep
    asserts that it actually covers the value -- a range that misses the cut
    region reports zero leaks for the wrong reason.
    """
    policy = PolicyManager()
    leaks: list[str] = []
    for prefix in (50, 120, 177, 190):
        for password in ("hunter2 tail", "correct horse battery staple", "p4ss w0rd!"):
            output = "a" * prefix + ' {"password": "' + password + '"}' + "c" * 100
            opening = output.index('"' + password)
            closing = opening + len(password) + 1
            inside = 0
            for max_bytes in range(prefix + 80, prefix + 201):
                cut = max_bytes - 100  # `truncate_output` leaves room for the marker
                inside += opening < cut < closing
                result = policy.process_output(output, redact=True, max_bytes=max_bytes)
                if any(word in result["result"] for word in password.split()):
                    leaks.append(
                        f"prefix={prefix} password={password!r} max_bytes={max_bytes}"
                    )
            assert inside > 0, (prefix, password)  # the sweep reached the value
    assert leaks == []


def test_no_pass_can_consume_a_boundary_another_pass_needs() -> None:
    """Every pass reads the original text; replacements land in one step.

    Rev 3 ran the URL pass first and rewrote the text: redacting the query
    value removed the backslash that escaped a quote, which turned the escaped
    quote into a closing one, and `{"password": "https://example.test/?token=
    abc123def456\\"hunter2"}` came out as `{"password": [REDACTED]hunter2"}` on
    both surfaces (board finding on rev 3). With span collection the keyed
    value's span contains the URL's span and wins.
    """
    text = '{"password": "https://example.test/?token=abc123def456\\"hunter2"}'
    assert _engine(text) == '{"password": "[REDACTED]"}'
    assert _policy(text) == '{"password": "[REDACTED]"}'
    # a URL that is NOT inside a keyed value keeps its host and route
    assert _engine("see https://example.test/?token=abc123def456&page=2.") == (
        "see https://example.test/?token=[REDACTED]&page=2."
    )


def test_truncation_after_a_backslash_cannot_expose_a_password() -> None:
    """The cut lands right after the `\\` of an escaped character.

    Rev 3 cut first and then asked the redactor to make sense of `"hunter2\\`
    (board finding on rev 3: 48 of 2 880 escape-heavy cuts leaked). Rev 4
    redacts the window before the cut, so the redactor sees the whole value.
    """
    output = "a" * 177 + " " + json.dumps({"password": "hunter2\\tail"}) + "c" * 100
    processed = PolicyManager().process_output(output, redact=True, max_bytes=300)
    assert processed["truncated"] is True
    assert "hunter2" not in processed["result"], processed["result"]


def test_the_redaction_window_reaches_past_the_cap() -> None:
    """A keyed value that straddles the cap is seen whole by the redactor.

    The window is `max_bytes + _REDACTION_WINDOW_SLACK` characters; a
    4 000-character value (PEM-sized) that starts before the cap and ends
    after it is inside the window and is redacted before the cut.
    """
    value = "".join(random.Random(4).choice(string.ascii_letters) for _ in range(4000))
    output = "a" * 190 + ' {"password": "' + value + '"}' + "c" * 100
    processed = PolicyManager().process_output(output, redact=True, max_bytes=400)
    assert processed["truncated"] is True  # the cut is at byte 300, inside the value
    assert value[:4] not in processed["result"], processed["result"][180:240]
    assert processed["result"].startswith("a" * 190 + ' {"password": "[REDACTED]')


# === properties =========================================================== #

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


def _probe_is_usable(text: str, encoded: str, probe: str) -> bool:
    """Skip a probe shorter than four characters or one the surrounding text
    already contains (the assertion could not tell a leak from the template)."""
    return len(probe) == 4 and probe not in text.replace(encoded, "", 1)


def test_property_every_keyed_password_is_redacted_whole() -> None:
    """Thousands of random passwords from every printable character.

    Quotes, backslashes, spaces, brackets, unicode: in JSON (ASCII-escaped and
    not), single-quoted, and -- for delimiter-free passwords -- bare and as a
    header. The redacted output never contains the first four characters of
    the value as it appeared. The board's cases (`a\\"hunter2`, `hunter2 Bearer
    x`, `https://…\\"hunter2`) are samples of this property.
    """
    rng = random.Random(234)
    checked = skipped = 0
    for password in _random_passwords(rng, 3000):
        for text, encoded, probe in _keyed_forms(password):
            if not _probe_is_usable(text, encoded, probe):
                skipped += 1
                continue
            checked += 1
            assert probe not in _engine(text), (password, text, _engine(text))
            assert probe not in _policy(text), (password, text, _policy(text))
    assert checked > 10000, (checked, skipped)


def test_property_truncation_never_exposes_a_keyed_password() -> None:
    """The same forms, with the cut swept across every offset of the value.

    `process_output(redact=True)` must never show the first four characters of
    the value, wherever the cut lands: before it, inside it (after a space,
    after a backslash, after a quote), or after it. The test asserts that the
    sweep actually lands inside every value it tries -- a sweep that misses
    the cut region reports zero leaks for the wrong reason.
    """
    rng = random.Random(4234)
    policy = PolicyManager()
    cuts = inside = values = 0
    for password in _random_passwords(rng, 300):
        prefix = rng.choice((50, 120, 177, 190))
        for form, encoded, probe in _keyed_forms(password):
            text = "a" * prefix + " " + form + "c" * 100
            if not _probe_is_usable(text, encoded, probe):
                continue
            start = text.index(encoded)
            start_bytes = len(text[:start].encode("utf-8"))
            end_bytes = start_bytes + len(encoded.encode("utf-8"))
            landed = 0
            for max_bytes in range(start_bytes - 2 + 100, end_bytes + 3 + 100):
                cut = max_bytes - 100  # `truncate_output` leaves room for the marker
                result = policy.process_output(text, redact=True, max_bytes=max_bytes)
                cuts += 1
                landed += start_bytes < cut < end_bytes
                assert probe not in result["result"], (
                    password,
                    form,
                    max_bytes,
                    result,
                )
            assert landed > 0, (password, form)
            inside += landed
            values += 1
    assert values > 1000 and cuts > 20000 and inside > 15000, (values, cuts, inside)


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


def test_property_prose_survives_byte_identical() -> None:
    """Thousands of generated prose/diagnostic lines survive both surfaces."""
    rng = random.Random(9234)
    for _ in range(2000):
        text = _random_prose(rng)
        assert _engine(text) == text, text
        assert _policy(text) == text, text


def _declared_secret_keys() -> set[str]:
    """Every key the redactor DECLARES sensitive, from both surfaces' sources.

    `AUTH_DIAGNOSTIC_SECRET_KEYS`, plus every name in the first alternation
    group of each `DEFAULT_REDACTION_PATTERNS` entry (`(secret|password|
    passwd|pwd)`, `(api[_-]?key|apikey)` expanded to `api_key`, `api-key`,
    `apikey`, ...). A key named anywhere as sensitive but not redacted in every
    form below fails `test_property_every_declared_key_is_redacted_in_every_form`
    on its own -- no case has to be hand-picked (board finding on rev 4:
    `passwd`, `pwd`, `private_key`, `credentials`, `auth` were named or
    expected and covered by nothing).
    """
    keys = set(AUTH_DIAGNOSTIC_SECRET_KEYS)
    for pattern in DEFAULT_REDACTION_PATTERNS:
        group = re.search(r"\(([a-z_|\[\]?-]+)\)", pattern)
        if group is None:
            continue  # a bare token shape (`sk-`, `ghp_`), not a key
        for name in group.group(1).split("|"):
            if "[_-]?" in name:
                keys.update(name.replace("[_-]?", sep) for sep in ("_", "-", ""))
            else:
                keys.add(name)
    keys.add("private-key")
    keys.add("privateKey")
    return keys


#: Bare (unquoted) syntaxes cannot carry a quote or a list/query separator
#: (`,` `;` `&`) in a value; everything else, including leading punctuation
#: and spaces, is fair.
_BARE_VALUE_ALPHABET = "".join(c for c in _PRINTABLE if c not in "\"',;&")


def _key_forms(key: str, value: str) -> list[tuple[str, str, str]]:
    """(text, encoded value, probe) for one key: JSON, single-quoted, `key=`,
    header. The bare forms carry the value's first token with quotes and list
    separators removed, which is all an unquoted syntax can carry; the probe
    is the first four encoded characters."""
    bare_tokens = "".join(c for c in value if c not in "\"',;&").split()
    # a bare value never ends on a closing bracket or a backslash
    # a bare value never starts on `[`/`{` nor ends on a closing bracket or a backslash
    bare = bare_tokens[0].lstrip("[{").rstrip(")]}\\") if bare_tokens else ""
    forms = [
        (json.dumps({key: value}), json.dumps(value), json.dumps(value)[1:5]),
        (
            "{'" + key + "': " + _single_quoted(value) + "}",
            _single_quoted(value),
            _single_quoted(value)[1:5],
        ),
    ]
    if bare and not bare.startswith("="):  # `key==x` is a comparison, not a value
        forms.append((f"{key}={bare}", bare, bare[:4]))
        forms.append((f"{key}: {bare}", bare, bare[:4]))
    return forms


def test_property_every_declared_key_is_redacted_in_every_form() -> None:
    """Every declared key × every form × random values, both surfaces.

    Values are drawn from every printable character (quoted forms) or every
    printable character but quotes and list separators (bare forms); a third
    of them start with punctuation and a third contain a space. For a WEAK
    key (`WEAK_SECRET_KEYS`) a plain word or number is kept by design, so
    those values are skipped for those keys; for a strong key nothing is
    skipped. The per-key result table in the plan comes from this loop.
    """
    rng = random.Random(5234)
    checked = 0
    leaks: list[str] = []
    for key in sorted(_declared_secret_keys()):
        for _ in range(60):
            alphabet = _PRINTABLE if rng.random() < 0.5 else _BARE_VALUE_ALPHABET
            value = "".join(rng.choice(alphabet) for _ in range(rng.randint(4, 20)))
            roll = rng.random()
            if roll < 0.33:
                value = rng.choice("!#$%&()*+-./:<=>?@[\\]^_`{|}~") + value
            elif roll < 0.66:
                value = value[:2] + " " + value[2:]
            for text, encoded, probe in _key_forms(key, value):
                if not _probe_is_usable(text, encoded, probe):
                    continue
                if (
                    key.lower() in WEAK_SECRET_KEYS
                    and _is_plain_word_or_number_for_test(encoded)
                ):
                    continue
                if not encoded.strip("\"'").strip():
                    continue
                checked += 1
                for surface, out in (
                    ("engine", _engine(text)),
                    ("policy", _policy(text)),
                ):
                    if probe in out:
                        leaks.append(f"{surface} key={key!r} text={text!r} out={out!r}")
    assert checked > 5000, checked
    assert leaks == [], "\n".join(leaks[:20])


def _is_plain_word_or_number_for_test(encoded: str) -> bool:
    """The weak-key exemption, restated: a word (letters/underscores) or a
    number, quotes and trailing sentence punctuation stripped."""
    value = encoded.strip("\"'").rstrip(".:!?")
    return bool(re.fullmatch(r"[A-Za-z_]+|[+-]?[0-9]+|0[xX][0-9a-fA-F]+", value))


# === rev 6: the composition property and the regressions it replaces ====== #


def test_secrets_inside_a_url_query_are_redacted_whatever_the_key() -> None:
    """The URL pass yields component spans, never a rewritten URL.

    Rev 5 replaced the whole URL with `redact_auth_url(url)` and dropped every
    span inside it as "contained" -- so a keyed value or a token under a
    query key `redact_auth_url` does not know leaked, and main (which ran the
    keyword rule over the rewritten URL) did better (board finding on rev 5).
    """
    for text, expected in [
        (
            "https://example.com/?aws_secret_access_key=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
            "https://example.com/?aws_secret_access_key=[REDACTED]",
        ),
        (
            "https://api.github.com/repos?access=" + TOKEN,
            "https://api.github.com/repos?access=[REDACTED]",
        ),
        (
            "https://example.com/login?pwd=hunter2",
            "https://example.com/login?pwd=[REDACTED]",
        ),
        (
            "https://user:pass@auth.example/cb?code=oauth-code&state=ok#access_token=abc.def",
            "https://auth.example/cb?code=[REDACTED]&state=ok",
        ),
        (
            "https://h.example/?q=4eC39HqLyjWDarjtT1zdp7dc&page=2",
            "https://h.example/?q=[REDACTED]&page=2",
        ),
        # grok/codex, round 4: userinfo stripping is a no-op rewrite for the
        # query, and `q`/`file` will never be on `AUTH_SECRET_QUERY_KEYS`
        (
            "https://alice:hunter2@example.test/cb?q=" + TOKEN,
            "https://example.test/cb?q=[REDACTED]",
        ),
        (
            "https://example.test/dl?file=sk-live-abc123def456",
            "https://example.test/dl?file=[REDACTED]",
        ),
    ]:
        assert _engine(text) == expected, text
        assert _policy(text) == expected, text
    # an OPERATOR pattern inside a URL path is never suppressed by a built-in
    manager = PolicyManager()
    manager._redaction_regexes = [re.compile(r"abcdef")]
    assert (
        manager.redact_secrets("https://example.test/abcdef")
        == "https://example.test/[REDACTED]"
    )
    assert _engine("password=https://h.example/?a=1,b=2") == "password=[REDACTED],b=2"
    assert "h.example" not in _policy("password=https://h.example/?a=1,b=2")
    # `redact_auth_url` itself is byte-for-byte main's: the elicitation URL
    assert (
        redact_auth_url("https://u:p@auth.example/cb?ticket=secret&x=1#frag")
        == "https://auth.example/cb?ticket=%5BREDACTED%5D&x=1"
    )


def test_a_containing_span_may_drop_an_inner_one_only_if_it_covers_it() -> None:
    """The merge rule, stated as a property of `apply_redaction_spans`.

    An outer span whose replacement is `[REDACTED]` or empty covers whatever
    it contains; any other outer replacement is weaker than the inner spans
    it would suppress (grok's framing), so the two merge to `[REDACTED]`.
    Rev 5's whole-URL rewrite was exactly such a weaker outer span.
    """
    text = "xx SECRET yy"
    inner = (3, 9, REDACTED)
    assert apply_redaction_spans(text, [(0, 12, "xx SECRET yy"), inner]) == "[REDACTED]"
    assert apply_redaction_spans(text, [(0, 12, "rewritten"), inner]) == "[REDACTED]"
    assert apply_redaction_spans(text, [(0, 12, REDACTED), inner]) == "[REDACTED]"
    assert apply_redaction_spans(text, [(0, 3, ""), inner]) == "[REDACTED] yy"


def test_a_literal_marker_in_the_input_is_not_a_shield() -> None:
    """A downstream server can write `[REDACTED]` itself; that must not disarm
    the rule next to it. Rev 5 dropped every span touching an existing marker
    (board finding: `password=hunter2[REDACTED]` survived, main redacted it).
    Existing markers are spans and merge with whatever touches them; a marker
    nothing touches is replaced by itself, so the surfaces stay idempotent.
    """
    for text, expected in [
        ("password=hunter2[REDACTED]", "password=[REDACTED]"),
        ("api_key=abc123def456[REDACTED]", "api_key=[REDACTED]"),
        ('{"password": "hunter2 [REDACTED]"}', '{"password": "[REDACTED]"}'),
        ('{"password": "[REDACTED] hunter2"}', '{"password": "[REDACTED]"}'),
        ("token=[REDACTED]abc123def456 tail", "token=[REDACTED] tail"),
        ("password=[REDACTED]", "password=[REDACTED]"),
        ('{"password": [REDACTED]}', '{"password": [REDACTED]}'),
        (
            "[REDACTED] [REDACTED_EMAIL] ghp_ sk-",
            "[REDACTED] [REDACTED_EMAIL] ghp_ sk-",
        ),
    ]:
        assert _engine(text) == expected, text
        assert "hunter2" not in _policy(text) and "abc123def456" not in _policy(text), (
            text
        )
    assert _policy("password= [REDACTED]") == _policy(_policy("password= [REDACTED]"))


def test_authorization_is_a_header_not_a_word() -> None:
    """`authorization: none` is prose; `if token == expected` is a comparison."""
    assert _engine("authorization: none") == "authorization: none"
    assert (
        _engine("Set the Authorization:\nheader first")
        == "Set the Authorization:\nheader first"
    )
    assert _engine("Authorization: Bearer abc.def") == "Authorization: [REDACTED]"
    assert _engine("Authorization=Basic dXNlcjpwYXNz") == "Authorization=[REDACTED]"
    assert _engine("if token == expected:") == "if token == expected:"
    assert (
        _engine("token = await self._get_token()")
        == "token = [REDACTED] self._get_token()"
    )


def _span_texts(text: str, spans: list[tuple[int, int, str]]) -> list[str]:
    """The source text of every covering span (replacement `[REDACTED]` or
    empty) of four or more characters that occurs exactly once in ``text``,
    so its absence from the output is unambiguous."""
    return [
        text[start:end]
        for start, end, replacement in spans
        if replacement in (REDACTED, "")
        and end - start >= 4
        and text.count(text[start:end]) == 1
    ]


_URL_SAFE = string.ascii_letters + string.digits + "-_.~%+"
_NON_SECRET_QUERY_KEYS = ("q", "page", "access", "u", "foo", "next", "redirect")


def _query_string_texts(rng: random.Random) -> list[str]:
    """Secrets in URL query strings under secret AND non-secret keys, with a
    keyed value around the URL sometimes."""
    texts = []
    for _ in range(400):
        secret = "".join(
            rng.choice(string.ascii_letters + string.digits) for _ in range(24)
        )
        key = rng.choice([*sorted(AUTH_SECRET_QUERY_KEYS), *_NON_SECRET_QUERY_KEYS])
        extra = "".join(rng.choice(_URL_SAFE) for _ in range(rng.randint(0, 8)))
        url = f"https://h.example/p/{extra}?{key}={secret}&page=2"
        if rng.random() < 0.3:
            url = f"https://u:{secret[:8]}@h.example/cb?{key}={secret}#frag"
        texts.append(
            rng.choice([url, f"see {url}.", f"password={url}", f'{{"note": "{url}"}}'])
        )
    return texts


def _marker_texts(rng: random.Random) -> list[str]:
    """Secrets next to, and inside quotes with, a literal `[REDACTED]`."""
    texts = []
    keys = ("password", "api_key", "token", "secret", "pwd")
    for _ in range(400):
        secret = "".join(
            rng.choice(string.ascii_letters + string.digits)
            for _ in range(rng.randint(6, 16))
        )
        key = rng.choice(keys)
        texts.append(
            rng.choice(
                [
                    f"{key}={secret}[REDACTED]",
                    f"{key}=[REDACTED]{secret}",
                    f'{{"{key}": "{secret} [REDACTED]"}}',
                    f'{{"{key}": "[REDACTED] {secret}"}}',
                    f"[REDACTED] {key}: {secret} [REDACTED]",
                ]
            )
        )
    return texts


def test_property_every_collected_span_is_gone_from_the_output() -> None:
    """For every span the redactor itself collected, the span's text is absent
    from the output -- on both surfaces, over the static corpora and over
    generators that put secrets inside URL query strings under secret and
    non-secret keys, and next to literal `[REDACTED]` markers. This is the
    property the containment rule and the marker rule broke on rev 5.
    """
    rng = random.Random(6234)
    texts = [entry[1] for entry in CREDENTIALS]
    for password in _random_passwords(rng, 500):
        texts.extend(text for text, _, _ in _keyed_forms(password))
    texts.extend(_query_string_texts(rng))
    texts.extend(_marker_texts(rng))
    policy = PolicyManager()
    checked = 0
    leaks: list[str] = []
    for text in texts:
        for surface, spans, out in (
            ("engine", collect_redaction_spans(text), _engine(text)),
            ("policy", policy.redaction_spans(text), policy.redact_secrets(text)),
        ):
            for piece in _span_texts(text, spans):
                checked += 1
                if piece in out:
                    leaks.append(f"{surface} {text!r} -> {out!r} still has {piece!r}")
    assert checked > 4000, checked
    assert leaks == [], "\n".join(leaks[:20])


def test_property_the_window_edge_never_emits_an_unredacted_value() -> None:
    """Nothing from past the cap is emitted except inside a span.

    A prefix the redactor shrinks (one oversized PEM block) put the window's
    far edge -- a second, unredacted cut -- into the returned text on rev 5:
    with the value straddling `max_bytes + 16384`, its head was emitted. The
    sweep asserts that some cuts straddle the edge.
    """
    policy = PolicyManager()
    max_bytes = 1000
    edge = max_bytes + 16384
    pem = (
        "-----BEGIN PRIVATE KEY-----" + "x" * (edge - 200) + "-----END PRIVATE KEY-----"
    )
    straddling = 0
    for pad in range(120, 200):
        text = pem + "\n" * pad + '{"password": "hunter2 tail"}' + "c" * 100
        start = text.index('"hunter2')
        end = start + len('"hunter2 tail"')
        straddling += start < edge < end
        result = policy.process_output(text, redact=True, max_bytes=max_bytes)
        assert "hunter2" not in result["result"], (pad, result["result"][-120:])
        assert len(result["result"].encode("utf-8")) <= max_bytes, pad
    assert straddling > 0


def test_the_window_edge_is_not_emitted_when_ordinary_values_shrink_it() -> None:
    """Codex's construction: no PEM needed. Two 8 300-char `token=` values fill
    the 16 684-char window with 70 chars of an unterminated JSON object at
    its far edge; redaction shrinks the window to ~100 chars, and rev 5
    returned all of it, `hunter2` included. Every value is under 16 KiB, so
    this is not the documented residual. Now only the cap, extended to the
    end of the span that straddles it, is kept.
    """
    text = ("token=" + "a" * 8300 + "\n") * 2
    text += '{"password": "hunter2 ' + "tail " * 20 + '"}'
    result = PolicyManager().process_output(text, redact=True, max_bytes=300)
    assert result["truncated"] is True
    assert "hunter2" not in result["result"], result["result"]
    assert result["result"].startswith("token=[REDACTED]\n")
    assert len(result["result"].encode("utf-8")) <= 300


#: (input, secrets main removes on at least one surface). Built by running
#: main @ 860636a's engine and policy over these inputs and recording every
#: token-character piece (3+ chars) that disappeared, minus main's collateral
#: (`next`, `home`: main's keyword rule ate the whole query tail; `access_token`:
#: a key name in a dropped fragment). Every one must still disappear here.
_MAIN_REDACTS: list[tuple[str, list[str]]] = [
    (
        "https://example.com/?aws_secret_access_key=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
        ["wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"],
    ),
    (
        "https://api.github.com/repos?access=ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        ["ghp_16C7e42F292c6912E7710c838347Ae178B4a"],
    ),
    ("https://example.com/login?pwd=hunter2", ["hunter2"]),
    ("https://example.com/login?password=hunter2&next=/home", ["hunter2"]),
    ("https://auth.example/cb?code=oauth-code&state=ok", ["oauth-code"]),
    (
        "https://auth.example/cb?bearer=secret-bearer&token=access-token",
        ["access-token", "secret-bearer"],
    ),
    (
        "https://auth.example/cb?jwt=eyJhbGciOiJIUzI1NiJ9.payload.sig",
        ["eyJhbGciOiJIUzI1NiJ9.payload.sig"],
    ),
    (
        "https://auth.example/cb?assertion=saml-secret&ticket=ticket-secret",
        ["saml-secret", "ticket-secret"],
    ),
    (
        "https://user:pass@auth.example/cb?code=oauth-code",
        ["oauth-code", "pass", "user"],
    ),
    ("https://auth.example/cb#access_token=abc.def.ghi", ["abc.def.ghi"]),
    ("password=hunter2", ["hunter2"]),
    ("password: hunter2", ["hunter2"]),
    ("api_key=abc123def456", ["abc123def456"]),
    ("secret=s3cr3t", ["s3cr3t"]),
    ("client_secret=abc", ["abc"]),
    ("access_token=AbC123dEf456GhI", ["AbC123dEf456GhI"]),
    ("refresh_token=xyz789xyz789", ["xyz789xyz789"]),
    ("authorization: Bearer abc.def", ["Bearer", "abc.def"]),
    ("Authorization=Basic dXNlcjpwYXNz", ["Basic"]),
    ("Bearer abc123def456", ["abc123def456"]),
    ("sk-live-abc123def456", ["sk-live-abc123def456"]),
    (
        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        ["ghp_16C7e42F292c6912E7710c838347Ae178B4a"],
    ),
    (
        "github_pat_11ABCDEFG0abcdefghijklmnop_Ab3dEf6GhI9jKl2",
        ["github_pat_11ABCDEFG0abcdefghijklmnop_Ab3dEf6GhI9jKl2"],
    ),
    ("set-cookie: session=abc123; Path=/", ["abc123", "session"]),
    ("cookie: sid=abc123def", ["abc123def", "sid"]),
    ("sid=abc123def456", ["abc123def456"]),
    ("session=013G8iK4noj1iNbSTqJVFEX6", ["013G8iK4noj1iNbSTqJVFEX6"]),
    (
        "id_token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1",
        ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1"],
    ),
    ("saml=PHNhbWw6QXNzZXJ0aW9u", ["PHNhbWw6QXNzZXJ0aW9u"]),
    ("assertion=abc123def456", ["abc123def456"]),
    ("tenant_id=acme-123", ["acme-123"]),
    ("tenant-id: acme-123", ["acme-123"]),
    ("aws_secret=wJalrXUtnFEMI", ["wJalrXUtnFEMI"]),
    ("aws_access=AKIAIOSFODNN7EXAMPLE", ["AKIAIOSFODNN7EXAMPLE"]),
    ("passwd=hunter2", ["hunter2"]),
    ("pwd=hunter2", ["hunter2"]),
    ("apikey: abc123def456", ["abc123def456"]),
    ("X-Api-Key: abc123def456", ["abc123def456"]),
    (
        "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1",
        ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1"],
    ),
    ("--token abc123def456 --password hunter2", ["abc123def456", "hunter2"]),
    ("token: abc123def456", ["abc123def456"]),
    ("auth_code=SplxlOBeZQQYbYS6WxSbIA", ["SplxlOBeZQQYbYS6WxSbIA"]),
]


@pytest.mark.parametrize(
    ("text", "secrets"), _MAIN_REDACTS, ids=[t[:40] for t, _ in _MAIN_REDACTS]
)
def test_never_worse_than_main(text: str, secrets: list[str]) -> None:
    for secret in secrets:
        assert secret not in _engine(text), (secret, _engine(text))
        assert secret not in _policy(text), (secret, _policy(text))


# === rev 7: the differential against main is THE never-worse test ========= #


def test_quoted_bearer_and_httpie_and_ruby_separators() -> None:
    """Rev 6 regressions against main, each a rule narrowed for a false
    positive without re-running the differential (board round 5).

    A quote before `Bearer` is how every JSON-serialised string arrives;
    `:=`/`==` are httpie's syntax; `=>` is Ruby's. `if token == expected:`
    (the false positive the narrowing was for) still survives.
    """
    for text, expected in [
        ('{"text": "Bearer test-token"}', '{"text": "Bearer [REDACTED]"}'),
        ("'Bearer abc123def456'", "'Bearer [REDACTED]'"),
        ('"Bearer abc123def456"', '"Bearer [REDACTED]"'),
        ("password:=hunter2", "password:=[REDACTED]"),
        ("token:=abc123def456", "token:=[REDACTED]"),
        ("password==hunter2", "password==[REDACTED]"),
        ('{"password"=>"hunter2"}', '{"password"=>"[REDACTED]"}'),
        ("if token == expected:", "if token == expected:"),
        ("token_type=Bearer expires_in=3600", "token_type=Bearer expires_in=3600"),
    ]:
        assert _engine(text) == expected, text
        assert "hunter2" not in _policy(text) and "abc123def456" not in _policy(text), (
            text
        )
    assert PolicyManager().process_output({"text": "Bearer test-token"}, redact=True)[
        "result"
    ] == {"text": "Bearer [REDACTED]"}


def test_percent_encoded_query_values_are_decoded_before_the_detectors() -> None:
    encoded = "".join(f"%{ord(c):02X}" for c in "ghp_") + TOKEN[4:]
    assert (
        _engine(f"https://h.example/?q={encoded}") == "https://h.example/?q=[REDACTED]"
    )
    assert (
        _policy(f"https://h.example/?q={encoded}") == "https://h.example/?q=[REDACTED]"
    )
    assert (
        _engine("https://h.example/?q=%20hello%20world")
        == "https://h.example/?q=%20hello%20world"
    )


def test_a_value_may_sit_on_the_next_indented_line() -> None:
    """YAML block style, pretty-printed JSON and a folded header are formats;
    a keyword at the end of a sentence followed by an unindented line, or by
    a bullet, is prose."""
    assert _engine('"password":\n    "hunter2"') == '"password":\n    "[REDACTED]"'
    assert _engine("password:\n  hunter2") == "password:\n  [REDACTED]"
    assert _engine("[x-api-key\n  abc123def456]") == "[x-api-key\n  [REDACTED]]"
    assert (
        _engine("Missing bearer token:\nthe bearer of")
        == "Missing bearer token:\nthe bearer of"
    )
    assert _engine("token:\n  - a bullet") == "token:\n  - a bullet"


# --- the differential ------------------------------------------------------- #

_DIFF_KEYS = [
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "client_secret",
    "session",
    "sid",
    "cookie",
    "set-cookie",
    "authorization",
    "bearer",
    "jwt",
    "saml",
    "assertion",
    "id_token",
    "aws_secret",
    "aws_access",
    "tenant_id",
    "code",
    "auth_code",
    "x-api-key",
    "private_key",
    "credentials",
]
_DIFF_VALUES = [
    "hunter2",
    "abc123def456",
    "s3cr3t",
    "correct-horse-battery-staple",
    "4eC39HqLyjWDarjtT1zdp7dc",
    "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
    "sk-live-abc123def456",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.N2QwODhm",
    "AKIAIOSFODNN7EXAMPLE",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "dXNlcjpwYXNzd29yZA==",
    "Xk9#mQ2vL",
    "p@ss w0rd",
    "abc",
    "12345678",
    "letters",
    "a.b.c",
    "x-y-z",
    "https://h.example/?t=1",
    "(paren)",
    "tok[1]",
    "🙂ß",
]
_DIFF_SEPS = [
    "=",
    ": ",
    ":",
    " = ",
    "=>",
    " => ",
    "->",
    ":=",
    "\n  ",
    ":\n  ",
    "\t",
    " ",
    "  ",
    '="{}"',
    "='{}'",
    ': "{}"',
    '": "{}"',
    '\\": \\"{}\\"',
    " is ",
    "|",
    # rev 8 (grok): CRLF header folding and Windows dumps, a no-break space,
    # and an unindented CRLF continuation -- every whitespace class a rule touches
    ":\r\n  ",
    "\r\n  ",
    ":\xa0",
    ":\r\n",
]
_DIFF_WRAPS = [
    "{}",
    "{} tail",
    "prefix {}",
    "{{{}}}",
    "[{}]",
    "({})",
    '"{}"',
    "'{}'",
    '{{"a": "{}"}}',
    "<x>{}</x>",
    "-- {} --",
    "https://h.example/?{}",
    "https://h.example/?x=1&{}&y=2",
    "https://h.example/#{}",
    "https://u:p@h.example/{}",
    "curl -H '{}' https://h.example",
    "export {}",
    "log: {}",
    "{}\n{}",
    "{}; {}",
]
_DIFF_TOKEN = re.compile(r"[A-Za-z0-9_.+/-]{3,}")


def _differential_corpus() -> list[tuple[str, str, str, str, str]]:
    """The board's differential corpus (seed 20260923, 4 000 inputs): keys ×
    values × separators × wraps -- the seat's generator with four separators
    added in rev 8 (which reshuffles the draws); the oracle fixture was
    recorded from `main` @ 860636a over exactly these rows."""
    rng = random.Random(2026_09_23)
    out = []
    for _ in range(4000):
        key = rng.choice(_DIFF_KEYS)
        if rng.random() < 0.3:
            key = key.upper() if rng.random() < 0.5 else key.title()
        value = rng.choice(_DIFF_VALUES)
        sep = rng.choice(_DIFF_SEPS)
        kv = f"{key}{sep.format(value)}" if "{}" in sep else f"{key}{sep}{value}"
        if kv.startswith(key) and '"' in sep and "{}" in sep and sep.startswith('"'):
            kv = f'"{kv}'
        wrap = rng.choice(_DIFF_WRAPS)
        text = wrap.format(kv, kv) if wrap.count("{}") == 2 else wrap.format(kv)
        out.append((text, key, sep, value, wrap))
    return out


def _main_oracle() -> dict[str, list]:
    """`main` @ 860636a's removals: per string-corpus row the token pieces its
    engine and policy removed; per dict-corpus row the pieces `process_output`
    removed from the serialised result."""
    blob = (
        Path(__file__).parent / "fixtures" / "redaction_main_oracle.b64"
    ).read_text()
    return json.loads(gzip.decompress(base64.b64decode(blob)).decode("utf-8"))


#: Words main removed as collateral (its `[\s:=]+` rule ate the word after a
#: keyword, its URL rewrite dropped userinfo, its Bearer rule ate the scheme
#: word, its policy default ate the key name itself): never a secret, never
#: counted.
_DIFF_COLLATERAL = frozenset(
    {
        "bearer",
        "basic",
        "token",
        "tail",
        "prefix",
        "export",
        "curl",
        "example",
        "h.example",
        "https",
        "http",
    }
)


_DIFF_WHITESPACE_SEPS = frozenset({" ", "  ", "\t", "\n  ", "\r\n  "})
_DIFF_PROSE_SEPS = frozenset({" is ", "|", "->"})


def _accepted_regression_class(
    key: str, sep: str, value: str, kept: list[str], wrap: str = ""
) -> str | None:
    """Rows where this redactor keeps a piece main removed, BY DESIGN -- decided from
    the row's own key, separator and value, not from sniffing the text. Each
    class is listed in the plan's accepted-regression table with its count
    and reason. Anything not matched here is a bug."""
    base = key.lower()
    name = base.split("_")[-1].split("-")[-1]
    plain = _is_plain_word_or_number_for_test(value)
    if wrap.startswith("https://h.example/?") and "=" not in sep and " " not in sep:
        # `?x=1&bearer:abc…&y=2`: main's `parse_qsl` took `bearer:abc…` as a
        # KEY and re-spelled it `bearer%3Aabc…=` -- a re-encoding, not a
        # redaction (the same as `|` -> `%7C`); the value is still there
        return "inside a URL query main re-encoded `key<sep>value` as a key (`%3A`, `%7C`); not a redaction"
    if sep in _DIFF_PROSE_SEPS:
        # main "removed" the value in `?jwt|hunter2` by re-encoding `|` as
        # `%7C` inside a URL, and ate `is`/`->` rows through `[\s:=]+`; it left
        # `token|hunter2` outside a URL untouched
        return "`is`/`|`/`->` are not separators (main: URL re-encoding of `|`, or its whitespace rule)"
    if sep in _DIFF_WHITESPACE_SEPS:
        if name == "code":
            return "`code` never fires on a whitespace-only separator (main redacted `code<TAB>s3cr3t`)"
        if not _value_could_be_a_credential_for_test(value):
            return "whitespace-only separator with a non-credential-shaped value (D2)"
        return None
    if (
        sep == ":\r\n"
        and not value.startswith(('"', "'"))
        and not _value_could_be_a_credential_for_test(value)
    ):
        return "unindented line-break continuation with a non-credential-shaped value (D2 applied to a line break)"
    if sep == '\\": \\"{}\\"':
        return "backslash-escaped quotes in a plain string (JSON inside a leaf is Consiliency/pmcp#290)"
    if base in ("code", "auth_code", "credentials") and plain:
        return "weak key keeps a plain word or number"
    if base in ("authorization", "bearer") and plain:
        return "`Authorization`/`Bearer` followed by a plain word is prose"
    if "&" in kept[0] or any("&" in piece for piece in kept):
        return "a bare value stops at `&`"
    return None


def _value_could_be_a_credential_for_test(value: str) -> bool:
    """D2, restated: not a plain word or number, and digit-bearing and 6+
    chars or punctuated and 8+."""
    if _is_plain_word_or_number_for_test(value):
        return False
    return len(value) >= (6 if any(c.isdigit() for c in value) else 8)


def test_differential_against_main_never_worse_except_by_stated_class() -> None:
    """For every corpus row and surface, every ≥ 4-char piece main removed is
    removed here too, unless the row falls into an accepted-regression class.
    Rev 6 hand-picked its never-worse corpus from inputs main handled cleanly
    and reported 0 while two regressions existed; this test takes the board's
    corpus and main's recorded output as the oracle instead. Rule: no
    lookahead, lookbehind or rule narrowing lands without re-running this.
    """
    policy = PolicyManager()
    corpus = _differential_corpus()
    oracle = _main_oracle()["string"]
    assert len(corpus) == len(oracle) == 4000
    bugs: list[str] = []
    accepted: dict[str, int] = {}
    better = worse_rows = 0
    for (text, key, sep, value, wrap), (main_engine, main_policy) in zip(
        corpus, oracle
    ):
        here = {
            "engine": _engine(text),
            "policy": policy.redact_secrets(text),
        }
        removed_here = {
            surface: set(_DIFF_TOKEN.findall(text)) - set(_DIFF_TOKEN.findall(out))
            for surface, out in here.items()
        }
        if removed_here["engine"] - set(main_engine) or removed_here["policy"] - set(
            main_policy
        ):
            better += 1
        for surface, main_removed in (("engine", main_engine), ("policy", main_policy)):
            kept = [
                piece
                for piece in main_removed
                if len(piece) >= 4
                and piece.lower() not in _DIFF_COLLATERAL
                and piece.lower() != key.lower()  # main ate the KEY itself
                and piece not in removed_here[surface]
            ]
            if not kept:
                continue
            worse_rows += 1
            reason = _accepted_regression_class(key, sep, value, kept, wrap)
            if reason is None:
                bugs.append(
                    f"{surface} {text!r} keeps {kept} (main removed them); here: {here[surface]!r}"
                )
            else:
                accepted[reason] = accepted.get(reason, 0) + 1
    assert bugs == [], f"{len(bugs)} unaccepted regressions:\n" + "\n".join(bugs[:25])
    assert better > 1000, better


def _dict_corpus() -> list[tuple[dict, str, str, str]]:
    """Structured results (seed 20260924, 400): a JSON text leaf, a header
    leaf, a prose leaf with a URL, a top-level key, a nested key, a list."""
    rng = random.Random(2026_09_24)
    out: list[tuple[dict, str, str, str]] = []
    for _ in range(400):
        key = rng.choice(_DIFF_KEYS)
        value = rng.choice(_DIFF_VALUES)
        sep = rng.choice([": ", "=", ': "{}"'])
        kv = f"{key}{sep.format(value)}" if "{}" in sep else f"{key}{sep}{value}"
        shape = rng.choice(
            ["leaf-json", "leaf-header", "leaf-text", "top-key", "nested", "list"]
        )
        if shape == "leaf-json":
            obj: dict = {
                "content": [{"type": "text", "text": json.dumps({key: value})}],
                "isError": rng.random() < 0.5,
            }
        elif shape == "leaf-header":
            obj = {
                "content": [
                    {"type": "text", "text": f"HTTP/1.1 401\r\n{key}: {value}\r\n"}
                ]
            }
        elif shape == "leaf-text":
            obj = {
                "content": [
                    {
                        "type": "text",
                        "text": f"error: {kv} while calling https://h.example/?{key}={value}",
                    }
                ]
            }
        elif shape == "top-key":
            obj = {key: value, "note": "ok"}
        elif shape == "nested":
            obj = {"result": {"auth": {key: value}, "items": [1, 2]}}
        else:
            obj = {
                "content": [
                    {"type": "text", "text": value},
                    {"type": "text", "text": kv},
                ]
            }
        out.append((obj, key, sep, value))
    return out


def test_differential_on_structured_results_never_worse_than_main() -> None:
    """A dict result goes through the CORE path (serialise, then redact the
    window); JSON text inside a leaf is Consiliency/pmcp#290's problem. The
    bar here is main's: every piece main's `process_output` removed from the
    serialised result is removed here too, or the row is in an accepted class
    (the same classes as the string differential). 400 structured results;
    main removes something on 111 of them.
    """
    policy = PolicyManager()
    oracle = _main_oracle()["dict"]
    corpus = _dict_corpus()
    assert len(corpus) == len(oracle) == 400
    bugs: list[str] = []
    accepted: dict[str, int] = {}
    main_types = _main_oracle()["dict_types"]
    assert len(main_types) == 400 and main_types.count("dict") == 370
    for (obj, key, sep, value), main_removed, main_type in zip(
        corpus, oracle, main_types
    ):
        dumped = json.dumps(obj, indent=2)
        result = policy.process_output(obj, redact=True)["result"]
        # A dict result stays a dict wherever main's did: gateway.invoke and
        # tasks_result put this on the wire, and a redaction that breaks the
        # serialised JSON silently turns the result into a string.
        if main_type == "dict" and not isinstance(result, dict):
            bugs.append(f"{obj!r} came back as {type(result).__name__}: {result!r}")
        out = result if isinstance(result, str) else json.dumps(result, indent=2)
        removed_here = set(_DIFF_TOKEN.findall(dumped)) - set(_DIFF_TOKEN.findall(out))
        kept = [
            p
            for p in main_removed
            if len(p) >= 4
            and p.lower() not in _DIFF_COLLATERAL
            and p not in removed_here
        ]
        if kept:
            reason = _accepted_regression_class(key, sep, value, kept)
            if reason is None:
                bugs.append(f"{obj!r} keeps {kept}; here: {out!r}")
            else:
                accepted[reason] = accepted.get(reason, 0) + 1
    assert bugs == [], "\n".join(bugs[:10])
    # the named case: main catches the Bearer value in a dumped leaf; so does this
    result = policy.process_output(
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"password": "hunter2", "Authorization": "Bearer hunter2tok"}
                    ),
                }
            ]
        },
        redact=True,
    )["result"]
    assert "hunter2tok" not in json.dumps(result)
    assert result["content"][0]["text"].endswith(
        '"Authorization": "Bearer [REDACTED]"}'
    )  # JSON intact
    assert PolicyManager().process_output({"text": "Bearer test-token"}, redact=True)[
        "result"
    ] == {"text": "Bearer [REDACTED]"}


def test_redaction_never_breaks_a_json_document() -> None:
    """Wherever the input parses as JSON, the output does too, on both surfaces.
    Main broke 7 of the corpus's 258 JSON rows (it ate a closing quote); rev 8
    breaks none. A keyed value in quotes is redacted INSIDE the quotes."""
    policy = PolicyManager()
    checked = 0
    for text, *_ in _differential_corpus():
        try:
            json.loads(text)
        except ValueError:
            continue
        checked += 1
        for surface, out in (
            ("engine", _engine(text)),
            ("policy", policy.redact_secrets(text)),
        ):
            try:
                json.loads(out)
            except ValueError:
                pytest.fail(f"{surface} broke JSON: {text!r} -> {out!r}")
    assert checked == 258, checked


@pytest.mark.parametrize(
    ("obj", "expected"),
    [
        ({"password": "hunter2"}, {"password": REDACTED}),
        ({"password": ["hunter2", "s3cr3t99"]}, {"password": [REDACTED, REDACTED]}),
        (
            {"result": {"auth": {"api_key": "abc123def456"}}},
            {"result": {"auth": {"api_key": REDACTED}}},
        ),
        ({"password": ""}, {"password": ""}),
        ({"code": ["red", "x9Kq2mZ7"]}, {"code": ["red", REDACTED]}),
    ],
)
def test_structured_result_round_trips_as_a_dict(obj: dict, expected: dict) -> None:
    """The headline case, on the structured surface: redacted, still a dict."""
    assert PolicyManager().process_output(obj, redact=True)["result"] == expected


def test_pretty_printed_list_value_is_redacted_inside_its_quotes() -> None:
    """`"password": [\n  "hunter2"\n]` -- a list under a secret key. Rev 7 ate
    the `[`; main and the rev-8 spike left the value. Each quoted element is
    redacted in place and the brackets stay."""
    text = '{\n  "password": [\n    "hunter2"\n  ]\n}'
    for out in (_engine(text), _policy(text)):
        assert "hunter2" not in out
        assert json.loads(out) == {"password": [REDACTED]}


def test_operator_pattern_applies_to_a_percent_encoded_query_value(
    tmp_path: Path,
) -> None:
    """Item 4 of rev 8: an operator's own pattern, written for the decoded
    shape, still redacts the value when it arrives percent-encoded in a URL
    query (main decoded before matching)."""
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("redaction:\n  patterns:\n    - 'acme-[0-9]{6}'\n")
    policy = PolicyManager(policy_path=policy_file)
    plain = policy.redact_secrets("id acme-123456 end")
    assert "123456" not in plain  # the pattern is live on the plain surface
    out = policy.redact_secrets("https://h.example/?q=%61%63%6D%65%2D123456")
    assert "123456" not in out and "%61%63%6D%65" not in out, out


def test_url_decode_depth_bound_fails_closed() -> None:
    """Item 3 of rev 8: a value that decodes past the depth bound raises no
    RecursionError and is redacted whole rather than passed through."""
    text = "https://h.example/?q=" * 400 + "%" + "25" * 400 + "20"
    for out in (_engine(text), _policy(text)):
        assert "%25%25" not in out
        assert REDACTED in out


def test_crlf_and_no_break_space_are_whitespace_too() -> None:
    """HTTP header folding and Windows dumps use CRLF; `\\s` on main matched
    `\\xa0`. Rev 7 narrowed continuations to LF with an indent gate (grok):
    each of these leaked while main redacted it.
    """
    for text, expected in [
        ("password:\r\nhunter2", "password:\r\n[REDACTED]"),
        ("password:\r\n  hunter2", "password:\r\n  [REDACTED]"),
        ("token\r\nabc123def456", "token\r\n[REDACTED]"),
        ("Authorization:\r\n  s3cr3tvalue", "Authorization:\r\n  [REDACTED]"),
        ("password:\xa0hunter2", "password:\xa0[REDACTED]"),
        (
            "Missing bearer token:\r\nthe bearer of",
            "Missing bearer token:\r\nthe bearer of",
        ),
        ("token:\nthe bearer of", "token:\nthe bearer of"),
    ]:
        assert _engine(text) == expected, text
        assert _policy(text) == expected, text


def test_encoded_query_values_are_decoded_a_bounded_number_of_times() -> None:
    """Decoding a query value and asking the engine about it can meet another
    encoded URL; unbounded, a downstream-controlled diagnostic raised
    `RecursionError` (grok and codex, independently). Past three levels the
    value is treated as covered and redacted -- failing closed.
    """
    codex = "https://h.example/?q=" * 400 + "%" + "25" * 400 + "20"
    assert len(codex) < 10_000
    assert _engine(codex) == "https://h.example/?q=[REDACTED]"
    assert _policy(codex) == "https://h.example/?q=[REDACTED]"
    grok = "https://h.example/?q=" + "%25" * 400 + "67%68%70%5F" + TOKEN[4:]
    assert _engine(grok) == "https://h.example/?q=[REDACTED]"
    assert (
        _engine("https://h.example/?q=%20hello%20world")
        == "https://h.example/?q=%20hello%20world"
    )


def test_encoded_query_values_are_matched_by_the_policy_patterns_too() -> None:
    """`%67%68%70%5Fabcdefghijklmnop` decodes to the C-13 probe the engine
    ignores on purpose; the policy's `ghp_` default must still catch it, as
    main did (codex): the operator's and default patterns are asked about
    the decoded value and a hit redacts the encoded value whole.
    """
    text = "https://h.example/?q=%67%68%70%5Fabcdefghijklmnop"
    assert _engine(text) == text
    assert _policy(text) == "https://h.example/?q=[REDACTED]"
    manager = PolicyManager()
    manager._redaction_regexes = [re.compile(r"mytoken-[a-z]+")]
    assert (
        manager.redact_secrets("https://h.example/?q=mytoken%2Ddeadbeef")
        == "https://h.example/?q=[REDACTED]"
    )


def test_a_comparison_survives_on_both_surfaces() -> None:
    """`if token == expected:` is untouched by the engine AND by the policy
    surface (whose default `token` pattern now carries the same separator
    grammar; rev 7 pinned only the engine -- grok)."""
    assert _engine("if token == expected:") == "if token == expected:"
    assert _policy("if token == expected:") == "if token == expected:"
    assert _policy("password==hunter2") == "password=[REDACTED]"
    assert _policy("password:=hunter2") == "password:[REDACTED]"
