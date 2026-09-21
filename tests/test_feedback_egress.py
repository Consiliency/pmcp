"""SL-4: `gateway.submit_feedback` itself, driven as production drives it.

Every node id here is frozen by `plans/phase-plan-v13-EGRESS.md` (SL-4.1, SL-4.3) and
run verbatim by EC-EGRESS-1..4, so each is a MODULE-LEVEL function: a class would
change the node id and break the acceptance commands.

**These are handler tests on purpose.** PKGID's lesson is that a gate-level test which
supplies an identity production never supplies proves nothing: SL-2's tests may hand
`evaluate_feedback_egress` any `environ` dict they like, and the gap that gate-level
coverage was meant to close went unnoticed. Every test below goes through
`gateway.submit_feedback` or `gateway.auth_connect` with `monkeypatch.setenv`, a real
`env_store.set_env_value` write, a real `gateway.auth_connect` call or a real
`load_dotenv` -- the inputs production actually has.

This file declares its own isolation rather than touching `tests/conftest.py`, which is
CONSENT's: a network guard that fails any test reaching GitHub *by either route* (the
transport's opener and, for the red phase, the handler's own `urlopen`, which this lane
deletes), a reset of the process-global `pmcp_introduced_keys` registry, a redirected
``HOME`` so no test reads the operator's real ``~/.config/pmcp/``, and a restore of the
four feedback environment variables -- `gateway.auth_connect` writes `os.environ`
directly, so a test that plants a token would otherwise leak it into the next one.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pmcp import cli, feedback_egress
from pmcp.config.guidance import GuidanceConfig
from pmcp.env_store import (
    pmcp_introduced_keys,
    reset_pmcp_introduced_keys,
)
from pmcp.feedback_egress import (
    PACKAGED_FEEDBACK_REPOSITORY,
    FeedbackProgress,
    FeedbackSubmission,
)
from pmcp.policy.policy import PolicyManager
from pmcp.tools import handlers
from pmcp.tools.handlers import GatewayTools, get_gateway_tool_definitions

_TOKEN_VAR = "PMCP_FEEDBACK_TOKEN"
_REPO_VAR = "PMCP_FEEDBACK_REPO"
_EXPORTED_TOKEN = "ghp-operator-exported-secret-value"
_TITLE = "Gateway refuses to start after upgrade"
_DESCRIPTION = "The gateway exits during startup with no diagnostic."


# --------------------------------------------------------------------------- #
# Isolation this file declares for itself.
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_live_github(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail any test whose handler reaches GitHub, by either route.

    Both routes are guarded because this file has to be honest in the RED phase as
    well: before SL-4 lands, `submit_feedback` opens its own `urllib.request.urlopen`
    bound into `handlers`, and only after it lands does the sole opener live in
    `feedback_egress`. `raising=False` is what lets the same fixture cover a name that
    exists now and is deleted by the implementation under test.
    """
    attempts: list[str] = []

    def _refuse(request: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append(getattr(request, "full_url", str(request)))
        raise AssertionError("live GitHub calls are disabled in this file")

    monkeypatch.setattr(feedback_egress._OPENER, "open", _refuse)
    monkeypatch.setattr(handlers, "urlopen", _refuse, raising=False)
    yield attempts
    assert attempts == [], f"a test reached GitHub: {attempts}"


@pytest.fixture(autouse=True)
def _gh_door_watch(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[list[str]]]:
    """`gh` looks installed everywhere in this file, and no spawn may happen anywhere.

    Autouse rather than opt-in, and this is not defensive tidiness: the RED run of this
    file against the pre-SL-4 handler really did reach `gh issue create --repo
    ViperJuice/pmcp` on a host where `gh` exists, because the tests that did not
    explicitly guard the door left it open. Door 3 authenticates from the inherited
    environment, so a test file that exercises this path at all must close it for every
    test in it, not only for the ones that assert about it.

    Recorded rather than refused: a raiser would fail somewhere inside the handler,
    and the property under test is the flat assertion that the list stayed empty.
    """
    spawns: list[list[str]] = []

    class _Process:
        returncode = 1

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"gh is not authenticated")

    async def _spawn(*cmd: Any, **kwargs: Any) -> Any:
        spawns.append([str(part) for part in cmd])
        return _Process()

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    yield spawns
    assert spawns == [], f"a test spawned a subprocess: {spawns}"


@pytest.fixture(autouse=True)
def _reset_pmcp_introduced() -> Iterator[None]:
    """The PMCP-introduced provenance registry is process-global; contain it here."""
    reset_pmcp_introduced_keys()
    yield
    reset_pmcp_introduced_keys()


@pytest.fixture(autouse=True)
def _isolated_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Point the user credential store at a temp dir, not the operator's real one."""
    home = tmp_path / "home"
    (home / ".config" / "pmcp").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    yield home


@pytest.fixture(autouse=True)
def _clean_feedback_env() -> Iterator[None]:
    """Restore the four variables this path reads, whoever wrote them.

    `monkeypatch` restores only what it set, and `gateway.auth_connect` writes
    `os.environ` itself -- so without this a planted token outlives its test.
    """
    names = (_TOKEN_VAR, _REPO_VAR, "GITHUB_TOKEN", "GH_TOKEN")
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
# Helpers.
# --------------------------------------------------------------------------- #


class _StubClientManager:
    """The little of a client manager `submit_feedback`/`auth_connect` can reach."""

    def get_all_tools(self) -> list[Any]:
        return []

    def get_tool(self, tool_id: str) -> Any:
        return None

    def is_server_online(self, name: str) -> bool:
        return False

    def is_lazy_server(self, name: str) -> bool:
        return False

    def get_server_status(self, name: str) -> Any:
        return None

    def get_all_server_statuses(self) -> list[Any]:
        return []

    def get_registry_meta(self) -> tuple[str, float]:
        return ("test-rev", 0.0)


def _gateway(
    *,
    submission: bool | None = False,
    telemetry: bool = True,
    project_root: Path | None = None,
) -> GatewayTools:
    """A gateway shaped the way an embedder builds one.

    ``submission=None`` builds it with **no** guidance config at all, which is the
    asymmetry `_telemetry_enabled` gets the other way round: absent config must read
    the submission flag OFF.
    """
    guidance = (
        None
        if submission is None
        else GuidanceConfig(
            enable_telemetry=telemetry, enable_feedback_submission=submission
        )
    )
    return GatewayTools(
        client_manager=_StubClientManager(),  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
        project_root=project_root,
        guidance_config=guidance,
    )


async def _submit(gateway: GatewayTools, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "title": _TITLE,
        "description": _DESCRIPTION,
        "issue_type": "bug",
        "confirm_submission": False,
    }
    payload.update(overrides)
    return await gateway.submit_feedback(payload)


class _Recorder:
    """A stand-in for the transport that records its calls and never sends."""

    def __init__(self, result: FeedbackSubmission | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.thread_ids: list[int] = []
        self._result = result or _created()

    def __call__(self, **kwargs: Any) -> FeedbackSubmission:
        self.calls.append(kwargs)
        self.thread_ids.append(threading.get_ident())
        return self._result


def _created(
    url: str = "https://github.com/o/r/issues/7", number: int = 7
) -> FeedbackSubmission:
    return FeedbackSubmission(
        outcome="created",
        issue_url=url,
        issue_number=number,
        repository_visibility="public",
        detail="created",
    )


def _install_transport(monkeypatch: pytest.MonkeyPatch, recorder: Any) -> None:
    """Replace the transport at BOTH names.

    The handler binds `submit_feedback_issue` at import, so patching only the
    `feedback_egress` attribute would not reach it; patching only the handler's name
    would not exist before SL-4 lands. `raising=False` covers the red phase.
    """
    monkeypatch.setattr(handlers, "submit_feedback_issue", recorder, raising=False)
    monkeypatch.setattr(feedback_egress, "submit_feedback_issue", recorder)


def _event_types(gateway: GatewayTools) -> list[str]:
    return [str(event.get("event_type")) for event in gateway._feedback_events]


class _Response:
    """A minimal `urlopen` answer: context manager, status, bounded reads."""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def getcode(self) -> int:
        return self.status

    def read(self, size: int = -1) -> bytes:
        if not self._body:
            return b""
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _created_body(number: int = 11) -> bytes:
    return json.dumps(
        {"html_url": f"https://github.com/o/r/issues/{number}", "number": number}
    ).encode("utf-8")


# --------------------------------------------------------------------------- #
# SL-4.1 -- the authority, preview and destination contracts.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_ambient_github_token_attempts_no_request(
    monkeypatch: pytest.MonkeyPatch, _gh_door_watch: list[list[str]]
) -> None:
    """EC-EGRESS-1. The operator's own GitHub identity is not pmcp's to spend.

    The whole point is that **no request is attempted at all**, so every door is
    watched at once: the transport is a recorder that must never be called, the
    module opener raises, `gh` looks installed and its spawn is recorded.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-the-operators-personal-token")
    monkeypatch.setenv("GH_TOKEN", "ghp-the-operators-personal-token")
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    result = await _submit(_gateway(submission=True), confirm_submission=True)

    assert recorder.calls == [], "a submission was attempted under an ambient token"
    assert _gh_door_watch == [], "the gh door was opened under an ambient token"
    assert result.submitted is False
    assert result.submission_outcome is None
    assert _TOKEN_VAR in result.message


@pytest.mark.asyncio
async def test_the_gh_cli_is_never_spawned_under_an_ambient_token(
    monkeypatch: pytest.MonkeyPatch, _gh_door_watch: list[list[str]]
) -> None:
    """EC-EGRESS-1, door 3. `gh` authenticates from the inherited environment.

    Deleting `GITHUB_TOKEN` from the token lookup alone merely routes an operator who
    has one past the API and into `gh`, which reads that same variable and posts
    anyway -- so the door is asserted gone structurally as well as behaviourally.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-the-operators-personal-token")
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    result = await _submit(_gateway(submission=True), confirm_submission=True)

    assert _gh_door_watch == []
    assert result.submitted is False
    source = inspect.getsource(GatewayTools.submit_feedback)
    assert not re.search(r"[\"']gh[\"']", source), "the gh door is still in the handler"
    assert "create_subprocess_exec" not in source


@pytest.mark.asyncio
async def test_confirm_submission_alone_does_not_post(
    monkeypatch: pytest.MonkeyPatch, _gh_door_watch: list[list[str]]
) -> None:
    """EC-EGRESS-2. Confirmation is the user's consent, not the operator's authority."""
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    result = await _submit(_gateway(submission=False), confirm_submission=True)

    assert recorder.calls == []
    assert _gh_door_watch == []
    assert result.ok is True
    assert result.submitted is False
    assert result.submission_outcome is None
    assert result.issue_url is not None
    assert result.issue_url.startswith(
        f"https://github.com/{PACKAGED_FEEDBACK_REPOSITORY}/issues/new?"
    )
    assert "pmcp guidance --feedback-submission on" in result.message
    assert "consent" in result.message.lower()


@pytest.mark.asyncio
async def test_the_preview_returns_the_payload_and_a_browser_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-2. The default call builds the payload and names where to put it."""
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    gateway = _gateway(submission=False)
    result = await _submit(gateway, confirm_submission=False)

    assert result.ok is True
    assert result.submitted is False
    assert recorder.calls == []
    assert "pmcp_version" in result.issue_body
    assert result.issue_body != _DESCRIPTION
    assert result.issue_url == feedback_egress.browser_issue_url(
        PACKAGED_FEEDBACK_REPOSITORY, result.issue_title, result.issue_body
    )
    assert "feedback_prepare" in _event_types(gateway)


@pytest.mark.asyncio
async def test_a_gateway_with_no_guidance_config_refuses_to_submit(
    monkeypatch: pytest.MonkeyPatch, _gh_door_watch: list[list[str]]
) -> None:
    """EC-EGRESS-2. Absent config reads the flag OFF -- the opposite of telemetry.

    `_telemetry_enabled` returns **True** for `guidance_config=None`. Copying that
    shape here would ship submission ON for every embedder that omits the config,
    which is exactly the asymmetry the freeze fixes at the handler.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    result = await _submit(_gateway(submission=None), confirm_submission=True)

    assert recorder.calls == []
    assert _gh_door_watch == []
    assert result.submitted is False
    assert "pmcp guidance --feedback-submission on" in result.message


@pytest.mark.asyncio
async def test_the_flag_on_without_a_token_previews_and_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, _gh_door_watch: list[list[str]]
) -> None:
    """EC-EGRESS-2. The enabled-but-uncredentialed path is a preview, not a failure."""
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    gateway = _gateway(submission=True)
    result = await _submit(gateway, confirm_submission=True)

    assert recorder.calls == []
    assert _gh_door_watch == []
    assert result.ok is True
    assert result.submitted is False
    assert result.repository == PACKAGED_FEEDBACK_REPOSITORY
    assert "pmcp_version" in result.issue_body
    assert result.issue_url is not None
    assert "/issues/new?" in result.issue_url
    assert _TOKEN_VAR in result.message
    assert "feedback_prepare" in _event_types(gateway)


@pytest.mark.asyncio
async def test_an_enabled_operator_with_a_token_posts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-1. The one arrangement that may post, and exactly what it hands over."""
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    gateway = _gateway(submission=True)
    result = await _submit(gateway, confirm_submission=True)

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["repository"] == PACKAGED_FEEDBACK_REPOSITORY
    assert call["token"] == _EXPORTED_TOKEN
    assert call["title"] == result.issue_title
    assert call["body"] == result.issue_body
    assert list(call["labels"]) == [
        "pmcp-feedback",
        "authenticated-feedback",
        "bug",
    ]
    assert isinstance(call["deadline"], float)
    assert isinstance(call["progress"], FeedbackProgress)

    assert result.ok is True
    assert result.submitted is True
    assert result.submission_outcome == "created"
    assert result.authenticated is True
    assert result.issue_number == 7
    assert result.repository_visibility == "public"
    assert "feedback_submitted" in _event_types(gateway)


@pytest.mark.asyncio
async def test_a_token_from_the_pmcp_credential_store_cannot_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-1. `gateway.auth_connect` can plant the very variable the gate reads.

    `env_var_allowed` admits any credential-shaped name when a server declares none,
    and `PMCP_FEEDBACK_TOKEN` is credential-shaped -- so this is a real agent-reachable
    route, driven here through the real tool rather than simulated.
    """
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)
    gateway = _gateway(submission=True)

    planted = await gateway.auth_connect(
        {
            "server_name": "some-server",
            "credential": "ghp-planted-by-the-agent",
            "env_var": _TOKEN_VAR,
            "scope": "user",
        }
    )
    assert planted.ok is True
    assert os.environ[_TOKEN_VAR] == "ghp-planted-by-the-agent"

    result = await _submit(gateway, confirm_submission=True)

    assert recorder.calls == [], "pmcp posted under a credential it planted itself"
    assert result.ok is False
    assert result.submitted is False
    assert "pmcp.env" in result.message


@pytest.mark.asyncio
async def test_a_checkout_env_file_cannot_supply_the_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """EC-EGRESS-1. A repository's `.env` reaches the gateway's own environment."""
    project = tmp_path / "checkout"
    project.mkdir()
    (project / ".env").write_text(f"{_TOKEN_VAR}=ghp-planted-by-a-checkout\n")
    monkeypatch.chdir(project)
    cli.load_startup_env(project / ".env")
    assert os.environ[_TOKEN_VAR] == "ghp-planted-by-a-checkout"

    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    result = await _submit(_gateway(submission=True), confirm_submission=True)

    assert recorder.calls == [], "pmcp posted under a token a checkout supplied"
    assert result.ok is False
    assert result.submitted is False


@pytest.mark.asyncio
async def test_a_checkout_env_file_cannot_redirect_the_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """EC-EGRESS-3. No attacker-named URL is ever handed to the agent.

    The destination is resolved *first*, so an untrusted override cannot appear in any
    output -- not in `repository`, and not in the browser URL door 4 renders.
    """
    project = tmp_path / "checkout"
    project.mkdir()
    (project / ".env").write_text(f"{_REPO_VAR}=attacker/evil\n")
    monkeypatch.chdir(project)
    cli.load_startup_env(project / ".env")
    assert os.environ[_REPO_VAR] == "attacker/evil"

    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    result = await _submit(_gateway(submission=True), confirm_submission=True)

    assert recorder.calls == []
    assert result.ok is False
    assert result.submitted is False
    assert result.repository == PACKAGED_FEEDBACK_REPOSITORY
    assert result.issue_url is None
    assert "attacker/evil" not in (result.issue_url or "")


@pytest.mark.asyncio
async def test_telemetry_disabled_builds_no_payload_and_records_no_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-2. The coarse switch refuses before anything is assembled."""
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    gateway = _gateway(submission=True, telemetry=False)
    result = await _submit(gateway, confirm_submission=True)

    assert recorder.calls == []
    assert result.ok is False
    assert result.submitted is False
    assert result.issue_title == _TITLE
    assert result.issue_body == _DESCRIPTION
    assert result.issue_url is None
    assert result.repository == PACKAGED_FEEDBACK_REPOSITORY
    assert result.repository_visibility == "unknown"
    assert "disabled" in result.message.lower()
    assert _event_types(gateway) == []


@pytest.mark.asyncio
async def test_the_default_repository_is_read_from_the_packaged_config() -> None:
    """EC-EGRESS-3. No literal repository name appears in this test, deliberately.

    `ViperJuice/pmcp` shipped as a default for exactly as long as nobody compared it to
    the packaged constant; a test that repeated the literal would have agreed with it.
    """
    result = await _submit(_gateway(submission=False))

    assert result.repository == PACKAGED_FEEDBACK_REPOSITORY
    assert result.issue_url is not None
    assert PACKAGED_FEEDBACK_REPOSITORY in result.issue_url


@pytest.mark.asyncio
async def test_a_hostile_repository_override_is_rendered_inert_in_the_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-3. A refusal prints the value it refused; it must not obey it."""
    hostile = "attacker/evil; rm -rf ~\x07$(id)"
    monkeypatch.setenv(_REPO_VAR, hostile)
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    result = await _submit(_gateway(submission=True), confirm_submission=True)

    assert recorder.calls == []
    assert result.ok is False
    assert result.submitted is False
    assert result.repository == PACKAGED_FEEDBACK_REPOSITORY
    assert result.issue_url is None
    assert "\x07" not in result.message
    assert hostile not in result.message


@pytest.mark.asyncio
async def test_no_blocking_http_call_runs_on_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4 (P-03). The submitter really blocks, and the loop really ticks.

    A mock that returns instantly satisfies neither assertion honestly: it would pass
    unchanged against `main`'s inline `urlopen`. This one sleeps for real and records
    the thread it ran on.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    recorder = _Recorder()

    def _blocking(**kwargs: Any) -> FeedbackSubmission:
        recorder.calls.append(kwargs)
        recorder.thread_ids.append(threading.get_ident())
        time.sleep(0.4)
        return _created()

    _install_transport(monkeypatch, _blocking)

    ticks = 0

    async def _heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.ensure_future(_heartbeat())
    try:
        result = await _submit(_gateway(submission=True), confirm_submission=True)
    finally:
        beat.cancel()

    assert result.submitted is True
    assert recorder.thread_ids, "the transport never ran"
    assert recorder.thread_ids[0] != threading.get_ident(), (
        "the blocking submission ran on the event loop's thread"
    )
    assert ticks > 5, f"the event loop stopped ticking during the submission: {ticks}"


@pytest.mark.asyncio
async def test_a_slow_submission_is_bounded_by_the_handler_timeout(
    monkeypatch: pytest.MonkeyPatch, _gh_door_watch: list[list[str]]
) -> None:
    """EC-EGRESS-4. A per-socket timeout is not a request bound; this is the bound."""
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    monkeypatch.setattr(
        handlers, "_FEEDBACK_SUBMIT_TIMEOUT_SECONDS", 0.25, raising=False
    )
    release = threading.Event()
    stall_finished = threading.Event()

    def _stall(**kwargs: Any) -> FeedbackSubmission:
        release.wait(timeout=10)
        stall_finished.set()
        return _created()

    _install_transport(monkeypatch, _stall)

    try:
        result = await _submit(_gateway(submission=True), confirm_submission=True)
        # The deterministic form of "it did not wait for the stall": the
        # transport had NOT finished when _submit returned, so the handler's own
        # timeout ended the call. Without that bound, _submit could only return
        # after `_stall` ran to completion -- which sets this flag.
        finished_before_return = stall_finished.is_set()
    finally:
        release.set()

    assert finished_before_return is False, (
        "the handler waited for the stalled submission"
    )
    assert result.ok is False
    assert result.submitted is False
    assert result.issue_url is not None
    assert result.issue_url.startswith("https://github.com/")
    assert _gh_door_watch == []


@pytest.mark.asyncio
async def test_a_failed_submission_opens_no_second_door(
    monkeypatch: pytest.MonkeyPatch, _gh_door_watch: list[list[str]]
) -> None:
    """EC-EGRESS-4. There is no fallback: a failure renders its outcome and stops."""
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    attempts: list[dict[str, Any]] = []

    def _raiser(**kwargs: Any) -> FeedbackSubmission:
        attempts.append(kwargs)
        raise RuntimeError("the transport failed")

    _install_transport(monkeypatch, _raiser)

    result = await _submit(_gateway(submission=True), confirm_submission=True)

    assert len(attempts) == 1, "the handler retried a failed submission"
    assert _gh_door_watch == [], "a failed submission fell back to the gh door"
    assert result.submitted is False


@pytest.mark.asyncio
async def test_a_preview_does_not_claim_the_repository_is_public() -> None:
    """EC-EGRESS-2. A claim about a repository pmcp did not ask about is not a fact."""
    result = await _submit(_gateway(submission=False))

    assert result.submitted is False
    assert result.repository_visibility == "unknown"


def test_the_tool_description_does_not_promise_that_confirmation_submits() -> None:
    """EC-EGRESS-2. The agent-facing text is part of the authority model.

    Any sentence that mentions `confirm_submission` must deny that it authorises a
    post: an agent told otherwise confirms, is refused, and has learned the wrong rule.
    """
    definitions = {tool.name: tool for tool in get_gateway_tool_definitions()}
    description = definitions["gateway.submit_feedback"].description or ""
    hint = _gateway(submission=False)._feedback_hint() or ""

    assert description
    assert hint
    for text in (description, hint):
        assert "confirm_submission=true to submit" not in text
        assert "pmcp guidance --feedback-submission" in text
        for sentence in re.split(r"(?<=[.;])\s+", text):
            if "confirm_submission" not in sentence:
                continue
            assert re.search(r"\bnot\b|\bnever\b", sentence), (
                f"this sentence promises confirmation submits: {sentence!r}"
            )


# --------------------------------------------------------------------------- #
# SL-4.3 -- durable provenance, and the post-deadline contract.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_planted_token_is_still_refused_after_its_store_entry_is_removed(
    monkeypatch: pytest.MonkeyPatch, _isolated_home: Path
) -> None:
    """EC-EGRESS-1, the durability layer. Store membership is not durable evidence.

    A check whose evidence can disappear while the thing it proves persists is not a
    check. The store file goes; `os.environ` keeps the plant; only the recorded write
    still refuses.
    """
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)
    gateway = _gateway(submission=True)

    planted = await gateway.auth_connect(
        {
            "server_name": "some-server",
            "credential": "ghp-planted-by-the-agent",
            "env_var": _TOKEN_VAR,
            "scope": "user",
        }
    )
    assert planted.ok is True
    store = Path(str(planted.env_path))
    store.unlink()
    assert not store.exists()
    assert os.environ[_TOKEN_VAR] == "ghp-planted-by-the-agent"

    result = await _submit(gateway, confirm_submission=True)

    assert recorder.calls == [], (
        "the plant was read as operator-supplied once the file went"
    )
    assert result.ok is False
    assert result.submitted is False


@pytest.mark.asyncio
async def test_the_provenance_record_survives_a_second_auth_connect_in_the_other_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """EC-EGRESS-1. The registry is additive: a later write displaces no earlier one."""
    project = tmp_path / "project"
    project.mkdir()
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)
    gateway = _gateway(submission=True, project_root=project)

    first = await gateway.auth_connect(
        {
            "server_name": "some-server",
            "credential": "ghp-planted-by-the-agent",
            "env_var": _TOKEN_VAR,
            "scope": "user",
        }
    )
    assert first.ok is True
    second = await gateway.auth_connect(
        {
            "server_name": "other-server",
            "credential": "unrelated-value",
            "env_var": "OTHER_SERVICE_TOKEN",
            "scope": "project",
        }
    )
    assert second.ok is True

    assert _TOKEN_VAR in pmcp_introduced_keys()
    assert "OTHER_SERVICE_TOKEN" in pmcp_introduced_keys()

    result = await _submit(gateway, confirm_submission=True)

    assert recorder.calls == []
    assert result.submitted is False


@pytest.mark.asyncio
async def test_a_startup_loaded_store_token_survives_an_unrelated_auth_connect_and_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _isolated_home: Path
) -> None:
    """EC-EGRESS-1, the chain the second panel named.

    A previous process's `auth_connect` put the token in the store; this process loads
    it at startup; an unrelated `auth_connect` then rewrites the store without it.
    `dotenv_sourced_keys` never saw it and the store no longer holds it -- recording
    the *load* is the only thing left that refuses.
    """
    project = tmp_path / "project"
    project.mkdir()
    store = _isolated_home / ".config" / "pmcp" / "pmcp.env"
    store.write_text(f"{_TOKEN_VAR}=ghp-planted-by-a-previous-process\n")
    monkeypatch.chdir(project)

    cli.load_startup_env(project / ".env")
    assert os.environ[_TOKEN_VAR] == "ghp-planted-by-a-previous-process"

    # The read-modify-write an unrelated server's auth_connect performs, over a store
    # file that is no longer there: the earlier entry is simply not carried forward.
    store.unlink()
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)
    gateway = _gateway(submission=True, project_root=project)
    unrelated = await gateway.auth_connect(
        {
            "server_name": "other-server",
            "credential": "unrelated-value",
            "env_var": "OTHER_SERVICE_TOKEN",
            "scope": "user",
        }
    )
    assert unrelated.ok is True
    assert _TOKEN_VAR not in store.read_text()

    result = await _submit(gateway, confirm_submission=True)

    assert recorder.calls == [], "a startup-loaded store token was honoured"
    assert result.ok is False
    assert result.submitted is False


@pytest.mark.asyncio
async def test_the_gate_is_asked_about_the_project_root_the_secret_writer_uses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """EC-EGRESS-1. `_write_secret` passes `self._project_root`; so must the gate.

    `resolve_project_root` walks up from the cwd only when its argument is `None`, so a
    gate asked with `None` would read a different file than `auth_connect` wrote -- and
    would look in the wrong place for exactly the plant it exists to catch. The
    registry is cleared first, so the **store lookup** is the only source left that can
    refuse, which is what makes the root the thing under test.
    """
    project = tmp_path / "project"
    project.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    roots: list[Any] = []
    real_gate = feedback_egress.evaluate_feedback_egress

    def _spy(**kwargs: Any) -> Any:
        roots.append(kwargs.get("project_root"))
        return real_gate(**kwargs)

    monkeypatch.setattr(handlers, "evaluate_feedback_egress", _spy, raising=False)
    monkeypatch.setattr(feedback_egress, "evaluate_feedback_egress", _spy)
    recorder = _Recorder()
    _install_transport(monkeypatch, recorder)

    gateway = _gateway(submission=True, project_root=project)
    planted = await gateway.auth_connect(
        {
            "server_name": "some-server",
            "credential": "ghp-planted-by-the-agent",
            "env_var": _TOKEN_VAR,
            "scope": "project",
        }
    )
    assert planted.ok is True
    assert Path(str(planted.env_path)).parent == project

    # Everything the runtime write recorded is discarded, so only a lookup against the
    # right project root can still see the plant.
    reset_pmcp_introduced_keys()

    result = await _submit(gateway, confirm_submission=True)

    assert roots == [project], f"the gate was asked about {roots}, not {project}"
    assert recorder.calls == []
    assert result.ok is False
    assert result.submitted is False


@pytest.mark.asyncio
async def test_a_submission_that_outlives_the_handler_bound_issues_no_post_afterwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4, the act layer. The bound is on the act, not only on the answer.

    `abandon_on_cancel=True` means the worker outlives the handler, so measuring how
    quickly the handler returned passes against a build that files the issue half a
    second later -- and the operator, told nothing was sent, then files it again from
    the compose URL. The worker is blocked *before* it may dispatch, released only
    after the handler has answered, and then joined: the opener must never have seen a
    POST.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    monkeypatch.setattr(
        handlers, "_FEEDBACK_SUBMIT_TIMEOUT_SECONDS", 0.25, raising=False
    )

    posts: list[str] = []
    release = threading.Event()
    finished = threading.Event()

    def _record_post(request: Any, *args: Any, **kwargs: Any) -> Any:
        posts.append(getattr(request, "full_url", str(request)))
        return _Response(201, _created_body())

    monkeypatch.setattr(feedback_egress._OPENER, "open", _record_post)

    real_compose = feedback_egress.browser_issue_url

    def _blocked_compose(repository: str, title: str, body: str) -> str:
        # Called at the top of `submit_feedback_issue`, before the claim: a real call,
        # held until the handler has given up.
        release.wait(timeout=10)
        return real_compose(repository, title, body)

    monkeypatch.setattr(feedback_egress, "browser_issue_url", _blocked_compose)

    real_submit = feedback_egress.submit_feedback_issue

    def _tracked(**kwargs: Any) -> FeedbackSubmission:
        try:
            return real_submit(**kwargs)
        finally:
            finished.set()

    _install_transport(monkeypatch, _tracked)

    result = await _submit(_gateway(submission=True), confirm_submission=True)

    assert result.submitted is False
    assert result.issue_url is not None

    release.set()
    assert finished.wait(timeout=10), "the abandoned worker never finished"
    assert posts == [], "the worker posted after the handler said nothing was sent"


@pytest.mark.asyncio
async def test_a_stall_before_the_claim_dispatches_nothing_after_the_handler_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4, the claim layer (the third panel's R1).

    Here the per-phase budget is small enough that the deadline check alone would let
    the worker through -- so the only thing that can stop it is losing the
    compare-and-set to the handler's `abandon()`. A lock that merely guards a snapshot
    slot does not prevent this: the handler reads an empty slot, answers
    `not_dispatched` with a **compose** URL, and the worker then posts.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    monkeypatch.setattr(
        handlers, "_FEEDBACK_SUBMIT_TIMEOUT_SECONDS", 0.3, raising=False
    )
    # NEGATIVE deliberately, and this is the whole test. The deadline handed to the
    # worker is the handler's own bound, so by the time the block is released the
    # deadline has passed -- and with any positive budget `claim_dispatch` refuses on
    # arithmetic before it ever consults the state. A negative budget makes the
    # budget check unconditionally pass, so the ONLY thing that can stop the worker is
    # losing the compare-and-set. Without this the test passes against a handler that
    # calls `latest()`, which is exactly the R1 race it exists to catch.
    monkeypatch.setattr(feedback_egress, "_POST_PHASE_BUDGET_SECONDS", -5.0)

    posts: list[str] = []
    release = threading.Event()
    finished = threading.Event()

    def _record_post(request: Any, *args: Any, **kwargs: Any) -> Any:
        posts.append(getattr(request, "full_url", str(request)))
        return _Response(201, _created_body())

    monkeypatch.setattr(feedback_egress._OPENER, "open", _record_post)

    real_compose = feedback_egress.browser_issue_url

    def _blocked_compose(repository: str, title: str, body: str) -> str:
        release.wait(timeout=10)
        return real_compose(repository, title, body)

    monkeypatch.setattr(feedback_egress, "browser_issue_url", _blocked_compose)

    real_submit = feedback_egress.submit_feedback_issue

    def _tracked(**kwargs: Any) -> FeedbackSubmission:
        try:
            return real_submit(**kwargs)
        finally:
            finished.set()

    _install_transport(monkeypatch, _tracked)

    result = await _submit(_gateway(submission=True), confirm_submission=True)

    assert result.submitted is False
    assert result.submission_outcome == "not_dispatched"
    assert result.issue_url is not None
    assert "/issues/new?" in result.issue_url

    release.set()
    assert finished.wait(timeout=10), "the abandoned worker never finished"
    assert posts == [], "the worker dispatched after losing the claim"


@pytest.mark.asyncio
async def test_a_dispatched_but_unconfirmed_post_is_not_reported_as_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4, the honesty layer. `submitted=False` is not "nothing happened".

    The gateway cannot know whether the issue exists, so it says so, hands back a
    *search* URL, and records no submission event. A compose URL after a possible
    success is how a duplicate gets filed.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    search_url = feedback_egress.browser_search_url(
        PACKAGED_FEEDBACK_REPOSITORY, _TITLE
    )
    unconfirmed = FeedbackSubmission(
        outcome="dispatched_unconfirmed",
        issue_url=search_url,
        issue_number=None,
        repository_visibility="unknown",
        detail="no_complete_response",
    )
    _install_transport(monkeypatch, _Recorder(unconfirmed))

    gateway = _gateway(submission=True)
    result = await _submit(gateway, confirm_submission=True)

    assert result.ok is False
    assert result.submitted is False
    assert result.submission_outcome == "dispatched_unconfirmed"
    assert result.issue_url == search_url
    assert "/issues?" in result.issue_url
    assert "may already exist" in result.message
    assert "feedback_submitted" not in _event_types(gateway)


@pytest.mark.asyncio
async def test_a_stall_after_dispatch_reports_unconfirmed_with_a_search_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4 (the second panel's Q2(b)). Winning the claim is a publication.

    The worker is stalled *inside* the transport, after the dispatch: `abandon()` finds
    the state `dispatching`, which is deliberately pessimistic -- telling an operator
    "this may exist, go look" when it does not is a cheap false positive, while the
    opposite error files a duplicate.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    monkeypatch.setattr(
        handlers, "_FEEDBACK_SUBMIT_TIMEOUT_SECONDS", 1.0, raising=False
    )
    monkeypatch.setattr(feedback_egress, "_POST_PHASE_BUDGET_SECONDS", 0.5)

    release = threading.Event()
    finished = threading.Event()

    def _stalled_post(request: Any, *args: Any, **kwargs: Any) -> Any:
        release.wait(timeout=10)
        return _Response(201, _created_body())

    monkeypatch.setattr(feedback_egress._OPENER, "open", _stalled_post)

    real_submit = feedback_egress.submit_feedback_issue

    def _tracked(**kwargs: Any) -> FeedbackSubmission:
        try:
            return real_submit(**kwargs)
        finally:
            finished.set()

    _install_transport(monkeypatch, _tracked)

    gateway = _gateway(submission=True)
    try:
        result = await _submit(gateway, confirm_submission=True)
    finally:
        release.set()
    finished.wait(timeout=10)

    assert result.ok is False
    assert result.submitted is False
    assert result.submission_outcome == "dispatched_unconfirmed"
    assert result.issue_url is not None
    assert "/issues?" in result.issue_url
    assert "/issues/new?" not in result.issue_url
    assert "feedback_submitted" not in _event_types(gateway)


@pytest.mark.asyncio
async def test_a_creation_observed_before_the_timeout_is_reported_as_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4. A timeout must not throw away a fact pmcp already had.

    The worker publishes `created` and then stalls; the handler's bound fires and
    `abandon()` returns the terminal snapshot rather than a pessimistic guess.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    monkeypatch.setattr(
        handlers, "_FEEDBACK_SUBMIT_TIMEOUT_SECONDS", 0.3, raising=False
    )
    release = threading.Event()
    created = _created("https://github.com/o/r/issues/31", 31)

    def _publish_then_stall(**kwargs: Any) -> FeedbackSubmission:
        kwargs["progress"].publish(created)
        release.wait(timeout=10)
        return created

    _install_transport(monkeypatch, _publish_then_stall)

    gateway = _gateway(submission=True)
    try:
        result = await _submit(gateway, confirm_submission=True)
    finally:
        release.set()

    assert result.ok is True
    assert result.submitted is True
    assert result.submission_outcome == "created"
    assert result.authenticated is True
    assert result.issue_url == "https://github.com/o/r/issues/31"
    assert result.issue_number == 31
    assert "feedback_submitted" in _event_types(gateway)


@pytest.mark.asyncio
async def test_a_stall_during_the_probe_does_not_lose_the_created_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4. The real transport, with the real probe stalling after a real POST.

    Only the informational probe was lost, so the visibility is `"unknown"` and the
    creation is still reported. Without the progress record the only answer available
    would be `dispatched_unconfirmed` -- the wrong one.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    monkeypatch.setattr(
        handlers, "_FEEDBACK_SUBMIT_TIMEOUT_SECONDS", 1.0, raising=False
    )
    monkeypatch.setattr(feedback_egress, "_POST_PHASE_BUDGET_SECONDS", 0.4)
    monkeypatch.setattr(feedback_egress, "_PROBE_PHASE_BUDGET_SECONDS", 0.05)

    release = threading.Event()
    finished = threading.Event()
    seen: list[str] = []

    def _post_then_stall(request: Any, *args: Any, **kwargs: Any) -> Any:
        method = getattr(request, "method", "GET")
        seen.append(method)
        if method == "POST":
            return _Response(201, _created_body(number=44))
        release.wait(timeout=10)
        return _Response(200, b'{"private": false}')

    monkeypatch.setattr(feedback_egress._OPENER, "open", _post_then_stall)

    real_submit = feedback_egress.submit_feedback_issue

    def _tracked(**kwargs: Any) -> FeedbackSubmission:
        try:
            return real_submit(**kwargs)
        finally:
            finished.set()

    _install_transport(monkeypatch, _tracked)

    gateway = _gateway(submission=True)
    try:
        result = await _submit(gateway, confirm_submission=True)
    finally:
        release.set()
    finished.wait(timeout=10)

    assert seen[:2] == ["POST", "GET"], f"the probe did not follow the post: {seen}"
    assert result.ok is True
    assert result.submitted is True
    assert result.submission_outcome == "created"
    assert result.issue_url == "https://github.com/o/r/issues/44"
    assert result.issue_number == 44
    assert result.repository_visibility == "unknown"
    assert "feedback_submitted" in _event_types(gateway)
