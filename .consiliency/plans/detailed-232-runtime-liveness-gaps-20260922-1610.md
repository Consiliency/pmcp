# Detailed plan: close the three server-hang runtime gaps in the client manager (C-01, C-02, C-04 of Consiliency/pmcp#232)

## Task

Fix the remainder of the 2026-09-01 codebase-review finding cluster in
`src/pmcp/client/manager.py` (see Consiliency/pmcp#232). C-03 is already fixed
in #281 and is out of scope. Close:

- **C-01** — a server→client JSON-RPC *request* (has both `method` and `id`) is
  silently dropped: the stdout/SSE dispatch gate only handles notifications, so
  a downstream `ping` never gets a reply and that server can block.
- **C-02** — `pending_requests` leaks when the **caller** of `_send_request` is
  cancelled: only `asyncio.TimeoutError` is caught and there is no `finally`, so
  the entry is never popped and `disconnect_server`'s "refuse while pending" is
  held hostage by a corpse.
- **C-04** — cancellation is never propagated downstream: `notifications/cancelled`
  appears nowhere in `src/pmcp/`; `gateway.cancel` and the idle timeout give up
  locally and never tell the downstream server to stop.

All three present to a user as "the server hangs" — this is liveness/protocol
correctness, not the trust boundary.

## Research summary

All three defects live in `src/pmcp/client/manager.py` (4035 lines) and were
verified against this tree (`plan/232-runtime-gaps` @ `5321274`):

- **Dispatch gate (C-01).** `_handle_stdout_line` (`manager.py:2777`, a plain
  `def`) and its SSE twin `_read_sse` (`manager.py:3000`) both classify a frame
  as a *response* first (`msg_id is not None and msg_id in pending_requests`,
  lines 2801 / 3022) and only then fall through to `msg_id is None and
  isinstance(method, str)` for notifications (lines 2825 / 3047). A frame that
  carries a `method` **and** an `id` (a server→client request) matches neither
  arm and vanishes. The comment at 2820-2823 acknowledges this as deliberate.
- **Latent misrouting correlated with C-01.** Because the response arm is keyed
  purely on `msg_id in pending_requests` and does **not** check for the absence
  of `method`, a downstream request whose `id` collides with one of our
  outstanding request ids (our ids come from a per-server monotonic counter,
  `_next_request_id`, `manager.py:1181`) is misrouted: line 2802/2818 pops our
  pending entry and resolves its future with `message.get("result", {})` — i.e.
  an empty result — even though the frame is a request, not a response. Proven
  red below. The correct discriminator is *"a frame with a `method` is a
  request/notification, never a response."*
- **`_send_request` / `_await_with_idle_timeout` (C-02).** `_send_request`
  (`manager.py:3071`) wraps the await in `try / except asyncio.TimeoutError`
  (pops at 3146) with **no** `except CancelledError` and **no** `finally`.
  `_await_with_idle_timeout` (`manager.py:3150`) waits on
  `asyncio.wait_for(asyncio.shield(future), slice_s)`; `shield` protects the
  *future*, but the surrounding await is cancelled and `CancelledError`
  propagates out of both functions, leaving `pending_requests[request_id]` in
  place. `disconnect_server` (`manager.py:1317`) refuses at 1323 whenever
  `get_pending_requests` is non-empty and not forced — so a leaked corpse blocks
  disconnect.
- **Cancellation propagation (C-04).** `grep -rn "notifications/cancelled" src/`
  returns nothing. `gateway.cancel` → `handlers.py:6636` `cancel()` →
  `manager.cancel_request` (`manager.py:3972`) cancels the future and pops the
  entry locally (4028-4030) and returns; nothing is sent downstream. The
  idle/ceiling path (`_send_request` `except asyncio.TimeoutError`, 3145-3148)
  likewise only cleans up locally. The only downstream cancel that exists is the
  `tasks/cancel` **request** proxy (`cancel_task`, `manager.py:3665`), which is
  the MCP tasks extension — a different mechanism.
- **No outbound write helper / no write lock.** Outbound frames are hand-rolled
  in `_send_request` (3105-3120) and `_send_initialize` (3227-3235): remote via
  `managed.write_stream.send(SessionMessage(msg))`, local via one
  `managed.process.stdin.write(...)` + `await ...stdin.drain()`. There is no
  shared helper and no write lock, but each frame is written with a **single**
  synchronous `write(full_line_with_newline)` call, which is atomic with respect
  to the event loop — so concurrent writers cannot byte-interleave as long as
  that one-write-per-frame discipline is kept.
- **Wire shapes, confirmed from the vendored `mcp_types` 2.0.0 package (not
  memory):**
  - `ping` request → `PingRequest`; its result type is `EmptyResult`, which
    serialises to `{}` (`mcp_types/methods.py:63,239`; verified
    `EmptyResult().model_dump(by_alias=True, mode="json", exclude_none=True) == {}`).
  - `notifications/cancelled` → `CancelledNotification` with
    `CancelledNotificationParams(request_id, reason)`; `request_id` serialises
    **`requestId`** on the wire (`_types.py:1893-1916`; verified
    `model_dump(by_alias=True) == {"method": "notifications/cancelled",
    "params": {"requestId": 7, "reason": "..."}}`). The docstring states the
    notification "can be sent by either side" and that **"A client MUST NOT
    attempt to cancel its `initialize` request."**
  - JSON-RPC error codes: `METHOD_NOT_FOUND = -32601` (`mcp_types/jsonrpc.py:100`),
    reachable in the manager's namespace as `mcp_types.METHOD_NOT_FOUND`
    (`mcp.types` is imported aliased as `mcp_types`, `manager.py:24`).
  - `mcp_types.jsonrpc_message_adapter.validate_python(...)` accepts a
    response, a notification, and an error frame (all three verified), so the
    remote path can reuse it uniformly.

## Design decisions (the two wire-surface changes, made explicitly)

### C-01 — what we reply, and to which methods

We are brokering an **untrusted** downstream server, so a reply is an outbound
write we now perform on its behalf; we keep that write minimal and never proxy a
server→client request to the agent.

1. **Classify by `method` first, response second** (fixes the latent
   misrouting). New order in *both* dispatch paths:
   - `isinstance(method, str)` **and** `msg_id is None` → notification →
     `_handle_downstream_notification` (unchanged behaviour).
   - `isinstance(method, str)` **and** `msg_id is not None` → server→client
     **request** → reply (new, C-01).
   - else if `msg_id is not None and msg_id in pending_requests` → response
     resolution (the existing 2801-2818 / 3022-3039 block, moved into the `elif`).
   - else → an unmatched response (unknown/duplicate id) → ignore, as today.
2. **What we reply:**
   - `method == "ping"` → success response `{"jsonrpc":"2.0","id":<id>,"result":{}}`
     (`EmptyResult`). `ping` is base-protocol and not capability-gated, so a
     downstream may legitimately send it; answering it is the liveness fix.
   - **any other** method (`sampling/createMessage`, `roots/list`,
     `elicitation/create`, or anything unknown) → JSON-RPC error
     `{"jsonrpc":"2.0","id":<id>,"error":{"code":-32601,"message":...}}`. We
     advertise **no** client capabilities in `initialize`
     (`"capabilities": {}`, `manager.py:3193`), so a well-behaved server should
     not send these; `-32601 method not found` is the correct, safe answer and
     avoids forwarding an untrusted server's prompt/sampling request to the
     agent. This is a conscious refusal to implement sampling/roots/elicitation
     at the gateway boundary, not an oversight.
3. **Request whose `id` IS in `pending_requests`:** with the reorder, a frame
   carrying a `method` is *always* treated as a request/notification, so it can
   never resolve a pending future even on an id collision. A frame *without* a
   `method` and with a known id still resolves its pending future exactly as
   today (the response arm is unchanged, only moved after the method check).
4. **Dead-pipe risk:** the reply is a fire-and-forget outbound write; if the
   process already exited it must not tear down the read loop. It is sent
   through the new guarded helper (below), which swallows and logs write errors.
   From the *sync* `_handle_stdout_line` it is scheduled as a tracked background
   task (`asyncio.create_task` + `_track_background_task`, `manager.py:~2946`);
   `_handle_stdout_line` therefore stays a plain `def` and its many synchronous
   test call-sites keep working. From the async `_read_sse` it is scheduled the
   same way for symmetry (so the SSE read loop never blocks on a write).

**Could not confirm from the repo:** the MCP specification prose for *which*
server→client requests a client with empty capabilities must accept is not
vendored in this checkout; the decision above is derived from the capability
gating we actually send plus the base-protocol status of `ping` (both
repo-visible), not from the spec text. Flagged rather than guessed.

### C-04 — when `notifications/cancelled` is sent, and with what params

Shape (from `mcp_types`, above):
`{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":<the id we sent downstream>,"reason":<str>}}`.
`requestId` is the JSON-RPC id we assigned via `_next_request_id` and stored as
`PendingRequest.request_id`. **It carries no `id` of its own** — it is a
notification, not a request.

Sent on exactly three local-give-up events, each best-effort and fire-and-forget:

1. **`gateway.cancel`** — in `cancel_request` (`manager.py:3972`), after the
   future is cancelled and the entry popped, schedule the notification with
   `reason="cancelled via gateway.cancel"`.
2. **Idle / ceiling timeout** — in `_send_request`'s `except asyncio.TimeoutError`
   (`manager.py:3145`), schedule with `reason="idle timeout"`. (Both the idle
   and the absolute-ceiling exits raise `asyncio.TimeoutError` from
   `_await_with_idle_timeout`, so this one site covers both.)
3. **Caller cancellation** — the C-02 path in `_send_request` (new
   `except asyncio.CancelledError`), schedule with
   `reason="caller cancelled"`, then re-raise.

**Not sent** on `disconnect_server` / `cancel_pending_requests` /
`cancel_all_pending_requests`: those tear the connection/process down, the pipe
is closing, and notifying a server we are dropping races the teardown for no
benefit. Stated as an explicit scope boundary.

**`initialize` guard (spec MUST):** "A client MUST NOT attempt to cancel its
`initialize` request." `PendingRequest` does not currently store the method, so
add a `method: str` field (set in `_send_request`) and skip the notification
when `method == "initialize"`. This also improves observability of pending
requests.

**Dead-pipe risk:** identical to C-01 — the notification goes through the
guarded helper, which swallows/logs a write to a closed stdin or a dead remote
stream. Because it is *scheduled* (`create_task`), not awaited, a write failure
can never propagate into the cancellation cleanup.

### C-02 ↔ C-04 ordering (both fire on cancellation)

`_send_request` is restructured so the local pop is unconditional and cannot be
skipped by a failing downstream write:

```
try:
    result = await self._await_with_idle_timeout(...)
    return result
except asyncio.TimeoutError:
    self._schedule_cancelled_notification(managed, request_id, method, "idle timeout")  # C-04
    raise TimeoutError(f"Request {method} timed out")
except asyncio.CancelledError:
    self._schedule_cancelled_notification(managed, request_id, method, "caller cancelled")  # C-04
    raise
finally:
    managed.pending_requests.pop(request_id, None)                      # C-02 (unconditional)
    managed.status.pending_request_count = len(managed.pending_requests)
```

Ordering, stated: **C-02 is the invariant, C-04 is best-effort.** On the timeout
and cancellation exits the C-04 notification is *scheduled* first (in the
`except`), then the C-02 pop runs in `finally`. Because C-04 is a `create_task`
(returns immediately, raises nothing synchronously) **and** the pop is in
`finally`, the C-02 cleanup can never be skipped by a C-04 failure — that is the
whole point of scheduling rather than awaiting. On the **success** path the
reader already popped the entry (2802 / 3023) before resolving the future, so
the `finally` pop is a harmless `pop(..., None)` no-op (ids are monotonic and
never reused, so it can never evict a different request's entry). For
`gateway.cancel`, the existing pop at 4028-4030 stays first, then C-04 is
scheduled.

## Changes

### `src/pmcp/client/manager.py` (modify)

- `PendingRequest` (dataclass, `:952`) — **add** field `method: str = ""` — so
  the C-04 send can honour the `initialize` MUST-NOT and to record the method on
  pending entries. Placed with a default to keep all existing positional/keyword
  constructions valid.
- `_send_request` (`:3071`) — **modify** — set `method=method` when building the
  `PendingRequest` (`:3092`); replace the `try/except asyncio.TimeoutError`
  (3135-3148) with the `try/except TimeoutError/except CancelledError/finally`
  block above (C-02 + C-04 idle/cancel). Do **not** route the request send
  itself through the new helper (its write errors must keep propagating as
  connect/call failures).
- `_send_message_to_downstream` (new `async def`) — **add** — one guarded
  outbound writer used only for fire-and-forget replies/notifications: remote →
  `write_stream.send(SessionMessage(jsonrpc_message_adapter.validate_python(payload)))`;
  local → single `stdin.write(json.dumps(payload).encode() + b"\n")` +
  `await stdin.drain()`; wrap in `try/except Exception` that logs via
  `describe_exception` and returns (dead-pipe guard). Returns early if the
  stream/process is gone.
- `_schedule_cancelled_notification` (new, sync) — **add** — build the
  `notifications/cancelled` frame with `params={"requestId": request_id,
  "reason": reason}`; return immediately without scheduling when
  `method == "initialize"`; otherwise `task = asyncio.create_task(
  self._send_message_to_downstream(managed, frame))` +
  `self._track_background_task(task, managed.config.name)`.
- `_reply_to_downstream_request` (new, sync) — **add** — build `{}`-result reply
  for `ping`, else a `-32601` error reply (`mcp_types.METHOD_NOT_FOUND`); schedule
  via `_send_message_to_downstream` + `_track_background_task`.
- `_handle_stdout_line` (`:2777`, stays `def`) — **modify** — reorder the
  dispatch (lines 2799-2826) to classify by `method` first (see C-01 design):
  request → `_reply_to_downstream_request`, notification → existing handler,
  else response-resolution block (moved into `elif`).
- `_read_sse` (`:3000`) — **modify** — apply the identical reorder to
  lines 3021-3048.
- `cancel_request` (`:3972`) — **modify** — after the pop at 4028-4030, call
  `self._schedule_cancelled_notification(managed, local_id, pending.method,
  "cancelled via gateway.cancel")` (C-04).

### `tests/test_client_manager.py` (modify)

Add the six acceptance tests measured below (verbatim bodies captured in this
run), plus two post-fix-only guards:

- the six probes: `test_c01_stdio_ping_gets_empty_result_reply`,
  `test_c01_collision_request_does_not_resolve_our_pending`,
  `test_c01_unsupported_request_gets_method_not_found`,
  `test_c02_caller_cancellation_pops_pending_entry`,
  `test_c04_gateway_cancel_notifies_downstream`,
  `test_c04_idle_timeout_notifies_downstream`.
- `test_c04_initialize_request_is_never_cancelled_downstream` — a pending
  `initialize` cancelled via `gateway.cancel`/idle path emits **no**
  `notifications/cancelled` (constructs `PendingRequest(..., method="initialize")`,
  so it depends on the new field and is post-fix-only).
- `test_c01_sse_ping_gets_empty_result_reply` — the SSE twin of the stdio ping
  reply, via `_read_sse` with a mocked `write_stream`.

Do **not** modify `tests/conftest.py`. The existing guards
`test_unknown_notification_is_a_noop_on_stdio_dispatch...`,
`...on_sse_dispatch...`, and
`test_typed_downstream_error_preserves_code_and_data_on_{stdio,sse}_dispatch`
(`test_client_manager.py:4867,4908,4954,4997`) must stay green — the reorder
preserves both the notification no-op and the response-resolution arm.

### `CHANGELOG.md` (modify)

- Under the existing `## [Unreleased]` → `### Fixed` heading (`:369`) — **add**
  one entry: downstream server→client requests are now answered (`ping` → empty
  result, others → `-32601`); `pending_requests` no longer leaks on caller
  cancellation; and `notifications/cancelled` is now sent downstream on
  `gateway.cancel`, idle timeout, and caller cancellation. Reference `see #232`.

## Documentation impact

- `CHANGELOG.md` — modify — as above. No other cross-cutting doc applies: this
  is an internal client-manager liveness fix with no README/API/openapi surface.
  `specs/phase-plans-v13.md` and `plans/phase-plan-v13-*.md` are **not** touched
  (they pin sha256s; editing them staled the gate earlier today).

## Frozen-vocabulary / protocol confirmation

The IF-0-FANOUT-1 contract (`_handle_downstream_notification`, `:2028`) is
frozen: sync, signature `(self, name, managed, method)`, docstring mentioning
the three `notifications/*/list_changed` methods — guarded by
`test_reconcile_contract_entry_point_has_frozen_signature`
(`test_client_manager.py:3860`). This plan does **not** touch that function, its
signature, or its docstring, and introduces **no** new downstream-notification
*vocabulary*: `notifications/cancelled`, `ping`, `EmptyResult`, and
`METHOD_NOT_FOUND` are all standard MCP/JSON-RPC identifiers taken from the
vendored `mcp_types` 2.0.0 package. The reorder in the two dispatch paths keeps
calling `_handle_downstream_notification` unchanged for the notification arm.

## Dependencies & order

1. Add `PendingRequest.method` first (other changes reference it).
2. Add `_send_message_to_downstream`, then `_schedule_cancelled_notification`
   and `_reply_to_downstream_request` (they depend on the writer).
3. Wire C-01 into `_handle_stdout_line` and `_read_sse`.
4. Wire C-02 + C-04 into `_send_request`; wire C-04 into `cancel_request`.
5. Add tests; run.

No external/migration dependencies.

## Verification

Run from the worktree (`uv sync -p 3.10` already done). Subset runs must pass
`--cov-fail-under=0`; some cancellation paths can hang, so cap with `--timeout`.

```bash
# The six acceptance probes + the two post-fix guards must be GREEN after the fix:
uv run pytest tests/test_client_manager.py -k "c01_ or c02_ or c04_" \
  -p no:cacheprovider --cov-fail-under=0 --timeout=30 -q

# The frozen-contract and dispatch guards must stay GREEN:
uv run pytest tests/test_client_manager.py \
  -k "frozen_signature or unknown_notification_is_a_noop or typed_downstream_error_preserves" \
  --cov-fail-under=0 --timeout=30 -q

# No new sweeping claim: confirm notifications/cancelled now exists only where intended.
grep -rn "notifications/cancelled" src/pmcp/

# Full module regression (dispatch, reconnect, tasks):
uv run pytest tests/test_client_manager.py --cov-fail-under=0 --timeout=120 -q

# Plan-consistency gate must remain 0 blocking:
uv run python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md
```

Per-criterion mutation the executor MUST run after the fix is green (revert one
line, confirm red, restore from a saved copy and `diff` byte-identical — never
`git checkout --`):

- **C-01 reply:** in `_reply_to_downstream_request`, change the `ping` branch to
  build the error frame instead of `{"result": {}}` → `test_c01_stdio_ping_...`
  and `test_c01_sse_ping_...` go red on the `result`/`error` assertion.
- **C-01 classify-first:** revert the reorder so the response arm runs first →
  `test_c01_collision_request_does_not_resolve_our_pending` goes red
  (`fut.done()` becomes True).
- **C-02:** delete the `finally:` pop in `_send_request` →
  `test_c02_caller_cancellation_pops_pending_entry` goes red (entry leaks).
- **C-04:** comment out the `_schedule_cancelled_notification` call in the
  `except asyncio.TimeoutError` (resp. `cancel_request`) →
  `test_c04_idle_timeout_notifies_downstream` (resp.
  `test_c04_gateway_cancel_notifies_downstream`) goes red.
- **C-04 initialize guard:** remove the `method == "initialize"` early return →
  `test_c04_initialize_request_is_never_cancelled_downstream` goes red.

## Acceptance criteria

Each was measured RED against the current defective tree for its named defect
(the live bug is the mutant; see the run log — 6/6 red, right reason). Each must
flip GREEN once the fix lands, and re-red under its mutation above.

- [ ] C-01a — a downstream `ping` request (stdio) is answered with
  `{"jsonrpc":"2.0","id":<id>,"result":{}}`, proven by
  `test_c01_stdio_ping_gets_empty_result_reply` (and the SSE twin
  `test_c01_sse_ping_gets_empty_result_reply`).
- [ ] C-01b — a downstream request whose `id` collides with an outstanding
  request id does **not** resolve our pending future, proven by
  `test_c01_collision_request_does_not_resolve_our_pending`.
- [ ] C-01c — an unsupported downstream request (`sampling/createMessage`) is
  answered with a JSON-RPC `-32601` error, proven by
  `test_c01_unsupported_request_gets_method_not_found`.
- [ ] C-02 — caller cancellation of `_send_request` pops the `pending_requests`
  entry (leaves it empty), proven by
  `test_c02_caller_cancellation_pops_pending_entry`.
- [ ] C-04 — `notifications/cancelled` with `params.requestId == <id>` is written
  downstream on both `gateway.cancel` and idle timeout, and is **not** written
  for a pending `initialize`, proven by
  `test_c04_gateway_cancel_notifies_downstream`,
  `test_c04_idle_timeout_notifies_downstream`, and
  `test_c04_initialize_request_is_never_cancelled_downstream`.

## Execution Policy

- execute: effort=high, reason=concurrency + protocol correctness on an
  untrusted-server boundary; subtle cancellation ordering and outbound writes.
