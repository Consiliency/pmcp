---
phase_loop_plan_version: 1
phase: MATCHFIX
roadmap: specs/phase-plans-v8.md
roadmap_sha256: 3e07c3a7e8ca6399747a99b81f7575b58dc48ed79af11ef428715a78784342b7
---

# MATCHFIX: Matcher Scoring Regression

## Context

Phase MATCHFIX implements Phase 3 of `specs/phase-plans-v8.md`: re-tune the capability matcher so representative real user queries resolve to the correct server against the shipped `src/pmcp/manifest/manifest.yaml`, with tests that use the real manifest instead of the synthetic `create_test_manifest()` fixture.

The roadmap hash was verified from `specs/phase-plans-v8.md` as `3e07c3a7e8ca6399747a99b81f7575b58dc48ed79af11ef428715a78784342b7`. Canonical `.phase-loop/` state marks `MATCHFIX` as `unplanned`; legacy `.codex/phase-loop/` state is compatibility-only and is not authoritative for this run. The worktree was clean before writing this plan.

Current matcher seams are narrow: `_keyword_match_score(...)` scores keyword evidence, `_manifest_keyword_weights(...)` computes real-manifest keyword weights, `_keyword_match(...)` applies the server threshold, and `rank_cli_hints(...)` shares `_keyword_match_score(...)` for CLI hint ranking. Execution should fix the server matching regression without changing the manifest schema, registry entries, or the public gateway response shape.

The real-manifest regression table for this phase is frozen as:

- `database sql` -> `sqlite`
- `sql query` -> `sqlite`
- `postgres database` -> `postgres`
- `headless browser` -> `puppeteer`
- `chrome automation` -> `puppeteer`
- `browser scraping` -> `puppeteer`

## Interface Freeze Gates

- [ ] IF-0-MATCHFIX-1 - `_keyword_match_score(...)`, `_manifest_keyword_weights(...)`, and `_keyword_match(...)` preserve CLI hint behavior while making `_keyword_match(<query>, load_manifest(real_manifest), detected_clis=set())` resolve `database sql` to `sqlite`, `sql query` to `sqlite`, `postgres database` to `postgres`, `headless browser` to `puppeteer`, `chrome automation` to `puppeteer`, and `browser scraping` to `puppeteer`; duplicate or near-duplicate manifest entries must not dilute matched keyword evidence below the server threshold.

## Lane Index & Dependencies

- SL-0 - Real-manifest matcher regression tests; Depends on: (none); Blocks: SL-2; Parallel-safe: yes
- SL-1 - Matcher scoring and threshold fix; Depends on: (none); Blocks: SL-2; Parallel-safe: yes
- SL-2 - MATCHFIX verification and reducer closeout; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Real-Manifest Matcher Regression Tests

- **Scope**: Add adversarial tests that load the shipped manifest and assert the roadmap query table against the real matcher.
- **Owned files**: `tests/test_manifest.py`
- **Interfaces provided**: IF-0-MATCHFIX-1 real-manifest query table; regression coverage proving representative query matches fail on pre-fix HEAD and pass after scoring repair
- **Interfaces consumed**: pre-existing `load_manifest(...)`, `_keyword_match(...)`, `_keyword_match_score(...)`, synthetic matcher tests in `create_test_manifest()`, shipped `src/pmcp/manifest/manifest.yaml`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a parametrized regression in `tests/test_manifest.py` that loads `Path("src/pmcp/manifest/manifest.yaml")` through `load_manifest(...)`, calls `_keyword_match(query, manifest, detected_clis=set())`, and asserts each frozen query maps to the expected server name and `entry_type == "server"`.
  - test: Assert every frozen query clears the server matching threshold with meaningful confidence, so matches are not present only because of tie-breaking side effects.
  - test: Add a focused duplicate-dilution regression showing repeated or near-duplicate keyword-bearing server entries do not drive a specifically matched server below threshold.
  - test: Preserve existing synthetic coverage for CLI preference, specific multi-keyword server preference, and generic `api` staying below threshold.
  - verify: `uv run pytest tests/test_manifest.py -k "real_manifest or matcher or score or threshold or keyword_match"`
  - verify: `git diff --check -- tests/test_manifest.py`

### SL-1 - Matcher Scoring and Threshold Fix

- **Scope**: Repair keyword evidence weighting and threshold behavior so real-manifest server matches survive common keyword dilution without admitting generic one-word noise.
- **Owned files**: `src/pmcp/manifest/matcher.py`
- **Interfaces provided**: IF-0-MATCHFIX-1 scoring implementation; stable server threshold behavior for real-manifest capability queries; duplicate-safe keyword weighting
- **Interfaces consumed**: pre-existing `_normalize_text(...)`, `_keyword_matches_query(...)`, `_keyword_match_score(...)`, `_manifest_keyword_weights(...)`, `_keyword_match(...)`, `rank_cli_hints(...)`, `Manifest.servers`, `ServerConfig.keywords`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Run the SL-0 real-manifest query table after implementation and confirm the pre-fix failing cases now pass without broadening the matcher to unrelated generic queries.
  - impl: Rework `_manifest_keyword_weights(...)` or `_keyword_match_score(...)` so matched keyword evidence is normalized by useful signal rather than diluted by the full manifest's repeated generic keywords or duplicate-like server entries.
  - impl: Keep `_keyword_match(...)` fail-closed for generic one-word queries such as `api`, preserving the existing minimum-quality guard.
  - impl: Preserve deterministic ranking and existing CLI hint behavior from `rank_cli_hints(...)`; any server-specific weighting change must not make unavailable or suppressed CLI hints surface unexpectedly.
  - impl: Do not edit `src/pmcp/manifest/manifest.yaml`, registry models, registry fetch code, or gateway discovery handlers in this phase; those are REGFIX-owned surfaces.
  - verify: `uv run pytest tests/test_manifest.py -k "real_manifest or matcher or score or threshold or keyword_match or generic_api or rank_cli_hints"`
  - verify: `git diff --check -- src/pmcp/manifest/matcher.py`

### SL-2 - MATCHFIX Verification and Reducer Closeout

- **Scope**: Verify MATCHFIX as one matcher contract, confirm IF-0-MATCHFIX-1 is fully produced, and record whether execution touched only phase-owned files.
- **Owned files**: none
- **Interfaces provided**: MATCHFIX verification evidence; IF-0-MATCHFIX-1 completion checklist; phase-owned dirty-path inventory for runner closeout
- **Interfaces consumed**: IF-0-MATCHFIX-1; SL-0 real-manifest regression tests; SL-1 matcher scoring implementation; roadmap MATCHFIX exit criteria
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm no lane-owned file overlaps another lane-owned file and no executor touched files outside `src/pmcp/manifest/matcher.py` and `tests/test_manifest.py` for implementation.
  - test: Confirm `tests/test_manifest.py` contains a real-manifest query table for `database sql`, `sql query`, `postgres database`, `headless browser`, `chrome automation`, and `browser scraping` and does not rely on `create_test_manifest()` for IF-0-MATCHFIX-1.
  - verify: `uv run pytest tests/test_manifest.py -k "real_manifest or matcher or score or threshold or keyword_match"`
  - verify: `TMPDIR=/var/tmp uv run ruff check src/ tests/`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `TMPDIR=/var/tmp uv run pytest -q`
  - verify: `git diff --check`
  - verify: `git status --short`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-2: work-unit=`phase_reducer`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
uv run pytest tests/test_manifest.py -k "real_manifest or matcher or score or threshold or keyword_match"
TMPDIR=/var/tmp uv run ruff check src/ tests/
uv run mypy src/pmcp --exclude baml_client
TMPDIR=/var/tmp uv run pytest -q
git diff --check
git status --short
```

## Acceptance Criteria

- [ ] `tests/test_manifest.py` loads the real `src/pmcp/manifest/manifest.yaml` and asserts the frozen query table for `database sql`, `sql query`, `postgres database`, `headless browser`, `chrome automation`, and `browser scraping`.
- [ ] `_keyword_match(...)` resolves the frozen query table to `sqlite`, `sqlite`, `postgres`, `puppeteer`, `puppeteer`, and `puppeteer` respectively with `entry_type == "server"` and confidence above the server threshold.
- [ ] Duplicate or near-duplicate manifest entries no longer dilute matched keyword evidence enough to drop representative real queries below threshold.
- [ ] Generic one-word noise such as `api` remains below threshold, and existing CLI hint behavior remains unchanged.
- [ ] No manifest schema, registry client, gateway discovery handler, or shipped `manifest.yaml` entry changes are made in MATCHFIX.
- [ ] `ruff`, mypy, and full `pytest` pass with `TMPDIR=/var/tmp` for commands that need a temporary directory outside `/tmp`.
