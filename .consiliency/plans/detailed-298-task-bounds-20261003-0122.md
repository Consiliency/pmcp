# Detailed plan: bound and finite-check every numeric task hint — at the gate, from downstream, and on the wire

> Written on main `89559db` (dev0, a team host), worktree `pmcp-298`, branch
> `plan/298-task-bounds`. Every number below was measured on that tree, or on
> the spike of this plan applied to it. The spike was then removed, and this PR
> carries only this file.

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
| `createdAt` / `lastUpdatedAt` → `created_at` / `updated_at` | kept | kept | **kept nan / ±inf** | kept as 1.0 | **bare `OverflowError`** | kept | kept as 5.0 | parsed | kept | finite number or parseable string is kept; NaN, ±Inf, bool, overflow, unparseable or a non-scalar becomes `None` |

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
3. A downstream task hint that cannot be used now reads as `null` instead of:
   - a coerced value (`"5"` → 5, `true` → 1);
   - a stored NaN, negative or zero;
   - a failed call.
4. Any outbound frame that carries NaN or ±Inf in opaque arguments or metadata
   now fails the call. Before, such a frame went out as `NaN` on stdio and
   silently as `null` on remote.

With the final spike applied, these run 1034 tests: **1030 passed, 4
skipped, 0 failed** (the 4 skips are the spike's own, Design decision 6):
- `tests/test_gateway_tool_schemas.py`
- `tests/test_tools.py`
- `tests/test_client_manager.py`
- `tests/test_phase6_tenant_code_mode.py`
- `tests/test_scoped_advisor_audit.py`
- `tests/test_server.py`
- the new module

The spike's first version made 26 scoped-audit tests fail, through the
exception constructor above. No existing test needed a change, except the
snapshot, which was regenerated.

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

### 6. Downstream values are dropped to `None` per field: not clamped, not refused

`McpTaskInfo` gains `mode="before"` validators:
- `_usable_ttl`: keeps a non-bool `int` in [0, 2^63 − 1], and returns `None`
  otherwise.
- `_usable_poll_interval`: keeps a non-bool `int`/`float` that is finite and
  greater than 0, and returns `None` otherwise. A huge int overflows `float()`
  and becomes `None`.
- `_normalize_task_timestamp`: keeps a finite number, or a numeric or ISO
  string that parses to one. It returns `None` for:
  - a bool;
  - NaN or ±Inf;
  - a value that overflows `float()`;
  - an unparseable string;
  - any other type. On main, a non-scalar fell through to pydantic and raised.

The downstream bounds are wider than the caller's. Spec `Task.ttl` 0 can mean
"already expired", so it is kept, and the upper bound is int64. pmcp only
reports these values. They are not forwarded, so the I-JSON bound does not
apply.

**Why not refuse.** Refusing the payload is what main does by accident, and
measured, it loses a task the downstream already created. It also renders the
downstream's value in the error. A hint the downstream got wrong does not
make the task unusable.

**Why not clamp.** pmcp does not act on `pollInterval` or `ttl` (Research
summary). It *reports* them. A clamped value would misreport what the
downstream said. `None` honestly means "the downstream gave no usable hint",
and `raw` still carries what was sent.

**`updated_at` is load-bearing, not cosmetic.** It drives
`_evict_terminal_tasks`' sort, so a NaN would make eviction order undefined.

**No log line.** Dropping is silent by design: logging the value would be an
echo, and logging only the type adds nothing that `raw` does not already
show. No validator raises, so nothing can interpolate a value.

The spike's downstream test **skips** 4 cases on purpose: a negative and a
numeric-string *timestamp* are usable timestamps. The implementer may split
those into a separate parameter list instead of skipping them.

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

Measured: mutant M14 adds `await asyncio.sleep(task_info.poll_interval or 0)`
to `get_task`. It turns *both* guard tests red (see the mutation table).

### 8. Outbound frames are strict JSON on every transport, and the scope of that

A module-level `_encode_outbound_frame(payload) -> str` in
`client/manager.py` runs `json.dumps(payload, allow_nan=False)`. On
`ValueError` it raises `NonFiniteOutboundNumber(_NON_FINITE_OUTBOUND)`, a
`ValueError` subclass with the fixed message "outbound frame carries a
non-finite number".

All three writers use it:
- The stdio branch writes its result.
- The remote branch calls it first, as a check, before handing the dict to
  the SDK. The SDK would otherwise turn NaN into `null`.

**Why the encoder and not the gate.** It is the one place every forwarded
value passes, whatever its origin: caller `arguments`, `task.metadata`,
`_meta`, `requestor_context`, or a value pmcp computed itself.

**It fails cleanly.** The write sits inside `_send_request`'s `try`, whose
`finally` pops the pending entry (C-02). Measured: nothing is written,
`pending_requests == {}`, and the caller gets the invoke handler's error arm
with the fixed message.

**This is a behaviour change, and it is the decision the maintainer most
needs to see.** On main, a caller who puts NaN in `invoke.arguments`:
- gets it delivered to a *Python* stdio downstream, which accepts the bare
  `NaN`;
- gets it delivered to a remote downstream as `null`;
- gets a hang until timeout from a strict-JSON stdio downstream, such as
  Node's `JSON.parse`.

After this plan, all three cases fail at once, with the same error.
This applies to requests (`_send_request`). The fire-and-forget writer is
described below.
JSON-RPC 2.0 is defined over JSON, so the old behaviour was undefined.

To ship #298 without this change, drop the `client/manager.py` hunk and its
two tests. Decisions 2 to 7 do not depend on it.

**The fire-and-forget writer is different.** `_send_message_to_downstream`
carries frames pmcp originates and does not wait on: replies to
downstream-initiated requests, and `notifications/cancelled`. Its contract
(its docstring, `manager.py:3206`) is to log and swallow every write failure,
so that a dead pipe tears nothing down. Its `except Exception` also wraps the
new encoder call.

On that path, a non-finite number therefore means the frame is **not
written**, a DEBUG line records it, and the downstream sees nothing. For a
relayed *reply*, that means the downstream waits on its own request. This is
consistent with the writer's existing dead-pipe behaviour.

The DEBUG line goes through `describe_exception`, which renders only the
constant message, so it echoes nothing.

pmcp builds these frames itself, and no caller value reaches them on main, so
the case is theoretical. The spike leaves the writer's contract unchanged.

**Constructor.** `NonFiniteOutboundNumber` keeps `ValueError`'s standard
constructor. `test_scoped_advisor_audit._raised_exceptions` builds every
raised class with one string argument, and the spike's first version, with
`__init__(self)` taking no argument, turned 26 of those red. The message is a
module constant passed at the raise.

### 9. Units (seconds in pmcp, milliseconds in MCP 2025-11-25) are out of scope; open a follow-up issue

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

**Recommended follow-up issue:** "task.ttl / task.poll_interval are
documented in seconds but forwarded unchanged into MCP fields defined in
milliseconds". The plan does not file it, because filing is outward-facing.
`gh issue list --search "milliseconds ttl"` found no existing issue.

### 10. Messages: no new validator interpolates a value, under either merge order

Every caller-side rule here is a `Field` constraint (`ge`, `gt`, `le`,
`allow_inf_nan`) or the gate's type checker. **No custom validator raises.**
The downstream validators return `None`, and the encoder raises with a
constant.

So the plan adds nothing to #297's `argument_error(TYPE)` constants. The one
interaction is a phrase.

**If #297 (PR 314) lands first:**
- **Gate.** `describe_schema_error` already renders the new keywords from the
  gateway's own schema node, with no value:
  - `exclusiveMinimum`: `$.task.poll_interval: must be greater than 0`;
  - `minimum`: `$.task.ttl: must be greater than or equal to 1`;
  - `maximum`: `… must be less than or equal to 9007199254740991`;
  - the finite check (`type`): `$.task.poll_interval: must be of type number,
    null`.
- **Model** (in-process callers only, such as `_task_wire_metadata`'s
  `model_validate(dict)`). #297's `_FIXED_PHRASES` has no `finite_number`
  entry, so it falls to `is invalid`. **Add `"finite_number": "must be a
  finite number"` to `_FIXED_PHRASES` in `src/pmcp/argument_errors.py`.** It
  is one line.
- **Gate call.** #297's `server.py` patch rewrites the `except` body after
  `jsonschema.validate(...)` (`describe_schema_error(e, tool.input_schema,
  arguments)`). Re-apply `cls=GATE_VALIDATOR` on the `validate` line itself,
  because #297 leaves that line alone.
- **Timestamp validator.** #297 changes `_normalize_task_timestamp`'s ISO
  branch to `parse_timestamp(candidate, source="task timestamp").timestamp()`.
  Wrap #297's call in this plan's `try/except (ValueError, OverflowError):
  return None`. If #297's `parse_timestamp` raises its own subclass, catch
  that too. `parse_timestamp` is in #297's `src/pmcp/parsing.py`; read it on
  merge. Keep the finite checks and the final `return None` from this plan.
- **Sweeps.** #297's sweeps iterate the advertised schema's keywords, so they
  pick up the new `exclusiveMinimum` and `maximum` with no change. Re-run
  `tests/test_argument_error_echo.py` after the merge.

**If #298 lands first:**
- Gate text for the new bounds is jsonschema's `e.message`, for example
  `Input validation error: -5 is less than the minimum of 1`, or
  `nan is not of type 'number', 'null'`.
- That is the same echo profile every existing numeric bound (`timeout_ms`,
  `limit`) has on main. The value is the caller's own number, returned to
  that caller only. It is not logged: the gate rejection logs nothing with
  the value, and the #296 audit record is structural.
- This plan adds **no new echo class** and does **not** re-implement a
  renderer. #297 converts these messages when it lands, with the one
  `finite_number` line above.
- When #297 lands second, it must also:
  - keep `cls=GATE_VALIDATOR` on its rewritten gate call;
  - keep this plan's `try/except`, finite checks and fallthrough `return None`
    when it changes the timestamp branch;
  - add the `finite_number` phrase.

  Put this list in PR 314's merge notes.

### 11. Consiliency/pmcp#236 piece B (`extra="forbid"`)

Piece B adds `additionalProperties: false` to every object schema. It touches
no numeric field. The only collision is the snapshot fixture: whichever
change lands second regenerates `tests/fixtures/gateway_tool_schemas.json`
with `PMCP_UPDATE_SCHEMA_SNAPSHOT=1` and reviews the diff.

Piece B's own test that the gate and model agree on unknown keys runs through
`_handle_call_tool`. It therefore also runs `GATE_VALIDATOR`, which leaves
`additionalProperties` alone.

## Changes

These are the spike's patches, verbatim under *Verbatim bodies*. Source:
5 files, +119 / −19. The new test module is ~500 lines.

### `src/pmcp/types.py` (modify, +64 / −13)
- `import math`.
- Constant `MAX_FORWARDED_TASK_NUMBER = 2**53 - 1`, above
  `GatewayArguments`, with a comment citing RFC 7493.
- `TaskMetadataInput.ttl`: `ge=1, le=MAX_FORWARDED_TASK_NUMBER`. This
  replaces the int64 pair and its #236 comment with one that cites both
  issues.
- `TaskMetadataInput.poll_interval`: `gt=0, le=MAX_FORWARDED_TASK_NUMBER,
  allow_inf_nan=False`, with a comment.
- `_INT64_MAX = 2**63 - 1`, and on `McpTaskInfo` the before-validators
  `_usable_ttl` and `_usable_poll_interval`. `McpTaskRecord` inherits both.
- `McpTaskInfo._normalize_task_timestamp`:
  - refuse a bool;
  - a non-finite or overflowing number becomes `None`;
  - a numeric string must parse finite;
  - the ISO parse is wrapped (`ValueError`/`OverflowError` → `None`);
  - the fallthrough returns `None` instead of the raw value.

### `src/pmcp/tools/schema.py` (modify, +24)
- `import math` and `import jsonschema`.
- `_is_json_number` and `GATE_VALIDATOR`, as in Design decision 4.

### `src/pmcp/server.py` (modify, +4 / −1)
- `from pmcp.tools.schema import GATE_VALIDATOR`.
- `jsonschema.validate(instance=arguments, schema=tool.input_schema,
  cls=GATE_VALIDATOR)` at `server.py:313`.

### `src/pmcp/client/manager.py` (modify, +23 / −3)
- Module level, after `_MAX_LISTING_PAGES`:
  - `class NonFiniteOutboundNumber(ValueError)`;
  - `_NON_FINITE_OUTBOUND`;
  - `_encode_outbound_frame`.
- `_send_message_to_downstream`, `_send_request` and `_send_initialize`:
  - stdio: `json.dumps(x)` becomes `_encode_outbound_frame(x)`;
  - remote: `_encode_outbound_frame(x)` runs as a check before
    `validate_python`. `_send_initialize`'s remote branch sends a constant
    pmcp built, and needs no check.

### `tests/fixtures/gateway_tool_schemas.json` (regenerate, +4 / −2)
- `poll_interval` gains `"exclusiveMinimum": 0, "maximum": 9007199254740991`.
- `ttl`'s bounds become `"minimum": 1, "maximum": 9007199254740991`.
- Generate it with `PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest
  tests/test_gateway_tool_schemas.py::test_advertised_schemas_match_snapshot`,
  then review the diff. It must be exactly those six lines.

### `tests/test_gateway_tool_schemas.py` (modify, docstring only, −3 / +3)
- In the module docstring, replace the sentence ending "…passes the gate
  (Consiliency/pmcp#298)." with: "`task.poll_interval` is bounded on both
  sides and finite-checked at the gate by `GATE_VALIDATOR`
  (Consiliency/pmcp#298; `tests/test_task_numeric_bounds.py`)."
- Change "Two classes stay open" to "One class stays open".
- Leave every test unchanged. `test_ttl_range_agrees_between_gate_and_model`
  still passes, as measured.

### `tests/test_task_numeric_bounds.py` (create)

The test file is given verbatim below. It contains:
- **Caller.**
  - `test_gate_and_model_agree_per_field_and_failure_mode`: 8 fields × 11
    modes.
  - `test_gate_rejects_a_boolean_for_every_numeric_argument`.
  - `test_task_hint_bounds`: the exact edges of both task fields.
  - `test_a_non_finite_poll_interval_off_the_wire_is_refused_at_the_gate`.
    It parses a real stdio line containing
    `NaN`/`Infinity`/`-Infinity`/`1e400` with the SDK's
    `jsonrpc_message_adapter`, then drives `_call_through_gate`.
- **Schema.**
  - `test_every_advertised_number_is_bounded_both_sides`, covering `number`
    and `integer`.
  - `test_every_float_argument_refuses_non_finite_values_in_the_model`, which
    requires `AllowInfNan(False)` on every `float` field of every gateway
    argument model.
  - `test_gate_validator_refuses_non_finite_numbers_and_keeps_the_rest`.
- **Downstream.**
  - `test_an_unusable_downstream_task_hint_is_dropped_not_raised`: 4 fields ×
    8 modes.
  - `test_a_usable_downstream_task_hint_is_kept`.
  - `test_a_created_task_with_unusable_hints_is_still_recorded`, driven
    through `call_tool`.
- **No loop.**
  - `test_a_downstream_poll_interval_never_drives_a_pmcp_loop`.
  - `test_poll_interval_has_no_consumer_outside_the_allowlist`.
- **Forwarded.**
  - `test_outbound_frames_refuse_non_finite_numbers`.
  - `test_a_request_carrying_nan_is_never_written`, for stdio and remote.
  - `test_forwarded_task_hints_are_spec_shaped`.

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
  > - A downstream task's `ttl`, `pollInterval`, `createdAt` or `updatedAt`
  >   that pmcp cannot use (non-finite, negative where that is meaningless, a
  >   boolean, a string where a number is expected, or out of range) is now
  >   reported as `null`. Before, it was coerced (`"5"` became 5, `true`
  >   became 1), stored as given, or it failed the call after the downstream
  >   had already created the task. The original value remains in the task's
  >   `raw`.
  > - pmcp no longer sends `NaN` or `±Infinity` to a downstream server. A
  >   request whose `arguments` or task metadata contain one now fails with
  >   `outbound frame carries a non-finite number`. A fire-and-forget frame
  >   (a reply or a notification pmcp originates) that contains one is
  >   dropped and logged, never written. Before, stdio servers
  >   received a non-JSON `NaN` literal and HTTP/SSE servers silently received
  >   `null`.

  Never write a closing keyword next to the number.
- **`README.md`.** No change. Its task paragraph (`README.md:1401-1408`)
  names the fields without values or units.
- **`specs/tenant-code-mode-host-contract.md`.** No change in this plan; the
  units are the follow-up of Design decision 9. Optionally, at `:106`, add
  that pmcp reports an unusable `pollInterval`/`ttl` as `null`. That is a
  one-line clarification of what a tenant server sees, and not a contract
  change.
- **`SECURITY.md`.** No ledger row changes. Run
  `scripts/check_security_claims.py` and expect `OK`.

## Dependencies & order

1. Independent of `main`. It applies to `89559db` as is.
2. It is **independent of #297, in both orders.** Design decision 10 gives the
   exact touch points: the `cls=` line, the timestamp `try`, and the
   `finite_number` phrase. Prefer landing #297 first. Then this plan's gate
   messages are structural from the first commit, and the merge is a
   one-line phrase addition plus `cls=`.
3. It is **independent of #236 piece B.** Only the snapshot collides, and it
   is regenerated by whichever lands second.
4. Within this plan, any order works. The `client/manager.py` hunk (Design
   decision 8) is separable: if the maintainer declines the outbound
   behaviour change, drop that hunk and
   `test_outbound_frames_refuse_non_finite_numbers` and
   `test_a_request_carrying_nan_is_never_written`. Nothing else depends on
   them.
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
- edit the docstring and the CHANGELOG by hand.

Then:

```bash
# 1. the new module (measured on the spike: 164 passed, 4 skipped)
uv run pytest tests/test_task_numeric_bounds.py --cov-fail-under=0 -p no:cacheprovider -q
# 2. the suites the change touches (measured on the spike: 1030 passed, 4 skipped, 0 failed)
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
# 5. the full suite: once, detached, with a notifying waiter (memory on dev0 is shared)
nohup uv run pytest -q -p no:cacheprovider > /tmp/pmcp-298-full.log 2>&1 &
```

Step 1 needs the 4 skips to be the downstream timestamp cases only (Design
decision 6). Check with `-rs`.

If #297 has landed, also run `uv run pytest tests/test_argument_error_echo.py
tests/test_exception_text_sinks.py --cov-fail-under=0 -q`. Then confirm that
`describe_pydantic_error` on a `finite_number` error reads `must be a finite
number`.

**Red on main.** The new module was run against `89559db`'s sources with an
import shim (`NonFiniteOutboundNumber` → `ValueError`, `GATE_VALIDATOR` →
`Draft202012Validator`, `MAX_FORWARDED_TASK_NUMBER` → `2**53 − 1`). Result:
**53 failed, 111 passed, 4 skipped**. The failures:

| Test | Red | First assertion on main |
|---|---|---|
| `test_an_unusable_downstream_task_hint_is_dropped_not_raised` | 28 | stored `-5`, `nan`, `1`; `ValidationError`; bare `OverflowError: int too large to convert to float` |
| `test_task_hint_bounds` | 10 | `ttl` 0/−5 accepted; `poll_interval` 0/−0.5/NaN/±Inf/±10^400 accepted at the gate |
| `test_a_non_finite_poll_interval_off_the_wire_is_refused_at_the_gate` | 4 | `NaN`, `Infinity`, `-Infinity` and `1e400` reached the handler, so `is_error` was not true |
| `test_gate_and_model_agree_per_field_and_failure_mode` | 2 | `poll_interval` ±10^400: gate PASS, model `float_type` |
| `test_a_created_task_with_unusable_hints_is_still_recorded` | 2 | `[fraction]`: `ValidationError … int_from_float, input_value=1.5`; `[huge_timestamp]`: `OverflowError` |
| `test_a_request_carrying_nan_is_never_written` | 2 | the frame was written, then `TimeoutError: Request tools/call timed out` (30.03 s each on main; the plan's test passes `timeout_ms=1000`) |
| `test_every_advertised_number_is_bounded_both_sides` | 1 | `['gateway.invoke.task.poll_interval']` |
| `test_every_float_argument_refuses_non_finite_values_in_the_model` | 1 | `['TaskMetadataInput.poll_interval']` |
| `test_gate_validator_refuses_non_finite_numbers_and_keeps_the_rest` | 1 | the stock validator accepts NaN |
| `test_outbound_frames_refuse_non_finite_numbers` | 1 | `json.dumps` wrote `NaN` |
| `test_forwarded_task_hints_are_spec_shaped` | 1 | `{"ttl": 0}` was forwarded |

The two no-loop guards **pass on main**. That is by design: pmcp has no loop
(Design decision 7), and mutant M14 is what proves they can fail.

## Acceptance criteria

- [ ] Every caller numeric argument (8 field paths across 4 tools) gets the
  same verdict from the gate and the model for each of the 11 failure modes,
  and accepts its legitimate example. Proven by
  `test_gate_and_model_agree_per_field_and_failure_mode`.
- [ ] `task.ttl` ∈ [1, 2^53−1] and `task.poll_interval` ∈ (0, 2^53−1],
  finite, at both the gate and the model. `1e-300`, `0.1` and the maximum are
  accepted. Proven by `test_task_hint_bounds`.
- [ ] `NaN`, `Infinity`, `-Infinity` and `1e400`, read off a real stdio line
  by the SDK's parser, are refused at the gate with `Input validation error:`
  before any handler runs. Proven by
  `test_a_non_finite_poll_interval_off_the_wire_is_refused_at_the_gate`.
- [ ] The gate refuses `true` for every numeric argument. Proven by
  `test_gate_rejects_a_boolean_for_every_numeric_argument`.
- [ ] Every advertised `number`/`integer` is bounded on both sides, and every
  `float` argument field carries `allow_inf_nan=False`. Proven by
  `test_every_advertised_number_is_bounded_both_sides` and
  `test_every_float_argument_refuses_non_finite_values_in_the_model`.
- [ ] The advertised schema equals the model's projection. Proven by the
  existing `test_advertised_schema_is_derived_from_registered_model`, and the
  snapshot diff is the six lines above. None of the existing schema-drift
  tests changes, other than the docstring.
- [ ] A downstream `ttl`, `pollInterval`, `createdAt` or `lastUpdatedAt` that
  cannot be used becomes `None` and never raises. Usable values are kept.
  Proven by `test_an_unusable_downstream_task_hint_is_dropped_not_raised` and
  `test_a_usable_downstream_task_hint_is_kept`.
- [ ] A task created with unusable hints is still recorded. Proven by
  `test_a_created_task_with_unusable_hints_is_still_recorded`.
- [ ] No downstream `pollInterval` makes pmcp sleep, spin or send more than
  one request per call, and no new reader of `.poll_interval` appears
  unnoticed. Proven by
  `test_a_downstream_poll_interval_never_drives_a_pmcp_loop` and
  `test_poll_interval_has_no_consumer_outside_the_allowlist`.
- [ ] No outbound frame carries NaN or ±Inf, on stdio or remote. Nothing is
  written, and the pending entry is popped. Forwarded task hints are an
  integer `ttl` and a finite `pollInterval`. Proven by
  `test_outbound_frames_refuse_non_finite_numbers`,
  `test_a_request_carrying_nan_is_never_written[stdio|remote]` and
  `test_forwarded_task_hints_are_spec_shaped`.
- [ ] Every mutant in the table below is red, for the reason stated.
- [ ] Verification steps 2 to 5 pass. The CHANGELOG entry is present, with
  no closing keyword.

## Mutation table

Each mutant was measured on the spiked tree (`mutants.py`: apply one string
replacement, run `tests/test_task_numeric_bounds.py`, restore the file from a
copy saved in memory). All 14 are red. After the run, the tree was
byte-identical to the spike: `diff <(git diff) spike-final.patch` was empty.

| # | Rule | Mutant | Red tests (measured) |
|---|---|---|---|
| M1 | `ttl` lower bound | `ge=1` → `ge=0` | `test_task_hint_bounds`, `test_forwarded_task_hints_are_spec_shaped` |
| M2 | `ttl` I-JSON upper bound | `le=MAX_FORWARDED_TASK_NUMBER` → int64 max | `test_task_hint_bounds[…9007199254740992-False]` |
| M3 | `poll_interval` lower bound | drop `gt=0` | `test_task_hint_bounds`, `test_gate_and_model_agree…`, `test_every_advertised_number_is_bounded_both_sides`, `test_forwarded_task_hints_are_spec_shaped` |
| M4 | `poll_interval` upper bound | drop `le=` | `test_every_advertised_number_is_bounded_both_sides`, `test_gate_and_model_agree…` (±10^400), `test_task_hint_bounds` |
| M5 | model refuses non-finite | drop `allow_inf_nan=False` | `test_every_float_argument_refuses_non_finite_values_in_the_model` **only**. pydantic's own `gt`/`le` already refuse NaN and ±Inf (`NaN > 0` is false), so the flag changes the error type to `finite_number`. It does not change the verdict. The class test is the pin, and it is why that test exists |
| M6 | gate uses the finite validator | drop `cls=GATE_VALIDATOR` in `server.py` | `test_a_non_finite_poll_interval_off_the_wire_is_refused_at_the_gate` (all 4 literals) |
| M7 | finite type check | `_is_json_number` returns the stock `number` check | `test_gate_validator_refuses…`, `…off_the_wire…`, `test_gate_and_model_agree…`, `test_task_hint_bounds` |
| M8 | downstream `ttl` sanitized | `_usable_ttl` returns `value` | `test_an_unusable_downstream_task_hint_is_dropped_not_raised`, `test_a_created_task…[fraction]` |
| M9 | downstream `pollInterval` finite | drop `math.isfinite(number) and` | `test_an_unusable_downstream…[pollInterval-…-inf]` |
| M10 | timestamp overflow | drop the `try/except OverflowError` around `float(value)` | `test_an_unusable_downstream…`, `test_a_created_task…[huge_timestamp]` |
| M11 | timestamp finite | return `number` without `isfinite` | `test_an_unusable_downstream…[lastUpdatedAt-updated_at--inf]` |
| M12 | strict outbound JSON | `allow_nan=False` → default | `test_outbound_frames_refuse_non_finite_numbers`, `test_a_request_carrying_nan_is_never_written` |
| M13 | remote path checked too | drop `_encode_outbound_frame(request)` before `validate_python` in `_send_request` | `test_a_request_carrying_nan_is_never_written[remote]` |
| M14 | no loop on a downstream hint | add `await asyncio.sleep(task_info.poll_interval or 0)` to `get_task` | `test_poll_interval_has_no_consumer_outside_the_allowlist`, `test_a_downstream_poll_interval_never_drives_a_pmcp_loop` |

The implementer re-runs the same 14 mutants on the final tree. After each
one, restore the file from a saved copy, never with `git checkout --`, and
confirm `git diff --stat` matches the pre-mutation state.

## Non-goals

- **Units** (seconds versus the spec's milliseconds). See Design decision 9;
  it is a follow-up issue.
- **NaN inside a downstream tool *result*.** pmcp passes it through, and
  `server.py:464` writes it with `json.dumps(result)` as `NaN` inside a text
  block. That is the downstream's content, not a task hint. A strict
  serializer there would change every result path, and that belongs in its
  own issue.
- **A clamp helper for a polling loop that does not exist.** See Design
  decision 7.
- **`Strict()` on numeric fields.** See Design decision 5.
- **Echo rendering.** That is Consiliency/pmcp#297. This plan only states
  its touch points (Design decision 10).

## Unverified

- **The behaviour of a real Node stdio downstream receiving `NaN`.** The
  "hangs until timeout" claim rests on `JSON.parse` refusing the bare literal
  and on the spike's fake, which wrote and then timed out. No live Node
  server was run.
- **#297's `parse_timestamp` exception type.** The resolution in Design
  decision 10 says to catch it; read `src/pmcp/parsing.py` on that branch
  when merging.
- **The full suite.** It was not run on the spike, because memory on dev0 is
  shared with a parallel planner. The 7 modules that touch the change were
  run: 1030 passed, 4 skipped, 0 failed. Verification step 5 runs the full suite once.

## Execution Policy

- execute: effort=medium.
- reason: a small, contained change (4 source files, about 120 lines), but
  on the trust edge, in both directions, and with one maintainer-visible
  behaviour change: the outbound strict JSON of Design decision 8, which is
  separable.
- Re-run the mutation table, the 7-module run, ruff and mypy before
  requesting review.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the source patch below (between the ```` fences) to
   `298-src.patch`, then run `git apply 298-src.patch` on `89559db`.
   It also regenerates the snapshot fixture.
2. Write the test module below to `tests/test_task_numeric_bounds.py`.
3. Edit the docstring of `tests/test_gateway_tool_schemas.py` and add the
   `CHANGELOG.md` entry by hand, as in *Changes* and *Documentation impact*.

### Patch — `src/pmcp/{types.py, tools/schema.py, server.py, client/manager.py}` and `tests/fixtures/gateway_tool_schemas.json`

````diff
diff --git a/src/pmcp/client/manager.py b/src/pmcp/client/manager.py
index 57a563e..f4a71a3 100644
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -189,6 +189,24 @@ _RECONCILE_RERUN_DEBOUNCE_S = 0.25
 _MAX_LISTING_PAGES = 500
 
 
+class NonFiniteOutboundNumber(ValueError):
+    """An outbound frame carries NaN or +-Infinity (Consiliency/pmcp#298)."""
+
+
+_NON_FINITE_OUTBOUND = "outbound frame carries a non-finite number"
+
+
+def _encode_outbound_frame(payload: dict[str, Any]) -> str:
+    """One JSON-RPC frame as strict JSON. NaN and +-Infinity are not JSON:
+    stdio would write them as bare literals a strict peer cannot parse, and the
+    SDK's HTTP and SSE clients would silently send them as ``null``. Refuse
+    instead, on every transport (Consiliency/pmcp#298)."""
+    try:
+        return json.dumps(payload, allow_nan=False)
+    except ValueError:
+        raise NonFiniteOutboundNumber(_NON_FINITE_OUTBOUND) from None
+
+
 class DownstreamError(Exception):
     """A JSON-RPC `error` object returned by a downstream MCP server.
 
@@ -3220,12 +3238,13 @@ class ClientManager:
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
@@ -3417,13 +3436,14 @@ class ClientManager:
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
 
@@ -3547,7 +3567,7 @@ class ClientManager:
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
index 7dc8e7c..dba185a 100644
--- a/src/pmcp/types.py
+++ b/src/pmcp/types.py
@@ -4,6 +4,7 @@ from __future__ import annotations
 
 from datetime import datetime
 from enum import Enum
+import math
 import re
 from typing import Annotated, Any, Literal
 
@@ -77,6 +78,11 @@ DEFAULT_AUTH_STATE_SEMANTICS: dict[AuthState, AuthStateSemanticsInfo] = {
 }
 
 
+#: The largest integer every JSON peer reads exactly (RFC 7493 s2.2, I-JSON):
+#: the upper bound on numeric task hints pmcp forwards downstream.
+MAX_FORWARDED_TASK_NUMBER = 2**53 - 1
+
+
 class GatewayArguments(BaseModel):
     """Base for every model that parses arguments an agent sends to a gateway
     tool, and for every model nested inside one. The advertised ``inputSchema``
@@ -518,6 +524,9 @@ class ServerStatus(BaseModel):
     avg_response_time_ms: float | None = None  # Rolling average response time
 
 
+_INT64_MAX = 2**63 - 1
+
+
 class McpTaskInfo(BaseModel):
     """Public view of a downstream MCP task."""
 
@@ -530,6 +539,28 @@ class McpTaskInfo(BaseModel):
     poll_interval: float | None = None
     raw: dict[str, Any] = Field(default_factory=dict)
 
+    # Downstream hints are kept only when usable, else dropped to None, field by
+    # field (Consiliency/pmcp#298): refusing the payload would fail a call whose
+    # task the downstream already created, and pmcp only reports these values.
+    @field_validator("ttl", mode="before")
+    @classmethod
+    def _usable_ttl(cls, value: Any) -> int | None:
+        if type(value) is int and 0 <= value <= _INT64_MAX:
+            return value
+        return None
+
+    @field_validator("poll_interval", mode="before")
+    @classmethod
+    def _usable_poll_interval(cls, value: Any) -> float | None:
+        if type(value) in (int, float):
+            try:
+                number = float(value)
+            except OverflowError:
+                return None
+            if math.isfinite(number) and number > 0:
+                return number
+        return None
+
     @field_validator("created_at", "updated_at", mode="before")
     @classmethod
     def _normalize_task_timestamp(cls, value: Any) -> float | None:
@@ -537,20 +568,31 @@ class McpTaskInfo(BaseModel):
             return None
         if isinstance(value, datetime):
             return value.timestamp()
+        if isinstance(value, bool):
+            return None
         if isinstance(value, int | float):
-            return float(value)
+            try:
+                number = float(value)
+            except OverflowError:
+                return None
+            return number if math.isfinite(number) else None
         if isinstance(value, str):
             candidate = value.strip()
             if not candidate:
                 return None
             try:
-                return float(candidate)
+                number = float(candidate)
             except ValueError:
                 pass
+            else:
+                return number if math.isfinite(number) else None
             if candidate.endswith("Z"):
                 candidate = f"{candidate[:-1]}+00:00"
-            return datetime.fromisoformat(candidate).timestamp()
-        return value
+            try:
+                return datetime.fromisoformat(candidate).timestamp()
+            except (ValueError, OverflowError):
+                return None
+        return None
 
 
 class McpTaskRecord(McpTaskInfo):
@@ -573,18 +615,27 @@ class TaskMetadataInput(GatewayArguments):
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
    NonFiniteOutboundNumber,
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

DOWNSTREAM_BAD: dict[str, Any] = {
    "negative": -5,
    "nan": float("nan"),
    "inf": float("inf"),
    "-inf": float("-inf"),
    "bool": True,
    "huge_int": BIG,
    "string": "5",
    "list": [1],
}


@pytest.mark.parametrize("mode", list(DOWNSTREAM_BAD))
@pytest.mark.parametrize(
    ("wire", "attr"),
    [
        ("ttl", "ttl"),
        ("pollInterval", "poll_interval"),
        ("createdAt", "created_at"),
        ("lastUpdatedAt", "updated_at"),
    ],
)
def test_an_unusable_downstream_task_hint_is_dropped_not_raised(
    wire: str, attr: str, mode: str
) -> None:
    value = DOWNSTREAM_BAD[mode]
    if attr in ("created_at", "updated_at") and mode in ("negative", "string"):
        pytest.skip("a negative or numeric-string timestamp is a usable timestamp")
    manager = ClientManager()
    info = manager._task_info_from_payload(
        {"taskId": "t1", "status": "working", wire: value}
    )
    assert info is not None
    assert getattr(info, attr) is None
    McpTaskInfo.model_validate(info.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("wire", "attr", "value"),
    [
        ("ttl", "ttl", 0),
        ("ttl", "ttl", 60000),
        ("pollInterval", "poll_interval", 2),
        ("pollInterval", "poll_interval", 2.5),
        ("createdAt", "created_at", "2025-11-25T10:00:00Z"),
    ],
)
def test_a_usable_downstream_task_hint_is_kept(
    wire: str, attr: str, value: Any
) -> None:
    info = ClientManager()._task_info_from_payload(
        {"taskId": "t1", "status": "working", wire: value}
    )
    assert info is not None and getattr(info, attr) is not None


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


@pytest.mark.parametrize("mode", ["fraction", "nan", "huge_timestamp"])
@pytest.mark.asyncio
async def test_a_created_task_with_unusable_hints_is_still_recorded(mode: str) -> None:
    """Before #298 a downstream `ttl: 1.5` raised after the downstream had
    created the task, so the caller got an error and pmcp lost the task."""
    task: dict[str, Any] = {"taskId": "t1", "status": "working"}
    if mode == "fraction":
        task["ttl"] = 1.5
    elif mode == "nan":
        task["pollInterval"] = float("nan")
    else:
        task["createdAt"] = BIG
    manager = ClientManager()
    _task_server(manager, {"task": task})
    await manager.call_tool("tasks::run", {}, task={"ttl": 300})
    assert manager.get_task_record("tasks", "t1") is not None


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


def test_outbound_frames_refuse_non_finite_numbers() -> None:
    assert _encode_outbound_frame({"a": 1.5, "b": [1, 2]}) == '{"a": 1.5, "b": [1, 2]}'
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(NonFiniteOutboundNumber) as raised:
            _encode_outbound_frame({"params": {"arguments": {"x": [value]}}})
        assert str(raised.value) == "outbound frame carries a non-finite number"


@pytest.mark.parametrize("remote", [False, True], ids=["stdio", "remote"])
@pytest.mark.asyncio
async def test_a_request_carrying_nan_is_never_written(remote: bool) -> None:
    manager = ClientManager()
    if remote:
        managed = ManagedClient(
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
    else:
        process = MagicMock()
        process.stdin.write = MagicMock()
        process.stdin.drain = AsyncMock()
        managed = ManagedClient(
            config=ResolvedServerConfig(
                name="s", source="custom", config=LocalMcpServerConfig(command="x")
            ),
            process=process,
            status=ServerStatus(name="s", status=ServerStatusEnum.ONLINE, tool_count=0),
        )
    with pytest.raises(NonFiniteOutboundNumber):
        await manager._send_request(
            managed,
            "tools/call",
            {"name": "t", "arguments": {"x": float("nan")}},
            timeout_ms=1000,
        )
    if remote:
        managed.write_stream.send.assert_not_called()  # type: ignore[union-attr]
    else:
        managed.process.stdin.write.assert_not_called()  # type: ignore[union-attr]
    assert managed.pending_requests == {}


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
