# Detailed plan: make the intermittent runtime-test hang fail fast with a traceback

> **Revision 2 (2026-09-02).** Rev 1 boarded 2 DISAGREE / 1 AGREE. Five real
> defects, fixed below; one seat claim checked and **rejected**.
> (1) **`asyncio.wait_for` does not bound a cancellation-resistant task** — it
> cancels, then *waits for the cancellation to finish*. Confirmed empirically:
> a coroutine that catches `CancelledError` and continues survived
> `wait_for`, an outer `wait_for` guard, **and** `asyncio.run`'s shutdown; the
> probe had to be killed. So rev 1's fail-fast wrapper could itself hang, and
> `timeout_method = "thread"` would then `os._exit` the whole run instead of
> printing the diagnostic. (2) The count freezes contradicted the three new
> tests they were meant to coexist with. (3) The faulthandler criterion demanded
> a stack dump plus continuation; the planned test only read the ini value.
> (4) The teardown subprocess would take the **temp dir** as rootdir, so the
> project's `timeout_method` would never load and the loop could certify a
> method CI does not use. (5) `config.getini()` returns a float while
> `[tool.pytest.ini_options]` scalars load as strings, so `isinstance(v, int)`
> is invalid.
>
> **Rejected, with evidence:** one seat reported that 3.1.1 holds `_ShutdownState`
> in `threading.local()` rather than a `ContextVar`, and that the plan's research
> replaces "one stale story with another". Checked in both venvs
> (`/home/viperjuice/code/pmcp`, py 3.10.12 — and this worktree, py 3.14.7): both
> resolve **sse_starlette 3.1.1**, and in both, `sse.py` contains `ContextVar`
> and **no** `threading.local`. The research below stands as written.

## Task

Close the diagnostic half of Consiliency/pmcp#200. Five `test (3.x)` jobs in the
last week stalled inside `tests/runtime/test_emitter_harness.py` and were killed
by `timeout-minutes: 25`. GitHub reports a timed-out job as **cancelled**, not
failed, so the flake has never produced a red X, a traceback, or a single line of
evidence about *which await* is stuck.

**This plan ships stage 1 only: turn a 25-minute silent cancel into a fast
failure that names the stuck await.** It does **not** fix the root cause. The
root cause is not known, and a fix chosen now would be a guess frozen into the
suite — the failure class this repo has hit repeatedly (see rev 2 of
`detailed-sha-pin-actions`, and #200's own first close). Stage 2 is filed as a
follow-up and starts when an occurrence produces the traceback this change
exists to capture.

## Research summary

Verified in this worktree and against the installed environment on 2026-09-02.

**The five stalls, from the CI logs.** Every timed-out job's last `PASSED` line
is in `TestRemoteEmitterReachesDispatch`, at ~3% of the suite, followed by ~24
minutes of silence and `Terminate orphan process: pid (…) (pytest)`:

| run | date | job | last PASSED |
|---|---|---|---|
| 32930058835 | 08-26 | test (3.11) | `test_add_tool_then_emit_reaches_read_sse` |
| 33240369148 | 08-29 | test (3.10) | `test_unrecognised_method_reaches_read_sse` |
| 33270684206 | 08-29 | test (3.10) | `test_unrecognised_method_reaches_read_sse` |
| 33343639560 | 08-31 | test (3.11) | `test_add_tool_then_emit_reaches_read_sse` |
| 33532264811 | 09-01 | test (3.12) | `test_unrecognised_method_reaches_read_sse` |

**"The next test hangs" is an inference, not a finding.** pytest prints `PASSED`
when the test function returns — *before* teardown. With `asyncio_mode = "auto"`,
pytest-asyncio then tears the loop down (cancels lingering tasks,
`shutdown_asyncgens`). An async generator that ignores cancellation hangs
*there*, and the log is indistinguishable from "the next test hung". The spy in
`test_emitter_harness.py:65` (`_tap()`) is itself an async generator wrapped
around the SSE read stream, so the just-passed test's teardown is a live
candidate. **Any instrumentation that only covers test bodies can miss this.**

**Where a hang can live at all.** The four `TestRemoteEmitterReachesDispatch`
bodies poll with `for _ in range(50): await asyncio.sleep(0.05)` — **bounded at
2.5 s**, so the poll is not it. The unbounded awaits are exactly three:
`manager.connect_server(...)`, `manager.disconnect_all()` (in `finally`), and
**`await task` in `run_fake_remote`'s `finally`** (`tests/runtime/fake_remote.py`,
around line 258) — where uvicorn's `serve()` may never return if a response
generator never finishes. Plus the pytest-asyncio teardown above.

**The `sse_starlette` mechanism has changed, and #200's own comment is stale.**
The installed version is **3.1.1**. `AppStatus` has **no `should_exit_event`** —
`vars(AppStatus)` is `{should_exit: False, original_handler, handle_exit}`. The
shutdown path is now (a) a per-event-loop `_ShutdownState` held in a
`contextvars.ContextVar`, and (b) `_get_uvicorn_server()`, which reads
`signal.getsignal(signal.SIGTERM).__self__` and polls **that** object's
`should_exit`. So the fix suggested in my #200 comment ("reset
`AppStatus.should_exit_event = None`") **cannot be implemented as written**, and
the module comments in `fake_remote.py:259-266` and `test_downstream_remote.py:37`
describe an older version's mechanism. Two consequences: a stale SIGTERM handler
left by an earlier uvicorn server can make a *later* loop's watcher see
`should_exit == True`; and the per-loop `_ShutdownState` means an event
registered on one loop is never signalled by a watcher on another. Both are
*plausible* paths to a stuck stream — **neither is verified**, which is precisely
why this plan instruments rather than fixes.

**No local reproduction.** `tests/mcp2x` + `test_emitter_harness.py` in one
process: **107 passed, 3/3 runs**, ~17 s each. A full-suite run with coverage and
`faulthandler_timeout=120` is running as this plan is written; the plan assumes it
also passes. Absence of a local repro is expected for a timing-dependent flake
and is not evidence against it.

**The measurement is already done** (`--durations=25`, full suite, this
worktree, 2026-09-02): the slowest non-`live` items are
`tests/test_progressive_disclosure.py::TestScenario7…::test_invoke_query_docs`
and `…TestScenario8…::test_invoke_query_docs_conceptual` at **60.06 s each**
(both land on exactly 60 s, which looks like an internal timeout rather than
real work — noted, not in scope), then
`test_subscriptions_e2e.py::test_connect_disconnect_refresh_each_deliver_all_three_kinds`
at 19.06 s and `test_shutdown_handles_timeout` at 10.08 s. So: **`timeout = 600`**
(10× the slowest) and **`faulthandler_timeout = 120`** (2× the slowest, and well
below the kill). The implementer does not need to re-run the measurement; it must
re-run only if it changes either number.

**Host quirk, not a regression.** A full run *from this worktree* reports ~107
failed / 106 errors in `test_version_checker.py`, `test_npm_resolver.py` and
`test_tools.py`, because `/tmp/package.json` exists on this host and the npm
identity resolver's `localPrefix` walk finds it (documented in
`tests/conftest.py`). The same suite from `/home/viperjuice/code/pmcp` was
**3386 passed, 3 skipped, 25 deselected in 7m40s, zero faulthandler dumps**.
Compare like with like: the acceptance counts below mean *the same command from
the same directory*, before and after.

**Tooling facts.** pytest is **9.0.2**; `faulthandler_timeout` /
`faulthandler_exit_on_timeout` are **built-in ini options** (no dependency).
`pytest_timeout` is **not installed**, so the `timeout` marker declared in
`pyproject.toml:146` and applied at `tests/test_manifest_provision.py:283` is
**inert today**; installing the plugin activates it — that test sits inside
`@pytest.mark.live` (`:256`), which `addopts = "-m 'not live'"` deselects, so
default and CI runs are unaffected. CI runs
`uv run pytest tests/ -v --tb=short --cov …` (`.github/workflows/test.yml:54`).
The `changelog` job requires an entry only for `src/` changes; this change
touches none.

## Changes

### `pyproject.toml` (modify)

- `[tool.pytest.ini_options]` — add `faulthandler_timeout` — dump every thread's
  stack when a single test item exceeds the threshold **and let the run
  continue**. This is the zero-risk half: it kills nothing, and on the next CI
  hit it prints the stack of the stuck await into the job log. Set in the ini
  file, not the CI command line, so a local run behaves like CI.
- `[tool.pytest.ini_options]` — add `timeout = 600` and `timeout_method` — the
  fail-fast half, via `pytest-timeout`. 600 s is 10× the measured slowest
  non-`live` test (60.06 s, above); `faulthandler_timeout = 120` is 2× it, so the
  stack dump lands 8 minutes before the kill and both sit inside the job's
  `timeout-minutes: 25`. Put the measured test name and both numbers in a comment
  on the setting, so the next person changing them knows what they were derived
  from. `timeout_method` is chosen by the teardown-coverage proof below, not
  assumed.
- `[dependency-groups] dev` (or the existing test extra — match where
  `pytest-cov` lives, around `:109-112`) — add `pytest-timeout>=2.3`.

### `uv.lock` (modify)

- Regenerate with `uv lock` — the `install-smoke` and `min-version-smoke` jobs
  install from it.

### `tests/runtime/fake_remote.py` (modify)

- `run_fake_remote`'s `finally` — modify — bound the wait for the server task.
  **Not `asyncio.wait_for`**: it cancels and then waits for the cancellation to
  complete, so a coroutine that swallows `CancelledError` hangs it exactly as
  hard as the original `await`. **WAS WRONG (rev 1).** Use the form that never
  awaits a pending task indefinitely:

  ```python
  done, pending = await asyncio.wait({task}, timeout=SERVE_STOP_TIMEOUT)
  if pending:
      task.cancel()
      done, pending = await asyncio.wait({task}, timeout=CANCEL_GRACE)
  ```

  then **raise regardless of whether `pending` is still non-empty**, leaving the
  task orphaned and saying so in the message. The message names the port, the
  server's `started` / `should_exit`, `AppStatus.should_exit`, and whether the
  task survived cancellation — that last fact is the single most valuable bit
  for stage 2, and no stack dump reports it (an idle loop in `epoll.poll()`
  names nothing).
  Pick `SERVE_STOP_TIMEOUT` = 10 s and `CANCEL_GRACE` = 5 s: uvicorn's graceful
  drain here is unbounded (`uvicorn.Config` is constructed without
  `timeout_graceful_shutdown`, `fake_remote.py:224-226`), and a healthy stop in
  this harness is sub-second — 10 s is ~20× the observed cost, far below any
  pytest-level timeout.
- `AppStatus.should_exit = False` — modify — move into a **nested unconditional
  `finally`** around the whole stop sequence. **WAS WRONG (rev 1):** "stays
  exactly as it is" — it currently sits *after* `await task` (`:266`), so the new
  diagnostic raise would skip it and poison the next test with the very latch
  this harness exists to clear.
- The comment block at `:259-266` — modify — correct it to the 3.1.1 mechanism
  (`_ShutdownState` contextvar + `_get_uvicorn_server()` SIGTERM introspection;
  no `should_exit_event`). State that the reset remains correct and necessary,
  and that the surrounding claim about *why* was written against an older
  version.

### `tests/runtime/test_downstream_remote.py` (modify)

- The module docstring at `:37` — modify — same version correction, one
  sentence. Do not touch
  `test_a_poisoned_app_status_does_not_break_the_next_server` (`:228`); it still
  pins real behaviour.

### `tests/runtime/test_hang_diagnostics.py` (create)

The acceptance proof. "The suite no longer hangs" **passes on unchanged `main`**
— it is not a criterion. So induce a hang deterministically and assert the
diagnostics fire, mutant-style, as in `.consiliency/evidence/mutation-217.md`:

- `test_a_server_task_that_never_finishes_raises_instead_of_hanging` — patch the
  server so `serve()` never returns; assert `run_fake_remote`'s exit raises with
  the diagnostic within ~2× `(SERVE_STOP_TIMEOUT + CANCEL_GRACE)`.
- `test_a_serve_task_that_swallows_cancellation_still_raises` — the **mutant rev
  1 would have failed**: `serve()` catches `CancelledError` and keeps running.
  Same bound, same assertion. Without this case the wrapper can be written with
  `wait_for` and look correct. Guard the whole test with an outer bound so a
  regression here fails rather than hangs the file.
- `test_the_timeout_plugin_is_active_and_covers_teardown` — a **subprocess**
  pytest run over a temp file whose **fixture teardown** sleeps 30 s. Pass the
  settings explicitly — `-o timeout=3 -o timeout_method=<the project's value>` —
  because the temp file makes the temp dir the rootdir and the project's
  `[tool.pytest.ini_options]` would never load. **WAS WRONG (rev 1):** a bare
  `--timeout=3` would have certified Linux's default `signal` method while CI
  ran another. Assert non-zero exit within ~15 s, and bound the subprocess with
  `timeout=` so a failure is a failure, not a hang.
  **This must be verified empirically, not assumed**: if the chosen method does
  not cover teardown, it cannot catch the most likely hang site and the setting
  changes until this passes.
- `test_a_teardown_hang_is_not_caught_without_the_plugin` — the negative half:
  the same subprocess with `-p no:timeout`, asserting it does **not** finish
  within ~10 s (then killing it). Proves the positive result came from the
  plugin and not from something else in the environment.
- `test_faulthandler_dumps_a_stack_and_the_run_continues` — a subprocess with a
  short `-o faulthandler_timeout=2`, a larger `-o timeout=30`, one blocking item
  and a **sentinel test after it**; assert the output contains the faulthandler
  header (`Thread 0x`), that the sentinel ran, and that pytest exited normally.
  **WAS WRONG (rev 1):** the planned test only read the ini values, which cannot
  prove either the dump or the continuation the criterion claims.
- `test_faulthandler_timeout_is_below_the_kill_timeout` — compare the two
  resolved settings **numerically**, via `float(...)` on both. **WAS WRONG (rev
  1):** `isinstance(value, int)` — `config.getini()` returns a float for this
  option and `[tool.pytest.ini_options]` scalars load as strings, so that
  assertion is invalid either way.

### `.consiliency/evidence/hang-diagnostics-200.md` (create)

- The transcript: the induced-hang runs with their exit codes and elapsed times,
  the `--durations=25` measurement behind the chosen timeout, and the proof that
  a fixture-teardown hang is caught. Same shape as `mutation-217.md`.

## Documentation impact

- `CHANGELOG.md` — **none**. No `src/` change; the `changelog` job requires an
  entry only for `src/` changes.
- Consiliency/pmcp#200 — comment (with the PR) — correct the stale
  `should_exit_event` hypothesis in my earlier comment, state the 3.1.1
  mechanism, and record that stage 1 ships diagnostics only.
- A **new follow-up issue** (stage 2) — create — "root-cause the runtime SSE
  hang once a traceback exists", linking #200 and naming the three candidate
  sites plus the pytest-asyncio teardown path.

## Dependencies & order

1. `pytest-timeout` in `pyproject.toml` + `uv lock` first — nothing else can be
   verified without the plugin installed.
2. Measure `--durations=25`; only then choose `timeout`, and set
   `faulthandler_timeout` strictly below it.
3. `fake_remote.py` bounded `await task` + the corrected comment.
4. `test_hang_diagnostics.py`; iterate `timeout_method` until the
   teardown-coverage test passes.
5. Evidence file, then the #200 comment and the stage-2 issue.

## Verification

```bash
uv sync --all-extras --dev
uv run pytest -q tests/runtime/test_hang_diagnostics.py          # the three proofs
uv run pytest -q tests/runtime                                    # 60 passed, unchanged
uv run pytest -q tests/ --durations=25 | tail -30                 # the measurement
uv run ruff check src/ tests/ scripts/ && uv run ruff format --check src/ tests/ scripts/
uv run mypy src/
uv run python scripts/check_workflows.py --base-ref origin/main   # untouched, must stay 0

# Fail-closed proof for the plugin, both directions:
#   (a) with `timeout` set    -> the teardown-hang fixture run exits non-zero fast
#   (b) with `-p no:timeout`  -> the same run hangs (kill it) — proving (a) was the plugin
```

Edge cases: a legitimately slow test near the threshold (the measurement exists
to avoid this); a coroutine that survives cancellation, which is why the stop
sequence raises even with the task still pending; `timeout_method = "thread"`
terminates the whole process, losing the rest of the run — if the teardown-coverage test passes only under `thread`,
say so explicitly in the evidence and accept it, because a lost tail is strictly
better than a 25-minute cancel; the `live`-marked `timeout(130)` marker becoming
active (deselected by default — assert that `-m 'not live'` collection is
unchanged at 1000/1019).

## Acceptance criteria

- [ ] `pytest-timeout` is installed via `pyproject.toml` + `uv.lock`, and
      `uv run pytest --collect-only -q tests/` reports **19 deselected**, the
      same `live` set as `main`, with total collected = main's 1019 **plus** the
      new tests. **WAS WRONG (rev 1):** it froze the total at 1000/1019 while
      this same plan adds default-collected tests — an executor obeying it
      literally would have deleted the proofs.
- [ ] A fixture whose **teardown** blocks for 30 s is killed in under ~15 s with
      a non-zero exit naming the timeout, in a subprocess given the project's
      `timeout` **and** `timeout_method` explicitly via `-o`; and the same run
      with `-p no:timeout` does **not** finish in ~10 s. Both directions.
      Teardown coverage is the whole point: the likeliest hang site is after
      `PASSED` is printed.
- [ ] A subprocess run with a short `faulthandler_timeout` and a blocking item
      emits a `Thread 0x…` dump, **the sentinel test after it still runs**, and
      pytest exits normally — the dump does not end the session. Separately,
      `float(faulthandler_timeout) < float(timeout)` on the committed settings,
      so the dump always precedes the kill.
- [ ] `run_fake_remote` raises within ~2×(`SERVE_STOP_TIMEOUT` + `CANCEL_GRACE`)
      naming port, server state, `AppStatus.should_exit`, and whether the task
      survived cancellation — in **both** mutants: a `serve()` that never
      returns, **and one that swallows `CancelledError`**. On unchanged `main`
      both hang indefinitely; a `wait_for`-based implementation passes the first
      and hangs on the second.
- [ ] `AppStatus.should_exit` is `False` after that diagnostic raise — asserted
      in the mutant tests, because the reset now has to survive an exception on
      the path that used to reach it only on success.
- [ ] The chosen `timeout` is ≥ 10× the slowest non-`live` test as measured by
      `--durations=25` in this change, and the measured value is recorded in the
      evidence file.
- [ ] The 60 pre-existing `tests/runtime` tests still pass (the directory now
      reports 60 + the new file's count), and the full suite's failures and
      skips are unchanged from `main` — passes increase by exactly the number of
      new tests. **WAS WRONG (rev 1):** "counts unchanged", which this plan's
      own additions make unsatisfiable.
- [ ] No file under `src/` is modified (`git diff --name-only origin/main…HEAD |
      grep '^src/'` is empty) — this is a diagnostics change; any production
      timeout is stage 2 and a separate decision.

## Non-goals

- **Fixing the hang.** Stage 2, after a traceback exists.
- **A timeout on `ClientManager.connect_server`.** If the captured evidence shows
  an unbounded client-side await, that is a production robustness change with
  its own blast radius — file it, do not fold it in here.
- Rewriting the emitter harness or the `_tap()` spy.
- Raising `timeout-minutes: 25` on the `test` job; a shorter job timeout is not a
  fix, and the guard in `check_workflows.py` bounds it to 10..30 deliberately.

## Execution Policy

- execute: effort=medium, reason=small diff but the teardown-coverage question is empirical and the acceptance criteria must be proven in both directions
