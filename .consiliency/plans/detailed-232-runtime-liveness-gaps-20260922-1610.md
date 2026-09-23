# Detailed plan: close the three server-hang runtime gaps in the client manager (C-01, C-02, C-04 of Consiliency/pmcp#232)

## Task

Fix the remainder of the 2026-09-01 codebase-review finding cluster in
`src/pmcp/client/manager.py` (see Consiliency/pmcp#232). C-03 is already fixed
in #281 and is out of scope. Close:

- **C-01** — a server→client JSON-RPC *request* (has both `method` and `id`) is
  silently dropped: the stdout/SSE dispatch gate only handles notifications, so
  a downstream `ping` never gets a reply and that server can block.
- **C-02** — `pending_requests` leaks when the **caller** of `_send_request` is
  cancelled: only `asyncio.TimeoutError` is caught and there is no `finally`.
- **C-04** — cancellation is never propagated downstream: `notifications/cancelled`
  appears nowhere in `src/pmcp/`.

All present to a user as "the server hangs" — liveness/protocol correctness,
not the trust boundary.

## Review resolution

Base commit for this plan: `37cffb0` (branch `plan/232-runtime-gaps`).

**Round-1 board (#283, DISAGREE + residuals) — fixed and re-cleared:**
- BLOCKING 1: cleanup scope widened — the `try/finally` in `_send_request` starts
  after registration and encloses the write *and* the wait; write errors still
  propagate.
- BLOCKING 2: bounded per-server outbound queue + one writer task; overflow drops.
- NON-BLOCKING 3: single-site dedup for `notifications/cancelled` on `gateway.cancel`.

**Round-2 board (re-panel: grok AGREE, gemini AGREE, codex PARTIALLY AGREE) — the
two items to close before merge, both fixed here:**
1. **Task leak of the outbound writer.** `_cleanup_client` (the reconnect
   teardown path) cancelled only `(read_task, stderr_task)`, so the `while True`
   writer leaked one task per reconnect generation. Fix: add `outbound_writer` to
   `_cleanup_client`'s cancel tuple; add an explicit guarded cancel in
   `disconnect_server` too. It is tracked via `_track_background_task(writer,
   name)` and recreated by `_enqueue_outbound` when the previous one is `done()`.
   Measured mutation below. (`disconnect_server` already swept it via
   `_cancel_background_tasks(server_name=name)`, `manager.py:1410` — the explicit
   cancel makes teardown independent of that sweep; the reconnect path was the
   real leak.)
2. **Verbatim test bodies embedded.** All 18 tests I ran this session are pasted
   under `## Test bodies (verbatim — run this session)` so the executor runs the
   measured tests, not paraphrases.

Cheap fold-ins (all applied): the `-32601` frame is spelled exactly (short
constant message `"Method not found"`); the reply/notify helpers never raise
(guarded internals) so a raise can't hit `_read_sse`'s blanket `except`; the
queue cap is mutation-tested; the SSE `-32601` twin is covered; a direct
caller-cancellation test is added; the timeout reason is `"timeout"` (the arm
fires for both idle and ceiling), not `"idle timeout"`; the tree citation is
`37cffb0`.

Undisturbed (praised): the `method`-first reorder + id-collision fix, `ping`→`{}`
/ `-32601` with the capability-gating rationale, the `requestId` wire shape from
vendored `mcp_types`, the `initialize` skip, and the "spec prose not vendored" flag.

## Research summary

All defects live in `src/pmcp/client/manager.py` (4035 lines), verified against
`37cffb0`:

- **Dispatch gate (C-01).** `_handle_stdout_line` (`:2777`, a plain `def`) and
  `_read_sse` (`:3000`) classify a frame as a *response* first
  (`msg_id in pending_requests`, `:2801` / `:3022`) and only then handle
  notifications; a frame with `method` **and** `id` (a server→client request)
  matches neither and vanishes.
- **Latent misrouting (C-01).** The response arm is keyed purely on
  `msg_id in pending_requests` without checking absence of `method`, so a
  downstream request whose id collides with one of ours (monotonic
  `_next_request_id`, `:1181`) is misrouted and its future resolved with an empty
  result. Correct discriminator: *a frame with a `method` is never a response.*
- **`_send_request` (C-02).** Registers `pending_requests[request_id]` (`:3101`)
  then awaits the write (`:3113`/`:3120`) **outside** any `try`, wrapping only the
  wait in `try/except asyncio.TimeoutError` (`:3135`) — no `except CancelledError`,
  no `finally`. `disconnect_server` (`:1317`) refuses at `:1323` while pending is
  non-empty; a corpse blocks it.
- **Cancellation propagation (C-04).** `grep -rn "notifications/cancelled" src/`
  is empty. `gateway.cancel`→`cancel_request` (`:3972`) and the idle path clean up
  locally only. `tasks/cancel` (`cancel_task`, `:3665`) is a different mechanism.
- **Bounded pattern to mirror.** `_schedule_reconcile` (`:2098-2117`) coalesces a
  chatty downstream to one in-flight + one queued and guards `create_task` with
  `except RuntimeError`. The outbound path follows the same shape.
- **Teardown paths.** `_cleanup_client` (reconnect, `:3504`) cancels a fixed
  tuple and does NOT call `_cancel_background_tasks`; `disconnect_server` DOES
  (`:1410`); `_disconnect_all_unlocked` cancels all (`:3471`).
- **Writes are atomic per frame.** Each frame is one synchronous `write(line)`, so
  frames can't byte-interleave while one-write-per-frame holds. No write lock.
- **Wire shapes, from vendored `mcp_types` 2.0.0 (verified, not memory):** `ping`
  → `EmptyResult` = `{}`; `notifications/cancelled` params serialise `requestId`
  (`_types.py:1893-1916`); `METHOD_NOT_FOUND=-32601` (`jsonrpc.py:100`) via
  `mcp_types.METHOD_NOT_FOUND` (`mcp.types` aliased `mcp_types`, `:24`);
  `jsonrpc_message_adapter.validate_python` accepts response/notification/error.
  `CancelledNotification` docstring: "can be sent by either side"; **"A client
  MUST NOT attempt to cancel its `initialize` request."**

## Design decisions

### C-01 — reply policy

Classify by `method` first in both paths (fixes the misrouting): `method`+no id →
notification (`_handle_downstream_notification`, unchanged); `method`+id →
server→client request → reply; else id in pending → response resolution (existing
block, moved into `elif`); else → ignore. Reply: `ping` →
`{"jsonrpc":"2.0","id":<id>,"result":{}}`; **any other** method →
`{"jsonrpc":"2.0","id":<id>,"error":{"code":mcp_types.METHOD_NOT_FOUND,
"message":"Method not found"}}` (short constant). We advertise no client
capabilities (`"capabilities": {}`, `:3193`), so `-32601` is correct and a
conscious refusal to forward an untrusted server's request to the agent. A frame
with a `method` can never resolve a pending future; a response (no `method`,
known id) resolves exactly as today.

**Could not confirm from the repo:** the MCP spec prose for which server→client
requests an empty-capabilities client must accept is not vendored; the decision
derives from the capability gating we send plus `ping`'s base-protocol status.
Flagged, not guessed.

### C-04 — when `notifications/cancelled` is sent

Shape (from `mcp_types`): `{"jsonrpc":"2.0","method":"notifications/cancelled",
"params":{"requestId":<the id we sent downstream>,"reason":<str>}}` — no id of its
own. Sent through the bounded outbound path on: `gateway.cancel` (`cancel_request`,
`reason="cancelled via gateway.cancel"`); idle/ceiling timeout (`_send_request`
`except TimeoutError`, `reason="timeout"` — the arm covers both idle and ceiling,
so the reason is neutral); caller cancellation (`_send_request` `except
CancelledError`, `reason="caller cancelled"`, **only when `request_id` is still in
`pending_requests`** — dedup). `_schedule_cancelled_notification` returns without
sending when `method == "initialize"` (spec MUST). Not sent on
disconnect/bulk-cancel (they pop before the future-cancel reaches `_send_request`,
so the dedup guard suppresses).

### BLOCKING 2 — bounded per-server outbound path

One `asyncio.Queue(maxsize=_OUTBOUND_QUEUE_MAXSIZE=256)` + one writer task per
`ManagedClient`, lazily created, mirroring `_schedule_reconcile`'s no-loop guard:
- `_enqueue_outbound(name, managed, frame)` (sync) — skip if
  `is_remote and write_stream is None`; create queue/writer lazily
  (`create_task` guarded by `except RuntimeError` → logged drop); `put_nowait`;
  on `QueueFull` warn + **drop** (overflow policy).
- `_drain_outbound` (async, one writer) — `while True: await
  self._send_message_to_downstream(await queue.get())`; blocks on a stalled sink,
  bounding to `maxsize` queued + 1 in-flight, **one** writer regardless of flood.
- `_send_message_to_downstream(managed, payload)` (async) — the one guarded
  writer; `try/except Exception` logs + returns (dead-pipe guard). Only for
  fire-and-forget frames; `_send_request`'s own request write stays inline so its
  errors keep propagating.

**Helpers never raise.** `_reply_to_downstream_request` /
`_schedule_cancelled_notification` build a dict (cannot raise) and call
`_enqueue_outbound`, whose `create_task` and `put_nowait` are both guarded; the
writer swallows write errors. This matches `_handle_downstream_notification`'s
contract, so a reply/notify can never hit `_read_sse`'s blanket `except Exception`
and trigger a spurious reconnect.

### Writer lifecycle (round-2 fix)

`_cleanup_client` (reconnect teardown) cancels `(read_task, stderr_task,
outbound_writer)`; `disconnect_server` cancels `outbound_writer` explicitly (in
addition to the existing `_cancel_background_tasks` sweep). The writer is
recreated on demand by `_enqueue_outbound` when the prior one is `done()`.

### C-02 ↔ C-04 ordering

```
managed.pending_requests[request_id] = pending
...
try:
    <send: remote write_stream.send() OR stdio write()+drain()>   # inside try now
    result = await self._await_with_idle_timeout(...)
    return result
except asyncio.TimeoutError:
    self._schedule_cancelled_notification(managed, request_id, method, "timeout")
    raise TimeoutError(f"Request {method} timed out")
except asyncio.CancelledError:
    if request_id in managed.pending_requests:                    # dedup
        self._schedule_cancelled_notification(managed, request_id, method, "caller cancelled")
    raise
finally:
    managed.pending_requests.pop(request_id, None)                # C-02 unconditional
    managed.status.pending_request_count = len(managed.pending_requests)
```

C-02 is the invariant; C-04 is *enqueued* (never awaited) so it cannot skip the
`finally` pop. A write error (`BrokenPipeError` from `drain()`) is neither
`TimeoutError` nor `CancelledError` → propagates untouched, and `finally` still
pops. On success the reader already popped (ids monotonic, never reused) → the
pop is a no-op. `cancel_request` pops **before** `future.cancel()` and schedules
its own notification, so the dedup guard suppresses `_send_request`'s.

## Changes

### `src/pmcp/client/manager.py` (modify)

- `_OUTBOUND_QUEUE_MAXSIZE = 256` — **add** (near `_STDIO_CHUNK_SIZE`).
- `PendingRequest` (`:952`) — **add** `method: str = ""`.
- `ManagedClient` — **add** `outbound: asyncio.Queue[dict[str, Any]] | None = None`,
  `outbound_writer: asyncio.Task[None] | None = None`.
- **add** `_send_message_to_downstream`, `_drain_outbound`, `_enqueue_outbound`,
  `_reply_to_downstream_request` (`ping`→`{}` / `-32601` "Method not found"),
  `_schedule_cancelled_notification` (`initialize` skip).
- `_handle_stdout_line` (`:2777`, stays `def`) / `_read_sse` (`:3000`) —
  **modify** the dispatch to classify by `method` first (C-01).
- `_send_request` (`:3071`) — **modify**: `method=method` on the PendingRequest;
  move the send inside the `try`; `except TimeoutError` (C-04 "timeout") /
  `except CancelledError` (C-04 + dedup) / `finally` pop (C-02).
- `cancel_request` (`:3972`) — **modify**: pop before `future.cancel()`; schedule
  the notification (C-04).
- `_cleanup_client` (`:3504`) — **modify**: add `outbound_writer` to the cancel
  tuple. `disconnect_server` (`:1317`) — **modify**: explicit guarded
  `outbound_writer.cancel()` after the read-task cancel.

### `tests/test_client_manager.py` (modify)

Add the 18 tests under `## Test bodies` verbatim (node ids validated with
`--collect-only`). Do **not** modify `tests/conftest.py`. The existing guards
(`:3860,4867,4908,4954,4997`) stay green (measured).

### `CHANGELOG.md` (modify)

- Under `## [Unreleased]` → `### Fixed` (`:369`) — **add** one entry covering: a
  downstream request is answered (`ping`→empty result, else `-32601`); no
  `pending_requests` leak on cancellation or a mid-write error; downstream
  `notifications/cancelled` on gateway.cancel / timeout / caller-cancel; bounded
  per-server reply/notify writes with a leak-free writer lifecycle. `see #232`.

## Documentation impact

- `CHANGELOG.md` — modify — as above. No other cross-cutting doc.
  `specs/phase-plans-v13.md` / `plans/phase-plan-v13-*.md` — **not** touched.

## Frozen-vocabulary / protocol confirmation

`_handle_downstream_notification` (IF-0-FANOUT-1, `:2028`) is frozen (sync, fixed
signature + docstring, guarded by `test_reconcile_contract_entry_point_has_frozen_signature`).
Untouched. No new downstream vocabulary: `notifications/cancelled`, `ping`,
`EmptyResult`, `METHOD_NOT_FOUND` are standard MCP/JSON-RPC identifiers from
vendored `mcp_types` 2.0.0. The reorder keeps the notification arm calling
`_handle_downstream_notification` unchanged (measured green).

## Dependencies & order

1. constant, `PendingRequest.method`, `ManagedClient.outbound*`.
2. `_send_message_to_downstream`→`_drain_outbound`→`_enqueue_outbound`→
   `_reply_to_downstream_request`/`_schedule_cancelled_notification`.
3. C-01 reorder (`_handle_stdout_line`/`_read_sse`).
4. `_send_request` restructure; `cancel_request` C-04.
5. Writer teardown in `_cleanup_client` + `disconnect_server`.
6. Tests. No external/migration dependencies.

## Verification

```bash
uv run pytest tests/test_client_manager.py \
  -k "c01_ or c02_ or c04_ or stalled_sink or outbound_queue or outbound_writer" \
  -p no:cacheprovider --cov-fail-under=0 --timeout=40 -q
uv run pytest tests/test_client_manager.py \
  -k "frozen_signature or unknown_notification_is_a_noop or typed_downstream_error_preserves" \
  --cov-fail-under=0 --timeout=40 -q
grep -rn "notifications/cancelled" src/pmcp/          # only the new call sites
uv run pytest tests/test_client_manager.py tests/test_client_manager_reconnect.py \
  tests/runtime/ tests/mcp2x/ --cov-fail-under=0 --timeout=300 -q
uv run python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md   # 0 blocking
```

## Acceptance criteria

Every criterion was MEASURED this run against the in-tree fix (a throwaway spike,
reverted byte-identically — `diff` confirmed; `manager.py` sha back to
`00d8f206…`) via mutation testing: 18/18 GREEN with the fix, plus the 5 existing
guards; RED under each named mutation (confirmed applied by `diff`). See
`## Measured mutation results`.

- [ ] C-01a — `ping` (stdio **and** SSE) → `{"jsonrpc":"2.0","id":<id>,"result":{}}`
  (`test_c01_stdio_ping_gets_empty_result_reply`, `test_c01_sse_ping_gets_empty_result_reply`).
- [ ] C-01b — a colliding-id request does not resolve our pending future
  (`test_c01_collision_request_does_not_resolve_our_pending`).
- [ ] C-01c — an unsupported request → `-32601` "Method not found", stdio **and**
  SSE (`test_c01_unsupported_request_gets_method_not_found`,
  `test_c01_sse_unsupported_request_gets_method_not_found`).
- [ ] C-02 — the entry is popped on caller-cancel during the wait, during the
  stdio `drain()`, during the remote `send()`, and when the write raises
  `BrokenPipeError` (which still propagates)
  (`test_c02_caller_cancellation_pops_pending_entry`,
  `test_c02_cancel_during_stdio_drain_pops_pending`,
  `test_c02_cancel_during_remote_send_pops_pending`,
  `test_c02_write_error_pops_pending_and_propagates`).
- [ ] C-04 — `notifications/cancelled` (`params.requestId == <id>`) on
  `gateway.cancel` and timeout; **not** for `initialize`; **exactly once** on
  `gateway.cancel`; and exactly once (reason `"caller cancelled"`) on a direct
  caller-cancel (`test_c04_gateway_cancel_notifies_downstream`,
  `test_c04_idle_timeout_notifies_downstream`,
  `test_c04_initialize_request_is_never_cancelled_downstream`,
  `test_c04_gateway_cancel_sends_exactly_one_notification`,
  `test_c04_direct_caller_cancel_notifies_once`).
- [ ] BLOCKING-2 — a stalled sink flooded with 400 pings yields ≤8 live outbound
  tasks, and the queue never exceeds its cap
  (`test_stalled_sink_does_not_allocate_unbounded_outbound_tasks`,
  `test_outbound_queue_respects_maxsize_under_stall`).
- [ ] Writer lifecycle — the outbound writer does not survive `_cleanup_client`
  (reconnect) or `disconnect_server`
  (`test_reconnect_cleanup_cancels_outbound_writer`,
  `test_disconnect_server_cancels_outbound_writer`).

## Measured mutation results

Fix in place: **18/18 probe GREEN**; 5 existing guards GREEN; probe + reconnect +
`test_client_manager` = **252 passed**. Each mutation RED for the right reason
(mutation confirmed applied by `diff` before trusting the run):

| Criterion | Mutation | Measured RED result |
|---|---|---|
| C-02 windows | narrow the `try` back to response-wait only (= pristine tree) | 3 window tests fail: `pending_requests` still holds the entry; the write-error test confirms `BrokenPipeError` still propagates |
| BLOCKING-2 bound | `_enqueue_outbound` → per-frame `create_task(_send_message_to_downstream)` | `test_stalled_sink...`: `unbounded outbound tasks under a stalled sink: 400` / `assert 400 <= 8` |
| BLOCKING-2 cap | `asyncio.Queue(maxsize=_OUTBOUND_QUEUE_MAXSIZE)` → `asyncio.Queue()` | `test_outbound_queue_respects_maxsize_under_stall`: `qsize 455 <= 256` fails |
| NON-BLOCKING 3 dedup | drop the `if request_id in managed.pending_requests` guard | `test_c04_gateway_cancel_sends_exactly_one_notification`: two frames, `assert 2 == 1` |
| Writer leak | remove `outbound_writer` from `_cleanup_client`'s tuple | `test_reconnect_cleanup_cancels_outbound_writer`: `outbound writer leaked across _cleanup_client` (task still pending); `test_disconnect_server_cancels_outbound_writer` stays green (its own sweep), confirming the two paths |

Additional mutations the executor should re-run after green (restore from a saved
`cp`, `diff` byte-identical — never `git checkout --`): revert the `ping` branch
to the error frame (C-01a red); revert the `method`-first reorder (C-01b red —
`fut.done()` True; also breaks the SSE `-32601` twin); comment out the
idle-timeout `_schedule_cancelled_notification` (C-04 timeout red); remove the
`method == "initialize"` early return (C-04 init guard red).

## Execution Policy

- execute: effort=high, reason=concurrency + protocol correctness on an
  untrusted-server boundary; cancellation ordering, a bounded outbound writer with
  its own lifecycle, and a hot dispatch-path reorder.

## Test bodies (verbatim — run this session)

Drop these into `tests/test_client_manager.py` (or a new module importing the same
symbols). All 18 were RUN this session: green against the fix, red under the
mutations above.

```python
"""Acceptance tests for Consiliency/pmcp#232 C-01/C-02/C-04."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pmcp.client.manager import (
    _OUTBOUND_QUEUE_MAXSIZE,
    ClientManager,
    ManagedClient,
    PendingRequest,
)
from pmcp.types import ServerStatus, ServerStatusEnum
from tests._timing import eventually


def _managed_stdio(name: str) -> ManagedClient:
    status = ServerStatus(name=name, status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
    managed.config.name = name
    managed.is_remote = False
    managed.process.stdin.write = MagicMock()
    managed.process.stdin.drain = AsyncMock()
    return managed


def _managed_remote(name: str, sent: list[Any]) -> ManagedClient:
    status = ServerStatus(name=name, status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=MagicMock(), process=None, status=status)
    managed.config.name = name
    managed.is_remote = True
    ws = MagicMock()
    ws.send = AsyncMock(side_effect=lambda m: sent.append(m))
    managed.write_stream = ws
    return managed


def _sent_frames(managed: ManagedClient) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for call in managed.process.stdin.write.call_args_list:
        try:
            frames.append(json.loads(call.args[0].decode()))
        except Exception:
            pass
    return frames


def _dumped(sent: list[Any]) -> list[dict[str, Any]]:
    return [
        m.message.model_dump(by_alias=True, mode="json", exclude_none=True)
        for m in sent
    ]


async def _block(*_a: Any, **_k: Any) -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_c01_stdio_ping_gets_empty_result_reply() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    mgr._clients["srv"] = managed
    line = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}).encode()
    mgr._handle_stdout_line("srv", managed, line, time.time())
    await eventually(
        lambda: managed.process.stdin.write.called,
        timeout=2.0,
        interval=0.005,
        message="no reply written for downstream ping",
    )
    assert {"jsonrpc": "2.0", "id": 99, "result": {}} in _sent_frames(managed)


@pytest.mark.asyncio
async def test_c01_sse_ping_gets_empty_result_reply() -> None:
    mgr = ClientManager()
    sent: list[Any] = []
    managed = _managed_remote("srv", sent)
    mgr._clients["srv"] = managed
    mgr._schedule_reconnect = MagicMock()  # type: ignore[method-assign]

    def _frame(payload: dict[str, Any]) -> Any:
        frame = MagicMock()
        frame.message.model_dump.return_value = payload
        return frame

    async def stream() -> Any:
        yield _frame({"jsonrpc": "2.0", "id": 5, "method": "ping"})

    await mgr._read_sse("srv", managed, stream())
    await eventually(
        lambda: bool(sent), timeout=2.0, interval=0.005, message="no SSE reply sent"
    )
    assert _dumped(sent)[0] == {"jsonrpc": "2.0", "id": 5, "result": {}}


@pytest.mark.asyncio
async def test_c01_collision_request_does_not_resolve_our_pending() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    mgr._clients["srv"] = managed
    fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
    now = time.time()
    managed.pending_requests[42] = PendingRequest(
        request_id=42,
        server_name="srv",
        tool_id="srv::x",
        started_at=now,
        last_heartbeat=now,
        timeout_ms=30000,
        future=fut,
    )
    line = json.dumps({"jsonrpc": "2.0", "id": 42, "method": "ping"}).encode()
    mgr._handle_stdout_line("srv", managed, line, now)
    await asyncio.sleep(0.05)
    assert not fut.done(), "server->client request misrouted as a response"
    assert 42 in managed.pending_requests


@pytest.mark.asyncio
async def test_c01_unsupported_request_gets_method_not_found() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    mgr._clients["srv"] = managed
    line = json.dumps(
        {"jsonrpc": "2.0", "id": 7, "method": "sampling/createMessage", "params": {}}
    ).encode()
    mgr._handle_stdout_line("srv", managed, line, time.time())
    await eventually(
        lambda: managed.process.stdin.write.called,
        timeout=2.0,
        interval=0.005,
        message="no error reply for unsupported downstream request",
    )
    match = [f for f in _sent_frames(managed) if f.get("id") == 7 and "error" in f]
    assert match, _sent_frames(managed)
    assert match[0]["error"]["code"] == -32601, match
    assert match[0]["error"]["message"] == "Method not found", match


@pytest.mark.asyncio
async def test_c01_sse_unsupported_request_gets_method_not_found() -> None:
    mgr = ClientManager()
    sent: list[Any] = []
    managed = _managed_remote("srv", sent)
    mgr._clients["srv"] = managed
    mgr._schedule_reconnect = MagicMock()  # type: ignore[method-assign]

    def _frame(payload: dict[str, Any]) -> Any:
        frame = MagicMock()
        frame.message.model_dump.return_value = payload
        return frame

    async def stream() -> Any:
        yield _frame({"jsonrpc": "2.0", "id": 8, "method": "roots/list", "params": {}})

    await mgr._read_sse("srv", managed, stream())
    await eventually(
        lambda: bool(sent), timeout=2.0, interval=0.005, message="no SSE error reply"
    )
    frame = _dumped(sent)[0]
    assert frame["id"] == 8 and frame["error"]["code"] == -32601, frame
    assert frame["error"]["message"] == "Method not found", frame


@pytest.mark.asyncio
async def test_c02_caller_cancellation_pops_pending_entry() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    mgr._clients["srv"] = managed
    task = asyncio.create_task(
        mgr._send_request(managed, "tools/call", {}, timeout_ms=60000)
    )
    await eventually(
        lambda: len(managed.pending_requests) == 1,
        timeout=2.0,
        interval=0.005,
        message="request never registered",
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert managed.pending_requests == {}, "pending_requests leaked on cancellation"


@pytest.mark.asyncio
async def test_c02_cancel_during_stdio_drain_pops_pending() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    managed.process.stdin.drain = _block  # type: ignore[assignment]
    mgr._clients["srv"] = managed
    task = asyncio.create_task(
        mgr._send_request(managed, "tools/call", {}, timeout_ms=60000)
    )
    await eventually(
        lambda: len(managed.pending_requests) == 1,
        timeout=2.0,
        interval=0.005,
        message="request never registered",
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert managed.pending_requests == {}, "leaked when cancelled during drain()"


@pytest.mark.asyncio
async def test_c02_cancel_during_remote_send_pops_pending() -> None:
    mgr = ClientManager()
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=MagicMock(), process=None, status=status)
    managed.config.name = "srv"
    managed.is_remote = True
    ws = MagicMock()
    ws.send = _block
    managed.write_stream = ws
    mgr._clients["srv"] = managed
    task = asyncio.create_task(
        mgr._send_request(managed, "tools/call", {}, timeout_ms=60000)
    )
    await eventually(
        lambda: len(managed.pending_requests) == 1,
        timeout=2.0,
        interval=0.005,
        message="request never registered",
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert managed.pending_requests == {}, "leaked when cancelled during send()"


@pytest.mark.asyncio
async def test_c02_write_error_pops_pending_and_propagates() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    managed.process.stdin.drain = AsyncMock(side_effect=BrokenPipeError("dead"))
    mgr._clients["srv"] = managed
    with pytest.raises(BrokenPipeError):
        await mgr._send_request(managed, "tools/call", {}, timeout_ms=60000)
    assert managed.pending_requests == {}, "leaked when the write raised"


@pytest.mark.asyncio
async def test_c04_gateway_cancel_notifies_downstream() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    mgr._clients["srv"] = managed
    fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
    now = time.time()
    managed.pending_requests[5] = PendingRequest(
        request_id=5,
        server_name="srv",
        tool_id="srv::x",
        started_at=now - 100,
        last_heartbeat=now - 100,
        timeout_ms=1000,
        future=fut,
        method="tools/call",
    )
    status, _m, _s, _e = await mgr.cancel_request("srv::5", force=True)
    assert status == "cancelled"
    await eventually(
        lambda: any(
            f.get("method") == "notifications/cancelled" for f in _sent_frames(managed)
        ),
        timeout=2.0,
        interval=0.005,
        message="no notifications/cancelled sent on gateway.cancel",
    )
    notif = [
        f for f in _sent_frames(managed) if f.get("method") == "notifications/cancelled"
    ][0]
    assert notif["params"]["requestId"] == 5, notif


@pytest.mark.asyncio
async def test_c04_idle_timeout_notifies_downstream() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    mgr._clients["srv"] = managed
    with pytest.raises(TimeoutError):
        await mgr._send_request(managed, "tools/list", {}, timeout_ms=20)
    await eventually(
        lambda: any(
            f.get("method") == "notifications/cancelled" for f in _sent_frames(managed)
        ),
        timeout=2.0,
        interval=0.005,
        message="no notifications/cancelled sent on idle timeout",
    )


@pytest.mark.asyncio
async def test_c04_initialize_request_is_never_cancelled_downstream() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    mgr._clients["srv"] = managed
    fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
    now = time.time()
    managed.pending_requests[3] = PendingRequest(
        request_id=3,
        server_name="srv",
        tool_id="",
        started_at=now - 100,
        last_heartbeat=now - 100,
        timeout_ms=1000,
        future=fut,
        method="initialize",
    )
    status, _m, _s, _e = await mgr.cancel_request("srv::3", force=True)
    assert status == "cancelled"
    await asyncio.sleep(0.1)
    frames = _sent_frames(managed)
    assert not any(f.get("method") == "notifications/cancelled" for f in frames), frames


@pytest.mark.asyncio
async def test_c04_gateway_cancel_sends_exactly_one_notification() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    mgr._clients["srv"] = managed
    task = asyncio.create_task(
        mgr._send_request(managed, "tools/call", {}, timeout_ms=60000)
    )
    await eventually(
        lambda: len(managed.pending_requests) == 1,
        timeout=2.0,
        interval=0.005,
        message="request never registered",
    )
    req_id = next(iter(managed.pending_requests))
    status, _m, _s, _e = await mgr.cancel_request(f"srv::{req_id}", force=True)
    assert status == "cancelled"
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    notifs = [
        f for f in _sent_frames(managed) if f.get("method") == "notifications/cancelled"
    ]
    assert len(notifs) == 1, f"expected exactly one cancelled notification: {notifs}"


@pytest.mark.asyncio
async def test_c04_direct_caller_cancel_notifies_once() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    mgr._clients["srv"] = managed
    task = asyncio.create_task(
        mgr._send_request(managed, "tools/call", {}, timeout_ms=60000)
    )
    await eventually(
        lambda: len(managed.pending_requests) == 1,
        timeout=2.0,
        interval=0.005,
        message="request never registered",
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await eventually(
        lambda: any(
            f.get("method") == "notifications/cancelled" for f in _sent_frames(managed)
        ),
        timeout=2.0,
        interval=0.005,
        message="no notifications/cancelled on direct caller cancellation",
    )
    notifs = [
        f for f in _sent_frames(managed) if f.get("method") == "notifications/cancelled"
    ]
    assert len(notifs) == 1, notifs
    assert notifs[0]["params"]["reason"] == "caller cancelled", notifs


@pytest.mark.asyncio
async def test_stalled_sink_does_not_allocate_unbounded_outbound_tasks() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    managed.process.stdin.drain = _block  # type: ignore[assignment]
    mgr._clients["srv"] = managed
    for i in range(400):
        line = json.dumps({"jsonrpc": "2.0", "id": 1000 + i, "method": "ping"}).encode()
        mgr._handle_stdout_line("srv", managed, line, time.time())
    await asyncio.sleep(0.15)
    live = [t for t in mgr._background_tasks if not t.done()]
    assert len(live) <= 8, f"unbounded outbound tasks under a stalled sink: {len(live)}"


@pytest.mark.asyncio
async def test_outbound_queue_respects_maxsize_under_stall() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    managed.process.stdin.drain = _block  # type: ignore[assignment]
    mgr._clients["srv"] = managed
    for i in range(_OUTBOUND_QUEUE_MAXSIZE + 200):
        line = json.dumps({"jsonrpc": "2.0", "id": 2000 + i, "method": "ping"}).encode()
        mgr._handle_stdout_line("srv", managed, line, time.time())
    await asyncio.sleep(0.15)
    assert managed.outbound is not None
    assert managed.outbound.qsize() <= _OUTBOUND_QUEUE_MAXSIZE, managed.outbound.qsize()


@pytest.mark.asyncio
async def test_reconnect_cleanup_cancels_outbound_writer() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    managed.process.stdin.drain = _block  # type: ignore[assignment]
    managed.process.returncode = 0  # let _terminate_process_tree early-return
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    mgr._reply_to_downstream_request("srv", managed, 1, "ping")
    await eventually(
        lambda: managed.outbound_writer is not None,
        timeout=2.0,
        interval=0.005,
        message="writer never started",
    )
    writer = managed.outbound_writer
    assert writer is not None
    await mgr._cleanup_client("srv", managed)
    assert writer.done(), "outbound writer leaked across _cleanup_client"


@pytest.mark.asyncio
async def test_disconnect_server_cancels_outbound_writer() -> None:
    mgr = ClientManager()
    managed = _managed_stdio("srv")
    managed.process.stdin.drain = _block  # type: ignore[assignment]
    managed.process.returncode = 0
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    mgr._reply_to_downstream_request("srv", managed, 1, "ping")
    await eventually(
        lambda: managed.outbound_writer is not None,
        timeout=2.0,
        interval=0.005,
        message="writer never started",
    )
    writer = managed.outbound_writer
    assert writer is not None
    ok, _cancelled, _msg = await mgr.disconnect_server("srv", force=True)
    assert ok
    assert writer.done(), "outbound writer leaked across disconnect_server"
```
