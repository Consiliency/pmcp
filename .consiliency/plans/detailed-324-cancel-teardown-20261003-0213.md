# Detailed plan: a cancelled caller keeps its cancellation through every teardown, and the teardown still completes

> Written on main `89559db` (dev0, a team host), worktree `pmcp-324`, branch
> `plan/324-cancel-teardown`. Every number below was measured on that tree, or
> on the spike of this plan applied to it, on CPython 3.10.20, 3.11 and 3.12.14.
> The spike was then removed; this PR carries only this file. The spike's
> source patch and its test module are reproduced verbatim at the end
> (*Verbatim bodies*).
>
> **Round 2** (after the round-1 panel on Consiliency/pmcp#332): fixed two
> blocking findings -- B1 (claude, codex): a cancel caught in a handshake
> handler was lost when the teardown then raised; B2 (codex): loop shutdown
> cancelled the private teardown task and left a SIGTERM-ignoring server
> alive. The helper's shield loop is replaced by a gate future that does not
> wake a cancelled caller (F2), a cancelled caller skips the graceful remote
> close (F3), the bounds are restated (F3), the auto-reconnect revival is
> stated as a follow-up (F4), and the structural test and census are fixed
> (F5). All numbers re-measured on the round-2 spike on 3.10, 3.11, 3.12.

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
`suppress(OSError)`, none touches cancellation). **23** handlers on main
(round 1 said 22: the table's last row covers four sync sites). The round-2
patch adds one, in `_terminate_process_tree` (Design decision 9). "Absorbs"
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

### 1. One primitive: run the teardown in a private task; the caller parks on a gate that defers its cancel

**Decision (round 2):** `_finish_then_reraise_cancel(cleanup, *, pending=None)`
in `manager.py`:
- it wraps the teardown coroutine in a task nobody else holds, named
  `pmcp-teardown:<qualname>` so the 60 s async watchdog
  (`tests/runtime/_hang_watchdog.py`) names the teardown a hang is parked in;
- the caller parks on a `_DeferredCancelGate`, an `asyncio.Future` subclass
  whose `cancel()` returns **False**, resolved by the private task's
  done-callback;
- `Task.cancel()` on a task parked on a future first calls that future's
  `cancel()`; when it returns False, CPython only sets the task's
  `_must_cancel` flag and leaves it parked, then throws **one**
  `CancelledError` into it when the future resolves. So a cancel does not wake
  the caller at all, a thousand cancels cost no wake-ups, and the cancel is
  delivered exactly once, after the teardown -- including one that arrives in
  the same loop iteration the teardown finishes (the race a `task.done()`
  check gets wrong);
- `pending` is a cancellation the caller already caught before calling (a
  handshake handler): it is re-raised after the teardown exactly like one that
  arrives during it (Design decision 3);
- a caller cancellation wins over a teardown error; the displaced error is
  logged at WARNING **by type only** (`teardown error displaced by the
  caller's cancellation: OSError`), never by value, and never re-raised;
- each cancel request also sets a "hurry" `asyncio.Event`, which the private
  task sees through a `ContextVar` (`_TEARDOWN_HURRY`) set in its own context
  copy; `_close_remote_transport` uses it (Design decision 6).

It never calls `uncancel()`, so on 3.11+ the caller's cancel count is left as
the canceller set it and `asyncio.timeout()` still converts its own cancel to
`TimeoutError` (Design decision 8). The gate relies on CPython's documented-in-
source `Task.cancel` protocol (call the waiter's `cancel()`, fall back to
`_must_cancel`), identical in the C and pure-Python tasks on 3.10, 3.11 and
3.12; mutant M9 (a gate whose `cancel()` returns True) is red on all three.

**Round 1 used a shield loop** (`while not task.done(): await
asyncio.shield(task)`, remembering the first `CancelledError`). It was
correct but woke the caller on every cancel: inside an anyio cancel scope,
which re-delivers `task.cancel()` on every loop iteration until the task
leaves the scope, that was one wake-up and one new shield per iteration
(Design decision 10).

**Why not the other alternatives:**
- *Re-raise at the await:* skips terminate/close/pop -- the constraint the
  issue states.
- *Check `child.done()` after the shield raises:* wrong in the same-iteration
  race; `test_helper_caller_cancel_in_the_completion_step_is_kept` pins it.
- *`Task.cancelling()`:* 3.11+ only.
- *Detach the teardown and raise immediately:* the `_clients` removal would
  land after the caller has seen `CancelledError`; the issue and the brief ask
  for "re-raise once cleanup completes".

### 2. The absorbing waits move into named teardown bodies that run only under the primitive

Each defective sequence becomes a method whose whole body runs inside the
private task, so the only cancellation its child waits can see is the child's
(or loop shutdown's, Design decision 9):

| Sequence | New body | Called as |
|---|---|---|
| stdio handshake failure | `_abort_stdio_handshake(name, managed, process)` | `await _finish_then_reraise_cancel(self._abort_stdio_handshake(...), pending=_caught_cancel(e))` |
| remote handshake failure | `_abort_remote_handshake(name, managed)` | same |
| `disconnect_server` from the read task to the status reset | `_finish_disconnect(name, managed, cancelled, *, caller)` | `return await _finish_then_reraise_cancel(...)` |
| `_cleanup_client` | `_cleanup_client_body(name, managed)` | `_cleanup_client(name, managed, *, pending=None)` is now that one call |

The child waits become `_reap_cancelled_child(task, *, timeout=None)`:
cancel, await through `shield` (or `wait_for(shield(...), timeout)`), absorb
whatever it ends with; its docstring states it is correct only inside the
primitive. `_teardown_outbound` keeps its contract and calls
`_reap_cancelled_child(writer, timeout=timeout)`.

Two tests pin the shape:
- `test_every_cancellation_handler_is_classified`: every
  `CancelledError`/`BaseException` handler in `src/pmcp`, with its count, must
  match a reasoned allowlist.
- `test_absorbing_teardown_only_runs_inside_the_private_task` (round 2,
  per call site): every call of `_reap_cancelled_child` /
  `_teardown_outbound` is attributed to its **innermost** enclosing function
  (a nested function's calls are no longer charged to its parent) and must be
  in a scoped body; and **every** call of a scoped body must itself be an
  argument of `_finish_then_reraise_cancel(...)` -- round 1 let one wrapped
  call excuse a bare one elsewhere. `test_the_structural_check_sees_a_bare_call_beside_a_wrapped_one`
  proves both on a synthetic module.

### 3. The handshake `except` also catches `CancelledError`, and hands it to the primitive as `pending`

**Decision:** the three handshake handlers -- `_connect_stdio`,
`_connect_remote_stream`, `adopt_process` -- catch
`(Exception, asyncio.CancelledError)`, not `BaseException`
(`KeyboardInterrupt`/`SystemExit` are not delivered into these coroutines by
asyncio). The status becomes `ERROR` with `last_error = "Connection
cancelled"` (`_handshake_error`; `describe_exception(CancelledError())` is
`''`, measured), where on main it stays `CONNECTING` forever.

**Round-2 fix (B1, claude and codex):** a handler that caught the caller's
`CancelledError` must pass it to the primitive:
`pending=_caught_cancel(e)` (the `CancelledError`, or None). Round 1 did not,
so the primitive knew only of cancels that arrived *during* the teardown; a
teardown that then raised (`_close_remote_transport` deliberately re-raises a
genuine transport failure; `_terminate_process_tree` can raise) replaced the
cancel, escaped past the handler's `raise`, and `_connect_with_retry` started
attempts 2 and 3. Measured on the round-1 spike, 3.10:
`AssertionError: stdio-terminate: the cancel was lost: raised OSError(...)`
(also `remote-close`, `adopt-terminate`), and `raised
RuntimeError('second attempt started') attempts=3`. Main is not affected (its
`except Exception` never catches the cancel). `adopt_process` threads it
through `_cleanup_client(name, managed, pending=...)`.

### 4. The `_clients` removal moves into a `finally` in every teardown body

`_abort_stdio_handshake`, `_abort_remote_handshake` and (round 2)
`_cleanup_client_body`'s stdio branch clear the registries in a `finally`, so
a teardown step that raises no longer leaves the stale entry behind. The
exception still propagates, unless a caller cancel displaces it. Measured on
main: `assert 'srv' not in {'srv': ManagedClient(...)}` with a raising
terminate, the same for `remote` with a raising close; round 2 found the same
for `adopt_process` (B1's adopt case). Falsifiers M14, M15, M17.

### 5. `disconnect_server` passes its caller to the background-task sweep (load-bearing)

Unchanged from round 1. `_cancel_background_tasks` excludes
`asyncio.current_task()`; inside the private task that is the private task,
not the caller. Without an explicit exclusion this is a **deadlock**: the
private task cancels the caller and gathers it while the caller waits on the
private task. `disconnect_server` captures `asyncio.current_task()` and
`_finish_disconnect` passes `exclude={caller}`.
`test_disconnect_server_does_not_cancel_its_own_caller` wraps the sweep so the
mutant (M11) fails instead of hanging.

### 6. Bounds, stated honestly; a cancelled caller skips the graceful remote close

A cancelled caller now waits for its teardown instead of escaping it. The
bounds (round-1 F3 corrected them):
- **child reaps:** unbounded only for a child that ignores cancellation.
  pmcp's own children (`_read_stdout`, `_read_stderr`, `_read_sse`,
  `_drain_outbound`) have no `CancelledError`/`BaseException` handler (the
  AST scan) and end at their next await; the classification test keeps that
  true. **This is a real trade-off:** on main a cancelled caller ran past a
  child that ignored cancellation (with its cancel lost); now it waits. No
  pmcp child does that, and bounding the reaps would change the non-cancelled
  path too.
- **`_terminate_process_tree`:** ≤ 5 s SIGTERM wait + ≤ 3 s SIGKILL wait +
  ≤ ~3 s group reap (`for _ in range(30): ... sleep(0.1)`) = **~11 s**, not 8.
  stdio keeps its SIGTERM grace for a cancelled caller too: a server may need
  it to release a browser profile, which is why the group reap exists.
- **`_close_remote_transport` (round 2, F3):** a cancelled caller no longer
  waits out the 5 s graceful close. `_close_remote_transport` reads
  `_TEARDOWN_HURRY`; once it is set (a cancel during the teardown, or a
  `pending` one), the graceful wait ends at once (`_wait_unless_hurried`) and
  the owner is escalated. Because the hurried escalation can cancel the owner
  *before* it has started to unwind, the owner may enter its transport's
  `__aexit__` with that cancellation already consumed; `_cancel_until_done`
  therefore re-cancels it every 0.5 s (`_HURRY_RECANCEL_S`) until it ends.
  The non-hurried path is unchanged (graceful wait, then one cancel, as on
  main). Measured: with a transport whose `__aexit__` blocks until cancelled
  and the graceful budget raised to 3600 s, a cancelled `disconnect_server`
  returns `CancelledError` with the owner escalated, and a cancel during the
  remote handshake does too in ~0.5 s; with the hurry removed (M19) both hit
  the 10 s hang guard, and with a single cancel (M20) the handshake case hangs.
- `disconnect_server` holds `_lifecycle_lock` through the teardown, as today;
  the lock is released when the caller re-raises (asserted).

### 7. `_cleanup_client`'s contract changes for cancellation only

Docstring (in the patch): a remote close failure is logged and suppressed; a
terminate failure propagates after the registries are cleared; a cancellation
of the caller -- one that arrives during the teardown, or one passed as
`pending` -- is re-raised after the whole teardown.

### 8. Not lost, not doubled, and `asyncio.timeout()` still works

The primitive never calls `cancel()` or `uncancel()` on the caller. Pinned by
`test_one_caller_cancel_is_delivered_exactly_once` (one `CancelledError`, then
`await asyncio.sleep(0)` succeeds; on 3.11+ `cancelling() == 1`),
`test_a_timeout_scope_around_the_teardown_still_reports_timeout` (3.11+), and
`test_helper_completes_cleanup_across_repeated_caller_cancels`.

### 9. Loop shutdown cannot leave the process tree alive (round 2, B2)

`asyncio.shield` and the gate protect against the *caller's* cancellation
only. CPython's shutdown (`asyncio.run` -> `_cancel_all_tasks`) cancels every
pending task, the private teardown task included. Codex's repro: a real
SIGTERM-ignoring server whose handshake fails, shutdown while
`_terminate_process_tree` waits on its exit: the loop closes with the server
alive (`returncode=None`) because the SIGKILL escalation is skipped.

**Decision: a synchronous last-resort kill that cannot be skipped.**
`_terminate_process_tree` wraps everything after its SIGTERM in `try: ...
except asyncio.CancelledError:` that, before re-raising, SIGKILLs the leader
(only if `process.returncode is None`, so a reaped and reusable pid is never
signalled) and the process group (if `_group_alive()`). It runs whoever
cancelled the wait -- a caller, a sweep, or loop shutdown -- with no await, so
nothing can skip it. It adds one handler to the census (classified
`reraises`).

**Rejected:** an `atexit` hook (runs after the loop is gone, misses
non-exiting loops, and would need a process registry) and a `finally` that
always SIGKILLs (it would also fire on the normal path after a clean SIGTERM
exit, where the group is already gone -- the `except CancelledError` is
exactly the case to cover).

**Measured** with `test_loop_shutdown_mid_terminate_still_kills_the_process_tree`
(a subprocess running `asyncio.run` around a real `_connect_stdio` whose child
ignores SIGTERM and spawns a grandchild; `main()` returns while terminate is
parked): **main** and the **round-1 spike**: `process tree outlived shutdown:
leader=... grandchild=...` on 3.10 (main measured on all three as part of the
module run); **round-2 spike**: both gone, on 3.10, 3.11 and 3.12. The same
handler also closes the pre-existing gap for every other cancelled terminate
(`_shutdown_one`, the update probe in `tools/handlers.py`).

### 10. anyio's re-delivered cancel: pmcp's spin removed, anyio's own remains (round-1 F2)

The MCP SDK runs request handlers in anyio scopes, and anyio's
`CancelScope._deliver_cancellation` reschedules itself with `call_soon` on
**every** loop iteration while any task in the scope is alive (`should_retry
= True` even for a task with `_must_cancel` set; anyio 4.14.2,
`anyio/_backends/_asyncio.py:581-629`). Measured (`probe_cpu.py`: a 0.3 s
teardown inside a cancelled anyio scope):

| | helper wake-ups | loop iterations | CPU / wall |
|---|---|---|---|
| round 1, 3.10 / 3.11 / 3.12 | 38,416 / 50,635 / 75,045 | same + 1 | 0.30 s / 0.30 s |
| round 2, 3.10 / 3.11 / 3.12 | **0 / 0 / 0** | 81,606 / 109,224 / 114,683 | 0.30 s / 0.30 s |

The gate removes every pmcp wake-up (the caller stays parked on one future;
`test_a_cancel_scope_does_not_wake_the_caller_during_the_teardown` asserts the
waiter object never changes across 200 re-deliveries), and each loop iteration
becomes about twice as cheap. **The core stays busy** because anyio's delivery
loop itself spins until the task leaves the scope; no pmcp-side change can
stop that without opening an anyio `CancelScope(shield=True)` around the wait,
which would hide the scope's cancel from the teardown and re-deliver it only
at the caller's next await -- turning a handshake error raised in between into
a converted cancel, the defect this plan removes. The window is bounded by the
teardown (remote: ~0 s plus re-cancels thanks to Design decision 6; stdio:
≤ ~11 s). Stated, not fixed.

### 11. Auto-reconnect after a cancelled reconnect: pre-existing, not fixed here (round-1 F4)

Reaping an old client's read task while its status is still `ONLINE` runs
`_read_stdout`'s `finally` -> `_schedule_reconnect`, and `_reconnect_loop`
spawns a new process about 5 s later. Main does the same; the cancelled
*caller* spawns nothing (fixed by Design decision 2), but the server can
still come back. Marking the client `OFFLINE` before the reap would change
every reconnect path (the reconnect loop itself pre-cleans through
`_cleanup_client`), so it belongs in its own issue. **For the coordinator to
file**, suggested title: "a cancelled reconnect is revived by auto-reconnect:
reaping an ONLINE client's read task schedules a reconnect".

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
- (Round 2) On main *and* on any design that only shields from the caller,
  loop shutdown during `_terminate_process_tree` leaves a SIGTERM-ignoring
  server running (Design decision 9).

## Changes

Line numbers are main `89559db`. The round-2 spike diff is
`src/pmcp/client/manager.py | 544 +++++----` (404 insertions, 140 deletions;
about 60 of the deletions are the `disconnect_server` body dedented into
`_finish_disconnect`, and about 40 are `_terminate_process_tree`'s body
re-indented under its new `try`).

### `src/pmcp/client/manager.py` (modify)

- Imports: `import contextvars`; `from collections.abc import Awaitable,
  Callable, Collection`.
- After `_TaskT` (`manager.py:145`): `_T`, `_HURRY_RECANCEL_S = 0.5`,
  `_DeferredCancelGate`, `_TEARDOWN_HURRY`, `_finish_then_reraise_cancel`
  (Design decision 1), `_reap_cancelled_child` (2), `_wait_unless_hurried`
  and `_cancel_until_done` (6), `_caught_cancel` and `_handshake_error` (3).
- `_terminate_process_tree` (`manager.py:244`): everything after the first
  `_signal(kill=False)` moves under `try: ... except asyncio.CancelledError:`
  with the synchronous last-resort SIGKILL (Design decision 9).
- `disconnect_server` (`manager.py:1349`) and new `_finish_disconnect`: as in
  round 1 (Design decision 5); the read-task wait becomes
  `_reap_cancelled_child(managed.read_task, timeout=1.0)`.
- `_connect_stdio` / `_connect_remote_stream` handshake handlers
  (`manager.py:2461`, `:2789`): `except (Exception, asyncio.CancelledError)
  as e:`; `_handshake_error(e)`; `await _finish_then_reraise_cancel(
  self._abort_*_handshake(...), pending=_caught_cancel(e))`; `raise`.
- New `_abort_stdio_handshake` / `_abort_remote_handshake`: the old bodies,
  child waits as `_reap_cancelled_child`, the `_clients` pop in a `finally`.
- `_close_remote_transport` (`manager.py:2596`): reads `_TEARDOWN_HURRY`;
  graceful wait via `_wait_unless_hurried` when inside a teardown; hurried
  escalation via `_cancel_until_done`; an INFO line instead of the "did not
  close within" WARNING when hurried. Unchanged outside a teardown.
- `_teardown_outbound` (`manager.py:3258-3270`): `_reap_cancelled_child(writer,
  timeout=timeout)` after dropping the refs.
- `_cleanup_client(name, managed, *, pending=None)` (`manager.py:3652`): one
  call to the primitive with `pending`; new `_cleanup_client_body` (old body,
  child loop as `_reap_cancelled_child`, the stdio branch's registry clears in
  a `finally`); docstring per Design decision 7.
- `adopt_process` handshake handler (`manager.py:3817`): `except (Exception,
  asyncio.CancelledError) as e:`; `await self._cleanup_client(name, managed,
  pending=_caught_cancel(e))`.

Unchanged on purpose: `_shutdown_one` (row 6), `_own_remote_transport`
(row 10), the task roots (rows 14–17).

### `tests/test_cancel_teardown.py` (create)

The module in *Verbatim bodies*: **41 tests** (40 on 3.10, where the
`asyncio.timeout` test skips); ~2.3–3.3 s per run, the slowest item the
0.8 s real-subprocess shutdown test.

| Test | Pins |
|---|---|
| `test_stdio_handshake_teardown_keeps_caller_cancel[read_task, stderr_task, outbound_writer, terminate]` | rows 1 + 3 |
| `test_remote_handshake_teardown_keeps_caller_cancel[read_task, outbound_writer, close_transport]` | rows 2 + 3 |
| `test_a_lost_cancel_does_not_start_another_connect_attempt` | the retry consequence |
| `test_cleanup_client_keeps_caller_cancel[...]` (4) | row 5 |
| `test_disconnect_server_keeps_caller_cancel[...]` (3) | row 4 (+ `LAZY`, lock released) |
| `test_disconnect_server_does_not_cancel_its_own_caller` | Design decision 5 |
| `test_child_cancellation_is_still_absorbed_without_a_caller_cancel`, `..._on_disconnect` | the child's cancel is still absorbed |
| `test_one_caller_cancel_is_delivered_exactly_once` | not lost, not doubled |
| `test_helper_completes_cleanup_across_repeated_caller_cancels`, `..._returns_the_cleanup_result_and_raises_its_error`, `..._cancel_wins_over_a_cleanup_error` (logged by type, never by value), `..._caller_cancel_in_the_completion_step_is_kept` | the primitive |
| `test_a_failing_terminate_still_drops_the_stale_client`, `test_a_failing_transport_close_still_drops_the_stale_client` | Design decision 4 |
| `test_stdio_cancel_during_handshake_still_tears_down`, `test_remote_...`, `test_adopt_...` | Design decision 3 |
| **round 2:** `test_a_cancel_caught_in_the_handshake_survives_a_failing_teardown[stdio-terminate, remote-close, adopt-terminate]` | B1, each site × its failing step: `CancelledError`, the step ran once, the displaced error logged by type, no value, no stale entry |
| **round 2:** `test_a_cancel_caught_in_the_handshake_is_not_retried_after_a_failing_teardown` | B1's retry consequence |
| **round 2:** `test_a_cancel_scope_does_not_wake_the_caller_during_the_teardown` | Design decision 10 (anyio) |
| **round 2:** `test_a_cancelled_disconnect_skips_the_graceful_remote_close`, `test_a_cancel_caught_in_the_remote_handshake_skips_the_graceful_close` | Design decision 6 |
| **round 2:** `test_loop_shutdown_mid_terminate_still_kills_the_process_tree` | B2, real subprocesses |
| `test_disconnect_all_redelivers_a_cancel_absorbed_by_shutdown_one` | row 6 stays safe |
| `test_a_timeout_scope_around_the_teardown_still_reports_timeout` (3.11+) | Design decision 8 |
| `test_every_cancellation_handler_is_classified`, `test_absorbing_teardown_only_runs_inside_the_private_task`, **round 2:** `test_the_structural_check_sees_a_bare_call_beside_a_wrapped_one` | the class guard |

Determinism: every suspension point is reached through an `asyncio.Event`
gate, and `asyncio.wait_for(..., 10.0)` is a hang guard only. Round-1 F5
was right that two tests count yields: `test_helper_caller_cancel_in_the_completion_step_is_kept`
uses two `sleep(0)`s to put the caller's cancel between the teardown's
completion and the gate's callback (deterministic on CPython 3.10–3.12; the
order of the ready queue is FIFO), and the anyio test samples the caller's
waiter over 200 `sleep(0)`s. The two remote-close tests release their
blocked transport in a `finally`, so a failing run (main, or mutants M19/M20)
fails at the hang guard instead of hanging the loop's own teardown -- round 2
found that it otherwise did.

## Documentation impact

- `CHANGELOG.md`: one bullet under `## [Unreleased]` → `### Fixed`, phrased
  "see Consiliency/pmcp#324" (no closing keyword): a caller cancelled while a
  server connection is being torn down (a failed or cancelled handshake,
  `disconnect_server`, a reconnect's cleanup) now keeps its cancellation and
  still gets the whole teardown; a cancelled caller no longer waits out the
  graceful remote close; and a stdio server's process tree is SIGKILLed even
  when its termination is cancelled, including by event-loop shutdown.
- `SECURITY.md`: no change; `scripts/check_security_claims.py` reports
  `OK … 129 cited node id(s)` on the spike.

## Dependencies & order

1. `_terminate_process_tree`'s last-resort kill (independent).
2. The gate, the primitive and `_reap_cancelled_child`.
3. Move the bodies, widen the three handshake handlers, thread `pending`.
4. The hurry path in `_close_remote_transport`.
5. The test module; CHANGELOG last.

**Touch points with open work.** Plan PR Consiliency/pmcp#329 (for
Consiliency/pmcp#298) changes `manager.py` around `_send_request`'s write
path and task-hint parsing; its patch adds no `CancelledError`/`BaseException`
handler, so only a textual rebase. The class guard is deliberately src-wide:
any later PR that adds a cancellation handler anywhere in `src/pmcp` fails it
until the handler is classified with a reason.

## Verification

Run from a fresh worktree of `origin/main` on dev0 (a team host):

```bash
git -C ~/code/pmcp worktree add -b fix/324-cancel-teardown "$WORKTREE_ROOT/pmcp-324-fix" origin/main
cd "$WORKTREE_ROOT/pmcp-324-fix"
uv sync --all-extras -p 3.10      # without --all-extras, `uv run` silently uses the system pytest
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir
uv python install 3.11 3.12
for v in 3.11 3.12; do UV_PROJECT_ENVIRONMENT=.venv-$v uv sync --all-extras -p $v -q; done
```

Apply *Verbatim bodies* (`git apply` the patch, write the test module, add the
CHANGELOG bullet by hand), then:

```bash
# 1+2. the new module plus the client-manager suites, on all three Pythons
#      (round-2 spike: 314 passed, 1 skipped on 3.10; 315 passed on 3.11; 315 passed on 3.12)
for v in 3.10 3.11 3.12; do E=.venv; [ $v != 3.10 ] && E=.venv-$v
  $E/bin/python -m pytest tests/test_cancel_teardown.py tests/test_client_manager.py \
    tests/test_client_manager_reconnect.py --cov-fail-under=0 -p no:cacheprovider -q -o timeout=120; done
# 3. CI gates (round-2 spike: all clean)
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src/pmcp/client/manager.py
python3 scripts/check_security_claims.py          # expect OK, 129 cited node ids
python3 scripts/check_plan_consistency.py .consiliency/plans/detailed-324-cancel-teardown-20261003-0213.md
#   measured on this file: "consistent ... blocking inconsistencies: 0", exit 0
# 4. the full suite: once, alone, detached, with a notifying waiter (memory on dev0 is shared)
#    (round-2 spike, run alone on 3.10: 4969 passed, 4 skipped, 80 deselected, 0 failed, 552 s)
nohup .venv/bin/python -m pytest -q -p no:cacheprovider > "$WORKTREE_ROOT/pmcp-324-full.log" 2>&1 &
```

Use the venv's own interpreter (`$E/bin/python -m pytest`) for 3.11/3.12:
round 2 found `.venv-3.11` recreated empty under `uv run -p 3.11`, which then
fails on `import anyio` instead of testing anything.

**Hang guard.** The module runs in ~2.3–3.3 s on every version; the slowest
item is the 0.8 s shutdown subprocess (its own `subprocess.run(timeout=60)`).
Every mutant below ran under `-o timeout=60` and a 300 s subprocess cap; none
hung. The 60 s watchdog, 700 s pytest-timeout and 720 s faulthandler are never
approached.

**Red on main.** The final module against `89559db`'s `manager.py`:
**3.10: 35 failed, 5 passed, 1 skipped; 3.11: 36 failed, 5 passed; 3.12: 36
failed, 5 passed.** The 5 that pass on main are the ones that must: the two
"child cancellation is still absorbed" tests, the `_shutdown_one`
gather-redelivery test, the synthetic structural test, and
`test_a_cancel_caught_in_the_handshake_is_not_retried_after_a_failing_teardown`
(main never catches the cancel, so it never retries; the test guards against
round 1's regression). **Against the round-1 spike** (3.10), every round-2
test is red: the three B1 sites (`the cancel was lost: raised OSError(...)`),
the retry (`attempts=3`), the anyio test (`the caller woke during the
teardown: 200 waiters`), both graceful-close tests (hang guard
`TimeoutError`), and the shutdown test (`process tree outlived shutdown`).

## Acceptance criteria

- [ ] A caller cancelled at every await of the stdio, remote,
  `disconnect_server` and `_cleanup_client` teardowns sees `CancelledError`,
  the teardown completes and the server is gone from `_clients`. Proven by the
  14 parametrized `..._keeps_caller_cancel[...]` cases.
- [ ] A cancelled connect is not retried, whether the cancel lands during the
  teardown or during the handshake with a teardown that then raises. Proven
  by `test_a_lost_cancel_does_not_start_another_connect_attempt` and
  `test_a_cancel_caught_in_the_handshake_is_not_retried_after_a_failing_teardown`.
- [ ] A cancel caught in any of the three handshake handlers survives a
  teardown that raises; the displaced error is logged by type only. Proven by
  `test_a_cancel_caught_in_the_handshake_survives_a_failing_teardown[...]` (3).
- [ ] A cancel during the handshake itself tears down on all three paths, and
  `last_error` reads `Connection cancelled`. Proven by the three
  `..._cancel_during_handshake_still_tears_down` tests.
- [ ] A child's own cancellation is still absorbed. Proven by the two
  `test_child_cancellation_is_still_absorbed_...` tests.
- [ ] One cancel in, one `CancelledError` out; the 3.11+ cancel count is 1;
  `asyncio.timeout()` still reports `TimeoutError`; a cancelled caller is not
  woken during the teardown, even by anyio's per-iteration re-delivery.
  Proven by the exactly-once, timeout-scope and cancel-scope tests.
- [ ] A cancelled caller skips the graceful remote close, and a hurried owner
  that blocks in `__aexit__` is re-cancelled until it ends. Proven by the two
  graceful-close tests.
- [ ] Loop shutdown during a stdio terminate still kills the process tree.
  Proven by `test_loop_shutdown_mid_terminate_still_kills_the_process_tree`.
- [ ] `disconnect_server` never sweeps its own caller (M11).
- [ ] Every `CancelledError`/`BaseException` handler in `src/pmcp` is
  classified; the absorbing primitives run only inside the private task, per
  call site. Proven by the three class-guard tests.
- [ ] Verification steps 1–4 pass on 3.10, and steps 1–2 on 3.11 and 3.12.
- [ ] Every mutant below is red, for the reason stated, on all three Pythons.

## Mutation table

Each mutant was measured on the round-2 spike (`mutants2.py`: apply one string
replacement to `manager.py`, run `tests/test_cancel_teardown.py` with the
target version's own interpreter under `-o timeout=60` and a 300 s cap,
restore the file from a saved copy in a `finally`). **All 21 are red on
3.10, 3.11 and 3.12**; red counts are 3.10's (3.11/3.12 add the
`asyncio.timeout` test where it applies). After each run, `diff manager.py
manager.spike2.py` was empty.

| # | Rule | Mutant | Red tests (3.10) |
|---|---|---|---|
| M1 | stdio teardown is scoped | `await self._abort_stdio_handshake(...)` directly | 9 (every stdio point, retry, exactly-once, B1 stdio, structural) |
| M2 | remote teardown is scoped | `await self._abort_remote_handshake(...)` directly | 6 |
| M3 | disconnect teardown is scoped | `return await self._finish_disconnect(...)` directly | 5 |
| M4 | `_cleanup_client` is scoped | `await self._cleanup_client_body(...)` directly | 6 |
| M5 | cancel during stdio handshake | handler back to `except Exception` | 3 (handshake-cancel, B1 stdio, classification) |
| M6 | cancel during remote handshake | same, remote | 4 |
| M7 | cancel during adopt handshake | same, `adopt_process` | 3 |
| M8 | primitive re-raises | `raise caller_cancel` → `return task.result()` | 25 |
| M9 | the gate does not wake the caller | `_DeferredCancelGate.cancel` returns `super().cancel(msg)` | 20 -- a woken caller leaves the helper at the first cancel, so every deferred-cancel case fails, and the anyio test |
| M10 | `pending` is honoured (B1) | `caller_cancel = pending` → `= None` | 4 (the three B1 sites, the B1 retry) |
| M11 | sweep excludes the caller | drop `exclude=` | `test_disconnect_server_does_not_cancel_its_own_caller` |
| M12 | displaced error is logged | `logger.warning(...)` → `pass` | 4 (helper test, the three B1 sites) |
| M13 | logged by type, never by value | `type(displaced).__name__` → `describe_exception(displaced)` | 4 (same tests: the value appears) |
| M14 | stdio pop in `finally` | `finally:` → `except BaseException: raise` / `else:` | 3 |
| M15 | remote pop in `finally` | same, remote | 3 |
| M16 | `_cleanup_client` forwards `pending` | drop `pending=pending` | B1 `[adopt-terminate]` |
| M17 | `_cleanup_client_body` clears in `finally` | `finally:` → `except BaseException: raise` / `else:` | 2 (B1 adopt, classification) |
| M18 | last-resort kill (B2) | the two kill statements → `if False:` | `test_loop_shutdown_mid_terminate_still_kills_the_process_tree` |
| M19 | hurried remote close | `hurry = _TEARDOWN_HURRY.get()` → `hurry = None` | both graceful-close tests (hang guard) |
| M20 | hurried owner re-cancelled | `await _cancel_until_done(task)` → `await task` | the remote-handshake graceful-close test (hang guard) |
| M21 | reap only inside the private task | `_shutdown_one`'s read wait → `_reap_cancelled_child` | classification, structural |

The implementer re-runs all 21 on the final tree with `mutants2.py`'s
`finally`-restore (never `git checkout --`).

## Non-goals

- **`disconnect_all`'s bookkeeping after a cancel.** Unchanged from round 1:
  shielding it would defeat `server.py:1017`'s 10 s shutdown budget.
- **`_own_remote_transport` forwarding its own pre-handoff cancel** (row 10).
- **Task roots** (rows 14–17).
- **Bounding the child reaps** (Design decision 6).
- **anyio's own delivery spin** (Design decision 10).
- **Skipping stdio's SIGTERM grace for a cancelled caller** (Design decision 6).
- **The auto-reconnect revival** (Design decision 11; a follow-up issue).
- **A remote transport left open by loop shutdown.** Shutdown cancels the
  private task inside `_close_remote_transport`, whose caller-cancel branch
  escalates to the owner, which shutdown cancels as well; sockets close with
  the loop. Only process trees outlive a loop, which is why Design decision 9
  covers terminate only.

## Unverified

- **Python 3.13+.** The gate relies on `Task.cancel` calling the waiter's
  `cancel()` and falling back to `_must_cancel`; that was measured on 3.10,
  3.11 and 3.12 only.
- **A real remote transport under the hurried close.** Measured with a fake
  transport whose `__aexit__` blocks until cancelled.
- **The 11 s terminate bound under a cancelled caller.** Read from the code;
  the shutdown test uses a real SIGTERM-ignoring server but measures that the
  tree dies, not the time.

## Execution Policy

- execute: effort=medium.
- reason: one file of source (`manager.py`, about 404/140 lines, much of it
  moved), on the connection lifecycle every server uses; one-line mistakes
  here are a deadlock (M11), a lost cancel (M10) or an orphaned process
  (M18). Small surface, sharp edges.
- Re-run the mutation table on all three Pythons, verification steps 1–3,
  ruff and mypy before requesting review.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the source patch below (between the ```` fences) to `324-src.patch`,
   then run `git apply 324-src.patch` on `89559db`.
2. Write the test module below to `tests/test_cancel_teardown.py`.
3. Add the `CHANGELOG.md` bullet by hand (the `_cleanup_client` docstring is
   in the patch).

### Patch — `src/pmcp/client/manager.py`

````diff
diff --git a/src/pmcp/client/manager.py b/src/pmcp/client/manager.py
index 57a563e..9f8b959 100644
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -3,6 +3,7 @@
 from __future__ import annotations
 
 import asyncio
+import contextvars
 from contextlib import AsyncExitStack
 import json
 import logging
@@ -15,7 +16,7 @@ import traceback
 import string
 import time
 from collections import deque
-from collections.abc import Callable, Collection
+from collections.abc import Awaitable, Callable, Collection
 from dataclasses import dataclass, field
 from types import ModuleType
 from typing import Any, Iterator, TypeVar
@@ -143,6 +144,187 @@ def describe_exception(exc: BaseException) -> str:
 
 
 _TaskT = TypeVar("_TaskT", bound=asyncio.Task[Any])
+_T = TypeVar("_T")
+# How often a hurried remote close re-cancels a transport owner that has not
+# ended (`_cancel_until_done`).
+_HURRY_RECANCEL_S = 0.5
+
+
+class _DeferredCancelGate(asyncio.Future[None]):
+    """The future a caller parks on while its teardown runs.
+
+    ``Task.cancel()`` on a task parked on a future first calls that future's
+    ``cancel()``; when it returns False the task only sets its own
+    ``_must_cancel`` flag and stays parked, and asyncio throws the
+    ``CancelledError`` into it once, when the future resolves. So the caller
+    is not woken by a cancel at all: one request or a thousand (anyio
+    re-delivers a scope's cancel on every loop iteration) cost no wake-up and
+    are delivered exactly once, after the teardown. That is what keeps a
+    cancelled caller off the CPU for the whole teardown (Consiliency/pmcp#324,
+    round 2). Each request is reported through ``on_cancel`` so the teardown
+    can hurry.
+    """
+
+    def __init__(self, on_cancel: Callable[[], None]) -> None:
+        super().__init__(loop=asyncio.get_running_loop())
+        self._on_cancel = on_cancel
+
+    def cancel(self, msg: Any | None = None) -> bool:
+        self._on_cancel()
+        return False
+
+
+# Set inside a teardown's private task: an Event that is set once the caller
+# has asked to be cancelled. `_close_remote_transport` skips its graceful wait
+# when it is set (Consiliency/pmcp#324, round 2).
+_TEARDOWN_HURRY: contextvars.ContextVar[asyncio.Event | None] = contextvars.ContextVar(
+    "pmcp_teardown_hurry", default=None
+)
+
+
+async def _finish_then_reraise_cancel(
+    cleanup: Awaitable[_T], *, pending: asyncio.CancelledError | None = None
+) -> _T:
+    """Run ``cleanup`` to completion even if the calling task is cancelled
+    while it runs, then re-raise that cancellation (Consiliency/pmcp#324).
+
+    Every teardown below cancels child tasks it owns and awaits them,
+    absorbing the child's expected ``CancelledError``. Awaited in the
+    caller's own task, that ``except`` cannot tell the child's cancellation
+    from the caller's, so a cancelled caller ran on. Re-raising at the await
+    instead would skip the process-tree termination, transport close and
+    ``_clients`` removal that follow. So the teardown runs in a private task
+    nobody else holds, and the caller parks on a `_DeferredCancelGate` that
+    resolves when the private task is done:
+
+    * a cancel of the caller does not wake it; asyncio throws it in once,
+      when the gate resolves -- also when it arrives in the same loop
+      iteration the teardown finishes;
+    * ``pending`` is a cancellation the caller already caught before calling
+      (a handshake handler that caught ``CancelledError``): it is re-raised
+      after the teardown, exactly like one that arrives during it;
+    * a caller cancellation wins over a teardown error; the displaced error
+      is logged by type only (no value) and never re-raised.
+
+    Never calls ``uncancel()``, so on 3.11+ the caller's cancel count is left
+    as the canceller set it and ``asyncio.timeout()`` still converts its own
+    cancel to ``TimeoutError``. Works the same on 3.10, which has no
+    ``cancelling()``.
+    """
+    hurry = asyncio.Event()
+    if pending is not None:
+        hurry.set()
+
+    async def run() -> _T:
+        _TEARDOWN_HURRY.set(hurry)  # this task's own context copy only
+        return await cleanup
+
+    task = asyncio.ensure_future(run())
+    # Named so the 60 s async watchdog (tests/runtime/_hang_watchdog.py) can
+    # say which teardown a hang is parked in.
+    task.set_name(f"pmcp-teardown:{getattr(cleanup, '__qualname__', 'cleanup')}")
+    gate = _DeferredCancelGate(hurry.set)
+
+    def _release(_task: asyncio.Future[_T]) -> None:
+        if not gate.done():
+            gate.set_result(None)
+
+    task.add_done_callback(_release)
+    caller_cancel = pending
+    try:
+        await gate
+    except asyncio.CancelledError as exc:
+        # Deferred, not absorbed: thrown in once the gate resolved, i.e.
+        # after the teardown. Re-raised below.
+        if caller_cancel is None:
+            caller_cancel = exc
+    if caller_cancel is not None:
+        displaced = None if task.cancelled() else task.exception()
+        if displaced is not None:
+            # The cancellation wins; the failure it displaces is logged, by
+            # type only, rather than dropped.
+            logger.warning(
+                "teardown error displaced by the caller's cancellation: "
+                f"{type(displaced).__name__}"
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
+async def _wait_unless_hurried(
+    task: asyncio.Task[Any], hurry: asyncio.Event, timeout: float
+) -> None:
+    """`wait_for(shield(task), timeout)`, except that it raises
+    `asyncio.TimeoutError` as soon as ``hurry`` is set. Never cancels
+    ``task``; its outcome is re-raised as the shield would."""
+    if not hurry.is_set():
+        waiter = asyncio.ensure_future(hurry.wait())
+        try:
+            await asyncio.wait(
+                {task, waiter}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
+            )
+        finally:
+            waiter.cancel()
+    if not task.done():
+        raise asyncio.TimeoutError
+    task.result()
+
+
+async def _cancel_until_done(
+    task: asyncio.Task[Any], *, interval: float = _HURRY_RECANCEL_S
+) -> None:
+    """Await a task already cancelled, re-cancelling it every ``interval``
+    until it ends; re-raise its outcome.
+
+    The hurried close cancels the owner before it has started to unwind, so
+    the owner may enter its transport's ``__aexit__`` with that cancellation
+    already consumed; an exit that then blocks (a dead peer) would otherwise
+    hang. The graceful path never has this problem, because it cancels an
+    owner that is already inside ``__aexit__``."""
+    while not task.done():
+        await asyncio.wait({task}, timeout=interval)
+        if not task.done():
+            task.cancel()
+    task.result()
+
+
+def _caught_cancel(exc: BaseException) -> asyncio.CancelledError | None:
+    """The cancellation a handshake handler caught, to hand to
+    `_finish_then_reraise_cancel` as ``pending``; None for an ordinary error."""
+    return exc if isinstance(exc, asyncio.CancelledError) else None
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
@@ -309,39 +491,55 @@ async def _terminate_process_tree(
 
     _signal(kill=False)
     try:
-        await asyncio.wait_for(process.wait(), timeout=5.0)
-        leader_exited = True
-    except asyncio.TimeoutError:
-        leader_exited = False
-
-    # If the leader is still alive, SIGKILL it (and, when it leads a group, the
-    # whole group). The leader exiting is NOT sufficient: a grandchild (e.g. a
-    # SIGTERM-ignoring browser) can outlive the leader inside the group and keep
-    # the profile SingletonLock — so we still escalate to a group SIGKILL below.
-    if not leader_exited:
-        _signal(kill=True)
         try:
-            await asyncio.wait_for(process.wait(), timeout=3.0)
+            await asyncio.wait_for(process.wait(), timeout=5.0)
+            leader_exited = True
         except asyncio.TimeoutError:
-            logger.warning(
-                f"[{name}] Process PID={process.pid} did not exit after SIGKILL "
-                "(possible D-state / uninterruptible I/O wait)"
-            )
+            leader_exited = False
+
+        # If the leader is still alive, SIGKILL it (and, when it leads a group, the
+        # whole group). The leader exiting is NOT sufficient: a grandchild (e.g. a
+        # SIGTERM-ignoring browser) can outlive the leader inside the group and keep
+        # the profile SingletonLock — so we still escalate to a group SIGKILL below.
+        if not leader_exited:
+            _signal(kill=True)
+            try:
+                await asyncio.wait_for(process.wait(), timeout=3.0)
+            except asyncio.TimeoutError:
+                logger.warning(
+                    f"[{name}] Process PID={process.pid} did not exit after SIGKILL "
+                    "(possible D-state / uninterruptible I/O wait)"
+                )
 
-    if _group_alive():
-        try:
-            os.killpg(group_pgid, signal.SIGKILL)  # type: ignore[arg-type]
-        except (ProcessLookupError, PermissionError, OSError):
-            pass
-        for _ in range(30):  # up to ~3s for the OS to reap the group
-            if not _group_alive():
-                break
-            await asyncio.sleep(0.1)
-        else:
-            logger.warning(
-                f"[{name}] process group {group_pgid} survived SIGKILL "
-                "(possible orphaned grandchild / D-state)"
-            )
+        if _group_alive():
+            try:
+                os.killpg(group_pgid, signal.SIGKILL)  # type: ignore[arg-type]
+            except (ProcessLookupError, PermissionError, OSError):
+                pass
+            for _ in range(30):  # up to ~3s for the OS to reap the group
+                if not _group_alive():
+                    break
+                await asyncio.sleep(0.1)
+            else:
+                logger.warning(
+                    f"[{name}] process group {group_pgid} survived SIGKILL "
+                    "(possible orphaned grandchild / D-state)"
+                )
+    except asyncio.CancelledError:
+        # Last resort, synchronous, cannot be skipped: whoever cancelled this
+        # wait -- a caller, or loop shutdown cancelling every task
+        # (`asyncio.run` -> `_cancel_all_tasks`) -- the tree is SIGKILLed
+        # before the cancellation propagates. Without this a SIGTERM-ignoring
+        # server outlived shutdown with `returncode=None`
+        # (Consiliency/pmcp#324, round 2).
+        if process.returncode is None:  # never signal a reaped (reusable) pid
+            _signal(kill=True)
+        if _group_alive():
+            try:
+                os.killpg(group_pgid, signal.SIGKILL)  # type: ignore[arg-type]
+            except (ProcessLookupError, PermissionError, OSError):
+                pass
+        raise
 
 
 # Heartbeat thresholds for health monitoring
@@ -1390,71 +1588,85 @@ class ClientManager:
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
@@ -2458,26 +2670,42 @@ class ClientManager:
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
+            # `pending`: a cancel caught here must survive a teardown that
+            # raises -- the helper re-raises it after the teardown, whatever
+            # the teardown ends with (round 2).
+            await _finish_then_reraise_cancel(
+                self._abort_stdio_handshake(name, managed, process),
+                pending=_caught_cancel(e),
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
@@ -2622,8 +2850,15 @@ class ClientManager:
                 if exc is not None:
                     raise exc
             return
+        # Inside a teardown whose caller has been cancelled, skip the graceful
+        # wait and escalate at once: a cancelled caller must not wait out the
+        # 5 s budget (Consiliency/pmcp#324, round 2).
+        hurry = _TEARDOWN_HURRY.get()
         try:
-            await asyncio.wait_for(asyncio.shield(task), timeout)
+            if hurry is None:
+                await asyncio.wait_for(asyncio.shield(task), timeout)
+            else:
+                await _wait_unless_hurried(task, hurry, timeout)
         except asyncio.TimeoutError:
             # The 5s budget bounds this graceful wait only, not the
             # escalation below: awaiting the cancelled owner is itself
@@ -2635,12 +2870,19 @@ class ClientManager:
             # failure surfacing *while* the owner unwinds under our cancel
             # must still propagate, so only the CancelledError our own
             # cancel() causes is swallowed below.
-            logger.warning(
-                f"[{name}] remote transport did not close within {timeout}s; cancelling"
-            )
+            if hurry is not None and hurry.is_set():
+                logger.info(f"[{name}] caller cancelled; closing remote transport now")
+            else:
+                logger.warning(
+                    f"[{name}] remote transport did not close within {timeout}s; "
+                    "cancelling"
+                )
             task.cancel()
             try:
-                await task
+                if hurry is not None and hurry.is_set():
+                    await _cancel_until_done(task)
+                else:
+                    await task
             except asyncio.CancelledError:
                 pass
         except asyncio.CancelledError:
@@ -2786,23 +3028,29 @@ class ClientManager:
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
+                self._abort_remote_handshake(name, managed),
+                pending=_caught_cancel(e),
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
@@ -3258,16 +3506,10 @@ class ClientManager:
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
@@ -3649,11 +3891,21 @@ class ClientManager:
         if had_prompts:
             self._catalog_events.note_prompts_changed()
 
-    async def _cleanup_client(self, name: str, managed: ManagedClient) -> None:
+    async def _cleanup_client(
+        self,
+        name: str,
+        managed: ManagedClient,
+        *,
+        pending: asyncio.CancelledError | None = None,
+    ) -> None:
         """Cancel a client's read task, kill its process, and remove it from registries.
 
-        Safe to call on any managed client regardless of state. All exceptions are
-        suppressed so callers always complete successfully.
+        Safe to call on any managed client regardless of state. A remote
+        close failure is logged and suppressed; a terminate failure propagates
+        after the registries are cleared. A cancellation of the caller --
+        one that arrives during the teardown, or one the caller already caught
+        and passes as ``pending`` -- is re-raised after the whole teardown
+        (Consiliency/pmcp#324).
 
         Cancels only *this* client's own read/stderr tasks — not every background
         task scoped to the server name. A reconnect runs its connect inside a task
@@ -3665,13 +3917,16 @@ class ClientManager:
         # `while True` writer is not a background-task sweep target on this path
         # (`_cleanup_client` deliberately does NOT call `_cancel_background_tasks`),
         # so without this it leaked one writer task per reconnect generation.
+        await _finish_then_reraise_cancel(
+            self._cleanup_client_body(name, managed), pending=pending
+        )
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
@@ -3704,7 +3959,15 @@ class ClientManager:
                     f"[{name}] Error closing remote transport: {describe_exception(e)}"
                 )
         else:
-            await _terminate_process_tree(managed.process, name)
+            try:
+                await _terminate_process_tree(managed.process, name)
+            finally:
+                # In a `finally`, as in the handshake bodies: a terminate
+                # that raises must not leave the entry behind (#324 round 2).
+                self._clients.pop(name, None)
+                self._servers.pop(name, None)
+                self._remove_server_indexes(name)
+            return
         self._clients.pop(name, None)
         self._servers.pop(name, None)
         self._remove_server_indexes(name)
@@ -3814,10 +4077,11 @@ class ClientManager:
 
             logger.info(f"Adopted {name}: {indexed} tools indexed")
 
-        except Exception as e:
+        except (Exception, asyncio.CancelledError) as e:
+            # CancelledError too, as in `_connect_stdio` (Consiliency/pmcp#324).
             status.status = ServerStatusEnum.ERROR
-            status.last_error = describe_exception(e)
-            await self._cleanup_client(name, managed)
+            status.last_error = _handshake_error(e)
+            await self._cleanup_client(name, managed, pending=_caught_cancel(e))
             raise
 
     async def call_tool(
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
    """The cancel is re-raised; the teardown error it displaces is logged by
    type, never by value, and not dropped."""
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
    assert "teardown error displaced" in caplog.text and "ValueError" in caplog.text
    assert "cleanup failed" not in caplog.text, "the displaced error's value was logged"


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


# --- round 2: a cancel caught in the handshake survives a failing teardown ----


def _adopt_task(mgr: ClientManager, gate: _Gate) -> asyncio.Task[None]:
    process = MagicMock()
    process.returncode = None
    process.stderr = None

    async def _read_stdout(name: str, managed: ManagedClient) -> None:
        await asyncio.Event().wait()

    mgr._read_stdout = _read_stdout  # type: ignore[method-assign]
    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
    return asyncio.create_task(mgr.adopt_process("srv", process, _stdio_config()))


@pytest.mark.parametrize("site", ["stdio-terminate", "remote-close", "adopt-terminate"])
async def test_a_cancel_caught_in_the_handshake_survives_a_failing_teardown(
    site: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Round-1 B1 (claude and codex): the handler caught the CancelledError
    before entering the helper, so a teardown that raised replaced it and the
    cancel was lost. `pending=` hands it to the helper."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    failing = AsyncMock(side_effect=OSError("secret-ish detail"))
    with caplog.at_level("WARNING", logger="pmcp.client.manager"):
        if site == "remote-close":
            task = await _remote_handshake_failure(mgr, None, gate, record)
            mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
            mgr._close_remote_transport = failing  # type: ignore[method-assign]
            await _cancel_while_parked(task, gate)
            kind, detail = await _outcome(task)
            for t in list(mgr._background_tasks):
                t.cancel()
            await asyncio.gather(*mgr._background_tasks, return_exceptions=True)
        else:
            with patch("pmcp.client.manager._terminate_process_tree", failing):
                if site == "stdio-terminate":
                    spawn, _term = _stdio_harness(mgr, None, gate, record)
                    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
                    with spawn:
                        task = asyncio.create_task(mgr._connect_stdio(_stdio_config()))
                        await _cancel_while_parked(task, gate)
                        kind, detail = await _outcome(task)
                else:
                    task = _adopt_task(mgr, gate)
                    await _cancel_while_parked(task, gate)
                    kind, detail = await _outcome(task)

    assert kind == "cancelled", f"{site}: the cancel was lost: {kind} {detail!r}"
    assert failing.await_count == 1
    assert "teardown error displaced" in caplog.text and "OSError" in caplog.text
    assert "secret-ish detail" not in caplog.text
    name = "remote" if site == "remote-close" else "srv"
    assert name not in mgr._clients


async def test_a_cancel_caught_in_the_handshake_is_not_retried_after_a_failing_teardown() -> (
    None
):
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, _term = _stdio_harness(mgr, None, gate, record)
    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
    attempts = 0
    real_connect = mgr._connect_server

    async def _counting(config: ResolvedServerConfig) -> None:
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise RuntimeError("second attempt started")
        await real_connect(config)

    mgr._connect_server = _counting  # type: ignore[method-assign]
    with (
        spawn,
        patch(
            "pmcp.client.manager._terminate_process_tree",
            AsyncMock(side_effect=OSError("kill failed")),
        ),
        patch.object(manager_mod, "RETRY_DELAYS", [0.0, 0.0, 0.0]),
    ):
        task = asyncio.create_task(mgr._connect_with_retry(_stdio_config()))
        await _cancel_while_parked(task, gate)
        kind, detail = await _outcome(task)

    assert (kind, attempts) == ("cancelled", 1), (
        f"{kind} {detail!r} attempts={attempts}"
    )


# --- round 2: anyio re-delivers a scope's cancel every iteration ---------------


async def test_a_cancel_scope_does_not_wake_the_caller_during_the_teardown() -> None:
    """Round-1 F2: anyio re-delivers a cancelled scope's cancel on every loop
    iteration, and the round-1 helper woke and re-awaited a fresh shield each
    time (measured ~62,500 wake-ups in 0.3 s). The gate's `cancel()` returns
    False, so the caller stays parked on ONE waiter for the whole teardown
    and the cancel is thrown in once at the end."""
    import anyio

    helper = _helper()
    release = asyncio.Event()
    holder: dict[str, Any] = {}

    async def cleanup() -> str:
        await release.wait()
        return "done"

    async def caller() -> bool:
        with anyio.CancelScope() as scope:
            holder["scope"] = scope
            await helper(cleanup())
        return scope.cancelled_caught

    task = asyncio.create_task(caller())
    while "scope" not in holder or task._fut_waiter is None:  # type: ignore[attr-defined]
        await asyncio.sleep(0)
    holder["scope"].cancel()
    waiters = []
    for _ in range(200):  # 200 loop iterations: 200 anyio re-deliveries
        await asyncio.sleep(0)
        waiters.append(task._fut_waiter)  # type: ignore[attr-defined]
    release.set()
    kind, value = await _outcome(task)

    assert (kind, value) == ("returned", True)
    assert all(w is waiters[0] for w in waiters), (
        f"the caller woke during the teardown: {len({id(w) for w in waiters})} waiters"
    )


# --- round 2: a cancelled caller does not wait out the graceful remote close --


class _HangingExit:
    """A transport whose `__aexit__` never returns unless cancelled."""

    def __init__(self) -> None:
        self.exit_entered = asyncio.Event()
        self.exit_cancelled = False
        self.release = asyncio.Event()  # test cleanup only, never the code

    async def __aenter__(self) -> tuple[Any, Any]:
        return (MagicMock(), MagicMock())

    async def __aexit__(self, *exc_info: Any) -> None:
        self.exit_entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.exit_cancelled = True
            raise


async def _release_owner(mgr: ClientManager, transport: _HangingExit) -> None:
    """Let a hung owner finish, so a failing run (main, or a mutant) cannot
    hang the loop's own teardown after the test."""
    transport.release.set()
    for t in list(mgr._background_tasks):
        t.cancel()
    await asyncio.wait_for(
        asyncio.gather(*mgr._background_tasks, return_exceptions=True), _HANG_GUARD_S
    )


def _no_graceful_budget(mgr: ClientManager) -> None:
    """Make the graceful phase effectively unbounded, so a run that waits it
    out hits the hang guard instead of passing slowly."""
    real = mgr._close_remote_transport

    async def _close(name: str, managed: ManagedClient, timeout: float = 5.0) -> None:
        await real(name, managed, 3600.0)

    mgr._close_remote_transport = _close  # type: ignore[method-assign]


async def test_a_cancelled_disconnect_skips_the_graceful_remote_close() -> None:
    mgr = ClientManager()
    transport = _HangingExit()

    async def _read_sse(name: str, managed: ManagedClient, read_stream: Any) -> None:
        await asyncio.Event().wait()

    mgr._read_sse = _read_sse  # type: ignore[method-assign]
    mgr._send_initialize = AsyncMock()  # type: ignore[method-assign]
    mgr._index_capabilities = AsyncMock(return_value=(0, 0, 0))  # type: ignore[method-assign]
    _no_graceful_budget(mgr)
    await asyncio.wait_for(
        mgr._connect_remote_stream(_remote_config(), transport, transport_name="t"),
        _HANG_GUARD_S,
    )
    task = asyncio.create_task(mgr.disconnect_server("remote", force=True))
    try:
        await asyncio.wait_for(transport.exit_entered.wait(), _HANG_GUARD_S)
        task.cancel()
        kind, detail = await _outcome(task)
    finally:
        await _release_owner(mgr, transport)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert transport.exit_cancelled, "the owner was not escalated"
    assert "remote" not in mgr._clients


async def test_a_cancel_caught_in_the_remote_handshake_skips_the_graceful_close() -> (
    None
):
    mgr = ClientManager()
    gate = _Gate()
    transport = _HangingExit()

    async def _read_sse(name: str, managed: ManagedClient, read_stream: Any) -> None:
        await asyncio.Event().wait()

    mgr._read_sse = _read_sse  # type: ignore[method-assign]
    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
    _no_graceful_budget(mgr)
    task = asyncio.create_task(
        mgr._connect_remote_stream(_remote_config(), transport, transport_name="t")
    )
    try:
        await _cancel_while_parked(task, gate)
        kind, detail = await _outcome(task)
    finally:
        await _release_owner(mgr, transport)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert transport.exit_cancelled
    assert "remote" not in mgr._clients


# --- round 2: loop shutdown still kills the process tree -----------------------

_SHUTDOWN_SCRIPT = r"""
import asyncio, os, sys
from pmcp.client import manager as m
from pmcp.client.manager import ClientManager
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

pidfile = sys.argv[1]
child = (
    "import os, signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "g = subprocess.Popen(['sleep', '300'])\n"
    "open(sys.argv[1], 'w').write(f'{os.getpid()} {g.pid}')\n"
    "time.sleep(300)\n"
)

async def main():
    parked = asyncio.Event()
    real = m._terminate_process_tree

    async def term(process, name):
        parked.set()
        await real(process, name)

    m._terminate_process_tree = term
    mgr = ClientManager()

    async def handshake(managed):
        while not (os.path.exists(pidfile) and open(pidfile).read().strip()):
            await asyncio.sleep(0.01)
        raise RuntimeError("handshake failed")

    mgr._send_initialize = handshake
    cfg = ResolvedServerConfig(
        name="srv", source="project",
        config=LocalMcpServerConfig(command=sys.executable, args=["-c", child, pidfile]),
    )
    asyncio.ensure_future(mgr._connect_stdio(cfg))
    await asyncio.wait_for(parked.wait(), 20)
    for _ in range(3):
        await asyncio.sleep(0)  # SIGTERM sent (ignored); terminate parked on its wait
    # main returns: asyncio.run cancels every task still pending, the teardown too

asyncio.run(main())
print("shutdown-complete", flush=True)
"""


def test_loop_shutdown_mid_terminate_still_kills_the_process_tree(
    tmp_path: Path,
) -> None:
    """Round-1 B2 (codex): `asyncio.run`'s shutdown cancels every task, the
    private teardown task included, while `_terminate_process_tree` waits on a
    SIGTERM-ignoring server. On main and on the round-1 patch the tree
    outlived the loop (`returncode=None`). The cancellation now SIGKILLs the
    tree synchronously before it propagates."""
    import os
    import signal
    import subprocess

    from tests._timing import eventually_sync

    pidfile = tmp_path / "pids"
    result = subprocess.run(
        [sys.executable, "-c", _SHUTDOWN_SCRIPT, str(pidfile)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "shutdown-complete" in result.stdout, result.stderr[-2000:]
    leader, grandchild = (int(x) for x in pidfile.read_text().split())

    def gone(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    try:
        eventually_sync(
            lambda: gone(leader) and gone(grandchild),
            timeout=5.0,
            interval=0.05,
            message=f"process tree outlived shutdown: leader={leader} grandchild={grandchild}",
        )
    finally:
        for pid in (leader, grandchild):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


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
    ("client/manager.py", "_terminate_process_tree", "reraises"): (
        1,
        "the tree's own cancel: SIGKILLs synchronously, then re-raises (round 2)",
    ),
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


def _calls_by_innermost_function(
    tree: ast.Module,
) -> list[tuple[str, ast.Call, ast.AST]]:
    """(innermost enclosing function name, call, the call's parent node) for
    every call: a call in a nested function belongs to the nested function,
    not to the one around it."""
    out: list[tuple[str, ast.Call, ast.AST]] = []

    def visit(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else fn
            )
            if isinstance(child, ast.Call):
                out.append((fn, child, node))
            visit(child, inner)

    visit(tree, "<module>")
    return out


def test_absorbing_teardown_only_runs_inside_the_private_task() -> None:
    """Every call site, checked on its own: `_reap_cancelled_child` and
    `_teardown_outbound` are called only from the scoped bodies, and EVERY
    call of a scoped body is itself an argument of
    `_finish_then_reraise_cancel(...)` -- one wrapped call elsewhere does not
    excuse a bare one."""
    tree = dict(_modules())["client/manager.py"]
    misplaced: list[str] = []
    for fn, call, parent in _calls_by_innermost_function(tree):
        name = _callee(call)
        if name == "_reap_cancelled_child" and fn not in _REAP_CALLERS:
            misplaced.append(f"{fn} calls _reap_cancelled_child")
        if name == "_teardown_outbound" and fn not in _SCOPED_BODIES:
            misplaced.append(f"{fn} calls _teardown_outbound")
        if name in _SCOPED_BODIES and not (
            isinstance(parent, ast.Call)
            and _callee(parent) == "_finish_then_reraise_cancel"
            and call in parent.args
        ):
            misplaced.append(f"{fn} calls {name} outside the helper")
    wrapped = {
        _callee(call)
        for _fn, call, parent in _calls_by_innermost_function(tree)
        if isinstance(parent, ast.Call)
        and _callee(parent) == "_finish_then_reraise_cancel"
    }
    assert misplaced == [], misplaced
    assert wrapped >= _SCOPED_BODIES, f"never wrapped: {_SCOPED_BODIES - wrapped}"


def test_the_structural_check_sees_a_bare_call_beside_a_wrapped_one() -> None:
    """The round-1 check let one wrapped call excuse a bare one elsewhere,
    and charged a nested function's calls to its parent."""
    source = (
        "class C:\n"
        "    async def a(self):\n"
        "        await _finish_then_reraise_cancel(self._cleanup_client_body(1, 2))\n"
        "    async def b(self):\n"
        "        task = self._cleanup_client_body(1, 2)\n"
        "    async def _abort_stdio_handshake(self):\n"
        "        async def nested():\n"
        "            await _reap_cancelled_child(None)\n"
    )
    calls = _calls_by_innermost_function(ast.parse(source))
    by_fn = {(fn, _callee(c)) for fn, c, _p in calls}
    assert ("nested", "_reap_cancelled_child") in by_fn
    assert ("_abort_stdio_handshake", "_reap_cancelled_child") not in by_fn
    bare = [
        fn
        for fn, c, parent in calls
        if _callee(c) == "_cleanup_client_body"
        and not (
            isinstance(parent, ast.Call)
            and _callee(parent) == "_finish_then_reraise_cancel"
        )
    ]
    assert bare == ["b"]
````
