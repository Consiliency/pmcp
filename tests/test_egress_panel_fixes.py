"""Panel-reconcile fixes for EGRESS: the cancellation exit, and a store PMCP could not read.

Two defects the assembled phase's review panel found, narrowed to what is actually
reproducible on this tree (see Consiliency/pmcp#230).

**F1 -- cancellation bypasses both exception arms.** `submit_feedback` wrapped its
`anyio.fail_after` / `to_thread.run_sync(..., abandon_on_cancel=True)` block in
`except TimeoutError` and `except Exception`. A cancellation -- the caller disconnects,
the task group unwinds -- raises a subclass of `BaseException`, so NEITHER arm ran,
`progress.abandon()` was never called, and a worker parked before `claim_dispatch`
could still win the claim and POST after the handler had gone. That is the R1 race
reached through a different exit, and the tests below reach it by cancelling the task
rather than by letting the bound fire.

**F2 -- a store PMCP could not read was indistinguishable from a store that is not
there.** The panel named `read_env_file`'s swallowing as the mechanism; measured on
this tree (python-dotenv 1.2.3, uid 1000) that is only half true, and the tests here
pin the half that survives. An unreadable *file* already raises `PermissionError`, so
the strict lookup already failed closed for it. A **directory** at a store path -- and
any other non-regular file -- returned `{}` silently, which the gate read as "nothing
is planted" and allowed. These tests drive that through `gateway.submit_feedback` so
they assert on `gate_error` as an operator sees it, not on a helper in isolation.

Node ids here are new: EC-EGRESS-1..4 name their ids verbatim and none of them is
touched by this file.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest

from pmcp import feedback_egress
from pmcp.config.guidance import GuidanceConfig
from pmcp.env_store import reset_pmcp_introduced_keys
from pmcp.feedback_egress import FeedbackProgress, FeedbackSubmission
from pmcp.policy.policy import PolicyManager
from pmcp.tools import handlers
from pmcp.tools.handlers import GatewayTools

_TOKEN_VAR = "PMCP_FEEDBACK_TOKEN"
_REPO_VAR = "PMCP_FEEDBACK_REPO"
_EXPORTED_TOKEN = "ghp-operator-exported-secret-value"
_TITLE = "Gateway refuses to start after upgrade"
_DESCRIPTION = "The gateway exits during startup with no diagnostic."

#: A cancellation ending, tolerated where a test is not about how the handler answered.
#: Named as the concrete asyncio class rather than through
#: `anyio.get_cancelled_exc_class()`, which needs a running async context and would
#: raise at import; every test in this file runs on the asyncio backend.
_CANCELLED = (asyncio.CancelledError,)


# --------------------------------------------------------------------------- #
# Isolation, declared here rather than in `tests/conftest.py`, which is CONSENT's.
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_live_github(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail any test in this file that reaches GitHub."""
    attempts: list[str] = []

    def _refuse(request: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append(getattr(request, "full_url", str(request)))
        raise AssertionError("live GitHub calls are disabled in this file")

    monkeypatch.setattr(feedback_egress._OPENER, "open", _refuse)
    yield attempts
    assert attempts == [], f"a test reached GitHub: {attempts}"


@pytest.fixture(autouse=True)
def _gh_door_watch(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[list[str]]]:
    """`gh` looks installed, and no spawn may happen: door 3 must stay shut."""
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
    """Point the user credential store at a temp dir, not the operator's real one.

    Every test in this file makes the gate consult `managed_secret_keys_strict`, which
    reads `Path.home() / ".config" / "pmcp" / "pmcp.env"` unconditionally. Without this
    the F2 tests would be asserting about the operator's own store.
    """
    home = tmp_path / "home"
    (home / ".config" / "pmcp").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    yield home


@pytest.fixture(autouse=True)
def _clean_feedback_env() -> Iterator[None]:
    """Restore the variables this path reads, whoever wrote them."""
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
    """The little of a client manager `submit_feedback` can reach."""

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
    *, submission: bool = True, project_root: Path | None = None
) -> GatewayTools:
    return GatewayTools(
        client_manager=_StubClientManager(),  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
        project_root=project_root,
        guidance_config=GuidanceConfig(
            enable_telemetry=True, enable_feedback_submission=submission
        ),
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


def _park_worker_before_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[threading.Event, threading.Event, threading.Event, list[str]]:
    """Block the real transport in the moment BEFORE `claim_dispatch`, and watch posts.

    `browser_issue_url` is the first thing `submit_feedback_issue` calls, and it is
    reached through the module global, so replacing it parks the worker with the claim
    still unmade. The handler's own copy of the name is bound into `handlers` at import
    and is deliberately left alone, so the handler never blocks.

    `_POST_PHASE_BUDGET_SECONDS` is made NEGATIVE for the same reason the R1 test makes
    it negative: with any positive budget `claim_dispatch` refuses on arithmetic alone
    once the deadline has passed, and the test would pass against a handler that never
    abandons at all. A negative budget makes the budget check unconditionally pass, so
    losing the compare-and-set is the ONLY thing that can stop the worker.
    """
    monkeypatch.setattr(feedback_egress, "_POST_PHASE_BUDGET_SECONDS", -5.0)

    posts: list[str] = []

    def _record_post(request: Any, *args: Any, **kwargs: Any) -> Any:
        posts.append(getattr(request, "full_url", str(request)))
        return _Response(201, _created_body())

    monkeypatch.setattr(feedback_egress._OPENER, "open", _record_post)

    parked = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    real_compose = feedback_egress.browser_issue_url

    def _blocked_compose(repository: str, title: str, body: str) -> str:
        parked.set()
        release.wait(timeout=10)
        return real_compose(repository, title, body)

    monkeypatch.setattr(feedback_egress, "browser_issue_url", _blocked_compose)

    real_submit = feedback_egress.submit_feedback_issue

    def _tracked(**kwargs: Any) -> FeedbackSubmission:
        try:
            return real_submit(**kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(handlers, "submit_feedback_issue", _tracked)
    monkeypatch.setattr(feedback_egress, "submit_feedback_issue", _tracked)

    return parked, release, finished, posts


def _capture_progress(monkeypatch: pytest.MonkeyPatch) -> list[FeedbackProgress]:
    """Record the `FeedbackProgress` the handler builds, so its state is observable."""
    built: list[FeedbackProgress] = []

    def _factory() -> FeedbackProgress:
        progress = FeedbackProgress()
        built.append(progress)
        return progress

    monkeypatch.setattr(handlers, "FeedbackProgress", _factory)
    return built


async def _wait_for(event: threading.Event, timeout: float = 10.0) -> None:
    """Wait on a thread event without blocking the loop the handler runs on.

    A bare `event.wait()` here would deadlock: the handler task cannot reach its
    `to_thread` hand-off while this coroutine holds the loop.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not event.is_set():
        assert loop.time() < deadline, "the worker never parked before the claim"
        await asyncio.sleep(0.01)


# --------------------------------------------------------------------------- #
# F1 -- the cancellation exit.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_cancelled_handler_abandons_the_claim_so_the_worker_never_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1. The third exit from the submission block, which neither arm covered.

    A cancellation is a `BaseException`, so `except TimeoutError` and `except Exception`
    both miss it and `progress.abandon()` never runs. The worker parked before
    `claim_dispatch` then finds `pending` with the handler apparently still listening,
    wins the claim, and POSTs -- after the caller that asked for the submission has
    gone, and with nobody left to be told an issue was filed.

    **What this falsifies and what it does not.** Exactly one property: the worker
    does not send. It falsifies any build that fails to abandon on the cancelled path,
    including the unfixed one, and a build that re-raises without abandoning.

    It deliberately does NOT assert how the handler answered. How the task ends is
    `test_a_cancelled_handler_re_raises_rather_than_answering`'s property, and asserting
    it here too would make both tests fail for a build that abandons and then swallows
    the cancellation -- which would look like two independent confirmations of one
    defect and would hide that `posts == []` is undisturbed by that mutant. So the
    await tolerates either ending on purpose.

    Nothing here exercises the success path, so neither this test nor its sibling
    falsifies a build that abandons on EVERY exit, success included;
    `test_a_successful_submission_is_still_reported_after_the_cancellation_fix` is what
    catches that.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    parked, release, finished, posts = _park_worker_before_the_claim(monkeypatch)
    built = _capture_progress(monkeypatch)

    task = asyncio.create_task(_submit(_gateway(), confirm_submission=True))
    await _wait_for(parked)

    task.cancel()
    with contextlib.suppress(*_CANCELLED):
        await task

    # The POST assertion comes FIRST and is the one that matters: it is the harm, and
    # it is what goes red against the unfixed handler. `is_abandoned` is the mechanism
    # and is asserted after, so a red run names the leak rather than the bookkeeping.
    release.set()
    assert finished.wait(timeout=10), "the abandoned worker never finished"
    assert posts == [], "the worker dispatched after the handler was cancelled"

    assert built, "the handler never built a FeedbackProgress"
    assert built[0].is_abandoned(), "the cancelled handler did not abandon the claim"


@pytest.mark.asyncio
async def test_a_cancelled_handler_re_raises_rather_than_answering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1, the other half: a cancellation is never swallowed into a normal answer.

    Abandoning and then returning a `SubmitFeedbackOutput` would satisfy the test above
    while telling the surrounding task group that this tool call completed -- so the
    cancellation would be lost and the caller would be handed an answer it had already
    stopped waiting for. This asserts the task really ends cancelled and produced no
    result. It does not falsify a build that re-raises WITHOUT abandoning; that is the
    previous test's `posts == []`.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    parked, release, finished, _posts = _park_worker_before_the_claim(monkeypatch)

    task = asyncio.create_task(_submit(_gateway(), confirm_submission=True))
    await _wait_for(parked)

    task.cancel()
    with pytest.raises(anyio.get_cancelled_exc_class()):
        await task
    assert task.cancelled(), "the cancellation did not propagate out of the handler"

    release.set()
    assert finished.wait(timeout=10)


@pytest.mark.asyncio
async def test_a_successful_submission_is_still_reported_after_the_cancellation_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1's regression guard: the ordinary success path still reports the creation.

    This is what a `try/finally` that abandons on every exit would break --
    `FeedbackProgress.abandon` is not idempotent, and abandoning after a published
    `created` snapshot is how a filed issue gets reported as unsubmitted.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)

    def _open(request: Any, *args: Any, **kwargs: Any) -> Any:
        return _Response(201, _created_body(number=99))

    monkeypatch.setattr(feedback_egress._OPENER, "open", _open)
    built = _capture_progress(monkeypatch)

    result = await _submit(_gateway(), confirm_submission=True)

    assert result.submitted is True
    assert result.submission_outcome == "created"
    assert result.issue_number == 99
    assert built and not built[0].is_abandoned(), "a successful submission abandoned"


# --------------------------------------------------------------------------- #
# F2 -- "no store" and "a store I could not read" are different answers.
# --------------------------------------------------------------------------- #


def _plant_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()


def _plant_unreadable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{_TOKEN_VAR}=planted-by-pmcp\n", encoding="utf-8")
    os.chmod(path, 0o000)


_needs_non_root = pytest.mark.skipif(
    os.getuid() == 0,
    reason="root reads a mode-000 file, so the permission case cannot be staged",
)


@pytest.mark.asyncio
async def test_a_directory_at_the_project_store_is_a_gate_error_not_an_allow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F2. A store path that is not a regular file must not read as "nothing planted".

    Measured on this tree: `read_env_file` on a directory returns `{}` **silently** --
    `Path.exists()` is true and `dotenv_values` yields nothing -- so all three
    provenance sources agreed the operator had exported this token, and the gate
    allowed a post. A store PMCP cannot read is not a store PMCP has read.

    This falsifies any build whose strict lookup cannot tell a directory from an absent
    file. It does NOT falsify a build that raises for a *missing* store too; that would
    refuse every legitimate operator token, and
    `test_a_missing_store_still_allows_an_operator_exported_token` is what catches it.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    project = tmp_path / "project"
    _plant_directory(project / ".env.pmcp")

    result = await _submit(_gateway(project_root=project), confirm_submission=True)

    assert result.submitted is False
    assert result.ok is False
    assert "could not establish this submission's authority" in result.message


@pytest.mark.asyncio
async def test_a_directory_at_the_user_store_is_a_gate_error_not_an_allow(
    monkeypatch: pytest.MonkeyPatch, _isolated_home: Path
) -> None:
    """F2, the user half -- which `managed_secret_keys_strict` reads unguarded.

    The plan says rule 9 is "already reachable" for the user lookup because it carries
    no `except`. That is true only for failures that RAISE. A directory raises nothing,
    so the unguarded call was no safer than the guarded one.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    store = _isolated_home / ".config" / "pmcp" / "pmcp.env"
    _plant_directory(store)

    result = await _submit(_gateway(), confirm_submission=True)

    assert result.submitted is False
    assert result.ok is False
    assert "could not establish this submission's authority" in result.message


@_needs_non_root
@pytest.mark.asyncio
async def test_an_unreadable_project_store_is_a_gate_error_not_an_allow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F2. The permission case, which already failed closed and must keep doing so.

    Unlike the directory case this one is GREEN before the fix: `read_env_file` raises
    `PermissionError` on a mode-000 file, and rule 9 turns it into `gate_error`. It is
    here to pin that behaviour against a "fix" that reaches for a blanket
    `except OSError: return {}` in the strict reader -- which would close the directory
    hole and open this one.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    project = tmp_path / "project"
    store = project / ".env.pmcp"
    _plant_unreadable(store)
    try:
        result = await _submit(_gateway(project_root=project), confirm_submission=True)
    finally:
        os.chmod(store, 0o600)

    assert result.submitted is False
    assert result.ok is False
    assert "could not establish this submission's authority" in result.message


@_needs_non_root
@pytest.mark.asyncio
async def test_an_unreadable_user_store_is_a_gate_error_not_an_allow(
    monkeypatch: pytest.MonkeyPatch, _isolated_home: Path
) -> None:
    """F2. The permission case on the user half, pinned for the same reason."""
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    store = _isolated_home / ".config" / "pmcp" / "pmcp.env"
    _plant_unreadable(store)
    try:
        result = await _submit(_gateway(), confirm_submission=True)
    finally:
        os.chmod(store, 0o600)

    assert result.submitted is False
    assert result.ok is False
    assert "could not establish this submission's authority" in result.message


@pytest.mark.asyncio
async def test_a_missing_store_still_allows_an_operator_exported_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F2's other direction: "no store" is a legitimate answer and must stay an allow.

    A gate that refuses whenever it cannot read a store would refuse every operator who
    has never run `auth_connect` -- the common case. This is the test the fix must not
    break, and it is what separates "distinguish absent from unreadable" from "raise on
    anything that is not a parseable file".
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    project = tmp_path / "project"
    project.mkdir()
    assert not (project / ".env.pmcp").exists()

    def _open(request: Any, *args: Any, **kwargs: Any) -> Any:
        return _Response(201, _created_body(number=5))

    monkeypatch.setattr(feedback_egress._OPENER, "open", _open)

    result = await _submit(_gateway(project_root=project), confirm_submission=True)

    assert result.submitted is True
    assert result.submission_outcome == "created"


@pytest.mark.asyncio
async def test_a_dangling_symlink_store_reads_as_absent_not_as_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F2, the boundary the `exists()` check has to land on the right side of.

    A symlink whose target is gone is a store that is **not there**, and `Path.exists()`
    follows the link and says so. Ordering the check as "absent first, then not a
    regular file" is what keeps this an allow; `is_file()` alone is false for both a
    dangling symlink and a directory and would refuse this legitimate operator.
    """
    monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env.pmcp").symlink_to(project / "absent-target")

    def _open(request: Any, *args: Any, **kwargs: Any) -> Any:
        return _Response(201, _created_body(number=6))

    monkeypatch.setattr(feedback_egress._OPENER, "open", _open)

    result = await _submit(_gateway(project_root=project), confirm_submission=True)

    assert result.submitted is True
    assert result.submission_outcome == "created"


def test_the_lenient_lookup_still_swallows_a_directory_for_the_sanitiser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _isolated_home: Path
) -> None:
    """F2's containment: `sanitized_subprocess_env` must NOT acquire a new raise.

    CONSENT depends on `read_env_file` and the lenient `managed_secret_keys` keeping
    their behaviour -- a failed strip is a smaller harm than a crashed spawn. The strict
    reader is a separate function precisely so this stays true, and this test is what
    stops a later edit from moving the check down into `read_env_file`.
    """
    from pmcp.env_store import managed_secret_keys, read_env_file

    project = tmp_path / "project"
    _plant_directory(project / ".env.pmcp")

    assert read_env_file(project / ".env.pmcp") == {}
    assert managed_secret_keys(project) == set()
