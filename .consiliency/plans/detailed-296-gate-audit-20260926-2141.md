# Detailed plan: record `tools/call` gate rejections in the scoped-advisor audit, without argument values

> **Revision 1 (2026-09-26).** Consiliency/pmcp#296, the prerequisite for
> piece B (`extra="forbid"`) of Consiliency/pmcp#236. The change is
> **embedded, not described**: the five blocks under *Verbatim bodies* are
> `git apply` patches against `origin/main` @ `959d4d4`, byte-identical to the
> verified code on the local-only branch `wip/296-code` @ `9ced94f` (not
> pushed). Proven by extracting them from this file, `git apply --check` on a
> clean `959d4d4` tree, applying, and `cmp` against `wip/296-code` (see
> *Embedding proof*).
>
> Decisions, each argued from the code below:
> - **A new event, `audit.rejection`, with `terminal_status:
>   "invalid_arguments"`** — not an `audit.invocation`. The one downstream
>   reader (agent-harness's board ledger) correlates every `gateway.invoke`
>   invocation to its run, and a correlation-free invocation record would fail
>   the seat's whole research ledger as `audit_correlation_mismatch`
>   (measured from its source below). The invocation vocabulary stays
>   `success`/`failure`/`denied`; the capability stays `scoped_advisor_audit.v1`.
> - **Policy is judged before the gate, and read once.** A call to a blocked
>   tool is `denied` whatever its arguments; the single verdict feeds both the
>   gate and `call_tool`'s `denied` arm.
> - **The record carries the tool, the failing JSON path and the validator
>   keyword, and the writer's own envelope — nothing else.** A dedicated
>   `record_rejected_arguments` builds it;
>   it never goes through `record_invocation`, whose correlation, source-hash
>   and digest channels all read argument values.
> - **Caller-chosen keys in the path are themselves a leak and are redacted to
>   `null`.** A path segment survives only as a key some `properties` map in
>   the tool's schema declares, or as an index into a list.

## Task

Address Consiliency/pmcp#296. `GatewayServer._handle_call_tool`
(`src/pmcp/server.py:281-471` on `main`) validates arguments with
`jsonschema.validate` and, on `ValidationError`, returns
`CallToolResult(is_error=True, "Input validation error: …")` at `:300-308`
**before** `call_tool` is entered. So a gate rejection never reaches
`_record_scoped_invocation`, and the scoped-advisor audit sees no attempt.
Piece A of Consiliency/pmcp#236 (PR 300, `cdaf3c6`) moved more rejections onto
that early return; piece B moves every unknown-key call there. This must land
before B.

Required by the issue and the coordinator:

1. Decide the `terminal_status`.
2. Decide whether a policy-blocked tool is judged before or after the gate,
   and preserve `denied` for a malformed call to a blocked tool.
3. The record carries tool name, failing JSON path, validator keyword only —
   never the jsonschema message, the instance, or any argument value. Fix the
   class: prove no instance-derived field reaches the record, including
   caller-supplied dict keys in `error.path` and the key an
   `additionalProperties` error names.
4. Enumerate **every** exit of `_handle_call_tool` and its audit fate.

## Research summary (all measured this session)

### The gate and the audit on `main` (`959d4d4`)

`_handle_call_tool` (main, line numbers of `src/pmcp/server.py`):

| line | step |
|---|---|
| `:293-294` | `name = params.name`; `arguments = params.arguments or {}` |
| `:296` | `tool = self._find_gateway_tool(name)` (registry lookup; `None` for an unregistered name) |
| `:297-308` | **gate**: if `tool is not None`, `jsonschema.validate`; on `ValidationError` → `return CallToolResult(is_error=True, …e.message)` — **no audit, no `_require_scoped_audit`, no policy check** |
| `:310` | `async def call_tool(...)`: everything below is inside its `try` |
| `:313` | `self._require_scoped_audit()` |
| `:315-327` | `if not self._policy_manager.is_gateway_tool_allowed(name)` → record `denied` → return |
| `:329-336` | `if tool is None: raise ValueError("Unknown tool")` (X1 guard, #236) |
| `:338-343` | scoped `gateway.invoke` correlation check (`InvokeInput.model_validate`) |
| `:345-399` | dispatch chain |
| `:405-419` | record `success` / `failure` / `denied` → return |
| `:423-434` | `except ScopedAdvisorAuditError` → return "channel failed" (unrecorded) |
| `:436-469` | `except Exception` → record `failure`/`denied` → return; if that record raises `ScopedAdvisorAuditError` → return "channel failed" |
| `:470-471` | `content = await call_tool(...)`; `return CallToolResult(content=content)` |

The audit is only ever live under the exact scoped policy:
`PolicyManager.activate_scoped_advisor` (`policy/policy.py:597-601`) raises
unless `is_scoped_advisor_policy()`, and `GatewayServer.__init__` calls it
whenever `audit_jsonl` is set. That policy allows exactly
`gateway.health`, `gateway.catalog_search`, `gateway.describe`,
`gateway.invoke` (`README.md:1511-1513`) — the same four names as
`_SCOPED_GATEWAY_TOOLS` (`scoped_advisor_audit.py:20-25`).

### What `record_invocation` would leak for an unvalidated payload

`ScopedAdvisorAudit.record_invocation` (`scoped_advisor_audit.py:186-228`)
derives these fields from `arguments` and `result`:

| field | source | why unusable for a rejected payload |
|---|---|---|
| `run_correlation_id`, `seat_correlation_id` | `arguments[...]` if it matches `^[A-Za-z0-9._:-]{1,128}$` | most API keys match this charset; on a rejected call nothing else vouches for the field |
| `downstream_tool_id` | `arguments["tool_id"]` if it matches `_TOOL_ID_PATTERN` | caller value |
| `evidence_label_digest` | `arguments[...]` if 64 hex | caller value |
| `source_reference_hash` | sha256 of the first public URL in `result` or **`arguments`** | a hash of a caller URL |
| `redacted_result_digest` | `sha256(json(result))` | passing the error or its message as `result` would publish a dictionary-attackable hash of a low-entropy secret |

So the #236 plan's "likely shape" (`:1106-1112`: `failure`/`denied`,
`result={"error_type": …}`, "arguments passed through the audit's existing
filtering") is **overridden**: the existing filtering is a *charset* filter,
not a provenance filter, and on a payload the gate just rejected no field is
vouched for. The gate record is built by a dedicated method from three inputs.

### The downstream reader: agent-harness's advisor-board research ledger

Read at `~/code/agent-harness` @ `b9627d53`
(`phase-loop-runtime/src/phase_loop_runtime/advisor_board/research.py`; the
pinned install `~/.local/share/agent-harness` @ `18a324a4` has the same
`scoped_advisor_audit.v1` requirement). It is the only code in either tree
that parses the audit (`grep -rln "audit.invocation\|scoped_advisor_audit"
--include='*.py'`: `research.py`, `schema.py` (capability string only),
`runner.py` (unrelated "audit" hits), and tests).

- `_validated_records` (`:403-442`): contiguous `sequence`, one
  `audit.started` first and one `audit.completed` last with
  `record_count == len(records)`, **one `policy_digest` and one
  `audit_session_id` across every record**. It does not enumerate `event`.
- The ledger loop (`:465-511`):

  ```python
  if (
      record.get("event") != "audit.invocation"
      or record.get("gateway_tool") != "gateway.invoke"
  ):
      continue
  ...
  correlated = (
      record.get("run_correlation_id") == config.run_correlation_id
      and record.get("seat_correlation_id") == config.seat_correlation_id
      and evidence_digest == config.evidence_label_digest
  )
  correlation_mismatch = correlation_mismatch or not correlated
  ...
  if correlation_mismatch:
      status = "failed"
  ```

So a rejection written as an `audit.invocation` for `gateway.invoke` with
`null` correlations would set `correlation_mismatch` and fail the seat's
entire research ledger — overriding every verified invocation — with a
misleading `audit_correlation_mismatch`. Carrying the correlations is ruled
out (they are unvalidated argument values). **So the rejection is its own
event.** The reader skips it by its first test, and its envelope
(`sequence`, `audit_session_id`, `policy_digest`) satisfies
`_validated_records`. The capability string stays `.v1`: no existing record
changes shape or meaning, and the reader dispatches on `event`. Mutant M13
(event renamed back to `audit.invocation`) is killed by the assertion that
rejected calls write **zero** `audit.invocation` records.

### Probe: which rejections reach the audit (`probe_gate_audit.py`, below)

Real `GatewayServer` with the scoped policy and a real audit file;
`provision` is outside that policy, so it is blocked. Pre-A is `1fb36f2`
(`cdaf3c6^`), main is `959d4d4`, patch is `wip/296-code`.

```text
== pre-A @ 1fb36f2
allowed, schema-malformed                gateway.describe   is_error=False text='{"error": true, "message": "1 validation error for DescribeI'
                                         audit=['audit.invocation:failure'] extra=[{}]
allowed, type error echoing a secret     gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-1' is not of type 'object"
                                         audit=[] extra=[]
allowed, pattern error echoing a secret  gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-2' does not match '^[0-9a"
                                         audit=[] extra=[]
blocked, schema-malformed                gateway.provision  is_error=True  text="Input validation error: 12345 is not of type 'string'"
                                         audit=[] extra=[]
blocked, model-only-malformed on pre-A   gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
blocked, well-formed                     gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
unregistered name                        gateway.nope       is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
SECRET in audit: False
dead sink, malformed describe: False {"error": true, "message": "Scoped advisor audit channel failed"}
== main @ 959d4d4 (src)
allowed, schema-malformed                gateway.describe   is_error=True  text="Input validation error: '' should be non-empty"
                                         audit=[] extra=[]
allowed, type error echoing a secret     gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-1' is not of type 'object"
                                         audit=[] extra=[]
allowed, pattern error echoing a secret  gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-2' is too short"
                                         audit=[] extra=[]
blocked, schema-malformed                gateway.provision  is_error=True  text="Input validation error: 12345 is not of type 'string'"
                                         audit=[] extra=[]
blocked, model-only-malformed on pre-A   gateway.provision  is_error=True  text="Input validation error: '' should be non-empty"
                                         audit=[] extra=[]
blocked, well-formed                     gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
unregistered name                        gateway.nope       is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
SECRET in audit: False
dead sink, malformed describe: True Input validation error: '' should be non-empty
== patch @ wip/296-code
allowed, schema-malformed                gateway.describe   is_error=True  text="Input validation error: '' should be non-empty"
                                         audit=['audit.rejection:invalid_arguments'] extra=[{'rejected_argument_path': ['tool_id'], 'rejected_argument_validator': 'minLength'}]
allowed, type error echoing a secret     gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-1' is not of type 'object"
                                         audit=['audit.rejection:invalid_arguments'] extra=[{'rejected_argument_path': ['arguments'], 'rejected_argument_validator': 'type'}]
allowed, pattern error echoing a secret  gateway.invoke     is_error=True  text="Input validation error: 'sk-SECRET-2' is too short"
                                         audit=['audit.rejection:invalid_arguments'] extra=[{'rejected_argument_path': ['evidence_label_digest'], 'rejected_argument_validator': 'minLength'}]
blocked, schema-malformed                gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
blocked, model-only-malformed on pre-A   gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
blocked, well-formed                     gateway.provision  is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
unregistered name                        gateway.nope       is_error=False text='{\n  "error": true,\n  "message": "Gateway tool blocked by pol'
                                         audit=['audit.invocation:denied'] extra=[{}]
SECRET in audit: False
dead sink, malformed describe: False {"error": true, "message": "Scoped advisor audit channel failed"}
```

Read off the probe:

- **Pre-existing on pre-A and main:** a type error (`'sk-SECRET-1' is not of
  type 'object'`) and a pattern/length error on `evidence_label_digest` are
  never audited, and the message echoes the value.
- **Piece A moved** `describe {"tool_id": ""}` from `failure` (pre-A, raised
  by the model inside `call_tool`) to unaudited.
- **The blocked-tool premise, measured precisely.** On pre-A a malformed call
  to a blocked tool was `denied` **only** when the hand-written schema passed
  it (`provision {"server_name": ""}`); one the hand-written schema already
  rejected (`{"server_name": 12345}`) was unaudited and got `Input
  validation error`, disclosing the blocked tool's schema. On main (after A)
  both are unaudited. So "preserve `denied`" means adopting the rule pre-A
  had by accident and applying it uniformly: this plan records **both** as
  `denied` — a superset of pre-A.
- **Dead sink.** Pre-A, the `""` case (reached `call_tool`) got "Scoped
  advisor audit channel failed". On main it gets `Input validation error`:
  a malformed call is answered even though the audit can no longer record
  it. The patch restores fail-closed behaviour for every gate rejection.

### Path segments: where instance data can enter `error.absolute_path`

jsonschema 4.25.1 (`uv run python -c "import importlib.metadata as m;
print(m.version('jsonschema'))"`; mcp 2.0.0). `absolute_path` is built from
the `path=` of each `descend()`: `properties` descends with the **declared**
name; `items`/`prefixItems`/`contains` with an **index**;
`additionalProperties` and `patternProperties` with the **caller's key**.
`propertyNames` descends with no path (the key is the error's `instance`,
which the record never reads). An `additionalProperties: false` error sits at
the *parent* object's path and names the extra key only in `message`.

Survey of the 26 advertised schemas on main (`survey.py`, below, plus a
`json.dumps` scan for the combinators — last line):

```text
26
gateway.catalog_search properties.filters.properties.tags items {"type": "string"}
gateway.invoke properties.arguments additionalProperties true
gateway.invoke properties.task.properties.metadata additionalProperties true
gateway.invoke properties.task.properties.requestor_context additionalProperties true
gateway.invoke properties._meta additionalProperties true
gateway.set_startup_policy properties.names items {"type": "string"}
gateway.request_capability properties.available_clis items {"type": "string"}
gateway.sync_environment properties.detected_clis items {"type": "string"}
gateway.tasks_list properties.requestor_context additionalProperties true
gateway.tasks_get properties.requestor_context additionalProperties true
gateway.tasks_result properties.requestor_context additionalProperties true
gateway.tasks_cancel properties.requestor_context additionalProperties true
gateway.register_discovered_server properties.env_vars items {"type": "string"}
anyOf/oneOf/allOf/$schema hits: []
```

No schema uses `patternProperties`, `$ref`, `anyOf`/`oneOf`/`allOf`, or has a
`$schema` (so `validator_for` picks Draft 2020-12). Caller-keyed descent exists only
through `additionalProperties: true` (`invoke.arguments`, `invoke._meta`,
`invoke.task.metadata`, `invoke.task.requestor_context`,
`tasks_*.requestor_context`), which can never fail. `items` appears on five
string arrays. **So on `main` the caller-key class is empty, but only by the
current schemas' shape** — any future `dict[str, int]` field
(`additionalProperties: {"type": "integer"}`) fills it. The patch redacts it
by construction and the tests exercise it with a synthetic schema.

`error.path` vs `error.absolute_path`: `best_match` can return an `anyOf`
child from `context`, whose `.path` is relative. Measured with
`{"a": {"anyOf": [{"properties": {"b": {"type": "string"}}}, {"type":
"integer"}]}}` on `{"a": {"b": 1}}`: `validator='type'`, `path=['b']`,
`absolute_path=['a', 'b']`.

### Deep nesting: an exception the gate does not catch

In-process, `jsonschema.validate` on a `tool_id` that is a 100,000-deep
nested list raises **`RecursionError`** (the error message `repr`s the
instance), which escapes `_handle_call_tool` unaudited on main and with the
patch. On the wire it cannot arrive: the SDK parses stdio frames with
`types.jsonrpc_message_adapter.validate_json` (`mcp/server/stdio.py:189`),
and measured with that adapter a nesting depth of 150 parses (and the gate
returns `ValidationError`), while 200, 254, 255, 1000 and 5000 are refused
before the handler: `Invalid JSON: recursion limit exceeded`. Out of scope
(see *Non-goals*).

## Design

### 1. `event = "audit.rejection"`, `terminal_status = "invalid_arguments"`

Two module constants in `scoped_advisor_audit.py`: `REJECTION_EVENT` and
`INVALID_ARGUMENTS_STATUS`. The event is new for the reason in *The
downstream reader*: nothing was invoked, and the record cannot carry the
correlations an invocation record is judged by. The status name, for an
operator reading the stream, is not `failure`: in the current vocabulary
`failure` means the call entered `call_tool` and either a handler returned
`ok: false` or an audited check raised (`server.py:405-412`, `:436-450`) —
a consumer counting `failure` as "dispatch began" would be wrong. Not
`denied`: that is policy (`:322`, `:410`, `:440-443`). A pydantic
*model-only* rejection inside a handler (a gate/model disagreement, see the
#236 plan's differential) still records `failure`; that is correct — it did
reach the handler — and is the same as today.

### 2. Policy first, read once

```python
allowed = self._policy_manager.is_gateway_tool_allowed(name)
if tool is not None and allowed:
    ...gate...
```

and in `call_tool`, `if not allowed:` replaces the second
`is_gateway_tool_allowed(name)` call. A blocked name skips the gate and is
recorded `denied` by the existing arm (unchanged code). Reasons:

- The verdict does not depend on arguments, so a blocked tool's attempt is
  `denied` whatever its shape (restores and generalises pre-A; probe above).
- A blocked tool's schema is no longer disclosed through `Input validation
  error` messages to a caller that may not call it.
- **Read once, not twice.** With two reads, a predicate that answered
  "blocked" at the gate and "allowed" in `call_tool` would dispatch
  *ungated* arguments to a handler — exactly what X1 of #236 exists to
  prevent. Policy is loaded once at start-up today (`PolicyManager` has no
  reload path), so this is structural rather than a live race, and it is
  pinned by a flipping-stub test (mutant M2).
- The unregistered-name path is unchanged: `tool is None` skips the gate;
  `call_tool` then records `denied` (blocked) or raises `Unknown tool`
  inside the audited path (X1 guard, `:329-336`, untouched).

### 3. `record_rejected_arguments` — provenance of every key

A new method on `ScopedAdvisorAudit`, called only from the gate. Inputs:
`gateway_tool` (the **registry's** `tool.name`, not the caller's string —
equal, since `_find_gateway_tool` matched it), the `ValidationError`, the
tool's schema, and `arguments` (read for container *types* only). It reads
exactly two attributes of the error: `absolute_path` and `validator`.

| key | value | provenance |
|---|---|---|
| `event` | `"audit.rejection"` | constant |
| `sequence`, `schema`, `audit_session_id`, `timestamp`, `policy_digest` | as every record | writer state |
| `gateway_tool` | `tool.name` if in `_SCOPED_GATEWAY_TOOLS`, else `null` | registry; under the only policy that activates the audit, always one of the four, so always named |
| `gateway_tool_digest` | `sha256(tool.name)` | registry |
| `terminal_status` | `"invalid_arguments"` | constant |
| `rejected_argument_path` | list of `str` / `int` / `null` | see below |
| `rejected_argument_validator` | `error.validator` if it is a `str` key of `validator_for(schema).VALIDATORS`, else `null` | the draft's keyword table |

Eleven keys, pinned exactly by `_REJECTION_RECORD_KEYS`. No correlation,
`downstream_tool_id`, `source_reference_hash`, `evidence_label_digest` or
`redacted_result_digest` — each is an argument value or a hash of one (see
*What `record_invocation` would leak*), and there is no result to digest.

**Path proof.** For each segment of `error.absolute_path`, walking
`arguments` alongside:

- `list` container and `type(segment) is int` → the index. A position, not a
  value.
- `dict` container and `type(segment) is str` (exactly `str`: a subclass
  could override `__eq__`/`__hash__` to pass the membership test while
  carrying a secret — in-process only, pinned by mutant M4b) and
  `segment in declared` → the segment. `declared` is every key of every `properties` map anywhere
  in the tool's schema, so the emitted string is equal to a string the
  schema's author wrote and discloses nothing the advertised schema does
  not. A caller key that happens to equal a declared name elsewhere (e.g.
  `env.tool_id`) is kept — equality with a public string is not a leak.
- anything else → `null`. This covers keys matched by
  `additionalProperties`/`patternProperties`, and an in-process `int` dict
  key (which a JSON object cannot carry, but `params.arguments` nested
  values are `Any`).

The walk reads `arguments` only through `isinstance(node, list|dict)` and
`node[segment]`; no value is copied out. **The key an `additionalProperties:
false` error names** (piece B) is only in `message` — the path is the parent
object — and `message` is never read. The record never reads `message`,
`instance`, `validator_value`, `schema`, `context`, `cause` or `json_path`;
a test swaps the error's class for one whose value-bearing attributes raise
on access, and the record is still written.

The client-facing response to an allowed tool's rejection is **byte
identical** to main (`Input validation error: {e.message}`). Its value echo
is Consiliency/pmcp#297's scope, not this issue's.

### 4. Dead sink at the gate

If the gate's record raises `ScopedAdvisorAuditError` (sink closed or
failed), return the same `{"error": true, "message": "Scoped advisor audit
channel failed"}` payload `call_tool` returns (`:423-434`), without
`is_error`, like it.

### 5. Every exit of `_handle_call_tool`, before and after

Patched line numbers (`wip/296-code`):

| # | exit | main | patched |
|---|---|---|---|
| E1 | gate `ValidationError` on an **allowed** registered tool | return, **unaudited** | `:306-340`: `audit.rejection` / `invalid_arguments`, return the unchanged error |
| E2 | E1 with a dead sink | return validation error, unaudited | `:315-329`: return "channel failed" (unrecordable by definition) |
| E3 | gate on a **blocked** registered tool | E1 (unaudited, schema disclosed) | gate skipped; E4 |
| E4 | blocked name (`if not allowed`) | `:315-327` `denied` | `:344-357` `denied` (same code, single verdict) |
| E5 | unregistered name, allowed | `raise Unknown tool` → `failure` | unchanged (`:360-366`) |
| E6 | scoped `invoke` without correlations | raise → `failure` | unchanged |
| E7 | dispatch returns | `success`/`failure`/`denied` | unchanged |
| E8 | `except ScopedAdvisorAuditError` (from `_require_scoped_audit` or a record) | "channel failed", unrecordable | unchanged |
| E9 | `except Exception` → record → return | recorded | unchanged |
| E10 | E9 whose record raises `ScopedAdvisorAuditError` | "channel failed", unrecordable | unchanged |
| E11 | exception escaping the handler: `jsonschema.SchemaError` | — | unchanged; unreachable, every advertised schema passes `Draft202012Validator.check_schema` (`tests/test_gateway_tool_schemas.py:402`) |
| E12 | exception escaping: `RecursionError` from `validate` | in-process only | unchanged; the wire caps nesting below 200 (measured above) — *Non-goals* |
| E13 | exception escaping: `is_gateway_tool_allowed` | inside `call_tool`'s `try` (would be `failure`) | now evaluated before `call_tool`; it is `fnmatch` over the loaded policy and does not raise on a `str` — *Unverified* |
| E14 | `CancelledError` / `BaseException` | propagates | unchanged |
| E15 | SDK rejection of `CallToolRequestParams` before the handler (e.g. `arguments` not an object) | never reaches `_handle_call_tool` | unchanged — *Non-goals* |

## Changes

### `src/pmcp/scoped_advisor_audit.py` (modify; patch under *Verbatim bodies*, +112 / −0)

- `import jsonschema`; `REJECTION_EVENT = "audit.rejection"`;
  `INVALID_ARGUMENTS_STATUS = "invalid_arguments"`.
- `_declared_property_names(schema) -> frozenset[str]`.
- `_rejected_argument_path(error, schema, arguments) -> list[str | int | None]`.
- `_rejected_argument_validator(error, schema) -> str | None`.
- `ScopedAdvisorAudit.record_rejected_arguments(*, gateway_tool, error, schema, arguments)`.

`record_invocation` is untouched.

### `src/pmcp/server.py` (modify; patch under *Verbatim bodies*, +31 / −2)

The gate becomes `if tool is not None and allowed:` with `allowed` read
once; the `except jsonschema.ValidationError` arm records the rejection
(or returns "channel failed"); `call_tool`'s policy arm reads `allowed`.

### `tests/test_scoped_advisor_audit.py` (modify; patch under *Verbatim bodies*, +307 / −0)

Adds `import jsonschema` and thirteen test items (bodies in the patch):

| test | pins |
|---|---|
| `test_gate_rejections_are_audited_without_argument_values` | five real gate rejections through the SDK handler (`type` on `arguments`, `pattern` on `evidence_label_digest`, `type` on `run_correlation_id`, nested `task.enabled`, `required`): **zero** `audit.invocation` records (the consumer-safety pin), five `audit.rejection` / `invalid_arguments`, exact `(path, keyword)` pairs, exact key set, secret substrings, correlations and message fragments absent, nothing dispatched, response unchanged |
| `test_policy_is_judged_before_the_gate` | `provision {"server_name": 12345}` → "blocked by policy", one `denied`, no `Input validation error` |
| `test_the_policy_verdict_is_read_once_per_call` | a stub answering blocked-then-allowed; the malformed `describe` is denied and never dispatched |
| `test_gate_rejection_with_a_dead_sink_fails_closed` | closed sink → "channel failed" payload |
| `test_rejected_argument_path_redacts_caller_chosen_keys[×7]` | `additionalProperties` key → `null`; `patternProperties` key → `null`; caller key equal to a declared name kept; in-process `int` key → `null`; a `str` subclass whose `__eq__` matches every declared name → `null`; array index kept; `anyOf` child → absolute path |
| `test_an_unknown_validator_keyword_is_not_recorded` | a keyword outside the draft's table → `null` |
| `test_the_rejection_record_never_reads_the_message_or_the_instance` | `_PoisonedError`: `message`, `instance`, `validator_value`, `context`, `cause`, `json_path`, `schema` raise on access; the record is still written with the exact key set |

### Test bodies

Verbatim in the test patch (extract with the heading prefix
``### Patch — `test_scoped_advisor_audit``, under *Verbatim bodies*),
appended after `test_capability_probe_is_machine_readable` under the banner
`# --- gate rejections reach the audit (Consiliency/pmcp#296) ---`, with the
helpers `_REJECTION_RECORD_KEYS`, `_scoped_server`, `_call`, `_first_error`,
`_record_one`, `_EqualsEverything`, `_CALLER_KEYED_SCHEMA` and
`_PoisonedError`:

1. `test_gate_rejections_are_audited_without_argument_values`
2. `test_policy_is_judged_before_the_gate`
3. `test_the_policy_verdict_is_read_once_per_call`
4. `test_gate_rejection_with_a_dead_sink_fails_closed`
5. `test_rejected_argument_path_redacts_caller_chosen_keys` (7 cases)
6. `test_an_unknown_validator_keyword_is_not_recorded`
7. `test_the_rejection_record_never_reads_the_message_or_the_instance`

What each pins is in the table above; which mutant each kills is in
*Mutation evidence*.

### `CHANGELOG.md` (modify) — `[Unreleased]` → `### Fixed`

A new first entry under `### Fixed`, and the piece-A entry's "Scoped-audit
change until Consiliency/pmcp#296 lands" sentence is replaced by one
sentence pointing at it (both are unreleased, so the release notes must not
contradict each other). Patch under *Verbatim bodies*.

### `README.md` (modify, `:1517-1520`)

One sentence on the `audit.rejection` event in the *Scoped advisor
research* section.

## Verification

```bash
cd <worktree of origin/main>
uv sync --all-extras -p 3.10                    # fresh worktree: else pytest is the system one
# apply (see *Verbatim bodies → how to apply*)
uv run pytest tests/test_scoped_advisor_audit.py -p no:cacheprovider --cov-fail-under=0 -q
                                                # expect 25 passed (12 existing + 13 new)
uv run ruff check src/ tests/                   # expect "All checks passed!"
uv run ruff format --check src/ tests/          # expect "163 files already formatted"
uv run mypy src/pmcp --exclude baml_client     # CI gate (test.yml:388); expect "no issues found in 50 source files"
env -u npm_config_cache -u npm_config_store_dir -u pnpm_config_store_dir \
  uv run pytest -m 'not live and not slow' -p no:cacheprovider -q
                                                # full suite; run as a background task that notifies on exit
```

## Acceptance criteria — measured this session

Tree: `wip/296-code` @ `9ced94f`, base `959d4d4`.

- [x] **Red on main.** The new test module run against main's `src/`
  (`PYTHONPATH=<main worktree>/src`, the patched tests): `13 failed, 12 passed in 2.94s` —
  exactly the 13 new items fail, the 12 existing pass.
- [x] **Green with the patch:** `25 passed in 2.89s`.
- [x] `ruff check src/ tests/`: `All checks passed!`
- [x] `ruff format --check src/ tests/`: `163 files already formatted`
- [x] `mypy src/pmcp --exclude baml_client`: `Success: no issues found in 50 source files`
- [x] Full suite, patched: `4349 passed, 3 skipped, 25 deselected in 418.91s (0:06:58) (exit=0)`
- [x] Full suite, main `959d4d4` (same host, same command): `4336 passed, 3 skipped, 25 deselected in 419.27s (0:06:59) (exit=0)`
  Difference: +13 passed = exactly the 13 new test items; skips and deselections unchanged.
- [x] Mutation run: `14/14 killed` (see *Mutation evidence*).
- [x] Embedding proof: five patches extracted from this file apply to
  `959d4d4` and the result is `cmp`-equal to `wip/296-code` on all five files.
- [ ] Panel CR + reconcile before merge (repo rule).
- [ ] Piece B of Consiliency/pmcp#236 does not merge before this.

## Mutation evidence

`mutants.py` (below) applies each mutant as one exact-string replacement to a
saved copy, runs `tests/test_scoped_advisor_audit.py`, and restores from the
saved copy (never `git checkout`). Output, verbatim from the log:

```text
M1 policy not judged before the gate: KILLED | 2 failed, 23 passed in 2.84s | test_policy_is_judged_before_the_gate, test_the_policy_verdict_is_read_once_per_call
M2 call_tool re-reads the policy verdict: KILLED | 1 failed, 24 passed in 2.81s | test_the_policy_verdict_is_read_once_per_call
M3 relative .path instead of .absolute_path: KILLED | 1 failed, 24 passed in 2.93s | test_rejected_argument_path_redacts_caller_chosen_keys
M4 caller keys not checked against declared names: KILLED | 3 failed, 22 passed in 2.86s | test_rejected_argument_path_redacts_caller_chosen_keys, test_the_rejection_record_never_reads_the_message_or_the_instance
M4b str subclass accepted by isinstance: KILLED | 1 failed, 24 passed in 2.95s | test_rejected_argument_path_redacts_caller_chosen_keys
M5 int segment kept without a list container: KILLED | 1 failed, 24 passed in 2.80s | test_rejected_argument_path_redacts_caller_chosen_keys
M6 validator keyword not bounded: KILLED | 1 failed, 24 passed in 2.89s | test_an_unknown_validator_keyword_is_not_recorded
M7 gate rejection not recorded: KILLED | 2 failed, 23 passed in 3.00s | test_gate_rejection_with_a_dead_sink_fails_closed, test_gate_rejections_are_audited_without_argument_values
M8 dead sink not caught at the gate: KILLED | 1 failed, 24 passed in 3.08s | test_gate_rejection_with_a_dead_sink_fails_closed
M9 correlation copied from unvalidated arguments: KILLED | 2 failed, 23 passed in 2.88s | test_gate_rejections_are_audited_without_argument_values, test_the_rejection_record_never_reads_the_message_or_the_instance
M10 jsonschema message recorded: KILLED | 2 failed, 23 passed in 2.83s | test_gate_rejections_are_audited_without_argument_values, test_the_rejection_record_never_reads_the_message_or_the_instance
M11 terminal_status failure: KILLED | 1 failed, 24 passed in 2.85s | test_gate_rejections_are_audited_without_argument_values
M13 rejection written as an audit.invocation: KILLED | 1 failed, 24 passed in 3.05s | test_gate_rejections_are_audited_without_argument_values
M12 declared names from the top level only: KILLED | 2 failed, 23 passed in 2.78s | test_gate_rejections_are_audited_without_argument_values, test_rejected_argument_path_redacts_caller_chosen_keys
14/14 killed
```

## Non-goals

- **Value echo in the response** (`Input validation error: 'sk-…' is not of
  type …`). Pre-existing, unchanged, and the caller's own value; tracked on
  Consiliency/pmcp#297 with the pydantic echo.
- **Surfacing rejections in the advisor-board ledger.** agent-harness skips
  `audit.rejection` today, which is the safe default. Counting them (e.g. a
  per-seat "rejected attempts" figure) is an agent-harness change; file an
  issue there if wanted.
- **Correlating a rejected call to its run/seat.** A rejected payload's
  correlation fields are unvalidated, and most API keys fit the correlation
  charset, so the gate record leaves them `null`. An operator sees the
  attempt, the tool, where and why it failed, and its position in the
  sequence; not which run sent it.
- **`RecursionError` on deeply nested in-process arguments (E12).** The wire
  cannot deliver them (measured). Catching it would mean treating an
  arbitrary exception from `validate` as a rejection; a separate issue if
  in-process embedders matter.
- **SDK-level rejections before `_handle_call_tool` (E15)** — a JSON-RPC
  error, not a tool call.
- Changing `record_invocation`, the statuses of existing paths, or the
  response shape of any existing path.
- Piece B itself.

## Unverified

- **Consumers outside `~/code/agent-harness` and `~/.local/share/agent-harness`.**
  The one reader found skips unknown events (measured from source, not run
  against a patched stream). Any other reader that rejects unknown `event`
  values would need `audit.rejection`.
- **E13**: that `is_gateway_tool_allowed` cannot raise on a `str` name was
  read from `policy.py:399-423,524-534` (`fnmatch` over compiled lists), not
  proven by test.

## Execution Policy

- execute: effort=low, reason=the patch is embedded verbatim and proven
  byte-identical to verified code; the executor applies, runs the
  Verification block and compares with *Acceptance criteria*.
- Every PR to main needs panel CR + reconcile first (repo rule).
- Commit/PR text says "see Consiliency/pmcp#296", never a closing keyword
  until the maintainer decides.

## Embedding proof

```text
$ git -C <fresh worktree> rev-parse --short HEAD
959d4d4
<scratch>/emb/server.patch: 54 lines
<scratch>/emb/scoped_advisor_audit.patch: 138 lines
<scratch>/emb/test_scoped_advisor_audit.patch: 322 lines
<scratch>/emb/CHANGELOG.patch: 14 lines
<scratch>/emb/README.patch: 16 lines
$ git apply --check <scratch>/emb/*.patch
check: ok
applied
cmp src/pmcp/server.py: identical to wip/296-code@9ced94f
cmp src/pmcp/scoped_advisor_audit.py: identical to wip/296-code@9ced94f
cmp tests/test_scoped_advisor_audit.py: identical to wip/296-code@9ced94f
cmp CHANGELOG.md: identical to wip/296-code@9ced94f
cmp README.md: identical to wip/296-code@9ced94f
$ git status --short
 M CHANGELOG.md
 M README.md
 M src/pmcp/scoped_advisor_audit.py
 M src/pmcp/server.py
 M tests/test_scoped_advisor_audit.py
```

## Verbatim bodies

### How to apply (and the extractor)

From a fresh worktree of `origin/main` @ `959d4d4`:

```bash
PLAN=.consiliency/plans/detailed-296-gate-audit-20260926-2141.md   # read from branch plan/296-gate-audit
X=<scratch>/extract_plan_block.py        # the script below, saved verbatim
for f in server scoped_advisor_audit test_scoped_advisor_audit CHANGELOG README; do
  python3 $X $PLAN "### Patch — \`$f" <scratch>/$f.patch
done
git apply --check <scratch>/*.patch && git apply <scratch>/*.patch
```

Patches are fenced with **four** backticks, and the extractor closes on the
same fence string, so a context line of three backticks cannot end a block
early. Blank context lines carry one leading space: an editor that strips
trailing whitespace breaks them and `git apply --check` fails loudly.

````python
"""Extract one verbatim patch from the Consiliency/pmcp#296 plan, byte for byte.

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
````

### Patch — `server.py` (`src/pmcp/server.py`)

````diff
diff --git a/src/pmcp/server.py b/src/pmcp/server.py
index 193ffe1..b50096e 100644
--- a/src/pmcp/server.py
+++ b/src/pmcp/server.py
@@ -294,10 +294,39 @@ class GatewayServer:
         arguments = params.arguments or {}
 
         tool = self._find_gateway_tool(name)
-        if tool is not None:
+        # Policy is judged once, before the schema (Consiliency/pmcp#296): a
+        # blocked name is recorded `denied` by `call_tool` whatever its
+        # arguments, and its schema is never disclosed through a validation
+        # message. `call_tool` reads this same verdict, so no name can skip
+        # the gate as "blocked" and then be dispatched as "allowed".
+        allowed = self._policy_manager.is_gateway_tool_allowed(name)
+        if tool is not None and allowed:
             try:
                 jsonschema.validate(instance=arguments, schema=tool.input_schema)
             except jsonschema.ValidationError as e:
+                try:
+                    if self._scoped_advisor_audit is not None:
+                        self._scoped_advisor_audit.record_rejected_arguments(
+                            gateway_tool=tool.name,
+                            error=e,
+                            schema=tool.input_schema,
+                            arguments=arguments,
+                        )
+                except ScopedAdvisorAuditError:
+                    logger.error("Scoped advisor audit channel failed")
+                    return CallToolResult(
+                        content=[
+                            TextContent(
+                                type="text",
+                                text=json.dumps(
+                                    {
+                                        "error": True,
+                                        "message": "Scoped advisor audit channel failed",
+                                    }
+                                ),
+                            )
+                        ]
+                    )
                 return CallToolResult(
                     is_error=True,
                     content=[
@@ -312,7 +341,7 @@ class GatewayServer:
                 result: Any
                 self._require_scoped_audit()
 
-                if not self._policy_manager.is_gateway_tool_allowed(name):
+                if not allowed:
                     payload = {
                         "error": True,
                         "message": f"Gateway tool blocked by policy: {name}",
````

### Patch — `scoped_advisor_audit.py` (`src/pmcp/scoped_advisor_audit.py`)

````diff
diff --git a/src/pmcp/scoped_advisor_audit.py b/src/pmcp/scoped_advisor_audit.py
index 8dfb4ee..48f30e0 100644
--- a/src/pmcp/scoped_advisor_audit.py
+++ b/src/pmcp/scoped_advisor_audit.py
@@ -13,7 +13,16 @@ from pathlib import Path
 from typing import Any, TextIO
 from urllib.parse import urlsplit, urlunsplit
 
+import jsonschema
+
 SCOPED_ADVISOR_AUDIT_CAPABILITY = "scoped_advisor_audit.v1"
+#: Event of a ``tools/call`` the input-schema gate rejected before dispatch
+#: (Consiliency/pmcp#296). Not an ``audit.invocation``: nothing was invoked, and
+#: a reader that correlates invocations to a run must not see an uncorrelated
+#: one (agent-harness ``advisor_board/research.py`` filters on this field).
+REJECTION_EVENT = "audit.rejection"
+#: Its ``terminal_status``. The invocation vocabulary stays success/failure/denied.
+INVALID_ARGUMENTS_STATUS = "invalid_arguments"
 _CORRELATION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
 _DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
 _TOOL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+::[A-Za-z0-9_.-]{1,192}$")
@@ -129,6 +138,70 @@ def _public_source_hash(value: Any) -> str | None:
     return None
 
 
+def _declared_property_names(schema: Any) -> frozenset[str]:
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
+def _rejected_argument_path(
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
+    declared = _declared_property_names(schema)
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
+def _rejected_argument_validator(
+    error: jsonschema.ValidationError, schema: Any
+) -> str | None:
+    """The failing keyword, if it is one the schema's draft defines."""
+    keywords = jsonschema.validators.validator_for(schema).VALIDATORS
+    validator = error.validator
+    return validator if isinstance(validator, str) and validator in keywords else None
+
+
 class ScopedAdvisorAudit:
     """Append-only JSONL writer with a single fsynced terminal marker."""
 
@@ -227,6 +300,45 @@ class ScopedAdvisorAudit:
             }
         )
 
+    def record_rejected_arguments(
+        self,
+        *,
+        gateway_tool: str,
+        error: jsonschema.ValidationError,
+        schema: dict[str, Any],
+        arguments: dict[str, Any],
+    ) -> None:
+        """Record a ``tools/call`` the input-schema gate rejected (Consiliency/pmcp#296).
+
+        The record names the tool, the failing JSON path and the validator
+        keyword, and nothing else from the call. It never reads the error's
+        ``message``, ``instance``, ``validator_value`` or ``context``: a
+        ``type``/``pattern``/``enum`` message echoes the rejected value, which
+        can be a secret. It carries no correlation, source hash or digest of
+        any argument either, because an unvalidated payload is exactly where a
+        caller can put a secret in a correlation-shaped field. It is its own
+        event, not an ``audit.invocation``, for the same reason: without
+        correlations it would read as a mismatched invocation.
+        """
+        argument_path = _rejected_argument_path(error, schema, arguments)
+        validator = _rejected_argument_validator(error, schema)
+        self._write(
+            {
+                "event": REJECTION_EVENT,
+                "schema": SCOPED_ADVISOR_AUDIT_CAPABILITY,
+                "audit_session_id": self.audit_session_id,
+                "timestamp": time.time(),
+                "policy_digest": self.policy_digest,
+                "gateway_tool": gateway_tool
+                if gateway_tool in _SCOPED_GATEWAY_TOOLS
+                else None,
+                "gateway_tool_digest": _digest(gateway_tool),
+                "terminal_status": INVALID_ARGUMENTS_STATUS,
+                "rejected_argument_path": argument_path,
+                "rejected_argument_validator": validator,
+            }
+        )
+
     def complete(self) -> None:
         if self._completed:
             return
````

### Patch — `test_scoped_advisor_audit.py` (`tests/test_scoped_advisor_audit.py`)

````diff
diff --git a/tests/test_scoped_advisor_audit.py b/tests/test_scoped_advisor_audit.py
index 6ae178c..501e2c3 100644
--- a/tests/test_scoped_advisor_audit.py
+++ b/tests/test_scoped_advisor_audit.py
@@ -9,6 +9,7 @@ from pathlib import Path
 from typing import Any
 from unittest.mock import MagicMock
 
+import jsonschema
 import pytest
 from mcp.server.connection import Connection
 from mcp.server.context import ServerRequestContext
@@ -578,3 +579,309 @@ def test_capability_probe_is_machine_readable() -> None:
     assert (
         "terminal_completion_fsync" in payload["capabilities"][0]["activation_requires"]
     )
+
+
+# --- gate rejections reach the audit (Consiliency/pmcp#296) --------------------
+
+#: The exact key set of every ``audit.rejection`` record. A new key is a new
+#: channel out of an unvalidated payload, so it must be added here on purpose.
+_REJECTION_RECORD_KEYS = frozenset(
+    {
+        "sequence",
+        "event",
+        "schema",
+        "audit_session_id",
+        "timestamp",
+        "policy_digest",
+        "gateway_tool",
+        "gateway_tool_digest",
+        "terminal_status",
+        "rejected_argument_path",
+        "rejected_argument_validator",
+    }
+)
+
+
+def _scoped_server(tmp_path: Path) -> tuple[GatewayServer, Path]:
+    audit_path = tmp_path / "audit.jsonl"
+    server = GatewayServer(
+        policy_path=_write_scoped_policy(tmp_path / "policy.json"),
+        audit_jsonl=audit_path,
+    )
+    server._create_server()
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
+@pytest.mark.asyncio
+async def test_gate_rejections_are_audited_without_argument_values(
+    tmp_path: Path,
+) -> None:
+    server, audit_path = _scoped_server(tmp_path)
+    dispatched: list[dict] = []
+
+    async def recording_invoke(arguments: dict) -> dict:
+        dispatched.append(arguments)
+        return {"ok": True}
+
+    server._gateway_tools.invoke = recording_invoke  # type: ignore[method-assign]
+    cases = [
+        # (arguments, expected path, expected keyword)
+        (
+            {"tool_id": "firecrawl::web_search", "arguments": "sk-TYPE-SECRET"},
+            ["arguments"],
+            "type",
+        ),
+        (
+            {
+                "tool_id": "firecrawl::web_search",
+                **_correlations(),
+                "evidence_label_digest": "sk-PATTERN-SECRET" + "Z" * 47,
+            },
+            ["evidence_label_digest"],
+            "pattern",
+        ),
+        (
+            {
+                "tool_id": "firecrawl::web_search",
+                **_correlations(),
+                "run_correlation_id": ["run-LIST-SECRET"],
+            },
+            ["run_correlation_id"],
+            "type",
+        ),
+        (
+            {"tool_id": "firecrawl::web_search", "task": {"enabled": "sk-ENUM"}},
+            ["task", "enabled"],
+            "type",
+        ),
+        ({"arguments": {"q": "sk-MISSING-SECRET"}}, [], "required"),
+    ]
+    for arguments, _, _ in cases:
+        result = await _call(server, "gateway.invoke", arguments)
+        # The caller still gets the gate's own message, unchanged.
+        assert result.is_error is True
+        assert result.content[0].text.startswith("Input validation error: ")
+    assert dispatched == []
+    await server.shutdown()
+
+    records = validate_scoped_advisor_audit(audit_path)
+    # No `audit.invocation` at all: agent-harness's board ledger
+    # (`advisor_board/research.py:465-481` @ b9627d53) correlates every
+    # `gateway.invoke` invocation to its run, and a record without
+    # correlations would fail the seat as `audit_correlation_mismatch`.
+    assert [r for r in records if r["event"] == "audit.invocation"] == []
+    rejections = [r for r in records if r["event"] == "audit.rejection"]
+    assert [r["terminal_status"] for r in rejections] == ["invalid_arguments"] * len(
+        cases
+    )
+    assert [
+        (r["rejected_argument_path"], r["rejected_argument_validator"])
+        for r in rejections
+    ] == [(path, keyword) for _, path, keyword in cases]
+    for record in rejections:
+        assert set(record) == _REJECTION_RECORD_KEYS
+        assert record["gateway_tool"] == "gateway.invoke"
+    raw_audit = audit_path.read_text()
+    for forbidden in (
+        "sk-TYPE-SECRET",
+        "sk-PATTERN-SECRET",
+        "run-LIST-SECRET",
+        "sk-ENUM",
+        "sk-MISSING-SECRET",
+        "run-103",
+        "seat-codex",
+        "firecrawl::web_search",
+        "is not of type",
+        "does not match",
+    ):
+        assert forbidden not in raw_audit
+
+
+@pytest.mark.asyncio
+async def test_policy_is_judged_before_the_gate(tmp_path: Path) -> None:
+    """A malformed call to a blocked tool is `denied`, not `invalid_arguments`.
+
+    `gateway.provision` is outside the scoped policy. Its argument is the
+    wrong type, which the gate would reject, but policy answers first and
+    the blocked tool's schema is not disclosed.
+    """
+    server, audit_path = _scoped_server(tmp_path)
+    result = await _call(
+        server, "gateway.provision", {"server_name": 12345, "api_key": "sk-X"}
+    )
+    text = result.content[0].text
+    assert "blocked by policy" in json.loads(text)["message"]
+    assert "Input validation error" not in text
+    await server.shutdown()
+    records = validate_scoped_advisor_audit(audit_path)
+    assert [
+        r["terminal_status"] for r in records if r["event"] == "audit.invocation"
+    ] == ["denied"]
+    assert "sk-X" not in audit_path.read_text()
+
+
+@pytest.mark.asyncio
+async def test_the_policy_verdict_is_read_once_per_call(tmp_path: Path) -> None:
+    """No name can skip the gate as blocked and then be dispatched as allowed.
+
+    The stub answers "blocked" the first time and "allowed" after. Reading
+    the verdict twice would skip the schema gate and then dispatch the
+    malformed arguments to the handler.
+    """
+    server, _ = _scoped_server(tmp_path)
+    verdicts = iter([False, True, True, True])
+    server._policy_manager.is_gateway_tool_allowed = (  # type: ignore[method-assign]
+        lambda name: next(verdicts)
+    )
+    dispatched: list[dict] = []
+
+    async def recording_describe(arguments: dict) -> dict:
+        dispatched.append(arguments)
+        return {"ok": True}
+
+    server._gateway_tools.describe = recording_describe  # type: ignore[method-assign]
+    result = await _call(server, "gateway.describe", {"tool_id": ""})
+    assert "blocked by policy" in json.loads(result.content[0].text)["message"]
+    assert dispatched == []
+    await server.shutdown()
+
+
+@pytest.mark.asyncio
+async def test_gate_rejection_with_a_dead_sink_fails_closed(tmp_path: Path) -> None:
+    server, _ = _scoped_server(tmp_path)
+    assert server._scoped_advisor_audit is not None
+    assert server._scoped_advisor_audit._file is not None
+    server._scoped_advisor_audit._file.close()
+    result = await _call(server, "gateway.describe", {"tool_id": ""})
+    assert json.loads(result.content[0].text) == {
+        "error": True,
+        "message": "Scoped advisor audit channel failed",
+    }
+    assert not result.is_error
+
+
+def _first_error(instance: Any, schema: dict[str, Any]) -> jsonschema.ValidationError:
+    error = jsonschema.exceptions.best_match(
+        jsonschema.validators.validator_for(schema)(schema).iter_errors(instance)
+    )
+    assert error is not None
+    return error
+
+
+def _record_one(
+    tmp_path: Path,
+    error: jsonschema.ValidationError,
+    schema: dict[str, Any],
+    arguments: Any,
+) -> dict[str, Any]:
+    path = tmp_path / f"audit-{len(list(tmp_path.iterdir()))}.jsonl"
+    audit = ScopedAdvisorAudit(path, policy_digest="e" * 64)
+    audit.record_rejected_arguments(
+        gateway_tool="gateway.invoke", error=error, schema=schema, arguments=arguments
+    )
+    audit.complete()
+    records = validate_scoped_advisor_audit(path)
+    assert "sk-" not in path.read_text()
+    return records[1]
+
+
+class _EqualsEverything(str):
+    """A ``str`` subclass that claims to equal any declared name."""
+
+    def __eq__(self, other: object) -> bool:
+        return True
+
+    def __hash__(self) -> int:
+        return hash("tool_id")
+
+
+_CALLER_KEYED_SCHEMA: dict[str, Any] = {
+    "type": "object",
+    "properties": {
+        "env": {"type": "object", "additionalProperties": {"type": "integer"}},
+        "hdrs": {
+            "type": "object",
+            "patternProperties": {"^x-": {"type": "integer"}},
+        },
+        "names": {"type": "array", "items": {"type": "string"}},
+        "tool_id": {"type": "string"},
+        "a": {
+            "anyOf": [
+                {"type": "object", "properties": {"b": {"type": "string"}}},
+                {"type": "integer"},
+            ]
+        },
+    },
+}
+
+
+@pytest.mark.parametrize(
+    ("arguments", "expected_path", "keyword"),
+    [
+        # A key the caller chose under additionalProperties is itself redacted.
+        ({"env": {"sk-KEY-SECRET": "v"}}, ["env", None], "type"),
+        # ... and so is one matched by patternProperties.
+        ({"hdrs": {"x-sk-HEADER": "v"}}, ["hdrs", None], "type"),
+        # A caller key that equals a declared name elsewhere discloses nothing.
+        ({"env": {"tool_id": "v"}}, ["env", "tool_id"], "type"),
+        # An in-process int key is not an array index.
+        ({"env": {12345: "v"}}, ["env", None], "type"),
+        # A str subclass cannot smuggle its content past the membership test.
+        ({"env": {_EqualsEverything("sk-EQ-SECRET"): "v"}}, ["env", None], "type"),
+        # An array index survives, as a position.
+        ({"names": ["ok", 7]}, ["names", 1], "type"),
+        # best_match returns an anyOf child: its `.path` is relative (["b"]).
+        ({"a": {"b": 1}}, ["a", "b"], "type"),
+    ],
+)
+def test_rejected_argument_path_redacts_caller_chosen_keys(
+    tmp_path: Path, arguments: dict, expected_path: list, keyword: str
+) -> None:
+    error = _first_error(arguments, _CALLER_KEYED_SCHEMA)
+    record = _record_one(tmp_path, error, _CALLER_KEYED_SCHEMA, arguments)
+    assert record["rejected_argument_path"] == expected_path
+    assert record["rejected_argument_validator"] == keyword
+
+
+def test_an_unknown_validator_keyword_is_not_recorded(tmp_path: Path) -> None:
+    error = jsonschema.ValidationError(
+        "m", validator="sk-KEYWORD-SECRET", path=["tool_id"]
+    )
+    record = _record_one(tmp_path, error, _CALLER_KEYED_SCHEMA, {"tool_id": "x"})
+    assert record["rejected_argument_validator"] is None
+    assert record["rejected_argument_path"] == ["tool_id"]
+
+
+class _PoisonedError(jsonschema.ValidationError):
+    """Every attribute that can carry the rejected value raises on access."""
+
+    def _poisoned(self: Any) -> Any:
+        raise AssertionError("the audit read a value-bearing error attribute")
+
+    message = property(_poisoned)  # type: ignore[assignment]
+    instance = property(_poisoned)  # type: ignore[assignment]
+    validator_value = property(_poisoned)  # type: ignore[assignment]
+    context = property(_poisoned)  # type: ignore[assignment]
+    cause = property(_poisoned)  # type: ignore[assignment]
+    json_path = property(_poisoned)  # type: ignore[assignment]
+    schema = property(_poisoned)  # type: ignore[assignment]
+
+
+def test_the_rejection_record_never_reads_the_message_or_the_instance(
+    tmp_path: Path,
+) -> None:
+    arguments = {"env": {"sk-KEY": "sk-VALUE"}}
+    error = _first_error(arguments, _CALLER_KEYED_SCHEMA)
+    error.__class__ = _PoisonedError
+    record = _record_one(tmp_path, error, _CALLER_KEYED_SCHEMA, arguments)
+    assert set(record) == _REJECTION_RECORD_KEYS
+    assert record["rejected_argument_path"] == ["env", None]
````

### Patch — `CHANGELOG.md`

````diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 12ec2d4..747e811 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -367,7 +367,8 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 
 
 ### Fixed
-- **Gateway tool `inputSchema`s are now derived from the pydantic models that validate the arguments, so the two can no longer disagree (Consiliency/pmcp#236).** Constraints the models always enforced are now advertised and enforced at the transport gate — `minLength` on identifiers, `submit_feedback.title` 8–160 chars, bounds on `tasks_result.options` — so those rejections now come back as an `isError` tool result reading `Input validation error: …` instead of an `{"error": true}` payload. `gateway.invoke` now advertises `task`, `trace_context` and `_meta`; `gateway.tasks_*` advertise `requestor_context`; `tasks_result.options` gains `timeout_ms`. Optional arguments are advertised as `type: [X, "null"]` and the transport gate now accepts an explicit `null` for them, as the handlers always did; 28 optional arguments (e.g. `catalog_search.query`, `invoke.options`, `auth_connect.credential`) were previously rejected at the gate when sent as `null`. The gate does not apply pydantic's lax coercion: values such as `1` for a boolean or `"5"` for an integer on the newly advertised `invoke.task` fields (`enabled`, `ttl`, `poll_interval`), which were previously accepted and coerced, are now rejected with `Input validation error: 1 is not of type 'boolean'`. `invoke.task.ttl` now advertises its range on both sides, so `1e20`, `-1e20` and `float(±2**63)` are rejected at the gate, and so is any integer outside [−2^63+1, 2^63−1] (including `-2**63` itself), which the handler previously accepted. `invoke.evidence_label_digest` now also advertises its exact length (64), so a digest with a trailing newline is rejected at the gate instead of by the handler. **Scoped-audit change until Consiliency/pmcp#296 lands:** gate rejections are not written to the scoped-advisor audit. So a *malformed* call to a *policy-blocked* gateway tool now gets `Input validation error: …` instead of "Gateway tool blocked by policy", and it is **no longer recorded as `denied`**. The same holds for the other inputs the gate now rejects that previously reached the handler and were recorded as `failure`. Well-formed calls to blocked tools are still recorded `denied`. Unknown keys are still ignored in this release — see the following entry once B lands. Argument descriptions agents already saw are unchanged, except `gateway.update_server.force`, which now describes the task-aware behaviour; 19 previously undescribed arguments gain a description.
+- **`tools/call` input-schema rejections are now recorded in the scoped-advisor audit, without argument values (Consiliency/pmcp#296).** A call the transport gate rejects used to return `Input validation error: …` before the audit was reached, so an operator saw no attempt at all. It is now written as a new `audit.rejection` event (not an `audit.invocation`: nothing was invoked, and a reader that correlates invocations to a run skips it) with the tool name, `terminal_status: "invalid_arguments"`, `rejected_argument_path`, the failing location as a JSON array (a key the schema declares, an array index, or `null` for a key the caller chose, since that key can itself be a secret), and `rejected_argument_validator`, the failing JSON Schema keyword (`type`, `pattern`, `required`, …). The record never contains the validation message, the rejected value, correlation IDs, or any digest of the arguments. The capability stays `scoped_advisor_audit.v1`; readers that dispatch on `event` are unaffected. Policy is now judged **before** the schema: a call to a policy-blocked gateway tool is refused with "Gateway tool blocked by policy" and recorded `denied` whatever its arguments, instead of getting an `Input validation error` that described the blocked tool's schema. If the audit sink has failed, a malformed call now gets "Scoped advisor audit channel failed" like every other call, instead of its validation error. The response to a rejected call from an allowed tool is unchanged.
+- **Gateway tool `inputSchema`s are now derived from the pydantic models that validate the arguments, so the two can no longer disagree (Consiliency/pmcp#236).** Constraints the models always enforced are now advertised and enforced at the transport gate — `minLength` on identifiers, `submit_feedback.title` 8–160 chars, bounds on `tasks_result.options` — so those rejections now come back as an `isError` tool result reading `Input validation error: …` instead of an `{"error": true}` payload. `gateway.invoke` now advertises `task`, `trace_context` and `_meta`; `gateway.tasks_*` advertise `requestor_context`; `tasks_result.options` gains `timeout_ms`. Optional arguments are advertised as `type: [X, "null"]` and the transport gate now accepts an explicit `null` for them, as the handlers always did; 28 optional arguments (e.g. `catalog_search.query`, `invoke.options`, `auth_connect.credential`) were previously rejected at the gate when sent as `null`. The gate does not apply pydantic's lax coercion: values such as `1` for a boolean or `"5"` for an integer on the newly advertised `invoke.task` fields (`enabled`, `ttl`, `poll_interval`), which were previously accepted and coerced, are now rejected with `Input validation error: 1 is not of type 'boolean'`. `invoke.task.ttl` now advertises its range on both sides, so `1e20`, `-1e20` and `float(±2**63)` are rejected at the gate, and so is any integer outside [−2^63+1, 2^63−1] (including `-2**63` itself), which the handler previously accepted. `invoke.evidence_label_digest` now also advertises its exact length (64), so a digest with a trailing newline is rejected at the gate instead of by the handler. Inputs the gate now rejects that previously reached the handler were recorded in the scoped-advisor audit as `failure`; they are now recorded as `audit.rejection` events with `terminal_status: "invalid_arguments"` (see the Consiliency/pmcp#296 entry below). Unknown keys are still ignored in this release — see the following entry once B lands. Argument descriptions agents already saw are unchanged, except `gateway.update_server.force`, which now describes the task-aware behaviour; 19 previously undescribed arguments gain a description.
 - **Exact-version validation follows npm's classification of package specs.** `is_valid_package_version` now refuses a version ending in `.tgz`, `.tar` or `.tar.gz` (any case), matching npm-package-arg's `isFileType` rule, which npm applies before reading a selector as a registry version; and a version whose major, minor or patch exceeds 2^53 - 1 (JavaScript's `Number.MAX_SAFE_INTEGER`), which node-semver refuses and npm-package-arg then reads as a dist-tag. It uses npm 10's pattern (npm-package-arg 12.x, whose `.` before `gz` is unescaped), a superset of npm 11's, since pmcp runs whichever `npx` is on PATH. The provision gate, package approvals, the CLI and the `gateway.provision` handler inherit it. **Upgrade note:** a package approval recorded earlier at either kind of version now approves nothing; it is ignored with a warning naming it (the rest of the store keeps working) and dropped on the next write to the store; re-approving the package at a registry version is that write. (`pmcp trust revoke-package <name>` also clears it, but a bare name revokes that package's valid approvals too.) A record with any other defect still fails the store closed.
 - **A downstream MCP server can no longer hang a caller by sending a request, being cancelled, or timing out — the remaining "the server hangs" runtime gaps are closed.** A server→client JSON-RPC request (a frame carrying both `method` and `id`) is now answered rather than dropped: `ping` gets an empty result and any other method a `-32601` "Method not found" refusal (the gateway advertises no client capabilities, so it does not forward an untrusted server's request to the agent), and classifying by `method` first also stops a downstream request whose id collides with one of ours from being misrouted as our response. `_send_request` no longer leaks a `pending_requests` entry when the caller is cancelled or the write itself raises — the entry is popped in a `finally` and a mid-write error still propagates. Cancellation is now propagated downstream as `notifications/cancelled` on `gateway.cancel`, on idle/ceiling timeout, and on caller cancellation (never for `initialize`, per spec; exactly once per cancellation). Replies and cancellation notifications go through a bounded per-server outbound queue drained by a single writer task whose lifecycle is torn down with the connection, so a downstream that stalls its own sink cannot make the gateway allocate unbounded tasks or buffer unbounded frames (review findings C-01, C-02, C-04). See [#232](https://github.com/Consiliency/pmcp/issues/232).
 - **A non-ASCII `Authorization` header no longer turns any request into a 500.** `hmac.compare_digest` raises `TypeError` on `str` containing non-ASCII, so an unauthenticated caller could crash any request with one header byte; the shared-secret comparison now happens on bytes and a bad header is simply unauthorized (review finding S-09). See [#231](https://github.com/Consiliency/pmcp/issues/231).
````

### Patch — `README.md`

````diff
diff --git a/README.md b/README.md
index 550464c..624e2ca 100644
--- a/README.md
+++ b/README.md
@@ -1517,7 +1517,10 @@ supply `run_correlation_id`, `seat_correlation_id`, and a SHA-256
 `evidence_label_digest` together. The append-only audit stores correlations,
 tool/status/policy/result digests, and a hashed public-source reference—not raw
 URLs, queries, arguments, credentials, or result bodies—and ends with one
-fsynced completeness marker.
+fsynced completeness marker. A call whose arguments fail the tool's input schema
+is recorded as a separate `audit.rejection` event (`terminal_status:
+"invalid_arguments"`) carrying only the tool, the failing JSON path
+(caller-chosen keys shown as `null`) and the failing schema keyword.
 
 Consumers can fail closed on older installations with:
 
````

## Measurement scripts

### `probe_gate_audit.py`

````python
"""Probe: which tools/call rejections reach the scoped audit (Consiliency/pmcp#296)."""
import asyncio, json, sys, tempfile
from pathlib import Path
from unittest.mock import MagicMock
from mcp.server.connection import Connection
from mcp.server.context import ServerRequestContext
from mcp.server.session import ServerSession
from mcp.types import CallToolRequestParams
import pmcp.server as S
from pmcp.server import GatewayServer

print("pmcp.server from", S.__file__)

def ctx():
    c = Connection.from_envelope("2025-11-25", None, None)
    return ServerRequestContext(session=ServerSession(MagicMock(), c), lifespan_context={}, protocol_version="2025-11-25", method="test")

def policy(p: Path) -> Path:
    p.write_text(json.dumps({"servers": {"allowlist": ["firecrawl", "brightdata"]},
        "gateway_tools": {"allowlist": ["gateway.health", "gateway.catalog_search", "gateway.describe", "gateway.invoke"]},
        "tools": {"allowlist": ["firecrawl::*search*", "firecrawl::*scrape*", "brightdata::*search*", "brightdata::*scrape*"]},
        "resources": {"denylist": ["*"]}, "prompts": {"denylist": ["*"]}}))
    return p

CORR = {"run_correlation_id": "run-1", "seat_correlation_id": "seat-1", "evidence_label_digest": "a" * 64}
CASES = [
    ("allowed, schema-malformed", "gateway.describe", {"tool_id": ""}),
    ("allowed, type error echoing a secret", "gateway.invoke", {"tool_id": "firecrawl::web_search", "arguments": "sk-SECRET-1", **CORR}),
    ("allowed, pattern error echoing a secret", "gateway.invoke", {"tool_id": "firecrawl::web_search", "arguments": {}, **CORR, "evidence_label_digest": "sk-SECRET-2"}),
    ("blocked, schema-malformed", "gateway.provision", {"server_name": 12345}),
    ("blocked, model-only-malformed on pre-A", "gateway.provision", {"server_name": ""}),
    ("blocked, well-formed", "gateway.provision", {"server_name": "x"}),
    ("unregistered name", "gateway.nope", {"a": 1}),
]

async def main():
    tmp = Path(tempfile.mkdtemp())
    audit = tmp / "audit.jsonl"
    srv = GatewayServer(policy_path=policy(tmp / "p.json"), audit_jsonl=audit)
    for label, name, args in CASES:
        before = len(audit.read_text().splitlines())
        r = await srv._handle_call_tool(ctx(), CallToolRequestParams(name=name, arguments=args))
        after = audit.read_text().splitlines()[before:]
        recs = [json.loads(l) for l in after]
        print(f"{label:40s} {name:18s} is_error={bool(r.is_error)!s:5s} text={r.content[0].text[:60]!r}")
        print(f"{'':40s} audit={[x['event'] + ':' + x['terminal_status'] for x in recs]} extra={[{k: x.get(k) for k in x if k.startswith('rejected')} for x in recs]}")
    await srv.shutdown()
    raw = audit.read_text()
    print("SECRET in audit:", any(s in raw for s in ("sk-SECRET-1", "sk-SECRET-2")))
    # dead sink
    srv2 = GatewayServer(policy_path=policy(tmp / "p2.json"), audit_jsonl=tmp / "a2.jsonl")
    srv2._scoped_advisor_audit._failed = True
    r = await srv2._handle_call_tool(ctx(), CallToolRequestParams(name="gateway.describe", arguments={"tool_id": ""}))
    print("dead sink, malformed describe:", bool(r.is_error), r.content[0].text[:70])

asyncio.run(main())
````

### `survey.py`

````python
import json
from pmcp.tools.handlers import get_gateway_tool_definitions
def walk(s, path, out):
    if isinstance(s, dict):
        for k in ("additionalProperties","patternProperties","propertyNames","$ref","$defs","items","prefixItems","unevaluatedProperties","dependentSchemas","if","contains"):
            if k in s: out.append((".".join(path), k, json.dumps(s[k])[:60]))
        for k,v in s.items(): walk(v, path+[k], out)
    elif isinstance(s, list):
        for i,v in enumerate(s): walk(v, path+[str(i)], out)
tools = get_gateway_tool_definitions()
print(len(tools))
for t in tools:
    out=[]; walk(t.input_schema, [], out)
    for o in out: print(t.name, *o)
````

### `mutants.py`

````python
"""Mutation run for Consiliency/pmcp#296. Each mutant is one exact-string
replacement; the file is restored from a saved copy after every run."""
import shutil, subprocess, sys
from pathlib import Path

SERVER = Path("src/pmcp/server.py")
AUDIT = Path("src/pmcp/scoped_advisor_audit.py")
MUTANTS = [
    ("M1 policy not judged before the gate", SERVER,
     "        if tool is not None and allowed:\n", "        if tool is not None:\n"),
    ("M2 call_tool re-reads the policy verdict", SERVER,
     "                if not allowed:\n",
     "                if not self._policy_manager.is_gateway_tool_allowed(name):\n"),
    ("M3 relative .path instead of .absolute_path", AUDIT,
     "    for segment in error.absolute_path:\n", "    for segment in error.path:\n"),
    ("M4 caller keys not checked against declared names", AUDIT,
     "type(segment) is str and segment in declared", "type(segment) is str"),
    ("M4b str subclass accepted by isinstance", AUDIT,
     "type(segment) is str and segment in declared", "isinstance(segment, str) and segment in declared"),
    ("M5 int segment kept without a list container", AUDIT,
     "if isinstance(node, list) and type(segment) is int:", "if type(segment) is int:"),
    ("M6 validator keyword not bounded", AUDIT,
     "    return validator if isinstance(validator, str) and validator in keywords else None\n",
     "    return validator\n"),
    ("M7 gate rejection not recorded", SERVER,
     "                    if self._scoped_advisor_audit is not None:\n",
     "                    if False:\n"),
    ("M8 dead sink not caught at the gate", SERVER,
     "                except ScopedAdvisorAuditError:\n                    logger.error(\"Scoped advisor audit channel failed\")\n                    return CallToolResult(\n",
     "                except KeyError:\n                    logger.error(\"Scoped advisor audit channel failed\")\n                    return CallToolResult(\n"),
    ("M9 correlation copied from unvalidated arguments", AUDIT,
     "                \"event\": REJECTION_EVENT,\n",
     "                \"event\": REJECTION_EVENT,\n                \"run_correlation_id\": arguments.get(\"run_correlation_id\"),\n"),
    ("M10 jsonschema message recorded", AUDIT,
     "                \"rejected_argument_validator\": validator,\n",
     "                \"rejected_argument_validator\": validator,\n                \"rejected_argument_message\": error.message,\n"),
    ("M11 terminal_status failure", AUDIT,
     "INVALID_ARGUMENTS_STATUS = \"invalid_arguments\"\n", "INVALID_ARGUMENTS_STATUS = \"failure\"\n"),
    ("M13 rejection written as an audit.invocation", AUDIT,
     "REJECTION_EVENT = \"audit.rejection\"\n", "REJECTION_EVENT = \"audit.invocation\"\n"),
    ("M12 declared names from the top level only", AUDIT,
     "            for child in node.values():\n                collect(child)\n        elif isinstance(node, list):\n            for child in node:\n                collect(child)\n\n    collect(schema)\n",
     "        elif isinstance(node, list):\n            pass\n\n    collect(schema)\n"),
]
scratch = Path(sys.argv[1])
saved = {p: scratch / (p.name + ".orig") for p in (SERVER, AUDIT)}
for p, s in saved.items():
    shutil.copyfile(p, s)
killed = 0
try:
    for label, path, old, new in MUTANTS:
        text = saved[path].read_text()
        assert text.count(old) == 1, f"{label}: anchor found {text.count(old)} times"
        path.write_text(text.replace(old, new))
        r = subprocess.run(["uv", "run", "pytest", "tests/test_scoped_advisor_audit.py",
                            "-p", "no:cacheprovider", "--cov-fail-under=0", "-q"],
                           capture_output=True, text=True)
        failed = sorted({l.split(" ")[1].split("::")[1].split("[")[0] for l in r.stdout.splitlines() if l.startswith("FAILED ")})
        tail = [l for l in r.stdout.splitlines() if " passed" in l or " failed" in l][-1:]
        status = "KILLED" if r.returncode != 0 else "SURVIVED"
        killed += r.returncode != 0
        print(f"{label}: {status} | {tail[0] if tail else r.stdout[-200:]} | {', '.join(failed)}")
        shutil.copyfile(saved[path], path)
finally:
    for p, s in saved.items():
        shutil.copyfile(s, p)
print(f"{killed}/{len(MUTANTS)} killed")
````
