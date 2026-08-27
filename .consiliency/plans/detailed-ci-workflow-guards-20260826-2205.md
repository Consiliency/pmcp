# Detailed plan: run #187's workflow guards in CI

> **Revision 5 (2026-08-27).** Rev 4 boarded 2 DISAGREE / 1 AGREE. Three real
> defects: the `continue-on-error` invariant was job-level only while the same
> mutant works at **step** level; deleted-file drift **crashes** instead of
> reporting, killing the one guard #187 calls the sole defence against silent job
> deletion; and the mutation-helper contract was internally impossible again.
>
> **Revision 4 (2026-08-27).** Rev 3 boarded 2 AGREE / 1 DISAGREE. The DISAGREE
> found a real new mutant — workflow-level `permissions: write-all` bypasses the
> job-level check entirely — plus four contract gaps. All five folded in below.
>
> **Revision 3 (2026-08-27).** Rev 1 was boarded (2 DISAGREE, 2 PARTIALLY AGREE)
> and rev 2 was boarded again (2 DISAGREE, 1 AGREE, 1 seat unavailable). Rev 2's
> central defect was that its *repair* of rev 1 created a fresh
> internally-impossible contract. Errors from both are kept inline as `WAS WRONG`
> rather than deleted — most rules here exist only because of them.
>
> One seat finding is **rejected**: `actions/checkout@v7` was reported as
> nonexistent. The repo uses v7 in every job and all 11 checks passed on it. A
> second is **partly rejected**: `.github/CODEOWNERS` was reported absent; it
> exists. Its substantive point — that CODEOWNERS gates nothing without a review
> requirement — is correct and is now recorded accurately.

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
  - **no `continue-on-error` and no `if:` on `publish` or `github-release`, at
    JOB level *and on every STEP within them*.** The job-level check alone is
    bypassed by the identical mutant one level down: `continue-on-error: true`
    on the "Publish to PyPI" step makes a tag push report **green while the
    upload failed** — the nastiest mutant in the set, still open in rev 4. The
    invariant must walk `jobs.<id>.steps[]`.
  - accept both `needs: X` and `needs: [X]`. The real file uses the scalar form;
    rejecting the list form is a false positive on a legitimate edit.
  - **the job set is exactly `{build, publish, github-release}`** — an added job
    (an `exfil` step with the release environment in scope) is a real mutant and
    is statically decidable, so it belongs here rather than in the label-ack
    remainder. Note this is stricter than `job_set_drift`, which allows additions
    generally; `release.yml` earns the stricter rule because it is the file with
    no PR-time feedback.
  - **`permissions:` equals the committed map exactly at BOTH levels** —
    job-level (`publish: {id-token: write}`, `github-release: {contents: write}`,
    `build` unset) **and workflow-level (absent)**. Checking only the job level
    is a concrete bypass: workflow-level `permissions: write-all` leaves every
    job map untouched while widening `build`, because a workflow-level block
    applies to any job that does not override it. Found by the red-team seat on
    rev 3; add it as a committed mutant in the matrix.
  - **every `uses:` equals the committed reference exactly.** This pins
    `pypa/gh-action-pypi-publish@release/v1` against being repointed to a fork.
    It does not make `@release/v1` safe — that is a mutable tag, called out under
    Non-goals — but it does make a *change* to it fail.

  **WAS WRONG (rev 2):** `TestReleaseInvariants` and AC1 required the checker to
  fail on a forked action, widened permissions, and an added job, while
  `release_invariants()` checked none of them and `job_set_drift` explicitly
  passes additions. Two seats independently flagged the contract as
  unsatisfiable: an implementer following Changes fails AC1, one following AC1
  invents invariants the plan never specified, and the cheap way out is deleting
  the tests. This is the same class of internally-impossible contract rev 1 had,
  reintroduced by rev 1's own repair.
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

  **A DELETED file is the case rev 4 got wrong.** `maintenance.yml` deleted is
  not on disk, so reading the head version raises and the checker exits with a
  traceback — and AC2 explicitly says a crash does not count, because the reason
  must name the removed jobs. The release/timeout invariants cannot cover for it:
  they only see surviving files. So the mutant #187 calls *the sole guard against
  silent job deletion* would ship dead while AC1's `release.yml` cases still went
  red. Specify: a changed path whose **head blob is absent** has an empty job
  set, and the failure reason names every job removed from that path. And
  `paths` must come from `git diff` **including deletions** (`--diff-filter=ACMRD`),
  not a glob of the worktree — a glob cannot see a file that is gone.
- `main()` — add — `--base-ref`. Invariants always run. The base is resolved
  **per event**, and the table is part of the spec because getting it wrong
  silently disables drift on the path that gates merges:

  | event | base |
  |---|---|
  | `pull_request` | `github.event.pull_request.base.sha` |
  | `push` | `github.event.before` |
  | `schedule` / `workflow_dispatch` | none — invariants only, drift skipped, and **said so in the log** |

  **WAS WRONG (rev 2):** it specified only `github.event.before`, which is a
  PushEvent field and is unset on `pull_request`. Following the only wiring given,
  every PR would have run with an empty base and skipped drift entirely —
  recreating rev 1's own `WAS WRONG` on the merge path. The other naive fill-in
  is equally broken: `--base-ref origin/main` on a push *to* main compares the
  just-pushed tip against itself.

  An empty `--base-ref` parses as `None`, never consuming the next argument.

- **Drift fails closed on an unresolvable base.** Distinguish *"the base commit
  resolves and the path is absent"* (a genuinely new workflow file — pass) from
  *"the base commit does not resolve"* (a shallow clone, a bad ref, a fetch
  failure — **fail**). A naive implementation treats both as "new file" and
  silently disables all drift protection.
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
  2. install `actionlint` **v1.7.12**, downloading
     `actionlint_1.7.12_linux_amd64.tar.gz` from the GitHub release and verifying
     it against the digest recorded in `.consiliency/evidence/mutation-189.md`
     (the implementer records the digest there when pinning; an acceptance
     criterion asserts the installed binary matches). Rev 1 said "pinned,
     checksum-verified" and rev 3 still named neither version nor digest in the
     Changes section — the red-team seat correctly called that a repeat of the
     defect it claims to fix. Then run it bare — **no glob**: `*.yml` leaves a
     `.yaml` workflow unchecked, and it exits 3 outside a git root.
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
- `.github/CODEOWNERS` **already exists** and already covers these paths via
  `*  @ViperJuice`. One seat reported it absent; that is wrong. But its own
  comment is accurate — *"Advisory, not a merge gate"* — and `gh api` confirms
  `required_pull_request_reviews` is **null**, so it gates nothing today.
  Making it load-bearing needs `require_code_owner_reviews: true` **and** a
  review requirement enabled in branch protection. That is a settings change on
  the operator's repository, so it is surfaced for a decision rather than folded
  into a merge.
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

### `scripts/_mutate_workflow.sh` (create)

The verification loop and the evidence matrix both invoke this; rev 3 referenced
it without creating it, so the specified evidence could not be produced as
written. It takes a mutant name, applies that edit to the real workflow tree, and exits
non-zero on an unknown name. **Its set is the explicit UNION of every mutation
case any criterion requires** — `TestReleaseInvariants` + `TestTimeoutInvariants`
+ the drift cases (`maintenance-deleted`, `changelog-job-deleted`) + the step-level
and both-level-permissions cases. **WAS WRONG (rev 4):** it said "exactly the set
named in TestReleaseInvariants + TestTimeoutInvariants" while the mandatory loop
invoked it for two drift-only mutants — accepting them violated the stated set,
rejecting them made AC2's commits impossible. That is the *fourth* consecutive
revision to ship an internally impossible mutation contract; the union rule
exists to end it.

The verification loop and this list are **the same list**, generated from one
source. Rev 4's loop omitted the four mutants rev 3 and rev 4 had just added
(forked action, widened permissions at each level, added job), so the loop would
have certified a checker that never saw them.

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
- [ ] **Drift fails closed on an unresolvable base ref** — a run against a
      nonexistent base commit exits non-zero, while a run whose base resolves but
      whose workflow path is genuinely new exits zero. A single implementation
      that treats both as "new file" silently disables all drift protection and
      must not pass.
- [ ] **The base ref is correct on `pull_request`** — asserted against the
      wiring in `test.yml`, not just the script. Rev 2's wiring named only
      `github.event.before`, which is unset on `pull_request`, so every PR would
      have skipped drift while the script itself remained correct. A script-level
      test cannot catch that; assert the workflow expression.
- [ ] The `test.yml` step invoking the checker is asserted to contain no `|| true`
      and no `continue-on-error` — the fail-open pattern
      `bypass-proofs-187.md` records as having already happened once here.
- [ ] `TestOnKeyParsing` fails if the `on`/`True` handling is removed — verified
      by deleting it and observing red.
- [ ] `release-diff-ack` is proven in **all three** directions, not just the
      negative one: a PR that does not touch `release.yml` passes; a PR that
      touches it **without** the label fails; and a PR that touches it **with**
      the label passes, including the label-applied-then-rerun path. Rev 3 had
      only the negative case, which an implementation that always fails on any
      `release.yml` change satisfies.
- [ ] The job reads **live** labels via the API, not
      `github.event.pull_request.labels`. That context is frozen at trigger time
      and a re-run replays the frozen payload, so a label applied to an already
      failed PR would never be seen — the exact trap the existing `changelog`
      job documents at `test.yml:220-228`.
- [ ] The downloaded `actionlint_1.7.12_linux_amd64.tar.gz` matches the recorded
      **archive** digest, asserted before extraction. **WAS WRONG (rev 4):** the
      Changes section verified the archive while the criterion asserted the
      installed *executable* against "that same recorded digest" — they are
      different byte streams, and a seat confirmed the real values differ
      (archive `8aca8d…` vs installed binary `c872d6…`), so the criterion could
      never pass. A separate `actionlint --version` assertion covers the
      installed artifact.

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
