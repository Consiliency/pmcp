# Detailed plan: nullable arguments reach every host as a type branch, never as `null` in an enum

**Revision 2** (after the round-1 review on Consiliency/pmcp#370). Rebased onto `origin/main` `b8e4305`, which adds `MIGRATING.md` and `tests/test_migration_doc.py`. The whole proof was re-run there. See **Round 1 → rev 2** at the end.

## Task

Fix Consiliency/pmcp#369 in a 3.0.x patch release. The issue was found by the 3.0.0 pre-release host gate (Consiliency/pmcp#301).

Since Consiliency/pmcp#300 (piece A of Consiliency/pmcp#236), every optional argument has been advertised as `type: [X, "null"]`. An optional enum also lists `null` among its `enum` values. OpenCode's Google provider rewrites the schemas for Gemini: it turns that `null` into the **string** `"null"` and drops the null branch of every type array.

The fix changes the spelling once, in the single place schemas are produced (`src/pmcp/tools/schema.py`). Three constraints hold throughout:

- the transport gate accepts exactly what it accepts today;
- an explicit `null` is still accepted wherever Consiliency/pmcp#300 promised it;
- the string `"null"` is never treated as `null`.

## Decision

**Every nullable argument is advertised as `anyOf: [X, {"type": "null"}]`.** In that form:

- `X` comes first and has one scalar `type`;
- `X` carries the value's own keywords (`enum`, `items`, `properties`, bounds);
- the field's `description` sits beside the `anyOf`;
- there is no `default: null`.

That gives one invariant, checked over every tool: **`null` appears in an advertised schema only as the second branch `{"type": "null"}` of a two-branch `anyOf`.** No JSON `null` value appears anywhere, whether as an `enum` member, a `const` or a `default`, and no `type` is an array.

This applies to all 42 nullable arguments, not only the 4 enums. In practice the snapshot diff is a mechanical rewrite of those 42 nodes, verified below; nothing else changes.

A small addition keeps what operators and agents see unchanged: **the gate unfolds a nullable union's refusal before it ranks the errors**. `gate_error_for()` in `schema.py` does this in three steps:

1. Collect `iter_errors`.
2. Replace each `anyOf: [X, null]` refusal of a non-null value with the errors `X` produced, in place and recursively. A root `type` mismatch is spelled `'<v>' is not of type 'X', 'null'`, as the type array spelled it.
3. Run `best_match`.

`validate_at_gate()` wraps it and raises, and the server and the migration guide's checker both call it.

Because the unfold happens before `best_match` chooses, the gate picks **the same error, with the same message, keyword and path, as 3.0's `type: [X, "null"]` spelling, including when one call has several failures.** The one intended difference is that an enum's message no longer lists `None`. Measured with zero differences over 69,000 fuzzed multi-failure calls against main's live schemas; see the proof.

Which values pass is decided by the validator alone and does not change. If the unfold raises, the gate falls back to the raw errors, so a refusal is never lost from the audit.

## Research summary

### Where schemas are produced

- `src/pmcp/tools/schema.py:input_schema_for` → `_normalize` → `_collapse_nullable` is the only producer. `handlers.py:719` calls it once per tool (cached).
- The gate is `server.py:316` (`jsonschema.validate(..., cls=GATE_VALIDATOR)`). On a refusal it calls `scoped_advisor_audit.record_rejected_arguments(error=e, ...)`, which reads `e.validator` and `e.absolute_path`.
- The snapshot fixture is `tests/fixtures/gateway_tool_schemas.json`. It is regenerated with `PMCP_UPDATE_SCHEMA_SNAPSHOT=1`.
- pydantic already emits `anyOf: [X, {"type": "null"}]` for `X | None`. Consiliency/pmcp#300's `_collapse_nullable` flattened it into `type: [X, "null"]` and appended `None` to `enum`. The fix keeps pydantic's union, drops `default: null`, and moves the field description off the branch.

### Every non-portable shape on main (`6edf8a4`; unchanged at `b8e4305`), derived from the snapshot

Classes, counted by a walk over all 26 tools:

| Shape | Count on main | After the fix |
|---|---|---|
| `type: ["string", "null"]` | 23 | 0 |
| `type: ["object", "null"]` | 12 | 0 |
| `type: ["array", "null"]` | 3 | 0 |
| `type: ["integer", "null"]` | 3 | 0 |
| `type: ["number", "null"]` | 1 | 0 |
| `null` inside `enum` | 4: `catalog_search.filters.risk_max`, `refresh.source`, `set_startup_policy.source`, `sync_environment.platform` | 0 |
| `const: null` / `default: null` / `nullable` / `oneOf` / `allOf` / `not` | 0 / 0 / 0 / 0 / 0 / 0 | 0 (now pinned) |
| nullable `$ref` | 0 (inlined by `_normalize` before the nullable step) | 0 |
| required-but-nullable `anyOf` | 0 | same spelling as optional |

### Host survey

Each capture comes from the real host binary from `/opt/consiliency/tooling/current` (release `2026.10.04.1`). A local mock LLM endpoint recorded the request the host sent. The host loaded pmcp over stdio from a scratch HOME, once with main's `src/` (`6edf8a4`) and once with the spike. No real host config or credentials were used.

| Host family | What it does to the advertised schema | main | spike |
|---|---|---|---|
| **OpenCode 1.18.34, Google provider** (`llm.runtime=ai-sdk`) | Two steps. (1) `ProviderTransform.schema`, google branch, maps every `enum` value through `String()` and rewrites `type: [X, "null"]` to `anyOf: [{type: X}]` + `nullable: true`. (2) `@ai-sdk/google` `convertJSONSchemaToOpenAPISchema` ignores an incoming `nullable` and collapses `anyOf: [X, {type: "null"}]` to `{...X, nullable: true}`. | 26/26 tools sent. 4 enums contain the string `"null"`, e.g. `risk_max: {"enum": ["low","medium","high","null"], "anyOf": [{"type":"string"}]}`. All 42 nullable nodes are sent as a typeless `anyOf: [{type: X}]` with no `nullable`, and the 3 arrays have `items` outside their ARRAY branch. | 26/26 tools sent. No `"null"` string anywhere. Every nullable node is `{type: X, nullable: true, ...X's keywords}`, e.g. `source: {"nullable": true, "type": "string", "enum": ["claude_config","custom"]}`. |
| **OpenCode `GeminiToolSchema`** (newer converter in the same binary, not the runtime selected for `google/*` today) | `enum` → `String()`; `type` array → first non-null type + `nullable`; `anyOf` branches are kept. | `"null"` string in 4 enums (Python port). | `anyOf: [{type: X, ...}, {type: "null"}]`, no `"null"` string (Python port). |
| **Codex** codex-cli 0.160.0, Responses API, namespace `mcp__pmcp`, `strict: false` | Passes `type`, `enum`, `anyOf`, `properties`, `items`, `description` through. Strips `default`, `minimum`, `maximum`, `exclusiveMinimum`, `minLength`, `maxLength` and `pattern` the same way on both. | Type arrays and `null` enum members sent as advertised. | `anyOf: [X, {"type": "null"}]` sent as advertised. |
| **Claude Code** 2.1.x, `ENABLE_TOOL_SEARCH=false` | Sends `input_schema` verbatim. | 26/26 identical to advertised. | 26/26 identical to advertised. |
| **Gemini native function calling** (Gemini API, agy) | Documented `Schema` is an OpenAPI 3.0 subset: scalar `type` (STRING, NUMBER, INTEGER, BOOLEAN, ARRAY, OBJECT, NULL), `nullable`, string-only `enum`, `anyOf`, `items`, `properties`, `required`, bounds. | Not reachable. agy refuses the dotted tool names before converting (Consiliency/pmcp#368, pre-existing), and there is no Gemini key on the host. | Same. The spike's OpenCode payload passes a check against the documented subset for all 26 tools. Live API acceptance is **not verified**. |

**Python port fidelity:** `opencode_gemini_request_schema()` in the new test module is checked against the captured OpenCode payloads. On both main and the spike, all 26 tools are **byte-for-byte identical** (`port mismatches 0` on both). That match is what lets the test stand in for the host in CI.

### Rejected encodings

- **Drop `null` from `enum` and keep `type: [X, "null"]`.** This breaks the gate. `enum` is an independent assertion, so `{"enum": ["a","b"], "type": ["string","null"]}` **refuses** `null` (measured: `E1 [(None, False), ('a', True), ('null', False), ...]`). That reverts Consiliency/pmcp#300's promise for those 4 arguments. It also leaves the other 38 type arrays typeless under the AI SDK.
- **`anyOf` for the 4 enums only.** It fixes the visible defect, but it leaves two spellings of one concept. It also keeps the class this survey found: each `type: ["object"|"array", "null"]` reaches Gemini with `properties`/`items` on a typeless node beside an `anyOf: [{type: OBJECT}]` / `[{type: ARRAY}]` branch that has none.
- **Accept the string `"null"` as `null` at the gate.** Excluded by the brief. It would widen what the gate accepts and give `"null"` two meanings.
- **`nullable: true` (OpenAPI 3.0).** It is not JSON Schema 2020-12. The gate (`Draft202012Validator`) would ignore it and refuse `null`.

## Changes

The full patch is embedded below and was applied to fresh `origin/main` for the proof.

### `src/pmcp/tools/schema.py` (modify)

- Module docstring: state the new spelling and why (Consiliency/pmcp#369).
- `_normalize`: call `_canonical_nullable` instead of `_collapse_nullable`.
- `_collapse_nullable` → **replace** with `_canonical_nullable(node)`. For a two-branch `anyOf` containing `{"type": "null"}`:
  - emit `anyOf: [X, {"type": "null"}]` with X first;
  - drop `default: null`;
  - if the node has its own `description`, remove `description` from X. X can be an inlined model whose docstring would otherwise ride along, and the AI SDK's fold lets the branch's description override the field's.
  - Required-but-nullable fields get the same spelling and stay in `required`.
- `gate_error_for(instance, schema)` → **add**. It runs `GATE_VALIDATOR(schema).iter_errors(instance)` and passes each error through `_unfolded`. For a canonical nullable `anyOf` refusing a non-null instance, `_unfolded` returns branch 0's errors, each re-rooted at the argument by `_rerooted`, in their original order and unfolded recursively. Otherwise it returns `[error]`. The result goes through `best_match`.
  - `_rerooted` gives each error its full absolute path (so `best_match`'s `-len(path)` and `path` keys rank it as 3.0's flat error ranked) and the original `type_checker`.
  - A root `type` mismatch becomes `'<v>' is not of type 'X', 'null'`, with `validator_value=[X, "null"]` and `schema={**X, "type": [X, "null"]}`, so `_matches_type` ranks it as 3.0 did.
  - If anything raises in the unfold, it falls back to `best_match(raw errors)`.
- `validate_at_gate(instance, schema)` → **add**. It calls `GATE_VALIDATOR.check_schema(schema)` (as `jsonschema.validate` did), then raises `gate_error_for(...)`'s error, if any.

### `src/pmcp/server.py` (modify)

- In the gate, replace `jsonschema.validate(instance=arguments, schema=tool.input_schema, cls=GATE_VALIDATOR)` with `validate_at_gate(arguments, tool.input_schema)`. The `except jsonschema.ValidationError as e:` block is unchanged, so the message and `record_rejected_arguments(error=e)` both get the chosen error. The `GATE_VALIDATOR` import moves out of `server.py`; nothing imported it from there except `test_migration_doc.py`, which is changed below.

### `tests/test_migration_doc.py` (modify; F001)

- `_gate_error(call)` returns `gate_error_for(call["arguments"], tool.input_schema)`, the error the server reports, instead of jsonschema's raw choice. Without this change, `test_the_guide_passes[gate]` fails with 3 problems: the `ttl: "5"` and `poll_interval: NaN` rows collapse to `... is not valid under any of the given schemas`. With it, that test file gives 34 passed, 3 skipped.

**The whole class of callers.** Every caller that validates against an advertised schema, found by grepping `src/`, `tests/` and `scripts/` for `jsonschema.validate`, `iter_errors`, `best_match`, `GATE_VALIDATOR` and `.is_valid(`:

- **Reads the refusal's message, keyword or path:** `src/pmcp/server.py` (the gate) and `tests/test_migration_doc.py::_gate_error`. Both now go through `validate_at_gate`/`gate_error_for`.
- **Only decides accept or refuse:** `tests/test_gateway_tool_schemas.py` (`jsonschema.validate` ×5), `tests/test_task_numeric_bounds.py` (`validate`/`is_valid`), `tests/test_task_units.py` (`iter_errors` emptiness), `tests/test_scoped_advisor_audit.py:1480/1498/1924`. These are unaffected, because accept and refuse do not change.
- `tests/test_scoped_advisor_audit.py::_first_error` ranks errors from hand-written schemas, not advertised ones.

There are no other `src/` callers.

### `tests/fixtures/gateway_tool_schemas.json` (regenerate, do not hand-edit)

```
PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest tests/test_gateway_tool_schemas.py -k snapshot
```

Expected diff: exactly the 42 nullable nodes. Verified: applying the mechanical rewrite to main's snapshot (`type: [X,"null"]` → `anyOf: [{X without description, type X, enum without None}, {"type":"null"}]` + `description`) reproduces the regenerated snapshot exactly (`snapshot == mechanical old->anyOf rewrite: True`; 42 type arrays → 42 null branches).

### `tests/test_gateway_tool_schemas.py` (modify)

- `_object_schemas`: comment only. The walk already reaches an optional nested object through `anyOf`.
- `test_advertised_schema_is_a_self_contained_mcp_input_schema`: the old `"anyOf" not in prop` assertion becomes "no `type` array".
- `test_input_schema_for_normalises_pydantic_output`: new expected shapes.
- `test_required_nullable_field_is_left_as_pydantic_wrote_it`: docstring only (same shape now).
- `_unbounded_integers`: **descend into `anyOf`.** Without this, `invoke.task.ttl` and `options.max_output_chars` would silently stop being checked (mutants M12 and M14).

### `tests/test_scoped_advisor_audit.py` (modify)

- `_open_containers`: read object-ness from the node and its `anyOf` branches, in either spelling. Without this, the generated-value sweep silently stops injecting into `invoke.options`/`task`/`trace_context`/`_meta` and `tasks_*.requestor_context`.
- `test_open_containers_see_every_nullable_object_argument` → **add**. It pins the containers found for `invoke`, `tasks_result` and `tasks_get`, so that a walker regression fails here instead of shrinking the sweep (M13).

### `tests/test_nullable_schema_portability.py` (create)

All per-argument cases are derived from the **models** (`_model_nullable_paths`: every field whose annotation admits `None`, nested models included), not from the schemas. That gives 42 sites over 13 tools. A schema that forgot a site therefore still gets tested.

1. **Structural**
   - `test_null_is_only_ever_a_type_branch[tool]`: the invariant above, plus no `oneOf`/`allOf`/`not`/`const`/`nullable` keyword.
   - `test_nullable_sites_are_exactly_the_fields_that_admit_none[tool]`: advertised sites == model sites.
   - `test_sites_were_derived`: ≥40 sites, including the issue's 4.
2. **Gate**
   - `test_gate_accepts_explicit_null_at_every_nullable_site` (42).
   - `test_gate_refuses_the_string_null_where_it_is_not_a_value`: every enum site and every non-string site. Free-form strings are excluded, because `"null"` is a valid string there.
   - `test_server_gate_on_every_nullable_enum` (4): end to end through `_handle_call_tool` with a stub handler. `"null"` gets `Input validation error` and never reaches the handler; `null` reaches it.
3. **Hosts**
   - Python ports: `opencode_google_transform`, `ai_sdk_google_openapi`, and `opencode_gemini_tool_schema` (`Q1`+`ix`). Each is transcribed from the 1.18.34 bundle, with byte offsets in the module docstring.
   - `test_ports_reproduce_the_captured_defect`: a fidelity self-check that reproduces the issue's capture.
   - `test_no_string_null_reaches_gemini` (both converters).
   - `test_gemini_request_schema_is_in_gemini_subset`: documented `Schema` fields, scalar type, string-only enums, arrays have `items`.
   - `test_every_nullable_argument_stays_nullable_and_omittable_for_gemini` (42): `nullable: true`, same `type`/`enum`/`items`/`properties`, and not in the parent's `required`.
   - `test_gemini_required_is_the_models_required`.
4. **Reports.** The oracle is `jsonschema`'s own `best_match` over the raw errors, run on the schema re-spelled the pre-#369 way (`_type_array_spelling`). Its enum messages are normalised by removing `, None]`.
   - `test_gate_reports_what_the_type_array_spelling_reported` (42 sites × 17 probes, including `[1, 2]` and `{"a": 1, "b": 2}`, two failures inside one value).
   - `test_gate_reports_what_the_type_array_spelling_reported_for_many_failures[tool]` (13 tools). Every ordered pair of the tool's sites × 6×6 grid values, plus 300 seeded calls with three sites each. This covers the ranking class F002 found: `best_match` takes the last sibling at the top level but the first one inside an `anyOf`.
   - `test_round_one_multi_failure_cases` (4): the review's measured divergences, pinned to 3.0's report.
   - `test_gate_error_for_falls_back_when_the_fold_fails`: if `_unfolded` raises, the raw refusal is still reported.
   - `test_server_message_for_a_wrongly_typed_nullable_argument`: the migration guide's row, end to end.

### `CHANGELOG.md` (modify; F003)

**This lands before the 3.0.0 tag** (`main` has no `## [3.0.0]` and there is no `v3*` tag). No released version advertised `type: [X, "null"]`, so there is **no Fixed entry**. Instead, the Consiliency/pmcp#236 **Changed** entry is amended: its sentence "Optional arguments are advertised as `type: [X, "null"]` …" now states the `anyOf` spelling, why (OpenCode's Google provider), what Gemini, Claude Code and Codex receive, and that a client reading one flat `type` per argument now finds `anyOf` (the review's nit). The hunk is in the patch.

*If the tag is cut first*, add instead a `## [Unreleased]` → `### Fixed` entry above `## [3.0.0]`. It should say:

> Optional arguments are advertised as `anyOf: [X, {"type": "null"}]` instead of `type: [X, "null"]` with `null` among an enum's values. OpenCode's Google provider had sent Gemini the string `"null"` as an allowed value of `catalog_search.filters.risk_max`, `refresh.source`, `set_startup_policy.source` and `sync_environment.platform`, and dropped `null` from all 42 nullable arguments. The gate accepts and refuses the same values. It reports the same error for a refused call, except that an enum's list of allowed values no longer shows `None`. A client that reads one flat `type` per argument now finds `anyOf`.

### Docs: no other file changes

- `README.md` and `docs/` do not spell the nullable shape.
- `MIGRATING.md` (now on `main`) pins gate **messages**, not the shape. All of its gate cases and `tools/call` snippets pass through `test_the_guide_passes` once its checker uses `gate_error_for` (above). The guide's text needs no edit.

## Verification

Run on dev0 (a team host):

```bash
git -C ~/code/pmcp fetch origin
git -C ~/code/pmcp worktree add -b fix/369-nullable-enums "$WORKTREE_ROOT/pmcp-369-fix" origin/main   # b8e4305 or later
cd "$WORKTREE_ROOT/pmcp-369-fix" && uv sync --all-extras -p 3.10
# apply the patch below, then:
PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest -p no:cacheprovider tests/test_gateway_tool_schemas.py -k snapshot
mkdir -p /var/tmp/pmcp-369-bt-$USER
env -u npm_config_cache -u npm_config_store_dir TMPDIR=/var/tmp/pmcp-369-bt-$USER \
  uv run pytest -p no:cacheprovider --basetemp=/var/tmp/pmcp-369-bt-$USER/full -q
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src/pmcp --exclude baml_client
```

To repeat the host captures, use the procedure in **Host survey**: a mock endpoint on 127.0.0.1, then `opencode run`, `codex exec` and `claude -p` from scratch HOMEs, each pointed at it. That procedure is optional: the port test is the CI stand-in, and its fidelity was checked against those captures.

## Acceptance criteria

- [ ] No advertised schema contains a JSON `null` or a `type` array. `null` appears only as `anyOf[1] == {"type": "null"}` of a two-branch `anyOf` (`test_null_is_only_ever_a_type_branch`).
- [ ] Advertised nullable sites == model fields admitting `None` (42 sites, 13 tools).
- [ ] Explicit `null` is accepted at all 42 sites. The string `"null"` is refused at every enum and non-string site, and end to end for the 4 enums.
- [ ] Through OpenCode's Google chain (port verified against the real host), no enum contains `"null"`, every nullable argument is `{type: X, nullable: true}` and omittable, and every node is in Gemini's documented subset.
- [ ] The gate reports the same message, `validator` and path as 3.0's spelling. The only difference allowed is that enum messages no longer list `None`. This covers:
  - every site × 17 single-value probes;
  - every ordered pair of a tool's sites × 6×6 grid values, plus 300 seeded three-site calls per tool;
  - the 4 round-1 cases.
- [ ] `test_migration_doc.py` passes. Its gate checker calls `gate_error_for`.
- [ ] If the unfold raises, the raw refusal is still reported (`test_gate_error_for_falls_back_when_the_fold_fails`).
- [ ] CHANGELOG: the #236 Changed entry states the `anyOf` spelling, and there is no Fixed entry (pre-tag).
- [ ] The snapshot diff is exactly the 42 nullable nodes.
- [ ] The full suite, ruff, ruff format and mypy pass. Mutants M1–M17 are killed.

## Out of scope

- agy's dotted-name rejection (Consiliency/pmcp#368).
- Live Gemini API acceptance: there is no key on the test host.
- Forbidding unknown keys (piece B of Consiliency/pmcp#236).
- The validation keywords Codex strips: they are identical before and after this change.

## Embedding proof (rev 2)

The proof was run on dev0 on 2026-10-05. The patch was extracted **from this plan file** (the `diff` block below; its bytes match the spike's diff) and applied to a fresh detached worktree at `origin/main` **`b8e4305`**:

| Check | Result |
|---|---|
| `git apply --check` + `git apply` | Clean: 6 files modified, 1 created |
| Snapshot regenerated (`PMCP_UPDATE_SCHEMA_SNAPSHOT=1`) | Byte-identical to the spike's and to rev 1's. Exactly the 42 nullable nodes changed |
| Full suite (`env -u npm_config_cache -u npm_config_store_dir`, `-p no:cacheprovider`, `TMPDIR` and `--basetemp` under `/var/tmp/pmcp-369-bt-viperjuice`) | **9100 passed, 5 skipped, 80 deselected (14m01s)**. The spike on `b8e4305` gave the same result: 9100 passed, 5 skipped |
| `ruff check src tests` / `ruff format --check src tests` / `mypy src/pmcp --exclude baml_client` | Pass / 182 files formatted / no issues in 53 files |
| `tests/test_migration_doc.py` (F001) | Passes as part of the suite. The review's F001 falsifier `_check_gate(MIGRATING.md)` returns `[]` |
| Review's F002 case, `sync_environment {"detected_clis": [1, 2]}` | `("2 is not of type 'string'", "type", ["detected_clis", 1])`, the same report as 3.0 |
| Multi-failure differential fuzz, outside CI: 69,000 random calls, 1–4 sites each, over **every** argument path of all 26 tools (not only the nullable ones), values from the probe set plus valid values | **0 report differences, 0 accept/refuse mismatches**. It was run twice: once against the `_type_array_spelling` oracle, and once against **main's live in-memory schemas** (exported from `b8e4305` `src/`, key order preserved, `jsonschema`'s own `best_match`) |
| The patched tests run against **unpatched** `b8e4305` `src/` | 185 failed, 368 passed, 3 skipped (breakdown below) |

**What is red on main and is evidence of the defect:**
- 42 × `..._stays_nullable_and_omittable_for_gemini`;
- 13 × `test_null_is_only_ever_a_type_branch`, `test_nullable_sites_are_exactly_...`, `test_gemini_request_schema_is_in_gemini_subset` and `test_advertised_schema_is_a_self_contained_...`;
- 4 × `test_no_string_null_reaches_gemini` (the issue's four enums);
- the snapshot test and `test_input_schema_for_normalises_pydantic_output`.

**What is red on main only because `gate_error_for` does not exist there:**
- the report tests: 42 single-value, 13 multi-failure, 4 round-1 cases and 1 fallback;
- the `test_migration_doc` failures. In the copied test tree these also lack `README.md`/`SECURITY.md`.

Main's own spelling *is* the oracle, so the report tests are regression guards on the unfold, not evidence of the defect.

**What is green on main by design (regression guards):**
- 42 × explicit `null` accepted;
- 23 × the string `"null"` refused;
- 4 × end-to-end tests on the nullable enums;
- the migration-guide message end to end;
- `required` equals the model's required fields;
- the port self-check;
- the open-containers pin.

### Mutants (rev 2)

All 17 mutants were run on a copy of the spike tree, and all 17 were killed:

| # | Mutant | Killed by (among others) |
|---|---|---|
| M1 | `None` appended to the branch's `enum` | `test_no_string_null_reaches_gemini`, `test_null_is_only_ever_a_type_branch`, snapshot |
| M2 | the pre-fix collapse (`type: [X,"null"]`, `None` in enum) | `..._omittable_for_gemini`, `..._self_contained_...`, snapshot |
| M3 | null branch dropped | `test_gate_accepts_explicit_null_at_every_nullable_site` |
| M4 | null as `{"const": null}` | `test_no_string_null_reaches_gemini`, Gemini subset |
| M5 | `default: null` kept | `test_null_is_only_ever_a_type_branch` |
| M6 | null branch first | `test_null_is_only_ever_a_type_branch` |
| M7 | inlined model docstring on the branch | `test_input_schema_for_normalises_pydantic_output`, snapshot |
| M8 | server calls `jsonschema.validate` instead of `validate_at_gate` | `test_server_message_for_a_wrongly_typed_nullable_argument` |
| M9 | folded type message omits `'null'` | `test_migration_doc::test_the_guide_passes`, report tests |
| M10 | no unfolding at all | `test_migration_doc::test_the_guide_passes`, report tests |
| M11 | port: `String(null)` not applied | `test_ports_reproduce_the_captured_defect` |
| M12 | `task.ttl` upper bound removed | `test_every_advertised_integer_is_bounded_both_sides` |
| M13 | `_open_containers` reverted to `type`-only | `test_open_containers_see_every_nullable_object_argument` |
| M14 | `options.max_output_chars` upper bound removed | `test_every_advertised_integer_is_bounded_both_sides` |
| **M15** | **unfold AFTER ranking (rev 1's behaviour)** | `..._for_many_failures`, `test_round_one_multi_failure_cases`, single-value report test, `test_the_guide_passes` |
| **M16** | no fallback when the unfold raises | `test_gate_error_for_falls_back_when_the_fold_fails` |
| **M17** | migration-guide checker reverted to raw `jsonschema.validate` | `test_migration_doc::test_the_guide_passes` |

## Round 1 → rev 2

The review is the claude seat's round-1 review on Consiliency/pmcp#370 (DISAGREE).

- **Rebased** onto `origin/main` `b8e4305` (it adds `MIGRATING.md` + `tests/test_migration_doc.py`), and the whole proof was re-run there.
- **F001 (blocking).** On `b8e4305`, `test_the_guide_passes[gate]` failed with 3 problems because the guide's checker called raw `jsonschema.validate`.
  - Fix: its `_gate_error` returns `gate_error_for(...)`, the server's choice.
  - Every other caller that validates against advertised schemas was enumerated in Changes. Only the server and this checker read the refusal; the rest only decide accept or refuse, which does not change.
  - Mutant M17 guards it.
- **F002.** Rev 1's `gate_error()` ran *after* `best_match` had chosen an error, so a call with several failures could report a different one (1,088 differences in the seat's 39k-call fuzz). It is replaced by **unfold-before-rank**:
  - `gate_error_for` replaces each nullable-union refusal with X's own errors, in place, then ranks;
  - `validate_at_gate` raises the result and is the server's only gate call.
  - Result: 0 differences over 69,000 multi-failure calls against main's live schemas. The claim is now true as written, apart from enum messages no longer listing `None`.
  - New CI coverage: a per-tool pair grid (all ordered site pairs × 36 value pairs, plus 300 seeded three-site calls), probes with two bad items or fields, the review's 4 cases, and mutant M15 (rev 1's behaviour).
- **F003.** 3.0.0 is not tagged, so the #236 Changed entry is amended to the `anyOf` spelling, and the Fixed entry is removed: no released version had the defect. The post-tag wording is kept in the plan as the fallback.
- **Nit (fail-safe).** If the unfold raises, `gate_error_for` falls back to `best_match` over the raw errors, so the refusal is still reported and audited (test + M16).
- **Nit (other converters).** The CHANGELOG now says that a client reading one flat `type` per argument finds `anyOf`.
- **Unchanged from rev 1:**
  - the encoding;
  - `_canonical_nullable`;
  - the snapshot (byte-identical);
  - the host survey;
  - the OpenCode port, which the seat confirmed clause for clause.
- **One behaviour kept on purpose:** `validate_at_gate` still calls `check_schema` per call, as `jsonschema.validate` did. `gate_error_for` skips it, because it costs ~15 ms of the ~16 ms per call and the advertised schemas are already validated by `test_advertised_schema_is_valid_json_schema`.

## Patch

Apply with `git apply` on `origin/main` (`b8e4305` or later), then regenerate the snapshot (see Changes). The fixture diff is not embedded; the regeneration command reproduces it byte for byte.

```diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 9fa0441..87f04de 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -786,7 +786,7 @@ to do, how to verify it, and how to roll back to 2.7.3.
   [Consiliency/pmcp#224](https://github.com/Consiliency/pmcp/issues/224).
 
 ### Changed
-- **Gateway tool `inputSchema`s are now derived from the pydantic models that validate the arguments, so the two can no longer disagree ([Consiliency/pmcp#236](https://github.com/Consiliency/pmcp/issues/236)).** Constraints the models always enforced are now advertised and enforced at the transport gate — `minLength` on identifiers, `submit_feedback.title` 8–160 chars, bounds on `tasks_result.options` — so those rejections now come back as an `isError` tool result reading `Input validation error: …` instead of an `{"error": true}` payload. `gateway.invoke` now advertises `task`, `trace_context` and `_meta`; `gateway.tasks_*` advertise `requestor_context`; `tasks_result.options` gains `timeout_ms`. Optional arguments are advertised as `type: [X, "null"]` and the transport gate now accepts an explicit `null` for them, as the handlers always did; 28 optional arguments (e.g. `catalog_search.query`, `invoke.options`, `auth_connect.credential`) were previously rejected at the gate when sent as `null`. The gate does not apply pydantic's lax coercion: values such as `1` for a boolean or `"5"` for an integer on the newly advertised `invoke.task` fields (`enabled`, `ttl`, `poll_interval`), which were previously accepted and coerced, are now rejected with `Input validation error: 1 is not of type 'boolean'`. `invoke.task.ttl` and `invoke.task.poll_interval` are now range-checked at the gate; the ranges are given in the [Consiliency/pmcp#298](https://github.com/Consiliency/pmcp/issues/298) entry below. `invoke.evidence_label_digest` now also advertises its exact length (64), so a digest with a trailing newline is rejected at the gate instead of by the handler. Inputs the gate now rejects that previously reached the handler were recorded in the scoped-advisor audit as `failure`; they are now recorded as `audit.rejection` events with `terminal_status: "invalid_arguments"` (see the [Consiliency/pmcp#296](https://github.com/Consiliency/pmcp/issues/296) entry below). Unknown keys are still accepted and ignored, as before; forbidding them is tracked on [Consiliency/pmcp#236](https://github.com/Consiliency/pmcp/issues/236). Argument descriptions agents already saw were unchanged by this entry, except `gateway.update_server.force`, which now describes the task-aware behaviour; 19 previously undescribed arguments gain a description. (Several more descriptions are corrected later in this release; see the agent-visible text entry under Changed.)
+- **Gateway tool `inputSchema`s are now derived from the pydantic models that validate the arguments, so the two can no longer disagree ([Consiliency/pmcp#236](https://github.com/Consiliency/pmcp/issues/236)).** Constraints the models always enforced are now advertised and enforced at the transport gate — `minLength` on identifiers, `submit_feedback.title` 8–160 chars, bounds on `tasks_result.options` — so those rejections now come back as an `isError` tool result reading `Input validation error: …` instead of an `{"error": true}` payload. `gateway.invoke` now advertises `task`, `trace_context` and `_meta`; `gateway.tasks_*` advertise `requestor_context`; `tasks_result.options` gains `timeout_ms`. Optional arguments are advertised as `anyOf: [X, {"type": "null"}]` and the transport gate now accepts an explicit `null` for them, as the handlers always did. `null` is never an `enum` value or part of a `type` array, because host schema converters do not carry those over: OpenCode's Google provider turns an `enum` value `null` into the string `"null"` and drops the `null` from a type array. That provider sends the `anyOf` form to Gemini as `{"type": X, "nullable": true}`; Claude Code and Codex pass it through unchanged. A client that reads a single flat `type` per argument now finds `anyOf` on these arguments ([Consiliency/pmcp#369](https://github.com/Consiliency/pmcp/issues/369)); 28 optional arguments (e.g. `catalog_search.query`, `invoke.options`, `auth_connect.credential`) were previously rejected at the gate when sent as `null`. The gate does not apply pydantic's lax coercion: values such as `1` for a boolean or `"5"` for an integer on the newly advertised `invoke.task` fields (`enabled`, `ttl`, `poll_interval`), which were previously accepted and coerced, are now rejected with `Input validation error: 1 is not of type 'boolean'`. `invoke.task.ttl` and `invoke.task.poll_interval` are now range-checked at the gate; the ranges are given in the [Consiliency/pmcp#298](https://github.com/Consiliency/pmcp/issues/298) entry below. `invoke.evidence_label_digest` now also advertises its exact length (64), so a digest with a trailing newline is rejected at the gate instead of by the handler. Inputs the gate now rejects that previously reached the handler were recorded in the scoped-advisor audit as `failure`; they are now recorded as `audit.rejection` events with `terminal_status: "invalid_arguments"` (see the [Consiliency/pmcp#296](https://github.com/Consiliency/pmcp/issues/296) entry below). Unknown keys are still accepted and ignored, as before; forbidding them is tracked on [Consiliency/pmcp#236](https://github.com/Consiliency/pmcp/issues/236). Argument descriptions agents already saw were unchanged by this entry, except `gateway.update_server.force`, which now describes the task-aware behaviour; 19 previously undescribed arguments gain a description. (Several more descriptions are corrected later in this release; see the agent-visible text entry under Changed.)
 - **`tools/call` input-schema rejections are now recorded in the scoped-advisor audit, without argument values ([Consiliency/pmcp#296](https://github.com/Consiliency/pmcp/issues/296)).** A call the transport gate rejects used to return `Input validation error: …` before the audit was reached, so an operator saw no attempt at all. It is now written as a new `audit.rejection` event (not an `audit.invocation`: nothing was invoked, and a reader that correlates invocations to a run skips it) with the tool name (for the scoped-advisor tools; any other tool is recorded with `gateway_tool: null` and a `gateway_tool_digest`), `terminal_status: "invalid_arguments"`, `rejected_argument_path`, the failing location as a JSON array (a key the schema declares, an array index, or `null` for a key the caller chose, since that key can itself be a secret), and `rejected_argument_validator`, the failing JSON Schema keyword (`type`, `pattern`, `required`, …). The record never contains the validation message, the rejected value, correlation IDs, or any digest of the arguments. The capability stays `scoped_advisor_audit.v1`; readers that dispatch on `event` are unaffected. Policy is now judged **before** the schema: a call to a policy-blocked gateway tool is refused with "Gateway tool blocked by policy" and recorded `denied` whatever its arguments, instead of getting an `Input validation error` that described the blocked tool's schema. If the audit sink has failed, a malformed call now gets "Scoped advisor audit channel failed" like every other call, instead of its validation error. The response to a rejected call from an allowed tool is unchanged. An `audit.invocation` record now reads nothing the schema gate did not vouch for: a call refused by policy, or made to an unregistered name, is recorded `denied` with every argument-derived field (`run_correlation_id`, `seat_correlation_id`, `downstream_tool_id`, `evidence_label_digest`, `source_reference_hash`) `null`, a result digest that no longer covers the caller's tool name, and a `gateway_tool_digest` of the registered name (for an unregistered name, of nothing) — previously a correlation-shaped value or a public URL anywhere in such a call's arguments was copied or hashed into the audit. Every other invocation record reads only the top-level arguments the tool's schema declares, so a correlation-shaped key a tool does not declare (e.g. `run_correlation_id` on `gateway.describe`) is no longer recorded; `gateway.invoke` declares every field the record reads, so its records are unchanged.
 - **`gateway.invoke`'s `task.ttl` and `task.poll_interval` are bounded, and
   NaN/Infinity are refused at the gate (see [Consiliency/pmcp#298](https://github.com/Consiliency/pmcp/issues/298)).**
diff --git a/src/pmcp/server.py b/src/pmcp/server.py
index 290c030..f17bb1a 100644
--- a/src/pmcp/server.py
+++ b/src/pmcp/server.py
@@ -70,7 +70,7 @@ from pmcp.scoped_advisor_audit import (
 from pmcp.subscriptions import BusCatalogEventSink
 from pmcp.summary import generate_capability_summary
 from pmcp.tools.handlers import GatewayTools, get_gateway_tool_definitions
-from pmcp.tools.schema import GATE_VALIDATOR
+from pmcp.tools.schema import validate_at_gate
 from pmcp.types import (
     DescriptionsCache,
     GatewayDiagnosticsInfo,
@@ -312,9 +312,7 @@ class GatewayServer:
         audited_arguments: dict[str, Any] | None = None
         if tool is not None and allowed:
             try:
-                jsonschema.validate(
-                    instance=arguments, schema=tool.input_schema, cls=GATE_VALIDATOR
-                )
+                validate_at_gate(arguments, tool.input_schema)
             except jsonschema.ValidationError as e:
                 try:
                     if self._scoped_advisor_audit is not None:
diff --git a/src/pmcp/tools/schema.py b/src/pmcp/tools/schema.py
index 162dd75..f05e79a 100644
--- a/src/pmcp/tools/schema.py
+++ b/src/pmcp/tools/schema.py
@@ -11,19 +11,30 @@ it hoists nested models into ``$defs``/``$ref``, emits a ``title`` on every
 property, spells optional fields as ``anyOf: [X, {"type": "null"}]`` with
 ``default: null``, and carries the model docstring as a top-level
 ``description``. :func:`input_schema_for` post-processes all four so the
-advertised shape is self-contained and title-free, and an optional string is
-``"type": ["string", "null"]`` — flat like the hand-written schemas were, but
-accepting ``null`` exactly where the model does.
-``tests/test_gateway_tool_schemas.py`` pins that post-processing.
+advertised shape is self-contained and title-free, and an optional field is
+``anyOf: [X, {"type": "null"}]`` with no ``default: null`` -- accepting
+``null`` exactly where the model does.
+
+That nullable spelling is the one portable across host schema converters
+(Consiliency/pmcp#369): ``null`` is a type branch, never an ``enum`` member,
+a ``const``, a ``default`` or an entry in a ``type`` array. OpenCode's Google
+provider stringifies every ``enum`` member (``null`` became the string
+``"null"``) and turns ``type: [X, "null"]`` into a typeless node whose
+``nullable`` the AI SDK then drops; the ``anyOf`` form is the one the AI SDK
+folds into Gemini's canonical ``{type: X, nullable: true}``.
+``tests/test_gateway_tool_schemas.py`` and
+``tests/test_nullable_schema_portability.py`` pin that post-processing.
 """
 
 from __future__ import annotations
 
+from collections import deque
 from copy import deepcopy
 import math
 from typing import Any
 
 import jsonschema
+from jsonschema.exceptions import best_match
 from pydantic import BaseModel
 
 #: Advertised for gateway tools that take no arguments at all.
@@ -71,7 +82,7 @@ def input_schema_for(model: type[BaseModel] | None) -> dict[str, Any]:
 
 
 def _normalize(node: Any, defs: dict[str, Any]) -> Any:
-    """Inline ``$ref``s, drop ``title``s, and collapse nullable ``anyOf``s."""
+    """Inline ``$ref``s, drop ``title``s, and canonicalise nullable ``anyOf``s."""
     if isinstance(node, list):
         return [_normalize(item, defs) for item in node]
     if not isinstance(node, dict):
@@ -92,32 +103,116 @@ def _normalize(node: Any, defs: dict[str, Any]) -> Any:
             out[key] = {name: _normalize(prop, defs) for name, prop in value.items()}
         else:
             out[key] = _normalize(value, defs)
-    return _collapse_nullable(out)
+    return _canonical_nullable(out)
 
 
-def _collapse_nullable(node: dict[str, Any]) -> dict[str, Any]:
-    """``anyOf: [X, null]`` + ``default: null`` -> ``X`` with ``null`` still allowed.
+def _canonical_nullable(node: dict[str, Any]) -> dict[str, Any]:
+    """``anyOf: [X, null]`` -> ``anyOf: [X, {"type": "null"}]``, X first, no ``default: null``.
 
-    pydantic spells ``X | None = None`` as that ``anyOf``. The advertised
-    schema keeps ``X``'s keywords flat (no ``anyOf``) and adds ``"null"`` to
-    its ``type`` (and to its ``enum``, if any), so the gate accepts exactly
-    what the model accepts: the field omitted, ``null``, or an ``X``. A
-    nullable field WITHOUT a ``None`` default (required-but-nullable) is left
-    as pydantic wrote it -- the collapse is only defined for the optional case.
+    pydantic spells ``X | None`` (with or without ``= None``) as a two-branch
+    ``anyOf``. The advertised schema keeps that union -- the gate accepts
+    exactly what the model accepts: an ``X``, ``null``, or (when optional)
+    the field omitted -- and drops a ``default: null``, so ``null`` reaches a
+    host only as the ``{"type": "null"}`` branch (Consiliency/pmcp#369).
     """
     any_of = node.get("anyOf")
     if not isinstance(any_of, list) or len(any_of) != 2 or _NULL_SCHEMA not in any_of:
         return node
-    if "default" not in node or node["default"] is not None:
-        return node
     (inner,) = [branch for branch in any_of if branch != _NULL_SCHEMA]
-    rest = {k: v for k, v in node.items() if k not in ("anyOf", "default")}
-    out = {**inner, **rest}
-    inner_type = out.get("type")
-    if isinstance(inner_type, str):
-        out["type"] = [inner_type, "null"]
-    elif isinstance(inner_type, list) and "null" not in inner_type:
-        out["type"] = [*inner_type, "null"]
-    if isinstance(out.get("enum"), list) and None not in out["enum"]:
-        out["enum"] = [*out["enum"], None]
+    out = {k: v for k, v in node.items() if k != "anyOf"}
+    if "description" in out:
+        # The field's description, not an inlined model's docstring: a host
+        # that folds the union (the AI SDK) would let the branch's win.
+        inner = {k: v for k, v in inner.items() if k != "description"}
+    if "default" in out and out["default"] is None:
+        del out["default"]
+    out["anyOf"] = [inner, dict(_NULL_SCHEMA)]
     return out
+
+
+def _is_nullable_union(schema: Any) -> bool:
+    any_of = schema.get("anyOf") if isinstance(schema, dict) else None
+    return isinstance(any_of, list) and len(any_of) == 2 and any_of[1] == _NULL_SCHEMA
+
+
+def _rerooted(
+    parent: jsonschema.ValidationError, child: jsonschema.ValidationError
+) -> jsonschema.ValidationError:
+    """``child`` (an error inside ``parent``'s ``anyOf``) as a top-level error."""
+    message, validator, validator_value, schema = (
+        child.message,
+        child.validator,
+        child.validator_value,
+        child.schema,
+    )
+    if child.validator == "type" and not child.relative_path:
+        # The value is the wrong type for X; it is not null either. Spelled as
+        # the ``type: [X, "null"]`` keyword spelled it, schema included, so the
+        # error ranks (``_matches_type``) as that one did.
+        types = [child.validator_value, "null"]
+        message = f"{child.instance!r} is not of type {', '.join(map(repr, types))}"
+        validator_value = types
+        schema = {**child.schema, "type": types}
+    return jsonschema.ValidationError(
+        message,
+        validator=validator,
+        validator_value=validator_value,
+        instance=child.instance,
+        schema=schema,
+        path=deque([*parent.absolute_path, *child.relative_path]),
+        schema_path=deque([*parent.absolute_schema_path, *child.relative_schema_path]),
+        context=list(child.context),
+        type_checker=child._type_checker,
+    )
+
+
+def _unfolded(error: jsonschema.ValidationError) -> list[jsonschema.ValidationError]:
+    """``error``, or -- for a non-null value a nullable union refused -- the
+    errors its value branch produced, in order, as top-level errors."""
+    if (
+        error.validator != "anyOf"
+        or not _is_nullable_union(error.schema)
+        or error.instance is None
+    ):
+        return [error]
+    return [
+        unfolded
+        for child in error.context
+        if child.relative_schema_path[0] == 0  # the value branch, not the null one
+        for unfolded in _unfolded(_rerooted(error, child))
+    ]
+
+
+def gate_error_for(
+    instance: Any, schema: dict[str, Any]
+) -> jsonschema.ValidationError | None:
+    """The error the gate reports for ``instance``, or ``None`` if it passes.
+
+    ``anyOf: [X, {"type": "null"}]`` refuses a bad value as a whole ("... is
+    not valid under any of the given schemas"). Before ``best_match`` ranks
+    the errors, each such refusal is replaced by the errors ``X`` produced,
+    in place: so the gate picks the same error, with the same message,
+    keyword and path, as under the ``type: [X, "null"]`` spelling
+    (Consiliency/pmcp#369) -- the one difference being that an ``enum``'s
+    message no longer lists ``None``. Which values pass is decided by the
+    validator alone and is unchanged. Any failure here falls back to the
+    unfolded errors, so a refusal is never lost.
+    """
+    errors = list(GATE_VALIDATOR(schema).iter_errors(instance))
+    if not errors:
+        return None
+    try:
+        ranked = [unfolded for error in errors for unfolded in _unfolded(error)]
+    except Exception:  # noqa: BLE001 -- never lose a refusal to the fold
+        ranked = errors
+    return best_match(ranked) or best_match(errors)
+
+
+def validate_at_gate(instance: Any, schema: dict[str, Any]) -> None:
+    """``jsonschema.validate`` as the transport gate runs it: raise the error
+    :func:`gate_error_for` chooses. Every caller that reports a gate refusal
+    (the server, the migration guide's checker) goes through this."""
+    GATE_VALIDATOR.check_schema(schema)  # as ``jsonschema.validate`` does
+    error = gate_error_for(instance, schema)
+    if error is not None:
+        raise error
diff --git a/tests/test_gateway_tool_schemas.py b/tests/test_gateway_tool_schemas.py
index 4986c06..11c4e3e 100644
--- a/tests/test_gateway_tool_schemas.py
+++ b/tests/test_gateway_tool_schemas.py
@@ -116,12 +116,10 @@ def _object_schemas(schema: dict[str, Any]) -> list[dict[str, Any]]:
 
     def walk(node: Any) -> None:
         if isinstance(node, dict):
-            kind = node.get("type")
-            # An optional nested object is typed ["object", "null"] (A1).
-            is_object = kind == "object" or (
-                isinstance(kind, list) and "object" in kind
-            )
-            if is_object and "properties" in node:
+            # An optional nested object is the first branch of
+            # `anyOf: [{"type": "object", ...}, {"type": "null"}]`
+            # (Consiliency/pmcp#369); the walk reaches it through `anyOf`.
+            if node.get("type") == "object" and "properties" in node:
                 found.append(node)
             for key, value in node.items():
                 if key == "properties" and isinstance(value, dict):
@@ -225,7 +223,9 @@ def test_advertised_schema_is_a_self_contained_mcp_input_schema(name: str) -> No
     assert not keywords & {"$ref", "$defs", "title"}, keywords
     for obj in _object_schemas(schema):
         for prop_name, prop in obj["properties"].items():
-            assert "anyOf" not in prop, f"{name}.{prop_name}: nullable anyOf survived"
+            assert not isinstance(prop.get("type"), list), (
+                f"{name}.{prop_name}: a type array is not portable (Consiliency/pmcp#369)"
+            )
             assert isinstance(prop.get("description"), str) and prop["description"], (
                 f"{name}.{prop_name}: every advertised argument needs a description"
             )
@@ -276,7 +276,7 @@ def test_input_schema_for_normalises_pydantic_output() -> None:
         "properties": {
             "name": {"type": "string", "minLength": 1, "description": "Name"},
             "title": {
-                "type": ["string", "null"],
+                "anyOf": [{"type": "string"}, {"type": "null"}],
                 "description": "A field called title",
             },
             "count": {
@@ -287,19 +287,28 @@ def test_input_schema_for_normalises_pydantic_output() -> None:
                 "description": "Count",
             },
             "inner": {
-                "type": ["object", "null"],
+                "anyOf": [
+                    {
+                        "type": "object",
+                        "properties": {
+                            "level": {
+                                "anyOf": [
+                                    {"type": "string", "enum": ["a", "b"]},
+                                    {"type": "null"},
+                                ],
+                                "description": "Level",
+                            }
+                        },
+                    },
+                    {"type": "null"},
+                ],
                 "description": "Inner",
-                "properties": {
-                    "level": {
-                        "type": ["string", "null"],
-                        "enum": ["a", "b", None],
-                        "description": "Level",
-                    }
-                },
             },
             "_meta": {
-                "type": ["object", "null"],
-                "additionalProperties": True,
+                "anyOf": [
+                    {"type": "object", "additionalProperties": True},
+                    {"type": "null"},
+                ],
                 "description": "Meta",
             },
         },
@@ -311,8 +320,8 @@ class _RequiredNullable(BaseModel):
 
 
 def test_required_nullable_field_is_left_as_pydantic_wrote_it() -> None:
-    """The collapse is defined for `X | None = None` only; a required nullable
-    field has no `default: null` and keeps its `anyOf`."""
+    """A required nullable field has no `default: null`; it gets the same
+    `anyOf: [X, {"type": "null"}]` an optional one does, and stays required."""
     prop = input_schema_for(_RequiredNullable)["properties"]["value"]
     assert prop == {
         "anyOf": [{"type": "string"}, {"type": "null"}],
@@ -573,6 +582,9 @@ def _unbounded_integers(node: Any, path: str = "") -> list[str]:
         kinds = kind if isinstance(kind, list) else [kind]
         if "integer" in kinds and ("minimum" not in node or "maximum" not in node):
             found.append(path or "<root>")
+        for branch in node.get("anyOf") or []:
+            # A nullable integer is `anyOf: [{"type": "integer", ...}, null]`.
+            found += _unbounded_integers(branch, path)
         for name, prop in (node.get("properties") or {}).items():
             found += _unbounded_integers(prop, f"{path}.{name}")
         if isinstance(node.get("items"), dict):
diff --git a/tests/test_migration_doc.py b/tests/test_migration_doc.py
index 26a0d38..02b0ed5 100644
--- a/tests/test_migration_doc.py
+++ b/tests/test_migration_doc.py
@@ -588,17 +588,13 @@ def _matches_exact(message: str) -> bool:
 
 
 def _gate_error(call: dict[str, Any]) -> jsonschema.ValidationError | None:
-    from pmcp.server import GATE_VALIDATOR
     from pmcp.tools.handlers import get_gateway_tool_definitions
+    from pmcp.tools.schema import gate_error_for
 
     tool = next(t for t in get_gateway_tool_definitions() if t.name == call["name"])
-    try:
-        jsonschema.validate(
-            instance=call["arguments"], schema=tool.input_schema, cls=GATE_VALIDATOR
-        )
-    except jsonschema.ValidationError as error:
-        return error
-    return None
+    # The error the server reports, not jsonschema's raw choice: a nullable
+    # argument's refusal is folded first (Consiliency/pmcp#369).
+    return gate_error_for(call["arguments"], tool.input_schema)
 
 
 def _check_gate(text: str) -> list[str]:
diff --git a/tests/test_nullable_schema_portability.py b/tests/test_nullable_schema_portability.py
new file mode 100644
index 0000000..8e3c48d
--- /dev/null
+++ b/tests/test_nullable_schema_portability.py
@@ -0,0 +1,862 @@
+"""Advertised nullable arguments survive host schema converters (Consiliency/pmcp#369).
+
+Since Consiliency/pmcp#300 an optional argument was advertised as
+``type: [X, "null"]`` and, for an enum, with ``null`` as an ``enum`` member.
+OpenCode's Google provider stringifies every ``enum`` member, so ``null``
+reached Gemini as the string ``"null"``, and the AI SDK's OpenAPI conversion
+turned each type array into a typeless node whose ``nullable`` it dropped.
+
+The advertised spelling is now ``anyOf: [X, {"type": "null"}]``. These tests
+pin three things, each derived over every tool and argument rather than
+listed:
+
+* the structural invariant: ``null`` appears only as that second branch --
+  never as a JSON ``null`` value (``enum``, ``const``, ``default``), never in
+  a ``type`` array -- and the nullable sites are exactly the model fields
+  that admit ``None``;
+* the gate: an explicit ``null`` is accepted at every nullable site, and the
+  string ``"null"`` is refused wherever it is not a legitimate value;
+* the hosts: the advertised schemas run through a port of OpenCode
+  1.18.34's Google conversion chain, and through its newer ``GeminiToolSchema``
+  converter, keep every optional argument omittable and nullable and turn
+  nothing into a string ``"null"``.
+
+The ports are transcribed from the minified JavaScript bundled in the
+``opencode-linux-x64`` 1.18.34 binary (``strings`` byte offsets ~11331089 for
+``ProviderTransform.schema``'s google branch, ~31666960 for the AI SDK's
+``convertJSONSchemaToOpenAPISchema``, ~17674026 for ``GeminiToolSchema``).
+They prove the shape that reaches the Gemini API, not that the API accepts
+it: no live Gemini call was made.
+"""
+
+from __future__ import annotations
+
+import math
+import typing
+from copy import deepcopy
+from typing import Any
+
+import jsonschema
+import pytest
+from pydantic import BaseModel
+
+from pmcp.server import GatewayServer
+from pmcp.tools.handlers import GATEWAY_TOOL_INPUT_MODELS, get_gateway_tool_definitions
+from pmcp.tools.schema import GATE_VALIDATOR
+
+from tests.test_gateway_tool_schemas import MINIMAL_VALID_ARGUMENTS
+
+NULL_BRANCH = {"type": "null"}
+TOOLS = {tool.name: tool for tool in get_gateway_tool_definitions()}
+TOOL_NAMES = list(TOOLS)
+
+Path = tuple[str, ...]
+
+
+# --- deriving the nullable sites -----------------------------------------------
+
+
+def _admits_none(annotation: Any) -> bool:
+    return (
+        annotation is None
+        or annotation is type(None)
+        or any(_admits_none(arg) for arg in typing.get_args(annotation))
+    )
+
+
+def _nested_models(annotation: Any) -> list[type[BaseModel]]:
+    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
+        return [annotation]
+    if typing.get_origin(annotation) in (dict, list):
+        return []  # a container of models is not an argument path
+    return [m for arg in typing.get_args(annotation) for m in _nested_models(arg)]
+
+
+def _model_nullable_paths(model: type[BaseModel], prefix: Path = ()) -> set[Path]:
+    """Every argument path whose model field admits ``None``."""
+    found: set[Path] = set()
+    for name, info in model.model_fields.items():
+        path = (*prefix, info.alias or name)
+        if _admits_none(info.annotation):
+            found.add(path)
+        for nested in _nested_models(info.annotation):
+            found |= _model_nullable_paths(nested, path)
+    return found
+
+
+def _object_branch(node: dict[str, Any]) -> dict[str, Any]:
+    """The schema that describes a non-null value: a union's first branch
+    that is not ``null`` (whatever the branch order or spelling)."""
+    for branch in node.get("anyOf") or []:
+        if isinstance(branch, dict) and branch.get("type") not in (None, "null"):
+            return branch
+    return node
+
+
+def _schema_nullable_sites(schema: dict[str, Any]) -> dict[Path, dict[str, Any]]:
+    """Every argument path whose advertised schema admits ``null``."""
+    sites: dict[Path, dict[str, Any]] = {}
+
+    def walk(node: dict[str, Any], prefix: Path) -> None:
+        for name, prop in (node.get("properties") or {}).items():
+            path = (*prefix, name)
+            if NULL_BRANCH in (prop.get("anyOf") or []):
+                sites[path] = prop
+            value = _object_branch(prop)
+            if value.get("type") == "object":
+                walk(value, path)
+
+    walk(schema, ())
+    return sites
+
+
+def _all_sites() -> list[tuple[str, Path]]:
+    """From the models, not the schemas: a site the schema forgot to make
+    nullable is still tested (and red), whatever the spelling."""
+    return [
+        (name, path)
+        for name in TOOL_NAMES
+        if (model := GATEWAY_TOOL_INPUT_MODELS[name]) is not None
+        for path in sorted(_model_nullable_paths(model))
+    ]
+
+
+SITES = _all_sites()
+
+
+def _advertised_node(name: str, path: Path) -> dict[str, Any]:
+    node = TOOLS[name].input_schema
+    for key in path:
+        node = (_object_branch(node).get("properties") or {}).get(key) or {}
+    return node
+
+
+def _value_schema(node: dict[str, Any]) -> dict[str, Any]:
+    """What a non-null value must match, in either spelling (the pre-#369
+    ``type: [X, "null"]`` one too, so these tests also run on main)."""
+    if "anyOf" in node:
+        return _object_branch(node)
+    if isinstance(node.get("type"), list):
+        out = {**node, "type": [t for t in node["type"] if t != "null"][0]}
+        if "enum" in out:
+            out["enum"] = [v for v in out["enum"] if v is not None]
+        return out
+    return node
+
+
+def _with(arguments: dict[str, Any], path: Path, value: Any) -> dict[str, Any]:
+    out = deepcopy(arguments)
+    node = out
+    for key in path[:-1]:
+        node = node.setdefault(key, {})
+    node[path[-1]] = value
+    return out
+
+
+def _walk(
+    node: Any, path: tuple[Any, ...] = ()
+) -> typing.Iterator[tuple[tuple[Any, ...], Any]]:
+    yield path, node
+    if isinstance(node, dict):
+        for key, value in node.items():
+            yield from _walk(value, (*path, key))
+    elif isinstance(node, list):
+        for index, value in enumerate(node):
+            yield from _walk(value, (*path, index))
+
+
+# --- 1. the structural invariant ----------------------------------------------
+
+
+def test_sites_were_derived() -> None:
+    """The four enums Consiliency/pmcp#369 names are among the derived sites."""
+    assert len(SITES) >= 40, len(SITES)
+    for site in [
+        ("gateway.catalog_search", ("filters", "risk_max")),
+        ("gateway.refresh", ("source",)),
+        ("gateway.set_startup_policy", ("source",)),
+        ("gateway.sync_environment", ("platform",)),
+    ]:
+        assert site in SITES
+
+
+@pytest.mark.parametrize("name", TOOL_NAMES)
+def test_null_is_only_ever_a_type_branch(name: str) -> None:
+    """No JSON ``null`` anywhere in an advertised schema (``enum``, ``const``,
+    ``default``, ``examples`` ...), no ``type`` array, and every
+    ``{"type": "null"}`` is the second of exactly two ``anyOf`` branches whose
+    first branch has one scalar ``type``."""
+    schema = TOOLS[name].input_schema
+    problems: list[str] = []
+    for path, node in _walk(schema):
+        if node is None:
+            problems.append(f"JSON null at {path}")
+        if not isinstance(node, dict):
+            continue
+        if isinstance(node.get("type"), list):
+            problems.append(f"type array at {path}: {node['type']}")
+        if node.get("type") == "null":
+            parent = path[:-2]
+            any_of = schema
+            for key in parent:
+                any_of = any_of[key]
+            ok = (
+                path[-2:] == ("anyOf", 1)
+                and isinstance(any_of.get("anyOf"), list)
+                and len(any_of["anyOf"]) == 2
+                and isinstance(any_of["anyOf"][0].get("type"), str)
+                and any_of["anyOf"][0]["type"] != "null"
+            )
+            if not ok:
+                problems.append(f"null branch outside [X, null] at {path}")
+        for keyword in ("oneOf", "allOf", "not", "const", "nullable"):
+            if keyword in node and path[-1:] != ("properties",):  # not a NAME
+                problems.append(f"non-portable {keyword!r} at {path}")
+    assert problems == [], problems
+
+
+@pytest.mark.parametrize(
+    "name", [n for n in TOOL_NAMES if GATEWAY_TOOL_INPUT_MODELS[n]]
+)
+def test_nullable_sites_are_exactly_the_fields_that_admit_none(name: str) -> None:
+    """The class, from the models: every field that admits ``None`` -- top
+    level or nested -- is advertised nullable, and nothing else is."""
+    model = GATEWAY_TOOL_INPUT_MODELS[name]
+    assert model is not None
+    advertised = set(_schema_nullable_sites(TOOLS[name].input_schema))
+    assert advertised == _model_nullable_paths(model)
+
+
+# --- 2. the gate --------------------------------------------------------------
+
+
+def _gate_accepts(name: str, arguments: dict[str, Any]) -> bool:
+    try:
+        jsonschema.validate(arguments, TOOLS[name].input_schema, cls=GATE_VALIDATOR)
+    except jsonschema.ValidationError:
+        return False
+    return True
+
+
+@pytest.mark.parametrize(("name", "path"), SITES)
+def test_gate_accepts_explicit_null_at_every_nullable_site(
+    name: str, path: Path
+) -> None:
+    """Consiliency/pmcp#300's promise, per site: ``null`` passes the gate."""
+    assert _gate_accepts(name, _with(MINIMAL_VALID_ARGUMENTS[name], path, None))
+
+
+@pytest.mark.parametrize(
+    ("name", "path"),
+    [
+        (name, path)
+        for name, path in SITES
+        if _value_schema(_advertised_node(name, path)).get("type") != "string"
+        or "enum" in _value_schema(_advertised_node(name, path))
+    ],
+)
+def test_gate_refuses_the_string_null_where_it_is_not_a_value(
+    name: str, path: Path
+) -> None:
+    """``"null"`` is not ``null``: refused at every enum and every non-string
+    nullable site. (A free-form string argument legitimately takes it.)"""
+    assert not _gate_accepts(name, _with(MINIMAL_VALID_ARGUMENTS[name], path, "null"))
+
+
+ENUM_SITES = [
+    (name, path)
+    for name, path in SITES
+    if "enum" in _value_schema(_advertised_node(name, path))
+]
+
+
+@pytest.mark.parametrize(("name", "path"), ENUM_SITES)
+@pytest.mark.asyncio
+async def test_server_gate_on_every_nullable_enum(
+    name: str, path: Path, monkeypatch: pytest.MonkeyPatch
+) -> None:
+    """End to end through ``_handle_call_tool``: ``null`` reaches the handler,
+    ``"null"`` is an ``Input validation error`` and never does."""
+    from mcp.types import CallToolRequestParams
+
+    srv = GatewayServer()
+    seen: list[dict[str, Any]] = []
+
+    async def handler(arguments: dict[str, Any], *_a: Any, **_k: Any) -> dict[str, Any]:
+        seen.append(arguments)
+        return {"ok": True}
+
+    monkeypatch.setattr(srv._gateway_tools, name.removeprefix("gateway."), handler)
+    base = MINIMAL_VALID_ARGUMENTS[name]
+    refused = await srv._handle_call_tool(
+        None,  # type: ignore[arg-type]
+        CallToolRequestParams(name=name, arguments=_with(base, path, "null")),
+    )
+    text = " ".join(getattr(c, "text", "") for c in refused.content)
+    assert refused.is_error is True and text.startswith("Input validation error:"), text
+    assert seen == []
+    await srv._handle_call_tool(
+        None,  # type: ignore[arg-type]
+        CallToolRequestParams(name=name, arguments=_with(base, path, None)),
+    )
+    assert seen == [_with(base, path, None)]
+
+
+# --- 3. the hosts: ports of OpenCode 1.18.34's Gemini conversions -------------
+
+
+def _js_string(value: Any) -> str:
+    """JavaScript's ``String(v)`` for a JSON value."""
+    if value is None:
+        return "null"
+    if value is True:
+        return "true"
+    if value is False:
+        return "false"
+    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
+        return str(int(value))
+    return str(value)
+
+
+def _js_truthy(value: Any) -> bool:
+    """JavaScript truthiness for a JSON value (``[]`` and ``{}`` are truthy)."""
+    if value is None or value is False:
+        return False
+    if isinstance(value, (int, float)) and not isinstance(value, bool):
+        return value != 0 and not (isinstance(value, float) and math.isnan(value))
+    if isinstance(value, str):
+        return value != ""
+    return True
+
+
+def _has_combiner(node: Any) -> bool:
+    return isinstance(node, dict) and any(
+        isinstance(node.get(k), list) for k in ("anyOf", "oneOf", "allOf")
+    )
+
+
+_SCHEMA_KEYS = (
+    "type", "properties", "items", "prefixItems", "enum", "const", "$ref",
+    "additionalProperties", "patternProperties", "required", "not", "if",
+    "then", "else",
+)  # fmt: skip
+
+
+def _is_schema(node: Any) -> bool:
+    return isinstance(node, dict) and (
+        _has_combiner(node) or any(k in node for k in _SCHEMA_KEYS)
+    )
+
+
+def opencode_google_transform(node: Any) -> Any:
+    """``ProviderTransform.schema``, the ``providerID === "google" ||
+    api.id.includes("gemini")`` branch (OpenCode 1.18.34)."""
+    if node is None or not isinstance(node, (dict, list)):
+        return node
+    if isinstance(node, list):
+        return [opencode_google_transform(item) for item in node]
+    out: dict[str, Any] = {}
+    for key, value in node.items():
+        if key == "enum" and isinstance(value, list):
+            out[key] = [_js_string(v) for v in value]
+            if out.get("type") in ("integer", "number"):
+                out["type"] = "string"
+        elif isinstance(value, (dict, list)):
+            out[key] = opencode_google_transform(value)
+        else:
+            out[key] = value
+    if isinstance(out.get("type"), list):
+        has_null = "null" in out["type"]
+        rest = [t for t in out["type"] if t != "null"]
+        if not rest:
+            out["type"] = "null"
+        else:
+            del out["type"]
+            out["anyOf"] = [{"type": t} for t in rest]
+            if has_null:
+                out["nullable"] = True
+    if (
+        out.get("type") == "object"
+        and _js_truthy(out.get("properties"))
+        and isinstance(out.get("required"), list)
+    ):
+        out["required"] = [k for k in out["required"] if k in out["properties"]]
+    if out.get("type") == "array" and not _has_combiner(out):
+        if out.get("items") is None:
+            out["items"] = {}
+        if isinstance(out["items"], dict) and not _is_schema(out["items"]):
+            out["items"]["type"] = "string"
+    if (
+        _js_truthy(out.get("type"))
+        and out["type"] != "object"
+        and not _has_combiner(out)
+    ):
+        out.pop("properties", None)
+        out.pop("required", None)
+    return out
+
+
+def _is_empty_object(node: Any) -> bool:
+    return (
+        isinstance(node, dict)
+        and node.get("type") == "object"
+        and (node.get("properties") is None or len(node["properties"]) == 0)
+        and not _js_truthy(node.get("additionalProperties"))
+    )
+
+
+def ai_sdk_google_openapi(node: Any, is_root: bool = True) -> Any:
+    """``@ai-sdk/google``'s ``convertJSONSchemaToOpenAPISchema`` as bundled."""
+    if node is None:
+        return None
+    if _is_empty_object(node):
+        if is_root:
+            return None
+        if _js_truthy(node.get("description")):
+            return {"type": "object", "description": node["description"]}
+        return {"type": "object"}
+    if isinstance(node, bool):
+        return {"type": "boolean", "properties": {}}
+    out: dict[str, Any] = {}
+    if _js_truthy(node.get("description")):
+        out["description"] = node["description"]
+    if _js_truthy(node.get("required")):
+        out["required"] = node["required"]
+    if _js_truthy(node.get("format")):
+        out["format"] = node["format"]
+    if "const" in node:
+        out["enum"] = [node["const"]]
+    kind = node.get("type")
+    if _js_truthy(kind):
+        if isinstance(kind, list):
+            rest = [t for t in kind if t != "null"]
+            if not rest:
+                out["type"] = "null"
+            else:
+                out["anyOf"] = [{"type": t} for t in rest]
+                if "null" in kind:
+                    out["nullable"] = True
+        else:
+            out["type"] = kind
+    if "enum" in node:
+        out["enum"] = node["enum"]
+    if node.get("properties") is not None:
+        out["properties"] = {
+            k: ai_sdk_google_openapi(v, False) for k, v in node["properties"].items()
+        }
+    items = node.get("items")
+    if _js_truthy(items):
+        out["items"] = (
+            [ai_sdk_google_openapi(i, False) for i in items]
+            if isinstance(items, list)
+            else ai_sdk_google_openapi(items, False)
+        )
+    if _js_truthy(node.get("allOf")):
+        out["allOf"] = [ai_sdk_google_openapi(b, False) for b in node["allOf"]]
+    any_of = node.get("anyOf")
+    if _js_truthy(any_of):
+        if any(isinstance(b, dict) and b.get("type") == "null" for b in any_of):
+            rest = [
+                b
+                for b in any_of
+                if not (isinstance(b, dict) and b.get("type") == "null")
+            ]
+            if len(rest) == 1:
+                converted = ai_sdk_google_openapi(rest[0], False)
+                if isinstance(converted, dict):
+                    out["nullable"] = True
+                    out.update(converted)
+            else:
+                out["anyOf"] = [ai_sdk_google_openapi(b, False) for b in rest]
+                out["nullable"] = True
+        else:
+            out["anyOf"] = [ai_sdk_google_openapi(b, False) for b in any_of]
+    if _js_truthy(node.get("oneOf")):
+        out["oneOf"] = [ai_sdk_google_openapi(b, False) for b in node["oneOf"]]
+    if "minLength" in node:
+        out["minLength"] = node["minLength"]
+    return out
+
+
+def opencode_gemini_request_schema(schema: dict[str, Any]) -> Any:
+    """What OpenCode's Google provider sends as a function's ``parameters``."""
+    return ai_sdk_google_openapi(opencode_google_transform(deepcopy(schema)))
+
+
+def _gts_sanitize(node: Any) -> Any:
+    """``GeminiToolSchema``'s first pass (``Q1``)."""
+    if not isinstance(node, dict):
+        return [_gts_sanitize(i) for i in node] if isinstance(node, list) else node
+    out = {
+        k: ([_js_string(v) for v in value] if k == "enum" and isinstance(value, list) else _gts_sanitize(value))
+        for k, value in node.items()
+    }  # fmt: skip
+    if isinstance(out.get("enum"), list) and out.get("type") in ("integer", "number"):
+        out["type"] = "string"
+    props = out.get("properties")
+    if (
+        out.get("type") == "object"
+        and isinstance(props, dict)
+        and isinstance(out.get("required"), list)
+    ):
+        out["required"] = [
+            r for r in out["required"] if isinstance(r, str) and r in props
+        ]
+    if out.get("type") == "array" and not _has_combiner(out):
+        out["items"] = out.get("items") if out.get("items") is not None else {}
+        if isinstance(out["items"], dict) and not _is_schema(out["items"]):
+            out["items"] = {**out["items"], "type": "string"}
+    if (
+        isinstance(out.get("type"), str)
+        and out["type"] != "object"
+        and not _has_combiner(out)
+    ):
+        out.pop("properties", None)
+        out.pop("required", None)
+    return out
+
+
+def _gts_project(node: Any) -> Any:
+    """``GeminiToolSchema``'s second pass (``ix``)."""
+    if not isinstance(node, dict) or (
+        node.get("type") == "object"
+        and (not isinstance(node.get("properties"), dict) or not node["properties"])
+        and not _js_truthy(node.get("additionalProperties"))
+    ):
+        return None
+    kind = node.get("type")
+    entries = {
+        "description": node.get("description"),
+        "required": node.get("required"),
+        "format": node.get("format"),
+        "type": ([t for t in kind if t != "null"] or [None])[0] if isinstance(kind, list) else kind,
+        "nullable": True if isinstance(kind, list) and "null" in kind else None,
+        "enum": [node["const"]] if "const" in node else node.get("enum"),
+        "properties": (
+            {k: v for k, v in ((k, _gts_project(p)) for k, p in node["properties"].items()) if v is not None}
+            if isinstance(node.get("properties"), dict) else None
+        ),
+        "items": (
+            [_gts_project(i) for i in node["items"]] if isinstance(node.get("items"), list)
+            else None if "items" not in node else _gts_project(node["items"])
+        ),
+        "allOf": [_gts_project(b) for b in node["allOf"]] if isinstance(node.get("allOf"), list) else None,
+        "anyOf": [_gts_project(b) for b in node["anyOf"]] if isinstance(node.get("anyOf"), list) else None,
+        "oneOf": [_gts_project(b) for b in node["oneOf"]] if isinstance(node.get("oneOf"), list) else None,
+        "minLength": node.get("minLength"),
+    }  # fmt: skip
+    return {k: v for k, v in entries.items() if v is not None}
+
+
+def opencode_gemini_tool_schema(schema: dict[str, Any]) -> Any:
+    """OpenCode's newer ``GeminiToolSchema.convert`` (``ix(Q1(x))``)."""
+    return _gts_project(_gts_sanitize(deepcopy(schema)))
+
+
+#: Gemini API ``Schema`` fields and ``Type`` values (ai.google.dev, ``Schema``).
+GEMINI_SCHEMA_FIELDS = {
+    "type", "format", "title", "description", "nullable", "enum", "maxItems",
+    "minItems", "properties", "required", "minProperties", "maxProperties",
+    "minLength", "maxLength", "pattern", "example", "anyOf", "propertyOrdering",
+    "default", "items", "minimum", "maximum",
+}  # fmt: skip
+GEMINI_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}
+
+
+def _converted_site(converted: Any, path: Path) -> dict[str, Any]:
+    node = converted
+    for key in path:
+        node = _object_branch(node)["properties"][key]
+    return node
+
+
+def _enum_strings_null(converted: Any) -> list[tuple[Any, ...]]:
+    return [
+        path
+        for path, node in _walk(converted)
+        if isinstance(node, dict)
+        and isinstance(node.get("enum"), list)
+        and any(v is None or v == "null" for v in node["enum"])
+    ]
+
+
+def test_ports_reproduce_the_captured_defect() -> None:
+    """Fidelity check of the ports against Consiliency/pmcp#369's capture: the
+    pre-fix spelling of ``refresh.source`` reaches Gemini with the STRING
+    ``"null"`` in its enum and its ``null`` branch dropped."""
+    pre_fix = {
+        "type": "object",
+        "properties": {
+            "source": {
+                "description": "Config source to reload from",
+                "enum": ["claude_config", "custom", None],
+                "type": ["string", "null"],
+            }
+        },
+    }
+    sent = opencode_gemini_request_schema(pre_fix)["properties"]["source"]
+    assert sent["enum"] == ["claude_config", "custom", "null"]
+    assert sent["anyOf"] == [{"type": "string"}]
+    assert "nullable" not in sent and "type" not in sent
+    newer = opencode_gemini_tool_schema(pre_fix)["properties"]["source"]
+    assert newer["enum"] == ["claude_config", "custom", "null"]
+
+
+@pytest.mark.parametrize("name", TOOL_NAMES)
+def test_no_string_null_reaches_gemini(name: str) -> None:
+    schema = TOOLS[name].input_schema
+    assert _enum_strings_null(opencode_gemini_request_schema(schema)) == []
+    assert _enum_strings_null(opencode_gemini_tool_schema(schema)) == []
+
+
+@pytest.mark.parametrize("name", TOOL_NAMES)
+def test_gemini_request_schema_is_in_gemini_subset(name: str) -> None:
+    """Every node OpenCode's Google provider sends uses only Gemini ``Schema``
+    fields, has one scalar ``type``, string-only enums, and arrays with
+    ``items`` (Gemini's documented function-declaration subset)."""
+    sent = opencode_gemini_request_schema(TOOLS[name].input_schema)
+    if sent is None:  # a no-argument tool: the AI SDK sends no parameters
+        assert GATEWAY_TOOL_INPUT_MODELS[name] is None
+        return
+    problems: list[str] = []
+
+    def check(node: Any, path: Path) -> None:
+        extra = set(node) - GEMINI_SCHEMA_FIELDS
+        if extra:
+            problems.append(f"{path}: fields {sorted(extra)}")
+        if node.get("type") not in GEMINI_TYPES:
+            problems.append(f"{path}: type {node.get('type')!r}")
+        if "enum" in node and (
+            node.get("type") != "string"
+            or not all(isinstance(v, str) for v in node["enum"])
+        ):
+            problems.append(f"{path}: non-string enum")
+        if node.get("type") == "array" and not isinstance(node.get("items"), dict):
+            problems.append(f"{path}: array without items")
+        for key, prop in (node.get("properties") or {}).items():
+            check(prop, (*path, key))
+        if isinstance(node.get("items"), dict):
+            check(node["items"], (*path, "[]"))
+        for i, branch in enumerate(node.get("anyOf") or []):
+            check(branch, (*path, f"anyOf{i}"))
+
+    check(sent, ())
+    assert problems == [], problems
+
+
+@pytest.mark.parametrize(("name", "path"), SITES)
+def test_every_nullable_argument_stays_nullable_and_omittable_for_gemini(
+    name: str, path: Path
+) -> None:
+    """Per site, through the AI SDK chain: the node is Gemini's canonical
+    ``{type: X, nullable: true}`` carrying X's own keywords (enum, items,
+    properties), and the argument is not in its parent's ``required``."""
+    schema = TOOLS[name].input_schema
+    value = _value_schema(_advertised_node(name, path))
+    sent = opencode_gemini_request_schema(schema)
+    node = _converted_site(sent, path)
+    assert node.get("nullable") is True, node
+    assert node.get("type") == value["type"], node
+    if "enum" in value:
+        assert node["enum"] == value["enum"]
+    if value["type"] == "array":
+        assert isinstance(node.get("items"), dict), node
+    if value.get("properties"):
+        assert set(node.get("properties") or {}) == set(value["properties"]), node
+    parent = _converted_site(sent, path[:-1])
+    assert path[-1] not in (parent.get("required") or [])
+
+    newer = _converted_site(opencode_gemini_tool_schema(schema), path)
+    branches = newer.get("anyOf")
+    assert branches is not None and branches[1] == {"type": "null"}, newer
+    assert branches[0].get("type") == value["type"], newer
+
+
+@pytest.mark.parametrize(
+    "name", [n for n in TOOL_NAMES if GATEWAY_TOOL_INPUT_MODELS[n]]
+)
+def test_gemini_required_is_the_models_required(name: str) -> None:
+    """Every optional argument stays omittable: the converted top-level
+    ``required`` is exactly the model's required fields."""
+    model = GATEWAY_TOOL_INPUT_MODELS[name]
+    assert model is not None
+    sent = opencode_gemini_request_schema(TOOLS[name].input_schema)
+    expected = {
+        (f.alias or n) for n, f in model.model_fields.items() if f.is_required()
+    }
+    assert set(sent.get("required") or []) == expected
+
+
+# --- 4. the gate reports what it reported under the old spelling --------------
+
+
+def _type_array_spelling(node: Any) -> Any:
+    """The advertised schema as Consiliency/pmcp#300 spelled it: each
+    ``anyOf: [X, null]`` folded back to ``X`` with ``"null"`` added to its
+    ``type`` (and ``None`` to its ``enum``). The oracle for the messages."""
+    if isinstance(node, list):
+        return [_type_array_spelling(item) for item in node]
+    if not isinstance(node, dict):
+        return node
+    out = {
+        k: (
+            {name: _type_array_spelling(p) for name, p in v.items()}
+            if k == "properties"
+            else _type_array_spelling(v)
+        )
+        for k, v in node.items()
+    }
+    any_of = out.get("anyOf")
+    if isinstance(any_of, list) and len(any_of) == 2 and any_of[1] == NULL_BRANCH:
+        inner = {**any_of[0], **{k: v for k, v in out.items() if k != "anyOf"}}
+        inner["type"] = [inner["type"], "null"]
+        if "enum" in inner:
+            inner["enum"] = [*inner["enum"], None]
+        return inner
+    return out
+
+
+#: Values of every JSON type, plus ones that break a bound or a length, and
+#: ones with two failures inside one value (two bad items, two bad fields).
+PROBES: list[Any] = [
+    5, 1.5, -1, 0, 10**20, float("nan"), True, "x", "null", "a" * 64 + "\n",
+    [], ["x", 1], [1, 2], {}, {"a": 1}, {"a": 1, "b": 2}, None,
+]  # fmt: skip
+
+
+def _reported(schema: dict[str, Any], arguments: dict[str, Any], *, gate: bool) -> Any:
+    """What a refusal reports: through the gate's selection, or through
+    ``jsonschema.validate``'s own (``best_match`` over the raw errors)."""
+    from jsonschema.exceptions import best_match
+
+    from pmcp.tools.schema import gate_error_for
+
+    if gate:
+        error = gate_error_for(arguments, schema)
+    else:
+        error = best_match(GATE_VALIDATOR(schema).iter_errors(arguments))
+    if error is None:
+        return "accepted"
+    message = error.message
+    if error.validator == "enum" and not gate:
+        message = message.replace(", None]", "]")  # the one intended difference
+    return message, error.validator, list(error.absolute_path)
+
+
+def _assert_reports_match(name: str, arguments: dict[str, Any]) -> None:
+    schema = TOOLS[name].input_schema
+    got = _reported(schema, arguments, gate=True)
+    want = _reported(_type_array_spelling(schema), arguments, gate=False)
+    assert got == want, (name, arguments, got, want)
+
+
+@pytest.mark.parametrize(("name", "path"), SITES)
+def test_gate_reports_what_the_type_array_spelling_reported(
+    name: str, path: Path
+) -> None:
+    """One value at one site, every probe: the gate's message, failing keyword
+    and argument path equal what ``jsonschema`` reported under the pre-#369
+    spelling -- except that an enum's message no longer lists ``None``."""
+    for value in PROBES:
+        _assert_reports_match(name, _with(MINIMAL_VALID_ARGUMENTS[name], path, value))
+
+
+#: Values for the multi-failure grid: wrong scalar, wrong string, two bad
+#: items, two bad fields, null, and the string "null".
+GRID_VALUES: list[Any] = [5, "x", [1, 2], {"a": 1, "b": 2}, None, "null"]
+
+
+@pytest.mark.parametrize("name", sorted({name for name, _ in SITES}))
+def test_gate_reports_what_the_type_array_spelling_reported_for_many_failures(
+    name: str,
+) -> None:
+    """Two or more failures in one call -- at two sites, or two inside one
+    value -- are where ``best_match``'s ranking decides (it takes the LAST
+    sibling at the top level but descends into an ``anyOf`` for the FIRST).
+    Every ordered pair of this tool's sites x every pair of grid values,
+    plus 300 seeded calls with three sites each, agree with the oracle."""
+    import itertools
+    import random
+
+    paths = [path for tool, path in SITES if tool == name]
+    base = MINIMAL_VALID_ARGUMENTS[name]
+    for first, second in itertools.permutations(paths, 2):
+        if first[: len(second)] == second or second[: len(first)] == first:
+            continue  # one is inside the other: the second write replaces it
+        for a, b in itertools.product(GRID_VALUES, repeat=2):
+            _assert_reports_match(name, _with(_with(base, first, a), second, b))
+    rng = random.Random(f"369:{name}")
+    for _ in range(300):
+        arguments = base
+        chosen: list[Path] = []
+        for path in rng.sample(paths, min(3, len(paths))):
+            if any(c[: len(path)] == path or path[: len(c)] == c for c in chosen):
+                continue  # nested in another chosen site
+            chosen.append(path)
+            arguments = _with(arguments, path, rng.choice(PROBES))
+        _assert_reports_match(name, arguments)
+
+
+@pytest.mark.parametrize(
+    ("name", "arguments", "reported"),
+    [
+        (
+            "gateway.sync_environment",
+            {"detected_clis": [1, 2]},
+            ("2 is not of type 'string'", "type", ["detected_clis", 1]),
+        ),
+        (
+            "gateway.request_capability",
+            {"query": "q", "available_clis": [1, 2]},
+            ("2 is not of type 'string'", "type", ["available_clis", 1]),
+        ),
+        (
+            "gateway.invoke",
+            {"tool_id": "a::b", "task": {"enabled": "x", "ttl": "5"}},
+            ("'5' is not of type 'integer', 'null'", "type", ["task", "ttl"]),
+        ),
+        (
+            "gateway.catalog_search",
+            {"filters": {"server": {}, "tags": "x"}},
+            ("'x' is not of type 'array', 'null'", "type", ["filters", "tags"]),
+        ),
+    ],
+)
+def test_round_one_multi_failure_cases(
+    name: str, arguments: dict[str, Any], reported: tuple[Any, ...]
+) -> None:
+    """The four cases the #370 round-1 review measured diverging from 3.0."""
+    assert _reported(TOOLS[name].input_schema, arguments, gate=True) == reported
+
+
+def test_gate_error_for_falls_back_when_the_fold_fails(
+    monkeypatch: pytest.MonkeyPatch,
+) -> None:
+    """A fold that raises never loses the refusal: the raw error is reported."""
+    import pmcp.tools.schema as schema_module
+
+    def boom(error: Any) -> Any:
+        raise KeyError("type")
+
+    monkeypatch.setattr(schema_module, "_unfolded", boom)
+    error = schema_module.gate_error_for(
+        {"source": 5}, TOOLS["gateway.refresh"].input_schema
+    )
+    assert error is not None and list(error.absolute_path) == ["source"]
+
+
+@pytest.mark.asyncio
+async def test_server_message_for_a_wrongly_typed_nullable_argument() -> None:
+    """The 3.0 migration guide's documented case, end to end."""
+    from mcp.types import CallToolRequestParams
+
+    srv = GatewayServer()
+    result = await srv._handle_call_tool(
+        None,  # type: ignore[arg-type]
+        CallToolRequestParams(
+            name="gateway.invoke",
+            arguments={"tool_id": "a::b", "task": {"enabled": True, "ttl": "5"}},
+        ),
+    )
+    text = " ".join(getattr(c, "text", "") for c in result.content)
+    assert text == "Input validation error: '5' is not of type 'integer', 'null'"
diff --git a/tests/test_scoped_advisor_audit.py b/tests/test_scoped_advisor_audit.py
index edf4db0..5496af0 100644
--- a/tests/test_scoped_advisor_audit.py
+++ b/tests/test_scoped_advisor_audit.py
@@ -1485,8 +1485,12 @@ def _open_containers(tool: Any, baseline: dict[str, Any]) -> list[str]:
     """Declared object-typed keys the gate lets generated content into."""
     containers = []
     for name, prop in sorted((tool.input_schema.get("properties") or {}).items()):
-        types = prop.get("type")
-        types = types if isinstance(types, list) else [types]
+        # A nullable object is `anyOf: [{"type": "object", ...}, {"type": "null"}]`
+        # (Consiliency/pmcp#369); a plain one is `{"type": "object", ...}`.
+        types = []
+        for schema in [prop, *(prop.get("anyOf") or [])]:
+            kind = schema.get("type")
+            types += kind if isinstance(kind, list) else [kind]
         if "object" not in types:
             continue
         probe = {**baseline, name: _mixed(_generated_keys(set(), _TAGS[1]), _TAGS[1])}
@@ -1498,6 +1502,22 @@ def _open_containers(tool: Any, baseline: dict[str, Any]) -> list[str]:
     return containers
 
 
+def test_open_containers_see_every_nullable_object_argument() -> None:
+    """The sweep below injects generated keys into every container this finds;
+    a walker that stops seeing nullable objects (their spelling changed in
+    Consiliency/pmcp#369) would shrink that sweep silently, not fail it."""
+    tools = _gateway_tools_by_name()
+    found = {
+        name: _open_containers(tools[name], _declared_baseline(tools[name]))
+        for name in ("gateway.invoke", "gateway.tasks_result", "gateway.tasks_get")
+    }
+    assert found == {
+        "gateway.invoke": ["_meta", "arguments", "options", "task", "trace_context"],
+        "gateway.tasks_result": ["options", "requestor_context"],
+        "gateway.tasks_get": ["requestor_context"],
+    }
+
+
 #: Constructor arguments for raised exception classes whose `__init__` needs
 #: more than a message. A new such class fails `_raised_exceptions` loudly.
 _EXCEPTION_ARGS: dict[str, tuple[Any, ...]] = {
```
