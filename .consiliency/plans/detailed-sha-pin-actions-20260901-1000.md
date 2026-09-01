# Detailed plan: pin every GitHub Action to a commit SHA, and guard the convention

> **Revision 3 (2026-09-01).** Rev 2 carried a **false premise of mine**: it said
> `release/v1` is 144 commits ahead of pypa's newest release, v1.9.0. The tag
> list was sorted lexically, so `v1.14.x` sorted before `v1.8`. The newest
> release is **v1.14.2**, and `release/v1`'s head **is** v1.14.2 — same commit.
> The 144 commits are between v1.9.0 and v1.14.2, i.e. five months of released
> work that a v1.9.0 pin would have **rolled back**. The operator's "pin to the
> release tag" decision was made on that error; it and "freeze today's head"
> now resolve to the same commit, so there is no rollback and no fork.
>
> **Revision 2 (2026-09-01).** Rev 1 boarded 2 DISAGREE / 1 AGREE. Three defects,
> each verified: (1) `EXPECTED_USES` with comments could **never match**, because
> `yaml.safe_load` strips comments before `_collect_uses` runs — every correctly
> pinned `release.yml` would have failed; (2) **three** mutants anchor on
> `@release/v1`, not one, and `replace()` refuses a non-unique anchor, so all
> three would have become silent survivors; (3) the local composite action's own
> `actions/setup-node@v4` runs with `id-token: write` and was wrongly out of
> scope. Also: `release/v1` is a **branch**, 144 commits past pypa's newest
> release — the operator chose to pin to the release tag instead.

## Task

Close Consiliency/pmcp#217. Every `uses:` reference in `.github/workflows/`
and in the local composite action resolves a **mutable tag** (or, for pypa, a
**branch**). Pin each to a commit SHA with the version as a trailing comment,
and add a guard so the convention cannot regress.

## Research summary

Verified in this worktree.

**The inventory, corrected.** 30 refs in `.github/workflows/`, of which 29 are
remote actions and 1 is the local path `./.github/actions/pipeline-bootstrap-setup`.
**Plus** that composite action's own `action.yml:7`: `actions/setup-node@v4`.
It runs inside `pipeline-bootstrap.yml`'s job with **`id-token: write` and
`contents: write`** (`:76-78`) — so it is not "repo-owned source reviewed like
any file"; it is a mutable ref with credentials in scope. **30 remote refs total,
0 SHA-pinned.** Rev 1 said 30 and excluded the composite; both were wrong.

**Dependabot has never touched `.github/actions/`.** Twenty Dependabot PRs on
this repo, zero under that path, and the composite still says `@v4` while every
workflow moved to `@v7`. Dependabot's `github-actions` ecosystem with
`directory: "/"` is evidently not scanning it here. A second `directory` entry
is required or the composite's pin freezes forever.

**`pypa/gh-action-pypi-publish@release/v1` is a branch, not a tag.**
`git/ref/tags/release/v1` returns nothing usable; `git/ref/heads/release%2Fv1`
resolves to `dc37677b2e1c…`. **That commit is exactly v1.14.2**, pypa's newest
release (2026-07-29). So the branch is their stable channel pointing at the
current release — not an unreleased build.

**WAS WRONG (rev 2):** it claimed the head was 144 commits ahead of the newest
release, v1.9.0, and that pinning meant choosing between an unreleased head
and a rollback. Both halves were a version-sort artifact: `matching-refs/tags/v1`
returns lexical order, in which `v1.14.2` precedes `v1.8.6`, so "the last five"
were v1.8.x–v1.9.0. Verified with `compare/`: `v1.14.2...release/v1` is
`identical, ahead_by=0`; `v1.9.0...v1.14.2` is `ahead_by=144`. **A v1.9.0 pin
would have been a real 144-commit rollback of the publish action.** Pin to
**v1.14.2** — which is both the release tag and today's head, so there is **no
behaviour change** and the comment `# v1.14.2` is honest and Dependabot-trackable.

**Version comparison must be numeric, not lexical.** Any "newest tag" step in
implementation or verification uses `sort -V` or the releases API's
`releases/latest`, never the raw tag list order. This mistake reached an
operator decision; it must not reach the code.

**Resolution procedure, corrected.** `git/ref/tags/<name>` with a slash in the
name is a prefix match and returns garbage. Use `git/matching-refs/tags/<name>`
and select the exact ref, or URL-encode. Annotated tags (`astral-sh/setup-uv@v7`
is one; the pypa release tags all are) need a second dereference through
`git/tags/<sha>`. 8 of the 10 distinct actions use lightweight tags.

**`yaml.safe_load` strips comments.** `_load` (`check_workflows.py:525-527`)
and the `:413` path both parse with `safe_load`, so by the time `_collect_uses`
(`:134`) sees a `uses:` value the `# vX.Y.Z` is gone. Any expected value that
includes a comment can never match. The comment convention must be checked on
**raw text**; the parsed `EXPECTED_USES` must hold the SHA form only.

**`replace()` in `_mutate_workflow.sh:104-120` refuses an anchor that does not
match exactly once.** After pinning, three mutants' anchors match zero times:
`continue-on-error-step` (`:229,232`), `if-on-publish-step` (`:245,248`), and
`forked-action` (`:286`). In the evidence loop (`mutate && commit; checker`) a
refused helper never commits, the checker runs on the clean tree, exits 0, and
the mutant is recorded as a **survivor** — while looking like a helper hiccup.
Rev 1 updated only `forked-action`. And `actions/checkout` appears 8× in
`test.yml` and 2× in `release.yml`, so any SHA-only anchor for a new mutant is
refused as non-unique; new anchors need step context.

## Changes

### `.github/workflows/*.yml` and `.github/actions/pipeline-bootstrap-setup/action.yml` (modify)

- Every remote `uses: owner/action@<tag>` — modify — to
  `uses: owner/action@<40-hex-sha> # <exact release>`. Resolve at implementation
  time with `git/matching-refs/tags/`, dereferencing annotated tags. **Do not
  copy SHAs from this plan.** The comment names the exact release (`# v7.0.0`),
  which is what Dependabot reads and what a human needs.
- `pypa/gh-action-pypi-publish` — modify — pin to **v1.14.2**'s commit
  (`dc37677b2e1c…`, full SHA resolved at implementation and asserted equal to
  the `release/v1` head), comment `# v1.14.2`. **No behaviour change**: this is
  the commit that runs today. **WAS WRONG (rev 2):** v1.9.0, a 144-commit
  rollback.
- The composite action's `setup-node@v4` — modify — pin to v7's commit like its
  workflow siblings, comment `# v7.0.0`.

### `.github/dependabot.yml` (modify)

- Add a second `github-actions` entry with
  `directory: "/.github/actions/pipeline-bootstrap-setup"`. **WAS WRONG (rev 1):**
  "no change" — Dependabot demonstrably does not scan that path here.

### `scripts/check_workflows.py` (modify)

- `EXPECTED_USES` (`:59`) — modify — to the **SHA form without comments**.
  **WAS WRONG (rev 1):** it put comments in; they can never match parsed YAML.
- `all_uses_are_sha_pinned(paths)` — add — **operates on raw text, not parsed
  YAML**, so it can see the comment. For every line matching `uses:` under
  `.github/workflows/*.yml` **and** `.github/actions/**/action.yml`: skip a value
  starting `./` (local path) or containing `.yml@` / `.yaml@` (reusable
  workflow); otherwise require
  `^\s*-?\s*uses:\s*[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+#\s*\S+`. The `[\w./-]+`
  admits subdirectory actions (`owner/repo/path@sha`). Reason text names file,
  line, and ref.
- **Call it from `main()`.** **WAS WRONG (rev 1):** unstated. The evidence loop
  runs the CLI, not pytest; a tests-only function lets the new mutants survive.
- Module docstring (`:22`) — modify — state the convention, and that comments are
  checked on raw text because the parser drops them.

### `scripts/_mutate_workflow.sh` (modify)

- **All three** `@release/v1` anchors — modify — to the pinned string:
  `continue-on-error-step`, `if-on-publish-step`, `forked-action`.
  **WAS WRONG (rev 1):** only `forked-action`.
- `tag-pinned-action` — add — reverts the `setup-node` pin in `test.yml`'s
  `test` job to `@v7` (unique: use the step context, since `checkout` is not
  unique). Fails via the new invariant.
- `sha-comment-dropped` — add — strips the comment from `release.yml`'s
  `upload-artifact` line (unique in the tree). Fails via the new invariant.
- `sha-moved` — add — alters one hex digit of the pypa SHA in `release.yml`
  (unique). Fails via `EXPECTED_USES`.
- `pypa-rolled-back` — add — replaces the pypa SHA with v1.9.0's commit
  (`ec4db0b4ddc6…`) and its comment with `# v1.9.0`. A valid SHA, a
  correct-looking comment, a real release — and 144 commits behind. Must fail
  via `EXPECTED_USES`. This is the mutant that would have caught rev 2's error.
- `composite-tag-pinned` — add — reverts the composite action's pin to `@v4`.
  Fails via the new invariant; proves the composite is in scope.
- The evidence loop — modify — record **the helper's exit code and the commit
  hash** per mutant, not only the checker's exit. A helper that refused never
  mutated, and its "kill" is fiction.

### `tests/test_workflow_guards.py` (modify)

- `TestShaPinning` — add — one test per new mutant plus: a correctly pinned ref
  with comment **passes**; a job-level reusable-workflow `uses:` is **skipped**;
  a subdirectory action `owner/repo/path@<sha> # v1` **passes**; a `docker://`
  `uses:` is reported (it is not pinnable by this scheme — decide: skip with a
  named reason, or fail; there are none today, so pin the decision by test).
- `TestMutationHelperContract` gains the four new mutants via its bidirectional
  check.

### `.consiliency/evidence/mutation-217.md` (create)

- The matrix, with per-mutant **helper exit, commit hash, checker exit, reason
  tag**. Include a row proving the three re-anchored mutants still mutate (their
  commit hash differs from base).

## Documentation impact

- `CHANGELOG.md` — add — `### Changed`, one bullet: every action is SHA-pinned
  (name the count, and that the PyPI publish action is pinned to v1.14.2, the
  commit `release/v1` already pointed at — so nothing runs differently).
  **WAS WRONG (rev 2):** a bullet announcing a 144-commit rollback that must not
  happen.
- `SECURITY.md` — add — a short supply-chain paragraph stating the pin
  convention and that Dependabot maintains it. **WAS WRONG (rev 1):** it pointed
  at `:39`, which is the JWKS sentence, not a release-path section.

## Dependencies & order

1. `all_uses_are_sha_pinned` on raw text, wired into `main()`, with tests —
   **RED against unchanged `main`, reporting 30 refs.** The guard must see the
   problem before anything is pinned.
2. Resolve every SHA with the corrected procedure; record the resolution output.
3. Pin all five workflows **and** the composite action.
4. **In the same commit as the `release.yml` pins:** update `EXPECTED_USES` and
   all three `@release/v1` mutant anchors. Otherwise `workflows` is red between
   commits and three mutants are silently dead.
5. `dependabot.yml` entry, new mutants, evidence matrix.
6. Docs.

## Verification

```bash
uv run pytest -q tests/test_workflow_guards.py
uv run python scripts/check_workflows.py --base-ref origin/main   # exit 0 after pinning

# Every SHA must resolve to the release its comment names. Aggregate exit; a
# MISMATCH is a failure, not a printout.
fail=0
grep -rnE "uses:\s*[^./ ][^ ]*@[0-9a-f]{40}\s*#" .github/workflows/*.yml .github/actions/*/action.yml \
| while IFS= read -r line; do
  ref=$(echo "$line" | sed -E 's/.*uses:\s*//; s/\s*#.*//'); tag=$(echo "$line" | sed -E 's/.*#\s*//; s/\s.*//')
  repo=${ref%@*}; sha=${ref#*@}
  got=$(gh api "repos/$repo/git/matching-refs/tags/$tag" 2>/dev/null \
        | python3 -c "import sys,json;rs=[r for r in json.load(sys.stdin) if r['ref']=='refs/tags/$tag'];print(rs[0]['object']['sha'] if rs else '')")
  typ=$(gh api "repos/$repo/git/matching-refs/tags/$tag" 2>/dev/null \
        | python3 -c "import sys,json;rs=[r for r in json.load(sys.stdin) if r['ref']=='refs/tags/$tag'];print(rs[0]['object']['type'] if rs else '')")
  [ "$typ" = tag ] && got=$(gh api "repos/$repo/git/tags/$got" -q .object.sha)
  if [ "$got" = "$sha" ]; then echo "OK       $ref  # $tag"; else echo "MISMATCH $ref  # $tag -> $got"; fail=1; fi
done
# (run the loop in the current shell, not a pipe subshell, so $fail propagates; exit $fail)

uv run ruff check src/ tests/ scripts/ && uv run mypy src/
```

Edge cases: annotated vs lightweight tags (both present); a `uses:` at job level
for a reusable workflow (none today — skip pinned by test); `docker://` refs
(none today — decision pinned by test); the composite action's `runs:` block
must not be mistaken for a step list.

## Acceptance criteria

- [ ] `all_uses_are_sha_pinned` is **RED against unchanged `main`** and reports
      **30** refs across `.github/workflows/` and the composite action. A guard
      that starts green has not seen the problem. Count is 30, not 29:
      rev 1 excluded the composite's ref.
- [ ] After pinning, the checker exits 0, and the resolution loop reports
      **30 OK, 0 MISMATCH with a non-zero aggregate exit on any mismatch** — a
      pin whose SHA does not match its claimed release must not pass.
      **WAS WRONG (rev 1):** its loop printed MISMATCH and exited 0.
- [ ] The three re-anchored mutants (`continue-on-error-step`,
      `if-on-publish-step`, `forked-action`) each **still mutate** — evidence
      shows a commit hash differing from base — and still exit non-zero. Rev 1
      would have left all three as silent survivors.
- [ ] The four new mutants each exit non-zero as committed scratch-branch
      mutants, with the same helper-exit + commit-hash evidence.
- [ ] A correctly pinned ref with comment passes; a reusable-workflow `uses:` is
      skipped; a subdirectory action passes. All three by synthetic fixture.
- [ ] `EXPECTED_USES` contains **no comments** and matches the pinned
      `release.yml` — proven by the checker exiting 0 on it, which rev 1's form
      could never have done.
- [ ] The pypa pin is **v1.14.2**'s commit **and equals the `release/v1` branch
      head at implementation time** — asserted by resolving both and comparing,
      so the "no behaviour change" claim is proven rather than assumed. If they
      have diverged by then, stop and re-decide; do not silently pick one.
- [ ] The resolution script determines "newest release" with `sort -V` or
      `releases/latest`, never lexical tag order — asserted by a test feeding it
      `v1.9.0, v1.14.2, v1.8.6` and requiring `v1.14.2`.
- [ ] `dependabot.yml` has the composite-action directory entry.
- [ ] Full suite green.

## Non-goals

- Verifying SHA currency inside the CI guard — a network call on the merge path.
  Dependabot owns currency; the guard owns form.
- Pinning `python:3.12-slim` in the `Dockerfile` by digest. Same class, different
  ecosystem, its own issue.
- Changing what the composite action *does*; only its one `uses:` is pinned.

## Execution Policy

- execute: effort=medium, reason=mechanical across seven files, but three
  mutant anchors and the guard constants must land atomically with the
  release.yml pins, and the pypa pin is a deliberate rollback on the publish path
