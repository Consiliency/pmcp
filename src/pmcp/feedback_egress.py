"""The outbound-action gate and transport behind ``gateway.submit_feedback``.

`submit_feedback` posts agent-authored text to a public GitHub repository. Everything
that decides *whether* that may happen, *where* it would go and *under whose
credential* lives here, and nothing here reads the process environment: the gate is
handed an ``environ`` mapping and the transport is handed a token. That is what makes
the removal of the ambient-credential fallback structural rather than a deletion a
later edit can quietly undo -- see ``## Verification`` in
``plans/phase-plan-v13-EGRESS.md`` (Consiliency/pmcp#230).

Two contracts are worth reading before changing anything in this module.

**The decision order is frozen and every branch fails closed.** Destination resolution
and validation come first, so no unvalidated repository can appear in *any* output --
including a refusal, and including the browser URL an operator is told to open. The
submission flag is reported before the confirmation, so an ordinary first call never
teaches an agent that confirming is what authorises a post. Any exception while
consulting the provenance registries denies as ``gate_error`` rather than propagating:
a caller must never be able to read a raise as permission.

**The dispatch claim is a compare-and-set, not a snapshot.** :class:`FeedbackProgress`
is the arbiter between a worker thread's decision to send and the handler's decision to
give up. Exactly one of :meth:`FeedbackProgress.claim_dispatch` and
:meth:`FeedbackProgress.abandon` wins. Without that, a worker stalled between a
deadline check and a publication lets the handler answer "nothing was sent" and hand
back a compose URL, and then post anyway -- so the operator files the issue twice.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pmcp.env_store import (
    dotenv_sourced_keys,
    managed_secret_keys_strict,
    pmcp_introduced_keys,
)
from pmcp.provision_gate import operator_safe
from pmcp.types import FeedbackSubmissionOutcome

#: Where feedback goes when the operator names nowhere else. The runtime source of
#: truth, shipped in the wheel: a security gate must not acquire "absent or malformed
#: distribution metadata" as a new failure mode in the one branch that decides where an
#: operator's text is sent. `importlib.metadata` is the drift oracle instead, compared
#: against this constant by
#: `test_the_packaged_default_repository_matches_the_distribution_metadata`.
PACKAGED_FEEDBACK_REPOSITORY: str = "Consiliency/pmcp"

_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}")

_REPOSITORY_ENV_VAR = "PMCP_FEEDBACK_REPO"
_TOKEN_ENV_VAR = "PMCP_FEEDBACK_TOKEN"

_USER_STORE_PATH = "~/.config/pmcp/pmcp.env"
_PROJECT_STORE_PATH = ".env.pmcp"

#: Connect + send + response headers, via the socket timeout, and the body read that
#: follows it. **Read the honest claim**: a per-socket timeout is not a request bound.
#: `urlopen(req, timeout=T)` applies `T` to each individual socket operation, so a peer
#: returning one byte every `T-1` seconds keeps the call alive forever; `httpx`'s read
#: timeout restarts per chunk for the same reason. What these budgets buy is that
#: nothing new *starts* without room to finish, and that the one phase the socket
#: timeout leaves open-ended -- the body -- is read in a loop that rechecks the clock
#: and caps the bytes. A single socket operation stalling below its own threshold still
#: cannot be interrupted from Python.
_POST_PHASE_BUDGET_SECONDS = 10.0
_PROBE_PHASE_BUDGET_SECONDS = 5.0

#: The body read stops at whichever comes first. A GitHub issue document is a few KiB.
_MAX_RESPONSE_BYTES = 256 * 1024
_BODY_CHUNK_BYTES = 8192

#: Statuses GitHub's create-issue endpoint returns *instead of* acting. An allowlist,
#: never a denylist, so an unfamiliar status defaults to uncertain: an intermediary's
#: 502 or 504 can arrive after the request was forwarded and the issue created, and the
#: fail-closed direction for that harm is "go and look", never "file it again".
#: 400 is deliberately absent -- rare from this endpoint, and nothing about it rules out
#: an intermediary that had already forwarded the request.
_ESTABLISHING_REFUSAL_STATUSES = frozenset({401, 403, 404, 410, 422})

#: Present only on a response from GitHub's own application layer, which is what
#: separates GitHub's answer from one an intermediary synthesised.
_GITHUB_REQUEST_ID_HEADER = "X-GitHub-Request-Id"

_API_ROOT = "https://api.github.com"
_WEB_ROOT = "https://github.com"


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse redirects: urllib does not strip ``Authorization`` when it follows one."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise HTTPError(newurl, code, "Redirects are not allowed.", headers, fp)


#: The seam the tests replace. Module-level so an autouse guard can fail any test that
#: would reach the live API, as PKGID's `_no_live_npm_registry` does for the registry.
_OPENER = build_opener(_NoRedirectHandler)


FeedbackEgressReason = Literal[
    "untrusted_repository_override",
    "invalid_repository",
    "telemetry_disabled",
    "submission_not_enabled",
    "not_confirmed",
    "untrusted_token",
    "no_feedback_token",
    "gate_error",
    "submit_allowed",
]


@dataclass(frozen=True)
class FeedbackEgressDecision:
    """What the gate decided. ``remedy`` is ``None`` exactly when allowed.

    ``token`` carries the credential to the transport and is ``repr``-suppressed: a
    decision is logged, echoed into error paths and rendered in tests, and none of
    those may disclose it.
    """

    submit_allowed: bool
    reason: FeedbackEgressReason
    remedy: str | None
    repository: str
    token: str | None = field(default=None, repr=False)


def _deny(
    reason: FeedbackEgressReason, remedy: str, repository: str
) -> FeedbackEgressDecision:
    return FeedbackEgressDecision(
        submit_allowed=False, reason=reason, remedy=remedy, repository=repository
    )


def _store_paths_remedy(prefix: str) -> str:
    """A remedy that names the two store files, because no verb removes an entry.

    `pmcp secrets` ships ``set``, ``sync`` and ``check`` only, so a remedy that named a
    removal command would name a verb that does not exist.
    """
    return (
        f"{prefix} Look in {_USER_STORE_PATH} and in the project {_PROJECT_STORE_PATH}, "
        "and export the value in the shell that starts pmcp instead."
    )


_GATE_ERROR_REMEDY = _store_paths_remedy(
    "pmcp could not establish where this variable came from, so it will not submit."
)


def _provenance_predicate(project_root: Path | None) -> Any:
    """``untrusted(name)`` -- did PMCP itself put this variable here, or the operator?

    Three sources, none of which subsumes another. ``dotenv_sourced_keys`` covers a
    checkout's plain ``.env``; ``pmcp_introduced_keys`` covers a plant whose store
    evidence has since vanished, whether written this process or loaded from a previous
    one's, and is durable where a store lookup is not; ``managed_secret_keys_strict``
    covers a store written out of band while this process runs, and is the only one that
    can see a file this process never read.

    The **strict** lookup, deliberately: the lenient one swallows ``OSError`` and
    ``ValueError`` around the project half, which makes "the lookup failed"
    indistinguishable from "the key is not planted" -- and the failure direction there
    is *allow*. Any exception from any of the three reaches rule 9; none is caught here.
    The union is computed at most once per decision, and only when a rule asks.
    """
    cache: set[str] | None = None

    def untrusted(name: str) -> bool:
        nonlocal cache
        if cache is None:
            cache = set(dotenv_sourced_keys())
            cache |= set(pmcp_introduced_keys())
            cache |= set(managed_secret_keys_strict(project_root))
        return name in cache

    return untrusted


def evaluate_feedback_egress(
    *,
    telemetry_enabled: bool,
    submission_enabled: bool,
    confirm_submission: bool,
    environ: Mapping[str, str],
    project_root: Path | None,
) -> FeedbackEgressDecision:
    """The single predicate ``submit_feedback`` consults before any network call.

    Every parameter is keyword-only and none has a default: a bool at a gate boundary
    that a caller may omit acquires the caller's default, and the house style defaults
    to permissive. A lane cannot accidentally call this with submission enabled.

    Rule 9 of the freeze -- any exception denies as ``gate_error`` rather than
    propagating -- is implemented as a guard around the whole decision rather than
    around the three registry calls alone. That is broader than the rule's letter and
    is the same direction: a caller must never read a raise as permission.
    """
    try:
        return _evaluate(
            telemetry_enabled=telemetry_enabled,
            submission_enabled=submission_enabled,
            confirm_submission=confirm_submission,
            environ=environ,
            project_root=project_root,
        )
    except Exception:
        # Built from constants only, so the failure branch itself cannot raise.
        return _deny("gate_error", _GATE_ERROR_REMEDY, PACKAGED_FEEDBACK_REPOSITORY)


def _evaluate(
    *,
    telemetry_enabled: bool,
    submission_enabled: bool,
    confirm_submission: bool,
    environ: Mapping[str, str],
    project_root: Path | None,
) -> FeedbackEgressDecision:
    untrusted = _provenance_predicate(project_root)

    # Rules 1-2. The destination is resolved and validated BEFORE anything else, so no
    # unvalidated value can appear in a refusal or in the browser URL the agent is told
    # to open. Rule 1 precedes rule 2 because the remedies differ: remove it from the
    # file, versus fix its shape.
    repository = PACKAGED_FEEDBACK_REPOSITORY
    override = environ.get(_REPOSITORY_ENV_VAR)
    if override is not None:
        if untrusted(_REPOSITORY_ENV_VAR):
            return _deny(
                "untrusted_repository_override",
                _store_paths_remedy(
                    f"{_REPOSITORY_ENV_VAR}={operator_safe(override)} was introduced by "
                    "pmcp itself, not exported by you, so it is not a destination "
                    "pmcp will send to."
                ),
                repository,
            )
        if not _REPOSITORY_PATTERN.fullmatch(override):
            return _deny(
                "invalid_repository",
                f"{_REPOSITORY_ENV_VAR}={operator_safe(override)} is not owner/repo "
                "shaped; export a value matching owner/repo, or unset it to use "
                f"{PACKAGED_FEEDBACK_REPOSITORY}.",
                repository,
            )
        repository = override

    # Rule 3, before rule 4: an operator who turned telemetry off must not be told to
    # turn on a submission flag that telemetry would override anyway.
    if telemetry_enabled is False:
        return _deny("telemetry_disabled", "pmcp guidance --telemetry on", repository)

    # Rule 4 MUST precede rule 5. Reversed, the ordinary first call is refused with
    # "call again with confirm_submission=true", the agent does exactly that, and it has
    # been taught that confirming is what authorises a post.
    if submission_enabled is False:
        return _deny(
            "submission_not_enabled",
            "pmcp guidance --feedback-submission on",
            repository,
        )

    if confirm_submission is False:
        return _deny(
            "not_confirmed",
            "Show the operator this payload, then call again with "
            "confirm_submission=true.",
            repository,
        )

    # Rule 6. The dedicated variable is the only credential this path knows about; no
    # ambient personal-access-token variable is consulted at any point, which is why
    # `test_the_gate_never_reads_github_token` can assert on the names the gate queried.
    # Before rule 7, so a planted credential is reported rather than silently treated as
    # absent -- otherwise the operator sets a variable that is already set.
    token = environ.get(_TOKEN_ENV_VAR)
    if untrusted(_TOKEN_ENV_VAR):
        return _deny(
            "untrusted_token",
            _store_paths_remedy(
                f"{_TOKEN_ENV_VAR} was introduced by pmcp itself, not exported by you, "
                "so pmcp will not post under it."
            ),
            repository,
        )

    if not token or not token.strip():
        return _deny(
            "no_feedback_token",
            f"Export {_TOKEN_ENV_VAR} in the shell that starts pmcp, or open the "
            "browser URL to submit the issue by hand.",
            repository,
        )

    return FeedbackEgressDecision(
        submit_allowed=True,
        reason="submit_allowed",
        remedy=None,
        repository=repository,
        token=token,
    )


def browser_issue_url(repository: str, title: str, body: str) -> str:
    """Door 4: the compose URL an operator opens to submit the payload by hand.

    Only ever called with a ``repository`` rules 1-2 resolved, which is what keeps an
    attacker-chosen destination out of a URL the agent is told to open.
    """
    query = urlencode({"title": title, "body": body})
    return f"{_WEB_ROOT}/{repository}/issues/new?{query}"


def browser_search_url(repository: str, title: str) -> str:
    """Where to look for an issue that may already exist.

    Handed back instead of a compose URL whenever pmcp cannot establish that nothing
    was created. Offering a compose URL after a possible success is how a duplicate
    gets filed.
    """
    query = urlencode({"q": f"is:issue {title}"})
    return f"{_WEB_ROOT}/{repository}/issues?{query}"


@dataclass(frozen=True)
class FeedbackSubmission:
    """What became of an attempted submission. ``detail`` is a failure *class*.

    Never a raw response body: this is rendered to an operator and recorded, and a
    server-controlled string has no business in either.
    """

    outcome: FeedbackSubmissionOutcome
    issue_url: str | None
    issue_number: int | None
    repository_visibility: Literal["public", "private", "unknown"]
    detail: str


_ProgressState = Literal["pending", "dispatching", "abandoned", "terminal"]


class FeedbackProgress:
    """The arbiter between the worker's decision to send and the handler's to give up.

    ``pending`` -> (``dispatching`` | ``abandoned``) -> a terminal snapshot, all five
    methods under one :class:`threading.Lock`. The lock is not decoration around a
    snapshot slot: :meth:`claim_dispatch` and :meth:`abandon` are two sides of one
    compare-and-set, and the lock is what makes exactly one of them win.

    Snapshots this class authors carry ``issue_url=None`` -- it is constructed with no
    destination and no payload, so it has no URL to name. The handler renders the
    compose or search URL from the outcome; snapshots the transport authors carry the
    URL already.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: _ProgressState = "pending"
        self._latest: FeedbackSubmission | None = None
        self._handler_gave_up = False

    def claim_dispatch(self, deadline: float) -> bool:
        """The worker's permission to send. The LAST step before the opener call.

        Returns ``False`` if the handler already gave up, or if the post could not
        finish inside *deadline*. Checking merely that the deadline has not passed is
        not enough: a POST started one second before it, with a ten-second socket
        timeout, still runs nine seconds past. This is the rule that bounds the *act*.
        """
        with self._lock:
            if self._handler_gave_up or self._state != "pending":
                return False
            if time.monotonic() + _POST_PHASE_BUDGET_SECONDS > deadline:
                self._state = "terminal"
                self._latest = FeedbackSubmission(
                    outcome="not_dispatched",
                    issue_url=None,
                    issue_number=None,
                    repository_visibility="unknown",
                    detail="no_budget_for_post",
                )
                return False
            self._state = "dispatching"
            return True

    def publish(self, submission: FeedbackSubmission) -> None:
        """Record a fact the moment it is known.

        The handler discards the worker's return value (``abandon_on_cancel=True``
        means ``fail_after`` returns while the worker runs on), so a POST that succeeded
        would otherwise be reported as unconfirmed merely because the probe after it
        stalled. Snapshots are immutable and replaced whole, never mutated in place.
        """
        with self._lock:
            self._latest = submission
            if self._state != "abandoned":
                self._state = "terminal"

    def abandon(self) -> FeedbackSubmission:
        """The handler's give-up, and the other side of :meth:`claim_dispatch`.

        Three-way, because a timeout is not evidence that nothing happened: it won the
        claim -> ``not_dispatched``, and a compose URL is safe because the worker is now
        forbidden to send; the worker was already ``dispatching`` ->
        ``dispatched_unconfirmed``, deliberately pessimistic, because telling an
        operator "this may exist, go look" when it does not is a cheap false positive
        while the opposite error files a duplicate; a terminal snapshot was already
        published -> that snapshot, which is what keeps a creation pmcp actually saw.
        """
        with self._lock:
            self._handler_gave_up = True
            if self._state == "terminal" and self._latest is not None:
                return self._latest
            if self._state == "pending":
                self._state = "abandoned"
                snapshot = FeedbackSubmission(
                    outcome="not_dispatched",
                    issue_url=None,
                    issue_number=None,
                    repository_visibility="unknown",
                    detail="handler_won_the_claim",
                )
                self._latest = snapshot
                return snapshot
            return FeedbackSubmission(
                outcome="dispatched_unconfirmed",
                issue_url=None,
                issue_number=None,
                repository_visibility="unknown",
                detail="dispatched_when_the_handler_gave_up",
            )

    def is_abandoned(self) -> bool:
        """Whether the handler has stopped listening, so the probe can be skipped."""
        with self._lock:
            return self._handler_gave_up

    def latest(self) -> FeedbackSubmission | None:
        """The most recent snapshot, or ``None`` while nothing is known."""
        with self._lock:
            return self._latest


def _read_bounded_body(stream: Any, bound: float) -> bytes:
    """Read a response body under a clock and a byte cap.

    This is the phase ``urlopen``'s timeout leaves open-ended -- it bounds each socket
    operation, not the transfer -- and the phase a trickling peer exploits. A body cut
    short here parses as nothing, which classifies as ``dispatched_unconfirmed``: the
    honest answer, since the request did leave.
    """
    chunks: list[bytes] = []
    total = 0
    while total < _MAX_RESPONSE_BYTES:
        if time.monotonic() >= bound:
            break
        chunk = stream.read(min(_BODY_CHUNK_BYTES, _MAX_RESPONSE_BYTES - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _unconfirmed(search_url: str, detail: str) -> FeedbackSubmission:
    return FeedbackSubmission(
        outcome="dispatched_unconfirmed",
        issue_url=search_url,
        issue_number=None,
        repository_visibility="unknown",
        detail=detail,
    )


def _not_dispatched(compose_url: str, detail: str) -> FeedbackSubmission:
    return FeedbackSubmission(
        outcome="not_dispatched",
        issue_url=compose_url,
        issue_number=None,
        repository_visibility="unknown",
        detail=detail,
    )


def _classify_response(status: int, raw: bytes, search_url: str) -> FeedbackSubmission:
    """A complete response that did not raise."""
    if not 200 <= status < 300:
        return _unconfirmed(search_url, f"unestablished_status_{status}")
    try:
        document: Any = json.loads(raw.decode("utf-8"))
    except Exception:
        return _unconfirmed(search_url, "unparseable_body")
    if isinstance(document, dict):
        issue_url = document.get("html_url")
        issue_number = document.get("number")
        if isinstance(issue_url, str) and issue_url and isinstance(issue_number, int):
            return FeedbackSubmission(
                outcome="created",
                issue_url=issue_url,
                issue_number=issue_number,
                repository_visibility="unknown",
                detail="created",
            )
    return _unconfirmed(search_url, "no_issue_in_body")


def _classify_http_error(
    error: HTTPError, compose_url: str, search_url: str
) -> FeedbackSubmission:
    """An ``HTTPError`` *is* a response -- ``.code``, ``.headers``, ``.read()``.

    Only GitHub's own answer establishes that nothing was created: a status on the
    allowlist AND the request-id header, which an intermediary cannot synthesise on
    GitHub's behalf. Everything else is uncertain, and gets the search URL.
    """
    headers = getattr(error, "headers", None)
    request_id = headers.get(_GITHUB_REQUEST_ID_HEADER) if headers is not None else None
    if error.code in _ESTABLISHING_REFUSAL_STATUSES and request_id:
        return FeedbackSubmission(
            outcome="refused",
            issue_url=compose_url,
            issue_number=None,
            repository_visibility="unknown",
            detail=f"github_refused_{error.code}",
        )
    if request_id:
        return _unconfirmed(search_url, f"unestablished_status_{error.code}")
    return _unconfirmed(search_url, f"no_request_id_status_{error.code}")


def _classify_transport_error(
    cause: Any, compose_url: str, search_url: str
) -> FeedbackSubmission:
    """Only DNS failure and a refused connection establish that no bytes were sent.

    A connection reset mid-flight cannot be distinguished from one before the first
    byte, so every other transport error is uncertain.
    """
    if isinstance(cause, socket.gaierror):
        return _not_dispatched(compose_url, "dns_never_resolved")
    if isinstance(cause, ConnectionRefusedError):
        return _not_dispatched(compose_url, "connection_refused")
    return _unconfirmed(search_url, "no_complete_response")


def _probe_repository_visibility(
    repository: str, token: str, deadline: float
) -> Literal["public", "private", "unknown"]:
    """The informational GET, after the consequential POST and only with budget left."""
    request = Request(
        f"{_API_ROOT}/repos/{repository}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    started = time.monotonic()
    bound = min(deadline, started + _PROBE_PHASE_BUDGET_SECONDS)
    try:
        with _OPENER.open(request, timeout=_PROBE_PHASE_BUDGET_SECONDS) as response:
            raw = _read_bounded_body(response, bound)
        document: Any = json.loads(raw.decode("utf-8"))
    except Exception:
        return "unknown"
    if not isinstance(document, dict) or "private" not in document:
        return "unknown"
    return "private" if document.get("private") else "public"


def submit_feedback_issue(
    *,
    repository: str,
    token: str,
    title: str,
    body: str,
    labels: Sequence[str],
    deadline: float,
    progress: FeedbackProgress,
) -> FeedbackSubmission:
    """Post the issue, then -- with budget left -- ask how visible the repository is.

    **Synchronous and blocking; the only code in this phase that opens a socket, and it
    must be called only from a worker thread.** *deadline* is a ``time.monotonic()``
    value, not a duration. It reads no environment variable: its authority is the
    ``token`` argument.
    """
    compose_url = browser_issue_url(repository, title, body)
    search_url = browser_search_url(repository, title)
    payload = json.dumps({"title": title, "body": body, "labels": list(labels)}).encode(
        "utf-8"
    )
    request = Request(
        f"{_API_ROOT}/repos/{repository}/issues",
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    # The claim is the last thing before the opener, and its result gates the dispatch.
    # Everything above this line is local work; nothing above it can send a byte.
    if not progress.claim_dispatch(deadline):
        lost = progress.latest()
        detail = lost.detail if lost is not None else "claim_refused"
        if lost is not None and lost.outcome == "dispatched_unconfirmed":
            return _unconfirmed(search_url, detail)
        return _not_dispatched(compose_url, detail)

    post_started = time.monotonic()
    try:
        with _OPENER.open(request, timeout=_POST_PHASE_BUDGET_SECONDS) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            raw = _read_bounded_body(
                response, min(deadline, post_started + _POST_PHASE_BUDGET_SECONDS)
            )
        submission = _classify_response(int(status), raw, search_url)
    except HTTPError as error:
        submission = _classify_http_error(error, compose_url, search_url)
    except URLError as error:
        submission = _classify_transport_error(error.reason, compose_url, search_url)
    except (socket.gaierror, ConnectionRefusedError) as error:
        submission = _classify_transport_error(error, compose_url, search_url)
    except Exception:
        submission = _unconfirmed(search_url, "no_complete_response")

    # Published the instant the response is classified, BEFORE the probe starts, so a
    # probe that stalls can no longer cost pmcp the report of a successful creation.
    progress.publish(submission)

    if submission.outcome != "created":
        return submission
    if progress.is_abandoned():
        return submission
    if time.monotonic() + _PROBE_PHASE_BUDGET_SECONDS > deadline:
        return submission

    visibility = _probe_repository_visibility(repository, token, deadline)
    if visibility == submission.repository_visibility:
        return submission
    submission = replace(submission, repository_visibility=visibility)
    progress.publish(submission)
    return submission
