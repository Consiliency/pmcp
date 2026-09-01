# Mutation evidence — Consiliency/pmcp#217

Every remote `uses:` in `.github/workflows/` and in the local composite action
`.github/actions/pipeline-bootstrap-setup/action.yml` is pinned to a commit SHA
with the release named in a trailing comment, and `scripts/check_workflows.py`
refuses any other form. This document is the transcript of the loop that proves
the guard sees each way that can regress.

Two checks divide the work. `all_uses_are_sha_pinned` runs on **raw text**
(`yaml.safe_load` drops the comment, so parsed YAML cannot see half the
convention) and rejects the *form*: a mutable tag, a branch, a missing comment.
It cannot know which commit is right. `EXPECTED_USES`, now in SHA form, rejects
the *commit* on `release.yml`: a moved digit, or a real older release with an
honest comment. The matrix names which check killed each mutant, so a mutant
killed by the wrong one is a loop failure, not a pass.

## Before pinning

The invariant was wired and run against the unpinned tree **before** any pin
was made. It reported **30 `[pin]` failures** — 29 in the workflows and 1 in
the composite — plus 3 `[release]` mismatches from the already-SHA-form
allowlist: 33 in total. A guard that starts green has not seen the problem.

## Resolution

Each pin is the commit its *original* mutable ref resolved to on 2026-09-01,
peeled through annotated tags (`astral-sh/setup-uv@v7` and every pypa release
tag are annotated; pinning the tag object's SHA would pass a form check and
break only at tag push). The verifier in the plan checks two things per pin:
(a) the trailing comment's tag resolves to the pinned SHA, and (b) the multiset
of `repo@commit` over the pre-pin refs on `origin/main` equals the multiset of
pinned `repo@sha` in the tree. (b) is the "no behaviour change" proof, keyed per
line because `actions/setup-node` is `@v4` in the composite and `@v7` in the
workflows.

```
checked 30 pinned refs (expect 30)          # 30 × OK(a)
OK(b)    all 30 pins equal their original refs' commits
exit 0
```

Fail-closed, both branches seen to fire:

```
# one hex digit of the pypa SHA altered
MISMATCH(a) pypa/gh-action-pypi-publish@…73ba34  # v1.14.2 -> …73ba33
exit 1
# pypa set to v1.9.0's REAL commit with an honest "# v1.9.0" comment
OK(a)    pypa/gh-action-pypi-publish@ec4db0b4ddc65acdf4bff5fa45ac92d78b56bdf0  # v1.9.0
MISMATCH(b):
< pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33
> pypa/gh-action-pypi-publish@ec4db0b4ddc65acdf4bff5fa45ac92d78b56bdf0
exit 1
```

The second proof is the one that matters: (a) alone accepts a correct-looking
rollback. v1.9.0 is 144 commits behind v1.14.2 and below the
GHSA-vxmw-7h4f-hqxh floor (`< 1.13.0`). `release/v1` and `v1.14.2` resolved to
the same commit (`dc37677b…`) on the day, so the pypa pin changes nothing that
runs; the composite's `setup-node@v4` pins to v4.4.0 (`49933ea…`), likewise.

| original ref | pinned | release |
|---|---|---|
| `actions/checkout@v7` ×12 | `3d3c42e5aac5ba805825da76410c181273ba90b1` | v7.0.1 |
| `actions/download-artifact@v7` ×2 | `37930b1c2abaa49bbe596cd826c3c89aef350131` | v7.0.0 |
| `actions/setup-node@v7` ×2 | `820762786026740c76f36085b0efc47a31fe5020` | v7.0.0 |
| `actions/setup-node@v4` ×1 (composite) | `49933ea5288caeca8642d1e84afbd3f7d6820020` | v4.4.0 |
| `actions/upload-artifact@v7` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | v7.0.1 |
| `astral-sh/setup-uv@v7` ×7 | `37802adc94f370d6bfd71619e3f0bf239e1f3b78` | v7.6.0 |
| `docker/build-push-action@v7` | `53b7df96c91f9c12dcc8a07bcb9ccacbed38856a` | v7.3.0 |
| `docker/login-action@v4` | `dbcb813823bdd20940b903addbd779551569679f` | v4.6.0 |
| `docker/metadata-action@v6` | `dc802804100637a589fabce1cb79ff13a1411302` | v6.2.0 |
| `docker/setup-buildx-action@v4` | `37fe631027851001ddb9b187196cc803df7f5f0e` | v4.3.0 |
| `pypa/gh-action-pypi-publish@release/v1` | `dc37677b2e1c63e2034f94d8a5b11f265b73ba33` | v1.14.2 |

## Protocol

As in `mutation-189.md`: mutants are **committed on a scratch branch**, never
left in the working tree (drift uses a three-dot diff and only sees committed
changes). This loop additionally records the **helper's exit code and the
mutant's commit hash**. That column exists because three mutants anchored on
`@release/v1`; after pinning, an un-updated anchor matches zero times,
`replace()` refuses, nothing is committed, the checker runs on the clean tree
and exits 0 — and the survivor looks like a helper hiccup. A row whose helper
exit is not 0, or whose hash equals the base, is a loop failure regardless of
the checker column. `PYTHONDONTWRITEBYTECODE=1` throughout.

```bash
git switch -c scratch-mutants
BASE=$(git rev-parse HEAD)
./scripts/_mutate_workflow.sh --list | while IFS='|' read -r name expected tags desc; do
  ./scripts/_mutate_workflow.sh "$name"; hexit=$?
  [ $hexit -eq 0 ] && git add -A && git commit -qm "mutant: $name"
  uv run python scripts/check_workflows.py --base-ref "$BASE"; echo "$name helper=$hexit commit=$(git rev-parse --short HEAD) checker=$?"
  git switch -q -C scratch-mutants "$BASE"      # not `reset --hard`, not `stash`
done
```

## Matrix

Base: the implementation commit. `helper` is `_mutate_workflow.sh`'s exit,
`commit` the mutant's committed hash (all distinct from base), `checker` is
`check_workflows.py --base-ref <base>`, `reason` the tag(s) it reported.

| mutant | expected | helper | commit | checker | reason |
|---|---|---|---|---|---|
| *(none: unmutated tree)* | 0 | — | — | 0 | — |
| `tag-case` | 1 | 0 | `71d0591` | **1** | `[release]` |
| `tags-deleted` | 1 | 0 | `012aa87` | **1** | `[release]` |
| `trigger-pull-request-added` | 1 | 0 | `88aec17` | **1** | `[release]` |
| `trigger-push-branches-added` | 1 | 0 | `531135f` | **1** | `[release]` |
| `trigger-workflow-dispatch-added` | 1 | 0 | `b6aa0ac` | **1** | `[release]` |
| `trigger-paths-filter-added` | 1 | 0 | `d0300a9` | **1** | `[release]` |
| `env-dropped` | 1 | 0 | `cc34cec` | **1** | `[release]` |
| `env-renamed` | 1 | 0 | `dea8f64` | **1** | `[release]` |
| `needs-build-dropped` | 1 | 0 | `f5fca3f` | **1** | `[release]` |
| `needs-publish-to-build` | 1 | 0 | `452520b` | **1** | `[release]` |
| `continue-on-error-job` | 1 | 0 | `edfd0f7` | **1** | `[release]` |
| `continue-on-error-step` | 1 | 0 | `f6acee8` | **1** | `[release]` |
| `if-on-build-job` | 1 | 0 | `dbe90c6` | **1** | `[release]` |
| `continue-on-error-build-job` | 1 | 0 | `3f7fd30` | **1** | `[release]` |
| `new-tag-triggered-workflow` | 1 | 0 | `d78d7c2` | **1** | `[release]` |
| `if-on-publish-job` | 1 | 0 | `eca06df` | **1** | `[release]` |
| `if-on-publish-step` | 1 | 0 | `0f80261` | **1** | `[release]` |
| `forked-action` | 1 | 0 | `1fa72c5` | **1** | `[release]` |
| `permissions-job-widened` | 1 | 0 | `93009de` | **1** | `[release]` |
| `permissions-workflow-level` | 1 | 0 | `dbc0efb` | **1** | `[release]` |
| `job-added` | 1 | 0 | `a36fdaa` | **1** | `[release]` |
| `job-deleted` | 1 | 0 | `bb42751` | **1** | `[drift] [release]` |
| `file-deleted` | 1 | 0 | `f29256d` | **1** | `[drift] [release]` |
| `timeout-360` | 1 | 0 | `583eb9d` | **1** | `[timeout]` |
| `timeout-1` | 1 | 0 | `35f8f2a` | **1** | `[timeout]` |
| `timeout-string` | 1 | 0 | `d0195eb` | **1** | `[timeout]` |
| `timeout-bool` | 1 | 0 | `a5ecf95` | **1** | `[timeout]` |
| `timeout-deleted` | 1 | 0 | `a33022e` | **1** | `[timeout]` |
| `maintenance-deleted` | 1 | 0 | `ce938c0` | **1** | `[drift]` |
| `changelog-job-deleted` | 1 | 0 | `1c04129` | **1** | `[drift]` |
| `workflows-job-deleted` | 1 | 0 | `2e7fc1e` | **1** | `[drift]` |
| `tag-pinned-action` | 1 | 0 | `430c549` | **1** | `[pin]` |
| `sha-comment-dropped` | 1 | 0 | `2dd6774` | **1** | `[pin]` |
| `sha-moved` | 1 | 0 | `10b6f01` | **1** | `[release]` |
| `pypa-rolled-back` | 1 | 0 | `b6b79b7` | **1** | `[release]` |
| `composite-tag-pinned` | 1 | 0 | `45b0cf8` | **1** | `[pin]` |
| `uses-quoted-key` | 1 | 0 | `8a3784e` | **1** | `[pin]` |
| `needs-as-list` | 0 | 0 | `6930fca` | 0 | — |
| `timeout-below-p100` | 0 | 0 | `b54e1c0` | 0 | — |
| `concurrency-added` | 0 | 0 | `aada7f6` | 0 | — |
| `guard-self-disabled` | 0 | 0 | `fafa2ba` | 0 | — |
| `guard-self-disabled-nonconstant` | 0 | 0 | `a47eb2c` | 0 | — |
| `guard-step-gutted` | 0 | 0 | `eff41d7` | 0 | — |

43 rows, 37 expected to fail, 6 expected green; every helper exit 0, every hash
distinct, every expected failure reported by the expected check, every expected
green row green.

### What the six new rows prove

- `tag-pinned-action`, `composite-tag-pinned`, `sha-comment-dropped` — the
  **form** check, in a workflow, in the composite (which runs with
  `id-token: write` and `contents: write`), and for a missing comment. The
  composite row is the one Dependabot's root entry could never have produced.
- `uses-quoted-key` — `"uses": actions/setup-node@v4` in the composite: valid
  YAML that GitHub runs and the line scan cannot see. It was a real bypass of
  the first cut of this guard (found by the board on #218): `"uses":`,
  `uses :`, `{uses: x}` and `uses: >-` all parsed as executable steps while
  `remote_uses` returned nothing. Killed now by the parsed-inventory
  cross-check — the parsed document's remote `uses:` values must equal the
  raw scan's, per file — which turns every unreadable spelling into a failure.
  Five such forms are pinned by test.
- `sha-moved`, `pypa-rolled-back` — form-valid pins to the **wrong commit**,
  killed by `EXPECTED_USES` and tagged `[release]`, not `[pin]`. The pin
  invariant does not and cannot catch these; the evidence says so rather than
  implying it.

### Anchors match the form of a pin, not its value

The first Dependabot bump of the composite's pin (#219, v4.4.0 → v7.0.0) went
red: two mutants and two tests had anchored on the literal
`@49933ea… # v4.4.0`. Every anchor that contains a pinned `uses:` line now
matches `@<40 hex> # <release>` by regex (`replace_re`, `replace_after_re`,
`flip_last_hex`), still exactly-once and fail-loud, and a contract test
forbids any 40-hex literal in the helper or the tests other than the
deliberate v1.9.0 rollback target. The matrix above was re-run after that
change; every row still mutates and is still killed.

### Re-anchored mutants

`continue-on-error-step`, `if-on-publish-step` and `forked-action` all anchored
on `uses: pypa/gh-action-pypi-publish@release/v1`. Their rows show helper 0
and a fresh hash: they still mutate. `forked-action` keeps the SHA form and the
comment, so only the allowlist sees the owner change — as the row's `[release]`
says.

### Not covered, unchanged from #189

The six expected-green rows are the cases the checker deliberately does not
own (`release-diff-ack` and the `test` job do). Also not covered here, by
design: whether a pinned SHA is *current* — Dependabot owns currency, the guard
owns form; and `docker://` references, which the guard reports rather than
admits until that decision is made in `check_workflows.py`. A `uses:` in a
form the line scan cannot read is **not** a residual: the parsed-inventory
cross-check fails it (`uses-quoted-key`).
