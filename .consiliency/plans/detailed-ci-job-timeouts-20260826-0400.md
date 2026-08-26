# Detailed plan: give every CI job a timeout, and make a stalled release recoverable

## Task

Close Consiliency/pmcp#187. No job in `release.yml`, `test.yml`, `docker.yml`
or `maintenance.yml` sets `timeout-minutes`, so a stalled job runs to GitHub's
6-hour default. On the release path that is not merely slow: `publish` and
`github-release` each `needs:` the job before them, so **one stall silently
blocks the entire publish for six hours**, and since the tag push *is* the
publish, a version can sit tagged-but-unshipped with nothing surfacing it.

Observed while cutting v2.4.1: `build` hung 3h11m on `Run tests with coverage`
against a healthy commit (the same command locally: 2737 passed, 85.86%
coverage vs a 60% gate). Cancel + `gh run rerun` cleared it, so it was a
transient runner stall, not the code.

## Research summary

Measured on the live repo, not estimated — per-job durations from
`GET /actions/runs/{id}/jobs`, `(completed_at - started_at)`:

| workflow | job | observed | proposed timeout |
|---|---|---|---|
| `release.yml` | `build` | 4m10s | **20** |
| `release.yml` | `publish` | 19s | **10** |
| `release.yml` | `github-release` | 8s | **10** |
| `test.yml` | `test` (3.10/3.11/3.12) | **5.35m p100** (8 runs) | **25** |
| `test.yml` | `install-smoke` | <1m | **10** |
| `test.yml` | `min-version-smoke` | <1m | **10** |
| `test.yml` | `lint` | <1m | **10** |
| `test.yml` | `changelog` | <1m | **10** |
| `test.yml` | `typecheck` | <1m | **10** |
| `docker.yml` | `build` | <1m | **20** |
| `maintenance.yml` | `notify-worker` | 9s | **10** |
| `pipeline-bootstrap.yml` | `bootstrap` | 10m | already has **30** — leave it |

Sizing rule: roughly 4× the observed p100, floored at 10 minutes. The point is
to fail fast on a *stall*, not to police normal variance — a job that legitimately
grows 3× should still pass. `docker.yml` gets 20 rather than 10 despite being
fast, because image builds are network-bound and legitimately spiky.

`pipeline-bootstrap.yml` already sets 30 and is the only precedent in the repo;
it is left untouched so this change adds a rule rather than relitigating one.

## Changes

### `.github/workflows/release.yml` (modify)

- `build` job — **add** `timeout-minutes: 20` — 4m10s observed; the job that stalled.
- `publish` job — **add** `timeout-minutes: 10` — 19s observed; PyPI upload.
- `github-release` job — **add** `timeout-minutes: 10` — 8s observed.
- `on:` block — **unchanged.** `workflow_dispatch` was proposed and is
  **struck** — see *Recovery* below.

### `.github/workflows/test.yml` (modify)

- `test`, `install-smoke`, `min-version-smoke`, `lint`, `changelog`, `typecheck`
  — **add** `timeout-minutes` per the table above. `test` is a 3-version matrix;
  `timeout-minutes` at job level applies per matrix leg, which is what we want.

### `.github/workflows/docker.yml` (modify)

- `build` job — **add** `timeout-minutes: 20`.

### `.github/workflows/maintenance.yml` (modify)

- `notify-worker` job — **add** `timeout-minutes: 10`.

## Recovery: `workflow_dispatch` is STRUCK

*Board finding, two seats independently, both verified against the workflow.*

The proposal was to add `workflow_dispatch` with a required `tag` input. **It is
dropped**, and not only for the security widening I flagged — the specified
change was also simply **incorrect**:

- `actions/checkout@v7` at `release.yml:12` and `:63` pins **no ref**.
- Both tag derivations read `GITHUB_REF_NAME` (`release.yml:73`, `:96`).

Under `workflow_dispatch`, `GITHUB_REF_NAME` is the ref chosen in the *"Use
workflow from"* dropdown — **not** a workflow input. So dispatching with
`tag=v2.4.1` from `main` would check out `main`, build and publish **main's**
artifacts, then try to create a GitHub Release named `main`. The required input
would be silently ignored. Writing an input and never wiring it is the same
class of defect as a hollow test: it looks like it constrains something.

The failure mode is the bad one: **PyPI publish can succeed while the GitHub
release step fails**, leaving a half-published version. And the mitigations I
offered do not hold — PyPI trusted publishing scoped to repo+workflow does
**not** restrict to tag events, and `environment: release` only gates if that
environment has required reviewers, which this plan never verified.

Recovery already exists and is what actually unblocked v2.4.1: `gh run rerun`
on the failed tag-push run. And the timeouts themselves are the real recovery
improvement — they convert a 6-hour silent hang into a ~20-minute red X, which
is what surfaces the stall in the first place.

A correct dispatch path would check out `refs/tags/${{ inputs.tag }}`, use
`inputs.tag` everywhere in place of `GITHUB_REF_NAME`, assert `pyproject`
version == tag, handle an already-published PyPI version on partial recovery,
and keep environment protection. That is a separate design and out of scope for
#187.

## Documentation impact

- `CHANGELOG.md` — **none.** CI-only; no user-visible behaviour, no shipped
  artifact changes. Per repo convention the `changelog` job requires an entry
  only for `src/` changes, and this touches no `src/`. State the decision
  explicitly rather than silently skipping.
- No other cross-cutting doc describes CI job limits.

## Dependencies & order

None between the four files — each edit is independent and additive. Do them in
one commit so the rule lands uniformly rather than half-applied.

## Verification

**These scripts are the HARDENED forms.** An earlier draft of this plan shipped
presence-only checks that were proven bypassable three ways in the same change
— see `.consiliency/evidence/bypass-proofs-187.md`. Do not reintroduce the
`glob("*.yml")`-only, presence-only, or print-only variants.

```bash
cd /mnt/workspace/worktrees/pmcp-187-timeouts

# 0. STRONGEST CHECK, and the one to reach for first: audit the diff SHAPE.
#    "every changed line is an added timeout-minutes, nothing removed"
#    subsumes both checks below in one assertion, and unlike them it also
#    catches edits to `on:`, `needs:`, `environment:` and `permissions:`.
git diff main..HEAD -- .github/ | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' \
  | grep -vE '^\+\s+timeout-minutes: [0-9]+$' \
  && { echo "UNEXPECTED change beyond timeout-minutes"; exit 1; } \
  || echo "diff shape OK: additive timeout-minutes only"

# 1. Every job has a timeout AND the value is sane.
#    Presence alone is not enough: `timeout-minutes: 360` re-creates the very
#    6-hour default this change exists to remove, and a string passes too.
#    Both extensions: GitHub runs .yml and .yaml.
python3 - <<'PY'
import pathlib, yaml, sys
bad = []
files = sorted(p for ext in ("*.yml", "*.yaml")
                 for p in pathlib.Path(".github/workflows").glob(ext))
for f in files:
    for name, job in (yaml.safe_load(f.read_text()).get("jobs") or {}).items():
        if "uses" in job:      # reusable-workflow call cannot carry a timeout
            continue
        v = job.get("timeout-minutes")
        if not isinstance(v, int) or isinstance(v, bool) or not 10 <= v <= 30:
            bad.append(f"{f.name}:{name} -> {v!r}")
print("jobs with a missing/invalid timeout:", bad or "none")
sys.exit(1 if bad else 0)
PY

# 2. Job sets must MATCH main, and this must FAIL, not print.
#    A job silently deleted or swallowed by over-indentation yields a VALID
#    workflow that check 1 and actionlint both accept. For release.yml --
#    tag-push only, never in PR checks -- this is the sole guard.
python3 - <<'PY'
import pathlib, subprocess, yaml, sys
bad = []
for f in sorted(pathlib.Path(".github/workflows").glob("*.y*ml")):
    now = sorted((yaml.safe_load(f.read_text()).get("jobs") or {}).keys())
    base = subprocess.run(["git","show",f"main:{f}"],capture_output=True,text=True).stdout
    was = sorted((yaml.safe_load(base).get("jobs") or {}).keys()) if base else now
    if now != was:
        bad.append(f"{f.name}: {was} -> {now}")
    print(f"  {f.name}: {now}")
print("job-set changes:", bad or "none")
sys.exit(1 if bad else 0)
PY

# 3. actionlint is MANDATORY. Bare, with no glob -- `*.yml` would leave a
#    .yaml workflow schema-unchecked, the same gap as check 1's globs.
actionlint

# 4. bootstrap untouched; suite untouched by a CI-only change.
git diff main -- .github/workflows/pipeline-bootstrap.yml
uv run pytest tests/ -q
```

**What these checks do NOT guard.** They cover exactly *{job-name set} × {timeout
presence and value}*. Every other byte of `release.yml` — the `on:` tag filter,
`needs:` edges, `environment:`, `permissions:` — is unguarded by 1–3, which is
why check 0 (diff shape) leads. The `on:`-block class is the worst: corrupt the
tag pattern and there is **no run at all**, so no red X ever appears — exactly
#187's motivating failure. Check 0 catches it; nothing else here does.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```

## Execution Policy
- execute: effort=low, reason=additive YAML with measured values and no source change

## Acceptance criteria

- [ ] Every job in `release.yml`, `test.yml`, `docker.yml` and `maintenance.yml`
      declares `timeout-minutes` — proven by the enumeration script in
      Verification exiting 0, which fails today.
- [ ] Every workflow still parses and lists the same job names as on `main` —
      proven by the YAML round-trip in Verification, diffed against
      `git show main:.github/workflows/<f>` job lists.
- [ ] No timeout is below the observed p100 for its job — the values in the
      table are each ≥4× observed, so a normal run cannot be killed by this
      change.
- [ ] The PR's CI reports the **same set of checks** as a `main` PR, all
      passing — proven by `gh pr checks`, count and names compared.
- [ ] `pipeline-bootstrap.yml`'s existing `timeout-minutes: 30` is unchanged —
      proven by `git diff main -- .github/workflows/pipeline-bootstrap.yml`
      being empty.
- [ ] `workflow_dispatch` is **not** added, and the PR body records why —
      proven by `git diff main -- .github/workflows/release.yml` showing no
      change to the `on:` block. The board found the specified version was not
      merely a security widening but incorrect: an unwired `tag` input against
      `GITHUB_REF_NAME` would publish the dispatched *ref*, not the requested
      tag.
- [ ] `actionlint` was run and **passed**, or its absence is stated in the PR —
      proven by the explicit `if/else` in Verification, not the fail-open
      `&& … || echo` form. A non-zero actionlint must fail, not print "skipped".
