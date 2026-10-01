# Detailed plan: describe validation errors from their structure, never their value — everywhere pmcp turns an exception into text

> **Revision 9 (2026-10-01), on main `d5a3cda`** (re-fetched: `origin/main`
> is still `d5a3cda`, as for rev 8, which moved it from `7680445` by six
> commits; rev 8's account follows). Two of them touch patched files:
> - Consiliency/pmcp#294 piece 1 (version pinning, Consiliency/pmcp#319): `manifest/loader.py`,
>   `tools/handlers.py`, `types.py`, the CHANGELOG and the README;
> - PR 321 (Consiliency/pmcp#321, for Consiliency/pmcp#287): the stdio and SSE read loops in
>   `client/manager.py`.
>
> Main was merged into `wip/297-code`. The conflicts were resolved like this:
> - the overlay's unreadable-document return keeps `exception_text` and
>   takes main's 4-tuple;
> - the stdio reader keeps PR 321's dispatcher with rev 8's parse split
>   (§12);
> - both CHANGELOG entries are kept.
>
> Main's one new direct parse, `_shipped_manifest_entries`, was caught by
> the helpers-only rule and is routed through `load_yaml`, and the sink guard
> found no new sink. PR 321 now drops a legacy-SSE frame that fails
> validation instead of ending the loop, so the frame sweep's downstream
> follows such a frame with the real reply, as it already did on stdio.
> The other commits are a pyjwt bump, a Consiliency/pmcp#294 plan document,
> a CI action bump, and Consiliency/pmcp#323 (`manifest/loader.py` warnings).
>
> Consiliency/pmcp#297, the prerequisite for piece B
> (`extra="forbid"`) of Consiliency/pmcp#236. The change is **embedded, not
> described**: the 40 blocks under *Verbatim bodies* are `git apply` patches
> against `origin/main` @ `d5a3cda`, byte-identical to the verified code on
> the local-only branch `wip/297-code` @ `8d33b49`, which was never pushed
> (rev 1 was `19dac95`, rev 2 `929f693`, rev 3 `026aadc`, rev 4 `ee644a9`, rev 5 `1824a09`, rev 6 `9b24daa`, rev 7 `dd3f707`, rev 8 `2d9e736`). *Embedding
> proof* extracts them from this file with its own extractor, `git apply
> --check`s them on a fresh `d5a3cda` worktree, applies them and compares
> the whole tree with `8d33b49`.
>
> **What rev 9 changes** (round-8 board on Consiliency/pmcp#314 @
> `4bafa47`: codex BLOCKING; gemini agree; claude non-blocking N1, N2; see
> *Rev 8 board findings — before/after*):
> - **Where JSON starts, from the grammar.** Rev 8 called a stdio line the
>   downstream's output when the parser rejected its first character. An
>   unterminated string is rejected *at* its opening quote, so
>   `"<secret>` was logged verbatim (codex P1). A rejected line is now
>   output only if its first significant character cannot begin **any**
>   JSON value (`{ [ " - 0-9 t f n`, and Python's `N`/`I`). A *significant*
>   character skips whitespace (everything `.strip` removes), control and
>   format characters (NUL, ESC, a BOM, ZWSP) and U+FFFD. Everything else
>   is described.
> - **Output is shown only up to a frame opener** (claude N1): a line that
>   is output is shown up to its first `{`, `[` or `"`, then
>   `(rest omitted)`. So a frame behind a banner, a stray `,`/`}`, an ANSI
>   escape or a CR on the same line is never shown. Digits are kept
>   (`listening on port 8080`).
> - **The oracle no longer shares the classifier's assumption:** it
>   derives "JSON-shaped" from the grammar's value-start set and allows a
>   shown record only if it holds no frame opener. No record is exempt from
>   the leak check.
>
> **Revs 2–8** (each answering the previous board; rev 8's full account,
> with the round-7 table, is in rev 8 of this plan, `4bafa47`; rev 7's
> with the round-6 table in rev 7, `4d4b186`; revs 2–6 in rev 6, `8d03712`):
> - rev 8: rejected stdio frames described, not logged (round-7 codex);
>   the static rule covers parser submodules; built on PR 321
>   (Consiliency/pmcp#287);
> - rev 7: parse failures classified by origin through `pmcp.parsing`; a
>   malformed `Origin` port is a 403; timestamps through the helper;
>   `threading.excepthook` wrapped;
> - rev 6: parse errors rendered structurally; `sys.excepthook` and
>   `handleError` wrapped; the CLI inside the static guard;
> - rev 5: the scrubber is installed on `import pmcp` (every entry point,
>   including `pmcp refresh`);
> - rev 4: the record-factory scrubber (every logger, including the SDK's
>   `"client"`);
> - rev 3: malformed downstream frames on every transport; a dataflow-aware
>   static guard;
> - rev 2: every exception-to-text sink through `exception_text`; the log
>   oracle sees everything; constraints read from pmcp's own schema.
>
> Decisions (rev 1's stand; each argued in *Design*):
> - One renderer module, `pmcp.argument_errors`, for both libraries. The
>   failing location is shown with caller-chosen keys as `*`. The reason is a
>   fixed phrase, and it never reads jsonschema's
>   `message`/`instance`/`validator_value`/`context`/`cause`/`schema` or
>   pydantic's `msg`/`input`/`ctx`.
> - pmcp's three custom validators raise fixed-message
>   `PydanticCustomError`s.
> - Same rule for the log and the audit. A call rejected by the tool's own
>   argument model is an `audit.rejection`. An unregistered tool name is not
>   logged.
> - Responses keep their prefixes (`Input validation error: $...`,
>   `Invalid arguments: $...`).
## Rev 8 board findings — before/after

The round-8 board on Consiliency/pmcp#314 @ `4bafa47`: codex BLOCKING (P1,
F023–F032); gemini agree; the claude seat found nothing blocking (N1, N2).
The round-7 table and rev 8's answers are in rev 8 of this plan
(`4bafa47`); the round-6 table in rev 7 (`4d4b186`); rounds 1–5 in rev 6
(`8d03712`).

**Reproduced on the real stdio reader** (`r9/repro.py`; the sentinel, or
its NUL-interleaved UTF-16/32 spelling, searched in every record):

| Line | Rev 8 | Rev 9 |
|---|---|---|
| codex: "S (unterminated) | leak | clean |
| codex: spaces + "S | leak | clean |
| N1: \xff + frame | leak | clean |
| N1: NUL + frame | leak | clean |
| N1: FF + frame | leak | clean |
| N1: VT + frame | leak | clean |
| N1: NBSP + frame | leak | clean |
| N1: ZWSP + frame | leak | clean |
| N1: U+2028 + frame | leak | clean |
| N1: ANSI + frame | leak | clean |
| N1: ',' + frame | leak | clean |
| N1: '}' + frame | leak | clean |
| N1: ready{frame} | leak | clean |
| N1: ready\r{frame} | leak | clean |
| N1: UTF-16LE+BOM | leak | clean |
| N1: UTF-16BE+BOM | leak | clean |
| N1: UTF-32+BOM | leak | clean |
| **total** | 17 of 17 leak | 0 of 17 leak |

| Finding | Rev 8 (`2d9e736`) | Rev 9 (`8d33b49`) | Proven by |
|---|---|---|---|
| **codex P1 (blocking): an unterminated JSON string is logged verbatim.** A downstream writes `"SENTINEL_297_SECRET_…` (or with leading spaces). `JSONDecodeError` ("Unterminated string starting at") points at position 0, the opening quote, so rev 8's rule ("the parser rejected the first significant character, which opens no object or array") called it output and logged `Non-JSON output: "SENTINEL…`. The test oracle encoded the same assumption and exempted the record. | The boundary was *where the parser failed*. | **The boundary is where JSON can start** (`_is_downstream_output(text)`). A rejected line is output only if its first significant character cannot begin any JSON value: `{ [ " - 0-9 t f n` (RFC 8259 §3), plus `N`/`I` for Python's `NaN`/`Infinity`. "Significant" skips everything `str.isspace` covers (all that `.strip` removes, NBSP and U+2028 included), control and format characters (NUL, ESC, a BOM, ZWSP) and U+FFFD. Any other rejected line is described, with no content. The trade-off is stated: a banner that starts like a value (`true story`, `-v`, `[INFO] …`, `42 …`) is described rather than shown. | `test_no_stdio_line_shows_frame_content` × 6 families × 628 lines (434–500 JSON-shaped per family). It covers every value-start character followed by the sentinel, an unterminated string and an unterminated key, eight number and literal prefixes followed by data, each behind 15 lead variants, and requires a description for every JSON-shaped line. **Red on rev 8**, first on codex's own case (`'' value-start '"'` → `Non-JSON output: "sk-proj-…`). Mutant M50 (the quote dropped from the value-start set) dies. |
| **claude N1 (non-blocking, pre-existing): a whole frame behind any rejected first character is logged raw.** `\xff`, NUL, FF, VT, NBSP, ZWSP, U+2028, an ANSI escape, `,`, `}`, a banner without a newline (`ready{…}`, `ready\r{…}`), and a UTF-16/32 frame with a BOM. `.strip()` then removed some prefixes, so the record was the bare frame. | Logged raw (as on main). | **Closed two ways.** The skipped lead covers every one of those prefixes, so a frame behind them is JSON-shaped and described. For a line that *is* output, `_output_text` shows its significant text only up to the first `{`, `[` or `"`, then ` (rest omitted)`. A banner before a frame on the same line therefore shows only itself, and since the skipped lead is never shown, `.strip()` cannot uncover a bare frame. Digits are not openers, so `listening on port 8080` stays whole. A shown line with no opener is shown as is. | The same generator: 12 prefix classes followed by a whole frame, behind every lead; `\xff`/`\xfe\xff` before a frame; the frame in UTF-8-with-BOM, UTF-16 and UTF-32 (both byte orders, with and without a BOM); CR-only separation, two frames on one line, a frame with a trailing banner. Every shown record must hold no opener, and no record may carry the sentinel. `test_a_banner_stays_useful_on_stdio` holds 7 operator controls (`server ready …`, `listening on port 8080`, a BOM or NUL before a banner, a banner before a frame, single versus double quotes). The frame sweep's exemption (`_non_json_line`) now requires the shown text to hold no opener, whatever the reader decided. **Red on rev 8:** all 17 repro cases. Mutants M51 (no truncation), M52 (control and format characters not skipped) and M53 (only ASCII whitespace skipped) die. |
| **claude N2 (note):** the SDK logs every *accepted* frame and outgoing arguments at DEBUG (`sse.py:99/:123`, `streamable_http.py:165/:562`). | Not in scope. | **Not in Consiliency/pmcp#297:** accepted values are Consiliency/pmcp#315's (*Non-goals*). | — |


## Task

Address Consiliency/pmcp#297: "Validation errors echo argument values
(pydantic `input_value`, jsonschema `type`/`pattern`) into responses and
logs". pmcp brokers untrusted servers for a prompt-injectable agent, so a
token passed in the wrong field came straight back into the agent's context
and the gateway log. Fix the class: enumerate every place a validation or
parse exception's text, or a caller value, reaches a response, a log line or
an audit record (rev 7: or a request header, into a 500's traceback); render errors from structure (`loc` + `type`; `json_path` +
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
| L9 (rev 2, corrected) | a `ValidationError` raised past argument validation (downstream data: `McpTaskInfo(...)` in `client/manager.py:1689-1705`, `McpTaskInfo.model_validate` `handlers.py:6141`; listing entries `client/manager.py:796/:843/:896`) is caught **inside** the handler or manager: `tasks_list/get/result/cancel` `handlers.py:6007-6016`, `:6050-6060`, `:6124-6134` put `str(e)` in the audit-event buffer (`gateway.health`'s `audit_events`) and `_sanitize_error(e)` (= `str(e)` through the redactor) in `errors`; the listing parsers log `describe_exception(e)` (= the same) | response, log, audit-event buffer / health | the downstream payload's values (a high-entropy value is sometimes masked by the redactor, a low-entropy one never is). *Every exception-to-text sink* below is the whole class |

Not leaking (measured or read):
- `ScopedAdvisorAudit.record_rejected_arguments` (`scoped_advisor_audit.py:303-351`, Consiliency/pmcp#296) records path + keyword only.
- The SDK's own params validation: `mcp/shared/jsonrpc_dispatcher.py:100-101` maps a pydantic `ValidationError` to `INVALID_PARAMS` with `data=""` ("no pydantic text on the wire"); `mcp 2.0.0`.
- CLI paths: no subcommand in `src/pmcp/cli.py` / `src/pmcp/cli_commands/` routes through a gateway argument model (`grep model_validate|ValidationError|jsonschema` is empty there); argparse echoes the operator's own terminal input only.
- `GatewayException.__str__` is its message only (`errors.py:141-156`); caller values live in `details`.
- No `GatewayArguments` model sets `hide_input_in_errors`; the only other custom validators with a value in their message are `PackagesPolicy._reject_version_qualified_entries` (`types.py:1158-1166`, operator config, not reachable from `tools/call`) and `McpTaskInfo._normalize_task_timestamp` (`types.py:533`, downstream data).

### Every exception-to-text sink (rev 2)

Measured on `7680445` and unchanged since; how the static guard defines a sink, and its counts on main (89 findings in 20 modules) are in rev 6 of this plan (`8d03712`).

### Downstream frames (rev 3)

Measured on `7680445` and unchanged since; how each MCP SDK client transport turns a malformed frame into an error are in rev 6 of this plan (`8d03712`).

### Not leaking except to the sender (rev 2, N4)

`mcp/server/streamable_http.py:550` (SDK, legacy streamable-HTTP transport)
answers a malformed JSON-RPC *envelope* (e.g. `"params": "<string>"`) with
HTTP 400 `Validation error: {str(e)}`, pydantic text included. Only the
sender of that malformed envelope sees it, and no tool argument can reach
it, because any `params` object passes the envelope. It is SDK-owned. See
*Non-goals*.

### The probe (before)

`probe.py` sent a wrong-type / pattern / length / enum value carrying a
sentinel to every declared property of every registered tool. On
`7680445` it found **97 leaking cases**, every one in the response and
the three validator cases in the log too; `count_leaks.py` found 225 gate
and 6 model response leaks. The outputs and both scripts are in rev 5 of
this plan (`91562e6`). On the patched tree both report zero.

### Library facts the design rests on

Measured on `7680445` and unchanged since; the measured jsonschema and pydantic behaviours the renderers rely on are in rev 6 of this plan (`8d03712`).

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
- **pydantic** (rev 2, N2): `error.errors(include_input=False,
  include_url=False, include_context=False)`. The error `type` selects
  either a fixed phrase (`_FIXED_PHRASES`: `missing`, `extra_forbidden`,
  `*_type`, `*_parsing`, pmcp's own types) or a constrained one
  (`_CONSTRAINED_PHRASES`: lengths, bounds, pattern, `literal_error`,
  `enum`). A constrained phrase names the **JSON Schema keyword** that holds
  its constraint (`minLength`, `maximum`, `pattern`, `enum`, ...). The value
  is read from the gateway's own schema node at the error's location
  (`_schema_node_at`: `properties` for a name, `items` for an index) and
  type-checked. With no such node the phrase names no constraint (`is too
  short`, `is not an allowed value`). pydantic's `ctx` is never read. So a
  validator that raises `PydanticCustomError("literal_error", …,
  {"expected": value})` renders the schema's enum, or nothing, and never the
  value. Any other type, including `value_error`/`assertion_error`, reads
  `is invalid`.
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
  GATEWAY_TOOL_INPUT_MODELS[tool].__name__`), and `exception_text(e)[:400]`
  for everything else (rev 2, §7): `str(e)` unchanged for a non-validation
  exception, `N validation error(s) for <Model>: ...` for any other.
- **Log.** `Tool execution error: invalid arguments for <registry name>:
  <description>`, or `Tool execution error: <exception_text(e)>`; an unregistered name logs
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

`tests/test_argument_error_echo.py` (the argument sweep; each axis is
stated in full in rev 6 of this plan, `8d03712`, unchanged since rev 2):
every tool in `GATEWAY_TOOL_INPUT_MODELS` × every declared position
(nested properties and list items; a position with no case fails) × every
shape the node really rejects (type, pattern, length, enum, removed
`required`, every custom validator found by introspection), each decorated
with extra keys and sentinel content in every open container, on a plain
and a scoped-audit server, × 6 sentinel families × 2 lengths. The oracle
requires a rejection (no vacuous pass), then no 12-character window or
hash of the sentinel in the response, every log record at DEBUG in full,
stdout/stderr, warnings, the audit JSONL or the audit-event buffer, then
identical output for two sentinel lengths, then (hex) the path and reason.
- **inside handlers**: a stub handler raising each validation-error type
  (downstream `McpTaskInfo`, an argument model, jsonschema) built from a
  sentinel value;
- **downstream, through the real handlers** (rev 2, B1): `_task_server`
  gives the real `GatewayServer` one task-capable downstream whose
  `_send_request` answers with a crafted task payload. The rest is
  unchanged: `ClientManager`, the handlers, the server arm and the audit.
  - Positions are every literal key `ClientManager._task_info_from_payload`
    reads, taken from its AST, × every bad shape the real parser rejects
    with a `ValidationError`, found by running it.
  - The paths are the five gateway tools that parse a task payload:
    `tasks_list`, `tasks_get`, `tasks_result`, `tasks_cancel`, and `invoke`
    with a task.
  - Per case: the downstream method was called; nothing leaks into any
    channel; the response says `validation error(s) for McpTaskInfo: $`, so
    there is no vacuous pass; the pair differential holds; and at the end
    `gateway.health`'s output carries no sentinel.
  - A value the parser *accepts* (a digits-only `createdAt` is a number) is
    downstream data returned by design and is skipped.
  - The listing parsers get the same treatment (tool, resource and prompt
    entries, and prompt arguments). The positions are the parser's own
    literal keys, restricted to those whose rejection is a
    `ValidationError`, as recorded from its `describe_exception` call.
- **static** (rev 2; dataflow-aware in rev 3):
  `tests/test_exception_text_sinks.py`. A test confirms that each of 44
  constructs fires: the rev 2 seat's 41 that run on 3.10, a return, a
  truncated copy, and the G1 regression. Another confirms that the
  renderers pass.
- **the SDK's `ClientSession`** (rev 4):
  `test_no_malformed_session_message_value_reaches_the_log` drives the real
  `refresh_server` (SDK `stdio_client` + `ClientSession`) against a stdio
  downstream. Before answering `tools/list`, the downstream sends one
  malformed message of each kind:
  - notifications: `message`, `progress`, `resources/updated`,
    `tools/list_changed`, `cancelled`;
  - server-to-client requests: `roots/list`, `sampling/createMessage`,
    `elicitation/create`, `ping`.

  Each runs over 3 families × 2 lengths, with the full leak oracle and a
  pair differential over the `"client"` and pmcp records. It is not
  vacuous: the SDK's rejection is observed, either as a scrubbed record
  (on `"client"` for the four notifications whose envelope is well formed)
  or as the SDK's error reply to the downstream's request.
- **parse errors** (rev 6; rev 7):
  - Every file-reading parse site pmcp exposes as a function (15) runs
    × 6 families × every input the raw parser rejects. There are 16 YAML
    shapes and 3 JSON:
    - the 4 syntax and tag shapes;
    - rev 7's `!!int`, `!!int 0x`, `!!float`, `!!bool`, `!!timestamp`,
      `!!binary`, `!!python/object:`, `!!python/name:`, `!!set` and merge
      tags;
    - an undefined and a duplicate anchor.

    Duplicate keys are in the list but are filtered out, since PyYAML
    accepts them.
  - "Rejects" means **any** exception.
  - Each input is checked in the rendered error, the traceback, the log
    and stdout/stderr, for the sentinel **in each case**: `!!float` and
    `!!bool` lowercase it.
  - The helpers are swept directly too, with deep nesting and an
    undecodable byte added, and every cause class the board named is
    required.
  - The 3 timestamp sites (trust store, package approvals, `McpTaskInfo`)
    are swept × 6 families.
  - Fresh interpreters check the fatal policy printed uncaught, a thread's
    uncaught chain, and the hooks with no stderr.
  - The helpers-only rule is AST-enforced, and its detector is pinned on
    23 spellings.
- **every entry point, and `pmcp refresh`** (rev 5): a fresh interpreter
  per entry point asserts that the record factory is the scrubber:
  - `import pmcp`, `pmcp.cli`, `pmcp.manifest.refresher`,
    `pmcp.transport.http`;
  - `python -m pmcp --version`;
  - each console script the distribution declares.

  The real `pmcp.cli.main(["refresh", ...])` runs against a downstream
  sending 3 malformed notification kinds × 3 families. Only the manifest
  lookup is replaced, and stdout, stderr and `.pmcp/logs/gateway.log` are
  checked.
- **the record scrubber, branch by branch** (rev 4):
  `tests/test_log_record_scrubber.py` covers:
  - `exc_info` on 8 logger names (`client`, `server`, `mcp.*`, `asyncio`,
    `uvicorn.error`, `httpx`, `anyio`, a third party), × 6 families;
  - `%`-args in a tuple, list, dict, nested containers, a `%(name)s`
    mapping, and `%r`;
  - `msg` as an exception;
  - `stack_info`;
  - plain exceptions left unchanged;
  - an idempotent install that wraps the previous factory, and the
    gateway installing it.
- **downstream frames, every transport** (rev 3):
  `tests/test_downstream_frame_echo.py`, a real downstream per transport.
  - The transports: streamable-HTTP JSON, streamable-HTTP SSE, legacy SSE
    and stdio.
  - The requests: every one pmcp sends.
  - The frames: five malformed envelope shapes, × 3 families × 2 lengths.
  - The checks: the leak oracle over the caller's output, `gateway.health`,
    the log, the streams and the warnings. The pair differential covers the
    caller's output (for rejections) and pmcp's own log records as a
    multiset, since transport tasks log concurrently. One timing line is
    exempt: a teardown's `did not close within 5.0s`.
- **connect retries** (rev 3): a connect whose every attempt fails with a
  validation error, observed through `connect_server`, the retry log and
  `gateway.health`.
- **past the gate**: every gate case sent straight to the real handler must
  raise the model's `ValidationError` without logging the value (this is
  what catches L7).

`tests/test_scoped_advisor_audit.py` (#296's sweep) loses its two log
exclusions (`_FOREIGN_INVOKE_INPUT`, the `Unknown tool: <name>` line) and
their machinery, so its log oracle now excludes nothing, and it accepts the
one new record shape a generated key can cause (`_META_REJECTION`) instead
of the `_INVOKE_ONLY_EXEMPT` field exemption, which is gone.

### 7. Every other exception-to-text sink (rev 2, B1)

`argument_errors` gains three functions, and every sink enumerated above
uses them:
- `exception_text(e)` is `str(e)`, except for a pydantic or jsonschema
  `ValidationError`, or an exception whose own text *embeds* the text of one
  it chains (`RuntimeError(f"... {e}") from e`). Those are described from
  their structure **without a schema**: `N validation error(s) for <Model>:
  $.<path>: <phrase>`, or `schema validation error: $.<path>: fails its
  <keyword> constraint` (N6). The path keeps names that pmcp's own models
  declare. These are read from the `pmcp.*` modules' namespaces, cached per
  `len(sys.modules)`, and do not include caller or downstream keys. Every
  other exception's text is unchanged, so no existing message changes.
- `safe_exc_info(e)` is `e` for `exc_info=`. It returns `None` when the
  chain (`__cause__`, `__context__`, group members) holds a validation
  error, because that traceback would render the value. The message
  carries `exception_text(e)` instead.
- `safe_traceback_text(e)` is the formatted traceback, or a one-line
  stand-in, for the one site that formats a traceback itself
  (`client/manager.py:2680`).

`exception_text`'s embedding check is an exact-substring **backstop** for
wrappers built with `f"{e}"` (rev 2 board, N2). It does not recognise a
truncated or reformatted copy (`str(e)[:200]`, `e.errors()`), nor
validation text that arrives as a plain string. So pmcp never builds such a
copy, and the guard flags the construction site. Stringified text from
outside pmcp (the SDK's parse errors) is replaced where pmcp receives it
(§9).

They are applied at the three shared renderers (`sanitize_auth_diagnostic`
renders an exception with `exception_text`, and `describe_exception`'s
leaves do too), so `_sanitize_error` and every `describe_exception` caller
follow. They are also applied mechanically at the 82 sinks: `{e}` →
`{exception_text(e)}`, `str(e)` → `exception_text(e)`, a logging argument
`e` → `exception_text(e)`, `exc_info=True` → `exc_info=safe_exc_info(e)`,
and `logger.exception(msg)` → `logger.error(msg, exc_info=safe_exc_info(exc))`.

`server.py`'s arm is simplified. The tool's own model rejection is
described against the tool's schema as `Invalid arguments: ...`. Everything
else goes through `exception_text(e)`. That removes rev 1's
`Validation error: ...` prefix, which is now the `N validation errors for
<Model>: ...` form.

The CLI is out of the static scope. There the operator reads their own
input and config errors. The same config errors reached through the gateway
(policy, trust store, package approvals, `.mcp.json`) are in scope and now
read structurally, which the CHANGELOG states.

### 8. Why sinks and not one choke point

pydantic's `ValidationError.__str__` is a C type, so there is no global
hook. `hide_input_in_errors` exists only per model, and the SDK's and
jsonschema's errors have no such switch. A logging filter sees only records
routed to the handlers it is attached to, and never sees a string built
with `f"{e}"` before logging. What *is* one place is the rule, and the
static test enforces it at every sink.

### 9. Text that originates outside pmcp (rev 3; rev 4)

- **The SDK's synthesised parse errors.** `_downstream_error` replaces any
  `-32700` message with `downstream sent a response that could not be
  parsed`, keeping the code and dropping `data`. It does the same for a
  malformed `error` (not an object, or a non-string `message`), as
  `downstream sent a malformed JSON-RPC error`. The check is by code, not
  by the SDK's wording. A downstream's own `-32700` prose, which describes
  *our* request, is replaced too.
- **Downstream-authored error text is kept, and so is the SDK's
  quotation of it.** This covers a downstream's own well-formed error
  `message` string, and the messages the SDK synthesises that *quote* a
  downstream's header or event name (rev 3 board, N1/N2):
  - `-32600 Unexpected content type: <Content-Type>`
    (`mcp/client/streamable_http.py:387-388`, also logged at ERROR);
  - `Unknown SSE event: <event>` (`:195`, WARNING);
  - `dropping tool %r: invalid x-mcp-header (%s)`
    (`mcp/client/session.py:1258`, WARNING; rev 6).

  All of these are returned or logged as before, by design. They are the
  downstream's text about its own failure, in the same class as its result:
  no caller value can reach them, and they are not a rendering of a
  rejection pmcp or its libraries made of a *value*.
  Consiliency/pmcp#234's redaction applies wherever pmcp renders them.
- **Every log record is scrubbed at creation** (rev 4, rev 3 board B1).
  - Rev 3 attached a filter to the `mcp`/`mcp.*` loggers by name. The
    SDK's `ClientSession` logs on `"client"` (`mcp/client/session.py:70`),
    and `_on_notify` logs a rejected notification with `exc_info=True`
    (`:1418-1419`, `:1432-1433`). That record reached the gateway log
    during the startup description refresh
    (`server.py` → `refresh_all` → `refresh_server` → the SDK's
    `stdio_client` + `ClientSession`).
  - Rev 4 does not enumerate loggers. `install_log_scrubber()` wraps the
    `logging` record factory (`logging.setLogRecordFactory`), keeping the
    previous factory, so every record any logger creates passes through
    `scrub_record`. That covers the SDK's `"client"`/`"server"`, uvicorn,
    httpx, anyio and any other logger, regardless of handlers or
    propagation.
  - It covers what a record *carries*: the traceback, the arguments, an
    exception passed as `msg`. Text a library has already formatted into
    the message string is out of its reach, for example asyncio's "Task
    exception was never retrieved", which embeds the task's `repr`. See
    *Unverified*.
  - `scrub_record` handles:
    - a `msg` that is itself such an exception;
    - `args` (a tuple, or a `%(name)s` mapping), with exceptions found
      however deeply nested in tuples, lists, sets and dicts;
    - an `exc_info` whose chain holds a validation error: the traceback is
      dropped and `exception_text` appended.

    `stack_info` renders frames and source lines only, so it needs no
    scrub (pinned by a test). Every other record is untouched, so `%r` of
    a plain exception still reads `RuntimeError('...')`.
  - The install is idempotent (a marker on the wrapper). **Rev 5:** it runs
    when the `pmcp` package is imported (`pmcp/__init__.py`), so every entry
    point has it before any library logs: the gateway, every `pmcp` CLI
    command (`pmcp refresh` reaches downstreams through the SDK's
    `ClientSession`), `python -m pmcp`, and an embedder. It runs again,
    harmlessly, at `pmcp.client.manager` import and in
    `GatewayServer.__init__`. A log
    call can never fail because of the scrub: it is wrapped, and on any
    error the record passes as it was.
- **stdio's own output** (stdout lines that are output by §12's rule,
  shown up to their first frame opener; and stderr) is still logged at
  DEBUG (*Non-goals*). The frame sweep exempts exactly that record shape,
  and only when what it shows holds no opener (rev 9, §12).

### 10. Parse errors (rev 6; classified by origin in rev 7)

A parse error's text can carry what it rejected, and not only through the
parser's own error types:
- PyYAML's `MarkedYAMLError` renders a snippet of the input around the
  mark when it parses a string. From a file object the snippet is omitted
  (measured), but a constructor error still names the input's tag.
- A **tag runs a constructor**, and the constructor's plain exception quotes
  the value. Measured on PyYAML 6:
  - `!!int <S>` → `ValueError: invalid literal for int() with base 10:
    '<S>'`;
  - `!!float` → `ValueError`, lowercased;
  - `!!bool` → `KeyError: '<s>'`;
  - `!!timestamp` → `AttributeError`;
  - deep nesting → `RecursionError`;
  - an undefined or duplicate anchor → `ComposerError` quoting its name;
  - duplicate keys are **accepted** (the last one wins).
- `fromisoformat` quotes the string it rejected.
- `tomllib`'s message can quote a key.
- JSON's message is fixed vocabulary, but `doc` holds the whole input, and
  a `bytes.decode` failure quotes the offending byte.

So rev 6's rule, "these three exception types are value-bearing", was a
denylist, and it failed open (round-6 B1). Rev 7 classifies a failure by
**where it happened**:

- **`pmcp.parsing`** is the one module that calls a parser:
  - `load_yaml(stream, *, source)` (`yaml.safe_load`);
  - `load_json(text, *, source, encoding=None)` (`json.loads`; with
    `encoding`, bytes are decoded inside the same classification);
  - `load_json_file(handle, *, source)`;
  - `parse_timestamp(text, *, source)` (`datetime.fromisoformat`).
- Each catches **any** `Exception` and records only three things: the
  line and column (only from a `MarkedYAMLError`'s mark or a
  `JSONDecodeError`'s `lineno`/`colno`), and the failure's class name.
  **After** the `except` block it raises `ParseError(kind, source, line,
  column, cause)`, whose text is `could not parse <KIND> <source> at line L,
  column C (<Class>)`. Raising outside the block means `__context__` and
  `__cause__` are both `None`: no traceback printer, `exc_info` or chain
  walk can reach the original.
- `source` is a fixed label pmcp chose, e.g. `"policy file"`, `"manifest
  overlay"`, `"trust store record"`, `"downstream stdio frame"`, `"npm
  packument"`, `"request body"`. It is never a caller's value, a path
  derived from a request, or a URL. Where a path is useful, the caller's own
  message already carries it, as before.
- **Caller semantics are unchanged**:
  - `YAMLParseError` subclasses `yaml.YAMLError`;
  - `JSONParseError` subclasses `json.JSONDecodeError` (with `doc=""`,
    `pos=0`);
  - all are `ValueError`s, so every existing `except` clause still catches
    what it caught.

  Some clauses now catch more than before, each measured and intended:
  - a tag's `ValueError` or a `RecursionError` is now a `YAMLError`;
  - a `RecursionError` or `UnicodeDecodeError` inside `load_json` is now
    a `JSONDecodeError`;
  - for example, a deeply nested stdio frame now takes the stdio reader's
    `except json.JSONDecodeError` arm (skip the line), where before it
    reached the reader's catch-all ("stdout read error").
- `exception_text` renders a `ParseError` as `str(error)`: it is
  value-free by construction and keeps its source label.
  `_is_parse_error`, the predicate behind `safe_exc_info`, the scrubber
  and the chain checks, excludes it. The raw-type branch
  (`yaml.YAMLError`, `json.JSONDecodeError`, `tomllib.TOMLDecodeError` →
  `could not parse <FORMAT> (<Class>) at line L, column C`) stays for any
  third-party code that parses and raises into pmcp.
- `sys.excepthook`, **`threading.excepthook`** (rev 7) and
  `logging.Handler.handleError` are wrapped by `install_log_scrubber`.
  Each is idempotent and keeps the previous hook. An uncaught chain that
  holds a validation or raw parse error is printed as a real traceback:
  the frames, then `<qualified.Class>: exception_text(...)` for each
  exception. With `sys.stderr` `None` each is silent, like the defaults.
  Anything else goes to the previous hook unchanged.
- **The static rule** (`test_no_parser_call_outside_the_helpers`): no
  module in `src/pmcp` but `parsing.py` references a parser. Imports are
  resolved, including aliases, `from … import … as …`, `import datetime`
  and function-local imports.
  - Any attribute of `json`, `yaml`, `tomllib`, `datetime.datetime` or
    `datetime.date` that is not on a short allowlist is a violation.
  - **Rev 8:** so is any attribute of a *submodule* of `json`, `yaml` or
    `tomllib` (`from yaml.loader import SafeLoader`, `yaml.cyaml`,
    `yaml.constructor`, `json.decoder`) unless its name is safe in the
    package (`JSONDecodeError`, `MarkedYAMLError`, …), and a parser
    package's attribute reached through another object (`policy.yaml.load`).
  - Distinctive parser names are flagged on any receiver, as is `.json(...)`
    with or without arguments (rev 8).
  - 8 exemptions are named with their reason, and must all still exist:
    - python-dotenv (5 calls in 3 functions), which never raises on bad input and logs
      "could not parse statement starting at line N", the line number only
      (measured);
    - a response object's `.json()` (5 calls in 5 functions), which has no
      constructors. Its
      failures are `JSONDecodeError` (rendered structurally),
      `RecursionError` or aiohttp's `ContentTypeError` (the server's MIME
      type), and every one is caught and rendered via `exception_text` or
      swallowed.

### 11. An unparseable `Origin` header (rev 7, B2)

`_origin_host_port` reads `urlparse(origin)`, `.hostname` and `.port` inside
`try/except ValueError`, and returns `None` on failure. Both callers
already treat `None` as unparseable: the DNS-rebinding check rejects it
(403), and the allowlist builder skips it. The case the board found is a
non-numeric port (`Port could not be cast to integer value as '<S>'`); an
out-of-range port and a malformed IPv6 literal take the same path. The
other `urllib.parse` `.port` reads in `src/pmcp` were audited: three in
`auth.py` are guarded, and one in `scoped_advisor_audit.py` is inside
`except (TypeError, ValueError)`. `.hostname` does not raise.

### 12. A rejected JSON-RPC frame on stdio (rev 8, round-7 codex F028–F034; rev 9, round-8 codex P1 and claude N1)

pmcp parses stdio frames itself (`_handle_stdout_line`). Its `except
json.JSONDecodeError` branch has always logged the line raw, as
`Non-JSON output: <line>`, on the reasoning that a line that is not JSON is
the downstream's own output: a banner, a log line. Two things break that:

- **rev 7 widened what reaches the branch.** `load_json` classifies any
  failure as a `JSONParseError`, which is a `JSONDecodeError`. A frame that
  is valid JSON but hits a parser limit (nesting deeper than the
  interpreter's recursion limit; an integer longer than
  `sys.get_int_max_str_digits()`, 4300) raised `RecursionError` /
  `ValueError` on main, which escaped the branch. On rev 7 it was logged
  raw, sentinel and all (measured, `r8/repro.py`).
- **the reasoning never held for JSON-shaped lines.** A truncated or
  otherwise malformed JSON-RPC frame is protocol data, and main logged it
  raw too.

So the branch is split by **what the line is**. Rev 8 let the parser draw
the line (output = rejected at its first significant character). Round 8
showed that wrong: an unterminated string is rejected *at* its opening
quote, so `"<secret>` was shown (codex P1). Rev 9 takes the boundary from
the JSON grammar instead (`_is_downstream_output(text)`):
- The line's first **significant** character is its first that is not
  whitespace (anything `str.isspace` accepts, so everything `.strip`
  removes: NBSP, U+2028, FF, VT …), not a control or format character
  (Unicode Cc/Cf: NUL, ESC, a byte-order mark, ZWSP) and not U+FFFD (an
  undecodable byte, such as a UTF-16/32 BOM).
- If that character can begin a JSON value — `{ [ " - 0-9 t f n` (RFC 8259
  §3), plus `N`/`I` for the `NaN`/`Infinity` Python's `json` accepts — the
  line is **JSON-shaped**. It is a rejected frame, and the record is a
  description:

  ```text
  [<server>] downstream sent a JSON-RPC frame that could not be parsed: could not parse JSON downstream stdio frame at line L, column C (<Class>)
  ```

  The position and class are the parser's, and the content never appears.
  The frame is dropped as before: it resolves no request, and the request
  waits for a well-formed reply or its timeout.
- Otherwise the line is the downstream's **output**, and `_output_text`
  shows only its significant text up to the first `{`, `[` or `"` (where
  frame content can begin), then ` (rest omitted)`. A banner written on the
  same line as a frame (`ready{…}`, `ready\r{…}`, `,{…}`) shows only its own
  text, and the skipped lead is never shown, so `.strip()` cannot uncover a
  bare frame (round-8 claude N1). Digits are not openers: `listening on
  port 8080` is shown whole. A shown line with no opener is shown as it is.

The trade-off: a banner that starts like a value (`true story`, `-v`,
`[INFO] …`, `42 …`, `null pointer`) is described rather than shown, and a
banner's quoted text after a `"` is cut. A stdio downstream's logs belong on
stderr, which is unchanged, so an operator loses little.

The test oracle does not reuse the reader's code or its assumption
(`_json_shaped`, `_carries_no_frame` in the test module):
- it states the grammar's value-start set and the openers itself;
- it expects a description for every JSON-shaped rejected line and a
  shown, opener-free record for every other;
- it exempts no record from the leak check.

The frame sweep's `_non_json_line` exemption applies only when the shown
text holds no opener, whatever the reader decided.

**PR 321 (Consiliency/pmcp#287, merged as `7208815`).** It rewrote this
reader. Parsed frames go through `_dispatch_downstream_frame` →
`_route_downstream_frame`, and `(ValueError, RecursionError)` from
`json.loads` is dropped with a value-free log. Rev 8's change is confined
to the parse and its `except json.JSONDecodeError` arm, so it composes:
- The merge conflict in `_handle_stdout_line` keeps PR 321's structure and
  rev 8's `load_json` call and split. It is the same resolution as a trial
  merge into PR 321's head `2ccf289`, done before it merged: 531 passed,
  including PR 321's own tests.
- PR 321's own test of a bare `[[[[…` line is what showed the first rev 8
  cut too narrow.
- Its `(ValueError, RecursionError)` arm is unreachable after rev 8:
  `load_json` classifies both as `JSONParseError`, which is described.
- PR 321 also made `_read_sse` drop a frame that fails JSON-RPC validation
  instead of ending the loop. That frame's log is type-only, and nothing in
  it renders the frame. The frame sweep's legacy-SSE downstream now follows
  such a frame with the real reply, or the request would only time out.


## Changes

These are the patches under *Verbatim bodies* (`git diff d5a3cda 8d33b49 -- <file>`):
40 files, +6149 / −425. One concern, rendering validation errors from
their structure, is applied at every sink. The bounded-plan threshold of
about 8 files is exceeded on purpose. 19 of the source files carry only the
mechanical §7 substitution plus an import, and splitting them into another
plan would leave the class open between the two merges.

### `src/pmcp/argument_errors.py` (create, +862)
- validator errors (rev 1, unchanged):
  - `CORRELATION_ID_CHARSET`, `SCOPED_CORRELATION_INCOMPLETE`,
    `PACKAGE_NAME_INVALID`;
  - `_PMCP_MESSAGES`;
  - `argument_error()`.
- pydantic (rev 2, N2):
  - `_FIXED_PHRASES` and `_CONSTRAINED_PHRASES` replace `_MODEL_PHRASES`
    and `_CONSTRAINT_CONTEXT`;
  - `_declared_names()`, cached, over every `pmcp.*` model;
  - `_schema_node_at()`, `_constraint_text()`, `_model_phrase(type, node)`;
  - `model_error_path()`, `describe_model_error()`.
- jsonschema (rev 1): `declared_property_names()`, `schema_error_path()`,
  `schema_error_keyword()`, `_schema_phrase()`, `describe_schema_error()`.
- any exception (rev 2, §7): `_chain()`, `_validation_text()`,
  `exception_text()`, `safe_exc_info()`, `safe_traceback_text()`.
- every log record (rev 4, §9): `_scrubbed()`, `scrub_record()`,
  `_scrubbing_factory()` and `install_log_scrubber()` (the record-factory
  scrubber; it replaces rev 3's `mcp.*` logger filter).
- parse errors and uncaught chains (rev 6, §10): `_parse_error_types()`,
  `_is_parse_error()` (rev 7: excludes `ParseError`), `_parse_text()`,
  `_install_excepthook()` (rev 7: silent with no stderr),
  `_install_threading_excepthook()` (rev 7), `_install_handle_error()`;
  `safe_traceback_text()` renders a real traceback with qualified class
  names (rev 7).

### `src/pmcp/__init__.py` (modify, +9 / −0, rev 5)
- installs `install_log_scrubber()` on package import, before any entry
  point's first log call.

### `src/pmcp/server.py` (modify, +84 / −13)
- the gate return renders `describe_schema_error` (rev 1);
- the `call_tool` arm (rev 2): the model rejection is described against the
  tool schema; everything else goes through `exception_text`; the audit
  records `audit.rejection` for a model rejection; the unknown-tool log line
  is fixed text;
- three startup/shutdown sinks go through `exception_text`;
- `GatewayServer.__init__` calls `install_log_scrubber()` (rev 4).

### `src/pmcp/tools/handlers.py` (modify, +54 / −30)
- `provision_status` validates before its `try` (rev 1);
- 26 sinks go through `exception_text` / `safe_exc_info` (rev 2). These are
  the `tasks_*` audit `error=` and `errors`, `auth_connect`, provisioning,
  handoff, the registry and the update probe.

### `src/pmcp/client/manager.py` (modify, +122 / −20)
- `describe_exception` leaves go through `exception_text`;
- `_connect_all_unlocked` and `_fetch_server_listings` messages go through
  `exception_text`;
- the cancellation-unwind traceback goes through `safe_traceback_text`;
- `_downstream_error` gives every `-32700`, and every malformed `error`,
  fixed text (rev 3, B1), using `_PARSE_ERROR`, `_PARSE_ERROR_MESSAGE` and
  `_MALFORMED_ERROR_MESSAGE`;
- `install_log_scrubber()` runs at import (rev 4);
- rev 8/9: `_handle_stdout_line` describes a rejected line that could be
  JSON (`exception_text` of the `JSONParseError`), and shows any other only
  up to its first frame opener (§12); rev 9's `_JSON_VALUE_START`,
  `_FRAME_OPENERS`, `_carries_nothing`, `_significant_start`,
  `_is_downstream_output(text)` and `_output_text`.

### `src/pmcp/auth.py` (modify, +10 / −5)
- `sanitize_auth_diagnostic` renders an exception with `exception_text`.

### `src/pmcp/scoped_advisor_audit.py` (modify, +22 / −69, rev 1)
- the path helpers moved to `argument_errors`;
- `record_rejected_arguments` accepts a pydantic error.

### `src/pmcp/types.py` (modify, +11 / −10, rev 1)
- the three validators raise `argument_error(...)`.

### Mechanical §7 sinks: `exception_text` / `safe_exc_info` plus the import (modify, rev 2)
- `config/guidance.py`, `config/loader.py`;
- `manifest/code_patterns_loader.py`, `manifest/environment.py`,
  `manifest/installer.py` (including `task.exception()` and `exc_info`),
  `manifest/loader.py`, `manifest/npm_resolver.py`,
  `manifest/package_identity.py`, `manifest/refresher.py`,
  `manifest/version_checker.py`;
- `package_approvals.py`, `policy/policy.py` (including
  `_warn_unparseable`), `provision_gate.py`;
- `subscriptions.py` (`logger.exception` → `logger.error(...,
  exc_info=safe_exc_info(exc))`);
- `templates/code_snippets_loader.py`, `trust_store.py`.

### `tests/test_argument_error_echo.py` (create, +1504)
- rev 1: the argument sweep, handler-past-the-gate, and renderer unit tests;
- rev 2 additions:
  - `_FAMILIES`, `_Tap` / `_Observed` / `_record_text`;
  - the N2 custom-error test and the wrapper test;
  - the static sink guard, which rev 3 moves to its own module;
  - `test_every_decorated_baseline_passes_the_gate`;
  - the downstream task sweep and the listing sweep;
  - rev 3: the connect-retry test.

### `tests/test_exception_text_sinks.py` (create, +771, rev 3; rev 4; rev 6; rev 7; rev 9)
- the dataflow-aware scanner (`exception_sinks`);
- the whole-`src/pmcp` check, asserted non-vacuous;
- 47 construct tests and a renderer-passes test (rev 4 adds a closure
  inside an `except` and an asyncio loop handler's `context['exception']`,
  inline and aliased);
- rev 9: rev 8's `_is_downstream_output` entry is gone from the read
  callees, since the predicate no longer receives the exception.

### `tests/test_downstream_frame_echo.py` (create, +1006, rev 3; rev 4; rev 8; rev 9)
- `_Downstream` (a streamable-HTTP and legacy-SSE server in one) and a
  stdio script, sharing their reply logic;
- 4 transports × 11 requests, each over 5 shapes × 3 families × 2 lengths;
- rev 4: 9 malformed `ClientSession` messages through the real
  `refresh_server`;
- rev 8: the `json-deep`, `json-bigint` and `json-syntax` shapes on every
  transport, with stdio's rejected line followed by the real reply; the
  `_non_json_line` exemption narrowed (rev 9: only a shown text with no
  frame opener);
  `_rejected_frames` (grammar-derived) and
  `test_no_rejected_json_rpc_frame_reaches_the_log`; the legacy-SSE
  downstream follows a rejected frame with the real reply (PR 321 drops
  such a frame instead of ending the loop);
- rev 9: the grammar oracle (`_GRAMMAR_VALUE_START`, `_json_shaped`,
  `_carries_no_frame`), `_line_cases` (628 lines per family),
  `test_no_stdio_line_shows_frame_content` and
  `test_a_banner_stays_useful_on_stdio`.

### `tests/test_parse_error_echo.py` (create, +875, rev 6; rev 7; rev 8)
- rev 7: the helpers-only static rule (`test_no_parser_call_outside_the_helpers`,
  8 named exemptions) and its detector pinned on 30 spellings and 8
  non-parsers (rev 8: parser submodules, `<x>.yaml.load`, `.json(...)` with
  arguments);
- the 15-site × 6-family dynamic sweep, with rev 7's tag and anchor inputs,
  any-exception rejection and any-case sentinel;
- rev 7: the helpers swept directly (`load_yaml`, `load_json`), the 3
  timestamp sites, a `ParseError` rendered as its own text, duplicate keys
  measured as data;
- the fatal policy printed uncaught, in a fresh interpreter; rev 7: an
  uncaught chain holding a raw YAML error or a pydantic error, a thread's
  uncaught chain, and the hooks with no stderr;
- a failing log handler while a parse error is handled;
- the renderer's raw-parse wording.

### `src/pmcp/parsing.py` (create, +156, rev 7)
- `ParseError` (value-free text, pickles by its fields) and
  `YAMLParseError` (also a `yaml.YAMLError`), `JSONParseError` (also a
  `json.JSONDecodeError`, `doc=""`), `TimestampParseError`;
- `load_yaml`, `load_json`, `load_json_file`, `parse_timestamp`: any
  exception → a `ParseError` raised outside the `except` (§10).

### Parse sites routed through `pmcp.parsing` (modify, rev 7)
Each call keeps its caller's `except` clause; the `source` label is fixed text:
- YAML: `policy/policy.py` (`policy file`), `manifest/loader.py`
  (`manifest`, `manifest overlay`), `config/guidance.py` (×3),
  `manifest/code_patterns_loader.py`, `templates/code_snippets_loader.py`,
  `manifest/refresher.py`;
- JSON: `policy/policy.py` (×2), `config/loader.py` (×2), `trust_store.py`,
  `package_approvals.py`, `scoped_advisor_audit.py`, `auth.py` (×4, JWKS
  and metadata bodies with `encoding="utf-8"`), `feedback_egress.py` (×2),
  `manifest/registry.py` (×2), `manifest/package_identity.py`,
  `manifest/npm_resolver.py` (×2), `cli.py` (×3), `tools/handlers.py` (×2),
  `client/manager.py` (the stdio frame), `transport/http.py` (the request
  body's `method` probe), `redaction_additive.py`;
- timestamps: `trust_store.py`, `package_approvals.py`, `types.py`
  (`McpTaskInfo`'s validator).

### `src/pmcp/transport/http.py` (modify, +18 / −7, rev 7)
- `_origin_host_port`: a malformed `Origin` authority returns `None`
  (403), not a 500 (§11, B2);
- the body `method` probe goes through `load_json`.

### `tests/test_http_transport.py` (modify, +46 / −0, rev 7)
- `test_malformed_origin_port_is_rejected_without_echo` × 3 configurations.

### `src/pmcp/cli.py` (modify, +14 / −12, rev 6)
- 8 sinks go through `exception_text` / `safe_exc_info` (the CLI is now in
  the static guard).

### `src/pmcp/manifest/loader.py` (modify, +11 / −6)
- rev 2's two sinks, plus rev 6's overlay `except yaml.YAMLError` sink;
- rev 7: both YAML parses through `load_yaml`; rev 8: main's new
  `_shipped_manifest_entries` (Consiliency/pmcp#294) through `load_yaml` too.

### `tests/test_log_record_scrubber.py` (create, +342, rev 4; rev 5)
- every branch of `scrub_record`, on 8 logger names; the factory's install;
- rev 5: every entry point, in a fresh interpreter; the real `pmcp refresh`.

### `tests/test_scoped_advisor_audit.py` (modify, +95 / −176, rev 1; rev 7)
- the foreign-line exclusions are removed;
- `_META_REJECTION` and `_assert_against` are added;
- rev 7: `_EXCEPTION_ARGS` gains `(kind, source)` for the three `ParseError`
  classes, which its every-raised-exception enumeration now finds.

### `tests/test_gateway_tool_schemas.py` (modify, +7 / −3, rev 1)
- the three fragments use the new wording.

## Documentation impact
- `CHANGELOG.md` — `[Unreleased]` → `### Fixed`, first entry — modify:
  - rev 1's fix, the wording change and the audit disposition;
  - rev 2's sentence that the rule holds wherever pmcp renders an exception
    (responses, logs and tracebacks, the audit-event buffer, `tasks_*`
    `errors`), including downstream data and the operator's own config
    files;
  - rev 3's sentence on malformed downstream frames, and rev 4's on
    scrubbing every log record at creation;
  - rev 6/7's sentence on parse errors (now naming tags, anchors and
    timestamps) and rev 7's on the `Origin` port.
- `README.md` (scoped-advisor audit paragraph) — modify (rev 1).

## Dependencies & order
Apply all 40 patches together; they are one `git apply`.
`pmcp.argument_errors` and `pmcp.parsing` import no `pmcp` module at load time (`_is_parse_error` imports `pmcp.parsing` lazily).
`_declared_names` imports `pmcp.types` lazily, so there are no import
cycles: `pmcp.types`, `auth`, `client.manager` and the others import it at
module level, and `python -c "import pmcp.server, pmcp.cli"` succeeds. No
migration, no config.

## Verification

```bash
# from a fresh worktree of origin/main @ d5a3cda (dev0: team host)
uv sync --all-extras -p 3.10
# apply (see *Verbatim bodies -> How to apply*)
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest tests/test_exception_text_sinks.py tests/test_argument_error_echo.py tests/test_downstream_frame_echo.py tests/test_log_record_scrubber.py tests/test_parse_error_echo.py tests/test_scoped_advisor_audit.py tests/test_gateway_tool_schemas.py tests/test_http_transport.py -q
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir
uv run pytest -m 'not live and not slow' -q
```

Red on main (and on rev 7's code `dd3f707`, rebased as `bf993e9`): copy the eight test files at `8d33b49` onto a clean `d5a3cda`
tree and run the same modules. The result is under *Acceptance
criteria*.

## Acceptance criteria — measured this session (on `8d33b49`, red on `d5a3cda` and on rev 8's code `2d9e736`)

- [x] **Caller arguments**:
  `test_no_rejected_argument_value_reaches_a_response_log_or_audit`,
  2 passed. Red on main: `('hex', '$.auth_mode:type-object', ...)`.
- [x] **Downstream payloads through the real handlers** (rev 2): the task
  and listing sweeps pass. Both are red on main.
- [x] **Malformed frames on every transport** (rev 3):
  `test_no_malformed_frame_value_reaches_pmcps_output`, 44 passed. Red on
  main: 44 failed.
- [x] **The SDK's `ClientSession`, through the real `refresh_server`**
  (rev 4): `test_no_malformed_session_message_value_reaches_the_log`,
  9 passed. Red on main: 7 failed.
- [x] **Every record scrubbed, every branch pinned** (rev 4):
  `tests/test_log_record_scrubber.py`, 68 passed. On main the unit tests
  cannot import `pmcp.argument_errors`.
- [x] **Every entry point installs the scrubber, and `pmcp refresh` logs no
  downstream value** (rev 5): 6 and 3 passed. Red on main: all 9 fail
  (`('import pmcp', 'False` ...; `('notifications/message', 'hex',
  'stderr', ...`).
- [x] **Every exception-to-text sink goes through the renderer**, now
  including the CLI and the parse errors (rev 6):
  `tests/test_exception_text_sinks.py`, 49 passed (47 constructs). On main
  the `src/pmcp` check fails with 89 findings.
- [x] **No parse error echoes its input, whatever raised it** (rev 6;
  rev 7): `tests/test_parse_error_echo.py`, all pass. Red on main: every
  YAML site leaks, the JSON sites fail on wording, and the helpers module
  is missing. **On rev 6's code (`9b24daa`, measured in rev 7 of this
  plan, `4d4b186`): 74 failed in this module:**
  - the sweep, 48 = 8 YAML sites × 6 families, each on `!!int` (on
    `!!bool` for digits);
  - the 6 timestamp cases, on `Invalid isoformat string: '<S>'`;
  - 14 tests of `pmcp.parsing`, which rev 6 does not have: the 12
    direct helper sweeps, the `ParseError` rendering and duplicate keys;
  - the helpers-only rule;
  - the thread hook and the no-stderr hook;
  - the 2 uncaught-chain cases, on wording only: rev 6 printed the
    unqualified class name;
  - the fatal policy's new wording.
- [x] **No stdio line shows frame content** (rev 8; rev 9, round-8 codex
  P1 and claude N1). The direct tests are
  `test_no_stdio_line_shows_frame_content` (6 families × 628 lines: every
  value-start, unterminated strings, literal prefixes and 12 prefix classes
  behind 15 leads, BOMs and UTF-16/32, CR-only, multiple frames) and
  `test_no_rejected_json_rpc_frame_reaches_the_log` (6 × 1060–1252
  grammar-derived frames). The frame sweep adds its `json-*` shapes on
  every transport. No record is exempt unless what it shows holds no frame
  opener. `test_a_banner_stays_useful_on_stdio` keeps 7 operator controls.
  Red on rev 8's code: see below.
- [x] **The static rule sees parser submodules** (rev 8, claude (3)): 30
  spellings detected, 8 non-parsers passed.
- [x] **An unparseable `Origin` is a 403 without echo** (rev 7, B2):
  `test_malformed_origin_port_is_rejected_without_echo`, 3 passed. Red on
  main: 3 failed, `assert 500 == 403` (and on rev 6, rev 7's measurement).
- [x] **Gates and the full suite are clean** (lines under *Full suite and
  gates*).

Red on main: the eight test files @ `8d33b49` on a clean `d5a3cda`,
`pytest ... -q --tb=line`. Failures are counted per test function, followed
by the first assertion line per module and the summary line. The errors are
`test_log_record_scrubber.py`'s fixture, which imports
`pmcp.argument_errors`.

```text
# failing (or erroring) tests on main d5a3cda, by module
     34 tests/test_argument_error_echo.py
     64 tests/test_downstream_frame_echo.py
      1 tests/test_exception_text_sinks.py
      3 tests/test_gateway_tool_schemas.py
      3 tests/test_http_transport.py
     68 tests/test_log_record_scrubber.py
     99 tests/test_parse_error_echo.py
      6 tests/test_scoped_advisor_audit.py

# the first assertion line per module
tests/test_exception_text_sinks.py:671: AssertionError: cli.py:939: FormattedValue uses e
tests/test_argument_error_echo.py:690: AssertionError: ('hex', '$.auth_mode:type-object', _Observed(response="Input validation error: {'Sqfee3b693849c460ce4e728Zx': 'Sqfe
tests/test_downstream_frame_echo.py:548: AssertionError: ('http-json', 'initialize', 'result-type', 'hex', _Observed(response='["Failed to connect to frames: Failed to pa
tests/test_log_record_scrubber.py:163: ModuleNotFoundError: No module named 'pmcp.argument_errors'
tests/test_parse_error_echo.py:237: AssertionError: {'auth.py::_fetch': ['453: json.loads'], 'auth.py::parse_url_elicitation_error': ['808: json.loads', '814: json.loads'
tests/test_scoped_advisor_audit.py:1696: AssertionError: ('top-level correlation', ['[1969-12-31T19:00:00] [ERROR] Tool execution error: 1 validation error for InvokeInpu
tests/test_gateway_tool_schemas.py:468: AssertionError: Input validation error: '' should be non-empty
tests/test_http_transport.py:521: assert 500 == 403

221 failed, 547 passed, 57 errors in 56.63s
```

Red on rev 8: the same eight files on `2d9e736` (rev 8's code). There are
19 failures:
- 6 in `test_no_stdio_line_shows_frame_content`, each on a leak, the first
  on codex's own case;
- 1 in `test_a_banner_stays_useful_on_stdio`;
- 11 in the frame sweep's stdio methods, on the `not-json` shape. Its line
  `<S> is not JSON {` holds an opener, so rev 9's narrowed exemption no
  longer excuses rev 8's unshortened record (claude N1's class);
- 1 in the sink guard. That one is by design: rev 8 passes the exception
  to its predicate, and rev 9's guard no longer lists that callee as a
  reader.


```text
# failing tests on rev 8's code 2d9e736, by test function
      1 FAILED tests/test_downstream_frame_echo.py::test_a_banner_stays_useful_on_stdio
     11 FAILED tests/test_downstream_frame_echo.py::test_no_malformed_frame_value_reaches_pmcps_output
      6 FAILED tests/test_downstream_frame_echo.py::test_no_stdio_line_shows_frame_content
      1 FAILED tests/test_exception_text_sinks.py::test_no_exception_reaches_text_except_through_the_renderer

# the first assertion line per module
tests/test_exception_text_sinks.py:671: AssertionError: client/manager.py:2909: Call uses error
tests/test_downstream_frame_echo.py:548: AssertionError: ('stdio', 'initialize', 'not-json', 'hex', _Observed(response='[]{

19 failed, 806 passed in 201.01s (0:03:21)
```

Green (patched): `825 passed in 176.36s (0:02:56)` for the eight modules.

## Mutation evidence

`mutants.py` (below) runs on a worktree of `8d33b49`:
- It applies each mutant; the anchor must occur exactly once.
- It runs the eight test modules with `-x --tb=line`, the dynamic sweeps
  first and the static guard last, so the named reason is the first
  **dynamic** failure where there is one.
- It refuses to start a mutant on a file that already differs from `HEAD`
  (rev 7: a killed run must not have its mutant saved as the clean copy).
- It restores the file **from a copy saved before the mutation** and
  `cmp`-checks it. `git status --short | wc -l` was `0` after each pass.

Every mutant applied and went red:
- M1–M22 are revs 1–2's, re-run.
- M23–M26 and G1 are new for the rev 2 board: G1 is the seat's surviving
  `_connect_with_retry` regression.
- M31 is new for the rev 4 board (the install removed from
  `pmcp/__init__.py`).
- M32–M35 are new for the rev 5 board: the YAML branch removed, parse
  errors not value-bearing, no excepthook, the CLI refresh line back to
  `{e}`; M36 (no `handleError` wrapper) too.
- M37–M45 are new for the round-6 board:
  - M37 is **"helper bypassed at one site"**: the policy YAML parse goes
    back to a direct `safe_load`;
  - M38 is **"B2 guard removed"**;
  - M39 has the helper classify by type again (`except yaml.YAMLError`);
  - M40 raises the `ParseError` inside the `except`, so it is chained;
  - M41 renders a `ParseError` as a raw parse error;
  - M42 puts the trust-store timestamp back on `fromisoformat`;
  - M43 drops the `threading.excepthook` wrapper;
  - M44 writes to a `None` stderr;
  - M45 prints the unqualified class name.
- M46 and M48 are new for the round-7 board:
  - M46 logs a rejected stdio frame raw again (the fix removed);
  - M48 makes the static rule ignore parser submodules (a mutant of the
    test's own detector).

  Rev 8's M47 and M49 mutated code that rev 9 replaced, and are retired.
- M50–M53 are new for the round-8 board:
  - M50 drops the quote from the value-start set (codex P1's case);
  - M51 shows output untruncated (claude N1);
  - M52 stops skipping control and format characters;
  - M53 skips only ASCII whitespace, so NBSP, U+2028, FF and VT are not
    skipped.
- **The rev 7 run found a gap, now closed.** Its first run (on `a947538`)
  had **M34** ("no excepthook installed") surviving both passes. Since a
  pmcp parse failure now chains nothing, the fatal-policy test no longer
  gives `sys.excepthook` a value-bearing chain, and no other test did.
  `test_an_uncaught_chain_prints_no_input` (a raw YAML error and a pydantic
  `ValidationError`, each chained under an uncaught `RuntimeError`, in a
  fresh interpreter) was added. The run below is on `8d33b49`, with it in
  place.
- M27–M30 are new for the rev 3 board. M24 is now "no record scrubber
  installed".
- M27 was first written (rev 4) as `record.name.startswith("mcp")`,
  which crashed on a record whose `name` is `None` instead of failing an
  assertion. It now uses `str(record.name)`, and this run is of the
  corrected mutant: it is killed by the `ClientSession` sweep (the
  `"client"` logger).

The first pass, condensed to fit the size budget: the killed list, then
the full line of each rev 8/9 stdio and static mutant. The full per-mutant
lines of the others, all killed on the same reasons, are in rev 8 of this
plan (`4bafa47`), and each mutant's log is kept with the run.

```text
56 mutants applied; 56 killed: M1 M2 M3 M4 M5 M6 M7 M8 M9 M10 M11 M12 M13 M14 M15 M16 M17 M18 M19 M20 M21 M22 M23 M24 M25 M26 M27 M28 M29 M30 M31 M32 M33 M34 M35 M36 M37 M38 M39 M40 M41 M42 M43 M44 M45 M46 M50 M51 M52 M53 M48 G1 S5 S6 S7 S8
M46 stdio: a rejected frame logged raw again: applied=yes exit=1 | 1 failed, 94 passed in 105.43s (0:01:45) | E   AssertionError: ('stdio', 'initialize', 'json-deep', ...
M50 stdio: the quote excluded from where JSON starts: applied=yes exit=1 | 1 failed, 114 passed in 124.67s (0:02:04) | E   AssertionError: (1057, 1060)
M51 stdio: shown output not truncated at a frame opener: applied=yes exit=1 | 1 failed, 94 passed in 107.77s (0:01:47) | E   AssertionError: ('stdio', 'initialize', 'n...
M52 stdio: control and format characters not skipped: applied=yes exit=1 | 1 failed, 114 passed in 122.37s (0:02:02) | E   AssertionError: (795, 1060)
M53 stdio: Unicode whitespace not skipped: applied=yes exit=1 | 1 failed, 120 passed in 124.61s (0:02:04) | E   AssertionError: ("'\\xa0' value-start '{'", ['[srv] Non...
M48 static check ignores parser submodules: applied=yes exit=1 | 1 failed, 218 passed in 136.21s (0:02:16) | E   AssertionError: from yaml.loader import SafeLoader
```

The first `E` line the script prints is sometimes a traceback line rather
than the assertion; each mutant's full log names the failing test (see
rev 6, `8d03712`, for M18, M21 and G1).

Second pass, with `NO_STATIC=1` (both static checks deselected: the
sink guard and rev 7's helpers-only rule), all mutants (the killed list,
the survivors in full, and the two rev 7 helper-bypass mutants):

```text
56 mutants applied; 54 killed with both static checks deselected: M1 M2 M3 M4 M5 M6 M7 M8 M9 M10 M11 M12 M13 M14 M15 M16 M17 M18 M19 M20 M21 M23 M24 M25 M26 M27 M28 M29 M30 M31 M32 M33 M34 M36 M37 M38 M39 M40 M41 M42 M43 M44 M45 M46 M50 M51 M52 M53 M48 G1 S5 S6 S7 S8
survived: M22 installer crash message uses raw exc (static guard): applied=yes exit=0 | 823 passed, 2 deselected in 160.91s (0:02:40) | 
survived: M35 CLI refresh logs the raw exception: applied=yes exit=0 | 823 passed, 2 deselected in 155.72s (0:02:35) | 
M37 helper bypassed at one site (policy YAML): applied=yes exit=1 | 1 failed, 233 passed, 2 deselected in 160.21s (0:02:40) | E   AssertionError: ('policy yaml, warn',...
M42 timestamp helper bypassed (trust store): applied=yes exit=1 | 1 failed, 339 passed, 2 deselected in 138.70s (0:02:18) | E   AssertionError: Unparseable trust times...
M46 stdio: a rejected frame logged raw again: applied=yes exit=1 | 1 failed, 94 passed, 2 deselected in 106.04s (0:01:46) | E   AssertionError: ('stdio', 'initialize',...
M50 stdio: the quote excluded from where JSON starts: applied=yes exit=1 | 1 failed, 114 passed, 2 deselected in 124.77s (0:02:04) | E   AssertionError: (1057, 1060)
M51 stdio: shown output not truncated at a frame opener: applied=yes exit=1 | 1 failed, 94 passed, 2 deselected in 107.83s (0:01:47) | E   AssertionError: ('stdio', 'i...
M52 stdio: control and format characters not skipped: applied=yes exit=1 | 1 failed, 114 passed, 2 deselected in 122.58s (0:02:02) | E   AssertionError: (795, 1060)
M53 stdio: Unicode whitespace not skipped: applied=yes exit=1 | 1 failed, 120 passed, 2 deselected in 125.33s (0:02:05) | E   AssertionError: ("'\\xa0' value-start '{'...
```

- Every mutant but M22 and M35 dies on dynamic tests alone, including
  M11, M19, M20, **G1**, and rev 7's **M37** (the helper bypassed: the tag
  sweep, with the helpers-only rule deselected) and **M42** (the timestamp
  sweep).
- **M22 survives by design.** No sweep drives a crashing install task, so
  the static guard is the only check on that sink. That is what the static
  half is for; in the first pass it kills M22
  (`manifest/installer.py:256: FormattedValue uses exc`).
- **M35 survives by design** too: no sweep drives `pmcp refresh` to a
  failure. In the first pass it is killed (`cli.py:940: FormattedValue
  uses e`).

Reasons, by what caught them:
- **response**: M1, M2, M8, M12, M17, S8;
- **log**: M3, S5 (`extra=`), S6 (stderr), S7 (warnings), G1;
- **audit**: M4, M14;
- **real handlers, downstream payloads**: M15, M19, M20;
- **real transports, malformed frames**:
  - M23 (the `-32700` passthrough: the response);
  - M24 (no scrubber: the SDK's traceback in the log);
  - M25 (a non-string `message` kept: stdio);
  - M26 (the scrubber keeps `exc_info`);
- **entry points**: M31 (`import pmcp` no longer installs the scrubber);
- **parse errors**: M32 (YAML branch removed), M33 (parse errors not
  value-bearing), M34 (no excepthook: the fatal policy, uncaught), M36
  (no `handleError` wrapper: a failing handler);
- **origin classification (rev 7)**:
  - M37, a site bypasses the helper: the tag sweep catches it, and
    separately the static rule does;
  - M39, type-based classification;
  - M40, the `ParseError` chained;
  - M41, rendered as a raw parse error;
  - M42, the timestamp;
  - M43–M45, the hooks and class names;
- **`Origin` header (rev 7)**: M38;
- **rejected stdio frames (rev 8)**: M46 (the grammar-derived test and the
  frame sweep's `json-*` shapes); **static detector**: M48;
- **where JSON starts, and what output shows (rev 9)**, as measured, in
  both passes:
  - M50: 3 of the 1060 grammar-derived frames, those that lost their
    opening brace and so start with `"jsonrpc"`, are no longer described
    (`(1057, 1060)`); codex's own unterminated-string case fails the same
    way in `test_no_stdio_line_shows_frame_content`;
  - M51: the frame sweep's stdio `not-json` line is shown whole, with its
    `{`;
  - M52: the 265 byte-order-mark frames are no longer described
    (`(795, 1060)`);
  - M53: an NBSP-prefixed frame is shown as `Non-JSON output:  (rest
    omitted)` instead of described.
- **static guard only**: M35 (a CLI sink no dynamic test drives; it
  survives the second pass by design, like M22);
- **record scrubber branches**: M27 (a non-`mcp` logger), M28 (`%`-args),
  M29 (`msg` an exception), M30 (nested containers);
- **behaviour**: M10;
- **wording**: M9, M13;
- **unit tests**: M5, M6, M7 (N2), M16 (group), M18, M21 (wrapper);
- **static guard**: M11 (also #296's pair differential), M22.

## Non-goals

- **Echo of accepted values: filed as Consiliency/pmcp#315** (coordinator,
  rev 1 board). Its text as proposed, with rev 3's addition (N3) at the
  end:

  > **Accepted argument values are echoed into responses and logs.**
  > Consiliency/pmcp#297 stops *validation errors* from echoing what they
  > rejected. A value that *passes* validation, such as a token put in the
  > wrong string field, is still echoed wherever a handler looks it up,
  > refuses it or reports on it. Most of these are also returned in a
  > structured output field by design. Sites on `7680445`:
  >
  > - **routing names**:
  >   - `server.py:359` (`Gateway tool blocked by policy: {name}`);
  >   - `server.py:380/:444` (`Unknown tool: {name}`, response only since
  >     #297);
  >   - `server.py:582/:609/:613/:683` (`resources/read`/`prompts/get`
  >     unknown or blocked URI/name, which the SDK also logs with a
  >     traceback: `mcp/shared/jsonrpc_dispatcher.py:754`);
  >   - `client/manager.py:1277` (`Unknown server`, when lazy).
  > - **lookups / policy refusals**:
  >   - `handlers.py:1341/:1359` (describe, `details` only);
  >   - `:1510/:1543/:1569` (`make_error(tool_id=...)` in `errors`);
  >   - `:2426/:2541/:3010/:3083/:3192` (`Server '{server_name}' ...`, and
  >     into the audit `error` via `_lifecycle_output` `:2898-2908`);
  >   - `:3888/:4044`, `:5585` (`Job '{job_id}' not found`);
  >   - `tasks_*` blocked-by-policy `:5938/:6021/:6065/:6157`;
  >   - `client/manager.py:3847/:3896` (`Task not found: {server}::{task_id}`).
  > - **a caller's header echoed into a log** (rev 5, N2):
  >   - the SDK's INFO `Rejected request with unknown or expired session
  >     ID: {id[:64]}` (`mcp/server/streamable_http_manager.py:364`);
  >   - pmcp's DEBUG `handle_mcp [%s]: %s method=%s session=%s accept=%r`
  >     (`transport/http.py:495`).
  > - **a rejected downstream value in pmcp's own warning** (rev 3, N3):
  >   `client/manager.py:2031-2035`, `{kind}/list returned an unusable
  >   cursor ({raw_cursor!r})`. Render `type(raw_cursor).__name__` instead.
  > - **format / allowlist refusals of a schema-valid string**:
  >   - `handlers.py:4549/:4558` (`Env var '{env_var}' is not permitted`);
  >   - `:4567-4581` via `env_store.py:23` (`{name!r}`);
  >   - `:5410` (`unsafe package identifier {package!r}`);
  >   - `:5427-5433` (disallowed `env_vars`, `operator_safe`);
  >   - `client/manager.py:4208/:4217` (`Invalid request_id format:
  >     {request_id}`, `Invalid local_id`);
  >   - `config/loader.py:778-780/:854` (the policy `path`).
  > - **free text echoed by design**:
  >   - `refresh` `reason` (`handlers.py:1857`, INFO log);
  >   - `request_capability` `query` (`:3860` log, `:3866/:3869` output);
  >   - `search_registry` `query` (`:5384`);
  >   - `submit_feedback` title/description (`:4681-4682`).
  > - **invoke's own log lines** name `tool_id` (`:1657/:1688/:1734/:1817`).
  >
  > Proposed class fix: give identifier fields a pattern so token-shaped input
  > fails validation (and is then described without its value by #297). For
  > example `tool_id` can reuse `scoped_advisor_audit._TOOL_ID_PATTERN`; add
  > patterns for `server_name`, `task_id`, `job_id`, `request_id` and
  > `env_var`. Then route the routing-name log lines through a fixed text as
  > #297 did for `Unknown tool`. This changes advertised schemas, so it
  > belongs with, or after, piece B of Consiliency/pmcp#236.

- **N5, `resources/read` / `prompts/get`**: this is part of the follow-up
  above, not this plan. It is a lookup echo, not a validation error. The
  SDK writes the traceback for any exception a request handler raises, so
  silencing it means raising `MCPError`, which changes the wire error code.
- **N4, the SDK's malformed-envelope echo** (`streamable_http.py:550`):
  SDK-owned. Only the sender sees it, and tool arguments cannot trigger it.
  The fix belongs upstream: the SDK's own `jsonrpc_dispatcher.py:100-101`
  already maps a `ValidationError` to `data: ""`.
- **N7, `isError: false` on the `call_tool` arm**: pre-existing. Changing it
  changes every handler exception's wire shape.
- **Downstream data that pmcp accepts** (a task's fields, a tool's
  description) is returned or indexed by design. Only a *rejected*
  downstream value is this plan's subject. The listing parsers' own
  `_entry_label(entry)` (`client/manager.py:586-598`) logs a downstream
  entry whose identity is unusable, by design, for diagnosis; that is not a
  validation error's text.
- **The operator's CLI** is no longer exempt (rev 6). It is inside the
  static guard like every other module, since `pmcp refresh` and the
  config commands read files and downstreams.
- **A downstream's own error message** (a well-formed JSON-RPC `message`
  string) is returned and logged as before (rev 3, §9). It is the
  downstream's own text about its failure, like its result, and
  Consiliency/pmcp#234 redacts it where pmcp renders it. Only an SDK- or
  downstream-produced `-32700`, and a malformed `error`, are replaced.
- **Downstream-authored text the SDK quotes** (rev 3 board N1/N2):
  `Unexpected content type: <header>` (`mcp/client/streamable_http.py:387-388`)
  and `Unknown SSE event: <name>` (`:195`) are kept, by design (§9). No
  caller value can reach them.
- **stdio's own output**: a stdout line that is output by §12's rule
  (its first significant character cannot begin a JSON value;
  `client/manager.py` `_handle_stdout_line`, DEBUG `Non-JSON output: ...`)
  is shown up to its first `{`, `[` or `"`. Every stderr line
  (`_read_stderr`) is logged as the downstream's own output, for diagnosis.
  A line with no frame opener is shown whole. A rejected line that could
  be JSON is described, never shown (rev 9, §12).
- **The SDK's DEBUG logging of accepted frames** (round-8 claude N2):
  `mcp/client/sse.py:99/:123` and `streamable_http.py:165/:562` log every
  accepted frame and the outgoing arguments. These are accepted values,
  so they belong to Consiliency/pmcp#315.
- **Pre-existing, operator/config-only echoes found by the round-7 claude
  seat: added to Consiliency/pmcp#315 by the coordinator, not #297's
  scope.**
  - N1: `config/loader.py:494` logs a rejected `allowPrivateRegistry` value
    with `%r`.
  - N2: `PMCP_PORT`, `PMCP_MAX_SPAWNS`, `PMCP_RATE_LIMIT` and
    `PMCP_REQUEST_TIMEOUT` go through a bare `int()`, and
    `client/manager.py`'s two warnings print `PMCP_STDIO_READ_LIMIT` /
    `PMCP_REQUEST_CEILING_MS` with `%r`.
- **`RecursionError` from `jsonschema.validate` on in-process nesting**
  (#296's E12): unchanged.
- **Piece B.** B's plan must re-run this sweep (see *Unverified*).

## Unverified

- **Under piece B** the sweep's extra-key decorations become
  `additionalProperties` rejections. jsonschema's `best_match` is expected
  to prefer the deeper failing error, so the path assertions should hold,
  and an `additionalProperties`-only failure reads `$...: has a property
  that is not accepted` without the key. The latter is unit-tested; the
  former is not measured.
- **The SDK's f-string log lines.** Two SDK lines render an exception into
  the message itself: `mcp/client/streamable_http.py:532` (`Reconnection
  failed: {e}`) and `:636` (`Session termination failed: {exc}`). The
  scrubber cannot see a validation error inside an already-formatted
  message. Both are HTTP-transport failures (httpx), not frame parsing, and
  the frame sweep did not reach them with a value. This is upstream's.
- **A log handler that fails while an `except` block is active** — fixed
  in rev 6. `logging.Handler.handleError` printed the active exception's
  chain to stderr ("--- Logging error ---"). The full suite hit it again in
  rev 6 through the new parse sweep, after a CLI test left a broken file
  handler behind. `install_log_scrubber` now wraps `handleError`: a chain
  holding a validation or parse error is printed by `safe_traceback_text`,
  and everything else by the original method (`raiseExceptions` is still
  honoured). Test: `test_a_failing_log_handler_prints_no_input`; mutant
  M36.
- **JSON parse errors on main were not a leak** in their text (fixed
  vocabulary), which is why the JSON sites are red on main only on wording.
  Since rev 7 they go through `load_json` like every other parse.
- **Scalar conversions outside the parse helpers.** `int(s)`, `float(s)`,
  `ipaddress.ip_address(s)` and `UUID(s)` also quote what they reject. The
  round-6 finding and rev 7's rule are about structured-text parsers.
  Inside a pydantic validator such an error becomes a `ValidationError`,
  which is rendered structurally. Elsewhere in `src/pmcp` (about 250
  `int(`/`float(`/`ip_address(`-shaped calls) they were not audited one by
  one as a class. The ones on request data seen while auditing B2 are
  guarded or swallowed (`ip_address` in `scoped_advisor_audit.py` and
  `transport/http.py`), but that is a sample, not a proof.
- **Dynamic parser access** (`getattr(yaml, name)`, `importlib`) is not
  seen by the static rule. None exists in `src/pmcp` today (measured by
  grep).
- **A response's `.json()`** is exempt, not routed (5 calls). aiohttp and
  httpx parse with `json.loads`, which has no constructors. Its failures
  are `JSONDecodeError` (fixed text; `doc` is rendered away by
  `exception_text`), `RecursionError` (fixed text) or aiohttp's
  `ContentTypeError` (the server's MIME type and the URL pmcp built).
  Every one is caught and rendered via `exception_text` or swallowed.
  Routing them would mean reading the body separately and would break 20
  existing mocks of `.json`.
- **The legacy-sse pair differential under heavy parallel load** (rev 8):
  in one run of rev 8, before PR 321 merged, with two mutation passes and
  the full suite running at once, it mismatched twice on pre-existing
  shapes; rev 8's final run and rev 9's had no mismatch. The cause was not
  isolated.
- **A line that starts inside a frame's string** shows its text up to the
  next `"`. A JSON string cannot hold a raw newline, and a pretty-printed
  frame's lines each start with `{`, `[`, `"`, `}`, `]` or a value, so
  such a line can only come from a downstream breaking its own output
  mid-string. Its text is then the downstream's output (rev 9, §12).
  A pretty-printed frame's closing lines (`}`, `]`, `},`) are output and
  show only those characters, up to any next opener.
- **TOML** is covered only where `tomllib` exists (Python 3.11+). pmcp
  parses no TOML today; the static rule forbids any `tomllib` parser outside
  `pmcp.parsing`, so a first use has to add a helper there.
- **Text a library formats into the message string** is not scrubbed
  (rev 4 board N1). The case found is asyncio's "Task exception was never
  retrieved", whose message embeds the task's `repr`, and with it a
  coroutine's arguments or result. pmcp cannot trigger it today: every
  `create_task` site in `src/pmcp` retrieves its result or catches its
  exception (the static guard's `.exception()` tracking covers the
  retrievals). The SDK's two f-string lines above are the same class.
- **Code that replaces the record factory after pmcp installed its own**
  (without wrapping it) would drop the scrub. `GatewayServer.__init__`
  installs it again, idempotently. Nothing in pmcp or its dependencies was
  found to call `setLogRecordFactory`.
- **Records created without the factory** (`logging.makeLogRecord`, which
  builds from a dict) are not scrubbed. pmcp's own code has none; the static
  guard flags `makeRecord`/`handle` calls in `src/pmcp`.
- **Readers of `audit.rejection`** other than agent-harness @ `18a324a4`.
- **pydantic error types outside the two phrase tables** render `is
  invalid`. This was not enumerated beyond pydantic-core 2.41.5.
- **Clients that parse jsonschema's wording** after `Input validation
  error: `, or rev 1's `Validation error: ` prefix (removed in rev 2; it
  never shipped).

## Execution Policy

- execute: effort=low, reason=the patch is embedded verbatim and proven
  byte-identical to verified code; the executor applies, runs
  *Verification* and compares with *Acceptance criteria*.
- Every PR to main needs panel CR + reconcile first (repo rule).
- Commit/PR text says "see Consiliency/pmcp#297", never a closing keyword.

## Embedding proof

The patches were generated with `git diff -U1 d5a3cda 8d33b49 -- <file>` and embedded. Then, from **this file**, on a fresh worktree `$WORKTREE_ROOT/pmcp-297-proof` of re-fetched `origin/main` (still `d5a3cda`); `p/` is the scratch directory the patches were extracted to, and "identical" means `cmp`-identical to `wip/297-code@8d33b49`. Each of the 40 patches was extracted to `p/<path with / as _>.patch` (the per-file line counts are omitted here for size). The proof was run again on the final file, with this section in it, and printed the same listing.

```text
$ git -C <proof worktree> rev-parse --short HEAD
d5a3cda
x2.py: 25 lines
extractor self-extract: identical
$ git apply --check p/*.patch
check: ok
applied
cmp CHANGELOG.md: identical
cmp README.md: identical
cmp src/pmcp/__init__.py: identical
cmp src/pmcp/argument_errors.py: identical
cmp src/pmcp/auth.py: identical
cmp src/pmcp/cli.py: identical
cmp src/pmcp/client/manager.py: identical
cmp src/pmcp/config/guidance.py: identical
cmp src/pmcp/config/loader.py: identical
cmp src/pmcp/feedback_egress.py: identical
cmp src/pmcp/manifest/code_patterns_loader.py: identical
cmp src/pmcp/manifest/environment.py: identical
cmp src/pmcp/manifest/installer.py: identical
cmp src/pmcp/manifest/loader.py: identical
cmp src/pmcp/manifest/npm_resolver.py: identical
cmp src/pmcp/manifest/package_identity.py: identical
cmp src/pmcp/manifest/refresher.py: identical
cmp src/pmcp/manifest/registry.py: identical
cmp src/pmcp/manifest/version_checker.py: identical
cmp src/pmcp/package_approvals.py: identical
cmp src/pmcp/parsing.py: identical
cmp src/pmcp/policy/policy.py: identical
cmp src/pmcp/provision_gate.py: identical
cmp src/pmcp/redaction_additive.py: identical
cmp src/pmcp/scoped_advisor_audit.py: identical
cmp src/pmcp/server.py: identical
cmp src/pmcp/subscriptions.py: identical
cmp src/pmcp/templates/code_snippets_loader.py: identical
cmp src/pmcp/tools/handlers.py: identical
cmp src/pmcp/transport/http.py: identical
cmp src/pmcp/trust_store.py: identical
cmp src/pmcp/types.py: identical
cmp tests/test_argument_error_echo.py: identical
cmp tests/test_downstream_frame_echo.py: identical
cmp tests/test_exception_text_sinks.py: identical
cmp tests/test_gateway_tool_schemas.py: identical
cmp tests/test_http_transport.py: identical
cmp tests/test_log_record_scrubber.py: identical
cmp tests/test_parse_error_echo.py: identical
cmp tests/test_scoped_advisor_audit.py: identical
changed paths == the 40 patched files
```

## Full suite and gates

On `wip/297-code` @ `8d33b49`, with `npm_config_cache`, `npm_config_store_dir` and
`pnpm_config_store_dir` unset (dev0 is a team host):

```text
$ pytest -m 'not live and not slow' -q
5283 passed, 3 skipped, 80 deselected in 984.18s (0:16:24)
EXIT=0
```

```text
$ ruff check src/ tests/
All checks passed!
$ ruff format --check src/ tests/
178 files already formatted
$ mypy src/
Success: no issues found in 54 source files
$ pytest (eight modules) -q
825 passed in 176.36s (0:02:56)
```

## Verbatim bodies

### How to apply (and the extractor)

From a fresh worktree of `origin/main` @ `d5a3cda`:

```bash
PLAN=.consiliency/plans/detailed-297-validation-echo-20260928-2127.md   # read from branch plan/297-validation-echo
X=<scratch>/extract_plan_block.py        # bootstrap it: see *Extractor* below
while read f; do
  python3 $X $PLAN "### Patch — \`$f\`" "<scratch>/$(echo $f | tr / _).patch"
done <<'LIST'
CHANGELOG.md
README.md
src/pmcp/__init__.py
src/pmcp/argument_errors.py
src/pmcp/auth.py
src/pmcp/cli.py
src/pmcp/client/manager.py
src/pmcp/config/guidance.py
src/pmcp/config/loader.py
src/pmcp/feedback_egress.py
src/pmcp/manifest/code_patterns_loader.py
src/pmcp/manifest/environment.py
src/pmcp/manifest/installer.py
src/pmcp/manifest/loader.py
src/pmcp/manifest/npm_resolver.py
src/pmcp/manifest/package_identity.py
src/pmcp/manifest/refresher.py
src/pmcp/manifest/registry.py
src/pmcp/manifest/version_checker.py
src/pmcp/package_approvals.py
src/pmcp/parsing.py
src/pmcp/policy/policy.py
src/pmcp/provision_gate.py
src/pmcp/redaction_additive.py
src/pmcp/scoped_advisor_audit.py
src/pmcp/server.py
src/pmcp/subscriptions.py
src/pmcp/templates/code_snippets_loader.py
src/pmcp/tools/handlers.py
src/pmcp/transport/http.py
src/pmcp/trust_store.py
src/pmcp/types.py
tests/test_argument_error_echo.py
tests/test_downstream_frame_echo.py
tests/test_exception_text_sinks.py
tests/test_gateway_tool_schemas.py
tests/test_http_transport.py
tests/test_log_record_scrubber.py
tests/test_parse_error_echo.py
tests/test_scoped_advisor_audit.py
LIST
git apply --check <scratch>/*.patch && git apply <scratch>/*.patch
```

The patches are `git diff -U1 d5a3cda 8d33b49 -- <file>` (revs 7 to 9 use 1
lines of context to stay within the plan's size budget; `git apply` needs
no more on an exact base). They are fenced with **four** backticks, and the
extractor closes on the same fence string. Blank context lines carry one
leading space. The test source is ASCII, with non-ASCII test strings written
as `\u` escapes. The two doc patches carry pre-existing non-ASCII context.

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

### Patch — `CHANGELOG.md`

````diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 0c8d9c6..e2e24e1 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -404,2 +404,3 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 ### Fixed
+- **A rejected gateway-tool argument no longer echoes its value into the response, the log or the scoped audit (Consiliency/pmcp#297).** Both validation layers rendered the value that failed: the input-schema gate returned jsonschema's message (`Input validation error: 'Bearer sk-…' is not of type 'object', 'null'`), and an argument model's pydantic error — returned as `str(e)[:400]` and logged as `Tool execution error: …` — carried `input_value=…` (the full value for `InvokeInput`'s correlation-ID charset check and a non-dict `meta`, a truncated repr of the whole argument dict for the all-or-none correlation check). Rejections are now described from their structure, as `<JSON path>: <reason>` — e.g. `Input validation error: $.options: must be of type object or null`, `Invalid arguments: $.run_correlation_id: correlation IDs may contain only alphanumerics and ._:-` — where the reason is a fixed phrase filled only from the tool's own schema or model (a type, a length, a pattern, the allowed values) and a key the caller chose is shown as `*`. The log line is `Tool execution error: invalid arguments for <tool>: <same description>`. **The same rule now holds wherever pmcp turns an exception into text** — tool responses, log lines and tracebacks, the in-memory audit-event buffer `gateway.health` exposes, and error fields such as `gateway.tasks_*` `errors`: a pydantic or jsonschema validation error (or an exception whose text embeds one) reads `N validation error(s) for <Model>: $.<path>: <reason>`, and a traceback whose chain holds one is not logged. This covers downstream data too: a task-capable server answering `tasks/get` with `{"taskId": "t", "ttl": "<secret>"}` used to get that value echoed back in `errors` and stored in the audit-event buffer. Operators see the same form for their own config files (policy, trust store, package approvals, `.mcp.json`): the failing field and why, not the value. A downstream that answers with a malformed JSON-RPC frame no longer has it echoed: the MCP SDK turns such a frame into a JSON-RPC `-32700` whose message is pydantic's text, and pmcp now replaces any `-32700` message (and a non-string `error.message`) with fixed text (`downstream sent a response that could not be parsed`), and, from the moment the `pmcp` package is imported (so in the gateway, every `pmcp` CLI command such as `pmcp refresh`, `python -m pmcp` and any embedder), scrubs every log record at creation, whatever logger makes it -- the MCP SDK's `ClientSession` (logger `client`) and third parties included -- when its traceback, `%`-arguments or an exception passed as the message carry a validation error (the traceback is dropped and the structural description appended). Text a library has already formatted into a message string is not scrubbed. This covers the gateway's startup description refresh and `pmcp refresh`, where a downstream's malformed notification used to put its value in the log. **Parse errors of structured text are rendered the same way:** a YAML, JSON (or, on Python 3.11+, TOML) file pmcp could not parse -- a policy, the manifest or an overlay, guidance, code patterns or snippets, a cache, the trust store or package approvals -- is reported as `could not parse YAML policy file at line L, column C (ParserError)`, never with PyYAML's snippet of the offending line (which could hold a secret) or the parser's message -- including a value a YAML tag made PyYAML convert (`k: !!int <value>` used to raise `invalid literal for int() with base 10: '<value>'`), an undefined or duplicate anchor, or nesting too deep to parse; every parse of structured text, and every ISO timestamp pmcp reads from a store or a downstream task (`Unparseable trust timestamp: could not parse timestamp trust store record (ValueError)`), goes through one helper per format that reports only the format, what was being read, the position and the failure's class; and an uncaught error whose chain holds a validation or parse error (a fatal explicit policy, say) is printed with its frames and that description instead of its text. A downstream's own, well-formed error message is still returned as before. **Wording change:** the text after `Input validation error: ` is no longer jsonschema's message; a client matching on phrases such as `is not of type` or `is too short` must match the new form. A call rejected by the tool's argument model (not the gate) is now recorded in the scoped audit as an `audit.rejection` like a gate rejection, with `rejected_argument_validator: null`, instead of an `audit.invocation` `failure` that copied its unvalidated correlation fields. An unregistered tool name is no longer written to the log (`Tool execution error: unknown gateway tool`); the response still names it. `gateway.provision_status` validates its arguments before its catch-all, which logged a traceback of the validation error. An HTTP request whose `Origin` header has a non-numeric or out-of-range port (`Origin: http://host:<text>`) is now refused with 403 like any other rejected origin, instead of failing with a 500 whose logged traceback quoted the port. A stdio downstream's line that cannot be parsed is no longer logged verbatim when it could be JSON: if its first significant character (after whitespace, control and format characters, and byte-order marks) can begin a JSON value -- `{`, `[`, `"`, a digit, `-`, `t`, `f`, `n` -- it is logged as a description (`downstream sent a JSON-RPC frame that could not be parsed: could not parse JSON downstream stdio frame at line L, column C (<Class>)`), so a malformed frame, an unterminated string or a frame that hits a parser limit (nesting depth, an integer's digit count) never appears in the log. Any other line is the downstream's own output and is shown only up to its first `{`, `[` or `"`, followed by `(rest omitted)`, so a banner written on the same line as a frame cannot carry it. A banner that starts like a JSON value (`true ...`, `-v`, `[INFO] ...`) is now described instead of shown.
 - **Version pinning: the invalid-pin warning now names the right consequence, and the
````

### Patch — `README.md`

````diff
diff --git a/README.md b/README.md
index f744f73..6c2777b 100644
--- a/README.md
+++ b/README.md
@@ -1572,5 +1572,7 @@ URLs, queries, arguments, credentials, or result bodies—and ends with one
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
````

### Patch — `src/pmcp/__init__.py`

````diff
diff --git a/src/pmcp/__init__.py b/src/pmcp/__init__.py
index 5029f42..731fe41 100644
--- a/src/pmcp/__init__.py
+++ b/src/pmcp/__init__.py
@@ -2,2 +2,11 @@
 
+from pmcp.argument_errors import install_log_scrubber as _install_log_scrubber
+
 __version__ = "2.7.3"
+
+# Every entry point imports this package first -- the gateway, the `pmcp`
+# CLI (`pmcp refresh` talks to downstreams through the MCP SDK's client),
+# `python -m pmcp` and any embedder -- so the log-record scrubber is in place
+# before any library can log a rejected value (Consiliency/pmcp#297).
+# Idempotent; `pmcp.client.manager` and `GatewayServer` call it too.
+_install_log_scrubber()
````

### Patch — `src/pmcp/argument_errors.py`

````diff
diff --git a/src/pmcp/argument_errors.py b/src/pmcp/argument_errors.py
new file mode 100644
index 0000000..9bc0ad3
--- /dev/null
+++ b/src/pmcp/argument_errors.py
@@ -0,0 +1,862 @@
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
+``input`` or ``ctx`` (a constraint is read from the gateway's own schema, so
+a custom error that reuses a pydantic type cannot smuggle a value in through
+its context).
+
+The same rule covers every other place pmcp turns an exception into text
+(Consiliency/pmcp#297, rev 2): :func:`exception_text` is ``str(error)``
+unless the error is, or embeds the text of, a validation error, and
+:func:`safe_exc_info` withholds a traceback whose chain holds one.
+``tests/test_argument_error_echo.py`` checks every ``except`` in ``src/pmcp``
+(bar the CLI) that can catch one renders it only through these.
+"""
+
+from __future__ import annotations
+
+import json
+import logging
+import sys
+import traceback
+from collections.abc import Iterable, Iterator
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
+#: Error types whose phrase needs no constraint.
+_FIXED_PHRASES: dict[str, str] = {
+    "missing": "is required",
+    "extra_forbidden": "is not an accepted argument",
+    "string_type": "must be a string",
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
+    **_PMCP_MESSAGES,
+}
+
+#: Error types whose phrase names a constraint: ``(the JSON Schema keyword
+#: that holds it in the gateway's own schema, the phrase with it, the phrase
+#: without it)``. The constraint is read from the schema node at the error's
+#: location, never from pydantic's ``ctx`` (Consiliency/pmcp#297 rev 2, N2).
+_CONSTRAINED_PHRASES: dict[str, tuple[str, str, str]] = {
+    "string_too_short": ("minLength", "must be at least {characters}", "is too short"),
+    "string_too_long": ("maxLength", "must be at most {characters}", "is too long"),
+    "string_pattern_mismatch": (
+        "pattern",
+        "must match the pattern {text}",
+        "does not match the required pattern",
+    ),
+    "too_short": ("minItems", "must have at least {items}", "has too few items"),
+    "too_long": ("maxItems", "must have at most {items}", "has too many items"),
+    "literal_error": ("enum", "must be one of {json}", "is not an allowed value"),
+    "enum": ("enum", "must be one of {json}", "is not an allowed value"),
+    "greater_than": (
+        "exclusiveMinimum",
+        "must be greater than {number}",
+        "is too small",
+    ),
+    "greater_than_equal": (
+        "minimum",
+        "must be greater than or equal to {number}",
+        "is too small",
+    ),
+    "less_than": ("exclusiveMaximum", "must be less than {number}", "is too large"),
+    "less_than_equal": (
+        "maximum",
+        "must be less than or equal to {number}",
+        "is too large",
+    ),
+}
+
+
+#: `_declared_names` for a given set of loaded modules (its key).
+_declared_cache: tuple[int, frozenset[str]] | None = None
+
+
+def _declared_names() -> frozenset[str]:
+    """Every field name and alias of every pydantic model pmcp defines.
+
+    Written by pmcp's authors, never by a caller or a downstream server, so a
+    location segment equal to one discloses nothing pmcp's own source does not.
+    Read from the ``pmcp.*`` modules' namespaces, and recomputed only when a
+    module has been imported since.
+    """
+    global _declared_cache
+    from pydantic import BaseModel
+
+    import pmcp.types  # noqa: F401 -- the argument models, at least
+
+    key = len(sys.modules)
+    if _declared_cache is not None and _declared_cache[0] == key:
+        return _declared_cache[1]
+    names: set[str] = set()
+    for module_name, module in list(sys.modules.items()):
+        if module is None or not module_name.startswith("pmcp."):
+            continue
+        for value in list(vars(module).values()):
+            if (
+                isinstance(value, type)
+                and issubclass(value, BaseModel)
+                and value.__module__ == module_name
+            ):
+                for name, field in value.model_fields.items():
+                    names.add(name)
+                    if isinstance(field.alias, str):
+                        names.add(field.alias)
+    _declared_cache = (key, frozenset(names))
+    return _declared_cache[1]
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
+    declared = _declared_names() | declared_property_names(schema)
+    items = error.errors(include_url=False, include_input=False, include_context=False)
+    loc = tuple(items[0]["loc"]) if items else ()
+    return _model_error_path(loc, arguments, declared)
+
+
+def _schema_node_at(schema: Any, loc: tuple[Any, ...]) -> Any:
+    """The node of the gateway's own schema at a pydantic location, or None."""
+    node = schema
+    for segment in loc:
+        if not isinstance(node, dict):
+            return None
+        if type(segment) is int:
+            node = node.get("items")
+        else:
+            properties = node.get("properties")
+            node = properties.get(segment) if isinstance(properties, dict) else None
+    return node if isinstance(node, dict) else None
+
+
+def _constraint_text(kind: str, value: Any) -> str | None:
+    if kind in ("characters", "items"):
+        noun = kind[:-1]
+        return _count(value, noun) if type(value) is int else None
+    if kind == "number":
+        return str(value) if type(value) in (int, float) else None
+    if kind == "text":
+        return value if isinstance(value, str) else None
+    if isinstance(value, list):  # "json": an enum of the schema's literals
+        return json.dumps(value)
+    return None
+
+
+def _model_phrase(error_type: Any, node: Any) -> str:
+    """The phrase for ``error_type``, its constraint read from ``node`` (the
+    gateway's schema at the error's location; ``None`` when there is none)."""
+    if not isinstance(error_type, str):
+        return "is invalid"
+    if error_type in _FIXED_PHRASES:
+        return _FIXED_PHRASES[error_type]
+    if error_type not in _CONSTRAINED_PHRASES:
+        return "is invalid"
+    keyword, with_constraint, without = _CONSTRAINED_PHRASES[error_type]
+    if not isinstance(node, dict) or keyword not in node:
+        return without
+    kind = with_constraint.split("{", 1)[1].split("}", 1)[0]
+    text = _constraint_text(kind, node[keyword])
+    return without if text is None else with_constraint.replace("{" + kind + "}", text)
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
+    declared = _declared_names() | declared_property_names(schema)
+    items = error.errors(include_url=False, include_input=False, include_context=False)
+    parts = []
+    for item in items[:_MAX_MODEL_ERRORS]:
+        loc = tuple(item["loc"])
+        path = _render_path(_model_error_path(loc, arguments, declared))
+        parts.append(
+            f"{path}: {_model_phrase(item['type'], _schema_node_at(schema, loc))}"
+        )
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
+    error of either library, checked against ``schema``, else ``None``."""
+    if isinstance(error, ValidationError):
+        return describe_model_error(error, schema, arguments)
+    if isinstance(error, jsonschema.ValidationError):
+        return describe_schema_error(error, schema, arguments)
+    return None
+
+
+# --- any exception pmcp renders ------------------------------------------------
+
+
+def _parse_error_types() -> tuple[type[BaseException], ...]:
+    """The parse errors of every structured-text parser pmcp uses (rev 6).
+
+    Their text can quote what they rejected: PyYAML's ``MarkedYAMLError``
+    renders a snippet of the input around the mark, and a constructor error
+    names the input's tag; ``tomllib``'s message can quote a key. JSON's
+    message is fixed vocabulary, but its ``doc`` holds the input, so it is
+    rendered the same way for one rule. python-dotenv does not raise on bad
+    input (it logs the line number only).
+    """
+    import yaml
+
+    types: list[type[BaseException]] = [yaml.YAMLError, json.JSONDecodeError]
+    try:
+        import tomllib  # Python 3.11+
+
+        types.append(tomllib.TOMLDecodeError)
+    except ImportError:  # pragma: no cover - Python 3.10
+        pass
+    return tuple(types)
+
+
+_PARSE_ERRORS: tuple[type[BaseException], ...] = ()
+
+
+def _is_parse_error(error: BaseException) -> bool:
+    """A parser's own error. Not :class:`pmcp.parsing.ParseError`, which
+    subclasses these types so callers' ``except`` clauses keep their meaning
+    but whose text is value-free by construction (rev 7): it is rendered as
+    ``str(error)``, source label included."""
+    from pmcp.parsing import ParseError
+
+    global _PARSE_ERRORS
+    if not _PARSE_ERRORS:
+        _PARSE_ERRORS = _parse_error_types()
+    return isinstance(error, _PARSE_ERRORS) and not isinstance(error, ParseError)
+
+
+def _is_validation_error(error: BaseException) -> bool:
+    """A validation *or parse* error: one whose own text can carry the input
+    it rejected (the parse half since rev 6)."""
+    return isinstance(
+        error, (ValidationError, jsonschema.ValidationError)
+    ) or _is_parse_error(error)
+
+
+def _parse_text(error: BaseException) -> str:
+    """A parse error without the input: the format, the error's class and,
+    where the parser records it, the line and column (never PyYAML's
+    ``problem``/``context`` text or snippet, JSON's ``msg``/``doc``, or
+    TOML's message)."""
+    kind = "JSON" if isinstance(error, json.JSONDecodeError) else None
+    line = column = None
+    if kind == "JSON":
+        line, column = getattr(error, "lineno", None), getattr(error, "colno", None)
+    elif type(error).__module__.startswith("yaml"):
+        kind = "YAML"
+        mark = getattr(error, "problem_mark", None) or getattr(
+            error, "context_mark", None
+        )
+        if mark is not None:
+            line, column = getattr(mark, "line", None), getattr(mark, "column", None)
+            line = line + 1 if isinstance(line, int) else None
+            column = column + 1 if isinstance(column, int) else None
+    else:
+        kind = "TOML"
+        line, column = getattr(error, "lineno", None), getattr(error, "colno", None)
+    where = (
+        f" at line {line}, column {column}"
+        if isinstance(line, int) and isinstance(column, int)
+        else ""
+    )
+    return f"could not parse {kind} ({type(error).__name__}){where}"
+
+
+def _chain(error: BaseException) -> Iterator[BaseException]:
+    """``error``, its ``__cause__``/``__context__`` chain and every exception
+    in a group, each once."""
+    pending, seen = [error], set()
+    while pending:
+        current = pending.pop()
+        if id(current) in seen:
+            continue
+        seen.add(id(current))
+        yield current
+        for linked in (current.__cause__, current.__context__):
+            if linked is not None:
+                pending.append(linked)
+        members = getattr(current, "exceptions", None)
+        if isinstance(members, (tuple, list)):
+            pending.extend(m for m in members if isinstance(m, BaseException))
+
+
+def _validation_text(error: BaseException) -> str:
+    """A validation error described without the schema that raised it: the
+    path (names pmcp's models declare, list indexes, ``*``) and a phrase
+    without constraints, since the schema is not known here (rev 2, N6).
+    A parse error is described by :func:`_parse_text` (rev 6)."""
+    if _is_parse_error(error):
+        return _parse_text(error)
+    if isinstance(error, ValidationError):
+        count = error.error_count()
+        plural = "" if count == 1 else "s"
+        return (
+            f"{count} validation error{plural} for {error.title}: "
+            f"{describe_model_error(error, None, None)}"
+        )
+    assert isinstance(error, jsonschema.ValidationError)
+    try:
+        declared = _declared_names()
+        path = [
+            segment
+            if type(segment) is int or (type(segment) is str and segment in declared)
+            else None
+            for segment in error.absolute_path
+        ]
+        keyword = error.validator if isinstance(error.validator, str) else None
+        phrase = (
+            f"fails its {keyword} constraint"
+            if keyword in jsonschema.validators.Draft202012Validator.VALIDATORS
+            else "is invalid"
+        )
+        return f"schema validation error: {_render_path(path)}: {phrase}"
+    except Exception:
+        return f"schema validation error: {_UNDESCRIBED}"
+
+
+def exception_text(error: BaseException) -> str:
+    """``str(error)``, except where that would carry a validation error's text.
+
+    A pydantic or jsonschema ``ValidationError`` renders the rejected value;
+    so does any exception whose own text embeds one it chains
+    (``RuntimeError(f"... {e}") from e``). Either is described from its
+    structure instead. Every other exception is ``str(error)`` unchanged.
+
+    The embedding check is an exact-substring backstop for wrappers built
+    with ``f"{e}"``/``f"{e!r}"``. It does not recognise a truncated or
+    reformatted copy (``str(e)[:200]``, ``e.errors()``), nor validation text
+    that arrives as a plain string -- which is why pmcp never builds such a
+    copy (``tests/test_exception_text_sinks.py`` flags the construction
+    site) and replaces the SDK's stringified parse errors where it receives
+    them (``pmcp.client.manager._downstream_error``).
+    """
+    if _is_validation_error(error):
+        return _validation_text(error)
+    text = str(error)
+    for linked in _chain(error):
+        if linked is not error and _is_validation_error(linked):
+            try:
+                embedded = str(linked)
+            except Exception:
+                embedded = ""
+            if embedded and embedded in text:
+                return f"{type(error).__name__}: {_validation_text(linked)}"
+    return text
+
+
+def safe_exc_info(error: BaseException) -> BaseException | None:
+    """``exc_info=`` for a log call: the exception, unless its chain holds a
+    validation error, whose rendered traceback would carry the value."""
+    if any(_is_validation_error(linked) for linked in _chain(error)):
+        return None
+    return error
+
+
+def _qualified_name(kind: type[BaseException]) -> str:
+    """``module.QualName`` as the interpreter prints it (bare for builtins)."""
+    module = getattr(kind, "__module__", None)
+    name = getattr(kind, "__qualname__", kind.__name__)
+    if module in (None, "builtins", "__main__"):
+        return str(name)
+    return f"{module}.{name}"
+
+
+def safe_traceback_text(error: BaseException) -> str:
+    """The formatted traceback. When the chain holds a validation or parse
+    error, every exception in it is rendered as its frames (file, line,
+    source) and ``Type: exception_text(...)`` -- the frames never carry an
+    exception's text -- so it stays a usable traceback (rev 6)."""
+    if safe_exc_info(error) is not None:
+        return "".join(
+            traceback.format_exception(type(error), error, error.__traceback__)
+        )
+    parts: list[str] = []
+    seen: set[int] = set()
+
+    def render(current: BaseException) -> None:
+        seen.add(id(current))
+        cause, context = current.__cause__, current.__context__
+        if cause is not None and id(cause) not in seen:
+            render(cause)
+            parts.append(
+                "\nThe above exception was the direct cause of the following "
+                "exception:\n\n"
+            )
+        elif (
+            context is not None
+            and id(context) not in seen
+            and not current.__suppress_context__
+        ):
+            render(context)
+            parts.append(
+                "\nDuring handling of the above exception, another exception "
+                "occurred:\n\n"
+            )
+        if current.__traceback__ is not None:
+            parts.append("Traceback (most recent call last):\n")
+            parts.extend(traceback.format_tb(current.__traceback__))
+        parts.append(f"{_qualified_name(type(current))}: {exception_text(current)}\n")
+
+    render(error)
+    return "".join(parts)
+
+
+# --- every log record, whoever logs it (rev 3, widened in rev 4) ------------
+
+
+def _scrubbed(value: Any, depth: int = 0) -> Any:
+    """`value` with every exception whose chain holds a validation error
+    replaced by its :func:`exception_text`, looking inside tuples, lists,
+    sets and dicts (keys and values)."""
+    if isinstance(value, BaseException):
+        return exception_text(value) if safe_exc_info(value) is None else value
+    if depth > 8:
+        return value
+    if isinstance(value, tuple):
+        return tuple(_scrubbed(item, depth + 1) for item in value)
+    if isinstance(value, list):
+        return [_scrubbed(item, depth + 1) for item in value]
+    if isinstance(value, (set, frozenset)):
+        return type(value)(_scrubbed(item, depth + 1) for item in value)
+    if isinstance(value, dict):
+        return {
+            _scrubbed(key, depth + 1): _scrubbed(item, depth + 1)
+            for key, item in value.items()
+        }
+    return value
+
+
+def scrub_record(record: logging.LogRecord) -> logging.LogRecord:
+    """Remove a validation error's text from `record`, in place.
+
+    - ``msg`` that is itself such an exception becomes its
+      :func:`exception_text`;
+    - ``args`` -- a tuple, or the mapping of a ``%(name)s`` message -- have
+      every such exception, however nested in containers, replaced;
+    - ``exc_info`` whose chain holds one is dropped, and the message gets
+      the exception's :func:`exception_text` appended, so the record still
+      says what failed.
+
+    ``stack_info`` needs nothing: it renders frames and source lines, never
+    an exception's text. Every other record is returned unchanged.
+    """
+    try:
+        if isinstance(record.msg, BaseException):
+            record.msg = _scrubbed(record.msg)
+        if record.args:
+            record.args = _scrubbed(record.args)
+        error = record.exc_info[1] if isinstance(record.exc_info, tuple) else None
+        if isinstance(error, BaseException) and safe_exc_info(error) is None:
+            try:
+                message = record.getMessage()
+            except Exception:
+                message = str(record.msg)
+            record.msg = f"{message} ({exception_text(error)})"
+            record.args = None
+            record.exc_info = None
+            record.exc_text = None
+    except Exception:
+        pass  # a log call must never fail because of the scrub
+    return record
+
+
+def _scrubbing_factory(previous: Any) -> Any:
+    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
+        return scrub_record(previous(*args, **kwargs))
+
+    factory.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
+    factory.previous = previous  # type: ignore[attr-defined]
+    return factory
+
+
+def install_log_scrubber() -> None:
+    """Scrub every `LogRecord` at creation, whatever logger creates it.
+
+    Wraps the current ``logging`` record factory, so every record any logger
+    creates -- the MCP SDK's (including ``"client"``, outside ``mcp.*``),
+    uvicorn's, httpx's, any other -- is scrubbed before any handler sees it,
+    regardless of propagation. It scrubs what a record *carries* (its
+    traceback, arguments, an exception as ``msg``); text a library has
+    already formatted into the message string is out of its reach.
+    Idempotent: installing twice keeps one wrapper. Installed on ``import
+    pmcp`` (so by every entry point), and again at ``pmcp.client.manager``
+    import and in ``GatewayServer.__init__``.
+    """
+    current = logging.getLogRecordFactory()
+    if not getattr(current, "pmcp_validation_scrubber", False):
+        logging.setLogRecordFactory(_scrubbing_factory(current))
+    _install_excepthook()
+    _install_threading_excepthook()
+    _install_handle_error()
+
+
+def _install_handle_error() -> None:
+    """A handler whose ``emit`` raises makes ``logging.Handler.handleError``
+    print "--- Logging error ---" and the exception it is handling, chain and
+    all, to stderr -- past every scrub, and while an ``except`` block for a
+    validation or parse error is active that chain holds it (rev 6; rev 3
+    listed it as unverified). Such a chain is printed by
+    :func:`safe_traceback_text`; everything else by the original method.
+    Idempotent."""
+    original = logging.Handler.handleError
+    if getattr(original, "pmcp_validation_scrubber", False):
+        return
+
+    def handle_error(self: logging.Handler, record: logging.LogRecord) -> None:
+        error = sys.exc_info()[1]
+        if not isinstance(error, BaseException) or safe_exc_info(error) is not None:
+            original(self, record)
+            return
+        if not logging.raiseExceptions or sys.stderr is None:
+            return
+        try:
+            sys.stderr.write(
+                "--- Logging error ---\n"
+                + safe_traceback_text(error)
+                + f"Message: {scrub_record(record).msg!r}\n"
+            )
+        except OSError:  # pragma: no cover - stderr closed, as the original
+            pass
+
+    handle_error.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
+    handle_error.original = original  # type: ignore[attr-defined]
+    logging.Handler.handleError = handle_error  # type: ignore[method-assign]
+
+
+def _install_excepthook() -> None:
+    """An uncaught exception is printed by ``sys.excepthook``, chain and all:
+    a ``raise ValueError(...) from e`` whose cause is a validation or parse
+    error would print the input past every log scrub (rev 6). Such a chain is
+    printed by :func:`safe_traceback_text` instead; every other exception by
+    the previous hook, unchanged. Idempotent."""
+    previous = sys.excepthook
+    if getattr(previous, "pmcp_validation_scrubber", False):
+        return
+
+    def hook(kind: Any, value: Any, tb: Any) -> None:
+        if isinstance(value, BaseException) and safe_exc_info(value) is None:
+            # The default hook prints nothing when there is no stderr.
+            if sys.stderr is not None:
+                sys.stderr.write(safe_traceback_text(value) + "\n")
+            return
+        previous(kind, value, tb)
+
+    hook.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
+    hook.previous = previous  # type: ignore[attr-defined]
+    sys.excepthook = hook
+
+
+def _install_threading_excepthook() -> None:
+    """``threading.excepthook`` prints an uncaught exception in a thread the
+    same way (rev 7, N3): such a chain is printed by
+    :func:`safe_traceback_text` under the interpreter's own header; every
+    other exception by the previous hook, unchanged. Idempotent."""
+    import threading
+
+    previous = threading.excepthook
+    if getattr(previous, "pmcp_validation_scrubber", False):
+        return
+
+    def hook(args: Any) -> None:
+        value = getattr(args, "exc_value", None)
+        if (
+            getattr(args, "exc_type", None) is not SystemExit
+            and isinstance(value, BaseException)
+            and safe_exc_info(value) is None
+        ):
+            if sys.stderr is not None:
+                thread = getattr(args, "thread", None)
+                name = getattr(thread, "name", None) or "unknown"
+                sys.stderr.write(
+                    f"Exception in thread {name}:\n" + safe_traceback_text(value) + "\n"
+                )
+            return
+        previous(args)
+
+    hook.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
+    hook.previous = previous  # type: ignore[attr-defined]
+    threading.excepthook = hook
````

### Patch — `src/pmcp/auth.py`

````diff
diff --git a/src/pmcp/auth.py b/src/pmcp/auth.py
index ccb5e36..d5121a6 100644
--- a/src/pmcp/auth.py
+++ b/src/pmcp/auth.py
@@ -21,3 +21,5 @@ from jwt import PyJWKSet
 
+from pmcp.argument_errors import exception_text
 from pmcp.keyword_matcher import key_start_pattern, redact_keyword_values
+from pmcp.parsing import load_json
 from pmcp.redaction_additive import redact_additive
@@ -452,3 +454,3 @@ class AsyncJWKS:
         try:
-            jwks = json.loads(content.decode("utf-8"))
+            jwks = load_json(content, source="JWKS response", encoding="utf-8")
         except (UnicodeDecodeError, json.JSONDecodeError) as exc:
@@ -588,3 +590,6 @@ def sanitize_auth_diagnostic(value: object, *, max_length: int | None = 400) ->
     """
-    text = redact_additive(_sanitize_base(str(value)))
+    # An exception goes through `exception_text`: a validation error's own
+    # text carries the rejected value (Consiliency/pmcp#297).
+    raw = exception_text(value) if isinstance(value, BaseException) else str(value)
+    text = redact_additive(_sanitize_base(raw))
     return text if max_length is None else text[:max_length]
@@ -807,3 +812,3 @@ def parse_url_elicitation_error(payload: object) -> list[UrlElicitationInfo]:
         try:
-            payload = json.loads(payload_text)
+            payload = load_json(payload_text, source="URL elicitation payload")
         except json.JSONDecodeError:
@@ -813,3 +818,3 @@ def parse_url_elicitation_error(payload: object) -> list[UrlElicitationInfo]:
             try:
-                payload = json.loads(match.group(1))
+                payload = load_json(match.group(1), source="URL elicitation payload")
             except json.JSONDecodeError:
@@ -901,3 +906,3 @@ def fetch_json_metadata(
             return None, f"{safe_url} returned non-JSON content"
-        data = json.loads(body.decode("utf-8"))
+        data = load_json(body, source="auth metadata response", encoding="utf-8")
         if not isinstance(data, dict):
````

### Patch — `src/pmcp/cli.py`

````diff
diff --git a/src/pmcp/cli.py b/src/pmcp/cli.py
index bbaee51..f9f9e2e 100644
--- a/src/pmcp/cli.py
+++ b/src/pmcp/cli.py
@@ -21,2 +21,3 @@ from urllib.parse import urlsplit, urlunsplit
 from dotenv import load_dotenv
+from pmcp.argument_errors import exception_text, safe_exc_info
 from pmcp import package_approvals, trust_store
@@ -46,2 +47,3 @@ from pmcp.validation import is_valid_package_version, parse_package_spec
 from pmcp.manifest.loader import load_manifest
+from pmcp.parsing import load_json, load_json_file
 from pmcp.types import StartupPolicyOperation
@@ -937,4 +939,4 @@ async def run_refresh(args: argparse.Namespace) -> None:
     except Exception as e:
-        logger.error(f"Refresh failed: {e}")
-        print(f"Error: {e}", file=sys.stderr)
+        logger.error(f"Refresh failed: {exception_text(e)}")
+        print(f"Error: {exception_text(e)}", file=sys.stderr)
         sys.exit(1)
@@ -1143,3 +1145,3 @@ def _extract_tool_payload(result: dict[str, object]) -> dict[str, object] | None
         try:
-            parsed = json.loads(text)
+            parsed = load_json(text, source="tool result text")
         except json.JSONDecodeError:
@@ -1221,4 +1223,4 @@ async def _query_running_gateway_status(
         return snapshot
-    except Exception:
-        logger.debug("Live gateway status query failed", exc_info=True)
+    except Exception as exc:
+        logger.debug("Live gateway status query failed", exc_info=safe_exc_info(exc))
         return None
@@ -1873,3 +1875,3 @@ def run_setup(args: argparse.Namespace) -> None:
         try:
-            parsed = json.loads(target_path.read_text())
+            parsed = load_json(target_path.read_text(), source="client config")
             if isinstance(parsed, dict):
@@ -1880,3 +1882,3 @@ def run_setup(args: argparse.Namespace) -> None:
             print(
-                f"Error: Could not parse existing config at {target_path}: {exc}",
+                f"Error: Could not parse existing config at {target_path}: {exception_text(exc)}",
                 file=sys.stderr,
@@ -1993,3 +1995,3 @@ def _load_local_mcp_json(project_root: Path | None) -> tuple[Path, dict | None]:
         with open(config_path) as f:
-            parsed = json.load(f)
+            parsed = load_json_file(f, source="client config")
             return config_path, parsed if isinstance(parsed, dict) else None
@@ -2098,3 +2100,3 @@ async def _probe_http_health(timeout_s: float) -> tuple[bool, str, int | None]:
             sanitize_auth_diagnostic(
-                f"{safe_url} unreachable ({exc.__class__.__name__}: {exc})"
+                f"{safe_url} unreachable ({exc.__class__.__name__}: {exception_text(exc)})"
             ),
@@ -2510,3 +2512,3 @@ async def run_server(args: argparse.Namespace) -> None:
     except Exception as e:
-        logger.error(f"Fatal error: {e}")
+        logger.error(f"Fatal error: {exception_text(e)}")
         raise
@@ -2741,3 +2743,3 @@ def run_trust(args: argparse.Namespace) -> None:
     except (trust_store.TrustStoreError, ValueError) as exc:
-        _trust_fail(str(exc))
+        _trust_fail(exception_text(exc))
 
@@ -3081,3 +3083,3 @@ def main() -> None:
     except Exception as e:
-        print(f"Fatal error: {e}", file=sys.stderr)
+        print(f"Fatal error: {exception_text(e)}", file=sys.stderr)
         sys.exit(1)
````

### Patch — `src/pmcp/client/manager.py`

````diff
diff --git a/src/pmcp/client/manager.py b/src/pmcp/client/manager.py
index 57a563e..bc915f1 100644
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -12,4 +12,4 @@ import random
 import re
+import unicodedata
 import signal
-import traceback
 import string
@@ -29,2 +29,7 @@ from pydantic import ValidationError
 
+from pmcp.argument_errors import (
+    exception_text,
+    safe_traceback_text,
+    install_log_scrubber,
+)
 from pmcp.auth import sanitize_auth_diagnostic
@@ -33,2 +38,3 @@ from pmcp.env_store import sanitized_subprocess_env
 from pmcp.manifest.installer import _operator_safe, _render_install_argv
+from pmcp.parsing import load_json
 from pmcp.remote_auth import (
@@ -69,2 +75,5 @@ except ImportError:
 logger = logging.getLogger(__name__)
+# The SDK logs rejected frames with a traceback (Consiliency/pmcp#297); every
+# record is scrubbed at creation (`install_log_scrubber`).
+install_log_scrubber()
 
@@ -133,3 +142,5 @@ def describe_exception(exc: BaseException) -> str:
     rendered = "; ".join(
-        f"{type(leaf).__name__}: {leaf}" if str(leaf) else type(leaf).__name__
+        f"{type(leaf).__name__}: {exception_text(leaf)}"
+        if str(leaf)
+        else type(leaf).__name__
         for leaf in shown
@@ -213,11 +224,90 @@ class DownstreamError(Exception):
 
+#: JSON-RPC's parse-error code. The SDK's HTTP transports synthesise it for a
+#: downstream frame their models reject, with `f"Failed to parse ...: {exc}"`
+#: -- pydantic's text, rejected value included -- as the message
+#: (`mcp/client/streamable_http.py:188,407`); it reaches pmcp as a plain
+#: string, where `exception_text` cannot recognise it (Consiliency/pmcp#297).
+_PARSE_ERROR = -32700
+_PARSE_ERROR_MESSAGE = "downstream sent a response that could not be parsed"
+_MALFORMED_ERROR_MESSAGE = "downstream sent a malformed JSON-RPC error"
+
+#: Every character that can begin a JSON value (RFC 8259 section 3: object,
+#: array, string, number, `true`/`false`/`null`), plus the `NaN` and
+#: `Infinity` that Python's `json` also accepts.
+_JSON_VALUE_START = frozenset('{["-0123456789tfnNI')
+#: Characters that open a JSON object, array or string: where a frame's
+#: content can begin inside a line.
+_FRAME_OPENERS = ("{", "[", '"')
+_OMITTED = " (rest omitted)"
+
+
+def _carries_nothing(char: str) -> bool:
+    """A lead character that shows nothing a reader needs: whitespace (all
+    that `str.strip` removes, including NBSP and U+2028), a control or format
+    character (NUL, ESC, a byte-order mark, ZWSP) or U+FFFD, the replacement
+    for an undecodable byte (a UTF-16/32 BOM, say)."""
+    return (
+        char.isspace() or char == "\ufffd" or unicodedata.category(char) in ("Cc", "Cf")
+    )
+
+
+def _significant_start(text: str) -> int:
+    """The index of the line's first character that is not `_carries_nothing`."""
+    for index, char in enumerate(text):
+        if not _carries_nothing(char):
+            return index
+    return len(text)
+
+
+def _is_downstream_output(text: str) -> bool:
+    """Whether a stdio line the parser rejected is the downstream's own
+    output (a banner, a log line), as opposed to a frame, whose content is
+    the downstream's data and is never logged (Consiliency/pmcp#297).
+
+    Decided by where JSON could start, not by where the parser failed (rev 9:
+    an unterminated string fails AT its opening quote): the line is output
+    only if its first significant character cannot begin any JSON value. A
+    banner that starts like a value (`true story`, `-v`, `[INFO]`) is
+    therefore described rather than shown -- the price of never echoing a
+    malformed frame.
+    """
+    start = _significant_start(text)
+    return start == len(text) or text[start] not in _JSON_VALUE_START
+
+
+def _output_text(text: str) -> str:
+    """What is shown of a line that is output: its significant text up to the
+    first character that could open an embedded frame (`{`, `[`, `"`), then a
+    fixed marker. A banner written without a newline before a frame
+    (`ready{...}`), or a frame behind a stray prefix character (`,{...}`,
+    `\x1b[0m{...}`), shows its prefix only. Digits are kept: a port number
+    carries no frame."""
+    body = text[_significant_start(text) :]
+    cuts = [index for index in (body.find(c) for c in _FRAME_OPENERS) if index >= 0]
+    if not cuts:
+        return body.rstrip()
+    return body[: min(cuts)].rstrip() + _OMITTED
+
+
 def _downstream_error(error: Any) -> DownstreamError:
-    """Build a `DownstreamError` from a JSON-RPC `error` member."""
+    """Build a `DownstreamError` from a JSON-RPC `error` member.
+
+    A parse error's message is replaced by fixed text, whoever wrote it: the
+    SDK's carries the rejected frame, and a downstream's own parse-error
+    prose describes our request, not its failure. So is a malformed `error`
+    (not an object, or a non-string `message`). Every other error keeps the
+    downstream's own message string, returned by design.
+    """
     if not isinstance(error, dict):
-        return DownstreamError(str(error))
-    return DownstreamError(
-        str(error.get("message", "Unknown error")),
-        code=error.get("code"),
-        data=error.get("data"),
-    )
+        return DownstreamError(_MALFORMED_ERROR_MESSAGE)
+    code = error.get("code")
+    if code == _PARSE_ERROR:
+        return DownstreamError(_PARSE_ERROR_MESSAGE, code=code)
+    message = error.get("message", "Unknown error")
+    if not isinstance(message, str):
+        # JSON-RPC requires a string: anything else is a malformed frame,
+        # whose content is not the downstream's message (the SDK's transports
+        # reject it outright; stdio is parsed without a model).
+        return DownstreamError(_MALFORMED_ERROR_MESSAGE, code=code)
+    return DownstreamError(message, code=code, data=error.get("data"))
 
@@ -1135,3 +1225,5 @@ class ClientManager:
             if isinstance(result, Exception):
-                error_msg = f"Failed to connect to {config.name}: {result}"
+                error_msg = (
+                    f"Failed to connect to {config.name}: {exception_text(result)}"
+                )
                 logger.error(error_msg)
@@ -1937,3 +2029,5 @@ class ClientManager:
             if isinstance(result, BaseException):
-                logger.debug(f"Server {name} doesn't support {kind}: {result}")
+                logger.debug(
+                    f"Server {name} doesn't support {kind}: {exception_text(result)}"
+                )
                 listings[kind] = None
@@ -2676,5 +2770,3 @@ class ClientManager:
                 # framework formats on its own.
-                traceback_text = "".join(
-                    traceback.format_exception(type(exc), exc, exc.__traceback__)
-                )
+                traceback_text = safe_traceback_text(exc)
                 logger.warning(
@@ -2843,8 +2935,18 @@ class ClientManager:
                 )
-            message = json.loads(text)
-        except json.JSONDecodeError:
-            # Non-JSON output already counted as a heartbeat by the caller.
-            logger.debug(
-                f"[{name}] Non-JSON output: {line.decode(errors='replace').strip()}"
-            )
+            message = load_json(text, source="downstream stdio frame")
+        except json.JSONDecodeError as error:
+            # Already counted as a heartbeat by the caller.
+            if not _is_downstream_output(text):
+                # A frame the parser rejected -- a syntax error, or a parser
+                # limit (nesting depth, an integer's digit count) on otherwise
+                # valid JSON -- is the downstream's data, not its output:
+                # described, never echoed (Consiliency/pmcp#297, rev 8).
+                logger.debug(
+                    f"[{name}] downstream sent a JSON-RPC frame that could not "
+                    f"be parsed: {exception_text(error)}"
+                )
+                return
+            # The downstream's own non-protocol output (a banner, a log line),
+            # logged as it is by design.
+            logger.debug(f"[{name}] Non-JSON output: {_output_text(text)}")
             return
````

### Patch — `src/pmcp/config/guidance.py`

````diff
diff --git a/src/pmcp/config/guidance.py b/src/pmcp/config/guidance.py
index 58976f6..c079962 100644
--- a/src/pmcp/config/guidance.py
+++ b/src/pmcp/config/guidance.py
@@ -13,2 +13,4 @@ import yaml
 from pydantic import BaseModel, Field
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_yaml
 
@@ -169,3 +171,3 @@ def load_guidance_config(config_path: Path | None = None) -> GuidanceConfig:
         with open(config_path) as f:
-            data = yaml.safe_load(f)
+            data = load_yaml(f, source="guidance config")
 
@@ -177,3 +179,5 @@ def load_guidance_config(config_path: Path | None = None) -> GuidanceConfig:
         # If config is invalid, log warning and use defaults
-        print(f"Warning: Failed to load guidance config from {config_path}: {e}")
+        print(
+            f"Warning: Failed to load guidance config from {config_path}: {exception_text(e)}"
+        )
         print("Using default guidance config (minimal mode)")
@@ -234,3 +238,3 @@ def set_telemetry_enabled(
         try:
-            loaded = yaml.safe_load(config_path.read_text())
+            loaded = load_yaml(config_path.read_text(), source="guidance config")
             if isinstance(loaded, dict):
@@ -274,3 +278,3 @@ def set_feedback_submission_enabled(
         try:
-            loaded = yaml.safe_load(config_path.read_text())
+            loaded = load_yaml(config_path.read_text(), source="guidance config")
             if isinstance(loaded, dict):
````

### Patch — `src/pmcp/config/loader.py`

````diff
diff --git a/src/pmcp/config/loader.py b/src/pmcp/config/loader.py
index c71e7f8..f07ea66 100644
--- a/src/pmcp/config/loader.py
+++ b/src/pmcp/config/loader.py
@@ -16,2 +16,4 @@ from typing import TYPE_CHECKING, Any, Literal, cast
 
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_json
 from pmcp.types import (
@@ -281,3 +283,3 @@ def parse_json_file(file_path: Path) -> McpConfigFile | None:
     except Exception as e:
-        logger.warning(f"Failed to parse config file {file_path}: {e}")
+        logger.warning(f"Failed to parse config file {file_path}: {exception_text(e)}")
         return None
@@ -297,3 +299,3 @@ def parse_config_bytes(content: bytes, file_path: Path) -> McpConfigFile | None:
     try:
-        data = json.loads(content)
+        data = load_json(content, source="config file")
 
@@ -318,3 +320,3 @@ def parse_config_bytes(content: bytes, file_path: Path) -> McpConfigFile | None:
     except Exception as e:
-        logger.warning(f"Failed to parse config file {file_path}: {e}")
+        logger.warning(f"Failed to parse config file {file_path}: {exception_text(e)}")
         return None
@@ -356,3 +358,3 @@ def _read_config_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
     except Exception as exc:
-        return None, f"invalid_json: {exc}"
+        return None, f"invalid_json: {exception_text(exc)}"
     return _config_object_from_bytes(content)
@@ -365,5 +367,5 @@ def _config_object_from_bytes(
     try:
-        data = json.loads(content)
+        data = load_json(content, source="config file")
     except Exception as exc:
-        return None, f"invalid_json: {exc}"
+        return None, f"invalid_json: {exception_text(exc)}"
     if not isinstance(data, dict):
@@ -1150,3 +1152,5 @@ def load_configs(
     except Exception as e:
-        logger.debug(f"Manifest defaults unavailable during config load: {e}")
+        logger.debug(
+            f"Manifest defaults unavailable during config load: {exception_text(e)}"
+        )
 
````

### Patch — `src/pmcp/feedback_egress.py`

````diff
diff --git a/src/pmcp/feedback_egress.py b/src/pmcp/feedback_egress.py
index 48d4217..9b08312 100644
--- a/src/pmcp/feedback_egress.py
+++ b/src/pmcp/feedback_egress.py
@@ -48,2 +48,3 @@ from pmcp.env_store import (
 )
+from pmcp.parsing import load_json
 from pmcp.provision_gate import operator_safe
@@ -524,3 +525,5 @@ def _classify_response(status: int, raw: bytes, search_url: str) -> FeedbackSubm
     try:
-        document: Any = json.loads(raw.decode("utf-8"))
+        document: Any = load_json(
+            raw, source="feedback issue response", encoding="utf-8"
+        )
     except Exception:
@@ -598,3 +601,5 @@ def _probe_repository_visibility(
             raw = _read_bounded_body(response, bound)
-        document: Any = json.loads(raw.decode("utf-8"))
+        document: Any = load_json(
+            raw, source="feedback repository response", encoding="utf-8"
+        )
     except Exception:
````

### Patch — `src/pmcp/manifest/code_patterns_loader.py`

````diff
diff --git a/src/pmcp/manifest/code_patterns_loader.py b/src/pmcp/manifest/code_patterns_loader.py
index b89f67a..e0e677b 100644
--- a/src/pmcp/manifest/code_patterns_loader.py
+++ b/src/pmcp/manifest/code_patterns_loader.py
@@ -11,3 +11,4 @@ from typing import Any
 
-import yaml
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_yaml
 
@@ -42,3 +43,3 @@ class CodePatternsLoader:
             with open(self._patterns_path) as f:
-                data = yaml.safe_load(f)
+                data = load_yaml(f, source="code patterns file")
 
@@ -66,3 +67,3 @@ class CodePatternsLoader:
             print(
-                f"Warning: Failed to load code patterns from {self._patterns_path}: {e}"
+                f"Warning: Failed to load code patterns from {self._patterns_path}: {exception_text(e)}"
             )
````

### Patch — `src/pmcp/manifest/environment.py`

````diff
diff --git a/src/pmcp/manifest/environment.py b/src/pmcp/manifest/environment.py
index 613559f..6e0bab0 100644
--- a/src/pmcp/manifest/environment.py
+++ b/src/pmcp/manifest/environment.py
@@ -11,2 +11,3 @@ from dataclasses import dataclass, field
 from typing import Literal
+from pmcp.argument_errors import exception_text
 
@@ -89,3 +90,3 @@ async def check_cli(name: str, check_command: list[str]) -> CLIInfo | None:
     except Exception as e:
-        logger.debug(f"Error checking CLI {name}: {e}")
+        logger.debug(f"Error checking CLI {name}: {exception_text(e)}")
         return None
@@ -113,3 +114,3 @@ async def get_cli_help(
     except Exception as e:
-        logger.debug(f"Error getting help for {name}: {e}")
+        logger.debug(f"Error getting help for {name}: {exception_text(e)}")
         return None
````

### Patch — `src/pmcp/manifest/installer.py`

````diff
diff --git a/src/pmcp/manifest/installer.py b/src/pmcp/manifest/installer.py
index 3454402..29b7ccc 100644
--- a/src/pmcp/manifest/installer.py
+++ b/src/pmcp/manifest/installer.py
@@ -14,2 +14,3 @@ from typing import Literal
 
+from pmcp.argument_errors import exception_text, safe_exc_info
 from pmcp.env_store import resolve_scope_path, sanitized_subprocess_env
@@ -239,3 +240,3 @@ class JobManager:
             job.status = "failed"
-            job.error = str(e)[:300]
+            job.error = exception_text(e)[:300]
             logger.error(f"Install job {job_id} failed: {job.error}")
@@ -249,6 +250,8 @@ class JobManager:
             if exc:
-                logger.error(f"Install job {job.id} task crashed: {exc}")
+                logger.error(
+                    f"Install job {job.id} task crashed: {exception_text(exc)}"
+                )
                 if job.status == "installing":
                     job.status = "failed"
-                    job.error = f"Monitor task crashed: {exc}"
+                    job.error = f"Monitor task crashed: {exception_text(exc)}"
                 # Kill subprocess if still running (but NOT if server_ready - it's being handed off)
@@ -262,3 +265,3 @@ class JobManager:
                     except Exception as e:
-                        logger.debug(f"task cleanup error: {e}")
+                        logger.debug(f"task cleanup error: {exception_text(e)}")
         except asyncio.CancelledError:
@@ -298,3 +301,3 @@ class JobManager:
             except Exception as e:
-                logger.debug(f"stream reader error: {e}")
+                logger.debug(f"stream reader error: {exception_text(e)}")
                 return (name, None)
@@ -424,3 +427,3 @@ class JobManager:
                                     logger.warning(
-                                        f"Install {job.id}: Error in server detection: {e}"
+                                        f"Install {job.id}: Error in server detection: {exception_text(e)}"
                                     )
@@ -461,5 +464,8 @@ class JobManager:
         except Exception as e:
-            logger.error(f"Install job {job.id} monitor error: {e}", exc_info=True)
+            logger.error(
+                f"Install job {job.id} monitor error: {exception_text(e)}",
+                exc_info=safe_exc_info(e),
+            )
             job.status = "failed"
-            job.error = str(e)
+            job.error = exception_text(e)
             # Try to clean up process
@@ -498,3 +504,5 @@ class JobManager:
         except Exception as e:
-            logger.warning(f"Install {job_id}: Error terminating process: {e}")
+            logger.warning(
+                f"Install {job_id}: Error terminating process: {exception_text(e)}"
+            )
 
````

### Patch — `src/pmcp/manifest/loader.py`

````diff
diff --git a/src/pmcp/manifest/loader.py b/src/pmcp/manifest/loader.py
index 6e7704e..3ddcb67 100644
--- a/src/pmcp/manifest/loader.py
+++ b/src/pmcp/manifest/loader.py
@@ -16,2 +16,4 @@ import yaml
 
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_yaml
 from pmcp.project_consent import log_refusal, read_and_gate
@@ -36,3 +38,4 @@ def _shipped_manifest_entries() -> dict[str, dict[str, Any]]:
     try:
-        servers = (yaml.safe_load(path.read_bytes()) or {}).get("servers") or {}
+        shipped = load_yaml(path.read_bytes(), source="shipped manifest")
+        servers = (shipped or {}).get("servers") or {}
     except (OSError, yaml.YAMLError, AttributeError):
@@ -1164,5 +1167,7 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
     try:
-        data = yaml.safe_load(content)
+        data = load_yaml(content, source="manifest overlay")
     except yaml.YAMLError as exc:
-        logger.warning(f"Skipping unreadable manifest overlay {path}: {exc}")
+        logger.warning(
+            f"Skipping unreadable manifest overlay {path}: {exception_text(exc)}"
+        )
         return {}, {}, {}, {}
@@ -1183,3 +1188,3 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
                 logger.warning(
-                    f"Skipping invalid server entry '{name}' in overlay {path}: {exc}"
+                    f"Skipping invalid server entry '{name}' in overlay {path}: {exception_text(exc)}"
                 )
@@ -1197,3 +1202,3 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
                     f"Skipping invalid cli_alternative '{name}' in overlay "
-                    f"{path}: {exc}"
+                    f"{path}: {exception_text(exc)}"
                 )
@@ -1255,3 +1260,3 @@ def load_manifest(manifest_path: Path | None = None) -> Manifest:
     with open(manifest_path, "r") as f:
-        data = yaml.safe_load(f)
+        data = load_yaml(f, source="manifest")
 
````

### Patch — `src/pmcp/manifest/npm_resolver.py`

````diff
diff --git a/src/pmcp/manifest/npm_resolver.py b/src/pmcp/manifest/npm_resolver.py
index 3382a1e..85dc7ba 100644
--- a/src/pmcp/manifest/npm_resolver.py
+++ b/src/pmcp/manifest/npm_resolver.py
@@ -64,2 +64,4 @@ from pathlib import Path
 from typing import Literal
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_json
 
@@ -376,3 +378,3 @@ class NpmResolver:
         try:
-            handshake = json.loads(line)
+            handshake = load_json(line, source="npm resolver handshake")
         except ValueError:
@@ -500,3 +502,5 @@ class NpmResolver:
             self._terminate()
-            return _refused(f"npm resolver child died before the query: {exc}")
+            return _refused(
+                f"npm resolver child died before the query: {exception_text(exc)}"
+            )
 
@@ -510,3 +514,3 @@ class NpmResolver:
         try:
-            response = json.loads(line)
+            response = load_json(line, source="npm resolver response")
         except ValueError:
````

### Patch — `src/pmcp/manifest/package_identity.py`

````diff
diff --git a/src/pmcp/manifest/package_identity.py b/src/pmcp/manifest/package_identity.py
index 29cf145..89ba7d1 100644
--- a/src/pmcp/manifest/package_identity.py
+++ b/src/pmcp/manifest/package_identity.py
@@ -31,3 +31,2 @@ from __future__ import annotations
 
-import json
 import logging
@@ -42,2 +41,4 @@ import semver
 
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_json
 from pmcp.validation import is_valid_package_name
@@ -121,5 +122,5 @@ def _fetch_packument(name: str) -> dict[str, Any] | None:
             body = response.read(_MAX_PACKUMENT_BYTES)
-        data = json.loads(body.decode("utf-8"))
+        data = load_json(body, source="npm packument", encoding="utf-8")
     except Exception as exc:
-        logger.debug("npm packument fetch failed for %r: %s", name, exc)
+        logger.debug("npm packument fetch failed for %r: %s", name, exception_text(exc))
         return None
@@ -205,3 +206,5 @@ def resolve_package_identity(spec: str) -> PackageIdentity | None:
     except Exception as exc:  # pragma: no cover - the fetch handles its own
-        logger.debug("npm packument lookup raised for %r: %s", name, exc)
+        logger.debug(
+            "npm packument lookup raised for %r: %s", name, exception_text(exc)
+        )
         return None
````

### Patch — `src/pmcp/manifest/refresher.py`

````diff
diff --git a/src/pmcp/manifest/refresher.py b/src/pmcp/manifest/refresher.py
index b9bf8ff..2049881 100644
--- a/src/pmcp/manifest/refresher.py
+++ b/src/pmcp/manifest/refresher.py
@@ -18,2 +18,3 @@ import yaml
 
+from pmcp.argument_errors import exception_text
 from pmcp.manifest.loader import (
@@ -29,2 +30,3 @@ from pmcp.manifest.version_checker import (
 )
+from pmcp.parsing import load_yaml
 from pmcp.types import (
@@ -64,3 +66,3 @@ def load_descriptions_cache(cache_path: Path | None = None) -> DescriptionsCache
         with open(cache_path, "r") as f:
-            data = yaml.safe_load(f)
+            data = load_yaml(f, source="descriptions cache")
 
@@ -91,3 +93,3 @@ def load_descriptions_cache(cache_path: Path | None = None) -> DescriptionsCache
     except Exception as e:
-        logger.warning(f"Failed to load descriptions cache: {e}")
+        logger.warning(f"Failed to load descriptions cache: {exception_text(e)}")
         return None
@@ -397,3 +399,3 @@ async def refresh_server(
     except Exception as e:
-        logger.error(f"Failed to refresh {server_name}: {e}")
+        logger.error(f"Failed to refresh {server_name}: {exception_text(e)}")
         return None
@@ -495,3 +497,3 @@ async def refresh_all(
         except Exception as e:
-            logger.error(f"Error refreshing {name}: {e}")
+            logger.error(f"Error refreshing {name}: {exception_text(e)}")
             if existing:
````

### Patch — `src/pmcp/manifest/registry.py`

````diff
diff --git a/src/pmcp/manifest/registry.py b/src/pmcp/manifest/registry.py
index cc2ed99..765f0e7 100644
--- a/src/pmcp/manifest/registry.py
+++ b/src/pmcp/manifest/registry.py
@@ -19,2 +19,4 @@ import aiohttp
 
+from pmcp.parsing import load_json
+
 logger = logging.getLogger(__name__)
@@ -491,3 +493,3 @@ async def _fetch_registry_servers_uncached(
                     break
-                payload = json.loads(body.decode("utf-8"))
+                payload = load_json(body, source="registry response", encoding="utf-8")
                 cache = _parse_cache_payload(payload, endpoint)
@@ -690,3 +692,3 @@ def load_registry_cache(cache_path: Path | None = None) -> RegistryCache | None:
     try:
-        payload = json.loads(path.read_text())
+        payload = load_json(path.read_text(), source="registry cache")
     except Exception:
````

### Patch — `src/pmcp/manifest/version_checker.py`

````diff
diff --git a/src/pmcp/manifest/version_checker.py b/src/pmcp/manifest/version_checker.py
index 19e71e6..7419b34 100644
--- a/src/pmcp/manifest/version_checker.py
+++ b/src/pmcp/manifest/version_checker.py
@@ -15,2 +15,3 @@ from semver import Version as SemverVersion
 
+from pmcp.argument_errors import exception_text
 from pmcp import __version__
@@ -1414,3 +1415,3 @@ async def get_npm_version(package_name: str, timeout: float = 10.0) -> str | Non
     except Exception as e:
-        logger.debug(f"npm lookup error for {package_name}: {e}")
+        logger.debug(f"npm lookup error for {package_name}: {exception_text(e)}")
         return None
@@ -1456,3 +1457,3 @@ async def get_pypi_version(package_name: str, timeout: float = 10.0) -> str | No
     except Exception as e:
-        logger.debug(f"PyPI lookup error for {package_name}: {e}")
+        logger.debug(f"PyPI lookup error for {package_name}: {exception_text(e)}")
         return None
@@ -1500,3 +1501,3 @@ async def get_cargo_version(crate_name: str, timeout: float = 10.0) -> str | Non
     except Exception as e:
-        logger.debug(f"crates.io lookup error for {crate_name}: {e}")
+        logger.debug(f"crates.io lookup error for {crate_name}: {exception_text(e)}")
         return None
@@ -1554,3 +1555,3 @@ async def get_docker_version(image_name: str, timeout: float = 10.0) -> str | No
     except Exception as e:
-        logger.debug(f"Docker Hub lookup error for {image_name}: {e}")
+        logger.debug(f"Docker Hub lookup error for {image_name}: {exception_text(e)}")
         return None
````

### Patch — `src/pmcp/package_approvals.py`

````diff
diff --git a/src/pmcp/package_approvals.py b/src/pmcp/package_approvals.py
index 68d7685..f6d9c34 100644
--- a/src/pmcp/package_approvals.py
+++ b/src/pmcp/package_approvals.py
@@ -43,2 +43,4 @@ from typing import TYPE_CHECKING, Any
 
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_json, parse_timestamp
 from pmcp.trust_store import TrustStoreError, trust_store_path
@@ -131,3 +133,5 @@ def _decode(entry: Any) -> PackageApproval:
     except ValueError as exc:
-        raise PackageApprovalError(f"Invalid package approval entry: {exc}") from exc
+        raise PackageApprovalError(
+            f"Invalid package approval entry: {exception_text(exc)}"
+        ) from exc
     if integrity is not None and not isinstance(integrity, str):
@@ -137,6 +141,6 @@ def _decode(entry: Any) -> PackageApproval:
     try:
-        parsed_at = datetime.fromisoformat(str(recorded_at))
+        parsed_at = parse_timestamp(str(recorded_at), source="package approval record")
     except ValueError as exc:
         raise PackageApprovalError(
-            f"Unparseable package approval timestamp: {exc}"
+            f"Unparseable package approval timestamp: {exception_text(exc)}"
         ) from exc
@@ -175,6 +179,6 @@ def _read_store_and_stale(
     try:
-        data = json.loads(raw)
+        data = load_json(raw, source="package approvals")
     except ValueError as exc:
         raise PackageApprovalError(
-            f"Cannot parse package approvals {path}: {exc}"
+            f"Cannot parse package approvals {path}: {exception_text(exc)}"
         ) from exc
````

### Patch — `src/pmcp/parsing.py`

````diff
diff --git a/src/pmcp/parsing.py b/src/pmcp/parsing.py
new file mode 100644
index 0000000..8fc809d
--- /dev/null
+++ b/src/pmcp/parsing.py
@@ -0,0 +1,156 @@
+"""Parse structured text without ever echoing it (Consiliency/pmcp#297, rev 7).
+
+A parser's failure can quote what it rejected, and not only through its own
+error type: PyYAML's ``MarkedYAMLError`` renders a snippet of the input, and
+a YAML tag makes it run a *constructor* whose plain ``ValueError`` /
+``KeyError`` quotes the value (``k: !!int <value>`` raises
+``ValueError: invalid literal for int() with base 10: '<value>'``). So a
+failure is classified by **where it happened**, not by its type: every parse
+of structured text in pmcp goes through one helper per format below, and any
+exception raised while parsing or constructing becomes a :class:`ParseError`
+that names the format, the source pmcp chose to describe (a path, "npm
+registry response") and, where the parser recorded one, the line and column
+-- never the input. It is raised outside the ``except`` block, so it chains
+nothing (``__cause__`` and ``__context__`` are both ``None``).
+
+Each :class:`ParseError` also subclasses the error type its format's callers
+already catch (``yaml.YAMLError``, ``json.JSONDecodeError`` and so
+``ValueError``), so no caller's ``except`` clause changes meaning.
+``tests/test_parse_error_echo.py`` fails on any direct parser call in
+``src/pmcp`` outside this module.
+"""
+
+from __future__ import annotations
+
+import json
+from datetime import datetime
+from typing import IO, Any
+
+import yaml
+
+
+class ParseError(ValueError):
+    """A structured-text parse failed. Its text is value-free by construction."""
+
+    def __init__(
+        self,
+        kind: str,
+        source: str,
+        line: int | None = None,
+        column: int | None = None,
+        cause: str | None = None,
+    ) -> None:
+        self.kind = kind
+        self.source = source
+        self.line = line
+        self.column = column
+        #: The class of the exception the parser raised (``ParserError``,
+        #: ``ValueError`` from a tag's constructor, ``RecursionError``, ...).
+        self.cause = cause
+        ValueError.__init__(self, self._text())
+
+    def _text(self) -> str:
+        where = (
+            f" at line {self.line}, column {self.column}"
+            if isinstance(self.line, int) and isinstance(self.column, int)
+            else ""
+        )
+        why = f" ({self.cause})" if self.cause else ""
+        return f"could not parse {self.kind} {self.source}{where}{why}"
+
+    def __str__(self) -> str:
+        return self._text()
+
+    def __reduce__(self) -> Any:  # pragma: no cover - pickling support
+        return (
+            type(self),
+            (self.kind, self.source, self.line, self.column, self.cause),
+        )
+
+
+class YAMLParseError(ParseError, yaml.YAMLError):
+    """A YAML parse (or construction) failed."""
+
+
+class JSONParseError(ParseError, json.JSONDecodeError):
+    """A JSON parse failed. ``doc`` is empty: it would be the input."""
+
+    def __init__(
+        self,
+        kind: str,
+        source: str,
+        line: int | None = None,
+        column: int | None = None,
+        cause: str | None = None,
+    ) -> None:
+        ParseError.__init__(self, kind, source, line, column, cause)
+        # `json.JSONDecodeError`'s attributes, without the input.
+        self.msg = "could not parse JSON"
+        self.doc = ""
+        self.pos = 0
+        self.lineno = line if isinstance(line, int) else 0
+        self.colno = column if isinstance(column, int) else 0
+
+
+class TimestampParseError(ParseError):
+    """An ISO-8601 timestamp parse failed."""
+
+
+def _yaml_position(error: BaseException) -> tuple[int | None, int | None]:
+    if isinstance(error, yaml.MarkedYAMLError):
+        mark = error.problem_mark or error.context_mark
+        if mark is not None:
+            return mark.line + 1, mark.column + 1
+    return None, None
+
+
+def load_yaml(stream: str | bytes | IO[Any], *, source: str) -> Any:
+    """``yaml.safe_load(stream)``; any failure is a :class:`YAMLParseError`."""
+    failure: tuple[int | None, int | None, str] | None = None
+    try:
+        return yaml.safe_load(stream)
+    except Exception as error:  # noqa: BLE001 -- classified by origin
+        line, column = _yaml_position(error)
+        failure = (line, column, type(error).__name__)
+    line, column, cause = failure
+    raise YAMLParseError("YAML", source, line, column, cause=cause)
+
+
+def load_json(
+    text: str | bytes | bytearray, *, source: str, encoding: str | None = None
+) -> Any:
+    """``json.loads(text)``; any failure is a :class:`JSONParseError`.
+
+    With ``encoding``, bytes are decoded first (``json.loads(b.decode(enc))``)
+    inside the same classification, so a ``UnicodeDecodeError`` -- whose text
+    quotes the offending byte -- is a parse failure too. Without it, bytes go
+    to ``json.loads`` as they are, which detects UTF-8/16/32 itself.
+    """
+    failure: tuple[int | None, int | None, str] | None = None
+    try:
+        if encoding is not None and isinstance(text, (bytes, bytearray)):
+            text = bytes(text).decode(encoding)
+        return json.loads(text)
+    except Exception as error:  # noqa: BLE001 -- classified by origin
+        if isinstance(error, json.JSONDecodeError):
+            failure = (error.lineno, error.colno, type(error).__name__)
+        else:
+            failure = (None, None, type(error).__name__)
+    line, column, cause = failure
+    raise JSONParseError("JSON", source, line, column, cause=cause)
+
+
+def load_json_file(handle: IO[Any], *, source: str) -> Any:
+    """``json.load(handle)`` through :func:`load_json`."""
+    return load_json(handle.read(), source=source)
+
+
+def parse_timestamp(text: str, *, source: str) -> datetime:
+    """``datetime.fromisoformat(text)``; any failure is a
+    :class:`TimestampParseError`."""
+    failure: str | None = None
+    try:
+        return datetime.fromisoformat(text)
+    except Exception as error:  # noqa: BLE001 -- classified by origin
+        failure = type(error).__name__
+    raise TimestampParseError("timestamp", source, cause=failure)
````

### Patch — `src/pmcp/policy/policy.py`

````diff
diff --git a/src/pmcp/policy/policy.py b/src/pmcp/policy/policy.py
index ec5ee42..fc4bfd3 100644
--- a/src/pmcp/policy/policy.py
+++ b/src/pmcp/policy/policy.py
@@ -14,4 +14,3 @@ from typing import TYPE_CHECKING, Any, Literal
 
-import yaml
-
+from pmcp.argument_errors import exception_text
 from pmcp.project_consent import log_refusal, read_and_gate
@@ -26,2 +25,3 @@ from pmcp.types import (
 from pmcp.auth import _sanitize_base
+from pmcp.parsing import load_json, load_yaml
 from pmcp.redaction_additive import (
@@ -362,3 +362,3 @@ class PolicyManager:
                 raise ValueError(
-                    f"Failed to load explicit policy {policy_path}: {e}"
+                    f"Failed to load explicit policy {policy_path}: {exception_text(e)}"
                 ) from e
@@ -382,3 +382,3 @@ class PolicyManager:
         logger.warning(
-            f"Could not parse policy file {policy_path}: {error}. {consequence}"
+            f"Could not parse policy file {policy_path}: {exception_text(error)}. {consequence}"
         )
@@ -415,5 +415,5 @@ class PolicyManager:
             if policy_path.suffix in (".yaml", ".yml"):
-                data = yaml.safe_load(content)
+                data = load_yaml(content, source="policy file")
             else:
-                data = json.loads(content)
+                data = load_json(content, source="policy file")
         except Exception as e:
@@ -421,3 +421,3 @@ class PolicyManager:
                 raise ValueError(
-                    f"Failed to load explicit policy {policy_path}: {e}"
+                    f"Failed to load explicit policy {policy_path}: {exception_text(e)}"
                 ) from e
@@ -435,6 +435,6 @@ class PolicyManager:
                 raise ValueError(
-                    f"Failed to load explicit policy {policy_path}: {e}"
+                    f"Failed to load explicit policy {policy_path}: {exception_text(e)}"
                 ) from e
             raise ValueError(
-                f"Invalid policy file {policy_path}: {e}. "
+                f"Invalid policy file {policy_path}: {exception_text(e)}. "
                 "Refusing to start rather than fall back to an unrestricted gateway."
@@ -887,3 +887,3 @@ class PolicyManager:
             try:
-                result = json.loads(final_str)
+                result = load_json(final_str, source="redacted output")
             except json.JSONDecodeError:
````

### Patch — `src/pmcp/provision_gate.py`

````diff
diff --git a/src/pmcp/provision_gate.py b/src/pmcp/provision_gate.py
index ee8bbd4..2fb99f4 100644
--- a/src/pmcp/provision_gate.py
+++ b/src/pmcp/provision_gate.py
@@ -42,2 +42,3 @@ from typing import TYPE_CHECKING, Literal
 
+from pmcp.argument_errors import exception_text
 from pmcp.package_approvals import is_package_approved
@@ -465,3 +466,3 @@ def evaluate_provision(
             getattr(server_config, "name", None),
-            exc,
+            exception_text(exc),
         )
````

### Patch — `src/pmcp/redaction_additive.py`

````diff
diff --git a/src/pmcp/redaction_additive.py b/src/pmcp/redaction_additive.py
index 085c9e8..5e4ddb7 100644
--- a/src/pmcp/redaction_additive.py
+++ b/src/pmcp/redaction_additive.py
@@ -28,3 +28,2 @@ from __future__ import annotations
 import bisect
-import json
 import re
@@ -33,2 +32,4 @@ from urllib.parse import unquote
 
+from pmcp.parsing import load_json
+
 #: Test-only work counter. None in production; the complexity tests set it to
@@ -1358,3 +1359,3 @@ def _clip_to_json_strings(text: str, spans: list[Span]) -> list[Span]:
     try:
-        json.loads(text)
+        load_json(text, source="redaction candidate")
     except (ValueError, RecursionError):
````

### Patch — `src/pmcp/scoped_advisor_audit.py`

````diff
diff --git a/src/pmcp/scoped_advisor_audit.py b/src/pmcp/scoped_advisor_audit.py
index 76d96e6..fdd7a90 100644
--- a/src/pmcp/scoped_advisor_audit.py
+++ b/src/pmcp/scoped_advisor_audit.py
@@ -16,2 +16,10 @@ from urllib.parse import urlsplit, urlunsplit
 import jsonschema
+import pydantic
+
+from pmcp.argument_errors import (
+    model_error_path,
+    schema_error_keyword,
+    schema_error_path,
+)
+from pmcp.parsing import load_json
 
@@ -44,3 +52,3 @@ def validate_scoped_advisor_audit(path: Path) -> list[dict[str, Any]]:
         records = [
-            json.loads(line)
+            load_json(line, source="scoped advisor audit record")
             for line in Path(path).read_text(encoding="utf-8").splitlines()
@@ -140,66 +148,2 @@ def _public_source_hash(value: Any) -> str | None:
 
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
@@ -306,3 +250,3 @@ class ScopedAdvisorAudit:
         gateway_tool: str,
-        error: jsonschema.ValidationError,
+        error: jsonschema.ValidationError | pydantic.ValidationError,
         schema: dict[str, Any],
@@ -310,3 +254,8 @@ class ScopedAdvisorAudit:
     ) -> None:
-        """Record a ``tools/call`` the input-schema gate rejected (Consiliency/pmcp#296).
+        """Record a ``tools/call`` the input-schema gate rejected (Consiliency/pmcp#296),
+        or the tool's own argument model did (Consiliency/pmcp#297).
+
+        A model rejection's path is its first error's location, redacted the
+        same way; its validator is ``None`` (a pydantic error type is not a
+        JSON Schema keyword).
 
@@ -329,4 +278,8 @@ class ScopedAdvisorAudit:
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
````

### Patch — `src/pmcp/server.py`

````diff
diff --git a/src/pmcp/server.py b/src/pmcp/server.py
index 0a6ef28..6d85733 100644
--- a/src/pmcp/server.py
+++ b/src/pmcp/server.py
@@ -14,2 +14,3 @@ from typing import Any, Literal
 import jsonschema
+import pydantic
 from mcp.server import Server
@@ -39,2 +40,8 @@ from mcp.types import (
 
+from pmcp.argument_errors import (
+    describe_model_error,
+    describe_schema_error,
+    exception_text,
+    install_log_scrubber,
+)
 from pmcp.client.manager import ClientManager
@@ -71,3 +78,7 @@ from pmcp.subscriptions import BusCatalogEventSink
 from pmcp.summary import generate_capability_summary
-from pmcp.tools.handlers import GatewayTools, get_gateway_tool_definitions
+from pmcp.tools.handlers import (
+    GATEWAY_TOOL_INPUT_MODELS,
+    GatewayTools,
+    get_gateway_tool_definitions,
+)
 from pmcp.types import (
@@ -125,2 +136,5 @@ class GatewayServer:
     ) -> None:
+        # Idempotent; again here in case a record factory was replaced since
+        # import (Consiliency/pmcp#297).
+        install_log_scrubber()
         self._project_root = project_root
@@ -337,2 +351,4 @@ class GatewayServer:
                     )
+                # Never `e.message`: for `type`, `pattern`, `enum` and length
+                # errors it quotes the rejected value (Consiliency/pmcp#297).
                 return CallToolResult(
@@ -341,3 +357,5 @@ class GatewayServer:
                         TextContent(
-                            type="text", text=f"Input validation error: {e.message}"
+                            type="text",
+                            text="Input validation error: "
+                            + describe_schema_error(e, tool.input_schema, arguments),
                         )
@@ -479,3 +497,37 @@ class GatewayServer:
             except Exception as e:
-                logger.error(f"Tool execution error: {e}")
+                # A `ValidationError`'s text renders the rejected value
+                # (pydantic's `input_value=...`, a validator's own message,
+                # jsonschema's `message`), so it is described from its
+                # structure instead, in the log, the response and the audit
+                # (Consiliency/pmcp#297). The tool's own argument model
+                # rejecting the call is described against the tool's schema;
+                # anything else goes through `exception_text`, which is
+                # `str(e)` for every exception that is not (and does not
+                # embed) a validation error.
+                input_model = GATEWAY_TOOL_INPUT_MODELS.get(audited_name or "")
+                rejected_by_model = (
+                    isinstance(e, pydantic.ValidationError)
+                    and input_model is not None
+                    and e.title == input_model.__name__
+                )
+                if (
+                    isinstance(e, pydantic.ValidationError)
+                    and rejected_by_model
+                    and tool is not None
+                ):
+                    reason = describe_model_error(e, tool.input_schema, arguments)
+                    described = f"Invalid arguments: {reason}"
+                    logger.error(
+                        "Tool execution error: invalid arguments for %s: %s",
+                        audited_name,
+                        reason,
+                    )
+                elif tool is None:
+                    # Only an unregistered name raises here; it is the
+                    # caller's string, so it is not logged (Consiliency/pmcp#297).
+                    described = exception_text(e)
+                    logger.error("Tool execution error: unknown gateway tool")
+                else:
+                    described = exception_text(e)
+                    logger.error(f"Tool execution error: {described}")
                 try:
@@ -487,8 +539,20 @@ class GatewayServer:
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
@@ -509,3 +573,8 @@ class GatewayServer:
                         type="text",
-                        text=json.dumps({"error": True, "message": str(e)[:400]}),
+                        text=json.dumps(
+                            {
+                                "error": True,
+                                "message": described[:400],
+                            }
+                        ),
                     )
@@ -735,3 +804,5 @@ class GatewayServer:
         except Exception as e:
-            logger.warning(f"Failed to load manifest startup configs: {e}")
+            logger.warning(
+                f"Failed to load manifest startup configs: {exception_text(e)}"
+            )
 
@@ -854,3 +925,3 @@ class GatewayServer:
             except Exception as e:
-                logger.warning(f"Failed to auto-generate cache: {e}")
+                logger.warning(f"Failed to auto-generate cache: {exception_text(e)}")
 
@@ -1020,3 +1091,3 @@ class GatewayServer:
         except Exception as e:
-            logger.error(f"Error during shutdown: {e}")
+            logger.error(f"Error during shutdown: {exception_text(e)}")
         finally:
````

### Patch — `src/pmcp/subscriptions.py`

````diff
diff --git a/src/pmcp/subscriptions.py b/src/pmcp/subscriptions.py
index f05ef50..007b47b 100644
--- a/src/pmcp/subscriptions.py
+++ b/src/pmcp/subscriptions.py
@@ -50,2 +50,4 @@ from mcp.shared.subscriptions import (
 
+from pmcp.argument_errors import safe_exc_info
+
 __all__ = ["CatalogEventSink", "BusCatalogEventSink"]
@@ -189,3 +191,3 @@ class BusCatalogEventSink:
             await self._bus.publish(kind())
-        except Exception:
+        except Exception as exc:
             # Isolate a raising bus from the drain, matching
@@ -193,2 +195,5 @@ class BusCatalogEventSink:
             # contract -- one bad publish must not stop the next drain.
-            logger.exception("subscription bus publish raised; catalog event dropped")
+            logger.error(
+                "subscription bus publish raised; catalog event dropped",
+                exc_info=safe_exc_info(exc),
+            )
````

### Patch — `src/pmcp/templates/code_snippets_loader.py`

````diff
diff --git a/src/pmcp/templates/code_snippets_loader.py b/src/pmcp/templates/code_snippets_loader.py
index 00e741c..02c2e70 100644
--- a/src/pmcp/templates/code_snippets_loader.py
+++ b/src/pmcp/templates/code_snippets_loader.py
@@ -12,3 +12,4 @@ from typing import TYPE_CHECKING
 
-import yaml
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_yaml
 
@@ -46,3 +47,3 @@ class CodeSnippetsLoader:
             with open(self._templates_path) as f:
-                data = yaml.safe_load(f)
+                data = load_yaml(f, source="code snippet templates")
 
@@ -63,3 +64,3 @@ class CodeSnippetsLoader:
             print(
-                f"Warning: Failed to load code snippets from {self._templates_path}: {e}"
+                f"Warning: Failed to load code snippets from {self._templates_path}: {exception_text(e)}"
             )
````

### Patch — `src/pmcp/tools/handlers.py`

````diff
diff --git a/src/pmcp/tools/handlers.py b/src/pmcp/tools/handlers.py
index 09e9f34..e68f542 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -22,2 +22,3 @@ from mcp.types import Tool
 from pydantic import BaseModel
+from pmcp.argument_errors import exception_text, safe_exc_info
 from pmcp import __version__ as PMCP_VERSION
@@ -202,2 +203,3 @@ from pmcp.manifest.loader import (
 )
+from pmcp.parsing import load_json, load_json_file
 
@@ -1016,6 +1018,6 @@ class GatewayTools:
             with open(path) as f:
-                data = json.load(f)
+                data = load_json_file(f, source="provisioned registry")
             return {k: v for k, v in data.items() if isinstance(k, str)}
         except Exception as e:
-            logger.warning(f"Could not load provisioned registry: {e}")
+            logger.warning(f"Could not load provisioned registry: {exception_text(e)}")
             return {}
@@ -1030,3 +1032,3 @@ class GatewayTools:
         except Exception as e:
-            logger.warning(f"Could not save provisioned registry: {e}")
+            logger.warning(f"Could not save provisioned registry: {exception_text(e)}")
 
@@ -1831,3 +1833,3 @@ class GatewayTools:
                 )
-            auth_challenge = self._auth_challenge_from_message(str(e))
+            auth_challenge = self._auth_challenge_from_message(exception_text(e))
             auth_state = "none"
@@ -1909,3 +1911,5 @@ class GatewayTools:
             except Exception as e:
-                logger.warning(f"Failed to load manifest startup configs: {e}")
+                logger.warning(
+                    f"Failed to load manifest startup configs: {exception_text(e)}"
+                )
 
@@ -1915,3 +1919,5 @@ class GatewayTools:
             except Exception as e:
-                logger.warning(f"Failed to restore provisioned servers: {e}")
+                logger.warning(
+                    f"Failed to restore provisioned servers: {exception_text(e)}"
+                )
 
@@ -2161,3 +2167,3 @@ class GatewayTools:
                 started_at=audit_started_at,
-                error=str(e),
+                error=exception_text(e),
             )
@@ -2169,3 +2175,3 @@ class GatewayTools:
                 revision_id="error",
-                errors=[str(e)],
+                errors=[exception_text(e)],
                 pending_requests_seen=pending_seen,
@@ -4269,3 +4275,5 @@ class GatewayTools:
                     )
-                logger.error(f"Failed to connect remote server {server_name}: {e}")
+                logger.error(
+                    f"Failed to connect remote server {server_name}: {exception_text(e)}"
+                )
                 self._record_feedback_event(
@@ -4351,3 +4359,5 @@ class GatewayTools:
         except Exception as e:
-            logger.error(f"Failed to start provisioning {server_name}: {e}")
+            logger.error(
+                f"Failed to start provisioning {server_name}: {exception_text(e)}"
+            )
             self._record_feedback_event(
@@ -4439,3 +4449,3 @@ class GatewayTools:
                         auth_event="url_elicitation_required",
-                        error=str(e),
+                        error=exception_text(e),
                     )
@@ -4444,3 +4454,3 @@ class GatewayTools:
                         server=server_name,
-                        message=str(e),
+                        message=exception_text(e),
                         auth_state="elicitation_required",
@@ -4607,3 +4617,3 @@ class GatewayTools:
                 auth_event="missing_credential",
-                error=str(exc),
+                error=exception_text(exc),
             )
@@ -4612,3 +4622,3 @@ class GatewayTools:
                 server=server_name,
-                message=str(exc),
+                message=exception_text(exc),
                 auth_state="missing_auth",
@@ -4816,3 +4826,3 @@ class GatewayTools:
             # granted permission to send.
-            logger.warning("Feedback submission raised: %s", exc)
+            logger.warning("Feedback submission raised: %s", exception_text(exc))
             result = progress.abandon()
@@ -5090,3 +5100,3 @@ class GatewayTools:
                 package_name=package_name,
-                message=f"Failed to run update probe: {e}",
+                message=f"Failed to run update probe: {exception_text(e)}",
             )
@@ -5320,3 +5330,3 @@ class GatewayTools:
                                 f"Failed to persist descriptions cache after updating "
-                                f"'{server_name}': {e}"
+                                f"'{server_name}': {exception_text(e)}"
                             )
@@ -5489,3 +5499,7 @@ class GatewayTools:
         except Exception as exc:  # resolution fails closed; see package_identity
-            logger.warning("Package identity lookup raised for %r: %s", package, exc)
+            logger.warning(
+                "Package identity lookup raised for %r: %s",
+                package,
+                exception_text(exc),
+            )
 
@@ -5606,6 +5620,8 @@ class GatewayTools:
 
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
@@ -5677,6 +5693,9 @@ class GatewayTools:
         except Exception as e:
-            logger.error(f"provision_status handler failed: {e}", exc_info=True)
+            logger.error(
+                f"provision_status handler failed: {exception_text(e)}",
+                exc_info=safe_exc_info(e),
+            )
             # Return a safe error response instead of crashing
             return ProvisionJobStatus(
-                job_id=input_data.get("job_id", "unknown"),
+                job_id=job_id,
                 server="unknown",
@@ -5771,5 +5790,8 @@ class GatewayTools:
         except Exception as e:
-            logger.error(f"Handoff failed for {job_server_name}: {e}", exc_info=True)
+            logger.error(
+                f"Handoff failed for {job_server_name}: {exception_text(e)}",
+                exc_info=safe_exc_info(e),
+            )
             job.status = "failed"
-            job.error = f"Handoff failed: {e}"
+            job.error = f"Handoff failed: {exception_text(e)}"
             # Kill the orphaned process
@@ -5817,3 +5839,3 @@ class GatewayTools:
         except Exception as e:
-            logger.error(f"Failed to refresh after install: {e}")
+            logger.error(f"Failed to refresh after install: {exception_text(e)}")
             refresh_error = self._sanitize_error(e)
@@ -6046,3 +6068,3 @@ class GatewayTools:
                 server_name=parsed.server_name,
-                error=str(e),
+                error=exception_text(e),
             )
@@ -6090,3 +6112,3 @@ class GatewayTools:
                 task_id=parsed.task_id,
-                error=str(e),
+                error=exception_text(e),
             )
@@ -6126,3 +6148,5 @@ class GatewayTools:
                     try:
-                        decoded = json.loads(result_payload)
+                        decoded = load_json(
+                            result_payload, source="tool result payload"
+                        )
                     except json.JSONDecodeError:
@@ -6164,3 +6188,3 @@ class GatewayTools:
                 task_id=parsed.task_id,
-                error=str(e),
+                error=exception_text(e),
             )
````

### Patch — `src/pmcp/transport/http.py`

````diff
diff --git a/src/pmcp/transport/http.py b/src/pmcp/transport/http.py
index 93d3c58..8a48f4d 100644
--- a/src/pmcp/transport/http.py
+++ b/src/pmcp/transport/http.py
@@ -22,3 +22,2 @@ import hmac
 import ipaddress
-import json
 import logging
@@ -44,2 +43,3 @@ from pmcp.auth import (
 )
+from pmcp.parsing import load_json
 from pmcp.types import GatewayDiagnosticsInfo
@@ -258,9 +258,20 @@ def _split_host_port(value: str, default_port: str) -> tuple[str, str]:
 def _origin_host_port(origin: str) -> tuple[str, str] | None:
-    """Return (hostname, port) for an Origin header value, or None if unparseable."""
-    parsed = urlparse(origin)
-    if not parsed.scheme or not parsed.hostname:
+    """Return (hostname, port) for an Origin header value, or None if unparseable.
+
+    ``urlparse`` and ``.port`` raise ``ValueError`` on a malformed authority
+    (``Port could not be cast to integer value as '<port>'``), quoting the
+    caller's header; an unparseable Origin is a rejected one, never a 500
+    (Consiliency/pmcp#297).
+    """
+    try:
+        parsed = urlparse(origin)
+        hostname = parsed.hostname
+        explicit_port = parsed.port
+    except ValueError:
+        return None
+    if not parsed.scheme or not hostname:
         return None
     default_port = "443" if parsed.scheme == "https" else "80"
-    port = str(parsed.port) if parsed.port is not None else default_port
-    return parsed.hostname, port
+    port = str(explicit_port) if explicit_port is not None else default_port
+    return hostname, port
 
@@ -643,3 +654,3 @@ def create_http_app(
             try:
-                body_method = json.loads(body_bytes).get("method")
+                body_method = load_json(body_bytes, source="request body").get("method")
             except Exception:
````

### Patch — `src/pmcp/trust_store.py`

````diff
diff --git a/src/pmcp/trust_store.py b/src/pmcp/trust_store.py
index dd9ed8c..5b4b1d3 100644
--- a/src/pmcp/trust_store.py
+++ b/src/pmcp/trust_store.py
@@ -44,2 +44,4 @@ from pathlib import Path
 from typing import Any
+from pmcp.argument_errors import exception_text
+from pmcp.parsing import load_json, parse_timestamp
 
@@ -269,5 +271,7 @@ def _decode(entry: Any) -> TrustRecord:
     try:
-        parsed_at = datetime.fromisoformat(str(recorded_at))
+        parsed_at = parse_timestamp(str(recorded_at), source="trust store record")
     except ValueError as exc:
-        raise TrustStoreError(f"Unparseable trust timestamp: {exc}") from exc
+        raise TrustStoreError(
+            f"Unparseable trust timestamp: {exception_text(exc)}"
+        ) from exc
 
@@ -297,5 +301,7 @@ def _read_store(path: Path) -> list[TrustRecord]:
     try:
-        data = json.loads(raw)
+        data = load_json(raw, source="trust store")
     except ValueError as exc:
-        raise TrustStoreError(f"Cannot parse trust store {path}: {exc}") from exc
+        raise TrustStoreError(
+            f"Cannot parse trust store {path}: {exception_text(exc)}"
+        ) from exc
 
````

### Patch — `src/pmcp/types.py`

````diff
diff --git a/src/pmcp/types.py b/src/pmcp/types.py
index 7dc8e7c..57f1baf 100644
--- a/src/pmcp/types.py
+++ b/src/pmcp/types.py
@@ -11,2 +11,9 @@ from pydantic import BaseModel, ConfigDict, Field, field_validator, model_valida
 
+from pmcp.argument_errors import (
+    CORRELATION_ID_CHARSET,
+    PACKAGE_NAME_INVALID,
+    SCOPED_CORRELATION_INCOMPLETE,
+    argument_error,
+)
+from pmcp.parsing import parse_timestamp
 from pmcp.validation import is_valid_package_name, version_separator_index
@@ -551,3 +558,3 @@ class McpTaskInfo(BaseModel):
                 candidate = f"{candidate[:-1]}+00:00"
-            return datetime.fromisoformat(candidate).timestamp()
+            return parse_timestamp(candidate, source="task timestamp").timestamp()
         return value
@@ -886,3 +893,3 @@ class InvokeInput(GatewayArguments):
         if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) is None:
-            raise ValueError("correlation IDs may contain only alphanumerics and ._:-")
+            raise argument_error(CORRELATION_ID_CHARSET)
         return value
@@ -899,5 +906,3 @@ class InvokeInput(GatewayArguments):
         ):
-            raise ValueError(
-                "scoped advisor correlation fields must be supplied together"
-            )
+            raise argument_error(SCOPED_CORRELATION_INCOMPLETE)
         return self
@@ -1394,7 +1399,3 @@ class RegisterDiscoveredServerInput(GatewayArguments):
         if not is_valid_package_name(value):
-            raise ValueError(
-                "package must be a valid npm/pypi identifier "
-                "(no leading dash, whitespace, path separators, or shell "
-                "metacharacters)"
-            )
+            raise argument_error(PACKAGE_NAME_INVALID)
         return value
````

### Patch — `tests/test_argument_error_echo.py`

````diff
diff --git a/tests/test_argument_error_echo.py b/tests/test_argument_error_echo.py
new file mode 100644
index 0000000..aff0567
--- /dev/null
+++ b/tests/test_argument_error_echo.py
@@ -0,0 +1,1504 @@
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
+import gc
+import functools
+from unittest import mock
+import hashlib
+import json
+import re
+import logging
+import traceback
+import typing
+from pathlib import Path
+from typing import Any
+
+import jsonschema
+import pytest
+from mcp.types import CallToolRequestParams
+from pydantic import BaseModel, ValidationError, field_validator
+from pydantic_core import PydanticCustomError
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
+
+def _digest(seed: str, length: int, alphabet: str = "0123456789abcdef") -> str:
+    """`length` deterministic high-entropy characters of `alphabet`."""
+    out, counter = "", 0
+    while len(out) < length:
+        block = hashlib.sha256(f"pmcp-297:{seed}:{counter}".encode()).digest()
+        out += "".join(alphabet[b % len(alphabet)] for b in block)
+        counter += 1
+    return out[:length]
+
+
+_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
+
+#: Sentinel families, two lengths each. A leak can be conditional on the
+#: value's shape (the rev 1 board's mutant S8 echoed only `isalpha()`
+#: values), so the sweep runs every case once per family: hex, letters only,
+#: a provider-token shape, a value with spaces, non-ASCII (written as `\u`
+#: escapes: the test source stays ASCII), and digits only.
+_FAMILIES: dict[str, tuple[str, str]] = {
+    "hex": (
+        "Sq" + _digest("hex-a", 22) + "Zx",
+        "Sq" + _digest("hex-b", 38) + "Zx",
+    ),
+    "alpha": (_digest("alpha-a", 26, _LETTERS), _digest("alpha-b", 42, _LETTERS)),
+    "token": ("sk-proj-" + _digest("token-a", 24), "sk-proj-" + _digest("token-b", 40)),
+    "spaced": (
+        "Bearer " + " ".join(_digest("sp-a", 24)[i : i + 6] for i in range(0, 24, 6)),
+        "Bearer " + " ".join(_digest("sp-b", 42)[i : i + 6] for i in range(0, 42, 6)),
+    ),
+    "unicode": (
+        "\u00e9\u4e2d" + _digest("uni-a", 24) + "\u00fc",
+        "\u00e9\u4e2d" + _digest("uni-b", 40) + "\u00fc",
+    ),
+    "digits": (_digest("dig-a", 26, "0123456789"), _digest("dig-b", 42, "0123456789")),
+}
+#: The hex pair, for the tests that need one family.
+_SENTINELS = _FAMILIES["hex"]
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
+    """Every window of the sentinel -- raw, JSON-escaped, `repr`-escaped and
+    `unicode_escape`d -- and each hash of it, a log or record could carry."""
+    forms: set[str] = set()
+    for spelling in {
+        sentinel,
+        json.dumps(sentinel)[1:-1],
+        repr(sentinel)[1:-1],
+        sentinel.encode("unicode_escape").decode("ascii"),
+    }:
+        forms |= {spelling[i : i + _WINDOW] for i in range(len(spelling) - _WINDOW + 1)}
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
+    return [
+        (label, value)
+        for label, value in _candidate_values(node, s)
+        if not jsonschema.validators.validator_for(node)(node).is_valid(value)
+    ]
+
+
+def _candidate_values(node: dict[str, Any], s: str) -> list[tuple[str, Any]]:
+    """The shapes `_invalid_values` tries (unchecked: a case's build reuses
+    the shape its construction already checked)."""
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
+    return candidates
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
+    return copy.deepcopy(_checked_baseline(tool.name))
+
+
+@functools.cache
+def _checked_baseline(name: str) -> dict[str, Any]:
+    tool = _tools()[name]
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
+    return arguments
+
+
+@pytest.mark.parametrize("name", sorted(_tools()))
+def test_every_decorated_baseline_passes_the_gate(name: str) -> None:
+    """Decorations alone are accepted, so a case's rejection is its own."""
+    tool = _tools()[name]
+    for sentinels in _FAMILIES.values():
+        for s in sentinels:
+            jsonschema.validate(_decorated(tool, s), tool.input_schema)
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
+                    value = dict(_candidate_values(node, s))[label]
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
+                            arguments[field_name] = dict(_candidate_values(node, s))[
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
+#: The attributes every `LogRecord` has; anything else came in via `extra=`.
+_STANDARD_RECORD_KEYS = frozenset(
+    logging.LogRecord("x", logging.INFO, "x", 1, "x", None, None).__dict__
+) | {"message", "asctime"}
+
+
+def _record_text(record: logging.LogRecord) -> str:
+    """Everything a log handler could see in `record`, whatever formats it:
+    pmcp's text and JSON formatters, the raw message and args, every
+    attribute (`extra=` fields included), and the traceback of `exc_info`
+    and `stack_info` rendered in full (pmcp's JSON formatter drops them)."""
+    parts = [formatter.format(record) for formatter in _pmcp_formatters()]
+    parts.append(repr(record.msg))
+    parts.append(repr(record.args))
+    parts.append(record.getMessage())
+    for key, value in sorted(record.__dict__.items()):
+        if key == "exc_info" and value and value[1] is not None:
+            parts.append("".join(traceback.format_exception(*value)))
+        elif key not in ("msg", "args"):
+            parts.append(f"{key}={value!r}")
+    return "\n".join(parts)
+
+
+def _record_stable(record: logging.LogRecord) -> str:
+    """`record` for the pair differential: both formatters at a fixed time,
+    plus every `extra=` attribute."""
+    extras = {
+        key: repr(value)
+        for key, value in sorted(record.__dict__.items())
+        if key not in _STANDARD_RECORD_KEYS
+    }
+    rendered = [_normalized(record, formatter) for formatter in _pmcp_formatters()]
+    text = "\n".join(rendered) + (f"\nextra={extras!r}" if extras else "")
+    # The one volatile token a downstream call logs: its wall-clock latency.
+    return _ELAPSED.sub("elapsed_ms=N", text)
+
+
+#: Exactly `elapsed_ms=<digits>` in `handlers.py`'s `tool_call` line.
+_ELAPSED = re.compile(r"\belapsed_ms=[0-9]+\b")
+
+
+class _Tap:
+    """Every channel a call can leak into: the response (given), the log
+    (`caplog` at DEBUG, root), stdout/stderr (`capfd`), warnings
+    (`recwarn`), the gateway's in-memory audit-event buffer (what
+    `gateway.health` exposes), and the scoped-audit JSONL."""
+
+    def __init__(
+        self,
+        server: GatewayServer,
+        audit_path: Path | None,
+        caplog: pytest.LogCaptureFixture,
+        capfd: pytest.CaptureFixture[str],
+        recwarn: pytest.WarningsRecorder,
+    ) -> None:
+        self.server, self.audit_path = server, audit_path
+        self.caplog, self.capfd, self.recwarn = caplog, capfd, recwarn
+        # A server an earlier test left open is finalised by the collector at
+        # an arbitrary moment, and its `ResourceWarning` would land inside
+        # some pair. Collect now; each test here shuts its own servers down.
+        gc.collect()
+
+    def _audit_text(self) -> str:
+        path = self.audit_path
+        return path.read_text() if path is not None and path.exists() else ""
+
+    def _buffer(self) -> list[str]:
+        events = getattr(self.server._gateway_tools, "__dict__", {}).get(
+            "_audit_events", []
+        )
+        return [event.model_dump_json() for event in events]
+
+    def start(self) -> tuple[int, int, str, list[str]]:
+        self.capfd.readouterr()
+        return (
+            len(self.caplog.records),
+            len(self.recwarn),
+            self._audit_text(),
+            self._buffer(),
+        )
+
+    def since(self, mark: tuple[int, int, str, list[str]], response: str) -> _Observed:
+        records = self.caplog.records[mark[0] :]
+        captured = self.capfd.readouterr()
+        warned = [
+            f"{w.category.__name__}: {w.message} @ {w.filename}:{w.lineno}"
+            for w in list(self.recwarn)[mark[1] :]
+        ]
+        raw_audit = self._audit_text()[len(mark[2]) :]
+        buffer = self._buffer()
+        new_events = [event for event in buffer if event not in mark[3]]
+        return _Observed(
+            response=response,
+            log="\n".join(_record_stable(record) for record in records),
+            raw_log="\n".join(_record_text(record) for record in records),
+            streams=captured.out + captured.err,
+            warnings="\n".join(warned),
+            audit=[
+                _stable(json.loads(line)) for line in raw_audit.splitlines() if line
+            ],
+            raw_audit=raw_audit,
+            events="\n".join(new_events),
+        )
+
+
+class _Observed(typing.NamedTuple):
+    response: str
+    log: str
+    raw_log: str
+    streams: str
+    warnings: str
+    audit: list[dict[str, Any]]
+    raw_audit: str
+    events: str
+
+    def leaks(self, sentinel: str) -> list[str]:
+        """The channels that carry `sentinel` in any form."""
+        channels = {
+            "response": self.response,
+            "log": self.raw_log,
+            "stdout/stderr": self.streams,
+            "warnings": self.warnings,
+            "audit": self.raw_audit,
+            "audit-event buffer": self.events,
+        }
+        forms = _forbidden(sentinel)
+        return [
+            name for name, text in channels.items() if any(f in text for f in forms)
+        ]
+
+    def stable(self) -> tuple[Any, ...]:
+        """What must be identical for two sentinels of different length."""
+        return (self.response, self.log, self.streams, self.warnings, self.audit)
+
+    def differs(self, other: _Observed) -> list[tuple[str, Any, Any]]:
+        """The channels where `self` and `other` differ, for a readable failure."""
+        names = ("response", "log", "streams", "warnings", "audit")
+        return [
+            (name, mine, theirs)
+            for name, mine, theirs in zip(names, self.stable(), other.stable())
+            if mine != theirs
+        ]
+
+
+def _event_shape(events: str) -> list[dict[str, Any]]:
+    return [
+        {
+            k: v
+            for k, v in json.loads(line).items()
+            if k not in ("timestamp", "duration_ms", "latency_ms")
+        }
+        for line in events.splitlines()
+        if line
+    ]
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("audited", [False, True], ids=["plain", "scoped-audit"])
+async def test_no_rejected_argument_value_reaches_a_response_log_or_audit(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    audited: bool,
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    server, audit_path = _server(tmp_path, audited=audited)
+    tap = _Tap(server, audit_path, caplog, capfd, recwarn)
+    for family, sentinels in _FAMILIES.items():
+        for case in _CASES:
+            seen = []
+            for sentinel in sentinels:
+                mark = tap.start()
+                result = await _call(server, case.tool, case.build(sentinel))
+                seen.append(tap.since(mark, _rejection(result, case.layer)))
+            for sentinel, observed in zip(sentinels, seen):
+                assert observed.leaks(sentinel) == [], (family, case.label, observed)
+            if family == "hex":
+                # Useful: the rejection names where, and why.
+                _useful(seen[0].response, case.layer, case.expected)
+            # Nothing else about the value -- length, count or hash -- either.
+            assert seen[0].stable() == seen[1].stable(), (family, case.label)
+            assert _event_shape(seen[0].events) == _event_shape(seen[1].events)
+    await server.shutdown()
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
+    await server.shutdown()
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
+        # the raw text does carry it (pydantic truncates it in the middle)
+        assert any(form in str(error) for form in _forbidden(s)), error
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
+#: What `exception_text` makes of each of `_handler_validation_errors`.
+_HANDLER_ERROR_TEXT = (
+    re.compile(
+        r"2 validation errors for McpTaskInfo: \$\.task_id: must be a string; \$\.created_at: "
+    ),
+    re.compile(r"3 validation errors for InvokeInput: \$\.tool_id: must be a string; "),
+    # `x` is no name pmcp's models declare, so it reads as `*`.
+    re.compile(r"schema validation error: \$\.\*: fails its type constraint$"),
+)
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize(
+    "index", [0, 1, 2], ids=["downstream-model", "argument-model", "jsonschema"]
+)
+async def test_a_validation_error_raised_by_a_handler_is_described_not_echoed(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    index: int,
+) -> None:
+    """Not the tool's own argument model, so not "Invalid arguments": the
+    server's arm renders it with `exception_text`, against no schema (rev 2,
+    N6: a jsonschema error names its keyword, never a constraint)."""
+    caplog.set_level(logging.DEBUG)
+    server, _ = _server(tmp_path, audited=False)
+    tap = _Tap(server, None, caplog, capfd, recwarn)
+    seen = []
+    for family, sentinels in _FAMILIES.items():
+        for s in sentinels:
+            server._gateway_tools = _RaisingTools(_handler_validation_errors(s)[index])  # type: ignore[assignment]
+            mark = tap.start()
+            result = await _call(server, "gateway.catalog_search", {"query": "q"})
+            payload = json.loads("".join(block.text for block in result.content))
+            observed = tap.since(mark, json.dumps(payload))
+            assert observed.leaks(s) == [], (family, observed)
+            assert payload["error"] is True
+            assert _HANDLER_ERROR_TEXT[index].match(payload["message"]), payload
+            assert f"Tool execution error: {payload['message']}" in observed.raw_log
+            seen.append(observed.stable())
+    await server.shutdown()
+    assert all(item == seen[0] for item in seen[1:])
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
+class _CollidingErrors(BaseModel):
+    """A validator author reusing pydantic's own error types with the value
+    in the very `ctx` keys a naive renderer would fill a phrase from (the rev
+    1 board's N2)."""
+
+    literal: str | None = None
+    short: str | None = None
+    bounded: int | None = None
+    nested: dict[str, str] | None = None
+
+    @field_validator("literal")
+    @classmethod
+    def _literal(cls, value: str) -> str:
+        raise PydanticCustomError("literal_error", "{expected}", {"expected": value})
+
+    @field_validator("short")
+    @classmethod
+    def _short(cls, value: str) -> str:
+        raise PydanticCustomError(
+            "string_too_short", "{min_length}", {"min_length": value}
+        )
+
+    @field_validator("bounded", mode="before")
+    @classmethod
+    def _bounded(cls, value: Any) -> int:
+        raise PydanticCustomError("greater_than", "{gt}", {"gt": value})
+
+    @field_validator("nested")
+    @classmethod
+    def _nested(cls, value: dict[str, str]) -> dict[str, str]:
+        raise PydanticCustomError("missing", "{input}", {"input": value})
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_a_custom_error_cannot_fill_a_phrase_from_its_context(family: str) -> None:
+    """A constraint is read from the gateway's own schema, never from `ctx`,
+    so a colliding custom error renders the schema's constraint -- or none."""
+    from pmcp.argument_errors import describe_model_error, exception_text
+
+    s = _FAMILIES[family][1]
+    arguments = {"literal": s, "short": s, "bounded": s, "nested": {"k": s}}
+    with pytest.raises(ValidationError) as raised:
+        _CollidingErrors.model_validate(arguments)
+    assert any(form in str(raised.value) for form in _forbidden(s))
+    schema = {
+        "type": "object",
+        "properties": {
+            "literal": {"type": "string", "enum": ["a", "b"]},
+            "short": {"type": "string", "minLength": 3},
+            "bounded": {"type": "integer"},
+            "nested": {"type": "object"},
+        },
+    }
+    for rendered, expected in (
+        (
+            describe_model_error(raised.value, schema, arguments),
+            '$.literal: must be one of ["a", "b"]; $.short: must be at least 3 '
+            "characters; $.bounded: is too small; $.nested: is required",
+        ),
+        (
+            exception_text(raised.value),
+            # No schema here, and a test model's fields are no names pmcp
+            # declares, so the locations read as `*`.
+            "4 validation errors for _CollidingErrors: $.*: is not an "
+            "allowed value; $.*: is too short; $.*: is too small; "
+            "$.*: is required",
+        ),
+    ):
+        assert rendered == expected
+        assert not any(form in rendered for form in _forbidden(s))
+
+
+def test_every_constrained_phrase_names_a_schema_keyword() -> None:
+    """Each constraint comes from a JSON Schema keyword of the gateway's own
+    schema; none from pydantic's `ctx`."""
+    import jsonschema.validators
+
+    from pmcp.argument_errors import _CONSTRAINED_PHRASES
+
+    keywords = jsonschema.validators.Draft202012Validator.VALIDATORS
+    for error_type, (keyword, with_constraint, without) in _CONSTRAINED_PHRASES.items():
+        assert keyword in keywords, error_type
+        assert "{" not in without, error_type
+        assert with_constraint.count("{") == 1, error_type
+
+
+def test_exception_text_describes_a_wrapper_that_embeds_a_validation_error() -> None:
+    """`RuntimeError(f"... {e}") from e` carries the value in its own text."""
+    from pmcp.argument_errors import exception_text, safe_exc_info
+
+    s = _SENTINELS[1]
+    try:
+        try:
+            McpTaskInfo.model_validate({"task_id": "t", "ttl": s})
+        except ValidationError as inner:
+            raise RuntimeError(f"task parse failed: {inner}") from inner
+    except RuntimeError as outer:
+        text = exception_text(outer)
+        assert text.startswith(
+            "RuntimeError: 1 validation error for McpTaskInfo: $.ttl: must be an integer"
+        ), text
+        assert not any(form in text for form in _forbidden(s))
+        assert safe_exc_info(outer) is None
+    plain = RuntimeError("no validation here")
+    assert exception_text(plain) == "no validation here"
+    assert safe_exc_info(plain) is plain
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_describe_exception_renders_a_grouped_validation_error_structurally(
+    family: str,
+) -> None:
+    """`describe_exception` flattens an anyio task group into its leaves --
+    the remote-transport paths' shape -- and each leaf goes through
+    `exception_text`, so a validation error inside a group is described,
+    not echoed."""
+    import builtins
+
+    from pmcp.client.manager import describe_exception
+
+    group_type = getattr(builtins, "ExceptionGroup", None)
+    if group_type is None:  # Python 3.10: anyio's backport
+        from exceptiongroup import ExceptionGroup as group_type
+    s = _FAMILIES[family][1]
+    with pytest.raises(ValidationError) as raised:
+        McpTaskInfo.model_validate({"task_id": "t", "ttl": {"v": s}})
+    leaf = raised.value
+    for group in (
+        group_type("unhandled errors in a TaskGroup", [leaf]),
+        group_type("unhandled errors in a TaskGroup", [RuntimeError("boom"), leaf]),
+    ):
+        text = describe_exception(group)
+        assert "validation error for McpTaskInfo: $.ttl: must be an integer" in text
+        assert not any(form in text for form in _forbidden(s)), text
+
+
+# --- downstream data, through the real handlers (rev 2, board finding B1) ------
+#
+# A downstream server's payload is validated by pmcp's own models
+# (`McpTaskInfo` for every task payload; `ToolInfo`/`ResourceInfo`/
+# `PromptInfo`/`PromptArgumentInfo` for listings). The handlers that catch the
+# failure used to render `str(e)` -- pydantic text with `input_value` -- into
+# the response, the log and the audit-event buffer `gateway.health` exposes.
+# Only the transport is replaced here: `ClientManager._send_request`.
+
+_DOWNSTREAM = "svc"
+
+
+def _payload_keys(function: Any) -> list[str]:
+    """Every literal key `function` reads from a mapping it is handed:
+    `x.get("k")`, `x["k"]`, and `helper(x, "k")` -- derived from its source,
+    so a new field the parser reads is swept without editing this test."""
+    import ast
+    import inspect
+    import textwrap
+
+    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
+    keys: set[str] = set()
+    for node in ast.walk(tree):
+        if isinstance(node, ast.Call):
+            if (
+                isinstance(node.func, ast.Attribute)
+                and node.func.attr == "get"
+                and node.args
+                and isinstance(node.args[0], ast.Constant)
+                and isinstance(node.args[0].value, str)
+            ):
+                keys.add(node.args[0].value)
+            if (
+                len(node.args) == 2
+                and isinstance(node.args[1], ast.Constant)
+                and isinstance(node.args[1].value, str)
+            ):
+                keys.add(node.args[1].value)
+        elif (
+            isinstance(node, ast.Subscript)
+            and isinstance(node.slice, ast.Constant)
+            and isinstance(node.slice.value, str)
+        ):
+            keys.add(node.slice.value)
+    return sorted(keys)
+
+
+def _bad_values(s: str) -> dict[str, Any]:
+    return {
+        "string": s,
+        "object": {s: s, "k": [s]},
+        "array": [s, {s: s}],
+        "number-in-object": {"n": 7, s: [s]},
+    }
+
+
+def _task_positions() -> list[tuple[str, str]]:
+    """(payload key, bad-value shape) pairs the real task parser rejects with
+    a `ValidationError` -- found by running it, not by listing fields."""
+    from pmcp.client.manager import ClientManager
+
+    manager = ClientManager()
+    keys = _payload_keys(ClientManager._task_info_from_payload)
+    assert {"ttl", "pollInterval", "createdAt", "status"} <= set(keys), keys
+    positions = []
+    for key in keys:
+        for shape, value in _bad_values(_SENTINELS[0]).items():
+            payload = (
+                {"taskId": "t", key: value}
+                if key not in ("taskId", "task_id")
+                else {key: value}
+            )
+            try:
+                manager._task_info_from_payload(payload)
+            except ValidationError:
+                positions.append((key, shape))
+    assert len(positions) > 10, positions
+    return positions
+
+
+def _task_server(
+    tmp_path: Path, *, audited: bool
+) -> tuple[GatewayServer, Path | None, dict]:
+    """A server whose one downstream (`svc`) is task-capable and answers every
+    request with `state["payload"]` as its task."""
+    from unittest.mock import MagicMock
+
+    from pmcp.client.manager import ManagedClient
+    from pmcp.config.loader import make_tool_id
+    from pmcp.types import (
+        LocalMcpServerConfig,
+        ResolvedServerConfig,
+        RiskHint,
+        ServerStatus,
+        ServerStatusEnum,
+        ToolInfo,
+    )
+
+    server, audit_path = _server(tmp_path, audited=audited)
+    # The scoped policy allows two research servers; this one stands in.
+    policy = server._policy_manager
+    policy.is_server_allowed = lambda name: True  # type: ignore[method-assign]
+    policy.is_tool_allowed = lambda tool_id: True  # type: ignore[method-assign]
+    manager = server._client_manager
+    tool = ToolInfo(
+        tool_id=make_tool_id(_DOWNSTREAM, "run"),
+        server_name=_DOWNSTREAM,
+        tool_name="run",
+        description="run",
+        short_description="run",
+        input_schema={"type": "object", "properties": {}},
+        execution={"taskSupport": "required"},
+        tags=["svc"],
+        risk_hint=RiskHint.LOW,
+    )
+    manager._tools[tool.tool_id] = tool
+    status = ServerStatus(
+        name=_DOWNSTREAM,
+        status=ServerStatusEnum.ONLINE,
+        tool_count=1,
+        server_capabilities={"tasks": {"listChanged": True}},
+        protocol_version="2025-11-25",
+    )
+    manager._clients[_DOWNSTREAM] = ManagedClient(
+        config=ResolvedServerConfig(
+            name=_DOWNSTREAM,
+            source="custom",
+            config=LocalMcpServerConfig(command="svc"),
+        ),
+        is_remote=True,
+        write_stream=MagicMock(),
+        status=status,
+    )
+    manager._servers[_DOWNSTREAM] = status
+    state: dict[str, Any] = {"payload": {}, "methods": []}
+
+    async def send_request(managed: Any, method: str, params: Any, **_: Any) -> Any:
+        state["methods"].append(method)
+        payload = state["payload"]
+        if method == "tasks/list":
+            return {"tasks": [payload]}
+        if method == "tasks/result":
+            return {"task": payload, "result": {"content": []}}
+        return {"task": payload}
+
+    manager._send_request = send_request  # type: ignore[method-assign]
+    return server, audit_path, state
+
+
+def _task_calls() -> list[tuple[str, dict[str, Any], str]]:
+    """(gateway tool, arguments, downstream method) for every gateway tool
+    that parses a downstream task payload."""
+    target = {"server_name": _DOWNSTREAM, "task_id": "t"}
+    return [
+        ("gateway.tasks_list", {"server_name": _DOWNSTREAM}, "tasks/list"),
+        ("gateway.tasks_get", target, "tasks/get"),
+        ("gateway.tasks_result", target, "tasks/result"),
+        ("gateway.tasks_cancel", target, "tasks/cancel"),
+        (
+            "gateway.invoke",
+            {
+                "tool_id": f"{_DOWNSTREAM}::run",
+                "task": {"enabled": True},
+                **_correlations(),
+            },
+            "tools/call",
+        ),
+    ]
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("audited", [False, True], ids=["plain", "scoped-audit"])
+async def test_no_downstream_value_reaches_a_response_log_or_audit(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    audited: bool,
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    server, audit_path, state = _task_server(tmp_path, audited=audited)
+    tap = _Tap(server, audit_path, caplog, capfd, recwarn)
+    positions = _task_positions()
+    parser = server._client_manager._task_info_from_payload
+
+    def rejects(key: str, value: Any) -> bool:
+        try:
+            parser({"taskId": "t", "status": "working", key: value})
+        except ValidationError:
+            return True
+        return False
+
+    rejected = expected = 0
+    for name, arguments, method in _task_calls():
+        for key, shape in positions:
+            for family, sentinels in _FAMILIES.items():
+                # A value the parser accepts (a digits-only `createdAt` is a
+                # number) is downstream data pmcp returns by design, not a
+                # rejection; only rejected values are this sweep's subject.
+                if not all(rejects(key, _bad_values(s)[shape]) for s in sentinels):
+                    continue
+                expected += 1
+                seen = []
+                for s in sentinels:
+                    state["payload"] = {
+                        "taskId": "t",
+                        "status": "working",
+                        key: _bad_values(s)[shape],
+                    }
+                    state["methods"].clear()
+                    server._client_manager._tasks.clear()
+                    if method == "tasks/cancel":
+                        # Cancel asks downstream only for a live, known task.
+                        server._client_manager._record_task(
+                            _DOWNSTREAM, McpTaskInfo(task_id="t", status="working")
+                        )
+                    mark = tap.start()
+                    result = await _call(server, name, arguments)
+                    response = "".join(block.text for block in result.content)
+                    observed = tap.since(mark, response)
+                    assert method in state["methods"], (name, state["methods"])
+                    assert observed.leaks(s) == [], (name, key, shape, family, observed)
+                    seen.append(observed)
+                # No vacuous pass: the payload was rejected, and said so.
+                assert re.search(
+                    r"validation errors? for McpTaskInfo: \$", seen[0].response
+                ), (
+                    name,
+                    key,
+                    seen[0].response,
+                )
+                assert not seen[0].differs(seen[1]), (
+                    name,
+                    key,
+                    shape,
+                    family,
+                    seen[0].differs(seen[1]),
+                )
+                assert _event_shape(seen[0].events) == _event_shape(seen[1].events)
+                rejected += 1
+    assert rejected == expected > len(_task_calls()) * len(positions), (
+        rejected,
+        expected,
+    )
+    # What `gateway.health` exposes (the audit-event buffer, among the rest).
+    health = "".join(
+        block.text for block in (await _call(server, "gateway.health", {})).content
+    )
+    await server.shutdown()
+    assert "audit_events" in health
+    for sentinels in _FAMILIES.values():
+        for s in sentinels:
+            assert not any(form in health for form in _forbidden(s))
+
+
+def _listing_parsers() -> list[tuple[str, Any, dict[str, Any], str | None]]:
+    """(label, real parser, a valid entry, the nested list its keys may also
+    sit in) for every downstream listing pmcp parses into its own models."""
+    from pmcp.client import manager
+
+    return [
+        (
+            "tools",
+            lambda entries: manager._parse_tool_entries("svc", entries, 100),
+            {"name": "t", "inputSchema": {"type": "object"}},
+            None,
+        ),
+        (
+            "resources",
+            lambda entries: manager._parse_resource_entries("svc", entries),
+            {"uri": "x://r"},
+            None,
+        ),
+        (
+            "prompts",
+            lambda entries: manager._parse_prompt_entries("svc", entries),
+            {"name": "p", "arguments": [{"name": "a"}]},
+            "arguments",
+        ),
+    ]
+
+
+@pytest.mark.parametrize("label", ["tools", "resources", "prompts"])
+def test_no_downstream_listing_value_reaches_the_log(
+    label: str,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+) -> None:
+    """The listing parsers skip an entry pmcp's model rejects and log why."""
+    from pmcp.client import manager
+
+    caplog.set_level(logging.DEBUG)
+    function = {
+        "tools": manager._parse_tool_entries,
+        "resources": manager._parse_resource_entries,
+        "prompts": manager._parse_prompt_entries,
+    }[label]
+    _, parse, valid, nested = next(p for p in _listing_parsers() if p[0] == label)
+    keys = [key for key in _payload_keys(function) if key]
+    tap = _Tap(typing.cast(Any, MagicMockServer()), None, caplog, capfd, recwarn)
+
+    def rejection(entry: dict[str, Any]) -> BaseException | None:
+        """What the real parser raised for `entry` (its `except` hands it to
+        `describe_exception`, recorded here and rendered as its type only)."""
+        with mock.patch.object(
+            manager, "describe_exception", side_effect=lambda exc: type(exc).__name__
+        ) as seen:
+            parse([entry])
+        return seen.call_args.args[0] if seen.call_args else None
+
+    rejected = 0
+    for key in keys:
+        for into_nested in [False, True] if nested else [False]:
+            for shape in _bad_values(_SENTINELS[0]):
+
+                def build(s: str) -> dict[str, Any]:
+                    entry = copy.deepcopy(valid)
+                    target = entry[nested][0] if into_nested else entry
+                    target[key] = _bad_values(s)[shape]
+                    return entry
+
+                # This sweep's subject is a validation error's text. An entry
+                # whose identity is unusable is skipped earlier, by
+                # `_required_identity`, and logged with `_entry_label` -- the
+                # downstream entry itself, by design (see the plan's
+                # Non-goals); an accepted value is indexed by design.
+                if not all(
+                    isinstance(rejection(build(s)), ValidationError)
+                    for sentinels in _FAMILIES.values()
+                    for s in sentinels
+                ):
+                    continue
+                for family, sentinels in _FAMILIES.items():
+                    pair = []
+                    for s in sentinels:
+                        mark = tap.start()
+                        parse([build(s)])
+                        observed = tap.since(mark, "")
+                        assert observed.leaks(s) == [], (
+                            label,
+                            key,
+                            shape,
+                            family,
+                            observed,
+                        )
+                        assert "validation error" in observed.log, observed.log
+                        pair.append(observed)
+                    assert pair[0].stable() == pair[1].stable(), (
+                        label,
+                        key,
+                        shape,
+                        family,
+                    )
+                rejected += 1
+    # No vacuous pass: pmcp's own model rejected some of the generated fields.
+    assert rejected > 3, (label, rejected)
+
+
+class MagicMockServer:
+    """A stand-in with no gateway tools, for `_Tap` outside a server."""
+
+    _gateway_tools = None
+
+
+@pytest.mark.asyncio
+async def test_a_connect_failure_carrying_a_validation_error_is_described(
+    tmp_path: Path,
+    monkeypatch: pytest.MonkeyPatch,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+) -> None:
+    """Dynamic half of the rev 2 board seat's surviving regression (a log
+    line in `_connect_with_retry` rendering `last_error`): a connect that
+    fails with a validation error, through retries, `connect_server`'s
+    result, the log and `gateway.health`."""
+    from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
+
+    caplog.set_level(logging.DEBUG)
+    monkeypatch.setattr("pmcp.client.manager.RETRY_DELAYS", [0.0, 0.0, 0.0])
+    server, _ = _server(tmp_path, audited=False)
+    manager = server._client_manager
+    tap = _Tap(server, None, caplog, capfd, recwarn)
+    config = ResolvedServerConfig(
+        name="flaky", source="custom", config=LocalMcpServerConfig(command="flaky")
+    )
+    seen = []
+    for family, sentinels in _FAMILIES.items():
+        for s in sentinels:
+
+            async def connect(_config: Any, s: str = s) -> None:
+                McpTaskInfo.model_validate({"task_id": "t", "ttl": {"v": s}})
+
+            monkeypatch.setattr(manager, "_connect_server", connect)
+            mark = tap.start()
+            errors = await manager.connect_server(config)
+            health = "".join(
+                b.text for b in (await _call(server, "gateway.health", {})).content
+            )
+            observed = tap.since(mark, json.dumps(errors) + health)
+            assert observed.leaks(s) == [], (family, observed)
+            assert (
+                "validation error for McpTaskInfo: $.ttl: must be an integer"
+                in (errors[0])
+            ), errors
+            seen.append((json.dumps(errors), observed.log))
+    await server.shutdown()
+    assert all(item == seen[0] for item in seen[1:])
````

### Patch — `tests/test_downstream_frame_echo.py`

````diff
diff --git a/tests/test_downstream_frame_echo.py b/tests/test_downstream_frame_echo.py
new file mode 100644
index 0000000..2ba69cf
--- /dev/null
+++ b/tests/test_downstream_frame_echo.py
@@ -0,0 +1,1006 @@
+"""A downstream's malformed JSON-RPC reply never echoes into pmcp's output
+(Consiliency/pmcp#297, rev 3).
+
+The MCP SDK's client transports validate every frame a downstream sends. For
+one they reject, the streamable-HTTP transport synthesises a JSON-RPC
+`-32700` whose message is pydantic's text, rejected value included
+(`mcp/client/streamable_http.py:188,407`), and every SDK transport logs the
+failure with a traceback. The rev 2 board seat showed that value reaching the
+`gateway.invoke` response, `gateway.health` and the log.
+
+This sweep runs a **real** downstream on every transport pmcp speaks -- the
+streamable-HTTP transport answering with JSON or with an event stream, the
+legacy SSE transport, and stdio -- and connects it through the real
+`ClientManager`. The downstream answers every request normally except one,
+which it answers with a malformed frame carrying a sentinel. That covers
+every request pmcp sends (`initialize`, the three listings, `tools/call`,
+`resources/read`, `prompts/get`, `tasks/*`) and every envelope member the
+SDK validates, made wrong: `result` not an object, `error.code` not an
+integer, `error.message` not a string, `jsonrpc` not `"2.0"`, and a body
+that is not JSON at all.
+
+The value must not reach the response (unless pmcp accepted the frame and
+returns its result as the product, which only stdio does, because pmcp parses
+stdio frames without a model), the log (raw records, tracebacks,
+`extra=`), `gateway.health`, stdout/stderr or warnings. One exact exemption
+applies: stdio's `Non-JSON output` DEBUG line, which logs the downstream's
+own non-protocol output by design (see the plan's *Non-goals*).
+"""
+
+from __future__ import annotations
+
+import asyncio
+import json
+import logging
+import queue
+import re
+import sys
+import textwrap
+import threading
+import typing
+import unicodedata
+from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
+from pathlib import Path
+from typing import Any
+
+import pytest
+from mcp.types import (
+    GetPromptRequestParams,
+    ReadResourceRequestParams,
+)
+
+from pmcp.types import (
+    LocalMcpServerConfig,
+    RemoteMcpServerConfig,
+    ResolvedServerConfig,
+)
+from tests.test_argument_error_echo import (
+    _FAMILIES,
+    _call,
+    _forbidden,
+    _record_stable,
+    _record_text,
+    _server,
+    _Tap,
+)
+from tests.test_scoped_advisor_audit import _make_ctx
+
+_SHAPES = (
+    "result-type",
+    "error-code",
+    "error-message",
+    "jsonrpc",
+    "not-json",
+    # rev 8 (round-7 codex F028-F034): JSON-RPC-shaped frames a parser
+    # rejects -- on a limit (nesting depth, an integer's digit count) or on
+    # syntax. Each fails BEFORE the sentinel, so the failure's position does
+    # not depend on the sentinel's length (the pair differential holds).
+    "json-deep",
+    "json-bigint",
+    "json-syntax",
+)
+#: The shapes whose frame the parser rejects: stdio then also sends the real
+#: reply, so the request completes instead of timing out.
+_REJECTED_SHAPES = ("not-json", "json-deep", "json-bigint", "json-syntax")
+_METHODS = (
+    "initialize",
+    "tools/list",
+    "resources/list",
+    "prompts/list",
+    "tools/call",
+    "resources/read",
+    "prompts/get",
+    "tasks/list",
+    "tasks/get",
+    "tasks/result",
+    "tasks/cancel",
+)
+_TRANSPORTS = ("http-json", "http-sse", "legacy-sse", "stdio")
+#: Three sentinel families: the shape-conditional axis is the argument
+#: sweep's; here the transports and envelope positions are.
+_FRAME_FAMILIES = ("hex", "alpha", "unicode")
+
+#: The downstream's logic, shared by the HTTP handler and the stdio script:
+#: answer `request` normally, unless it is the targeted method.
+_DOWNSTREAM_LOGIC = textwrap.dedent(
+    '''
+    import json
+
+    def normal(method, params):
+        if method == "initialize":
+            return {
+                "protocolVersion": params.get("protocolVersion", "2025-11-25"),
+                "capabilities": {"tools": {}, "resources": {}, "prompts": {}, "tasks": {}},
+                "serverInfo": {"name": "frames", "version": "1"},
+            }
+        if method == "tools/list":
+            return {"tools": [{"name": "run", "inputSchema": {"type": "object"},
+                               "execution": {"taskSupport": "optional"}}]}
+        if method == "resources/list":
+            return {"resources": [{"uri": "x://r", "name": "r"}]}
+        if method == "prompts/list":
+            return {"prompts": [{"name": "p"}]}
+        if method == "tools/call":
+            if params.get("task"):
+                return {"task": {"taskId": "t", "status": "working"}}
+            return {"content": [{"type": "text", "text": "ok"}]}
+        if method == "resources/read":
+            return {"contents": [{"uri": "x://r", "text": "ok"}]}
+        if method == "prompts/get":
+            return {"messages": []}
+        if method == "tasks/list":
+            return {"tasks": [{"taskId": "t", "status": "working"}]}
+        if method in ("tasks/get", "tasks/cancel"):
+            return {"task": {"taskId": "t", "status": "working"}}
+        if method == "tasks/result":
+            return {"task": {"taskId": "t", "status": "completed"}, "result": {"content": []}}
+        return {}
+
+    def reply(request, state):
+        """The raw reply body (bytes) for `request`, or None for a notification."""
+        method, rid = request.get("method"), request.get("id")
+        if rid is None:
+            return None
+        s = state["s"]
+        if method != state["method"]:
+            frame = {"jsonrpc": "2.0", "id": rid, "result": normal(method, request.get("params") or {})}
+        elif state["shape"] == "result-type":
+            frame = {"jsonrpc": "2.0", "id": rid, "result": s}
+        elif state["shape"] == "error-code":
+            frame = {"jsonrpc": "2.0", "id": rid, "error": {"code": s, "message": "m"}}
+        elif state["shape"] == "error-message":
+            frame = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": {s: s}}}
+        elif state["shape"] == "jsonrpc":
+            frame = {"jsonrpc": s, "id": rid, "result": {}}
+        elif state["shape"] == "json-deep":
+            head = '{"jsonrpc":"2.0","id":%s,"error":{"message":"m","data":' % json.dumps(rid)
+            return (head + "[" * 1500 + "0" + "]" * 1500 + ',"code":%s}}' % json.dumps(s)).encode()
+        elif state["shape"] == "json-bigint":
+            head = '{"jsonrpc":"2.0","id":%s,"error":{"message":"m","data":' % json.dumps(rid)
+            return (head + "7" * 5000 + ',"code":%s}}' % json.dumps(s)).encode()
+        elif state["shape"] == "json-syntax":
+            head = '{"jsonrpc":"2.0","id":%s,"error":{"message":"m",,' % json.dumps(rid)
+            return (head + '"code":%s}}' % json.dumps(s)).encode()
+        else:
+            return (s + " is not JSON {").encode()
+        return json.dumps(frame).encode()
+    '''
+)
+_logic: dict[str, Any] = {}
+exec(_DOWNSTREAM_LOGIC, _logic)  # noqa: S102 -- the test's own downstream logic
+
+
+class _Downstream:
+    """A streamable-HTTP (`/mcp`) and legacy-SSE (`/sse`, `/messages`)
+    downstream in one local server; `state` picks the malformed reply."""
+
+    def __init__(self) -> None:
+        self.state: dict[str, Any] = {
+            "method": None,
+            "shape": None,
+            "s": "",
+            "sse": False,
+        }
+        self.streams: list[queue.Queue[bytes | None]] = []
+        self.seen: set[str | None] = set()
+        downstream = self
+
+        class Handler(BaseHTTPRequestHandler):
+            protocol_version = "HTTP/1.1"
+
+            def log_message(self, *args: Any) -> None:
+                pass
+
+            def _body(self) -> dict[str, Any]:
+                return json.loads(self.rfile.read(int(self.headers["content-length"])))
+
+            def do_POST(self) -> None:  # noqa: N802
+                request = self._body()
+                downstream.seen.add(request.get("method"))
+                data = _logic["reply"](request, downstream.state)
+                if self.path.startswith("/messages"):
+                    if data is not None and downstream.streams:
+                        downstream.streams[-1].put(data)
+                        if request.get("method") == downstream.state["method"]:
+                            # The read loop drops a frame that fails JSON-RPC
+                            # validation and keeps reading (Consiliency/pmcp#287),
+                            # as stdio does: follow it with the real reply, or
+                            # the request only times out.
+                            downstream.streams[-1].put(
+                                _logic["reply"](
+                                    request, {**downstream.state, "method": None}
+                                )
+                            )
+                    self.send_response(202)
+                    self.send_header("content-length", "0")
+                    self.end_headers()
+                    return
+                if data is None:
+                    self.send_response(202)
+                    self.send_header("content-length", "0")
+                    self.end_headers()
+                    return
+                if downstream.state["sse"]:
+                    data = b"event: message\ndata: " + data + b"\n\n"
+                    kind = "text/event-stream"
+                else:
+                    kind = "application/json"
+                self.send_response(200)
+                self.send_header("content-type", kind)
+                self.send_header("content-length", str(len(data)))
+                self.end_headers()
+                self.wfile.write(data)
+
+            def do_GET(self) -> None:  # noqa: N802
+                if not self.path.startswith("/sse"):
+                    self.send_response(405)
+                    self.send_header("content-length", "0")
+                    self.end_headers()
+                    return
+                stream: queue.Queue[bytes | None] = queue.Queue()
+                downstream.streams.append(stream)
+                self.send_response(200)
+                self.send_header("content-type", "text/event-stream")
+                self.send_header("cache-control", "no-cache")
+                self.end_headers()
+                self.wfile.write(b"event: endpoint\ndata: /messages?session=1\n\n")
+                self.wfile.flush()
+                while (data := stream.get()) is not None:
+                    try:
+                        self.wfile.write(b"event: message\ndata: " + data + b"\n\n")
+                        self.wfile.flush()
+                    except OSError:
+                        return
+
+            def do_DELETE(self) -> None:  # noqa: N802
+                self.send_response(200)
+                self.send_header("content-length", "0")
+                self.end_headers()
+
+        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
+        self.httpd.daemon_threads = True
+        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
+        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
+
+    def close(self) -> None:
+        for stream in self.streams:
+            stream.put(None)
+        self.httpd.shutdown()
+        self.httpd.server_close()
+
+
+_STDIO_SCRIPT = _DOWNSTREAM_LOGIC + textwrap.dedent(
+    """
+    import sys
+    REJECTED = @@REJECTED@@
+    state_path = sys.argv[1]
+    for line in sys.stdin:
+        with open(state_path) as handle:
+            state = json.load(handle)
+        request = json.loads(line)
+        with open(state_path + ".seen", "a") as seen:
+            seen.write(str(request.get("method")) + "\\n")
+        data = reply(request, state)
+        if data is None:
+            continue
+        if request.get("method") == state["method"] and state["shape"] in REJECTED:
+            # stdio frames are lines: the rejected line, then the reply the
+            # request is waiting for (else it would only time out).
+            sys.stdout.buffer.write(data + b"\\n")
+            data = reply(request, {**state, "method": None})
+        sys.stdout.buffer.write(data + b"\\n")
+        sys.stdout.flush()
+    """
+).replace("@@REJECTED@@", repr(_REJECTED_SHAPES))
+
+
+def _config(transport: str, downstream: _Downstream, tmp: Path) -> ResolvedServerConfig:
+    if transport == "stdio":
+        script = tmp / "frames_downstream.py"
+        script.write_text(_STDIO_SCRIPT)
+        config: Any = LocalMcpServerConfig(
+            command=sys.executable, args=[str(script), str(tmp / "state.json")]
+        )
+    elif transport == "legacy-sse":
+        config = RemoteMcpServerConfig(type="sse", url=f"{downstream.base}/sse")
+    else:
+        config = RemoteMcpServerConfig(type="http", url=f"{downstream.base}/mcp")
+    return ResolvedServerConfig(name="frames", source="custom", config=config)
+
+
+async def _request(server: Any, method: str) -> str:
+    """Drive `method` through pmcp's own surface; return what the caller sees."""
+    if method == "tools/call":
+        result = await _call(
+            server,
+            "gateway.invoke",
+            {"tool_id": "frames::run", "options": {"timeout_ms": 3000}},
+        )
+    elif method.startswith("tasks/"):
+        target = {"server_name": "frames", "task_id": "t"}
+        if method == "tasks/cancel":
+            from pmcp.types import McpTaskInfo
+
+            server._client_manager._record_task(
+                "frames", McpTaskInfo(task_id="t", status="working")
+            )
+        name = {
+            "tasks/list": "gateway.tasks_list",
+            "tasks/get": "gateway.tasks_get",
+            "tasks/result": "gateway.tasks_result",
+            "tasks/cancel": "gateway.tasks_cancel",
+        }[method]
+        args = {"server_name": "frames"} if method == "tasks/list" else target
+        result = await _call(server, name, args)
+    elif method == "resources/read":
+        entry = server._server.get_request_handler("resources/read")
+        try:
+            result = await entry.handler(
+                _make_ctx(), ReadResourceRequestParams(uri="x://r")
+            )
+        except Exception as error:  # noqa: BLE001 -- the SDK sends `str(error)`
+            return f"raised {type(error).__name__}: {error}"
+    elif method == "prompts/get":
+        entry = server._server.get_request_handler("prompts/get")
+        try:
+            result = await entry.handler(
+                _make_ctx(), GetPromptRequestParams(name="frames::p")
+            )
+        except Exception as error:  # noqa: BLE001 -- the SDK sends `str(error)`
+            return f"raised {type(error).__name__}: {error}"
+    else:
+        return ""  # a connect-time method: the connect result is the output
+    return result.model_dump_json()
+
+
+def _accepted(response: str) -> bool:
+    """A stdio frame pmcp accepted: its result is the product, by design."""
+    try:
+        payload = json.loads(response)
+    except ValueError:
+        return False
+    content = payload.get("content") or []
+    text = "".join(
+        block.get("text", "") for block in content if isinstance(block, dict)
+    )
+    try:
+        inner = json.loads(text)
+    except ValueError:
+        return False
+    return isinstance(inner, dict) and (inner.get("ok") is True or "task" in inner)
+
+
+#: The one timing-dependent line a teardown logs: whether a transport closed
+#: inside its 5 s grace depends on the host, not on the frame.
+_CLOSE_TIMEOUT = re.compile(
+    r"\[frames\] remote transport did not close within [0-9.]+s; cancelling"
+)
+
+
+_NON_JSON_PREFIX = "[frames] Non-JSON output: "
+
+
+#: The test's own statement of the JSON grammar (RFC 8259 section 3), not
+#: the reader's: a value begins with one of these -- object, array, string,
+#: number, `true`/`false`/`null` -- plus Python's `NaN`/`Infinity`.
+_GRAMMAR_VALUE_START = '{["-0123456789tfnNI'
+#: What opens a container or a string, where frame content can begin.
+_GRAMMAR_OPENERS = '{["'
+
+
+def _first_significant(line: str) -> str:
+    """The first character a reader would see, written independently of the
+    reader: skip whatever `str.isspace` calls space (all `.strip` removes),
+    control and format characters (NUL, ESC, a BOM, ZWSP) and U+FFFD (an
+    undecodable byte)."""
+    for char in line:
+        if not (
+            char.isspace()
+            or char == "\ufffd"
+            or unicodedata.category(char) in ("Cc", "Cf")
+        ):
+            return char
+    return ""
+
+
+def _json_shaped(line: str) -> bool:
+    """Rev 9's rule, from the grammar: a line whose first significant
+    character can begin a JSON value is protocol data, never shown."""
+    return _first_significant(line) in tuple(_GRAMMAR_VALUE_START)
+
+
+def _carries_no_frame(shown: str) -> bool:
+    """A shown text can be exempt only if it holds no character that could
+    open frame content -- whatever the reader decided."""
+    return not any(opener in shown for opener in _GRAMMAR_OPENERS)
+    return False
+
+
+def _non_json_line(record: logging.LogRecord) -> bool:
+    """Exactly stdio's DEBUG line for a downstream's non-JSON output -- and
+    only when what it shows holds no frame-opening character (rev 9): a
+    record that could carry frame content is never exempt."""
+    message = record.getMessage()
+    return (
+        record.name == "pmcp.client.manager"
+        and record.levelno == logging.DEBUG
+        and message.startswith(_NON_JSON_PREFIX)
+        and _carries_no_frame(message[len(_NON_JSON_PREFIX) :])
+        and not record.exc_info
+    )
+
+
+@pytest.fixture(autouse=True)
+def _only_pytests_log_handlers() -> Any:
+    """Detach root handlers other tests left behind (a CLI test's file
+    handler whose directory is gone, say). A handler that fails while the
+    SDK's `except` block is active makes `logging.Handler.handleError` print
+    that exception's chain to stderr -- the rejected frame included -- past
+    every filter; see the plan's *Unverified*. pytest's own handlers stay."""
+    root = logging.getLogger()
+    foreign = [h for h in root.handlers if not type(h).__module__.startswith("_pytest")]
+    for handler in foreign:
+        root.removeHandler(handler)
+    yield
+    for handler in foreign:
+        root.addHandler(handler)
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("method", _METHODS)
+@pytest.mark.parametrize("transport", _TRANSPORTS)
+async def test_no_malformed_frame_value_reaches_pmcps_output(
+    monkeypatch: pytest.MonkeyPatch,
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    transport: str,
+    method: str,
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    # A failed connect is retried with back-off; the retries are the same
+    # frames again, and waiting them out adds nothing.
+    monkeypatch.setattr("pmcp.client.manager.RETRY_DELAYS", [0.0, 0.0, 0.0])
+    downstream = _Downstream()
+    downstream.state["sse"] = transport == "http-sse"
+    server, _ = _server(tmp_path, audited=False)
+    policy = server._policy_manager
+    policy.is_server_allowed = lambda name: True  # type: ignore[method-assign]
+    policy.is_tool_allowed = lambda tool_id: True  # type: ignore[method-assign]
+    policy.is_resource_allowed = lambda resource_id: True  # type: ignore[method-assign]
+    policy.is_prompt_allowed = lambda prompt_id: True  # type: ignore[method-assign]
+    tap = _Tap(server, None, caplog, capfd, recwarn)
+    manager = server._client_manager
+    config = _config(transport, downstream, tmp_path)
+    cases = 0
+    try:
+        for _ in (method,):
+            for shape in _SHAPES:
+                for family in _FRAME_FAMILIES:
+                    seen: list[tuple[str, ...]] = []
+                    for s in _FAMILIES[family]:
+                        state = {
+                            **downstream.state,
+                            "method": method,
+                            "shape": shape,
+                            "s": s,
+                        }
+                        downstream.state.update(state)
+                        (tmp_path / "state.json").write_text(json.dumps(state))
+                        # A known task makes a forced disconnect send `tasks/cancel`,
+                        # which may be the malformed method: forget it first.
+                        manager._tasks.clear()
+                        await manager.disconnect_server("frames", force=True)
+                        # Same request ids for both sentinels of a pair.
+                        manager._request_counters.pop("frames", None)
+                        mark = tap.start()
+                        errors = await manager.connect_server(config)
+                        answer = await asyncio.wait_for(_request(server, method), 20)
+                        accepted = _accepted(answer)
+                        response = json.dumps(errors) + answer
+                        health = "".join(
+                            b.text
+                            for b in (await _call(server, "gateway.health", {})).content
+                        )
+                        observed = tap.since(mark, response + health)
+                        # The pair differential: the caller-facing text, and
+                        # pmcp's own log records. The SDK's and httpx's
+                        # transport chatter varies with task timing (how many
+                        # messages were sent before a teardown); it is still
+                        # in the leak check above.
+                        pair_view = (
+                            # An accepted answer is the downstream's data, with
+                            # its timestamps: the pair compares rejections.
+                            json.dumps(errors) + ("<accepted>" if accepted else answer),
+                            # As a multiset: the transports' reader tasks log
+                            # concurrently, so the order is not the pair's to
+                            # keep.
+                            "\n".join(
+                                sorted(
+                                    _record_stable(r)
+                                    for r in caplog.records[mark[0] :]
+                                    if r.name.split(".")[0] == "pmcp"
+                                    and not _non_json_line(r)
+                                    and not _CLOSE_TIMEOUT.fullmatch(r.getMessage())
+                                )
+                            ),
+                            observed.streams,
+                            observed.warnings,
+                        )
+                        records = caplog.records[mark[0] :]
+                        if transport == "stdio":
+                            observed = observed._replace(
+                                raw_log="\n".join(
+                                    _record_text(r)
+                                    for r in records
+                                    if not _non_json_line(r)
+                                ),
+                                log="\n".join(
+                                    _record_stable(r)
+                                    for r in records
+                                    if not _non_json_line(r)
+                                ),
+                            )
+                        leaks = observed.leaks(s)
+                        if transport == "stdio" and leaks == ["response"] and accepted:
+                            leaks = []  # accepted as the product, by design
+                        assert leaks == [], (transport, method, shape, family, observed)
+                        if transport == "stdio" and shape.startswith("json-"):
+                            # No vacuous pass: the rejected frame reached the
+                            # reader and was described, not exempted.
+                            assert any(
+                                "sent a JSON-RPC frame that could not be parsed"
+                                in r.getMessage()
+                                for r in records
+                            ), (transport, method, shape, family)
+                        seen.append(pair_view)
+                    assert seen[0] == seen[1], (transport, method, shape, family, seen)
+                    cases += 1
+        assert cases == len(_SHAPES) * len(_FRAME_FAMILIES)
+        # No vacuous pass: the downstream did answer the targeted method.
+        seen_methods = set(downstream.seen)
+        if transport == "stdio":
+            seen_methods |= set((tmp_path / "state.json.seen").read_text().splitlines())
+        assert method in seen_methods, seen_methods
+    finally:
+        manager._tasks.clear()
+        await manager.disconnect_server("frames", force=True)
+        await server.shutdown()
+        downstream.close()
+
+
+# --- the SDK's own ClientSession (rev 4, rev 3 board B1) ----------------------
+#
+# `refresh_server` (the gateway's startup description refresh) talks to a
+# downstream through the SDK's `stdio_client` + `ClientSession`, whose logger
+# is named "client" -- outside `mcp.*` -- and which logs a notification it
+# rejects with `exc_info=True`. The downstream here sends one malformed
+# message of each kind before it answers `tools/list`.
+
+_SESSION_MESSAGES = {
+    "notifications/message": lambda s: {"level": s, "data": "x"},
+    "notifications/progress": lambda s: {"progressToken": {s: s}, "progress": 1},
+    "notifications/resources/updated": lambda s: {"uri": [s]},
+    "notifications/tools/list_changed": lambda s: s,
+    "notifications/cancelled": lambda s: {"requestId": {s: s}},
+    "request:roots/list": lambda s: s,
+    "request:sampling/createMessage": lambda s: {s: s},
+    "request:elicitation/create": lambda s: {"message": {s: s}},
+    "request:ping": lambda s: [s],
+}
+
+#: The kinds whose envelope is well formed, so `ClientSession` itself (on the
+#: "client" logger) is what rejects them.
+_SESSION_LEVEL = {
+    "notifications/message",
+    "notifications/progress",
+    "notifications/resources/updated",
+    "notifications/cancelled",
+}
+
+_SESSION_SCRIPT = textwrap.dedent(
+    """
+    import json, sys
+    with open(sys.argv[1]) as handle:  # the case, not on the command line:
+        kind, params = json.load(handle)  # pmcp records the command line
+    for line in sys.stdin:
+        with open(sys.argv[1] + ".in", "a") as seen:
+            seen.write(line)
+        request = json.loads(line)
+        rid, method = request.get("id"), request.get("method")
+        if rid is None or method is None:
+            continue
+        if method == "initialize":
+            result = {"protocolVersion": request["params"]["protocolVersion"],
+                      "capabilities": {"tools": {}}, "serverInfo": {"name": "d", "version": "1"}}
+        else:
+            if method == "tools/list":
+                if kind.startswith("request:"):
+                    frame = {"jsonrpc": "2.0", "id": 900, "method": kind[8:], "params": params}
+                else:
+                    frame = {"jsonrpc": "2.0", "method": kind, "params": params}
+                sys.stdout.write(json.dumps(frame) + "\\n")
+            result = {"tools": [{"name": "run", "inputSchema": {"type": "object"}}]} if method == "tools/list" else {}
+        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\\n")
+        sys.stdout.flush()
+    """
+)
+
+
+@pytest.mark.asyncio
+@pytest.mark.parametrize("kind", sorted(_SESSION_MESSAGES))
+async def test_no_malformed_session_message_value_reaches_the_log(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    recwarn: pytest.WarningsRecorder,
+    kind: str,
+) -> None:
+    from pmcp.manifest.loader import ServerConfig
+    from pmcp.manifest.refresher import refresh_server
+
+    caplog.set_level(logging.DEBUG)
+    script = tmp_path / "session_downstream.py"
+    script.write_text(_SESSION_SCRIPT)
+    tap = _Tap(typing.cast(Any, _NoServer()), None, caplog, capfd, recwarn)
+    for family in _FRAME_FAMILIES:
+        seen = []
+        for s in _FAMILIES[family]:
+            config = ServerConfig(
+                name="d",
+                description="d",
+                keywords=[],
+                install={},
+                command=sys.executable,
+                args=[str(script), str(tmp_path / "case.json")],
+            )
+            (tmp_path / "case.json").write_text(
+                json.dumps([kind, _SESSION_MESSAGES[kind](s)])
+            )
+            mark = tap.start()
+            result = await asyncio.wait_for(refresh_server(config, force=True), 30)
+            observed = tap.since(mark, repr(result))
+            assert observed.leaks(s) == [], (kind, family, observed)
+            seen.append(
+                "\\n".join(
+                    sorted(
+                        _record_stable(r)
+                        for r in caplog.records[mark[0] :]
+                        if r.name == "client" or r.name.split(".")[0] == "pmcp"
+                    )
+                )
+            )
+        assert seen[0] == seen[1], (kind, family, seen)
+    # No vacuous pass: the SDK rejected the message -- in its envelope
+    # (`mcp.client.stdio`) or, for a well-formed envelope, in `ClientSession`
+    # (the "client" logger) -- and the scrubbed record says so.
+    rejected = {
+        r.name
+        for r in caplog.records
+        if r.name.split(".")[0] in ("client", "mcp")
+        and "validation error" in r.getMessage()
+    }
+    replies = [
+        json.loads(line)
+        for line in (tmp_path / "case.json.in").read_text().splitlines()
+    ]
+    answered = any(r.get("id") == 900 and "error" in r for r in replies)
+    assert rejected or answered, kind
+    if kind in _SESSION_LEVEL:
+        assert "client" in rejected, (kind, rejected)
+
+
+class _NoServer:
+    """`_Tap` outside a server: no gateway tools, so no audit-event buffer."""
+
+    _gateway_tools = None
+
+
+# --- rev 8: every JSON-RPC-shaped frame the stdio parser rejects ---------------
+
+
+def _rejected_frames(s: str) -> list[tuple[str, bytes]]:
+    """JSON-RPC-shaped stdio lines the real parser rejects, derived from the
+    grammar rather than from findings (round-7 codex F028-F034 are two of
+    them): every truncation point and every structural-character deletion of
+    a frame carrying the sentinel, a doubled separator at every structural
+    position, and both parser limits (nesting depth, an integer's digits),
+    as an object and as a batch, bare and after leading whitespace."""
+    frame = json.dumps(
+        {"jsonrpc": "2.0", "id": 7, "result": {"k": s, "list": [1, s], "o": {"s": s}}}
+    )
+    variants: list[tuple[str, str]] = []
+    for cut in range(1, len(frame)):
+        variants.append((f"truncated@{cut}", frame[:cut]))
+    for i, char in enumerate(frame):
+        if char in '{}[]:,"':
+            variants.append((f"deleted {char}@{i}", frame[:i] + frame[i + 1 :]))
+        if char in ":,":
+            variants.append((f"doubled {char}@{i}", frame[:i] + char + frame[i:]))
+    variants.append(
+        ("depth", frame[:-1] + ',"d":' + "[" * 1500 + "0" + "]" * 1500 + "}")
+    )
+    variants.append(("digits", frame[:-1] + ',"n":' + "7" * 5000 + "}"))
+    # Codex's shapes without an object around them, and PR 321's bare array:
+    variants.append(("bare-depth", "[" * 1500 + json.dumps(s) + "]" * 1500))
+    variants.append(("bare-digits", "7" * 5000 + " " + json.dumps(s)))
+    variants.append(("scalar-then-data", "42 " + json.dumps(s)))
+    out: list[tuple[str, bytes]] = []
+    for label, text in variants:
+        for prefix, suffix, shape in (
+            ("", "", "object"),
+            ("  ", "", "indented"),
+            ("[", "]", "batch"),
+            ("\ufeff", "", "bom"),
+        ):
+            line = prefix + text + suffix
+            try:
+                json.loads(line)
+            except Exception:  # noqa: BLE001 -- any rejection counts
+                out.append((f"{shape} {label}", line.encode()))
+    return out
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_no_rejected_json_rpc_frame_reaches_the_log(
+    caplog: pytest.LogCaptureFixture, family: str
+) -> None:
+    """A JSON-RPC-shaped stdio line that the parser rejects -- on syntax or a
+    limit -- is described, never logged raw; no record is exempted. A line
+    that is not JSON-RPC-shaped is still the downstream's own output, logged
+    as it is (the positive control for the boundary)."""
+    import time
+    from unittest.mock import MagicMock
+
+    from pmcp.client.manager import ClientManager, ManagedClient
+    from pmcp.types import ServerStatus, ServerStatusEnum
+
+    caplog.set_level(logging.DEBUG)
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden(s)
+    manager = ClientManager()
+    managed = ManagedClient(
+        config=MagicMock(),
+        process=MagicMock(),
+        status=ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0),
+    )
+    frames = _rejected_frames(s)
+    # No vacuous pass: the generator covers each class, and both limits.
+    labels = " ".join(label for label, _ in frames)
+    for kind in (
+        "truncated",
+        "deleted",
+        "doubled",
+        "depth",
+        "digits",
+        "batch",
+        "indented",
+        "bom",
+        "bare-depth",
+        "bare-digits",
+        "scalar-then-data",
+    ):
+        assert kind in labels, kind
+    described = 0
+    for label, line in frames:
+        start = len(caplog.records)
+        manager._handle_stdout_line("srv", managed, line, time.time())
+        records = caplog.records[start:]
+        text = "\n".join(_record_text(r) for r in records)
+        assert not any(form in text for form in forbidden), (label, text[:300])
+        described += any(
+            "sent a JSON-RPC frame that could not be parsed" in r.getMessage()
+            for r in records
+        )
+    # The frames carry the sentinel as JSON spells it (`\\u` escapes for the
+    # non-ASCII family), which is what a raw log line would show.
+    spelled = json.dumps(s)[1:-1].encode("ascii")
+    carrying = [label for label, line in frames if spelled in line]
+    assert described == len(frames) and len(carrying) > 100, (described, len(frames))
+    # The boundary, both ways. Output -- the parser rejects the first
+    # character, and it opens nothing -- is logged as it is, by design; a
+    # line that opens like JSON is described even if it is a log line.
+    start = len(caplog.records)
+    manager._handle_stdout_line(
+        "srv", managed, f"server ready {s}".encode(), time.time()
+    )
+    assert any(
+        r.getMessage() == f"[srv] Non-JSON output: server ready {s}"
+        for r in caplog.records[start:]
+    )
+    for described_line in (f"[INFO] {s}", f"true {s}"):
+        start = len(caplog.records)
+        manager._handle_stdout_line(
+            "srv", managed, described_line.encode(), time.time()
+        )
+        text = "\n".join(_record_text(r) for r in caplog.records[start:])
+        assert not any(form in text for form in forbidden), (described_line, text)
+
+
+# --- rev 9: where JSON starts, and what a shown line may hold -----------------
+
+#: Lead characters a line can start with before its first significant one:
+#: plain and Unicode whitespace (all of which `.strip` removes), control and
+#: format characters, and the byte-order mark.
+_LEADS = (
+    "",
+    " ",
+    "   ",
+    "\t",
+    "\r",
+    "\x0c",
+    "\x0b",
+    " ",
+    " ",
+    "　",
+    "​",
+    "﻿",
+    "\x00",
+    "\x1b",
+    " ﻿\t",
+)
+#: Prefixes a downstream might write before a whole frame on the same line
+#: (round-8 claude N1), each a different class of first character.
+_FRAME_PREFIXES = (
+    ",",
+    "}",
+    ":",
+    "+",
+    "ready",
+    "ready ",
+    "ready\r",
+    "server ready: ",
+    "\x1b[0m",
+    "[INFO] ",
+    "true ",
+    "42 ",
+)
+
+
+def _line_cases(s: str) -> list[tuple[str, bytes]]:
+    """Lines a stdio downstream could write, each carrying `s` only as JSON
+    content (a string value, after an opener, or where a value begins):
+    - every value-start character followed by the sentinel, and an
+      unterminated string (round-8 codex P1), behind every lead;
+    - a number or literal prefix followed by the sentinel;
+    - every prefix class followed by a whole frame, behind every lead;
+    - the whole frame in UTF-8 with a BOM, UTF-16 and UTF-32 (with and
+      without a BOM, both byte orders), a non-UTF-8 byte before it;
+    - CR-only separation and several frames on one line."""
+    spelled = json.dumps(s)[1:-1]
+    frame = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"k": s}})
+    out: list[tuple[str, bytes]] = []
+    for lead in _LEADS:
+        tag = repr(lead)
+        for char in _GRAMMAR_VALUE_START:
+            out.append(
+                (f"{tag} value-start {char!r}", (lead + char + spelled).encode())
+            )
+        out.append((f"{tag} unterminated string", (lead + '"' + spelled).encode()))
+        out.append((f"{tag} unterminated key", (lead + '{"' + spelled).encode()))
+        for literal in (
+            "42 ",
+            "-1 ",
+            "0.5",
+            "true ",
+            "false",
+            "null ",
+            "NaN ",
+            "Infinity ",
+        ):
+            out.append(
+                (f"{tag} {literal!r} then data", (lead + literal + spelled).encode())
+            )
+        for prefix in _FRAME_PREFIXES:
+            out.append((f"{tag} {prefix!r} + frame", (lead + prefix + frame).encode()))
+    out.append(("\\xff + frame", b"\xff" + frame.encode()))
+    out.append(("\\xfe\\xff + frame", b"\xfe\xff" + frame.encode()))
+    for codec in (
+        "utf-8-sig",
+        "utf-16",
+        "utf-16-le",
+        "utf-16-be",
+        "utf-32",
+        "utf-32-le",
+        "utf-32-be",
+    ):
+        out.append((f"frame in {codec}", frame.encode(codec)))
+    out.append(("CR-only", ("ready\r" + frame + "\r" + frame).encode()))
+    out.append(("two frames", (frame + frame).encode()))
+    out.append(("two frames, spaced", (frame + " " + frame).encode()))
+    out.append(("frame + trailing banner", (frame + " done").encode()))
+    return out
+
+
+def _stdio_manager() -> tuple[Any, Any]:
+    from unittest.mock import MagicMock
+
+    from pmcp.client.manager import ClientManager, ManagedClient
+    from pmcp.types import ServerStatus, ServerStatusEnum
+
+    return ClientManager(), ManagedClient(
+        config=MagicMock(),
+        process=MagicMock(),
+        status=ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0),
+    )
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_no_stdio_line_shows_frame_content(
+    caplog: pytest.LogCaptureFixture, family: str
+) -> None:
+    """Rev 9 (round-8 codex P1 and claude N1): no line a downstream writes
+    shows frame content in the log. The expectation is the grammar's, not
+    the reader's: a line whose first significant character can begin a
+    JSON value is described; any other line is shown only up to its first
+    frame-opening character. No record is exempt from the leak check."""
+    import time
+
+    caplog.set_level(logging.DEBUG)
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
+    manager, managed = _stdio_manager()
+    cases = _line_cases(s)
+    shaped = 0
+    for label, line in cases:
+        decoded = line.decode("utf-8", "replace")
+        start = len(caplog.records)
+        manager._handle_stdout_line("srv", managed, line, time.time())
+        records = caplog.records[start:]
+        text = "\n".join(_record_text(r) for r in records)
+        # No record exempt: the sentinel, and its NUL-interleaved UTF-16/32
+        # spelling, appear nowhere.
+        flat = text.replace("\x00", "")
+        assert not any(form in text or form in flat for form in forbidden), (
+            label,
+            text[:300],
+        )
+        shown = [
+            r.getMessage()[len("[srv] Non-JSON output: ") :]
+            for r in records
+            if r.getMessage().startswith("[srv] Non-JSON output: ")
+        ]
+        described = any(
+            "sent a JSON-RPC frame that could not be parsed" in r.getMessage()
+            for r in records
+        )
+        try:
+            json.loads(line)
+            parsed = True
+        except Exception:  # noqa: BLE001 -- any rejection
+            parsed = False
+        if parsed:
+            continue  # valid JSON: the dispatcher's business, not this test's
+        if _json_shaped(decoded):
+            shaped += 1
+            assert described and not shown, (label, [r.getMessage() for r in records])
+        else:
+            assert shown and not described, (label, [r.getMessage() for r in records])
+            assert all(_carries_no_frame(text) for text in shown), (label, shown)
+    # No vacuous pass: both sides of the boundary are exercised.
+    assert shaped > 100 and len(cases) - shaped > 20, (shaped, len(cases))
+
+
+def test_a_banner_stays_useful_on_stdio(caplog: pytest.LogCaptureFixture) -> None:
+    """The operator-facing controls: plain output is shown as it is, digits
+    included; a BOM or a control character before it is dropped; a banner
+    before a frame keeps its own text."""
+    import time
+
+    caplog.set_level(logging.DEBUG)
+    manager, managed = _stdio_manager()
+    frame = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"k": "frame-secret-9x"}})
+    for line, shown in (
+        ("server ready banner-ok-7f3a", "server ready banner-ok-7f3a"),
+        ("listening on port 8080", "listening on port 8080"),
+        ("﻿ready banner-ok-7f3a", "ready banner-ok-7f3a"),
+        ("\x00  ready", "ready"),
+        ("ready banner-ok-7f3a " + frame, "ready banner-ok-7f3a (rest omitted)"),
+        ("warning: value is 'quoted'", "warning: value is 'quoted'"),
+        ('warning: value is "quoted"', "warning: value is (rest omitted)"),
+    ):
+        start = len(caplog.records)
+        manager._handle_stdout_line("srv", managed, line.encode(), time.time())
+        messages = [r.getMessage() for r in caplog.records[start:]]
+        assert messages == [f"[srv] Non-JSON output: {shown}"], (line, messages)
````

### Patch — `tests/test_exception_text_sinks.py`

````diff
diff --git a/tests/test_exception_text_sinks.py b/tests/test_exception_text_sinks.py
new file mode 100644
index 0000000..984594a
--- /dev/null
+++ b/tests/test_exception_text_sinks.py
@@ -0,0 +1,771 @@
+"""Every place `src/pmcp` turns an exception into text goes through the
+value-free renderers (Consiliency/pmcp#297).
+
+A pydantic or jsonschema `ValidationError` renders the rejected value, so
+wherever pmcp renders an exception that may be one -- a response field, a
+log line or its traceback, an audit or audit-event field -- it must use
+`exception_text` / `safe_exc_info` (or a renderer built on them). This test
+enforces that statically, over every module in `src/pmcp`, the CLI included
+(rev 6: `pmcp refresh` and the config commands read files and downstreams).
+
+The scanner tracks exceptions through a function, not just an `except`
+body (rev 3; the rev 2 board seat found 25 constructs rev 2's scanner
+missed). Within each function (and the module body), an **exception name**
+is:
+
+- an `except` clause's name, if a type it catches -- resolved in the
+  module's own namespace -- is a base of pydantic's or jsonschema's
+  `ValidationError` (an unresolvable type counts as one);
+- a name assigned from `<x>.exception(...)` anywhere in the value
+  (`fut.exception()`, `t.exception() if ... else None`);
+- a name `isinstance`-tested anywhere in a condition against such a type;
+- a name bound from `gather(..., return_exceptions=True)`, and a loop
+  variable over any exception name or over `<x>.exceptions`;
+- an alias of any of these (`last = e`), wherever it is later used.
+
+A **use** of an exception name is safe only as: an argument to a renderer
+(by its bare name, imported from `pmcp` -- `self._sanitize_error` is the one
+method), `type()`/`isinstance()`, `raise`, a comparison or truthiness test,
+an alias to a plain name, a loop's iterable, or an attribute that carries no
+text. Anything else -- an f-string, `str()`/`repr()`, a `%` argument, a
+store into an attribute or container, a return, a text-bearing attribute
+(`args`, `errors()`, jsonschema's `path`/`json_path`, `__notes__`, ...), or
+an unknown callee -- is a sink. Also sinks, anywhere: `exc_info=` not
+through `safe_exc_info`, `logger.exception`, `logging.exception`,
+`makeRecord`/`handle`, `sys.exc_info()`/`sys.exception()`, and every
+`traceback` renderer.
+"""
+
+from __future__ import annotations
+
+import ast
+import builtins
+import importlib
+import typing
+from pathlib import Path
+from typing import Any
+
+import json
+
+import jsonschema
+import pydantic
+import yaml
+import pytest
+
+#: Exceptions whose own text can carry the input they rejected: validation
+#: errors, and (rev 6) the parse errors of every structured-text parser pmcp
+#: uses -- the same set `exception_text` renders structurally.
+_VALIDATION_ERRORS = (
+    pydantic.ValidationError,
+    jsonschema.ValidationError,
+    yaml.YAMLError,
+    json.JSONDecodeError,
+)
+
+#: Renderers, called by their bare name.
+_RENDERERS = {
+    "exception_text",
+    "safe_exc_info",
+    "safe_traceback_text",
+    "describe_exception",
+    "sanitize_auth_diagnostic",
+    "describe_argument_error",
+    "describe_schema_error",
+    "describe_model_error",
+    "model_error_path",
+    "schema_error_path",
+    "schema_error_keyword",
+}
+#: Where a renderer that is not in `pmcp.argument_errors` is defined; its own
+#: body is scanned like any other.
+_RENDERER_HOMES = {
+    "describe_exception": "client/manager.py",
+    "sanitize_auth_diagnostic": "auth.py",
+}
+#: The one renderer called as a method.
+_RENDERER_METHODS = {"_sanitize_error"}
+#: Callees that receive an exception and do not render its text, each read:
+_NON_RENDERING_CALLEES = {
+    # manager.py: a boolean predicate over the message.
+    "_is_protocol_version_initialize_error",
+    # manager.py: hands the exception to the awaiting connect caller, whose
+    # own `except` is checked here like any other.
+    "set_exception",
+    # policy.py: renders its `error` argument with `exception_text`.
+    "_warn_unparseable",
+    # scoped_advisor_audit.py (#296): records path and keyword only.
+    "record_rejected_arguments",
+    # parsing.py (rev 7): reads a MarkedYAMLError's mark line and column only.
+    "_yaml_position",
+    # manager.py: flattens a group into leaves; its callers render each leaf
+    # with `exception_text` (`describe_exception`).
+    "_iter_leaf_exceptions",
+    # auth.py: see `_NON_RENDERING_FUNCTIONS`.
+    "parse_url_elicitation_error",
+}
+#: Functions that read an exception's text to parse it and return no text.
+_NON_RENDERING_FUNCTIONS = {
+    # auth.py: finds a JSON-RPC -32042 payload in `args[0]` / `str()` and
+    # returns structured `UrlElicitationInfo` (URLs the server sent).
+    "parse_url_elicitation_error",
+    # manager.py: a predicate (above).
+    "_is_protocol_version_initialize_error",
+    # manager.py: yields leaves; never renders.
+    "_iter_leaf_exceptions",
+}
+#: Attributes that carry an exception's text or the rejected value.
+_TEXT_ATTRIBUTES = {
+    "args",
+    "message",
+    "errors",
+    "json",
+    "instance",
+    "validator_value",
+    "context",
+    "cause",
+    "schema",
+    "path",
+    "relative_path",
+    "absolute_path",
+    "json_path",
+    "schema_path",
+    "__notes__",
+    "doc",
+    "msg",
+    "exceptions",
+    "__cause__",
+    "__context__",
+    "__traceback__",
+    "__str__",
+    "__repr__",
+    "__dict__",
+    "add_note",
+}
+_TRACEBACK_RENDERERS = {
+    "format_exc",
+    "format_exception",
+    "format_exception_only",
+    "print_exc",
+    "print_exception",
+    "TracebackException",
+}
+
+
+def _sources() -> list[Path]:
+    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
+    return [
+        path
+        for path in sorted(root.rglob("*.py"))
+        if not any(part in ("baml_client",) for part in path.relative_to(root).parts)
+        and path.name != "argument_errors.py"
+    ]
+
+
+def _namespace_for(path: Path) -> dict[str, Any]:
+    root = Path(__file__).resolve().parents[1] / "src"
+    parts = path.relative_to(root).with_suffix("").parts
+    name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
+    return dict(vars(importlib.import_module(name)))
+
+
+def _snippet_namespace(tree: ast.Module) -> dict[str, Any]:
+    """Every import in the source -- function-local ones too -- executed,
+    over the builtins."""
+    namespace: dict[str, Any] = {}
+    imports = [
+        n
+        for n in ast.walk(tree)
+        if isinstance(n, ast.Import) or (isinstance(n, ast.ImportFrom) and n.level == 0)
+    ]
+    for statement in imports:
+        try:
+            exec(  # noqa: S102 -- the source's own import statements
+                compile(
+                    ast.Module(body=[statement], type_ignores=[]), "<imports>", "exec"
+                ),
+                namespace,
+            )
+        except ImportError:
+            continue  # a platform-only module (`msvcrt`); its names stay unresolved
+    return namespace
+
+
+def _catches_validation(node: Any, namespace: dict[str, Any]) -> bool:
+    """Whether an `except`/`isinstance` type expression can match a
+    validation error. Resolved in the module's namespace; unresolvable
+    counts as yes."""
+    if node is None:
+        return True
+    try:
+        # The expression is an `except`/`isinstance` type taken from pmcp's
+        # own source (or a test snippet above), evaluated in that module's
+        # namespace to resolve imports and aliases -- as #296's
+        # `_raised_exceptions` does. No external input reaches it.
+        value = eval(ast.unparse(node), {**vars(builtins), **namespace})  # noqa: S307
+    except Exception:
+        return True
+    pending, classes = [value], []
+    while pending:
+        item = pending.pop()
+        if isinstance(item, tuple):
+            pending.extend(item)
+        elif typing.get_args(item) and not isinstance(item, type):
+            pending.extend(typing.get_args(item))  # `int | float`
+        else:
+            classes.append(item)
+    for cls in classes:
+        if not isinstance(cls, type):
+            return True
+        if any(issubclass(error, cls) for error in _VALIDATION_ERRORS):
+            return True
+    return False
+
+
+def _callee(call: ast.Call) -> str | None:
+    func = call.func
+    if isinstance(func, ast.Name):
+        return func.id
+    if isinstance(func, ast.Attribute):
+        return func.attr
+    return None
+
+
+def _is_renderer_call(call: ast.Call, imported: set[str], label: str) -> bool:
+    func = call.func
+    if isinstance(func, ast.Name):
+        if func.id in ("type", "isinstance"):
+            return True
+        if func.id in _RENDERERS:
+            home = _RENDERER_HOMES.get(func.id)
+            return func.id in imported or (home is not None and label.endswith(home))
+        return func.id in _NON_RENDERING_CALLEES
+    if isinstance(func, ast.Attribute):
+        if (
+            func.attr in _RENDERER_METHODS
+            and isinstance(func.value, ast.Name)
+            and func.value.id == "self"
+        ):
+            return True
+        return func.attr in _NON_RENDERING_CALLEES
+    return False
+
+
+def _scopes(tree: ast.Module) -> list[list[ast.stmt]]:
+    """Each function's body, and the module's own statements."""
+    bodies: list[list[ast.stmt]] = [
+        [
+            s
+            for s in tree.body
+            if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
+        ]
+    ]
+    for node in ast.walk(tree):
+        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
+            bodies.append(node.body)
+    return bodies
+
+
+def _own_nodes(body: list[ast.AST]) -> list[ast.AST]:
+    """Nodes of `body`, including nested functions and lambdas -- a closure
+    sees the enclosing scope's names (rev 4: a `def` inside an `except`) --
+    but not nested classes. A nested function is also scanned as its own
+    scope; findings are de-duplicated."""
+    found: list[ast.AST] = []
+    pending: list[ast.AST] = list(body)
+    while pending:
+        node = pending.pop()
+        found.append(node)
+        for child in ast.iter_child_nodes(node):
+            if not isinstance(child, ast.ClassDef):
+                pending.append(child)
+    return found
+
+
+def _is_context_exception(node: ast.AST) -> bool:
+    """`context["exception"]` / `context.get("exception")`: an asyncio loop
+    exception handler's exception (rev 4)."""
+    if isinstance(node, ast.Subscript):
+        key = node.slice
+        return isinstance(key, ast.Constant) and key.value == "exception"
+    return (
+        isinstance(node, ast.Call)
+        and isinstance(node.func, ast.Attribute)
+        and node.func.attr == "get"
+        and bool(node.args)
+        and isinstance(node.args[0], ast.Constant)
+        and node.args[0].value == "exception"
+    )
+
+
+#: A name's region: the ids of the nodes where it holds an exception, or
+#: `None` for the whole scope.
+_Regions = dict[str, "set[int]"]
+
+
+def _widen(regions: _Regions, name: str, region: set[int]) -> bool:
+    before = regions.get(name, set())
+    after = before | region
+    if after == before and name in regions:
+        return False
+    regions[name] = after
+    return True
+
+
+def _ids(nodes: list[ast.AST]) -> set[int]:
+    return {id(node) for node in _own_nodes(nodes)}
+
+
+def _isinstance_names(
+    test: ast.AST, namespace: dict[str, Any], *, positive: bool
+) -> set[str]:
+    """Names `test` narrows to a possible validation error: `isinstance(x, T)`
+    alone or under `and` (positive), or `not isinstance(x, T)` alone or under
+    `or` (negative)."""
+    if positive and isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
+        return set().union(
+            *(_isinstance_names(v, namespace, positive=True) for v in test.values)
+        )
+    if not positive and isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
+        return set().union(
+            *(_isinstance_names(v, namespace, positive=False) for v in test.values)
+        )
+    if not positive:
+        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
+            return _isinstance_names(test.operand, namespace, positive=True)
+        return set()
+    if (
+        isinstance(test, ast.Call)
+        and _callee(test) == "isinstance"
+        and len(test.args) == 2
+        and isinstance(test.args[0], ast.Name)
+        and _catches_validation(test.args[1], namespace)
+    ):
+        return {test.args[0].id}
+    return set()
+
+
+def _exits(body: list[ast.stmt]) -> bool:
+    return bool(body) and isinstance(
+        body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break)
+    )
+
+
+def _loop_targets(node: ast.AST, names: _Regions) -> set[str]:
+    """Targets of a loop that receive an exception: position-mapped through
+    a literal sequence of tuples, `zip(...)` and `enumerate(...)`; every
+    target of an opaque iterable that is an exception name or `.exceptions`."""
+    iterable, target = node.iter, node.target  # type: ignore[attr-defined]
+
+    def mentions(expr: ast.AST) -> bool:
+        return any(
+            (isinstance(n, ast.Name) and n.id in names)
+            or (isinstance(n, ast.Attribute) and n.attr == "exceptions")
+            for n in ast.walk(expr)
+        )
+
+    columns: list[ast.AST] | None = None
+    if isinstance(iterable, ast.Call) and _callee(iterable) == "zip":
+        columns = list(iterable.args)
+    elif (
+        isinstance(iterable, ast.Call)
+        and _callee(iterable) == "enumerate"
+        and iterable.args
+    ):
+        columns = [ast.Constant(0), iterable.args[0]]
+    elif isinstance(iterable, (ast.Tuple, ast.List)) and all(
+        isinstance(e, ast.Tuple) for e in iterable.elts
+    ):
+        width = {len(e.elts) for e in iterable.elts}  # type: ignore[attr-defined]
+        if len(width) == 1:
+            n = width.pop()
+            columns = [
+                ast.Tuple([e.elts[i] for e in iterable.elts], ast.Load())
+                for i in range(n)
+            ]  # type: ignore[attr-defined]
+    if (
+        columns is not None
+        and isinstance(target, ast.Tuple)
+        and len(target.elts) == len(columns)
+    ):
+        return {
+            t.id
+            for t, column in zip(target.elts, columns)
+            if isinstance(t, ast.Name) and mentions(column)
+        }
+    if mentions(iterable):
+        return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
+    return set()
+
+
+def _exception_regions(body: list[ast.stmt], namespace: dict[str, Any]) -> _Regions:
+    regions: _Regions = {}
+    nodes = _own_nodes(body)
+    everywhere = {id(node) for node in nodes}
+    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
+
+    def block_of(node: ast.AST) -> list[ast.AST] | None:
+        if node in body:
+            return list(body)
+        parent = parents.get(node)
+        for field in ("body", "orelse", "finalbody", "handlers"):
+            statements = getattr(parent, field, None)
+            if isinstance(statements, list) and node in statements:
+                return statements
+        return None
+
+    def after(node: ast.AST) -> set[int]:
+        block = block_of(node)
+        return _ids(block[block.index(node) + 1 :]) if block is not None else set()
+
+    for node in nodes:
+        if isinstance(node, ast.ExceptHandler) and node.name:
+            if _catches_validation(node.type, namespace):
+                _widen(regions, node.name, _ids(node.body))
+        elif isinstance(node, (ast.If, ast.While)):
+            for name in _isinstance_names(node.test, namespace, positive=True):
+                _widen(regions, name, _ids(node.body))
+            for name in _isinstance_names(node.test, namespace, positive=False):
+                _widen(regions, name, _ids(node.orelse))
+                if _exits(node.body):
+                    _widen(regions, name, after(node))
+        elif isinstance(node, ast.IfExp):
+            for name in _isinstance_names(node.test, namespace, positive=True):
+                _widen(regions, name, _ids([node.body]))
+            for name in _isinstance_names(node.test, namespace, positive=False):
+                _widen(regions, name, _ids([node.orelse]))
+        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
+            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
+            calls = [c for c in ast.walk(node.value) if isinstance(c, ast.Call)]
+            if (
+                _is_context_exception(node.value)
+                or any(
+                    _callee(c) == "exception" and isinstance(c.func, ast.Attribute)
+                    for c in calls
+                )
+                or any(
+                    _callee(c) == "gather"
+                    and any(
+                        k.arg == "return_exceptions"
+                        and not (
+                            isinstance(k.value, ast.Constant) and k.value.value is False
+                        )
+                        for k in c.keywords
+                    )
+                    for c in calls
+                )
+            ):
+                for t in targets:
+                    if isinstance(t, ast.Name):
+                        _widen(regions, t.id, everywhere)
+
+    def held(use: ast.Name) -> bool:
+        return id(use) in regions.get(use.id, set())
+
+    changed = True
+    while changed:
+        changed = False
+        for node in nodes:
+            if isinstance(node, ast.Assign):
+                sources = [
+                    n
+                    for n in ([node.value] if isinstance(node.value, ast.Name) else [])
+                    + (
+                        [node.value.value]
+                        if isinstance(node.value, ast.Subscript)
+                        and isinstance(node.value.value, ast.Name)
+                        else []
+                    )
+                    if n.id in regions and held(n)
+                ]
+                if sources:
+                    for t in node.targets:
+                        if isinstance(t, ast.Name):
+                            # An alias escapes its region: held everywhere.
+                            changed |= _widen(regions, t.id, everywhere)
+            elif isinstance(node, (ast.For, ast.AsyncFor)):
+                live = {k: v for k, v in regions.items()}
+                for name in _loop_targets(node, live):
+                    changed |= _widen(regions, name, _ids(node.body))
+            elif isinstance(node, ast.comprehension):
+                for name in _loop_targets(node, regions):
+                    changed |= _widen(regions, name, everywhere)
+    # Narrowing the other way: in the `else` of `isinstance(x, T)`, and after
+    # `if isinstance(x, T): raise/return`, `x` is not an exception.
+    for node in nodes:
+        if isinstance(node, ast.If):
+            for name in _isinstance_names(node.test, namespace, positive=True):
+                if name in regions:
+                    regions[name] -= _ids(node.orelse)
+                    if _exits(node.body):
+                        regions[name] -= after(node)
+    return regions
+
+
+def _in_loop_iterable(use: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
+    child, node = use, parents.get(use)
+    while node is not None:
+        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
+            return child is node.iter
+        if isinstance(node, (ast.stmt, ast.Lambda)):
+            return False
+        child, node = node, parents.get(node)
+    return False
+
+
+def exception_sinks(
+    source: str, label: str, namespace: dict[str, Any] | None = None
+) -> list[str]:
+    """Every exception-to-text sink in `source` (see the module docstring)."""
+    tree = ast.parse(source)
+    namespace = {**(namespace or {}), **_snippet_namespace(tree)}
+    for function in ast.walk(tree):
+        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
+            function.name in _NON_RENDERING_FUNCTIONS
+        ):
+            function.body = [ast.Pass()]
+    imported = {
+        alias.asname or alias.name
+        for node in ast.walk(tree)
+        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pmcp")
+        for alias in node.names
+    }
+    parents = {
+        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
+    }
+    found: list[str] = []
+    for function in ast.walk(tree):
+        if (
+            isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
+            and function.name in _RENDERERS
+            and not label.endswith(_RENDERER_HOMES.get(function.name, "\0"))
+        ):
+            found.append(
+                f"{label}:{function.lineno}: shadows the renderer {function.name}"
+            )
+    for body in _scopes(tree):
+        regions = _exception_regions(body, namespace)
+        for use in _own_nodes(body):
+            if not (
+                isinstance(use, ast.Name)
+                and isinstance(use.ctx, ast.Load)
+                and id(use) in regions.get(use.id, set())
+            ):
+                continue
+            if _in_loop_iterable(use, parents):
+                continue  # the loop's targets are tracked instead
+            parent = parents.get(use)
+            if isinstance(parent, ast.keyword):
+                parent = parents.get(parent)
+            if isinstance(parent, ast.Call) and use is not parent.func:
+                if _is_renderer_call(parent, imported, label):
+                    continue
+            elif isinstance(parent, (ast.Raise, ast.Compare, ast.BoolOp, ast.UnaryOp)):
+                continue
+            elif (
+                isinstance(parent, (ast.If, ast.While, ast.Assert))
+                and use is parent.test
+            ):
+                continue
+            elif isinstance(parent, ast.IfExp) and use is parent.test:
+                continue
+            elif isinstance(parent, ast.Assign) and parent.value is use:
+                if all(isinstance(t, ast.Name) for t in parent.targets):
+                    continue
+            elif isinstance(parent, ast.Subscript) and parent.value is use:
+                grand = parents.get(parent)
+                if (
+                    isinstance(grand, ast.Assign)
+                    and grand.value is parent
+                    and all(isinstance(t, ast.Name) for t in grand.targets)
+                ):
+                    continue
+                if isinstance(grand, (ast.Raise, ast.Compare)):
+                    continue
+                if isinstance(grand, ast.Call) and _is_renderer_call(
+                    grand, imported, label
+                ):
+                    continue
+            elif isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)) and any(
+                n is use for n in ast.walk(parent.iter)
+            ):
+                continue
+            elif (
+                isinstance(parent, ast.Attribute)
+                and parent.attr not in _TEXT_ATTRIBUTES
+            ):
+                grand = parents.get(parent)
+                if not (isinstance(grand, ast.Call) and grand.func is parent):
+                    continue
+            found.append(f"{label}:{use.lineno}: {type(parent).__name__} uses {use.id}")
+    for node in ast.walk(tree):
+        if _is_context_exception(node):
+            parent = parents.get(node)
+            if isinstance(parent, ast.Assign) and all(
+                isinstance(t, ast.Name) for t in parent.targets
+            ):
+                continue  # tracked as an exception name
+            if isinstance(parent, ast.Call) and _is_renderer_call(
+                parent, imported, label
+            ):
+                continue
+            if isinstance(
+                parent, (ast.Compare, ast.BoolOp, ast.If, ast.UnaryOp, ast.Raise)
+            ):
+                continue
+            found.append(f"{label}:{node.lineno}: context['exception'] used as text")
+    for call in ast.walk(tree):
+        if not isinstance(call, ast.Call):
+            continue
+        callee = _callee(call)
+        if (
+            callee == "exception"
+            and (call.args or call.keywords)
+            and not any(k.arg == "timeout" for k in call.keywords)
+        ):
+            found.append(f"{label}:{call.lineno}: logger.exception renders a traceback")
+        if (
+            callee == "exception"
+            and isinstance(call.func, ast.Attribute)
+            and not call.args
+            and all(k.arg == "timeout" for k in call.keywords)
+            and not isinstance(parents.get(call), (ast.Assign, ast.IfExp))
+        ):
+            found.append(f"{label}:{call.lineno}: .exception() used inline")
+        if callee in _TRACEBACK_RENDERERS:
+            found.append(f"{label}:{call.lineno}: traceback.{callee}")
+        if (
+            callee in ("exc_info", "exception")
+            and isinstance(call.func, ast.Attribute)
+            and (isinstance(call.func.value, ast.Name) and call.func.value.id == "sys")
+        ):
+            found.append(f"{label}:{call.lineno}: sys.{callee}()")
+        if callee in ("makeRecord", "handle") and isinstance(call.func, ast.Attribute):
+            found.append(f"{label}:{call.lineno}: {callee} builds a record by hand")
+        for keyword in call.keywords:
+            if keyword.arg == "exc_info" and not (
+                isinstance(keyword.value, ast.Call)
+                and _callee(keyword.value) == "safe_exc_info"
+            ):
+                found.append(
+                    f"{label}:{call.lineno}: exc_info= not through safe_exc_info"
+                )
+            if keyword.arg is None and any(
+                isinstance(k, ast.Constant) and k.value == "exc_info"
+                for k in ast.walk(keyword.value)
+            ):
+                found.append(f"{label}:{call.lineno}: exc_info passed through **")
+    return list(dict.fromkeys(found))
+
+
+def test_no_exception_reaches_text_except_through_the_renderer() -> None:
+    sources = _sources()
+    assert len(sources) > 40, len(sources)
+    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
+    found = [
+        sink
+        for path in sources
+        for sink in exception_sinks(
+            path.read_text(), str(path.relative_to(root)), _namespace_for(path)
+        )
+    ]
+    assert found == [], "\n".join(found)
+    # Not vacuous: the analysis tracked exceptions through every module.
+    tracked = sum(
+        len(_exception_regions(body, _namespace_for(path)))
+        for path in sources
+        for body in _scopes(ast.parse(path.read_text()))
+    )
+    assert tracked > 100, tracked
+
+
+#: Every construct the rev 2 board seat fed rev 2's scanner (42; `except*`
+#: needs Python 3.11, this suite runs 3.10), plus the `_connect_with_retry`
+#: regression it found surviving every test. Each must be flagged.
+_FLAGGED = {
+    "alias_used_after_except": "last=None\nfor i in r:\n    try:\n        f()\n    except Exception as e:\n        last = e\nlog(f'{last}')\n",
+    "attr_store_then_render": "try:\n    f()\nexcept Exception as e:\n    self.last_error = e\nlog(f'{self.last_error}')\n",
+    "subscript_store": "try:\n    f()\nexcept Exception as e:\n    errs[name] = e\nlog(str(errs[name]))\n",
+    "import_alias_except": "from pydantic import ValidationError as PVE\ntry:\n    f()\nexcept PVE as e:\n    log(f'{e}')\n",
+    "custom_base_except": "try:\n    f()\nexcept (KeyError, TypeError, PydanticValidationError) as e:\n    log(f'{e}')\n",
+    "isinstance_boolop": "def g(r):\n    if isinstance(r, Exception) and r:\n        log(f'{r}')\n",
+    "isinstance_negative": "def g(r):\n    if not isinstance(r, BaseException):\n        return\n    log(f'{r}')\n",
+    "isinstance_valueerror": "def g(r):\n    if isinstance(r, ValueError):\n        log(f'{r}')\n",
+    "gather_results_loop": "async def g():\n    rs = await asyncio.gather(*ts, return_exceptions=True)\n    for r in rs:\n        log(f'{r}')\n",
+    "task_exception_inline": "def g(t):\n    log(f'{t.exception()}')\n",
+    "task_exception_ifexp": "def g(t):\n    exc = t.exception() if not t.cancelled() else None\n    log(f'{exc}')\n",
+    "sys_exc_info": "import sys\ntry:\n    f()\nexcept Exception:\n    log(str(sys.exc_info()[1]))\n",
+    "sys_exception": "import sys\ntry:\n    f()\nexcept Exception:\n    log(str(sys.exception()))\n",
+    "format_exception_only": "import sys, traceback\ntry:\n    f()\nexcept Exception:\n    log(''.join(traceback.format_exception_only(*sys.exc_info()[:2])))\n",
+    "tb_exception_obj": "import sys, traceback\ntry:\n    f()\nexcept Exception:\n    log(''.join(traceback.TracebackException(*sys.exc_info()).format()))\n",
+    "jsonschema_path": "try:\n    v()\nexcept Exception as e:\n    log(f'bad at {list(e.path)}')\n",
+    "jsonschema_json_path": "try:\n    v()\nexcept Exception as e:\n    log('bad at ' + e.json_path)\n",
+    "jsonschema_relative_path": "try:\n    v()\nexcept Exception as e:\n    log(e.relative_path)\n",
+    "notes": "try:\n    v()\nexcept Exception as e:\n    log(e.__notes__)\n",
+    "jsondecode_doc": "try:\n    v()\nexcept ValueError as e:\n    log(e.doc)\n",
+    "exc_info_kwargs_splat": "try:\n    f()\nexcept Exception:\n    logger.error('x', **{'exc_info': True})\n",
+    "logger_handle_make_record": "import sys\ntry:\n    f()\nexcept Exception:\n    logger.handle(logger.makeRecord('n', 40, 'f', 1, 'x', None, sys.exc_info()))\n",
+    "name_shadow_renderer": "def describe_exception(e):\n    return str(e)\n",
+    "renderer_attr_on_other_obj": "try:\n    f()\nexcept Exception as e:\n    log(other.type(e))\n",
+    "e_str_method": "try:\n    f()\nexcept Exception as e:\n    log(e.__str__())\n",
+    "getattr_args": "try:\n    f()\nexcept Exception as e:\n    log(getattr(e, 'message', ''))\n",
+    "vars_e": "try:\n    f()\nexcept Exception as e:\n    log(vars(e))\n",
+    "e_errors_call": "try:\n    f()\nexcept Exception as e:\n    log(e.errors())\n",
+    "e_json_call": "try:\n    f()\nexcept Exception as e:\n    log(e.json())\n",
+    "future_exception_timeout": "def g(fut):\n    exc = fut.exception(timeout=0)\n    log(f'{exc}')\n",
+    "warnings_warn": "import warnings\ntry:\n    f()\nexcept Exception as e:\n    warnings.warn(f'{e}')\n",
+    "percent_arg": "try:\n    f()\nexcept Exception as e:\n    logger.warning('x %s', e)\n",
+    "repr": "try:\n    f()\nexcept Exception as e:\n    x = repr(e)\n",
+    "chained_raise_text": "try:\n    f()\nexcept Exception as e:\n    raise RuntimeError(f'wrap {e}') from e\n",
+    "chained_raise_repr": "try:\n    f()\nexcept Exception as e:\n    raise RuntimeError('wrap %r' % (e,)) from e\n",
+    "logging_exception_module": "import logging\ntry:\n    f()\nexcept Exception:\n    logging.exception('x')\n",
+    "logger_exception_noargs": "try:\n    f()\nexcept Exception:\n    logger.exception(msg) if 0 else None\n",
+    "print_exc": "import traceback\ntry:\n    f()\nexcept Exception:\n    traceback.print_exc()\n",
+    "starred_args": "try:\n    f()\nexcept Exception as e:\n    log(*e.args)\n",
+    "exc_group_loop_var": "try:\n    f()\nexcept BaseExceptionGroup as eg:\n    for x in eg.exceptions:\n        log(f'{x}')\n",
+    "lambda_capture": "try:\n    f()\nexcept Exception as e:\n    cb = lambda: str(e)\n",
+    "return_exception": "def g():\n    try:\n        f()\n    except Exception as e:\n        return e\n",
+    "truncated_copy": "try:\n    f()\nexcept Exception as e:\n    raise RuntimeError(str(e)[:200]) from e\n",
+    "closure_in_except": (
+        "try:\n    f()\nexcept Exception as e:\n    def inner():\n        log(f'{e}')\n    later(inner)\n"
+    ),
+    "loop_exception_handler": (
+        "def handler(loop, context):\n    log(f\"{context['exception']}\")\n"
+    ),
+    "loop_exception_handler_alias": (
+        "def handler(loop, context):\n    exc = context.get('exception')\n    log(str(exc))\n"
+    ),
+    "connect_with_retry_regression": (
+        "async def _connect_with_retry(self, config):\n"
+        "    last_error = None\n"
+        "    for attempt in range(3):\n"
+        "        try:\n"
+        "            await self._connect_server(config)\n"
+        "            return\n"
+        "        except Exception as e:\n"
+        "            last_error = e\n"
+        "    if last_error:\n"
+        "        logger.warning(f'giving up: {last_error}')\n"
+        "        raise last_error\n"
+    ),
+}
+
+
+@pytest.mark.parametrize("label", sorted(_FLAGGED))
+def test_the_scanner_flags_each_construct(label: str) -> None:
+    """The static check is only as wide as its rules: each fires."""
+    assert exception_sinks(_FLAGGED[label], label), label
+
+
+def test_the_scanner_passes_the_renderers() -> None:
+    clean = (
+        "from pmcp.argument_errors import exception_text, safe_exc_info\n"
+        "try:\n    f()\nexcept Exception as e:\n"
+        "    last = e\n"
+        "    log(f'{exception_text(e)}', exc_info=safe_exc_info(e))\n"
+        "    if e.code == 1 or isinstance(e, KeyError):\n        raise\n"
+        "    raise X() from e\n"
+        "try:\n    f()\nexcept KeyError as e:\n    log(f'{e}')\n"
+        "def g(fut):\n    exc = fut.exception()\n    if exc is not None:\n"
+        "        log(exception_text(exc))\n        raise exc\n"
+    )
+    assert exception_sinks(clean, "clean") == []
````

### Patch — `tests/test_gateway_tool_schemas.py`

````diff
diff --git a/tests/test_gateway_tool_schemas.py b/tests/test_gateway_tool_schemas.py
index e6ddeb9..94456d8 100644
--- a/tests/test_gateway_tool_schemas.py
+++ b/tests/test_gateway_tool_schemas.py
@@ -437,3 +437,7 @@ async def _call_through_gate(name: str, arguments: dict[str, Any]) -> Any:
     [
-        ("gateway.describe", {"tool_id": ""}, "should be non-empty"),
+        (
+            "gateway.describe",
+            {"tool_id": ""},
+            "$.tool_id: must be at least 1 character",
+        ),
         (
@@ -441,3 +445,3 @@ async def _call_through_gate(name: str, arguments: dict[str, Any]) -> Any:
             {"title": "short", "description": "d"},
-            "is too short",
+            "$.title: must be at least 8 characters",
         ),
@@ -446,3 +450,3 @@ async def _call_through_gate(name: str, arguments: dict[str, Any]) -> Any:
             {"server_name": "s", "task_id": "t", "options": {"max_output_chars": 5}},
-            "less than the minimum",
+            "$.options.max_output_chars: must be greater than or equal to",
         ),
````

### Patch — `tests/test_http_transport.py`

````diff
diff --git a/tests/test_http_transport.py b/tests/test_http_transport.py
index e83214a..c4f2ffe 100644
--- a/tests/test_http_transport.py
+++ b/tests/test_http_transport.py
@@ -490,2 +490,48 @@ class TestHttpObservabilityContracts:
 
+    @pytest.mark.parametrize(
+        "client_kwargs",
+        [
+            {},
+            {"allowed_origins": ["https://app.example"]},
+            {"auth_token": "gateway-token"},
+        ],
+        ids=["default", "allowlist", "shared-secret"],
+    )
+    def test_malformed_origin_port_is_rejected_without_echo(
+        self,
+        client_kwargs: dict[str, object],
+        capfd: pytest.CaptureFixture[str],
+        caplog: pytest.LogCaptureFixture,
+    ) -> None:
+        """An unauthenticated Origin whose port is not a number is a 403, not a
+        500 whose log line quotes the port (Consiliency/pmcp#297, rev 7 B2)."""
+        import logging
+
+        # ASCII: an Origin header is Latin-1 on the wire.
+        sentinel = "Q7portSentinel" + "x" * 20
+        client = _make_contract_client(**client_kwargs)  # type: ignore[arg-type]
+        caplog.set_level(logging.DEBUG)
+
+        response = client.post(
+            "/mcp",
+            headers={"Origin": f"http://evil.example:{sentinel}"},
+            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
+        )
+
+        assert response.status_code == 403
+        out, err = capfd.readouterr()
+        observed = "\n".join(
+            [
+                response.text,
+                str(dict(response.headers)),
+                out,
+                err,
+                *(record.getMessage() for record in caplog.records),
+                *(str(record.exc_info) for record in caplog.records),
+            ]
+        )
+        assert len(sentinel) == 34
+        assert sentinel not in observed
+        assert sentinel.encode("utf-8").hex() not in observed.encode("utf-8").hex()
+
     def test_no_origin_and_loopback_and_same_origin_pass_by_default(self) -> None:
````

### Patch — `tests/test_log_record_scrubber.py`

````diff
diff --git a/tests/test_log_record_scrubber.py b/tests/test_log_record_scrubber.py
new file mode 100644
index 0000000..1eaf856
--- /dev/null
+++ b/tests/test_log_record_scrubber.py
@@ -0,0 +1,342 @@
+"""Every log record is scrubbed at creation, whoever logs it
+(Consiliency/pmcp#297, rev 4).
+
+`install_log_scrubber` wraps the `logging` record factory, so it covers
+loggers pmcp does not own and cannot enumerate -- the MCP SDK's
+`ClientSession` logs on `"client"`, outside `mcp.*` (the rev 3 board's B1)
+-- and each branch of `scrub_record` is pinned here: `exc_info`, `%`-args
+(including exceptions nested in containers and a `%(name)s` mapping), a
+`msg` that is itself an exception, and `stack_info`.
+"""
+
+from __future__ import annotations
+
+import logging
+import traceback
+from collections.abc import Iterator
+from typing import Any
+
+import pytest
+from pydantic import ValidationError
+
+from pmcp.types import McpTaskInfo
+from tests.test_argument_error_echo import _FAMILIES, _forbidden, _record_text
+
+_LOGGERS = (
+    "client",
+    "server",
+    "mcp.client.session",
+    "asyncio",
+    "uvicorn.error",
+    "httpx",
+    "anyio",
+    "third.party",
+)
+
+
+def _validation_error(s: str) -> ValidationError:
+    try:
+        McpTaskInfo.model_validate({"task_id": "t", "ttl": {"v": s}})
+    except ValidationError as error:
+        return error
+    raise AssertionError("no validation error")
+
+
+class _Capture(logging.Handler):
+    def __init__(self) -> None:
+        super().__init__(logging.DEBUG)
+        self.records: list[logging.LogRecord] = []
+
+    def emit(self, record: logging.LogRecord) -> None:
+        self.records.append(record)
+
+
+@pytest.fixture
+def capture() -> Iterator[_Capture]:
+    """A handler on every logger under test, with propagation off, so the
+    record the scrubber made is the one inspected."""
+    from pmcp.argument_errors import install_log_scrubber
+
+    install_log_scrubber()
+    handler = _Capture()
+    saved = []
+    for name in _LOGGERS:
+        logger = logging.getLogger(name)
+        saved.append((logger, logger.level, logger.propagate))
+        logger.addHandler(handler)
+        logger.setLevel(logging.DEBUG)
+        logger.propagate = False
+    yield handler
+    for logger, level, propagate in saved:
+        logger.removeHandler(handler)
+        logger.setLevel(level)
+        logger.propagate = propagate
+
+
+def _clean(record: logging.LogRecord, s: str) -> bool:
+    text = _record_text(record)
+    return not any(form in text for form in _forbidden(s))
+
+
+@pytest.mark.parametrize("name", _LOGGERS)
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_exc_info_is_scrubbed_on_any_logger(
+    capture: _Capture, name: str, family: str
+) -> None:
+    s = _FAMILIES[family][1]
+    try:
+        raise _validation_error(s)
+    except ValidationError:
+        logging.getLogger(name).warning(
+            "Failed to validate notification: %s", "m", exc_info=True
+        )
+    (record,) = capture.records
+    assert record.exc_info is None
+    assert "validation error for McpTaskInfo: $.ttl" in record.getMessage()
+    assert _clean(record, s)
+
+
+@pytest.mark.parametrize(
+    "shape",
+    ["tuple", "list", "dict", "set-free-nested", "mapping", "repr"],
+)
+def test_percent_args_are_scrubbed_however_nested(
+    capture: _Capture, shape: str
+) -> None:
+    s = _FAMILIES["alpha"][1]
+    error = _validation_error(s)
+    logger = logging.getLogger("third.party")
+    if shape == "tuple":
+        logger.error("failed: %s", error)
+    elif shape == "list":
+        logger.error("failed: %s", [1, error])
+    elif shape == "dict":
+        logger.error("failed: %s", {"k": error})
+    elif shape == "set-free-nested":
+        logger.error("failed: %s", ([{"k": (error,)}],))
+    elif shape == "mapping":
+        logger.error("failed: %(e)s", {"e": error})
+    else:
+        logger.error("failed: %r", error)
+    (record,) = capture.records
+    assert _clean(record, s), record.getMessage()
+    assert "validation error for McpTaskInfo" in record.getMessage()
+
+
+def test_msg_that_is_an_exception_is_scrubbed(capture: _Capture) -> None:
+    s = _FAMILIES["hex"][1]
+    logging.getLogger("client").error(_validation_error(s))
+    (record,) = capture.records
+    assert _clean(record, s)
+    assert "validation error for McpTaskInfo" in record.getMessage()
+
+
+def test_stack_info_carries_no_exception_text(capture: _Capture) -> None:
+    """`stack_info` renders frames and source lines, never an exception's
+    text, so the scrubber leaves it; this pins that it stays clean inside an
+    `except` block holding a validation error."""
+    s = _FAMILIES["digits"][1]
+    try:
+        raise _validation_error(s)
+    except ValidationError:
+        logging.getLogger("client").warning("inside", stack_info=True)
+    (record,) = capture.records
+    assert record.stack_info
+    assert _clean(record, s)
+
+
+def test_other_exceptions_and_values_pass_unchanged(capture: _Capture) -> None:
+    logger = logging.getLogger("third.party")
+    plain = RuntimeError("plain failure")
+    try:
+        raise plain
+    except RuntimeError:
+        logger.error("x %r %s", plain, {"k": 1}, exc_info=True)
+    (record,) = capture.records
+    assert record.args == (plain, {"k": 1})
+    assert record.exc_info is not None and record.exc_info[1] is plain
+    assert "RuntimeError('plain failure')" in record.getMessage()
+    assert "plain failure" in "".join(traceback.format_exception(*record.exc_info))
+
+
+def test_install_is_idempotent_and_wraps_the_previous_factory() -> None:
+    from pmcp.argument_errors import install_log_scrubber
+
+    original = logging.getLogRecordFactory()
+    marked: list[Any] = []
+
+    def custom(*args: Any, **kwargs: Any) -> logging.LogRecord:
+        record = original(*args, **kwargs)
+        marked.append(record)
+        return record
+
+    logging.setLogRecordFactory(custom)
+    try:
+        install_log_scrubber()
+        installed = logging.getLogRecordFactory()
+        install_log_scrubber()
+        assert logging.getLogRecordFactory() is installed
+        assert getattr(installed, "previous", None) is custom
+        logging.getLogger("third.party").warning("hello")
+        assert marked, "the previous factory was not called"
+    finally:
+        logging.setLogRecordFactory(original)
+        install_log_scrubber()
+
+
+def test_the_gateway_installs_the_scrubber(tmp_path: Any) -> None:
+    from pmcp.server import GatewayServer
+
+    original = logging.getLogRecordFactory()
+    logging.setLogRecordFactory(logging.LogRecord)
+    try:
+        policy = tmp_path / "policy.json"
+        policy.write_text("{}")
+        GatewayServer(policy_path=policy, cache_dir=tmp_path / "cache")
+        assert getattr(logging.getLogRecordFactory(), "pmcp_validation_scrubber", False)
+    finally:
+        logging.setLogRecordFactory(original)
+
+
+# --- every entry point installs it (rev 5, rev 4 board B1) --------------------
+
+_MARKER = "import logging; print(getattr(logging.getLogRecordFactory(), 'pmcp_validation_scrubber', False))"
+
+
+def _console_scripts() -> list[str]:
+    """The console scripts pmcp's distribution declares (pyproject's
+    `[project.scripts]`), as `module:attr`."""
+    from importlib.metadata import distribution
+
+    return sorted(
+        ep.value
+        for ep in distribution("pmcp").entry_points
+        if ep.group == "console_scripts"
+    )
+
+
+def _entry_points() -> dict[str, str]:
+    """A fresh interpreter per entry point: the snippet enters pmcp that way
+    and then prints whether the record factory is the scrubber."""
+    snippets = {
+        f"import {module}": f"import {module}\n{_MARKER}"
+        for module in (
+            "pmcp",
+            "pmcp.cli",
+            "pmcp.manifest.refresher",
+            "pmcp.transport.http",
+        )
+    }
+    snippets["python -m pmcp --version"] = (
+        "import runpy, sys\nsys.argv = ['pmcp', '--version']\n"
+        "try:\n    runpy.run_module('pmcp', run_name='__main__')\n"
+        "except SystemExit:\n    pass\n" + _MARKER
+    )
+    for value in _console_scripts():
+        module, _, attr = value.partition(":")
+        snippets[f"console script {value}"] = (
+            f"import importlib\ngetattr(importlib.import_module({module!r}), {attr!r})\n{_MARKER}"
+        )
+    return snippets
+
+
+@pytest.mark.parametrize("entry", sorted(_entry_points()))
+def test_every_entry_point_installs_the_scrubber(entry: str) -> None:
+    import subprocess
+    import sys
+
+    assert _console_scripts(), "pmcp declares no console script?"
+    result = subprocess.run(
+        [sys.executable, "-c", _entry_points()[entry]],
+        capture_output=True,
+        text=True,
+        timeout=120,
+    )
+    assert result.returncode == 0, result.stderr
+    assert result.stdout.strip().splitlines()[-1] == "True", (entry, result.stdout)
+
+
+_CLI_REFRESH = """
+import json, sys
+from pathlib import Path
+import pmcp.cli as cli
+import pmcp.manifest.refresher as refresher
+from pmcp.manifest.loader import ServerConfig
+
+tmp, script = Path(sys.argv[1]), sys.argv[2]
+config = ServerConfig(name="d", description="d", keywords=[], install={},
+                      command=sys.executable, args=[script, str(tmp / "case.json")])
+
+class _Manifest:
+    servers = {"d": config}
+    def get_server(self, name):
+        return self.servers.get(name)
+
+# Only the manifest lookup is replaced: the downstream is the test's.
+refresher.load_manifest = lambda *a, **k: _Manifest()
+sys.argv = ["pmcp", "refresh", "--server", "d", "--force",
+            "--cache-dir", str(tmp / "cache"), "-l", "info"]
+try:
+    cli.main()
+except SystemExit:
+    pass
+"""
+
+
+@pytest.mark.parametrize(
+    "kind",
+    [
+        "notifications/message",
+        "notifications/progress",
+        "notifications/resources/updated",
+    ],
+)
+def test_pmcp_refresh_logs_no_downstream_value(tmp_path: Any, kind: str) -> None:
+    """The operator's `pmcp refresh` (the real `pmcp.cli.main`, in a fresh
+    interpreter) against a downstream whose malformed notification the
+    SDK's `ClientSession` rejects: neither stderr nor `.pmcp/logs/gateway.log`
+    carries the value."""
+    import json
+    import os
+    import subprocess
+    import sys
+
+    from tests.test_downstream_frame_echo import _SESSION_MESSAGES, _SESSION_SCRIPT
+
+    script = tmp_path / "session_downstream.py"
+    script.write_text(_SESSION_SCRIPT)
+    driver = tmp_path / "cli_refresh.py"
+    driver.write_text(_CLI_REFRESH)
+    for family in ("hex", "alpha", "unicode"):
+        s = _FAMILIES[family][1]
+        (tmp_path / "case.json").write_text(
+            json.dumps([kind, _SESSION_MESSAGES[kind](s)])
+        )
+        (tmp_path / "case.json.in").unlink(missing_ok=True)
+        result = subprocess.run(
+            [sys.executable, str(driver), str(tmp_path), str(script)],
+            capture_output=True,
+            text=True,
+            timeout=120,
+            cwd=tmp_path,
+            env={**os.environ, "HOME": str(tmp_path)},
+        )
+        log_file = tmp_path / ".pmcp" / "logs" / "gateway.log"
+        log = log_file.read_text(errors="replace") if log_file.exists() else ""
+        for channel, text in (
+            ("stdout", result.stdout),
+            ("stderr", result.stderr),
+            ("log file", log),
+        ):
+            assert not any(form in text for form in _forbidden(s)), (
+                kind,
+                family,
+                channel,
+                text,
+            )
+        # No vacuous pass: the refresh ran, and the SDK rejected the message.
+        assert "Refreshing server: d" in result.stdout, result.stdout + result.stderr
+        assert "Failed to validate notification" in result.stderr + log, (
+            kind,
+            result.stderr,
+        )
````

### Patch — `tests/test_parse_error_echo.py`

````diff
diff --git a/tests/test_parse_error_echo.py b/tests/test_parse_error_echo.py
new file mode 100644
index 0000000..b12fe09
--- /dev/null
+++ b/tests/test_parse_error_echo.py
@@ -0,0 +1,875 @@
+"""A parse error never echoes the structured text it rejected
+(Consiliency/pmcp#297; rev 6, reclassified by origin in rev 7).
+
+PyYAML renders a snippet of the input around the error mark, and a YAML tag
+makes it run a constructor whose *plain* ``ValueError`` / ``KeyError`` quotes
+the value (``k: !!int <value>``). So a failure is classified by where it
+happened, not by its type: every parse of structured text in ``src/pmcp``
+goes through ``pmcp.parsing`` (``load_yaml`` / ``load_json`` /
+``load_json_file`` / ``parse_timestamp``), which turns any exception raised
+while parsing into a value-free ``ParseError`` that chains nothing.
+
+Two halves:
+- **static**: no module in ``src/pmcp`` but ``parsing.py`` references a
+  parser -- resolved through ``import x as y`` / ``from x import y as z`` /
+  ``import datetime`` -- except the attributes named safe below (an
+  allowlist, so a parser entry point nobody listed fails), nor calls a
+  distinctive parser name on any receiver (``.fromisoformat``,
+  ``.safe_load``, ``.load_all``, ``.raw_decode``, ...), nor ``.json()`` on a
+  response; the exemptions are named, with the reason, and must all still
+  exist;
+- **dynamic**: a sentinel in each parser's rejected input -- syntax errors,
+  every value-constructing YAML tag, undefined and duplicate anchors -- at
+  every file-reading site pmcp exposes as a function, checked (in any case)
+  in the log, the raised message, the traceback the gateway would print
+  uncaught and stdout/stderr; plus the helpers themselves, the timestamp
+  sites, and a fresh interpreter for the fatal policy path.
+"""
+
+from __future__ import annotations
+
+import ast
+import json
+import logging
+import os
+import subprocess
+import sys
+from collections.abc import Callable
+from pathlib import Path
+from typing import Any
+
+import pytest
+
+from tests.test_argument_error_echo import _FAMILIES, _forbidden, _record_text
+
+_SRC = Path(__file__).resolve().parents[1] / "src" / "pmcp"
+
+#: The one module allowed to call a parser.
+_HELPERS = "parsing.py"
+
+#: Per resolved module (or class), the attributes that do not parse text.
+#: An allowlist: any other attribute of these -- ``safe_load``, ``load_all``,
+#: ``compose``, ``JSONDecoder``, ``fromisoformat``, ``strptime``, whatever a
+#: later release adds -- is a parser reference.
+_SAFE_ATTRIBUTES: dict[str, frozenset[str]] = {
+    "json": frozenset({"dumps", "dump", "JSONDecodeError", "JSONEncoder"}),
+    "yaml": frozenset(
+        {
+            "YAMLError",
+            "MarkedYAMLError",
+            "dump",
+            "dump_all",
+            "safe_dump",
+            "safe_dump_all",
+            "Dumper",
+            "SafeDumper",
+        }
+    ),
+    "tomllib": frozenset({"TOMLDecodeError"}),
+    "datetime.datetime": frozenset(
+        {"now", "utcnow", "fromtimestamp", "utcfromtimestamp", "min", "max"}
+    ),
+    "datetime.date": frozenset({"today", "fromtimestamp", "min", "max"}),
+}
+
+#: Parser entry points recognisable by name on ANY receiver -- a variable, an
+#: attribute, a class the resolver cannot follow.
+_PARSER_NAMES = frozenset(
+    {
+        "fromisoformat",
+        "strptime",
+        "safe_load",
+        "safe_load_all",
+        "load_all",
+        "full_load",
+        "full_load_all",
+        "unsafe_load",
+        "unsafe_load_all",
+        "raw_decode",
+        "JSONDecoder",
+    }
+)
+
+#: Named exemptions, ``file::function`` -> reason. Each must still exist.
+_EXEMPT: dict[str, str] = {
+    # python-dotenv never raises on bad input; it logs "python-dotenv could
+    # not parse statement starting at line N" -- the line number only
+    # (measured on python-dotenv 1.x).
+    "env_store.py::read_env_file": "dotenv_values",
+    "cli.py::load_startup_env": "load_dotenv",
+    "tools/handlers.py::_check_api_key_available": "load_dotenv",
+    # A response object's `.json()`: JSON has no constructors, so its
+    # failures are JSONDecodeError (fixed vocabulary; the `doc` attribute is
+    # rendered by exception_text as `could not parse JSON ...`),
+    # RecursionError, or aiohttp's ContentTypeError (the server's MIME type).
+    # Every one is caught and rendered via exception_text or swallowed.
+    "manifest/version_checker.py::get_npm_version": ".json() (aiohttp)",
+    "manifest/version_checker.py::get_pypi_version": ".json() (aiohttp)",
+    "manifest/version_checker.py::get_cargo_version": ".json() (aiohttp)",
+    "manifest/version_checker.py::get_docker_version": ".json() (aiohttp)",
+    "cli.py::_probe_http_health": ".json() (httpx), error swallowed",
+}
+
+_DOTENV = frozenset({"dotenv_values", "load_dotenv"})
+
+
+def _aliases(tree: ast.AST) -> dict[str, str]:
+    """Local name -> the dotted name it is bound to, for every import in the
+    module (at any depth: a function-local ``import yaml`` counts)."""
+    bound: dict[str, str] = {}
+    for node in ast.walk(tree):
+        if isinstance(node, ast.Import):
+            for alias in node.names:
+                if alias.asname:
+                    bound[alias.asname] = alias.name
+                else:
+                    head = alias.name.split(".")[0]
+                    bound[head] = head
+        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
+            for alias in node.names:
+                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
+    return bound
+
+
+def _qualified(node: ast.AST, bound: dict[str, str]) -> str | None:
+    parts: list[str] = []
+    while isinstance(node, ast.Attribute):
+        parts.append(node.attr)
+        node = node.value
+    if not isinstance(node, ast.Name) or node.id not in bound:
+        return None
+    return ".".join([bound[node.id], *reversed(parts)])
+
+
+#: The parser packages: an attribute of any of their SUBMODULES
+#: (``yaml.loader.SafeLoader``, ``yaml.cyaml.CSafeLoader``,
+#: ``yaml.constructor.SafeConstructor``, ``json.decoder.JSONDecoder``) is a
+#: parser reference unless its name is safe in the package itself (rev 8,
+#: round-7 claude (3)).
+_PARSER_PACKAGES = ("json", "yaml", "tomllib")
+
+
+def _violation(qualified: str) -> bool:
+    owner, _, attr = qualified.rpartition(".")
+    if owner in _SAFE_ATTRIBUTES:
+        return attr not in _SAFE_ATTRIBUTES[owner]
+    package = owner.split(".")[0]
+    if package in _PARSER_PACKAGES and owner != package:
+        return attr not in _SAFE_ATTRIBUTES[package]
+    return False
+
+
+def _parser_references(source: str) -> list[tuple[int, str]]:
+    """``(line, what)`` for every parser reference in ``source``."""
+    tree = ast.parse(source)
+    bound = _aliases(tree)
+    found: list[tuple[int, str]] = []
+    for node in ast.walk(tree):
+        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
+            for alias in node.names:
+                if _violation(f"{node.module}.{alias.name}"):
+                    found.append(
+                        (node.lineno, f"from {node.module} import {alias.name}")
+                    )
+        elif isinstance(node, ast.Attribute):
+            qualified = _qualified(node, bound)
+            if qualified is not None and _violation(qualified):
+                found.append((node.lineno, qualified))
+            elif node.attr in _PARSER_NAMES:
+                found.append((node.lineno, f".{node.attr}"))
+            elif (
+                isinstance(node.value, ast.Attribute)
+                and node.value.attr in _PARSER_PACKAGES
+                and node.attr not in _SAFE_ATTRIBUTES[node.value.attr]
+            ):
+                # A parser package reached through another module's attribute
+                # (``policy.yaml.load``), which the resolver cannot follow.
+                found.append((node.lineno, f".{node.value.attr}.{node.attr}"))
+        elif isinstance(node, ast.Call):
+            func = node.func
+            if isinstance(func, ast.Attribute) and func.attr == "json":
+                # With or without arguments (``resp.json(content_type=None)``).
+                found.append((node.lineno, ".json()"))
+            elif isinstance(func, ast.Name) and (
+                func.id in _DOTENV or bound.get(func.id, "").startswith("dotenv.")
+            ):
+                found.append((node.lineno, func.id))
+        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
+            qualified = bound.get(node.id)
+            if qualified is not None and _violation(qualified):
+                found.append((node.lineno, qualified))
+    return sorted(set(found))
+
+
+def _owner(tree: ast.AST, line: int) -> str:
+    functions = [
+        n
+        for n in ast.walk(tree)
+        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
+    ]
+    owner = min(
+        (f for f in functions if f.lineno <= line <= (f.end_lineno or f.lineno)),
+        key=lambda f: (f.end_lineno or f.lineno) - f.lineno,
+        default=None,
+    )
+    return owner.name if owner else "<module>"
+
+
+def _parse_sites() -> dict[str, list[str]]:
+    """``file::function`` -> the parser references in it, outside the helpers."""
+    found: dict[str, list[str]] = {}
+    for path in sorted(_SRC.rglob("*.py")):
+        rel = path.relative_to(_SRC).as_posix()
+        if rel == _HELPERS or "baml_client" in path.parts:
+            continue
+        source = path.read_text()
+        tree = ast.parse(source)
+        for line, what in _parser_references(source):
+            found.setdefault(f"{rel}::{_owner(tree, line)}", []).append(
+                f"{line}: {what}"
+            )
+    return found
+
+
+def test_no_parser_call_outside_the_helpers() -> None:
+    sites = _parse_sites()
+    unexempt = {k: v for k, v in sites.items() if k not in _EXEMPT}
+    assert not unexempt, unexempt
+    # Every exemption still names a real call: a stale one would hide the
+    # next parse added to that function.
+    assert set(_EXEMPT) == set(sites), sorted(set(_EXEMPT) ^ set(sites))
+
+
+@pytest.mark.parametrize(
+    "snippet",
+    [
+        "import yaml\nyaml.safe_load(t)",
+        "import yaml as y\ny.safe_load(t)",
+        "from yaml import safe_load\nsafe_load(t)",
+        "from yaml import safe_load as sl\nsl(t)",
+        "import yaml\nyaml.load_all(t)",
+        "import yaml\nyaml.compose(t)",
+        "import yaml\nf = yaml.full_load",
+        "import json\njson.loads(t)",
+        "import json as j\nj.load(f)",
+        "from json import loads\nloads(t)",
+        "import json\njson.JSONDecoder().decode(t)",
+        "from json import JSONDecoder\nJSONDecoder().raw_decode(t)",
+        "import tomllib\ntomllib.loads(t)",
+        "from datetime import datetime\ndatetime.fromisoformat(t)",
+        "from datetime import datetime as dt\ndt.fromisoformat(t)",
+        "import datetime\ndatetime.datetime.fromisoformat(t)",
+        "import datetime\ndatetime.date.fromisoformat(t)",
+        "from datetime import datetime\ndatetime.strptime(t, f)",
+        "cls.fromisoformat(t)",
+        "self._yaml.safe_load(t)",
+        "response.json()",
+        "await resp.json(content_type=None)",
+        "from yaml.loader import SafeLoader\nSafeLoader(t).get_single_data()",
+        "from yaml.cyaml import CSafeLoader\nCSafeLoader(t).get_single_data()",
+        "from yaml.constructor import SafeConstructor\nSafeConstructor()",
+        "from json.decoder import JSONDecoder\nJSONDecoder().decode(t)",
+        "import yaml.loader\nyaml.loader.SafeLoader(t)",
+        "policy.yaml.load(t, Loader=policy.yaml.SafeLoader)",
+        "from dotenv import load_dotenv as ld\nld(p)",
+        "def f():\n    import yaml\n    return yaml.safe_load(t)",
+    ],
+)
+def test_the_parse_site_check_sees_every_spelling(snippet: str) -> None:
+    """No vacuous pass: each spelling of a parser call is found."""
+    assert _parser_references(snippet), snippet
+
+
+@pytest.mark.parametrize(
+    "snippet",
+    [
+        "import json\njson.dumps(v)",
+        "import yaml\nyaml.safe_dump(v)",
+        "from datetime import datetime, timezone\ndatetime.now(timezone.utc)",
+        "import json\nexcept_types = (json.JSONDecodeError, ValueError)",
+        "from pmcp.parsing import load_yaml\nload_yaml(t, source='x')",
+        "from json.decoder import JSONDecodeError\nexcept_types = (JSONDecodeError,)",
+        "from yaml.error import MarkedYAMLError\nisinstance(e, MarkedYAMLError)",
+        "self.yaml.safe_dump(v)",
+    ],
+)
+def test_the_parse_site_check_passes_non_parsers(snippet: str) -> None:
+    assert not _parser_references(snippet), snippet
+
+
+# --- dynamic ---------------------------------------------------------------------
+
+
+def _yaml_bad(s: str) -> str:
+    """Four ways PyYAML rejects input, each quoting it in its message."""
+    return f"servers: [{s}}}\n"
+
+
+def _yaml_bads(s: str) -> list[str]:
+    """Every way PyYAML rejects input that we know quotes it -- syntax, and
+    each tag whose constructor raises a *plain* exception (rev 7, B1)."""
+    return [
+        f"servers: [{s}}}\n",  # ParserError (flow sequence)
+        f"a: b\n  c: {s}: d\n",  # ScannerError (mapping values)
+        f'k: "{s}\n',  # ScannerError (unterminated quote)
+        f"k: !{s} v\n",  # ConstructorError (the tag is the input)
+        f"k: !!int {s}\n",  # ValueError: invalid literal for int() ... '<s>'
+        f"k: !!int 0x{s}\n",  # ValueError (base 16)
+        f"k: !!float {s}\n",  # ValueError: could not convert ... '<s lowered>'
+        f"k: !!bool {s}\n",  # KeyError: '<s lowered>'
+        f"k: !!timestamp {s}\n",  # AttributeError
+        f"k: !!binary {s}!\n",  # ConstructorError (base64)
+        f"k: !!python/object:{s} {{}}\n",  # ConstructorError (the tag)
+        f"k: !!python/name:{s} ''\n",  # ConstructorError (the tag)
+        f"a: &x1 [1]\nb: *{s}\n",  # ComposerError: undefined alias '<s>'
+        f"a: &{s} 1\nb: &{s} 2\n",  # ComposerError: duplicate anchor '<s>'
+        f"k: !!set [{s}]\n",  # ConstructorError (node kind)
+        f"k: {{<<: {s}}}\n",  # ConstructorError (merge)
+        # Accepted, so filtered out by `_rejects` (measured: last key wins):
+        f"{s}: 1\n{s}: 2\n",  # duplicate keys
+    ]
+
+
+def _json_bads(s: str) -> list[str]:
+    return [f'{{"k": {s}}}', f'{{"{s}": 1,}}', f"[1, 2, {s}"]
+
+
+def _run(fn: Callable[[], Any]) -> tuple[str, str]:
+    """(the raised error as pmcp renders it, the traceback the gateway would
+    print uncaught) for `fn`. pmcp renders an exception only through
+    `exception_text` -- the static guard pins that at every sink -- so that,
+    not the library's own `str()`, is what reaches a caller; a message pmcp
+    builds itself (`ValueError(f"... {exception_text(e)}")`) is covered by
+    the same call."""
+    try:
+        from pmcp.argument_errors import exception_text, safe_traceback_text
+    except ImportError:  # a tree without the renderer (main, for the red run)
+        import traceback
+
+        def exception_text(error: BaseException) -> str:
+            return str(error)
+
+        def safe_traceback_text(error: BaseException) -> str:
+            return "".join(
+                traceback.format_exception(type(error), error, error.__traceback__)
+            )
+
+    try:
+        fn()
+    except BaseException as error:  # noqa: BLE001 -- inspected
+        return exception_text(error), safe_traceback_text(error)
+    return "", ""
+
+
+def _rejects(parser: str, text: str) -> bool:
+    """Whether the raw parser rejects ``text`` -- with ANY exception: a tag's
+    constructor raises ``ValueError``/``KeyError``/``AttributeError``."""
+    import yaml
+
+    try:
+        (yaml.safe_load if parser == "yaml" else json.loads)(text)
+    except Exception:  # noqa: BLE001 -- any rejection counts
+        return True
+    return False
+
+
+def _forbidden_any_case(s: str) -> set[str]:
+    """``_forbidden`` of the sentinel in each case: PyYAML's float and bool
+    constructors quote the value lowercased."""
+    return _forbidden(s) | _forbidden(s.lower()) | _forbidden(s.upper())
+
+
+def _policy(tmp: Path, content: str, suffix: str, fatal: bool) -> Callable[[], Any]:
+    from pmcp.policy.policy import PolicyManager
+
+    path = tmp / f"policy{suffix}"
+    path.write_text(content)
+    return lambda: PolicyManager()._parse_policy(content, path, fatal=fatal)
+
+
+def _write(tmp: Path, name: str, content: str) -> Path:
+    path = tmp / name
+    path.write_text(content)
+    return path
+
+
+def _cases() -> list[tuple[str, str, Callable[[Path, str], Callable[[], Any]]]]:
+    """(label, parser, builder(tmp, bad text) -> call)."""
+    from pmcp import package_approvals, trust_store
+    from pmcp.config import guidance
+    from pmcp.config import loader as config_loader
+    from pmcp.manifest import loader as manifest_loader
+    from pmcp.manifest import refresher, registry
+    from pmcp.manifest.code_patterns_loader import CodePatternsLoader
+    from pmcp.templates.code_snippets_loader import CodeSnippetsLoader
+
+    return [
+        ("policy yaml, warn", "yaml", lambda t, c: _policy(t, c, ".yaml", False)),
+        ("policy yaml, fatal", "yaml", lambda t, c: _policy(t, c, ".yaml", True)),
+        ("policy json, warn", "json", lambda t, c: _policy(t, c, ".json", False)),
+        ("policy json, fatal", "json", lambda t, c: _policy(t, c, ".json", True)),
+        (
+            "manifest overlay",
+            "yaml",
+            lambda t, c: lambda: manifest_loader._parse_overlay_document(
+                t / "overlay.yaml", c.encode()
+            ),
+        ),
+        (
+            "manifest",
+            "yaml",
+            lambda t, c: lambda: manifest_loader.load_manifest(_write(t, "m.yaml", c)),
+        ),
+        (
+            "config file",
+            "json",
+            lambda t, c: lambda: config_loader.parse_config_bytes(
+                c.encode(), t / ".mcp.json"
+            ),
+        ),
+        (
+            "config object",
+            "json",
+            lambda t, c: lambda: _returned(
+                config_loader._config_object_from_bytes(c.encode())
+            ),
+        ),
+        (
+            "guidance",
+            "yaml",
+            lambda t, c: lambda: guidance.load_guidance_config(_write(t, "g.yaml", c)),
+        ),
+        (
+            "code patterns",
+            "yaml",
+            lambda t, c: lambda: CodePatternsLoader(_write(t, "p.yaml", c)),
+        ),
+        (
+            "code snippets",
+            "yaml",
+            lambda t, c: lambda: CodeSnippetsLoader(_write(t, "s.yaml", c)),
+        ),
+        (
+            "descriptions cache",
+            "yaml",
+            lambda t, c: lambda: refresher.load_descriptions_cache(
+                _write(t, "d.yaml", c)
+            ),
+        ),
+        (
+            "registry cache",
+            "json",
+            lambda t, c: lambda: registry.load_registry_cache(_write(t, "r.json", c)),
+        ),
+        (
+            "trust store",
+            "json",
+            lambda t, c: lambda: trust_store._read_store(_write(t, "trust.json", c)),
+        ),
+        (
+            "package approvals",
+            "json",
+            lambda t, c: lambda: package_approvals._read_store(_write(t, "a.json", c)),
+        ),
+    ]
+
+
+def _returned(value: Any) -> None:
+    """A site that returns its diagnostic instead of raising: make the
+    returned text the 'raised' text."""
+    raise RuntimeError(repr(value))
+
+
+@pytest.mark.parametrize("label", [c[0] for c in _cases()])
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_no_parse_site_echoes_its_input(
+    tmp_path: Path,
+    caplog: pytest.LogCaptureFixture,
+    capfd: pytest.CaptureFixture[str],
+    label: str,
+    family: str,
+) -> None:
+    caplog.set_level(logging.DEBUG)
+    (case,) = [c for c in _cases() if c[0] == label]
+    _, parser, build = case
+    s = _FAMILIES[family][1]
+    # Only inputs the parser rejects: a digits-only sentinel makes `{"k": 123}`
+    # valid JSON, which is data, not a parse error.
+    bads = [
+        bad
+        for bad in (_yaml_bads(s) if parser == "yaml" else _json_bads(s))
+        if _rejects(parser, bad)
+    ]
+    assert len(bads) >= (12 if parser == "yaml" else 2), (label, family, bads)
+    forbidden = _forbidden_any_case(s)
+    silent: list[str] = []
+    for bad in bads:
+        start = len(caplog.records)
+        capfd.readouterr()
+        raised, shown = _run(build(tmp_path, bad))
+        streams = capfd.readouterr()
+        logged = "\n".join(_record_text(r) for r in caplog.records[start:])
+        for channel, text in (
+            ("raised", raised),
+            ("traceback", shown),
+            ("log", logged),
+            ("stdout/stderr", streams.out + streams.err),
+        ):
+            assert not any(form in text for form in forbidden), (
+                label,
+                bad,
+                channel,
+                text,
+            )
+        everything = raised + shown + logged + streams.out + streams.err
+        if "could not parse" not in everything and (
+            raised or shown or streams.out.strip() or streams.err.strip()
+        ):
+            silent.append(bad)
+    # No vacuous pass, checked after every input's leak check: for each
+    # rejected input pmcp said it could not parse, or said nothing at all (a
+    # site that falls back silently, e.g. a cache miss). PyYAML reading a file
+    # omits the snippet but a constructor error still names the input's tag,
+    # so the leak half needs every input, not just the first.
+    assert not silent, (label, silent)
+
+
+def test_an_uncaught_fatal_policy_error_prints_no_input(tmp_path: Path) -> None:
+    """An explicit policy that does not parse is fatal: the gateway exits with
+    the error uncaught, and the interpreter prints it. Its chain holds the
+    YAML error, whose text quotes the file; the excepthook `import pmcp`
+    installs prints it structurally."""
+    s = _FAMILIES["token"][1]
+    policy = tmp_path / "policy.yaml"
+    policy.write_text(_yaml_bad(s))
+    result = subprocess.run(
+        [
+            sys.executable,
+            "-c",
+            "import sys; from pmcp.policy.policy import PolicyManager; "
+            "PolicyManager(policy_path=__import__('pathlib').Path(sys.argv[1]))",
+            str(policy),
+        ],
+        capture_output=True,
+        text=True,
+        timeout=120,
+        env={**os.environ, "HOME": str(tmp_path)},
+        cwd=tmp_path,
+    )
+    assert result.returncode != 0
+    assert "could not parse YAML policy file at line 1" in result.stderr, result.stderr
+    assert "Traceback (most recent call last)" in result.stderr
+    assert not any(form in result.stderr + result.stdout for form in _forbidden(s)), (
+        result.stderr
+    )
+
+
+def test_parse_text_names_only_format_class_and_position() -> None:
+    import yaml
+
+    from pmcp.argument_errors import exception_text
+
+    s = _FAMILIES["alpha"][1]
+    for bad, expected in (
+        (f"servers: [{s}}}", "could not parse YAML (ParserError) at line 1, column "),
+        (f"k: !{s} v", "could not parse YAML (ConstructorError) at line 1, column "),
+    ):
+        try:
+            yaml.safe_load(bad)
+        except yaml.YAMLError as error:
+            # PyYAML's snippet truncates long lines; any window is the leak.
+            assert any(form in str(error) for form in _forbidden(s)), str(error)
+            assert exception_text(error).startswith(expected), exception_text(error)
+    try:
+        json.loads(f'{{"k": {s}}}')
+    except json.JSONDecodeError as error:
+        assert (
+            exception_text(error)
+            == "could not parse JSON (JSONDecodeError) at line 1, column 7"
+        )
+
+
+def test_a_failing_log_handler_prints_no_input(
+    capfd: pytest.CaptureFixture[str],
+) -> None:
+    """A handler whose `emit` raises while an `except` block for a parse
+    error is active: `logging.Handler.handleError` prints the active chain to
+    stderr. pmcp's wrapper prints it structurally."""
+    import yaml
+
+    import pmcp  # noqa: F401 -- installs the scrubber, excepthook and handleError
+
+    class _Broken(logging.Handler):
+        def emit(self, record: logging.LogRecord) -> None:
+            # As every stdlib handler does: a failing emit calls handleError.
+            try:
+                raise OSError("disk gone")
+            except Exception:
+                self.handleError(record)
+
+    s = _FAMILIES["hex"][1]
+    logger = logging.getLogger("pmcp.test.broken")
+    handler = _Broken()
+    logger.addHandler(handler)
+    try:
+        capfd.readouterr()
+        try:
+            yaml.safe_load(f"servers: [{s}}}")
+        except yaml.YAMLError:
+            logger.error("could not load")
+        err = capfd.readouterr().err
+    finally:
+        logger.removeHandler(handler)
+    assert "--- Logging error ---" in err
+    assert "could not parse YAML (ParserError)" in err, err
+    assert "OSError: disk gone" in err, err
+    assert not any(form in err for form in _forbidden(s)), err
+
+
+# --- the helpers themselves (rev 7) ---------------------------------------------
+
+
+def _helper_yaml_inputs(s: str) -> list[str]:
+    return [
+        *(bad for bad in _yaml_bads(s) if _rejects("yaml", bad)),
+        "[" * 3000 + s + "]" * 3000,  # RecursionError
+    ]
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_load_yaml_classifies_every_failure_by_origin(family: str) -> None:
+    import yaml
+
+    from pmcp.argument_errors import exception_text, safe_traceback_text
+    from pmcp.parsing import ParseError, YAMLParseError, load_yaml
+
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden_any_case(s)
+    causes: set[str] = set()
+    for bad in _helper_yaml_inputs(s):
+        with pytest.raises(YAMLParseError) as caught:
+            load_yaml(bad, source="policy file")
+        error = caught.value
+        # It chains nothing, so no traceback printer can reach the original.
+        assert error.__cause__ is None and error.__context__ is None, bad
+        assert isinstance(error, (yaml.YAMLError, ValueError, ParseError))
+        for text in (
+            str(error),
+            repr(error),
+            exception_text(error),
+            safe_traceback_text(error),
+            repr(error.args),
+        ):
+            assert not any(form in text for form in forbidden), (bad, text)
+        assert str(error).startswith("could not parse YAML policy file"), str(error)
+        causes.add(error.cause or "")
+    # No vacuous pass: the plain exceptions the board found are among them.
+    plain = {"ValueError", "KeyError", "AttributeError", "RecursionError"}
+    if family == "digits":
+        plain.discard("ValueError")  # `!!int <digits>` is a valid int
+    assert plain <= causes, causes
+    assert {"ParserError", "ScannerError", "ConstructorError"} <= causes, causes
+    if family in ("alpha", "hex", "token"):
+        # A name with no space or leading digit is a valid anchor name.
+        assert "ComposerError" in causes, causes
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_load_json_classifies_every_failure_by_origin(family: str) -> None:
+    from pmcp.argument_errors import exception_text, safe_traceback_text
+    from pmcp.parsing import JSONParseError, load_json
+
+    s = _FAMILIES[family][1]
+    forbidden = _forbidden_any_case(s)
+    inputs: list[Any] = [
+        *(b for b in _json_bads(s) if _rejects("json", b)),
+        "[" * 100000 + json.dumps(s) + "]" * 100000,  # RecursionError
+        s.encode("utf-8") + b"\xff",  # UnicodeDecodeError (encoding=)
+    ]
+    causes: set[str] = set()
+    for bad in inputs:
+        with pytest.raises(JSONParseError) as caught:
+            load_json(bad, source="registry response", encoding="utf-8")
+        error = caught.value
+        assert error.__cause__ is None and error.__context__ is None
+        assert isinstance(error, json.JSONDecodeError) and error.doc == ""
+        for text in (str(error), exception_text(error), safe_traceback_text(error)):
+            assert not any(form in text for form in forbidden), (bad, text)
+        causes.add(error.cause or "")
+    assert {"RecursionError", "UnicodeDecodeError"} <= causes, causes
+
+
+def test_duplicate_yaml_keys_are_data_not_a_parse_error() -> None:
+    """Measured, so the sweep's `_rejects` filter drops them: PyYAML's
+    safe_load keeps the last value."""
+    from pmcp.parsing import load_yaml
+
+    assert load_yaml("a: 1\na: 2\n", source="x") == {"a": 2}
+
+
+@pytest.mark.parametrize("family", sorted(_FAMILIES))
+def test_no_timestamp_site_echoes_its_input(family: str) -> None:
+    """``datetime.fromisoformat`` quotes a bad timestamp (rev 7, N1)."""
+    from pmcp import package_approvals, trust_store
+    from pmcp.argument_errors import exception_text, safe_traceback_text
+    from pmcp.types import McpTaskInfo
+
+    s = "2026-13-99T" + _FAMILIES[family][1]
+    forbidden = _forbidden_any_case(_FAMILIES[family][1])
+    calls: list[Callable[[], Any]] = [
+        lambda: trust_store._decode(
+            {
+                "absolute_path": "/x",
+                "content_sha256": "0" * 64,
+                "scope": "project",
+                "decision": "approved",
+                "recorded_at": s,
+            }
+        ),
+        lambda: package_approvals._decode(
+            {
+                "registry": "npm",
+                "name": "left-pad",
+                "resolved_version": "1.3.0",
+                "integrity": None,
+                "decision": "approved",
+                "recorded_at": s,
+            }
+        ),
+        lambda: McpTaskInfo(task_id="t", created_at=s),
+    ]
+    for call in calls:
+        with pytest.raises(Exception) as caught:
+            call()
+        error = caught.value
+        for text in (exception_text(error), safe_traceback_text(error)):
+            assert not any(form in text for form in forbidden), text
+    try:
+        calls[0]()
+    except Exception as error:  # noqa: BLE001 -- inspected
+        assert "could not parse timestamp trust store record" in str(error)
+
+
+def test_parse_error_is_rendered_as_its_own_text() -> None:
+    """A ParseError subclasses the parser's type but is not a parser's own
+    error: exception_text keeps its source label and safe_exc_info keeps the
+    traceback."""
+    from pmcp.argument_errors import exception_text, safe_exc_info
+    from pmcp.parsing import load_yaml
+
+    try:
+        load_yaml("k: !!int nope\n", source="guidance config")
+    except Exception as error:  # noqa: BLE001 -- inspected
+        assert (
+            exception_text(error) == "could not parse YAML guidance config (ValueError)"
+        )
+        assert safe_exc_info(error) is error
+    else:  # pragma: no cover
+        raise AssertionError("did not raise")
+
+
+def test_a_thread_prints_no_input(tmp_path: Path) -> None:
+    """``threading.excepthook`` prints an uncaught thread exception, chain and
+    all (rev 7, N3): the wrapper `import pmcp` installs prints it
+    structurally, under the interpreter's header and qualified class name;
+    with no stderr it prints nothing, like the default."""
+    s = _FAMILIES["token"][1]
+    script = (
+        "import sys, threading, yaml, pmcp\n"
+        "def run():\n"
+        "    try:\n"
+        "        yaml.safe_load(sys.argv[1])\n"
+        "    except yaml.YAMLError as e:\n"
+        "        raise RuntimeError('boom') from e\n"
+        "t = threading.Thread(target=run, name='parser'); t.start(); t.join()\n"
+        "if len(sys.argv) > 2:\n"
+        "    sys.stderr = None\n"
+        "    t = threading.Thread(target=run); t.start(); t.join()\n"
+    )
+    result = subprocess.run(
+        [sys.executable, "-c", script, f"servers: [{s}}}"],
+        capture_output=True,
+        text=True,
+        timeout=120,
+        cwd=tmp_path,
+    )
+    assert "Exception in thread parser:" in result.stderr, result.stderr
+    assert "yaml.parser.ParserError: could not parse YAML (ParserError)" in (
+        result.stderr
+    ), result.stderr
+    assert "RuntimeError: boom" in result.stderr
+    assert not any(form in result.stderr for form in _forbidden(s)), result.stderr
+    silent = subprocess.run(
+        [sys.executable, "-c", script, f"servers: [{s}}}", "no-stderr"],
+        capture_output=True,
+        text=True,
+        timeout=120,
+        cwd=tmp_path,
+    )
+    assert silent.returncode == 0, silent.stderr
+    assert silent.stderr.count("Exception in thread") == 1, silent.stderr
+
+
+@pytest.mark.parametrize("origin", ["yaml", "pydantic"])
+def test_an_uncaught_chain_prints_no_input(tmp_path: Path, origin: str) -> None:
+    """``sys.excepthook`` prints an uncaught exception's chain. Since rev 7 a
+    pmcp parse failure chains nothing, so this pins the wrapper on the
+    chains that still can hold a value: a parser's own error raised outside
+    ``pmcp.parsing`` and a pydantic ``ValidationError`` (the rev 7 mutation
+    run found M34, "no excepthook", surviving without it)."""
+    s = _FAMILIES["token"][1]
+    raise_inner = {
+        "yaml": "    import yaml\n    yaml.safe_load(sys.argv[1])\n",
+        "pydantic": (
+            "    import pydantic\n"
+            "    pydantic.TypeAdapter(int).validate_python(sys.argv[1])\n"
+        ),
+    }[origin]
+    script = (
+        "import sys, pmcp\n"
+        "try:\n" + raise_inner + "except Exception as e:\n"
+        "    raise RuntimeError('boom') from e\n"
+    )
+    arg = f"servers: [{s}}}" if origin == "yaml" else s
+    result = subprocess.run(
+        [sys.executable, "-c", script, arg],
+        capture_output=True,
+        text=True,
+        timeout=120,
+        cwd=tmp_path,
+    )
+    assert result.returncode == 1, result.stderr
+    assert "Traceback (most recent call last)" in result.stderr, result.stderr
+    assert "RuntimeError: boom" in result.stderr, result.stderr
+    expected = {
+        "yaml": "yaml.parser.ParserError: could not parse YAML (ParserError)",
+        "pydantic": "pydantic_core._pydantic_core.ValidationError: 1 validation error",
+    }[origin]
+    assert expected in result.stderr, result.stderr
+    assert not any(form in result.stderr for form in _forbidden(s)), result.stderr
+
+
+def test_an_uncaught_error_with_no_stderr_prints_nothing(tmp_path: Path) -> None:
+    """The default excepthook is silent when ``sys.stderr`` is None; so is
+    pmcp's wrapper (rev 7 nit), instead of raising AttributeError."""
+    script = (
+        "import sys, yaml, pmcp\n"
+        "try:\n"
+        "    yaml.safe_load('servers: [x}')\n"
+        "except yaml.YAMLError as e:\n"
+        "    err = RuntimeError('boom')\n"
+        "    err.__cause__ = e\n"
+        "sys.stderr = None\n"
+        "sys.excepthook(RuntimeError, err, None)\n"
+        "sys.stdout.write('survived')\n"
+    )
+    result = subprocess.run(
+        [sys.executable, "-c", script],
+        capture_output=True,
+        text=True,
+        timeout=120,
+        cwd=tmp_path,
+    )
+    assert result.returncode == 0, result.stderr
+    assert result.stdout == "survived"
+    assert result.stderr == ""
````

### Patch — `tests/test_scoped_advisor_audit.py`

````diff
diff --git a/tests/test_scoped_advisor_audit.py b/tests/test_scoped_advisor_audit.py
index 138e06a..9d427ad 100644
--- a/tests/test_scoped_advisor_audit.py
+++ b/tests/test_scoped_advisor_audit.py
@@ -1360,22 +1360,13 @@ _MARKERS = (
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
 
@@ -1506,2 +1497,6 @@ _EXCEPTION_ARGS: dict[str, tuple[Any, ...]] = {
     "MissingApiKeyError": ("STUB_VAR", "stub", "stub"),
+    # `pmcp.parsing` (Consiliency/pmcp#297, rev 7): (kind, source).
+    "YAMLParseError": ("YAML", "stub handler failed"),
+    "JSONParseError": ("JSON", "stub handler failed"),
+    "TimestampParseError": ("timestamp", "stub handler failed"),
 }
@@ -1588,2 +1583,23 @@ def _invocations(audit_path: Path, event: str) -> list[dict[str, Any]]:
 
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
@@ -1601,10 +1617,2 @@ def _assert_nothing_generated_leaked(
 
-def _is_foreign(message: str, name: str | None = None) -> bool:
-    """Whether `message` is exactly one of the lines above -- the whole of it.
-    `name` is the tool name of the call that logged it, if known."""
-    if _FOREIGN_INVOKE_INPUT.fullmatch(message):
-        return True
-    return name is not None and message == f"Tool execution error: Unknown tool: {name}"
-
-
 _PMCP_FORMATTERS: list[logging.Formatter] = []
@@ -1633,3 +1641,5 @@ def _log_text(
     """Every log record as pmcp's formatters render it -- message, and any
-    `exc_info` traceback -- bar the exact foreign lines above."""
+    `exc_info` traceback. Nothing is excluded: the two pre-existing echoes
+    the sweep used to skip (a non-dict `meta`, an unknown tool's name) are
+    fixed by Consiliency/pmcp#297."""
     # The capture is live (DEBUG, root), so an empty result is not vacuous
@@ -1637,7 +1647,6 @@ def _log_text(
     assert caplog.records, "nothing was logged, so the log oracle proves nothing"
-    excluded = calls.foreign if calls is not None else set()
+    del calls  # every call's log is in `caplog`
     return "\n".join(
-        formatter.format(shown)
+        formatter.format(record)
         for record in caplog.records
-        if (shown := _oracle_view(record, excluded)) is not None
         for formatter in _pmcp_formatters()
@@ -1646,26 +1655,2 @@ def _log_text(
 
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
@@ -1686,7 +1671,6 @@ def _normalized(record: logging.LogRecord, formatter: logging.Formatter) -> str:
 
-def _normalized_log(records: list[logging.LogRecord], excluded: set[int]) -> str:
+def _normalized_log(records: list[logging.LogRecord]) -> str:
     return "\n".join(
-        _normalized(shown, formatter)
+        _normalized(record, formatter)
         for record in records
-        if (shown := _oracle_view(record, excluded)) is not None
         for formatter in _pmcp_formatters()
@@ -1704,5 +1688,2 @@ class _LoggedCalls:
         self.caplog = caplog
-        #: Records that are exactly `Tool execution error: Unknown tool: <name>`
-        #: for the name of the call that logged them.
-        self.foreign: set[int] = set()
 
@@ -1713,9 +1694,3 @@ class _LoggedCalls:
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
@@ -1760,3 +1735,3 @@ async def test_generated_caller_values_never_reach_an_allowed_record(
     baseline = _declared_baseline(tool)
-    exempt = _INVOKE_ONLY_EXEMPT if tool_name == "gateway.invoke" else frozenset()
+    exempt: frozenset[str] = frozenset()
     server, audit_path, real_tools = _allowed_server(tmp_path)
@@ -1824,3 +1799,3 @@ async def test_generated_caller_values_never_reach_an_allowed_record(
 
-    records = _invocations(audit_path, "audit.invocation")
+    records = _call_records(audit_path)
     ok_count = 1 + len(_TAGS) * len(cases)
@@ -1831,4 +1806,4 @@ async def test_generated_caller_values_never_reach_an_allowed_record(
     ):
-        _assert_pair(tool_name, label, first, second, case_exempt - _INVOKE_ONLY_EXEMPT)
-        _assert_pair(tool_name, label, first, reference, case_exempt)
+        _assert_pair(tool_name, label, first, second, case_exempt)
+        _assert_against(tool_name, label, first, reference, case_exempt)
     errors = records_per_case[1:]
@@ -1840,3 +1815,3 @@ async def test_generated_caller_values_never_reach_an_allowed_record(
         _assert_pair(tool_name, label, first, second, frozenset())
-        _assert_pair(tool_name, label, first, reference, exempt)
+        _assert_against(tool_name, label, first, reference, exempt)
     _assert_nothing_generated_leaked(
@@ -1878,3 +1853,3 @@ async def test_generated_caller_values_never_reach_an_uncorrelated_invoke_record
 
-    records = _invocations(audit_path, "audit.invocation")
+    records = _call_records(audit_path)
     assert len(records) == 1 + len(_TAGS) * len(_SHAPES)
@@ -1884,5 +1859,3 @@ async def test_generated_caller_values_never_reach_an_uncorrelated_invoke_record
         _assert_pair("gateway.invoke", f"E6 {shape}", first, second, frozenset())
-        _assert_pair(
-            "gateway.invoke", f"E6 {shape}", first, reference, _INVOKE_ONLY_EXEMPT
-        )
+        _assert_against("gateway.invoke", f"E6 {shape}", first, reference, frozenset())
     _assert_nothing_generated_leaked(
@@ -2030,3 +2003,3 @@ async def test_generated_caller_values_on_the_real_scoped_handlers(
     baseline = _declared_baseline(tool)
-    exempt = _INVOKE_ONLY_EXEMPT if tool_name == "gateway.invoke" else frozenset()
+    exempt: frozenset[str] = frozenset()
     server, audit_path = _scoped_server(tmp_path)
@@ -2050,3 +2023,3 @@ async def test_generated_caller_values_on_the_real_scoped_handlers(
 
-    records = _invocations(audit_path, "audit.invocation")
+    records = _call_records(audit_path)
     assert len(records) == 1 + len(_TAGS) * len(_SHAPES)
@@ -2055,3 +2028,3 @@ async def test_generated_caller_values_on_the_real_scoped_handlers(
         _assert_pair(tool_name, f"real {shape}", first, second, frozenset())
-        _assert_pair(tool_name, f"real {shape}", first, reference, exempt)
+        _assert_against(tool_name, f"real {shape}", first, reference, exempt)
     _assert_nothing_generated_leaked(
@@ -2075,107 +2048,53 @@ def test_the_log_normalisation_keeps_a_hex_count_and_drops_only_the_time() -> No
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

## Measurement scripts

`probe.py`, `count_leaks.py` and `codemod.py` (rev 1–2 measurement and provenance scripts) are embedded in rev 5 of this plan (`91562e6`); they are unchanged, and omitted here to keep the plan under its size budget.

### `mutants.py` — the mutation run; `python mutants.py <worktree> <out-dir> [M4 ...]`, `NO_STATIC=1` deselects both static checks

```python
"""Apply each mutant to a saved copy, run the tests, restore from the copy.

usage: python mutants.py <worktree> <out-dir>
"""
import filecmp, os, shutil, subprocess, sys
from pathlib import Path

root, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
# The dynamic sweeps first, the static guard last: under -x the named reason
# is the first dynamic failure when there is one.
TESTS = ["tests/test_argument_error_echo.py", "tests/test_downstream_frame_echo.py",
         "tests/test_log_record_scrubber.py", "tests/test_parse_error_echo.py",
         "tests/test_scoped_advisor_audit.py", "tests/test_gateway_tool_schemas.py",
         "tests/test_http_transport.py", "tests/test_exception_text_sinks.py"]
S = "src/pmcp/server.py"; A = "src/pmcp/argument_errors.py"; T = "src/pmcp/types.py"
H = "src/pmcp/tools/handlers.py"; D = "src/pmcp/scoped_advisor_audit.py"
U = "src/pmcp/auth.py"; P = "src/pmcp/__init__.py"; L = "src/pmcp/cli.py"; C = "src/pmcp/client/manager.py"; I = "src/pmcp/manifest/installer.py"
R = "src/pmcp/parsing.py"; O = "src/pmcp/policy/policy.py"; W = "src/pmcp/transport/http.py"; K = "src/pmcp/trust_store.py"
MUTANTS = [
 ("M1 gate renders e.message", S, [("+ describe_schema_error(e, tool.input_schema, arguments),", "+ e.message,")]),
 ("M2 except arm returns str(e)", S, [('"message": described[:400],', '"message": str(e)[:400],')]),
 ("M3 except arm logs str(e)", S, [("                        audited_name,\n                        reason,\n", "                        audited_name,\n                        str(e),\n")]),
 ("M4 model rejection recorded as invocation", S, [("                    if rejected_by_model and tool is not None:\n", "                    if False:\n")]),
 ("M5 model loc not redacted", A, [("        elif type(segment) is str and segment in declared:\n", "        elif type(segment) is str:\n")]),
 ("M6 schema path not redacted", A, [("        elif isinstance(node, dict) and type(segment) is str and segment in declared:\n", "        elif isinstance(node, dict) and type(segment) is str:\n")]),
 ("M7 phrase constraint from pydantic ctx", A, [("    items = error.errors(include_url=False, include_input=False, include_context=False)\n    parts = []\n", "    items = error.errors(include_url=False, include_input=False, include_context=True)\n    parts = []\n"), ("_model_phrase(item['type'], _schema_node_at(schema, loc))", "_model_phrase(item['type'], {'enum': [(item.get('ctx') or {}).get('expected')], 'minLength': (item.get('ctx') or {}).get('min_length'), 'exclusiveMinimum': (item.get('ctx') or {}).get('gt')})")]),
 ("M8 missing-required reads an instance key", A, [("    for name in required:\n        if isinstance(name, str) and name not in node:\n            return name\n    return None\n", "    return str(list(node)[-1])\n")]),
 ("M9 validator back to ValueError with value", T, [("            raise argument_error(CORRELATION_ID_CHARSET)\n", '            raise ValueError(f"correlation IDs may contain only alphanumerics and ._:-: {value}")\n')]),
 ("M10 provision_status validates inside its try", H, [("        parsed = ProvisionStatusInput.model_validate(input_data)\n        job_id = parsed.job_id\n        try:\n", "        try:\n            parsed = ProvisionStatusInput.model_validate(input_data)\n            job_id = parsed.job_id\n"), ("                job_id=job_id,\n                server=\"unknown\",", "                job_id=input_data.get(\"job_id\", \"unknown\"),\n                server=\"unknown\",")]),
 ("M11 unknown tool name logged", S, [('logger.error("Tool execution error: unknown gateway tool")', 'logger.error(f"Tool execution error: {e}")')]),
 ("M12 type phrase is e.message", A, [('        return f"must be of type {_type_names(constraint)}", None\n', "        return error.message, None\n")]),
 ("M13 model phrase is pydantic msg", A, [("f\"{path}: {_model_phrase(item['type'], _schema_node_at(schema, loc))}\"", "f\"{path}: {item['msg']}\"")]),
 ("M14 audit model path from input", D, [("                argument_path = model_error_path(error, schema, arguments)\n", "                argument_path = [str(i.get('input')) for i in error.errors()]\n")]),
 ("M15 sanitize_auth_diagnostic uses str(value)", U, [("    raw = exception_text(value) if isinstance(value, BaseException) else str(value)\n", "    raw = str(value)\n")]),
 ("M16 describe_exception leaf uses str(leaf)", C, [('        f"{type(leaf).__name__}: {exception_text(leaf)}"\n', '        f"{type(leaf).__name__}: {leaf}"\n')]),
 ("M17 exception_text skips validation errors", A, [("    if _is_validation_error(error):\n        return _validation_text(error)\n    text = str(error)\n", "    text = str(error)\n")]),
 ("M18 safe_exc_info always returns the error", A, [("    if any(_is_validation_error(linked) for linked in _chain(error)):\n        return None\n    return error\n", "    return error\n")]),
 ("M19 tasks_get response uses str(e)", H, [("            return TasksGetOutput(ok=False, errors=[self._sanitize_error(e)])\n", "            return TasksGetOutput(ok=False, errors=[str(e)])\n")]),
 ("M20 tasks_get audit buffer uses str(e)", H, [("                task_id=parsed.task_id,\n                error=exception_text(e),\n            )\n            return TasksGetOutput(ok=False", "                task_id=parsed.task_id,\n                error=str(e),\n            )\n            return TasksGetOutput(ok=False")]),
 ("M21 exception_text ignores an embedded validation error", A, [("            if embedded and embedded in text:\n", "            if False:\n")]),
 ("M22 installer crash message uses raw exc (static guard)", I, [('job.error = f"Monitor task crashed: {exception_text(exc)}"', 'job.error = f"Monitor task crashed: {exc}"')]),
 ("M23 SDK parse error keeps its message", C, [("    if code == _PARSE_ERROR:\n", "    if False:\n")]),
 ("M24 no record scrubber installed", A, [("    logging.setLogRecordFactory(_scrubbing_factory(current))\n", "    pass\n")]),
 ("M25 malformed error message kept", C, [("    if not isinstance(message, str):\n", "    if False:\n")]),
 ("M26 scrubber keeps the traceback", A, [("            record.exc_info = None\n", "")]),
 ("M27 scrubber misses a non-mcp logger", A, [("        return scrub_record(previous(*args, **kwargs))\n", "        record = previous(*args, **kwargs)\n        return scrub_record(record) if str(record.name).startswith(\"mcp\") else record\n")]),
 ("M28 %-args branch removed", A, [("        if record.args:\n            record.args = _scrubbed(record.args)\n", "")]),
 ("M29 msg-is-an-exception branch removed", A, [("        if isinstance(record.msg, BaseException):\n            record.msg = _scrubbed(record.msg)\n", "")]),
 ("M30 nested containers not walked", A, [("    if isinstance(value, tuple):\n        return tuple(_scrubbed(item, depth + 1) for item in value)\n", "    if isinstance(value, tuple):\n        return tuple(exception_text(i) if isinstance(i, BaseException) and safe_exc_info(i) is None else i for i in value)\n")]),
 ("M31 install removed from pmcp/__init__.py", P, [("_install_log_scrubber()\n", "")]),
 ("M32 YAML branch removed from the parse set", A, [("    types: list[type[BaseException]] = [yaml.YAMLError, json.JSONDecodeError]\n", "    types: list[type[BaseException]] = [json.JSONDecodeError]\n")]),
 ("M33 parse errors not treated as value-bearing", A, [("    ) or _is_parse_error(error)\n", "    )\n")]),
 ("M34 no excepthook installed", A, [("    _install_excepthook()\n", "")]),
 ("M35 CLI refresh logs the raw exception", L, [('        logger.error(f"Refresh failed: {exception_text(e)}")\n', '        logger.error(f"Refresh failed: {e}")\n')]),
 ("M36 no handleError wrapper installed", A, [("    _install_handle_error()\n", "")]),
 ("M37 helper bypassed at one site (policy YAML)", O, [('                data = load_yaml(content, source="policy file")\n', '                data = __import__("yaml").safe_load(content)\n')]),
 ("M38 B2 guard removed", W, [("    try:\n        parsed = urlparse(origin)\n        hostname = parsed.hostname\n        explicit_port = parsed.port\n    except ValueError:\n        return None\n", "    parsed = urlparse(origin)\n    hostname = parsed.hostname\n    explicit_port = parsed.port\n")]),
 ("M39 load_yaml classifies by type (YAMLError only)", R, [("        return yaml.safe_load(stream)\n    except Exception as error:  # noqa: BLE001 -- classified by origin\n", "        return yaml.safe_load(stream)\n    except yaml.YAMLError as error:\n")]),
 ("M40 ParseError raised inside the except (chained)", R, [("        failure = (line, column, type(error).__name__)\n    line, column, cause = failure\n    raise YAMLParseError", "        raise YAMLParseError(\"YAML\", source, line, column, cause=type(error).__name__)\n    line, column, cause = failure\n    raise YAMLParseError")]),
 ("M41 ParseError rendered as a raw parse error", A, [("    return isinstance(error, _PARSE_ERRORS) and not isinstance(error, ParseError)\n", "    return isinstance(error, _PARSE_ERRORS)\n")]),
 ("M42 timestamp helper bypassed (trust store)", K, [('parse_timestamp(str(recorded_at), source="trust store record")', '__import__("datetime").datetime.fromisoformat(str(recorded_at))')]),
 ("M43 no threading.excepthook wrapper", A, [("    _install_threading_excepthook()\n", "")]),
 ("M44 excepthook writes with no stderr", A, [('            if sys.stderr is not None:\n                sys.stderr.write(safe_traceback_text(value) + "\\n")\n', '            sys.stderr.write(safe_traceback_text(value) + "\\n")\n')]),
 ("M45 unqualified class name", A, [("_qualified_name(type(current))", "type(current).__name__")]),
 ("M46 stdio: a rejected frame logged raw again", C, [("            if not _is_downstream_output(text):\n", "            if False:\n")]),
 ("M50 stdio: the quote excluded from where JSON starts", C, [("""_JSON_VALUE_START = frozenset('{["-0123456789tfnNI')""", """_JSON_VALUE_START = frozenset('{[-0123456789tfnNI')""")]),
 ("M51 stdio: shown output not truncated at a frame opener", C, [("    if not cuts:\n        return body.rstrip()\n", "    if True:\n        return body.rstrip()\n")]),
 ("M52 stdio: control and format characters not skipped", C, [('        char.isspace() or char == "\\ufffd" or unicodedata.category(char) in ("Cc", "Cf")\n', '        char.isspace() or char == "\\ufffd"\n')]),
 ("M53 stdio: Unicode whitespace not skipped", C, [("        char.isspace() or char", "        char in ' \\t\\r\\n' or char")]),
 ("M48 static check ignores parser submodules", "tests/test_parse_error_echo.py", [("    if package in _PARSER_PACKAGES and owner != package:\n", "    if False:\n")]),
 ("G1 connect retry logs last_error", C, [("        if last_error:\n            raise last_error\n", "        if last_error:\n            logger.warning(f\"giving up: {last_error}\")\n            raise last_error\n")]),
 ("S5 value in a log extra= field", S, [("                        audited_name,\n                        reason,\n                    )\n", "                        audited_name,\n                        reason,\n                        extra={'args_dump': repr(arguments)},\n                    )\n")]),
 ("S6 arguments printed to stderr", S, [("                    reason = describe_model_error(e, tool.input_schema, arguments)\n", "                    reason = describe_model_error(e, tool.input_schema, arguments)\n                    print(arguments, file=sys.stderr)\n")]),
 ("S7 arguments in warnings.warn", S, [("                    reason = describe_model_error(e, tool.input_schema, arguments)\n", "                    reason = describe_model_error(e, tool.input_schema, arguments)\n                    __import__('warnings').warn(str(arguments))\n")]),
 ("S8 echo only isalpha values", S, [("+ describe_schema_error(e, tool.input_schema, arguments),", "+ describe_schema_error(e, tool.input_schema, arguments) + ''.join(' ' + v for v in arguments.values() if isinstance(v, str) and v.isalpha()),")]),
]
ONLY = sys.argv[3:]
for label, rel, edits in MUTANTS:
    if ONLY and label.split()[0] not in ONLY:
        continue
    path = root / rel; saved = out / (path.name + ".saved")
    # Refuse to start from a dirty file: a killed earlier run must not have
    # its mutant saved as the "clean" copy (rev 7).
    assert subprocess.run(["git", "diff", "--quiet", "--", rel], cwd=root).returncode == 0, (label, "dirty before mutation")
    shutil.copy2(path, saved)
    text = path.read_text()
    for old, new in edits:
        n = text.count(old)
        assert n == 1, (label, n, old)
        text = text.replace(old, new)
    path.write_text(text)
    log = out / (label.split()[0] + ".log")
    extra = ["--deselect", "tests/test_exception_text_sinks.py::test_no_exception_reaches_text_except_through_the_renderer",
             "--deselect", "tests/test_parse_error_echo.py::test_no_parser_call_outside_the_helpers"] if os.environ.get("NO_STATIC") else []
    r = subprocess.run([str(root / ".venv/bin/python"), "-m", "pytest", *TESTS, "-q", "-p", "no:cacheprovider", "-x", "--tb=line", *extra],
                       cwd=root, capture_output=True, text=True)
    log.write_text(r.stdout + r.stderr)
    shutil.copy2(saved, path)
    assert filecmp.cmp(saved, path, shallow=False), label
    lines = (r.stdout).splitlines()
    first = next((l for l in lines if l.startswith(("E ", "/")) and ("Error" in l or "assert" in l)), "")
    summary = next((l for l in reversed(lines) if " passed" in l or " failed" in l), "")
    print(f"{label}: applied=yes exit={r.returncode} | {summary.strip()} | {first.strip()[:230]}")
```

