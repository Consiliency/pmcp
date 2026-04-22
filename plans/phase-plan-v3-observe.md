# OBSERVE: Gateway Observability and Transport Semantics

## Context

Phase 4 of `specs/phase-plans-v3.md` makes PMCP's gateway and proxy behavior auditable while preserving the protocol, task, and auth contracts already completed in Phases 1 through 3. The working tree already records negotiated protocol versions on server status, exposes task IDs through invoke/pending/task surfaces, redacts auth diagnostics, and reports startup/auth status through `gateway.health` and `pmcp status --verbose`.

The remaining Phase 4 work is to add a PMCP-owned structured observability contract around those facts: trace context propagation where PMCP can safely preserve it, redacted audit events for gateway actions, deterministic ordering for tool/catalog/status surfaces, Streamable HTTP compatibility with stable and draft request headers, and status/doctor diagnostics that make transport/session/header/auth/rate-limit behavior debuggable without exposing secrets.

Official MCP documentation checked during planning confirms that `2025-11-25` Streamable HTTP remains POST/GET/DELETE based and that draft transport work adds `Mcp-Method` and `Mcp-Name` request headers plus OpenTelemetry `_meta` trace context conventions. Treat those draft-only behaviors as tolerant compatibility paths, not hard requirements for older clients.

## Interface Freeze Gates

- [ ] IF-0-OBSERVE-1 — `pmcp.types` defines additive observability models for trace context, gateway audit events, and gateway transport diagnostics; existing public output fields remain backward compatible.
- [ ] IF-0-OBSERVE-2 — PMCP audit events capture method/action, server name, tool/resource/prompt identity, negotiated protocol version, task ID when present, outcome, latency, and redacted error/auth details without storing credentials or raw request bodies.
- [ ] IF-0-OBSERVE-3 — Trace context keys `traceparent`, `tracestate`, and `baggage` are accepted from MCP `_meta` and/or HTTP headers where available, validated as non-secret strings, and propagated only through explicit PMCP-owned context fields or downstream request metadata.
- [ ] IF-0-OBSERVE-4 — `tools/list`, `gateway.catalog_search`, `gateway.describe` lookup-dependent outputs, server status, pending requests, and task lists use deterministic ordering by stable public identifiers when relevance sorting is not explicitly requested.
- [ ] IF-0-OBSERVE-5 — Streamable HTTP `/mcp` tolerates draft `Mcp-Method`, `Mcp-Name`, and `MCP-Protocol-Version` headers from conforming clients, validates only compatibility-safe mismatches, and does not break existing rmcp/Codex workarounds.
- [ ] IF-0-OBSERVE-6 — `gateway.health`, `pmcp status --verbose`, and `pmcp doctor` expose gateway/proxy diagnostics for transport, session/header compatibility, auth metadata, rate limits, and trace/audit readiness without exposing bearer tokens, API keys, auth codes, or user-identifying data.

## Lane Index & Dependencies

- SL-0 — Observability model contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 — Deterministic manager and downstream trace context; Depends on: SL-0; Blocks: SL-2, SL-5, SL-6; Parallel-safe: yes
- SL-2 — Gateway tool audit, ordering, and health diagnostics; Depends on: SL-0, SL-1; Blocks: SL-5, SL-6; Parallel-safe: mixed
- SL-3 — Streamable HTTP header and trace compatibility; Depends on: SL-0; Blocks: SL-5, SL-6; Parallel-safe: yes
- SL-4 — MCP server surface ordering and routing checks; Depends on: SL-0; Blocks: SL-6; Parallel-safe: yes
- SL-5 — CLI status and doctor diagnostics; Depends on: SL-2, SL-3; Blocks: SL-6; Parallel-safe: yes
- SL-6 — Documentation and phase review; Depends on: SL-1, SL-2, SL-3, SL-4, SL-5; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Observability Model Contract

- **Scope**: Freeze additive public and internal models for trace context, audit events, and gateway diagnostics.
- **Owned files**: `src/pmcp/types.py`
- **Interfaces provided**: `TraceContextInfo` or equivalent, `GatewayAuditEvent` or equivalent, `GatewayDiagnosticsInfo` or equivalent, additive fields on `HealthOutput` and/or `ServerHealthInfo` for gateway diagnostics
- **Interfaces consumed**: existing `AuthState`, `McpTaskInfo`, `PendingRequestInfo`, `ServerHealthInfo`, `HealthOutput`, `InvokeOutput`, `LifecycleServerOutput`, `RefreshOutput`
- **Parallel-safe**: no
- **Tasks**:
  - test: Define model-level serialization expectations in producer lane tests rather than adding broad type-only tests unless validation logic is non-trivial.
  - impl: Add optional fields only; do not rename or require existing health, invoke, refresh, pending, task, or lifecycle fields.
  - impl: Use simple JSON-safe public values for audit event categories, outcomes, transport names, and header compatibility states.
  - impl: Ensure trace context fields accept only strings and can be omitted independently.
  - impl: Ensure diagnostics models have no field intended for raw tokens, credentials, full request bodies, or unsanitized exception strings.
  - verify: `uv run ruff check src/pmcp/types.py`

### SL-1 — Deterministic Manager and Downstream Trace Context

- **Scope**: Make `ClientManager` collection snapshots deterministic and preserve explicit trace context through downstream calls where PMCP owns the request metadata.
- **Owned files**: `src/pmcp/client/manager.py`, `tests/test_client_manager.py`
- **Interfaces provided**: deterministic `get_all_tools()`, `get_all_resources()`, `get_all_prompts()`, `get_all_server_statuses()`, `get_pending_requests(...)`, `list_tasks(...)`; downstream trace metadata handoff used by SL-2
- **Interfaces consumed**: SL-0 trace context model, existing `ToolInfo.tool_id`, `ResourceInfo.resource_id`, `PromptInfo.prompt_id`, `ServerStatus.name`, `PendingRequest`, `McpTaskRecord`, Phase 1 `protocol_version`, Phase 2 task IDs
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add manager tests proving tools/resources/prompts/statuses are returned in stable identifier order regardless of insertion order.
  - test: Add pending-request and task-list ordering tests using mixed server names and request/task IDs.
  - test: Add trace-context coverage for downstream `tools/call` or task calls if the manager owns the final JSON-RPC params path.
  - impl: Sort snapshot getters by public stable keys; keep direct lookup APIs unchanged.
  - impl: Avoid changing relevance ranking in gateway search; provide deterministic input ordering for callers that do not request relevance.
  - impl: If trace context is passed through `_meta`, merge it without overwriting caller-provided non-trace metadata.
  - verify: `uv run pytest tests/test_client_manager.py -k "order or deterministic or trace or task or pending"`
  - verify: `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py`

### SL-2 — Gateway Tool Audit, Ordering, and Health Diagnostics

- **Scope**: Add structured audit production and gateway-level diagnostics to gateway tools while making catalog and status outputs deterministic.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: IF-0-OBSERVE-2 audit events, IF-0-OBSERVE-4 gateway ordering, health diagnostics for IF-0-OBSERVE-6, trace context extraction from tool inputs
- **Interfaces consumed**: SL-0 observability models, SL-1 deterministic manager snapshots and trace handoff, existing `_record_feedback_event(...)`, `_sanitize_error(...)`, `GatewayTools.health()`, `catalog_search(...)`, `describe(...)`, `invoke(...)`, lifecycle tools, pending/task tools, auth redaction helpers
- **Parallel-safe**: mixed
- **Tasks**:
  - test: Add audit-event shape tests for successful and failed `gateway.invoke`, lifecycle actions, refresh refusal/force, task result/cancel, and policy/auth failures.
  - test: Add redaction tests proving audit events omit credentials, auth URLs are sanitized, and errors are truncated through existing auth-safe helpers.
  - test: Add deterministic `catalog_search(include_offline=True)` coverage where live and cached tools are merged in stable `server::tool` order when no text query changes relevance.
  - test: Add health coverage for gateway diagnostics fields such as transport mode, audit buffer status, trace support, protocol version visibility, auth metadata presence, and rate-limit configuration presence without secret values.
  - impl: Add a bounded in-memory audit event buffer or hook on `GatewayTools`; do not introduce a database or external exporter.
  - impl: Emit audit events from public gateway action boundaries, including method/action, server/tool/task identifiers, protocol version when known, outcome, latency, and redacted error/auth state.
  - impl: Extract trace context from accepted input metadata and pass it to the manager where SL-1 supports propagation.
  - impl: Keep `gateway.catalog_search` relevance behavior for non-empty queries, but add deterministic tie-breakers by server and tool ID.
  - impl: Add gateway diagnostics to health additively; avoid changing existing per-server status semantics.
  - verify: `uv run pytest tests/test_tools.py -k "audit or trace or health or catalog or pending or task or lifecycle or refresh"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-3 — Streamable HTTP Header and Trace Compatibility

- **Scope**: Make the HTTP transport tolerant of stable/draft MCP headers and expose safe transport diagnostics.
- **Owned files**: `src/pmcp/transport/http.py`, `tests/test_http_transport.py`
- **Interfaces provided**: IF-0-OBSERVE-5 header compatibility, HTTP-side trace context capture, rate-limit/session/header diagnostics consumed by CLI/health paths through app state or a narrow accessor
- **Interfaces consumed**: SL-0 trace and diagnostics models, existing `/mcp`, `/health`, `/metrics`, auth-token guard, rate-limit counters, rmcp pre-session GET and initialized-notification compatibility paths, `normalize_auth_metadata(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add tests for accepting `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` headers on valid initialize, notification, and tool-call-shaped requests.
  - test: Add mismatch tests that are compatibility-safe: malformed or contradictory draft headers should produce diagnostics or 400 only where the request body can be inspected without breaking initialize/session manager behavior.
  - test: Add trace header tests for `traceparent`, `tracestate`, and `baggage` showing valid strings are captured and secrets are not logged.
  - test: Preserve existing tests for unauthenticated `/health` and `/metrics`, bearer auth on `/mcp`, rate limiting, request timeout, and rmcp workarounds.
  - impl: Parse draft headers case-insensitively and tolerate their absence for existing clients.
  - impl: Validate `Mcp-Method`/`Mcp-Name` against JSON-RPC body only after body replay handling is already needed and only for POST bodies that can be parsed safely.
  - impl: Add protocol/header/session/rate-limit diagnostic counters without logging raw authorization headers or request bodies.
  - impl: Do not require a specific OpenTelemetry SDK or backend.
  - verify: `uv run pytest tests/test_http_transport.py -k "header or trace or auth or rate or health or metrics or rmcp or timeout"`
  - verify: `uv run ruff check src/pmcp/transport/http.py tests/test_http_transport.py`

### SL-4 — MCP Server Surface Ordering and Routing Checks

- **Scope**: Keep PMCP's own MCP server-facing lists and call routing deterministic and compatible with the new observability contract.
- **Owned files**: `src/pmcp/server.py`, `tests/test_server.py`
- **Interfaces provided**: deterministic `list_tools`, resource list, and prompt list ordering; routing coverage for existing gateway tools after observability additions
- **Interfaces consumed**: SL-0 observability model availability, existing `get_gateway_tool_definitions()`, `ClientManager.get_all_resources()`, `ClientManager.get_all_prompts()`, policy filters, guidance resource behavior, task tool routing
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or update server tests proving PMCP gateway tools are listed in a stable order.
  - test: Add resource and prompt ordering coverage if existing fake manager tests can exercise those handlers without broad setup.
  - test: Keep task tool routing coverage intact after audit/diagnostic additions.
  - impl: Sort PMCP-owned MCP list responses by stable public fields where ordering is currently insertion-dependent.
  - impl: Do not change tool names, routing names, or call result JSON wrapping.
  - verify: `uv run pytest tests/test_server.py -k "list or route or task or status"`
  - verify: `uv run ruff check src/pmcp/server.py tests/test_server.py`

### SL-5 — CLI Status and Doctor Diagnostics

- **Scope**: Surface the new gateway/proxy diagnostics in CLI status and doctor output without leaking secrets.
- **Owned files**: `src/pmcp/cli.py`, `src/pmcp/cli_commands/doctor.py`, `tests/test_cli.py`
- **Interfaces provided**: IF-0-OBSERVE-6 CLI presentation and JSON pass-through, doctor checks for HTTP/session/header/auth/rate-limit diagnostics
- **Interfaces consumed**: SL-2 `gateway.health` diagnostics payload, SL-3 transport diagnostic shape, existing `_query_running_gateway_status(...)`, `_probe_http_health(...)`, `collect_remote_header_diagnostics(...)`, auth URL and credential redaction helpers
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add live `pmcp status --verbose` tests that render transport, protocol/header compatibility, trace support, audit status, auth metadata presence, and rate-limit information when present.
  - test: Add `pmcp status --json` tests proving health diagnostics pass through unchanged.
  - test: Add `pmcp doctor` tests for missing remote header env vars, protected-resource metadata hints, rate-limit visibility, and draft-header compatibility notes.
  - test: Add redaction assertions for bearer tokens, API keys, auth codes, query secrets, and credential-bearing URLs in status/doctor output.
  - impl: Keep normal non-verbose `pmcp status` compact.
  - impl: Prefer live gateway diagnostics; preserve local fallback behavior when the gateway is unreachable.
  - impl: Do not print raw headers, token values, request bodies, or user-specific trace baggage.
  - verify: `uv run pytest tests/test_cli.py -k "status or doctor or header or trace or auth or rate"`
  - verify: `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_cli.py`

### SL-6 — Documentation and Phase Review

- **Scope**: Document the observability contract and close the roadmap phase only after producer lanes are implemented and verified.
- **Owned files**: `README.md`, `SECURITY.md`, `CHANGELOG.md`, `specs/phase-plans-v3.md`
- **Interfaces provided**: user-facing observability, audit, trace, transport-header, and diagnostics documentation; completed Phase 4 checklist if implementation completes
- **Interfaces consumed**: SL-1 ordering behavior, SL-2 audit/health fields, SL-3 HTTP compatibility behavior, SL-4 MCP list ordering behavior, SL-5 CLI/doctor output, IF-0-OBSERVE-1 through IF-0-OBSERVE-6
- **Parallel-safe**: no
- **Tasks**:
  - test: Manually review docs for exact field names, redaction language, and draft-feature caveats.
  - impl: Document audit events as local/structured and bounded, not as an external exporter guarantee.
  - impl: Document trace context support as propagation/preservation, not a requirement for any OpenTelemetry backend.
  - impl: Document draft `Mcp-Method`/`Mcp-Name` handling as tolerant compatibility while `MCP-Protocol-Version` remains the stable HTTP version signal.
  - impl: Update SECURITY with what is redacted from audit/status/doctor output and why `/health` and `/metrics` stay unauthenticated.
  - impl: Add a CHANGELOG entry if this branch is release-bound.
  - impl: Mark Phase 4 exit criteria in `specs/phase-plans-v3.md` only after all producer lanes and whole-phase verification complete.
  - verify: `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_http_transport.py tests/test_cli.py tests/test_server.py -q`
  - verify: `uv run ruff check src/pmcp/client/manager.py src/pmcp/tools/handlers.py src/pmcp/transport/http.py src/pmcp/server.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_client_manager.py tests/test_tools.py tests/test_http_transport.py tests/test_cli.py tests/test_server.py`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_client_manager.py -k "order or deterministic or trace or task or pending"`
- `uv run pytest tests/test_tools.py -k "audit or trace or health or catalog or pending or task or lifecycle or refresh"`
- `uv run pytest tests/test_http_transport.py -k "header or trace or auth or rate or health or metrics or rmcp or timeout"`
- `uv run pytest tests/test_server.py -k "list or route or task or status"`
- `uv run pytest tests/test_cli.py -k "status or doctor or header or trace or auth or rate"`
- `uv run ruff check src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/tools/handlers.py src/pmcp/transport/http.py src/pmcp/server.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_client_manager.py tests/test_tools.py tests/test_http_transport.py tests/test_cli.py tests/test_server.py`

Whole-phase regression:

- `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_http_transport.py tests/test_cli.py tests/test_server.py -q`
- `uv run pytest tests/test_phase4_e2e.py tests/test_auth.py tests/test_policy.py tests/test_transport_http.py -q`
- `uv run ruff check src/ tests/`
- `uv run ruff format --check src/ tests/`
- `uv run mypy src/pmcp --exclude baml_client`
- `uv run pytest -q` before release handoff if time permits.

## Acceptance Criteria

- [ ] PMCP defines additive trace, audit, and gateway diagnostic models without changing existing required output fields.
- [ ] Gateway audit events capture method/action, target identity, protocol version, task ID, outcome, latency, and redacted error/auth details.
- [ ] Audit, status, doctor, and logs do not expose bearer tokens, API keys, auth codes, raw credential-bearing URLs, or raw request bodies.
- [ ] Trace context keys `traceparent`, `tracestate`, and `baggage` are preserved or propagated where PMCP has explicit metadata/header access.
- [ ] Tool/resource/prompt/server/pending/task outputs are deterministic when no explicit relevance ranking overrides ordering.
- [ ] `gateway.catalog_search` keeps relevance behavior for text queries while adding stable tie-breakers.
- [ ] Streamable HTTP accepts existing clients that omit draft headers and tolerates conforming clients that send `Mcp-Method`, `Mcp-Name`, and `MCP-Protocol-Version`.
- [ ] Draft header validation does not break current rmcp/Codex compatibility paths.
- [ ] `gateway.health` exposes safe gateway/proxy diagnostics for transport, session/header compatibility, auth metadata, rate-limit configuration, audit readiness, and trace support.
- [ ] `pmcp status --verbose`, `pmcp status --json`, and `pmcp doctor` surface the diagnostics needed to debug gateway/proxy behavior without secrets.
- [ ] Tests cover deterministic ordering, trace propagation, redaction, HTTP header compatibility, audit event shape, and CLI/doctor diagnostics.
- [ ] README, SECURITY, CHANGELOG, and `specs/phase-plans-v3.md` are updated only after implementation behavior and verification evidence are known.
