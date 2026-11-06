"""Plain values stay withheld when the base URL rewrite glues a hex digit onto a family (Consiliency/pmcp#407 r5)."""

import json

import pytest

from pmcp.auth import sanitize_auth_diagnostic
from pmcp.policy.policy import PolicyManager


@pytest.mark.parametrize("name", ["x:uth", "x%3Auth", "x,redential", "x|redentials"])
def test_hex_glued_family_plain_value_is_withheld(name: str) -> None:
    policy = PolicyManager()
    free = f"see https://example.test/p?{name}=quokkawombat" + "\\" + " next"
    row = {"t": f'<a href="https://example.test/p?{name}=quokkawombat">P</a>'}
    result = policy.process_output(row, redact=True, max_bytes=10**6)["result"]
    for out in (
        sanitize_auth_diagnostic(free, max_length=None),
        policy.redact_secrets(free),
        sanitize_auth_diagnostic(json.dumps(row), max_length=None),
        policy.redact_secrets(json.dumps(row)),
        result if isinstance(result, str) else json.dumps(result),
    ):
        assert "quokka" not in out, out
