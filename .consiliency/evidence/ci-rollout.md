# CI gate rollout record

B0 introduces `ci-scope` and `ci-required` as FULL-only jobs. Both use embedded
standard-library code; no policy checkout, decoder or API can intercept recovery.
The table is P3-A: every PR job, including audit, must literally succeed. On
push/schedule/dispatch, only changelog and release-diff-ack must be skipped under
their existing PR-only conditions. Cancellation, missing or unknown results fail.

No hosted cancellation rehearsal has been completed. Until its result is known,
the Linux suite has no scope `needs` edge and no `if`, per the accepted fallback.
The local tests execute the actual embedded scripts with result/identity fixtures;
they do not prove GitHub context names or protection behavior.

Next stages are prerequisites, not current guarantees:

1. Complete the sandbox pending-scope cancellation rehearsal, including other
   required checks already successful, and capture check names and merge blocking.
2. Merge B0 only after its exact-head panel and full CI, then verify full main.
3. Introduce/pin the complete accepted asset classifier in B1 shadow; collect at
   least ten hosted runs, including three asset and three normal cases, plus
   fork/negative and broken-policy/evaluator recovery evidence.
4. Add app-bound ci-required and audit protection while retaining all eight old
   contexts; read back and rehearse actual red blocking.
5. Under this fallback, a distinct post-S shadow PR installs the scope edge and
   cancellation-aware condition with a literal true activation-off flag. Prove
   scope failure/skip still runs the real suite before retiring test protection.
6. Retire only the legacy test context after aggregate enforcement is proven.
   Only a later reviewed activation can omit the suite for exact ASSET_V1.
7. Observe at least ten eligible runs and fourteen days, whichever is later.

Rollback retains both stable job IDs and the required aggregate. Any nonempty
PMCP_CI_FORCE_FULL restricts execution to the embedded FULL-only result contract.
It cannot waive a failed suite/audit. Repair a broken embedded evaluator through
an exact-head reviewed FULL-only recovery PR; never remove the only required
suite enforcement. If retiring the aggregate, first restore app-bound test
protection, read back and demonstrate actual red blocking.

The trust root remains exact-head human cross-vendor control-plane review.
Candidate-owned workflow and guard code cannot resist an arbitrary author who
rewrites both. There is no new unattended-merge authority.

The current owner instruction selects P3-A: audit blocks every PR, including
asset-only and unrelated changes. This is an implementation default already
authorized by the owner, not a new pending policy choice.

Bootstrap guards bind the entire two job definitions (including env, outputs,
step IDs, shells, matrix absence and stable names) as well as the embedded code.
Global execution overrides and top-level trigger filters fail closed. All
selected verification steps reject unexpected if/continue-on-error and shell overrides;
step names are unique and the exact suite command binds its unconditional step;
the locked audit's exact execution is pinned. Stable ci-scope/ci-required names
cannot be emitted by another workflow. These remain reviewed controls within
the human workflow-author trust boundary. They are not an independent defense
against an author who rewrites the workflows and the guards together.

Source records: original B0 d3dfb30c39530482582bd0efb004d062ac739921 was reviewed
against A6c44b3bf25edab7d79dd294ded2d23dfa0738f26. The corrected B0 is rebased on
Acf09cd50eff39ec762db807f4659bf27ef743945 and the separate advisory-floor patch
446afb18a67efcedb46c71136c9d966c2441d772. Full current implementation head,
findings and new-round receipts live in the durable implementation status and
PR426. No previous head/base approval is reused.
