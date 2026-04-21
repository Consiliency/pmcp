# OBSERVE: Startup Observability and Polish

## Context

Phase 5 improves visibility into the startup policy decisions introduced by the config, resolver, runtime, and migration phases. The current runtime already classifies servers with `resolve_startup_configs(...)`, logs eager/lazy/skipped counts during startup and refresh, registers lazy servers in `ClientManager`, and exposes basic `online`, `lazy`, `offline`, and `error` status through `gateway.health` and `pmcp status`.

The remaining gap is that the structured status surface does not preserve the policy reason behind those states. Users can see that a server is lazy or absent, but not whether it was explicitly eager, lazy by default, skipped because of unknown `autoStart`, denied by policy, or skipped because required auth was missing. This phase should add backward-compatible structured fields and CLI presentation without changing startup behavior.

## Interface Freeze Gates

- [ ] IF-0-OBSERVE-1 — `pmcp.types.ServerHealthInfo` exposes backward-compatible optional startup policy fields: the resolved startup classification, resolver source, skip reason, and missing-auth environment variable when available.
- [ ] IF-0-OBSERVE-2 — `GatewayServer.initialize()` records the `StartupResolution` outcome after resolver classification so `gateway.health` can report eager, lazy, skipped, denied, and missing-auth decisions without re-resolving configuration.
- [ ] IF-0-OBSERVE-3 — `GatewayTools.refresh(...)` replaces the stored startup observation snapshot after refresh and returns the same health/status classification shape as initial startup.
- [ ] IF-0-OBSERVE-4 — `gateway.health` remains backward compatible: existing `revision_id`, `servers`, `last_refresh_ts`, and per-server `name`, `status`, `tool_count`, `error` fields keep their current meanings.
- [ ] IF-0-OBSERVE-5 — `pmcp status --verbose` displays startup policy details from live `gateway.health` when available and does not eagerly connect configured servers in local fallback status mode.
- [ ] IF-0-OBSERVE-6 — Startup and refresh logs include one concise summary line with eager, lazy, skipped, policy-denied, missing-auth, and unknown-auto-start counts, plus actionable per-entry messages for skipped `autoStart` and missing-auth entries.

## Lane Index & Dependencies

- SL-0 — Observability model contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 — Startup snapshot producer; Depends on: SL-0; Blocks: SL-3, SL-5; Parallel-safe: yes
- SL-2 — Refresh and health consumer; Depends on: SL-0; Blocks: SL-3, SL-4, SL-5; Parallel-safe: yes
- SL-3 — Gateway observability tests; Depends on: SL-1, SL-2; Blocks: SL-6; Parallel-safe: yes
- SL-4 — CLI verbose status presentation; Depends on: SL-2; Blocks: SL-5, SL-6; Parallel-safe: yes
- SL-5 — CLI tests; Depends on: SL-4; Blocks: SL-6; Parallel-safe: yes
- SL-6 — Documentation and phase review; Depends on: SL-1, SL-2, SL-3, SL-4, SL-5; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Observability Model Contract

- **Scope**: Add the public, backward-compatible status fields and a small internal snapshot representation for startup policy decisions.
- **Owned files**: `src/pmcp/types.py`, `src/pmcp/config/loader.py`
- **Interfaces provided**: optional `ServerHealthInfo.startup_policy`, `ServerHealthInfo.startup_source`, `ServerHealthInfo.startup_skip_reason`, `ServerHealthInfo.startup_env_var`; internal helper such as `StartupObservation` or `StartupObservationSnapshot`
- **Interfaces consumed**: `StartupResolution`, `StartupSkip`, `StartupSkipReason`, `ResolvedServerConfig`, existing `ServerHealthInfo` and `HealthOutput`
- **Parallel-safe**: no
- **Tasks**:
  - test: Define the expected health model fields in SL-3 before or alongside implementation.
  - impl: Add optional fields to `ServerHealthInfo` rather than changing existing required fields or enum values.
  - impl: Use explicit string literals for the public startup policy values, such as `eager`, `lazy`, `skipped`, and `unknown`, so JSON output remains simple.
  - impl: Add a narrow helper that converts a `StartupResolution` into a name-keyed snapshot containing eager names, lazy names, skipped entries, resolver source, skip reason, and missing-auth env var.
  - impl: Keep resolver behavior pure; the helper may consume resolver output, but `resolve_startup_configs(...)` should not gain runtime logging or status side effects.
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/config/loader.py`

### SL-1 — Startup Snapshot Producer

- **Scope**: Preserve the startup resolver outcome from `GatewayServer.initialize()` and emit concise startup policy logs.
- **Owned files**: `src/pmcp/server.py`
- **Interfaces provided**: startup use of IF-0-OBSERVE-2 and startup logging use of IF-0-OBSERVE-6
- **Interfaces consumed**: `resolve_startup_configs(...)`, startup observation helper from SL-0, `GatewayTools` snapshot setter from SL-2, `StartupSkipReason`, `ClientManager.register_lazy_configs(...)`, `ClientManager.connect_all(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or update startup health tests in SL-3 to prove initial startup classifications survive into `gateway.health`.
  - impl: After `resolution = resolve_startup_configs(...)`, pass the derived startup observation snapshot into `self._gateway_tools` before any later health calls can run.
  - impl: Replace the current three count logs with one concise summary that includes eager, lazy, skipped, policy-denied, missing-auth, and unknown-auto-start counts.
  - impl: Keep per-skipped-entry logs, but make unknown `autoStart` and missing-auth messages actionable; include the server name, skip reason, and env var name when present.
  - impl: Do not change the order of orphan cleanup, lazy registration, eager connection, health monitor startup, or capability summary generation.
  - verify: `uv run pytest tests/test_lazy_start.py -k "initialize or auto_start or missing_auth or health"`
  - verify: `uv run ruff check src/pmcp/server.py tests/test_lazy_start.py`

### SL-2 — Refresh and Health Consumer

- **Scope**: Store refresh resolver outcomes and merge startup policy details into `gateway.health`.
- **Owned files**: `src/pmcp/tools/handlers.py`
- **Interfaces provided**: `GatewayTools.set_startup_observations(...)` or equivalent, refresh use of IF-0-OBSERVE-3, health use of IF-0-OBSERVE-4
- **Interfaces consumed**: startup observation helper from SL-0, `StartupResolution`, `StartupSkipReason`, `ClientManager.get_all_server_statuses(...)`, provisioned registry fallback, existing `RefreshOutput`, existing `HealthOutput`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add health tests in SL-3 for online eager, lazy, skipped unknown `autoStart`, policy-denied, and missing-auth entries.
  - impl: Add a private name-keyed startup observation snapshot to `GatewayTools`, defaulting to empty so tests and non-initialized handlers keep current behavior.
  - impl: In `refresh(...)`, replace the snapshot after successful resolver classification and before returning `RefreshOutput`.
  - impl: In `health(...)`, attach optional startup fields to each existing status row when the snapshot has a matching entry.
  - impl: Add skipped entries from the snapshot to `HealthOutput.servers` when they are not already represented by `ClientManager` status, using `status="offline"`, `tool_count=0`, and the skip fields rather than an error.
  - impl: Keep provisioned offline entries in health output; if a provisioned entry also has a startup observation, merge the observation fields rather than duplicating the server.
  - impl: Mirror the startup concise summary log in `refresh(...)`, including counts by skip reason.
  - verify: `uv run pytest tests/test_tools.py -k "health or refresh or missing_auth or policy"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-3 — Gateway Observability Tests

- **Scope**: Cover the public health/status contract for lazy, eager, skipped, denied, and missing-auth startup decisions.
- **Owned files**: `tests/test_lazy_start.py`, `tests/test_tools.py`
- **Interfaces provided**: regression coverage for IF-0-OBSERVE-1 through IF-0-OBSERVE-4 and refresh-side IF-0-OBSERVE-6
- **Interfaces consumed**: `GatewayServer.initialize()`, `GatewayTools.refresh(...)`, `GatewayTools.health()`, `ServerHealthInfo`, `StartupSkipReason`, existing mock client manager patterns
- **Parallel-safe**: yes
- **Tasks**:
  - test: Extend startup tests to assert configured `autoStart` eager entries appear in health with startup policy `eager`.
  - test: Extend lazy startup tests to assert default lazy entries appear with startup policy `lazy`.
  - test: Add startup coverage where unknown `autoStart` appears in health as a skipped/offline row with skip reason `unknown_auto_start`.
  - test: Add health coverage where a policy-denied resolver skip appears as skipped/offline with skip reason `policy_denied`.
  - test: Add missing-auth coverage where an eager manifest entry reports `missing_auth` and the env var name without failing startup.
  - test: Add refresh coverage proving a later refresh replaces stale startup observation details.
  - impl: Adjust no production files in this lane.
  - verify: `uv run pytest tests/test_lazy_start.py -k "health or initialize or missing_auth or auto_start"`
  - verify: `uv run pytest tests/test_tools.py -k "health or refresh or missing_auth or policy"`

### SL-4 — CLI Verbose Status Presentation

- **Scope**: Surface startup policy details in `pmcp status --verbose`, preferring live gateway health and avoiding eager local fallback startup.
- **Owned files**: `src/pmcp/cli.py`
- **Interfaces provided**: CLI use of IF-0-OBSERVE-5
- **Interfaces consumed**: live `_query_running_gateway_status(...)` output, optional `ServerHealthInfo` startup fields, existing local fallback `load_configs(...)`, `filter_self_references(...)`, `PolicyManager`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add CLI assertions in SL-5 before or alongside implementation.
  - impl: For live status output, include startup policy details only when `args.verbose` is set so normal `pmcp status` remains compact.
  - impl: For live JSON output, pass through health payload fields unchanged; do not rename public keys.
  - impl: For local fallback status mode, stop using `connect_all(...)` merely to render status; list configured allowed servers as lazy/offline with no tool count unless there is live gateway data.
  - impl: If changing local fallback behavior is too broad for this phase, gate the no-connect fallback behind `--verbose` and keep existing behavior otherwise; record the decision in SL-6.
  - impl: Keep pending-request rendering unchanged.
  - verify: `uv run pytest tests/test_cli.py -k "status"`
  - verify: `uv run ruff check src/pmcp/cli.py tests/test_cli.py`

### SL-5 — CLI Tests

- **Scope**: Prove `pmcp status --verbose` renders startup policy details and JSON preserves the structured health fields.
- **Owned files**: `tests/test_cli.py`
- **Interfaces provided**: CLI regression coverage for IF-0-OBSERVE-5
- **Interfaces consumed**: `run_status(...)`, `_query_running_gateway_status(...)`, optional health startup fields, argparse status options
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a live-snapshot `--verbose` test where eager, lazy, skipped, and missing-auth servers print startup policy details.
  - test: Add a `--json` live-snapshot test proving startup policy fields are present in emitted JSON when provided by health.
  - test: Add or update local fallback tests to confirm `run_status(...)` does not unexpectedly spawn every configured server if SL-4 changes fallback behavior.
  - test: Keep existing status parser tests unchanged except for any new assertions needed around verbose output.
  - impl: Adjust no production files in this lane.
  - verify: `uv run pytest tests/test_cli.py -k "status"`

### SL-6 — Documentation and Phase Review

- **Scope**: Document the observability surface and update the roadmap status only after implementation and tests complete.
- **Owned files**: `README.md`, `CHANGELOG.md`, `specs/phase-plans-v1.md`
- **Interfaces provided**: user-facing explanation of startup policy status fields and Phase 5 completion status if implemented
- **Interfaces consumed**: outputs from SL-1 through SL-5, IF-0-OBSERVE-1 through IF-0-OBSERVE-6
- **Parallel-safe**: no
- **Tasks**:
  - test: Manually review README status and startup-policy sections for consistency with the final field names.
  - impl: Document how `pmcp status --verbose` and `gateway.health` distinguish lazy, eager, skipped, denied, and missing-auth servers.
  - impl: Include a concise example of missing-auth output that names the env var but does not reveal secret values.
  - impl: Add a CHANGELOG entry if this phase is going into a release branch.
  - impl: If Phase 5 completes, mark Phase 5 status and exit criteria in `specs/phase-plans-v1.md` using the existing completed-phase style.
  - verify: `uv run pytest tests/test_lazy_start.py tests/test_tools.py tests/test_cli.py`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/config/loader.py src/pmcp/server.py src/pmcp/tools/handlers.py src/pmcp/cli.py tests/test_lazy_start.py tests/test_tools.py tests/test_cli.py`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_lazy_start.py -k "initialize or auto_start or missing_auth or health"`
- `uv run pytest tests/test_tools.py -k "health or refresh or missing_auth or policy"`
- `uv run pytest tests/test_cli.py -k "status"`
- `uv run ruff check src/pmcp/types.py src/pmcp/config/loader.py src/pmcp/server.py src/pmcp/tools/handlers.py src/pmcp/cli.py tests/test_lazy_start.py tests/test_tools.py tests/test_cli.py`

Whole-phase regression:

- `uv run pytest tests/test_startup_resolver.py tests/test_lazy_start.py tests/test_tools.py tests/test_cli.py`
- `uv run pytest tests/test_config_loader.py tests/test_guidance_config.py tests/test_server_lifecycle.py tests/test_client_manager.py`
- `uv run pytest` before handing off the phase if time permits.

## Acceptance Criteria

- [ ] `gateway.health` includes backward-compatible optional fields that identify eager, lazy, skipped, policy-denied, missing-auth, and unknown `autoStart` startup outcomes.
- [ ] `GatewayServer.initialize()` records startup resolver observations without changing eager/lazy connection behavior.
- [ ] `gateway.refresh` records replacement startup observations and uses the same status shape as initial startup.
- [ ] Skipped startup entries that are not represented by `ClientManager` statuses appear in health output with `status="offline"` and machine-readable skip details.
- [ ] `pmcp status --verbose` displays startup policy details from live gateway health.
- [ ] JSON status output preserves startup policy fields from health without lossy renaming.
- [ ] Unknown `autoStart` entries produce actionable logs and health/status details.
- [ ] Missing-auth eager entries report the required env var name without printing secret values or failing gateway startup.
- [ ] Tests cover status/health output for lazy, eager, skipped, policy-denied, and missing-auth cases.
- [ ] Normal `pmcp status`, `gateway.health`, startup, and refresh behavior remain backward compatible for existing clients.
