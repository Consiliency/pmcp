# REFRESH: Refresh and In-Flight Semantics

## Context

Phase 1 serialized `ClientManager` lifecycle mutation and same-server startup, but `gateway.refresh` still has no explicit behavior when another client has pending downstream requests. Today refresh resolves configs, replaces startup observations, disconnects all servers, registers lazy configs, and reconnects eager configs without first reporting or controlling active invokes, reads, prompts, or lazy-start/provision calls.

This phase freezes a conservative shared-service policy: `gateway.refresh` refuses by default when pending downstream requests exist, and only cancels them when the caller sets `force=true`. No drain mode is introduced in this phase. Refresh input/output changes are additive, existing fields remain unchanged, and pending request IDs keep the existing `server::local_id` format.

## Interface Freeze Gates

- [x] IF-0-REFRESH-1 — `RefreshInput` adds optional `force: bool = False`; when `force` is false and pending requests exist, `GatewayTools.refresh(...)` returns without disconnecting, reconnecting, or replacing startup observations.
- [x] IF-0-REFRESH-2 — `RefreshOutput` keeps existing fields and adds optional/defaulted counters: `pending_requests_seen`, `pending_requests_cancelled`, `pending_requests_refused`, and `pending_requests_remaining`; existing callers that ignore new fields remain compatible.
- [x] IF-0-REFRESH-3 — Forced refresh cancels all pending downstream requests before disconnect/reconnect, reports the cancellation count in `RefreshOutput`, and logs the disruptive action.
- [x] IF-0-REFRESH-4 — `gateway.list_pending` remains accurate before a refused refresh, after a refused refresh, and after a forced refresh; pending request IDs and `PendingRequestInfo` fields are unchanged.
- [x] IF-0-REFRESH-5 — Startup observations are replaced only after config loading and startup resolution succeed and after the pending-request policy allows refresh to proceed.
- [x] IF-0-REFRESH-6 — Refresh racing with lazy-start or provision-connect does not deadlock with Phase 1 lifecycle/connect coordination; normal downstream tool invokes do not hold refresh locks for their full response duration.
- [x] IF-0-REFRESH-7 — Gateway tool schema for `gateway.refresh` documents `force` without adding new tool names or changing required fields.

## Lane Index & Dependencies

- SL-0 — Refresh public contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3; Parallel-safe: no
- SL-1 — Manager pending-request helpers; Depends on: SL-0; Blocks: SL-2, SL-3; Parallel-safe: yes
- SL-2 — Gateway refresh policy; Depends on: SL-0, SL-1; Blocks: SL-3, SL-4; Parallel-safe: no
- SL-3 — Refresh race and compatibility tests; Depends on: SL-0, SL-1, SL-2; Blocks: SL-4; Parallel-safe: no
- SL-4 — Documentation impact and closeout; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Refresh Public Contract

- **Scope**: Add the additive refresh input/output contract and gateway tool schema fields without changing existing required fields or tool names.
- **Owned files**: `src/pmcp/types.py`
- **Interfaces provided**: `RefreshInput.force`, `RefreshOutput.pending_requests_seen`, `RefreshOutput.pending_requests_cancelled`, `RefreshOutput.pending_requests_refused`, `RefreshOutput.pending_requests_remaining`
- **Interfaces consumed**: existing `RefreshInput.source`, `RefreshInput.reason`, existing `RefreshOutput.ok`, `servers_seen`, `servers_online`, `tools_indexed`, `revision_id`, `errors`
- **Parallel-safe**: no
- **Tasks**:
  - test: Defer schema and handler assertions to SL-2/SL-3 because `tests/test_tools.py` owns gateway-facing refresh compatibility.
  - impl: Add `force: bool = False` to `RefreshInput`.
  - impl: Add defaulted integer counters to `RefreshOutput`: `pending_requests_seen=0`, `pending_requests_cancelled=0`, `pending_requests_refused=0`, and `pending_requests_remaining=0`.
  - impl: Keep all existing `RefreshOutput` fields required and unchanged.
  - verify: `uv run ruff check src/pmcp/types.py`

### SL-1 — Manager Pending-Request Helpers

- **Scope**: Add manager-level helpers for snapshotting and force-cancelling all pending downstream requests so refresh policy can be implemented without duplicating request internals in `GatewayTools`.
- **Owned files**: `src/pmcp/client/manager.py`, `tests/test_client_manager.py`
- **Interfaces provided**: `ClientManager.get_pending_requests(...)` snapshot contract, new private/public helper such as `ClientManager.cancel_all_pending_requests() -> int`
- **Interfaces consumed**: existing `ManagedClient.pending_requests`, `PendingRequest.future`, `ManagedClient.status.pending_request_count`, existing `ClientManager.cancel_request(...)` semantics
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a manager test proving `get_pending_requests()` returns a stable list snapshot that is not affected when the caller mutates the returned list.
  - test: Add a manager test proving `cancel_all_pending_requests()` cancels each pending future, removes pending entries from each managed client, updates each status pending count, and returns the number cancelled.
  - test: Add a manager test proving already-completed pending futures are skipped or removed consistently and do not inflate the cancelled count.
  - impl: Snapshot `self._clients.items()` and each `pending_requests.items()` before iterating so cancellation cannot fail because registries change while requests resolve.
  - impl: Do not acquire the global lifecycle lock for normal pending snapshots; cancellation should be short and non-blocking and must not wait on downstream responses.
  - verify: `uv run pytest tests/test_client_manager.py -k "pending or cancel_all or request_state"`
  - verify: `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py`

### SL-2 — Gateway Refresh Policy

- **Scope**: Enforce the frozen refresh policy in `GatewayTools.refresh(...)` and update the `gateway.refresh` tool schema.
- **Owned files**: `src/pmcp/tools/handlers.py`
- **Interfaces provided**: refused refresh behavior, forced cancellation behavior, updated `gateway.refresh` input schema, refresh logs for pending-request refusal/cancellation
- **Interfaces consumed**: `RefreshInput.force`, `RefreshOutput` pending counters from SL-0, pending helpers from SL-1, existing startup resolution helpers, existing `GatewayTools.set_startup_observations(...)`
- **Parallel-safe**: no
- **Tasks**:
  - test: Defer handler tests to SL-3 because `tests/test_tools.py` is owned there.
  - impl: Add `force` to `get_gateway_tool_definitions()` for `gateway.refresh` with description that default behavior refuses when pending requests exist.
  - impl: In `GatewayTools.refresh(...)`, load and resolve configs first, but do not call `set_startup_observations(...)` until after the pending-request policy check passes.
  - impl: If `force` is false and `get_pending_requests()` returns any pending requests, return `RefreshOutput(ok=False, ...)` with current registry metadata, current server/tool counts, `pending_requests_seen`, `pending_requests_refused`, and an actionable error mentioning `force=true` and `gateway.list_pending`.
  - impl: If `force` is true and pending requests exist, call the manager cancellation helper before `disconnect_all()`, report `pending_requests_cancelled`, and log the number cancelled.
  - impl: Preserve the existing successful refresh output shape plus additive counters.
  - impl: Preserve the existing catch-all error path, returning the additive counters at their default values unless cancellation already happened and can be reported locally.
  - verify: `uv run ruff check src/pmcp/tools/handlers.py`

### SL-3 — Refresh Race and Compatibility Tests

- **Scope**: Add gateway-facing tests for the new refresh policy, output compatibility, list-pending accuracy, startup observation ordering, and lazy-start race behavior.
- **Owned files**: `tests/test_tools.py`
- **Interfaces provided**: regression coverage for IF-0-REFRESH-1 through IF-0-REFRESH-7
- **Interfaces consumed**: refresh schemas from SL-0, manager pending helpers from SL-1, gateway policy from SL-2, existing `MockClientManager`, existing refresh compatibility tests
- **Parallel-safe**: no
- **Tasks**:
  - test: Extend `MockClientManager` with pending-request snapshot and force-cancel behavior needed by refresh tests, without adding public gateway fields beyond SL-0.
  - test: Add a default refresh test with one pending request proving refresh returns `ok=False`, reports `pending_requests_seen` and `pending_requests_refused`, does not call `disconnect_all()`, does not register lazy configs, does not connect eager configs, and leaves startup observations unchanged.
  - test: Add a forced refresh test proving pending requests are cancelled before disconnect/reconnect and output reports `pending_requests_cancelled`.
  - test: Add a compatibility assertion that refresh without pending requests still returns the existing success fields and default zero pending counters.
  - test: Add a schema assertion for `gateway.refresh` showing `force` is optional and no new required fields were introduced.
  - test: Add a list-pending accuracy test around refused and forced refresh using the mock manager or real pending request objects, whichever matches existing test style with least new scaffolding.
  - test: Add a refresh/lazy-start race test proving a pending lazy-start request causes default refresh refusal and forced refresh cancels before lifecycle replacement; do not require actual subprocess startup.
  - verify: `uv run pytest tests/test_tools.py -k "refresh or pending or lazy_start"`
  - verify: `uv run ruff check tests/test_tools.py`

### SL-4 — Documentation Impact and Closeout

- **Scope**: Record phase completion and document the new user-visible refresh force behavior.
- **Owned files**: `README.md`, `CHANGELOG.md`, `specs/phase-plans-v2.md`, `plans/phase-plan-v2-refresh.md`
- **Interfaces provided**: docs/release note decision, roadmap status update, completed acceptance checklist
- **Interfaces consumed**: SL-0 refresh contract, SL-1 manager helper behavior, SL-2 gateway policy, SL-3 verification results
- **Parallel-safe**: no
- **Tasks**:
  - impl: Update README command/tool documentation to state that `gateway.refresh` refuses while requests are pending unless `force=true`, and points users to `gateway.list_pending`.
  - impl: Add a CHANGELOG entry under Unreleased noting the new refresh pending-request policy if this branch is release-bound.
  - impl: Mark Phase 2 exit criteria in `specs/phase-plans-v2.md` after implementation and verification complete.
  - impl: Mark this plan's acceptance criteria complete and record any execution deviations.
  - verify: `uv run pytest tests/test_client_manager.py tests/test_tools.py`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/tools/handlers.py tests/test_client_manager.py tests/test_tools.py`

## Verification

Lane-specific verification:

- `uv run ruff check src/pmcp/types.py`
- `uv run pytest tests/test_client_manager.py -k "pending or cancel_all or request_state"`
- `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py`
- `uv run ruff check src/pmcp/tools/handlers.py`
- `uv run pytest tests/test_tools.py -k "refresh or pending or lazy_start"`
- `uv run ruff check tests/test_tools.py`

Whole-phase regression:

- `uv run pytest tests/test_client_manager.py tests/test_tools.py`
- `uv run pytest tests/test_lazy_start.py tests/test_server_lifecycle.py tests/test_http_transport.py tests/test_cli.py` if handler changes affect startup, HTTP shared-service behavior, or CLI refresh/status output.
- `uv run pytest` before release handoff if time permits.

## Acceptance Criteria

- [x] `gateway.refresh` refuses by default when pending downstream requests exist and does not disconnect, reconnect, register lazy configs, or replace startup observations.
- [x] `gateway.refresh(force=true)` cancels pending downstream requests before disconnect/reconnect and reports how many were cancelled.
- [x] Refresh output preserves existing fields and adds pending-request counters with backward-compatible defaults.
- [x] Refresh logs and errors communicate whether requests were refused or force-cancelled and tell users to inspect `gateway.list_pending` when relevant.
- [x] `gateway.list_pending` remains accurate before and after refused refresh and after forced refresh.
- [x] Startup observations are replaced only after config resolution succeeds and pending-request policy permits refresh to proceed.
- [x] Refresh racing with an in-flight tool call or lazy-start path cannot corrupt manager state or deadlock with Phase 1 lifecycle/connect coordination.
- [x] Pending request IDs and `PendingRequestInfo` output fields remain unchanged.
- [x] Tests cover default refusal, forced cancellation, no-pending compatibility, list-pending accuracy, startup observation preservation on refusal, and refresh/lazy-start race behavior.

## Execution Notes

- Completed SL-0 through SL-4 in the main Codex thread; no worker fanout used.
- No drain mode was added; default refresh refusal and `force=true` cancellation are the frozen policy.
- Verification run: `uv run ruff check src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/tools/handlers.py tests/test_client_manager.py tests/test_tools.py`, `uv run pytest tests/test_client_manager.py -k "pending or cancel_all or request_state"`, and `uv run pytest tests/test_tools.py -k "refresh or pending or lazy_start"`.
