# Detailed plan: describe rejected gateway-tool arguments from their structure, never their value

> **Revision 1 (2026-09-28), on main `7680445`.** Consiliency/pmcp#297, the
> prerequisite for piece B (`extra="forbid"`) of Consiliency/pmcp#236.
> The change is **embedded, not described**: the ten blocks under *Verbatim
> bodies* are `git apply` patches against `origin/main` @ `7680445`,
> byte-identical to the verified code on the local-only branch
> `wip/297-code` @ `19dac95` (never pushed). The *Embedding proof* extracts
> them from this file with the extractor below, runs `git apply --check` on a
> fresh `7680445` worktree, applies them and `cmp`s every file against
> `19dac95`.
>
> Decisions, each argued in *Design*:
> - **One renderer, two libraries.** A new module `pmcp.argument_errors`
>   turns a jsonschema or pydantic `ValidationError` into
>   `$.<path>: <reason>`. The path is the failing location with every key
>   the caller chose shown as `*`; the reason is a fixed phrase filled only
>   from the gateway's own schema node (jsonschema) or from an allowlist of
>   constraint `ctx` keys (pydantic). It never reads jsonschema's `message`,
>   `instance`, `validator_value`, `context`, `cause` or `schema`, nor
>   pydantic's `msg`, `input` or any other `ctx` entry.
> - **pmcp's own validators stop being free text.** The three custom
>   validators on argument models raise `PydanticCustomError(<type>, <fixed
>   message>)` from a table in the renderer module, which the renderer reads
>   by type; a validator can no longer put its input into what is shown.
> - **Same rule for the log and the audit.** `call_tool`'s `except` arm logs
>   the description, not `str(e)`; a call rejected by the tool's own argument
>   model is recorded as an `audit.rejection` (as the gate's are since
>   Consiliency/pmcp#296), not as an `audit.invocation` that copied its
>   unvalidated correlation fields; an unregistered tool name is no longer
>   logged.
> - **The response stays useful and keeps its prefixes.** `Input validation
>   error: $.options: must be of type object or null` (gate) and `{"error":
>   true, "message": "Invalid arguments: $.run_correlation_id: correlation
>   IDs may contain only alphanumerics and ._:-"}` (model). A validation
>   error from anything else a handler parses reads `Validation error: ...`.

## Task

Address Consiliency/pmcp#297: "Validation errors echo argument values
(pydantic `input_value`, jsonschema `type`/`pattern`) into responses and
logs". pmcp brokers untrusted servers for a prompt-injectable agent, so a
token passed in the wrong field came straight back into the agent's context
and the gateway log. Fix the class: enumerate every place a validation or
parse exception's text, or a caller value, reaches a response, a log line or
an audit record; render errors from structure (`loc` + `type`; `json_path` +
validator keyword), never text; keep errors useful; and pin it with a
property test whose axes come from the code. Must land before piece B of
Consiliency/pmcp#236, whose `extra_forbidden` error carries `input_value`.

## Research summary (measured on main `7680445`)

### Where rejection text reaches a caller, a log or the audit

Every site below was read on `7680445` (line numbers are main's).

| # | Site | Channel | What leaks |
|---|---|---|---|
| L1 | `src/pmcp/server.py:342` gate `f"Input validation error: {e.message}"` | response | jsonschema's message quotes the instance for `type`, `enum`, `pattern`, `minLength`/`maxLength` (`'Bearer sk-...' is not of type 'object', 'null'`) |
| L2 | `src/pmcp/server.py:480` `logger.error(f"Tool execution error: {e}")` | log | `str(ValidationError)`: pydantic's `input_value=...` (the value, or a middle-truncated repr of the whole argument dict) |
| L3 | `src/pmcp/server.py:510` `"message": str(e)[:400]` | response | same text as L2 |
| L4 | `src/pmcp/server.py:383` scoped `InvokeInput.model_validate(arguments)` and `src/pmcp/tools/handlers.py:1458` `InvokeInput.model_validate` | L2+L3 | correlation-ID charset validator `types.py:887` (full value), all-or-none `model_validator` `types.py:900-902` (truncated repr of the whole dict, incl. `arguments`), non-dict `meta` reaching the model by name (`populate_by_name`) |
| L5 | `src/pmcp/tools/handlers.py:5396` `RegisterDiscoveredServerInput.model_validate` | L2+L3 | the package validator `types.py:1391-1399` (`input_value='-rf SECRET'`) |
| L6 | `src/pmcp/server.py:491` `except` arm's `record_invocation(arguments=audited_arguments)` | audit | a call the model rejected still had its correlation-shaped fields copied: measured, the all-or-none case wrote the caller's `run_correlation_id` into an `audit.invocation` |
| L7 | `src/pmcp/tools/handlers.py:5572-5651` `provision_status` validates inside a `try` whose arm logs `exc_info=True` (`:5643`) and renders `_sanitize_error(e)` (`:5650`, which does not strip `input_value`) | log (traceback), response | unreachable from `tools/call` (the gate rejects the same inputs first) but live for an in-process caller |
| L8 | `src/pmcp/server.py:380`/`:444` `raise ValueError(f"Unknown tool: {name}")` into L2 | log | the caller's tool name (Consiliency/pmcp#296's Non-goals handed it here) |
| L9 | any pydantic or jsonschema `ValidationError` a handler raises past validation (e.g. `McpTaskInfo.model_validate(task_data)` `handlers.py:6141` on downstream data) | L2+L3 | the parsed payload's values |

Not leaking (measured or read):
- `ScopedAdvisorAudit.record_rejected_arguments` (`scoped_advisor_audit.py:303-351`, Consiliency/pmcp#296) records path + keyword only.
- The SDK's own params validation: `mcp/shared/jsonrpc_dispatcher.py:100-101` maps a pydantic `ValidationError` to `INVALID_PARAMS` with `data=""` ("no pydantic text on the wire"); `mcp 2.0.0`.
- CLI paths: no subcommand in `src/pmcp/cli.py` / `src/pmcp/cli_commands/` routes through a gateway argument model (`grep model_validate|ValidationError|jsonschema` is empty there); argparse echoes the operator's own terminal input only.
- `GatewayException.__str__` is its message only (`errors.py:141-156`); caller values live in `details`.
- No `GatewayArguments` model sets `hide_input_in_errors`; the only other custom validators with a value in their message are `PackagesPolicy._reject_version_qualified_entries` (`types.py:1158-1166`, operator config, not reachable from `tools/call`) and `McpTaskInfo._normalize_task_timestamp` (`types.py:533`, downstream data).

### The probe (before)

`probe.py` (below) sent, for every registered tool and every declared
property (one level of nesting), a wrong-type / pattern / length / enum
value carrying `SENTINELzq9x`, plus the three `InvokeInput` validator cases,
to a real `GatewayServer`. On `7680445`: **97 leaking cases**, every one in
the response, the three validator cases in the log too; e.g.

```text
gateway.invoke  type      options               resp=True log=False :: "Input validation error: 'SENTINELzq9x' is not of type 'object', 'null'"
gateway.invoke  charset   run_correlation_id    resp=True log=True  :: '{"error": true, "message": "1 validation error for InvokeInput\nrun_correlation_id\n  Value error, correlation IDs may contain only alphanumerics and ...'
gateway.invoke  allornone run_correlation_id    resp=True log=True  :: '{"error": true, "message": "1 validation error for InvokeInput\n  Value error, scoped advisor correlation fields must be supplied together [type=value...'
gateway.auth_connect type credential            resp=True log=False :: "Input validation error: {'SENTINELzq9x': 'SENTINELzq9x'} is not of type 'string', 'null'"
```

On the patched tree the same probe prints zero leaking lines, and e.g.
`Input validation error: $.options: must be of type object or null`,
`Invalid arguments: $.run_correlation_id: correlation IDs may contain only alphanumerics and ._:-`,
`Invalid arguments: $: scoped advisor correlation fields must be supplied together`.

The full sweep (`count_leaks.py`, the new test's generator run once per
case, sentinel windows as the oracle), tests @ `19dac95`:

```text
== pmcp-297-main 7680445 (tests @19dac95)
plain {'gate': 248, 'model': 6} {('gate', 'response'): 225, ('model', 'log'): 6, ('model', 'response'): 6}
audited {'gate': 248, 'model': 6} {('gate', 'response'): 225, ('model', 'audit'): 1, ('model', 'log'): 6, ('model', 'response'): 6}
== pmcp-297-mut 19dac95 (tests @19dac95)
plain {'gate': 248, 'model': 6} {}
audited {'gate': 248, 'model': 6} {}
```

(The 23 gate cases that did not leak on main are the `required` cases:
jsonschema names only the missing, schema-declared key.)

### Library facts the design rests on

- **pydantic truncates `input_value` in the middle**:
  `input_value={'Sq3b2ee216cab4bcafa8599...e216cab4bcafa85997Zx'}` (pydantic
  2.12.5 / pydantic-core 2.41.5). A search for the whole secret misses the
  leak; the issue's "last 21 characters" is this. The sweep searches every
  12-character window of the sentinel instead.
- **pydantic message templates**, from `pydantic_core` `list_all_errors()`
  (103 types): the placeholders are constraints (`min_length`,
  `max_length`, `pattern`, `expected`, `gt`, `ge`, `lt`, `le`, ...) or
  input-derived (`actual_length`, `error`, `tag`, `attribute`,
  `class_name`, `encoding_error`, `tz_actual`, ...). `value_error` and
  `assertion_error` render `{error}`, i.e. whatever the validator's author
  interpolated. So "`msg` only where provably value-free" is decided per
  error type, from the template, not per message.
- **jsonschema 4.25.1**: `absolute_path` descends with the declared name
  under `properties`, an index under `items`, and the caller's key under
  `additionalProperties`/`patternProperties` (Consiliency/pmcp#296's
  survey). `absolute_schema_path` ends with the failing keyword, so the
  constraint can be read from the gateway's own schema rather than from the
  error.
- **The harness ledger** (`agent-harness` @ `18a324a4`,
  `phase_loop_runtime/advisor_board/research.py:445-503`): every
  `audit.invocation` of `gateway.invoke` whose three correlations do not
  match the seat's fails the whole ledger (`audit_correlation_mismatch`);
  `audit.rejection` is skipped. So nulling the correlations of a
  model-rejected invoke record (a first draft of this plan) would have
  turned one malformed call in an otherwise correct run into a failed
  ledger; recording it as `audit.rejection` does not.

### Echo of *valid* argument values (enumerated; see *Non-goals*)

The explorer pass (every `handlers.py` handler) plus a grep for
`message=f"`, `error=str(`, `raise ...(f"` found the echoes below. None is
a validation error's text: each is a schema-valid value the handler then
looks up, refuses, or reports on, and most are also echoed by design in a
structured output field (`CancelOutput.request_id`,
`AuthConnectOutput.env_var`, `InvokeOutput.tool_id`, ...).
- lookups / policy refusals naming the value: `server.py:359`
  (`Gateway tool blocked by policy: {name}`), `server.py:380/:444`
  (`Unknown tool: {name}` in the *response*), `handlers.py:1341/:1359`
  (details only), `:1510/:1543/:1569` (`make_error(tool_id=...)`),
  `:2426/:2541/:3010/:3083/:3192` (`Server '{server_name}' ...`),
  `:3888/:4044`, `:5585` (`Job '{job_id}' not found`), tasks_* blocked-by-policy;
- allowlist / format refusals of a schema-valid string:
  `handlers.py:4549/:4558` (`Env var '{env_var}' is not permitted`),
  `:4567-4581` (`str(exc)` of `env_store.validate_env_var_name`,
  `env_store.py:23` `{name!r}`), `:5410` (`unsafe package identifier
  {package!r}`, defense in depth, unreachable behind the model validator),
  `:5427-5433` (disallowed `env_vars` names via `operator_safe`),
  `client/manager.py:4208/:4217` (`Invalid request_id format: {request_id}`
  / `Invalid local_id`), `config/loader.py:778-780/:854` (policy path);
- free text echoed by design: `refresh` `reason` (`:1857` log),
  `request_capability` `query` (`:3860` log, `:3866/:3869` output),
  `search_registry` `query` (`:5384`), `submit_feedback` title/description
  (`:4681-4682`).

## Design

### 1. Where: a JSON path with caller-chosen keys as `*`

`schema_error_path` / `declared_property_names` move verbatim from
`scoped_advisor_audit.py` (Consiliency/pmcp#296) into the new module, which
the audit now imports, so one implementation decides both the audit's
`rejected_argument_path` and the text: a segment survives only as an index
into a list or as a key some `properties` map declares; anything else is
`None` in the record and `*` in the text (JSONPath's wildcard: "some key").
The pydantic location gets the same rule, with the declared set widened to
every field name and alias of every `GatewayArguments` model (author
strings, so `$.meta` for the `populate_by_name` spelling is still named).
An `int` segment survives only when the argument walk shows a list at that
point, so an in-process `int` dict key is redacted as in #296.

### 2. Why: a fixed phrase, filled from the gateway's own constraint

- **jsonschema**: the keyword (`error.validator`, only if the draft
  defines it) selects a phrase; the constraint is read from **our schema**
  by walking `error.absolute_schema_path`, never from
  `error.validator_value`/`schema`. `type` -> `must be of type object or
  null`; `enum` -> `must be one of [...]`; `pattern`, `minLength`,
  `maxLength`, `minItems`, `maxItems`, `minimum`, `maximum`, the exclusive
  bounds, `const`; `required` -> `$.<missing key>: is required`, where the
  key is the first name in the schema's `required` list that the object
  lacks (the object is only tested for the schema's names); and
  `additionalProperties` -> `has a property that is not accepted`, at the
  parent path, **without the key** (piece B makes this reachable; the key
  may itself be the secret). Any other keyword: `fails the schema's <kw>
  constraint`; unknown: `is invalid`.
- **pydantic**: `error.errors(include_input=False, include_url=False)`;
  the error `type` selects a phrase from `_MODEL_PHRASES` (types whose
  template takes no input: `missing`, `extra_forbidden`, `*_type`,
  `*_parsing`, lengths, bounds, `literal_error`, `enum`, pattern); a
  phrase may name only `ctx` keys in `_CONSTRAINT_CONTEXT = {min_length,
  max_length, pattern, expected, gt, ge, lt, le}`. Any other type,
  including `value_error`/`assertion_error`, reads `is invalid`.
- **pmcp's validators** (`InvokeInput._validate_correlation_id`,
  `InvokeInput._reject_partial_scoped_correlation`,
  `RegisterDiscoveredServerInput._validate_package`) now `raise
  argument_error(<TYPE>)`, a `PydanticCustomError` whose type keys the same
  table and whose message is the table's constant; the text is unchanged
  (`match="supplied together"` in `tests/test_scoped_advisor_audit.py:181`
  still passes). A future validator that raises a plain `ValueError`
  degrades to `is invalid`, never to its message (mutant M9).
- Up to five pydantic errors are described, `; `-joined, then `and N more`.
- Describing is wrapped: any exception while rendering yields `$: is
  invalid` (the exception itself is never rendered), mirroring #296's E16
  fail-closed stance.

### 3. The three channels

- **Response.** Gate: `Input validation error: ` + description (prefix kept:
  `tests/runtime/test_wire_*_era.py:79`, `tests/mcp2x/test_server_handlers.py:146`,
  `tests/test_gateway_tool_schemas.py:463` match on it). `except` arm:
  `{"error": true, "message": "Invalid arguments: <description>"}` when the
  error is the tool's own argument model (`e.title ==
  GATEWAY_TOOL_INPUT_MODELS[tool].__name__`), `Validation error:
  <description>` for any other pydantic/jsonschema error (L9), and
  `str(e)[:400]` unchanged for every non-validation exception.
- **Log.** `Tool execution error: invalid arguments for <registry name>:
  <description>` (or `validation error for ...`); an unregistered name logs
  `Tool execution error: unknown gateway tool` (the response still names it,
  pinned by `tests/test_gateway_tool_schemas.py:498/:530`,
  `tests/test_server.py:312`). Every other exception logs as before.
- **Audit.** A model rejection calls `record_rejected_arguments` (now
  accepting a pydantic error: path = first error's redacted location,
  `rejected_argument_validator: null`), writing `audit.rejection` /
  `invalid_arguments` like the gate. Every other failure keeps
  `record_invocation(arguments=audited_arguments)`. Behaviour change vs
  main: such a call used to be an `audit.invocation` `failure`; with the
  ledger reader above, a correctly correlated but malformed call (`meta:
  "x"`) no longer counts as a failed invocation, and a partially correlated
  one no longer fails the ledger with `audit_correlation_mismatch`.

### 4. `provision_status`

`ProvisionStatusInput.model_validate` moves above the `try` (L7), so a
rejection raises into `call_tool`'s arm like every other handler instead of
being logged with a traceback; the arm's fallback `job_id=` now uses the
validated id.

### 5. Why not a regex scrub of `str(e)`

Stripping `input_value=...` from rendered text is a denylist over a format
pydantic owns (and a validator's own message has no marker at all);
Consiliency/pmcp#296's lesson is that a denylist fails open. Rendering from
structure has no text to scrub.

### 6. The sweep and why its axes cover the class

`tests/test_argument_error_echo.py`:
- **tools**: every name in `GATEWAY_TOOL_INPUT_MODELS` (a test asserts every
  tool that declares a property contributes cases, and the three that
  declare none contribute none);
- **positions**: every declared property, nested object property and list
  item of each advertised schema (a typed position that yields no case
  fails the test: no silent shrink);
- **shapes** at each position: every one of wrong type (a string, an object
  whose keys and values carry the sentinel, a list carrying it), pattern
  (at the node's exact length, so the pattern and not the length fails),
  `maxLength`, `enum` that the node really rejects (checked with
  jsonschema);
- **required**: each root `required` key removed (a nested `required`
  fails the test until the axis is extended);
- **model-only**: every custom validator found by introspecting every model
  reachable from each tool's input model (a validator without a recipe, or
  a recipe without a validator, fails the test), and every field the model
  accepts by name under `populate_by_name`;
- **decoration** of every case: an extra top-level key, an extra key on
  every object along the path, and sentinel content in every open container
  the schema declares (`invoke.arguments`, `_meta`, `task.metadata`,
  `requestor_context`), so a rejection anywhere cannot echo a neighbour;
- **servers**: plain, and scoped-audit with every tool allowed (both with
  the real `GatewayTools`);
- **oracle**: per case, (a) it was rejected (an `isError` gate result, or the
  `{"error": true}` payload) - no vacuous pass; (b) no 12-character window
  of the sentinel, nor a sha256/sha1/md5 of it (raw, JSON-quoted,
  lower-cased), is in the response, the log (every record at DEBUG,
  rendered by pmcp's own text **and** JSON formatters, tracebacks included)
  or the audit JSONL; (c) with two sentinels of different length the
  response, the time-normalised log and the audit records are identical;
  (d) the rejection names the path (and, for validators, the reason).
  (b)-(c) run before (d), so main goes red on the leak, not on wording.
- **inside handlers**: a stub handler raising each validation-error type
  (downstream `McpTaskInfo`, an argument model, jsonschema) built from a
  sentinel value;
- **past the gate**: every gate case sent straight to the real handler must
  raise the model's `ValidationError` without logging the value (this is
  what catches L7).

`tests/test_scoped_advisor_audit.py` (#296's sweep) loses its two log
exclusions (`_FOREIGN_INVOKE_INPUT`, the `Unknown tool: <name>` line) and
their machinery, so its log oracle now excludes nothing, and it accepts the
one new record shape a generated key can cause (`_META_REJECTION`) instead
of the `_INVOKE_ONLY_EXEMPT` field exemption, which is gone.

## Changes

Patches under *Verbatim bodies* (`git diff 7680445 19dac95 -- <file>`).
Ten files: five source, three tests, two docs. Source is over the ~3-concern
line only in appearance: it is one concern (render rejections from
structure) applied to its three channels.

### `src/pmcp/argument_errors.py` (create, +388)
- module docstring — add — the rule, the fields it never reads.
- `CORRELATION_ID_CHARSET`, `SCOPED_CORRELATION_INCOMPLETE`, `PACKAGE_NAME_INVALID`, `_PMCP_MESSAGES`, `argument_error()` — add — fixed-message errors for pmcp's validators.
- `_CONSTRAINT_CONTEXT`, `_MODEL_PHRASES`, `_argument_names()`, `_model_error_path()`, `model_error_path()`, `_model_phrase()`, `describe_model_error()` — add — pydantic rendering.
- `declared_property_names()`, `schema_error_path()`, `schema_error_keyword()` — add (moved verbatim from `scoped_advisor_audit.py`, renamed public) — one path rule for text and audit.
- `_schema_node()`, `_count()`, `_type_names()`, `_missing_required()`, `_schema_phrase()`, `describe_schema_error()` — add — jsonschema rendering.
- `describe_argument_error()` — add — dispatch; `None` for any other exception.

### `src/pmcp/server.py` (modify, +68 / -10)
- imports — add `pydantic`, `describe_argument_error`, `describe_schema_error`, `GATEWAY_TOOL_INPUT_MODELS`.
- `_handle_call_tool` gate return — modify — description instead of `e.message` (L1).
- `call_tool` `except Exception` — modify — `rejected_by_model`, description in log and response (L2, L3, L9), `audit.rejection` for a model rejection (L6), no caller name in the unknown-tool log (L8).

### `src/pmcp/scoped_advisor_audit.py` (modify, +20 / -68)
- `_declared_property_names`, `_rejected_argument_path`, `_rejected_argument_validator` — delete (moved).
- `record_rejected_arguments` — modify — accepts a pydantic `ValidationError` too (path from `model_error_path`, validator `None`).

### `src/pmcp/types.py` (modify, +9 / -9)
- `InvokeInput._validate_correlation_id`, `InvokeInput._reject_partial_scoped_correlation`, `RegisterDiscoveredServerInput._validate_package` — modify — `raise argument_error(...)` (L4, L5).

### `src/pmcp/tools/handlers.py` (modify, +6 / -4)
- `GatewayTools.provision_status` — modify — validate before the `try`; fallback `job_id` from the validated input (L7).

### `tests/test_argument_error_echo.py` (create, +805) — see *Design §6*.

### `tests/test_scoped_advisor_audit.py` (modify, +91 / -176)
- `_FOREIGN_INVOKE_INPUT`, `_is_foreign`, `_EXCLUDED_MESSAGE`, `_has_diagnostics`, `_oracle_view`, `_LoggedCalls.foreign`, `_INVOKE_ONLY_EXEMPT`, `test_the_foreign_log_exclusion_matches_only_the_exact_lines`, `test_an_excluded_line_keeps_its_traceback_and_stack_in_the_oracle` (+ `_Records`, `_excluded_record`, `_invoke_input_line`) — delete — nothing is excluded any more.
- `_META_REJECTION`, `_call_records`, `_assert_against`, `test_the_formerly_excluded_log_echoes_are_gone` — add.
- the three generated sweeps — modify — read invocation *and* rejection records, compare with `_assert_against`.

### `tests/test_gateway_tool_schemas.py` (modify, +7 / -3)
- `test_server_gate_rejects_what_the_model_rejects` fragments — modify — the new wording (`$.tool_id: must be at least 1 character`, `$.title: must be at least 8 characters`, `$.options.max_output_chars: must be greater than or equal to`).

## Documentation impact
- `CHANGELOG.md` — `[Unreleased]` -> `### Fixed`, first entry — add — the fix, the wording change for clients that match on jsonschema phrases, the audit disposition, the unknown-tool log line.
- `README.md` (scoped-advisor audit paragraph, main `:1519-1524`) — modify — model rejections are `audit.rejection` too; the caller-facing text and log name the path and a schema reason, never the value.

## Dependencies & order
Apply all ten patches together (they are one `git apply`). `types.py`
imports `pmcp.argument_errors`, which imports `pmcp.types` only lazily
inside `_argument_names()` (no cycle); `scoped_advisor_audit.py` imports it
at module level. No migration, no config.

## Verification

```bash
# from a fresh worktree of origin/main @ 7680445 (dev0: team host)
uv sync --all-extras -p 3.10
# apply (see *Verbatim bodies -> How to apply*)
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest tests/test_argument_error_echo.py tests/test_scoped_advisor_audit.py tests/test_gateway_tool_schemas.py -q
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir
uv run pytest -m 'not live and not slow' -q
```

Red on main: copy the three test files at `19dac95` onto a clean
`7680445` tree and run the same three modules (result under *Acceptance
criteria*).

## Acceptance criteria — measured this session

- [x] For every registered gateway tool and every generated invalid
  argument (254 cases), no 12-character window or hash of the sentinel
  reaches the response, the log (text + JSON formatters, tracebacks) or the
  scoped audit, on the plain and the scoped-audit server, and two sentinels
  of different length give identical responses, logs and audit records —
  `pytest tests/test_argument_error_echo.py::test_no_rejected_argument_value_reaches_a_response_log_or_audit` (2 passed patched; both red on main: `AssertionError: ('$.auth_mode:type-object', 'response')`).
- [x] Each of those rejections names its path and reason: `Input validation error: $.options: must be of type object or null` for the issue's example — `test_a_schema_rejection_names_the_field_and_the_reason_only` (5 passed; on main these five fail with `ModuleNotFoundError: No module named 'pmcp.argument_errors'` since they call the renderer directly; the sweep's usefulness assertion `_useful` covers the same wording end to end).
- [x] A model-rejected call is an `audit.rejection` with no argument value; nothing in `test_scoped_advisor_audit.py`'s log oracle is excluded — `pytest tests/test_scoped_advisor_audit.py` (156 passed patched; on main with the patched tests, 6 fail, e.g. `('top-level correlation', ['... Tool execution error: 1 validation error for InvokeInput ...`).
- [x] Every handler, called past the gate, raises its model's `ValidationError` without logging the value — `test_every_handler_rejects_what_the_gate_rejects_without_logging_it` (passed; red on main at `provision_status`: `('$.job_id:type-object', '... provision_status handler failed: 1 validation error for Provis...fa85997Zx'}]}, input_type=dict]`).
- [x] `ruff check src/ tests/`, `ruff format --check src/ tests/`, `mypy src/` clean; full `pytest -m 'not live and not slow'` green (lines below).

Red on main, exact tail (`pytest tests/test_argument_error_echo.py tests/test_scoped_advisor_audit.py tests/test_gateway_tool_schemas.py -q --tb=line`, patched tests on `7680445`):

```text
tests/test_argument_error_echo.py:506: AssertionError: ('$.auth_mode:type-object', 'response')
tests/test_argument_error_echo.py:506: AssertionError: ('$.auth_mode:type-object', 'response')
tests/test_argument_error_echo.py:557: AssertionError: ('$.job_id:type-object', '[2026-09-28T21:26:22] [ERROR] provision_status handler failed: 1 validation error for Provis...fa85997Zx\'}]}, input_type=dict]\
tests/test_argument_error_echo.py:624: AssertionError: ab4bcafa8599
tests/test_argument_error_echo.py:624: AssertionError: ab4bcafa8599
tests/test_argument_error_echo.py:624: AssertionError: ab4bcafa8599
tests/test_argument_error_echo.py:683: ModuleNotFoundError: No module named 'pmcp.argument_errors'   (x5, and :697, :727, :760, :777: the renderer's own unit tests)
tests/test_scoped_advisor_audit.py:1692: AssertionError: ('top-level correlation', ['[1969-12-31T19:00:00] [ERROR] Tool execution error: 1 validation error for InvokeInput
tests/test_scoped_advisor_audit.py:1692: AssertionError: ('E6 correlation', ['[1969-12-31T19:00:00] [ERROR] Tool execution error: 1 validation error for InvokeInput
tests/test_scoped_advisor_audit.py:1692: AssertionError: ("('gateway.caller_marker_a', 'gateway.caller_marker_bbbbbbb') correlation", [... "msg": "Too
tests/test_scoped_advisor_audit.py:1692: AssertionError: ("('gateway.caller_marker_a', 'gateway.caller_marker_bbbbbbb') correlation", [... "msg": "Too
tests/test_scoped_advisor_audit.py:1692: AssertionError: ('real correlation', ['[1969-12-31T19:00:00] [ERROR] Tool execution error: 1 validation error for InvokeInput
tests/test_scoped_advisor_audit.py:2086: AssertionError: [2026-09-28T21:26:45] [INFO] Loaded policy from ...
tests/test_gateway_tool_schemas.py:468: AssertionError: Input validation error: '' should be non-empty
tests/test_gateway_tool_schemas.py:468: AssertionError: Input validation error: 'short' is too short
tests/test_gateway_tool_schemas.py:468: AssertionError: Input validation error: 5 is less than the minimum of 100
24 failed, 368 passed in 25.98s
```

The three `:624` lines are `test_a_validation_error_raised_by_a_handler_is_described_not_echoed` (downstream model, argument model, jsonschema): a sentinel window in main's response/log.

Green (patched): `392 passed in 36.73s` for the three modules; gates and the
full suite in *Embedding proof*.

## Mutation evidence

`mutants.py` (below) on a worktree of `19dac95`: each mutant is applied
(the anchor must occur exactly once), the three test modules run with `-x
--tb=line`, and the file is restored **from a copy saved before the
mutation** and `cmp`-checked; `git status --short | wc -l` was `0`
afterwards. Every mutant applied and went red; the named reason is the first
failing assertion.

```text
M1 gate renders e.message: applied=yes exit=1 | 1 failed, 1 passed in 0.13s | E   AssertionError: ('$.auth_mode:type-object', 'response')
M2 except arm returns str(e): applied=yes exit=1 | 1 failed, 1 passed in 1.98s | E   AssertionError: ("validator:('InvokeInput', '_validate_correlation_id', 'run_correlation_id')", 'response')
M3 except arm logs str(e): applied=yes exit=1 | 1 failed, 1 passed in 2.12s | E   AssertionError: ("validator:('InvokeInput', '_validate_correlation_id', 'run_correlation_id')", 'log', '[2026-09-28T21:24:17] [ERROR] ...y alphanumerics and ._:- [type=correlation_id_charset, input_value=\'Sq3b2ee216cab4bcafa8
M4 model rejection recorded as invocation: applied=yes exit=1 | 1 failed, 2 passed in 5.03s | E   AssertionError: ("validator:('InvokeInput', '_reject_partial_scoped_correlation', '')", 'audit')
M5 model loc not redacted: applied=yes exit=1 | 1 failed, 14 passed in 13.49s | E   AssertionError: assert '$.env.sk-KEY...be an integer' == '$.env.*: must be an integer'
M6 schema path not redacted: applied=yes exit=1 | 1 failed, 12 passed in 12.30s | E   AssertionError: assert '$.env.sk-KEY... type integer' == '$.env.*: mus... type integer'
M7 phrase may read ctx error: applied=yes exit=1 | 1 failed, 15 passed in 7.35s | E   AssertionError: assert frozenset({'e...', 'lt', ...}) == {'expected', ..._length', ...}
M8 missing-required reads an instance key: applied=yes exit=1 | 1 failed, 1 passed in 0.26s | E   AssertionError: ('required:server_name', 'response')
M9 validator back to ValueError with value: applied=yes exit=1 | 1 failed, 1 passed in 1.92s | E   AssertionError: Invalid arguments: $.run_correlation_id: is invalid
M10 provision_status validates inside its try: applied=yes exit=1 | 1 failed, 3 passed in 7.08s | E   AssertionError: ('$.job_id:type-object', '[2026-09-28T21:25:11] [ERROR] provision_status handler failed: 1 validation error for Provis...fa85997Zx\'}]}, input_type=dict]\
M11 unknown tool name logged: applied=yes exit=1 | 1 failed, 164 passed in 31.05s | E   AssertionError: ("('gateway.caller_marker_a', 'gateway.caller_marker_bbbbbbb') correlation", ['[1969-12-31T19:00:00] [ERROR] Tool exec...evel": "ERROR", "logger": "pmcp.server", "msg": "Tool execution error: Unknown tool: gate
M12 type phrase is e.message: applied=yes exit=1 | 1 failed, 1 passed in 0.12s | E   AssertionError: ('$.consent_acknowledged:type-string', 'response')
M13 model phrase is pydantic msg: applied=yes exit=1 | 1 failed, 13 passed in 7.48s | E   AssertionError: assert '$.meta: Inpu...id dictionary' == '$.meta: must be an object'
M14 audit model path from input: applied=yes exit=1 | 1 failed, 2 passed in 4.80s | E   AssertionError: ("validator:('InvokeInput', '_validate_correlation_id', 'run_correlation_id')", 'audit')
```

Reasons: M1/M2/M3/M8/M12 — the sweep finds a sentinel window in the
response or log; M4/M14 — in the audit; M10 — in `provision_status`'s
logged traceback; M11 — #296's pair differential (the two unregistered
names differ in the log); M5/M6 — a caller key in the path (unit tests:
the sweep's schemas have no failing caller-keyed position on main, as
#296 measured); M7 — the constraint allowlist changed; M9 and M13 — the
usefulness assertion (a `ValueError` validator degrades to `is invalid`
and leaks nothing; pydantic's own `msg` for `dict_type` carries no value
either, so M13 is caught by wording, not by the leak oracle). M4 was first
run as `and e.title == ... -> and False`, which also flips the response
prefix and went red on wording (`Validation error: ...`) before reaching
the audit; the recorded M4 mutates only the audit branch (`if
rejected_by_model and tool is not None: -> if False:`).

## Non-goals

- **Echo of schema-valid values** (the list under *Research summary*):
  lookups, policy refusals, allowlist refusals and free text that the
  handlers report back, most of them also in structured output fields by
  design. They are not validation errors, and removing them site by site
  would leave the structured fields. The class fix for "a token in the
  wrong field" there is to make the field reject token-shaped input, i.e.
  patterns on identifier fields (`tool_id` could reuse
  `scoped_advisor_audit._TOOL_ID_PATTERN`; `server_name`, `request_id`,
  `env_var`), which changes advertised schemas — proposed as a follow-up
  issue alongside piece B of Consiliency/pmcp#236.
- **The unknown/blocked tool name in the response** (`server.py:359`,
  `:380`, `:444`): the caller's own routing name, pinned by three tests;
  only the log line is fixed here.
- **Downstream tools' own error text** returned as their result: the
  product of `gateway.invoke`, covered by redaction (Consiliency/pmcp#234).
- **`RecursionError` from `jsonschema.validate` on in-process nesting
  (#296's E12)**: the wire cannot deliver it (#296 measured); unchanged.
- **Piece B.** B's plan must re-run this sweep; see *Unverified* for how
  its extra-key decorations are expected to interact with B.

## Unverified

- **Under piece B** the sweep's extra-key decorations become
  `additionalProperties` rejections. jsonschema's `best_match` is expected
  to prefer the deeper failing error, so the path assertions should hold,
  and an `additionalProperties`-only failure reads `$...: has a property
  that is not accepted` without the key (the latter is unit-tested here;
  the former is not measured).
- That no client other than the tests above parses jsonschema's wording
  after `Input validation error: ` (the CHANGELOG calls the change out).
- Readers of `audit.rejection` other than agent-harness @ `18a324a4` (the
  only one found); a model rejection now carries
  `rejected_argument_validator: null`, a value #296's gate records already
  allowed (unknown keyword).
- pydantic error types outside `_MODEL_PHRASES` render `is invalid`; which
  types a future model can raise was not enumerated beyond pydantic-core
  2.41.5's 103.

## Execution Policy

- execute: effort=low, reason=the patch is embedded verbatim and proven
  byte-identical to verified code; the executor applies, runs
  *Verification* and compares with *Acceptance criteria*.
- Every PR to main needs panel CR + reconcile first (repo rule).
- Commit/PR text says "see Consiliency/pmcp#297", never a closing keyword.

## Embedding proof

Patches generated with `git diff 7680445 19dac95 -- <file>` and embedded;
then, from **this file**, on a fresh worktree `$WORKTREE_ROOT/pmcp-297-proof`
of `7680445` (`<scratch>` = the session scratch dir):

```text
$ git -C <proof worktree> rev-parse --short HEAD
7680445
$ awk '/^#### Extractor/{...}' <plan> > <scratch>/emb/extract_plan_block.py
$ python3 <scratch>/emb/extract_plan_block.py <plan> "#### Extractor" <scratch>/emb/x2.py && cmp ...
<scratch>/emb/x2.py: 25 lines
extractor self-extract: identical
<scratch>/emb/argument_errors.patch: 394 lines
<scratch>/emb/server.patch: 135 lines
<scratch>/emb/scoped_advisor_audit.patch: 123 lines
<scratch>/emb/types.patch: 50 lines
<scratch>/emb/handlers.patch: 29 lines
<scratch>/emb/test_argument_error_echo.patch: 811 lines
<scratch>/emb/test_scoped_advisor_audit.patch: 402 lines
<scratch>/emb/test_gateway_tool_schemas.patch: 28 lines
<scratch>/emb/CHANGELOG.patch: 12 lines
<scratch>/emb/README.patch: 19 lines
$ git apply --check <scratch>/emb/*.patch
check: ok
applied
cmp src/pmcp/argument_errors.py: identical to wip/297-code@19dac95
cmp src/pmcp/server.py: identical to wip/297-code@19dac95
cmp src/pmcp/scoped_advisor_audit.py: identical to wip/297-code@19dac95
cmp src/pmcp/types.py: identical to wip/297-code@19dac95
cmp src/pmcp/tools/handlers.py: identical to wip/297-code@19dac95
cmp tests/test_argument_error_echo.py: identical to wip/297-code@19dac95
cmp tests/test_scoped_advisor_audit.py: identical to wip/297-code@19dac95
cmp tests/test_gateway_tool_schemas.py: identical to wip/297-code@19dac95
cmp CHANGELOG.md: identical to wip/297-code@19dac95
cmp README.md: identical to wip/297-code@19dac95
$ git status --short
 M CHANGELOG.md
 M README.md
 M src/pmcp/scoped_advisor_audit.py
 M src/pmcp/server.py
 M src/pmcp/tools/handlers.py
 M src/pmcp/types.py
 M tests/test_gateway_tool_schemas.py
 M tests/test_scoped_advisor_audit.py
?? src/pmcp/argument_errors.py
?? tests/test_argument_error_echo.py
$ git add -N . && git diff --quiet 19dac95 && echo "proof tree == 19dac95 (whole tree)"
proof tree == 19dac95 (whole tree)
```

Gates on the proof tree (after `uv sync --all-extras -p 3.10`):

```text
$ ruff check src/ tests/
All checks passed!
$ ruff format --check src/ tests/
172 files already formatted
$ mypy src/
Success: no issues found in 53 source files
$ pytest tests/test_argument_error_echo.py tests/test_scoped_advisor_audit.py tests/test_gateway_tool_schemas.py -q
392 passed in 24.53s
```

## Full suite and gates

On `wip/297-code` @ `19dac95` (the tree the proof `cmp`s against),
`npm_config_cache`, `npm_config_store_dir` and `pnpm_config_store_dir`
unset (dev0 is a team host):

```text
$ pytest -m 'not live and not slow' -q
4717 passed, 3 skipped, 80 deselected in 611.07s (0:10:11)
EXIT=0
```

## Verbatim bodies

### How to apply (and the extractor)

From a fresh worktree of `origin/main` @ `7680445`:

```bash
PLAN=.consiliency/plans/detailed-297-validation-echo-20260928-2127.md   # read from branch plan/297-validation-echo
X=<scratch>/extract_plan_block.py        # the script below, saved verbatim
for f in argument_errors server scoped_advisor_audit types handlers test_argument_error_echo test_scoped_advisor_audit test_gateway_tool_schemas CHANGELOG README; do
  python3 $X $PLAN "### Patch — \`$f" <scratch>/$f.patch
done
git apply --check <scratch>/*.patch && git apply <scratch>/*.patch
```

Patches are fenced with **four** backticks, and the extractor closes on the
same fence string. Blank context lines carry one leading space: an editor
that strips trailing whitespace breaks them and `git apply --check` fails
loudly. The test source is ASCII (non-ASCII test strings are `\u`
escapes); the two doc patches carry pre-existing non-ASCII context.

#### Extractor

Bootstrap it from this file with
`awk '/^#### Extractor/{f=1;next} f&&/^```python/{g=1;next} g&&/^```$/{exit} g' $PLAN > $X`
(it then extracts itself byte-identically: `python3 $X $PLAN "#### Extractor" x2.py && cmp $X x2.py`).

```python
"""Extract one verbatim patch from the Consiliency/pmcp#297 plan, byte for byte.

usage: python extract_plan_block.py <plan.md> "<heading prefix>" <out-file>

Finds the single line that starts with the heading prefix, takes the first
fenced block after it, and writes every line up to the line that equals that
block's opening fence (stripped of its info string), each followed by "\n".
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
fence = lines[i][: len(lines[i]) - len(lines[i].lstrip("`"))]
j = i + 1
while lines[j] != fence:
    j += 1
Path(out).write_text("".join(line + "\n" for line in lines[i + 1 : j]))
print(f"{out}: {j - i - 1} lines")
```

### Patch — `argument_errors` (`src/pmcp/argument_errors.py`)

````diff
diff --git a/src/pmcp/argument_errors.py b/src/pmcp/argument_errors.py
new file mode 100644
index 0000000..03bbdeb
--- /dev/null
+++ b/src/pmcp/argument_errors.py
@@ -0,0 +1,388 @@
+"""Describe a rejected gateway-tool argument without the value that failed.
+
+A gateway tool's arguments are checked twice: by the advertised JSON Schema
+(``GatewayServer._handle_call_tool``'s gate) and by the tool's pydantic model
+(``pmcp.types.*Input``). The text both libraries render for a failure embeds
+the rejected value -- jsonschema's ``message`` (``'sk-...' is not of type
+'object'``), pydantic's ``input_value=...`` and a custom validator's own
+message -- and it went verbatim into the tool response and the gateway log
+(Consiliency/pmcp#297). A caller can put a secret in any argument, so that
+text may carry one.
+
+These functions render a rejection from its *structure* instead, never from
+its text:
+
+- **where**: the failing location, as a JSON path (``$.options.timeout_ms``).
+  A segment survives only as a list index or as a name an argument model or
+  schema declares; any other key was chosen by the caller and may itself be a
+  secret, so it becomes ``*``.
+- **why**: a fixed phrase for the failing keyword (jsonschema) or error type
+  (pydantic), filled only from the constraint the gateway's own schema or
+  model defines (``must be at most 128 characters``). An unknown keyword or
+  type renders as ``is invalid``.
+
+Neither reads a value: not jsonschema's ``message``, ``instance``,
+``validator_value``, ``context`` or ``cause``, and not pydantic's ``msg``,
+``input`` or any ``ctx`` entry outside :data:`_CONSTRAINT_CONTEXT`.
+"""
+
+from __future__ import annotations
+
+import json
+from collections.abc import Iterable
+from typing import Any
+
+import jsonschema
+from pydantic import ValidationError
+from pydantic_core import PydanticCustomError
+
+#: Stands in for a path segment the caller chose.
+REDACTED_SEGMENT = "*"
+
+#: What a rejection reads as if describing it fails (an in-process mapping
+#: whose lookup raises, say); the exception itself is never rendered.
+_UNDESCRIBED = "$: is invalid"
+
+#: At most this many pydantic errors are described; the rest are counted.
+_MAX_MODEL_ERRORS = 5
+
+# --- pmcp's own validator errors -------------------------------------------
+# A custom validator raises one of these instead of ``ValueError(...)``: its
+# type selects a fixed message below, so no validator can interpolate the
+# value it rejects into what the caller or the log sees.
+
+CORRELATION_ID_CHARSET = "correlation_id_charset"
+SCOPED_CORRELATION_INCOMPLETE = "scoped_correlation_incomplete"
+PACKAGE_NAME_INVALID = "package_name_invalid"
+
+_PMCP_MESSAGES: dict[str, str] = {
+    CORRELATION_ID_CHARSET: "correlation IDs may contain only alphanumerics and ._:-",
+    SCOPED_CORRELATION_INCOMPLETE: (
+        "scoped advisor correlation fields must be supplied together"
+    ),
+    PACKAGE_NAME_INVALID: (
+        "package must be a valid npm/pypi identifier (no leading dash, "
+        "whitespace, path separators, or shell metacharacters)"
+    ),
+}
+
+
+def argument_error(error_type: str) -> PydanticCustomError:
+    """The error a pmcp validator raises for ``error_type`` (a constant above)."""
+    return PydanticCustomError(error_type, _PMCP_MESSAGES[error_type])
+
+
+# --- pydantic ----------------------------------------------------------------
+
+#: The ``ctx`` keys a phrase below may name. Each is a constraint the model
+#: declares (a length, a bound, a pattern, the allowed literals), never read
+#: from the input. ``actual_length``, ``error``, ``tag`` and the rest are
+#: left out on purpose.
+_CONSTRAINT_CONTEXT = frozenset(
+    {"min_length", "max_length", "pattern", "expected", "gt", "ge", "lt", "le"}
+)
+
+_MODEL_PHRASES: dict[str, str] = {
+    "missing": "is required",
+    "extra_forbidden": "is not an accepted argument",
+    "string_type": "must be a string",
+    "string_too_short": "must be at least {min_length} characters",
+    "string_too_long": "must be at most {max_length} characters",
+    "string_pattern_mismatch": "must match the pattern {pattern}",
+    "int_type": "must be an integer",
+    "int_parsing": "must be an integer",
+    "int_from_float": "must be an integer",
+    "float_type": "must be a number",
+    "float_parsing": "must be a number",
+    "bool_type": "must be a boolean",
+    "bool_parsing": "must be a boolean",
+    "dict_type": "must be an object",
+    "model_type": "must be an object",
+    "model_attributes_type": "must be an object",
+    "list_type": "must be an array",
+    "too_short": "must have at least {min_length} items",
+    "too_long": "must have at most {max_length} items",
+    "literal_error": "must be {expected}",
+    "enum": "must be {expected}",
+    "greater_than": "must be greater than {gt}",
+    "greater_than_equal": "must be greater than or equal to {ge}",
+    "less_than": "must be less than {lt}",
+    "less_than_equal": "must be less than or equal to {le}",
+    **_PMCP_MESSAGES,
+}
+
+
+def _argument_names() -> frozenset[str]:
+    """Every field name and alias of every gateway argument model.
+
+    Written by pmcp's authors, never by a caller, so a location segment equal
+    to one discloses nothing the advertised schemas do not.
+    """
+    from pmcp.types import GatewayArguments
+
+    names: set[str] = set()
+    pending: list[type] = [GatewayArguments]
+    while pending:
+        model = pending.pop()
+        pending.extend(model.__subclasses__())
+        for name, field in getattr(model, "model_fields", {}).items():
+            names.add(name)
+            if isinstance(field.alias, str):
+                names.add(field.alias)
+    return frozenset(names)
+
+
+def _render_path(segments: Iterable[str | int | None]) -> str:
+    path = "$"
+    for segment in segments:
+        if segment is None:
+            path += f".{REDACTED_SEGMENT}"
+        elif isinstance(segment, int):
+            path += f"[{segment}]"
+        else:
+            path += f".{segment}"
+    return path
+
+
+def _model_error_path(
+    loc: tuple[Any, ...], arguments: Any, declared: frozenset[str]
+) -> list[str | int | None]:
+    """``loc`` with every caller-chosen key redacted; reads only container
+    types from ``arguments`` (to tell a list index from an ``int`` key)."""
+    path: list[str | int | None] = []
+    node = arguments
+    for segment in loc:
+        if isinstance(node, list) and type(segment) is int:
+            path.append(segment)
+        elif type(segment) is str and segment in declared:
+            path.append(segment)
+        else:
+            path.append(None)
+        try:
+            node = node[segment]
+        except (KeyError, IndexError, TypeError):
+            node = None
+    return path
+
+
+def model_error_path(
+    error: ValidationError, schema: Any, arguments: Any
+) -> list[str | int | None]:
+    """The first error's location, redacted as :func:`schema_error_path` is."""
+    declared = _argument_names() | declared_property_names(schema)
+    items = error.errors(include_url=False, include_input=False, include_context=False)
+    loc = tuple(items[0]["loc"]) if items else ()
+    return _model_error_path(loc, arguments, declared)
+
+
+def _model_phrase(error_type: Any, ctx: Any) -> str:
+    phrase = _MODEL_PHRASES.get(error_type) if isinstance(error_type, str) else None
+    if phrase is None:
+        return "is invalid"
+    context = ctx if isinstance(ctx, dict) else {}
+    fields = {key: context.get(key) for key in _CONSTRAINT_CONTEXT if key in context}
+    try:
+        return phrase.format(**fields)
+    except (KeyError, IndexError, ValueError):
+        return "is invalid"
+
+
+def describe_model_error(error: ValidationError, schema: Any, arguments: Any) -> str:
+    """``$.<loc>: <phrase>`` for each of pydantic's errors, joined by ``; ``."""
+    try:
+        return _describe_model_error(error, schema, arguments)
+    except Exception:
+        return _UNDESCRIBED
+
+
+def _describe_model_error(error: ValidationError, schema: Any, arguments: Any) -> str:
+    declared = _argument_names() | declared_property_names(schema)
+    items = error.errors(include_url=False, include_input=False, include_context=True)
+    parts = [
+        f"{_render_path(_model_error_path(tuple(item['loc']), arguments, declared))}: "
+        f"{_model_phrase(item['type'], item.get('ctx'))}"
+        for item in items[:_MAX_MODEL_ERRORS]
+    ]
+    if len(items) > _MAX_MODEL_ERRORS:
+        parts.append(f"and {len(items) - _MAX_MODEL_ERRORS} more")
+    return "; ".join(parts)
+
+
+# --- jsonschema --------------------------------------------------------------
+
+
+def declared_property_names(schema: Any) -> frozenset[str]:
+    """Every key of every ``properties`` map anywhere in ``schema``.
+
+    These strings are written by the schema's author, never by a caller, so a
+    path segment equal to one of them discloses nothing the schema does not.
+    """
+    names: set[str] = set()
+
+    def collect(node: Any) -> None:
+        if isinstance(node, dict):
+            properties = node.get("properties")
+            if isinstance(properties, dict):
+                names.update(key for key in properties if isinstance(key, str))
+            for child in node.values():
+                collect(child)
+        elif isinstance(node, list):
+            for child in node:
+                collect(child)
+
+    collect(schema)
+    return frozenset(names)
+
+
+def schema_error_path(
+    error: jsonschema.ValidationError, schema: Any, arguments: Any
+) -> list[str | int | None]:
+    """The failing instance location, with every caller-chosen key redacted.
+
+    Walks ``error.absolute_path`` (not ``.path``, which is relative when
+    ``best_match`` returns an ``anyOf`` child). A segment survives only as an
+    array index (an ``int`` whose container is a list) or as a key the schema
+    declares under some ``properties``. Any other key -- one matched by
+    ``additionalProperties`` or ``patternProperties``, or a non-``str`` key a
+    caller built in-process (an ``int``, or a ``str`` subclass whose ``__eq__``
+    could pass the membership test) -- is chosen by the caller and may itself
+    be a secret, so it becomes ``None``. The walk reads only container types
+    from ``arguments``, never a value.
+    """
+    declared = declared_property_names(schema)
+    path: list[str | int | None] = []
+    node = arguments
+    for segment in error.absolute_path:
+        if isinstance(node, list) and type(segment) is int:
+            path.append(segment)
+        elif isinstance(node, dict) and type(segment) is str and segment in declared:
+            path.append(segment)
+        else:
+            path.append(None)
+        try:
+            node = node[segment]
+        except (KeyError, IndexError, TypeError):
+            node = None
+    return path
+
+
+def schema_error_keyword(error: jsonschema.ValidationError, schema: Any) -> str | None:
+    """The failing keyword, if it is one the schema's draft defines."""
+    keywords = jsonschema.validators.validator_for(schema).VALIDATORS
+    validator = error.validator
+    return validator if isinstance(validator, str) and validator in keywords else None
+
+
+def _schema_node(schema: Any, schema_path: Iterable[Any]) -> Any:
+    """The node of *our* schema that holds the failing keyword."""
+    node = schema
+    for segment in schema_path:
+        node = node[segment]
+    return node
+
+
+def _count(number: Any, noun: str) -> str:
+    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
+
+
+def _type_names(value: Any) -> str:
+    names = value if isinstance(value, list) else [value]
+    return " or ".join(str(name) for name in names)
+
+
+def _missing_required(
+    required: Any, error: jsonschema.ValidationError, arguments: Any
+) -> str | None:
+    """The first name ``required`` lists that the object lacks. Only the
+    schema's own names are returned; the object is only tested for them."""
+    node = arguments
+    for segment in error.absolute_path:
+        try:
+            node = node[segment]
+        except (KeyError, IndexError, TypeError):
+            return None
+    if not isinstance(node, dict) or not isinstance(required, list):
+        return None
+    for name in required:
+        if isinstance(name, str) and name not in node:
+            return name
+    return None
+
+
+def _schema_phrase(
+    keyword: str | None,
+    error: jsonschema.ValidationError,
+    schema: Any,
+    arguments: Any,
+) -> tuple[str, str | None]:
+    """``(phrase, extra path segment)`` for ``keyword``, read from our schema."""
+    if keyword is None:
+        return "is invalid", None
+    try:
+        node = _schema_node(schema, list(error.absolute_schema_path)[:-1])
+        constraint = node[keyword]
+    except (KeyError, IndexError, TypeError):
+        return "is invalid", None
+    if keyword == "type":
+        return f"must be of type {_type_names(constraint)}", None
+    if keyword == "enum":
+        return f"must be one of {json.dumps(constraint)}", None
+    if keyword == "const":
+        return f"must be {json.dumps(constraint)}", None
+    if keyword == "pattern":
+        return f"must match the pattern {constraint}", None
+    if keyword == "minLength":
+        return f"must be at least {_count(constraint, 'character')}", None
+    if keyword == "maxLength":
+        return f"must be at most {_count(constraint, 'character')}", None
+    if keyword == "minItems":
+        return f"must have at least {_count(constraint, 'item')}", None
+    if keyword == "maxItems":
+        return f"must have at most {_count(constraint, 'item')}", None
+    if keyword == "minimum":
+        return f"must be greater than or equal to {constraint}", None
+    if keyword == "maximum":
+        return f"must be less than or equal to {constraint}", None
+    if keyword == "exclusiveMinimum":
+        return f"must be greater than {constraint}", None
+    if keyword == "exclusiveMaximum":
+        return f"must be less than {constraint}", None
+    if keyword == "required":
+        return "is required", _missing_required(constraint, error, arguments)
+    if keyword in ("additionalProperties", "unevaluatedProperties"):
+        return "has a property that is not accepted", None
+    return f"fails the schema's {keyword} constraint", None
+
+
+def describe_schema_error(
+    error: jsonschema.ValidationError, schema: Any, arguments: Any
+) -> str:
+    """``$.<path>: <phrase>`` for one jsonschema error, from its structure."""
+    try:
+        return _describe_schema_error(error, schema, arguments)
+    except Exception:
+        return _UNDESCRIBED
+
+
+def _describe_schema_error(
+    error: jsonschema.ValidationError, schema: Any, arguments: Any
+) -> str:
+    path = schema_error_path(error, schema, arguments)
+    phrase, missing = _schema_phrase(
+        schema_error_keyword(error, schema), error, schema, arguments
+    )
+    if missing is not None:
+        path = [*path, missing]
+    return f"{_render_path(path)}: {phrase}"
+
+
+def describe_argument_error(
+    error: BaseException, schema: Any, arguments: Any
+) -> str | None:
+    """A value-free description of ``error`` if it is an argument-validation
+    error of either library, else ``None``."""
+    if isinstance(error, ValidationError):
+        return describe_model_error(error, schema, arguments)
+    if isinstance(error, jsonschema.ValidationError):
+        return describe_schema_error(error, schema, arguments)
+    return None
````

### Patch — `server` (`src/pmcp/server.py`)

````diff
diff --git a/src/pmcp/server.py b/src/pmcp/server.py
index 0a6ef28..45988f1 100644
--- a/src/pmcp/server.py
+++ b/src/pmcp/server.py
@@ -12,6 +12,7 @@ from pathlib import Path
 from typing import Any, Literal
 
 import jsonschema
+import pydantic
 from mcp.server import Server
 from mcp.server.context import ServerRequestContext
 from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
@@ -37,6 +38,7 @@ from mcp.types import (
     Tool,
 )
 
+from pmcp.argument_errors import describe_argument_error, describe_schema_error
 from pmcp.client.manager import ClientManager
 from pmcp.config.guidance import GuidanceConfig, load_guidance_config
 from pmcp.config.loader import (
@@ -69,7 +71,11 @@ from pmcp.scoped_advisor_audit import (
 )
 from pmcp.subscriptions import BusCatalogEventSink
 from pmcp.summary import generate_capability_summary
-from pmcp.tools.handlers import GatewayTools, get_gateway_tool_definitions
+from pmcp.tools.handlers import (
+    GATEWAY_TOOL_INPUT_MODELS,
+    GatewayTools,
+    get_gateway_tool_definitions,
+)
 from pmcp.types import (
     DescriptionsCache,
     GatewayDiagnosticsInfo,
@@ -335,11 +341,15 @@ class GatewayServer:
                             )
                         ]
                     )
+                # Never `e.message`: for `type`, `pattern`, `enum` and length
+                # errors it quotes the rejected value (Consiliency/pmcp#297).
                 return CallToolResult(
                     is_error=True,
                     content=[
                         TextContent(
-                            type="text", text=f"Input validation error: {e.message}"
+                            type="text",
+                            text="Input validation error: "
+                            + describe_schema_error(e, tool.input_schema, arguments),
                         )
                     ],
                 )
@@ -477,7 +487,36 @@ class GatewayServer:
                     )
                 ]
             except Exception as e:
-                logger.error(f"Tool execution error: {e}")
+                # A `ValidationError`'s text renders the rejected value
+                # (pydantic's `input_value=...`, a validator's own message,
+                # jsonschema's `message`); describe it from its structure
+                # instead, in the log, the response and the audit
+                # (Consiliency/pmcp#297). The tool's own argument model
+                # rejecting the call is "invalid arguments"; any other (a
+                # downstream payload a handler parses) is a "validation error".
+                input_model = GATEWAY_TOOL_INPUT_MODELS.get(audited_name or "")
+                rejected_by_model = (
+                    isinstance(e, pydantic.ValidationError)
+                    and input_model is not None
+                    and e.title == input_model.__name__
+                )
+                described = describe_argument_error(
+                    e, tool.input_schema if tool is not None else None, arguments
+                )
+                kind = "Invalid arguments" if rejected_by_model else "Validation error"
+                if described is not None:
+                    logger.error(
+                        "Tool execution error: %s for %s: %s",
+                        kind.lower(),
+                        audited_name,
+                        described,
+                    )
+                elif tool is None:
+                    # Only an unregistered name raises here; it is the
+                    # caller's string, so it is not logged (Consiliency/pmcp#297).
+                    logger.error("Tool execution error: unknown gateway tool")
+                else:
+                    logger.error(f"Tool execution error: {e}")
                 try:
                     failure_status = (
                         "denied"
@@ -485,12 +524,24 @@ class GatewayServer:
                         and e.code == ErrorCode.E402_TOOL_DENIED
                         else "failure"
                     )
-                    self._record_scoped_invocation(
-                        gateway_tool=audited_name,
-                        terminal_status=failure_status,
-                        arguments=audited_arguments,
-                        result={"error_type": type(e).__name__},
-                    )
+                    if rejected_by_model and tool is not None:
+                        # Like a gate rejection: an `audit.rejection` (tool,
+                        # path, nothing the caller sent), not an invocation
+                        # whose correlations nothing vouched for.
+                        if self._scoped_advisor_audit is not None:
+                            self._scoped_advisor_audit.record_rejected_arguments(
+                                gateway_tool=tool.name,
+                                error=e,
+                                schema=tool.input_schema,
+                                arguments=arguments,
+                            )
+                    else:
+                        self._record_scoped_invocation(
+                            gateway_tool=audited_name,
+                            terminal_status=failure_status,
+                            arguments=audited_arguments,
+                            result={"error_type": type(e).__name__},
+                        )
                 except ScopedAdvisorAuditError:
                     logger.error("Scoped advisor audit channel failed")
                     return [
@@ -507,7 +558,14 @@ class GatewayServer:
                 return [
                     TextContent(
                         type="text",
-                        text=json.dumps({"error": True, "message": str(e)[:400]}),
+                        text=json.dumps(
+                            {
+                                "error": True,
+                                "message": f"{kind}: {described}"
+                                if described is not None
+                                else str(e)[:400],
+                            }
+                        ),
                     )
                 ]
 
````

### Patch — `scoped_advisor_audit` (`src/pmcp/scoped_advisor_audit.py`)

````diff
diff --git a/src/pmcp/scoped_advisor_audit.py b/src/pmcp/scoped_advisor_audit.py
index 76d96e6..142ede6 100644
--- a/src/pmcp/scoped_advisor_audit.py
+++ b/src/pmcp/scoped_advisor_audit.py
@@ -14,6 +14,13 @@ from typing import Any, TextIO
 from urllib.parse import urlsplit, urlunsplit
 
 import jsonschema
+import pydantic
+
+from pmcp.argument_errors import (
+    model_error_path,
+    schema_error_keyword,
+    schema_error_path,
+)
 
 SCOPED_ADVISOR_AUDIT_CAPABILITY = "scoped_advisor_audit.v1"
 #: Event of a ``tools/call`` the input-schema gate rejected before dispatch
@@ -138,70 +145,6 @@ def _public_source_hash(value: Any) -> str | None:
     return None
 
 
-def _declared_property_names(schema: Any) -> frozenset[str]:
-    """Every key of every ``properties`` map anywhere in ``schema``.
-
-    These strings are written by the schema's author, never by a caller, so a
-    path segment equal to one of them discloses nothing the schema does not.
-    """
-    names: set[str] = set()
-
-    def collect(node: Any) -> None:
-        if isinstance(node, dict):
-            properties = node.get("properties")
-            if isinstance(properties, dict):
-                names.update(key for key in properties if isinstance(key, str))
-            for child in node.values():
-                collect(child)
-        elif isinstance(node, list):
-            for child in node:
-                collect(child)
-
-    collect(schema)
-    return frozenset(names)
-
-
-def _rejected_argument_path(
-    error: jsonschema.ValidationError, schema: Any, arguments: Any
-) -> list[str | int | None]:
-    """The failing instance location, with every caller-chosen key redacted.
-
-    Walks ``error.absolute_path`` (not ``.path``, which is relative when
-    ``best_match`` returns an ``anyOf`` child). A segment survives only as an
-    array index (an ``int`` whose container is a list) or as a key the schema
-    declares under some ``properties``. Any other key -- one matched by
-    ``additionalProperties`` or ``patternProperties``, or a non-``str`` key a
-    caller built in-process (an ``int``, or a ``str`` subclass whose ``__eq__``
-    could pass the membership test) -- is chosen by the caller and may itself
-    be a secret, so it becomes ``None``. The walk reads only container types
-    from ``arguments``, never a value.
-    """
-    declared = _declared_property_names(schema)
-    path: list[str | int | None] = []
-    node = arguments
-    for segment in error.absolute_path:
-        if isinstance(node, list) and type(segment) is int:
-            path.append(segment)
-        elif isinstance(node, dict) and type(segment) is str and segment in declared:
-            path.append(segment)
-        else:
-            path.append(None)
-        try:
-            node = node[segment]
-        except (KeyError, IndexError, TypeError):
-            node = None
-    return path
-
-
-def _rejected_argument_validator(
-    error: jsonschema.ValidationError, schema: Any
-) -> str | None:
-    """The failing keyword, if it is one the schema's draft defines."""
-    keywords = jsonschema.validators.validator_for(schema).VALIDATORS
-    validator = error.validator
-    return validator if isinstance(validator, str) and validator in keywords else None
-
-
 class ScopedAdvisorAudit:
     """Append-only JSONL writer with a single fsynced terminal marker."""
 
@@ -304,11 +247,16 @@ class ScopedAdvisorAudit:
         self,
         *,
         gateway_tool: str,
-        error: jsonschema.ValidationError,
+        error: jsonschema.ValidationError | pydantic.ValidationError,
         schema: dict[str, Any],
         arguments: dict[str, Any],
     ) -> None:
-        """Record a ``tools/call`` the input-schema gate rejected (Consiliency/pmcp#296).
+        """Record a ``tools/call`` the input-schema gate rejected (Consiliency/pmcp#296),
+        or the tool's own argument model did (Consiliency/pmcp#297).
+
+        A model rejection's path is its first error's location, redacted the
+        same way; its validator is ``None`` (a pydantic error type is not a
+        JSON Schema keyword).
 
         The record names the tool, the failing JSON path and the validator
         keyword, and nothing else from the call. It never reads the error's
@@ -327,8 +275,12 @@ class ScopedAdvisorAudit:
         may carry a caller value, and nothing chains it into a log.
         """
         try:
-            argument_path = _rejected_argument_path(error, schema, arguments)
-            validator = _rejected_argument_validator(error, schema)
+            if isinstance(error, jsonschema.ValidationError):
+                argument_path = schema_error_path(error, schema, arguments)
+                validator = schema_error_keyword(error, schema)
+            else:
+                argument_path = model_error_path(error, schema, arguments)
+                validator = None
         except Exception:
             raise ScopedAdvisorAuditError(
                 "scoped advisor audit could not describe a rejected call"
````

### Patch — `types` (`src/pmcp/types.py`)

````diff
diff --git a/src/pmcp/types.py b/src/pmcp/types.py
index 95b5a53..874fa1f 100644
--- a/src/pmcp/types.py
+++ b/src/pmcp/types.py
@@ -9,6 +9,12 @@ from typing import Annotated, Any, Literal
 
 from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
 
+from pmcp.argument_errors import (
+    CORRELATION_ID_CHARSET,
+    PACKAGE_NAME_INVALID,
+    SCOPED_CORRELATION_INCOMPLETE,
+    argument_error,
+)
 from pmcp.validation import is_valid_package_name, version_separator_index
 
 # === Transport Types ===
@@ -884,7 +890,7 @@ class InvokeInput(GatewayArguments):
         if value is None:
             return None
         if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) is None:
-            raise ValueError("correlation IDs may contain only alphanumerics and ._:-")
+            raise argument_error(CORRELATION_ID_CHARSET)
         return value
 
     @model_validator(mode="after")
@@ -897,9 +903,7 @@ class InvokeInput(GatewayArguments):
         if any(value is not None for value in values) and not all(
             value is not None for value in values
         ):
-            raise ValueError(
-                "scoped advisor correlation fields must be supplied together"
-            )
+            raise argument_error(SCOPED_CORRELATION_INCOMPLETE)
         return self
 
 
@@ -1392,11 +1396,7 @@ class RegisterDiscoveredServerInput(GatewayArguments):
     @classmethod
     def _validate_package(cls, value: str) -> str:
         if not is_valid_package_name(value):
-            raise ValueError(
-                "package must be a valid npm/pypi identifier "
-                "(no leading dash, whitespace, path separators, or shell "
-                "metacharacters)"
-            )
+            raise argument_error(PACKAGE_NAME_INVALID)
         return value
 
 
````

### Patch — `handlers` (`src/pmcp/tools/handlers.py`)

````diff
diff --git a/src/pmcp/tools/handlers.py b/src/pmcp/tools/handlers.py
index 45a956c..7bde5da 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -5569,10 +5569,12 @@ class GatewayTools:
         """gateway.provision_status - Check status of a running installation."""
         import time
 
+        # Outside the `try`: its arm logs a traceback and renders `str(e)`,
+        # and a `ValidationError`'s text carries the rejected value. Raised,
+        # it is described without it (Consiliency/pmcp#297).
+        parsed = ProvisionStatusInput.model_validate(input_data)
+        job_id = parsed.job_id
         try:
-            parsed = ProvisionStatusInput.model_validate(input_data)
-            job_id = parsed.job_id
-
             job_manager = get_job_manager()
             job = job_manager.get_job(job_id)
 
@@ -5643,7 +5645,7 @@ class GatewayTools:
             logger.error(f"provision_status handler failed: {e}", exc_info=True)
             # Return a safe error response instead of crashing
             return ProvisionJobStatus(
-                job_id=input_data.get("job_id", "unknown"),
+                job_id=job_id,
                 server="unknown",
                 status="failed",
                 progress=0,
````

### Patch — `test_argument_error_echo` (`tests/test_argument_error_echo.py`)

````diff
diff --git a/tests/test_argument_error_echo.py b/tests/test_argument_error_echo.py
new file mode 100644
index 0000000..86f8e00
--- /dev/null
+++ b/tests/test_argument_error_echo.py
@@ -0,0 +1,805 @@
+"""A rejected gateway-tool argument never echoes its value (Consiliency/pmcp#297).
+
+The oracle is a generated sweep, not hand-picked cases. Its axes come from the
+code:
+
+- every registered gateway tool (``GATEWAY_TOOL_INPUT_MODELS``);
+- every position its advertised schema declares -- nested object properties
+  and list items included -- and, at each, every invalid shape that schema
+  node rejects (wrong type, pattern, length, enum);
+- every custom validator on every argument model the tool parses with, found
+  by introspection (a new validator without a recipe fails the sweep);
+- every field an argument model also accepts by name under
+  ``populate_by_name`` (``InvokeInput.meta``), which the gate never sees;
+- every validation-error type (pydantic and jsonschema) raised from inside a
+  handler.
+
+Each case carries one sentinel in every position the caller controls around
+the invalid value -- the value itself, dict keys, list items, extra keys on
+every object on the path, and every open container (``invoke.arguments``,
+``_meta``, ``task.metadata``, ``requestor_context``). The sweep asserts that
+each case was rejected by argument validation (no vacuous pass), that the
+rejection names the failing field, and that the sentinel -- and its hashes --
+reaches neither the response, the log (every record at DEBUG, rendered by
+pmcp's own text and JSON formatters, tracebacks included) nor the scoped
+audit. It also runs every case twice with sentinels of different length and
+requires the response, log and audit to be identical, so no length, count or
+hash of the value is disclosed either.
+"""
+
+from __future__ import annotations
+
+import copy
+import hashlib
+import json
+import logging
+import string
+import typing
+from pathlib import Path
+from typing import Any
+
+import jsonschema
+import pytest
+from mcp.types import CallToolRequestParams
+from pydantic import BaseModel, ValidationError
+
+from pmcp.server import GatewayServer
+from pmcp.tools.handlers import GATEWAY_TOOL_INPUT_MODELS, get_gateway_tool_definitions
+from pmcp.types import InvokeInput, McpTaskInfo
+from tests.test_scoped_advisor_audit import (
+    _correlations,
+    _make_ctx,
+    _normalized,
+    _pmcp_formatters,
+    _stable,
+    _write_scoped_policy,
+)
+
+#: Two high-entropy sentinels of different length (ASCII alphanumerics, so no
+#: renderer escapes them out of a substring search).
+_SENTINELS = (
+    "Sq" + hashlib.sha256(b"pmcp-297-a").hexdigest()[:22] + "Zx",
+    "Sq" + hashlib.sha256(b"pmcp-297-b").hexdigest()[:38] + "Zx",
+)
+
+_GATE_PREFIX = "Input validation error: $"
+_MODEL_PREFIX = "Invalid arguments: $"
+
+
+#: pydantic truncates a long `input_value` repr in the middle
+#: (`'Sq3b2ee216cab4bcafa8599...e216cab4bcafa85997Zx'`), so the whole sentinel
+#: never appears in such a leak; every window of this many characters is
+#: searched for instead (48 bits of hex: no accidental match).
+_WINDOW = 12
+
+
+def _forbidden(sentinel: str) -> set[str]:
+    """Every window of the sentinel, and each hash of it, a log or record
+    could carry."""
+    forms = {sentinel[i : i + _WINDOW] for i in range(len(sentinel) - _WINDOW + 1)}
+    for text in (sentinel, json.dumps(sentinel), sentinel.lower()):
+        forms.add(hashlib.sha256(text.encode()).hexdigest())
+        forms.add(hashlib.sha1(text.encode()).hexdigest())
+        forms.add(hashlib.md5(text.encode()).hexdigest())
+    return forms
+
+
+# --- the generator -------------------------------------------------------------
+
+
+def _tools() -> dict[str, Any]:
+    return {tool.name: tool for tool in get_gateway_tool_definitions()}
+
+
+def _models() -> dict[str, type[BaseModel] | None]:
+    return dict(GATEWAY_TOOL_INPUT_MODELS)
+
+
+def _types(node: dict[str, Any]) -> list[str]:
+    declared = node.get("type")
+    if declared is None:
+        return []
+    return declared if isinstance(declared, list) else [declared]
+
+
+def _positions(
+    node: dict[str, Any], path: tuple[str | int, ...] = ()
+) -> list[tuple[tuple[str | int, ...], dict[str, Any]]]:
+    """Every declared position below the root, depth-first."""
+    found: list[tuple[tuple[str | int, ...], dict[str, Any]]] = []
+    for key, child in sorted((node.get("properties") or {}).items()):
+        found.append(((*path, key), child))
+        found.extend(_positions(child, (*path, key)))
+    items = node.get("items")
+    if isinstance(items, dict):
+        found.append(((*path, 0), items))
+        found.extend(_positions(items, (*path, 0)))
+    return found
+
+
+def _invalid_values(node: dict[str, Any], s: str) -> list[tuple[str, Any]]:
+    """Values this schema node rejects, each carrying `s` in every slot."""
+    types = _types(node)
+    candidates: list[tuple[str, Any]] = []
+    if types and "string" not in types:
+        candidates.append(("type-string", s))
+    if types and "object" not in types:
+        candidates.append(("type-object", {s: s, "k": [s, {s: s}]}))
+    if types and "array" not in types:
+        candidates.append(("type-array", [s, {s: s}, [s]]))
+    if "string" in types:
+        if "pattern" in node:
+            length = max(node.get("minLength", 0), len(s) + 1)
+            candidates.append(("pattern", ("!" + s * 8)[:length]))
+        if "maxLength" in node:
+            candidates.append(("maxLength", s * (node["maxLength"] // len(s) + 1)))
+        if "enum" in node:
+            candidates.append(("enum", s))
+    return [
+        (label, value)
+        for label, value in candidates
+        if not jsonschema.validators.validator_for(node)(node).is_valid(value)
+    ]
+
+
+def _open_containers(schema: dict[str, Any]) -> list[tuple[str | int, ...]]:
+    """Declared object positions that accept any key."""
+    return [
+        path
+        for path, node in _positions(schema)
+        if "object" in _types(node)
+        and node.get("additionalProperties", True) is not False
+        and not node.get("properties")
+    ]
+
+
+def _open_content(s: str) -> dict[str, Any]:
+    return {s: s, "nested": {s: [s, {s: s}]}, "list": [s, 7, {s: None}]}
+
+
+def _baseline(tool: Any) -> dict[str, Any]:
+    """The smallest arguments the tool's schema accepts, from its schema."""
+    schema = tool.input_schema
+    baseline: dict[str, Any] = {}
+    for name in schema.get("required") or []:
+        prop = schema["properties"][name]
+        baseline[name] = (
+            prop["enum"][0]
+            if "enum" in prop
+            else "x" * max(prop.get("minLength", 1), 1)
+        )
+    if tool.name == "gateway.invoke":
+        baseline["tool_id"] = "srv::tool"
+        baseline.update(_correlations())
+    jsonschema.validate(baseline, schema)
+    return baseline
+
+
+def _set(
+    arguments: dict[str, Any], path: tuple[str | int, ...], value: Any, s: str
+) -> None:
+    """Put `value` at `path`, creating containers, and give every object on
+    the way an extra key carrying `s`."""
+    node: Any = arguments
+    for segment, following in zip(path, path[1:]):
+        existing = node[segment] if isinstance(node, dict) and segment in node else None
+        if isinstance(following, int):
+            child: Any = existing if isinstance(existing, list) else []
+            while len(child) <= following:
+                child.append(None)
+        else:
+            child = existing if isinstance(existing, dict) else {}
+            child[f"extra_{s}"] = s
+        if isinstance(node, list):
+            node[segment] = child  # type: ignore[index]
+        else:
+            node[segment] = child
+        node = child
+    node[path[-1]] = value
+
+
+def _decorated(tool: Any, s: str) -> dict[str, Any]:
+    """The baseline with `s` in an extra top-level key and in every open
+    container the schema declares."""
+    arguments = copy.deepcopy(_baseline(tool))
+    arguments[f"extra_{s}"] = {s: [s]}
+    for path in _open_containers(tool.input_schema):
+        _set(arguments, path, _open_content(s), s)
+    jsonschema.validate(arguments, tool.input_schema)
+    return arguments
+
+
+def _expected_path(path: tuple[str | int, ...]) -> str:
+    rendered = "$"
+    for segment in path:
+        rendered += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
+    return rendered
+
+
+def _reachable_models(model: type[BaseModel] | None) -> list[type[BaseModel]]:
+    """`model` and every model nested in its fields' annotations."""
+    seen: list[type[BaseModel]] = []
+    pending: list[Any] = [model] if model is not None else []
+    while pending:
+        current = pending.pop()
+        if isinstance(current, type) and issubclass(current, BaseModel):
+            if current in seen:
+                continue
+            seen.append(current)
+            pending.extend(f.annotation for f in current.model_fields.values())
+        else:
+            pending.extend(typing.get_args(current))
+    return seen
+
+
+#: How to fail each custom validator on an argument model, as top-level
+#: arguments merged over the decorated baseline. Keyed by (model, validator,
+#: field); the sweep asserts this table equals what introspection finds.
+_VALIDATOR_RECIPES: dict[tuple[str, str, str], Any] = {
+    ("InvokeInput", "_validate_correlation_id", "run_correlation_id"): (
+        lambda s: {"run_correlation_id": s + "!"},
+        "$.run_correlation_id: correlation IDs may contain only",
+    ),
+    ("InvokeInput", "_validate_correlation_id", "seat_correlation_id"): (
+        lambda s: {"seat_correlation_id": s + "!"},
+        "$.seat_correlation_id: correlation IDs may contain only",
+    ),
+    ("InvokeInput", "_reject_partial_scoped_correlation", ""): (
+        lambda s: {
+            "run_correlation_id": s,
+            "seat_correlation_id": None,
+            "evidence_label_digest": None,
+        },
+        "$: scoped advisor correlation fields must be supplied together",
+    ),
+    ("RegisterDiscoveredServerInput", "_validate_package", "package"): (
+        lambda s: {"package": "-" + s},
+        "$.package: package must be a valid npm/pypi identifier",
+    ),
+}
+
+
+def _validators(model: type[BaseModel]) -> list[tuple[str, str, str]]:
+    decorators = model.__pydantic_decorators__
+    found = [
+        (model.__name__, name, field)
+        for name, decorator in decorators.field_validators.items()
+        for field in decorator.info.fields
+    ]
+    found += [(model.__name__, name, "") for name in decorators.model_validators]
+    return found
+
+
+class _Case(typing.NamedTuple):
+    tool: str
+    label: str
+    build: Any  # sentinel -> arguments
+    layer: str  # "gate" or "model"
+    expected: str  # the rendered location (and phrase) the rejection must name
+
+
+def _cases() -> list[_Case]:
+    tools, models = _tools(), _models()
+    cases: list[_Case] = []
+    discovered: set[tuple[str, str, str]] = set()
+    for name in sorted(tools):
+        tool = tools[name]
+        for path, node in _positions(tool.input_schema):
+            # No silent shrink: every typed position yields a case.
+            assert not _types(node) or _invalid_values(node, _SENTINELS[0]), (
+                name,
+                path,
+            )
+            for label, _ in _invalid_values(node, _SENTINELS[0]):
+
+                def build(s: str, tool=tool, path=path, node=node, label=label) -> dict:
+                    arguments = _decorated(tool, s)
+                    value = dict(_invalid_values(node, s))[label]
+                    _set(arguments, path, value, s)
+                    return arguments
+
+                cases.append(
+                    _Case(
+                        name,
+                        f"{_expected_path(path)}:{label}",
+                        build,
+                        "gate",
+                        _expected_path(path) + ": ",
+                    )
+                )
+        # A missing required key, beside the caller's own extra keys: the
+        # description must name the schema's key, never one of the caller's.
+        assert not [p for p, n in _positions(tool.input_schema) if n.get("required")], (
+            "a nested `required` needs this axis extended",
+            name,
+        )
+        for required in tool.input_schema.get("required") or []:
+
+            def build(s: str, tool=tool, required=required) -> dict:
+                arguments = _decorated(tool, s)
+                del arguments[required]
+                return arguments
+
+            cases.append(
+                _Case(
+                    name,
+                    f"required:{required}",
+                    build,
+                    "gate",
+                    f"$.{required}: is required",
+                )
+            )
+        for model in _reachable_models(models[name]):
+            for key in _validators(model):
+                discovered.add(key)
+                recipe, expected = _VALIDATOR_RECIPES[key]
+
+                def build(s: str, tool=tool, recipe=recipe) -> dict:
+                    return {**_decorated(tool, s), **recipe(s)}
+
+                cases.append(_Case(name, f"validator:{key}", build, "model", expected))
+            if model.model_config.get("populate_by_name") and model is models[name]:
+                for field_name, field in model.model_fields.items():
+                    if not isinstance(field.alias, str) or field.alias == field_name:
+                        continue
+                    node = tool.input_schema["properties"][field.alias]
+                    for label, _ in _invalid_values(node, _SENTINELS[0]):
+
+                        def build(
+                            s: str,
+                            tool=tool,
+                            node=node,
+                            label=label,
+                            field_name=field_name,
+                            alias=field.alias,
+                        ) -> dict:
+                            arguments = _decorated(tool, s)
+                            # By name only: with the alias present too,
+                            # pydantic reads the alias.
+                            assert arguments.pop(alias, None) is not None
+                            arguments[field_name] = dict(_invalid_values(node, s))[
+                                label
+                            ]
+                            return arguments
+
+                        cases.append(
+                            _Case(
+                                name,
+                                f"by-name:{field_name}:{label}",
+                                build,
+                                "model",
+                                f"$.{field_name}: ",
+                            )
+                        )
+    assert discovered == set(_VALIDATOR_RECIPES), (
+        "every custom validator on an argument model needs a recipe, and "
+        "every recipe a validator",
+        discovered ^ set(_VALIDATOR_RECIPES),
+    )
+    return cases
+
+
+_CASES = _cases()
+
+
+def test_the_sweep_covers_every_tool_that_can_reject_an_argument() -> None:
+    """No vacuous pass: every registered tool that declares an argument
+    contributes cases, and the ones that do not declare none at all."""
+    tools = _tools()
+    covered = {case.tool for case in _CASES}
+    for name, tool in tools.items():
+        if tool.input_schema.get("properties"):
+            assert name in covered, name
+        else:
+            assert name not in covered, name
+    assert {case.layer for case in _CASES} == {"gate", "model"}
+    assert len(_CASES) > 100, len(_CASES)
+
+
+# --- running a case --------------------------------------------------------------
+
+
+def _server(tmp_path: Path, *, audited: bool) -> tuple[GatewayServer, Path | None]:
+    audit_path = tmp_path / "audit.jsonl" if audited else None
+    if audited:
+        server = GatewayServer(
+            policy_path=_write_scoped_policy(tmp_path / "policy.json"),
+            audit_jsonl=audit_path,
+            cache_dir=tmp_path / "cache",
+        )
+    else:
+        policy = tmp_path / "policy.json"
+        policy.write_text("{}")
+        server = GatewayServer(policy_path=policy, cache_dir=tmp_path / "cache")
+    server._create_server()
+    # Every registered tool reaches its real handler.
+    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
+        lambda name: True
+    )
+    return server, audit_path
+
+
+async def _call(server: GatewayServer, name: str, arguments: dict) -> Any:
+    assert server._server is not None
+    entry = server._server.get_request_handler("tools/call")
+    assert entry is not None
+    return await entry.handler(
+        _make_ctx(), CallToolRequestParams(name=name, arguments=arguments)
+    )
+
+
+def _rejection(result: Any, layer: str) -> str:
+    """The rejection text, asserting the call was rejected by argument
+    validation on `layer` (an `isError` gate result, or the `{"error": true}`
+    payload of `call_tool`'s `except` arm) and not, say, accepted."""
+    text = "".join(block.text for block in result.content)
+    if layer == "gate":
+        assert result.is_error is True, text
+        return text
+    payload = json.loads(text)
+    assert payload.get("error") is True, text
+    return str(payload["message"])
+
+
+def _useful(response: str, layer: str, expected: str) -> None:
+    """The rejection says where, and why, in the value-free form."""
+    prefix = _GATE_PREFIX if layer == "gate" else _MODEL_PREFIX
+    assert response.startswith(prefix), response
+    assert expected in response, response
+
+
+class _Observed(typing.NamedTuple):
+    response: str
+    log: str
+    raw_log: str
+    audit: list[dict[str, Any]]
+    raw_audit: str
+
+
+async def _observe(
+    server: GatewayServer,
+    audit_path: Path | None,
+    caplog: pytest.LogCaptureFixture,
+    case: _Case,
+    sentinel: str,
+) -> _Observed:
+    arguments = case.build(sentinel)
+    before_log = len(caplog.records)
+    before_audit = audit_path.read_text() if audit_path and audit_path.exists() else ""
+    result = await _call(server, case.tool, arguments)
+    records = caplog.records[before_log:]
+    raw_log = "\n".join(
+        formatter.format(record)
+        for record in records
+        for formatter in _pmcp_formatters()
+    )
+    log = "\n".join(
+        _normalized(record, formatter)
+        for record in records
+        for formatter in _pmcp_formatters()
+    )
+    raw_audit = (audit_path.read_text() if audit_path else "")[len(before_audit) :]
+    audit = [_stable(json.loads(line)) for line in raw_audit.splitlines() if line]
+    return _Observed(
+        response=_rejection(result, case.layer),
+        log=log,
+        raw_log=raw_log,
+        audit=audit,
+        raw_audit=raw_audit,
+    )
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("audited", [False, True], ids=["plain", "scoped-audit"])
+async def test_no_rejected_argument_value_reaches_a_response_log_or_audit(
+    tmp_path: Path, caplog: pytest.LogCaptureFixture, audited: bool
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    server, audit_path = _server(tmp_path, audited=audited)
+    for case in _CASES:
+        seen = [
+            await _observe(server, audit_path, caplog, case, sentinel)
+            for sentinel in _SENTINELS
+        ]
+        for sentinel, observed in zip(_SENTINELS, seen):
+            for form in _forbidden(sentinel):
+                assert form not in observed.response, (case.label, "response")
+                assert form not in observed.raw_log, (
+                    case.label,
+                    "log",
+                    observed.raw_log,
+                )
+                assert form not in observed.raw_audit, (case.label, "audit")
+        # Useful: the rejection names where, and why.
+        _useful(seen[0].response, case.layer, case.expected)
+        # Nothing else about the value -- length, count or hash -- either.
+        assert seen[0].response == seen[1].response, case.label
+        assert seen[0].log == seen[1].log, (case.label, seen[0].log, seen[1].log)
+        assert seen[0].audit == seen[1].audit, case.label
+    # The audit oracle is live, not empty by accident.
+    if audit_path is not None:
+        assert '"audit.rejection"' in audit_path.read_text()
+    assert caplog.records, "nothing was logged, so the log oracle proves nothing"
+
+
+# --- every handler, called past the gate ------------------------------------------
+
+
+@pytest.mark.asyncio
+async def test_every_handler_rejects_what_the_gate_rejects_without_logging_it(
+    tmp_path: Path, caplog: pytest.LogCaptureFixture
+) -> None:
+    """In-process callers reach a handler without the gate. Each handler must
+    then raise the model's `ValidationError` (which the server describes)
+    rather than catch it and log or render its text -- the shape
+    `provision_status` had, with a traceback, before Consiliency/pmcp#297."""
+    caplog.set_level(logging.DEBUG)
+    server, _ = _server(tmp_path, audited=False)
+    tools = server._gateway_tools
+    checked = 0
+    for case in _CASES:
+        if case.layer != "gate":
+            continue
+        handler = getattr(tools, case.tool.removeprefix("gateway."))
+        for s in _SENTINELS:
+            start = len(caplog.records)
+            raised: BaseException | None = None
+            try:
+                await handler(case.build(s))
+            except Exception as error:  # noqa: BLE001 - inspected below
+                raised = error
+            logged = "\n".join(
+                formatter.format(record)
+                for record in caplog.records[start:]
+                for formatter in _pmcp_formatters()
+            )
+            for form in _forbidden(s):
+                assert form not in logged, (case.label, logged)
+            assert isinstance(raised, ValidationError), (case.label, raised)
+        checked += 1
+    assert checked > 100, checked
+
+
+# --- validation errors raised inside a handler --------------------------------------
+
+
+def _handler_validation_errors(s: str) -> list[BaseException]:
+    """One of each validation-error type a handler can raise, built from a
+    value carrying `s`: a pydantic error from a downstream-data model, a
+    pydantic error from an argument model, and a jsonschema error."""
+    errors: list[BaseException] = []
+    for model, data in (
+        (McpTaskInfo, {"task_id": {s: s}, "created_at": [s]}),
+        (InvokeInput, {"tool_id": {s: s}, "options": s, "_meta": [s]}),
+    ):
+        try:
+            model.model_validate(data)
+        except ValidationError as error:
+            errors.append(error)
+    try:
+        jsonschema.validate({"x": {s: s}}, {"properties": {"x": {"type": "string"}}})
+    except jsonschema.ValidationError as error:
+        errors.append(error)
+    assert len(errors) == 3
+    for error in errors:
+        assert s in str(error)  # the raw text does carry it
+    return errors
+
+
+class _RaisingTools:
+    def __init__(self, error: BaseException) -> None:
+        self.error = error
+
+    def __getattr__(self, name: str) -> Any:
+        async def handler(*args: Any, **kwargs: Any) -> dict:
+            raise self.error
+
+        return handler
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize(
+    "index", [0, 1, 2], ids=["downstream-model", "argument-model", "jsonschema"]
+)
+async def test_a_validation_error_raised_by_a_handler_is_described_not_echoed(
+    tmp_path: Path, caplog: pytest.LogCaptureFixture, index: int
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    server, _ = _server(tmp_path, audited=False)
+    tool = _tools()["gateway.catalog_search"]
+    texts, logs = [], []
+    for s in _SENTINELS:
+        server._gateway_tools = _RaisingTools(_handler_validation_errors(s)[index])  # type: ignore[assignment]
+        start = len(caplog.records)
+        result = await _call(server, tool.name, {"query": "q"})
+        text = "".join(block.text for block in result.content)
+        payload = json.loads(text)
+        assert payload["error"] is True, text
+        log = "\n".join(
+            formatter.format(record)
+            for record in caplog.records[start:]
+            for formatter in _pmcp_formatters()
+        )
+        for form in _forbidden(s):
+            assert form not in text and form not in log, form
+        # Not catalog_search's own argument model: a validation error, not
+        # "invalid arguments".
+        assert payload["message"].startswith("Validation error: $"), text
+        assert "validation error for gateway.catalog_search: $" in log
+        texts.append(text)
+        logs.append(
+            "\n".join(
+                _normalized(r, f)
+                for r in caplog.records[start:]
+                for f in _pmcp_formatters()
+            )
+        )
+    assert texts[0] == texts[1] and logs[0] == logs[1]
+
+
+# --- the renderers, directly ---------------------------------------------------------
+
+
+class _PoisonedSchemaError(jsonschema.ValidationError):
+    """Every attribute that can carry the rejected value raises on access."""
+
+    def _poisoned(self: Any) -> Any:
+        raise AssertionError("the renderer read a value-bearing error attribute")
+
+    message = property(_poisoned)  # type: ignore[assignment]
+    instance = property(_poisoned)  # type: ignore[assignment]
+    validator_value = property(_poisoned)  # type: ignore[assignment]
+    context = property(_poisoned)  # type: ignore[assignment]
+    cause = property(_poisoned)  # type: ignore[assignment]
+    schema = property(_poisoned)  # type: ignore[assignment]
+
+
+@pytest.mark.parametrize(
+    ("arguments", "expected"),
+    [
+        # The issue's example, verbatim shape.
+        (
+            {"tool_id": "a::b", "options": "Bearer sk-SECRET-VALUE"},
+            "$.options: must be of type object or null",
+        ),
+        (
+            {"tool_id": "a::b", "evidence_label_digest": "sk-" * 30},
+            "$.evidence_label_digest: must be at most 64 characters",
+        ),
+        (
+            {"tool_id": "a::b", "evidence_label_digest": "sk-SECRET".ljust(64, "!")},
+            "$.evidence_label_digest: must match the pattern ^[0-9a-f]{64}$",
+        ),
+        (
+            {"tool_id": "a::b", "task": {"ttl": "sk-SECRET"}},
+            "$.task.ttl: must be of type integer or null",
+        ),
+        ({"arguments": {"sk-SECRET": 1}}, "$.tool_id: is required"),
+    ],
+)
+def test_a_schema_rejection_names_the_field_and_the_reason_only(
+    arguments: dict, expected: str
+) -> None:
+    from pmcp.argument_errors import describe_schema_error
+
+    schema = _tools()["gateway.invoke"].input_schema
+    error = jsonschema.exceptions.best_match(
+        jsonschema.validators.validator_for(schema)(schema).iter_errors(arguments)
+    )
+    assert error is not None
+    error.__class__ = _PoisonedSchemaError
+    described = describe_schema_error(error, schema, arguments)
+    assert described == expected
+    assert "sk-" not in described and "SECRET" not in described
+
+
+def test_a_caller_chosen_key_in_a_path_is_redacted() -> None:
+    from pmcp.argument_errors import describe_schema_error
+
+    schema = {
+        "type": "object",
+        "properties": {
+            "env": {"type": "object", "additionalProperties": {"type": "integer"}},
+            "names": {"type": "array", "items": {"type": "string"}},
+        },
+    }
+    for arguments, expected in (
+        ({"env": {"sk-KEY-SECRET": "v"}}, "$.env.*: must be of type integer"),
+        ({"names": ["ok", {"sk-SECRET": 1}]}, "$.names[1]: must be of type string"),
+    ):
+        error = jsonschema.exceptions.best_match(
+            jsonschema.validators.validator_for(schema)(schema).iter_errors(arguments)
+        )
+        assert error is not None
+        assert describe_schema_error(error, schema, arguments) == expected
+    closed = {"type": "object", "properties": {"a": {}}, "additionalProperties": False}
+    error = jsonschema.exceptions.best_match(
+        jsonschema.validators.validator_for(closed)(closed).iter_errors({"sk-KEY": 1})
+    )
+    assert error is not None
+    assert (
+        describe_schema_error(error, closed, {"sk-KEY": 1})
+        == "$: has a property that is not accepted"
+    )
+
+
+def test_a_model_rejection_names_the_field_and_the_reason_only() -> None:
+    from pmcp.argument_errors import describe_model_error
+
+    schema = _tools()["gateway.invoke"].input_schema
+    secret = "sk-" + "S" * 77  # the issue's 80-character secret
+    cases = [
+        (
+            {"tool_id": "a::b", "run_correlation_id": secret + "!"},
+            "$.run_correlation_id: correlation IDs may contain only alphanumerics and ._:-",
+        ),
+        (
+            {"tool_id": "a::b", "run_correlation_id": "r", "arguments": {"q": secret}},
+            "$: scoped advisor correlation fields must be supplied together",
+        ),
+        ({"tool_id": "a::b", "meta": secret}, "$.meta: must be an object"),
+        (
+            {
+                "tool_id": "a::b",
+                "_meta": {secret: 1},
+                "options": {"timeout_ms": secret},
+            },
+            "$.options.timeout_ms: must be an integer",
+        ),
+    ]
+    for arguments, expected in cases:
+        with pytest.raises(ValidationError) as raised:
+            InvokeInput.model_validate(arguments)
+        assert secret[-21:] in str(raised.value) or secret in str(raised.value)
+        described = describe_model_error(raised.value, schema, arguments)
+        assert described == expected
+        assert "sk-" not in described and "SSS" not in described
+
+
+def test_a_dict_key_in_a_model_location_is_redacted() -> None:
+    from pmcp.argument_errors import describe_model_error
+
+    class _Keyed(BaseModel):
+        env: dict[str, int]
+
+    with pytest.raises(ValidationError) as raised:
+        _Keyed.model_validate({"env": {"sk-KEY-SECRET": "x"}})
+    assert "sk-KEY-SECRET" in str(raised.value)
+    described = describe_model_error(
+        raised.value, {"properties": {"env": {}}}, {"env": {"sk-KEY-SECRET": "x"}}
+    )
+    assert described == "$.env.*: must be an integer"
+
+
+def test_a_model_phrase_reads_only_constraint_context() -> None:
+    """Every placeholder a phrase names is a constraint the model declares;
+    none is a value-derived `ctx` entry pydantic also offers."""
+    from pmcp.argument_errors import _CONSTRAINT_CONTEXT, _MODEL_PHRASES
+
+    placeholders = {
+        name
+        for phrase in _MODEL_PHRASES.values()
+        for _, name, _, _ in string.Formatter().parse(phrase)
+        if name
+    }
+    assert placeholders <= _CONSTRAINT_CONTEXT
+    assert _CONSTRAINT_CONTEXT == {
+        "min_length",
+        "max_length",
+        "pattern",
+        "expected",
+        "gt",
+        "ge",
+        "lt",
+        "le",
+    }
+    # Offered by pydantic, derived from the input: never read.
+    for derived in (
+        "actual_length",
+        "error",
+        "tag",
+        "input",
+        "attribute",
+        "class_name",
+    ):
+        assert derived not in _CONSTRAINT_CONTEXT
````

### Patch — `test_scoped_advisor_audit` (`tests/test_scoped_advisor_audit.py`)

````diff
diff --git a/tests/test_scoped_advisor_audit.py b/tests/test_scoped_advisor_audit.py
index 138e06a..d2133be 100644
--- a/tests/test_scoped_advisor_audit.py
+++ b/tests/test_scoped_advisor_audit.py
@@ -1358,26 +1358,17 @@ _MARKERS = (
 #: The index of the one generated value that sits in a declared key the gate
 #: rejects (see `_invalid_declared`).
 _INVALID_INDEX = 99
-#: The pre-existing log lines this plan does not own, matched as whole
-#: messages so that one carrying anything more stays in the oracle. Both come
-#: from `call_tool`'s `except Exception`, `f"Tool execution error: {e}"`:
-#: - pydantic's `InvokeInput` error for a non-dict `meta` (it reaches the
-#:   model by alias), which echoes the value -- on main too; the channel
-#:   Consiliency/pmcp#297 names. It is the only `InvokeInput` error the sweep
-#:   produces (measured: 216 lines, all this shape);
-#: - `Unknown tool: <name>`, excluded only for the exact name of the call
-#:   that produced it (see `_LoggedCalls`); unreachable under the scoped
-#:   policy, whose allowlist is four registered names.
-_FOREIGN_INVOKE_INPUT = re.compile(
-    r"Tool execution error: 1 validation error for InvokeInput\n"
-    r"meta\n"
-    r"  Input should be a valid dictionary "
-    r"\[type=dict_type, input_value=[^\n]*, input_type=[A-Za-z_]+\]\n"
-    r"    For further information visit https://errors\.pydantic\.dev/[0-9.]+/v/dict_type"
-)
-#: Only for `gateway.invoke` may a generated key change the status and so the
-#: result digest: an undeclared `meta` reaches `InvokeInput` by alias.
-_INVOKE_ONLY_EXEMPT = frozenset({"terminal_status", "redacted_result_digest"})
+#: An undeclared `meta` reaches `InvokeInput` by name (`populate_by_name`)
+#: and, when it is not a dict, fails it: since Consiliency/pmcp#297 that call
+#: is an `audit.rejection` naming only the tool and `["meta"]`, so a
+#: generated key no longer changes any field of an invocation record.
+_META_REJECTION = {
+    "event": "audit.rejection",
+    "gateway_tool": "gateway.invoke",
+    "terminal_status": "invalid_arguments",
+    "rejected_argument_path": ["meta"],
+    "rejected_argument_validator": None,
+}
 
 
 def _gateway_tools_by_name() -> dict[str, Any]:
@@ -1586,6 +1577,27 @@ def _invocations(audit_path: Path, event: str) -> list[dict[str, Any]]:
     ]
 
 
+def _call_records(audit_path: Path) -> list[dict[str, Any]]:
+    """One record per call: its invocation, or its argument rejection."""
+    return [
+        _stable(r)
+        for r in validate_scoped_advisor_audit(audit_path)
+        if r["event"] in ("audit.invocation", "audit.rejection")
+    ]
+
+
+def _assert_against(
+    tool_name: str, label: str, record: dict, reference: dict, exempt: frozenset
+) -> None:
+    """`record` equals the declared-arguments `reference` bar `exempt`, unless
+    it is the one rejection a generated key can cause (`_META_REJECTION`)."""
+    if record["event"] == "audit.rejection":
+        assert tool_name == "gateway.invoke", (tool_name, label)
+        assert {k: record.get(k) for k in _META_REJECTION} == _META_REJECTION, label
+        return
+    _assert_pair(tool_name, label, record, reference, exempt)
+
+
 def _assert_nothing_generated_leaked(
     raw_audit: str, log_text: str, count: int, *, url_hash: bool = True
 ) -> None:
@@ -1599,14 +1611,6 @@ def _assert_nothing_generated_leaked(
             assert forbidden not in log_text, ("log", forbidden)
 
 
-def _is_foreign(message: str, name: str | None = None) -> bool:
-    """Whether `message` is exactly one of the lines above -- the whole of it.
-    `name` is the tool name of the call that logged it, if known."""
-    if _FOREIGN_INVOKE_INPUT.fullmatch(message):
-        return True
-    return name is not None and message == f"Tool execution error: Unknown tool: {name}"
-
-
 _PMCP_FORMATTERS: list[logging.Formatter] = []
 
 
@@ -1631,43 +1635,20 @@ def _log_text(
     caplog: pytest.LogCaptureFixture, calls: _LoggedCalls | None = None
 ) -> str:
     """Every log record as pmcp's formatters render it -- message, and any
-    `exc_info` traceback -- bar the exact foreign lines above."""
+    `exc_info` traceback. Nothing is excluded: the two pre-existing echoes
+    the sweep used to skip (a non-dict `meta`, an unknown tool's name) are
+    fixed by Consiliency/pmcp#297."""
     # The capture is live (DEBUG, root), so an empty result is not vacuous
     # by accident: the gateway logs on start-up and shutdown.
     assert caplog.records, "nothing was logged, so the log oracle proves nothing"
-    excluded = calls.foreign if calls is not None else set()
+    del calls  # every call's log is in `caplog`
     return "\n".join(
-        formatter.format(shown)
+        formatter.format(record)
         for record in caplog.records
-        if (shown := _oracle_view(record, excluded)) is not None
         for formatter in _pmcp_formatters()
     )
 
 
-#: What stands in for an excluded message when its record carries diagnostics.
-_EXCLUDED_MESSAGE = "<excluded pre-existing line>"
-
-
-def _has_diagnostics(record: logging.LogRecord) -> bool:
-    return bool(record.exc_info or record.exc_text or record.stack_info)
-
-
-def _oracle_view(
-    record: logging.LogRecord, excluded: set[int]
-) -> logging.LogRecord | None:
-    """The record as the log oracle sees it. An excluded line (one of the
-    exact foreign messages above) is dropped only when it carries no
-    traceback, exception text or stack; otherwise only its message is
-    replaced, and the rendered diagnostics stay in the oracle."""
-    if id(record) not in excluded and not _is_foreign(record.getMessage()):
-        return record
-    if not _has_diagnostics(record):
-        return None
-    shown = logging.makeLogRecord(record.__dict__)
-    shown.msg, shown.args = _EXCLUDED_MESSAGE, None
-    return shown
-
-
 #: A default object repr, the one volatile part a message can carry besides
 #: the time (none measured in the sweep's logs; kept exact, never a bare hex).
 _OBJECT_REPR = re.compile(r"(<[A-Za-z_][\w.]* object) at 0x[0-9a-f]+>")
@@ -1684,11 +1665,10 @@ def _normalized(record: logging.LogRecord, formatter: logging.Formatter) -> str:
     return _OBJECT_REPR.sub(r"\1 at ADDRESS>", formatter.format(fixed))
 
 
-def _normalized_log(records: list[logging.LogRecord], excluded: set[int]) -> str:
+def _normalized_log(records: list[logging.LogRecord]) -> str:
     return "\n".join(
-        _normalized(shown, formatter)
+        _normalized(record, formatter)
         for record in records
-        if (shown := _oracle_view(record, excluded)) is not None
         for formatter in _pmcp_formatters()
     )
 
@@ -1702,22 +1682,13 @@ class _LoggedCalls:
     def __init__(self, server: GatewayServer, caplog: pytest.LogCaptureFixture) -> None:
         self.server = server
         self.caplog = caplog
-        #: Records that are exactly `Tool execution error: Unknown tool: <name>`
-        #: for the name of the call that logged them.
-        self.foreign: set[int] = set()
 
     async def pair(self, label: str, calls: list[tuple[str, dict]]) -> list[Any]:
         logs, results = [], []
         for name, arguments in calls:
             start = len(self.caplog.records)
             results.append(await _call(self.server, name, arguments))
-            produced = self.caplog.records[start:]
-            self.foreign |= {
-                id(record)
-                for record in produced
-                if _is_foreign(record.getMessage(), name)
-            }
-            logs.append(_normalized_log(produced, self.foreign))
+            logs.append(_normalized_log(self.caplog.records[start:]))
         assert all(log == logs[0] for log in logs), (label, logs)
         return results
 
@@ -1758,7 +1729,7 @@ async def test_generated_caller_values_never_reach_an_allowed_record(
     tool = _gateway_tools_by_name()[tool_name]
     declared = set(tool.input_schema.get("properties") or {})
     baseline = _declared_baseline(tool)
-    exempt = _INVOKE_ONLY_EXEMPT if tool_name == "gateway.invoke" else frozenset()
+    exempt: frozenset[str] = frozenset()
     server, audit_path, real_tools = _allowed_server(tmp_path)
     stub = server._gateway_tools
     calls = _LoggedCalls(server, caplog)
@@ -1822,15 +1793,15 @@ async def test_generated_caller_values_never_reach_an_allowed_record(
     server._gateway_tools = real_tools
     await server.shutdown()
 
-    records = _invocations(audit_path, "audit.invocation")
+    records = _call_records(audit_path)
     ok_count = 1 + len(_TAGS) * len(cases)
     ok, raised = records[:ok_count], records[ok_count:]
     reference, generated = ok[0], ok[1:]
     for (label, case_exempt, _), first, second in zip(
         cases, generated[::2], generated[1::2]
     ):
-        _assert_pair(tool_name, label, first, second, case_exempt - _INVOKE_ONLY_EXEMPT)
-        _assert_pair(tool_name, label, first, reference, case_exempt)
+        _assert_pair(tool_name, label, first, second, case_exempt)
+        _assert_against(tool_name, label, first, reference, case_exempt)
     errors = records_per_case[1:]
     assert len(raised) == 3 * len(errors)
     for error, (reference, first, second) in zip(
@@ -1838,7 +1809,7 @@ async def test_generated_caller_values_never_reach_an_allowed_record(
     ):
         label = f"raises {error!r}"
         _assert_pair(tool_name, label, first, second, frozenset())
-        _assert_pair(tool_name, label, first, reference, exempt)
+        _assert_against(tool_name, label, first, reference, exempt)
     _assert_nothing_generated_leaked(
         audit_path.read_text(),
         _log_text(caplog, calls),
@@ -1876,15 +1847,13 @@ async def test_generated_caller_values_never_reach_an_uncorrelated_invoke_record
         )
     await server.shutdown()
 
-    records = _invocations(audit_path, "audit.invocation")
+    records = _call_records(audit_path)
     assert len(records) == 1 + len(_TAGS) * len(_SHAPES)
     reference, generated = records[0], records[1:]
     assert reference["terminal_status"] == "failure"
     for shape, first, second in zip(_SHAPES, generated[::2], generated[1::2]):
         _assert_pair("gateway.invoke", f"E6 {shape}", first, second, frozenset())
-        _assert_pair(
-            "gateway.invoke", f"E6 {shape}", first, reference, _INVOKE_ONLY_EXEMPT
-        )
+        _assert_against("gateway.invoke", f"E6 {shape}", first, reference, frozenset())
     _assert_nothing_generated_leaked(
         audit_path.read_text(),
         _log_text(caplog, calls),
@@ -2028,7 +1997,7 @@ async def test_generated_caller_values_on_the_real_scoped_handlers(
     tool = _gateway_tools_by_name()[tool_name]
     declared = set(tool.input_schema.get("properties") or {})
     baseline = _declared_baseline(tool)
-    exempt = _INVOKE_ONLY_EXEMPT if tool_name == "gateway.invoke" else frozenset()
+    exempt: frozenset[str] = frozenset()
     server, audit_path = _scoped_server(tmp_path)
     calls = _LoggedCalls(server, caplog)
     await _call(server, tool_name, baseline)
@@ -2048,12 +2017,12 @@ async def test_generated_caller_values_on_the_real_scoped_handlers(
         )
     await server.shutdown()
 
-    records = _invocations(audit_path, "audit.invocation")
+    records = _call_records(audit_path)
     assert len(records) == 1 + len(_TAGS) * len(_SHAPES)
     reference, generated = records[0], records[1:]
     for shape, first, second in zip(_SHAPES, generated[::2], generated[1::2]):
         _assert_pair(tool_name, f"real {shape}", first, second, frozenset())
-        _assert_pair(tool_name, f"real {shape}", first, reference, exempt)
+        _assert_against(tool_name, f"real {shape}", first, reference, exempt)
     _assert_nothing_generated_leaked(
         audit_path.read_text(),
         _log_text(caplog, calls),
@@ -2073,109 +2042,55 @@ def test_the_log_normalisation_keeps_a_hex_count_and_drops_only_the_time() -> No
     """Regression (PR 306 board): a caller-derived count logged in hex must
     survive normalisation; only the time and a default object repr's address
     are volatile."""
-    count_a = _normalized_log([_log_record("argument_count=0x28", 1.0)], set())
-    count_b = _normalized_log([_log_record("argument_count=0x2e", 2.0)], set())
+    count_a = _normalized_log([_log_record("argument_count=0x28", 1.0)])
+    count_b = _normalized_log([_log_record("argument_count=0x2e", 2.0)])
     assert count_a != count_b
     assert "0x28" in count_a and "0x2e" in count_b
-    same_a = _normalized_log([_log_record("started", 1.0)], set())
-    same_b = _normalized_log([_log_record("started", 1_000_000.5)], set())
+    same_a = _normalized_log([_log_record("started", 1.0)])
+    same_b = _normalized_log([_log_record("started", 1_000_000.5)])
     assert same_a == same_b
-    repr_a = _normalized_log([_log_record("<a.B object at 0x7f00aa>", 1.0)], set())
-    repr_b = _normalized_log([_log_record("<a.B object at 0x7f00bb>", 1.0)], set())
+    repr_a = _normalized_log([_log_record("<a.B object at 0x7f00aa>", 1.0)])
+    repr_b = _normalized_log([_log_record("<a.B object at 0x7f00bb>", 1.0)])
     assert repr_a == repr_b
     # A hex span that is not an object repr's address is kept.
-    assert _normalized_log(
-        [_log_record("fp 0x7f00aa>", 1.0)], set()
-    ) != _normalized_log([_log_record("fp 0x7f00bb>", 1.0)], set())
-
-
-def test_the_foreign_log_exclusion_matches_only_the_exact_lines() -> None:
-    """Regression (PR 306 board): a line that starts like a foreign one but
-    carries anything more stays in the oracle."""
-    with pytest.raises(ValidationError) as raised:
-        InvokeInput.model_validate({"tool_id": "a::b", "meta": "caller_marker_x"})
-    invoke_input = f"Tool execution error: {raised.value}"
-    assert _is_foreign(invoke_input)
-    assert not _is_foreign(invoke_input + " (3 args)")
-    assert not _is_foreign(invoke_input.replace("meta\n", "meta\npayload=x\n", 1))
-    unknown = "Tool execution error: Unknown tool: gateway.caller_name"
-    assert _is_foreign(unknown, "gateway.caller_name")
-    assert not _is_foreign(unknown)
-    assert not _is_foreign(unknown, "gateway.other")
-    payload = "Tool execution error: Unknown tool: payload={'k': 'caller_marker_x'}"
-    assert not _is_foreign(payload)
-    assert not _is_foreign(payload, "gateway.caller_name")
-
-
-class _Records:
-    """Stands in for `caplog` where `_log_text` needs only `.records`."""
-
-    def __init__(self, records: list[logging.LogRecord]) -> None:
-        self.records = records
-
-
-def _excluded_record(message: str, marker: str, diagnostics: str) -> logging.LogRecord:
-    record = logging.LogRecord(
-        "pmcp.server", logging.ERROR, __file__, 1, message, None, None
+    assert _normalized_log([_log_record("fp 0x7f00aa>", 1.0)]) != _normalized_log(
+        [_log_record("fp 0x7f00bb>", 1.0)]
     )
-    if diagnostics == "exc_info":
-        try:
-            raise ValueError(marker)
-        except ValueError:
-            record.exc_info = sys.exc_info()
-    elif diagnostics == "exc_text":
-        record.exc_text = f"Traceback (most recent call last):\nValueError: {marker}"
-    else:
-        record.stack_info = f"Stack (most recent call last):\n  {marker}"
-    return record
-
 
-def _invoke_input_line() -> str:
-    with pytest.raises(ValidationError) as raised:
-        InvokeInput.model_validate({"tool_id": "a::b", "meta": "not-a-dict"})
-    return f"Tool execution error: {raised.value}"
 
-
-@pytest.mark.parametrize("diagnostics", ["exc_info", "exc_text", "stack_info"])
-@pytest.mark.parametrize("shape", ["invoke-input", "unknown-tool"])
-def test_an_excluded_line_keeps_its_traceback_and_stack_in_the_oracle(
-    shape: str, diagnostics: str
+@pytest.mark.asyncio
+async def test_the_formerly_excluded_log_echoes_are_gone(
+    tmp_path: Path, caplog: pytest.LogCaptureFixture
 ) -> None:
-    """Regression (PR 306 board, round 2): an exactly-excluded message may
-    hide only itself. A traceback, exception text or stack attached to it is
-    rendered into the oracle, so a caller value there is caught by both the
-    forbidden-set check and the pair differential."""
-    name = "gateway.caller_name"
-    message = (
-        _invoke_input_line()
-        if shape == "invoke-input"
-        else f"Tool execution error: Unknown tool: {name}"
-    )
-    assert _is_foreign(message, name)
-    records = {
-        tag: _excluded_record(message, f"caller_marker_{tag}", diagnostics)
-        for tag in _TAGS
-    }
-    excluded = (
-        {id(record) for record in records.values()}
-        if shape == "unknown-tool"
-        else set()
+    """The sweep's log oracle used to skip two exact lines from `call_tool`'s
+    `except Exception`: pydantic's `InvokeInput` error for a non-dict `meta`
+    (value included) and `Unknown tool: <name>`. Consiliency/pmcp#297 fixes
+    both, so nothing is excluded; this pins the fixed lines."""
+    caplog.set_level(logging.DEBUG)
+    server, audit_path = _scoped_server(tmp_path)
+    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
+        lambda name: True
     )
-    normalized = {
-        tag: _normalized_log([record], excluded) for tag, record in records.items()
-    }
-    for tag in _TAGS:
-        assert f"caller_marker_{tag}" in normalized[tag], (shape, diagnostics)
-        assert _EXCLUDED_MESSAGE in normalized[tag]
-        assert message.splitlines()[0] not in normalized[tag]
-    assert normalized[_TAGS[0]] != normalized[_TAGS[1]]
-    calls = _LoggedCalls.__new__(_LoggedCalls)
-    calls.foreign = excluded
-    text = _log_text(_Records(list(records.values())), calls)  # type: ignore[arg-type]
-    with pytest.raises(AssertionError):
-        _assert_nothing_generated_leaked("", text, 1)
-    # Without diagnostics the excluded line is dropped entirely.
-    bare = logging.LogRecord(
-        "pmcp.server", logging.ERROR, __file__, 1, message, None, None
+    await _call(
+        server,
+        "gateway.invoke",
+        {
+            "tool_id": "firecrawl::search",
+            "meta": "caller_marker_meta",
+            **_correlations(),
+        },
     )
-    assert _normalized_log([bare], excluded | {id(bare)}) == ""
+    await _call(server, "gateway.caller_marker_name", {})
+    await server.shutdown()
+    text = _log_text(caplog)
+    assert "caller_marker" not in text, text
+    assert (
+        "Tool execution error: invalid arguments for gateway.invoke: "
+        "$.meta: must be an object"
+    ) in text
+    assert "Tool execution error: unknown gateway tool" in text
+    rejections = _invocations(audit_path, "audit.rejection")
+    assert [{k: r.get(k) for k in _META_REJECTION} for r in rejections] == [
+        _META_REJECTION
+    ]
+    assert "caller_marker" not in audit_path.read_text()
````

### Patch — `test_gateway_tool_schemas` (`tests/test_gateway_tool_schemas.py`)

````diff
diff --git a/tests/test_gateway_tool_schemas.py b/tests/test_gateway_tool_schemas.py
index e6ddeb9..94456d8 100644
--- a/tests/test_gateway_tool_schemas.py
+++ b/tests/test_gateway_tool_schemas.py
@@ -435,16 +435,20 @@ async def _call_through_gate(name: str, arguments: dict[str, Any]) -> Any:
 @pytest.mark.parametrize(
     ("name", "arguments", "fragment"),
     [
-        ("gateway.describe", {"tool_id": ""}, "should be non-empty"),
+        (
+            "gateway.describe",
+            {"tool_id": ""},
+            "$.tool_id: must be at least 1 character",
+        ),
         (
             "gateway.submit_feedback",
             {"title": "short", "description": "d"},
-            "is too short",
+            "$.title: must be at least 8 characters",
         ),
         (
             "gateway.tasks_result",
             {"server_name": "s", "task_id": "t", "options": {"max_output_chars": 5}},
-            "less than the minimum",
+            "$.options.max_output_chars: must be greater than or equal to",
         ),
     ],
 )
````

### Patch — `CHANGELOG` (`CHANGELOG.md`)

````diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 6ee53d7..a326c0b 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -383,6 +383,7 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 
 
 ### Fixed
+- **A rejected gateway-tool argument no longer echoes its value into the response, the log or the scoped audit (Consiliency/pmcp#297).** Both validation layers rendered the value that failed: the input-schema gate returned jsonschema's message (`Input validation error: 'Bearer sk-…' is not of type 'object', 'null'`), and an argument model's pydantic error — returned as `str(e)[:400]` and logged as `Tool execution error: …` — carried `input_value=…` (the full value for `InvokeInput`'s correlation-ID charset check and a non-dict `meta`, a truncated repr of the whole argument dict for the all-or-none correlation check). Rejections are now described from their structure, as `<JSON path>: <reason>` — e.g. `Input validation error: $.options: must be of type object or null`, `Invalid arguments: $.run_correlation_id: correlation IDs may contain only alphanumerics and ._:-` — where the reason is a fixed phrase filled only from the tool's own schema or model (a type, a length, a pattern, the allowed values) and a key the caller chose is shown as `*`. The log line is `Tool execution error: invalid arguments for <tool>: <same description>`; a validation error from anything else a handler parses (a downstream payload) reads `Validation error: …` and is described the same way. **Wording change:** the text after `Input validation error: ` is no longer jsonschema's message; a client matching on phrases such as `is not of type` or `is too short` must match the new form. A call rejected by the tool's argument model (not the gate) is now recorded in the scoped audit as an `audit.rejection` like a gate rejection, with `rejected_argument_validator: null`, instead of an `audit.invocation` `failure` that copied its unvalidated correlation fields. An unregistered tool name is no longer written to the log (`Tool execution error: unknown gateway tool`); the response still names it. `gateway.provision_status` validates its arguments before its catch-all, which logged a traceback of the validation error.
 - **`tools/call` input-schema rejections are now recorded in the scoped-advisor audit, without argument values (Consiliency/pmcp#296).** A call the transport gate rejects used to return `Input validation error: …` before the audit was reached, so an operator saw no attempt at all. It is now written as a new `audit.rejection` event (not an `audit.invocation`: nothing was invoked, and a reader that correlates invocations to a run skips it) with the tool name, `terminal_status: "invalid_arguments"`, `rejected_argument_path`, the failing location as a JSON array (a key the schema declares, an array index, or `null` for a key the caller chose, since that key can itself be a secret), and `rejected_argument_validator`, the failing JSON Schema keyword (`type`, `pattern`, `required`, …). The record never contains the validation message, the rejected value, correlation IDs, or any digest of the arguments. The capability stays `scoped_advisor_audit.v1`; readers that dispatch on `event` are unaffected. Policy is now judged **before** the schema: a call to a policy-blocked gateway tool is refused with "Gateway tool blocked by policy" and recorded `denied` whatever its arguments, instead of getting an `Input validation error` that described the blocked tool's schema. If the audit sink has failed, a malformed call now gets "Scoped advisor audit channel failed" like every other call, instead of its validation error. The response to a rejected call from an allowed tool is unchanged. An `audit.invocation` record now reads nothing the schema gate did not vouch for: a call refused by policy, or made to an unregistered name, is recorded `denied` with every argument-derived field (`run_correlation_id`, `seat_correlation_id`, `downstream_tool_id`, `evidence_label_digest`, `source_reference_hash`) `null`, a result digest that no longer covers the caller's tool name, and a `gateway_tool_digest` of the registered name (for an unregistered name, of nothing) — previously a correlation-shaped value or a public URL anywhere in such a call's arguments was copied or hashed into the audit. Every other invocation record reads only the top-level arguments the tool's schema declares, so a correlation-shaped key a tool does not declare (e.g. `run_correlation_id` on `gateway.describe`) is no longer recorded; `gateway.invoke` declares every field the record reads, so its records are unchanged.
 - **`sanitize_auth_diagnostic` does its keyword and URL-punctuation work in linear time.** The keyword rule now runs through `pmcp.keyword_matcher` (the same matches as the regular expression it replaces, pinned by a seeded corpus), and trailing punctuation is split off a URL in one pass. Output is unchanged.
 - **Gateway tool `inputSchema`s are now derived from the pydantic models that validate the arguments, so the two can no longer disagree (Consiliency/pmcp#236).** Constraints the models always enforced are now advertised and enforced at the transport gate — `minLength` on identifiers, `submit_feedback.title` 8–160 chars, bounds on `tasks_result.options` — so those rejections now come back as an `isError` tool result reading `Input validation error: …` instead of an `{"error": true}` payload. `gateway.invoke` now advertises `task`, `trace_context` and `_meta`; `gateway.tasks_*` advertise `requestor_context`; `tasks_result.options` gains `timeout_ms`. Optional arguments are advertised as `type: [X, "null"]` and the transport gate now accepts an explicit `null` for them, as the handlers always did; 28 optional arguments (e.g. `catalog_search.query`, `invoke.options`, `auth_connect.credential`) were previously rejected at the gate when sent as `null`. The gate does not apply pydantic's lax coercion: values such as `1` for a boolean or `"5"` for an integer on the newly advertised `invoke.task` fields (`enabled`, `ttl`, `poll_interval`), which were previously accepted and coerced, are now rejected with `Input validation error: 1 is not of type 'boolean'`. `invoke.task.ttl` now advertises its range on both sides, so `1e20`, `-1e20` and `float(±2**63)` are rejected at the gate, and so is any integer outside [−2^63+1, 2^63−1] (including `-2**63` itself), which the handler previously accepted. `invoke.evidence_label_digest` now also advertises its exact length (64), so a digest with a trailing newline is rejected at the gate instead of by the handler. Inputs the gate now rejects that previously reached the handler were recorded in the scoped-advisor audit as `failure`; they are now recorded as `audit.rejection` events with `terminal_status: "invalid_arguments"` (see the Consiliency/pmcp#296 entry above). Unknown keys are still ignored in this release — see the following entry once B lands. Argument descriptions agents already saw are unchanged, except `gateway.update_server.force`, which now describes the task-aware behaviour; 19 previously undescribed arguments gain a description.
````

### Patch — `README` (`README.md`)

````diff
diff --git a/README.md b/README.md
index 94542a3..f7145ea 100644
--- a/README.md
+++ b/README.md
@@ -1518,9 +1518,11 @@ supply `run_correlation_id`, `seat_correlation_id`, and a SHA-256
 tool/status/policy/result digests, and a hashed public-source reference—not raw
 URLs, queries, arguments, credentials, or result bodies—and ends with one
 fsynced completeness marker. A call whose arguments fail the tool's input schema
-is recorded as a separate `audit.rejection` event (`terminal_status:
-"invalid_arguments"`) carrying only the tool, the failing JSON path
-(caller-chosen keys shown as `null`) and the failing schema keyword. Only
+or its argument model is recorded as a separate `audit.rejection` event
+(`terminal_status: "invalid_arguments"`) carrying only the tool, the failing JSON
+path (caller-chosen keys shown as `null`) and the failing schema keyword (`null`
+for a model rejection). The rejection the caller sees, and the log line, name the
+same path and a reason taken from the schema, never the rejected value. Only
 arguments that passed the tool's input schema, and only the top-level keys it
 declares, are read into any record; a call denied by policy records none of its
 arguments.
````

## Measurement scripts

Run from the repo root of the tree being measured (they import the test module).

### `probe.py` — the before/after probe (*Research summary*)

```python
"""Probe: which rejections echo a sentinel into the response or the log (main)."""
import asyncio, json, logging, os, sys, tempfile, io
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from tests.test_scoped_advisor_audit import _make_ctx  # noqa
from mcp.types import CallToolRequestParams
from pmcp.server import GatewayServer
from pmcp.tools.handlers import get_gateway_tool_definitions

S = "SENTINELzq9x"
buf = io.StringIO()
h = logging.StreamHandler(buf); h.setLevel(logging.DEBUG)
logging.getLogger().addHandler(h); logging.getLogger().setLevel(logging.DEBUG)

def bad_values(prop):
    t = prop.get("type"); ts = t if isinstance(t, list) else [t]
    out = []
    if "string" in ts:
        out.append(("type", {S: S}))
        if "pattern" in prop: out.append(("pattern", S + " !"))
        if "maxLength" in prop: out.append(("maxLength", S * 40))
        if "enum" in prop: out.append(("enum", S))
    else:
        out.append(("type", S))
    return out

async def main():
    tmp = Path(tempfile.mkdtemp())
    os.chdir(tmp)
    (tmp / "p.json").write_text("{}")
    srv = GatewayServer(project_root=tmp, cache_dir=tmp / "c", policy_path=tmp / "p.json")
    srv._create_server()
    entry = srv._server.get_request_handler("tools/call")
    for tool in get_gateway_tool_definitions():
        props = tool.input_schema.get("properties") or {}
        cases = [("extra+", "__extra__", None)]
        for k, p in props.items():
            for kind, v in bad_values(p):
                cases.append((kind, k, v))
            if p.get("type") in ("object", ["object", "null"]):
                for k2, p2 in (p.get("properties") or {}).items():
                    for kind, v in bad_values(p2):
                        cases.append((kind, f"{k}.{k2}", v))
        if tool.name == "gateway.invoke":
            for f in ("run_correlation_id", "seat_correlation_id"):
                cases.append(("charset", f, S + "!"))
            cases.append(("allornone", "run_correlation_id", S))
        for kind, key, v in cases:
            args = {"__x" + S: S}
            for r in tool.input_schema.get("required") or []:
                args[r] = "tid::x" if r == "tool_id" else "xx"
            if key == "__extra__":
                continue
            if "." in key:
                a, b = key.split(".")
                args[a] = {b: v}
            else:
                args[key] = v
            if kind == "allornone":
                args.update({"arguments": {"q": S * 3}})
            start = buf.tell()
            try:
                res = await asyncio.wait_for(entry.handler(_make_ctx(), CallToolRequestParams(name=tool.name, arguments=args)), 20)
                text = " ".join(c.text for c in res.content)
            except Exception as e:
                text = f"RAISED {type(e).__name__}"
            log = buf.getvalue()[start:]
            leak_r, leak_l = S in text, S in log
            if leak_r or leak_l:
                print(f"{tool.name:34} {kind:9} {key:28} resp={leak_r} log={leak_l} :: {text[:150]!r}")
asyncio.run(main())
```

### `count_leaks.py` — leak counts per layer and channel over the sweep's own cases

```python
"""Run every sweep case once per server; count cases leaking per channel."""
import asyncio, logging, sys, tempfile, collections
from pathlib import Path
sys.path.insert(0, ".")
from tests.test_argument_error_echo import _CASES, _SENTINELS, _server, _call, _forbidden, _pmcp_formatters

class Cap(logging.Handler):
    def __init__(self): super().__init__(logging.DEBUG); self.records = []
    def emit(self, r): self.records.append(r)

async def main():
    cap = Cap(); root = logging.getLogger(); root.addHandler(cap); root.setLevel(logging.DEBUG)
    for audited in (False, True):
        srv, audit = _server(Path(tempfile.mkdtemp()), audited=audited)
        counts = collections.Counter(); total = collections.Counter()
        s = _SENTINELS[0]; forms = _forbidden(s)
        for c in _CASES:
            start = len(cap.records); before = audit.read_text() if audit and audit.exists() else ""
            r = await _call(srv, c.tool, c.build(s))
            text = "".join(b.text for b in r.content)
            log = "\n".join(f.format(x) for x in cap.records[start:] for f in _pmcp_formatters())
            aud = (audit.read_text() if audit else "")[len(before):]
            total[c.layer] += 1
            for ch, t in (("response", text), ("log", log), ("audit", aud)):
                if any(f in t for f in forms): counts[(c.layer, ch)] += 1
        print("audited" if audited else "plain", dict(total), dict(sorted(counts.items())))
asyncio.run(main())
```

### `mutants.py` — the mutation run (*Mutation evidence*); `python mutants.py <worktree> <out-dir> [M4 ...]`

```python
"""Apply each mutant to a saved copy, run the tests, restore from the copy.

usage: python mutants.py <worktree> <out-dir>
"""
import filecmp, shutil, subprocess, sys
from pathlib import Path

root, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
TESTS = ["tests/test_argument_error_echo.py", "tests/test_scoped_advisor_audit.py",
         "tests/test_gateway_tool_schemas.py"]
S = "src/pmcp/server.py"; A = "src/pmcp/argument_errors.py"; T = "src/pmcp/types.py"
H = "src/pmcp/tools/handlers.py"; D = "src/pmcp/scoped_advisor_audit.py"
MUTANTS = [
 ("M1 gate renders e.message", S, [("+ describe_schema_error(e, tool.input_schema, arguments),", "+ e.message,")]),
 ("M2 except arm returns str(e)", S, [('"message": f"{kind}: {described}"', '"message": str(e)[:400]')]),
 ("M3 except arm logs str(e)", S, [("                        audited_name,\n                        described,\n", "                        audited_name,\n                        str(e),\n")]),
 ("M4 model rejection recorded as invocation", S, [("                    if rejected_by_model and tool is not None:\n", "                    if False:\n")]),
 ("M5 model loc not redacted", A, [("        elif type(segment) is str and segment in declared:\n", "        elif type(segment) is str:\n")]),
 ("M6 schema path not redacted", A, [("        elif isinstance(node, dict) and type(segment) is str and segment in declared:\n", "        elif isinstance(node, dict) and type(segment) is str:\n")]),
 ("M7 phrase may read ctx error", A, [('    {"min_length", "max_length", "pattern", "expected", "gt", "ge", "lt", "le"}\n', '    {"min_length", "max_length", "pattern", "expected", "gt", "ge", "lt", "le", "error"}\n'), ('    "missing": "is required",\n', '    "missing": "is required",\n    "value_error": "{error}",\n')]),
 ("M8 missing-required reads an instance key", A, [("    for name in required:\n        if isinstance(name, str) and name not in node:\n            return name\n    return None\n", "    return str(list(node)[-1])\n")]),
 ("M9 validator back to ValueError with value", T, [("            raise argument_error(CORRELATION_ID_CHARSET)\n", '            raise ValueError(f"correlation IDs may contain only alphanumerics and ._:-: {value}")\n')]),
 ("M10 provision_status validates inside its try", H, [("        parsed = ProvisionStatusInput.model_validate(input_data)\n        job_id = parsed.job_id\n        try:\n", "        try:\n            parsed = ProvisionStatusInput.model_validate(input_data)\n            job_id = parsed.job_id\n")]),
 ("M11 unknown tool name logged", S, [('logger.error("Tool execution error: unknown gateway tool")', 'logger.error(f"Tool execution error: {e}")')]),
 ("M12 type phrase is e.message", A, [('        return f"must be of type {_type_names(constraint)}", None\n', "        return error.message, None\n")]),
 ("M13 model phrase is pydantic msg", A, [("f\"{_model_phrase(item['type'], item.get('ctx'))}\"", "f\"{item['msg']}\"")]),
 ("M14 audit model path from input", D, [("                argument_path = model_error_path(error, schema, arguments)\n", "                argument_path = [str(i.get('input')) for i in error.errors()]\n")]),
]
ONLY = sys.argv[3:]
for label, rel, edits in MUTANTS:
    if ONLY and label.split()[0] not in ONLY:
        continue
    path = root / rel; saved = out / (path.name + ".saved")
    shutil.copy2(path, saved)
    text = path.read_text()
    for old, new in edits:
        n = text.count(old)
        assert n == 1, (label, n, old)
        text = text.replace(old, new)
    path.write_text(text)
    log = out / (label.split()[0] + ".log")
    r = subprocess.run([str(root / ".venv/bin/python"), "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider", "-x", "--tb=line"],
                       cwd=root, capture_output=True, text=True)
    log.write_text(r.stdout + r.stderr)
    shutil.copy2(saved, path)
    assert filecmp.cmp(saved, path, shallow=False), label
    lines = (r.stdout).splitlines()
    first = next((l for l in lines if l.startswith(("E ", "/")) and ("Error" in l or "assert" in l)), "")
    summary = next((l for l in reversed(lines) if " passed" in l or " failed" in l), "")
    print(f"{label}: applied=yes exit={r.returncode} | {summary.strip()} | {first.strip()[:230]}")
```

