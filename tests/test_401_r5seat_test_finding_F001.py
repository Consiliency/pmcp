"""A redacted query value's own backslash stays withheld when the base URL rewrite re-spells its name (Consiliency/pmcp#407 r5)."""

import pytest

from pmcp.auth import sanitize_auth_diagnostic
from pmcp.policy.policy import PolicyManager


@pytest.mark.parametrize(
    "name", ["token+", "password+", "x,ode", "x%2Cookie", "x:ssertion"]
)
def test_respelled_name_secret_suffix_is_withheld(name: str) -> None:
    policy = PolicyManager()
    free = f"see https://example.test/p?{name}=quokkawombat" + "\\" + " next"
    for out in (
        sanitize_auth_diagnostic(free, max_length=None),
        policy.redact_secrets(free),
    ):
        assert "quokka" not in out, out
        assert "\\" not in out, out
    row = {
        "t": f'<a href="https://example.test/p?{name}=quokkawombat' + "\\" + '">P</a>'
    }
    result = policy.process_output(row, redact=True, max_bytes=10**6)["result"]
    if isinstance(result, dict):
        assert "quokka" not in result["t"], result
        assert "\\" not in result["t"], result
    else:
        assert "quokka" not in result, result
