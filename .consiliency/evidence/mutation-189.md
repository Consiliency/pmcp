# Mutation evidence — Consiliency/pmcp#189

`.github/workflows/release.yml` triggers **only** on tag push, so it never
appears in a PR check: the tag push *is* the publish (build → PyPI trusted
publishing → GitHub release from a CHANGELOG awk extraction). A mistake in it is
invisible until a release breaks, and the worst mutants are silent — changing
the tag filter `"v*"` to `"V*"` leaves a workflow that is still valid, still
passes actionlint, and simply never runs. There is no red X anywhere; a version
just sits tagged-but-unshipped.

Protocol: mutants are **committed on a scratch branch**, never left in the
working tree — `job_set_drift` uses a three-dot diff, which only ever sees
committed changes, so a working-tree mutation makes drift a silent no-op while
the invariants quietly cover for it. `PYTHONDONTWRITEBYTECODE=1` throughout and
`__pycache__` purged before the test runs, per `mutation-180.md`.

Reproduce:

```bash
git switch -c scratch-mutants
BASE=$(git rev-parse HEAD~0)          # the pre-mutant commit
./scripts/_mutate_workflow.sh --list | while IFS='|' read -r name expected tags desc; do
  ./scripts/_mutate_workflow.sh "$name" && git commit -qam "mutant: $name"
  uv run python scripts/check_workflows.py --base-ref "$BASE"; echo "$name -> $?"
  git switch -q -C scratch-mutants "$BASE"      # not `reset --hard`, not `stash`
done
```

`scripts/_mutate_workflow.sh --list` is the **single source** of the mutant set —
the loop above consumes it verbatim, `tests/test_workflow_guards.py::TestMutationHelperContract`
asserts the tests exercise nothing that is not in it, and this document is a
transcript of that loop rather than a hand-written list. Four consecutive plan
revisions shipped a mutant list that disagreed with the loop meant to execute
it, so the loop would have certified a checker that never saw half the mutants.
Every edit also asserts its anchor matches **exactly once**, so an anchor that
drifts out of the file fails loudly instead of applying nothing and reporting a
comfortable green.

## Matrix

`checker` is `scripts/check_workflows.py --base-ref <pre-mutant commit>`.
`actionlint` is 1.7.12, the only static gate this repo had **before** this
change — it is the honest "before" column. `pytest` is
`tests/test_workflow_guards.py`, which runs in the existing `test` job.
`reason` is the tag on the failure the checker actually reported, so a mutant
killed by the wrong check is a loop failure rather than a pass.

| mutant | checker | actionlint | pytest | reason |
|---|---|---|---|---|
| *(none: unmutated tree)* | **0** | 0 | 0 | — |
| `tag-case` | **1** | 0 | 1 | `[release]` |
| `tags-deleted` | **1** | 0 | 1 | `[release]` |
| `env-dropped` | **1** | 0 | 1 | `[release]` |
| `env-renamed` | **1** | 0 | 1 | `[release]` |
| `needs-build-dropped` | **1** | 0 | 1 | `[release]` |
| `needs-publish-to-build` | **1** | 0 | 1 | `[release]` |
| `continue-on-error-job` | **1** | 0 | 1 | `[release]` |
| `continue-on-error-step` | **1** | 0 | 1 | `[release]` |
| `if-on-publish-job` | **1** | 0 | 1 | `[release]` |
| `if-on-publish-step` | **1** | 0 | 1 | `[release]` |
| `forked-action` | **1** | 0 | 1 | `[release]` |
| `permissions-job-widened` | **1** | 0 | 1 | `[release]` |
| `permissions-workflow-level` | **1** | 0 | 1 | `[release]` |
| `job-added` | **1** | 0 | 1 | `[release]` |
| `job-deleted` | **1** | 1 | 1 | `[release]` + `[drift]` |
| `file-deleted` | **1** | 0 | 2 | `[release]` + `[drift]` |
| `timeout-360` | **1** | 0 | 1 | `[timeout]` |
| `timeout-1` | **1** | 0 | 1 | `[timeout]` |
| `timeout-string` | **1** | 1 | 1 | `[timeout]` |
| `timeout-bool` | **1** | 1 | 1 | `[timeout]` |
| `timeout-deleted` | **1** | 0 | 1 | `[timeout]` |
| `maintenance-deleted` | **1** | 0 | **0** | `[drift]` only |
| `changelog-job-deleted` | **1** | 0 | **0** | `[drift]` only |
| `workflows-job-deleted` | **1** | 0 | 1 | `[drift]` only |

25 of 25 killed, each by the check that is supposed to kill it. The exact edits
are in `scripts/_mutate_workflow.sh`; `--list` prints a one-line description of
each.

### NOT COVERED — expected green, named individually

These are in the same helper and the same loop, and they exit **0**. They are
listed so this file cannot imply coverage that does not exist.

| mutant | checker | actionlint | pytest | why |
|---|---|---|---|---|
| `needs-as-list` | 0 | 0 | 0 | not a defect: `needs: [build]` is a legitimate equivalent of the committed `needs: build`. Rejecting it would be a false positive on a real edit, so the green is the assertion. |
| `timeout-below-p100` | 0 | 0 | 0 | **genuinely uncovered.** `build` 20 → 12 is above the 10-minute floor but could be below a future p100. Not statically decidable; this is the single uncovered mutant the plan's non-goals name. |
| `concurrency-added` | 0 | 0 | 0 | **uncovered by any static check.** A workflow-level `concurrency` with `cancel-in-progress` on `release.yml` can cancel an in-flight release. Covered only by `release-diff-ack`'s label, i.e. by a human looking at the diff. |
| `guard-self-disabled` | 0 | 1 | 1 | `if: ${{ false }}` on the guard's own job. actionlint's `if-cond` rule catches the *literal*; the checker does not. |
| `guard-self-disabled-nonconstant` | 0 | **0** | 1 | the honest version of the above: `if: ${{ github.actor == 'nobody-at-all' }}` is always false and actionlint says nothing. Caught only by the `test` job's assertion that the guard job carries no `if:`. |
| `guard-step-gutted` | 0 | 0 | 1 | the checker step's command replaced by `echo`. `check_workflows.py` runs *from the PR branch*, so it cannot see this; only the `test` job's assertion catches it. |

Two things the table cannot show, recorded here instead:

- **Environment protection rules.** `gh api repos/Consiliency/pmcp/environments/release`
  returns `{"protection_rules": [], "deployment_branch_policy": null}` **today**.
  The `environment: release` invariant therefore guards a **name, not a gate** —
  it stops the job being repointed at an auto-created `dev` environment, and
  that is all. Removing the (currently empty) protection rules in GitHub
  settings is invisible to every check here.
- **`pypa/gh-action-pypi-publish@release/v1` is a mutable tag, not a SHA pin.**
  The `uses:` invariant makes a *change* to that reference fail; it does not
  make the reference safe. SHA-pinning is out of scope for this change and
  needs its own issue.

### actionlint: the "before" column, read honestly

actionlint 1.7.12 killed **4 of 30** mutants, and the four are exactly the
schema class:

```
job-deleted        .github/workflows/release.yml:38:3: job "github-release" needs job "publish"
                   which does not exist in this workflow [job-needs]
timeout-string     .github/workflows/release.yml:11:22: expecting a single ${{...}} expression or
                   float number literal, but found plain text node [syntax-check]
timeout-bool       .github/workflows/release.yml:11:22: expected scalar node for float value but
                   found scalar node with "!!bool" tag [syntax-check]
guard-self-disabled .github/workflows/test.yml:305:9: constant expression "false" in condition.
                   remove the if: section [if-cond]
```

It caught **none** of the mutants that leave the workflow both valid and silent
— `tag-case`, `continue-on-error-step`, `env-renamed`, `forked-action`,
`permissions-workflow-level`, every drift case. It is kept in the job for
schema validation and is claimed as nothing more. Note also that `job-deleted`
is caught only *incidentally*, by the dangling `needs:` it leaves behind: delete
`github-release` instead and actionlint is silent again.

### actionlint pin — the recorded digest

The `workflows` job downloads a fixed version and asserts the **archive**
digest before extraction:

```
$ curl -sSL -o actionlint.tgz \
    https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_amd64.tar.gz
$ sha256sum actionlint.tgz
8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8  actionlint.tgz
$ tar xzf actionlint.tgz actionlint && sha256sum actionlint
c872d6db8c6bf83a8eaa704fc93999f027d55dffbc63b8a6abdccb47df5f4cd4  actionlint
$ ./actionlint --version | head -1
1.7.12
```

The archive and the extracted executable are **different byte streams**, so one
digest cannot assert both — an earlier plan revision required exactly that and
the criterion could never have passed. `ACTIONLINT_SHA256` in `test.yml` is the
archive digest (`8aca8d…`), checked with `sha256sum -c -` *before* `tar -xzf`;
the installed artifact is covered separately by
`test "$(actionlint --version | head -n1)" = "${ACTIONLINT_VERSION}"`.

## Drift is load-bearing, proven by removing it

The plan's AC2 exists because a review seat verified that replacing
`job_set_drift` with `return []` still passed every criterion — the invariants
alone caught each case the criteria named, so the one guard #187 calls the sole
defence against silent job deletion could have shipped dead with the suite
green. Re-run with the stub in place, mutants still committed:

```
drift-stubbed + maintenance-deleted   -> exit 0     <- WRONG, and nothing else sees it
drift-stubbed + changelog-job-deleted -> exit 0     <- WRONG
drift-stubbed + workflows-job-deleted -> exit 0     <- WRONG
drift-stubbed + job-deleted           -> exit 1     (release invariants still catch it)
drift-stubbed + file-deleted          -> exit 1     (release invariants still catch it)
```

With drift live, all five exit 1. The three that go green under the stub are the
proof: they are caught by drift **and by nothing else**, because the release and
timeout invariants only ever see files that survived.

The reported reasons, verbatim — the exit code alone would not distinguish which
check fired:

```
FAIL [drift] .github/workflows/maintenance.yml (deleted): job(s) removed since <base>:
     notify-worker — a rename counts as a removal; restore the job or state the removal in the PR
FAIL [drift] .github/workflows/test.yml (modified): job(s) removed since <base>:
     changelog — a rename counts as a removal; restore the job or state the removal in the PR
FAIL [drift] .github/workflows/release.yml (deleted): job(s) removed since <base>:
     build, github-release, publish — a rename counts as a removal; ...
```

A **deleted** file is the case an earlier plan revision got wrong: reading the
head blob unconditionally raises, and a traceback does not name the jobs that
were removed. The head blob of an absent path is treated as an empty job set,
and the paths come from `git diff --no-renames --diff-filter=ACMRD`, which
**includes deletions** — a glob of the worktree cannot see a file that is gone.

## Drift fails closed on an unresolvable base

A shallow clone, a bad ref or a failed fetch must not look like "every file is
new"; that would exit 0 with all drift protection silently disabled.

```
$ uv run python scripts/check_workflows.py --base-ref definitely-not-a-ref ; echo $?
FAIL [drift] base ref 'definitely-not-a-ref' does not resolve to a commit — refusing to
     pass with drift detection disabled (shallow clone? missing fetch-depth: 0?)
1
```

while a base that *does* resolve and simply has no such path is a genuinely new
workflow file and passes — the two are distinguished by
`git rev-parse --verify <base>^{commit}` before any path lookup
(`TestJobSetDrift::test_a_missing_base_and_a_new_file_are_distinguished`).

## The `on:` key is `True`, not `"on"`

YAML 1.1 reads a bare `on:` as the boolean `True`. `yaml.safe_load` on the real
`release.yml` yields keys `['name', True, 'jobs']`, so `"on" in doc` is **False**
and a guard written as `doc["on"]` inside a `try` passes vacuously — dead, and
green. Both spellings are handled, and the criterion is that removing either
branch goes red. Verified by deleting each:

```
# only the quoted "on" handled (the True branch deleted):
FAILED TestOnKeyParsing::test_get_on_reads_the_boolean_key
FAILED TestOnKeyParsing::test_the_real_document_passes_with_the_boolean_key
2 failed, 4 passed
... and scripts/check_workflows.py itself then exits 1 on the UNMUTATED tree,
    because the real file uses the bare form.

# only the boolean True handled (the "on" branch deleted):
FAILED TestOnKeyParsing::test_get_on_reads_the_string_key
FAILED TestOnKeyParsing::test_a_quoted_on_key_passes_too
2 failed, 4 passed
```

## `release-diff-ack`, all three directions

`check_workflows.py` is an allowlist: it catches bad states it knows how to
name. `release-diff-ack` is the tripwire for the remainder — the mutants above
that stay green, and the ones nobody has thought of.

This is a **local simulation, not a live CI run**: the shipped step body is
extracted from `.github/workflows/test.yml` with PyYAML (so it is the real
script, not a paraphrase), the `${{ github.repository }}` and PR-number
expressions are substituted textually, and `gh` is a stub. The one direction
that *is* live is A: this PR does not touch `release.yml`, and the job is
expected green in CI.

```
A. PR does NOT touch release.yml, no label
   release.yml is untouched — no acknowledgement required.                    EXIT=0
B. PR touches release.yml, live lookup returns no labels
   ::error::This PR changes .github/workflows/release.yml ...                 EXIT=1
C. PR touches release.yml, label present
   release-change-approved label present — acknowledged.                      EXIT=0
C2. label applied AFTER the trigger, then the job re-run
    (frozen payload empty; only the live lookup can see the label)
   Live label lookup succeeded. Current labels: release-change-approved       EXIT=0
D. live lookup FAILS, frozen payload carries the label
   Live label lookup FAILED — falling back to the event payload.             EXIT=0
E. live lookup SUCCEEDS returning zero labels (label removed after trigger)
   ::error::This PR changes .github/workflows/release.yml ...                 EXIT=1
```

B alone would be satisfied by an implementation that always fails on any
`release.yml` change, which is why A and C are asserted too. C2 is the reason
the job reads **live** labels: the event payload is frozen at trigger time and a
"Re-run jobs" replays that same frozen payload, so a label applied to an
already-failed PR would never be seen. E is the reason the fallback branches on
the lookup's **exit status** and never on emptiness — a successful lookup
returning zero labels is authoritative, and resurrecting a removed label from
the frozen payload would bypass the guard entirely.

## Live CI — the half that cannot be proven locally

Everything above ran on this machine. The single most likely way to ship a guard
that guards nothing is for it to die on the runner while `uv run` still passes
locally — a missing `yaml` on a stock `ubuntu-latest` image, a base ref that is
empty on a real `pull_request` event, a download that fails its digest check.
PR #198, run `33048605049`, `pull_request` on `fix/189-ci-workflow-guards`:

```
SUCCESS  build            SUCCESS  release-diff-ack
SUCCESS  changelog        SUCCESS  test (3.10)
SUCCESS  install-smoke    SUCCESS  test (3.11)
SUCCESS  lint             SUCCESS  test (3.12)
SUCCESS  min-version-smoke SUCCESS typecheck
SUCCESS  notify-worker    SUCCESS  workflows
```

`workflows` and `release-diff-ack` **appear as check names**. That is the
assertion, not just their colour: a job that silently never ran is simply
absent from this list and produces no red X anywhere — the same check-name-set
evidence #188 used.

From the `workflows` job log:

```
Install actionlint    actionlint.tar.gz: OK          <- archive digest, before extraction
Resolve the drift base  drift base for pull_request: '0edaaf47874cdf2f714098b95ad346df006bf815'
Check workflow ...      drift: base 0edaaf47874cdf2f714098b95ad346df006bf815,
                        changed workflow paths: ['.github/workflows/test.yml']
Check workflow ...      workflow guards: OK
```

`0edaaf47…` is exactly `gh pr view 198 --json baseRefOid`. Drift **ran** on a
real `pull_request` event against the real base and read a real changed path —
which a script-level test cannot establish, because the failure mode being ruled
out is in the workflow expression, not in the script.

From the `release-diff-ack` job log (direction A, live):

```
Changed files:
.consiliency/evidence/mutation-189.md
.consiliency/plans/detailed-ci-workflow-guards-20260826-2205.md
.github/workflows/test.yml
CHANGELOG.md
scripts/_mutate_workflow.sh
scripts/check_workflows.py
release.yml is untouched — no acknowledgement required.
```

Directions B, C, C2, D and E remain **local simulations** of the shipped step
body; this PR does not touch `release.yml`, so only A could be live.

One incidental finding worth recording: the first push of this branch produced
**no Actions run at all**, because the PR was in a conflicting state and GitHub
cannot build the `refs/pull/N/merge` ref for a conflicting PR. There is no red
X for that either — the checks simply do not exist. It is the same failure shape
this whole change is about, one level up, and it is another reason the two jobs
need to be *required* contexts: a required context that never reports blocks the
merge as "Expected", whereas an absent optional check blocks nothing.

## Residuals — what still has no automated backstop

Stated plainly rather than implied closed:

1. **GitHub counts an `if:`-skipped job as *satisfying* a required check.** A PR
   that adds `if: false` to the `workflows` job merges green as far as branch
   protection is concerned. `guard-self-disabled-nonconstant` above shows
   actionlint does not see the non-literal form. The `test` job's assertions
   catch it — unless the same PR disables that job too.
2. **The guard runs from the PR branch.** A PR can keep the job name while
   gutting its steps or editing `check_workflows.py` to `exit 0`
   (`guard-step-gutted`).
3. **`workflows` and `release-diff-ack` are not yet required contexts.** Branch
   protection's `required_status_checks.contexts` is today `["test (3.10)",
   "test (3.11)", "test (3.12)", "install-smoke", "min-version-smoke", "lint",
   "typecheck", "changelog"]`. Until the two are added by `gh api` **after**
   this merges, a PR that deletes the `workflows` job means the job never runs —
   drift cannot fire, because it *is* what was deleted, and there is no red X.
4. **CODEOWNERS is the only backstop for 1–3, and it gates nothing today.**
   `.github/CODEOWNERS` exists and covers these paths via `* @ViperJuice`, but
   `gh api` confirms `required_pull_request_reviews` is **null**, and the file's
   own comment says it is advisory. Making it load-bearing needs
   `require_code_owner_reviews: true` **and** a review requirement enabled —
   a settings change on the operator's repository, surfaced for a decision
   rather than folded into this merge.
