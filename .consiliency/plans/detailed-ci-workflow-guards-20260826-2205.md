# Detailed plan: run #187's workflow guards in CI

> **Revision 2 (2026-08-26, post-board).** Four seats reviewed revision 1: two
> DISAGREE, two PARTIALLY AGREE. Revision 1's errors are recorded inline as
> `WAS WRONG` notes rather than deleted — several rules below exist only because
> of them.

## Task

Close Consiliency/pmcp#189. `release.yml` triggers only on tag push, so it never
appears in PR checks. Every guard that protected #188's change to it was run by
hand, once. The next PR touching it inherits none of them, on the one file where
a mistake is invisible until a release breaks.

## Research summary

`.github/workflows/` holds five files. Only `test.yml` runs on `pull_request`
(`test.yml:3-13`), so a PR-gating job must live there.

`test.yml` already encodes the decisions this work needs: the `changelog` job
(`test.yml:200-`) is gated `if: github.event_name == 'pull_request'`, declares an
explicit job-level `permissions:` block (a job-level block *replaces* the repo
default for every scope, so `contents: read` must be repeated), and uses
`fetch-depth: 0` because `actions/checkout` defaults to a depth-1 clone.

`yaml.safe_load` on the real `release.yml` yields keys `['name', True, 'jobs']` —
verified. `"on" in doc` is **False**; `True in doc` is **True**. A guard written
as `doc["on"]` inside a `try` passes vacuously.

`actionlint` (1.7.12, present at `/usr/local/bin/actionlint`) caught **0 of 7**
of #189's mutants and 0 of 7 additional ones — verified by a seat that ran them.
It catches the syntax class only (over-indentation → `unexpected key "publish"`).
Its value is schema validation; the plan frames it that way and claims no more.

Current `release.yml` shape: `needs: build` (scalar, not a list), `environment:
release`, `github-release` `needs: publish`, and the publish step on
`pypa/gh-action-pypi-publish@release/v1` — a **mutable tag, not a SHA pin**.
Verified via `gh api`: the `release` environment has `"protection_rules": []` and
`deployment_branch_policy: null`, and branch protection's
`required_status_checks.contexts` is `[test (3.10), test (3.11), test (3.12),
install-smoke, min-version-smoke, lint, typecheck, changelog]`.

**WAS WRONG (rev 1):** "structural invariants are *strictly stronger* than the
issue's diff-shape assertion." Disproven. A seat applied six mutants that pass
both the invariants and actionlint — `continue-on-error: true` on publish (tag
push reports **green** while the PyPI upload failed), an `if:` on publish, the
publish action repointed to a fork, widened `permissions:`, workflow-level
`concurrency` with `cancel-in-progress`, and an added `exfil` job. Every one is a
changed line a diff-shape check flags. The approaches are **incomparable**:
invariants catch bad states however reached; diff-shape catches changes the
allowlist never anticipated. Do both.

## Changes

### `scripts/check_workflows.py` (create)

- `release_invariants(doc)` — add — on the *parsed* `release.yml`:
  - the file exists (this is what catches outright deletion);
  - `push.tags` contains exactly `v*`;
  - `publish` has `needs: build` and **`environment == "release"` by name**, not
    merely present. `environment: dev` passes a presence check and ships;
    GitHub auto-creates unprotected environments. Record in the evidence file
    that this environment carries **no GitHub-side protection rules today**, so
    the invariant guards a name, not a gate.
  - `github-release` has `needs: publish`;
  - **no `continue-on-error` and no `if:` on `publish` or `github-release`** —
    the `continue-on-error` mutant is the nastiest of the eight: a tag push
    reports green while the upload failed.
  - accept both `needs: X` and `needs: [X]`. The real file uses the scalar form;
    rejecting the list form is a false positive on a legitimate edit.
- `timeout_invariants(doc, path)` — add — **applies to every workflow file, not
  just `release.yml`**, and uses #187's hardened predicate verbatim:
  `isinstance(v, int) and not isinstance(v, bool) and 10 <= v <= 30`, skipping
  jobs with `uses:` (a reusable-workflow call cannot carry `timeout-minutes`).
  **WAS WRONG (rev 1):** a floor of `>= 5` on `release.yml` only. Three seats
  caught it. `.consiliency/evidence/bypass-proofs-187.md:20-31` records
  `timeout-minutes: 360` as a closed bypass — it is valid, preserves the job set,
  and **recreates the six-hour default #187 exists to fix**. A floor with no
  ceiling reopens it. The floor was separately raised 1 → 10 at
  `bypass-proofs-187.md:175-179`; `>= 5` regressed both ends. Every delivered
  value across all five files is in 10..30 (verified: 30, 25, 10×5, 20, 10, 10,
  20, 10), so the predicate fits the tree as it stands.
- `job_set_drift(paths, base_ref)` — add — for each changed file under
  `.github/workflows/`, parse the base version via `git show` and the head
  version from disk; fail on any job name present in base and absent in head.
  A rename is a removal plus an addition and must fail. A newly added workflow
  file has no base version — pass, do not crash.
- `main()` — add — `--base-ref`. Invariants always run. **On a `push` event, use
  `github.event.before` as the base** rather than skipping drift.
  **WAS WRONG (rev 1):** drift was skipped with no base ref, so a direct-to-main
  push deleting `maintenance.yml` or one of its jobs passed everything. An empty
  `--base-ref` must parse as `None`, not consume the next argument.
- Read the `on:` key as **both `"on"` and `True`** (YAML 1.1). Under a `.get`
  idiom this fails closed rather than vacuously — but `TestOnKeyParsing` stays,
  because the `try`-wrapped variant does not.

### `.github/workflows/test.yml` (modify)

- `workflows` job — add — `timeout-minutes: 10`, explicit
  `permissions: {contents: read}`, `actions/checkout@v7` with `fetch-depth: 0`.
  (A seat flagged `checkout@v7` as nonexistent; **rejected** — the repo uses v7
  in every job and all 11 checks passed on it today. Stale model knowledge.)
  Steps:
  1. `astral-sh/setup-uv@v7` + `uv sync`. **WAS WRONG (rev 1):** the job was
     modelled on `changelog`, which runs no Python packages, and had no Python
     setup at all. `check_workflows.py` imports `yaml`, which is not on a stock
     `ubuntu-latest` image, so step 3 would raise `ModuleNotFoundError` — the
     guard dead on arrival while local `uv run` still passed and AC2 looked
     green. This is the single most likely way to ship a guard that guards
     nothing.
  2. install `actionlint` at an **explicitly named version with an explicitly
     named checksum** (rev 1 said "pinned, checksum-verified" but named neither,
     making the criterion unenforceable), then run it bare — **no glob**: `*.yml`
     leaves a `.yaml` workflow unchecked, and it exits 3 outside a git root.
  3. `uv run python scripts/check_workflows.py --base-ref …`.
- `release-diff-ack` job — add — any PR whose diff touches `.github/workflows/release.yml`
  fails unless the PR carries a `release-change-approved` label. This is the
  tripwire for the whole unguarded remainder (the six mutants above and the ones
  nobody has thought of), mirroring the existing `changelog`/`skip-changelog`
  pattern. It covers by *acknowledgement* what an allowlist cannot cover by
  enumeration.

### Repo settings — required, and not a file change

**WAS WRONG (rev 1):** the plan implied the drift check protects itself. It does
not. GitHub runs `pull_request` jobs from the PR branch's own workflow file, so a
PR that deletes the `workflows` job means the job never runs — the drift check
cannot fire because it *is* what was deleted. No red X, ever. Branch protection's
required contexts currently do not include it.

- Add `workflows` and `release-diff-ack` to
  `required_status_checks.contexts` via `gh api` after merge. A required context
  that never reports blocks the merge as "Expected".
- Add `CODEOWNERS` covering `.github/` and `scripts/check_workflows.py`.
- **State the residuals plainly** in the evidence file rather than implying
  closure: GitHub counts an `if:`-skipped job as *satisfying* a required check,
  so `if: false` on the guard merges green; and the PR can keep the job name
  while gutting its steps or editing `check_workflows.py` to `exit 0`, since the
  script runs from the PR branch. CODEOWNERS review is the only backstop for
  those, which makes it load-bearing, not optional.

### `tests/test_workflow_guards.py` (create)

- `TestReleaseInvariants` — add — one test per statically decidable mutant,
  including the eight-mutant set above (`continue-on-error`, `if:` on publish,
  `environment: dev`, forked action, widened permissions, added job).
- `TestTimeoutInvariants` — add — `360`, `"not a number"`, `True`, `1`, and a
  legitimate `10`/`20`/`25`/`30`, across all five workflow files.
- `TestJobSetDrift` — add — job removed (fail), added (pass), renamed (fail),
  file unchanged (pass), new file with no base (pass, no crash).
- `TestOnKeyParsing` — add.

### `.consiliency/evidence/mutation-189.md` (create)

Mutation matrix: exact edit, command, before/after exit codes, for every mutant —
**including the ones not covered**, named individually.

## Documentation impact

- `CHANGELOG.md` — add — `### Added`: workflow guards now run in CI; names what
  is checked, what is acknowledged by label, and what is not covered.
- `CONTRIBUTING.md` — verified it does not enumerate CI jobs. No change.

## Dependencies & order

1. `scripts/check_workflows.py`.
2. `tests/test_workflow_guards.py` — the mutation tests are what prove the script
   is worth wiring.
3. `test.yml` jobs.
4. Branch-protection contexts + CODEOWNERS (post-merge, `gh api`).
5. Evidence file, recording real results.

## Verification

```bash
uv run pytest -q tests/test_workflow_guards.py -v
uv run python scripts/check_workflows.py --base-ref origin/main   # must exit 0

# Mutants must be COMMITTED on a scratch branch, not left in the working tree.
# WAS WRONG (rev 1): the loop mutated the working tree while drift uses a
# three-dot diff that only sees committed changes, so drift never ran at all --
# every catch came from the invariants reading disk, and `job-deleted` passed via
# "publish job missing", masking drift's total silence.
git switch -c scratch-mutants
for m in tag-case tags-deleted env-dropped env-renamed needs-publish-to-build \
         needs-build-dropped timeout-360 timeout-1 continue-on-error if-on-publish \
         job-deleted file-deleted maintenance-deleted changelog-job-deleted; do
  ./scripts/_mutate_workflow.sh "$m" && git commit -qam "mutant: $m"
  uv run python scripts/check_workflows.py --base-ref main; echo "$m -> $?"
  git reset -q --soft HEAD~1 && git stash -q
done

actionlint && echo "actionlint clean"
uv run ruff check . && uv run ruff format --check . && uv run mypy src/
```

Edge cases: a workflow with no `jobs:`; invalid YAML (report, do not traceback);
a `--base-ref` that does not exist; a PR adding a new workflow file;
`release.yml` deleted entirely.

## Acceptance criteria

- [ ] Every statically decidable mutant listed in `TestReleaseInvariants` and
      `TestTimeoutInvariants` is applied **as a commit on a scratch branch** and
      `check_workflows.py` exits non-zero, with real exit codes recorded in
      `.consiliency/evidence/mutation-189.md`. Includes `timeout-360`, which
      revision 1's floor let through.
- [ ] **Drift is proven live, independently of the invariants:** `maintenance.yml`
      deleted, and the `changelog` job deleted from `test.yml`, both committed,
      each exit non-zero **via `job_set_drift`** — asserted by the reported
      reason, not just the exit code. **WAS WRONG (rev 1):** a seat verified that
      replacing `job_set_drift` with `return []` passed all four original
      criteria, because the invariants alone caught every case criterion 1 named.
      Drift — which #187's own evidence calls the sole guard against silent job
      deletion — could have shipped dead with the suite green.
- [ ] `check_workflows.py --base-ref origin/main` exits 0 on the unmutated tree,
      proving the guard is not a blanket failure.
- [ ] The `test.yml` step invoking the checker is asserted to contain no `|| true`
      and no `continue-on-error` — the fail-open pattern
      `bypass-proofs-187.md` records as having already happened once here.
- [ ] `TestOnKeyParsing` fails if the `on`/`True` handling is removed — verified
      by deleting it and observing red.
- [ ] A PR touching `release.yml` without the `release-change-approved` label
      fails `release-diff-ack`.

## Non-goals

- **A timeout below a job's real p100** (e.g. `build` 20 → 12, above the floor
  but below a future p100). Not statically decidable; named here as the single
  uncovered mutant. **WAS WRONG (rev 1):** the uncovered case was implied to be
  `timeout-1`, which produced an internally impossible contract — the loop ran
  seven mutants all of which the checker catches, while acceptance demanded six
  and the non-goals claimed a seventh was uncovered. Two seats flagged the
  contradiction, and both warned the dangerous resolution is *weakening the
  floor* so one mutant stays green and the evidence can say "six of seven".
- Environment protection rules configured in GitHub settings — invisible to any
  file check. Recorded in the evidence file as unguarded.
- SHA-pinning `pypa/gh-action-pypi-publish` (currently `@release/v1`). Worth
  doing; out of scope here, and it needs its own issue.

## Execution Policy

- execute: effort=medium, reason=self-contained script plus CI wiring; the
  subtlety is concentrated in the mutation matrix rather than the code
