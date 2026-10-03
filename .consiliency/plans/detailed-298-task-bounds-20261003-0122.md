# Detailed plan: bound and finite-check every numeric task hint — at the gate, from downstream, and on the wire

> Written on main `89559db` (dev0, a team host), worktree `pmcp-298`, branch
> `plan/298-task-bounds`. Every number below was measured on that tree, or on
> the spike of this plan applied to it. The spike was then removed, and this PR
> carries only this file.
>
> **Revision 3 (2026-10-03), on main `89559db`.** Board round 2 on PR 329
> (rev 2, `8bde68b`) passed with claude and gemini, but codex found two
> blocking defects. Both are fixed, and claude's three round-2 notes are
> taken.
>
> **B1. Remote ingestion turned non-finite values into `null` before
> normalisation.** `_read_sse` dumped every SDK message with
> `mode="json"`. Measured through the real readers (legacy `sse_client` over
> an httpx MockTransport, and streamable HTTP's SSE-event and JSON-body
> handlers): NaN, ±Infinity and ±1e400 reached pmcp as `null`, so
> `unusable_fields` came out `[]`. The dump now uses `mode="python"`, and
> tests go through those real readers (*Transport table*, Research summary).
>
> **B2. Blank timestamps were fabricated.** A blank or whitespace-only
> timestamp became "not sent", and the current time was recorded in its
> place. Blank strings are now unusable. The rest of that class was checked
> field by field and decided (Design decision 6): a JSON `null` the
> downstream *sent* is now unusable, except for `ttl`, where MCP's `null`
> means unlimited; a blank `status` is unusable too.
>
> **Round-2 notes taken:**
> - **R2-N1:** `unusable_fields` is filtered to the five names at a single
>   point.
> - **R2-N2:** eviction sorts on `min(updated_at, pmcp's record time)`, so
>   one server cannot keep its records by claiming a future time. A
>   cross-server test covers it.
> - **R2-N3:** the encoder raises outside its `except` block, so no
>   `__context__` is attached.
>
> **Re-measured:**
> - the new module, red on main and green on the patch;
> - the 7 touched modules;
> - both #297 orders on fresh trees (byte-identical);
> - 32 mutants, all killed;
> - the full suite, once.
>
> Rev 2 is `8bde68b`.
>
> **Revision 2 (2026-10-03), on main `89559db`.** This revision answers
> board round 1 on PR 329. Codex raised two blocking findings, B1 and B2. The
> claude seat raised seven non-blocking ones, N1 to N7, and all seven are
> taken. The rest of this note lists what changed and where.
>
> **B1, #297 composition.** #297's tests expected a malformed task hint to
> raise. Under this plan it is dropped instead, so 75 of those tests failed,
> and two passed without exercising anything.
> - The fixes are in Design decision 10: each fixture moves to a field that
>   still rejects, and the downstream sweep flips to the new invariant.
> - Measured: #297 + #298 was applied to a fresh main in both orders. Both
>   produce byte-identical trees, and 12 modules give 1506 passed, 0 failed.
>
> **B2, recording.** An unusable `lastUpdatedAt` used to become the current
> time when the task was recorded. It now stays `null` through recording, and
> eviction has a stated rule (Design decision 6).
>
> **The seven non-blocking findings:**
> - **N1:** a non-string `status` is dropped instead of raising.
> - **N2:** a whole-number float `ttl` (`300000.0`) is kept as an int.
> - **N3:** a new `unusable_fields` list tells "unusable" apart from the MCP
>   `null`, which means "unlimited". The `raw` claim is corrected.
> - **N4:** the fire-and-forget writer and `_send_initialize` now have tests
>   and mutants, and so do non-finite *string* timestamps.
> - **N5:** the M6 claim is corrected.
> - **N6:** a note for Consiliency/pmcp#330.
> - **N7:** the encoder now fails closed on anything that is not strict JSON.
>
> Re-measured:
> - the spike, the new module (on the patched tree and on main), and the 7
>   touched modules;
> - the mutation table, now 25 mutants, all killed;
> - the full suite, once.
>
> Revision 1 was `b0bfe07`.

## Task

Consiliency/pmcp#298: `gateway.invoke`'s `task.ttl` accepts negative values,
and `task.poll_interval` accepts NaN, ±Infinity and any negative value. Both
are forwarded downstream unchanged. Two later comments on the issue add that
`task.poll_interval` has no bound at all. A JSON integer with
|n| ≥ 2**1024 − 2**970, of either sign, passes the gate, and the model then
refuses it with `float_type`, echoing it.

The brief widens the issue to the whole class. That means every numeric,
duration, interval, timeout or limit value that the gateway (a) accepts from a
caller, or (b) reads from a downstream and acts on or forwards. Each one is
checked against these failure modes:
- negative;
- zero where zero is meaningless;
- NaN and ±Infinity;
- a bool posing as an int;
- a huge value;
- a float where an int is expected.

The rules:
- Caller input is refused at the gate, with a structural message.
- Downstream values are made safe.
- What pmcp forwards is valid JSON and of the type the spec gives.
- The advertised schema stays derived from the model, with no drift.

## Research summary

### What a caller can send, and what the transports deliver

- The gateway tools that take a numeric argument, found by walking every
  `_GATEWAY_TOOL_SPECS` model (`src/pmcp/tools/handlers.py`) recursively:

  | Tool | Field | Model | Type |
  |---|---|---|---|
  | `gateway.catalog_search` | `limit` | `CatalogSearchInput` | int |
  | `gateway.search_registry` | `limit` | `SearchRegistryInput` | int |
  | `gateway.invoke` | `options.timeout_ms` and `options.max_output_chars` | `InvokeOptions` | int |
  | `gateway.tasks_result` | `options.timeout_ms` and `options.max_output_chars` | `InvokeOptions` (shared) | int |
  | `gateway.invoke` | `task.ttl` | `TaskMetadataInput`, `types.py:574` | int |
  | `gateway.invoke` | `task.poll_interval` | `TaskMetadataInput`, `types.py:586` | float |

  No other caller field is numeric. The containers pmcp passes through without
  interpreting them can still hold numbers, and pmcp forwards them:
  `invoke.arguments`, `task.metadata`, `task.requestor_context`, `_meta`, and
  the `tasks_*` `requestor_context`.
- **Both transports deliver non-finite numbers to the gate.** Measured with
  `probe_transport.py` against the SDK pinned in `uv.lock` (mcp 2.0.0):
  - stdio uses `jsonrpc_message_adapter.validate_json`, and HTTP uses
    `pydantic_core.from_json`.
  - Both parse `NaN`, `Infinity` and `-Infinity` to floats, and both parse
    `1e400` and `-1e400` to ±inf. `json.loads` does the same.
  - A 401-digit integer arrives as a Python int.
- **No JSON Schema bound refuses NaN.** Measured with the gate's own
  `jsonschema`:
  - With `{"type":"number","exclusiveMinimum":0,"maximum":86400}`, NaN
    **passes**. Every comparison with NaN is false.
  - `exclusiveMinimum` alone passes `inf`, and `maximum` alone passes `-inf`.
  - `type: integer` already refuses NaN and ±inf, because its check is
    `float.is_integer()`. `type: number` does not.

  So bounds alone cannot make the gate agree with a model that has
  `allow_inf_nan=False`. The gate's *validator* has to change too.
- There is one gate site: `jsonschema.validate(instance=arguments,
  schema=tool.input_schema)` at `server.py:313`. It runs for every registered
  tool before dispatch. The only other `jsonschema` use in `src/pmcp` is the
  scoped audit's `validator_for(...).VALIDATORS` keyword list
  (`scoped_advisor_audit.py:200`).

### What pmcp forwards

- `ClientManager._task_wire_metadata` (`manager.py:1634`) copies `ttl` and
  `poll_interval` into the request's `task` as `ttl` and `pollInterval`.
- Outbound frames are encoded in three places:
  - `_send_message_to_downstream` (`manager.py:3228`);
  - `_send_request` (`manager.py:3426`);
  - `_send_initialize` (`manager.py:3550`).

  On **stdio**, each writes `json.dumps(...)` with the default
  `allow_nan=True`, so a NaN anywhere in the frame goes out as the bare
  literal `NaN`. That is not JSON (RFC 8259 §6).

  On **remote** (streamable HTTP and SSE), the frame goes through the SDK.
  Its `streamable_http.py:326` and `sse.py:126` call
  `model_dump(mode="json")`, which **silently turns NaN and ±Inf into
  `null`**. Measured: `{"v": NaN, "w": Infinity}` went out as
  `{"v":null,"w":null}`.

  So the same caller payload reaches a stdio downstream as invalid JSON and a
  remote one as a different value.

  In the spike's stdio and remote tests on main, pmcp wrote the frame and then
  waited out the whole request timeout (`30.03s` each). Nothing refused it.

### What the MCP spec says (2025-11-25, `schema/2025-11-25/schema.json`, fetched and read with `jq`)

- `TaskMetadata` (the request's `task`) has a single field, `ttl`:
  `{"type": "integer"}`, described as "Requested duration in **milliseconds**
  to retain task from creation". It has no `pollInterval`, and
  `additionalProperties` is not set (so it is open). It states no minimum.
- `Task` (the response) has:
  - `ttl`: `{"type": ["integer","null"]}`, described as "Actual retention
    duration from creation in milliseconds, null for unlimited". It is
    required.
  - `pollInterval`: `{"type": "integer"}`, described as "Suggested polling
    interval in milliseconds". It is optional.

  Neither field states a minimum.
- **Units differ from pmcp's.** pmcp's own docs give seconds:
  - the field descriptions say "Requested task TTL in seconds" and "Seconds
    between task status polls";
  - `specs/tenant-code-mode-host-contract.md:120` says "`ttl`: task lifetime
    hint in seconds".

  pmcp forwards the number unchanged. A spec-conformant downstream therefore
  reads `ttl: 300` as 300 ms. This is outside #298; see Design decision 9.

### What pmcp does with downstream task values

- `_task_info_from_payload` (`manager.py:1679`) builds `McpTaskInfo`
  (`types.py:521`) from `ttl`, `pollInterval`/`poll_interval`,
  `createdAt`/`created_at`, `updatedAt`/`lastUpdatedAt`, and the rest.
  `_record_task` (`manager.py:1702`) copies the result into an
  `McpTaskRecord`.
- **pmcp has no polling loop.** This was measured with grep and AST:
  - No `asyncio.sleep` in `src/pmcp` takes a task value. The sleeps are
    constants: connection retries, reconnect back-off, the health interval and
    the reconcile debounce.
  - `.poll_interval` is read in only two functions, `_task_wire_metadata`
    (to forward it) and `_record_task` (to copy it).
  - `.ttl` is read the same way. `_evict_terminal_tasks` (`manager.py:1731`)
    uses a count cap and sorts on `updated_at or 0.0`. It does not use `ttl`.

  So pmcp *reports* the downstream's `pollInterval` and `ttl` to callers (in
  `tasks_list`, `tasks_get`, `tasks_result`, `tasks_cancel` and `invoke`'s
  `task`), but never acts on them. `updated_at` *is* acted on, through the
  eviction sort.
- **A bad downstream value can fail the call that created the task.** Measured
  with `probe_downstream.py`, and through `call_tool` in the spike's tests:
  - `ttl: 1.5`, `NaN`, `Infinity` or `1e300` raises `ValidationError` inside
    `call_tool`. That happens *after* the downstream accepted and created the
    task. The record is never written, so pmcp loses the task. The invoke
    handler's generic arm returns `str(e)[:400]`, and that text carries
    pydantic's `input_value=` of the **downstream's** value.
  - `pollInterval: 10**400` raises `ValidationError` (`float_type`).
  - `createdAt`/`lastUpdatedAt: 10**400` raises a bare **`OverflowError`**
    from `_normalize_task_timestamp`'s `float(value)`. pydantic does not
    convert it.
  - `pollInterval` NaN, ±Inf, negative or 0 is stored. So is `ttl` negative,
    bool (stored as 1) or `"5"` (stored as 5). So are timestamps that are NaN
    or ±Inf. A NaN `updated_at` makes `_evict_terminal_tasks`' sort order
    undefined.
  - On output, `_sanitize_task_for_output` dumps with `mode="json"`. That
    keeps a NaN in a `float` field as a Python NaN, which the server then
    writes as `NaN` with `json.dumps(result)` (`server.py:464`). In the
    `dict[str, Any]` `raw`, the same dump turns NaN into `None`.
- **A non-numeric field fails the same way (rev 2, N1).** `status` reaches
  `McpTaskInfo` unguarded. `status_message` is guarded with `isinstance(…,
  str)`. A downstream `status: 5` (or NaN, `true`, a list or an object)
  raises `ValidationError` after the downstream created the task. The effects
  are:
  - the record is lost;
  - the error echoes `input_value=5`;
  - a whole `tasks/list` fails because of one bad entry;
  - so does `tasks_cancel`.

  No other non-numeric task field can raise this way:
  - `taskId` is checked with `isinstance(…, str)`, and the payload is
    dropped if it fails;
  - `statusMessage` is guarded the same way;
  - `raw` is the payload `dict` itself.
- **Recording substitutes a value (rev 2, B2).**
  - `_record_task` (`manager.py:1736`) writes
    `updated_at=task_info.updated_at or time.time()`. With the field-level
    drop, an unusable `lastUpdatedAt` (NaN) would become `None` and then the
    *current time*. Callers would see that time, and it would drive eviction.
    The `or` also replaces a legitimate `0.0`.
  - `_evict_terminal_tasks` (`manager.py:1764`) sorts on
    `updated_at or 0.0`.
  - `created_at` keeps the *first* recorded value, even when that one was
    `None` and a later payload carries a usable one.
  - `ttl` and `poll_interval` are copied as they are. The other `or`s in
    `_record_task` (`tool_id`, `requestor_context`) are not task hints.
- **A whole-number float `ttl` (rev 2, N2).** On main, a downstream
  `ttl: 300000.0` is stored as `300000`, because pydantic accepts an integral
  float for `int`. Draft 2020-12 calls it an integer too. A plain
  `type(value) is int` rule would wrongly drop it.
- **What reaches pmcp, per transport (rev 3, board round 2 B1).** The frame
  `{"jsonrpc":"2.0","id":1,"result":{"task":{"taskId":"t","status":"working","ttl":L,"pollInterval":L,"lastUpdatedAt":L}}}`
  was sent with each literal `L` through each transport's real parse step:
  - **stdio:** `_handle_stdout_line`, which uses `json.loads`;
  - **legacy SSE:** the SDK's `sse_client`, over an httpx2 `MockTransport`;
  - **streamable HTTP:** `StreamableHTTPTransport._handle_sse_event` (the
    GET stream and SSE responses) and `_handle_json_response` (a JSON body).

  Every remote path then goes through pmcp's `_read_sse`. The SDK's
  `validate_json` neither rejects nor converts these tokens: all three
  remote entry points parse NaN, ±Infinity and ±1e400 to floats, just as
  `json.loads` does. The loss was pmcp's own `model_dump(mode="json")`.
  Measured by `probe_transports.py`; the *Unusable on rev 2* and *rev 3*
  columns show `unusable_fields` from the parsed task.

  | `L` | stdio sees | SSE / GET-event / JSON-body see, rev 2 (`mode="json"`) | unusable on rev 2: stdio → remote | rev 3 (`mode="python"`), all four transports | unusable on rev 3 |
  |---|---|---|---|---|---|
  | `NaN` | `nan` | `null` | `[updated_at, ttl, poll_interval]` → **`[]`** | `nan` | `[updated_at, ttl, poll_interval]` |
  | `Infinity` | `inf` | `null` | same → **`[]`** | `inf` | same |
  | `-Infinity` | `-inf` | `null` | same → **`[]`** | `-inf` | same |
  | `1e400` | `inf` | `null` | same → **`[]`** | `inf` | same |
  | `-1e400` | `-inf` | `null` | same → **`[]`** | `-inf` | same |
  | `null` (control) | `None` | `None` | `[]` → `[]` | `None` | `[updated_at, poll_interval]` (sent null; `ttl` stays unlimited) |
  | `""`, `" \n\t "` | the string | the string | `[ttl, poll_interval]` on all four (**`updated_at` missing**: B2) | the string | `[updated_at, ttl, poll_interval]` |
  | `{}`, `[]` | the value | the value | `[updated_at, ttl, poll_interval]` on all four | the value | same |

  On rev 2, a remote `ttl` of NaN therefore read as unlimited, and a remote
  `lastUpdatedAt` of NaN was recorded as the current time. These are the two
  outcomes rev 2 set out to prevent, and they now hold on every transport.

  **What else `mode="python"` changes.** The SDK envelope's fields (`result`,
  `params`, `error.data`) are `dict[str, Any]`/`Any` that `validate_json`
  filled from JSON, so a python-mode dump returns the same values a JSON
  dump does, except that non-finite floats stay floats. `error` (an
  `ErrorData` model) still dumps to a dict. Nothing in the dispatcher needs
  JSON-mode types.

  The one visible effect beyond tasks: a downstream tool *result* containing
  NaN now reaches the caller's text block as `NaN` on remote, as it already
  did on stdio, instead of `null`. That passthrough is a non-goal; see
  *Non-goals*.

### Out of the class (stated so nobody re-derives it)

- **JWT `exp`, `nbf` and `iat`** (caller-supplied, on HTTP with resource-server
  auth) are validated by PyJWT. Consiliency/pmcp#231 owns that surface.
- **Operator numbers** (`PMCP_*` env vars, CLI flags, policy `limits`) are not
  caller or downstream input. Their echo class belongs to Consiliency/pmcp#315.
- **JSON-RPC response ids from a downstream** are matched by dict lookup
  against ids pmcp chose. pmcp does not act on them numerically.
- **Downstream tool *results*** that contain NaN are content pmcp passes
  through. `server.py:464` writes them as `NaN` inside a text block. That is a
  pre-existing passthrough, not a task hint. It is listed under *Non-goals*.

### The probe table (main `89559db` → this plan)

The table is measured. Notation:
- **G** is the gate, `jsonschema` against the advertised schema.
- **M** is the model.
- `rej` means refused. `PASS→x` means accepted and stored as `x`.
- `echo` means the model's error text carries the value.

**Caller fields**

| Field (bound on main) | negative | 0 | NaN | ±Inf | `true` | ±10^400 | 1e20 | 2.5 (int field) | 5.0 (int field) | Bound after |
|---|---|---|---|---|---|---|---|---|---|---|
| `catalog_search.limit` [1,100] | G rej / M rej | G rej / M rej | G rej / M rej | G rej / M rej | G rej / M PASS→1 | G rej / M rej | G rej / M rej | G rej / M rej | G PASS / M PASS→5 | unchanged |
| `search_registry.limit` [1,20] | same as `catalog_search.limit` | | | | | | | | | unchanged |
| `invoke.options.timeout_ms` [1000,300000] (also `tasks_result.options`) | G rej / M rej | G rej / M rej | G rej / M rej | G rej / M rej | G rej / M rej | G rej / M rej | G rej / M rej | G rej / M rej | G rej / M rej | unchanged |
| `invoke.options.max_output_chars` [100,100000] (also `tasks_result.options`) | same as `timeout_ms` | | | | | | | | | unchanged |
| **`invoke.task.ttl`** [−2^63+1, 2^63−1] | **G PASS / M PASS→−5** | **G PASS / M PASS→0** | G rej / M rej | G rej / M rej | G rej / M PASS→1 | G rej / M rej | G rej / M rej | G rej / M rej | G PASS / M PASS→5 | **[1, 2^53−1]**; after: negative and 0 are G rej / M rej |
| **`invoke.task.poll_interval`** (unbounded) | **G PASS / M PASS→−5.0** | **G PASS / M PASS→0.0** | **G PASS / M PASS→nan** | **G PASS / M PASS→±inf** | G rej / M PASS→1.0 | **G PASS / M rej (`float_type`, echo)** | **G PASS / M PASS→1e20** | n/a | n/a | **(0, 2^53−1], finite**; after: every bold cell is G rej / M rej, and `1e-300` stays G PASS / M PASS |

`2**53` and `2**63` follow the `1e20` column. Opaque containers (`arguments`,
`task.metadata`, `requestor_context`, `_meta`) are measured on the outbound
side (Design decision 8).

**Downstream fields**

The downstream `McpTaskInfo` fields are measured through
`_task_info_from_payload`. Columns:
- **str `"5"`** is a numeric string;
- **ISO** is an ISO 8601 string;
- **10^300** is the float `1e300`.

| Wire field → attribute | negative | 0 | NaN / ±Inf | `true` | 10^400 (int) | 10^300 | str `"5"` | ISO | 2.5 | After |
|---|---|---|---|---|---|---|---|---|---|---|
| `ttl` → `ttl` | kept −5 | kept 0 | **ValidationError** (call fails, task lost) | kept as 1 | kept (401 digits) | **ValidationError** | kept as 5 | n/a | **ValidationError** | non-bool int in [0, 2^63−1] is kept; anything else becomes `None` |
| `pollInterval` → `poll_interval` | kept −5.0 | kept 0.0 | **kept nan / ±inf** | kept as 1.0 | **ValidationError** | kept | kept as 5.0 | n/a | kept | non-bool int/float, finite and > 0, is kept; anything else becomes `None` |
| `createdAt` / `lastUpdatedAt` → `created_at` / `updated_at` | kept | kept | **kept nan / ±inf** | kept as 1.0 | **bare `OverflowError`** | kept | kept as 5.0 | parsed | kept | finite number or parseable (finite) string is kept; NaN, ±Inf, `"nan"`/`"1e400"`, bool, overflow, unparseable or a non-scalar becomes `None` |
| `status` → `status` (rev 2, N1) | — | — | **ValidationError** | **ValidationError** | **ValidationError** | **ValidationError** | kept | — | **ValidationError** | any string is kept; anything else becomes `None` |

In every "After" cell, "becomes `None`" also means the field is named in
`unusable_fields` (rev 2, Design decision 6). A whole-number float `ttl`
(`300000.0`) is kept as `300000`, as on main (N2).

### The test surface this touches

`tests/test_gateway_tool_schemas.py` pins three things:
- advertised == `input_schema_for(model)`;
- the snapshot `tests/fixtures/gateway_tool_schemas.json`;
- `test_every_advertised_integer_is_bounded_both_sides`.

**Integers only.** The derivation test already enforces model/schema
agreement for every `ge`/`gt`/`le` bound, so a bound added to the model is
advertised automatically. Nothing pins `number` bounds or `allow_inf_nan`,
which has no JSON Schema projection. The module docstring names
`task.poll_interval` as "stays open (Consiliency/pmcp#298)".

`tests/test_scoped_advisor_audit.py::_raised_exceptions` collects every
exception class `src/pmcp` raises, using the AST, and builds each one with one
string argument. It found the spike's first version of the new exception,
whose `__init__` took no argument: 26 red. So a new exception class must keep
the standard one-argument constructor (Design decision 8).

**#297's tests rely on a malformed task hint raising (rev 2, B1).** The
affected set was derived by searching every test file that #297 adds or
changes (`git diff 89559db 0a93265 --name-only -- tests/`) for each task
field this plan changes:
- `ttl`, `poll_interval`/`pollInterval`;
- `created_at`/`createdAt`, `updated_at`/`lastUpdatedAt`;
- `status`;
- `McpTaskInfo`, `_task_info_from_payload`, `_record_task`, `parse_timestamp`.

Measured: main + #298 rev 2, then #297 (`0a93265`) applied on top. These 6
modules failed **75** tests and passed 570:
- `test_argument_error_echo.py`
- `test_downstream_frame_echo.py`
- `test_log_record_scrubber.py`
- `test_parse_error_echo.py`
- `test_exception_text_sinks.py`
- the new module

The failures:

| #297 test | Failed | Why |
|---|---|---|
| `test_log_record_scrubber.py::test_exc_info_is_scrubbed_on_any_logger` | 48 | `_validation_error` builds `McpTaskInfo(ttl={"v": s})`, which no longer raises (`AssertionError: no validation error`) |
| `test_log_record_scrubber.py::test_percent_args_are_scrubbed_however_nested` / `…msg_that_is_an_exception…` / `…stack_info…` | 6 / 1 / 1 | same fixture |
| `test_argument_error_echo.py::test_describe_exception_renders_a_grouped_validation_error_structurally` | 6 | `pytest.raises(ValidationError)` around `ttl={"v": s}`: DID NOT RAISE |
| `test_parse_error_echo.py::test_no_timestamp_site_echoes_its_input` | 6 | `McpTaskInfo(created_at="2026-13-99T…")` no longer raises |
| `test_downstream_frame_echo.py::test_a_wrapped_handler_keeps_the_wire_code` | 3 | `invalid()` uses `ttl: s` ("did not reject") |
| `test_argument_error_echo.py::test_no_downstream_value_reaches_a_response_log_or_audit` | 2 | `_task_positions()` finds no rejecting position (`assert len(positions) > 10`) |
| `test_argument_error_echo.py::test_a_validation_error_raised_by_a_handler_is_described_not_echoed` / `…connect_failure…` | 1 / 1 | `created_at: [s]` and `ttl: {"v": s}` no longer raise |

**Two passed without exercising anything.** Both are worse than a failure:
- `test_exception_text_describes_a_wrapper_that_embeds_a_validation_error`.
  `McpTaskInfo(ttl=s)` does not raise, so the `except RuntimeError` arm and
  its assertions never run.
- `test_no_malformed_frame_value_reaches_pmcps_output` for `tasks/*`.
  `ttl: s` is now *accepted*, so the sweep's "accepted → the response may
  carry it" exemption applies, and the rejection path is never reached.

### Backward compatibility, measured

These existing tests and docs were grepped for values the new bounds refuse
(`"ttl": 0`, negative, `poll_interval=0`, negative, NaN):
`tests/test_tools.py`, `tests/test_client_manager.py`,
`tests/test_phase6_tenant_code_mode.py`, `README.md`, `specs/` and `docs/`.
**None send one.** The values in use are all still accepted:
- caller `ttl` 300, 120 and 3600;
- caller `poll_interval` 2.5, 0.5 and 0.1;
- downstream `ttl` 300 and 120;
- downstream `pollInterval` 2, 2.5 and 0.1.

These behaviour changes are visible:
1. `invoke.task.ttl` now refuses ≤ 0, and values in (2^53−1, 2^63−1].
2. `invoke.task.poll_interval` now refuses ≤ 0, NaN, ±Inf and > 2^53−1.
3. A downstream `ttl`, `pollInterval`, `createdAt`, `lastUpdatedAt` or
   `status` that cannot be used now reads as `null`, and its field is named in
   the new `unusable_fields` list. Before, it was one of:
   - coerced (`"5"` → 5, `true` → 1);
   - stored as NaN, negative or zero;
   - a failed call that lost the task.

   A whole-number float `ttl` stays an int, as on main.
4. Every task object gains `unusable_fields: []`. This is additive, and no
   existing test compares a whole task dump.
4a. (rev 3) A downstream `null` for `status`, `createdAt`, `lastUpdatedAt` or
   `pollInterval`, or a blank timestamp or status, now reads as unusable.
   Before, it was treated as not sent, and for `lastUpdatedAt` that meant
   the current time was recorded. A `null` `ttl` is unchanged: unlimited.
4b. (rev 3) On remote transports, a non-finite number in a downstream
   *result* now reaches pmcp as a float, as it already did on stdio, instead
   of as `null` (*Transport table*).
5. Any outbound frame that is not strict JSON (NaN, ±Inf, a non-JSON type, a
   cycle) now fails a request, or is dropped and logged for a fire-and-forget
   frame. Before, NaN went out as `NaN` on stdio and silently as `null` on
   remote.

With the rev 3 spike applied, these run 1151 tests: **1151 passed, 0
skipped, 0 failed**:
- `tests/test_gateway_tool_schemas.py`
- `tests/test_tools.py`
- `tests/test_client_manager.py`
- `tests/test_phase6_tenant_code_mode.py`
- `tests/test_scoped_advisor_audit.py`
- `tests/test_server.py`
- the new module

No existing *main* test needed a change, except the snapshot, which was
regenerated. #297's tests need the migration in Design decision 10.

## Design decisions (made explicitly)

### 1. The class is every numeric value crossing a trust edge, not the two fields named

Six caller fields were enumerated. Four were already sound: bounded on both
sides, NaN refused by `type: integer`, and `true` refused by the gate. They
get **no change**, but they **are** added to the per-field, per-mode agreement
matrix, so a later edit cannot loosen them unnoticed.

The defect class has three edges:
1. a caller's number reaching the model unbounded or non-finite;
2. a downstream's number being stored, reported, or raised on;
3. pmcp writing a non-finite number to a downstream.

Each edge gets one class-level mechanism: Design decisions 4, 6 and 8. The
edges are not fixed per field.

### 2. `task.ttl`: `ge=1, le=2**53 − 1`

- **The lower bound is `ge=1`, not `ge=0`.** The issue allowed either. A
  retention of zero makes the task's result unretrievable the moment it
  exists, and that is the "zero where meaningless" case. This holds in pmcp's
  units and in the spec's. `ge=1` is unit-agnostic.
- **The upper bound narrows from int64 to 2^53 − 1.**
  - pmcp forwards the value as the spec's `TaskMetadata.ttl` integer.
  - A JavaScript peer reads the number as a double, and the TypeScript SDK is
    the reference implementation. Above 2^53 − 1, the value is no longer the
    integer the caller sent. RFC 7493 (I-JSON) §2.2 sets the interoperable
    integer range at exactly ±(2^53 − 1).
  - 2^53 − 1 is ~285,000 years in ms, and ~285 million years in s. No
    legitimate value is lost, in either unit.
  - It still closes Consiliency/pmcp#236's int64 drift. Every
    `test_ttl_range_agrees_between_gate_and_model` parameter is still refused,
    and `3600` is still accepted (measured).
- The bound is the named constant `MAX_FORWARDED_TASK_NUMBER = 2**53 − 1` in
  `types.py`, shared with `poll_interval`.

### 3. `task.poll_interval`: `gt=0, le=2**53 − 1, allow_inf_nan=False`, and it stays a `float`

- `gt=0`: zero and negative intervals are meaningless.
- **A finite `le` is needed, together with `gt`.** As the issue's round-7
  comment notes, `le` alone leaves the negative side open. With both,
  ±10^400 is refused **at the gate**, by `maximum` and `exclusiveMinimum`.
  Measured: gate rej / model rej, where before it was gate PASS / model
  `float_type` with an echo.
- `allow_inf_nan=False` makes the model refuse non-finite values with
  `finite_number`. It is not projected into the schema; Design decision 4 is
  the gate's half.
- **`1e-300` is accepted.** It is finite and positive. pmcp never sleeps on
  this value (it only forwards it), and any floor would be a unit-dependent
  guess. Design decision 9 explains why units are not settled here.
- **It stays `float`, not `int`.** The spec's `Task.pollInterval` is an
  integer, but that is the *response* field. In a *request*, `pollInterval` is
  a pmcp extension. `TaskMetadata` does not define it, and its
  `additionalProperties` is open. For this field, "valid per spec" therefore
  reduces to "a finite JSON number". Making it `int` would refuse `2.5` and
  `0.5`, which are in existing tests and in the tenant contract's examples.

### 4. The gate refuses NaN and ±Infinity with a type checker, not a bound

JSON Schema cannot express "finite", and NaN slips past every bound (measured
above). The gate is therefore given a validator class:

```python
GATE_VALIDATOR = jsonschema.validators.extend(
    jsonschema.Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine("number", _is_json_number),
)
```

`_is_json_number` is the standard `number` check without NaN and ±Inf. It
lives in `src/pmcp/tools/schema.py`, next to the derivation it validates
against. `server.py:313` passes `cls=GATE_VALIDATOR`.

Why this mechanism:
- **It is RFC-correct.** NaN is not a JSON number (RFC 8259 §6), so a `type`
  failure is literally true.
- **It covers every typed `number` everywhere.** That includes fields added
  later. `integer` needs no redefinition, because its built-in
  `float.is_integer()` check already refuses NaN and ±Inf.
- **The schema does not change, so it advertises nothing non-standard.** A
  custom keyword would put a non-standard key in every `inputSchema`, and
  `format` is annotation-only by default.
- **`check_schema` is unchanged.** `GATE_VALIDATOR.check_schema(...)` passes
  for every advertised schema. The class keeps Draft 2020-12's meta-schema,
  and the existing `test_advertised_schema_is_valid_json_schema` uses
  `Draft202012Validator` directly.
- **The error's `validator` is `"type"`.** So both the #296 audit record
  (`rejected_argument_validator: "type"`) and #297's renderer handle it with
  no new case: #297 renders it as `must be of type number, null`.

A walker that refuses non-finite numbers anywhere in the arguments, including
opaque containers, was **rejected for the gate**. Opaque containers are not
pmcp's to type: `arguments` is validated against the *downstream's* schema.
Their non-finite numbers are refused at the one place pmcp makes them its
own, the outbound encoder (Design decision 8).

### 5. `true`/`false` for a number: no `Strict()`

The gate refuses a boolean for every numeric field (measured, all 8). The
model's lax mode coerces `true` to 1. That asymmetry is already documented in
`tests/test_gateway_tool_schemas.py`'s docstring, where the gate is stricter
on coercion.

`Field(strict=True)` was **rejected**. It would also make the model refuse
`5.0` for an int, which the gate accepts (Draft 2020-12 treats `5.0` as an
integer). That would open the *opposite* drift: gate passes, model refuses
and echoes.

Every caller path runs the gate first (`_handle_call_tool`), so `true` cannot
reach the model from a caller. A test pins the gate's refusal per field.

### 6. Downstream values are dropped to `None` and named in `unusable_fields`: not clamped, not refused

**Mechanism (rev 2).** `McpTaskInfo` gets one `model_validator(mode="before")`,
`_drop_unusable_hints`. It replaces rev 1's three field validators, and it
runs a check for each field:

| Field | Kept when | Notes |
|---|---|---|
| `ttl` | a non-bool `int` in [0, 2^63 − 1] | Also a **finite whole-number float**, converted to `int`, so `300000.0` → `300000` as on main (N2). The upper bound is int64, not I-JSON: pmcp only reports this value, it does not forward it. Spec `Task.ttl` 0 can mean "already expired", so 0 is kept. |
| `poll_interval` | a non-bool `int`/`float`, finite and > 0 | A huge int overflows `float()` and is unusable. |
| `created_at`, `updated_at` | a finite number, or a numeric or ISO 8601 string that parses to one (a `datetime` as is) | Unusable: a bool, NaN, ±Inf, a value that overflows `float()`, a **non-finite numeric string** (`"nan"`, `"1e400"`, `"-inf"`, N4), a **blank or whitespace-only string** (rev 3, B2; rev 2 returned `None`, i.e. "not sent"), an unparseable string, or a non-scalar. On main, a non-scalar fell through to pydantic and raised. |
| `status` (N1) | any non-blank `str` | An unknown future status is kept, as the tenant contract requires. A non-string or a blank string (rev 3) is unusable. |

A field that holds an unusable value becomes `None`, and its name is
appended to the new public field `unusable_fields: list[str]`.

An *absent* value is not unusable, and inside the model neither is an
explicit `None`: the field stays `None` and is **not** named. A `null` the
downstream *sent* is decided at the parse boundary instead (rev 3, below).

`unusable_fields` is filtered at a single point to the five field names, in
table order, whatever a caller of the model passes in (rev 3, R2-N1). So it
is value-free by construction. `test_unusable_fields_holds_only_known_field_names`
passes it a secret-shaped string, an object and a duplicate.

The validator is idempotent. `_sanitize_task_for_output` dumps the task and
validates it again, and `unusable_fields` survives that round trip.

**Representation of "unusable" (N3).** In MCP 2025-11-25, `Task.ttl: null`
means **unlimited**. Reporting `null` for "the downstream sent something
pmcp could not read" would therefore mislead. These options were rejected:
- **Omitting the key.** It changes the output's shape per task, and
  pydantic's dump has no per-instance exclude.
- **A sentinel value.** It is not the downstream's value either.

`unusable_fields` keeps every field's type and adds one structural fact: the
field *names*, never the values. So a `null` `ttl` not named there keeps the
MCP meaning, and a `null` `ttl` named there means "unusable".

**`raw` is not a faithful copy.** The output path dumps with `mode="json"`,
so `raw`'s NaN and ±Inf become `null` as well. Rev 1's CHANGELOG said
otherwise; that is now corrected. `raw` carries the downstream's *finite*
values as sent.

**Present but useless must never read as absent (rev 3, board round 2
B2).** "Absent" has consequences:
- an absent `ttl` reads as unlimited;
- an absent `updatedAt` is recorded as pmcp's observation time;
- an absent field is not named in `unusable_fields`.

So every way a sent value can say nothing was checked, field by field. The
decision is per cell, and every cell has a test:

| Sent value | `status` | `createdAt` | `lastUpdatedAt`/`updatedAt` | `ttl` | `pollInterval` |
|---|---|---|---|---|---|
| key absent | absent: `None`, not named | absent | absent: **pmcp's time** (main's behaviour, kept; see below) | absent: not named | absent |
| JSON `null` | **unusable** (required, non-null in MCP) | **unusable** | **unusable**, recorded `null` | **kept `None`, not named**: MCP's "unlimited" | **unusable** (MCP: integer, not nullable) |
| `""` / whitespace | **unusable** (rev 3: a blank status says nothing) | **unusable** (rev 3; rev 2 read it as absent) | **unusable** (rev 3; rev 2 fabricated the time) | unusable | unusable |
| `{}` / `[]` | unusable | unusable | unusable | unusable | unusable |
| NaN / ±Inf / ±1e400 | unusable | unusable | unusable | unusable | unusable |

The JSON-`null` row is decided at the parse boundary, because only there
can a *sent* `null` be told from a missing key. `_task_info_from_payload`
passes `UNUSABLE_TASK_VALUE` (the checks' own sentinel, now public in
`types.py`) for a hint the payload carries as `null`, using the same
first-alias-wins order as its `.get` chain. The model check then treats it
like any other unusable value.

Inside the model, `None` still means "not given". That keeps three things
idempotent: `_record_task`'s explicit `None` arguments, the
`_sanitize_task_for_output` round trip, and pmcp's own constructions.
`statusMessage` is not a hint: a non-string is dropped silently, as on main,
because pmcp neither acts on it nor reports it as a hint.

**Why an absent `lastUpdatedAt` still gets pmcp's time.** MCP requires it,
so only a non-conformant downstream omits it, and main has always shown
such a task as "updated when pmcp saw it". The existing tests' fixtures omit
it too. Rev 3 keeps that behaviour only for a truly absent key. Every sent
value, including `null` and blank, is reported as unusable instead.

**Recording (B2).** `_record_task` no longer substitutes values:

| Field | Public value | Rule |
|---|---|---|
| `updated_at` | the downstream's usable value | |
| | `None` | when it was unusable (named in `unusable_fields`) |
| | pmcp's observation time | **only when the key is absent.** This is main's behaviour for a non-conformant downstream. A sent `null`, a blank string or anything else unusable is `None` and named (rev 3). It is decided with `is None`, not `or`, so a usable `0.0` is kept. |
| `created_at` | the **first usable** value pmcp saw | A later usable value fills an earlier `None`, and `created_at` then leaves `unusable_fields`. On main, a first `None` stuck. |
| `ttl`, `poll_interval`, `status` | as normalised | |

**Eviction rule (rev 3, R2-N2).**
- `_evict_terminal_tasks` orders terminal records oldest first by
  `min(updated_at, _recorded_at)`, or by `_recorded_at` when `updated_at` is
  `None`.
- `_recorded_at` is a pydantic `PrivateAttr`. It is never serialised,
  defaults to `time.time()` at construction (`default_factory`), and is set
  to the recording time on every `_record_task`.

A downstream's timestamp can therefore only make its own record *older*,
never newer than when pmcp recorded it.

Rev 2's key was `updated_at` when it was usable. Under that key, one server
claiming `lastUpdatedAt: 1e300` kept its terminal records past every other
server's newer ones in the shared 100-record cap. Main has the same flaw,
but timestamps are the one task value pmcp acts on.
`test_one_downstream_cannot_keep_its_tasks_by_claiming_a_future_timestamp`
covers it: a "liar" server's records come first, an "honest" server's newer
record follows, and with a cap of 2 the honest record is kept.

A downstream that claims an *old* time evicts its own records first, which
harms nobody else. Flooding the cap with many terminal tasks is a
count problem, separate from timestamps, and is not in scope.

**Why not refuse.** Refusing the payload is what main does by accident. As
measured, it loses a task the downstream already created, and it renders the
downstream's value in the error. A hint the downstream got wrong does not
make the task unusable.

**Why not clamp.** pmcp does not act on `pollInterval` or `ttl` (Research
summary); it only *reports* them. A clamped value would misreport what the
downstream said.

**No log line.** A log line would either echo the value or add nothing that
`unusable_fields` does not already state. No check raises, so nothing can
interpolate a value.

### 7. Guard against a future polling loop: no loop exists, so there is no clamp helper

The measured fact is that pmcp has no loop driven by a downstream interval.
Two tests guard it:
1. **Behavioural.** A fake downstream keeps a task `working` with
   `pollInterval` in {0, NaN, Infinity, 1e-300, −1}.
   - Each of `get_task` and `get_task_result` completes inside
     `asyncio.wait_for(…, 2)`.
   - Exactly one downstream request is sent per call (`await_count == 2`).
   - No `asyncio.sleep` runs during the two calls. The monkeypatch target
     `pmcp.client.manager.asyncio.sleep` is the `asyncio` module's own
     attribute, so this records `asyncio.sleep` globally for the test. The
     implementer may narrow it.
2. **Static.** An AST scan of `src/pmcp` finds every function that reads
   `.poll_interval`. That set must be a subset of `{_task_wire_metadata,
   _record_task}`.

**The static guard's reach.** It sees attribute reads only. A future
consumer that reads the value as `record.model_dump()["poll_interval"]` or
`payload["pollInterval"]` would evade it. The behavioural test covers that
case, as long as the loop runs inside `get_task` or `get_task_result`. Review
covers everything else.

**Rule for any future consumer.** Adding a function to that allowlist is the
moment to clamp. A loop that sleeps on a downstream hint must use
`min(max(hint_s, floor), ceiling)`, where:
- `floor` ≥ 0.1 s;
- `ceiling` ≤ the call's own `timeout_ms`;
- `None`, which is all Design decision 6 lets through for an unusable value,
  means `floor`.

The rule is written here, not as code. Nothing would call a helper yet, and a
helper with no caller has no test that could fail.

Measured: mutant M15 adds `await asyncio.sleep(task_info.poll_interval or 0)`
to `get_task`. It turns *both* guard tests red (see the mutation table).

### 8. Outbound frames are strict JSON on every transport, and the scope of that

A module-level `_encode_outbound_frame(payload) -> str` in
`client/manager.py` runs `json.dumps(payload, allow_nan=False)`. Rev 2 (N7)
widens what it catches to `ValueError` (NaN, ±Inf, a cycle), `TypeError` (a
value `json` cannot encode, such as a `datetime`, `bytes` or a model) and
`RecursionError`. Any of them raises `OutboundFrameNotJson(_OUTBOUND_NOT_JSON)`:
- the class is a `ValueError` subclass;
- the message is fixed: "outbound frame is not strict JSON";
- it is raised **outside** the `except` block (rev 3, R2-N3), so neither
  `__cause__` nor `__context__` is attached. Rev 2's `raise … from None`
  hid the cause but still attached it as `__context__`. That text carries
  the value: `Out of range float values are not JSON compliant: nan` on
  3.12+, and a dict key in a 3.13 `TypeError`. The same pattern is used in
  #297's parsing helpers.

That is the fail-closed, value-free error N7 asked for.

**What N7 changes on remote.** Before, the SDK's `model_dump(mode="json")`
serialised a non-JSON-native param, for example by turning a `datetime` into
an ISO string. Now pmcp refuses it. No call site builds such params: a full
read of the callers found only JSON-parsed caller data and pmcp constants,
and stdio has always refused them, with a `TypeError` whose message names
only the type. One encoder rule now covers both transports. On remote, the
check costs one extra `json.dumps` per frame.

All three writers use it:
- **`_send_request`.** stdio writes the result. Remote calls it first, as a
  check, before `validate_python`. The write sits inside the `try`, whose
  `finally` pops the pending entry (C-02). The caller gets the invoke
  handler's error arm with the fixed message.
- **`_send_message_to_downstream`** (fire-and-forget: replies to
  downstream-initiated requests and `notifications/cancelled`). Both branches
  are encoded the same way. Its contract (its docstring, `manager.py:3206`) is
  to log and swallow every write failure, so a non-JSON frame is **not
  written** and does **not raise**. A DEBUG line goes through
  `describe_exception`, which renders the constant message and echoes
  nothing. For a relayed *reply*, the downstream waits on its own request.

  These frames are built only from a downstream id (already filtered to
  `str|int`), method-not-found constants, and pmcp's own int request ids. No
  float can enter them, so this case is unreachable today.
- **`_send_initialize`.** stdio encodes its constant `notifications/initialized`
  frame with the encoder. Its remote branch sends that constant without a
  check.

**Tests for every hunk (N4).**
- `test_a_fire_and_forget_frame_carrying_nan_is_dropped_not_written[stdio|remote]`
  covers the fire-and-forget writer: NaN → nothing written and no raise; a
  clean frame → written.
- `test_every_downstream_writer_encodes_through_the_strict_encoder` is a
  static check. Every function in `client/manager.py` that calls
  `stdin.write` must call `_encode_outbound_frame` and never `json.dumps`.
  It pins `_send_initialize`, whose frame no test can make non-JSON.
- Mutants M16–M18 cover these hunks; rev 1's C2, C3 and C6 survived.

**This is a behaviour change, and it is the decision the maintainer most
needs to see.** On main, a caller who puts NaN in `invoke.arguments` has it:
- delivered to a *Python* stdio downstream, which accepts the bare `NaN`;
- delivered to a remote downstream as `null`;
- turned into a hang until timeout by a strict-JSON stdio downstream, such as
  Node's `JSON.parse`.

After this plan, all three requests fail at once, with the same error.
JSON-RPC 2.0 is defined over JSON. To ship #298 without this change, drop
the `client/manager.py` encoder hunks and their four tests. Decisions 2 to 7
do not depend on them.

**Constructor.** `OutboundFrameNotJson` keeps `ValueError`'s standard
constructor. `test_scoped_advisor_audit._raised_exceptions` builds every
raised class with one string argument, and rev 1's first version, with
`__init__(self)` taking no argument, made 26 of those tests fail.

### 9. Units (seconds in pmcp, milliseconds in MCP 2025-11-25) are out of scope: Consiliency/pmcp#330

Every bound above is unit-agnostic on purpose:
- `ge=1` and `gt=0`;
- 2^53 − 1, the interoperability limit, which is not "one day in seconds".

Changing what pmcp *means* by `ttl` and `poll_interval` touches three things:
- the field descriptions (agent-facing);
- the forwarded values, which would need × 1000 for spec-conformant peers;
- `specs/tenant-code-mode-host-contract.md`, the contract tenant servers
  build against.

That is a separate behaviour change with its own compatibility question,
which is why this plan does not make it.

**Follow-up:** rev 1 recommended an issue, and it is now filed as
Consiliency/pmcp#330.

**For Consiliency/pmcp#330 (N6).** Only the *lower* bounds (`ge=1`, `gt=0`)
are unit-agnostic. If #330 converts seconds to milliseconds by multiplying by
1000 before forwarding, the caller's upper bound must fall to
`floor((2^53 − 1) / 1000)` = 9,007,199,254,740 for both fields. Otherwise
the forwarded value leaves the I-JSON range. Note this on #330.

### 10. Composition with Consiliency/pmcp#297: messages, conflicts and test migrations, in both orders

**Messages.** Every caller-side rule in this plan is one of:
- a `Field` constraint (`ge`, `gt`, `le`, `allow_inf_nan`);
- the gate's type checker.

No custom validator raises. The downstream checks return `_UNUSABLE`, and the
encoder raises with a constant. So the plan adds nothing to #297's
`argument_error(TYPE)` constants.

**If #298 lands first:**
- Gate text for the new bounds is jsonschema's `e.message`, for example
  `-5 is less than the minimum of 1`. That is the echo profile every existing
  numeric bound has on main: the caller's own number, returned to that caller
  only, never logged, with a structural #296 audit record.
- This plan adds **no new echo class**.
- #297 converts these messages when it lands.

**If #297 lands first:** `describe_schema_error` renders the new keywords
from the gateway's own schema node, with no value. For example:
- `$.task.poll_interval: must be greater than 0`;
- `$.task.ttl: must be greater than or equal to 1`;
- for NaN (`type`): `$.task.poll_interval: must be of type number, null`.

**Composition, measured (rev 2, B1).**
1. Two fresh worktrees were made: `pmcp-298-oA` from `0a93265` (#297's
   code) and `pmcp-298-oB` from `89559db`. The resolutions are scripted in
   `resolve.py`, and the test migration in `migrate297.py`.
2. **Order A** applied this plan's patch with `git apply --3way` onto #297.
3. **Order B** applied this plan's patch to main, committed it, then applied
   `git diff 89559db 0a93265` (#297) with `--3way`.
4. Each order then took the resolutions and the test migration below.

The two final trees are **byte-identical** (`diff -r` of `src/` and
`tests/`). Rev 3 regenerated both trees from scratch: the rev 2 trees were
removed with `git worktree remove`. On each, these 12 modules gave
**1576 passed, 0 failed**, in 258 s (A) and 262 s (B); rev 2 gave 1506. The
conflicts, the resolutions and the migration patch are unchanged from rev 2.
#297's `_payload_keys` still finds `ttl`, `pollInterval`, `createdAt` and
`status` in the rewritten `_task_info_from_payload`, because its `.get("…")`
calls are kept. rev 3's `mode="python"` dump in `_read_sse` merges cleanly
with #297's changes there:
- `test_argument_error_echo`, `test_downstream_frame_echo`,
  `test_log_record_scrubber`, `test_parse_error_echo`,
  `test_exception_text_sinks`;
- the new module, `test_gateway_tool_schemas`, `test_client_manager`,
  `test_tools`, `test_scoped_advisor_audit`, `test_server`,
  `test_phase6_tenant_code_mode`.

**Source resolutions.** These are the same in both orders. Only the side
that "lands second" differs.

| File | Conflict | Resolution |
|---|---|---|
| `src/pmcp/server.py` | #297 widens the `from pmcp.tools.handlers import (...)` line (adds `GATEWAY_TOOL_INPUT_MODELS`); #298 adds `from pmcp.tools.schema import GATE_VALIDATOR` after it | Keep #297's import block and add #298's line after it. The `jsonschema.validate(..., cls=GATE_VALIDATOR)` call merges cleanly: #297 rewrites only the `except` body |
| `src/pmcp/types.py` | #297 rewrites `_normalize_task_timestamp`'s ISO branch to `parse_timestamp(...)`; #298 removes that validator (rev 2's `_usable_task_timestamp` replaces it) | Keep #298's `_drop_unusable_hints` and helpers, and drop #297's field validator. In `_usable_task_timestamp`, call `parse_timestamp(candidate, source="task timestamp").timestamp()` inside `try … except (ValueError, OverflowError, OSError): return _UNUSABLE`. `TimestampParseError` subclasses `ParseError(ValueError)`, so it is caught (read in `0a93265:src/pmcp/parsing.py`), and #297's helpers-only rule for parse sites stays satisfied |
| `src/pmcp/argument_errors.py` | — | Add `"finite_number": "must be a finite number"` to `_FIXED_PHRASES`, after `float_parsing`. Without it, an in-process `finite_number` reads `is invalid` |

The sink guard `tests/test_exception_text_sinks.py` passes with the encoder's
`except (ValueError, TypeError, RecursionError)`. That arm renders nothing:
it raises a constant `from None`.

**Test migration** (*Verbatim bodies → #297 test migration*). The rule is to
keep every rejection test rejecting, by moving its input to a `McpTaskInfo`
field that still refuses:
- `task_id`, which must be a string;
- `status_message`, which is `str | None` in the model;
- `raw`, which must be a dict.

Where a test's subject is no longer a rejection, it asserts the new
invariant instead of passing without exercising anything.

| #297 test | Migration |
|---|---|
| `test_log_record_scrubber.py::_validation_error` and its 4 users | `{"task_id": "t", "ttl": {"v": s}}` → `{"task_id": {"v": s}}`; `$.ttl` → `$.task_id` |
| `test_argument_error_echo.py::_handler_validation_errors` | `"created_at": [s]` → `"status_message": [s]`; the expected text's `$.created_at` → `$.status_message` |
| `…::test_exception_text_describes_a_wrapper…`, `…::test_describe_exception_renders_a_grouped…`, `…::test_a_connect_failure_carrying_a_validation_error…` | `ttl` fixtures → `{"task_id": {"v": s}}`; `$.ttl: must be an integer` → `$.task_id: must be a string`. The first was a pass that exercised nothing; it now exercises its assertions |
| `test_downstream_frame_echo.py::test_a_wrapped_handler_keeps_the_wire_code` | `{"task_id": "t", "ttl": s}` → `{"task_id": "t", "raw": s}`. The rejected input must be `s` *itself*: `mcp_data_carries` drops `data` only when it carries the rejected input. With `task_id: {"v": s}`, that case failed 3/3 (measured) |
| `test_downstream_frame_echo.py` `invalid_payload` for `tasks/*` | `"ttl": s` → `"taskId": bad`. A malformed `taskId` is still refused (`tasks/get` → `Task not found: …`, with the caller's ids only) or skipped (`tasks/list`), and it is never rendered. That is stronger than rev 1's input, whose sentinel was accepted and exempted as the response product |
| `test_parse_error_echo.py::test_no_timestamp_site_echoes_its_input` | The `McpTaskInfo(created_at=s)` lambda → `parse_timestamp(s, source="task timestamp")`, which keeps the parse helper's coverage. A new assertion states that the task site no longer raises at all: `created_at is None` and `unusable_fields == ["created_at"]` |
| `test_argument_error_echo.py::_task_positions` and `test_no_downstream_value_reaches_a_response_log_or_audit` | The sweep's subject flips. Before: "a rejected downstream task value renders structurally". After: "**no** key × shape the parser reads is rejected, and no channel but the response carries the value". `_task_positions` returns every (key, shape) except the identifier keys `taskId`/`task_id` (§9: identifiers are kept and audited by design). Per call, the sweep asserts: the downstream was reached; `"validation error" not in` the response; no leak in log, streams, warnings, audit or the audit-event buffer; and a pair differential on every channel except the response and the audit's `redacted_result_digest`, which is a digest of the accepted result by design (`_audit_without_result`). Both exclusions were found by running: `taskId` = string leaked into the event buffer as the task id, and the result digest differed for `createdAt`. Measured: the counts assertion `expected > len(calls) × len(positions)` holds |

**Who carries the migration:**
- **If #297 lands first**, #298's implementation PR carries the migration
  patch and the `finite_number` line, and resolves `types.py` and
  `server.py` as above.
- **If #298 lands first**, #297's PR (314) must carry the same migration
  patch and phrase, and resolve the same two conflicts. Put the table above
  in PR 314's merge notes.

### 11. Consiliency/pmcp#236 piece B (`extra="forbid"`)

Piece B adds `additionalProperties: false` to every object schema. It touches
no numeric field. The only collision is the snapshot fixture: whichever
change lands second regenerates `tests/fixtures/gateway_tool_schemas.json`
with `PMCP_UPDATE_SCHEMA_SNAPSHOT=1` and reviews the diff.

Piece B's own test that the gate and model agree on unknown keys runs through
`_handle_call_tool`. It therefore also runs `GATE_VALIDATOR`, which leaves
`additionalProperties` alone.

## Changes

These are the rev 3 spike's patches, verbatim under *Verbatim bodies*.
- Source and fixture: 5 files, +286 / −51.
- The new test module: 867 lines, 25 tests (285 cases).
- The #297 test migration is a separate patch: 5 files, +63 / −45. It
  applies in whichever order lands second (Design decision 10).

### `src/pmcp/types.py` (modify, +150 / −31)
- `import math`, and `PrivateAttr` added to the pydantic import.
- Constant `MAX_FORWARDED_TASK_NUMBER = 2**53 - 1`, above
  `GatewayArguments`, with a comment citing RFC 7493.
- `TaskMetadataInput.ttl`: `ge=1, le=MAX_FORWARDED_TASK_NUMBER`. This
  replaces the int64 pair and its #236 comment with one that cites both
  issues.
- `TaskMetadataInput.poll_interval`: `gt=0, le=MAX_FORWARDED_TASK_NUMBER,
  allow_inf_nan=False`, with a comment.
- Before `McpTaskInfo`:
  - `_INT64_MAX`;
  - the sentinel `UNUSABLE_TASK_VALUE` (public; the parser passes it for
    a sent `null`, rev 3), aliased `_UNUSABLE`;
  - the checks `_usable_task_ttl`, `_usable_poll_interval`,
    `_usable_task_timestamp` and `_usable_task_status`;
  - the table `_TASK_HINT_CHECKS`.
- `McpTaskInfo`:
  - a docstring stating the `None`/`unusable_fields` meaning;
  - a new field `unusable_fields: list[str] = Field(default_factory=list)`;
  - `@model_validator(mode="before") _drop_unusable_hints`. It replaces the
    old `_normalize_task_timestamp` field validator, whose ISO parse moves
    into `_usable_task_timestamp`.
- `McpTaskRecord`: `_recorded_at: float = PrivateAttr(default_factory=time.time)`
  (and `import time`).
- `_usable_task_timestamp`: a blank string is `_UNUSABLE` (rev 3).
  `_usable_task_status`: a blank string is `_UNUSABLE` (rev 3).
  `_drop_unusable_hints`: one filter to the known names, in table order
  (R2-N1).

### `src/pmcp/tools/schema.py` (modify, +24)
- `import math` and `import jsonschema`.
- `_is_json_number` and `GATE_VALIDATOR`, as in Design decision 4.

### `src/pmcp/server.py` (modify, +4 / −1)
- `from pmcp.tools.schema import GATE_VALIDATOR`.
- `jsonschema.validate(instance=arguments, schema=tool.input_schema,
  cls=GATE_VALIDATOR)` at `server.py:313`.

### `src/pmcp/client/manager.py` (modify, +104 / −17)
- Module level, after `_MAX_LISTING_PAGES`:
  - `class OutboundFrameNotJson(ValueError)`;
  - `_OUTBOUND_NOT_JSON`;
  - `_encode_outbound_frame`, which catches `ValueError`, `TypeError` and
    `RecursionError`, and raises outside the handler (R2-N3).
- `_read_sse`: `model_dump(..., mode="python", ...)` in place of
  `mode="json"` (rev 3, B1).
- `_task_info_from_payload`: a local `sent_null(value, *keys)` passes
  `UNUSABLE_TASK_VALUE` for a hint the payload carries as `null`, except
  `ttl` (rev 3, B2). The `.get("…")` chains are unchanged.
- The three writers (Design decision 8).
- `_record_task`:
  - `now = time.time()`;
  - `created_at` keeps the first usable value;
  - `unusable_fields` is carried over, minus a refilled `created_at`;
  - `updated_at` follows the rule in Design decision 6, using `is None`, not
    `or`;
  - `record._recorded_at = now`.
- `_evict_terminal_tasks`: the sort key is `min(updated_at, _recorded_at)`,
  or `_recorded_at` when `updated_at` is `None` (R2-N2).

### `tests/fixtures/gateway_tool_schemas.json` (regenerate, +4 / −2)
- `poll_interval` gains `"exclusiveMinimum": 0, "maximum": 9007199254740991`.
- `ttl`'s bounds become `"minimum": 1, "maximum": 9007199254740991`.
- `unusable_fields` is *output*, not an argument, so the snapshot does not
  change for it.
- Generate it with `PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest
  tests/test_gateway_tool_schemas.py::test_advertised_schemas_match_snapshot`,
  then review the diff. It must be exactly those six lines.

### `tests/test_gateway_tool_schemas.py` (modify, docstring only, −3 / +3)
- In the module docstring, replace the sentence ending "…passes the gate
  (Consiliency/pmcp#298)." with: "`task.poll_interval` is bounded on both
  sides and finite-checked at the gate by `GATE_VALIDATOR`
  (Consiliency/pmcp#298; `tests/test_task_numeric_bounds.py`)."
- Change "Two classes stay open" to "One class stays open".

### `tests/test_task_numeric_bounds.py` (create, 867 lines)
- **Caller.**
  - `test_gate_and_model_agree_per_field_and_failure_mode`: 8 fields × 11
    modes.
  - `test_gate_rejects_a_boolean_for_every_numeric_argument`.
  - `test_task_hint_bounds`.
  - `test_a_non_finite_poll_interval_off_the_wire_is_refused_at_the_gate`.
- **Schema.**
  - `test_every_advertised_number_is_bounded_both_sides`.
  - `test_every_float_argument_refuses_non_finite_values_in_the_model`.
  - `test_gate_validator_refuses_non_finite_numbers_and_keeps_the_rest`.
- **Downstream (rev 2).**
  - `test_an_unusable_downstream_task_hint_is_dropped_not_raised`: 51
    cases. It now includes `status` (N1) and non-finite numeric *string*
    timestamps (N4), and asserts `unusable_fields`. There are no skips any
    more; rev 1 had 4.
  - `test_a_usable_downstream_task_hint_is_kept`: 10 cases, including
    `ttl: 300000.0` → `300000` (N2), timestamps `-5` and `"5"`, an
    `updated_at` of `0.0`, and a future status.
  - `test_an_absent_ttl_is_not_an_unusable_one` (N3).
  - `test_a_task_with_unusable_hints_is_recorded_and_reported_null` (B2):
    5 cases × the invoke, get, list and cancel paths. It checks the
    **returned/recorded** task, not the normalised info.
  - `test_eviction_orders_a_task_without_a_usable_timestamp_by_when_pmcp_saw_it`
    (B2).
  - **rev 3:** `test_a_sent_null_hint_is_unusable_not_absent` (6 wire
    keys); `test_unusable_fields_holds_only_known_field_names` (R2-N1);
    `test_one_downstream_cannot_keep_its_tasks_by_claiming_a_future_timestamp`
    (R2-N2). The unusable table gains blank and whitespace timestamps and
    a blank status. The recorded-task test grows to 13 cases × 4 paths. It
    runs under a fixed clock (`time.time` → 1234.0), so a substituted "now"
    is visible. It also checks the caller's output (the JSON dump, validated
    again), not only the record.
- **Through the real readers (rev 3, B1).**
  - `test_a_non_finite_downstream_hint_reaches_normalisation_on_every_transport`:
    4 transports (stdio, legacy SSE, streamable SSE event, streamable JSON
    body) × NaN, ±Infinity and ±1e400. Each case sends a real frame through
    the transport's own parse and `_read_sse`, then asserts that the value
    arrived non-finite, the three fields are named, and the record has no
    unlimited `ttl` and no fabricated `updated_at`.
  - `test_a_sent_null_ttl_stays_unlimited_on_every_transport`: the control.
  - `test_created_at_keeps_the_first_usable_value_pmcp_saw`.
- **No loop.**
  - `test_a_downstream_poll_interval_never_drives_a_pmcp_loop`.
  - `test_poll_interval_has_no_consumer_outside_the_allowlist`.
- **Forwarded.**
  - `test_outbound_frames_refuse_anything_but_strict_json` (N7: NaN, ±Inf,
    an object, `bytes`, a cycle; constant message; no `__cause__` and no
    `__context__`, R2-N3).
  - `test_a_request_carrying_nan_is_never_written[stdio|remote]`.
  - `test_a_fire_and_forget_frame_carrying_nan_is_dropped_not_written[stdio|remote]`
    (N4).
  - `test_every_downstream_writer_encodes_through_the_strict_encoder` (N4).
  - `test_forwarded_task_hints_are_spec_shaped`.

### #297's tests and `src/pmcp/argument_errors.py` (modify, whichever lands second; +63 / −45)
- See Design decision 10 and *Verbatim bodies → #297 test migration*.

## Documentation impact

- **`CHANGELOG.md`**, under `## [Unreleased]` → `### Changed`. Create the
  heading if it is absent.

  > **`gateway.invoke`'s `task.ttl` and `task.poll_interval` are bounded, and
  > NaN/Infinity are refused at the gate (see Consiliency/pmcp#298).**
  > - `task.ttl` must be an integer from 1 to 2^53−1. Zero and negative
  >   values, which were forwarded downstream unchanged, are now rejected with
  >   `Input validation error: …`, and so is any value above 2^53−1.
  > - `task.poll_interval` must be a finite number greater than 0 and at most
  >   2^53−1. Zero, negative values, `NaN`, `Infinity` and `-Infinity` are
  >   now rejected; they were previously accepted and forwarded.
  > - The transport gate now treats `NaN` and `±Infinity` as non-numbers for
  >   every numeric argument. Both transports can deliver them, even though
  >   they are not JSON.
  > - A downstream task's `ttl`, `pollInterval`, `createdAt`,
  >   `lastUpdatedAt`/`updatedAt` or `status` that pmcp cannot use is now
  >   reported as `null`, and its field name is listed in the task's new
  >   `unusable_fields` array. "Cannot use" means non-finite, out of range, a
  >   boolean, a string where a number is expected, or a non-string status.
  >   Before, such a value was coerced (`"5"` became 5, `true` became 1),
  >   stored as given, or it failed the call after the downstream had already
  >   created the task.
  >
  >   A `null` `ttl` that is *not* listed in `unusable_fields` keeps its MCP
  >   meaning: unlimited. The task's `raw` holds what the downstream sent,
  >   except that non-finite numbers appear there as `null` too.
  >
  >   A task whose `lastUpdatedAt` was unusable now reports `updatedAt: null`
  >   instead of the time pmcp recorded it. "Unusable" includes a `null`,
  >   blank or whitespace-only value the downstream sent. Only a field the
  >   downstream left out entirely is treated as not sent, and only a `null`
  >   `ttl` keeps a meaning (unlimited).
  >
  >   Over HTTP and SSE, these values are now judged as the server sent them.
  >   Before, `NaN` or `Infinity` arrived as `null`.
  > - pmcp no longer sends a downstream server anything that is not strict
  >   JSON: `NaN`, `±Infinity`, or a value JSON cannot encode. A request whose
  >   `arguments` or task metadata contain one now fails with `outbound frame
  >   is not strict JSON`. A fire-and-forget frame (a reply or a notification
  >   pmcp originates) that contains one is dropped and logged, never written.
  >   Before, stdio servers received a non-JSON `NaN` literal, and HTTP/SSE
  >   servers silently received `null`.

  Never write a closing keyword next to the number.
- **`README.md`.** No change. Its task paragraph (`README.md:1401-1408`)
  names the fields without values or units.
- **`specs/tenant-code-mode-host-contract.md`.** At `:106`, after "PMCP
  forwards them when supplied and may surface returned values to clients",
  add: "A returned value PMCP cannot use is surfaced as `null` and named in
  the task's `unusable_fields`." This is a clarification of what a tenant
  server's clients see, not a contract change. The units are the follow-up in
  Design decision 9, Consiliency/pmcp#330.
- **`SECURITY.md`.** No ledger row changes. Run
  `scripts/check_security_claims.py` and expect `OK`.

## Dependencies & order

1. Independent of `main`. It applies to `89559db` as is.
2. **Composes with #297 in both orders, but not for free.** Whichever lands
   second carries:
   - the two source resolutions (`server.py` import, `types.py` timestamp);
   - the `finite_number` phrase;
   - the #297 test migration.

   All are given and measured in Design decision 10. Prefer landing #297
   first: then this plan's gate messages are structural from the first
   commit, and #298's implementation PR carries the migration. That migration
   is a test-only patch to #297's modules plus one phrase line.
3. It is **independent of #236 piece B.** Only the snapshot collides, and it
   is regenerated by whichever lands second.
4. Within this plan, any order works. The outbound encoder (Design decision 8)
   is separable. If the maintainer declines that behaviour change, drop its
   `client/manager.py` hunks and these four tests:
   - `test_outbound_frames_refuse_anything_but_strict_json`;
   - `test_a_request_carrying_nan_is_never_written`;
   - `test_a_fire_and_forget_frame_carrying_nan_is_dropped_not_written`;
   - `test_every_downstream_writer_encodes_through_the_strict_encoder`.

   Keep the `_record_task` and `_evict_terminal_tasks` hunks.
5. Write the documentation last.

## Verification

Run from a fresh worktree of `origin/main` on dev0 (a team host).

```bash
git -C ~/code/pmcp worktree add -b fix/298-task-bounds "$WORKTREE_ROOT/pmcp-298-fix" origin/main
cd "$WORKTREE_ROOT/pmcp-298-fix"
uv sync --all-extras -p 3.10      # without --all-extras, `uv run` silently uses the system pytest
unset npm_config_cache npm_config_store_dir pnpm_config_store_dir
```

Apply *Verbatim bodies*:
- `git apply` the source patch;
- write the test module;
- edit the docstring and the CHANGELOG by hand;
- if #297 has landed, also apply the migration and the resolutions in Design
  decision 10.

Then:

```bash
# 1. the new module (rev 3 spike: 285 passed, 0 skipped)
uv run pytest tests/test_task_numeric_bounds.py --cov-fail-under=0 -p no:cacheprovider -q
# 2. the suites the change touches (rev 3 spike: 1151 passed, 0 failed)
uv run pytest tests/test_gateway_tool_schemas.py tests/test_tools.py tests/test_client_manager.py \
  tests/test_phase6_tenant_code_mode.py tests/test_scoped_advisor_audit.py tests/test_server.py \
  tests/test_task_numeric_bounds.py --cov-fail-under=0 -p no:cacheprovider -q
# 3. the snapshot: regenerate, then confirm the diff is exactly the six lines in *Changes*
PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest tests/test_gateway_tool_schemas.py::test_advertised_schemas_match_snapshot \
  --cov-fail-under=0 -p no:cacheprovider -q && git diff --stat tests/fixtures/gateway_tool_schemas.json
# 4. CI gates the plan's own list would otherwise miss
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src/pmcp/types.py src/pmcp/tools/schema.py src/pmcp/client/manager.py src/pmcp/server.py
python3 scripts/check_security_claims.py          # expect OK
# 5. if #297 is in the tree: its modules, with the migration (measured, both orders: 1576 passed with the rest)
uv run pytest tests/test_argument_error_echo.py tests/test_downstream_frame_echo.py tests/test_log_record_scrubber.py \
  tests/test_parse_error_echo.py tests/test_exception_text_sinks.py --cov-fail-under=0 -p no:cacheprovider -q
# 6. the full suite: once, detached, with a notifying waiter (memory on dev0 is shared)
nohup uv run pytest -q -p no:cacheprovider > /tmp/pmcp-298-full.log 2>&1 &
```

**Red on main.** The rev 3 module was run against `89559db`'s sources with
an import shim:
- `OutboundFrameNotJson` → a local `ValueError`;
- `_encode_outbound_frame` → `json.dumps`;
- `GATE_VALIDATOR` → `Draft202012Validator`;
- `MAX_FORWARDED_TASK_NUMBER` → `2**53 − 1`.

The result was **179 failed, 106 passed**. Main has no `unusable_fields`, so
every downstream assertion on it fails. The table names the substantive cause
where there is one:

| Test | Failed | Cause on main |
|---|---|---|
| `test_an_unusable_downstream_task_hint_is_dropped_not_raised` | 57 | stored `-5`, `nan` or `1`; `ValidationError`; bare `OverflowError`; `status: 5` raises; a blank timestamp read as absent |
| `test_a_task_with_unusable_hints_is_recorded_and_reported_null` | 52 | raised after the task was created; a NaN/blank/`null` `lastUpdatedAt` recorded as given or as the current time |
| `test_a_non_finite_downstream_hint_reaches_normalisation_on_every_transport` | 20 | the three remote transports deliver `null` (`mode="json"`); stdio stores NaN |
| `test_task_hint_bounds` | 10 | `ttl` 0/−5 accepted; `poll_interval` 0/−0.5/NaN/±Inf/±10^400 accepted at the gate |
| `test_a_usable_downstream_task_hint_is_kept` | 10 | no `unusable_fields` attribute |
| `test_a_sent_null_hint_is_unusable_not_absent` | 6 | a sent `null` reads as absent |
| `test_a_non_finite_poll_interval_off_the_wire_is_refused_at_the_gate` / `test_a_sent_null_ttl_stays_unlimited_on_every_transport` | 4 / 4 | the literals reached the handler / no `unusable_fields` |
| `test_gate_and_model_agree_per_field_and_failure_mode`, `test_a_request_carrying_nan_is_never_written`, `test_a_fire_and_forget_frame_carrying_nan_is_dropped_not_written` | 2 each | the gate passes ±10^400 that the model refuses; NaN frames were written |
| `test_one_downstream_cannot_keep_its_tasks_by_claiming_a_future_timestamp` | 1 | the honest server's newer record is evicted (`or 0.0` key) |
| `test_every_downstream_writer_…`, `test_outbound_frames_…`, `test_every_advertised_number_…`, `test_every_float_argument_…`, `test_gate_validator_…`, `test_forwarded_task_hints_…`, `test_created_at_keeps_…`, `test_an_absent_ttl_…`, `test_unusable_fields_holds_only_known_field_names` | 1 each | `json.dumps`; unbounded `poll_interval`; no `AllowInfNan`; the stock validator; `{"ttl": 0}` forwarded; `created_at` raised or stuck; no `unusable_fields` |

These pass on main, by design:
- the two no-loop guards (Design decision 7; mutant M15 proves they can
  fail);
- the single-server eviction test. On main, a NaN `updated_at` is truthy
  and happens to sort last. Mutants M22 and M29 prove that the eviction
  tests pin the rev 3 rule.

## Acceptance criteria

- [ ] Every caller numeric argument (8 field paths across 4 tools) gets the
  same verdict from the gate and the model for each of the 11 failure modes,
  and accepts its legitimate example. Proven by
  `test_gate_and_model_agree_per_field_and_failure_mode`.
- [ ] `task.ttl` ∈ [1, 2^53−1] and `task.poll_interval` ∈ (0, 2^53−1],
  finite, at both the gate and the model. Proven by `test_task_hint_bounds`.
- [ ] `NaN`, `Infinity`, `-Infinity` and `1e400` read off a real stdio line
  are refused at the gate. Proven by
  `test_a_non_finite_poll_interval_off_the_wire_is_refused_at_the_gate`.
- [ ] The gate refuses `true` for every numeric argument. Proven by
  `test_gate_rejects_a_boolean_for_every_numeric_argument`.
- [ ] Every advertised `number`/`integer` is bounded on both sides, and every
  `float` argument carries `allow_inf_nan=False`. Proven by
  `test_every_advertised_number_is_bounded_both_sides` and
  `test_every_float_argument_refuses_non_finite_values_in_the_model`.
- [ ] The advertised schema equals the model's projection. Proven by the
  existing schema-drift tests, unchanged except the docstring, and the
  snapshot diff is the six lines.
- [ ] A downstream `ttl`, `pollInterval`, `createdAt`, `lastUpdatedAt` or
  `status` that cannot be used becomes `None`, is named in `unusable_fields`,
  and never raises. Usable values are kept, including `ttl: 300000.0` as an
  int. An absent `ttl` is not named. Proven by
  `test_an_unusable_downstream_task_hint_is_dropped_not_raised`,
  `test_a_usable_downstream_task_hint_is_kept` and
  `test_an_absent_ttl_is_not_an_unusable_one`.
- [ ] On the invoke, get, list and cancel paths, the **returned/recorded**
  task reports the unusable field as `null` and names it. That includes
  `updated_at`, with no substitution of the current time. Proven by
  `test_a_task_with_unusable_hints_is_recorded_and_reported_null`.
- [ ] Eviction orders a task without a usable `updated_at` by pmcp's record
  time. `created_at` keeps the first usable value. Proven by
  `test_eviction_orders_…` and `test_created_at_keeps_…`.
- [ ] No downstream `pollInterval` makes pmcp sleep, spin or send more than
  one request per call. Proven by the two no-loop guards.
- [ ] No outbound frame that is not strict JSON is written, by
  `_send_request` or by the fire-and-forget writer, on stdio or remote. Every
  stdio writer encodes through `_encode_outbound_frame`. The refusal is
  value-free. Proven by `test_outbound_frames_refuse_anything_but_strict_json`,
  `test_a_request_carrying_nan_is_never_written`,
  `test_a_fire_and_forget_frame_carrying_nan_is_dropped_not_written` and
  `test_every_downstream_writer_encodes_through_the_strict_encoder`.
- [ ] (rev 3) On stdio, legacy SSE, streamable SSE event and streamable
  JSON body, NaN, ±Infinity and ±1e400 reach task normalisation as
  non-finite floats, and are named unusable. The record has `ttl` and
  `updated_at` null, with no fabricated time. A sent `null` `ttl` stays
  unlimited on every transport. Proven by
  `test_a_non_finite_downstream_hint_reaches_normalisation_on_every_transport`
  and `test_a_sent_null_ttl_stays_unlimited_on_every_transport`.
- [ ] (rev 3) A sent `null` (except `ttl`), a blank or whitespace-only
  string, `{}` or `[]` is never read as "absent", for any of the five
  fields. On the recorded task and the caller's output, it is `null` and
  named, under a fixed clock that shows any substituted time. Proven by
  `test_a_sent_null_hint_is_unusable_not_absent`,
  `test_an_unusable_downstream_task_hint_is_dropped_not_raised` and
  `test_a_task_with_unusable_hints_is_recorded_and_reported_null`.
- [ ] (rev 3) `unusable_fields` holds only the five names, and one server's
  future timestamps cannot keep its records while another server's newer
  record is evicted. Proven by
  `test_unusable_fields_holds_only_known_field_names` and
  `test_one_downstream_cannot_keep_its_tasks_by_claiming_a_future_timestamp`.
- [ ] Composed with #297 in the order they land: the 12 modules of Design
  decision 10 pass with the migration, and none of the migrated tests passes
  without exercising its assertions.
- [ ] All 32 mutants are killed.
- [ ] Verification steps 2 to 6 pass. The CHANGELOG entry is present, with
  no closing keyword.

## Mutation table

All 32 mutants were measured on the rev 3 spike with `mutants3.py`. Each run
applies one string replacement, runs `tests/test_task_numeric_bounds.py`
(285 cases), then restores the file from a copy saved in memory. **All 32
are killed.** Afterwards the tree was byte-identical to the spike:
`diff <(git diff) spike-r3.patch` was empty.

Rev 3 adds M26–M29, M31 and M32. M22, M23 and M30 are re-pointed at rev 3's
code. Rev 2's M30/M33 pair has gone: with *two* filters on
`unusable_fields`, each mutant was equivalent and survived. Rev 3 keeps a
single filter, and M30 now removes it.

| # | Rule | Mutant | Failed (measured) |
|---|---|---|---|
| M1 | `ttl` lower bound | `ge=1` → `ge=0` | 2: `test_task_hint_bounds`, `test_forwarded_task_hints_are_spec_shaped` |
| M2 | `ttl` I-JSON upper bound | `le=` → int64 max | 1: `test_task_hint_bounds` |
| M3 | `poll_interval` lower bound | drop `gt=0` | 6: `test_task_hint_bounds`, `test_gate_and_model_agree…`, `test_every_advertised_number…`, `test_forwarded…` |
| M4 | `poll_interval` upper bound | drop `le=` | 3: `test_every_advertised_number…`, `test_gate_and_model_agree…`, `test_task_hint_bounds` |
| M5 | the model refuses non-finite values | drop `allow_inf_nan=False` | 1: `test_every_float_argument_refuses_non_finite_values_in_the_model` only. pydantic's `gt`/`le` already refuse NaN/±Inf; the class test is the pin |
| M6 | the gate uses the finite validator | drop `cls=GATE_VALIDATOR` | 1: `…off_the_wire…[NaN]` only. The advertised bounds already refuse ±Infinity and 1e400 (N5) |
| M7 | the finite type check | `_is_json_number` → the stock check | 4: `test_gate_validator…`, `…off_the_wire…`, `test_gate_and_model_agree…`, `test_task_hint_bounds` |
| M8 | downstream `ttl` range/type | `_usable_task_ttl` keeps any value | 38: `…dropped_not_raised`, `…recorded…`, `test_an_absent_ttl…`, `…on_every_transport` |
| M9 | downstream `pollInterval` finite | drop `math.isfinite(number) and` | 9: `…dropped_not_raised`, `…on_every_transport` |
| M10 | timestamp overflow | drop the `try/except OverflowError` | 6: `…dropped_not_raised`, `…recorded…[created-overflow]` |
| M11 | numeric timestamp finite | return `number` without `isfinite` | 31: `…dropped_not_raised`, `…recorded…`, `…on_every_transport`, `test_created_at_keeps…` |
| M12 | numeric-string timestamp finite | string branch without `isfinite` | 6: `…dropped_not_raised` |
| M13 | strict outbound JSON | `allow_nan=False` → default | 5: `test_outbound_frames…`, `test_a_request_carrying_nan…`, `test_a_fire_and_forget…` |
| M14 | the remote request path is checked | drop `_encode_outbound_frame(request)` (remote) | 1: `test_a_request_carrying_nan_is_never_written[remote]` |
| M15 | no loop on a downstream hint | `await asyncio.sleep(task_info.poll_interval or 0)` in `get_task` | 6: both no-loop guards |
| M16 | fire-and-forget remote check | drop it | 1: `test_a_fire_and_forget…[remote]` |
| M17 | fire-and-forget stdio | back to `json.dumps` | 2: `test_a_fire_and_forget…[stdio]`, `test_every_downstream_writer…` |
| M18 | `_send_initialize` stdio | back to `json.dumps` | 1: `test_every_downstream_writer_encodes_through_the_strict_encoder` |
| M19 | `status` guard | keep any value | 15: `…dropped_not_raised[status=…]`, `…recorded…[status-*]` |
| M20 | whole-number float `ttl` kept | drop the `is_integer()` → `int` conversion | 1: `test_a_usable_downstream_task_hint_is_kept[ttl=300000.0]` |
| M21 | no substitution of `updated_at` | `if updated_at is None and "updated_at" not in …` → `if not updated_at:` | 44: `…recorded…`, `…on_every_transport` |
| M22 | eviction fallback | sort key → `updated_at or 0.0` | 2: both eviction tests |
| M23 | an unusable field is named | never add it to `dropped` | 141: every test that asserts `unusable_fields` |
| M24 | the encoder fails closed on non-JSON types | `except ValueError` only | 1: `test_outbound_frames_refuse_anything_but_strict_json` |
| M25 | `created_at` keeps the first usable value | `existing is not None` only | 1: `test_created_at_keeps_the_first_usable_value_pmcp_saw` |
| **M26** | **B1: remote values survive to normalisation** | `_read_sse` dump back to `mode="json"` | **15**: `…on_every_transport` (all 5 literals × the 3 remote transports; stdio passes, as it should) |
| **M27** | **B2: a blank timestamp is unusable** | `return None` for a blank string | **16**: `…dropped_not_raised[…=''/' \n\t ']`, `…recorded…[created-blank, updated-blank, updated-whitespace]` |
| **M28** | **B2: a sent `null` is unusable** (except `ttl`) | `sent_null` never substitutes | **18**: `test_a_sent_null_hint_is_unusable_not_absent`, `…stays_unlimited_on_every_transport`, `…recorded…[*-null]` |
| **M29** | **R2-N2: eviction uses the earlier time** | key `updated_at` (no `min`) | **1**: `test_one_downstream_cannot_keep_its_tasks_by_claiming_a_future_timestamp` |
| M30 | R2-N1: one filter to the known names | final `[n for n in _TASK_HINT_CHECKS if n in dropped]` → `sorted(dropped)` | 21: `test_unusable_fields_holds_only_known_field_names`, `…on_every_transport` (order) |
| **M31** | **R2-N3: nothing attached to the refusal** | `raise … from None` inside the `except` | **1**: `test_outbound_frames_refuse_anything_but_strict_json` (`__context__` is not None) |
| **M32** | **B2 class: a blank status is unusable** | keep a blank string | **6**: `…dropped_not_raised[status=''/'  ']`, `…recorded…[status-blank]` |

The implementer re-runs the same 32 mutants on the final tree. Restore each
file from a saved copy, never with `git checkout --`, and confirm that
`git diff --stat` matches the pre-mutation state.

## Non-goals

- **Units** (seconds versus the spec's milliseconds). See Design decision 9;
  it is a follow-up, Consiliency/pmcp#330.
- **NaN inside a downstream tool *result*.** pmcp passes it through, and
  `server.py:464` writes it with `json.dumps(result)` as `NaN` inside a text
  block. That is the downstream's content, not a task hint. Since rev 3 this
  is true on remote transports too: before, they silently delivered `null`
  (*Transport table*). A strict serializer there would change every result
  path, and that belongs in its own issue.
- **A clamp helper for a polling loop that does not exist.** See Design
  decision 7.
- **`Strict()` on numeric fields.** See Design decision 5.
- **Echo rendering.** That is Consiliency/pmcp#297. This plan states its
  touch points and its test migration (Design decision 10).

## Unverified

- **The behaviour of a real Node stdio downstream receiving `NaN`.** The
  "hangs until timeout" claim rests on `JSON.parse` refusing the bare literal
  and on the spike's fake, which wrote and then timed out. No live Node
  server was run.
- **#297 beyond `0a93265`.** The composition was measured against #297's
  rev 12 code (`wip/297-code` @ `0a93265`). A later #297 revision that adds
  task-field fixtures needs the same search, as Design decision 10 describes.

**Measured in rev 3:**
- the full suite, once, alone and detached, with the npm variables unset, on
  the rev 3 spike: **5214 passed, 3 skipped, 80 deselected, 0 failed**
  (595 s; rev 2: 5144);
- the 7 touched modules: **1151 passed**;
- `ruff check`, `ruff format --check` and `mypy src/pmcp` (52 files): clean;
- both #297 orders: **1576 passed** each, on byte-identical trees.

## Execution Policy

- execute: effort=medium.
- reason: a contained change (4 source files, about 230 lines), but on the
  trust edge, in both directions, with:
  - one maintainer-visible behaviour change, the strict outbound JSON of
    Design decision 8, which is separable;
  - one new public output field, `unusable_fields`;
  - a test migration in #297's modules, carried by whichever PR lands
    second.
- Re-run the mutation table, the 7-module run, ruff and mypy before
  requesting review. If #297 is in the tree, re-run its 5 modules as well.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the source patch below (between the ```` fences) as `298-src.patch`,
   then run `git apply 298-src.patch` on `89559db`. It also regenerates the
   snapshot fixture.
2. Write the test module below to `tests/test_task_numeric_bounds.py`.
3. Edit the docstring of `tests/test_gateway_tool_schemas.py` and add the
   `CHANGELOG.md` entry by hand, as in *Changes* and *Documentation impact*.
4. **Only when #297 is in the tree** (in either order):
   - resolve `server.py` and `types.py` as in Design decision 10's table;
   - then `git apply` the *#297 test migration* patch, which is relative to
     #297's `0a93265` versions of those files.

### Patch — `src/pmcp/{types.py, tools/schema.py, server.py, client/manager.py}` and `tests/fixtures/gateway_tool_schemas.json`

````diff
diff --git a/src/pmcp/client/manager.py b/src/pmcp/client/manager.py
index 57a563e..e59d7e9 100644
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -39,6 +39,7 @@ from pmcp.subscriptions import CatalogEventSink
 from pmcp.validation import normalized_executable_name
 from pmcp.types import (
     LocalMcpServerConfig,
+    UNUSABLE_TASK_VALUE,
     McpTaskInfo,
     McpTaskRecord,
     PromptArgumentInfo,
@@ -189,6 +190,33 @@ _RECONCILE_RERUN_DEBOUNCE_S = 0.25
 _MAX_LISTING_PAGES = 500
 
 
+class OutboundFrameNotJson(ValueError):
+    """An outbound frame is not strict JSON (Consiliency/pmcp#298)."""
+
+
+_OUTBOUND_NOT_JSON = "outbound frame is not strict JSON"
+
+
+def _encode_outbound_frame(payload: dict[str, Any]) -> str:
+    """One JSON-RPC frame as strict JSON, or a value-free refusal.
+
+    NaN and +-Infinity are not JSON: stdio would write them as bare literals a
+    strict peer cannot parse, and the SDK's HTTP and SSE clients would
+    silently send them as ``null``. A value ``json`` cannot encode at all
+    (``TypeError``), a cycle (``ValueError``) or runaway nesting
+    (``RecursionError``) is refused the same way, on every transport, with a
+    message that carries nothing from the frame (Consiliency/pmcp#298).
+    """
+    try:
+        return json.dumps(payload, allow_nan=False)
+    except (ValueError, TypeError, RecursionError):
+        pass
+    # Raised OUTSIDE the handler: `from None` would still leave the json
+    # error attached as `__context__`, whose text can carry the value
+    # (`...: nan` on 3.12+, a dict key on 3.13).
+    raise OutboundFrameNotJson(_OUTBOUND_NOT_JSON)
+
+
 class DownstreamError(Exception):
     """A JSON-RPC `error` object returned by a downstream MCP server.
 
@@ -1681,21 +1709,41 @@ class ClientManager:
         if not isinstance(task_id, str) or not task_id:
             return None
         status_message = payload.get("statusMessage", payload.get("status_message"))
+        status = payload.get("status")
+        created_at = payload.get("createdAt", payload.get("created_at"))
+        updated_at = payload.get(
+            "updatedAt",
+            payload.get(
+                "updated_at",
+                payload.get("lastUpdatedAt", payload.get("last_updated_at")),
+            ),
+        )
         poll_interval = payload.get("pollInterval", payload.get("poll_interval"))
+
+        def sent_null(value: Any, *keys: str) -> Any:
+            # A JSON `null` the downstream SENT is not an absent value: only
+            # `ttl: null` means something in MCP ("unlimited"). For the other
+            # hints it is a present-but-unusable value, so it must not read as
+            # "not sent" -- `_record_task` would substitute the current time
+            # for an absent `updatedAt` (Consiliency/pmcp#298).
+            if value is None and any(key in payload for key in keys):
+                return UNUSABLE_TASK_VALUE
+            return value
+
         return McpTaskInfo(
             task_id=task_id,
-            status=payload.get("status"),
+            status=sent_null(status, "status"),
             status_message=status_message if isinstance(status_message, str) else None,
-            created_at=payload.get("createdAt", payload.get("created_at")),
-            updated_at=payload.get(
+            created_at=sent_null(created_at, "createdAt", "created_at"),
+            updated_at=sent_null(
+                updated_at,
                 "updatedAt",
-                payload.get(
-                    "updated_at",
-                    payload.get("lastUpdatedAt", payload.get("last_updated_at")),
-                ),
+                "updated_at",
+                "lastUpdatedAt",
+                "last_updated_at",
             ),
             ttl=payload.get("ttl"),
-            poll_interval=poll_interval,
+            poll_interval=sent_null(poll_interval, "pollInterval", "poll_interval"),
             raw=payload,
         )
 
@@ -1708,22 +1756,41 @@ class ClientManager:
         requestor_context: dict[str, Any] | None = None,
     ) -> McpTaskRecord:
         existing = self._tasks.get((server_name, task_info.task_id))
+        now = time.time()
+        # created_at: the first usable value pmcp saw (Consiliency/pmcp#298).
+        created_at = (
+            existing.created_at
+            if existing is not None and existing.created_at is not None
+            else task_info.created_at
+        )
+        unusable = [
+            name
+            for name in task_info.unusable_fields
+            if not (name == "created_at" and created_at is not None)
+        ]
+        # updated_at: the downstream's usable value; None when it was unusable
+        # (named in `unusable_fields`); pmcp's observation time only when the
+        # downstream sent none at all -- `is None`, not `or`, so a usable 0.0
+        # is kept (Consiliency/pmcp#298).
+        updated_at = task_info.updated_at
+        if updated_at is None and "updated_at" not in task_info.unusable_fields:
+            updated_at = now
         record = McpTaskRecord(
             task_id=task_info.task_id,
             status=task_info.status,
             status_message=task_info.status_message,
-            created_at=task_info.created_at
-            if existing is None
-            else existing.created_at,
-            updated_at=task_info.updated_at or time.time(),
+            created_at=created_at,
+            updated_at=updated_at,
             ttl=task_info.ttl,
             poll_interval=task_info.poll_interval,
+            unusable_fields=unusable,
             raw=task_info.raw,
             server_name=server_name,
             tool_id=tool_id or (existing.tool_id if existing else None),
             requestor_context=requestor_context
             or (existing.requestor_context if existing else None),
         )
+        record._recorded_at = now
         self._tasks[(server_name, task_info.task_id)] = record
         self._evict_terminal_tasks()
         return record
@@ -1743,7 +1810,18 @@ class ClientManager:
         excess = len(terminal) - self._max_terminal_tasks
         if excess <= 0:
             return
-        terminal.sort(key=lambda item: (item[1].updated_at or 0.0, item[0]))
+        # Oldest first. A downstream's `updated_at` can only make its record
+        # OLDER than when pmcp recorded it, never newer: the key is the earlier
+        # of the two, so a far-future `lastUpdatedAt` cannot keep one server's
+        # records while another's are evicted (Consiliency/pmcp#298).
+        terminal.sort(
+            key=lambda item: (
+                min(item[1].updated_at, item[1]._recorded_at)
+                if item[1].updated_at is not None
+                else item[1]._recorded_at,
+                item[0],
+            )
+        )
         for key, _record in terminal[:excess]:
             self._tasks.pop(key, None)
 
@@ -3166,9 +3244,16 @@ class ClientManager:
                     raise message
 
                 try:
+                    # `mode="python"`, not "json": the SDK already parsed the
+                    # frame (`validate_json`, which reads `NaN`, `Infinity` and
+                    # `1e400` as floats), and a JSON-mode dump would turn those
+                    # into `None` -- indistinguishable from a sent `null` (MCP's
+                    # "unlimited" `ttl`). Python mode hands the dispatcher the
+                    # same values the stdio path's `json.loads` does, so task
+                    # normalisation sees them (Consiliency/pmcp#298).
                     payload = message.message.model_dump(
                         by_alias=True,
-                        mode="json",
+                        mode="python",
                         exclude_none=True,
                     )
                 except Exception as e:
@@ -3220,12 +3305,13 @@ class ClientManager:
             if managed.is_remote:
                 if managed.write_stream is None:
                     return
+                _encode_outbound_frame(payload)
                 msg = mcp_types.jsonrpc_message_adapter.validate_python(payload)
                 await managed.write_stream.send(SessionMessage(msg))
             else:
                 if not managed.process or not managed.process.stdin:
                     return
-                data = json.dumps(payload) + "\n"
+                data = _encode_outbound_frame(payload) + "\n"
                 managed.process.stdin.write(data.encode())
                 await managed.process.stdin.drain()
         except Exception as e:
@@ -3417,13 +3503,14 @@ class ClientManager:
                 # JSONRPCNotification | JSONRPCResponse | JSONRPCError), not a
                 # pydantic model, so it has no .model_validate(); construct via
                 # its published TypeAdapter instead.
+                _encode_outbound_frame(request)
                 msg = mcp_types.jsonrpc_message_adapter.validate_python(request)
                 await managed.write_stream.send(SessionMessage(msg))
             else:
                 if not managed.process or not managed.process.stdin:
                     raise RuntimeError("Process not running")
 
-                data = json.dumps(request) + "\n"
+                data = _encode_outbound_frame(request) + "\n"
                 managed.process.stdin.write(data.encode())
                 await managed.process.stdin.drain()
 
@@ -3547,7 +3634,7 @@ class ClientManager:
             msg = mcp_types.jsonrpc_message_adapter.validate_python(notification)
             await managed.write_stream.send(SessionMessage(msg))
         elif managed.process and managed.process.stdin:
-            data = json.dumps(notification) + "\n"
+            data = _encode_outbound_frame(notification) + "\n"
             managed.process.stdin.write(data.encode())
             await managed.process.stdin.drain()
 
diff --git a/src/pmcp/server.py b/src/pmcp/server.py
index 0a6ef28..ed00259 100644
--- a/src/pmcp/server.py
+++ b/src/pmcp/server.py
@@ -70,6 +70,7 @@ from pmcp.scoped_advisor_audit import (
 from pmcp.subscriptions import BusCatalogEventSink
 from pmcp.summary import generate_capability_summary
 from pmcp.tools.handlers import GatewayTools, get_gateway_tool_definitions
+from pmcp.tools.schema import GATE_VALIDATOR
 from pmcp.types import (
     DescriptionsCache,
     GatewayDiagnosticsInfo,
@@ -310,7 +311,9 @@ class GatewayServer:
         audited_arguments: dict[str, Any] | None = None
         if tool is not None and allowed:
             try:
-                jsonschema.validate(instance=arguments, schema=tool.input_schema)
+                jsonschema.validate(
+                    instance=arguments, schema=tool.input_schema, cls=GATE_VALIDATOR
+                )
             except jsonschema.ValidationError as e:
                 try:
                     if self._scoped_advisor_audit is not None:
diff --git a/src/pmcp/tools/schema.py b/src/pmcp/tools/schema.py
index 4315cd7..162dd75 100644
--- a/src/pmcp/tools/schema.py
+++ b/src/pmcp/tools/schema.py
@@ -20,14 +20,38 @@ accepting ``null`` exactly where the model does.
 from __future__ import annotations
 
 from copy import deepcopy
+import math
 from typing import Any
 
+import jsonschema
 from pydantic import BaseModel
 
 #: Advertised for gateway tools that take no arguments at all.
 NO_ARGUMENTS_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}
 
 _REF_PREFIX = "#/$defs/"
+
+
+def _is_json_number(checker: Any, instance: Any) -> bool:
+    """JSON Schema's ``number``, minus NaN and +-Infinity (Consiliency/pmcp#298).
+
+    Neither is a JSON number (RFC 8259 s6), yet Python's ``json`` and pydantic's
+    parser both read ``NaN``/``Infinity``/``1e400`` off the wire, and no bound
+    keyword refuses NaN: every comparison with it is false. ``integer`` needs no
+    change -- its check is ``float.is_integer()``, which both already fail.
+    """
+    return jsonschema.Draft202012Validator.TYPE_CHECKER.is_type(
+        instance, "number"
+    ) and not (isinstance(instance, float) and not math.isfinite(instance))
+
+
+#: The validator the transport gate runs every advertised ``inputSchema`` with.
+GATE_VALIDATOR = jsonschema.validators.extend(
+    jsonschema.Draft202012Validator,
+    type_checker=jsonschema.Draft202012Validator.TYPE_CHECKER.redefine(
+        "number", _is_json_number
+    ),
+)
 _NULL_SCHEMA: dict[str, Any] = {"type": "null"}
 
 
diff --git a/src/pmcp/types.py b/src/pmcp/types.py
index 7dc8e7c..0f16043 100644
--- a/src/pmcp/types.py
+++ b/src/pmcp/types.py
@@ -4,10 +4,19 @@ from __future__ import annotations
 
 from datetime import datetime
 from enum import Enum
+import math
 import re
+import time
 from typing import Annotated, Any, Literal
 
-from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
+from pydantic import (
+    BaseModel,
+    ConfigDict,
+    Field,
+    PrivateAttr,
+    field_validator,
+    model_validator,
+)
 
 from pmcp.validation import is_valid_package_name, version_separator_index
 
@@ -77,6 +86,11 @@ DEFAULT_AUTH_STATE_SEMANTICS: dict[AuthState, AuthStateSemanticsInfo] = {
 }
 
 
+#: The largest integer every JSON peer reads exactly (RFC 7493 s2.2, I-JSON):
+#: the upper bound on numeric task hints pmcp forwards downstream.
+MAX_FORWARDED_TASK_NUMBER = 2**53 - 1
+
+
 class GatewayArguments(BaseModel):
     """Base for every model that parses arguments an agent sends to a gateway
     tool, and for every model nested inside one. The advertised ``inputSchema``
@@ -518,8 +532,94 @@ class ServerStatus(BaseModel):
     avg_response_time_ms: float | None = None  # Rolling average response time
 
 
+_INT64_MAX = 2**63 - 1
+
+#: Stands in for a downstream task value pmcp cannot use. The downstream
+#: parser also passes it for a hint the downstream sent as JSON `null`.
+UNUSABLE_TASK_VALUE: Any = object()
+_UNUSABLE = UNUSABLE_TASK_VALUE
+
+
+def _usable_task_ttl(value: Any) -> Any:
+    """A non-bool integer in [0, int64], or a finite whole-number float there
+    (``300000.0``: JSON Schema calls it an integer too)."""
+    if type(value) is float and math.isfinite(value) and value.is_integer():
+        value = int(value)
+    if type(value) is int and 0 <= value <= _INT64_MAX:
+        return value
+    return _UNUSABLE
+
+
+def _usable_poll_interval(value: Any) -> Any:
+    """A non-bool, finite number greater than 0."""
+    if type(value) in (int, float):
+        try:
+            number = float(value)
+        except OverflowError:
+            return _UNUSABLE
+        if math.isfinite(number) and number > 0:
+            return number
+    return _UNUSABLE
+
+
+def _usable_task_timestamp(value: Any) -> Any:
+    """Epoch seconds from a finite number, a numeric string or an ISO 8601
+    string; a datetime as is."""
+    if isinstance(value, datetime):
+        return value.timestamp()
+    if isinstance(value, bool):
+        return _UNUSABLE
+    if isinstance(value, int | float):
+        try:
+            number = float(value)
+        except OverflowError:
+            return _UNUSABLE
+        return number if math.isfinite(number) else _UNUSABLE
+    if isinstance(value, str):
+        candidate = value.strip()
+        if not candidate:
+            # sent, but empty: unusable, not "not sent" (Consiliency/pmcp#298)
+            return _UNUSABLE
+        try:
+            number = float(candidate)
+        except ValueError:
+            pass
+        else:
+            return number if math.isfinite(number) else _UNUSABLE
+        if candidate.endswith("Z"):
+            candidate = f"{candidate[:-1]}+00:00"
+        try:
+            return datetime.fromisoformat(candidate).timestamp()
+        except (ValueError, OverflowError, OSError):
+            return _UNUSABLE
+    return _UNUSABLE
+
+
+def _usable_task_status(value: Any) -> Any:
+    """Any non-blank string: unknown future statuses are kept (the tenant
+    contract); a blank one says nothing and is unusable."""
+    return value if isinstance(value, str) and value.strip() else _UNUSABLE
+
+
+_TASK_HINT_CHECKS: dict[str, Any] = {
+    "status": _usable_task_status,
+    "created_at": _usable_task_timestamp,
+    "updated_at": _usable_task_timestamp,
+    "ttl": _usable_task_ttl,
+    "poll_interval": _usable_poll_interval,
+}
+
+
 class McpTaskInfo(BaseModel):
-    """Public view of a downstream MCP task."""
+    """Public view of a downstream MCP task.
+
+    A downstream value pmcp cannot use is reported as ``None`` and its field
+    is named in ``unusable_fields`` (Consiliency/pmcp#298). So a ``None``
+    ``ttl`` not named there keeps the MCP meaning "unlimited", and one named
+    there means the downstream sent something pmcp could not read. Nothing is
+    refused: refusing would fail a call whose task the downstream had already
+    created, and pmcp only reports these values.
+    """
 
     task_id: str
     status: McpTaskStatus | str | None = None
@@ -528,29 +628,36 @@ class McpTaskInfo(BaseModel):
     updated_at: float | None = None
     ttl: int | None = None
     poll_interval: float | None = None
+    unusable_fields: list[str] = Field(default_factory=list)
     raw: dict[str, Any] = Field(default_factory=dict)
 
-    @field_validator("created_at", "updated_at", mode="before")
+    @model_validator(mode="before")
     @classmethod
-    def _normalize_task_timestamp(cls, value: Any) -> float | None:
-        if value is None:
-            return None
-        if isinstance(value, datetime):
-            return value.timestamp()
-        if isinstance(value, int | float):
-            return float(value)
-        if isinstance(value, str):
-            candidate = value.strip()
-            if not candidate:
-                return None
-            try:
-                return float(candidate)
-            except ValueError:
-                pass
-            if candidate.endswith("Z"):
-                candidate = f"{candidate[:-1]}+00:00"
-            return datetime.fromisoformat(candidate).timestamp()
-        return value
+    def _drop_unusable_hints(cls, data: Any) -> Any:
+        if not isinstance(data, dict):
+            return data
+        out = dict(data)
+        unusable = out.get("unusable_fields")
+        dropped = (
+            {name for name in unusable if isinstance(name, str)}
+            if isinstance(unusable, list)
+            else set()
+        )
+        for name, check in _TASK_HINT_CHECKS.items():
+            value = out.get(name)
+            if value is None:
+                continue
+            usable = check(value)
+            if usable is _UNUSABLE:
+                out[name] = None
+                dropped.add(name)
+            else:
+                out[name] = usable
+        if dropped or "unusable_fields" in out:
+            # Only the known field names, in table order: value-free by
+            # construction, whatever a caller of the model passed in.
+            out["unusable_fields"] = [n for n in _TASK_HINT_CHECKS if n in dropped]
+        return out
 
 
 class McpTaskRecord(McpTaskInfo):
@@ -560,6 +667,9 @@ class McpTaskRecord(McpTaskInfo):
     tool_id: str | None = None
     local_request_id: str | None = None
     requestor_context: dict[str, Any] | None = None
+    #: When pmcp last recorded this task (epoch seconds; not public). The
+    #: eviction order falls back to it when ``updated_at`` is None.
+    _recorded_at: float = PrivateAttr(default_factory=time.time)
 
 
 class TaskMetadataInput(GatewayArguments):
@@ -573,18 +683,27 @@ class TaskMetadataInput(GatewayArguments):
     )
     ttl: int | None = Field(
         default=None,
-        # int64, both sides: pydantic already refuses a float outside it
-        # (`int_parsing_size`), so advertise the range and let the gate refuse
-        # `1e20` and `-1e20` too, rather than passing them on to the model
-        # (Consiliency/pmcp#236, board rounds 4 and 5)
-        # -2**63 + 1: `float(-2**63)` is exactly representable, and pydantic
-        # refuses it (board round 6, N6-1), so the inclusive bound stops one short
-        ge=-9_223_372_036_854_775_807,
-        le=9_223_372_036_854_775_807,
+        # [1, 2**53 - 1] (Consiliency/pmcp#298): a zero or negative retention is
+        # meaningless, and the value is forwarded downstream as the MCP
+        # `TaskMetadata.ttl` integer, which a JavaScript peer reads as a double
+        # -- above 2**53 - 1 it is no longer the integer the caller sent. The
+        # bound also keeps the gate and the model agreeing on floats outside
+        # int64 (Consiliency/pmcp#236).
+        ge=1,
+        le=MAX_FORWARDED_TASK_NUMBER,
         description="Requested task TTL in seconds",
     )
     poll_interval: float | None = Field(
-        default=None, description="Seconds between task status polls"
+        default=None,
+        # finite and positive, both sides bounded (Consiliency/pmcp#298): NaN and
+        # +-Infinity are not JSON numbers, and an unbounded float lets a JSON
+        # integer of |n| >= 2**1024 - 2**970 past the gate to a model that
+        # cannot hold it. `allow_inf_nan` is not projected into the schema;
+        # the gate's validator refuses non-finite numbers itself.
+        gt=0,
+        le=MAX_FORWARDED_TASK_NUMBER,
+        allow_inf_nan=False,
+        description="Seconds between task status polls",
     )
     requestor_context: dict[str, Any] | None = Field(
         default=None, description="Opaque requestor context forwarded downstream"
diff --git a/tests/fixtures/gateway_tool_schemas.json b/tests/fixtures/gateway_tool_schemas.json
index ebb9a0b..cf9be95 100644
--- a/tests/fixtures/gateway_tool_schemas.json
+++ b/tests/fixtures/gateway_tool_schemas.json
@@ -292,6 +292,8 @@
      },
      "poll_interval": {
       "description": "Seconds between task status polls",
+      "exclusiveMinimum": 0,
+      "maximum": 9007199254740991,
       "type": [
        "number",
        "null"
@@ -307,8 +309,8 @@
      },
      "ttl": {
       "description": "Requested task TTL in seconds",
-      "maximum": 9223372036854775807,
-      "minimum": -9223372036854775807,
+      "maximum": 9007199254740991,
+      "minimum": 1,
       "type": [
        "integer",
        "null"
````

### File — `tests/test_task_numeric_bounds.py`

````python
"""Numeric task hints and every other numeric gateway argument: bounded, finite,
and agreed between the transport gate and the model (Consiliency/pmcp#298).

Three directions:
- caller -> pmcp: the gate refuses what the model refuses, per field and per
  failure mode, including NaN/+-Infinity, which no JSON Schema bound refuses;
- downstream -> pmcp: an unusable task hint is dropped to None, field by field,
  and never fails the call that created the task;
- pmcp -> downstream: no frame carries a non-finite number.
"""

from __future__ import annotations

import ast
import asyncio
import math
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jsonschema
from mcp.types import jsonrpc_message_adapter
import pytest
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

from pmcp.client.manager import (
    ClientManager,
    ManagedClient,
    OutboundFrameNotJson,
    _encode_outbound_frame,
)
from pmcp.tools.handlers import GATEWAY_TOOL_INPUT_MODELS, get_gateway_tool_definitions
from pmcp.tools.schema import GATE_VALIDATOR
from pmcp.types import (
    MAX_FORWARDED_TASK_NUMBER,
    GatewayArguments,
    LocalMcpServerConfig,
    McpTaskInfo,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    ServerStatus,
    ServerStatusEnum,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "pmcp"
BIG = 10**400

# Every numeric argument a caller can send, as (tool, path, accepted example).
NUMERIC_ARGUMENTS: list[tuple[str, tuple[str, ...], Any]] = [
    ("gateway.catalog_search", ("limit",), 20),
    ("gateway.search_registry", ("limit",), 5),
    ("gateway.invoke", ("options", "timeout_ms"), 30000),
    ("gateway.invoke", ("options", "max_output_chars"), 1000),
    ("gateway.invoke", ("task", "ttl"), 300),
    ("gateway.invoke", ("task", "poll_interval"), 2.5),
    ("gateway.tasks_result", ("options", "timeout_ms"), 30000),
    ("gateway.tasks_result", ("options", "max_output_chars"), 1000),
]

FAILURE_MODES: dict[str, Any] = {
    "negative": -5,
    "zero": 0,
    "nan": float("nan"),
    "inf": float("inf"),
    "-inf": float("-inf"),
    "huge_int": BIG,
    "-huge_int": -BIG,
    "1e20": 1e20,
    "2**53": 2**53,
    "2**63": 2**63,
    "fraction": 2.5,
}

BASE_ARGUMENTS = {
    "gateway.catalog_search": {},
    "gateway.search_registry": {"query": "x"},
    "gateway.invoke": {"tool_id": "a::b"},
    "gateway.tasks_result": {"server_name": "s", "task_id": "t"},
}


def _schema(tool: str) -> dict[str, Any]:
    return next(
        t for t in get_gateway_tool_definitions() if t.name == tool
    ).input_schema


def _with(tool: str, path: tuple[str, ...], value: Any) -> dict[str, Any]:
    args: dict[str, Any] = dict(BASE_ARGUMENTS[tool])
    node = args
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value
    return args


def _gate_accepts(tool: str, args: dict[str, Any]) -> bool:
    try:
        jsonschema.validate(args, _schema(tool), cls=GATE_VALIDATOR)
    except jsonschema.ValidationError:
        return False
    return True


def _model_accepts(tool: str, args: dict[str, Any]) -> bool:
    model = GATEWAY_TOOL_INPUT_MODELS[tool]
    assert model is not None
    try:
        model.model_validate(args)
    except ValidationError:
        return False
    return True


# --- caller -> pmcp ------------------------------------------------------------


@pytest.mark.parametrize("mode", list(FAILURE_MODES))
@pytest.mark.parametrize(
    ("tool", "path", "ok"),
    NUMERIC_ARGUMENTS,
    ids=[f"{t}:{'.'.join(p)}" for t, p, _ in NUMERIC_ARGUMENTS],
)
def test_gate_and_model_agree_per_field_and_failure_mode(
    tool: str, path: tuple[str, ...], ok: Any, mode: str
) -> None:
    """For every numeric argument and failure mode, the gate's verdict is the
    model's: whatever passes the gate the model accepts, so no rejection
    reaches the model to be rendered from its value."""
    args = _with(tool, path, FAILURE_MODES[mode])
    assert _gate_accepts(tool, args) == _model_accepts(tool, args), (tool, path, mode)
    good = _with(tool, path, ok)
    assert _gate_accepts(tool, good) and _model_accepts(tool, good)


@pytest.mark.parametrize(
    ("tool", "path", "ok"),
    NUMERIC_ARGUMENTS,
    ids=[f"{t}:{'.'.join(p)}" for t, p, _ in NUMERIC_ARGUMENTS],
)
def test_gate_rejects_a_boolean_for_every_numeric_argument(
    tool: str, path: tuple[str, ...], ok: Any
) -> None:
    """`true` is not a number to the gate. The model's lax mode would coerce it
    to 1, so the gate is the only line, and it holds for every field."""
    assert not _gate_accepts(tool, _with(tool, path, True))


@pytest.mark.parametrize(
    ("path", "value", "accepted"),
    [
        (("task", "ttl"), 0, False),
        (("task", "ttl"), -5, False),
        (("task", "ttl"), 1, True),
        (("task", "ttl"), MAX_FORWARDED_TASK_NUMBER, True),
        (("task", "ttl"), MAX_FORWARDED_TASK_NUMBER + 1, False),
        (("task", "poll_interval"), 0, False),
        (("task", "poll_interval"), -0.5, False),
        (("task", "poll_interval"), float("nan"), False),
        (("task", "poll_interval"), float("inf"), False),
        (("task", "poll_interval"), float("-inf"), False),
        (("task", "poll_interval"), BIG, False),
        (("task", "poll_interval"), -BIG, False),
        (("task", "poll_interval"), 1e-300, True),
        (("task", "poll_interval"), 0.1, True),
        (("task", "poll_interval"), MAX_FORWARDED_TASK_NUMBER, True),
    ],
)
def test_task_hint_bounds(path: tuple[str, ...], value: Any, accepted: bool) -> None:
    args = _with("gateway.invoke", path, value)
    assert _gate_accepts("gateway.invoke", args) is accepted
    assert _model_accepts("gateway.invoke", args) is accepted


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400"])
@pytest.mark.asyncio
async def test_a_non_finite_poll_interval_off_the_wire_is_refused_at_the_gate(
    literal: str,
) -> None:
    """The SDK's own parser reads `NaN`/`Infinity`/`1e400` off the wire as a
    float, so the gate meets it; the gate refuses it before any handler runs."""
    from mcp.types import CallToolResult

    from tests.test_gateway_tool_schemas import _call_through_gate

    line = (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":'
        '"gateway.invoke","arguments":{"tool_id":"a::b","task":{"poll_interval":'
        + literal
        + "}}}}"
    )
    message = jsonrpc_message_adapter.validate_json(line, by_name=False)
    arguments = message.params["arguments"]  # type: ignore[union-attr,index]
    assert not math.isfinite(arguments["task"]["poll_interval"])
    result = await _call_through_gate("gateway.invoke", arguments)
    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    assert result.content[0].text.startswith("Input validation error:")  # type: ignore[union-attr]


# --- the advertised schema -------------------------------------------------------


def _numbers(node: Any, path: str = "") -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        kinds = node.get("type")
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if "number" in kinds or "integer" in kinds:
            found.append((path, node))
        for name, prop in (node.get("properties") or {}).items():
            found += _numbers(prop, f"{path}.{name}")
        if isinstance(node.get("items"), dict):
            found += _numbers(node["items"], f"{path}[]")
    return found


def test_every_advertised_number_is_bounded_both_sides() -> None:
    """Integers already were (Consiliency/pmcp#236); a `number` the gate leaves
    unbounded passes a JSON integer no float can hold to the model."""
    unbounded = [
        f"{tool.name}{path}"
        for tool in get_gateway_tool_definitions()
        for path, node in _numbers(tool.input_schema)
        if not ({"minimum", "exclusiveMinimum"} & set(node))
        or not ({"maximum", "exclusiveMaximum"} & set(node))
    ]
    assert unbounded == []


def _gateway_argument_models() -> list[type[BaseModel]]:
    seen: list[type[BaseModel]] = []

    def walk(model: type[BaseModel]) -> None:
        if model in seen:
            return
        seen.append(model)
        for field in model.model_fields.values():
            for arg in (*getattr(field.annotation, "__args__", ()), field.annotation):
                if isinstance(arg, type) and issubclass(arg, GatewayArguments):
                    walk(arg)

    for model in GATEWAY_TOOL_INPUT_MODELS.values():
        if model is not None:
            walk(model)
    return seen


def _is_float_field(field: FieldInfo) -> bool:
    annotation = field.annotation
    return annotation is float or float in getattr(annotation, "__args__", ())


def test_every_float_argument_refuses_non_finite_values_in_the_model() -> None:
    """`allow_inf_nan` has no JSON Schema projection, so the advertised schema
    cannot pin it: this does. The gate's side is `GATE_VALIDATOR`."""
    missing = []
    for model in _gateway_argument_models():
        for name, field in model.model_fields.items():
            if not _is_float_field(field):
                continue
            flags = [getattr(m, "allow_inf_nan", None) for m in field.metadata]
            if False not in flags:
                missing.append(f"{model.__name__}.{name}")
    assert missing == []


def test_gate_validator_refuses_non_finite_numbers_and_keeps_the_rest() -> None:
    schema = {"type": "number"}
    for value in (float("nan"), float("inf"), float("-inf")):
        assert not GATE_VALIDATOR(schema).is_valid(value)
    for value in (0, 1, -1.5, 1e308, 2**80):
        assert GATE_VALIDATOR(schema).is_valid(value)
    assert not GATE_VALIDATOR(schema).is_valid(True)
    GATE_VALIDATOR.check_schema(_schema("gateway.invoke"))


# --- downstream -> pmcp ---------------------------------------------------------

# (wire key, attribute, unusable value) -- every value the downstream parser
# must drop, never raise on. Usable look-alikes are in the next table.
_HINTS = [("ttl", "ttl"), ("pollInterval", "poll_interval")]
_STAMPS = [("createdAt", "created_at"), ("lastUpdatedAt", "updated_at")]
_NUMERIC_BAD: dict[str, Any] = {
    "nan": float("nan"),
    "inf": float("inf"),
    "-inf": float("-inf"),
    "bool": True,
    "huge_int": BIG,
    "list": [1],
    "object": {"v": 1},
}
UNUSABLE: list[tuple[str, str, Any]] = [
    *[(w, a, v) for w, a in _HINTS + _STAMPS for v in _NUMERIC_BAD.values()],
    *[(w, a, v) for w, a in _HINTS for v in (-5, "5", "")],
    ("ttl", "ttl", 2.5),
    ("ttl", "ttl", 1e300),
    ("ttl", "ttl", 2**63),
    ("pollInterval", "poll_interval", 0),
    *[
        (w, a, v)
        for w, a in _STAMPS
        for v in ("nan", "1e400", "-inf", "2026-13-99T", "", " \n\t ")
    ],
    *[
        ("status", "status", v)
        for v in (5, float("nan"), True, ["working"], {"v": 1}, "", "  ")
    ],
]
USABLE: list[tuple[str, str, Any, Any]] = [
    ("ttl", "ttl", 0, 0),
    ("ttl", "ttl", 60000, 60000),
    ("ttl", "ttl", 300000.0, 300000),
    ("pollInterval", "poll_interval", 2, 2.0),
    ("pollInterval", "poll_interval", 2.5, 2.5),
    ("createdAt", "created_at", "2025-11-25T10:00:00Z", 1764064800.0),
    ("createdAt", "created_at", -5, -5.0),
    ("createdAt", "created_at", "5", 5.0),
    ("lastUpdatedAt", "updated_at", 0.0, 0.0),
    ("status", "status", "some_future_status", "some_future_status"),
]


@pytest.mark.parametrize(
    ("wire", "attr", "value"), UNUSABLE, ids=[f"{w}={v!r}"[:40] for w, _, v in UNUSABLE]
)
def test_an_unusable_downstream_task_hint_is_dropped_not_raised(
    wire: str, attr: str, value: Any
) -> None:
    info = ClientManager()._task_info_from_payload(
        {"taskId": "t1", "status": "working", wire: value}
    )
    assert info is not None
    assert getattr(info, attr) is None
    assert info.unusable_fields == [attr]
    again = McpTaskInfo.model_validate(info.model_dump(mode="json"))
    assert again.unusable_fields == [attr] and getattr(again, attr) is None


@pytest.mark.parametrize(
    ("wire", "attr", "value", "kept"),
    USABLE,
    ids=[f"{w}={v!r}"[:40] for w, _, v, _ in USABLE],
)
def test_a_usable_downstream_task_hint_is_kept(
    wire: str, attr: str, value: Any, kept: Any
) -> None:
    info = ClientManager()._task_info_from_payload(
        {"taskId": "t1", "status": "working", wire: value}
    )
    assert info is not None
    assert getattr(info, attr) == kept and type(getattr(info, attr)) is type(kept)
    assert info.unusable_fields == []


def test_an_absent_ttl_is_not_an_unusable_one() -> None:
    """MCP's `ttl: null` means unlimited: only a value pmcp could not read is
    named in `unusable_fields`."""
    manager = ClientManager()
    sent_null = manager._task_info_from_payload({"taskId": "t", "ttl": None})
    absent = manager._task_info_from_payload({"taskId": "t"})
    unusable = manager._task_info_from_payload({"taskId": "t", "ttl": -1})
    assert sent_null is not None and absent is not None and unusable is not None
    assert sent_null.ttl is None and sent_null.unusable_fields == []
    assert absent.ttl is None and absent.unusable_fields == []
    assert unusable.ttl is None and unusable.unusable_fields == ["ttl"]


# A JSON null the downstream SENT, for a hint where MCP gives null no meaning,
# is unusable -- not "absent" (rev 3, board round 2 B2).
SENT_NULLS = [
    ("status", "status"),
    ("createdAt", "created_at"),
    ("created_at", "created_at"),
    ("lastUpdatedAt", "updated_at"),
    ("updatedAt", "updated_at"),
    ("pollInterval", "poll_interval"),
]


@pytest.mark.parametrize(("wire", "attr"), SENT_NULLS)
def test_a_sent_null_hint_is_unusable_not_absent(wire: str, attr: str) -> None:
    info = ClientManager()._task_info_from_payload({"taskId": "t", wire: None})
    assert info is not None
    assert getattr(info, attr) is None and info.unusable_fields == [attr]


def test_unusable_fields_holds_only_known_field_names() -> None:
    """Value-free by construction: whatever a caller passes in, only the five
    field names survive, in table order."""
    info = McpTaskInfo(
        task_id="t",
        unusable_fields=["sk-SECRET-VALUE", "ttl", {"k": 1}, "status", "ttl"],
    )
    assert info.unusable_fields == ["status", "ttl"]
    assert McpTaskInfo(task_id="t", unusable_fields="ttl").unusable_fields == []


def _task_server(manager: ClientManager, reply: dict[str, Any]) -> ManagedClient:
    from pmcp.types import RiskHint, ToolInfo

    manager._tools["tasks::run"] = ToolInfo(
        tool_id="tasks::run",
        server_name="tasks",
        tool_name="run",
        description="d",
        short_description="d",
        input_schema={"type": "object"},
        tags=[],
        risk_hint=RiskHint.LOW,
        execution={"taskSupport": "optional"},
    )
    managed = ManagedClient(
        config=ResolvedServerConfig(
            name="tasks",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http", url="https://t.example/mcp"
            ),
        ),
        is_remote=True,
        write_stream=MagicMock(),
        status=ServerStatus(
            name="tasks",
            status=ServerStatusEnum.ONLINE,
            tool_count=1,
            server_capabilities={"tasks": {}},
        ),
    )
    manager._clients["tasks"] = managed
    manager._send_request = AsyncMock(return_value=reply)  # type: ignore[method-assign]
    return managed


RECORDED_CASES: dict[str, dict[str, Any]] = {
    "ttl-fraction": {"ttl": 1.5},
    "poll-nan": {"pollInterval": float("nan")},
    "poll-null": {"pollInterval": None},
    "created-overflow": {"createdAt": BIG},
    "created-blank": {"createdAt": ""},
    "updated-nan": {"lastUpdatedAt": float("nan")},
    "updated-blank": {"lastUpdatedAt": ""},
    "updated-whitespace": {"lastUpdatedAt": " \n\t "},
    "updated-null": {"lastUpdatedAt": None},
    "updated-object": {"lastUpdatedAt": {}},
    "updated-array": {"lastUpdatedAt": []},
    "status-number": {"status": 5},
    "status-blank": {"status": ""},
}


@pytest.mark.parametrize("case", list(RECORDED_CASES))
@pytest.mark.parametrize("path", ["invoke", "get", "list", "cancel"])
@pytest.mark.asyncio
async def test_a_task_with_unusable_hints_is_recorded_and_reported_null(
    case: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before #298, `ttl: 1.5`, `createdAt: 10**400` or `status: 5` raised
    after the downstream had created the task: the caller got an error and pmcp
    lost the task. Now it is recorded, and the RETURNED task -- what a caller
    sees -- reports the field as null and names it in `unusable_fields`."""
    ((wire, value),) = RECORDED_CASES[case].items()
    attr = {
        "ttl": "ttl",
        "pollInterval": "poll_interval",
        "createdAt": "created_at",
        "lastUpdatedAt": "updated_at",
        "status": "status",
    }[wire]
    task: dict[str, Any] = {"taskId": "t1", "status": "working", wire: value}
    # A fixed clock: a substituted "now" would show up as exactly this value.
    monkeypatch.setattr("time.time", lambda: 1234.0)
    manager = ClientManager()
    if path == "list":
        _task_server(manager, {"tasks": [task]})
        listed = await manager.list_tasks("tasks")
        returned: Any = listed["tasks"][0]
    else:
        _task_server(manager, {"task": task})
        if path == "invoke":
            await manager.call_tool("tasks::run", {}, task={"ttl": 300})
        elif path == "get":
            await manager.get_task("tasks", "t1")
        else:
            manager._record_task("tasks", McpTaskInfo(task_id="t1", status="working"))
            await manager.cancel_task("tasks", "t1")
        returned = manager.get_task_record("tasks", "t1")
        assert returned is not None
        returned = returned.model_dump()
    assert returned[attr] is None, returned
    assert attr in returned["unusable_fields"], returned
    if attr != "updated_at":
        assert returned["updated_at"] == 1234.0  # absent: pmcp's time, as on main
    # What a caller receives: the output path's JSON dump, validated again.
    output = McpTaskInfo.model_validate(
        {k: v for k, v in returned.items() if k in McpTaskInfo.model_fields}
    ).model_dump(mode="json")
    assert output[attr] is None and attr in output["unusable_fields"], output
    assert 1234.0 not in (output["created_at"], output[attr]), output


@pytest.mark.asyncio
async def test_eviction_orders_a_task_without_a_usable_timestamp_by_when_pmcp_saw_it() -> (
    None
):
    """A terminal task whose `lastUpdatedAt` was unusable sorts by pmcp's own
    record time, not as the oldest possible (`or 0.0`): an old task with a
    real timestamp is evicted first."""
    manager = ClientManager()
    manager._max_terminal_tasks = 1
    manager._record_task(
        "s", McpTaskInfo(task_id="old", status="completed", updated_at=1.0)
    )
    manager._record_task(
        "s", McpTaskInfo(task_id="new", status="completed", updated_at=float("nan"))
    )
    assert [t.task_id for t in manager.get_tracked_tasks("s")] == ["new"]


def test_created_at_keeps_the_first_usable_value_pmcp_saw() -> None:
    """A first payload with an unusable `createdAt` leaves it null; a later
    usable one fills it, and the field is no longer named unusable."""
    manager = ClientManager()
    first = manager._record_task(
        "s", McpTaskInfo(task_id="t", status="working", created_at=float("nan"))
    )
    assert first.created_at is None and first.unusable_fields == ["created_at"]
    second = manager._record_task(
        "s", McpTaskInfo(task_id="t", status="working", created_at=5.0)
    )
    assert second.created_at == 5.0 and second.unusable_fields == []
    third = manager._record_task(
        "s", McpTaskInfo(task_id="t", status="working", created_at=float("nan"))
    )
    assert third.created_at == 5.0 and third.unusable_fields == []


def test_one_downstream_cannot_keep_its_tasks_by_claiming_a_future_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The terminal-task cap is shared by every server. A downstream that
    claims `lastUpdatedAt: 1e300` must not make its records outlive another
    server's newer ones: the eviction key is the earlier of the downstream's
    time and pmcp's record time (rev 3, board round 2 R2-N2)."""
    clock = {"now": 100.0}
    monkeypatch.setattr("time.time", lambda: clock["now"])
    manager = ClientManager()
    manager._max_terminal_tasks = 2
    for task_id in ("b1", "b2"):
        manager._record_task(
            "liar",
            McpTaskInfo(task_id=task_id, status="completed", updated_at=1e300),
        )
        clock["now"] += 1
    clock["now"] = 200.0
    manager._record_task(
        "honest", McpTaskInfo(task_id="a1", status="completed", updated_at=200.0)
    )
    kept = {(t.server_name, t.task_id) for t in manager.get_tracked_tasks()}
    assert ("honest", "a1") in kept, kept
    assert ("liar", "b1") not in kept, kept


# --- downstream -> pmcp through the REAL readers (rev 3, board round 2 B1) ----
#
# The mocks above hand `_send_request`'s result straight to the parser. These
# go through each transport's own parse step -- stdio's line handler, the real
# `sse_client` over an httpx MockTransport, and streamable HTTP's SSE-event and
# JSON-body handlers -- and `_read_sse`, which used to JSON-dump every message
# and so turned NaN/Infinity/1e400 into `null` before pmcp saw them.

_NON_FINITE_LITERALS = ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"]
_TRANSPORTS = ["stdio", "legacy-sse", "streamable-sse-event", "streamable-json-body"]


def _task_frame(literal: str) -> str:
    return (
        '{"jsonrpc":"2.0","id":1,"result":{"task":{"taskId":"t","status":"working",'
        f'"ttl":{literal},"pollInterval":{literal},"lastUpdatedAt":{literal}}}}}}}'
    )


async def _through_transport(transport: str, frame: str) -> dict[str, Any]:
    """The result a pending request resolves to when `frame` arrives."""
    import httpx2
    from mcp.shared._context_streams import create_context_streams
    from mcp.shared.message import SessionMessage

    from tests.test_client_manager import _remote_reader_client

    manager = ClientManager()
    managed, pending = _remote_reader_client()
    if transport == "stdio":
        manager._handle_stdout_line("srv", managed, frame.encode() + b"\n", 0.0)
        return pending.future.result()
    manager._clients["srv"] = managed
    managed.status.status = ServerStatusEnum.ONLINE
    pending.future.add_done_callback(
        lambda _f: setattr(managed.status, "status", ServerStatusEnum.OFFLINE)
    )
    if transport == "legacy-sse":
        from mcp.client.sse import sse_client

        events = "event: endpoint\ndata: /messages?session_id=abc\n\n"
        events += f"event: message\ndata: {frame}\n\n"

        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=events.encode(),
            )

        def factory(
            headers: Any = None, timeout: Any = None, auth: Any = None
        ) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(
                transport=httpx2.MockTransport(handler),
                headers=headers,
                timeout=timeout,
                auth=auth,
            )

        async with sse_client(
            "http://example.invalid/sse", httpx_client_factory=factory
        ) as (read_stream, _write_stream):
            await asyncio.wait_for(
                manager._read_sse("srv", managed, read_stream), timeout=5
            )
        return pending.future.result()
    from httpx2 import ServerSentEvent
    from mcp.client.streamable_http import StreamableHTTPTransport

    http = StreamableHTTPTransport("http://example.invalid/mcp")
    writer, reader = create_context_streams[SessionMessage | Exception](10)

    async def produce() -> None:
        async with writer:
            if transport == "streamable-sse-event":
                await http._handle_sse_event(ServerSentEvent(data=frame), writer)
            else:
                response = httpx2.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=frame.encode(),
                )
                await http._handle_json_response(response, writer, request_id=1)

    await asyncio.wait_for(
        asyncio.gather(produce(), manager._read_sse("srv", managed, reader)),
        timeout=5,
    )
    return pending.future.result()


@pytest.mark.parametrize("literal", _NON_FINITE_LITERALS)
@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.asyncio
async def test_a_non_finite_downstream_hint_reaches_normalisation_on_every_transport(
    transport: str, literal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NaN, +-Infinity and +-1e400 arrive as non-finite floats on every
    transport, are named unusable, and the recorded task neither reads `ttl` as
    unlimited nor gets a fabricated `updated_at`."""
    monkeypatch.setattr("time.time", lambda: 1234.0)
    result = await _through_transport(transport, _task_frame(literal))
    task = result["task"]
    assert not math.isfinite(task["ttl"]), (transport, literal, task)
    manager = ClientManager()
    info = manager._task_info_from_payload(task)
    assert info is not None
    assert info.unusable_fields == ["updated_at", "ttl", "poll_interval"]
    record = manager._record_task("srv", info).model_dump()
    assert record["ttl"] is None and record["updated_at"] is None, record
    assert record["unusable_fields"] == ["updated_at", "ttl", "poll_interval"]


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.asyncio
async def test_a_sent_null_ttl_stays_unlimited_on_every_transport(
    transport: str,
) -> None:
    """The control: a JSON `null` `ttl` arrives as None and is NOT unusable;
    a `null` `lastUpdatedAt` is."""
    frame = (
        '{"jsonrpc":"2.0","id":1,"result":{"task":{"taskId":"t","status":"working",'
        '"ttl":null,"lastUpdatedAt":null}}}'
    )
    task = (await _through_transport(transport, frame))["task"]
    info = ClientManager()._task_info_from_payload(task)
    assert info is not None and info.ttl is None
    assert info.unusable_fields == ["updated_at"], (transport, task)


# --- no polling loop ------------------------------------------------------------


@pytest.mark.parametrize("poll", [0, float("nan"), float("inf"), 1e-300, -1])
@pytest.mark.asyncio
async def test_a_downstream_poll_interval_never_drives_a_pmcp_loop(
    poll: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pmcp does not poll: `tasks_get`/`tasks_result` each send one request and
    return, whatever `pollInterval` the downstream suggests."""
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(delay: float, *args: Any, **kwargs: Any) -> Any:
        sleeps.append(delay)
        return await real_sleep(0)

    monkeypatch.setattr("pmcp.client.manager.asyncio.sleep", recording_sleep)
    manager = ClientManager()
    reply = {"task": {"taskId": "t1", "status": "working", "pollInterval": poll}}
    _task_server(manager, reply)
    await asyncio.wait_for(manager.get_task("tasks", "t1"), timeout=2)
    await asyncio.wait_for(manager.get_task_result("tasks", "t1"), timeout=2)
    assert manager._send_request.await_count == 2  # type: ignore[attr-defined]
    assert sleeps == []


def test_poll_interval_has_no_consumer_outside_the_allowlist() -> None:
    """Reading `.poll_interval` anywhere but where pmcp forwards or records it
    is a new consumer. A loop that sleeps on a downstream hint must clamp it to
    a finite, positive range first (see the #298 plan); add it here only then."""
    allowed = {
        ("client/manager.py", "_task_wire_metadata"),
        ("client/manager.py", "_record_task"),
    }
    found = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(func):
                if isinstance(node, ast.Attribute) and node.attr == "poll_interval":
                    found.add((str(path.relative_to(SRC)), func.name))
    assert found <= allowed, found - allowed


# --- pmcp -> downstream ---------------------------------------------------------


class _NotJson:
    pass


def test_outbound_frames_refuse_anything_but_strict_json() -> None:
    assert _encode_outbound_frame({"a": 1.5, "b": [1, 2]}) == '{"a": 1.5, "b": [1, 2]}'
    cycle: list[Any] = []
    cycle.append(cycle)
    for value in (float("nan"), float("inf"), float("-inf"), _NotJson(), b"x", cycle):
        with pytest.raises(OutboundFrameNotJson) as raised:
            _encode_outbound_frame({"params": {"arguments": {"x": [value]}}})
        assert str(raised.value) == "outbound frame is not strict JSON"
        # raised outside the handler: nothing attached that could carry the
        # value (`...: nan` on 3.12+, a dict key on 3.13)
        assert raised.value.__cause__ is None and raised.value.__context__ is None


def _managed(remote: bool) -> ManagedClient:
    if remote:
        return ManagedClient(
            config=ResolvedServerConfig(
                name="s",
                source="custom",
                config=RemoteMcpServerConfig(
                    type="streamable-http", url="https://s.example/mcp"
                ),
            ),
            is_remote=True,
            write_stream=AsyncMock(),
            status=ServerStatus(name="s", status=ServerStatusEnum.ONLINE, tool_count=0),
        )
    process = MagicMock()
    process.stdin.write = MagicMock()
    process.stdin.drain = AsyncMock()
    return ManagedClient(
        config=ResolvedServerConfig(
            name="s", source="custom", config=LocalMcpServerConfig(command="x")
        ),
        process=process,
        status=ServerStatus(name="s", status=ServerStatusEnum.ONLINE, tool_count=0),
    )


def _written(managed: ManagedClient) -> int:
    if managed.is_remote:
        return managed.write_stream.send.await_count  # type: ignore[union-attr]
    return managed.process.stdin.write.call_count  # type: ignore[union-attr]


@pytest.mark.parametrize("remote", [False, True], ids=["stdio", "remote"])
@pytest.mark.asyncio
async def test_a_request_carrying_nan_is_never_written(remote: bool) -> None:
    manager = ClientManager()
    managed = _managed(remote)
    with pytest.raises(OutboundFrameNotJson):
        await manager._send_request(
            managed,
            "tools/call",
            {"name": "t", "arguments": {"x": float("nan")}},
            timeout_ms=1000,
        )
    assert _written(managed) == 0
    assert managed.pending_requests == {}


@pytest.mark.parametrize("remote", [False, True], ids=["stdio", "remote"])
@pytest.mark.asyncio
async def test_a_fire_and_forget_frame_carrying_nan_is_dropped_not_written(
    remote: bool,
) -> None:
    """`_send_message_to_downstream` swallows write failures by contract: a
    non-JSON frame is not written and does not raise. A clean one is."""
    manager = ClientManager()
    managed = _managed(remote)
    await manager._send_message_to_downstream(
        managed, {"jsonrpc": "2.0", "id": 1, "result": {"x": float("nan")}}
    )
    assert _written(managed) == 0
    await manager._send_message_to_downstream(
        managed, {"jsonrpc": "2.0", "id": 1, "result": {"x": 1.5}}
    )
    assert _written(managed) == 1


def test_every_downstream_writer_encodes_through_the_strict_encoder() -> None:
    """Every function in `client/manager.py` that writes to a stdio pipe
    encodes with `_encode_outbound_frame`, never `json.dumps` -- including
    `_send_initialize`, whose frame is a constant no test can make non-JSON."""
    tree = ast.parse((SRC / "client" / "manager.py").read_text())
    writers = {}
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        calls = [
            ast.unparse(node.func)
            for node in ast.walk(func)
            if isinstance(node, ast.Call)
        ]
        if any(c.endswith("stdin.write") for c in calls):
            writers[func.name] = calls
    assert {"_send_message_to_downstream", "_send_request", "_send_initialize"} <= set(
        writers
    ), sorted(writers)
    for name, calls in writers.items():
        assert "_encode_outbound_frame" in calls, name
        assert "json.dumps" not in calls, name


def test_forwarded_task_hints_are_spec_shaped() -> None:
    manager = ClientManager()
    wire = manager._task_wire_metadata({"ttl": 300, "poll_interval": 2.5})
    assert wire == {"ttl": 300, "pollInterval": 2.5}
    assert type(wire["ttl"]) is int and math.isfinite(wire["pollInterval"])
    _encode_outbound_frame(wire)
    for bad in (
        {"ttl": 0},
        {"ttl": -1},
        {"poll_interval": float("nan")},
        {"poll_interval": 0},
    ):
        with pytest.raises(ValidationError):
            manager._task_wire_metadata(bad)
````

### Patch — #297 test migration and the `finite_number` phrase (relative to `0a93265`)

````diff
diff --git a/src/pmcp/argument_errors.py b/src/pmcp/argument_errors.py
index 7a5d1b1..9906ce8 100644
--- a/src/pmcp/argument_errors.py
+++ b/src/pmcp/argument_errors.py
@@ -96,6 +96,7 @@ _FIXED_PHRASES: dict[str, str] = {
     "int_from_float": "must be an integer",
     "float_type": "must be a number",
     "float_parsing": "must be a number",
+    "finite_number": "must be a finite number",
     "bool_type": "must be a boolean",
     "bool_parsing": "must be a boolean",
     "dict_type": "must be an object",
diff --git a/tests/test_argument_error_echo.py b/tests/test_argument_error_echo.py
index 7058d8b..cef1c58 100644
--- a/tests/test_argument_error_echo.py
+++ b/tests/test_argument_error_echo.py
@@ -749,7 +749,7 @@ def _handler_validation_errors(s: str) -> list[BaseException]:
     pydantic error from an argument model, and a jsonschema error."""
     errors: list[BaseException] = []
     for model, data in (
-        (McpTaskInfo, {"task_id": {s: s}, "created_at": [s]}),
+        (McpTaskInfo, {"task_id": {s: s}, "status_message": [s]}),
         (InvokeInput, {"tool_id": {s: s}, "options": s, "_meta": [s]}),
     ):
         try:
@@ -781,7 +781,7 @@ class _RaisingTools:
 #: What `exception_text` makes of each of `_handler_validation_errors`.
 _HANDLER_ERROR_TEXT = (
     re.compile(
-        r"2 validation errors for McpTaskInfo: \$\.task_id: must be a string; \$\.created_at: "
+        r"2 validation errors for McpTaskInfo: \$\.task_id: must be a string; \$\.status_message: "
     ),
     re.compile(r"3 validation errors for InvokeInput: \$\.tool_id: must be a string; "),
     # `x` is no name pmcp's models declare, so it reads as `*`.
@@ -1050,13 +1050,13 @@ def test_exception_text_describes_a_wrapper_that_embeds_a_validation_error() ->
     s = _SENTINELS[1]
     try:
         try:
-            McpTaskInfo.model_validate({"task_id": "t", "ttl": s})
+            McpTaskInfo.model_validate({"task_id": {"v": s}})
         except ValidationError as inner:
             raise RuntimeError(f"task parse failed: {inner}") from inner
     except RuntimeError as outer:
         text = exception_text(outer)
         assert text.startswith(
-            "RuntimeError: 1 validation error for McpTaskInfo: $.ttl: must be an integer"
+            "RuntimeError: 1 validation error for McpTaskInfo: $.task_id: must be a string"
         ), text
         assert not any(form in text for form in _forbidden(s))
         assert safe_exc_info(outer) is None
@@ -1082,14 +1082,14 @@ def test_describe_exception_renders_a_grouped_validation_error_structurally(
         from exceptiongroup import ExceptionGroup as group_type
     s = _FAMILIES[family][1]
     with pytest.raises(ValidationError) as raised:
-        McpTaskInfo.model_validate({"task_id": "t", "ttl": {"v": s}})
+        McpTaskInfo.model_validate({"task_id": {"v": s}})
     leaf = raised.value
     for group in (
         group_type("unhandled errors in a TaskGroup", [leaf]),
         group_type("unhandled errors in a TaskGroup", [RuntimeError("boom"), leaf]),
     ):
         text = describe_exception(group)
-        assert "validation error for McpTaskInfo: $.ttl: must be an integer" in text
+        assert "validation error for McpTaskInfo: $.task_id: must be a string" in text
         assert not any(form in text for form in _forbidden(s)), text
 
 
@@ -1150,8 +1150,9 @@ def _bad_values(s: str) -> dict[str, Any]:
 
 
 def _task_positions() -> list[tuple[str, str]]:
-    """(payload key, bad-value shape) pairs the real task parser rejects with
-    a `ValidationError` -- found by running it, not by listing fields."""
+    """(payload key, bad-value shape) for every key the real task parser reads
+    -- found from its source, not by listing fields. Since Consiliency/pmcp#298
+    the parser drops a value it cannot use and raises on none of them."""
     from pmcp.client.manager import ClientManager
 
     manager = ClientManager()
@@ -1165,9 +1166,9 @@ def _task_positions() -> list[tuple[str, str]]:
                 if key not in ("taskId", "task_id")
                 else {key: value}
             )
-            try:
-                manager._task_info_from_payload(payload)
-            except ValidationError:
+            manager._task_info_from_payload(payload)  # never raises (#298)
+            if key not in ("taskId", "task_id"):
+                # an identifier pmcp keeps and audits by design (section 9)
                 positions.append((key, shape))
     assert len(positions) > 10, positions
     return positions
@@ -1242,6 +1243,14 @@ def _task_server(
     return server, audit_path, state
 
 
+def _audit_without_result(observed: _Observed) -> list[dict[str, Any]]:
+    """The audit records minus the digest of the accepted result (#298)."""
+    return [
+        {k: v for k, v in record.items() if k != "redacted_result_digest"}
+        for record in observed.audit
+    ]
+
+
 def _task_calls() -> list[tuple[str, dict[str, Any], str]]:
     """(gateway tool, arguments, downstream method) for every gateway tool
     that parses a downstream task payload."""
@@ -1278,22 +1287,18 @@ async def test_no_downstream_value_reaches_a_response_log_or_audit(
     positions = _task_positions()
     parser = server._client_manager._task_info_from_payload
 
-    def rejects(key: str, value: Any) -> bool:
-        try:
-            parser({"taskId": "t", "status": "working", key: value})
-        except ValidationError:
-            return True
-        return False
-
     rejected = expected = 0
     for name, arguments, method in _task_calls():
         for key, shape in positions:
             for family, sentinels in _FAMILIES.items():
-                # A value the parser accepts (a digits-only `createdAt` is a
-                # number) is downstream data pmcp returns by design, not a
-                # rejection; only rejected values are this sweep's subject.
-                if not all(rejects(key, _bad_values(s)[shape]) for s in sentinels):
-                    continue
+                # Since #298 every value is accepted (a usable one kept, an
+                # unusable one dropped), and the task's `raw` returns it to the
+                # caller by design: the response is the product. The log, the
+                # streams, the warnings and the audit must not carry it.
+                for s in sentinels:
+                    parser(
+                        {"taskId": "t", "status": "working", key: _bad_values(s)[shape]}
+                    )
                 expected += 1
                 seen = []
                 for s in sentinels:
@@ -1314,23 +1319,27 @@ async def test_no_downstream_value_reaches_a_response_log_or_audit(
                     response = "".join(block.text for block in result.content)
                     observed = tap.since(mark, response)
                     assert method in state["methods"], (name, state["methods"])
-                    assert observed.leaks(s) == [], (name, key, shape, family, observed)
+                    leaks = [c for c in observed.leaks(s) if c != "response"]
+                    assert leaks == [], (name, key, shape, family, observed)
                     seen.append(observed)
-                # No vacuous pass: the payload was rejected, and said so.
-                assert re.search(
-                    r"validation errors? for McpTaskInfo: \$", seen[0].response
-                ), (
+                # No vacuous pass: the call reached the downstream (above), and
+                # nothing was refused as a validation error.
+                assert "validation error" not in seen[0].response, (
                     name,
                     key,
                     seen[0].response,
                 )
-                assert not seen[0].differs(seen[1]), (
-                    name,
-                    key,
-                    shape,
-                    family,
-                    seen[0].differs(seen[1]),
-                )
+                # The response and the audit's digest of it are the accepted
+                # result; every other channel must not tell the pair apart.
+                differs = [
+                    d
+                    for d in seen[0].differs(seen[1])
+                    if d[0] not in ("response", "audit")
+                ]
+                assert not differs, (name, key, shape, family, differs)
+                assert _audit_without_result(seen[0]) == _audit_without_result(
+                    seen[1]
+                ), (name, key, shape, family)
                 assert _event_shape(seen[0].events) == _event_shape(seen[1].events)
                 rejected += 1
     assert rejected == expected > len(_task_calls()) * len(positions), (
@@ -1485,7 +1494,7 @@ async def test_a_connect_failure_carrying_a_validation_error_is_described(
         for s in sentinels:
 
             async def connect(_config: Any, s: str = s) -> None:
-                McpTaskInfo.model_validate({"task_id": "t", "ttl": {"v": s}})
+                McpTaskInfo.model_validate({"task_id": {"v": s}})
 
             monkeypatch.setattr(manager, "_connect_server", connect)
             mark = tap.start()
@@ -1496,7 +1505,7 @@ async def test_a_connect_failure_carrying_a_validation_error_is_described(
             observed = tap.since(mark, json.dumps(errors) + health)
             assert observed.leaks(s) == [], (family, observed)
             assert (
-                "validation error for McpTaskInfo: $.ttl: must be an integer"
+                "validation error for McpTaskInfo: $.task_id: must be a string"
                 in (errors[0])
             ), errors
             seen.append((json.dumps(errors), observed.log))
diff --git a/tests/test_downstream_frame_echo.py b/tests/test_downstream_frame_echo.py
index 789f083..a97152f 100644
--- a/tests/test_downstream_frame_echo.py
+++ b/tests/test_downstream_frame_echo.py
@@ -160,10 +160,12 @@ _DOWNSTREAM_LOGIC = textwrap.dedent(
             "tools/call": {"content": [{"type": "text", "text": bad}]},
             "resources/read": {"contents": [{"uri": "x://r", "text": bad}]},
             "prompts/get": {"messages": [{"role": bad, "content": {"type": "text", "text": "m"}}]},
-            "tasks/list": {"tasks": [{"taskId": "t", "status": "working", "ttl": s}]},
-            "tasks/get": {"task": {"taskId": "t", "status": "working", "ttl": s}},
-            "tasks/cancel": {"task": {"taskId": "t", "status": "working", "ttl": s}},
-            "tasks/result": {"task": {"taskId": "t", "status": "completed", "ttl": s}, "result": {"content": []}},
+            # A malformed task hint is dropped, not rejected (#298); a malformed
+            # `taskId` is still refused, and never rendered.
+            "tasks/list": {"tasks": [{"taskId": bad, "status": "working"}]},
+            "tasks/get": {"task": {"taskId": bad, "status": "working"}},
+            "tasks/cancel": {"task": {"taskId": bad, "status": "working"}},
+            "tasks/result": {"task": {"taskId": bad, "status": "completed"}, "result": {"content": []}},
         }[method]
 
     def reply(request, state):
@@ -1026,7 +1028,7 @@ async def test_a_wrapped_handler_keeps_the_wire_code(family: str) -> None:
 
     def invalid() -> ValidationError:
         try:
-            McpTaskInfo.model_validate({"task_id": "t", "ttl": s})
+            McpTaskInfo.model_validate({"task_id": "t", "raw": s})
         except ValidationError as error:
             return error
         raise AssertionError("did not reject")
diff --git a/tests/test_log_record_scrubber.py b/tests/test_log_record_scrubber.py
index a0dca03..c3790c8 100644
--- a/tests/test_log_record_scrubber.py
+++ b/tests/test_log_record_scrubber.py
@@ -36,7 +36,8 @@ _LOGGERS = (
 
 def _validation_error(s: str) -> ValidationError:
     try:
-        McpTaskInfo.model_validate({"task_id": "t", "ttl": {"v": s}})
+        # `task_id` still rejects; a malformed task hint is dropped (#298)
+        McpTaskInfo.model_validate({"task_id": {"v": s}})
     except ValidationError as error:
         return error
     raise AssertionError("no validation error")
@@ -92,7 +93,7 @@ def test_exc_info_is_scrubbed_on_any_logger(
         )
     (record,) = capture.records
     assert record.exc_info is None
-    assert "validation error for McpTaskInfo: $.ttl" in record.getMessage()
+    assert "validation error for McpTaskInfo: $.task_id" in record.getMessage()
     assert _clean(record, s)
 
 
diff --git a/tests/test_parse_error_echo.py b/tests/test_parse_error_echo.py
index b12fe09..8d740a7 100644
--- a/tests/test_parse_error_echo.py
+++ b/tests/test_parse_error_echo.py
@@ -712,6 +712,7 @@ def test_no_timestamp_site_echoes_its_input(family: str) -> None:
     """``datetime.fromisoformat`` quotes a bad timestamp (rev 7, N1)."""
     from pmcp import package_approvals, trust_store
     from pmcp.argument_errors import exception_text, safe_traceback_text
+    from pmcp.parsing import parse_timestamp
     from pmcp.types import McpTaskInfo
 
     s = "2026-13-99T" + _FAMILIES[family][1]
@@ -736,7 +737,9 @@ def test_no_timestamp_site_echoes_its_input(family: str) -> None:
                 "recorded_at": s,
             }
         ),
-        lambda: McpTaskInfo(task_id="t", created_at=s),
+        # The task-timestamp site no longer raises (#298): its parse is
+        # covered here directly, and the site itself below.
+        lambda: parse_timestamp(s, source="task timestamp"),
     ]
     for call in calls:
         with pytest.raises(Exception) as caught:
@@ -748,6 +751,8 @@ def test_no_timestamp_site_echoes_its_input(family: str) -> None:
         calls[0]()
     except Exception as error:  # noqa: BLE001 -- inspected
         assert "could not parse timestamp trust store record" in str(error)
+    task = McpTaskInfo(task_id="t", created_at=s)
+    assert task.created_at is None and task.unusable_fields == ["created_at"]
 
 
 def test_parse_error_is_rendered_as_its_own_text() -> None:
````
