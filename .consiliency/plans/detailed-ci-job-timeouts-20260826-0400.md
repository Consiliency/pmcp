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
| `test.yml` | `test` (3.10/3.11/3.12) | 4–5m across 5 runs | **20** |
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

```bash
cd /mnt/workspace/worktrees/pmcp-187-timeouts

# 1. Every job in every workflow now has a timeout (the actual acceptance test).
python3 - <<'PY'
import pathlib, yaml, sys
missing = []
for f in sorted(pathlib.Path(".github/workflows").glob("*.yml")):
    for name, job in (yaml.safe_load(f.read_text()).get("jobs") or {}).items():
        if "timeout-minutes" not in job:
            missing.append(f"{f.name}:{name}")
print("jobs missing timeout-minutes:", missing or "none")
sys.exit(1 if missing else 0)
PY

# 2. Every workflow still parses as valid YAML with its jobs intact.
python3 -c "
import pathlib, yaml
for f in sorted(pathlib.Path('.github/workflows').glob('*.yml')):
    d = yaml.safe_load(f.read_text())
    print(f.name, '->', sorted((d.get('jobs') or {}).keys()))
"

# 3. actionlint — Actions-SCHEMA validation, which a generic YAML load cannot
#    replace. NOTE the shape: `cmd && actionlint || echo skipped` is FAIL-OPEN,
#    because the `||` branch also swallows a non-zero actionlint. Any finding
#    would print "skipped" and exit 0. Branch explicitly instead (board finding,
#    red-team seat — same shape as the hollow assertions in #184/#185).
if command -v actionlint >/dev/null 2>&1; then
  actionlint .github/workflows/*.yml   # non-zero here MUST fail the run
else
  echo "actionlint absent — schema not validated, say so in the PR"
fi

# 4. The repo suite must be untouched by a CI-only change.
uv run pytest tests/ -q
```

**The real proof is CI itself**: open the PR and confirm every check still
passes *and* that each job now reports a timeout. A YAML edit that parses but
disables a job would show up as a check that never runs — check the PR's check
list against `main`'s, count included.

Edge cases: `timeout-minutes` at job level vs step level (job level is correct
here — it bounds the whole job including setup); matrix jobs (applies per leg);
`needs:`-chained jobs (each needs its own, a timeout does not inherit).

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
