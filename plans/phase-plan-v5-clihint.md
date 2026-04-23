# CLIHINT: CLI Hint Contract and Manifest Examples

## Context

Phase 1 of `specs/phase-plans-v5.md` freezes the compact CLI hint contract
before later phases wire CLI preference into `gateway.request_capability` and
`gateway.catalog_search`. The roadmap file was staged as a new file at planning
time (`A  specs/phase-plans-v5.md`).

The current implementation has useful pieces but no shared hint model:
`src/pmcp/manifest/loader.py` defines `CLIAlternative` with `name`,
`keywords`, `check_command`, `help_command`, `description`, and
`prefer_mcp_for`; `src/pmcp/types.py` defines `CLIResolution` with
`name`, `path`, `help_output`, and `examples`; and
`CatalogSearchOutput` currently returns only MCP `CapabilityCard` results. This
phase should add the reusable `CLIHint` contract and manifest examples without
changing gateway matching or handler behavior.

Documentation impact for this phase is intentionally limited: README and
CHANGELOG changes belong to later CLI exposure phases because Phase 1 does not
change user-facing discovery behavior.

## Interface Freeze Gates

- [x] IF-0-CLIHINT-1 - `src/pmcp/types.py` defines `CLIHint(BaseModel)` with
  exactly these compact public fields: `name: str`, `description: str`,
  `available: bool`, `path: str | None = None`, `check_command: list[str]`,
  `help_command: list[str]`, `examples: list[str]`,
  `prefer_mcp_for: list[str]`, and `reason: str | None = None`.
- [x] IF-0-CLIHINT-2 - `CLIHint` serialization is compact by construction:
  `model_dump(exclude_none=True)` contains no full CLI help output field and
  uses command lists plus curated examples rather than raw manuals.
- [x] IF-0-CLIHINT-3 - `pmcp.manifest.loader.CLIAlternative` accepts
  `examples: list[str] = field(default_factory=list)` while preserving default
  behavior for older manifest entries that omit `examples`.
- [x] IF-0-CLIHINT-4 - `_parse_cli_alternative(...)` loads YAML
  `examples` into `CLIAlternative.examples` and defaults absent examples to an
  empty list without requiring manifest version changes.
- [x] IF-0-CLIHINT-5 - Every built-in entry under
  `src/pmcp/manifest/manifest.yaml` `cli_alternatives` has 2-4 compact,
  non-secret, non-destructive examples.
- [x] IF-0-CLIHINT-6 - Existing response schemas stay backward compatible in
  Phase 1: `CLIResolution`, `CapabilityResolution`, and `CatalogSearchOutput`
  keep their existing required fields and gateway handlers do not add query
  matching or CLI preference behavior yet.

## Lane Index & Dependencies

- SL-0 - CLI hint model contract; Depends on: (none); Blocks: SL-2; Parallel-safe: yes
- SL-1 - Manifest examples contract and data; Depends on: (none); Blocks: SL-2; Parallel-safe: yes
- SL-2 - Phase verification and closeout; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - CLI Hint Model Contract

- **Scope**: Add the reusable Pydantic CLI hint model and serialization coverage without changing active gateway handler flows.
- **Owned files**: `src/pmcp/types.py`, `tests/test_tools.py`
- **Interfaces provided**: `CLIHint`, compact `CLIHint.model_dump(exclude_none=True)` contract, unchanged `CLIResolution` legacy shape
- **Interfaces consumed**: existing `BaseModel` and `Field` usage in `src/pmcp/types.py`, existing `CLIResolution`, existing test import style in `tests/test_tools.py`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add `test_cli_hint_serializes_compact_contract` proving a populated `CLIHint` dumps `name`, `description`, `available`, `path`, `check_command`, `help_command`, `examples`, `prefer_mcp_for`, and `reason`, and does not expose `help_output`.
  - test: Add `test_cli_resolution_legacy_shape_remains_valid` or equivalent coverage proving existing `CLIResolution(name=..., path=..., help_output=..., examples=...)` still validates and serializes as before.
  - impl: Add `CLIHint` near the capability request types in `src/pmcp/types.py` with default factories for list fields.
  - impl: Do not replace `CLIResolution` in this phase; later phases can add optional `CLIHint` fields to response models when they wire discovery behavior.
  - verify: `uv run pytest tests/test_tools.py -k "cli_hint or cli_resolution"`
  - verify: `uv run ruff check src/pmcp/types.py tests/test_tools.py`

### SL-1 - Manifest Examples Contract and Data

- **Scope**: Teach the manifest loader about curated CLI examples and populate every built-in CLI alternative with compact examples.
- **Owned files**: `src/pmcp/manifest/loader.py`, `src/pmcp/manifest/manifest.yaml`, `tests/test_manifest.py`
- **Interfaces provided**: `CLIAlternative.examples`, `_parse_cli_alternative(...)` examples parsing, packaged manifest example guard
- **Interfaces consumed**: existing manifest YAML layout, existing `CLIAlternative` dataclass construction sites, existing `load_manifest(...)`, existing matcher tests that instantiate `CLIAlternative`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add `test_manifest_parses_cli_examples` using a temporary manifest with one CLI that includes examples, then assert `manifest.get_cli(...).examples` preserves the list.
  - test: Add `test_manifest_cli_examples_default_to_empty` using a temporary manifest without `examples`, then assert the parsed `CLIAlternative.examples` value is `[]`.
  - test: Add `test_packaged_cli_alternatives_have_compact_examples` proving every built-in CLI alternative has 2-4 string examples, each short enough for compact discovery responses and free of obvious destructive commands such as `rm`, `delete`, `destroy`, or `force`.
  - impl: Add `examples: list[str] = field(default_factory=list)` to `CLIAlternative`.
  - impl: Extend `_parse_cli_alternative(...)` to pass `data.get("examples", [])` into the dataclass without changing defaults for `keywords`, `check_command`, `help_command`, `description`, or `prefer_mcp_for`.
  - impl: Update all test `CLIAlternative(...)` construction sites only where required by the dataclass change; the default factory should keep existing call sites valid.
  - impl: Add 2-4 compact examples for each built-in CLI alternative in `manifest.yaml`, using patterns such as `git status --short`, `docker ps`, `kubectl get pods`, `node --version`, `python3 -m pytest`, `aws configure list`, `gcloud config list`, `az --version`, `terraform validate`, `curl -I https://example.com`, and `jq '.items[]' file.json`.
  - verify: `uv run pytest tests/test_manifest.py -k "cli_examples or manifest_cli_config"`
  - verify: `uv run ruff check src/pmcp/manifest/loader.py tests/test_manifest.py`

### SL-2 - Phase Verification and Closeout

- **Scope**: Confirm Phase 1 froze the reusable CLI hint and manifest example contracts without expanding into CLI matching or gateway behavior.
- **Owned files**: `plans/phase-plan-v5-clihint.md`
- **Interfaces provided**: completed CLIHINT checklist, execution notes, and any recorded docs/no-docs decision
- **Interfaces consumed**: SL-0 `CLIHint` and serialization test results, SL-1 manifest parser/data test results, Phase 1 exit criteria from `specs/phase-plans-v5.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Review SL-0 and SL-1 verification output and map every Phase 1 exit criterion to a named test or explicit no-op decision.
  - impl: Mark interface gates and acceptance criteria complete only after the implementation and verification commands pass.
  - impl: Record that README/CHANGELOG updates remain deferred unless execution discovers an unavoidable response-shape change.
  - impl: Record any intentional deviations, especially if `CLIHint` field names differ from the frozen contract or if packaged examples are intentionally fewer than 2 for a built-in CLI.
  - verify: `uv run pytest tests/test_manifest.py tests/test_tools.py -k "cli_hint or cli_resolution or cli_examples or manifest_cli_config"`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/manifest/loader.py tests/test_manifest.py tests/test_tools.py`
  - verify: `uv run ruff format --check src/pmcp/types.py src/pmcp/manifest/loader.py tests/test_manifest.py tests/test_tools.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_tools.py -k "cli_hint or cli_resolution"`
- `uv run ruff check src/pmcp/types.py tests/test_tools.py`
- `uv run pytest tests/test_manifest.py -k "cli_examples or manifest_cli_config"`
- `uv run ruff check src/pmcp/manifest/loader.py tests/test_manifest.py`

Whole-phase regression:

- `uv run pytest tests/test_manifest.py tests/test_tools.py -k "cli_hint or cli_resolution or cli_examples or manifest_cli_config"`
- `uv run pytest tests/test_manifest.py -k "cli or manifest or keyword"`
- `uv run ruff check src/pmcp/types.py src/pmcp/manifest/loader.py tests/test_manifest.py tests/test_tools.py`
- `uv run ruff format --check src/pmcp/types.py src/pmcp/manifest/loader.py tests/test_manifest.py tests/test_tools.py`
- `uv run mypy src/pmcp --exclude baml_client`

Release-bound broader checks:

- `uv run pytest tests/test_tools.py -k "request_capability or catalog_search or sync_environment or cli"`
- `uv run pytest -q`
- `uv build`

## Acceptance Criteria

- [x] `src/pmcp/types.py` defines a dedicated `CLIHint` model with command name,
  description, availability, path, check command, help command, examples,
  preference metadata, and reason fields.
- [x] `CLIHint` serialization stays compact and does not include full help
  output.
- [x] `CLIAlternative.examples` is parsed from manifest YAML and defaults to
  `[]` for older entries.
- [x] Each packaged CLI alternative has 2-4 compact curated examples.
- [x] Existing response schemas and gateway handler behavior remain backward
  compatible; Phase 1 does not add CLI query matching or `use_cli` behavior.
- [x] Named tests cover CLI hint serialization, legacy `CLIResolution`
  compatibility, manifest parsing of examples, omitted-example defaults, and
  packaged manifest example coverage.
- [x] README and CHANGELOG changes are consciously deferred to later phases
  unless execution introduces a user-visible discovery behavior change.

## Execution Notes

- Implemented SL-0 and SL-1 without adding CLI query matching, CLI preference
  behavior, or response-shape changes to `CapabilityResolution` or
  `CatalogSearchOutput`.
- README and CHANGELOG remain deferred because Phase 1 only freezes internal
  model and packaged manifest contracts.
- Verification completed:
  `uv run pytest tests/test_tools.py -k "cli_hint or cli_resolution"`;
  `uv run ruff check src/pmcp/types.py tests/test_tools.py`;
  `uv run pytest tests/test_manifest.py -k "cli_examples or manifest_cli_config"`;
  `uv run ruff check src/pmcp/manifest/loader.py tests/test_manifest.py`;
  `uv run pytest tests/test_manifest.py tests/test_tools.py -k "cli_hint or cli_resolution or cli_examples or manifest_cli_config"`;
  `uv run pytest tests/test_manifest.py -k "cli or manifest or keyword"`;
  `uv run ruff check src/pmcp/types.py src/pmcp/manifest/loader.py tests/test_manifest.py tests/test_tools.py`;
  `uv run ruff format --check src/pmcp/types.py src/pmcp/manifest/loader.py tests/test_manifest.py tests/test_tools.py`;
  `uv run mypy src/pmcp --exclude baml_client`;
  `uv run pytest tests/test_tools.py -k "request_capability or catalog_search or sync_environment or cli"`;
  `uv run pytest -q`;
  `uv build`.
