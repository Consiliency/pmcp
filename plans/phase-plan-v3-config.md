# CONFIG: Discovery and Configuration Administration

## Context

Phase 5 of `specs/phase-plans-v3.md` adds structured persistent administration for startup policy, setup profiles, and server discovery after the PROTO, AUTH, and OBSERVE contracts are available. The current code already parses `autoStart` and legacy `disableAutoStart`, resolves configured/manifest/provisioned servers into lazy/eager/skipped groups, exposes startup observations through `gateway.health` and live `pmcp status --verbose`, supports `pmcp setup` for Claude/OpenCode stdio/http snippets, and has registry discovery tools through `gateway.search_registry` and `gateway.register_discovered_server`.

CONFIG should build on those existing primitives. The new work is the administrative layer: effective configuration/status with source attribution, dry-run and explicit-apply startup-policy mutation that preserves unrelated `.mcp.json` content, named setup profiles, stale policy diagnostics, and a conservative read-only review of registry/server-card metadata where stable enough to improve discovery.

## Interface Freeze Gates

- [x] IF-0-CONFIG-1 - PMCP exposes an additive config administration model with effective per-server rows that include `name`, `status`, `startup_policy`, `startup_source`, source file attribution, `startup_skip_reason`, `startup_env_var`, `auth_state`, provisioned/manifest/configured flags, and non-secret diagnostics.
- [x] IF-0-CONFIG-2 - `gateway.config_status` returns read-only effective configuration/status for configured, manifest, provisioned, discovered, unknown `autoStart`, policy-denied, missing-auth, and stale provisioned entries without mutating files.
- [x] IF-0-CONFIG-3 - `gateway.get_startup_policy` returns persisted `autoStart` and legacy `disableAutoStart` entries grouped by source path, with conflict and stale-entry diagnostics.
- [x] IF-0-CONFIG-4 - `gateway.set_startup_policy` supports structured `autoStart` add/remove/set operations with `dry_run` defaulting to true or otherwise requiring an explicit `apply` flag, writes exactly one selected config file, uses atomic JSON writes, and preserves unrelated top-level and server fields.
- [x] IF-0-CONFIG-5 - `pmcp setup` supports named profiles for local stdio, shared-local HTTP, authenticated shared HTTP, and CI, and writes only when `--write` is supplied.
- [x] IF-0-CONFIG-6 - Registry/server-card discovery is evaluated through read-only metadata normalization; any unstable fields remain diagnostics or additive hints and do not drive automatic config mutation.
- [x] IF-0-CONFIG-7 - Tests cover read-only status, atomic/no-op config edits, ambiguous-source refusal, stale and conflict diagnostics, setup profile output/write behavior, discovery metadata normalization, and backward compatibility with existing `.mcp.json` files.

## Lane Index & Dependencies

- SL-0 - Admin contracts and config edit primitives; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 - Registry and server-card discovery normalization; Depends on: SL-0; Blocks: SL-2, SL-4; Parallel-safe: yes
- SL-2 - Gateway configuration administration tools; Depends on: SL-0, SL-1; Blocks: SL-3, SL-4; Parallel-safe: no
- SL-3 - CLI setup profiles and config commands; Depends on: SL-0, SL-2; Blocks: SL-4; Parallel-safe: no
- SL-4 - End-to-end, docs, and roadmap closeout; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Admin Contracts and Config Edit Primitives

- **Scope**: Define the shared config-admin models and low-level source-aware startup-policy read/preview/write helpers that all user-facing surfaces consume.
- **Owned files**: `src/pmcp/types.py`, `src/pmcp/config/loader.py`, `tests/test_config_loader.py`, `tests/test_startup_resolver.py`
- **Interfaces provided**: `ConfigSourceInfo`, `EffectiveConfigEntry`, `ConfigStatusOutput`, `StartupPolicySource`, `StartupPolicyDiagnostic`, `StartupPolicyOperation`, `StartupPolicyPreview`, `StartupPolicyOutput`, source-aware config loading helpers, startup-policy preview helper, atomic selected-source writer, stale `autoStart`/`disableAutoStart` diagnostics, unknown-provisioned diagnostics
- **Interfaces consumed**: existing `McpConfigFile.autoStart`, `McpConfigFile.disableAutoStart`, `load_configs(...)`, `load_enabled_auto_start(...)`, `load_disabled_auto_start(...)`, `resolve_startup_configs(...)`, `StartupSkipReason`, `StartupObservation`, manifest `ServerConfig`, Phase 3 `AuthState`, and Phase 4 startup/status diagnostics
- **Parallel-safe**: no
- **Tasks**:
  - test: Add config-loader tests proving source-aware config status reports project, user, and custom source paths without changing existing server precedence.
  - test: Add startup-policy preview tests for add/remove/set operations, no-op detection, sorted/deduplicated `autoStart` output, preservation of unrelated top-level keys, and legacy `disableAutoStart` conflict diagnostics.
  - test: Add atomic-write tests proving only the selected config path changes and invalid JSON or non-object config files fail before writing.
  - test: Add resolver/status tests for stale `autoStart`, unknown provisioned names, missing auth, and policy-denied rows using the existing `StartupSkipReason` vocabulary.
  - impl: Add compact Pydantic models for config-admin outputs and operations in `src/pmcp/types.py` with additive optional fields only.
  - impl: Add source-aware config discovery helpers in `src/pmcp/config/loader.py` that return parsed config plus path/source metadata instead of only merged server configs.
  - impl: Add preview/apply helpers that operate on structured JSON objects, preserve unrelated keys and server definitions, and write via the existing atomic JSON pattern or a loader-local equivalent.
  - impl: Keep mutation scoped to `autoStart` in this phase; do not introduce arbitrary JSON patch behavior.
  - verify: `uv run pytest tests/test_config_loader.py tests/test_startup_resolver.py -k "config_status or startup_policy or auto_start or disableAutoStart or provisioned or policy_denied or missing_auth"`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/config/loader.py tests/test_config_loader.py tests/test_startup_resolver.py`

### SL-1 - Registry and Server-Card Discovery Normalization

- **Scope**: Normalize stable read-only discovery metadata so CONFIG can improve discovery without letting unstable registry/server-card fields mutate user config automatically.
- **Owned files**: `src/pmcp/manifest/loader.py`, `src/pmcp/manifest/refresher.py`, `tests/test_manifest.py`, `tests/test_refresher.py`
- **Interfaces provided**: normalized discovery metadata fields for package name, transport, server-card URL or identifier, env-var requirements, auth metadata hints, declared capabilities, and non-secret diagnostics
- **Interfaces consumed**: SL-0 discovery/admin output models, existing manifest `ServerConfig`, existing registry result parsing in `GatewayTools.search_registry(...)`, Phase 1 metadata preservation assumptions, Phase 3 auth metadata fields
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add manifest/refresher tests for optional server-card-style metadata fields when present and for unchanged parsing of current `manifest.yaml` entries.
  - test: Add normalization tests proving unknown or draft discovery fields are preserved only as diagnostics or raw metadata hints and do not become required fields.
  - test: Add redaction-oriented tests for discovery diagnostics that include auth URLs or env-var names but no credential values.
  - impl: Extend manifest server config parsing additively for stable discovery hints that can be represented without changing provisioning semantics.
  - impl: Keep registry/server-card support read-only and side-effect free; registration/provisioning continues to require explicit tool calls.
  - impl: Document unstable fields in code-level diagnostics rather than creating permanent public contract fields prematurely.
  - verify: `uv run pytest tests/test_manifest.py tests/test_refresher.py -k "discovery or registry or server_card or auth_metadata or manifest"`
  - verify: `uv run ruff check src/pmcp/manifest/loader.py src/pmcp/manifest/refresher.py tests/test_manifest.py tests/test_refresher.py`

### SL-2 - Gateway Configuration Administration Tools

- **Scope**: Add gateway tools for read-only config status, persisted startup-policy inspection, and explicit startup-policy mutation using the SL-0 contracts.
- **Owned files**: `src/pmcp/tools/handlers.py`, `src/pmcp/server.py`, `tests/test_tools.py`
- **Interfaces provided**: `gateway.config_status`, `gateway.get_startup_policy`, `gateway.set_startup_policy`, tool schemas for startup-policy operations, effective config rows merged with health/startup observations, dry-run preview output, explicit apply output
- **Interfaces consumed**: SL-0 config-admin models and preview/write helpers, SL-1 normalized discovery metadata, existing `GatewayTools.set_startup_observations(...)`, `gateway.health`, `load_manifest()`, `_load_provisioned_registry()`, `_discovered_server_configs`, policy manager, auth-state fields, and audit event redaction behavior
- **Parallel-safe**: no
- **Tasks**:
  - test: Add `gateway.config_status` tests proving configured, manifest, provisioned, discovered, lazy, eager, skipped, missing-auth, policy-denied, stale `autoStart`, and unknown-provisioned entries appear as structured rows with source attribution.
  - test: Add `gateway.get_startup_policy` tests proving project/user/custom `autoStart` and `disableAutoStart` lists are reported separately and include conflict diagnostics.
  - test: Add `gateway.set_startup_policy` tests for dry-run default behavior, explicit apply, selected source path, no-op operations, ambiguous-source refusal, invalid operation refusal, atomic write failure behavior, and preservation of unrelated `.mcp.json` content.
  - test: Add regression tests proving refresh/connect/disconnect/restart runtime lifecycle tools still do not edit `autoStart`.
  - test: Add audit/redaction tests proving config-admin diagnostics do not include secret values from env vars, auth headers, or URLs.
  - impl: Register and dispatch the three gateway tools through existing tool definition and server call paths.
  - impl: Build effective status by merging loaded config sources, manifest/provisioned/discovered registries, startup observations, health/auth states, and diagnostics from SL-0.
  - impl: Route mutation through SL-0 preview/apply helpers and require a single explicit target source/path before writing.
  - impl: After successful apply, return a next step such as `gateway.refresh(reason="startup_policy_changed")` without silently refreshing unless the tool contract explicitly includes and tests that behavior.
  - verify: `uv run pytest tests/test_tools.py -k "config_status or startup_policy or auto_start or disableAutoStart or stale or policy_denied or missing_auth"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py src/pmcp/server.py tests/test_tools.py`

### SL-3 - CLI Setup Profiles and Config Commands

- **Scope**: Expose the new configuration administration through CLI commands and named setup profiles while preserving current `pmcp setup` behavior by default.
- **Owned files**: `src/pmcp/cli.py`, `tests/test_cli.py`, `tests/test_setup_command.py`
- **Interfaces provided**: `pmcp setup --profile <name>`, profile definitions for `local-stdio`, `shared-local-http`, `authenticated-shared-http`, and `ci`, CLI config status/policy commands or subcommands, JSON/text rendering for config-admin outputs
- **Interfaces consumed**: SL-0 config-admin models and setup profile contract, SL-2 gateway tool response shapes, existing `_build_setup_config(...)`, `_merge_setup_config(...)`, `_atomic_write_json(...)`, live status rendering, Phase 3 auth-state display helpers
- **Parallel-safe**: no
- **Tasks**:
  - test: Add parse/render tests for `pmcp setup --profile local-stdio`, `shared-local-http`, `authenticated-shared-http`, and `ci` while keeping existing `--client`/`--mode` defaults unchanged.
  - test: Add write/merge tests proving setup profiles preserve unrelated client config and still require `--write` for filesystem changes.
  - test: Add CLI config status/policy JSON tests proving machine-readable output preserves source paths, diagnostics, and auth/startup states.
  - test: Add CLI text-output tests proving stale `autoStart`, legacy `disableAutoStart`, missing auth, and policy conflicts are distinguishable without printing secret values.
  - test: Add startup-policy mutation CLI tests for preview-only default, explicit apply, selected source/path, and no-op messaging.
  - impl: Add setup profile definitions as a small local mapping or helper near existing setup helpers instead of replacing the current mode/client implementation.
  - impl: Add CLI config-admin commands that call gateway tools when a live gateway is requested and use local SL-0 helpers when operating directly on files, matching existing CLI patterns.
  - impl: Keep `pmcp setup --client claude --mode http` output compatible with existing tests and README examples.
  - verify: `uv run pytest tests/test_cli.py tests/test_setup_command.py -k "config_status or startup_policy or setup or profile or auto_start or disableAutoStart"`
  - verify: `uv run ruff check src/pmcp/cli.py tests/test_cli.py tests/test_setup_command.py`

### SL-4 - End-to-End, Docs, and Roadmap Closeout

- **Scope**: Validate cross-surface CONFIG behavior, document the stable admin contract, and close the Phase 5 checklist only after every producer lane has finished.
- **Owned files**: `tests/test_phase4_e2e.py`, `README.md`, `CHANGELOG.md`, `specs/phase-plans-v3.md`, `plans/phase-plan-v3-config.md`
- **Interfaces provided**: CONFIG end-to-end smoke coverage, user-facing configuration administration docs, completed roadmap checklist
- **Interfaces consumed**: SL-0 admin models and config edit helpers, SL-1 discovery metadata findings, SL-2 gateway tools, SL-3 CLI commands and setup profiles, verification results from all lanes
- **Parallel-safe**: no
- **Tasks**:
  - test: Add end-to-end smoke proving config status reports effective startup/auth/policy state consistently through gateway and CLI JSON.
  - test: Add end-to-end smoke proving startup-policy preview does not edit `.mcp.json`, apply edits exactly the selected source, and `gateway.refresh` sees the updated eager/lazy classification.
  - test: Add end-to-end smoke proving setup profiles render and write expected Claude/OpenCode snippets while preserving existing config keys.
  - test: Add end-to-end smoke or documented manual check for registry/server-card discovery metadata remaining read-only.
  - impl: Update README with `gateway.config_status`, `gateway.get_startup_policy`, `gateway.set_startup_policy`, setup profiles, preview/apply behavior, stale diagnostics, and limitations.
  - impl: Add a CHANGELOG entry for CONFIG if this branch is release-bound.
  - impl: Mark Phase 5 exit criteria complete in `specs/phase-plans-v3.md` only after implementation and verification complete.
  - impl: Mark this plan's interface gates and acceptance criteria complete and record execution deviations.
  - verify: `uv run pytest tests/test_phase4_e2e.py -k "config or startup_policy or setup or profile or discovery"`
  - verify: Manually review markdown formatting in `README.md`, `CHANGELOG.md`, `specs/phase-plans-v3.md`, and `plans/phase-plan-v3-config.md`.

## Verification

Lane-specific verification:

- `uv run pytest tests/test_config_loader.py tests/test_startup_resolver.py -k "config_status or startup_policy or auto_start or disableAutoStart or provisioned or policy_denied or missing_auth"`
- `uv run ruff check src/pmcp/types.py src/pmcp/config/loader.py tests/test_config_loader.py tests/test_startup_resolver.py`
- `uv run pytest tests/test_manifest.py tests/test_refresher.py -k "discovery or registry or server_card or auth_metadata or manifest"`
- `uv run ruff check src/pmcp/manifest/loader.py src/pmcp/manifest/refresher.py tests/test_manifest.py tests/test_refresher.py`
- `uv run pytest tests/test_tools.py -k "config_status or startup_policy or auto_start or disableAutoStart or stale or policy_denied or missing_auth"`
- `uv run ruff check src/pmcp/tools/handlers.py src/pmcp/server.py tests/test_tools.py`
- `uv run pytest tests/test_cli.py tests/test_setup_command.py -k "config_status or startup_policy or setup or profile or auto_start or disableAutoStart"`
- `uv run ruff check src/pmcp/cli.py tests/test_cli.py tests/test_setup_command.py`
- `uv run pytest tests/test_phase4_e2e.py -k "config or startup_policy or setup or profile or discovery"`

Whole-phase regression:

- `uv run pytest tests/test_config_loader.py tests/test_startup_resolver.py tests/test_tools.py tests/test_cli.py tests/test_setup_command.py tests/test_manifest.py tests/test_refresher.py tests/test_phase4_e2e.py -q`
- `uv run pytest tests/test_lazy_start.py tests/test_server.py tests/test_manifest_provision.py tests/test_secrets_command.py -q` because CONFIG consumes startup, health/status, provisioning, and auth-state contracts.
- `uv run ruff check src/ tests/`
- `uv run ruff format --check src/ tests/`
- `uv run mypy src/pmcp --exclude baml_client`
- `uv run pytest -q` before release handoff if time permits.

Manual smoke after implementation:

- `pmcp status --json --pending`
- `pmcp status --verbose --pending`
- `pmcp setup --profile local-stdio`
- `pmcp setup --profile shared-local-http`
- `pmcp setup --profile authenticated-shared-http`
- `pmcp setup --profile ci`
- `pmcp doctor`

## Acceptance Criteria

- [x] PMCP exposes read-only effective configuration/status explaining eager, lazy, skipped, policy-denied, missing-auth, provisioned, discovered, stale, and unknown entries with source attribution.
- [x] `gateway.config_status`, `gateway.get_startup_policy`, and `gateway.set_startup_policy` are additive gateway tools with schemas, typed outputs, dispatch tests, and non-secret diagnostics.
- [x] Startup-policy mutation supports preview and explicit apply for `autoStart` while preserving unrelated `.mcp.json` content and refusing ambiguous source selection.
- [x] No-op edits are reported as no-ops and do not rewrite the target config file.
- [x] PMCP detects stale `autoStart`, legacy `disableAutoStart`, unknown server names, missing auth, and policy conflicts as structured diagnostics.
- [x] `pmcp setup` supports named profiles for local stdio, shared-local HTTP, authenticated shared HTTP, and CI while preserving existing `--client`/`--mode` behavior.
- [x] Registry/server-card-aware discovery is integrated only where stable enough for read-only metadata and explicit provisioning flow improvements.
- [x] Tests cover atomic config edits, no-op edits, conflict detection, profile output/write behavior, discovery metadata normalization, and backward compatibility with existing config files.
- [x] README documents the CONFIG admin tools, setup profiles, preview/apply semantics, source attribution, discovery limits, and non-goals.

## Execution Notes

- Implemented serially in the main Codex thread; no worker fanout was used.
- Registry/server-card support remains read-only metadata normalization and diagnostics only; it does not mutate config or change provisioning semantics.
- Gateway startup-policy apply returns a refresh next step and does not silently reconnect.
