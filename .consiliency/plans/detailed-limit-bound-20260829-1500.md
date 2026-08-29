# Detailed plan: bound `max_tools_per_server`, and stop the code arguing against it

> **Revision 2 (2026-08-29).** Boarded 3 AGREE, no blocking defects. Three
> corrections applied, one of them a **misattributed citation of mine** that
> argued the opposite of what the source says.

## Task

Close Consiliency/pmcp#207. Add `Field(ge=1)` to
`LimitsPolicy.max_tools_per_server`, and correct three comments that state the
reason it was omitted — a reason #202 removed.

## Research summary

Verified in this worktree.

**Why the bound was blocked, and why it no longer is.** #175 proposed it, then
dropped it: `PolicyManager._load_policy(..., fatal=False)` swallowed validation
errors and fell back to an **allow-all** default, so making `0` schema-invalid
would have silently discarded an operator's whole policy file. #202 made a
discovered policy that *parses but fails validation* terminate startup, so a
schema bound can no longer silently unrestrict anything.

**`0` is not a meaningful setting.** At `0`, `_parse_tool_entries` empties its
result before examining a single entry, and no documented semantic assigns `0` a
"disable tool indexing" meaning.

**WAS WRONG (rev 1):** it supported this by citing `manager.py:107` — "the cap is
a runaway guard, not a catalog-size limit". That sentence is about
`_MAX_LISTING_PAGES = 500`, **not** `max_tools_per_server`, and the same passage
describes `max_tools_per_server`'s default of 100 as a *legitimate catalog size*
— the opposite of the point it was quoted for. The conclusion survives on
`_parse_tool_entries`; the citation does not, and is removed rather than
reworded.

**Two independent axes.** `LimitsPolicy.max_tools_per_server` (`types.py:960`) is
the policy/schema surface. `ClientManager.__init__`'s `max_tools_per_server: int = 100`
(`manager.py:936`) is a separate constructor parameter, and
`manager.py:2169` guards `< 1` — so it already handles zero *and negatives*
defensively. #207 is about the schema axis; the constructor axis is deliberately
left alone (see Non-goals).

**What currently pins `0` as valid.** `tests/test_client_manager.py:6110`
asserts `LimitsPolicy(max_tools_per_server=0).max_tools_per_server == 0`, and
`:6116` constructs `ClientManager(max_tools_per_server=0)`. The first must
invert; the second must **stay**, because it exercises the constructor axis and
the log behaviour, both of which survive this change.

## The migration consequence, stated plainly

**This change plus #202 converts a working configuration into a hard boot
failure.** An operator whose discovered policy contains `max_tools_per_server: 0`
starts fine today (with a now-accurate log saying nothing was indexed); after
this, that file fails validation and #202 terminates startup.

That is the intended direction — a nonsensical value should be rejected loudly
rather than silently indexing nothing — but it is a real behaviour change for
anyone holding that value, and it must be in the CHANGELOG under `### Changed`,
not buried as a fix. The affected population is plausibly zero, since `0` yields
a gateway that indexes no tools at all, but "plausibly zero" is not "none".

## Changes

### `src/pmcp/types.py` (modify)

- `LimitsPolicy.max_tools_per_server` (`:973` — rev 1 said `:960`, which is the
  docstring) — modify — `Field(default=100, ge=1)`.
- `LimitsPolicy` docstring (`:957-970`) — modify — it currently says the field
  **deliberately carries no `Field(ge=1)`** and explains why, citing the
  swallow-and-fall-back-to-allow-all behaviour. That reason is now false. Replace
  it with a short record that the bound was blocked until #202 made discovered
  policy validation fatal. Keeping the history matters: without it, the next
  person to consider a schema bound has no way to know the hazard existed or that
  it was removed.

### `src/pmcp/client/manager.py` (modify)

- The comment at `:2174-2177` — modify — same correction. It currently justifies
  the missing bound by the fail-open.
- The comment at `:683-687` — modify — it tells the operator to "fix their policy
  file", which after the bound is unreachable via a policy file; that path is
  constructor-only.
- `:2169`'s `< 1` guard and the "so none were indexed" log at `:690` —
  **unchanged**. They remain reachable through the constructor axis, and the log
  fix from #175 is correct independently of whether the schema accepts `0`.

### `tests/test_client_manager.py` (modify)

- `:6110` — modify — invert: `LimitsPolicy(max_tools_per_server=0)` must now
  raise `ValidationError`. Also assert `-1` raises, since `ge=1` covers it and
  the old bound covered neither.
- `:6116` and the rest of `TestZeroLimitLogsAccurately` — **keep**. They drive
  `ClientManager(max_tools_per_server=0)` directly, which is still legal, and
  they pin the accurate-log behaviour. Deleting them because "0 is now invalid"
  would remove coverage of a path that still exists.
- The class docstring at `:6066-6075` — modify — it explains the log fix by
  reference to a policy file setting `0`, which is no longer possible. Reframe to
  the constructor axis so the test's stated reason matches its actual mechanism.

### `tests/test_policy_fail_open.py` or a sibling (modify/create)

- Add the end-to-end consequence: a **discovered policy file** containing
  `max_tools_per_server: 0` now makes `PolicyManager()` raise **`ValueError`** —
  `PolicyManager` wraps pydantic errors, so this test must follow
  `test_policy_fail_open.py`'s existing `ValueError` / `Invalid policy file`
  pattern and its `_discovery_paths` helper. Only the schema-level test in
  `test_client_manager.py` sees a raw `ValidationError`, and it needs the pydantic
  import. This is the
  interaction between #207 and #202, and neither issue's tests cover it alone —
  #207's tests are schema-level and #202's use a different invalid key.

## Documentation impact

- `CHANGELOG.md` — add — in a **fresh `[Unreleased]`** section. **2.7.0 is now
  cut**, so #175's and #202's entries — including the sentence saying
  `LimitsPolicy` still accepts `0` and that the bound is left to a follow-up —
  sit under `## [2.7.0]` as shipped history. Leave them; they were true of that
  release. Under `### Changed`, not `### Fixed`: a policy file with
  `max_tools_per_server: 0` is now rejected, and via #202 that terminates
  startup. Name the value explicitly so an affected operator can search for it.
- `README.md` — modify **only if** it documents `max_tools_per_server` or shows a
  policy example carrying it; check both before editing.

## Dependencies & order

1. The `ge=1` bound and the inverted schema test.
2. The three comment corrections.
3. The end-to-end discovered-policy test.
4. CHANGELOG.

## Verification

```bash
uv run pytest -q tests/test_client_manager.py -k "ZeroLimit or Limits"
uv run pytest -q tests/test_policy_fail_open.py
uv run pytest -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/ && uv run mypy src/

# The zero-limit log path must still be reachable and still accurate:
#   construct ClientManager(max_tools_per_server=0) directly and assert the
#   message names the limit and does not say "unparseable".
```

Edge cases: `max_tools_per_server: -1` in a policy file; the value absent
(default 100 still applies); an explicit `--policy` carrying `0` (already fatal
via `fatal=True`, but assert it, since that path's message differs); a policy
with `limits: {}`.

## Acceptance criteria

- [ ] `LimitsPolicy(max_tools_per_server=0)` and `(-1)` both raise
      `ValidationError`; the default of 100 still applies when the field is absent.
- [ ] A **discovered policy file** containing `max_tools_per_server: 0` makes
      `PolicyManager()` raise — the #207 × #202 interaction, proven end-to-end
      rather than inferred from the schema test.
- [ ] `ClientManager(max_tools_per_server=0)` **still works** and still logs the
      accurate zero-limit message. A change that removes this path because the
      schema now rejects `0` must not pass — the constructor axis is untouched by
      this issue.
- [ ] None of the three corrected comments still asserts that auto-discovery
      swallows validation errors — grep for `swallow` and `fatal=False` across
      `src/` and `tests/` returns nothing describing current behaviour.
- [ ] Full suite green.

## Non-goals

- **Bounding `ClientManager.__init__`'s parameter.** It is a programmatic API, its
  `< 1` guard already handles the value defensively, and #175's own review noted
  it is not required to close the finding. Mirroring `ge=1` there is defensible
  but is a separate decision about how strict a Python constructor should be.
- Any further policy-schema tightening. This is the first bound added after #202
  removed the hazard; adding one at a time keeps the blast radius legible.

## Execution Policy

- execute: effort=low, reason=a one-field schema bound plus comment corrections;
  the only subtlety is which existing tests invert and which must survive
