"""President probe P5 (Consiliency/pmcp#407 round 5): a secret query value's own trailing
backslash run (and a `;;;\\` run) stays withheld when the query name ends in a credential
family followed by a literal `+` (a form-encoded space), as e222606 withheld it on the same
input, in free text and in the Consiliency/pmcp#401 href shape. Synthetic value."""

import json

import pytest

from pmcp.auth import sanitize_auth_diagnostic
from pmcp.policy.policy import PolicyManager

WORD = "hunter" + "word"


@pytest.mark.parametrize(
    "name", ["credential+", "x+credential+", "private_key+", "token+"]
)
@pytest.mark.parametrize("tail", ["\\", ";;;\\"])
def test_plus_terminated_credential_name_keeps_no_secret_suffix(
    name: str, tail: str
) -> None:
    policy = PolicyManager()
    url = f"https://example.test/p?{name}={WORD}{tail}"
    free = f"see {url} next"
    for out in (
        sanitize_auth_diagnostic(free, max_length=None),
        policy.redact_secrets(free),
    ):
        assert WORD not in out, out
        assert "\\" not in out and ";;;" not in out, out
    row = {"t": f'<a href="{url}">P</a>'}
    for out in (
        sanitize_auth_diagnostic(json.dumps(row), max_length=None),
        policy.redact_secrets(json.dumps(row)),
    ):
        assert WORD not in out, out
        assert ";;;" not in out and "]\\\\" not in out, out
    result = policy.process_output(row, redact=True, max_bytes=10**6)["result"]
    if isinstance(result, dict):
        value = result["t"].split('href="', 1)[1].split('"', 1)[0].rsplit("=", 1)[1]
        assert value in ("[REDACTED]", "%5BREDACTED%5D"), result
    else:
        assert WORD not in result
