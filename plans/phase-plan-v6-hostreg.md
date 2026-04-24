# HOSTREG: Tenant Capability Registration

## Context

Phase 2 of `specs/phase-plans-v6.md` makes a separate tenant code-mode MCP
server discoverable and configurable through PMCP without turning PMCP into a
code runner.

The prerequisite contract is already present in the staged baseline:
`specs/tenant-code-mode-host-contract.md` defines PMCP as the host-side broker
and the tenant code-mode server as the sandbox execution authority. HOSTREG
must consume that contract, not expand it into runtime execution,
tenant-specific auth storage, or a new gateway tool.

Existing PMCP surfaces that HOSTREG should reuse:

- `src/pmcp/manifest/manifest.yaml` already contains curated CLI alternatives,
  remote streamable-HTTP server entries, and lazy packaged server metadata.
- `src/pmcp/manifest/loader.py` parses remote manifest entries with `transport`,
  `url`, `headers`, `requires_api_key`, and `env_var` fields.
- `src/pmcp/tools/handlers.py` merges configured `.mcp.json` servers into a
  manifest view, ranks CLI hints before server candidates, lazy-provisions
  configured remote servers, checks remote header placeholders, and surfaces
  cached offline tool cards in `gateway.catalog_search(include_offline=true)`.
- `README.md` already documents CLI-first `gateway.request_capability`, offline
  catalog search, lazy `autoStart`, remote downstream servers, and
  `${ENV_VAR}` header interpolation.

The current v6 roadmap and HOSTCONTRACT artifacts are staged but not committed.
Execution should preserve that staged baseline and avoid unrelated cleanup.

## Interface Freeze Gates

- [x] IF-0-HOSTREG-1 - PMCP has exactly one tenant code-mode registration path
  for this phase: a documented `.mcp.json` entry named `tenant-code-mode`, or a
  packaged manifest entry only if execution can provide a non-placeholder
  command or remote URL. PMCP must not ship an empty-command placeholder that
  `gateway.provision` would treat as an installable server.
- [x] IF-0-HOSTREG-2 - `gateway.request_capability` can recommend a registered
  `tenant-code-mode` server for hosted sandbox/code-mode requests while
  preserving `status="use_cli"` for local installed CLI requests such as
  `python`, `node`, `git`, or `docker`.
- [x] IF-0-HOSTREG-3 - `gateway.catalog_search(include_offline=true)` can
  surface cached tenant code-mode tool cards for a registered compatible server
  without connecting to the server or fabricating tool schemas.
- [x] IF-0-HOSTREG-4 - Remote streamable-HTTP examples use non-secret
  placeholders such as `${TENANT_CODE_MODE_MCP_TOKEN}` and
  `${TENANT_CODE_MODE_TENANT_ID}` and never print credential values.
- [x] IF-0-HOSTREG-5 - Tests prove tenant code-mode discovery is lazy: it does
  not execute code, start unrelated local processes, provision the companion
  server during discovery, or mix CLI hints into MCP capability cards.

## Lane Index & Dependencies

- SL-0 - Registration baseline and strategy; Depends on: (none); Blocks: SL-1, SL-2, SL-3; Parallel-safe: no
- SL-1 - Manifest and registration contract tests; Depends on: SL-0; Blocks: SL-2, SL-3, SL-4; Parallel-safe: yes
- SL-2 - Capability recommendation and offline catalog behavior; Depends on: SL-0, SL-1; Blocks: SL-3, SL-4; Parallel-safe: no
- SL-3 - README registration examples; Depends on: SL-0, SL-1, SL-2; Blocks: SL-4; Parallel-safe: yes
- SL-4 - Phase verification and closeout; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Registration Baseline and Strategy

- **Scope**: Freeze the HOSTREG execution strategy against the staged
  HOSTCONTRACT output and current PMCP discovery behavior before editing source
  files.
- **Owned files**: none; read-only survey of
  `specs/tenant-code-mode-host-contract.md`, `specs/phase-plans-v6.md`,
  `src/pmcp/manifest/manifest.yaml`, `src/pmcp/manifest/loader.py`,
  `src/pmcp/tools/handlers.py`, `tests/test_tools.py`,
  `tests/test_manifest.py`, and `README.md`
- **Interfaces provided**: registration strategy decision, predecessor contract
  checklist, no-placeholder manifest rule, lazy discovery baseline
- **Interfaces consumed**: IF-0-HOSTCONTRACT-1, Phase 2 exit criteria,
  `ServerConfig`, `RemoteMcpServerConfig`, `_build_manifest_with_config_servers`,
  `_keywords_for_config_server`, `rank_cli_hints(...)`,
  `GatewayTools.catalog_search(...)`, `GatewayTools.request_capability(...)`,
  `manifest_server_to_config(...)`, remote header placeholder behavior
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm `specs/tenant-code-mode-host-contract.md` still describes
    PMCP as broker and the companion server as execution authority.
  - test: Confirm the shipped manifest either has no `tenant-code-mode` entry or
    has a non-placeholder `command` or `url`; an empty-command placeholder is a
    blocker.
  - test: Confirm current remote manifest provisioning connects `url` entries
    directly and checks missing header placeholders before connection.
  - impl: Record the execution strategy in the first commit message or closeout:
    documented registration only unless a real companion command or endpoint is
    available.
  - verify: `git status --short -- specs/phase-plans-v6.md specs/tenant-code-mode-host-contract.md plans/phase-plan-v6-hostcontract.md`
  - verify: `rg -n "Tenant code-mode|execution authority|streamable HTTP|lazy|artifact" specs/tenant-code-mode-host-contract.md`
  - verify: `rg -n "tenant-code-mode|code-mode|sandbox|url:|command:" src/pmcp/manifest/manifest.yaml`

### SL-1 - Manifest and Registration Contract Tests

- **Scope**: Lock the tenant registration contract around real manifest/config
  behavior without requiring the companion repo to be published.
- **Owned files**: `src/pmcp/manifest/manifest.yaml`, `tests/test_manifest.py`
- **Interfaces provided**: manifest registration invariant,
  tenant-code-mode keyword contract, no eager startup invariant, no placeholder
  install invariant
- **Interfaces consumed**: `Manifest`, `ServerConfig`, `load_manifest(...)`,
  `_keyword_match(...)`, `rank_cli_hints(...)`, packaged manifest remote-entry
  patterns such as `excalidraw`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a manifest regression that accepts one of two valid states:
    `tenant-code-mode` is absent from the packaged manifest because registration
    is documented in README, or it exists with a real non-placeholder `url` or
    local `command`.
  - test: If a packaged `tenant-code-mode` entry is added, assert it has
    keywords for `code execution`, `sandbox execution`, `mobile code mode`,
    `task runs`, `logs`, and `artifacts`; `auto_start` is false; and any
    `headers` use `${ENV_VAR}` placeholders.
  - test: If no packaged entry is added, add a synthetic manifest fixture that
    proves a future tenant code-mode `ServerConfig` parses as
    `streamable-http`, keeps `headers` metadata, and remains lazy.
  - test: Add a matcher regression proving tenant code-mode server keywords can
    match hosted sandbox/code-mode requests when the server is registered, while
    CLI hints still win for local CLI-shaped requests.
  - impl: Add the packaged manifest entry only if there is a real companion
    command or real remote endpoint. Otherwise leave `manifest.yaml` unchanged
    and let README own the documented registration path.
  - impl: Keep install fields non-executable for docs-only fixtures; never use a
    test fixture to create a runnable placeholder in the shipped manifest.
  - verify: `uv run pytest tests/test_manifest.py -k "tenant_code_mode or manifest_has_no_packaged_auto_start_defaults or rank_cli_hints"`
  - verify: `git diff --check -- src/pmcp/manifest/manifest.yaml tests/test_manifest.py`

### SL-2 - Capability Recommendation and Offline Catalog Behavior

- **Scope**: Teach PMCP's existing handler discovery path to recommend a
  registered tenant code-mode server for hosted code-mode requests and to expose
  cached tenant tool cards offline, without weakening CLI-first local behavior.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: configured-server tenant keywords, request-capability
  recommendation behavior, offline cached tenant catalog behavior, lazy
  discovery safety tests
- **Interfaces consumed**: SL-1 registration contract, `rank_cli_hints(...)`,
  `_resolve_cli_availability(...)`, `_build_manifest_with_config_servers(...)`,
  `_keywords_for_config_server(...)`, `CapabilityResolution`,
  `CapabilityCandidate`, `CatalogSearchOutput`, `DescriptionsCache`,
  `GeneratedServerDescriptions`, `PrebuiltToolInfo`,
  `_get_cached_tools_for_offline_servers(...)`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add a configured remote server named `tenant-code-mode` using
    `RemoteMcpServerConfig(type="streamable-http", url="https://tenant.example/mcp",
    headers={"Authorization": "Bearer ${TENANT_CODE_MODE_MCP_TOKEN}"})` and
    assert `gateway.request_capability({"query": "hosted sandbox code execution",
    "available_clis": []})` returns a server candidate for `tenant-code-mode`.
  - test: Add a collision regression with the same configured server and
    `available_clis=["python"]` or `["node"]`; local CLI-shaped queries must
    still return `status="use_cli"` and compact `CLIResolution` fields.
  - test: Add a regression that explicit MCP intent such as `tenant code mode
    mcp server` returns a server candidate even when CLI hints exist.
  - test: Add an offline catalog regression with a `DescriptionsCache` entry for
    `tenant-code-mode` containing cached tools such as `run_script`,
    `get_result`, and `cancel_run`; assert
    `gateway.catalog_search({"query": "sandbox", "include_offline": true})`
    returns offline MCP `CapabilityCard` results and keeps `cli_hints`
    separate.
  - test: Assert these discovery calls do not call `ensure_connected(...)`,
    `connect_all(...)`, `get_job_manager().start_install(...)`, or tool
    invocation paths.
  - impl: Extend configured-server keyword construction narrowly enough to make
    a registered `tenant-code-mode` server discoverable by hosted
    sandbox/code-mode/log/artifact requests. Prefer deriving from the configured
    server name and remote transport over adding a broad global special case.
  - impl: Reuse existing `rank_cli_hints(...)` ordering; do not add a
    tenant-specific bypass before the CLI-first branch.
  - impl: Reuse existing cached offline card conversion; add code only if tests
    show the current behavior cannot surface registered tenant tool cards
    without connection.
  - verify: `uv run pytest tests/test_tools.py -k "tenant_code_mode or request_capability_returns_use_cli_for_available_cli or catalog_search"`
  - verify: `git diff --check -- src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-3 - README Registration Examples

- **Scope**: Document remote and local-development tenant server registration
  without printing secrets or implying PMCP owns sandbox execution.
- **Owned files**: `README.md`
- **Interfaces provided**: documented tenant code-mode `.mcp.json` registration
  entry, remote header placeholder example, lazy startup guidance, local
  development mode guidance
- **Interfaces consumed**: SL-1 registration decision, SL-2 request-capability
  behavior, `specs/tenant-code-mode-host-contract.md`, existing README
  sections for Offline Tool Discovery, Dynamic Server Provisioning, Optional
  Eager Startup, and Remote Downstream Servers
- **Parallel-safe**: yes
- **Tasks**:
  - test: Ensure the README example names the configured server
    `tenant-code-mode`, uses `type: "http"` or `type: "streamable-http"`, shows
    a full remote MCP endpoint, and uses header placeholders such as
    `${TENANT_CODE_MODE_MCP_TOKEN}` and `${TENANT_CODE_MODE_TENANT_ID}`.
  - test: Ensure local-development guidance uses a clearly replaceable
    companion-server command and does not claim a package is published unless
    SL-1 added a real packaged manifest entry.
  - test: Ensure the docs say tenant server registration remains lazy unless
    the operator explicitly adds `tenant-code-mode` to `autoStart`.
  - test: Ensure the docs route users through existing
    `gateway.request_capability`, `gateway.catalog_search(include_offline=true)`,
    `gateway.provision`, and `gateway.invoke` surfaces and do not introduce
    `gateway.run_code` or `pmcp execute`.
  - impl: Add a concise subsection near the remote downstream server and tenant
    host contract docs rather than a broad rewrite of README setup guidance.
  - impl: Cross-link the host contract for policy/safety boundaries and defer
    deeper auth/operator guardrails to HOSTPOLICY.
  - verify: `rg -n "tenant-code-mode|TENANT_CODE_MODE_MCP_TOKEN|TENANT_CODE_MODE_TENANT_ID|autoStart|gateway\\.run_code|pmcp execute" README.md`
  - verify: `git diff --check -- README.md`

### SL-4 - Phase Verification and Closeout

- **Scope**: Reduce the registration, handler, catalog, and docs outputs into
  a phase-ready implementation diff and handoff for HOSTMETA.
- **Owned files**: `plans/phase-plan-v6-hostreg.md`
- **Interfaces provided**: completed HOSTREG checklist, verification summary,
  staged artifact state, HOSTMETA handoff notes
- **Interfaces consumed**: SL-0 registration strategy, SL-1 manifest/tests,
  SL-2 handler/tests, SL-3 README docs, Phase 2 exit criteria,
  IF-0-HOSTREG-1 through IF-0-HOSTREG-5
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm every Phase 2 exit criterion maps to a source/test/docs diff
    or to an explicit no-op decision about the packaged manifest entry.
  - test: Confirm no lane added PMCP-owned script execution, tenant auth
    storage, a new gateway tool, a companion package publication dependency, or
    live hosted infrastructure.
  - test: Confirm any final docs or closeout text depends on all prior lane
    findings and does not race with source/test changes.
  - impl: Mark IF-0-HOSTREG gates complete only after the targeted tests and
    diff checks pass.
  - impl: Record that HOSTMETA consumes the registered server name,
    task-capable tool assumptions, offline tool-card fixtures, and lazy remote
    configuration path.
  - verify: `uv run pytest tests/test_manifest.py tests/test_tools.py -k "tenant_code_mode or request_capability or catalog_search or rank_cli_hints"`
  - verify: `uv run ruff check src/pmcp/manifest src/pmcp/tools tests/test_manifest.py tests/test_tools.py`
  - verify: `uv run ruff format --check src/pmcp/manifest src/pmcp/tools tests/test_manifest.py tests/test_tools.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `git diff --check`

## Verification

Lane-specific verification:

- `git status --short -- specs/phase-plans-v6.md specs/tenant-code-mode-host-contract.md plans/phase-plan-v6-hostcontract.md`
- `rg -n "Tenant code-mode|execution authority|streamable HTTP|lazy|artifact" specs/tenant-code-mode-host-contract.md`
- `rg -n "tenant-code-mode|code-mode|sandbox|url:|command:" src/pmcp/manifest/manifest.yaml`
- `uv run pytest tests/test_manifest.py -k "tenant_code_mode or manifest_has_no_packaged_auto_start_defaults or rank_cli_hints"`
- `uv run pytest tests/test_tools.py -k "tenant_code_mode or request_capability_returns_use_cli_for_available_cli or catalog_search"`
- `rg -n "tenant-code-mode|TENANT_CODE_MODE_MCP_TOKEN|TENANT_CODE_MODE_TENANT_ID|autoStart|gateway\\.run_code|pmcp execute" README.md`
- `git diff --check -- src/pmcp/manifest/manifest.yaml src/pmcp/tools/handlers.py tests/test_manifest.py tests/test_tools.py README.md`

Whole-phase regression:

- `uv run pytest tests/test_manifest.py tests/test_tools.py -k "tenant_code_mode or request_capability or catalog_search or rank_cli_hints"`
- `uv run ruff check src/pmcp/manifest src/pmcp/tools tests/test_manifest.py tests/test_tools.py`
- `uv run ruff format --check src/pmcp/manifest src/pmcp/tools tests/test_manifest.py tests/test_tools.py`
- `uv run mypy src/pmcp --exclude baml_client`
- `git diff --check`

No live hosted tenant service, cloud credential, companion package publication,
or full PMCP regression is required for HOSTREG unless execution changes shared
gateway behavior beyond the discovery/registration paths named above.

## Acceptance Criteria

- [x] `tenant-code-mode` has a safe registration path: documented `.mcp.json`
  registration, or a packaged manifest entry only if it has a real command or
  real remote URL.
- [x] Tenant registration keywords cover code execution, sandbox execution,
  mobile code mode, task runs, logs, artifacts, streamable HTTP, and remote
  hosted operation.
- [x] `gateway.request_capability` recommends the registered tenant code-mode
  server for hosted sandbox/code-mode requests.
- [x] CLI-first behavior remains intact for local CLI-shaped requests and
  `status="use_cli"` responses.
- [x] `gateway.catalog_search(include_offline=true)` surfaces cached tenant
  code-mode MCP cards without mixing CLI hints into MCP result cards.
- [x] Discovery tests prove no code execution, no unrelated local process start,
  no companion provisioning, and no live infrastructure dependency.
- [x] README documents remote streamable-HTTP tenant registration with
  `${ENV_VAR}` header placeholders and no secret values.
- [x] README keeps PMCP as broker and the companion tenant server as execution
  authority; no new `gateway.run_code` or `pmcp execute` surface is introduced.

## Execution Closeout

- SL-0 confirmed the execution strategy is documented `.mcp.json`
  registration only for this phase. The packaged manifest remains unchanged
  because there is no real companion command or hosted endpoint to ship.
- SL-1 added manifest regressions proving any future packaged
  `tenant-code-mode` entry must be non-placeholder, lazy, keyword-complete, and
  env-placeholder based. A synthetic streamable-HTTP fixture proves future
  tenant registration parsing without publishing the companion server.
- SL-2 extended configured-server discovery so a registered
  `tenant-code-mode` server can match hosted sandbox/code-mode requests while
  preserving the existing CLI-first branch for local CLI-shaped requests.
  Offline catalog coverage proves cached tenant tool cards surface without
  connecting to the server or mixing CLI hints into MCP cards.
- SL-3 added a concise README subsection for hosted streamable-HTTP and local
  stdio development registration. The examples use placeholder env vars and
  keep PMCP as broker while the companion server remains the execution
  authority.
- HOSTMETA consumes the configured server name `tenant-code-mode`, the
  task-capable tool assumptions (`run_script`, `get_result`, `cancel_run` as
  fixture examples), the offline tool-card fixture shape, and the lazy remote
  configuration path.
- Verification passed:
  `uv run pytest tests/test_manifest.py tests/test_tools.py -k "tenant_code_mode or request_capability or catalog_search or rank_cli_hints"`,
  `uv run ruff check src/pmcp/manifest src/pmcp/tools tests/test_manifest.py tests/test_tools.py`,
  `uv run ruff format --check src/pmcp/manifest src/pmcp/tools tests/test_manifest.py tests/test_tools.py`,
  `uv run mypy src/pmcp --exclude baml_client`, and `git diff --check`.
