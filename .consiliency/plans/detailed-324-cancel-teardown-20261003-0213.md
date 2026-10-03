# Detailed plan: a cancelled caller keeps its cancellation through every teardown, and the teardown still completes

> Written on main `89559db` (dev0, a team host), worktree `pmcp-324`, branch
> `plan/324-cancel-teardown`; since round 6 re-measured on main `c9206a9`
> (merged into the branch; it includes Consiliency/pmcp#337,
> Consiliency/pmcp#339 and Consiliency/pmcp#340). Every number below was measured on that tree, or
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
>
> **Round 3** (after the round-2 panel): codex found two more blocking
> defects in the round-2 machinery -- loop shutdown could cancel the private
> teardown task before its first instruction (process alive, `_clients`
> kept), and a cancel after the graceful-close escalation had begun could hang
> `disconnect_server` while it held the lifecycle lock. That was the third
> round of new edge cases in the same machinery (private task, deferral gate,
> hurry ContextVar, re-cancel loop), so round 3 **removes the machinery**
> instead of patching it (Design decision 1). All numbers below are
> re-measured on the round-3 spike on 3.10, 3.11 and 3.12.
>
> **Round 4** (after the round-3 panel: claude and gemini AGREE; codex found
> two places the synchronous pattern was not yet applied): a
> `disconnect_all()` cancelled before its per-server workers start now kills
> every tree from a parent-level fallback (Design decision 7), and the remote
> pre-handoff path abandons the owner through `_abandon_owner` like every
> other owner cancel, which a structural test now enforces (Design decision
> 6). Claude's round-3 notes are taken: the `SingletonLock` sentence is
> corrected (Design decision 1), the census is restated, and the two nits are
> addressed. All numbers below are re-measured on the round-4 spike on 3.10,
> 3.11 and 3.12.
>
> **Round 5** (after the round-4 panel: claude F1, blocking): the
> round-4 fallback abandoned clients still marked ONLINE, so each cancelled
> reader's `finally` scheduled an auto-reconnect -- after the fallback's
> sweep and after `_reconnect_tasks` was cleared -- and a `disconnect_all`
> (or `refresh`) cancelled at 0 or 1 loop steps respawned every server ~5 s
> later with its old config. The plan's test missed it because its clients
> had no reader tasks. `_abandon_client_io` now marks an ONLINE client
> OFFLINE before it cancels anything, which covers every synchronous abandon
> path at one spot (Design decision 7), and a new test with real readers and
> real processes cancels at 0, 1 and 2 steps and waits past the reconnect
> delay. Consiliency/pmcp#336 is *not* closed by this (Design decision 10,
> measured). All numbers below are re-measured on the round-5 spike on main
> `160a9e8`, on 3.10, 3.11 and 3.12.
>
> **Round 6** (after the round-5 panel: claude and gemini AGREE; grok and
> codex one blocker each). Grok: `asyncio.wait_for` on 3.10/3.11 returns
> the result instead of raising when the awaited work finishes in the same
> turn a cancel of the caller lands, so a cancelled teardown "finished" and
> `restart_server`/`refresh` spawned replacements and the update probe
> returned its result; every `wait_for` in `src/pmcp` (16 sites) is
> replaced by `pmcp.waits.bounded_wait`, built on `asyncio.wait`, and a
> structural test bans `wait_for` in `src/pmcp` with no exemptions (Design
> decision 11). Codex: once asyncio has reaped a leader, no kill path could
> find its group, so a grandchild that outlived the leader survived every
> teardown (pre-existing); the group id is now retained when the client is
> created and used, with a reuse check, by every kill path (Design decision
> 4). Claude's note: Verification keeps pytest's basetemp outside the
> checkout and off `/tmp`. All numbers below are re-measured on the round-6
> spike on main `c9206a9`, on 3.10, 3.11 and 3.12.

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

### 1. On a caller cancel, finish synchronously; otherwise, unchanged (round 3)

Rounds 1 and 2 kept the whole graceful teardown running for a cancelled
caller, inside a private task the caller waited on. Each round, a reviewer
found a new way that machinery failed: a cancel caught before the task
started (round 1, B1); shutdown cancelling the task (round 1, B2); the anyio
spin (round 1, F2); shutdown cancelling the task **before its first
instruction**, and an escalation that ignored a later cancel and hung
`disconnect_server` with the lifecycle lock held (round 2, codex). The common
cause is that the teardown awaited things *after* the caller asked to stop,
and every such await is a new place to be cancelled, to hang, or to hold a
lock. Round 3 changes the design so that state cannot exist:

- **Child waits never surface the child's outcome.** `_reap_child(task, *,
  timeout=None)` cancels the child and waits through `asyncio.wait({task},
  timeout=...)`, which never raises the child's result or its
  `CancelledError`. So a `CancelledError` out of *any* teardown await is the
  caller's own -- the ambiguity that started this issue is gone without a
  private task -- and nothing in a teardown catches a `CancelledError` to
  absorb it.
- **A caller cancel switches the teardown to a synchronous path.** Every
  teardown entry point has an `except asyncio.CancelledError:` that calls
  `ClientManager._abandon_client_io(name, managed)` and the site's own
  synchronous registry step, then `raise`s. `_abandon_client_io` contains no
  `await`:
  - cancel the reader, stderr and outbound-writer tasks **without awaiting
    them** (`_cancel_without_waiting`; a done-callback retrieves each outcome
    and logs a genuine failure by type);
  - stdio: `_kill_process_tree_now(process)` -- SIGKILL the leader and its
    group (Design decision 4); no SIGTERM grace for a cancelled caller;
  - remote: `_abandon_owner(owner, shutdown)` -- set the owner's shutdown
    event and cancel the owner from **loop callbacks**, never awaiting it:
    first on the next iteration, then every 0.5 s (`_OWNER_RECANCEL_S`) until
    it ends. The deferral is measured, not decorative: a cancel issued at
    once is consumed while the owner is still parked on `shutdown.wait()`,
    and the transport's `__aexit__` then runs uncancelled -- against a dead
    peer it blocks for good (`test_remote_handshake_teardown_keeps_caller_cancel`
    failed with an immediate cancel; mutant M21). Callbacks are not a task,
    so nothing in this can be cancelled before it runs or hold the caller.
    The graceful close is abandoned, so a streamable-HTTP session-ending
    `DELETE` may not be sent; the server reaps the session on its own timeout
    (round-2 N4 accepted that). An owner is never cancelled any other way on a
cancel path (Design decision 6);
  - a failure in any step is caught and logged **by type only**, so the
    caller's cancellation is always what propagates (round-1 B1, now by
    construction).
- **With nothing to await after the cancel**, nothing can be cancelled before
  it runs (round-2 codex B1), nothing can hang (round-2 codex B2), no lock is
  held across a suspension, there is no linger to bound (round-1 F3), and
  nothing spins under anyio (round-1 F2).
- **The non-cancelled path keeps today's graceful behaviour**: SIGTERM grace
  then SIGKILL, a graceful remote close then escalation, and a child's own
  cancellation absorbed. `test_child_cancellation_is_still_absorbed_*` pins
  that (`record == ["terminated"]`: the graceful path still SIGTERMs first).

**Removed** relative to round 2 (`_abandon_owner`'s re-cancel is the one
idea kept from round 2's `_cancel_until_done`, minus the waiting):
`_finish_then_reraise_cancel`,
`_DeferredCancelGate`, `_TEARDOWN_HURRY`, `_wait_unless_hurried`,
`_cancel_until_done`, `_HURRY_RECANCEL_S`, `_caught_cancel` and the `pending=`
plumbing, the four `_abort_*`/`_finish_disconnect`/`_cleanup_client_body`
private-task bodies' wrapping. With them go round-2's anyio residual (F2) and
claude's round-2 N1 (3.13's `uncancel()` withdrawing a deferred cancel -- no
cancel is deferred now) and N3 (the hurry ContextVar inherited by catalog
drain tasks).

**Why not keep a private task and fix codex's two cases:** each fix would add
a rule to machinery that has failed three times, and the round-2 B1 case (a
task cancelled before its first instruction) has no fix inside the task --
only a synchronous path in the cancelled caller can run after it. That path
is the design.

**What a cancelled caller gives up**, stated: the SIGTERM grace -- a stdio
server is SIGKILLed, also mid-grace when the cancel lands during
`_terminate_process_tree` (the round-3 claude seat measured `flushed=False`
for a server that needs 1 s to exit, against `flushed=True` on main). For a
browser server (Playwright, browser-use) that means an unflushed profile --
recent cookie and session writes in a persistent context are lost -- and a
"didn't shut down correctly" restore state on the next launch. It does
**not** leave a stuck lock: Chrome's `SingletonLock` is a symlink a SIGKILL
leaves on disk, which ProcessSingleton treats as stale (dead pid, same host)
and takes over on the next launch (round-1 of this plan said "no
`SingletonLock` survives", which was wrong; this is from Chrome's documented
behaviour, not measured -- there is no Chrome on dev0). Keeping the grace
(SIGTERM now plus a scheduled SIGKILL) would reopen the shutdown leak of
round-1 B2 whenever the loop closes inside the grace window, so the trade is
kept. Also given up: the remote session `DELETE`, and waiting for the child
tasks to end (they end on their own; each is cancel-responsive, which the
census keeps true).

### 2. Where the synchronous path is, and the census

| Teardown entry point | On a caller cancel |
|---|---|
| `_connect_stdio`: cancel *during* the handshake | `_abandon_client_io` + `_drop_client`, `last_error = "Connection cancelled"`, raise |
| `_connect_stdio`: cancel during the graceful teardown of a *failed* handshake (`_abort_stdio_handshake`) | `_abandon_client_io`, raise; `_drop_client` in the `finally` |
| `_connect_remote_stream`: the same two handlers | same, with the owner abandoned |
| `_connect_remote_stream`: pre-handoff `except BaseException` | `_abandon_owner(owner_task, shutdown)`, raise (round 4; was `await gather(owner)` on main, a single cancel in round 3) |
| `adopt_process`: cancel during the handshake | `_abandon_client_io` + `_forget_client`, raise |
| `disconnect_server` | `_abandon_client_io`, `_cancel_background_tasks_now`, `_forget_disconnected`, raise |
| `_cleanup_client` | `_abandon_client_io`, raise; `_forget_client` in the `finally` |
| `_disconnect_all_unlocked._shutdown_one` | `_abandon_client_io`, raise (the gather re-raises) |
| `_disconnect_all_unlocked` itself (round 4) | for **every** registered client `_abandon_client_io`, then `_cancel_background_tasks_now`, raise; the registry clears run in a `finally` |
| `_close_remote_transport` | cancel the owner without waiting (a done-callback logs a genuine unwind failure, sanitised, as before), raise |
| `_terminate_process_tree` | `_kill_process_tree_now(process, group_pgid=...)`, raise |
| `tools/handlers.py` `_run_update_probe_command` | `_kill_process_tree_now(process)`, raise (was `await _terminate_process_tree`) |

The census of `CancelledError`/`BaseException` handlers in `src/pmcp`: **23**
on main (round 1 said 22: the last row covers four sync sites). On the
round-4 patch: **25** (round 3 had 24, as the round-3 claude seat
counted; round 4 adds the `disconnect_all` fallback). Round 6: **26** --
`pmcp.waits.bounded_wait`'s handler (cancels the work without waiting,
re-raises). Every one in `manager.py` re-raises except the health monitor's
task root; no handler absorbs a caller's cancel. Three
class tests pin it:
- `test_every_cancellation_handler_is_classified`: every handler, with its
  count, matches a reasoned allowlist;
- `test_no_cancellation_handler_awaits`: no handler that catches a
  cancellation contains an `await`, except one task root
  (`installer.JobManager._monitor_install`, whose own cancel ends it) -- the
  class rule that makes round 2's three defects unrepresentable;
- `test_every_teardown_abandons_synchronously_on_cancel`: each entry point in
  the table has a `CancelledError` handler that calls `_abandon_client_io`
  (or `_kill_process_tree_now`) and ends in `raise`;
- `test_abandon_is_synchronous_and_child_waits_never_absorb`:
  `_abandon_client_io` and `_kill_process_tree_now` are plain functions, and
  `_reap_child` uses `asyncio.wait`, never `shield`, and catches no
  cancellation.

### 3. A cancel *during* the handshake gets the synchronous teardown

Unchanged in intent from rounds 1–2, simpler in form: the three handshake
handlers catch `asyncio.CancelledError` separately and run the synchronous
path; `last_error` becomes `"Connection cancelled"` (`_handshake_error`;
`describe_exception(CancelledError())` is `''`). On main this path ran no
teardown at all (process, read task and a `CONNECTING` entry leaked).

### 4. `_kill_process_tree_now`: the one uninterruptible step

**Round 6 (codex): the group is retained, so a reaped leader's grandchild
still dies.** `_kill_process_tree_now` discovered the group only while
`process.returncode is None`, and `_terminate_process_tree` returned early
once it was set; after asyncio reaped the leader, no kill path reached the
group. Codex's repro on 3.10–3.12: a session leader starts `sleep 300`
(inheriting its pipes) and exits 0; a cancelled `disconnect_all` or a
`disconnect_server` leaves the grandchild alive (pre-existing on main).
**Decision:** read the group when the client is created --
`ManagedClient.__post_init__` sets `group_pgid = _own_group_pgid(process)`,
which is the pid when `os.getpgid(pid) == pid` (stdio servers are spawned
with `start_new_session=True`: the child calls `setsid()` before `exec`, so
pgid == pid; confirmed in the test, and `getpgid` still answers for an
unreaped zombie) and `None` otherwise (an adopted process that does not
lead a group, so the gateway's own group is never kept). The update probe
reads it right after its spawn. Every kill path takes it:
`_abandon_client_io` → `_kill_process_tree_now(process,
group_pgid=managed.group_pgid)`; `disconnect_server`, `_shutdown_one`,
`_cleanup_client` and the stdio handshake abort →
`_terminate_process_tree(process, name, group_pgid=...)`; the probe's
cancel and timeout paths. Once the leader is reaped, the leader's pid is
**never** signalled; the group is signalled only through
`_kill_retained_group(pgid)`:
1. `os.killpg(pgid, 0)` must succeed -- the group still has a member;
2. `os.kill(pgid, 0)` must fail with `ProcessLookupError` -- no live process
   holds the leader's pid.

*Why that check, and the window it leaves.* While any member of our group
lives, the kernel keeps the id in use (a `struct pid` used as a pgid is not
freed), so it cannot be handed to a new process. A live process with the
leader's pid therefore means the id was reused after our group emptied, and
we send nothing (`test_a_reused_group_id_is_not_signalled`). A `/proc`
scan for members with that pgid was considered and rejected: it is
Linux-only and cannot tell our orphans from a reused group's members any
better than the check above. **Residual:** the id is reused by a new
session leader that then exits while its own children live, all between
our reap and the kill -- the pid space must cycle (`pid_max`, 4 194 304 by
default on 64-bit Linux) within that window, which is sub-second for a
teardown.

A plain function. It SIGKILLs the leader **only while `process.returncode is
None`** (round-2 N5: once asyncio has reaped the leader its pid may be
reused), and the process group when the leader leads it (`os.getpgid(pid) ==
pid`, read while the leader is unreaped) or when `_terminate_process_tree`
cached `group_pgid` earlier. Each signal is guarded against
`ProcessLookupError`/`PermissionError`/`OSError`.

**The remaining PID-reuse window (N5), stated:** asyncio learns of the
leader's exit from its child watcher, which reaps with `waitpid` and then
sets `returncode` in a loop callback. Between the two the pid is free while
`returncode` is still `None`, so a kill in that window could hit a reused pid
-- only if the kernel wraps the pid space in that instant. The same window
exists for every `process.kill()`/`terminate()` in pmcp and on main; this
plan neither opens nor closes it. The group kill has the analogous window for
a group whose last member exits at that instant.

### 5. `disconnect_server` and the lifecycle lock

The cancel path runs entirely inside `async with self._lifecycle_lock:` and
contains no `await`, so the lock is released by the `async with` exit, which
for `asyncio.Lock` is `self.release()` -- synchronous (`asyncio/locks.py`,
`__aexit__`). Measured: `assert not mgr._lifecycle_lock.locked()` after a
cancel at every disconnect point, and after codex's hung-escalation repro.
The background-task sweep (`_cancel_background_tasks`) now waits with
`asyncio.wait`, not `gather`: a cancelled `gather` still waits for every
child to finish, so a swept task that ignores cancellation held a cancelled
caller (new `[sweep]` point; mutant M12). Its synchronous twin
`_cancel_background_tasks_now` serves the cancel path. Because there is no
private task, `current_task()` is the caller again, and the round-1/2
`exclude={caller}` parameter is gone; `test_disconnect_server_does_not_cancel_its_own_caller`
stays.

### 6. `_close_remote_transport`: unambiguous waits, no wait after a cancel; every owner cancel goes through `_abandon_owner`

The graceful wait and the escalation wait both use `asyncio.wait({owner})`
(was `wait_for(shield(owner))` and `await owner`), so a `CancelledError` there
is the caller's. On it: `_abandon_owner` (signal, then cancel from loop
callbacks until it ends) with a done-callback that logs a genuine unwind
failure (the sanitised traceback the method has always logged), and
re-raise -- never wait for the owner. This is
codex's round-2 B2: an owner whose `__aexit__` blocks in a `finally` can no
longer hold the caller or the lifecycle lock.

**Round 4:** the remote pre-handoff handler in `_connect_remote_stream` used
a single immediate cancel, the failure M21 pins. Codex's repro: the caller is
cancelled while the transport is being entered (`call_soon(connect.cancel)`
inside `__aenter__`); the owner publishes `ready` and parks on
`shutdown.wait()`, the one cancel is consumed there, and its `__aexit__` then
blocks uncancelled -- owner pending, HTTP client unclosed, on 3.10–3.12. It
now calls `_abandon_owner(owner_task, shutdown)`. `_abandon_owner` cancels at
once *and* re-cancels from loop callbacks (next iteration, then every 0.5 s)
until the owner ends; the immediate cancel ends an owner that is still
entering its transport without waiting a loop iteration.
`test_an_owner_is_cancelled_only_through_abandon_owner` pins that no other
cancel of an owner exists in `manager.py`. On the non-cancelled path the
behaviour is as on main: graceful wait, then one cancel and an unbounded wait
for the owner (the documented pre-existing hang class for an `__aexit__` that
ignores cancellation), and a genuine unwind failure propagates.

### 7. Fan-outs: `disconnect_all` has a parent-level fallback (round 4)

`_shutdown_one` abandons and re-raises like every other teardown, so when
`server.py:1017`'s 10 s `wait_for(disconnect_all(), ...)` runs out, every tree
is SIGKILLed at once. **But a per-worker handler only runs once its worker has
started.** Codex's round-3 repro: `task = create_task(mgr.disconnect_all())`
then `loop.call_soon(task.cancel)` -- the gather's workers are cancelled
before their first instruction, no handler runs, and on round 3 (and main)
the subprocess stayed alive with no kill invoked. **Decision:** the parent,
`_disconnect_all_unlocked`, wraps its gather and sweep in `except
asyncio.CancelledError:` that, synchronously and whether or not the workers
ran, calls `_abandon_client_io` for every client it snapshotted (repeating it
for a worker that did run is harmless: a done task is not re-cancelled, a
second SIGKILL of a not-yet-reaped pid hits the same process, a second owner
kick chain stops when the owner ends), then `_cancel_background_tasks_now`,
then re-raises; the old registry clears move into a `finally` of the same
method, so they run on the cancelled path too. They stay inline there:
`tests/runtime/test_publisher_coverage.py`'s AST guard pins catalog writes to
`_disconnect_all_unlocked` (the full suite caught a first version that
factored them into a helper).
Measured with real processes: two SIGTERM-ignoring servers, cancel before the
workers start -> both `returncode == -SIGKILL`, `_clients == {}`, lock free;
on round 3 the test fails.

**Round 5: an abandoned client must not look ONLINE to its reader (claude
F1).** `_read_stdout`'s and `_read_sse`'s `finally` schedules an
auto-reconnect when it still sees its client ONLINE. On the normal path
`_shutdown_one` and `disconnect_server` set OFFLINE before they reap, but the
fallback runs for workers that never ran, so the readers it cancelled saw
ONLINE and scheduled `reconnect-<name>` one loop step later -- after
`_cancel_background_tasks_now()` and after the `finally` had cleared
`_reconnect_tasks`, so nothing cancelled them. Claude measured both servers
back ONLINE at 5.01–5.03 s for k = 0 and 1 on 3.10 and 3.12, and 12
orphaned `sleep 300` grandchildren in a first sweep. **Decision:** one spot,
not one line per caller. `_abandon_client_io` -- which every synchronous
abandon path calls (the stdio, remote and adopt handshakes, `_cleanup_client`,
`disconnect_server`, `_shutdown_one`, the `disconnect_all` fallback) -- first
sets an ONLINE client's status to OFFLINE, then cancels the reader, stderr
and writer tasks. A status that is already CONNECTING, ERROR or OFFLINE is
left as it is, so a failed handshake keeps its `last_error`. The awaited
graceful paths that reap a reader (`disconnect_server`, `_shutdown_one`) set
OFFLINE themselves before they reap, as on main.
`test_a_cancelled_disconnect_all_does_not_respawn_its_servers` connects two
real SIGTERM-ignoring servers with grandchildren through `_connect_stdio`
(real reader tasks), cancels `disconnect_all` at 0, 1 and 2 loop steps, and
asserts that no `reconnect-*` task or `_reconnect_tasks` entry exists once
the readers have run, that nothing is spawned within 5.5 s (past the first
reconnect delay, 5 s), and that every leader and grandchild is dead. On the
round-4 spike it fails at k = 0 with `reconnect-s0` and `reconnect-s1`
pending (mutant M26).

**The other fan-outs, checked:**
- `refresh()` and server shutdown go through `_disconnect_all_unlocked`:
  covered.
- `_connect_all_unlocked` gathers connect workers. A worker cancelled before
  its first instruction has spawned nothing; one cancelled at any later
  await runs its own handler, and a cancel inside `create_subprocess_exec`
  is handled by asyncio itself (it closes and waits the half-made
  transport). So it needs no parent kill -- and must not have one, since a
  cancelled `connect_all` keeps the servers that did connect.
  `test_connect_all_cancelled_before_its_workers_start_spawns_nothing`
  pins it with a real command (no process spawned, `_clients` empty).
- `_fetch_server_listings`' gather and the reconcile tasks own no process or
  transport; the background sweep is `_cancel_background_tasks`, already
  `asyncio.wait` plus a synchronous twin.

### 8. Not lost, not doubled, and `asyncio.timeout()` still works

Every handler re-raises the `CancelledError` it caught; nothing calls
`cancel()` or `uncancel()` on the caller. Pinned on 3.10–3.12 by
`test_one_caller_cancel_is_delivered_exactly_once` (two requests in one
iteration, one `CancelledError`, then `await asyncio.sleep(0)` succeeds; on
3.11+ `cancelling() == 2`, both requests counted), and on 3.11+ by
`test_a_timeout_scope_around_the_teardown_still_reports_timeout` and
`test_a_taskgroup_sibling_failure_kills_the_tree`
(`ExceptionGroup([ValueError])`, tree killed, client dropped).

### 9. Loop shutdown in every phase, with real processes

`test_loop_shutdown_kills_the_process_tree[phase]` runs `asyncio.run` around
a real `_connect_stdio` whose server ignores SIGTERM and has a grandchild:

| Phase | main | round-2 spike | round 3 |
|---|---|---|---|
| `mid-handshake` (main returns while the handshake is pending) | tree **alive**, `clients=['srv']`, 0.02 s | dead, but **5.03 s** (the private task ran the full SIGTERM grace; claude's N2) and `terminate_entered=True` | dead, **0.02 s**, `terminate_entered=False` |
| `just-failed` (codex round 2: the handshake fails, then the loop is stopped at once) | tree **alive** | tree **alive** (the private task was cancelled before it ran) | dead, `clients=[]` |
| `mid-terminate` (codex round 1) | tree **alive**, `clients=['srv']` | dead | dead, 0.02 s |

(Elapsed times are `asyncio.run`'s, measured on 3.10 by the same script the
test runs; the test asserts the deterministic facts -- tree dead, `clients=[]`
and, mid-handshake, that the graceful terminate was never entered -- not the
times.) `test_a_cancelled_terminate_kills_a_sigterm_ignoring_tree` covers
`_terminate_process_tree`'s own cancel path for callers that do not abandon
themselves (`returncode == -SIGKILL`).

### 10. Auto-reconnect after a cancelled reconnect: pre-existing, not fixed here

Filed as Consiliency/pmcp#336 (round-1 F4). **Round 5: the F1 fix does not
close it, measured.** #336's case is a reconnect of an ONLINE server whose
caller is cancelled: `_connect_stdio`'s pre-clean calls `_cleanup_client`,
whose *graceful* `_cleanup_client_io` reaps the old reader while the client
is still ONLINE -- before any cancel arrives -- so the reader's `finally`
schedules a reconnect, and once the cancelled caller is gone nothing stops
it. The synchronous abandon (which now marks OFFLINE) runs only afterwards.
Measured on the round-5 spike with a real server: a second `_connect_stdio`
for an ONLINE `real`, cancelled at `call_soon`, 50 ms and 300 ms, leaves
`_reconnect_tasks == ['real']` in all three cases. Closing it means marking
the old client OFFLINE (or flagging the teardown) in the graceful reap too,
on every reconnect path (stdio, remote, adopt), and proving a genuine
downstream disconnect still reconnects -- #336's own scope. This plan fixes
the teardown paths only: every synchronous abandon, which is the class F1
belongs to.

### 11. No `asyncio.wait_for` in `src/pmcp` (round 6, grok)

On 3.10 and 3.11, `asyncio.wait_for`'s `except CancelledError` ends with
`if fut.done(): return fut.result()`: when the awaited work completes in
the same turn as a cancel of the *caller* lands, the cancel is dropped and
the result returned; on 3.11 the task's `cancelling()` is left at 1, so a
later `asyncio.timeout()` reports a cancellation instead of a timeout.
Grok's repro, a real child killed and the caller cancelled from the exit
waiter's done callback: 10/10 swallowed on 3.10 and 3.11; 3.12 keeps the
cancel. In this plan's code that meant `_terminate_process_tree`'s waits
returned normally, so `restart_server` (`disconnect_server` then
`connect_server`) and `refresh` (`_disconnect_all_unlocked` then
`_connect_all_unlocked`) spawned replacements after being cancelled, and
the update probe's `wait_for(process.communicate(), 60)` returned the
probe result and skipped `_kill_process_tree_now`. The round-5 tests
missed it because they cancel while the process is alive or park in a
mocked terminate.

**Decision: fix the class.** One helper, `pmcp.waits.bounded_wait(aw,
timeout)`, replaces every `wait_for` in `src/pmcp`. It waits with
`asyncio.wait({task}, timeout=...)`, which never raises the work's outcome,
so a `CancelledError` out of it can only be the caller's, and it always
propagates -- also when the work finished in that same turn (the task's
`cancelling()` is 1, consumed by whatever scope requested it). On the
caller's cancel it cancels the work **without waiting** (the class rule:
no handler that catches a cancellation awaits) and re-raises. On timeout it
behaves as `wait_for`: cancel the work, wait for it to end, raise
`asyncio.TimeoutError` (the 3.10 class, so every existing `except
asyncio.TimeoutError` still fires), or return the result if the work
finished anyway. The 16 sites, classified:

| Site | Waits on | Caller-cancellable by |
|---|---|---|
| `client/manager.py` `_terminate_process_tree` (×2) | `process.wait()`, 5 s and 3 s | every teardown's caller (restart, refresh, shutdown, a tool call) |
| `client/manager.py` `_read_stderr` | `stderr.readline()`, 120 s | the teardown that cancels the reader |
| `client/manager.py` idle wait (`wait_for(shield(future), slice)`) | a pending request | the tool call's caller |
| `tools/handlers.py` `_run_update_probe_command` | `communicate()`, 60 s | the `update_server` call |
| `server.py` shutdown | `disconnect_all()`, 10 s | `run_server`'s own cancel |
| `transport/http.py` (×2) | the body read, `handle_request` | the client disconnecting |
| `manifest/environment.py` (×2) | detection `communicate()`, 5 s / 10 s | the caller of the probe |
| `manifest/installer.py` (×6) | install-job `wait()`/`communicate()` | the job's cancel |

Every one is on a path a caller can cancel, so none is exempt:
`test_no_wait_for_in_src_pmcp` bans any `wait_for` attribute, name or
import in `src/pmcp`, with no exemption list. Two existing tests in
`tests/test_tools.py` patched `asyncio.wait_for` to fake a probe timeout;
they now patch `pmcp.tools.handlers.bounded_wait`.

**Tests, real processes, all three Pythons:**
- `test_bounded_wait_keeps_a_cancel_that_lands_as_the_work_finishes`: the
  helper with the cancel scheduled as the work returns -- `CancelledError`
  on every version; on 3.11+ `cancelling() == 1` when caught, and after the
  `uncancel()` a scope would do a later `asyncio.timeout()` still raises
  `TimeoutError`; the stdlib `wait_for` is run alongside as the control
  (swallowed on 3.10/3.11).
- `test_a_cancel_as_the_leader_exits_is_not_swallowed_by_terminate`: a real
  child exits on SIGTERM and the caller is cancelled in that turn (the
  process's `wait` is wrapped to `call_soon(caller.cancel)` as it returns):
  cancelled, tree dead.
- `test_a_restart_cancelled_as_the_old_server_exits_spawns_nothing`:
  `restart_server` on a real server, cancelled as the old server exits:
  cancelled, **no spawn**, no client registered, old tree dead.
- `test_an_update_probe_cancelled_as_it_finishes_is_cancelled`: a real
  probe command whose `communicate()` returns as the caller is cancelled:
  cancelled, not a probe result.

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
- (Round 3) Main also leaves the tree alive when shutdown lands
  mid-handshake (`clients=['srv']`), the same leak as a caller cancel there.

## Changes

Line numbers are main `89559db`. The round-3 spike diff is
`src/pmcp/client/manager.py` (about 439 lines in, 217 out; roughly 40 of each
are `_terminate_process_tree`'s body re-indented under its new `try`, and
60 are `disconnect_server`'s bookkeeping moved into `_forget_disconnected`)
and `src/pmcp/tools/handlers.py` (7 in, 7 out).

### `src/pmcp/client/manager.py` (modify)

- After `_TaskT` (`manager.py:145`): `_retrieve_outcome`,
  `_cancel_without_waiting`, `_OWNER_RECANCEL_S`, `_abandon_owner`,
  `_reap_child` (Design decision 1),
  `_log_abandoned_owner_failure` (6), `_handshake_error` (3).
- Before `_terminate_process_tree` (`manager.py:244`): `_kill_process_tree_now`
  (Design decision 4). `_terminate_process_tree`: everything after its first
  `_signal(kill=False)` under `try: … except asyncio.CancelledError:
  _kill_process_tree_now(process, group_pgid=group_pgid); raise`.
- `_cancel_background_tasks` (`manager.py:1178`): waits with `asyncio.wait`
  and retrieves outcomes; new `_cancel_background_tasks_now` and the shared
  `_background_tasks_for` (Design decision 5).
- `disconnect_server` (`manager.py:1349`): after the `if not managed:` early
  return, `try: reap read task; _teardown_outbound; _close_or_terminate;
  _cancel_background_tasks` / `except asyncio.CancelledError:
  _abandon_client_io; _cancel_background_tasks_now; _forget_disconnected;
  raise`; then `_forget_disconnected` and `return (True, cancelled, None)`.
  New `_close_or_terminate` (the old close step, returning the described
  failure) and `_forget_disconnected` (the old bookkeeping, unchanged).
- `_connect_stdio` (`manager.py:2461`): `except asyncio.CancelledError` (the
  synchronous path) before `except Exception` (graceful
  `_abort_stdio_handshake` under `try/except CancelledError/finally
  _drop_client`). New `_abort_stdio_handshake`, `_drop_client`,
  `_abandon_client_io`.
- `_connect_remote_stream` (`manager.py:2755`, `:2789`): the pre-handoff
  handler cancels the owner without awaiting it; the handshake handlers as in
  stdio; new `_abort_remote_handshake`.
- `_close_remote_transport` (`manager.py:2596`): Design decision 6.
- `_teardown_outbound` (`manager.py:3258-3270`): `await _reap_child(writer,
  timeout=timeout)`.
- `_shutdown_one` (`manager.py:3564`): `_reap_child` for the read task, and an
  `except asyncio.CancelledError: _abandon_client_io; raise`.
- `_cleanup_client` (`manager.py:3652`): `try: await _cleanup_client_io /
  except CancelledError: _abandon_client_io; raise / finally:
  _forget_client`; docstring per the new contract.
- `adopt_process` (`manager.py:3817`): `except asyncio.CancelledError:
  _abandon_client_io; _forget_client; raise` before `except Exception`.
- Round 5: `_abandon_client_io` first marks an ONLINE client OFFLINE, so no
  cancelled reader's `finally` schedules an auto-reconnect (Design decision
  7, claude F1).

- Round 6: `_own_group_pgid`, `_kill_retained_group` (Design decision 4);
  `ManagedClient.group_pgid` set in `__post_init__`; `_kill_process_tree_now`
  and `_terminate_process_tree(..., *, group_pgid=None)` use the retained
  group once the leader is reaped and never signal its pid; every caller
  passes `managed.group_pgid`. The four `wait_for` calls become
  `bounded_wait` (Design decision 11).

### `src/pmcp/waits.py` (create, round 6)

- `bounded_wait(aw, timeout)` (Design decision 11).

### `src/pmcp/server.py`, `src/pmcp/transport/http.py`, `src/pmcp/manifest/environment.py`, `src/pmcp/manifest/installer.py` (modify, round 6)

- Each `asyncio.wait_for(` becomes `bounded_wait(` (1, 2, 2 and 6 sites),
  with `from pmcp.waits import bounded_wait`. Nothing else changes.

### `tests/test_tools.py` (modify, round 6)

- The two probe tests that patched `asyncio.wait_for` patch
  `pmcp.tools.handlers.bounded_wait`; a fake `_terminate_process_tree`
  accepts the new keyword.

### `src/pmcp/tools/handlers.py` (modify)

- `_run_update_probe_command`'s `except asyncio.CancelledError`
  (`handlers.py:3409`): `_kill_process_tree_now(process)` instead of
  `await _terminate_process_tree(...)`; import it.
- Round 6: `bounded_wait` instead of `wait_for`; the group id is read right
  after the spawn and passed to both kill paths.

**Round-6 diff size** (`git diff --stat` on `c9206a9`): 8 files, 741
insertions, 288 deletions -- `client/manager.py` 896 changed lines in all
rounds, `waits.py` 63 new, `tools/handlers.py` 25, `tests/test_tools.py` 19,
`manifest/installer.py` 13, and 3–5 each in `server.py`,
`transport/http.py` and `manifest/environment.py`.

### `tests/test_cancel_teardown.py` (create)

The module in *Verbatim bodies*: **55 tests** (53 on 3.10, where the two
3.11+ tests skip); ~20 s per run (round-5 spike: 21.6 s on 3.10), most of it the
real-subprocess tests and the round-5 test's 5.5 s wait past the reconnect
delay.

| Test | Pins |
|---|---|
| `test_stdio_handshake_teardown_keeps_caller_cancel[read_task, stderr_task, outbound_writer, terminate]` | caller cancelled at each graceful-teardown await: `CancelledError` **before** the parked child is released (no linger), tree killed synchronously, client dropped |
| `test_remote_handshake_teardown_keeps_caller_cancel[read_task, outbound_writer, close_transport]` | the same for remote, with a transport whose `__aexit__` blocks until cancelled (a dead peer): the abandoned owner still ends within 100 loop iterations |
| `test_a_lost_cancel_does_not_start_another_connect_attempt` | no retry |
| `test_cleanup_client_keeps_caller_cancel[...]` (4), `test_disconnect_server_keeps_caller_cancel[read_task, outbound_writer, terminate, sweep]` | the other teardowns; `LAZY`, lock released; `[sweep]` pins `asyncio.wait` over `gather` |
| `test_disconnect_server_does_not_cancel_its_own_caller` | the sweep still excludes its caller |
| `test_a_cancelled_disconnect_does_not_wait_on_a_hung_escalation` | **codex round-2 B2**: owner's `__aexit__` blocks in a `finally` that ignores cancellation; one caller cancel returns, drops the client, releases the lock |
| `test_child_cancellation_is_still_absorbed_without_a_caller_cancel`, `..._on_disconnect` | the graceful path is unchanged (`record == ["terminated"]`) |
| `test_one_caller_cancel_is_delivered_exactly_once` | two requests in one iteration, one `CancelledError`; 3.11+ `cancelling() == 2` |
| `test_a_failing_terminate_still_drops_the_stale_client`, `test_a_failing_transport_close_still_drops_the_stale_client` | `finally` removal |
| `test_stdio_cancel_during_handshake_kills_without_a_graceful_teardown`, `test_remote_cancel_during_handshake_abandons_the_owner`, `test_adopt_cancel_during_handshake_kills_without_a_graceful_teardown` | Design decision 3 |
| `test_a_failing_kill_cannot_replace_the_cancel[stdio, adopt]`, `test_a_cancel_caught_in_the_handshake_is_not_retried` | round-1 B1 (P1/P1b): a failing step never replaces the cancel; logged by type, no value |
| `test_a_cancel_scope_gets_its_cancel_without_waiting_on_the_teardown` | anyio scope: the caller leaves at once (round-1 F2) |
| `test_a_timeout_scope_around_the_teardown_still_reports_timeout`, `test_a_taskgroup_sibling_failure_kills_the_tree` (3.11+) | Design decision 8 |
| `test_disconnect_all_redelivers_a_cancel_and_kills_now` | Design decision 7 |
| `test_a_cancelled_terminate_kills_a_sigterm_ignoring_tree` (real process) | `_terminate_process_tree`'s own cancel path: `returncode == -SIGKILL` |
| **round 4:** `test_disconnect_all_cancelled_before_its_workers_start_kills_every_tree` (real processes) | codex round-3 (1): two SIGTERM-ignoring servers, `call_soon(task.cancel)` before the workers start: both `-SIGKILL`, `_clients == {}`, lock free |
| **round 6:** `test_bounded_wait_keeps_a_cancel_that_lands_as_the_work_finishes`, `test_no_wait_for_in_src_pmcp`, `test_a_cancel_as_the_leader_exits_is_not_swallowed_by_terminate`, `test_a_restart_cancelled_as_the_old_server_exits_spawns_nothing`, `test_an_update_probe_cancelled_as_it_finishes_is_cancelled` (real processes) | grok round 5: a cancel landing in the turn the awaited work finishes is kept -- by the helper, in terminate, in `restart_server` (no spawn) and in the update probe; `wait_for` banned in `src/pmcp` (Design decision 11) |
| **round 6:** `test_a_grandchild_of_a_reaped_leader_is_killed[disconnect_all_call_soon, disconnect_server_cancelled, disconnect_server]`, `test_a_reused_group_id_is_not_signalled` (real processes) | codex round 5: a session leader that exited 0 (reaped) and a live `sleep 300` grandchild holding its pipes: the grandchild dies on a cancelled `disconnect_all`, a cancelled and an uncancelled `disconnect_server`; a group whose id a live process holds is not signalled (Design decision 4) |
| **round 5:** `test_a_cancelled_disconnect_all_does_not_respawn_its_servers` (real processes, real readers) | claude round-4 F1: two connected SIGTERM-ignoring servers with grandchildren, `disconnect_all` cancelled at 0, 1 and 2 loop steps: no `reconnect-*` task once the readers have run, no spawn within 5.5 s, every leader and grandchild dead, `_clients == {}` |
| **round 4:** `test_connect_all_cancelled_before_its_workers_start_spawns_nothing` (real command) | the other process-owning fan-out needs no parent kill: nothing spawned |
| **round 4:** `test_a_cancel_at_remote_handoff_abandons_the_owner` | codex round-3 (2): the owner's `__aexit__` is cancelled and the owner ends |
| **round 4:** `test_an_owner_is_cancelled_only_through_abandon_owner` | structural: no other cancel of an owner in `manager.py` |
| `test_loop_shutdown_kills_the_process_tree[mid-handshake, just-failed, mid-terminate]` (real processes) | Design decision 9, including **codex round-2 B1** and claude N2 |
| `test_every_cancellation_handler_is_classified`, `test_no_cancellation_handler_awaits`, `test_every_teardown_abandons_synchronously_on_cancel`, `test_abandon_is_synchronous_and_child_waits_never_absorb` | the class guard |

Determinism: every suspension point is reached through an `asyncio.Event`
gate, and gates are released only **after** the caller's outcome is read, so
a caller that waited for its teardown hits the 10 s hang guard instead of
passing. A deferring child or finalizer ignores *repeated* cancellations
(`_wait_ignoring_cancels`), so re-cancelling cannot rescue a design that
waits. Two places count loop iterations instead of using a timer:
`_ends_within_yields` (an abandoned owner must end within 100 iterations) and
the terminate test's two `sleep(0)`s.

## Documentation impact

- `CHANGELOG.md`: one bullet under `## [Unreleased]` → `### Fixed`, phrased
  "see Consiliency/pmcp#324" (no closing keyword): a caller cancelled while a
  server connection is being torn down (a failed or cancelled handshake,
  `disconnect_server`, a reconnect's cleanup, shutdown) keeps its
  cancellation and returns at once; the server's process tree is SIGKILLed
  and the client dropped synchronously, and a remote transport is abandoned
  without its graceful close. A stdio server is SIGKILLed even when its
  termination is cancelled, including by event-loop shutdown. A cancelled
  `disconnect_all()`/`refresh()` does not respawn the servers it was
  removing (the reconnect revival after a cancelled *reconnect* remains
  Consiliency/pmcp#336).
- `SECURITY.md`: no change; `scripts/check_security_claims.py` reports
  `OK … 129 cited node id(s)` on the spike.

## Dependencies & order

1. `_kill_process_tree_now` and `_terminate_process_tree`'s cancel path.
2. `_reap_child`, `_cancel_without_waiting`, `_abandon_client_io`.
3. The teardown entry points, one at a time, each with its test group.
4. `_close_remote_transport`, the background-task sweep, `_shutdown_one`,
   the update probe.
5. The class-guard tests last; CHANGELOG last.

**Touch points with open work.** Plan PR Consiliency/pmcp#329 (for
Consiliency/pmcp#298) changes `manager.py` around `_send_request`'s write path
and task-hint parsing; no cancellation handler, so only a textual rebase. The
class guards are deliberately src-wide: a later PR that adds a cancellation
handler anywhere in `src/pmcp`, or an `await` inside one, fails them until it
is classified.

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
#      (round-6 spike on c9206a9: 327 passed, 2 skipped on 3.10; 329 passed on 3.11; 329 passed on 3.12)
for v in 3.10 3.11 3.12; do E=.venv; [ $v != 3.10 ] && E=.venv-$v
  $E/bin/python -m pytest tests/test_cancel_teardown.py tests/test_client_manager.py \
    tests/test_client_manager_reconnect.py --cov-fail-under=0 -p no:cacheprovider -q -o timeout=60; done
# 3. CI gates (round-3 spike: all clean)
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src/pmcp/client/manager.py src/pmcp/tools/handlers.py
python3 scripts/check_security_claims.py          # expect OK, 129 cited node ids
python3 scripts/check_plan_consistency.py .consiliency/plans/detailed-324-cancel-teardown-20261003-0213.md
#   measured on this file: "consistent ... blocking inconsistencies: 0", exit 0
# 4. the full suite: once, alone, detached, with a notifying waiter (memory on dev0 is shared)
#    (round-6 spike on c9206a9, run alone on 3.10 with the npm vars unset and
#     pytest's basetemp outside the checkout and off /tmp: 5576 passed, 5 skipped,
#     80 deselected, 0 failed, 588 s;
#     in round 4 a first run caught the publisher-coverage AST guard, see Design decision 7)
#    /tmp has a shared per-user quota on dev0 that a full run can exhaust (measured on
#    the #326 implementation: 57 `OSError: [Errno 122] Disk quota exceeded`), so keep
#    pytest's basetemp and the log off it:
nohup .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp="$WORKTREE_ROOT/pmcp-324-bt" > "$WORKTREE_ROOT/pmcp-324-full.log" 2>&1 &
```

**basetemp (claude round 5):** pass `--basetemp` to a directory **outside
the checkout** -- the trust store refuses a store inside a checkout -- and
**off `/tmp`**, whose shared per-user quota a full run can exhaust; e.g.
`--basetemp="$WORKTREE_ROOT/pmcp-324-bt"` beside the worktree.

Use the venv's own interpreter (`$E/bin/python -m pytest`) for 3.11/3.12;
`uv run -p 3.11` can recreate `.venv-3.11` empty. Without the `unset`, the
`test_tools.py` update-server tests fail on main as well (a dev0 environment
artefact, measured).

**Hang guard.** The module runs in ~3–5 s; the real-subprocess tests carry
their own `subprocess.run(timeout=60)`. Every mutant below ran under
`-o timeout=60` and a 300 s cap. The 60 s watchdog, 700 s pytest-timeout and
720 s faulthandler are never approached by the patched tree.

**Red on main and on round 3.** The final module (run with `-o timeout=30`,
since a tree that waits after a cancel hits the hang guards):

| Tree | 3.10 | 3.11 | 3.12 |
|---|---|---|---|
| main `89559db` | 38 failed, 5 passed, 2 skipped | 40 failed, 5 passed | 40 failed, 5 passed |
| round-3 spike (`c85b60f`) | 5 failed, 38 passed, 2 skipped | 5 failed, 40 passed | 5 failed, 40 passed |
| round-4 spike | 43 passed, 2 skipped | 45 passed | 45 passed |
| round-5 spike (`160a9e8`) | 44 passed, 2 skipped | 46 passed | 46 passed |
| round-6 spike (`c9206a9`) | 53 passed, 2 skipped | 55 passed | 55 passed |

On main the 5 that pass are the ones that must: the two "child cancellation
is still absorbed" tests, `test_disconnect_server_does_not_cancel_its_own_caller`,
`test_a_cancel_caught_in_the_handshake_is_not_retried`, and
`test_connect_all_cancelled_before_its_workers_start_spawns_nothing` (main
spawns nothing there either). On round 3 the 5 failures are exactly codex's
two round-3 findings: the `disconnect_all` repro, the handoff repro, the
owner structural test, and the census and entry-point class tests (the new
fallback handler). The round-2 spike measured in round 3 (31/33/33 failed)
is unchanged in substance.

## Acceptance criteria

- [ ] A caller cancelled at any await of the stdio, remote,
  `disconnect_server`, `_cleanup_client` or `_shutdown_one` teardown sees
  `CancelledError` without waiting for its children; the process tree is
  SIGKILLed or the transport owner abandoned; the client is gone from
  `_clients`; the lifecycle lock is released. Proven by the parametrized
  `..._keeps_caller_cancel[...]` cases, the hung-escalation test and the
  `disconnect_all` test.
- [ ] Every handler that catches a cancellation in `src/pmcp` is classified,
  none awaits (one task root excepted), and every teardown entry point has a
  synchronous abandon-and-raise handler. Proven by the four class tests.
- [ ] A cancelled connect is not retried, and a failing teardown step cannot
  replace the cancel (logged by type, no value). Proven by the retry and
  failing-kill tests.
- [ ] A cancel during the handshake kills without a graceful teardown on all
  three paths; `last_error` reads `Connection cancelled`.
- [ ] The non-cancelled path is unchanged: SIGTERM first, a child's own
  cancellation absorbed, a failed handshake raises its own error.
- [ ] One cancel in, one `CancelledError` out; `asyncio.timeout()` still
  reports `TimeoutError`; a TaskGroup sibling failure kills the tree; an
  anyio scope gets its cancel at once.
- [ ] Loop shutdown in each of the three phases leaves no process and no
  client entry, with real processes; mid-handshake shutdown never enters the
  graceful terminate.
- [ ] A `disconnect_all()` cancelled before any worker starts kills every
  registered tree and drops the registries (real processes); a cancelled
  `connect_all()` before its workers start spawns nothing.
- [ ] A cancel at remote handoff abandons the owner, whose `__aexit__` is
  cancelled; no owner is cancelled other than through `_abandon_owner`.
- [ ] A cancelled `disconnect_all()` does not respawn its servers: with real
  readers and real processes, cancelled at 0, 1 and 2 loop steps, no
  reconnect is scheduled, nothing spawns past the reconnect delay, and no
  leader or grandchild survives. Every synchronous abandon marks an ONLINE
  client OFFLINE before cancelling its reader (one spot,
  `_abandon_client_io`); mutant M26.
- [ ] A cancel that lands in the turn the awaited work finishes is not
  swallowed: no `wait_for` in `src/pmcp`; terminate, `restart_server` (no
  spawn) and the update probe propagate it on 3.10–3.12; on 3.11+ a later
  `asyncio.timeout()` still reports `TimeoutError`. Mutants M27–M29.
- [ ] A grandchild that outlives its reaped leader is killed by a cancelled
  `disconnect_all`, and by a cancelled or uncancelled `disconnect_server`;
  the reaped leader's pid is never signalled, and a group whose id a live
  process holds is left alone. Mutants M30–M33.
- [ ] Verification steps 1–4 pass on 3.10, and 1–2 on 3.11 and 3.12.
- [ ] Every mutant below is red, for the reason stated, on all three Pythons.

## Mutation table

Each mutant was measured on the round-6 spike on `c9206a9` (`mutants6.py`: one or more
string edits to `manager.py`, `handlers.py` or `waits.py`, run
`tests/test_cancel_teardown.py` with the target version's own interpreter
under `-o timeout=60` and a 300 s cap, restore in a `finally`). **All 32 are red on 3.10, 3.11 and 3.12**; none
hung. After each run the two files were byte-identical to the spike.
Red tests are 3.10's.

| # | Mutant | Red tests (3.10) |
|---|---|---|
| M1 | stdio: cancel during the handshake not abandoned | 3 red -- stdio_cancel_during_handshake_kills_without_a_graceful_teard, a_failing_kill_cannot_replace_the_cancel[stdio], loop_shutdown_kills_the_process_tree[mid-handshake] |
| M2 | stdio: cancel during the graceful teardown not abandoned | 6 red -- stdio_handshake_teardown[read_task], stdio_handshake_teardown[stderr_task], stdio_handshake_teardown[outbound_writer] (+3) |
| M3 | remote: cancel during the handshake not abandoned | 1 red -- remote_cancel_during_handshake_abandons_the_owner |
| M4 | remote: cancel during the graceful teardown not abandoned | 3 red -- remote_handshake_teardown[read_task], remote_handshake_teardown[outbound_writer], remote_handshake_teardown[close_transport] |
| M5 | adopt: cancel during the handshake not abandoned | 3 red -- adopt_cancel_during_handshake_kills_without_a_graceful_teard, a_failing_kill_cannot_replace_the_cancel[adopt], every_teardown_abandons_synchronously_on_cancel |
| M6 | disconnect: cancel not abandoned | 5 red -- disconnect_server[read_task], disconnect_server[outbound_writer], disconnect_server[terminate] (+2) |
| M7 | disconnect: registries not reset on cancel | 6 red -- disconnect_server[read_task], disconnect_server[outbound_writer], disconnect_server[terminate] (+3) |
| M8 | _cleanup_client: cancel not abandoned | 5 red -- cleanup_client[read_task], cleanup_client[stderr_task], cleanup_client[outbound_writer] (+2) |
| M9 | _shutdown_one: cancel not abandoned | 1 red -- every_teardown_abandons_synchronously_on_cancel |
| M10 | child waits back to shield + absorb (the original defect) | 18 red -- stdio_handshake_teardown[read_task], stdio_handshake_teardown[stderr_task], stdio_handshake_teardown[outbound_writer] (+15) |
| M12 | background sweep back to gather | 1 red -- disconnect_server[sweep] |
| M13 | abandon does not kill | 23 red -- stdio_handshake_teardown[read_task], stdio_handshake_teardown[stderr_task], stdio_handshake_teardown[outbound_writer] (+20) |
| M14 | synchronous kill skips the group | 4 red -- a_cancelled_disconnect_all_does_not_respawn_its_servers, loop_shutdown_kills_the_process_tree[mid-handshake], loop_shutdown_kills_the_process_tree[just-failed] (+1) |
| M15 | cancelled terminate does not kill (round-1 B2) | 2 red -- a_cancelled_terminate_kills_a_sigterm_ignoring_tree, every_teardown_abandons_synchronously_on_cancel |
| M16 | abandon failure logged with its value | 2 red -- a_failing_kill_cannot_replace_the_cancel[stdio], a_failing_kill_cannot_replace_the_cancel[adopt] |
| M17 | abandon lets a kill failure escape | 3 red -- a_failing_kill_cannot_replace_the_cancel[stdio], a_failing_kill_cannot_replace_the_cancel[adopt], a_cancel_caught_in_the_handshake_is_not_retried |
| M18 | stdio stale entry dropped only on success | 7 red -- stdio_handshake_teardown[read_task], stdio_handshake_teardown[stderr_task], stdio_handshake_teardown[outbound_writer] (+4) |
| M19 | update-probe cancel awaits terminate | 1 red -- no_cancellation_handler_awaits |
| M20 | abandoned owner only signalled, never cancelled | 5 red -- remote_handshake_teardown[read_task], remote_handshake_teardown[outbound_writer], remote_handshake_teardown[close_transport] (+2) |
| M21 | abandoned owner cancelled once, immediately (no re-cancel chain) | 5 red -- remote_handshake_teardown[read_task], remote_handshake_teardown[outbound_writer], remote_handshake_teardown[close_transport] (+2) |
| M22 | cancelled close waits for the owner after abandoning it (round-2 B2) | 2 red -- a_cancelled_disconnect_does_not_wait_on_a_hung_escalation, no_cancellation_handler_awaits |
| M23 | disconnect_all fallback does not abandon (codex r3, 1) | 4 red -- disconnect_all_cancelled_before_its_workers_start_kills_ever, a_cancelled_disconnect_all_does_not_respawn_its_servers, a_grandchild_of_a_reaped_leader_is_killed[disconnect_all_cal (+1) |
| M24 | disconnect_all drops the registries only on success | 2 red -- disconnect_all_cancelled_before_its_workers_start_kills_ever, a_cancelled_disconnect_all_does_not_respawn_its_servers |
| M25 | handoff cancels the owner once (codex r3, 2) | 2 red -- a_cancel_at_remote_handoff_abandons_the_owner, an_owner_is_cancelled_only_through_abandon_owner |
| M26 | abandon does not mark an ONLINE client OFFLINE first (claude r4, F1) | 1 red -- a_cancelled_disconnect_all_does_not_respawn_its_servers |
| M27 | terminate waits with asyncio.wait_for again (grok r5) | 3 red -- no_wait_for_in_src_pmcp, a_cancel_as_the_leader_exits_is_not_swallowed_by_terminate, a_restart_cancelled_as_the_old_server_exits_spawns_nothing |
| M28 | update probe waits with asyncio.wait_for again (grok r5) | 2 red -- no_wait_for_in_src_pmcp, an_update_probe_cancelled_as_it_finishes_is_cancelled |
| M29 | bounded_wait returns the result when the work finished in the cancel's turn | 4 red -- bounded_wait_keeps_a_cancel_that_lands_as_the_work_finishes, a_cancel_as_the_leader_exits_is_not_swallowed_by_terminate, a_restart_cancelled_as_the_old_server_exits_spawns_nothing (+1) |
| M30 | the process group is not retained when the client is created (codex r5) | 3 red -- a_grandchild_of_a_reaped_leader_is_killed[disconnect_all_cal, a_grandchild_of_a_reaped_leader_is_killed[disconnect_server_, a_grandchild_of_a_reaped_leader_is_killed[disconnect_server] |
| M31 | synchronous kill skips the retained group once the leader is reaped | 1 red -- a_grandchild_of_a_reaped_leader_is_killed[disconnect_all_cal |
| M32 | graceful terminate skips the retained group once the leader is reaped | 2 red -- a_grandchild_of_a_reaped_leader_is_killed[disconnect_server_, a_grandchild_of_a_reaped_leader_is_killed[disconnect_server] |
| M33 | retained group signalled even when a live process holds the leader's pid | 1 red -- a_reused_group_id_is_not_signalled |

M27–M28 are red on 3.12 too: through the structural ban, and the terminate
and probe regressions also fail there under the restored `wait_for`.
One flake was found and fixed while measuring: the test helper
`_pid_alive` raised `ProcessLookupError` when a process vanished between
opening and reading its `/proc/<pid>/stat` (an extra red on 3.11's M3 run);
it now treats that as gone, and M3, M26 and M30–M32 were re-run with the
fixed helper on all three Pythons (same red tests, no extras). 20 repeated
runs of the real-process tests per Python were all green.

The implementer re-runs all 32 on the final tree on all three Pythons, with
`mutants6.py`'s `finally`-restore (never `git checkout --`).

## Non-goals

- *(Removed in round 5, grok F007: the round-3 sentence that a cancelled
  `disconnect_all` leaves its dicts uncleared was stale. Since round 4 the
  clears run in `_disconnect_all_unlocked`'s `finally`, on the cancelled path
  too -- `_clients`, `_servers`, the catalogs and the task registries --
  which Design decision 7, the acceptance criteria and mutant M24 require.)*
- **`_own_remote_transport` forwarding its own pre-handoff cancel** into
  `ready` (row 10 of the main census): not a lost caller cancel.
- **Task roots** (`cli.run_server`, `_health_monitor_loop`, the installer's
  `_monitor_install`).
- **The SIGTERM grace and the remote `DELETE` for a cancelled caller**
  (Design decision 1).
- **The auto-reconnect revival** (Design decision 10).
- **The PID-reuse window** inherent in asyncio's child-watcher ordering
  (Design decision 4).
- **`adopt_process`'s cancelled `last_error`** (claude round-3 nit): its
  cancel path uses `_forget_client`, which pops `_servers[name]`, so the
  `"Connection cancelled"` it sets is discarded -- as `adopt_process`'s
  failure path already does via `_cleanup_client`. Unchanged on purpose.
- **`_reap_child`'s test-double branch** (claude round-3 nit): a real task
  always takes the `asyncio.wait` path; the branch exists because
  `tests/test_client_manager.py`'s `TestCleanupClient` passes `MagicMock`
  read tasks, on which `asyncio.wait` blocks forever on 3.11+. Converting
  those tests to real futures is a cleanup for another change.

## Unverified

- **Python 3.13+.** Measured on 3.10, 3.11 and 3.12. Nothing in the design
  depends on a version-specific `Task` internal any more.
- **A real remote transport.** The remote paths are measured with fake
  transports (including codex's blocking-finalizer repro); the real-process
  tests are stdio.
- **The exact moment of the round-2 B1 race.** Codex's ordering ("release the
  handshake and return immediately") did not reproduce in this harness as
  written; stopping the loop right after the handshake failure (`just-failed`)
  does reproduce it on the round-2 spike (tree alive) and on main. The class
  reason it cannot recur -- there is no task left to be cancelled before it
  runs -- does not depend on the ordering.

## Execution Policy

- execute: effort=medium.
- reason: one file of source plus one line in `tools/handlers.py`, on the
  connection lifecycle every server uses. The design is simpler than rounds
  1–2 (no private task, no deferral), and the class tests forbid the shapes
  that broke them.
- Re-run the mutation table on all three Pythons, verification steps 1–3,
  ruff and mypy before requesting review.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the source patch below (between the ```` fences) to `324-src.patch`,
   then run `git apply 324-src.patch` on `c9206a9` (measured). It creates
   `src/pmcp/waits.py` and changes `client/manager.py`, `tools/handlers.py`,
   `server.py`, `transport/http.py`, `manifest/environment.py`,
   `manifest/installer.py` and `tests/test_tools.py`. It changes
   `src/pmcp/client/manager.py` and `src/pmcp/tools/handlers.py`.
2. Write the test module below to `tests/test_cancel_teardown.py`.
3. Add the `CHANGELOG.md` bullet by hand.

### Patch — `src/pmcp/client/manager.py`, `src/pmcp/tools/handlers.py`, `src/pmcp/waits.py` (new), `src/pmcp/server.py`, `src/pmcp/transport/http.py`, `src/pmcp/manifest/environment.py`, `src/pmcp/manifest/installer.py`, `tests/test_tools.py`

````diff
diff --git a/src/pmcp/client/manager.py b/src/pmcp/client/manager.py
index cae5645..ab41448 100644
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -57,6 +57,7 @@ from pmcp.types import (
     ToolInfo,
     TraceContextInfo,
 )
+from pmcp.waits import bounded_wait
 
 resource_module: ModuleType | None
 
@@ -146,6 +147,136 @@ def describe_exception(exc: BaseException) -> str:
 
 _TaskT = TypeVar("_TaskT", bound=asyncio.Task[Any])
 
+
+# --- Cancellation during teardown (Consiliency/pmcp#324) -----------------------
+#
+# The rule every teardown below follows:
+#
+# * Waits for a child task the teardown cancelled go through `asyncio.wait`,
+#   which never raises the child's outcome. So a `CancelledError` out of any
+#   teardown await is the CALLER's -- never ambiguous with the child's -- and
+#   nothing in a teardown catches a `CancelledError` in order to absorb it.
+# * When the caller is cancelled mid-teardown, the teardown switches to a
+#   synchronous path (`ClientManager._abandon_client_io`) that does only what
+#   cannot be interrupted: SIGKILL the process tree, cancel the client's tasks
+#   without awaiting them, drop it from the registries. Then it re-raises.
+#   That path has no `await`, so nothing in it can be cancelled before it
+#   runs, hang, or hold a lock across a suspension.
+# * The non-cancelled path keeps the graceful behaviour (SIGTERM grace,
+#   graceful remote close).
+
+
+def _retrieve_outcome(task: asyncio.Future[Any]) -> None:
+    """Done-callback for a task that was cancelled and is not awaited: mark
+    its outcome retrieved (no "exception was never retrieved"), and log a
+    genuine failure by type only."""
+    if task.cancelled():
+        return
+    exc = task.exception()
+    if exc is not None:
+        logger.debug(f"abandoned task ended with {type(exc).__name__}")
+
+
+def _cancel_without_waiting(task: asyncio.Future[Any] | None) -> None:
+    if task is not None and not task.done():
+        task.cancel()
+        task.add_done_callback(_retrieve_outcome)
+
+
+# How often an abandoned transport owner is re-cancelled until it ends.
+_OWNER_RECANCEL_S = 0.5
+
+
+def _abandon_owner(
+    task: asyncio.Future[Any] | None,
+    shutdown: asyncio.Event | None,
+    on_done: Callable[[asyncio.Future[Any]], None] = _retrieve_outcome,
+) -> None:
+    """Abandon a remote transport owner without awaiting it.
+
+    Signal its shutdown and cancel it now (an owner still entering its
+    transport ends at once), then cancel it again from loop callbacks -- on
+    the next iteration, after the owner has woken from `shutdown.wait()` and
+    entered its transport's `__aexit__`, then every `_OWNER_RECANCEL_S` until
+    it ends. The immediate cancel alone is consumed while the owner is parked
+    on `shutdown.wait()`, and a dead peer's `__aexit__` would then block
+    uncancelled (measured). Callbacks only, no task: nothing here
+    can be cancelled before it runs or hold its caller (Consiliency/pmcp#324).
+    """
+    if task is None or task.done():
+        return
+    if shutdown is not None:
+        shutdown.set()
+    task.add_done_callback(on_done)
+    loop = asyncio.get_running_loop()
+
+    def kick() -> None:
+        if not task.done():
+            task.cancel()
+            loop.call_later(_OWNER_RECANCEL_S, kick)
+
+    task.cancel()  # ends an owner still entering its transport at once
+    loop.call_soon(kick)
+
+
+async def _reap_child(
+    task: asyncio.Future[Any] | None, *, timeout: float | None = None
+) -> None:
+    """Cancel a child task this teardown owns and wait (bounded by
+    ``timeout``) for it to end.
+
+    `asyncio.wait` never raises the child's outcome, so the child's own
+    ``CancelledError`` is not seen here at all, and a ``CancelledError`` out
+    of this await can only be the caller's -- it propagates. This is what
+    lets the teardowns below tell the two apart without a private task.
+    """
+    if task is None:
+        return
+    if not isinstance(task, asyncio.Future):
+        # A test double, not a task: cancel it unless done and move on, as
+        # the old `shield` + `except Exception` path effectively did.
+        # `asyncio.wait` on such an object would wait forever on 3.11+.
+        if not task.done():
+            task.cancel()
+        return
+    if not task.done():
+        task.cancel()
+        await asyncio.wait({task}, timeout=timeout)
+    if task.done():
+        _retrieve_outcome(task)
+    else:
+        task.add_done_callback(_retrieve_outcome)
+
+
+def _log_abandoned_owner_failure(name: str, task: asyncio.Future[Any]) -> None:
+    """Done-callback for a transport owner a cancelled caller escalated and
+    did not wait for: log a genuine unwind failure (sanitised traceback, as
+    `_close_remote_transport` always has) rather than drop it."""
+    if task.cancelled():
+        return
+    exc = task.exception()
+    if exc is None:
+        return
+    # Formatted and sanitised rather than passed as `exc_info=`: `exc_info`
+    # appends the unredacted exception tree after the sanitised message.
+    traceback_text = "".join(
+        traceback.format_exception(type(exc), exc, exc.__traceback__)
+    )
+    logger.warning(
+        f"[{name}] remote transport failed to unwind after our caller's "
+        f"cancellation: {describe_exception(exc)}\n"
+        f"{sanitize_auth_diagnostic(traceback_text, max_length=None)}"
+    )
+
+
+def _handshake_error(exc: BaseException) -> str:
+    """`last_error` for a failed handshake. A cancellation renders as an
+    empty string through `describe_exception`, so name it."""
+    if isinstance(exc, asyncio.CancelledError):
+        return "Connection cancelled"
+    return describe_exception(exc)
+
+
 # The three catalog kinds, in the order reconciliation fetches and applies them.
 # Iterating this rather than three hand-written branches is what keeps
 # "each kind is handled independently" true as kinds are added.
@@ -270,8 +401,107 @@ class _NullCatalogEventSink:
         pass
 
 
+def _own_group_pgid(process: asyncio.subprocess.Process | None) -> int | None:
+    """The process group a downstream leads, read while it is still ours.
+
+    stdio servers are spawned with ``start_new_session=True``, so the child
+    calls ``setsid()`` before ``exec`` and its pgid equals its pid; an adopted
+    process may or may not lead a group. Read once, when the client is
+    created -- the leader is then alive or an unreaped zombie, and
+    ``os.getpgid`` answers for both -- so the group can still be killed
+    after asyncio has reaped the leader (codex round 5: a grandchild that
+    outlived its leader survived every teardown). ``None`` when the process
+    does not lead its own group, so the gateway's own group is never kept.
+    """
+    if process is None or process.returncode is not None:
+        return None
+    pid = process.pid
+    if not isinstance(pid, int) or not hasattr(os, "getpgid"):
+        return None
+    try:
+        return pid if os.getpgid(pid) == pid else None
+    except (ProcessLookupError, PermissionError, OSError):
+        return None
+
+
+def _kill_retained_group(group_pgid: int | None) -> None:
+    """SIGKILL a group whose leader asyncio has already reaped, synchronously.
+
+    Only while (1) the group still has a member (``killpg(pgid, 0)``) and (2)
+    no process holds the leader's pid (``kill(pgid, 0)`` fails with
+    ``ProcessLookupError``). While any member of our group lives, the kernel
+    keeps the id in use, so it cannot have been handed to a new process; a
+    process holding that pid therefore means the id was reused after our
+    group emptied, and the group is left alone. Residual: the id is reused
+    by a new session leader that then exits while its own children live, all
+    between our reap and this call -- the pid space must cycle in that window.
+    """
+    if group_pgid is None or not hasattr(os, "killpg"):
+        return
+    try:
+        os.killpg(group_pgid, 0)
+    except (ProcessLookupError, PermissionError, OSError):
+        return  # no member left (or not ours to signal)
+    try:
+        os.kill(group_pgid, 0)
+        return  # a live process holds the leader's pid: the id was reused
+    except ProcessLookupError:
+        pass
+    except (PermissionError, OSError):
+        return
+    try:
+        os.killpg(group_pgid, signal.SIGKILL)
+    except (ProcessLookupError, PermissionError, OSError):
+        pass
+
+
+def _kill_process_tree_now(
+    process: asyncio.subprocess.Process | None,
+    *,
+    group_pgid: int | None = None,
+) -> None:
+    """SIGKILL a downstream process and its group, synchronously.
+
+    The uninterruptible step of a cancelled teardown (Consiliency/pmcp#324):
+    no await, so nothing can cancel or skip it. The leader is signalled only
+    while ``process.returncode is None`` -- once asyncio has reaped it, its
+    pid may be reused. Its group is signalled when the leader leads it (taken
+    from ``os.getpgid`` while the leader is unreaped) or when ``group_pgid``
+    was cached earlier by `_terminate_process_tree` and the group is still
+    alive.
+    """
+    if process is None:
+        _kill_retained_group(group_pgid)
+        return
+    pid = process.pid
+    if process.returncode is not None:
+        # The leader is reaped: never signal its pid; the group only through
+        # the retained, checked id (codex round 5).
+        _kill_retained_group(group_pgid)
+        return
+    if isinstance(pid, int):
+        if group_pgid is None and hasattr(os, "getpgid"):
+            try:
+                if os.getpgid(pid) == pid:
+                    group_pgid = pid
+            except (ProcessLookupError, PermissionError, OSError):
+                pass
+        try:
+            process.kill()
+        except (ProcessLookupError, OSError):
+            pass
+    if group_pgid is not None and hasattr(os, "killpg"):
+        try:
+            os.killpg(group_pgid, signal.SIGKILL)
+        except (ProcessLookupError, PermissionError, OSError):
+            pass
+
+
 async def _terminate_process_tree(
-    process: asyncio.subprocess.Process | None, name: str
+    process: asyncio.subprocess.Process | None,
+    name: str,
+    *,
+    group_pgid: int | None = None,
 ) -> None:
     """Terminate a downstream process and its whole process group.
 
@@ -285,7 +515,11 @@ async def _terminate_process_tree(
     never accidentally signals an unrelated group such as the gateway's own.
     """
     if process is None or process.returncode is not None:
+        # Already reaped: a grandchild may still hold the group (codex round
+        # 5). Kill it through the id retained at spawn, never the pid.
+        _kill_retained_group(group_pgid)
         return
+    retained_pgid = group_pgid
 
     def _signal(kill: bool) -> None:
         # Process-group signalling is POSIX-only. On Windows os.getpgid/os.killpg
@@ -318,9 +552,14 @@ async def _terminate_process_tree(
     # Cache the process group up front: once the leader exits, os.getpgid(pid)
     # fails, so we could no longer find the group to escalate against. Only set
     # when this process leads its own group (POSIX, start_new_session=True).
-    group_pgid: int | None = None
+    group_pgid = retained_pgid
     pid = process.pid
-    if isinstance(pid, int) and hasattr(os, "getpgid") and hasattr(os, "killpg"):
+    if (
+        group_pgid is None
+        and isinstance(pid, int)
+        and hasattr(os, "getpgid")
+        and hasattr(os, "killpg")
+    ):
         try:
             if os.getpgid(pid) == pid:
                 group_pgid = pid
@@ -338,39 +577,46 @@ async def _terminate_process_tree(
 
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
+            await bounded_wait(process.wait(), timeout=5.0)
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
+                await bounded_wait(process.wait(), timeout=3.0)
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
+        # Whoever cancelled this wait -- a caller, or loop shutdown cancelling
+        # every task -- the tree is SIGKILLed synchronously before the
+        # cancellation propagates (Consiliency/pmcp#324).
+        _kill_process_tree_now(process, group_pgid=group_pgid)
+        raise
 
 
 # Heartbeat thresholds for health monitoring
@@ -1062,6 +1308,14 @@ class ManagedClient:
     # drains the queue, recreated on demand when the previous one is done.
     outbound: asyncio.Queue[dict[str, Any]] | None = None
     outbound_writer: asyncio.Task[None] | None = None
+    # The process group the downstream leads, read when the client is
+    # created, so every kill path can reach a grandchild after asyncio has
+    # reaped the leader (codex round 5, Consiliency/pmcp#324).
+    group_pgid: int | None = None
+
+    def __post_init__(self) -> None:
+        if self.group_pgid is None:
+            self.group_pgid = _own_group_pgid(self.process)
 
 
 class ClientManager:
@@ -1216,11 +1470,38 @@ class ClientManager:
         # task: a connect/reconnect task scoped to this server name must never
         # cancel a gather() containing itself (that self-cancel recurses until
         # RecursionError and leaves the server stuck in ERROR).
+        tasks = self._background_tasks_for(server_name=server_name, exclude=exclude)
+        for task in tasks:
+            task.cancel()
+        if tasks:
+            # `asyncio.wait`, not `gather`: a cancelled `gather` still waits for
+            # every child to finish, so a cancelled caller would wait out a
+            # child that ignores cancellation (Consiliency/pmcp#324).
+            await asyncio.wait(tasks)
+            for task in tasks:
+                _retrieve_outcome(task)
+        self._background_tasks.difference_update(task for task in tasks if task.done())
+        for task in tasks:
+            if task.done():
+                self._background_task_servers.pop(task, None)
+
+    def _cancel_background_tasks_now(self, *, server_name: str | None = None) -> None:
+        """`_cancel_background_tasks` without the wait: for a cancelled
+        teardown, which must not await (Consiliency/pmcp#324)."""
+        for task in self._background_tasks_for(server_name=server_name):
+            _cancel_without_waiting(task)
+
+    def _background_tasks_for(
+        self,
+        *,
+        server_name: str | None = None,
+        exclude: set[asyncio.Task[Any]] | None = None,
+    ) -> list[asyncio.Task[Any]]:
         exclude = set(exclude) if exclude else set()
         current = asyncio.current_task()
         if current is not None:
             exclude.add(current)
-        tasks = [
+        return [
             task
             for task in self._background_tasks
             if task not in exclude
@@ -1232,14 +1513,6 @@ class ClientManager:
                 or task is self._connect_tasks.get(server_name)
             )
         ]
-        for task in tasks:
-            task.cancel()
-        if tasks:
-            await asyncio.gather(*tasks, return_exceptions=True)
-        self._background_tasks.difference_update(task for task in tasks if task.done())
-        for task in tasks:
-            if task.done():
-                self._background_task_servers.pop(task, None)
 
     def _next_request_id(self, server_name: str) -> int:
         request_id = self._request_counters.get(server_name, 0) + 1
@@ -1425,68 +1698,82 @@ class ClientManager:
             managed.status.status = ServerStatusEnum.OFFLINE
             managed.status.pending_request_count = 0
 
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
-
             try:
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
-
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
+                await _reap_child(managed.read_task, timeout=1.0)
+                # Cancel the outbound writer explicitly (in addition to the
+                # server-name sweep below), so teardown of this path does not
+                # depend on that sweep also matching it -- and reset
+                # `outbound`/`outbound_writer`, the same postcondition
+                # `_cleanup_client` holds (Consiliency/pmcp#287).
+                await self._teardown_outbound(managed, timeout=1.0)
+                closed = await self._close_or_terminate(name, managed)
+                if closed is not None:
+                    return (False, cancelled, closed)
+                await self._cancel_background_tasks(server_name=name)
+            except asyncio.CancelledError:
+                # The caller was cancelled mid-disconnect: finish it
+                # synchronously, then re-raise (Consiliency/pmcp#324). The
+                # `async with` releases the lifecycle lock without awaiting.
+                self._abandon_client_io(name, managed)
+                self._cancel_background_tasks_now(server_name=name)
+                self._forget_disconnected(name, config)
+                raise
+            self._forget_disconnected(name, config)
             return (True, cancelled, None)
 
+    async def _close_or_terminate(
+        self, name: str, managed: ManagedClient
+    ) -> str | None:
+        """`disconnect_server`'s close step: None on success, else the
+        described failure. A cancellation propagates."""
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
+                await _terminate_process_tree(
+                    managed.process, name, group_pgid=managed.group_pgid
+                )
+        except Exception as e:
+            described = describe_exception(e)
+            logger.warning(f"Error disconnecting from {name}: {described}")
+            return described
+        return None
+
+    def _forget_disconnected(self, name: str, config: Any) -> None:
+        """`disconnect_server`'s registry bookkeeping. Synchronous, so the
+        cancelled path runs it too (Consiliency/pmcp#324)."""
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
+
     async def restart_server(
         self, config: ResolvedServerConfig, force: bool = False
     ) -> tuple[bool, int, list[str]]:
@@ -2548,27 +2835,95 @@ class ClientManager:
                 f"{resource_count} resources, {prompt_count} prompts indexed"
             )
 
+        except asyncio.CancelledError as e:
+            # Cancelled DURING the handshake: no graceful teardown, the
+            # synchronous one (Consiliency/pmcp#324). On main this path ran
+            # no teardown at all and leaked the process and the entry.
+            status.status = ServerStatusEnum.ERROR
+            status.last_error = _handshake_error(e)
+            self._abandon_client_io(name, managed)
+            self._drop_client(name, managed)
+            raise
         except Exception as e:
             status.status = ServerStatusEnum.ERROR
             status.last_error = describe_exception(e)
-            for task in (managed.read_task, managed.stderr_task):
-                if task and not task.done():
-                    task.cancel()
-                    try:
-                        await asyncio.shield(task)
-                    except (asyncio.CancelledError, Exception):
-                        pass
-            # A writer started before the handshake failed (e.g. a `ping`
-            # answered during `initialize`) must not survive this pop
-            # (Consiliency/pmcp#287).
-            await self._teardown_outbound(managed)
-            await _terminate_process_tree(process, name)
-            # Drop the stale ERROR client so it can't be found as a live
-            # connection on the next connect attempt (issue: stale entry + leak).
-            if self._clients.get(name) is managed:
-                self._clients.pop(name, None)
+            try:
+                await self._abort_stdio_handshake(name, managed, process)
+            except asyncio.CancelledError:
+                # Cancelled while tearing down a failed handshake.
+                self._abandon_client_io(name, managed)
+                raise
+            finally:
+                # Drop the stale ERROR client so it can't be found as a live
+                # connection on the next connect attempt (issue: stale entry +
+                # leak) -- also when a teardown step raises.
+                self._drop_client(name, managed)
             raise
 
+    async def _abort_stdio_handshake(
+        self,
+        name: str,
+        managed: ManagedClient,
+        process: asyncio.subprocess.Process,
+    ) -> None:
+        """The graceful teardown a failed stdio handshake owes. A caller
+        cancellation propagates out of any await here; the handler above
+        then finishes synchronously."""
+        await _reap_child(managed.read_task)
+        await _reap_child(managed.stderr_task)
+        # A writer started before the handshake failed (e.g. a `ping`
+        # answered during `initialize`) must not survive this pop
+        # (Consiliency/pmcp#287).
+        await self._teardown_outbound(managed)
+        await _terminate_process_tree(process, name, group_pgid=managed.group_pgid)
+
+    def _drop_client(self, name: str, managed: ManagedClient) -> None:
+        if self._clients.get(name) is managed:
+            self._clients.pop(name, None)
+
+    def _abandon_client_io(self, name: str, managed: ManagedClient) -> None:
+        """Everything a teardown must still do when its caller has been
+        cancelled -- synchronously, with no await (Consiliency/pmcp#324):
+
+        * cancel the client's reader, stderr and outbound-writer tasks
+          without awaiting them (each logs a genuine failure by type);
+        * stdio: SIGKILL the process tree (`_kill_process_tree_now`); no
+          SIGTERM grace for a cancelled caller;
+        * remote: signal the transport owner and cancel it from loop
+          callbacks (`_abandon_owner`) without awaiting it. The graceful
+          close is abandoned, so a streamable-HTTP
+          session-ending DELETE may not be sent; the server reaps the
+          session on its own timeout.
+
+        Never raises: a failure in one step is logged by type, and the
+        caller's cancellation is what propagates.
+
+        First, an ONLINE client is marked OFFLINE, before its reader is
+        cancelled: the reader's `finally` schedules an auto-reconnect for a
+        client it still sees ONLINE, and on this path nothing would cancel
+        that reconnect (claude round 4, F1: a `disconnect_all` cancelled
+        before its workers ran respawned every server 5 s later). One spot
+        for every synchronous abandon path -- handshakes, `_cleanup_client`,
+        `disconnect_server`, `_shutdown_one`, the `disconnect_all` fallback
+        and `adopt_process`.
+        """
+        if managed.status.status == ServerStatusEnum.ONLINE:
+            managed.status.status = ServerStatusEnum.OFFLINE
+        for task in (managed.read_task, managed.stderr_task, managed.outbound_writer):
+            _cancel_without_waiting(task)
+        managed.outbound = None
+        managed.outbound_writer = None
+        try:
+            if managed.is_remote:
+                _abandon_owner(managed.transport_owner_task, managed.transport_shutdown)
+            else:
+                _kill_process_tree_now(managed.process, group_pgid=managed.group_pgid)
+        except Exception as exc:
+            logger.warning(
+                f"[{name}] teardown step failed while abandoning a cancelled "
+                f"caller's client: {type(exc).__name__}"
+            )
+
     async def _connect_sse(self, config: ResolvedServerConfig) -> None:
         """Connect to a remote SSE MCP server."""
         if not isinstance(config.config, RemoteMcpServerConfig):
@@ -2713,67 +3068,40 @@ class ClientManager:
                     raise exc
             return
         try:
-            await asyncio.wait_for(asyncio.shield(task), timeout)
-        except asyncio.TimeoutError:
-            # The 5s budget bounds this graceful wait only, not the
-            # escalation below: awaiting the cancelled owner is itself
-            # unbounded, and an __aexit__ that ignores cancellation hangs
-            # there -- the same hang class as today's dead-peer teardown,
-            # neither introduced nor removed by this method. Timeout-as-
-            # success is deliberate (a dead peer must not read as "disconnect
-            # refused"), but that only covers the timeout itself -- a genuine
-            # failure surfacing *while* the owner unwinds under our cancel
-            # must still propagate, so only the CancelledError our own
-            # cancel() causes is swallowed below.
-            logger.warning(
-                f"[{name}] remote transport did not close within {timeout}s; cancelling"
-            )
-            task.cancel()
-            try:
-                await task
-            except asyncio.CancelledError:
-                pass
-        except asyncio.CancelledError:
-            # NOT the same case as the timeout above. The shield keeps the
-            # owner alive, so a CancelledError here is *our caller* being
-            # cancelled, not the owner. Escalate to the owner so its stack
-            # still unwinds, then re-raise the caller's own cancellation --
-            # swallowing it would suppress cancellation of whatever task is
-            # running disconnect_server / _shutdown_one, which is the exact
-            # cancellation-correctness class this fix exists to fix. A
-            # genuine failure surfacing from the owner during this forced
-            # unwind can't also be raised (the caller's own CancelledError
-            # takes precedence, per the same reasoning), but is logged rather
-            # than silently dropped.
-            task.cancel()
-            try:
-                await task
-            except asyncio.CancelledError:
-                pass
-            except Exception as exc:
-                # The traceback, not just the message: this is by construction
-                # the hardest path here to reproduce (needs a caller cancelled
-                # *while* a forced owner unwind is independently failing), so
-                # the frames matter if it is ever seen again.
-                #
-                # Formatted and sanitised rather than passed as `exc_info=`.
-                # `exc_info` hands the raw exception to the logging machinery,
-                # which appends the unredacted exception tree *after* the
-                # sanitised message -- so a bearer token in a transport error
-                # reached the log in full despite the message above being
-                # clean. Redaction here is best-effort defence in depth
-                # (SECURITY.md), and it cannot be applied to text the logging
-                # framework formats on its own.
-                traceback_text = "".join(
-                    traceback.format_exception(type(exc), exc, exc.__traceback__)
-                )
+            # `asyncio.wait`, never `wait_for(shield(task))`: it does not
+            # raise the owner's outcome, so a CancelledError here is our
+            # caller's alone (Consiliency/pmcp#324).
+            done, _ = await asyncio.wait({task}, timeout=timeout)
+            if not done:
+                # The budget bounds this graceful wait only, not the
+                # escalation: awaiting the cancelled owner is itself
+                # unbounded, and an __aexit__ that ignores cancellation hangs
+                # there -- the same hang class as a dead-peer teardown.
+                # Timeout-as-success is deliberate (a dead peer must not read
+                # as "disconnect refused"), but a genuine failure surfacing
+                # while the owner unwinds under our cancel still propagates.
                 logger.warning(
-                    f"[{name}] remote transport failed to unwind while "
-                    f"escalating our caller's cancellation: "
-                    f"{describe_exception(exc)}\n"
-                    f"{sanitize_auth_diagnostic(traceback_text, max_length=None)}"
+                    f"[{name}] remote transport did not close within {timeout}s; "
+                    "cancelling"
                 )
+                task.cancel()
+                await asyncio.wait({task})
+        except asyncio.CancelledError:
+            # Our caller was cancelled, during the graceful wait or the
+            # escalation. Escalate to the owner so its stack still unwinds,
+            # in the owner, but do not wait for it: an owner whose
+            # `__aexit__` blocks must not hold a cancelled caller -- or the
+            # lifecycle lock `disconnect_server` holds (Consiliency/pmcp#324).
+            # A genuine failure surfacing from that unwind is logged by the
+            # callback rather than dropped.
+            _abandon_owner(
+                task, shutdown, lambda t: _log_abandoned_owner_failure(name, t)
+            )
             raise
+        if not task.cancelled():
+            exc = task.exception()
+            if exc is not None:
+                raise exc
         # NOTE: no `except Exception` here, deliberately. A transport exit
         # that genuinely fails must propagate, or disconnect_server's
         # `except Exception -> return (False, cancelled, str(e))` can never
@@ -2848,9 +3176,9 @@ class ClientManager:
             # signal-and-wait -- a cancelled caller must not linger, and the
             # peer reaps its own session on timeout. The owner's `async
             # with` unwinds in the owner, as always -- never touch its stack
-            # from this task.
-            owner_task.cancel()
-            await asyncio.gather(owner_task, return_exceptions=True)
+            # from this task. Not awaited either (Consiliency/pmcp#324): an
+            # owner whose enter ignores cancellation must not hold us.
+            _abandon_owner(owner_task, shutdown)
             raise
 
         try:
@@ -2876,30 +3204,41 @@ class ClientManager:
                 f"{resource_count} resources, {prompt_count} prompts indexed"
             )
 
+        except asyncio.CancelledError as e:
+            # As in `_connect_stdio` (Consiliency/pmcp#324).
+            status.status = ServerStatusEnum.ERROR
+            status.last_error = _handshake_error(e)
+            self._abandon_client_io(name, managed)
+            self._drop_client(name, managed)
+            raise
         except Exception as e:
             status.status = ServerStatusEnum.ERROR
             status.last_error = describe_exception(e)
-            if managed.read_task and not managed.read_task.done():
-                managed.read_task.cancel()
-                try:
-                    await asyncio.shield(managed.read_task)
-                except (asyncio.CancelledError, Exception):
-                    pass
-            # Same as the stdio handshake path (Consiliency/pmcp#287).
-            await self._teardown_outbound(managed)
-            await self._close_remote_transport(name, managed)
-            # Drop the stale ERROR client so it can't be found as a live
-            # connection on the next connect attempt.
-            if self._clients.get(name) is managed:
-                self._clients.pop(name, None)
+            try:
+                await self._abort_remote_handshake(name, managed)
+            except asyncio.CancelledError:
+                self._abandon_client_io(name, managed)
+                raise
+            finally:
+                # Drop the stale ERROR client so it can't be found as a live
+                # connection on the next connect attempt.
+                self._drop_client(name, managed)
             raise
 
+    async def _abort_remote_handshake(self, name: str, managed: ManagedClient) -> None:
+        """The graceful teardown a failed remote handshake owes; see
+        `_abort_stdio_handshake`."""
+        await _reap_child(managed.read_task)
+        # Same as the stdio handshake path (Consiliency/pmcp#287).
+        await self._teardown_outbound(managed)
+        await self._close_remote_transport(name, managed)
+
     async def _read_stderr(self, name: str, stderr: asyncio.StreamReader) -> None:
         """Read stderr from a server process."""
         try:
             while True:
                 try:
-                    line = await asyncio.wait_for(stderr.readline(), timeout=120.0)
+                    line = await bounded_wait(stderr.readline(), timeout=120.0)
                 except asyncio.TimeoutError:
                     logger.debug(f"[{name}] stderr readline timed out, continuing")
                     continue
@@ -3356,16 +3695,10 @@ class ClientManager:
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
+        # `_reap_child`: the writer's own cancellation is never seen; a
+        # CancelledError out of here is the caller's and propagates
+        # (Consiliency/pmcp#324).
+        await _reap_child(writer, timeout=timeout)
 
     async def _drain_outbound(self, managed: ManagedClient) -> None:
         """The one writer task per client: drain the bounded outbound queue.
@@ -3585,7 +3918,7 @@ class ClientManager:
         slice_s = min(idle_timeout_s, IDLE_POLL_SLICE_S)
         while True:
             try:
-                return await asyncio.wait_for(asyncio.shield(future), timeout=slice_s)
+                return await bounded_wait(asyncio.shield(future), timeout=slice_s)
             except asyncio.TimeoutError:
                 if future.done():
                     return future.result()
@@ -3676,14 +4009,7 @@ class ClientManager:
                 managed.status.pending_request_count = 0
 
                 # Cancel read task
-                if managed.read_task:
-                    managed.read_task.cancel()
-                    try:
-                        await asyncio.wait_for(
-                            asyncio.shield(managed.read_task), timeout=1.0
-                        )
-                    except (asyncio.TimeoutError, asyncio.CancelledError):
-                        pass
+                await _reap_child(managed.read_task, timeout=1.0)
 
                 # Close transport. _close_remote_transport itself never
                 # swallows a genuine transport-exit failure; the swallow
@@ -3694,7 +4020,16 @@ class ClientManager:
                 if managed.is_remote:
                     await self._close_remote_transport(name, managed)
                 else:
-                    await _terminate_process_tree(managed.process, name)
+                    await _terminate_process_tree(
+                        managed.process, name, group_pgid=managed.group_pgid
+                    )
+            except asyncio.CancelledError:
+                # disconnect_all was cancelled (e.g. the shutdown budget in
+                # server.py ran out): kill and abandon now, without awaiting,
+                # then re-raise; the gather re-raises it to disconnect_all's
+                # caller (Consiliency/pmcp#324).
+                self._abandon_client_io(name, managed)
+                raise
             except Exception as e:
                 logger.warning(
                     f"Error disconnecting from {name}: {describe_exception(e)}"
@@ -3702,57 +4037,75 @@ class ClientManager:
 
         # Reap servers concurrently: each _terminate_process_tree can cost up to
         # ~8s for a hung stdio server, and disconnect_all() runs under a bounded
-        # shutdown budget (server.py wraps it in wait_for). A sequential loop
+        # shutdown budget (server.py wraps it in bounded_wait). A sequential loop
         # would let two+ hung servers blow that budget and leave later groups
         # unsignalled — orphaning browsers (issue #79/1c) at shutdown. Concurrent
         # reaping makes total time ≈ the slowest single server.
         clients = list(self._clients.items())
-        if clients:
-            await asyncio.gather(
-                *(_shutdown_one(name, managed) for name, managed in clients),
-                return_exceptions=True,
-            )
-
         current = asyncio.current_task()
         exclude = {current} if current is not None else set()
-        await self._cancel_background_tasks(exclude=exclude)
-        self._connect_tasks.clear()
-        self._reconnect_tasks.clear()
-        self._reconcile_tasks.clear()
-        self._reconcile_reruns.clear()
-        self._catalog_suppressed.clear()
-        self._clients.clear()
-        # Capture non-empty immediately before each clear (not after — the
-        # clear must happen first for the dict to actually be empty
-        # afterward, but the check must be the pre-clear state) so a
-        # wholesale teardown still announces what it emptied. Without this,
-        # refresh([]) (disconnect-all + no reconnect) empties every catalog
-        # and publishes nothing — the listener-with-no-publishers failure
-        # this phase exists to prevent. Deliberately NOT routed through
-        # _remove_server_indexes: that method is per-server-name, this is a
-        # wholesale clear, and rewriting it as a loop over names would
-        # change shutdown semantics for no benefit.
-        had_tools = bool(self._tools)
-        had_resources = bool(self._resources)
-        had_prompts = bool(self._prompts)
-        self._tools.clear()
-        self._resources.clear()
-        self._prompts.clear()
-        self._tasks.clear()
-        self._servers.clear()
-        self._lazy_configs.clear()
-        if had_tools:
-            self._catalog_events.note_tools_changed()
-        if had_resources:
-            self._catalog_events.note_resources_changed()
-        if had_prompts:
-            self._catalog_events.note_prompts_changed()
+        try:
+            if clients:
+                await asyncio.gather(
+                    *(_shutdown_one(name, managed) for name, managed in clients),
+                    return_exceptions=True,
+                )
+            await self._cancel_background_tasks(exclude=exclude)
+        except asyncio.CancelledError:
+            # Parent-level fallback (Consiliency/pmcp#324): a cancel that lands
+            # before a `_shutdown_one` worker has run its first instruction
+            # never reaches that worker's own handler, so kill and abandon
+            # every client here, synchronously, whether or not its worker ran
+            # (repeating it for one that did is harmless), then drop the
+            # registries and re-raise.
+            for name, managed in clients:
+                self._abandon_client_io(name, managed)
+            self._cancel_background_tasks_now()
+            raise
+        finally:
+            # The registry clears are synchronous, so the cancelled path runs
+            # them too; they stay in this method, which the publisher-coverage
+            # AST guard (tests/runtime/test_publisher_coverage.py) requires.
+            self._connect_tasks.clear()
+            self._reconnect_tasks.clear()
+            self._reconcile_tasks.clear()
+            self._reconcile_reruns.clear()
+            self._catalog_suppressed.clear()
+            self._clients.clear()
+            # Capture non-empty immediately before each clear (not after — the
+            # clear must happen first for the dict to actually be empty
+            # afterward, but the check must be the pre-clear state) so a
+            # wholesale teardown still announces what it emptied. Without this,
+            # refresh([]) (disconnect-all + no reconnect) empties every catalog
+            # and publishes nothing — the listener-with-no-publishers failure
+            # this phase exists to prevent. Deliberately NOT routed through
+            # _remove_server_indexes: that method is per-server-name, this is a
+            # wholesale clear, and rewriting it as a loop over names would
+            # change shutdown semantics for no benefit.
+            had_tools = bool(self._tools)
+            had_resources = bool(self._resources)
+            had_prompts = bool(self._prompts)
+            self._tools.clear()
+            self._resources.clear()
+            self._prompts.clear()
+            self._tasks.clear()
+            self._servers.clear()
+            self._lazy_configs.clear()
+            if had_tools:
+                self._catalog_events.note_tools_changed()
+            if had_resources:
+                self._catalog_events.note_resources_changed()
+            if had_prompts:
+                self._catalog_events.note_prompts_changed()
 
     async def _cleanup_client(self, name: str, managed: ManagedClient) -> None:
         """Cancel a client's read task, kill its process, and remove it from registries.
 
-        Safe to call on any managed client regardless of state. All exceptions are
-        suppressed so callers always complete successfully.
+        Safe to call on any managed client regardless of state. A remote close
+        failure is logged and suppressed; a terminate failure propagates after
+        the registries are cleared. A cancellation of the caller finishes the
+        teardown synchronously (`_abandon_client_io`) and then propagates
+        (Consiliency/pmcp#324).
 
         Cancels only *this* client's own read/stderr tasks — not every background
         task scoped to the server name. A reconnect runs its connect inside a task
@@ -3764,13 +4117,24 @@ class ClientManager:
         # `while True` writer is not a background-task sweep target on this path
         # (`_cleanup_client` deliberately does NOT call `_cancel_background_tasks`),
         # so without this it leaked one writer task per reconnect generation.
+        try:
+            await self._cleanup_client_io(name, managed)
+        except asyncio.CancelledError:
+            self._abandon_client_io(name, managed)
+            raise
+        finally:
+            self._forget_client(name)
+
+    def _forget_client(self, name: str) -> None:
+        self._clients.pop(name, None)
+        self._servers.pop(name, None)
+        self._remove_server_indexes(name)
+
+    async def _cleanup_client_io(self, name: str, managed: ManagedClient) -> None:
+        """`_cleanup_client`'s graceful teardown; a caller cancellation
+        propagates out of any await here."""
         for task in (managed.read_task, managed.stderr_task, managed.outbound_writer):
-            if task and not task.done():
-                task.cancel()
-                try:
-                    await asyncio.shield(task)
-                except (asyncio.CancelledError, Exception):
-                    pass
+            await _reap_child(task)
         # Reset the outbound path so nothing survives onto a next generation.
         # The writer was cancelled above, but the Queue -- and any reply /
         # notifications/cancelled frames the dead connection left buffered,
@@ -3803,10 +4167,9 @@ class ClientManager:
                     f"[{name}] Error closing remote transport: {describe_exception(e)}"
                 )
         else:
-            await _terminate_process_tree(managed.process, name)
-        self._clients.pop(name, None)
-        self._servers.pop(name, None)
-        self._remove_server_indexes(name)
+            await _terminate_process_tree(
+                managed.process, name, group_pgid=managed.group_pgid
+            )
 
     async def refresh(self, configs: list[ResolvedServerConfig]) -> list[str]:
         """Refresh connections (disconnect + reconnect)."""
@@ -3913,6 +4276,13 @@ class ClientManager:
 
             logger.info(f"Adopted {name}: {indexed} tools indexed")
 
+        except asyncio.CancelledError as e:
+            # As in `_connect_stdio` (Consiliency/pmcp#324).
+            status.status = ServerStatusEnum.ERROR
+            status.last_error = _handshake_error(e)
+            self._abandon_client_io(name, managed)
+            self._forget_client(name)
+            raise
         except Exception as e:
             status.status = ServerStatusEnum.ERROR
             status.last_error = describe_exception(e)
diff --git a/src/pmcp/manifest/environment.py b/src/pmcp/manifest/environment.py
index 613559f..e8a36ac 100644
--- a/src/pmcp/manifest/environment.py
+++ b/src/pmcp/manifest/environment.py
@@ -3,6 +3,7 @@
 from __future__ import annotations
 
 import asyncio
+from pmcp.waits import bounded_wait
 import logging
 import os
 import platform
@@ -72,7 +73,7 @@ async def check_cli(name: str, check_command: list[str]) -> CLIInfo | None:
             stdout=asyncio.subprocess.PIPE,
             stderr=asyncio.subprocess.PIPE,
         )
-        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5.0)
+        stdout, stderr = await bounded_wait(process.communicate(), timeout=5.0)
 
         if process.returncode == 0:
             version = stdout.decode().strip() or stderr.decode().strip()
@@ -101,7 +102,7 @@ async def get_cli_help(
             stdout=asyncio.subprocess.PIPE,
             stderr=asyncio.subprocess.PIPE,
         )
-        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10.0)
+        stdout, stderr = await bounded_wait(process.communicate(), timeout=10.0)
 
         output = stdout.decode() or stderr.decode()
         lines = output.strip().split("\n")[:max_lines]
diff --git a/src/pmcp/manifest/installer.py b/src/pmcp/manifest/installer.py
index 3454402..c6bc240 100644
--- a/src/pmcp/manifest/installer.py
+++ b/src/pmcp/manifest/installer.py
@@ -20,6 +20,7 @@ from pmcp.manifest.loader import (
     requires_credential,
 )
 from pmcp.validation import is_valid_package_version, parse_package_spec
+from pmcp.waits import bounded_wait
 
 logger = logging.getLogger(__name__)
 
@@ -435,7 +436,7 @@ class JobManager:
 
             # Process finished - check return code
             try:
-                await asyncio.wait_for(process.wait(), timeout=5.0)
+                await bounded_wait(process.wait(), timeout=5.0)
             except asyncio.TimeoutError:
                 logger.warning(f"Install {job.id}: Process didn't exit cleanly")
                 await self._safe_terminate_process(process, job.id, force=True)
@@ -484,7 +485,7 @@ class JobManager:
                 process.terminate()
 
             try:
-                await asyncio.wait_for(process.wait(), timeout=5.0)
+                await bounded_wait(process.wait(), timeout=5.0)
             except asyncio.TimeoutError:
                 if not force:
                     logger.warning(
@@ -492,7 +493,7 @@ class JobManager:
                     )
                     process.kill()
                     try:
-                        await asyncio.wait_for(process.wait(), timeout=2.0)
+                        await bounded_wait(process.wait(), timeout=2.0)
                     except asyncio.TimeoutError:
                         logger.error(f"Install {job_id}: Process won't die!")
         except Exception as e:
@@ -554,7 +555,7 @@ class JobManager:
 
             # Give it a moment to terminate gracefully
             try:
-                await asyncio.wait_for(job.process.wait(), timeout=2.0)
+                await bounded_wait(job.process.wait(), timeout=2.0)
             except asyncio.TimeoutError:
                 job.process.kill()
                 await job.process.wait()
@@ -705,7 +706,7 @@ async def install_server(
             env=build_install_child_env(server_config, project_root),
         )
 
-        stdout, stderr = await asyncio.wait_for(
+        stdout, stderr = await bounded_wait(
             process.communicate(),
             timeout=timeout,
         )
@@ -758,7 +759,7 @@ async def verify_installation(
             env=build_install_child_env(server_config, project_root),
         )
 
-        await asyncio.wait_for(process.communicate(), timeout=5.0)
+        await bounded_wait(process.communicate(), timeout=5.0)
         return True
 
     except Exception:
diff --git a/src/pmcp/server.py b/src/pmcp/server.py
index ed00259..077f78b 100644
--- a/src/pmcp/server.py
+++ b/src/pmcp/server.py
@@ -78,6 +78,7 @@ from pmcp.types import (
     LocalMcpServerConfig,
     ResolvedServerConfig,
 )
+from pmcp.waits import bounded_wait
 
 logger = logging.getLogger(__name__)
 
@@ -1017,7 +1018,7 @@ class GatewayServer:
         # deterministically in-process instead (test_listen_registration.py).
         self._listen_handler.close()
         try:
-            await asyncio.wait_for(self._client_manager.disconnect_all(), timeout=10.0)
+            await bounded_wait(self._client_manager.disconnect_all(), timeout=10.0)
         except asyncio.TimeoutError:
             logger.warning("Shutdown timed out, forcing disconnect")
         except Exception as e:
diff --git a/src/pmcp/tools/handlers.py b/src/pmcp/tools/handlers.py
index 09e9f34..3abb4f0 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -31,7 +31,12 @@ from pmcp.auth import (
     sanitize_url_elicitation_url,
 )
 
-from pmcp.client.manager import ClientManager, _terminate_process_tree
+from pmcp.client.manager import (
+    ClientManager,
+    _kill_process_tree_now,
+    _own_group_pgid,
+    _terminate_process_tree,
+)
 from pmcp.config.guidance import GuidanceConfig
 from pmcp.config.loader import (
     registry_allow_private_from_config,
@@ -200,6 +205,7 @@ from pmcp.manifest.loader import (
     is_usable_credential_value,
     requires_credential,
 )
+from pmcp.waits import bounded_wait
 
 logger = logging.getLogger(__name__)
 
@@ -3404,12 +3410,17 @@ class GatewayTools:
             env=env,
             start_new_session=True,
         )
+        # Read now, while the leader is ours: a grandchild can outlive it
+        # (Consiliency/pmcp#324, codex round 5).
+        group_pgid = _own_group_pgid(process)
         try:
-            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60.0)
+            stdout, stderr = await bounded_wait(process.communicate(), timeout=60.0)
         except asyncio.CancelledError:
-            # Reap the tree before propagating; cancellation must not leak a
-            # process. Re-raised unchanged -- a cancellation is not a timeout.
-            await _terminate_process_tree(process, "update-probe")
+            # Kill the tree before propagating; cancellation must not leak a
+            # process. Synchronously: an await here could itself be cancelled
+            # (loop shutdown) and skip the kill (Consiliency/pmcp#324).
+            # Re-raised unchanged -- a cancellation is not a timeout.
+            _kill_process_tree_now(process, group_pgid=group_pgid)
             raise
         except (asyncio.TimeoutError, TimeoutError) as exc:
             # asyncio.TimeoutError is listed EXPLICITLY: it only became an alias
@@ -3418,7 +3429,9 @@ class GatewayTools:
             # the cleanup never runs. CI caught this on 3.10 while 3.11 and 3.12
             # both passed -- the fix silently did nothing on the oldest
             # supported version.
-            await _terminate_process_tree(process, "update-probe")
+            await _terminate_process_tree(
+                process, "update-probe", group_pgid=group_pgid
+            )
             # NORMALISE to the builtin before it reaches a caller, the way
             # ClientManager._send_request does. Re-raising the asyncio class
             # reintroduced the same 3.10 split one frame up: the caller's
diff --git a/src/pmcp/transport/http.py b/src/pmcp/transport/http.py
index 624108b..f0dc9d1 100644
--- a/src/pmcp/transport/http.py
+++ b/src/pmcp/transport/http.py
@@ -47,6 +47,7 @@ from pmcp.auth import (
     validate_resource_server_token,
 )
 from pmcp.types import GatewayDiagnosticsInfo
+from pmcp.waits import bounded_wait
 
 if TYPE_CHECKING:
     from mcp.server import Server
@@ -688,7 +689,7 @@ def create_http_app(
         if request.method == "POST":
             original_receive = request._receive
             try:
-                body_bytes, body_too_large = await asyncio.wait_for(
+                body_bytes, body_too_large = await bounded_wait(
                     _read_body_capped(original_receive, _MAX_BODY_BYTES),
                     timeout=request_timeout,
                 )
@@ -772,7 +773,7 @@ def create_http_app(
             )
         else:
             try:
-                await asyncio.wait_for(
+                await bounded_wait(
                     session_manager.handle_request(
                         request.scope, request.receive, tracking_send
                     ),
diff --git a/src/pmcp/waits.py b/src/pmcp/waits.py
new file mode 100644
index 0000000..f74f66d
--- /dev/null
+++ b/src/pmcp/waits.py
@@ -0,0 +1,63 @@
+"""A bounded wait that never loses its caller's cancellation (Consiliency/pmcp#324).
+
+`asyncio.wait_for` on Python 3.10 and 3.11 ends its ``except CancelledError``
+with ``if fut.done(): return fut.result()``: when the awaited work finishes in
+the same loop turn as a cancel of the *caller* lands, the cancel is swallowed
+and the result returned as if nothing happened (and on 3.11 the task's
+``cancelling()`` count is left raised, so a later ``asyncio.timeout()``
+reports a cancellation instead of a timeout). A teardown that "finished" that
+way lets its caller carry on -- `restart_server` spawns a replacement,
+`refresh` reconnects, the update probe returns its result -- after it was
+cancelled.
+
+`bounded_wait` is the replacement used everywhere in ``src/pmcp``
+(``tests/test_cancel_teardown.py`` bans ``wait_for`` there). It waits with
+`asyncio.wait`, which never raises the awaited work's outcome: a
+``CancelledError`` out of it can only be the caller's, and it always
+propagates -- also when the work finished in the same turn.
+"""
+
+from __future__ import annotations
+
+import asyncio
+from collections.abc import Awaitable
+from typing import Any, TypeVar
+
+_T = TypeVar("_T")
+
+
+def _retrieve(task: asyncio.Future[Any]) -> None:
+    """Mark a finished task's outcome retrieved (no "never retrieved" noise)."""
+    if not task.cancelled():
+        task.exception()
+
+
+async def bounded_wait(aw: Awaitable[_T], timeout: float | None) -> _T:
+    """Await ``aw`` for at most ``timeout`` seconds, like `asyncio.wait_for`.
+
+    * Done in time: its result, or its exception, is returned or raised.
+    * Timed out: the work is cancelled and waited for, as `wait_for` does,
+      then `asyncio.TimeoutError` is raised (the work's result is returned
+      instead if it finished anyway).
+    * The caller is cancelled: the work is cancelled *without* being waited
+      for -- a cancelled caller does not wait on anything -- and the
+      caller's ``CancelledError`` propagates, even when the work had already
+      finished in the same turn.
+    """
+    task = asyncio.ensure_future(aw)
+    try:
+        await asyncio.wait({task}, timeout=timeout)
+    except asyncio.CancelledError:
+        if not task.done():
+            task.cancel()
+            task.add_done_callback(_retrieve)
+        else:
+            _retrieve(task)
+        raise
+    if task.done():
+        return task.result()
+    task.cancel()
+    await asyncio.wait({task})
+    if task.cancelled():
+        raise asyncio.TimeoutError
+    return task.result()
diff --git a/tests/test_tools.py b/tests/test_tools.py
index 1e91803..623c660 100644
--- a/tests/test_tools.py
+++ b/tests/test_tools.py
@@ -4428,7 +4428,7 @@ class TestCapabilityAndProvision:
     ):
         """A hung update probe must come back as ok=False, on every supported version.
 
-        `_run_update_probe_command` bounds the probe with `asyncio.wait_for`, and
+        `_run_update_probe_command` bounds the probe with `bounded_wait` (`asyncio.wait_for` until Consiliency/pmcp#324), and
         on the 3.10 floor `asyncio.TimeoutError` is NOT the builtin `TimeoutError`
         and not a subclass of it -- so a handler catching only the builtin lets the
         timeout escape and `update_server` raises instead of answering. Both classes
@@ -4486,7 +4486,7 @@ class TestCapabilityAndProvision:
     ):
         """The asyncio timeout class must not escape the helper.
 
-        `asyncio.wait_for` raises `asyncio.TimeoutError`, which on 3.10 is not
+        `bounded_wait` (like `asyncio.wait_for`) raises `asyncio.TimeoutError`, which on 3.10 is not
         the builtin and not a subclass of it. The helper converts before the
         exception reaches any caller, so a caller can catch one type on every
         supported version -- the same contract `ClientManager._send_request`
@@ -4513,11 +4513,13 @@ class TestCapabilityAndProvision:
 
         reaped: list[str] = []
 
-        async def _fake_reap(process, label):
+        async def _fake_reap(process, label, **kwargs):
             reaped.append(label)
 
         monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
-        monkeypatch.setattr(asyncio, "wait_for", _timing_out)
+        # The probe waits with `bounded_wait` since Consiliency/pmcp#324
+        # round 6 (`asyncio.wait_for` can swallow a same-turn cancel).
+        monkeypatch.setattr("pmcp.tools.handlers.bounded_wait", _timing_out)
         monkeypatch.setattr("pmcp.tools.handlers._terminate_process_tree", _fake_reap)
 
         with pytest.raises(TimeoutError) as excinfo:
@@ -4872,13 +4874,14 @@ async def main():
         "open(%r,'w').write(str(p.pid));"
         "time.sleep(30)" % pidfile
     )
-    real_wait_for = asyncio.wait_for
+    import pmcp.tools.handlers as H
+
+    real_wait = H.bounded_wait
 
     async def fast(aw, timeout=None):
-        return await real_wait_for(aw, timeout=1.5)
+        return await real_wait(aw, timeout=1.5)
 
-    import pmcp.tools.handlers as H
-    H.asyncio.wait_for = fast
+    H.bounded_wait = fast
     try:
         await gt._run_update_probe_command([sys.executable, "-c", script])
     except BaseException:
````

### File — `tests/test_cancel_teardown.py`

````python
"""Consiliency/pmcp#324: a caller cancelled during a teardown keeps its
cancellation, and the teardown it abandons cannot leave a process, a client
entry or a held lock behind.

Before this module, every teardown in `ClientManager` cancelled child tasks
and awaited them through `except (CancelledError, Exception): pass`, which
also absorbed the *caller's* cancellation: the caller raised the handshake
error instead, and `_connect_with_retry` started another attempt.

The rule these tests pin (round 3 of the plan):

* Child waits go through `asyncio.wait`, which never raises the child's
  outcome, so a `CancelledError` out of a teardown await is the caller's.
* On it, the teardown finishes SYNCHRONOUSLY (`_abandon_client_io`): SIGKILL
  the process tree, cancel the client's tasks without awaiting them, drop the
  registries -- then re-raise. No await, so nothing in it can be cancelled
  before it runs, hang, or hold the lifecycle lock across a suspension.
* Without a caller cancel the graceful teardown is unchanged, and a child's
  own cancellation is still absorbed.

Deterministic: every suspension point is reached through an `asyncio.Event`
gate, and the cancel is delivered while the caller is parked there. The gate
is released only AFTER the caller's outcome is read: a caller that waited for
its teardown would hit the hang guard instead of passing. No timed sleeps;
`asyncio.wait_for(..., _HANG_GUARD_S)` is a hang guard only (tests/_timing.py).
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any
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
from tests._timing import eventually, eventually_sync

_HANG_GUARD_S = 10.0
_CHILD_POINTS = {"read_task", "stderr_task", "outbound_writer"}
_PKG = Path(manager_mod.__file__).resolve().parents[1]  # src/pmcp


_ALL_GATES: list[_Gate] = []


class _Gate:
    """One suspension point: `entered` is set once the caller is parked on
    it; `release` lets it finish (only ever after the caller's outcome)."""

    def __init__(self) -> None:
        self.parked = asyncio.Event()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        _ALL_GATES.append(self)


@pytest.fixture(autouse=True)
async def _release_every_gate() -> Any:
    """However a test ends -- including on a tree where it fails early --
    release every gate it made, so a child that ignores cancellation cannot
    hang the event loop's own teardown."""
    yield
    for gate in _ALL_GATES:
        gate.release.set()
    _ALL_GATES.clear()
    await asyncio.sleep(0)


def _child(gate: _Gate | None) -> asyncio.Task[None]:
    """A child task a teardown will cancel. With a gate it *defers* its own
    cancellation until released, so a teardown that awaited it would park."""

    async def body() -> None:
        try:
            if gate is not None:
                gate.parked.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if gate is not None:
                gate.entered.set()
                await _wait_ignoring_cancels(gate.release)
            raise

    return asyncio.create_task(body())


async def _wait_ignoring_cancels(event: asyncio.Event) -> None:
    """Wait for ``event`` through any number of cancellations -- a child or
    a transport finalizer that does not stop when told to. Only waiting on
    it can then hold a caller; re-cancelling it does not help."""
    while not event.is_set():
        try:
            await event.wait()
        except asyncio.CancelledError:
            continue


def _gated_call(gate: _Gate | None, record: list[str], label: str) -> Any:
    """An async stand-in for `_terminate_process_tree` /
    `_close_remote_transport` that parks on `gate`."""

    async def call(*_a: Any, **_k: Any) -> None:
        if gate is not None:
            gate.entered.set()
            await gate.release.wait()
        record.append(label)

    return call


def _recording_kill(record: list[str], *, fail: bool = False) -> Any:
    def kill(process: Any, **_k: Any) -> None:
        record.append("killed")
        if fail:
            raise OSError("secret-ish detail")

    return kill


async def _cancel_parked(task: asyncio.Task[Any], gate: _Gate) -> None:
    await asyncio.wait_for(gate.entered.wait(), _HANG_GUARD_S)
    assert not task.done(), "caller finished before reaching the gate"
    task.cancel()


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


async def _ends_within_yields(task: asyncio.Future[Any], yields: int = 100) -> bool:
    """Whether ``task`` ends within ``yields`` loop iterations -- a task
    that was cancelled or signalled ends in a few; one nobody told to stop
    never does. A yield count, not a timer."""
    for _ in range(yields):
        if task.done():
            return True
        await asyncio.sleep(0)
    return task.done()


async def _drain(mgr: ClientManager, *gates: _Gate) -> None:
    """Test cleanup: release every gate, end every background task."""
    for gate in gates:
        gate.release.set()
    pending = [t for t in mgr._background_tasks if not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.wait(pending, timeout=_HANG_GUARD_S)


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


# --- the handshake-failure teardowns -----------------------------------------

_STDIO_POINTS = ["read_task", "stderr_task", "outbound_writer", "terminate"]


def _stdio_harness(
    mgr: ClientManager, point: str | None, gate: _Gate, record: list[str]
) -> tuple[Any, Any, Any]:
    """Patch `_connect_stdio`'s collaborators so its handshake fails and its
    graceful teardown parks at `point`."""
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
    kill = patch(
        "pmcp.client.manager._kill_process_tree_now",
        _recording_kill(record),
        create=True,
    )
    return spawn, term, kill


@pytest.mark.parametrize("point", _STDIO_POINTS)
async def test_stdio_handshake_teardown_keeps_caller_cancel(point: str) -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, point, gate, record)
    with spawn, term, kill:
        task = asyncio.create_task(mgr._connect_stdio(_stdio_config()))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)  # gate still closed
        finally:
            await _drain(mgr, gate)

    assert kind == "cancelled", (
        f"caller cancelled at {point} lost its cancellation: {kind} {detail!r}"
    )
    assert record == ["killed"], f"tree not killed synchronously: {record}"
    assert "srv" not in mgr._clients, f"stale client left after cancel at {point}"


_REMOTE_POINTS = ["read_task", "outbound_writer", "close_transport"]


def _remote_task(
    mgr: ClientManager,
    point: str | None,
    gate: _Gate,
    record: list[str],
    seen: list[ManagedClient] | None = None,
) -> asyncio.Task[None]:
    class _Transport:
        """`__aexit__` blocks until cancelled -- a dead peer's graceful
        close. Only an owner that is *cancelled* (not merely signalled to
        shut down) gets past it, which is what the abandon path owes."""

        async def __aenter__(self) -> tuple[Any, Any]:
            return (MagicMock(), MagicMock())

        async def __aexit__(self, *exc_info: Any) -> None:
            await asyncio.Event().wait()

    async def _read_sse(name: str, managed: ManagedClient, read_stream: Any) -> None:
        await _child(gate if point == "read_task" else None)

    async def _send_initialize(managed: ManagedClient) -> None:
        if seen is not None:
            seen.append(managed)
        managed.outbound_writer = _child(gate if point == "outbound_writer" else None)
        if point in _CHILD_POINTS:
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
    seen: list[ManagedClient] = []
    task = _remote_task(mgr, point, gate, record, seen)
    try:
        await _cancel_parked(task, gate)
        kind, detail = await _outcome(task)
        owner = seen[0].transport_owner_task
        assert owner is not None
        owner_ended = await _ends_within_yields(owner)
    finally:
        await _drain(mgr, gate)

    assert kind == "cancelled", (
        f"caller cancelled at {point} lost its cancellation: {kind} {detail!r}"
    )
    assert "remote" not in mgr._clients, f"stale client left after cancel at {point}"
    assert owner_ended, "transport owner not signalled by the abandon path"


async def test_a_lost_cancel_does_not_start_another_connect_attempt() -> None:
    """`_connect_with_retry` catches `Exception`: on main the cancel came out
    as the handshake error and attempts 2 and 3 ran."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, "read_task", gate, record)
    attempts = 0
    real_connect = mgr._connect_server

    async def _counting(config: ResolvedServerConfig) -> None:
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise RuntimeError("second attempt started")
        await real_connect(config)

    mgr._connect_server = _counting  # type: ignore[method-assign]
    with spawn, term, kill, patch.object(manager_mod, "RETRY_DELAYS", [0.0] * 3):
        task = asyncio.create_task(mgr._connect_with_retry(_stdio_config()))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert (kind, attempts) == ("cancelled", 1), (
        f"cancelled connect retried: {kind} {detail!r}, attempts={attempts}"
    )


# --- the other teardowns in the class --------------------------------------------

_CLEANUP_POINTS = ["read_task", "stderr_task", "outbound_writer", "terminate"]


def _stdio_managed(gate: _Gate | None, point: str | None) -> ManagedClient:
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
    managed.config.source = "custom"
    managed.is_remote = False
    managed.process.returncode = None
    managed.read_task = _child(gate if point == "read_task" else None)
    managed.stderr_task = _child(gate if point == "stderr_task" else None)
    managed.outbound_writer = _child(gate if point == "outbound_writer" else None)
    return managed


@pytest.mark.parametrize("point", _CLEANUP_POINTS)
async def test_cleanup_client_keeps_caller_cancel(point: str) -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    managed = _stdio_managed(gate, point)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    await asyncio.sleep(0)  # let the children park before they are cancelled
    with (
        patch(
            "pmcp.client.manager._terminate_process_tree",
            _gated_call(gate if point == "terminate" else None, record, "terminated"),
        ),
        patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record),
            create=True,
        ),
    ):
        task = asyncio.create_task(mgr._cleanup_client("srv", managed))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert kind == "cancelled", f"cancel at {point} lost: {kind} {detail!r}"
    assert record == ["killed"]
    assert "srv" not in mgr._clients and "srv" not in mgr._servers


_DISCONNECT_POINTS = ["read_task", "outbound_writer", "terminate", "sweep"]


@pytest.mark.parametrize("point", _DISCONNECT_POINTS)
async def test_disconnect_server_keeps_caller_cancel(point: str) -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    managed = _stdio_managed(gate, point)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    if point == "sweep":
        # A background task scoped to the server that defers its own
        # cancellation: `gather` would hold a cancelled caller until it ends.
        mgr._track_background_task(_child(gate), "srv")
    await asyncio.sleep(0)
    with (
        patch(
            "pmcp.client.manager._terminate_process_tree",
            _gated_call(gate if point == "terminate" else None, record, "terminated"),
        ),
        patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record),
            create=True,
        ),
    ):
        task = asyncio.create_task(mgr.disconnect_server("srv", force=True))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
            locked = mgr._lifecycle_lock.locked()
        finally:
            await _drain(mgr, gate)

    assert kind == "cancelled", f"cancel at {point} lost: {kind} {detail!r}"
    # At "sweep" the close already ran gracefully; the cancel came after it.
    assert record == (["terminated", "killed"] if point == "sweep" else ["killed"])
    assert "srv" not in mgr._clients
    assert mgr._servers["srv"].status is ServerStatusEnum.LAZY
    assert not locked, "lifecycle lock held after a cancelled disconnect"


async def test_disconnect_server_does_not_cancel_its_own_caller() -> None:
    """A caller that is itself a background task scoped to the server name
    (a reconnect path) must not be swept by its own disconnect."""
    mgr = ClientManager()
    managed = _stdio_managed(None, None)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    with patch("pmcp.client.manager._terminate_process_tree", AsyncMock()):
        task = mgr._track_background_task(
            asyncio.create_task(mgr.disconnect_server("srv", force=True)), "srv"
        )
        kind, value = await _outcome(task)
    assert (kind, value) == ("returned", (True, 0, None)), f"{kind} {value!r}"


# --- round 2 of the panel (codex B2): a hung escalation must not hold the lock --


class _FinalizingTransport:
    """`__aexit__`, once cancelled, awaits a further cleanup step in its
    `finally` that does not finish until the test releases it."""

    def __init__(self) -> None:
        self.finalizer_started = asyncio.Event()
        self.release = asyncio.Event()

    async def __aenter__(self) -> tuple[Any, Any]:
        return (MagicMock(), MagicMock())

    async def __aexit__(self, *exc_info: Any) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            self.finalizer_started.set()
            await _wait_ignoring_cancels(self.release)


async def test_a_cancelled_disconnect_does_not_wait_on_a_hung_escalation() -> None:
    """Codex, round 2: the graceful wait times out, the escalation cancels
    the owner, whose `__aexit__` then blocks in a `finally`; a caller cancel
    after that must still return, drop the client and release the lock."""
    mgr = ClientManager()
    transport = _FinalizingTransport()

    async def _read_sse(name: str, managed: ManagedClient, read_stream: Any) -> None:
        await asyncio.Event().wait()

    mgr._read_sse = _read_sse  # type: ignore[method-assign]
    mgr._send_initialize = AsyncMock()  # type: ignore[method-assign]
    mgr._index_capabilities = AsyncMock(return_value=(0, 0, 0))  # type: ignore[method-assign]
    real_close = mgr._close_remote_transport

    async def _close(name: str, managed: ManagedClient, timeout: float = 5.0) -> None:
        await real_close(name, managed, 0.0)  # graceful budget spent at once

    mgr._close_remote_transport = _close  # type: ignore[method-assign]
    await asyncio.wait_for(
        mgr._connect_remote_stream(_remote_config(), transport, transport_name="t"),
        _HANG_GUARD_S,
    )
    task = asyncio.create_task(mgr.disconnect_server("remote", force=True))
    try:
        await asyncio.wait_for(transport.finalizer_started.wait(), _HANG_GUARD_S)
        task.cancel()  # once: a second cancel would also break an awaiting handler
        kind, detail = await _outcome(task)
        locked = mgr._lifecycle_lock.locked()
    finally:
        transport.release.set()
        await _drain(mgr)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert "remote" not in mgr._clients
    assert not locked, "lifecycle lock held behind a hung owner"


# --- what must NOT change -----------------------------------------------------------


async def test_child_cancellation_is_still_absorbed_without_a_caller_cancel() -> None:
    mgr = ClientManager()
    record: list[str] = []
    spawn, term, kill = _stdio_harness(mgr, None, _Gate(), record)
    with spawn, term, kill:
        with pytest.raises(RuntimeError, match="handshake failed"):
            await asyncio.wait_for(mgr._connect_stdio(_stdio_config()), _HANG_GUARD_S)
    assert record == ["terminated"], "the graceful path must still SIGTERM first"
    assert "srv" not in mgr._clients


async def test_child_cancellation_is_still_absorbed_on_disconnect() -> None:
    mgr = ClientManager()
    managed = _stdio_managed(None, None)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    await asyncio.sleep(0)
    with patch("pmcp.client.manager._terminate_process_tree", AsyncMock()):
        ok, _cancelled, err = await asyncio.wait_for(
            mgr.disconnect_server("srv", force=True), _HANG_GUARD_S
        )
    assert (ok, err) == (True, None)


async def test_one_caller_cancel_is_delivered_exactly_once() -> None:
    """Not lost, and not doubled -- also for two cancels before it runs."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, "read_task", gate, record)
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

    with spawn, term, kill:
        task = asyncio.create_task(caller())
        try:
            await _cancel_parked(task, gate)
            task.cancel()  # a second request in the same iteration
            kind, value = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert (kind, value) == ("returned", "caught once")
    if sys.version_info >= (3, 11):
        assert seen["cancelling"] == 2  # both requests counted, one delivered


async def test_a_failing_terminate_still_drops_the_stale_client() -> None:
    mgr = ClientManager()
    record: list[str] = []
    spawn, _term, kill = _stdio_harness(mgr, None, _Gate(), record)
    with (
        spawn,
        kill,
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
    task = _remote_task(mgr, None, gate, record)
    mgr._close_remote_transport = AsyncMock(  # type: ignore[method-assign]
        side_effect=OSError("close failed")
    )
    try:
        kind, detail = await _outcome(task)
    finally:
        await _drain(mgr)
    assert kind == "raised" and isinstance(detail, OSError), f"{kind} {detail!r}"
    assert "remote" not in mgr._clients


# --- a cancel DURING the handshake -------------------------------------------------


def _parking_handshake(gate: _Gate) -> Any:
    async def _send_initialize(managed: ManagedClient) -> None:
        gate.entered.set()
        await asyncio.Event().wait()

    return _send_initialize


async def test_stdio_cancel_during_handshake_kills_without_a_graceful_teardown() -> (
    None
):
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, None, gate, record)
    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
    with spawn, term, kill:
        task = asyncio.create_task(mgr._connect_stdio(_stdio_config()))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert record == ["killed"], "SIGKILL now, no SIGTERM grace for a cancel"
    assert "srv" not in mgr._clients
    assert mgr._servers["srv"].last_error == "Connection cancelled"


async def test_remote_cancel_during_handshake_abandons_the_owner() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    task = _remote_task(mgr, None, gate, record)
    seen: list[ManagedClient] = []
    parking = _parking_handshake(gate)

    async def _send_initialize(managed: ManagedClient) -> None:
        seen.append(managed)
        await parking(managed)

    mgr._send_initialize = _send_initialize  # type: ignore[method-assign]
    try:
        await _cancel_parked(task, gate)
        kind, detail = await _outcome(task)
        owner = seen[0].transport_owner_task
        assert owner is not None
        owner_ended = await _ends_within_yields(owner)
    finally:
        await _drain(mgr)
    assert owner_ended, "transport owner not signalled by the abandon path"

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert record == [], "no graceful close for a cancelled caller"
    assert "remote" not in mgr._clients


def _adopt_task(mgr: ClientManager, gate: _Gate) -> asyncio.Task[None]:
    process = MagicMock()
    process.returncode = None
    process.stderr = None

    async def _read_stdout(name: str, managed: ManagedClient) -> None:
        await asyncio.Event().wait()

    mgr._read_stdout = _read_stdout  # type: ignore[method-assign]
    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
    return asyncio.create_task(mgr.adopt_process("srv", process, _stdio_config()))


async def test_adopt_cancel_during_handshake_kills_without_a_graceful_teardown() -> (
    None
):
    mgr = ClientManager()
    gate, record = _Gate(), []
    with (
        patch(
            "pmcp.client.manager._terminate_process_tree",
            _gated_call(None, record, "terminated"),
        ),
        patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record),
            create=True,
        ),
    ):
        task = _adopt_task(mgr, gate)
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert record == ["killed"]
    assert "srv" not in mgr._clients


@pytest.mark.parametrize("site", ["stdio", "adopt"])
async def test_a_failing_kill_cannot_replace_the_cancel(
    site: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 1, B1 (P1/P1b): a teardown error raised while handling a caller
    cancel must not replace it. The synchronous path logs it by type only."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    with caplog.at_level("WARNING", logger="pmcp.client.manager"):
        with patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record, fail=True),
            create=True,
        ):
            if site == "stdio":
                spawn, term, _kill = _stdio_harness(mgr, None, gate, record)
                mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
                with spawn, term:
                    task = asyncio.create_task(mgr._connect_stdio(_stdio_config()))
                    await _cancel_parked(task, gate)
                    kind, detail = await _outcome(task)
            else:
                task = _adopt_task(mgr, gate)
                await _cancel_parked(task, gate)
                kind, detail = await _outcome(task)
        await _drain(mgr)

    assert kind == "cancelled", f"{site}: the cancel was lost: {kind} {detail!r}"
    assert "OSError" in caplog.text and "secret-ish detail" not in caplog.text
    assert "srv" not in mgr._clients


async def test_a_cancel_caught_in_the_handshake_is_not_retried() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, _kill = _stdio_harness(mgr, None, gate, record)
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
        term,
        patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record, fail=True),
            create=True,
        ),
        patch.object(manager_mod, "RETRY_DELAYS", [0.0] * 3),
    ):
        task = asyncio.create_task(mgr._connect_with_retry(_stdio_config()))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr)
    assert (kind, attempts) == ("cancelled", 1), f"{kind} {detail!r} {attempts}"


# --- scopes that cancel on the caller's behalf ------------------------------------


async def test_a_cancel_scope_gets_its_cancel_without_waiting_on_the_teardown() -> None:
    """anyio (the MCP SDK's request handlers) re-delivers a scope's cancel on
    every loop iteration; the round-2 deferral spun the CPU for the whole
    teardown. Now the caller leaves at once, before the parked child ends."""
    import anyio

    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, "read_task", gate, record)
    holder: dict[str, Any] = {}

    async def caller() -> bool:
        with anyio.CancelScope() as scope:
            holder["scope"] = scope
            await mgr._connect_stdio(_stdio_config())
        return scope.cancelled_caught

    with spawn, term, kill:
        task = asyncio.create_task(caller())
        try:
            await asyncio.wait_for(gate.entered.wait(), _HANG_GUARD_S)
            holder["scope"].cancel()
            kind, value = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert (kind, value) == ("returned", True)
    assert record == ["killed"]


@pytest.mark.skipif(sys.version_info < (3, 11), reason="asyncio.timeout is 3.11+")
async def test_a_timeout_scope_around_the_teardown_still_reports_timeout() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, "terminate", gate, record)
    holder: dict[str, Any] = {}

    async def caller() -> str:
        try:
            async with asyncio.timeout(None) as scope:  # type: ignore[attr-defined]
                holder["scope"] = scope
                await mgr._connect_stdio(_stdio_config())
        except TimeoutError:
            return "timeout"
        return "no timeout"

    with spawn, term, kill:
        task = asyncio.create_task(caller())
        try:
            await asyncio.wait_for(gate.entered.wait(), _HANG_GUARD_S)
            holder["scope"].reschedule(asyncio.get_running_loop().time())
            kind, value = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert (kind, value) == ("returned", "timeout")
    assert record == ["killed"] and "srv" not in mgr._clients


@pytest.mark.skipif(sys.version_info < (3, 11), reason="TaskGroup is 3.11+")
async def test_a_taskgroup_sibling_failure_kills_the_tree() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, "terminate", gate, record)

    async def sibling() -> None:
        await asyncio.wait_for(gate.entered.wait(), _HANG_GUARD_S)
        raise ValueError("sibling")

    async def group() -> None:
        async with asyncio.TaskGroup() as tg:  # type: ignore[attr-defined]
            tg.create_task(mgr._connect_stdio(_stdio_config()))
            tg.create_task(sibling())

    with spawn, term, kill:
        task = asyncio.create_task(group())
        try:
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    group_type = getattr(builtins, "BaseExceptionGroup")  # 3.11+ only
    assert kind == "raised" and isinstance(detail, group_type)
    assert [type(e) for e in detail.exceptions] == [ValueError]
    assert record == ["killed"] and "srv" not in mgr._clients


async def test_disconnect_all_redelivers_a_cancel_and_kills_now() -> None:
    """`_shutdown_one` runs as a `gather` child; on cancel it now kills and
    re-raises, and the gather re-raises to disconnect_all's caller."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    managed = _stdio_managed(gate, "read_task")
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    await asyncio.wait_for(gate.parked.wait(), _HANG_GUARD_S)
    with (
        patch(
            "pmcp.client.manager._terminate_process_tree",
            _gated_call(None, record, "terminated"),
        ),
        patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record),
            create=True,
        ),
    ):
        task = asyncio.create_task(mgr.disconnect_all())
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert kind == "cancelled", f"{kind} {detail!r}"
    # The worker's handler and the parent-level fallback both kill (harmless).
    assert set(record) == {"killed"}, record


# --- real processes -------------------------------------------------------------------


async def test_a_cancelled_terminate_kills_a_sigterm_ignoring_tree() -> None:
    """`_terminate_process_tree`'s own cancel path, for its callers that do not
    abandon on their own (the update probe's timeout branch): cancelled during
    the SIGTERM grace, it SIGKILLs synchronously before re-raising."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(300)",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None
        await asyncio.wait_for(process.stdout.readline(), _HANG_GUARD_S)
        task = asyncio.create_task(manager_mod._terminate_process_tree(process, "t"))
        await asyncio.sleep(0)  # SIGTERM sent (ignored); parked in the grace wait
        await asyncio.sleep(0)
        task.cancel()
        kind, _ = await _outcome(task)
        returncode = await asyncio.wait_for(process.wait(), _HANG_GUARD_S)
    finally:
        if process.returncode is None:
            process.kill()
    assert kind == "cancelled"
    assert returncode == -signal.SIGKILL


# --- round 3 of the panel (codex): fan-out and handoff ------------------------------


async def _sigterm_ignoring_process() -> asyncio.subprocess.Process:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(300)",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    await asyncio.wait_for(process.stdout.readline(), _HANG_GUARD_S)
    return process


async def test_disconnect_all_cancelled_before_its_workers_start_kills_every_tree() -> (
    None
):
    """Codex, round 3: `call_soon(task.cancel)` right after creating the
    `disconnect_all` task cancels the gather's `_shutdown_one` workers before
    their first instruction, so no per-server handler runs. The parent-level
    fallback kills every tree anyway (real processes)."""
    mgr = ClientManager()
    processes = [await _sigterm_ignoring_process() for _ in range(2)]
    try:
        for i, process in enumerate(processes):
            status = ServerStatus(
                name=f"s{i}", status=ServerStatusEnum.ONLINE, tool_count=0
            )
            managed = ManagedClient(config=MagicMock(), process=process, status=status)
            managed.is_remote = False
            mgr._clients[f"s{i}"] = managed
            mgr._servers[f"s{i}"] = status
        task = asyncio.create_task(mgr.disconnect_all())
        asyncio.get_running_loop().call_soon(task.cancel)
        kind, detail = await _outcome(task)
        codes = [await asyncio.wait_for(p.wait(), _HANG_GUARD_S) for p in processes]
    finally:
        for p in processes:
            if p.returncode is None:
                p.kill()
    assert kind == "cancelled", f"{kind} {detail!r}"
    assert codes == [-signal.SIGKILL, -signal.SIGKILL], codes
    assert mgr._clients == {} and not mgr._lifecycle_lock.locked()


_MCP_SERVER = r"""
import json, os, signal, subprocess, sys
if sys.argv[2:] != ["default-sigterm"]:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
g = subprocess.Popen(["sleep", "300"])
open(sys.argv[1], "w").write(f"{os.getpid()} {g.pid}")
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": []}
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
    sys.stdout.flush()
"""


def _pid_alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        # gone before (or while) its stat file was read
        return False


async def _cancelled_disconnect_all_case(
    k: int, tmp_path: Path
) -> tuple[ClientManager, list[int]]:
    """Two real, connected, SIGTERM-ignoring servers -- each with a real
    reader task and a `sleep 300` grandchild -- then `disconnect_all`
    cancelled after k loop steps (k = 0 is `call_soon(task.cancel)`, before
    any worker runs)."""
    mgr = ClientManager()
    pidfiles = [tmp_path / f"k{k}-s{i}.pids" for i in range(2)]
    for i, pidfile in enumerate(pidfiles):
        cfg = ResolvedServerConfig(
            name=f"s{i}",
            source="project",
            config=LocalMcpServerConfig(
                command=sys.executable, args=["-c", _MCP_SERVER, str(pidfile)]
            ),
        )
        await asyncio.wait_for(mgr._connect_stdio(cfg), _HANG_GUARD_S)
        assert mgr._clients[f"s{i}"].read_task is not None
        assert mgr._clients[f"s{i}"].status.status == ServerStatusEnum.ONLINE
    pids = [int(p) for f in pidfiles for p in f.read_text().split()]
    task = asyncio.create_task(mgr.disconnect_all())
    if k == 0:
        asyncio.get_running_loop().call_soon(task.cancel)
    else:
        for _ in range(k):
            await asyncio.sleep(0)
        task.cancel()
    kind, detail = await _outcome(task)
    assert kind == "cancelled", f"k={k}: {kind} {detail!r}"
    return mgr, pids


async def test_a_cancelled_disconnect_all_does_not_respawn_its_servers(
    tmp_path: Path,
) -> None:
    """Claude round 4, F1: with real reader tasks, the fallback cancelled the
    readers of clients still marked ONLINE, and each reader's `finally`
    scheduled an auto-reconnect that respawned the server ~5 s later, with its
    old config. Cancel at 0, 1 and 2 loop steps, wait past the first reconnect
    delay (5 s): no reconnect task, no respawn, no live leader or grandchild."""
    spawns: list[str] = []
    real_exec = asyncio.create_subprocess_exec
    cases = [await _cancelled_disconnect_all_case(k, tmp_path) for k in (0, 1, 2)]

    async def counting_exec(*args: Any, **kwargs: Any) -> Any:
        spawns.append(str(args[:1]))
        return await real_exec(*args, **kwargs)

    try:
        with patch("asyncio.create_subprocess_exec", counting_exec):
            for _ in range(10):  # let every cancelled reader run its `finally`
                await asyncio.sleep(0)
            for k, (mgr, _pids) in zip((0, 1, 2), cases):
                assert mgr._reconnect_tasks == {}, f"k={k}: {mgr._reconnect_tasks}"
                pending = [t.get_name() for t in mgr._background_tasks if not t.done()]
                assert not [n for n in pending if n.startswith("reconnect-")], (
                    f"k={k}: {pending}"
                )
            await asyncio.sleep(5.5)  # past the first reconnect delay
        for k, (mgr, pids) in zip((0, 1, 2), cases):
            assert mgr._clients == {}, f"k={k}: {list(mgr._clients)}"
            await eventually(
                lambda pids=pids: not any(_pid_alive(p) for p in pids),
                timeout=_HANG_GUARD_S,
                message=f"k={k}: a leader or grandchild is still alive",
            )
        assert spawns == [], spawns
    finally:
        for _mgr, pids in cases:
            for p in pids:
                try:
                    os.kill(p, signal.SIGKILL)
                except ProcessLookupError:
                    pass


# --- round 6 of the panel: `wait_for` swallowing a same-turn cancel (grok) ---------


def _cancel_in_the_turn_it_finishes(
    obj: Any, attr: str, victim: list[asyncio.Task[Any]]
) -> None:
    """Wrap the coroutine method ``obj.<attr>`` so that, as the real one
    returns, the cancel of ``victim[0]`` is scheduled with `call_soon`: it
    then lands in the same loop turn as the awaited work completes, before
    the waiter resumes -- the window in which `asyncio.wait_for` on 3.10 and
    3.11 returns the result instead of raising (grok round 5)."""
    real = getattr(obj, attr)

    async def finishing_then_cancelled(*args: Any, **kwargs: Any) -> Any:
        result = await real(*args, **kwargs)
        asyncio.get_running_loop().call_soon(victim[0].cancel)
        return result

    setattr(obj, attr, finishing_then_cancelled)


async def test_bounded_wait_keeps_a_cancel_that_lands_as_the_work_finishes() -> None:
    """The helper itself: the work completes and the caller is cancelled in
    the same turn. `bounded_wait` raises the caller's `CancelledError` on
    every version; on 3.11+ the task's `cancelling()` is 1 when it is caught
    (delivered once) and an `uncancel()` -- what any scope does -- leaves a
    later `asyncio.timeout()` working. The stdlib `wait_for` is measured
    alongside as the control (it swallows on 3.10/3.11)."""

    async def work() -> str:
        return "done"

    async def caller(waiter: Any, victim: list[asyncio.Task[Any]]) -> str:
        holder = type("H", (), {})()
        holder.run = work
        _cancel_in_the_turn_it_finishes(holder, "run", victim)
        try:
            await waiter(holder.run(), 5.0)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            assert task is not None
            if sys.version_info >= (3, 11):
                assert task.cancelling() == 1
                task.uncancel()
                assert task.cancelling() == 0
                try:
                    async with asyncio.timeout(0.01):
                        await asyncio.sleep(1)
                except TimeoutError:
                    return "cancelled; a later timeout still reports TimeoutError"
                return "cancelled; a later timeout was poisoned"
            return "cancelled"
        return "swallowed"

    victim: list[asyncio.Task[Any]] = []
    task = asyncio.create_task(caller(manager_mod.bounded_wait, victim))
    victim.append(task)
    outcome = await asyncio.wait_for(task, _HANG_GUARD_S)
    if sys.version_info >= (3, 11):
        assert outcome == "cancelled; a later timeout still reports TimeoutError"
    else:
        assert outcome == "cancelled"

    control: list[asyncio.Task[Any]] = []
    ctask = asyncio.create_task(caller(asyncio.wait_for, control))
    control.append(ctask)
    try:
        control_outcome = await asyncio.wait_for(ctask, _HANG_GUARD_S)
    except asyncio.CancelledError:
        control_outcome = "cancelled late"
    if sys.version_info < (3, 12):
        assert control_outcome in ("swallowed", "cancelled late"), control_outcome


def test_no_wait_for_in_src_pmcp() -> None:
    """The class (grok round 5): every bounded wait in `src/pmcp` goes through
    `pmcp.waits.bounded_wait`. No `wait_for` call -- `asyncio.wait_for`, a
    bare `wait_for`, or one reached through any alias -- and no import of
    the name. No exemptions."""
    found = []
    for path in sorted(_PKG.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "wait_for":
                found.append(f"{path.relative_to(_PKG)}:{node.lineno}")
            if isinstance(node, ast.Name) and node.id == "wait_for":
                found.append(f"{path.relative_to(_PKG)}:{node.lineno}")
            if isinstance(node, ast.ImportFrom) and any(
                a.name == "wait_for" for a in node.names
            ):
                found.append(f"{path.relative_to(_PKG)}:{node.lineno}")
    assert found == [], found


async def _real_server(
    tmp_path: Path, name: str, *extra: str
) -> tuple[ClientManager, ResolvedServerConfig, list[int]]:
    mgr = ClientManager()
    pidfile = tmp_path / f"{name}.pids"
    cfg = ResolvedServerConfig(
        name=name,
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable,
            args=["-c", _MCP_SERVER, str(pidfile), *extra],
        ),
    )
    await asyncio.wait_for(mgr._connect_stdio(cfg), _HANG_GUARD_S)
    return mgr, cfg, [int(p) for p in pidfile.read_text().split()]


def _kill_pids(pids: list[int]) -> None:
    for p in pids:
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def test_a_cancel_as_the_leader_exits_is_not_swallowed_by_terminate(
    tmp_path: Path,
) -> None:
    """Grok round 5, `_terminate_process_tree`: a real child exits on SIGTERM
    and the caller is cancelled in the same turn. The cancel propagates (on
    3.10/3.11 `wait_for` returned normally), the tree is dead."""
    mgr, _cfg, pids = await _real_server(tmp_path, "srv", "default-sigterm")
    managed = mgr._clients["srv"]
    victim: list[asyncio.Task[Any]] = []
    try:
        _cancel_in_the_turn_it_finishes(managed.process, "wait", victim)
        task = asyncio.create_task(
            manager_mod._terminate_process_tree(
                managed.process, "srv", group_pgid=managed.group_pgid
            )
        )
        victim.append(task)
        kind, detail = await _outcome(task)
        assert kind == "cancelled", f"{kind} {detail!r}"
        await eventually(
            lambda: not any(_pid_alive(p) for p in pids), timeout=_HANG_GUARD_S
        )
    finally:
        _kill_pids(pids)
        for t in list(mgr._background_tasks):
            t.cancel()


async def test_a_restart_cancelled_as_the_old_server_exits_spawns_nothing(
    tmp_path: Path,
) -> None:
    """Grok round 5, `restart_server`: the old server exits on SIGTERM and
    the restart is cancelled in that same turn. On 3.10/3.11 `wait_for`
    swallowed the cancel, `disconnect_server` returned normally and the
    restart spawned a replacement. Now: `CancelledError`, nothing spawned,
    no client registered, the old tree dead."""
    mgr, cfg, pids = await _real_server(tmp_path, "srv", "default-sigterm")
    managed = mgr._clients["srv"]
    spawns: list[str] = []
    real_exec = asyncio.create_subprocess_exec

    async def counting_exec(*args: Any, **kwargs: Any) -> Any:
        spawns.append("spawn")
        return await real_exec(*args, **kwargs)

    victim: list[asyncio.Task[Any]] = []
    try:
        _cancel_in_the_turn_it_finishes(managed.process, "wait", victim)
        with patch("asyncio.create_subprocess_exec", counting_exec):
            task = asyncio.create_task(mgr.restart_server(cfg, force=True))
            victim.append(task)
            kind, detail = await _outcome(task)
            for _ in range(10):
                await asyncio.sleep(0)
        assert kind == "cancelled", f"{kind} {detail!r}"
        assert spawns == [], spawns
        assert "srv" not in mgr._clients
        await eventually(
            lambda: not any(_pid_alive(p) for p in pids), timeout=_HANG_GUARD_S
        )
    finally:
        _kill_pids(pids)
        for t in list(mgr._background_tasks):
            t.cancel()


async def test_an_update_probe_cancelled_as_it_finishes_is_cancelled(
    tmp_path: Path,
) -> None:
    """Grok round 5, the update probe: `communicate()` completes and the
    caller is cancelled in the same turn. On 3.10/3.11 `wait_for` returned
    the probe result and skipped the kill. Now the cancel propagates."""
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools

    gt = GatewayTools(client_manager=MagicMock(), policy_manager=PolicyManager())
    victim: list[asyncio.Task[Any]] = []
    real_exec = asyncio.create_subprocess_exec

    async def hooked_exec(*args: Any, **kwargs: Any) -> Any:
        process = await real_exec(*args, **kwargs)
        _cancel_in_the_turn_it_finishes(process, "communicate", victim)
        return process

    with patch("asyncio.create_subprocess_exec", hooked_exec):
        task = asyncio.create_task(
            gt._run_update_probe_command([sys.executable, "-c", "print('ok')"])
        )
        victim.append(task)
        kind, detail = await _outcome(task)
    assert kind == "cancelled", f"{kind} {detail!r}"


# --- round 6 of the panel: a grandchild that outlives its reaped leader (codex) --


_EXITING_LEADER = (
    "import os, subprocess, sys\n"
    "g = subprocess.Popen(['sleep', '300'])\n"
    "print(g.pid, flush=True)\n"
    "sys.stdin.readline()\n"  # stay alive until the client has been created
)


async def _reaped_leader_with_grandchild() -> tuple[ManagedClient, int]:
    """A session leader (as pmcp spawns stdio servers) that starts `sleep
    300` -- inheriting its pipes -- and then exits 0. The `ManagedClient` is
    created while the leader lives, as `_connect_stdio` creates it; then the
    leader exits and asyncio reaps it."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _EXITING_LEADER,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stdin is not None
    grandchild = int(
        (await asyncio.wait_for(process.stdout.readline(), _HANG_GUARD_S)).strip()
    )
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=MagicMock(), process=process, status=status)
    managed.is_remote = False
    assert managed.group_pgid == process.pid  # start_new_session: pgid == pid
    assert os.getpgid(process.pid) == process.pid
    process.stdin.write(b"exit\n")
    # Not `process.wait()`: on 3.11+ it also waits for the pipes to close,
    # and the grandchild holds them. The child watcher sets `returncode`
    # when it reaps the leader.
    await eventually(lambda: process.returncode is not None, timeout=_HANG_GUARD_S)
    assert process.returncode == 0 and _pid_alive(grandchild)
    return managed, grandchild


@pytest.mark.parametrize(
    "how",
    ["disconnect_all_call_soon", "disconnect_server_cancelled", "disconnect_server"],
)
async def test_a_grandchild_of_a_reaped_leader_is_killed(how: str) -> None:
    """Codex round 5: once asyncio has reaped the leader, `getpgid(pid)` no
    longer answers, so no kill path reached the group and the grandchild
    survived every teardown (pre-existing on main). The group id is now
    retained when the client is created and used by every kill path; the
    leader's pid is never signalled."""
    managed, grandchild = await _reaped_leader_with_grandchild()
    mgr = ClientManager()
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    try:
        if how == "disconnect_all_call_soon":
            task = asyncio.create_task(mgr.disconnect_all())
            asyncio.get_running_loop().call_soon(task.cancel)
            kind, detail = await _outcome(task)
            assert kind == "cancelled", f"{kind} {detail!r}"
        elif how == "disconnect_server_cancelled":
            task = asyncio.create_task(mgr.disconnect_server("srv", force=True))
            for _ in range(2):
                await asyncio.sleep(0)
            task.cancel()
            kind, detail = await _outcome(task)
            assert kind in ("cancelled", "returned"), f"{kind} {detail!r}"
        else:
            await asyncio.wait_for(
                mgr.disconnect_server("srv", force=True), _HANG_GUARD_S
            )
        await eventually(
            lambda: not _pid_alive(grandchild),
            timeout=_HANG_GUARD_S,
            message=f"{how}: the grandchild {grandchild} survived",
        )
    finally:
        _kill_pids([grandchild])


def test_a_reused_group_id_is_not_signalled() -> None:
    """The guard on the retained id: when a live process holds the leader's
    pid, the id was reused after our group emptied, and nothing is sent."""
    sent: list[tuple[int, int]] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        sent.append((pgid, sig))

    with (
        patch.object(manager_mod.os, "killpg", fake_killpg),
        patch.object(manager_mod.os, "kill", lambda pid, sig: None),
    ):
        manager_mod._kill_retained_group(4242)
    assert sent == [(4242, 0)], sent


async def test_connect_all_cancelled_before_its_workers_start_spawns_nothing(
    tmp_path: Path,
) -> None:
    """The other fan-out that owns processes. A connect worker cancelled
    before its first instruction has spawned nothing, and one cancelled at
    any later await runs its own handler (asyncio's own subprocess creation
    kills a half-made process if cancelled), so connect_all needs no
    parent-level kill -- and must not tear down servers that did connect."""
    mgr = ClientManager()
    pidfile = tmp_path / "pid"
    config = ResolvedServerConfig(
        name="srv",
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable,
            args=["-c", f"open({str(pidfile)!r}, 'w').write('x')"],
        ),
    )
    task = asyncio.create_task(mgr.connect_all([config], retry=False))
    asyncio.get_running_loop().call_soon(task.cancel)
    kind, detail = await _outcome(task)
    for _ in range(20):
        await asyncio.sleep(0)
    assert kind == "cancelled", f"{kind} {detail!r}"
    assert mgr._clients == {} and not pidfile.exists()


async def test_a_cancel_at_remote_handoff_abandons_the_owner() -> None:
    """Codex, round 3: the caller is cancelled while the transport is
    being entered. The owner publishes `ready`, parks on `shutdown.wait()`,
    and a single immediate cancel is consumed there -- its `__aexit__` then
    blocks uncancelled. `_abandon_owner` re-cancels until it ends."""
    mgr = ClientManager()
    holder: dict[str, Any] = {}
    exit_cancelled: list[bool] = []

    class _Transport:
        async def __aenter__(self) -> tuple[Any, Any]:
            asyncio.get_running_loop().call_soon(holder["connect"].cancel)
            return (MagicMock(), MagicMock())

        async def __aexit__(self, *exc_info: Any) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                exit_cancelled.append(True)
                raise

    owners: list[asyncio.Task[Any]] = []
    real_own = mgr._own_remote_transport

    async def _own(*args: Any) -> None:
        task = asyncio.current_task()
        assert task is not None
        owners.append(task)
        await real_own(*args)

    mgr._own_remote_transport = _own  # type: ignore[method-assign]
    task = asyncio.create_task(
        mgr._connect_remote_stream(_remote_config(), _Transport(), transport_name="t")
    )
    holder["connect"] = task
    try:
        kind, detail = await _outcome(task)
        owner_ended = await _ends_within_yields(owners[0])
    finally:
        await _drain(mgr)
    assert kind == "cancelled", f"{kind} {detail!r}"
    assert owner_ended and exit_cancelled == [True], "owner left in its __aexit__"
    assert "remote" not in mgr._clients


def test_an_owner_is_cancelled_only_through_abandon_owner() -> None:
    """Every cancel of a remote transport owner on a cancel path goes through
    `_abandon_owner` (a single immediate cancel is consumed before the
    owner's `__aexit__`). The graceful escalation in `_close_remote_transport`
    cancels its local `task` and then waits for it, on the uncancelled path."""
    tree = dict(_modules())["client/manager.py"]
    bad: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name == "_abandon_owner":
            continue
        for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
            if _callee(call) == "_cancel_without_waiting" and any(
                "owner" in ast.unparse(a) for a in call.args
            ):
                bad.append(f"{fn.name}: {ast.unparse(call)}")
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "cancel"
                and "owner" in ast.unparse(call.func.value)
            ):
                bad.append(f"{fn.name}: {ast.unparse(call)}")
    assert bad == [], bad


# --- real processes: loop shutdown in every phase ----------------------------------

_SHUTDOWN_SCRIPT = r"""
import asyncio, os, sys, time
from pmcp.client import manager as m
from pmcp.client.manager import ClientManager
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

phase, pidfile = sys.argv[1], sys.argv[2]
child = (
    "import os, signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "g = subprocess.Popen(['sleep', '300'])\n"
    "open(sys.argv[1], 'w').write(f'{os.getpid()} {g.pid}')\n"
    "time.sleep(300)\n"
)
state = {"terminate_entered": False}

async def main():
    parked = asyncio.Event()
    failed = asyncio.Event()
    release = asyncio.Event()
    real = m._terminate_process_tree

    async def term(process, name, **kwargs):
        state["terminate_entered"] = True
        parked.set()
        await real(process, name, **kwargs)

    m._terminate_process_tree = term
    mgr = ClientManager()
    state["mgr"] = mgr

    async def handshake(managed):
        while not (os.path.exists(pidfile) and open(pidfile).read().strip()):
            await asyncio.sleep(0.01)
        if phase == "mid-handshake":
            parked.set()
            await asyncio.Event().wait()
        await release.wait()
        failed.set()
        raise RuntimeError("handshake failed")

    mgr._send_initialize = handshake
    cfg = ResolvedServerConfig(
        name="srv", source="project",
        config=LocalMcpServerConfig(command=sys.executable, args=["-c", child, pidfile]),
    )
    asyncio.ensure_future(mgr._connect_stdio(cfg))
    if phase == "just-failed":
        # codex, round 2: let the handshake fail, then stop the loop at once,
        # so shutdown cancels every task -- in round 2 that included a
        # teardown task scheduled but not yet started
        while not (os.path.exists(pidfile) and open(pidfile).read().strip()):
            await asyncio.sleep(0.01)
        release.set()
        await failed.wait()
        asyncio.get_running_loop().stop()
        return
    if phase == "mid-terminate":
        release.set()
    await asyncio.wait_for(parked.wait(), 20)
    for _ in range(3):
        await asyncio.sleep(0)
    # main returns: asyncio.run cancels every task still pending

t0 = time.monotonic()
try:
    asyncio.run(main())
except RuntimeError as exc:  # "Event loop stopped before Future completed."
    print(f"loop-stopped: {exc}", flush=True)
print(f"shutdown-complete elapsed={time.monotonic() - t0:.3f} "
      f"terminate_entered={state['terminate_entered']} "
      f"clients={sorted(state['mgr']._clients)}", flush=True)
"""


@pytest.mark.parametrize("phase", ["mid-handshake", "just-failed", "mid-terminate"])
def test_loop_shutdown_kills_the_process_tree(phase: str, tmp_path: Path) -> None:
    """A real SIGTERM-ignoring server with a grandchild, and `asyncio.run`
    returning while the connect is (a) parked in the handshake, (b) has just
    failed its handshake (codex round 2: the round-2 private teardown task was
    cancelled before its first instruction), (c) parked in the graceful
    terminate (codex round 1). The tree must be dead and `_clients` empty."""
    pidfile = tmp_path / "pids"
    result = subprocess.run(
        [sys.executable, "-c", _SHUTDOWN_SCRIPT, phase, str(pidfile)],
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
            message=f"{phase}: tree outlived shutdown: {result.stdout.strip()}",
        )
    finally:
        for pid in (leader, grandchild):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    assert "clients=[]" in result.stdout, result.stdout
    if phase == "mid-handshake":
        # claude N2: a cancel mid-handshake is a synchronous SIGKILL, not the
        # 5-11 s graceful terminate the round-2 private task ran.
        assert "terminate_entered=False" in result.stdout, result.stdout


# --- the class: every handler that catches a cancellation is classified --------

# (module, enclosing function, "reraises" | "absorbs") -> (count, why).
_CANCEL_HANDLERS: dict[tuple[str, str, str], tuple[int, str]] = {
    ("cli.py", "run_server", "absorbs"): (1, "task root: logs, then the process exits"),
    ("waits.py", "bounded_wait", "reraises"): (
        1,
        "round 6: cancels the work without waiting, then re-raises the caller's cancel",
    ),
    ("client/manager.py", "_terminate_process_tree", "reraises"): (
        1,
        "SIGKILLs the tree synchronously, then re-raises",
    ),
    ("client/manager.py", "ClientManager.disconnect_server", "reraises"): (
        1,
        "abandons synchronously, then re-raises",
    ),
    ("client/manager.py", "ClientManager._connect_stdio", "reraises"): (
        2,
        "cancel during the handshake; cancel during the graceful teardown",
    ),
    ("client/manager.py", "ClientManager._connect_remote_stream", "reraises"): (
        3,
        "pre-handoff BaseException; the two handshake handlers",
    ),
    ("client/manager.py", "ClientManager._close_remote_transport", "reraises"): (
        1,
        "escalates to the owner without waiting, then re-raises",
    ),
    ("client/manager.py", "ClientManager._disconnect_all_unlocked", "reraises"): (
        1,
        "parent-level fallback: abandons every client, then re-raises",
    ),
    ("client/manager.py", "ClientManager._cleanup_client", "reraises"): (
        1,
        "abandons synchronously, then re-raises",
    ),
    ("client/manager.py", "ClientManager.adopt_process", "reraises"): (
        1,
        "cancel during the handshake",
    ),
    (
        "client/manager.py",
        "ClientManager._disconnect_all_unlocked._shutdown_one",
        "reraises",
    ): (1, "abandons synchronously, then re-raises into the gather"),
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
        "SIGKILLs the probe synchronously, then re-raises",
    ),
    ("trust_store.py", "_write_store", "reraises"): (1, "sync temp-file cleanup"),
}

# Handlers allowed to await: task roots whose own cancellation ends them.
_AWAITING_HANDLERS = {("manifest/installer.py", "JobManager._monitor_install")}


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


def _handlers() -> list[tuple[str, str, ast.ExceptHandler]]:
    found: list[tuple[str, str, ast.ExceptHandler]] = []
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
                    found.append((rel, ".".join(scope), child))
                visit(child, scope)

        visit(tree, [])
    return found


def _scan() -> dict[tuple[str, str, str], int]:
    counts: dict[tuple[str, str, str], int] = {}
    for rel, scope, handler in _handlers():
        raises = any(isinstance(n, ast.Raise) for n in ast.walk(handler))
        key = (rel, scope, "reraises" if raises else "absorbs")
        counts[key] = counts.get(key, 0) + 1
    return counts


def test_every_cancellation_handler_is_classified() -> None:
    found = _scan()
    expected = {k: n for k, (n, _why) in _CANCEL_HANDLERS.items()}
    assert found == expected, (
        f"unclassified or changed: {sorted(set(found.items()) - set(expected.items()))}; "
        f"gone: {sorted(set(expected.items()) - set(found.items()))}"
    )


def test_no_cancellation_handler_awaits() -> None:
    """The class rule (round 3): a handler that catches a cancellation does
    only synchronous work. An await there can itself be cancelled before it
    runs (loop shutdown), hang, or hold a lock -- the three round-2 defects."""
    awaiting = sorted(
        (rel, scope, handler.lineno)
        for rel, scope, handler in _handlers()
        if (rel, scope) not in _AWAITING_HANDLERS
        and any(isinstance(n, ast.Await) for n in ast.walk(handler))
    )
    assert awaiting == [], awaiting


# Each teardown entry point, and the synchronous step its CancelledError
# handler must call.
_TEARDOWN_ENTRIES = {
    "ClientManager._connect_stdio": "_abandon_client_io",
    "ClientManager._connect_remote_stream": "_abandon_client_io",
    "ClientManager.adopt_process": "_abandon_client_io",
    "ClientManager.disconnect_server": "_abandon_client_io",
    "ClientManager._cleanup_client": "_abandon_client_io",
    "ClientManager._disconnect_all_unlocked._shutdown_one": "_abandon_client_io",
    "ClientManager._disconnect_all_unlocked": "_abandon_client_io",
    "_terminate_process_tree": "_kill_process_tree_now",
}


def _callee(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def test_every_teardown_abandons_synchronously_on_cancel() -> None:
    by_scope: dict[str, list[ast.ExceptHandler]] = {}
    for rel, scope, handler in _handlers():
        if rel == "client/manager.py":
            by_scope.setdefault(scope, []).append(handler)
    missing = []
    for scope, step in _TEARDOWN_ENTRIES.items():
        ok = [
            h
            for h in by_scope.get(scope, [])
            if any(isinstance(n, ast.Call) and _callee(n) == step for n in ast.walk(h))
            and isinstance(h.body[-1], ast.Raise)
        ]
        if not ok:
            missing.append(f"{scope}: no CancelledError handler calling {step}")
    assert missing == [], missing


def test_abandon_is_synchronous_and_child_waits_never_absorb() -> None:
    """`_abandon_client_io` and `_kill_process_tree_now` are plain functions,
    and `_reap_child` waits with `asyncio.wait` and catches no cancellation."""
    tree = dict(_modules())["client/manager.py"]
    defs = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert isinstance(defs["_abandon_client_io"], ast.FunctionDef)
    assert isinstance(defs["_kill_process_tree_now"], ast.FunctionDef)
    reap = defs["_reap_child"]
    assert not any(
        isinstance(h, ast.ExceptHandler) and _catches_cancellation(h)
        for h in ast.walk(reap)
    )
    assert any(
        isinstance(c, ast.Call) and ast.unparse(c.func) == "asyncio.wait"
        for c in ast.walk(reap)
    )
    assert not any(
        isinstance(c, ast.Call) and ast.unparse(c.func) == "asyncio.shield"
        for c in ast.walk(reap)
    )
````
