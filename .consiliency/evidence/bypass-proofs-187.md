# Acceptance-test bypass proofs — Consiliency/pmcp#187

The first version of this plan's acceptance test had three bypasses, all found
by the advisor board's correctness seat and all reproduced empirically before
being acted on. Each is recorded with the original (exit 0, wrongly passing)
and hardened (exit 1) result.

The general shape: **a check that only asserts presence proves almost nothing.**
This is the fifth round in this repo where a check I wrote looked adequate and
was not, three of those written immediately after documenting that exact
failure mode.

## A. `.yaml` extension escapes the glob

Original globbed `*.yml` only. GitHub runs both extensions.

    original : missing: none                          EXIT=0   <- WRONG
    hardened : ['sneaky.yaml:unbounded -> None']      EXIT=1

## B. Presence checked, value not

`timeout-minutes: "not a number"` passed. So did `timeout-minutes: 360` --
which **re-creates the six-hour default this issue exists to fix** while
satisfying the test.

    original : missing: none                          EXIT=0   <- WRONG
    hardened : ['release.yml:build -> 360']           EXIT=1

Hardened form asserts `isinstance(v, int) and not isinstance(v, bool) and
1 <= v <= 30`, and skips jobs with `uses:` (a reusable-workflow call cannot
carry `timeout-minutes`, so requiring one there would force invalid YAML).

## C. A job silently deleted still passes

Over-indenting `publish:` makes it parse as a key *inside* `build`'s mapping.
The YAML is valid, `jobs` becomes `[build, github-release]`, and the presence
check passes because every *surviving* job has a timeout. The publish job is
simply gone.

    original : jobs [build, github-release], missing: none   EXIT=0   <- WRONG
    hardened : release.yml: [build, github-release, publish]
                         -> [build, github-release]          EXIT=1

**This is the one that mattered.** The plan claimed "the real proof is CI
itself" -- false for `release.yml`, which triggers only on tag push and never
appears in PR checks. A mangled release.yml would have surfaced at the next tag
push, as a broken release. The hardened check compares job-name sets against
`main` and **fails** rather than printing.

## Also corrected

Live `test (3.10)` p100 is **5.30m** (measured over 5 runs), not the 4-5m the
plan's table recorded. The plan's own acceptance criterion ("each value >= 4x
observed") was therefore falsified by its own value of 20 on day one. Raised to
25.

## Why actionlint is mandatory here, not optional

`release.yml` cannot be exercised pre-merge -- it triggers only on tag push --
so actionlint is the *only* Actions-schema validation on the release path
before a tag is cut. The plan's original invocation was also fail-open
(`command -v actionlint && actionlint ... || echo skipped` swallows a non-zero
actionlint into the `||` branch), which is the same defect class as B above.
