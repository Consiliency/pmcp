# Detailed plan: poll-until conversion — slice C2a of Consiliency/pmcp#235 (the sleep-then-assert sites + the two tautological lower bounds)

> **Bounded-plan verdict: slice C2 EXCEEDS the threshold and is split.** The
> C1 plan's C2 inventory names 12 files; verified against `main` at `11d7a8e`
> the real footprint is **15 files and ~47 call sites** (see "Scale check"
> below), against the skill's ~8-file / ~3-concern ceiling. This document plans
> **C2a** — the highest-value cut: every site where a *fixed* sleep stands in
> front of an assertion (the ones a loaded runner actually turns red), plus
> the two tautological `elapsed >` lower bounds C1 asked C2 to replace with a
> static relation — across **6 test files + 1 doc**, touching no `src/`.
> **C2b** (pure consolidation of the suite's already-bounded private pollers,
> 7 files) and **C2c** (the two "work inside a wall-clock window" redesigns,
> 3 files) are named at the end with per-file inventories so each can be its
> own bounded plan against the same frozen helper.
>
> **Why this cut.** The inventory has three shapes with very different risk:
> (1) `sleep(N)` then `assert` — the flake class; a starved runner fails it.
> (2) `for _ in range(K): if cond: break; sleep(i)` — already a deadline
> poller; converting it is consolidation, not de-flaking. (3) "count ticks in
> a real window" — needs a *different* proof shape, not `eventually`. C2a is
> shape (1) plus the static-constant swap; C2b is shape (2); C2c is shape (3).
> Files are never split across sub-slices, which is why
> `test_listen_over_http.py`'s two shape-(2) loops ride along in C2a.

## Task

Slice C2 of Consiliency/pmcp#235 (see #235): convert the suite's "sleep N then
assert" sites to `eventually` from `tests/_timing.py` (frozen API landed by
slice C1, PR #268), fold the remaining private deadline pollers into the
shared helper, and replace the tautological lower bounds `elapsed > 8` /
`elapsed > 12` with a static relation between the sleep constant and the
configured `request_timeout`. Test infrastructure only; no `src/` change.

The rule (from `tests/_timing.py`'s module docstring and `CONTRIBUTING.md`
"Timing in tests", `:35-44`): lower bounds on a real timer are safe and stay;
upper bounds on elapsed wall time are the flaky class and are replaced by the
property they stood in for; `timeout=` on the helpers is a hang guard, never
an assertion.

## Research summary

Every line number below was read this session against `main` @ `11d7a8e`
(worktree `plan/235-slice-c2-poll-until`). The C1 plan's inventory
(`.consiliency/plans/detailed-deterministic-test-timing-20260920-0846.md:461-499`)
was checked site by site; its line numbers are 3–4 lines stale in
`test_manifest.py` (C1 added an import) and otherwise accurate.

**The helper is frozen and fit.** `tests/_timing.py` exports exactly
`eventually` (async; predicate evaluated at least once *before* the deadline
is consulted; predicate exceptions propagate immediately; sleeps via
`asyncio.sleep`), `eventually_sync`, and `Rendezvous`. `tests/__init__.py`
exists, so `from tests._timing import eventually` works from
`tests/mcp2x/` (no `__init__.py` there, but `test_catalog_publishers.py:49`
already imports `tests.runtime.harness` the same way). Precedent for the
import style: `tests/test_client_manager.py:53`.

**Scale check (cheap counts, run before deep reconnaissance).** Files with
≥1 conversion site: `test_manifest.py` (7), `test_tools.py` (1, in a
subprocess source string), `test_client_manager.py` (3),
`runtime/harness.py` (1), `test_credential_boot.py` (1),
`runtime/test_emitter_harness.py` (8), `mcp2x/test_subscription_contract.py`
(7 + 1 keep), `test_http_dos.py` (2), `mcp2x/test_listen_over_http.py` (2 +
static relation), `mcp2x/test_catalog_publishers.py` (1 helper, 4 callers),
`test_client_manager_reconnect.py` (2), `test_egress_panel_fixes.py` (1),
`mcp2x/test_listen_registration.py` (1), `test_package_identity_gate.py` (1
window), `runtime/test_subscriptions_e2e.py` (static relation), and — **not
in the C1 inventory** — `runtime/fake_remote.py:254-259` (a startup poll
identical to `test_http_dos.py:140-145`) and `test_feedback_egress.py:631/641`
(a second "ticker in a wall-clock window", `ticks > 5` during a 0.4 s
`time.sleep`, the same class as `test_package_identity_gate.py:983`). That is
15–17 files; the threshold trips.

**What C2a's sites actually wait for (the deterministic property behind each
sleep).**

- `tests/test_manifest.py` — `JobManager.start_install`
  (`src/pmcp/manifest/installer.py:167-240`) sets `job.status = "installing"`
  and `job.last_heartbeat` synchronously at `:220-221`, *after* the awaited
  `create_subprocess_exec` at `:210`, and creates `_monitor_task` at `:224`
  with no further await — so every "give it time to start" sleep before a
  `cancel_job` waits for state that already holds when `start_install`
  returns. `cancel_job` (`:545-568`) awaits `process.wait()` at `:557` before
  returning, so `process.returncode is not None` also already holds; the only
  genuinely asynchronous fact is `_monitor_task` finishing after
  `_monitor_task.cancel()` at `:566`. **Two traps:** (a) `_monitor_install`
  *catches* `CancelledError` at `:454-459` and returns normally, so
  `monitor_task.cancelled()` is never True — `done()` is the only reachable
  terminal signal; (b) that same handler overwrites `job.error` with
  `"Installation cancelled"` on the monitor's next turn, so
  `test_cancel_job_sets_failed_status`'s `job.error == "Cancelled by user"`
  (`:1975`) passes only because no await sits between `cancel_job` and the
  assertion — **do not insert one**. The heartbeat test's
  `time.time() - job.last_heartbeat < 5` (`:1660`) is kept per the C1
  decision, but it is *tautological* — `last_heartbeat` is initialised at
  `:221` moments earlier, so it holds whether or not the monitor ever
  updates it (`:388`); C2a adds the assertion the docstring actually claims.
- `tests/test_tools.py:4736-4784` (`_SCENARIO`, class
  `TestUpdateProbeProcessCleanup` at `:4716`) — after
  `_run_update_probe_command` (`src/pmcp/tools/handlers.py:3766-3820`) times
  out it awaits `_terminate_process_tree` (`:3809`), which returns once
  `killpg(pgid, 0)` fails (`src/pmcp/client/manager.py`, `_group_alive`
  loop). The grandchild's `/proc/<pid>` entry disappears when init reaps the
  reparented process, which is asynchronous to that; the `sleep(0.5)` at
  `:4772` is a grace window. The script is stdlib-only by design
  (`:4727-4733`), so the poll is written inline in the script text.
- `tests/mcp2x/test_subscription_contract.py` — the sink
  (`src/pmcp/subscriptions.py:104-194`) self-schedules a drain task from
  `_note` (`:126`), clears/re-arms in `_on_drain_done` (`:135-158`), and
  isolates a raising bus in `_publish` (`:187-194`). Every sleep here waits
  for that drain task to have run; `bus.published` / `bus.calls` on the
  three recording buses (`:40-84`) are the observable. `:263` is different:
  it proves the *naive* sink drops the second event — a negative with no
  positive signal to poll for — and stays.
- `tests/mcp2x/test_listen_registration.py:283-286` — after
  `notifications/cancelled` for id 1 the SDK cancels that handler task, whose
  `finally` (`mcp/server/subscriptions.py:236-240`, mcp 2.x) unsubscribes
  from the bus. The bus's listener table `InMemorySubscriptionBus._listeners`
  (`:100`) shrinks by one; that is the pollable property. It is SDK-private,
  but the test already reaches pmcp-private `gw._subscription_bus` /
  `gw._listen_handler`, and a rename fails loudly (AttributeError), never
  falsely green — the concern `test_listen_over_http.py:20-25` raises about
  `ListenHandler._streams` is about *asserting* through a private, which this
  is not (the assertion stays on the wire frame at `:291-294`).
- `tests/mcp2x/test_listen_over_http.py:212-240` and
  `tests/runtime/test_subscriptions_e2e.py:127-235` — `elapsed > 8` after
  `sleep(8.2)` and `elapsed > 12` after `sleep(13)` cannot fail; the property
  is "the sleep exceeds the configured `request_timeout`" (3 and 5), which
  is a relation between two constants and belongs at import time. The
  exemption they guard is `src/pmcp/transport/http.py:696`
  (`if body_method == "subscriptions/listen":`).

**Corrections to the C1 inventory.** (1) `test_subscription_contract.py:263`
is a negative soak, not a conversion target. (2) The `<5` heartbeat check
C1 called "legitimate freshness" is tautological (above); kept, but no
longer the test's only assertion. (3) `test_manifest.py`'s four pre-cancel
sleeps convert to *deletion*, not to `eventually` — the state is already set.
(4) Two sites missing from C1's list: `runtime/fake_remote.py:254-259`
(→ C2b) and `test_feedback_egress.py:631/641` (→ C2c). (5) `_gone` in
`test_client_manager.py:3637/:3691` is a bounded poller (shape 2), assigned
to C2c with the file's debounce site so the file is edited once.

**Environment facts that shape verification.** pytest-asyncio 1.3.0,
`asyncio_mode = "auto"`, `addopts = "-m 'not live'"` (`pyproject.toml`).
`tests/runtime/test_subscriptions_e2e.py` is *not* `live`-marked and runs by
default (19.7 s; it boots an isolated gateway and only *observes* a `:3344`
gateway if one is up — `harness.py:203-208`). Ruff runs the default rule set
(no `[tool.ruff]` in `pyproject.toml`), so an unused `import time` is an
F401. Baseline: the six C2a files pass 46/46 in 34 s from this worktree.
A full `uv run pytest -q` from a worktree under
`/mnt/HC_Volume_105438154` shows ~107 spurious npm-identity failures from
`/mnt/HC_Volume_105438154/node_modules` (memory note; C1 plan Verification
preamble) — run the full-suite check from a checkout outside that volume.

## Changes

### `tests/test_manifest.py` (modify)

- imports (`:5-12`) — add — `from tests._timing import eventually` (keep
  `time`: the retained `<5` check uses it).
- `TestMonitorInstall.test_monitor_updates_heartbeat_on_output` (`:1637-1660`)
  — modify — move `job = manager.get_job(job_id)` / `assert job is not None`
  to directly after `start_install`; capture `hb0 = job.last_heartbeat`;
  replace `await asyncio.sleep(0.2)` (`:1654`) with the file's own precedent
  from `test_monitor_reads_stderr` (`:1680-1683`): `assert job._monitor_task
  is not None; await asyncio.wait_for(job._monitor_task, timeout=5.0)`; add
  `assert job.last_heartbeat > hb0, "stdout output did not refresh
  last_heartbeat"` (the docstring's actual claim — `installer.py:388`);
  keep `assert time.time() - job.last_heartbeat < 5` (`:1660`) unchanged.
  Strict `>` is safe: at least one event-loop turn and a pipe read separate
  the two `time.time()` reads (`installer.py:221` vs `:388`).
- `TestMonitorInstall.test_monitor_cancellation_cleanup` (`:1793-1822`) —
  modify — delete `# Give it time to start` + `await asyncio.sleep(0.1)`
  (`:1808-1809`); `job.status == "installing"` (`:1813`) is set at
  `installer.py:220` before `start_install` returns. No other change.
- `TestCancelJob.test_cancel_job_terminates_process` (`:1892-1920`) — modify
  — delete the pre-cancel `await asyncio.sleep(0.1)` (`:1909`) and the
  post-cancel `# Process should be terminated` / `await asyncio.sleep(0.1)`
  (`:1918-1919`); keep `assert process is None or process.returncode is not
  None` immediately after `cancel_job` — `installer.py:557` awaits
  `process.wait()` before returning, so this is a "must already be true"
  assertion.
- `TestCancelJob.test_cancel_job_cancels_monitor_task` (`:1922-1950`) —
  modify — delete the pre-cancel sleep (`:1938`); replace `# Wait for
  cancellation to complete` / `await asyncio.sleep(0.2)` / the
  `cancelled() or done()` assertion (`:1946-1950`) with
  `await eventually(lambda: monitor_task is None or monitor_task.done(),
  timeout=5.0, message="monitor task still running after cancel_job")`.
  Add a one-line comment: `cancelled()` is unreachable because
  `_monitor_install` catches `CancelledError` (`installer.py:454`).
- `TestCancelJob.test_cancel_job_sets_failed_status` (`:1952-1975`) — modify
  — delete the pre-cancel sleep (`:1968`) **only**. Keep `cancel_job` →
  `get_job` → `job.error == "Cancelled by user"` with no await between them,
  and add a one-line comment saying why (`installer.py:454-459` overwrites
  `job.error` on the monitor's next turn).

### `tests/mcp2x/test_subscription_contract.py` (modify)

- imports (`:18-35`) — add — `from tests._timing import eventually`.
- `test_note_inside_running_loop_self_schedules_a_drain_with_no_flush_call`
  (`:173-182`) — modify — replace `await asyncio.sleep(0.05)` (`:180`) with
  `await eventually(lambda: bus.published, message="note_* inside a running
  loop never self-scheduled a drain")`; keep `assert bus.published ==
  [ToolsListChanged()]`.
- `test_note_during_a_suspended_publish_is_not_stranded` (`:188-218`) —
  modify — replace the `for _ in range(50): … await asyncio.sleep(0.01)` loop
  (`:212-216`, comment included) with `await eventually(lambda:
  len(bus.published) >= 2, message="the prompts event noted during the
  suspended publish was stranded")`; keep the equality assert (`:218`).
- `test_naive_snapshot_and_exit_drain_fails_the_lost_wakeup_regression`
  (`:220-268`) — **no change** — `await asyncio.sleep(0.1)` at `:263` is a
  negative soak proving the naive sink never delivers the second event; add
  a one-line comment naming it as such so the next sweep does not "convert"
  it.
- `test_raising_bus_is_isolated_and_the_sink_still_works_afterward`
  (`:274-288`) — modify — `:279` → `await eventually(lambda: bus.calls >= 1)`
  (keep the trailing comment's meaning: the drain must not propagate the
  exception — `eventually` re-raises a predicate exception, so a propagating
  publish would still surface); keep `assert bus.calls == 1`. `:286` →
  `await eventually(lambda: bus.calls >= 2, message="the second note after a
  raising publish never drained -- _draining was left set")`; keep
  `assert bus.calls == 2`.
- `test_cancellation_before_the_drain_starts_does_not_wedge_the_sink`
  (`:398-426`) — modify — replace the sleep-first `for _ in range(100)` loop
  and the trailing assert (`:418-426`) with one
  `await eventually(lambda: any(isinstance(e, ToolsListChanged) for e in
  bus.published), message="pre-start cancellation stranded the event")`.
  The `{bus.published}` dump in the old message is lost (the helper's
  `message` is static); acceptable — the property name is the diagnostic.
- `test_event_noted_during_a_cancelled_drain_is_not_stranded` (`:429-459`)
  — modify — same shape at `:450-457`, predicate on `PromptsListChanged`,
  message "event noted during a cancelled drain was stranded"; keep
  `assert sink._draining is False` (`:459`).
- `test_cancelled_drain_does_not_wedge_the_sink` (`:462-490`) — modify —
  replace the `for _ in range(50)` loop + assert (`:486-490`) with one
  `eventually` on `PromptsListChanged`, message "a fresh note after a
  cancelled drain was not delivered".

### `tests/mcp2x/test_listen_registration.py` (modify)

- imports (`:20-39`) — add — `from tests._timing import eventually`. `anyio`
  stays imported (`fail_after` `:88`, memory streams `:102-105`, task group
  `:108`).
- `test_cancelled_notification_ends_subscription` (`:259-294`) — modify —
  before `await duplex.send(_cancelled_notification(1))` (`:283`) capture
  `listeners = gw._subscription_bus._listeners` and `before =
  len(listeners)`; replace the comment + `await anyio.sleep(0.2)`
  (`:284-286`) with `await eventually(lambda: len(listeners) == before - 1,
  message="cancelling listen id 1 never unsubscribed it from the bus")`.
  Comment: SDK-private table, read as a *sequencing* signal only — the
  assertion stays on the wire frame (`:291-294`); a rename is a loud
  AttributeError, not a false green.

### `tests/test_tools.py` (modify)

- `TestUpdateProbeProcessCleanup._SCENARIO` (`:4736-4784`, a source string
  executed in a child interpreter) — modify — extend the script's import line
  (`:4736`) to `import asyncio, os, sys, tempfile, time`; replace
  `await asyncio.sleep(0.5)` (`:4772`) and the one-shot `/proc` check
  (`:4777`) with, after the `pidfile` read/unlink and the `SETUP-FAILED`
  guard (`:4773-4776`):
  ```python
      # Hang guard, not a measurement: the probe has already awaited
      # _terminate_process_tree; init's reap of the reparented grandchild is
      # what we wait for. Stdlib only -- this runs in a child interpreter.
      deadline = time.monotonic() + 5.0
      while os.path.exists("/proc/" + pid) and time.monotonic() < deadline:
          await asyncio.sleep(0.05)
      print("ORPHANED" if os.path.exists("/proc/" + pid) else "REAPED")
  ```
  The in-script `time.sleep(30)` at `:4757/:4759` are the hung parent and
  grandchild themselves — keep. The test body (`:4800-4805`) is unchanged.

### `tests/mcp2x/test_listen_over_http.py` (modify)

- imports (`:30-48`) — modify — add `from tests._timing import eventually`;
  **remove `import time`** (`:34`; its four uses at `:219, :238, :270, :273` all go
  below; leaving it is an F401).
- module constants — add — directly after the imports:
  ```python
  # The timeout-exemption proof (IF-0-P3B-3) rests on one relation: the
  # publish is delayed past the app's request_timeout, so a notification that
  # still arrives can only mean the listen stream survived it. A sleep never
  # returns early, so the relation is between two constants and is checked
  # here, at import time, not by timing the test.
  _REQUEST_TIMEOUT_S = 3
  _SLEEP_PAST_TIMEOUT_S = 8.2
  assert _SLEEP_PAST_TIMEOUT_S > _REQUEST_TIMEOUT_S, (
      "the pre-publish sleep must exceed request_timeout or the exemption "
      "test proves nothing"
  )
  ```
- `_run_listen_app` (`:100-128`) — modify — replace the `for _ in range(200)
  … else: raise RuntimeError` startup poll (`:117-122`) with
  `await eventually(lambda: uv_server.started, timeout=10.0, interval=0.05,
  message="listen app never started")`.
- `TestTimeoutExemption.test_timeout_exemption_keeps_stream_alive`
  (`:212-240`) — modify — `_run_listen_app(request_timeout=_REQUEST_TIMEOUT_S)`;
  delete `start = time.monotonic()` (`:219`); `await
  asyncio.sleep(_SLEEP_PAST_TIMEOUT_S)` (`:234`); delete `elapsed = …` and
  `assert elapsed > 8, elapsed` (`:238-239`); keep `assert frame["method"]
  == "notifications/tools/list_changed"` (`:240`). Reword the comment at
  `:231-233` and the docstring's "asserted at t > 8s" (`:216`) to point
  at the module constants.
- `TestClientCloseReleasesSlot.test_client_close_ends_subscription`
  (`:246-302`) — modify — replace `deadline = time.monotonic() + 10.0` /
  `last: object = None` / the `while` retry loop / `pytest.fail(...)`
  (`:270-302`; `aclose()` stays at `:265`) with:
  ```python
  last: object = None

  async def _b_is_acked() -> bool:
      nonlocal last
      # Streamed, not `client_b.post(...)`: (keep the existing rationale
      # comment from :274-278 here)
      async with client_b.stream(
          "POST", f"{running.base_url}/mcp", headers=headers_b, json=body_b
      ) as response_b:
          message = await _first_message(response_b, timeout=3.0)
      last = message
      return message.get("method") == "notifications/subscriptions/acknowledged"

  async with httpx.AsyncClient(timeout=None) as client_b:
      try:
          await eventually(_b_is_acked, timeout=10.0, interval=0.2)
      except AssertionError:
          raise AssertionError(
              "subscription B was never acked after A's client disconnect "
              f"(max_subscriptions=1); last response was {last!r}"
          ) from None
  ```
  Note `_first_message`'s own `asyncio.wait_for` raises
  `asyncio.TimeoutError` on 3.10 (not the builtin), and `eventually`
  propagates predicate exceptions — the *existing* loop has the same
  propagation, so behaviour is unchanged; do not add a swallow.

### `tests/runtime/test_subscriptions_e2e.py` (modify)

- module constants — add — after the imports: `_REQUEST_TIMEOUT_S = 5`,
  `_SLEEP_PAST_TIMEOUT_S = 13`, and the same import-time `assert
  _SLEEP_PAST_TIMEOUT_S > _REQUEST_TIMEOUT_S` with a message. Keep
  `import time` (`_read_until` at `:100-103` uses it).
- `test_connect_disconnect_refresh_each_deliver_all_three_kinds`
  (`:127-235`) — modify — `booted_gateway(request_timeout=_REQUEST_TIMEOUT_S,
  …)` (`:138-140`); delete `start = time.monotonic()` (`:141`); `await
  asyncio.sleep(_SLEEP_PAST_TIMEOUT_S)` (`:212`); delete `elapsed = …` and
  the `assert elapsed > 12, (…)` block (`:220-226`). Reword the comment at
  `:206-211` and the module docstring sentence "pushes the refresh step's
  notification past t=12s" (`:37-38`) to name the constants and the
  import-time relation. The `_read_until(... budget=10.0)` calls after
  `gateway.refresh` (`:214-217`) are the surviving proof that the stream is
  still delivering after the timeout window.

### `CHANGELOG.md` (modify)

- `## [Unreleased]` → `### Fixed` (`:369`) — add — one bullet after C1's
  entry (`:370`): *"Tests: the fixed `sleep`-then-assert sites in the
  install-job, subscription-sink, listen-registration and update-probe tests
  now wait for the property (`eventually`), and the two tautological
  `elapsed >` bounds in the listen-timeout tests are a static
  sleep-vs-`request_timeout` relation checked at import time (slice C2a). See
  [#235](https://github.com/Consiliency/pmcp/issues/235)."* Do not add a
  new heading; never write a closing keyword near `#235`.

## Documentation impact

- `CHANGELOG.md` — modify — as above. `CONTRIBUTING.md`'s "Timing in tests"
  already states the rule (`:35-44`); nothing to add. No `README`/`docs/`
  footprint: test-only change.

## Dependencies & order

1. Nothing blocks: `tests/_timing.py` is on `main` (PR #268) and its API is
   frozen. Do **not** touch `tests/conftest.py` (slices A/B own it) or
   `tests/_timing.py`.
2. Edit order is free; suggested: `test_manifest.py` first (the two traps live
   there — verify the cancel-ordering trap by running the file after each
   edit), then `test_subscription_contract.py`, `test_listen_registration.py`,
   `test_tools.py`, then the two static-relation files together, then
   `CHANGELOG.md`.
3. Coordination: `test_package_identity_gate.py`, `test_feedback_egress.py`
   and `test_client_manager.py` are **not** in C2a precisely because PKGID /
   EGRESS agents are active this session; C2c must rebase on whatever they
   land.
4. Branch/PR discipline: commit as `test: …` with `see Consiliency/pmcp#235`;
   never `fixes`/`closes`/`resolves` near a `#N`, including the PR title.

## Verification

From the worktree, after `uv sync -p 3.10` (else `uv run` silently uses the
system pytest). Subset runs need `--cov-fail-under=0`.

```bash
W=/mnt/HC_Volume_105438154/worktrees/pmcp-plan-235-c2   # or wherever C2a is implemented
cd "$W"
C2A="tests/test_manifest.py tests/mcp2x/test_subscription_contract.py \
     tests/mcp2x/test_listen_registration.py tests/mcp2x/test_listen_over_http.py \
     tests/runtime/test_subscriptions_e2e.py"
ORPHAN="tests/test_tools.py::TestUpdateProbeProcessCleanup"

# 1. Lint exactly as CI does (ruff defaults; an unused `import time` is F401).
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/

# 2. The six files, green, under coverage instrumentation (baseline 46 tests
#    in the touched classes; whole files here). Expect ~35 s.
uv run pytest $C2A $ORPHAN --cov=pmcp --cov-report= --cov-fail-under=0 -q -p no:cacheprovider

# 3. No fixed SLEEP-BEFORE-ASSERT remains in the six files. Polling INTERVALS
#    are not that class and must be allowed: `eventually(interval=...)` sleeps,
#    and the orphan scenario's own poll uses `await asyncio.sleep(0.05)`. A
#    check that forbade them would fail a correct implementation -- three panel
#    seats independently flagged the earlier form, which did exactly that.
#    Expected output: exactly the three lines below, nothing else.
#      tests/mcp2x/test_subscription_contract.py:<n>: ... asyncio.sleep(0.1)   (negative soak)
#      tests/test_tools.py:<n>/<n+2>: ... time.sleep(30)                       (the hung parent/grandchild, in-script)
#      tests/test_tools.py:<n>: ... asyncio.sleep(0.05)                        (the orphan poll's INTERVAL)
grep -nE "(asyncio|anyio|time)\.sleep\(\s*[0-9]*\.?[0-9]+\s*\)" $C2A tests/test_tools.py \
  | grep -vE "sleep\(\s*0\s*\)" | grep -vE "_SLEEP_PAST_TIMEOUT_S"
#    Then assert the interval is a POLL interval, not a bare wait: every
#    remaining sub-second sleep must sit inside a `while`/deadline loop.
#    Inspect each of the three hits by eye; there are only three.
#    and the two tautological bounds are gone (both greps print nothing):
grep -n "assert elapsed" tests/mcp2x/test_listen_over_http.py tests/runtime/test_subscriptions_e2e.py
grep -n "^import time" tests/mcp2x/test_listen_over_http.py

# 4. Starved-CPU rehearsal: one core, two busy loops, three passes. This is
#    the load profile behind Consiliency/pmcp#226 and the reason the old
#    sleeps were flaky. Expect 3/3 green.
#    NOTE: the status must PROPAGATE. An earlier form ended with `kill %1 %2`
#    after a `break`, so the compound command exited 0 even when a pass failed
#    -- a verification step that could not fail (codex, blocking). The busy
#    loops are pinned to the SAME core as pytest, or they do not contend.
(
  taskset -c 0 yes >/dev/null & B1=$!
  taskset -c 0 yes >/dev/null & B2=$!
  sleep 1
  rc=0
  for i in 1 2 3; do
    taskset -c 0 uv run pytest $C2A $ORPHAN -q -x --cov-fail-under=0 -p no:cacheprovider \
      || { echo "FAIL pass $i"; rc=1; break; }
  done
  kill $B1 $B2 2>/dev/null
  echo "starvation rehearsal rc=$rc   # MUST be 0"
  exit $rc
)

# 5. MUTATIONS -- each must turn the named test RED. Apply one at a time,
#    run, then `git checkout -- src/ tests/`.
#
# 5a. installer.py:388  comment out `job.last_heartbeat = time.time()`
#     -> test_monitor_updates_heartbeat_on_output fails on "did not refresh last_heartbeat".
uv run pytest tests/test_manifest.py -k test_monitor_updates_heartbeat_on_output -q --cov-fail-under=0
# 5b. installer.py:556-560  delete the `try: await asyncio.wait_for(job.process.wait(), timeout=2.0) ... ` block
#     (leave `job.process.terminate()` at :553)
#     -> test_cancel_job_terminates_process fails: returncode is still None when cancel_job returns.
uv run pytest tests/test_manifest.py -k test_cancel_job_terminates_process -q --cov-fail-under=0
# 5c. installer.py:548-549  add `return True` right after the `if not job: return False` guard
#     -> test_cancel_job_cancels_monitor_task fails after the 5 s hang guard with
#        "monitor task still running after cancel_job" (the monitor is parked in
#        asyncio.wait(..., timeout=HEARTBEAT_TIMEOUT=120)). Kill the leftover `sleep 30`.
uv run pytest tests/test_manifest.py -k test_cancel_job_cancels_monitor_task -q --cov-fail-under=0
# 5d. subscriptions.py:126  comment out `self._start_drain(loop)` in `_note`
#     -> test_note_inside_running_loop_self_schedules_a_drain_with_no_flush_call fails
#        "never self-scheduled a drain" in ~5 s (and the raising-bus test's first eventually).
uv run pytest tests/mcp2x/test_subscription_contract.py -k "self_schedules or raising_bus_is_isolated" -q --cov-fail-under=0
# 5e. subscriptions.py:152  delete `self._draining = False` in `_on_drain_done`
#     -> test_raising_bus_is_isolated_and_the_sink_still_works_afterward fails on the
#        second eventually ("_draining was left set"); the three cancellation tests fail too.
uv run pytest tests/mcp2x/test_subscription_contract.py -k "raising_bus_is_isolated or wedge or stranded" -q --cov-fail-under=0
# 5f. subscriptions.py  delete the RE-ARM branch in `_on_drain_done` (the
#     `if not self._pending: return` / `_start_drain(...)` block, around :152-158)
#     -> test_event_noted_during_a_cancelled_drain_is_not_stranded fails.
#
#     THE TARGET TEST MATTERS. The sink is defended in depth, so no single-line
#     mutation reddens `test_note_during_a_suspended_publish_is_not_stranded`:
#       * `while`->`if` in `_drain_pending` is defeated by the re-arm, and
#       * deleting the re-arm is defeated by the `while` loop, which re-checks
#         `_pending` when the suspended publish resumes.
#     Revision 1 named the first, revision 2 named the second, and BOTH left that
#     test green (grok and codex, independently, both blocking). Pick a test where
#     only ONE path exists: in the CANCELLED-drain case the loop cannot run at all,
#     so the re-arm is the sole route and deleting it strands the event -- which is
#     exactly what `_on_drain_done`'s docstring says it is there to prevent.
uv run pytest tests/mcp2x/test_subscription_contract.py::test_event_noted_during_a_cancelled_drain_is_not_stranded -q --cov-fail-under=0
#     To falsify the suspended-publish test specifically, apply BOTH mutations
#     together; note it as a two-line mutation, not one.
uv run pytest tests/mcp2x/test_subscription_contract.py -k suspended_publish -q --cov-fail-under=0
# 5g. handlers.py:3794  delete `start_new_session=True` in `_run_update_probe_command`
#     -> the scenario prints ORPHANED after its 5 s guard; the test fails "did not reap".
uv run pytest $ORPHAN -q --cov-fail-under=0
# 5h. http.py:696  change `"subscriptions/listen"` to `"subscriptions/never"`
#     -> test_timeout_exemption_keeps_stream_alive fails (frame never arrives; the stream
#        is truncated at 3 s), and test_subscriptions_e2e fails in _read_until after refresh.
#     Run BOTH by explicit node id: `-k timeout_exemption` applies to every path
#     given, so it deselected the runtime test entirely (codex, blocking).
#     `test_timeout_exemption_keeps_stream_alive` is a METHOD of
#     `class TestTimeoutExemption`, so the bare function-style node id selects
#     nothing and pytest exits 4 every time -- green-looking for the wrong reason
#     (grok, blocking). Validated with `--collect-only`: the two ids below collect
#     exactly 2 tests.
uv run pytest \
  "tests/mcp2x/test_listen_over_http.py::TestTimeoutExemption::test_timeout_exemption_keeps_stream_alive" \
  "tests/runtime/test_subscriptions_e2e.py::test_connect_disconnect_refresh_each_deliver_all_three_kinds" \
  -q --cov-fail-under=0
# 5i. test_listen_over_http.py  set `_SLEEP_PAST_TIMEOUT_S = 2.5`;
#     test_subscriptions_e2e.py  set `_SLEEP_PAST_TIMEOUT_S = 4`
#     -> each module fails at COLLECTION with the relation's message (0 s, no test runs).
uv run pytest tests/mcp2x/test_listen_over_http.py tests/runtime/test_subscriptions_e2e.py --collect-only -q
# 5j. test_listen_registration.py  send `_cancelled_notification(999)` instead of `(1)`
#     -> fails "never unsubscribed it from the bus" after the 5 s guard, by name, rather than
#        on the wrong frame after a 0.2 s sleep.
uv run pytest tests/mcp2x/test_listen_registration.py -k cancelled_notification -q --cov-fail-under=0
# 5k. test_listen_over_http.py  replace `await client_a.aclose()` with `pass`
#     -> test_client_close_ends_subscription fails after 10 s with "never acked ... last response was {...}".
uv run pytest tests/mcp2x/test_listen_over_http.py -k client_close -q --cov-fail-under=0
git checkout -- src/ tests/

# 6. Full suite, from a checkout NOT under /mnt/HC_Volume_105438154 (the
#    volume-root node_modules produces ~107 spurious npm-identity failures).
uv run pytest -q
```

Edge cases to check by hand while implementing:

- `test_cancel_job_sets_failed_status`: after deleting the pre-cancel sleep,
  confirm there is still **no await** between `cancel_job` and the
  `job.error` assertion (trap (b) above); the file's own run is the check.
- `test_monitor_updates_heartbeat_on_output`: `hb0` must be read *before*
  any await follows `start_install` — the monitor task runs on the first
  yield.
- `_SCENARIO` is a `str.format` template (`{src!r}`): the new lines contain
  no braces, so no escaping is needed; the `%r` pidfile substitution at
  `:4759` is untouched.
- `test_listen_over_http.py`: after the edits `time` must be unreferenced;
  `ruff check` (step 1) is the gate.
- Predicates passed to `eventually` must be zero-arg; `lambda: bus.published`
  returns the list itself (truthy when non-empty) — that is the intended
  contract, not a bug.

## Acceptance criteria

Each criterion names the mutation that turns it red (all from step 5).

- [ ] **Static relation replaces the tautological bounds.**
      `tests/mcp2x/test_listen_over_http.py` and
      `tests/runtime/test_subscriptions_e2e.py` each define
      `_REQUEST_TIMEOUT_S` / `_SLEEP_PAST_TIMEOUT_S` with an import-time
      `assert`, use them at the `_run_listen_app` / `booted_gateway` and
      `asyncio.sleep` sites, and contain no `assert elapsed` (step 3 greps
      print nothing). *Red by:* 5i (collection error naming the relation);
      5h (the property the constants guard).
- [ ] **The install-job tests wait for state, not time.** `grep -c
      "asyncio.sleep(" tests/test_manifest.py` counts only the sites outside
      `TestMonitorInstall`/`TestCancelJob` (i.e. zero in `:1637-1975`), and
      `test_monitor_updates_heartbeat_on_output` asserts `job.last_heartbeat >
      hb0`. *Red by:* 5a (heartbeat never refreshed), 5b (returncode still
      None), 5c (monitor never finishes; fails at the 5 s guard by message).
- [ ] **The sink tests poll the drain.** `test_subscription_contract.py` has
      exactly one fixed nonzero `asyncio.sleep` left (the `:263` negative
      soak, commented as such) and seven `eventually(` calls. *Red by:* 5d
      (no self-scheduled drain), 5e (`_draining` wedge), 5f (re-arm deleted,
      proven against the CANCELLED-drain test, where the `while` loop cannot
      run and the re-arm is the only delivery path; the suspended-publish test
      needs both mutations at once, because each path covers the other).
- [ ] **The orphan-reap script and the cancel-registration test poll their
      property.** `_SCENARIO` contains `time.monotonic()` and no
      `asyncio.sleep(0.5)`; `test_listen_registration.py` contains no
      `anyio.sleep`. *Red by:* 5g (ORPHANED after the 5 s guard), 5j
      ("never unsubscribed", by name).
- [ ] **Green under lint, coverage and starvation.** Step 1 clean; step 2
      passes; step 4 passes 3/3 pinned to one core under load; step 6 full
      suite green from a clean-volume checkout. *Red by:* leaving the
      `import time` in `test_listen_over_http.py` (F401 in step 1); 5k
      (the retry poller is live: "never acked" after 10 s).

## Execution Policy

- execute: effort=low, reason=test-only edits with every replacement spelled
  out; the two non-mechanical spots (the heartbeat strengthening and the
  cancel-ordering trap in `test_manifest.py`) are named with line numbers and
  a file-level run catches either slip immediately.

## Named follow-on sub-slices (not planned here)

**C2b — private-poller consolidation (7 files, no `src/`, effort=low).**
Every site is already a bounded deadline loop; the change is folding it into
the shared helper so the suite has one poller. Per file, read this session:

- `tests/runtime/test_emitter_harness.py` — eight `for _ in range(50): if
  <calls predicate>: break; await asyncio.sleep(0.05)` loops starting at
  `:134`, `:166`, `:192`, `:220`, `:246`, `:276`, `:301`, `:330` → `await eventually(lambda: any(c.get("method") == M for c in
  calls), timeout=2.5, message=…)`; drop the now-redundant trailing asserts
  (their `{calls}` dump is lost — decide whether to keep the assert as the
  message carrier). Red mutation: in `ClientManager._read_sse` /
  `_handle_stdout_line` stop routing notifications (the spies see nothing).
- `tests/mcp2x/test_catalog_publishers.py:52-66` — delete `POLL_ATTEMPTS`,
  `POLL_INTERVAL_S`, `_poll_until`; its four callers become
  `await eventually(<predicate>, timeout=2.0, interval=0.02, message=…)`.
  Red mutation: stub `BusCatalogEventSink._start_drain` to a no-op.
- `tests/test_client_manager_reconnect.py:46-70` (`_await_status`) — rebuild
  on `eventually` with a predicate returning the `ServerStatus` and a
  re-raise that adds `last status=…`; `:198-206` (20×0.05 leaked-task loop)
  → `eventually(lambda: not [live tasks])`. Red mutation: make
  `_connect_stdio` skip `_cleanup_client` on initialize failure.
- `tests/test_egress_panel_fixes.py:290-300` (`_wait_for(threading.Event)`)
  → `await eventually(event.is_set, timeout=timeout, message="the worker
  never parked before the claim")`. Red mutation: the handler never
  `to_thread`s (park never happens).
- `tests/test_http_dos.py:140-145` (startup poll, same as C2a's
  `_run_listen_app` recipe) and `:212-234` (the B-ack retry loop; **its
  `except (TimeoutError, AssertionError)` at `:222` must become
  `except (asyncio.TimeoutError, AssertionError)`** — on 3.10 the builtin does
  not catch `wait_for`'s timeout, so today a slow ack propagates instead of
  retrying; under `eventually` the same catch is required inside the
  predicate). Red mutation: `aclose()` → `pass`, as 5k.
- `tests/runtime/fake_remote.py:254-259` — the same startup poll (missed by
  the C1 inventory); a helper module, not a test.
- `tests/runtime/harness.py:142-154` and
  `tests/test_credential_boot.py:242-254` (`_wait_for_health`, two copies) →
  `eventually_sync(predicate, timeout=30.0, interval=0.5)` where the
  predicate returns True on `curl -sf /health` success and calls
  `pytest.fail(...)` (propagates immediately) when `proc.poll() is not None`.
  `test_credential_boot.py` is `live`-marked (`-m live` to run). Red
  mutation: point the curl at `port + 1` (the predicate never holds; fails
  at the 30 s guard by message instead of hanging).

**C2c — window lower bounds and the remaining `test_client_manager.py` sites
(3 files, no `src/`, effort=medium; rebase on PKGID/EGRESS work first).**

- `tests/test_package_identity_gate.py:950-983` (`ticks >= 10` during a
  0.5 s blocking fetch) and — missed by the C1 inventory —
  `tests/test_feedback_egress.py:616-655` (`ticks > 5` during a 0.4 s
  blocking submit): same class. Recommended shape instead of C1's "drop to
  `>= 1`": a thread↔loop handshake with no wall-clock window at all — the
  blocking stub does `loop.call_soon_threadsafe(loop_alive.set)` then
  `assert acked.wait(5)` (a `threading.Event` set by a loop-side task that
  awaits `loop_alive`). If the fetch/submit ran *on* the loop thread the ack
  can never arrive → the 5 s hang guard fails by name; the `time.sleep` and
  the ticker both disappear. Red mutation: in `handlers.py:5146` /
  the egress submitter, call the function inline instead of
  `anyio.to_thread.run_sync`. Keep the existing `fetch_threads[0] !=
  loop_thread` assertion.
- `tests/test_client_manager.py:4257-4298` (`spins >= 1` in a 0.6 s window,
  `spins <= 6`) → in `storm_send` record `loop.time()` per `tools/list`;
  `await eventually(lambda: len(stamps) >= 2, timeout=5.0)`; assert
  `stamps[1] - stamps[0] >= _RECONCILE_RERUN_DEBOUNCE_S`
  (`src/pmcp/client/manager.py:169` = 0.25, slept at `:2153`) — a lower
  bound on a real timer; drop the `sleep(0.6)` and both count bounds. Red
  mutation: set `_RECONCILE_RERUN_DEBOUNCE_S = 0` (gap ≈ 0). Same file:
  `_gone` at `:3637-3644` and `:3691-3698` → `await eventually(lambda:
  <ProcessLookupError on os.kill(pid, 0)>, timeout=3.0, message=…)`.
  **Gotcha:** `asyncio.sleep` is patched globally inside *five* tests in
  this file — `with patch("asyncio.sleep", new=AsyncMock())` at `:3115`,
  `:3133`, `:3149`, `:3183` and `new=sleep` at `:3223`
  (`test_reconnect_loop_does_not_hold_lifecycle_lock_during_backoff`). None
  of the three C2c sites are inside those `with` blocks, but re-check before
  calling `eventually` anywhere else in the file.

**Kept on purpose (do not convert in any sub-slice):**
`tests/test_server_lifecycle.py:90`, `tests/test_transport_http.py:484`
(never-returning fakes vs a hard timeout), `tests/test_scoped_advisor_audit.py:461`
(negative soak), `tests/test_refresher.py:807`,
`tests/test_provision_validation.py:272` (fakes held open for overlap),
`tests/test_manifest_provision.py:295` (`live`), the in-script sleeps in
`tests/runtime/test_hang_diagnostics.py:198/:210` and
`tests/test_client_manager.py:3616/:3666/:3672`, and
`tests/runtime/_hang_watchdog.py:194` (implementation).
