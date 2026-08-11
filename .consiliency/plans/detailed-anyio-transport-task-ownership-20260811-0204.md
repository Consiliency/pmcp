# Detailed plan: per-client task ownership of remote transport teardown

## Task

`ManagedClient.sse_exit_stack` is an `AsyncExitStack` entered in one task and
closed in another. anyio cancel scopes are bound to the task that created them,
so closing that stack from a foreign task violates its invariant. Fix the
ownership: the task that enters a remote client's exit stack must be the task
that closes it, on request.

The payoff is that `tests/runtime/test_downstream_remote.py` — which today crams
four independent properties into one test function with one `run_fake_remote`
lifecycle specifically to dodge this — splits into four focused
`@pytest.mark.asyncio` tests, each with its own `ClientManager`, its own
`run_fake_remote(alloc_port(), ...)`, and a `disconnect_all()` teardown.

Base: `main` @ `9d2ac02`, worktree `/mnt/workspace/worktrees/pmcp-plan-anyio`,
branch `plan/anyio-task-ownership`.

---

## Research summary

Everything below was reproduced in this worktree against the real
`tests/runtime/fake_remote.py` peer. **The one-symptom-one-root framing in
`tests/runtime/test_downstream_remote.py`'s module docstring is wrong: the two
symptoms have two different, unrelated causes, and only one of them is a pmcp
bug.** Evidence is in `## Appendix A — reproduction log`.

### Finding 1 — symptom 1 (`disconnect_all()` fails) is a real pmcp bug

Confirmed verbatim. Splitting the test and running the reconnect property alone:

```
E  asyncio.exceptions.CancelledError: Cancelled via cancel scope 7ea49af51550 by
   <Task pending name='Task-516'
    coro=<ClientManager._disconnect_all_unlocked.<locals>._shutdown_one()
    running at .../src/pmcp/client/manager.py:2049>
    cb=[gather.<locals>._done_callback() at .../asyncio/tasks.py:764]>
```

The exit stack is entered by whichever task calls `_connect_remote_stream`
(`manager.py:1500-1512`) and closed at three sites in a different task:
`disconnect_server` (`manager.py:864-873`), `_disconnect_all_unlocked._shutdown_one`
(`manager.py:2047-2056`), `_cleanup_client` (`manager.py:2135-2146`).

This has **two faces**, and the plan must not overstate either:

- **Entering task still alive** (the reconnect property, which calls the private
  `_connect_streamable_http` directly from the test's own task): anyio delivers
  the cancellation to the still-live host task, so `CancelledError` escapes into
  the caller. Deterministic, 3/3.
- **Entering task already dead** (the normal `connect_server` path — the stack is
  entered inside the ephemeral task `_connect_singleflight` creates at
  `manager.py:608-611`, which returns as soon as connect completes): there is no
  host task left to cancel, so `aclose()` raises
  `RuntimeError: Attempted to exit cancel scope in a different task than it was
  entered in`, which `_is_cancel_scope_task_mismatch_error` (`manager.py:67-75`)
  swallows at DEBUG. Observed once per teardown at `manager.py:2052`.

**Measured cost of the swallowed case: none.** `contextlib.AsyncExitStack`
continues unwinding the remaining callbacks after one raises, so the owned
`httpx2.AsyncClient` — entered *first*, unwound *last* — still closes.
Verified: after `connect_server()` + `disconnect_all()` against a live peer,
`remote_http_client.is_closed is True`; open socket FDs are flat (5) across six
connect/disconnect cycles. What is left broken is the transport's anyio task
group, which never runs its `__aexit__` — an invariant violation with no
observed resource cost. **P2's leak fix (`fde079d`) does hold.** Do not sell
this fix as a leak fix.

An earlier measurement of +5 leaked asyncio tasks per cycle was traced to
`sse_starlette`'s server-side `_shutdown_watcher`, not to pmcp. It reproduces
identically under a correct owner-task close, so it is not evidence here.

### Finding 2 — symptom 2 (cross-loop poisoning) is not a pmcp bug at all

`sse_starlette.sse.AppStatus.should_exit` is a **process-global class attribute**
(`.venv/.../sse_starlette/sse.py:126-137`) that latches `True` the first time any
uvicorn server in the process shuts down, and is never reset. Every SSE stream
created afterwards — in any event loop, against any server — terminates
immediately, which surfaces on the pmcp side as
`Failed to connect to <name>: SSE stream ended without a response` and on the
uvicorn side as `ASGI callable returned without completing response`.

Attribution is not inferential:

- A **raw `httpx` initialize POST** — no `ClientManager`, no exit stack, no anyio
  cancel scope anywhere on the client side — fails against a fresh
  `run_fake_remote` on a fresh port in a fresh event loop, after a prior test
  left a live MCP session when its peer shut down.
- It reproduces with **no pmcp teardown at all** (abandon the manager: never call
  `disconnect_all`, never close the stack) — 1 failed / 1 passed.
- It reproduces **identically under a correct owner-task close** that logs zero
  cancel-scope mismatches — 3/3 runs, same failure.
- `AppStatus.should_exit` prints `False` on entry to the first test and `True` on
  its exit; resetting it between tests turns 2 failed / 1 passed into 3 passed.

So the owner-task fix does **not** fix symptom 2, and symptom 2 does not need it.
Symptom 2 is fixed in the harness, in one line, in the fixture that creates the
poison.

### Finding 3 — the two fixes are independent and compose into the acceptance

With only the harness fix, the four-way split (original late-teardown structure,
`disconnect_all()` in `finally`) gives **3 passed / 1 failed**, the failure being
the reconnect property with the exact `CancelledError` above. That is a
ready-made red state for the mandated regression proof.

---

## Design

### Chosen: long-lived per-client transport owner task

The connect path today enters the stack inside a task that then **exits**, so
"signal the connect task to tear down" is not available — the owning task is
already gone. The fix introduces a task whose whole job is to own the transport:

```python
async def _own_remote_transport(
    self,
    name: str,
    transport_context: Any,
    remote_http_client: httpx2.AsyncClient | None,
    ready: asyncio.Future[tuple[Any, Any]],
    shutdown: asyncio.Event,
) -> None:
    try:
        async with AsyncExitStack() as stack:
            # LIFO: client entered first so it closes last, preserving the
            # ordering _connect_remote_stream documents today.
            if remote_http_client is not None:
                await stack.enter_async_context(remote_http_client)
            transport = await stack.enter_async_context(transport_context)
            ready.set_result(transport[:2])
            await shutdown.wait()
    except BaseException as exc:
        if not ready.done():
            # Pre-handoff failure: the connect caller is the one waiting, so
            # hand it the exception rather than raising into a task nobody
            # awaits yet.
            ready.set_exception(exc)
            return
        # Post-handoff failure: do NOT swallow. The owner task carries the
        # exception, and _close_remote_transport re-raises it when it awaits
        # this task, which is what preserves disconnect_server's
        # (False, cancelled, str(e)) contract at manager.py:876-878.
        raise
```

Attach a done-callback that logs an owner that exits before it was asked to —
otherwise a transport that dies while parked is invisible until someone
disconnects, and the loop emits `Task exception was never retrieved`. Reading
`task.exception()` in the callback does not consume it: awaiting the task later
still re-raises, so teardown still reports the real failure.

Ownership transfer must be guarded. The owner is live from `create_task`, but
`ManagedClient` does not reference it until after `await ready` resolves.
Cancel the connect task inside that window and the owner parks forever with
nothing pointing at it:

```python
owner_task = self._track_background_task(
    asyncio.create_task(self._own_remote_transport(...)), name
)
try:
    read_stream, write_stream = await ready
    managed = ManagedClient(
        ..., transport_owner_task=owner_task, transport_shutdown=shutdown
    )
    self._clients[name] = managed
except BaseException:
    # BaseException, not Exception: cancellation is the case that bites here,
    # and it is a BaseException. Cancel rather than signal-and-wait -- a
    # cancelled caller must not linger, and the peer reaps its own session on
    # timeout. The owner's `async with` unwinds in the owner, as always.
    owner_task.cancel()
    await asyncio.gather(owner_task, return_exceptions=True)
    raise
```

Everything the stack owns is entered and unwound inside this one task, so the
cancel-scope invariant holds by construction. Teardown becomes a signal:

```python
async def _close_remote_transport(
    self, name: str, managed: ManagedClient, timeout: float = 5.0
) -> None:
    task, shutdown = managed.transport_owner_task, managed.transport_shutdown
    if task is None or shutdown is None:
        return
    shutdown.set()
    if task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except asyncio.TimeoutError:
        logger.warning(
            f"[{name}] remote transport did not close within {timeout}s; cancelling"
        )
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    except asyncio.CancelledError:
        # NOT the same case as the timeout above. The shield keeps the owner
        # alive, so a CancelledError here is *our caller* being cancelled, not
        # the owner. Escalate to the owner so its stack still unwinds, then
        # re-raise: swallowing it would suppress cancellation of whatever task
        # is running disconnect_server / _shutdown_one, which is the exact
        # cancellation-correctness class this fix exists to fix.
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    # NOTE: no `except Exception` here, deliberately. A transport exit that
    # genuinely fails must propagate, or disconnect_server's
    # `except Exception -> return (False, cancelled, str(e))` at
    # manager.py:876-878 can never fire and a broken teardown is reported as a
    # successful disconnect. The swallow belongs at the two call sites that
    # legitimately must not fail -- _shutdown_one and _cleanup_client -- not
    # here.
```

Properties to preserve and state in code comments:

- **Cancel-escalation is task-correct.** `task.cancel()` raises `CancelledError`
  *inside* the owner at its `await shutdown.wait()`, and the `async with` unwinds
  in the owner's own task. Escalating on timeout is safe, unlike closing from
  outside.
- **A crashed owner closes its own stack** — the `async with` unwinds on the way
  out. Today a connect task that dies leaves the stack open forever.
- **Idempotent.** Setting the event twice, awaiting a finished owner, or being
  called for a client that never got an owner (stdio) all return without effect.
- **The 5 s budget bounds the graceful wait, not the escalation.** The
  `await asyncio.gather(task, ...)` after `task.cancel()` is itself unbounded:
  an `__aexit__` that ignores cancellation hangs there. That is the same hang
  class as today's dead-peer `aclose()`, neither introduced nor removed by this
  change. Stated as a known residual rather than papered over — do not let the
  timeout number imply teardown is bounded end to end.
- **`ready` is created with `asyncio.get_running_loop().create_future()`**, not
  a bare `asyncio.Future()`, matching the existing idiom at `manager.py:1864`
  (which uses the older `get_event_loop()`; the running-loop accessor is the
  non-deprecated spelling of the same intent inside a coroutine).
- **Peer-already-gone is the common shutdown case.** Session termination fails
  fast (`Session termination failed: All connection attempts failed`), but the
  configured connect timeout is 30 s, so the 5 s budget is what stops a dead peer
  from stalling shutdown. `server.py` wraps `disconnect_all()` in its own
  `wait_for`; the executor must confirm the per-client budget composes under it
  before finalising the number.

### Alternatives rejected

| Alternative | Why not |
|---|---|
| Keep `sse_exit_stack`, close it under `anyio.from_thread`/a shim | There is no shim for this. anyio's check is on the host task identity; only running in that task satisfies it. |
| Make `_connect_singleflight`'s connect task long-lived and park it there | Conflates connect retry/singleflight bookkeeping with transport ownership, and that task is shared by the whole connect pipeline including stdio. The owner must be per-transport, created where the transport is. |
| Swap `disconnect_all()` for per-name `disconnect_server()` in test teardown | Already tried and recorded as failed; also addresses only one of three close sites and leaves the invariant violated. |
| Fold `_read_sse` into the owner task | Scope creep. The read loop's task identity is not implicated; it reads memory-object streams, which are not task-bound. Explicitly rejected. |
| Bump `sse_starlette` and hope the global is gone | Not gated on: the latch is in the currently pinned version, an unbounded dependency bump is exactly the failure mode this repo has already paid for twice, and the one-line harness reset is version-independent. |
| Close the ownership-transfer window by publishing `ManagedClient` *before* awaiting `ready` | Structurally eliminates the window rather than guarding it, and was tempting for that reason. Rejected: it makes `_clients[name]` observable with `write_stream=None` for the duration of transport establishment — a state that does not exist today (the current code sets `write_stream` in the same statement that publishes) and that the health monitor and `_send_initialize` would both have to learn to tolerate. Guarding the window is smaller and does not widen the observable state machine. |
| Keep `_is_cancel_scope_task_mismatch_error` as defence-in-depth | After the fix a mismatch means a new ownership bug. A swallow at DEBUG is precisely what let this sit undiagnosed for months. Delete it. |

---

## Changes

### `src/pmcp/client/manager.py` (modify)

- `_is_cancel_scope_task_mismatch_error` (around `:67-75`) — **delete** — after
  the fix a cancel-scope mismatch is a real bug and must be loud, not swallowed
  at DEBUG.
- `ManagedClient.sse_exit_stack` (around `:478`) — **delete** — the stack no
  longer escapes the task that owns it.
- `ManagedClient.transport_owner_task: asyncio.Task[None] | None = None` — **add**
  — the task that entered and will close the transport.
- `ManagedClient.transport_shutdown: asyncio.Event | None = None` — **add** — the
  graceful-teardown signal the owner parks on.
- `ManagedClient.remote_http_client` (around `:485`) — **modify (comment only)** —
  it is now entered into the owner's stack; keep the field, tests assert
  `.is_closed`.
- `ClientManager._own_remote_transport` — **add** — the owner coroutine above.
- `ClientManager._close_remote_transport` — **add** — signal-and-await teardown
  above; the single entry point for all three close sites.
- `ClientManager._connect_remote_stream` (around `:1461-1550`) — **modify** —
  replace inline `AsyncExitStack()` entry with: create `ready` future + `shutdown`
  event, `self._track_background_task(asyncio.create_task(self._own_remote_transport(...)), name)`,
  `read_stream, write_stream = await ready`. Populate the two new `ManagedClient`
  fields instead of `sse_exit_stack`. Three teardown paths, all
  `BaseException`-safe — an executor who copies only `except Exception` here
  reintroduces the orphan, so write them as shown in the design section:
  (i) the **ownership-transfer guard** around `await ready` + publication
  (cancel the owner and re-raise); (ii) the pre-`ready` failure, where the owner
  hands the exception back through `ready` and has already unwound its own
  stack; (iii) the post-connect failure (`_send_initialize` /
  `_index_capabilities` raises, around `:1541-1550`), which goes through
  `_close_remote_transport` — never `remote_stack.aclose()`, which no longer
  exists.
- `ClientManager.disconnect_server` (around `:862-878`) — **modify** — the
  `managed.is_remote` branch calls `await self._close_remote_transport(name, managed)`.
  The surrounding `except Exception as e: return (False, cancelled, str(e))`
  (`:876-878`) is preserved **and must remain reachable** — that is why
  `_close_remote_transport` does not swallow. **Contract decisions:** a timeout
  that escalates to cancel returns success with a WARNING log (the transport is
  closed either way, and `False` would make a dead-peer disconnect look like a
  refusal to the `gateway.disconnect_server` caller); a transport exit that
  raises for any other reason returns `(False, cancelled, str(e))` as today.
- `ClientManager._disconnect_all_unlocked._shutdown_one` (around `:2045-2056`) —
  **modify** — same replacement, relying on this function's existing
  `except Exception as e: logger.warning(...)` to swallow — this is one of the
  two sites that legitimately must not fail. The concurrent `asyncio.gather` becomes safe
  structurally: each close now happens in its own client's owner task, so
  `_shutdown_one` never touches a foreign cancel scope. Symptom 1 dies by
  construction, and the concurrency that exists for the 8 s stdio-reap budget
  (issue #79/1c) is preserved.
- `ClientManager._cleanup_client` (around `:2133-2146`) — **modify** — same
  replacement, keeping the documented never-raises contract: log and swallow any
  non-`CancelledError` exception. This is the reconnect path
  (`_connect_remote_stream`'s "existing live connection found" branch) and the
  `_reconnect_loop` path; it must not abort the recovery that called it.

### `tests/runtime/fake_remote.py` (modify)

- `run_fake_remote` `finally:` block (around `:109-111`) — **modify** — after
  `await task`, reset `sse_starlette.sse.AppStatus.should_exit = False`, with a
  comment naming the process-global latch, why it breaks the *next* event loop,
  and that the fixture that creates the poison is the one that clears it. Import
  `AppStatus` at module scope. **Nothing else in the repo may reset it** — one
  site, not a scattered autouse fixture.

### `tests/runtime/test_downstream_remote.py` (modify)

- Module docstring — **rewrite** — the current diagnosis is wrong in its
  conclusion (one root) and must not survive. Replace with: symptom 1 was pmcp
  task ownership (fixed in `manager.py`); symptom 2 was `sse_starlette`'s
  `AppStatus.should_exit` (fixed in `fake_remote.py`); cite the raw-httpx
  attribution so the next reader does not re-derive it. Delete the "do not split
  this file" instruction — the file is now split.
- `test_ec_p2_7_headers_redirect_and_reconnect_leak` — **delete**, replaced by:
- `test_ec_p2_7_configured_headers_reach_peer` — **add** — property 1 + the
  `gateway.invoke` wire proof; own `ClientManager`, own `run_fake_remote`,
  `disconnect_all()` teardown.
- `test_ec_p2_7_auth_gate_rejects_missing_and_wrong_header` — **add** —
  properties 2 + 2b.
- `test_ec_p2_7_follows_redirects` — **add** — property 3 (`/relocated` 307).
- `test_ec_p2_7_reconnect_does_not_leak_transports` — **add** — property 4. This
  is the one that fails today with the `CancelledError`; keep its existing
  socket-count `<=` comment (the SL-5 non-strict-comparison rationale) verbatim —
  it is hard-won and unrelated to this change.
- Every teardown uses `disconnect_all()`, and `pytest.fail`, never `pytest.skip`.

### `tests/test_client_manager.py` (modify)

- `test_disconnect_all_closes_remote_stack` (around `:628-652`) — **modify** —
  constructs `ManagedClient(sse_exit_stack=MagicMock())` at `:643`. Rewrite
  against the new fields: a real owner task parked on a real `asyncio.Event`, and
  assert the task completes after `disconnect_all()`.
- `test_disconnect_all_ignores_cancel_scope_task_mismatch` (around `:654-685`,
  constructs a stack at `:673`) — **delete** — it asserts the swallow that this
  change removes. Deleting a test that pins removed behaviour is correct; note it
  explicitly in the PR body so it is not mistaken for coverage loss.
- **Add** `test_close_remote_transport_escalates_to_cancel_on_timeout` — the
  owner ignores the shutdown event past the timeout; assert it is cancelled, the
  call returns, and the WARNING is logged.
- **Add** `test_disconnect_server_reports_failure_when_transport_exit_raises` —
  a transport whose `__aexit__` raises must yield `(False, cancelled, "<msg>")`,
  not `(True, ...)`. Without this the preserved contract at `:876-878` is
  unenforced no matter what the plan says.
- **Add** `test_connect_cancelled_during_ownership_transfer_leaves_no_owner` —
  the orphan regression. A fake transport signals an `asyncio.Event` from its
  `__aenter__`; the test awaits that signal (the connect task is then parked at
  `await ready`) and cancels the connect task. Assert the owner task is `done()`
  and that no task tagged with the server name survives in
  `manager._background_tasks`. Must be proven to fail without the
  ownership-transfer guard — the same revert-to-red discipline as the acceptance
  test, since a guard bug is silent by construction.

### `CHANGELOG.md` (modify)

- Unreleased section — **add** — one entry: remote downstream transports are now
  entered and closed in a dedicated per-client owner task, fixing the anyio
  cancel-scope task-ownership violation in teardown.

## Documentation impact

- `CHANGELOG.md` — modify — user-visible lifecycle behaviour change (see above).
- No other doc footprint: `README.md`, `CONTRIBUTING.md`, `SPEC_COMPLIANCE.md`,
  `AGENTS.md`/`CLAUDE.md` describe no part of the internal transport lifecycle,
  and no public API signature, tool schema, or wire contract changes. No frozen
  vocabulary or protocol contract is touched — `ManagedClient` is internal state,
  not a wire type.

---

## Dependencies & order

1. **Harness fix first** (`tests/runtime/fake_remote.py`). It is independent of
   the source fix and unblocks the split. Land it first so the red/green proof in
   step 3 isolates the source fix rather than confounding two causes.
2. **Split the test file** (`tests/runtime/test_downstream_remote.py`). With only
   step 1 in place this must be **3 passed / 1 failed**, the failure being the
   reconnect test with the `CancelledError` quoted in Finding 1. Capture that
   output — it is the mandated proof the regression test catches the defect.
3. **Source fix** (`src/pmcp/client/manager.py`), all sites in one commit. The
   three close sites and the connect path must change together; landing the owner
   task without converting a close site leaves that site closing a stack it no
   longer has a handle to.
4. **Unit-test updates** (`tests/test_client_manager.py`) — must land with step 3;
   they fail to import/construct otherwise.
5. `CHANGELOG.md`.

No external/blocking dependencies: no migrations, no dependency version changes,
no config surface.

---

## Verification

Run everything from `/mnt/workspace/worktrees/pmcp-plan-anyio`. Never bind,
signal, or restart `127.0.0.1:3344` — the live gateway serves real traffic as a
systemd user unit. Resolve its pid from the socket
(`ss -ltnpH 'sport = :3344'`) if a safety check is needed; never `pgrep -f`, never
a hardcoded pid. All fixtures use `alloc_port()` from `tests/runtime/harness.py`.

```bash
# 1. Acceptance: the four-way split, repeatedly.
uv run pytest tests/runtime/test_downstream_remote.py -p no:randomly -q   # x3
uv run pytest tests/runtime/test_downstream_remote.py -q                  # random order

# 2. Regression proof (MANDATORY — a passing test is not evidence the test works).
git stash push src/pmcp/client/manager.py
uv run pytest tests/runtime/test_downstream_remote.py -p no:randomly -q
#   MUST be red, and MUST fail with:
#   CancelledError: Cancelled via cancel scope <id> by <Task ... _shutdown_one() ...>
#   A failure with any other message means the test is catching something else.
git stash pop
uv run pytest tests/runtime/test_downstream_remote.py -p no:randomly -q   # green again

# 3. The transport lifecycle suites most likely to be perturbed.
uv run pytest tests/mcp2x/test_client_transport.py tests/test_client_manager.py \
              tests/test_client_manager_reconnect.py tests/runtime/ -q

# 4. The gate that actually runs in CI. Runtime acceptance is not evidence mypy agrees.
uv run mypy src/pmcp --exclude baml_client
uv run ruff check .
uv run ruff format --check .

# 5. Full suite. Baseline to preserve: 2498 passed / 3 skipped / 0 failed.
uv run pytest -q
```

Behaviours and edge cases to check by hand:

- `grep -rn "sse_exit_stack\|_is_cancel_scope_task_mismatch_error" src/ tests/`
  returns nothing.
- `grep -rn "cancel-scope mismatch\|different task" ` over a full
  `tests/runtime/` run at `--log-cli-level=DEBUG` returns nothing — the invariant
  now holds rather than being swallowed.
- The eight `disconnect_all()` call sites under `tests/` and the two
  `disconnect_server` call sites in `src/pmcp/tools/handlers.py` (`:2361`,
  `:2777`) still behave: covered by step 3, no source change expected in
  `handlers.py`.
- `tests/mcp2x/test_client_transport.py` monkeypatches the transport with a plain
  `@asynccontextmanager`; the owner task enters it exactly as the real one, so
  these are expected to pass **unchanged**. This is a verify step, not a change —
  if they need edits, the owner design has leaked into the transport contract and
  that must be reported, not patched around.
- Dead-peer teardown: shut `run_fake_remote` down before `disconnect_all()` and
  confirm teardown still completes inside the budget.
- stdio downstreams are untouched — `_terminate_process_tree` still runs on the
  non-remote branch of all three sites.

---

## Acceptance criteria

- [ ] `tests/runtime/test_downstream_remote.py` contains four independent
      `@pytest.mark.asyncio` tests, each with its own `ClientManager`, its own
      `run_fake_remote(alloc_port(), ...)`, and a `disconnect_all()` teardown, and
      `uv run pytest tests/runtime/test_downstream_remote.py -q` passes 3 runs in
      a row plus one random-order run.
- [ ] Reverting only `src/pmcp/client/manager.py` turns the reconnect test red
      with `CancelledError: Cancelled via cancel scope ... _shutdown_one()`;
      restoring it turns it green. Both outputs pasted into the PR body.
- [ ] Cancelling the connect task inside the ownership-transfer window leaves no
      surviving owner task, proven by a test that is red without the guard.
- [ ] A transport exit that raises still yields `(False, cancelled, "<msg>")`
      from `disconnect_server` — the `:876-878` contract is enforced by test, not
      by assertion in this document.
- [ ] `grep -rn "sse_exit_stack\|_is_cancel_scope_task_mismatch_error" src/ tests/`
      returns no matches, and a full `tests/runtime/` run at DEBUG logs no
      cancel-scope mismatch.
- [ ] `uv run mypy src/pmcp --exclude baml_client`, `uv run ruff check .`, and
      `uv run ruff format --check .` are clean.
- [ ] `uv run pytest -q` reports at least 2498 passed / 0 failed, with the skip
      count unchanged at 3 (a skipped test exits 0 and is not a pass).

---

## Explicitly NOT changing

This touches connection lifecycle for every downstream client — the riskiest
surface in the gateway. Out of scope, by name:

- **stdio lifecycle** — `_connect_stdio`, `adopt_process`,
  `_terminate_process_tree`, the stderr reader. stdio clients have no exit stack;
  the non-remote branch of all three close sites is untouched.
- **The read loop** — `_read_sse` stays a separately tracked task, cancelled
  before the transport closes exactly as today. Folding it into the owner is the
  obvious creep; rejected.
- **`_lifecycle_lock`**, `_connect_singleflight`, `_connect_with_retry`,
  `_reconnect_loop` back-off, `ensure_connected` — no changes to when or how
  often a connect happens, only to which task owns the transport it produces.
- **`_cancel_background_tasks` semantics**, the `_background_tasks` /
  `_connect_tasks` / `_reconnect_tasks` registries, and the self-cancel exclusion
  logic. The owner is registered through the existing
  `_track_background_task(task, name)` — no new registry.
- **Public API** — `connect_server`, `disconnect_server`, `disconnect_all`,
  `restart_server`, `refresh` keep their signatures and return contracts.
  `src/pmcp/tools/handlers.py` and `src/pmcp/server.py` need no edits.
- **`sse_starlette` / `mcp` / `httpx2` dependency versions** — the harness fix is
  version-independent.
- **`/health`** — never acceptance here, and unchanged.
- Nothing outside the worktree; nothing under `~/.pmcp`; no file deletions or
  moves outside `/mnt/workspace/worktrees/pmcp-plan-anyio`.

**Can this be done safely without restructuring something else?** Yes. The
change is contained to one class in one module plus one test fixture, and the
riskiest interaction — the concurrent `gather` in `_disconnect_all_unlocked`,
which exists for a real shutdown-budget reason — gets *safer*, not more coupled:
each close moves into its own client's owner task, so the concurrency no longer
crosses cancel-scope boundaries at all.

---

## Execution Policy

- execute: effort=high, reason=concurrency and task-lifetime semantics on the
  gateway's most load-bearing surface; the failure mode is silent

---

## Appendix A — reproduction log

Every run below was executed in this worktree at `9d2ac02` with the source
unmodified. Scratch files were deleted before commit; they are reproducible from
the descriptions.

| # | Setup | Result |
|---|---|---|
| 1 | Four-way split, `disconnect_all()` in `finally` (peer already down) | 2 passed / 2 failed — redirect + reconnect tests fail |
| 2 | Same, redirect test alone | 1 passed — no predecessor, no poison |
| 3 | Same, headers test then redirect test | redirect test **fails** |
| 4 | Same, auth-gate test (failed connects only) then redirect test | 2 passed — a failed connect does not poison |
| 5 | Connect + teardown, stack closed by a **foreign** task, peer still up | next loop passes |
| 6 | Connect + teardown, stack closed by a foreign task, **peer down first** | next loop **fails**, 3/3 |
| 7 | Same as 6 but stack closed by the **owner** task, 0 cancel-scope mismatches logged | next loop **fails**, 3/3 — ownership is irrelevant to symptom 2 |
| 8 | Same as 6 but **no teardown at all** (manager abandoned) | next loop **fails** — teardown is irrelevant to symptom 2 |
| 9 | Same as 8, next loop uses a **raw `httpx` initialize POST**, no pmcp client | **fails** with `RemoteProtocolError`; server logs `ASGI callable returned without completing response` — the poison is server-side |
| 10 | Print `AppStatus.should_exit` around the poisoning test | `False` on entry, `True` on exit |
| 11 | Same, resetting `AppStatus.should_exit = False` between tests | 2 failed / 1 passed → **3 passed** |
| 12 | `connect_server()` + `disconnect_all()`, peer alive: assert `remote_http_client.is_closed` | passes — the httpx client **does** close; P2's leak fix holds |
| 13 | Six connect/disconnect cycles, socket FD count | flat at 5 — no socket leak |
| 14 | Same, asyncio task count | +5/cycle — but all `sse_starlette._shutdown_watcher`, server-side, and identical under a correct owner-task close |
| 15 | Reconnect property, stack closed **in the entering task** | passes — owner ownership fixes symptom 1 |
| 16 | Four-way split (original late-teardown structure) **+ `AppStatus` reset** | **3 passed / 1 failed** — only the reconnect test, with the `CancelledError` |
