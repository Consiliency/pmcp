# Detailed plan: run #187's workflow guards in CI

## Task

Close Consiliency/pmcp#189. `release.yml` triggers only on tag push, so it never
appears in PR checks. Every guard that protected #188's change to it —
diff-shape audit, timeout validation, job-set comparison, `actionlint` — was run
by hand, once, against that one diff. The next PR touching it inherits none of
them, on the one file where a mistake is invisible until a release breaks.

## Research summary

`.github/workflows/` holds five files: `test.yml`, `release.yml`, `docker.yml`,
`maintenance.yml`, `pipeline-bootstrap.yml`. Only `test.yml` runs on
`pull_request` (`test.yml:3-13`, `branches: [main]`), so it is the only place a
new guard job can live and still gate a PR.

`test.yml` already contains the exact shape this work needs. The `changelog` job
(`test.yml:200-`) is gated `if: github.event_name == 'pull_request'`, declares an
explicit job-level `permissions:` block (with the load-bearing comment that a
job-level block *replaces* the repo default for every scope, so `contents: read`
must be repeated), and uses `fetch-depth: 0` because `actions/checkout` defaults
to a depth-1 clone that does not contain the PR base ref a three-dot diff needs.
The new job copies all three of those decisions rather than rediscovering them.

`actionlint` is present on this host at `/usr/local/bin/actionlint`, so the
mutation matrix below can be produced locally before CI ever runs it.

#189 records seven mutants that pass every naive check. The important one is the
tag-pattern corruption `"v*"` → `"V*"`: the workflow becomes valid, no run is
triggered at tag push, and there is therefore never a red X. A version sits
tagged-but-unshipped indefinitely. That mutant is invisible to `actionlint`, to a
timeout check, and to a job-set comparison — all three see a perfectly good file.

**The design consequence:** the issue proposes a *diff-shape* assertion for this
class, but a diff test only fires when the line is touched in a way the pattern
notices, and it goes stale the moment the file is legitimately reformatted. A
**structural invariant check on the file's parsed content** is strictly stronger:
it asserts what `release.yml` must always be true of, independent of how a diff
reached that state, and it fails identically whether the mutation arrived by
edit, by merge, or by the file being deleted outright.

## Changes

### `scripts/check_workflows.py` (create)

- `release_invariants(doc)` — add — assert on the *parsed* `release.yml`:
  - triggers on `push` with a `tags` filter containing exactly `v*` (catches
    `V*`, catches a deleted `tags:` filter, catches a deleted `on: push`);
  - `publish` declares `needs: build` and an `environment:` (catches both
    silent-publish mutants);
  - `github-release` declares `needs: publish` (catches the regate-on-build
    mutant, which would publish a release for a failed upload);
  - every job declares `timeout-minutes`, and each is `>= 5` (a floor that
    catches a fat-fingered `1`; the true p100 is not knowable statically and the
    plan does not pretend otherwise — see Non-goals).
  - the file existing at all is the first assertion, which is what catches
    deletion.
- `job_set_drift(paths, base_ref)` — add — for every changed file under
  `.github/workflows/`, parse the base-ref version via `git show` and the head
  version from disk, and fail on any job name present in base and absent in head.
  This is the only guard against silent job deletion: #189 verified that
  deleting a terminal job outright leaves a file both `actionlint` and a
  presence check accept.
- `main()` — add — `--base-ref` argument; runs invariants unconditionally and
  drift only for changed workflow files; prints one line per finding and exits
  non-zero on any.
- Parse with `yaml.safe_load`. **Read the `on:` key as both `"on"` and `True`** —
  YAML 1.1 parses a bare `on` as the boolean `True`, so a naive `doc["on"]`
  raises `KeyError` on every real workflow and an invariant written that way
  would pass vacuously if wrapped in a `try`. This is the single most likely way
  to write a guard that guards nothing.

### `.github/workflows/test.yml` (modify)

- `workflows` job — add — `runs-on: ubuntu-latest`, `timeout-minutes: 10`,
  explicit `permissions: {contents: read}`, `actions/checkout@v7` with
  `fetch-depth: 0`. Three steps:
  1. install `actionlint` (pinned release download, checksum-verified — an
     unpinned installer on the path that validates the release pipeline is its
     own supply-chain hole);
  2. run bare `actionlint` with **no glob** — #189 verified `*.yml` silently
     leaves a `.yaml` workflow unchecked — from the repo root, since it exits 3
     outside a git project root;
  3. run `scripts/check_workflows.py --base-ref <merge base>`.
- Not gated on `pull_request` only: unlike `changelog`, the invariant half is
  meaningful on `push: [main]` too, and a direct-to-main workflow edit is exactly
  the case with no PR to check. Drift-checking is skipped when there is no base
  ref; invariants still run.

### `tests/test_workflow_guards.py` (create)

- `TestReleaseInvariants` — add — one test per mutant from #189's matrix. Each
  loads the real `release.yml`, applies the mutation **in memory**, and asserts
  `release_invariants` reports it. A mutant that the check misses fails the test.
- `TestJobSetDrift` — add — synthetic base/head pairs: job removed (fail), job
  added (pass), job renamed (fail — a rename is a removal plus an addition and
  must not be waved through), file unchanged (pass).
- `TestOnKeyParsing` — add — assert the `on`/`True` handling directly, so the
  YAML 1.1 trap above can never regress into a vacuous check.

### `.consiliency/evidence/mutation-189.md` (create)

- Mutation matrix — add — for all seven mutants in #189: the exact edit, the
  command run, and the before/after exit codes. Records which are **caught**,
  and states plainly which is **not** (see Non-goals) rather than implying full
  coverage.

## Documentation impact

- `CHANGELOG.md` — add — a `### Added` entry under `[Unreleased]`: workflow
  guards now run in CI; names what is checked and what is not.
- `CONTRIBUTING.md` — modify **only if** it documents the CI job list; check
  before editing. If it does not enumerate jobs, no change.

## Dependencies & order

1. `scripts/check_workflows.py` first — the tests and the CI job both call it.
2. `tests/test_workflow_guards.py` second, before wiring CI: the mutation tests
   are what prove the script is worth wiring.
3. `test.yml` job third.
4. Evidence file last, recording results from the finished script.

No external dependency beyond `actionlint` (downloaded in-job) and `pyyaml`,
which the project already depends on.

## Verification

```bash
# The guard catches every mutant it claims to
uv run pytest -q tests/test_workflow_guards.py -v

# The guard passes on the real, correct tree (no false positive)
uv run python scripts/check_workflows.py --base-ref origin/main

# Each #189 mutant, applied for real, then reverted
for m in tag-case tags-deleted env-dropped needs-publish-to-build \
         needs-build-dropped timeout-1 job-deleted; do
  ./scripts/_mutate_workflow.sh "$m"          # scratch helper, not committed
  uv run python scripts/check_workflows.py --base-ref origin/main; echo "$m -> $?"
  git checkout -- .github/workflows/
done

# actionlint agrees the tree is clean, and is genuinely reached
actionlint && echo "actionlint clean"

uv run ruff check . && uv run ruff format --check . && uv run mypy src/
```

Edge cases: a workflow with no `jobs:` key; a workflow that is invalid YAML
(must report, not traceback); `--base-ref` pointing at a ref that does not exist;
a PR that *adds* a new workflow file (no base version to compare — must pass, not
crash); `release.yml` deleted entirely.

## Acceptance criteria

- [ ] Each of the seven mutants in #189's table is applied to the real
      `.github/workflows/` tree and `scripts/check_workflows.py` exits non-zero
      for six of them, with the exit codes recorded in
      `.consiliency/evidence/mutation-189.md`. A mutant that is only asserted
      in-memory does not count — the check must fail against a mutated file on
      disk.
- [ ] `scripts/check_workflows.py --base-ref origin/main` exits 0 on the
      unmutated tree, proving the guard is not a blanket failure.
- [ ] `TestOnKeyParsing` fails if the `on`/`True` YAML 1.1 handling is removed —
      verified by deleting that handling and observing red, not by reading it.
- [ ] Deleting `.github/workflows/release.yml` outright causes a non-zero exit,
      proving the "a file nobody notices is gone" case is covered.

## Non-goals

- **Detecting a timeout set below a job's real p100.** The seventh mutant in
  #189's table is not statically decidable — the p100 depends on runner load and
  dependency resolution time. The check enforces presence and a `>= 5` floor and
  the evidence file states this mutant as **not covered**. Claiming otherwise
  would be exactly the vacuous-guard failure this issue exists to fix.
- Replacing `actionlint` with a hand-rolled schema check.
- Extending the invariants to `docker.yml` / `maintenance.yml` beyond the shared
  timeout and job-set-drift checks; only `release.yml` has the invisible-failure
  property that motivates the specific invariants.

## Execution Policy

- execute: effort=medium, reason=self-contained script plus CI wiring; the
  subtlety is concentrated in the mutation matrix rather than the code
