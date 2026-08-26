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
so actionlint is the only Actions-*schema* validation on the release path
before a tag is cut. It is not sufficient on its own: see c3 below, where a
deleted job passes actionlint cleanly and only the job-set comparison catches
it. The plan's original invocation was also fail-open
(`command -v actionlint && actionlint ... || echo skipped` swallows a non-zero
actionlint into the `||` branch), which is the same defect class as B above.

---

## Corrections and additions from the implementation pass

The implementer re-ran the whole matrix against the committed state and found
three things this document got wrong or missed. All re-verified independently
before being recorded.

### C is caught by check #2, NOT by check #1

My instruction to the implementer said all three bypasses must make **check #1**
exit non-zero. That is wrong for C. Measured:

    c  (publish over-indented into build)   check1=0  check2=1  actionlint=1
    c2 (terminal job over-indented)         check1=0  check2=1  actionlint=1
    c3 (terminal job DELETED outright)      check1=0  check2=1  actionlint=0

Check #1 counts jobs that *survive parsing*; a swallowed job is not one, so its
absence cannot make a presence-check fail. The bypass is genuinely closed --
just by a different check than I claimed. The body of this document already
attributes C to the job-set comparison, so only the instruction was wrong.

### c3 is the finding that matters: check #2 is the SOLE guard

Deleting a terminal job outright yields a **perfectly valid workflow**.
Verified: check #1 exits 0, **actionlint exits 0**, and only the job-set
comparison against `main` exits 1.

actionlint caught c and c2 only because over-indentation is a *syntax* error
(`unexpected key "publish" for "job" section`, plus `job "github-release" needs
job "publish" which does not exist`). Deletion is not a syntax error, so nothing
schema-level sees it.

So for `release.yml` -- which triggers on tag push only and therefore never
appears in PR checks -- the job-set comparison is the *only* thing standing
between a dropped `publish` job and a silently broken release. That is the
argument for its fail-rather-than-print form, stated more strongly than this
document originally did.

### The actionlint invocation had the same extension gap as bypass A

`actionlint .github/workflows/*.yml` never schema-checks a `.yaml` workflow.
Verified with a `sneaky.yaml` carrying a bogus `with:` key:

    actionlint .github/workflows/*.yml   -> exit 0   (never looked)
    actionlint                           -> exit 3   (discovers both)

Check #1 was widened to both extensions but the actionlint line was not -- the
same gap, one layer down. **Use bare `actionlint`**, which discovers both
extensions itself, rather than globbing.

### Known-inert exemption

Check #1 skips jobs carrying `uses:` (a reusable-workflow call cannot take
`timeout-minutes`). No job in any of the five workflows is such a call today, so
the exemption is currently untested against real input. It is correct to have --
without it, a future `uses:` job would force invalid YAML to satisfy the check --
but it would silently exempt any future job that gains a `uses:` key.
