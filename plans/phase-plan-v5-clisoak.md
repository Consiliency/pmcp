# CLISOAK: CLI Exposure Soak, Docs, and Release Gate

## Context

Phase 5 of `specs/phase-plans-v5.md` is the release gate for the CLI-first
discovery roadmap. It depends on the completed REQCLI and CATALOGCLI
interfaces: `GatewayTools.request_capability(...)` can return
`CapabilityResolution(status="use_cli", cli=CLIResolution(...))`, and
`GatewayTools.catalog_search(...)` can return separate
`CatalogSearchOutput.cli_hints` without mixing CLI guidance into MCP
`CapabilityCard` results.

The current worktree already contains the upstream contracts this phase should
soak rather than redesign: `src/pmcp/types.py` defines `CLIHint`,
`CLIResolution`, and `CatalogSearchOutput.cli_hints`;
`src/pmcp/manifest/matcher.py` defines `rank_cli_hints(...)`; and
`src/pmcp/tools/handlers.py` maps available CLI hints into `use_cli` and
catalog hint responses without fetching full CLI help output. Existing handler
tests in `tests/test_tools.py`, matcher tests in `tests/test_manifest.py`,
offline catalog tests in `tests/test_offline_discovery.py`, and progressive
disclosure tests in `tests/test_progressive_disclosure.py` form the regression
base for this release gate.

The roadmap file is staged as a new file at planning time
(`A  specs/phase-plans-v5.md`), so it is not untracked. This plan file did not
exist before planning.

## Interface Freeze Gates

- [x] IF-0-CLISOAK-1 - One `gateway.request_capability` call for a deterministic
  installed CLI task, such as `{"query": "git commits",
  "available_clis": ["git"]}`, returns enough structured information for direct
  Bash/direct CLI use: `status="use_cli"`, `cli.name`, `cli.description`,
  `cli.available`, optional `cli.path`, `cli.help_command`, `cli.examples`,
  `cli.reason`, and a message/recommendation that PMCP is not executing the
  command or provisioning an MCP server.
- [x] IF-0-CLISOAK-2 - One `gateway.catalog_search` call for a deterministic
  installed CLI query, such as `{"query": "git"}` with cached
  `CLIInfo(name="git", path="/usr/bin/git")`, returns enough structured CLI
  hint information in `cli_hints` for direct Bash/direct CLI use while keeping
  all MCP tool cards in `results`.
- [x] IF-0-CLISOAK-3 - Normal discovery responses preserve the compactness
  boundary: request and catalog discovery do not execute native CLI commands,
  do not fetch or embed full CLI help output, and do not serialize
  `help_output` in the compact `CLIHint`/normal `CLIResolution` paths.
- [x] IF-0-CLISOAK-4 - MCP-first routes remain intact: explicit MCP server
  requests, `prefer_mcp_for` phrases such as GitHub issues and pull requests,
  offline catalog cards, and non-CLI progressive-disclosure searches do not
  become `use_cli` or CLI pseudo-tools.
- [x] IF-0-CLISOAK-5 - README documents the intended branch: start with
  `gateway.request_capability` or `gateway.catalog_search`; use Bash/direct CLI
  when PMCP returns CLI guidance; otherwise continue with MCP provisioning,
  description, and invocation.
- [x] IF-0-CLISOAK-6 - CHANGELOG records the CLI-first discovery behavior with
  precise release wording: PMCP exposes native CLI hints for installed CLIs; it
  does not execute shell commands and does not add a general `pmcp invoke`
  transport.
- [x] IF-0-CLISOAK-7 - Full release verification passes before any version bump
  or publish step. This phase does not bump `pyproject.toml` or
  `src/pmcp/__init__.py` unless execution is explicitly release-bound.

## Lane Index & Dependencies

- SL-0 - One-call CLI discovery soak tests; Depends on: (none); Blocks: SL-2, SL-3; Parallel-safe: yes
- SL-1 - Compactness and regression test hardening; Depends on: (none); Blocks: SL-2, SL-3; Parallel-safe: yes
- SL-2 - README CLI-first flow docs; Depends on: SL-0, SL-1; Blocks: SL-3; Parallel-safe: yes
- SL-3 - Release notes, verification, and closeout; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - One-call CLI Discovery Soak Tests

- **Scope**: Add release-gate smoke tests proving a single PMCP discovery call
  can teach a model the direct CLI path for both request-capability and
  catalog-search entry points.
- **Owned files**: `tests/test_phase4_e2e.py`
- **Interfaces provided**: `test_clisoak_request_capability_one_call_returns_direct_cli_guidance`,
  `test_clisoak_catalog_search_one_call_returns_direct_cli_hint`,
  deterministic fake CLI availability for CLISOAK release smoke coverage
- **Interfaces consumed**: `GatewayTools.request_capability(...)`,
  `GatewayTools.catalog_search(...)`, `CLIInfo`, `CLIAlternative`, `Manifest`,
  `ServerConfig`, `CapabilityResolution(status="use_cli")`,
  `CatalogSearchOutput.cli_hints`, existing `CapabilityCard` separation rules
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a deterministic request-capability smoke using a fake manifest
    with a `git` CLI alternative and `available_clis=["git"]`; assert the
    first response contains `status="use_cli"`, compact CLI fields, examples,
    help command, `available is True`, no `help_output`, and a message that
    tells the model to use Bash/direct CLI without PMCP execution.
  - test: In the request-capability smoke, monkeypatch `load_configs(...)` to
    avoid local config influence and keep the test independent of host MCP
    server state.
  - test: Add a catalog-search smoke using cached
    `CLIInfo(name="git", path="/usr/bin/git")`; assert the first response
    includes a `git` `cli_hints` entry with path, help command, examples, and
    reason while all `results` entries remain MCP `CapabilityCard` values.
  - test: Serialize both responses with `model_dump(exclude_none=True)` or JSON
    and assert they contain no full help text and no `help_output` key in the
    normal compact discovery path.
  - test: Use only fake manifest data, cached CLI info, and in-memory
    `GatewayTools` state; do not require a live Git binary, Docker daemon,
    credentials, or network access.
  - impl: Reuse local test construction patterns already present in
    `tests/test_phase4_e2e.py` for `GatewayTools`, `ClientManager`,
    `PolicyManager`, and in-memory `ToolInfo` objects.
  - verify: `uv run pytest tests/test_phase4_e2e.py -k "clisoak or cli"`
  - verify: `uv run ruff check tests/test_phase4_e2e.py`

### SL-1 - Compactness and Regression Test Hardening

- **Scope**: Tighten existing handler, matcher, offline, and progressive
  disclosure tests around compact CLI fields and MCP behavior that must not
  regress during the soak.
- **Owned files**: `tests/test_tools.py`, `tests/test_manifest.py`,
  `tests/test_offline_discovery.py`, `tests/test_progressive_disclosure.py`
- **Interfaces provided**: strengthened compactness assertions for
  `CLIHint`/`CLIResolution`, regression coverage for `use_cli`, `cli_hints`,
  `prefer_mcp_for`, offline catalog totals, and non-CLI progressive searches
- **Interfaces consumed**: `CLIHint.model_dump(...)`,
  `CLIResolution.help_output`, `rank_cli_hints(...)`,
  `GatewayTools._resolve_cli_availability(...)`, existing
  `TestCatalogSearch`, `TestCapabilityRequest`, offline discovery fixtures, and
  progressive-disclosure catalog fixtures
- **Parallel-safe**: yes
- **Tasks**:
  - test: Strengthen the compact model tests so `CLIHint` dumps only the compact
    hint fields and never exposes `help_output`.
  - test: Strengthen request-capability coverage so the normal `use_cli` path
    has `cli.help_output is None`, includes `help_command` and `examples`, and
    preserves the "PMCP does not execute the CLI" message.
  - test: Strengthen catalog coverage so matching CLI hints remain separate
    from MCP cards and queryless catalog search still returns `cli_hints == []`
    without probing CLIs.
  - test: Keep or add regression coverage showing `prefer_mcp_for` suppresses
    `git` for GitHub issues and pull requests, and explicit MCP/server intent
    still returns server candidates.
  - test: Keep or add offline catalog assertions showing `include_offline=True`
    can coexist with CLI hints without counting hints in `total_available`.
  - test: Update progressive-disclosure expectations only where additive
    `cli_hints=[]` fields need explicit compatibility assertions for non-CLI
    searches.
  - impl: Do not change public runtime contracts in this lane unless a failing
    test exposes a CLISOAK blocker that must be fed back into the owning
    REQCLI/CATALOGCLI code.
  - verify: `uv run pytest tests/test_tools.py -k "cli_hint or cli_hints or use_cli or request_capability or catalog_search or sync_environment"`
  - verify: `uv run pytest tests/test_manifest.py -k "rank_cli_hints or cli"`
  - verify: `uv run pytest tests/test_offline_discovery.py -k "catalog_search or offline"`
  - verify: `uv run pytest tests/test_progressive_disclosure.py -k "search"`
  - verify: `uv run ruff check tests/test_tools.py tests/test_manifest.py tests/test_offline_discovery.py tests/test_progressive_disclosure.py`

### SL-2 - README CLI-first Flow Docs

- **Scope**: Document the final CLI-first discovery branch as a product flow
  without implying that PMCP executes shell commands or turns CLIs into MCP
  tools.
- **Owned files**: `README.md`
- **Interfaces provided**: README flow text and examples for
  `status="use_cli"`, `cli_hints`, direct Bash/direct CLI follow-up,
  MCP fallback through provisioning/description/invocation, and compactness
  boundaries
- **Interfaces consumed**: SL-0 one-call request and catalog smoke contracts,
  SL-1 compactness and MCP regression results, existing README progressive
  disclosure sections, `IF-0-REQCLI-3`, `IF-0-CATALOGCLI-4`
- **Parallel-safe**: yes, after SL-0 and SL-1 freeze the tested response shape
- **Tasks**:
  - test: Review the README request-capability example against the tested
    `use_cli` response and include `status`, `message`, `cli.name`,
    `cli.available`, `cli.help_command`, `cli.examples`, and `cli.reason`
    without `help_output`.
  - test: Review the README catalog-search example against the tested
    `cli_hints` response and include separate `results` and `cli_hints`
    arrays without any CLI pseudo `tool_id`.
  - impl: Clarify that either `gateway.request_capability` or
    `gateway.catalog_search` can be the first discovery call; a `use_cli`
    response or matching `cli_hints` entry is enough context for the model to
    use Bash/direct CLI.
  - impl: Clarify that the MCP path remains provisioning, describing, and
    invoking MCP tools when PMCP returns server candidates or tool cards rather
    than CLI guidance.
  - impl: State the compactness boundary: PMCP returns help commands and curated
    examples, not full native CLI help dumps.
  - impl: Avoid documenting a new gateway tool, a new PMCP CLI command, or a
    general `pmcp invoke` transport.
  - verify: `rg -n "use_cli|cli_hints|help_command|help_output|Bash/direct CLI|gateway.provision|gateway.invoke" README.md`

### SL-3 - Release Notes, Verification, and Closeout

- **Scope**: Record the precise release claim, run the release-gate command
  set, and reduce producer-lane results into the final CLISOAK checklist.
- **Owned files**: `CHANGELOG.md`, `plans/phase-plan-v5-clisoak.md`
- **Interfaces provided**: CHANGELOG CLI-first discovery note, release
  verification evidence when release-bound, completed CLISOAK checklist,
  execution notes, any explicit deviations or follow-up blockers
- **Interfaces consumed**: SL-0 smoke-test results, SL-1 regression results,
  SL-2 README docs, Phase 5 exit criteria from `specs/phase-plans-v5.md`,
  whole-roadmap verification commands, current package version state in
  `pyproject.toml` and `src/pmcp/__init__.py`
- **Parallel-safe**: no
- **Tasks**:
  - test: Run the targeted CLISOAK commands from SL-0, SL-1, and SL-2 and map
    each Phase 5 exit criterion to a named test, README section, changelog
    entry, or explicit release decision.
  - test: Run the whole-roadmap release verification command set before any
    version bump or publish decision: targeted manifest/tools/phase4 tests,
    `ruff check`, `ruff format --check`, `mypy`, full `pytest`, and `uv build`.
  - impl: Add a CHANGELOG entry under `[Unreleased]` unless execution is
    explicitly release-bound to a new version section.
  - impl: Phrase the CHANGELOG claim precisely: PMCP exposes compact native CLI
    hints for installed CLIs during discovery and lets the model use the shell
    directly; PMCP does not execute the CLI or add a general `pmcp invoke`
    transport.
  - impl: If release-bound and all verification passes, add concise release
    verification evidence in the style of existing release sections.
  - impl: Do not edit `pyproject.toml` or `src/pmcp/__init__.py` for a version
    bump unless the user explicitly asks for a release bump/publish step.
  - impl: Update this plan's interface gates, execution notes, verification
    summary, and acceptance criteria only after producer lanes and release
    verification have completed.
  - impl: Record any CLISOAK blocker that forces a runtime contract change as a
    feedback item against the owning upstream phase instead of silently
    widening the Phase 5 scope.
  - verify: `uv run pytest tests/test_manifest.py -k "cli or manifest or keyword"`
  - verify: `uv run pytest tests/test_tools.py -k "request_capability or catalog_search or sync_environment or cli"`
  - verify: `uv run pytest tests/test_phase4_e2e.py -k "catalog or capability or cli or clisoak"`
  - verify: `uv run ruff check src/ tests/`
  - verify: `uv run ruff format --check src/ tests/`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `uv run pytest -q`
  - verify: `uv build`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_phase4_e2e.py -k "clisoak or cli"`
- `uv run ruff check tests/test_phase4_e2e.py`
- `uv run pytest tests/test_tools.py -k "cli_hint or cli_hints or use_cli or request_capability or catalog_search or sync_environment"`
- `uv run pytest tests/test_manifest.py -k "rank_cli_hints or cli"`
- `uv run pytest tests/test_offline_discovery.py -k "catalog_search or offline"`
- `uv run pytest tests/test_progressive_disclosure.py -k "search"`
- `uv run ruff check tests/test_tools.py tests/test_manifest.py tests/test_offline_discovery.py tests/test_progressive_disclosure.py`
- `rg -n "use_cli|cli_hints|help_command|help_output|Bash/direct CLI|gateway.provision|gateway.invoke" README.md`

Whole-phase regression:

- `uv run pytest tests/test_manifest.py -k "cli or manifest or keyword"`
- `uv run pytest tests/test_tools.py -k "request_capability or catalog_search or sync_environment or cli"`
- `uv run pytest tests/test_phase4_e2e.py -k "catalog or capability or cli or clisoak"`
- `uv run pytest tests/test_offline_discovery.py -k "catalog_search or offline"`
- `uv run pytest tests/test_progressive_disclosure.py -k "search"`
- `rg -n "CLI-first|native CLI|use_cli|cli_hints|help_output|does not execute|pmcp invoke" README.md CHANGELOG.md plans/phase-plan-v5-clisoak.md`

Release-bound broader checks:

- `uv run ruff check src/ tests/`
- `uv run ruff format --check src/ tests/`
- `uv run mypy src/pmcp --exclude baml_client`
- `uv run pytest -q`
- `uv build`

## Execution Notes

- SL-0 complete: `tests/test_phase4_e2e.py` now contains deterministic one-call
  CLISOAK smoke tests for both `gateway.request_capability(...)` and
  `gateway.catalog_search(...)`, using only fake manifest data and in-memory
  gateway state.
- SL-1 complete: existing handler, matcher, offline-catalog, and
  progressive-disclosure coverage already exercised the compact CLI contract,
  `prefer_mcp_for` suppression, queryless `cli_hints == []`, and MCP-card
  separation without requiring runtime changes in this phase.
- SL-2 complete: `README.md` now documents the final CLI-first flow from either
  discovery entry point, includes compact `use_cli` and `cli_hints` examples,
  and states that PMCP does not execute the recommended CLI or expose full help
  dumps in the normal path.
- SL-3 complete: `CHANGELOG.md` now records the unreleased CLI-first discovery
  behavior with precise wording that PMCP surfaces compact native CLI guidance
  but does not add a general `pmcp invoke` transport for CLIs.
- No intentional deviation: CLISOAK closed as a soak/documentation/release gate
  only; no version bump was made because execution was not explicitly
  release-bound.

## Verification Results

- Passed: `uv run pytest tests/test_phase4_e2e.py -k "clisoak or cli"` (2 passed).
- Passed: `uv run ruff check tests/test_phase4_e2e.py`.
- Passed: `uv run pytest tests/test_tools.py -k "cli_hint or cli_hints or use_cli or request_capability or catalog_search or sync_environment"` (24 passed, 102 deselected).
- Passed: `uv run pytest tests/test_manifest.py -k "rank_cli_hints or cli"` (13 passed, 62 deselected).
- Passed: `uv run pytest tests/test_offline_discovery.py -k "catalog_search or offline"` (14 passed).
- Passed: `uv run pytest tests/test_progressive_disclosure.py -k "search"` (13 passed, 3 skipped, 11 deselected).
- Passed: `rg -n "use_cli|cli_hints|help_command|help_output|Bash/direct CLI|gateway.provision|gateway.invoke" README.md`.
- Passed: `uv run pytest tests/test_manifest.py -k "cli or manifest or keyword"` (74 passed, 1 skipped).
- Passed: `uv run pytest tests/test_tools.py -k "request_capability or catalog_search or sync_environment or cli"` (24 passed, 102 deselected).
- Passed: `uv run pytest tests/test_phase4_e2e.py -k "catalog or capability or cli or clisoak"` (2 passed, 9 deselected).
- Passed: `rg -n "CLI-first|native CLI|use_cli|cli_hints|help_output|does not execute|pmcp invoke" README.md CHANGELOG.md plans/phase-plan-v5-clisoak.md`.
- Passed: `uv run ruff check src/ tests/`.
- Passed: `uv run ruff format --check src/ tests/`.
- Passed: `uv run mypy src/pmcp --exclude baml_client`.
- Passed: `uv run pytest -q` (1772 passed, 12 skipped, 21 deselected).
- Passed: `uv build`.

## Acceptance Criteria

- [x] README documents the intended flow: call `gateway.request_capability` or
  `gateway.catalog_search`, use Bash/direct CLI when PMCP returns CLI guidance,
  and otherwise continue through MCP provisioning, description, and invocation.
- [x] Release-gate tests prove one `gateway.request_capability` call returns
  enough compact CLI information for direct Bash/direct CLI use without a
  second PMCP MCP call.
- [x] Release-gate tests prove one `gateway.catalog_search` call returns enough
  compact `cli_hints` information for direct Bash/direct CLI use without
  turning CLI hints into MCP capability cards.
- [x] Compactness tests prove normal discovery responses include help commands
  and curated examples, not full native CLI help dumps or serialized
  `help_output`.
- [x] Existing progressive-disclosure and offline catalog tests pass with the
  additive CLI fields while preserving MCP result-card and `total_available`
  semantics.
- [x] CHANGELOG records the CLI-first discovery behavior when release-bound,
  using precise wording that PMCP exposes native CLI hints but does not execute
  shell commands itself.
- [x] Full release verification passes before any version bump or publish step.
- [x] No unrelated gateway tools, PMCP CLI commands, live Docker daemon
  requirements, cloud credentials, or network-dependent tests are introduced by
  CLISOAK.
