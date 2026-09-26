# Detailed plan: derive gateway `inputSchema`s from their argument models, then forbid unknown keys

> **Revision 2 (2026-09-26): A1/A2/A3/X1 from the board.** Piece A is now
> *embedded*, not described: the five code blocks under *Verbatim bodies → A*
> (`schema.py` and the test module as whole files; `types.py`, `handlers.py`
> and `server.py` as `git apply` patches against `origin/main` @ `9ca081e`)
> plus the generated snapshot are byte-identical to the frozen, verified
> piece-A code (`wip/236-schema-drift-rev2-code` @ `c79bf1a`; ancestors
> `a25dce0`, `972bc90`, `40b2ed5`, `d0722f4`, `72eaa76`). `a25dce0` differed
> from `972bc90` only in the test module's docstring. `c79bf1a` adds board
> round 4's F1 fix (a `le=` bound on `task.ttl`, a test, a docstring line, +1
> snapshot line). Full suite, mutants and gates were re-measured at
> `c79bf1a`. Proven by applying them to a fresh `origin/main`
> worktree and running `cmp` on all six files (see *Embedding proof*). The six
> files are byte-identical between `860636a`, `8dec131` and `9ca081e`, so
> every "HEAD" measurement below still describes `main`. What changed:
> - **A1 (blocking):** `_collapse_nullable` now adds `"null"` to the collapsed
>   `type` (and `None` to `enum`), so the gate accepts explicit `null` exactly
>   where the model does. Pinned per field by
>   `test_optional_field_null_agrees_between_gate_and_model` (42 cases). This
>   *loosens* the gate on 28 fields that `main` rejected (see *A is not fully
>   behaviour-neutral*).
> - **A2 (blocking):** schemas are derived once per process
>   (`@functools.cache _derived_gateway_tools`), pinned by call count in
>   `test_schemas_are_derived_once_per_process`.
> - **A3:** the scoped-audit gap on gate rejections is attributed correctly: it
>   pre-exists on `main`, A moves more rejections onto it, B adds more.
>   Scoped out to **Consiliency/pmcp#296**, which lands before B. Under A, a
>   malformed call to a policy-blocked tool is no longer recorded as `denied`
>   (measured).
> - **X1 (blocking for B), now structural (`40b2ed5`):** `call_tool` raises
>   `Unknown tool` for any name the registry does not list, *before* the
>   dispatch chain and inside the audited path
>   (`test_a_dispatch_branch_for_an_unregistered_name_fails_closed`, mutant
>   M-X1s). The text pin `test_every_dispatched_gateway_name_is_registered`
>   stays as a second line of defence. The board showed it can be bypassed on
>   its own. B depends on the guard.
> - **Board round 4 (`b308e57`) finding F1 folded in (code at `c79bf1a`):** a
>   sixth gate/model disagreement class, **integer range**. `task.ttl` was the
>   only integer property with no `maximum` (measured). The gate accepted
>   `1e20`, and the model refused it with `int_parsing_size`, echoing the
>   value, so the gate was the *looser* layer (on `main` too). `c79bf1a` adds
>   `le=9_223_372_036_854_775_807` (int64), and both layers now reject it
>   (new test, mutant M-F1). Side effect, measured: a JSON **integer** above
>   int64 for `task.ttl` was accepted by the model on `main` and now is not.
>   See *A is not fully behaviour-neutral*.
> - **Board round 3 (`3481bbc`) findings N1–N4 folded in (code at `972bc90`):**
>   N1, the X1 guard now runs *before* the scoped-audit
>   `InvokeInput.model_validate` (new test, mutant M-N1; B must keep that
>   order). N2, `evidence_label_digest` gains `min_length=64, max_length=64`,
>   which closes a fifth gate/model disagreement class, regex dialect (new
>   test, mutant M-N2; it is the only `pattern` in any gateway model,
>   measured). N3, the F3 follow-up is Consiliency/pmcp#297, and the `meta`
>   echo is pre-existing on `main`, not A-only. N4, A's CHANGELOG states the
>   policy-blocked audit consequence that ships until
>   Consiliency/pmcp#296 lands.
> - **Board round 2 (`28ae22d`) findings F1–F5 folded in:** lax-coercion
>   disagreement (F1, 11 new `invoke.task` rejections under A, in
>   *A is not fully behaviour-neutral* and A's CHANGELOG); X1 structural (F2);
>   the gate-*passing* pydantic echo path (F3, in *Order: A first, then B*);
>   host dialect support for `type: [X, "null"]` named as an unmeasured risk
>   (F4); A3 filed and the policy-blocked consequence measured (F5).
> - The "jsonschema echoes only the key" claim is corrected: `type`, `enum` and
>   `pattern` errors echo the value (measured on `main`).
> - X2: the handler-link test already used a word-boundary regex in the frozen
>   code.
> - Counts re-measured on the frozen code: **213** schema tests at `c79bf1a`
>   (211 at `972bc90`/`a25dce0`, 209 at `40b2ed5`, 208 at `72eaa76`/`d0722f4`).
>   Full suite at `c79bf1a`: `4277 passed, 3 skipped, 25 deselected` in 6:53, with no errors (measured here, and by the coordinator). **On current `main` + A** (the
>   board seat's assembled `9ca081e` + `a25dce0` tree): `4282 passed, 3
>   skipped, 25 deselected`, i.e. the plan's number plus exactly the 7 newer
>   C3 tests on `main` (`tests/test_client_manager.py` collects 256 on `main`
>   + A vs 249 on the A branch, whose base is `8dec131`). That is the seat's
>   measurement and was not re-run here. Full suite at `972bc90`: `4275 passed, 3 skipped, 25 deselected` in 8:21, with no errors (measured here, and by the coordinator). At `40b2ed5`: `4273 passed, 3 skipped, 25 deselected` in 6:59, with no errors (measured here, and by the coordinator).
>   Earlier: `4272 passed, 3 skipped, 25 deselected` (measured by this planner
>   on `72eaa76`, and by the coordinator on `d0722f4`). The `d0722f4` run also hit 1 teardown error in
>   `test_workflow_guards`, because the host's live gateway restarted a
>   firecrawl child mid-run. That is environmental: the file passes 194/194 on
>   this tree and on `main`. Snapshot 814 lines (813 before F1, 811 before N2). Probe reports 23/26 tools
>   differing from `main` (18 constraint drift + 5 null-only). Every piece-B
>   number is from the revision-1 tree and is marked for re-measurement.
> - **Descriptions (resolved in-revision).** `72eaa76` kept the model text in
>   all 15 description conflicts, contrary to revision 1's rule. The code was
>   corrected to option (b) at `d0722f4`: the 14 conflicts use `main`'s
>   hand-written text, and `update_server.force` keeps the model text.
>   Measured with `desc_cmp.py`: `same 54 differ 1` (the one is
>   `update_server.force`).
> - **Walker (fixed in A at `d0722f4`).** The test helper `_object_schemas`
>   now also walks objects typed `["object", "null"]` (31/31 object schemas).
> - The board's `search_registry.available_clis` does not exist. The field is
>   `request_capability.available_clis`, and `main`'s gate *rejects* `null`
>   there.
>
> Resumes an unfinished spike from a planner whose session died. Everything
> below marked **measured** was run in this session against `860636a`
> (`origin/main` at planning time) or against the spike tree described in
> *Verdict on the inherited spike*. Nothing is carried over unverified.

## Task

Address Consiliency/pmcp#236 (C-10 / M-02): the 26 hand-written `Tool(input_schema={...})`
dicts in `src/pmcp/tools/handlers.py` drift from the pydantic models the handlers
actually validate with, and unknown argument keys are silently ignored. pmcp
brokers untrusted MCP servers for a prompt-injectable agent; the gateway's own
tools are that agent's API, and "accept a hallucinated key, proceed with
different behaviour" is the wrong default for that audience.

The fix lands as **two separately-mergeable pieces, A then B**:

- **A — kill the drift by construction.** Each advertised `inputSchema` is
  derived from the model its handler validates with; a test pins every link in
  that chain. Behaviour-preserving at the model layer. At the transport gate it
  starts rejecting what the model already rejected, and (revision 2, A1) accepts
  explicit `null` where the model already accepted it (see *A is not fully
  behaviour-neutral*). Schemas are derived once per process (A2).
- **B — `extra="forbid"` on every argument model.** A behaviour change for
  callers that send extra keys. Own CHANGELOG entry, release-note callout,
  measured blast radius.

## Research summary (all measured this session)

### The two validation layers, and where drift bites

`tools/call` goes through `GatewayServer._handle_call_tool` (`src/pmcp/server.py:283-309`),
which **already runs `jsonschema.validate(arguments, tool.input_schema)` against
the advertised schema before dispatch** and returns
`CallToolResult(is_error=True, "Input validation error: …")` on failure. Only
then does `call_tool` dispatch to `GatewayTools.<method>(arguments)`, which runs
`<Model>.model_validate(arguments)`; a pydantic `ValidationError` there is caught
by the generic `except Exception` at `server.py:428` and returned as
`{"error": true, "message": str(e)[:400]}` — *not* `is_error`, and with
`logger.error(f"Tool execution error: {e}")`.

So "drift" concretely means the gate and the model disagree, and the caller sees
a different error shape depending on which layer catches it. The issue framed
this as documentation drift; it is also an enforcement inconsistency.

### Tool ↔ model inventory (the lead's "~10 models / ~16 unmodelled" premise is wrong)

**26 tools, 23 dispatched through a model, 3 argless. There are zero tools that
take arguments and have no model.** Measured with
`scratchpad/probe_head.py` against `860636a` (re-run 2026-09-26 against
`9ca081e`: first line still `tools: 26 modelled: 23`):

| Tool | Model (`src/pmcp/types.py`) | Nested argument models |
|---|---|---|
| `gateway.catalog_search` | `CatalogSearchInput` | `CatalogFilters` |
| `gateway.describe` | `DescribeInput` | |
| `gateway.invoke` | `InvokeInput` | `InvokeOptions`, `TaskMetadataInput`, `TraceContextInfo` |
| `gateway.refresh` | `RefreshInput` | |
| `gateway.connect_server` | `ConnectServerInput` | |
| `gateway.disconnect_server` | `DisconnectServerInput` | |
| `gateway.restart_server` | `RestartServerInput` | |
| `gateway.set_startup_policy` | `StartupPolicyOperation` | |
| `gateway.request_capability` | `CapabilityRequestInput` | |
| `gateway.sync_environment` | `SyncEnvironmentInput` | |
| `gateway.provision` | `ProvisionInput` | |
| `gateway.update_server` | `UpdateServerInput` | |
| `gateway.auth_connect` | `AuthConnectInput` | |
| `gateway.submit_feedback` | `SubmitFeedbackInput` | |
| `gateway.provision_status` | `ProvisionStatusInput` | |
| `gateway.list_pending` | `ListPendingInput` | |
| `gateway.cancel` | `CancelInput` | |
| `gateway.tasks_list` | `TasksListInput` | |
| `gateway.tasks_get` | `TasksGetInput` | |
| `gateway.tasks_result` | `TasksResultInput` | `InvokeOptions` |
| `gateway.tasks_cancel` | `TasksCancelInput` | |
| `gateway.search_registry` | `SearchRegistryInput` | |
| `gateway.register_discovered_server` | `RegisterDiscoveredServerInput` | |
| `gateway.health`, `gateway.config_status`, `gateway.get_startup_policy` | **none** — `server.py:351-356` calls `health()`, `config_status()`, `get_startup_policy()` with no arguments; extra keys are dropped at dispatch, not by pydantic | |

The "create models for the unmodelled tools or scope out" decision therefore
dissolves. The three argless tools get a shared `NO_ARGUMENTS_SCHEMA` and a
`None` model in the registry; the test suite asserts the registry's `None`
entries are exactly the tools `server.py` dispatches with `()`.

### `extra=` inventory (measured, `probe_head.py`)

- All 23 top-level argument models **and** the 4 nested ones report
  `model_config.get("extra") == None` → pydantic default `ignore`. Runtime
  probe: `DescribeInput.model_validate({"tool_id": "a::b", "bogus_key": 1})`
  → `{'tool_id': 'a::b'}`, no error;
  `InvokeInput.model_validate({"tool_id": "a::b", "options": {"timeoutMs": 5}})`
  → `timeout_ms=30000`, no error (the misspelled key is dropped and the default
  timeout silently applies — the C-10 case exactly).
- **No HEAD schema contains `additionalProperties`** at any depth, so the gate
  accepts `{"tool_id": "a::b", "bogus_key": 1}` for `gateway.describe`,
  `{"bogus_key": true}` for `gateway.health`, and
  `{"options": {"bogus_key": 1}}` for `gateway.invoke` (all three measured).
- The 8 existing `extra="forbid"` are all policy models (`types.py:970-1080`);
  the 3 `extra="ignore"` are `LocalMcpServerConfig`, `RemoteMcpServerConfig`,
  `McpConfigFile` (`types.py:225-260`). **B leaves those three alone**: `.mcp.json`
  is written by Claude Code, Cursor and hand editors; unknown keys there are
  forward-compatibility, not agent input. The base class introduced in A makes
  the boundary explicit (see `GatewayArguments` docstring).

### Drift on HEAD (measured: 18 of 26 tools with constraint drift; with the revision-2 `schema.py` the probe prints 23/26, see below)

Comparing each hand-written schema against the schema derived from its model
(descriptions and `additionalProperties` ignored), **every difference is the
model being stricter or broader than what is advertised** — nowhere does the
hand-written schema state something the model lacks.

**Revision 2 re-run.** With the frozen revision-2 `schema.py` as
`spike_schema.py`, the probe's last line reads
`tools with drift (ignoring descriptions and additionalProperties): 23/26`
(was 18/26 with revision 1's `schema.py`). The extra five are **null-only**:
their only differences are `hand='string' model=["string", "null"]`-style
`type` lines and a trailing `null` in `enum` (A1's collapse). The five are
`catalog_search`, `refresh`, `set_startup_policy`, `sync_environment` and
`list_pending`. The 18 below are unchanged, and in several of them (`invoke`,
`auth_connect`, `submit_feedback`, `tasks_list`, `tasks_result`,
`request_capability`) the same `"null"` lines now appear next to the constraint
drift. The full measured output is under *Measurement scripts*.
The 18 constraint-drift tools:

- `minLength: 1` unadvertised on 20 string arguments across 15 tools
  (`tool_id`, `server_name`, `query`, `job_id`, `request_id`, `task_id`,
  `package`, `credential`).
- `gateway.submit_feedback.title`: model `min_length=8, max_length=160`;
  advertised: plain string.
- `gateway.invoke`: model accepts `task` (5 sub-fields), `trace_context`
  (3 sub-fields) and `_meta`; none advertised. `run_correlation_id` /
  `seat_correlation_id` have `minLength 1 / maxLength 128` unadvertised.
- `gateway.tasks_result.options`: advertised without `timeout_ms` at all and
  with no bounds on `max_output_chars`; the model is `InvokeOptions`
  (`timeout_ms` 1000–300000 default 30000, `max_output_chars` 100–100000).
- `gateway.tasks_list/get/result/cancel`: model accepts `requestor_context`
  (forwarded downstream as `requestorContext`, `client/manager.py:1643`); not advertised.
- **Description drift, 15 arguments**: the model's pre-existing
  `Field(description=…)` text differs from the hand-written text for the same
  argument (e.g. `request_capability.query`: hand-written has the worked
  examples, model says "Natural language capability request";
  `register_discovered_server.server_name`: hand-written says "used with
  gateway.provision", model drops it). Full list in `scratchpad/desc_diff.out`,
  reproduced under *Piece A → `types.py`*. The inherited spike let the model text
  win silently in all 15; this plan does not.

### Is `model_json_schema()` usable verbatim as an MCP `inputSchema`? No.

Measured on `InvokeInput.model_json_schema(by_alias=True, mode="validation")`:

- top-level keys `['$defs', 'description', 'properties', 'required', 'title', 'type']`
  — `$defs` holds `InvokeOptions`, `TaskMetadataInput`, `TraceContextInfo`;
  `options` is `{'anyOf': [{'$ref': '#/$defs/InvokeOptions'}, {'type': 'null'}], 'default': None}`;
  every property carries `'title': 'Tool Id'`-style noise; the model docstring
  becomes a top-level `description` that would shadow the `Tool.description`.
- `run_correlation_id` is `{'anyOf': [{...string...}, {'type': 'null'}], 'default': None, 'title': ...}`.

MCP clients expect self-contained, `$ref`-free object schemas. So
post-processing is required, and **that post-processing is itself drift
surface**: it gets its own module (`src/pmcp/tools/schema.py`, 4 documented
transforms: inline `$defs`/`$ref`, drop `title`, collapse
`anyOf:[X, null] + default:null` → `X` **with `"null"` added to `X`'s `type`
(and `None` to its `enum`)**, drop the docstring description) and its own test
against a synthetic model (`test_input_schema_for_normalises_pydantic_output`),
so a change to the transforms cannot hide behind the real tools' snapshot.
Mutations M4 and M-A1 below prove that test fires.

**Revision 2: optional fields advertise `type: [X, "null"]`, not a bare `X`.**
Revision 1 collapsed to the bare type, which made the gate reject `null` where
the model accepts it (board finding A1). The frozen code keeps the schema flat
(no `anyOf`) but lists `"null"` in `type`. It leaves a *required*-nullable field
(no `None` default) as pydantic's `anyOf`
(`test_required_nullable_field_is_left_as_pydantic_wrote_it`). No real gateway
model has one today, which is why
`test_advertised_schema_is_a_self_contained_mcp_input_schema`'s "no surviving
`anyOf`" assertion passes. Adding one will turn that test RED on purpose.
Revision 1 said `_summarize_arg_schema` reads `prop["type"]` as a string. That
function only summarises *downstream* tools' schemas for `gateway.describe`
(post-A `handlers.py:692`, called at `:1381`). No in-repo code reads a gateway
tool's property `type` (grep of `src/pmcp`: the only consumers of
`get_gateway_tool_definitions()` are `server.py:266` (`tools/list`) and `:277`
(the gate)). It is valid JSON Schema: Draft 2020-12 `check_schema` passes for
all 26. The board's claude seat confirmed that the MCP SDK ships the schemas
unchanged over `tools/list`; for example, `refresh.source` goes out as
`{"enum": ["claude_config","custom",null], "type": ["string","null"]}`.

**Named risk (F4), unmeasured: MCP-host dialect support for `type: [X,
"null"]` and for `null` inside `enum`.** Some hosts translate a tool's
`inputSchema` into a function-calling schema that is an OpenAPI subset.
Older Gemini function declarations are the example the board named, and
historically they have rejected type arrays and `null` in `enum`. Such a host
could refuse the whole tool list or drop tools. This plan has measured no
host. **Before releasing A,** run one `tools/list` smoke test from each
host family the fleet drives the gateway from (at least a Gemini host), and
check that all 26 gateway tools load and one `null`-bearing call works.

**Irreducible residue** — "advertised == model" means "advertised == the
model's *JSON-Schema projection*". Three things the models enforce are not
expressible in the schema and remain handler-only: `InvokeInput`'s
correlation-ID charset validator and its all-or-none `model_validator`, and
`RegisterDiscoveredServerInput._validate_package`. They are listed in the test
module docstring so nobody "fixes" the residue by weakening the models.

### The fixture (`tests/fixtures/gateway_tool_schemas.json`) — keep it, but know what it is

It is **not** the drift check; the drift check is the per-tool
`advertised == input_schema_for(model)` assertion plus the handler-source and
dispatch-source links. The snapshot is (1) an **agent-facing API change
detector** — any change to what agents see must show up as a reviewable JSON
diff in the PR, and B's diff to it *is* B's review artifact — and (2) a
**pydantic-version canary**: a 2.x minor that changes emission shape goes RED
here before it reaches an agent. It cannot drift *silently* (the test fails when
it disagrees); its residual risk is a blind
`PMCP_UPDATE_SCHEMA_SNAPSHOT=1` regeneration, which is a review-discipline item
called out in the test's docstring and in `CONTRIBUTING`-style guidance below.
Measured sizes (revision 2, frozen code): A snapshot **814 lines** at `c79bf1a` (813 before F1's `maximum` on `task.ttl`; 811 before N2's `minLength`/`maxLength` on `evidence_label_digest`)
(0 × `"additionalProperties": false`, 8 × `"additionalProperties": true` for the
free-form dicts, 42 × `"null"` from A1). The revision-1 A snapshot was 681
lines, and the revision-1 spike/B snapshot 712. **B's snapshot size is
unmeasured on revision 2.**

### Verdict on the inherited spike

Saved pristine at
`/tmp/claude-1000/-home-viperjuice-code-pmcp/f9824f56-5f85-411e-9d9e-5507fe665162/scratchpad/lane-236-spike/`.
Its `handlers.py` diff is exactly three hunks (two imports, one replacement of
the 646-line tool list with a 215-line spec registry); it did not touch any
handler body. `types.py` +341 is `GatewayArguments` + reparenting + moving every
description into `Field(description=…)`. Measured on the fused spike:
`ruff check` and `ruff format --check` clean; `tests/test_gateway_tool_schemas.py`
+ `tests/test_baseline_constraints.py` 228 passed; **all 26 `Tool.description`
strings byte-identical to HEAD at runtime** (the diff only reflowed string
concatenation).

What is wrong with it as a deliverable:

1. **A and B are fused.** `GatewayArguments` carries `extra="forbid"`, and
   `NO_ARGUMENTS_SCHEMA` hard-codes `additionalProperties: False` — that is B
   leaking into A's builder. Split as described below; measured A-only tree:
   163 passed.
2. **It silently resolved the 15 description conflicts in favour of the model
   text**, losing agent-facing content. Fixed in A's `types.py` change list:
   `72eaa76` repeated the problem, and `d0722f4` corrects it (see *Piece A →
   `types.py`*).
3. **It ignored B's second-order consequences**: `_extract_trace_context`
   (post-A tree `handlers.py:858-878`; HEAD `:1279-1299`) runs *before*
   `InvokeInput.model_validate` (post-A `:1458`; HEAD `:1879`) and reads two undeclared spellings (`meta`, `traceContext`);
   `InvokeInput.model_config = ConfigDict(populate_by_name=True)` makes the model
   accept `meta` while the `by_alias` schema advertises only `_meta` — so B's
   gate would reject `meta` and the model accept it, a new drift B would
   introduce. And `tests/test_tools.py:6213` sends
   `trace_context: {"authorization": …}` directly to `gt.invoke()` expecting it
   to be silently dropped — under `forbid` that raises. All handled under *Piece B*.
4. **It never measured anything.** Every table below is from this session.

Its design is sound and is adopted: spec registry (`_GatewayToolSpec`,
`_GATEWAY_TOOL_SPECS`, `GATEWAY_TOOL_INPUT_MODELS`), `input_schema_for`, the
three-link test chain, the synthetic-model normalisation test, the snapshot.

### A is not fully behaviour-neutral — own it

After A the gate enforces what only the model enforced before. Same inputs are
rejected; the *shape* changes from `{"error": true, "message": "1 validation
error for DescribeInput…"}` (content, not `is_error`) to
`CallToolResult(is_error=True, "Input validation error: '' should be non-empty")`.
Measured through `_handle_call_tool` for `describe {"tool_id": ""}`,
`submit_feedback {"title": "short"}`, `tasks_result options.max_output_chars: 5`
(`test_server_gate_rejects_what_the_model_rejects`). Also, 16 properties become
newly *advertised* (`invoke.task.*`, `invoke.trace_context.*`, `invoke._meta`,
`tasks_*.requestor_context`, `tasks_result.options.timeout_ms`) and 19 arguments
that had no description get one (both re-measured on the frozen code by
`desc_cmp.py` under *Measurement scripts*: `same 54 differ 1 new description 19
newly advertised 16`). This goes in A's CHANGELOG entry.

**Revision 2: A also *loosens* the gate for explicit `null` (A1).** The
42 optional-field-`null` cases (every `X | None = None` field, top level and
one level into a nested argument model) were measured with `probe_null.py` on
`main` and on the frozen A. The model accepts all 42. A's gate accepts all 42
(42/42 agree). **`main`'s gate agreed on only 14.** Those 14 are the ones the
board named, and they were accepted on `main` only because the hand-written
schema never declared them: `invoke.task` (+4 sub-fields), `invoke.trace_context`
(+3), `invoke._meta`, and `requestor_context` on all **four** `tasks_*` tools.
They stay accepted. The other **28 were rejected by `main`'s gate**
(`None is not of type 'string'`) and now pass it and reach the handler, which
already accepted them:
`auth_connect.{credential, elicitation_id, elicitation_url, env_var}`,
`catalog_search.{query, filters, filters.server, filters.tags, filters.risk_max}`,
`invoke.{run_correlation_id, seat_correlation_id, evidence_label_digest, options, options.max_output_chars}`,
`list_pending.server`, `refresh.{source, reason}`,
`request_capability.available_clis`, `set_startup_policy.{source, path}`,
`submit_feedback.{subordinate_server, failed_tool_call}`,
`sync_environment.{platform, detected_clis}`, `tasks_list.{server_name, cursor}`,
`tasks_result.{options, options.max_output_chars}`.
This is the direction A1 asked for (gate == model), but it is a behaviour
change for callers that send `null`, so it goes in A's CHANGELOG entry too.
The board's list also named `search_registry.available_clis`. That field does
not exist (`SearchRegistryInput` has only `query` and `limit`). The nearest
field, `request_capability.available_clis`, is one of the 28 that `main`
rejected.

**A also *tightens* the gate on lax-coercible values (board F1).** In lax mode
pydantic coerces `1`/`0`/`"true"` to a boolean and `"5"`/`True` to a number;
jsonschema does not. Where the gate and the model both type a field, the gate
is therefore **stricter** than the model. The board's claude seat ran a
7,655-case differential corpus through the real `_handle_call_tool` and found
**54** "gate rejects, model accepts" cases under A. **43** of them were
already rejected on `main`, because the hand-written schema had the same
types. **11 are new under A**, all on the newly advertised `invoke.task.*`
sub-fields, which `main` passed through undeclared. The 11 are reproduced
here with `probe_coerce.py` (`main`: 0 of the 11 rejected; A: 11 of 11):

```text
task.enabled=1          A gate: 1 is not of type 'boolean'
task.enabled=0 / 'true' A gate: … is not of type 'boolean'
task.ttl='5'            A gate: '5' is not of type 'integer', 'null'
task.ttl=True / False / '0005'
task.poll_interval=True A gate: True is not of type 'number', 'null'
task.poll_interval=False / '5' / '0005'
```

(The 54/43 totals are the seat's measurement and were not re-run here.) This
is the safe direction: the error names the problem, and no value is silently
changed. It is a behaviour change nonetheless, so it goes in A's CHANGELOG.
The test module's docstring was corrected at `40b2ed5` to match: gate and
model agree on *required* fields and on `null`, not on type *coercion*. Lax
coercion is the fourth residue class, alongside the three validator-only
constraints.

**A fifth class: regex dialect (board round 3, N2), closed for the only
field it hit.** The gate runs `pattern` through Python `re.search`, where `$`
also matches just before a final `\n`. The model uses pydantic-core's Rust
regex, where `$` matches only at the end of the string. So "everything the
projection can express agrees, except coercion" was false for `pattern`.
`InvokeInput.evidence_label_digest` (`pattern=r"^[0-9a-f]{64}$"`) accepted
`"a"*64 + "\n"` at the gate while the model rejected it. That is
pre-existing on `main`, fails safe, and the echoed value is a digest.
Measured with `probe_pattern.py`:

```text
main: 64 hex + newline: gate accepts | model rejects (string_pattern_mismatch)
A (972bc90): 64 hex + newline: gate rejects | model rejects (string_too_long)
```

`972bc90` adds `min_length=64, max_length=64` to the field. Both dialects
enforce lengths the same way, so the newline case is now a `maxLength` gate
rejection (`test_digest_pattern_agrees_between_gate_and_model`, mutant M-N2).
**Can another field hit it? Not today (measured):** `probe_pattern.py` walks
every advertised schema and every argument model, nested ones included. The
only `pattern` keyword in any advertised gateway schema, and the only
`pattern` constraint on any gateway argument model, is
`InvokeInput.evidence_label_digest`. A future `pattern` field reintroduces the
class unless its lengths are bounded the same way, or a gate/model
agreement test like `test_digest_pattern_agrees_between_gate_and_model`
covers it. End-anchor syntax differs between the two regex engines, so this
plan does not recommend a portable anchor. The
test module's docstring names this class and the test that pins it, as of
`a25dce0`.

**A sixth class: integer range (board round 4, F1), closed for the only
field it hit.** JSON Schema's `integer` accepts any integral number,
including the float `1e20`. pydantic's lax float→int conversion refuses a
float past int64 with `int_parsing_size`, and that error echoes the value.
An integer property with no `maximum` therefore has the gate *looser* than
the model. Measured with `probe_int_range.py`, which walks every advertised
schema for integer properties without a `maximum`:

```text
integer properties without `maximum`: ['gateway.invoke.task.ttl']
task.ttl=1e+20: gate accepts | model rejects (int_parsing_size)
task.ttl=9223372036854775808: gate accepts | model accepts
task.ttl=3600: gate accepts | model accepts
```

(at `a25dce0`; on `main` the gate never declared `task`, so it too accepts
`1e20`, and the model is the same). At `c79bf1a`:

```text
integer properties without `maximum`: []
task.ttl=1e+20: gate rejects | model rejects (int_parsing_size)
task.ttl=9223372036854775808: gate rejects | model rejects (less_than_equal)
task.ttl=3600: gate accepts | model accepts
```

`c79bf1a` adds `le=9_223_372_036_854_775_807` to `TaskMetadataInput.ttl`, so
the gate advertises the bound and rejects `1e20`. Test:
`test_ttl_range_agrees_between_gate_and_model[1e20, 2**63]`, mutant M-F1. No
other integer property lacks a `maximum` (measured: `[]`). **Correction to
the framing "the int64 bound pydantic already enforces":** it already
enforced it for *floats* only. A JSON **integer** `2**63` was accepted by the
model at `a25dce0` (and on `main`) and is now rejected by both layers
(`less_than_equal`). That is a narrowing for `task.ttl` values above
2^63−1, which no real TTL reaches. It is listed with A's other behaviour
changes. The test module's docstring names this class as of `c79bf1a`.

## Order: A first, then B — why

- A is mergeable on its own and is behaviour-preserving at the model layer; B is
  a behaviour change that needs its own release note.
- B's implementation *depends* on A: with the schema derived from the model,
  flipping `extra="forbid"` makes pydantic emit `additionalProperties: false`
  everywhere automatically, so the transport gate rejects extras with a
  key-only message (`Additional properties are not allowed ('bogus_key' was
  unexpected)`) *before* pydantic sees them. Without A, B would need 26 hand
  edits adding `additionalProperties: false` — i.e. more of the drift A removes.
- B's review artifact (the snapshot diff) only exists once A has landed the
  snapshot.
- Measured why the gate must stay in front under B: pydantic's
  `extra_forbidden` error string includes
  `input_value='Bearer sk-SAMPLE'`. `server.py:427-458` logs it
  (`logger.error(f"Tool execution error: {e}")`, `:428`) and returns
  `str(e)[:400]`, so a forbid error that reaches pydantic from a
  gate-bypassing caller echoes the value into both the response and the log.
  B pins that the gate catches it first (`test_unknown_key_value_never_echoed`).
- **Correction (revision 2): jsonschema does *not* in general "name only the
  key".** Measured with `probe_echo.py` (*Measurement scripts*), identically on
  `main` and on A:

  | Gate error class | Message | Echoes the value? |
  |---|---|---|
  | `additionalProperties: false` | `Additional properties are not allowed ('authorizatoin' was unexpected)` | **no** (key only) |
  | `type` (`invoke.arguments` sent a string) | `'Bearer sk-SAMPLE' is not of type 'object'` | **yes** |
  | `enum` (`refresh.source`) | `'Bearer sk-SAMPLE' is not one of ['claude_config', 'custom']` | **yes** |
  | `pattern` (`invoke.evidence_label_digest`, the one `pattern` in any gateway schema) | `'Bearer sk-SAMPLE' does not match '^[0-9a-f]{64}$'` | **yes** |
  | `minLength` (A only) | `'' should be non-empty` | yes, but only the empty string |

  The value echo on `type`/`enum`/`pattern` errors is **pre-existing on
  `main`**. A adds no new echoing keyword; its new constraints are
  `minLength`/`maxLength`/bounds, which only echo short or out-of-range
  values. The gate path returns the message in the `CallToolResult` but does
  **not** log it (`server.py:298-308` has no logger call), whereas the pydantic
  path does both. What this means:
  - **For B:** the gate protects B's *unknown-key* class specifically, because
    `additionalProperties` is the one error class that names only the key.
    `test_unknown_key_value_never_echoed` is scoped to exactly that class and
    is correct as written. B must not claim that gate errors never carry
    values. A secret sent as a wrong-*typed* value to a *declared* key is
    echoed today and stays echoed (out of scope, unchanged by A or B).
  - **For X1:** the protection holds only when the gate *runs*. For a
    dispatched name absent from the registry, `_find_gateway_tool` returns
    `None`, the gate is skipped, and under B pydantic's `extra_forbidden` (value
    included) would reach both the response and the log. That is why X1
    closes that path structurally, and why B depends on it (*X1*).
  - **The gate-*passing* path (board F3, pre-existing, measured).** Arguments
    that pass the gate and are rejected by a **model-only validator** raise a
    pydantic `ValidationError` whose string includes `input_value`. The
    `except Exception` arm logs it (`server.py:428`, post-A `:437`) and
    returns `str(e)[:400]` (`:458`, post-A `:467`). Measured with
    `probe_model_echo.py`, the same on `main` and on A:
    - the correlation-ID **charset validator** echoes the full value
      (`run_correlation_id="Bearer sk-…"` → `echoes value = True`);
    - the **all-or-none `model_validator`** echoes the whole argument dict as
      `input_value`. pydantic truncates the middle but keeps the tail, so with
      an 80-character secret in `arguments.api_key` the last **21 characters**
      of the secret appear (`…FGHIJKLMNOPqrstuvwxyz'}}`, measured). The
      `arguments` dict is where downstream credentials travel;
    - `meta` with a non-dict value (accepted as a spelling of `_meta` through
      `populate_by_name`) gives a `dict_type` error that echoes the value
      (`echoes value = True`). This is **pre-existing on `main`**: the output
      is identical on `main` and A, because `main`'s gate never declared
      `meta` either.

    **What B changes here:** only the `meta` case. B removes
    `populate_by_name`, and `meta` becomes an undeclared key that the gate
    rejects by *name* (`additionalProperties`), so its value no longer reaches
    pydantic. The charset and all-or-none paths are **unchanged by B**. They
    are listed under *Non-goals* and tracked as **Consiliency/pmcp#297**
    (render pydantic errors without `input_value`). B does not wait for
    Consiliency/pmcp#297, because the only echo path B touches is `meta`, and B removes it. B's `test_unknown_key_value_never_echoed`
    must not be read as covering them.

## Piece A — changes

### `src/pmcp/tools/schema.py` (add, 99 lines; whole file verbatim under *Verbatim bodies → A*)

- `NO_ARGUMENTS_SCHEMA = {"type": "object", "properties": {}}` — **no**
  `additionalProperties` (that is B). Byte-equal to what `gateway.health`
  advertises on HEAD.
- `input_schema_for(model | None) -> dict` — `None` → deep copy of
  `NO_ARGUMENTS_SCHEMA`; else `model.model_json_schema(by_alias=True, mode="validation")`
  → pop `$defs` → `_normalize` → pop top-level `description`.
- `_normalize(node, defs)` — inline `#/$defs/*` refs (sibling keys such as
  `description` merged over the target; any other `$ref` prefix raises
  `ValueError`), drop `title` keys (but never a *property named* `title`), recurse.
- `_collapse_nullable(node)` (**revised for A1**) — only for
  `anyOf: [X, {"type": "null"}]` **with `default: None`** (an optional field):
  → `X`'s keywords flat, `anyOf` and `default` removed, **`"null"` appended to
  `X`'s `type`** (`"string"` → `["string", "null"]`; an existing list gains
  `"null"` if missing) and **`None` appended to `X`'s `enum`** if it has one
  (the `enum` keyword is checked independently of `type`, so without this a
  `Literal | None` field would still reject `null`). A nullable field
  *without* a `None` default (required-but-nullable) is returned untouched as
  pydantic's `anyOf`. The gate therefore accepts exactly what the model
  accepts: the field omitted, `null`, or an `X`.
- Reason: documented in the module docstring; each transform is one line of
  drift surface and is pinned by `test_input_schema_for_normalises_pydantic_output`
  (plus `test_required_nullable_field_is_left_as_pydantic_wrote_it` for the
  collapse's boundary).

### `src/pmcp/types.py` (modify; `git apply` patch verbatim under *Verbatim bodies → A*, +281 / −110)

- **Add `class GatewayArguments(BaseModel)`** directly above `TraceContextInfo`
  with **no `model_config`** in A. Docstring: marks agent-facing argument
  contracts; models parsing downstream data (`McpTaskInfo`, registry results,
  `.mcp.json`) must not use it. It exists so B is a one-line flip.
- **Reparent 27 models** to `GatewayArguments`: the 23 in the inventory table
  plus `CatalogFilters`, `InvokeOptions`, `TaskMetadataInput`, `TraceContextInfo`.
  `InvokeInput` keeps `ConfigDict(populate_by_name=True)` in A (B removes it).
- **Move every argument description into `Field(description=…)`** so the derived
  schema carries it.

  Rule for the text: **`main`'s hand-written schema text wins**. It is what
  agents have been reading, and it is uniformly the more informative. There is
  one exception, `UpdateServerInput.force`: its model text is the accurate one,
  because the hand-written copy predates task support.

  > **Decision: option (b), applied and measured.** The first revision-2 code
  > (`72eaa76`) kept the model text in all 15 conflicts. Choosing between
  > (a) accepting that and (b) restoring revision 1's rule, the coordinator
  > chose **(b)** and applied it at `d0722f4`: the 14 conflicts are moved into
  > `Field(description=…)` with `main`'s text, and the snapshot is regenerated
  > (14 lines change). Measured with `desc_cmp.py` (`main` → `d0722f4`):
  > `tool descriptions byte-identical: 26/26`, `same 54 differ 1 new
  > description 19 newly advertised 16`. The one remaining difference is
  > `update_server.force`, the intended exception. The 15 conflicts, and what
  > each resolves to:
  >
  > | Argument | `main` (hand-written) → **kept** except `force` | model text (`72eaa76`) |
  > |---|---|---|
  > | `connect_server.server_name` | Name of the server to connect | Server to connect |
  > | `disconnect_server.server_name` | Name of the server to disconnect | Server to disconnect |
  > | `restart_server.server_name` | Name of the server to restart | Server to restart |
  > | `request_capability.query` | Natural language description of the capability needed (e.g., 'I need to scrape a website', 'browser automation') | Natural language capability request |
  > | `provision.server_name` | Name of the server to provision (from manifest) | Name of the server to provision from manifest |
  > | `update_server.server_name` | Name of server to update | Server to update |
  > | `update_server.force` | Cancel this server's pending requests before restarting | Restart the server even if it has pending requests or active MCP tasks, cancelling them. Mirrors gateway.restart_server's force flag. **← kept (the exception)** |
  > | `auth_connect.server_name` | Server name that needs authentication | Server requiring authentication |
  > | `auth_connect.credential` | API key, token, or subscription credential to store | Secret token/API key to store |
  > | `auth_connect.env_var` | Optional explicit environment variable key | Override environment variable key to store into |
  > | `auth_connect.scope` | Where to store the credential | Where to store credentials |
  > | `provision_status.job_id` | Job ID from gateway.provision response | Job ID from provision response |
  > | `search_registry.query` | Natural language description of the capability needed | Natural language capability description |
  > | `register_discovered_server.server_name` | Logical name for this server (e.g. 'github') used with gateway.provision | Logical name for this server (e.g. 'github') |
  > | `register_discovered_server.env_vars` | Required environment variable names (e.g. ['GITHUB_TOKEN']) | Required environment variable names |

  54 descriptions match `main` byte for byte (40 lifted verbatim + the 14
  resolved conflicts; measured); 19 arguments that had no
  description on HEAD get the spike's text (list in `scratchpad/desc_diff.out`:
  `catalog_search.filters`, `invoke.options`, the six `set_startup_policy.*`,
  `submit_feedback.issue_type`, `tasks_get/result/cancel.server_name|task_id`,
  `tasks_result.options[.max_output_chars|.redact_secrets]`, `tasks_cancel.force`);
  16 newly advertised properties get the spike's text. The snapshot in the PR
  is the review surface for all of this.
- Reason: the model is the single source; descriptions that live only in the
  hand-written dict are exactly the drift being removed.

### `src/pmcp/tools/handlers.py` (modify; `git apply` patch verbatim under *Verbatim bodies → A*, +195 / −616)

- Import `functools`, `NamedTuple` (typing) and `BaseModel` (pydantic); import
  `input_schema_for` from `pmcp.tools.schema`.
- **Add `class _GatewayToolSpec(NamedTuple)`**: `name: str`,
  `input_model: type[BaseModel] | None`, `description: str`.
- **Add `_GATEWAY_TOOL_SPECS: tuple[_GatewayToolSpec, ...]`** — 26 entries in the
  existing order, each carrying the tool's **unchanged** description string
  (measured byte-identical at runtime) and the model from the inventory table;
  `None` for `health`, `config_status`, `get_startup_policy`.
- **Add `GATEWAY_TOOL_INPUT_MODELS: dict[str, type[BaseModel] | None]`** derived
  from the specs — the public registry the tests read.
- **Replace `get_gateway_tool_definitions()`** (HEAD `handlers.py:470-1111`,
  26 inline `Tool(...)`; post-A the registry and builder occupy `:472-689`)
  with **two functions (A2)**:
  - `@functools.cache def _derived_gateway_tools() -> tuple[Tool, ...]`, which
    builds `Tool(name=spec.name, description=spec.description, input_schema=input_schema_for(spec.input_model))`
    for each spec **once per process**;
  - `get_gateway_tool_definitions() -> list[Tool]`, which returns
    `list(_derived_gateway_tools())`.

  Net −421 lines (numstat +195 / −616).
  Why cache: the board measured the derivation at 13.4 ms per call, against
  0.058 ms for the hand-written literal, and `server.py:277`
  (`_find_gateway_tool`) calls `get_gateway_tool_definitions()` on **every**
  `tools/call`, and `:266` on every `tools/list`. The models are module-level
  and immutable at runtime, so a process-lifetime cache is exact. Callers get a
  fresh `list`, but the `Tool` objects (and their `input_schema` dicts) are
  **shared** across calls. Nothing in-repo mutates them (the only consumers are
  `server.py:266` and `:277`, which read). A future caller that mutates an
  `input_schema` would poison every later `tools/list` and gate check.
  `test_schemas_are_derived_once_per_process` pins the call count (A2 row
  under *Acceptance criteria*). It deliberately measures call count, not wall
  time.
- Nothing else in the file changes. `server.py` already reads
  `get_gateway_tool_definitions()` for both `tools/list` and the gate. Its
  only change in A is the X1 guard (next section).
  The `_extract_trace_context` / `InvokeInput.model_validate` bodies are
  unchanged and move to post-A `:858-878` / `:1458` (HEAD `:1279-1299` /
  `:1879`).
- Reason: by construction there is no hand-written schema left to drift.

### `src/pmcp/server.py` (modify; `git apply` patch verbatim under *Verbatim bodies → A*, +9 / −0 against `main`)

- In `_handle_call_tool.call_tool`, directly after the policy check and
  **before** the scoped-audit `InvokeInput.model_validate` check (which is
  otherwise unchanged), insert
  `if tool is None: raise ValueError(f"Unknown tool: {name}")` with a comment.
  This is the structural half of X1 (see *X1* below). Because it follows the
  policy check, a blocked unknown name is still recorded `denied`. Any other
  unknown name is recorded `failure` by the `except Exception` arm, as
  before. Because it precedes the scoped check (N1), no ungated argument ever
  reaches pydantic.
- Post-A `server.py` line numbers after `:329` shift by +9 relative to `main`
  (`Unknown tool` else-branch `:392`→`:401`, `except Exception` `:427`→`:436`).
  Where this plan cites `server.py` lines without "post-A", they are `main`'s.

### `tests/test_gateway_tool_schemas.py` (add, 555 lines; whole file verbatim under *Verbatim bodies → A*)

**213 tests** at `c79bf1a` (measured: `213 passed`, collect-only counts in brackets; 211 at `972bc90`/`a25dce0`, 209 at `40b2ed5`, 208 before the X1 guard test). The
three-link chain plus shape, snapshot, normalisation, gate, and the revision-2
additions:

- `test_registry_lists_every_advertised_tool_in_order`
- `test_advertised_schema_is_derived_from_registered_model[26]` — the
  "hand-corrupted schema" detector (M1).
- `test_handler_validates_arguments_with_the_registered_model[23]` — asserts
  `re.search(rf"\b{Model.__name__}\.model_validate\(", source)` on the
  handler's source. **X2: already a word-boundary regex in the frozen code**
  (mutant M-X2 below: `_DescribeInput.model_validate(` passes a substring check
  but fails this one). Known cost: source inspection is brittle to refactors
  that move validation into a helper. The structural alternative (server.py
  validating via the registry instead of each handler) is a named non-goal.
- `test_server_dispatch_agrees_with_registry[26]` — `None` ⇔ `method()` in
  `_handle_call_tool` source. Proves registry → dispatch only.
- **`test_every_dispatched_gateway_name_is_registered` (X1 text pin, new)** —
  see *X1* below. Lexical: proves dispatch ⇔ registry for `name == "…"`
  branches only.
- **`test_a_dispatch_branch_for_an_unregistered_name_fails_closed` (X1
  structural, new at `40b2ed5`)** — see *X1*. Unregisters `gateway.health`
  via monkeypatch and asserts its handler never runs and the response says
  `Unknown tool: gateway.health`.
- **`test_an_unregistered_invoke_never_reaches_the_scoped_audit_model` (N1,
  new at `972bc90`)** — with a scoped audit configured and `gateway.invoke`
  unregistered via monkeypatch, sends a secret-bearing `run_correlation_id`.
  Asserts `Unknown tool: gateway.invoke` and that the secret is absent from
  the response, i.e. the guard ran before the scoped `InvokeInput` parse.
- **`test_digest_pattern_agrees_between_gate_and_model` (N2, new at
  `972bc90`)** — `"a"*64 + "\n"` for `evidence_label_digest` is rejected by
  both the gate and the model.
- **`test_ttl_range_agrees_between_gate_and_model[2]` (F1, new at
  `c79bf1a`)** — `task.ttl` of `1e20` and `2**63` is rejected by both the gate
  and the model, and `3600` is accepted by both.
- `test_no_argument_tools_advertise_an_empty_object[3]`
- `test_advertised_schema_is_a_self_contained_mcp_input_schema[26]` — no
  `$ref`/`$defs`/`title` keywords, no surviving `anyOf`, every property described.
  **Walker fixed for A1 (`d0722f4`).** After A1 the five *optional* nested
  objects (`catalog_search.filters`, `invoke.options`, `invoke.task`,
  `invoke.trace_context`, `tasks_result.options`) are typed
  `["object", "null"]`. The `72eaa76` helper selected only
  `node.get("type") == "object"` and found 26 of the 31 object schemas. The
  helper `_object_schemas` now also accepts a `type` list containing
  `"object"` and finds all 31 (measured). Mutant M-W: remove the description
  from `InvokeOptions.timeout_ms` (`types.py:808`). The new helper fails
  `…is_a_self_contained_mcp_input_schema[gateway.invoke]` and
  `[gateway.tasks_result]`, which shares `InvokeOptions` (2 failed, 24 passed
  under `-k self_contained`). The `72eaa76` helper passes all 26 on the same
  mutant, which was the blind spot.
- `test_advertised_schema_is_valid_json_schema[26]` — `Draft202012Validator.check_schema`.
- `test_advertised_schema_accepts_the_minimal_valid_arguments[26]` — gate and
  model both accept the minimal argument set.
- `test_advertised_schemas_match_snapshot`
- `test_input_schema_for_normalises_pydantic_output` (now expects
  `["string", "null"]`, `["object", "null"]` and `enum: ["a", "b", None]` for the
  synthetic optionals), `test_input_schema_for_none_is_the_empty_object`
- **`test_required_nullable_field_is_left_as_pydantic_wrote_it` (new)** — the
  collapse's boundary: `str | None` with no default keeps its `anyOf`.
- **`test_optional_field_null_agrees_between_gate_and_model[42]` (A1, new)** —
  for every optional field with a `None` default (top level, and one level
  into each nested argument model), sends explicit `null` through both the
  gate (`jsonschema.validate` against the advertised schema) and the model,
  and asserts `gate_accepts == model_accepts`. This is the board's requested
  property, "the model accepts `null` iff the derived schema does", checked
  per field. Note: the 42-case enumeration walks
  `model_fields[...].annotation`, and `TasksResultInput.options` is a
  `ForwardRef` until the model is rebuilt. It resolves here because the module
  computes `TOOL_NAMES` (which derives every schema) at import, before the
  parametrize list is built. Measured: without that rebuild, the enumeration
  yields 41 cases on `main`'s models, missing
  `tasks_result.options.max_output_chars`.
- **`test_schemas_are_derived_once_per_process` (A2, new)** — patches
  `handlers.input_schema_for` with a counting wrapper, clears the cache, then
  calls `get_gateway_tool_definitions()` and `GatewayServer._find_gateway_tool`
  5× each, and asserts `input_schema_for` ran exactly `len(TOOL_NAMES)` = 26
  times.
- `test_server_gate_rejects_what_the_model_rejects[3]` — pins A's error-shape change.

### `tests/fixtures/gateway_tool_schemas.json` (add)

Generated with `PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest tests/test_gateway_tool_schemas.py`
after the code above is applied (the writer is deterministic:
`json.dumps(..., indent=1, sort_keys=True) + "\n"`), then reviewed line by line
in the PR. Measured at `c79bf1a`: 814 lines, 0 × `additionalProperties: false`, 8 × `true`.
The embedding proof `cmp`s the generated file against the frozen fixture.

### X1 — unregistered names fail closed inside the audited path (structural; blocking for B)

`_handle_call_tool` runs the gate only for names `_find_gateway_tool` finds in
`get_gateway_tool_definitions()` (`server.py:296-308`). On `main`, a dispatch
branch in `call_tool` for a name **not** in the registry would skip the gate
and hand raw arguments to its handler. Under B that handler's pydantic
`extra_forbidden` error, which carries `input_value='Bearer sk-…'`, would then
be both logged and returned. Measured on `main` and on A: today the dispatched
names equal the registry (26/26).

**Revision 2's first answer was a text pin, and the board showed it can be
bypassed.** `test_every_dispatched_gateway_name_is_registered` extracts
`\bname == "(gateway\.[a-z_]+)"` from the source of `_handle_call_tool`. The
board's claude seat broke it with a one-line mutant,
`elif name == "gateway.health" or name == "gateway.health2":`. The schema
tests stayed green, `_find_gateway_tool("gateway.health2")` returned `None`,
and the raw arguments were dispatched. A lexical pin cannot see `or`, `in`,
`match`, or dict dispatch.

**So X1 is now structural (`40b2ed5`, placement fixed at `972bc90`).** Inside
`call_tool`, directly after the policy check, and *before* both the
scoped-audit `InvokeInput.model_validate` check and the dispatch chain:

```python
if tool is None:
    raise ValueError(f"Unknown tool: {name}")
```

(post-A `server.py:330-337`, with a comment). Nothing can reach a handler, or
any pydantic parse, without having gone through the schema gate, whatever
shape a dispatch condition takes. The guard raises **inside** the audited
path: the `except Exception` arm (post-A `:436`) records the call as
`terminal_status="failure"`, exactly as an unknown name has always been
recorded through the old `else: raise ValueError(...)` (post-A `:401`, now
unreachable for unregistered names). This retires revision 2's "pin, not
fail closed" argument. That argument assumed failing closed meant an early
return at the gate, *outside* the audited path. Failing closed inside
`call_tool` is what the seat proposed, and it keeps the audit.

- **Test:** `test_a_dispatch_branch_for_an_unregistered_name_fails_closed`.
  It monkeypatches `srv._find_gateway_tool` to report `gateway.health` (a
  name that *has* a dispatch branch) as unregistered, replaces the `health`
  handler with a recorder, and calls `_handle_call_tool` with
  `{"x": "Bearer sk-x"}`. It asserts that the handler never ran and that the
  response contains `Unknown tool: gateway.health`.
- **Mutant M-X1s** (guard off, `if tool is None:` → `if False:`): this test
  and the N1 test fail by name (see *Acceptance criteria*). The coordinator also measured
  the seat's own bypass (`… or name == "gateway.health2"`) against the guard:
  the response is `Unknown tool: gateway.health2`.
- **Placement (board round 3, N1).** At `40b2ed5` the guard sat *below* the
  scoped-audit check, which runs `InvokeInput.model_validate(arguments)` when
  a scoped audit is configured and `name == "gateway.invoke"`. Had
  `gateway.invoke` ever left the registry, its ungated arguments would have
  reached pydantic, and the error would have echoed the value into the
  response and the log. The seat reproduced this with a stub audit:
  `secret in resp: True | in log: True`. At `972bc90` the guard is above
  the check. Test: `test_an_unregistered_invoke_never_reaches_the_scoped_audit_model`
  unregisters `gateway.invoke` with a scoped audit configured and asserts
  `Unknown tool: gateway.invoke`, with the secret absent from the response.
  Mutant M-N1 (guard moved back below the scoped check) fails it.
- **The text pin stays as a second line of defence.** It gives an earlier,
  clearer failure for the common case of a branch whose name was never
  registered. Mutant M-X1 (rename the `gateway.health` branch) is still caught
  by the pin alone. The older `test_server_dispatch_agrees_with_registry`
  proves only registry → dispatch.

**B depends on the structural guard, not on the pin.** B's claim that an
unknown key is rejected by name and its value never echoed holds only for
names the gate sees. The `tool is None` guard is what guarantees that every
name that reaches a handler was gated. B's PR must not merge unless both the
guard and its test are present and green on B's head, and the guard still
sits **above** the scoped-audit check (N1, enforced by
`test_an_unregistered_invoke_never_reaches_the_scoped_audit_model`).

### Scoped-audit gap on gate rejections (A3) — attributed, scoped out to Consiliency/pmcp#296

**Attribution (revision 1 put it in B; it starts earlier).** On `main` the
gate returns `CallToolResult(is_error=True, …)` at `server.py:300-308`
*before* `call_tool` is entered. So a gate rejection never reaches
`_record_scoped_invocation` (`:320`, `:405`, `:436`), and it also skips
`_require_scoped_audit()` and the policy check. **This is pre-existing on
`main`** for every gate rejection the hand-written schemas already made
(missing required key, wrong type, enum). **A moves more rejections onto that
path:** on the 18 constraint-drift tools, inputs that `main`'s gate passed and
the *model* rejected (e.g. `describe {"tool_id": ""}`) were audited as
`failure` inside `call_tool`. Under A the gate rejects them first, unaudited.
The board's corpus counts 544 such model→gate moves. (A1 moves 28 `null`
cases the other way, off the unaudited path.) **B adds more:** every
unknown-key call becomes a gate rejection.

**A consequence for policy-blocked tools (board F5, measured).** Because the
gate runs before the policy check, a *malformed* call to a *policy-blocked*
tool was recorded as `denied` on `main` and is not recorded under A.
`probe_blocked_audit.py` forces `is_gateway_tool_allowed` to `False` and
captures `_record_scoped_invocation`:

```text
== main
  describe {'tool_id': ''}: is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol' audit=['denied']
  describe {'tool_id': 'srv::tool'}: is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol' audit=['denied']
== A
  describe {'tool_id': ''}: is_error=True text="Input validation error: '' should be non-empty" audit=[]
  describe {'tool_id': 'srv::tool'}: is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol' audit=['denied']
```

So probes of blocked tools with malformed arguments drop out of the scoped
audit under A. A gate rejection has no side effects, so the audit loses
*attempts*, not *actions*.

**Decision: scope out to Consiliency/pmcp#296, which lands before B.**
Recording a *rejected, unvalidated* payload means deciding what the audit may
store without copying raw values. That needs its own design and review.
Consiliency/pmcp#296 ("Record `tools/call` gate rejections in the
scoped-advisor audit") is filed. Its likely shape mirrors the existing
`except Exception` arm: `terminal_status="failure"`, or `"denied"` when the
tool is policy-blocked, `result={"error_type": "InputValidationError"}`,
arguments passed through the audit's existing filtering, and never the
jsonschema message (which can echo values, see above). It must land **before B
merges**, since B is where the unaudited set grows the most.

### `CHANGELOG.md` (modify) — `[Unreleased]` → `### Fixed`

"Gateway tool `inputSchema`s are now derived from the pydantic models that
validate the arguments, so the two can no longer disagree
(Consiliency/pmcp#236). Constraints the models always enforced are now
advertised and enforced at the transport gate — `minLength` on identifiers,
`submit_feedback.title` 8–160 chars, bounds on `tasks_result.options` — so
those rejections now come back as an `isError` tool result reading
`Input validation error: …` instead of an `{"error": true}` payload.
`gateway.invoke` now advertises `task`, `trace_context` and `_meta`;
`gateway.tasks_*` advertise `requestor_context`; `tasks_result.options` gains
`timeout_ms`. Optional arguments are advertised as `type: [X, "null"]` and
the transport gate now accepts an explicit `null` for them, as the handlers
always did; 28 optional arguments (e.g. `catalog_search.query`,
`invoke.options`, `auth_connect.credential`) were previously rejected at the
gate when sent as `null`. The gate does not apply pydantic's lax
coercion: values such as `1` for a boolean or `"5"` for an integer on the
newly advertised `invoke.task` fields (`enabled`, `ttl`, `poll_interval`),
which were previously accepted and coerced, are now rejected with
`Input validation error: 1 is not of type 'boolean'`.
`invoke.task.ttl` now advertises an int64 maximum, so `1e20` (and any
integer above 2^63−1, which the handler previously accepted) is rejected at
the gate.
`invoke.evidence_label_digest` now also advertises its exact length (64), so
a digest with a trailing newline is rejected at the gate instead of by the
handler. **Scoped-audit change until Consiliency/pmcp#296 lands:** gate
rejections are not written to the scoped-advisor audit. So a *malformed*
call to a *policy-blocked* gateway tool now gets
`Input validation error: …` instead of "Gateway tool blocked by policy", and
it is **no longer recorded as `denied`**. The same holds for the other
inputs the gate now rejects that previously reached the handler and were
recorded as `failure`. Well-formed calls to blocked tools are still recorded
`denied`. Unknown keys are still ignored in this release — see the following
entry once B lands."

Add to that entry (the description decision was resolved to (b)): "Argument
descriptions agents already saw are unchanged, except
`gateway.update_server.force`, which now describes the task-aware behaviour;
19 previously undescribed arguments gain a description."

## Piece B — changes

> **Revision 2 status of B: described, not implemented, and not re-measured.**
> Every B number in this plan (223 schema tests, 264 with the edited callers,
> the 712-line snapshot with 31 × `false` / 8 × `true`, the MB1–MB4 counts,
> the full suite 4287 passed) was measured on the **revision-1** tree, whose A
> collapsed optionals to a bare `type`. None of them has been re-measured on
> revision-2 A. B's executor must re-measure all of them. What is known to
> still hold, and what must change:
>
> - **B depends on X1's structural guard.** B's security claim ("an unknown
>   key is rejected by *name*, and its value is never echoed or logged") holds
>   only for names the gate sees. The `tool is None` guard in `call_tool`
>   (in A) guarantees that no ungated name reaches a handler.
>   `test_a_dispatch_branch_for_an_unregistered_name_fails_closed` pins it.
>   B's PR must not merge unless the guard and that test are present and green
>   on B's head, **and the guard still sits above the scoped-audit
>   `InvokeInput.model_validate` check** (the board seat's condition, N1).
>   `test_an_unregistered_invoke_never_reaches_the_scoped_audit_model`
>   enforces that order, and it must be green too. Under B that pre-guard
>   parse would become an `extra_forbidden` echo. If a later change removes either,
>   `test_unknown_key_value_never_echoed` no longer covers every tool.
> - **B lands after Consiliency/pmcp#296** (A3).
> - **Hunks still apply (checked by reading the post-A source, not by
>   applying).** The `types.py` `GatewayArguments` / `InvokeInput` context and
>   the `_extract_trace_context` candidate tuple (post-A `handlers.py:862-867`)
>   are unchanged by revision 2. The `NO_ARGUMENTS_SCHEMA` replacement is
>   unchanged. The four caller edits touch files A does not modify.
> - **B test-code changes forced by revision-2 A (1 required, 1 recommended):**
>   1. `test_advertised_schema_forbids_unknown_keys` (B's own test) skips any
>      property whose `type` is not the string `"object"`. After A1, the
>      optional nested objects are `["object", "null"]`, so as written it would
>      silently skip `invoke.options`, `invoke.task`, `invoke.trace_context`,
>      `invoke._meta`, `catalog_search.filters` and `tasks_result.options`: a
>      check that proves less than it claims. Change its guard to "`"object"`
>      is the type or is in the type list". (A's `_object_schemas` helper, which
>      this test also calls, is already fixed in A at `d0722f4`, so there is
>      nothing to do there.) Then prove the fix with a mutant: drop
>      `additionalProperties: false` from `InvokeOptions` only, and expect RED.
>   2. B's appended block defines `_model_types`, but revision-2 A's test
>      module already defines an identical `_model_types`. Appending it again
>      is harmless at runtime (same body), and ruff does not flag it (measured:
>      `ruff check --select F811` on A's module + B's block reports
>      "All checks passed!"). It is still a second copy, so omit B's
>      `_model_types` and reuse A's.
> - The A1 property test (`test_optional_field_null_agrees_between_gate_and_model`)
>   still applies under B unchanged: `extra="forbid"` does not affect `null`
>   on declared fields.

### `src/pmcp/types.py` (modify)

- `GatewayArguments.model_config = ConfigDict(extra="forbid")`; docstring gains
  the rationale (unknown keys are an error, never dropped; a misspelled or
  hallucinated argument must fail loudly). This is the one-line flip; pydantic
  then emits `additionalProperties: false` on all 27 models' schemas.
- `InvokeInput`: **remove `model_config = ConfigDict(populate_by_name=True)`**.
  Measured: nothing constructs `InvokeInput(meta=…)` by name
  (`tests/test_scoped_advisor_audit.py:146-157` use `tool_id=`/correlation
  fields only); with it kept, the model would accept `meta` while the gate
  rejects it — drift B would introduce.

### `src/pmcp/tools/schema.py` (modify)

- `NO_ARGUMENTS_SCHEMA` gains `"additionalProperties": False` so
  `health`/`config_status`/`get_startup_policy` reject keys at the gate (the only
  layer that can, since they are dispatched with no arguments).

### `src/pmcp/tools/handlers.py` (modify)

- `_extract_trace_context` (post-A `:850-870`; HEAD `:1279-1299`): drop the `input_data.get("meta")` and
  `input_data.get("traceContext")` candidates. Under B both spellings are
  rejected by the gate (never advertised) and by the model, so the branches are
  dead; keeping them documents a contract that no longer exists. Measured: no
  test or caller sends `traceContext`; `meta` only via the alias.

### `tests/test_tools.py` (modify, `:6209-6226`)

`test_invoke_policy_denied_does_not_log_trace_secret` (the test at `:6213`) sends
`trace_context: {"authorization": "Bearer should-not-log"}` straight to
`gt.invoke()` and expects the key to be dropped and the call to proceed to the
E402 policy denial. Under B `TraceContextInfo` forbids it. Change the test to
send the secret under `_meta` (a declared free-form `dict[str, Any]`, still
filtered to `TRACE_CONTEXT_KEYS` by `_extract_trace_context`), which keeps the
test's purpose — a secret in an envelope is not logged — intact. The
"misplaced key is rejected" behaviour gets its own test in B's file.

### `tests/test_server.py` (modify, `:247-290` and `:342-380`)

Two routing tests reuse one argument dict across tools with different schemas
(`{"server_name","task_id"}` for all four `gateway.tasks_*`;
`{"operation","names"}` for `config_status`, `get_startup_policy`,
`set_startup_policy`). Give each tool its own valid arguments (`tasks_list`
without `task_id`; `{}` for the two argless tools). The tests' purpose —
routing reaches the right handler — is unchanged.

### `tests/test_scoped_advisor_audit.py` (modify, the `gateway.provision` call after `:280`)

Drop the four undeclared keys (`tool_id`, `run_correlation_id`,
`seat_correlation_id`, `evidence_label_digest`) from the policy-denied
provision call. They were raw
values planted to prove the denied-call audit does not copy them; the
`gateway.invoke` calls earlier in the same test plant the same kind of values
in the free-form `arguments` dict and keep that proof.

**These four are the whole measured in-repo blast radius** (see *Blast radius*).

### `tests/test_gateway_tool_schemas.py` (modify; verbatim under *Verbatim bodies → B*, with the two changes in the B preamble)

- `test_every_argument_model_extends_gateway_arguments` — walks nested
  annotations; every argument model is a `GatewayArguments` with `extra == "forbid"`.
- `test_unknown_top_level_key_is_rejected_by_the_model[23]`,
  `test_unknown_nested_key_is_rejected_by_the_model[4]` (incl. the C-10 case
  `options.timeoutMs`).
- `test_advertised_schema_forbids_unknown_keys[26]` — every object schema says
  `additionalProperties: false` except the four free-form dicts
  (`arguments`, `metadata`, `requestor_context`, `_meta`).
- `test_server_gate_rejects_unknown_keys_before_dispatch[3]` — through
  `_handle_call_tool`; includes `gateway.health` (argless tool).
- `test_unknown_key_value_never_echoed` — a secret under a misspelled key comes
  back as `Input validation error: Additional properties are not allowed
  ('authorizatoin' was unexpected)` with the value absent from the response
  text and from `caplog`. (M-B3 below shows this goes RED if the gate is bypassed.)
- `test_meta_and_tracecontext_spellings_are_rejected` — `meta` and
  `traceContext` on `gateway.invoke` are rejected at the gate.
- Regenerate the snapshot; its diff (**+26 top-level and +N nested
  `additionalProperties: false`**) is the reviewable artifact.

### `CHANGELOG.md` (modify) — `[Unreleased]` → `### Changed` (**breaking for extra-key callers**)

"**Gateway tools now reject unknown argument keys.** Every `gateway.*` tool's
`inputSchema` carries `additionalProperties: false` (nested objects too, except
the free-form `arguments`, `_meta`, `task.metadata` and `requestor_context`
dicts), and the argument models are `extra="forbid"`. A misspelled or invented
key — `timeoutMs`, `servr`, `bogus` — is an `isError` result naming the key,
where it was previously dropped and the call ran with defaults
(Consiliency/pmcp#236). `gateway.invoke` no longer accepts `meta` or
`traceContext` as spellings of `_meta`/`trace_context` — neither was ever
advertised. Callers that relied on being lenient must remove the extra keys;
the error message names them."

**Release-note callout** (README "Gateway Tools" intro or the release body):
one paragraph, same content, headed *Breaking for agents sending extra keys*.

## Blast radius of B (measured)

- **In-repo direct callers of gateway tools with extra keys: one** —
  `tests/test_tools.py:6213` (above). `src/pmcp/cli.py` builds `auth_args`
  (`:2850-2856`, `:2899-2906`) from `server_name`, `credential`, `scope`,
  `env_var`, `auth_mode`, `elicitation_id`, `elicitation_url`,
  `consent_acknowledged` — all declared; `gateway.health {}`,
  `gateway.update_server {server_name}`, `gateway.provision {server_name}` —
  declared. `scripts/` contains no gateway tool call (`sse_flake_probe.py`
  mentions `gateway.invoke` in a comment only). `client/manager.py`'s
  `requestor_context` is *outbound* to downstream servers, not a gateway-tool
  argument.
- **Full suite on the fused spike (= B before any test edit; revision-1 tree): 4 failed, 4251
  passed, 3 skipped in 11:35.** All four pass on the A-only tree (measured:
  `4 passed`), so they are B's entire in-repo blast radius, and each is a test
  that sends keys its tool does not declare:

  | Test | Extra key(s) sent | Why it failed under B | B's edit |
  |---|---|---|---|
  | `tests/test_tools.py::TestInvokeErrorPaths::test_tenant_code_mode_invoke_policy_denied_audit` (`:6213`) | `trace_context.authorization` | `InvokeInput.model_validate` raises `extra_forbidden` (direct call, no gate) | send the secret under `_meta` (free-form) |
  | `tests/test_server.py::TestServerCreation::test_task_tools_are_routed_through_call_tool_handler` (`:247`) | `task_id` to `gateway.tasks_list` — one dict reused across four task tools | gate returns `Input validation error…` text; test `json.loads` it | per-tool arguments |
  | `tests/test_server.py::TestServerCreation::test_conformance_config_and_lifecycle_tools_route_json` (`:342`) | `operation`, `names` to the argless `config_status` / `get_startup_policy` | same | `{}` for the argless tools |
  | `tests/test_scoped_advisor_audit.py::test_scoped_server_filters_controls_and_writes_private_complete_audit` (`:162`, the `gateway.provision` call) | `tool_id`, `run_correlation_id`, `seat_correlation_id`, `evidence_label_digest` — deliberately raw values to prove the denied-call audit does not copy them | gate rejects before the policy denial the test is about | drop the four keys; the invoke calls earlier in the same test already prove audit filtering via the free-form `arguments` dict |

  A consequence worth stating: a gate rejection returns before
  `_record_scoped_invocation`, so under B an extra-key call is **not** written
  to the scoped-advisor audit. As *Scoped-audit gap on gate rejections (A3)*
  sets out, that is pre-existing on `main`, A moves the drift tools'
  model-rejections onto it, and B widens the set further. It is scoped out
  to Consiliency/pmcp#296, which lands before B.
- **Full suite on the B tree after those four edits** (revision-1 tree,
  **not re-measured on revision 2**): 4287 passed, 0 failed, 3 skipped, 25
  deselected in 9:56 (`scratchpad/b_full.log`). The 60 tests over the
  revision-1 A were B's new parametrized cases.
- **Full suite on the A tree (revision 2, frozen code `c79bf1a`, re-measured
  2026-09-26)**: **4277 passed, 0 failed, 3 skipped, 25 deselected** in 6:53,
  with no errors (`uv run pytest -m 'not live' -p no:cacheprovider -q` with
  `npm_config_cache`, `npm_config_store_dir` and `pnpm_config_store_dir`
  unset). That is 50 more than revision 1's A (4227), matching the schema
  file's growth from 163 to 213. A breaks nothing in-repo. Earlier revision-2
  trees: `72eaa76` and `d0722f4` gave 4272 each, `40b2ed5` gave 4273, and
  `972bc90` gave 4275. **On current `main` + A:** the A branch's base is
  `8dec131`, not `main`'s `9ca081e`. The board's claude seat assembled
  `9ca081e` + `a25dce0` and measured `4282 passed, 3 skipped, 25
  deselected`, with no errors. That is 4275 plus exactly the 7 newer C3 tests
  on `main` (`tests/test_client_manager.py` collects 256 there vs 249 on the
  A branch). It is the seat's measurement, not re-run here. At `c79bf1a` the
  expected equivalent is 4284, which is unmeasured. The coordinator's `d0722f4`
  run also hit 1 environmental teardown error in `test_workflow_guards`.
- **Full suite on `860636a` (baseline, throwaway worktree
  `/mnt/HC_Volume_105438154/worktrees/pmcp-236-head`)**: 4063 passed, 1 failed —
  `tests/test_manifest.py::TestMonitorInstall::test_monitor_reads_stderr`, which
  passed 3/3 when re-run in isolation; a load-induced flake (host load ≈16
  during the run) in a file no piece touches. The 4227 − 4063 = 164 extra
  revision-1 A tests were the new parametrized schema tests (revision 2: 4272).
  The `860636a` baseline was not re-run on `9ca081e`.
- **Argument for `forbid` everywhere rather than selectively**: every argument
  model is consumed by exactly one audience — a prompt-injectable agent — and
  every field that legitimately carries open content is already typed
  `dict[str, Any]` (which stays open: pydantic emits `additionalProperties: true`
  for it, measured). There is no argument model where an unknown *sibling* key
  has a benign meaning. The config-file models are the counter-case and stay
  `ignore`.

## Documentation impact

- `CHANGELOG.md`: one `### Fixed` entry for A, one `### Changed` entry for B
  (texts above). Both cite Consiliency/pmcp#236.
- `README.md`: the `gateway.invoke` row (`README.md:398`) already says
  "with argument validation"; B adds the breaking callout paragraph. Tool count
  unchanged (26) so `tests/test_baseline_constraints.py::TestReadmeToolCount` is unaffected.
- `tests/test_gateway_tool_schemas.py` docstring documents the snapshot
  regeneration rule ("regenerate deliberately, review the diff").

## Dependencies & order

1. A: apply the four verbatim bodies (*Verbatim bodies → A*, exact commands in
   *Embedding proof*) → generate the snapshot → review the snapshot against the
   HEAD schemas with the *Measurement scripts* probes run from a `main` checkout:
   the *only* differences must be the 23/26 `probe_head.py` list (18 constraint
   drift + 5 null-only), the 16 newly advertised properties, the 19 new
   descriptions and exactly one description change, `update_server.force`
   (`desc_cmp.py`: `same 54 differ 1`) → F4 host smoke test (before
   release) → CHANGELOG A → PR A.
2. B (after A merges, **and after Consiliency/pmcp#296 lands**; X1's guard
   test must be green on B's head): `types.py` flip + `populate_by_name` removal →
   `schema.py` no-arg schema → `handlers.py` trace-context branches →
   `test_tools.py:6213` → B tests (with the B-preamble type-list fix) →
   re-measure every B number → regenerate snapshot → CHANGELOG B + callout →
   PR B with the snapshot diff called out in the description.

## Verification

```bash
cd <worktree>
uv sync --all-extras -p 3.10                           # fresh worktree: without it pytest is the system one
uv run pytest tests/test_gateway_tool_schemas.py -p no:cacheprovider --cov-fail-under=0 -q
                                                       # A: 213 passed (measured at c79bf1a)
uv run pytest tests/test_gateway_tool_schemas.py tests/test_baseline_constraints.py \
  -p no:cacheprovider --cov-fail-under=0 -q            # A: 250 passed (measured at c79bf1a; baseline file alone 37, unchanged)
uv run pytest tests/test_tools.py -p no:cacheprovider --cov-fail-under=0 -q   # B: after the :6213 edit
env -u npm_config_cache -u npm_config_store_dir \
  uv run pytest -m 'not live' -p no:cacheprovider -q   # full suite; run it as a background task that notifies on exit
                                                       # A: 4277 passed, 3 skipped, 25 deselected (measured at c79bf1a; 4282 on current main + A, board seat)
uv run ruff check src/ tests/                          # A: "All checks passed!" (measured at c79bf1a)
uv run ruff format --check src/ tests/                 # A: "163 files already formatted" (measured at c79bf1a)
uv run mypy src/pmcp --exclude baml_client            # CI gate (test.yml:387); A: "no issues found in 50 source files" (measured at c79bf1a)
uv run python ~/code/pmcp/scripts/check_plan_consistency.py \
  .consiliency/plans/detailed-236-schema-drift-20260923-0915.md   # see *Consistency gate* for what it checks here
```

Edge cases exercised by the tests: a model field literally named `title`
(`_Outer.title`) must survive title-stripping; an aliased field (`_meta`) must
advertise under its alias; a `Literal | None` field must collapse to
`{"type": ["string", "null"], "enum": [..., None]}`; a required-nullable field
keeps its `anyOf`; the argless tools must be exactly the ones `server.py`
dispatches with `()`; dispatch names must equal registry names.

## Acceptance criteria — measured this session

### Piece A (tree: frozen revision-2 code `c79bf1a`; 213 passed green)

Re-measured 2026-09-26 in a detached worktree at `72eaa76`, after the full
suite there had finished (the mutants edit files the suite imports). Re-run
at `d0722f4` (counts identical), at `40b2ed5`, at `972bc90`, and **again at
`c79bf1a`**, after its full suite finished. The counts below are the
`c79bf1a` run: every row has the same failing tests as at `972bc90`, plus
two more passes from the two F1 test cases. (M-X1s gained the N1 test as a
second failure at `972bc90`.) Each
mutation was applied by exact-string replacement asserted to match once, shown
with `diff -u` against a saved copy, run against
`tests/test_gateway_tool_schemas.py`, then restored with `cp` from the saved
copy and proven by `cmp` and `git diff --quiet HEAD -- <file>`. Unmutated
baseline: `213 passed`. All RED for the named reason:

| # | Mutation (file:entity) | Confirmed diff | RED tests | Why it must fire |
|---|---|---|---|---|
| M1 | `handlers.py:_derived_gateway_tools` — HEAD's hand-written dict for `gateway.describe` (no `minLength`) (**the hand-corrupted schema**) | `-input_schema=input_schema_for(spec.input_model),` / `+input_schema=({…HEAD dict…} if spec.name == "gateway.describe" else input_schema_for(spec.input_model)),` | `test_advertised_schema_is_derived_from_registered_model[gateway.describe]`, `test_advertised_schemas_match_snapshot`, `test_server_gate_rejects_what_the_model_rejects[gateway.describe…]`, and incidentally `test_schemas_are_derived_once_per_process` (25 ≠ 26 calls) — 4 failed, 209 passed | someone reintroduces a hand-written schema that bypasses the builder |
| M2 | `handlers.py:_GATEWAY_TOOL_SPECS` — `gateway.describe` registered with `ConnectServerInput` | `-input_model=DescribeInput,` / `+input_model=ConnectServerInput,` | `test_handler_validates_arguments_with_the_registered_model[gateway.describe]`, `…accepts_the_minimal_valid_arguments[gateway.describe]`, snapshot, gate — 4 failed, 209 passed | the registry names a model the handler does not run |
| M3 | `types.py:DescribeInput.tool_id` — `min_length=1` → `2` | `-min_length=1, …` / `+min_length=2, …` | `test_advertised_schemas_match_snapshot`, `test_server_gate_rejects_what_the_model_rejects[gateway.describe…]` — 2 failed, 211 passed | an agent-facing contract change must be a reviewed snapshot diff |
| M4 | `schema.py:_collapse_nullable` — early `return node` (nullable `anyOf` no longer collapsed) | `+    return node` | `test_input_schema_for_normalises_pydantic_output`, snapshot, `…is_a_self_contained_mcp_input_schema[…]` ×13 — 15 failed, 198 passed. The synthetic-model test fires independently of the real tools | the post-processing is drift surface of its own |
| **M-A1** | `schema.py:_collapse_nullable` (`:94`) — drop `"null"`: `out["type"] = [inner_type, "null"]` → `out["type"] = inner_type` (revision 1's behaviour) | `-        out["type"] = [inner_type, "null"]` / `+        out["type"] = inner_type` | `test_optional_field_null_agrees_between_gate_and_model` **×42** (e.g. `gateway.submit_feedback {…, 'failed_tool_call': None}: gate=False model=True`), snapshot, `test_input_schema_for_normalises_pydantic_output` — 44 failed, 169 passed. All 42 fail, including the 14 fields `main` accepted only because it never declared them, since A declares them | the gate must accept `null` exactly where the model does (board A1) |
| **M-A2** | `handlers.py:get_gateway_tool_definitions` (`:689`) — bypass the cache at the call site: `list(_derived_gateway_tools())` → `list(_derived_gateway_tools.__wrapped__())` | `-    return list(_derived_gateway_tools())` / `+    return list(_derived_gateway_tools.__wrapped__())` | `test_schemas_are_derived_once_per_process` — **`assert 260 == 26`** (10 lookups × 26 derivations) — 1 failed, 212 passed | derivation must happen once per process, not per `tools/call` (board A2) |
| **M-X1** | `server.py:_handle_call_tool` — rename the dispatch branch `name == "gateway.health"` → `"gateway.health_internal"` | `-                elif name == "gateway.health":` / `+                elif name == "gateway.health_internal":` | **only** `test_every_dispatched_gateway_name_is_registered` — 1 failed, 212 passed. `test_server_dispatch_agrees_with_registry` stays green, which is the gap X1 closes | a dispatch branch for an unregistered name would skip the gate |
| **M-X2** | `handlers.py:GatewayTools.describe` — `DescribeInput.model_validate(` → `_DescribeInput.model_validate(` (a substring match, not a word match) | `-        parsed = DescribeInput.model_validate(input_data)` / `+        parsed = _DescribeInput.model_validate(input_data)` | `test_handler_validates_arguments_with_the_registered_model[gateway.describe]` — 1 failed, 212 passed | the handler-link check matches on a word boundary, not a substring |
| **M-W** | `types.py:InvokeOptions.timeout_ms` (`:808`) — drop `description="Timeout in milliseconds"`  | `-        default=30000, ge=1000, le=300000, description="Timeout in milliseconds"` / `+        default=30000, ge=1000, le=300000` | `…is_a_self_contained_mcp_input_schema[gateway.invoke]` and `[gateway.tasks_result]` — plus the snapshot — 3 failed, 210 passed (under `-k self_contained`: 2 failed, 24 passed). The `72eaa76` helper passes 26/26 on the same mutant | the walker must reach the `["object", "null"]` nested objects |
| **M-X1s** | `server.py:_handle_call_tool.call_tool` — X1 guard off: `if tool is None:` → `if False:` | `-                if tool is None:` / `+                if False:` | `test_a_dispatch_branch_for_an_unregistered_name_fails_closed` and `test_an_unregistered_invoke_never_reaches_the_scoped_audit_model` — 2 failed, 211 passed. The text pin stays green, because the dispatch source is unchanged | an ungated name must never reach a handler, whatever the dispatch condition's shape |
| *(bypass, not a test-RED mutant)* | `server.py` — the board seat's bypass: `elif name == "gateway.health" or name == "gateway.health2":` | as named | **none: 213 passed**, as expected, since the pin is lexical and the guard is behavioural. The guard's effect was measured directly with `probe_bypass.py`: guard on, `handler_ran=False response='{"error": true, "message": "Unknown tool: gateway.health2"}'`; guard off (M-X1s + bypass), `handler_ran=True response='{}'` | shows why X1 is structural: the pin cannot see this, the guard refuses it |
| **M-N1** | `server.py:_handle_call_tool.call_tool` — move the X1 guard back **below** the scoped-audit `InvokeInput.model_validate` check (the `40b2ed5` order) | the guard block and the scoped block swap places (full diff in the run log) | **only** `test_an_unregistered_invoke_never_reaches_the_scoped_audit_model` — 1 failed, 212 passed | an ungated `gateway.invoke` must not reach the scoped pydantic parse, whose error echoes values (board N1) |
| **M-N2** | `types.py:InvokeInput.evidence_label_digest` — drop `min_length=64, max_length=64` | `-        min_length=64,` / `-        max_length=64,` | `test_digest_pattern_agrees_between_gate_and_model`, snapshot — 2 failed, 211 passed | the gate's Python `$` accepts a trailing newline that pydantic's Rust `$` rejects; the lengths make both layers agree (board N2) |
| **M-F1** | `types.py:TaskMetadataInput.ttl` — drop `le=9_223_372_036_854_775_807` | `-        le=9_223_372_036_854_775_807,` | `test_ttl_range_agrees_between_gate_and_model[1e+20]`, `[9223372036854775808]`, snapshot — 3 failed, 210 passed | an integer property with no `maximum` lets the gate accept a float the model refuses with a value-echoing `int_parsing_size` (board round 4, F1) |

Note M1 and M3 both light the gate test: the gate test is what turns "the
schema says X" into "the transport enforces X".

**The A2 mutant to cite is M-A2, not "delete the decorator".** Removing
`@functools.cache` also turns `test_schemas_are_derived_once_per_process` RED
(measured at `72eaa76`, 1 failed, 207 passed), but for the wrong reason: the test's own
`handlers._derived_gateway_tools.cache_clear()` raises
`AttributeError: 'function' object has no attribute 'cache_clear'` before
anything is counted. That RED proves the decorator exists, not that
derivation happens once. M-A2 keeps the decorator and bypasses it at the call
site, and the test fails on the count (`260 == 26`), which is the property.

### Piece B (tree: fused spike + B additions — **revision-1 tree; not re-measured on revision 2**)

Every count below predates A1 (bare `type` for optionals), A2, X1 and the 45 new A
tests. On revision-2 A the base count is 213, not 163, and MB1's
`test_advertised_schema_forbids_unknown_keys` count depends on the type-list fix in
the B preamble. B's executor re-runs MB1–MB4 and adds one mutant for that fix.

B tree = fused spike + `populate_by_name` removed + `meta`/`traceContext`
branches dropped + the four test edits + A's and B's extra tests. Measured:
`ruff check` / `ruff format --check` clean; `tests/test_gateway_tool_schemas.py`
223 passed; with `tests/test_baseline_constraints.py` and the four edited
callers 264 passed; snapshot 712 lines, 31 × `additionalProperties: false`,
8 × `true`. Each mutation confirmed with `diff -u` against the saved B copy
(`scratchpad/mutate_B.out`), run against the schema test file, restored
(`diff -q` clean on six files). Parametrize ids collapsed to counts:

| # | Mutation (file:entity) | Confirmed diff | RED tests | Why it must fire |
|---|---|---|---|---|
| MB1 | `types.py:GatewayArguments` — `extra="forbid"` removed (B reverted to A at the model) | `-    model_config = ConfigDict(extra="forbid")` | 57 failed / 166 passed: `test_unknown_top_level_key_is_rejected_by_the_model` ×23, `test_advertised_schema_forbids_unknown_keys` ×23, `…nested_key…` ×4, `test_server_gate_rejects_unknown_keys_before_dispatch` ×2, `test_meta_and_tracecontext_spellings_are_rejected` ×2, `test_unknown_key_value_never_echoed`, `test_every_argument_model_extends_gateway_arguments`, snapshot | the one-line flip is the whole of B; every layer notices |
| MB2 | `schema.py:NO_ARGUMENTS_SCHEMA` — `additionalProperties: False` removed | `-    "additionalProperties": False,` | 5 failed / 218 passed: `test_advertised_schema_forbids_unknown_keys` ×3 (the argless tools), `test_server_gate_rejects_unknown_keys_before_dispatch[gateway.health]`, snapshot | the argless tools have no model; only the gate can reject their extras |
| MB3 | `server.py:_handle_call_tool` — `jsonschema.validate(...)` replaced by `pass` (gate bypassed) | `-jsonschema.validate(instance=arguments, schema=tool.input_schema)` / `+pass` | 9 failed / 214 passed: `test_unknown_key_value_never_echoed`, `test_server_gate_rejects_unknown_keys_before_dispatch` ×3, `test_meta_and_tracecontext_spellings_are_rejected` ×2, `test_server_gate_rejects_what_the_model_rejects` ×3 | with the gate gone, pydantic's `extra_forbidden` error — which carries `input_value='Bearer sk-…'` (measured) — is what the caller gets and what `server.py:429` logs |
| MB4 | `types.py:InvokeInput` — `populate_by_name=True` restored | `+    model_config = ConfigDict(populate_by_name=True)` | 1 failed / 222 passed: `test_meta_and_tracecontext_spellings_are_rejected[arguments0]` (`meta`) | the model would accept a spelling the advertised schema rejects — drift B must not reintroduce |

### Restoration proofs

- [x] Revision 2: the plan branch's `src/` and `tests/` are byte-identical to
  its base `8dec131` (`git diff --stat 8dec131 -- src tests` empty at commit
  time). Against `origin/main` @ `9ca081e` they differ only in the two files
  that Consiliency/pmcp#292 changed on `main` (`src/pmcp/client/manager.py`,
  `tests/test_client_manager.py`), none of which piece A touches. The revision
  changes only this plan and `plans/manifest.json`. The
  measurement worktree's mutations were each restored and proven by `cmp` +
  `git diff --quiet HEAD`, and the worktree was then removed.
- [x] Revision 1: `src/` and `tests/` byte-identical to `860636a` before the plan commit —
  measured after `finish.sh`: `git status --short` shows only `?? .consiliency/plans/detailed-236-schema-drift-20260923-0915.md`; `git status --short -- src tests` empty; zero untracked files under `src/` or `tests/`.
- [x] `origin/plan/236-schema-drift` == HEAD — proven after the push by `git rev-parse HEAD origin/plan/236-schema-drift` printing one sha twice (recorded in the delivery report; a plan cannot contain its own commit sha).

## Non-goals

- Moving validation out of the handlers into `server.py` via the registry (would
  remove the `inspect.getsource` link). Worth doing; not this issue.
- Changing `extra` on `LocalMcpServerConfig`, `RemoteMcpServerConfig`,
  `McpConfigFile` (config files, not agent input).
- Changing any handler's behaviour, any tool's description, or the tool count.
- Expressing the three validator-only constraints in JSON Schema.
- Downstream tools' schemas (`gateway.describe` output) — untouched.
- Recording gate rejections in the scoped-advisor audit (A3). The gap is
  pre-existing on `main`. Consiliency/pmcp#296, to land before B.
- Failing closed on unknown names with an early return *at the gate*
  (outside the audited path). X1 instead fails closed inside `call_tool`,
  where the audit records it.
- Value echo from **model-only validators** on gate-*passing* arguments (F3):
  the correlation-ID charset validator and the all-or-none `model_validator`.
  This is pre-existing on `main` and unchanged by A or B (see *Order: A
  first, then B*). Tracked as **Consiliency/pmcp#297**: render pydantic
  errors without `input_value` in the `except Exception` arm, e.g. catch
  `ValidationError` and format `e.errors(include_input=False)`.
- Value echo in `type`/`enum`/`pattern` gate errors for *declared* keys
  (pre-existing on `main`, unchanged by A or B).

## Execution Policy

- execute A: effort=low, reason=A is embedded verbatim and proven
  byte-identical to verified code (five bodies + a deterministic snapshot).
  The remaining work is review of the 814-line snapshot, which must be read against the measured HEAD schemas, not
  eyeballed.
- execute B: effort=medium (was low), reason=one-line flip plus four
  consequential edits, but every B number must be re-measured on revision-2
  A, the type-list fix to two test helpers needs its own mutant, and B is
  gated on Consiliency/pmcp#296 (A3) and on X1's guard test.
- Every PR to main needs panel CR + reconcile first (repo rule).

## Embedding proof (revision 2, re-measured 2026-09-26 against `c79bf1a`)

Proves that the A bodies below, applied exactly as *A — how an executor
applies these* instructs, reproduce the frozen, verified piece-A code byte for
byte. Fresh detached worktree at `origin/main` (`9ca081e`, 0 changes) →
`uv sync --all-extras -p 3.10` → the extractor taken **out of this plan**
(not a local copy) → the five extract commands → `git apply --check` + `git
apply` of the three patches → the snapshot generation command → `cmp` each of
the six resulting files against `git show c79bf1a:<path>`
(= `origin/wip/236-schema-drift-rev2-code`) → the schema test file:

```text
base: 9ca081e674806202dfa41864489cb9e3ae225dd9  clean: 0 changes
src/pmcp/tools/schema.py: 99 lines
tests/test_gateway_tool_schemas.py: 555 lines
<scratch>/types.patch: 634 lines
<scratch>/handlers.patch: 866 lines
<scratch>/server.patch: 20 lines
1 passed, 212 deselected in 0.18s
===== cmp against c79bf1a (= origin/wip/236-schema-drift-rev2-code)
cmp OK  src/pmcp/tools/schema.py  sha256=ddc7a14d17b91bcb
cmp OK  src/pmcp/tools/handlers.py  sha256=9b8c18905828826e
cmp OK  src/pmcp/types.py  sha256=767696eb3c959efc
cmp OK  src/pmcp/server.py  sha256=b6d1f494a7186c39
cmp OK  tests/test_gateway_tool_schemas.py  sha256=4707fd43d8870716
cmp OK  tests/fixtures/gateway_tool_schemas.json  sha256=5da153e64ed5970f
changed vs base:  M src/pmcp/server.py  M src/pmcp/tools/handlers.py  M src/pmcp/types.py ?? src/pmcp/tools/schema.py ?? tests/fixtures/gateway_tool_schemas.json ?? tests/test_gateway_tool_schemas.py
===== pytest
213 passed in 0.54s
```

Exactly the six files changed, and nothing else. The fixture `cmp` is the
check that the snapshot *content* matches. The generation run itself passes
by construction, since it writes the file it then reads. Pasting this output
into the plan was the only edit after the proof. The five bodies were then
re-extracted from the final plan text and re-`cmp`ed. The proof worktree was
removed afterwards. (Earlier proofs against `72eaa76`, `d0722f4` (five files
each, 208 tests), `40b2ed5` (six files, 209 tests), and `972bc90` and
`a25dce0` (six files, 211 tests) also passed every `cmp`.)

**Consistency gate.** `uv run python ~/code/pmcp/scripts/check_plan_consistency.py
.consiliency/plans/detailed-236-schema-drift-20260923-0915.md` →
`lane-contracted: 0   EC-proved node ids: 0 / consistent / blocking
inconsistencies: 0`. This is vacuous for a detailed plan. The gate
cross-checks phase-plan lane tables against `EC-*` node ids and verifies a
`roadmap_sha256` pin, and this plan has no lanes, no EC ids and no pin. The
same class of cross-check (the rule stated in three places must agree) was
done by hand for A1/A2/X1/A3 across the header, *Piece A*, *Acceptance
criteria* and *Piece B*.

## Verbatim bodies

### A — how an executor applies these (and the extractor)

Piece A is five byte-exact bodies plus a generated snapshot. From a fresh
worktree of `origin/main`:

```bash
PLAN=.consiliency/plans/detailed-236-schema-drift-20260923-0915.md   # read it from the plan branch
X=<scratch>/extract_plan_block.py                                       # the script below, saved verbatim
python3 $X $PLAN "### A — \`src/pmcp/tools/schema.py\`"      src/pmcp/tools/schema.py
python3 $X $PLAN "### A — \`tests/test_gateway_tool_schemas.py\`" tests/test_gateway_tool_schemas.py
python3 $X $PLAN "### A — \`src/pmcp/types.py\`"             <scratch>/types.patch
python3 $X $PLAN "### A — \`src/pmcp/tools/handlers.py\`"     <scratch>/handlers.patch
python3 $X $PLAN "### A — \`src/pmcp/server.py\`"            <scratch>/server.patch
git apply --check <scratch>/types.patch <scratch>/handlers.patch <scratch>/server.patch \
  && git apply <scratch>/types.patch <scratch>/handlers.patch <scratch>/server.patch
uv sync --all-extras -p 3.10
PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest tests/test_gateway_tool_schemas.py -q -k test_advertised_schemas_match_snapshot
uv run pytest tests/test_gateway_tool_schemas.py -q                     # expect 213 passed
```

The three patches are `git diff origin/main c79bf1a -- <file>` against
`origin/main` @ `9ca081e` (identical for these files to `860636a` and
`8dec131`). Their blank context lines carry one leading space. An editor that
strips trailing whitespace breaks them, and `git apply --check` then fails
loudly rather than half-applying. The snapshot writer is deterministic, so the
generated fixture is `cmp`-equal to the frozen one (see *Embedding proof*).

```python
"""Extract one verbatim body from the Consiliency/pmcp#236 plan, byte for byte.

usage: python extract_plan_block.py <plan.md> "<heading prefix>" <out-file>

Finds the single line that starts with the heading prefix, takes the first
fenced block after it (the fence line starts with three backticks), and writes
every line up to the closing fence (a line that is exactly three backticks),
each followed by a newline.
"""

import sys
from pathlib import Path

plan, heading, out = sys.argv[1], sys.argv[2], sys.argv[3]
lines = Path(plan).read_text().split("\n")
starts = [i for i, line in enumerate(lines) if line.startswith(heading)]
assert len(starts) == 1, f"heading {heading!r} found {len(starts)} times"
i = starts[0] + 1
while not lines[i].startswith("```"):
    i += 1
j = i + 1
while lines[j] != "```":
    j += 1
Path(out).write_text("".join(line + "\n" for line in lines[i + 1 : j]))
print(f"{out}: {j - i - 1} lines")
```

### A — `src/pmcp/tools/schema.py` (new module, whole file, 99 lines)

```python
"""Derive the gateway's advertised tool ``inputSchema`` from its argument model.

Every gateway tool validates its arguments with a pydantic model
(``pmcp.types.*Input``). The ``inputSchema`` advertised over ``tools/list`` —
and enforced by ``GatewayServer._handle_call_tool`` before the model ever
runs — is derived from that same model here, so the two cannot drift
(Consiliency/pmcp#236).

``model_json_schema()`` is not usable verbatim as an MCP ``inputSchema``:
it hoists nested models into ``$defs``/``$ref``, emits a ``title`` on every
property, spells optional fields as ``anyOf: [X, {"type": "null"}]`` with
``default: null``, and carries the model docstring as a top-level
``description``. :func:`input_schema_for` post-processes all four so the
advertised shape is self-contained and title-free, and an optional string is
``"type": ["string", "null"]`` — flat like the hand-written schemas were, but
accepting ``null`` exactly where the model does.
``tests/test_gateway_tool_schemas.py`` pins that post-processing.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel

#: Advertised for gateway tools that take no arguments at all.
NO_ARGUMENTS_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}

_REF_PREFIX = "#/$defs/"
_NULL_SCHEMA: dict[str, Any] = {"type": "null"}


def input_schema_for(model: type[BaseModel] | None) -> dict[str, Any]:
    """Return the MCP ``inputSchema`` for a gateway tool argument model.

    ``None`` means the tool takes no arguments.
    """
    if model is None:
        return deepcopy(NO_ARGUMENTS_SCHEMA)
    raw = model.model_json_schema(by_alias=True, mode="validation")
    defs = raw.pop("$defs", {})
    schema = _normalize(raw, defs)
    # The Tool carries its own description; the model docstring is not it.
    schema.pop("description", None)
    return schema


def _normalize(node: Any, defs: dict[str, Any]) -> Any:
    """Inline ``$ref``s, drop ``title``s, and collapse nullable ``anyOf``s."""
    if isinstance(node, list):
        return [_normalize(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith(_REF_PREFIX):
            raise ValueError(f"unsupported $ref in gateway tool schema: {ref}")
        target = _normalize(deepcopy(defs[ref[len(_REF_PREFIX) :]]), defs)
        siblings = _normalize({k: v for k, v in node.items() if k != "$ref"}, defs)
        return {**target, **siblings}
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title":
            continue  # pydantic's per-field/model title noise
        if key == "properties" and isinstance(value, dict):
            # Keys here are property NAMES (a field may be called "title").
            out[key] = {name: _normalize(prop, defs) for name, prop in value.items()}
        else:
            out[key] = _normalize(value, defs)
    return _collapse_nullable(out)


def _collapse_nullable(node: dict[str, Any]) -> dict[str, Any]:
    """``anyOf: [X, null]`` + ``default: null`` -> ``X`` with ``null`` still allowed.

    pydantic spells ``X | None = None`` as that ``anyOf``. The advertised
    schema keeps ``X``'s keywords flat (no ``anyOf``) and adds ``"null"`` to
    its ``type`` (and to its ``enum``, if any), so the gate accepts exactly
    what the model accepts: the field omitted, ``null``, or an ``X``. A
    nullable field WITHOUT a ``None`` default (required-but-nullable) is left
    as pydantic wrote it -- the collapse is only defined for the optional case.
    """
    any_of = node.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2 or _NULL_SCHEMA not in any_of:
        return node
    if "default" not in node or node["default"] is not None:
        return node
    (inner,) = [branch for branch in any_of if branch != _NULL_SCHEMA]
    rest = {k: v for k, v in node.items() if k not in ("anyOf", "default")}
    out = {**inner, **rest}
    inner_type = out.get("type")
    if isinstance(inner_type, str):
        out["type"] = [inner_type, "null"]
    elif isinstance(inner_type, list) and "null" not in inner_type:
        out["type"] = [*inner_type, "null"]
    if isinstance(out.get("enum"), list) and None not in out["enum"]:
        out["enum"] = [*out["enum"], None]
    return out
```

### A — `tests/test_gateway_tool_schemas.py` (new module, whole file, 555 lines, 213 tests)

```python
"""Advertised gateway tool schemas are derived from, and agree with, the
pydantic models that validate their arguments (Consiliency/pmcp#236).

`get_gateway_tool_definitions()` builds every `inputSchema` from
`GATEWAY_TOOL_INPUT_MODELS` via `input_schema_for`, and
`GatewayServer._handle_call_tool` enforces that advertised schema with
`jsonschema` before the handler's `Model.model_validate` runs. These tests
pin the three links in that chain — advertised == derived, derived == what
the handler validates with, and the post-processing that turns
`model_json_schema()` into an MCP `inputSchema` — so a hand edit to any one
of them fails here rather than drifting.

"Advertised == model" means advertised == the model's JSON-Schema PROJECTION.
Three things the models enforce are not expressible in the schema and stay
handler-only: `InvokeInput`'s correlation-ID charset validator and its
all-or-none `model_validator`, and `RegisterDiscoveredServerInput`'s
`_validate_package`. Everything the projection CAN express -- types, bounds,
enums, required, and (A1) `null` on optional fields -- agrees on what is
REQUIRED and on `null` (`test_optional_field_null_agrees_between_gate_and_model`
checks per field). It does not agree on type COERCION: pydantic's lax mode
accepts `1` / `"true"` for a boolean and `"5"` for a number, and the JSON-Schema
gate does not, so the gate is stricter there (stated in the plan). Nor, by
itself, on a regex `pattern`: the gate's Python `$` matches before a final
newline and pydantic's Rust `$` does not -- closed for the one `pattern` field
(`evidence_label_digest`) by length bounds, pinned by
`test_digest_pattern_agrees_between_gate_and_model`.
Nor on an integer's range: pydantic refuses a float past int64, so an
unbounded integer field (`task.ttl`) gets that bound advertised, pinned by
`test_ttl_range_agrees_between_gate_and_model`.
"""

from __future__ import annotations

import inspect
import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import jsonschema
import pytest
from pydantic import BaseModel, Field

from pmcp.server import GatewayServer
from pmcp.tools.handlers import (
    GATEWAY_TOOL_INPUT_MODELS,
    GatewayTools,
    get_gateway_tool_definitions,
)
from pmcp.tools.schema import NO_ARGUMENTS_SCHEMA, input_schema_for

SNAPSHOT = Path(__file__).parent / "fixtures" / "gateway_tool_schemas.json"

#: The smallest argument set each tool accepts. `{}` for the no-argument tools.
MINIMAL_VALID_ARGUMENTS: dict[str, dict[str, Any]] = {
    "gateway.catalog_search": {},
    "gateway.describe": {"tool_id": "srv::tool"},
    "gateway.invoke": {"tool_id": "srv::tool"},
    "gateway.refresh": {},
    "gateway.connect_server": {"server_name": "srv"},
    "gateway.disconnect_server": {"server_name": "srv"},
    "gateway.restart_server": {"server_name": "srv"},
    "gateway.health": {},
    "gateway.config_status": {},
    "gateway.get_startup_policy": {},
    "gateway.set_startup_policy": {"operation": "add"},
    "gateway.request_capability": {"query": "scrape a site"},
    "gateway.sync_environment": {},
    "gateway.provision": {"server_name": "srv"},
    "gateway.update_server": {"server_name": "srv"},
    "gateway.auth_connect": {"server_name": "srv"},
    "gateway.submit_feedback": {"title": "a title here", "description": "d"},
    "gateway.provision_status": {"job_id": "job"},
    "gateway.list_pending": {},
    "gateway.cancel": {"request_id": "srv::1"},
    "gateway.tasks_list": {},
    "gateway.tasks_get": {"server_name": "srv", "task_id": "t"},
    "gateway.tasks_result": {"server_name": "srv", "task_id": "t"},
    "gateway.tasks_cancel": {"server_name": "srv", "task_id": "t"},
    "gateway.search_registry": {"query": "github"},
    "gateway.register_discovered_server": {"package": "pkg", "server_name": "srv"},
}

TOOL_NAMES = [tool.name for tool in get_gateway_tool_definitions()]
TOOLS_WITH_MODELS = [n for n in TOOL_NAMES if GATEWAY_TOOL_INPUT_MODELS[n] is not None]
TOOLS_WITHOUT_MODELS = [n for n in TOOL_NAMES if GATEWAY_TOOL_INPUT_MODELS[n] is None]


def _model_types(annotation: Any) -> list[type[BaseModel]]:
    import typing

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [t for arg in typing.get_args(annotation) for t in _model_types(arg)]


def _tool(name: str):
    return next(t for t in get_gateway_tool_definitions() if t.name == name)


def _handler_method(name: str) -> str:
    return name.removeprefix("gateway.")


def _object_schemas(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every object schema reachable from `schema`, itself included."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            kind = node.get("type")
            # An optional nested object is typed ["object", "null"] (A1).
            is_object = kind == "object" or (
                isinstance(kind, list) and "object" in kind
            )
            if is_object and "properties" in node:
                found.append(node)
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    for prop in value.values():
                        walk(prop)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return found


def _schema_keywords(schema: dict[str, Any]) -> set[str]:
    """Every dict key used as a schema keyword (property NAMES excluded)."""
    keys: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                keys.add(key)
                if key == "properties" and isinstance(value, dict):
                    for prop in value.values():
                        walk(prop)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return keys


# --- registry <-> advertised <-> handler --------------------------------------


def test_registry_lists_every_advertised_tool_in_order() -> None:
    assert list(GATEWAY_TOOL_INPUT_MODELS) == TOOL_NAMES
    assert set(MINIMAL_VALID_ARGUMENTS) == set(TOOL_NAMES)


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_advertised_schema_is_derived_from_registered_model(name: str) -> None:
    """A hand-edited inputSchema no longer matches its model and fails here."""
    assert _tool(name).input_schema == input_schema_for(GATEWAY_TOOL_INPUT_MODELS[name])


@pytest.mark.parametrize("name", TOOLS_WITH_MODELS)
def test_handler_validates_arguments_with_the_registered_model(name: str) -> None:
    """The model the schema is derived from is the one the handler runs."""
    model = GATEWAY_TOOL_INPUT_MODELS[name]
    assert model is not None
    source = inspect.getsource(getattr(GatewayTools, _handler_method(name)))
    assert re.search(rf"\b{model.__name__}\.model_validate\(", source), (
        f"{name}: handler does not validate with {model.__name__}"
    )


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_server_dispatch_agrees_with_registry(name: str) -> None:
    """Tools registered with no model are dispatched with no arguments."""
    source = inspect.getsource(GatewayServer._handle_call_tool)
    method = _handler_method(name)
    if GATEWAY_TOOL_INPUT_MODELS[name] is None:
        assert f"self._gateway_tools.{method}()" in source
    else:
        assert f"self._gateway_tools.{method}(" in source
        assert f"self._gateway_tools.{method}()" not in source


def test_every_dispatched_gateway_name_is_registered() -> None:
    """X1: `_handle_call_tool` validates only names it finds in the registry,
    so a dispatch branch for an unregistered name would skip the schema gate
    and hand raw arguments to its handler. Pin the two sets equal, both ways
    (the parametrised test above only proves registry -> dispatch)."""
    source = inspect.getsource(GatewayServer._handle_call_tool)
    dispatched = set(re.findall(r'\bname == "(gateway\.[a-z_]+)"', source))
    assert len(dispatched) >= len(TOOL_NAMES) - 1  # the pattern matched the branches
    assert dispatched == set(TOOL_NAMES), {
        "dispatched, not registered": sorted(dispatched - set(TOOL_NAMES)),
        "registered, not dispatched": sorted(set(TOOL_NAMES) - dispatched),
    }


@pytest.mark.parametrize("name", TOOLS_WITHOUT_MODELS)
def test_no_argument_tools_advertise_an_empty_object(name: str) -> None:
    assert _tool(name).input_schema == NO_ARGUMENTS_SCHEMA


# --- the advertised shape ------------------------------------------------------


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_advertised_schema_is_a_self_contained_mcp_input_schema(name: str) -> None:
    schema = _tool(name).input_schema
    assert schema["type"] == "object"
    keywords = _schema_keywords(schema)
    assert not keywords & {"$ref", "$defs", "title"}, keywords
    for obj in _object_schemas(schema):
        for prop_name, prop in obj["properties"].items():
            assert "anyOf" not in prop, f"{name}.{prop_name}: nullable anyOf survived"
            assert isinstance(prop.get("description"), str) and prop["description"], (
                f"{name}.{prop_name}: every advertised argument needs a description"
            )


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_advertised_schema_accepts_the_minimal_valid_arguments(name: str) -> None:
    arguments = MINIMAL_VALID_ARGUMENTS[name]
    jsonschema.validate(instance=arguments, schema=_tool(name).input_schema)
    model = GATEWAY_TOOL_INPUT_MODELS[name]
    if model is not None:
        model.model_validate(arguments)


def test_advertised_schemas_match_snapshot() -> None:
    """The agent-facing API changed: regenerate the snapshot deliberately with
    `PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest tests/test_gateway_tool_schemas.py`
    and review the diff."""
    current = {t.name: t.input_schema for t in get_gateway_tool_definitions()}
    if os.environ.get("PMCP_UPDATE_SCHEMA_SNAPSHOT") == "1":
        SNAPSHOT.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    assert json.loads(SNAPSHOT.read_text()) == current


# --- the post-processing itself ------------------------------------------------


class _Inner(BaseModel):
    """Inner doc."""

    level: Literal["a", "b"] | None = Field(default=None, description="Level")


class _Outer(BaseModel):
    """Outer doc (dropped: the Tool carries its own description)."""

    name: str = Field(min_length=1, description="Name")
    title: str | None = Field(default=None, description="A field called title")
    count: int = Field(default=3, ge=1, le=9, description="Count")
    inner: _Inner | None = Field(default=None, description="Inner")
    meta: dict[str, Any] | None = Field(default=None, alias="_meta", description="Meta")


def test_input_schema_for_normalises_pydantic_output() -> None:
    assert input_schema_for(_Outer) == {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1, "description": "Name"},
            "title": {
                "type": ["string", "null"],
                "description": "A field called title",
            },
            "count": {
                "type": "integer",
                "default": 3,
                "minimum": 1,
                "maximum": 9,
                "description": "Count",
            },
            "inner": {
                "type": ["object", "null"],
                "description": "Inner",
                "properties": {
                    "level": {
                        "type": ["string", "null"],
                        "enum": ["a", "b", None],
                        "description": "Level",
                    }
                },
            },
            "_meta": {
                "type": ["object", "null"],
                "additionalProperties": True,
                "description": "Meta",
            },
        },
    }


class _RequiredNullable(BaseModel):
    value: str | None = Field(description="Nullable but required")


def test_required_nullable_field_is_left_as_pydantic_wrote_it() -> None:
    """The collapse is defined for `X | None = None` only; a required nullable
    field has no `default: null` and keeps its `anyOf`."""
    prop = input_schema_for(_RequiredNullable)["properties"]["value"]
    assert prop == {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "description": "Nullable but required",
    }


def _optional_fields(model: type[BaseModel]) -> list[str]:
    return [
        (f.alias or n)
        for n, f in model.model_fields.items()
        if not f.is_required() and f.default is None
    ]


def _nullable_cases() -> list[tuple[str, dict[str, Any]]]:
    """Every (tool, arguments) where one optional field -- top-level or one
    level down inside a nested argument model -- is sent as explicit null."""
    cases: list[tuple[str, dict[str, Any]]] = []
    for name in TOOLS_WITH_MODELS:
        model = GATEWAY_TOOL_INPUT_MODELS[name]
        assert model is not None
        base = MINIMAL_VALID_ARGUMENTS[name]
        for field in _optional_fields(model):
            cases.append((name, {**base, field: None}))
        for fname, finfo in model.model_fields.items():
            for nested in _model_types(finfo.annotation):
                for sub in _optional_fields(nested):
                    cases.append((name, {**base, (finfo.alias or fname): {sub: None}}))
    return cases


@pytest.mark.parametrize(("name", "arguments"), _nullable_cases())
def test_optional_field_null_agrees_between_gate_and_model(
    name: str, arguments: dict[str, Any]
) -> None:
    """A1: the gate accepts explicit null for an optional field iff the model
    does. On HEAD the hand-written schemas rejected null where the model took
    it (`invoke.task`, `tasks_*.requestor_context`, ...)."""
    from pydantic import ValidationError

    model = GATEWAY_TOOL_INPUT_MODELS[name]
    assert model is not None
    try:
        model.model_validate(arguments)
        model_accepts = True
    except ValidationError:
        model_accepts = False
    try:
        jsonschema.validate(instance=arguments, schema=_tool(name).input_schema)
        gate_accepts = True
    except jsonschema.ValidationError:
        gate_accepts = False
    assert gate_accepts == model_accepts, (
        f"{name} {arguments}: gate={gate_accepts} model={model_accepts}"
    )


def test_schemas_are_derived_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """A2: deriving 23 schemas costs ~13 ms; `GatewayServer` reads the tool list
    on every tools/call and tools/list, so it must be built once, not per call."""
    import pmcp.tools.handlers as handlers

    calls: list[Any] = []
    real = handlers.input_schema_for
    monkeypatch.setattr(
        handlers, "input_schema_for", lambda m: (calls.append(m), real(m))[1]
    )
    handlers._derived_gateway_tools.cache_clear()
    try:
        srv = GatewayServer()
        for _ in range(5):
            get_gateway_tool_definitions()
            srv._find_gateway_tool("gateway.describe")
        assert len(calls) == len(TOOL_NAMES), len(calls)
    finally:
        handlers._derived_gateway_tools.cache_clear()


def test_input_schema_for_none_is_the_empty_object() -> None:
    assert input_schema_for(None) == NO_ARGUMENTS_SCHEMA
    assert input_schema_for(None) is not NO_ARGUMENTS_SCHEMA


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_advertised_schema_is_valid_json_schema(name: str) -> None:
    jsonschema.Draft202012Validator.check_schema(_tool(name).input_schema)


# --- the gate now enforces what the model enforces ----------------------------


async def _call_through_gate(name: str, arguments: dict[str, Any]) -> Any:
    """Drive `tools/call` through `GatewayServer._handle_call_tool`, which runs
    the jsonschema gate against the advertised schema before dispatch."""
    from unittest.mock import MagicMock

    from mcp.server.connection import Connection
    from mcp.server.context import ServerRequestContext
    from mcp.server.session import ServerSession
    from mcp.types import CallToolRequestParams

    srv = GatewayServer()
    srv._create_server(instructions="test")
    assert srv._server is not None
    entry = srv._server.get_request_handler("tools/call")
    assert entry is not None
    connection = Connection.from_envelope("2025-11-25", None, None)
    ctx = ServerRequestContext(
        session=ServerSession(MagicMock(), connection),
        lifespan_context={},
        protocol_version="2025-11-25",
        method="test",
    )
    return await entry.handler(
        ctx, CallToolRequestParams(name=name, arguments=arguments)
    )


@pytest.mark.parametrize(
    ("name", "arguments", "fragment"),
    [
        ("gateway.describe", {"tool_id": ""}, "should be non-empty"),
        (
            "gateway.submit_feedback",
            {"title": "short", "description": "d"},
            "is too short",
        ),
        (
            "gateway.tasks_result",
            {"server_name": "s", "task_id": "t", "options": {"max_output_chars": 5}},
            "less than the minimum",
        ),
    ],
)
@pytest.mark.asyncio
async def test_server_gate_rejects_what_the_model_rejects(
    name: str, arguments: dict[str, Any], fragment: str
) -> None:
    """Constraints that only the model enforced on HEAD (minLength, minimum...)
    are now advertised, so the gate rejects them with the gate's error shape."""
    from mcp.types import CallToolResult

    result = await _call_through_gate(name, arguments)
    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert text.startswith("Input validation error:"), text
    assert fragment in text, text


@pytest.mark.asyncio
async def test_a_dispatch_branch_for_an_unregistered_name_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """X1, structurally: a name the registry does not list never reaches a
    handler, even when `_handle_call_tool` has a dispatch branch for it (the
    board's bypass was `elif name == "gateway.health" or name == "gateway.health2"`,
    which the text pin above cannot see). Simulated by unregistering a name
    that has a branch: the gate is skipped, so dispatch must refuse it."""
    from mcp.types import CallToolRequestParams

    srv = GatewayServer()
    real_find = srv._find_gateway_tool
    monkeypatch.setattr(
        srv,
        "_find_gateway_tool",
        lambda name: None if name == "gateway.health" else real_find(name),
    )
    called: list[str] = []

    async def health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        called.append("health")
        return {}

    monkeypatch.setattr(srv._gateway_tools, "health", health)
    result = await srv._handle_call_tool(
        None,  # type: ignore[arg-type]
        CallToolRequestParams(name="gateway.health", arguments={"x": "Bearer sk-x"}),
    )
    assert called == [], "an ungated name reached its handler"
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert "Unknown tool: gateway.health" in text, text


@pytest.mark.asyncio
async def test_an_unregistered_invoke_never_reaches_the_scoped_audit_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-closed guard runs BEFORE the scoped-audit `InvokeInput` check:
    were `gateway.invoke` ever unregistered, its ungated arguments would
    otherwise reach pydantic, whose error echoes the value into the response
    and the log (board round 3, N1)."""
    from mcp.types import CallToolRequestParams

    srv = GatewayServer()
    srv._scoped_advisor_audit = object()  # type: ignore[assignment]
    monkeypatch.setattr(srv, "_require_scoped_audit", lambda: None)
    real_find = srv._find_gateway_tool
    monkeypatch.setattr(
        srv,
        "_find_gateway_tool",
        lambda name: None if name == "gateway.invoke" else real_find(name),
    )
    monkeypatch.setattr(srv, "_record_scoped_invocation", lambda **_kw: None)
    secret = "SECRETVALUE-" + "x" * 40
    result = await srv._handle_call_tool(
        None,  # type: ignore[arg-type]
        CallToolRequestParams(
            name="gateway.invoke",
            arguments={"tool_id": "a::b", "run_correlation_id": secret + "!"},
        ),
    )
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert "Unknown tool: gateway.invoke" in text, text
    assert "SECRETVALUE" not in text, text


def test_digest_pattern_agrees_between_gate_and_model() -> None:
    """A trailing newline passes Python's `$` but not pydantic's: the length
    bounds make the gate reject it too (board round 3, N2)."""
    from pmcp.types import InvokeInput

    schema = _tool("gateway.invoke").input_schema
    value = "a" * 64 + "\n"
    args = {"tool_id": "a::b", "evidence_label_digest": value}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(args, schema)
    with pytest.raises(Exception):
        InvokeInput.model_validate(args)


@pytest.mark.parametrize("ttl", [1e20, 2**63])
def test_ttl_range_agrees_between_gate_and_model(ttl: float) -> None:
    """`task.ttl` past int64: the model refuses it, so the gate must too
    (board round 4, F1: `1e20` passed the gate on main and under A)."""
    from pmcp.types import InvokeInput

    schema = _tool("gateway.invoke").input_schema
    args = {"tool_id": "a::b", "task": {"ttl": ttl}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(args, schema)
    with pytest.raises(Exception):
        InvokeInput.model_validate(args)
    ok = {"tool_id": "a::b", "task": {"ttl": 3600}}
    jsonschema.validate(ok, schema)
    InvokeInput.model_validate(ok)
```

### A — `src/pmcp/types.py` (`git apply` patch against `origin/main`, +281 / −110)

```diff
diff --git a/src/pmcp/types.py b/src/pmcp/types.py
index 215088d..6496e37 100644
--- a/src/pmcp/types.py
+++ b/src/pmcp/types.py
@@ -77,12 +77,26 @@ DEFAULT_AUTH_STATE_SEMANTICS: dict[AuthState, AuthStateSemanticsInfo] = {
 }
 
 
-class TraceContextInfo(BaseModel):
+class GatewayArguments(BaseModel):
+    """Base for every model that parses arguments an agent sends to a gateway
+    tool, and for every model nested inside one. The advertised ``inputSchema``
+    of each gateway tool is derived from its model (Consiliency/pmcp#236), so
+    this base marks which models are agent-facing argument contracts. Models
+    that parse *downstream* data (McpTaskInfo, registry results, .mcp.json)
+    must NOT use this base.
+    """
+
+
+class TraceContextInfo(GatewayArguments):
     """OpenTelemetry-style trace context accepted by PMCP-owned surfaces."""
 
-    traceparent: str | None = None
-    tracestate: str | None = None
-    baggage: str | None = None
+    traceparent: str | None = Field(
+        default=None, description="W3C traceparent header value"
+    )
+    tracestate: str | None = Field(
+        default=None, description="W3C tracestate header value"
+    )
+    baggage: str | None = Field(default=None, description="W3C baggage header value")
 
 
 class GatewayAuditEvent(BaseModel):
@@ -327,15 +341,25 @@ class StartupPolicySource(BaseModel):
     error: str | None = None
 
 
-class StartupPolicyOperation(BaseModel):
+class StartupPolicyOperation(GatewayArguments):
     """Input for previewing or applying autoStart mutations."""
 
-    operation: Literal["add", "remove", "set"]
-    names: list[str] = Field(default_factory=list)
-    source: ConfigSourceName | None = None
-    path: str | None = None
-    dry_run: bool = True
-    apply: bool = False
+    operation: Literal["add", "remove", "set"] = Field(
+        description="Mutation to apply to the autoStart list"
+    )
+    names: list[str] = Field(
+        default_factory=list, description="Server names the operation applies to"
+    )
+    source: ConfigSourceName | None = Field(
+        default=None, description="Config source to edit (project, user, or custom)"
+    )
+    path: str | None = Field(
+        default=None, description="Explicit config file path (overrides source)"
+    )
+    dry_run: bool = Field(default=True, description="Preview without writing")
+    apply: bool = Field(
+        default=False, description="Write the change (requires dry_run=false)"
+    )
 
 
 class StartupPolicyPreview(BaseModel):
@@ -538,22 +562,41 @@ class McpTaskRecord(McpTaskInfo):
     requestor_context: dict[str, Any] | None = None
 
 
-class TaskMetadataInput(BaseModel):
+class TaskMetadataInput(GatewayArguments):
     """Task metadata for task-augmented tool invocation."""
 
-    enabled: bool = True
-    metadata: dict[str, Any] | None = None
-    ttl: int | None = None
-    poll_interval: float | None = None
-    requestor_context: dict[str, Any] | None = None
+    enabled: bool = Field(
+        default=True, description="Run as an MCP task when the server supports it"
+    )
+    metadata: dict[str, Any] | None = Field(
+        default=None, description="Opaque task metadata forwarded downstream"
+    )
+    ttl: int | None = Field(
+        default=None,
+        # int64: pydantic already refuses a float past it (`int_parsing_size`),
+        # so advertise it and let the gate refuse `1e20` too, rather than
+        # passing it on to the model (Consiliency/pmcp#236, board round 4)
+        le=9_223_372_036_854_775_807,
+        description="Requested task TTL in seconds",
+    )
+    poll_interval: float | None = Field(
+        default=None, description="Seconds between task status polls"
+    )
+    requestor_context: dict[str, Any] | None = Field(
+        default=None, description="Opaque requestor context forwarded downstream"
+    )
 
 
-class TasksListInput(BaseModel):
+class TasksListInput(GatewayArguments):
     """Input for gateway.tasks_list."""
 
-    server_name: str | None = None
-    cursor: str | None = None
-    requestor_context: dict[str, Any] | None = None
+    server_name: str | None = Field(default=None, description="Optional server filter")
+    cursor: str | None = Field(
+        default=None, description="Optional downstream pagination cursor"
+    )
+    requestor_context: dict[str, Any] | None = Field(
+        default=None, description="Opaque requestor context forwarded downstream"
+    )
 
 
 class TasksListOutput(BaseModel):
@@ -565,12 +608,14 @@ class TasksListOutput(BaseModel):
     errors: list[str] | None = None
 
 
-class TasksGetInput(BaseModel):
+class TasksGetInput(GatewayArguments):
     """Input for gateway.tasks_get."""
 
-    server_name: str = Field(min_length=1)
-    task_id: str = Field(min_length=1)
-    requestor_context: dict[str, Any] | None = None
+    server_name: str = Field(min_length=1, description="Server that owns the task")
+    task_id: str = Field(min_length=1, description="Opaque downstream task ID")
+    requestor_context: dict[str, Any] | None = Field(
+        default=None, description="Opaque requestor context forwarded downstream"
+    )
 
 
 class TasksGetOutput(BaseModel):
@@ -581,13 +626,17 @@ class TasksGetOutput(BaseModel):
     errors: list[str] | None = None
 
 
-class TasksResultInput(BaseModel):
+class TasksResultInput(GatewayArguments):
     """Input for gateway.tasks_result."""
 
-    server_name: str = Field(min_length=1)
-    task_id: str = Field(min_length=1)
-    options: InvokeOptions | None = None
-    requestor_context: dict[str, Any] | None = None
+    server_name: str = Field(min_length=1, description="Server that owns the task")
+    task_id: str = Field(min_length=1, description="Opaque downstream task ID")
+    options: InvokeOptions | None = Field(
+        default=None, description="Output redaction and truncation options"
+    )
+    requestor_context: dict[str, Any] | None = Field(
+        default=None, description="Opaque requestor context forwarded downstream"
+    )
 
 
 class TasksResultOutput(BaseModel):
@@ -602,13 +651,15 @@ class TasksResultOutput(BaseModel):
     errors: list[str] | None = None
 
 
-class TasksCancelInput(BaseModel):
+class TasksCancelInput(GatewayArguments):
     """Input for gateway.tasks_cancel."""
 
-    server_name: str = Field(min_length=1)
-    task_id: str = Field(min_length=1)
-    force: bool = False
-    requestor_context: dict[str, Any] | None = None
+    server_name: str = Field(min_length=1, description="Server that owns the task")
+    task_id: str = Field(min_length=1, description="Opaque downstream task ID")
+    force: bool = Field(default=False, description="Cancel even if the task is healthy")
+    requestor_context: dict[str, Any] | None = Field(
+        default=None, description="Opaque requestor context forwarded downstream"
+    )
 
 
 class TasksCancelOutput(BaseModel):
@@ -624,21 +675,36 @@ class TasksCancelOutput(BaseModel):
 # === Gateway Tool Input/Output Types ===
 
 
-class CatalogFilters(BaseModel):
+class CatalogFilters(GatewayArguments):
     """Filters for catalog search."""
 
-    server: str | None = None
-    tags: list[str] | None = None
-    risk_max: Literal["low", "medium", "high"] | None = None
+    server: str | None = Field(
+        default=None, description="Filter to tools from a specific server"
+    )
+    tags: list[str] | None = Field(
+        default=None, description="Filter to tools with any of these tags"
+    )
+    risk_max: Literal["low", "medium", "high"] | None = Field(
+        default=None, description="Maximum risk level to include"
+    )
 
 
-class CatalogSearchInput(BaseModel):
+class CatalogSearchInput(GatewayArguments):
     """Input for gateway.catalog_search."""
 
-    query: str | None = None
-    filters: CatalogFilters | None = None
-    limit: int = Field(default=20, ge=1, le=100)
-    include_offline: bool = False
+    query: str | None = Field(
+        default=None,
+        description="Search query to match against tool names, descriptions, and tags",
+    )
+    filters: CatalogFilters | None = Field(
+        default=None, description="Narrow results by server, tags, or risk level"
+    )
+    limit: int = Field(
+        default=20, ge=1, le=100, description="Maximum number of results to return"
+    )
+    include_offline: bool = Field(
+        default=False, description="Include tools from offline servers"
+    )
 
 
 class CapabilityCard(BaseModel):
@@ -688,10 +754,12 @@ class CatalogSearchOutput(BaseModel):
     manifest_candidates: list[CapabilityCandidate] = Field(default_factory=list)
 
 
-class DescribeInput(BaseModel):
+class DescribeInput(GatewayArguments):
     """Input for gateway.describe."""
 
-    tool_id: str = Field(min_length=1)
+    tool_id: str = Field(
+        min_length=1, description='The tool ID in format "server_name::tool_name"'
+    )
 
 
 class ArgInfo(BaseModel):
@@ -740,28 +808,71 @@ class SchemaCard(BaseModel):
     feedback_hint: str | None = None
 
 
-class InvokeOptions(BaseModel):
+class InvokeOptions(GatewayArguments):
     """Options for tool invocation."""
 
-    timeout_ms: int = Field(default=30000, ge=1000, le=300000)
-    max_output_chars: int | None = Field(default=None, ge=100, le=100000)
-    redact_secrets: bool = False
+    timeout_ms: int = Field(
+        default=30000, ge=1000, le=300000, description="Timeout in milliseconds"
+    )
+    max_output_chars: int | None = Field(
+        default=None,
+        ge=100,
+        le=100000,
+        description="Maximum output characters (truncated if exceeded)",
+    )
+    redact_secrets: bool = Field(
+        default=False, description="Redact detected secrets from output"
+    )
 
 
-class InvokeInput(BaseModel):
+class InvokeInput(GatewayArguments):
     """Input for gateway.invoke."""
 
     model_config = ConfigDict(populate_by_name=True)
 
-    tool_id: str = Field(min_length=1)
-    arguments: dict[str, Any] = Field(default_factory=dict)
-    task: TaskMetadataInput | None = None
-    options: InvokeOptions | None = None
-    trace_context: TraceContextInfo | None = None
-    meta: dict[str, Any] | None = Field(default=None, alias="_meta")
-    run_correlation_id: str | None = Field(default=None, min_length=1, max_length=128)
-    seat_correlation_id: str | None = Field(default=None, min_length=1, max_length=128)
-    evidence_label_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
+    tool_id: str = Field(
+        min_length=1, description='The tool ID in format "server_name::tool_name"'
+    )
+    arguments: dict[str, Any] = Field(
+        default_factory=dict,
+        description="Arguments to pass to the tool (must match tool schema)",
+    )
+    task: TaskMetadataInput | None = Field(
+        default=None, description="Run as an MCP task (long-running invocation)"
+    )
+    options: InvokeOptions | None = Field(
+        default=None, description="Timeout, output truncation, and redaction options"
+    )
+    trace_context: TraceContextInfo | None = Field(
+        default=None, description="Trace context forwarded to the downstream server"
+    )
+    meta: dict[str, Any] | None = Field(
+        default=None,
+        alias="_meta",
+        description="Request metadata; trace context keys are forwarded downstream",
+    )
+    run_correlation_id: str | None = Field(
+        default=None,
+        min_length=1,
+        max_length=128,
+        description="Scoped-advisor run correlation ID",
+    )
+    seat_correlation_id: str | None = Field(
+        default=None,
+        min_length=1,
+        max_length=128,
+        description="Scoped-advisor seat correlation ID",
+    )
+    evidence_label_digest: str | None = Field(
+        default=None,
+        # the lengths pin what the pattern means in both regex dialects: the
+        # gate's Python `$` also matches before a final newline, pydantic's
+        # Rust `$` does not (Consiliency/pmcp#236, board round 3)
+        min_length=64,
+        max_length=64,
+        pattern=r"^[0-9a-f]{64}$",
+        description="SHA-256 digest of the caller evidence label",
+    )
 
     @field_validator("run_correlation_id", "seat_correlation_id")
     @classmethod
@@ -809,12 +920,19 @@ class InvokeOutput(BaseModel):
     url_elicitations: list[UrlElicitationInfo] | None = None
 
 
-class RefreshInput(BaseModel):
+class RefreshInput(GatewayArguments):
     """Input for gateway.refresh."""
 
-    source: Literal["claude_config", "custom"] | None = None
-    reason: str | None = None
-    force: bool = False
+    source: Literal["claude_config", "custom"] | None = Field(
+        default=None, description="Config source to reload from"
+    )
+    reason: str | None = Field(
+        default=None, description="Reason for refresh (for logging)"
+    )
+    force: bool = Field(
+        default=False,
+        description="Cancel pending downstream requests before refreshing",
+    )
 
 
 class RefreshOutput(BaseModel):
@@ -836,24 +954,32 @@ class RefreshOutput(BaseModel):
     mcp_tasks_remaining: int = 0
 
 
-class ConnectServerInput(BaseModel):
+class ConnectServerInput(GatewayArguments):
     """Input for gateway.connect_server."""
 
-    server_name: str = Field(min_length=1, description="Server to connect")
+    server_name: str = Field(min_length=1, description="Name of the server to connect")
 
 
-class DisconnectServerInput(BaseModel):
+class DisconnectServerInput(GatewayArguments):
     """Input for gateway.disconnect_server."""
 
-    server_name: str = Field(min_length=1, description="Server to disconnect")
-    force: bool = False
+    server_name: str = Field(
+        min_length=1, description="Name of the server to disconnect"
+    )
+    force: bool = Field(
+        default=False,
+        description="Cancel this server's pending requests before disconnecting",
+    )
 
 
-class RestartServerInput(BaseModel):
+class RestartServerInput(GatewayArguments):
     """Input for gateway.restart_server."""
 
-    server_name: str = Field(min_length=1, description="Server to restart")
-    force: bool = False
+    server_name: str = Field(min_length=1, description="Name of the server to restart")
+    force: bool = Field(
+        default=False,
+        description="Cancel this server's pending requests before restarting",
+    )
 
 
 class LifecycleServerOutput(BaseModel):
@@ -913,10 +1039,13 @@ class HealthOutput(BaseModel):
 # === Pending Request Monitoring Types ===
 
 
-class ListPendingInput(BaseModel):
+class ListPendingInput(GatewayArguments):
     """Input for gateway.list_pending."""
 
-    server: str | None = None  # Filter by server (optional)
+    server: str | None = Field(
+        default=None,
+        description="Filter to pending requests on a specific server (optional)",
+    )
 
 
 class PendingRequestInfo(BaseModel):
@@ -941,11 +1070,17 @@ class ListPendingOutput(BaseModel):
     total_pending: int
 
 
-class CancelInput(BaseModel):
+class CancelInput(GatewayArguments):
     """Input for gateway.cancel."""
 
-    request_id: str = Field(min_length=1)  # Format: "server_name::local_id"
-    force: bool = False  # Force cancel even if heartbeat is recent
+    request_id: str = Field(
+        min_length=1,
+        description='Request ID in format "server_name::local_id" from gateway.list_pending',
+    )
+    force: bool = Field(
+        default=False,
+        description="Force cancel even if request is healthy (has recent heartbeat)",
+    )
 
 
 class CancelOutput(BaseModel):
@@ -1083,10 +1218,13 @@ class GatewayPolicy(BaseModel):
 # === Capability Request Types ===
 
 
-class CapabilityRequestInput(BaseModel):
+class CapabilityRequestInput(GatewayArguments):
     """Input for gateway.request_capability."""
 
-    query: str = Field(min_length=1, description="Natural language capability request")
+    query: str = Field(
+        min_length=1,
+        description="Natural language description of the capability needed (e.g., 'I need to scrape a website', 'browser automation')",
+    )
     available_clis: list[str] | None = Field(
         default=None,
         description="Optional: CLIs known to be available in the environment",
@@ -1207,13 +1345,16 @@ class SearchRegistryResult(BaseModel):
     diagnostics: list[str] = Field(default_factory=list)
 
 
-class SearchRegistryInput(BaseModel):
+class SearchRegistryInput(GatewayArguments):
     """Input for gateway.search_registry."""
 
     query: str = Field(
-        min_length=1, description="Natural language capability description"
+        min_length=1,
+        description="Natural language description of the capability needed",
+    )
+    limit: int = Field(
+        default=5, ge=1, le=20, description="Maximum number of results to return"
     )
-    limit: int = Field(default=5, ge=1, le=20)
 
 
 class SearchRegistryOutput(BaseModel):
@@ -1224,7 +1365,7 @@ class SearchRegistryOutput(BaseModel):
     next_step: str
 
 
-class RegisterDiscoveredServerInput(BaseModel):
+class RegisterDiscoveredServerInput(GatewayArguments):
     """Input for gateway.register_discovered_server."""
 
     package: str = Field(
@@ -1232,10 +1373,12 @@ class RegisterDiscoveredServerInput(BaseModel):
         description="npm package identifier (e.g. '@modelcontextprotocol/server-github')",
     )
     server_name: str = Field(
-        min_length=1, description="Logical name for this server (e.g. 'github')"
+        min_length=1,
+        description="Logical name for this server (e.g. 'github') used with gateway.provision",
     )
     env_vars: list[str] = Field(
-        default_factory=list, description="Required environment variable names"
+        default_factory=list,
+        description="Required environment variable names (e.g. ['GITHUB_TOKEN'])",
     )
     description: str = Field(
         default="", description="Short description of the server's purpose"
@@ -1266,11 +1409,11 @@ class RegisterDiscoveredServerOutput(BaseModel):
     next_step: str | None = None
 
 
-class ProvisionInput(BaseModel):
+class ProvisionInput(GatewayArguments):
     """Input for gateway.provision - install and start a specific server."""
 
     server_name: str = Field(
-        min_length=1, description="Name of the server to provision from manifest"
+        min_length=1, description="Name of the server to provision (from manifest)"
     )
 
 
@@ -1307,15 +1450,25 @@ FeedbackSubmissionOutcome = Literal[
 ]
 
 
-class SubmitFeedbackInput(BaseModel):
+class SubmitFeedbackInput(GatewayArguments):
     """Input for gateway.submit_feedback."""
 
-    title: str = Field(min_length=8, max_length=160)
-    description: str = Field(min_length=1)
-    issue_type: Literal["bug", "feature_request"] = Field(default="bug")
-    subordinate_server: str | None = None
-    failed_tool_call: str | None = None
-    confirm_submission: bool = False
+    title: str = Field(min_length=8, max_length=160, description="Issue title")
+    description: str = Field(
+        min_length=1, description="Issue details (technical data only)"
+    )
+    issue_type: Literal["bug", "feature_request"] = Field(
+        default="bug", description="Kind of issue to file"
+    )
+    subordinate_server: str | None = Field(
+        default=None, description="Subordinate MCP server involved (if known)"
+    )
+    failed_tool_call: str | None = Field(
+        default=None, description="Specific failed tool call (if known)"
+    )
+    confirm_submission: bool = Field(
+        default=False, description="Set true only after user confirms submission"
+    )
 
 
 class SubmitFeedbackOutput(BaseModel):
@@ -1339,10 +1492,10 @@ class SubmitFeedbackOutput(BaseModel):
     submission_outcome: FeedbackSubmissionOutcome | None = None
 
 
-class UpdateServerInput(BaseModel):
+class UpdateServerInput(GatewayArguments):
     """Input for gateway.update_server."""
 
-    server_name: str = Field(min_length=1, description="Server to update")
+    server_name: str = Field(min_length=1, description="Name of server to update")
     force: bool = Field(
         default=False,
         description=(
@@ -1373,26 +1526,38 @@ class UpdateServerOutput(BaseModel):
     message: str
 
 
-class AuthConnectInput(BaseModel):
+class AuthConnectInput(GatewayArguments):
     """Input for gateway.auth_connect - save auth credentials for a server."""
 
     server_name: str = Field(
-        min_length=1, description="Server requiring authentication"
+        min_length=1, description="Server name that needs authentication"
     )
     credential: str | None = Field(
-        default=None, min_length=1, description="Secret token/API key to store"
+        default=None,
+        min_length=1,
+        description="API key, token, or subscription credential to store",
     )
     env_var: str | None = Field(
         default=None,
-        description="Override environment variable key to store into",
+        description="Optional explicit environment variable key",
     )
     scope: Literal["user", "project"] = Field(
-        default="user", description="Where to store credentials"
+        default="user", description="Where to store the credential"
+    )
+    auth_mode: Literal["api_key", "url_elicitation"] = Field(
+        default="api_key",
+        description="API-key storage or URL-mode elicitation acknowledgement",
+    )
+    elicitation_id: str | None = Field(
+        default=None, description="URL-mode elicitation identifier"
+    )
+    elicitation_url: str | None = Field(
+        default=None, description="Sanitized URL-mode elicitation URL"
+    )
+    consent_acknowledged: bool = Field(
+        default=False,
+        description="Acknowledge that the out-of-band URL flow was completed",
     )
-    auth_mode: Literal["api_key", "url_elicitation"] = "api_key"
-    elicitation_id: str | None = None
-    elicitation_url: str | None = None
-    consent_acknowledged: bool = False
 
 
 class AuthConnectOutput(BaseModel):
@@ -1408,10 +1573,12 @@ class AuthConnectOutput(BaseModel):
     url_elicitation: UrlElicitationInfo | None = None
 
 
-class ProvisionStatusInput(BaseModel):
+class ProvisionStatusInput(GatewayArguments):
     """Input for gateway.provision_status - check job progress."""
 
-    job_id: str = Field(min_length=1, description="Job ID from provision response")
+    job_id: str = Field(
+        min_length=1, description="Job ID from gateway.provision response"
+    )
 
 
 class ProvisionJobStatus(BaseModel):
@@ -1439,11 +1606,15 @@ class ProvisionJobStatus(BaseModel):
     error: str | None = None
 
 
-class SyncEnvironmentInput(BaseModel):
+class SyncEnvironmentInput(GatewayArguments):
     """Input for gateway.sync_environment."""
 
-    platform: Literal["mac", "wsl", "linux", "windows"] | None = None
-    detected_clis: list[str] | None = None
+    platform: Literal["mac", "wsl", "linux", "windows"] | None = Field(
+        default=None, description="Override detected platform (optional)"
+    )
+    detected_clis: list[str] | None = Field(
+        default=None, description="Override detected CLIs (optional)"
+    )
 
 
 class SyncEnvironmentOutput(BaseModel):
```

### A — `src/pmcp/tools/handlers.py` (`git apply` patch against `origin/main`, +195 / −616)

```diff
diff --git a/src/pmcp/tools/handlers.py b/src/pmcp/tools/handlers.py
index 956c8c8..45a956c 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -14,11 +14,12 @@ from collections import deque
 from datetime import datetime, timezone
 from pathlib import Path
 from collections.abc import Callable, Mapping
-from typing import Any, Literal, cast
+from typing import Any, Literal, cast, NamedTuple
 
 import anyio
 from dotenv import load_dotenv
 from mcp.types import Tool
+from pydantic import BaseModel
 from pmcp import __version__ as PMCP_VERSION
 from pmcp.auth import (
     UNVERIFIED_URL_CAVEAT,
@@ -190,6 +191,7 @@ from pmcp.types import (
     UrlElicitationInfo,
 )
 
+from pmcp.tools.schema import input_schema_for
 from pmcp.manifest.loader import (
     Manifest,
     ServerConfig,
@@ -467,647 +469,224 @@ TRACE_VALUE_DENY_PATTERN = re.compile(
 )
 
 
-def get_gateway_tool_definitions() -> list[Tool]:
-    """Get MCP tool definitions for the gateway."""
-    return [
-        Tool(
-            name="gateway.catalog_search",
-            description=(
-                "Search for available tools across all connected MCP servers. "
-                "Returns compact capability cards without full schemas. "
-                "Use filters to narrow results by server, tags, or risk level. "
-                "Set include_offline=True to also discover provisionable servers not yet running. "
-                "This is the primary tool discovery entry point."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "query": {
-                        "type": "string",
-                        "description": "Search query to match against tool names, descriptions, and tags",
-                    },
-                    "filters": {
-                        "type": "object",
-                        "properties": {
-                            "server": {
-                                "type": "string",
-                                "description": "Filter to tools from a specific server",
-                            },
-                            "tags": {
-                                "type": "array",
-                                "items": {"type": "string"},
-                                "description": "Filter to tools with any of these tags",
-                            },
-                            "risk_max": {
-                                "type": "string",
-                                "enum": ["low", "medium", "high"],
-                                "description": "Maximum risk level to include",
-                            },
-                        },
-                    },
-                    "limit": {
-                        "type": "integer",
-                        "minimum": 1,
-                        "maximum": 100,
-                        "default": 20,
-                        "description": "Maximum number of results to return",
-                    },
-                    "include_offline": {
-                        "type": "boolean",
-                        "default": False,
-                        "description": "Include tools from offline servers",
-                    },
-                },
-            },
+class _GatewayToolSpec(NamedTuple):
+    """One advertised gateway tool: its name, argument model, and description."""
+
+    name: str
+    input_model: type[BaseModel] | None
+    description: str
+
+
+# The single source of each gateway tool's advertised inputSchema is the
+# pydantic model its handler validates with (Consiliency/pmcp#236). `None`
+# marks the tools dispatched with no arguments (see server.py call_tool).
+_GATEWAY_TOOL_SPECS: tuple[_GatewayToolSpec, ...] = (
+    _GatewayToolSpec(
+        name="gateway.catalog_search",
+        input_model=CatalogSearchInput,
+        description=(
+            "Search for available tools across all connected MCP servers. Returns compact capability cards without full schemas. Use filters to narrow results by server, tags, or risk level. Set include_offline=True to also discover provisionable servers not yet running. This is the primary tool discovery entry point."
         ),
-        Tool(
-            name="gateway.describe",
-            description=(
-                "Get detailed information about a specific tool, including its arguments and constraints. "
-                "Use this before invoking a tool to understand its requirements."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "tool_id": {
-                        "type": "string",
-                        "description": 'The tool ID in format "server_name::tool_name"',
-                    },
-                },
-                "required": ["tool_id"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.describe",
+        input_model=DescribeInput,
+        description=(
+            "Get detailed information about a specific tool, including its arguments and constraints. Use this before invoking a tool to understand its requirements."
         ),
-        Tool(
-            name="gateway.invoke",
-            description=(
-                "Invoke a tool on a downstream MCP server. "
-                "Arguments are validated against the tool schema before execution. "
-                "Output is automatically truncated if too large."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "tool_id": {
-                        "type": "string",
-                        "description": 'The tool ID in format "server_name::tool_name"',
-                    },
-                    "arguments": {
-                        "type": "object",
-                        "description": "Arguments to pass to the tool (must match tool schema)",
-                    },
-                    "run_correlation_id": {
-                        "type": "string",
-                        "description": "Scoped-advisor run correlation ID",
-                    },
-                    "seat_correlation_id": {
-                        "type": "string",
-                        "description": "Scoped-advisor seat correlation ID",
-                    },
-                    "evidence_label_digest": {
-                        "type": "string",
-                        "pattern": "^[0-9a-f]{64}$",
-                        "description": "SHA-256 digest of the caller evidence label",
-                    },
-                    "options": {
-                        "type": "object",
-                        "properties": {
-                            "timeout_ms": {
-                                "type": "integer",
-                                "minimum": 1000,
-                                "maximum": 300000,
-                                "default": 30000,
-                                "description": "Timeout in milliseconds",
-                            },
-                            "max_output_chars": {
-                                "type": "integer",
-                                "minimum": 100,
-                                "maximum": 100000,
-                                "description": "Maximum output characters (truncated if exceeded)",
-                            },
-                            "redact_secrets": {
-                                "type": "boolean",
-                                "default": False,
-                                "description": "Redact detected secrets from output",
-                            },
-                        },
-                    },
-                },
-                "required": ["tool_id"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.invoke",
+        input_model=InvokeInput,
+        description=(
+            "Invoke a tool on a downstream MCP server. Arguments are validated against the tool schema before execution. Output is automatically truncated if too large."
         ),
-        Tool(
-            name="gateway.refresh",
-            description=(
-                "Reload backend MCP server configurations and reconnect. "
-                "Use this when new MCP servers have been configured or to recover from connection errors. "
-                "Refuses by default while downstream requests are pending; set force=true to cancel them."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "source": {
-                        "type": "string",
-                        "enum": ["claude_config", "custom"],
-                        "description": "Config source to reload from",
-                    },
-                    "reason": {
-                        "type": "string",
-                        "description": "Reason for refresh (for logging)",
-                    },
-                    "force": {
-                        "type": "boolean",
-                        "default": False,
-                        "description": "Cancel pending downstream requests before refreshing",
-                    },
-                },
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.refresh",
+        input_model=RefreshInput,
+        description=(
+            "Reload backend MCP server configurations and reconnect. Use this when new MCP servers have been configured or to recover from connection errors. Refuses by default while downstream requests are pending; set force=true to cancel them."
         ),
-        Tool(
-            name="gateway.connect_server",
-            description=(
-                "Connect or start a known downstream MCP server by name. "
-                "Resolves configured, provisioned manifest, and registered discovered servers."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "server_name": {
-                        "type": "string",
-                        "description": "Name of the server to connect",
-                    },
-                },
-                "required": ["server_name"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.connect_server",
+        input_model=ConnectServerInput,
+        description=(
+            "Connect or start a known downstream MCP server by name. Resolves configured, provisioned manifest, and registered discovered servers."
         ),
-        Tool(
-            name="gateway.disconnect_server",
-            description=(
-                "Disconnect a running downstream MCP server without changing persistent config. "
-                "Refuses by default when that server has pending requests; set force=true to cancel them."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "server_name": {
-                        "type": "string",
-                        "description": "Name of the server to disconnect",
-                    },
-                    "force": {
-                        "type": "boolean",
-                        "default": False,
-                        "description": "Cancel this server's pending requests before disconnecting",
-                    },
-                },
-                "required": ["server_name"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.disconnect_server",
+        input_model=DisconnectServerInput,
+        description=(
+            "Disconnect a running downstream MCP server without changing persistent config. Refuses by default when that server has pending requests; set force=true to cancel them."
         ),
-        Tool(
-            name="gateway.restart_server",
-            description=(
-                "Restart a known downstream MCP server without changing persistent config. "
-                "Refuses by default when that server has pending requests; set force=true to cancel them."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "server_name": {
-                        "type": "string",
-                        "description": "Name of the server to restart",
-                    },
-                    "force": {
-                        "type": "boolean",
-                        "default": False,
-                        "description": "Cancel this server's pending requests before restarting",
-                    },
-                },
-                "required": ["server_name"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.restart_server",
+        input_model=RestartServerInput,
+        description=(
+            "Restart a known downstream MCP server without changing persistent config. Refuses by default when that server has pending requests; set force=true to cancel them."
         ),
-        Tool(
-            name="gateway.health",
-            description=(
-                "Get the health status of the gateway and all connected MCP servers. "
-                "Shows server status, tool counts, and last refresh time."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {},
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.health",
+        input_model=None,
+        description=(
+            "Get the health status of the gateway and all connected MCP servers. Shows server status, tool counts, and last refresh time."
         ),
-        Tool(
-            name="gateway.config_status",
-            description=(
-                "Show read-only effective configuration and startup policy status "
-                "with source attribution and non-secret diagnostics."
-            ),
-            input_schema={"type": "object", "properties": {}},
+    ),
+    _GatewayToolSpec(
+        name="gateway.config_status",
+        input_model=None,
+        description=(
+            "Show read-only effective configuration and startup policy status with source attribution and non-secret diagnostics."
         ),
-        Tool(
-            name="gateway.get_startup_policy",
-            description=(
-                "Return persisted autoStart and legacy disableAutoStart entries "
-                "grouped by config source."
-            ),
-            input_schema={"type": "object", "properties": {}},
+    ),
+    _GatewayToolSpec(
+        name="gateway.get_startup_policy",
+        input_model=None,
+        description=(
+            "Return persisted autoStart and legacy disableAutoStart entries grouped by config source."
         ),
-        Tool(
-            name="gateway.set_startup_policy",
-            description=(
-                "Preview or explicitly apply an autoStart add/remove/set operation "
-                "against one selected config source or path."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "operation": {
-                        "type": "string",
-                        "enum": ["add", "remove", "set"],
-                    },
-                    "names": {"type": "array", "items": {"type": "string"}},
-                    "source": {
-                        "type": "string",
-                        "enum": ["project", "user", "custom"],
-                    },
-                    "path": {"type": "string"},
-                    "dry_run": {"type": "boolean", "default": True},
-                    "apply": {"type": "boolean", "default": False},
-                },
-                "required": ["operation"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.set_startup_policy",
+        input_model=StartupPolicyOperation,
+        description=(
+            "Preview or explicitly apply an autoStart add/remove/set operation against one selected config source or path."
         ),
-        Tool(
-            name="gateway.request_capability",
-            description=(
-                "Recommend the right tool for a task — describe what you need in natural language. "
-                "Examples: 'scrape a website', 'search Slack messages', 'query Postgres', 'browse the web'. "
-                "Matches against installed CLIs and 90+ provisionable MCP servers and returns ranked candidates; "
-                "it does NOT start anything — call gateway.provision to actually install/start the recommended server. "
-                "Prefer this over gateway.provision when you don't already know the exact server name."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "query": {
-                        "type": "string",
-                        "description": "Natural language description of the capability needed (e.g., 'I need to scrape a website', 'browser automation')",
-                    },
-                    "available_clis": {
-                        "type": "array",
-                        "items": {"type": "string"},
-                        "description": "Optional: CLIs known to be available in the environment",
-                    },
-                },
-                "required": ["query"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.request_capability",
+        input_model=CapabilityRequestInput,
+        description=(
+            "Recommend the right tool for a task — describe what you need in natural language. Examples: 'scrape a website', 'search Slack messages', 'query Postgres', 'browse the web'. Matches against installed CLIs and 90+ provisionable MCP servers and returns ranked candidates; it does NOT start anything — call gateway.provision to actually install/start the recommended server. Prefer this over gateway.provision when you don't already know the exact server name."
         ),
-        Tool(
-            name="gateway.sync_environment",
-            description=(
-                "Sync environment information from the host. "
-                "Detects the platform (mac/wsl/linux/windows) and probes for installed CLIs. "
-                "This information is used to prefer CLIs over MCP servers when matching capabilities."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "platform": {
-                        "type": "string",
-                        "enum": ["mac", "wsl", "linux", "windows"],
-                        "description": "Override detected platform (optional)",
-                    },
-                    "detected_clis": {
-                        "type": "array",
-                        "items": {"type": "string"},
-                        "description": "Override detected CLIs (optional)",
-                    },
-                },
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.sync_environment",
+        input_model=SyncEnvironmentInput,
+        description=(
+            "Sync environment information from the host. Detects the platform (mac/wsl/linux/windows) and probes for installed CLIs. This information is used to prefer CLIs over MCP servers when matching capabilities."
         ),
-        Tool(
-            name="gateway.provision",
-            description=(
-                "Provision (install and start) a specific MCP server from the manifest. "
-                "Use this after reviewing candidates from gateway.request_capability. "
-                "Returns immediately with a job_id for tracking. "
-                "Poll gateway.provision_status to check progress. "
-                "Use gateway.request_capability instead if you don't know the exact server name."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "server_name": {
-                        "type": "string",
-                        "description": "Name of the server to provision (from manifest)",
-                    },
-                },
-                "required": ["server_name"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.provision",
+        input_model=ProvisionInput,
+        description=(
+            "Provision (install and start) a specific MCP server from the manifest. Use this after reviewing candidates from gateway.request_capability. Returns immediately with a job_id for tracking. Poll gateway.provision_status to check progress. Use gateway.request_capability instead if you don't know the exact server name."
         ),
-        Tool(
-            name="gateway.update_server",
-            description=(
-                "Update a subordinate MCP server package to latest version and restart it "
-                "so the new version is actually running. "
-                "Call this to check for and apply an update -- the gateway does not "
-                "volunteer update notices, so nothing will prompt you. "
-                "Refuses to restart by default when the server has pending requests; "
-                "set force=true to cancel them."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "server_name": {
-                        "type": "string",
-                        "description": "Name of server to update",
-                    },
-                    "force": {
-                        "type": "boolean",
-                        "default": False,
-                        "description": "Cancel this server's pending requests before restarting",
-                    },
-                },
-                "required": ["server_name"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.update_server",
+        input_model=UpdateServerInput,
+        description=(
+            "Update a subordinate MCP server package to latest version and restart it so the new version is actually running. Call this to check for and apply an update -- the gateway does not volunteer update notices, so nothing will prompt you. Refuses to restart by default when the server has pending requests; set force=true to cancel them."
         ),
-        Tool(
-            name="gateway.auth_connect",
-            description=(
-                "Store credentials for a server and make them available to provisioning. "
-                "Use this when gateway.provision reports missing authentication."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "server_name": {
-                        "type": "string",
-                        "description": "Server name that needs authentication",
-                    },
-                    "credential": {
-                        "type": "string",
-                        "description": "API key, token, or subscription credential to store",
-                    },
-                    "auth_mode": {
-                        "type": "string",
-                        "enum": ["api_key", "url_elicitation"],
-                        "default": "api_key",
-                        "description": "API-key storage or URL-mode elicitation acknowledgement",
-                    },
-                    "elicitation_id": {
-                        "type": "string",
-                        "description": "URL-mode elicitation identifier",
-                    },
-                    "elicitation_url": {
-                        "type": "string",
-                        "description": "Sanitized URL-mode elicitation URL",
-                    },
-                    "consent_acknowledged": {
-                        "type": "boolean",
-                        "default": False,
-                        "description": "Acknowledge that the out-of-band URL flow was completed",
-                    },
-                    "env_var": {
-                        "type": "string",
-                        "description": "Optional explicit environment variable key",
-                    },
-                    "scope": {
-                        "type": "string",
-                        "enum": ["user", "project"],
-                        "default": "user",
-                        "description": "Where to store the credential",
-                    },
-                },
-                "required": ["server_name"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.auth_connect",
+        input_model=AuthConnectInput,
+        description=(
+            "Store credentials for a server and make them available to provisioning. Use this when gateway.provision reports missing authentication."
         ),
-        Tool(
-            name="gateway.submit_feedback",
-            description=(
-                "Prepare a PMCP feedback issue for GitHub. Returns an exact "
-                "preview payload and a browser URL an operator can open. pmcp "
-                "posts nothing itself unless the operator has enabled submission "
-                "(`pmcp guidance --feedback-submission on`) and exported "
-                "PMCP_FEEDBACK_TOKEN; confirm_submission=true records the user's "
-                "consent and is not by itself authority to post."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "title": {
-                        "type": "string",
-                        "description": "Issue title",
-                    },
-                    "description": {
-                        "type": "string",
-                        "description": "Issue details (technical data only)",
-                    },
-                    "issue_type": {
-                        "type": "string",
-                        "enum": ["bug", "feature_request"],
-                        "default": "bug",
-                    },
-                    "subordinate_server": {
-                        "type": "string",
-                        "description": "Subordinate MCP server involved (if known)",
-                    },
-                    "failed_tool_call": {
-                        "type": "string",
-                        "description": "Specific failed tool call (if known)",
-                    },
-                    "confirm_submission": {
-                        "type": "boolean",
-                        "default": False,
-                        "description": "Set true only after user confirms submission",
-                    },
-                },
-                "required": ["title", "description"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.submit_feedback",
+        input_model=SubmitFeedbackInput,
+        description=(
+            "Prepare a PMCP feedback issue for GitHub. Returns an exact preview payload and a browser URL an operator can open. pmcp posts nothing itself unless the operator has enabled submission (`pmcp guidance --feedback-submission on`) and exported PMCP_FEEDBACK_TOKEN; confirm_submission=true records the user's consent and is not by itself authority to post."
         ),
-        Tool(
-            name="gateway.provision_status",
-            description=(
-                "Check the status of a running server installation. "
-                "Use after gateway.provision returns a job_id. "
-                "Returns progress percentage, output log, and final tools when complete."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "job_id": {
-                        "type": "string",
-                        "description": "Job ID from gateway.provision response",
-                    },
-                },
-                "required": ["job_id"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.provision_status",
+        input_model=ProvisionStatusInput,
+        description=(
+            "Check the status of a running server installation. Use after gateway.provision returns a job_id. Returns progress percentage, output log, and final tools when complete."
         ),
-        Tool(
-            name="gateway.list_pending",
-            description=(
-                "List all pending tool invocations with health status. "
-                "Shows elapsed time, heartbeat age, and current state for each request. "
-                "Use this to monitor long-running operations before deciding to cancel."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "server": {
-                        "type": "string",
-                        "description": "Filter to pending requests on a specific server (optional)",
-                    },
-                },
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.list_pending",
+        input_model=ListPendingInput,
+        description=(
+            "List all pending tool invocations with health status. Shows elapsed time, heartbeat age, and current state for each request. Use this to monitor long-running operations before deciding to cancel."
         ),
-        Tool(
-            name="gateway.cancel",
-            description=(
-                "Cancel a pending tool invocation. "
-                "By default, refuses to cancel healthy requests (recent heartbeat). "
-                "Use force=true to cancel anyway. "
-                "Use gateway.list_pending first to see request IDs and health status."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "request_id": {
-                        "type": "string",
-                        "description": 'Request ID in format "server_name::local_id" from gateway.list_pending',
-                    },
-                    "force": {
-                        "type": "boolean",
-                        "default": False,
-                        "description": "Force cancel even if request is healthy (has recent heartbeat)",
-                    },
-                },
-                "required": ["request_id"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.cancel",
+        input_model=CancelInput,
+        description=(
+            "Cancel a pending tool invocation. By default, refuses to cancel healthy requests (recent heartbeat). Use force=true to cancel anyway. Use gateway.list_pending first to see request IDs and health status."
         ),
-        Tool(
-            name="gateway.tasks_list",
-            description=(
-                "List brokered downstream MCP tasks. "
-                "MCP task IDs are opaque downstream task identifiers, not PMCP request IDs."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "server_name": {
-                        "type": "string",
-                        "description": "Optional server filter",
-                    },
-                    "cursor": {
-                        "type": "string",
-                        "description": "Optional downstream pagination cursor",
-                    },
-                },
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.tasks_list",
+        input_model=TasksListInput,
+        description=(
+            "List brokered downstream MCP tasks. MCP task IDs are opaque downstream task identifiers, not PMCP request IDs."
         ),
-        Tool(
-            name="gateway.tasks_get",
-            description="Get current status for one downstream MCP task.",
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "server_name": {"type": "string"},
-                    "task_id": {"type": "string"},
-                },
-                "required": ["server_name", "task_id"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.tasks_get",
+        input_model=TasksGetInput,
+        description=("Get current status for one downstream MCP task."),
+    ),
+    _GatewayToolSpec(
+        name="gateway.tasks_result",
+        input_model=TasksResultInput,
+        description=(
+            "Fetch a downstream MCP task result and apply the same output redaction and truncation options as gateway.invoke."
         ),
-        Tool(
-            name="gateway.tasks_result",
-            description=(
-                "Fetch a downstream MCP task result and apply the same output "
-                "redaction and truncation options as gateway.invoke."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "server_name": {"type": "string"},
-                    "task_id": {"type": "string"},
-                    "options": {
-                        "type": "object",
-                        "properties": {
-                            "max_output_chars": {"type": "integer"},
-                            "redact_secrets": {"type": "boolean"},
-                        },
-                    },
-                },
-                "required": ["server_name", "task_id"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.tasks_cancel",
+        input_model=TasksCancelInput,
+        description=(
+            "Cancel a downstream MCP task by opaque task ID. Use gateway.cancel only for PMCP request IDs from gateway.list_pending."
         ),
-        Tool(
-            name="gateway.tasks_cancel",
-            description=(
-                "Cancel a downstream MCP task by opaque task ID. "
-                "Use gateway.cancel only for PMCP request IDs from gateway.list_pending."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "server_name": {"type": "string"},
-                    "task_id": {"type": "string"},
-                    "force": {"type": "boolean", "default": False},
-                },
-                "required": ["server_name", "task_id"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.search_registry",
+        input_model=SearchRegistryInput,
+        description=(
+            "Search the public MCP Registry for external servers not in the local manifest. Use this when gateway.request_capability returns not_available. Returns package names and metadata; call gateway.register_discovered_server then gateway.provision to install."
         ),
-        Tool(
-            name="gateway.search_registry",
-            description=(
-                "Search the public MCP Registry for external servers not in the local manifest. "
-                "Use this when gateway.request_capability returns not_available. "
-                "Returns package names and metadata; call gateway.register_discovered_server then gateway.provision to install."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "query": {
-                        "type": "string",
-                        "description": "Natural language description of the capability needed",
-                    },
-                    "limit": {
-                        "type": "integer",
-                        "minimum": 1,
-                        "maximum": 20,
-                        "default": 5,
-                        "description": "Maximum number of results to return",
-                    },
-                },
-                "required": ["query"],
-            },
+    ),
+    _GatewayToolSpec(
+        name="gateway.register_discovered_server",
+        input_model=RegisterDiscoveredServerInput,
+        description=(
+            "Register an externally-discovered MCP server package so it can be provisioned. Call this after gateway.search_registry to register the chosen package, then call gateway.provision to install and start it."
         ),
+    ),
+)
+
+#: Advertised tool name -> the model its handler validates arguments with.
+GATEWAY_TOOL_INPUT_MODELS: dict[str, type[BaseModel] | None] = {
+    spec.name: spec.input_model for spec in _GATEWAY_TOOL_SPECS
+}
+
+
+@functools.cache
+def _derived_gateway_tools() -> tuple[Tool, ...]:
+    """Derive every Tool once per process: ``input_schema_for`` walks 23 model
+    schemas (~13 ms), and ``GatewayServer`` looks the list up on every
+    ``tools/call`` and ``tools/list``."""
+    return tuple(
         Tool(
-            name="gateway.register_discovered_server",
-            description=(
-                "Register an externally-discovered MCP server package so it can be provisioned. "
-                "Call this after gateway.search_registry to register the chosen package, "
-                "then call gateway.provision to install and start it."
-            ),
-            input_schema={
-                "type": "object",
-                "properties": {
-                    "package": {
-                        "type": "string",
-                        "description": "npm package identifier (e.g. '@modelcontextprotocol/server-github')",
-                    },
-                    "server_name": {
-                        "type": "string",
-                        "description": "Logical name for this server (e.g. 'github') used with gateway.provision",
-                    },
-                    "env_vars": {
-                        "type": "array",
-                        "items": {"type": "string"},
-                        "description": "Required environment variable names (e.g. ['GITHUB_TOKEN'])",
-                    },
-                    "description": {
-                        "type": "string",
-                        "description": "Short description of the server's purpose",
-                    },
-                },
-                "required": ["package", "server_name"],
-            },
-        ),
-    ]
+            name=spec.name,
+            description=spec.description,
+            input_schema=input_schema_for(spec.input_model),
+        )
+        for spec in _GATEWAY_TOOL_SPECS
+    )
+
+
+def get_gateway_tool_definitions() -> list[Tool]:
+    """Get MCP tool definitions for the gateway, schemas derived from the models."""
+    return list(_derived_gateway_tools())
 
 
 def _summarize_arg_schema(
```

### A — `src/pmcp/server.py` (`git apply` patch against `origin/main`, +9 / −0)

```diff
diff --git a/src/pmcp/server.py b/src/pmcp/server.py
index f702600..193ffe1 100644
--- a/src/pmcp/server.py
+++ b/src/pmcp/server.py
@@ -327,6 +327,15 @@ class GatewayServer:
                         TextContent(type="text", text=json.dumps(payload, indent=2))
                     ]
 
+                if tool is None:
+                    # Fail closed on any name the registry does not list: only
+                    # a registered name went through the schema gate above, so
+                    # nothing below -- the scoped-audit model check or a
+                    # dispatch branch -- may run for anything else. This
+                    # raises inside the audited path, like an unknown name
+                    # always has (Consiliency/pmcp#236, X1).
+                    raise ValueError(f"Unknown tool: {name}")
+
                 if self._scoped_advisor_audit is not None and name == "gateway.invoke":
                     scoped_input = InvokeInput.model_validate(arguments)
                     if scoped_input.run_correlation_id is None:
```

### B — `src/pmcp/tools/schema.py`: `NO_ARGUMENTS_SCHEMA` becomes

```python
NO_ARGUMENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
```

### B — `src/pmcp/types.py` / `src/pmcp/tools/handlers.py` hunks

```diff
 class GatewayArguments(BaseModel):
     """..."""
+
+    model_config = ConfigDict(extra="forbid")

 class InvokeInput(GatewayArguments):
     """Input for gateway.invoke."""

-    model_config = ConfigDict(populate_by_name=True)
-
     tool_id: str = Field(

         for candidate in (
             input_data.get("_meta"),
-            input_data.get("meta"),
             input_data.get("trace_context"),
-            input_data.get("traceContext"),
         ):
```

### B — appended to `tests/test_gateway_tool_schemas.py` (verbatim as measured on the revision-1 B tree: 223 passed; apply the B-preamble changes, then re-measure)

```python
# --- unknown keys are an error (Consiliency/pmcp#236, piece B) -----------------

FREE_FORM_PROPERTIES = {"arguments", "metadata", "requestor_context", "_meta"}


def _argument_models() -> list[type[BaseModel]]:
    """Every registered argument model plus every model nested inside one."""
    seen: dict[str, type[BaseModel]] = {}

    def walk(model: type[BaseModel]) -> None:
        if model.__name__ in seen:
            return
        seen[model.__name__] = model
        for field in model.model_fields.values():
            for candidate in _model_types(field.annotation):
                walk(candidate)

    for model in GATEWAY_TOOL_INPUT_MODELS.values():
        if model is not None:
            walk(model)
    return list(seen.values())


def _model_types(annotation: Any) -> list[type[BaseModel]]:
    import typing

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [t for arg in typing.get_args(annotation) for t in _model_types(arg)]


def test_every_argument_model_extends_gateway_arguments() -> None:
    from pmcp.types import GatewayArguments

    models = _argument_models()
    assert {m.__name__ for m in models} >= {
        "InvokeOptions",
        "CatalogFilters",
        "TaskMetadataInput",
        "TraceContextInfo",
    }
    for model in models:
        assert issubclass(model, GatewayArguments), model.__name__
        assert model.model_config.get("extra") == "forbid", model.__name__


@pytest.mark.parametrize("name", TOOLS_WITH_MODELS)
def test_unknown_top_level_key_is_rejected_by_the_model(name: str) -> None:
    from pydantic import ValidationError

    model = GATEWAY_TOOL_INPUT_MODELS[name]
    assert model is not None
    with pytest.raises(ValidationError, match="bogus_key"):
        model.model_validate({**MINIMAL_VALID_ARGUMENTS[name], "bogus_key": 1})


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("gateway.invoke", {"tool_id": "s::t", "options": {"timeoutMs": 5000}}),
        ("gateway.catalog_search", {"filters": {"servr": "x"}}),
        ("gateway.invoke", {"tool_id": "s::t", "task": {"ttl_seconds": 1}}),
        (
            "gateway.tasks_result",
            {"server_name": "s", "task_id": "t", "options": {"max": 1}},
        ),
    ],
)
def test_unknown_nested_key_is_rejected_by_the_model(
    name: str, arguments: dict[str, Any]
) -> None:
    from pydantic import ValidationError

    model = GATEWAY_TOOL_INPUT_MODELS[name]
    assert model is not None
    with pytest.raises(ValidationError):
        model.model_validate(arguments)


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_advertised_schema_forbids_unknown_keys(name: str) -> None:
    """Every object the schema describes says additionalProperties: false,
    except the deliberately free-form dict arguments."""
    schema = _tool(name).input_schema
    assert schema.get("additionalProperties") is False, name
    for obj in _object_schemas(schema):
        for prop_name, prop in obj["properties"].items():
            if prop.get("type") != "object":
                continue
            expected = prop_name in FREE_FORM_PROPERTIES
            assert prop.get("additionalProperties") is expected, (
                f"{name}.{prop_name}: additionalProperties should be {expected}"
            )


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("gateway.describe", {"tool_id": "s::t", "bogus_key": 1}),
        ("gateway.health", {"bogus_key": True}),
        ("gateway.invoke", {"tool_id": "s::t", "options": {"bogus_key": 1}}),
    ],
)
@pytest.mark.asyncio
async def test_server_gate_rejects_unknown_keys_before_dispatch(
    name: str, arguments: dict[str, Any]
) -> None:
    from unittest.mock import MagicMock

    from mcp.server.connection import Connection
    from mcp.server.context import ServerRequestContext
    from mcp.server.session import ServerSession
    from mcp.types import CallToolRequestParams, CallToolResult

    srv = GatewayServer()
    srv._create_server(instructions="test")
    assert srv._server is not None
    entry = srv._server.get_request_handler("tools/call")
    assert entry is not None
    connection = Connection.from_envelope("2025-11-25", None, None)
    ctx = ServerRequestContext(
        session=ServerSession(MagicMock(), connection),
        lifespan_context={},
        protocol_version="2025-11-25",
        method="test",
    )
    result = await entry.handler(
        ctx, CallToolRequestParams(name=name, arguments=arguments)
    )
    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert text.startswith("Input validation error:"), text
    assert "bogus_key" in text, text


@pytest.mark.asyncio
async def test_unknown_key_value_never_echoed(caplog: pytest.LogCaptureFixture) -> None:
    """The gate rejects the unknown key by NAME. pydantic's extra_forbidden error
    would echo the VALUE (input_value='Bearer sk-…'), and server.py returns and
    logs str(e) -- so the gate must catch it first."""
    import logging

    from mcp.types import CallToolResult

    secret = "Bearer sk-SECRETVALUE-do-not-echo"
    with caplog.at_level(logging.DEBUG):
        result = await _call_through_gate(
            "gateway.describe", {"tool_id": "s::t", "authorizatoin": secret}
        )
    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert text.startswith("Input validation error:"), text
    assert "'authorizatoin' was unexpected" in text, text
    assert "SECRETVALUE" not in text
    assert "SECRETVALUE" not in caplog.text


@pytest.mark.parametrize(
    "arguments",
    [
        {"tool_id": "s::t", "meta": {"traceparent": "00-a-b-01"}},
        {"tool_id": "s::t", "traceContext": {"traceparent": "00-a-b-01"}},
    ],
)
@pytest.mark.asyncio
async def test_meta_and_tracecontext_spellings_are_rejected(
    arguments: dict[str, Any],
) -> None:
    """Only the advertised spellings (`_meta`, `trace_context`) are accepted;
    the model agrees with the gate (no populate_by_name)."""
    from mcp.types import CallToolResult
    from pydantic import ValidationError

    from pmcp.types import InvokeInput

    result = await _call_through_gate("gateway.invoke", arguments)
    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    with pytest.raises(ValidationError):
        InvokeInput.model_validate(arguments)
```

### B — the four existing callers (measured diff, `git diff` on the B tree)

```diff
diff --git a/tests/test_scoped_advisor_audit.py b/tests/test_scoped_advisor_audit.py
index 6ae178c..b824f4d 100644
--- a/tests/test_scoped_advisor_audit.py
+++ b/tests/test_scoped_advisor_audit.py
@@ -288,10 +288,6 @@ async def test_scoped_server_filters_controls_and_writes_private_complete_audit(
         "gateway.provision",
         {
             "server_name": "not-allowlisted-server",
-            "tool_id": "raw credential value",
-            "run_correlation_id": "secret query with spaces",
-            "seat_correlation_id": "seat secret with spaces",
-            "evidence_label_digest": "not-a-digest-secret",
         },
     )
     assert "blocked by policy" in json.loads(denied.content[0].text)["message"]
diff --git a/tests/test_server.py b/tests/test_server.py
index 8919888..60a6432 100644
--- a/tests/test_server.py
+++ b/tests/test_server.py
@@ -286,7 +286,11 @@ class TestServerCreation:
                 _make_ctx(),
                 CallToolRequestParams(
                     name=tool_name,
-                    arguments={"server_name": "test", "task_id": "task-1"},
+                    arguments=(
+                        {"server_name": "test"}
+                        if tool_name == "gateway.tasks_list"
+                        else {"server_name": "test", "task_id": "task-1"}
+                    ),
                 ),
             )
             payload = json.loads(result.content[0].text)
@@ -369,7 +373,11 @@ class TestServerCreation:
                 _make_ctx(),
                 CallToolRequestParams(
                     name=tool_name,
-                    arguments={"operation": "add", "names": ["svc"]},
+                    arguments=(
+                        {"operation": "add", "names": ["svc"]}
+                        if tool_name == "gateway.set_startup_policy"
+                        else {}
+                    ),
                 ),
             )
             payload = json.loads(result.content[0].text)
diff --git a/tests/test_tools.py b/tests/test_tools.py
index 1e91803..90876ee 100644
--- a/tests/test_tools.py
+++ b/tests/test_tools.py
@@ -6210,7 +6210,7 @@ class TestInvokeErrorPaths:
             {
                 "tool_id": "tenant-code-mode::run_script",
                 "arguments": {},
-                "trace_context": {"authorization": "Bearer should-not-log"},
+                "_meta": {"authorization": "Bearer should-not-log"},
             }
         )
         health = await gt.health()
```

## Measurement scripts

The instrument behind the inventory, `extra=` table, gate probe, `model_json_schema()` noise and per-tool drift table. Run it from a checkout of `860636a` (or `main` @ `9ca081e`, where every file it imports is identical) with the piece-A `schema.py` saved beside it as `spike_schema.py`: `uv run python probe_head.py <dir-holding-spike_schema.py>`.

**Revision 2 re-run** (2026-09-26): `main` @ `9ca081e` `src/` on `PYTHONPATH`, the
frozen revision-2 `schema.py` as `spike_schema.py`. The inventory, `extra=`,
runtime-drop, gate-accepts and `model_json_schema()` sections print exactly
what the Research summary quotes (`tools: 26 modelled: 23`; all 27 models
`extra=None`; `DescribeInput -> {'tool_id': 'a::b'} (no error)`; no
`additionalProperties` anywhere; the three gate probes `ACCEPTS`). The
drift section, in full:

```text
== schema-vs-model drift on HEAD (hand-written vs derived, per tool) ==
  gateway.catalog_search:
    differs: /properties/query/type: hand='string' model=["string", "null"]
    differs: /properties/filters/type: hand='object' model=["object", "null"]
    differs: /properties/filters/properties/server/type: hand='string' model=["string", "null"]
    differs: /properties/filters/properties/tags/type: hand='array' model=["array", "null"]
    differs: /properties/filters/properties/risk_max/type: hand='string' model=["string", "null"]
    differs: /properties/filters/properties/risk_max/enum: hand=["low", "medium", "high"] model=["low", "medium", "high", null]
  gateway.describe:
    only in model:        /properties/tool_id/minLength = 1
  gateway.invoke:
    only in model:        /properties/tool_id/minLength = 1
    only in model:        /properties/arguments/additionalProperties = True
    only in model:        /properties/task/properties/enabled/default = True
    only in model:        /properties/task/properties/enabled/type = 'boolean'
    only in model:        /properties/task/properties/metadata/additionalProperties = True
    only in model:        /properties/task/properties/metadata/type = ["object", "null"]
    only in model:        /properties/task/properties/ttl/type = ["integer", "null"]
    only in model:        /properties/task/properties/poll_interval/type = ["number", "null"]
    only in model:        /properties/task/properties/requestor_context/additionalProperties = True
    only in model:        /properties/task/properties/requestor_context/type = ["object", "null"]
    only in model:        /properties/task/type = ["object", "null"]
    only in model:        /properties/trace_context/properties/traceparent/type = ["string", "null"]
    only in model:        /properties/trace_context/properties/tracestate/type = ["string", "null"]
    only in model:        /properties/trace_context/properties/baggage/type = ["string", "null"]
    only in model:        /properties/trace_context/type = ["object", "null"]
    only in model:        /properties/_meta/additionalProperties = True
    only in model:        /properties/_meta/type = ["object", "null"]
    only in model:        /properties/run_correlation_id/maxLength = 128
    only in model:        /properties/run_correlation_id/minLength = 1
    only in model:        /properties/seat_correlation_id/maxLength = 128
    only in model:        /properties/seat_correlation_id/minLength = 1
    differs: /properties/run_correlation_id/type: hand='string' model=["string", "null"]
    differs: /properties/seat_correlation_id/type: hand='string' model=["string", "null"]
    differs: /properties/evidence_label_digest/type: hand='string' model=["string", "null"]
    differs: /properties/options/type: hand='object' model=["object", "null"]
    differs: /properties/options/properties/max_output_chars/type: hand='integer' model=["integer", "null"]
  gateway.refresh:
    differs: /properties/source/type: hand='string' model=["string", "null"]
    differs: /properties/source/enum: hand=["claude_config", "custom"] model=["claude_config", "custom", null]
    differs: /properties/reason/type: hand='string' model=["string", "null"]
  gateway.connect_server:
    only in model:        /properties/server_name/minLength = 1
  gateway.disconnect_server:
    only in model:        /properties/server_name/minLength = 1
  gateway.restart_server:
    only in model:        /properties/server_name/minLength = 1
  gateway.set_startup_policy:
    differs: /properties/source/type: hand='string' model=["string", "null"]
    differs: /properties/source/enum: hand=["project", "user", "custom"] model=["project", "user", "custom", null]
    differs: /properties/path/type: hand='string' model=["string", "null"]
  gateway.request_capability:
    only in model:        /properties/query/minLength = 1
    differs: /properties/available_clis/type: hand='array' model=["array", "null"]
  gateway.sync_environment:
    differs: /properties/platform/type: hand='string' model=["string", "null"]
    differs: /properties/platform/enum: hand=["mac", "wsl", "linux", "windows"] model=["mac", "wsl", "linux", "windows", null]
    differs: /properties/detected_clis/type: hand='array' model=["array", "null"]
  gateway.provision:
    only in model:        /properties/server_name/minLength = 1
  gateway.update_server:
    only in model:        /properties/server_name/minLength = 1
  gateway.auth_connect:
    only in model:        /properties/server_name/minLength = 1
    only in model:        /properties/credential/minLength = 1
    differs: /properties/credential/type: hand='string' model=["string", "null"]
    differs: /properties/elicitation_id/type: hand='string' model=["string", "null"]
    differs: /properties/elicitation_url/type: hand='string' model=["string", "null"]
    differs: /properties/env_var/type: hand='string' model=["string", "null"]
  gateway.submit_feedback:
    only in model:        /properties/title/maxLength = 160
    only in model:        /properties/title/minLength = 8
    differs: /properties/subordinate_server/type: hand='string' model=["string", "null"]
    differs: /properties/failed_tool_call/type: hand='string' model=["string", "null"]
  gateway.provision_status:
    only in model:        /properties/job_id/minLength = 1
  gateway.list_pending:
    differs: /properties/server/type: hand='string' model=["string", "null"]
  gateway.cancel:
    only in model:        /properties/request_id/minLength = 1
  gateway.tasks_list:
    only in model:        /properties/requestor_context/additionalProperties = True
    only in model:        /properties/requestor_context/type = ["object", "null"]
    differs: /properties/server_name/type: hand='string' model=["string", "null"]
    differs: /properties/cursor/type: hand='string' model=["string", "null"]
  gateway.tasks_get:
    only in model:        /properties/server_name/minLength = 1
    only in model:        /properties/task_id/minLength = 1
    only in model:        /properties/requestor_context/additionalProperties = True
    only in model:        /properties/requestor_context/type = ["object", "null"]
  gateway.tasks_result:
    only in model:        /properties/server_name/minLength = 1
    only in model:        /properties/task_id/minLength = 1
    only in model:        /properties/options/properties/timeout_ms/default = 30000
    only in model:        /properties/options/properties/timeout_ms/maximum = 300000
    only in model:        /properties/options/properties/timeout_ms/minimum = 1000
    only in model:        /properties/options/properties/timeout_ms/type = 'integer'
    only in model:        /properties/options/properties/max_output_chars/maximum = 100000
    only in model:        /properties/options/properties/max_output_chars/minimum = 100
    only in model:        /properties/options/properties/redact_secrets/default = False
    only in model:        /properties/requestor_context/additionalProperties = True
    only in model:        /properties/requestor_context/type = ["object", "null"]
    differs: /properties/options/type: hand='object' model=["object", "null"]
    differs: /properties/options/properties/max_output_chars/type: hand='integer' model=["integer", "null"]
  gateway.tasks_cancel:
    only in model:        /properties/server_name/minLength = 1
    only in model:        /properties/task_id/minLength = 1
    only in model:        /properties/requestor_context/additionalProperties = True
    only in model:        /properties/requestor_context/type = ["object", "null"]
  gateway.search_registry:
    only in model:        /properties/query/minLength = 1
  gateway.register_discovered_server:
    only in model:        /properties/package/minLength = 1
    only in model:        /properties/server_name/minLength = 1

  tools with drift (ignoring descriptions and additionalProperties): 23/26
```

```python
"""Measure HEAD (860636a): model `extra` defaults, jsonschema gate, verbatim
model_json_schema() noise, and per-tool drift between the hand-written
inputSchema and what the spike's input_schema_for() derives from the model."""

import json
import sys

import jsonschema
from pydantic import BaseModel

sys.path.insert(0, sys.argv[1])  # dir holding spike_schema.py
from spike_schema import input_schema_for  # noqa: E402

from pmcp import types as T  # noqa: E402
from pmcp.tools.handlers import get_gateway_tool_definitions  # noqa: E402

MODELS = {
    "gateway.catalog_search": T.CatalogSearchInput,
    "gateway.describe": T.DescribeInput,
    "gateway.invoke": T.InvokeInput,
    "gateway.refresh": T.RefreshInput,
    "gateway.connect_server": T.ConnectServerInput,
    "gateway.disconnect_server": T.DisconnectServerInput,
    "gateway.restart_server": T.RestartServerInput,
    "gateway.health": None,
    "gateway.config_status": None,
    "gateway.get_startup_policy": None,
    "gateway.set_startup_policy": T.StartupPolicyOperation,
    "gateway.request_capability": T.CapabilityRequestInput,
    "gateway.sync_environment": T.SyncEnvironmentInput,
    "gateway.provision": T.ProvisionInput,
    "gateway.update_server": T.UpdateServerInput,
    "gateway.auth_connect": T.AuthConnectInput,
    "gateway.submit_feedback": T.SubmitFeedbackInput,
    "gateway.provision_status": T.ProvisionStatusInput,
    "gateway.list_pending": T.ListPendingInput,
    "gateway.cancel": T.CancelInput,
    "gateway.tasks_list": T.TasksListInput,
    "gateway.tasks_get": T.TasksGetInput,
    "gateway.tasks_result": T.TasksResultInput,
    "gateway.tasks_cancel": T.TasksCancelInput,
    "gateway.search_registry": T.SearchRegistryInput,
    "gateway.register_discovered_server": T.RegisterDiscoveredServerInput,
}

tools = {t.name: t for t in get_gateway_tool_definitions()}
assert set(tools) == set(MODELS), set(tools) ^ set(MODELS)
print("tools:", len(tools), "modelled:", sum(m is not None for m in MODELS.values()))

print("\n== extra= on every argument model (incl. nested) ==")
nested = [T.CatalogFilters, T.InvokeOptions, T.TaskMetadataInput, T.TraceContextInfo]
for m in [m for m in MODELS.values() if m is not None] + nested:
    print(f"  {m.__name__:32s} extra={m.model_config.get('extra')!r}")

print("\n== runtime: unknown key silently dropped? ==")
p = T.DescribeInput.model_validate({"tool_id": "a::b", "bogus_key": 1})
print("  DescribeInput ->", p.model_dump(), "(no error)")
p = T.InvokeInput.model_validate({"tool_id": "a::b", "options": {"timeoutMs": 5}})
print("  InvokeInput options.timeoutMs ->", p.options, "(no error; default timeout)")

print("\n== jsonschema gate: additionalProperties anywhere on HEAD? ==")
hits = [n for n, t in tools.items() if "additionalProperties" in json.dumps(t.input_schema)]
print("  tools whose schema mentions additionalProperties:", hits)
for name, args in [
    ("gateway.describe", {"tool_id": "a::b", "bogus_key": 1}),
    ("gateway.health", {"bogus_key": True}),
    ("gateway.invoke", {"tool_id": "a::b", "options": {"bogus_key": 1}}),
]:
    try:
        jsonschema.validate(instance=args, schema=tools[name].input_schema)
        print(f"  {name}: jsonschema ACCEPTS {args}")
    except jsonschema.ValidationError as e:
        print(f"  {name}: jsonschema REJECTS: {e.message}")

print("\n== verbatim model_json_schema() noise (InvokeInput) ==")
raw = T.InvokeInput.model_json_schema(by_alias=True, mode="validation")
print("  top-level keys:", sorted(raw))
print("  $defs:", sorted(raw.get("$defs", {})))
print("  options prop:", raw["properties"]["options"])
print("  tool_id prop:", raw["properties"]["tool_id"])
print("  run_correlation_id prop:", raw["properties"]["run_correlation_id"])

print("\n== schema-vs-model drift on HEAD (hand-written vs derived, per tool) ==")


def strip_desc(node):
    if isinstance(node, dict):
        return {k: strip_desc(v) for k, v in node.items() if k != "description"}
    if isinstance(node, list):
        return [strip_desc(x) for x in node]
    return node


def flat(node, path=""):
    out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(flat(v, f"{path}/{k}"))
    elif isinstance(node, list):
        out[path] = json.dumps(node, sort_keys=True)
    else:
        out[path] = repr(node)
    return out


drift_count = 0
for name, model in MODELS.items():
    hand = strip_desc(tools[name].input_schema)
    derived = strip_desc(input_schema_for(model))
    # additionalProperties is piece B, not drift; ignore for this comparison
    d2 = json.loads(json.dumps(derived).replace('"additionalProperties": false, ', "").replace(', "additionalProperties": false', ""))
    fh, fd = flat(hand), flat(d2)
    only_hand = {k: v for k, v in fh.items() if k not in fd}
    only_derived = {k: v for k, v in fd.items() if k not in fh}
    differ = {k: (fh[k], fd[k]) for k in fh if k in fd and fh[k] != fd[k]}
    if only_hand or only_derived or differ:
        drift_count += 1
        print(f"  {name}:")
        for k, v in only_hand.items():
            print(f"    only in hand-written: {k} = {v}")
        for k, v in only_derived.items():
            print(f"    only in model:        {k} = {v}")
        for k, (a, b) in differ.items():
            print(f"    differs: {k}: hand={a} model={b}")
print(f"\n  tools with drift (ignoring descriptions and additionalProperties): {drift_count}/26")
```

### Revision-2 probes (A1 null agreement, echo classes, descriptions)

Each is run twice, once with `main`'s `src/` on `PYTHONPATH` and once with
piece A's, both using the same venv:
`PYTHONPATH=<src> uv run python <probe> > out`.

`probe_null.py`: for every optional field (top level, and one level into a
nested argument model) sent as explicit `null`, does the gate accept it, and
does the model? `null_cmp.py main.json a.json` compares the two runs.

```python
"""For every optional field (top level, and one level down in a nested
argument model) sent as explicit null: does the gate accept it, does the model?
Run once with main's src on PYTHONPATH and once with piece A's."""
import json, sys, typing
import jsonschema
from pydantic import BaseModel, ValidationError
from pmcp import types as T
from pmcp.tools.handlers import get_gateway_tool_definitions

MODELS = {
    "gateway.catalog_search": T.CatalogSearchInput, "gateway.describe": T.DescribeInput,
    "gateway.invoke": T.InvokeInput, "gateway.refresh": T.RefreshInput,
    "gateway.connect_server": T.ConnectServerInput, "gateway.disconnect_server": T.DisconnectServerInput,
    "gateway.restart_server": T.RestartServerInput, "gateway.set_startup_policy": T.StartupPolicyOperation,
    "gateway.request_capability": T.CapabilityRequestInput, "gateway.sync_environment": T.SyncEnvironmentInput,
    "gateway.provision": T.ProvisionInput, "gateway.update_server": T.UpdateServerInput,
    "gateway.auth_connect": T.AuthConnectInput, "gateway.submit_feedback": T.SubmitFeedbackInput,
    "gateway.provision_status": T.ProvisionStatusInput, "gateway.list_pending": T.ListPendingInput,
    "gateway.cancel": T.CancelInput, "gateway.tasks_list": T.TasksListInput,
    "gateway.tasks_get": T.TasksGetInput, "gateway.tasks_result": T.TasksResultInput,
    "gateway.tasks_cancel": T.TasksCancelInput, "gateway.search_registry": T.SearchRegistryInput,
    "gateway.register_discovered_server": T.RegisterDiscoveredServerInput,
}
MIN = {"gateway.describe": {"tool_id": "srv::tool"}, "gateway.invoke": {"tool_id": "srv::tool"},
    "gateway.connect_server": {"server_name": "srv"}, "gateway.disconnect_server": {"server_name": "srv"},
    "gateway.restart_server": {"server_name": "srv"}, "gateway.set_startup_policy": {"operation": "add"},
    "gateway.request_capability": {"query": "scrape a site"}, "gateway.provision": {"server_name": "srv"},
    "gateway.update_server": {"server_name": "srv"}, "gateway.auth_connect": {"server_name": "srv"},
    "gateway.submit_feedback": {"title": "a title here", "description": "d"},
    "gateway.provision_status": {"job_id": "job"}, "gateway.cancel": {"request_id": "srv::1"},
    "gateway.tasks_get": {"server_name": "srv", "task_id": "t"},
    "gateway.tasks_result": {"server_name": "srv", "task_id": "t"},
    "gateway.tasks_cancel": {"server_name": "srv", "task_id": "t"},
    "gateway.search_registry": {"query": "github"},
    "gateway.register_discovered_server": {"package": "pkg", "server_name": "srv"}}

def mtypes(a):
    if isinstance(a, type) and issubclass(a, BaseModel):
        return [a]
    return [t for x in typing.get_args(a) for t in mtypes(x)]

def opt(m):
    return [(f.alias or n) for n, f in m.model_fields.items() if not f.is_required() and f.default is None]

tools = {t.name: t for t in get_gateway_tool_definitions()}
for _m in MODELS.values():
    _m.model_rebuild(force=True)  # resolve ForwardRef annotations (TasksResultInput.options)
out = {}
for name, m in MODELS.items():
    base = MIN.get(name, {})
    cases = [(f, {**base, f: None}) for f in opt(m)]
    for fn, fi in m.model_fields.items():
        for nested in mtypes(fi.annotation):
            for sub in opt(nested):
                cases.append((f"{fi.alias or fn}.{sub}", {**base, (fi.alias or fn): {sub: None}}))
    for label, args in cases:
        try:
            m.model_validate(args); ma = True
        except ValidationError:
            ma = False
        try:
            jsonschema.validate(instance=args, schema=tools[name].input_schema); ga = True
        except jsonschema.ValidationError:
            ga = False
        out[f"{name}.{label}"] = [ga, ma]
json.dump(out, sys.stdout, indent=0, sort_keys=True)
```

```python
"""Compare probe_null.py output from main and from piece A."""
import json, sys
m = json.load(open(sys.argv[1])); a = json.load(open(sys.argv[2]))
assert m.keys() == a.keys(), set(m) ^ set(a)
print("cases:", len(a), "| model accepts null:", sum(v[1] for v in a.values()))
print("A gate == model:", sum(v[0] == v[1] for v in a.values()), "| main gate == model:", sum(v[0] == v[1] for v in m.values()))
print("main gate rejects, model accepts:", sorted(k for k, v in m.items() if v[0] != v[1]))
```

Output (measured):

```text
cases: 42 | model accepts null: 42
A gate == model: 42 | main gate == model: 14
main gate rejects, model accepts: ['gateway.auth_connect.credential', 'gateway.auth_connect.elicitation_id', 'gateway.auth_connect.elicitation_url', 'gateway.auth_connect.env_var', 'gateway.catalog_search.filters', 'gateway.catalog_search.filters.risk_max', 'gateway.catalog_search.filters.server', 'gateway.catalog_search.filters.tags', 'gateway.catalog_search.query', 'gateway.invoke.evidence_label_digest', 'gateway.invoke.options', 'gateway.invoke.options.max_output_chars', 'gateway.invoke.run_correlation_id', 'gateway.invoke.seat_correlation_id', 'gateway.list_pending.server', 'gateway.refresh.reason', 'gateway.refresh.source', 'gateway.request_capability.available_clis', 'gateway.set_startup_policy.path', 'gateway.set_startup_policy.source', 'gateway.submit_feedback.failed_tool_call', 'gateway.submit_feedback.subordinate_server', 'gateway.sync_environment.detected_clis', 'gateway.sync_environment.platform', 'gateway.tasks_list.cursor', 'gateway.tasks_list.server_name', 'gateway.tasks_result.options', 'gateway.tasks_result.options.max_output_chars']
```

`probe_echo.py`: which gate and model error classes echo the argument *value*.
Its output on `main` and on A is quoted in *Order: A first, then B* (the only
difference is the `minLength` row: `main` ACCEPTS `""`, A rejects it with
`'' should be non-empty`).

```python
"""Which gate/model errors echo the argument VALUE? (Consiliency/pmcp#236 rev 2)"""
import json, jsonschema
from pydantic import BaseModel, ConfigDict, ValidationError
from pmcp.tools.handlers import get_gateway_tool_definitions
tools = {t.name: t for t in get_gateway_tool_definitions()}
S = "Bearer sk-SAMPLE"
def gate(name, args):
    try:
        jsonschema.validate(instance=args, schema=tools[name].input_schema); return "ACCEPTS"
    except jsonschema.ValidationError as e:
        return f"{e.message!r} echoes_value={S in e.message}"
print("type   :", gate("gateway.invoke", {"tool_id": "a::b", "arguments": S}))
print("minLen :", gate("gateway.describe", {"tool_id": ""}))
print("enum   :", gate("gateway.refresh", {"source": S}))
print("extra  :", gate("gateway.invoke", {"tool_id": "a::b", "authorizatoin": S}))
print("pattern keywords in any gateway schema:", "pattern" in json.dumps([t.input_schema for t in tools.values()]))
try:
    jsonschema.validate({"x": 1, "authorizatoin": S}, {"type": "object", "properties": {"x": {}}, "additionalProperties": False})
except jsonschema.ValidationError as e:
    print("additionalProperties:false :", repr(e.message), "echoes_value=", S in e.message)
try:
    jsonschema.validate({"x": S}, {"type": "object", "properties": {"x": {"type": "string", "pattern": "^[a-z]+$"}}})
except jsonschema.ValidationError as e:
    print("pattern (synthetic)        :", repr(e.message), "echoes_value=", S in e.message)
class F(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int = 1
try:
    F.model_validate({"authorizatoin": S})
except ValidationError as e:
    print("pydantic extra_forbidden   : echoes_value=", S in str(e), "|", str(e).splitlines()[2].strip())
print("pattern (invoke.evidence_label_digest):", gate("gateway.invoke", {"tool_id": "a::b", "evidence_label_digest": S}))
```

`dump_schemas.py` + `desc_cmp.py`: argument-description comparison, `main` →
A. Run `dump_schemas.py` under each `src/`, then
`desc_cmp.py main.json a.json`. Output at `d0722f4` (measured; the one `DIFF`
line is `update_server.force`):

```python
import json, sys
from pmcp.tools.handlers import get_gateway_tool_definitions
json.dump({t.name: {"d": t.description, "s": t.input_schema} for t in get_gateway_tool_definitions()}, sys.stdout, sort_keys=True)
```

```python
import json, sys
main = json.load(open(sys.argv[1])); a = json.load(open(sys.argv[2]))
def props(node, path=""):
    out = {}
    for name, p in (node.get("properties") or {}).items():
        out[f"{path}{name}"] = p
        if isinstance(p, dict) and "properties" in p:
            out.update(props(p, f"{path}{name}."))
    return out
same = differ = newdesc = newprop = 0
diffs = []; newdescs = []; newprops = []
for tool in main:
    assert main[tool]["d"] == a[tool]["d"], tool
    pm, pa = props(main[tool]["s"]), props(a[tool]["s"])
    assert set(pm) <= set(pa), (tool, set(pm) - set(pa))
    for k, p in pa.items():
        key = f"{tool}.{k}"
        if k not in pm:
            newprop += 1; newprops.append(key); continue
        dm = pm[k].get("description")
        if dm is None:
            newdesc += 1; newdescs.append(key)
        elif dm == p.get("description"):
            same += 1
        else:
            differ += 1; diffs.append((key, dm, p.get("description")))
print("tool descriptions byte-identical: 26/26" if len(main) == 26 else len(main))
print("same", same, "differ", differ, "new description", newdesc, "newly advertised", newprop)
for d in diffs: print("DIFF", d)
print("NEWDESC", newdescs)
print("NEWPROP", newprops)
```

```text
tool descriptions byte-identical: 26/26
same 54 differ 1 new description 19 newly advertised 16
```

(At `72eaa76`, before option (b): `same 40 differ 15`.)

### Board round 2 probes (F1 coercion, F3 model-validator echo, F5 blocked audit, X1 bypass)

Run like the others, `PYTHONPATH=<main or A src> uv run python <probe>`.
`probe_bypass.py` and the M-X1s check need a tree with the bypass mutant
applied (see *Acceptance criteria*).

```python
"""F1: lax-coercible values on invoke.task.* -- gate vs model (Consiliency/pmcp#236)."""
import jsonschema
from pydantic import ValidationError
from pmcp.tools.handlers import get_gateway_tool_definitions
from pmcp.types import InvokeInput
schema = {t.name: t for t in get_gateway_tool_definitions()}["gateway.invoke"].input_schema
vals = {"enabled": [1, 0, "true"], "ttl": ["5", True, False, "0005"], "poll_interval": [True, False, "5", "0005"]}
n_new = 0
for field, vs in vals.items():
    for v in vs:
        args = {"tool_id": "srv::tool", "task": {field: v}}
        try:
            InvokeInput.model_validate(args); model = True
        except ValidationError:
            model = False
        try:
            jsonschema.validate(args, schema); gate = "accepts"
        except jsonschema.ValidationError as e:
            gate = f"rejects: {e.message}"
        n_new += model and gate != "accepts"
        print(f"  task.{field}={v!r}: model={'accepts' if model else 'rejects'} gate {gate}")
print("model accepts, gate rejects:", n_new)
```

```python
"""F3: model-only validators echo input_value (Consiliency/pmcp#236)."""
from pydantic import ValidationError
from pmcp.types import InvokeInput
S = "Bearer sk-SAMPLE-value"
cases = {
    "charset validator": {"tool_id": "s::t", "run_correlation_id": S, "seat_correlation_id": "s1", "evidence_label_digest": "a" * 64},
    "all-or-none validator": {"tool_id": "s::t", "run_correlation_id": "r1", "arguments": {"api_key": S}},
    "meta non-dict (populate_by_name)": {"tool_id": "s::t", "meta": S},
}
for label, args in cases.items():
    try:
        InvokeInput.model_validate(args); print(f"  {label}: ACCEPTED")
    except ValidationError as e:
        print(f"  {label}: str(e) echoes value = {S in str(e)}")
```

Output of both, `main` then A (measured at `40b2ed5`):

```text
== main
  task.enabled=1: model=accepts gate accepts
  task.enabled=0: model=accepts gate accepts
  task.enabled='true': model=accepts gate accepts
  task.ttl='5': model=accepts gate accepts
  task.ttl=True: model=accepts gate accepts
  task.ttl=False: model=accepts gate accepts
  task.ttl='0005': model=accepts gate accepts
  task.poll_interval=True: model=accepts gate accepts
  task.poll_interval=False: model=accepts gate accepts
  task.poll_interval='5': model=accepts gate accepts
  task.poll_interval='0005': model=accepts gate accepts
model accepts, gate rejects: 0
  charset validator: str(e) echoes value = True
  all-or-none validator: str(e) echoes value = False
  meta non-dict (populate_by_name): str(e) echoes value = True
== A
  task.enabled=1: model=accepts gate rejects: 1 is not of type 'boolean'
  task.enabled=0: model=accepts gate rejects: 0 is not of type 'boolean'
  task.enabled='true': model=accepts gate rejects: 'true' is not of type 'boolean'
  task.ttl='5': model=accepts gate rejects: '5' is not of type 'integer', 'null'
  task.ttl=True: model=accepts gate rejects: True is not of type 'integer', 'null'
  task.ttl=False: model=accepts gate rejects: False is not of type 'integer', 'null'
  task.ttl='0005': model=accepts gate rejects: '0005' is not of type 'integer', 'null'
  task.poll_interval=True: model=accepts gate rejects: True is not of type 'number', 'null'
  task.poll_interval=False: model=accepts gate rejects: False is not of type 'number', 'null'
  task.poll_interval='5': model=accepts gate rejects: '5' is not of type 'number', 'null'
  task.poll_interval='0005': model=accepts gate rejects: '0005' is not of type 'number', 'null'
model accepts, gate rejects: 11
  charset validator: str(e) echoes value = True
  all-or-none validator: str(e) echoes value = False
  meta non-dict (populate_by_name): str(e) echoes value = True
```

```python
"""F5: a malformed call to a policy-blocked tool -- recorded in the audit? (Consiliency/pmcp#236)"""
import asyncio
from mcp.types import CallToolRequestParams
from pmcp.server import GatewayServer

async def main():
    srv = GatewayServer()
    recorded = []
    srv._policy_manager.is_gateway_tool_allowed = lambda name: False
    srv._record_scoped_invocation = lambda **kw: recorded.append(kw["terminal_status"])
    for args in ({"tool_id": ""}, {"tool_id": "srv::tool"}):
        recorded.clear()
        r = await srv._handle_call_tool(None, CallToolRequestParams(name="gateway.describe", arguments=args))
        print(f"  describe {args}: is_error={r.is_error} text={r.content[0].text[:60]!r} audit={recorded}")
asyncio.run(main())
```

(Output quoted under *Scoped-audit gap on gate rejections (A3)*.)

```python
"""Seat's X1 bypass: a dispatch branch for an unregistered name (Consiliency/pmcp#236)."""
import asyncio
from mcp.types import CallToolRequestParams
from pmcp.server import GatewayServer
async def main():
    srv = GatewayServer()
    called = []
    async def health(*a, **k):
        called.append(1); return {}
    srv._gateway_tools.health = health
    r = await srv._handle_call_tool(None, CallToolRequestParams(name="gateway.health2", arguments={"x": "Bearer sk-x"}))
    print(f"  gateway.health2: handler_ran={bool(called)} response={r.content[0].text!r}")
asyncio.run(main())
```

(Output quoted in the *Acceptance criteria* bypass row.)

### Board round 3 probe (N2 regex dialect)

`probe_pattern.py` lists every `pattern` in the advertised schemas and every
`pattern` constraint on a gateway argument model (nested ones included), then
checks `"a"*64 + "\n"` for `evidence_label_digest` against gate and model. It
needs piece A's registry. On `main`, only the last check applies; its output
is quoted under *A is not fully behaviour-neutral*.

```python
"""N2: every `pattern` in the advertised gateway schemas and every regex
constraint on a gateway argument model -- does the Python-`$` gate agree with
pydantic's Rust-`$` model on a trailing newline? (Consiliency/pmcp#236)"""
import json, typing
import jsonschema
from pydantic import BaseModel, ValidationError
from pmcp.tools.handlers import GATEWAY_TOOL_INPUT_MODELS, get_gateway_tool_definitions

tools = {t.name: t for t in get_gateway_tool_definitions()}
hits = []
def walk(node, path):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "pattern":
                hits.append((path, v))
            walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i in node:
            walk(i, path)
for name, t in tools.items():
    walk(t.input_schema, name)
print("pattern keywords in advertised schemas:", hits)

def mtypes(a):
    if isinstance(a, type) and issubclass(a, BaseModel):
        return [a]
    return [x for y in typing.get_args(a) for x in mtypes(y)]
seen, model_patterns = set(), []
def mwalk(m):
    if m in seen:
        return
    seen.add(m); m.model_rebuild(force=True)
    for fn, fi in m.model_fields.items():
        for meta in fi.metadata:
            if getattr(meta, "pattern", None):
                model_patterns.append(f"{m.__name__}.{fn}")
        for sub in mtypes(fi.annotation):
            mwalk(sub)
for m in GATEWAY_TOOL_INPUT_MODELS.values():
    if m is not None:
        mwalk(m)
print("pattern constraints on argument models (incl. nested):", model_patterns)

args = {"tool_id": "a::b", "evidence_label_digest": "a" * 64 + "\n"}
try:
    jsonschema.validate(args, tools["gateway.invoke"].input_schema); gate = "accepts"
except jsonschema.ValidationError as e:
    gate = f"rejects: {e.message[:60]}"
from pmcp.types import InvokeInput
try:
    InvokeInput.model_validate(args); model = "accepts"
except ValidationError as e:
    model = f"rejects ({e.errors()[0]['type']})"
print(f"64 hex + newline: gate {gate} | model {model}")
```

Output at `972bc90` (measured):

```text
pattern keywords in advertised schemas: [('gateway.invoke/properties/evidence_label_digest', '^[0-9a-f]{64}$')]
pattern constraints on argument models (incl. nested): ['InvokeInput.evidence_label_digest']
64 hex + newline: gate rejects: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | model rejects (string_too_long)
```

### Board round 4 probe (F1 integer range)

`probe_int_range.py` lists every integer property without a `maximum` in the
advertised schemas, then checks `task.ttl` values `1e20`, `2**63` and `3600`
against gate and model. It needs piece A's registry. Its output at
`a25dce0` and `c79bf1a` is quoted under *A is not fully behaviour-neutral*.

```python
"""Board round 4, F1: integer fields without a `maximum`, and `1e20` for
task.ttl through gate and model (Consiliency/pmcp#236)."""
import jsonschema
from pydantic import ValidationError
from pmcp.tools.handlers import get_gateway_tool_definitions
from pmcp.types import InvokeInput

tools = {t.name: t for t in get_gateway_tool_definitions()}
unbounded = []
def walk(node, path):
    if isinstance(node, dict):
        t = node.get("type")
        if (t == "integer" or (isinstance(t, list) and "integer" in t)) and "maximum" not in node:
            unbounded.append(path)
        for k, v in node.items():
            if k == "properties" and isinstance(v, dict):
                for pn, pv in v.items():
                    walk(pv, f"{path}.{pn}")
            elif isinstance(v, (dict, list)):
                walk(v, path)
    elif isinstance(node, list):
        for i in node:
            walk(i, path)
for name, t in tools.items():
    walk(t.input_schema, name)
print("integer properties without `maximum`:", unbounded)
for ttl in (1e20, 2**63, 3600):
    args = {"tool_id": "a::b", "task": {"ttl": ttl}}
    try:
        jsonschema.validate(args, tools["gateway.invoke"].input_schema); g = "accepts"
    except jsonschema.ValidationError:
        g = "rejects"
    try:
        InvokeInput.model_validate(args); m = "accepts"
    except ValidationError as e:
        m = f"rejects ({e.errors()[0]['type']})"
    print(f"task.ttl={ttl!r}: gate {g} | model {m}")
```
