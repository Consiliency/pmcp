# Detailed plan: make the intermittent runtime-test hang fail fast with a traceback

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
- `[tool.pytest.ini_options]` — add `timeout` and `timeout_method = "thread"` —
  the fail-fast half, via `pytest-timeout`. **Choose the value from a
  measurement, not a guess**: run `--durations=25` on the full suite and set the
  timeout to roughly 10× the slowest non-`live` test, floor 120 s. Record the
  measured slowest test and the chosen number in a comment on the setting.
- `[dependency-groups] dev` (or the existing test extra — match where
  `pytest-cov` lives, around `:109-112`) — add `pytest-timeout>=2.3`.

### `uv.lock` (modify)

- Regenerate with `uv lock` — the `install-smoke` and `min-version-smoke` jobs
  install from it.

### `tests/runtime/fake_remote.py` (modify)

- `run_fake_remote`'s `finally` — modify — wrap `await task` in
  `asyncio.wait_for(..., timeout=<N>)`; on `TimeoutError`, cancel the task, await
  it suppressing `CancelledError`, and **raise** with a message naming the port,
  the server's `started`/`should_exit` state, and `AppStatus.should_exit`. This
  is the single most likely hang site and the one a stack dump describes worst
  (an idle loop in `epoll.poll()` names nothing). `AppStatus.should_exit = False`
  on both entry and exit stays exactly as it is.
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
  server object so `serve()` never returns; assert `run_fake_remote`'s exit
  raises within ~2× the wait, with the diagnostic message, rather than hanging.
- `test_the_timeout_plugin_is_active_and_covers_teardown` — a subprocess pytest
  run (`--timeout=3`) over a temp file whose **fixture teardown** sleeps 30 s;
  assert it exits non-zero within ~15 s and the output names the timeout.
  **This is the one that must be verified empirically, not assumed**: if the
  chosen `timeout_method` does not cover teardown, the plugin cannot catch the
  most likely hang site and the setting must change until this test passes.
- `test_faulthandler_timeout_is_configured` — read the resolved ini value; assert
  it is an int, ≥ 60, and **strictly less than** the `timeout` value, so the
  stack dump always lands *before* the kill. A dump after the kill is useless.

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
to avoid this); `timeout_method = "thread"` terminates the whole process, losing
the rest of the run — if the teardown-coverage test passes only under `thread`,
say so explicitly in the evidence and accept it, because a lost tail is strictly
better than a 25-minute cancel; the `live`-marked `timeout(130)` marker becoming
active (deselected by default — assert that `-m 'not live'` collection is
unchanged at 1000/1019).

## Acceptance criteria

- [ ] `pytest-timeout` is installed via `pyproject.toml` + `uv.lock`, and
      `uv run pytest --collect-only -q tests/` still reports **1000/1019
      collected (19 deselected)** — the marker activation changes nothing that
      runs by default.
- [ ] A fixture whose **teardown** blocks for 30 s is killed by the plugin in
      under ~15 s with a non-zero exit and a message naming the timeout —
      **proven in a subprocess run, and proven to hang without the plugin**
      (`-p no:timeout`). Teardown coverage is the whole point: the most likely
      hang site is after `PASSED` is printed.
- [ ] `faulthandler_timeout` is set, is strictly less than `timeout`, and a test
      that blocks longer than it emits a `Thread 0x…` stack dump into the run
      output while the run continues.
- [ ] `run_fake_remote` with a `serve()` that never returns **raises within
      ~2×N seconds** naming port, server state and `AppStatus.should_exit` —
      where on unchanged `main` the same scenario hangs indefinitely.
- [ ] The chosen `timeout` is ≥ 10× the slowest non-`live` test as measured by
      `--durations=25` in this change, and the measured value is recorded in the
      evidence file.
- [ ] `tests/runtime` still reports **60 passed**, and the full suite's
      pass/skip/deselect counts are unchanged from `main`.
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
