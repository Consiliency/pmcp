# Detailed plan: close the five catalog-indexing findings

> **Revision 2 (2026-08-27).** Boarded 2 AGREE / 1 DISAGREE. Rev 1's item 3 would
> have introduced a **fail-open security regression**, and its item 4 named a
> helper that rejects every valid tool. Both corrected below.

## Task

Close Consiliency/pmcp#175 — five findings split out of #173 so that issue could
close on its own subject. None is blocking; all are recorded so they stay visible.

**Scope note.** Five distinct changes is at the upper end of what one bounded
plan should carry. They are kept together because each is a few lines in two
files (`manager.py`, `types.py`), they share one reviewer context (catalog
indexing), and splitting would cost five plan/board/PR cycles for changes smaller
than the ceremony. If the board disagrees, item 1 (the mutation-surviving test)
is the one worth its own change — it is the only item that is a *missing test*
rather than a code fix.

## Research summary

Verified in this worktree:

- `_index_tools` (`manager.py:~1611-1621`): `entries` is a **list** of
  `(tool_id, tool_info)` tuples. The loop does `self._tools[tool_id] = tool_info`,
  so two entries sharing an identity overwrite — but `indexed = len(entries)`
  counts the **list**, so both are counted while one lands. The docstring on the
  sibling `_index_resources` (`:1633-1635`) states the opposite in as many words:
  *"The count returned is what was actually indexed, not what was offered, so a
  caller reporting it is not overstating the catalog."* That claim is false
  exactly when identities collide. A documented guarantee contradicted by the
  code is worse than an undocumented gap — a reader trusts it.
- `LimitsPolicy.max_tools_per_server` (`types.py:960`) is a bare `int = 100` with
  no bound, so `0` validates.
- `adopt_process` (`manager.py:3134`) indexes with no preceding
  `_remove_server_indexes`; the connect and reconcile paths at `:1238`, `:2094`
  and `:2126` all remove first.
- `input_schema = tool.get("inputSchema", {})` (`manager.py:616`) sits directly
  beside `_required_identity(tool, "name")` — the same function that #172 added
  precisely to stop manufacturing missing values.

## Changes

### `src/pmcp/client/manager.py` (modify)

- `_index_tools` / `_index_resources` / the third `_index_*` — modify — count
  what actually landed, not what was offered. Simplest correct form: return
  `len({tool_id for tool_id, _ in entries})`, or count insertions. **Item 5.**
- `_index_resources` docstring (`:1633-1635`) — modify — the claim becomes true
  once the count is fixed; keep the sentence and let the code earn it rather than
  deleting the promise.
- A duplicate identity within one listing — modify — log once at DEBUG naming the
  colliding id. Silent last-write-wins is the failure the count fix makes
  *visible*; a log makes it *diagnosable*. **Item 5.**
- `adopt_process` (`:3134`) — modify — call `_remove_server_indexes(name)` before
  indexing, matching every other indexing path. **Item 2.** Chosen over amending
  the docstrings because three call sites already do it and the odd one out is
  the surprise; making the code uniform is cheaper to hold in the head than an
  exception written down in two places.
- `input_schema = tool.get("inputSchema", {})` (`:616`) — modify — treat a
  missing `inputSchema` as **unparseable**, so the entry is skipped and logged
  like any other unparseable entry (see the helper note below).
  **Item 4.** This is the deliberate decision #175 asks for: MCP requires
  `inputSchema` on a tool, and manufacturing an accept-anything schema
  misrepresents the tool to every caller. Consistent with #172's ruling on
  manufactured identities. The board agreed it should ship as skip-and-log rather
  than as a documented default.

  **Do NOT call `_required_identity(tool, "inputSchema")`.** Verified: that helper
  requires `isinstance(value, str) and value` (`manager.py:530-534`), and an
  `inputSchema` is an **object** — a literal call would reject every valid tool
  in the catalog. Add a sibling required-**object** check that raises inside the
  same per-entry `try`, so the existing skip-and-log path handles it unchanged.

  **Fixture fallout, on the change list rather than discovered at runtime:**
  `tests/mcp2x/test_catalog_publishers.py` indexes `{"name": "t1"}` with no
  `inputSchema` at lines **130, 140, 193, 218, 278, 310** and asserts `== 1`.
  Those fixtures need a valid `inputSchema`, as do the new item 1 and item 5
  fixtures — otherwise item 4 silently zeroes their counts and the failure looks
  like a bug in items 1 and 5.

### `src/pmcp/types.py` (modify)

- **Item 3 — do NOT add `Field(ge=1)`.** Fix the *log*, not the schema: when the
  limit truncates a listing to zero entries, say that the limit is zero rather
  than "all entries unparseable".

  **WAS WRONG (rev 1).** Verified: `PolicyManager._load_policy(..., fatal=False)`
  — the auto-discovery path — swallows **any** validation exception and leaves
  `self._policy` as the default `GatewayPolicy()`, which is **allow-all**
  (`policy/policy.py:59-77`). So adding `ge=1` would make every policy file
  containing `max_tools_per_server: 0` schema-invalid, silently discarding that
  file **in its entirety** — allow/deny lists, limits and redaction all reverting
  to permissive defaults. That converts a cosmetic log fix into a security
  regression, which is a spectacularly bad trade.

  The underlying fail-open is filed separately as **#202**; it is not this
  change's to fix, and folding it in would hide it. Any future tightening of the
  policy schema carries the same risk until #202 is resolved.

### `tests/test_client_manager.py` (modify)

- `TestToolLimitIsEnforced` — add — index **more** entries than
  `max_tools_per_server` and assert exactly `limit` land in the catalog.
  **Item 1.** This is the item with teeth: a mutation of the boundary
  (`len(entries) >= limit` → `>`) **survived the entire existing file** and was
  the sole survivor of nine. The test must therefore be written so that mutation
  fails — i.e. it must pin the boundary, not merely that truncation happens.
  Assert at `limit`, `limit + 1`, and `limit - 1`.
- `TestDuplicateIdentitiesAreNotDoubleCounted` — add — two entries sharing an id;
  assert the returned count equals the number in the catalog. **Item 5.**
- `TestAdoptProcessRemovesFirst` — add — index, adopt, assert no stale entry
  survives. **Item 2.**
- `TestMissingInputSchemaIsUnparseable` — add — **Item 4.**
- `TestZeroLimitLogsAccurately` — add — with `max_tools_per_server=0`, the
  emitted message names the zero limit and does **not** say the entries were
  unparseable; `LimitsPolicy(max_tools_per_server=0)` still validates. **Item 3.**

## Documentation impact

- `CHANGELOG.md` — add — one `### Fixed` entry naming all five; item 4 is a
  behaviour change (a tool without `inputSchema` is now skipped rather than
  accepted with a permissive schema) and must be called out as such, not buried.
- No README change: none of this alters user-facing configuration. The policy
  schema is deliberately left untouched (see item 3 and #202).

## Dependencies & order

1. Item 1's test **first, against unchanged code** — confirm it passes, then
   apply the boundary mutation and confirm it fails. A test written after the
   fix cannot prove it would have caught the bug.
2. Items 3 (log wording only) and 5 (independent, mechanical).
3. Item 2 (`adopt_process`), then item 4 (`inputSchema`) — item 4 is the only
   behaviour change and belongs last so a bisect lands on it cleanly.

## Verification

```bash
uv run pytest -q tests/test_client_manager.py
# Item 1 must be mutation-proven, not assumed:
#   flip `>= limit` to `> limit` in the truncation, run the new test, expect RED,
#   restore, expect GREEN. Record both exit codes.
uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run mypy src/
# (Rev 1 listed a 98-server manifest check here. That is npm identity
# resolution, unrelated to catalog indexing, and is not coverage for dropped
# tools — removed rather than left as false reassurance.)
```

Edge cases: a listing that is entirely duplicates; `adopt_process` on a name with
no prior index; a tool with `inputSchema: null` (distinct from absent); a
listing exactly at the limit.

## Acceptance criteria

- [ ] The boundary mutation `>= limit` → `> limit` makes the new test **fail** —
      recorded before/after exit codes. Item 1 exists because that mutation
      survived nine others; a test that does not kill it has not closed the
      finding.
- [ ] For a listing with duplicate identities, each `_index_*` returns a count
      equal to the number of that server's entries in **its own** catalog —
      `_index_tools` against `_tools`, `_index_resources` against `_resources`,
      `_index_prompts` against `_prompts`. **WAS WRONG (rev 1):** the criterion
      compared all three against `self._tools`, so it could not prove the guarantee
      for resources or prompts at all.
- [ ] With `max_tools_per_server=0`, the log says the limit is zero and does
      **not** claim the entries were unparseable — asserted on the emitted
      message. `LimitsPolicy(max_tools_per_server=0)` must still **validate**,
      since rejecting it is what triggers #202's fail-open.
- [ ] `adopt_process` leaves no entry that a prior index put there — asserted on
      catalog contents, not by checking that the method was called.
- [ ] A tool dict without `inputSchema` is skipped and logged as unparseable,
      and one with `inputSchema` present is indexed unchanged.
- [ ] Full suite green; no change to the 98-server manifest resolution.

## Non-goals

- Reworking `_parse_tool_entries`' per-entry error handling, which #172 settled.
- Any change to catalog *pagination*, which #173/#174 closed.

## Execution Policy

- execute: effort=medium, reason=five small changes, one of which (item 4)
  changes what is accepted from a server and needs the CHANGELOG to say so
