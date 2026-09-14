"""A project gateway policy needs consent, and can only ever narrow (S-11).

Two failures live here, and they are different failures:

* **Shadowing.** `DEFAULT_POLICY_PATHS` lists the project-relative names
  *before* the `~/.claude/` ones and discovery used to `break` at the first
  existing file, so a `.mcp-gateway-policy.yaml` committed to a repository
  replaced the operator's global policy outright -- and the fallback
  `GatewayPolicy()` is allow-all. Cloning a repo could silently unrestrict the
  gateway. CONSENT-7 closes this: an unapproved project policy is never applied,
  and never even parsed.
* **Widening.** Once a project policy *is* approved it still must not grant
  anything the operator's policy withholds. CONSENT-3 closes this with
  conjunctive composition (`IF-0-CONSENT-2`), which is deliberately *not* list
  intersection: intersecting the glob strings `github*` and `github-mcp` yields
  nothing, yet `github-mcp` is allowed by both.

The composition rules each have a naive form that widens, and every one of them
has a test here whose only job is to fail against that naive form:

* limits take `min()` over the user's **effective** value and the project's
  **explicitly-set** value. Min-over-explicitly-set-on-both-sides lets an
  approved project policy *raise* a ceiling the operator never set;
  min-over-effective lets a project policy that never mentions `limits` drag a
  raised operator limit back down through Pydantic defaults.
* redaction unions the two **effective** pattern sets, where "effective" is
  `patterns or DEFAULT_REDACTION_PATTERNS`. A raw list union drops every default
  pattern whenever the user list is empty -- a repository file quietly reducing
  secret redaction.
* `policy_digest` covers both policies, so a project policy is never invisible
  to telemetry.

`--policy` is untouched throughout: an explicit policy skips discovery, and
therefore composition, entirely.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from pmcp.policy.policy import DEFAULT_REDACTION_PATTERNS, PolicyManager
from pmcp.types import GatewayPolicy

# A user policy with an effect that is observable from the outside: `evil` is
# denied. Asserting enforcement rather than "a policy loaded" is what keeps a
# refuse-everything regression from passing this file.
_USER_DENIES_EVIL = {"servers": {"denylist": ["evil"]}}


def _discovery(
    monkeypatch: pytest.MonkeyPatch,
    *,
    project: Path | None = None,
    user: Path | None = None,
) -> None:
    """Point discovery at exactly these two candidates and nothing else.

    Both module lists are patched together. `USER_POLICY_PATHS` is what tells
    the manager which discovered path is operator-supplied (and therefore
    ungated); `DEFAULT_POLICY_PATHS` remains the single ordered search list, so
    patching it alone still suppresses the developer's real `~/.claude` policy
    exactly as it did before this lane.
    """
    users = [user] if user is not None else []
    monkeypatch.setattr("pmcp.policy.policy.USER_POLICY_PATHS", users)
    monkeypatch.setattr(
        "pmcp.policy.policy.DEFAULT_POLICY_PATHS",
        ([project] if project is not None else []) + users,
    )


def _user_only_digest(policy_data: dict[str, Any]) -> str:
    """The digest `policy_digest` produced before this lane, for one policy.

    Computed from the model rather than from a second `PolicyManager`, so
    "the project policy contributed nothing" is checked against a fixed value
    instead of against another run of the code under test.
    """
    payload = GatewayPolicy.model_validate(policy_data).model_dump_json(
        exclude_none=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# === an unapproved project policy contributes nothing at all ===


def test_unapproved_project_policy_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused bytes must never reach the parser, not merely never be applied.

    The probe is a project policy that is *schema-invalid*: `denylist` as a
    scalar. Consiliency/pmcp#202 makes a discovered policy that parses but fails validation a
    startup refusal, so an implementation that parsed before gating -- or that
    gated and then parsed anyway -- would raise `ValueError` here. Construction
    completing is the assertion.

    The read counters close the other half: the gate performs exactly one
    `read_bytes` to hash the content, and nothing may open the path again.
    A gate-then-reread would approve bytes nobody parses.
    """
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text("servers:\n  denylist: not-a-list\n")
    user = tmp_path / "user-policy.yaml"
    user.write_text(json.dumps(_USER_DENIES_EVIL))
    _discovery(monkeypatch, project=project, user=user)

    reads: list[str] = []
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes

    def counting_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if Path(self) == project:
            reads.append("read_text")
        return real_read_text(self, *args, **kwargs)

    def counting_read_bytes(self: Path, *args: Any, **kwargs: Any) -> bytes:
        if Path(self) == project:
            reads.append("read_bytes")
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    manager = PolicyManager()

    # Never parsed: an admitted schema-invalid policy would have raised.
    assert manager.is_server_allowed("evil") is False
    assert manager.policy_digest == _user_only_digest(_USER_DENIES_EVIL)
    # Never re-read: one hashing read, and no text read at all.
    assert reads == ["read_bytes"]


def test_unapproved_project_policy_does_not_shadow_the_user_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EC-CONSENT-7's falsifier: allow-all project file beside a user denial.

    On unchanged `main` the project file is found first, loaded, and `break`s
    the loop -- the user policy is never read and `evil` is allowed.
    """
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(json.dumps({"servers": {"allowlist": ["evil", "good"]}}))
    user = tmp_path / "user-policy.yaml"
    user.write_text(json.dumps(_USER_DENIES_EVIL))
    _discovery(monkeypatch, project=project, user=user)

    manager = PolicyManager()

    assert manager.is_server_allowed("evil") is False
    assert manager.is_server_allowed("good") is True
    # Contributed nothing, rather than merely nothing extra: the digest is the
    # one a manager with no project file at all would produce.
    assert manager.policy_digest == _user_only_digest(_USER_DENIES_EVIL)


def test_unapproved_project_policy_refusal_names_the_trust_approve_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A silent skip is a policy that stopped applying with no way to notice."""
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(json.dumps({"servers": {"allowlist": ["evil"]}}))
    _discovery(monkeypatch, project=project)

    with caplog.at_level(logging.WARNING, logger="pmcp.policy.policy"):
        PolicyManager()

    remediation = f"pmcp trust approve {project.resolve()}"
    assert [m for m in _warnings(caplog) if remediation in m]


# === an approved project policy narrows, and only narrows ===


def test_project_policy_cannot_allow_what_user_policy_denies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """EC-CONSENT-3's falsifier, across every boolean list predicate.

    All four surfaces are checked because a lane could compose `servers` and
    forget `tools`, and the gap would not show up anywhere else.
    """
    user = tmp_path / "user-policy.yaml"
    user.write_text(
        json.dumps(
            {
                "servers": {"denylist": ["evil"]},
                "tools": {"denylist": ["evil::*"]},
                "gateway_tools": {"denylist": ["gateway.provision"]},
                "resources": {"denylist": ["evil::*"]},
                "prompts": {"denylist": ["evil::*"]},
            }
        )
    )
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(
        json.dumps(
            {
                "servers": {"allowlist": ["evil"]},
                "tools": {"allowlist": ["evil::*"]},
                "gateway_tools": {"allowlist": ["gateway.provision"]},
                "resources": {"allowlist": ["evil::*"]},
                "prompts": {"allowlist": ["evil::*"]},
            }
        )
    )
    approve_project_file(project)
    _discovery(monkeypatch, project=project, user=user)

    manager = PolicyManager()

    assert manager.is_server_allowed("evil") is False
    assert manager.is_tool_allowed("evil::run") is False
    assert manager.is_gateway_tool_allowed("gateway.provision") is False
    assert manager.is_resource_allowed("evil::file") is False
    assert manager.is_prompt_allowed("evil::greet") is False


def test_project_policy_cannot_widen_a_user_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """Conjunctive, not intersection -- both halves asserted.

    `github-mcp` is the positive case that kills a list-intersection
    implementation: intersecting the glob *strings* `github*` and `github-mcp`
    is empty, yet both policies allow that server. `gitlab` is the negative
    case: a name the project allowlist would admit but the user's does not.
    """
    user = tmp_path / "user-policy.yaml"
    user.write_text(json.dumps({"servers": {"allowlist": ["github*"]}}))
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(json.dumps({"servers": {"allowlist": ["github-mcp", "gitlab"]}}))
    approve_project_file(project)
    _discovery(monkeypatch, project=project, user=user)

    manager = PolicyManager()

    assert manager.is_server_allowed("github-mcp") is True
    assert manager.is_server_allowed("gitlab") is False
    assert manager.is_server_allowed("github-other") is False


def test_project_policy_denial_still_applies_on_top_of_user_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """Narrowing is the one direction a project policy may move things.

    Denylists compose by union, which falls out of the conjunction: a name
    denied by either side is denied.
    """
    user = tmp_path / "user-policy.yaml"
    user.write_text(json.dumps({"servers": {"denylist": ["user-denied"]}}))
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(json.dumps({"servers": {"denylist": ["project-denied"]}}))
    approve_project_file(project)
    _discovery(monkeypatch, project=project, user=user)

    manager = PolicyManager()

    assert manager.is_server_allowed("user-denied") is False
    assert manager.is_server_allowed("project-denied") is False
    assert manager.is_server_allowed("neither") is True


# === limits: min over user EFFECTIVE and project EXPLICITLY-SET ===


def test_project_policy_limits_take_the_minimum_of_the_user_effective_and_project_explicit_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """Whichever side is lower wins, for all three limits, in both directions."""
    user = tmp_path / "user-policy.yaml"
    user.write_text(
        json.dumps(
            {
                "limits": {
                    "max_tools_per_server": 500,
                    "max_output_bytes": 10,
                    "max_output_tokens": 900,
                }
            }
        )
    )
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(
        json.dumps(
            {
                "limits": {
                    "max_tools_per_server": 7,
                    "max_output_bytes": 99999,
                    "max_output_tokens": 11,
                }
            }
        )
    )
    approve_project_file(project)
    _discovery(monkeypatch, project=project, user=user)

    manager = PolicyManager()

    assert manager.get_max_tools_per_server() == 7  # project is lower
    assert manager.get_max_output_bytes() == 10  # user is lower
    assert manager.get_max_output_tokens() == 11  # project is lower


def test_project_policy_unset_limits_do_not_lower_a_raised_user_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """The failure mode of min-over-*effective* values.

    The project policy never mentions `limits`, so its Pydantic defaults
    (100 / 50000 / 4000) are not a statement about anything. Taking `min()`
    against them would silently undo an operator who deliberately raised a
    ceiling -- a project file changing a limit by saying nothing.
    """
    user = tmp_path / "user-policy.yaml"
    user.write_text(
        json.dumps(
            {
                "limits": {
                    "max_tools_per_server": 500,
                    "max_output_bytes": 999999,
                    "max_output_tokens": 40000,
                }
            }
        )
    )
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(json.dumps({"servers": {"denylist": ["evil"]}}))
    approve_project_file(project)
    _discovery(monkeypatch, project=project, user=user)

    manager = PolicyManager()

    assert manager.get_max_tools_per_server() == 500
    assert manager.get_max_output_bytes() == 999999
    assert manager.get_max_output_tokens() == 40000
    # The project policy is genuinely loaded -- otherwise this test would pass
    # against an implementation that ignores project policies entirely.
    assert manager.is_server_allowed("evil") is False


def test_a_project_limit_cannot_raise_a_limit_the_user_never_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """The failure mode of min-over-*explicitly-set* values -- S-11 again.

    The user policy never mentions `limits`, so the only explicitly-set value
    in play is the project's `1000`. A `min()` over just the explicitly-set
    values returns 1000, and a repository file has raised the operator's
    ceiling tenfold. The user's effective value must always be a term.

    `limits: {}` on the project side is the same hazard once removed: it names
    the section without setting anything, so it still sets nothing.
    """
    user = tmp_path / "user-policy.yaml"
    user.write_text(json.dumps({"servers": {"denylist": ["evil"]}}))
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(
        json.dumps(
            {
                "limits": {
                    "max_tools_per_server": 1000,
                    "max_output_bytes": 10_000_000,
                    "max_output_tokens": 100_000,
                }
            }
        )
    )
    approve_project_file(project)
    _discovery(monkeypatch, project=project, user=user)

    manager = PolicyManager()

    assert manager.get_max_tools_per_server() == 100
    assert manager.get_max_output_bytes() == 50000
    assert manager.get_max_output_tokens() == 4000

    empty_section = tmp_path / "empty-limits" / ".mcp-gateway-policy.yaml"
    empty_section.parent.mkdir()
    empty_section.write_text("limits: {}\n")
    approve_project_file(empty_section)
    _discovery(monkeypatch, project=empty_section, user=user)

    assert PolicyManager().get_max_tools_per_server() == 100


# === redaction: union of EFFECTIVE pattern sets ===


def test_project_redaction_patterns_extend_rather_than_replace_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """The naive list union drops every default pattern.

    `_compile_redaction_patterns` uses `patterns or DEFAULT_REDACTION_PATTERNS`,
    so an empty user list means "the defaults". Unioning the *raw* lists gives
    `[] + ["mytoken-..."]`, which is truthy -- the defaults stop applying and a
    repository file has quietly reduced secret redaction for the whole gateway.
    The union is therefore over the **effective** sets.
    """
    user = tmp_path / "user-policy.yaml"
    user.write_text(json.dumps(_USER_DENIES_EVIL))  # no redaction section at all
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(
        json.dumps({"redaction": {"patterns": [r"mytoken-[A-Za-z0-9]+"]}})
    )
    approve_project_file(project)
    _discovery(monkeypatch, project=project, user=user)

    manager = PolicyManager()
    redacted = manager.redact_secrets(
        "ghp_abcdefghijklmnop and mytoken-deadbeef together"
    )

    assert "ghp_abcdefghijklmnop" not in redacted  # a DEFAULT pattern still applies
    assert "mytoken-deadbeef" not in redacted  # and the project pattern was added


def test_an_explicit_user_redaction_list_is_not_dropped_by_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """The mirror image: a project list must not displace the operator's own."""
    user = tmp_path / "user-policy.yaml"
    user.write_text(json.dumps({"redaction": {"patterns": [r"usertoken-[a-z]+"]}}))
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(json.dumps({"redaction": {"patterns": [r"projtoken-[a-z]+"]}}))
    approve_project_file(project)
    _discovery(monkeypatch, project=project, user=user)

    redacted = PolicyManager().redact_secrets("usertoken-abc projtoken-xyz")

    assert "usertoken-abc" not in redacted
    assert "projtoken-xyz" not in redacted
    # The user named its own patterns, so the defaults are still displaced --
    # the union is of effective sets, not an unconditional merge of defaults.
    assert DEFAULT_REDACTION_PATTERNS  # guard: the constant still exists
    assert "ghp_abcdefghijklmnop" in PolicyManager().redact_secrets(
        "ghp_abcdefghijklmnop"
    )


# === the digest, revocation, and the untouched paths ===


def test_policy_digest_covers_the_project_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """A project policy that changes behaviour must change the digest.

    Otherwise telemetry reports the operator's policy while the gateway is
    enforcing something narrower, and nobody can tell which policy ran.
    """
    user = tmp_path / "user-policy.yaml"
    user.write_text(json.dumps(_USER_DENIES_EVIL))

    first = tmp_path / "a" / ".mcp-gateway-policy.yaml"
    first.parent.mkdir()
    first.write_text(json.dumps({"servers": {"denylist": ["alpha"]}}))
    approve_project_file(first)
    _discovery(monkeypatch, project=first, user=user)
    with_first = PolicyManager().policy_digest

    second = tmp_path / "b" / ".mcp-gateway-policy.yaml"
    second.parent.mkdir()
    second.write_text(json.dumps({"servers": {"denylist": ["beta"]}}))
    approve_project_file(second)
    _discovery(monkeypatch, project=second, user=user)
    with_second = PolicyManager().policy_digest

    user_only = _user_only_digest(_USER_DENIES_EVIL)
    assert with_first != user_only
    assert with_second != user_only
    assert with_first != with_second
    # ...and not the *project*-only digest either, which is what `main` reports
    # today because the project file shadows the user file outright. Without
    # this pair the test would pass against unchanged `main`.
    assert with_second != _user_only_digest({"servers": {"denylist": ["beta"]}})
    composed = PolicyManager()
    assert composed.is_server_allowed("evil") is False  # user's denial survives
    assert composed.is_server_allowed("beta") is False  # project's too


def test_editing_an_approved_project_policy_revokes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    approve_project_file: Any,
) -> None:
    """EC-CONSENT-4 at the policy site: approval is of bytes, not of a path."""
    user = tmp_path / "user-policy.yaml"
    user.write_text(json.dumps({"servers": {"denylist": ["never"]}}))
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(json.dumps({"servers": {"denylist": ["evil"]}}))
    approve_project_file(project)
    _discovery(monkeypatch, project=project, user=user)

    assert PolicyManager().is_server_allowed("evil") is False

    project.write_text(json.dumps({"servers": {"denylist": ["evil", "also-evil"]}}))

    with caplog.at_level(logging.WARNING, logger="pmcp.policy.policy"):
        manager = PolicyManager()

    assert manager.is_server_allowed("evil") is True
    assert manager.policy_digest == _user_only_digest(
        {"servers": {"denylist": ["never"]}}
    )
    remediation = f"pmcp trust approve {project.resolve()}"
    assert [m for m in _warnings(caplog) if remediation in m and "changed" in m], (
        _warnings(caplog)
    )


def test_explicit_policy_path_skips_discovery_and_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """`--policy` is operator-supplied: no gate, no discovery, no composition.

    The project policy here is *approved* and would narrow if it were composed,
    so this fails against an implementation that composes regardless of how the
    base policy arrived -- not merely against one that forgets to gate.
    """
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(json.dumps({"servers": {"denylist": ["explicit-denied"]}}))
    project = tmp_path / ".mcp-gateway-policy.yaml"
    project.write_text(json.dumps({"servers": {"denylist": ["project-denied"]}}))
    approve_project_file(project)
    _discovery(monkeypatch, project=project)

    manager = PolicyManager(explicit)

    assert manager.explicit_policy is True
    assert manager.is_server_allowed("explicit-denied") is False
    assert manager.is_server_allowed("project-denied") is True
    assert manager.policy_digest == _user_only_digest(
        {"servers": {"denylist": ["explicit-denied"]}}
    )


def test_user_policy_alone_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """EC-CONSENT-5 at this site: no project file, byte-identical behaviour.

    The user-scoped path is discovered without any trust record, enforces on
    its own, keeps its limits verbatim, produces the pre-lane digest, and says
    nothing about consent.
    """
    user = tmp_path / "user-policy.yaml"
    data = {"servers": {"denylist": ["evil"]}, "limits": {"max_tools_per_server": 42}}
    user.write_text(json.dumps(data))
    _discovery(monkeypatch, project=tmp_path / ".mcp-gateway-policy.yaml", user=user)

    with caplog.at_level(logging.WARNING, logger="pmcp.policy.policy"):
        manager = PolicyManager()

    assert manager.is_server_allowed("evil") is False
    assert manager.is_server_allowed("good") is True
    assert manager.get_max_tools_per_server() == 42
    assert manager.policy_digest == _user_only_digest(data)
    assert _warnings(caplog) == []
