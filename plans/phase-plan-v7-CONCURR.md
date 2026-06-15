---
phase_loop_plan_version: 1
phase: CONCURR
roadmap: specs/phase-plans-v7.md
roadmap_sha256: f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5
---

# CONCURR: Concurrency & Lifecycle Hardening

## Context

Phase CONCURR implements Phase 2 of `specs/phase-plans-v7.md`: close the shared-gateway lifecycle races in `src/pmcp/client/manager.py` while preserving backward-compatible gateway request IDs and existing singleton-lock behavior. The roadmap hash was verified from `specs/phase-plans-v7.md` as `f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5`, and canonical `.phase-loop/` state marks REDACT complete and CONCURR unplanned.

Current code already has same-server singleflight (`_connect_tasks`) and a lifecycle lock around refresh/disconnect paths, but `connect_all(...)`, `ensure_connected(...)`, and `connect_server(...)` can still mutate `_clients`, `_servers`, lazy registrations, and catalog indexes outside `_lifecycle_lock`. Background tasks are also split between `ManagedClient.read_task`, untracked stderr reader tasks, reconnect tasks created from read-loop finally blocks, and in-flight connect tasks. CONCURR should tighten these existing mechanisms rather than introduce a new lifecycle abstraction.

`src/pmcp/client/manager.py` is the single writer for lifecycle implementation. Request cancellation will use the roadmap's monotonic-across-reconnect option so the public `server::local_id` request ID shape remains compatible and no gateway schema or CLI output files need to be owned by this phase.

## Interface Freeze Gates

- [ ] IF-0-CONCURR-1 - `ClientManager` serializes every client/catalog mutation from `connect_all(...)`, `connect_server(...)`, `ensure_connected(...)`, reconnect attempts, `refresh(...)`, `disconnect_server(...)`, and `disconnect_all(...)` through `_lifecycle_lock`; same-server singleflight still deduplicates concurrent starts inside that boundary. `ClientManager` owns a manager-level background task registry for in-flight connect, stdout/SSE readers, stderr readers, reconnect loops, and health monitoring tasks, and `disconnect_all(...)`/`GatewayServer.shutdown()` cancel and await that registry before returning. PMCP request IDs remain in the existing `server::local_id` public shape, but `local_id` is allocated from a manager-level monotonic per-server counter that survives reconnects so stale cancel IDs cannot target a later connection.

## Lane Index & Dependencies

- SL-0 - Manager concurrency regression tests; Depends on: (none); Blocks: SL-1, SL-2, SL-3; Parallel-safe: no
- SL-1 - Manager lifecycle serialization and task tracking; Depends on: SL-0; Blocks: SL-2, SL-3; Parallel-safe: no
- SL-2 - Gateway shutdown lifecycle coverage; Depends on: SL-1; Blocks: SL-3; Parallel-safe: no
- SL-3 - CONCURR verification and closeout; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Manager Concurrency Regression Tests

- **Scope**: Add failing-first manager tests for lifecycle-lock coverage, background-task cleanup, stale cancel refusal, and manager-level reconnect storm guarding.
- **Owned files**: `tests/test_client_manager.py`
- **Interfaces provided**: failing-first coverage for IF-0-CONCURR-1 manager invariants; test helpers for controlled connect/refresh/disconnect interleavings; regression cases for monotonic request IDs and reconnect task deduplication
- **Interfaces consumed**: existing `ClientManager`, `ManagedClient`, `PendingRequest`, `ServerStatus`, `ServerStatusEnum`, `ResolvedServerConfig`, `LocalMcpServerConfig`, current pending-request display contract
- **Parallel-safe**: no
- **Tasks**:
  - test: Add a regression where one task begins lazy `ensure_connected(...)` or `connect_server(...)`, a concurrent `refresh(...)` or `disconnect_all(...)` starts before the connect finishes, and the test asserts lifecycle mutations do not interleave, no old process/client survives, and the final catalog/status state is coherent.
  - test: Add coverage proving `connect_all(...)` still shares duplicate server starts while holding the lifecycle boundary, including concurrent callers for the same lazy server.
  - test: Add a background-task cleanup regression that creates tracked read, stderr, reconnect, and in-flight connect tasks, calls `disconnect_all()`, and asserts each task is cancelled or completed and removed from the manager registry before return.
  - test: Add a stale-cancel regression where a pending request ID observed before reconnect cannot cancel a later request after the same server reconnects; the later request must remain pending unless its own current `server::local_id` is cancelled.
  - test: Add a reconnect-storm regression where the old `ManagedClient` is replaced during reconnect and duplicate read-loop failures still produce at most one manager-level reconnect task for that server.
  - verify: `uv run pytest tests/test_client_manager.py -k "refresh or reconnect or cancel or shutdown or concurrent or lifecycle"`
  - verify: `git diff --check -- tests/test_client_manager.py`

### SL-1 - Manager Lifecycle Serialization and Task Tracking

- **Scope**: Implement IF-0-CONCURR-1 inside `ClientManager` without changing gateway tool schemas or request ID formatting.
- **Owned files**: `src/pmcp/client/manager.py`
- **Interfaces provided**: locked lifecycle mutation boundary; manager-level background task registry; manager-level per-server reconnect guard; manager-level per-server monotonic request counters; unchanged public `server::local_id` request IDs with no local ID reuse across reconnects
- **Interfaces consumed**: SL-0 regression tests, existing `_lifecycle_lock`, `_connect_tasks`, `_spawn_semaphore`, `_cleanup_client(...)`, `_disconnect_all_unlocked(...)`, `_read_stdout(...)`, `_read_sse(...)`, `_read_stderr(...)`, `_reconnect_loop(...)`, `_send_request(...)`, `cancel_request(...)`
- **Parallel-safe**: no
- **Tasks**:
  - test: Run the SL-0 targeted tests and confirm the new tests fail on the pre-fix code before implementation proceeds.
  - impl: Split public locked entry points from private unlocked helpers so `connect_all(...)`, `connect_server(...)`, `ensure_connected(...)`, reconnect attempts, `refresh(...)`, `disconnect_server(...)`, and `disconnect_all(...)` share one lifecycle boundary without deadlocking when refresh calls connect logic internally.
  - impl: Keep same-server singleflight semantics inside the lifecycle boundary, including duplicate-name deduplication and existing retry behavior.
  - impl: Add a small manager-owned task registration helper used by connect tasks, stdio stdout readers, stderr readers, remote/SSE readers, reconnect loops, and the existing health monitor so teardown has one cancellation surface.
  - impl: Update `disconnect_all(...)`, `_disconnect_all_unlocked(...)`, `_cleanup_client(...)`, and connect-failure paths to cancel and await tracked tasks, clear `_connect_tasks`, and leave no live task references after shutdown.
  - impl: Move the reconnect storm guard from `ManagedClient.reconnecting` to manager-level per-server state so replacing the managed client cannot re-enable duplicate reconnect loops.
  - impl: Allocate request IDs from a manager-level monotonic counter keyed by server name instead of resetting with each `ManagedClient`, preserving current JSON-RPC numeric IDs and public `server::local_id` formatting while preventing stale cancel reuse after reconnect.
  - verify: `uv run pytest tests/test_client_manager.py -k "refresh or reconnect or cancel or shutdown or concurrent or lifecycle"`
  - verify: `git diff --check -- src/pmcp/client/manager.py`

### SL-2 - Gateway Shutdown Lifecycle Coverage

- **Scope**: Prove gateway shutdown waits for the hardened manager lifecycle cleanup and preserves timeout/error behavior at the server boundary.
- **Owned files**: `src/pmcp/server.py`, `tests/test_server_lifecycle.py`
- **Interfaces provided**: shutdown coverage for IF-0-CONCURR-1; confirmation that `GatewayServer.shutdown()` delegates to the manager cleanup path and releases the singleton lock after cleanup, timeout, or error
- **Interfaces consumed**: SL-1 manager cleanup contract, existing `GatewayServer.shutdown()`, existing `release_singleton_lock()` behavior, existing shutdown timeout tests
- **Parallel-safe**: no
- **Tasks**:
  - test: Extend `tests/test_server_lifecycle.py` so shutdown awaits the manager cleanup call that cancels tracked background tasks, using a manager double that records ordering relative to singleton-lock release.
  - test: Preserve the existing timeout and exception-path expectations: shutdown must not raise, must still release the singleton lock, and must not require live downstream servers or credentials.
  - impl: Keep `src/pmcp/server.py` unchanged unless the new test exposes a real boundary bug; if a change is required, keep it limited to shutdown ordering around the existing manager call and singleton-lock release.
  - verify: `uv run pytest tests/test_server_lifecycle.py -k "shutdown or lifecycle"`
  - verify: `git diff --check -- src/pmcp/server.py tests/test_server_lifecycle.py`

### SL-3 - CONCURR Verification and Closeout

- **Scope**: Run the CONCURR verification set, confirm IF-0-CONCURR-1 is fully produced, and prepare runner closeout evidence without owning additional source files.
- **Owned files**: none
- **Interfaces provided**: CONCURR verification evidence; IF-0-CONCURR-1 completion checklist; phase-owned dirty-path inventory
- **Interfaces consumed**: IF-0-CONCURR-1, SL-0 test results, SL-1 implementation results, SL-2 shutdown results, roadmap CONCURR exit criteria
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm no lane-owned file overlaps another lane-owned file and no executor touched files outside the active CONCURR ownership set.
  - test: Confirm lifecycle serialization, no orphaned subprocess/task, stale cancel protection, and reconnect-storm guard each have failing-first regression coverage in `tests/test_client_manager.py` or `tests/test_server_lifecycle.py`.
  - verify: `uv run pytest tests/test_client_manager.py tests/test_server_lifecycle.py -k "refresh or reconnect or cancel or shutdown or concurrent"`
  - verify: `TMPDIR=/var/tmp uv run pytest`
  - verify: `uv run ruff check .`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `git status --short`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
uv run pytest tests/test_client_manager.py tests/test_server_lifecycle.py -k "refresh or reconnect or cancel or shutdown or concurrent"
TMPDIR=/var/tmp uv run pytest
uv run ruff check .
uv run mypy src/pmcp --exclude baml_client
git status --short
```

Effective automation.suite_command:

```bash
TMPDIR=/var/tmp uv run pytest && uv run ruff check . && uv run mypy src/pmcp --exclude baml_client
```

## Acceptance Criteria

- [ ] `refresh(force)` and `disconnect_all()` serialize against concurrent `ensure_connected(...)`, `connect_server(...)`, and `connect_all(...)`; the regression test leaves no orphaned subprocess, leaked client, or torn catalog after a lazy-connect race.
- [ ] Reconnect, stderr-reader, stdout/SSE-reader, health-monitor, and in-flight connect tasks are tracked in a manager-level task registry and cancelled/awaited by `disconnect_all()` and `GatewayServer.shutdown()`.
- [ ] Request IDs remain compatible with the existing `server::local_id` shape, but local IDs are monotonic per server across reconnects so a stale `gateway.cancel("srv::N")` cannot cancel a newer request on a replacement connection.
- [ ] The reconnect-storm guard is keyed by server name at manager level and survives `ManagedClient` replacement across crash/reconnect cycles.
- [ ] `uv run pytest tests/test_client_manager.py tests/test_server_lifecycle.py -k "refresh or reconnect or cancel or shutdown or concurrent"` passes.
- [ ] Full verification passes with `TMPDIR=/var/tmp uv run pytest`, `uv run ruff check .`, and `uv run mypy src/pmcp --exclude baml_client`.
