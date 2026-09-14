"""An auto-discovered policy that parses must not fail open (Consiliency/pmcp#202).

`PolicyManager` starts from `GatewayPolicy()`, and that default is **allow-all**:
every field is a `default_factory`, so a discarded policy is not a partial policy
-- it is no policy. Discarding a policy file therefore silently unrestricts the
gateway.

These tests pin the split the fix introduces, by *failure mode* rather than by
file shape:

* the file does not exist          -> no policy, silent (unchanged)
* the parser **raises**            -> warn and continue (unchanged, deliberate;
  `tests/test_scoped_advisor_audit.py` pins that case from the other side)
* it **parses** but is not a valid policy -> refuse to start

A list root, a scalar root and an empty YAML file are the third case, not the
second: `yaml.safe_load` returns `list`, `str` and `None` for them *without
raising*. They are the shapes an accidental policy file most often takes, and
they are exactly what an earlier revision of the fix would have let through.

**Every discovered file here is project-scoped**, so since CONSENT
(`IF-0-CONSENT-2`, Consiliency/pmcp#230) each one must be approved before it is read at all.
The approvals below are what keep this module testing what it was written to
test: without them every case would pass for the wrong reason -- the consent
gate refusing the file before the #202 logic ever ran. Consent decides whether a
project file is *read*; #202 decides what happens to one that is read and turns
out to be wrong, and this file still pins the second question.

The one case consent genuinely takes over is an unreadable file: the gate now
fronts the read, so a permissions failure is a consent refusal rather than a
parse warning. That case asserts the new contract, and the old one is preserved
one scope over, on a user-scoped policy, which is never gated.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from pmcp.policy.policy import PolicyManager

# A policy whose effect is observable: `deny-me` must actually be denied. Without
# asserting enforcement, a change that refuses *everything* would pass this file.
_VALID_POLICY = {
    "servers": {"denylist": ["deny-me"]},
    "limits": {"max_tools_per_server": 7},
}


def _discovery_paths(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> None:
    """Point auto-discovery at `paths` and nothing else.

    `USER_POLICY_PATHS` is left alone, so none of these paths is recognised as
    operator-supplied and all of them are project-scoped -- which is what this
    module has always been exercising, now named explicitly.
    """
    monkeypatch.setattr("pmcp.policy.policy.DEFAULT_POLICY_PATHS", list(paths))


def _user_discovery_path(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Discover `path` as a **user-scoped** policy: ungated, no trust record."""
    monkeypatch.setattr("pmcp.policy.policy.USER_POLICY_PATHS", [path])
    monkeypatch.setattr("pmcp.policy.policy.DEFAULT_POLICY_PATHS", [path])


def _is_readable(path: Path) -> bool:
    try:
        path.read_text()
    except OSError:
        return False
    return True


# === parses, but is not a valid policy -> fatal ===


def test_schema_invalid_discovered_policy_refuses_to_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Callable[[Path], None],
) -> None:
    """The bug itself: valid JSON, invalid policy, previously -> allow-all."""
    policy_file = tmp_path / ".mcp-gateway-policy.json"
    policy_file.write_text(json.dumps({"gateway_tools": {"unknown": []}}))
    approve_project_file(policy_file)
    _discovery_paths(monkeypatch, policy_file)

    with pytest.raises(ValueError, match="Invalid policy file") as excinfo:
        PolicyManager()

    # The error must name the offending path, and must not be mistaken for the
    # explicit-`--policy` failure, which is a different event with its own text.
    assert str(policy_file) in str(excinfo.value)
    assert "explicit policy" not in str(excinfo.value)


@pytest.mark.parametrize("limit", [0, -1])
def test_discovered_policy_with_an_unusable_tool_limit_refuses_to_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
    approve_project_file: Callable[[Path], None],
) -> None:
    """The #207 x #202 interaction, end to end.

    #207 bounded `LimitsPolicy.max_tools_per_server` at `ge=1`. On its own that
    is a schema fact; what makes it a *behaviour* change is #202, which turned a
    discovered policy that parses-but-fails-validation into a startup refusal.
    Neither issue's own tests cover the pair -- #207's are schema-level and
    #202's use a different invalid key -- so a regression in either half (the
    bound reverted, or discovered-policy validation made non-fatal again) would
    show up here and nowhere else.

    `PolicyManager` wraps the pydantic error in `ValueError`, so this is the
    `Invalid policy file` path, not a raw `ValidationError`.
    """
    policy_file = tmp_path / ".mcp-gateway-policy.json"
    policy_file.write_text(json.dumps({"limits": {"max_tools_per_server": limit}}))
    approve_project_file(policy_file)
    _discovery_paths(monkeypatch, policy_file)

    with pytest.raises(ValueError, match="Invalid policy file") as excinfo:
        PolicyManager()

    assert str(policy_file) in str(excinfo.value)
    assert "max_tools_per_server" in str(excinfo.value)
    assert "explicit policy" not in str(excinfo.value)


def test_discovered_policy_with_empty_limits_still_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Callable[[Path], None],
) -> None:
    """The bound must reject only the stated values, not an absent field.

    `limits: {}` is the shape most likely to be caught by an over-broad bound:
    it names the section without setting anything, and must keep the default.
    """
    policy_file = tmp_path / ".mcp-gateway-policy.yaml"
    policy_file.write_text("servers:\n  denylist:\n    - deny-me\nlimits: {}\n")
    approve_project_file(policy_file)
    _discovery_paths(monkeypatch, policy_file)

    manager = PolicyManager()

    assert manager.get_max_tools_per_server() == 100
    assert manager.is_server_allowed("deny-me") is False


def test_explicit_policy_with_an_unusable_tool_limit_is_fatal_too(
    tmp_path: Path,
) -> None:
    """Same value, different path, different message.

    An explicit `--policy` was already fatal for every failure mode, but it
    reports as `explicit policy` rather than `Invalid policy file`, so an
    operator grepping for one string would not find the other.
    """
    policy_file = tmp_path / "explicit.json"
    policy_file.write_text(json.dumps({"limits": {"max_tools_per_server": 0}}))

    with pytest.raises(ValueError, match="explicit policy") as excinfo:
        PolicyManager(policy_file)

    assert "max_tools_per_server" in str(excinfo.value)


@pytest.mark.parametrize(
    ("label", "content"),
    [
        # `yaml.safe_load` returns list / str / None for these -- no exception.
        ("list root", "- gateway.health\n- gateway.invoke\n"),
        ("scalar root", "gateway.health\n"),
        ("empty file", ""),
    ],
)
def test_non_mapping_discovered_policy_refuses_to_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    content: str,
    approve_project_file: Callable[[Path], None],
) -> None:
    policy_file = tmp_path / ".mcp-gateway-policy.yaml"
    policy_file.write_text(content)
    approve_project_file(policy_file)
    _discovery_paths(monkeypatch, policy_file)

    with pytest.raises(ValueError, match="policy root must be an object") as excinfo:
        PolicyManager()
    assert str(policy_file) in str(excinfo.value), label


def test_json_non_mapping_root_refuses_to_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Callable[[Path], None],
) -> None:
    """`json.loads("[]")` parses; only the schema rejects it."""
    policy_file = tmp_path / ".mcp-gateway-policy.json"
    policy_file.write_text("[]")
    approve_project_file(policy_file)
    _discovery_paths(monkeypatch, policy_file)

    with pytest.raises(ValueError, match="policy root must be an object"):
        PolicyManager()


def test_higher_priority_invalid_policy_does_not_fall_through(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Callable[[Path], None],
) -> None:
    """Discovery `break`s at the first *existing* path -- and stays that way.

    An invalid file at the higher-priority path must fail closed rather than
    silently hand control to a lower-priority policy.
    """
    first = tmp_path / ".mcp-gateway-policy.yaml"
    first.write_text("servers:\n  denylist: not-a-list\n")
    second = tmp_path / ".mcp-gateway-policy.json"
    second.write_text(json.dumps(_VALID_POLICY))
    approve_project_file(first)
    approve_project_file(second)
    _discovery_paths(monkeypatch, first, second)

    with pytest.raises(ValueError, match="Invalid policy file") as excinfo:
        PolicyManager()
    assert str(first) in str(excinfo.value)


# === the parser raises -> warn and continue (unchanged) ===


def test_unparseable_discovered_policy_warns_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    approve_project_file: Callable[[Path], None],
) -> None:
    policy_file = tmp_path / ".mcp-gateway-policy.json"
    policy_file.write_text("{")
    approve_project_file(policy_file)
    _discovery_paths(monkeypatch, policy_file)

    with caplog.at_level(logging.WARNING, logger="pmcp.policy.policy"):
        manager = PolicyManager()

    assert manager.is_gateway_tool_allowed("gateway.provision") is True
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(str(policy_file) in message for message in warnings)
    # Asserted on the emitted text, not on the level: a reader of the old line
    # could not tell that the gateway had just become unrestricted.
    assert any("No policy is in effect" in message for message in warnings)


def test_empty_json_is_a_parse_failure_unlike_empty_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    approve_project_file: Callable[[Path], None],
) -> None:
    """The split is drawn by the parser, so it lands differently per format.

    `yaml.safe_load("")` returns `None` -- it parses, so it is fatal (covered by
    `test_non_mapping_discovered_policy_refuses_to_start`). `json.loads("")`
    *raises*, so an empty `.json` takes the warn path instead. README documents
    this asymmetry; without this test that documented claim is unpinned, and a
    reader who tests the fix with an empty `.json` and sees only a warning would
    conclude it does not work.
    """
    policy_file = tmp_path / ".mcp-gateway-policy.json"
    policy_file.write_text("")
    approve_project_file(policy_file)
    _discovery_paths(monkeypatch, policy_file)

    with caplog.at_level(logging.WARNING, logger="pmcp.policy.policy"):
        manager = PolicyManager()

    assert manager.is_server_allowed("deny-me") is True
    assert any(
        "No policy is in effect" in r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )


def test_unreadable_project_policy_is_a_consent_refusal_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A permissions failure now reports as a consent refusal, not a parse warning.

    This is the one case in this module CONSENT genuinely takes over, and the
    change is not avoidable by recording an approval: approving reads the file,
    so a file that cannot be read cannot be approved either. The gate reads
    before the parser ever runs, so `unreadable` is decided there.

    What must not change is the consequence -- an unreadable project policy is
    still not fatal, and the gateway still comes up. That half is asserted
    exactly as before; only the wording of the warning moved, and it moved
    towards naming a fix the operator can run.
    """
    locked = tmp_path / ".mcp-gateway-policy.yaml"
    locked.write_text(json.dumps(_VALID_POLICY))
    locked.chmod(0o000)
    if _is_readable(locked):
        locked.chmod(0o644)
        pytest.skip("running as a user that ignores file permissions (e.g. root)")
    _discovery_paths(monkeypatch, locked)

    try:
        with caplog.at_level(logging.WARNING, logger="pmcp.policy.policy"):
            manager = PolicyManager()
    finally:
        locked.chmod(0o644)

    assert manager.is_server_allowed("deny-me") is True
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("could not be read" in message for message in warnings), warnings
    assert any(
        f"pmcp trust approve {locked.resolve()}" in message for message in warnings
    ), warnings


def test_unreadable_user_policy_still_warns_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The coverage the test above used to carry, kept where it still applies.

    A user-scoped policy is operator-supplied and never gated, so its read is
    still the loader's own and a permissions failure still takes the
    `Could not parse policy file ...` warn path. Without this, moving the
    project case to the consent wording would have deleted the only test that
    an unreadable *ungated* policy warns rather than raising.
    """
    locked = tmp_path / "gateway-policy.yaml"
    locked.write_text(json.dumps(_VALID_POLICY))
    locked.chmod(0o000)
    if _is_readable(locked):
        locked.chmod(0o644)
        pytest.skip("running as a user that ignores file permissions (e.g. root)")
    _user_discovery_path(monkeypatch, locked)

    try:
        with caplog.at_level(logging.WARNING, logger="pmcp.policy.policy"):
            manager = PolicyManager()
    finally:
        locked.chmod(0o644)

    assert manager.is_server_allowed("deny-me") is True
    assert any(
        "No policy is in effect" in r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )


# === a valid policy still loads AND enforces ===


def test_valid_discovered_policy_loads_and_enforces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Callable[[Path], None],
) -> None:
    """The criterion that guards the opposite failure: refusing too much."""
    policy_file = tmp_path / ".mcp-gateway-policy.yaml"
    policy_file.write_text(json.dumps(_VALID_POLICY))  # JSON is valid YAML
    approve_project_file(policy_file)
    _discovery_paths(monkeypatch, policy_file)

    manager = PolicyManager()

    assert manager.is_server_allowed("deny-me") is False
    assert manager.is_server_allowed("allow-me") is True
    assert manager.get_max_tools_per_server() == 7


def test_no_policy_file_anywhere_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _discovery_paths(
        monkeypatch,
        tmp_path / ".mcp-gateway-policy.yaml",
        tmp_path / ".mcp-gateway-policy.json",
    )

    with caplog.at_level(logging.WARNING, logger="pmcp.policy.policy"):
        manager = PolicyManager()

    assert manager.is_gateway_tool_allowed("gateway.provision") is True
    assert [
        r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
    ] == []


# === Path.cwd() is read at construction, not at import ===


def test_default_paths_follow_the_cwd_at_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Callable[[Path], None],
) -> None:
    """`DEFAULT_POLICY_PATHS` must not freeze `Path.cwd()` at import time.

    Deliberately does **not** monkeypatch `DEFAULT_POLICY_PATHS`: patching in a
    relative path would make this pass against `main` too, since `main`'s loop
    would resolve that relative path against the post-`chdir` cwd. The module's
    real state is the subject here. `pmcp.policy.policy` is imported at collection
    time, long before this `chdir`.
    """
    project = tmp_path / "project"
    project.mkdir()
    policy_file = project / ".mcp-gateway-policy.yaml"
    policy_file.write_text(json.dumps(_VALID_POLICY))
    approve_project_file(policy_file)
    monkeypatch.chdir(project)

    manager = PolicyManager()

    assert manager.is_server_allowed("deny-me") is False
    assert manager.get_max_tools_per_server() == 7


def test_default_policy_paths_stays_a_patchable_module_attribute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Callable[[Path], None],
) -> None:
    """The seam `tests/test_scoped_advisor_audit.py` relies on.

    Absolute entries must pass through path resolution unchanged, so a patched
    list is honoured verbatim regardless of the working directory.
    """
    policy_file = tmp_path / "elsewhere.yaml"
    policy_file.write_text(json.dumps(_VALID_POLICY))
    approve_project_file(policy_file)
    _discovery_paths(monkeypatch, policy_file)
    monkeypatch.chdir(tmp_path)

    assert PolicyManager().is_server_allowed("deny-me") is False


# === explicit --policy is unchanged: all three modes remain fatal ===


def test_explicit_policy_remains_fatal_for_every_mode(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="explicit policy"):
        PolicyManager(missing)

    unparseable = tmp_path / "unparseable.json"
    unparseable.write_text("{")
    with pytest.raises(ValueError, match="explicit policy"):
        PolicyManager(unparseable)

    schema_invalid = tmp_path / "schema-invalid.json"
    schema_invalid.write_text(json.dumps({"gateway_tools": {"unknown": []}}))
    with pytest.raises(ValueError, match="explicit policy"):
        PolicyManager(schema_invalid)

    non_mapping = tmp_path / "non-mapping.yaml"
    non_mapping.write_text("- gateway.health\n")
    with pytest.raises(ValueError, match="explicit policy"):
        PolicyManager(non_mapping)
