# REQCLI: Request Capability CLI Resolution

## Context

Phase 3 of `specs/phase-plans-v5.md` depends on the completed CLIHINT and
CLIMATCH phases. The current worktree already has the upstream interfaces:
`src/pmcp/types.py` defines `CLIHint`, `src/pmcp/manifest/matcher.py` defines
`CLIHintMatch` and `rank_cli_hints(...)`, and `GatewayTools` keeps
path-aware CLI probe metadata through `_resolve_cli_availability(...)`.

`gateway.request_capability` currently computes CLI hint matches but only logs
them. Its public flow still returns explicit MCP server candidates, category
picks, or `not_available`. Phase 3 should consume the ranked hint result and
return `status="use_cli"` when a native installed CLI is the right first
surface.

There is one ordering constraint to freeze before implementation: the packaged
manifest contains both a `git` CLI alternative and a `git` MCP server. The
roadmap requires `gateway.request_capability({"query": "git commits",
"available_clis": ["git"]})` to return `use_cli`, so a bare same-named server
match cannot always preempt CLI resolution. Explicit MCP intent should still be
preserved, for example `git mcp server`, `provision git server`, or other
queries that clearly ask for the MCP server rather than local Git.

The roadmap file is staged as a new file at planning time
(`A  specs/phase-plans-v5.md`), so it is not untracked.

## Interface Freeze Gates

- [x] IF-0-REQCLI-1 - `GatewayTools.request_capability(...)` returns
  `CapabilityResolution(status="use_cli")` for the first unsuppressed
  `rank_cli_hints(...)` match whose `CLIHint.available` is `True`, after strong
  explicit MCP server intent is honored and before category/no-match fallback.
- [x] IF-0-REQCLI-2 - Same-named CLI/server collisions prefer the installed CLI
  for local task queries such as `git commits`; they return the MCP server only
  when the query contains explicit MCP/server/provision intent.
- [x] IF-0-REQCLI-3 - The `use_cli` payload remains compact and does not fetch
  or include full help output. `CapabilityResolution.cli` uses `CLIResolution`
  with additive compact fields mirroring the selected hint: `name`, `path`,
  `description`, `available`, `check_command`, `help_command`, `examples`,
  `prefer_mcp_for`, and `reason`; legacy `help_output` remains optional and is
  left `None` in normal responses.
- [x] IF-0-REQCLI-4 - The `use_cli` message explicitly directs the caller to
  use Bash/direct CLI and makes clear PMCP is not executing the command or
  provisioning a server for this path.
- [x] IF-0-REQCLI-5 - CLI availability comes only from explicit
  `available_clis`, cached `gateway.sync_environment` state, or bounded
  `probe_clis(...)` fallback through `_resolve_cli_availability(...)`; probe
  paths are preserved when known and not fabricated when only a CLI name was
  provided.
- [x] IF-0-REQCLI-6 - `prefer_mcp_for` suppression prevents matching native
  CLIs, such as `git` for `github issues` or `pull requests`, from being the
  primary answer.
- [x] IF-0-REQCLI-7 - Existing MCP recommendation behavior remains compatible:
  strong explicit server requests return `status="candidates"`, category
  requests still return `status="pick_from_category"` when no CLI wins, and
  unknown requests still return `status="not_available"` with registry search
  guidance.
- [x] IF-0-REQCLI-8 - README documents the request-capability CLI-first response
  shape with a compact example and without claiming PMCP executes the CLI.

## Lane Index & Dependencies

- SL-0 - `request_capability` use_cli integration; Depends on: (none); Blocks: SL-1, SL-2; Parallel-safe: no
- SL-1 - README CLI-first request flow; Depends on: SL-0; Blocks: SL-2; Parallel-safe: yes
- SL-2 - Phase verification and closeout; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - `request_capability` use_cli Integration

- **Scope**: Convert the existing ranked CLI hint plumbing into the public
  `status="use_cli"` response while preserving explicit MCP server and
  category fallback behavior.
- **Owned files**: `src/pmcp/types.py`, `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: additive `CLIResolution` compact fields,
  `CapabilityResolution(status="use_cli", cli=...)`, helper or inline mapping
  from `CLIHint` to `CLIResolution`, same-named CLI/server collision policy
- **Interfaces consumed**: Phase 1 `CLIHint`, Phase 2 `rank_cli_hints(...)`,
  `CLIHintMatch.hint`, `GatewayTools._resolve_cli_availability(...)`, existing
  explicit server name matching, existing category fallback, existing
  `CapabilityResolution` statuses
- **Parallel-safe**: no
- **Tasks**:
  - test: Update the legacy `CLIResolution` model test so old fields still
    validate while the new compact fields default safely and serialize without
    requiring `help_output`.
  - test: Add
    `test_request_capability_returns_use_cli_for_available_cli` with query
    `git commits` and `available_clis=["git"]`; assert `status=="use_cli"`,
    `cli.name=="git"`, compact examples, help command, description, no full
    help output, and a message telling the caller to use Bash/direct CLI.
  - test: Add
    `test_request_capability_git_commits_prefers_cli_over_same_named_git_server`
    using a manifest that contains both CLI `git` and server `git`; assert the
    bare local task returns `use_cli`.
  - test: Add
    `test_request_capability_explicit_git_mcp_server_request_returns_candidate`
    using the same collision manifest and a query such as `use git mcp server`;
    assert `status=="candidates"` and the candidate is the `git` server.
  - test: Add
    `test_request_capability_use_cli_preserves_cached_sync_environment_path`
    by monkeypatching `probe_clis(...)`, calling `sync_environment(...)`, then
    asserting the later `request_capability(...)` response includes the cached
    path.
  - test: Add
    `test_request_capability_probe_fallback_returns_use_cli_when_detected`
    proving an omitted `available_clis` input can still return `use_cli` from
    bounded probe results.
  - test: Add or update MCP override coverage for `github issues` and
    `pull requests` so `prefer_mcp_for` does not return `git` as the primary
    answer.
  - test: Keep no-match coverage proving unknown queries still return
    `not_available` with `gateway.search_registry` guidance when no CLI or MCP
    route matches.
  - impl: Add additive compact fields to `CLIResolution` in
    `src/pmcp/types.py`: `description`, `available`, `check_command`,
    `help_command`, `prefer_mcp_for`, and `reason`, while preserving
    `name`, `path`, `help_output`, and `examples`.
  - impl: Import `CLIResolution` in `src/pmcp/tools/handlers.py` and map the
    selected `CLIHint` into `CapabilityResolution.cli` without fetching full CLI
    help output.
  - impl: Refine the current explicit server name handling so same-named
    CLI/server collisions only preempt CLI when the query explicitly asks for
    an MCP/server/provision path; non-overlapping explicit server names keep the
    current candidate behavior.
  - impl: Insert the public CLI return branch before category fallback so
    installed local CLI tasks do not become MCP category picks.
  - impl: Use the first unsuppressed `rank_cli_hints(...)` match; keep
    suppressed matches out of normal `use_cli` responses unless they are needed
    only for tests or diagnostics.
  - verify: `uv run pytest tests/test_tools.py -k "request_capability and (use_cli or git or sync_environment or search_guidance)"`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-1 - README CLI-First Request Flow

- **Scope**: Document the Phase 3 `gateway.request_capability` CLI-first path
  in the progressive disclosure workflow without expanding catalog-search
  behavior or release notes.
- **Owned files**: `README.md`
- **Interfaces provided**: README example for `status="use_cli"`, compact
  request-capability CLI guidance, explicit statement that PMCP recommends but
  does not execute native CLI commands
- **Interfaces consumed**: SL-0 `CapabilityResolution(status="use_cli")`
  response shape, Phase 3 non-goals, existing Progressive Disclosure Workflow
  README section
- **Parallel-safe**: yes, after SL-0 freezes response fields
- **Tasks**:
  - test: Review the README example against the implemented response fields and
    verify it includes `status`, `message`, `cli.name`, `cli.help_command`, and
    `cli.examples` without `help_output`.
  - impl: Add a compact CLI-first example near the current "Step 1: Request a
    Capability" section using `gateway.request_capability({ query: "git commits",
    available_clis: ["git"] })`.
  - impl: State that the model should use Bash/direct CLI after a `use_cli`
    response and should continue to use MCP provisioning/invocation for server
    candidates.
  - impl: Leave `gateway.catalog_search` CLI hint docs and CHANGELOG release
    notes for CATALOGCLI/CLISOAK unless execution discovers a Phase 3 response
    contract that must be documented immediately.
  - verify: `rg -n '"use_cli"|help_command|help_output|direct CLI|Bash' README.md`

### SL-2 - Phase Verification and Closeout

- **Scope**: Confirm Phase 3 satisfies the REQCLI exit criteria and records
  the remaining docs/release work for later phases.
- **Owned files**: `plans/phase-plan-v5-reqcli.md`
- **Interfaces provided**: completed REQCLI checklist, execution notes,
  verification summary, docs and release-note decision
- **Interfaces consumed**: SL-0 handler/model tests and response contracts,
  SL-1 README update, Phase 3 exit criteria from `specs/phase-plans-v5.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Review SL-0 and SL-1 verification output and map every Phase 3 exit
    criterion to a named test, README example, or explicit no-op decision.
  - impl: Mark interface gates and acceptance criteria complete only after the
    implementation and verification commands pass.
  - impl: Record that `gateway.catalog_search` CLI hints and CHANGELOG release
    notes remain deferred to CATALOGCLI/CLISOAK.
  - impl: Record any intentional deviation, especially if implementation
    chooses to expose `CLIHint` directly instead of the additive
    `CLIResolution` mapping frozen in this plan.
  - verify: `uv run pytest tests/test_tools.py -k "request_capability or sync_environment or cli"`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py`
  - verify: `uv run ruff format --check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_tools.py -k "request_capability and (use_cli or git or sync_environment or search_guidance)"`
- `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py`
- `rg -n '"use_cli"|help_command|help_output|direct CLI|Bash' README.md`

Whole-phase regression:

- `uv run pytest tests/test_tools.py -k "request_capability or sync_environment or cli"`
- `uv run pytest tests/test_manifest.py tests/test_tools.py -k "rank_cli_hints or keyword_match or match_capability or request_capability or sync_environment"`
- `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py`
- `uv run ruff format --check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py`
- `uv run mypy src/pmcp --exclude baml_client`

Release-bound broader checks:

- `uv run pytest -q`
- `uv build`

## Execution Notes

- SL-0 complete: `CapabilityResolution(status="use_cli")` now maps the first
  available unsuppressed `rank_cli_hints(...)` result into additive
  `CLIResolution` fields without fetching full help output.
- SL-0 complete: same-named CLI/server collisions prefer the installed CLI for
  bare local tasks such as `git commits`; explicit MCP/server/provision intent
  still returns server candidates.
- SL-0 complete: handler tests cover explicit `available_clis`, cached
  `sync_environment` paths, probe fallback, same-name collision behavior,
  explicit MCP override, `prefer_mcp_for`, and no-match search guidance.
- SL-1 complete: README documents the CLI-first request flow and states that
  PMCP recommends Bash/direct CLI without executing the native command.
- Deferred by design: `gateway.catalog_search` CLI hints remain in CATALOGCLI,
  and CHANGELOG/release notes remain in CLISOAK.
- No intentional deviation: implementation uses additive `CLIResolution`
  mapping rather than exposing `CLIHint` directly.

## Verification Results

- Passed: `uv run pytest tests/test_tools.py -k "request_capability and (use_cli or git or sync_environment or search_guidance)"` (8 passed).
- Passed: `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py`.
- Passed: `rg -n '"use_cli"|help_command|help_output|direct CLI|Bash' README.md`.
- Passed: `uv run pytest tests/test_tools.py -k "request_capability or sync_environment or cli"` (15 passed).
- Passed: `uv run pytest tests/test_manifest.py tests/test_tools.py -k "rank_cli_hints or keyword_match or match_capability or request_capability or sync_environment"` (26 passed).
- Passed: `uv run ruff format --check src/pmcp/types.py src/pmcp/tools/handlers.py tests/test_tools.py`.
- Passed: `uv run mypy src/pmcp --exclude baml_client`.
- Not run: release-bound `uv run pytest -q` and `uv build`; those remain broader release checks outside this phase closeout.

## Acceptance Criteria

- [x] `gateway.request_capability({"query": "git commits",
  "available_clis": ["git"]})` returns `status="use_cli"` with CLI details for
  `git`.
- [x] The `use_cli` response includes compact examples, help command,
  description, path when known, and a message that directs the model to use
  Bash/direct CLI.
- [x] The normal `use_cli` response does not include full CLI help output and
  does not execute the recommended CLI command.
- [x] Explicit MCP server requests continue to return server candidates,
  including same-named CLI/server cases when the query explicitly asks for MCP
  server/provision behavior.
- [x] Same-named CLI/server bare local tasks, especially `git commits`, prefer
  the installed CLI over the MCP server.
- [x] Queries matching `prefer_mcp_for`, such as GitHub issues or pull
  requests, do not incorrectly return `git` as the primary answer.
- [x] Handler-level tests cover explicit `available_clis`, cached
  `sync_environment` state, probe fallback, no-match behavior, same-name
  collision behavior, and MCP override behavior.
- [x] README documents the Phase 3 request-capability CLI-first response shape,
  while catalog-search CLI hints and CHANGELOG release notes remain deferred to
  later phases.
