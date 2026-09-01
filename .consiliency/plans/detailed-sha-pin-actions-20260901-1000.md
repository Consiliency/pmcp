# Detailed plan: pin every GitHub Action to a commit SHA, and guard the convention

## Task

Close Consiliency/pmcp#217. All 30 `uses:` references in `.github/workflows/`
resolve a **mutable tag**. Pin each to its commit SHA with the version kept as a
trailing comment, and add a guard so the convention cannot regress.

## Research summary

Verified in this worktree.

**The inventory.** 30 refs, 11 distinct actions, 0 SHA-pinned:
`actions/checkout@v7` ×12, `astral-sh/setup-uv@v7` ×7,
`actions/download-artifact@v7` ×2, `actions/setup-node@v7` ×2,
`actions/upload-artifact@v7`, `docker/build-push-action@v7`,
`docker/metadata-action@v6`, `docker/login-action@v4`,
`docker/setup-buildx-action@v4`, and **`pypa/gh-action-pypi-publish@release/v1`**.
`./.github/actions/pipeline-bootstrap-setup` is a local path — not a ref, not
in scope.

**The one that matters most.** `pypa/gh-action-pypi-publish@release/v1` runs in
`release.yml`'s `publish` job with `id-token: write` — the PyPI trusted-publishing
identity. `release/v1` is a floating ref by the action's own design. For this
repo the tag push *is* the publish, so a moved ref runs new code with PyPI
credentials in scope and **no diff, no PR, no review**.

**Dependabot already handles SHA pins.** `.github/dependabot.yml` tracks
`github-actions` weekly. Dependabot's native behaviour for a SHA-pinned action
is to bump the SHA and maintain a trailing `# vX.Y.Z` comment. Pinning costs
nothing in automation — *provided the comment convention is followed*, because
Dependabot reads the comment to know which release line to track.

**The #189 guard already asserts `release.yml`'s refs exactly.**
`EXPECTED_USES` at `scripts/check_workflows.py:59` lists each job's `uses:`
values verbatim, and `_mutate_workflow.sh:285`'s `forked-action` mutant proves a
repoint fails. Both are written against the current tag strings. Pinning
`release.yml` therefore **requires updating both in the same PR**, or the
`workflows` job goes red on the pinning PR itself.

**The cost, which is the point.** After this, a Dependabot bump touching
`release.yml` fails `workflows` until `EXPECTED_USES` is updated alongside it.
That converts a one-file bot PR into a two-file human PR — for `release.yml`
only. That is exactly the deliberate-change property #189 exists to enforce on
that file, so it is accepted rather than worked around.

## Changes

### `.github/workflows/*.yml` (modify — all five)

- Every `uses: owner/action@<tag>` — modify — to
  `uses: owner/action@<40-hex-sha> # <tag>`. Resolve each SHA **at
  implementation time** from the tag the file currently names, via
  `gh api repos/<owner>/<action>/git/ref/tags/<tag>` (dereferencing an annotated
  tag to its commit — `git/ref/tags/` can return a tag object, whose `object.sha`
  must be followed once more). Do not copy SHAs from this plan or from memory;
  a SHA that does not match the tag it claims is worse than the tag.
- The trailing comment carries the **exact release** (`# v7.0.0`), not the
  major (`# v7`), where the tag resolves to a specific release. Dependabot uses
  it; a human reading the file needs it; and it makes drift visible.
- `pypa/gh-action-pypi-publish@release/v1` — modify — pin to the commit
  `release/v1` currently points at, comment `# release/v1 (<vX.Y.Z>)` naming the
  release it corresponds to. This is the highest-value pin in the tree.

### `scripts/check_workflows.py` (modify)

- `EXPECTED_USES` (`:59`) — modify — to the pinned forms, **including the
  comment**, so that a pin whose comment has been silently dropped also fails.
  The existing exact-match invariant then catches a moved SHA for free.
- `all_uses_are_sha_pinned(paths)` — add — a new invariant across **every**
  workflow file: each `uses:` that is not a local path (`./…`) or a reusable
  workflow must match `^[\w.-]+/[\w.-]+@[0-9a-f]{40}\s+#\s*\S+`. Reason text
  names the offending file, job, and ref. Without this the convention regresses
  the first time someone pastes `@v4` from a README.
- The module docstring (`:22`) — modify — state the pin convention and that
  `EXPECTED_USES` now includes comments.

### `scripts/_mutate_workflow.sh` (modify)

- `forked-action` (`:285`) — modify — the mutant must repoint the **pinned**
  string; today it replaces a tag string that will no longer exist, so the
  mutant would silently stop mutating and the matrix would show it "killed"
  because the sed matched nothing and the tree was unchanged. **Verify the sed
  actually changed the file** before recording the exit code.
- `tag-pinned-action` — add — reverts one ref in `test.yml` to `@v7`. Must
  fail via the new invariant.
- `sha-comment-dropped` — add — keeps the SHA but removes the `# vX.Y.Z`
  comment on one ref. Must fail: a pin without its comment is unmaintainable.
- `sha-moved` — add — changes one release.yml SHA by a digit. Must fail via
  `EXPECTED_USES`, proving the exact-match still bites after pinning.

### `tests/test_workflow_guards.py` (modify)

- `TestShaPinning` — add — one test per new mutant above, in memory, plus the
  positive case: a correctly pinned ref with a comment passes.
- `TestMutationHelperContract` — the helper list must gain the three new
  mutants, and the bidirectional inclusion test will enforce it.

### `.consiliency/evidence/mutation-217.md` (create)

- Extend the #189 matrix with the new rows and **real committed-mutant exit
  codes**, including a check that each mutant's sed actually modified the tree.

## Documentation impact

- `CHANGELOG.md` — add — `### Changed`: every action is now SHA-pinned; name the
  PyPI publish action explicitly, since that is the one an operator would want to
  know about.
- `SECURITY.md` — modify — the supply-chain section, if present, should state
  the pin convention; if absent, add one sentence under the existing
  release-path discussion (`:39` area). Check before editing.
- `.github/dependabot.yml` — **no change**. Confirm in the PR body that the
  existing `github-actions` entry handles SHA pins, so a reader does not assume
  automation was lost.

## Dependencies & order

1. The new `all_uses_are_sha_pinned` invariant and its tests **first, against
   unchanged workflows** — it must go RED on the current tree. That is the proof
   the guard sees the problem before anything is pinned.
2. Resolve SHAs and pin all five workflow files.
3. Update `EXPECTED_USES` and the `forked-action` mutant in the same commit as
   the `release.yml` pins — otherwise `workflows` is red between commits.
4. New mutants, evidence matrix.
5. Docs.

## Verification

```bash
uv run pytest -q tests/test_workflow_guards.py
uv run python scripts/check_workflows.py --base-ref origin/main   # exit 0 after pinning

# Every SHA must resolve to the tag it claims -- assert it, do not eyeball it:
grep -rhoE "uses:\s*\S+@[0-9a-f]{40}\s*#\s*\S+" .github/workflows/*.yml | while read -r line; do
  ref=$(echo "$line" | sed -E 's/uses:\s*//; s/\s*#.*//'); tag=$(echo "$line" | sed -E 's/.*#\s*//')
  repo=${ref%@*}; sha=${ref#*@}
  got=$(gh api "repos/$repo/git/ref/tags/$tag" -q .object.sha 2>/dev/null)
  # annotated tags: follow one level
  [ "$(gh api "repos/$repo/git/ref/tags/$tag" -q .object.type)" = tag ] && got=$(gh api "repos/$repo/git/tags/$got" -q .object.sha)
  [ "$got" = "$sha" ] && echo "OK   $ref" || echo "MISMATCH $ref (tag $tag -> $got)"
done

# Mutants committed on a scratch branch; check each sed changed the tree.
uv run ruff check src/ tests/ scripts/ && uv run mypy src/
```

Edge cases: an annotated tag (two-level dereference — `actions/*` use these);
an action whose tag was retagged upstream between resolution and merge (re-run
the resolver script in CI's `workflows` job? — no: that is a network call in a
guard, rejected; the guard asserts *form*, Dependabot asserts *currency*); a
`uses:` at **job** level for a reusable workflow (none exist today — the
invariant must skip `uses:` whose value contains `.yml@`, and a test must pin
that skip so a future reusable workflow is not misreported).

## Acceptance criteria

- [ ] `all_uses_are_sha_pinned` is **RED against unchanged `main`** and reports
      all 29 non-local refs — proven by running the new invariant before any
      workflow is edited. A guard that starts green has not seen the problem.
- [ ] After pinning, the checker exits 0 on the tree, and the SHA-resolution
      loop above reports **29 OK, 0 MISMATCH**. A pin whose SHA does not match
      its claimed tag must not pass.
- [ ] The three new mutants (`tag-pinned-action`, `sha-comment-dropped`,
      `sha-moved`) each exit non-zero as **committed** scratch-branch mutants,
      with evidence that each sed **actually modified the file** — the existing
      `forked-action` mutant would otherwise silently stop mutating once its
      target string is gone.
- [ ] A correctly pinned ref with its comment **passes** the new invariant, and
      a job-level reusable-workflow `uses:` is **skipped**, not flagged — proven
      by a synthetic fixture.
- [ ] `TestMutationHelperContract`'s bidirectional check passes with the new
      mutants present, so the helper and the tests still name the same set.
- [ ] Every remaining `uses:` in `.github/workflows/` is either a local path or
      matches the pinned form — asserted by grep in the evidence file, count 29.
- [ ] Full suite green.

## Non-goals

- Pinning the local composite action `./.github/actions/pipeline-bootstrap-setup`
  — it is repo-owned source, reviewed like any other file.
- Changing Dependabot's cadence or grouping.
- Verifying SHA currency inside the CI guard. That would put a network call on
  the merge path; Dependabot owns currency, the guard owns form.
- Pinning Docker base images in `docker.yml`'s Dockerfile — same class, separate
  ecosystem, its own issue if wanted.

## Execution Policy

- execute: effort=medium, reason=mechanical edits across five files, but the
  guard and mutant updates must land atomically with the release.yml pins or CI
  is red mid-sequence, and a wrong SHA is a silent supply-chain failure
