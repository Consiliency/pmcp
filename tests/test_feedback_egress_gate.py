"""SL-2: the outbound-action gate and the transport behind `gateway.submit_feedback`.

Every node id here is frozen by `plans/phase-plan-v13-EGRESS.md` (SL-2.1, SL-2.3) and
run verbatim by EC-EGRESS-1..4, so each is a MODULE-LEVEL function: a class would
change the node id and break the acceptance commands.

This file declares its own isolation rather than touching `tests/conftest.py`, which is
CONSENT's (IF-0-EGRESS-1, "Every test file this phase adds declares two autouse
fixtures of its own"): a network guard that fails any test reaching api.github.com, and
a reset of the process-global `pmcp_introduced_keys` registry. A third is added here for
the same reason the first two exist -- `managed_secret_keys_strict` reads
``~/.config/pmcp/pmcp.env``, so without a redirected ``HOME`` every provenance test
would consult the operator's real credential store.
"""

from __future__ import annotations

import email.message
import importlib.metadata
import inspect
import json
import os
import re
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from pmcp import env_store, feedback_egress
from pmcp.env_store import (
    record_dotenv_keys,
    record_pmcp_introduced_keys,
    reset_pmcp_introduced_keys,
)

# A value no assertion may ever find rendered back out of a decision.
_TOKEN = "ghp-operator-exported-secret-value"
_TITLE = "Gateway refuses to start after upgrade"
_BODY = "steps to reproduce\nwith a newline"
_LABELS = ("pmcp-feedback", "authenticated-feedback", "bug")


# --------------------------------------------------------------------------- #
# Isolation this file declares for itself.
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_live_github(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail any test in this file whose transport reaches the real GitHub API.

    Modelled on PKGID's ``_no_live_npm_registry`` (``tests/conftest.py:168``), and for
    the same reason its amendment 6 records: a refusal assertion can pass for the wrong
    reason offline, so "no request was made" has to be asserted, not assumed. Tests that
    exercise the transport install their own stub over this one via ``_install_opener``.
    """
    attempts: list[str] = []

    def _refuse(request: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append(getattr(request, "full_url", str(request)))
        raise AssertionError("live GitHub calls are disabled in this file")

    monkeypatch.setattr(feedback_egress._OPENER, "open", _refuse)
    yield attempts
    assert attempts == [], f"a test reached GitHub: {attempts}"


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


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """An explicit project root, so no gate call ever walks up from the worktree."""
    root = tmp_path / "project"
    root.mkdir(exist_ok=True)
    return root


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #


class _RecordingEnviron(Mapping[str, str]):
    """An ``environ`` that remembers every name the gate asked it about."""

    def __init__(self, data: Mapping[str, str]) -> None:
        self._data = dict(data)
        self.queried: list[str] = []

    def __getitem__(self, key: str) -> str:
        self.queried.append(key)
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        self.queried.append(key)
        return self._data.get(key, default)


def _decide(
    *,
    root: Path,
    environ: Mapping[str, str] | None = None,
    telemetry: bool = True,
    submission: bool = True,
    confirm: bool = True,
) -> feedback_egress.FeedbackEgressDecision:
    return feedback_egress.evaluate_feedback_egress(
        telemetry_enabled=telemetry,
        submission_enabled=submission,
        confirm_submission=confirm,
        environ={} if environ is None else environ,
        project_root=root,
    )


def _headers(values: Mapping[str, str]) -> email.message.Message:
    message = email.message.Message()
    for key, value in values.items():
        message[key] = value
    return message


_GITHUB_REQUEST_ID = {"X-GitHub-Request-Id": "ABCD:1234:5678"}


class _FakeResponse:
    """What `_OPENER.open` returns: a context manager with `read`, status, headers."""

    def __init__(
        self,
        status: int = 201,
        body: bytes = b"",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = _headers(headers or _GITHUB_REQUEST_ID)
        self._body = body
        self._offset = 0
        self.reads = 0

    def read(self, size: int = -1) -> bytes:
        self.reads += 1
        if size is None or size < 0:
            chunk = self._body[self._offset :]
            self._offset = len(self._body)
            return chunk
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        return False


class _TricklingResponse(_FakeResponse):
    """A peer that never stops sending: one byte per read, forever."""

    def __init__(self) -> None:
        super().__init__(status=201, body=b"")

    def read(self, size: int = -1) -> bytes:
        self.reads += 1
        return b"x"


class _Clock:
    """A stand-in for `feedback_egress`'s `time`, so a phase can consume budget."""

    def __init__(self, start: float) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now


def _created_body(number: int = 77) -> bytes:
    return json.dumps(
        {
            "html_url": f"https://github.com/example/repo/issues/{number}",
            "number": number,
        }
    ).encode("utf-8")


def _http_error(status: int, *, request_id: bool = True) -> HTTPError:
    headers = _headers(_GITHUB_REQUEST_ID if request_id else {})
    return HTTPError(
        "https://api.github.com/repos/x/y/issues",
        status,
        "refused",
        headers,  # type: ignore[arg-type]
        None,
    )


def _install_opener(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[Any]:
    """Replace the module opener with *handler*; return the list of requests seen."""
    calls: list[Any] = []

    def _open(request: Any, timeout: Any = None, *args: Any, **kwargs: Any) -> Any:
        calls.append(request)
        return handler(request, len(calls))

    monkeypatch.setattr(feedback_egress._OPENER, "open", _open)
    return calls


def _never_called(request: Any, call_index: int) -> Any:
    raise AssertionError("the opener must not be called here")


def _submit(
    *,
    deadline: float | None = None,
    progress: feedback_egress.FeedbackProgress | None = None,
    repository: str | None = None,
) -> feedback_egress.FeedbackSubmission:
    return feedback_egress.submit_feedback_issue(
        repository=repository or feedback_egress.PACKAGED_FEEDBACK_REPOSITORY,
        token=_TOKEN,
        title=_TITLE,
        body=_BODY,
        labels=list(_LABELS),
        deadline=time.monotonic() + 30.0 if deadline is None else deadline,
        progress=progress or feedback_egress.FeedbackProgress(),
    )


# --------------------------------------------------------------------------- #
# SL-2.1 -- the credential, consent and destination rules.
# --------------------------------------------------------------------------- #


def test_the_gate_never_reads_github_token(project_root: Path) -> None:
    """EC-EGRESS-1: an ambient GITHUB_TOKEN/GH_TOKEN is not a feedback credential."""
    environ = _RecordingEnviron(
        {"GITHUB_TOKEN": "ambient-personal-token", "GH_TOKEN": "ambient-gh-token"}
    )

    decision = _decide(root=project_root, environ=environ)

    assert decision.submit_allowed is False
    assert decision.reason == "no_feedback_token"
    assert decision.token is None
    assert "GITHUB_TOKEN" not in environ.queried
    assert "GH_TOKEN" not in environ.queried
    # Structural, and the same pattern `## Verification` greps: it matches a *read*,
    # so the module may still say in prose why it does not perform one.
    reads = re.compile(
        r"environ[.\[][^\n]*(GITHUB_TOKEN|GH_TOKEN)|getenv\(.(GITHUB_TOKEN|GH_TOKEN)"
    )
    assert reads.search(inspect.getsource(feedback_egress)) is None


def test_the_submitter_reads_no_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-1: the transport's authority is its argument, structurally."""
    source = inspect.getsource(feedback_egress)
    assert "os.environ" not in source
    assert "getenv" not in source
    assert "environb" not in source

    class _Recorder(dict):  # type: ignore[type-arg]
        def __init__(self, base: Mapping[str, str]) -> None:
            super().__init__(base)
            self.queried: list[str] = []

        def get(self, key: Any, default: Any = None) -> Any:
            self.queried.append(key)
            return super().get(key, default)

        def __getitem__(self, key: Any) -> Any:
            self.queried.append(key)
            return super().__getitem__(key)

    recorder = _Recorder(dict(os.environ) | {"PMCP_FEEDBACK_TOKEN": "planted"})
    monkeypatch.setattr(os, "environ", recorder)
    _install_opener(
        monkeypatch, lambda request, index: _FakeResponse(body=_created_body())
    )

    result = _submit()

    assert result.outcome == "created"
    for name in ("PMCP_FEEDBACK_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
        assert name not in recorder.queried


def test_the_decision_never_renders_the_token(project_root: Path) -> None:
    """EC-EGRESS-1: the credential is carried, never displayed."""
    decision = _decide(root=project_root, environ={"PMCP_FEEDBACK_TOKEN": _TOKEN})

    assert decision.submit_allowed is True
    assert decision.token == _TOKEN
    assert _TOKEN not in repr(decision)
    assert _TOKEN not in str(decision)
    assert _TOKEN not in (decision.remedy or "")
    assert _TOKEN not in decision.reason
    assert _TOKEN not in decision.repository

    # The refusals are the tempting place to echo the value back ("this token came
    # from a file"), so they are asserted too, not just the allow.
    record_dotenv_keys(["PMCP_FEEDBACK_TOKEN"])
    refusal = _decide(root=project_root, environ={"PMCP_FEEDBACK_TOKEN": _TOKEN})

    assert refusal.reason == "untrusted_token"
    assert refusal.token is None
    assert _TOKEN not in repr(refusal)
    assert _TOKEN not in str(refusal)
    assert _TOKEN not in (refusal.remedy or "")


def test_a_checkout_sourced_token_is_refused(project_root: Path) -> None:
    """EC-EGRESS-1: a repository's `.env` cannot supply the credential."""
    record_dotenv_keys(["PMCP_FEEDBACK_TOKEN"])

    decision = _decide(root=project_root, environ={"PMCP_FEEDBACK_TOKEN": _TOKEN})

    assert decision.submit_allowed is False
    assert decision.reason == "untrusted_token"
    assert decision.remedy is not None
    assert ".env.pmcp" in decision.remedy


def test_a_pmcp_store_named_token_is_refused(project_root: Path) -> None:
    """EC-EGRESS-1: a key PMCP's own store holds now is a name collision, not consent."""
    env_store.set_env_value("project", "PMCP_FEEDBACK_TOKEN", _TOKEN, project_root)

    decision = _decide(root=project_root, environ={"PMCP_FEEDBACK_TOKEN": _TOKEN})

    assert decision.submit_allowed is False
    assert decision.reason == "untrusted_token"


def test_a_pmcp_introduced_key_is_refused_without_any_store_entry(
    project_root: Path,
) -> None:
    """EC-EGRESS-1: the durable record refuses after the store evidence is gone.

    No store file exists and no dotenv delta was recorded; only
    ``pmcp_introduced_keys`` -- the registry a later read-modify-write cannot erase --
    still says PMCP put this variable here.
    """
    record_pmcp_introduced_keys(["PMCP_FEEDBACK_TOKEN"])
    assert env_store.managed_secret_keys_strict(project_root) == set()

    decision = _decide(root=project_root, environ={"PMCP_FEEDBACK_TOKEN": _TOKEN})

    assert decision.submit_allowed is False
    assert decision.reason == "untrusted_token"


def test_a_project_store_lookup_failure_is_not_read_as_absent(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    """EC-EGRESS-2: a failed store read denies; the lenient lookup would have allowed.

    The failure is injected at ``read_env_file`` rather than produced with an
    unreadable file on disk, because a chmod-000 file proves nothing when the suite
    runs as root. (Corrected after measurement: ``dotenv_values`` does NOT swallow an
    unreadable path -- it raises ``PermissionError`` -- so that shape was already
    fail-closed. The shape that read as empty was a DIRECTORY at the store path, which
    ``_read_env_file_strict`` now refuses.) The injected ``OSError`` is exactly what
    the two lookups differ about.
    """
    project_store = env_store.resolve_scope_path("project", project_root)
    real_read = env_store.read_env_file

    def _read(path: Path) -> dict[str, str]:
        if path == project_store:
            raise OSError("project store is unreadable")
        return real_read(path)

    monkeypatch.setattr(env_store, "read_env_file", _read)

    # The lenient lookup answers "not planted" for a lookup that failed ...
    assert env_store.managed_secret_keys(project_root) == set()
    # ... the strict one says so.
    with pytest.raises(OSError):
        env_store.managed_secret_keys_strict(project_root)

    decision = _decide(root=project_root, environ={"PMCP_FEEDBACK_TOKEN": _TOKEN})

    assert decision.submit_allowed is False
    assert decision.reason == "gate_error"
    assert decision.remedy is not None


def test_the_submission_flag_is_reported_before_the_confirmation(
    project_root: Path,
) -> None:
    """EC-EGRESS-2: the ordinary first call never learns that confirming authorises."""
    decision = _decide(
        root=project_root,
        environ={"PMCP_FEEDBACK_TOKEN": _TOKEN},
        submission=False,
        confirm=False,
    )

    assert decision.reason == "submission_not_enabled"
    assert decision.remedy == "pmcp guidance --feedback-submission on"


def test_telemetry_disabled_is_reported_before_the_submission_flag(
    project_root: Path,
) -> None:
    """EC-EGRESS-2: the coarser switch is named first; telemetry would override anyway."""
    decision = _decide(
        root=project_root,
        environ={"PMCP_FEEDBACK_TOKEN": _TOKEN},
        telemetry=False,
        submission=False,
        confirm=False,
    )

    assert decision.reason == "telemetry_disabled"
    assert decision.remedy == "pmcp guidance --telemetry on"


def test_an_operator_exported_token_with_the_flag_is_allowed(
    project_root: Path,
) -> None:
    """EC-EGRESS-1: the one path that may post -- and only with every switch set."""
    decision = _decide(root=project_root, environ={"PMCP_FEEDBACK_TOKEN": _TOKEN})

    assert decision.submit_allowed is True
    assert decision.reason == "submit_allowed"
    assert decision.remedy is None
    assert decision.token == _TOKEN
    assert decision.repository == feedback_egress.PACKAGED_FEEDBACK_REPOSITORY


def test_a_provenance_lookup_failure_denies_rather_than_raising(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> None:
    """EC-EGRESS-2, rule 9: a raise must never reach a caller that could read it as allow."""
    for name in (
        "dotenv_sourced_keys",
        "pmcp_introduced_keys",
        "managed_secret_keys_strict",
    ):

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("provenance registry is unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(feedback_egress, name, _boom)
            decision = _decide(
                root=project_root, environ={"PMCP_FEEDBACK_TOKEN": _TOKEN}
            )

        assert decision.submit_allowed is False, name
        assert decision.reason == "gate_error", name
        assert decision.token is None, name
        # Even a gate error names a validated destination and never the raw override.
        assert decision.repository == feedback_egress.PACKAGED_FEEDBACK_REPOSITORY


def test_the_default_repository_is_the_packaged_constant(project_root: Path) -> None:
    """EC-EGRESS-3: the destination with no override is the packaged symbol."""
    decision = _decide(root=project_root, environ={"PMCP_FEEDBACK_TOKEN": _TOKEN})

    assert decision.repository == feedback_egress.PACKAGED_FEEDBACK_REPOSITORY
    assert feedback_egress.browser_issue_url(
        decision.repository, _TITLE, _BODY
    ).startswith(f"https://github.com/{feedback_egress.PACKAGED_FEEDBACK_REPOSITORY}/")


def test_the_packaged_default_repository_matches_the_distribution_metadata() -> None:
    """EC-EGRESS-3: the drift oracle that would have caught `ViperJuice/pmcp`.

    `importlib.metadata` is the oracle, never the runtime source: a security gate must
    not acquire "absent or malformed distribution metadata" as a new failure mode in
    the branch that decides where an operator's text is sent.
    """
    urls = importlib.metadata.metadata("pmcp").get_all("Project-URL") or []
    repository_urls = [
        value.split(",", 1)[1].strip()
        for value in urls
        if value.split(",", 1)[0].strip().lower() == "repository"
    ]
    assert repository_urls, f"no Project-URL: Repository in metadata: {urls}"
    owner_name = repository_urls[0].rstrip("/").removesuffix(".git").split("/")[-2:]

    assert "/".join(owner_name) == feedback_egress.PACKAGED_FEEDBACK_REPOSITORY


def test_a_checkout_sourced_repository_override_is_refused(project_root: Path) -> None:
    """EC-EGRESS-3: a repository's `.env` cannot redirect the destination."""
    record_dotenv_keys(["PMCP_FEEDBACK_REPO"])

    decision = _decide(
        root=project_root,
        environ={"PMCP_FEEDBACK_REPO": "attacker/evil", "PMCP_FEEDBACK_TOKEN": _TOKEN},
    )

    assert decision.submit_allowed is False
    assert decision.reason == "untrusted_repository_override"
    assert decision.repository == feedback_egress.PACKAGED_FEEDBACK_REPOSITORY


def test_a_malformed_repository_override_is_refused(project_root: Path) -> None:
    """EC-EGRESS-3: an override that is not `owner/repo` is never substituted."""
    for bad in (
        "not-a-repo",
        "owner/repo/extra",
        "owner/repo\n",
        "owner/re;po",
        "https://github.com/owner/repo",
        "",
        " owner/repo",
        "a" * 101 + "/repo",
    ):
        decision = _decide(
            root=project_root,
            environ={"PMCP_FEEDBACK_REPO": bad, "PMCP_FEEDBACK_TOKEN": _TOKEN},
        )
        assert decision.submit_allowed is False, bad
        assert decision.reason == "invalid_repository", bad
        assert decision.repository == feedback_egress.PACKAGED_FEEDBACK_REPOSITORY, bad


def test_a_refused_destination_yields_no_browser_url(project_root: Path) -> None:
    """EC-EGRESS-3: an attacker-named destination reaches no output, URL included.

    The refusal's own remedy renders the offending value inert (`operator_safe`) and
    the resolved `repository` is back at the packaged default, so the browser URL the
    agent is told to open can only ever name the packaged repository.
    """
    hostile = "attacker/evil\x1b]0;pwn\x07$(whoami)"
    record_dotenv_keys(["PMCP_FEEDBACK_REPO"])

    decision = _decide(
        root=project_root,
        environ={"PMCP_FEEDBACK_REPO": hostile, "PMCP_FEEDBACK_TOKEN": _TOKEN},
    )

    assert decision.submit_allowed is False
    assert decision.repository == feedback_egress.PACKAGED_FEEDBACK_REPOSITORY
    assert decision.remedy is not None
    assert hostile not in decision.remedy
    assert "\x1b" not in decision.remedy
    url = feedback_egress.browser_issue_url(decision.repository, _TITLE, _BODY)
    assert "attacker/evil" not in url


def test_the_browser_url_names_the_resolved_repository() -> None:
    """EC-EGRESS-3: door 4 points at the resolved destination and carries the payload."""
    url = feedback_egress.browser_issue_url("owner/repo", "a title", "a body & more")

    assert url.startswith("https://github.com/owner/repo/issues/new?")
    assert "title=a+title" in url
    assert "body=a+body+%26+more" in url


def test_the_search_url_names_the_repository_and_the_title() -> None:
    """EC-EGRESS-4: where to look for an issue that may already exist."""
    url = feedback_egress.browser_search_url("owner/repo", "a title")

    assert url.startswith("https://github.com/owner/repo/issues?")
    assert "a+title" in url
    assert "issues/new" not in url


# --------------------------------------------------------------------------- #
# SL-2.3 -- the deadline, progress and outcome contract.
# --------------------------------------------------------------------------- #


def test_an_expired_deadline_dispatches_no_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: nothing new starts after the deadline."""
    calls = _install_opener(monkeypatch, _never_called)

    result = _submit(deadline=time.monotonic() - 1.0)

    assert calls == []
    assert result.outcome == "not_dispatched"
    # The transport knows the destination and the payload, so what it returns carries
    # the compose URL even when the claim it lost was recorded by `FeedbackProgress`,
    # whose own snapshots have no destination to name.
    assert result.issue_url is not None
    assert "/issues/new?" in result.issue_url


def test_a_post_that_cannot_finish_within_the_budget_is_not_dispatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: a POST that cannot finish inside the bound must not start.

    A deadline that has not passed is not enough: a POST begun one second before it,
    with a ten-second socket timeout, still runs nine seconds past.
    """
    calls = _install_opener(monkeypatch, _never_called)

    result = _submit(
        deadline=time.monotonic() + feedback_egress._POST_PHASE_BUDGET_SECONDS - 1.0
    )

    assert calls == []
    assert result.outcome == "not_dispatched"


def test_the_phase_budgets_sum_below_the_handler_bound() -> None:
    """EC-EGRESS-4: an edit to one constant cannot break the invariant silently.

    `_FEEDBACK_SUBMIT_TIMEOUT_SECONDS` is SL-4's constant in `handlers.py`; until that
    lane lands, the freeze's value (IF-0-EGRESS-1: 20.0) stands in. Once SL-4 lands,
    this reads the real one and a later edit to either side fails here.
    """
    from pmcp.tools import handlers

    bound = getattr(handlers, "_FEEDBACK_SUBMIT_TIMEOUT_SECONDS", 20.0)

    assert (
        feedback_egress._POST_PHASE_BUDGET_SECONDS
        + feedback_egress._PROBE_PHASE_BUDGET_SECONDS
        < bound
    )


def test_the_visibility_probe_runs_only_after_a_created_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: the informational call never precedes the consequential one."""

    def _handler(request: Any, index: int) -> Any:
        assert index == 1, "the probe ran before or without a created issue"
        assert request.get_method() == "POST"
        raise _http_error(422)

    calls = _install_opener(monkeypatch, _handler)

    result = _submit()

    assert len(calls) == 1
    assert result.outcome == "refused"
    assert result.repository_visibility == "unknown"

    # And with a created issue it does run, after the POST.
    def _both(request: Any, index: int) -> Any:
        if index == 1:
            return _FakeResponse(body=_created_body())
        return _FakeResponse(status=200, body=json.dumps({"private": True}).encode())

    calls = _install_opener(monkeypatch, _both)
    result = _submit()

    assert len(calls) == 2
    assert calls[0].get_method() == "POST"
    assert calls[1].get_method() == "GET"
    assert result.outcome == "created"
    assert result.repository_visibility == "private"


def test_a_probe_with_no_remaining_budget_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: visibility is unknown rather than paid for past the bound.

    The clock is a shim because the case needs a POST that really consumed most of the
    budget: the claim refuses any deadline nearer than `_POST_PHASE_BUDGET_SECONDS`, so
    with an instantaneous stub POST there is always probe budget left.
    """
    clock = _Clock(1_000.0)
    monkeypatch.setattr(feedback_egress, "time", clock)

    def _handler(request: Any, index: int) -> Any:
        assert index == 1, "the probe ran with no budget left"
        clock.now += 8.0  # the POST spent most of the budget
        return _FakeResponse(body=_created_body())

    deadline = clock.now + feedback_egress._POST_PHASE_BUDGET_SECONDS + 2.0
    calls = _install_opener(monkeypatch, _handler)

    result = _submit(deadline=deadline)

    assert len(calls) == 1
    assert result.outcome == "created"
    assert result.repository_visibility == "unknown"


def test_a_dispatched_post_with_no_response_is_reported_as_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: bytes may have left and nothing establishes otherwise."""

    def _handler(request: Any, index: int) -> Any:
        raise URLError(socket.timeout("timed out"))

    _install_opener(monkeypatch, _handler)

    result = _submit()

    assert result.outcome == "dispatched_unconfirmed"
    assert result.issue_url is not None
    assert "issues/new" not in result.issue_url
    assert result.issue_url.startswith(
        f"https://github.com/{feedback_egress.PACKAGED_FEEDBACK_REPOSITORY}/issues?"
    )


def test_an_establishing_status_is_refused_with_a_compose_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: only GitHub's own answer establishes that nothing was created."""
    for status in (401, 403, 404, 410, 422):
        _install_opener(
            monkeypatch,
            lambda request, index, status=status: (_ for _ in ()).throw(
                _http_error(status)
            ),
        )

        result = _submit()

        assert result.outcome == "refused", status
        assert result.issue_url is not None
        assert "/issues/new?" in result.issue_url, status
        assert str(status) in result.detail, status


def test_a_gateway_error_status_is_unconfirmed_with_a_search_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: an intermediary's 5xx can follow a created issue."""
    for status in (500, 502, 503, 504, 400, 429):
        _install_opener(
            monkeypatch,
            lambda request, index, status=status: (_ for _ in ()).throw(
                _http_error(status)
            ),
        )

        result = _submit()

        assert result.outcome == "dispatched_unconfirmed", status
        assert result.issue_url is not None
        assert "/issues?" in result.issue_url, status
        assert "issues/new" not in result.issue_url, status


def test_an_error_without_a_github_request_id_is_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: without the header the answer never reached GitHub's application."""
    _install_opener(
        monkeypatch,
        lambda request, index: (_ for _ in ()).throw(
            _http_error(422, request_id=False)
        ),
    )

    result = _submit()

    assert result.outcome == "dispatched_unconfirmed"
    assert result.issue_url is not None
    assert "/issues?" in result.issue_url


def test_an_unparseable_success_body_is_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: a 2xx that yields no issue is uncertain, never a negative."""
    for body in (b"not json at all", b"{}", b"[]", b'{"number": 5}'):
        _install_opener(
            monkeypatch,
            lambda request, index, body=body: _FakeResponse(status=201, body=body),
        )

        result = _submit()

        assert result.outcome == "dispatched_unconfirmed", body
        assert result.issue_url is not None
        assert "/issues?" in result.issue_url, body


def test_a_dns_or_refused_connection_establishes_not_dispatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: DNS never resolved, or the peer refused -- no bytes were sent."""
    for error in (
        URLError(socket.gaierror(-2, "Name or service not known")),
        URLError(ConnectionRefusedError(111, "Connection refused")),
        socket.gaierror(-2, "Name or service not known"),
        ConnectionRefusedError(111, "Connection refused"),
    ):
        _install_opener(
            monkeypatch,
            lambda request, index, error=error: (_ for _ in ()).throw(error),
        )

        result = _submit()

        assert result.outcome == "not_dispatched", error
        assert result.issue_url is not None
        assert "/issues/new?" in result.issue_url, error


def test_the_worker_dispatches_only_if_it_wins_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: the claim is the last step before the opener and it gates the send."""
    progress = feedback_egress.FeedbackProgress()
    handler_answer = progress.abandon()
    assert handler_answer.outcome == "not_dispatched"

    calls = _install_opener(monkeypatch, _never_called)

    result = _submit(progress=progress)

    assert calls == []
    assert result.outcome == "not_dispatched"


def test_the_handler_may_report_not_dispatched_only_if_it_won_the_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4, R1: claim and abandon are two sides of one compare-and-set.

    Three checks: the handler wins and the worker is then forbidden to send; the worker
    wins and the handler must answer `dispatched_unconfirmed` rather than
    `not_dispatched`; and, driven concurrently many times, exactly one side wins.
    """
    # 1. Handler first.
    progress = feedback_egress.FeedbackProgress()
    assert progress.abandon().outcome == "not_dispatched"
    assert progress.claim_dispatch(time.monotonic() + 30.0) is False

    # 2. Worker first: the handler may no longer claim nothing was sent.
    progress = feedback_egress.FeedbackProgress()
    assert progress.claim_dispatch(time.monotonic() + 30.0) is True
    assert progress.abandon().outcome == "dispatched_unconfirmed"

    # 3. Concurrently, many times: exactly one side wins each round.
    for _ in range(200):
        progress = feedback_egress.FeedbackProgress()
        start = threading.Barrier(2)
        outcomes: dict[str, Any] = {}

        def _worker() -> None:
            start.wait()
            outcomes["claimed"] = progress.claim_dispatch(time.monotonic() + 30.0)

        def _handler() -> None:
            start.wait()
            outcomes["abandoned"] = progress.abandon().outcome

        threads = [threading.Thread(target=_worker), threading.Thread(target=_handler)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)
            assert not thread.is_alive()

        claimed = outcomes["claimed"]
        said_nothing_was_sent = outcomes["abandoned"] == "not_dispatched"
        assert claimed != said_nothing_was_sent, outcomes


def test_the_probe_is_skipped_once_the_handler_has_abandoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: nobody is listening, so do not spend the probe."""
    progress = feedback_egress.FeedbackProgress()

    def _handler(request: Any, index: int) -> Any:
        assert index == 1, "the probe ran after the handler abandoned"
        response = _FakeResponse(body=_created_body())
        progress.abandon()
        return response

    calls = _install_opener(monkeypatch, _handler)

    result = _submit(progress=progress)

    assert len(calls) == 1
    assert result.outcome == "created"
    assert progress.is_abandoned() is True


def test_the_creation_is_published_before_the_probe_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: the probe can no longer cost pmcp the report of a creation."""
    progress = feedback_egress.FeedbackProgress()
    seen_at_probe: list[Any] = []

    def _handler(request: Any, index: int) -> Any:
        if index == 1:
            return _FakeResponse(body=_created_body(number=99))
        seen_at_probe.append(progress.latest())
        return _FakeResponse(status=200, body=json.dumps({"private": False}).encode())

    _install_opener(monkeypatch, _handler)

    result = _submit(progress=progress)

    assert len(seen_at_probe) == 1
    published = seen_at_probe[0]
    assert published is not None
    assert published.outcome == "created"
    assert published.issue_number == 99
    assert result.repository_visibility == "public"


def test_a_stall_inside_the_transport_after_dispatch_publishes_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4, Q2(b): a worker stalled after the claim is uncertain, not a no."""
    progress = feedback_egress.FeedbackProgress()
    inside = threading.Event()
    release = threading.Event()

    def _handler(request: Any, index: int) -> Any:
        inside.set()
        assert release.wait(timeout=10.0)
        return _FakeResponse(body=_created_body())

    _install_opener(monkeypatch, _handler)
    worker = threading.Thread(target=lambda: _submit(progress=progress))
    worker.start()
    try:
        assert inside.wait(timeout=10.0)
        answer = progress.abandon()
    finally:
        release.set()
        worker.join(timeout=10.0)

    assert not worker.is_alive()
    # The arbiter has no destination to name, so the handler renders the search URL
    # from the outcome (the freeze's table); what it must never say here is
    # `not_dispatched`, which is what licenses a compose URL.
    assert answer.outcome == "dispatched_unconfirmed"
    assert answer.issue_url is None
    assert progress.is_abandoned() is True


def test_the_body_read_is_bounded_by_its_own_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: `urlopen`'s timeout is per socket operation, not per request.

    A peer trickling bytes keeps a call alive indefinitely, so the body read has its
    own bound: it rechecks the deadline and caps the bytes it will accept.
    """
    # The byte cap, end to end: a peer that never stops sending is cut off, and a
    # truncated body classifies as uncertain rather than as a creation or a refusal.
    response = _TricklingResponse()
    _install_opener(monkeypatch, lambda request, index: response)

    deadline = time.monotonic() + feedback_egress._POST_PHASE_BUDGET_SECONDS + 1.0
    started = time.monotonic()
    result = _submit(deadline=deadline)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, "the body read was not bounded"
    assert 1 < response.reads <= feedback_egress._MAX_RESPONSE_BYTES + 1
    assert result.outcome == "dispatched_unconfirmed"

    # The clock, on the loop itself: bounding only by bytes would let a slow peer hold
    # the call for `cap / rate` seconds, which is not a bound at all. Driven directly
    # because reaching it through the transport requires a deadline at least a whole
    # `_POST_PHASE_BUDGET_SECONDS` away -- the claim refuses anything nearer.
    slow = _TricklingResponse()
    read_start = time.monotonic()
    body = feedback_egress._read_bounded_body(slow, read_start - 1.0)

    assert body == b""
    assert slow.reads == 0
    assert time.monotonic() - read_start < 1.0


def test_the_progress_record_is_safe_to_read_while_the_worker_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EC-EGRESS-4: the handler reads snapshots, never a half-written record."""
    progress = feedback_egress.FeedbackProgress()
    inside = threading.Event()
    release = threading.Event()

    def _handler(request: Any, index: int) -> Any:
        if index == 1:
            inside.set()
            assert release.wait(timeout=10.0)
            return _FakeResponse(body=_created_body(number=42))
        return _FakeResponse(status=200, body=json.dumps({"private": False}).encode())

    _install_opener(monkeypatch, _handler)
    worker = threading.Thread(target=lambda: _submit(progress=progress))
    worker.start()
    try:
        assert inside.wait(timeout=10.0)
        observed = []
        for _ in range(500):
            observed.append((progress.latest(), progress.is_abandoned()))
        release.set()
        for _ in range(500):
            observed.append((progress.latest(), progress.is_abandoned()))
    finally:
        release.set()
        worker.join(timeout=10.0)

    assert not worker.is_alive()
    for snapshot, abandoned in observed:
        assert abandoned is False
        assert snapshot is None or snapshot.outcome in {
            "created",
            "refused",
            "not_dispatched",
            "dispatched_unconfirmed",
        }
    final = progress.latest()
    assert final is not None
    assert final.outcome == "created"
    assert final.issue_number == 42
