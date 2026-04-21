# Phase roadmap v2

## Context

PMCP now supports a user-owned startup policy where downstream servers are lazy by default, explicit `autoStart` entries connect eagerly, and `gateway.health` / `pmcp status --verbose` expose startup policy observations. The remaining pre-release concern is shared-service hardening: one PMCP HTTP gateway may be used by multiple clients and client types at the same time.

The current shared-service shape is broadly correct: PMCP has a singleton gateway lock, streamable HTTP transport with session state, optional bearer auth, request timeouts, body-size checks, per-IP rate limiting, pending-request tracking, and a cap on concurrent local process spawns. The audit found that the main risk is not multi-client HTTP session support itself; it is global downstream server lifecycle state being mutated concurrently by lazy start, provision, refresh, reconnect, and future lifecycle commands.

## Architecture North Star

PMCP should behave as one shared gateway process with shared downstream server state. Multiple clients can discover, invoke, provision, refresh, and inspect that shared state without duplicate downstream spawns, dictionary mutation races, stale tool indexes, or ambiguous cancellation behavior. Lifecycle-changing operations should be serialized where needed, per-server startup should be single-flight, and disruptive operations should expose explicit semantics rather than surprising other clients.

## Assumptions

- HTTP shared-service mode is the preferred mode for multiple simultaneous clients.
- Stdio mode remains a single-process local testing mode and does not need cross-client coordination.
- Downstream MCP servers remain shared across clients; this roadmap does not introduce per-client server sandboxes.
- `gateway.refresh` is allowed to be disruptive, but the disruption must be intentional, observable, and testable.
- Existing tool IDs, health output, provisioning output, and startup policy fields remain backward compatible unless this roadmap explicitly freezes an additive field.
- Localhost clients usually share one source IP, so per-IP rate limiting can affect multiple local clients together.
- Future natural-language settings/configuration tools should be built only after explicit lifecycle behavior is hardened.

## Non-Goals

- Do not add a broad natural-language `gateway.settings` tool in this roadmap.
- Do not add per-client downstream server isolation or per-client credentials.
- Do not change the MCP transport protocol or replace streamable HTTP.
- Do not implement idle timeout shutdown unless it naturally falls out of explicit lifecycle controls.
- Do not persist startup policy changes to `.mcp.json` in this roadmap.
- Do not change secret storage semantics beyond documenting shared-service implications.

## Cross-Cutting Principles

- Single-flight by server name: two clients asking for the same lazy server should create at most one downstream connection attempt.
- Serialize global lifecycle mutation: refresh, disconnect, reconnect, provision-connect, and future stop/start commands must not interleave destructively.
- Make disruption explicit: operations that cancel other clients' pending requests should say so in tool output and logs.
- Preserve read concurrency: catalog, health, status, pending-list, and normal invokes should remain concurrent unless a lifecycle mutation is actively changing shared state.
- Keep operational controls structured first; natural-language wrappers can come later after stable primitives exist.
- Prefer additive public fields and narrow tests over broad API reshaping before release.

## Top Interface-Freeze Gates

- IF-0-SERIALIZE-1 — `ClientManager` exposes deterministic concurrency semantics for per-server startup and global lifecycle mutation without changing public tool payloads.
- IF-0-REFRESH-2 — `gateway.refresh` has explicit in-flight request behavior, including force/drain/cancel semantics or a documented fixed policy.
- IF-0-LIFECYCLE-3 — Optional lifecycle tools use structured inputs and outputs for starting, stopping, and restarting downstream servers without persistent config mutation.
- IF-0-OPS-4 — Shared-service operational guidance documents multi-client mode, rate-limit implications, auth expectations, and lifecycle disruption behavior.
- IF-0-SOAK-5 — Multi-client stress and regression tests cover concurrent lazy-start, invoke, refresh, provision/connect, and status/pending visibility.

## Phases

### Phase 1 — Shared State Serialization (SERIALIZE)

**Objective**

Harden `ClientManager` so concurrent clients cannot duplicate lazy starts, mutate connection dictionaries during iteration, or interleave per-server connect/reconnect operations destructively.

**Exit criteria**

- [x] Concurrent `ensure_connected("same-server")` calls result in at most one downstream connect attempt.
- [x] Concurrent provision/connect calls for the same server are single-flight or return an already-running result after the first succeeds.
- [x] `disconnect_all()` iterates stable snapshots and cannot fail because `_clients` changes during iteration.
- [x] Global lifecycle mutation has a clear lock boundary covering refresh/disconnect/reconnect state changes.
- [x] Read-only status/catalog methods keep returning snapshots without exposing mutable internal collections.
- [x] Tests cover same-server lazy-start races, same-server connect races, and disconnect during concurrent state mutation.

**Scope notes**

- Likely add a manager-level lifecycle lock plus per-server locks or per-server in-flight connection tasks.
- Keep the public `ClientManager` method names stable unless a new private helper clarifies lock ownership.
- Ensure `_tools`, `_resources`, `_prompts`, `_servers`, `_clients`, and `_lazy_configs` are updated coherently under lifecycle mutation.
- Preserve the existing concurrent spawn semaphore as a resource cap; it is not a replacement for per-server single-flight.
- Avoid holding lifecycle locks while waiting on long-running downstream tool calls unless necessary.

**Non-goals**

- Do not add user-facing lifecycle tools in this phase.
- Do not change `gateway.refresh` output shape yet.
- Do not add persistent configuration mutation.

**Key files**

- `src/pmcp/client/manager.py`
- `tests/test_client_manager.py`
- `tests/test_lazy_start.py`
- `tests/test_tools.py`

**Depends on**

- (none)

**Produces**

- IF-0-SERIALIZE-1 — `ClientManager` exposes deterministic concurrency semantics for per-server startup and global lifecycle mutation without changing public tool payloads.

### Phase 2 — Refresh and In-Flight Semantics (REFRESH)

**Objective**

Make `gateway.refresh` behavior explicit and safe when other clients have active invokes, describes, reads, prompts, lazy starts, or provisioning-related connections in flight.

**Exit criteria**

- [x] `gateway.refresh` cannot corrupt manager state when another client is invoking a downstream tool.
- [x] Pending requests affected by refresh are either allowed to drain or intentionally cancelled according to an explicit policy.
- [x] Refresh output and logs communicate when pending requests were cancelled, refused, or waited on.
- [x] `gateway.list_pending` remains accurate before, during, and after refresh.
- [x] Refresh replaces startup observations only after successful resolver classification and does not leave stale partial observations on failure.
- [x] Tests cover refresh racing with an in-flight tool call and refresh racing with lazy-start.

**Scope notes**

- Decide during phase planning whether refresh should default to "cancel active requests" or "refuse unless force/drain option is set"; freeze that choice before implementation.
- If adding fields to `RefreshInput` or `RefreshOutput`, keep them additive and optional.
- Coordinate with Phase 1 lock ownership so refresh does not deadlock while cancelling or waiting for pending requests.
- Keep startup policy behavior from v1 unchanged.

**Non-goals**

- Do not add per-client request ownership.
- Do not add a UI prompt or interactive confirmation flow.
- Do not change pending request IDs.

**Key files**

- `src/pmcp/types.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/client/manager.py`
- `tests/test_tools.py`
- `tests/test_client_manager.py`

**Depends on**

- IF-0-SERIALIZE-1

**Produces**

- IF-0-REFRESH-2 — `gateway.refresh` has explicit in-flight request behavior, including force/drain/cancel semantics or a documented fixed policy.

### Phase 3 — Structured Lifecycle Controls (LIFECYCLE)

**Objective**

Add narrow, structured gateway tools for runtime downstream server lifecycle control so clients can start, stop, and restart shared servers without broad natural-language settings mutation.

**Exit criteria**

- [x] A client can explicitly start/connect a configured, manifest, or provisioned server by name without duplicate spawns.
- [x] A client can explicitly stop/disconnect a running downstream server and free resources.
- [x] A client can restart a downstream server with clear behavior for active pending requests.
- [x] Lifecycle tool outputs include server name, prior status, new status, cancelled request count when applicable, and errors when startup/stop fails.
- [x] Health/status output reflects stopped servers without losing startup policy observations where relevant.
- [x] Tests cover start, stop, restart, unknown server, policy-denied server, missing-auth server, and active-request stop behavior.

**Scope notes**

- Candidate tools: `gateway.connect_server`, `gateway.disconnect_server`, and `gateway.restart_server`.
- Reuse Phase 1 and Phase 2 lifecycle locks and request cancellation semantics.
- Use structured inputs with explicit `force` where a command may cancel active work.
- Keep persistent `.mcp.json` startup policy changes out of scope; stopping a server is runtime-only.
- If names differ during phase planning, freeze final tool names before implementation.

**Non-goals**

- Do not add `gateway.settings` or free-form natural-language configuration.
- Do not edit `.mcp.json` or change `autoStart`.
- Do not add idle auto-shutdown.

**Key files**

- `src/pmcp/types.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/client/manager.py`
- `src/pmcp/server.py`
- `tests/test_tools.py`
- `tests/test_client_manager.py`
- `README.md`

**Depends on**

- IF-0-SERIALIZE-1
- IF-0-REFRESH-2

**Produces**

- IF-0-LIFECYCLE-3 — Optional lifecycle tools use structured inputs and outputs for starting, stopping, and restarting downstream servers without persistent config mutation.

### Phase 4 — Shared-Service Operations and Policy (OPS)

**Objective**

Document and harden the operational contract for multiple clients sharing one PMCP gateway, including auth, rate limiting, status, diagnostics, and user expectations around shared downstream state.

**Exit criteria**

- [x] README explains that HTTP mode is the supported multi-client mode and stdio is single-process local mode.
- [x] README or SECURITY documents that one local per-IP rate limit applies across localhost clients.
- [x] `pmcp doctor` or existing diagnostics surface enough information to detect shared-service mode, singleton lock state, and HTTP reachability.
- [x] Status/help text clearly distinguishes PMCP gateway health from downstream server lifecycle state.
- [x] Lifecycle and refresh docs warn that one client can affect shared downstream servers used by another client.
- [x] Tests cover any CLI/docs-adjacent behavior changes, especially diagnostics/status output.

**Scope notes**

- Prefer documentation and small diagnostic improvements over new APIs.
- If adding rate-limit observability, keep it additive and avoid exposing client-identifying data.
- Keep `/health` and `/metrics` unauthenticated unless a separate security decision changes that contract.
- Mention bearer auth expectations for any non-localhost exposure.

**Non-goals**

- Do not implement per-client auth scopes.
- Do not make `/health` or `/metrics` authenticated in this roadmap unless a release-blocking issue is found.
- Do not introduce a reverse proxy dependency.

**Key files**

- `README.md`
- `SECURITY.md`
- `src/pmcp/cli.py`
- `src/pmcp/transport/http.py`
- `tests/test_cli.py`
- `tests/test_http_transport.py`

**Depends on**

- IF-0-SERIALIZE-1

**Produces**

- IF-0-OPS-4 — Shared-service operational guidance documents multi-client mode, rate-limit implications, auth expectations, and lifecycle disruption behavior.

### Phase 5 — Multi-Client Soak and Release Gate (SOAK)

**Objective**

Add focused multi-client regression and stress coverage, then define the final release-readiness gate for shared-service PMCP.

**Exit criteria**

- [x] Tests simulate multiple clients invoking the same lazy server concurrently.
- [x] Tests simulate one client refreshing or stopping a server while another has an active request.
- [x] Tests verify status, health, and pending-request visibility across shared operations.
- [x] Tests verify no duplicate downstream process or remote connection is created for single-flight paths.
- [x] Final full test suite, lint, type, build, and smoke commands pass.
- [x] Known residual shared-service risks are documented in release notes or the roadmap before release.

**Scope notes**

- Prefer deterministic fake downstream servers and mocks over long sleeps.
- Keep stress tests bounded so they can run in CI.
- Include at least one HTTP transport smoke path if existing test utilities make that practical.
- This phase can include small fixes discovered by the soak tests if they remain within the frozen contracts.

**Non-goals**

- Do not add heavy load testing infrastructure.
- Do not require external MCP services or network access.
- Do not benchmark performance beyond checking for correctness under concurrency.

**Key files**

- `tests/test_client_manager.py`
- `tests/test_tools.py`
- `tests/test_http_transport.py`
- `tests/test_cli.py`
- `CHANGELOG.md`
- `README.md`

**Depends on**

- IF-0-SERIALIZE-1
- IF-0-REFRESH-2
- IF-0-LIFECYCLE-3
- IF-0-OPS-4

**Produces**

- IF-0-SOAK-5 — Multi-client stress and regression tests cover concurrent lazy-start, invoke, refresh, provision/connect, and status/pending visibility.

## Phase Dependency DAG

```text
SERIALIZE -> REFRESH -> LIFECYCLE -> SOAK
     \          \            /
      \--------> OPS -------/
```

## Execution Notes

- Phase 1 should run first; it freezes lock ownership and single-flight behavior used by every later phase.
- Phase 2 should follow Phase 1 because refresh semantics depend on the lifecycle lock model.
- Phase 3 should wait for Phase 2 if lifecycle commands can cancel or restart active requests.
- Phase 4 can be planned after Phase 1 and executed in parallel with Phase 2 or Phase 3 if docs avoid documenting unsettled tool names until those names freeze.
- Phase 5 should run last and may include small contract-preserving fixes found by concurrency tests.
- Do not implement a natural-language settings tool before Phase 3 primitives are stable; consider a future roadmap for persistent startup-policy mutation after this release.

## Verification

Run these after implementation phases, not during roadmap planning:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_lazy_start.py tests/test_http_transport.py tests/test_cli.py -q
uv run pytest tests/test_server_lifecycle.py tests/test_transport_http.py tests/test_secrets_command.py -q
uv run pytest -q
```

For release-readiness after Phase 5:

```bash
uv build
pmcp status --json
pmcp status --verbose
pmcp setup --client claude --mode http
pmcp setup --client opencode --mode http
```

## Phase 5 Execution Notes

Completed SOAK coverage with deterministic asyncio gates and local mocks. Added
manager-level concurrent lazy invoke and active pending-request visibility tests,
gateway-facing concurrent lazy invoke, refresh refusal, lifecycle force-scope,
health/list-pending visibility tests, a local HTTP `/health` + `/metrics` +
authenticated `/mcp` smoke path, and live `pmcp status` JSON/human pending
visibility checks.

Release gate results:

- `uv run pytest tests/test_client_manager.py -k "soak or concurrent or singleflight or pending or refresh or disconnect_server or restart_server"` — passed, 14 selected.
- `uv run pytest tests/test_tools.py -k "soak or concurrent or provision or refresh or pending or health or connect_server or disconnect_server or restart_server"` — passed, 40 selected.
- `uv run pytest tests/test_http_transport.py -k "health or metrics or auth or mcp or smoke"` — passed, 5 selected.
- `uv run pytest tests/test_cli.py -k "status or pending or doctor or health"` — passed, 33 selected.
- `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_lazy_start.py tests/test_http_transport.py tests/test_cli.py -q` — passed, 272 tests.
- `uv run pytest tests/test_server_lifecycle.py tests/test_transport_http.py tests/test_secrets_command.py -q` — passed, 49 tests.
- `uv run pytest -q` — passed, 1595 passed, 12 skipped, 21 deselected, 1 warning.
- `uv run ruff check src/ tests/` — passed.
- `uv run ruff format --check src/ tests/` — passed after cleaning pre-existing formatting drift.
- `uv run mypy src/pmcp --exclude baml_client` — passed.
- `uv build` — passed, built `dist/pmcp-1.9.4.tar.gz` and `dist/pmcp-1.9.4-py3-none-any.whl`.
- `pmcp status --json` — passed.
- `pmcp status --verbose` — passed.
- `pmcp setup --client claude --mode http` — passed.
- `pmcp setup --client opencode --mode http` — passed.

No known residual shared-service correctness risks remain from the SOAK scenarios
covered here.
