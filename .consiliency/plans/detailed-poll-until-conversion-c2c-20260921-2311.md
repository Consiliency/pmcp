# Detailed plan: convert the wall-clock-window tests (slice C2c of #235)

## Task

Slice C2c of Consiliency/pmcp#235 — the last slice. Remove the residual timing
flake class that `eventually` alone cannot fix: **counting work done inside a
real wall-clock window.** Three test files, no `src/` change:

- `tests/test_package_identity_gate.py` — `test_registration_resolves_off_the_event_loop`
  asserts `ticks >= 10` during a 0.5 s blocking fetch.
- `tests/test_feedback_egress.py` — `test_no_blocking_http_call_runs_on_the_event_loop`
  asserts `ticks > 5` during a 0.4 s blocking submit (same shape; missed by the C1 inventory).
- `tests/test_client_manager.py` — the reconcile-debounce test asserts `spins <= 6` /
  `spins >= 1` in a 0.6 s window, plus two `_gone` process-reaping pollers.

The rule being enforced (`CONTRIBUTING.md` → "Timing in tests", `tests/_timing.py`
docstring): **upper bounds on elapsed wall time (and counts of work inside a
fixed window) are the flake class; lower bounds on a real timer are safe.** A
starved runner fails a "count ≥ N in a window" assertion even when the code is
correct.

This plan was built by prototyping every replacement in the worktree, running it
green against intact source, running each named mutation to red, then reverting
to a clean tree. All results below are **measured**, not reasoned.

## Research summary

- `tests/_timing.py` provides `eventually(predicate, *, timeout, interval, message)`
  (async poll; re-raises a predicate exception immediately; `timeout=` is a hang
  guard, never an assertion), `eventually_sync`, and `Rendezvous`. `eventually`
  sleeps via `asyncio.sleep`, so it must not run inside a test that patches
  `asyncio.sleep` globally. `test_client_manager.py` already imports `eventually`
  and `Rendezvous` (`tests/test_client_manager.py:53`); the pkgid and egress files
  do **not** import `_timing` and, under this plan, still won't — the handshake
  uses only stdlib `asyncio` + `threading`.
- The two ticker sites are the **same shape**, verified: a blocking stub records
  the thread it ran on and sleeps for real; a ticker/heartbeat coroutine counts
  loop iterations; the test asserts (a) `thread[0] != loop_thread` — kept — and
  (b) a count lower bound inside the window — the flake. Only cosmetic differences
  (`fetch_threads`/`ticker`/`create_task` vs `recorder.thread_ids`/`_heartbeat`/`ensure_future`).
- Offload seams in `src/pmcp/tools/handlers.py`:
  - registration → `resolved = await anyio.to_thread.run_sync(resolve_package_identity, package, abandon_on_cancel=True)` (`handlers.py:5870`), inside `with anyio.fail_after(_REGISTRATION_RESOLVE_TIMEOUT_SECONDS)` (=20.0, `handlers.py:210`). `resolve_package_identity` calls the monkeypatched `package_identity._fetch_packument`. The surrounding handler **fails closed** — `handlers.py:5873` is `except TimeoutError:`; the arm that swallows the handshake's `AssertionError` is `except Exception` at **`handlers.py:5875`**.
  - feedback submit → `result = await anyio.to_thread.run_sync(functools.partial(submit_feedback_issue, …), abandon_on_cancel=True)` (`handlers.py:5154`), inside `with anyio.fail_after(_FEEDBACK_SUBMIT_TIMEOUT_SECONDS)` (=20.0, `handlers.py:220`). Also fails closed — `except Exception` is `handlers.py:5202` and its warning "Feedback submission raised: …" is logged at **`handlers.py:5206`**.
- Debounce: `src/pmcp/client/manager.py:169` `_RECONCILE_RERUN_DEBOUNCE_S = 0.25`,
  slept at `manager.py:2153` (`await asyncio.sleep(_RECONCILE_RERUN_DEBOUNCE_S)`).
- Reaping: `_terminate_process_tree` (`manager.py:243`) SIGTERMs, waits, escalates to a
  group SIGKILL. The two `_gone` pollers assert the tree is reaped within a deadline.

## Corrections to the C2c inventory (verified against the tree this run)

1. **The recommended debounce assertion as written is a tautology and cannot
   fail under its own named mutation.** The inventory says: `assert stamps[1] -
   stamps[0] >= _RECONCILE_RERUN_DEBOUNCE_S`, red mutation "set
   `_RECONCILE_RERUN_DEBOUNCE_S = 0`". If the test reads the same constant for its
   threshold, the mutation that zeroes the debounce **also zeroes the threshold**,
   so `gap >= 0` is trivially true and the test stays GREEN. Measured: with a
   literal floor `assert gap >= 0.2` the mutation goes red (`0.000s gap`); with the
   imported constant it would not. **Fix: assert against a literal `0.2`** (below
   the real 0.25, still a lower bound on a real `asyncio.sleep`), naming the
   constant only in the message. This is the 12th instance of the repo's
   can't-fail-check defect class; it is caught here, not shipped.

2. **The pkgid offload line is `handlers.py:5870`, not `handlers.py:5146`.** The
   inventory's "handlers.py:5146 / the egress submitter" conflates two seams:
   registration resolves at `:5870` (`resolve_package_identity`); the feedback
   submit offloads at `:5154` (`submit_feedback_issue`). Neither is 5146. Both
   mutations are given precisely below.

3. **Both ticker mutations surface through the handler's fail-closed path, not
   the handshake message.** Because `handlers.py:5875`/`:5202` swallow the
   AssertionError the stalled handshake raises, the mutated pkgid test fails at
   `assert out.registered is True` and the mutated egress test at `assert
   result.submitted is True` — each after ~5 s (the handshake's own hang guard).
   The detection is real and only-under-mutation; the diagnostic is one hop
   removed. The egress handler additionally logs the handshake message verbatim
   ("the loop never acked while the submit blocked"), confirming root cause. This
   is documented so the executor is not surprised that the red assertion is not
   the `acked.wait` line.

4. **The two `_gone` sites are structurally C2b-class (bounded-deadline pollers),
   not the window-count class.** They already `return True` on the property
   (`ProcessLookupError`) and `False` after `range(30)`; converting them to
   `eventually` is the same mechanic as C2b, and their red mutation is a reaping
   regression, not a debounce/offload change. They are in C2c only by file
   association. Line numbers confirmed: `test_client_manager.py:3637` (in
   `test_real_process_tree_is_reaped`, def at `:3603`) and `:3691` (in
   `test_sigterm_ignoring_grandchild_is_group_sigkilled`, def at `:3653`) — the
   inventory's `:3637-3644` / `:3691-3698` ranges are right.

5. **"Kept on purpose" list is accurate and complete for these three files.**
   Verified: `test_client_manager.py:3616/:3666/:3672` are `time.sleep(...)` inside
   **subprocess script strings** (Python source sent to child processes), not
   test-body sleeps — correctly kept. No other window-count assertion exists in the
   three files: the only `ticks`/`spins` sites are the three named
   (`test_package_identity_gate.py:983`, `test_feedback_egress.py:655`,
   `test_client_manager.py:4300-4301`). The `assert calls.count("tools/list") == N`
   at `:4043/:4049/:4067` are exact deterministic counts after an `await
   asyncio.sleep(0)` yield (no wall-clock window) — **not** in scope, must not be
   converted.

6. **No C2c site sits under one of the five global `asyncio.sleep` patches.**
   Confirmed the patches are at `:3115`, `:3133`, `:3149`, `:3183`, `:3223`, all
   inside earlier test functions (before `class TestStdioReadLimit` at `:3457` and
   `class TestTerminateProcessTree` at `:3534`). Every C2c site is at `:3637`+.
   `eventually` (which sleeps via `asyncio.sleep`) is therefore safe at all three.

## Prototype result: the handshake redesign works

Prototyped standalone and in-tree. The redesign replaces "count ticks in a
window" with a **thread↔loop handshake that has no window at all**:

- `loop_alive = asyncio.Event()`; `acked = threading.Event()`.
- A loop-side task `acker`/`_acker` does `await loop_alive.wait(); acked.set()`.
- The blocking stub (run off-loop via `anyio.to_thread.run_sync`) does:
  `loop.call_soon_threadsafe(loop_alive.set)`, then `assert acked.wait(5), "<msg>"`.

Property proven: **the loop makes progress while the blocking call is in flight.**
If the call runs *on* the loop thread (the mutation), the loop is blocked inside
`acked.wait`, so it can neither run the scheduled `loop_alive.set` callback nor the
`acker` task → `acked` is never set → `acked.wait(5)` returns False → the assertion
fails at the 5 s hang guard. If it runs *off* the loop (correct), the loop is free,
the ack round-trips in ~ms, and the stub returns. It **distinguishes on-loop from
off-loop execution** — it is not a check that always passes.

Standalone prototype output:
```
off_loop  -> PASS (ok) in 0.002s
on_loop   -> FAIL as designed (AssertionError('the loop never acked; the blocking call ran on the loop thread')) in 1.000s
```
In-tree, both redesigned ticker tests pass against intact source (measured green,
below); both fail under the inline-call mutation (measured red, below). The
existing `thread[0] != loop_thread` assertion is kept in each as a direct belt.

## Changes

### `tests/test_package_identity_gate.py` (modify)

- Import `time` (`:34`) — **delete** — its only use was `time.sleep(0.5)`, removed by
  this change; leaving it is an F401 that fails `ruff check` (measured).
- `test_registration_resolves_off_the_event_loop` (def `:950`) — **modify** — replace
  the `slow_fetch`/`ticker`/`ticks >= 10` body (`:953`–`:983`) with the handshake.
  Keep `assert out.registered is True` and `assert fetch_threads and fetch_threads[0]
  != loop_thread`. Verified replacement body:

  ```python
      loop = asyncio.get_running_loop()
      loop_thread = threading.get_ident()
      fetch_threads: list[int] = []

      # A thread<->loop handshake proves the fetch runs off the loop without
      # counting work inside a wall-clock window (the Consiliency/pmcp#226 flake
      # class). The loop-side `acker` can only set `acked` if the loop keeps
      # running *while* the fetch blocks -- impossible if the fetch ran on the
      # loop thread, in which case `acked.wait(5)` times out and fails by name.
      loop_alive = asyncio.Event()
      acked = threading.Event()

      async def acker() -> None:
          await loop_alive.wait()
          acked.set()

      def handshake_fetch(name: str) -> dict[str, Any]:
          fetch_threads.append(threading.get_ident())
          loop.call_soon_threadsafe(loop_alive.set)
          assert acked.wait(5), (
              "the loop never acked while the fetch blocked; "
              "the registry lookup ran on the event loop thread"
          )
          return _packument(name, "1.0.0")

      monkeypatch.setattr(package_identity, "_fetch_packument", handshake_fetch)
      gateway, _ = _gateway(monkeypatch, _empty_policy(tmp_path))

      ack_task = asyncio.create_task(acker())
      try:
          out = await gateway.register_discovered_server(
              {"server_name": "slow", "package": "slow-mcp"}
          )
      finally:
          ack_task.cancel()

      assert out.registered is True
      assert fetch_threads and fetch_threads[0] != loop_thread
  ```

### `tests/test_feedback_egress.py` (modify)

- Import `time` (`:33`) — **delete** — same reason (only use was `time.sleep(0.4)`);
  F401 otherwise (measured).
- `test_no_blocking_http_call_runs_on_the_event_loop` (def `:616`) — **modify** —
  replace the `_blocking`/`_heartbeat`/`ticks > 5` body with the handshake. Keep
  the docstring, `assert result.submitted is True`, `assert recorder.thread_ids`,
  and `assert recorder.thread_ids[0] != threading.get_ident()`. Verified replacement
  (starting after `monkeypatch.setenv(_TOKEN_VAR, _EXPORTED_TOKEN)`):

  ```python
      recorder = _Recorder()
      loop = asyncio.get_running_loop()

      # A thread<->loop handshake proves the submit runs off the loop without
      # counting ticks inside a wall-clock window (the Consiliency/pmcp#226 flake
      # class). The loop-side `_acker` can only set `acked` if the loop keeps
      # running *while* the submit blocks -- impossible if it ran on the loop
      # thread, in which case `acked.wait(5)` times out and fails by name.
      loop_alive = asyncio.Event()
      acked = threading.Event()

      async def _acker() -> None:
          await loop_alive.wait()
          acked.set()

      def _blocking(**kwargs: Any) -> FeedbackSubmission:
          recorder.calls.append(kwargs)
          recorder.thread_ids.append(threading.get_ident())
          loop.call_soon_threadsafe(loop_alive.set)
          assert acked.wait(5), (
              "the loop never acked while the submit blocked; "
              "the blocking submission ran on the event loop's thread"
          )
          return _created()

      _install_transport(monkeypatch, _blocking)

      ack_task = asyncio.ensure_future(_acker())
      try:
          result = await _submit(_gateway(submission=True), confirm_submission=True)
      finally:
          ack_task.cancel()

      assert result.submitted is True
      assert recorder.thread_ids, "the transport never ran"
      assert recorder.thread_ids[0] != threading.get_ident(), (
          "the blocking submission ran on the event loop's thread"
      )
  ```

### `tests/test_client_manager.py` (modify) — no new imports (`eventually` already imported at `:53`)

- `test_reconcile_does_not_spin_when_the_server_answers_with_a_storm`
  (class `TestDownstreamReconcileScheduler`, storm body around `:4271`–`:4301`) —
  **modify** — record `loop.time()` per `tools/list`, wait for two re-runs with
  `eventually`, assert the gap against a **literal 0.2** (see correction #1). Drop
  `await asyncio.sleep(0.6)` and both `spins` bounds. Verified replacement:

  ```python
          calls: list[str] = []
          # Stamp each reconcile re-run's `tools/list` on the loop clock. The gap
          # between consecutive re-runs is bounded below by the debounce, so a
          # lower bound on that gap proves the loop is not spinning -- without an
          # upper bound on work done inside a wall-clock window (the #226 class).
          stamps: list[float] = []

          async def storm_send(
              managed_arg: ManagedClient,
              method: str,
              params: dict[str, Any],
              *args: Any,
              **kwargs: Any,
          ) -> dict[str, Any]:
              calls.append(method)
              if method == "tools/list":
                  stamps.append(asyncio.get_running_loop().time())
                  # The server answers the listing by announcing another change.
                  manager._handle_downstream_notification(
                      "srv", managed_arg, "notifications/tools/list_changed"
                  )
                  return {"tools": [{"name": "alpha", "inputSchema": {}}]}
              return {}

          manager._send_request = storm_send  # type: ignore[method-assign]
          manager._handle_downstream_notification(
              "srv", managed, "notifications/tools/list_changed"
          )
          # Wait for two re-runs to land; the timeout is a hang guard, not a
          # measurement.
          # The count must be read at FAILURE time. An f-string in `message=`
          # is formatted when `eventually` is CALLED, so it would report the
          # pre-poll count -- informative-looking and stale.
          try:
              await eventually(lambda: len(stamps) >= 2, timeout=5.0)
          except AssertionError:
              raise AssertionError(
                  "reconcile re-ran fewer than twice: "
                  f"{calls.count('tools/list')} tools/list passes"
              ) from None

          # Cancel the (deliberately endless) loop before asserting.
          await manager._cancel_background_tasks()

          # Consecutive re-runs are spaced by at least the debounce. 0.2 is a
          # literal floor below the real 0.25 (_RECONCILE_RERUN_DEBOUNCE_S): a
          # lower bound on a real `asyncio.sleep`, so it cannot flake, yet it goes
          # red if the debounce is removed. Asserting against the imported
          # constant would be a tautology -- the mutation that zeroes the debounce
          # would zero the threshold too.
          gap = stamps[1] - stamps[0]
          assert gap >= 0.2, (
              f"reconcile re-runs are not debounced: {gap:.3f}s gap "
              "(expected >= _RECONCILE_RERUN_DEBOUNCE_S = 0.25)"
          )
  ```

- Two `_gone` pollers — **modify** — convert each bounded `for _ in range(30)` loop +
  `assert await _gone(...)` into a sync `_gone` predicate + `await eventually(...)`,
  timeout 3.0, message preserved. The two blocks are byte-identical except the
  assert messages; edit each by its distinct messages.
  - `test_real_process_tree_is_reaped` (`_gone` def `:3637`, asserts `:3646`–`:3647`):
    ```python
            def _gone(pid: int) -> bool:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                return False

            await eventually(
                lambda: _gone(parent_pid), timeout=3.0, message="parent process was not reaped"
            )
            await eventually(
                lambda: _gone(child_pid),
                timeout=3.0,
                message="grandchild process was orphaned, not reaped",
            )
    ```
  - `test_sigterm_ignoring_grandchild_is_group_sigkilled` (`_gone` def `:3691`,
    asserts `:3700`–`:3702`): identical predicate; messages `"parent was not reaped"`
    and `"SIGTERM-ignoring grandchild was not group-SIGKILLed"`.

## Documentation impact

- `CHANGELOG.md` — **modify** — append one bullet under `### Fixed` (heading at
  `:369`), directly after the two C2a bullets at `:372`–`:373`, matching their form
  (C2a placed its timing entries under Fixed, not Changed). Suggested:
  `- **Tests: the wall-clock-window assertions now wait for a property or a
  timer lower bound.** The registry-off-loop and feedback-off-loop tests prove
  the blocking call runs off the event loop with a thread↔loop handshake instead
  of counting loop ticks in a fixed window; the reconcile-debounce test asserts a
  lower bound on the real inter-run gap instead of a spin count in 0.6 s; and the
  process-reaping pollers use `eventually` (slice C2c). See [#235](https://github.com/Consiliency/pmcp/issues/235), see [#226](https://github.com/Consiliency/pmcp/issues/226).`
  Do **not** add a duplicate `### Fixed` heading.

No other doc footprint — the `CONTRIBUTING.md` "Timing in tests" rule and the
`tests/_timing.py` docstring already state the policy this slice enforces; no
`src/` change, so no README/ARCHITECTURE/openapi impact.

## Dependencies & order

Independent edits; any order. The C2a work (`tests/_timing.py`, the `eventually`
import in `test_client_manager.py`) is already merged on `main` (commit `db34db8`),
so `eventually` is importable. No dependency on C2b (in review from its own
worktree). Do **not** touch `tests/conftest.py` (merged slices own it).

### Also update, or the file contradicts itself

- `tests/test_client_manager.py:4265` — the storm test's docstring still says it
  "asserts a hard ceiling on reconciles in a fixed window". After this slice the
  body asserts a debounce GAP instead; reword it. (grok, non-blocking.)

### Maintenance note on the literal floor

`assert gap >= 0.2` is deliberately DECOUPLED from
`_RECONCILE_RERUN_DEBOUNCE_S` (0.25) -- coupling them is what made the original
assertion vacuous under its own mutation. The cost is that the two can drift: a
0.21 s gap passes on purpose, and if the debounce constant is ever changed this
independent threshold must be revisited by hand. Stated here so the next change
to that constant does not silently outrun the test. (codex.)

## Verification

All commands below were run this planning session; the pasted results are
measured on this worktree (`main` @ `480fc5c`, `uv sync -p 3.10`). The executor
must re-run them after applying the edits. Fresh worktree first: `uv sync -p 3.10`
(else `uv run` uses the system pytest). Subset runs need `--cov-fail-under=0`.
Node ids validated with `--collect-only`.

1. **Lint** (proves the two `import time` removals are complete):
   ```
   uv run ruff check tests/test_package_identity_gate.py tests/test_feedback_egress.py tests/test_client_manager.py
   ```
   Measured after the edits: `All checks passed!` Measured with either `import time`
   left in place: `F401 [*] 'time' imported but unused`.

2. **The five converted tests, green against intact source:**
   ```
   uv run pytest \
     "tests/test_package_identity_gate.py::test_registration_resolves_off_the_event_loop" \
     "tests/test_feedback_egress.py::test_no_blocking_http_call_runs_on_the_event_loop" \
     "tests/test_client_manager.py::TestDownstreamReconcileScheduler::test_reconcile_does_not_spin_when_the_server_answers_with_a_storm" \
     "tests/test_client_manager.py::TestTerminateProcessTree::test_real_process_tree_is_reaped" \
     "tests/test_client_manager.py::TestTerminateProcessTree::test_sigterm_ignoring_grandchild_is_group_sigkilled" \
     -q --cov-fail-under=0 --timeout=90
   ```
   Measured: `5 passed in 0.44s` (down from `1.87s` for the pre-conversion versions —
   the fixed 0.5/0.4/0.6 s waits are gone).

3. **Full suite** from a checkout **not** under `/mnt/HC_Volume_105438154` (the
   volume-root `node_modules` produces ~107 spurious npm-identity failures — see the
   worktree-volume memory): `uv run pytest -q`. (Not run this session; run it from a
   clean-volume checkout at execution. Labelled here so the executor does not skip it.)

## Acceptance criteria

Each criterion names its red-turning mutation **with the measured result**. Every
mutation was reverted from a saved copy and proven byte-identical; the intact
tests were re-run green after each (so each mutation is red only under mutation).

- [ ] **Ticker windows replaced by the off-loop handshake (pkgid + egress).**
      Neither test contains `ticks`, `ticker`, `_heartbeat`, or `import time`; each
      has the `loop_alive`/`acked` handshake and keeps its `thread[0] != loop_thread`
      assertion.
      *Red by (pkgid):* inline the resolve — `handlers.py:5870`
      `resolved = await anyio.to_thread.run_sync(resolve_package_identity, package,
      abandon_on_cancel=True)` → `resolved = resolve_package_identity(package)`.
      **Measured:** `1 failed in 5.44s`; `AssertionError: assert False is True` at
      `test_package_identity_gate.py:988` (`out.registered is True`) — the handshake
      AssertionError is swallowed by the fail-closed `except Exception` at
      `handlers.py:5875`, so registration fails; the 5.44 s runtime is the stalled
      loop. Intact: passes.
      *Red by (egress):* inline the submit — `handlers.py:5154`
      `await anyio.to_thread.run_sync(functools.partial(submit_feedback_issue, …),
      abandon_on_cancel=True)` → a direct `submit_feedback_issue(repository=…, …)`
      call. **Measured:** `1 failed in 5.18s`; `AssertionError: assert False is True`
      at `test_feedback_egress.py:658` (`result.submitted is True`); handler logs
      `handlers.py:5202 Feedback submission raised: the loop never acked while the
      submit blocked; the blocking submission ran on the event loop's thread`. Intact:
      passes.

- [ ] **Debounce proven by a real-timer lower bound, not a spin count — and the
      threshold is a literal, not the mutable constant.**
      `test_reconcile_does_not_spin_…` has no `await asyncio.sleep(0.6)` and no
      `spins`; it records `stamps` and asserts `gap >= 0.2`.
      *Red by:* `manager.py:169` `_RECONCILE_RERUN_DEBOUNCE_S = 0.25` → `= 0`.
      **Measured:** `1 failed in 0.26s`; `AssertionError: reconcile re-runs are not
      debounced: 0.000s gap (expected >= _RECONCILE_RERUN_DEBOUNCE_S = 0.25)` at
      `test_client_manager.py:4326`. Intact: `1 passed in 0.34s`. (Had the threshold
      been the imported constant, `gap >= 0` would stay green under this mutation —
      the tautology this criterion exists to avoid.)

- [ ] **The two `_gone` reaping pollers report by message at their hang guard, not
      by hanging.** Each is a sync `_gone` predicate wrapped in `await eventually(…,
      timeout=3.0, message=…)`; no `for _ in range(30)` loop remains in either.
      *Red by:* neuter reaping — insert `return` at the top of
      `_terminate_process_tree` body (`manager.py`, before `_signal(kill=False)`).
      **Measured:** `2 failed in 6.37s` (~3 s each); `AssertionError: parent process
      was not reaped` raised from `tests/_timing.py:62` (eventually's deadline) for
      `test_real_process_tree_is_reaped`, and the sigterm test fails the same way —
      proving the converted poller fails **by name at the 3 s guard**, not a hang.
      Intact: both pass.

## Execution Policy

- execute: effort=low, reason=test-only edits, every replacement block pasted
  verbatim from a version already run green + red this session; the one
  non-mechanical decision (the literal `0.2` debounce floor vs. the constant) is
  made and measured here.

## Notes for the executor

- The pkgid/egress mutations fail through the handler's fail-closed path (`assert
  out.registered is True` / `assert result.submitted is True`), **not** the
  `acked.wait` line — expected; see correction #3. Do not "fix" this by removing
  the fail-closed except; it is production behaviour.
- Never revert a mutation with `git checkout --` (prohibited here; discards
  uncommitted work). `cp` from a saved copy and `diff -q` to prove byte-identical.
- Some reaping mutations leave real child processes alive for up to 60 s after the
  test fails; harmless, they self-exit. Run reaping mutations with `--timeout` set.
- Do not convert `assert calls.count("tools/list") == N` at
  `test_client_manager.py:4043/:4049/:4067` — deterministic counts, not a window.
