# Detailed plan: task `ttl` and `poll_interval` stay in seconds in pmcp, and are converted to and from MCP's milliseconds at one choke point per direction

> Written on main `2adcd9a` (dev0, a team host), worktree `pmcp-330`, branch
> `plan/330-task-units`. Every number below was measured on that tree, or on
> the spike of this plan applied to it. The spike was then removed, and this PR
> carries only this file. See Consiliency/pmcp#330.

## Task

Consiliency/pmcp#330. MCP 2025-11-25 defines `TaskMetadata.ttl`, `Task.ttl` and
`Task.pollInterval` in **milliseconds**. pmcp documents `gateway.invoke`'s
`task.ttl` and `task.poll_interval` in **seconds**, and forwards the number
unchanged. A caller asking for `ttl: 300` (five minutes, per pmcp's docs)
therefore gets 300 ms from a spec-conforming downstream. Reported values are
misread the same way.

**Owner decision (2026-10-04), not reopened here.** pmcp's own interface keeps
seconds, as documented, and pmcp converts at the boundary in both directions:

- outbound: a caller's `task.ttl` in seconds becomes the downstream `ttl` in ms;
- inbound: a downstream task's `ttl` and `pollInterval` in ms become seconds in
  every task pmcp returns or records. That covers `gateway.tasks_*`,
  `gateway.invoke`'s task output, and the records.

The Consiliency/pmcp#298 bounds (PR #337, on main; plan
`.consiliency/plans/detailed-298-task-bounds-20261003-0122.md`) are restated in
the right unit and applied on the right side of the conversion. The behaviour
change goes in the CHANGELOG: anyone who worked around the bug by sending ms
now gets values 1000× longer. README and `specs/tenant-code-mode-host-contract.md`
are updated.

## Research summary

### What the spec says (read from the installed SDK, not from memory)

`.venv/lib/python3.10/site-packages/mcp_types/_v2025_11_25/__init__.py`
(mcp 2.0.0) contains the following:

| Type | Field | Spec text |
|---|---|---|
| `TaskMetadata` (request `params.task`) | `ttl: int \| None` | "Requested duration in milliseconds to retain task from creation." |
| `Task` | `ttl: int \| None` | "Actual retention duration from creation in milliseconds, null for unlimited." |
| `Task` | `pollInterval: int \| None` | "Suggested polling interval in milliseconds." |

`TaskMetadata` has **no `pollInterval`**. pmcp nonetheless sends one
(`_task_wire_metadata`). See Design decision 6.

### How the boundary sites were derived

The sites come from the code, not from the issue's examples:

1. `grep -rniE "ttl|poll_?interval|pollInterval|keep_?alive|keepAlive"` over
   every tracked file outside `tests/` and `.consiliency/`.
2. An AST sweep over `src/pmcp`. It covers every string constant
   `ttl`/`pollInterval`/`poll_interval`/`keepAlive`/`keep_alive`/`ttlMs`/`pollIntervalMs`,
   every attribute, keyword, field or assignment whose name matches
   `ttl|poll|keep_?alive`, and the function each one sits in. The script is
   `derive_sites.py` below.
3. Every construction of a task model:
   `grep -rn "McpTaskInfo\|McpTaskRecord\|_task_info_from_payload\|_task_wire_metadata\|_record_task\|TaskMetadataInput"`.
   A model built from another model's dump never names the field, so the AST
   sweep cannot see it.

```python
"""Every read/write of a task duration in src/pmcp, from the AST (Consiliency/pmcp#330)."""
import ast, pathlib, re
SRC = pathlib.Path("src/pmcp")
KEYS = {"ttl", "pollInterval", "poll_interval", "keepAlive", "keep_alive", "pollIntervalMs", "ttlMs", "ttl_ms"}
NAME = re.compile(r"ttl|poll|keep_?alive", re.I)
for path in sorted(SRC.rglob("*.py")):
    tree = ast.parse(path.read_text())
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    def where(n):
        while n in parents:
            n = parents[n]
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                return n.name
        return "<module>"
    for node in ast.walk(tree):
        kind = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in KEYS:
            kind = f"str {node.value!r}"
        elif isinstance(node, ast.Attribute) and NAME.search(node.attr):
            kind = f"attr .{node.attr}"
        elif isinstance(node, ast.keyword) and node.arg and NAME.search(node.arg):
            kind = f"kw {node.arg}="
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and NAME.search(node.target.id):
            kind = f"field {node.target.id}"
        elif isinstance(node, ast.Name) and NAME.search(node.id) and isinstance(node.ctx, ast.Store):
            kind = f"name {node.id}"
        if kind:
            print(f"{path.relative_to(SRC)}:{getattr(node,'lineno','?')}\t{where(node)}\t{kind}")
```

Its output on main `2adcd9a` (`uv run python derive_sites.py | sort -u`), with
the task rows kept and the rest classified:

```
client/manager.py:1678-1681  _task_wire_metadata      attr .ttl/.poll_interval; str 'ttl', 'pollInterval'
client/manager.py:1751-1764  _task_info_from_payload  str 'ttl', 'pollInterval', 'poll_interval'; kw ttl=, poll_interval=
client/manager.py:1802-1803  _record_task             attr .ttl/.poll_interval; kw ttl=, poll_interval=
types.py:607-608             <module>                 str 'ttl', 'poll_interval'  (the #298 check table)
types.py:634-635             McpTaskInfo              field ttl, poll_interval
types.py:690, 702            TaskMetadataInput        field ttl, poll_interval
--- not task durations ---
auth.py:852, 950             AsyncJWKS._ttl_seconds   JWKS cache TTL, pmcp-internal seconds
manifest/registry.py:23      REGISTRY_CACHE_TTL_SECONDS  registry cache, pmcp-internal
client/manager.py:383        IDLE_POLL_SLICE_S        idle-timeout slice, pmcp-internal
manifest/installer.py:330    poll_timeout             install monitor, pmcp-internal
manifest/npm_resolver.py:401, 562  .poll              Popen.poll()
```

The non-code hits (grep) are:
- `README.md:1502`, `specs/tenant-code-mode-host-contract.md:89, 106, 122-123`
  and the CHANGELOG #298 entry. These are the docs this plan edits.
- `SECURITY.md:12, 61, 192`, which describe the HTTP session keep-alive. That
  is not a task field.
- `diagnostics/issue-79-1b/*`, which is the idle-timeout repro and has no task
  `ttl`.
- historical `plans/` and `specs/phase-plans-*`, which are not edited.

There is no `notifications/tasks/status` handler (`grep -rn "tasks/status"
src/` is empty), and pmcp never acts as a task *server* upstream. So no other
direction exists.

### The derived boundary-site table

"Boundary" means a value crossing between pmcp's seconds and the wire's
milliseconds. The rows marked *re-validation* do not cross the boundary. They
re-validate a model that already holds seconds, so they must **not** convert
again. That constraint shapes Design decision 3.

| # | Direction | Site (main `2adcd9a`) | Wire key ↔ pmcp field | Reached from | After this plan |
|---|---|---|---|---|---|
| O1 | pmcp → downstream | `manager.py:1679` `_task_wire_metadata` | `task.ttl` → `params.task.ttl` | `call_tool` (`:3974`), the only caller | `task_seconds_to_wire(parsed.ttl)`: s × 1000, int |
| O2 | pmcp → downstream | `manager.py:1681` `_task_wire_metadata` | `task.poll_interval` → `params.task.pollInterval` | same | `task_seconds_to_wire(parsed.poll_interval)`: s × 1000 |
| O3 | pmcp → downstream | `manager.py` `_task_request_params` | sends only `taskId`, `cursor`, `requestorContext` | `tasks/get`, `/result`, `/list`, `/cancel` | no duration, nothing to convert; pinned by the structural test |
| I1 | downstream → pmcp | `manager.py:1763` `_task_info_from_payload` | `ttl` → `McpTaskInfo.ttl` | `tools/call` reply (`:3988`), `tasks/list` (`:4034`), `tasks/get` (`:4062`), `tasks/result` (`:4088`), `tasks/cancel` (`:4123`) | `task_duration_from_wire("ttl", …)`: checked in ms, then ÷ 1000 |
| I2 | downstream → pmcp | `manager.py:1751-1764` `_task_info_from_payload` (`pick` over `pollInterval`, `poll_interval`) | `pollInterval`/`poll_interval` → `McpTaskInfo.poll_interval` | same five | `task_duration_from_wire("poll_interval", …)` |
| I3 | downstream → pmcp | `manager.py:4125` `cancel_task` fallback `McpTaskInfo(task_id, status, updated_at, raw)` | none: no duration read | `tasks/cancel` with no task in the reply | unchanged; a duration read here fails the structural test (mutant M13) |
| R1 | *re-validation* | `manager.py:1802-1803` `_record_task` → `McpTaskRecord(ttl=task_info.ttl, …)` | seconds → seconds | every I-path | unchanged; the model's check is now in seconds |
| R2 | *re-validation* | `handlers.py:6025` `tasks_list`: `McpTaskInfo(**task)` from `record.model_dump()` | seconds → seconds | `gateway.tasks_list` | unchanged |
| R3 | *re-validation* | `handlers.py:6169-6176` `_sanitize_task_for_output`: JSON dump, then `McpTaskInfo.model_validate` | seconds → seconds | `gateway.invoke`, `tasks_list`, `tasks_get`, `tasks_result` | unchanged |
| R4 | *re-validation* | `McpTaskInfo._drop_unusable_hints` (`types.py:641`) | runs on every construction above | all | ttl check in seconds (`_usable_task_ttl`); poll check unit-free |
| P1 | passthrough | `McpTaskInfo.raw` (`manager.py:1765`) | the downstream payload as sent | every task output | **verbatim, in ms**: Design decision 7 |
| P2 | passthrough | `gateway.tasks_result`'s `result` (`handlers.py` `result.get("result", result)`), and `gateway.invoke`'s `result` for a non-task call | as sent | — | verbatim: Design decision 7 |

Each direction has one choke point: **O1/O2 → `_task_wire_metadata`** and
**I1/I2 → `_task_info_from_payload`**. Both are already the only functions in
`src/pmcp` that name a wire key for a task duration (the AST table above). The
conversion therefore goes into two helpers in `types.py`:
`task_seconds_to_wire` and `task_duration_from_wire`. Each is called from
exactly one of those two functions.

### The repro, measured

`repro_330.py` (below) uses a fake downstream that honours the spec. It keeps a
task for `params.task.ttl` **milliseconds** from creation and answers
`tasks/get` with an error once that time has passed. Its clock is advanced by
hand, so the result is deterministic. The fake goes through `GatewayTools`
(`gateway.invoke`, then `gateway.tasks_get`) over a real `ClientManager`.

```python
"""Consiliency/pmcp#330 repro: `gateway.invoke` with `task: {ttl: 300}` against
a downstream that honours MCP 2025-11-25 (`ttl` in milliseconds). Prints the
ttl the downstream received and when the task stops being retrievable."""

import asyncio
from typing import Any
from unittest.mock import MagicMock

from pmcp.client.manager import ClientManager, ManagedClient
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from pmcp.types import (
    RemoteMcpServerConfig, ResolvedServerConfig, RiskHint, ServerStatus,
    ServerStatusEnum, ToolInfo,
)

CLOCK = [0.0]  # the downstream's clock, in seconds; advanced by hand


class SpecDownstream:
    def __init__(self) -> None:
        self.tasks: dict[str, dict[str, Any]] = {}
        self.sent_ttl: Any = None

    async def __call__(self, managed: Any, method: str, params: dict[str, Any], **_: Any) -> Any:
        if method == "tools/call":
            self.sent_ttl = params["task"]["ttl"]
            self.tasks["t1"] = {"created": CLOCK[0], "ttl_ms": self.sent_ttl}
            return {"task": {"taskId": "t1", "status": "working",
                             "ttl": self.sent_ttl, "pollInterval": 1000}}
        task = self.tasks.get(params["taskId"])
        if task is None or CLOCK[0] - task["created"] >= task["ttl_ms"] / 1000:
            raise RuntimeError("Task not found")  # expired: the spec lets it be deleted
        return {"task": {"taskId": "t1", "status": "working",
                         "ttl": task["ttl_ms"], "pollInterval": 1000}}


async def main() -> None:
    manager = ClientManager()
    manager._tools["spec::run"] = ToolInfo(
        tool_id="spec::run", server_name="spec", tool_name="run", description="d",
        short_description="d", input_schema={"type": "object"}, tags=[],
        risk_hint=RiskHint.LOW, execution={"taskSupport": "optional"})
    status = ServerStatus(name="spec", status=ServerStatusEnum.ONLINE, tool_count=1,
                          server_capabilities={"tasks": {}})
    manager._servers["spec"] = status
    manager._clients["spec"] = ManagedClient(
        config=ResolvedServerConfig(name="spec", source="custom", config=RemoteMcpServerConfig(
            type="streamable-http", url="https://spec.example/mcp")),
        is_remote=True, write_stream=MagicMock(), status=status)
    downstream = SpecDownstream()
    manager._send_request = downstream  # type: ignore[method-assign]
    gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())

    invoked = await gateway.invoke({"tool_id": "spec::run", "task": {"ttl": 300}})
    print(f"caller task.ttl=300 (seconds, per pmcp docs) -> downstream received ttl={downstream.sent_ttl}")
    print(f"gateway.invoke reports task.ttl={invoked.task.ttl!r} poll_interval={invoked.task.poll_interval!r}")
    for t in (0.299, 0.300, 0.5, 299.999, 300.0):
        CLOCK[0] = t
        got = await gateway.tasks_get({"server_name": "spec", "task_id": "t1"})
        print(f"  t={t:>8.3f}s  tasks_get ok={got.ok}")


asyncio.run(main())
```

**On main `2adcd9a`** (a clean worktree, `uv run python repro_330.py`), the
task expires after **300 ms**, and pmcp reports the downstream's `pollInterval`
of 1000 ms as 1000 seconds:

```
caller task.ttl=300 (seconds, per pmcp docs) -> downstream received ttl=300
gateway.invoke reports task.ttl=300 poll_interval=1000.0
  t=   0.299s  tasks_get ok=True
  t=   0.300s  tasks_get ok=False
  t=   0.500s  tasks_get ok=False
  t= 299.999s  tasks_get ok=False
  t= 300.000s  tasks_get ok=False
```

**With this plan's patch**, the task expires after **300 s**, and both values
come back in seconds:

```
caller task.ttl=300 (seconds, per pmcp docs) -> downstream received ttl=300000
gateway.invoke reports task.ttl=300.0 poll_interval=1.0
  t=   0.299s  tasks_get ok=True
  t=   0.300s  tasks_get ok=True
  t=   0.500s  tasks_get ok=True
  t= 299.999s  tasks_get ok=True
  t= 300.000s  tasks_get ok=False
```

The same scenario is a test in the patch,
`test_a_ttl_of_300_lasts_300_seconds_on_a_spec_downstream`. It fails on main
and passes with the patch.

### The test surface this touches (measured on the spike, before migration)

With only the source change applied, 7 modules
(`test_task_numeric_bounds`, `test_gateway_tool_schemas`, `test_tools`,
`test_client_manager`, `test_phase6_tenant_code_mode`, `test_server`,
`test_phase4_e2e`) gave **13 failed, 1003 passed**. Every failure asserted the
old pass-through or the old ms-sized bound:

- `test_task_numeric_bounds.py`: 11 failures.
  - `test_task_hint_bounds`, 2: it accepted `2**53 − 1` seconds.
  - `test_a_usable_downstream_task_hint_is_kept`, 5: ms were kept as given.
  - `test_forwarded_task_hints_are_spec_shaped`, 1: it expected `ttl: 300`.
  - `test_every_aliased_hint_prefers_its_usable_alias`, 2: 2.5 was kept.
  - `test_advertised_schemas_match_snapshot`, 1: this failure is in
    `test_gateway_tool_schemas.py`, not in this module. The snapshot's
    `maximum` and descriptions changed.
- `test_client_manager.py::TestCallTool`, 2:
  `test_tenant_code_mode_call_forwards_task_and_trace_metadata` expected the
  outbound `ttl: 300`, and `test_task_proxy_methods_update_registry` fed
  ms-meaning values and asserted them back.

`test_tools.py` builds `McpTaskRecord`s directly through a fake manager, in
seconds, and does not change. `test_phase6_tenant_code_mode.py` asserts no
duration, but its fake tenant returned `ttl: 300, pollInterval: 0.1`. That
fixture is made spec-honest (`300000`, `100`) so the soak test models a real
tenant.

## Design decisions (made explicitly)

### 1. One choke point per direction, two helpers in `types.py`

- `task_seconds_to_wire(seconds)` returns `seconds * MS_PER_SECOND`. It is
  called only from `_task_wire_metadata`, for both O1 and O2.
- `task_duration_from_wire(name, value)` is called only from
  `_task_info_from_payload`, for I1 and I2. It does three things in order:
  1. `None` → `None`.
  2. It applies the wire check in ms, which is the #298 rule. A value that
     fails comes back as `UNUSABLE_TASK_VALUE`.
  3. Otherwise it returns `usable / MS_PER_SECOND`.
- `MS_PER_SECOND = 1000` is the only conversion constant.

The structural tests pin the layout. Only those two functions name a
duration wire key. Each converter has exactly one caller. Every `ttl=` or
`poll_interval=` keyword elsewhere copies a model attribute of the same name.
So a new reader or writer cannot bypass the conversion and still pass.

### 2. Inbound values are `float` seconds; `McpTaskInfo.ttl` becomes `float | None`

ms → s is `value / 1000`, a float. An int would turn 300 ms into `0`, which
reads as "zero retention", a different meaning. It would also turn 1500 ms into
1 s. The model field type changes from `int | None` to `float | None`. That is
part of the same behaviour change, and the CHANGELOG says so: a task that
showed `ttl: 300000` now shows `ttl: 300.0`.

Precision:
- ms → s is a correctly rounded division. It is exact to the millisecond for
  every `ttl` up to 2^53 ms. Above that, which the #298 wire rule still
  accepts up to 2^63 − 1 ms, the float carries 53 bits, like every float in
  pmcp's output.
- s → ms is exact for the integer `ttl` (`int * 1000`).
- For a float `poll_interval`, one multiply and one divide are each correctly
  rounded, so a round trip lands within 2 ulp. The property test asserts that
  bound rather than `==`. pmcp only reports `poll_interval` (#298 decision 7:
  no loop consumes it), so 2 ulp is immaterial.

### 3. The model's own check is in seconds; the wire check runs before conversion

`McpTaskInfo._drop_unusable_hints` runs on **every** construction (R1–R4). It
runs again on values that are already in seconds: a record, a list entry, a
sanitized output. #298's `_usable_task_ttl` demanded an integer number of ms.
Left in the model, it would mark `1.5` seconds unusable on the first
re-validation. Mutant M7 shows that this is what happens. So the check splits:

- `_usable_wire_task_ttl` is #298's rule, unchanged: a non-bool integer in
  [0, 2^63 − 1] ms, or a whole-number float there. It applies to the value as
  sent, through `_WIRE_TASK_HINT_CHECKS`. That table serves both
  `task_hint_is_usable` (the alias picker) and `task_duration_from_wire`.
- `_usable_task_ttl` is now the model check, in seconds: a non-bool, finite
  number in [0, (2^63 − 1) / 1000]. That accepts exactly the image of the wire
  rule.
- `_usable_poll_interval` (finite, > 0) has no unit and serves both sides.

`test_seconds_survive_every_revalidation_unchanged` drives a converted
fractional task through `_record_task`, then `McpTaskInfo(**record)`, then
`_sanitize_task_for_output`, and asserts that nothing changes.

### 4. Edge cases

| Case | Decision | Test |
|---|---|---|
| Caller `ttl` integer s → ms | exact (`int * 1000`), sent as an int | `test_ttl_round_trips_exactly` (2010 samples, including both ends) |
| Downstream ms → s, `ttl` | `/ 1000` → float; `1` → `0.001`, `1500` → `1.5`, `INT64_MAX` → `INT64_MAX / 1000` | `test_a_usable_downstream_duration_is_reported_in_seconds` |
| `ttl: null` sent | stays `None` = unlimited, **not** unusable (the converter's first branch) | `test_a_null_or_absent_ttl_stays_unlimited`, M9 |
| `ttl` absent | `None`, not unusable | same |
| `pollInterval: null` sent | unusable, as #298 decided (the parser passes `UNUSABLE_TASK_VALUE`, and the converter keeps it) | `test_an_unusable_downstream_duration_is_named_not_converted` |
| Caller bound | both fields ≤ `MAX_TASK_SECONDS` = ⌊(2^53 − 1) / 1000⌋ = **9,007,199,254,740 s**; lower bounds unchanged (`ttl ≥ 1`, `poll_interval > 0`) | `test_caller_bounds_are_in_seconds` (gate and model agree), M6 |
| Overflow after × 1000 | max `ttl` → 9,007,199,254,740,000 ms ≤ 2^53 − 1, an int; max `poll_interval` → 9.00719925474e15, exact; smallest `poll_interval` 5e−324 s → 4.94e−321 ms > 0, so it never underflows to 0 | `test_the_largest_accepted_value_does_not_overflow_on_the_wire` |
| Downstream bound | #298's rule on the **ms** value as sent: `ttl` an integer in [0, 2^63 − 1], `pollInterval` finite > 0. `ttl: 1.5` (fractional ms) stays unusable | `test_an_unusable_downstream_duration_is_named_not_converted`, M8 |
| Underflow inbound | `pollInterval: 5e-324` ms passes the ms check, but ÷ 1000 = `0.0` s. The seconds check (> 0) then names it unusable; it is not reported as 0 | same test |
| Non-numeric | caller: `"300"`, `true`, `[300]`, `{…}` are still refused at the gate, and `null` is "not given". Downstream: `"300000"`, `true`, NaN, ±Inf are still unusable. #330 changes neither | `test_a_non_numeric_caller_duration_is_still_refused`, the unusable table |

### 5. The bounds sit on the side whose unit they were written in

#298 bounded the caller's value by the I-JSON limit because it was forwarded
as is. Now the forwarded value is the caller's × 1000. The caller-side `le`
therefore falls to `MAX_TASK_SECONDS`, which the #298 plan's N6 already
pointed out. The advertised schema moves with it, from `maximum:
9007199254740991` to `9007199254740` for both fields. A value between the two
was accepted on main and is refused now. This is listed in the CHANGELOG.

The downstream rule stays in ms, applied before division. That keeps #298's
meaning: "an integer `ttl`, as MCP's schema types it".

### 6. Outbound `pollInterval` is converted too (judgement call, stated)

MCP's `TaskMetadata` has no `pollInterval`. pmcp has always sent one, under the
spec's millisecond field name, and a spec-conforming server ignores it
(`extra="ignore"` in the SDK model). The choices were:

- **convert it** (chosen). Any server that reads it gets the unit its name
  promises. This is consistent with "convert at the boundary in both
  directions", and it is one call at the same choke point.
- stop sending it. That removes a field tenant servers may already read; the
  tenant contract lists it. It is a separate compatibility change, outside
  #330.
- send it unconverted. A field named for ms would carry seconds. Rejected.

It is sent as `seconds * 1000`, a float, not rounded to an int. Rounding would
need a rule for sub-millisecond intervals, and mutant M16 shows that the
round-trip test pins this choice.

### 7. `raw` and relayed results keep the downstream's units

The owner's decision names the task fields `ttl` and `poll_interval`. `raw` is,
by name and by the #298 contract text, "what the downstream sent". Converting
inside it would make it neither raw nor consistent, since its key is still
`pollInterval` and the spec reads that in ms. Likewise,
`gateway.tasks_result`'s `result`, and `gateway.invoke`'s `result` for a call
that created no task, are relayed as sent. The contract and the CHANGELOG
state this in one sentence each.

### 8. `ttl` and `poll_interval` field descriptions say where the ms happen

The agent-facing descriptions become "Requested task TTL in seconds (sent to
the downstream server in milliseconds)" and "Seconds between task status polls
(sent to the downstream server in milliseconds)". The snapshot
`tests/fixtures/gateway_tool_schemas.json` is regenerated. Its diff is exactly
4 lines: 2 descriptions and 2 maxima.

### 9. Overlap with Consiliency/pmcp#347 (plan for Consiliency/pmcp#338, the task cap)

#347's plan (`detailed-338-task-cap-20261004-0451.md`, read at
`origin/plan/338-task-cap`) touches the same files. In each, whichever PR lands
second rebases:

| File | #330 hunks | #338 hunks | Conflict risk |
|---|---|---|---|
| `src/pmcp/client/manager.py` | the import block (`task_duration_from_wire`, `task_seconds_to_wire`); `_task_wire_metadata` (1678-1681); the `McpTaskInfo(...)` return of `_task_info_from_payload` (1757-1765) | the import block; hunks from 1771 on (`_record_task` and later), the task proxies at 3992+ | adjacent at 1765/1771 and in the import list: textual, resolve by keeping both |
| `src/pmcp/types.py` | the constants after `MAX_FORWARDED_TASK_NUMBER`; the check functions; `McpTaskInfo.ttl` type; `TaskMetadataInput` bounds | `McpTaskRecord._connection_id` (PrivateAttr) | none expected; different classes |
| `CHANGELOG.md` | the #298 bounds lines; the "Known follow-up" bullet, replaced by the #330 entry | the same "Known follow-up" context, plus a new #338 entry after it | **textual conflict**: keep #298's rewritten bullet, then the #330 entry, then the #338 entry |
| `tests/test_task_numeric_bounds.py` | imports; the `test_task_hint_bounds` rows; `USABLE`; `test_forwarded_task_hints_are_spec_shaped`; the alias test | eviction and recording tests (483+, 508+, 539+, 671+, 891+, 924+, 941+, 977+) | low; the alias-test hunk is near #338's 891 hunk |

This plan's patch is kept minimal so that the rebase stays mechanical. It
adds no new class, does not move `_record_task`, and makes no eviction
change.

## Changes

| File | Change | Size |
|---|---|---|
| `src/pmcp/types.py` | Adds `MS_PER_SECOND` and `MAX_TASK_SECONDS`. `_usable_task_ttl` is renamed to `_usable_wire_task_ttl`. A new seconds-unit `_usable_task_ttl` is the model check. Adds `_WIRE_TASK_HINT_CHECKS`. `task_hint_is_usable` uses the wire table. Adds `task_seconds_to_wire` and `task_duration_from_wire`. `McpTaskInfo.ttl` becomes `float \| None`. `TaskMetadataInput.ttl`/`poll_interval` get `le=MAX_TASK_SECONDS` and the new descriptions | +80 / −12 |
| `src/pmcp/client/manager.py` | 2 imports; O1/O2 through `task_seconds_to_wire`; I1/I2 through `task_duration_from_wire` | +10 / −4 |
| `tests/fixtures/gateway_tool_schemas.json` | regenerated: 2 maxima, 2 descriptions | +4 / −4 |
| `tests/test_task_units.py` | **new**; 54 cases | +550 |
| `tests/test_task_numeric_bounds.py` | migrated to the new unit: bounds rows, `USABLE` rows, the outbound shape, the alias rows | +15 / −11 |
| `tests/test_client_manager.py` | 2 tests migrated: the fake downstream sends ms; the outbound assertion expects ms | +8 / −8 |
| `tests/test_phase6_tenant_code_mode.py` | the fake tenant returns spec ms (`300000`, `100`) | +2 / −2 |
| `README.md` | the units sentence in the tenant-runs paragraph | +7 / −1 |
| `specs/tenant-code-mode-host-contract.md` | a "Units" paragraph in the task lifecycle section; the `ttl`/`poll_interval` bullets in the metadata section | +24 / −2 |
| `CHANGELOG.md` | the #298 bounds restated; its "Known follow-up" replaced by a pointer; a new #330 entry under `[Unreleased]` → `### Changed` | +43 / −8 |

`McpTaskInfo`'s output schema is not snapshotted (`grep -c "unusable_fields\|McpTaskInfo" tests/fixtures/*.json` → 0), so the `ttl` type change moves no fixture.

## Tests

`tests/test_task_units.py` (new, 54 cases). Every case maps to a derived site
or a decision:

| Test | Covers | Cases |
|---|---|---|
| `test_a_ttl_of_300_lasts_300_seconds_on_a_spec_downstream` | the repro: O1 + I1 end to end through `gateway.invoke`/`gateway.tasks_get` | 1 |
| `test_outbound_ttl_and_poll_interval_are_sent_in_milliseconds` | O1, O2 through `gateway.invoke` | 1 |
| `test_every_inbound_path_reports_seconds[pollInterval, poll_interval]` | I1, I2 on all five reply paths (`tools/call`, `tasks/list`, `tasks/get`, `tasks/result`, `tasks/cancel`). Each checks the gateway output (R3) **and** the record (R1); `raw` stays in ms (P1); both wire aliases | 2 |
| `test_ttl_round_trips_exactly` | the property: integer s → ms → s is the identity; 2010 seeded samples over [1, `MAX_TASK_SECONDS`] | 1 |
| `test_poll_interval_round_trips_to_within_rounding` | the property: float s → ms → s within 2 ulp; 2009 log-uniform samples from 5e−324 to `MAX_TASK_SECONDS` | 1 |
| `test_caller_bounds_are_in_seconds` | bounds in the new unit; gate (`GATE_VALIDATOR` on the advertised schema) and model agree | 10 |
| `test_the_largest_accepted_value_does_not_overflow_on_the_wire` | overflow after × 1000; no underflow to 0 ms | 1 |
| `test_a_non_numeric_caller_duration_is_still_refused` | non-numeric stays refused; `null` stays "not given" | 5 |
| `test_a_usable_downstream_duration_is_reported_in_seconds` | ms → s values and type `float` | 8 |
| `test_an_unusable_downstream_duration_is_named_not_converted` | the #298 wire rule in ms; the underflow-to-0 case | 11 |
| `test_a_null_or_absent_ttl_stays_unlimited` | null/absent `ttl` | 1 |
| `test_the_model_checks_seconds` | R4: the model's check is in seconds, both bounds | 6 |
| `test_seconds_survive_every_revalidation_unchanged` | R1 → R2 → R3 idempotence | 1 |
| `test_only_the_two_choke_points_name_a_task_duration_wire_key` | **structural**: every function in `src/pmcp` naming `ttl`/`pollInterval`/`poll_interval` is `_task_wire_metadata` or `_task_info_from_payload` (pins O3 and I3) | 1 |
| `test_the_outbound_choke_point_converts_every_duration_it_writes` | **structural**: each `payload["ttl"/"pollInterval"] = …` is a `task_seconds_to_wire(...)` call | 1 |
| `test_the_inbound_choke_point_converts_every_duration_it_reads` | **structural**: the `ttl=`/`poll_interval=` of the `McpTaskInfo(...)` call are `task_duration_from_wire(...)` calls | 1 |
| `test_every_other_duration_assignment_copies_seconds_from_a_model` | **structural**: every other `ttl=`/`poll_interval=` keyword in `src/pmcp` is a same-named attribute copy (R1) | 1 |
| `test_each_converter_has_exactly_one_caller` | **structural**: one call site per direction | 1 |

The migrations in the three existing modules are listed under *Changes* and
appear in the patch.

## Verification

Run from a fresh worktree of `origin/main` on dev0 (a team host):

```bash
git -C ~/code/pmcp fetch origin
git -C ~/code/pmcp worktree add -b fix/330-task-units "$WORKTREE_ROOT/pmcp-330-fix" origin/main
cd "$WORKTREE_ROOT/pmcp-330-fix"
uv sync --all-extras -p 3.10      # without --all-extras, `uv run` silently uses the system pytest
mkdir -p /var/tmp/pmcp-330-bt-$USER
BT=/var/tmp/pmcp-330-bt-$USER
```

Apply *Verbatim bodies* (one `git apply`). Then:

```bash
# 1. the new module (spike: 54 passed)
uv run pytest tests/test_task_units.py --cov-fail-under=0 -p no:cacheprovider --basetemp=$BT/u -q
# 2. the suites the change touches (spike: 1072 passed, 0 failed)
env -u npm_config_cache -u npm_config_store_dir uv run pytest tests/test_task_units.py \
  tests/test_task_numeric_bounds.py tests/test_gateway_tool_schemas.py tests/test_tools.py \
  tests/test_client_manager.py tests/test_phase6_tenant_code_mode.py tests/test_server.py \
  tests/test_phase4_e2e.py --cov-fail-under=0 -p no:cacheprovider --basetemp=$BT/t -q
# 3. the snapshot: regenerating it must leave the patched fixture unchanged
PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest tests/test_gateway_tool_schemas.py::test_advertised_schemas_match_snapshot \
  --cov-fail-under=0 -p no:cacheprovider --basetemp=$BT/s -q && git diff --exit-code tests/fixtures/gateway_tool_schemas.json
# 4. CI gates the list above would otherwise miss
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src/pmcp/types.py src/pmcp/client/manager.py
python3 scripts/check_security_claims.py          # expect OK
# 5. the repro (expect ttl=300000 and ok=False only at t=300.000)
uv run python repro_330.py
# 6. the mutation table (expect 16 KILLED)
uv run python mutants330.py
# 7. the full suite: once, detached, with a notifying waiter (memory on dev0 is shared)
env -u npm_config_cache -u npm_config_store_dir nohup uv run pytest -q -p no:cacheprovider --basetemp=$BT/full \
  > $BT/full.log 2>&1 &
```

Step 3 uses `git diff --exit-code` rather than reading a diffstat. The
fixture is already patched, so a regeneration that changes it means the
schema and the snapshot disagree.

## Red on main

The new module was run against main `2adcd9a`'s sources. `MAX_TASK_SECONDS`
does not exist on main, so the import was shimmed: the import line was dropped
and `MAX_TASK_SECONDS = (2**53 - 1) // 1000` was defined in the module. Result:
**23 failed, 31 passed**.

| Test | Failed | Cause on main |
|---|---|---|
| `test_a_ttl_of_300_lasts_300_seconds_on_a_spec_downstream` | 1 | the downstream got `ttl: 300` (ms) |
| `test_outbound_ttl_and_poll_interval_are_sent_in_milliseconds` | 1 | `{"ttl": 300, "pollInterval": 2.5}` |
| `test_every_inbound_path_reports_seconds` | 2 | `ttl` 300000 / `poll_interval` 2500.0 reported as is |
| `test_a_usable_downstream_duration_is_reported_in_seconds` | 8 | ms kept as given, `int` type |
| `test_an_unusable_downstream_duration_is_named_not_converted[…5e-324]` | 1 | a value that rounds to 0 s is "usable" when nothing divides |
| `test_caller_bounds_are_in_seconds` | 4 | 9,007,199,254,741 … 2^53 − 1 are accepted, at the gate and in the model |
| `test_the_largest_accepted_value_does_not_overflow_on_the_wire` | 1 | `ttl` forwarded unscaled (`9007199254740`) |
| `test_the_model_checks_seconds[ttl-1.5-True]` | 1 | the model demands integer ms |
| `test_seconds_survive_every_revalidation_unchanged` | 1 | `ttl` 1500 stays 1500 |
| `test_the_outbound_choke_point_converts…`, `test_the_inbound_choke_point_converts…`, `test_each_converter_has_exactly_one_caller` | 1 each | no converter exists |

These pass on main, by design:
- the two round-trip properties, because main's pass-through is the identity
  in both directions. They pin **symmetry**, and M1–M4, M9 and M16 show that
  they fail when one direction converts and the other does not;
- the null/absent and non-numeric controls, which #330 must not change;
- `test_only_the_two_choke_points_name…` and
  `test_every_other_duration_assignment…`. Main already has exactly those two
  sites. M12–M14 prove that the tests can fail.

The migrated existing modules (`test_task_numeric_bounds.py`,
`test_client_manager.py`, `test_phase6_tenant_code_mode.py`, with the same
shim) give **13 failed, 562 passed** on main. The failures break down as:
- `test_task_hint_bounds`, 3: `MAX_TASK_SECONDS + 1` for both fields, and
  2^53 − 1 for `ttl`, are accepted on main;
- `USABLE`, 5: the ms values come back unconverted;
- `test_forwarded_task_hints_are_spec_shaped`, 1;
- the alias test, 2;
- the two `test_client_manager.py` tests.

## Mutation table

All 16 mutants were measured on the spike with `mutants330.py`. Each run
applies one string replacement, runs `tests/test_task_units.py` (54 cases), and
then restores the file from a copy saved in memory. **All 16 are killed.**
Afterwards the source/test diff was byte-identical to the spike patch.

| # | Rule | Mutant | Failed (measured) |
|---|---|---|---|
| M1 | outbound `ttl` converted | `payload["ttl"] = parsed.ttl` | 7: the repro, outbound, every-inbound-path, overflow, outbound-structural, ttl round trip |
| M2 | outbound `pollInterval` converted | `payload["pollInterval"] = parsed.poll_interval` | 4: outbound, poll round trip, overflow, outbound-structural |
| M3 | inbound `ttl` converted | `ttl=payload.get("ttl")` | 10: usable-in-seconds, unusable table, every-inbound-path, revalidation, inbound-structural, ttl round trip |
| M4 | inbound `pollInterval` converted | `poll_interval=poll_interval` | 9: usable-in-seconds, unusable table (underflow), every-inbound-path, poll round trip, revalidation, inbound-structural |
| M5 | inbound is float seconds | `usable / MS_PER_SECOND` → `usable // MS_PER_SECOND` | 8: usable-in-seconds, every-inbound-path, poll round trip, revalidation |
| M6 | caller bound restated in seconds | `MAX_TASK_SECONDS = MAX_FORWARDED_TASK_NUMBER` | 3: caller bounds, overflow |
| M7 | the model checks seconds | model table `"ttl": _usable_wire_task_ttl` | 4: usable-in-seconds, revalidation, model-checks-seconds |
| M8 | the wire checks ms (integer) | wire table `"ttl": _usable_task_ttl` | 2: usable-in-seconds (`int64` boundary), unusable table (`ttl: 1.5`) |
| M9 | `ttl: null` stays unlimited | converter `None` → `_UNUSABLE` | 22: null/absent, usable, unusable, both round trips |
| M10 | model `ttl` upper bound | drop `<= _INT64_MAX / MS_PER_SECOND` | 1: model-checks-seconds |
| M11 | model `ttl` lower bound | drop `0 <=` | 1: model-checks-seconds |
| M12 | no double conversion | `_record_task` passes `ttl` through `task_duration_from_wire` again | 6: one-caller, every-inbound-path, other-assignments, choke-points, revalidation |
| M13 | no bypassing inbound site | `cancel_task` fallback adds `ttl=result.get("ttl")` | 2: other-assignments, choke-points |
| M14 | no bypassing outbound site | `_task_request_params` adds `"ttl": 300` to `params.task` | 1: choke-points |
| M15 | the factor is 1000 | `MS_PER_SECOND = 1024` | 14: the repro, usable, every-inbound-path, outbound, revalidation, overflow, model-checks-seconds |
| M16 | outbound `pollInterval` is not rounded | `int(seconds * MS_PER_SECOND)` | 2: poll round trip, overflow |

```python
"""Apply one string replacement, run tests/test_task_units.py, restore from an
in-memory copy. Usage: uv run python mutants330.py (from the worktree root)."""
import re, subprocess, sys
M = [
 ("M1","outbound ttl unconverted","src/pmcp/client/manager.py",
  'payload["ttl"] = task_seconds_to_wire(parsed.ttl)','payload["ttl"] = parsed.ttl'),
 ("M2","outbound pollInterval unconverted","src/pmcp/client/manager.py",
  'payload["pollInterval"] = task_seconds_to_wire(parsed.poll_interval)','payload["pollInterval"] = parsed.poll_interval'),
 ("M3","inbound ttl unconverted","src/pmcp/client/manager.py",
  'ttl=task_duration_from_wire("ttl", payload.get("ttl")),','ttl=payload.get("ttl"),'),
 ("M4","inbound pollInterval unconverted","src/pmcp/client/manager.py",
  'poll_interval=task_duration_from_wire("poll_interval", poll_interval),','poll_interval=poll_interval,'),
 ("M5","inbound floor division (int seconds)","src/pmcp/types.py",
  "return usable / MS_PER_SECOND","return usable // MS_PER_SECOND"),
 ("M6","caller bound not restated (ms-sized)","src/pmcp/types.py",
  "MAX_TASK_SECONDS = MAX_FORWARDED_TASK_NUMBER // MS_PER_SECOND","MAX_TASK_SECONDS = MAX_FORWARDED_TASK_NUMBER"),
 ("M7","model checks ttl in ms (integer rule)","src/pmcp/types.py",
  '    "ttl": _usable_task_ttl,\n','    "ttl": _usable_wire_task_ttl,\n'),
 ("M8","wire ttl checked in seconds (fractional ms ok)","src/pmcp/types.py",
  '    "ttl": _usable_wire_task_ttl,\n}','    "ttl": _usable_task_ttl,\n}'),
 ("M9","sent null ttl made unusable","src/pmcp/types.py",
  "    if value is None:\n        return None\n    usable = _WIRE","    if value is None:\n        return _UNUSABLE\n    usable = _WIRE"),
 ("M10","model ttl upper bound dropped","src/pmcp/types.py",
  "0 <= number <= _INT64_MAX / MS_PER_SECOND","0 <= number"),
 ("M11","model ttl lower bound dropped","src/pmcp/types.py",
  "0 <= number <= _INT64_MAX / MS_PER_SECOND","number <= _INT64_MAX / MS_PER_SECOND"),
 ("M12","inbound converted twice (record re-converts)","src/pmcp/client/manager.py",
  "            ttl=task_info.ttl,\n            poll_interval=task_info.poll_interval,\n            unusable_fields=unusable,",
  "            ttl=task_duration_from_wire(\"ttl\", task_info.ttl),\n            poll_interval=task_info.poll_interval,\n            unusable_fields=unusable,"),
 ("M13","new bypassing inbound site (cancel fallback reads raw ttl)","src/pmcp/client/manager.py",
  '                status="cancelled",\n                updated_at=time.time(),',
  '                status="cancelled",\n                updated_at=time.time(),\n                ttl=result.get("ttl"),'),
 ("M14","new bypassing outbound site (requestor params carry ttl)","src/pmcp/client/manager.py",
  '            payload["task"] = {"requestorContext": requestor_context}',
  '            payload["task"] = {"requestorContext": requestor_context, "ttl": 300}'),
 ("M15","conversion factor wrong","src/pmcp/types.py",
  "MS_PER_SECOND = 1000\n","MS_PER_SECOND = 1024\n"),
 ("M16","outbound converter rounds poll to int ms","src/pmcp/types.py",
  "    return seconds * MS_PER_SECOND\n","    return int(seconds * MS_PER_SECOND)\n"),
]
only = set(sys.argv[1:])
results = []
for mid, rule, path, old, new in M:
    if only and mid not in only: continue
    src = open(path).read()
    assert src.count(old) == 1, (mid, src.count(old))
    open(path, "w").write(src.replace(old, new))
    try:
        p = subprocess.run(["uv","run","pytest","tests/test_task_units.py","--cov-fail-under=0",
            "-p","no:cacheprovider",f"--basetemp=/var/tmp/pmcp-330-bt-viperjuice/mut-{mid}",
            "-q","--no-header","-rf"], capture_output=True, text=True)
    finally:
        open(path, "w").write(src)
    failed = sorted({re.sub(r"\[.*", "", l.split("::",1)[1].split(" ")[0]) for l in p.stdout.splitlines() if l.startswith("FAILED")})
    n = sum(1 for l in p.stdout.splitlines() if l.startswith("FAILED"))
    err = "ERROR" in p.stdout and n == 0
    print(f"{mid}|{rule}|{'KILLED' if n or err else 'SURVIVED'}|{n}|{', '.join(failed)}", flush=True)
```

## Embedding proof

This proof was measured on 2026-10-04, on a **fresh** detached worktree of
`origin/main` at `2adcd9a`. That worktree is separate from the spike's.

1. **Extraction.** The patch was taken out of this file with
   `awk '/^````diff$/{f=1;next} /^````$/{f=0} f' plan.md > extracted.patch`.
   `cmp` shows it is byte-identical to the spike's `git diff`, with
   `tests/test_task_units.py` added through `git add -N`.
2. **Apply.** `git apply --check`, then `git apply`, both clean. The patch
   modified 9 files and created 1 (`tests/test_task_units.py`).
3. **The new module:** **54 passed**.
4. **The 8 touched modules:** **1072 passed**, 0 failed.
5. **Snapshot regeneration** left the patched fixture byte-identical to the
   patch's version.
6. **CI gates:** `ruff check` and `ruff format --check` passed (176 files),
   `mypy` passed on both source files, and `check_security_claims.py` printed
   `OK`.
7. **`repro_330.py`:** `ttl=300000` was sent, and `tasks_get` was ok until
   t = 299.999 s and failed at 300.000 s. That is the "with this plan's patch"
   output above.
8. **`mutants330.py`:** **16/16 KILLED**, with the same per-mutant failure
   counts as on the spike. The tree's diff was byte-identical afterwards.
9. **The full suite:**
   `env -u npm_config_cache -u npm_config_store_dir uv run pytest -q -p no:cacheprovider --basetemp=…`
   gave **8338 passed, 3 skipped, 80 deselected, 0 failed**, in 537 s. The
   spike tree gave the same result: 8338 passed, 3 skipped, in 557 s.
10. **Red on main** is in its own section above: 23 of the new module's 54
    cases fail on main, and 13 of the migrated cases fail on main.

The spike was then reverted with `git apply -R`, so this branch carries only
this plan file.

## Acceptance criteria

- [ ] A caller's `task.ttl` of `N` seconds reaches the downstream as
  `ttl: N*1000`, an int. `task.poll_interval` of `p` seconds reaches it as
  `pollInterval: p*1000`. Proven by
  `test_outbound_ttl_and_poll_interval_are_sent_in_milliseconds` and the
  repro.
- [ ] A downstream `ttl`/`pollInterval` in ms is reported in seconds (float)
  by `gateway.invoke`, `tasks_list`, `tasks_get`, `tasks_result` and
  `tasks_cancel`, and is recorded in seconds. `raw` keeps the ms. Proven by
  `test_every_inbound_path_reports_seconds` for both wire aliases.
- [ ] `ttl: 300` on a spec-conforming downstream lives 300 s, not 300 ms.
  Proven by `test_a_ttl_of_300_lasts_300_seconds_on_a_spec_downstream` and by
  `repro_330.py` (output above).
- [ ] The caller's bound is 9,007,199,254,740 s for both fields, at the gate
  and in the model, and the largest value forwards as ≤ 2^53 − 1 ms. Proven by
  `test_caller_bounds_are_in_seconds` and
  `test_the_largest_accepted_value_does_not_overflow_on_the_wire`.
- [ ] Downstream values are checked in ms as sent, under #298's rule.
  `ttl: null` and absent mean unlimited. Underflow to 0 s is unusable.
  Non-numeric values stay refused. Proven by the usable/unusable/null tables
  and `test_a_non_numeric_caller_duration_is_still_refused`.
- [ ] Converted seconds survive `_record_task`, `McpTaskInfo(**record)` and
  `_sanitize_task_for_output` unchanged. Proven by
  `test_seconds_survive_every_revalidation_unchanged`.
- [ ] The only places in `src/pmcp` that name a duration wire key are the two
  choke points; each converter has one caller; every other duration keyword
  copies seconds from a model. Proven by the five structural tests.
- [ ] All 16 mutants are killed by `tests/test_task_units.py`.
- [ ] README, the tenant contract and the CHANGELOG say: seconds in pmcp, ms
  on the wire, the conversion in both directions, `raw` verbatim, the new
  maximum, and the 1000× warning for callers who sent ms.
- [ ] The full suite passes; ruff, ruff format, mypy and
  `check_security_claims.py` are clean.

## Non-goals

- Changing pmcp's interface to milliseconds. The owner decided against it on
  2026-10-04.
- Dropping the non-spec outbound `pollInterval` (Design decision 6).
- Converting inside `raw`, or inside relayed results (Design decision 7).
- Expiring pmcp's own task records by `ttl`. pmcp records tasks for its
  lifetime and caps them (Consiliency/pmcp#338). This plan only fixes units.
- Any polling loop: #298 decision 7 still holds, and
  `test_poll_interval_has_no_consumer_outside_the_allowlist` is untouched and
  passes.
- Other `ttl`-named settings (JWKS cache, registry cache, idle timeout). These
  are pmcp-internal seconds and never cross the MCP wire.

## Unverified

- **A real third-party spec-conforming server.** The repro uses a faithful
  fake that applies the spec's text ("duration in milliseconds to retain task
  from creation"). No public MCP server that implements tasks retention was
  run against it.
- **Callers who already send ms.** The CHANGELOG warns them. Nothing in the
  repo can tell whether such callers exist.
- **#338 landing first.** The overlap table comes from reading #347's
  embedded patch, not from applying both. Whichever lands second re-runs
  steps 1–4 of *Verification*.

## Execution Policy

- execute: effort=low.
- reason: a contained change on the trust edge, in both directions. It touches
  2 source files (+90 / −16) through two choke points. Its one behaviour
  change is caller-visible, and the CHANGELOG states it.
- Re-run the mutation table, the 8-module run, ruff and mypy before requesting
  review.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the patch below (between the ```` fences) as `330.patch`.
2. Run `git apply 330.patch` on `2adcd9a`. It changes 9 files and creates
   `tests/test_task_units.py`, including the regenerated snapshot fixture and
   the README, contract and CHANGELOG edits.
3. Save `repro_330.py` and `mutants330.py` from above at the worktree root.
   They are run, not committed.

### Patch — `src/pmcp/{types.py, client/manager.py}`, tests, fixture, `README.md`, `CHANGELOG.md`, `specs/tenant-code-mode-host-contract.md`

````diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 1eb3866..a6e70e7 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -634,12 +634,14 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 ### Changed
 - **`gateway.invoke`'s `task.ttl` and `task.poll_interval` are bounded, and
   NaN/Infinity are refused at the gate (see Consiliency/pmcp#298).**
-  - `task.ttl` must be an integer from 1 to 2^53−1. Zero and negative values,
-    which were forwarded downstream unchanged, are now rejected with
-    `Input validation error: …`, and so is any value above 2^53−1.
+  - `task.ttl` must be an integer from 1 to 9,007,199,254,740: 2^53−1
+    milliseconds once Consiliency/pmcp#330 converts it (next entry). Zero and
+    negative values, which were forwarded downstream unchanged, are now
+    rejected with `Input validation error: …`, and so is any value above the
+    maximum.
   - `task.poll_interval` must be a finite number greater than 0 and at most
-    2^53−1. Zero, negative values, `NaN`, `Infinity` and `-Infinity` are now
-    rejected; they were previously accepted and forwarded.
+    9,007,199,254,740. Zero, negative values, `NaN`, `Infinity` and `-Infinity`
+    are now rejected; they were previously accepted and forwarded.
   - The transport gate now treats `NaN` and `±Infinity` as non-numbers for
     every numeric argument. Both transports can deliver them, even though they
     are not JSON. Until Consiliency/pmcp#297 lands, a rejection message may
@@ -689,9 +691,42 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
     originates) that contains one is dropped and logged, never written. Before,
     stdio servers received a non-JSON `NaN` literal, and HTTP/SSE servers
     silently received `null`.
-  - **Known follow-up:** pmcp documents `ttl` and `poll_interval` in seconds,
-    but MCP defines both in milliseconds, and pmcp forwards them unchanged.
-    Tracked as Consiliency/pmcp#330.
+  - pmcp documented `ttl` and `poll_interval` in seconds, but MCP defines both
+    in milliseconds, and pmcp forwarded them unchanged. The next entry
+    (Consiliency/pmcp#330) fixes that.
+- **Task `ttl` and `poll_interval` are now converted between pmcp's seconds and
+  MCP's milliseconds (see Consiliency/pmcp#330).** pmcp has always documented
+  `gateway.invoke`'s `task.ttl` and `task.poll_interval` in seconds. MCP
+  2025-11-25 defines `ttl` and `pollInterval` in milliseconds, and pmcp passed
+  the number through unchanged. So `task: {ttl: 300}`, meant as five minutes,
+  gave a spec-conforming server a 300 ms retention, and its task was gone
+  0.3 s later.
+  - **If you worked around this by sending milliseconds, your values are now
+    1000× too long.** `task: {ttl: 300000}` used to mean five minutes to a
+    spec-conforming server. It now asks for 300,000 seconds, about 3.5 days.
+    Send seconds instead: `ttl: 300`. The same applies to `poll_interval`.
+  - Outbound: `task.ttl` is sent as `ttl` in milliseconds (seconds × 1000,
+    exact). `task.poll_interval` is sent as `pollInterval` × 1000. MCP's
+    `TaskMetadata` has no `pollInterval`, so a spec-conforming server ignores
+    it.
+  - Inbound: a downstream task's `ttl` and `pollInterval` are read in
+    milliseconds. pmcp reports and records them as `ttl` and `poll_interval`
+    in seconds, everywhere a task is returned: `gateway.invoke`'s `task`,
+    `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result` and
+    `gateway.tasks_cancel`. A task that used to show `ttl: 300000` now shows
+    `ttl: 300.0`. `ttl` is now a number that may be fractional, not an
+    integer: `1500` ms is reported as `1.5`. A `ttl` of `null` still means
+    unlimited.
+  - Bounds: the caller's maximum for both fields is now 9,007,199,254,740
+    seconds, so that the milliseconds pmcp sends stay within 2^53−1. Larger
+    values, which the previous entry accepted up to 2^53−1, are rejected
+    with `Input validation error: …`. A downstream value is checked as sent,
+    in milliseconds, under the previous entry's rules: `ttl` must be an
+    integer from 0 to 2^63−1, and `pollInterval` a finite number greater than
+    0. Only then is it converted. A `pollInterval` so small that it divides to
+    0 seconds is reported as unusable.
+  - Unchanged: a task's `raw` object, and the results pmcp relays as sent,
+    keep the downstream's own milliseconds.
 - **`pmcp startup add/set --source project` now carries your prior trust approval forward when it rewrites `.mcp.json`.** Setting the startup policy changes the file's bytes, and trust approval is content-keyed, so the edit used to silently invalidate your own `pmcp trust approve` of that file and the next startup refused it. When the pre-write bytes were approved, pmcp now re-records the approval for the exact bytes it writes — keyed on the opened descriptor's verified identity (the resolved key must name the same file the descriptor holds open), never re-approving a file that was not already approved, and never approving a substituted file. A target swapped or unlinked mid-operation is refused rather than mis-bound, and on POSIX a symlinked `.mcp.json` is refused up front. If re-recording ever fails because the trust store is unusable, the edit is still written and the failure is surfaced as a diagnostic rather than crashing (an unusable store also fails the approval check, so nothing is silently carried forward). See [#253](https://github.com/Consiliency/pmcp/issues/253).
 - **`pmcp trust approve` now refuses a store resident in the checkout containing the file being approved**, matching what `serve --project` enforces — so approve no longer reports success for an approval that serve will then refuse. See [#252](https://github.com/Consiliency/pmcp/issues/252).
 - **Every install spawn now logs the command it runs, at WARNING, before it
diff --git a/README.md b/README.md
index 0578946..b840ca4 100644
--- a/README.md
+++ b/README.md
@@ -1502,7 +1502,13 @@ Tenant runs use the existing task broker. Submit long-running work with
 `gateway.invoke` and non-secret `task.metadata`, `task.ttl`,
 `task.poll_interval`, `task.requestor_context`, and trace keys such as
 `_meta.traceparent`; PMCP forwards those fields to the downstream server only
-when the server and tool advertise task support. The returned downstream MCP
+when the server and tool advertise task support. `task.ttl` and
+`task.poll_interval` are in seconds, at most 9,007,199,254,740. PMCP sends them
+downstream in milliseconds, which is the unit MCP 2025-11-25 uses (`ttl: 300`
+reaches the server as `ttl: 300000`). The `ttl` and `poll_interval` of every
+task PMCP returns are converted back from the server's milliseconds to seconds,
+and may be fractional (`1500` ms is reported as `1.5`). A task's `raw` object
+keeps the values exactly as the server sent them. The returned downstream MCP
 task ID is then used with `gateway.tasks_list`, `gateway.tasks_get`,
 `gateway.tasks_result`, and `gateway.tasks_cancel`. Do not use PMCP request IDs
 from `gateway.list_pending` or `gateway.cancel` for tenant task operations.
diff --git a/specs/tenant-code-mode-host-contract.md b/specs/tenant-code-mode-host-contract.md
index 998e2e5..12cf51c 100644
--- a/specs/tenant-code-mode-host-contract.md
+++ b/specs/tenant-code-mode-host-contract.md
@@ -109,6 +109,24 @@ clients, but PMCP does not persist task records past gateway process lifetime.
 A returned value PMCP cannot use is surfaced as `null` and named in the task's
 `unusable_fields`.
 
+Units (Consiliency/pmcp#330). On the wire, `ttl` and `pollInterval` are in
+milliseconds, as MCP 2025-11-25 defines them. The tenant server receives them in
+milliseconds and must return them in milliseconds. PMCP's own interface uses
+seconds, and PMCP converts at the boundary in both directions:
+
+- Outbound: the caller's `task.ttl` and `task.poll_interval`, in seconds, are
+  multiplied by 1000. A caller's `ttl: 300` reaches the tenant server as
+  `ttl: 300000`.
+- Inbound: a returned `ttl` or `pollInterval` is checked in milliseconds, as
+  sent. A `ttl` must be an integer in [0, 2^63 − 1], or `null` for unlimited. A
+  `pollInterval` must be a finite number greater than 0. The value is then
+  divided by 1000, and PMCP reports and records it as `ttl` and `poll_interval`
+  in seconds. Both are numbers that may be fractional: `ttl: 1500` becomes
+  `1.5`. A `ttl` of `null` stays `null`, which means unlimited. A
+  `pollInterval` so small that it divides to 0 seconds is unusable.
+- The task's `raw` object, and any result PMCP relays as sent, keep the tenant
+  server's own values and units.
+
 ## Metadata Forwarding Contract
 
 PMCP can forward OpenTelemetry-style trace context through
@@ -119,8 +137,12 @@ documented. These values are strings only and are metadata, not authentication.
 Task metadata supplied to `gateway.invoke` may include:
 
 - `metadata`: a bounded object for tenant-server execution context.
-- `ttl`: task lifetime hint in seconds.
-- `pollInterval`: polling hint forwarded on the wire as `pollInterval`.
+- `ttl`: requested task retention in seconds, at most 9,007,199,254,740. It is
+  sent to the tenant server as `ttl` in milliseconds, which is the MCP
+  `TaskMetadata.ttl`.
+- `poll_interval`: polling hint in seconds, at most 9,007,199,254,740. It is
+  forwarded on the wire as `pollInterval` in milliseconds. MCP 2025-11-25's
+  `TaskMetadata` defines only `ttl`, so a tenant server may ignore this one.
 - `requestor_context` or downstream `requestorContext`: non-secret context for
   host/client visibility.
 
diff --git a/src/pmcp/client/manager.py b/src/pmcp/client/manager.py
index cae5645..e202341 100644
--- a/src/pmcp/client/manager.py
+++ b/src/pmcp/client/manager.py
@@ -40,7 +40,9 @@ from pmcp.validation import normalized_executable_name
 from pmcp.types import (
     LocalMcpServerConfig,
     UNUSABLE_TASK_VALUE,
+    task_duration_from_wire,
     task_hint_is_usable,
+    task_seconds_to_wire,
     McpTaskInfo,
     McpTaskRecord,
     PromptArgumentInfo,
@@ -1675,10 +1677,12 @@ class ClientManager:
         payload: dict[str, Any] = {}
         if parsed.metadata:
             payload["metadata"] = parsed.metadata
+        # pmcp's seconds become MCP's milliseconds here, and only here
+        # (Consiliency/pmcp#330).
         if parsed.ttl is not None:
-            payload["ttl"] = parsed.ttl
+            payload["ttl"] = task_seconds_to_wire(parsed.ttl)
         if parsed.poll_interval is not None:
-            payload["pollInterval"] = parsed.poll_interval
+            payload["pollInterval"] = task_seconds_to_wire(parsed.poll_interval)
         if parsed.requestor_context:
             payload["requestorContext"] = parsed.requestor_context
         return payload
@@ -1760,8 +1764,10 @@ class ClientManager:
             status_message=status_message if isinstance(status_message, str) else None,
             created_at=created_at,
             updated_at=updated_at,
-            ttl=payload.get("ttl"),
-            poll_interval=poll_interval,
+            # MCP's milliseconds become pmcp's seconds here, and only here;
+            # `raw` keeps the downstream's own units (Consiliency/pmcp#330).
+            ttl=task_duration_from_wire("ttl", payload.get("ttl")),
+            poll_interval=task_duration_from_wire("poll_interval", poll_interval),
             raw=payload,
         )
 
diff --git a/src/pmcp/types.py b/src/pmcp/types.py
index 448e714..2d0d17f 100644
--- a/src/pmcp/types.py
+++ b/src/pmcp/types.py
@@ -89,6 +89,15 @@ DEFAULT_AUTH_STATE_SEMANTICS: dict[AuthState, AuthStateSemanticsInfo] = {
 #: the upper bound on numeric task hints pmcp forwards downstream.
 MAX_FORWARDED_TASK_NUMBER = 2**53 - 1
 
+#: MCP 2025-11-25 carries `TaskMetadata.ttl`, `Task.ttl` and `Task.pollInterval`
+#: in milliseconds; pmcp's own interface uses seconds (Consiliency/pmcp#330).
+MS_PER_SECOND = 1000
+
+#: The largest `task.ttl` / `task.poll_interval` a caller may send, in seconds:
+#: times `MS_PER_SECOND`, it is still at most `MAX_FORWARDED_TASK_NUMBER` ms
+#: (Consiliency/pmcp#298 bound, restated for Consiliency/pmcp#330).
+MAX_TASK_SECONDS = MAX_FORWARDED_TASK_NUMBER // MS_PER_SECOND
+
 
 class GatewayArguments(BaseModel):
     """Base for every model that parses arguments an agent sends to a gateway
@@ -539,9 +548,10 @@ UNUSABLE_TASK_VALUE: Any = object()
 _UNUSABLE = UNUSABLE_TASK_VALUE
 
 
-def _usable_task_ttl(value: Any) -> Any:
-    """A non-bool integer in [0, int64], or a finite whole-number float there
-    (``300000.0``: JSON Schema calls it an integer too)."""
+def _usable_wire_task_ttl(value: Any) -> Any:
+    """A downstream ``ttl`` in milliseconds: a non-bool integer in [0, int64],
+    or a finite whole-number float there (``300000.0``: JSON Schema calls it an
+    integer too)."""
     if type(value) is float and math.isfinite(value) and value.is_integer():
         value = int(value)
     if type(value) is int and 0 <= value <= _INT64_MAX:
@@ -549,6 +559,20 @@ def _usable_task_ttl(value: Any) -> Any:
     return _UNUSABLE
 
 
+def _usable_task_ttl(value: Any) -> Any:
+    """A ``ttl`` in seconds, as pmcp holds it: a non-bool, finite number in
+    [0, int64 / 1000] -- any value the wire check above accepts, divided by
+    1000 (Consiliency/pmcp#330)."""
+    if type(value) in (int, float):
+        try:
+            number = float(value)
+        except OverflowError:
+            return _UNUSABLE
+        if math.isfinite(number) and 0 <= number <= _INT64_MAX / MS_PER_SECOND:
+            return number
+    return _UNUSABLE
+
+
 def _usable_poll_interval(value: Any) -> Any:
     """A non-bool, finite number greater than 0."""
     if type(value) in (int, float):
@@ -609,10 +633,43 @@ _TASK_HINT_CHECKS: dict[str, Any] = {
 }
 
 
+#: The checks a downstream value gets AS SENT, before any conversion: the
+#: durations are checked in the wire's milliseconds (Consiliency/pmcp#330).
+_WIRE_TASK_HINT_CHECKS: dict[str, Any] = {
+    **_TASK_HINT_CHECKS,
+    "ttl": _usable_wire_task_ttl,
+}
+
+
 def task_hint_is_usable(name: str, value: Any) -> bool:
-    """Whether ``value`` passes the check for task field ``name`` -- for the
-    downstream parser choosing among a field's wire aliases."""
-    return value is not None and _TASK_HINT_CHECKS[name](value) is not _UNUSABLE
+    """Whether wire value ``value`` passes the check for task field ``name`` --
+    for the downstream parser choosing among a field's wire aliases."""
+    return value is not None and _WIRE_TASK_HINT_CHECKS[name](value) is not _UNUSABLE
+
+
+def task_seconds_to_wire(seconds: int | float) -> int | float:
+    """The one outbound conversion (Consiliency/pmcp#330): a caller's
+    ``task.ttl`` / ``task.poll_interval`` in seconds, as the milliseconds MCP
+    2025-11-25 puts on the wire. Exact for an integer ``ttl``; the caller-side
+    bound (``MAX_TASK_SECONDS``) keeps the result within I-JSON."""
+    return seconds * MS_PER_SECOND
+
+
+def task_duration_from_wire(name: str, value: Any) -> Any:
+    """The one inbound conversion (Consiliency/pmcp#330): a downstream
+    ``ttl`` / ``pollInterval`` in milliseconds, checked in milliseconds (the
+    Consiliency/pmcp#298 rule), as float seconds.
+
+    ``None`` (not sent; for ``ttl`` also a sent ``null``, MCP's "unlimited")
+    stays ``None``. A value the wire check refuses, and the parser's
+    ``UNUSABLE_TASK_VALUE``, come back as ``UNUSABLE_TASK_VALUE`` for the model
+    to report as unusable."""
+    if value is None:
+        return None
+    usable = _WIRE_TASK_HINT_CHECKS[name](value)
+    if usable is _UNUSABLE:
+        return _UNUSABLE
+    return usable / MS_PER_SECOND
 
 
 class McpTaskInfo(BaseModel):
@@ -631,7 +688,10 @@ class McpTaskInfo(BaseModel):
     status_message: str | None = None
     created_at: float | None = None
     updated_at: float | None = None
-    ttl: int | None = None
+    #: Seconds. The downstream sends milliseconds; ``raw`` keeps them as sent
+    #: (Consiliency/pmcp#330).
+    ttl: float | None = None
+    #: Seconds, as ``ttl``.
     poll_interval: float | None = None
     unusable_fields: list[str] = Field(default_factory=list)
     raw: dict[str, Any] = Field(default_factory=dict)
@@ -694,10 +754,14 @@ class TaskMetadataInput(GatewayArguments):
         # `TaskMetadata.ttl` integer, which a JavaScript peer reads as a double
         # -- above 2**53 - 1 it is no longer the integer the caller sent. The
         # bound also keeps the gate and the model agreeing on floats outside
-        # int64 (Consiliency/pmcp#236).
+        # int64 (Consiliency/pmcp#236). Seconds, sent downstream as
+        # milliseconds, so the bound is 2**53 - 1 ms (Consiliency/pmcp#330).
         ge=1,
-        le=MAX_FORWARDED_TASK_NUMBER,
-        description="Requested task TTL in seconds",
+        le=MAX_TASK_SECONDS,
+        description=(
+            "Requested task TTL in seconds (sent to the downstream server in "
+            "milliseconds)"
+        ),
     )
     poll_interval: float | None = Field(
         default=None,
@@ -706,10 +770,14 @@ class TaskMetadataInput(GatewayArguments):
         # integer of |n| >= 2**1024 - 2**970 past the gate to a model that
         # cannot hold it. `allow_inf_nan` is not projected into the schema;
         # the gate's validator refuses non-finite numbers itself.
+        # Seconds, sent downstream as milliseconds (Consiliency/pmcp#330).
         gt=0,
-        le=MAX_FORWARDED_TASK_NUMBER,
+        le=MAX_TASK_SECONDS,
         allow_inf_nan=False,
-        description="Seconds between task status polls",
+        description=(
+            "Seconds between task status polls (sent to the downstream server "
+            "in milliseconds)"
+        ),
     )
     requestor_context: dict[str, Any] | None = Field(
         default=None, description="Opaque requestor context forwarded downstream"
diff --git a/tests/fixtures/gateway_tool_schemas.json b/tests/fixtures/gateway_tool_schemas.json
index cf9be95..9ad4d10 100644
--- a/tests/fixtures/gateway_tool_schemas.json
+++ b/tests/fixtures/gateway_tool_schemas.json
@@ -291,9 +291,9 @@
       ]
      },
      "poll_interval": {
-      "description": "Seconds between task status polls",
+      "description": "Seconds between task status polls (sent to the downstream server in milliseconds)",
       "exclusiveMinimum": 0,
-      "maximum": 9007199254740991,
+      "maximum": 9007199254740,
       "type": [
        "number",
        "null"
@@ -308,8 +308,8 @@
       ]
      },
      "ttl": {
-      "description": "Requested task TTL in seconds",
-      "maximum": 9007199254740991,
+      "description": "Requested task TTL in seconds (sent to the downstream server in milliseconds)",
+      "maximum": 9007199254740,
       "minimum": 1,
       "type": [
        "integer",
diff --git a/tests/test_client_manager.py b/tests/test_client_manager.py
index dff5a63..08a22c6 100644
--- a/tests/test_client_manager.py
+++ b/tests/test_client_manager.py
@@ -1670,8 +1670,8 @@ class TestCallTool:
                 "task": {
                     "task_id": "tenant-run-1",
                     "status": "working",
-                    "ttl": 300,
-                    "poll_interval": 2.5,
+                    "ttl": 300000,
+                    "poll_interval": 2500,
                     "diagnostics": {"summary": "queued"},
                 }
             }
@@ -1705,8 +1705,8 @@ class TestCallTool:
             },
             "task": {
                 "metadata": {"run_kind": "smoke"},
-                "ttl": 300,
-                "pollInterval": 2.5,
+                "ttl": 300000,
+                "pollInterval": 2500.0,
                 "requestorContext": {"client": "mobile"},
             },
         }
@@ -1807,8 +1807,8 @@ class TestCallTool:
                             "statusMessage": "needs approval",
                             "createdAt": "2026-01-02T03:04:05Z",
                             "lastUpdatedAt": "2026-01-02T03:04:06Z",
-                            "ttl": 300,
-                            "pollInterval": 2,
+                            "ttl": 300000,
+                            "pollInterval": 2000,
                             "metadata": {"unknown": "kept"},
                         },
                         {
@@ -1816,8 +1816,8 @@ class TestCallTool:
                             "status": "host_custom_waiting",
                             "created_at": 1760000000,
                             "last_updated_at": 1760000001.5,
-                            "ttl": 120,
-                            "poll_interval": 0.5,
+                            "ttl": 120000,
+                            "poll_interval": 500,
                         },
                     ]
                 },
diff --git a/tests/test_phase6_tenant_code_mode.py b/tests/test_phase6_tenant_code_mode.py
index 53f20ea..bad189c 100644
--- a/tests/test_phase6_tenant_code_mode.py
+++ b/tests/test_phase6_tenant_code_mode.py
@@ -132,8 +132,8 @@ def _tenant_gateway(policy_manager: PolicyManager | None = None) -> GatewayTools
                 else "queued",
                 "createdAt": "2026-01-02T03:04:05Z",
                 "lastUpdatedAt": "2026-01-02T03:04:06Z",
-                "ttl": 300,
-                "pollInterval": 0.1,
+                "ttl": 300000,
+                "pollInterval": 100,
             }
             return {"task": tasks[task_id]}
         if method == "tasks/list":
diff --git a/tests/test_task_numeric_bounds.py b/tests/test_task_numeric_bounds.py
index f804183..0262d79 100644
--- a/tests/test_task_numeric_bounds.py
+++ b/tests/test_task_numeric_bounds.py
@@ -34,6 +34,7 @@ from pmcp.tools.handlers import GATEWAY_TOOL_INPUT_MODELS, get_gateway_tool_defi
 from pmcp.tools.schema import GATE_VALIDATOR
 from pmcp.types import (
     MAX_FORWARDED_TASK_NUMBER,
+    MAX_TASK_SECONDS,
     GatewayArguments,
     LocalMcpServerConfig,
     McpTaskInfo,
@@ -153,8 +154,9 @@ def test_gate_rejects_a_boolean_for_every_numeric_argument(
         (("task", "ttl"), 0, False),
         (("task", "ttl"), -5, False),
         (("task", "ttl"), 1, True),
-        (("task", "ttl"), MAX_FORWARDED_TASK_NUMBER, True),
-        (("task", "ttl"), MAX_FORWARDED_TASK_NUMBER + 1, False),
+        (("task", "ttl"), MAX_TASK_SECONDS, True),
+        (("task", "ttl"), MAX_TASK_SECONDS + 1, False),
+        (("task", "ttl"), MAX_FORWARDED_TASK_NUMBER, False),
         (("task", "poll_interval"), 0, False),
         (("task", "poll_interval"), -0.5, False),
         (("task", "poll_interval"), float("nan"), False),
@@ -164,7 +166,8 @@ def test_gate_rejects_a_boolean_for_every_numeric_argument(
         (("task", "poll_interval"), -BIG, False),
         (("task", "poll_interval"), 1e-300, True),
         (("task", "poll_interval"), 0.1, True),
-        (("task", "poll_interval"), MAX_FORWARDED_TASK_NUMBER, True),
+        (("task", "poll_interval"), MAX_TASK_SECONDS, True),
+        (("task", "poll_interval"), MAX_TASK_SECONDS + 1, False),
     ],
 )
 def test_task_hint_bounds(path: tuple[str, ...], value: Any, accepted: bool) -> None:
@@ -309,11 +312,12 @@ UNUSABLE: list[tuple[str, str, Any]] = [
     ],
 ]
 USABLE: list[tuple[str, str, Any, Any]] = [
-    ("ttl", "ttl", 0, 0),
-    ("ttl", "ttl", 60000, 60000),
-    ("ttl", "ttl", 300000.0, 300000),
-    ("pollInterval", "poll_interval", 2, 2.0),
-    ("pollInterval", "poll_interval", 2.5, 2.5),
+    # durations arrive in ms and are kept in seconds (Consiliency/pmcp#330)
+    ("ttl", "ttl", 0, 0.0),
+    ("ttl", "ttl", 60000, 60.0),
+    ("ttl", "ttl", 300000.0, 300.0),
+    ("pollInterval", "poll_interval", 2000, 2.0),
+    ("pollInterval", "poll_interval", 2.5, 0.0025),
     ("createdAt", "created_at", "2025-11-25T10:00:00Z", 1764064800.0),
     ("createdAt", "created_at", -5, -5.0),
     ("createdAt", "created_at", "5", 5.0),
@@ -854,7 +858,7 @@ def test_every_downstream_writer_encodes_through_the_strict_encoder() -> None:
 def test_forwarded_task_hints_are_spec_shaped() -> None:
     manager = ClientManager()
     wire = manager._task_wire_metadata({"ttl": 300, "poll_interval": 2.5})
-    assert wire == {"ttl": 300, "pollInterval": 2.5}
+    assert wire == {"ttl": 300000, "pollInterval": 2500.0}  # ms (#330)
     assert type(wire["ttl"]) is int and math.isfinite(wire["pollInterval"])
     _encode_outbound_frame(wire)
     for bad in (
@@ -899,8 +903,8 @@ def test_a_usable_timestamp_alias_wins_over_an_unusable_one(
     ("payload", "attr", "kept"),
     [
         ({"createdAt": None, "created_at": 5}, "created_at", 5.0),
-        ({"pollInterval": None, "poll_interval": 2.5}, "poll_interval", 2.5),
-        ({"pollInterval": 0, "poll_interval": 2.5}, "poll_interval", 2.5),
+        ({"pollInterval": None, "poll_interval": 2500}, "poll_interval", 2.5),
+        ({"pollInterval": 0, "poll_interval": 2500}, "poll_interval", 2.5),
     ],
 )
 def test_every_aliased_hint_prefers_its_usable_alias(
diff --git a/tests/test_task_units.py b/tests/test_task_units.py
new file mode 100644
index 0000000..f09ac58
--- /dev/null
+++ b/tests/test_task_units.py
@@ -0,0 +1,550 @@
+"""Task durations: seconds in pmcp, milliseconds on the MCP wire
+(Consiliency/pmcp#330).
+
+MCP 2025-11-25 carries `TaskMetadata.ttl`, `Task.ttl` and `Task.pollInterval`
+in milliseconds. pmcp's own interface (`gateway.invoke`'s `task.ttl` and
+`task.poll_interval`, and the `ttl`/`poll_interval` of every task it returns
+or records) is in seconds. The conversion happens at one choke point per
+direction:
+- outbound: `_task_wire_metadata` -> `task_seconds_to_wire`;
+- inbound: `_task_info_from_payload` -> `task_duration_from_wire`.
+
+The Consiliency/pmcp#298 bounds apply on the side where the value is in the
+unit they were written for: the caller's bound is restated in seconds so the
+forwarded milliseconds stay within I-JSON, and a downstream value is checked in
+milliseconds, as sent, before it is divided.
+"""
+
+from __future__ import annotations
+
+import ast
+import math
+import random
+from pathlib import Path
+from typing import Any
+from unittest.mock import MagicMock
+
+import pytest
+from pydantic import ValidationError
+
+from pmcp.client.manager import ClientManager, ManagedClient
+from pmcp.policy.policy import PolicyManager
+from pmcp.tools.handlers import GatewayTools, get_gateway_tool_definitions
+from pmcp.tools.schema import GATE_VALIDATOR
+from pmcp.types import (
+    MAX_FORWARDED_TASK_NUMBER,
+    MAX_TASK_SECONDS,
+    McpTaskInfo,
+    McpTaskRecord,
+    RemoteMcpServerConfig,
+    ResolvedServerConfig,
+    RiskHint,
+    ServerStatus,
+    ServerStatusEnum,
+    TaskMetadataInput,
+    ToolInfo,
+)
+
+SRC = Path(__file__).resolve().parent.parent / "src" / "pmcp"
+SERVER = "spec"
+TOOL_ID = f"{SERVER}::run"
+INT64_MAX = 2**63 - 1
+
+
+def _manager() -> ClientManager:
+    manager = ClientManager()
+    manager._tools[TOOL_ID] = ToolInfo(
+        tool_id=TOOL_ID,
+        server_name=SERVER,
+        tool_name="run",
+        description="d",
+        short_description="d",
+        input_schema={"type": "object"},
+        tags=[],
+        risk_hint=RiskHint.LOW,
+        execution={"taskSupport": "optional"},
+    )
+    status = ServerStatus(
+        name=SERVER,
+        status=ServerStatusEnum.ONLINE,
+        tool_count=1,
+        server_capabilities={"tasks": {}},
+        protocol_version="2025-11-25",
+    )
+    manager._servers[SERVER] = status
+    manager._clients[SERVER] = ManagedClient(
+        config=ResolvedServerConfig(
+            name=SERVER,
+            source="custom",
+            config=RemoteMcpServerConfig(
+                type="streamable-http", url="https://spec.example/mcp"
+            ),
+        ),
+        is_remote=True,
+        write_stream=MagicMock(),
+        status=status,
+    )
+    return manager
+
+
+class SpecDownstream:
+    """A downstream that honours MCP 2025-11-25: `params.task.ttl` is the
+    retention in MILLISECONDS from creation, and a task past it is gone. It
+    reports `ttl` and `pollInterval` back in milliseconds, under the alias the
+    test picks."""
+
+    def __init__(self, clock: list[float], poll_key: str = "pollInterval") -> None:
+        self.clock = clock
+        self.poll_key = poll_key
+        self.sent: list[tuple[str, dict[str, Any]]] = []
+        self.tasks: dict[str, dict[str, Any]] = {}
+
+    def _wire(self, task_id: str, status: str | None = None) -> dict[str, Any]:
+        task = self.tasks[task_id]
+        if status is not None:
+            task["status"] = status
+        return {
+            "taskId": task_id,
+            "status": task["status"],
+            "createdAt": "2026-10-04T00:00:00Z",
+            "lastUpdatedAt": "2026-10-04T00:00:00Z",
+            "ttl": task["ttl"],
+            self.poll_key: 2500,
+        }
+
+    def _live(self, task_id: str) -> None:
+        task = self.tasks.get(task_id)
+        if task is None or self.clock[0] - task["created"] >= task["ttl"] / 1000:
+            self.tasks.pop(task_id, None)
+            raise RuntimeError(f"Task not found: {task_id}")
+
+    async def __call__(
+        self, managed: Any, method: str, params: dict[str, Any], **_: Any
+    ) -> dict[str, Any]:
+        self.sent.append((method, params))
+        if method == "tools/call":
+            self.tasks["t1"] = {
+                "created": self.clock[0],
+                "ttl": params["task"]["ttl"],
+                "status": "working",
+            }
+            return {"task": self._wire("t1")}
+        if method == "tasks/list":
+            for task_id in list(self.tasks):
+                try:
+                    self._live(task_id)
+                except RuntimeError:
+                    pass
+            return {"tasks": [self._wire(task_id) for task_id in self.tasks]}
+        self._live(params["taskId"])
+        if method == "tasks/get":
+            return {"task": self._wire(params["taskId"])}
+        if method == "tasks/result":
+            return {
+                "task": self._wire(params["taskId"], "completed"),
+                "result": {"ok": True},
+            }
+        if method == "tasks/cancel":
+            return {"task": self._wire(params["taskId"], "cancelled")}
+        raise AssertionError(method)
+
+
+def _gateway(poll_key: str = "pollInterval") -> tuple[GatewayTools, SpecDownstream]:
+    manager = _manager()
+    clock = [0.0]
+    downstream = SpecDownstream(clock, poll_key)
+    manager._send_request = downstream  # type: ignore[method-assign]
+    return GatewayTools(client_manager=manager, policy_manager=PolicyManager()), (
+        downstream
+    )
+
+
+# --- the repro ------------------------------------------------------------------
+
+
+@pytest.mark.asyncio
+async def test_a_ttl_of_300_lasts_300_seconds_on_a_spec_downstream() -> None:
+    """The issue's repro: `task: {ttl: 300}` is five minutes in pmcp's docs.
+    Before #330 the downstream got `ttl: 300` -- 300 ms -- and the task was
+    gone after 0.3 s."""
+    gateway, downstream = _gateway()
+    invoked = await gateway.invoke({"tool_id": TOOL_ID, "task": {"ttl": 300}})
+    assert invoked.ok and invoked.task is not None
+    assert downstream.sent[0][1]["task"]["ttl"] == 300_000
+
+    downstream.clock[0] = 0.5  # past 300 ms
+    alive = await gateway.tasks_get({"server_name": SERVER, "task_id": "t1"})
+    assert alive.ok, alive.errors
+    downstream.clock[0] = 299.999
+    alive = await gateway.tasks_get({"server_name": SERVER, "task_id": "t1"})
+    assert alive.ok, alive.errors
+    downstream.clock[0] = 300.0
+    gone = await gateway.tasks_get({"server_name": SERVER, "task_id": "t1"})
+    assert not gone.ok
+
+
+# --- per boundary site ------------------------------------------------------------
+
+
+@pytest.mark.asyncio
+async def test_outbound_ttl_and_poll_interval_are_sent_in_milliseconds() -> None:
+    """Site O1 (`ttl`) and O2 (`pollInterval`), `_task_wire_metadata`: the only
+    place task durations go downstream."""
+    gateway, downstream = _gateway()
+    await gateway.invoke(
+        {"tool_id": TOOL_ID, "task": {"ttl": 300, "poll_interval": 2.5}}
+    )
+    ((method, params),) = downstream.sent
+    assert method == "tools/call"
+    assert params["task"] == {"ttl": 300_000, "pollInterval": 2500.0}
+    assert type(params["task"]["ttl"]) is int
+
+
+def _assert_seconds(task: Any) -> None:
+    data = task if isinstance(task, dict) else task.model_dump(mode="json")
+    assert data["ttl"] == 300.0 and data["poll_interval"] == 2.5, data
+    assert data["unusable_fields"] == [], data
+    # `raw` is what the downstream sent, in its own units.
+    assert data["raw"]["ttl"] == 300_000, data
+
+
+@pytest.mark.parametrize("poll_key", ["pollInterval", "poll_interval"])
+@pytest.mark.asyncio
+async def test_every_inbound_path_reports_seconds(poll_key: str) -> None:
+    """Sites I1 (`ttl`) and I2 (`pollInterval`/`poll_interval`) are read in
+    `_task_info_from_payload`, which each of the five downstream replies that
+    carry a task goes through. Each path is checked in what the caller gets
+    back AND in what pmcp records."""
+    gateway, _ = _gateway(poll_key)
+    manager = gateway._client_manager
+
+    def recorded() -> McpTaskRecord:
+        record = manager.get_task_record(SERVER, "t1")
+        assert record is not None
+        return record
+
+    invoked = await gateway.invoke(  # tools/call
+        {"tool_id": TOOL_ID, "task": {"ttl": 300}}
+    )
+    _assert_seconds(invoked.task)
+    _assert_seconds(recorded())
+
+    listed = await gateway.tasks_list({"server_name": SERVER})  # tasks/list
+    assert listed.ok, listed.errors
+    (task,) = listed.tasks
+    _assert_seconds(task)
+    _assert_seconds(recorded())
+
+    got = await gateway.tasks_get({"server_name": SERVER, "task_id": "t1"})
+    assert got.ok, got.errors
+    _assert_seconds(got.task)
+    _assert_seconds(recorded())
+
+    result = await gateway.tasks_result({"server_name": SERVER, "task_id": "t1"})
+    assert result.ok, result.errors
+    _assert_seconds(result.task)
+    _assert_seconds(recorded())
+
+    manager._record_task(SERVER, McpTaskInfo(task_id="t1", status="working"))
+    cancelled = await gateway.tasks_cancel({"server_name": SERVER, "task_id": "t1"})
+    assert cancelled.ok
+    _assert_seconds(cancelled.task)
+    _assert_seconds(recorded())
+
+
+# --- round trip -----------------------------------------------------------------
+
+
+def _round_trip(**task: Any) -> McpTaskInfo:
+    manager = ClientManager()
+    wire = manager._task_wire_metadata(task)
+    info = manager._task_info_from_payload({"taskId": "t", **wire})
+    assert info is not None and info.unusable_fields == [], (task, wire, info)
+    return info
+
+
+def test_ttl_round_trips_exactly() -> None:
+    """Integer seconds -> ms -> seconds is exact over the whole caller range."""
+    rng = random.Random(330)
+    samples = [1, 2, 59, 60, 300, 3600, 86_400, 10**9, MAX_TASK_SECONDS - 1]
+    samples += [MAX_TASK_SECONDS]
+    samples += [rng.randint(1, MAX_TASK_SECONDS) for _ in range(2000)]
+    for seconds in samples:
+        assert _round_trip(ttl=seconds).ttl == seconds
+
+
+def test_poll_interval_round_trips_to_within_rounding() -> None:
+    """Float seconds -> ms -> seconds: one multiply and one divide, each
+    correctly rounded, so the result is within 2 ulp of what was sent."""
+    rng = random.Random(330)
+    samples = [5e-324, 1e-300, 0.001, 0.1, 0.25, 1.0, 2.5, 30.0, MAX_TASK_SECONDS]
+    samples += [
+        10 ** rng.uniform(-300, math.log10(MAX_TASK_SECONDS)) for _ in range(2000)
+    ]
+    for seconds in samples:
+        back = _round_trip(poll_interval=seconds).poll_interval
+        assert back is not None
+        assert abs(back - seconds) <= 2 * math.ulp(seconds), (seconds, back)
+
+
+# --- the bounds, in the unit of each side --------------------------------------
+
+
+def _invoke_schema() -> dict[str, Any]:
+    (tool,) = [t for t in get_gateway_tool_definitions() if t.name == "gateway.invoke"]
+    return tool.input_schema
+
+
+@pytest.mark.parametrize(
+    ("field", "value", "accepted"),
+    [
+        ("ttl", 1, True),
+        ("ttl", MAX_TASK_SECONDS, True),
+        ("ttl", MAX_TASK_SECONDS + 1, False),
+        ("ttl", MAX_FORWARDED_TASK_NUMBER, False),  # the old, millisecond-sized bound
+        ("ttl", 0, False),
+        ("poll_interval", 5e-324, True),
+        ("poll_interval", float(MAX_TASK_SECONDS), True),
+        ("poll_interval", MAX_TASK_SECONDS + 0.5, False),
+        ("poll_interval", float(MAX_FORWARDED_TASK_NUMBER), False),
+        ("poll_interval", 0, False),
+    ],
+)
+def test_caller_bounds_are_in_seconds(field: str, value: Any, accepted: bool) -> None:
+    """The #298 range, restated: the caller's bound is in seconds so that, times
+    1000, it is still at most 2**53 - 1 ms. Gate and model agree."""
+    args = {"tool_id": "a::b", "task": {field: value}}
+    gate_ok = not list(GATE_VALIDATOR(_invoke_schema()).iter_errors(args))
+    try:
+        TaskMetadataInput.model_validate({field: value})
+        model_ok = True
+    except ValidationError:
+        model_ok = False
+    assert (gate_ok, model_ok) == (accepted, accepted)
+
+
+def test_the_largest_accepted_value_does_not_overflow_on_the_wire() -> None:
+    assert MAX_TASK_SECONDS == 9_007_199_254_740
+    wire = ClientManager()._task_wire_metadata(
+        {"ttl": MAX_TASK_SECONDS, "poll_interval": float(MAX_TASK_SECONDS)}
+    )
+    assert wire["ttl"] == 9_007_199_254_740_000 <= MAX_FORWARDED_TASK_NUMBER
+    assert type(wire["ttl"]) is int
+    assert wire["pollInterval"] == 9_007_199_254_740_000.0 <= MAX_FORWARDED_TASK_NUMBER
+    # the smallest accepted poll interval does not underflow to 0 ms
+    tiny = ClientManager()._task_wire_metadata({"poll_interval": 5e-324})
+    assert tiny["pollInterval"] > 0
+
+
+@pytest.mark.parametrize("value", ["300", True, [300], {"s": 300}, None])
+def test_a_non_numeric_caller_duration_is_still_refused(value: Any) -> None:
+    """Unchanged by #330: the gate refuses a string, a boolean or a container
+    for either field (`null` is accepted as "not given")."""
+    for field in ("ttl", "poll_interval"):
+        args = {"tool_id": "a::b", "task": {field: value}}
+        errors = list(GATE_VALIDATOR(_invoke_schema()).iter_errors(args))
+        assert (errors == []) == (value is None), (field, value)
+
+
+@pytest.mark.parametrize(
+    ("wire", "attr", "value", "kept"),
+    [
+        ("ttl", "ttl", 0, 0.0),
+        ("ttl", "ttl", 1, 0.001),
+        ("ttl", "ttl", 1500, 1.5),
+        ("ttl", "ttl", 300_000.0, 300.0),
+        ("ttl", "ttl", INT64_MAX, INT64_MAX / 1000),
+        ("pollInterval", "poll_interval", 1, 0.001),
+        ("pollInterval", "poll_interval", 2.5, 0.0025),
+        ("pollInterval", "poll_interval", 1e308, 1e305),
+    ],
+)
+def test_a_usable_downstream_duration_is_reported_in_seconds(
+    wire: str, attr: str, value: Any, kept: float
+) -> None:
+    info = ClientManager()._task_info_from_payload({"taskId": "t", wire: value})
+    assert info is not None
+    assert getattr(info, attr) == kept and type(getattr(info, attr)) is float
+    assert info.unusable_fields == []
+
+
+@pytest.mark.parametrize(
+    ("wire", "attr", "value"),
+    [
+        # the #298 rule, applied to the value AS SENT, in milliseconds
+        ("ttl", "ttl", 1.5),  # not an integer number of ms
+        ("ttl", "ttl", -1),
+        ("ttl", "ttl", INT64_MAX + 1),
+        ("ttl", "ttl", "300000"),
+        ("ttl", "ttl", True),
+        ("ttl", "ttl", float("nan")),
+        ("pollInterval", "poll_interval", 0),
+        ("pollInterval", "poll_interval", "2500"),
+        ("pollInterval", "poll_interval", float("inf")),
+        ("pollInterval", "poll_interval", None),
+        # usable in ms, but 5e-324 / 1000 underflows to 0 s: unusable
+        ("pollInterval", "poll_interval", 5e-324),
+    ],
+)
+def test_an_unusable_downstream_duration_is_named_not_converted(
+    wire: str, attr: str, value: Any
+) -> None:
+    info = ClientManager()._task_info_from_payload({"taskId": "t", wire: value})
+    assert info is not None
+    assert getattr(info, attr) is None and info.unusable_fields == [attr]
+
+
+def test_a_null_or_absent_ttl_stays_unlimited() -> None:
+    manager = ClientManager()
+    for payload in ({"taskId": "t", "ttl": None}, {"taskId": "t"}):
+        info = manager._task_info_from_payload(payload)
+        assert info is not None and info.ttl is None and info.unusable_fields == []
+
+
+@pytest.mark.parametrize(
+    ("attr", "value", "usable"),
+    [
+        ("ttl", 1.5, True),
+        ("ttl", INT64_MAX / 1000, True),
+        ("ttl", 1e300, False),
+        ("ttl", -1.0, False),
+        ("poll_interval", 0.0025, True),
+        ("poll_interval", 0.0, False),
+    ],
+)
+def test_the_model_checks_seconds(attr: str, value: float, usable: bool) -> None:
+    """`McpTaskInfo` holds seconds and is re-validated on every record, list
+    and output: its own check is in seconds, the wire check in ms."""
+    info = McpTaskInfo(task_id="t", **{attr: value})
+    assert (getattr(info, attr) == value) is usable
+    assert info.unusable_fields == ([] if usable else [attr])
+
+
+def test_seconds_survive_every_revalidation_unchanged() -> None:
+    """A converted task is validated again by `_record_task`, by
+    `gateway.tasks_list` (`McpTaskInfo(**record)`) and by
+    `_sanitize_task_for_output` (JSON dump, then validate). None of those may
+    convert again or re-apply the millisecond check."""
+    manager = ClientManager()
+    info = manager._task_info_from_payload(
+        {"taskId": "t", "ttl": 1500, "pollInterval": 250}
+    )
+    assert info is not None and (info.ttl, info.poll_interval) == (1.5, 0.25)
+    record = manager._record_task("s", info)
+    again = McpTaskInfo(
+        **{
+            k: v
+            for k, v in record.model_dump().items()
+            if k in McpTaskInfo.model_fields
+        }
+    )
+    output = GatewayTools(
+        client_manager=manager, policy_manager=PolicyManager()
+    )._sanitize_task_for_output(again)
+    for task in (record, again, output):
+        assert (task.ttl, task.poll_interval) == (1.5, 0.25), task
+        assert task.unusable_fields == []
+
+
+# --- structure: every boundary site goes through the converter ---------------------
+
+_WIRE_KEYS = {"ttl", "pollInterval", "poll_interval"}
+OUTBOUND = ("client/manager.py", "_task_wire_metadata")
+INBOUND = ("client/manager.py", "_task_info_from_payload")
+
+
+def _functions() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
+    found = []
+    for path in sorted(SRC.rglob("*.py")):
+        for node in ast.walk(ast.parse(path.read_text())):
+            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
+                found.append((str(path.relative_to(SRC)), node))
+    return found
+
+
+def _called(node: ast.AST) -> str | None:
+    if isinstance(node, ast.Call):
+        func = node.func
+        if isinstance(func, ast.Name):
+            return func.id
+        if isinstance(func, ast.Attribute):
+            return func.attr
+    return None
+
+
+def test_only_the_two_choke_points_name_a_task_duration_wire_key() -> None:
+    """A task duration crosses the downstream boundary only where its wire key
+    is named. Derived from the code: every function in `src/pmcp` that names
+    `ttl`, `pollInterval` or `poll_interval` as a string is one of the two
+    choke points. A new site (a second payload reader or writer) fails here
+    until it routes through the converter and is added."""
+    found = {
+        (path, func.name)
+        for path, func in _functions()
+        for node in ast.walk(func)
+        if isinstance(node, ast.Constant) and node.value in _WIRE_KEYS
+    }
+    assert found == {OUTBOUND, INBOUND}, found
+
+
+def test_the_outbound_choke_point_converts_every_duration_it_writes() -> None:
+    funcs = dict(((p, f.name), f) for p, f in _functions())
+    writes = [
+        node
+        for node in ast.walk(funcs[OUTBOUND])
+        if isinstance(node, ast.Assign)
+        and isinstance(node.targets[0], ast.Subscript)
+        and isinstance(node.targets[0].slice, ast.Constant)
+        and node.targets[0].slice.value in _WIRE_KEYS
+    ]
+    assert {w.targets[0].slice.value for w in writes} == {"ttl", "pollInterval"}  # type: ignore[attr-defined]
+    for write in writes:
+        assert _called(write.value) == "task_seconds_to_wire", ast.unparse(write)
+
+
+def test_the_inbound_choke_point_converts_every_duration_it_reads() -> None:
+    funcs = dict(((p, f.name), f) for p, f in _functions())
+    (build,) = [
+        node for node in ast.walk(funcs[INBOUND]) if _called(node) == "McpTaskInfo"
+    ]
+    durations = {
+        kw.arg: kw.value
+        for kw in build.keywords  # type: ignore[attr-defined]
+        if kw.arg in ("ttl", "poll_interval")
+    }
+    assert set(durations) == {"ttl", "poll_interval"}
+    for value in durations.values():
+        assert _called(value) == "task_duration_from_wire", ast.unparse(value)
+
+
+def test_every_other_duration_assignment_copies_seconds_from_a_model() -> None:
+    """Outside the inbound choke point, a `ttl=`/`poll_interval=` keyword in
+    `src/pmcp` only copies an already-converted model attribute (seconds to
+    seconds), so nothing else can feed a raw wire value into a task model."""
+    for path, func in _functions():
+        if (path, func.name) == INBOUND:
+            continue
+        for node in ast.walk(func):
+            if not isinstance(node, ast.Call):
+                continue
+            for kw in node.keywords:
+                if kw.arg in ("ttl", "poll_interval"):
+                    assert (
+                        isinstance(kw.value, ast.Attribute) and kw.value.attr == kw.arg
+                    ), (path, func.name, ast.unparse(kw.value))
+
+
+def test_each_converter_has_exactly_one_caller() -> None:
+    callers: dict[str, set[tuple[str, str]]] = {
+        "task_seconds_to_wire": set(),
+        "task_duration_from_wire": set(),
+    }
+    for path, func in _functions():
+        for node in ast.walk(func):
+            name = _called(node)
+            if name in callers:
+                callers[name].add((path, func.name))
+    assert callers == {
+        "task_seconds_to_wire": {OUTBOUND},
+        "task_duration_from_wire": {INBOUND},
+    }
````
