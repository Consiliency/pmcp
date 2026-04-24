# Tenant Code-Mode Host Contract

## Purpose

This contract freezes the PMCP-facing expectations for hosting a separate
tenant code-mode MCP server. PMCP is the host-side broker: it discovers the
downstream server, applies gateway policy, forwards bounded metadata, invokes
tools, tracks transient task IDs, reads resources, truncates and optionally
redacts output, and reports local diagnostics.

The tenant code-mode MCP server is the execution authority. It owns sandbox
runtime selection, isolation, job scheduling, artifact persistence, tenant
authorization, and script execution semantics. PMCP must be able to broker that
server through existing MCP surfaces without becoming a code runner.

## Roles

- **PMCP host**: a local-first MCP gateway that exposes stable gateway tools,
  connects to downstream MCP servers, enforces configured host policy, and
  presents task/resource state returned by those servers.
- **Tenant code-mode server**: a separate downstream MCP server that accepts
  code-mode run requests, executes them in its own controlled environment, and
  returns task state, summaries, logs, and artifact/resource references.
- **Client**: the MCP client connected to PMCP. The client calls PMCP gateway
  tools and receives PMCP-normalized results, task metadata, resources, and
  diagnostics.

## Non-Goals

This contract does not add a new PMCP gateway tool, manifest entry, handler
branch, sandbox runtime, credential, queue, artifact store, or durable log
store. It does not make PMCP a multi-tenant authorization layer. It also does
not require live hosted infrastructure, cloud credentials, or the companion
server's final package name.

## Terminology

- **Run**: a tenant server unit of code-mode execution. PMCP treats a run as a
  downstream MCP task when the server returns task metadata.
- **PMCP request ID**: an identifier for a PMCP gateway tool invocation or
  pending gateway request. It is host-local and distinct from a downstream task
  ID.
- **Downstream MCP task ID**: the task identifier returned by the tenant server
  and used with downstream `tasks/list`, `tasks/get`, `tasks/result`, and
  `tasks/cancel`.
- **Artifact reference**: a bounded handle, URI, resource ID, or metadata record
  that lets the client fetch large outputs through MCP resources instead of
  embedding them in a tool response.
- **Requestor context**: caller-supplied, non-secret metadata forwarded for
  tenant-server visibility. It is not identity, authorization, or credential
  transport.

## Expected Tenant Tool Families

The tenant server should expose discoverable tools for these families. Exact
tool names are not frozen; names such as `run_script`, `get_run`, `get_result`,
and `cancel_run` are examples only.

- **Run submission**: accept a code-mode request, arguments, execution options,
  and optional task metadata. Long-running submissions should return a
  downstream task ID instead of blocking until all raw output is complete.
- **Run lookup**: return current task/run state, status message, timestamps,
  polling hints, and lightweight diagnostics for a known run.
- **Result retrieval**: return a summary-first result for completed or failed
  runs, with bounded raw logs and artifact/resource references for larger
  payloads.
- **Cancellation**: request cooperative cancellation and return terminal or
  still-working task state. Cancellation may be idempotent for already terminal
  tasks.
- **Artifact/resource access**: expose large logs, generated files, structured
  outputs, or debug bundles through MCP resources or resource-like references
  instead of embedding large payloads in gateway tool output.

Tenant tools intended for asynchronous execution must advertise
`execution.taskSupport` as `optional` or `required`. Tools that do not advertise
task support are treated as synchronous from PMCP's perspective.

## Task Lifecycle Contract

PMCP forwards task-augmented calls through `gateway.invoke(..., task=...)` only
when the downstream server advertises task capability and the selected tool
advertises `execution.taskSupport` as `optional` or `required`. Required-task
tools are invoked with task metadata even when the client does not explicitly
request task mode. A task request to a non-task-capable tool or server fails
before downstream dispatch.

The tenant server should return task payloads using MCP-compatible fields such
as `taskId` or `task_id`, `status`, `statusMessage`, `createdAt`, `updatedAt`,
`ttl`, and `pollInterval`. PMCP accepts at least these status values and
preserves unknown future-compatible values in raw task metadata:

- `working`
- `input_required`
- `completed`
- `failed`
- `cancelled`

PMCP records downstream tasks in transient gateway memory. These records can be
listed and refreshed through `gateway.tasks_list` and `gateway.tasks_get`, task
results can be fetched through `gateway.tasks_result`, and cancellation can be
requested through `gateway.tasks_cancel`. `gateway.tasks_cancel` uses the
downstream MCP task ID, not a PMCP request ID. `gateway.list_pending` and
`gateway.cancel` remain PMCP request controls and must not be confused with
tenant run/task IDs.

The tenant server should treat `pollInterval` and `ttl` as hints and lifecycle
metadata. PMCP forwards them when supplied and may surface returned values to
clients, but PMCP does not persist task records past gateway process lifetime.

## Metadata Forwarding Contract

PMCP can forward OpenTelemetry-style trace context through
`_meta.traceparent`, `_meta.tracestate`, and `_meta.baggage` on downstream
requests. The same trace keys are accepted from HTTP request metadata where
documented. These values are strings only and are metadata, not authentication.

Task metadata supplied to `gateway.invoke` may include:

- `metadata`: a bounded object for tenant-server execution context.
- `ttl`: task lifetime hint in seconds.
- `pollInterval`: polling hint forwarded on the wire as `pollInterval`.
- `requestor_context` or downstream `requestorContext`: non-secret context for
  host/client visibility.

Trace baggage, task metadata, and requestor context must not carry bearer
tokens, API keys, OAuth codes, tenant auth tokens, user secrets, or durable
identity assertions. The tenant server may use those fields for correlation and
diagnostics, but authorization and tenant isolation must be enforced by the
tenant server or surrounding deployment controls.

## Result, Output, and Artifact Rules

Tenant code-mode responses should be summary-first. The top-level tool or task
result should make the run outcome clear before raw logs or large diagnostics.
Raw stdout/stderr, stack traces, dependency logs, and debug bundles must be
bounded, redacted when possible, and represented as artifact/resource
references when they exceed a small response envelope.

PMCP applies gateway output truncation and optional secret redaction to
`gateway.invoke` and `gateway.tasks_result` responses. That processing is a
host-side safety layer, not a substitute for tenant-server output hygiene. The
tenant server should avoid secret-bearing telemetry, secret-bearing artifact
names, and unbounded diagnostic fields.

PMCP does not provide durable sandbox-log storage. Large or durable execution
artifacts belong to the tenant server and should be exposed through MCP
resources or tenant-owned storage references. PMCP may proxy `gateway.read_resource`
for resources discovered from downstream metadata, subject to normal gateway
configuration and policy.

## Compatibility Assumptions

Streamable HTTP is the primary hosted transport path for a tenant code-mode MCP
server. Local `stdio` remains acceptable for development, mock fixtures, and
operator testing. PMCP supports lazy downstream configuration: a tenant server
can be registered without eager startup unless a future registration phase
explicitly configures eager startup.

Remote server headers may use PMCP's existing environment-placeholder behavior
so secrets stay in env stores or process environment instead of manifests. This
contract does not introduce tenant-specific credential storage. Hosted
deployments that need tenant auth, per-user isolation, or network controls must
provide them outside this phase's PMCP contract.

The companion runtime is vendor-neutral. The tenant server may choose its own
sandbox provider, language runtime, queue, artifact backend, and policy engine
without changing PMCP gateway APIs, provided it exposes MCP-compatible tools,
tasks, resources, and metadata.

## Host-Surface Inventory

This contract maps to current PMCP host surfaces:

- `TraceContextInfo`, `InvokeInput.trace_context`, and `_meta` handling define
  accepted trace keys.
- `TaskMetadataInput`, `McpTaskInfo`, `McpTaskRecord`, and `TaskSupportMode`
  define task metadata, transient records, and `execution.taskSupport` values.
- `_server_supports_tasks`, `_tool_task_support`, `_task_wire_metadata`,
  `list_tasks`, `get_task`, `get_task_result`, and `cancel_task` define PMCP's
  task gate and proxy behavior.
- `ResourceInfo` and `read_resource` define host-side resource discovery and
  resource reads.
- `gateway.invoke`, `gateway.tasks_result`, policy checks, truncation,
  optional redaction, and bounded `gateway.health.audit_events` define
  host-side output safety and diagnostics.
- README and SECURITY document the local-first trust model, streamable HTTP
  compatibility, trace metadata, transient task records, redaction, and the
  absence of durable audit storage or multi-tenant authorization in PMCP.

## Future Mock-Server Fixture Notes

Later HOSTMETA and HOSTSOAK phases should use a deterministic mock tenant MCP
server fixture rather than live infrastructure. The fixture should:

- advertise server task capability and a task-capable submission tool;
- expose one synchronous metadata tool and one asynchronous code-mode run tool;
- return task IDs, `working`, `input_required`, `completed`, `failed`, and
  `cancelled` statuses through downstream task methods;
- echo only safe trace/requestor metadata presence and never echo secrets;
- provide bounded summaries plus redacted log snippets;
- expose at least one MCP resource for larger artifacts;
- support deterministic cancellation and result retrieval; and
- run over local `stdio` and streamable HTTP test configurations without cloud
  credentials or the companion repo's final package name.
