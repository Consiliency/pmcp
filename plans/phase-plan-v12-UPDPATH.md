---
phase_loop_plan_version: 1
phase: UPDPATH
roadmap: specs/phase-plans-v12.md
roadmap_sha256: bf1848c8178686235bf707d4959b26a7781ec12387dab7404ff789eadfe7485d
---

# PHASE-2-UPDPATH: Update-path identity and environment contracts

## Context

Three sites pair a cached description with a configured server **by name** and
then decide freshness by comparing **versions only**. None of them asks whether
the cache still describes the same *package*. Verified against the tree at
v2.3.0:

- `refresh_server`'s short-circuit — `src/pmcp/manifest/refresher.py:219-227`.
  `detect_package_type` / `pkg_name` resolution sits at `:232`, **below** the
  early return, so the comparison could not consult identity even if it wanted
  to. This is why EC-UPDPATH-2 is a real move and not a restatement of EC-1.
- `refresh_all` — `:335-357`. It loads the cache, pulls `existing_servers.get(name)`
  by name, and hands that entry to `refresh_server` as `existing`. A caller
  assembling the pair itself must not be able to bypass the gate (EC-3).
- `check_staleness` — `:393`, comparison at `:422`. Backs
  `pmcp refresh --check-versions`, so the failure is operator-visible: a cache
  for `old-package@2.0.0` against a config for `new-package@1.0.0` prints
  "All cached descriptions are up to date."

The cross-**ecosystem** case is already handled and must not be re-planned: a
version against a digest classifies as `compare_versions(...) == "incomparable"`,
which is not `"not_newer"`, so the short-circuit does not fire. Only the
**same-ecosystem** case survives — `@old/pkg@1.0.0` cached, `@new/pkg@1.0.0`
configured, both npm, both orderable, versions equal.

**A defect discovered while planning, which EC-6 depends on.** `cli.py:770`
computes `cache_path = get_cache_path(args.cache_dir)` and the
`--check-versions` branch at `:776` then calls `check_staleness()` with **no
arguments**, dropping it. `pmcp refresh --check-versions --cache-dir X` silently
inspects the default cache instead of `X`. EC-6 requires a CLI-level regression
test; without this wiring, such a test cannot point the CLI at a fixture cache
and would have to reach past the CLI to monkeypatch — which is precisely the
"unit test dressed as a CLI test" EC-6 exists to rule out. SL-1 fixes the
wiring as a prerequisite for its own test, and the plan records it as an
in-scope behaviour change rather than smuggling it in.

**The ambient-environment contract (EC-4) requires no source change.** Every
stdio spawn path reaches `sanitized_subprocess_env` (`src/pmcp/env_store.py:124`),
whose first statement is `os.environ.copy()` — evaluated at spawn time, not
captured earlier. `_connect_stdio` calls it at `manager.py:2151`. So connect,
refresh, auto-reconnect and `update_server`'s restart already share one
live-environment contract; the phase's job is to *document the uniformity* and
*pin it with a test*, not to change behaviour. The roadmap decided this
deliberately over freezing or refusing, and that decision is confirmed and
carried into this plan unchanged.

## Interface Freeze Gates

- [ ] IF-0-UPDPATH-1 — `refresher.py` exposes one internal identity predicate
  with the frozen signature `_same_package(cached_package: str, configured_package: str | None) -> bool`,
  returning `False` when either side is empty, `None`, or unknown. All three
  call sites (`refresh_server`, `refresh_all`, `check_staleness`) route through
  it; no site re-implements the comparison inline. Frozen so the CHANGELOG entry
  and any later phase cite one stable name, and so the "unknown → refresh"
  semantics live in exactly one place that a test can pin.

## Lane Index & Dependencies

SL-1 — Package-identity gate across all three refresh sites
  Depends on: (none)
  Blocks: SL-3
  Parallel-safe: yes

SL-2 — Ambient-environment contract pinned and documented
  Depends on: (none)
  Blocks: SL-3
  Parallel-safe: yes

SL-3 — Documentation & spec reconciliation (SL-docs)
  Depends on: SL-1, SL-2
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-1 — Package-identity gate across all three refresh sites

- **Scope**: Resolve package identity before the freshness short-circuit at all three sites, refuse the short-circuit when identity differs or is unknown, and fix the CLI's dropped `cache_path` so the CLI-level regression test is honest.
- **Owned files**: `src/pmcp/manifest/refresher.py`, `src/pmcp/cli.py`, `tests/test_refresher.py`, `tests/test_cli.py`
- **Interfaces provided**: `_same_package` (IF-0-UPDPATH-1)
- **Interfaces consumed**: `compare_versions` (pre-existing), `detect_package_type` (pre-existing) — both live in `src/pmcp/manifest/version_checker.py` and are unchanged by this phase
- **Parallel-safe**: yes

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `tests/test_refresher.py` | `TestPackageIdentityGate` and `TestUnknownPackageForcesRefresh` — asserting the *post-fix* behaviour: a same-ecosystem package swap refreshes via `refresh_server`, via `refresh_all`, and is reported stale by `check_staleness`; an empty `package` forces a refresh. RED today. | `uv run pytest tests/test_refresher.py::TestPackageIdentityGate tests/test_refresher.py::TestUnknownPackageForcesRefresh -q` |
| SL-1.2 | test | — | `tests/test_cli.py` | `TestCheckVersionsIdentityGate` — asserting the *post-fix* behaviour: `pmcp refresh --check-versions --cache-dir <tmp>` reads `<tmp>`, and does not report up-to-date across a package swap. RED today. | `uv run pytest tests/test_cli.py::TestCheckVersionsIdentityGate -q` |
| SL-1.3 | impl | SL-1.1 | `src/pmcp/manifest/refresher.py` | — | — |
| SL-1.4 | impl | SL-1.2 | `src/pmcp/cli.py` | — | — |
| SL-1.5 | verify | SL-1.4 | `src/pmcp/manifest/refresher.py`, `src/pmcp/cli.py`, `tests/test_refresher.py`, `tests/test_cli.py` | all SL-1 tests | `uv run pytest tests/test_refresher.py tests/test_cli.py -q` |

**Implementation notes binding on SL-1.3:**

- Move `detect_package_type` / `pkg_name` resolution **above** the short-circuit
  (currently `:232`, must precede `:219`). EC-2 is this move; without it the
  guard has nothing to compare.
- The guard must read an unknown side as **"cannot confirm identity → refresh"**,
  never as **"cannot compare → skip the check."** The second phrasing is the
  natural one to reach for and is the same fail-open collapse as
  `not is_version_newer(...)`, which shipped three times (#155, #156, #163)
  before TRISTATE deleted the wrappers to make it unrepresentable. SL-1.1 must
  contain a test that fails under the skip-the-check phrasing specifically —
  not merely one that passes under the correct phrasing.
- `refresh_all` must not be able to bypass the gate by assembling the pair
  itself (EC-3). It hands `existing_servers.get(name)` to `refresh_server`, so
  routing every site through `_same_package` is what satisfies this; a test
  must call `refresh_all` directly rather than asserting only on `refresh_server`.

### SL-2 — Ambient-environment contract pinned and documented

- **Scope**: Pin with a test that a credential rotated during `update_server`'s probe window reaches the restarted server, and state the uniform live-environment contract in `update_server`'s own docstring.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: (none — documents existing behaviour)
- **Interfaces consumed**: `sanitized_subprocess_env` (pre-existing) — `src/pmcp/env_store.py:124`, unchanged by this phase. SL-2 consumes nothing from SL-1 and can run before, after, or alongside it.
- **Parallel-safe**: yes

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-2.1 | test | — | `tests/test_tools.py` | `TestUpdateServerAmbientEnvironment` — a value mutated in `os.environ` between probe and restart is present in the restarted server's spawn environment; the pre-rotation value is absent | `uv run pytest tests/test_tools.py::TestUpdateServerAmbientEnvironment -q` |
| SL-2.2 | impl | SL-2.1 | `src/pmcp/tools/handlers.py` | — | — |
| SL-2.3 | verify | SL-2.2 | `src/pmcp/tools/handlers.py`, `tests/test_tools.py` | all SL-2 tests | `uv run pytest tests/test_tools.py -q` |

**Implementation notes binding on SL-2:**

- SL-2.2 is a **docstring-only** change to `update_server`
  (`handlers.py:4942`). If it turns into a behaviour change, the lane has
  exceeded EC-4 and must stop and report — freezing the ambient environment is
  an explicit Non-goal of this phase.
- The docstring must frame the invariant as **uniformity** — "`update_server`
  follows the same live-environment contract as every other spawn path" — not
  as a probe-window special case, because there is no probe-window-specific
  code to point at.
- SL-2.1 asserts against the environment actually handed to the spawn, not
  against `os.environ` itself; asserting on `os.environ` would pass without
  the contract holding.

### SL-3 — Documentation & spec reconciliation (SL-docs)

- **Scope**: Refresh the docs catalog, record both fixes in the CHANGELOG, state the live-environment contract in user-facing docs, and append any post-execution amendments to the v12 roadmap whose criteria turned out wrong.
- **Owned files**: `.claude/docs-catalog.json`, `README.md`, `CHANGELOG.md`, `docs/**`, `specs/phase-plans-v12.md`, `plans/phase-plan-v12-UPDPATH.md`
- **Interfaces provided**: (none)
- **Interfaces consumed**: SL-1's `_same_package` semantics and CLI wiring change; SL-2's documented live-environment contract
- **Parallel-safe**: no (terminal)
- **Depends on**: SL-1, SL-2

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Action |
|---|---|---|---|---|
| SL-3.1 | docs | — | `.claude/docs-catalog.json` | Rescan the catalog. If `_shared/scaffold_docs_catalog.py` is absent, record "docs-catalog rescan helper unavailable; manual catalog audit" in the commit message and audit by hand. Known repo issue: the rescan drops root-level docs outside its roots (Consiliency/pmcp#171) — verify root entries survive rather than assuming. |
| SL-3.2 | docs | SL-3.1 | `README.md`, `CHANGELOG.md`, `docs/**` | CHANGELOG records **both** fixes (EC-5): the identity gate across all three sites, and the CLI `--cache-dir` wiring. README states the live-environment contract as uniform across every spawn path. Record intentionally-skipped catalog files in the commit message. |
| SL-3.3 | docs | SL-3.2 | `specs/phase-plans-v12.md`, `plans/phase-plan-v12-UPDPATH.md` | Append `### Post-execution amendments` naming any criterion this run found empirically wrong, dated. In particular record whether EC-7's "empty package forces refresh" needed a distinct code path or fell out of `_same_package` for free. |
| SL-3.4 | verify | SL-3.3 | — | `uv run ruff format --check src/ tests/`. No markdown linter is configured in this repo; record that as a no-op rather than inventing one. |

## Execution Policy
- execute: effort=medium
- SL-1: effort=high, reason=the guard phrasing is the defect and a fail-open form passes naive tests
- SL-2: effort=low, reason=docstring plus one spawn-environment assertion against unchanged behaviour
- SL-3: effort=low, reason=docs sweep with no logic

## Spec Closeout Plan
- schema: `spec_delta_closeout.v1`
- decision: `roadmap_amendment`
- target surfaces: `specs/phase-plans-v12.md`, `plans/phase-plan-v12-UPDPATH.md`, `CHANGELOG.md`, `README.md`
- evidence paths: `plans/phase-plan-v12-UPDPATH.md`
- redaction posture: `metadata_only`
- downstream handling: roadmap amendment

## Acceptance Criteria

Each criterion names a **test class the lane must create**, not a `-k`
substring. Substring filters were drafted first and rejected: against the tree
at v2.3.0, `-k identity` already collects 29 tests, `-k environ` 8, and
`-k refresh_all` / `-k unknown_package` one each — so four of the five would
have been **green before any lane ran**, and `-k check_versions` collected zero,
which fails the command outright. A named class that does not yet exist collects
zero and fails; once created it collects only this phase's tests. The class
names below are binding on the lanes.

- [ ] EC-UPDPATH-1 — proven by `uv run pytest tests/test_refresher.py::TestPackageIdentityGate -q`: a cache for `old-package@1.0.0` against a config for `new-package@1.0.0` (same ecosystem, both orderable, equal versions) refreshes instead of returning the cached descriptions. RED before SL-1.3.
- [ ] EC-UPDPATH-2 — proven by `uv run pytest tests/test_refresher.py::TestPackageIdentityGate -q`: the same class cannot pass unless identity is resolved before the short-circuit, since the short-circuit returns before `:232` is reached today.
- [ ] EC-UPDPATH-3 — proven by `uv run pytest tests/test_refresher.py::TestPackageIdentityGate -q`: a test calling `refresh_all` directly shows it carries package identity into the pair it assembles, so the by-name cache lookup cannot bypass EC-1.
- [ ] EC-UPDPATH-6 — proven by `uv run pytest tests/test_cli.py::TestCheckVersionsIdentityGate -q`: `pmcp refresh --check-versions` driven through `async_main` against a fixture cache does not print "All cached descriptions are up to date" across a same-ecosystem package swap, and honours `--cache-dir`.
- [ ] EC-UPDPATH-7 — proven by `uv run pytest tests/test_refresher.py::TestUnknownPackageForcesRefresh -q`: an entry whose `package` is `""` (how `load_descriptions_cache` renders an absent field, `refresher.py:70`) forces a refresh; and a companion test in the same class fails if the guard is phrased to skip the comparison when either side is unknown.
- [ ] EC-UPDPATH-4 — proven by `uv run pytest tests/test_tools.py::TestUpdateServerAmbientEnvironment -q`: a credential rotated during the probe window reaches the restarted server rather than a probe-time snapshot, and `update_server`'s docstring states the contract as uniform across spawn paths.
- [ ] EC-UPDPATH-5 — proven by `uv run pytest tests/ -q`, `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`, `uv run mypy src/pmcp --exclude baml_client`, and a CHANGELOG entry covering both fixes.

## Verification

```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
uv run coverage report --fail-under=60   # only after a --cov run; CI gate
```

RED proof required before SL-1.3 and SL-2.2 land — each lane must show the new
tests failing against the pre-phase tip, not merely passing afterward.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```

## Execution Notes

- **Deviation from the roadmap's lane sketch, deliberate.** Scope notes proposed
  three lanes split as A=`refresher.py`, B=`handlers.py`+docs, C=`tests/`. A
  standalone test lane cannot work here: every `impl` task must be preceded by a
  `test` task **in the same lane**, and a lane owning all of `tests/` would
  collide with both impl lanes on write ownership. Tests are folded into the
  lane that owns their subject, which keeps the count at three (SL-1, SL-2,
  SL-docs) and keeps ownership disjoint.
- **`cli.py` is in scope, which the roadmap's Key files did not list.** EC-6
  demands a CLI-level regression test and the CLI currently drops `cache_path`
  (`cli.py:770` computes it, `:776` ignores it). SL-1 owns the fix. This is a
  real user-visible behaviour change — `--cache-dir` starts working with
  `--check-versions` — and belongs in the CHANGELOG on its own, not as a test
  fixture detail.
- **Single-writer files**: `CHANGELOG.md` and `README.md` are owned solely by
  SL-docs. Neither impl lane writes them, so EC-5's CHANGELOG requirement is
  satisfied in SL-docs after both lanes verify. `specs/phase-plans-v12.md` is
  likewise SL-docs-only.
- **Known destructive changes**: none — every lane is purely additive except
  SL-1.3's relocation of the `detect_package_type` call within `refresh_server`,
  which moves existing lines rather than deleting behaviour.
- **Expected add/add conflicts**: none — there is no preamble lane and no lane
  stubs a file another lane replaces.
- **SL-0 re-exports**: not applicable — no preamble lane in this phase.
- **Worktree naming**: `claude-execute-phase` allocates unique worktree names
  via `scripts/allocate_worktree_name.sh`. This plan does not spell out lane
  worktree paths.
- **Stale-base guidance** (verbatim): Lane teammates working in isolated
  worktrees do not see sibling-lane merges automatically. If a lane finds its
  worktree base is pre-<first upstream dependency's merge>, it MUST stop and
  report rather than committing — the orchestrator will re-spawn or rebase.
  Silent `git reset --hard` or `git checkout HEAD~N -- …` in a stale worktree
  produces commits that destroy peer-lane work on `--no-ff` merge.
- **No external release is dispatched by this phase.** `validate_plan_doc`
  emits check (N) — "release-shaped plan has no post-dispatch evidence-reducer
  lane" — because SL-3 owns `CHANGELOG.md`. That heuristic is looking for a
  phase that cuts a tag or triggers a release workflow and then needs the
  resulting SHA back-filled. This phase writes a CHANGELOG entry under
  `[Unreleased]` and stops; v2.3.0 was already cut before planning. No reducer
  lane is needed, and adding one would have nothing to reduce.
- **Do not re-plan the cross-ecosystem case.** It is already correct via
  `compare_versions(...) == "incomparable"`. A lane that "fixes" it is fixing
  nothing and risks regressing the incomparable contract TRISTATE established.
  Verify it holds; do not touch it.
- **EC-4 is a decision, not a discovery.** The roadmap chose *document* over
  *freeze* or *refuse* for Consiliency/pmcp#162, and that choice was reconfirmed
  when this plan was written. Freezing would pin a credential the operator
  deliberately rotated; refusing would fail an update the operator wanted. A
  lane that finds the decision uncomfortable must stop and report rather than
  re-deciding it mid-execution.
