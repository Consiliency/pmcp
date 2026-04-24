# HOSTCONTRACT: Tenant Host Contract Freeze

## Context

Phase 1 of `specs/phase-plans-v6.md` freezes the PMCP-facing contract for a
separate tenant code-mode MCP server before manifest registration, invocation
hardening, policy guardrails, or release soak work depends on it.

PMCP already has the host-side surfaces this contract should describe rather
than redesign:

- `src/pmcp/types.py` defines `TraceContextInfo`, `TaskMetadataInput`,
  `McpTaskInfo`, `McpTaskRecord`, `InvokeInput`, `InvokeOutput`,
  `ResourceInfo`, and modern tool metadata such as `execution.taskSupport`.
- `src/pmcp/client/manager.py` gates task dispatch on server task capability and
  tool `execution.taskSupport`, forwards task metadata and trace context to
  downstream `tools/call`, tracks transient downstream task IDs, and proxies
  `tasks/list`, `tasks/get`, `tasks/result`, and `tasks/cancel`.
- `src/pmcp/tools/handlers.py` applies gateway policy, output truncation,
  optional secret redaction, audit events, auth-state reporting, and task result
  handling around `gateway.invoke` and `gateway.tasks_*`.
- `README.md` and `SECURITY.md` already document the current local-first trust
  model, streamable HTTP, task support, trace metadata, redaction, transient
  task records, and the fact that PMCP is not a multi-tenant authorization
  layer.

The new contract file does not exist at planning time:
`specs/tenant-code-mode-host-contract.md`. The v6 roadmap is staged as a new
file at planning time (`A  specs/phase-plans-v6.md`), so it is protected in the
index but still not committed.

This phase is contract-only. It must not add a new gateway tool, edit the main
manifest, execute untrusted scripts, implement sandbox runtime behavior, or
turn PMCP into a code runner.

## Interface Freeze Gates

- [x] IF-0-HOSTCONTRACT-1 - `specs/tenant-code-mode-host-contract.md` defines
  the tenant code-mode server role, PMCP host role, non-goals, terminology, and
  expected tool families for script submission, run inspection, result
  retrieval, cancellation, and artifact/resource access without requiring exact
  final tool names.
- [x] IF-0-HOSTCONTRACT-2 - The contract freezes task behavior around advertised
  server task capability, tool `execution.taskSupport`, `gateway.invoke(...,
  task=...)`, downstream task IDs, accepted status values, `pollInterval`,
  `ttl`, cancellation semantics, task-result retrieval, and the distinction
  between PMCP request IDs and downstream MCP task IDs.
- [x] IF-0-HOSTCONTRACT-3 - The contract freezes metadata forwarding
  expectations for `traceparent`, `tracestate`, `baggage`, task `metadata`, and
  task `requestor_context`, including the safe boundary that trace baggage and
  requestor metadata are not identity, auth, or secret transport.
- [x] IF-0-HOSTCONTRACT-4 - The contract freezes safe output expectations:
  summaries before raw logs, gateway truncation and optional redaction, bounded
  diagnostics, artifact/resource references instead of large payloads, no
  secret-bearing telemetry, and no durable sandbox-log storage in PMCP.
- [x] IF-0-HOSTCONTRACT-5 - The contract names compatibility assumptions for
  streamable HTTP, optional local stdio development, lazy downstream
  configuration, env-placeholder headers, task-capable servers, and
  vendor-neutral companion-runtime choices.
- [x] IF-0-HOSTCONTRACT-6 - The contract includes future mock-server fixture
  design notes that later phases can turn into deterministic tests without
  depending on live infrastructure, cloud credentials, or the companion repo's
  final package name.

## Lane Index & Dependencies

- SL-0 - Current host-surface inventory; Depends on: (none); Blocks: SL-1, SL-2; Parallel-safe: yes
- SL-1 - Tenant host contract spec; Depends on: SL-0; Blocks: SL-2, SL-3; Parallel-safe: no
- SL-2 - Public docs alignment decision; Depends on: SL-0, SL-1; Blocks: SL-3; Parallel-safe: yes
- SL-3 - Phase verification and closeout; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Current Host-Surface Inventory

- **Scope**: Inventory the existing PMCP task, trace, remote transport,
  resource, redaction, auth, and policy surfaces that the contract must consume.
- **Owned files**: none; read-only survey of `src/pmcp/types.py`,
  `src/pmcp/client/manager.py`, `src/pmcp/tools/handlers.py`, `README.md`,
  `SECURITY.md`, `tests/test_tools.py`, `tests/test_transport_http.py`
- **Interfaces provided**: source-backed contract vocabulary for task support,
  trace context, requestor context, streamable HTTP, resources/artifacts,
  output processing, audit events, auth states, and policy boundaries
- **Interfaces consumed**: Phase 1 exit criteria from
  `specs/phase-plans-v6.md`, current Pydantic models in `src/pmcp/types.py`,
  current downstream manager behavior in `src/pmcp/client/manager.py`, current
  gateway handler behavior in `src/pmcp/tools/handlers.py`, existing task and
  trace tests
- **Parallel-safe**: yes
- **Tasks**:
  - test: Map every Phase 1 exit criterion to at least one existing source
    symbol, README/SECURITY section, or explicit companion-server contract
    decision before writing the spec.
  - test: Confirm that existing task behavior is documented from
    `TaskMetadataInput`, `McpTaskInfo`, `McpTaskRecord`,
    `_tool_task_support(...)`, `_server_supports_tasks(...)`,
    `_task_wire_metadata(...)`, and the manager `tasks/*` proxy methods.
  - test: Confirm that existing trace behavior is documented from
    `TraceContextInfo`, `InvokeInput.trace_context`, `_meta` handling, and the
    HTTP trace-header compatibility tests.
  - test: Confirm that output safety language uses existing policy/redaction
    and truncation behavior rather than promising durable sandbox log storage or
    new artifact persistence in PMCP.
  - impl: Produce a short inventory note inside the contract draft or closeout
    section rather than editing source code.
  - verify: `rg -n "class TraceContextInfo|class TaskMetadataInput|class McpTaskInfo|class McpTaskRecord|class InvokeInput|class ResourceInfo|TaskSupportMode" src/pmcp/types.py`
  - verify: `rg -n "_tool_task_support|_server_supports_tasks|_task_wire_metadata|list_tasks|get_task_result|cancel_task|read_resource|streamable" src/pmcp/client/manager.py`
  - verify: `rg -n "taskSupport|traceparent|requestor_context|tasks_result|redaction|audit_events" tests/test_tools.py tests/test_transport_http.py README.md SECURITY.md`

### SL-1 - Tenant Host Contract Spec

- **Scope**: Write the PMCP host contract as a vendor-neutral spec that later
  registration, metadata, policy, and soak phases can consume directly.
- **Owned files**: `specs/tenant-code-mode-host-contract.md`
- **Interfaces provided**: tenant host contract spec, tenant server tool-family
  contract, task lifecycle contract, metadata forwarding contract, safe output
  contract, artifact/resource contract, compatibility assumptions, mock fixture
  design notes
- **Interfaces consumed**: SL-0 host-surface inventory, Phase 1 exit criteria,
  existing `gateway.invoke`, `gateway.describe`, `gateway.tasks_list`,
  `gateway.tasks_get`, `gateway.tasks_result`, `gateway.tasks_cancel`,
  `gateway.read_resource`, streamable HTTP and stdio configuration behavior,
  README/SECURITY trust model language
- **Parallel-safe**: no
- **Tasks**:
  - test: Draft a contract checklist in the spec and ensure it covers expected
    tool families for run submission, run lookup, result retrieval,
    cancellation, and artifact/resource discovery without requiring exact names
    such as `run_script`, `get_run`, `get_result`, or `cancel_run`.
  - test: Ensure the task section explicitly requires a task-capable downstream
    server to advertise task support and task-capable tools to advertise
    `execution.taskSupport` as `optional` or `required`.
  - test: Ensure task status vocabulary includes at least PMCP's accepted
    statuses: `working`, `input_required`, `completed`, `failed`, and
    `cancelled`, while allowing raw task metadata to preserve future-compatible
    downstream fields.
  - test: Ensure the metadata section defines `traceparent`, `tracestate`,
    `baggage`, task `metadata`, `ttl`, `pollInterval`, and
    `requestorContext`/`requestor_context` handling, plus the rule that these
    fields must not carry secrets or tenant auth tokens.
  - test: Ensure the output section requires summary-first responses, bounded
    raw output, redacted logs, artifact references/resources for large payloads,
    and no secret-bearing telemetry.
  - test: Ensure compatibility language covers streamable HTTP as the primary
    hosted path, stdio as a local development path, lazy downstream
    configuration, env-placeholder headers, and no eager startup unless
    explicitly configured later.
  - impl: Add the contract file with sections for purpose, roles, non-goals,
    terminology, tenant tool families, schemas and metadata, task lifecycle,
    result and artifact handling, safety/output rules, compatibility, and
    future mock-server fixture requirements.
  - impl: Keep the spec descriptive and contract-first; do not add manifest
    entries, handler branches, source models, runtime code, or tests in this
    phase.
  - verify: `rg -n "run_script|get_run|get_result|cancel_run|execution\\.taskSupport|taskSupport|traceparent|tracestate|baggage|requestor|pollInterval|artifact|resource|streamable HTTP|stdio|lazy" specs/tenant-code-mode-host-contract.md`
  - verify: `git diff --check -- specs/tenant-code-mode-host-contract.md`

### SL-2 - Public Docs Alignment Decision

- **Scope**: Decide whether README and SECURITY need a narrow pointer to the
  new host contract, while deferring policy, auth, and operator guardrail
  expansion to HOSTPOLICY.
- **Owned files**: `README.md`, `SECURITY.md`
- **Interfaces provided**: optional host-contract cross-reference, explicit
  no-op decision if existing README/SECURITY language is sufficient for
  HOSTCONTRACT, deferred-docs note for HOSTPOLICY when needed
- **Interfaces consumed**: SL-1 contract sections, existing README task/trace
  and remote-server sections, existing SECURITY trust model and known
  limitations, HOSTPOLICY scope from `specs/phase-plans-v6.md`
- **Parallel-safe**: yes, after SL-1 freezes the contract file path and title
- **Tasks**:
  - test: Check whether readers can discover the host contract from existing
    README/SECURITY structure; if not, add only a short pointer and avoid
    expanding policy guidance in this phase.
  - test: Confirm any README/SECURITY wording keeps PMCP as broker/policy
    gateway and the companion tenant server as execution authority.
  - test: Confirm any SECURITY wording does not imply PMCP provides
    multi-tenant isolation, per-tenant auth, sandbox runtime guarantees, or
    durable audit storage.
  - impl: If needed, add a narrow README pointer near the task/trace/remote
    server documentation to `specs/tenant-code-mode-host-contract.md`.
  - impl: If needed, add a narrow SECURITY pointer near known limitations for
    tenant code-mode hosting trust boundaries.
  - impl: If no README/SECURITY edit is needed, record that no-op decision in
    SL-3 closeout instead of adding docs churn.
  - verify: `rg -n "tenant code-mode|host contract|sandbox|execution authority|broker" README.md SECURITY.md specs/tenant-code-mode-host-contract.md`
  - verify: `git diff --check -- README.md SECURITY.md`

### SL-3 - Phase Verification and Closeout

- **Scope**: Reduce the source inventory, contract spec, and public-docs
  decision into the final HOSTCONTRACT checklist and execution handoff.
- **Owned files**: `plans/phase-plan-v6-hostcontract.md`
- **Interfaces provided**: completed HOSTCONTRACT checklist, verification
  summary, docs decision, execution notes, next-phase readiness note
- **Interfaces consumed**: SL-0 inventory results, SL-1 contract file, SL-2 docs
  alignment decision, Phase 1 exit criteria from `specs/phase-plans-v6.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Map every Phase 1 exit criterion to a contract section and verify
    there is no hidden manifest, handler, runtime, credential, or sandbox work
    in the final diff.
  - test: Run the targeted existing tests only if execution changes behavior or
    if the executor wants source-backed confidence beyond docs checks.
  - impl: Mark interface gates complete only after the contract file and any
    docs-pointer decision pass review.
  - impl: Record that HOSTREG consumes the contract for manifest/config
    registration, HOSTMETA consumes task/metadata sections, HOSTPOLICY consumes
    trust-boundary sections, and HOSTSOAK consumes mock fixture notes.
  - impl: Record any blocker that requires changing Phase 1, especially if
    existing PMCP task or trace behavior cannot support the contract as written.
  - verify: `rg -n "IF-0-HOSTCONTRACT|HOSTREG|HOSTMETA|HOSTPOLICY|HOSTSOAK|no new gateway tool|manifest" plans/phase-plan-v6-hostcontract.md specs/tenant-code-mode-host-contract.md`
  - verify: `git diff --check -- plans/phase-plan-v6-hostcontract.md specs/tenant-code-mode-host-contract.md README.md SECURITY.md`

## Verification

Lane-specific verification:

- `rg -n "class TraceContextInfo|class TaskMetadataInput|class McpTaskInfo|class McpTaskRecord|class InvokeInput|class ResourceInfo|TaskSupportMode" src/pmcp/types.py`
- `rg -n "_tool_task_support|_server_supports_tasks|_task_wire_metadata|list_tasks|get_task_result|cancel_task|read_resource|streamable" src/pmcp/client/manager.py`
- `rg -n "taskSupport|traceparent|requestor_context|tasks_result|redaction|audit_events" tests/test_tools.py tests/test_transport_http.py README.md SECURITY.md`
- `rg -n "run_script|get_run|get_result|cancel_run|execution\\.taskSupport|taskSupport|traceparent|tracestate|baggage|requestor|pollInterval|artifact|resource|streamable HTTP|stdio|lazy" specs/tenant-code-mode-host-contract.md`
- `rg -n "tenant code-mode|host contract|sandbox|execution authority|broker" README.md SECURITY.md specs/tenant-code-mode-host-contract.md`
- `rg -n "IF-0-HOSTCONTRACT|HOSTREG|HOSTMETA|HOSTPOLICY|HOSTSOAK|no new gateway tool|manifest" plans/phase-plan-v6-hostcontract.md specs/tenant-code-mode-host-contract.md`
- `git diff --check -- plans/phase-plan-v6-hostcontract.md specs/tenant-code-mode-host-contract.md README.md SECURITY.md`

Optional source-backed smoke checks if execution wants confidence beyond docs
checks or changes code unexpectedly:

- `uv run pytest tests/test_tools.py -k "optional_task or tasks_result or conformance_task_gateway_route_and_audit or propagates_trace_context"`
- `uv run pytest tests/test_transport_http.py -k "trace_headers or never_echo_tokens"`

Whole-phase regression:

- No full regression is required for a docs-only HOSTCONTRACT execution that
  changes only `specs/tenant-code-mode-host-contract.md`, optional README/SECURITY
  pointers, and this plan closeout.
- If execution changes runtime code despite the phase non-goals, run the
  relevant targeted `uv run pytest` commands above plus:
  `uv run ruff check src/pmcp tests/`,
  `uv run ruff format --check src/pmcp tests/`, and
  `uv run mypy src/pmcp --exclude baml_client`.

## Acceptance Criteria

- [x] `specs/tenant-code-mode-host-contract.md` exists and describes the
  PMCP-hosted tenant code-mode contract without adding PMCP-owned script
  execution.
- [x] The contract names expected tenant server tool families for script
  submission, run lookup, result retrieval, cancellation, and artifact/resource
  access without requiring exact final tool names.
- [x] The contract defines task capability, `execution.taskSupport`, task
  metadata, task IDs, statuses, cancellation, polling hints, and result
  retrieval through existing gateway task surfaces.
- [x] The contract defines trace and requestor metadata forwarding expectations
  for `traceparent`, `tracestate`, `baggage`, task `metadata`, and
  `requestor_context` without treating them as identity or secret transport.
- [x] The contract defines safe output rules for summaries, redacted logs,
  bounded raw output, artifact references, and no secret-bearing telemetry.
- [x] The contract names streamable HTTP, local stdio development, lazy
  configuration, and env-placeholder header compatibility assumptions.
- [x] Future mock-server fixture design notes are present for HOSTMETA/HOSTSOAK
  without depending on live infrastructure or cloud credentials.
- [x] README/SECURITY are either narrowly linked to the contract or explicitly
  left unchanged in closeout because HOSTPOLICY owns deeper guardrail docs.
- [x] The final diff contains no manifest registration, handler branch, new
  gateway tool, sandbox runtime, credential, or untrusted-code execution change.

## Execution Closeout

- SL-0 inventory confirmed the contract maps to existing PMCP host surfaces:
  task-capability gates, `execution.taskSupport`, transient task records,
  trace metadata forwarding, streamable HTTP compatibility, resource reads,
  output truncation, optional redaction, bounded audit events, and current
  README/SECURITY trust-boundary language.
- SL-1 added `specs/tenant-code-mode-host-contract.md` as the frozen
  host/tenant contract without source, manifest, credential, runtime, handler,
  sandbox, or untrusted-code execution changes.
- SL-2 added narrow README and SECURITY pointers. Deeper operator guardrail and
  policy documentation remains deferred to HOSTPOLICY.
- HOSTREG consumes the contract's role, tool-family, compatibility, and
  transport assumptions for manifest/config registration. HOSTMETA consumes the
  task lifecycle and metadata-forwarding sections. HOSTPOLICY consumes the
  trust-boundary and output-safety sections. HOSTSOAK consumes the mock-server
  fixture notes.
- No Phase 1 blocker was found. Existing PMCP task and trace behavior can
  support the contract as written.
