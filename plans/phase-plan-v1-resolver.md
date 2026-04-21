# RESOLVER: Shared Startup Resolver

## Context

Phase 2 creates a shared startup resolver contract without changing gateway runtime startup behavior. Phase 1 added the `.mcp.json` `autoStart` field and `load_enabled_auto_start(...)`; today `GatewayServer.initialize()` still independently builds lazy `.mcp.json` configs and eager legacy manifest `auto_start` configs, while `gateway.refresh` independently reloads configs, legacy manifest auto-start servers, and provisioned servers.

This phase should introduce a pure resolver that classifies downstream servers into lazy, eager, and skipped groups from already-loaded inputs. It must not open network connections, spawn processes, mutate `ClientManager`, or flip runtime eager startup policy yet. Phase 3 will wire this resolver into `GatewayServer.initialize()` and `gateway.refresh` as the behavior migration.

## Interface Freeze Gates

- [ ] IF-0-RESOLVER-1 — `pmcp.config.loader.resolve_startup_configs(...)` is a pure function that accepts configured `ResolvedServerConfig` entries, manifest servers, enabled `autoStart` names, disabled `disableAutoStart` names, provisioned server names, a server policy predicate, and an auth-availability predicate, and returns a structured result without connecting to servers or reading config files.
- [ ] IF-0-RESOLVER-2 — The resolver result exposes disjoint `lazy_configs: list[ResolvedServerConfig]`, `eager_configs: list[ResolvedServerConfig]`, and `skipped: list[StartupSkip]` collections with deterministic source precedence: configured project/user/custom server definitions win over manifest definitions on name collision.
- [ ] IF-0-RESOLVER-3 — Explicit `autoStart` names classify matching configured servers and manifest-only servers as eager; configured servers not listed in `autoStart` remain lazy; manifest-only servers not listed in `autoStart` remain lazy unless the caller opts into legacy manifest auto-start compatibility.
- [ ] IF-0-RESOLVER-4 — Provisioned registry server names are classified lazy by default, resolved from manifest definitions when available, and become eager only when also listed in `autoStart`.
- [ ] IF-0-RESOLVER-5 — Policy-denied servers and missing-auth eager servers are excluded from both lazy and eager outputs and represented in `skipped` with machine-readable reasons.
- [ ] IF-0-RESOLVER-6 — `GatewayServer.initialize()` and `GatewayTools.refresh()` runtime behavior remains unchanged in Phase 2; existing manifest auto-start and refresh behavior continue until Phase 3 wiring.

## Lane Index & Dependencies

- SL-0 — Resolver contract and model; Depends on: (none); Blocks: SL-1, SL-2, SL-3; Parallel-safe: no
- SL-1 — Resolver unit coverage; Depends on: SL-0; Blocks: SL-3; Parallel-safe: yes
- SL-2 — Runtime compatibility guard coverage; Depends on: SL-0; Blocks: SL-3; Parallel-safe: yes
- SL-3 — Phase review and documentation decision; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Resolver contract and model

- **Scope**: Add the pure startup resolver API and structured result/skip models while reusing existing server config conversion helpers.
- **Owned files**: `src/pmcp/config/loader.py`
- **Interfaces provided**: `StartupResolution`, `StartupSkip`, `StartupSkipReason`, `resolve_startup_configs(...)`
- **Interfaces consumed**: pre-existing `ResolvedServerConfig`, `McpServerConfig`, `RemoteMcpServerConfig`, `LocalMcpServerConfig`, `manifest_server_to_config(...)`, `pmcp.manifest.loader.ServerConfig`, caller-provided `is_server_allowed(name) -> bool`, caller-provided `is_auth_available(env_var) -> bool`
- **Parallel-safe**: no
- **Tasks**:
  - test: Define expected resolver result shapes in SL-1 before or alongside the implementation.
  - impl: Add small structured result models in `src/pmcp/config/loader.py`, preferably dataclasses or Pydantic models consistent with nearby code.
  - impl: Add `resolve_startup_configs(...)` with deterministic inputs rather than hidden file reads; callers pass already-loaded config lists, manifest server mappings or iterables, startup policy sets, provisioned names, policy/auth predicates, and a legacy manifest auto-start compatibility flag.
  - impl: Build a name-index where configured `ResolvedServerConfig` entries take precedence over manifest-derived configs; preserve input ordering for deterministic tests.
  - impl: Classify configured servers as lazy unless explicitly enabled by `autoStart`; classify manifest-only explicit `autoStart` names as eager; classify provisioned manifest names as lazy unless explicitly enabled.
  - impl: Exclude policy-denied servers and missing-auth eager servers from outputs and add skip entries with reasons such as `policy_denied`, `missing_auth`, `unknown_auto_start`, and `unknown_provisioned`.
  - impl: Keep `load_configs(...)`, `load_enabled_auto_start(...)`, `load_disabled_auto_start(...)`, and `manifest_server_to_config(...)` behavior unchanged.
  - verify: `uv run ruff check src/pmcp/config/loader.py`

### SL-1 — Resolver unit coverage

- **Scope**: Prove the resolver contract across configured, manifest, provisioned, policy-denied, unknown, and missing-auth inputs without touching runtime startup.
- **Owned files**: `tests/test_startup_resolver.py`
- **Interfaces provided**: contract tests for `resolve_startup_configs(...)`
- **Interfaces consumed**: `StartupResolution`, `StartupSkip`, `StartupSkipReason`, `resolve_startup_configs(...)`, `ResolvedServerConfig`, `LocalMcpServerConfig`, `RemoteMcpServerConfig`, `pmcp.manifest.loader.ServerConfig`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add configured local server coverage where a `.mcp.json` server not listed in `autoStart` is lazy and the same server listed in `autoStart` is eager.
  - test: Add configured remote server coverage to prove remote `ResolvedServerConfig` entries are preserved and classified without local command assumptions.
  - test: Add manifest-only explicit `autoStart` coverage where `manifest_server_to_config(...)` output is eager.
  - test: Add collision coverage proving a configured server definition wins over a manifest definition with the same name.
  - test: Add provisioned registry coverage proving provisioned manifest names are lazy by default and eager only when explicitly listed in `autoStart`.
  - test: Add unknown `autoStart` and unknown provisioned-name coverage with skip reasons and no output config.
  - test: Add policy-denied coverage proving denied names are excluded from lazy and eager outputs with a skip reason.
  - test: Add missing-auth coverage proving an eager server that requires an unavailable env var is skipped with a clear reason while non-eager availability remains lazy when appropriate.
  - verify: `uv run pytest tests/test_startup_resolver.py`

### SL-2 — Runtime compatibility guard coverage

- **Scope**: Preserve Phase 2 non-goal behavior by guarding that startup and refresh still use their current runtime paths until Phase 3.
- **Owned files**: `tests/test_lazy_start.py`, `tests/test_tools.py`
- **Interfaces provided**: regression coverage that Phase 2 does not flip runtime eager startup policy
- **Interfaces consumed**: existing `GatewayServer.initialize()`, existing `GatewayTools.refresh(...)`, existing `load_configs(...)`, existing `load_disabled_auto_start(...)`, existing manifest `auto_start`, existing provisioned registry restore behavior
- **Parallel-safe**: yes
- **Tasks**:
  - test: Adjust or add `GatewayServer.initialize()` tests to assert `.mcp.json` configs remain lazy and legacy manifest `auto_start` configs are still eagerly connected in Phase 2.
  - test: Add a guard that Phase 1 `autoStart` config data is not yet consumed by `GatewayServer.initialize()` for runtime eager startup.
  - test: Add or update `GatewayTools.refresh(...)` tests to assert current refresh behavior remains unchanged, including policy filtering and provisioned registry restore behavior.
  - impl: Do not change `src/pmcp/server.py` or `src/pmcp/tools/handlers.py` in this phase except if tests reveal a narrow bug in existing behavior; any runtime wiring belongs to Phase 3.
  - verify: `uv run pytest tests/test_lazy_start.py -k "auto_start or lazy or initialize"`
  - verify: `uv run pytest tests/test_tools.py -k "refresh or provisioned"`

### SL-3 — Phase review and documentation decision

- **Scope**: Review the resolver contract against the roadmap, decide whether user docs should change, and run the phase checks.
- **Owned files**: `README.md`, `specs/phase-plans-v1.md`
- **Interfaces provided**: documentation decision and Phase 2 roadmap status update if implementation completes
- **Interfaces consumed**: `resolve_startup_configs(...)` contract from SL-0, resolver test coverage from SL-1, runtime compatibility evidence from SL-2, Phase 2 non-goal that runtime behavior is unchanged
- **Parallel-safe**: no
- **Tasks**:
  - test: Manually review README startup-policy wording for consistency with a non-runtime resolver contract.
  - impl: Prefer no README change unless Phase 2 exposes a user-visible behavior or public API that users should call directly.
  - impl: If Phase 2 is completed, update `specs/phase-plans-v1.md` Phase 2 status and checked exit criteria in the same style as Phase 1.
  - verify: `uv run pytest tests/test_startup_resolver.py tests/test_lazy_start.py tests/test_tools.py`
  - verify: `uv run ruff check src/pmcp/config/loader.py tests/test_startup_resolver.py tests/test_lazy_start.py tests/test_tools.py`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_startup_resolver.py`
- `uv run pytest tests/test_lazy_start.py -k "auto_start or lazy or initialize"`
- `uv run pytest tests/test_tools.py -k "refresh or provisioned"`
- `uv run ruff check src/pmcp/config/loader.py tests/test_startup_resolver.py tests/test_lazy_start.py tests/test_tools.py`

Whole-phase regression:

- `uv run pytest tests/test_config_loader.py tests/test_startup_resolver.py tests/test_lazy_start.py tests/test_tools.py`
- `uv run pytest tests/test_server_lifecycle.py tests/test_server.py -k "initialize or shutdown or lifecycle"`
- `uv run pytest` before handing off the phase if time permits.

## Acceptance Criteria

- [ ] `resolve_startup_configs(...)` returns separate lazy, eager, and skipped collections without process startup, network I/O, `ClientManager` mutation, or config-file discovery.
- [ ] Explicit `autoStart` can classify configured local servers, configured remote servers, and manifest-only servers as eager in resolver output.
- [ ] Configured server definitions take precedence over manifest defaults when names collide.
- [ ] Provisioned registry servers are classified lazy by default and eager only when explicitly listed in `autoStart`.
- [ ] Policy-denied servers are excluded from lazy and eager outputs and reported in `skipped`.
- [ ] Missing-auth eager servers are skipped with a machine-readable reason and enough detail for later status/logging.
- [ ] Unknown `autoStart` and unknown provisioned names are reported without raising.
- [ ] Existing `GatewayServer.initialize()` legacy manifest auto-start behavior remains unchanged in Phase 2.
- [ ] Existing `gateway.refresh` behavior remains unchanged in Phase 2.
