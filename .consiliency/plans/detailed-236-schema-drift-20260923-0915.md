# Detailed plan: derive gateway `inputSchema`s from their argument models, then forbid unknown keys

> Resumes an unfinished spike from a planner whose session died. Everything
> below marked **measured** was run in this session against `860636a`
> (`origin/main` at planning time) or against the spike tree described in
> *Verdict on the inherited spike*. Nothing is carried over unverified.

## Task

Close Consiliency/pmcp#236 (C-10 / M-02): the 26 hand-written `Tool(input_schema={...})`
dicts in `src/pmcp/tools/handlers.py` drift from the pydantic models the handlers
actually validate with, and unknown argument keys are silently ignored. pmcp
brokers untrusted MCP servers for a prompt-injectable agent; the gateway's own
tools are that agent's API, and "accept a hallucinated key, proceed with
different behaviour" is the wrong default for that audience.

The fix lands as **two separately-mergeable pieces, A then B**:

- **A — kill the drift by construction.** Each advertised `inputSchema` is
  derived from the model its handler validates with; a test pins every link in
  that chain. Behaviour-preserving at the model layer; at the transport gate it
  starts rejecting what the model already rejected (see *A is not fully
  behaviour-neutral*).
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
`scratchpad/probe_head.py` against `860636a`:

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

### Drift on HEAD (measured: 18 of 26 tools; `scratchpad/probe_head.out`)

Comparing each hand-written schema against the schema derived from its model
(descriptions and `additionalProperties` ignored), **every difference is the
model being stricter or broader than what is advertised** — nowhere does the
hand-written schema state something the model lacks:

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

MCP clients (and `_summarize_arg_schema` in this repo, which reads
`prop["type"]` directly) expect self-contained, `$ref`-free object schemas with
a plain `"type"` per property. So post-processing is required, and **that
post-processing is itself drift surface**: it gets its own module
(`src/pmcp/tools/schema.py`, 4 documented transforms: inline `$defs`/`$ref`,
drop `title`, collapse `anyOf:[X, null] + default:null` → `X`, drop the
docstring description) and its own test against a synthetic model
(`test_input_schema_for_normalises_pydantic_output`) so a change to the
transforms cannot hide behind the real tools' snapshot. Mutation M4 below proves
that test fires.

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
Measured sizes: A-only snapshot 681 lines (0 × `additionalProperties: false`,
8 × `additionalProperties: true` for the free-form dicts); spike/B snapshot 712.

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
   text**, losing agent-facing content. Fixed in A's `types.py` change list.
3. **It ignored B's second-order consequences**: `_extract_trace_context`
   (`handlers.py:850-870`) runs *before* `InvokeInput.model_validate`
   (`:1449-1450`) and reads two undeclared spellings (`meta`, `traceContext`);
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
that had no description get one. This goes in A's CHANGELOG entry.

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
  `input_value='Bearer sk-SECRETVALUE'`; jsonschema's names only the key.
  `server.py:428-457` returns `str(e)[:400]` and logs it, so a forbid error
  reaching pydantic from a gate-bypassing caller echoes the value. B pins that
  the gate catches it first (`test_unknown_key_value_never_echoed`).

## Piece A — changes

### `src/pmcp/tools/schema.py` (add, 80 lines; verbatim under *Test bodies → A*)

- `NO_ARGUMENTS_SCHEMA = {"type": "object", "properties": {}}` — **no**
  `additionalProperties` (that is B). Byte-equal to what `gateway.health`
  advertises on HEAD.
- `input_schema_for(model | None) -> dict` — `None` → deep copy of
  `NO_ARGUMENTS_SCHEMA`; else `model.model_json_schema(by_alias=True, mode="validation")`
  → pop `$defs` → `_normalize` → pop top-level `description`.
- `_normalize(node, defs)` — inline `#/$defs/*` refs (sibling keys such as
  `description` merged over the target; any other `$ref` prefix raises
  `ValueError`), drop `title` keys (but never a *property named* `title`), recurse.
- `_collapse_nullable(node)` — `anyOf: [X, {"type": "null"}]` with
  `default: None` → `X` with the `default` removed. Optional fields advertise
  as their bare type, matching HEAD's hand-written shape.
- Reason: documented in the module docstring; each transform is one line of
  drift surface and is pinned by `test_input_schema_for_normalises_pydantic_output`.

### `src/pmcp/types.py` (modify)

- **Add `class GatewayArguments(BaseModel)`** directly above `TraceContextInfo`
  with **no `model_config`** in A. Docstring: marks agent-facing argument
  contracts; models parsing downstream data (`McpTaskInfo`, registry results,
  `.mcp.json`) must not use it. It exists so B is a one-line flip.
- **Reparent 27 models** to `GatewayArguments`: the 23 in the inventory table
  plus `CatalogFilters`, `InvokeOptions`, `TaskMetadataInput`, `TraceContextInfo`.
  `InvokeInput` keeps `ConfigDict(populate_by_name=True)` in A (B removes it).
- **Move every argument description into `Field(description=…)`** so the derived
  schema carries it. Rule for the text: **the HEAD hand-written schema text wins**
  — it is what agents have been reading and is uniformly the more informative —
  with one exception, `UpdateServerInput.force`, whose model text ("Restart the
  server even if it has pending requests or active MCP tasks… Mirrors
  gateway.restart_server's force flag.") is the accurate one (the hand-written
  copy predates task support). The 15 conflicts to resolve, HEAD text → keep:
  - `ConnectServerInput.server_name` → "Name of the server to connect"
  - `DisconnectServerInput.server_name` → "Name of the server to disconnect"
  - `RestartServerInput.server_name` → "Name of the server to restart"
  - `CapabilityRequestInput.query` → "Natural language description of the capability needed (e.g., 'I need to scrape a website', 'browser automation')"
  - `ProvisionInput.server_name` → "Name of the server to provision (from manifest)"
  - `UpdateServerInput.server_name` → "Name of server to update"
  - `UpdateServerInput.force` → **keep the model text** (exception above)
  - `AuthConnectInput.server_name` → "Server name that needs authentication"
  - `AuthConnectInput.credential` → "API key, token, or subscription credential to store"
  - `AuthConnectInput.env_var` → "Optional explicit environment variable key"
  - `AuthConnectInput.scope` → "Where to store the credential"
  - `ProvisionStatusInput.job_id` → "Job ID from gateway.provision response"
  - `SearchRegistryInput.query` → "Natural language description of the capability needed"
  - `RegisterDiscoveredServerInput.server_name` → "Logical name for this server (e.g. 'github') used with gateway.provision"
  - `RegisterDiscoveredServerInput.env_vars` → "Required environment variable names (e.g. ['GITHUB_TOKEN'])"

  40 descriptions are lifted verbatim (measured); 19 arguments that had no
  description on HEAD get the spike's text (list in `scratchpad/desc_diff.out`:
  `catalog_search.filters`, `invoke.options`, the six `set_startup_policy.*`,
  `submit_feedback.issue_type`, `tasks_get/result/cancel.server_name|task_id`,
  `tasks_result.options[.max_output_chars|.redact_secrets]`, `tasks_cancel.force`);
  16 newly advertised properties get the spike's text. The snapshot in the PR
  is the review surface for all of this.
- Reason: the model is the single source; descriptions that live only in the
  hand-written dict are exactly the drift being removed.

### `src/pmcp/tools/handlers.py` (modify)

- Import `NamedTuple` (typing) and `BaseModel` (pydantic); import
  `input_schema_for` from `pmcp.tools.schema`.
- **Add `class _GatewayToolSpec(NamedTuple)`**: `name: str`,
  `input_model: type[BaseModel] | None`, `description: str`.
- **Add `_GATEWAY_TOOL_SPECS: tuple[_GatewayToolSpec, ...]`** — 26 entries in the
  existing order, each carrying the tool's **unchanged** description string
  (measured byte-identical at runtime) and the model from the inventory table;
  `None` for `health`, `config_status`, `get_startup_policy`.
- **Add `GATEWAY_TOOL_INPUT_MODELS: dict[str, type[BaseModel] | None]`** derived
  from the specs — the public registry the tests read.
- **Replace the body of `get_gateway_tool_definitions()`** (currently
  `handlers.py:470-1110`, 26 inline `Tool(...)`) with
  `[Tool(name=spec.name, description=spec.description, input_schema=input_schema_for(spec.input_model)) for spec in _GATEWAY_TOOL_SPECS]`.
  Net −431 lines.
- Nothing else in the file changes. `server.py` is untouched in A: it already
  reads `get_gateway_tool_definitions()` for both `tools/list` and the gate.
- Reason: by construction there is no hand-written schema left to drift.

### `tests/test_gateway_tool_schemas.py` (add; verbatim under *Test bodies → A*)

The three-link chain plus shape, snapshot, normalisation, and gate tests:

- `test_registry_lists_every_advertised_tool_in_order`
- `test_advertised_schema_is_derived_from_registered_model[26]` — the
  "hand-corrupted schema" detector (M1).
- `test_handler_validates_arguments_with_the_registered_model[23]` — asserts
  `f"{Model.__name__}.model_validate("` appears in the handler's source. Known
  cost: source inspection is brittle to refactors that move validation into a
  helper; the structural alternative (server.py validating via the registry
  instead of each handler) is a named non-goal.
- `test_server_dispatch_agrees_with_registry[26]` — `None` ⇔ `method()` in
  `_handle_call_tool` source.
- `test_no_argument_tools_advertise_an_empty_object[3]`
- `test_advertised_schema_is_a_self_contained_mcp_input_schema[26]` — no
  `$ref`/`$defs`/`title` keywords, no surviving `anyOf`, every property described.
- `test_advertised_schema_is_valid_json_schema[26]` — `Draft202012Validator.check_schema`.
- `test_advertised_schema_accepts_the_minimal_valid_arguments[26]` — gate and
  model both accept the minimal argument set.
- `test_advertised_schemas_match_snapshot`
- `test_input_schema_for_normalises_pydantic_output`, `test_input_schema_for_none_is_the_empty_object`
- `test_server_gate_rejects_what_the_model_rejects[3]` — pins A's error-shape change.

### `tests/fixtures/gateway_tool_schemas.json` (add)

Generated once with `PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest tests/test_gateway_tool_schemas.py`
after the description decisions above are applied, then reviewed line by line in
the PR (A-only measured: 681 lines, no `additionalProperties: false`).

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
`timeout_ms`. Unknown keys are still ignored in this release — see the
following entry once B lands."

## Piece B — changes

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

- `_extract_trace_context` (`:850-870`): drop the `input_data.get("meta")` and
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

### `tests/test_gateway_tool_schemas.py` (modify; verbatim under *Test bodies → B*)

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
- **Full suite on the fused spike (= B before any test edit): 4 failed, 4251
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
  to the scoped-advisor audit. That is already true on HEAD for every gate
  rejection (missing required, wrong type); B widens the set. Recording gate
  rejections in the audit is a named non-goal / follow-up, not silently absorbed.
- **Full suite on the B tree after those four edits**: **4287 passed, 0
  failed**, 3 skipped, 25 deselected in 9:56 (`scratchpad/b_full.log`). The
  60 tests over A-only are B's new parametrized cases.
- **Full suite on the A-only tree**: **4227 passed, 0 failed**, 3 skipped, 25
  deselected in 12:25 (`scratchpad/aonly_full.log`). A breaks nothing in-repo.
- **Full suite on `860636a` (baseline, throwaway worktree
  `/mnt/HC_Volume_105438154/worktrees/pmcp-236-head`)**: 4063 passed, 1 failed —
  `tests/test_manifest.py::TestMonitorInstall::test_monitor_reads_stderr`, which
  passed 3/3 when re-run in isolation; a load-induced flake (host load ≈16
  during the run) in a file no piece touches. The 4227 − 4063 = 164 extra
  A-only tests are the new parametrized schema tests.
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

1. A: `schema.py` → `types.py` (base class, reparenting, descriptions with the
   15 decisions) → `handlers.py` registry → tests → generate snapshot → review
   the snapshot against `scratchpad/schemas_head.json` (the plan's measured HEAD
   schemas): the *only* differences must be the 18-tool drift list above plus
   the 19 new descriptions. → CHANGELOG A → PR A.
2. B (after A merges): `types.py` flip + `populate_by_name` removal →
   `schema.py` no-arg schema → `handlers.py` trace-context branches →
   `test_tools.py:6213` → B tests → regenerate snapshot → CHANGELOG B + callout
   → PR B with the snapshot diff called out in the description.

## Verification

```bash
cd <worktree>
uv run pytest tests/test_gateway_tool_schemas.py tests/test_baseline_constraints.py \
  -p no:cacheprovider --cov-fail-under=0 -q            # A: 163 passed (measured)
uv run pytest tests/test_tools.py -p no:cacheprovider --cov-fail-under=0 -q   # B: after the :6213 edit
nohup uv run pytest -p no:cacheprovider -q > /tmp/full.log 2>&1 & disown   # full suite, detached
uv run ruff check src/ tests/                          # measured clean on A-only and on the spike
uv run ruff format --check src/ tests/                 # measured clean on A-only and on the spike
uv run mypy src/
uv run python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md   # blocking inconsistencies: 0 (measured)
```

Edge cases exercised by the tests: a model field literally named `title`
(`_Outer.title`) must survive title-stripping; an aliased field (`_meta`) must
advertise under its alias; a `Literal | None` field must collapse to
`{"type": "string", "enum": [...]}`; the argless tools must be exactly the ones
`server.py` dispatches with `()`.

## Acceptance criteria — measured this session

### Piece A (tree: spike with `forbid` and `additionalProperties` removed; 163 passed green)

Each mutation was applied to the A-only tree, confirmed with `diff -u` against
the saved A-only copy (`scratchpad/mutate_A.out` has the full diffs), run
against `tests/test_gateway_tool_schemas.py`, and restored (`diff -q` clean,
five files). All RED for the named reason:

| # | Mutation (file:entity) | Confirmed diff | RED tests | Why it must fire |
|---|---|---|---|---|
| M1 | `handlers.py:get_gateway_tool_definitions` — inline dict for `gateway.describe` without `minLength` (**the hand-corrupted schema**) | `-input_schema=input_schema_for(spec.input_model)` / `+… if spec.name == "gateway.describe" else …` | `test_advertised_schema_is_derived_from_registered_model[gateway.describe]`, `test_advertised_schemas_match_snapshot`, `test_server_gate_rejects_what_the_model_rejects[gateway.describe…]` — 3 failed, 160 passed | someone reintroduces a hand-written schema that bypasses the builder |
| M2 | `handlers.py:_GATEWAY_TOOL_SPECS` — `gateway.describe` registered with `ConnectServerInput` | `-input_model=DescribeInput,` / `+input_model=ConnectServerInput,` | `test_handler_validates_arguments_with_the_registered_model[gateway.describe]`, `…accepts_the_minimal_valid_arguments[gateway.describe]`, snapshot, gate — 4 failed, 159 passed | the registry names a model the handler does not run |
| M3 | `types.py:DescribeInput.tool_id` — `min_length=1` → `2` | `-min_length=1,` / `+min_length=2,` | `test_advertised_schemas_match_snapshot`, `test_server_gate_rejects_what_the_model_rejects[gateway.describe…]` — 2 failed, 161 passed | an agent-facing contract change must be a reviewed snapshot diff |
| M4 | `schema.py:_collapse_nullable` — early `return node` (nullable `anyOf` no longer collapsed) | `+    return node` | `test_input_schema_for_normalises_pydantic_output`, snapshot, `…is_a_self_contained_mcp_input_schema[…]` × 10+ — the synthetic-model test fires independently of the real tools | the post-processing is drift surface of its own |

Note M1 and M3 both light the gate test: the gate test is what turns "the
schema says X" into "the transport enforces X".

### Piece B (tree: fused spike + B additions)

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

- [x] `src/` and `tests/` byte-identical to `860636a` before the plan commit —
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

## Execution Policy

- execute A: effort=medium, reason=mechanical registry + 27 model edits, but 15
  description decisions and a 681-line snapshot must be reviewed against the
  measured HEAD schemas, not eyeballed.
- execute B: effort=low, reason=one-line flip plus four consequential edits; the
  risk is entirely in the release note and the snapshot diff review.
- Every PR to main needs panel CR + reconcile first (repo rule).

## Test bodies

### A — `src/pmcp/tools/schema.py` (new module, verbatim; A-only: no `additionalProperties`)

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
advertised shape matches what the hand-written schemas advertised before —
self-contained, title-free, ``"type": "string"`` for an optional string —
and ``tests/test_gateway_tool_schemas.py`` pins that post-processing.
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
    """``anyOf: [X, null]`` + ``default: null`` -> ``X`` (an optional field)."""
    any_of = node.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2 or _NULL_SCHEMA not in any_of:
        return node
    (inner,) = [branch for branch in any_of if branch != _NULL_SCHEMA]
    rest = {key: value for key, value in node.items() if key != "anyOf"}
    if "default" in rest and rest["default"] is None:
        del rest["default"]
    return {**inner, **rest}
```

### A — `tests/test_gateway_tool_schemas.py` (verbatim, measured 163 passed on the A-only tree)

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
"""

from __future__ import annotations

import inspect
import json
import os
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


def _tool(name: str):
    return next(t for t in get_gateway_tool_definitions() if t.name == name)


def _handler_method(name: str) -> str:
    return name.removeprefix("gateway.")


def _object_schemas(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every object schema reachable from `schema`, itself included."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
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
    assert f"{model.__name__}.model_validate(" in source, (
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
            "title": {"type": "string", "description": "A field called title"},
            "count": {
                "type": "integer",
                "default": 3,
                "minimum": 1,
                "maximum": 9,
                "description": "Count",
            },
            "inner": {
                "type": "object",
                "description": "Inner",
                "properties": {
                    "level": {
                        "type": "string",
                        "enum": ["a", "b"],
                        "description": "Level",
                    }
                },
            },
            "_meta": {
                "type": "object",
                "additionalProperties": True,
                "description": "Meta",
            },
        },
    }


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

### B — appended to `tests/test_gateway_tool_schemas.py` (verbatim, measured 223 passed on the B tree)

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
