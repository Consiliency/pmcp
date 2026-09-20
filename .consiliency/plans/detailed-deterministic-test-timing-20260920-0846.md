# Detailed plan: deterministic test timing — slice C1 of #235 (helper + #226 + the wall-clock upper bounds)

> **Bounded-plan verdict: slice C EXCEEDS the threshold and is split.** The
> full slice touches 29 test files (98 `sleep(` lines, ~90 real call sites).
> This document plans **C1** — the shared helper, the #226 fix and its sibling,
> and every *upper-bound* wall-clock assertion in the suite — across **7 test
> files + 1 new module + 2 docs**. **C2** (bulk poll-until conversion, ~12
> files) and **C3** (production-clock injection, ~3 files, touches `src/`) are
> named at the end with their file lists so they can be planned as their own
> bounded plans against the helper this slice lands.

## Task

Slice C of Consiliency/pmcp#235 (finding T-06/T-07 of the 2026-09-01 review):
make the suite's timing deterministic — replace wall-clock dependence with
property assertions — and introduce the shared deterministic-time helper the
suite lacks. Also resolves Consiliency/pmcp#226
(`test_connect_all_parallel_execution` failing CI on a 2 ms margin). See #235,
see #226.

## Research summary

Verified against `main` at `4461e2f` (every line number below was read this
session; the survey the lead supplied was checked site by site — see
"Corrections to the survey").

**The runtime is pytest-asyncio, not anyio.** `pyproject.toml:147` sets
`asyncio_mode = "auto"`; `uv.lock` pins `pytest-asyncio 1.3.0`. Only three
test files import anyio (`tests/test_exception_group_diagnostics.py`,
`tests/mcp2x/test_listen_registration.py`, `tests/test_egress_panel_fixes.py`)
and none use `pytest.mark.anyio` or an `anyio_backend` fixture.
`requires-python = ">=3.10"` (`pyproject.toml:11`), so `asyncio.Barrier` and
`asyncio.timeout()` (3.11+) are unavailable — `asyncio.Event` + `wait_for` it is.

**No shared helper exists, but the suite has already grown five private ones,
all the same shape.** A deadline poller appears as: `_drain` (three copies:
`tests/test_client_manager.py:3923`, `:4655`, `:5000`) and two inline `_wait`
closures (`:6138`, `:6186`), `_wait_for` at
`tests/test_egress_panel_fixes.py:290`, `_poll_until` at
`tests/mcp2x/test_catalog_publishers.py:55`, `_wait_for_status` at
`tests/test_client_manager_reconnect.py:55`, `_wait_for_health` (two copies:
`tests/runtime/harness.py:143`, `tests/test_credential_boot.py:241`),
`_read_until` at `tests/runtime/test_subscriptions_e2e.py:92`, and eight
`for _ in range(50): … await asyncio.sleep(0.05)` loops in
`tests/runtime/test_emitter_harness.py`. The helper this slice adds is the
consolidation of those; the sites themselves are C2's work except the five in
`test_client_manager.py`, which C1 converts because it is already editing that
file and they are the helper's zero-risk proof of fit. One *fake clock* also
already exists: `_Clock` at `tests/test_feedback_egress_gate.py:195`,
monkeypatched over `feedback_egress.time` at `:696` so a phase can consume
budget deterministically. It is the local precedent for C3's clock seam, and
the reason C3 is feasible without a new dependency.

**What the flaky class actually is.** Every CI-observed failure in this class
is an *upper bound on elapsed real time* (`elapsed < X`). Lower bounds on a
real sleep or timer (`elapsed > 8` after `await asyncio.sleep(8.2)`) cannot
fail — a sleep never returns early — so they are documentation, not checks, and
not flaky. Upper bounds are the whole target. The suite has **ten**:
`tests/test_client_manager.py:2176` (`< 0.2`, #226) and `:2235` (`< 0.09`);
`tests/test_npm_resolver.py:811` (`< 5.0` half) and `:819` (`< 0.5`);
`tests/test_feedback_egress.py:682` (`< 5.0`);
`tests/test_feedback_egress_gate.py:988` (`< 5.0`) and `:1002` (`< 1.0`);
`tests/test_package_identity_gate.py:1009` (`< 5`);
`tests/runtime/test_hang_diagnostics.py:150`, `:179` (`< _MUTANT_BOUND`) and
`:265` (`< 15`). In every case the file already contains — or the production
code already exposes — a deterministic property that is what the elapsed bound
was standing in for.

**#226 specifically.** `ClientManager._connect_all_unlocked`
(`src/pmcp/client/manager.py:1078-1098`) creates one `asyncio.Task` per
distinct name via `create_task` *before* `gather`, so all three
`mock_connect` bodies are scheduled before any is awaited. The property
"parallel, not serial" is therefore exactly: **all three bodies are in flight
at once**. A rendezvous that each body must pass — released only when the
third arrives, bounded so a serial implementation fails with a named
assertion rather than hanging — proves it with no clock. The issue's own
suggestion (`max(call_times) - min(call_times) < 0.05`) is still a wall-clock
upper bound and is not adopted.

**Kind-4 sleeps are correct and stay.** `tests/test_server_lifecycle.py:90`
(`sleep(20)` against `server.py:965`'s hard-coded `timeout=10.0`),
`tests/mcp2x/test_listen_over_http.py:234` (`8.2` vs `request_timeout=3`),
`tests/runtime/test_subscriptions_e2e.py:212` (`13` vs `request_timeout=5`).
Their *assertions* are lower bounds and safe. C1 does not touch them.

### Corrections to the survey (disagreements, with evidence)

1. **The five "risky tiny sleeps" in `test_client_manager.py`
   (`:3928/:4660/:5004/:6140/:6188`, `sleep(0.005)`) are not event-loop
   yields.** Each is the interval of a `while manager._reconcile_tasks:` poll
   already wrapped in `asyncio.wait_for(_wait(), 5.0)`. They are Kind 2
   *already fixed* — five copies of the helper this slice introduces. Zero
   flake risk today; they are consolidated, not "fixed".
2. **`tests/test_refresher.py:807` and `tests/test_provision_validation.py:272`
   (`sleep(0.01)`) are Kind 1, not Kind 3** — both sit inside a *fake* and
   exist to hold the fake open so a second caller overlaps it (`peak_active`
   at `:803`; "Yield so the racing poll reaches the lock while we hold it" at
   `:271`). Keep. `tests/test_egress_panel_fixes.py:300` is the interval of a
   deadline poller (`_wait_for`, `:290`) — Kind 2 already fixed.
3. **`tests/test_manifest_provision.py:295` (`sleep(2)`) is not Kind 4.** It is
   the poll interval of a 120 s deadline loop in a `live`-marked test that
   `addopts = "-m 'not live'"` deselects by default. Nothing to do.
4. **`tests/test_scoped_advisor_audit.py:457` (`time.sleep(1.5)`) is not Kind
   4 either** — it is a *negative soak*: sleep, then assert four subprocesses
   are **still alive** (`poll() is None`). A negative cannot be polled for;
   there is no deadline-poller form. Keep, comment it as a soak. (C2 may
   shorten it; it is not flaky in the upper-bound sense.)
5. **Three upper bounds were missing from the survey:**
   `tests/test_feedback_egress_gate.py:988` (`elapsed < 5.0`) and `:1002`
   (`time.monotonic() - read_start < 1.0`), and the half of
   `tests/test_npm_resolver.py:811` that is an upper bound. All three are in
   C1.
6. **`tests/test_tools.py:4772` (`sleep(0.5)`) is inside the subprocess
   source string** that starts at `:4740`, like the `:4757/:4759` false
   targets the survey correctly excluded. It *is* a real Kind-2 sleep — but
   one that runs in the child, so the conversion is a poll loop inside the
   script text. C2.
7. **Two timing-sensitive sites the survey did not list are the hardest ones
   in the slice:** `tests/test_client_manager.py:3298` (heartbeat every 0.1 s
   against a 0.3 s idle window) and `:3335` (every 0.05 s against a 200 ms
   ceiling). They test `_await_with_idle_timeout`
   (`src/pmcp/client/manager.py:3133`), which reads `time.time()` — so
   neither a deadline poller nor an event-loop clock can make them
   deterministic; only a clock injected into production code can. **C3**, and
   it touches `src/`.
8. Scale: 98 grep lines / 29 files, not 90 / 28 — the difference is docstring
   mentions, `sleep(0, result=…)` fakes and the `patch("asyncio.sleep")` at
   `:3211`. Not material.

### Helper choice: deadline poller, not `autojump_clock`

**anyio's `autojump_clock` is not viable here, for three independent reasons:**

- It does not exist in anyio. `autojump_clock` is a **pytest-trio** fixture
  backed by `trio.testing.MockClock`; anyio 4.x ships no clock virtualisation
  for its asyncio backend (`anyio.sleep` on asyncio is `loop.call_later`). The
  suite runs pytest-asyncio 1.3.0 in auto mode, and the three anyio-importing
  files use it as a library, not as the test runner.
- **Most Kind-2 waits are on things a virtual clock cannot advance**: child
  processes (`echo`, `sleep 30`, `pmcp` itself, `curl` health probes in
  `harness.py`), uvicorn servers, and worker threads (`to_thread`,
  `threading.Event` in the egress tests). Under a mocked clock those tests
  would deadlock — the exact tests that flake today.
- The two idle-timeout tests that *would* benefit from a virtual clock read
  `time.time()` in production code (`manager.py:3133`), so a loop-clock
  override would not reach them anyway; they need injection (C3).

A **deadline poller** (`eventually`) is monotone in real progress: it passes
as soon as the property holds, and its only timing parameter is a generous
*hang guard* (default 5 s) that never tightens as the machine slows down. It
replaces "sleep N then assert" with "assert as soon as true, fail loudly if
never". For the concurrency property in #226, the poller is not the right
shape either — that needs a **rendezvous**, which the same module provides.

## Changes

### `tests/_timing.py` (create)

One module, three exports, no pytest fixtures (importable from helpers like
`tests/runtime/harness.py`, and from subprocess-run modules). Name follows
`tests/runtime/_hang_watchdog.py` (leading underscore = helper, not a test).
`tests/__init__.py` already exists, so `from tests._timing import …` works.

- `async def eventually(predicate, *, timeout=5.0, interval=0.01, message=None)`
  — add — deadline poller. `predicate` is a sync or async zero-arg callable;
  returns its first truthy value; on expiry raises `AssertionError(message or
  "condition not met within {timeout}s")`. Deadline via
  `asyncio.get_running_loop().time()` (the loop clock, matching
  `test_egress_panel_fixes.py:296`). Interval sleeps use `asyncio.sleep`, so a
  test that patches `asyncio.sleep` (`test_client_manager.py:3211`) must not
  call it — document that in the docstring.
- `def eventually_sync(predicate, *, timeout=30.0, interval=0.5, message=None)`
  — add — the blocking twin for subprocess health polling; `time.monotonic()`
  deadline, `time.sleep` interval. Consumed by C2 (`harness.py:143`,
  `test_credential_boot.py:241`); added now so the API is fixed in one review.
- `class Rendezvous` — add — the concurrency proof for #226.
  `Rendezvous(parties: int, *, timeout: float = 2.0)`; `async def arrive()`
  increments an arrival count, sets an `asyncio.Event` when the count reaches
  `parties`, then `await asyncio.wait_for(event.wait(), timeout)`; on
  `TimeoutError` raises `AssertionError(f"only {arrived} of {parties} parties
  were in flight together")`. Property `arrived` (int) for post-hoc asserts.
  The `Event` is created lazily on first `arrive()` so the object can be
  built outside a running loop (Python 3.10 binds `Event` to a loop at
  construction).
- Module docstring — add — states the rule this slice enforces: *lower bounds
  on real timers are safe; upper bounds on elapsed time are the flaky class
  and are replaced by the property they stood in for; `timeout=` on these
  helpers is a hang guard, never an assertion.*

### `tests/test_client_manager.py` (modify)

- `TestParallelConnections.test_connect_all_parallel_execution` (`:2156-2177`)
  — modify — closes #226. Replace `call_times`/`start`/`elapsed` with a
  `Rendezvous(3)`; `mock_connect` becomes `await gate.arrive()`. Wrap the
  `connect_all` call in `asyncio.wait_for(…, 5.0)` as a hang guard. Assert
  `errors == []` (a serial implementation surfaces the rendezvous
  `AssertionError` as a `Failed to connect to …: only 1 of 3 parties …` entry
  — the failure names itself) and `gate.arrived == 3`. Delete the
  `time`-based assertion at `:2176`. Keep the docstring; update the comment.
- `TestParallelConnections.test_connect_all_deduplicates_same_name_configs`
  (`:2200-2235`) — modify — the property is dedup, already proven by
  `sorted(calls) == ["other", "same"]` at `:2234`. Delete the
  `time.time() - start < 0.09` assertion at `:2235` and the `start = …` at
  `:2231`; change the fake's `sleep(0.05)` at `:2211` to `sleep(0)` (a yield
  is all the dedup path needs — the concurrency claim is now
  `test_connect_all_parallel_execution`'s alone).
- `TestDownstreamReconcileScheduler._drain` (`:3923`), the `_drain` at
  `:4655`, the `_drain` at `:5000`, and the inline `_wait` closures at
  `:6138-6142` and `:6186-6190` — modify — replace the body with
  `await eventually(lambda: not manager._reconcile_tasks, timeout=timeout,
  interval=0.005, message="a reconcile is still in flight")`. Same semantics
  (poll every 5 ms, 5 s guard); the three staticmethods stay as one-line
  wrappers so their ~40 call sites do not change. Net: −20 lines.
- `import time` — keep — still used at `:3290-3336` and elsewhere.

### `tests/test_feedback_egress.py` (modify)

- `test_a_slow_submission_is_bounded_by_the_handler_timeout` (`:660-686`) —
  modify — the property "the handler returned because its 0.25 s timeout
  fired, not because the stall released" is observable without a clock: the
  transport is still parked on `release` when `_submit` returns. Capture
  `stalled_at_return = not release.is_set()` on the line after the `await`
  (inside the `try`, before the `finally` sets it), delete `started`/`elapsed`
  and the `elapsed < 5.0` assertion at `:682`, and assert
  `stalled_at_return is True` alongside the existing `result.submitted is
  False`. The handler timeout is a real timer, so the test still takes
  ~0.25 s; that is a lower bound and fine.

### `tests/test_feedback_egress_gate.py` (modify)

- `test_the_body_read_is_bounded_by_its_own_budget` (`:970-1002`) — modify —
  `_TricklingResponse.read` (`:190`) does not sleep, so `elapsed < 5.0` at
  `:988` never measured anything; the bound is the byte-cap assertion on
  `:989` (`1 < response.reads <= _MAX_RESPONSE_BYTES + 1`), which stays.
  Delete `started`/`elapsed` and `:988`. For the second half, `slow.reads ==
  0` at `:1001` already proves `_read_bounded_body` returned without touching
  the peer once the deadline had passed; delete `read_start`-based `:1002`
  (keep `read_start` only as the deadline argument — rename to `past_deadline
  = time.monotonic() - 1.0` for honesty).

### `tests/test_package_identity_gate.py` (modify)

- `test_a_hung_registry_lookup_is_bounded_by_the_handler` (`:985-1012`) —
  modify — same shape as the egress test: capture `not release.is_set()`
  immediately after `await gateway.register_discovered_server(...)` inside the
  `try`; delete `started`/`elapsed` and `assert elapsed < 5` at `:1009`;
  assert the captured flag. `out.registered is False` and the
  `_discovered_server_configs` negative at `:1010-1012` stay.
- `test_registration_resolves_off_the_event_loop` (`:950-982`) — no change —
  `ticks >= 10` during a 0.5 s blocking fetch is a *lower* bound on loop
  liveness (≥10 of ~25 expected ticks); the fetch-thread identity check at
  `:980` is the deterministic property and already present. Note only.

### `tests/test_npm_resolver.py` (modify)

- `TestHungChild.test_the_first_caller_stalls_and_no_later_one_does`
  (`:802-822`) — modify — keep the **lower** bound `0.5 < first_elapsed` (the
  1.0 s `_QUERY_TIMEOUT` at `npm_resolver.py:74` is a real timer; a lower
  bound on it cannot flake) and drop the `< 5.0` upper half. Replace
  `rest_elapsed < 0.5` at `:819` with the property the resolver exposes: on a
  query timeout `NpmResolver` terminates the child
  (`npm_resolver.py:508-509`, `_refused("npm resolver child timed out or
  died")`), so every later caller inside `_RESPAWN_COOLDOWN` (`:85`, 60 s)
  takes the cheap path at `:405-410` and is refused with reason
  `"npm resolver child is cooling down after a failure"`. Assert, for each of
  the 19 later resolutions, `"cooling down" in result.reason`; keep
  `instance.spawn_attempts == 1`. That is stronger than the elapsed bound — it
  proves *which* branch answered, not merely that it was quick.

### `tests/runtime/test_hang_diagnostics.py` (modify)

- `test_a_server_task_that_never_finishes_raises_instead_of_hanging`
  (`:126-153`) and `test_a_serve_task_that_swallows_cancellation_still_raises`
  (`:156-181`) — modify — `_bounded(coro, limit)` (`:65-77`) already raises
  `AssertionError("the test body did not finish within {limit}s")` when the
  body exceeds `_MUTANT_BOUND`, so `elapsed < _MUTANT_BOUND` at `:150`/`:179`
  can never be the first thing to fail; and it is an upper bound on a body
  that legitimately runs for `SERVE_STOP_TIMEOUT + CANCEL_GRACE` — half the
  bound. Delete `started`/`elapsed` and both assertions. Everything else in
  those tests stays.
- `test_the_timeout_plugin_is_active_and_covers_teardown` (`:224-267`) —
  modify — if pytest-timeout failed to kill the 30 s teardown, the child
  session would *pass* (`returncode == 0`) and print no timeout text; both
  are already asserted at `:264` and `:266`. `elapsed < 15` at `:265` adds
  only a way to fail on a slow runner. Delete `started`/`elapsed`/`:265`.
  `subprocess.run(timeout=60)` remains the hang guard.

## Documentation impact

- `CONTRIBUTING.md` — modify — under "Running Tests" (`:19`), add a short
  "Timing in tests" paragraph: never assert an upper bound on elapsed wall
  time; wait with `tests._timing.eventually`, prove concurrency with
  `Rendezvous`, and treat `timeout=` as a hang guard. Three sentences and a
  pointer to `tests/_timing.py`'s docstring — this is the rule reviewers will
  hold C2 to.
- `CHANGELOG.md` — modify — under `## [Unreleased]`, a `### Fixed` entry (the
  file uses only Added / Fixed / Security headings):
  "Tests no longer assert upper bounds on wall-clock time; #226's parallel
  connection test proves concurrency with a rendezvous. See #235, see #226."
- `plans/manifest.json` — modify — `type=detailed` entry for this plan
  (written by the skill's close-out; recorded here because the file is
  tracked).

## Dependencies & order

1. `tests/_timing.py` first — every other change imports it.
2. `tests/test_client_manager.py` second: it is both the #226 fix and the
   helper's five-site consolidation, so if the helper's API is wrong it
   shows here before the other files are touched.
3. The remaining five test files are independent of each other and of (2);
   any order.
4. Docs last, after the helper's names are final.

No `src/` change. No new dependency (`pytest-repeat` is **not** installed; the
verification loop below uses the shell).

**Merge-order note:** slice B (`plan/235-slice-b-module-state`) also edits
`tests/conftest.py`; this slice deliberately does not touch `conftest.py`
(the helper is a plain module, not a fixture) so the two slices do not
conflict.

## Verification

Run from a checkout whose parent directories contain no `node_modules` —
`/mnt/HC_Volume_105438154/node_modules` exists, so a worktree under that
volume will show ~107 spurious npm-identity failures unrelated to this slice
(known; see `tests/conftest.py`). Use the primary checkout or set the
worktree's parent to a clean directory. After `uv sync -p 3.10`:

```bash
# 1. The new module imports and its guards fail loudly.
uv run python -c "from tests._timing import eventually, eventually_sync, Rendezvous; print('ok')"

# 2. #226: the rewritten test, 30 consecutive runs under CI's coverage
#    instrumentation, which is what widened the old margin past 0.2 s.
for i in $(seq 30); do
  uv run pytest tests/test_client_manager.py -k TestParallelConnections \
    --cov=pmcp --cov-report= -q -p no:cacheprovider || { echo "FAIL on run $i"; break; }
done

# 3. #226 mutant: a SERIAL connect_all must FAIL the test, by name, not hang.
#    Temporarily replace the loop body in _connect_all_unlocked
#    (src/pmcp/client/manager.py:1086-1098) so each task is created AND
#    awaited before the next is created:
#        for config in configs:
#            task = asyncio.create_task(self._connect_singleflight(config, retry))
#            await asyncio.gather(task, return_exceptions=True)
#            tasks.append(task)
#    Expected: the test fails within ~2 s with
#    "Failed to connect to …: only 1 of 3 parties were in flight together".
uv run pytest tests/test_client_manager.py -k test_connect_all_parallel_execution -q
git checkout src/pmcp/client/manager.py

# 4. The other five files, each under coverage.
uv run pytest tests/test_feedback_egress.py tests/test_feedback_egress_gate.py \
  tests/test_package_identity_gate.py tests/test_npm_resolver.py \
  tests/runtime/test_hang_diagnostics.py --cov=pmcp --cov-report= -q

# 5. Starved-CPU rehearsal for the whole edited set: pin to one core with
#    two busy loops competing, three passes. This is the load profile that
#    produced #226's 0.2019 s.
( yes >/dev/null & yes >/dev/null & sleep 1; \
  taskset -c 0 uv run pytest tests/test_client_manager.py tests/test_feedback_egress.py \
    tests/test_feedback_egress_gate.py tests/test_package_identity_gate.py \
    tests/test_npm_resolver.py tests/runtime/test_hang_diagnostics.py -q -x; \
  kill %1 %2 )

# 6. No upper-bound wall-clock assertion remains outside the Kind-4 lower
#    bounds. Expected hits: ONLY the `> 8` / `> 12` lower bounds and the
#    `0.5 < first_elapsed` lower bound.
grep -rnE "assert .*(elapsed|time\.(time|monotonic)\(\) *- *[a-z_]+) *<" tests

# 7. Full suite and the lint CI actually runs (test.yml:238/241 — ruff on
#    src/ and tests/; mypy covers src/pmcp only, so it is not a gate here).
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```

Edge cases to check by hand while implementing:

- `Rendezvous.arrive()` called from a coroutine that is later cancelled by
  `connect_all`'s error path must not leak a pending `Event.wait()` —
  `wait_for` cancels it; confirm no "Task was destroyed but it is pending"
  warning in the run-2 output.
- `eventually` with an `async` predicate that itself raises: the exception
  must propagate immediately (not be swallowed until the deadline).
- `eventually(..., timeout=0)` evaluates the predicate exactly once, then
  raises — useful for the C2 conversions that want "already true".

## Acceptance criteria

- [ ] `tests/_timing.py` exists exporting `eventually`, `eventually_sync`,
      `Rendezvous`; `uv run ruff check src/ tests/` and
      `uv run ruff format --check src/ tests/` are clean.
- [ ] `test_connect_all_parallel_execution` contains no `time.` call and
      passes 30/30 under `--cov=pmcp`; with the serial mutant from step 3 it
      fails within 5 s with a message naming "of 3 parties".
- [ ] Step 6's grep returns only the three lower-bound sites
      (`test_listen_over_http.py`, `test_subscriptions_e2e.py`,
      `test_npm_resolver.py`'s `0.5 <`); every `elapsed < …` at
      `test_client_manager.py:2176/:2235`, `test_npm_resolver.py:811/:819`,
      `test_feedback_egress.py:682`, `test_feedback_egress_gate.py:988/:1002`,
      `test_package_identity_gate.py:1009`,
      `test_hang_diagnostics.py:150/:179/:265` is gone.
- [ ] The five `_drain`/`_wait` pollers in `test_client_manager.py` delegate
      to `eventually`; `grep -c "while manager._reconcile_tasks" tests/test_client_manager.py` returns 0.
- [ ] `uv run pytest -q` passes; step 5's starved run passes 3/3.

## Execution Policy

- execute: effort=medium, reason=test-only edits, but the #226 rendezvous and
  the "capture before finally" pattern are concurrency-shaped and easy to get
  subtly wrong; medium, not low.

## Named follow-on slices (not planned here)

**C2 — bulk poll-until conversion (~12 files, no `src/`).** Convert every
"sleep N then assert progress" to `eventually`, and fold the private pollers
into it:
`tests/test_manifest.py` (`:1650`, `:1805`, `:1905`, `:1915`, `:1934`,
`:1943`, `:1964` — poll for `job.status` in a terminal set, `process.returncode
is not None`, `monitor_task.done()`; `:1655`'s heartbeat freshness stays);
`tests/test_tools.py:4772` (inside the subprocess script — poll `/proc/<pid>`
in the script text); `tests/test_client_manager.py:4290` (debounce — the
property is "consecutive `tools/list` calls are ≥ `_RECONCILE_RERUN_DEBOUNCE_S`
apart", a lower bound on a real timer, recorded via `loop.time()` in
`storm_send`) and `:3640/:3694` (`_gone` → `eventually`);
`tests/runtime/harness.py:143` + `tests/test_credential_boot.py:241`
(→ `eventually_sync`); `tests/runtime/test_emitter_harness.py` (×8);
`tests/mcp2x/test_subscription_contract.py` (`:180`, `:215`, `:263`, `:279`,
`:286`, `:420`, `:452`, `:488`); `tests/test_http_dos.py` (`:143`, `:230`);
`tests/mcp2x/test_listen_over_http.py` (`:120`, `:292`);
`tests/mcp2x/test_catalog_publishers.py:55`;
`tests/test_client_manager_reconnect.py` (`:55`, `:206`);
`tests/test_egress_panel_fixes.py:290`;
`tests/mcp2x/test_listen_registration.py:286` (`anyio.sleep(0.2)` "let the
cancellation land" — poll the bus's subscription table instead).
Also in C2: replace the tautological Kind-4 lower bounds (`elapsed > 8`,
`elapsed > 12`) with a static relation between the sleep constant and the
configured `request_timeout` — the property is "the sleep exceeds the budget",
which is checkable at import time.

**C3 — production-clock injection (~3 files, touches `src/`).**
`tests/test_client_manager.py:3298` and `:3335` (heartbeat-vs-idle-window;
`_await_with_idle_timeout` reads `time.time()` at `manager.py:3133+`) need a
clock seam in `ClientManager` — the `_Clock`-over-module-`time` pattern from
`tests/test_feedback_egress_gate.py:195/:696` is the precedent; `tests/test_server_lifecycle.py:90` costs 10
real seconds because `server.py:965` hard-codes `timeout=10.0` — extract a
module constant so the test can patch it to 0.05 s and assert the
"Shutdown timed out" log line instead of merely "did not raise".
`tests/test_scoped_advisor_audit.py:457`'s 1.5 s soak can be shortened once
the property ("no seat exits on a lock/audit collision") has a positive
signal to poll for.
