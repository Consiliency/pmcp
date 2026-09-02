# Hang-diagnostics evidence — Consiliency/pmcp#200 (stage 1)

Five `test (3.x)` jobs in the week to 2026-09-01 stalled inside
`tests/runtime/test_emitter_harness.py` and were killed by the job's
`timeout-minutes: 25`. GitHub reports a timed-out job as **cancelled**, not
failed, so not one of them produced a red X, a traceback, or a single line of
evidence about which await was stuck.

This change ships **diagnostics only**. It does not fix the hang; the root cause
is still unknown and a fix chosen now would be a guess frozen into the suite.
Stage 2 begins when an occurrence produces the traceback this change exists to
capture.

"The suite no longer hangs" passes on unchanged `main`, so it is not a
criterion. Every proof below induces a hang deterministically and asserts the
diagnostic fires, mutant-style (`mutation-217.md`).

All runs: worktree `pmcp-sse-hang`, branch `fix/200-sse-hang-diagnostics`, base
`origin/main` @ `d743bc4`, Python 3.14.7, pytest 9.0.2, pytest-timeout 2.4.0,
sse_starlette 3.1.1, on 2026-09-02.

## The measurement behind the two numbers

`uv run pytest tests/ -q --durations=25`, full suite, this worktree, **before**
any change (the tree differed from `origin/main` only by the plan document):

```
60.25s call  tests/test_progressive_disclosure.py::TestScenario8_LibraryConcepts::test_invoke_query_docs_conceptual
60.22s call  tests/test_progressive_disclosure.py::TestScenario7_LibraryDocumentation::test_invoke_query_docs
26.16s call  tests/runtime/test_emitter_harness.py::TestRemoteEmitterReachesDispatch::test_emit_alone_reaches_read_sse
18.48s call  tests/runtime/test_subscriptions_e2e.py::test_connect_disconnect_refresh_each_deliver_all_three_kinds
10.11s call  tests/test_server_lifecycle.py::TestGatewayServerShutdown::test_shutdown_handles_timeout
```

Slowest non-`live` item: **60.25 s**. Both 60 s items land on almost exactly
60 s, which looks like an internal timeout rather than real work — noted, not in
scope. The third entry is worth recording: the file that hangs in CI is also the
third-slowest locally, at 26 s, which is consistent with a timing-sensitive path
rather than a deterministic deadlock.

Chosen and committed:

| setting | value | derivation |
|---|---|---|
| `faulthandler_timeout` | 120 | 2× the slowest (60.25 s) |
| `timeout` | 700 | ≥10× the slowest (10 × 60.25 = **602.5**) |
| `timeout_method` | `signal` | selected empirically, below |

**Deviation from the plan, stated deliberately.** The plan names `timeout = 600`
and *also* requires "the chosen `timeout` is ≥ 10× the slowest non-`live` test as
measured by `--durations=25` **in this change**". With the slowest at 60.25 s
those two clauses cannot both hold: 600 is 9.96×. (The plan's own figure, 60.06 s,
already made 10× = 600.6 > 600, so the arithmetic was off in the plan too.) The
acceptance criterion wins; 700 s satisfies it, keeps the dump ~9.7 minutes ahead
of the kill, and still sits far inside `timeout-minutes: 25`.

## `timeout_method`: selected, not assumed

The likeliest hang site is **after** the last `PASSED` line — pytest prints
`PASSED` when the test function returns, *before* teardown, and with
`asyncio_mode = "auto"` pytest-asyncio then tears the loop down (cancels
lingering tasks, `shutdown_asyncgens`). The `_tap()` spy at
`test_emitter_harness.py:65` is itself an async generator wrapped around the SSE
read stream, so the just-passed test's teardown is a live candidate and a method
that does not cover teardown is worthless here.

Both settings are passed with `-o` because the temp test file makes the temp
directory the rootdir, so the project's `[tool.pytest.ini_options]` never loads —
a bare `--timeout=3` would have certified Linux's default while CI ran another.

The induced module:

```python
@pytest.fixture
def blocks_on_the_way_out():
    yield
    time.sleep(30)          # hangs in TEARDOWN, after PASSED is printed

def test_passes_then_hangs_in_teardown(blocks_on_the_way_out):
    assert True
```

```
$ python -m pytest test_induced.py -p no:cacheprovider -o timeout=3 -o timeout_method=signal -q
___________ ERROR at teardown of test_passes_then_hangs_in_teardown ____________
    @pytest.fixture
    def blocks_on_the_way_out():
        yield
>       time.sleep(30)
E       Failed: Timeout (>3.0s) from pytest-timeout.
1 passed, 1 error in 3.02s
EXIT=1  elapsed=3.37s

$ python -m pytest test_induced.py -p no:cacheprovider -o timeout=3 -o timeout_method=thread -q
  File ".../_pytest/fixtures.py", line 924, in _teardown_yield_fixture
    next(it)
  File ".../test_induced.py", line 9, in blocks_on_the_way_out
    time.sleep(30)
+++++++++++++++++++++++++++++++++++ Timeout ++++++++++++++++++++++++++++++++++++
EXIT=1  elapsed=3.26s          # no summary line -- os._exit killed the session
```

**Both** methods cover teardown, so the plan's fallback ("if the teardown test
passes only under `thread`, say so and accept it") was not needed. `signal` is
selected because it is strictly better on the axis that separates them: it
reports a normal pytest ERROR with the offending frame **and the session
continues to a summary**, whereas `thread` hard-exits the process mid-report and
loses the rest of the run. The plan was willing to trade the tail away; it does
not have to be.

The negative half, proving the kill above came from the plugin and not from
anything else in the environment — same module, `-p no:timeout`, no `-o timeout`
overrides (those options do not exist once the plugin is gone, and an unknown-ini
error would have exited fast and satisfied nothing):

```
$ timeout 10 python -m pytest test_induced.py -p no:cacheprovider -p no:timeout -q
.
EXIT=124  elapsed=10.01s        # 124 == still running when killed at 10 s
```

The `.` is the test passing; the process then sat in teardown until killed.

## `faulthandler`: it dumps, and the run continues

The zero-risk half. A subprocess with `-o faulthandler_timeout=2 -o timeout=30`,
a 5 s blocking item and a sentinel item after it: the dump fires, the blocking
item still completes, the sentinel still runs, and pytest exits **0**. Asserted
in `test_faulthandler_dumps_a_stack_and_the_run_continues` on `Thread 0x`,
`Timeout (0:00:02)`, `2 passed`, and `returncode == 0`. Reading the ini value —
which is all the first revision of this plan proposed — could not have proven
either the dump or the continuation.

Separately, `float(faulthandler_timeout) < float(timeout) < 25*60` is asserted on
the committed settings, so the dump always precedes the kill and both precede the
job cap. (`getini` returns a float for one and a string for the other, so an
`isinstance(..., int)` check would have been invalid either way.)

## The async blind spot, and the watchdog that closes it

Everything above induces a **synchronous** hang (`time.sleep`), where a signal
traceback names the offending line trivially. The hang this change is actually
chasing is a **suspended coroutine**, and that is a different problem: a
suspended coroutine's frames live in `Task` objects, not on any thread stack.
Both `faulthandler` and `pytest-timeout` dump the *thread* stack. So they name
nothing.

Reproduced on this branch. A test awaiting an `Event` through a coroutine and an
async generator, killed by `-o timeout=8 -o timeout_method=signal`, reports this
and nothing more:

```
        ready = []
        try:
>           fd_event_list = self._selector.poll(timeout, max_ev)
E           Failed: Timeout (>8.0s) from pytest-timeout.

.../python3.14/selectors.py:452: Failed
```

`epoll.poll` — the stuck await is invisible. Without a fix, this PR would have
converted a 25-minute silent cancel into a 700-second failure that **still could
not tell `_tap()` from `connect_server()` from `disconnect_all()`**, which is the
one question #200 exists to answer.

`tests/runtime/_hang_watchdog.py` closes it. Same module, same command, with
`-p tests.runtime._hang_watchdog`:

```
[async-stacks] test_async_induced.py::test_hangs_inside_an_async_generator has been running 2.0s (threshold 2.0s) -- see Consiliency/pmcp#200
[async-stacks] 1 pending task(s) on <_UnixSelectorEventLoop running=True closed=False debug=False>:
--- <Task pending name='Task-2' coro=<test_hangs_inside_an_async_generator() running at .../test_async_induced.py:15> wait_for=<Future pending cb=[Task.task_wakeup()]> ...>
Stack for <Task pending name='Task-2' ...> (most recent call last):
  File ".../test_async_induced.py", line 15, in test_hangs_inside_an_async_generator
    await _drives_the_generator()
    [await chain] (what print_stack cannot reach):
      File ".../test_async_induced.py", line 15, in test_hangs_inside_an_async_generator
        await _drives_the_generator()
      File ".../test_async_induced.py", line 10, in _drives_the_generator
        async for _ in _tap_like_generator():
      File ".../test_async_induced.py", line 5, in _tap_like_generator
        await asyncio.Event().wait()
      File ".../python3.14/asyncio/locks.py", line 213, in wait
        await fut
      -- suspended on <_asyncio.FutureIter object at 0x75d1e5af82b0>
```

That is the whole point of the change, in one block: the exact coroutine, the
exact generator, the exact awaited line.

### Why `Task.print_stack()` alone was not enough

For a **suspended** coroutine `Task.get_stack()` returns a *single* frame — the
coroutine's own `cr_frame`, whose `f_back` is `None` because a suspended
coroutine sits on nobody's stack. That is the `Stack for <Task pending ...>`
section above, and on its own it names `test_hangs_inside_an_async_generator`
and stops. Everything beneath it is reachable only by walking `cr_await` /
`ag_await`, which is the `[await chain]` section.

That walk is **required, not a refinement**: the prime suspect is `_tap()` at
`test_emitter_harness.py:65`, an **async generator** wrapped around the SSE read
stream. `print_stack()` reports the task that drives such a generator; only the
`ag_frame`/`ag_await` branch names the generator itself. The mutant therefore
hangs *through* an async generator and asserts on the generator's own function
name, so that branch cannot ship as dead code.

One extra hop was needed and is worth recording. `async for x in agen()`
suspends on an `async_generator_asend`, whose only public attributes are
`close`/`send`/`throw` — no `ag_frame`, no `ag_await` — so the walk dead-ended
one hop short of the generator, printing `-- suspended on
<async_generator_asend object>`. Probed directly: `gc.get_referents()` on that
object returns exactly `[async_generator]`. `_step_through_opaque()` takes that
single hop, narrowly (first referent carrying a coroutine/generator frame) and
fully guarded.

### Both directions

| run | result |
|---|---|
| with `-p tests.runtime._hang_watchdog` | timeout fires, `[async-stacks]` present, chain names `_drives_the_generator` **and** `_tap_like_generator` |
| without it | timeout fires, `[async-stacks]` absent, traceback ends at the selector, `_tap_like_generator` nowhere in the output |

The negative run still *fails* — this is not "nothing happened" — it simply
cannot say where. Asserted in
`test_without_the_watchdog_an_async_hang_names_only_the_selector`.

The subprocess proofs certify the plugin's logic but not the wiring, so
`test_the_watchdog_is_wired_into_this_package` asserts, inside a normal run,
that `pytest_runtest_logstart` recorded *this* item's nodeid and that the autouse
async fixture handed the watchdog `asyncio.get_running_loop()`.

### Design constraints, and why

- **The loop must come from `asyncio.get_running_loop()` in an async fixture.**
  A watchdog thread cannot reach a running loop on its own, and
  `get_event_loop_policy().get_event_loop()` from a sync fixture raises
  `RuntimeError('no running event loop')`. An autouse *async* fixture is safe
  for this package's sync tests too — under `asyncio_mode = "auto"` they get a
  loop as well; verified against a mixed sync/async module before relying on it.
- **One session daemon thread, not a `threading.Timer` per test.** `Timer.start()`
  spawns a thread; per-item would be ~3400 thread creations across the suite. The
  thread starts lazily at the first `pytest_runtest_logstart`, so `--collect-only`
  spawns nothing.
- **Armed from `logstart`, cleared at `logfinish`**, so the window covers setup,
  call **and teardown**. Teardown coverage is the point, for the same reason it
  was for `timeout_method`.
- **It can never fail.** Every path swallows; the worst case is silence. A
  watchdog that can raise turns a diagnosable hang into a confusing error.
  `asyncio.all_tasks()` snapshots a WeakSet the loop is mutating, so from another
  thread it can raise `RuntimeError: Set changed size during iteration` — retried,
  then abandoned quietly.
- **60 s, strictly below `faulthandler_timeout = 120`**, so async stacks land
  before the thread dump and long before the 700 s kill.

### Scope: `tests/runtime/`, not `tests/`

Registered in `tests/runtime/conftest.py`. Three reasons, one of them decisive:

1. All five CI stalls happened in this package.
2. The slowest item here is **26.16 s** against a 60 s threshold — >2× headroom.
3. Decisive: at `tests/` scope the two progressive-disclosure tests at **60.22 s
   and 60.25 s** sit right on the threshold and would trip a spurious dump on
   essentially every run. A diagnostic that cries wolf every run is worse than
   none, and lowering its value to fit them would forfeit the headroom in 1–2.

A run of the whole package with the watchdog live produced **zero** dumps
(`grep -c "async-stacks"` → 0 over `69 passed in 123.43s`).

## The two `run_fake_remote` mutants

On unchanged `main`, `run_fake_remote`'s `finally` is `server.should_exit = True;
await task` — unbounded. uvicorn's graceful drain here is unbounded too
(`uvicorn.Config` is constructed without `timeout_graceful_shutdown`), so that
`await` is one of only three unbounded awaits the harness has.

The replacement is **not** `asyncio.wait_for`. `wait_for` cancels the task and
then waits for the cancellation to *finish*, so a `serve()` that swallows
`CancelledError` hangs it exactly as hard as the bare `await`. Confirmed
empirically while writing the plan: such a coroutine survived `wait_for`, an
outer `wait_for` guard, **and** `asyncio.run`'s own shutdown; the probe had to be
killed. The shipped form uses `asyncio.wait({task}, timeout=…)`, which reports a
pending task instead of awaiting it, and raises whether or not the cancel took.

| mutant | `serve()` behaviour | on `main` | with this change |
|---|---|---|---|
| `_NeverStops` | never returns, honours cancel | hangs forever | raises, names the port and state |
| `_SwallowsCancellation` | catches `CancelledError`, keeps running | hangs forever | raises, and reports **`SURVIVED cancellation`** |

The second mutant is the one that matters: a `wait_for`-based implementation
passes the first and hangs on the second, and without this case the wrapper can
be written wrongly and look correct.

Whether the task survived cancellation is the single most valuable fact for
stage 2, and **no stack dump reports it** — an idle loop parked in `epoll.poll()`
names nothing. So the message carries it explicitly, alongside the port,
`server.started`, `server.should_exit` and `AppStatus.should_exit`:

Both messages, as actually printed by driving each mutant through
`run_fake_remote` directly:

```
--- mutant 1: serve() never returns (elapsed 10.07s) ---
fake remote on port 44427 did not stop within 10.0s of should_exit=True; the serve() task ended only once cancelled [server.started=True server.should_exit=True AppStatus.should_exit=False] -- see Consiliency/pmcp#200
AppStatus.should_exit after the raise: False
--- mutant 2: serve() swallows CancelledError (elapsed 15.07s) ---
fake remote on port 57711 did not stop within 10.0s of should_exit=True; the serve() task SURVIVED cancellation after 5.0s and is now orphaned [server.started=True server.should_exit=True AppStatus.should_exit=False] -- see Consiliency/pmcp#200
AppStatus.should_exit after the raise: False
```

The elapsed times are the bound working: 10.07 s when the cancel lands
(`SERVE_STOP_TIMEOUT`), 15.07 s when it does not (`+ CANCEL_GRACE`). Both are
inside the acceptance bound of 2 × (10 + 5) = 30 s. On unchanged `main` both are
unbounded.

Both mutant tests also assert `AppStatus.should_exit is False` after the raise.
That is not decoration: the reset used to sit *after* `await task`, so the new
diagnostic raise would have skipped it and poisoned the next test with the very
latch this harness exists to clear. It now lives in a nested unconditional
`finally`.

Each mutant is bounded twice — by the stop sequence itself, and by an outer
`asyncio.wait` guard in the test — so a regression in either fails this file
rather than hanging it. The cancellation-swallowing orphan is retired through an
escape `asyncio.Event` in the test's `finally`, because pytest-asyncio's loop
teardown cancels and *gathers* lingering tasks and would otherwise hang on the
one task in the suite that ignores a cancel.

```
$ uv run pytest -q tests/runtime/test_hang_diagnostics.py
6 passed in 43.89s
EXIT=0
```

## Counts, before and after

Same command from the same directory, before and after, as the plan requires.
From this worktree ~107 tests in `test_version_checker.py`, `test_npm_resolver.py`
and `test_tools.py` fail for an unrelated host reason: `/tmp/package.json` exists
here and the npm identity resolver's `localPrefix` walk finds it (documented in
`tests/conftest.py`). That is a host quirk, not a regression, and it is identical
on both sides of the change.

| `uv run pytest tests/ -q --durations=25` | before | after |
|---|---|---|
| failed | 107 | 107 |
| passed | 3173 | 3179 |
| skipped | 3 | 3 |
| deselected | 25 | 25 |
| errors | 106 | 106 |

The two summary lines, verbatim:

```
before: 107 failed, 3173 passed, 3 skipped, 25 deselected, 106 errors in 349.49s (0:05:49)   EXIT=1
after:  107 failed, 3179 passed, 3 skipped, 25 deselected, 106 errors in 369.69s (0:06:09)   EXIT=1
```

Passes increase by exactly **6**, the number of new tests. Failures, skips,
deselections and errors are unchanged. Exit 1 on both sides is the
`/tmp/package.json` host quirk, not this change.

```
$ uv run pytest --collect-only -q tests/
before: 3389/3414 tests collected (25 deselected)
after:  3395/3420 tests collected (25 deselected)
```

+6 collected, +6 total, the `live` set unchanged at 25 deselected.

**Second deviation from the plan, stated deliberately.** The plan's first
acceptance criterion asks for "**19 deselected** … total collected = main's
**1019** plus the new tests". Those figures are rev-1 residue and contradict the
plan's own research section, which records "3386 passed, 3 skipped, **25
deselected**". Measured on the unmodified tree: 3389/3414 with 25 deselected. The
criterion's *structure* — deselected unchanged, collected up by exactly the new
tests — is satisfied against the real baseline.

## The rest of the verification block

```
$ uv run pytest -q tests/runtime
66 passed in 107.99s                      EXIT=0    # 60 pre-existing + 6 new
$ uv run ruff check src/ tests/ scripts/
All checks passed!                        EXIT=0
$ uv run ruff format --check src/ tests/ scripts/
129 files already formatted               EXIT=0
$ uv run mypy src/
Success: no issues found in 43 source files   EXIT=0
$ uv run python scripts/check_workflows.py --base-ref origin/main
workflow guards: OK                       EXIT=0
$ git diff --name-only origin/main..HEAD -- src/
(empty)
```

No file under `src/` is modified. This is a diagnostics change; any production
timeout — a bound on `ClientManager.connect_server`, for instance — is a stage-2
decision with its own blast radius.

## Correction to the record

`AppStatus` in the installed **sse_starlette 3.1.1** is exactly
`{should_exit, original_handler, handle_exit}`. There is **no
`should_exit_event`**, so the fix floated in #200's early comment ("reset
`AppStatus.should_exit_event = None`") cannot be written as stated. 3.1.1 keeps a
per-event-loop `_ShutdownState` in a `contextvars.ContextVar`, and
`_get_uvicorn_server()` reads `signal.getsignal(signal.SIGTERM).__self__` and
polls *that* object's `should_exit`.

Two consequences follow, both plausible and **neither verified**: a stale SIGTERM
handler left by an earlier uvicorn server can make a later loop's watcher see
`should_exit == True`, and a `_ShutdownState` registered on one loop is never
signalled by a watcher on another. That they are unverified is precisely why this
change instruments rather than fixes. The stale comments in `fake_remote.py` and
`test_downstream_remote.py` are corrected in this change; the reset itself
remains correct and necessary.
