# SOAK: Multi-Client Soak and Release Gate

## Context

Phase 1 serialized `ClientManager` lifecycle mutation and same-server startup. Phase 2 froze `gateway.refresh` in-flight behavior: refresh refuses pending work by default and cancels only with `force=true`. Phase 3 added runtime-only lifecycle controls with the same target-server pending-request policy. Phase 4 documented the HTTP shared-service operational contract, including unauthenticated `/health` and `/metrics`, bearer auth on `/mcp`, per-IP rate limiting, and CLI status/doctor visibility.

Phase 5 is the release soak phase. It should not introduce new public gateway tools or change payload shapes. The work should add bounded, deterministic multi-client stress and regression coverage across the already-frozen shared-service paths, then produce a final release-readiness record. Small fixes are allowed only when a soak test exposes a bug inside the frozen contracts.

The current test suite already has focused coverage for same-server single-flight connection attempts, refresh refusal/cancellation, lifecycle stop/restart behavior, live `pmcp status` snapshots, and HTTP health/metrics/auth/rate-limit contracts. Current gaps are cross-operation soak scenarios: simultaneous invokes against one lazy server, refresh or lifecycle mutation while another client has active work, status/health/pending visibility during those shared operations, and release-gate evidence tying test/lint/type/build/smoke results to residual risk documentation.

## Interface Freeze Gates

- [x] IF-0-SOAK-1 — Bounded concurrency tests simulate two or more clients invoking the same lazy downstream server and prove at most one downstream local process or remote connection attempt is created for the single-flight path.
- [x] IF-0-SOAK-2 — Bounded concurrency tests simulate `gateway.refresh` and `gateway.disconnect_server` / `gateway.restart_server` while another client has an active request, proving default refusal preserves active work and `force=true` cancels only the documented scope.
- [x] IF-0-SOAK-3 — `gateway.health`, `gateway.list_pending`, and live `pmcp status --json` / human status views remain accurate before, during, and after shared refresh/lifecycle operations.
- [x] IF-0-SOAK-4 — At least one HTTP transport smoke path covers the shared-service surface without external network access, using the existing Starlette test client or local app utilities.
- [x] IF-0-SOAK-5 — Multi-client stress and regression tests cover concurrent lazy-start, invoke, refresh, provision/connect, and status/pending visibility without adding heavy load-test infrastructure.
- [x] IF-0-SOAK-6 — Release gate records full test suite, lint, format, type, build, and smoke command results, and documents any residual shared-service risks in `CHANGELOG.md`, `README.md`, or `specs/phase-plans-v2.md` before release.

## Lane Index & Dependencies

- SL-0 — Soak contract and fixture budget; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 — Manager concurrency soak; Depends on: SL-0; Blocks: SL-4; Parallel-safe: yes
- SL-2 — Gateway operation soak; Depends on: SL-0; Blocks: SL-3, SL-4; Parallel-safe: yes
- SL-3 — HTTP and CLI visibility smoke; Depends on: SL-0, SL-2; Blocks: SL-4; Parallel-safe: yes
- SL-4 — Release gate and residual risk synthesis; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Soak Contract and Fixture Budget

- **Scope**: Freeze the deterministic soak scenarios, concurrency bounds, and release-gate command list before adding tests.
- **Owned files**: `plans/phase-plan-v2-soak.md`
- **Interfaces provided**: soak concurrency budget of small `asyncio.gather(...)` groups, no external MCP services, no long sleeps, release-gate command list, residual-risk documentation rule
- **Interfaces consumed**: IF-0-SERIALIZE-1, IF-0-REFRESH-2, IF-0-LIFECYCLE-3, IF-0-OPS-4, existing pytest/ruff/mypy/build commands from `specs/phase-plans-v2.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Defer concrete assertions to SL-1, SL-2, and SL-3 because those lanes own the test files.
  - impl: Use deterministic `asyncio.Event`, fake managers, mocked subprocess/remote connectors, and Starlette `TestClient` utilities rather than real sleeps or external servers.
  - impl: Keep stress sizes CI-bounded, such as two to five concurrent tasks per scenario unless an existing helper already uses a larger safe bound.
  - impl: Treat any source edit discovered by soak as a contract-preserving fix only; do not add public fields, tools, command flags, or auth/rate-limit policy changes.
  - verify: Review this plan for lane file ownership and acyclic dependencies before implementation starts.

### SL-1 — Manager Concurrency Soak

- **Scope**: Add direct `ClientManager` concurrency tests for lazy single-flight, invoke pending state, refresh/lifecycle interference, and duplicate downstream connection prevention.
- **Owned files**: `src/pmcp/client/manager.py`, `tests/test_client_manager.py`
- **Interfaces provided**: manager-level soak coverage for IF-0-SOAK-1, IF-0-SOAK-2, and the single-flight portions of IF-0-SOAK-5
- **Interfaces consumed**: existing `_connect_singleflight(...)`, `connect_all(...)`, `connect_server(...)`, `ensure_connected(...)`, `call_tool(...)`, `refresh(...)`, `disconnect_server(...)`, `restart_server(...)`, `get_pending_requests(...)`, `cancel_all_pending_requests()`, `cancel_pending_requests(server)`, `ManagedClient.pending_requests`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a bounded same-lazy-server invoke test where multiple simulated clients call through one lazy server concurrently and the mocked `_connect_server` / downstream connection path is awaited exactly once.
  - test: Add a same-server local process or remote transport single-flight test that counts mocked subprocess or streamable HTTP connection creation and proves concurrent connect/provision paths create only one downstream connection.
  - test: Add a refresh-while-active-request test using a pending future and `asyncio.Event` gates proving default refresh leaves the active request registered and does not disconnect/reconnect underneath it.
  - test: Add a force-refresh or force-disconnect test proving only the documented pending requests are cancelled and unrelated server pending requests remain visible.
  - test: Add a snapshot visibility assertion around the active operation proving `get_pending_requests()` reports stable request IDs and states during the soak.
  - impl: If tests expose a race in manager registry mutation, narrow the fix to lock boundaries, task snapshotting, or pending-request bookkeeping inside `ClientManager`.
  - impl: Do not hold global lifecycle locks across normal downstream tool response waits unless the failing test proves the existing contract cannot be preserved otherwise.
  - verify: `uv run pytest tests/test_client_manager.py -k "soak or concurrent or singleflight or pending or refresh or disconnect_server or restart_server"`
  - verify: `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py`

### SL-2 — Gateway Operation Soak

- **Scope**: Add gateway-facing soak tests for concurrent invoke, provision/connect, refresh, lifecycle controls, health, and pending visibility through `GatewayTools`.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: public gateway regression coverage for IF-0-SOAK-1, IF-0-SOAK-2, IF-0-SOAK-3, and IF-0-SOAK-5
- **Interfaces consumed**: `GatewayTools.invoke(...)`, `provision(...)`, `refresh(...)`, `connect_server(...)`, `disconnect_server(...)`, `restart_server(...)`, `health(...)`, `list_pending(...)`, `MockClientManager`, refresh/lifecycle output counters and `cancelled_request_count`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Extend `MockClientManager` only as needed to model blocked active requests, same-server single-flight results, connection-attempt counts, and pending-request state transitions.
  - test: Add a concurrent invoke/provision test for a configured lazy server proving one caller starts the server and the other observes either the running server or the shared result without a duplicate connect.
  - test: Add a default `gateway.refresh` while pending test proving output reports `pending_requests_seen` / `pending_requests_refused`, pending visibility remains intact, and no disconnect/register/connect lifecycle events run.
  - test: Add forced `gateway.refresh` and forced `gateway.disconnect_server` / `gateway.restart_server` tests proving cancellation counts and target/all-server cancellation scopes match Phases 2 and 3.
  - test: Add a health/list-pending sequence around shared operations proving server status, startup policy fields, revision metadata, and pending request IDs remain visible and coherent.
  - impl: If tests expose a gateway handler bug, narrow the fix to sequencing, output counter population, or mock-compatible state observation inside `GatewayTools`.
  - impl: Preserve all existing tool names, required fields, and output model shapes.
  - verify: `uv run pytest tests/test_tools.py -k "soak or concurrent or provision or refresh or pending or health or connect_server or disconnect_server or restart_server"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-3 — HTTP and CLI Visibility Smoke

- **Scope**: Add at least one local HTTP shared-service smoke path and CLI visibility checks that consume gateway health/pending state without starting external services.
- **Owned files**: `src/pmcp/transport/http.py`, `src/pmcp/cli.py`, `tests/test_http_transport.py`, `tests/test_cli.py`
- **Interfaces provided**: HTTP smoke coverage for IF-0-SOAK-3 and IF-0-SOAK-4, CLI visibility coverage for live status pending/health snapshots
- **Interfaces consumed**: SL-2 health and pending semantics, existing `create_http_app(...)`, unauthenticated `/health`, unauthenticated `/metrics`, authenticated `/mcp`, `_query_running_gateway_status(...)`, `run_status(...)`, `_get_gateway_health_url()`, `_probe_http_health(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or extend an HTTP transport smoke test using `TestClient` that exercises `/health` and `/metrics` while the mock MCP app remains local and deterministic.
  - test: If practical with existing session-manager mocks, add a minimal `/mcp` authenticated request smoke assertion that does not depend on external MCP servers or network access.
  - test: Add a live `pmcp status --json --pending` fixture proving shared pending requests and server health fields from a gateway snapshot are passed through during a simulated active operation.
  - test: Add a human `pmcp status --pending` or verbose status assertion proving pending and downstream lifecycle labels remain understandable after SL-2's shared-operation states.
  - impl: If tests expose a visibility bug, narrow the fix to HTTP route plumbing or CLI rendering; do not authenticate `/health` or `/metrics` and do not reshape JSON status.
  - verify: `uv run pytest tests/test_http_transport.py -k "health or metrics or auth or mcp or smoke"`
  - verify: `uv run pytest tests/test_cli.py -k "status or pending or doctor or health"`
  - verify: `uv run ruff check src/pmcp/transport/http.py src/pmcp/cli.py tests/test_http_transport.py tests/test_cli.py`

### SL-4 — Release Gate and Residual Risk Synthesis

- **Scope**: Run and record the release-readiness gate, update residual risk documentation, and mark SOAK complete only after all producer lanes are verified.
- **Owned files**: `CHANGELOG.md`, `README.md`, `specs/phase-plans-v2.md`, `plans/phase-plan-v2-soak.md`
- **Interfaces provided**: final release gate evidence, residual shared-service risk record, completed Phase 5 checklist, completed SOAK acceptance checklist
- **Interfaces consumed**: SL-1 manager soak results, SL-2 gateway soak results, SL-3 HTTP/CLI smoke results, IF-0-SOAK-1 through IF-0-SOAK-6, roadmap release-readiness command list
- **Parallel-safe**: no
- **Tasks**:
  - impl: Add a concise CHANGELOG entry under Unreleased summarizing the multi-client soak/release-gate coverage if the branch is release-bound.
  - impl: Document any known residual shared-service risks in `CHANGELOG.md`, `README.md`, or Phase 5 execution notes in `specs/phase-plans-v2.md`; explicitly record "no known residual shared-service risks" only if verification supports that.
  - impl: Mark Phase 5 exit criteria in `specs/phase-plans-v2.md` only after lane verification and release-gate commands complete.
  - impl: Mark this plan's acceptance criteria complete and add execution notes with command results and any deviations.
  - verify: `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_lazy_start.py tests/test_http_transport.py tests/test_cli.py -q`
  - verify: `uv run pytest tests/test_server_lifecycle.py tests/test_transport_http.py tests/test_secrets_command.py -q`
  - verify: `uv run pytest -q`
  - verify: `uv run ruff check src/ tests/`
  - verify: `uv run ruff format --check src/ tests/`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `uv build`
  - verify: `pmcp status --json`
  - verify: `pmcp status --verbose`
  - verify: `pmcp setup --client claude --mode http`
  - verify: `pmcp setup --client opencode --mode http`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_client_manager.py -k "soak or concurrent or singleflight or pending or refresh or disconnect_server or restart_server"`
- `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py`
- `uv run pytest tests/test_tools.py -k "soak or concurrent or provision or refresh or pending or health or connect_server or disconnect_server or restart_server"`
- `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`
- `uv run pytest tests/test_http_transport.py -k "health or metrics or auth or mcp or smoke"`
- `uv run pytest tests/test_cli.py -k "status or pending or doctor or health"`
- `uv run ruff check src/pmcp/transport/http.py src/pmcp/cli.py tests/test_http_transport.py tests/test_cli.py`

Whole-phase regression:

- `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_lazy_start.py tests/test_http_transport.py tests/test_cli.py -q`
- `uv run pytest tests/test_server_lifecycle.py tests/test_transport_http.py tests/test_secrets_command.py -q`
- `uv run pytest -q`
- `uv run ruff check src/ tests/`
- `uv run ruff format --check src/ tests/`
- `uv run mypy src/pmcp --exclude baml_client`
- `uv build`

Manual/local smoke after implementation:

- `pmcp status --json`
- `pmcp status --verbose`
- `pmcp setup --client claude --mode http`
- `pmcp setup --client opencode --mode http`

## Acceptance Criteria

- [x] Tests simulate multiple clients invoking the same lazy server concurrently and prove one downstream connection attempt is shared.
- [x] Tests simulate one client refreshing a gateway while another has an active request, covering both default refusal and forced cancellation semantics.
- [x] Tests simulate one client stopping or restarting a server while another has an active request, covering default refusal and target-only forced cancellation.
- [x] Tests verify `gateway.health`, `gateway.list_pending`, and live `pmcp status` visibility across shared operations.
- [x] Tests verify no duplicate downstream process or remote connection is created for same-server lazy-start, invoke, provision, or connect paths.
- [x] At least one HTTP transport smoke path runs locally without external MCP services or network access.
- [x] Full test suite, ruff lint, ruff format check, mypy, build, and local smoke commands pass.
- [x] Known residual shared-service risks are documented in release notes, README, or the roadmap before release.

## Execution Notes

SOAK completed with deterministic manager, gateway, HTTP, and CLI tests. No
production source changes were required by the soak assertions.

Release gate summary:

- Full pytest passed: 1595 passed, 12 skipped, 21 deselected.
- Ruff lint passed for `src/` and `tests/`.
- Mypy passed for `src/pmcp --exclude baml_client`.
- `uv build` passed and produced `dist/pmcp-1.9.4.tar.gz` and
  `dist/pmcp-1.9.4-py3-none-any.whl`.
- `pmcp status --json`, `pmcp status --verbose`,
  `pmcp setup --client claude --mode http`, and
  `pmcp setup --client opencode --mode http` passed.
- Full `uv run ruff format --check src/ tests/` passed after cleaning
  pre-existing formatting drift.

No known residual shared-service correctness risks remain from the covered
SOAK scenarios.
