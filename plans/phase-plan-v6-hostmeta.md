# HOSTMETA: Invocation Metadata and Task Brokering

## Context

Phase 3 of `specs/phase-plans-v6.md` hardens the existing `gateway.invoke`
and `gateway.tasks_*` paths for tenant code-mode usage. PMCP must prove it can
forward bounded trace, requestor, and task metadata to a task-capable tenant
server, record returned downstream task IDs, expose task lifecycle state, and
continue applying host-side truncation, redaction, and audit visibility.

The prerequisite host contract exists in the current staged baseline:
`specs/tenant-code-mode-host-contract.md` defines PMCP as the broker and the
tenant code-mode server as the sandbox execution authority. HOSTMETA consumes
that contract and must not add script execution, durable task/artifact storage,
streaming logs, a new gateway tool, tenant authorization, or live hosted
infrastructure.

Current repo surfaces to reuse:

- `src/pmcp/types.py` already defines `TraceContextInfo`, `TaskMetadataInput`,
  `McpTaskInfo`, `McpTaskRecord`, `InvokeInput`, `InvokeOutput`,
  `TasksList*`, `TasksGet*`, `TasksResult*`, and `TasksCancel*`.
- `src/pmcp/client/manager.py` already gates task dispatch on server task
  capability plus tool `execution.taskSupport`, builds downstream `tools/call`
  params, records transient downstream task IDs, and proxies `tasks/list`,
  `tasks/get`, `tasks/result`, and `tasks/cancel`.
- `src/pmcp/tools/handlers.py` already extracts trace context from `_meta`,
  `meta`, `trace_context`, or `traceContext`, applies gateway policy and output
  processing, returns task-aware `gateway.invoke` output, exposes
  `gateway.tasks_*`, and writes bounded audit events.
- `tests/test_client_manager.py` and `tests/test_tools.py` already contain
  task, trace, task-result redaction, and audit coverage that HOSTMETA should
  extend into tenant-shaped cases rather than replace.

Baseline risk: the worktree already has staged v6 roadmap/HOSTCONTRACT/HOSTREG
artifacts and unstaged HOSTREG-era edits in shared files including `README.md`,
`src/pmcp/tools/handlers.py`, `tests/test_manifest.py`, and
`tests/test_tools.py`. HOSTMETA execution must preserve those edits, inspect the
current diff before changing shared files, and avoid rewriting HOSTREG discovery
work.

## Interface Freeze Gates

- [x] IF-0-HOSTMETA-1 - `GatewayTools.invoke(...)` accepts trace context from
  `_meta`, `meta`, `trace_context`, or `traceContext`, drops unsafe trace
  values, and passes only `traceparent`, `tracestate`, and `baggage` through
  `TraceContextInfo` to `ClientManager.call_tool(...)`.
- [x] IF-0-HOSTMETA-2 - `ClientManager.call_tool(...)` sends downstream
  `tools/call` params with `task.metadata`, `task.ttl`,
  `task.pollInterval`, `task.requestorContext`, and `_meta` trace keys only
  when the selected tool and server are task-capable under the existing
  `execution.taskSupport` and server `tasks` capability rules.
- [x] IF-0-HOSTMETA-3 - Returned tenant task payloads with `taskId` or
  `task_id` are recorded as transient `McpTaskRecord` entries keyed by
  downstream server name and downstream task ID, retaining non-secret
  `server_name`, `tool_id`, `requestor_context`, status, polling hints, and raw
  task metadata.
- [x] IF-0-HOSTMETA-4 - `gateway.tasks_list`, `gateway.tasks_get`,
  `gateway.tasks_result`, and `gateway.tasks_cancel` use downstream MCP task
  IDs, update the transient registry from downstream task responses, and do not
  confuse downstream task IDs with PMCP pending-request IDs.
- [x] IF-0-HOSTMETA-5 - `gateway.invoke` and `gateway.tasks_result` continue to
  apply host-side truncation and optional redaction to sandbox-shaped logs,
  diagnostics, and result payloads while returning task-start responses without
  embedding large task results.
- [x] IF-0-HOSTMETA-6 - `gateway.health` audit events include enough non-secret
  task and tool identity for tenant debugging: method/action, outcome, server,
  tool, downstream task ID when known, trace-present boolean, protocol version
  when available, auth state, latency, and sanitized errors.

## Lane Index & Dependencies

- SL-0 - HOSTMETA baseline and contract inventory; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 - Manager wire metadata and task registry contract; Depends on: SL-0; Blocks: SL-2, SL-3, SL-5; Parallel-safe: yes
- SL-2 - Gateway invoke, task surfaces, and audit behavior; Depends on: SL-0, SL-1; Blocks: SL-3, SL-4, SL-5; Parallel-safe: no
- SL-3 - Tenant-shaped deterministic task tests; Depends on: SL-0, SL-1, SL-2; Blocks: SL-4, SL-5; Parallel-safe: no
- SL-4 - README task lifecycle documentation; Depends on: SL-0, SL-2, SL-3; Blocks: SL-5; Parallel-safe: yes
- SL-5 - Phase verification and closeout; Depends on: SL-0, SL-1, SL-2, SL-3, SL-4; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - HOSTMETA Baseline and Contract Inventory

- **Scope**: Freeze the execution baseline by mapping HOSTMETA exit criteria to
  the staged host contract, current task/trace code, current tests, and
  existing dirty shared-file edits.
- **Owned files**: none; read-only survey of `specs/phase-plans-v6.md`,
  `specs/tenant-code-mode-host-contract.md`,
  `plans/phase-plan-v6-hostcontract.md`, `plans/phase-plan-v6-hostreg.md`,
  `src/pmcp/types.py`, `src/pmcp/client/manager.py`,
  `src/pmcp/tools/handlers.py`, `tests/test_client_manager.py`,
  `tests/test_tools.py`, `tests/test_integration.py`, `README.md`
- **Interfaces provided**: HOSTMETA source inventory, shared-file conflict map,
  tenant task lifecycle checklist, no-new-runtime boundary
- **Interfaces consumed**: IF-0-HOSTCONTRACT-1, IF-0-HOSTMETA-3 from
  `specs/phase-plans-v6.md`, `specs/tenant-code-mode-host-contract.md`,
  current `TraceContextInfo`, `TaskMetadataInput`, `McpTaskRecord`,
  `InvokeInput`, `InvokeOutput`, `Tasks*` models, current HOSTREG edits
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm `git status --short` and record all staged/unstaged files
    that overlap with HOSTMETA, especially `README.md`,
    `src/pmcp/tools/handlers.py`, and `tests/test_tools.py`.
  - test: Confirm the host contract still says PMCP brokers execution and the
    tenant server owns sandbox execution, authorization, artifacts, and logs.
  - test: Map every HOSTMETA roadmap exit criterion to an existing source or
    test surface before adding code.
  - test: Identify whether existing transient task records already retain all
    non-secret tenant visibility fields; if not, limit any model changes to
    additive `McpTaskRecord` or `McpTaskInfo` fields.
  - impl: Record the conflict map in the executor closeout before editing
    shared files; do not revert or restage unrelated HOSTREG changes.
  - verify: `git status --short`
  - verify: `rg -n "PMCP is the host-side broker|requestor_context|taskSupport|traceparent|gateway.tasks_result|audit_events" specs/tenant-code-mode-host-contract.md src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/tools/handlers.py tests/test_client_manager.py tests/test_tools.py README.md`

### SL-1 - Manager Wire Metadata and Task Registry Contract

- **Scope**: Lock `ClientManager` as the single downstream wire boundary for
  tenant task metadata, trace metadata, task capability checks, and transient
  downstream task registration.
- **Owned files**: `src/pmcp/client/manager.py`, `src/pmcp/types.py`,
  `tests/test_client_manager.py`
- **Interfaces provided**: downstream `tools/call` task/trace param contract,
  downstream task payload parsing contract, transient task registry contract,
  task proxy method contract
- **Interfaces consumed**: SL-0 inventory, `TraceContextInfo`,
  `TaskMetadataInput`, `McpTaskInfo`, `McpTaskRecord`,
  `ToolInfo.execution["taskSupport"]`, `ServerStatus.server_capabilities`,
  `_trace_context_payload(...)`, `_task_wire_metadata(...)`,
  `_extract_task_payload(...)`, `_task_info_from_payload(...)`,
  `_record_task(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Extend `tests/test_client_manager.py` with a tenant-shaped
    task-capable tool and server named `tenant-code-mode` that proves
    `call_tool("tenant-code-mode::run_script", ..., task=..., trace_context=...)`
    sends `metadata`, `ttl`, `pollInterval`, `requestorContext`, and `_meta`
    trace keys in the downstream `tools/call` params.
  - test: Assert unsafe or unsupported fields are not added to downstream
    params by `ClientManager`; PMCP must forward only the bounded contract
    fields and must not synthesize tenant auth or identity.
  - test: Assert returned tenant task payloads with both `taskId` and
    `task_id` forms are recorded under the downstream server/task ID, with
    `tool_id`, `requestor_context`, status, `ttl`, `poll_interval`, and raw
    payload preserved.
  - test: Assert task-required tools fail before dispatch when the server does
    not advertise task support, preserving current pre-dispatch behavior.
  - test: Extend task proxy coverage so `tasks/list`, `tasks/get`,
    `tasks/result`, and `tasks/cancel` update the same transient registry and
    send downstream `taskId` params, never PMCP request IDs.
  - impl: Prefer no source changes if existing `ClientManager` behavior already
    passes the tenant-shaped tests.
  - impl: If a gap exists, update only the manager/type code needed for
    additive metadata retention or bounded param forwarding.
  - verify: `uv run pytest tests/test_client_manager.py -k "tenant_code_mode or call_tool_optional_task_records_downstream_task or preserves_trace_context or task_proxy_methods_update_registry or required_task_without_server_capability"`
  - verify: `git diff --check -- src/pmcp/client/manager.py src/pmcp/types.py tests/test_client_manager.py`

### SL-2 - Gateway Invoke, Task Surfaces, and Audit Behavior

- **Scope**: Prove `GatewayTools` preserves the tenant metadata/task contract
  from public gateway input through invoke output, task tools, output
  processing, and audit events.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: gateway invoke tenant metadata behavior, task tools
  public behavior, redacted/truncated sandbox-result behavior, task-aware audit
  behavior
- **Interfaces consumed**: SL-1 manager contract, `GatewayTools.invoke(...)`,
  `_extract_trace_context(...)`, `_safe_trace_value(...)`,
  `_audit(...)`, `GatewayTools.tasks_list(...)`,
  `GatewayTools.tasks_get(...)`, `GatewayTools.tasks_result(...)`,
  `GatewayTools.tasks_cancel(...)`, `PolicyManager.process_output(...)`,
  `MockClientManager`
- **Parallel-safe**: no, because this lane owns files with current unstaged
  HOSTREG edits
- **Tasks**:
  - test: Before editing, inspect current unstaged diffs for
    `src/pmcp/tools/handlers.py` and `tests/test_tools.py` and preserve HOSTREG
    discovery changes.
  - test: Extend `MockClientManager` narrowly to record the last task metadata
    and trace context received by `call_tool(...)` without changing its public
    behavior for unrelated tests.
  - test: Add a tenant-shaped `gateway.invoke` regression for
    `tenant-code-mode::run_script` proving `_meta` or `trace_context` trace
    input is sanitized, `task.metadata`, `ttl`, `poll_interval`, and
    `requestor_context` reach the manager, and the invoke response returns a
    task record rather than raw sandbox output.
  - test: Add a trace sanitization regression proving auth-like or oversized
    trace values are dropped and do not reach the manager or audit error text.
  - test: Extend `gateway.tasks_*` coverage for tenant downstream task IDs,
    including list/get/result/cancel, terminal idempotence, and not-found
    errors. Assertions must name downstream task IDs and must not use
    `server::local_id` PMCP pending-request IDs.
  - test: Add or extend sandbox-shaped result processing coverage so
    `gateway.tasks_result(..., options={redact_secrets: true,
    max_output_chars: ...})` redacts secrets, truncates large logs, and keeps
    summaries/result payloads bounded.
  - test: Assert audit events for invoke and task tools include method/action,
    outcome, server/tool/task ID where available, trace-present boolean, and
    sanitized errors without raw task metadata or secrets.
  - impl: Prefer tests-only changes if existing handler behavior passes.
  - impl: If handler changes are needed, keep them additive and inside existing
    invoke/task/audit helpers; do not add a new gateway tool or new execution
    subsystem.
  - verify: `uv run pytest tests/test_tools.py -k "tenant_code_mode or propagates_trace_context or optional_task or tasks_result or conformance_task_gateway_route_and_audit"`
  - verify: `git diff --check -- src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-3 - Tenant-Shaped Deterministic Task Tests

- **Scope**: Add deterministic tenant-code-mode coverage that exercises the
  whole host-side task lifecycle without live hosted infrastructure or the
  companion repo.
- **Owned files**: `tests/test_integration.py`
- **Interfaces provided**: deterministic mock tenant lifecycle test, no-live
  infrastructure proof, hosted-code-mode lifecycle coverage through existing
  gateway surfaces
- **Interfaces consumed**: SL-1 manager contract, SL-2 gateway task behavior,
  tenant server contract fixture requirements from
  `specs/tenant-code-mode-host-contract.md`, `GatewayTools`,
  `ClientManager`, `PolicyManager`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add a non-`live` deterministic test in `tests/test_integration.py`
    using in-process fakes or mocked `ClientManager._send_request(...)` to
    model a task-capable `tenant-code-mode` server. Do not require installed MCP
    servers, cloud credentials, a real remote URL, or the companion repo.
  - test: The fixture should expose a task-capable submission tool, return a
    downstream tenant task ID, then drive `tasks/list`, `tasks/get`,
    `tasks/result`, and `tasks/cancel` through existing gateway surfaces.
  - test: Assert the lifecycle includes at least `working`, `input_required`,
    `completed`, and `cancelled` states where the existing API can model them
    deterministically.
  - test: Assert result retrieval returns bounded sandbox-shaped diagnostics
    and that redaction/truncation behavior is covered by SL-2 rather than
    duplicated deeply here.
  - impl: Keep the mock fixture local to the test file unless reuse becomes
    necessary for HOSTSOAK; avoid adding a new helper module for this phase
    unless the test becomes unwieldy.
  - impl: Do not mark the test `live`; it must run in normal local/CI targeted
    pytest without external servers.
  - verify: `uv run pytest tests/test_integration.py -k "tenant_code_mode or task"`
  - verify: `git diff --check -- tests/test_integration.py`

### SL-4 - README Task Lifecycle Documentation

- **Scope**: Document the tenant task lifecycle through existing PMCP gateway
  surfaces after tests prove the behavior.
- **Owned files**: `README.md`
- **Interfaces provided**: tenant code-mode task lifecycle docs, trace/task
  metadata example, task ID boundary warning, result/redaction guidance
- **Interfaces consumed**: SL-2 and SL-3 tested behavior, HOSTCONTRACT metadata
  and task sections, existing README task support, gateway tools, remote
  downstream server, and tenant-code-mode registration sections
- **Parallel-safe**: yes, after SL-2 and SL-3 define the tested behavior
- **Tasks**:
  - test: Before editing, inspect current staged and unstaged README diffs and
    preserve HOSTCONTRACT/HOSTREG docs already present.
  - test: Confirm docs distinguish downstream MCP task IDs from PMCP request
    IDs and route task operations through `gateway.tasks_list`,
    `gateway.tasks_get`, `gateway.tasks_result`, and
    `gateway.tasks_cancel`.
  - test: Confirm examples use non-secret trace/task metadata and never show
    bearer tokens, tenant auth, API keys, or durable identity in `_meta`,
    baggage, task metadata, or `requestor_context`.
  - impl: Add a concise tenant-code-mode invocation/task lifecycle paragraph or
    example near the existing task support or tenant registration sections.
  - impl: Keep policy/auth/operator guardrail expansion deferred to
    HOSTPOLICY; this lane should not rewrite SECURITY or policy docs unless
    execution discovers a direct HOSTMETA contradiction.
  - verify: `rg -n "tenant-code-mode|gateway\\.invoke|gateway\\.tasks_list|gateway\\.tasks_get|gateway\\.tasks_result|gateway\\.tasks_cancel|traceparent|requestor_context|PMCP request ID|downstream MCP task ID" README.md`
  - verify: `git diff --check -- README.md`

### SL-5 - Phase Verification and Closeout

- **Scope**: Reduce the manager, gateway, deterministic tenant tests, and docs
  outputs into the final HOSTMETA handoff.
- **Owned files**: `plans/phase-plan-v6-hostmeta.md`
- **Interfaces provided**: completed HOSTMETA checklist, verification summary,
  shared-file preservation notes, HOSTSOAK/HOSTPOLICY handoff notes
- **Interfaces consumed**: SL-0 baseline inventory, SL-1 manager results,
  SL-2 gateway/task/audit results, SL-3 deterministic tenant lifecycle test,
  SL-4 README docs, HOSTMETA exit criteria from `specs/phase-plans-v6.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm every HOSTMETA roadmap exit criterion maps to passing tests,
    docs, or an explicit no-op source decision.
  - test: Confirm no lane added PMCP-owned script execution, durable task
    persistence, artifact storage, streaming log transport, tenant auth, a new
    gateway tool, or live hosted infrastructure.
  - test: Confirm final artifact writes depend on all producer lanes and do not
    race with source/test/doc changes.
  - impl: Record any unavoidable shared-file merge notes caused by in-flight
    HOSTREG edits.
  - impl: Record that HOSTPOLICY consumes policy/auth/operator-risk
    documentation gaps, and HOSTSOAK consumes deterministic tenant fixture
    coverage for release-gate expansion.
  - verify: `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_integration.py -k "tenant_code_mode or task or trace_context or tasks_result or conformance_task_gateway_route_and_audit"`
  - verify: `uv run ruff check src/pmcp/client src/pmcp/tools tests/test_client_manager.py tests/test_tools.py tests/test_integration.py`
  - verify: `uv run ruff format --check src/pmcp/client src/pmcp/tools tests/test_client_manager.py tests/test_tools.py tests/test_integration.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `git diff --check`

## Verification

Lane-specific verification:

- `git status --short`
- `rg -n "PMCP is the host-side broker|requestor_context|taskSupport|traceparent|gateway.tasks_result|audit_events" specs/tenant-code-mode-host-contract.md src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/tools/handlers.py tests/test_client_manager.py tests/test_tools.py README.md`
- `uv run pytest tests/test_client_manager.py -k "tenant_code_mode or call_tool_optional_task_records_downstream_task or preserves_trace_context or task_proxy_methods_update_registry or required_task_without_server_capability"`
- `uv run pytest tests/test_tools.py -k "tenant_code_mode or propagates_trace_context or optional_task or tasks_result or conformance_task_gateway_route_and_audit"`
- `uv run pytest tests/test_integration.py -k "tenant_code_mode or task"`
- `rg -n "tenant-code-mode|gateway\\.invoke|gateway\\.tasks_list|gateway\\.tasks_get|gateway\\.tasks_result|gateway\\.tasks_cancel|traceparent|requestor_context|PMCP request ID|downstream MCP task ID" README.md`
- `git diff --check -- src/pmcp/client/manager.py src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_client_manager.py tests/test_tools.py tests/test_integration.py README.md`

Whole-phase regression:

- `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_integration.py -k "tenant_code_mode or task or trace_context or tasks_result or conformance_task_gateway_route_and_audit"`
- `uv run ruff check src/pmcp/client src/pmcp/tools tests/test_client_manager.py tests/test_tools.py tests/test_integration.py`
- `uv run ruff format --check src/pmcp/client src/pmcp/tools tests/test_client_manager.py tests/test_tools.py tests/test_integration.py`
- `uv run mypy src/pmcp --exclude baml_client`
- `git diff --check`

No live tenant service, cloud credential, companion repo checkout, full PMCP
release regression, new streaming log transport, durable PMCP task store, or
new gateway code-execution tool is required for HOSTMETA.

## Acceptance Criteria

- [x] Mock-server or deterministic tenant-shaped tests prove
  `gateway.invoke(..., task=...)` forwards task metadata and records returned
  tenant run task IDs.
- [x] Trace metadata is accepted through `_meta` or `trace_context`, sanitized
  by existing safeguards, and forwarded to the downstream tenant server.
- [x] `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and
  `gateway.tasks_cancel` expose tenant run state using downstream task IDs
  without confusing them with PMCP request IDs.
- [x] Result processing continues to apply truncation and optional redaction to
  sandbox logs, diagnostics, and result payloads.
- [x] Health/audit events include enough non-secret task and tool identity to
  debug tenant code-mode failures from PMCP.
- [x] HOSTMETA execution preserves staged HOSTCONTRACT/HOSTREG artifacts and
  current shared-file edits unless the user explicitly asks for cleanup.

## Execution Closeout

- SL-0 inventory confirmed HOSTMETA could build on existing PMCP task, trace,
  output-processing, and audit surfaces. The dirty-tree overlap was limited to
  existing HOSTREG edits in `README.md`, `src/pmcp/tools/handlers.py`, and
  `tests/test_tools.py`; those edits were preserved.
- SL-1 added tenant-shaped `ClientManager` coverage proving bounded task
  metadata, `requestorContext`, `pollInterval`, and `_meta` trace keys are
  forwarded only through the task-capable downstream call path and that returned
  `task_id` payloads are retained in transient records with non-secret
  visibility fields.
- SL-2 extended gateway tests for tenant-code-mode invoke/task behavior, trace
  sanitization, downstream task ID usage, task-result redaction, and audit
  identity fields without adding a new gateway tool or execution subsystem.
- SL-3 added a deterministic non-live tenant lifecycle integration test covering
  `working`, `input_required`, `completed`, and `cancelled` states through
  existing gateway task surfaces.
- SL-4 documented the tenant task lifecycle in README next to the registration
  guidance, including the downstream MCP task ID versus PMCP request ID
  boundary and non-secret metadata guidance.
- HOSTPOLICY still owns broader policy/auth/operator-risk guardrails. HOSTSOAK
  consumes this deterministic tenant fixture pattern for the release-gate
  matrix.
