# Mutation evidence — Consiliency/pmcp#175

Protocol: `__pycache__` purged and `PYTHONDONTWRITEBYTECODE=1` on every
apply/restore, per the tooling note in `mutation-180.md`. Stale bytecode
fabricated a false RED during #184 and can equally fabricate a false GREEN.

Every exit code below was **captured from the shell** (`echo $?` immediately
after the run), not read off a summary line. The numbers here were produced by
re-running both mutations after the board round, on the branch as it stands —
not transcribed from an earlier session.

## 1. The truncation boundary — item 1, the mutant that survived nine others

The reason #175 item 1 exists. Mutating the tool-limit guard in
`_parse_tool_entries` (`src/pmcp/client/manager.py`) from `>=` to `>` **survived
the entire existing `tests/test_client_manager.py`** and was the sole survivor
of a nine-mutant battery. The off-by-one lets a downstream put `limit + 1` tools
in the catalog — one more than the bound, every time, silently.

    -        if len(entries) >= limit:
    +        if len(entries) > limit:

Command, run at each of the three tree states:

    find . -name __pycache__ -type d -not -path "./.venv/*" -exec rm -rf {} +
    PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q \
      tests/test_client_manager.py -k "TestToolLimitIsEnforced"

| tree state | result | exit |
|---|---|---|
| unchanged (`>=`) | `4 passed, 225 deselected in 1.40s` | **0** |
| mutant applied (`>`) | `2 failed, 2 passed, 225 deselected in 1.45s` | **1** |
| restored (`>=`), `git diff` empty | `4 passed, 225 deselected in 1.25s` | **0** |

The mutant's failure, which is the whole point — the catalog took six tools
under a limit of five:

    FAILED ...::TestToolLimitIsEnforced::test_no_more_than_the_limit_is_ever_indexed[6]
    FAILED ...::TestToolLimitIsEnforced::test_truncation_keeps_the_first_entries_and_says_so
    E       assert 6 == 5
    E        +  where 5 = <...TestToolLimitIsEnforced object ...>.LIMIT

**Ordering matters and was honoured.** The test was written and committed
(`fde84cc`) **against unchanged code, before the mutation was applied** — a test
written after the rest of the diff cannot demonstrate it would have caught the
bug. Only the `limit + 1` case distinguishes `>=` from `>`, which is why the
test asserts *exact* counts at `limit - 1`, `limit` and `limit + 1` rather than
the weaker "fewer than offered", which the mutant passes.

## 2. `adopt_process`'s removal deleted — item 2

Item 2 added one line to `adopt_process`. A test that merely asserted the
removal *method was called* would pass just as happily with the call in the
wrong place, so the tests assert on **catalog contents** — and this mutation is
what demonstrates they do.

    -        self._remove_server_indexes(name)

Command, at each of the three tree states:

    find . -name __pycache__ -type d -not -path "./.venv/*" -exec rm -rf {} +
    PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q \
      tests/test_client_manager.py -k "TestAdoptProcessRemovesStaleIndexesFirst"

| tree state | result | exit |
|---|---|---|
| unchanged (line present) | `4 passed, 225 deselected in 1.24s` | **0** |
| mutant applied (line deleted) | `2 failed, 2 passed, 225 deselected in 1.52s` | **1** |
| restored, `git diff` empty | `4 passed, 225 deselected in 1.32s` | **0** |

The mutant reproduces the defect exactly — a stale tool from a previous listing
surviving the adopt, still routable:

    FAILED ...::TestAdoptProcessRemovesStaleIndexesFirst::test_a_prior_listings_tools_do_not_survive_the_adopt
    FAILED ...::TestAdoptProcessRemovesStaleIndexesFirst::test_resources_and_prompts_are_cleared_too
    E       AssertionError: adopt_process indexed on top of the previous catalog
    E       assert {'srv::fresh', 'srv::stale'} == {'srv::fresh'}
    E         Extra items in the left set: 'srv::stale'

The two tests that stay green under the mutant are the ones that *should*:
`test_another_servers_entries_are_untouched` and
`test_adopting_a_name_with_no_prior_index_is_unchanged` pin that the removal is
per-server and harmless when there is nothing to remove — neither is a
discriminating test for this line, and neither pretends to be.

## A regression the suite caught that no mutation would have

Recorded because it is the more instructive failure of the two, and because it
argues for keeping guard tests that look redundant.

Item 5's first implementation moved the catalog **write** and the count together
into a module-level helper, `_apply_entries(catalog, name, kind, entries)`.
`tests/runtime/test_publisher_coverage.py` is an AST honesty guard: it parses
`client/manager.py`, attributes every `self._tools` / `self._resources` /
`self._prompts` write to its enclosing method, and fails if one appears outside
the five publishing mutators. A helper that receives the dict as a *parameter*
is invisible to that walk — so the refactor did not trip the guard, it
**silently emptied** it:

    E   AssertionError: expected writes from {'_index_tools', '_index_resources',
        '_index_prompts', '_remove_server_indexes', '_disconnect_all_unlocked'},
        saw {'_remove_server_indexes', '_disconnect_all_unlocked', '__init__'}

The three `_index_*` methods had stopped appearing as catalog writers at all.
Nothing about the observable behaviour changed — counts and logs were identical
— so no mutation of the *product* code would have surfaced this. Only a guard
asserting on the shape of the source did.

Fixed in `b5d0609`: the write goes back inside each `_index_*`, where the guard
can attribute it, and the helper — now `_distinct_indexed(name, kind, entries)`
— only counts and logs. Its docstring records why it must not take the catalog
dict, so the next reader does not re-make the same simplification.

The lesson generalises: a guard that asserts *"these five functions must still
do X"* protects against refactors that a behavioural test cannot see, and its
apparent redundancy is exactly what makes it load-bearing.

## What is deliberately not mutation-tested

Item 3 changes **log wording only** and adds no schema bound. There is no
behavioural boundary to mutate: `LimitsPolicy(max_tools_per_server=0)` must
continue to **validate**, because `PolicyManager._load_policy(..., fatal=False)`
swallows validation errors and falls back to the allow-all default — so
rejecting the value would silently discard an operator's entire policy file.
That fail-open is #202. The tests assert on the emitted message (the zero limit
is named, "unparseable" is absent) and pin that `0` still validates, which is
the strongest available statement about a change that alters no behaviour.
