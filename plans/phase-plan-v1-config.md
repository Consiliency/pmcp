# CONFIG: Startup Policy Config Contract

## Context

**Status**: Completed in `4ebb81c`.

Phase 1 defines the user-facing `.mcp.json` startup policy contract without changing gateway runtime startup behavior. Today `McpConfigFile` accepts `mcpServers` and `disableAutoStart`, `load_configs(...)` aggregates project, user, and custom server definitions with project > user > custom precedence, and `load_disabled_auto_start(...)` unions disabled names across the same sources. This phase adds `autoStart` as the explicit eager-start policy field, keeps `disableAutoStart` compatibility, and documents the distinction between configured availability and eager startup.

Runtime use of `autoStart` is intentionally deferred to Phase 2 and Phase 3. `GatewayServer.initialize()`, manifest `auto_start`, and `gateway.refresh` should continue to behave as they do before this phase.

## Interface Freeze Gates

- [x] IF-0-CONFIG-1 — `pmcp.types.McpConfigFile` accepts top-level camelCase `autoStart: list[str]` with default `[]`, keeps `disableAutoStart: list[str]` unchanged, ignores unrelated extra fields, and does not accept or document a snake_case startup alias.
- [x] IF-0-CONFIG-2 — `pmcp.config.loader.load_enabled_auto_start(project_root=None, user_config_paths=None, custom_config_path=None) -> set[str]` reads `autoStart` from project, user, and custom config sources using the same discovery inputs as `load_disabled_auto_start(...)` and returns the union of all configured names.
- [x] IF-0-CONFIG-3 — `load_disabled_auto_start(...)` behavior is preserved exactly: it continues to read `disableAutoStart` from project, user, and custom config sources and returns the union of all disabled names.
- [x] IF-0-CONFIG-4 — `load_configs(...)`, `_coerce_server_entry(...)`, `LocalMcpServerConfig`, and `RemoteMcpServerConfig` behavior remain unchanged except that parsed config files may now carry the unused top-level `autoStart` field.
- [x] IF-0-CONFIG-5 — README documents `mcpServers` as lazy availability, `autoStart` as explicit eager intent for future startup policy, and `disableAutoStart` as legacy compatibility for manifest auto-start defaults.

## Lane Index & Dependencies

- SL-0 — Config model and loader contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3; Parallel-safe: no
- SL-1 — Loader aggregation tests; Depends on: SL-0; Blocks: SL-3; Parallel-safe: yes
- SL-2 — Config model compatibility tests; Depends on: SL-0; Blocks: SL-3; Parallel-safe: yes
- SL-3 — Documentation and phase review; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Config model and loader contract

- **Scope**: Add the `autoStart` config field and the enabled auto-start loader helper while preserving existing config parsing and disabled auto-start behavior.
- **Owned files**: `src/pmcp/types.py`, `src/pmcp/config/loader.py`
- **Interfaces provided**: `McpConfigFile.autoStart`, `load_enabled_auto_start(...)`
- **Interfaces consumed**: pre-existing `parse_json_file(...)`, `find_project_root(...)`, `DEFAULT_USER_CONFIG_PATHS`, `PMCP_CONFIG`, `McpConfigFile.disableAutoStart`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add failing expectations in downstream test lanes before or alongside implementation for `McpConfigFile.autoStart` and `load_enabled_auto_start(...)`.
  - impl: Add `autoStart: list[str] = Field(default_factory=list)` to `McpConfigFile` in `src/pmcp/types.py`.
  - impl: Add `load_enabled_auto_start(...)` in `src/pmcp/config/loader.py`, mirroring `load_disabled_auto_start(...)` source discovery and union semantics but reading `McpConfigFile.autoStart`.
  - impl: Export/import needs are minimal because `pmcp.config.loader` functions are imported directly from the module in existing tests; do not introduce new package init files unless required by existing style.
  - verify: `uv run pytest tests/test_config_loader.py tests/test_guidance_config.py`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/config/loader.py tests/test_config_loader.py tests/test_guidance_config.py`

### SL-1 — Loader aggregation tests

- **Scope**: Cover enabled and disabled startup policy aggregation across project, user, and custom config sources.
- **Owned files**: `tests/test_config_loader.py`
- **Interfaces provided**: test coverage for `load_enabled_auto_start(...)`, regression coverage for `load_disabled_auto_start(...)`
- **Interfaces consumed**: `McpConfigFile.autoStart`, `McpConfigFile.disableAutoStart`, `load_enabled_auto_start(...)`, `load_disabled_auto_start(...)`, existing temp-file config patterns
- **Parallel-safe**: yes
- **Tasks**:
  - test: Import `load_enabled_auto_start` and `load_disabled_auto_start` from `pmcp.config.loader`.
  - test: Add a project/user/custom aggregation test where `autoStart` names from all sources are returned as one set.
  - test: Add a precedence-specific assertion that unlike server definitions, startup policy name lists are unioned and not overridden by higher-priority sources.
  - test: Add a regression test that `disableAutoStart` still unions project/user/custom names after the new helper is introduced.
  - test: Include missing-file and invalid-json coverage only if it is not already covered indirectly by helper behavior; keep it focused on startup policy helpers.
  - impl: Adjust no production files in this lane.
  - verify: `uv run pytest tests/test_config_loader.py -k "auto_start or AutoStart or disabled"`

### SL-2 — Config model compatibility tests

- **Scope**: Prove that the Pydantic config contract accepts camelCase `autoStart`, keeps `disableAutoStart`, and continues ignoring unrelated fields.
- **Owned files**: `tests/test_guidance_config.py`
- **Interfaces provided**: model-contract tests for `McpConfigFile`
- **Interfaces consumed**: `pmcp.types.McpConfigFile`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Extend `TestMcpConfigFileExtraFields` or add a nearby focused test class to assert `McpConfigFile.model_validate({"autoStart": ["context7"], "mcpServers": {}}).autoStart == ["context7"]`.
  - test: Assert a mixed config containing `mcpServers`, `autoStart`, `disableAutoStart`, and arbitrary extra fields preserves the known fields and ignores extras.
  - test: Assert the default for `autoStart` is `[]` when absent.
  - impl: Adjust no production files in this lane.
  - verify: `uv run pytest tests/test_guidance_config.py -k "McpConfigFile"`

### SL-3 — Documentation and phase review

- **Scope**: Document the startup policy semantics and perform the final consistency pass after implementation and test lanes are complete.
- **Owned files**: `README.md`
- **Interfaces provided**: user-facing config semantics for `mcpServers`, `autoStart`, and `disableAutoStart`
- **Interfaces consumed**: `McpConfigFile.autoStart`, `load_enabled_auto_start(...)`, `load_disabled_auto_start(...)`, Phase 1 non-goal that runtime behavior is unchanged
- **Parallel-safe**: no
- **Tasks**:
  - test: Review README examples manually for consistency with current Phase 1 behavior and future policy wording.
  - impl: In the configuration section, add a concise `.mcp.json` example showing `mcpServers` plus top-level `autoStart`.
  - impl: State that `mcpServers` makes a downstream server available lazily/on demand, while `autoStart` records explicit eager-start intent for the startup policy migration.
  - impl: State that `disableAutoStart` remains a legacy compatibility field for disabling packaged manifest auto-start defaults during migration.
  - impl: Avoid claiming that runtime eager startup is already controlled by `autoStart` in Phase 1.
  - verify: `uv run pytest tests/test_config_loader.py tests/test_guidance_config.py`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/config/loader.py tests/test_config_loader.py tests/test_guidance_config.py`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_config_loader.py -k "auto_start or AutoStart or disabled"`
- `uv run pytest tests/test_guidance_config.py -k "McpConfigFile"`
- `uv run ruff check src/pmcp/types.py src/pmcp/config/loader.py tests/test_config_loader.py tests/test_guidance_config.py`

Whole-phase regression:

- `uv run pytest tests/test_config_loader.py tests/test_guidance_config.py`
- `uv run pytest tests/test_lazy_start.py -k "auto_start or disabled"` to confirm current manifest auto-start behavior remains unchanged where covered.
- `uv run pytest` before handing off the phase if time permits.

## Acceptance Criteria

- [x] `McpConfigFile` accepts `autoStart: list[str]` and defaults it to `[]`.
- [x] `McpConfigFile` still accepts `disableAutoStart: list[str]`, defaults it to `[]`, and ignores unrelated extra fields.
- [x] `load_enabled_auto_start(...)` returns the union of `autoStart` names from project, user, and custom config sources.
- [x] `load_disabled_auto_start(...)` retains its existing union behavior for `disableAutoStart`.
- [x] Tests cover project, user, and custom config aggregation for enabled and disabled auto-start names.
- [x] Local and remote `mcpServers` parsing behavior is unchanged.
- [x] Gateway runtime eager connection behavior is unchanged in this phase.
- [x] README describes the intended distinction between `mcpServers`, `autoStart`, and `disableAutoStart` without overstating Phase 1 runtime behavior.
