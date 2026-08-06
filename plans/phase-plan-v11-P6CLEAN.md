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

Read that baseline with the caveat that `pyproject.toml:81` sets
`addopts = "-m 'not live'"`: the 2276 excludes every `live`-marked integration test, and
no part of it boots the gateway. That is why `## Verification` has a mandatory second
step and why this plan does not treat a green suite as acceptance.

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

- **Scope**: Record in the CHANGELOG that this closes out v10 Phase 6, reconcile the docs catalog, and run whole-phase verification including the mandatory gateway boot and real downstream tool call.
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
| SL-1.4 | verify | SL-1.3 | — | gateway boot + real downstream call | see `## Verification` step 2; satisfies cross-cutting principle 1 |

SL-1.4 creates its fixture server and isolated config in a temp dir at verification
time rather than committing them, so this deliberately-small phase adds no new repo
files. The tradeoff is that the gate is not reusable by CI; making it durable is worth
doing but belongs to a phase that is allowed to add source, not this one.

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`low`, unsupported=`inherit_default`, inherit-default=`true`
- SL-0: effort=`low`, reason=single mechanical edit to one test with the target form already validated and three sibling tests as precedent
- SL-1: work-unit=`phase_reducer`, effort=`medium`, reason=carries the principle-1 gateway boot whose lock-dir and isolated-config constraints can kill the operator's live gateway if handled carelessly

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
- **Gateway acceptance is required, and this plan originally omitted it.** Cross-cutting
  principle 1 (`specs/phase-plans-v11.md:87`) is unconditional: every phase touching
  `pmcp` must prove (a) the process starts and listens and (b) a real downstream server
  serves a real tool call through it. The first draft of this plan verified only
  pytest/ruff/mypy and asserted "no lane should start a server at all." That was wrong.
  The principle's stated rationale is *this repo's own history* — "2276 green tests
  coexisted with a gateway that could not boot" — and the draft's verification section
  leaned on exactly that 2276-test count as its proof. Argued in full under
  `## Why the gateway boot stays, for a test-only phase`.
- **Default pytest is weaker than it looks.** `pyproject.toml:81` sets
  `addopts = "-m 'not live'"`, so `uv run pytest -q` silently excludes every test marked
  `live` — the opt-in integration tests that touch real package managers and network.
  A green 2276 therefore says nothing about live integration. This is a second reason
  the boot check is not redundant with the suite.
- **Gateway boot safety — non-negotiable, every mechanism confirmed in source.**
  The hazard: `_kill_orphan_processes` (`src/pmcp/server.py:683`) scans `/proc` on Linux
  and sends **SIGKILL** to any process whose `(Path(command).name, tuple(args))`
  fingerprint (`server.py:700`) matches a *configured* stdio server. It is fed
  `resolution.lazy_configs + resolution.eager_configs` (`server.py:604`). So a careless
  second instance SIGKILLs the live gateway's downstream children — violating principle 3.
  - **`HOME` MUST be redirected to a throwaway dir on the boot invocation. This is the
    load-bearing step.** An earlier draft of this plan relied on `--config` alone, which
    is **wrong**: `--config` is *additive*, appended after the project and user paths
    (`src/pmcp/config/loader.py:285-299`), and the server resolves shipped-manifest
    entries regardless. Redirecting `HOME` is what actually suppresses `~/.mcp.json`,
    `~/.claude/.mcp.json`, `~/.pmcp/manifest.yaml`, and the default singleton lock —
    `default_user_config_paths()` resolves `Path.home()` **at call time**
    (`config/loader.py:48-53`), so the env var genuinely takes effect. P1, PG, and P5 all
    already do this.
  - **Spare port only. Never 3344.** A live systemd PMCP gateway owns `127.0.0.1:3344`
    on this host and it is the operator's daily driver (principle 3).
  - **MUST also pass `--lock-dir <tmpdir>`** (`src/pmcp/cli.py:191`; `PMCP_LOCK_DIR` at
    `cli.py:2201`). `_run_http` calls `acquire_singleton_lock(self._lock_dir)` and `None`
    means a **global** lock under `~/.pmcp` (`src/pmcp/server.py:779-784`,
    `acquire_singleton_lock` at `src/pmcp/identity.py:176`). Keep it even with `HOME`
    redirected — belt and braces, and it makes the intent explicit.
  - **MUST also pass `--config <isolated>`, `--project <empty dir>`, and `--policy
    <fixture-only>`** (`src/pmcp/cli.py:139,133,144`). The policy is a real second layer,
    not decoration: `is_server_allowed` is threaded into `resolve_startup_configs`
    (`server.py:569`) and only surviving configs reach `_kill_orphan_processes`
    (`server.py:604`), so a fixture-only allowlist narrows the kill set directly.
  - **The fixture's fingerprint must be non-colliding by construction.** Both tuple
    elements are made unique: command basename `p6clean-fixture-server` (a wrapper script
    in the temp dir — no real server is named that) and a sole argument that is a fresh
    `mktemp -d` path. Matching requires *both* to be equal, so no live process can match.
  - **Assert principle 3 explicitly, before and after.** Snapshot `:3344`'s listener PID
    and its children, and require both the health probe and the child PID set to be
    unchanged afterwards. That is the evidence, not an assumption.
  - **`/health` proves only that the HTTP server answers** — `transport/http.py:436`
    returns `"ok": True` as a hardcoded literal. It covers principle 1(a) only; the real
    `gateway.invoke` round-trip is the sole proof of 1(b).
  - **Guarded teardown.** Boot under `trap`/`finally` so the child gateway is killed and
    the temp dir removed even when an assertion fails midway.
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
- **Concurrency with other v11 phases — the roadmap's "zero contention" claim is FALSE.**
  The first draft of this plan repeated it ("shares no file with P1, PG, or P5"). That
  does not survive contact with the lane plans. Verified against the roadmap's own
  **Key files** blocks: P1 lists `tests/` and `CHANGELOG.md`; P5 lists `tests/`,
  `CHANGELOG.md`, and `src/pmcp/manifest/installer.py`. See
  `## Cross-Phase Serialization` — this is a merge-ordering contract, not a footnote.

## Why the gateway boot stays, for a test-only phase

The reasonable objection is that a boot-plus-downstream-call is disproportionate for a
change that edits six lines of one test and cannot touch shipped code. I considered
arguing for an exemption and decided against it. The reasoning, stated so it can be
overruled deliberately rather than by omission:

1. **The principle is unconditional and its rationale is precisely this argument.**
   "This change is too small to break the boot" is the reasoning principle 1 exists to
   defeat. #111 shipped a gateway that was dead on arrival while the suite was green,
   because `uv.lock` pinned a working `mcp` and nothing ever booted the installed
   artifact. An exemption granted on size is the same bet that already lost.
2. **A test-only diff is exactly where the check is cheapest and least ambiguous.**
   SL-0 cannot plausibly break the boot, so the gate should pass first try. That makes
   it a near-zero-cost regression tripwire, not a research task. If it *does* fail, the
   failure is real and attributable — the tree was green before this phase (baseline
   measured in `## Context`).
3. **It is load-bearing on the merged union, which is the thing that actually ships.**
   Per `## Cross-Phase Serialization`, P6CLEAN merges alongside P1 and P5, and **P5
   modifies `src/pmcp/manifest/installer.py`** — shipped gateway code, and the very file
   IF-0-P6CLEAN-1 freezes. Verifying P6CLEAN in isolation proves little; the boot check
   run on the merged union is where it earns its cost.
4. **The suite cannot substitute for it.** `pyproject.toml:81` excludes `live` tests by
   default, so the headline "2276 passed" covers no live integration at all.

Net: the incremental cost is one guarded boot in the reducer lane; the incremental risk
of skipping it is the exact failure mode the roadmap was written to stop. If the
operator still wants the exemption, the place to grant it is a roadmap amendment to
principle 1 — not a silent omission in a lane plan.

## Cross-Phase Serialization

P6CLEAN is **not** independently mergeable. Shared write surfaces, verified against the
roadmap's **Key files** blocks:

| File | P6CLEAN | P1 | P5 | Severity |
|---|---|---|---|---|
| `tests/test_manifest.py` | SL-0 (writes) | `tests/` | `tests/` + owns `src/pmcp/manifest/installer.py` | **Sharp** — a real test-file conflict |
| `CHANGELOG.md` | SL-1 (writes) | writes | writes | Trivial — append conflict under `## [Unreleased]` |
| `.claude/docs-catalog.json` | SL-1 (writes) | writes | writes | Trivial — currently `[]`, likely no-op |

**Merge ownership order: P6CLEAN merges FIRST.** It is the smallest diff, it is
test-only, and it has no dependency on either other phase. P1 and P5 then rebase onto
it. The inverse order forces the six-line test edit to be re-derived on top of whatever
P5 did to the same file, which is strictly worse.

**The sharp edge is P5, and it is worse than a textual conflict.** P5 owns
`src/pmcp/manifest/installer.py` — the file IF-0-P6CLEAN-1 freezes. SL-0's assertion
depends on `_monitor_install` *returning* on startup-pattern match
(`installer.py:331-341`) and on `job.process` surviving when status is `server_ready`
(`installer.py:388-390`). If P5 changes either, SL-0's `await asyncio.wait_for(job._monitor_task, ...)`
hangs to its 5s ceiling and fails — and it will fail in P5's branch, not this one, where
the cause is non-obvious. **Action:** P5's executor must be told IF-0-P6CLEAN-1 is a
frozen contract it consumes. If P5 needs to change monitor return semantics, that is a
roadmap amendment, not a local decision.

**Verify the merged union, not each phase in isolation.** Whichever phase merges last
runs the full `## Verification` block — including the gateway boot — against the merged
result. A per-phase green is necessary but not sufficient; three phases each green in
isolation can still produce a broken union, which is the same class of gap principle 1
describes.

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
- [ ] Cross-cutting principle 1 (`specs/phase-plans-v11.md:87`) — the gateway starts and
  listens, and a real downstream server serves a real tool call through it. Proven by
  `## Verification` step 2: `GET http://127.0.0.1:$PORT/health` returns 200 (1a), and an
  MCP client call to `gateway.invoke` returns `p6clean-pong:p6clean` from the fixture
  process (1b — `/health` alone does not prove this; `transport/http.py:436` hardcodes
  `"ok": True`). Run with `HOME` redirected, on a spare port, with `--lock-dir`,
  `--config`, `--project`, and a fixture-only `--policy`; never against 3344.
- [ ] Cross-cutting principle 3 (`specs/phase-plans-v11.md:89`) — the operator's gateway
  survives the boot untouched. Proven by `## Verification` step 2's before/after
  assertions: `curl -sf http://127.0.0.1:3344/health` succeeds both times, and
  `pgrep -P $LIVE_PID` returns a byte-identical child PID set.
- [ ] Cross-phase — the merged union of P6CLEAN with whichever of P1/P5 land alongside it
  passes the whole `## Verification` block, not merely each phase in isolation. Proven by
  the last phase to merge re-running the block against the merged result.

## Verification

Run from the repo root after both lanes merge — and again on the merged union if P1 or
P5 land alongside this phase (see `## Cross-Phase Serialization`).

### Step 1 — Suite, lint, types, source-level criterion

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

Remember `uv run pytest -q` applies `-m 'not live'` (`pyproject.toml:81`). Step 1 alone
is **not** acceptance for this phase.

### Step 2 — Gateway boot + real downstream tool call (cross-cutting principle 1)

Read `## Execution Notes > Gateway boot safety` before running this. Spare port only;
`--lock-dir` and an isolated `--config` are both mandatory.

```bash
set -euo pipefail
WORK="$(mktemp -d)"; PORT=38344            # spare port — NEVER 3344
FAKE_HOME="$WORK/home"; mkdir -p "$FAKE_HOME" "$WORK/proj"
GW_PID=""
cleanup() {
  [ -n "$GW_PID" ] && kill "$GW_PID" 2>/dev/null || true
  [ -n "$GW_PID" ] && wait "$GW_PID" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

# --- BEFORE: snapshot the operator's live gateway (principle 3 evidence) ---
curl -sf http://127.0.0.1:3344/health >/dev/null || { echo "live gateway down BEFORE; abort"; exit 1; }
LIVE_PID="$(ss -ltnpH 'sport = :3344' | grep -oP 'pid=\K[0-9]+' | head -1)"
[ -n "$LIVE_PID" ] || { echo "could not resolve :3344 listener pid; abort"; exit 1; }
# `|| true` is required: pgrep -P exits 1 when a process has no children, which is a
# perfectly valid state and must not kill the step under `set -e`. The [ -n ] guard
# above is the one that must still fail hard.
LIVE_KIDS_BEFORE="$(pgrep -P "$LIVE_PID" | sort | tr '\n' ' ' || true)"
echo "live gateway pid=$LIVE_PID children=[$LIVE_KIDS_BEFORE]"

# --- Fixture: fingerprint cannot collide, by construction ---
# _kill_orphan_processes fingerprints a config as (Path(command).name, tuple(args))
# (server.py:700). BOTH elements below are unique to this run: the command basename
# is p6clean-fixture-server (no real server is named that) and the sole arg is a
# fresh mktemp path. No live process can match the pair.
cat > "$WORK/p6clean_fixture_server.py" <<'PY'
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("p6clean-fixture")

@mcp.tool()
def ping(value: str) -> str:
    """Echo the value back, prefixed, so the response is unambiguously ours."""
    return f"p6clean-pong:{value}"

if __name__ == "__main__":
    mcp.run()
PY
cat > "$WORK/p6clean-fixture-server" <<PY
#!/bin/sh
exec "$PWD/.venv/bin/python" "$WORK/p6clean_fixture_server.py"
PY
chmod +x "$WORK/p6clean-fixture-server"

# Isolated config: ONLY the fixture. Note --config is ADDITIVE (config/loader.py:285-299),
# so this alone does not bound resolution — HOME redirection below is what does.
cat > "$WORK/config.json" <<PY
{"mcpServers": {"p6clean_fixture": {"command": "$WORK/p6clean-fixture-server", "args": ["--p6clean-run", "$WORK"]}}}
PY

# Fixture-only policy. is_server_allowed is threaded into resolve_startup_configs
# (server.py:569), and only surviving configs reach _kill_orphan_processes
# (server.py:604) — so this genuinely narrows the kill set.
cat > "$WORK/policy.yaml" <<'PY'
servers:
  allowlist:
    - p6clean_fixture
PY

# --- BOOT: HOME redirected. This is the load-bearing isolation. ---
# default_user_config_paths() resolves Path.home() at call time (config/loader.py:48-53),
# so HOME= suppresses ~/.mcp.json, ~/.claude/.mcp.json, ~/.pmcp/manifest.yaml, and the
# default singleton lock. .venv/bin/pmcp is used instead of `uv run` so the redirected
# HOME cannot send uv looking for a cache that isn't there.
env HOME="$FAKE_HOME" XDG_CONFIG_HOME="$FAKE_HOME/.config" \
  "$PWD/.venv/bin/pmcp" --transport http --host 127.0.0.1 --port "$PORT" \
  --lock-dir "$WORK/lock" --config "$WORK/config.json" \
  --project "$WORK/proj" --policy "$WORK/policy.yaml" > "$WORK/gw.log" 2>&1 &
GW_PID=$!

for _ in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$PORT/health" > /dev/null && break
  kill -0 "$GW_PID" 2>/dev/null || { echo "gateway exited early:"; cat "$WORK/gw.log"; exit 1; }
  sleep 0.5
done

# --- Confirm isolation actually took ---
# The ONLY number that means anything here is eager+lazy, because that is exactly what
# _kill_orphan_processes receives (server.py:604). It must equal 1 — our fixture, and
# nothing else. Do NOT assert on `skipped`: the shipped manifest always contributes 106
# servers, so a CORRECTLY isolated run legitimately reports skipped=106 /
# policy_denied=106. An earlier draft aborted on "counts in the hundreds", which had it
# exactly backwards — it would have failed every correct run.
SUMMARY="$(grep -E 'Startup policy summary' "$WORK/gw.log" | tail -1)"
[ -n "$SUMMARY" ] || { echo "no startup summary in log; abort"; cat "$WORK/gw.log"; exit 1; }
echo "$SUMMARY"
EAGER="$(printf '%s' "$SUMMARY" | grep -oP 'eager=\K[0-9]+')"
LAZY="$(printf '%s' "$SUMMARY" | grep -oP 'lazy=\K[0-9]+')"
[ "$((EAGER + LAZY))" -eq 1 ] || {
  echo "FAIL: isolation did not take — eager+lazy=$((EAGER + LAZY)), expected exactly 1 (the fixture)."
  echo "      HOME redirection or --policy is not in effect; _kill_orphan_processes would see foreign configs."
  exit 1; }
echo "ISOLATION OK: eager=$EAGER lazy=$LAZY (fixture only)"

# (a) the process starts and listens
curl -sf "http://127.0.0.1:$PORT/health" | tee "$WORK/health.json"

# (b) a real downstream server serves a real tool call through it
PORT="$PORT" "$PWD/.venv/bin/python" - <<'PY'
import asyncio, os
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    url = f"http://127.0.0.1:{os.environ['PORT']}/mcp"
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert "gateway.invoke" in names, f"gateway.invoke missing: {sorted(names)}"
            res = await s.call_tool("gateway.invoke", {
                "tool_id": "p6clean_fixture::ping",
                "arguments": {"value": "p6clean"},
            })
            body = "".join(getattr(c, "text", "") for c in res.content)
            assert "p6clean-pong:p6clean" in body, f"downstream call failed: {body!r}"
            print("PRINCIPLE-1 OK:", body)

asyncio.run(main())
PY

# --- AFTER: the operator's gateway and its children must be untouched ---
curl -sf http://127.0.0.1:3344/health >/dev/null || { echo "FAIL: live gateway died"; exit 1; }
LIVE_KIDS_AFTER="$(pgrep -P "$LIVE_PID" | sort | tr '\n' ' ' || true)"
[ "$LIVE_KIDS_BEFORE" = "$LIVE_KIDS_AFTER" ] || {
  echo "FAIL: live gateway children changed"; echo "  before=[$LIVE_KIDS_BEFORE]"; echo "  after =[$LIVE_KIDS_AFTER]"; exit 1; }
echo "PRINCIPLE-3 OK: :3344 still listening, children unchanged"
```

Expected: the startup summary reports `eager + lazy == 1` — the fixture and nothing else.
`skipped` and `policy_denied` will both read ~106 on a **correct** run, because the
shipped manifest always contributes that many and the fixture-only policy denies them
all; those numbers are evidence that the policy worked, not that isolation failed. Then
`list_tools` includes `gateway.invoke`; the call returns
`p6clean-pong:p6clean`; `:3344` still answers and `LIVE_KIDS` is byte-identical
before and after. `cleanup` runs on every exit path.

**`/health` is not functional proof.** `transport/http.py:436` returns `"ok": True` as a
hardcoded literal, so the probe shows only that the HTTP server answers. The real
downstream `gateway.invoke` returning `p6clean-pong:` is the proof for principle 1(b);
`/health` covers only 1(a) "starts and listens." Do not let one stand in for the other.

If `gateway.invoke`'s `tool_id` separator or argument shape differs at execution time,
resolve it against the live gateway's `gateway.describe` output rather than editing the
assertion to something weaker — a passing call that does not reach the downstream
process satisfies nothing.

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `tests/test_manifest.py`, `CHANGELOG.md`, `.claude/docs-catalog.json`
- evidence paths: `plans/phase-plan-v11-P6CLEAN.md`
- redaction posture: `metadata_only`
- downstream handling: `none`
