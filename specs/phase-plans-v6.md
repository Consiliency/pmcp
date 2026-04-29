# Phase roadmap v6

## Context

PMCP is currently a local-first progressive MCP gateway. It exposes stable
`gateway.*` meta-tools, lazily manages downstream MCP servers, forwards
task-augmented invocations when downstream servers advertise task support, and
surfaces local structured observability through health, pending-request, task,
trace, and audit fields.

The next architectural extension is to prepare PMCP to act as the host-side
broker for a separate tenant code-mode MCP server. The companion server will own
tenant sandbox execution. PMCP should discover, configure, authorize, invoke,
monitor, and document that server without turning the gateway process into a
general-purpose code runner.

This roadmap prepares PMCP to host that tenant code-mode capability as a remote
or locally configured downstream MCP server while preserving the existing
single-gateway abstraction for clients such as mobile devices that do not have a
local shell.

## Architecture North Star

PMCP remains the gateway and policy broker. The tenant code-mode MCP server
remains the execution authority.

The desired user flow is:

1. A client connects only to PMCP.
2. PMCP exposes the code-mode tenant server as a normal discoverable capability.
3. The model asks PMCP for the capability, describes the sandbox tool contract,
   and submits scripts through `gateway.invoke`.
4. PMCP forwards trace, requestor, auth, policy, and task metadata to the tenant
   server.
5. The tenant server runs sandboxed code and returns task IDs, logs, artifacts,
   summaries, and safe diagnostics through MCP task/result surfaces.
6. PMCP continues to own gateway-level policy, truncation, redaction,
   lifecycle controls, and operator visibility.

## Assumptions

- The companion tenant MCP repo is developed separately and will expose a
  streamable HTTP MCP endpoint plus optional local stdio development mode.
- PMCP should integrate the companion server through existing downstream MCP
  abstractions first, not through a new bespoke execution subsystem.
- Task-capable execution is the primary integration path because sandbox runs
  are long-running, cancellable, and produce logs/artifacts over time.
- PMCP may add host-side contracts, manifest entries, tests, and documentation,
  but it must not execute untrusted scripts inside the PMCP process.
- The current HTTP bearer-token model is not sufficient for hosted
  multi-tenant production by itself; this roadmap only prepares PMCP's host
  integration surface.

## Non-Goals

- Do not add a general `gateway.run_code` or `pmcp execute` tool in this
  roadmap.
- Do not implement the sandbox runtime, job queue, artifact store, tenant
  database, billing, or isolation layer in PMCP.
- Do not make PMCP a multi-tenant authorization server.
- Do not persist full sandbox logs or artifacts in PMCP.
- Do not require live hosted infrastructure or real cloud credentials for
  tests.

## Cross-Cutting Principles

- Broker, do not run: all script execution remains inside the companion MCP
  server.
- Contract first: PMCP must freeze the expected tool, task, metadata, and
  diagnostics shapes before adding user-facing recommendations.
- Additive compatibility: existing gateway tools and response fields stay
  backward compatible.
- Policy remains central: PMCP must continue to apply allow/deny, output caps,
  and redaction before returning sandbox results.
- Mobile-first ergonomics: one PMCP connection should provide enough discovery,
  schema, task, and result information for clients without local computer access.

## Top Interface-Freeze Gates

- IF-0-HOSTCONTRACT-1 — PMCP has a documented downstream tenant code-mode MCP
  contract covering tools, task support, trace metadata, result summaries, and
  artifact/resource expectations.
- IF-0-HOSTREG-2 — PMCP can advertise and configure the tenant code-mode MCP
  server through manifest/config paths without eager startup or local execution.
- IF-0-HOSTMETA-3 — PMCP forwards bounded requestor, trace, and task metadata to
  tenant code-mode tools and exposes returned run/task state through existing
  gateway task surfaces.
- IF-0-HOSTPOLICY-4 — PMCP documents and tests host-side policy, auth, and
  operator guardrails for the tenant code-mode server.
- IF-0-HOSTSOAK-5 — PMCP has mock-server contract tests and release docs proving
  the hosted code-mode integration path without live infrastructure.

## Phases

### Phase 1 — Tenant Host Contract Freeze (HOSTCONTRACT)

**Objective**

Freeze the PMCP-facing contract for a tenant code-mode MCP server before any
manifest, handler, or docs integration depends on it.

**Exit criteria**

- [x] A PMCP-host contract spec describes expected tenant server tools such as
  `run_script`, `get_run`, `get_result`, `cancel_run`, and artifact/resource
  access without requiring those exact names if the companion repo chooses a
  better final shape.
- [x] The contract defines required MCP task behavior, including advertised
  server task capability, tool `execution.taskSupport`, task IDs, status values,
  cancellation semantics, polling hints, and result retrieval.
- [x] The contract defines PMCP metadata forwarding expectations for
  `traceparent`, `tracestate`, `baggage`, and task `requestor_context`.
- [x] The contract defines safe output rules: summaries first, redacted logs,
  bounded raw output, artifact references instead of large payloads, and no
  secret-bearing telemetry.
- [x] The contract names compatibility assumptions for streamable HTTP, local
  stdio development, and lazy downstream configuration.

**Scope notes**

- Likely lanes:
  - Contract document and terminology alignment with existing gateway task/docs
    language.
  - Test fixture design notes for a future mock tenant MCP server.
- Start from current PMCP task, trace, policy, and remote-server behavior.
- Keep the contract vendor-neutral so the companion repo can choose its runtime
  provider without changing PMCP's gateway API.

**Non-goals**

- Do not add a new gateway tool.
- Do not edit the main manifest in this phase unless required to document a
  concrete contract example.

**Key files**

- `specs/tenant-code-mode-host-contract.md`
- `README.md`
- `SECURITY.md`
- `src/pmcp/types.py`
- `src/pmcp/client/manager.py`

**Depends on**

- (none)

**Produces**

- IF-0-HOSTCONTRACT-1 — PMCP has a documented downstream tenant code-mode MCP
  contract covering tools, task support, trace metadata, result summaries, and
  artifact/resource expectations.

### Phase 2 — Tenant Capability Registration (HOSTREG)

**Objective**

Make the tenant code-mode MCP server discoverable and configurable through PMCP's
existing manifest, remote config, and capability-request flows without starting
it eagerly or pretending it is a local CLI.

**Exit criteria**

- [x] PMCP has a manifest or documented registration entry for the tenant
  code-mode MCP server with keywords for code execution, sandbox execution,
  mobile code mode, task runs, logs, and artifacts.
- [x] `gateway.request_capability` can recommend the tenant code-mode MCP server
  for hosted sandbox/code-mode requests while preserving CLI-first behavior for
  local installed CLIs.
- [x] `gateway.catalog_search(include_offline=true)` can surface cached or
  configured tenant code-mode tool cards once a compatible server is registered.
- [x] Remote streamable HTTP configuration examples show tenant header
  placeholders without printing secret values.
- [x] Tests prove tenant code-mode discovery does not execute code, start an
  unrelated local process, or mix the sandbox server with CLI hints.

**Scope notes**

- Likely lanes:
  - Manifest/discovery integration and handler-level tests.
  - README setup examples for remote and local-development tenant server modes.
- Reuse existing remote-server config and `${ENV_VAR}` header interpolation.
- Keep server startup lazy unless explicitly placed in `autoStart`.

**Non-goals**

- Do not introduce tenant-specific auth storage beyond existing env-store and
  remote header placeholder behavior.
- Do not require the companion repo to be published before mock/test coverage can
  pass.

**Key files**

- `src/pmcp/manifest/manifest.yaml`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/types.py`
- `tests/test_tools.py`
- `tests/test_manifest.py`
- `README.md`

**Depends on**

- IF-0-HOSTCONTRACT-1

**Produces**

- IF-0-HOSTREG-2 — PMCP can advertise and configure the tenant code-mode MCP
  server through manifest/config paths without eager startup or local execution.

### Phase 3 — Invocation Metadata and Task Brokering (HOSTMETA)

**Objective**

Harden the existing `gateway.invoke` and `gateway.tasks_*` paths for tenant
code-mode usage by proving metadata propagation, task lifecycle visibility, and
result processing against a mock task-capable server.

**Exit criteria**

- [x] Mock-server tests prove `gateway.invoke(..., task=...)` forwards task
  metadata and records returned tenant run task IDs.
- [x] Trace metadata is accepted through `_meta` or `trace_context`, sanitized by
  existing safeguards, and forwarded to the downstream tenant server.
- [x] `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and
  `gateway.tasks_cancel` expose tenant run state without confusing PMCP request
  IDs with downstream task IDs.
- [x] Result processing continues to apply truncation and optional redaction to
  sandbox logs, diagnostics, and result payloads.
- [x] Health/audit events include enough non-secret task and tool identity to
  debug tenant code-mode failures from PMCP.

**Scope notes**

- Likely lanes:
  - Mock task-capable MCP server fixture and gateway integration tests.
  - Documentation of the tenant task lifecycle through existing PMCP surfaces.
- Prefer testing existing abstractions before adding new models.
- If existing transient task state is insufficient, add only additive metadata
  fields required for host visibility.

**Non-goals**

- Do not persist artifacts or task records in PMCP.
- Do not add streaming log transport unless the current MCP task/result contract
  proves insufficient and a follow-up roadmap is created.

**Key files**

- `src/pmcp/client/manager.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/types.py`
- `tests/test_tools.py`
- `tests/test_integration.py`
- `README.md`

**Depends on**

- IF-0-HOSTCONTRACT-1

**Produces**

- IF-0-HOSTMETA-3 — PMCP forwards bounded requestor, trace, and task metadata to
  tenant code-mode tools and exposes returned run/task state through existing
  gateway task surfaces.

### Phase 4 — Host Policy and Operator Guardrails (HOSTPOLICY)

**Objective**

Define the PMCP-side safety posture for exposing a tenant code-mode server,
including policy templates, auth guidance, lifecycle disruption rules, and
operator diagnostics.

**Exit criteria**

- [x] README and SECURITY clearly distinguish PMCP host responsibility from the
  companion sandbox server's tenant isolation responsibility.
- [x] Policy examples show how to allow or deny the tenant server and its tools
  without exposing unrelated downstream MCP servers.
- [x] Auth examples use env placeholders and `gateway.auth_connect`/env-store
  guidance without printing tenant tokens.
- [x] Operator docs explain rate limits, `/health` and `/metrics` exposure,
  task cancellation, artifact retention boundaries, and residual risks for
  hosted deployments.
- [x] Tests cover policy-denied tenant server/tool paths and redaction of
  sandbox-like secret strings in result processing.

**Scope notes**

- Likely lanes:
  - Policy/auth docs and sample config.
  - Policy/redaction regression tests for tenant-code-mode-shaped outputs.
- The docs should be explicit that PMCP alone is not sufficient for multi-tenant
  production isolation.

**Non-goals**

- Do not implement SSO, RBAC, billing, or tenant identity in PMCP.
- Do not broaden existing HTTP auth beyond what the host integration requires to
  document safely.

**Key files**

- `README.md`
- `SECURITY.md`
- `src/pmcp/policy/policy.py`
- `tests/test_policy.py`
- `tests/test_auth.py`
- `tests/test_tools.py`

**Depends on**

- IF-0-HOSTCONTRACT-1
- IF-0-HOSTREG-2

**Produces**

- IF-0-HOSTPOLICY-4 — PMCP documents and tests host-side policy, auth, and
  operator guardrails for the tenant code-mode server.

### Phase 5 — Host Integration Soak and Release Gate (HOSTSOAK)

**Objective**

Close the PMCP host-readiness work with deterministic contract tests, docs, and
release notes proving that PMCP can broker a tenant code-mode MCP server without
owning the sandbox runtime.

**Exit criteria**

- [x] A deterministic mock tenant code-mode MCP server fixture exercises
  discovery, describe, invoke, task polling, result retrieval, cancellation,
  policy denial, and redaction.
- [x] End-to-end tests prove mobile/no-local-shell users can discover the hosted
  code-mode path from PMCP and submit a task-capable sandbox run through
  `gateway.invoke`.
- [x] README has a concise operator/user flow for PMCP plus the companion tenant
  code-mode MCP server.
- [x] CHANGELOG records the host integration capability with precise wording that
  PMCP brokers execution but does not run scripts itself.
- [x] Full release verification passes before version bump or publish.

**Scope notes**

- Likely lanes:
  - Mock tenant MCP e2e coverage and release-gate verification.
  - README/CHANGELOG closeout and roadmap checklist reconciliation.
- Treat any required new runtime contract as a blocker that feeds back into
  HOSTCONTRACT, HOSTREG, or HOSTMETA rather than expanding soak scope.

**Non-goals**

- Do not depend on a live hosted sandbox service.
- Do not add unrelated gateway features during soak.

**Key files**

- `tests/test_phase6_tenant_code_mode.py`
- `tests/test_tools.py`
- `tests/test_policy.py`
- `README.md`
- `CHANGELOG.md`
- `specs/phase-plans-v6.md`

**Depends on**

- IF-0-HOSTMETA-3
- IF-0-HOSTPOLICY-4

**Produces**

- IF-0-HOSTSOAK-5 — PMCP has mock-server contract tests and release docs proving
  the hosted code-mode integration path without live infrastructure.

## Phase Dependency DAG

```text
HOSTCONTRACT -> HOSTREG ----\
            \-> HOSTMETA ----> HOSTSOAK
HOSTCONTRACT -> HOSTPOLICY -/
HOSTREG ------> HOSTPOLICY
```

## Execution Notes

- Phase 1 should run first because it freezes the host/tenant contract consumed
  by both repos.
- Phase 2 and Phase 3 can be planned after Phase 1. They can execute in parallel
  only if manifest/discovery work and task/mock-server work have disjoint file
  ownership.
- Phase 4 can start after Phase 1, but final docs should incorporate Phase 2
  registration details.
- Phase 5 is the release gate and should not introduce new public gateway
  contracts unless prior phases missed an integration blocker.
- HOSTSOAK execution added deterministic non-live tenant coverage and release
  docs. The remaining baseline formatting blocker was fixed by formatting
  `src/pmcp/summary/generator.py` and
  `src/pmcp/summary/template_fallback.py`; `uv run ruff format --check src/ tests/`
  now passes.
- Next command: none - v6 roadmap complete.

## Verification

Run these after implementation phases, not during roadmap planning:

```bash
uv run pytest tests/test_tools.py -k "request_capability or catalog_search or tasks or tenant or code_mode"
uv run pytest tests/test_policy.py -k "policy or redaction or tenant"
uv run pytest tests/test_integration.py -k "task or remote or tenant"
```

Whole-roadmap release verification:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
uv run pytest -q
uv build
git diff --check
```
