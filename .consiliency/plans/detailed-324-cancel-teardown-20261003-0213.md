# Detailed plan: a cancelled caller keeps its cancellation through every teardown, and the teardown still completes

> Written on main `89559db` (dev0, a team host), worktree `pmcp-324`, branch
> `plan/324-cancel-teardown`. Every number below was measured on that tree, or
> on the spike of this plan applied to it, on CPython 3.10.20, 3.11 and 3.12.14.
> The spike was then removed; this PR carries only this file. The spike's
> source patch and its test module are reproduced verbatim at the end
> (*Verbatim bodies*).

## Task

Consiliency/pmcp#324, a non-blocking finding from the review panel on
Consiliency/pmcp#321 (see Consiliency/pmcp#287): `_teardown_outbound` in
`src/pmcp/client/manager.py`, and the read/stderr task waits in the
handshake-failure `except` blocks of `_connect_stdio` and
`_connect_remote_stream`, suppress `asyncio.CancelledError`. A caller cancelled
while parked in one of those waits loses its cancellation and keeps running
through the rest of the block. Re-raising at the await would be worse: it skips
the process-tree termination, the transport close and the `_clients` removal
that follow.

The brief widens this to the class: every place in `src/pmcp` that suppresses
`CancelledError` (`except asyncio.CancelledError: pass`,
`contextlib.suppress(asyncio.CancelledError)`, `except BaseException`,
`except (asyncio.CancelledError, Exception)`), each classified as either
awaiting a child it just cancelled (the child's cancel is expected) or able to
absorb the caller's own cancel. The fix must remember the caller's cancel
across the whole cleanup and re-raise it once the cleanup completes, on
Python 3.10–3.12 (3.10 has no `Task.cancelling()`/`uncancel()`); cleanup must
still complete, and the cancel must be neither lost nor doubled.

## Research summary

### The sites named in the issue (main `89559db`)

- `_connect_stdio` (`manager.py:2352`), handshake `except Exception` at
  `manager.py:2461`: cancels `read_task`/`stderr_task` and awaits each through
  `asyncio.shield`, swallowing `(asyncio.CancelledError, Exception)` at
  `manager.py:2469`; then `await self._teardown_outbound(managed)`, then
  `await _terminate_process_tree(process, name)`, then pops `_clients`, then
  `raise`.
- `_connect_remote_stream` (`manager.py:2695`), handshake `except Exception` at
  `manager.py:2789`: the same read-task wait (swallow at `manager.py:2796`),
  `_teardown_outbound`, `_close_remote_transport`, pop, `raise`.
- `_teardown_outbound` (`manager.py:3237`): cancels the writer, awaits
  `shield(writer)` (or `wait_for(shield(writer), timeout)`), swallows
  `(asyncio.CancelledError, Exception)` at `manager.py:3269`. Callers: the two
  handshake blocks and `disconnect_server` (`manager.py:1413`, `timeout=1.0`).

Awaited from the caller's own task, `except (CancelledError, Exception)` around
`await asyncio.shield(child)` cannot tell the child's cancellation from the
caller's: the shield raises `CancelledError` in the caller in both cases. A
`child.done()` check after the fact is not a reliable discriminator either:
when the child finishes and the caller is cancelled in the same loop iteration,
the shield's outer future is cancelled before its done-callback runs, so the
caller sees `CancelledError` with the child already done (mutant M10 below is
exactly that check, and `test_helper_caller_cancel_in_the_completion_step_is_kept`
is red against it).

### Every handler in `src/pmcp` that can catch a cancellation

Found by an AST walk of `src/pmcp/**/*.py` for `except` clauses naming
`CancelledError` or `BaseException` (or bare `except:`; there are none), plus
`grep -rn 'suppress(' src/pmcp` (every `contextlib.suppress` is
`suppress(OSError)`, none touches cancellation). 22 handlers on main. "Absorbs"
means the handler body contains no `raise`.

| # | Site (main) | Kind | Whose cancel can arrive | Verdict |
|---|---|---|---|---|
| 1 | `_connect_stdio` `manager.py:2469` | absorbs | the child's **or the caller's** | **defect** (issue) |
| 2 | `_connect_remote_stream` `manager.py:2796` | absorbs | child's or caller's | **defect** (issue) |
| 3 | `_teardown_outbound` `manager.py:3269` | absorbs | child's or caller's | **defect** (issue) |
| 4 | `disconnect_server` `manager.py:1403` (`wait_for(shield(read_task), 1.0)`) | absorbs | child's or caller's | **defect** (class) |
| 5 | `_cleanup_client` `manager.py:3673` (read/stderr/writer loop) | absorbs | child's or caller's | **defect** (class) |
| 6 | `_disconnect_all_unlocked._shutdown_one` `manager.py:3586` | absorbs | the gather's outer cancel | safe: a `gather` child; gather re-raises the outer cancel once every child is done (measured, test below) |
| 7 | `_close_remote_transport` `manager.py:2644` (timeout branch, `await task` on the owner it just cancelled) | absorbs | child's or caller's | safe after this plan: every caller is either a private teardown task (rows 1–5, after the fix) or a `_shutdown_one` gather child (row 6) |
| 8 | `_close_remote_transport` `manager.py:2661` (inside the caller-cancel branch) | absorbs | the owner's | safe: the enclosing branch re-raises the caller's cancel at `manager.py:2686` |
| 9 | `_close_remote_transport` `manager.py:2646` | reraises | caller's | correct already: escalates to the owner, then re-raises |
| 10 | `_own_remote_transport` `manager.py:2561` (`except BaseException`) | reraises | the owner task's own | forwards a pre-handoff failure (cancel included) into `ready`; otherwise re-raises. Not this class (see *Non-goals*) |
| 11 | `_connect_remote_stream` `manager.py:2755` (`except BaseException`, pre-handoff) | reraises | caller's | correct: cancels and gathers the owner, re-raises |
| 12 | `_reconcile_server_catalog` `manager.py:2193` | reraises | — | correct |
| 13 | `_send_request` `manager.py:3446` | reraises | — | correct |
| 14 | `_health_monitor_loop` `manager.py:4232` (`break`) | absorbs | its own (task root) | safe: the task *is* what was cancelled, and it ends |
| 15 | `cli.py:2508` `run_server` | absorbs | its own (top-level) | safe: logs, returns, process exits |
| 16 | `installer.py:264` `JobManager._handle_task_exception` | absorbs | none: a sync done-callback reading a finished task | safe, no await |
| 17 | `installer.py:454` `JobManager._monitor_install` | absorbs | its own (task root, `installer.py:566` cancels without awaiting) | safe: terminates the process, marks the job failed, ends |
| 18 | `installer.py:428` `_monitor_install` (inner) | reraises | — | correct |
| 19 | `tools/handlers.py:3409` `_run_update_probe_command` | reraises | — | correct: reaps the probe, re-raises |
| 20–22, + | `env_store.py:177`, `trust_store.py:370`, `package_approvals.py:287`, `manifest/registry.py:722` (`except BaseException`) | reraises | — | sync temp-file cleanup, then `raise` |

Rows 1–5 are the defect: each can swallow the caller's cancel. `gather(...,
return_exceptions=True)` sites (`manager.py:1130, 1207, 1923, 2763, 3614`) and
`asyncio.wait` sites (`cli.py:2489, 2500`, `installer.py:332`) do not suppress a
caller's cancel (the outer gather/wait raises it); they are listed here so no
one re-derives them.

### What the defect costs, measured on main

Probes in `tests/test_cancel_teardown.py` (verbatim below). Each suspension
point is reached through an `asyncio.Event` gate; the cancel is delivered while
the caller is parked there. On main, 3.10:

- **Rows 1–3, at a child wait:** the caller raises `RuntimeError('handshake
  failed')` instead of `CancelledError`: `AssertionError: caller cancelled at
  read_task lost its cancellation: raised RuntimeError('handshake failed')`
  (same for `stderr_task`, `outbound_writer`; stdio and remote).
- **The window is not one event-loop step.** The cancel is converted into the
  handshake error, and `_connect_with_retry` (`manager.py:1480`) catches
  `Exception`, so it runs the next attempt: `AssertionError: cancelled connect
  retried: raised RuntimeError('second attempt started'), attempts=3`. A
  cancelled lazy start (`ensure_connected` awaits its connect task, so a
  cancelled tool call cancels it) keeps retrying for the whole backoff.
- **At `_terminate_process_tree` / `_close_remote_transport`:** the cancel
  propagates, but mid-teardown. Measured: stdio leaves `_clients == ['srv']`
  with status `ERROR` and the terminate abandoned; remote leaves
  `_clients == ['remote']` **and the transport owner task still pending**.
- **Row 4 (`disconnect_server`):** a caller cancelled at the read-task or
  writer wait gets `(True, 0, None)` back; at terminate it gets
  `CancelledError` with the terminate abandoned.
- **Row 5 (`_cleanup_client`):** a caller cancelled at any child wait returns
  normally. Its callers are `_connect_stdio`/`_connect_remote_stream` (the
  reconnect pre-clean, `manager.py:2362, 2712`), so a cancelled reconnect goes
  on to **spawn a new process**.
- **Not in the issue: a cancel during the handshake itself.** The handshake
  `except` is `except Exception`, which a `CancelledError` does not enter. A
  caller cancelled inside `_send_initialize` leaves the process running, the
  read task alive and a `CONNECTING` entry in `_clients` (probe: `cancel-in-
  handshake ('cancelled', None) clients: ['srv'] terminated: [] read_task
  done: False`). The same holds for the remote path (transport left open) and
  for `adopt_process` (`manager.py:3817`), the third handshake path.

### Python versions

`asyncio.Task.cancelling()`/`uncancel()` exist only from 3.11. On 3.11+,
`asyncio.timeout()` decides whether a `CancelledError` is its own by comparing
`uncancel()` against the count it saw on entry, so a fix must not call
`uncancel()` or add cancels of its own. A swallowed cancel on 3.11+ also leaves
`cancelling()` at 1 forever, which poisons any later `asyncio.timeout()` in
the same task. The design below touches neither count.

## Design decisions (made explicitly)

### 1. One primitive: run the teardown in a private task and defer the caller's cancel

**Decision:** add `_finish_then_reraise_cancel(cleanup)` to `manager.py`. It
wraps the teardown coroutine in a task nobody else holds, and awaits it through
`asyncio.shield` in a loop:

- a `CancelledError` out of the shield is the caller's (the private task is
  unreferenced, so only loop shutdown could cancel it, and then the caller is
  being cancelled too): remember the first one, and keep waiting while the
  private task runs;
- when the private task is done, re-raise the remembered cancel exactly once;
  otherwise return the teardown's result or raise its error;
- if both a cancel and a teardown error happened, the cancel wins and the
  error is logged at WARNING through `describe_exception` (the
  `_close_remote_transport` precedent of "logged rather than silently
  dropped"), never discarded;
- the private task is named `pmcp-teardown:<qualname>` so the 60 s async
  watchdog (`tests/runtime/_hang_watchdog.py`) names the teardown a hang is
  parked in.

**Why not the alternatives:**
- *Re-raise at the await* (the obvious fix): skips terminate/close/pop; this is
  the constraint the issue states, and the terminate-point probe above shows
  the stale entry and the live owner it leaves.
- *Check `child.done()` after the shield raises:* wrong in the same-iteration
  race (Research summary). Measured as mutant M10: red on
  `test_helper_caller_cancel_in_the_completion_step_is_kept`.
- *`Task.cancelling()`:* 3.11+ only.
- *Reading the private `Task._must_cancel`:* a CPython implementation detail,
  and the C task does not expose it on every version.
- *Detach the teardown and raise immediately* (the caller does not linger):
  then `_clients` removal lands after the caller has seen `CancelledError`,
  and the issue and the brief both ask for "re-raise once cleanup completes".

### 2. The absorbing waits move into named teardown bodies that run only under the primitive

Each defective sequence becomes a method whose whole body runs inside the
private task, so the only cancellation its child waits can see is the child's:

| Sequence | New body | Called as |
|---|---|---|
| stdio handshake failure | `_abort_stdio_handshake(name, managed, process)` | `await _finish_then_reraise_cancel(self._abort_stdio_handshake(...))` |
| remote handshake failure | `_abort_remote_handshake(name, managed)` | same |
| `disconnect_server` from the read task to the status reset | `_finish_disconnect(name, managed, cancelled, *, caller)` | `return await _finish_then_reraise_cancel(...)` |
| `_cleanup_client` | `_cleanup_client_body(name, managed)` | `_cleanup_client` is now that one line |

The child waits become one helper, `_reap_cancelled_child(task, *,
timeout=None)`: cancel, await through `shield` (or `wait_for(shield(...),
timeout)`), absorb whatever it ends with. Its docstring states it is correct
only inside the primitive. `_teardown_outbound` keeps its contract (refs
dropped before the await, never raises for a non-cancellation failure) and
calls `_reap_cancelled_child(writer, timeout=timeout)`.

Two tests pin the shape so a later edit cannot quietly reintroduce the defect:
`test_every_cancellation_handler_is_classified` (every `CancelledError` /
`BaseException` handler in `src/pmcp`, with its count, must match a reasoned
allowlist) and `test_absorbing_teardown_only_runs_inside_the_private_task`
(`_reap_cancelled_child` and `_teardown_outbound` are called only from the
four bodies, and each body only as an argument of
`_finish_then_reraise_cancel`).

### 3. The handshake `except` also catches `CancelledError` (a cancel *during* the handshake gets the same teardown)

**Decision:** widen the three handshake handlers — `_connect_stdio`,
`_connect_remote_stream`, `adopt_process` — from `except Exception` to
`except (Exception, asyncio.CancelledError)`. Not `BaseException`:
`KeyboardInterrupt`/`SystemExit` are not delivered into these coroutines by
asyncio and should not run a teardown. The status becomes `ERROR` with
`last_error = "Connection cancelled"` (new helper `_handshake_error`;
`describe_exception(CancelledError())` returns `''`, measured), where today it
stays `CONNECTING` forever. The handler ends with `raise`, so the caller's
`CancelledError` propagates after the teardown. Measured leak on main, above;
falsifiers M5–M7.

### 4. The `_clients` removal moves into a `finally` in both abort bodies

**Decision:** `_abort_stdio_handshake` and `_abort_remote_handshake` pop the
stale entry in a `finally`, so a teardown step that raises
(`_terminate_process_tree`, or `_close_remote_transport`, which never swallows
a genuine failure by design) no longer leaves the `ERROR` entry behind. The
exception still propagates, as today. Measured on main:
`assert 'srv' not in {'srv': ManagedClient(...)}` with a raising terminate, and
the same for `remote` with a raising close. Falsifiers M14, M15.

### 5. `disconnect_server` passes its caller to the background-task sweep (load-bearing)

`_cancel_background_tasks` excludes `asyncio.current_task()` so a caller
scoped to the server name never cancels itself. Inside the private task,
`current_task()` is the private task, not the caller. **Without an explicit
exclusion this is a deadlock, not a lost cancel:** the private task cancels
the caller and then `gather`s it, while the caller waits on the private task.
Measured as mutant M11 on the real sweep: the test module hung until the
300 s subprocess timeout killed it (the 60 s watchdog would have fired in CI).
**Decision:** `disconnect_server` captures `asyncio.current_task()` and
`_finish_disconnect` passes `exclude={caller}`. The test for it
(`test_disconnect_server_does_not_cancel_its_own_caller`) tracks the caller as
a background task under the server name and wraps the sweep so the mutant
fails with an `AssertionError` instead of hanging the suite. (That test is
also red on main, by construction of its guard: main's sweep is called with
`exclude=None` and relies on `current_task()`. Its real falsifier is M11.)

The same cycle cannot form in the other bodies: none of them sweeps or awaits
a task that could be its caller. A *different* task sweeping a connect task
that is itself parked in `_finish_then_reraise_cancel` is not a cycle: that
private task waits for nothing outside itself, finishes its bounded teardown,
and the connect task then raises.

### 6. No new timeouts; the linger is the teardown's own bound

A cancelled caller now waits for its teardown instead of escaping it. That is
bounded by the teardown, not by anything new:

- child reaps: every reaped child is cancel-responsive. `_read_stdout`,
  `_read_stderr`, `_read_sse` and `_drain_outbound` have **no**
  `CancelledError`/`BaseException` handler (the AST scan; they are absent from
  the 22-row table), so each ends at its next await once cancelled.
  `test_every_cancellation_handler_is_classified` keeps that true: adding one
  fails the test until someone classifies it.
- `_terminate_process_tree`: ≤ 5 s SIGTERM wait + ≤ 3 s SIGKILL wait
  (`manager.py:312, 324`).
- `_close_remote_transport`: ≤ 5 s graceful, then cancel the owner and await
  it; unbounded only for an `__aexit__` that ignores cancellation, which the
  method already documents as pre-existing (`manager.py:2627-2635`) and which
  a cancelled caller hit on main as well (its caller-cancel branch also awaits
  the cancelled owner unbounded).
- `disconnect_server` holds `_lifecycle_lock` through the teardown, cancelled
  or not, as it does today; the lock is released when the caller re-raises
  (asserted: `assert not mgr._lifecycle_lock.locked()`).

Adding a bound to the child reaps would change the non-cancelled path, which
today waits unbounded for a cancel-responsive child, for no measured benefit.

### 7. `_cleanup_client`'s contract changes for cancellation only

Its docstring says "All exceptions are suppressed so callers always complete
successfully". After this plan, a non-cancellation failure is still
suppressed; a caller's cancellation now propagates *after* the full teardown
(today it was swallowed at a child wait, or propagated mid-teardown at
terminate/close). Rewrite the sentence to say so. Callers: the two reconnect
pre-cleans and `adopt_process`'s handshake handler; all are already prepared
for `CancelledError` (it propagates out of a connect today whenever the cancel
lands outside the child waits).

### 8. Not lost, not doubled, and `asyncio.timeout()` still works

The primitive never calls `cancel()` or `uncancel()`. Pinned by
`test_one_caller_cancel_is_delivered_exactly_once` (the caller catches one
`CancelledError`, then `await asyncio.sleep(0)` succeeds, so no second cancel
is queued; on 3.11+ `current_task().cancelling() == 1`) and, on 3.11+,
`test_a_timeout_scope_around_the_teardown_still_reports_timeout`
(`asyncio.timeout()` fired while the caller is parked in the terminate step
still surfaces as `TimeoutError`, and the terminate completes). Repeated
cancels during one teardown are delivered as one
(`test_helper_completes_cleanup_across_repeated_caller_cancels`).

## Corrections to the issue description

- "Window is one event-loop step today" understates it. The cancel is not
  delayed, it is **converted**: the caller raises the handshake error, and
  `_connect_with_retry` runs attempts 2 and 3 (measured `attempts=3`).
- The issue names three sites; the class has five (rows 1–5), and
  `_cleanup_client`'s lost cancel makes a cancelled reconnect spawn a new
  process.
- The issue's sites are only reachable for a handshake that *fails*. A cancel
  *during* the handshake (`except Exception` is never entered) leaks the
  process, the read task and a `CONNECTING` entry, on all three handshake
  paths. Design decision 3.
- At the terminate/close step the cancel does propagate, but the teardown is
  abandoned: stale `ERROR` entry, and on the remote path a live transport
  owner.

## Changes

Line numbers are main `89559db`. The spike diff is
`src/pmcp/client/manager.py | 316 +++++----` (216 insertions, 100 deletions;
about 60 of the deletions are the `disconnect_server` body dedented into
`_finish_disconnect`).

### `src/pmcp/client/manager.py` (modify)

- Imports: `from collections.abc import Awaitable, Callable, Collection`.
- After `_TaskT` (`manager.py:145`): add `_T = TypeVar("_T")`,
  `_finish_then_reraise_cancel` (Design decision 1), `_reap_cancelled_child`
  (Design decision 2), `_handshake_error` (Design decision 3).
- `disconnect_server` (`manager.py:1349`): inside the `async with
  self._lifecycle_lock:` block, after the `if not managed:` early return,
  replace the body from `config = managed.config` to `return (True, cancelled,
  None)` with `return await _finish_then_reraise_cancel(self._finish_disconnect(
  name, managed, cancelled, caller=asyncio.current_task()))`.
- New `_finish_disconnect` (after `disconnect_server`): the moved body,
  unchanged except: the read-task wait (`manager.py:1397-1406`) becomes
  `await _reap_cancelled_child(managed.read_task, timeout=1.0)` (same 1 s
  bound), and `await self._cancel_background_tasks(server_name=name)` becomes
  `await self._cancel_background_tasks(server_name=name, exclude={caller} if
  caller is not None else None)` (Design decision 5).
- `_connect_stdio` handshake handler (`manager.py:2461-2480`): `except
  (Exception, asyncio.CancelledError) as e:`; `status.last_error =
  _handshake_error(e)`; `await _finish_then_reraise_cancel(
  self._abort_stdio_handshake(name, managed, process))`; `raise`.
- New `_abort_stdio_handshake`: reap `read_task`, reap `stderr_task`,
  `_teardown_outbound`, `_terminate_process_tree`, with the `_clients` pop in a
  `finally` (Design decision 4).
- `_connect_remote_stream` handshake handler (`manager.py:2789-2805`): same
  shape, calling new `_abort_remote_handshake` (reap `read_task`,
  `_teardown_outbound`, `_close_remote_transport`, pop in `finally`).
- `_teardown_outbound` (`manager.py:3258-3270`): after dropping the refs,
  `await _reap_cancelled_child(writer, timeout=timeout)`, with a comment that
  every caller runs it under the primitive. The early return for a missing or
  finished writer moves into the helper.
- `_cleanup_client` (`manager.py:3652`): the body becomes `await
  _finish_then_reraise_cancel(self._cleanup_client_body(name, managed))`; new
  `_cleanup_client_body` holds the old body with the child loop as
  `for task in (...): await _reap_cancelled_child(task)`. Update the
  docstring's "callers always complete successfully" sentence (Design
  decision 7).
- `adopt_process` handshake handler (`manager.py:3817`): `except (Exception,
  asyncio.CancelledError) as e:` and `_handshake_error(e)`. Its teardown is
  `_cleanup_client`, already scoped.

Unchanged on purpose: `_shutdown_one` (row 6), `_close_remote_transport`
(rows 7–9), `_own_remote_transport` (row 10), the task roots (rows 14–17).

### `tests/test_cancel_teardown.py` (create)

The module in *Verbatim bodies*: 32 tests (31 on 3.10, where the
`asyncio.timeout` test skips).

| Test | Pins |
|---|---|
| `test_stdio_handshake_teardown_keeps_caller_cancel[read_task, stderr_task, outbound_writer, terminate]` | row 1 + 3, every await of the stdio teardown |
| `test_remote_handshake_teardown_keeps_caller_cancel[read_task, outbound_writer, close_transport]` | row 2 + 3, every await of the remote teardown; owner task done |
| `test_a_lost_cancel_does_not_start_another_connect_attempt` | the retry consequence |
| `test_cleanup_client_keeps_caller_cancel[read_task, stderr_task, outbound_writer, terminate]` | row 5 |
| `test_disconnect_server_keeps_caller_cancel[read_task, outbound_writer, terminate]` | row 4 (+ status `LAZY`, lock released) |
| `test_disconnect_server_does_not_cancel_its_own_caller` | Design decision 5 |
| `test_child_cancellation_is_still_absorbed_without_a_caller_cancel`, `..._on_disconnect` | the child's cancel is still absorbed |
| `test_one_caller_cancel_is_delivered_exactly_once` | not lost, not doubled |
| `test_helper_completes_cleanup_across_repeated_caller_cancels`, `test_helper_returns_the_cleanup_result_and_raises_its_error`, `test_helper_cancel_wins_over_a_cleanup_error` (asserts the log), `test_helper_caller_cancel_in_the_completion_step_is_kept` | the primitive |
| `test_a_failing_terminate_still_drops_the_stale_client`, `test_a_failing_transport_close_still_drops_the_stale_client` | Design decision 4 |
| `test_stdio_cancel_during_handshake_still_tears_down`, `test_remote_...`, `test_adopt_...` | Design decision 3 |
| `test_disconnect_all_redelivers_a_cancel_absorbed_by_shutdown_one` | row 6 stays safe |
| `test_a_timeout_scope_around_the_teardown_still_reports_timeout` (3.11+) | Design decision 8 |
| `test_every_cancellation_handler_is_classified`, `test_absorbing_teardown_only_runs_inside_the_private_task` | the class guard |

No timed sleeps. `await asyncio.sleep(0)` is used only as a single yield, and
`asyncio.wait_for(..., 10.0)` only as a hang guard (`tests/_timing.py`'s rule).
The cancel is ordered against the gate's release by the FIFO ready queue
(`_cancel_while_parked`'s docstring), not by a yield count.

## Documentation impact

- `CHANGELOG.md`: one bullet under `## [Unreleased]` → `### Fixed`, phrased
  "see Consiliency/pmcp#324" (no closing keyword): a caller cancelled while a
  server connection is being torn down (a failed or cancelled handshake,
  `disconnect_server`, a reconnect's cleanup) now keeps its cancellation and
  still gets the whole teardown; before, the cancellation could be lost (the
  connect was retried) or the teardown abandoned (a stale entry, a running
  process or an open remote transport).
- `SECURITY.md`: no change. No ledger claim covers cancellation;
  `scripts/check_security_claims.py` reports `OK … 129 cited node id(s)` on
  the spike.
- `_cleanup_client`'s docstring (Design decision 7).

## Dependencies & order

1. Add the primitive and `_reap_cancelled_child`.
2. Move the bodies (handshake ×2, disconnect, cleanup), then widen the three
   handshake handlers.
3. Add the test module; update the allowlist only if line-level code moved.
4. CHANGELOG last.

**Touch points with open work.** Plan PR Consiliency/pmcp#329 (for
Consiliency/pmcp#298) changes `manager.py` around `_send_request`'s write
path and task-hint parsing; its patch adds no `CancelledError`/`BaseException`
handler (checked: `grep '^+.*except.*(CancelledError|BaseException)'` is
empty for it and for plan PRs Consiliency/pmcp#314, #295, #331), so there is no
semantic overlap and only a possible textual rebase. The class guard
`test_every_cancellation_handler_is_classified` is deliberately src-wide: any
later PR that adds a cancellation handler anywhere in `src/pmcp` fails it until
the handler is classified in the allowlist with a reason. That is the point of
the test, and the failure message names the handler.

## Verification

Run from a fresh worktree of `origin/main` on dev0 (a team host):

```bash
git -C ~/code/pmcp worktree add -b fix/324-cancel-teardown "$WORKTREE_ROOT/pmcp-324-fix" origin/main
cd "$WORKTREE_ROOT/pmcp-324-fix"
uv sync --all-extras -p 3.10      # without --all-extras, `uv run` silently uses the system pytest
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir
```

Apply *Verbatim bodies* (`git apply` the patch, write the test module, edit
the `_cleanup_client` docstring sentence and CHANGELOG by hand), then:

```bash
# 1. the new module (spike: 31 passed, 1 skipped on 3.10; 32 passed on 3.11 and 3.12; ~1.2 s)
uv run pytest tests/test_cancel_teardown.py --cov-fail-under=0 -p no:cacheprovider -q
# 2. the same module, plus the client-manager suites, on all three Pythons
#    (spike: 305 passed, 1 skipped on 3.10; 306 passed on 3.11; 306 passed on 3.12)
uv python install 3.11 3.12
for v in 3.11 3.12; do UV_PROJECT_ENVIRONMENT=.venv-$v uv sync --all-extras -p $v -q; done
for v in 3.10 3.11 3.12; do E=.venv; [ $v != 3.10 ] && E=.venv-$v
  UV_PROJECT_ENVIRONMENT=$E uv run --no-sync -p $v pytest tests/test_cancel_teardown.py \
    tests/test_client_manager.py tests/test_client_manager_reconnect.py \
    --cov-fail-under=0 -p no:cacheprovider -q; done
# 3. every module that exercises a changed function (spike, before the last two
#    helper refinements: 1193 passed, 3 skipped, 0 failed, 346 s; slowest item
#    60.06 s is the pre-existing tests/test_progressive_disclosure.py network test)
uv run pytest tests/mcp2x/test_catalog_publishers.py tests/mcp2x/test_client_transport.py \
  tests/runtime/test_downstream_handshake_era.py tests/runtime/test_downstream_remote.py \
  tests/runtime/test_downstream_stdio.py tests/runtime/test_emitter_harness.py \
  tests/runtime/test_hang_diagnostics.py tests/runtime/test_publisher_coverage.py \
  tests/runtime/test_subscriptions_e2e.py tests/test_baseline_constraints.py \
  tests/test_cancel_teardown.py tests/test_client_manager.py tests/test_client_manager_reconnect.py \
  tests/test_cli_p4.py tests/test_cli.py tests/test_exception_group_diagnostics.py \
  tests/test_fresh_operator_baseline.py tests/test_gateway_tool_schemas.py tests/test_integration.py \
  tests/test_package_identity_gate.py tests/test_pkgid_panel_fixes.py tests/test_pkgid_spawn_logging.py \
  tests/test_progressive_disclosure.py tests/test_provision_validation.py tests/test_server_lifecycle.py \
  tests/test_server.py tests/test_startup_resolver.py tests/test_tools.py \
  tests/test_trust_boundaries_composition.py tests/test_trust_boundaries_e2e.py \
  --cov-fail-under=0 -p no:cacheprovider -q --durations=5
# 4. CI gates the plan's own list would otherwise miss (spike: all clean)
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src/pmcp/client/manager.py
python3 scripts/check_security_claims.py          # expect OK, 129 cited node ids
python3 scripts/check_plan_consistency.py .consiliency/plans/detailed-324-cancel-teardown-20261003-0213.md
#   measured on this file: "consistent … blocking inconsistencies: 0", exit 0 (a detailed plan has no roadmap pin)
# 5. the full suite: once, detached, with a notifying waiter (memory on dev0 is shared)
nohup uv run pytest -q -p no:cacheprovider > "$WORKTREE_ROOT/pmcp-324-full.log" 2>&1 &
```

**Hang guard.** No test in the new module comes near the diagnostics: the
whole module runs in ~1.2 s on every version, every await in it is behind an
`Event` gate or a 10 s `wait_for` hang guard, and none of the 60 s watchdog,
700 s pytest-timeout or 720 s faulthandler thresholds is approached. The one
way this change could hang (Design decision 5, mutant M11) is a deadlock the
dedicated test turns into an assertion.

**Red on main.** The final module against `89559db`'s `manager.py`:
**3.10: 28 failed, 3 passed, 1 skipped; 3.11: 29 failed, 3 passed; 3.12: 29
failed, 3 passed.** The 3 that pass on main are the ones that must: the two
"child cancellation is still absorbed" tests and the `_shutdown_one`
gather-redelivery test. First assertion on main per group:

| Test (group) | First assertion on main |
|---|---|
| stdio/remote `..._keeps_caller_cancel[<child>]` | `caller cancelled at read_task lost its cancellation: raised RuntimeError('handshake failed')` |
| stdio `[terminate]`, remote `[close_transport]` | `teardown abandoned the terminate wait` / `... close_transport wait` |
| `test_a_lost_cancel_does_not_start_another_connect_attempt` | `cancelled connect retried: raised RuntimeError('second attempt started'), attempts=3` |
| `test_cleanup_client_keeps_caller_cancel[<child>]` | `... lost its cancellation: returned None` |
| `test_disconnect_server_keeps_caller_cancel[<child>]` | `... lost its cancellation: returned (True, 0, None)` |
| `..._cancel_during_handshake_still_tears_down` (stdio/remote/adopt) | `process left running after a cancelled handshake` / `transport left open ...` / `assert [] == ['terminated']` |
| `test_a_failing_terminate/transport_close_still_drops_the_stale_client` | `assert 'srv' not in {'srv': ManagedClient(...)}` |
| `test_one_caller_cancel_is_delivered_exactly_once` | `('raised', RuntimeError('handshake failed')) == ('returned', 'caught once')` |
| `test_a_timeout_scope_around_the_teardown_still_reports_timeout` (3.11+) | `assert [] == ['terminated']` (timeout reported, terminate abandoned) |
| the 4 helper tests | `_finish_then_reraise_cancel is missing` |
| `test_every_cancellation_handler_is_classified` | the six defective handlers listed as unclassified |
| `test_disconnect_server_does_not_cancel_its_own_caller` | `the sweep would cancel and await its own caller` (guard artifact; see Design decision 5) |

## Acceptance criteria

- [ ] A caller cancelled at **every** await of the stdio, remote,
  `disconnect_server` and `_cleanup_client` teardowns sees `CancelledError`,
  the teardown completes (process tree terminated / transport closed, owner
  task done) and the server is gone from `_clients`. Proven by the 14
  parametrized `..._keeps_caller_cancel[...]` cases.
- [ ] A cancelled connect is not retried. Proven by
  `test_a_lost_cancel_does_not_start_another_connect_attempt`.
- [ ] A cancel during the handshake itself tears down on all three handshake
  paths, and `last_error` reads `Connection cancelled`. Proven by the three
  `..._cancel_during_handshake_still_tears_down` tests.
- [ ] A child's own cancellation is still absorbed: a failed handshake still
  raises its own error, a disconnect still returns `(True, 0, None)`. Proven
  by the two `test_child_cancellation_is_still_absorbed_...` tests.
- [ ] One cancel in, one `CancelledError` out; the 3.11+ cancel count is 1;
  `asyncio.timeout()` still reports `TimeoutError`. Proven by
  `test_one_caller_cancel_is_delivered_exactly_once` and
  `test_a_timeout_scope_around_the_teardown_still_reports_timeout`.
- [ ] The primitive keeps a cancel delivered in the teardown's completion
  step, survives repeated cancels, and logs a teardown error that a cancel
  displaces. Proven by the four `test_helper_...` tests.
- [ ] `disconnect_server` never sweeps its own caller. Proven by
  `test_disconnect_server_does_not_cancel_its_own_caller` (M11).
- [ ] Every `CancelledError`/`BaseException` handler in `src/pmcp` is
  classified, and the absorbing primitives run only inside the private task.
  Proven by the two class-guard tests.
- [ ] Verification steps 1–5 pass on 3.10, and step 2 on 3.11 and 3.12.
- [ ] Every mutant below is red, for the reason stated.

## Mutation table

Each mutant was measured on the spiked tree (`mutants.py`: apply one string
replacement to `manager.py`, run `tests/test_cancel_teardown.py` on 3.10 with
`-o timeout=60` and a 300 s subprocess cap, restore the file from a saved copy
in a `finally`). All 15 are red. After the run, `diff manager.py
manager.spike.py` was empty.

| # | Rule | Mutant | Red tests (measured) |
|---|---|---|---|
| M1 | stdio teardown is scoped | `await self._abort_stdio_handshake(...)` directly | 7: the 4 stdio `[point]` cases, the retry test, the exactly-once test, the structural test |
| M2 | remote teardown is scoped | `await self._abort_remote_handshake(...)` directly | 4: the 3 remote `[point]` cases, the structural test |
| M3 | disconnect teardown is scoped | `return await self._finish_disconnect(...)` directly | 4: the 3 disconnect `[point]` cases, the structural test |
| M4 | `_cleanup_client` is scoped | `await self._cleanup_client_body(...)` directly | 5: the 4 cleanup `[point]` cases, the structural test |
| M5 | cancel during stdio handshake | handler back to `except Exception` | `test_stdio_cancel_during_handshake_still_tears_down`, the classification test |
| M6 | cancel during remote handshake | same, remote | `test_remote_cancel_during_handshake_still_tears_down`, the classification test |
| M7 | cancel during adopt handshake | same, `adopt_process` | `test_adopt_cancel_during_handshake_still_tears_down`, the classification test |
| M8 | primitive re-raises | `raise caller_cancel` → `return task.result()` | 19: every `[point]` case, retry, exactly-once, 3 helper tests |
| M9 | primitive keeps waiting | `while not task.done()` → `if not task.done()` (one shield await) | 18: every `[point]` case, retry, exactly-once, 2 helper tests |
| M10 | completion-step race | remember the cancel only `if not task.done()` | `test_helper_caller_cancel_in_the_completion_step_is_kept` **only**. This is the `task.done()` discriminator the Research summary rejects |
| M11 | sweep excludes the caller | drop `exclude=` in `_finish_disconnect` | `test_disconnect_server_does_not_cancel_its_own_caller`. Against the real sweep (no guard) this mutant **deadlocks**; the guard is why the test fails instead of hanging |
| M12 | displaced error is logged | `logger.warning(...)` → `pass` | `test_helper_cancel_wins_over_a_cleanup_error` |
| M13 | reap only inside the private task | `_shutdown_one`'s read wait → `await _reap_cancelled_child(...)` | the classification test, the structural test |
| M14 | stdio pop in `finally` | `finally:` → `except BaseException: raise` / `else:` | `test_a_failing_terminate_still_drops_the_stale_client`, the classification test |
| M15 | remote pop in `finally` | same, remote | `test_a_failing_transport_close_still_drops_the_stale_client`, the classification test |

The implementer re-runs the same 15 on the final tree, restoring from a saved
copy (never `git checkout --`), and confirms `git diff --stat` matches the
pre-mutation state. Mutants were measured on 3.10 only; the module itself is
green on 3.11 and 3.12 and red on main on all three.

## Non-goals

- **`disconnect_all`'s bookkeeping after a cancel.** A cancel absorbed in
  `_shutdown_one` is re-raised by the `gather` (measured, row 6), and the
  dict clears after the gather are then skipped. Shielding `disconnect_all`
  the same way would defeat the shutdown budget: `server.py:1017` wraps it in
  `asyncio.wait_for(..., timeout=10.0)`, and `wait_for` waits out an inner
  that defers its cancel. At shutdown the process exits anyway. If a
  non-shutdown caller of a cancelled `disconnect_all` ever matters, that is
  its own issue.
- **`_own_remote_transport` forwarding its own pre-handoff cancel into
  `ready`** (row 10). A sweep that cancels only the owner hands the connect
  caller a `CancelledError` it did not receive. In practice the same sweep
  cancels the connect task too, and nothing here is absorbed. Not this class.
- **Task roots** (rows 14–17): they absorb their own cancel and end; no caller
  runs on.
- **Bounding the child reaps.** Design decision 6.
- **Propagate-early sites outside the five** (`handlers.py:3409`'s
  `_terminate_process_tree` after a cancel can itself be cancelled again):
  they propagate rather than absorb, and the probe they would need is a
  second cancel during process reaping. Not observed; not in scope.

## Unverified

- **A real downstream process.** All probes use fakes for the process and the
  transport; `_terminate_process_tree`'s 5 s + 3 s bound is read from the
  code, not measured under a cancelled caller.
- **The full suite on the final spike.** Step 3's 30-module run (1193 passed)
  was before the last two helper refinements (logging the displaced error,
  naming the task). After them, the new module plus the two client-manager
  suites were re-run on 3.10/3.11/3.12 (305/306/306 passed). Verification
  step 5 runs the full suite once.
- **Mutants on 3.11/3.12.** Measured on 3.10 only.

## Execution Policy

- execute: effort=medium.
- reason: one file of source (`manager.py`, about 216/100 lines, mostly moved
  code), but on the connection lifecycle that every server uses, with a
  deadlock available to a one-line mistake (M11). Small surface, sharp edges.
- Re-run the mutation table, verification steps 1–4 on all three Pythons, ruff
  and mypy before requesting review.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the source patch below (between the ```` fences) to `324-src.patch`,
   then run `git apply 324-src.patch` on `89559db`.
2. Write the test module below to `tests/test_cancel_teardown.py`.
3. Edit `_cleanup_client`'s docstring sentence (Design decision 7) and add the
   `CHANGELOG.md` bullet by hand.

### Patch — `src/pmcp/client/manager.py`

````diff
diff --git a/src/pmcp/client/manager.py b/src/pmcp/client/manager.py
index 57a563e..f15acab 100644
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -15,7 +15,7 @@ import traceback
 import string
 import time
 from collections import deque
-from collections.abc import Callable, Collection
+from collections.abc import Awaitable, Callable, Collection
 from dataclasses import dataclass, field
 from types import ModuleType
 from typing import Any, Iterator, TypeVar
@@ -143,6 +143,95 @@ def describe_exception(exc: BaseException) -> str:
 
 
 _TaskT = TypeVar("_TaskT", bound=asyncio.Task[Any])
+_T = TypeVar("_T")
+
+
+async def _finish_then_reraise_cancel(cleanup: Awaitable[_T]) -> _T:
+    """Run ``cleanup`` to completion even if the calling task is cancelled
+    while it runs, then re-raise that cancellation (Consiliency/pmcp#324).
+
+    Every teardown below cancels child tasks it owns and awaits them,
+    absorbing the child's expected ``CancelledError``. Awaited in the
+    caller's own task, that ``except`` cannot tell the child's cancellation
+    from the caller's, so a cancelled caller ran on. Re-raising at the await
+    instead would skip the process-tree termination, transport close and
+    ``_clients`` removal that follow. So the teardown runs in a private task
+    that nobody else holds a reference to, and the caller waits on it
+    through a shield:
+
+    * a ``CancelledError`` out of the shield while the private task is still
+      running can only be the caller's -- remember it and keep waiting;
+    * one that arrives in the same loop iteration the private task finishes
+      is also the caller's (the private task was not cancelled), which is
+      the race a ``task.done()`` check alone gets wrong;
+    * the remembered cancellation is re-raised exactly once, after the
+      teardown, and wins over a teardown error.
+
+    Python 3.10 has no ``Task.cancelling()``/``uncancel()``, so this does
+    not count cancellations; it never calls ``uncancel()`` either, so on
+    3.11+ the caller's cancel count is left exactly as the canceller set it
+    and ``asyncio.timeout()`` still converts its own cancel to
+    ``TimeoutError``.
+    """
+    task = asyncio.ensure_future(cleanup)
+    # Named so the 60 s async watchdog (tests/runtime/_hang_watchdog.py) can
+    # say which teardown a hang is parked in.
+    task.set_name(f"pmcp-teardown:{getattr(cleanup, '__qualname__', 'cleanup')}")
+    caller_cancel: asyncio.CancelledError | None = None
+    while not task.done():
+        try:
+            await asyncio.shield(task)
+        except asyncio.CancelledError as exc:
+            # Deferred, not absorbed: re-raised below once `task` is done.
+            if caller_cancel is None:
+                caller_cancel = exc
+        except Exception:
+            pass  # the teardown's own failure; read from `task` below
+    if caller_cancel is not None:
+        displaced = None if task.cancelled() else task.exception()
+        if displaced is not None:
+            # The caller's cancellation wins, but the teardown failure it
+            # displaces is logged rather than dropped (the
+            # `_close_remote_transport` precedent).
+            logger.warning(
+                f"teardown error displaced by the caller's cancellation: "
+                f"{describe_exception(displaced)}"
+            )
+        raise caller_cancel
+    return task.result()
+
+
+async def _reap_cancelled_child(
+    task: asyncio.Task[Any] | None, *, timeout: float | None = None
+) -> None:
+    """Cancel a child task the current teardown owns and wait for it,
+    absorbing however it ends.
+
+    Absorbing ``CancelledError`` here is correct ONLY inside
+    ``_finish_then_reraise_cancel``: there the awaiting task is private, so
+    the only cancellation that can arrive is the child's own. Called from a
+    caller's task directly, it would swallow the caller's cancellation --
+    the defect Consiliency/pmcp#324 fixed.
+    """
+    if task is None or task.done():
+        return
+    task.cancel()
+    try:
+        if timeout is None:
+            await asyncio.shield(task)
+        else:
+            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
+    except (asyncio.CancelledError, Exception):
+        pass
+
+
+def _handshake_error(exc: BaseException) -> str:
+    """`last_error` for a failed handshake. A cancellation renders as an
+    empty string through `describe_exception`, so name it."""
+    if isinstance(exc, asyncio.CancelledError):
+        return "Connection cancelled"
+    return describe_exception(exc)
+
 
 # The three catalog kinds, in the order reconciliation fetches and applies them.
 # Iterating this rather than three hand-written branches is what keeps
@@ -1390,71 +1479,85 @@ class ClientManager:
                     status.pending_request_count = 0
                 return (True, cancelled, None)
 
-            config = managed.config
-            managed.status.status = ServerStatusEnum.OFFLINE
-            managed.status.pending_request_count = 0
+            # Under `_finish_then_reraise_cancel`: a caller cancelled at any
+            # await below still gets the whole disconnect, then its
+            # cancellation (Consiliency/pmcp#324). The caller is passed in so
+            # the background-task sweep still excludes it -- inside the
+            # private task `current_task()` is no longer the caller.
+            return await _finish_then_reraise_cancel(
+                self._finish_disconnect(
+                    name, managed, cancelled, caller=asyncio.current_task()
+                )
+            )
 
-            if managed.read_task and not managed.read_task.done():
-                managed.read_task.cancel()
-                try:
-                    await asyncio.wait_for(
-                        asyncio.shield(managed.read_task), timeout=1.0
-                    )
-                except (asyncio.TimeoutError, asyncio.CancelledError):
-                    pass
-                except Exception:
-                    pass
-
-            # Cancel the outbound writer explicitly (in addition to the
-            # server-name sweep below), so teardown of this path does not
-            # depend on that sweep also matching it -- and reset
-            # `outbound`/`outbound_writer`, the same postcondition
-            # `_cleanup_client` holds (Consiliency/pmcp#287).
-            await self._teardown_outbound(managed, timeout=1.0)
+    async def _finish_disconnect(
+        self,
+        name: str,
+        managed: ManagedClient,
+        cancelled: int,
+        *,
+        caller: asyncio.Task[Any] | None,
+    ) -> tuple[bool, int, str | None]:
+        """`disconnect_server`'s teardown, from the read task to the status
+        reset. Runs only under `_finish_then_reraise_cancel`."""
+        config = managed.config
+        managed.status.status = ServerStatusEnum.OFFLINE
+        managed.status.pending_request_count = 0
 
-            try:
-                if managed.is_remote:
-                    # _close_remote_transport never swallows a genuine
-                    # transport-exit failure -- that's deliberate, so it can
-                    # still reach the `except Exception` below and return
-                    # (False, cancelled, str(e)) rather than reporting a
-                    # broken teardown as a successful disconnect. A timeout
-                    # that escalates to cancel is logged there and returns
-                    # normally: the transport is closed either way, and
-                    # `False` here would make a dead-peer disconnect look
-                    # like a refusal to the caller.
-                    await self._close_remote_transport(name, managed)
-                else:
-                    await _terminate_process_tree(managed.process, name)
-            except Exception as e:
-                described = describe_exception(e)
-                logger.warning(f"Error disconnecting from {name}: {described}")
-                return (False, cancelled, described)
+        await _reap_cancelled_child(managed.read_task, timeout=1.0)
 
-            await self._cancel_background_tasks(server_name=name)
-            self._connect_tasks.pop(name, None)
-            self._reconnect_tasks.pop(name, None)
-            # A reconcile cancelled before it ever started never runs its own
-            # `finally`, so clear its bookkeeping here too. The catalog removal
-            # below supersedes whatever it would have published.
-            self._reconcile_tasks.pop(name, None)
-            self._reconcile_reruns.discard(name)
-            self._catalog_suppressed.pop(name, None)
-            self._clients.pop(name, None)
-            self._remove_server_indexes(name)
-            if config is not None and config.source in {"project", "user", "custom"}:
-                self._lazy_configs[name] = config
-            self._servers[name] = ServerStatus(
-                name=name,
-                status=ServerStatusEnum.LAZY
-                if name in self._lazy_configs
-                else ServerStatusEnum.OFFLINE,
-                tool_count=0,
-            )
-            self._revision_id = _generate_revision_id()
-            self._last_refresh_ts = time.time()
-            # No flush() here, deliberately -- see _index_capabilities.
-            return (True, cancelled, None)
+        # Cancel the outbound writer explicitly (in addition to the
+        # server-name sweep below), so teardown of this path does not
+        # depend on that sweep also matching it -- and reset
+        # `outbound`/`outbound_writer`, the same postcondition
+        # `_cleanup_client` holds (Consiliency/pmcp#287).
+        await self._teardown_outbound(managed, timeout=1.0)
+
+        try:
+            if managed.is_remote:
+                # _close_remote_transport never swallows a genuine
+                # transport-exit failure -- that's deliberate, so it can
+                # still reach the `except Exception` below and return
+                # (False, cancelled, str(e)) rather than reporting a
+                # broken teardown as a successful disconnect. A timeout
+                # that escalates to cancel is logged there and returns
+                # normally: the transport is closed either way, and
+                # `False` here would make a dead-peer disconnect look
+                # like a refusal to the caller.
+                await self._close_remote_transport(name, managed)
+            else:
+                await _terminate_process_tree(managed.process, name)
+        except Exception as e:
+            described = describe_exception(e)
+            logger.warning(f"Error disconnecting from {name}: {described}")
+            return (False, cancelled, described)
+
+        await self._cancel_background_tasks(
+            server_name=name, exclude={caller} if caller is not None else None
+        )
+        self._connect_tasks.pop(name, None)
+        self._reconnect_tasks.pop(name, None)
+        # A reconcile cancelled before it ever started never runs its own
+        # `finally`, so clear its bookkeeping here too. The catalog removal
+        # below supersedes whatever it would have published.
+        self._reconcile_tasks.pop(name, None)
+        self._reconcile_reruns.discard(name)
+        self._catalog_suppressed.pop(name, None)
+        self._clients.pop(name, None)
+        self._remove_server_indexes(name)
+        if config is not None and config.source in {"project", "user", "custom"}:
+            self._lazy_configs[name] = config
+        self._servers[name] = ServerStatus(
+            name=name,
+            status=ServerStatusEnum.LAZY
+            if name in self._lazy_configs
+            else ServerStatusEnum.OFFLINE,
+            tool_count=0,
+        )
+        self._revision_id = _generate_revision_id()
+        self._last_refresh_ts = time.time()
+        # No flush() here, deliberately -- see _index_capabilities.
+        return (True, cancelled, None)
 
     async def restart_server(
         self, config: ResolvedServerConfig, force: bool = False
@@ -2458,26 +2561,38 @@ class ClientManager:
                 f"{resource_count} resources, {prompt_count} prompts indexed"
             )
 
-        except Exception as e:
+        except (Exception, asyncio.CancelledError) as e:
+            # CancelledError too: a caller cancelled DURING the handshake
+            # must not leave the process, its reader tasks and a CONNECTING
+            # `_clients` entry behind (Consiliency/pmcp#324).
             status.status = ServerStatusEnum.ERROR
-            status.last_error = describe_exception(e)
-            for task in (managed.read_task, managed.stderr_task):
-                if task and not task.done():
-                    task.cancel()
-                    try:
-                        await asyncio.shield(task)
-                    except (asyncio.CancelledError, Exception):
-                        pass
+            status.last_error = _handshake_error(e)
+            await _finish_then_reraise_cancel(
+                self._abort_stdio_handshake(name, managed, process)
+            )
+            raise
+
+    async def _abort_stdio_handshake(
+        self,
+        name: str,
+        managed: ManagedClient,
+        process: asyncio.subprocess.Process,
+    ) -> None:
+        """Everything a failed or cancelled stdio handshake owes, in order.
+        Runs only under `_finish_then_reraise_cancel` (Consiliency/pmcp#324)."""
+        try:
+            await _reap_cancelled_child(managed.read_task)
+            await _reap_cancelled_child(managed.stderr_task)
             # A writer started before the handshake failed (e.g. a `ping`
             # answered during `initialize`) must not survive this pop
             # (Consiliency/pmcp#287).
             await self._teardown_outbound(managed)
             await _terminate_process_tree(process, name)
+        finally:
             # Drop the stale ERROR client so it can't be found as a live
             # connection on the next connect attempt (issue: stale entry + leak).
             if self._clients.get(name) is managed:
                 self._clients.pop(name, None)
-            raise
 
     async def _connect_sse(self, config: ResolvedServerConfig) -> None:
         """Connect to a remote SSE MCP server."""
@@ -2786,23 +2901,28 @@ class ClientManager:
                 f"{resource_count} resources, {prompt_count} prompts indexed"
             )
 
-        except Exception as e:
+        except (Exception, asyncio.CancelledError) as e:
+            # CancelledError too, as in `_connect_stdio` (Consiliency/pmcp#324).
             status.status = ServerStatusEnum.ERROR
-            status.last_error = describe_exception(e)
-            if managed.read_task and not managed.read_task.done():
-                managed.read_task.cancel()
-                try:
-                    await asyncio.shield(managed.read_task)
-                except (asyncio.CancelledError, Exception):
-                    pass
+            status.last_error = _handshake_error(e)
+            await _finish_then_reraise_cancel(
+                self._abort_remote_handshake(name, managed)
+            )
+            raise
+
+    async def _abort_remote_handshake(self, name: str, managed: ManagedClient) -> None:
+        """Everything a failed or cancelled remote handshake owes, in order.
+        Runs only under `_finish_then_reraise_cancel` (Consiliency/pmcp#324)."""
+        try:
+            await _reap_cancelled_child(managed.read_task)
             # Same as the stdio handshake path (Consiliency/pmcp#287).
             await self._teardown_outbound(managed)
             await self._close_remote_transport(name, managed)
+        finally:
             # Drop the stale ERROR client so it can't be found as a live
             # connection on the next connect attempt.
             if self._clients.get(name) is managed:
                 self._clients.pop(name, None)
-            raise
 
     async def _read_stderr(self, name: str, stderr: asyncio.StreamReader) -> None:
         """Read stderr from a server process."""
@@ -3258,16 +3378,10 @@ class ClientManager:
         writer = managed.outbound_writer
         managed.outbound = None
         managed.outbound_writer = None
-        if writer is None or writer.done():
-            return
-        writer.cancel()
-        try:
-            if timeout is None:
-                await asyncio.shield(writer)
-            else:
-                await asyncio.wait_for(asyncio.shield(writer), timeout=timeout)
-        except (asyncio.CancelledError, Exception):
-            pass
+        # Every caller runs this under `_finish_then_reraise_cancel`, which
+        # is what makes absorbing the writer's cancellation safe
+        # (Consiliency/pmcp#324).
+        await _reap_cancelled_child(writer, timeout=timeout)
 
     async def _drain_outbound(self, managed: ManagedClient) -> None:
         """The one writer task per client: drain the bounded outbound queue.
@@ -3665,13 +3779,14 @@ class ClientManager:
         # `while True` writer is not a background-task sweep target on this path
         # (`_cleanup_client` deliberately does NOT call `_cancel_background_tasks`),
         # so without this it leaked one writer task per reconnect generation.
+        await _finish_then_reraise_cancel(self._cleanup_client_body(name, managed))
+
+    async def _cleanup_client_body(self, name: str, managed: ManagedClient) -> None:
+        """`_cleanup_client`'s teardown. Runs only under
+        `_finish_then_reraise_cancel`, so a caller cancelled at any await
+        still gets the whole teardown, then its cancellation (Consiliency/pmcp#324)."""
         for task in (managed.read_task, managed.stderr_task, managed.outbound_writer):
-            if task and not task.done():
-                task.cancel()
-                try:
-                    await asyncio.shield(task)
-                except (asyncio.CancelledError, Exception):
-                    pass
+            await _reap_cancelled_child(task)
         # Reset the outbound path so nothing survives onto a next generation.
         # The writer was cancelled above, but the Queue -- and any reply /
         # notifications/cancelled frames the dead connection left buffered,
@@ -3814,9 +3929,10 @@ class ClientManager:
 
             logger.info(f"Adopted {name}: {indexed} tools indexed")
 
-        except Exception as e:
+        except (Exception, asyncio.CancelledError) as e:
+            # CancelledError too, as in `_connect_stdio` (Consiliency/pmcp#324).
             status.status = ServerStatusEnum.ERROR
-            status.last_error = describe_exception(e)
+            status.last_error = _handshake_error(e)
             await self._cleanup_client(name, managed)
             raise
 
````

### File — `tests/test_cancel_teardown.py`

````python
"""Consiliency/pmcp#324: a caller cancelled during a teardown keeps its
cancellation, and the teardown still completes.

Every teardown sequence in `ClientManager` cancels child tasks it owns and
awaits them, absorbing the child's `CancelledError`. Before this module the
same `except` also absorbed the *caller's* cancellation, so a cancelled
caller ran on: it raised the handshake error instead of `CancelledError`, and
`_connect_with_retry` (which catches `Exception`) started another attempt.
Re-raising at the await instead would skip the process-tree termination,
the transport close and the `_clients` removal that follow.

The rule these tests pin: a caller cancelled at ANY await of a teardown
(1) still gets the whole teardown, (2) sees exactly one `CancelledError`
afterwards, and (3) a child's own expected cancellation is still absorbed.

Deterministic: every suspension point is reached through an `asyncio.Event`
gate, and the cancel is delivered while the caller is parked on that gate.
No timed sleeps. `asyncio.wait_for(..., _HANG_GUARD_S)` is a hang guard only
(tests/_timing.py), far below the 60 s async watchdog.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
import sys
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.client import manager as manager_mod
from pmcp.client.manager import ClientManager, ManagedClient
from pmcp.types import (
    LocalMcpServerConfig,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    ServerStatus,
    ServerStatusEnum,
)

_HANG_GUARD_S = 10.0
_CHILD_POINTS = {"read_task", "stderr_task", "outbound_writer"}
_PKG = Path(manager_mod.__file__).resolve().parents[1]  # src/pmcp


class _Gate:
    """One suspension point: `entered` is set once the caller is parked on
    it; `release` lets it finish."""

    def __init__(self) -> None:
        self.parked = asyncio.Event()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = False


def _child(gate: _Gate | None) -> asyncio.Task[None]:
    """A child task the teardown will cancel. With a gate it *defers* its
    own cancellation until released, so the teardown is parked awaiting it;
    without one it finishes as soon as it is cancelled."""

    async def body() -> None:
        try:
            if gate is not None:
                gate.parked.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if gate is not None:
                gate.entered.set()
                await gate.release.wait()
                gate.finished = True
            raise

    return asyncio.create_task(body())


def _gated_call(gate: _Gate | None, record: list[str], label: str) -> Any:
    """An async stand-in for `_terminate_process_tree` /
    `_close_remote_transport` that parks on `gate`."""

    async def call(*_a: Any, **_k: Any) -> None:
        if gate is not None:
            gate.entered.set()
            await gate.release.wait()
            gate.finished = True
        record.append(label)

    return call


async def _cancel_while_parked(task: asyncio.Task[Any], gate: _Gate) -> None:
    """Deliver one cancel to `task` while it is parked on `gate`, then let
    the gate finish. `cancel()` schedules the caller's wake-up before
    `release.set()` schedules the gate's, so the caller always sees the
    cancel first (FIFO ready queue) -- no yield count to tune."""
    await asyncio.wait_for(gate.entered.wait(), _HANG_GUARD_S)
    assert not task.done(), "caller finished before reaching the gate"
    task.cancel()
    gate.release.set()


async def _outcome(task: asyncio.Task[Any]) -> tuple[str, Any]:
    """('cancelled', None) | ('raised', exc) | ('returned', value)."""
    try:
        value = await asyncio.wait_for(asyncio.shield(task), _HANG_GUARD_S)
    except asyncio.CancelledError:
        if task.cancelled():
            return ("cancelled", None)
        raise
    except BaseException as exc:  # noqa: BLE001 - classify, don't propagate
        return ("raised", exc)
    return ("returned", value)


def _stdio_config(name: str = "srv") -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name, source="project", config=LocalMcpServerConfig(command="fake")
    )


def _remote_config(name: str = "remote") -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name,
        source="custom",
        config=RemoteMcpServerConfig(
            type="streamable-http", url="http://example.invalid/mcp"
        ),
    )


# --- the handshake-failure except-blocks -------------------------------------

_STDIO_POINTS = ["read_task", "stderr_task", "outbound_writer", "terminate"]


def _stdio_harness(
    mgr: ClientManager, point: str | None, gate: _Gate, record: list[str]
) -> tuple[Any, Any]:
    """Patch `_connect_stdio`'s collaborators so its handshake fails and its
    teardown parks at `point`."""
    process = MagicMock()
    process.returncode = None
    process.stderr = MagicMock()

    async def _read_stdout(name: str, managed: ManagedClient) -> None:
        await _child(gate if point == "read_task" else None)

    async def _read_stderr(name: str, stderr: Any) -> None:
        await _child(gate if point == "stderr_task" else None)

    async def _send_initialize(managed: ManagedClient) -> None:
        managed.outbound_writer = _child(gate if point == "outbound_writer" else None)
        if point in _CHILD_POINTS:  # the gated child must be inside its `try`
            await asyncio.wait_for(gate.parked.wait(), _HANG_GUARD_S)
        raise RuntimeError("handshake failed")

    mgr._read_stdout = _read_stdout  # type: ignore[method-assign]
    mgr._read_stderr = _read_stderr  # type: ignore[method-assign]
    mgr._send_initialize = _send_initialize  # type: ignore[method-assign]
    spawn = patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    term = patch(
        "pmcp.client.manager._terminate_process_tree",
        _gated_call(gate if point == "terminate" else None, record, "terminated"),
    )
    return spawn, term


@pytest.mark.parametrize("point", _STDIO_POINTS)
async def test_stdio_handshake_teardown_keeps_caller_cancel(point: str) -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term = _stdio_harness(mgr, point, gate, record)
    with spawn, term:
        task = asyncio.create_task(mgr._connect_stdio(_stdio_config()))
        await _cancel_while_parked(task, gate)
        kind, detail = await _outcome(task)

    assert kind == "cancelled", (
        f"caller cancelled at {point} lost its cancellation: {kind} {detail!r}"
    )
    assert gate.finished, f"teardown abandoned the {point} wait"
    assert record == ["terminated"], f"process tree not terminated: {record}"
    assert "srv" not in mgr._clients, f"stale client left after cancel at {point}"


_REMOTE_POINTS = ["read_task", "outbound_writer", "close_transport"]


async def _remote_handshake_failure(
    mgr: ClientManager, point: str | None, gate: _Gate, record: list[str]
) -> asyncio.Task[None]:
    class _Transport:
        async def __aenter__(self) -> tuple[Any, Any]:
            return (MagicMock(), MagicMock())

        async def __aexit__(self, *exc_info: Any) -> None:
            return None

    async def _read_sse(name: str, managed: ManagedClient, read_stream: Any) -> None:
        await _child(gate if point == "read_task" else None)

    async def _send_initialize(managed: ManagedClient) -> None:
        managed.outbound_writer = _child(gate if point == "outbound_writer" else None)
        if point in _CHILD_POINTS:  # the gated child must be inside its `try`
            await asyncio.wait_for(gate.parked.wait(), _HANG_GUARD_S)
        raise RuntimeError("handshake failed")

    real_close = mgr._close_remote_transport
    gated = _gated_call(gate if point == "close_transport" else None, record, "closed")

    async def _close(name: str, managed: ManagedClient, timeout: float = 5.0) -> None:
        await gated()
        await real_close(name, managed, timeout)

    mgr._read_sse = _read_sse  # type: ignore[method-assign]
    mgr._send_initialize = _send_initialize  # type: ignore[method-assign]
    mgr._close_remote_transport = _close  # type: ignore[method-assign]
    return asyncio.create_task(
        mgr._connect_remote_stream(_remote_config(), _Transport(), transport_name="t")
    )


@pytest.mark.parametrize("point", _REMOTE_POINTS)
async def test_remote_handshake_teardown_keeps_caller_cancel(point: str) -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    task = await _remote_handshake_failure(mgr, point, gate, record)
    await _cancel_while_parked(task, gate)
    kind, detail = await _outcome(task)

    assert kind == "cancelled", (
        f"caller cancelled at {point} lost its cancellation: {kind} {detail!r}"
    )
    assert gate.finished, f"teardown abandoned the {point} wait"
    assert record == ["closed"], f"remote transport not closed: {record}"
    assert "remote" not in mgr._clients, f"stale client left after cancel at {point}"
    owners = [t for t in mgr._background_tasks if not t.done()]
    assert owners == [], f"transport owner outlived the teardown: {owners}"


async def test_a_lost_cancel_does_not_start_another_connect_attempt() -> None:
    """The consequence that makes this more than one event-loop step:
    `_connect_with_retry` catches `Exception`, so a cancel converted into the
    handshake error starts the next attempt."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term = _stdio_harness(mgr, "read_task", gate, record)
    attempts = 0
    real_connect = mgr._connect_server

    async def _counting(config: ResolvedServerConfig) -> None:
        nonlocal attempts
        attempts += 1
        if attempts > 1:  # a second attempt must never start; stop it here
            raise RuntimeError("second attempt started")
        await real_connect(config)

    mgr._connect_server = _counting  # type: ignore[method-assign]
    with spawn, term, patch.object(manager_mod, "RETRY_DELAYS", [0.0, 0.0, 0.0]):
        task = asyncio.create_task(mgr._connect_with_retry(_stdio_config()))
        await _cancel_while_parked(task, gate)
        kind, detail = await _outcome(task)

    assert (kind, attempts) == ("cancelled", 1), (
        f"cancelled connect retried: {kind} {detail!r}, attempts={attempts}"
    )


# --- the other teardown sequences in the class -------------------------------

_CLEANUP_POINTS = ["read_task", "stderr_task", "outbound_writer", "terminate"]


@pytest.mark.parametrize("point", _CLEANUP_POINTS)
async def test_cleanup_client_keeps_caller_cancel(point: str) -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
    managed.is_remote = False
    managed.read_task = _child(gate if point == "read_task" else None)
    managed.stderr_task = _child(gate if point == "stderr_task" else None)
    managed.outbound_writer = _child(gate if point == "outbound_writer" else None)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = status
    await asyncio.sleep(0)  # let the children park before they are cancelled
    with patch(
        "pmcp.client.manager._terminate_process_tree",
        _gated_call(gate if point == "terminate" else None, record, "terminated"),
    ):
        task = asyncio.create_task(mgr._cleanup_client("srv", managed))
        await _cancel_while_parked(task, gate)
        kind, detail = await _outcome(task)

    assert kind == "cancelled", (
        f"caller cancelled at {point} lost its cancellation: {kind} {detail!r}"
    )
    assert gate.finished
    assert record == ["terminated"]
    assert "srv" not in mgr._clients


_DISCONNECT_POINTS = ["read_task", "outbound_writer", "terminate"]


@pytest.mark.parametrize("point", _DISCONNECT_POINTS)
async def test_disconnect_server_keeps_caller_cancel(point: str) -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
    managed.config.source = "custom"
    managed.is_remote = False
    managed.read_task = _child(gate if point == "read_task" else None)
    managed.outbound_writer = _child(gate if point == "outbound_writer" else None)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = status
    await asyncio.sleep(0)
    with patch(
        "pmcp.client.manager._terminate_process_tree",
        _gated_call(gate if point == "terminate" else None, record, "terminated"),
    ):
        task = asyncio.create_task(mgr.disconnect_server("srv", force=True))
        await _cancel_while_parked(task, gate)
        kind, detail = await _outcome(task)

    assert kind == "cancelled", (
        f"caller cancelled at {point} lost its cancellation: {kind} {detail!r}"
    )
    assert gate.finished
    assert record == ["terminated"]
    assert "srv" not in mgr._clients
    assert mgr._servers["srv"].status is ServerStatusEnum.LAZY
    assert not mgr._lifecycle_lock.locked()


async def test_disconnect_server_does_not_cancel_its_own_caller() -> None:
    """The background-task sweep runs inside the private teardown task, where
    `current_task()` is no longer the caller; the caller is excluded
    explicitly. A caller that is itself a background task scoped to the
    server name (a reconnect path) must not cancel itself."""
    mgr = ClientManager()
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
    managed.config.source = "custom"
    managed.is_remote = False
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = status
    real_sweep = mgr._cancel_background_tasks
    holder: dict[str, asyncio.Task[Any]] = {}

    async def _sweep(
        *, server_name: str | None = None, exclude: set[Any] | None = None
    ) -> None:
        # Without the exclusion the private teardown task would gather its
        # own caller, which is waiting on it: a deadlock, not a failure
        # (measured: the mutant hangs the module). Fail it loudly instead.
        if holder["caller"] not in (exclude or set()):
            raise AssertionError("the sweep would cancel and await its own caller")
        await real_sweep(server_name=server_name, exclude=exclude)

    mgr._cancel_background_tasks = _sweep  # type: ignore[method-assign]
    with patch("pmcp.client.manager._terminate_process_tree", AsyncMock()):
        task = mgr._track_background_task(
            asyncio.create_task(mgr.disconnect_server("srv", force=True)), "srv"
        )
        holder["caller"] = task
        kind, value = await _outcome(task)
    assert (kind, value) == ("returned", (True, 0, None)), f"{kind} {value!r}"


# --- what must NOT change ------------------------------------------------------


async def test_child_cancellation_is_still_absorbed_without_a_caller_cancel() -> None:
    """No caller cancel: every child ends cancelled and the handshake error,
    not a CancelledError, reaches the caller."""
    mgr = ClientManager()
    record: list[str] = []
    spawn, term = _stdio_harness(mgr, None, _Gate(), record)
    with spawn, term:
        with pytest.raises(RuntimeError, match="handshake failed"):
            await asyncio.wait_for(mgr._connect_stdio(_stdio_config()), _HANG_GUARD_S)
    assert record == ["terminated"]
    assert "srv" not in mgr._clients


async def test_child_cancellation_is_still_absorbed_on_disconnect() -> None:
    mgr = ClientManager()
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
    managed.config.source = "custom"
    managed.is_remote = False
    managed.read_task = _child(None)
    managed.outbound_writer = _child(None)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = status
    await asyncio.sleep(0)
    with patch("pmcp.client.manager._terminate_process_tree", AsyncMock()):
        ok, _cancelled, err = await asyncio.wait_for(
            mgr.disconnect_server("srv", force=True), _HANG_GUARD_S
        )
    assert (ok, err) == (True, None)
    assert managed.read_task.cancelled() and managed.outbound_writer is None


async def test_one_caller_cancel_is_delivered_exactly_once() -> None:
    """Not lost, and not doubled: the caller catches one CancelledError and
    can keep awaiting; on 3.11+ its cancel count is exactly 1."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term = _stdio_harness(mgr, "read_task", gate, record)
    seen: dict[str, Any] = {}

    async def caller() -> str:
        try:
            await mgr._connect_stdio(_stdio_config())
        except asyncio.CancelledError:
            current = asyncio.current_task()
            assert current is not None
            if sys.version_info >= (3, 11):
                seen["cancelling"] = current.cancelling()
            await asyncio.sleep(0)  # a second, queued cancel would land here
            return "caught once"
        return "not cancelled"

    with spawn, term:
        task = asyncio.create_task(caller())
        await _cancel_while_parked(task, gate)
        kind, value = await _outcome(task)

    assert (kind, value) == ("returned", "caught once")
    if sys.version_info >= (3, 11):
        assert seen["cancelling"] == 1


# --- the helper itself ----------------------------------------------------------


def _helper() -> Callable[[Awaitable[Any]], Awaitable[Any]]:
    helper = getattr(manager_mod, "_finish_then_reraise_cancel", None)
    if helper is None:
        pytest.fail("pmcp.client.manager._finish_then_reraise_cancel is missing")
    return helper


async def test_helper_completes_cleanup_across_repeated_caller_cancels() -> None:
    helper = _helper()
    gates = [_Gate(), _Gate()]
    steps: list[int] = []

    async def cleanup() -> str:
        for i, g in enumerate(gates):
            g.entered.set()
            await g.release.wait()
            steps.append(i)
        return "done"

    task = asyncio.create_task(helper(cleanup()))
    await _cancel_while_parked(task, gates[0])
    await _cancel_while_parked(task, gates[1])
    kind, _ = await _outcome(task)
    assert (kind, steps) == ("cancelled", [0, 1])


async def test_helper_returns_the_cleanup_result_and_raises_its_error() -> None:
    helper = _helper()

    async def ok() -> str:
        return "value"

    async def boom() -> None:
        raise ValueError("cleanup failed")

    assert await helper(ok()) == "value"
    with pytest.raises(ValueError, match="cleanup failed"):
        await helper(boom())


async def test_helper_cancel_wins_over_a_cleanup_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The cancel is re-raised; the teardown error it displaces is logged,
    not dropped (the `_close_remote_transport` precedent)."""
    helper = _helper()
    gate = _Gate()

    async def cleanup() -> None:
        gate.entered.set()
        await gate.release.wait()
        raise ValueError("cleanup failed")

    task = asyncio.create_task(helper(cleanup()))
    with caplog.at_level("WARNING", logger="pmcp.client.manager"):
        await _cancel_while_parked(task, gate)
        kind, _ = await _outcome(task)
    assert kind == "cancelled"
    assert "cleanup failed" in caplog.text, caplog.text


async def test_helper_caller_cancel_in_the_completion_step_is_kept() -> None:
    """The race a `task.done()` check gets wrong: the cleanup finishes and
    the caller is cancelled in the same loop iteration."""
    helper = _helper()
    release = asyncio.Event()

    async def cleanup() -> str:
        await release.wait()
        return "done"

    task = asyncio.create_task(helper(cleanup()))
    await asyncio.sleep(0)  # let the helper park on the shield
    release.set()  # cleanup completes on the next iteration ...
    await asyncio.sleep(0)  # ... which runs now; the shield's callback is queued
    task.cancel()  # ... and the caller is cancelled before it runs
    kind, _ = await _outcome(task)
    assert kind == "cancelled"


async def test_a_failing_terminate_still_drops_the_stale_client() -> None:
    """The `_clients` removal is in a `finally`: a teardown step that raises
    no longer leaves the ERROR entry behind (on main it did)."""
    mgr = ClientManager()
    record: list[str] = []
    spawn, _term = _stdio_harness(mgr, None, _Gate(), record)
    with (
        spawn,
        patch(
            "pmcp.client.manager._terminate_process_tree",
            AsyncMock(side_effect=OSError("kill failed")),
        ),
    ):
        with pytest.raises(OSError, match="kill failed"):
            await asyncio.wait_for(mgr._connect_stdio(_stdio_config()), _HANG_GUARD_S)
    assert "srv" not in mgr._clients


async def test_a_failing_transport_close_still_drops_the_stale_client() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    task = await _remote_handshake_failure(mgr, None, gate, record)
    mgr._close_remote_transport = AsyncMock(  # type: ignore[method-assign]
        side_effect=OSError("close failed")
    )
    kind, detail = await _outcome(task)
    assert kind == "raised" and isinstance(detail, OSError), f"{kind} {detail!r}"
    assert "remote" not in mgr._clients
    for t in list(mgr._background_tasks):
        t.cancel()
    await asyncio.gather(*mgr._background_tasks, return_exceptions=True)


# --- a cancel DURING the handshake gets the same teardown -----------------------


def _parking_handshake(gate: _Gate) -> Any:
    async def _send_initialize(managed: ManagedClient) -> None:
        gate.entered.set()
        await asyncio.Event().wait()

    return _send_initialize


async def test_stdio_cancel_during_handshake_still_tears_down() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term = _stdio_harness(mgr, None, gate, record)
    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
    with spawn, term:
        task = asyncio.create_task(mgr._connect_stdio(_stdio_config()))
        await _cancel_while_parked(task, gate)
        kind, detail = await _outcome(task)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert record == ["terminated"], "process left running after a cancelled handshake"
    assert "srv" not in mgr._clients, (
        "CONNECTING client left after a cancelled handshake"
    )
    assert mgr._servers["srv"].last_error == "Connection cancelled"


async def test_remote_cancel_during_handshake_still_tears_down() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    task = await _remote_handshake_failure(mgr, None, gate, record)
    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
    await _cancel_while_parked(task, gate)
    kind, detail = await _outcome(task)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert record == ["closed"], "transport left open after a cancelled handshake"
    assert "remote" not in mgr._clients
    assert [t for t in mgr._background_tasks if not t.done()] == []


async def test_adopt_cancel_during_handshake_still_tears_down() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    process = MagicMock()
    process.returncode = None
    process.stderr = None

    async def _read_stdout(name: str, managed: ManagedClient) -> None:
        await asyncio.Event().wait()

    mgr._read_stdout = _read_stdout  # type: ignore[method-assign]
    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
    with patch(
        "pmcp.client.manager._terminate_process_tree",
        _gated_call(None, record, "terminated"),
    ):
        task = asyncio.create_task(mgr.adopt_process("srv", process, _stdio_config()))
        await _cancel_while_parked(task, gate)
        kind, detail = await _outcome(task)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert record == ["terminated"]
    assert "srv" not in mgr._clients


# --- the sites that stay as they are ---------------------------------------------


async def test_disconnect_all_redelivers_a_cancel_absorbed_by_shutdown_one() -> None:
    """`_shutdown_one` absorbs a CancelledError at its read-task wait, but it
    runs as a `gather` child: gather re-raises the outer cancel once every
    child is done, so disconnect_all's caller still sees it."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
    managed.is_remote = False
    managed.read_task = _child(gate)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = status
    await asyncio.wait_for(gate.parked.wait(), _HANG_GUARD_S)
    with patch(
        "pmcp.client.manager._terminate_process_tree",
        _gated_call(None, record, "terminated"),
    ):
        task = asyncio.create_task(mgr.disconnect_all())
        await _cancel_while_parked(task, gate)
        kind, detail = await _outcome(task)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert record == ["terminated"]


@pytest.mark.skipif(sys.version_info < (3, 11), reason="asyncio.timeout is 3.11+")
async def test_a_timeout_scope_around_the_teardown_still_reports_timeout() -> None:
    """The cancel count is untouched: `asyncio.timeout()` that fires while the
    caller is parked in the teardown still converts to TimeoutError."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term = _stdio_harness(mgr, "terminate", gate, record)

    async def caller() -> str:
        try:
            async with asyncio.timeout(None) as scope:  # type: ignore[attr-defined]
                holder["scope"] = scope
                await mgr._connect_stdio(_stdio_config())
        except TimeoutError:
            return "timeout"
        return "no timeout"

    holder: dict[str, Any] = {}
    with spawn, term:
        task = asyncio.create_task(caller())
        await asyncio.wait_for(gate.entered.wait(), _HANG_GUARD_S)
        loop = asyncio.get_running_loop()
        holder["scope"].reschedule(loop.time())  # fires on the next iteration
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gate.release.set()
        kind, value = await _outcome(task)

    assert (kind, value) == ("returned", "timeout")
    assert record == ["terminated"]
    assert "srv" not in mgr._clients


# --- the class: every handler that catches a cancellation is classified --------

# (module, enclosing function, "reraises" | "absorbs") -> (count, why). A new
# handler fails this test until it is classified here; "absorbs" is legal only
# where the absorbed cancellation is provably not the caller's (Consiliency/pmcp#324).
_CANCEL_HANDLERS: dict[tuple[str, str, str], tuple[int, str]] = {
    ("cli.py", "run_server", "absorbs"): (1, "task root: logs, then the process exits"),
    ("client/manager.py", "_finish_then_reraise_cancel", "absorbs"): (
        1,
        "defers: kept and re-raised once the private teardown task is done",
    ),
    ("client/manager.py", "_reap_cancelled_child", "absorbs"): (
        1,
        "the child's own cancel; only ever awaited inside a private teardown task",
    ),
    ("client/manager.py", "ClientManager._close_remote_transport", "absorbs"): (
        2,
        "awaits the owner it just cancelled; every caller is a private teardown "
        "task or a gather child whose gather re-raises",
    ),
    ("client/manager.py", "ClientManager._close_remote_transport", "reraises"): (
        1,
        "the caller's cancel: escalates to the owner, then re-raises",
    ),
    ("client/manager.py", "ClientManager._connect_remote_stream", "reraises"): (
        2,
        "pre-handoff BaseException; handshake teardown",
    ),
    ("client/manager.py", "ClientManager._connect_stdio", "reraises"): (
        1,
        "handshake teardown",
    ),
    ("client/manager.py", "ClientManager.adopt_process", "reraises"): (
        1,
        "handshake teardown",
    ),
    (
        "client/manager.py",
        "ClientManager._disconnect_all_unlocked._shutdown_one",
        "absorbs",
    ): (1, "gather child: gather re-raises the outer cancel when all are done"),
    ("client/manager.py", "ClientManager._health_monitor_loop", "absorbs"): (
        1,
        "task root: its own cancel ends the loop",
    ),
    ("client/manager.py", "ClientManager._own_remote_transport", "reraises"): (
        1,
        "hands a pre-handoff failure to `ready`, otherwise re-raises",
    ),
    ("client/manager.py", "ClientManager._reconcile_server_catalog", "reraises"): (
        1,
        "re-raise",
    ),
    ("client/manager.py", "ClientManager._send_request", "reraises"): (1, "re-raise"),
    ("env_store.py", "write_env_file", "reraises"): (1, "sync temp-file cleanup"),
    ("manifest/installer.py", "JobManager._handle_task_exception", "absorbs"): (
        1,
        "sync done-callback reading a finished task's result; no await",
    ),
    ("manifest/installer.py", "JobManager._monitor_install", "absorbs"): (
        1,
        "task root: its own cancel ends the job",
    ),
    ("manifest/installer.py", "JobManager._monitor_install", "reraises"): (
        1,
        "re-raise",
    ),
    ("manifest/registry.py", "save_registry_cache", "reraises"): (
        1,
        "sync temp-file cleanup",
    ),
    ("package_approvals.py", "_write_store", "reraises"): (1, "sync temp-file cleanup"),
    ("tools/handlers.py", "GatewayTools._run_update_probe_command", "reraises"): (
        1,
        "reaps the probe, then re-raises",
    ),
    ("trust_store.py", "_write_store", "reraises"): (1, "sync temp-file cleanup"),
}


def _catches_cancellation(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for node in names:
        name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
        if name in {"CancelledError", "BaseException"}:
            return True
    return False


def _modules() -> list[tuple[str, ast.Module]]:
    return [
        (path.relative_to(_PKG).as_posix(), ast.parse(path.read_text()))
        for path in sorted(_PKG.rglob("*.py"))
    ]


def _scan() -> dict[tuple[str, str, str], int]:
    found: dict[tuple[str, str, str], int] = {}
    for rel, tree in _modules():

        def visit(node: ast.AST, scope: list[str]) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    visit(child, [*scope, child.name])
                    continue
                if isinstance(child, ast.ExceptHandler) and _catches_cancellation(
                    child
                ):
                    raises = any(isinstance(n, ast.Raise) for n in ast.walk(child))
                    key = (rel, ".".join(scope), "reraises" if raises else "absorbs")
                    found[key] = found.get(key, 0) + 1
                visit(child, scope)

        visit(tree, [])
    return found


def test_every_cancellation_handler_is_classified() -> None:
    found = _scan()
    expected = {k: n for k, (n, _why) in _CANCEL_HANDLERS.items()}
    assert found == expected, (
        f"unclassified or changed: {sorted(set(found.items()) - set(expected.items()))}; "
        f"gone: {sorted(set(expected.items()) - set(found.items()))}"
    )


# The teardown bodies that absorb a child's cancellation, and the only call
# shape allowed for each: `_finish_then_reraise_cancel(self.<body>(...))`.
_SCOPED_BODIES = {
    "_abort_stdio_handshake",
    "_abort_remote_handshake",
    "_finish_disconnect",
    "_cleanup_client_body",
}
# Who may call the absorbing primitives: a scoped body, or `_teardown_outbound`
# (itself called only from scoped bodies).
_REAP_CALLERS = _SCOPED_BODIES | {"_teardown_outbound"}


def _callee(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def test_absorbing_teardown_only_runs_inside_the_private_task() -> None:
    tree = dict(_modules())["client/manager.py"]
    misplaced: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
            name = _callee(call)
            if name == "_reap_cancelled_child" and fn.name not in _REAP_CALLERS:
                misplaced.append(f"{fn.name} calls _reap_cancelled_child")
            if name == "_teardown_outbound" and fn.name not in _SCOPED_BODIES:
                misplaced.append(f"{fn.name} calls _teardown_outbound")
    wrapped: set[str] = set()
    bare: list[str] = []
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        if _callee(call) == "_finish_then_reraise_cancel":
            for arg in call.args:
                if isinstance(arg, ast.Call):
                    wrapped.add(_callee(arg))
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        name = _callee(call)
        if name in _SCOPED_BODIES and name not in wrapped:
            bare.append(name)
    direct = [
        _callee(c)
        for p in ast.walk(tree)
        if isinstance(p, ast.Await) and isinstance(p.value, ast.Call)
        for c in [p.value]
        if _callee(c) in _SCOPED_BODIES
    ]
    assert misplaced == [], misplaced
    assert wrapped >= _SCOPED_BODIES, f"never wrapped: {_SCOPED_BODIES - wrapped}"
    assert direct == [] and bare == [], f"awaited outside the helper: {direct or bare}"
````
