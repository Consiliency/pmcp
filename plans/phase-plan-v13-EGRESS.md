---
phase_loop_plan_version: 1
phase: EGRESS
roadmap: specs/phase-plans-v13.md
roadmap_sha256: c1ba7f853a92d4368f25e26feeb20db0b5dbfa6829d24636832853bfaf189ae7
---

# PHASE-4-EGRESS: Outbound actions need explicit authority

## Context

EGRESS closes S-04: `gateway.submit_feedback` posts agent-authored text to a public
GitHub repository under whatever identity the host happens to be carrying. Every line
below was re-verified against this worktree's `HEAD` (`5c7e0a1`). **The roadmap's
citation `tools/handlers.py:4796-4992` is stale**: `submit_feedback` is
`handlers.py:4995-5191`, and `:4796-4992` is now the tail of `auth_connect`. A lane
that trusted the roadmap would edit the wrong function.

**Every egress door in this handler, enumerated rather than assumed.** PKGID
amendment 1's lesson is that a gate belongs in front of *what executes*, and that
"install" named one function rather than the set of things that fetch and run code.
The same applies here: "the network call" names one `urlopen`, and there are four
doors.

| # | Site | What it reaches | Credential it uses today |
|---|---|---|---|
| 1 | `handlers.py:5059` `urlopen(repo_req, timeout=5)` | `GET api.github.com/repos/{repository}` — the visibility probe | `PMCP_FEEDBACK_TOKEN` **or** `GITHUB_TOKEN` (`:5043`) |
| 2 | `handlers.py:5089` `urlopen(req, timeout=10)` | `POST api.github.com/repos/{repository}/issues` — the post | same |
| 3 | `handlers.py:5123` `shutil.which("gh")` → `handlers.py:5139` `asyncio.create_subprocess_exec` | `gh issue create --repo {repository}` | **`gh`'s own stored credentials, and an ambient `GITHUB_TOKEN`/`GH_TOKEN` in the inherited environment** |
| 4 | `handlers.py:5175` `urlencode` browser URL | no request; a URL handed to the agent | none — but it names the destination |

Doors 1-3 are all reached from one agent tool call with `confirm_submission=true`.
**Door 3 is the one the roadmap's exit criteria do not name, and it is the one that
survives EC-EGRESS-1 as written**: deleting `GITHUB_TOKEN` from the token lookup at
`:5043` merely routes an operator with an ambient `GITHUB_TOKEN` past doors 1-2 and
into door 3, where `gh` reads that same variable from the inherited environment and
posts anyway. Door 4 is not a request, but it renders `repository` into a URL the
agent is told to open, so an attacker-chosen destination there is still a disclosure.

Elsewhere in the file the only outbound sites are `handlers.py:3769`
(`update_server`'s probe spawn, gated and logged by PKGID) and the downstream MCP
transport inside `ClientManager`. `grep -n 'os.environ\['` finds exactly one write in
this file, `handlers.py:4972` in `auth_connect` — see "Two inputs the agent can
reach" below. Nothing else in `handlers.py` opens a socket or spawns a process from
agent input. **Out of scope, stated:** the redaction quality of
`_build_feedback_issue` (`:3855-3914`) is S-12/#234, not this phase; PKGID's gated
spawn sites are PKGID's; `sanitized_subprocess_env` is unchanged.

**Two inputs the agent can reach, which the roadmap's criteria do not cover.**

1. **`auth_connect` writes the gateway's own `os.environ`** (`handlers.py:4972`), and
   `env_var_allowed` (`validation.py:176-188`) admits any *credential-shaped* name
   when a server declares none — the OPEN residual recorded in PKGID amendment 9.
   `PMCP_FEEDBACK_TOKEN` matches `_CREDENTIAL_NAME_RE` (`validation.py:162-165`:
   `<prefix>_TOKEN`). So an agent can plant the very variable EC-EGRESS-1 makes
   authoritative. `PMCP_FEEDBACK_REPO` does **not** match, so the destination is not
   reachable this way.
2. **A checkout's dotenv files reach the gateway's environment.**
   `cli.load_startup_env` does `load_dotenv(dotenv_path)` (`cli.py:2926`, a bare
   `find_dotenv` walk) and `load_dotenv(Path.cwd()/".env.pmcp")` (`:2930`); at runtime
   `_check_env_var_available` loads `Path.cwd()/".env"`, `.env.pmcp` and the user
   store (`handlers.py:3010-3018`). `load_dotenv` defaults to `override=False`, so a
   shell export wins — but with nothing exported, a repository supplies both
   `PMCP_FEEDBACK_TOKEN` and `PMCP_FEEDBACK_REPO`.

Both are closed by one provenance rule in the freeze, using registries that already
exist: `env_store.dotenv_sourced_keys()` (`env_store.py:140`, the #229 provenance
registry) and `env_store.managed_secret_keys()` (`:157`, the keys PMCP's own user and
project credential stores hold — which is exactly what `auth_connect` writes through
`set_env_value`).

**Three facts that shape the design, each read rather than assumed.**

1. **`enable_feedback_submission` does not exist.** `GuidanceConfig`
   (`config/guidance.py:36-69`) has `enable_telemetry: bool = True`;
   `_telemetry_enabled` (`handlers.py:3798-3802`) returns **True** when
   `self._guidance_config is None`. The new flag must default off *and* read off when
   the config object is absent — the opposite asymmetry, decided at the handler.
2. **The default repository is wrong today.** `handlers.py:4998` reads
   `os.environ.get("PMCP_FEEDBACK_REPO", "ViperJuice/pmcp")`; `git remote -v` and
   `pyproject.toml:105-108` both say `Consiliency/pmcp`. Today's default posts the
   operator's failure text, audit events and platform details to a third party's
   public repository.
3. **The blocking calls are inside `async def submit_feedback`** (`:5059`, `:5089`),
   with per-socket timeouts of 5 s and 10 s. This file already has the shape that
   fixes it: `anyio.to_thread.run_sync(..., abandon_on_cancel=True)` under
   `anyio.fail_after(_REGISTRATION_RESOLVE_TIMEOUT_SECONDS)` (`handlers.py:204`,
   `:5780-5783`), added because a socket timeout is not a request bound.

**Consumed from merged phases.** `operator_safe` (`src/pmcp/provision_gate.py:99`,
PKGID SL-1) renders an untrusted value inert for an operator's terminal; EGRESS
imports it rather than restating it a third time. TRUST's store is not used: this
phase records no approvals.

## Interface Freeze Gates

- [ ] IF-0-EGRESS-1 — the outbound-action gate, `src/pmcp/feedback_egress.py`:
  `PACKAGED_FEEDBACK_REPOSITORY: str = "Consiliency/pmcp"`;
  `FeedbackEgressReason = Literal["untrusted_repository_override", "invalid_repository", "telemetry_disabled", "submission_not_enabled", "not_confirmed", "untrusted_token", "no_feedback_token", "gate_error", "submit_allowed"]`;
  `FeedbackEgressDecision(submit_allowed: bool, reason: FeedbackEgressReason, remedy: str | None, repository: str, token: str | None = field(default=None, repr=False))` (frozen dataclass);
  `evaluate_feedback_egress(*, telemetry_enabled: bool, submission_enabled: bool, confirm_submission: bool, environ: Mapping[str, str], project_root: Path | None) -> FeedbackEgressDecision`;
  `browser_issue_url(repository: str, title: str, body: str) -> str`;
  `browser_search_url(repository: str, title: str) -> str` — where to look for an issue that may already exist;
  `FeedbackSubmission(outcome: FeedbackSubmissionOutcome, issue_url: str | None, issue_number: int | None, repository_visibility: Literal["public", "private", "unknown"], detail: str)` (frozen dataclass; `detail` is a failure *class*, never a raw response body);
  `FeedbackProgress` — the **arbiter** between the worker's decision to send and the
  handler's decision to give up: `claim_dispatch(deadline: float) -> bool` (worker),
  `publish(submission: FeedbackSubmission) -> None` (worker),
  `abandon() -> FeedbackSubmission` (handler, on timeout),
  `is_abandoned() -> bool`, `latest() -> FeedbackSubmission | None`. All five take one
  `threading.Lock` and operate on a small state machine: `pending` →
  (`dispatching` | `abandoned`) → a terminal `FeedbackSubmission` snapshot;
  `submit_feedback_issue(*, repository: str, token: str, title: str, body: str, labels: Sequence[str], deadline: float, progress: FeedbackProgress) -> FeedbackSubmission` — **synchronous and blocking; it is the only code in this phase that opens a socket, and it must be called only from a worker thread** (see SL-4). `deadline` is a `time.monotonic()` value, not a duration.

  `FeedbackSubmissionOutcome = Literal["created", "refused", "not_dispatched", "dispatched_unconfirmed"]`
  lives in **`src/pmcp/types.py`**, not in `feedback_egress.py`, and `feedback_egress`
  imports it. `types.py` has to name the alias anyway to type
  `SubmitFeedbackOutput.submission_outcome`, and SL-1 publishes `types.py` before SL-2
  exists; defining it in the later module would either invert the dependency or duplicate
  the Literal in two files, which is precisely the two-registries-drifting defect
  `scripts/check_plan_consistency.py` was written for. An inline Literal on the field was
  rejected for the same reason.

  It also fixes four names in `src/pmcp/env_store.py`, because the gate's provenance
  evidence has to be durable and its failures have to be visible:
  `record_pmcp_introduced_keys(keys: Iterable[str]) -> None` and
  `pmcp_introduced_keys() -> frozenset[str]` — the process-global, **additive** record
  of every environment key PMCP itself introduced into its own `os.environ`, **by any
  route**: a runtime write (`auth_connect`, `handlers.py:4972`) or a load of one of
  PMCP's own credential-store files (`cli.py:2929-2930`). Shaped exactly like
  `record_dotenv_keys`/`dotenv_sourced_keys` (`env_store.py:112-142`) so the module has
  one idiom, not two; `reset_pmcp_introduced_keys() -> None`, a **test-only seam** with
  no production caller, mirroring `reset_dotenv_keys` (`:145-154`); and
  `managed_secret_keys_strict(project: Path | None = None) -> set[str]`, which does what
  `managed_secret_keys` does **without** its `except (OSError, ValueError): pass`
  around the project lookup (`:167-170`).

  **Why a durable record rather than a store lookup.** `auth_connect` writes the
  credential store and *then* `os.environ[env_var]` (`handlers.py:4953` →
  `_write_secret` → `set_env_value`, then `:4972`; verified store-before-environ, so
  there is no ordering window). But membership in the store is not durable evidence.
  `set_env_value` is a read-modify-write (`env_store.py:215-218`) over `read_env_file`,
  which returns `{}` for a file it cannot read (`:47-48`) — so **any** `auth_connect`
  call, for any unrelated server, can rewrite the store without an earlier key. An
  operator deleting the file does the same. **A check whose evidence can disappear
  while the thing it proves persists is not a check**, and the disappearance is
  reachable through a tool the agent calls.

  **Why the record must cover LOADS, not only writes — the second panel's Q1.** The
  first revision recorded only runtime writes and left the startup store loads for SEAL,
  on the reasoning that removing a store entry is an operator action. That reasoning was
  refuted by this plan's own finding above. The full chain: a *previous* process's
  `auth_connect` put `PMCP_FEEDBACK_TOKEN` in the store; this process's
  `cli.py:2929-2930` loads it into `os.environ` recording nothing; the agent then calls
  `auth_connect` for some unrelated server, whose read-modify-write drops the old entry;
  and now `dotenv_sourced_keys()` never saw it, a write-only registry never wrote it,
  and `managed_secret_keys_strict()` no longer finds it — all three say *operator
  supplied*, and EC-EGRESS-1 is unmet. Recording the load closes it, which is why the
  registry is named for what it means (**PMCP introduced this**) rather than for one
  mechanism, and why SL-3 owns the `cli.py` half. The runtime loads at
  `handlers.py:3010-3018` need no change: they already measure and record their delta
  through `record_dotenv_keys` (`:3016-3018`), which the gate also reads.

  No gateway tool can clear the registry — `reset_pmcp_introduced_keys` has no
  production caller and is not reachable from `server.py`'s dispatch of the 26 tools.
  `## Verification` asserts that structurally.

  **Why a strict lookup.** `managed_secret_keys`'s suppressed project lookup makes "the
  lookup failed" indistinguishable from "the key is not planted" — the failure direction
  is *allow*, and rule 9 could never fire for it. The lenient function keeps its existing
  caller (`sanitized_subprocess_env`, `:198`), where swallowing is correct because a
  failed strip is a smaller harm than a crashed spawn. The gate gets the strict variant.
  Note that the *user* lookup is already unguarded (`:166`), so rule 9 is reachable for
  it today; only the project half fails open.

  **Every parameter is keyword-only and none has a default.** PKGID's IF-0-PKGID-1
  rationale applies verbatim: a bool at a gate boundary that a caller may omit
  acquires the caller's default, and the house style (`is_server_allowed`,
  `policy.py:161-175`) defaults to permissive. A lane cannot accidentally call this
  with submission enabled.

  **Decision order. Every branch fails closed; the first match returns.**
  **`untrusted(name)` — the provenance predicate rules 1 and 6 share.** A name is
  untrusted when it appears in **any** of `dotenv_sourced_keys()` (keys PMCP loaded
  from a plain dotenv file — #229's recorded delta, plus the runtime loads at
  `handlers.py:3016-3018`), `pmcp_introduced_keys()` (keys PMCP put in its own
  environment by a runtime write or by loading its own credential store — durable, and
  the registry that covers every agent-reachable route), or
  `managed_secret_keys_strict(project_root)` (keys PMCP's own user and project stores
  hold *now*). All three are needed and none subsumes another: the first covers a
  checkout's `.env`; the second covers a plant whose store evidence has since vanished,
  whether it was written this process or loaded from a previous one's; the third covers
  a store written out of band while this process runs, and is the only one that can see
  a file this process never read. **Any exception from any of the three propagates to
  rule 9**; none is caught here.

  1. **Destination, from `environ` only.** `PMCP_FEEDBACK_REPO` present and
     `untrusted("PMCP_FEEDBACK_REPO")` → deny,
     `untrusted_repository_override`, `repository = PACKAGED_FEEDBACK_REPOSITORY`.
  2. `PMCP_FEEDBACK_REPO` present, trusted, but not matching `^[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}$`
     → deny, `invalid_repository`, `repository = PACKAGED_FEEDBACK_REPOSITORY`.
     Otherwise `repository` is the override, or `PACKAGED_FEEDBACK_REPOSITORY` when
     unset. **`repository` is never `None` and is never an unvalidated string.**
     **Rules 1-2 MUST precede every other rule.** Today's telemetry-disabled refusal
     echoes the raw environment value straight into `SubmitFeedbackOutput.repository`
     (`handlers.py:4998`, `:5009`) without ever validating it, and rule 5 would return
     before the destination was examined. Resolving first is what guarantees that no
     unvalidated destination appears in *any* output — including a refusal, and
     including the browser URL of door 4, which the agent is told to open. Rule 1
     precedes rule 2 because the remedies differ: remove it from the file, versus fix
     its shape.
  3. `telemetry_enabled is False` → deny, `telemetry_disabled`. Before rule 4 because
     it is the coarser, pre-existing switch: an operator who turned telemetry off must
     not be told to turn on a submission flag that telemetry would override anyway.
  4. `submission_enabled is False` → deny, `submission_not_enabled`. **This rule MUST
     precede rule 5.** With the order reversed, the ordinary first call
     (`confirm_submission=false`, flag off) refuses with "call again with
     `confirm_submission=true`" — the agent does exactly that, is refused again for a
     different reason, and, worse, has been told that confirming is what authorises a
     post. That belief is precisely what EC-EGRESS-2 exists to destroy. PKGID item 3
     is the precedent: a frozen order that placed a deny after an earlier-returning
     rule shipped in the plan and was caught only because a lane read the rationale.
  5. `confirm_submission is False` → deny, `not_confirmed`.
  6. `token = environ.get("PMCP_FEEDBACK_TOKEN")`. **`GITHUB_TOKEN` is not read at any
     point in this function, and neither is `GH_TOKEN`.** If
     `untrusted("PMCP_FEEDBACK_TOKEN")` → deny, `untrusted_token`. Before rule 7 so a
     planted credential is reported rather than silently treated as absent; the operator
     otherwise sets a variable that is already set.
  7. `token` missing or blank → deny, `no_feedback_token`.
  8. Otherwise allow: `submit_allowed=True`, `reason="submit_allowed"`, `remedy=None`,
     `token` populated.
  9. **Any** exception raised while consulting the three provenance sources (two touch
     disk — `managed_secret_keys_strict` reads both store files per call) → deny,
     `gate_error`, never propagate. A caller must never be able to read a raise as
     permission. This rule is only real because rules 1 and 6 use the **strict** lookup:
     with the lenient one, a project-store read that raised was swallowed into an empty
     set and the gate carried on as if nothing were planted.

  `remedy` is `None` only when allowed, and otherwise **depends on the reason**; a
  single frozen string is unconstructible across nine branches. `submission_not_enabled`
  → `pmcp guidance --feedback-submission on`. `telemetry_disabled` →
  `pmcp guidance --telemetry on`. `no_feedback_token` → export `PMCP_FEEDBACK_TOKEN`,
  or open the browser URL. `untrusted_token` / `untrusted_repository_override` → name
  the two store paths (`~/.config/pmcp/pmcp.env`, the project `.env.pmcp`) and say to
  export the value in the shell that starts pmcp instead. **No remedy may name a verb
  that does not exist**: there is no `pmcp secrets rm` (`cli.py:615-660` ships
  `set`, `sync`, `check` only), so these remedies name paths, not a removal command.
  Every remedy that embeds a value passes it through `operator_safe`.

  **The `managed_secret_keys_strict()` source is a name-collision check, not
  provenance**, and it over-refuses by design: an operator who exported
  `PMCP_FEEDBACK_TOKEN` in their shell *and* also has that key in PMCP's store is
  refused, because the two cannot be told apart after `load_dotenv(override=False)`
  has run. That is the fail-closed direction, and the remedy names both fixes. The
  other two sources are true provenance: `dotenv_sourced_keys()` records the delta #229
  measured, and `pmcp_introduced_keys()` records the write or the load as it happens.

  **Known limitation, recorded rather than papered over.** A credential-store file that
  *this* process never read and that is deleted before the gate looks — a store written
  and removed entirely out of band, by another process, between this process's startup
  and the call — is invisible to all three sources. Nothing PMCP does can observe an
  environment variable's origin after the fact; the three registries work because PMCP
  is present at every moment it introduces one itself. This residual requires an
  out-of-band writer on the same machine and is not reachable from any gateway tool.
  Recorded for SEAL.

  **The preview payload shape, named against `SubmitFeedbackOutput`
  (`types.py:1310-1323`). This phase adds no field to `SubmitFeedbackInput`, no gateway
  tool, and no change to the tool count of 26 (`tests/test_baseline_constraints.py:84`).
  It adds exactly ONE optional output field**, `submission_outcome: FeedbackSubmissionOutcome | None = None`
  — see the `dispatched_unconfirmed` row for why `submitted: bool` alone is a lie there.
  It defaults to `None`, so every existing construction and every existing assertion is
  untouched. **Correction to this plan's first revision, which said "no field to
  `types.py`":** that commitment was cheap to keep and wrong to keep, because keeping it
  forced the gateway to report a certain negative it does not have.

  | reason / outcome | `ok` | `submitted` | `submission_outcome` | payload built? | event recorded? | `issue_url` | `repository_visibility` |
  |---|---|---|---|---|---|---|---|
  | `untrusted_repository_override`, `invalid_repository`, `telemetry_disabled`, `untrusted_token`, `gate_error` | `False` | `False` | `None` | **no** — `issue_title=parsed.title`, `issue_body=parsed.description`, as today's telemetry branch (`:5011-5012`) | **no** | `None` | `"unknown"` |
  | `submission_not_enabled`, `not_confirmed`, `no_feedback_token` | `True` | `False` | `None` | yes, `_build_feedback_issue` | yes, `feedback_prepare` | `browser_issue_url(...)` | `"unknown"` |
  | `submit_allowed` → `created` | `True` | `True` | `"created"` | yes | yes, `feedback_submitted` | the created issue's `html_url` | from the post-POST probe, else `"unknown"` |
  | `submit_allowed` → `refused` (GitHub itself answered with an establishing status: 401/403/404/410/422 **with** `X-GitHub-Request-Id`) | `False` | `False` | `"refused"` | yes | **no** | `browser_issue_url(...)` | `"unknown"` |
  | `submit_allowed` → `not_dispatched` (**established**: the claim was refused by the deadline or lost to the handler, or DNS/connection-refused proves no bytes were sent) | `False` | `False` | `"not_dispatched"` | yes | **no** | `browser_issue_url(...)` | `"unknown"` |
  | `submit_allowed` → `dispatched_unconfirmed` (bytes may have been sent and nothing establishes otherwise: no complete response, a 5xx or any unlisted status, a missing request-id header, an unparseable body, or the handler's timeout path when `abandon()` finds the state `dispatching`) | `False` | `False` | `"dispatched_unconfirmed"` | yes | **no** | `browser_search_url(...)` — **where to look, never a compose URL** | `"unknown"` |
  | **handler timed out, but `progress.latest()` says `created`** | `True` | `True` | `"created"` | yes | yes, `feedback_submitted` | the created issue's `html_url` | `"unknown"` — the probe never finished |

  **The `dispatched_unconfirmed` row is the one that must not be collapsed into the
  others.** The gateway cannot know whether the issue exists: the POST was sent and the
  response was lost. `submitted=False` here means "pmcp did not observe a submission",
  and that is exactly why `submission_outcome` exists and why `issue_url` is a *search*
  URL: handing the operator a compose URL after a possible success is how a duplicate
  gets filed, and it is the failure mode of pretending an unknown is a no. The message
  must say the issue may already exist and name where to look. A `refused` outcome is a
  genuine negative — the server answered — and keeps `submitted=False` honestly.
  **The handler's timeout path is three-way, not two-way**, and calls
  `progress.abandon()` — never `latest()` — to decide which: it won the claim →
  `not_dispatched`, and the compose URL is safe because the worker is now forbidden to
  send; the state was `dispatching` → `dispatched_unconfirmed` with the search URL; a
  terminal `created` snapshot was already published → the last row, reporting the issue
  pmcp actually saw created, with `"unknown"` visibility because only the probe was lost.
  A timeout is not evidence that nothing happened, and `latest()` alone cannot tell these
  apart without racing the worker.

  `warning` is today's constant, unchanged. `authenticated` is `True` on **both** rows
  that report a created issue — the normal success and the timed-out-but-observed one —
  and `False` everywhere else; it describes the credential the post used, not whether the
  handler waited for the probe. `repository` is always the rule-1/2 result. `message` is rendered from
  `reason` + `remedy` and is always `operator_safe` in every value it interpolates.
  **`repository_visibility` stops being `"public"` on paths that made no request**
  (today's preview hardcodes it, `:5033`): a claim about a repository we did not ask
  about is a claim the tests cannot prove. The `submission_not_enabled` message must
  still contain the word "consent" and the `telemetry_disabled` message the word
  "disabled", because `tests/test_tools.py:4448` and `:4469` assert exactly those.

  **A failed submission opens no second door.** There is no fallback: the `gh` path is
  deleted, so a POST that fails renders its outcome row and stops.

  **The deadline and progress contract. Read the honest claim first: a per-socket
  timeout is not a request bound, and neither the standard library nor `httpx` offers
  one.** `urlopen(req, timeout=T)` sets `T` on the socket, so it bounds each individual
  connect / send / recv operation and **not** the request: a peer that returns one byte
  every `T-1` seconds keeps the call alive forever, and `httpx`'s `read` timeout behaves
  the same way because it too restarts per chunk. The first revision claimed the socket
  timeouts "summing under the handler bound" bounded the request. They do not, and the
  plan says so rather than shipping a guarantee it cannot keep. What this phase *does*
  guarantee, and what each mechanism actually buys:

  1. **Nothing new starts after the deadline, and the dispatch decision is the same
     atomic step as the claim.** `submit_feedback_issue` takes a monotonic `deadline` and
     calls `progress.claim_dispatch(deadline)` as the **last thing before the opener**.
     Under the lock, that call: returns `False` if the state is not `pending` (the
     handler already gave up); returns `False` and records a `not_dispatched` snapshot if
     `time.monotonic() + _POST_PHASE_BUDGET_SECONDS > deadline`; otherwise moves
     `pending → dispatching` and returns `True`. **The worker dispatches if and only if
     it returns `True`.** Checking merely that the deadline has not passed is not enough:
     a POST started one second before it with a ten-second socket timeout still runs nine
     seconds past. This is the rule that bounds the **act**.

     **A lock that merely guards a snapshot slot does NOT give you this**, and the third
     plan panel's R1 is the proof. With a separate deadline check, then a `publish`, then
     the opener call, a worker stalled *between* the check and the publish leaves the
     handler reading an empty slot: it answers `not_dispatched` with a **compose** URL,
     the worker then resumes and POSTs, and the operator files the issue a second time
     from that URL — the exact duplicate the `dispatched_unconfirmed` row exists to
     prevent. Publishing before the opener does not fix it, because nothing stops a
     worker that has already been overtaken. The check, the state transition and the
     permission to send must be one indivisible step, and the handler's give-up must be
     the same step seen from the other side: `abandon()` moves `pending → abandoned` and
     returns a `not_dispatched` snapshot **only if it won**; if the state is already
     `dispatching` it returns `dispatched_unconfirmed`, and if a terminal snapshot is
     present it returns that. Exactly one of the two sides wins, always.
     `is_abandoned()` lets the worker skip the visibility probe once nobody is listening.
  2. **Per-phase budgets, which narrow the unbounded window rather than closing it.**
     `_POST_PHASE_BUDGET_SECONDS = 10.0` covers connect + send + response headers via
     `urlopen`'s socket timeout; the response **body** is then read in a bounded loop
     that re-checks `time.monotonic()` against the deadline and caps the bytes it will
     accept, because that is the phase `urlopen` leaves open-ended and the phase a
     trickling peer exploits. `_PROBE_PHASE_BUDGET_SECONDS = 5.0` covers the probe the
     same way. `_POST_PHASE_BUDGET_SECONDS + _PROBE_PHASE_BUDGET_SECONDS = 15.0 <
     _FEEDBACK_SUBMIT_TIMEOUT_SECONDS = 20.0`, asserted by
     `test_the_phase_budgets_sum_below_the_handler_bound` so a later edit to one
     constant cannot break the invariant silently. **Residual, stated:** a single socket
     operation that stalls below its own timeout threshold cannot be interrupted from
     Python, so an abandoned worker can outlive the handler indefinitely. It holds one
     of anyio's default 40 thread-limiter tokens. This phase does not add a
     concurrency guard for it; SEAL should decide whether repeated abandonment needs one.
  3. **Every fact is published the moment it is known, because the handler discards the
     worker's return value.** `abandon_on_cancel=True` means `fail_after` returns while
     the worker runs on, so a POST that **succeeded** — response read, issue number in
     hand — would be reported as unconfirmed merely because the probe after it stalled.
     That loses a fact pmcp had. Winning `claim_dispatch` is itself the first
     publication: the state is `dispatching`, which `abandon()` reads as
     `dispatched_unconfirmed`. The worker then publishes `not_dispatched` if the opener
     raises an error that **establishes** no bytes were sent (`socket.gaierror`,
     `ConnectionRefusedError` — DNS never resolved, or the peer refused the connection);
     then `created`, `refused` or `dispatched_unconfirmed` the instant the response is
     classified — **before the probe starts** — and once more with the visibility filled
     in if the probe completes. Treating `dispatching` as "may have been sent" is
     deliberately pessimistic: telling an operator "this may exist, go look" when it does
     not is a cheap false positive, while the opposite error files a duplicate. That
     asymmetry is the whole reason this record exists. Any other `URLError` is
     `dispatched_unconfirmed`, because a connection reset mid-flight cannot be
     distinguished from one before the first byte.
  4. **The visibility probe stays, and moves to AFTER a `created` POST**, running only
     with budget left (`time.monotonic() + _PROBE_PHASE_BUDGET_SECONDS <= deadline`),
     else `"unknown"`. It never gated anything — today it is computed and reported
     alongside an already-successful post (`:5048-5064`) — so running it first only
     spent the budget of the act the operator authorised, and put a 5 s informational
     call in front of a 10 s consequential one. The second panel asked whether it should
     run in the same bounded call at all: it can, **because rule 3 commits the created
     result before the probe begins**, so the probe can no longer cost pmcp the report of
     a successful creation. Deleting it outright would make an existing output field
     permanently dead, which this phase has no criterion for.

  **Which failures ESTABLISH that nothing was created — the third panel's R2.** The
  first revision mapped every error status to `refused` and handed back a compose URL.
  That is wrong for exactly the statuses an intermediary emits: a 502 or 504 can arrive
  after the request was forwarded and the issue created, and a 500 can follow a commit.
  `refused` is therefore an **allowlist**, and its members are the statuses GitHub's
  create-issue endpoint returns *instead of* acting:

  | status | why it establishes rejection |
  |---|---|
  | 401 | the credential failed authentication; the request never reached issue creation |
  | 403 | authenticated but not permitted — also GitHub's rate-limit and abuse response; the endpoint refuses before acting |
  | 404 | the repository is not visible to this token (GitHub answers 404 rather than 403 so as not to disclose private repositories); nothing is created |
  | 410 | issues are disabled on the repository; nothing is created |
  | 422 | validation failed — by the definition of the status, the resource was not created |

  **and only when the response carries `X-GitHub-Request-Id`**, which is what
  distinguishes an answer from the API itself from one an intermediary synthesised. Every
  other status, a missing request-id header, an unparseable body, and a 2xx whose body
  does not yield an issue are all `dispatched_unconfirmed` with the **search** URL. The
  list is an allowlist and not a denylist precisely so that an unfamiliar status defaults
  to uncertain — the fail-closed direction for this harm is "go and look", never "file it
  again". 400 is deliberately **not** on the list: it is rare from this endpoint, and
  nothing about it rules out an intermediary that had already forwarded the request.
  `urlopen` raises `HTTPError` for a non-2xx, and an `HTTPError` **is** a response —
  `.code`, `.headers` and `.read()` — which is where the classifier reads both the status
  and the header.

  **Thread-safety across the boundary.** `FeedbackProgress` guards a single
  state word plus one `FeedbackSubmission | None` slot — immutable snapshots, replaced
  whole, never mutated in place — with a `threading.Lock`. The lock **does** arbitrate
  contention here, which is the correction R1 forced: `claim_dispatch` and `abandon`
  are two sides of one compare-and-set, so the lock is what makes exactly one of them
  win. Each call acquires it for a state word read and a slot assignment, a few
  microseconds, which is a memory barrier rather than I/O and is not the loop-blocking
  P-03 forbids. `test_the_progress_record_is_safe_to_read_while_the_worker_runs` drives a
  real concurrent read against a running worker, and
  `test_the_handler_may_report_not_dispatched_only_if_it_won_the_claim` drives both sides
  against each other.

## Lane Index & Dependencies

SL-1 — Provenance registry and output contract (preamble)
  Depends on: (none)
  Blocks: SL-2, SL-3, SL-4
  Parallel-safe: yes

SL-2 — Outbound-action gate and transport
  Depends on: SL-1
  Blocks: SL-4
  Parallel-safe: yes

SL-3 — Operator submission flag, CLI verb, and startup-load recording
  Depends on: SL-1
  Blocks: SL-4
  Parallel-safe: yes

SL-4 — `submit_feedback` rewiring
  Depends on: SL-1, SL-2, SL-3
  Blocks: SL-5
  Parallel-safe: no (sole writer of `handlers.py`)

SL-5 — Documentation & spec reconciliation (the `SL-docs` lane below; PKGID's plan
  indexes its docs lane the same way, and its task ids are `SL-docs.N`)
  Depends on: SL-4
  Blocks: (none)
  Parallel-safe: no (terminal)

**Why this decomposition, and not the roadmap's two.** The roadmap suggests lane A
(credential and consent gate) and lane B (repository default plus HTTP off the loop),
and warns that both write one region of `handlers.py` and must be serialised. That
split is not file-disjoint at all — *both* halves of it are edits to the same forty
lines of one function, so the two lanes would be one lane with a merge conflict in the
middle. Everything that is **not** `handlers.py` goes in front instead, and SL-4 makes
one pass over the single-writer region with every interface already on disk and already
tested. The roadmap's two concerns are not lost; they are both SL-4's, because they are
the same edit.

**Why a preamble lane, revised after the second plan panel.** The first revision had
SL-1 (gate) and SL-2 (flag) as two roots and kept them that way by *declining* to record
the credential-store loads in `cli.py` — a correctness decision made to preserve a lane
shape. That was backwards, and Q1 below is the hole it left. `src/pmcp/env_store.py` and
`src/pmcp/types.py` now form their own root lane that publishes the provenance API and
the one new output field on day one; the gate lane, the flag/CLI lane and the handler
lane then all consume a frozen interface. Two lanes still run concurrently (SL-2 ∥ SL-3),
the dependency that forced the question is now explicit rather than avoided, and this is
the shape PKGID's Execution Notes wished for when they recorded "there is no SL-0
preamble lane".

## Lanes

### SL-1 — Provenance registry and output contract (preamble)

- **Scope**: The durable record of which environment variables PMCP itself put in its own process, the strict store lookup the gate needs, and the one optional output field that lets the gateway report an outcome it is unsure of. Published before anything consumes it.
- **Owned files**: `src/pmcp/env_store.py`, `src/pmcp/types.py`, `tests/test_feedback_provenance.py`
- **Interfaces provided**: `record_pmcp_introduced_keys(keys: Iterable[str]) -> None`, `pmcp_introduced_keys() -> frozenset[str]`, `reset_pmcp_introduced_keys() -> None` (test-only seam), `managed_secret_keys_strict(project: Path | None = None) -> set[str]`; in `types.py`: `FeedbackSubmissionOutcome`, `SubmitFeedbackOutput.submission_outcome`
- **Interfaces consumed**: (none)
- **Parallel-safe**: yes
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `tests/test_feedback_provenance.py` | **exactly these names**: `test_a_key_pmcp_wrote_at_runtime_is_recorded`, `test_the_registry_is_additive_and_not_production_clearable`, `test_the_strict_lookup_raises_where_the_lenient_one_swallows`, `test_the_lenient_lookup_keeps_its_existing_caller_behaviour`, `test_the_submission_outcome_field_defaults_to_none` | `uv run pytest -q tests/test_feedback_provenance.py` |
| SL-1.2 | impl | SL-1.1 | `src/pmcp/env_store.py`, `src/pmcp/types.py` | — | — |
| SL-1.3 | verify | SL-1.2 | `src/pmcp/env_store.py`, `src/pmcp/types.py` | all SL-1 tests | `uv run pytest -q tests/test_feedback_provenance.py tests/test_env_leak_229.py tests/test_tools.py && uv run mypy src/` |

**One registry, because the gate needs exactly one distinction: did PMCP put this
variable here, or did the operator's shell?** `record_pmcp_introduced_keys` /
`pmcp_introduced_keys` record every key PMCP introduces into its **own** `os.environ`,
by any route — a runtime write (`auth_connect`, `handlers.py:4972`) or a load of one of
PMCP's own credential-store files (`cli.py:2929-2930`). It is process-global and
**additive**, shaped exactly like `record_dotenv_keys`/`dotenv_sourced_keys`
(`env_store.py:112-142`) so the module has one idiom rather than two, and
`reset_pmcp_introduced_keys` mirrors `reset_dotenv_keys` (`:145-154`) as a **test-only
seam with no production caller**. The name deliberately departs from
`record_runtime_env_write(key)`: the registry has to cover a *load*, not only a write,
and the iterable form matches its sibling and matches how a `set(os.environ) - before`
delta is naturally measured.

**`dotenv_sourced_keys` is left exactly as it is.** It has a merged consumer —
`sanitized_subprocess_env` strips its keys from every spawned child (`env_store.py:200`)
— so widening its membership would change a behaviour outside this phase's charter.
The new registry is read only by this phase's gate.

**Why a strict lookup.** `managed_secret_keys` suppresses `OSError`/`ValueError` around
its project lookup (`:167-170`), which makes "the lookup failed" indistinguishable from
"the key is not planted" — the failure direction is *allow*, and IF-0-EGRESS-1's rule 9
could never fire for it. The lenient function keeps its signature and its only existing
caller, where swallowing is correct because a failed strip is a smaller harm than a
crashed spawn; `test_the_lenient_lookup_keeps_its_existing_caller_behaviour` pins that
so a lane does not "fix" it and change `sanitized_subprocess_env` underneath CONSENT.
The *user* lookup is already unguarded (`:166`), so rule 9 is reachable for it today;
only the project half fails open.

**`types.py` is additive**: one optional field with a `None` default, so every existing
construction and assertion is untouched. Neither owned file is owned by CONSENT (its
lanes own `project_consent.py`, `manifest/loader.py`, `config/loader.py`,
`policy/policy.py`, `tests/conftest.py`) nor by merged PKGID, so neither needs
cross-phase serialisation. Only the docs lane does — see Execution Notes.

**Every test file this phase adds declares two autouse fixtures of its own.**
`tests/conftest.py` is CONSENT SL-1's file, so — per IF-0-PKGID-2's precedent, and
mirroring PKGID's `_no_live_npm_registry` guard (`tests/conftest.py:168`) — each of
`tests/test_feedback_provenance.py`, `tests/test_feedback_egress_gate.py`,
`tests/test_feedback_submission_flag.py` and `tests/test_feedback_egress.py` declares
them locally:
1. **A network guard**: replace `feedback_egress`'s opener with a function that fails
   the test if it is called without the test having installed its own stub, so no test
   in this phase can reach `api.github.com` even if the gate regresses. PKGID's
   amendment 6 is why this matters — three existing tests had silently started calling
   the real npm registry and still passed, because a refusal assertion can pass for the
   wrong reason offline.
2. **A registry reset**: `reset_pmcp_introduced_keys()` before and after every test.
   The registry is process-global and any test that drives `auth_connect` records into
   it; `conftest.py`'s `_reset_dotenv_provenance` (`:151-165`) is the model. Nothing
   outside this phase reads the registry, so containing the reset to this phase's files
   is sufficient.

### SL-2 — Outbound-action gate and transport

- **Scope**: The single predicate `submit_feedback` consults before any network call, the destination it resolves, the browser URLs it falls back to, and the deadline-bounded blocking transport that publishes what it learns as it learns it.
- **Owned files**: `src/pmcp/feedback_egress.py`, `tests/test_feedback_egress_gate.py`
- **Interfaces provided**: `PACKAGED_FEEDBACK_REPOSITORY`, `FeedbackEgressReason`, `FeedbackEgressDecision`, `evaluate_feedback_egress`, `browser_issue_url`, `browser_search_url`, `FeedbackSubmission`, `FeedbackProgress`, `submit_feedback_issue`
- **Interfaces consumed**: `pmcp_introduced_keys`, `managed_secret_keys_strict`, `FeedbackSubmissionOutcome` (SL-1); `dotenv_sourced_keys` (`src/pmcp/env_store.py`, merged); `operator_safe` (`src/pmcp/provision_gate.py`, PKGID SL-1)
- **Parallel-safe**: yes
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-2.1 | test | SL-1 | `tests/test_feedback_egress_gate.py` | **the credential, consent and destination rules; exactly these names**: `test_the_gate_never_reads_github_token`, `test_the_submitter_reads_no_environment_variable`, `test_the_decision_never_renders_the_token`, `test_a_checkout_sourced_token_is_refused`, `test_a_pmcp_store_named_token_is_refused`, `test_a_pmcp_introduced_key_is_refused_without_any_store_entry`, `test_a_project_store_lookup_failure_is_not_read_as_absent`, `test_the_submission_flag_is_reported_before_the_confirmation`, `test_telemetry_disabled_is_reported_before_the_submission_flag`, `test_an_operator_exported_token_with_the_flag_is_allowed`, `test_a_provenance_lookup_failure_denies_rather_than_raising`, `test_the_default_repository_is_the_packaged_constant`, `test_the_packaged_default_repository_matches_the_distribution_metadata`, `test_a_checkout_sourced_repository_override_is_refused`, `test_a_malformed_repository_override_is_refused`, `test_a_refused_destination_yields_no_browser_url`, `test_the_browser_url_names_the_resolved_repository`, `test_the_search_url_names_the_repository_and_the_title` | `uv run pytest -q tests/test_feedback_egress_gate.py` |
| SL-2.2 | impl | SL-2.1 | `src/pmcp/feedback_egress.py` | — | — |
| SL-2.3 | test | SL-2.2 | `tests/test_feedback_egress_gate.py` | **the deadline, progress and outcome contract; exactly these names**: `test_an_expired_deadline_dispatches_no_post`, `test_a_post_that_cannot_finish_within_the_budget_is_not_dispatched`, `test_the_phase_budgets_sum_below_the_handler_bound`, `test_the_visibility_probe_runs_only_after_a_created_issue`, `test_a_probe_with_no_remaining_budget_is_skipped`, `test_a_dispatched_post_with_no_response_is_reported_as_unconfirmed`, `test_an_establishing_status_is_refused_with_a_compose_url`, `test_a_gateway_error_status_is_unconfirmed_with_a_search_url`, `test_an_error_without_a_github_request_id_is_unconfirmed`, `test_an_unparseable_success_body_is_unconfirmed`, `test_a_dns_or_refused_connection_establishes_not_dispatched`, `test_the_worker_dispatches_only_if_it_wins_the_claim`, `test_the_handler_may_report_not_dispatched_only_if_it_won_the_claim`, `test_the_probe_is_skipped_once_the_handler_has_abandoned`, `test_the_creation_is_published_before_the_probe_starts`, `test_a_stall_inside_the_transport_after_dispatch_publishes_unconfirmed`, `test_the_body_read_is_bounded_by_its_own_budget`, `test_the_progress_record_is_safe_to_read_while_the_worker_runs` | `uv run pytest -q tests/test_feedback_egress_gate.py` |
| SL-2.4 | verify | SL-2.3 | `src/pmcp/feedback_egress.py` | all SL-2 tests | `uv run pytest -q tests/test_feedback_egress_gate.py && uv run mypy src/ && uv run python -c "import pmcp.feedback_egress"` |

**`PACKAGED_FEEDBACK_REPOSITORY` is the runtime source of truth, and
`importlib.metadata` is the drift oracle — not the other way round.** The constant
ships in the wheel, so it is packaged configuration that both the code and the test
read, which is what EC-EGRESS-3 asks for; `test_the_default_repository_is_the_packaged_constant`
and the handler-level `test_the_default_repository_is_read_from_the_packaged_config`
assert against the symbol, never against a literal. Reading
`importlib.metadata.metadata("pmcp")`'s `Project-URL: Repository` *at runtime* was
rejected: a security gate must not acquire a new failure mode (absent or malformed
distribution metadata, under a vendored copy, a zipapp, or a bare `PYTHONPATH`) in the
one branch that decides where an operator's text is sent. Instead
`test_the_packaged_default_repository_matches_the_distribution_metadata` compares the
constant to that metadata, which is present under both an editable `uv sync` and an
installed wheel, so the drift that produced `ViperJuice/pmcp` cannot recur silently.
`pyproject.toml:105-108` is the value it is compared against.

`PMCP_FEEDBACK_REPO` **stays** as an operator override — a fork, or a private mirror,
is a legitimate destination and removing the escape hatch would push operators back
onto door 4 by hand. What changes is that it must survive rules 1 and 2: a value a
checkout supplied is refused rather than honoured, and a value that is not
`owner/repo`-shaped is refused rather than substituted into a URL. If it names a
repository the operator did not intend but *did* export themselves, this phase does
not and cannot detect that; the preview names the destination in `repository` and in
the browser URL before anything is sent, which is the only defence available and is
the reason preview is the default.

`submit_feedback_issue` takes its token as an argument and reads no environment
variable, which is what makes `GITHUB_TOKEN`'s removal structural rather than a deletion
someone can reintroduce. It performs the issue `POST` and then, with budget left, the
visibility `GET`, publishing to `FeedbackProgress` at each point where it learns
something — see the freeze's progress contract, which is the half of this lane a
reviewer should read first.

### SL-3 — Operator submission flag, CLI verb, and startup-load recording

- **Scope**: The operator's opt-in to outbound submission — where it is stored, how it is written, what `pmcp guidance` reports — and the one startup site that puts PMCP's own credential stores into PMCP's own environment without recording it.
- **Owned files**: `src/pmcp/config/guidance.py`, `src/pmcp/cli.py`, `tests/test_feedback_submission_flag.py`
- **Interfaces provided**: `GuidanceConfig.enable_feedback_submission` (default `False`), `set_feedback_submission_enabled(enabled: bool, config_path: Path | None = None) -> tuple[GuidanceConfig, Path]`, `pmcp guidance --feedback-submission on|off`
- **Interfaces consumed**: `record_pmcp_introduced_keys` (SL-1)
- **Parallel-safe**: yes
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-3.1 | test | SL-1 | `tests/test_feedback_submission_flag.py` | **exactly these names**: `test_feedback_submission_defaults_to_off`, `test_a_config_without_the_key_reads_as_off`, `test_a_generated_default_config_states_the_submission_default`, `test_the_cli_verb_persists_the_submission_flag`, `test_the_cli_verb_turns_the_submission_flag_off_again`, `test_the_guidance_status_shows_the_submission_flag`, `test_startup_store_loads_are_recorded_as_pmcp_introduced` | `uv run pytest -q tests/test_feedback_submission_flag.py` |
| SL-3.2 | impl | SL-3.1 | `src/pmcp/config/guidance.py` | — | — |
| SL-3.3 | impl | SL-3.2 | `src/pmcp/cli.py` | — | — |
| SL-3.4 | verify | SL-3.3 | `src/pmcp/config/guidance.py`, `src/pmcp/cli.py` | all SL-3 tests | `uv run pytest -q tests/test_feedback_submission_flag.py tests/test_cli.py tests/test_env_leak_229.py && uv run mypy src/` |

**Why the flag lives in `GuidanceConfig` and not anywhere else.** It is an operator
preference about pmcp's own behaviour, and it gates the sibling of the switch already
there: `enable_telemetry` (`guidance.py:64-69`) turns the feedback *workflow* on, this
turns the *outbound act* on. Two booleans about one feature in two config files would
give operators two places to look and two places to get wrong. The file is
`~/.claude/gateway-guidance.yaml` (`guidance.py:153`), user-scoped and outside any
checkout, so TRUST's residency principle — a repository must not be able to ship its
own consent — holds here with no new machinery. It is **not** a `pmcp trust` verb:
`pmcp trust` records decisions about a *subject* (a path's bytes, a package identity)
in a store an operator can list and revoke; this is a global mode switch with no
subject, and modelling it as a record would invent one.

`set_feedback_submission_enabled` mirrors `set_telemetry_enabled` (`guidance.py:210-238`)
exactly, including its read-modify-write of the whole YAML document, so an operator's
other settings survive. `create_default_guidance_config` (`:174-207`) gains an explicit
`"enable_feedback_submission": False` so a freshly generated file *states* the default
rather than relying on the model's. The CLI flag mirrors `--telemetry`
(`cli.py:548-552`, handled at `:2444-2448`) and the status block gains one line beside
`Feedback Telemetry` (`:2465`).

**Default off in three places, because two are not enough.** The pydantic default is
`False`; a YAML file that predates this phase has no key and therefore reads `False`;
and — the one this lane cannot enforce — a `GatewayTools` built with
`guidance_config=None` must also read off, which is SL-4's line and SL-4's test.
`_telemetry_enabled` (`handlers.py:3798-3802`) returns **True** for `None`, so a lane
that copies its shape ships the flag on by default for every embedder that omits the
config.

**`load_startup_env` gains the recording this phase's gate depends on.**
`cli.py:2929-2930` loads `~/.config/pmcp/pmcp.env` and `$CWD/.env.pmcp` — PMCP's own
credential stores — into PMCP's own `os.environ`, and records nothing. The docstring at
`:2913` says those loads "need no recording; `managed_secret_keys` already covers those
keys", which was true for the only consumer at the time and is **false for a provenance
check**, because `managed_secret_keys` answers about the file's contents *now*. This
lane measures the delta the same way `:2925-2927` already does for the plain `.env`, and
calls `record_pmcp_introduced_keys` with it:

```
before = set(os.environ)
load_dotenv(Path.home() / ".config" / "pmcp" / "pmcp.env", override=False)
load_dotenv(Path.cwd() / ".env.pmcp", override=False)
record_pmcp_introduced_keys(set(os.environ) - before)
```

`override=False` is what makes the delta correct: a variable the operator exported is
already in `before`, so it is never recorded and never refused. The runtime loads at
`handlers.py:3010-3018` need **no** change — they already measure and record their delta
through `record_dotenv_keys` (`:3016-3018`), and the gate reads that registry too.

### SL-4 — `submit_feedback` rewiring

- **Scope**: Replace the body of `submit_feedback` with: resolve the decision, honour it, and run the transport off the event loop under a deadline that bounds the act as well as the answer. Record `auth_connect`'s environment write. Delete the `gh` door and the `GITHUB_TOKEN` fallback.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_feedback_egress.py`
- **Interfaces provided**: (none — behaviour change only)
- **Interfaces consumed**: `record_pmcp_introduced_keys`, `FeedbackSubmissionOutcome`, `SubmitFeedbackOutput.submission_outcome` (SL-1); `evaluate_feedback_egress`, `FeedbackEgressDecision`, `browser_issue_url`, `browser_search_url`, `submit_feedback_issue`, `FeedbackSubmission`, `FeedbackProgress`, `PACKAGED_FEEDBACK_REPOSITORY` (SL-2); `GuidanceConfig.enable_feedback_submission` (SL-3); `anyio.to_thread.run_sync`, `anyio.fail_after` (already imported, `handlers.py:21`)
- **Parallel-safe**: no
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-4.1 | test | SL-1, SL-2, SL-3 | `tests/test_feedback_egress.py` | **exactly these names**, all driven through `gateway.submit_feedback`: `test_an_ambient_github_token_attempts_no_request`, `test_the_gh_cli_is_never_spawned_under_an_ambient_token`, `test_confirm_submission_alone_does_not_post`, `test_the_preview_returns_the_payload_and_a_browser_url`, `test_a_gateway_with_no_guidance_config_refuses_to_submit`, `test_the_flag_on_without_a_token_previews_and_names_the_variable`, `test_an_enabled_operator_with_a_token_posts_once`, `test_a_token_from_the_pmcp_credential_store_cannot_post`, `test_a_checkout_env_file_cannot_supply_the_token`, `test_a_checkout_env_file_cannot_redirect_the_destination`, `test_telemetry_disabled_builds_no_payload_and_records_no_event`, `test_the_default_repository_is_read_from_the_packaged_config`, `test_a_hostile_repository_override_is_rendered_inert_in_the_refusal`, `test_no_blocking_http_call_runs_on_the_event_loop`, `test_a_slow_submission_is_bounded_by_the_handler_timeout`, `test_a_failed_submission_opens_no_second_door`, `test_a_preview_does_not_claim_the_repository_is_public`, `test_the_tool_description_does_not_promise_that_confirmation_submits` | `uv run pytest -q tests/test_feedback_egress.py` |
| SL-4.2 | impl | SL-4.1 | `src/pmcp/tools/handlers.py` | — | — |
| SL-4.3 | test | SL-4.2 | `tests/test_feedback_egress.py` | **the durable-provenance and post-deadline contracts; exactly these names**: `test_a_planted_token_is_still_refused_after_its_store_entry_is_removed`, `test_the_provenance_record_survives_a_second_auth_connect_in_the_other_scope`, `test_a_startup_loaded_store_token_survives_an_unrelated_auth_connect_and_is_refused`, `test_the_gate_is_asked_about_the_project_root_the_secret_writer_uses`, `test_a_submission_that_outlives_the_handler_bound_issues_no_post_afterwards`, `test_a_stall_before_the_claim_dispatches_nothing_after_the_handler_answers`, `test_a_dispatched_but_unconfirmed_post_is_not_reported_as_submitted`, `test_a_stall_after_dispatch_reports_unconfirmed_with_a_search_url`, `test_a_creation_observed_before_the_timeout_is_reported_as_submitted`, `test_a_stall_during_the_probe_does_not_lose_the_created_issue` | `uv run pytest -q tests/test_feedback_egress.py` |
| SL-4.4 | verify | SL-4.3 | `src/pmcp/tools/handlers.py`, `tests/test_feedback_egress.py` | all SL-4 tests | `uv run pytest -q tests/test_feedback_egress.py tests/test_tools.py tests/test_provision_validation.py tests/test_phase4_e2e.py tests/test_baseline_constraints.py tests/test_env_leak_229.py && uv run mypy src/` |

**The handler after this lane holds no transport.** Every argument of the call site,
named — a lane must not have to guess one:

```
decision = evaluate_feedback_egress(
    telemetry_enabled=self._telemetry_enabled(),           # unchanged helper, :3798
    submission_enabled=bool(                               # the None-is-off line
        self._guidance_config and self._guidance_config.enable_feedback_submission
    ),
    confirm_submission=parsed.confirm_submission,
    environ=os.environ,                                    # the live mapping, not a copy
    project_root=self._project_root,                       # NOT None -- see below
)
...                                                        # render per the freeze table
progress = FeedbackProgress()
deadline = time.monotonic() + _FEEDBACK_SUBMIT_TIMEOUT_SECONDS
try:
    with anyio.fail_after(_FEEDBACK_SUBMIT_TIMEOUT_SECONDS):
        result = await anyio.to_thread.run_sync(
            functools.partial(
                submit_feedback_issue,
                repository=decision.repository, token=decision.token,
                title=issue_title, body=issue_body,
                labels=["pmcp-feedback", "authenticated-feedback", parsed.issue_type],
                deadline=deadline, progress=progress,
            ),
            abandon_on_cancel=True,
        )
except TimeoutError:
    result = progress.abandon()       # atomically closes the state; exactly one side wins
```

**`project_root=self._project_root`, not `None`, and this is not cosmetic.**
`auth_connect`'s `scope="project"` write goes through `_write_secret`, which passes
`self._project_root` to `set_env_value` (`handlers.py:3115-3117`). `resolve_project_root`
falls back to a cwd walk only when its argument is `None` (`env_store.py:24-33`), so a
gateway constructed with an explicit `project_root` writes one file and a gate asked
with `None` would read a different one — the provenance check would then look in the
wrong place for exactly the plant it exists to catch. The gate must ask about the same
root the secret writer used. `test_the_gate_is_asked_about_the_project_root_the_secret_writer_uses`
is the falsifier.

**`auth_connect` gains one line**: `record_pmcp_introduced_keys([env_var])` in the same
statement group as `os.environ[env_var] = parsed.credential` (`:4972`), with no `await`
between them, so no other task can observe the environment write without the record.
It runs for `scope="user"` and `scope="project"` alike, because `:4972` does.

`_FEEDBACK_SUBMIT_TIMEOUT_SECONDS = 20.0` sits beside
`_REGISTRATION_RESOLVE_TIMEOUT_SECONDS` (`handlers.py:204`) with the same reasoning: a
per-socket timeout is not a request bound, and a hostile or merely slow endpoint that
trickles bytes holds a tool call open indefinitely without one.
`abandon_on_cancel=True` is not optional — without it the timeout waits for the
sleeping socket it was meant to escape, and
`test_a_slow_submission_is_bounded_by_the_handler_timeout` deadlocks. **It is also
why the freeze needs both the deadline contract and `FeedbackProgress`**: an abandoned
worker keeps running *and* its return value is discarded, so the bound on this `with`
block bounds only the answer, and without the progress record a POST that had already
succeeded would be reported as unconfirmed merely because the probe after it stalled.
`test_a_creation_observed_before_the_timeout_is_reported_as_submitted` and
`test_a_stall_during_the_probe_does_not_lose_the_created_issue` are the falsifiers for
that half.

**Why the thread and not `httpx`.** `httpx` is a declared dependency (`pyproject.toml`)
but is imported only inside two `cli.py` diagnostics functions (`:1990`, `:2013`), and
`httpx2` belongs to the downstream MCP transport. Rewriting this path natively async
would change its failure and timeout semantics — in a function whose error handling is
a bare `except Exception` — in the same commit that changes its authority model, and
nothing in the phase's criteria would be proved by it. Nor would it deliver a total
request bound: `httpx`'s `read` timeout, like a socket timeout, restarts per chunk. The
thread-plus-bound shape is already in this file, already reviewed, and carries the "a
socket timeout is not a request bound" argument. `urlopen` moves to
`feedback_egress.py` rather than staying inline, which is what makes the
`## Verification` grep (`no urlopen in handlers.py`) a real structural assertion rather
than a style check.

**Why `gh` is deleted rather than gated.** `gh` authenticates from its own stored
credentials *and* from an ambient `GITHUB_TOKEN`/`GH_TOKEN` in the environment it
inherits, so a post through it is under the operator's personal identity by
construction, and pmcp cannot tell which credential it used. Putting it behind the new
flag would leave a submission path whose authority no test can assert — the property
this phase exists to establish. Spawning it with a sanitized environment carrying
`GH_TOKEN=$PMCP_FEEDBACK_TOKEN` was considered and rejected as strictly redundant: the
API path already does exactly that, better. Operators who relied on `gh` set
`PMCP_FEEDBACK_TOKEN` or use the browser URL. Deleting it also removes the
`create_subprocess_exec` door and the file's last `shutil` use.

**Why these are handler-level tests.** PKGID's lesson is that a gate-level test that
supplies what production never supplies proves nothing: SL-2's tests can hand
`evaluate_feedback_egress` any `environ` dict they like. Every one of SL-4's goes
through `gateway.submit_feedback` with `monkeypatch.setenv`, a real
`env_store.set_env_value` write, a real `gateway.auth_connect` call, or a real
`load_dotenv` — the inputs production actually has.
`test_no_blocking_http_call_runs_on_the_event_loop` patches
`feedback_egress.submit_feedback_issue` with a function that **really blocks**
(`time.sleep`) and records `threading.get_ident()`, while a heartbeat task counts
`asyncio.sleep(0.01)` ticks: it asserts the loop kept ticking *and* that the call ran
off the main thread. A mock-only test cannot prove this — an `AsyncMock` returning
instantly satisfies the same assertions against unchanged `main`, where the call is
inline. And `test_a_submission_that_outlives_the_handler_bound_issues_no_post_afterwards`
is the one test here that cannot be written by watching the handler: it blocks the
worker **inside** `submit_feedback_issue`, past the pre-dispatch check, lets
`fail_after` fire, asserts the handler answered, **then releases the block, joins the
worker, and asserts no POST request ever reached the opener**. Measuring only how
quickly the handler returned passes against a build that files the issue half a second
later — which is precisely the defect.
### SL-docs — Documentation & spec reconciliation

- **Scope**: Refresh the docs catalog, update cross-cutting documentation this phase touches or invalidates, and append post-execution amendments to the roadmap where its EGRESS text turned out wrong.
- **Owned files**: `.claude/docs-catalog.json`, `CHANGELOG.md`, `README.md`, `specs/phase-plans-v13.md`
- **Interfaces provided**: (none)
- **Interfaces consumed**: (none)
- **Parallel-safe**: no (terminal)
- **Depends on**: SL-4 (and, transitively, every lane)
- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Action |
|---|---|---|---|---|
| SL-docs.1 | docs | — | `.claude/docs-catalog.json` | Rescan via `_shared/scaffold_docs_catalog.py --rescan` if present; if absent, record "docs-catalog rescan helper unavailable; manual catalog audit" in the commit message and proceed. |
| SL-docs.2 | docs | SL-docs.1 | per catalog | Decide per catalog file whether this phase changes it. CHANGELOG gets the behaviour-change entry: submission is now opt-in, `GITHUB_TOKEN` is no longer honoured, the `gh` fallback is removed, and the default repository is corrected from `ViperJuice/pmcp` to `Consiliency/pmcp`; and `SubmitFeedbackOutput` gains the optional `submission_outcome`, which distinguishes a post the server refused from one whose response was lost. README gets the `pmcp guidance --feedback-submission` verb and the `PMCP_FEEDBACK_TOKEN` provenance rule — including that a token PMCP itself stored or loaded from a `.env` is **not** honoured, and must be exported in the shell that starts pmcp. **`SECURITY.md` is deliberately NOT updated here** — the roadmap's Execution Notes assign the trust-model write-up to SEAL, once; TRUST and PKGID both set that precedent. Record the skip explicitly. |
| SL-docs.3 | docs | SL-docs.2 | `specs/phase-plans-v13.md` | Append `### Post-execution amendments — EGRESS` to the EGRESS phase section. At minimum it must record the stale `:4796-4992` citation, the fourth egress door (`gh`) the exit criteria never named, the two agent-reachable inputs to the credential predicate, the fact that `abandon_on_cancel=True` bounds the answer and not the act *and discards the worker's result* (so EC-EGRESS-4 needs a pre-dispatch deadline and a progress record, not just a handler timeout), the `dispatched_unconfirmed` outcome the roadmap's binary submitted/not-submitted framing has no room for, the finding that **no per-socket timeout in the standard library or in `httpx` is a total request bound**, the compare-and-set that makes the worker's dispatch and the handler's give-up mutually exclusive (a snapshot lock alone leaves the R1 race), the establishing-status allowlist (an intermediary's 5xx is uncertain, not negative), so the phase bounds the act and publishes facts as they are learned rather than claiming a bound it cannot deliver, and the fact that the criteria are proven across four test files and six source files rather than the one `tests/test_feedback_egress.py` and one `handlers.py` the roadmap's Key files and evidence paths name (the same defect PKGID amendment 11(e) records). |
| SL-docs.4 | verify | SL-docs.3 | — | `uv run ruff format --check src/ tests/ scripts/` and `python3 scripts/check_plan_consistency.py plans/phase-plan-v13-EGRESS.md`; plus any repo doc linters, no-op if none configured. |


## Execution Notes

- **Parallelism.** SL-1 is the only root: both SL-2 and SL-3 consume its provenance API,
  and SL-2 consumes its output field too. Run SL-1 alone, then **SL-2 and SL-3
  concurrently** (they share no file), then SL-4, then SL-docs. Four waves:
  `SL-1` → `{SL-2, SL-3}` → `SL-4` → `SL-docs`. No lane may begin against a guessed
  `evaluate_feedback_egress` signature, a guessed registry name, or a guessed flag name.
  *Revised after the second plan panel*: the first revision had two roots and kept them
  by declining to record the `cli.py` store loads — a correctness decision taken to
  preserve a lane shape, and the hole the panel's Q1 found. The preamble lane costs one
  wave and buys a frozen interface every other lane codes against.
- **Single-writer region: `src/pmcp/tools/handlers.py`, owner SL-4, sole writer.** It
  is 6584 lines and the roadmap names it as the whole v13 single-writer hazard. SL-4's
  edits are confined to `:18-19` (two imports), `:866-871` (the tool description),
  `:3804-3814` (`_feedback_hint`'s wording) and `:4995-5191` (`submit_feedback`), plus
  one constant beside `:204`. PKGID's lanes wrote `:4127-4490` and `:5513-5587` —
  disjoint ranges, but git merges files, not ranges. **PKGID is merged (`5c7e0a1`), so
  no live conflict exists; do not open any other `handlers.py` work alongside SL-4.**
- **Cross-phase collision: SL-docs is not exclusively ours.** `CHANGELOG.md`,
  `.claude/docs-catalog.json`, `README.md` and `specs/phase-plans-v13.md` are the same
  four files CONSENT's and PKGID's docs lanes own, and the roadmap's Execution Notes
  already record that pairwise collision as invisible to any single plan's ownership
  check. **Execute SL-docs serially against any other phase's docs lane**, and rebase
  whichever lands second. EGRESS's *impl* lanes remain parallel with every other
  phase: no other phase writes `feedback_egress.py`, `config/guidance.py` or the
  `submit_feedback` range.
- **Known destructive changes**, all four in SL-4, all loud and all in the CHANGELOG:
  (a) the `gh issue create` path (`:5123-5173`) is deleted, with its
  `asyncio.create_subprocess_exec` and the file's last `shutil` use; (b) the
  `or os.environ.get("GITHUB_TOKEN")` fallback (`:5043`) is deleted; (c) `urlopen` and
  `urlencode` (`:18-19`) become unused and are removed — ruff fails otherwise; (d) the
  tool description's "set `confirm_submission=true` to submit" (`:870`) is false after
  this phase and is rewritten, as is `_feedback_hint`'s matching sentence. **No file is
  deleted by any lane**, and SL-1, SL-2 and SL-3 are purely additive.
- **Assumption 5 — "a default-deny that silently breaks a working setup is a failed
  phase" — is satisfied in its letter, not dodged.** Submission *does* stop working for
  an operator who relied on an ambient `GITHUB_TOKEN` or on `gh`. It stops **loudly**,
  and the freeze's table — not this bullet — is the contract for how. Precisely: the
  three ordinary refusals (`submission_not_enabled`, `not_confirmed`,
  `no_feedback_token`) return `ok=True` with the full payload, a browser URL that still
  submits, and the exact `pmcp guidance --feedback-submission on` command. The
  misconfiguration and fail-closed rows (`untrusted_repository_override`,
  `invalid_repository`, `telemetry_disabled`, `untrusted_token`, `gate_error`) return
  `ok=False` with **no** payload and **no** browser URL, because each of them means pmcp
  cannot establish where the text would go or under whose authority — handing back a
  submit-ready URL there would be the silent failure, not the loud one. Every row
  carries a remedy naming a real command or a real path. No other gateway behaviour
  changes; feedback preview, which is what the agent actually uses, is unaffected.
  *(Corrected after the plan panel: the first revision of this bullet claimed `ok=True`
  plus payload plus browser URL for **every** refusal, which contradicts five rows of
  this plan's own table.)*
- **Known limitation, recorded not fixed.** With no token, door 4 is now the only
  submission route, and `_build_feedback_issue` bodies run to ~4000 tokens
  (`FEEDBACK_TOKEN_LIMIT`, `:198`), which can exceed a practical URL length. The
  payload is returned in `issue_body` regardless, so nothing is lost; shortening the
  browser payload is feature work, not this phase.
- **Expected add/add conflicts**: none. `feedback_egress.py` is a new module no other
  lane stubs; there is no package `__init__` re-export to add.
- **Expected out-of-lane fallout: measured, and there is none in the four suites the
  panel expected it in.** `grep -rn 'ViperJuice/pmcp\|repository_visibility\|"public"'`
  over `tests/test_tools.py`, `tests/test_phase4_e2e.py`,
  `tests/test_baseline_constraints.py` and `tests/test_provision_validation.py` returns
  **nothing**, and `grep -rn repository_visibility tests/` returns nothing across the
  whole suite: no golden encodes the wrong repository or the hardcoded `"public"`
  visibility. The two existing `submit_feedback` assertions
  (`tests/test_tools.py:4442`, `:4468`) check `submitted is False` plus the substrings
  "consent" and "disabled", which the freeze's message rules deliberately preserve. The
  repair pass should therefore expect **no** golden updates from this phase; if it finds
  one, something changed outside this plan. **One unrelated `ViperJuice` reference does
  exist and is NOT this phase's**: `src/pmcp/manifest/version_checker.py:24` builds
  `_USER_AGENT = f"pmcp/{__version__} (github.com/ViperJuice/pmcp)"`, asserted by
  `tests/test_version_checker.py:1413`. It is the same drift defect at a second site,
  it rides the *registry* lookups rather than this handler, and EGRESS owns neither
  file — reported upward rather than fixed here.
- **Lane-unique scratchpads (PKGID amendment 7, verbatim requirement).** Every subagent
  in a session shares one scratchpad directory, and two lanes' scripts have already
  collided there once, briefly capturing a live mutant in a commit. **Lanes must use
  lane-unique scratch subdirectories, and must assert the working tree matches `HEAD`
  before and after any mutation run.**
- **Stale-base guidance** (copy verbatim): lane teammates in isolated worktrees do not
  see sibling merges automatically. If SL-4 finds its base is pre-SL-2 or pre-SL-3, or either of those is pre-SL-1, it
  MUST stop and report rather than commit — the orchestrator re-spawns or rebases. A
  silent `git reset --hard` or `git checkout HEAD~N -- …` in a stale worktree produces
  commits that destroy peer-lane work on a `--no-ff` merge.
- **Trust the document over any brief, and report disagreements.** Node ids in this
  plan were authored in the acceptance criteria first and the lane tables derived from
  them; generate a lane's list with `python3 scripts/check_plan_consistency.py --brief
  SL-4 plans/phase-plan-v13-EGRESS.md` rather than retyping it.

## Acceptance Criteria

- [ ] EC-EGRESS-1 — ambient `GITHUB_TOKEN` is never used; only a dedicated, operator-supplied `PMCP_FEEDBACK_TOKEN` is honoured. Proven by `uv run pytest -q tests/test_feedback_egress.py::test_an_ambient_github_token_attempts_no_request tests/test_feedback_egress.py::test_the_gh_cli_is_never_spawned_under_an_ambient_token tests/test_feedback_egress.py::test_an_enabled_operator_with_a_token_posts_once tests/test_feedback_egress.py::test_a_token_from_the_pmcp_credential_store_cannot_post tests/test_feedback_egress.py::test_a_checkout_env_file_cannot_supply_the_token tests/test_feedback_egress_gate.py::test_the_gate_never_reads_github_token tests/test_feedback_egress_gate.py::test_the_submitter_reads_no_environment_variable tests/test_feedback_egress_gate.py::test_the_decision_never_renders_the_token tests/test_feedback_egress_gate.py::test_a_checkout_sourced_token_is_refused tests/test_feedback_egress_gate.py::test_a_pmcp_store_named_token_is_refused tests/test_feedback_egress_gate.py::test_a_pmcp_introduced_key_is_refused_without_any_store_entry tests/test_feedback_egress_gate.py::test_an_operator_exported_token_with_the_flag_is_allowed tests/test_feedback_provenance.py::test_a_key_pmcp_wrote_at_runtime_is_recorded tests/test_feedback_provenance.py::test_the_registry_is_additive_and_not_production_clearable tests/test_feedback_submission_flag.py::test_startup_store_loads_are_recorded_as_pmcp_introduced tests/test_feedback_egress.py::test_a_planted_token_is_still_refused_after_its_store_entry_is_removed tests/test_feedback_egress.py::test_the_provenance_record_survives_a_second_auth_connect_in_the_other_scope tests/test_feedback_egress.py::test_a_startup_loaded_store_token_survives_an_unrelated_auth_connect_and_is_refused tests/test_feedback_egress.py::test_the_gate_is_asked_about_the_project_root_the_secret_writer_uses`, falsified by setting `GITHUB_TOKEN` (and `GH_TOKEN`), enabling the flag, calling with `confirm_submission=true`, and asserting that **no request was attempted at all**: `feedback_egress.submit_feedback_issue` replaced by a recorder that was never called, the module opener replaced by a raiser that never raised, `shutil.which` returning a real path while `asyncio.create_subprocess_exec` is a recorder that was never called, and `submitted is False`. Falsified further at the provenance layer: the same `PMCP_FEEDBACK_TOKEN` value refuses when `env_store.set_env_value` put it in PMCP's own store (the route `gateway.auth_connect` writes, `handlers.py:4972`) or when a `.env` in cwd introduced it (`dotenv_sourced_keys`), and allows when the operator exported it. **Falsified further at the durability layer**, which is what the plan panel's P1 found missing: drive a real `gateway.auth_connect` that plants `PMCP_FEEDBACK_TOKEN`, then **delete the store file**, and assert the call is still refused `untrusted_token` — the store lookup alone would now read it as operator-supplied, and `pmcp_introduced_keys()` is what still refuses; **and by the chain the second panel named**, which the first revision deferred and this plan's own read-modify-write finding refutes: put the token in the store, restart so `cli.py:2929-2930` loads it into `os.environ`, then call `auth_connect` for an **unrelated** server whose rewrite drops the original entry, and assert the token is still refused — a write-only registry never wrote it, `dotenv_sourced_keys` never saw it, and the store no longer holds it, so recording the *load* is the only thing left that refuses; assert a second `auth_connect` in the other scope does not displace the first record; and assert the gate was asked about `self._project_root`, the same root `_write_secret` passes to `set_env_value` (`handlers.py:3115-3117`), by constructing the gateway with an explicit project root and planting the token in *that* project store. **Fails on `main`**: `:5043` reads `os.environ.get("PMCP_FEEDBACK_TOKEN") or os.environ.get("GITHUB_TOKEN")` and posts through `urlopen` at `:5089`; with no token at all it reaches `gh` at `:5123-5143`, which posts under the ambient token anyway.
- [ ] EC-EGRESS-2 — preview is the default, and `confirm_submission` alone cannot cause a post without `enable_feedback_submission: true`. Proven by `uv run pytest -q tests/test_feedback_egress.py::test_confirm_submission_alone_does_not_post tests/test_feedback_egress.py::test_the_preview_returns_the_payload_and_a_browser_url tests/test_feedback_egress.py::test_a_gateway_with_no_guidance_config_refuses_to_submit tests/test_feedback_egress.py::test_the_flag_on_without_a_token_previews_and_names_the_variable tests/test_feedback_egress.py::test_telemetry_disabled_builds_no_payload_and_records_no_event tests/test_feedback_egress.py::test_a_preview_does_not_claim_the_repository_is_public tests/test_feedback_egress.py::test_the_tool_description_does_not_promise_that_confirmation_submits tests/test_feedback_egress_gate.py::test_the_submission_flag_is_reported_before_the_confirmation tests/test_feedback_egress_gate.py::test_telemetry_disabled_is_reported_before_the_submission_flag tests/test_feedback_egress_gate.py::test_a_provenance_lookup_failure_denies_rather_than_raising tests/test_feedback_provenance.py::test_the_strict_lookup_raises_where_the_lenient_one_swallows tests/test_feedback_provenance.py::test_the_lenient_lookup_keeps_its_existing_caller_behaviour tests/test_feedback_egress_gate.py::test_a_project_store_lookup_failure_is_not_read_as_absent tests/test_feedback_submission_flag.py::test_feedback_submission_defaults_to_off tests/test_feedback_submission_flag.py::test_a_config_without_the_key_reads_as_off tests/test_feedback_submission_flag.py::test_a_generated_default_config_states_the_submission_default tests/test_feedback_submission_flag.py::test_the_cli_verb_persists_the_submission_flag tests/test_feedback_submission_flag.py::test_the_cli_verb_turns_the_submission_flag_off_again tests/test_feedback_submission_flag.py::test_the_guidance_status_shows_the_submission_flag`, falsified by calling `gateway.submit_feedback` with `confirm_submission=true` and a valid `PMCP_FEEDBACK_TOKEN` against a gateway whose `GuidanceConfig` leaves `enable_feedback_submission` at its default — assert `submitted is False`, the recorder was never called, `issue_url` is a `https://github.com/<repo>/issues/new?…` browser URL, `issue_body` is the full built payload, and `message` names `pmcp guidance --feedback-submission on`; and by the same call against a gateway constructed with `guidance_config=None`, which must also refuse. Falsified at the fail-closed layer by making the project-store lookup raise and asserting the decision is `gate_error`, never an allow — `managed_secret_keys` swallows `OSError`/`ValueError` there (`env_store.py:167-170`), so the strict variant is what makes rule 9 reachable, asserted directly by comparing the two functions against the same unreadable file — and by pinning that the lenient one still swallows, so a lane cannot "fix" it and change `sanitized_subprocess_env` underneath CONSENT. Falsified at the ordering layer by asserting the reason for an unconfirmed call with the flag off is `submission_not_enabled`, not `not_confirmed` — the order the freeze fixes, and the one PKGID item 3 shows a plan can get wrong. **Fails on `main`**: there is no flag; `:5028` returns a preview only when `confirm_submission` is false, and a confirmed call with a token posts at `:5089`. `test_the_tool_description_does_not_promise_that_confirmation_submits` fails on `main` too — `:870` says "set confirm_submission=true to submit".
- [ ] EC-EGRESS-3 — the default repository is the real remote, asserted against the packaged configuration rather than a literal duplicated in the test. Proven by `uv run pytest -q tests/test_feedback_egress.py::test_the_default_repository_is_read_from_the_packaged_config tests/test_feedback_egress.py::test_a_checkout_env_file_cannot_redirect_the_destination tests/test_feedback_egress.py::test_a_hostile_repository_override_is_rendered_inert_in_the_refusal tests/test_feedback_egress_gate.py::test_the_default_repository_is_the_packaged_constant tests/test_feedback_egress_gate.py::test_the_packaged_default_repository_matches_the_distribution_metadata tests/test_feedback_egress_gate.py::test_a_checkout_sourced_repository_override_is_refused tests/test_feedback_egress_gate.py::test_a_malformed_repository_override_is_refused tests/test_feedback_egress_gate.py::test_a_refused_destination_yields_no_browser_url tests/test_feedback_egress_gate.py::test_the_browser_url_names_the_resolved_repository tests/test_feedback_egress_gate.py::test_the_search_url_names_the_repository_and_the_title`, falsified by calling with `PMCP_FEEDBACK_REPO` unset and asserting `result.repository == pmcp.feedback_egress.PACKAGED_FEEDBACK_REPOSITORY` **and** that the browser URL contains that same symbol's value — no `"Consiliency/pmcp"` literal appears in either test; by asserting the constant equals `importlib.metadata.metadata("pmcp")`'s `Project-URL: Repository` owner/name, which is the drift check that would have caught `ViperJuice/pmcp`; by writing `PMCP_FEEDBACK_REPO=attacker/evil` into a `.env` that a real `load_dotenv` introduces and asserting the call refuses with `issue_url is None` and `repository` back at the packaged default, so no attacker-named URL is ever handed to the agent; and by an override carrying a control character and shell metacharacters, asserting the refusal renders it inert (`operator_safe`) and still refuses. **Fails on `main`**: `:4998` is `os.environ.get("PMCP_FEEDBACK_REPO", "ViperJuice/pmcp")` — a literal naming a repository this project does not own, echoed unvalidated into every output including `:5009`.
- [ ] EC-EGRESS-4 — no blocking HTTP call runs on the event loop in this path (review finding P-03), and the submission is bounded — **bounded as an act, not merely as an answer**. Proven by `uv run pytest -q tests/test_feedback_egress.py::test_no_blocking_http_call_runs_on_the_event_loop tests/test_feedback_egress.py::test_a_slow_submission_is_bounded_by_the_handler_timeout tests/test_feedback_egress.py::test_a_failed_submission_opens_no_second_door tests/test_feedback_egress.py::test_a_submission_that_outlives_the_handler_bound_issues_no_post_afterwards tests/test_feedback_egress.py::test_a_stall_before_the_claim_dispatches_nothing_after_the_handler_answers tests/test_feedback_egress.py::test_a_dispatched_but_unconfirmed_post_is_not_reported_as_submitted tests/test_feedback_egress_gate.py::test_an_expired_deadline_dispatches_no_post tests/test_feedback_egress_gate.py::test_a_post_that_cannot_finish_within_the_budget_is_not_dispatched tests/test_feedback_egress_gate.py::test_the_phase_budgets_sum_below_the_handler_bound tests/test_feedback_egress_gate.py::test_the_visibility_probe_runs_only_after_a_created_issue tests/test_feedback_egress_gate.py::test_a_probe_with_no_remaining_budget_is_skipped tests/test_feedback_egress_gate.py::test_a_dispatched_post_with_no_response_is_reported_as_unconfirmed tests/test_feedback_egress_gate.py::test_an_establishing_status_is_refused_with_a_compose_url tests/test_feedback_egress_gate.py::test_a_gateway_error_status_is_unconfirmed_with_a_search_url tests/test_feedback_egress_gate.py::test_an_error_without_a_github_request_id_is_unconfirmed tests/test_feedback_egress_gate.py::test_an_unparseable_success_body_is_unconfirmed tests/test_feedback_egress_gate.py::test_a_dns_or_refused_connection_establishes_not_dispatched tests/test_feedback_egress_gate.py::test_the_worker_dispatches_only_if_it_wins_the_claim tests/test_feedback_egress_gate.py::test_the_handler_may_report_not_dispatched_only_if_it_won_the_claim tests/test_feedback_egress_gate.py::test_the_probe_is_skipped_once_the_handler_has_abandoned tests/test_feedback_egress_gate.py::test_the_creation_is_published_before_the_probe_starts tests/test_feedback_egress_gate.py::test_a_stall_inside_the_transport_after_dispatch_publishes_unconfirmed tests/test_feedback_egress_gate.py::test_the_body_read_is_bounded_by_its_own_budget tests/test_feedback_egress_gate.py::test_the_progress_record_is_safe_to_read_while_the_worker_runs tests/test_feedback_provenance.py::test_the_submission_outcome_field_defaults_to_none tests/test_feedback_egress.py::test_a_stall_after_dispatch_reports_unconfirmed_with_a_search_url tests/test_feedback_egress.py::test_a_creation_observed_before_the_timeout_is_reported_as_submitted tests/test_feedback_egress.py::test_a_stall_during_the_probe_does_not_lose_the_created_issue`, falsified by replacing `feedback_egress.submit_feedback_issue` with a function that genuinely blocks for 0.5 s and records `threading.get_ident()`, running a heartbeat task that increments a counter every `asyncio.sleep(0.01)` for the duration, and asserting both that the heartbeat ticked many times while the submission was in flight and that the recorded thread id is not the main thread's — a mock that returns immediately proves neither, and would pass unchanged against `main`'s inline `urlopen`; by monkeypatching `_FEEDBACK_SUBMIT_TIMEOUT_SECONDS` low against a submitter that sleeps past it and asserting the handler returns `ok=False, submitted=False` with a browser URL rather than hanging; and by a submitter that raises, asserting no `gh` spawn, no second HTTP attempt, and `submitted is False`. **Falsified at the act layer, which is what the plan panel's P2 found missing**: block the worker inside `submit_feedback_issue` on a `threading.Event` before the POST, let `fail_after` fire, assert the handler answered `submitted=False` with a browser URL, **then release the event, join the worker, and assert no POST request ever reached the opener** — with `abandon_on_cancel=True` the worker outlives the handler, so a build without the pre-dispatch deadline check files the issue after the operator was told nothing was submitted, and a manual submission through the returned URL then duplicates it. **Measuring only how quickly the handler returned passes against that build; the plan says so in SL-4 precisely because the obvious test misses the defect.** **Falsified at the progress layer, which the second panel's Q2(b) found missing**: stall the worker *inside* the transport after the dispatch check and assert the handler reports `dispatched_unconfirmed` with a **search** URL; and — the case that loses a fact pmcp had — let the POST succeed, publish `created`, then stall the visibility probe past the bound, and assert the handler reports `submitted=True` with the created issue's URL and `repository_visibility == "unknown"`, **not** `dispatched_unconfirmed`. With `abandon_on_cancel=True` the worker's return value is discarded, so without `FeedbackProgress` the only available answer is the wrong one. Falsified at the unit layer by calling `submit_feedback_issue` with an already-expired deadline (outcome `not_dispatched`, opener never called), with a deadline nearer than `_POST_PHASE_BUDGET_SECONDS` (also `not_dispatched`, because a POST that cannot finish inside the bound must not start), by a `socket.gaierror`/`ConnectionRefusedError` establishing `not_dispatched` rather than the pessimistic `dispatched_unconfirmed`, by asserting the created result is published *before* the probe begins, by a response body that trickles past its own budget being cut off rather than read forever, and by asserting `_POST_PHASE_BUDGET_SECONDS + _PROBE_PHASE_BUDGET_SECONDS < _FEEDBACK_SUBMIT_TIMEOUT_SECONDS` so a later edit to one constant cannot break the invariant silently. Falsified at the honesty layer by a POST whose bytes were sent and whose response never arrived: outcome `dispatched_unconfirmed`, `submission_outcome == "dispatched_unconfirmed"`, `issue_url` a **search** URL rather than a compose URL, and a message saying the issue may already exist — against an error *status*, which is a real negative, the outcome is `refused` instead. And by asserting the visibility probe runs only after a `created` POST, is skipped with no budget left, and is skipped once the handler has abandoned. **Falsified at the claim layer, which the third panel's R1 found missing**: stall the worker in the moment *before* `claim_dispatch`, let the handler's bound fire and answer, release the worker, join it, and assert the opener never saw a POST — the handler won the compare-and-set, so `claim_dispatch` returns `False` and the worker is forbidden to send. Without one atomic claim, the handler reads an empty slot, answers `not_dispatched` with a **compose** URL, and the worker then posts, so the operator files the issue twice; a lock that merely guards a snapshot slot does not prevent this. **Falsified at the classification layer, which the third panel's R2 found missing**: an establishing status (401/403/404/410/422 carrying `X-GitHub-Request-Id`) returns `refused` with a compose URL, while a 502 returns `dispatched_unconfirmed` with a **search** URL, as does the same 422 with the request-id header absent and a 2xx whose body does not yield an issue — an intermediary's 5xx can arrive after the request was forwarded and the issue created, so it is uncertain, not negative. **Fails on `main`**: `urlopen(repo_req, timeout=5)` (`:5059`) and `urlopen(req, timeout=10)` (`:5089`) execute inline inside `async def submit_feedback`, so the heartbeat stops for up to 15 s; there is no end-to-end bound at all; the probe runs *before* the post and spends its budget; nothing arbitrates between giving up and sending; and every failure — a lost response, a creation whose probe stalled, an intermediary's 502 — is reported identically as no submission.

## Verification

```bash
uv sync --all-extras -p 3.10      # a fresh worktree's `uv run` otherwise picks SYSTEM pytest

uv run pytest -q tests/test_feedback_provenance.py tests/test_feedback_egress_gate.py \
                tests/test_feedback_submission_flag.py tests/test_feedback_egress.py
uv run pytest -q tests/test_tools.py tests/test_provision_validation.py \
                tests/test_phase4_e2e.py tests/test_baseline_constraints.py tests/test_cli.py \
                tests/test_env_leak_229.py
uv run pytest -q tests/                 # compare counts to the pre-phase baseline, same dir
uv run ruff check src/ tests/ scripts/ && uv run ruff format --check src/ tests/ scripts/
uv run mypy src/
uv run python -c "import pmcp.feedback_egress"                    # importable

# EC-EGRESS-1, structurally: no GITHUB_TOKEN read survives anywhere on this path.
rg -n "os\.environ[.\[][^\n]*(GITHUB_TOKEN|GH_TOKEN)|getenv\(.(GITHUB_TOKEN|GH_TOKEN)" \
      src/pmcp/tools/handlers.py src/pmcp/feedback_egress.py
# ^ MUST be empty. Today it returns exactly one hit, handlers.py:5043 — the line
#   this phase deletes. The pattern matches a *read*, not the name: a bare
#   `rg GITHUB_TOKEN` also hits handlers.py:1080, the `env_vars` schema example in
#   register_discovered_server's tool definition, which this phase does not own and
#   must not remove. A lane that "fixes" that hit has crossed into PKGID's surface.

# EC-EGRESS-4, structurally: no blocking opener runs inline in this handler, and the
# only subprocess spawn left in the file is PKGID's update probe.
rg -n 'urlopen|urlencode|urllib' src/pmcp/tools/handlers.py
# ^ MUST be empty (both module imports at :18-19 are removed with the last use).
rg -n 'create_subprocess_exec' src/pmcp/tools/handlers.py
# ^ MUST be exactly one hit, the update-server probe (~:3769). The gh door is gone.
rg -n "shutil\.which\(.gh.\)|gh.*issue.*create" src/pmcp/tools/handlers.py
# ^ MUST be empty.

# The transport lives in exactly one place, and it is never awaited directly.
rg -n 'submit_feedback_issue' src/pmcp/tools/handlers.py
# ^ MUST be exactly two lines: the from-import (the frozen call site names the bare
#   symbol, so an import is REQUIRED) and the reference handed to
#   anyio.to_thread.run_sync. Corrected twice during execution: the original
#   'exactly one handlers.py use' was unsatisfiable, and a paren-anchored pattern
#   misses it too, because the call goes through functools.partial and the name is
#   never immediately followed by '('. What matters is that handlers.py never OPENS
#   a socket itself -- the urlopen/urlencode greps above assert that directly.

# The provenance registry is not clearable by anything an agent can reach.
rg -n 'reset_pmcp_introduced_keys' src/pmcp/
# ^ MUST be exactly one hit: the definition in env_store.py. Any src/ CALLER is a
#   production clear path, and server.py dispatches 26 tools, none of which may reach it.
rg -n 'record_pmcp_introduced_keys\(' src/pmcp/ --glob '!env_store.py'
# ^ MUST be exactly two CALL sites: the auth_connect call beside handlers.py:4972, and the
#   startup store-load delta in cli.load_startup_env. A MISSING hit is the second
#   panel's Q1 hole: a store key loaded at startup and then dropped from the file by an
#   unrelated auth_connect reads as operator-supplied.
#   env_store.py is excluded because the definition site also carries the name in a
#   section comment and in its own docstring example -- as record_dotenv_keys does
#   (:107, :124) -- so a count over the whole tree measures prose, not call sites.
#   Corrected during execution: the original 'exactly three hits' passed VACUOUSLY at
#   wave 1 (definition + comment + docstring, zero callers) and would have failed
#   spuriously at 5 once both real calls landed.
#   Corrected AGAIN during execution, by SL-3: without the trailing paren the pattern
#   still counted an import line and a docstring mention, so cli.py alone matched three
#   lines for one call. record_dotenv_keys shows the same shape: 5 matching lines, 2
#   call sites. Match the call, not the name.

# Every site that puts PMCP's own credential stores into PMCP's own environment records
# it. These are the only such sites; a new one added later must record too.
rg -n 'load_dotenv\(' src/pmcp/ --glob '!env_store.py'
# ^ MUST be: cli.py's plain-.env load (recorded via record_dotenv_keys), cli.py's two
#   store loads (recorded via record_pmcp_introduced_keys), and handlers.py's runtime
#   load (already recorded via record_dotenv_keys). Any unrecorded load is a provenance
#   hole. Corrected during execution by SL-4: the name-only pattern also matched a
#   `.. code-block:: python` example inside env_store's own docstring -- prose, not a
#   load. Match the call, and exclude the module whose docstrings describe it.
#   One prose match remains by design: cli.py's docstring sentence naming the bare
#   ``load_dotenv()`` it replaced. Four real loads, one prose line, and the prose line
#   sits inside the function the audit is about.

# The gate reads the STRICT lookup; the lenient one keeps only its existing caller.
rg -n 'managed_secret_keys\b' src/pmcp/
# ^ MUST NOT appear in feedback_egress.py. Its only src/ caller stays
#   env_store.sanitized_subprocess_env.

# The deadline reaches the transport, and the probe is not in front of the post.
rg -n 'deadline' src/pmcp/feedback_egress.py src/pmcp/tools/handlers.py
# ^ MUST show the handler computing one monotonic deadline and the submitter checking
#   it immediately before the POST dispatch.

# The handler reads what the abandoned worker learned, rather than only its result.
rg -n 'progress' src/pmcp/tools/handlers.py
# ^ MUST show a FeedbackProgress constructed before the run_sync and read on the
#   TimeoutError path. Without that read, a POST that succeeded is reported as
#   unconfirmed whenever the probe after it stalls -- the second panel's Q2(b).
rg -n 'claim_dispatch|abandon\(' src/pmcp/feedback_egress.py src/pmcp/tools/handlers.py
# ^ MUST show claim_dispatch called in feedback_egress.py as the LAST step before the
#   opener, its result gating the dispatch, and abandon() called on handlers.py's
#   TimeoutError path. A handler that calls latest() there instead has the R1 race: it
#   can answer not_dispatched with a compose url while the worker goes on to post.
rg -n 'publish\(' src/pmcp/feedback_egress.py
# ^ MUST show a publish immediately after the response is classified, BEFORE the
#   visibility probe runs.
rg -n '401|403|404|410|422|X-GitHub-Request-Id' src/pmcp/feedback_egress.py
# ^ MUST show the establishing-status ALLOWLIST and the request-id header check. A
#   classifier that maps every non-2xx to `refused` hands back a compose url after an
#   intermediary's 502, which may follow a created issue.

# EGRESS does not cross into a sibling phase's files.
git diff --name-only origin/main..HEAD -- src/pmcp/policy/ src/pmcp/manifest/ \
    src/pmcp/provision_gate.py src/pmcp/trust_store.py src/pmcp/validation.py \
    tests/conftest.py
# ^ MUST be empty. A non-empty result means a lane crossed the phase boundary.
#   Corrected during execution by SL-4: src/pmcp/types.py was dropped from this list
#   because IF-0-EGRESS-1 now MANDATES the one optional SubmitFeedbackOutput field, and
#   SL-1 owns the file. The check predated that correction and would have flagged the
#   phase's own frozen interface as a boundary crossing.

python3 scripts/check_plan_consistency.py plans/phase-plan-v13-EGRESS.md
```

Host note: `/tmp/package.json` makes ~107 npm-identity tests fail on some hosts
(`tests/conftest.py`); compare the same command from the same directory before and
after, never against a clean-machine expectation.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/tools/handlers.py`
- evidence paths: `tests/test_feedback_egress.py`, `CHANGELOG.md`
- redaction posture: `metadata_only`
- missing or malformed evidence routes to `blocker_class=contract_bug` (non-human).
- downstream handling: the roadmap's evidence paths name only
  `tests/test_feedback_egress.py`, and its Key files name only that file and
  `src/pmcp/tools/handlers.py`. The criteria are actually proven across four
  test files and six source files (`src/pmcp/feedback_egress.py`, `src/pmcp/env_store.py`,
  `src/pmcp/types.py`, `src/pmcp/config/guidance.py`, `src/pmcp/cli.py`,
  `src/pmcp/tools/handlers.py`).
  This is the same gap PKGID amendment 11(e) records; SL-docs.3 records it as a
  post-execution amendment, and SEAL's closeout should collect all four test files.

## Execution Policy

- default: effort=medium
- SL-1: effort=medium, reason=four small additive names, but every later lane codes against them and a wrong provenance shape reopens EC-EGRESS-1
- SL-2: effort=high, reason=fail-closed ordering in a nine-branch predicate where a wrong default publishes an operator's diagnostics to a third party, plus a transport whose deadline bounds an act that outlives its caller
- SL-3: effort=low, reason=one config field, one setter, one CLI flag and one recorded delta, each mirroring an existing one beside it
- SL-4: effort=high, reason=sole writer of the roadmap's single-writer file, the lane that deletes two egress doors, and the three-way timeout path
- SL-docs: effort=minimal, reason=docs sweep plus one roadmap amendment
