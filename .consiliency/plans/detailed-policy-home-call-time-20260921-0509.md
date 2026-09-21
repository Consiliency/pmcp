# Detailed plan: resolve the operator policy home at call time, keeping the allowlist seam (see #262)

## Task

Consiliency/pmcp#262 (see #262; sibling of #261, under the #235 hermeticity
umbrella). `src/pmcp/policy/policy.py` builds `USER_POLICY_PATHS` and
`DEFAULT_POLICY_PATHS` at module scope, so `Path.home()` is frozen at import.
The resolvers `_default_policy_paths()` / `_user_policy_paths()` read the module
attributes at call time (the #202 cwd fix) but the home component is already
baked in, so the suite's autouse `HOME` redirect (`tests/conftest.py:130`
`isolate_trust_store`) cannot reach it, and a re-homed production process has
the same blind spot `config/loader.py:66-70` documents for its own constant.
Mirror the call-time precedent in `config/loader.py` and `manifest/loader.py`
**without** weakening the allowlist: `USER_POLICY_PATHS` is the ungated set
(IF-0-CONSENT-2) *and* a documented monkeypatch seam, and a fix that recomputed
the home while ignoring a patched attribute would make the developer's real
`~/.claude/gateway-policy.*` ungated inside the very trust-boundary tests that
guard S-11.

## Execution Policy
- execute: effort=high, reason=trust-boundary allowlist seam; two properties (patch honoured verbatim, home resolved at call time) must hold simultaneously and a naive fix fails open

## Research summary

Verified against `main` @ `11d7a8e` in this worktree; every `file:NNN` below was
read this run. The lead's description of the defect is accurate: the constants
at `policy.py:83-88` freeze `Path.home()`, and the only consumer of both
resolvers is `PolicyManager._discover_policies` (`policy.py:167` and `:171`),
so no signature changes reach the 32 policy importers.

**The precedent.** `config/loader.py:66-83` keeps `DEFAULT_USER_CONFIG_PATHS`
as documentation with a "Do NOT read this frozen list at runtime" comment and
adds `default_user_config_paths()`; `manifest/loader.py:672-686`
(`_overlay_manifest_paths`) computes `Path.home() / ".pmcp" / "manifest.yaml"`
at call time. Neither constant is patched by any test (`grep` over `tests/`
finds no `DEFAULT_USER_CONFIG_PATHS` / `DEFAULT_USER_MANIFEST_PATHS` patch), so
neither precedent had to solve the seam problem policy has. Policy cannot copy
them verbatim.

**The seam.** Ten patch sites, all `monkeypatch.setattr` replacements (no
in-place `append`/`clear`, no `==`/`isinstance` reads of the constants anywhere
in `src/` or `tests/`): both lists patched together at
`tests/test_policy_fail_open.py:66-67`,
`tests/test_policy_package_identifiers.py:72-73`,
`tests/test_project_source_consent_policy.py:71-73`,
`tests/test_trust_boundaries_composition.py:241-242`,
`tests/test_trust_boundaries_e2e.py:232-233` and `:982-985`; **`DEFAULT` alone**
at `tests/test_policy_fail_open.py:61`, `tests/test_scoped_advisor_audit.py:116`,
`tests/test_trust_boundaries_e2e.py:874` and `:959`. No test patches `USER`
alone. Two existing tests pin the "read at construction, not import" contract
for cwd: `test_default_paths_follow_the_cwd_at_construction`
(`tests/test_policy_fail_open.py:405`) and
`test_default_policy_paths_stays_a_patchable_module_attribute` (`:431`); the new
tests belong beside them.

**Why `DEFAULT_POLICY_PATHS` needs the same treatment.** It splices the frozen
user entries at import. If only `USER_POLICY_PATHS` became call-time, then with
nothing patched and `HOME` changed, the search list would still contain the
*old* home's entries (not in the live user set, so classed project-scoped and
sent through the consent gate — the operator's own file refused with a
`pmcp trust approve` prompt) and would not contain the *new* home's entries at
all. Prototyped this run as mutation M3 below: the red-on-main test stays red.

**Prototype evidence (this run, then reverted).** The mechanism below was
applied to this worktree and (a) `ruff check`, `ruff format --check` and
`mypy src/pmcp` were clean; (b) the seven seam-using files + `test_policy_fail_open.py`
passed (90 tests); (c) the three new tests were run against `main` and against
four named mutations — the table under *Acceptance criteria* is measured, not
predicted. The full suite with the prototype in place: `4027 passed, 3 skipped, 25
deselected in 576.58s`, exit 0 (this host, this run). All five mutations M1-M5
were RUN, not argued; results are quoted per criterion.

**SECURITY.md.** No claim in the trust-model region cites user-policy path
discovery; `C-08` covers project sources only. `python3
scripts/check_security_claims.py` is `OK … 123 cited node id(s)` on this tree
and no claim text changes; it runs as a guard, not as a changed surface.

## Frozen-contract region touched

`policy.py:73-82` is the IF-0-CONSENT-2 contract comment and is kept verbatim:

```
#: The operator-supplied locations. Membership here -- not absoluteness, and not
#: "somewhere under `$HOME`" -- is what makes a discovered path ungated
#: (IF-0-CONSENT-2, Consiliency/pmcp#230). Both rejected alternatives fail open: a checkout
#: lives at an absolute path once discovery resolves it, and checkouts routinely
#: live under `$HOME` (`~/code/...`), so either rule would hand a repository file
#: the operator's trust -- S-11 through the back door.
#:
#: Anything discovered that is *not* one of these is project-scoped and must pass
#: the consent gate. That direction is the fail-closed one: an unrecognised entry
#: is treated as repository-supplied, never as operator-supplied.
```

**No new vocabulary is introduced and the gated/ungated decision rule is
unchanged**: a discovered path is ungated iff it is a member of the user list
*in force* (the patched attribute verbatim, else the two `~/.claude` names
under the live home); everything else is project-scoped and goes through
`read_and_gate`. What changes is only *when* `Path.home()` is read for the
unpatched case. `_discover_policies` (`policy.py:150-187`) is not edited.

## Mechanism: identity-sentinel defaults

Keep both public attributes, same names, same documented values, same
`monkeypatch.setattr` seam. Retain a private reference to the *original*
object of each; a resolver that finds the attribute `is` that original treats
it as "unpatched: compute from the live `Path.home()`"; anything else is the
caller's exact list. Identity, not equality, so a patched list that happens to
equal the frozen one is still honoured as the caller's. The frozen defaults are
**tuples** so the seam is replace-only: an in-place mutation would leave the
identity intact and be silently ignored, and a tuple turns that into an
immediate `AttributeError` instead. The search list, when unpatched, is
*derived from* the effective user list — so a user entry can never be
searched-but-unrecognised (which would gate the operator's own file) or
recognised-but-unsearched.

| `USER` | `DEFAULT` | user set (ungated) | search list | who does this |
|---|---|---|---|---|
| untouched | untouched | live home ×2 | project names + live home ×2 | production; T1 |
| patched | patched | patched verbatim | patched verbatim | the six paired sites; T2 |
| untouched | patched | live home ×2 | patched verbatim | the four `DEFAULT`-only sites (temp paths never match; identical outcome to today) |
| patched | untouched | patched verbatim | project names + patched user | no existing site; T3 pins it |

Rejected: (a) `USER_POLICY_PATHS = None` sentinel — loses the documented value
and the `[*PROJECT, *USER]` composition, and changes the attribute's type for
readers; (b) a plain factory that ignores the attribute (the config precedent)
— fails open in the paired-patch tests, proven by mutation M1; (c) making only
`USER` call-time — mutation M3.

## Changes

### `src/pmcp/policy/policy.py` (modify)
- import — add `from collections.abc import Sequence` — the attributes must admit both the frozen tuple and a patched list.
- `_USER_POLICY_TAILS` — add — the two home-relative tails (`.claude/gateway-policy.yaml`, `.json`) as the single source both the factory and the frozen default derive from, so the names are not duplicated the way `config/loader.py:72-83` duplicates its own.
- `default_user_policy_paths()` — add (public, mirrors `default_user_config_paths`) — `[Path.home() / tail for tail in _USER_POLICY_TAILS]`, resolved at call time; docstring cites Consiliency/pmcp#262 and the config precedent.
- `USER_POLICY_PATHS` — modify — `USER_POLICY_PATHS: Sequence[Path] = tuple(default_user_policy_paths())`, followed by `_FROZEN_USER_POLICY_PATHS = USER_POLICY_PATHS`. Add the "Do NOT read this frozen tuple at runtime" comment in the `config/loader.py:66-70` style, stating the identity rule and why it is a tuple. The IF-0-CONSENT-2 block above it is untouched.
- `DEFAULT_POLICY_PATHS` — modify — `DEFAULT_POLICY_PATHS: Sequence[Path] = (*PROJECT_POLICY_PATHS, *USER_POLICY_PATHS)`, followed by `_FROZEN_DEFAULT_POLICY_PATHS = DEFAULT_POLICY_PATHS`, with a two-line comment stating the same contract.
- `_resolve_against_cwd` — modify — parameter type `list[Path]` → `Sequence[Path]`; body unchanged.
- `_effective_user_policy_paths()` — add — `return default_user_policy_paths() if USER_POLICY_PATHS is _FROZEN_USER_POLICY_PATHS else USER_POLICY_PATHS`; docstring: identity, not equality.
- `_default_policy_paths()` — modify — when `DEFAULT_POLICY_PATHS is _FROZEN_DEFAULT_POLICY_PATHS`, resolve `[*PROJECT_POLICY_PATHS, *_effective_user_policy_paths()]`; otherwise resolve the attribute verbatim (today's behaviour). Extend the docstring with the "derived from the allowlist" sentence.
- `_user_policy_paths()` — modify — `set(_resolve_against_cwd(_effective_user_policy_paths()))`; existing docstring kept.
- `PROJECT_POLICY_PATHS` — unchanged (cwd-relative by design, never patched).

### `tests/test_policy_fail_open.py` (modify)
Add three tests under the existing `# === Path.cwd() is read at construction, not at import ===` section (`:402`), directly after `test_default_policy_paths_stays_a_patchable_module_attribute` (`:431`). Add a module-level helper `_home_with_policy(tmp_path_factory, name, policy) -> Path` that `mktemp`s a home, creates `.claude/`, writes `gateway-policy.yaml` and returns the home; the fake homes are `tmp_path_factory.mktemp` siblings (never under `tmp_path`, per `conftest.py:130-181`'s measured reason). All three assert *enforcement* (`is_server_allowed`, `get_max_tools_per_server`), per the file's own rule at `:46-48`, and assert on `caplog` WARNING records so a file that was discovered-then-refused is distinguishable from one that was never discovered.

- `test_user_policy_paths_follow_home_at_construction` — add — **the red-on-`main` test.** Patches neither list (docstring says so, in the style of `:412-417`). Two fake homes, each with a distinct policy (`deny-in-a`; `_VALID_POLICY`). `monkeypatch.setenv("HOME", first)` → `PolicyManager()` → `setenv("HOME", second)` → `PolicyManager()`. Assert the first manager denies `deny-in-a`, the second denies `deny-me` with `max_tools_per_server == 7` and allows `deny-in-a`, and **no WARNING was logged** (no consent refusal: both files were classed *user*). The two constructions in one process are the re-homed-process case the issue names. The autouse `isolate_cwd` (`conftest.py:182`) guarantees no project file is in the cwd.
- `test_a_patched_user_policy_list_is_honoured_over_the_live_home` — add — **the anti-naive-fix test (property 1).** Fake home with `_VALID_POLICY` at `.claude/gateway-policy.yaml`, `HOME` pointed at it, then `USER_POLICY_PATHS = []` and `DEFAULT_POLICY_PATHS = [that file]`. The file *is* searched but is *not* in the patched allowlist, so it must be project-scoped: assert `deny-me` is allowed, the limit is not 7, and **exactly one** WARNING whose text contains `str(policy_file)` (the consent refusal). Do NOT patch `DEFAULT` to `[]` — with an empty search list nothing is discovered under any implementation and the test cannot fail (measured this run: that first draft stayed green under mutation M1).
- `test_the_search_list_derives_from_the_patched_user_list` — add — **the `DEFAULT`-derivation test.** Same fake home and `HOME`; patch `USER_POLICY_PATHS = []` only. Assert `deny-me` is allowed and **no** WARNING: the live-home file must be neither loaded nor refused, because an unpatched search list is built from the patched (empty) user list, not from the live home.

### `tests/test_policy_package_identifiers.py` (modify)
- `_manager` docstring at `:57-59` — modify — it states `USER_POLICY_PATHS` "is computed from the home directory at import time, before the autouse HOME redirect", which becomes false. Reword to: the explicit policy skips discovery, so these tests never depend on the discovery seam at all. No code change.

### `CHANGELOG.md` (modify)
- `## [Unreleased]` → existing `### Fixed` heading (search for `### Fixed` under Unreleased; do not add a second one) — add — one bullet in the file's bold-lead style: the operator's `~/.claude/gateway-policy.{yaml,json}` locations are now resolved when a `PolicyManager` is built rather than when `pmcp.policy.policy` is imported, so a `HOME` changed after import (the test suite's isolation, or a re-homed process) is respected; a monkeypatched `USER_POLICY_PATHS` / `DEFAULT_POLICY_PATHS` is still honoured verbatim. Link `[#262](https://github.com/Consiliency/pmcp/issues/262)` with "see", never a closing keyword.

## Documentation impact
- `CHANGELOG.md` — modify — as above.
- `README.md` — none — `:1379` documents *where* the file lives, not when it is read; still accurate.
- `SECURITY.md` — none — no claim cites user-policy discovery (`C-08` is project sources; `C-13` is composition). The checker runs as a guard in Verification.
- `src/pmcp/provision_gate.py:347-361` (user-facing strings naming `~/.claude/gateway-policy.yaml`) — none — path unchanged.

## Dependencies & order
1. Write the three tests first and run them on the untouched tree: **T1 must be red** (`assert True is False` at the `deny-in-a` assertion), T2 and T3 green. That run is the evidence for AC-1; capture its output in the PR body.
2. Apply the `policy.py` change. All three green; the four mutations in AC-2..AC-4 each turn the named test red.
3. Docstring, CHANGELOG. No migration, no external dependency, no signature reaching the 32 importers (`_discover_policies` is the only caller of both resolvers).

## Verification
`automation.suite_command`: `uv run pytest -q -p no:cacheprovider`

1. **Red on `main` (AC-1)** — with `src/pmcp/policy/policy.py` unchanged and the three tests added: `uv run pytest tests/test_policy_fail_open.py -q -p no:cacheprovider --cov-fail-under=0 -k "follow_home or honoured_over_the_live_home or derives_from_the_patched"` → `1 failed, 2 passed`, the failure being `test_user_policy_paths_follow_home_at_construction`. Then with the fix → `3 passed`. If the implementer applies the fix first: `git stash push -- src/pmcp/policy/policy.py`, run, `git stash pop`.
2. **Mutations (AC-2..AC-4)** — apply each by hand to the fixed `policy.py`, run the same command, revert with `git checkout -- src/pmcp/policy/policy.py` *only after* confirming the tests are the sole uncommitted change (see the "revert from a saved copy" gotcha in the handoff):
   - M1 — `_effective_user_policy_paths()` returns `default_user_policy_paths()` unconditionally → T2 **and** T3 fail (measured).
   - M2 — the unpatched branch of `_default_policy_paths()` splices `default_user_policy_paths()` instead of `_effective_user_policy_paths()` → T3 fails (measured).
   - M3 — delete the unpatched branch of `_default_policy_paths()` (read the frozen attribute) → T1 fails (measured).
   - M4 — `_user_policy_paths()` reads `USER_POLICY_PATHS` directly → T1 fails (measured).
3. **Seam files unchanged in behaviour** — `uv run pytest tests/test_policy_fail_open.py tests/test_project_source_consent_policy.py tests/test_policy_package_identifiers.py tests/test_trust_boundaries_composition.py tests/test_trust_boundaries_e2e.py tests/test_scoped_advisor_audit.py -q -p no:cacheprovider --cov-fail-under=0` → all pass (90 on the prototype). Red-turning mutation, **measured**: M5 — make `_default_policy_paths()` always take the unpatched branch (ignore a patched `DEFAULT_POLICY_PATHS`) → `15 failed, 7 passed` across `tests/test_policy_fail_open.py` + the new tests, including `test_default_policy_paths_stays_a_patchable_module_attribute`, `test_valid_discovered_policy_loads_and_enforces` and T2. Note: `tests/test_scoped_advisor_audit.py` stayed green under M5 (its assertion holds whether or not the malformed file is searched), so do not cite it as M5's red test.
4. **Type and lint gate** — `uv run mypy src/pmcp` (`Success: no issues found in 49 source files` on the prototype), `uv run ruff check src/ tests/`, `uv run ruff format --check src/ tests/`. Red-turning mutation: annotate `USER_POLICY_PATHS: list[Path]` while assigning the tuple → mypy error.
5. **Claim ledger guard** — `python3 scripts/check_security_claims.py` → `OK … 123 cited node id(s)`. Red-turning mutation: rename any cited test in a touched file (e.g. `test_project_redaction_patterns_extend_rather_than_replace_defaults`) → `FAIL R8`. Nothing in this plan renames one.
6. **Full suite** — `uv run pytest -q -p no:cacheprovider` shows no new failures. Same-host result with the prototype applied, this run: `4027 passed, 3 skipped, 25 deselected`, exit 0. Red-turning mutation: any of M1-M5 (each reddens at least one collected test above).
7. **Untouched by design** — `git diff --stat` shows no change to `tests/conftest.py`, `src/pmcp/policy/policy.py::_discover_policies`, or the IF-0-CONSENT-2 comment block (`git diff -U0 src/pmcp/policy/policy.py | grep -c "IF-0-CONSENT-2"` → `0`).

## Implementation notes carried from panel review

- Annotate `_effective_user_policy_paths()` as returning `Sequence[Path]`, so
  the unpatched `tuple[Path, ...]` and a monkeypatched `list[Path]` unify under
  mypy without a cast (gemini and grok, independently).
- `_default_policy_paths()` must keep passing its result through
  `_resolve_against_cwd(...)`, i.e. resolve
  `[*PROJECT_POLICY_PATHS, *_effective_user_policy_paths()]` — otherwise the
  relative PROJECT entries stop being resolved against `Path.cwd()`, which is
  the regression Consiliency/pmcp#202 fixed (gemini).

## Acceptance criteria
- [ ] **AC-1 (call-time home; red on today's `main`).** `test_user_policy_paths_follow_home_at_construction` fails on `main` @ `11d7a8e` at its first assertion (`assert under_first.is_server_allowed("deny-in-a") is False` → `assert True is False`) and passes with the fix. Red-turning mutations against the fixed tree: M3 or M4 (either half of the resolver reading its frozen attribute).
- [ ] **AC-2 (patched allowlist honoured verbatim — nothing added).** `test_a_patched_user_policy_list_is_honoured_over_the_live_home` passes: with `HOME` holding a real policy file and `USER_POLICY_PATHS` patched to `[]`, that file is classed project-scoped and refused with exactly one consent warning naming it. Red-turning mutation: M1 (the naive fix) — the file becomes ungated and `deny-me` is denied.
- [ ] **AC-3 (search list derives from the effective allowlist).** `test_the_search_list_derives_from_the_patched_user_list` passes: `USER_POLICY_PATHS = []` with `DEFAULT_POLICY_PATHS` untouched discovers nothing and logs nothing. Red-turning mutations: M1 or M2.
- [ ] **AC-4 (existing seam preserved).** The ten patch sites listed in *Research summary* are unmodified and the six files pass; `test_default_policy_paths_stays_a_patchable_module_attribute` passes. Red-turning mutation (M5, measured this run): make `_default_policy_paths()` ignore a patched attribute (always take the unpatched branch) → `15 failed` in `tests/test_policy_fail_open.py` (14 existing, incl. that test) plus T2.
- [ ] **AC-5 (gates).** `mypy src/pmcp`, `ruff check`, `ruff format --check` and `scripts/check_security_claims.py` are clean, and `SECURITY.md` is unchanged. Red-turning mutation: the `list[Path]` annotation in Verification 4.
