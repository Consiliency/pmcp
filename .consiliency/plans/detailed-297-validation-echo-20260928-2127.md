# Detailed plan: describe validation errors from their structure, never their value — everywhere pmcp turns an exception into text

> **Revision 2 (2026-09-28), on main `7680445`** (re-fetched: `origin/main`
> is still `7680445`). Consiliency/pmcp#297, the prerequisite for piece B
> (`extra="forbid"`) of Consiliency/pmcp#236. The change is **embedded, not
> described**: the 28 blocks under *Verbatim bodies* are `git apply` patches
> against `origin/main` @ `7680445`, byte-identical to the verified code on
> the local-only branch `wip/297-code` @ `929f693` (never pushed; rev 1 was
> `19dac95`). *Embedding proof* extracts them from this file with its own
> extractor, `git apply --check`s them on a fresh `7680445` worktree,
> applies them and compares the whole tree with `929f693`.
>
> **What rev 2 changes** (rev 1 board on Consiliency/pmcp#314: codex
> BLOCKING, claude PARTIALLY AGREE, gemini no blocking finding; every finding
> is answered in *Rev 1 board findings — before/after*):
> - **B1 / N3: a validation error caught inside a handler bypassed the
>   renderer.** Rev 1 fixed the three `server.py` channels. `tasks_*` (and
>   every other handler arm) caught the exception themselves and rendered
>   `str(e)` / `_sanitize_error(e)` into the response and the audit-event
>   buffer that `gateway.health` exposes. So a downstream
>   `{"taskId": "t", "ttl": "<secret>"}` came back verbatim. Rev 2 fixes the
>   class: **every** place in `src/pmcp` (bar the operator CLI) that turns an
>   exception into text now goes through `exception_text(e)` /
>   `safe_exc_info(e)`. That covers 82 sinks in 19 modules, plus the three
>   shared renderers `sanitize_auth_diagnostic`, `_sanitize_error` and
>   `describe_exception`. A static test derived from the AST fails on any new
>   sink. A second dynamic sweep drives the **real** handlers with only
>   `ClientManager._send_request` replaced, at every downstream-payload
>   position the real parsers reject.
> - **N1: the log oracle now sees everything a handler could.** Raw
>   `LogRecord`s (msg, args, every `extra=` attribute, `exc_info` and
>   `stack_info` rendered in full), stdout/stderr (`capfd`), warnings
>   (`recwarn`), and the audit-event buffer. **S8:** every case runs once per
>   sentinel *family*: hex, letters only, a provider-token shape, spaced,
>   non-ASCII, digits. The seat's mutants S5–S8 are in the mutation run and
>   are all killed.
> - **N2: a custom error cannot fill a phrase from its `ctx`.** A constraint
>   (length, bound, pattern, allowed values) is now read from the gateway's
>   own schema node at the error's location, never from pydantic's `ctx`.
>   With no schema, the phrase names no constraint.
> - **N6:** a validation error described without its schema names only its
>   keyword or type (`fails its type constraint`). It never names a
>   constraint read from the wrong schema.
> - **N4, N5, N7** are decided under *Non-goals*. The accepted-value echo
>   follow-up is filed there with its exact site list.
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

## Rev 1 board findings — before/after

| Finding | Rev 1 (`19dac95`) | Rev 2 (`929f693`) | Proven by |
|---|---|---|---|
| **codex B1 / claude N3**: validation errors caught *inside* handlers bypass the renderer (`tasks_get` `handlers.py:6050-6060` on main: `error=str(e)` into the audit-event buffer, `errors=[self._sanitize_error(e)]` into the response). The reproduction is a downstream `{"taskId":"t","ttl":"SENTINELzq9x"}` parsed into `McpTaskInfo` (`client/manager.py:1689-1705`). | Only `server.py`'s arm rendered structurally. The downstream test replaced the whole handler (`_RaisingTools`), so it never reached the handler arms. The L9 row claimed the scope anyway. | *Design §7*. Every exception-to-text sink in `src/pmcp` bar the CLI goes through `exception_text` / `safe_exc_info`: 82 sinks in 19 modules (*Research → Every exception-to-text sink*), plus the three shared renderers. The L9 row is rewritten. | `test_no_exception_reaches_text_except_through_the_renderer` (static, AST: red on main with 82 findings). `test_no_downstream_value_reaches_a_response_log_or_audit[plain/scoped-audit]` drives the **real** `tasks_list/get/result/cancel` and `invoke` (task-augmented) with only `_send_request` replaced, at every payload key the real parser reads × every shape it rejects × 6 families, and checks the response, log, stderr, warnings, audit JSONL, audit-event buffer and a final `gateway.health`. It is red on main (`('gateway.tasks_list', 'createdAt', ...)`). `test_no_downstream_listing_value_reaches_the_log[tools/resources/prompts]` does the same for the listing parsers (red on main). Mutants M15–M22. |
| **claude N1**: the log oracle saw only pmcp-formatter output (the JSON formatter drops tracebacks, neither renders `extra=`), and not stderr or warnings. Seat mutants S5–S7 survived. | Formatter text only. | `_Tap` / `_record_text` scan both formatters, `msg`, `args`, `getMessage()`, **every** record attribute (so `extra=`), the full `exc_info` traceback via `traceback.format_exception` and `stack_info`, `capfd` stdout+stderr, `recwarn`, the scoped audit JSONL and the audit-event buffer. The pair differential also covers `extra=` attributes, stderr and warnings. | S5 (`extra=`), S6 (`print(..., file=sys.stderr)`), S7 (`warnings.warn`) are killed (*Mutation evidence*). |
| **claude N1 / S8**: a leak conditional on the value's shape (`isalpha()`) never fired on hex sentinels. | One family (hex). | Six families (`_FAMILIES`): hex, letters only, `sk-proj-…`, `Bearer …` with spaces, non-ASCII (`\u` escapes in the source), digits only. Each has two lengths. The forbidden set adds JSON-, `repr`- and `unicode_escape`-escaped windows. | S8 is killed, by the `alpha` family. |
| **claude N2**: `PydanticCustomError("literal_error", …, {"expected": value})` (or `string_too_short` with `min_length`) was rendered with the value, because a phrase read its constraint from `ctx`. | `_CONSTRAINT_CONTEXT` allowlisted `ctx` keys. | `_CONSTRAINED_PHRASES` maps each type to a **JSON Schema keyword**. The constraint is read from the gateway's own schema node at the error's location (`_schema_node_at`), with type checks, never from `ctx`. `errors()` is called with `include_context=False`. With no node, the phrase names no constraint. | `test_a_custom_error_cannot_fill_a_phrase_from_its_context[6 families]` (four colliding custom errors) and `test_every_constrained_phrase_names_a_schema_keyword`. Mutant M7 (constraint from `ctx`) is killed. |
| **claude N3**: the L9 row was inaccurate. | "reach L2+L3". | L9 now names the handler-local catches (B1 above). | *Research*. |
| **claude N4**: the SDK's legacy streamable-HTTP transport answers a malformed JSON-RPC envelope with `Validation error: {str(e)}` (`mcp/server/streamable_http.py:550`). | Silent. | Listed under *Not leaking to anyone but the sender* and *Non-goals*. It is SDK-owned, reaches only the sender, and no tool argument can reach it. | Seat measured it; read at `streamable_http.py:550`. |
| **claude N5**: `resources/read` / `prompts/get` echo an unknown URI or name. The SDK then logs it with a traceback (`mcp/shared/jsonrpc_dispatcher.py:754` `logger.exception("handler for %r raised")`). | Not mentioned. | **Follow-up, not this plan** (*Non-goals*). It is a lookup echo of a caller's routing identifier, not a validation error. The log line is written by the SDK for *any* exception a request handler raises, so the only way to silence it is for pmcp to raise `MCPError`, which changes the error code on the wire for two methods. That is a protocol-visible change of a different class. The unknown-*tool* log line was fixed in rev 1 because it is pmcp's own logger and #296 handed it to #297. | — |
| **claude N6**: a jsonschema error raised inside a handler was described against the *tool's* schema. | `describe_argument_error(e, tool.input_schema, …)` for any jsonschema error. | The arm describes only the tool's own **model** rejection against the tool's schema. Everything else goes through `exception_text`, whose schema-less form names only the keyword (`schema validation error: $.*: fails its type constraint`) and never a constraint. | `test_a_validation_error_raised_by_a_handler_is_described_not_echoed[jsonschema]`. |
| **claude N7** (nit, pre-existing): a model rejection returns `isError: false` with `{"error": true}`, while a gate rejection returns `isError: true`. | Unchanged. | Unchanged, and noted under *Non-goals*: changing `isError` for the `call_tool` arm changes every handler exception's wire shape. | — |
| **Coordinator: file the accepted-value echo follow-up** with its exact site list. | Listed as a non-goal. | *Non-goals → Follow-up issue text*, with every site `file:line` on main. | — |

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
| L9 (rev 2, corrected) | a `ValidationError` raised past argument validation (downstream data: `McpTaskInfo(...)` in `client/manager.py:1689-1705`, `McpTaskInfo.model_validate` `handlers.py:6141`; listing entries `client/manager.py:796/:843/:896`) is caught **inside** the handler or manager: `tasks_list/get/result/cancel` `handlers.py:6007-6016`, `:6050-6060`, `:6124-6134` put `str(e)` in the audit-event buffer (`gateway.health`'s `audit_events`) and `_sanitize_error(e)` (= `str(e)` through the redactor) in `errors`; the listing parsers log `describe_exception(e)` (= the same) | response, log, audit-event buffer / health | the downstream payload's values (a high-entropy value is sometimes masked by the redactor, a low-entropy one never is). *Every exception-to-text sink* below is the whole class |

Not leaking (measured or read):
- `ScopedAdvisorAudit.record_rejected_arguments` (`scoped_advisor_audit.py:303-351`, Consiliency/pmcp#296) records path + keyword only.
- The SDK's own params validation: `mcp/shared/jsonrpc_dispatcher.py:100-101` maps a pydantic `ValidationError` to `INVALID_PARAMS` with `data=""` ("no pydantic text on the wire"); `mcp 2.0.0`.
- CLI paths: no subcommand in `src/pmcp/cli.py` / `src/pmcp/cli_commands/` routes through a gateway argument model (`grep model_validate|ValidationError|jsonschema` is empty there); argparse echoes the operator's own terminal input only.
- `GatewayException.__str__` is its message only (`errors.py:141-156`); caller values live in `details`.
- No `GatewayArguments` model sets `hide_input_in_errors`; the only other custom validators with a value in their message are `PackagesPolicy._reject_version_qualified_entries` (`types.py:1158-1166`, operator config, not reachable from `tools/call`) and `McpTaskInfo._normalize_task_timestamp` (`types.py:533`, downstream data).

### Every exception-to-text sink (rev 2)

The rev 1 board showed that L1–L9 were instances, not the class. The class
is every place pmcp turns an exception that **may be a validation error**
into text: a response field, a log line (message, argument or traceback),
an audit or audit-event field. The guard test's scanner
(`_exception_sinks`, below and in the test) enumerates it from the AST of
every module in `src/pmcp` bar `cli.py`, `cli_commands/` and `__main__.py`.
Those three are the operator's own terminal, and none of them routes through
an argument model.

An *exception name* is one of these:
- an `except` clause's name, if the clause can catch a pydantic
  (`ValueError` → `Exception` → `BaseException`) or jsonschema (`_Error` →
  `Exception`) `ValidationError`;
- a name assigned `<task>.exception()`;
- a name narrowed by `isinstance(x, Exception|BaseException)`;
- an alias of any of these.

A *sink* is any use of such a name other than:
- a renderer (`exception_text`, `safe_exc_info`, `describe_exception`,
  `sanitize_auth_diagnostic`, `_sanitize_error`, the `describe_*` functions,
  `type`, `isinstance`);
- `raise`, a comparison or a truthiness test;
- an attribute that is not text-bearing (`e.code`, not `e.args`);
- one of five callees, each read and listed in the test with its reason.

Also a sink: any `exc_info=` that is not `safe_exc_info(...)`, any
`logger.exception(...)`, and any `traceback.format_*`/`print_*`. On main
`7680445` the scanner reports **82** (verbatim, `sinks_main.txt`):

```text
src/pmcp/client/manager.py:2680: Call uses exc
src/pmcp/client/manager.py:2680: Attribute uses exc
src/pmcp/client/manager.py:1135: FormattedValue uses result
src/pmcp/client/manager.py:1944: FormattedValue uses result
src/pmcp/client/manager.py:2680: Call uses exc
src/pmcp/client/manager.py:2680: Attribute uses exc
src/pmcp/client/manager.py:2680: traceback.format_exception
src/pmcp/config/guidance.py:178: FormattedValue uses e
src/pmcp/config/loader.py:282: FormattedValue uses e
src/pmcp/config/loader.py:319: FormattedValue uses e
src/pmcp/config/loader.py:357: FormattedValue uses exc
src/pmcp/config/loader.py:368: FormattedValue uses exc
src/pmcp/config/loader.py:1151: FormattedValue uses e
src/pmcp/manifest/code_patterns_loader.py:67: FormattedValue uses e
src/pmcp/manifest/environment.py:90: FormattedValue uses e
src/pmcp/manifest/environment.py:114: FormattedValue uses e
src/pmcp/manifest/installer.py:250: FormattedValue uses exc
src/pmcp/manifest/installer.py:253: FormattedValue uses exc
src/pmcp/manifest/installer.py:240: Call uses e
src/pmcp/manifest/installer.py:464: Call uses e
src/pmcp/manifest/installer.py:462: FormattedValue uses e
src/pmcp/manifest/installer.py:499: FormattedValue uses e
src/pmcp/manifest/installer.py:299: FormattedValue uses e
src/pmcp/manifest/installer.py:263: FormattedValue uses e
src/pmcp/manifest/installer.py:425: FormattedValue uses e
src/pmcp/manifest/installer.py:462: exc_info= not through safe_exc_info
src/pmcp/manifest/loader.py:783: FormattedValue uses exc
src/pmcp/manifest/loader.py:797: FormattedValue uses exc
src/pmcp/manifest/npm_resolver.py:501: FormattedValue uses exc
src/pmcp/manifest/package_identity.py:124: Call uses exc
src/pmcp/manifest/package_identity.py:206: Call uses exc
src/pmcp/manifest/refresher.py:92: FormattedValue uses e
src/pmcp/manifest/refresher.py:398: FormattedValue uses e
src/pmcp/manifest/refresher.py:496: FormattedValue uses e
src/pmcp/manifest/version_checker.py:1415: FormattedValue uses e
src/pmcp/manifest/version_checker.py:1457: FormattedValue uses e
src/pmcp/manifest/version_checker.py:1501: FormattedValue uses e
src/pmcp/manifest/version_checker.py:1555: FormattedValue uses e
src/pmcp/package_approvals.py:132: FormattedValue uses exc
src/pmcp/package_approvals.py:141: FormattedValue uses exc
src/pmcp/package_approvals.py:179: FormattedValue uses exc
src/pmcp/policy/policy.py:363: FormattedValue uses e
src/pmcp/policy/policy.py:422: FormattedValue uses e
src/pmcp/policy/policy.py:439: FormattedValue uses e
src/pmcp/policy/policy.py:436: FormattedValue uses e
src/pmcp/provision_gate.py:466: Call uses exc
src/pmcp/server.py:736: FormattedValue uses e
src/pmcp/server.py:1021: FormattedValue uses e
src/pmcp/server.py:342: Attribute uses e
src/pmcp/server.py:480: FormattedValue uses e
src/pmcp/server.py:510: Call uses e
src/pmcp/server.py:855: FormattedValue uses e
src/pmcp/subscriptions.py:194: logger.exception renders a traceback
src/pmcp/templates/code_snippets_loader.py:64: FormattedValue uses e
src/pmcp/tools/handlers.py:988: FormattedValue uses e
src/pmcp/tools/handlers.py:999: FormattedValue uses e
src/pmcp/tools/handlers.py:1800: Call uses e
src/pmcp/tools/handlers.py:2130: Call uses e
src/pmcp/tools/handlers.py:2138: Call uses e
src/pmcp/tools/handlers.py:4320: FormattedValue uses e
src/pmcp/tools/handlers.py:4576: Call uses exc
src/pmcp/tools/handlers.py:4581: Call uses exc
src/pmcp/tools/handlers.py:4785: Call uses exc
src/pmcp/tools/handlers.py:5056: FormattedValue uses e
src/pmcp/tools/handlers.py:5455: Call uses exc
src/pmcp/tools/handlers.py:5643: FormattedValue uses e
src/pmcp/tools/handlers.py:5739: FormattedValue uses e
src/pmcp/tools/handlers.py:5737: FormattedValue uses e
src/pmcp/tools/handlers.py:5783: FormattedValue uses e
src/pmcp/tools/handlers.py:6012: Call uses e
src/pmcp/tools/handlers.py:6056: Call uses e
src/pmcp/tools/handlers.py:6130: Call uses e
src/pmcp/tools/handlers.py:1878: FormattedValue uses e
src/pmcp/tools/handlers.py:1884: FormattedValue uses e
src/pmcp/tools/handlers.py:4238: FormattedValue uses e
src/pmcp/tools/handlers.py:4408: Call uses e
src/pmcp/tools/handlers.py:4413: Call uses e
src/pmcp/tools/handlers.py:5286: FormattedValue uses e
src/pmcp/tools/handlers.py:5643: exc_info= not through safe_exc_info
src/pmcp/tools/handlers.py:5737: exc_info= not through safe_exc_info
src/pmcp/trust_store.py:272: FormattedValue uses exc
src/pmcp/trust_store.py:300: FormattedValue uses exc
```

Plus the three shared renderers, which on main passed an exception's full
text on:
- `auth.py:589` `sanitize_auth_diagnostic(value)` → `str(value)`;
- `client/manager.py:133` `describe_exception` → `f"{type(leaf).__name__}: {leaf}"`;
- `handlers.py:920` `_sanitize_error(e)` → `sanitize_auth_diagnostic(e)`.

On `929f693` the scanner reports none. The five allowlisted callees:
- `parse_url_elicitation_error` (`auth.py:801`): it parses a JSON-RPC
  `-32042` payload out of `args[0]` and returns structured URLs, never the
  text;
- `_is_protocol_version_initialize_error`: a boolean predicate;
- `set_exception`: hands the exception to the awaiting caller, whose own
  `except` is scanned;
- `_warn_unparseable`: its body now uses `exception_text`;
- `record_rejected_arguments`: #296's audit, path and keyword only.

### Not leaking except to the sender (rev 2, N4)

`mcp/server/streamable_http.py:550` (SDK, legacy streamable-HTTP transport)
answers a malformed JSON-RPC *envelope* (e.g. `"params": "<string>"`) with
HTTP 400 `Validation error: {str(e)}`, pydantic text included. Only the
sender of that malformed envelope sees it, and no tool argument can reach
it, because any `params` object passes the envelope. It is SDK-owned. See
*Non-goals*.

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
case, hex family, sentinel windows as the oracle), tests @ `9c111c4`. `src/`
is byte-identical between `9c111c4` and `929f693`: the two commits after it
touch only `tests/test_argument_error_echo.py`. So this stands for the
shipped tree.

```text
== pmcp-297-ref 7680445 (tests @9c111c4, hex family)
plain {'gate': 248, 'model': 6} {('gate', 'response'): 225, ('model', 'log'): 6, ('model', 'response'): 6}
audited {'gate': 248, 'model': 6} {('gate', 'response'): 225, ('model', 'audit'): 1, ('model', 'log'): 6, ('model', 'response'): 6}
== pmcp-297-code 9c111c4 (tests @9c111c4, hex family)
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
validated id. Since rev 2 this is belt and braces: the arm's own sinks
(`_sanitize_error`, `exception_text`, `safe_exc_info`) would keep the value
out of it anyway. That is why mutant M10 is killed on behaviour (the handler
returns instead of raising), not on a leak.

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
- **sentinel families** (rev 2, S8): every case runs once per family: hex,
  letters only, `sk-proj-…`, `Bearer …` with spaces, non-ASCII, digits
  only. Each family has two lengths.
- **oracle** (rev 2, N1): per case,
  - (a) it was rejected (an `isError` gate result, or the `{"error": true}`
    payload), so there is no vacuous pass;
  - (b) no 12-character window of the sentinel (raw, JSON-, `repr`- and
    `unicode_escape`-escaped), and no sha256/sha1/md5 of it, is in any
    channel `_Tap` watches. The channels are: the response; every log
    record at DEBUG, as both pmcp formatters render it plus `msg`, `args`,
    `getMessage()`, every attribute (so `extra=`) and the full `exc_info`
    and `stack_info` tracebacks; stdout/stderr (`capfd`); warnings
    (`recwarn`); the scoped audit JSONL; and the audit-event buffer;
  - (c) with two sentinels of different length, the response, the
    time-normalised log (with `extra=` attributes), the streams, the
    warnings, the audit records and the event shapes are identical;
  - (d) for the hex family, the rejection names the path (and, for
    validators, the reason).

  (b)-(c) run before (d), so main goes red on the leak, not on wording.
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
- **static** (rev 2): the scanner above as a test, plus a test per rule
  that it fires and one that the renderers pass.
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

## Changes

These are the patches under *Verbatim bodies* (`git diff 7680445 929f693 -- <file>`):
28 files, +2632 / −352. One concern, rendering validation errors from
their structure, is applied at every sink. The bounded-plan threshold of
about 8 files is exceeded on purpose. 16 of the source files carry only the
mechanical §7 substitution plus an import, and splitting them into another
plan would leave the class open between the two merges.

### `src/pmcp/argument_errors.py` (create, +561)
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

### `src/pmcp/server.py` (modify, +80 / −13)
- the gate return renders `describe_schema_error` (rev 1);
- the `call_tool` arm (rev 2): the model rejection is described against the
  tool schema; everything else goes through `exception_text`; the audit
  records `audit.rejection` for a model rejection; the unknown-tool log line
  is fixed text;
- three startup/shutdown sinks go through `exception_text`.

### `src/pmcp/tools/handlers.py` (modify, +49 / −28)
- `provision_status` validates before its `try` (rev 1);
- 26 sinks go through `exception_text` / `safe_exc_info` (rev 2). These are
  the `tasks_*` audit `error=` and `errors`, `auth_connect`, provisioning,
  handoff, the registry and the update probe.

### `src/pmcp/client/manager.py` (modify, +11 / −7)
- `describe_exception` leaves go through `exception_text`;
- `_connect_all_unlocked` and `_fetch_server_listings` messages go through
  `exception_text`;
- the cancellation-unwind traceback goes through `safe_traceback_text`.

### `src/pmcp/auth.py` (modify, +5 / −1)
- `sanitize_auth_diagnostic` renders an exception with `exception_text`.

### `src/pmcp/scoped_advisor_audit.py` (modify, +20 / −68, rev 1)
- the path helpers moved to `argument_errors`;
- `record_rejected_arguments` accepts a pydantic error.

### `src/pmcp/types.py` (modify, +9 / −9, rev 1)
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

### `tests/test_argument_error_echo.py` (create, +1708)
- rev 1: the argument sweep, handler-past-the-gate, and renderer unit tests;
- rev 2 additions:
  - `_FAMILIES`, `_Tap` / `_Observed` / `_record_text`;
  - the N2 custom-error test, the wrapper test and the static sink guard
    (with its rule tests);
  - `test_every_decorated_baseline_passes_the_gate`;
  - the downstream task sweep and the listing sweep.

### `tests/test_scoped_advisor_audit.py` (modify, +91 / −176, rev 1)
- the foreign-line exclusions are removed;
- `_META_REJECTION` and `_assert_against` are added.

### `tests/test_gateway_tool_schemas.py` (modify, +7 / −3, rev 1)
- the three fragments use the new wording.

## Documentation impact
- `CHANGELOG.md` — `[Unreleased]` → `### Fixed`, first entry — modify:
  - rev 1's fix, the wording change and the audit disposition;
  - rev 2's sentence that the rule holds wherever pmcp renders an exception
    (responses, logs and tracebacks, the audit-event buffer, `tasks_*`
    `errors`), including downstream data and the operator's own config
    files.
- `README.md` (scoped-advisor audit paragraph) — modify (rev 1).

## Dependencies & order
Apply all 28 patches together; they are one `git apply`.
`pmcp.argument_errors` imports no `pmcp` module at load time.
`_declared_names` imports `pmcp.types` lazily, so there are no import
cycles: `pmcp.types`, `auth`, `client.manager` and the others import it at
module level, and `python -c "import pmcp.server, pmcp.cli"` succeeds. No
migration, no config.

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

Red on main: copy the three test files at `929f693` onto a clean `7680445`
tree and run the same three modules. The result is under *Acceptance
criteria*.

## Acceptance criteria — measured this session (on `929f693`, red on `7680445`)

- [x] **Caller arguments** (the rev 1 axes). 254 cases × 6 sentinel
  families × 2 lengths, on the plain and the scoped-audit server.
  - Nothing leaks into the response, the log (raw records, `extra=`,
    tracebacks), stdout/stderr, warnings, the audit JSONL or the audit-event
    buffer, and the pair differential holds.
  - Command:
    `pytest tests/test_argument_error_echo.py::test_no_rejected_argument_value_reaches_a_response_log_or_audit`.
  - 2 passed. Red on main: `AssertionError: ('hex', '$.auth_mode:type-object', ...)`.
- [x] **Downstream data through the real handlers** (B1).
  - Tasks: every rejected payload key and shape, through all five task
    paths. `test_no_downstream_value_reaches_a_response_log_or_audit`:
    2 passed. Red on main: `('gateway.tasks_list', 'createdAt', ...)`.
  - Listings: `test_no_downstream_listing_value_reaches_the_log`: 3 passed.
    Red on main: `('tools', 'description', 'object', 'alpha', ...)`.
- [x] **Every exception-to-text sink goes through the renderer.**
  `test_no_exception_reaches_text_except_through_the_renderer` passes. On
  main it fails, and the scanner lists 82 sinks. Each scanner rule fires:
  `test_the_sink_scanner_flags_each_shape`, 10 passed.
- [x] **A custom error cannot fill a phrase from its `ctx`** (N2):
  `test_a_custom_error_cannot_fill_a_phrase_from_its_context`, 6 passed.
  A group leaf and a wrapper that embeds a validation error are described,
  not echoed: `test_describe_exception_renders_a_grouped_validation_error_structurally`
  (6 passed) and `test_exception_text_describes_a_wrapper_that_embeds_a_validation_error`.
- [x] **Gates and the full suite are clean.** `ruff check src/ tests/`,
  `ruff format --check src/ tests/` and `mypy src/` pass, and
  `pytest -m 'not live and not slow'` is green (lines under *Full suite and
  gates*).

Red on main, with the three test files @ `929f693` on a clean `7680445`
(`pytest ... -q --tb=line`; identical lines collapsed as `(xN)`):

```text
tests/test_argument_error_echo.py:690: AssertionError: ('hex', '$.auth_mode:type-object', _Observed(response="Input validation error: {'Sqfee3b693849c460ce4e728Zx': 'Sqfee3b...ne of ['api_key', 'url_e
tests/test_argument_error_echo.py:690: AssertionError: ('hex', '$.auth_mode:type-object', _Observed(response="Input validation error: {'Sqfee3b693849c460ce4e728Zx': 'Sqfee3b...ped_advisor_audit.v1","s
E   AssertionError: ('$.job_id:type-object', '[2026-09-28T22:53:17] [ERROR] provision_status handler failed: 1 validation error for Provis...ce4e728Zx\'}]}, input_type=dict]\
ERROR    pmcp.tools.handlers:handlers.py:5643 provision_status handler failed: 1 validation error for ProvisionStatusInput
tests/test_argument_error_echo.py:736: AssertionError: ('$.job_id:type-object', '[2026-09-28T22:53:17] [ERROR] provision_status handler failed: 1 validation error for Provis...ce4e728Zx\'}]}, input_ty
tests/test_argument_error_echo.py:817: AssertionError: ('hex', _Observed(response='{"error": true, "message": "2 validation errors for McpTaskInfo\
tests/test_argument_error_echo.py:817: AssertionError: ('hex', _Observed(response='{"error": true, "message": "3 validation errors for InvokeInput\
tests/test_argument_error_echo.py:817: AssertionError: ('hex', _Observed(response='{"error": true, "message": "{\'Sqfee3b693849c460ce4e728Zx\': \'Sqfee3b693849c460ce4e728Zx\...=None
(x5) tests/test_argument_error_echo.py:869: ModuleNotFoundError: No module named 'pmcp.argument_errors'
tests/test_argument_error_echo.py:883: ModuleNotFoundError: No module named 'pmcp.argument_errors'
tests/test_argument_error_echo.py:913: ModuleNotFoundError: No module named 'pmcp.argument_errors'
tests/test_argument_error_echo.py:946: ModuleNotFoundError: No module named 'pmcp.argument_errors'
(x6) tests/test_argument_error_echo.py:997: ModuleNotFoundError: No module named 'pmcp.argument_errors'
tests/test_argument_error_echo.py:1037: ModuleNotFoundError: No module named 'pmcp.argument_errors'
tests/test_argument_error_echo.py:1048: ModuleNotFoundError: No module named 'pmcp.argument_errors'
(x6) tests/test_argument_error_echo.py:1092: assert 'validation error for McpTaskInfo: $.ttl: must be an integer' in "ExceptionGroup(1 sub-exception): ValidationError: 1 validation error for McpTaskInfo\nt
tests/test_argument_error_echo.py:1313: AssertionError: manager.py:2680: Call uses exc
(x2) tests/test_argument_error_echo.py:1570: AssertionError: ('gateway.tasks_list', 'createdAt', '{
tests/test_argument_error_echo.py:1685: AssertionError: ('tools', 'description', 'object', 'alpha', _Observed(response='', log='[1969-12-31T19:00:00] [WARNING] [svc] Skipping...=None
tests/test_argument_error_echo.py:1685: AssertionError: ('resources', 'annotations', 'string', 'spaced', _Observed(response='', log='[1969-12-31T19:00:00] [WARNING] [svc] Ski...=None
tests/test_argument_error_echo.py:1685: AssertionError: ('prompts', 'annotations', 'string', 'spaced', _Observed(response='', log='[1969-12-31T19:00:00] [WARNING] [svc] Skipp...=None
tests/test_scoped_advisor_audit.py:1692: AssertionError: ('top-level correlation', ['[1969-12-31T19:00:00] [ERROR] Tool execution error: 1 validation error for InvokeInput
tests/test_scoped_advisor_audit.py:1692: AssertionError: ('E6 correlation', ['[1969-12-31T19:00:00] [ERROR] Tool execution error: 1 validation error for InvokeInput
(x2) tests/test_scoped_advisor_audit.py:1692: AssertionError: ("('gateway.caller_marker_a', 'gateway.caller_marker_bbbbbbb') correlation", ['[1969-12-31T19:00:00] [ERROR] Tool exec...evel": "ERROR", "logge
tests/test_scoped_advisor_audit.py:1692: AssertionError: ('real correlation', ['[1969-12-31T19:00:00] [ERROR] Tool execution error: 1 validation error for InvokeInput
tests/test_scoped_advisor_audit.py:2086: AssertionError: [2026-09-28T22:53:35] [INFO] Loaded policy from /tmp/pytest-of-viperjuice/pytest-5478/test_the_formerly_excluded_log0/policy.json
tests/test_gateway_tool_schemas.py:468: AssertionError: Input validation error: '' should be non-empty
tests/test_gateway_tool_schemas.py:468: AssertionError: Input validation error: 'short' is too short
tests/test_gateway_tool_schemas.py:468: AssertionError: Input validation error: 5 is less than the minimum of 100
43 failed, 405 passed in 18.79s
```

The `ModuleNotFoundError` lines are the renderer's own unit tests, which
import `pmcp.argument_errors`. Every sweep fails on a leak, not on an
import.

Green (patched): `448 passed in 49.02s` for the three modules.

## Mutation evidence

`mutants.py` (below) on a worktree of `929f693`. It applies each mutant,
with the anchor required to occur exactly once, runs the three test modules
with `-x --tb=line`, restores the file **from a copy saved before the
mutation** and `cmp`-checks it. `git status --short | wc -l` was `0` after
the run. Every mutant applied and went red; the named reason is the first
failing assertion. M1–M14 are rev 1's, re-anchored where rev 2 moved the
code (M7 now takes the constraint from `ctx`, and M10 also restores rev 1's
fallback `job_id`). M15–M22 are new for B1. S5–S8 are the rev 1 board
seat's surviving mutants.

```text
M1 gate renders e.message: applied=yes exit=1 | 1 failed, 27 passed in 0.59s | E   AssertionError: ('hex', '$.auth_mode:type-object', _Observed(response="Input validation error: {'Sqfee3b693849c460ce4e728Zx': 'Sqfee3b...ne of ['api_key', 'url_elicitation']", log='', raw_log='', streams='', warnings='', audit
M2 except arm returns str(e): applied=yes exit=1 | 1 failed, 27 passed in 1.14s | E   AssertionError: ('hex', "validator:('InvokeInput', '_validate_correlation_id', 'run_correlation_id')", _Observed(response="1 validatio...=None
M3 except arm logs str(e): applied=yes exit=1 | 1 failed, 27 passed in 1.13s | E   AssertionError: ('hex', "validator:('InvokeInput', '_validate_correlation_id', 'run_correlation_id')", _Observed(response='Invalid arg...=None
M4 model rejection recorded as invocation: applied=yes exit=1 | 1 failed, 28 passed in 7.65s | E   AssertionError: ('hex', "validator:('InvokeInput', '_reject_partial_scoped_correlation', '')", _Observed(response='Invalid arguments: ..."sequence":222,"source_reference_hash":null,"terminal_status":"failure","timestamp":17906
M5 model loc not redacted: applied=yes exit=1 | 1 failed, 40 passed in 14.29s | E   AssertionError: assert '$.env.sk-KEY...be an integer' == '$.env.*: must be an integer'
M6 schema path not redacted: applied=yes exit=1 | 1 failed, 38 passed in 14.15s | E   AssertionError: assert '$.env.sk-KEY... type integer' == '$.env.*: mus... type integer'
M7 phrase constraint from pydantic ctx: applied=yes exit=1 | 1 failed, 41 passed in 14.95s | E   assert '$.literal: m...: is required' == '$.literal: m...: is required'
M8 missing-required reads an instance key: applied=yes exit=1 | 1 failed, 27 passed in 0.51s | E   AssertionError: ('hex', 'required:server_name', _Observed(response='Input validation error: $.extra_Sqfee3b693849c460ce4e728Zx: is required', log='', raw_log='', streams='', warnings='', audit=[], raw_audit='', events=''))
M9 validator back to ValueError with value: applied=yes exit=1 | 1 failed, 27 passed in 1.09s | E   AssertionError: Invalid arguments: $.run_correlation_id: is invalid
M10 provision_status validates inside its try: applied=yes exit=1 | 1 failed, 29 passed in 14.57s | E   AssertionError: ('required:job_id', None)
M11 unknown tool name logged: applied=yes exit=1 | 1 failed, 55 passed in 16.88s | E   AssertionError: server.py:525: FormattedValue uses e
M12 type phrase is e.message: applied=yes exit=1 | 1 failed, 27 passed in 0.67s | E   AssertionError: ('hex', '$.consent_acknowledged:type-string', _Observed(response="Input validation error: $.consent_acknowledged: 'Sqf...0ce4e728Zx' is not of type 'boolean'", log='', raw_log='', streams='', warnings='', audit
M13 model phrase is pydantic msg: applied=yes exit=1 | 1 failed, 30 passed in 16.87s | E   AssertionError: {'error': True, 'message': '2 validation errors for McpTaskInfo: $.task_id: Input should be a valid string; $.created_at: Input should be a valid number'}
M14 audit model path from input: applied=yes exit=1 | 1 failed, 28 passed in 13.26s | E   AssertionError: ('hex', "validator:('InvokeInput', '_validate_correlation_id', 'run_correlation_id')", _Observed(response='Invalid arg..._advisor_audit.v1","sequence":218,"terminal_status":"invalid_arguments","timestamp":17906
M15 sanitize_auth_diagnostic uses str(value): applied=yes exit=1 | 1 failed, 67 passed in 23.07s | E   AssertionError: ('gateway.tasks_list', 'createdAt', '{
M16 describe_exception leaf uses str(leaf): applied=yes exit=1 | 1 failed, 49 passed in 15.18s | E   assert 'validation error for McpTaskInfo: $.ttl: must be an integer' in "ExceptionGroup(1 sub-exception): ValidationError: 1 validation error for McpTaskInfo\nttl\n  Input should be a valid ...ErNBOfkoxABIALAEbZa'}, input_type
M17 exception_text skips validation errors: applied=yes exit=1 | 1 failed, 30 passed in 23.82s | E   AssertionError: ('hex', _Observed(response='{"error": true, "message": "2 validation errors for McpTaskInfo\
M18 safe_exc_info always returns the error: applied=yes exit=1 | 1 failed, 48 passed in 21.07s | E   pydantic_core._pydantic_core.ValidationError: 1 validation error for McpTaskInfo
M19 tasks_get response uses str(e): applied=yes exit=1 | 1 failed, 55 passed in 15.15s | E   AssertionError: handlers.py:6079: Call uses e
M20 tasks_get audit buffer uses str(e): applied=yes exit=1 | 1 failed, 55 passed in 34.15s | E   AssertionError: handlers.py:6077: Call uses e
M21 exception_text ignores an embedded validation error: applied=yes exit=1 | 1 failed, 48 passed in 20.78s | E   pydantic_core._pydantic_core.ValidationError: 1 validation error for McpTaskInfo
M22 installer crash message uses raw exc (static guard): applied=yes exit=1 | 1 failed, 55 passed in 26.30s | E   AssertionError: installer.py:256: FormattedValue uses exc
S5 value in a log extra= field: applied=yes exit=1 | 1 failed, 27 passed in 2.39s | E   AssertionError: ('hex', "validator:('InvokeInput', '_validate_correlation_id', 'run_correlation_id')", _Observed(response='Invalid arg...=None
S6 arguments printed to stderr: applied=yes exit=1 | 1 failed, 27 passed in 1.74s | E   AssertionError: ('hex', "validator:('InvokeInput', '_validate_correlation_id', 'run_correlation_id')", _Observed(response='Invalid arg...b693849c460ce4e728Zx', 7, {'Sqfee3b693849c460ce4e728Zx': None}]}}}
S7 arguments in warnings.warn: applied=yes exit=1 | 1 failed, 27 passed in 1.49s | E   AssertionError: ('hex', "validator:('InvokeInput', '_validate_correlation_id', 'run_correlation_id')", _Observed(response='Invalid arg...ne}]}}} @ /mnt/workspace/worktrees/viperjuice/pmcp-297-mut/src/pmcp/server.py:515", audit
S8 echo only isalpha values: applied=yes exit=1 | 1 failed, 27 passed in 1.48s | E   AssertionError: ('alpha', '$.auth_mode:enum', _Observed(response='Input validation error: $.auth_mode: must be one of ["api_key", "url...ation"] x AEaXjvOLkjBUhTKsJtXJSKWjIq', log='', raw_log='', streams='', warnings='', audit
```

The static guard sits earlier in the test file than the dynamic sweeps, so
under `-x` it is what stops M11, M19, M20 and M22. Rerun without it
(`NO_STATIC=1`, which deselects
`test_no_exception_reaches_text_except_through_the_renderer`), the dynamic
sweeps kill M11, M19 and M20 on their own:

```text
M11 unknown tool name logged: applied=yes exit=1 | 1 failed, 219 passed, 1 deselected in 54.51s | E   AssertionError: ("('gateway.caller_marker_a', 'gateway.caller_marker_bbbbbbb') correlation", ['[1969-12-31T19:00:00] [ERROR] Tool exec...evel": "ERROR", "logger": "pmcp.server", "msg": "Tool execution error: Unknown tool: gate
M19 tasks_get response uses str(e): applied=yes exit=1 | 1 failed, 66 passed, 1 deselected in 19.43s | E   AssertionError: ('gateway.tasks_get', 'createdAt', 'string', 'hex', _Observed(response='{
M20 tasks_get audit buffer uses str(e): applied=yes exit=1 | 1 failed, 66 passed, 1 deselected in 21.30s | E   AssertionError: ('gateway.tasks_get', 'createdAt', 'string', 'spaced', _Observed(response='{
M22 installer crash message uses raw exc (static guard): applied=yes exit=0 | 447 passed, 1 deselected in 75.33s (0:01:15) | 
```

M22, the installer's monitor-crash message, **survives** this pass, as
expected. No sweep drives a crashing install task, and the static guard is
the only check on a sink that no dynamic path reaches; that is what M22
shows. An earlier run of this pass also saw M22 go red, on a pair
differential in the downstream sweep. That was flakiness, not detection: a
`ResourceWarning` from an earlier test's unclosed audit file was finalised
by the collector inside a pair. The fix is in `929f693`: `_Tap` runs
`gc.collect()`, and every test shuts its servers down. After it, three
parallel runs of the three modules under `-W error::ResourceWarning` were
green (`447 passed, 1 deselected`, three times).

Reasons, by channel:
- **response**: M1, M2, M8, M12, M17 (sentinel window) and S8 (the `alpha`
  family);
- **log**: M3, S5 (`extra=`), S6 (stderr), S7 (warnings);
- **audit**: M4, M14;
- **downstream, through the real handlers**: M15, and M19/M20 in the second
  pass;
- **behaviour**: M10 (the handler returns instead of raising; rev 2's
  renderers already keep the value out of that arm's log);
- **wording**: M9 and M13;
- **unit tests**: M5 and M6 (the path unit tests), M7 (the N2 custom-error
  test), M16 (the group test), M18 and M21 (the wrapper test);
- **static guard**: M11 (also #296's pair differential), M19, M20, M22.

## Non-goals

- **Echo of accepted values: a follow-up issue** (coordinator, rev 1
  board). Its text, ready to file:

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
- **The operator's CLI** (`cli.py`, `cli_commands/`): the operator reads
  their own input there.
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
- **Exceptions from SDK code that pmcp never renders.** The SDK's own
  loggers (e.g. `mcp/client/*` parsing a downstream frame) log with their
  own formatting. pmcp's handlers on the root logger see those records, and
  the sweeps would catch any that fired on their paths, but no sweep drives
  a malformed downstream JSON-RPC frame through the SDK transport.
- **The `initialize` path** (`ServerStatus(...)` built from a downstream's
  `initialize` result) is covered by the static guard and by
  `describe_exception`, not by a dynamic sweep, because it needs a
  connected process.
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

The patches were generated with `git diff 7680445 929f693 -- <file>` and embedded. Then, from **this file**, on a fresh worktree `$WORKTREE_ROOT/pmcp-297-proof` of re-fetched `origin/main` (still `7680445`), with `<scratch>` the session scratch dir:

```text
$ git -C <proof worktree> rev-parse --short HEAD
7680445
<scratch>/emb2/x2.py: 25 lines
extractor self-extract: identical
<scratch>/emb2/p/CHANGELOG.md.patch: 12 lines
<scratch>/emb2/p/README.md.patch: 19 lines
<scratch>/emb2/p/src_pmcp_argument_errors.py.patch: 567 lines
<scratch>/emb2/p/src_pmcp_auth.py.patch: 24 lines
<scratch>/emb2/p/src_pmcp_client_manager.py.patch: 64 lines
<scratch>/emb2/p/src_pmcp_config_guidance.py.patch: 23 lines
<scratch>/emb2/p/src_pmcp_config_loader.py.patch: 59 lines
<scratch>/emb2/p/src_pmcp_manifest_code_patterns_loader.py.patch: 21 lines
<scratch>/emb2/p/src_pmcp_manifest_environment.py.patch: 30 lines
<scratch>/emb2/p/src_pmcp_manifest_installer.py.patch: 89 lines
<scratch>/emb2/p/src_pmcp_manifest_loader.py.patch: 30 lines
<scratch>/emb2/p/src_pmcp_manifest_npm_resolver.py.patch: 23 lines
<scratch>/emb2/p/src_pmcp_manifest_package_identity.py.patch: 32 lines
<scratch>/emb2/p/src_pmcp_manifest_refresher.py.patch: 39 lines
<scratch>/emb2/p/src_pmcp_manifest_version_checker.py.patch: 48 lines
<scratch>/emb2/p/src_pmcp_package_approvals.py.patch: 41 lines
<scratch>/emb2/p/src_pmcp_policy_policy.py.patch: 52 lines
<scratch>/emb2/p/src_pmcp_provision_gate.py.patch: 21 lines
<scratch>/emb2/p/src_pmcp_scoped_advisor_audit.py.patch: 123 lines
<scratch>/emb2/p/src_pmcp_server.py.patch: 171 lines
<scratch>/emb2/p/src_pmcp_subscriptions.py.patch: 27 lines
<scratch>/emb2/p/src_pmcp_templates_code_snippets_loader.py.patch: 21 lines
<scratch>/emb2/p/src_pmcp_tools_handlers.py.patch: 252 lines
<scratch>/emb2/p/src_pmcp_trust_store.py.patch: 34 lines
<scratch>/emb2/p/src_pmcp_types.py.patch: 50 lines
<scratch>/emb2/p/tests_test_argument_error_echo.py.patch: 1714 lines
<scratch>/emb2/p/tests_test_gateway_tool_schemas.py.patch: 28 lines
<scratch>/emb2/p/tests_test_scoped_advisor_audit.py.patch: 402 lines
$ git apply --check <scratch>/emb2/p/*.patch
check: ok
applied
cmp CHANGELOG.md: identical to wip/297-code@929f693
cmp README.md: identical to wip/297-code@929f693
cmp src/pmcp/argument_errors.py: identical to wip/297-code@929f693
cmp src/pmcp/auth.py: identical to wip/297-code@929f693
cmp src/pmcp/client/manager.py: identical to wip/297-code@929f693
cmp src/pmcp/config/guidance.py: identical to wip/297-code@929f693
cmp src/pmcp/config/loader.py: identical to wip/297-code@929f693
cmp src/pmcp/manifest/code_patterns_loader.py: identical to wip/297-code@929f693
cmp src/pmcp/manifest/environment.py: identical to wip/297-code@929f693
cmp src/pmcp/manifest/installer.py: identical to wip/297-code@929f693
cmp src/pmcp/manifest/loader.py: identical to wip/297-code@929f693
cmp src/pmcp/manifest/npm_resolver.py: identical to wip/297-code@929f693
cmp src/pmcp/manifest/package_identity.py: identical to wip/297-code@929f693
cmp src/pmcp/manifest/refresher.py: identical to wip/297-code@929f693
cmp src/pmcp/manifest/version_checker.py: identical to wip/297-code@929f693
cmp src/pmcp/package_approvals.py: identical to wip/297-code@929f693
cmp src/pmcp/policy/policy.py: identical to wip/297-code@929f693
cmp src/pmcp/provision_gate.py: identical to wip/297-code@929f693
cmp src/pmcp/scoped_advisor_audit.py: identical to wip/297-code@929f693
cmp src/pmcp/server.py: identical to wip/297-code@929f693
cmp src/pmcp/subscriptions.py: identical to wip/297-code@929f693
cmp src/pmcp/templates/code_snippets_loader.py: identical to wip/297-code@929f693
cmp src/pmcp/tools/handlers.py: identical to wip/297-code@929f693
cmp src/pmcp/trust_store.py: identical to wip/297-code@929f693
cmp src/pmcp/types.py: identical to wip/297-code@929f693
cmp tests/test_argument_error_echo.py: identical to wip/297-code@929f693
cmp tests/test_gateway_tool_schemas.py: identical to wip/297-code@929f693
cmp tests/test_scoped_advisor_audit.py: identical to wip/297-code@929f693
proof tree == 929f693 (whole tree)
```

## Full suite and gates

On `wip/297-code` @ `929f693`, with `npm_config_cache`, `npm_config_store_dir` and
`pnpm_config_store_dir` unset (dev0 is a team host):

```text
$ pytest -m 'not live and not slow' -q
4773 passed, 3 skipped, 80 deselected in 811.22s (0:13:31)
EXIT=0
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
448 passed in 54.75s
```

## Verbatim bodies

### How to apply (and the extractor)

From a fresh worktree of `origin/main` @ `7680445`:

```bash
PLAN=.consiliency/plans/detailed-297-validation-echo-20260928-2127.md   # read from branch plan/297-validation-echo
X=<scratch>/extract_plan_block.py        # bootstrap it: see *Extractor* below
while read f; do
  python3 $X $PLAN "### Patch — \`$f\`" "<scratch>/$(echo $f | tr / _).patch"
done <<'LIST'
CHANGELOG.md
README.md
src/pmcp/argument_errors.py
src/pmcp/auth.py
src/pmcp/client/manager.py
src/pmcp/config/guidance.py
src/pmcp/config/loader.py
src/pmcp/manifest/code_patterns_loader.py
src/pmcp/manifest/environment.py
src/pmcp/manifest/installer.py
src/pmcp/manifest/loader.py
src/pmcp/manifest/npm_resolver.py
src/pmcp/manifest/package_identity.py
src/pmcp/manifest/refresher.py
src/pmcp/manifest/version_checker.py
src/pmcp/package_approvals.py
src/pmcp/policy/policy.py
src/pmcp/provision_gate.py
src/pmcp/scoped_advisor_audit.py
src/pmcp/server.py
src/pmcp/subscriptions.py
src/pmcp/templates/code_snippets_loader.py
src/pmcp/tools/handlers.py
src/pmcp/trust_store.py
src/pmcp/types.py
tests/test_argument_error_echo.py
tests/test_gateway_tool_schemas.py
tests/test_scoped_advisor_audit.py
LIST
git apply --check <scratch>/*.patch && git apply <scratch>/*.patch
```

The patches are fenced with **four** backticks, and the extractor closes on
the same fence string. Blank context lines carry one leading space. The test
source is ASCII, with non-ASCII test strings written as `\u` escapes. The two
doc patches carry pre-existing non-ASCII context.

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
index 6ee53d7..b390c72 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -383,6 +383,7 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 
 
 ### Fixed
+- **A rejected gateway-tool argument no longer echoes its value into the response, the log or the scoped audit (Consiliency/pmcp#297).** Both validation layers rendered the value that failed: the input-schema gate returned jsonschema's message (`Input validation error: 'Bearer sk-…' is not of type 'object', 'null'`), and an argument model's pydantic error — returned as `str(e)[:400]` and logged as `Tool execution error: …` — carried `input_value=…` (the full value for `InvokeInput`'s correlation-ID charset check and a non-dict `meta`, a truncated repr of the whole argument dict for the all-or-none correlation check). Rejections are now described from their structure, as `<JSON path>: <reason>` — e.g. `Input validation error: $.options: must be of type object or null`, `Invalid arguments: $.run_correlation_id: correlation IDs may contain only alphanumerics and ._:-` — where the reason is a fixed phrase filled only from the tool's own schema or model (a type, a length, a pattern, the allowed values) and a key the caller chose is shown as `*`. The log line is `Tool execution error: invalid arguments for <tool>: <same description>`. **The same rule now holds wherever pmcp turns an exception into text** — tool responses, log lines and tracebacks, the in-memory audit-event buffer `gateway.health` exposes, and error fields such as `gateway.tasks_*` `errors`: a pydantic or jsonschema validation error (or an exception whose text embeds one) reads `N validation error(s) for <Model>: $.<path>: <reason>`, and a traceback whose chain holds one is not logged. This covers downstream data too: a task-capable server answering `tasks/get` with `{"taskId": "t", "ttl": "<secret>"}` used to get that value echoed back in `errors` and stored in the audit-event buffer. Operators see the same form for their own config files (policy, trust store, package approvals, `.mcp.json`): the failing field and why, not the value. **Wording change:** the text after `Input validation error: ` is no longer jsonschema's message; a client matching on phrases such as `is not of type` or `is too short` must match the new form. A call rejected by the tool's argument model (not the gate) is now recorded in the scoped audit as an `audit.rejection` like a gate rejection, with `rejected_argument_validator: null`, instead of an `audit.invocation` `failure` that copied its unvalidated correlation fields. An unregistered tool name is no longer written to the log (`Tool execution error: unknown gateway tool`); the response still names it. `gateway.provision_status` validates its arguments before its catch-all, which logged a traceback of the validation error.
 - **`tools/call` input-schema rejections are now recorded in the scoped-advisor audit, without argument values (Consiliency/pmcp#296).** A call the transport gate rejects used to return `Input validation error: …` before the audit was reached, so an operator saw no attempt at all. It is now written as a new `audit.rejection` event (not an `audit.invocation`: nothing was invoked, and a reader that correlates invocations to a run skips it) with the tool name, `terminal_status: "invalid_arguments"`, `rejected_argument_path`, the failing location as a JSON array (a key the schema declares, an array index, or `null` for a key the caller chose, since that key can itself be a secret), and `rejected_argument_validator`, the failing JSON Schema keyword (`type`, `pattern`, `required`, …). The record never contains the validation message, the rejected value, correlation IDs, or any digest of the arguments. The capability stays `scoped_advisor_audit.v1`; readers that dispatch on `event` are unaffected. Policy is now judged **before** the schema: a call to a policy-blocked gateway tool is refused with "Gateway tool blocked by policy" and recorded `denied` whatever its arguments, instead of getting an `Input validation error` that described the blocked tool's schema. If the audit sink has failed, a malformed call now gets "Scoped advisor audit channel failed" like every other call, instead of its validation error. The response to a rejected call from an allowed tool is unchanged. An `audit.invocation` record now reads nothing the schema gate did not vouch for: a call refused by policy, or made to an unregistered name, is recorded `denied` with every argument-derived field (`run_correlation_id`, `seat_correlation_id`, `downstream_tool_id`, `evidence_label_digest`, `source_reference_hash`) `null`, a result digest that no longer covers the caller's tool name, and a `gateway_tool_digest` of the registered name (for an unregistered name, of nothing) — previously a correlation-shaped value or a public URL anywhere in such a call's arguments was copied or hashed into the audit. Every other invocation record reads only the top-level arguments the tool's schema declares, so a correlation-shaped key a tool does not declare (e.g. `run_correlation_id` on `gateway.describe`) is no longer recorded; `gateway.invoke` declares every field the record reads, so its records are unchanged.
 - **`sanitize_auth_diagnostic` does its keyword and URL-punctuation work in linear time.** The keyword rule now runs through `pmcp.keyword_matcher` (the same matches as the regular expression it replaces, pinned by a seeded corpus), and trailing punctuation is split off a URL in one pass. Output is unchanged.
 - **Gateway tool `inputSchema`s are now derived from the pydantic models that validate the arguments, so the two can no longer disagree (Consiliency/pmcp#236).** Constraints the models always enforced are now advertised and enforced at the transport gate — `minLength` on identifiers, `submit_feedback.title` 8–160 chars, bounds on `tasks_result.options` — so those rejections now come back as an `isError` tool result reading `Input validation error: …` instead of an `{"error": true}` payload. `gateway.invoke` now advertises `task`, `trace_context` and `_meta`; `gateway.tasks_*` advertise `requestor_context`; `tasks_result.options` gains `timeout_ms`. Optional arguments are advertised as `type: [X, "null"]` and the transport gate now accepts an explicit `null` for them, as the handlers always did; 28 optional arguments (e.g. `catalog_search.query`, `invoke.options`, `auth_connect.credential`) were previously rejected at the gate when sent as `null`. The gate does not apply pydantic's lax coercion: values such as `1` for a boolean or `"5"` for an integer on the newly advertised `invoke.task` fields (`enabled`, `ttl`, `poll_interval`), which were previously accepted and coerced, are now rejected with `Input validation error: 1 is not of type 'boolean'`. `invoke.task.ttl` now advertises its range on both sides, so `1e20`, `-1e20` and `float(±2**63)` are rejected at the gate, and so is any integer outside [−2^63+1, 2^63−1] (including `-2**63` itself), which the handler previously accepted. `invoke.evidence_label_digest` now also advertises its exact length (64), so a digest with a trailing newline is rejected at the gate instead of by the handler. Inputs the gate now rejects that previously reached the handler were recorded in the scoped-advisor audit as `failure`; they are now recorded as `audit.rejection` events with `terminal_status: "invalid_arguments"` (see the Consiliency/pmcp#296 entry above). Unknown keys are still ignored in this release — see the following entry once B lands. Argument descriptions agents already saw are unchanged, except `gateway.update_server.force`, which now describes the task-aware behaviour; 19 previously undescribed arguments gain a description.
````

### Patch — `README.md`

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

### Patch — `src/pmcp/argument_errors.py`

````diff
diff --git a/src/pmcp/argument_errors.py b/src/pmcp/argument_errors.py
new file mode 100644
index 0000000..6fdf622
--- /dev/null
+++ b/src/pmcp/argument_errors.py
@@ -0,0 +1,561 @@
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
+def _is_validation_error(error: BaseException) -> bool:
+    return isinstance(error, (ValidationError, jsonschema.ValidationError))
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
+    without constraints, since the schema is not known here (rev 2, N6)."""
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
+def safe_traceback_text(error: BaseException) -> str:
+    """The formatted traceback, or a one-line stand-in when the chain holds a
+    validation error (see :func:`safe_exc_info`)."""
+    if safe_exc_info(error) is None:
+        return f"(traceback withheld: {exception_text(error)})"
+    return "".join(traceback.format_exception(type(error), error, error.__traceback__))
````

### Patch — `src/pmcp/auth.py`

````diff
diff --git a/src/pmcp/auth.py b/src/pmcp/auth.py
index ccb5e36..c0a4fec 100644
--- a/src/pmcp/auth.py
+++ b/src/pmcp/auth.py
@@ -19,6 +19,7 @@ import aiohttp
 import jwt
 from jwt import PyJWKSet
 
+from pmcp.argument_errors import exception_text
 from pmcp.keyword_matcher import key_start_pattern, redact_keyword_values
 from pmcp.redaction_additive import redact_additive
 from pmcp.types import AuthChallengeInfo, AuthMetadataInfo, UrlElicitationInfo
@@ -586,7 +587,10 @@ def sanitize_auth_diagnostic(value: object, *, max_length: int | None = 400) ->
     can only replace more of it with the marker (Consiliency/pmcp#234). The
     cut is taken last, as before.
     """
-    text = redact_additive(_sanitize_base(str(value)))
+    # An exception goes through `exception_text`: a validation error's own
+    # text carries the rejected value (Consiliency/pmcp#297).
+    raw = exception_text(value) if isinstance(value, BaseException) else str(value)
+    text = redact_additive(_sanitize_base(raw))
     return text if max_length is None else text[:max_length]
 
 
````

### Patch — `src/pmcp/client/manager.py`

````diff
diff --git a/src/pmcp/client/manager.py b/src/pmcp/client/manager.py
index ba8068a..18daccd 100644
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -11,7 +11,6 @@ from pathlib import Path
 import random
 import re
 import signal
-import traceback
 import string
 import time
 from collections import deque
@@ -26,6 +25,7 @@ from mcp.client.sse import sse_client
 from mcp.client.streamable_http import streamable_http_client
 from mcp.shared.message import SessionMessage
 
+from pmcp.argument_errors import exception_text, safe_traceback_text
 from pmcp.auth import sanitize_auth_diagnostic
 from pmcp.config.loader import make_tool_id
 from pmcp.env_store import sanitized_subprocess_env
@@ -130,7 +130,9 @@ def describe_exception(exc: BaseException) -> str:
 
     shown = leaves[:_MAX_DESCRIBED_LEAVES]
     rendered = "; ".join(
-        f"{type(leaf).__name__}: {leaf}" if str(leaf) else type(leaf).__name__
+        f"{type(leaf).__name__}: {exception_text(leaf)}"
+        if str(leaf)
+        else type(leaf).__name__
         for leaf in shown
     )
     if len(leaves) > len(shown):
@@ -1132,7 +1134,9 @@ class ClientManager:
         errors: list[str] = []
         for config, result in zip(configs, results):
             if isinstance(result, Exception):
-                error_msg = f"Failed to connect to {config.name}: {result}"
+                error_msg = (
+                    f"Failed to connect to {config.name}: {exception_text(result)}"
+                )
                 logger.error(error_msg)
                 errors.append(error_msg)
 
@@ -1941,7 +1945,9 @@ class ClientManager:
             ("prompts", listing_results[2]),
         ):
             if isinstance(result, BaseException):
-                logger.debug(f"Server {name} doesn't support {kind}: {result}")
+                logger.debug(
+                    f"Server {name} doesn't support {kind}: {exception_text(result)}"
+                )
                 listings[kind] = None
             else:
                 listings[kind] = result
@@ -2676,9 +2682,7 @@ class ClientManager:
                 # clean. Redaction here is best-effort defence in depth
                 # (SECURITY.md), and it cannot be applied to text the logging
                 # framework formats on its own.
-                traceback_text = "".join(
-                    traceback.format_exception(type(exc), exc, exc.__traceback__)
-                )
+                traceback_text = safe_traceback_text(exc)
                 logger.warning(
                     f"[{name}] remote transport failed to unwind while "
                     f"escalating our caller's cancellation: "
````

### Patch — `src/pmcp/config/guidance.py`

````diff
diff --git a/src/pmcp/config/guidance.py b/src/pmcp/config/guidance.py
index 58976f6..ffe9695 100644
--- a/src/pmcp/config/guidance.py
+++ b/src/pmcp/config/guidance.py
@@ -11,6 +11,7 @@ from typing import Literal
 
 import yaml
 from pydantic import BaseModel, Field
+from pmcp.argument_errors import exception_text
 
 
 class GuidanceLayers(BaseModel):
@@ -175,7 +176,9 @@ def load_guidance_config(config_path: Path | None = None) -> GuidanceConfig:
         return GuidanceConfig(**data["guidance"])
     except Exception as e:
         # If config is invalid, log warning and use defaults
-        print(f"Warning: Failed to load guidance config from {config_path}: {e}")
+        print(
+            f"Warning: Failed to load guidance config from {config_path}: {exception_text(e)}"
+        )
         print("Using default guidance config (minimal mode)")
         return GuidanceConfig()
 
````

### Patch — `src/pmcp/config/loader.py`

````diff
diff --git a/src/pmcp/config/loader.py b/src/pmcp/config/loader.py
index c71e7f8..8fb335e 100644
--- a/src/pmcp/config/loader.py
+++ b/src/pmcp/config/loader.py
@@ -14,6 +14,7 @@ from enum import Enum
 from pathlib import Path
 from typing import TYPE_CHECKING, Any, Literal, cast
 
+from pmcp.argument_errors import exception_text
 from pmcp.types import (
     ConfigSourceInfo,
     ConfigSourceName,
@@ -279,7 +280,7 @@ def parse_json_file(file_path: Path) -> McpConfigFile | None:
             return None
         content = file_path.read_bytes()
     except Exception as e:
-        logger.warning(f"Failed to parse config file {file_path}: {e}")
+        logger.warning(f"Failed to parse config file {file_path}: {exception_text(e)}")
         return None
     return parse_config_bytes(content, file_path)
 
@@ -316,7 +317,7 @@ def parse_config_bytes(content: bytes, file_path: Path) -> McpConfigFile | None:
 
         return McpConfigFile.model_validate(data)
     except Exception as e:
-        logger.warning(f"Failed to parse config file {file_path}: {e}")
+        logger.warning(f"Failed to parse config file {file_path}: {exception_text(e)}")
         return None
 
 
@@ -354,7 +355,7 @@ def _read_config_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
     try:
         content = path.read_bytes()
     except Exception as exc:
-        return None, f"invalid_json: {exc}"
+        return None, f"invalid_json: {exception_text(exc)}"
     return _config_object_from_bytes(content)
 
 
@@ -365,7 +366,7 @@ def _config_object_from_bytes(
     try:
         data = json.loads(content)
     except Exception as exc:
-        return None, f"invalid_json: {exc}"
+        return None, f"invalid_json: {exception_text(exc)}"
     if not isinstance(data, dict):
         return None, "config_root_not_object"
     return data, None
@@ -1148,7 +1149,9 @@ def load_configs(
 
         manifest_servers = load_manifest().servers
     except Exception as e:
-        logger.debug(f"Manifest defaults unavailable during config load: {e}")
+        logger.debug(
+            f"Manifest defaults unavailable during config load: {exception_text(e)}"
+        )
 
     def build_resolved_config(
         name: str,
````

### Patch — `src/pmcp/manifest/code_patterns_loader.py`

````diff
diff --git a/src/pmcp/manifest/code_patterns_loader.py b/src/pmcp/manifest/code_patterns_loader.py
index b89f67a..9b584aa 100644
--- a/src/pmcp/manifest/code_patterns_loader.py
+++ b/src/pmcp/manifest/code_patterns_loader.py
@@ -10,6 +10,7 @@ from pathlib import Path
 from typing import Any
 
 import yaml
+from pmcp.argument_errors import exception_text
 
 
 class CodePatternsLoader:
@@ -64,7 +65,7 @@ class CodePatternsLoader:
         except Exception as e:
             # If loading fails, log warning but continue with empty patterns
             print(
-                f"Warning: Failed to load code patterns from {self._patterns_path}: {e}"
+                f"Warning: Failed to load code patterns from {self._patterns_path}: {exception_text(e)}"
             )
 
     def get_hint_for_tool(
````

### Patch — `src/pmcp/manifest/environment.py`

````diff
diff --git a/src/pmcp/manifest/environment.py b/src/pmcp/manifest/environment.py
index 613559f..6e0bab0 100644
--- a/src/pmcp/manifest/environment.py
+++ b/src/pmcp/manifest/environment.py
@@ -9,6 +9,7 @@ import platform
 import shutil
 from dataclasses import dataclass, field
 from typing import Literal
+from pmcp.argument_errors import exception_text
 
 logger = logging.getLogger(__name__)
 
@@ -87,7 +88,7 @@ async def check_cli(name: str, check_command: list[str]) -> CLIInfo | None:
         logger.debug(f"Timeout checking CLI: {name}")
         return CLIInfo(name=name, path=path)
     except Exception as e:
-        logger.debug(f"Error checking CLI {name}: {e}")
+        logger.debug(f"Error checking CLI {name}: {exception_text(e)}")
         return None
 
 
@@ -111,7 +112,7 @@ async def get_cli_help(
         logger.debug(f"Timeout getting help for: {name}")
         return None
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
@@ -12,6 +12,7 @@ from dataclasses import dataclass, field
 from pathlib import Path
 from typing import Literal
 
+from pmcp.argument_errors import exception_text, safe_exc_info
 from pmcp.env_store import resolve_scope_path, sanitized_subprocess_env
 from pmcp.manifest.environment import Platform
 from pmcp.manifest.loader import (
@@ -237,7 +238,7 @@ class JobManager:
 
         except Exception as e:
             job.status = "failed"
-            job.error = str(e)[:300]
+            job.error = exception_text(e)[:300]
             logger.error(f"Install job {job_id} failed: {job.error}")
 
         return job_id
@@ -247,10 +248,12 @@ class JobManager:
         try:
             exc = task.exception()
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
                 if (
                     job.status != "server_ready"
@@ -260,7 +263,7 @@ class JobManager:
                     try:
                         job.process.kill()
                     except Exception as e:
-                        logger.debug(f"task cleanup error: {e}")
+                        logger.debug(f"task cleanup error: {exception_text(e)}")
         except asyncio.CancelledError:
             # Task was cancelled, not an error
             pass
@@ -296,7 +299,7 @@ class JobManager:
                 line = await stream.readline()
                 return (name, line)
             except Exception as e:
-                logger.debug(f"stream reader error: {e}")
+                logger.debug(f"stream reader error: {exception_text(e)}")
                 return (name, None)
 
         try:
@@ -422,7 +425,7 @@ class JobManager:
                                         return
                                 except Exception as e:
                                     logger.warning(
-                                        f"Install {job.id}: Error in server detection: {e}"
+                                        f"Install {job.id}: Error in server detection: {exception_text(e)}"
                                     )
 
                 except asyncio.CancelledError:
@@ -459,9 +462,12 @@ class JobManager:
             job.error = "Installation cancelled"
 
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
             await self._safe_terminate_process(process, job.id, force=True)
 
@@ -496,7 +502,9 @@ class JobManager:
                     except asyncio.TimeoutError:
                         logger.error(f"Install {job_id}: Process won't die!")
         except Exception as e:
-            logger.warning(f"Install {job_id}: Error terminating process: {e}")
+            logger.warning(
+                f"Install {job_id}: Error terminating process: {exception_text(e)}"
+            )
 
     def _parse_progress(self, line: str, current: int) -> int:
         """Try to parse progress percentage from output line."""
````

### Patch — `src/pmcp/manifest/loader.py`

````diff
diff --git a/src/pmcp/manifest/loader.py b/src/pmcp/manifest/loader.py
index 9837e82..40f5b48 100644
--- a/src/pmcp/manifest/loader.py
+++ b/src/pmcp/manifest/loader.py
@@ -13,6 +13,7 @@ from typing import Any, Literal, cast
 
 import yaml
 
+from pmcp.argument_errors import exception_text
 from pmcp.project_consent import log_refusal, read_and_gate
 
 logger = logging.getLogger(__name__)
@@ -780,7 +781,7 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
                 servers[name] = _parse_server_config(name, server_data)
             except Exception as exc:
                 logger.warning(
-                    f"Skipping invalid server entry '{name}' in overlay {path}: {exc}"
+                    f"Skipping invalid server entry '{name}' in overlay {path}: {exception_text(exc)}"
                 )
     elif raw_servers:
         logger.warning(f"Skipping 'servers' in overlay {path}: not a mapping")
@@ -794,7 +795,7 @@ def _parse_overlay_document(path: Path, content: bytes) -> _OverlayDocument:
             except Exception as exc:
                 logger.warning(
                     f"Skipping invalid cli_alternative '{name}' in overlay "
-                    f"{path}: {exc}"
+                    f"{path}: {exception_text(exc)}"
                 )
     elif raw_clis:
         logger.warning(f"Skipping 'cli_alternatives' in overlay {path}: not a mapping")
````

### Patch — `src/pmcp/manifest/npm_resolver.py`

````diff
diff --git a/src/pmcp/manifest/npm_resolver.py b/src/pmcp/manifest/npm_resolver.py
index 3382a1e..41631d6 100644
--- a/src/pmcp/manifest/npm_resolver.py
+++ b/src/pmcp/manifest/npm_resolver.py
@@ -62,6 +62,7 @@ from collections.abc import Mapping
 from dataclasses import dataclass
 from pathlib import Path
 from typing import Literal
+from pmcp.argument_errors import exception_text
 
 logger = logging.getLogger(__name__)
 
@@ -498,7 +499,9 @@ class NpmResolver:
             proc.stdin.flush()
         except (BrokenPipeError, ValueError, OSError) as exc:
             self._terminate()
-            return _refused(f"npm resolver child died before the query: {exc}")
+            return _refused(
+                f"npm resolver child died before the query: {exception_text(exc)}"
+            )
 
         line = reader.read(_QUERY_TIMEOUT)
         if line is None:
````

### Patch — `src/pmcp/manifest/package_identity.py`

````diff
diff --git a/src/pmcp/manifest/package_identity.py b/src/pmcp/manifest/package_identity.py
index 29cf145..962b614 100644
--- a/src/pmcp/manifest/package_identity.py
+++ b/src/pmcp/manifest/package_identity.py
@@ -40,6 +40,7 @@ from urllib.request import HTTPRedirectHandler, Request, build_opener
 
 import semver
 
+from pmcp.argument_errors import exception_text
 from pmcp.validation import is_valid_package_name
 
 logger = logging.getLogger(__name__)
@@ -121,7 +122,7 @@ def _fetch_packument(name: str) -> dict[str, Any] | None:
             body = response.read(_MAX_PACKUMENT_BYTES)
         data = json.loads(body.decode("utf-8"))
     except Exception as exc:
-        logger.debug("npm packument fetch failed for %r: %s", name, exc)
+        logger.debug("npm packument fetch failed for %r: %s", name, exception_text(exc))
         return None
     return data if isinstance(data, dict) else None
 
@@ -203,7 +204,9 @@ def resolve_package_identity(spec: str) -> PackageIdentity | None:
     try:
         packument = _fetch_packument(name)
     except Exception as exc:  # pragma: no cover - the fetch handles its own
-        logger.debug("npm packument lookup raised for %r: %s", name, exc)
+        logger.debug(
+            "npm packument lookup raised for %r: %s", name, exception_text(exc)
+        )
         return None
     if not isinstance(packument, dict):
         return None
````

### Patch — `src/pmcp/manifest/refresher.py`

````diff
diff --git a/src/pmcp/manifest/refresher.py b/src/pmcp/manifest/refresher.py
index b9bf8ff..b9f45fc 100644
--- a/src/pmcp/manifest/refresher.py
+++ b/src/pmcp/manifest/refresher.py
@@ -16,6 +16,7 @@ from pathlib import Path
 
 import yaml
 
+from pmcp.argument_errors import exception_text
 from pmcp.manifest.loader import (
     credential_lookup_keys,
     load_manifest,
@@ -89,7 +90,7 @@ def load_descriptions_cache(cache_path: Path | None = None) -> DescriptionsCache
         )
 
     except Exception as e:
-        logger.warning(f"Failed to load descriptions cache: {e}")
+        logger.warning(f"Failed to load descriptions cache: {exception_text(e)}")
         return None
 
 
@@ -395,7 +396,7 @@ async def refresh_server(
                 )
 
     except Exception as e:
-        logger.error(f"Failed to refresh {server_name}: {e}")
+        logger.error(f"Failed to refresh {server_name}: {exception_text(e)}")
         return None
 
 
@@ -493,7 +494,7 @@ async def refresh_all(
                 # Keep existing if refresh failed
                 return name, existing
         except Exception as e:
-            logger.error(f"Error refreshing {name}: {e}")
+            logger.error(f"Error refreshing {name}: {exception_text(e)}")
             if existing:
                 return name, existing
         return name, None
````

### Patch — `src/pmcp/manifest/version_checker.py`

````diff
diff --git a/src/pmcp/manifest/version_checker.py b/src/pmcp/manifest/version_checker.py
index 19e71e6..7419b34 100644
--- a/src/pmcp/manifest/version_checker.py
+++ b/src/pmcp/manifest/version_checker.py
@@ -13,6 +13,7 @@ import aiohttp
 from packaging.version import InvalidVersion, Version
 from semver import Version as SemverVersion
 
+from pmcp.argument_errors import exception_text
 from pmcp import __version__
 from pmcp.manifest.npm_resolver import get_resolver
 
@@ -1412,7 +1413,7 @@ async def get_npm_version(package_name: str, timeout: float = 10.0) -> str | Non
         logger.debug(f"npm lookup timeout for {package_name}")
         return None
     except Exception as e:
-        logger.debug(f"npm lookup error for {package_name}: {e}")
+        logger.debug(f"npm lookup error for {package_name}: {exception_text(e)}")
         return None
 
 
@@ -1454,7 +1455,7 @@ async def get_pypi_version(package_name: str, timeout: float = 10.0) -> str | No
         logger.debug(f"PyPI lookup timeout for {package_name}")
         return None
     except Exception as e:
-        logger.debug(f"PyPI lookup error for {package_name}: {e}")
+        logger.debug(f"PyPI lookup error for {package_name}: {exception_text(e)}")
         return None
 
 
@@ -1498,7 +1499,7 @@ async def get_cargo_version(crate_name: str, timeout: float = 10.0) -> str | Non
         logger.debug(f"crates.io lookup timeout for {crate_name}")
         return None
     except Exception as e:
-        logger.debug(f"crates.io lookup error for {crate_name}: {e}")
+        logger.debug(f"crates.io lookup error for {crate_name}: {exception_text(e)}")
         return None
 
 
@@ -1552,7 +1553,7 @@ async def get_docker_version(image_name: str, timeout: float = 10.0) -> str | No
         logger.debug(f"Docker Hub lookup timeout for {image_name}")
         return None
     except Exception as e:
-        logger.debug(f"Docker Hub lookup error for {image_name}: {e}")
+        logger.debug(f"Docker Hub lookup error for {image_name}: {exception_text(e)}")
         return None
 
 
````

### Patch — `src/pmcp/package_approvals.py`

````diff
diff --git a/src/pmcp/package_approvals.py b/src/pmcp/package_approvals.py
index 68d7685..6b90dd0 100644
--- a/src/pmcp/package_approvals.py
+++ b/src/pmcp/package_approvals.py
@@ -41,6 +41,7 @@ from pathlib import Path
 from typing import TYPE_CHECKING, Any
 
 
+from pmcp.argument_errors import exception_text
 from pmcp.trust_store import TrustStoreError, trust_store_path
 from pmcp.validation import (
     is_valid_package_name,
@@ -129,7 +130,9 @@ def _decode(entry: Any) -> PackageApproval:
     try:
         _require_identity_fields(registry, name, version)
     except ValueError as exc:
-        raise PackageApprovalError(f"Invalid package approval entry: {exc}") from exc
+        raise PackageApprovalError(
+            f"Invalid package approval entry: {exception_text(exc)}"
+        ) from exc
     if integrity is not None and not isinstance(integrity, str):
         raise PackageApprovalError("Package approval integrity is not a string")
     if decision not in DECISIONS:
@@ -138,7 +141,7 @@ def _decode(entry: Any) -> PackageApproval:
         parsed_at = datetime.fromisoformat(str(recorded_at))
     except ValueError as exc:
         raise PackageApprovalError(
-            f"Unparseable package approval timestamp: {exc}"
+            f"Unparseable package approval timestamp: {exception_text(exc)}"
         ) from exc
 
     return PackageApproval(
@@ -176,7 +179,7 @@ def _read_store_and_stale(
         data = json.loads(raw)
     except ValueError as exc:
         raise PackageApprovalError(
-            f"Cannot parse package approvals {path}: {exc}"
+            f"Cannot parse package approvals {path}: {exception_text(exc)}"
         ) from exc
     if not isinstance(data, dict):
         raise PackageApprovalError(f"Package approvals {path} is not a JSON object")
````

### Patch — `src/pmcp/policy/policy.py`

````diff
diff --git a/src/pmcp/policy/policy.py b/src/pmcp/policy/policy.py
index ec5ee42..530e974 100644
--- a/src/pmcp/policy/policy.py
+++ b/src/pmcp/policy/policy.py
@@ -14,6 +14,7 @@ from typing import TYPE_CHECKING, Any, Literal
 
 import yaml
 
+from pmcp.argument_errors import exception_text
 from pmcp.project_consent import log_refusal, read_and_gate
 from pmcp.types import (
     GatewayPolicy,
@@ -360,7 +361,7 @@ class PolicyManager:
         except Exception as e:
             if fatal:
                 raise ValueError(
-                    f"Failed to load explicit policy {policy_path}: {e}"
+                    f"Failed to load explicit policy {policy_path}: {exception_text(e)}"
                 ) from e
             self._warn_unparseable(policy_path, e)
             return None
@@ -380,7 +381,7 @@ class PolicyManager:
             else "No policy is in effect: the gateway is running unrestricted."
         )
         logger.warning(
-            f"Could not parse policy file {policy_path}: {error}. {consequence}"
+            f"Could not parse policy file {policy_path}: {exception_text(error)}. {consequence}"
         )
 
     def _parse_policy(
@@ -419,7 +420,7 @@ class PolicyManager:
         except Exception as e:
             if fatal:
                 raise ValueError(
-                    f"Failed to load explicit policy {policy_path}: {e}"
+                    f"Failed to load explicit policy {policy_path}: {exception_text(e)}"
                 ) from e
             self._warn_unparseable(policy_path, e)
             return None
@@ -433,10 +434,10 @@ class PolicyManager:
         except Exception as e:
             if fatal:
                 raise ValueError(
-                    f"Failed to load explicit policy {policy_path}: {e}"
+                    f"Failed to load explicit policy {policy_path}: {exception_text(e)}"
                 ) from e
             raise ValueError(
-                f"Invalid policy file {policy_path}: {e}. "
+                f"Invalid policy file {policy_path}: {exception_text(e)}. "
                 "Refusing to start rather than fall back to an unrestricted gateway."
             ) from e
 
````

### Patch — `src/pmcp/provision_gate.py`

````diff
diff --git a/src/pmcp/provision_gate.py b/src/pmcp/provision_gate.py
index ee8bbd4..2fb99f4 100644
--- a/src/pmcp/provision_gate.py
+++ b/src/pmcp/provision_gate.py
@@ -40,6 +40,7 @@ from dataclasses import dataclass
 from pathlib import PurePath
 from typing import TYPE_CHECKING, Literal
 
+from pmcp.argument_errors import exception_text
 from pmcp.package_approvals import is_package_approved
 from pmcp.validation import (
     is_valid_package_name,
@@ -463,7 +464,7 @@ def evaluate_provision(
         logger.warning(
             "Provisioning gate failed closed for %r: %s",
             getattr(server_config, "name", None),
-            exc,
+            exception_text(exc),
         )
         return _deny(
             "not_approved", _fallback_remedy(server_config, identity), identity
````

### Patch — `src/pmcp/scoped_advisor_audit.py`

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

### Patch — `src/pmcp/server.py`

````diff
diff --git a/src/pmcp/server.py b/src/pmcp/server.py
index 0a6ef28..dce397b 100644
--- a/src/pmcp/server.py
+++ b/src/pmcp/server.py
@@ -12,6 +12,7 @@ from pathlib import Path
 from typing import Any, Literal
 
 import jsonschema
+import pydantic
 from mcp.server import Server
 from mcp.server.context import ServerRequestContext
 from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
@@ -37,6 +38,11 @@ from mcp.types import (
     Tool,
 )
 
+from pmcp.argument_errors import (
+    describe_model_error,
+    describe_schema_error,
+    exception_text,
+)
 from pmcp.client.manager import ClientManager
 from pmcp.config.guidance import GuidanceConfig, load_guidance_config
 from pmcp.config.loader import (
@@ -69,7 +75,11 @@ from pmcp.scoped_advisor_audit import (
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
@@ -335,11 +345,15 @@ class GatewayServer:
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
@@ -477,7 +491,41 @@ class GatewayServer:
                     )
                 ]
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
                     failure_status = (
                         "denied"
@@ -485,12 +533,24 @@ class GatewayServer:
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
@@ -507,7 +567,12 @@ class GatewayServer:
                 return [
                     TextContent(
                         type="text",
-                        text=json.dumps({"error": True, "message": str(e)[:400]}),
+                        text=json.dumps(
+                            {
+                                "error": True,
+                                "message": described[:400],
+                            }
+                        ),
                     )
                 ]
 
@@ -733,7 +798,9 @@ class GatewayServer:
             manifest = load_manifest()
             manifest_servers = manifest.servers
         except Exception as e:
-            logger.warning(f"Failed to load manifest startup configs: {e}")
+            logger.warning(
+                f"Failed to load manifest startup configs: {exception_text(e)}"
+            )
 
         enabled_auto_start = load_enabled_auto_start(
             project_root=self._project_root,
@@ -852,7 +919,7 @@ class GatewayServer:
                     f"Cached descriptions for {len(self._descriptions_cache.servers)} servers"
                 )
             except Exception as e:
-                logger.warning(f"Failed to auto-generate cache: {e}")
+                logger.warning(f"Failed to auto-generate cache: {exception_text(e)}")
 
         logger.debug("Capability summary:\n%s", self._capability_summary)
 
@@ -1018,7 +1085,7 @@ class GatewayServer:
         except asyncio.TimeoutError:
             logger.warning("Shutdown timed out, forcing disconnect")
         except Exception as e:
-            logger.error(f"Error during shutdown: {e}")
+            logger.error(f"Error during shutdown: {exception_text(e)}")
         finally:
             # Always release singleton lock
             release_singleton_lock()
````

### Patch — `src/pmcp/subscriptions.py`

````diff
diff --git a/src/pmcp/subscriptions.py b/src/pmcp/subscriptions.py
index f05ef50..007b47b 100644
--- a/src/pmcp/subscriptions.py
+++ b/src/pmcp/subscriptions.py
@@ -48,6 +48,8 @@ from mcp.shared.subscriptions import (
     ToolsListChanged,
 )
 
+from pmcp.argument_errors import safe_exc_info
+
 __all__ = ["CatalogEventSink", "BusCatalogEventSink"]
 
 logger = logging.getLogger(__name__)
@@ -187,8 +189,11 @@ class BusCatalogEventSink:
     async def _publish(self, kind: _CatalogEventClass) -> None:
         try:
             await self._bus.publish(kind())
-        except Exception:
+        except Exception as exc:
             # Isolate a raising bus from the drain, matching
             # `InMemorySubscriptionBus.publish`'s own listener-isolation
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
index 00e741c..37322a5 100644
--- a/src/pmcp/templates/code_snippets_loader.py
+++ b/src/pmcp/templates/code_snippets_loader.py
@@ -11,6 +11,7 @@ from pathlib import Path
 from typing import TYPE_CHECKING
 
 import yaml
+from pmcp.argument_errors import exception_text
 
 if TYPE_CHECKING:
     from pmcp.types import ToolInfo
@@ -61,7 +62,7 @@ class CodeSnippetsLoader:
         except Exception as e:
             # If loading fails, log warning but continue with empty snippets
             print(
-                f"Warning: Failed to load code snippets from {self._templates_path}: {e}"
+                f"Warning: Failed to load code snippets from {self._templates_path}: {exception_text(e)}"
             )
 
     def get_snippet_for_tool(
````

### Patch — `src/pmcp/tools/handlers.py`

````diff
diff --git a/src/pmcp/tools/handlers.py b/src/pmcp/tools/handlers.py
index 45a956c..a9ab37f 100644
--- a/src/pmcp/tools/handlers.py
+++ b/src/pmcp/tools/handlers.py
@@ -20,6 +20,7 @@ import anyio
 from dotenv import load_dotenv
 from mcp.types import Tool
 from pydantic import BaseModel
+from pmcp.argument_errors import exception_text, safe_exc_info
 from pmcp import __version__ as PMCP_VERSION
 from pmcp.auth import (
     UNVERIFIED_URL_CAVEAT,
@@ -985,7 +986,7 @@ class GatewayTools:
                 data = json.load(f)
             return {k: v for k, v in data.items() if isinstance(k, str)}
         except Exception as e:
-            logger.warning(f"Could not load provisioned registry: {e}")
+            logger.warning(f"Could not load provisioned registry: {exception_text(e)}")
             return {}
 
     def _save_provisioned_registry(self) -> None:
@@ -996,7 +997,7 @@ class GatewayTools:
             with open(path, "w") as f:
                 json.dump(self._provisioned_registry, f)
         except Exception as e:
-            logger.warning(f"Could not save provisioned registry: {e}")
+            logger.warning(f"Could not save provisioned registry: {exception_text(e)}")
 
     def _register_provisioned_server(
         self, server_name: str, env_var: str | None
@@ -1797,7 +1798,7 @@ class GatewayTools:
                     next_step=url_elicitations[0].next_step,
                     feedback_hint=self._feedback_hint(),
                 )
-            auth_challenge = self._auth_challenge_from_message(str(e))
+            auth_challenge = self._auth_challenge_from_message(exception_text(e))
             auth_state = "none"
             if auth_challenge:
                 auth_state = (
@@ -1875,13 +1876,17 @@ class GatewayTools:
                 manifest = load_manifest()
                 manifest_servers = manifest.servers
             except Exception as e:
-                logger.warning(f"Failed to load manifest startup configs: {e}")
+                logger.warning(
+                    f"Failed to load manifest startup configs: {exception_text(e)}"
+                )
 
             provisioned: dict[str, str | None] = {}
             try:
                 provisioned = self._load_provisioned_registry()
             except Exception as e:
-                logger.warning(f"Failed to restore provisioned servers: {e}")
+                logger.warning(
+                    f"Failed to restore provisioned servers: {exception_text(e)}"
+                )
 
             enabled_auto_start = load_enabled_auto_start(
                 project_root=self._project_root,
@@ -2127,7 +2132,7 @@ class GatewayTools:
                 action="refresh",
                 outcome="failure",
                 started_at=audit_started_at,
-                error=str(e),
+                error=exception_text(e),
             )
             return RefreshOutput(
                 ok=False,
@@ -2135,7 +2140,7 @@ class GatewayTools:
                 servers_online=0,
                 tools_indexed=0,
                 revision_id="error",
-                errors=[str(e)],
+                errors=[exception_text(e)],
                 pending_requests_seen=pending_seen,
                 pending_requests_cancelled=pending_cancelled,
                 mcp_tasks_seen=active_tasks_seen,
@@ -4235,7 +4240,9 @@ class GatewayTools:
                         url_elicitations=url_elicitations,
                         feedback_hint=self._feedback_hint(),
                     )
-                logger.error(f"Failed to connect remote server {server_name}: {e}")
+                logger.error(
+                    f"Failed to connect remote server {server_name}: {exception_text(e)}"
+                )
                 self._record_feedback_event(
                     "provision_failure",
                     {
@@ -4317,7 +4324,9 @@ class GatewayTools:
             )
 
         except Exception as e:
-            logger.error(f"Failed to start provisioning {server_name}: {e}")
+            logger.error(
+                f"Failed to start provisioning {server_name}: {exception_text(e)}"
+            )
             self._record_feedback_event(
                 "provision_failure",
                 {
@@ -4405,12 +4414,12 @@ class GatewayTools:
                         server_name=server_name,
                         auth_state="elicitation_required",
                         auth_event="url_elicitation_required",
-                        error=str(e),
+                        error=exception_text(e),
                     )
                     return AuthConnectOutput(
                         ok=False,
                         server=server_name,
-                        message=str(e),
+                        message=exception_text(e),
                         auth_state="elicitation_required",
                     )
                 retry_step = f"Retry gateway.provision(server_name='{server_name}') or gateway.invoke."
@@ -4573,12 +4582,12 @@ class GatewayTools:
                 server_name=server_name,
                 auth_state="missing_auth",
                 auth_event="missing_credential",
-                error=str(exc),
+                error=exception_text(exc),
             )
             return AuthConnectOutput(
                 ok=False,
                 server=server_name,
-                message=str(exc),
+                message=exception_text(exc),
                 auth_state="missing_auth",
                 env_var=env_var,
             )
@@ -4782,7 +4791,7 @@ class GatewayTools:
             # A transport that raises instead of returning an outcome is a bug, not a
             # second door. Give up the same way, so the worker can never afterwards be
             # granted permission to send.
-            logger.warning("Feedback submission raised: %s", exc)
+            logger.warning("Feedback submission raised: %s", exception_text(exc))
             result = progress.abandon()
 
         # FeedbackProgress is constructed with no destination, so the snapshots IT
@@ -5053,7 +5062,7 @@ class GatewayTools:
                 server=server_name,
                 package_type=package_type,
                 package_name=package_name,
-                message=f"Failed to run update probe: {e}",
+                message=f"Failed to run update probe: {exception_text(e)}",
             )
 
         if not ok:
@@ -5283,7 +5292,7 @@ class GatewayTools:
                             # reported result to failed.
                             logger.warning(
                                 f"Failed to persist descriptions cache after updating "
-                                f"'{server_name}': {e}"
+                                f"'{server_name}': {exception_text(e)}"
                             )
 
             message = (
@@ -5452,7 +5461,11 @@ class GatewayTools:
         except TimeoutError:
             timed_out = True
         except Exception as exc:  # resolution fails closed; see package_identity
-            logger.warning("Package identity lookup raised for %r: %s", package, exc)
+            logger.warning(
+                "Package identity lookup raised for %r: %s",
+                package,
+                exception_text(exc),
+            )
 
         if resolved is None:
             reason = (
@@ -5569,10 +5582,12 @@ class GatewayTools:
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
 
@@ -5640,10 +5655,13 @@ class GatewayTools:
             )
 
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
                 status="failed",
                 progress=0,
@@ -5734,9 +5752,12 @@ class GatewayTools:
             )
 
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
             if process and process.returncode is None:
                 try:
@@ -5780,7 +5801,7 @@ class GatewayTools:
                 if t.server_name == job_server_name
             ]
         except Exception as e:
-            logger.error(f"Failed to refresh after install: {e}")
+            logger.error(f"Failed to refresh after install: {exception_text(e)}")
             refresh_error = self._sanitize_error(e)
 
         message = f"Server '{job_server_name}' installed"
@@ -6009,7 +6030,7 @@ class GatewayTools:
                 outcome="failure",
                 started_at=audit_started_at,
                 server_name=parsed.server_name,
-                error=str(e),
+                error=exception_text(e),
             )
             return TasksListOutput(ok=False, errors=[self._sanitize_error(e)])
 
@@ -6053,7 +6074,7 @@ class GatewayTools:
                 started_at=audit_started_at,
                 server_name=parsed.server_name,
                 task_id=parsed.task_id,
-                error=str(e),
+                error=exception_text(e),
             )
             return TasksGetOutput(ok=False, errors=[self._sanitize_error(e)])
 
@@ -6127,7 +6148,7 @@ class GatewayTools:
                 started_at=audit_started_at,
                 server_name=parsed.server_name,
                 task_id=parsed.task_id,
-                error=str(e),
+                error=exception_text(e),
             )
             return TasksResultOutput(ok=False, errors=[self._sanitize_error(e)])
 
````

### Patch — `src/pmcp/trust_store.py`

````diff
diff --git a/src/pmcp/trust_store.py b/src/pmcp/trust_store.py
index dd9ed8c..23f9a7d 100644
--- a/src/pmcp/trust_store.py
+++ b/src/pmcp/trust_store.py
@@ -42,6 +42,7 @@ from dataclasses import dataclass
 from datetime import datetime, timezone
 from pathlib import Path
 from typing import Any
+from pmcp.argument_errors import exception_text
 
 APPROVED = "approved"
 DENIED = "denied"
@@ -269,7 +270,9 @@ def _decode(entry: Any) -> TrustRecord:
     try:
         parsed_at = datetime.fromisoformat(str(recorded_at))
     except ValueError as exc:
-        raise TrustStoreError(f"Unparseable trust timestamp: {exc}") from exc
+        raise TrustStoreError(
+            f"Unparseable trust timestamp: {exception_text(exc)}"
+        ) from exc
 
     return TrustRecord(
         absolute_path=Path(str(absolute_path)),
@@ -297,7 +300,9 @@ def _read_store(path: Path) -> list[TrustRecord]:
     try:
         data = json.loads(raw)
     except ValueError as exc:
-        raise TrustStoreError(f"Cannot parse trust store {path}: {exc}") from exc
+        raise TrustStoreError(
+            f"Cannot parse trust store {path}: {exception_text(exc)}"
+        ) from exc
 
     if not isinstance(data, dict):
         raise TrustStoreError(f"Trust store {path} is not a JSON object")
````

### Patch — `src/pmcp/types.py`

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

### Patch — `tests/test_argument_error_echo.py`

````diff
diff --git a/tests/test_argument_error_echo.py b/tests/test_argument_error_echo.py
new file mode 100644
index 0000000..8922d0e
--- /dev/null
+++ b/tests/test_argument_error_echo.py
@@ -0,0 +1,1708 @@
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
+# --- every exception-to-text sink in src/pmcp (rev 2, board finding B1/N3) ------
+
+#: Exception types an `except` clause names that also catch a pydantic
+#: (`ValueError`) or jsonschema (`_Error`) `ValidationError`.
+_CATCHES_VALIDATION = {
+    "Exception",
+    "BaseException",
+    "ValueError",
+    "ValidationError",
+    "_Error",
+}
+#: Renderers that describe a validation error from its structure.
+_SAFE_RENDERERS = {
+    "exception_text",
+    "safe_exc_info",
+    "safe_traceback_text",
+    "describe_exception",
+    "sanitize_auth_diagnostic",
+    "_sanitize_error",
+    "describe_argument_error",
+    "describe_schema_error",
+    "describe_model_error",
+    "type",
+    "isinstance",
+}
+#: Callees that receive an exception and do not render its text, each read:
+_NON_RENDERING_CALLEES = {
+    # auth.py: parses a JSON-RPC elicitation payload out of `args[0]` and
+    # returns structured URLs; never returns or logs the exception's text.
+    "parse_url_elicitation_error",
+    # manager.py: a boolean predicate over the message.
+    "_is_protocol_version_initialize_error",
+    # manager.py: hands the exception to the awaiting connect caller, whose
+    # own `except` is checked here like any other.
+    "set_exception",
+    # policy.py: renders its `error` argument with `exception_text`.
+    "_warn_unparseable",
+    # scoped_advisor_audit.py (#296): records path and keyword only.
+    "record_rejected_arguments",
+}
+#: Attributes of an exception that carry its text (or the value) themselves.
+_TEXT_ATTRIBUTES = {
+    "args",
+    "message",
+    "errors",
+    "json",
+    "instance",
+    "validator_value",
+    "context",
+    "cause",
+    "exceptions",
+    "__cause__",
+    "__context__",
+    "__traceback__",
+    "__str__",
+    "__repr__",
+}
+_TRACEBACK_RENDERERS = {
+    "format_exc",
+    "format_exception",
+    "print_exc",
+    "print_exception",
+}
+#: Functions that read an exception's text to parse it, and return no text:
+_NON_RENDERING_FUNCTIONS = {
+    # auth.py: looks for a JSON-RPC -32042 payload in `args[0]` / `str()`
+    # and returns structured `UrlElicitationInfo` (URLs the server sent).
+    "parse_url_elicitation_error",
+}
+
+
+def _sink_sources() -> list[Path]:
+    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
+    return [
+        path
+        for path in sorted(root.rglob("*.py"))
+        if not any(
+            part in ("cli.py", "cli_commands", "__main__.py", "baml_client")
+            for part in path.relative_to(root).parts
+        )
+        and path.name != "argument_errors.py"
+    ]
+
+
+def _caught_names(node: Any) -> set[str]:
+    import ast
+
+    if node is None:
+        return {"<bare>"}
+    if isinstance(node, ast.Tuple):
+        return set().union(*(_caught_names(item) for item in node.elts))
+    if isinstance(node, ast.Attribute):
+        return {node.attr}
+    if isinstance(node, ast.Name):
+        return {node.id}
+    return {"<expression>"}
+
+
+def _exception_sinks(source: str, label: str) -> list[str]:
+    """Every use of an exception that could render a validation error's text
+    without going through a renderer above. An exception name is: an
+    `except` clause's name, if the clause can catch a `ValidationError`; a
+    name assigned `<task>.exception()`; a name narrowed by
+    `isinstance(name, Exception|BaseException)`; and any alias of those."""
+    import ast
+
+    tree = ast.parse(source)
+    for function in ast.walk(tree):
+        if (
+            isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
+            and function.name in _NON_RENDERING_FUNCTIONS
+        ):
+            function.body = [ast.Pass()]
+    parents = {
+        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
+    }
+    found: list[str] = []
+    scopes: list[tuple[list[ast.stmt], set[str]]] = []
+    for node in ast.walk(tree):
+        if isinstance(node, ast.ExceptHandler) and node.name:
+            if _caught_names(node.type) & (
+                _CATCHES_VALIDATION | {"<bare>", "<expression>"}
+            ):
+                scopes.append((node.body, {node.name}))
+        elif isinstance(node, ast.If):
+            test = node.test
+            if (
+                isinstance(test, ast.Call)
+                and getattr(test.func, "id", None) == "isinstance"
+                and isinstance(test.args[0], ast.Name)
+                and _caught_names(test.args[1]) & {"Exception", "BaseException"}
+            ):
+                scopes.append((node.body, {test.args[0].id}))
+        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
+            names = {
+                target.id
+                for stmt in ast.walk(node)
+                if isinstance(stmt, ast.Assign)
+                and isinstance(stmt.value, ast.Call)
+                and isinstance(stmt.value.func, ast.Attribute)
+                and stmt.value.func.attr == "exception"
+                and not stmt.value.args
+                for target in stmt.targets
+                if isinstance(target, ast.Name)
+            }
+            if names:
+                scopes.append((node.body, names))
+    for body, names in scopes:
+        module = ast.Module(body=body, type_ignores=[])
+        # Aliases (`last_error = e`) are exception names too.
+        for stmt in ast.walk(module):
+            if (
+                isinstance(stmt, ast.Assign)
+                and isinstance(stmt.value, ast.Name)
+                and stmt.value.id in names
+            ):
+                names |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
+        for use in ast.walk(module):
+            if not (
+                isinstance(use, ast.Name)
+                and use.id in names
+                and isinstance(use.ctx, ast.Load)
+            ):
+                continue
+            parent = parents.get(use)
+            if isinstance(parent, ast.keyword):
+                parent = parents.get(parent)
+            if isinstance(parent, ast.Call):
+                callee = getattr(parent.func, "id", None) or getattr(
+                    parent.func, "attr", None
+                )
+                if callee in _SAFE_RENDERERS | _NON_RENDERING_CALLEES:
+                    continue
+            elif isinstance(
+                parent, (ast.Raise, ast.Compare, ast.BoolOp, ast.If, ast.UnaryOp)
+            ):
+                continue
+            elif isinstance(parent, ast.Assign) and parent.value is use:
+                continue
+            elif (
+                isinstance(parent, ast.Attribute)
+                and parent.attr not in _TEXT_ATTRIBUTES
+            ):
+                continue
+            found.append(f"{label}:{use.lineno}: {type(parent).__name__} uses {use.id}")
+    for call in ast.walk(tree):
+        if not isinstance(call, ast.Call):
+            continue
+        callee = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
+        if callee == "exception" and (call.args or call.keywords):
+            found.append(f"{label}:{call.lineno}: logger.exception renders a traceback")
+        if callee in _TRACEBACK_RENDERERS:
+            found.append(f"{label}:{call.lineno}: traceback.{callee}")
+        for keyword in call.keywords:
+            if keyword.arg == "exc_info" and not (
+                isinstance(keyword.value, ast.Call)
+                and getattr(keyword.value.func, "id", None) == "safe_exc_info"
+            ):
+                found.append(
+                    f"{label}:{call.lineno}: exc_info= not through safe_exc_info"
+                )
+    return found
+
+
+def test_no_exception_reaches_text_except_through_the_renderer() -> None:
+    """Static half of the class (rev 2): every place `src/pmcp` (bar the
+    operator's CLI) turns an exception that may be a `ValidationError` into
+    text -- a response, a log line, a traceback, an audit or health field --
+    goes through `exception_text` / `safe_exc_info` or another renderer
+    above. The dynamic sweeps below exercise the reachable ones."""
+    sources = _sink_sources()
+    assert len(sources) > 40, len(sources)
+    found = [
+        sink
+        for path in sources
+        for sink in _exception_sinks(path.read_text(), str(path.name))
+    ]
+    assert found == [], "\n".join(found)
+
+
+@pytest.mark.parametrize(
+    "snippet",
+    [
+        "try:\n    f()\nexcept Exception as e:\n    log(f'{e}')\n",
+        "try:\n    f()\nexcept ValueError as e:\n    x = str(e)\n",
+        "try:\n    f()\nexcept Exception as e:\n    logger.warning('%s', e)\n",
+        "try:\n    f()\nexcept Exception as e:\n    y = e.args[0]\n",
+        "try:\n    f()\nexcept Exception as e:\n    last = e\n    out(last)\n",
+        "try:\n    f()\nexcept Exception:\n    logger.error('x', exc_info=True)\n",
+        "try:\n    f()\nexcept Exception:\n    logger.exception('x')\n",
+        "def g(t):\n    exc = t.exception()\n    log(f'{exc}')\n",
+        "def g(r):\n    if isinstance(r, Exception):\n        log(f'{r}')\n",
+        "import traceback\ntraceback.format_exc()\n",
+    ],
+)
+def test_the_sink_scanner_flags_each_shape(snippet: str) -> None:
+    """The static check is only as wide as its rules: each rule fires."""
+    assert _exception_sinks(snippet, "snippet"), snippet
+
+
+def test_the_sink_scanner_passes_the_renderers() -> None:
+    clean = (
+        "try:\n    f()\nexcept Exception as e:\n"
+        "    log(f'{exception_text(e)}', exc_info=safe_exc_info(e))\n"
+        "    if e.code == 1:\n        raise\n    raise X() from e\n"
+        "try:\n    f()\nexcept KeyError as e:\n    log(f'{e}')\n"
+    )
+    assert _exception_sinks(clean, "clean") == []
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
````

### Patch — `tests/test_gateway_tool_schemas.py`

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

### Patch — `tests/test_scoped_advisor_audit.py`

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

## Measurement scripts

Run each from the repo root of the tree it measures. `probe.py` and `count_leaks.py` import the test module.

### `probe.py` — rev 1's before/after probe

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

### `count_leaks.py` — leak counts per layer and channel over the sweep's own cases (hex family)

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

### `mutants.py` — the mutation run; `python mutants.py <worktree> <out-dir> [M4 ...]`, `NO_STATIC=1` deselects the static guard

```python
"""Apply each mutant to a saved copy, run the tests, restore from the copy.

usage: python mutants.py <worktree> <out-dir>
"""
import filecmp, os, shutil, subprocess, sys
from pathlib import Path

root, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
TESTS = ["tests/test_argument_error_echo.py", "tests/test_scoped_advisor_audit.py",
         "tests/test_gateway_tool_schemas.py"]
S = "src/pmcp/server.py"; A = "src/pmcp/argument_errors.py"; T = "src/pmcp/types.py"
H = "src/pmcp/tools/handlers.py"; D = "src/pmcp/scoped_advisor_audit.py"
U = "src/pmcp/auth.py"; C = "src/pmcp/client/manager.py"; I = "src/pmcp/manifest/installer.py"
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
    shutil.copy2(path, saved)
    text = path.read_text()
    for old, new in edits:
        n = text.count(old)
        assert n == 1, (label, n, old)
        text = text.replace(old, new)
    path.write_text(text)
    log = out / (label.split()[0] + ".log")
    extra = ["--deselect", "tests/test_argument_error_echo.py::test_no_exception_reaches_text_except_through_the_renderer"] if os.environ.get("NO_STATIC") else []
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

### `codemod.py` — how the 82 mechanical sinks were rewritten (provenance; the patches above are authoritative)

```python
"""Route mechanical exception-to-text sinks through exception_text / safe_exc_info."""
import ast, sys, re
from pathlib import Path

CATCHES = {"Exception", "BaseException", "ValueError", "ValidationError", "_Error", "<bare>", "?"}
SAFE = {"exception_text", "safe_exc_info", "describe_exception", "sanitize_auth_diagnostic", "_sanitize_error", "type", "isinstance", "describe_argument_error", "describe_schema_error", "describe_model_error", "safe_traceback_text"}
LOGF = {"debug", "info", "warning", "error", "critical", "exception", "log"}

def caught(t):
    if t is None: return {"<bare>"}
    if isinstance(t, ast.Tuple): return set().union(*(caught(x) for x in t.elts))
    if isinstance(t, ast.Attribute): return {t.attr}
    if isinstance(t, ast.Name): return {t.id}
    return {"?"}

def edit(path):
    src = path.read_text(); lines = src.split("\n"); tree = ast.parse(src)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    reps = []  # (lineno, col, end_col, new)
    needs = set()
    for h in ast.walk(tree):
        if not isinstance(h, ast.ExceptHandler) or not h.name or not (caught(h.type) & CATCHES): continue
        for node in ast.walk(ast.Module(body=h.body, type_ignores=[])):
            if not (isinstance(node, ast.Name) and node.id == h.name and isinstance(node.ctx, ast.Load)): continue
            p = parents.get(node)
            if isinstance(p, ast.FormattedValue):
                reps.append((node.lineno, node.col_offset, node.end_col_offset, f"exception_text({h.name})")); needs.add("exception_text")
                if p.conversion in (ord("r"), ord("s")):
                    reps.append(("conv", node.lineno, node.end_col_offset))
            elif isinstance(p, ast.Call) and getattr(p.func, "id", None) == "str" and len(p.args) == 1:
                reps.append((p.lineno, p.col_offset, p.end_col_offset, f"exception_text({h.name})")); needs.add("exception_text")
            elif isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute) and p.func.attr in LOGF and node in p.args[1:]:
                reps.append((node.lineno, node.col_offset, node.end_col_offset, f"exception_text({h.name})")); needs.add("exception_text")
        for node in ast.walk(ast.Module(body=h.body, type_ignores=[])):
            if isinstance(node, ast.Call):
                for k in node.keywords:
                    if k.arg == "exc_info" and isinstance(k.value, ast.Constant) and k.value.value is True:
                        reps.append((k.value.lineno, k.value.col_offset, k.value.end_col_offset, f"safe_exc_info({h.name})")); needs.add("safe_exc_info")
    # apply right-to-left per line
    conv = {(r[1], r[2]) for r in reps if r[0] == "conv"}
    reps = sorted({r for r in reps if r[0] != "conv"}, key=lambda r: (r[0], -r[1]))
    for ln, c, e, new in reps:
        line = lines[ln - 1]
        tail = line[e:]
        if (ln, e) in conv:
            tail = re.sub(r"^![rs]", "", tail)
        lines[ln - 1] = line[:c] + new + tail
    if needs:
        path.write_text("\n".join(lines))
    return len(reps), needs

root = Path(sys.argv[1])
for path in sorted(root.rglob("*.py")):
    if any(s in str(path) for s in ("cli.py", "cli_commands", "__main__.py", "baml_client", "argument_errors.py")): continue
    n, needs = edit(path)
    if n: print(path, n, sorted(needs))
```

