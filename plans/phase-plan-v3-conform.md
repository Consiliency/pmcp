# CONFORM: Protocol Conformance Soak and Release Gate

## Context

Phase 6 of `specs/phase-plans-v3.md` is the final release gate for the v3 protocol-current roadmap. Earlier phases have added negotiated MCP protocol versions and metadata preservation, MCP task brokering, auth and elicitation state reporting, gateway/proxy observability, and structured startup-policy/setup administration.

CONFORM should not introduce broad new runtime contracts. Its job is to prove the contracts work together across old and current MCP protocol behavior using deterministic local fakes, shared-service HTTP smoke tests, CLI/admin smoke, release notes, and full release verification. Live third-party servers and credentials remain out of scope; optional external MCP SDK conformance runs may be recorded as manual release evidence only if practical and stable.

## Interface Freeze Gates

- [x] IF-0-CONFORM-1 - The local conformance matrix covers at least one old-protocol stdio-style fake server and one current-protocol fake server, including `2024-11-05` and `2025-11-25` behavior where PMCP has explicit support.
- [x] IF-0-CONFORM-2 - Conformance tests cover modern tool/resource/prompt metadata preservation, MCP task-supported tools, auth and URL-mode elicitation states, deterministic ordering, trace/header behavior, and startup-policy mutation without external network access.
- [x] IF-0-CONFORM-3 - HTTP shared-service smoke exercises `/mcp`, `/health`, `/metrics`, bearer-auth boundaries, draft/stable header compatibility, rate limiting, and gateway diagnostics with local Starlette/TestClient utilities only.
- [x] IF-0-CONFORM-4 - Existing v2 shared-service lifecycle guarantees remain covered: lazy startup single-flight, refresh/lifecycle refusal by default, forced cancellation, pending-request visibility, task visibility, and no persistent config mutation from runtime lifecycle tools.
- [x] IF-0-CONFORM-5 - Release documentation states supported MCP protocol versions, task support limitations, auth/elicitation limitations, gateway/proxy observability behavior, startup-policy/admin behavior, and draft-feature compatibility flags.
- [x] IF-0-CONFORM-6 - Full release verification has named evidence for targeted conformance tests, full pytest suite, ruff lint, ruff format check, mypy, package build, and local smoke commands.

## Lane Index & Dependencies

- SL-0 - Client protocol and metadata conformance; Depends on: (none); Blocks: SL-1, SL-4, SL-5; Parallel-safe: yes
- SL-1 - Gateway task, auth, ordering, and config conformance; Depends on: SL-0; Blocks: SL-4, SL-5; Parallel-safe: mixed
- SL-2 - HTTP shared-service conformance smoke; Depends on: (none); Blocks: SL-4, SL-5; Parallel-safe: yes
- SL-3 - CLI and MCP server surface smoke; Depends on: SL-1; Blocks: SL-4, SL-5; Parallel-safe: yes
- SL-4 - End-to-end release soak; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: SL-5; Parallel-safe: no
- SL-5 - Release documentation and roadmap closeout; Depends on: SL-0, SL-1, SL-2, SL-3, SL-4; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Client Protocol and Metadata Conformance

- **Scope**: Prove `ClientManager` negotiates old/current protocol versions and preserves modern metadata through deterministic fake downstream clients.
- **Owned files**: `tests/test_client_manager.py`
- **Interfaces provided**: old/current protocol conformance evidence, metadata preservation evidence, task-capability discovery evidence
- **Interfaces consumed**: IF-0-PROTO-1, existing `ClientManager._send_initialize(...)`, `ToolInfo`, `ResourceInfo`, `PromptInfo`, `ServerStatus.protocol_version`, `server_capabilities`, `execution.taskSupport`, schema dialect and raw metadata preservation fields
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or consolidate a `conformance`-named test set proving initialization handles `2024-11-05`, `2025-03-26`, `2025-06-18`, and `2025-11-25` protocol responses without losing status visibility.
  - test: Cover one old-protocol stdio-style fake server payload with minimal tools/resources/prompts and one current-protocol fake payload with `title`, `icons`, `outputSchema`, `annotations`, `execution.taskSupport`, schema dialect, and unknown additive metadata.
  - test: Prove required-task tools fail when server capabilities do not advertise tasks and optional-task tools preserve downstream task metadata when capabilities do advertise tasks.
  - test: Prove trace context merged through `_meta` does not overwrite unrelated caller metadata.
  - impl: Prefer expanding existing fake-manager and mocked `_send_request` tests over adding new production APIs.
  - impl: Keep old-protocol behavior backward compatible; do not require current metadata fields on old fake payloads.
  - verify: `uv run pytest tests/test_client_manager.py -k "conformance or protocol or metadata or task or trace"`
  - verify: `uv run ruff check tests/test_client_manager.py`

### SL-1 - Gateway Task, Auth, Ordering, and Config Conformance

- **Scope**: Prove gateway tools expose the completed protocol, task, auth, ordering, observability, and config-admin contracts coherently.
- **Owned files**: `tests/test_tools.py`
- **Interfaces provided**: gateway conformance evidence for task tools, auth/elicitation states, deterministic catalog/health output, audit redaction, and startup-policy admin tools
- **Interfaces consumed**: SL-0 fake protocol/tool metadata behavior, IF-0-TASKS-2, IF-0-AUTH-3, IF-0-OBSERVE-4, IF-0-CONFIG-5, `GatewayTools.invoke(...)`, task gateway methods, `catalog_search(...)`, `describe(...)`, `health(...)`, `config_status(...)`, `get_startup_policy(...)`, `set_startup_policy(...)`, audit event and redaction helpers
- **Parallel-safe**: mixed
- **Tasks**:
  - test: Add gateway-level conformance tests that route a current-protocol task-capable tool through `gateway.invoke`, `gateway.tasks_list`, `gateway.tasks_result`, and `gateway.tasks_cancel`.
  - test: Add auth/elicitation conformance tests proving `missing_auth`, `insufficient_scope`, `elicitation_required`, and `policy_denied` states remain structured and redacted in invoke/provision/health paths.
  - test: Add deterministic ordering assertions for merged live/cached `gateway.catalog_search`, task lists, pending requests, and health rows when no relevance ranking applies.
  - test: Add config-admin conformance coverage for read-only status, dry-run startup-policy preview, explicit apply, no-op behavior, stale diagnostics, and lifecycle tools not mutating `autoStart`.
  - test: Add audit/trace assertions showing recent audit events include method/action, target identity, protocol version or task ID when known, outcome, latency, and redacted error/auth details.
  - impl: Keep tests local and deterministic by extending existing `MockClientManager` and temporary config fixtures.
  - impl: Do not add live registry, package-manager, or credential dependencies to normal conformance coverage.
  - verify: `uv run pytest tests/test_tools.py -k "conformance or task or auth or elicitation or catalog or health or startup_policy or config_status or audit or trace"`
  - verify: `uv run ruff check tests/test_tools.py`

### SL-2 - HTTP Shared-Service Conformance Smoke

- **Scope**: Prove the Streamable HTTP shared-service surface remains compatible, authenticated where required, observable, and self-contained.
- **Owned files**: `tests/test_http_transport.py`, `tests/test_transport_http.py`
- **Interfaces provided**: HTTP conformance smoke for `/mcp`, `/health`, `/metrics`, auth guard, stable/draft headers, rate limits, diagnostics, and rmcp/Codex compatibility paths
- **Interfaces consumed**: IF-0-OBSERVE-4, existing `GatewayServer(...).create_app()`, `/mcp` route behavior, `/health` and `/metrics` route behavior, auth-token guard, rate-limit counters, request-timeout behavior, `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, `traceparent`, `tracestate`, and `baggage` header handling
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or consolidate local TestClient smoke that initializes through `/mcp` with a bearer token while `/health` and `/metrics` remain unauthenticated.
  - test: Cover clients that omit draft headers and clients that send `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, and trace context headers.
  - test: Cover malformed or contradictory draft headers only where existing body parsing allows compatibility-safe validation.
  - test: Cover rate-limit behavior and gateway diagnostic counters without asserting secret-bearing header values.
  - test: Preserve existing rmcp pre-session GET, initialized-notification, request-size, timeout, and auth-boundary tests.
  - impl: Keep smoke entirely local; do not open sockets or require external network access unless an existing test already does so under local loopback control.
  - impl: Do not introduce a mandatory OpenTelemetry SDK or external collector.
  - verify: `uv run pytest tests/test_http_transport.py tests/test_transport_http.py -k "conformance or smoke or mcp or health or metrics or auth or header or trace or rate or timeout or rmcp"`
  - verify: `uv run ruff check tests/test_http_transport.py tests/test_transport_http.py`

### SL-3 - CLI and MCP Server Surface Smoke

- **Scope**: Prove CLI status/doctor/setup/config commands and PMCP's own MCP server tool routing expose the conformance contract consistently.
- **Owned files**: `tests/test_cli.py`, `tests/test_server.py`, `tests/test_setup_command.py`
- **Interfaces provided**: CLI and server smoke evidence for status, doctor, setup profiles, config status, gateway tool listing, task routing, and lifecycle/config routing
- **Interfaces consumed**: SL-1 gateway tool contracts, existing `pmcp status`, `pmcp doctor`, `pmcp setup --profile`, CLI JSON/text renderers, `GatewayServer` tool/resource/prompt handlers, `get_gateway_tool_definitions()`, task and config-admin dispatch paths
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add CLI smoke coverage for `pmcp status --json`, `pmcp status --verbose`, `pmcp doctor`, setup profiles, and config status/policy commands using local fixtures.
  - test: Assert CLI output includes protocol version, task counts, auth state, gateway diagnostics, startup policy, and config source attribution where present.
  - test: Assert CLI and doctor redaction hides bearer tokens, API keys, auth codes, credential-bearing URLs, and raw trace baggage.
  - test: Add server smoke proving gateway tools list deterministically and route task/config/lifecycle tools without changing tool names or JSON wrapping.
  - impl: Prefer augmenting existing parse/render and handler tests; avoid shelling out except where subprocess CLI smoke is already established.
  - impl: Keep normal non-verbose status compact while preserving JSON pass-through for release evidence.
  - verify: `uv run pytest tests/test_cli.py tests/test_server.py tests/test_setup_command.py -k "conformance or status or doctor or setup or profile or config_status or startup_policy or protocol or task or route or list"`
  - verify: `uv run ruff check tests/test_cli.py tests/test_server.py tests/test_setup_command.py`

### SL-4 - End-to-End Release Soak

- **Scope**: Add a final local release-soak layer that exercises cross-surface behavior after the producer conformance lanes finish.
- **Owned files**: `tests/test_phase4_e2e.py`
- **Interfaces provided**: final local release-soak evidence for mixed protocol/task/gateway/admin/HTTP/CLI behavior
- **Interfaces consumed**: SL-0 protocol and metadata evidence, SL-1 gateway conformance, SL-2 HTTP smoke, SL-3 CLI/server smoke, v2 shared-service lifecycle guarantees, existing `_run_pmcp(...)` subprocess helper, local fake `ClientManager`, `GatewayTools`, and temporary config fixtures
- **Parallel-safe**: no
- **Tasks**:
  - test: Rename or extend the file's module docstring/comments as needed to reflect that it now holds v3 release-gate smoke, while preserving existing Phase 4 tests.
  - test: Add one mixed old/current fake-server smoke proving protocol status, modern metadata, task execution, auth state, trace/audit diagnostics, and startup-policy status can be observed through gateway outputs.
  - test: Add one local HTTP smoke if SL-2 coverage needs cross-surface confirmation, using local app/TestClient behavior rather than external network access.
  - test: Add one subprocess CLI smoke that covers status/doctor/setup/config behavior with temporary HOME/project directories and no credentials.
  - test: Include v2 lifecycle regression smoke for refused refresh/lifecycle during active pending work and active MCP tasks.
  - impl: Keep this lane as a reducer over producer behavior; do not define new production contracts here.
  - impl: If a smoke duplicates narrower lane coverage, keep the E2E assertion high-level and stable.
  - verify: `uv run pytest tests/test_phase4_e2e.py -k "phase4 or conformance or release or protocol or task or lifecycle or setup or doctor or config"`
  - verify: `uv run ruff check tests/test_phase4_e2e.py`

### SL-5 - Release Documentation and Roadmap Closeout

- **Scope**: Document release support and close the roadmap only after all conformance evidence and release commands are available.
- **Owned files**: `CHANGELOG.md`, `README.md`, `SECURITY.md`, `specs/phase-plans-v3.md`, `plans/phase-plan-v3-conform.md`
- **Interfaces provided**: release notes, supported-version statement, limitations, draft-feature compatibility flags, security/redaction notes, completed Phase 6 checklist, execution notes
- **Interfaces consumed**: SL-0 protocol/metadata findings, SL-1 gateway/task/auth/config findings, SL-2 HTTP/header/trace findings, SL-3 CLI/server findings, SL-4 end-to-end release-soak findings, IF-0-CONFORM-1 through IF-0-CONFORM-6
- **Parallel-safe**: no
- **Tasks**:
  - test: Manually review release docs for exact supported MCP protocol versions, task limitations, auth/elicitation limitations, observability behavior, setup/config behavior, and draft-feature caveats.
  - impl: Update CHANGELOG release notes with CONFORM results only after verification evidence is known.
  - impl: Update README supported-protocol/admin/observability sections if conformance uncovered behavior that needs clearer user guidance.
  - impl: Update SECURITY only for release-relevant auth, trace, audit, status, doctor, or HTTP exposure caveats discovered during conformance.
  - impl: Mark Phase 6 exit criteria in `specs/phase-plans-v3.md` complete only after producer lanes and whole-phase verification pass.
  - impl: Mark this plan's interface gates and acceptance criteria complete and record any execution deviations.
  - verify: `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_http_transport.py tests/test_transport_http.py tests/test_cli.py tests/test_server.py tests/test_setup_command.py tests/test_phase4_e2e.py -q`
  - verify: Manually review markdown formatting in `CHANGELOG.md`, `README.md`, `SECURITY.md`, `specs/phase-plans-v3.md`, and `plans/phase-plan-v3-conform.md`.

## Verification

Lane-specific verification:

- `uv run pytest tests/test_client_manager.py -k "conformance or protocol or metadata or task or trace"`
- `uv run ruff check tests/test_client_manager.py`
- `uv run pytest tests/test_tools.py -k "conformance or task or auth or elicitation or catalog or health or startup_policy or config_status or audit or trace"`
- `uv run ruff check tests/test_tools.py`
- `uv run pytest tests/test_http_transport.py tests/test_transport_http.py -k "conformance or smoke or mcp or health or metrics or auth or header or trace or rate or timeout or rmcp"`
- `uv run ruff check tests/test_http_transport.py tests/test_transport_http.py`
- `uv run pytest tests/test_cli.py tests/test_server.py tests/test_setup_command.py -k "conformance or status or doctor or setup or profile or config_status or startup_policy or protocol or task or route or list"`
- `uv run ruff check tests/test_cli.py tests/test_server.py tests/test_setup_command.py`
- `uv run pytest tests/test_phase4_e2e.py -k "phase4 or conformance or release or protocol or task or lifecycle or setup or doctor or config"`
- `uv run ruff check tests/test_phase4_e2e.py`

Whole-phase regression:

- `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_http_transport.py tests/test_transport_http.py tests/test_cli.py tests/test_server.py tests/test_setup_command.py tests/test_phase4_e2e.py -q`
- `uv run pytest tests/test_auth.py tests/test_policy.py tests/test_config_loader.py tests/test_startup_resolver.py tests/test_manifest.py tests/test_refresher.py tests/test_manifest_provision.py tests/test_lazy_start.py tests/test_server_lifecycle.py tests/test_secrets_command.py -q`
- `uv run ruff check src/ tests/`
- `uv run ruff format --check src/ tests/`
- `uv run mypy src/pmcp --exclude baml_client`
- `uv build`
- `uv run pytest -q`

Manual smoke after implementation:

- `pmcp status --json --pending`
- `pmcp status --verbose --pending`
- `pmcp doctor`
- `pmcp setup --profile local-stdio`
- `pmcp setup --profile shared-local-http`
- `pmcp setup --profile authenticated-shared-http`
- `pmcp setup --profile ci`

Optional release evidence:

- Run external MCP SDK conformance tests only if they are stable, local, and practical for this release; record them as manual evidence, not as a mandatory CI gate.

## Acceptance Criteria

- [x] Conformance tests include at least one old-protocol stdio-style fake server and one current-protocol fake server.
- [x] Tests cover `2024-11-05`, `2025-03-26`, `2025-06-18`, and `2025-11-25` protocol status handling where PMCP claims compatibility.
- [x] Tests cover modern metadata preservation for tools, resources, prompts, schema dialects, task hints, and unknown additive fields.
- [x] Tests cover task-supported tool invocation, task list/get/result/cancel behavior, required-task capability refusal, and active-task lifecycle refusal/force semantics.
- [x] Tests cover structured auth and elicitation states without leaking credentials or auth-bearing URLs.
- [x] Tests cover deterministic gateway catalog, health, pending, task, tool, resource, and prompt ordering where no explicit relevance ranking applies.
- [x] Tests cover trace context and stable/draft HTTP header compatibility while preserving existing clients that omit draft headers.
- [x] HTTP smoke covers shared-service `/mcp`, `/health`, `/metrics`, bearer auth, rate limiting, diagnostics, and rmcp/Codex compatibility paths without external network access.
- [x] CLI smoke covers status, doctor, setup profiles, config status, and startup-policy views with JSON and human-readable output where applicable.
- [x] Existing v2 shared-service lifecycle semantics remain covered and are not weakened by protocol-current behavior.
- [x] Release notes document supported MCP protocol versions, task support limitations, auth limitations, gateway/proxy observability behavior, startup-policy/admin behavior, and draft-feature compatibility flags.
- [x] Full test suite, lint, format check, mypy, build, and local smoke commands have recorded passing evidence before Phase 6 is marked complete.

## Execution Evidence

- SL-0: `uv run pytest tests/test_client_manager.py -k "conformance or protocol or metadata or task or trace" -q` passed with 21 tests.
- SL-1: `uv run pytest tests/test_tools.py -k "conformance or task or auth or elicitation or catalog or health or startup_policy or config_status or audit or trace" -q` passed with 34 tests.
- SL-2: `uv run pytest tests/test_http_transport.py tests/test_transport_http.py -k "conformance or smoke or mcp or health or metrics or auth or header or trace or rate or timeout or rmcp" -q` passed with 35 tests.
- SL-3: `uv run pytest tests/test_cli.py tests/test_server.py tests/test_setup_command.py -k "conformance or status or doctor or setup or profile or config_status or startup_policy or protocol or task or route or list" -q` passed with 54 tests.
- SL-4: `uv run pytest tests/test_phase4_e2e.py -k "phase4 or conformance or release or protocol or task or lifecycle or setup or doctor or config" -q` passed with 7 tests.
- Phase regression: `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_http_transport.py tests/test_transport_http.py tests/test_cli.py tests/test_server.py tests/test_setup_command.py tests/test_phase4_e2e.py -q` passed with 350 tests.
- Broader shared-service regression: `uv run pytest tests/test_auth.py tests/test_policy.py tests/test_config_loader.py tests/test_startup_resolver.py tests/test_manifest.py tests/test_refresher.py tests/test_manifest_provision.py tests/test_lazy_start.py tests/test_server_lifecycle.py tests/test_secrets_command.py -q` passed with 1030 passed, 1 skipped, 16 deselected, and one pre-existing unknown `pytest.mark.timeout` warning.
- Static/release checks: `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/pmcp --exclude baml_client`, and `uv build` passed.
- Full suite: `uv run pytest -q` passed with 1659 passed, 12 skipped, 21 deselected, and one pre-existing unknown `pytest.mark.timeout` warning.
- Local smoke: `uv run pmcp status --json --pending`, `uv run pmcp status --verbose --pending`, `uv run pmcp doctor`, and `uv run pmcp setup --profile local-stdio|shared-local-http|authenticated-shared-http|ci` returned 0. `pmcp doctor` reported a non-fatal stale lock warning and successful HTTP health reachability.
- External MCP SDK conformance was not run; it remains optional manual evidence and is not a mandatory local release gate.
