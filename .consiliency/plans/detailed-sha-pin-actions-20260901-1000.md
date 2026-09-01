# Detailed plan: pin every GitHub Action to a commit SHA, and guard the convention

> **Revision 5 (2026-09-01).** Rev 4.1 boarded 2 DISAGREE (grok, codex; gemini
> errored, Claude seat unavailable). Five findings, all verified against this
> file: (1) the Execution Policy still called the pypa pin "a deliberate
> rollback" — a rev 2 sentence contradicting the v1.14.2 decision; (2) the
> verifier's comment promised check (b), SHA == the *original mutable ref's*
> commit, but the code only resolved the comment's tag, so an old-but-real
> release with its matching SHA passed; (3) an acceptance criterion demanded a
> "resolution script" that picks the newest release with `sort -V` — no such
> script existed in Changes, and picking "newest" is not the pin rule anyway;
> (4) Changes listed five new mutants, two other sections said four; (5) the
> composite's `setup-node` was bumped v4 → v7 in a job with `id-token: write`
> while Non-goals said "changing what the composite does" is out of scope.
> Operator decision: **pin v4's current commit (v4.4.0) — zero behaviour change
> anywhere in this PR**; Dependabot's new directory entry will propose v7 as its
> own reviewable PR. Check (b) is now actually scripted: the verifier reads the
> *pre-pin* refs from `origin/main`, resolves each (tag or branch, peeled), and
> requires the pinned SHA to equal that commit.
>
> **Revision 4 (2026-09-01).** Rev 2's board (2 DISAGREE; one seat errored)
> returned after rev 3. Its pypa finding matched rev 3's correction and added
> **GHSA-vxmw-7h4f-hqxh** — `pypa/gh-action-pypi-publish < 1.13.0` is vulnerable
> to injectable expression expansions, so the v1.9.0 pin would have been a
> *vulnerable* rollback. Five further verified defects fixed below: the
> verifier's `grep | while` subshell lost its failure flag; the reusable-workflow
> exemption was a hole; `.yaml` forms were unguarded; the `tag-pinned-action`
> anchor was not unique; and the verifier proved SHA↔comment but not
> SHA↔*current target*, so a correct-looking rollback passed it. (Rev 4 only
> *described* that last fix in a comment; rev 5 scripts it.)
>
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

**And v1.9.0 was vulnerable.** GHSA-vxmw-7h4f-hqxh (low): injectable expression
expansions in action steps, `pypa/gh-action-pypi-publish < 1.13.0`, patched in
1.13.0. Verified via the advisories API. So rev 2's pin was not merely five
months stale — it would have reintroduced a published advisory on the publish
path. v1.14.2 is clear. Any future re-pin must stay `>= 1.13.0`.

**Version comparison must be numeric, not lexical.** This change contains
**no** "pick the newest tag" step — the pin rule is "the commit the original
mutable ref resolves to today", full stop. That is precisely because a
newest-picker sorted lexically is how rev 2 chose v1.9.0. If a future re-pin
ever needs "newest", it must use `sort -V` or the releases API's
`releases/latest`; this plan adds no such code and no test for it.

**Resolution procedure, corrected.** `git/ref/tags/<name>` with a slash in the
name is a prefix match and returns garbage. Use `git/matching-refs/tags/<name>`
and select the exact ref, or URL-encode. Annotated tags (`astral-sh/setup-uv@v7`
is one; the pypa release tags all are) need a second dereference through
`git/tags/<sha>`. 8 of the 10 distinct actions use lightweight tags.
Equivalent and simpler: `git ls-remote https://github.com/<repo> refs/tags/<ref>
'refs/tags/<ref>^{}' refs/heads/<ref>` — the `^{}` line is the peeled commit
when the tag is annotated; take it when present, else the plain line. The
verifier below uses this form because it handles tags and branches uniformly.

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
- The composite action's `setup-node@v4` — modify — pin to **the commit `v4`
  resolves to today** (`49933ea…` = **v4.4.0**, re-resolved at implementation),
  comment `# v4.4.0`. **Not** v7: that would be a major-version bump inside a
  job holding `id-token: write` and `contents: write`, contradicting the
  Non-goals. **WAS WRONG (rev 2–4.1):** bumped to v7. Dependabot's new entry
  for this directory (below) will propose v7 as its own reviewable PR.

### `.github/dependabot.yml` (modify)

- Add a second `github-actions` entry with
  `directory: "/.github/actions/pipeline-bootstrap-setup"`. **WAS WRONG (rev 1):**
  "no change" — Dependabot demonstrably does not scan that path here.

### `scripts/check_workflows.py` (modify)

- `EXPECTED_USES` (`:59`) — modify — to the **SHA form without comments**.
  **WAS WRONG (rev 1):** it put comments in; they can never match parsed YAML.
- `all_uses_are_sha_pinned(paths)` — add — **operates on raw text, not parsed
  YAML**, so it can see the comment. Scan `.github/workflows/*.yml` **and**
  `*.yaml`, and `.github/actions/**/action.yml` **and** `action.yaml`.
  **WAS WRONG (rev 2):** `*.yml` / `action.yml` only. GitHub runs both
  extensions, `workflow_files()` (`check_workflows.py:~533`) already globs both
  *with a docstring saying why*, and renaming the composite to `action.yaml`
  would have kept it executable while removing it from the guard.
  Skip **only** a value starting `./` (same-repository path). Otherwise require
  `^\s*-?\s*uses:\s*[\w.-]+/[\w./-]+@[0-9a-f]{40}\s+#\s*\S+`, which admits
  subdirectory actions and **remote reusable workflows**
  (`owner/repo/.github/workflows/x.yml@<sha>`).
  **WAS WRONG (rev 2):** it exempted any value containing `.yml@`/`.yaml@`. That
  let `owner/repo/.github/workflows/build.yml@main` — mutable remote code — pass
  the guard. Reusable workflows are SHA-pinnable and GitHub documents SHA as the
  safest form. Reason text names file, line, and ref.
- **Call it from `main()`.** **WAS WRONG (rev 1):** unstated. The evidence loop
  runs the CLI, not pytest; a tests-only function lets the new mutants survive.
- Module docstring (`:22`) — modify — state the convention, and that comments are
  checked on raw text because the parser drops them.

### `scripts/_mutate_workflow.sh` (modify)

- **All three** `@release/v1` anchors — modify — to the pinned string:
  `continue-on-error-step`, `if-on-publish-step`, `forked-action`.
  **WAS WRONG (rev 1):** only `forked-action`.
- `tag-pinned-action` — add — reverts the `setup-node` pin in `test.yml`'s
  `install-smoke` job to `@v7`. **WAS WRONG (rev 2):** "use the step context" —
  the three-line `setup-node` block is **byte-identical** in the `test` and
  `install-smoke` jobs (`test.yml:38-40` and `:81-83`), so a step-level anchor
  matches twice and `replace()` refuses it. The anchor must include a line that
  exists only in one job (the `install-smoke` job header, or its preceding
  unique step). Fails via the new invariant.
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
  with comment **passes**; a job-level reusable workflow that is a `./` same-repo
  path is **skipped**, and a **remote** one (`owner/repo/.github/workflows/x.yml@…`)
  is **required to be SHA-pinned** — `@main` fails, `@<sha> # vX` passes;
  a subdirectory action `owner/repo/path@<sha> # v1` **passes**; a `docker://`
  `uses:` is reported (it is not pinnable by this scheme — decide: skip with a
  named reason, or fail; there are none today, so pin the decision by test).
- `TestMutationHelperContract` gains the **five** new mutants
  (`tag-pinned-action`, `sha-comment-dropped`, `sha-moved`, `pypa-rolled-back`,
  `composite-tag-pinned`) via its bidirectional check — the existing contract
  (`test_workflow_guards.py:~752`) requires exact set equality with `--list`, so
  all five must be listed there. **WAS WRONG (rev 2–4.1):** said four.

### `.consiliency/evidence/mutation-217.md` (create)

- The matrix, with per-mutant **helper exit, commit hash, checker exit, reason
  tag**. Include a row proving the three re-anchored mutants still mutate (their
  commit hash differs from base).

## Documentation impact

- `CHANGELOG.md` — add — `### Changed`: every action is SHA-pinned (name the
  count, and that the PyPI publish action is pinned to v1.14.2, the commit
  `release/v1` already pointed at — so nothing runs differently there). State
  plainly that **every** pin, the composite's `setup-node@v4` → v4.4.0 included,
  is the commit the mutable ref resolved to on the day — **no action runs a
  different version after this change**. Reference as "see #217", never a
  closing keyword. **WAS WRONG (rev 2):** a bullet announcing a 144-commit
  rollback; **(rev 2–4.1):** a bullet announcing a v4 → v7 bump.
- `SECURITY.md` — add — a short supply-chain paragraph stating the pin
  convention and that Dependabot maintains it. **WAS WRONG (rev 1):** it pointed
  at `:39`, which is the JWKS sentence, not a release-path section.

## Dependencies & order

1. `all_uses_are_sha_pinned` on raw text, wired into `main()`, with tests —
   **RED against unchanged `main`, reporting 30 refs.** The guard must see the
   problem before anything is pinned.
2. Resolve every SHA with the corrected procedure (from the refs on
   `origin/main`, never from a "newest" pick); record the resolution output.
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

# Every pinned SHA must (a) resolve to the release its trailing comment names
# AND (b) equal the commit the ORIGINAL mutable ref (as it stood on origin/main
# before pinning) resolves to today. (b) is the "no behaviour change" proof;
# (a) alone accepts an old-but-real release with its matching SHA.
# **WAS WRONG (rev 4/4.1):** promised (b) in a comment, coded only (a).
# Aggregate exit. NOT `grep | while` -- under bash that loop is a subshell and
# `fail=1` is lost. **WAS WRONG (rev 2)**: exactly that.
resolve() {  # resolve <owner/repo> <ref>  -> peeled commit sha, or empty
  git ls-remote "https://github.com/$1" "refs/tags/$2" "refs/tags/$2^{}" "refs/heads/$2" 2>/dev/null \
    | awk -v want="$2" '$2=="refs/tags/"want"^{}"{p=$1} $2=="refs/tags/"want||$2=="refs/heads/"want{c=$1} END{print (p!=""?p:c)}'
}
fail=0
# (b): the multiset of (repo, resolved-commit) over the PRE-PIN refs on
# origin/main must equal the multiset of (repo, pinned-sha) in the working
# tree. Keyed per LINE, not per repo: `actions/setup-node` is `@v4` in the
# composite and `@v7` in the workflows, and a per-repo map would take whichever
# came first. **WAS WRONG (rev 5 draft):** per-repo map.
files=$(git ls-tree -r --name-only origin/main .github/ | grep -E '^\.github/(workflows/[^/]+|actions/.*/action)\.ya?ml$')
expected=$(for f in $files; do git show "origin/main:$f"; done \
  | grep -E "uses:\s*[^./ ][^ ]*@" | grep -v "uses:\s*\./" \
  | sed -E 's/.*uses:\s*//; s/\s*#.*//; s/\s+$//' \
  | while IFS= read -r ref; do repo=${ref%@*}; c=$(resolve "$repo" "${ref#*@}"); \
      [ -n "$c" ] || { echo "UNRESOLVED $ref" >&2; echo "UNRESOLVED@$ref"; }; echo "$repo@$c"; done | sort)
n=0; actual=""
while IFS= read -r line; do
  n=$((n+1))
  ref=$(echo "$line" | sed -E 's/.*uses:\s*//; s/\s*#.*//'); tag=$(echo "$line" | sed -E 's/.*#\s*//; s/\s.*//')
  repo=${ref%@*}; sha=${ref#*@}
  a=$(resolve "$repo" "$tag")                 # (a) comment tag -> peeled commit
  if [ "$a" = "$sha" ]; then echo "OK(a)    $ref  # $tag"; else echo "MISMATCH(a) $ref  # $tag -> ${a:-?}"; fail=1; fi
  actual="$actual$repo@$sha"$'\n'
done < <(grep -rhE "uses:\s*[^./ ][^ ]*@[0-9a-f]{40}\s*#" .github/workflows/*.y*ml .github/actions/*/action.y*ml)
echo "checked $n pinned refs (expect 30)"; [ "$n" -eq 30 ] || fail=1
# (b): multiset equality. Any line here is a pin that is not the commit its
# original ref resolves to today (or an original ref that resolved to nothing).
# printf '%s\n' on the left: $(...) strips the trailing newline, `| sort` on the
# right keeps it, and diff then flags the last line on a clean tree.
if d=$(diff <(printf '%s\n' "$expected") <(printf '%s' "$actual" | sort)); then echo "OK(b)    all $n pins equal their original refs' commits"
else echo "MISMATCH(b):"; echo "$d"; fail=1; fi
exit "$fail"

# Prove the loop FAILS CLOSED, both branches: (1) alter one SHA digit in a copy
# -> MISMATCH, exit 1; (2) set a line to v1.9.0's real SHA with comment
# `# v1.9.0` -> (a) passes, (b) MISMATCHes, exit 1. Thirty OKs prove nothing
# about the mismatch branch until it has been seen to fire.

uv run ruff check src/ tests/ scripts/ && uv run mypy src/
```

Edge cases: annotated vs lightweight tags (both present); a `uses:` at job level
for a reusable workflow (none today — the **remote-must-pin / `./`-skip** split is
pinned by test, not a blanket skip); `docker://` refs
(none today — decision pinned by test); the composite action's `runs:` block
must not be mistaken for a step list.

## Acceptance criteria

- [ ] `all_uses_are_sha_pinned` is **RED against unchanged `main`** and reports
      **30** refs across `.github/workflows/` and the composite action. A guard
      that starts green has not seen the problem. Count is 30, not 29:
      rev 1 excluded the composite's ref.
- [ ] After pinning, the checker exits 0, and the resolution loop reports
      **30 OK, 0 MISMATCH** — and, run against a copy with one SHA altered by a
      digit, **exits 1**. Both directions. **WAS WRONG (rev 2):** the loop was a
      `grep | while` subshell, so `fail=1` was lost and the exit was always 0;
      the criterion demanded fail-closed but the script could not deliver it.
- [ ] For **every** pinned ref, the pinned SHA equals the **peeled** commit that
      the original mutable ref (`v4`, `v7`, `release/v1`, …) resolves to at
      implementation time — recorded per ref by the verifier's check (b), which
      reads the original refs from `origin/main`. This is the "no behaviour
      change" proof, and it is the check that rejects a correct-looking rollback
      such as `pypa-rolled-back` (proven by running it against that mutant and
      seeing MISMATCH). **No exceptions**: the composite's `setup-node` pins to
      v4.4.0, the commit `v4` resolves to. **WAS WRONG (rev 2–4.1):** a
      "declared exception" bumping it to v7.
- [ ] The pypa pin is `>= 1.13.0` (GHSA-vxmw-7h4f-hqxh) — asserted numerically.
- [ ] The three re-anchored mutants (`continue-on-error-step`,
      `if-on-publish-step`, `forked-action`) each **still mutate** — evidence
      shows a commit hash differing from base — and still exit non-zero. Rev 1
      would have left all three as silent survivors.
- [ ] The **five** new mutants (`tag-pinned-action`, `sha-comment-dropped`,
      `sha-moved`, `pypa-rolled-back`, `composite-tag-pinned`) each exit
      non-zero as committed scratch-branch mutants, with the same helper-exit +
      commit-hash evidence.
- [ ] A correctly pinned ref with comment passes; a `./` local path is skipped;
      a subdirectory action passes; a **remote reusable workflow** pinned to a SHA
      passes and one pinned to `@main` **fails**; an `action.yaml` composite is
      **scanned**. All by synthetic fixture. **WAS WRONG (rev 2):** reusable
      workflows were exempted wholesale.
- [ ] `EXPECTED_USES` contains **no comments** and matches the pinned
      `release.yml` — proven by the checker exiting 0 on it, which rev 1's form
      could never have done.
- [ ] The pypa pin is **v1.14.2**'s commit **and equals the `release/v1` branch
      head at implementation time** — asserted by resolving both and comparing,
      so the "no behaviour change" claim is proven rather than assumed. If they
      have diverged by then, stop and re-decide; do not silently pick one.
- [ ] No code or script added by this change sorts or ranks version strings;
      every pin is derived from the original ref's resolved commit (`grep -n
      "sort\|newest\|latest" scripts/ tests/test_workflow_guards.py` shows no
      new version-ranking logic). **WAS WRONG (rev 4/4.1):** demanded a
      "resolution script" with a `sort -V` test that no Changes entry created.
- [ ] `dependabot.yml` has the composite-action directory entry.
- [ ] Full suite green.

## Non-goals

- Verifying SHA currency inside the CI guard — a network call on the merge path.
  Dependabot owns currency; the guard owns form.
- Pinning `python:3.12-slim` in the `Dockerfile` by digest. Same class, different
  ecosystem, its own issue.
- Changing what the composite action *does*; only its one `uses:` is pinned.

## Execution Policy

- execute: effort=medium, reason=mechanical across eight files but the three mutant anchors and EXPECTED_USES must land atomically with the release.yml pins; every pin including pypa v1.14.2 and the composite setup-node v4.4.0 is the commit its mutable ref resolves to today so nothing runs differently
