---
phase_loop_plan_version: 1
phase: P6CLEAN
roadmap: specs/phase-plans-v11.md
roadmap_sha256: 2f03c6f3c01d903e55b87bdbe4ca8b9b25fcbb318a48a30161d35cd6b76b3be0
---

# P6CLEAN: Deferred v10 Robustness Remnants

## Context

`specs/phase-plans-v10.md` is CLOSED. Its status header records file:line evidence
that six of its seven phases shipped as ordinary PRs across the 1.19.x-1.20.0 line,
outside this pipeline. P6CLEAN exists solely so the one genuinely unfinished item
from v10 Phase 6 is not silently dropped.

**The single remaining item.** `tests/test_manifest.py:1775`, inside
`TestInstallMonitor.test_monitor_server_ready_on_startup_pattern` (declared at
`tests/test_manifest.py:1756`), still gates its assertion on
`await asyncio.sleep(0.3)`. The test starts `bash -c "echo initialized && sleep 5"`
and then sleeps a fixed 0.3s before asserting `job.status == "server_ready"`. That is
a wall-clock race: under load the monitor may not have read and matched the startup
line yet, and the assertion fails for reasons unrelated to the code under test.

**The mechanism that makes a deterministic wait possible.** In
`src/pmcp/manifest/installer.py`, when `_is_server_started(decoded)` matches, the
monitor sets `job.status = "server_ready"`, sets `job.progress = 100`, cancels the
pending stream readers, and **returns** (`installer.py:331-341`). It deliberately does
*not* terminate the subprocess — the process is handed off to `ClientManager`. The
`finally` block preserves that: `job.process` is only cleared when status is not
`server_ready` (`installer.py:388-390`). The task handle is published as
`InstallJob._monitor_task` (`installer.py:61`, assigned at `installer.py:143`).

Because the monitor task *completes* on pattern match, awaiting that task is a fully
event-driven wait — strictly stronger than a poll loop, with no wall-clock dependency
at all.

**The idiom already exists in this exact test class.** Three sibling tests were
already converted to this form during the v10 work and are the pattern to match:

- `test_monitor_reads_stderr` (`tests/test_manifest.py:1658`) — `await asyncio.wait_for(job._monitor_task, timeout=5.0)`
- `test_monitor_keeps_last_20_lines` (`tests/test_manifest.py:1685`) — same, `timeout=5.0`
- the exit-code test ending at `tests/test_manifest.py:1730` — same, `timeout=2.0`, then asserts `job.status == "failed"`

Each awaits the monitor task to completion and *then* asserts terminal status. The
target test should read identically.

**Verified-already-done, do not re-plan.** The `_tasks` registry eviction that an
earlier draft of this phase claimed was missing is present and tested. It lives in
`src/pmcp/client/manager.py`, not `src/pmcp/tools/handlers.py` (where v10's prose
pointed). See `## Execution Notes > Pre-verified as complete` for the evidence table.

**Baseline measured on this branch before planning** (`plan/v11-p6clean`, base
`df6efe0`), after `uv sync --all-extras`: `uv run pytest -q` → 2276 passed, 3 skipped,
24 deselected in 218.79s; `uv run ruff check src/ tests/` → clean;
`uv run ruff format --check src/ tests/` → 83 files already formatted;
`uv run mypy src/pmcp --exclude baml_client` → no issues in 41 source files. The tree
is green going in, so any failure this phase surfaces is caused by this phase.

## Interface Freeze Gates

- [ ] IF-0-P6CLEAN-1 — `InstallJob._monitor_task` is the awaitable completion handle for install monitoring, and `JobManager._monitor_install` returns (rather than looping) once `_is_server_started` matches, having set `job.status = "server_ready"` and left `job.process` alive for handoff. Tests may await this task instead of sleeping. Frozen at `src/pmcp/manifest/installer.py:61,143,331-341,388-390`. This phase consumes this contract; it does not change it.

## Lane Index & Dependencies

- SL-0 - De-flake the startup-pattern monitor test; Depends on: (none); Blocks: SL-1; Parallel-safe: yes
- SL-1 - CHANGELOG, docs reconciliation, and phase reducer closeout; Depends on: SL-0; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - De-flake the startup-pattern monitor test

- **Scope**: Replace the fixed `asyncio.sleep(0.3)` in `test_monitor_server_ready_on_startup_pattern` with a deterministic await on the monitor task, matching the idiom its three siblings already use.
- **Owned files**: `tests/test_manifest.py`
- **Interfaces provided**: (none)
- **Interfaces consumed**: IF-0-P6CLEAN-1 — `InstallJob._monitor_task` (`src/pmcp/manifest/installer.py:61,143`), monitor return-on-match (`installer.py:331-341`), `job.process` preserved when `server_ready` (`installer.py:388-390`)
- **Parallel-safe**: yes (sole writer of `tests/test_manifest.py` this phase)

**The change.** At `tests/test_manifest.py:1774-1779`, replace:

```python
        # Wait for pattern detection
        await asyncio.sleep(0.3)

        job = manager.get_job(job_id)
        assert job is not None
        assert job.status == "server_ready"
```

with:

```python
        # Wait deterministically for the monitor to detect the startup pattern
        # and return, rather than racing a fixed wall-clock sleep against the
        # subprocess. The monitor returns as soon as it matches, deliberately
        # leaving the process alive for handoff.
        job = manager.get_job(job_id)
        assert job is not None
        assert job._monitor_task is not None
        await asyncio.wait_for(job._monitor_task, timeout=5.0)
        assert job.status == "server_ready"
```

The trailing cleanup block (`tests/test_manifest.py:1781-1783`, `job.process.kill()`)
stays exactly as-is and is still required: the monitor intentionally leaves the
`sleep 5` subprocess running.

**This exact edit was applied and validated during planning, then reverted** so this
branch stays a pure plan branch. Result: `-k test_monitor` → 7 passed; the target test
run 5 consecutive times → 5/5 passed; per-test wall time fell from 0.49s to 0.05-0.06s,
confirming the 0.3s floor is genuinely gone rather than merely hidden.

- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-0.1 | test | — | `tests/test_manifest.py` | `test_monitor_server_ready_on_startup_pattern` | `uv run pytest tests/test_manifest.py -k test_monitor_server_ready_on_startup_pattern -q` |
| SL-0.2 | impl | SL-0.1 | `tests/test_manifest.py` | — | — |
| SL-0.3 | verify | SL-0.2 | `tests/test_manifest.py` | all `TestInstallMonitor` tests | `uv run pytest tests/test_manifest.py -k test_monitor -q` |

Note on task ordering: SL-0 is a test-only lane, so the conventional "failing test
first, then impl" split degenerates. SL-0.1 is the characterization step — run the
target test and record its current pass state and wall time as the baseline. SL-0.2
applies the edit. SL-0.3 proves the whole `TestInstallMonitor` class still passes and
that the target test's wall time dropped. Do not add a new test; the roadmap's exit
criterion is about *how* the existing test waits.

### SL-1 - CHANGELOG, docs reconciliation, and phase reducer closeout

- **Scope**: Record in the CHANGELOG that this closes out v10 Phase 6, reconcile the docs catalog, and run whole-phase verification.
- **Owned files**: `CHANGELOG.md`, `.claude/docs-catalog.json`
- **Interfaces provided**: (none)
- **Interfaces consumed**: SL-0's merged change to `tests/test_manifest.py`
- **Parallel-safe**: no (terminal reducer)

The CHANGELOG entry goes under the existing `## [Unreleased]` heading
(`CHANGELOG.md:8`), which is currently empty. It is a test-only change, so it belongs
under a `### Fixed` (or `### Changed`) subheading and must state plainly that this
closes the last outstanding item from `specs/phase-plans-v10.md` Phase 6 — that
sentence is the audit trail EC-P6CLEAN-2 is asking for.

`.claude/docs-catalog.json` is currently an empty JSON array (`[]`). The rescan is
therefore expected to be a no-op; if it stays `[]`, record that explicitly in the
commit message rather than silently leaving the file untouched. No user-facing
documentation describes this test's timing, so no `docs/**` or `README.md` change is
expected — if that holds, say so in the commit message.

- **Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | docs | — | `.claude/docs-catalog.json` | — | `python3 "$(git rev-parse --show-toplevel)/.claude/skills/_shared/scaffold_docs_catalog.py" --rescan` (if the helper is absent, record "docs-catalog rescan helper unavailable; manual audit, catalog empty" and proceed) |
| SL-1.2 | docs | SL-1.1 | `CHANGELOG.md` | — | — |
| SL-1.3 | verify | SL-1.2 | — | full suite | see `## Verification` |

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`low`, unsupported=`inherit_default`, inherit-default=`true`
- SL-0: effort=`low`, reason=single mechanical edit to one test with the target form already validated and three sibling tests as precedent
- SL-1: work-unit=`phase_reducer`, effort=`low`, reason=one CHANGELOG paragraph plus a no-op catalog rescan

## Execution Notes

- **Lane count: one work lane, deliberately.** The roadmap declares this phase a single
  lane and that is correct — the substantive change is six lines in one test function.
  A second *work* lane would have to touch `tests/test_manifest.py` too, producing
  `overlapping_write_ownership` at runtime for zero parallelism gain. SL-1 is not a
  second work lane; it is the mandatory terminal docs/reducer lane that carries
  EC-P6CLEAN-2's CHANGELOG requirement, owns files disjoint from SL-0, and is marked
  `Parallel-safe: no`. I do not disagree with the roadmap's single-lane call.
- **Pre-verified as complete — do not re-plan any of these.** Every other v10 Phase 6
  exit criterion was checked against the code in this worktree during planning:

  | v10 P6 item | Status | Evidence |
  |---|---|---|
  | `_tasks` registry evicts terminal records | done | `src/pmcp/client/manager.py:507` (`_tasks`), `:508-512` (`_max_terminal_tasks = 100` + comment), `:1042-1060` (`_evict_terminal_tasks`, oldest-first by `updated_at`, active records never evicted), `:1061-1063` (`_terminal_task`), called at `:1039`; regression test `test_terminal_task_records_are_evicted_past_cap` at `tests/test_client_manager.py:1588` |
  | done-callback uses `pop(t, None)` | done | `src/pmcp/client/manager.py:597` — `task.add_done_callback(lambda t: self._background_task_servers.pop(t, None))`; the paired set uses `.discard` at `:596`, which is the correct set-typed equivalent |
  | Overlay warns when it shadows a shipped server | done | `src/pmcp/manifest/loader.py:632-637` — worded "overrides existing server", which is why a grep for "shadow" finds nothing |
  | `_find_project_manifest` symlink containment | done | `src/pmcp/manifest/loader.py:469-475` — resolves the candidate and requires it to stay within the ancestor tree |
  | Version-check URLs `quote`-escaped | done | `src/pmcp/manifest/version_checker.py:52,94,136,187` (npm, pypi, crates, docker) |
  | `test_monitor_reads_stderr`, `test_monitor_keeps_last_20_lines` poll instead of sleeping | done | `tests/test_manifest.py:1676-1679` and `:1705-1708` — both already use `await asyncio.wait_for(job._monitor_task, timeout=5.0)` with explanatory comments |

- **Provenance correction worth carrying forward.** v10 P6's de-flake criterion named
  only `test_monitor_reads_stderr` and `test_monitor_keeps_last_20_lines` — both of
  which are done. The `asyncio.sleep(0.3)` that actually survives is in a *third* test,
  `test_monitor_server_ready_on_startup_pattern`, which v10 never named. The v11
  roadmap points at it by line number (`tests/test_manifest.py:1775`), which is
  correct and is what this plan implements. So the carried item is real, but its
  provenance is the line pointer, not the v10 criterion text. Anyone re-reading v10's
  criterion literally would conclude the item was already finished; it is not.
- **The general lesson, restated because it already cost one draft.** Verify a
  "missing" item against the CODE, not against the roadmap prose that described it.
  Roadmap prose points at where an author *expected* code to live; the earlier draft
  searched `src/pmcp/tools/handlers.py` on that basis and wrongly concluded the task
  registry had no eviction.
- **Sibling `asyncio.sleep` calls are explicitly out of scope.** `tests/test_manifest.py`
  contains further sleeps at lines 1650, 1802, 1902, 1912, 1931, 1940, and 1961.
  EC-P6CLEAN-1 names only `:1775`, and v10 named only the two already-fixed tests.
  Converting the others is unscoped work with its own flake risk (several are
  cancellation/timing tests where a sleep is load-bearing, not incidental). Leave them.
  If the executor believes one is a genuine flake source, report it rather than
  fixing it in this phase.
- **`mypy` does not cover this change.** CI runs `uv run mypy src/pmcp --exclude
  baml_client` — `tests/` is not type-checked. A green mypy proves nothing about SL-0;
  `ruff check src/ tests/` and `ruff format --check src/ tests/` do cover it.
- **Environment.** Run `uv sync --all-extras`, never bare `uv sync`. Bare `uv sync`
  prunes pytest, after which `uv run pytest` silently falls through to a system pytest
  that cannot import `pmcp` and the failure looks like a code problem.
- **Never bind port 3344.** A live systemd PMCP gateway owns it on this host. Nothing
  in this phase needs a port, so no lane should start a server at all.
- **`TMPDIR` note.** Some older plans in `plans/` prefix commands with
  `TMPDIR=/var/tmp`. That was not needed in this worktree — the full suite, ruff, and
  mypy all ran clean without it. Only reach for it if a lane hits a `/tmp` space or
  permission error.
- **Single-writer files**: `tests/test_manifest.py` — owner SL-0. `CHANGELOG.md` and
  `.claude/docs-catalog.json` — owner SL-1. No file is written by more than one lane.
- **Known destructive changes**: none — SL-0 replaces six lines within one test
  function; no file or test is deleted.
- **Expected add/add conflicts**: none — there is no SL-0 preamble stub and no lane
  creates a file another lane replaces.
- **SL-0 re-exports**: none — this phase adds no symbols to any `__init__.py`.
- **Stale-base guidance** (copy verbatim): Lane teammates working in isolated worktrees
  do not see sibling-lane merges automatically. If a lane finds its worktree base is
  pre-<first upstream dependency's merge>, it MUST stop and report rather than
  committing — the orchestrator will re-spawn or rebase. Silent `git reset --hard` or
  `git checkout HEAD~N -- …` in a stale worktree produces commits that destroy
  peer-lane work on `--no-ff` merge. Concretely: SL-1 must see SL-0's change to
  `tests/test_manifest.py` before it runs SL-1.3.
- **Concurrency with other v11 phases.** P6CLEAN shares no file with P1, PG, or P5 and
  can be planned, executed, and merged independently of all of them.

## Acceptance Criteria

- [ ] EC-P6CLEAN-1 — `tests/test_manifest.py` contains no `asyncio.sleep` between
  `start_install` and the `server_ready` assertion in
  `test_monitor_server_ready_on_startup_pattern`; the wait is
  `await asyncio.wait_for(job._monitor_task, timeout=5.0)`. Proven by
  `uv run pytest tests/test_manifest.py -k test_monitor -q` (all pass) plus
  `rg -n 'asyncio.sleep' tests/test_manifest.py` showing no hit between line 1756 and
  the end of that test function. The grep is paired with the test run, not used alone.
- [ ] EC-P6CLEAN-1 — the test does not regress under load. Proven by running the target
  test 5 consecutive times, all passing, and by its per-test wall time dropping below
  the previous 0.3s floor:
  `for i in 1 2 3 4 5; do uv run pytest tests/test_manifest.py -k test_monitor_server_ready_on_startup_pattern -q; done`
- [ ] EC-P6CLEAN-2 — full suite, ruff, and mypy green. Proven by `uv run pytest -q`
  (expect ≥2276 passed, 3 skipped — the pre-phase baseline), `uv run ruff check src/ tests/`,
  `uv run ruff format --check src/ tests/`, and `uv run mypy src/pmcp --exclude baml_client`.
- [ ] EC-P6CLEAN-2 — CHANGELOG records the closeout. Proven by
  `rg -n 'v10' CHANGELOG.md` matching a new entry under `## [Unreleased]` that names
  Phase 6 of `specs/phase-plans-v10.md` as closed out.

## Verification

Run from the repo root after both lanes merge:

```bash
uv sync --all-extras
uv run pytest tests/test_manifest.py -k test_monitor -q
for i in 1 2 3 4 5; do uv run pytest tests/test_manifest.py -k test_monitor_server_ready_on_startup_pattern -q; done
rg -n 'asyncio.sleep' tests/test_manifest.py
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
uv run pytest -q
rg -n 'v10' CHANGELOG.md
git diff --check
git status --short
```

Expected: the `-k test_monitor` selection passes (7 tests); the 5-run loop is 5/5 with
each run well under the former 0.3s floor; the `rg` for `asyncio.sleep` shows no hit
inside `test_monitor_server_ready_on_startup_pattern` (hits elsewhere in the file are
expected and out of scope); ruff/format/mypy clean; full suite ≥2276 passed, 3 skipped;
`rg -n 'v10' CHANGELOG.md` matches the new Unreleased entry; `git status --short` clean
apart from the phase's own commits.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `tests/test_manifest.py`, `CHANGELOG.md`, `.claude/docs-catalog.json`
- evidence paths: `plans/phase-plan-v11-p6clean.md`
- redaction posture: `metadata_only`
- downstream handling: `none`
