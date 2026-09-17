"""EC-SEAL-3: every refusal carries a remedy, and the remedy is real (SL-4).

The roadmap's literal wording -- "every refusal path prints the exact ``pmcp``
command that would grant the action" -- is **not satisfiable**, and SEAL's plan
records why: five families of refusal name no ``pmcp`` verb because none exists
(edit your policy file; pin the version in ``.mcp.json``; export a variable).
The criterion was amended under its own id, and what this file proves is
strictly stronger than the literal reading, which one unverifiable string would
have satisfied.

Four layers, and each is falsifiable on its own:

* **Exhaustiveness.** The audit's case table is checked against each gate's
  **closed reason vocabulary read out of the source** -- ``ConsentReason``'s
  ``Literal`` members, ``PROVISION_REASONS``, ``FeedbackEgressReason``'s
  ``Literal`` members. Every member must be covered by a case and no case may
  name a member that does not exist. A reason a later phase adds therefore fails
  this audit *on the day it is added*, which is the only version of "every
  refusal path" that survives a future phase. This is why the vocabularies are
  never restated here as literals.
* **Content.** Every refusal's remedy is non-empty; every command any remedy
  contains survives ``shlex.split`` and round-trips the value it names; and
  every ``pmcp`` command resolves against the **real** argparse tree *and* the
  dispatch chain read out of ``cli.async_main`` with ``ast``. A remedy naming a
  verb that does not exist is the defect EGRESS avoided by declining to write
  ``pmcp secrets rm``; nothing here compares against a list of verbs a test
  author typed.
* **Quoting.** Every operator-facing line that interpolates a
  repository-controlled value escapes non-printables and then ``shlex.quote``s,
  unconditionally. A project path containing a space must still yield a runnable
  ``pmcp trust approve '/path/with a space/.mcp.json'``; a path carrying shell
  metacharacters must render inert. Both are proven by *executing the printed
  command through the real CLI* and asserting the file it approves is the file
  that was refused -- the class CONSENT shipped and fixed.
* **Delivery.** A gate can return a perfect remedy that no handler prints. Each
  gate is driven through its real surface and the remedy asserted present in
  what an operator or agent actually sees.

**Honesty.** ``test_the_remedies_that_name_no_pmcp_verb_are_exactly_the_enumerated_set``
pins the refusals for which no ``pmcp`` verb exists. The set can shrink when a
later phase adds a verb; it cannot grow silently.

**What this audit does NOT prove**, stated so nobody reads more into it: a
remedy that is well-formed, runnable, and names a real verb but gives *wrong
advice* (say, ``_unpinned_remedy`` returning ``pmcp trust approve-package X``)
passes every test here. Correct-advice-ness is not mechanically checkable; what
is checkable is that no refusal is silent, no printed command is a fiction, and
nothing an operator pastes can execute something the repository chose. The
round-trip assertions are what stop the remaining easy fake: a remedy that names
*a* valid command but not the value that was refused fails
``test_every_remedy_is_runnable_as_printed``.

**Nothing here reaches the network or spawns a process.** ``_no_egress`` is
modelled on ``tests/conftest.py``'s ``_no_live_npm_registry``: it records and
refuses every socket connection and every process spawn, and asserts per test
that none was attempted.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import os
import platform
import shlex
import socket
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast, get_args
from unittest.mock import patch

import pytest

from pmcp import cli as cli_module
from pmcp import feedback_egress, trust_store
from pmcp.config.guidance import GuidanceConfig
from pmcp.config.loader import load_configs
from pmcp.env_store import (
    record_dotenv_keys,
    record_pmcp_introduced_keys,
    reset_dotenv_keys,
    reset_pmcp_introduced_keys,
)
from pmcp.feedback_egress import (
    FeedbackEgressDecision,
    FeedbackEgressReason,
    evaluate_feedback_egress,
)
from pmcp.manifest import package_identity
from pmcp.manifest.loader import Manifest, ServerConfig
from pmcp.manifest.package_identity import PackageIdentity
from pmcp.package_approvals import approve_package, package_approvals_path
from pmcp.policy.policy import PolicyManager
from pmcp.project_consent import ConsentReason, read_and_gate
from pmcp.provision_gate import (
    PROVISION_REASONS,
    ProvisionDecision,
    evaluate_provision,
)
from pmcp.tools import handlers as handlers_module
from pmcp.tools.handlers import GatewayTools

PLATFORMS = ("mac", "linux", "wsl", "windows")

# `submit_feedback`'s telemetry block calls `platform.platform()`, and CPython's
# `platform.processor()` shells out to `uname -p` the first time it is asked.
# That is a stdlib probe, not this gateway reaching anywhere, but the guard below
# refuses every spawn without exception -- so prime the cache at import, BEFORE
# the guard is installed, rather than carving a hole in the guard. A hole is what
# a later spawn would slip through.
platform.platform()

#: Cumulative record of every egress attempt this module made, across all tests.
#: ``_no_egress`` fails the offending test where it happens; this list is what
#: ``test_no_remedy_audit_case_reached_the_network`` reads.
_EGRESS_ATTEMPTS: list[str] = []


# --------------------------------------------------------------------------- #
# The guard.
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_egress(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Refuse and record every socket connection and every process spawn.

    Modelled on ``tests/conftest.py``'s ``_no_live_npm_registry`` (``:168``),
    widened from the npm opener to *any* socket and *any* spawn: this file
    drives four gates and two gateway handlers, and a refusal that quietly made
    a network call would be an audit that tested the day's weather.

    The per-test assertion at teardown is what makes each audit case covered
    individually; the module-level list is what
    ``test_no_remedy_audit_case_reached_the_network`` asserts over.
    """
    attempts: list[str] = []

    def _refuse(kind: str) -> Any:
        def _blocked(*args: Any, **kwargs: Any) -> Any:
            detail = f"{kind}{args[1:] if args else ()}"
            attempts.append(detail)
            _EGRESS_ATTEMPTS.append(detail)
            raise OSError(f"{kind} is disabled in the remedy audit")

        return _blocked

    monkeypatch.setattr(socket.socket, "connect", _refuse("socket.connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse("socket.connect_ex"))
    monkeypatch.setattr(socket, "create_connection", _refuse("create_connection"))
    monkeypatch.setattr(subprocess.Popen, "__init__", _refuse("subprocess.Popen"))
    monkeypatch.setattr(subprocess, "run", _refuse("subprocess.run"))
    monkeypatch.setattr(os, "system", _refuse("os.system"))
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _refuse("create_subprocess_exec")
    )
    monkeypatch.setattr(
        asyncio, "create_subprocess_shell", _refuse("create_subprocess_shell")
    )

    yield attempts

    assert attempts == [], (
        f"a remedy-audit case attempted egress: {attempts}. Every gate in this "
        "file must be driven offline."
    )


@pytest.fixture(autouse=True)
def _clean_provenance() -> Iterator[None]:
    """Both provenance registries are process-global; reset them around each test."""
    reset_dotenv_keys()
    reset_pmcp_introduced_keys()
    yield
    reset_dotenv_keys()
    reset_pmcp_introduced_keys()


@pytest.fixture(autouse=True)
def _clean_feedback_env() -> Iterator[None]:
    """Restore the variables the egress gate reads, whoever wrote them."""
    names = ("PMCP_FEEDBACK_TOKEN", "PMCP_FEEDBACK_REPO")
    before = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ.pop(name, None)
    yield
    for name, value in before.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


# --------------------------------------------------------------------------- #
# The case table.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Command:
    """A command a remedy prints, and the value pasting it must act on.

    ``text`` must appear verbatim in the remedy: a declaration that drifted from
    what is printed would let the round-trip assertions grade a string nobody
    sees. ``value`` is what ``shlex.split(text)`` must contain -- the file that
    was refused, the identity that needs approving -- so a remedy naming a valid
    command for the *wrong* subject still fails.
    """

    text: str
    value: str


@dataclass(frozen=True)
class Case:
    """One decision the audit grades.

    ``case_id`` is finer than ``reason``: ``denied`` alone covers three distinct
    refusals with three distinct remedies, and an audit keyed only on the reason
    would grade one of them and call the other two covered.
    """

    case_id: str
    gate: Literal["consent", "provision", "feedback"]
    reason: str
    refused: bool
    remedy: str
    commands: tuple[Command, ...] = ()


def _consent_cases(tmp_path: Path) -> list[Case]:
    """Every ``ConsentReason``, produced by driving the real gate."""
    cases: list[Case] = []

    unapproved = tmp_path / "unapproved" / ".mcp.json"
    unapproved.parent.mkdir(parents=True)
    unapproved.write_text('{"mcpServers": {}}', encoding="utf-8")
    _, decision = read_and_gate(unapproved, "project_mcp_json")
    assert decision.reason == "no_record"
    cases.append(
        Case(
            "consent:no_record",
            "consent",
            decision.reason,
            True,
            decision.remediation,
            (Command(decision.remediation, str(decision.path)),),
        )
    )

    edited = tmp_path / "edited" / ".mcp.json"
    edited.parent.mkdir(parents=True)
    edited.write_text('{"mcpServers": {}}', encoding="utf-8")
    trust_store.record(edited, edited.read_bytes(), "project", trust_store.APPROVED)
    edited.write_text('{"mcpServers": {"added": {}}}', encoding="utf-8")
    _, decision = read_and_gate(edited, "project_mcp_json")
    assert decision.reason == "content_changed"
    cases.append(
        Case(
            "consent:content_changed",
            "consent",
            decision.reason,
            True,
            decision.remediation,
            (Command(decision.remediation, str(decision.path)),),
        )
    )

    missing = tmp_path / "missing" / ".mcp.json"
    _, decision = read_and_gate(missing, "project_mcp_json")
    assert decision.reason == "unreadable"
    cases.append(
        Case(
            "consent:unreadable",
            "consent",
            decision.reason,
            True,
            decision.remediation,
            (Command(decision.remediation, str(decision.path)),),
        )
    )

    approved = tmp_path / "approved" / ".mcp.json"
    approved.parent.mkdir(parents=True)
    approved.write_text('{"mcpServers": {}}', encoding="utf-8")
    trust_store.record(approved, approved.read_bytes(), "project", trust_store.APPROVED)
    _, decision = read_and_gate(approved, "project_mcp_json")
    assert decision.allowed and decision.reason == "approved"
    cases.append(
        Case(
            "consent:approved", "consent", decision.reason, False, decision.remediation
        )
    )

    return cases


def _policy(tmp_path: Path, name: str, body: str) -> PolicyManager:
    directory = tmp_path / "policies" / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "gateway-policy.yaml"
    path.write_text(body, encoding="utf-8")
    return PolicyManager(policy_path=path)


def _server(
    name: str,
    args: list[str],
    *,
    command: str = "npx",
    install: list[str] | None = None,
    package: str | None = None,
) -> ServerConfig:
    return ServerConfig(
        name=name,
        description="",
        keywords=[],
        install={
            p: list(install if install is not None else [command, *args])
            for p in PLATFORMS
        },
        command=command,
        args=list(args),
        package=package,
    )


class _RaisingPolicy:
    """A policy object whose every predicate raises, to reach rule 8."""

    def evaluate_package_policy(self, identity: PackageIdentity | None) -> str:
        raise RuntimeError("policy store is unavailable")


def _provision_case(
    case_id: str,
    decision: ProvisionDecision,
    commands: tuple[Command, ...] = (),
) -> Case:
    return Case(
        case_id,
        "provision",
        decision.reason,
        not decision.allowed,
        decision.remedy or "",
        commands,
    )


def _provision_cases(tmp_path: Path) -> list[Case]:
    """Every member of ``PROVISION_REASONS``, produced by driving the real gate.

    ``denied`` gets three cases and ``not_approved`` two, because each reason
    covers refusals whose remedies are built by different helpers. Grading one
    per reason would leave the others unaudited under a green test.
    """
    cases: list[Case] = []
    empty = _policy(tmp_path, "empty", "servers: {}\n")
    denylist = _policy(
        tmp_path, "denylist", 'packages:\n  denylist:\n    - "evil-pkg"\n'
    )
    scoped_denylist = _policy(
        tmp_path, "scoped", 'packages:\n  denylist:\n    - "@shipped/*"\n'
    )
    allowlist = _policy(
        tmp_path, "allowlist", 'packages:\n  allowlist:\n    - "good-pkg"\n'
    )

    evil = PackageIdentity("npm", "evil-pkg", "6.6.6", None)
    good = PackageIdentity("npm", "good-pkg", "1.2.3", None)

    # (1a) Rule 1 on a resolved identity.
    decision = evaluate_provision(
        _server("evil", ["-y", "evil-pkg@6.6.6"]),
        evil,
        source="configured",
        policy=denylist,
    )
    assert (decision.allowed, decision.reason) == (False, "denied")
    cases.append(_provision_case("provision:denied:identity", decision))

    # (1b) Rule 1 on a manifest lookup, which carries no identity at all.
    decision = evaluate_provision(
        _server("shipped", ["-y", "@shipped/server"]),
        None,
        source="manifest",
        policy=scoped_denylist,
    )
    assert (decision.allowed, decision.reason) == (False, "denied")
    cases.append(_provision_case("provision:denied:manifest_denylist", decision))

    # (1c) Rule 1 on a manifest entry whose npx argv cannot be read, with a
    # denylist in force -- the argument pmcp cannot place is named instead.
    decision = evaluate_provision(
        _server("opaque", ["--registry", "https://example.invalid", "some-pkg"]),
        None,
        source="manifest",
        policy=scoped_denylist,
    )
    assert (decision.allowed, decision.reason) == (False, "denied")
    cases.append(_provision_case("provision:denied:manifest_undetermined", decision))

    # (2) The manifest exemption.
    decision = evaluate_provision(
        _server("shipped", ["-y", "@shipped/server"]),
        None,
        source="manifest",
        policy=empty,
    )
    assert (decision.allowed, decision.reason) == (True, "manifest_backed")
    cases.append(_provision_case("provision:manifest_backed", decision))

    # (3) Nothing resolvable, so nothing to approve.
    decision = evaluate_provision(
        _server("ghost", ["-y", "ghost-pkg"], package="ghost-pkg"),
        None,
        source="configured",
        policy=empty,
    )
    assert (decision.allowed, decision.reason) == (False, "unresolvable_identity")
    cases.append(_provision_case("provision:unresolvable_identity", decision))

    # (4) An identity whose argv re-resolves at spawn.
    decision = evaluate_provision(
        _server("loose", ["-y", "good-pkg"]),
        good,
        source="configured",
        policy=empty,
    )
    assert (decision.allowed, decision.reason) == (False, "unpinned_configured_argv")
    assert decision.remedy is not None
    cases.append(
        _provision_case(
            "provision:unpinned_configured_argv",
            decision,
            (Command("npx -y good-pkg@1.2.3", "good-pkg@1.2.3"),),
        )
    )

    # (5) An operator approval for this exact identity.
    approve_package(good)
    decision = evaluate_provision(
        _server("pinned", ["-y", "good-pkg@1.2.3"]),
        good,
        source="configured",
        policy=empty,
    )
    assert (decision.allowed, decision.reason) == (True, "package_approved")
    cases.append(_provision_case("provision:package_approved", decision))

    # (6) The policy names it. Recorded approvals are dropped first so this is
    # the rule under test and not rule 5 answering again.
    package_approvals_path().unlink(missing_ok=True)
    decision = evaluate_provision(
        _server("pinned", ["-y", "good-pkg@1.2.3"]),
        good,
        source="configured",
        policy=allowlist,
    )
    assert (decision.allowed, decision.reason) == (True, "policy_allowed")
    cases.append(_provision_case("provision:policy_allowed", decision))

    # (7) "unspecified" is not permission.
    decision = evaluate_provision(
        _server("pinned", ["-y", "good-pkg@1.2.3"]),
        good,
        source="configured",
        policy=empty,
    )
    assert (decision.allowed, decision.reason) == (False, "not_approved")
    assert decision.remedy is not None
    cases.append(
        _provision_case(
            "provision:not_approved",
            decision,
            (Command("pmcp trust approve-package good-pkg@1.2.3", "good-pkg@1.2.3"),),
        )
    )

    # (8) Rule 8: the gate itself failed, and there is no identity to name. The
    # reason is reused, the remedy is not -- which is why this is its own case.
    decision = evaluate_provision(
        _server("ghost", ["-y", "ghost-pkg"], package="ghost-pkg"),
        None,
        source="configured",
        policy=cast(Any, _RaisingPolicy()),
    )
    assert (decision.allowed, decision.reason) == (False, "not_approved")
    cases.append(_provision_case("provision:not_approved:gate_error", decision))

    return cases


def _feedback_case(case_id: str, decision: FeedbackEgressDecision) -> Case:
    return Case(
        case_id,
        "feedback",
        decision.reason,
        not decision.submit_allowed,
        decision.remedy or "",
        (
            (Command(decision.remedy, shlex.split(decision.remedy)[-1]),)
            if decision.remedy is not None and decision.remedy.startswith("pmcp ")
            else ()
        ),
    )


def _decide_feedback(
    *,
    project_root: Path,
    environ: dict[str, str],
    telemetry: bool = True,
    submission: bool = True,
    confirm: bool = True,
) -> FeedbackEgressDecision:
    return evaluate_feedback_egress(
        telemetry_enabled=telemetry,
        submission_enabled=submission,
        confirm_submission=confirm,
        environ=environ,
        project_root=project_root,
    )


def _feedback_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Case]:
    """Every member of ``FeedbackEgressReason``, produced by driving the gate."""
    root = tmp_path / "project"
    root.mkdir(parents=True, exist_ok=True)
    token = "operator-exported-value"
    cases: list[Case] = []

    record_dotenv_keys(["PMCP_FEEDBACK_REPO"])
    decision = _decide_feedback(
        project_root=root, environ={"PMCP_FEEDBACK_REPO": "someone/else"}
    )
    assert decision.reason == "untrusted_repository_override"
    cases.append(_feedback_case("feedback:untrusted_repository_override", decision))
    reset_dotenv_keys()

    decision = _decide_feedback(
        project_root=root, environ={"PMCP_FEEDBACK_REPO": "not a repo"}
    )
    assert decision.reason == "invalid_repository"
    cases.append(_feedback_case("feedback:invalid_repository", decision))

    decision = _decide_feedback(project_root=root, environ={}, telemetry=False)
    assert decision.reason == "telemetry_disabled"
    cases.append(_feedback_case("feedback:telemetry_disabled", decision))

    decision = _decide_feedback(project_root=root, environ={}, submission=False)
    assert decision.reason == "submission_not_enabled"
    cases.append(_feedback_case("feedback:submission_not_enabled", decision))

    decision = _decide_feedback(project_root=root, environ={}, confirm=False)
    assert decision.reason == "not_confirmed"
    cases.append(_feedback_case("feedback:not_confirmed", decision))

    record_pmcp_introduced_keys(["PMCP_FEEDBACK_TOKEN"])
    decision = _decide_feedback(
        project_root=root, environ={"PMCP_FEEDBACK_TOKEN": token}
    )
    assert decision.reason == "untrusted_token"
    cases.append(_feedback_case("feedback:untrusted_token", decision))
    reset_pmcp_introduced_keys()

    decision = _decide_feedback(project_root=root, environ={})
    assert decision.reason == "no_feedback_token"
    cases.append(_feedback_case("feedback:no_feedback_token", decision))

    with monkeypatch.context() as patched:

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("provenance registry is unavailable")

        patched.setattr(feedback_egress, "dotenv_sourced_keys", _boom)
        decision = _decide_feedback(
            project_root=root, environ={"PMCP_FEEDBACK_TOKEN": token}
        )
    assert decision.reason == "gate_error"
    cases.append(_feedback_case("feedback:gate_error", decision))

    decision = _decide_feedback(
        project_root=root, environ={"PMCP_FEEDBACK_TOKEN": token}
    )
    assert decision.submit_allowed is True and decision.reason == "submit_allowed"
    cases.append(_feedback_case("feedback:submit_allowed", decision))

    return cases


@pytest.fixture
def cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Case]:
    """The audit's case table, built by driving all three gates for real.

    Nothing here is a literal copy of a remedy: every string graded below came
    out of the shipped code during this fixture.
    """
    return (
        _consent_cases(tmp_path)
        + _provision_cases(tmp_path)
        + _feedback_cases(tmp_path, monkeypatch)
    )


def _refusals(cases: list[Case]) -> list[Case]:
    return [case for case in cases if case.refused]


# --------------------------------------------------------------------------- #
# Layer 1: exhaustiveness, driven off each gate's closed vocabulary.
# --------------------------------------------------------------------------- #


def _covered(cases: list[Case], gate: str) -> set[str]:
    return {case.reason for case in cases if case.gate == gate}


def test_every_consent_refusal_reason_is_driven_by_this_audit(
    cases: list[Case],
) -> None:
    """``ConsentReason``'s members, read from the source, all appear as cases."""
    vocabulary = set(get_args(ConsentReason))
    assert vocabulary, "ConsentReason is not a Literal any more; the audit is blind"
    assert _covered(cases, "consent") == vocabulary


def test_every_provision_refusal_reason_is_driven_by_this_audit(
    cases: list[Case],
) -> None:
    """``PROVISION_REASONS``, read from the source, all appear as cases."""
    assert PROVISION_REASONS, "PROVISION_REASONS is empty; the audit is blind"
    assert _covered(cases, "provision") == set(PROVISION_REASONS)


def test_every_feedback_refusal_reason_is_driven_by_this_audit(
    cases: list[Case],
) -> None:
    """``FeedbackEgressReason``'s members, read from the source, all appear."""
    vocabulary = set(get_args(FeedbackEgressReason))
    assert vocabulary, (
        "FeedbackEgressReason is not a Literal any more; the audit is blind"
    )
    assert _covered(cases, "feedback") == vocabulary


# --------------------------------------------------------------------------- #
# Layer 2: content.
# --------------------------------------------------------------------------- #


def test_every_refusal_carries_a_non_empty_remedy(cases: list[Case]) -> None:
    """A refusal with nothing to do about it is a dead end, not a gate."""
    silent = [case.case_id for case in _refusals(cases) if not case.remedy.strip()]
    assert silent == [], f"refusals with no remedy: {silent}"

    # And the converse the three gates all document: an allow carries none.
    noisy = [case.case_id for case in cases if not case.refused and case.remedy.strip()]
    assert noisy == [], f"allowed decisions carrying a remedy: {noisy}"


def _strip(token: str) -> str:
    return token.strip("()[]{},.;:\"'`")


#: Words that may follow a bare ``pmcp`` in a remedy's PROSE. Enumerated for the
#: same reason the reason vocabularies are: ``pmcp trst approve`` would otherwise
#: be silently classified as prose and never resolved. A typo'd verb is not in
#: this set, so it fails.
_PROSE_AFTER_PMCP = frozenset(
    {
        "",  # a bare trailing "pmcp", e.g. "the shell that starts pmcp"
        "cannot",
        "could",
        "itself",
        "instead",
        "or",
        "will",
        "has",
        "is",
        "does",
    }
)


def _pmcp_commands(remedy: str, verbs: frozenset[str]) -> list[list[str]]:
    """Every ``pmcp <verb> ...`` a remedy prints, as argv.

    A token-level scan, never a regex over the raw text: ``~/.config/pmcp/...``
    contains ``pmcp`` between two slashes and is a path, not a command. An
    occurrence whose following word is a real top-level verb is a command and is
    returned; any other occurrence must be declared prose.

    Every command the shipped remedies print today IS the whole remedy, so the
    sentence-boundary handling below is not exercised by the current tree. It is
    here so a remedy that later embeds a command mid-sentence is read correctly
    instead of silently swallowing the prose after it -- and if it ever read one
    wrongly, ``_parses`` in the caller would refuse the argv rather than pass.
    """
    found: list[list[str]] = []
    tokens = remedy.split()
    for index, token in enumerate(tokens):
        if _strip(token) != "pmcp":
            continue
        following = _strip(tokens[index + 1]) if index + 1 < len(tokens) else ""
        if following in verbs:
            argv = [following]
            for rest in tokens[index + 2 :]:
                stripped = rest.rstrip(",;:")
                if not stripped:
                    break
                if stripped.endswith("."):
                    # A sentence terminator: this is the last argument, and
                    # nothing after it belongs to the command.
                    trimmed = stripped.rstrip(".")
                    if trimmed:
                        argv.append(trimmed)
                    break
                argv.append(stripped)
            found.append(argv)
            continue
        assert following in _PROSE_AFTER_PMCP, (
            f"'pmcp {following}' in a remedy is neither a CLI verb nor declared "
            f"prose: {remedy!r}"
        )
    return found


def _dispatched_top_level_verbs() -> frozenset[str]:
    """The commands ``cli.async_main`` actually branches on, read with ``ast``.

    The argparse tree says a verb *parses*; this says it is *dispatched*. Both
    are read out of the shipped source, so neither can drift from a list a test
    author maintains.
    """
    tree = ast.parse(inspect.getsource(cli_module.async_main))
    verbs: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if (
            isinstance(left, ast.Attribute)
            and left.attr == "command"
            and isinstance(left.value, ast.Name)
            and left.value.id == "args"
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.Eq)
            and isinstance(node.comparators[0], ast.Constant)
            and isinstance(node.comparators[0].value, str)
        ):
            verbs.add(node.comparators[0].value)
    assert verbs, "no dispatch branches found in async_main; the audit is blind"
    return frozenset(verbs)


def _dispatched_trust_verbs() -> frozenset[str]:
    """The keys of ``cli.run_trust``'s handler table, read with ``ast``."""
    tree = ast.parse(inspect.getsource(cli_module.run_trust))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and node.keys:
            keys = [
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            ]
            if len(keys) == len(node.keys):
                return frozenset(keys)
    raise AssertionError("no handler table found in run_trust; the audit is blind")


def _parses(argv: list[str]) -> Any:
    """Parse ``pmcp <argv>`` through the real entry path, or fail the test."""
    try:
        with patch.object(sys, "argv", ["pmcp", *argv]):
            return cli_module.parse_args()
    except SystemExit as exit_code:  # argparse refused the argv
        raise AssertionError(
            f"the CLI does not accept 'pmcp {' '.join(argv)}' (exit {exit_code.code})"
        ) from exit_code


def test_every_pmcp_command_in_a_remedy_names_a_verb_the_cli_dispatches(
    cases: list[Case],
) -> None:
    """A remedy naming a command that does not exist is worse than none.

    EGRESS nearly shipped ``pmcp secrets rm``; there is no such verb. This test
    resolves every ``pmcp ...`` a remedy prints against the real argparse tree
    AND the dispatch chain, both read out of the shipped source.
    """
    verbs = _dispatched_top_level_verbs()
    trust_verbs = _dispatched_trust_verbs()

    seen: list[list[str]] = []
    for case in cases:
        for argv in _pmcp_commands(case.remedy, verbs):
            seen.append(argv)
            assert argv[0] in verbs, (
                f"{case.case_id} names 'pmcp {argv[0]}', which async_main never "
                f"dispatches"
            )
            namespace = _parses(argv)
            assert namespace.command == argv[0]
            if argv[0] == "trust":
                assert namespace.trust_command in trust_verbs, (
                    f"{case.case_id} names 'pmcp trust {namespace.trust_command}', "
                    "which run_trust never dispatches"
                )

    assert seen, "no remedy named a pmcp command at all; the audit found nothing"


def test_every_remedy_is_runnable_as_printed(cases: list[Case]) -> None:
    """Each declared command survives ``shlex.split`` and names the right value.

    This is the general form of the class CONSENT shipped and fixed: a remedy
    that is *almost* a command -- a path with a space in it, unquoted -- is not
    runnable, and one that is runnable but names a different subject approves
    the wrong thing.
    """
    for case in cases:
        for command in case.commands:
            assert command.text in case.remedy, (
                f"{case.case_id} declares a command it does not print: "
                f"{command.text!r} not in {case.remedy!r}"
            )
            argv = shlex.split(command.text)
            assert argv, f"{case.case_id}: {command.text!r} splits to nothing"
            assert command.value in argv, (
                f"{case.case_id}: running {command.text!r} does not act on "
                f"{command.value!r}; argv is {argv}"
            )

    graded = [case.case_id for case in _refusals(cases) if case.commands]
    assert graded, "no refusal declared a command; nothing was graded"


# --------------------------------------------------------------------------- #
# Layer 3: quoting. Proven by executing the printed command.
# --------------------------------------------------------------------------- #


def _run_cli(*argv: str) -> None:
    """Run one ``pmcp`` invocation exactly as ``main()`` would dispatch it."""
    with patch.object(sys, "argv", ["pmcp", *argv]):
        args = cli_module.parse_args()
    asyncio.run(cli_module.async_main(args))


def test_a_path_containing_a_space_yields_a_runnable_approve_command(
    tmp_path: Path,
) -> None:
    """The exact shape CONSENT amendment 10a named, proven end to end."""
    project = tmp_path / "with a space"
    project.mkdir()
    config = project / ".mcp.json"
    config.write_text('{"mcpServers": {}}', encoding="utf-8")

    _, decision = read_and_gate(config, "project_mcp_json")
    assert decision.allowed is False
    remedy = decision.remediation

    assert remedy == f"pmcp trust approve {shlex.quote(str(config.resolve()))}"
    argv = shlex.split(remedy)
    assert argv[:3] == ["pmcp", "trust", "approve"]
    assert argv[3] == str(config.resolve())
    assert len(argv) == 4, f"the path was word-split: {argv}"

    # Runnable as printed: paste it, and the file that was refused is approved.
    _run_cli(*argv[1:])
    assert trust_store.is_approved(config.resolve(), config.read_bytes())


def test_a_path_carrying_shell_metacharacters_is_rendered_inert(
    tmp_path: Path,
) -> None:
    """A repository chooses the name; the operator pastes the line. Both hold.

    Two shapes. A printable but hostile name must be quoted so a shell expands
    nothing inside it. A name carrying a control character must not merely be
    escaped -- an escaped ``\\r`` renders identically to a *different*, printable
    file, so the unresolved source is named instead and pasting still approves
    the file that was refused.
    """
    hostile = tmp_path / "payload$(id)`whoami`;rm -rf x"
    hostile.mkdir()
    config = hostile / ".mcp.json"
    config.write_text('{"mcpServers": {}}', encoding="utf-8")

    _, decision = read_and_gate(config, "project_mcp_json")
    remedy = decision.remediation
    argv = shlex.split(remedy)

    assert len(argv) == 4, f"metacharacters word-split the command: {argv}"
    assert argv[3] == str(config.resolve())
    # Single-quoted, inside which $(...), backticks and ; are literal.
    assert f"'{config.resolve()}'" in remedy
    _run_cli(*argv[1:])
    assert trust_store.is_approved(config.resolve(), config.read_bytes())

    # A control character in the RESOLVED target -- the shape a repository can
    # actually choose, because it owns the symlink target and not the checkout
    # directory. Escaping alone is not enough here: an escaped CR renders exactly
    # like a different, printable file, so pasting the escaped name would approve
    # that other file. The gate names the unresolved, printable source instead.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    target = hidden / "payload\rinjected.json"
    target.write_text('{"mcpServers": {}}', encoding="utf-8")
    source = checkout / ".mcp.json"
    source.symlink_to(target)

    _, control = read_and_gate(source, "project_mcp_json")
    assert control.allowed is False
    assert control.path == target.resolve(), "the gate judged the wrong file"
    assert control.remediation.isprintable(), control.remediation
    named = shlex.split(control.remediation)
    assert len(named) == 4, named
    assert Path(named[3]).resolve() == target.resolve(), (
        "the printed command names a different file from the one refused"
    )
    _run_cli(*named[1:])
    assert trust_store.is_approved(target.resolve(), target.read_bytes())

    # The residual case the gate documents rather than solves: when the source
    # is non-printable too, the escaped target remains. It stays SAFE -- nothing
    # a terminal will act on, nothing a shell will expand -- but it is not
    # runnable against the same file, and by then the odd directory is the
    # operator's own rather than the repository's. Pinned so the limitation is a
    # measured property and not an oversight.
    both = tmp_path / "own\rdirectory"
    both.mkdir()
    awkward = both / ".mcp.json"
    awkward.write_text('{"mcpServers": {}}', encoding="utf-8")
    _, residual = read_and_gate(awkward, "project_mcp_json")
    assert residual.allowed is False
    assert residual.remediation.isprintable(), residual.remediation
    residual_argv = shlex.split(residual.remediation)
    assert len(residual_argv) == 4, residual_argv
    assert residual_argv[3] == str(awkward.resolve()).replace("\r", "\\r")
    assert Path(residual_argv[3]) != awkward.resolve()


# --------------------------------------------------------------------------- #
# Layer 4: delivery. A remedy no handler prints is not a remedy.
# --------------------------------------------------------------------------- #


def test_a_consent_refusal_reaches_the_operator_as_one_warning_naming_its_remedy(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One WARNING, exactly one, naming the remediation -- through a real reader.

    ``load_configs`` reads the project file at five independent sites and every
    one of them goes through the same gate, so "exactly one" is the assertion
    that matters: five warnings for one refused file is what trains an operator
    to stop reading them.

    The records are selected by the sentence ``log_refusal`` emits, not by
    logger name: it logs through the *caller's* logger (here
    ``pmcp.config.loader``), so filtering on ``pmcp.project_consent`` would match
    nothing and the test would pass by finding no warnings at all.
    """
    config = tmp_path / ".mcp.json"
    config.write_text(
        '{"mcpServers": {"repo-server": {"command": "node", "args": ["evil.js"]}}}',
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        configs = load_configs(project_root=tmp_path, user_config_paths=[])

    assert [loaded.name for loaded in configs] == []
    records = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and "To use it, run:" in record.getMessage()
    ]
    assert len(records) == 1, [record.getMessage() for record in records]
    message = records[0].getMessage()
    assert f"pmcp trust approve {shlex.quote(str(config.resolve()))}" in message
    assert message.isprintable()


def _packument(name: str, version: str) -> dict[str, Any]:
    return {
        "name": name,
        "dist-tags": {"latest": version},
        "versions": {version: {"dist": {"integrity": f"sha512-{name}-{version}"}}},
    }


class _MinimalClientManager:
    """The little of a client manager these handlers can reach."""

    def __init__(self) -> None:
        self.connected: list[Any] = []

    def get_all_tools(self) -> list[Any]:
        return []

    def get_tool(self, tool_id: str) -> None:
        return None

    def is_server_online(self, name: str) -> bool:
        return False

    def is_lazy_server(self, name: str) -> bool:
        return False

    def get_server_status(self, name: str) -> None:
        return None

    def get_all_server_statuses(self) -> list[Any]:
        return []

    def get_registry_meta(self) -> tuple[str, float]:
        return ("test-rev", 0.0)

    async def connect_server(self, config: Any) -> list[str]:
        self.connected.append(config)
        return []


class _RecordingJobManager:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], ServerConfig]] = []
        # The root production threaded through (SEAL SL-0). Recorded separately so
        # the existing two-tuple `calls` assertions keep their exact shape.
        self.project_roots: list[Path | None] = []

    async def start_install(
        self,
        server_config: ServerConfig,
        platform: str,
        project_root: Path | None = None,
    ) -> str:
        self.calls.append(
            (list(server_config.install.get(platform) or []), server_config)
        )
        self.project_roots.append(project_root)
        return f"job-{len(self.calls)}"


@pytest.mark.asyncio
async def test_a_provision_refusal_reaches_the_agent_facing_message_with_its_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lifecycle refusal an agent sees carries the gate's remedy verbatim."""
    monkeypatch.setattr(
        package_identity,
        "_fetch_packument",
        lambda name: _packument(name, "6.6.6") if name == "evil-pkg" else None,
    )
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers={},
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )
    monkeypatch.setattr(handlers_module, "load_manifest", lambda: manifest)
    monkeypatch.setattr(handlers_module, "load_configs", lambda **_: [])
    monkeypatch.setattr(handlers_module, "get_job_manager", _RecordingJobManager)

    policy = _policy(tmp_path, "allow-name", "servers:\n  allowlist:\n    - useful\n")
    gateway = GatewayTools(
        client_manager=cast(Any, _MinimalClientManager()),
        policy_manager=policy,
    )
    cast(Any, gateway)._platform = "linux"

    registered = await gateway.register_discovered_server(
        {"server_name": "useful", "package": "evil-pkg", "env_vars": []}
    )
    assert registered.registered is True

    config = gateway._discovered_server_configs["useful"]
    identity = gateway._discovered_server_identities["useful"]
    decision = evaluate_provision(config, identity, source="discovered", policy=policy)
    assert decision.allowed is False
    assert decision.remedy

    refused = await gateway.provision({"server_name": "useful"})
    assert refused.ok is False
    assert decision.remedy in refused.message, refused.message

    connect_refused = await gateway.connect_server({"server_name": "useful"})
    assert connect_refused.ok is False
    assert decision.remedy in connect_refused.message, connect_refused.message


class _StubClientManager(_MinimalClientManager):
    pass


@pytest.mark.asyncio
async def test_a_feedback_refusal_reaches_the_tool_output_with_its_remedy(
    tmp_path: Path,
) -> None:
    """``SubmitFeedbackOutput.message`` carries the egress gate's remedy.

    Both shapes are driven: the refusals that build no payload at all, and the
    ones that build a preview. A remedy that only reached the first would leave
    an operator staring at a preview with nothing to do about it.
    """
    root = tmp_path / "project"
    root.mkdir()

    no_submission = GatewayTools(
        client_manager=cast(Any, _StubClientManager()),
        policy_manager=PolicyManager(policy_path=None),
        project_root=root,
        guidance_config=GuidanceConfig(
            enable_telemetry=True, enable_feedback_submission=False
        ),
    )
    payload = {
        "title": "Gateway refuses to start after upgrade",
        "description": "The gateway exits during startup with no diagnostic.",
        "issue_type": "bug",
        "confirm_submission": False,
    }
    preview = await no_submission.submit_feedback(dict(payload))
    decision = _decide_feedback(
        project_root=root, environ=dict(os.environ), submission=False, confirm=False
    )
    assert decision.reason == "submission_not_enabled"
    assert decision.remedy is not None
    assert decision.remedy in preview.message, preview.message

    telemetry_off = GatewayTools(
        client_manager=cast(Any, _StubClientManager()),
        policy_manager=PolicyManager(policy_path=None),
        project_root=root,
        guidance_config=GuidanceConfig(
            enable_telemetry=False, enable_feedback_submission=True
        ),
    )
    refused = await telemetry_off.submit_feedback(dict(payload))
    off = _decide_feedback(
        project_root=root, environ=dict(os.environ), telemetry=False, confirm=False
    )
    assert off.reason == "telemetry_disabled"
    assert off.remedy is not None
    assert off.remedy in refused.message, refused.message
    assert refused.ok is False


def test_a_trust_cli_refusal_names_the_command_that_would_grant_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fourth surface: `pmcp trust` itself, refusing and then granting.

    Two halves, and both are the criterion. A `pmcp trust` invocation that is
    refused prints the form of the command that *would* work rather than a bare
    error; and the remedies the other gates print, run verbatim through this
    CLI, grant exactly what was refused.
    """
    # (a) A refused `pmcp trust approve-package`: a range pins nothing, and the
    # refusal names the exact spelling the provisioning refusal prints.
    with pytest.raises(SystemExit) as exit_info:
        _run_cli("trust", "approve-package", "good-pkg@^1.0.0")
    assert exit_info.value.code == 1
    error = capsys.readouterr().err
    assert "name@<version>" in error
    assert "as printed in the provisioning refusal" in error
    assert not package_approvals_path().exists()

    # (b) The provisioning refusal's own remedy, run as printed, grants it.
    identity = PackageIdentity("npm", "good-pkg", "1.2.3", None)
    decision = evaluate_provision(
        _server("pinned", ["-y", "good-pkg@1.2.3"]),
        identity,
        source="configured",
        policy=_policy(tmp_path, "trust-cli", "servers: {}\n"),
    )
    assert (decision.allowed, decision.reason) == (False, "not_approved")
    assert decision.remedy is not None
    argv = shlex.split(decision.remedy)
    assert argv[0] == "pmcp"
    _run_cli(*argv[1:])
    assert package_approvals_path().exists()
    assert (
        evaluate_provision(
            _server("pinned", ["-y", "good-pkg@1.2.3"]),
            identity,
            source="configured",
            policy=_policy(tmp_path, "trust-cli", "servers: {}\n"),
        ).reason
        == "package_approved"
    )


# --------------------------------------------------------------------------- #
# Layer 5: honesty.
# --------------------------------------------------------------------------- #


#: The refusals for which no ``pmcp`` verb exists, pinned by case id. This set
#: may SHRINK when a later phase adds a verb; it must never grow silently. Each
#: entry is here because building the verb would be new operator surface in a
#: phase whose charter is to document and prove.
#:
#: NOTE for the record: SEAL's plan says this set has five members, "pinned by
#: reason id". Five is the count of no-verb remedy *helpers* Finding 3 found by
#: reading the source; driven off the three closed reason vocabularies instead,
#: which is what the criterion actually requires, the set is twelve cases across
#: nine reasons. The plan attributes only one no-verb reason to the egress gate
#: ("a planted credential") where six of its nine reasons name no verb, and
#: counts `denied`'s two remedies as two entries where the gate has three. The
#: plan under-counts; the truth is pinned here and reported upward.
_NO_PMCP_VERB_CASES = frozenset(
    {
        # A packages.denylist entry: remedied by editing the operator's policy file.
        "provision:denied:identity",
        "provision:denied:manifest_denylist",
        # An npx argument pmcp cannot place: same file, different sentence.
        "provision:denied:manifest_undetermined",
        # No resolved identity: remedied through gateway.register_discovered_server.
        "provision:unresolvable_identity",
        "provision:not_approved:gate_error",
        # An unpinned argv: remedied in .mcp.json, or by registering again.
        "provision:unpinned_configured_argv",
        # A planted destination or credential, and the shapes beside them: two
        # store paths and a shell export. `pmcp secrets` ships set/sync/check
        # only, so a removal verb would be a fiction.
        "feedback:untrusted_repository_override",
        "feedback:invalid_repository",
        "feedback:untrusted_token",
        "feedback:no_feedback_token",
        "feedback:gate_error",
        # The agent is told what to do next; there is no operator command at all.
        "feedback:not_confirmed",
    }
)

#: The same set at reason granularity, so neither a new case id nor a new reason
#: can slip past. A reason is listed only when EVERY case for it names no verb.
_NO_PMCP_VERB_REASONS = frozenset(
    {
        ("provision", "denied"),
        ("provision", "unresolvable_identity"),
        ("provision", "unpinned_configured_argv"),
        ("feedback", "untrusted_repository_override"),
        ("feedback", "invalid_repository"),
        ("feedback", "not_confirmed"),
        ("feedback", "untrusted_token"),
        ("feedback", "no_feedback_token"),
        ("feedback", "gate_error"),
    }
)


def test_the_remedies_that_name_no_pmcp_verb_are_exactly_the_enumerated_set(
    cases: list[Case],
) -> None:
    """The exceptions are named, so the set can shrink but never grow silently.

    This is also the test that stops the audit being decorative. A build whose
    every remedy were the same harmless placeholder would satisfy "non-empty",
    "runnable" and "names a real verb" vacuously -- and fail here, because the
    no-verb set would be either everything or nothing.
    """
    verbs = _dispatched_top_level_verbs()
    no_verb = {
        case.case_id
        for case in _refusals(cases)
        if not _pmcp_commands(case.remedy, verbs)
    }

    assert no_verb == _NO_PMCP_VERB_CASES, (
        "the set of refusals naming no pmcp verb changed. Added: "
        f"{sorted(no_verb - _NO_PMCP_VERB_CASES)}; removed: "
        f"{sorted(_NO_PMCP_VERB_CASES - no_verb)}. A new one must be justified "
        "here or given a verb."
    )

    by_reason = {
        (gate, reason)
        for gate, reason in {(case.gate, case.reason) for case in _refusals(cases)}
        if all(
            not _pmcp_commands(case.remedy, verbs)
            for case in _refusals(cases)
            if (case.gate, case.reason) == (gate, reason)
        )
    }
    assert by_reason == _NO_PMCP_VERB_REASONS

    # Every other refusal does name a verb, which is the half this set implies.
    with_verb = {case.case_id for case in _refusals(cases)} - no_verb
    assert with_verb, "no refusal named a pmcp verb at all"


def test_no_remedy_audit_case_reached_the_network(
    _no_egress: list[str],
) -> None:
    """Nothing in this file touched a socket or spawned a process.

    Two assertions, because either alone is weak. The cumulative list proves no
    case in this module attempted egress; the live probe proves the guard is
    armed rather than a fixture that silently stopped patching -- the failure
    mode that would make every other case here vacuous.
    """
    assert _EGRESS_ATTEMPTS == [], (
        f"a remedy-audit case attempted egress: {_EGRESS_ATTEMPTS}"
    )

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            probe.connect(("127.0.0.1", 9))
    finally:
        probe.close()
    assert _no_egress[-1].startswith("socket.connect"), _no_egress
    # The probe is this test's own; drop it so the teardown assertion is about
    # the audit, not about the proof that the guard works.
    _no_egress.clear()
    _EGRESS_ATTEMPTS.clear()
