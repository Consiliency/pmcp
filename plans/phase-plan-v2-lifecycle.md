# LIFECYCLE: Structured Lifecycle Controls

## Context

Phase 1 made same-server startup and global manager mutation deterministic. Phase 2 froze refresh behavior for active downstream work: refresh refuses by default while requests are pending and cancels only when `force=true`. Phase 3 adds explicit runtime lifecycle controls for shared downstream servers without introducing broad settings mutation or persistent `.mcp.json` edits.

The implementation should fit the existing gateway surface: tools are declared in `get_gateway_tool_definitions()`, routed through `GatewayServer._setup_handlers()`, implemented by `GatewayTools`, and backed by `ClientManager` connection state. Existing `gateway.provision` already starts configured lazy servers and manifest servers as part of install/provision flows; the new lifecycle tools should be narrower operational controls for already known server names.

This phase freezes the public tool names as `gateway.connect_server`, `gateway.disconnect_server`, and `gateway.restart_server`. `disconnect_server` and `restart_server` use the Phase 2 disruption policy: refuse by default if the target server has pending requests, and cancel target-server pending requests only when `force=true`.

## Interface Freeze Gates

- [x] IF-0-LIFECYCLE-1 — `get_gateway_tool_definitions()` exposes three additive tools: `gateway.connect_server`, `gateway.disconnect_server`, and `gateway.restart_server`; no existing tool names, required fields, or output models are changed.
- [x] IF-0-LIFECYCLE-2 — `ConnectServerInput` has `server_name: str`; `DisconnectServerInput` and `RestartServerInput` have `server_name: str` and optional `force: bool = False`.
- [x] IF-0-LIFECYCLE-3 — Lifecycle outputs use one shared model shape with `ok`, `server`, `action`, `prior_status`, `new_status`, `cancelled_request_count`, `message`, and optional `errors`; status values are the existing `ServerStatusEnum` strings where known, otherwise `"unknown"`.
- [x] IF-0-LIFECYCLE-4 — `gateway.connect_server` resolves server names from allowed configured servers, provisioned/manifest servers, and registered discovered servers, then connects through `ClientManager` same-server single-flight semantics without duplicate spawns.
- [x] IF-0-LIFECYCLE-5 — `gateway.disconnect_server` is runtime-only: it removes the active client and server indexes for the named server, frees local resources, leaves config files and `autoStart` unchanged, and preserves startup observations for health/status context.
- [x] IF-0-LIFECYCLE-6 — `gateway.restart_server` is equivalent to target-server disconnect followed by connect for the same resolved runtime config; it refuses by default when that server has pending requests and cancels only target-server requests when `force=true`.
- [x] IF-0-LIFECYCLE-7 — Unknown, policy-denied, and missing-auth server names return structured `ok=false` lifecycle outputs instead of raising through the MCP handler; missing-auth output names the required env var without printing secret values.
- [x] IF-0-LIFECYCLE-8 — `GatewayServer._setup_handlers()` routes all three lifecycle tool names and serializes their Pydantic outputs through the existing `model_dump()` path.
- [x] IF-0-LIFECYCLE-9 — `gateway.health` continues to show stopped servers as `offline` or `lazy` when the server remains known, and startup policy observations are not discarded by a runtime stop.

## Lane Index & Dependencies

- SL-0 — Public lifecycle contract; Depends on: (none); Blocks: SL-1, SL-3, SL-5; Parallel-safe: no
- SL-1 — Manager target-server lifecycle; Depends on: SL-0; Blocks: SL-2, SL-3, SL-5; Parallel-safe: no
- SL-2 — Manager lifecycle tests; Depends on: SL-1; Blocks: SL-5; Parallel-safe: yes
- SL-3 — Gateway lifecycle behavior; Depends on: SL-0, SL-1; Blocks: SL-4, SL-5; Parallel-safe: no
- SL-4 — Gateway lifecycle tests; Depends on: SL-3; Blocks: SL-5; Parallel-safe: yes
- SL-5 — Documentation, roadmap, and closeout; Depends on: SL-0, SL-1, SL-2, SL-3, SL-4; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Public Lifecycle Contract

- **Scope**: Add the additive lifecycle input/output models, gateway tool schemas, and server routing without implementing lifecycle behavior in handlers.
- **Owned files**: `src/pmcp/types.py`, `src/pmcp/server.py`
- **Interfaces provided**: `ConnectServerInput`, `DisconnectServerInput`, `RestartServerInput`, shared lifecycle output model such as `LifecycleServerOutput`, `gateway.connect_server`, `gateway.disconnect_server`, `gateway.restart_server` server dispatch
- **Interfaces consumed**: existing `ServerStatusEnum` value strings, existing MCP `Tool` schema style, existing `GatewayServer._setup_handlers()` routing and `model_dump()` serialization
- **Parallel-safe**: no
- **Tasks**:
  - test: Defer schema assertions to SL-4 because gateway-facing tests own tool-definition compatibility.
  - impl: Add lifecycle input models with `server_name` using the same validation style as `ProvisionInput.server_name`.
  - impl: Add one shared lifecycle output model with `ok`, `server`, `action`, `prior_status`, `new_status`, `cancelled_request_count`, `message`, and `errors`.
  - impl: Add tool definitions for `gateway.connect_server`, `gateway.disconnect_server`, and `gateway.restart_server`; `force` is optional and only present on disconnect/restart.
  - impl: Route the three new tool names in `GatewayServer._setup_handlers()` to `GatewayTools.connect_server(...)`, `disconnect_server(...)`, and `restart_server(...)`.
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/server.py`

### SL-1 — Manager Target-Server Lifecycle

- **Scope**: Add target-server connect/disconnect/restart primitives in `ClientManager` that reuse Phase 1 single-flight and Phase 2 pending-request semantics.
- **Owned files**: `src/pmcp/client/manager.py`
- **Interfaces provided**: target-server disconnect helper such as `ClientManager.disconnect_server(name, force=False)`, target-server restart/connect composition support, target-server pending cancellation helper such as `cancel_pending_requests(server=name)`, stable per-server status/index cleanup
- **Interfaces consumed**: `ClientManager._connect_singleflight(...)`, `ClientManager.ensure_connected(...)`, `ClientManager.connect_all(...)`, `ClientManager.get_pending_requests(server)`, `ClientManager.cancel_all_pending_requests()` semantics, `ManagedClient.pending_requests`, `ServerStatusEnum`, `_remove_server_indexes(...)`, `_cleanup_client(...)`, `_lifecycle_lock`
- **Parallel-safe**: no
- **Tasks**:
  - test: Defer direct manager assertions to SL-2.
  - impl: Add a target-server pending cancellation helper that cancels and removes only pending requests for one server and returns the newly cancelled count.
  - impl: Add a target-server disconnect helper that snapshots the named `ManagedClient`, cancels or refuses pending requests according to `force`, closes the process/remote transport using existing cleanup paths, removes only that server's tools/resources/prompts/client entry, and publishes a stable offline status for known stopped servers.
  - impl: Preserve `_lazy_configs` for configured lazy servers when a runtime stop should leave them startable again; do not mutate config files or startup policy.
  - impl: Ensure disconnect/restart uses `_lifecycle_lock` for registry mutation but does not hold it while normal downstream tool invokes run.
  - impl: Ensure reconnect after restart goes through `_connect_singleflight(config)` or `connect_all([config])` so concurrent starts share work.
  - verify: `uv run ruff check src/pmcp/client/manager.py`

### SL-2 — Manager Lifecycle Tests

- **Scope**: Cover direct `ClientManager` lifecycle primitives, resource cleanup, single-flight reuse, and target-only pending cancellation.
- **Owned files**: `tests/test_client_manager.py`
- **Interfaces provided**: regression coverage for IF-0-LIFECYCLE-4, IF-0-LIFECYCLE-5, IF-0-LIFECYCLE-6, and manager portions of IF-0-LIFECYCLE-9
- **Interfaces consumed**: manager helpers from SL-1, existing `ManagedClient`, `PendingRequest`, `ServerStatus`, `ServerStatusEnum`, existing async process/remote cleanup test patterns
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a disconnect-server test proving only the named server is removed from `_clients`, `_tools`, `_resources`, and `_prompts`, while unrelated servers remain online.
  - test: Add a pending-request refusal test proving target disconnect with `force=False` does not cancel active work or remove the client.
  - test: Add a force-disconnect test proving only target-server pending futures are cancelled and unrelated server pending requests remain listed.
  - test: Add a restart test proving disconnect happens before reconnect and reconnect uses the existing single-flight path for the target config.
  - test: Add an offline/lazy status test proving a runtime stop keeps enough status/config state for `gateway.health` and a later explicit connect.
  - verify: `uv run pytest tests/test_client_manager.py -k "disconnect_server or restart_server or lifecycle or pending"`
  - verify: `uv run ruff check tests/test_client_manager.py`

### SL-3 — Gateway Lifecycle Behavior

- **Scope**: Implement `GatewayTools` lifecycle handlers and server-resolution helpers for configured, manifest/provisioned, and discovered server names.
- **Owned files**: `src/pmcp/tools/handlers.py`
- **Interfaces provided**: `GatewayTools.connect_server(...)`, `GatewayTools.disconnect_server(...)`, `GatewayTools.restart_server(...)`, lifecycle server-resolution helper, structured policy-denied/missing-auth/unknown failures
- **Interfaces consumed**: lifecycle models from SL-0, manager helpers from SL-1, existing `_load_configured_servers()`, `_load_provisioned_registry()`, `load_manifest()`, `manifest_server_to_config(...)`, `_discovered_server_configs`, `_check_any_api_key_available(...)`, `_auth_env_options(...)`, `PolicyManager.is_server_allowed(...)`, Phase 2 `force` semantics
- **Parallel-safe**: no
- **Tasks**:
  - test: Defer gateway assertions to SL-4.
  - impl: Add a private resolver that returns an allowed `ResolvedServerConfig` for configured servers first, then manifest/provisioned or registered discovered servers, while reporting unknown, policy-denied, and missing-auth failures as lifecycle outputs.
  - impl: Implement `connect_server` so already-online servers return `ok=true` with `prior_status` and `new_status` unchanged, lazy/configured servers start through manager single-flight, and failed starts report manager/connection errors without raising through the server.
  - impl: Implement `disconnect_server` so pending target-server requests cause `ok=false` refusal unless `force=true`, and successful forced disconnect reports `cancelled_request_count`.
  - impl: Implement `restart_server` so it resolves the target config before stopping, applies the same target pending policy, disconnects the target, reconnects through the same manager connect path, and reports final status plus errors if reconnect fails.
  - impl: Keep `gateway.provision` behavior unchanged except for sharing any private resolver only when doing so does not change its output text/status contract.
  - verify: `uv run ruff check src/pmcp/tools/handlers.py`

### SL-4 — Gateway Lifecycle Tests

- **Scope**: Add gateway-facing tests for tool schemas, server routing, start/stop/restart behavior, and failure modes.
- **Owned files**: `tests/test_tools.py`, `tests/test_server.py`
- **Interfaces provided**: regression coverage for IF-0-LIFECYCLE-1 through IF-0-LIFECYCLE-9 at the public gateway layer
- **Interfaces consumed**: lifecycle models from SL-0, manager helpers from SL-1, handler behavior from SL-3, existing `MockClientManager`, existing refresh/provision/startup observation fixtures
- **Parallel-safe**: yes
- **Tasks**:
  - test: Extend `MockClientManager` with target-server disconnect/restart and target pending cancellation behavior needed by lifecycle handler tests.
  - test: Add schema assertions proving all three lifecycle tools are present, only disconnect/restart expose optional `force`, and no existing tool required fields change.
  - test: Add connect tests for already-online, configured lazy, manifest/provisioned, unknown, policy-denied, and missing-auth server names.
  - test: Add disconnect tests for successful stop, unknown server, pending refusal, and forced pending cancellation with `cancelled_request_count`.
  - test: Add restart tests for successful restart, reconnect failure, pending refusal, and forced cancellation before reconnect.
  - test: Add health/status assertion that a runtime-stopped server remains visible with startup observation fields when applicable.
  - test: Add server routing assertions in `tests/test_server.py` for the three new gateway tool names.
  - verify: `uv run pytest tests/test_tools.py -k "connect_server or disconnect_server or restart_server or lifecycle"`
  - verify: `uv run pytest tests/test_server.py -k "connect_server or disconnect_server or restart_server or handlers"`
  - verify: `uv run ruff check tests/test_tools.py tests/test_server.py`

### SL-5 — Documentation, Roadmap, and Closeout

- **Scope**: Document the new runtime lifecycle controls and update roadmap/plan completion after all producer lanes are verified.
- **Owned files**: `README.md`, `CHANGELOG.md`, `specs/phase-plans-v2.md`, `plans/phase-plan-v2-lifecycle.md`
- **Interfaces provided**: user-facing lifecycle tool documentation, runtime-only warning, completed Phase 3 checklist, execution notes
- **Interfaces consumed**: SL-0 public contract, SL-1 manager semantics, SL-2 manager test results, SL-3 gateway behavior, SL-4 public test results
- **Parallel-safe**: no
- **Tasks**:
  - impl: Update README gateway tool tables from 16 meta-tools to 19 meta-tools and add concise descriptions for connect/disconnect/restart.
  - impl: Document that disconnect/restart are runtime-only, do not edit `.mcp.json` or `autoStart`, and can affect other clients sharing the gateway.
  - impl: Document that disconnect/restart refuse active target-server requests unless `force=true`, matching refresh's shared-service disruption policy.
  - impl: Add a CHANGELOG entry under Unreleased if this branch is release-bound.
  - impl: Mark Phase 3 exit criteria in `specs/phase-plans-v2.md` after implementation and verification complete.
  - impl: Mark this plan's acceptance criteria complete and record any execution deviations.
  - verify: `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_server.py`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/tools/handlers.py src/pmcp/server.py tests/test_client_manager.py tests/test_tools.py tests/test_server.py`

## Verification

Lane-specific verification:

- `uv run ruff check src/pmcp/types.py src/pmcp/server.py`
- `uv run ruff check src/pmcp/client/manager.py`
- `uv run pytest tests/test_client_manager.py -k "disconnect_server or restart_server or lifecycle or pending"`
- `uv run ruff check tests/test_client_manager.py`
- `uv run ruff check src/pmcp/tools/handlers.py`
- `uv run pytest tests/test_tools.py -k "connect_server or disconnect_server or restart_server or lifecycle"`
- `uv run pytest tests/test_server.py -k "connect_server or disconnect_server or restart_server or handlers"`
- `uv run ruff check tests/test_tools.py tests/test_server.py`

Whole-phase regression:

- `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_server.py`
- `uv run pytest tests/test_lazy_start.py tests/test_server_lifecycle.py tests/test_http_transport.py tests/test_cli.py` if lifecycle implementation touches startup, HTTP shared-service behavior, or CLI status/help output.
- `uv run pytest` before release handoff if time permits.

## Acceptance Criteria

- [x] `gateway.connect_server` can explicitly start/connect an allowed configured, provisioned/manifest, or registered discovered server by name without duplicate same-server spawns.
- [x] `gateway.disconnect_server` can explicitly stop/disconnect a running downstream server, free resources, and remove that server's tool/resource/prompt indexes without affecting unrelated servers.
- [x] `gateway.restart_server` performs target-server stop then start with clear pending-request behavior and no persistent config mutation.
- [x] Disconnect/restart refuse by default when the target server has pending requests and cancel only target-server pending requests when `force=true`.
- [x] Lifecycle outputs include server name, action, prior status, new status, cancelled request count, message, and structured errors when startup/stop fails.
- [x] Unknown server, policy-denied server, missing-auth server, already-running connect, stopped/offline disconnect, and reconnect failure paths return structured `ok=false` or no-op `ok=true` outputs as appropriate.
- [x] `gateway.health` reflects runtime-stopped servers without losing startup policy observations where relevant.
- [x] Gateway server routing supports all three lifecycle tools through the existing JSON serialization path.
- [x] Tests cover start, stop, restart, unknown server, policy-denied server, missing-auth server, and active-request stop/restart behavior.

## Execution Notes

- Implemented lifecycle controls as additive gateway tools and Pydantic models.
- `disconnect_server` and `restart_server` use target-server pending-request refusal by default and target-only cancellation with `force=true`.
- Runtime stops preserve known server status for health and preserve startup observations; no persistent `.mcp.json` or `autoStart` mutation is performed.
- Updated the baseline gateway tool surface test to the frozen 19-tool contract.
- Verification completed with full `uv run pytest` and touched-file `uv run ruff check`.
