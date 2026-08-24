---
phase_loop_plan_version: 1
phase: UPDPATH
roadmap: specs/phase-plans-v12.md
roadmap_sha256: 3b6439a9dddb9ef5b5b709ed6fa32f889a082ed0b0118554e6ae9c2265bf4847
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

The **docker** cross-ecosystem case is already handled and must not be
re-planned: a version against a digest classifies as
`compare_versions(...) == "incomparable"`, which is not `"not_newer"`, so the
short-circuit does not fire.

Two cases survive, and the second was missed on the first pass:

1. **Same ecosystem** — `@old/pkg@1.0.0` cached, `@new/pkg@1.0.0` configured,
   both npm, both orderable, versions equal.
2. **npm ↔ pypi ↔ cargo** — all produce orderable *release* versions, so
   `incomparable` never fires, and `GeneratedServerDescriptions`
   (`types.py:1359-1367`) stores `package` as a bare name with **no ecosystem**.
   So pypi `foo@1.0.0` against npm `foo@1.0.0` reads as the same package. This
   is why IF-0-UPDPATH-2 adds `package_type` to the cached entry: a name alone
   cannot express package identity, and the gate is only as good as what the
   cache remembers.

**The ambient-environment contract (EC-4) is two contracts, not one, and the
original single-sentence version was false.** Verified empirically at v2.3.0: a
manifest credential is resolved into `LocalMcpServerConfig.env`
(`config/loader.py:1025`), so rotating it during the probe window makes the
recheck's child environment differ from the probe's and `update_server` returns
`ok=False` **without restarting** (`tools/handlers.py:5090-5165`). It is not
"picked up by the restarted server." That refusal is the 2.2.1 TOCTOU guard
behaving as designed. What *is* live is the genuinely ambient case: both sides
derive from one `stripped_base`, so an ambient change cancels out and never
refuses, and `sanitized_subprocess_env` (`env_store.py:138`) calls
`os.environ.copy()` at spawn time — the same call connect, refresh and
auto-reconnect reach. The phase documents both halves and changes neither.

**A defect discovered while planning, which EC-6 depends on.** `cli.py:771`
computes `cache_path = get_cache_path(args.cache_dir)` and the
`--check-versions` branch at `:776` then calls `check_staleness()` with **no
arguments**, dropping it. `pmcp refresh --check-versions --cache-dir X` silently
inspects the default cache instead of `X`. EC-6 requires a CLI-level regression
test; without this wiring, such a test cannot point the CLI at a fixture cache
and would have to reach past the CLI to monkeypatch — which is precisely the
"unit test dressed as a CLI test" EC-6 exists to rule out. SL-1 fixes the
wiring as a prerequisite for its own test, and the plan records it as an
in-scope behaviour change rather than smuggling it in.

Neither half of EC-4 requires a source change: the refusal and the live-spawn
read both already work as described. The roadmap's *decision* — document rather
than freeze or refuse — is unchanged and confirmed; only its description of what
there is to document was corrected.

## Interface Freeze Gates

- [ ] IF-0-UPDPATH-1 — `refresher.py` exposes one internal identity predicate
  with the frozen signature
  `_same_package(cached_package: str, cached_type: str | None, configured_package: str | None, configured_type: str | None) -> bool`,
  returning `False` when **any** of the four is empty, `None`, or `"unknown"`.
  All three call sites (`refresh_server`, `refresh_all`, `check_staleness`)
  route through it; no site re-implements the comparison inline. Frozen so the
  CHANGELOG entry and any later phase cite one stable name, and so the
  "unknown → refresh" semantics live in exactly one place that a test can pin.
  **The type arms are not decorative** — a bare-name comparison cannot tell npm
  `foo` from pypi `foo`, and both produce orderable release versions, so the
  short-circuit fires on a genuine package swap.
- [ ] IF-0-UPDPATH-2 — `GeneratedServerDescriptions` gains
  `package_type: str | None = None` (`src/pmcp/types.py:1359`), and
  `load_descriptions_cache` populates it via
  `server_data.get("package_type")` — defaulting to `None`, **not** `""`, so an
  absent value is distinguishable from a recorded empty one. Every cache
  written before this phase therefore reads as unknown, which forces a refresh
  under EC-UPDPATH-7; the migration is safe by construction and needs no
  version bump of the cache format.

## Lane Index & Dependencies

SL-1 — Package-identity gate across all three refresh sites
  Depends on: (none)
  Blocks: SL-3
  Parallel-safe: yes

SL-2 — Probe-window environment contract: refusal + live spawn
  Depends on: (none)
  Blocks: SL-3
  Parallel-safe: yes

SL-3 — Documentation & spec reconciliation (SL-docs)
  Depends on: SL-1, SL-2
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-1 — Package-identity gate across all three refresh sites

- **Scope**: Give the cached entry a package type, resolve package identity before the freshness short-circuit at all three sites, refuse the short-circuit when identity differs or is unknown, close the failure paths that write a stale entry back, and fix the CLI's dropped `cache_path` so the CLI-level regression test is honest.
- **Owned files**: `src/pmcp/manifest/refresher.py`, `src/pmcp/types.py`, `src/pmcp/cli.py`, `tests/test_refresher.py`, `tests/test_cli.py`
- **Interfaces provided**: `_same_package` (IF-0-UPDPATH-1), `GeneratedServerDescriptions.package_type` (IF-0-UPDPATH-2)
- **Interfaces consumed**: `compare_versions` (pre-existing), `detect_package_type` (pre-existing) — both live in `src/pmcp/manifest/version_checker.py` and are unchanged by this phase
- **Parallel-safe**: yes

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `tests/test_refresher.py` | `TestPackageIdentityGate` and `TestUnknownPackageForcesRefresh` — asserting the *post-fix* behaviour: a same-ecosystem package swap refreshes via `refresh_server`, via `refresh_all`, and is reported stale by `check_staleness`; an npm↔pypi swap at an equal version does the same; an empty `package` or absent `package_type` forces a refresh. RED today. | `uv run pytest tests/test_refresher.py::TestPackageIdentityGate tests/test_refresher.py::TestUnknownPackageForcesRefresh -q` |
| SL-1.2 | test | — | `tests/test_refresher.py` | `TestMismatchedCacheNeverSurvivesFailure` — a failed or raising `refresh_server` must not write the identity-mismatched entry back through either `refresh_all` fallback path. RED today. | `uv run pytest tests/test_refresher.py::TestMismatchedCacheNeverSurvivesFailure -q` |
| SL-1.3 | test | — | `tests/test_cli.py` | `TestCheckVersionsIdentityGate` — asserting the *post-fix* behaviour: `pmcp refresh --check-versions --cache-dir <tmp>` reads `<tmp>`, and does not report up-to-date across a package swap. RED today. | `uv run pytest tests/test_cli.py::TestCheckVersionsIdentityGate -q` |
| SL-1.4 | impl | SL-1.1 | `src/pmcp/types.py` | — | — |
| SL-1.5 | impl | SL-1.4 | `src/pmcp/manifest/refresher.py` | — | — |
| SL-1.6 | impl | SL-1.3 | `src/pmcp/cli.py` | — | — |
| SL-1.7 | verify | SL-1.6 | `src/pmcp/manifest/refresher.py`, `src/pmcp/types.py`, `src/pmcp/cli.py`, `tests/test_refresher.py`, `tests/test_cli.py` | all SL-1 tests | `uv run pytest tests/test_refresher.py tests/test_cli.py -q` |

**Implementation notes binding on SL-1.4 – SL-1.6:**

- Move `detect_package_type` / `pkg_name` resolution **above** the short-circuit
  (currently `:232`, must precede `:219`). EC-2 is this move; without it the
  guard has nothing to compare. **Move it — do not duplicate it.** A second
  resolution call above the early return would satisfy EC-1 and EC-3 while
  leaving the original below, which is not what EC-2 asks for; SL-1.7 must
  confirm exactly one `detect_package_type` call remains in `refresh_server`.
- `refresh_all`'s failure paths are part of the gate, not separate from it
  (`refresher.py:358-367` returns the existing entry when `refresh_server`
  returns `None` or raises, and `:376-378` re-adds every cached entry missing
  from the new set). An identity-mismatched entry must not survive either path
  back into the saved cache. Dropping the entry on a failed regeneration is
  acceptable; writing the wrong package's descriptions back is not.
- Populate `package_type` wherever an entry is written, not only where it is
  read — an entry regenerated by this phase must carry its type, or the next
  run reads it as unknown and refreshes forever.
- **`check_staleness`'s polarity is inverted relative to `refresh_server`, and a
  mechanical port of the guard will be backwards.** `refresh_server` compares
  `== "not_newer"` to *skip* a refresh (`:221`); `check_staleness` compares
  `== "newer"` to *add* to the stale dict (`:422`). An identity mismatch must
  therefore **add** the server to `stale_servers`, not refuse a short-circuit.
  Cosmetic corollary for SL-3: the stale tuple is
  `(cached_version, latest_version)`, so an identity-stale entry at an equal
  version prints `srv: 1.0.0 -> 1.0.0` at the CLI — confusing but not wrong.
- **The configured-unknown arms have no possible behavioural RED test.**
  `refresh_server` falls back to `pkg_name = f"{command} {args}"` (`:233-234`),
  so the configured name is never empty. Direct predicate assertions are the
  only pin for that half of IF-0-UPDPATH-1, and SL-1.1 must include them —
  at minimum `_same_package("", None, "x", "npm") is False`,
  `_same_package("x", None, "x", "npm") is False` (unknown *type*, known name),
  and `_same_package("x", "npm", None, "npm") is False`.
- **Read the RED requirement per-test, not per-command.** A class-level command
  can fail overall while a non-discriminating companion test inside it is green,
  hiding behind a red classmate. Each new test must be shown red individually.
- Expect existing SL-1-owned tests to go red and need fixture updates —
  `TestCheckStaleness`'s `package="@test/mcp"` fixtures and
  `TestUpToDateShortCircuit` both predate any identity gate. Same files, same
  lane, so no ownership problem; treat it as expected work rather than a
  regression. Note that `test_short_circuit_is_a_single_compare_versions_call`
  (`tests/test_refresher.py:707`, asserts `result is existing`) is what catches
  a degenerate always-`False` gate, so it must keep passing.
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

### SL-2 — Probe-window environment contract: refusal + live spawn

- **Scope**: Pin with two tests what actually happens across `update_server`'s probe window — a config-driven env change is refused, a genuinely ambient one is live at spawn — and state both halves in `update_server`'s own docstring.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: (none — documents existing behaviour)
- **Interfaces consumed**: `sanitized_subprocess_env` (pre-existing) — `src/pmcp/env_store.py:124`, unchanged by this phase. SL-2 consumes nothing from SL-1 and can run before, after, or alongside it.
- **Parallel-safe**: yes

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-2.1 | test | — | `tests/test_tools.py` | `TestUpdateServerAmbientEnvironment` — **two** tests. (a) a *manifest credential* rotated between probe and recheck yields `ok=False`, no restart, and a message naming the config change; (b) a *genuinely ambient* variable (not an explicit `env` override) mutated in the same window does **not** cause a refusal, and the restarted server's spawn environment carries the new value | `uv run pytest tests/test_tools.py::TestUpdateServerAmbientEnvironment -q` |
| SL-2.2 | impl | SL-2.1 | `src/pmcp/tools/handlers.py` | — | — |
| SL-2.3 | verify | SL-2.2 | `src/pmcp/tools/handlers.py`, `tests/test_tools.py` | all SL-2 tests | `uv run pytest tests/test_tools.py -q` |

**Implementation notes binding on SL-2:**

- SL-2.2 is a **docstring-only** change to `update_server`
  (`handlers.py:4942`). If it turns into a behaviour change, the lane has
  exceeded EC-4 and must stop and report — freezing the ambient environment is
  an explicit Non-goal of this phase.
- The docstring must state **both** halves and must not collapse them into a
  single "live environment" sentence. A config-driven change (which includes
  every manifest credential, since `config/loader.py:1025` resolves it into
  `LocalMcpServerConfig.env`) is **refused**: the guarantee is "the config
  restarted onto is the config that was probed," and the operator's rotation
  applies on the next update. A genuinely ambient variable is **live at spawn**:
  both sides of the guard derive from one `stripped_base`, so it cancels out and
  never causes a refusal, and `sanitized_subprocess_env` reads `os.environ` at
  spawn time exactly as connect, refresh and auto-reconnect do.
- SL-2.1(b) asserts against the environment actually handed to the spawn, not
  against `os.environ` itself; asserting on `os.environ` would pass without
  the contract holding. **Two ways to write it that pass vacuously, both of
  which must be avoided:**
  1. A fully mocked `ClientManager` has no spawn to capture — `_connect_stdio`
     builds the env internally at `client/manager.py:2151`, so a test against
     the mock is green whether or not the contract holds. Patch at the spawn
     boundary (`asyncio.create_subprocess_exec`) with a real `_connect_stdio`.
  2. Rotating a **managed** secret key tests the wrong contract:
     `sanitized_subprocess_env` strips every managed key from the base and
     re-applies only the server's own resolved value, so a managed-key rotation
     exercises the `own` arm — which is 4a's path, not 4b's. 4b must rotate a
     genuinely non-managed ambient key.
- **Do not "fix" the refusal.** It is the 2.2.1 TOCTOU guard, which landed after
  its own board review, and reopening it is an explicit Non-goal. A lane that
  believes the refusal is wrong must stop and report rather than change it.

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
- [ ] EC-UPDPATH-3 — proven by `uv run pytest tests/test_refresher.py::TestPackageIdentityGate tests/test_refresher.py::TestMismatchedCacheNeverSurvivesFailure -q`: a test calling `refresh_all` directly shows it carries package identity into the pair it assembles, **and** that an identity-mismatched entry survives neither the `None`/raise fallback (`:358-367`) nor the final merge (`:376-378`) back into the saved cache.
- [ ] EC-UPDPATH-6 — proven by `uv run pytest tests/test_cli.py::TestCheckVersionsIdentityGate -q`: `pmcp refresh --check-versions` driven through `async_main` against a fixture cache does not print "All cached descriptions are up to date" across a same-ecosystem package swap, and honours `--cache-dir`.
- [ ] EC-UPDPATH-7 — proven by `uv run pytest tests/test_refresher.py::TestUnknownPackageForcesRefresh -q`: an entry whose `package` is `""` (how `load_descriptions_cache` renders an absent field, `refresher.py:70`) forces a refresh; and a companion test in the same class fails if the guard is phrased to skip the comparison when either side is unknown.
- [ ] EC-UPDPATH-4 — proven by `uv run pytest tests/test_tools.py::TestUpdateServerAmbientEnvironment -q`: 4a — a manifest credential rotated during the probe window yields `ok=False` with no restart; 4b — a genuinely ambient variable rotated in the same window causes no refusal and reaches the restarted server's spawn environment. `update_server`'s docstring states both halves.
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
  (`cli.py:771` computes it, `:776` ignores it). SL-1 owns the fix. This is a
  real user-visible behaviour change — `--cache-dir` starts working with
  `--check-versions` — and belongs in the CHANGELOG on its own, not as a test
  fixture detail.
- **Single-writer files**: `CHANGELOG.md` and `README.md` are owned solely by
  SL-docs. Neither impl lane writes them, so EC-5's CHANGELOG requirement is
  satisfied in SL-docs after both lanes verify. `specs/phase-plans-v12.md` is
  likewise SL-docs-only.
- **Known destructive changes**: none — every lane is purely additive except
  SL-1.5's relocation of the `detect_package_type` call within `refresh_server`,
  which moves existing lines rather than deleting behaviour. "Purely additive"
  is about *files*, not about *test outcomes*: existing SL-1-owned tests will
  need fixture updates once the gate exists (see SL-1's implementation notes).
- **`tests/conftest.py` is owned by no lane, deliberately.** Lanes must define
  new fixtures in their own test file rather than reaching for the shared
  conftest; a lane that edits it produces an unowned dirty path and fails the
  closeout's `phase_owned_dirty` check.
- **`docs/` does not exist in this repo.** SL-3 owning `docs/**` is harmless —
  it may create it — but `README.md` is where the environment contract must
  actually land. Do not treat an empty `docs/**` glob as the contract being
  documented.
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

## Post-execution amendments (2026-08-24)

Recorded by SL-3 after SL-1 and SL-2 merged. The full list of what execution
discovered — including two operator-visible consequences this plan did not
anticipate — is in `specs/phase-plans-v12.md` under
"Post-execution amendments — UPDPATH (2026-08-24)". Only the points where **this
document** was wrong or imprecise are repeated here.

- **"Resolve identity before the short-circuit" was implemented as "resolve
  before, compare inside."** The `detect_package_type` / `pkg_name` resolution
  moved above the early return as this plan required, and exactly one call
  remains in `refresh_server`. The `_same_package` **call**, however, is the
  second conjunct of the existing `if version and … and compare_versions(…) ==
  "not_newer"`, so `get_package_version` still runs before identity is
  consulted. SL-1 reported that placing the guard ahead of the fetch broke
  `TestUpToDateShortCircuit`'s call-count assertion, since a mismatch would then
  skip the version lookup entirely. This plan read as though the guard would sit
  ahead of the fetch; it does not, and nothing in EC-UPDPATH-1..3 requires it to.
- **The `TestUpToDateShortCircuit` fixture warning was misplaced.** That class
  needed no changes at all: both of its tests assert the short-circuit does
  *not* fire, so an extra always-`False` conjunct cannot move their outcome —
  which also means neither of them discriminates a degenerate gate. The
  prediction was right for `TestCheckStaleness` (three fixtures gained
  `package_type="npm"`), and the test this plan correctly identified as the one
  that catches an always-`False` gate,
  `test_short_circuit_is_a_single_compare_versions_call`, lives in
  `TestShortCircuitUsesCompareVersions`, not in `TestUpToDateShortCircuit`; its
  fixture needed `package_type="npm"` too. All four were pure insertions, so
  `git diff --numstat -- tests/` reports zero deletions and the fixture work is
  invisible unless you read the hunks.
- **EC-3 needed state, not just a gated pair.** This plan named both of
  `refresh_all`'s failure paths correctly but not what closing them costs:
  setting `existing = None` stops the callee from short-circuiting and does
  nothing about the final merge loop, which re-adds cached entries straight from
  `existing_servers`. `refresh_all` keeps a `mismatched: set[str]` and excludes
  those names from the merge.
- **EC-7 needed no code path of its own** — the empty-`package` case falls out of
  `_same_package`'s any-unknown arm for free.
- **`roadmap_sha256` in this document's frontmatter is now stale** and was left
  that way deliberately: it pins the roadmap as it stood when this plan was
  written, and the closeout amendment above changed the roadmap. The v12 plans
  that ran before this one carry a stale pin for the same reason.
