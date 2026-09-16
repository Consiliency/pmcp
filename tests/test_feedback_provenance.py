"""Provenance of the variables PMCP itself put in its own environment (SL-1).

`gateway.submit_feedback` posts agent-authored text to GitHub. The gate this
phase adds (SL-2) must honour only a credential the **operator** supplied, and
refuse one **PMCP** introduced -- because an agent can plant
`PMCP_FEEDBACK_TOKEN` through `gateway.auth_connect`, and a checkout's dotenv
can plant it too (Consiliency/pmcp#230).

The distinction has to be **durable**, which is why this lane records it rather
than looking it up. `auth_connect` writes the credential store and then
`os.environ[env_var]`, but store membership is not evidence that survives:
`set_env_value` is a read-modify-write over `read_env_file`, which returns `{}`
for a file it cannot read -- so any LATER `auth_connect`, for any unrelated
server, can rewrite the store without an earlier key while the planted variable
stays in `os.environ`. A check whose evidence can disappear while the thing it
proves persists is not a check, and the disappearance is reachable through a
tool the agent calls. `record_pmcp_introduced_keys` / `pmcp_introduced_keys`
are that durable record, shaped exactly like `record_dotenv_keys` /
`dotenv_sourced_keys` so `env_store` has one idiom rather than two.

Two boundaries are pinned here because both are ways this has been got wrong:

* `dotenv_sourced_keys` is left alone. `sanitized_subprocess_env` strips its
  keys from every spawned child, so widening its membership would change
  behaviour outside this phase (`test_the_new_registry_does_not_widen_the_
  dotenv_strip`).
* `managed_secret_keys` keeps swallowing `OSError`/`ValueError` around its
  project lookup for its one existing caller, and the gate gets a strict
  variant instead -- because a swallowed failure makes "the lookup failed"
  indistinguishable from "the key is not planted", and the failure direction is
  ALLOW (`test_the_strict_lookup_raises_where_the_lenient_one_swallows`,
  `test_the_lenient_lookup_keeps_its_existing_caller_behaviour`).
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterator
from pathlib import Path
import socket
from typing import Any, get_args

from pydantic import ValidationError
import pytest

from pmcp import env_store
from pmcp.env_store import (
    dotenv_sourced_keys,
    managed_secret_keys,
    managed_secret_keys_strict,
    pmcp_introduced_keys,
    record_pmcp_introduced_keys,
    reset_pmcp_introduced_keys,
    sanitized_subprocess_env,
)
from pmcp.types import (
    FeedbackSubmissionOutcome,
    SubmitFeedbackInput,
    SubmitFeedbackOutput,
)

PREFIX = "PMCP_TEST_230_"
PLANTED = f"{PREFIX}FEEDBACK_TOKEN"
OTHER = f"{PREFIX}OTHER_TOKEN"


@pytest.fixture(autouse=True)
def _reset_pmcp_introduced_registry() -> Iterator[None]:
    """Keep the PMCP-introduced provenance registry from leaking between tests.

    The registry is process-global and additive, and production paths tests
    exercise write to it: any test that drives `auth_connect` records the key it
    planted. `tests/conftest.py`'s `_reset_dotenv_provenance` is the model; this
    phase declares its own because `conftest.py` is CONSENT SL-1's file.
    """
    reset_pmcp_introduced_keys()
    yield
    reset_pmcp_introduced_keys()


@pytest.fixture(autouse=True)
def _no_live_github(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail any test in this module that reaches the network at all.

    The phase's whole point is that `submit_feedback` must not post under the
    operator's ambient credentials, and a refusal assertion can pass for the
    wrong reason offline -- PKGID's amendment 6 found three tests that had
    silently started calling the real npm registry and still passed. Modelled on
    `tests/conftest.py`'s `_no_live_npm_registry` (`:168`), but guarding the
    socket rather than `feedback_egress`'s opener: that module is SL-2's and
    does not exist in this wave (see the report's plan-defect note).
    """
    attempts: list[str] = []

    def _refuse_connect(self: socket.socket, address: Any, *args: Any) -> Any:
        attempts.append(f"connect {address!r}")
        raise OSError("outbound connections are disabled in these tests")

    def _refuse_dns(host: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append(f"getaddrinfo {host!r}")
        raise OSError("DNS lookups are disabled in these tests")

    monkeypatch.setattr(socket.socket, "connect", _refuse_connect)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse_dns)
    yield attempts
    assert attempts == [], f"a test reached the network: {attempts}"


def _write_user_store(home: Path, body: str) -> None:
    (home / ".config" / "pmcp").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "pmcp" / "pmcp.env").write_text(body)


def _src_calls_to(name: str) -> list[str]:
    """Every call to `name` anywhere under `src/pmcp`, as `path:lineno`.

    A `def` is not an `ast.Call`, so the definition itself never matches and no
    exclusion is needed.
    """
    src_root = Path(env_store.__file__).parent
    callers: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = None
            if isinstance(func, ast.Name):
                called = func.id
            elif isinstance(func, ast.Attribute):
                called = func.attr
            if called == name:
                callers.append(f"{path.name}:{node.lineno}")
    return callers


def _called_names_in(function: str) -> set[str]:
    """Names called inside one `env_store` function, for structural pins."""
    tree = ast.parse(
        Path(env_store.__file__).read_text(encoding="utf-8"),
        filename=env_store.__file__,
    )
    definition = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function
    )
    names: set[str] = set()
    for node in ast.walk(definition):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def _raise_on_project_store(
    monkeypatch: pytest.MonkeyPatch, project_store: Path
) -> None:
    """Make exactly the project-store read fail, the way an unreadable file does.

    Monkeypatched rather than `chmod 000` so the frozen node ids do not depend
    on the uid the suite runs as -- root reads a mode-000 file. The real-file
    comparison is `test_an_unreadable_project_store_file_separates_the_lookups`.
    """
    real_read = env_store.read_env_file

    def _read(path: Path) -> dict[str, str]:
        if path == project_store:
            raise PermissionError(13, "Permission denied", str(path))
        return real_read(path)

    monkeypatch.setattr(env_store, "read_env_file", _read)


def test_a_key_pmcp_wrote_at_runtime_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The write route: `auth_connect` puts a credential in `os.environ`
    (`handlers.py:4972`) and records the key, so the gate can tell that variable
    from one the operator exported into their shell. Presence in `os.environ`
    proves nothing by itself -- the record is the whole distinction."""
    monkeypatch.setenv(PLANTED, "planted-through-auth-connect")
    monkeypatch.setenv(OTHER, "exported-by-the-operator")

    assert pmcp_introduced_keys() == frozenset(), (
        "an empty registry is the correct default: PMCP imported as a library "
        "has introduced nothing"
    )

    record_pmcp_introduced_keys({PLANTED})

    assert PLANTED in pmcp_introduced_keys()
    assert OTHER in os.environ
    assert OTHER not in pmcp_introduced_keys()


def test_the_registry_is_additive_and_not_production_clearable() -> None:
    """Additive, and no agent-reachable path can erase it.

    Additive because the durability argument depends on it: a second
    `auth_connect`, in the other scope, must not displace the first record. Not
    production-clearable because a registry a gateway tool could empty would be
    exactly the decaying evidence this lane exists to replace --
    `reset_pmcp_introduced_keys` is a test-only seam, so nothing under `src/`
    may call it, and `server.py`'s dispatch of the 26 tools cannot reach one.
    """
    record_pmcp_introduced_keys({PLANTED})
    record_pmcp_introduced_keys({OTHER})

    assert {PLANTED, OTHER} <= pmcp_introduced_keys()

    record_pmcp_introduced_keys({PLANTED})
    assert {PLANTED, OTHER} <= pmcp_introduced_keys(), "re-recording is not a clear"

    record_pmcp_introduced_keys(set())
    assert {PLANTED, OTHER} <= pmcp_introduced_keys(), "an empty delta is not a clear"

    snapshot = pmcp_introduced_keys()
    assert isinstance(snapshot, frozenset), "consumers get an immutable snapshot"
    assert not hasattr(snapshot, "discard"), "a consumer cannot drop a key"

    assert _src_calls_to("reset_pmcp_introduced_keys") == [], (
        "reset_pmcp_introduced_keys is a test-only seam: a production caller "
        "would make the gate's provenance evidence erasable"
    )


def test_the_strict_lookup_raises_where_the_lenient_one_swallows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`managed_secret_keys` suppresses `OSError`/`ValueError` around its
    project lookup, which makes "the lookup failed" indistinguishable from "the
    key is not planted" -- and the failure direction is ALLOW, so the gate's
    fail-closed rule could never fire for it. The strict variant lets the
    failure propagate; both read the same two files otherwise."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    _write_user_store(home, f"{OTHER}=from-the-user-store\n")
    monkeypatch.setenv("HOME", str(home))
    _raise_on_project_store(monkeypatch, project / ".env.pmcp")

    assert managed_secret_keys(project) == {OTHER}, (
        "the lenient lookup answers as though the project store held nothing"
    )

    with pytest.raises(OSError):
        managed_secret_keys_strict(project)

    # The user half is already unguarded in both, so fail-closed is reachable
    # there today; only the project half failed open.
    def _read_nothing(path: Path) -> dict[str, str]:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(env_store, "read_env_file", _read_nothing)
    with pytest.raises(OSError):
        managed_secret_keys(project)


def test_the_lenient_lookup_keeps_its_existing_caller_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sanitized_subprocess_env` is the lenient lookup's only `src/` caller and
    must keep swallowing: a failed strip is a smaller harm than a crashed spawn,
    and CONSENT builds on this. Pinned so a later lane cannot "fix" the swallow
    and change every downstream server's environment underneath that phase."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    _write_user_store(home, f"{OTHER}=from-the-user-store\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(OTHER, "from-the-user-store")
    monkeypatch.setenv("PATH", "/usr/bin")
    _raise_on_project_store(monkeypatch, project / ".env.pmcp")

    env = sanitized_subprocess_env(project=project)

    assert OTHER not in env, "the user store's key is still stripped"
    assert env["PATH"] == "/usr/bin", "non-secret ambient vars survive"

    called = _called_names_in("sanitized_subprocess_env")
    assert "managed_secret_keys" in called
    assert "managed_secret_keys_strict" not in called, (
        "the strict lookup belongs to the gate; routing the sanitiser through "
        "it would turn an unreadable project store into a failed server spawn"
    )


def test_the_submission_outcome_field_defaults_to_none() -> None:
    """`types.py` is additive: one optional field with a `None` default, so
    every existing construction and assertion is untouched. The alias lives in
    `types.py` -- `feedback_egress` imports it -- so the Literal is named once
    rather than in two files that can drift."""
    required = {
        "ok": True,
        "submitted": False,
        "repository": "owner/name",
        "issue_title": "a title long enough",
        "issue_body": "a body",
        "message": "a message",
    }

    assert SubmitFeedbackOutput(**required).submission_outcome is None

    assert set(get_args(FeedbackSubmissionOutcome)) == {
        "created",
        "refused",
        "not_dispatched",
        "dispatched_unconfirmed",
    }
    for outcome in get_args(FeedbackSubmissionOutcome):
        built = SubmitFeedbackOutput(**required, submission_outcome=outcome)
        assert built.submission_outcome == outcome

    with pytest.raises(ValidationError):
        SubmitFeedbackOutput(**required, submission_outcome="submitted")


def test_the_new_registry_does_not_widen_the_dotenv_strip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two registries are separate, and only one of them strips.

    `dotenv_sourced_keys` has a merged consumer -- `sanitized_subprocess_env`
    removes its keys from every spawned child (#229) -- so recording into the
    new registry must not remove anything from a child's environment. The new
    registry is read only by this phase's gate.
    """
    home = tmp_path / "home"
    _write_user_store(home, "")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(PLANTED, "planted-through-auth-connect")

    record_pmcp_introduced_keys({PLANTED})

    assert PLANTED not in dotenv_sourced_keys()
    assert sanitized_subprocess_env(project=tmp_path / "project")[PLANTED] == (
        "planted-through-auth-connect"
    )


def test_the_test_only_reset_seam_clears_the_registry() -> None:
    """The seam tests need, mirroring `reset_dotenv_keys`: without it, keys one
    test recorded would be visible to every later test in the process."""
    record_pmcp_introduced_keys({PLANTED})
    assert PLANTED in pmcp_introduced_keys()

    reset_pmcp_introduced_keys()

    assert pmcp_introduced_keys() == frozenset()


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root reads a mode-000 file, so the file cannot be made unreadable",
)
def test_an_unreadable_project_store_file_separates_the_lookups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same separation against a genuinely unreadable file rather than a
    monkeypatched read -- the condition the gate will actually meet."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()
    _write_user_store(home, f"{OTHER}=from-the-user-store\n")
    monkeypatch.setenv("HOME", str(home))

    project_store = project / ".env.pmcp"
    project_store.write_text(f"{PLANTED}=planted\n")
    project_store.chmod(0o000)
    try:
        assert managed_secret_keys(project) == {OTHER}
        with pytest.raises(OSError):
            managed_secret_keys_strict(project)
    finally:
        project_store.chmod(0o600)


def test_the_input_contract_is_unchanged() -> None:
    """SL-1 adds nothing to the tool's input: the new field is output-only, so
    the 26 gateway tool definitions and their schemas are untouched."""
    assert set(SubmitFeedbackInput.model_fields) == {
        "title",
        "description",
        "issue_type",
        "subordinate_server",
        "failed_tool_call",
        "confirm_submission",
    }
