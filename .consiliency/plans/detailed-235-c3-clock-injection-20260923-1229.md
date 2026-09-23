# Detailed plan: inject the request clock — slice C3 of Consiliency/pmcp#235 (idle-timeout tests without wall time)

> **Bounded-plan verdict: within threshold.** Two files change (`src/pmcp/client/manager.py`,
> `tests/test_client_manager.py`) plus one CHANGELOG line. The change is a seam, not a
> behaviour change: production behaviour on the default clock is byte-for-byte the same
> arithmetic it was. Every number below was **measured this session** against a throwaway
> spike on `plan/235-c3-clock` at `860636a` (= `origin/main`), then the spike was reverted
> (`src/` and `tests/` byte-identical to HEAD before this plan was committed).

## Task

Slice C3 of Consiliency/pmcp#235 (test hermeticity umbrella), the last open slice. The C1
plan (`.consiliency/plans/detailed-deterministic-test-timing-20260920-0846.md`, survey
item 7 and "Helper choice") found that two heartbeat tests in
`tests/test_client_manager.py` — one bumping `last_heartbeat` every 0.1 s against a 0.3 s
idle window, one every 0.05 s against a 200 ms ceiling — exercise
`ClientManager._await_with_idle_timeout`, which reads `time.time()` in production code,
"so neither a deadline poller nor an event-loop clock can make them deterministic; only a
clock injected into production code can." This plan injects that clock, converts the
tests, and proves by measurement that they no longer wait real seconds and no longer
depend on the machine's speed. See Consiliency/pmcp#235.

## Research summary (current tree, `860636a`)

**The C1 plan's line numbers are stale** — Consiliency/pmcp#286 (`860636a`) reshaped
`manager.py`. Located by content this session:

- `_send_request` is `src/pmcp/client/manager.py:3233-3330`; it stamps `started_at` and
  `last_heartbeat` from one `now = time.time()` at `:3239`, then calls
  `_await_with_idle_timeout` at `:3298`.
- `_await_with_idle_timeout` is `:3332-3369`. It waits in slices —
  `slice_s = min(idle_timeout_s, 1.0)` (`:3351`), `await asyncio.wait_for(asyncio.shield(future), timeout=slice_s)`
  (`:3354`) — and after each slice expires reads `now = time.time()` (`:3358`) and compares
  `now - pending.started_at >= ceiling_s` (`:3359`) and
  `now - pending.last_heartbeat >= idle_timeout_s` (`:3367`).
- The heartbeat is stamped by the two readers: `_read_stdout` (`:2922-2925`, `now = time.time()`;
  `managed.status.last_activity_at = now`; every pending `req.last_heartbeat = now`) and
  `_read_sse` (`:3044-3047`, identical). The same `now` also feeds `elapsed_ms = (now - pending.started_at) * 1000`
  when a response resolves a request (`:2849`, `:3072`).
- Three more readers compare against those stamps: `_health_monitor_loop` (`:4050`,
  `now - pending.last_heartbeat` at `:4085`), `get_request_state` (`:4157-4159`) and
  `cancel_request` (`:4213-4215`).
- The tests: `TestIdleTimeout` at `tests/test_client_manager.py:3262-3441`.
  `test_idle_timeout_survives_periodic_output` (`:3282`, keepalive every `sleep(0.1)`
  against `idle_timeout_s=0.3`), `test_idle_timeout_fires_when_silent` (`:3314`, a real
  200 ms wait), `test_absolute_ceiling_fires_for_chatty_call` (`:3328`, `sleep(0.05)`
  bumps against `PMCP_REQUEST_CEILING_MS=200`), and the two
  `test_progress_notification_bumps_pending_heartbeat_{stdout,sse}` (`:3360`, `:3401`)
  that assert only `pending.last_heartbeat > stale`. Baseline call times measured this
  session: **0.50 s, 0.20 s, 0.41 s, 0.01 s, 0.01 s**.

**The `time.time()` census.** The brief's "20 call sites" is a grep-line count: two of
the 20 lines are the `PendingRequest` field comments (`:965`, `:966`). There are **18
call sites**, all in `manager.py`; the module never calls `time.monotonic()`.

**No test patches `pmcp.client.manager.time`** (grep `manager.time|pmcp.client.manager.time`
in `tests/` → nothing), so a module-level monkeypatch — the pattern `_Clock` uses at
`tests/test_feedback_egress_gate.py:195/:696` over `feedback_egress.time` — is available
but is *not* chosen (see "Seam shape"). The `_Clock` precedent is why C3 needs no new
dependency: a fake clock is a five-line class.

**The stamps are user-visible as epoch seconds.** `src/pmcp/tools/handlers.py:6329-6341`
(`gateway.list_pending`) renders `started_at_iso=datetime.fromtimestamp(req.started_at, tz=utc)`
and computes `elapsed_seconds=now - req.started_at` and
`last_heartbeat_seconds_ago=now - req.last_heartbeat` with **its own** `now = time.time()`;
`src/pmcp/cli.py:1478` and `:1549` (`pmcp status`) compute `time.time() - p.started_at`
the same way; `types.py:928-932` (`PendingRequestInfo.started_at_iso`,
`last_heartbeat_seconds_ago`) is the public shape. This is the fact that decides the
monotonic question (below).

**What C2 already settled.** The C1 plan's C3 paragraph also named
`tests/test_server_lifecycle.py:90` (extract `server.py:965`'s `timeout=10.0`) and
`tests/test_scoped_advisor_audit.py:461` (the 1.5 s soak). The merged C2a plan
(`detailed-poll-until-conversion-c2a-20260921-0514.md`, "Kept on purpose (do not convert
in any sub-slice)") lists both as deliberately kept. This plan follows C2a: **C3 is the
clock seam only**. `test_c04_idle_timeout_notifies_downstream`
(`tests/test_client_manager.py:6728`, `timeout_ms=20`, 0.03 s, no upper bound) is also
left alone — it is cheap and not in the flaky class.

**Ruff config:** `pyproject.toml` has no `[tool.ruff]` section, so the default rule set
(E, F) applies; closures over a loop variable (B023) are not flagged, and the closures
below are evaluated before the variable changes anyway. `Callable` in `manager.py` comes
from `collections.abc` (`:18` already imports `Collection` from there).

## Design decisions

### 1. Seam shape: a constructor-injected callable, not a module patch

`ClientManager.__init__` gains a keyword-only `clock: Callable[[], float] | None = None`
and stores `self._clock = clock if clock is not None else time.time`. The seam sites call
`self._clock()`.

Why not monkeypatch `pmcp.client.manager.time` like `_Clock` does: (a) that converts all
18 sites at once, including the revision-id generator and the reported timestamps that
must stay on wall time (next section); (b) it is process-global state shared with every
other `ClientManager` alive in the test process, which is exactly the class of leak
`reset-module-state-between-tests` (Consiliency/pmcp#235's sibling slice) exists to
remove; (c) an injected callable is visible in the constructor signature, so the
boundary is documented where it is decided. Every production constructor
(`server.py:175`, `cli.py:957/:1151/:1237/:2743`) passes nothing and gets `time.time`.

### 2. Seam boundary: 7 sites in, 11 sites out

**In the seam — the "request-liveness clock".** The rule: a site joins if it *stamps*
`PendingRequest.started_at` / `last_heartbeat` / `status.last_activity_at`, or *compares*
a reading against one of those stamps. A fake clock on one side and wall time on the
other produces `now - last_heartbeat ≈ 1.79e9` (measured under mutation M1/M2 below), so
the group must move together:

| # | site (spike line) | entity | role |
|---|---|---|---|
| 1 | `:3253` | `_send_request` | stamps `started_at`, `last_heartbeat` |
| 2 | `:2936` | `_read_stdout` | stamps `last_activity_at`, every `last_heartbeat`; `elapsed_ms` |
| 3 | `:3058` | `_read_sse` | same as 2 |
| 4 | `:3375` | `_await_with_idle_timeout` | compares: idle window, absolute ceiling |
| 5 | `:4067` | `_health_monitor_loop` | compares: stall/slow warnings (`:4085`) |
| 6 | `:4174` | `get_request_state` | compares: TIMEOUT / STALLED / ACTIVE |
| 7 | `:4230` | `cancel_request` | compares: `was_stalled`, refuse-if-healthy, reported `elapsed` |

**Out of the seam — deliberately wall time.** None is ever compared with a request stamp;
each is a *reported* timestamp or an identifier, where a fake clock in a test would be
wrong (colliding revision ids) or meaningless:

| site | entity | why it stays |
|---|---|---|
| `:483` | `_generate_revision_id` → `rev-<ms>-<suffix>` | identifier; fake time would collide ids |
| `:1051`, `:1126`, `:1307`, `:1447`, `:3678` | `_last_refresh_ts` | catalog refresh timestamp returned by `get_revision()` (`:4020`) |
| `:1710`, `:3894` | `McpTaskRecord.updated_at` | task-record bookkeeping, eviction order (`:1738`) |
| `:2446`, `:2770`, `:3674` | `status.last_connected_at` | reported in `ServerStatus` (`types.py:490`) |

After the change `manager.py` has 11 `time.time()` calls and 7 `self._clock()` calls
(measured on the spike: `grep -c 'time\.time()'` → 11, `grep -c 'self\._clock()'` → 7;
Verification step 1 re-checks this).

### 3. `time.time()` vs `time.monotonic()`: stay on wall time; monotonic is a named follow-up

Wall time is the wrong clock for durations — an NTP step moves every idle window and
every `elapsed_seconds` at once. But `started_at` and `last_heartbeat` are **epoch
values consumed outside `ClientManager`**: `handlers.py:6335` renders `started_at` with
`datetime.fromtimestamp` and `handlers.py:6338/:6341` and `cli.py:1478/:1549` subtract
their own `time.time()` from the stamps. Changing the default clock to monotonic would
make `started_at_iso` read as 1970 and `elapsed_seconds` read as ~1.79e9 — a
user-visible break in `gateway.list_pending` and `pmcp status`. That is a cross-module
change with its own review, not a test seam, so **C3 does not move to monotonic** and
pins the default with `test_default_request_clock_is_wall_time` (mutation M8 goes red).

**Follow-up (file after C3 merges, do not do here):** "Request durations on a monotonic
clock." Either (a) keep `started_at` as epoch for display and add
`started_mono`/`last_heartbeat_mono` for every comparison, or (b) route every external
consumer through `ClientManager` methods (`request_elapsed(pending)`,
`heartbeat_age(pending)`) so no other module does clock arithmetic, then switch the
default `clock` to `time.monotonic` and render `started_at_iso` from a separately kept
epoch. The seam this plan adds makes either option a default-swap plus the consumer
edits: `handlers.py:6329-6341`, `cli.py:1465-1479`, `cli.py:1549-1550`,
`types.py:928-932`.

### 4. How the fake clock drives the loop without real waiting

`_await_with_idle_timeout` really does wait on the event loop:
`asyncio.wait_for(asyncio.shield(future), timeout=slice_s)` with
`slice_s = min(idle_timeout_s, 1.0)`. Faking `now` alone would leave the converted
"survives" test sleeping 0.3 s of real time per slice. Two things make the wait
disappear:

1. **The slice length becomes a module constant, `IDLE_POLL_SLICE_S = 1.0`**, replacing
   the literal `1.0`. The tests `monkeypatch.setattr("pmcp.client.manager.IDLE_POLL_SLICE_S", 0.001)`.
   The slice is *only* the re-check cadence; what each check *sees* is the fake clock. So
   shortening it changes how often the check runs, never what it decides. (Measured:
   with the literal restored — mutation M9 — the three tests still pass but take
   1.25 s / 1.01 s / 0.50 s instead of 0.01 s each.)
2. **The driver task steps the clock exactly once per check.** `_FakeClock.now()` counts
   its reads. The driver does `await eventually(lambda: clock.reads >= k, interval=0)`
   before step *k*, then mutates (`pending.last_heartbeat = clock.value`,
   `clock.advance(0.125)`) with no `await` in between. `interval=0` is
   `asyncio.sleep(0)`, so the driver gets a turn every loop iteration; the `wait_for`
   timer needs ≥1 ms of real time *and* two further ready-queue hops (timer callback →
   `_cancel_and_wait` → the loop task's step) before read *k+1*, so step *k* always
   lands between read *k* and read *k+1*. The test does not trust that argument: it
   records `(clock.reads, age)` at every step and asserts the exact sequence
   `[(1, 0.0), (2, 0.125), (3, 0.125), (4, 0.125), (5, 0.125)]` — an ordering slip would
   fail by name, not pass by luck. Measured 40/40 (see Verification 6).

**Dyadic constants.** 0.05 × 4 on binary floats is `0.1999999999998181`, so the existing
constants (0.05/0.1/0.2/0.3) would make an exact `>=` comparison miss by one check. The
converted tests use 0.125 steps against 0.25 / 0.5 windows and a 0.25 s ceiling
(`timeout_ms=250`, `PMCP_REQUEST_CEILING_MS=250`, `timeout_ms=500`), all exactly
representable, so `==` assertions on fake elapsed time are exact (verified in-session:
`1000.0 + 8 × 0.125 - 1000.0 == 1.0` is `True`).

**No upper bound on wall-clock time anywhere.** The converted tests assert on fake-clock
values, read counts, log text, and returned values. The only real-time parameters are
hang guards (`eventually`'s default 5 s; a bounded number of fake steps after which the
driver fails the request with a *named* `AssertionError`). Each mutant that would
otherwise hang ends by name (M2: "idle timeout never fired in 1.0 s of request-clock").

### 5. Why the "started_at stays on wall time" mutant must be bounded in fake steps

If `_send_request` stamps from wall time while the checks read the fake clock, the
ceiling is never reached (`now - started_at` is hugely negative) and — while the chatty
driver keeps stamping — neither is the idle window. The drivers therefore run a bounded
number of fake steps and then `set_exception(AssertionError(...))` on the pending
future, which `_await_with_idle_timeout` returns via `future.result()`. That is
deliberately **not** an outer `asyncio.wait_for` hang guard: on Python 3.10
`asyncio.TimeoutError` is not the builtin `TimeoutError` (C1 plan, Revision 2), so an
outer guard would fail differently on 3.10 and 3.11+.

## Changes

### `src/pmcp/client/manager.py`

| entity | action | reason |
|---|---|---|
| import block `:18` | add `Callable` to `from collections.abc import ... Collection` | type of the injected clock |
| new constant `IDLE_POLL_SLICE_S = 1.0` after `HEALTH_CHECK_INTERVAL` (`:349`) | add, with comment | the re-check cadence of `_await_with_idle_timeout`, patchable by tests without changing outcomes |
| `PendingRequest.started_at` / `.last_heartbeat` field comments (`:965-966`) | reword: "request clock", plus a 3-line note that both come from `ClientManager._clock` and are epoch seconds because `gateway.list_pending` renders them | documents the coherence contract at the data it governs |
| `ClientManager.__init__` (`:1033`) | add kw-only `clock: Callable[[], float] | None = None`; set `self._clock = clock if clock is not None else time.time` with a comment naming the seam group | the seam |
| `_read_stdout` `:2922` | `time.time()` → `self._clock()` | stamps heartbeat |
| `_read_sse` `:3044` | same | stamps heartbeat |
| `_send_request` `:3239` | same | stamps `started_at`/`last_heartbeat` |
| `_await_with_idle_timeout` `:3351`, `:3358` | `min(idle_timeout_s, 1.0)` → `min(idle_timeout_s, IDLE_POLL_SLICE_S)`; `time.time()` → `self._clock()`; docstring gains two lines on slice vs clock | the compare side |
| `_health_monitor_loop` `:4050` | `time.time()` → `self._clock()` | compares heartbeat age |
| `get_request_state` `:4157` | same | compares |
| `cancel_request` `:4213` | same | compares, reports `elapsed` |

Nothing else in `src/` changes. The eleven remaining `time.time()` calls are the
out-of-seam sites of Design decision 2.

### `tests/test_client_manager.py`

| entity | action | reason |
|---|---|---|
| imports | add `import logging`; add `HEARTBEAT_STALL_THRESHOLD`, `HEARTBEAT_WARN_THRESHOLD` to the `pmcp.client.manager` import; add `RequestState` to the `pmcp.types` import | used by the new tests |
| `_FakeClock` (module-level, before `TestIdleTimeout`) | add | the fake: `value`, `reads`, `now()`, `advance()` |
| `TestIdleTimeout` docstring | extend | states the fake-clock/dyadic contract for the class |
| `TestIdleTimeout._pending` | add staticmethod | a `PendingRequest` stamped from the fake clock, as `_send_request` would |
| `test_default_request_clock_is_wall_time` | add | pins the monotonic decision (M8) |
| `test_idle_timeout_survives_periodic_output` | rewrite | fake clock + recorded `(reads, age)` sequence |
| `test_idle_timeout_fires_when_silent` | rewrite | fires at exactly 0.25 s fake; bounded driver |
| `test_absolute_ceiling_fires_for_chatty_call` | rewrite | fires at exactly 0.25 s fake; asserts the ceiling log line; bounded driver |
| `test_request_state_reads_the_request_clock` | add | covers seam site 6 (M5) |
| `test_cancel_request_reads_the_request_clock` | add | covers seam site 7 (M6) |
| `test_health_monitor_reads_the_request_clock` | add | covers seam site 5 (M7) |
| `test_progress_notification_bumps_pending_heartbeat_stdout` / `_sse` | tighten `> stale` to `== clock.value`, also assert `status.last_activity_at == clock.value` | covers seam sites 2 and 3 (M3, M4) |
| `test_request_ceiling_ms_env_parsing` | unchanged | — |

`tests/_timing.py` and `tests/conftest.py` are not touched. `eventually(..., interval=0)`
is within the frozen API.

### `CHANGELOG.md`

Under `## [Unreleased]` → `### Fixed`, next to the C2a/C2c lines (`:375-377`), add one
entry in the house style:

> - **Tests: the idle-timeout tests no longer race a real clock.** `ClientManager` now
>   takes an injected request clock (`clock=`, wall time by default) that stamps and
>   compares every request heartbeat, and the idle re-check cadence is a constant
>   (`IDLE_POLL_SLICE_S`); the heartbeat-vs-idle-window and ceiling tests drive a fake
>   clock one idle check at a time instead of sleeping 0.05–0.1 s against 0.2–0.3 s
>   windows, and finish in ~10 ms each (slice C3). No production behaviour changes: the
>   default clock is still `time.time()`, because `gateway.list_pending` and
>   `pmcp status` render `started_at` as an epoch. See
>   [#235](https://github.com/Consiliency/pmcp/issues/235).

### Production diff (spike, verbatim — apply as-is)

```diff
diff --git a/src/pmcp/client/manager.py b/src/pmcp/client/manager.py
index a7c3336..f99cfe6 100644
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -15,7 +15,7 @@ import traceback
 import string
 import time
 from collections import deque
-from collections.abc import Collection
+from collections.abc import Callable, Collection
 from dataclasses import dataclass, field
 from types import ModuleType
 from typing import Any, Iterator, TypeVar
@@ -347,6 +347,10 @@ async def _terminate_process_tree(
 HEARTBEAT_WARN_THRESHOLD = 60.0  # Warn if no activity for 60s
 HEARTBEAT_STALL_THRESHOLD = 120.0  # Mark as stalled after 120s
 HEALTH_CHECK_INTERVAL = 30.0  # Background health check every 30s
+# Longest real wait between two idle/ceiling checks in _await_with_idle_timeout.
+# The checks themselves read the request clock (ClientManager._clock); this only
+# bounds how often they run, so tests can shrink it without changing an outcome.
+IDLE_POLL_SLICE_S = 1.0
 
 # Connection retry settings
 MAX_CONNECTION_RETRIES = 3
@@ -962,8 +966,11 @@ class PendingRequest:
     request_id: int
     server_name: str
     tool_id: str  # Empty for non-tool requests (initialize, tools/list)
-    started_at: float  # time.time() when request started
-    last_heartbeat: float  # time.time() of last activity
+    # Both stamps come from the owning ClientManager's request clock (`_clock`,
+    # wall time by default) and are only ever compared with readings of that
+    # same clock. Epoch seconds: gateway.list_pending renders started_at as ISO.
+    started_at: float  # request clock when request started
+    last_heartbeat: float  # request clock of last activity
     timeout_ms: int  # Configured timeout
     future: asyncio.Future[Any]
     task_id: str | None = None
@@ -1037,10 +1044,17 @@ class ClientManager:
         project_root: Path | None = None,
         *,
         catalog_events: CatalogEventSink | None = None,
+        clock: Callable[[], float] | None = None,
     ) -> None:
         self._catalog_events: CatalogEventSink = (
             catalog_events or _NullCatalogEventSink()
         )
+        # The request clock: every PendingRequest stamp (started_at,
+        # last_heartbeat, status.last_activity_at) and every comparison against
+        # one (idle/ceiling timeout, health monitor, request state, cancel)
+        # reads THIS, so a test can drive the idle timeout with a fake clock.
+        # Wall time by default: handlers/cli render started_at as an epoch.
+        self._clock: Callable[[], float] = clock if clock is not None else time.time
         self._clients: dict[str, ManagedClient] = {}
         self._tools: dict[str, ToolInfo] = {}
         self._resources: dict[str, ResourceInfo] = {}
@@ -2919,7 +2933,7 @@ class ClientManager:
                 # UPDATE heartbeat on ANY output from server. This includes JSON
                 # progress notifications (id: null) that don't resolve a request,
                 # so per-request liveness drives the idle timeout in _send_request.
-                now = time.time()
+                now = self._clock()
                 managed.status.last_activity_at = now
                 for req in managed.pending_requests.values():
                     req.last_heartbeat = now
@@ -3041,7 +3055,7 @@ class ClientManager:
             async for message in read_stream:
                 # Any output counts as per-request liveness, including progress
                 # notifications (id: null), so the idle timeout sees the keepalive.
-                now = time.time()
+                now = self._clock()
                 managed.status.last_activity_at = now
                 for req in managed.pending_requests.values():
                     req.last_heartbeat = now
@@ -3236,7 +3250,7 @@ class ClientManager:
     ) -> dict[str, Any]:
         """Send a JSON-RPC request and wait for response."""
         request_id = self._next_request_id(managed.config.name)
-        now = time.time()
+        now = self._clock()
 
         request = {
             "jsonrpc": "2.0",
@@ -3347,15 +3361,18 @@ class ClientManager:
         future, so a response arriving mid-slice is returned rather than dropped.
         Raises ``asyncio.TimeoutError`` on idle/ceiling so the caller maps it to the
         usual ``TimeoutError``.
+
+        The slice (``IDLE_POLL_SLICE_S``) is real event-loop time and only decides
+        how often the check runs; what the check *sees* is ``self._clock``.
         """
-        slice_s = min(idle_timeout_s, 1.0)
+        slice_s = min(idle_timeout_s, IDLE_POLL_SLICE_S)
         while True:
             try:
                 return await asyncio.wait_for(asyncio.shield(future), timeout=slice_s)
             except asyncio.TimeoutError:
                 if future.done():
                     return future.result()
-                now = time.time()
+                now = self._clock()
                 if now - pending.started_at >= ceiling_s:
                     logger.warning(
                         "[%s] request %d hit absolute ceiling (%.1fs)",
@@ -4047,7 +4064,7 @@ class ClientManager:
         while True:
             try:
                 await asyncio.sleep(HEALTH_CHECK_INTERVAL)
-                now = time.time()
+                now = self._clock()
 
                 # Periodic memory logging
                 if now - last_memory_log >= MEMORY_LOG_INTERVAL:
@@ -4154,7 +4171,7 @@ class ClientManager:
 
     def get_request_state(self, pending: PendingRequest) -> RequestState:
         """Determine current state of a pending request."""
-        now = time.time()
+        now = self._clock()
         elapsed = now - pending.started_at
         heartbeat_age = now - pending.last_heartbeat
 
@@ -4210,7 +4227,7 @@ class ClientManager:
         if pending.future.done():
             return ("already_complete", "Request already completed", False, None)
 
-        now = time.time()
+        now = self._clock()
         elapsed = now - pending.started_at
         heartbeat_age = now - pending.last_heartbeat
         was_stalled = heartbeat_age > HEARTBEAT_STALL_THRESHOLD
```

## Test bodies

Verbatim from the spike (`tests/test_client_manager.py`, from `class _FakeClock` through the end of `test_progress_notification_bumps_pending_heartbeat_sse`; `test_request_ceiling_ms_env_parsing` follows unchanged). Already `ruff format`-clean.

```python
class _FakeClock:
    """The request clock a test advances by hand (slice C3 of Consiliency/pmcp#235).

    ``now`` is what ``ClientManager(clock=...)`` reads; ``reads`` counts those
    reads, so a driver task can step the clock exactly once per idle check
    (``eventually(lambda: clock.reads >= k, interval=0)``) instead of racing a
    real timer. Nothing here consults wall time.
    """

    def __init__(self, start: float = 1_000.0) -> None:
        self.value = start
        self.reads = 0

    def now(self) -> float:
        self.reads += 1
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += seconds
        return self.value


class TestIdleTimeout:
    """Tests for the inactivity (idle) timeout on downstream requests (#79/1a).

    Time is a ``_FakeClock`` injected into ``ClientManager``; the real slice
    (``IDLE_POLL_SLICE_S``) is shrunk to 1 ms so the loop re-checks quickly, but
    every outcome is decided by fake-clock values alone. Steps are dyadic
    (0.125 s) so every ``>=`` comparison is exact.
    """

    @staticmethod
    def _managed(remote: bool = False) -> ManagedClient:
        """Build a ManagedClient with a mock process suitable for _send_request."""
        config = MagicMock()
        config.name = "test"
        status = ServerStatus(name="test", status=ServerStatusEnum.ONLINE, tool_count=0)
        process = MagicMock()
        process.returncode = None
        process.stdin = MagicMock()
        process.stdin.write = MagicMock()
        process.stdin.drain = AsyncMock()
        process.stdout = MagicMock()
        return ManagedClient(
            config=config, process=process, status=status, is_remote=remote
        )

    @staticmethod
    def _pending(clock: _FakeClock, timeout_ms: int) -> PendingRequest:
        """A PendingRequest stamped from the fake clock, as _send_request would."""
        return PendingRequest(
            request_id=1,
            server_name="test",
            tool_id="t::x",
            started_at=clock.value,
            last_heartbeat=clock.value,
            timeout_ms=timeout_ms,
            future=asyncio.get_running_loop().create_future(),
        )

    def test_default_request_clock_is_wall_time(self) -> None:
        """Without injection the request clock is the epoch, not monotonic.

        gateway.list_pending renders ``started_at`` with ``datetime.fromtimestamp``
        and ``pmcp status`` subtracts ``time.time()`` from it, so the default
        is a cross-module contract; moving it to a monotonic clock is a
        separate change, not a test seam.
        """
        assert ClientManager()._clock is time.time

    @pytest.mark.asyncio
    async def test_idle_timeout_survives_periodic_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A call that keeps producing output past the idle window completes.

        Five idle checks each find a heartbeat at most 0.125 s old (window 0.25 s)
        while the request itself ages to 0.5 s: liveness is per-heartbeat, not
        per-request. The driver records what every check saw.
        """
        monkeypatch.setattr("pmcp.client.manager.IDLE_POLL_SLICE_S", 0.001)
        clock = _FakeClock()
        manager = ClientManager(clock=clock.now)
        managed = self._managed()
        pending = self._pending(clock, timeout_ms=250)
        managed.pending_requests[1] = pending
        seen: list[tuple[int, float]] = []

        async def keepalive() -> None:
            # One step per idle check: once read k has happened nothing else
            # moves the clock, so `value - last_heartbeat` is exactly the age
            # check k computed. Then "emit output" and move 0.125 s on.
            for k in range(1, 5):
                await eventually(lambda: clock.reads >= k, interval=0)
                seen.append((clock.reads, clock.value - pending.last_heartbeat))
                pending.last_heartbeat = clock.value
                clock.advance(0.125)
            await eventually(lambda: clock.reads >= 5, interval=0)
            seen.append((clock.reads, clock.value - pending.last_heartbeat))
            pending.future.set_result({"ok": True})

        task = asyncio.create_task(keepalive())
        result = await manager._await_with_idle_timeout(
            managed, 1, pending, pending.future, idle_timeout_s=0.25, ceiling_s=100.0
        )
        await task
        assert result == {"ok": True}
        assert seen == [(1, 0.0), (2, 0.125), (3, 0.125), (4, 0.125), (5, 0.125)]
        assert clock.value - pending.started_at == 0.5  # outlived the 0.25 s window

    @pytest.mark.asyncio
    async def test_idle_timeout_fires_when_silent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A silent downstream times out exactly at the idle threshold and is removed."""
        monkeypatch.setattr("pmcp.client.manager.IDLE_POLL_SLICE_S", 0.001)
        clock = _FakeClock()
        manager = ClientManager(clock=clock.now)
        managed = self._managed()
        start = clock.value

        async def silence() -> None:
            # No output ever: each idle check finds the clock 0.125 s further on.
            # Bounded in fake steps so a broken idle path fails by name.
            for k in range(1, 9):
                await eventually(
                    lambda: clock.reads >= k or not managed.pending_requests,
                    interval=0,
                )
                if not managed.pending_requests:
                    return
                clock.advance(0.125)
            for req in managed.pending_requests.values():
                req.future.set_exception(
                    AssertionError("idle timeout never fired in 1.0 s of request-clock")
                )

        task = asyncio.create_task(silence())
        with pytest.raises(TimeoutError):
            await manager._send_request(
                managed, "tools/call", {}, tool_id="t::x", timeout_ms=250
            )
        await task

        # Read 1 stamped the request; checks 1 and 2 saw ages 0.125 and 0.25.
        assert (clock.reads, clock.value - start) == (3, 0.25)
        assert managed.pending_requests == {}
        assert managed.status.pending_request_count == 0

    @pytest.mark.asyncio
    async def test_absolute_ceiling_fires_for_chatty_call(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A continuously-heartbeating call that never resolves hits the ceiling."""
        monkeypatch.setenv("PMCP_REQUEST_CEILING_MS", "250")
        monkeypatch.setattr("pmcp.client.manager.IDLE_POLL_SLICE_S", 0.001)
        clock = _FakeClock()
        manager = ClientManager(clock=clock.now)
        managed = self._managed()
        start = clock.value

        async def chatty() -> None:
            # Fresh output before every idle check for 0.5 s of fake time (the
            # 0.5 s idle window never elapses), then silence, so a ceiling that
            # never fires still ends the request -- by the idle path, late.
            for k in range(1, 13):
                await eventually(
                    lambda: clock.reads >= k or not managed.pending_requests,
                    interval=0,
                )
                if not managed.pending_requests:
                    return
                if k <= 4:
                    for req in managed.pending_requests.values():
                        req.last_heartbeat = clock.value
                clock.advance(0.125)

        task = asyncio.create_task(chatty())
        with caplog.at_level(logging.WARNING, logger="pmcp.client.manager"):
            with pytest.raises(TimeoutError):
                await manager._send_request(
                    managed, "tools/call", {}, tool_id="t::x", timeout_ms=500
                )
        await task

        assert "hit absolute ceiling" in caplog.text
        # Read 1 stamped the request; check 2 saw 0.25 s of age, the ceiling.
        assert (clock.reads, clock.value - start) == (3, 0.25)
        assert managed.pending_requests == {}

    @pytest.mark.asyncio
    async def test_request_state_reads_the_request_clock(self) -> None:
        """get_request_state ages a request on the injected clock."""
        clock = _FakeClock()
        manager = ClientManager(clock=clock.now)
        pending = self._pending(clock, timeout_ms=1_000_000)

        assert manager.get_request_state(pending) is RequestState.PENDING
        clock.advance(HEARTBEAT_WARN_THRESHOLD + 1)
        assert manager.get_request_state(pending) is RequestState.ACTIVE
        clock.advance(HEARTBEAT_STALL_THRESHOLD - HEARTBEAT_WARN_THRESHOLD)
        assert manager.get_request_state(pending) is RequestState.STALLED

    @pytest.mark.asyncio
    async def test_cancel_request_reads_the_request_clock(self) -> None:
        """cancel_request judges staleness and reports elapsed on the injected clock."""
        clock = _FakeClock()
        manager = ClientManager(clock=clock.now)
        managed = self._managed()
        manager._clients["test"] = managed
        managed.pending_requests[1] = self._pending(clock, timeout_ms=1_000_000)

        clock.advance(HEARTBEAT_STALL_THRESHOLD + 1)
        status, _message, was_stalled, elapsed = await manager.cancel_request("test::1")
        assert (status, was_stalled, elapsed) == ("cancelled", True, 121.0)

    @pytest.mark.asyncio
    async def test_health_monitor_reads_the_request_clock(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The health monitor's stall warning ages a request on the injected clock."""
        monkeypatch.setattr("pmcp.client.manager.HEALTH_CHECK_INTERVAL", 0.001)
        clock = _FakeClock()
        manager = ClientManager(clock=clock.now)
        managed = self._managed()
        manager._clients["test"] = managed
        managed.pending_requests[1] = self._pending(clock, timeout_ms=1_000_000)
        clock.advance(HEARTBEAT_STALL_THRESHOLD + 1)

        task = asyncio.create_task(manager._health_monitor_loop())
        try:
            with caplog.at_level(logging.WARNING, logger="pmcp.client.manager"):
                await eventually(
                    lambda: "Request test::1 stalled (no heartbeat for 121s)"
                    in caplog.text,
                    message="stall warning never logged with the fake-clock age",
                )
        finally:
            task.cancel()
            await task  # the loop turns CancelledError into a clean return

    @pytest.mark.asyncio
    async def test_progress_notification_bumps_pending_heartbeat_stdout(self) -> None:
        """An id:null JSON notification stamps last_heartbeat from the clock (stdio)."""
        clock = _FakeClock()
        manager = ClientManager(clock=clock.now)
        managed = self._managed()
        # Graceful branch in finally; avoid scheduling a reconnect task.
        managed.status.status = ServerStatusEnum.OFFLINE
        stale = clock.value - 10
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        pending = PendingRequest(
            request_id=1,
            server_name="test",
            tool_id="t::x",
            started_at=stale,
            last_heartbeat=stale,
            timeout_ms=30000,
            future=future,
        )
        managed.pending_requests[1] = pending

        notif = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"progress": 1},
                }
            )
            + "\n"
        )
        cast(Any, managed.process).stdout.read = AsyncMock(
            side_effect=[notif.encode(), b""]
        )

        await manager._read_stdout("test", managed)

        assert pending.last_heartbeat == clock.value
        assert managed.status.last_activity_at == clock.value
        # Retrieve the ConnectionError set by the EOF finally so it is not logged.
        with contextlib.suppress(Exception):
            pending.future.exception()

    @pytest.mark.asyncio
    async def test_progress_notification_bumps_pending_heartbeat_sse(self) -> None:
        """An id:null JSON notification stamps last_heartbeat from the clock (SSE)."""
        clock = _FakeClock()
        manager = ClientManager(clock=clock.now)
        managed = self._managed(remote=True)
        managed.status.status = ServerStatusEnum.OFFLINE
        stale = clock.value - 10
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        pending = PendingRequest(
            request_id=1,
            server_name="test",
            tool_id="t::x",
            started_at=stale,
            last_heartbeat=stale,
            timeout_ms=30000,
            future=future,
        )
        managed.pending_requests[1] = pending

        msg = MagicMock()
        msg.message.model_dump.return_value = {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": {},
        }

        async def stream() -> Any:
            yield msg

        await manager._read_sse("test", managed, stream())

        assert pending.last_heartbeat == clock.value
        assert managed.status.last_activity_at == clock.value
        with contextlib.suppress(Exception):
            pending.future.exception()

```

## Verification

Every step below was run this session against the spike (`src/` + `tests/` as in the
diff and test bodies above), on Python 3.10.12 via `uv run`. Node ids were validated
with `--collect-only` before use. Expected results are the measured ones.

```bash
# 0. Node ids exist (6 before C3 + 4 added = 10 in the class).
uv run pytest "tests/test_client_manager.py::TestIdleTimeout" --collect-only -q --cov-fail-under=0
#   → 10 tests collected

# 1. The seam boundary is exactly 7 in / 11 out.
grep -c 'self\._clock()' src/pmcp/client/manager.py     # → 7
grep -c 'time\.time()'   src/pmcp/client/manager.py     # → 11
grep -n 'time\.monotonic' src/pmcp/client/manager.py    # → nothing

# 2. CI gates.
uv run ruff check src/ tests/                            # → All checks passed!
uv run ruff format --check src/ tests/                   # → 161 files already formatted
uv run mypy src/pmcp/client/manager.py                   # → Success: no issues found in 1 source file

# 3. The converted class, with per-test call times.
uv run pytest "tests/test_client_manager.py::TestIdleTimeout" -q --cov-fail-under=0 --durations=0
#   → 10 passed; every converted test ≤ 0.01 s call time
#     (baseline before C3: 0.50 s survives, 0.41 s ceiling, 0.20 s silent)

# 4. The whole file (every other test that builds PendingRequest with time.time() stamps
#    and goes through a seam method on the default clock).
nohup uv run pytest tests/test_client_manager.py -q --cov-fail-under=0 > /tmp/claude-1000/<lane>/c3-full-file.log 2>&1 & disown
#   → 253 passed in 12.00s

# 5. Whole suite (detached; poll the log yourself -- a disowned job sends no notification). Use a lane-unique log dir.
nohup uv run pytest tests/ -q --cov-fail-under=0 > /tmp/claude-1000/<lane>/c3-suite.log 2>&1 & disown
#   → 4068 passed, 3 skipped, 25 deselected in 592.43s

# 6. Flake ratio and per-run time, 40 runs (pytest-repeat is not installed).
for i in $(seq 1 40); do
  uv run pytest "tests/test_client_manager.py::TestIdleTimeout" -q --cov-fail-under=0 --durations=0 \
    > /tmp/claude-1000/<lane>/c3-run-$i.log 2>&1 && echo PASS || echo FAIL
done | sort | uniq -c
#   → 40 PASS, 0 FAIL

# 7. Plan-consistency gate (plan-only commit; must stay 0).
uv run python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md
#   → blocking inconsistencies: 0
```

### Measured this session

| check | result |
|---|---|
| 0. collect | 10 tests collected in the class (`10 tests collected in 0.05s`); the 7 pre-existing target ids resolved by content |
| 1. boundary | `self._clock()` = 7; `time.time()` = 11; `monotonic` = 0 |
| 2. ruff check / format / mypy | `All checks passed!` / `161 files already formatted` (after one `ruff format` reflow of a `cancel_request(...)` call) / `Success: no issues found in 1 source file` |
| 3. class | `10 passed in 0.84s`; each converted test **0.01 s** call time (`--durations=0`) |
| 4. whole file | **253 passed in 12.00s** |
| 6. flake loop | **40/40 PASS, 0 FAIL**; summed call time of the class per run: mean **0.045 s**, every run 0.040–0.07 s; the pytest *process* wall was 1.72–2.43 s per run (mean 1.92 s), all of it collection/import of the 6 800-line module — the tests themselves never wait |
| 7. plan consistency | `blocking inconsistencies: 0` |

### Whole suite

Run detached on the spike after the mutation runs and polled to completion before the
spike was reverted: **`4068 passed, 3 skipped, 25 deselected in 592.43s (0:09:52)`**,
no failures (`-x` never stopped it). Slowest were the pre-existing 60 s
`test_progressive_disclosure` docs-query tests, unrelated to this slice.

### Mutation table (each applied to the spike, confirmed by `diff` against the saved spike copy, then reverted)

The confirmation column quotes the `diff` hunk header (`NNNNcNNNN`), which names the
mutated line; `git diff --stat` against HEAD was non-empty for every mutant (27–29
insertions). Runs used `--tb=line`; the "red for" column is the first `E` line verbatim.

| # | mutation (spike line) | applied | result | red for |
|---|---|---|---|---|
| M1 | `_await_with_idle_timeout` `:3375` `self._clock()` → `time.time()` | `3375c3375` | **3 failed**, 7 passed | survives: `asyncio.exceptions.TimeoutError` (wall `now` − fake `last_heartbeat` ≈ 1.79e9 ≥ window at the first check); silent & ceiling: `assert (1, 0.125) == (3, 0.25)` — the fake clock was read once (the stamp), never by a check |
| M2 | `_send_request` `:3253` → `time.time()` | `3253c3253` | **2 failed**, 8 passed | silent: `AssertionError: idle timeout never fired in 1.0 s of request-clock` (fake `now` − wall stamp is negative forever; the bounded driver ends it by name); ceiling: `AssertionError: assert 'hit absolute ceiling' in ''` (the idle path fired late instead) |
| M3 | `_read_stdout` `:2936` → `time.time()` | `2936c2936` | **1 failed** | `assert 1790166536.5362232 == 1000.0` — stamp on wall, clock on fake |
| M4 | `_read_sse` `:3058` → `time.time()` | `3058c3058` | **1 failed** | `assert 1790166538.290628 == 1000.0` |
| M5 | `get_request_state` `:4174` → `time.time()` | `4174c4174` | **1 failed** | `assert <RequestState.TIMEOUT> is <RequestState.PENDING>` — a fresh request looks 1.79e9 s old |
| M6 | `cancel_request` `:4230` → `time.time()` | `4230c4230` | **1 failed** | `assert ('cancelled', …, 1790165541.86) == ('cancelled', True, 121.0)` — reported `elapsed` is wall arithmetic |
| M7 | `_health_monitor_loop` `:4067` → `time.time()` | `4067c4067` | **1 failed** (5.01 s — the `eventually` hang guard) | `AssertionError: stall warning never logged with the fake-clock age` (it logged "no heartbeat for 1790166…s" instead of "121s") |
| M8 | default `time.time` → `time.monotonic` (`:1057`) | `1057c1057` | **1 failed** | `assert <built-in function monotonic> is <built-in function time>` — the epoch contract with `gateway.list_pending`/`pmcp status` |
| M9 | slice literal restored: `min(idle_timeout_s, IDLE_POLL_SLICE_S)` → `min(idle_timeout_s, 1.0)` (`:3368`) | `3368c3368` | **10 passed** (not red) | *cannot* be red without an upper-bound wall assertion, which is the flaky class this slice removes; detected by measurement instead: survives **1.25 s**, ceiling **1.01 s**, silent **0.50 s** call time vs 0.01 s each |
| M10 | idle `>=` → `>` (`:3384`) — a *behaviour* mutant | `3384c3384` | **1 failed** | silent: `assert (4, 0.375) == (3, 0.25)` — fired one check late; the exact-boundary assertion has teeth |
| M11 | ceiling `>=` → `>` (`:3376`) — behaviour mutant | `3376c3376` | **1 failed** | ceiling: `assert (4, 0.375) == (3, 0.25)` |

After the last mutant `manager.py` was restored from the spike copy (`cmp` → identical).

### Acceptance criteria (runnable, all measured green above)

1. `TestIdleTimeout` passes with every converted test ≤ 0.01 s call time (step 3).
2. 40/40 repeated runs pass (step 6). The per-run call time is *reported*, not gated — mean 0.045 s, max 0.07 s this session vs a 1.11 s baseline for the three converted tests — because a ceiling on it would be the wall-clock upper bound this slice removes.
3. `grep -c 'self\._clock()'` = 7 and `grep -c 'time\.time()'` = 11 in `manager.py` (step 1).
4. Mutants M1–M8, M10, M11 each fail at least one named test (mutation table).
5. `ruff check` and `ruff format --check` clean on `src/` and `tests/` (step 2).
6. `tests/test_client_manager.py` fully green (step 4: 253 passed).
7. No converted test contains `elapsed <`, `time.time()`, `time.monotonic()` or `asyncio.sleep(<nonzero>)`:
   `sed -n '/^class _FakeClock/,/def test_request_ceiling_ms_env_parsing/p' tests/test_client_manager.py | grep -nE 'elapsed <|= time\.time\(\)|time\.monotonic|asyncio\.sleep\([1-9.]'` → empty (measured: empty; the docstring of `test_default_request_clock_is_wall_time` mentions `time.time()` in prose, which is why the pattern requires an assignment).

## Non-goals (explicit)

- **Monotonic durations** — follow-up, Design decision 3.
- **`tests/test_server_lifecycle.py:90`** (`sleep(20)` vs `server.py:965` `timeout=10.0`) and
  **`tests/test_scoped_advisor_audit.py:461`** (1.5 s negative soak): C2a's "Kept on purpose"
  list governs; not touched.
- **`test_c04_idle_timeout_notifies_downstream`** (`:6728`, 20 ms real, lower-bound only): kept.
- **The other 24 `PendingRequest(...)` constructions in the test file** that stamp
  `time.time()`: they run on the default clock and are unaffected; converting them is churn.
- **`IDLE_POLL_SLICE_S = 0`** (spin instead of a 1 ms timer) was considered and rejected
  as further from production behaviour for no measurable gain (0.01 s per test already).
- No change to `tests/_timing.py`, `tests/conftest.py`, `SECURITY.md`, `plans/phase-plan-v13-*.md`, `specs/phase-plans-v13.md`.

## Risks

- **Coherence drift**: a future `time.time()` added to a seam method would silently
  reintroduce wall/fake mixing. Mitigation: the `PendingRequest` field comment and the
  `__init__` comment name the group, and acceptance criterion 3 pins the counts — bump
  both numbers deliberately when adding a site.
- **Ordering argument in Design decision 4** relies on the loop's ready-queue semantics.
  It is asserted, not assumed (`seen == [...]`, `clock.reads == 3`), and measured 40/40
  under concurrent load (the full-file run and the loop overlapped for part of the
  window). If it ever fails on CI, the failure names the read count; the fallback is
  `>=` on `reads` with the ages still exact — do not add a sleep.
- **`caplog` and the health loop** at `HEALTH_CHECK_INTERVAL=0.001` logs the stall
  warning every millisecond until cancelled (~10–50 records); harmless, but keep the
  cancel in `finally`.
- **`_enqueue_outbound` writer task** created by `cancel_request`'s cancellation
  notification is left to the loop teardown, exactly as the existing `test_c04_*`
  tests do.

## Handoff

- Branch: implement on a fresh worktree from `origin/main`; apply the production diff
  and test bodies verbatim; add the CHANGELOG line; run Verification 0–7.
- Commit message subject: `test: inject the request clock so the idle-timeout tests never race wall time (slice C3 of Consiliency/pmcp#235)`.
  Body must reference `Consiliency/pmcp#235` and **must not** use a closing keyword —
  closure of Consiliency/pmcp#235 is decided after merge, not by this slice.
- PR: panel CR + reconcile before merge (repo rule); mention M9 honestly as
  "measured, not asserted".
- Follow-up issue to file after merge: "Request durations on a monotonic clock" with the
  consumer list from Design decision 3.

## Unverified / notes

- The 40-run loop measures this host under moderate load (the full-file run overlapped
  the first ~15 iterations); CI runners are slower but the tests contain no wall-clock
  upper bound to be slower *against*.
