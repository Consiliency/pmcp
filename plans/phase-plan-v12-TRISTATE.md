---
phase_loop_plan_version: 1
phase: TRISTATE
roadmap: specs/phase-plans-v12.md
roadmap_sha256: 806fb51f50a493d1422c54e8d9ded14385525fcd2d3267fa4d39ed4f624ea2b3
---

# TRISTATE: Tri-state version comparison

## Context

`src/pmcp/manifest/version_checker.py` currently exposes three public predicates over one private classification core:

- `is_version_newer(current, latest, package_type)` (`:603`) — **fails closed**: `False` means *either* "up to date" *or* "cannot be ordered".
- `are_versions_comparable(current, latest, package_type)` (`:576`) — added in 2.2.1 to answer the pair question that two unary guards could not.
- `is_version_orderable(value, package_type)` (`:557`) — genuinely unary; **stays** (roadmap Non-Goal).

The classification itself branches three ways in both `is_version_newer` and `are_versions_comparable`: digest identity (`_digest_identity`, `:442`), SemVer ecosystem (`_semver_parse`/`_semver_is_newer`, `:519`/`:545`), else PEP 440 (`_parse_version`, `:485`). `are_versions_comparable` mirrors that branching **by hand**, which is why 2.2.1 shipped a drift test pinning them together.

Consumers, grep-verified — there are exactly two, both in `src/pmcp/manifest/refresher.py`:

- `:234-235` — the "already up to date" short-circuit: `are_versions_comparable(...) and not is_version_newer(...)`. This is the negation site.
- `:435` — `check_staleness`: `if version and is_version_newer(...)`. Positive, not negated.

Nothing else in `src/` calls either function. `is_version_orderable` has **zero** `src/` callers as of 2.2.1.

**Why the roadmap's lane hint does not survive contact.** The roadmap's Scope notes propose 3 lanes split by file (version_checker / refresher / tests). That hint was written before the panel's fable seat flipped EC-TRISTATE-3 from *keep-the-wrapper-and-police-it* to *delete the wrappers outright*. Deletion makes the split order-coupled: the version_checker lane cannot delete `is_version_newer` until `refresher.py` has migrated off it, and `refresher.py` cannot migrate until `compare_versions` exists. A naive 3-way split either produces a lane cycle or has two lanes writing `version_checker.py`.

**A task-level reducer gate was tried and rejected.** The first draft of this plan kept the file split and expressed the coupling as a task edge — SL-1's deletion task depending on the consumer lanes' verify tasks. That is unsafe two ways: if the runner lifts task edges to lane edges it is a `cycle` diagnostic (SL-1 → SL-2 → SL-1), and moving the deletion into its own lane instead trips `overlapping_write_ownership` because two lanes would write `version_checker.py`. Both refuse execution at runtime.

The honest structure is one working lane. Deleting the wrappers and migrating `refresher.py` off them is a **single atomic change** — there is no ordering of separate lanes that keeps every worktree importable in between. The phase is four files and a mechanical move, so serialising costs little, and FANOUT runs concurrently as an independent roadmap root regardless.

## Interface Freeze Gates

- [ ] IF-0-TRISTATE-1 — `compare_versions(current: str, latest: str, package_type: str | None = None) -> Literal["newer", "not_newer", "incomparable"]` in `src/pmcp/manifest/version_checker.py`, exported and importable. It is the **sole** classification path: `_digest_identity` / `_semver_parse` / `_parse_version` are consulted from exactly one place after this phase. Published by SL-1.1 on day 1 as a signature + `Literal` alias with a `NotImplementedError` body, so SL-2 and SL-3 write against the contract without waiting for SL-1.2.

## Lane Index & Dependencies

SL-1 — Tri-state migration
  Depends on: (none)
  Blocks: SL-2
  Parallel-safe: no

SL-2 — Documentation & spec reconciliation
  Depends on: SL-1
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-1 — Tri-state migration

- **Scope**: Introduce `compare_versions` as the single classification path, move both `refresher.py` call sites onto it, delete `is_version_newer` and `are_versions_comparable`, and rework the tests that referenced them.
- **Owned files**: `src/pmcp/manifest/version_checker.py`, `src/pmcp/manifest/refresher.py`, `tests/test_version_checker.py`, `tests/test_refresher.py`
- **Interfaces provided**: `compare_versions`, `VersionComparison` (the `Literal` alias)
- **Interfaces consumed**: (none)
- **Parallel-safe**: no — single lane by necessity, not oversight. See Context: the deletion and the `refresher.py` migration cannot be separated without either a lane cycle or two writers on `version_checker.py`.

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `tests/test_version_checker.py`, `tests/test_refresher.py` | `compare_versions` three-value contract; short-circuit fires only on `not_newer` | `uv run pytest tests/test_version_checker.py tests/test_refresher.py -q -k 'compare_versions or short_circuit'` |
| SL-1.2 | impl | SL-1.1 | `src/pmcp/manifest/version_checker.py` | — | `uv run pytest tests/test_version_checker.py -q` |
| SL-1.3 | impl | SL-1.2 | `src/pmcp/manifest/refresher.py` | — | `uv run pytest tests/test_refresher.py -q` |
| SL-1.4 | impl | SL-1.3 | `src/pmcp/manifest/version_checker.py`, `tests/test_version_checker.py` | — | `uv run pytest -q` |
| SL-1.5 | verify | SL-1.4 | `src/pmcp/manifest/**`, `tests/test_version_checker.py`, `tests/test_refresher.py` | all | `uv run pytest -q && uv run ruff check . && uv run ruff format --check src/ tests/ && uv run mypy src` |

- **SL-1.1** writes the RED tests first, and both must fail against `main`. Assert the three-value contract, that no input yields anything outside the `Literal`, and that the short-circuit does **not** fire for an `incomparable` pair. The observable for the short-circuit is the **second** `get_package_version` call on the refresh path, as established in 2.2.1 — asserting the return value cannot distinguish "refreshed" from "returned the stale cache".
- **SL-1.2** adds `compare_versions` by **moving** the existing branching out of `is_version_newer`/`are_versions_comparable`, not copying it. Both become one-line delegations for the length of SL-1.2–SL-1.3 (migration shims, explicitly permitted by EC-TRISTATE-3). Preserve every current answer: `1.0.0-rc1 < 1.0.0`, digest canonicalisation (`abcdef123456` == `sha256:abcdef123456`), CalVer ordering, the `.public` comparison making `1.0.0+b` equal `1.0.0`, and mixed version/digest → `incomparable`.
- **SL-1.3** rewrites `refresher.py:234-235` as a single `compare_versions(...) == "not_newer"` test, replacing the `are_versions_comparable(...) and not is_version_newer(...)` pair, and `:435` (`check_staleness`) as `== "newer"`. Delete the stale negation-hazard comment at `:220-231` — the hazard is gone with the boolean. **Existing `TestUpToDateShortCircuit` tests must keep passing unchanged**; if either needs editing, the migration changed behaviour and that is a bug, not an accommodation.
- **SL-1.4** deletes `is_version_newer`, `are_versions_comparable` and their shims, and removes `test_no_unguarded_negation_of_is_version_newer` plus its AST helpers (`_negated_is_version_newer`, `_conjuncts`, `_strip`, `_is_pair_guard_for`, `_guaranteed_conditions`, `_called_name`) and the wrapper-drift assertions that no longer have wrappers to compare. Repoint the 28-value × 7-type corpus onto `compare_versions` directly. Keep `is_version_orderable`. Do **not** port the lint's documented limitations forward.

### SL-docs — Documentation & spec reconciliation

- **Scope**: Refresh the docs catalog, record the new API and the wrapper removal in the CHANGELOG, and append post-execution amendments to the v12 roadmap where this phase's lane hint proved wrong.
- **Owned files**: `CHANGELOG.md`, `README.md`, `.claude/docs-catalog.json`, `specs/phase-plans-v12.md`, `docs/**`
- **Interfaces provided**: (none)
- **Interfaces consumed**: (none)
- **Parallel-safe**: no (terminal)
- **Depends on**: SL-1

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Action |
|---|---|---|---|---|
| SL-docs.1 | docs | SL-1.5 | `.claude/docs-catalog.json` | Rescan via `_shared/scaffold_docs_catalog.py --rescan`. If the helper is absent, record "docs-catalog rescan helper unavailable; manual catalog audit" in the commit message and proceed. |
| SL-docs.2 | docs | SL-docs.1 | `CHANGELOG.md`, `README.md`, per catalog | `### Changed` entry naming `compare_versions` and the removal of `is_version_newer` / `are_versions_comparable`. State plainly that the removal is what makes the collapse unrepresentable. Grep `README.md` for either symbol before deciding it needs no change. |
| SL-docs.3 | docs | SL-docs.2 | `specs/phase-plans-v12.md` | Append `### Post-execution amendments` under Phase 1 recording that the roadmap's 3-lane file split did not survive the wrapper-deletion decision — the deletion and the `refresher.py` migration are one atomic change, so the phase executed as a single working lane. |
| SL-docs.4 | verify | SL-docs.3 | — | Repo doc linters if configured; otherwise no-op. `uv run ruff format --check src/ tests/`. |

## Execution Notes

- **Single-writer files**: every source and test file this phase touches belongs to SL-1. `CHANGELOG.md`, `README.md`, `.claude/docs-catalog.json` and `specs/phase-plans-v12.md` belong to SL-docs. There is no contention because there is one working lane.
- **The task order inside SL-1 is the safety mechanism.** SL-1.4 deletes symbols that SL-1.3 must already have migrated off. Running it before SL-1.3 breaks the worktree with an `ImportError` that reads like a merge problem and is not. The shim window between SL-1.2 and SL-1.4 exists precisely so the tree stays importable throughout.
- **This phase is deliberately serial.** Wall-clock parallelism for v12 comes from FANOUT, which is an independent roadmap root with a disjoint file set and can run at the same time as this entire phase.
- **Known destructive changes**: `is_version_newer` and `are_versions_comparable` deleted from `src/pmcp/manifest/version_checker.py` (SL-1.4); `test_no_unguarded_negation_of_is_version_newer` and its AST helpers deleted from `tests/test_version_checker.py` (SL-2.2); the negation-hazard comment block at `refresher.py:220-231` deleted (SL-3.2). All are intended. Nothing else in this phase deletes.
- **Expected add/add conflicts**: none — SL-1.1 stubs `compare_versions` and SL-1.2 fills it, both inside the same lane.
- **SL-0 re-exports**: not applicable; this phase adds no package-level re-export. `version_checker` has no `__init__` surface to update.
- **Behaviour must not change.** This is a representation change. Every answer the current predicates give must survive: prerelease ordering, digest canonicalisation across both spellings, CalVer, `.public` comparison for build metadata, and mixed version/digest → `incomparable`. The 2.2.1 corpus is the regression net; if SL-2 finds itself *editing* an expected value rather than repointing the call, that is a behaviour change and must be reported, not accommodated.
- **Stale-base guidance** (verbatim): Lane teammates working in isolated worktrees do not see sibling-lane merges automatically. If a lane finds its worktree base is pre-SL-1.1, it MUST stop and report rather than committing — the orchestrator will re-spawn or rebase. Silent `git reset --hard` or `git checkout HEAD~N -- …` in a stale worktree produces commits that destroy peer-lane work on `--no-ff` merge.
- **Roadmap divergence recorded**: the roadmap's Scope notes hint at 3 lanes split by file. This plan uses one working lane + docs. See Context for why the split is not available once the wrappers are deleted; SL-docs.3 amends the roadmap.

## Execution Policy

- work-unit defaults: effort=low, reason=mechanical migration onto an existing classification core
- SL-1: effort=medium, reason=moving branching without changing any answer is subtly wrong-prone and the corpus canonicalisation was gotten wrong twice before
- SL-2: effort=minimal, reason=docs sweep only

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `roadmap_amendment`
- target surfaces: `specs/phase-plans-v12.md`
- evidence paths: `plans/phase-plan-v12-TRISTATE.md`
- redaction posture: `metadata_only`
- downstream handling: `roadmap amendment`

## Acceptance Criteria

- [ ] EC-TRISTATE-1 — proven by `uv run pytest tests/test_version_checker.py -q -k compare_versions` plus `uv run python -c "import pmcp.manifest.version_checker as v; assert v.compare_versions('1.0.0','2.0.0','npm') == 'newer'"`
- [ ] EC-TRISTATE-2 — proven by `uv run pytest tests/test_refresher.py -q -k short_circuit`, which must be RED against `main`
- [ ] EC-TRISTATE-3 — proven by `uv run python -c "import pmcp.manifest.version_checker as v; assert not hasattr(v,'is_version_newer'); assert not hasattr(v,'are_versions_comparable')"` and by `uv run pytest tests/test_version_checker.py -q -k negation` collecting zero tests
- [ ] EC-TRISTATE-4 — proven by `uv run pytest tests/test_version_checker.py -q -k corpus`, demonstrated RED by injecting a classification change into `compare_versions`
- [ ] EC-TRISTATE-5 — proven by `uv run pytest -q && uv run ruff check . && uv run ruff format --check src/ tests/ && uv run mypy src`, and by a `### Changed` CHANGELOG entry naming both removed symbols

## Verification

```bash
# Full gates after all lanes merge.
uv run pytest -q                                   # 2568 baseline, adjusted for deleted lint tests; 0 failed
uv run ruff check . && uv run ruff format --check src/ tests/ && uv run mypy src

# EC-TRISTATE-3: absence is the assertion. Not a grep -- two earlier drafts of
# this check matched comments and docstrings, and one named a file that has
# never existed.
uv run python -c "
import pmcp.manifest.version_checker as v
assert not hasattr(v, 'is_version_newer'), 'the negatable boolean is back'
assert not hasattr(v, 'are_versions_comparable'), 'the pair wrapper is back'
assert hasattr(v, 'is_version_orderable'), 'the unary predicate should have stayed'
assert hasattr(v, 'compare_versions')
print('  wrappers absent; compare_versions present')"

# Behaviour parity -- every answer the removed predicates gave must survive.
uv run python -c "
from pmcp.manifest.version_checker import compare_versions as c
cases = [
    ('1.0.0','2.0.0','npm','newer'),
    ('1.0.0-rc1','1.0.0','npm','newer'),
    ('1.0.0-1','1.0.0','npm','newer'),
    ('2.0.0','1.0.0','npm','not_newer'),
    ('1.0.0','1.0.0+b',None,'not_newer'),
    ('1.0','1.0.0',None,'not_newer'),
    ('abcdef123456','sha256:abcdef123456','docker','not_newer'),
    ('abcdef123456','abcdef123457','docker','newer'),
    ('202612180000','202612190000',None,'newer'),
    ('1.0.0','abcdef123456','docker','incomparable'),
    ('1.0.0','nightly','npm','incomparable'),
    ('unknown','2.0.0','npm','incomparable'),
]
bad = [(a,b,t,c(a,b,t),want) for a,b,t,want in cases if c(a,b,t) != want]
assert not bad, bad
print(f'  {len(cases)} parity cases OK')"

# EC-TRISTATE-2: the short-circuit consumes the tri-state directly.
uv run pytest tests/test_refresher.py -q -k 'short_circuit or unorderable or incomparable'
```

Edge cases to exercise: an `incomparable` pair reaching the short-circuit (must refresh, not skip); `check_staleness` at `refresher.py:435` where the call is **positive**, not negated, so a migration error there fails open rather than closed; the 28-value × 7-type corpus with a classification change injected into `compare_versions`; and a cache entry whose `package` field is absent, which UPDPATH will build on.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```
