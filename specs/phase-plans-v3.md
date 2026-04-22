# Phase roadmap v3

## Context

PMCP 1.10.0 completed the shared-service hardening roadmap: downstream startup is lazy by default, eager startup is explicit through `autoStart`, shared lifecycle mutation is serialized, refresh and lifecycle tools refuse active work by default, pending work is visible, and the HTTP shared-service contract is documented and soaked.

The next implementation risk is protocol drift. PMCP currently acts as an MCP gateway and compatibility layer, but the MCP specification has advanced to `2025-11-25` with new durable task semantics, richer tool metadata, authorization and elicitation changes, sampling tool calls, and gateway/proxy-oriented observability and transport conventions. The draft spec after `2025-11-25` also points at extension capability declaration, OpenTelemetry trace context propagation, deterministic `tools/list` ordering, and Streamable HTTP request header conventions.

PMCP should treat these protocol changes as the next foundation before adding broad settings or natural-language administration tools. Startup-policy mutation remains important, but it should sit behind a protocol-aligned gateway surface that understands modern MCP metadata, task execution, auth flows, and gateway/proxy observability.

Research sources:

- MCP `2025-11-25` key changes: `https://modelcontextprotocol.io/specification/2025-11-25/changelog`
- MCP `2025-11-25` specification: `https://modelcontextprotocol.io/specification/2025-11-25`
- MCP Tasks: `https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks`
- MCP Tools: `https://modelcontextprotocol.io/specification/2025-11-25/server/tools`
- MCP draft changelog after `2025-11-25`: `https://modelcontextprotocol.io/specification/draft/changelog`
- MCP roadmap: `https://modelcontextprotocol.io/development/roadmap`

## Architecture North Star

PMCP should be a protocol-current MCP gateway that can safely broker modern servers and clients without hiding important capability metadata or weakening protocol semantics. It should negotiate current protocol versions where supported, preserve and expose modern schema and metadata fields, map durable MCP task execution onto PMCP's pending/cancel/status model, support modern authorization and elicitation flows without leaking credentials, and provide audit-ready gateway/proxy observability.

Once that protocol foundation is stable, PMCP can add persistent startup-policy and setup/profile administration in a way that is portable across clients and aligned with MCP's registry, server metadata, and enterprise gateway direction.

## Assumptions

- PMCP must remain backward compatible with older MCP servers that only support earlier protocol versions.
- Streamable HTTP remains PMCP's preferred shared-service transport; stdio remains supported for local and child-process downstream servers.
- The Python MCP SDK dependency may support newer protocol pieces, but PMCP's custom stdio/downstream manager path still needs explicit review and tests.
- PMCP's public gateway tool IDs should remain stable unless a phase explicitly freezes a new additive tool.
- New MCP fields that PMCP does not understand should be preserved where practical instead of dropped.
- MCP Tasks are experimental in `2025-11-25`; PMCP should support them conservatively and isolate task support behind explicit capability checks.
- Startup-policy mutation should edit user-owned config only through structured, previewable operations.

## Non-Goals

- Do not remove compatibility with `2024-11-05`, `2025-03-26`, or `2025-06-18` servers.
- Do not implement every MCP extension or draft feature before it has a stable enough shape; draft features should be capability-gated.
- Do not make PMCP a general OAuth provider or enterprise identity broker.
- Do not add arbitrary JSON patching or free-form natural-language settings mutation.
- Do not require external MCP services for normal test coverage.
- Do not replace the existing progressive-disclosure gateway tools.

## Cross-Cutting Principles

- Capability negotiation first: PMCP should infer behavior from negotiated protocol version and declared capabilities, not server names or package versions.
- Preserve metadata: unknown but valid MCP fields should remain available to clients when PMCP can safely pass or expose them.
- Keep task and pending semantics coherent: PMCP pending requests, cancellation, status, and task lifecycle must not contradict each other.
- Auth and elicitation flows must avoid secret exfiltration: prefer browser/out-of-band flows and explicit user-controlled credential storage.
- Gateway observability should be structured: audit, trace, and status data should be machine-readable without exposing secrets or client-identifying data unnecessarily.
- Configuration changes must be previewable and reversible.

## Top Interface-Freeze Gates

- IF-0-PROTO-1 — PMCP negotiates and records MCP protocol versions and preserves modern tool/resource/prompt metadata without breaking older servers.
- IF-0-TASKS-2 — PMCP maps MCP task-augmented execution onto pending/list/cancel/status semantics with deterministic task visibility and cancellation behavior.
- IF-0-AUTH-3 — PMCP supports modern MCP auth and elicitation expectations through safe, explicit, non-secret-leaking flows.
- IF-0-OBSERVE-4 — PMCP exposes gateway/proxy observability, trace propagation, deterministic catalog ordering, and protocol header behavior in a testable contract.
- IF-0-CONFIG-5 — PMCP provides structured persistent startup-policy and setup/profile administration without arbitrary config mutation.
- IF-0-CONFORM-6 — PMCP has a cross-version conformance soak covering old and current MCP protocol behavior, tasks, auth, metadata, and gateway/proxy paths.

## Phases

### Phase 1 — Protocol Version and Metadata Alignment (PROTO)

**Objective**

Bring PMCP's downstream client and gateway surfaces up to current MCP protocol expectations while preserving compatibility with older servers.

**Exit criteria**

- [x] PMCP initializes downstream servers with an explicit protocol negotiation strategy instead of hardcoding only `2024-11-05`.
- [x] PMCP records negotiated protocol version and exposes it through internal status and appropriate diagnostic views.
- [x] Tool metadata preserves and exposes modern fields such as `title`, `icons`, `outputSchema`, `annotations`, `execution.taskSupport`, and JSON Schema dialect information where present.
- [x] Resource, resource template, and prompt metadata preserve modern icon/title fields where supported by the SDK or raw payloads.
- [x] `gateway.catalog_search` and `gateway.describe` remain backward compatible while surfacing richer metadata additively.
- [x] Tests cover old-protocol servers, current-protocol servers, missing optional fields, unknown extra fields, and schema dialect defaults.

**Scope notes**

- Start by auditing `ClientManager._send_initialize`, remote transport initialization, and SDK-provided protocol negotiation behavior.
- Prefer additive fields in PMCP types and outputs; avoid changing existing required fields.
- If upstream SDK types drop unknown fields, preserve raw metadata in a narrow PMCP-owned side channel.
- Treat tool annotations as untrusted hints in docs and policy decisions.
- Keep JSON Schema validation behavior compatible with existing schemas while documenting `2020-12` defaults.

**Non-goals**

- Do not implement MCP Tasks in this phase beyond preserving `execution.taskSupport`.
- Do not change policy decisions based solely on untrusted annotations.
- Do not require every manifest server to support `2025-11-25`.

**Key files**

- `src/pmcp/client/manager.py`
- `src/pmcp/types.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/cli.py`
- `tests/test_client_manager.py`
- `tests/test_tools.py`
- `tests/test_cli.py`

**Depends on**

- (none)

**Produces**

- IF-0-PROTO-1 — PMCP negotiates and records MCP protocol versions and preserves modern tool/resource/prompt metadata without breaking older servers.

### Phase 2 — Task-Aware Gateway Execution (TASKS)

**Objective**

Support MCP `2025-11-25` task-augmented execution in a way that composes with PMCP's existing pending-request, cancellation, lifecycle, and status model.

**Exit criteria**

- [x] PMCP detects server and tool task support from capabilities and `execution.taskSupport`.
- [x] PMCP can invoke optional or required task-supported tools without treating accepted task creation as a completed tool result.
- [x] PMCP exposes task state through gateway-visible pending/status surfaces without breaking existing `gateway.list_pending` output.
- [x] PMCP can proxy or implement `tasks/list`, `tasks/get`, `tasks/result`, and `tasks/cancel` where supported by downstream servers.
- [x] Forced refresh, disconnect, and restart behavior clearly covers active MCP tasks and normal pending requests.
- [x] Tests cover working, input-required, completed, failed, cancelled, expired/missing, and required-task tool scenarios.

**Scope notes**

- Model MCP tasks as distinct from but related to PMCP `PendingRequest` records; avoid overloading request IDs where task IDs are required.
- Preserve task IDs as opaque server-generated strings.
- Bind task visibility to the same server/requestor context PMCP can actually enforce; document limitations for unauthenticated local transports.
- If a downstream server requires task augmentation and PMCP cannot support it yet, return a structured gateway error that tells the client why.
- Make cancellation idempotence and terminal-state behavior match MCP requirements.

**Non-goals**

- Do not create PMCP-owned durable job storage beyond what is needed to broker downstream task state.
- Do not implement speculative retry policy for tasks; MCP's roadmap still calls retry semantics an open area.
- Do not expose task results to unrelated requestors when authorization context is unavailable.

**Key files**

- `src/pmcp/client/manager.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/types.py`
- `src/pmcp/server.py`
- `tests/test_client_manager.py`
- `tests/test_tools.py`
- `tests/test_server.py`
- `tests/test_phase4_e2e.py`

**Depends on**

- IF-0-PROTO-1

**Produces**

- IF-0-TASKS-2 — PMCP maps MCP task-augmented execution onto pending/list/cancel/status semantics with deterministic task visibility and cancellation behavior.

### Phase 3 — Authorization and Elicitation Modernization (AUTH)

**Objective**

Align PMCP's auth and credential UX with current MCP authorization and elicitation patterns while preserving existing local secret workflows.

**Exit criteria**

- [x] PMCP recognizes and reports authorization metadata relevant to OIDC discovery, protected resource metadata, and Client ID Metadata Documents where available.
- [x] PMCP handles incremental scope consent challenges in a structured way rather than reducing them to opaque connection failures.
- [x] `gateway.auth_connect` or successor flows support URL-mode elicitation for secure out-of-band credential collection when a server can provide it.
- [x] Missing-auth, insufficient-scope, and policy-denied states are distinct in health/status/doctor output.
- [x] Secrets and auth URLs are redacted in logs, status, doctor output, and release diagnostics.
- [x] Tests cover missing auth, expired/insufficient scope, URL-mode elicitation, local env-store fallback, and refusal paths.

**Scope notes**

- Reuse the existing 1Password/env-store guidance and `gateway.auth_connect` semantics where possible.
- Prefer structured auth state and next-step output over interactive prompts inside gateway tools.
- Support MCP auth metadata discovery as client/gateway behavior; do not make PMCP an authorization server unless a later roadmap requires it.
- Keep `/health` and `/metrics` unauthenticated unless a separate security decision changes that contract.

**Non-goals**

- Do not implement enterprise SSO, Cross-App Access, DPoP, or Workload Identity Federation directly in this phase.
- Do not store third-party refresh tokens unless there is a clear encrypted storage contract.
- Do not leak bearer tokens, API keys, auth codes, or userinfo in any output.

**Key files**

- `src/pmcp/tools/handlers.py`
- `src/pmcp/types.py`
- `src/pmcp/cli.py`
- `src/pmcp/transport/http.py`
- `src/pmcp/manifest/installer.py`
- `tests/test_tools.py`
- `tests/test_cli.py`
- `tests/test_http_transport.py`
- `tests/test_secrets_command.py`
- `SECURITY.md`
- `README.md`

**Depends on**

- IF-0-PROTO-1

**Produces**

- IF-0-AUTH-3 — PMCP supports modern MCP auth and elicitation expectations through safe, explicit, non-secret-leaking flows.

### Phase 4 — Gateway Observability and Transport Semantics (OBSERVE)

**Objective**

Make PMCP's gateway/proxy behavior auditable and compatible with emerging MCP observability and Streamable HTTP conventions.

**Exit criteria**

- [x] PMCP preserves or emits OpenTelemetry trace context metadata where requests cross gateway boundaries.
- [x] Gateway audit events capture method, server, tool/resource/prompt identity, protocol version, lifecycle action, task ID when present, outcome, latency, and redacted error details.
- [x] `tools/list` and PMCP catalog outputs are deterministically ordered for caching and prompt stability.
- [x] Streamable HTTP requests include or tolerate current/draft MCP method/name header conventions where supported by clients and servers.
- [x] Status and doctor commands expose enough gateway/proxy diagnostics to debug session, header, auth, and rate-limit behavior without exposing secrets.
- [x] Tests cover deterministic ordering, trace propagation, redaction, HTTP header compatibility, and audit event shape.

**Scope notes**

- Keep observability local and structured first; external exporter integration can come later.
- Use `_meta` trace context conventions where they are stable enough, and gate draft-only behavior behind compatibility paths.
- Avoid client-identifying audit fields unless they are explicitly redacted or hashed.
- Maintain compatibility with existing rmcp/Codex HTTP transport workarounds.

**Non-goals**

- Do not require a specific OpenTelemetry backend.
- Do not introduce a database just for audit logs.
- Do not make draft header requirements break older clients.

**Key files**

- `src/pmcp/transport/http.py`
- `src/pmcp/server.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/client/manager.py`
- `src/pmcp/cli.py`
- `tests/test_http_transport.py`
- `tests/test_server.py`
- `tests/test_tools.py`
- `tests/test_cli.py`
- `README.md`
- `SECURITY.md`

**Depends on**

- IF-0-PROTO-1
- IF-0-TASKS-2

**Produces**

- IF-0-OBSERVE-4 — PMCP exposes gateway/proxy observability, trace propagation, deterministic catalog ordering, and protocol header behavior in a testable contract.

### Phase 5 — Discovery and Configuration Administration (CONFIG)

**Objective**

Add structured persistent administration for startup policy, setup profiles, and server discovery after the gateway understands current MCP metadata and observability.

**Exit criteria**

- [x] PMCP exposes read-only effective configuration/status explaining eager, lazy, skipped, policy-denied, missing-auth, and provisioned states with source attribution.
- [x] PMCP provides structured startup-policy mutation that can preview and apply `autoStart` changes while preserving unrelated `.mcp.json` content.
- [x] PMCP detects stale `autoStart`, legacy `disableAutoStart`, unknown server names, missing auth, and policy conflicts.
- [x] `pmcp setup` supports named profiles for common modes such as local stdio, shared-local HTTP, authenticated shared HTTP, and CI.
- [x] Registry/server-card-aware discovery is evaluated and integrated where stable enough to improve PMCP's manifest/provisioning flow.
- [x] Tests cover atomic config edits, no-op edits, conflict detection, profile output, and backward compatibility with existing config files.

**Scope notes**

- Candidate tools include `gateway.config_status`, `gateway.get_startup_policy`, and `gateway.set_startup_policy`.
- Mutating tools should support dry-run/preview output and require explicit apply semantics.
- Use structured config parsing and writing rather than ad hoc string manipulation.
- Keep natural-language wrappers out of this phase unless they compile to structured previews and require explicit confirmation.
- Server card support should be read-only/discovery-oriented unless the stable spec gives stronger mutation semantics.

**Non-goals**

- Do not add arbitrary JSON patch operations.
- Do not silently edit multiple config files when the intended source is ambiguous.
- Do not make profile setup overwrite user config without `--write` or equivalent explicit user intent.

**Key files**

- `src/pmcp/config/loader.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/types.py`
- `src/pmcp/cli.py`
- `src/pmcp/manifest/loader.py`
- `tests/test_tools.py`
- `tests/test_cli.py`
- `tests/test_lazy_start.py`
- `tests/test_startup_resolver.py`
- `README.md`

**Depends on**

- IF-0-PROTO-1
- IF-0-AUTH-3
- IF-0-OBSERVE-4

**Produces**

- IF-0-CONFIG-5 — PMCP provides structured persistent startup-policy and setup/profile administration without arbitrary config mutation.

### Phase 6 — Protocol Conformance Soak and Release Gate (CONFORM)

**Objective**

Validate PMCP against mixed old/current MCP servers and the new gateway/admin contracts before release.

**Exit criteria**

- [ ] Tests cover at least one old-protocol stdio-style fake server and one current-protocol fake server.
- [ ] Tests cover tool metadata preservation, task-supported tools, auth/elicitation states, deterministic ordering, trace/header behavior, and startup-policy mutation.
- [ ] HTTP smoke covers shared-service gateway behavior without external network access.
- [x] CLI smoke covers status, doctor, setup profiles, and config status.
- [ ] Release notes document supported MCP protocol versions, task support limitations, auth limitations, and any draft-feature compatibility flags.
- [ ] Full test suite, lint, format check, mypy, build, and local smoke commands pass.

**Scope notes**

- Prefer deterministic fake MCP servers and local Starlette/TestClient utilities.
- Include regression tests for all v2 shared-service lifecycle semantics to ensure protocol work does not weaken them.
- If external MCP SDK conformance tests exist and are practical, run them as optional/manual release evidence rather than mandatory CI until stable.
- Mark draft-only features clearly in docs and changelog.

**Non-goals**

- Do not require live third-party MCP servers or credentials.
- Do not block release on experimental draft features unless PMCP claims support for them.
- Do not benchmark throughput beyond basic bounded concurrency correctness.

**Key files**

- `tests/test_client_manager.py`
- `tests/test_tools.py`
- `tests/test_http_transport.py`
- `tests/test_cli.py`
- `tests/test_server.py`
- `tests/test_phase4_e2e.py`
- `CHANGELOG.md`
- `README.md`
- `SECURITY.md`
- `specs/phase-plans-v3.md`

**Depends on**

- IF-0-PROTO-1
- IF-0-TASKS-2
- IF-0-AUTH-3
- IF-0-OBSERVE-4
- IF-0-CONFIG-5

**Produces**

- IF-0-CONFORM-6 — PMCP has a cross-version conformance soak covering old and current MCP protocol behavior, tasks, auth, metadata, and gateway/proxy paths.

## Phase Dependency DAG

```text
PROTO -> TASKS -> OBSERVE -> CONFIG -> CONFORM
   \        \          /
    \------> AUTH ----/
```

## Execution Notes

- Phase 1 should run first because every later phase depends on negotiated protocol version and metadata preservation.
- Phase 2 and Phase 3 can be planned after Phase 1 freezes interfaces; they may execute in parallel if owned files are split carefully, but both touch gateway handler/types surfaces.
- Phase 4 should wait for Phase 2 task IDs and Phase 1 metadata because observability needs to name protocol/task context accurately.
- Phase 5 should wait for Phase 3 and Phase 4 so config/admin UX can surface auth and observability facts correctly.
- Phase 6 runs last and should be treated as the release gate for this roadmap.
- If draft MCP header/extension behavior changes during implementation, prefer compatibility shims and documentation over hard dependencies.

## Verification

Run these after implementation phases, not during roadmap planning:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_http_transport.py tests/test_cli.py tests/test_server.py -q
uv run pytest tests/test_lazy_start.py tests/test_startup_resolver.py tests/test_phase4_e2e.py tests/test_secrets_command.py -q
uv run pytest -q
```

Protocol/admin smoke before release:

```bash
uv build
pmcp status --json --pending
pmcp status --verbose --pending
pmcp doctor
pmcp setup --client claude --mode http
pmcp setup --client opencode --mode http
pmcp setup --client claude --mode stdio
```
