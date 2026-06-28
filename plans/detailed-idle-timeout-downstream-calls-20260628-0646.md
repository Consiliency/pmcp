# Detailed plan: convert downstream-call timeout to an inactivity (idle) timeout (#79 symptom 1a)

> **Plan Mode was NOT active when this was produced.** This is a planning artifact only — no implementation has begun.

## Task

Fix PMCP issue #79 symptom 1a: long downstream MCP tool calls (e.g. a multi-step `browser_run_code_unsafe` loop) are killed by a hard wall-clock timeout. In `src/pmcp/client/manager.py`, `_send_request` (line 1527) waits on `asyncio.wait_for(future, timeout=timeout_ms/1000.0)` — a fixed *total* deadline. `PendingRequest.last_heartbeat` is updated on downstream output and a health monitor observes it, but nothing uses it to extend the deadline.

Convert `timeout_ms` semantics into an **inactivity (idle) timeout**: the call survives as long as the downstream keeps producing output, and only fails after `idle` seconds of silence — with a generous, env-configurable **absolute ceiling** as a backstop so a chatty-but-never-completing call cannot hang forever. Bounded to `manager.py` timeout logic + tests. Refresh (#79/2) and process-group reaping (#79/1c) are separate plans.

## Research summary

All findings are from direct reads of `src/pmcp/client/manager.py`, `src/pmcp/tools/handlers.py`, and `tests/test_client_manager.py` this session.

- **The hard wait** is `manager.py:1527`: `result = await asyncio.wait_for(future, timeout=timeout_ms/1000.0)`; on `asyncio.TimeoutError` it pops the pending request (1530) and raises `TimeoutError` (1532). `handlers.py:1656` catches that and returns `E303_TOOL_TIMEOUT` — that mapping must be preserved.
- **`PendingRequest`** (`manager.py:294-306`) already carries `started_at`, `last_heartbeat`, `timeout_ms`, and `future` — no schema change needed.
- **Heartbeat gap (must fix too):** in the stdio reader `_read_stdout` (`manager.py:1280-1323`), a matched JSON **response** pops+resolves its request (1294-1318); **non-JSON** output bumps *all* pending heartbeats (1321-1322); but a JSON **notification** (`msg_id is None`, e.g. `notifications/progress`) updates only server-level `status.last_activity_at` (1289), **not** per-request `last_heartbeat`. The remote reader `_read_sse` (`manager.py:1420-1448`) has the identical structure and the identical gap. **MCP progress notifications are the natural keepalive during a long browser op, so the idle timeout is only effective if per-request `last_heartbeat` is bumped on *all* downstream output.** This is the second half of the fix.
- **Health monitor** (`_health_monitor_loop`, `manager.py:2111-2167`) only **logs** stalled/slow requests (thresholds `HEARTBEAT_WARN_THRESHOLD=60.0`, `HEARTBEAT_STALL_THRESHOLD=120.0`, `HEALTH_CHECK_INTERVAL=30.0` at lines 75-77). It never cancels futures — so the wait loop in `_send_request` is the **sole** timeout-enforcement point. No change needed here.
- **Callers of `_send_request`** all share the same wait and inherit the new semantics uniformly (desirable): `initialize` (1542/1548), `tools/list`/`resources/list`/`prompts/list` (1026/1029/1030), `call_tool` (1831), `tasks/*` (1883/1905/1930/1974), `read_resource` (2005/2042). Most use the `timeout_ms=30000` default (`_send_request` signature, `manager.py:1483`).
- **Env-var pattern to mirror:** `_stdio_read_limit()` + `DEFAULT_STDIO_READ_LIMIT` (`manager.py:96-118`) reads `PMCP_STDIO_READ_LIMIT`, int-parses with a logged fallback on bad/non-positive values. The new ceiling helper should copy this exactly.
- **Test harness:** `tests/test_client_manager.py` (uses `pytest.mark.asyncio`, `AsyncMock`/`MagicMock`). The fixture around line 532 builds a `ManagedClient` with a mock process; `test_disconnect_all_cancels_pending_requests` (579-602) constructs a real `PendingRequest`, injects it into `managed.pending_requests`, and drives the future — directly reusable for idle-timeout tests.

## Changes

### `src/pmcp/client/manager.py` (modify)

- **Module constants (near lines 74-77)** — add `DEFAULT_REQUEST_CEILING_MS = 600000` (10 min absolute backstop) — add — gives the idle wait a hard upper bound independent of the idle threshold. Keep next to the heartbeat thresholds for discoverability.
- **`_request_ceiling_ms()` helper (new, mirror `_stdio_read_limit` at 96-118)** — add — reads `PMCP_REQUEST_CEILING_MS`, int-parses with logged fallback to `DEFAULT_REQUEST_CEILING_MS` on missing/invalid/non-positive, identical structure to the stdio-limit helper. Reason: env-configurable backstop per the task; reuse the established config idiom.
- **`_read_stdout` heartbeat bump (`manager.py:1287-1289`)** — modify — immediately after computing `now` and setting `managed.status.last_activity_at`, also bump every in-flight request: `for req in managed.pending_requests.values(): req.last_heartbeat = now`. Reason: makes JSON progress notifications (and any output) count as per-request liveness; remove the now-redundant per-branch bumps at 1296 (the popped response) and 1321-1322 (non-JSON) since the single top-of-loop bump covers all cases.
- **`_read_sse` heartbeat bump (`manager.py:1426-1431`)** — modify — same change: after `now`/`last_activity_at`, bump all pending `last_heartbeat`; drop the redundant Exception-branch (1430-1431) and matched-response (1442) bumps. Reason: parity with stdio so remote/HTTP downstreams also benefit from the idle timeout.
- **`_send_request` wait logic (`manager.py:1525-1532`)** — modify — replace the single `asyncio.wait_for(future, timeout=timeout_ms/1000.0)` with a call to a new `_await_with_idle_timeout(...)` (below). Preserve the existing cleanup (`managed.pending_requests.pop(request_id, None)` + recount) and `raise TimeoutError(f"Request {method} timed out")` on timeout, so `handlers.py:1656` still maps to E303. Reason: core behavior change, kept minimal at the call site.
- **`_await_with_idle_timeout(self, managed, request_id, pending, future, idle_timeout_s, ceiling_s)` (new private method)** — add — loop: `await asyncio.wait_for(asyncio.shield(future), timeout=slice_s)` in slices (`slice_s = min(idle_timeout_s, ~1.0s)`); on each `TimeoutError`, if `future.done()` return its result, elif `time.time() - pending.started_at >= ceiling_s` raise `TimeoutError` (ceiling hit), elif `time.time() - pending.last_heartbeat >= idle_timeout_s` raise `TimeoutError` (idle hit), else continue. `idle_timeout_s = timeout_ms/1000.0`; `ceiling_s = _request_ceiling_ms()/1000.0`. Use `asyncio.shield` so a slice timeout never cancels the real future. Reason: isolating the loop makes it unit-testable without a live process and keeps `_send_request` readable.

### `tests/test_client_manager.py` (modify)

- **`test_idle_timeout_survives_periodic_output`** — add — drive `_send_request` (or `_await_with_idle_timeout` directly) with a small idle threshold (e.g. 0.3s); a background task bumps `pending.last_heartbeat` every ~0.1s for longer than the old hard window, then sets the future result → assert it returns the result, no timeout.
- **`test_idle_timeout_fires_when_silent`** — add — no heartbeat bumps; assert `TimeoutError` raised after ~idle threshold and the request is removed from `managed.pending_requests`.
- **`test_absolute_ceiling_fires_for_chatty_call`** — add — bump `last_heartbeat` continuously (never idle) but never resolve the future, with a small ceiling (e.g. `PMCP_REQUEST_CEILING_MS` via `monkeypatch.setenv` → ~0.5s) → assert `TimeoutError` raised at the ceiling despite ongoing heartbeats.
- **`test_progress_notification_bumps_pending_heartbeat`** — add — feed the stdio reader (or call the extracted bump path) a JSON notification with `id: null` and assert in-flight `pending.last_heartbeat` advanced — guards the heartbeat-gap regression.
- **`test_request_ceiling_ms_env_parsing`** — add — mirror existing `_stdio_read_limit` tests: valid value, non-int, non-positive → assert fallback to `DEFAULT_REQUEST_CEILING_MS` (parametrized).

## Documentation impact

- `CHANGELOG.md` — modify — add an entry under the next unreleased version: idle/inactivity-based downstream timeout + `PMCP_REQUEST_CEILING_MS`; note that `timeout_ms` is now an inactivity timeout, not a total deadline (behavior change worth flagging).
- `README.md` — modify **only if** it documents `PMCP_STDIO_READ_LIMIT` or a tunables/env-var list — add `PMCP_REQUEST_CEILING_MS` alongside it. (Verify during implementation; if no such section exists, skip — do not invent one.)

## Dependencies & order

1. Add module constant + `_request_ceiling_ms()` helper (no dependencies).
2. Add `_await_with_idle_timeout` (uses the helper).
3. Switch `_send_request` to call it (uses step 2).
4. Close the heartbeat gap in `_read_stdout` and `_read_sse` (independent of 1-3, but **required** for the idle timeout to actually help notification-driven ops — land in the same change).
5. Tests last.

No blocking external dependencies. No migrations.

## Verification

```bash
# Targeted unit tests
pytest tests/test_client_manager.py -k "idle_timeout or ceiling or progress_notification or request_ceiling" -v

# Full client-manager suite — guard against regressing initialize/call_tool/read_resource,
# all of which route through _send_request
pytest tests/test_client_manager.py -v

# Type/lint per repo tooling (confirm exact commands from pyproject/CI)
ruff check src/pmcp/client/manager.py
mypy src/pmcp/client/manager.py   # if mypy is configured

# Full suite sanity
pytest -q
```

Edge cases to confirm by test or inspection:
- A truly idle downstream still times out at the idle threshold (no infinite hang).
- A continuously-chatty-but-never-completing call is killed by the ceiling.
- `initialize` handshake (no/late response) still times out within the idle window — no regression to startup behavior.
- The slice loop never cancels the real future (use `asyncio.shield`); a response arriving mid-slice is returned, not dropped.
- Pending request is removed from `managed.pending_requests` on timeout (no leak), matching today's line 1530 cleanup.

## Acceptance criteria

- [ ] `pytest tests/test_client_manager.py -v` passes, including the 5 new tests.
- [ ] A downstream that emits output every ~0.1s past the old fixed window completes successfully (idle timeout does not fire while output flows).
- [ ] A silent downstream raises `TimeoutError` at ~`idle_timeout_s` and is removed from `pending_requests`; `handlers.py` still returns `E303_TOOL_TIMEOUT` for it (unchanged mapping).
- [ ] A continuously-heartbeating call that never resolves raises `TimeoutError` at `_request_ceiling_ms()` (default 600000ms, overridable via `PMCP_REQUEST_CEILING_MS`).
- [ ] A JSON notification with `id: null` advances in-flight `pending.last_heartbeat` in both `_read_stdout` and `_read_sse`.
