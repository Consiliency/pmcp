# RUNTIME: Runtime Startup Policy Migration

**Status**: Completed in current working tree.

## Context

Phase 3 wires the Phase 2 startup resolver into gateway runtime behavior. Today `GatewayServer.initialize()` loads `.mcp.json` configs as lazy, then independently adds legacy manifest `auto_start` entries as eager. `GatewayTools.refresh()` has a separate policy path that reconnects `.mcp.json`, legacy manifest `auto_start`, and provisioned registry servers together through `ClientManager.refresh(...)`.

The migration target is one startup policy path: configured, manifest, and provisioned servers are resolved through `resolve_startup_configs(...)`; explicit user `autoStart` controls eager connections; all other configured, manifest, and provisioned servers remain lazy unless a named legacy compatibility switch is enabled. Startup must register lazy configs before connecting eager configs, and refresh must preserve first-use lazy behavior by re-registering lazy configs instead of eagerly connecting every available server.

## Interface Freeze Gates

- [x] IF-0-RUNTIME-1 — `GatewayServer.initialize()` calls `load_enabled_auto_start(...)`, `load_disabled_auto_start(...)`, `load_manifest()`, and `resolve_startup_configs(...)` after `load_configs(...)` and `filter_self_references(...)`, then registers `StartupResolution.lazy_configs` before connecting `StartupResolution.eager_configs`.
- [x] IF-0-RUNTIME-2 — `GatewayTools.refresh(...)` uses the same resolver inputs and policy/auth predicates as startup, including configured configs, manifest servers, explicit `autoStart`, legacy `disableAutoStart`, provisioned registry names, policy allow checks, and auth availability checks.
- [x] IF-0-RUNTIME-3 — Legacy manifest `auto_start` eager behavior is available only when `PMCP_LEGACY_MANIFEST_AUTOSTART=1` is set; otherwise manifest-only servers, including packaged `auto_start: true` entries, resolve lazy unless explicitly listed in `autoStart`.
- [x] IF-0-RUNTIME-4 — Refresh reconnects only eager configs and registers lazy configs for on-demand startup; it must not call `ClientManager.refresh(...)` with lazy configs in a way that eagerly starts them.
- [x] IF-0-RUNTIME-5 — Policy-denied and missing-auth eager servers from `StartupResolution.skipped` are logged without aborting gateway startup or refresh; missing-auth lazy manifest servers remain available lazily.
- [x] IF-0-RUNTIME-6 — Existing health/status semantics remain compatible: online connected servers still report online, registered lazy servers still report lazy/offline as currently modeled by `ClientManager.register_lazy_configs(...)`, and `gateway.health` does not require a response-model change in this phase.

## Lane Index & Dependencies

- SL-0 — Runtime resolver adapter; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 — Gateway initialize migration; Depends on: SL-0; Blocks: SL-3, SL-5; Parallel-safe: yes
- SL-2 — Gateway refresh migration; Depends on: SL-0; Blocks: SL-4, SL-5; Parallel-safe: yes
- SL-3 — Initialize behavior tests; Depends on: SL-1; Blocks: SL-5; Parallel-safe: yes
- SL-4 — Refresh behavior tests; Depends on: SL-2; Blocks: SL-5; Parallel-safe: yes
- SL-5 — Phase synthesis and regression review; Depends on: SL-1, SL-2, SL-3, SL-4; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Runtime Resolver Adapter

- **Scope**: Add the minimal shared runtime glue that converts current server, manifest, policy, auth, and environment inputs into a `StartupResolution`.
- **Owned files**: `src/pmcp/config/loader.py`
- **Interfaces provided**: `is_legacy_manifest_auto_start_enabled(...)`, optional helper for formatting/logging `StartupSkip` entries if needed
- **Interfaces consumed**: `resolve_startup_configs(...)`, `load_enabled_auto_start(...)`, `load_disabled_auto_start(...)`, `StartupResolution`, `StartupSkip`, `StartupSkipReason`, `os.environ`
- **Parallel-safe**: no
- **Tasks**:
  - test: Drive the legacy switch through runtime tests in SL-3 and SL-4 rather than adding broad resolver tests; Phase 2 already covers resolver classification.
  - impl: Add a small helper such as `is_legacy_manifest_auto_start_enabled(env: Mapping[str, str] | None = None) -> bool` that returns true only for `PMCP_LEGACY_MANIFEST_AUTOSTART=1`.
  - impl: Keep the compatibility switch local to runtime policy; do not change manifest parsing or remove `ServerConfig.auto_start`.
  - impl: If skip logging is centralized, keep it narrow and dependency-free so both `pmcp.server` and `pmcp.tools.handlers` can call it without introducing import cycles.
  - verify: `uv run ruff check src/pmcp/config/loader.py`

### SL-1 — Gateway Initialize Migration

- **Scope**: Replace `GatewayServer.initialize()`'s bespoke lazy/manifest auto-start split with the shared resolver output.
- **Owned files**: `src/pmcp/server.py`
- **Interfaces provided**: startup use of IF-0-RUNTIME-1, IF-0-RUNTIME-3, and IF-0-RUNTIME-5
- **Interfaces consumed**: `load_configs(...)`, `filter_self_references(...)`, `load_manifest()`, `load_enabled_auto_start(...)`, `load_disabled_auto_start(...)`, `resolve_startup_configs(...)`, `PolicyManager.is_server_allowed(...)`, `os.environ.get`, `ClientManager.register_lazy_configs(...)`, `ClientManager.connect_all(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Update initialize tests in SL-3 before or alongside implementation to expect explicit `autoStart` eager behavior and default manifest lazy behavior.
  - impl: Import `load_enabled_auto_start`, `resolve_startup_configs`, and `is_legacy_manifest_auto_start_enabled` from `pmcp.config.loader`.
  - impl: After config load and self-reference filtering, load the manifest once and pass `manifest.servers` into `resolve_startup_configs(...)`.
  - impl: Pass enabled and disabled startup policy sets using the same `project_root` and `custom_config_path` arguments already used by config loading.
  - impl: Pass `self._policy_manager.is_server_allowed` as the resolver policy predicate and `lambda env_var: bool(os.environ.get(env_var))` as startup auth availability.
  - impl: Replace `lazy_configs`, `auto_start_configs`, `allowed_lazy`, and `allowed_auto_start` with `resolution.lazy_configs` and `resolution.eager_configs`.
  - impl: Keep `_kill_orphan_processes(...)`, `register_lazy_configs(...)`, `connect_all(...)`, health monitor start, capability summary, and description-cache generation ordering intact.
  - impl: Log counts for lazy, eager, and skipped entries; do not surface skipped entries in public health output in this phase.
  - verify: `uv run pytest tests/test_lazy_start.py -k "initialize or auto_start or lazy"`
  - verify: `uv run pytest tests/test_server_lifecycle.py -k "initialize"`
  - verify: `uv run ruff check src/pmcp/server.py tests/test_lazy_start.py tests/test_server_lifecycle.py`

### SL-2 — Gateway Refresh Migration

- **Scope**: Make `gateway.refresh` resolve startup policy the same way as gateway initialization while preserving lazy first-use behavior.
- **Owned files**: `src/pmcp/tools/handlers.py`
- **Interfaces provided**: refresh use of IF-0-RUNTIME-2, IF-0-RUNTIME-3, IF-0-RUNTIME-4, and IF-0-RUNTIME-5
- **Interfaces consumed**: `load_configs(...)`, `filter_self_references(...)`, `load_manifest()`, `load_enabled_auto_start(...)`, `load_disabled_auto_start(...)`, `resolve_startup_configs(...)`, `GatewayTools._load_provisioned_registry()`, `GatewayTools._check_api_key_available(...)`, `PolicyManager.is_server_allowed(...)`, `ClientManager.disconnect_all(...)`, `ClientManager.register_lazy_configs(...)`, `ClientManager.connect_all(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Update refresh tests in SL-4 before or alongside implementation to prove lazy configs are registered and only explicit eager configs are connected.
  - impl: Import `load_enabled_auto_start`, `load_disabled_auto_start`, `resolve_startup_configs`, and `is_legacy_manifest_auto_start_enabled` from `pmcp.config.loader`.
  - impl: Load configured configs, filter self-references, load manifest once, and load provisioned registry names before resolving.
  - impl: Pass `manifest.servers` and `set(provisioned_registry)` into `resolve_startup_configs(...)`; let the resolver classify provisioned manifest entries as lazy unless explicitly enabled.
  - impl: Pass `self._policy_manager.is_server_allowed` and `self._check_api_key_available` into the resolver.
  - impl: Replace the current append-and-filter refresh list with `resolution.lazy_configs` and `resolution.eager_configs`.
  - impl: Avoid `ClientManager.refresh(...)` for the final eager/lazy result unless that method is changed in this lane's owned file set; use existing public manager calls in sequence: disconnect current clients, register lazy configs, connect eager configs.
  - impl: Keep the `RefreshOutput` fields backward compatible; compute `servers_seen` from unique resolved lazy plus eager plus skipped names, and preserve `revision_id` from `get_registry_meta()`.
  - impl: Log skipped policy/auth/unknown entries without failing refresh unless actual disconnect/connect operations fail.
  - verify: `uv run pytest tests/test_tools.py -k "refresh or provisioned"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-3 — Initialize Behavior Tests

- **Scope**: Update gateway startup tests to assert the new resolver-backed runtime policy.
- **Owned files**: `tests/test_lazy_start.py`, `tests/test_server_lifecycle.py`
- **Interfaces provided**: regression coverage for IF-0-RUNTIME-1, IF-0-RUNTIME-3, IF-0-RUNTIME-5, and IF-0-RUNTIME-6 during startup
- **Interfaces consumed**: `GatewayServer.initialize()`, `load_enabled_auto_start(...)`, `load_disabled_auto_start(...)`, `resolve_startup_configs(...)`, `PMCP_LEGACY_MANIFEST_AUTOSTART`, existing client-manager mocks
- **Parallel-safe**: yes
- **Tasks**:
  - test: Replace `test_initialize_does_not_consume_mcp_json_auto_start_yet` with coverage proving configured servers listed in `autoStart` are passed to `connect_all(...)`.
  - test: Update manifest auto-start tests so manifest `auto_start: true` servers are registered lazy by default unless `PMCP_LEGACY_MANIFEST_AUTOSTART=1` is patched into the environment.
  - test: Keep or update the ordering test to assert `register_lazy_configs(...)` is called before `connect_all(...)` when both lazy and eager configs exist.
  - test: Add missing-auth eager manifest coverage where an explicit `autoStart` manifest server requiring an unavailable env var is skipped, while startup continues.
  - test: Update lifecycle initialize mocks to include `load_enabled_auto_start` and resolver-era manifest access where needed.
  - impl: Adjust no production files in this lane.
  - verify: `uv run pytest tests/test_lazy_start.py -k "initialize or auto_start or lazy"`
  - verify: `uv run pytest tests/test_server_lifecycle.py -k "initialize"`

### SL-4 — Refresh Behavior Tests

- **Scope**: Update gateway refresh tests to assert the new resolver-backed runtime policy and lazy preservation.
- **Owned files**: `tests/test_tools.py`
- **Interfaces provided**: regression coverage for IF-0-RUNTIME-2, IF-0-RUNTIME-3, IF-0-RUNTIME-4, and provisioned-server lazy classification
- **Interfaces consumed**: `GatewayTools.refresh(...)`, `load_enabled_auto_start(...)`, `load_disabled_auto_start(...)`, `PMCP_LEGACY_MANIFEST_AUTOSTART`, `MockClientManager`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Replace `TestRefreshCompatibility.test_refresh_keeps_current_manifest_policy_and_provisioned_behavior` with tests proving `.mcp.json`, manifest-only, and provisioned entries are registered lazy by default.
  - test: Add a refresh test where `load_enabled_auto_start(...)` returns a configured server name and `connect_all(...)` receives only that eager config.
  - test: Add a refresh test where a manifest `auto_start: true` server is eager only when `PMCP_LEGACY_MANIFEST_AUTOSTART=1`.
  - test: Add a provisioned-registry refresh test proving provisioned servers remain lazy unless explicitly listed in `autoStart`.
  - test: Add policy-denied and missing-auth assertions if the existing `MockClientManager` can expose enough state without broad fixture churn.
  - impl: Adjust no production files in this lane.
  - verify: `uv run pytest tests/test_tools.py -k "refresh or provisioned"`

### SL-5 — Phase Synthesis and Regression Review

- **Scope**: Run the final consistency pass, update roadmap status if Phase 3 implementation completes, and consciously defer user-facing docs to Phase 4 unless behavior text must be corrected immediately.
- **Owned files**: `specs/phase-plans-v1.md`
- **Interfaces provided**: completed Phase 3 roadmap status and checked exit criteria if implementation is merged
- **Interfaces consumed**: startup evidence from SL-1 and SL-3, refresh evidence from SL-2 and SL-4, IF-0-RUNTIME-1 through IF-0-RUNTIME-6
- **Parallel-safe**: no
- **Tasks**:
  - test: Review Phase 3 exit criteria against implemented runtime behavior and test evidence.
  - impl: If Phase 3 is completed, mark Phase 3 status and exit criteria in `specs/phase-plans-v1.md` using the existing Phase 1/2 style.
  - impl: Do not update README or manifest defaults in this lane unless Phase 3 exposes inaccurate user-facing instructions that would mislead users before Phase 4.
  - verify: `uv run pytest tests/test_startup_resolver.py tests/test_lazy_start.py tests/test_server_lifecycle.py tests/test_tools.py`
  - verify: `uv run ruff check src/pmcp/config/loader.py src/pmcp/server.py src/pmcp/tools/handlers.py tests/test_lazy_start.py tests/test_server_lifecycle.py tests/test_tools.py`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_lazy_start.py -k "initialize or auto_start or lazy"`
- `uv run pytest tests/test_server_lifecycle.py -k "initialize"`
- `uv run pytest tests/test_tools.py -k "refresh or provisioned"`
- `uv run ruff check src/pmcp/config/loader.py src/pmcp/server.py src/pmcp/tools/handlers.py tests/test_lazy_start.py tests/test_server_lifecycle.py tests/test_tools.py`

Whole-phase regression:

- `uv run pytest tests/test_startup_resolver.py tests/test_lazy_start.py tests/test_server_lifecycle.py tests/test_tools.py`
- `uv run pytest tests/test_config_loader.py tests/test_guidance_config.py tests/test_client_manager.py`
- `uv run pytest` before handing off the phase if time permits.

## Acceptance Criteria

- [x] `GatewayServer.initialize()` registers resolver lazy configs before connecting resolver eager configs.
- [x] Configured `.mcp.json` servers listed in top-level `autoStart` are eagerly connected during startup; configured servers not listed remain lazy.
- [x] `gateway.refresh` uses the same resolver policy as startup for configured, manifest, and provisioned servers.
- [x] Refresh preserves lazy first-use behavior by registering lazy configs instead of eagerly reconnecting every available server.
- [x] Manifest `auto_start: true` entries are lazy by default and become eager only through explicit `autoStart` or `PMCP_LEGACY_MANIFEST_AUTOSTART=1`.
- [x] `disableAutoStart` continues to suppress legacy manifest eager behavior when the legacy compatibility switch is enabled.
- [x] Policy-denied servers and missing-auth eager servers are skipped cleanly without failing gateway startup or refresh.
- [x] Lazy and online server health/status output remains compatible with current `ClientManager` status behavior.
- [x] Existing lazy-start tests are updated for the new eager source and pass with the resolver-backed runtime path.
