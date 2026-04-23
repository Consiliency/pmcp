# CATALOGCLI: Catalog Search CLI Hints

## Context

Phase 4 of `specs/phase-plans-v5.md` depends on the completed CLIHINT and
CLIMATCH interfaces. The current worktree already contains the upstream
contracts this phase should consume: `src/pmcp/types.py` defines `CLIHint`,
`src/pmcp/manifest/matcher.py` defines `CLIHintMatch` and
`rank_cli_hints(...)`, and `GatewayTools._resolve_cli_availability(...)`
preserves path-aware `CLIInfo` metadata from explicit environment sync or
bounded probing.

`gateway.catalog_search` currently returns only MCP `CapabilityCard` results.
`CatalogSearchOutput` has `results`, `total_available`, `truncated`, and
`stale_updates`; `total_available` is computed from MCP tool candidates before
catalog filters are applied. Phase 4 should add query-scoped CLI hints as a
separate additive field without changing MCP result cards, `gateway.describe`,
or `gateway.invoke` semantics.

The roadmap file is staged as a new file at planning time
(`A  specs/phase-plans-v5.md`), so it is not untracked.

## Interface Freeze Gates

- [x] IF-0-CATALOGCLI-1 - `CatalogSearchOutput` defines an additive
  `cli_hints: list[CLIHint] = Field(default_factory=list)` field. Existing
  fields keep their names and meanings.
- [x] IF-0-CATALOGCLI-2 - `CatalogSearchOutput.total_available` continues to
  count MCP tool candidates only. This phase does not add `total_cli_hints`
  unless implementation discovers a compatibility need and documents it.
- [x] IF-0-CATALOGCLI-3 - `GatewayTools.catalog_search(...)` returns CLI hints
  only when `CatalogSearchInput.query` is non-empty; queryless catalog search
  returns `cli_hints=[]` and does not probe CLIs for broad defaults.
- [x] IF-0-CATALOGCLI-4 - Query-scoped hints are produced by
  `_resolve_cli_availability(...)` plus `rank_cli_hints(query, manifest, ...)`
  using available, unsuppressed `CLIHint` matches only.
- [x] IF-0-CATALOGCLI-5 - CLI hints remain separate from
  `CatalogSearchOutput.results`. No CLI hint is converted into a
  `CapabilityCard`, no CLI pseudo `tool_id` is introduced, and
  `gateway.describe` remains scoped to MCP tool IDs.
- [x] IF-0-CATALOGCLI-6 - Catalog filters, `include_offline`, `limit`, and
  `truncated` continue to apply to MCP tool results. CLI hints are driven by
  the query and availability state, not by MCP result filtering.
- [x] IF-0-CATALOGCLI-7 - CLI hints use the compact `CLIHint` fields only:
  command name, description, availability, path when known, check command,
  help command, examples, `prefer_mcp_for`, and reason. They do not fetch or
  include full CLI help output.
- [x] IF-0-CATALOGCLI-8 - Existing `stale_updates` behavior is preserved and
  can coexist with `cli_hints` in the same catalog response.

## Lane Index & Dependencies

- SL-0 - Catalog output contract and handler integration; Depends on: (none); Blocks: SL-1, SL-2; Parallel-safe: no
- SL-1 - README catalog CLI hint docs; Depends on: SL-0; Blocks: SL-2; Parallel-safe: yes
- SL-2 - Phase verification and closeout; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Catalog Output Contract and Handler Integration

- **Scope**: Add query-scoped CLI hints to the catalog response model and
  handler while preserving MCP result-card behavior.
- **Owned files**: `src/pmcp/types.py`, `src/pmcp/tools/handlers.py`,
  `tests/test_tools.py`, `tests/test_offline_discovery.py`
- **Interfaces provided**: `CatalogSearchOutput.cli_hints`,
  query-scoped `GatewayTools.catalog_search(...)` CLI hint construction,
  preserved MCP-only `results`, preserved MCP-only `total_available`
- **Interfaces consumed**: Phase 1 `CLIHint`, Phase 2 `rank_cli_hints(...)`,
  `GatewayTools._resolve_cli_availability(...)`,
  `GatewayTools._detected_cli_infos`, `CatalogSearchInput.query`, existing
  catalog filtering and stale-update logic
- **Parallel-safe**: no
- **Tasks**:
  - test: Add coverage proving a bare `CatalogSearchOutput` or queryless
    `gateway.catalog_search({})` response exposes `cli_hints == []` while
    preserving existing `results`, `total_available`, and `truncated`
    expectations.
  - test: Add
    `test_catalog_search_returns_cli_hints_for_matching_available_cli` using
    the request-test manifest and cached `CLIInfo(name="git", path="/usr/bin/git")`;
    assert query `"git"` returns a compact `git` hint with help command,
    examples, path, and no `help_output`.
  - test: Add
    `test_catalog_search_keeps_cli_hints_separate_from_capability_cards`;
    assert every `result` is a `CapabilityCard` for an MCP tool and no CLI
    pseudo-card or `git::...` tool ID appears.
  - test: Add `test_catalog_search_non_matching_query_omits_cli_hints` proving
    unrelated queries keep `cli_hints == []`.
  - test: Add
    `test_catalog_search_queryless_does_not_probe_cli_hints` by monkeypatching
    `probe_clis(...)` to fail if called, then calling `catalog_search({})`.
  - test: Add or update offline catalog coverage in `tests/test_offline_discovery.py`
    so `include_offline=True` still returns offline MCP cards and can coexist
    with `cli_hints` without counting hints in `total_available`.
  - test: Add stale-update coexistence coverage or extend the existing stale
    update catalog test to assert `stale_updates` and `cli_hints` serialize
    independently.
  - impl: Add `cli_hints` to `CatalogSearchOutput` using the existing
    `CLIHint` contract. If Pydantic forward-reference behavior is awkward,
    move `CLIHint` above `CatalogSearchOutput` without changing its fields.
  - impl: In `catalog_search(...)`, initialize `cli_hints` to `[]` and only
    load the manifest and resolve CLI availability when `parsed.query` is
    non-empty.
  - impl: For query-scoped hints, call `_resolve_cli_availability(manifest)`
    and `rank_cli_hints(parsed.query, manifest, available_clis=detected_clis,
    detected_cli_infos=detected_cli_infos)`, then return the matched
    `CLIHint` objects without fetching full help output.
  - impl: Keep CLI hints out of the MCP `tools` list before and after catalog
    filters, sorting, limiting, card conversion, and stale-update collection.
  - impl: Leave `gateway.describe` unchanged so CLI hints cannot be described
    or invoked through MCP tool IDs.
  - verify: `uv run pytest tests/test_tools.py -k "catalog_search and (cli_hint or cli_hints or queryless or stale)"`
  - verify: `uv run pytest tests/test_offline_discovery.py -k "catalog_search or offline"`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py tests/test_offline_discovery.py`

### SL-1 - README Catalog CLI Hint Docs

- **Scope**: Document the additive catalog-search CLI hint field in the
  progressive-disclosure workflow without expanding release notes.
- **Owned files**: `README.md`
- **Interfaces provided**: README catalog-search example with separate
  `results` and `cli_hints`, compact guidance for using direct CLI after a
  catalog hint
- **Interfaces consumed**: SL-0 `CatalogSearchOutput.cli_hints`, existing
  README progressive-disclosure workflow, Phase 4 non-goals
- **Parallel-safe**: yes, after SL-0 freezes response fields
- **Tasks**:
  - test: Review the README example against the implemented response fields
    and verify it includes `results`, `cli_hints`, `name`, `help_command`, and
    `examples` without `help_output`.
  - impl: Update the `gateway.catalog_search` tool table row to mention
    additive compact CLI hints.
  - impl: Add a compact example under "Step 2: Search Available Tools" showing
    `gateway.catalog_search({ query: "git" })` returning MCP `results` plus
    a separate `cli_hints` array.
  - impl: State that catalog CLI hints are recommendations for Bash/direct CLI
    use and are not invokable via `gateway.describe` or `gateway.invoke`.
  - impl: Leave CHANGELOG and broader release claims for CLISOAK unless
    execution introduces a user-visible contract beyond this phase.
  - verify: `rg -n "cli_hints|catalog_search|help_command|gateway.describe|gateway.invoke|direct CLI" README.md`

### SL-2 - Phase Verification and Closeout

- **Scope**: Confirm Phase 4 satisfies the CATALOGCLI exit criteria and record
  any remaining release-gate work for CLISOAK.
- **Owned files**: `plans/phase-plan-v5-catalogcli.md`
- **Interfaces provided**: completed CATALOGCLI checklist, execution notes,
  verification summary, docs and release-note decision
- **Interfaces consumed**: SL-0 catalog model/handler tests and response
  contract, SL-1 README update, Phase 4 exit criteria from
  `specs/phase-plans-v5.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Review SL-0 and SL-1 verification output and map every Phase 4 exit
    criterion to a named test, README example, or explicit no-op decision.
  - impl: Mark interface gates and acceptance criteria complete only after the
    implementation and verification commands pass.
  - impl: Record that CHANGELOG and end-to-end one-call release claims remain
    deferred to CLISOAK.
  - impl: Record any intentional deviation, especially if execution introduces
    `total_cli_hints` or suppresses CLI hints under filtered catalog searches.
  - verify: `uv run pytest tests/test_tools.py -k "catalog_search or request_capability or sync_environment or cli"`
  - verify: `uv run pytest tests/test_offline_discovery.py -k "catalog_search or offline"`
  - verify: `uv run pytest tests/test_progressive_disclosure.py -k "catalog_search"`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py tests/test_offline_discovery.py`
  - verify: `uv run ruff format --check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py tests/test_offline_discovery.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`

## Execution Notes

- SL-0 complete: `CatalogSearchOutput.cli_hints` was added as an additive
  compact `CLIHint` list, and `GatewayTools.catalog_search(...)` now constructs
  query-scoped CLI hints from `_resolve_cli_availability(...)` and
  `rank_cli_hints(...)`.
- SL-0 complete: CLI hints are not added to the MCP `tools` collection, are not
  converted into `CapabilityCard` entries, do not introduce CLI pseudo tool IDs,
  and do not change `gateway.describe` or `gateway.invoke`.
- SL-0 complete: no `total_cli_hints` field was introduced. `total_available`,
  `limit`, `truncated`, `include_offline`, filters, and `stale_updates` remain
  MCP-result scoped.
- SL-1 complete: README documents catalog CLI hints as separate Bash/direct CLI
  recommendations and shows `results` and `cli_hints` independently.
- CHANGELOG and end-to-end release-soak claims remain deferred to CLISOAK.
- Verification note: the planned
  `uv run pytest tests/test_progressive_disclosure.py -k "catalog_search"`
  command selected zero tests in the current file naming scheme. The closest
  meaningful progressive-disclosure coverage was run with `-k "search"`.

## Verification

Lane-specific verification:

- `uv run pytest tests/test_tools.py -k "catalog_search and (cli_hint or cli_hints or queryless or stale)"`
- `uv run pytest tests/test_offline_discovery.py -k "catalog_search or offline"`
- `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py tests/test_offline_discovery.py`
- `rg -n "cli_hints|catalog_search|help_command|gateway.describe|gateway.invoke|direct CLI" README.md`

Whole-phase regression:

- `uv run pytest tests/test_tools.py -k "catalog_search or request_capability or sync_environment or cli"`
- `uv run pytest tests/test_offline_discovery.py -k "catalog_search or offline"`
- `uv run pytest tests/test_progressive_disclosure.py -k "catalog_search"`
- `uv run pytest tests/test_manifest.py tests/test_tools.py -k "rank_cli_hints or keyword_match or match_capability or request_capability or catalog_search or sync_environment"`
- `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py tests/test_offline_discovery.py`
- `uv run ruff format --check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py tests/test_offline_discovery.py`
- `uv run mypy src/pmcp --exclude baml_client`

Release-bound broader checks:

- `uv run pytest -q`
- `uv build`

## Acceptance Criteria

- [x] `CatalogSearchOutput` includes an additive `cli_hints` field that
  serializes compact `CLIHint` objects and defaults to an empty list.
- [x] `gateway.catalog_search({"query": "git"})` returns a matching `git` CLI
  hint when Git is available through cached environment state or bounded CLI
  probing.
- [x] CLI hints stay separate from `results`; catalog result cards remain MCP
  `CapabilityCard` entries only.
- [x] Queryless `gateway.catalog_search({})` returns no broad CLI hints and
  does not perform CLI probing only to populate defaults.
- [x] `total_available`, `limit`, `truncated`, `include_offline`, catalog
  filters, and `stale_updates` retain their existing MCP-result semantics.
- [x] Tests cover matching CLI hints, non-matching queries, queryless behavior,
  offline catalog mode, stale-update coexistence, and response compatibility
  when no hints exist.
- [x] README documents catalog CLI hints as direct Bash/CLI recommendations,
  not as invokable MCP tools.
- [x] CHANGELOG and release-soak claims remain deferred to CLISOAK unless
  implementation changes the public release surface beyond this phase.
