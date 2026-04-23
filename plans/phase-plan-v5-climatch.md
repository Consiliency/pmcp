# CLIMATCH: Central CLI Matching and Hint Builder

## Context

Phase 2 of `specs/phase-plans-v5.md` depends on the Phase 1 CLI hint
contract. The current worktree already contains that upstream shape:
`src/pmcp/types.py` defines `CLIHint`, `src/pmcp/manifest/loader.py` parses
`CLIAlternative.examples`, and `src/pmcp/manifest/manifest.yaml` includes
curated examples plus `prefer_mcp_for` phrases such as GitHub issues and pull
requests. The roadmap file itself is staged as a new file at planning time
(`A  specs/phase-plans-v5.md`), so it is not committed yet.

The existing matcher is narrower than Phase 2 needs:
`src/pmcp/manifest/matcher.py` only returns a legacy `MatchResult` and scores
CLI keywords for detected CLI names. `GatewayTools` stores only
`self._detected_clis: set[str] | None`, so paths from `probe_clis(...)` are
discarded before future `CLIHint` responses can use them. This phase should
centralize deterministic CLI ranking and availability resolution while
preserving public gateway response behavior for Phase 3 and Phase 4.

Documentation impact is intentionally limited. Phase 2 does not expose CLI
hints through public gateway outputs yet, so README and CHANGELOG updates stay
deferred unless execution discovers an unavoidable user-visible behavior change.

## Interface Freeze Gates

- [x] IF-0-CLIMATCH-1 - `src/pmcp/manifest/matcher.py` defines an internal
  `CLIHintMatch` dataclass with `hint: CLIHint`, `score: float`,
  `suppressed_by_prefer_mcp: bool = False`, and
  `matched_prefer_mcp_phrase: str | None = None`.
- [x] IF-0-CLIMATCH-2 - `rank_cli_hints(query, manifest, *, available_clis=None,
  detected_cli_infos=None, include_unavailable=False,
  include_suppressed=False, min_score=0.2) -> list[CLIHintMatch]` is the shared
  deterministic CLI preference entry point for Phase 3, Phase 4, and any future
  discovery surface.
- [x] IF-0-CLIMATCH-3 - `rank_cli_hints(...)` scores only local manifest data:
  CLI name, description, keywords, curated examples, and `prefer_mcp_for`
  phrases. It does not call an LLM, network service, shell command, or full
  help-output fetcher.
- [x] IF-0-CLIMATCH-4 - CLI hints are marked `available=True` only when the CLI
  name appears in explicit `available_clis`, cached `GatewayTools`
  environment state, or `detected_cli_infos` from `probe_clis(...)`.
- [x] IF-0-CLIMATCH-5 - When `detected_cli_infos[name].path` is present,
  `CLIHint.path` preserves that path; when availability came only from
  `available_clis`, the hint remains available with `path=None`.
- [x] IF-0-CLIMATCH-6 - A query matching a CLI's `prefer_mcp_for` phrase is
  suppressed from default helper results, and is returned only when
  `include_suppressed=True` with `suppressed_by_prefer_mcp=True`,
  `matched_prefer_mcp_phrase` populated, and `hint.reason` explaining the MCP
  preference.
- [x] IF-0-CLIMATCH-7 - Existing legacy matcher contracts remain compatible:
  `match_capability(...)`, `_keyword_match(...)`, and `MatchResult` continue to
  satisfy existing tests while delegating CLI scoring to the shared CLI hint
  ranking logic where practical.
- [x] IF-0-CLIMATCH-8 - `GatewayTools` preserves probe metadata in a
  `dict[str, CLIInfo]` cache without changing `gateway.request_capability`,
  `gateway.catalog_search`, or `gateway.sync_environment` public schemas in
  this phase.

## Lane Index & Dependencies

- SL-0 - Matcher and ranked hint helper; Depends on: (none); Blocks: SL-1, SL-2; Parallel-safe: yes
- SL-1 - GatewayTools CLI availability cache; Depends on: SL-0; Blocks: SL-2; Parallel-safe: no
- SL-2 - Phase verification and documentation decision; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Matcher and Ranked Hint Helper

- **Scope**: Add the central CLI hint ranking helper and direct matcher tests while preserving the legacy matcher API.
- **Owned files**: `src/pmcp/manifest/matcher.py`, `src/pmcp/manifest/__init__.py`, `tests/test_manifest.py`
- **Interfaces provided**: `CLIHintMatch`, `rank_cli_hints(...)`, preserved `match_capability(...)`, preserved `_keyword_match(...)`, preserved `MatchResult`
- **Interfaces consumed**: Phase 1 `CLIHint`, `CLIAlternative.examples`, `CLIAlternative.prefer_mcp_for`, `CLIInfo.path`, existing `Manifest`, existing server keyword scoring behavior
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add `test_rank_cli_hints_prefers_available_cli_for_local_task` proving a query such as `"git commits"` returns a first `CLIHintMatch` for `git` when `available_clis={"git"}`.
  - test: Add `test_rank_cli_hints_uses_name_description_keywords_and_examples` proving matches can come from CLI name, description, keyword phrases, and curated examples.
  - test: Add `test_rank_cli_hints_preserves_probe_path` using a `CLIInfo(name="git", path="/usr/bin/git")` in `detected_cli_infos`.
  - test: Add `test_rank_cli_hints_available_clis_have_no_path_when_unprobed` proving explicit available CLI names do not invent a path.
  - test: Add `test_rank_cli_hints_suppresses_prefer_mcp_phrase_by_default` and `test_rank_cli_hints_can_return_suppressed_prefer_mcp_match` for `"github issues"` or `"pull requests"`.
  - test: Keep or update existing `_keyword_match` and `match_capability` tests so legacy callers still see CLI preference when an installed CLI matches and server preference when no CLI is available.
  - impl: Add `CLIHintMatch` and `rank_cli_hints(...)` in `matcher.py`; keep helper scoring deterministic and local to manifest data.
  - impl: Normalize query, CLI names, descriptions, keywords, examples, and `prefer_mcp_for` phrases consistently with the existing keyword matcher style.
  - impl: Build `CLIHint` from each `CLIAlternative` with compact fields only: `name`, `description`, `available`, `path`, `check_command`, `help_command`, `examples`, `prefer_mcp_for`, and `reason`.
  - impl: Exclude unavailable hints by default, exclude `prefer_mcp_for` suppressed hints by default, sort returned matches by descending score and then stable CLI name.
  - impl: Reuse `rank_cli_hints(...)` inside legacy matcher code for CLI candidates where doing so does not change existing public semantics.
  - impl: Export `rank_cli_hints` and `CLIHintMatch` from `pmcp.manifest.__init__` only if existing import style or tests need package-level imports; otherwise keep imports explicit from `pmcp.manifest.matcher`.
  - verify: `uv run pytest tests/test_manifest.py -k "rank_cli_hints or keyword_match or match_capability"`
  - verify: `uv run ruff check src/pmcp/manifest/matcher.py src/pmcp/manifest/__init__.py tests/test_manifest.py`

### SL-1 - GatewayTools CLI Availability Cache

- **Scope**: Preserve CLI probe metadata in `GatewayTools` and route handler availability resolution through one private path for future public CLI hint integrations.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: `GatewayTools._detected_cli_infos: dict[str, CLIInfo]`, a private CLI availability resolver returning available CLI names plus probed `CLIInfo` records, unchanged public gateway output schemas
- **Interfaces consumed**: SL-0 `rank_cli_hints(...)`, existing `probe_clis(...)`, existing `detect_platform()`, existing `CapabilityRequestInput.available_clis`, existing `SyncEnvironmentInput.detected_clis`, existing `SyncEnvironmentOutput.detected_clis`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add `test_sync_environment_caches_cli_probe_infos_with_paths` by monkeypatching `probe_clis(...)` to return `CLIInfo` records and asserting `GatewayTools` retains the path metadata internally while the public output still returns the same detected CLI name list.
  - test: Add `test_request_capability_reuses_cached_cli_probe_infos_without_reprobing` proving cached probe info from `sync_environment` avoids a second `probe_clis(...)` call.
  - test: Add `test_request_capability_available_clis_do_not_overwrite_cached_probe_paths` proving explicit `available_clis` can mark names available without erasing existing cached paths.
  - test: Add `test_request_capability_still_returns_server_candidate_before_phase_3_cli_response` proving Phase 2 does not start returning `status="use_cli"`.
  - impl: Import `CLIInfo` and the SL-0 helper in `handlers.py` as needed without changing gateway tool schemas.
  - impl: Add `self._detected_cli_infos: dict[str, CLIInfo] = {}` alongside the existing `self._detected_clis` compatibility cache.
  - impl: Replace set-only CLI probing blocks in `request_capability(...)` and `sync_environment(...)` with a small private resolver that accepts explicit CLI names, cached probe info, or fresh `probe_clis(...)` results.
  - impl: Preserve `self._detected_clis` for existing behavior and tests, but treat `self._detected_cli_infos` as the source of path-aware metadata for future hint construction.
  - impl: If `request_capability(...)` calls `rank_cli_hints(...)` in this phase, use it only to validate central matching/plumbing and leave public response branching unchanged until Phase 3.
  - verify: `uv run pytest tests/test_tools.py -k "sync_environment or request_capability"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-2 - Phase Verification and Documentation Decision

- **Scope**: Confirm the centralized matcher and handler cache satisfy Phase 2 without leaking CLI hints into public discovery outputs early.
- **Owned files**: `plans/phase-plan-v5-climatch.md`
- **Interfaces provided**: completed CLIMATCH checklist, execution notes, and explicit docs/no-docs decision
- **Interfaces consumed**: SL-0 matcher tests and interfaces, SL-1 handler cache tests and interfaces, Phase 2 exit criteria from `specs/phase-plans-v5.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Review SL-0 and SL-1 verification output and map every Phase 2 exit criterion to a named test or explicit no-op decision.
  - impl: Mark interface gates and acceptance criteria complete only after the implementation and verification commands pass.
  - impl: Record that README and CHANGELOG remain deferred because public response behavior is unchanged in Phase 2.
  - impl: Record any intentional deviations, especially if execution chooses a different helper name or internal match metadata shape.
  - verify: `uv run pytest tests/test_manifest.py tests/test_tools.py -k "rank_cli_hints or keyword_match or match_capability or sync_environment or request_capability"`
  - verify: `uv run ruff check src/pmcp/manifest/matcher.py src/pmcp/manifest/__init__.py src/pmcp/tools/handlers.py tests/test_manifest.py tests/test_tools.py`
  - verify: `uv run ruff format --check src/pmcp/manifest/matcher.py src/pmcp/manifest/__init__.py src/pmcp/tools/handlers.py tests/test_manifest.py tests/test_tools.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`

## Verification

Execution notes:

- SL-0 added `CLIHintMatch` and `rank_cli_hints(...)` in
  `src/pmcp/manifest/matcher.py`, exported the helper from
  `pmcp.manifest`, and covered name, description, keyword, example, path,
  explicit availability, and `prefer_mcp_for` suppression behavior in
  `tests/test_manifest.py`.
- SL-1 added `GatewayTools._detected_cli_infos` and a private availability
  resolver that preserves probe paths, reuses cached probe metadata, and calls
  `rank_cli_hints(...)` without changing Phase 2 public response schemas.
- README and CHANGELOG remain deferred because Phase 2 does not expose CLI
  hints through public gateway outputs and `gateway.request_capability` still
  returns server candidates or not-available responses rather than
  `status="use_cli"`.
- Intentional deviation: no public response branch consumes CLI hint matches in
  this phase; the handler logs matches at debug level only to validate plumbing
  for Phase 3.

Lane-specific verification:

- `uv run pytest tests/test_manifest.py -k "rank_cli_hints or keyword_match or match_capability"`
- `uv run ruff check src/pmcp/manifest/matcher.py src/pmcp/manifest/__init__.py tests/test_manifest.py`
- `uv run pytest tests/test_tools.py -k "sync_environment or request_capability"`
- `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

Whole-phase regression:

- `uv run pytest tests/test_manifest.py tests/test_tools.py -k "rank_cli_hints or keyword_match or match_capability or sync_environment or request_capability"`
- `uv run pytest tests/test_manifest.py -k "cli or manifest or keyword"`
- `uv run pytest tests/test_tools.py -k "request_capability or catalog_search or sync_environment or cli"`
- `uv run ruff check src/pmcp/manifest/matcher.py src/pmcp/manifest/__init__.py src/pmcp/tools/handlers.py tests/test_manifest.py tests/test_tools.py`
- `uv run ruff format --check src/pmcp/manifest/matcher.py src/pmcp/manifest/__init__.py src/pmcp/tools/handlers.py tests/test_manifest.py tests/test_tools.py`
- `uv run mypy src/pmcp --exclude baml_client`

Release-bound broader checks:

- `uv run pytest -q`
- `uv build`

## Acceptance Criteria

- [x] A shared deterministic helper returns ranked CLI hint matches for a query
  using CLI name, description, keywords, examples, and `prefer_mcp_for`.
- [x] CLI hints are marked available only from explicit `available_clis`, cached
  `gateway.sync_environment` state, or bounded `probe_clis(...)` detection.
- [x] CLI path information is preserved when probe results provide it and is not
  fabricated when only a CLI name was provided.
- [x] `prefer_mcp_for` suppresses default CLI recommendations for configured
  phrases and exposes structured suppression metadata when requested for tests
  or future diagnostics.
- [x] Existing legacy matcher behavior and handler public schemas remain
  backward compatible.
- [x] Handler internals retain path-aware CLI metadata for Phase 3
  `gateway.request_capability` and Phase 4 `gateway.catalog_search` integration.
- [x] Named tests prove installed CLIs are preferred for matching local tasks
  and MCP preference overrides win for configured phrases.
- [x] README and CHANGELOG changes are consciously deferred unless execution
  introduces a user-visible discovery behavior change.
