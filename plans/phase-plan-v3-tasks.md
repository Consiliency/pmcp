# TASKS: Task-Aware Gateway Execution

## Context

Phase 2 of `specs/phase-plans-v3.md` maps MCP `2025-11-25` task-augmented execution onto PMCP's existing pending-request, cancellation, lifecycle, and status model. Phase 1 has already frozen the protocol and metadata contract: PMCP records negotiated protocol versions and preserves `ToolInfo.execution`, including `execution.taskSupport`.

The current implementation tracks in-flight JSON-RPC calls as `PendingRequest` objects in `src/pmcp/client/manager.py`; `gateway.list_pending` and `gateway.cancel` expose those request IDs as `server::local_id`. MCP task IDs must remain separate opaque downstream task identifiers. A task-augmented `tools/call` can return a task creation result immediately, while the real operation continues through `tasks/get`, `tasks/result`, and `tasks/cancel`.

MCP task support is negotiated through server capabilities and per-tool `execution.taskSupport`, where `forbidden` is the default, `optional` allows task-augmented execution, and `required` requires it. Task states include `working`, `input_required`, `completed`, `failed`, and `cancelled`; missing or expired task IDs should be surfaced as structured gateway errors rather than invented local terminal states.

## Interface Freeze Gates

- [x] IF-0-TASKS-1 — `ToolInfo.execution["taskSupport"]` and `ServerStatus.server_capabilities["tasks"]` are the only task-support signals; PMCP normalizes absent or unknown tool task support to `forbidden` for execution decisions while preserving raw metadata.
- [x] IF-0-TASKS-2 — `ClientManager.call_tool(...)` accepts an explicit task-augmentation mode, sends `tools/call` params with `task` metadata only when supported or required, and returns task creation results without treating them as final tool output.
- [x] IF-0-TASKS-3 — PMCP tracks brokered MCP tasks as separate `McpTaskRecord` entries keyed by opaque downstream `taskId`, with optional linkage to the originating PMCP pending request ID, server, tool ID, requestor context, timestamps, poll interval, ttl, and current status.
- [x] IF-0-TASKS-4 — `gateway.list_pending` remains backward compatible and additively includes task linkage for request-backed tasks; task visibility is also available through dedicated task surfaces without changing existing pending request ID formats.
- [x] IF-0-TASKS-5 — PMCP exposes `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and `gateway.tasks_cancel` as gateway-safe proxies for downstream `tasks/list`, `tasks/get`, `tasks/result`, and `tasks/cancel`, including server filters and structured not-supported/not-found errors.
- [x] IF-0-TASKS-6 — `gateway.cancel`, forced `gateway.refresh`, `gateway.disconnect_server`, and `gateway.restart_server` define deterministic behavior for both local pending requests and active MCP tasks: default lifecycle operations refuse active work, forced lifecycle operations cancel local requests and attempt downstream task cancellation before disconnect.
- [x] IF-0-TASKS-7 — Tests cover optional-task invocation, required-task invocation, unsupported required-task failure, `working`, `input_required`, `completed`, `failed`, `cancelled`, expired/missing task handling, idempotent cancellation, and lifecycle refusal/force behavior.

## Lane Index & Dependencies

- SL-0 — Task type contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 — Downstream task brokering; Depends on: SL-0; Blocks: SL-2, SL-4; Parallel-safe: no
- SL-2 — Gateway task tools, pending surfaces, and lifecycle behavior; Depends on: SL-0, SL-1; Blocks: SL-3, SL-4; Parallel-safe: no
- SL-3 — Server routing for task tools; Depends on: SL-0, SL-2; Blocks: SL-4; Parallel-safe: yes
- SL-4 — End-to-end, docs, and roadmap closeout; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Task Type Contract

- **Scope**: Add explicit task and task-proxy models while keeping existing invoke, pending, and lifecycle output shapes backward compatible.
- **Owned files**: `src/pmcp/types.py`
- **Interfaces provided**: `TaskSupportMode`, `McpTaskStatus`, `McpTaskInfo`, `McpTaskRecord`, `TaskMetadataInput`, `TasksListInput`, `TasksListOutput`, `TasksGetInput`, `TasksGetOutput`, `TasksResultInput`, `TasksResultOutput`, `TasksCancelInput`, `TasksCancelOutput`, additive `InvokeInput.task`, additive `InvokeOutput.task`, additive `PendingRequestInfo.task_id`, additive `PendingRequestInfo.task_status`, additive lifecycle task count fields
- **Interfaces consumed**: existing `ToolInfo.execution`, `ServerStatus.server_capabilities`, `InvokeInput`, `InvokeOutput`, `PendingRequestInfo`, `ListPendingOutput`, `CancelOutput`, `RefreshOutput`, and `LifecycleServerOutput`
- **Parallel-safe**: no
- **Tasks**:
  - test: Defer behavioral tests to SL-1, SL-2, SL-3, and SL-4 because those lanes own manager, gateway, server, and lifecycle test files.
  - impl: Define task status and support literals around MCP wire values: `forbidden`, `optional`, `required`, `working`, `input_required`, `completed`, `failed`, and `cancelled`.
  - impl: Add compact public task models that preserve opaque `taskId`, status, status message, timestamps, `ttl`, `pollInterval`, and raw task metadata without introducing durable storage.
  - impl: Extend existing pending and lifecycle output models only with optional fields or defaulted counters so old clients continue to parse responses.
  - impl: Add task tool input/output models for server-filtered listing, get/result/cancel by `server_name` plus `task_id`, and optional force semantics where applicable.
  - verify: `uv run ruff check src/pmcp/types.py`

### SL-1 — Downstream Task Brokering

- **Scope**: Teach `ClientManager` how to detect task capability, request task-augmented tool calls, proxy downstream task methods, and maintain a transient in-memory task index.
- **Owned files**: `src/pmcp/client/manager.py`, `tests/test_client_manager.py`
- **Interfaces provided**: task-support helper, `ClientManager.call_tool(..., task=...)`, `ClientManager.list_tasks(...)`, `ClientManager.get_task(...)`, `ClientManager.get_task_result(...)`, `ClientManager.cancel_task(...)`, task registry helpers, active-task lifecycle helpers
- **Interfaces consumed**: SL-0 task models, Phase 1 `ToolInfo.execution`, Phase 1 `ServerStatus.server_capabilities`, existing `_send_request(...)`, existing `PendingRequest`, existing disconnect/reconnect bookkeeping
- **Parallel-safe**: no
- **Tasks**:
  - test: Add optional-task tool invocation proving PMCP sends `tools/call` with `params.task` only when requested and stores the returned `task.taskId` as a task record instead of final content.
  - test: Add required-task tool invocation proving PMCP automatically sends task metadata when `execution.taskSupport == "required"` and does not route through normal synchronous result assumptions.
  - test: Add unsupported required-task failure proving PMCP returns a structured error when a required-task tool cannot be brokered because server task capability is absent.
  - test: Add proxy tests for downstream `tasks/list`, `tasks/get`, `tasks/result`, and `tasks/cancel` request shapes, including cursor passthrough for list and raw result preservation for result.
  - test: Add task registry tests for `working`, `input_required`, `completed`, `failed`, `cancelled`, and expired/missing task responses.
  - test: Add idempotent cancellation tests proving terminal task cancellation is reported as already terminal and missing downstream task IDs produce not-found output without clearing unrelated records.
  - impl: Add a private task-support resolver that combines `ToolInfo.execution["taskSupport"]` with `ServerStatus.server_capabilities["tasks"]` and treats missing or unknown values as `forbidden`.
  - impl: Extend `call_tool` with a keyword-only task metadata parameter and build `tools/call` params as `{"name": ..., "arguments": ..., "task": ...}` only for supported task paths.
  - impl: Record `McpTaskRecord` values in memory when a result contains `task.taskId`, linked to server, tool, optional local request ID, requestor context if available, timestamps, ttl, poll interval, and raw metadata.
  - impl: Add downstream task proxy methods using `_send_request(...)` for `tasks/list`, `tasks/get`, `tasks/result`, and `tasks/cancel`, updating the local task record from returned task fields where present.
  - impl: Keep task IDs opaque strings and avoid synthesizing local IDs that could be mistaken for downstream IDs.
  - verify: `uv run pytest tests/test_client_manager.py -k "task or tasks or required_task or pending or cancel"`
  - verify: `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py`

### SL-2 — Gateway Task Tools, Pending Surfaces, and Lifecycle Behavior

- **Scope**: Expose task-aware invocation, task proxy tools, additive pending/task visibility, and task-aware lifecycle decisions through `GatewayTools`.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: `gateway.invoke` task option handling, `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, `gateway.tasks_cancel`, additive task fields in `gateway.list_pending`, task-aware cancel behavior, task-aware refresh/disconnect/restart behavior
- **Interfaces consumed**: SL-0 task models and lifecycle count fields, SL-1 manager task methods, active-task registry, task cancellation helpers, and support decisions, existing policy checks, existing output redaction/truncation, existing `gateway.list_pending`, `gateway.cancel`, `gateway.refresh`, `gateway.disconnect_server`, and `gateway.restart_server` contracts
- **Parallel-safe**: no
- **Tasks**:
  - test: Add `gateway.invoke` optional-task tests proving task creation returns `ok=True` with a task object and does not require a final tool result in `result`.
  - test: Add `gateway.invoke` required-task tests proving the gateway requests task augmentation automatically and reports a structured unsupported error when manager capability checks reject it.
  - test: Add `gateway.list_pending` compatibility tests proving old fields remain unchanged and task-linked requests add only optional `task_id` and `task_status`.
  - test: Add `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and `gateway.tasks_cancel` tests for success, not-supported server, missing task, terminal task, and downstream error shapes.
  - test: Add output processing tests proving `gateway.tasks_result` applies the same redaction and output-size options as `gateway.invoke`.
  - test: Add lifecycle tests proving default refresh, disconnect, and restart refuse when either pending requests or active MCP tasks exist.
  - test: Add lifecycle tests proving forced refresh, disconnect, and restart count pending requests separately from active tasks and attempt downstream task cancellation before disconnect.
  - test: Add lifecycle tests proving terminal `completed`, `failed`, and `cancelled` tasks do not block lifecycle operations.
  - test: Add lifecycle tests proving task-cancel failure is surfaced in lifecycle errors without claiming the task was cancelled.
  - impl: Extend `get_gateway_tool_definitions()` with task tool definitions and conservative descriptions that distinguish PMCP request IDs from MCP task IDs.
  - impl: Extend `invoke(...)` to accept task metadata/options from SL-0, pass them to `ClientManager.call_tool`, and return a task-aware output when the downstream result is a task creation result.
  - impl: Add handler methods for task list/get/result/cancel that validate server and task IDs, delegate to SL-1 manager methods, and return structured outputs.
  - impl: Add task linkage to pending output construction without renaming or reformatting existing `request_id` values.
  - impl: Route `gateway.cancel` to local request cancellation for `server::local_id` values and document that MCP task cancellation uses `gateway.tasks_cancel`.
  - impl: Update `refresh`, `disconnect_server`, and `restart_server` to refuse active states `working` and `input_required` by default, treat `completed`, `failed`, and `cancelled` as terminal, and attempt downstream task cancellation on forced lifecycle operations.
  - impl: Return separate counts for pending requests seen/cancelled and MCP tasks seen/cancelled/refused/remaining where SL-0 output fields allow it.
  - verify: `uv run pytest tests/test_tools.py -k "task or tasks or list_pending or cancel or invoke or refresh or disconnect or restart"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-3 — Server Routing for Task Tools

- **Scope**: Wire the new gateway task tools through the MCP server dispatch layer and keep unknown-tool behavior unchanged.
- **Owned files**: `src/pmcp/server.py`, `tests/test_server.py`
- **Interfaces provided**: server dispatch branches for `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and `gateway.tasks_cancel`
- **Interfaces consumed**: SL-2 `GatewayTools` task methods and tool definitions, existing `TextContent` JSON serialization behavior, existing error serialization path
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add server call routing tests proving each new `gateway.tasks_*` tool delegates to the matching `GatewayTools` method and returns JSON text.
  - test: Add a regression test proving an unknown task tool name still follows the existing unknown-tool error path.
  - impl: Add explicit `elif` branches in `GatewayServer._setup_handlers()` for the four new gateway task tools.
  - impl: Preserve existing model-to-dict conversion and JSON text wrapping for all task outputs.
  - verify: `uv run pytest tests/test_server.py -k "task or tasks or call_tool"`
  - verify: `uv run ruff check src/pmcp/server.py tests/test_server.py`

### SL-4 — End-to-End, Docs, and Roadmap Closeout

- **Scope**: Add cross-surface coverage, document task behavior and limitations, and close the Phase 2 checklist after implementation verification.
- **Owned files**: `tests/test_phase4_e2e.py`, `README.md`, `CHANGELOG.md`, `specs/phase-plans-v3.md`, `plans/phase-plan-v3-tasks.md`
- **Interfaces provided**: end-to-end task smoke coverage, user-facing task support notes, completed TASKS acceptance checklist
- **Interfaces consumed**: SL-1 downstream task brokering, SL-2 gateway task tools, pending outputs, and lifecycle task decisions, SL-3 server routing, verification results from all lanes
- **Parallel-safe**: no
- **Tasks**:
  - test: Add an end-to-end gateway smoke that invokes a fake task-supported tool, observes the task in task/list or pending surfaces, fetches the result, and verifies terminal status handling.
  - test: Add an end-to-end lifecycle smoke proving default refresh refuses active task work and forced refresh attempts task cancellation before reconnect/disconnect.
  - impl: Add README documentation for task support, clearly distinguishing PMCP pending request IDs from downstream MCP task IDs.
  - impl: Document limitations: task storage is transient, task visibility is bound to enforceable server/requestor context, and unauthenticated local transports cannot provide cross-user authorization isolation.
  - impl: Add a CHANGELOG entry for task-aware execution and task proxy tools if this branch is release-bound.
  - impl: Mark Phase 2 exit criteria complete in `specs/phase-plans-v3.md` only after implementation and verification complete.
  - impl: Mark this plan's interface gates and acceptance criteria complete and record execution deviations.
  - verify: `uv run pytest tests/test_phase4_e2e.py -k "task or tasks"`
  - verify: Manually review markdown formatting in `README.md`, `CHANGELOG.md`, `specs/phase-plans-v3.md`, and `plans/phase-plan-v3-tasks.md`.

## Verification

Lane-specific verification:

- `uv run ruff check src/pmcp/types.py`
- `uv run pytest tests/test_client_manager.py -k "task or tasks or required_task or pending or cancel"`
- `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py`
- `uv run pytest tests/test_tools.py -k "task or tasks or list_pending or cancel or invoke or refresh or disconnect or restart"`
- `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`
- `uv run pytest tests/test_server.py -k "task or tasks or call_tool"`
- `uv run ruff check src/pmcp/server.py tests/test_server.py`
- `uv run pytest tests/test_phase4_e2e.py -k "task or tasks"`

Whole-phase regression:

- `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_server.py tests/test_phase4_e2e.py -q`
- `uv run pytest tests/test_http_transport.py tests/test_transport_http.py tests/test_cli.py -q` if task fields touch shared status, transport, or CLI serialization.
- `uv run ruff check src/ tests/`
- `uv run ruff format --check src/ tests/`
- `uv run mypy src/pmcp --exclude baml_client`
- `uv run pytest -q` before release handoff if time permits.

## Acceptance Criteria

- [x] PMCP detects server and tool task support from negotiated capabilities and `execution.taskSupport`, defaulting absent or unknown task support to `forbidden` for execution decisions.
- [x] Optional task-supported tools can be invoked with task augmentation and return task creation metadata without being treated as completed tool results.
- [x] Required task-supported tools are invoked with task augmentation automatically, and unsupported required-task tools return a structured gateway error.
- [x] MCP task IDs remain opaque and distinct from PMCP pending request IDs across invocation, listing, cancellation, lifecycle, and docs.
- [x] `gateway.list_pending` remains backward compatible and adds task linkage only through optional fields.
- [x] `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and `gateway.tasks_cancel` proxy downstream task methods where supported and return structured not-supported/not-found/terminal-state outputs.
- [x] `gateway.cancel` continues to cancel PMCP pending requests, while MCP task cancellation is performed through `gateway.tasks_cancel`.
- [x] Default refresh, disconnect, and restart refuse active pending requests or active MCP tasks; forced lifecycle operations cancel local requests and attempt downstream task cancellation before disconnect.
- [x] Tests cover `working`, `input_required`, `completed`, `failed`, `cancelled`, expired/missing task IDs, idempotent cancellation, optional-task tools, required-task tools, and lifecycle refusal/force behavior.
- [x] README or release notes document transient task storage, requestor-context limitations, and the distinction between PMCP request IDs and MCP task IDs.

## Execution Notes

- Implemented in one sequential lane pass because the live worktree already contained Phase 1 metadata changes and the task lanes had tight dependencies.
- `scripts/preflight.sh` was not present in this repository, so verification used the repository's `uv run` commands directly.
- Full verification completed with `1622 passed, 12 skipped, 21 deselected`; the remaining warning is the existing unknown `pytest.mark.timeout` marker in `tests/test_manifest_provision.py`.
