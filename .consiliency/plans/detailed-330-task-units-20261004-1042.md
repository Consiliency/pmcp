# Detailed plan: task `ttl` and `poll_interval` stay in seconds in pmcp, and are converted to and from MCP's milliseconds at one choke point per direction

> Written on main `2adcd9a` (dev0, a team host), worktree `pmcp-330`, branch
> `plan/330-task-units`. Every number below was measured on that tree, or on
> the spike of this plan applied to it. The spike was then removed, and this PR
> carries only this file. See Consiliency/pmcp#330.
>
> **Revision 3 (2026-10-04), rebased onto main `b2884db`.** Board round 2 on
> PR 355 (rev 2, `afb64d4`) had no blocking finding. Grok and gemini filed
> nothing, and codex was degraded. The claude seat said PARTIALLY AGREE: all
> three round-1 findings were resolved, and it raised one new non-blocking
> finding, which rev 3 takes as the coordinator directed.
>
> - **Round-2 F001.** The construction guard sees only a task model built
>   **by its class name**. Three other forms build or patch one from a raw
>   downstream reply, and the seat's mutants for them survived every task test:
>   - Y1: `TypeAdapter(McpTaskInfo).validate_python(...)`;
>   - Y2: a classmethod called through an instance, `record.model_validate(...)`;
>   - Y4: `rec.__dict__.update(...)`.
>
>   All three sat in the one reply path no units test covered: a
>   spec-conforming `tasks/result` with no task in it.
> - **Not done: chasing more forms in the guard.** That pattern never ends.
>   Rev 3 adds a **behavioural backstop** instead:
>   `test_every_reply_path_reports_and_records_seconds`. It covers every reply
>   path of every task operation (9 rows) against a spec-shaped downstream in
>   ms that also echoes stray ms fields on replies that carry no task. It
>   asserts that every task the gateway outputs and every record pmcp holds
>   reports seconds. `test_the_reply_path_table_covers_every_task_operation`
>   derives the operations from the code. The seat's falsifier is included as
>   `test_a_result_reply_without_a_task_never_records_wire_durations`.
> - **The guard's claim is narrowed** to "catches direct construction by
>   class name; the per-path behavioural test is the backstop" (Design
>   decision 11). Rev 2's "in any form" wording is withdrawn.
> - **Y1, Y2 and Y4 are rows in the mutation table.** Each is killed by the
>   per-path behavioural test, and by the falsifier: 24/24 killed.
> - **Rebase.** Main moved to `b2884db` (Consiliency/pmcp#358, the 2.8.0 docs
>   audit). Only `CHANGELOG.md` conflicted. Its #298 entry and "Known
>   follow-up" now use linked issue references, and a new "Upgrade notes"
>   section has a "Task numbers are bounded" line. The #330 entry is carried
>   over in that style. That line now gives the seconds maximum, and a new
>   upgrade note states the unit change for callers and tenant servers. The
>   other 9 files applied unchanged. `client/manager.py` is unchanged on main,
>   and the `handlers.py` task sites keep their line numbers, so the
>   boundary-site table still holds. The derivation sweep's line numbers are
>   from `2adcd9a`.
> - **A rev 1 verification bug is fixed.** Step 3 used `git diff --exit-code`
>   on the fixture, which always fails on a patched tree. It now compares the
>   fixture's sha256 before and after regeneration.
>
> **Re-measured on fresh trees under `/var/tmp`, from `b2884db`:** the new
> module (77 cases), red on main and green on the patch; the 8 touched modules;
> 24 mutants, each restore sha-verified; the full suite, once. Rev 2 is
> `afb64d4`.
>
> **Revision 2 (2026-10-04), on main `2adcd9a`.** Board round 1 on PR 355
> (rev 1, `b98de7d`) had no blocking finding. Grok, codex and gemini found no
> defect. The claude seat said PARTIALLY AGREE and raised three findings, all
> taken:
>
> - **F001 (docs).** The CHANGELOG and the tenant contract now warn **tenant
>   servers** built to the old seconds contract: they now receive ms, and the
>   seconds they return read 1000× too small. Both docs state that the
>   downstream's snake_case `poll_interval` is ms too. The #298 bullet now says
>   "at most 2^53−1 ms". The contract no longer says pmcp "forwards them when
>   supplied" unqualified. A docs test pins all of this.
> - **F002 (alias choice).** `pollInterval` vs `poll_interval` was chosen on
>   the ms value, before division, so a camelCase value that rounds to 0 s hid
>   a usable snake_case one. The choice is now made on the value as pmcp will
>   hold it, in seconds (Design decision 10), and a test covers it.
> - **F003 (class).** The structural guards only saw code that *names* a
>   duration. A task model built by spreading a downstream reply bypassed them;
>   the seat's mutants X1 and X2 survived. The fix closes the class (Design
>   decision 11). Every construction in `src/pmcp` of a model that carries a
>   task (`McpTaskInfo`, `McpTaskRecord`, and the 5 outputs that embed one,
>   found transitively from `pmcp.types`) is listed with its exact task-data
>   arguments and the reason they are already in seconds, in the forms
>   `Model(...)`, `Model(**x)`, `Model.model_validate(x)` and
>   `model_construct`. A new or changed one fails. (Rev 3 narrows this claim:
>   these forms are by class name only.) Attribute stores and `model_copy(update=…)` of a
>   duration are forbidden. Two provenance checks pin the two allowlisted
>   spreads, and three behavioural tests bind the fallbacks. X1, X2 and two
>   more (X9 `model_copy`, X10 the list fallback) are now in the mutation
>   table: 21/21 killed.
>
> **Re-measured on fresh trees under `/var/tmp`:** the new module, red on main
> and green on the patch; the 8 touched modules; 21 mutants, each restore
> sha-verified; the full suite, once. The overlap note now covers #338 rev 6
> (Design decision 9). Rev 1 is `b98de7d`.

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
| R5 | *construction, closed* | every other construction of a task-carrying model (`InvokeOutput`, `TasksListOutput`, `TasksGetOutput`, `TasksResultOutput`, `TasksCancelOutput`) | — | models only | rev 2: listed with exact arguments; a new one fails (Design decision 11) |
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
  3. Otherwise it divides by `MS_PER_SECOND` and applies the **seconds**
     check (rev 2). A value that underflows to 0 s therefore comes back as
     `UNUSABLE_TASK_VALUE` here, not only later in the model.
- `MS_PER_SECOND = 1000` is the only conversion constant.

The structural tests pin the layout. Only those two functions name a
duration wire key. Each converter has exactly one caller. Every `ttl=` or
`poll_interval=` keyword elsewhere copies a model attribute of the same name.

**Rev 2 narrows the rev 1 claim.** Those guards catch a reader or writer that
*names* a duration. Rev 1 concluded that no new reader could bypass the
conversion, and the board showed that a spread
(`McpTaskInfo.model_validate({**reply, …})`) does. Design decision 11 adds a
guard for construction by class name, and (rev 3) a per-path behavioural
backstop for every other form.

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
- Rev 2: the converter applies both checks, the ms check then the seconds
  check, through one private helper, `_wire_duration_seconds`. The alias
  picker uses the same helper (Design decision 10).

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
| Both poll aliases sent (rev 2) | `pollInterval` wins if it is usable **in seconds**; otherwise the first usable `poll_interval` does. `{"pollInterval": 5e-324, "poll_interval": 2500}` → 2.5 | `test_the_alias_is_chosen_by_usability_in_seconds`, `test_the_camel_case_alias_wins_when_both_are_usable`, M17 |
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

#347's plan (`detailed-338-task-cap-20261004-0451.md`, re-read for rev 2 at
`origin/plan/338-task-cap` rev 6, `57881a8`; rev 7 was in progress and not
pushed) touches the same files. In each, whichever PR lands
second rebases:

| File | #330 hunks | #338 hunks | Conflict risk |
|---|---|---|---|
| `src/pmcp/client/manager.py` | the import block (`task_duration_from_wire`, `task_seconds_to_wire`); `_task_wire_metadata` (1678-1681); the `McpTaskInfo(...)` return of `_task_info_from_payload` (1757-1765) | the import block; hunks from 1771 on (`_record_task` and later), the task proxies at 3992+ | adjacent at 1765/1771 and in the import list: textual, resolve by keeping both |
| `src/pmcp/types.py` | the constants after `MAX_FORWARDED_TASK_NUMBER`; the check functions; `McpTaskInfo.ttl` type; `TaskMetadataInput` bounds | `McpTaskRecord._connection_id` (PrivateAttr) | none expected; different classes |
| `CHANGELOG.md` | the #298 bounds lines; the "Known follow-up" bullet, replaced by the #330 entry | the same "Known follow-up" context, plus a new #338 entry after it | **textual conflict**: keep #298's rewritten bullet, then the #330 entry, then the #338 entry |
| `tests/test_task_numeric_bounds.py` | imports; the `test_task_hint_bounds` rows; `USABLE`; `test_forwarded_task_hints_are_spec_shaped`; the alias test | eviction and recording tests (483+, 508+, 539+, 671+, 891+, 924+, 941+, 977+) | low; the alias-test hunk is near #338's 891 hunk |

**Rev 2 additions to the overlap.** #338 rev 6 changes `tools/handlers.py`.
`invoke` and `tasks_result` take the task from new
`call_tool_with_task`/`get_task_result_with_task` replies instead of
`get_task_record`, and `tasks_list` always uses `McpTaskInfo(**task)` from
the listed dump. #330 does not edit `handlers.py`, but its closed-world test
(`TASK_MODEL_CONSTRUCTIONS`, Design decision 11) records the exact arguments
of every task-model construction there. So:
- if #338 lands first, the #330 executor re-derives the table on the rebased
  tree. The test prints the diff. Each new or changed entry must get a reason
  that it holds seconds, or the code must route through
  `_task_info_from_payload`. If `call_tool_with_task`'s reply type is a
  pydantic model in `pmcp.types` that embeds `McpTaskInfo`, it is picked up
  automatically;
- if #330 lands first, #338's executor runs `tests/test_task_units.py`, and
  the same test names each construction #338 added.

`tasks_list`'s `McpTaskInfo(**task)` entry is the same before and after #338
rev 6. Its provenance test (`list_tasks` appends only `record.model_dump()`)
matches #338's "records then dumps each at once".

This plan's patch is kept minimal so that the rebase stays mechanical. It
adds no new class, does not move `_record_task`, and makes no eviction
change.

### 10. Alias choice is made in seconds (rev 2, board F002)

#298's rule is "the first usable alias wins" (`pollInterval`, then
`poll_interval`). Rev 1 judged "usable" on the ms value. A camelCase value that
is usable in ms but divides to 0 s therefore won, and was then dropped as
unusable, hiding a usable snake_case value. `task_hint_is_usable` now judges a
duration by `_wire_duration_seconds`, which is what the converter returns. The
precedence is stated in the CHANGELOG and the contract: `pollInterval` if it
is usable after conversion, otherwise `poll_interval`. The snake_case alias is
milliseconds too.

### 11. Building a task model from downstream data: a by-name guard plus a per-path behavioural backstop (rev 2, board F003; narrowed in rev 3)

**What the guard does and does not catch (rev 3).** The guard catches a task
model constructed directly **by class name**. Python has other ways to build
or patch an object: `TypeAdapter(M)`, a classmethod reached through an
instance, `__dict__`, `setattr` and `functools.partial`. Round 2's mutants Y1,
Y2 and Y4 used three of these, and the guard did not see them. Rev 3 does not
try to enumerate those forms. The backstop is **behavioural**, in rule 4
below: every reply path of every task operation is driven with a spec-shaped
downstream in ms, and every task pmcp outputs or records must report seconds.
Any form of bypass on any path then shows up in the units.

The rev 1 guards see a duration crossing that names a wire key or a
`ttl=`/`poll_interval=` keyword. Anything that builds a model from a mapping
(`Model(**x)`, `Model.model_validate(x)`), or changes one without validation
(`obj.ttl = …`, `model_copy(update=…)`), names neither. The fix removes that
degree of freedom with three rules:

1. **Every construction is reviewed.**
   `test_every_task_model_construction_is_a_reviewed_one` (by class name
   only, rev 3) works in three steps:
   - It finds the task-carrying models from `pmcp.types` itself:
     `McpTaskInfo`, its subclasses, and every model whose field annotations
     mention one, transitively. Today that is `McpTaskInfo`, `McpTaskRecord`,
     `InvokeOutput`, `TasksListOutput`, `TasksGetOutput`, `TasksResultOutput`
     and `TasksCancelOutput`.
   - It then walks every call in `src/pmcp` that names one of them:
     `M(...)` or `M.attr(...)`.
   - It records the call's task-data arguments as source: positional
     arguments, `**` spreads, and `task`/`tasks`/`ttl`/`poll_interval`/`raw`.

   The result must equal `TASK_MODEL_CONSTRUCTIONS`, which gives each
   (file, function, model, form) its exact arguments and a reason. An output
   that only carries errors has no task data and is skipped; an
   `McpTaskInfo`/`McpTaskRecord` construction never is. The current entries
   are:

   | Site | Task-data args | Why it holds seconds |
   |---|---|---|
   | `manager._task_info_from_payload` `McpTaskInfo(...)` | `ttl=task_duration_from_wire(...)`, `poll_interval=task_duration_from_wire(...)`, `raw=payload` | **the** inbound converter |
   | `manager._record_task` `McpTaskRecord(...)` | `ttl=task_info.ttl`, `poll_interval=task_info.poll_interval`, `raw=task_info.raw` | copies a parsed model |
   | `manager.cancel_task` `McpTaskInfo(...)` (I3 fallback) | `raw=result` | no duration; `raw` verbatim by design |
   | `handlers.tasks_list` `McpTaskInfo(**task)` | `**task` | `task` is a `record.model_dump()` from `list_tasks`, pinned by `test_list_tasks_returns_only_record_dumps` |
   | `handlers._sanitize_task_for_output` `McpTaskInfo.model_validate(task_data)` | `task_data` | `task.model_dump(mode="json")` of a model, pinned by `test_output_sanitising_revalidates_only_a_model_dump` |
   | `handlers.invoke` `InvokeOutput` | `task=public_task` | sanitized record |
   | `handlers.tasks_list` `TasksListOutput` | `tasks=tasks` | sanitized records |
   | `handlers.tasks_get` `TasksGetOutput` | `task=self._sanitize_task_for_output(task)` | the record `get_task` returned |
   | `handlers.tasks_result` `TasksResultOutput` | `task=self._sanitize_task_for_output(task) if task is not None else None` | registry record |
   | `handlers.tasks_cancel` `TasksCancelOutput` | `task=task` | the record `cancel_task` returned |

2. **No mutation around validation.**
   `test_no_task_duration_is_assigned_or_copied_around_validation` forbids two
   things anywhere in `src/pmcp`: an attribute store to `.ttl` or
   `.poll_interval`, and any `model_copy(update=…)` or
   `model_construct(update=…)`. Pydantic validates only on construction, so
   either would put an unconverted value in a task model unchecked.

3. **The fallbacks are bound by behaviour too.**
   - `test_the_cancel_fallback_never_reports_a_wire_duration_as_seconds` is
     the seat's falsifier, verbatim in substance.
   - `test_a_get_reply_without_a_task_reports_no_task`: `get_task` raises,
     and `gateway.tasks_get` returns `ok: false` with no task.
   - `test_a_listed_task_without_a_record_is_still_reported_in_seconds`.

4. **The per-path behavioural backstop (rev 3).**
   `test_every_reply_path_reports_and_records_seconds` drives each row of
   `REPLY_PATHS` through the gateway tool for that operation, over a real
   `ClientManager`. The downstream answers in MCP 2025-11-25's shapes, in ms
   (`ttl: 300000`, `pollInterval: 2500`), and replies that carry no task also
   carry those stray ms fields. The test asserts that every task the gateway
   output contains, and every record in the registry afterwards, reports
   exactly the row's expected `(ttl, poll_interval)` with no
   `unusable_fields`. The rows:

   | Operation | Gateway tool | Reply shape(s) | Expected |
   |---|---|---|---|
   | `call_tool` | `invoke` | `CreateTaskResult` `{task}` | (300.0, 2.5) |
   | `get_task` | `tasks_get` | `GetTaskResult` (the Task at top level) | (300.0, 2.5) |
   | `get_task` | `tasks_get` | `{task}` (also accepted) | (300.0, 2.5) |
   | `list_tasks` | `tasks_list` | `ListTasksResult` `{tasks: [...]}` | (300.0, 2.5) |
   | `get_task_result` | `tasks_result` | `{task, result}` | (300.0, 2.5) |
   | `get_task_result` | `tasks_result` | **spec: a `CallToolResult` with no task** (+ stray ms), then `tasks/get` → `GetTaskResult` | (300.0, 2.5) |
   | `cancel_task` | `tasks_cancel` | `CancelTaskResult` (the Task at top level) | (300.0, 2.5) |
   | `cancel_task` | `tasks_cancel` | `{task}` | (300.0, 2.5) |
   | `cancel_task` | `tasks_cancel` | no task (+ stray ms): the fallback | (None, None) |

   `get_task`'s own no-task branch raises. It is bound by
   `test_a_get_reply_without_a_task_reports_no_task`.
   `test_the_reply_path_table_covers_every_task_operation` derives the
   operations from the code: the set of `ClientManager` methods that call
   `_task_info_from_payload` must equal the table's operations. A new
   operation therefore fails until it gets a row.
   `test_a_result_reply_without_a_task_never_records_wire_durations` is the
   round-2 seat's falsifier.

Mutants X1 (the cancel fallback spreads the reply), X2 (`get_task` falls back
to spreading the reply), X9 (`get_task` patches the record with
`model_copy(update=payload)`) and X10 (the `tasks_list` fallback merges `raw`)
are all killed, each by the by-name rule and by a behavioural test. Rev 3's
Y1 (`TypeAdapter`), Y2 (a classmethod through an instance) and Y4
(`__dict__.update`) are not seen by the by-name rule, and are killed by the
per-path backstop and the falsifier.

## Changes

| File | Change | Size |
|---|---|---|
| `src/pmcp/types.py` | Adds `MS_PER_SECOND` and `MAX_TASK_SECONDS`. `_usable_task_ttl` is renamed to `_usable_wire_task_ttl`. A new seconds-unit `_usable_task_ttl` is the model check. Adds `_WIRE_TASK_HINT_CHECKS`. Adds `_TASK_DURATIONS` and `_wire_duration_seconds` (the ms check, ÷ 1000, the seconds check). `task_hint_is_usable` judges a duration in seconds (rev 2). Adds `task_seconds_to_wire` and `task_duration_from_wire`. `McpTaskInfo.ttl` becomes `float \| None`. `TaskMetadataInput.ttl`/`poll_interval` get `le=MAX_TASK_SECONDS` and the new descriptions | +99 / −12 |
| `src/pmcp/client/manager.py` | 2 imports; O1/O2 through `task_seconds_to_wire`; I1/I2 through `task_duration_from_wire` | +10 / −4 |
| `tests/fixtures/gateway_tool_schemas.json` | regenerated: 2 maxima, 2 descriptions | +4 / −4 |
| `tests/test_task_units.py` | **new**; 77 cases (rev 1: 54, rev 2: 66) | +986 |
| `tests/test_task_numeric_bounds.py` | migrated to the new unit: bounds rows, `USABLE` rows, the outbound shape, the alias rows | +15 / −11 |
| `tests/test_client_manager.py` | 2 tests migrated: the fake downstream sends ms; the outbound assertion expects ms | +8 / −8 |
| `tests/test_phase6_tenant_code_mode.py` | the fake tenant returns spec ms (`300000`, `100`) | +2 / −2 |
| `README.md` | the units sentence in the tenant-runs paragraph | +7 / −1 |
| `specs/tenant-code-mode-host-contract.md` | a "Units" paragraph in the task lifecycle section, covering the snake_case alias and its precedence; a "Changed in Consiliency/pmcp#330" warning to tenant servers; the "forwards them when supplied" sentence restated; the `ttl`/`poll_interval` bullets in the metadata section | +35 / −4 |
| `CHANGELOG.md` | rev 3, against `b2884db`'s format: the "Upgrade notes" line "Task numbers are bounded" gives the seconds maximum, and a new upgrade note states the unit change; the #298 bounds restated ("at most 2^53−1 ms"); its "Known follow-up" replaced by a pointer; a new #330 entry under `[Unreleased]` → `### Changed`, with the caller warning, the tenant-server warning and the alias's unit and precedence | +62 / −10 |

`McpTaskInfo`'s output schema is not snapshotted (`grep -c "unusable_fields\|McpTaskInfo" tests/fixtures/*.json` → 0), so the `ttl` type change moves no fixture.

## Tests

`tests/test_task_units.py` (new, 77 cases; rev 2 added 12, rev 3 added 11). Every case maps to a derived site
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
| `test_the_alias_is_chosen_by_usability_in_seconds` (rev 2) | F002: a camelCase value that underflows or is 0 does not hide a usable snake_case one | 3 |
| `test_the_camel_case_alias_wins_when_both_are_usable` (rev 2) | the stated precedence | 1 |
| `test_every_task_model_construction_is_a_reviewed_one` (rev 2) | **structural**: F003, construction by class name (narrowed in rev 3) | 1 |
| `test_no_task_duration_is_assigned_or_copied_around_validation` (rev 2) | **structural**: no attribute store or `model_copy(update=…)` | 1 |
| `test_list_tasks_returns_only_record_dumps` (rev 2) | **structural**: provenance of `McpTaskInfo(**task)` in `tasks_list` | 1 |
| `test_output_sanitising_revalidates_only_a_model_dump` (rev 2) | **structural**: provenance of `model_validate(task_data)` | 1 |
| `test_the_cancel_fallback_never_reports_a_wire_duration_as_seconds` (rev 2) | I3, by behaviour (the seat's falsifier) | 1 |
| `test_a_get_reply_without_a_task_reports_no_task` (rev 2) | the `get_task` fallback, by behaviour | 1 |
| `test_a_listed_task_without_a_record_is_still_reported_in_seconds` (rev 2) | the `tasks_list` fallback, by behaviour | 1 |
| `test_every_reply_path_reports_and_records_seconds` (rev 3) | **the behavioural backstop**: every reply path of every task operation; every output and record in seconds | 9 |
| `test_the_reply_path_table_covers_every_task_operation` (rev 3) | **structural**: the table's operations equal the code's task-parsing methods | 1 |
| `test_a_result_reply_without_a_task_never_records_wire_durations` (rev 3) | the round-2 seat's falsifier: spec `tasks/result` with no task | 1 |
| `test_the_changelog_and_contract_state_the_units_for_tenant_servers` (rev 2) | F001: the CHANGELOG warns tenant servers and gives the alias's unit; the contract names the snake_case alias and drops the old wording | 1 |

The migrations in the three existing modules are listed under *Changes* and
appear in the patch.

## Verification

Run from a fresh worktree of `origin/main` on dev0 (a team host):

```bash
git -C ~/code/pmcp fetch origin
git -C ~/code/pmcp worktree add -b fix/330-task-units "$WORKTREE_ROOT/pmcp-330-fix" origin/main   # measured on b2884db
cd "$WORKTREE_ROOT/pmcp-330-fix"
uv sync --all-extras -p 3.10      # without --all-extras, `uv run` silently uses the system pytest
mkdir -p /var/tmp/pmcp-330-bt-$USER     # keep basetemps and logs off /mnt/workspace
BT=/var/tmp/pmcp-330-bt-$USER
```

Apply *Verbatim bodies* (one `git apply`). Then:

```bash
# 1. the new module (rev 3: 77 passed)
uv run pytest tests/test_task_units.py --cov-fail-under=0 -p no:cacheprovider --basetemp=$BT/u -q
# 2. the suites the change touches (rev 3: 1095 passed, 0 failed)
env -u npm_config_cache -u npm_config_store_dir uv run pytest tests/test_task_units.py \
  tests/test_task_numeric_bounds.py tests/test_gateway_tool_schemas.py tests/test_tools.py \
  tests/test_client_manager.py tests/test_phase6_tenant_code_mode.py tests/test_server.py \
  tests/test_phase4_e2e.py --cov-fail-under=0 -p no:cacheprovider --basetemp=$BT/t -q
# 3. the snapshot: regenerating it must leave the patched fixture byte-identical
before=$(sha256sum tests/fixtures/gateway_tool_schemas.json)
PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest tests/test_gateway_tool_schemas.py::test_advertised_schemas_match_snapshot \
  --cov-fail-under=0 -p no:cacheprovider --basetemp=$BT/s -q
[ "$before" = "$(sha256sum tests/fixtures/gateway_tool_schemas.json)" ] && echo SNAPSHOT-STABLE
# 4. CI gates the list above would otherwise miss
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src/pmcp/types.py src/pmcp/client/manager.py
python3 scripts/check_security_claims.py          # expect OK
# 5. the repro (expect ttl=300000 and ok=False only at t=300.000)
uv run python repro_330.py
# 6. the mutation table (expect 24 KILLED; each restore is sha256-verified by the script)
uv run python mutants330.py
# 7. the full suite: once, detached, with a notifying waiter (memory on dev0 is shared)
env -u npm_config_cache -u npm_config_store_dir nohup uv run pytest -q -p no:cacheprovider --basetemp=$BT/full \
  > $BT/full.log 2>&1 &
```

Step 3 compares the fixture's sha256 before and after regeneration. The
fixture is already patched, so any change means the schema and the snapshot
disagree. Rev 1 and rev 2 used `git diff --exit-code` here, which fails on any
patched tree. Their measurements compared against the patch directly, so
their results stand.

## Red on main

Rev 3 was measured on a fresh detached worktree of main `b2884db` under
`/var/tmp`. The rev 3 test modules were copied in. `MAX_TASK_SECONDS` does not
exist on main, so the import was shimmed: the import line was dropped and
`MAX_TASK_SECONDS = (2**53 - 1) // 1000` was defined in the module. Main's own
`CHANGELOG.md` and contract were used; they were never overwritten in rev 3's
tree. Result: **39 failed, 38 passed**.

| Test | Failed | Cause on main |
|---|---|---|
| `test_a_ttl_of_300_lasts_300_seconds_on_a_spec_downstream` | 1 | the downstream got `ttl: 300` (ms) |
| `test_outbound_ttl_and_poll_interval_are_sent_in_milliseconds` | 1 | `{"ttl": 300, "pollInterval": 2.5}` |
| `test_every_inbound_path_reports_seconds` | 2 | `ttl` 300000 / `poll_interval` 2500.0 reported as is |
| `test_a_listed_task_without_a_record_is_still_reported_in_seconds` | 1 | same, on the `McpTaskInfo(**task)` path |
| `test_a_usable_downstream_duration_is_reported_in_seconds` | 8 | ms kept as given, `int` type |
| `test_an_unusable_downstream_duration_is_named_not_converted[…5e-324]` | 1 | a value that rounds to 0 s is "usable" when nothing divides |
| `test_the_alias_is_chosen_by_usability_in_seconds`, `test_the_camel_case_alias_wins_when_both_are_usable` | 3 + 1 | values come back in ms |
| `test_caller_bounds_are_in_seconds` | 4 | 9,007,199,254,741 … 2^53 − 1 accepted at the gate and in the model |
| `test_the_largest_accepted_value_does_not_overflow_on_the_wire` | 1 | `ttl` forwarded unscaled |
| `test_the_model_checks_seconds[ttl-1.5-True]` | 1 | the model demands integer ms |
| `test_seconds_survive_every_revalidation_unchanged` | 1 | `ttl` 1500 stays 1500 |
| `test_every_task_model_construction_is_a_reviewed_one` | 1 | main's parser passes `ttl=payload.get('ttl')`, not the converter |
| `test_the_outbound_choke_point_converts…`, `test_the_inbound_choke_point_converts…`, `test_each_converter_has_exactly_one_caller` | 1 each | no converter exists |
| `test_the_changelog_and_contract_state_the_units_for_tenant_servers` | 1 | main's CHANGELOG has no #330 entry |
| `test_every_reply_path_reports_and_records_seconds` (rev 3) | 8 of 9 | every path with a task reports `ttl` 300000 / `poll_interval` 2500.0 |
| `test_a_result_reply_without_a_task_never_records_wire_durations` (rev 3) | 1 | the `tasks/get` refresh records ms |

These pass on main, by design:
- the two round-trip properties. Main's pass-through is the identity in both
  directions, so they pin **symmetry**: M1–M4, M9 and M16 show they fail when
  one direction converts and the other does not;
- the null/absent and non-numeric controls, which #330 must not change;
- the closed-world rules that main already satisfies. Main has the same two
  name-sites, no attribute stores, and the same list/sanitize provenance.
  M12–M14 and X1, X2, X9 and X10 prove that each can fail;
- the cancel- and get-fallback behaviours, which hold on main. That is the
  point: they bind the fallbacks against regressions (X1, X2). The same goes
  for the backstop's `cancel_task` no-task row (None, None), which passes on
  main;
- `test_the_reply_path_table_covers_every_task_operation`: main has the same
  five task-parsing methods.

The migrated existing modules (`test_task_numeric_bounds.py`,
`test_client_manager.py`, `test_phase6_tenant_code_mode.py`, unchanged from
rev 1, with the same shim) give **13 failed, 562 passed** on main. The
failures break down as:
- `test_task_hint_bounds`, 3: `MAX_TASK_SECONDS + 1` for both fields, and
  2^53 − 1 for `ttl`, are accepted on main;
- `USABLE`, 5: the ms values come back unconverted;
- `test_forwarded_task_hints_are_spec_shaped`, 1;
- the alias test, 2;
- the two `test_client_manager.py` tests.

## Mutation table

All 24 mutants were measured on the rev 3 spike (`b2884db` + this patch) with
`mutants330.py`, and again on the embedding-proof tree with the same counts.
Each run applies one string replacement, runs `tests/test_task_units.py`
(77 cases), restores the file from its saved bytes, and **asserts that the
file's sha256 equals the original's**. **All 24 are killed.** Afterwards the
tree's `git diff` sha256 was unchanged (`6d2347d2…`).

The additions by revision:
- rev 2: M17 (F002), the round-1 seat's X1 and X2 (F003), and X9 and X10;
- rev 3: the round-2 seat's Y1, Y2 and Y4. The by-name guard does not see
  them, and the per-path backstop and the falsifier kill each one.

Counts grew from rev 2 because the backstop sees more mutants.

| # | Rule | Mutant | Failed (measured) |
|---|---|---|---|
| M1 | outbound ttl unconverted | `payload["ttl"] = parsed.ttl` | 8: a_listed_task_without_a_record_is_still_reported_in_seconds, a_ttl_of_300_lasts_300_seconds_on_a_spec_downstream, every_inbound_path_reports_seconds, outbound_ttl_and_poll_interval_are_sent_in_milliseconds, the_largest_accepted_value_does_not_overflow_on_the_wire, the_outbound_choke_point_converts_every_duration_it_writes, ttl_round_trips_exactly |
| M2 | outbound pollInterval unconverted | `payload["pollInterval"] = parsed.poll_interval` | 4: outbound_ttl_and_poll_interval_are_sent_in_milliseconds, poll_interval_round_trips_to_within_rounding, the_largest_accepted_value_does_not_overflow_on_the_wire, the_outbound_choke_point_converts_every_duration_it_writes |
| M3 | inbound ttl unconverted | `ttl=payload.get("ttl")` | 21: a_listed_task_without_a_record_is_still_reported_in_seconds, a_result_reply_without_a_task_never_records_wire_durations, a_usable_downstream_duration_is_reported_in_seconds, an_unusable_downstream_duration_is_named_not_converted, every_inbound_path_reports_seconds, every_reply_path_reports_and_records_seconds, every_task_model_construction_is_a_reviewed_one, seconds_survive_every_revalidation_unchanged, the_inbound_choke_point_converts_every_duration_it_reads, ttl_round_trips_exactly |
| M4 | inbound pollInterval unconverted | `poll_interval=poll_interval` | 24: a_listed_task_without_a_record_is_still_reported_in_seconds, a_result_reply_without_a_task_never_records_wire_durations, a_usable_downstream_duration_is_reported_in_seconds, an_unusable_downstream_duration_is_named_not_converted, every_inbound_path_reports_seconds, every_reply_path_reports_and_records_seconds, every_task_model_construction_is_a_reviewed_one, poll_interval_round_trips_to_within_rounding, seconds_survive_every_revalidation_unchanged, the_alias_is_chosen_by_usability_in_seconds, the_camel_case_alias_wins_when_both_are_usable, the_inbound_choke_point_converts_every_duration_it_reads |
| M5 | inbound floor division (int seconds) | `(usable / MS_PER_SECOND)` → `(usable // MS_PER_SECOND)` | 22: a_listed_task_without_a_record_is_still_reported_in_seconds, a_result_reply_without_a_task_never_records_wire_durations, a_usable_downstream_duration_is_reported_in_seconds, every_inbound_path_reports_seconds, every_reply_path_reports_and_records_seconds, poll_interval_round_trips_to_within_rounding, seconds_survive_every_revalidation_unchanged, the_alias_is_chosen_by_usability_in_seconds, the_camel_case_alias_wins_when_both_are_usable |
| M6 | caller bound not restated (ms-sized) | `MAX_TASK_SECONDS = MAX_FORWARDED_TASK_NUMBER` | 3: caller_bounds_are_in_seconds, the_largest_accepted_value_does_not_overflow_on_the_wire |
| M7 | model checks ttl in ms (integer rule) | model table `"ttl": _usable_wire_task_ttl` | 4: a_usable_downstream_duration_is_reported_in_seconds, seconds_survive_every_revalidation_unchanged, the_model_checks_seconds |
| M8 | wire ttl checked in seconds (fractional ms ok) | wire table `"ttl": _usable_task_ttl` | 2: a_usable_downstream_duration_is_reported_in_seconds, an_unusable_downstream_duration_is_named_not_converted |
| M9 | sent null ttl made unusable | converter `None` → `_UNUSABLE` | 25: a_null_or_absent_ttl_stays_unlimited, a_usable_downstream_duration_is_reported_in_seconds, an_unusable_downstream_duration_is_named_not_converted, poll_interval_round_trips_to_within_rounding, the_alias_is_chosen_by_usability_in_seconds, ttl_round_trips_exactly |
| M10 | model ttl upper bound dropped | drop `<= _INT64_MAX / MS_PER_SECOND` | 1: the_model_checks_seconds |
| M11 | model ttl lower bound dropped | drop `0 <=` | 1: the_model_checks_seconds |
| M12 | inbound converted twice (record re-converts) | `_record_task` passes `ttl` through `task_duration_from_wire` again | 17: a_listed_task_without_a_record_is_still_reported_in_seconds, a_result_reply_without_a_task_never_records_wire_durations, each_converter_has_exactly_one_caller, every_inbound_path_reports_seconds, every_other_duration_assignment_copies_seconds_from_a_model, every_reply_path_reports_and_records_seconds, every_task_model_construction_is_a_reviewed_one, only_the_two_choke_points_name_a_task_duration_wire_key, seconds_survive_every_revalidation_unchanged |
| M13 | new bypassing inbound site (cancel fallback reads raw ttl) | `cancel_task` fallback adds `ttl=result.get("ttl")` | 5: every_other_duration_assignment_copies_seconds_from_a_model, every_reply_path_reports_and_records_seconds, every_task_model_construction_is_a_reviewed_one, only_the_two_choke_points_name_a_task_duration_wire_key, the_cancel_fallback_never_reports_a_wire_duration_as_seconds |
| M14 | new bypassing outbound site (requestor params carry ttl) | `_task_request_params` adds `"ttl": 300` to `params.task` | 1: only_the_two_choke_points_name_a_task_duration_wire_key |
| M15 | conversion factor wrong | `MS_PER_SECOND = 1024` | 28: a_listed_task_without_a_record_is_still_reported_in_seconds, a_result_reply_without_a_task_never_records_wire_durations, a_ttl_of_300_lasts_300_seconds_on_a_spec_downstream, a_usable_downstream_duration_is_reported_in_seconds, every_inbound_path_reports_seconds, every_reply_path_reports_and_records_seconds, outbound_ttl_and_poll_interval_are_sent_in_milliseconds, seconds_survive_every_revalidation_unchanged, the_alias_is_chosen_by_usability_in_seconds, the_camel_case_alias_wins_when_both_are_usable, the_largest_accepted_value_does_not_overflow_on_the_wire, the_model_checks_seconds |
| M16 | outbound converter rounds poll to int ms | `int(seconds * MS_PER_SECOND)` | 2: poll_interval_round_trips_to_within_rounding, the_largest_accepted_value_does_not_overflow_on_the_wire |
| M17 | alias judged in ms, before conversion (F002) | `task_hint_is_usable`: `if name in _TASK_DURATIONS:` → `if False:` | 2: the_alias_is_chosen_by_usability_in_seconds |
| X1 | cancel fallback spreads the downstream reply (F003) | cancel fallback → `McpTaskInfo.model_validate({**result, …})` | 3: every_reply_path_reports_and_records_seconds, every_task_model_construction_is_a_reviewed_one, the_cancel_fallback_never_reports_a_wire_duration_as_seconds |
| X2 | get_task falls back to spreading the reply (F003) | `get_task`: no task → `McpTaskInfo.model_validate({**payload, "task_id": task_id})` | 2: a_get_reply_without_a_task_reports_no_task, every_task_model_construction_is_a_reviewed_one |
| X9 | get_task patches the record from the reply, unvalidated (F003) | `get_task` returns `….model_copy(update=payload)` | 5: every_inbound_path_reports_seconds, every_reply_path_reports_and_records_seconds, no_task_duration_is_assigned_or_copied_around_validation |
| X10 | tasks_list fallback built from the wire payload in raw (F003) | `tasks_list`: `McpTaskInfo(**{**task, **task["raw"]})` | 2: a_listed_task_without_a_record_is_still_reported_in_seconds, every_task_model_construction_is_a_reviewed_one |
| Y1 | tasks/result no-task branch: TypeAdapter spreads the reply (round 2) | `get_task_result` no-task branch records `TypeAdapter(McpTaskInfo).validate_python({**result, "task_id": task_id})` instead of `get_task` | 2: a_result_reply_without_a_task_never_records_wire_durations, every_reply_path_reports_and_records_seconds |
| Y2 | tasks/result no-task branch: classmethod through an instance (round 2) | same branch records `record.model_validate({**record.model_dump(), **result})` | 2: a_result_reply_without_a_task_never_records_wire_durations, every_reply_path_reports_and_records_seconds |
| Y4 | tasks/result no-task branch: __dict__.update from the reply (round 2) | same branch: `rec = await self.get_task(...)`, then `rec.__dict__.update({k: v for k, v in result.items() if k in type(rec).model_fields})` | 2: a_result_reply_without_a_task_never_records_wire_durations, every_reply_path_reports_and_records_seconds |

The round-2 seat's Y3 (the `__dict__.update` placed in `get_task`) was
already killed by rev 2's inbound-path tests, so it is not repeated here.

The round-1 seat's X3 and X6 survive as equivalent mutants, and stay out of
the table:
- X3 (the alias picker uses the seconds-side table) is the intended behaviour
  since rev 2.
- X6 (`isinstance` in the model check) is equivalent because only converter
  floats or copied attributes reach the model.

```python
"""Apply one string replacement, run tests/test_task_units.py, restore from an
in-memory copy. Usage: uv run python mutants330.py (from the worktree root)."""
import hashlib, re, subprocess, sys
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
  "(usable / MS_PER_SECOND)","(usable // MS_PER_SECOND)"),
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
 ("M17","alias judged in ms, before conversion (F002)","src/pmcp/types.py",
  "    if name in _TASK_DURATIONS:\n","    if False:\n"),
 ("X1","cancel fallback spreads the downstream reply (F003)","src/pmcp/client/manager.py",
  "            task_info = McpTaskInfo(\n                task_id=task_id,\n                status=\"cancelled\",\n                updated_at=time.time(),\n                raw=result,\n            )",
  "            task_info = McpTaskInfo.model_validate(\n                {**result, \"task_id\": task_id, \"status\": \"cancelled\", \"raw\": result}\n            )"),
 ("X2","get_task falls back to spreading the reply (F003)","src/pmcp/client/manager.py",
  "        if task_info is None:\n            raise KeyError(f\"Task not found: {server_name}::{task_id}\")",
  "        if task_info is None:\n            task_info = McpTaskInfo.model_validate({**payload, \"task_id\": task_id})"),
 ("X9","get_task patches the record from the reply, unvalidated (F003)","src/pmcp/client/manager.py",
  "        return self._record_task(server_name, task_info)\n\n    async def get_task_result(",
  "        return self._record_task(server_name, task_info).model_copy(update=payload)\n\n    async def get_task_result("),
 ("X10","tasks_list fallback built from the wire payload in raw (F003)","src/pmcp/tools/handlers.py",
  "McpTaskInfo(**task)","McpTaskInfo(**{**task, **task[\"raw\"]})"),
 ("Y1","tasks/result no-task branch: TypeAdapter spreads the reply (round 2)","src/pmcp/client/manager.py",
  "        else:\n            await self.get_task(\n                server_name,\n                task_id,\n                requestor_context=requestor_context\n                or (record.requestor_context if record is not None else None),\n            )\n        return result",
  "        else:\n            self._record_task(server_name, __import__(\"pydantic\").TypeAdapter(McpTaskInfo).validate_python({**result, \"task_id\": task_id}))\n        return result"),
 ("Y2","tasks/result no-task branch: classmethod through an instance (round 2)","src/pmcp/client/manager.py",
  "        else:\n            await self.get_task(\n                server_name,\n                task_id,\n                requestor_context=requestor_context\n                or (record.requestor_context if record is not None else None),\n            )\n        return result",
  "        else:\n            self._record_task(server_name, record.model_validate({**record.model_dump(), **result}))\n        return result"),
 ("Y4","tasks/result no-task branch: __dict__.update from the reply (round 2)","src/pmcp/client/manager.py",
  "        else:\n            await self.get_task(\n                server_name,\n                task_id,\n                requestor_context=requestor_context\n                or (record.requestor_context if record is not None else None),\n            )\n        return result",
  "        else:\n            rec = await self.get_task(server_name, task_id)\n            rec.__dict__.update({k: v for k, v in result.items() if k in type(rec).model_fields})\n        return result"),
]
only = set(sys.argv[1:])
results = []
for mid, rule, path, old, new in M:
    if only and mid not in only: continue
    raw = open(path, "rb").read()
    digest = hashlib.sha256(raw).hexdigest()
    src = raw.decode()
    assert src.count(old) == 1, (mid, src.count(old))
    open(path, "w").write(src.replace(old, new))
    try:
        p = subprocess.run(["uv","run","pytest","tests/test_task_units.py","--cov-fail-under=0",
            "-p","no:cacheprovider",f"--basetemp=/var/tmp/pmcp-330-bt-viperjuice/mut-{mid}",
            "-q","--no-header","-rf"], capture_output=True, text=True)
    finally:
        open(path, "wb").write(raw)
        assert hashlib.sha256(open(path, "rb").read()).hexdigest() == digest, (mid, path)
    failed = sorted({re.sub(r"\[.*", "", l.split("::",1)[1].split(" ")[0]) for l in p.stdout.splitlines() if l.startswith("FAILED")})
    n = sum(1 for l in p.stdout.splitlines() if l.startswith("FAILED"))
    err = "ERROR" in p.stdout and n == 0
    print(f"{mid}|{rule}|{'KILLED' if n or err else 'SURVIVED'}|{n}|{', '.join(failed)}", flush=True)
```

## Embedding proof

Rev 3 was measured on 2026-10-04, on a **fresh** detached worktree of
`origin/main` at `b2884db`, at `/var/tmp/pmcp-330-r3-proof`. It is separate
from the spike (`/var/tmp/pmcp-330-r3-new`) and from the red-on-main tree.
Basetemps and logs went to `/var/tmp`.

1. **Extraction.** The patch was taken out of this file with
   `awk '/^````diff$/{f=1;next} /^````$/{f=0} f' plan.md`. Its sha256 is
   `6d2347d28f9939ea847dd85f48b81a269b560212d2e41854bb7ffa12cb3182ca`, the same as the spike's `git diff`, with
   `tests/test_task_units.py` added through `git add -N`.
2. **Apply.** `git apply --check`, then `git apply`, both clean on
   `b2884db`. The patch modified 9 files and created 1. After applying, the
   tree's `git diff` sha256 equals the patch's.
3. **The new module:** **77 passed**.
4. **The 8 touched modules:** **1095 passed**, 0 failed.
5. **Snapshot regeneration:** the fixture's sha256 was unchanged (`SNAPSHOT-STABLE`).
6. **CI gates:** `ruff check` and `ruff format --check` passed (178 files),
   `mypy` passed on both source files, and `check_security_claims.py` printed
   `OK` (131 cited node ids on `b2884db`).
7. **`repro_330.py`:** `ttl=300000` was sent, and `tasks_get` was ok until
   t = 299.999 s and failed at 300.000 s.
8. **`mutants330.py`:** **24/24 KILLED**, with the same per-mutant counts as on the
   spike. Each restore was sha-verified inside the script, and the tree's
   diff sha256 was unchanged afterwards.
9. **The full suite:**
   `env -u npm_config_cache -u npm_config_store_dir uv run pytest -q -p no:cacheprovider --basetemp=/var/tmp/…`
   gave **8655 passed, 3 skipped, 80 deselected, 0 failed**, in 565 s.
   The count is higher than rev 2's 8350 because `b2884db` adds the
   docs-audit tests and rev 3 adds 11 cases.
10. **Red on main** is in its own section above: 39 of the new module's 77
    cases fail on `b2884db`, and 13 of the migrated cases fail.

The spike, proof and main trees were then removed, so this branch carries only
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
- [ ] (rev 2) When both poll aliases are sent, the choice is made in seconds.
  Proven by `test_the_alias_is_chosen_by_usability_in_seconds`.
- [ ] (rev 2) Every construction of a task-carrying model by class name in
  `src/pmcp` is in `TASK_MODEL_CONSTRUCTIONS` with its exact task-data
  arguments. No attribute
  store or `model_copy(update=…)` touches a duration. Proven by the closed-world
  and provenance tests, and by the fallback behaviour tests.
- [ ] (rev 2) The CHANGELOG and the contract warn tenant servers built to the
  seconds contract, and give the snake_case alias's unit. Proven by
  `test_the_changelog_and_contract_state_the_units_for_tenant_servers`.
- [ ] (rev 3) Every reply path of every task operation reports and records
  seconds against a spec-shaped downstream in ms, and the path table covers
  every `ClientManager` task operation. Proven by
  `test_every_reply_path_reports_and_records_seconds`,
  `test_the_reply_path_table_covers_every_task_operation` and the seat's
  falsifier.
- [ ] All 24 mutants are killed by `tests/test_task_units.py`. Y1, Y2 and Y4
  are each killed by the per-path backstop.
- [ ] README, the tenant contract and the CHANGELOG say: seconds in pmcp, ms
  on the wire, the conversion in both directions, `raw` verbatim, the new
  maximum, the 1000× warning for callers who sent ms, and (rev 2) the warning
  for tenant servers.
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
  steps 1–4 of *Verification*, and re-derives `TASK_MODEL_CONSTRUCTIONS`
  (Design decision 9).

## Execution Policy

- execute: effort=low.
- reason: a contained change on the trust edge, in both directions. It touches
  2 source files (+109 / −16) through two choke points. Its one behaviour
  change is caller-visible, and the CHANGELOG states it.
- Re-run the mutation table, the 8-module run, ruff and mypy before requesting
  review.
- Get a cross-vendor panel CR before merge, as for every PR to main.

## Verbatim bodies

### How to apply

1. Save the patch below (between the ```` fences) as `330.patch`.
2. Run `git apply 330.patch` on `b2884db` (rev 3; rev 1 and rev 2 targeted
   `2adcd9a`). It changes 9 files and creates
   `tests/test_task_units.py`, including the regenerated snapshot fixture and
   the README, contract and CHANGELOG edits.
3. Save `repro_330.py` and `mutants330.py` from above at the worktree root.
   They are run, not committed.

### Patch — `src/pmcp/{types.py, client/manager.py}`, tests, fixture, `README.md`, `CHANGELOG.md`, `specs/tenant-code-mode-host-contract.md`

````diff
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 200e082..78016b3 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -57,12 +57,19 @@ Each is described in full in the section named at the end of the line.
   coercion (`1` for a boolean, `"5"` for an integer) on `invoke.task` is refused, and
   an explicit `null` for an optional argument is now accepted. Policy is judged before
   the schema, and gate rejections are recorded as `audit.rejection` events. *Changed*
-- **Task numbers are bounded.** `invoke.task.ttl` must be an integer from 1 to 2^53−1,
-  and `invoke.task.poll_interval` a finite number above 0 and at most 2^53−1. `NaN` and
+- **Task numbers are bounded.** `invoke.task.ttl` must be an integer from 1 to
+  9,007,199,254,740 (seconds), and `invoke.task.poll_interval` a finite number above 0
+  and at most 9,007,199,254,740. `NaN` and
   `±Infinity` are refused for every numeric argument, and a request carrying a value
   that is not strict JSON fails with `outbound frame is not strict JSON`. A downstream
   task field pmcp cannot use is reported as `null` and named in `unusable_fields`.
   *Changed*
+- **Task `ttl` and `poll_interval` are seconds in pmcp and milliseconds on the wire.**
+  pmcp now converts both ways, as MCP 2025-11-25 requires. If you sent milliseconds
+  to work around the old pass-through, your values are now 1000× too long. A tenant
+  server built to the old seconds contract now receives milliseconds and must return
+  `ttl`/`pollInterval`/`poll_interval` in milliseconds. A task's `ttl` is now a
+  fractional number of seconds. *Changed*
 - **Redaction removes more.** `sanitize_auth_diagnostic`, `PolicyManager.redact_secrets`
   and `process_output` now also replace vendor token shapes, JWTs, PEM private keys,
   high-entropy runs, URL userinfo and secret query values with `[REDACTED]`. Existing
@@ -707,12 +714,14 @@ Each is described in full in the section named at the end of the line.
 - **`tools/call` input-schema rejections are now recorded in the scoped-advisor audit, without argument values ([Consiliency/pmcp#296](https://github.com/Consiliency/pmcp/issues/296)).** A call the transport gate rejects used to return `Input validation error: …` before the audit was reached, so an operator saw no attempt at all. It is now written as a new `audit.rejection` event (not an `audit.invocation`: nothing was invoked, and a reader that correlates invocations to a run skips it) with the tool name (for the scoped-advisor tools; any other tool is recorded with `gateway_tool: null` and a `gateway_tool_digest`), `terminal_status: "invalid_arguments"`, `rejected_argument_path`, the failing location as a JSON array (a key the schema declares, an array index, or `null` for a key the caller chose, since that key can itself be a secret), and `rejected_argument_validator`, the failing JSON Schema keyword (`type`, `pattern`, `required`, …). The record never contains the validation message, the rejected value, correlation IDs, or any digest of the arguments. The capability stays `scoped_advisor_audit.v1`; readers that dispatch on `event` are unaffected. Policy is now judged **before** the schema: a call to a policy-blocked gateway tool is refused with "Gateway tool blocked by policy" and recorded `denied` whatever its arguments, instead of getting an `Input validation error` that described the blocked tool's schema. If the audit sink has failed, a malformed call now gets "Scoped advisor audit channel failed" like every other call, instead of its validation error. The response to a rejected call from an allowed tool is unchanged. An `audit.invocation` record now reads nothing the schema gate did not vouch for: a call refused by policy, or made to an unregistered name, is recorded `denied` with every argument-derived field (`run_correlation_id`, `seat_correlation_id`, `downstream_tool_id`, `evidence_label_digest`, `source_reference_hash`) `null`, a result digest that no longer covers the caller's tool name, and a `gateway_tool_digest` of the registered name (for an unregistered name, of nothing) — previously a correlation-shaped value or a public URL anywhere in such a call's arguments was copied or hashed into the audit. Every other invocation record reads only the top-level arguments the tool's schema declares, so a correlation-shaped key a tool does not declare (e.g. `run_correlation_id` on `gateway.describe`) is no longer recorded; `gateway.invoke` declares every field the record reads, so its records are unchanged.
 - **`gateway.invoke`'s `task.ttl` and `task.poll_interval` are bounded, and
   NaN/Infinity are refused at the gate (see [Consiliency/pmcp#298](https://github.com/Consiliency/pmcp/issues/298)).**
-  - `task.ttl` must be an integer from 1 to 2^53−1. Zero and negative values,
-    which were forwarded downstream unchanged, are now rejected with
-    `Input validation error: …`, and so is any value above 2^53−1.
+  - `task.ttl` must be an integer from 1 to 9,007,199,254,740 seconds, so that
+    it is at most 2^53−1 ms once [Consiliency/pmcp#330](https://github.com/Consiliency/pmcp/issues/330) converts it (next
+    entry). Zero and negative values, which were forwarded downstream unchanged,
+    are now rejected with `Input validation error: …`, and so is any value above
+    the maximum.
   - `task.poll_interval` must be a finite number greater than 0 and at most
-    2^53−1. Zero, negative values, `NaN`, `Infinity` and `-Infinity` are now
-    rejected; they were previously accepted and forwarded.
+    9,007,199,254,740. Zero, negative values, `NaN`, `Infinity` and `-Infinity`
+    are now rejected; they were previously accepted and forwarded.
   - The transport gate now treats `NaN` and `±Infinity` as non-numbers for
     every numeric argument. Both transports can deliver them, even though they
     are not JSON. Until [Consiliency/pmcp#297](https://github.com/Consiliency/pmcp/issues/297) lands, a rejection message may
@@ -764,9 +773,52 @@ Each is described in full in the section named at the end of the line.
     originates) that contains one is dropped and logged, never written. Before,
     stdio servers received a non-JSON `NaN` literal, and HTTP/SSE servers
     silently received `null`.
-  - **Known follow-up:** pmcp documents `ttl` and `poll_interval` in seconds,
-    but MCP defines both in milliseconds, and pmcp forwards them unchanged.
-    Tracked as [Consiliency/pmcp#330](https://github.com/Consiliency/pmcp/issues/330).
+  - pmcp documented `ttl` and `poll_interval` in seconds, but MCP defines both
+    in milliseconds, and pmcp forwarded them unchanged. The next entry
+    ([Consiliency/pmcp#330](https://github.com/Consiliency/pmcp/issues/330)) fixes that.
+- **Task `ttl` and `poll_interval` are now converted between pmcp's seconds and
+  MCP's milliseconds (see [Consiliency/pmcp#330](https://github.com/Consiliency/pmcp/issues/330)).** pmcp has always documented
+  `gateway.invoke`'s `task.ttl` and `task.poll_interval` in seconds. MCP
+  2025-11-25 defines `ttl` and `pollInterval` in milliseconds, and pmcp passed
+  the number through unchanged. So `task: {ttl: 300}`, meant as five minutes,
+  gave a spec-conforming server a 300 ms retention, and its task was gone
+  0.3 s later.
+  - **If you worked around this by sending milliseconds, your values are now
+    1000× too long.** `task: {ttl: 300000}` used to mean five minutes to a
+    spec-conforming server. It now asks for 300,000 seconds, about 3.5 days.
+    Send seconds instead: `ttl: 300`. The same applies to `poll_interval`.
+  - **If you run a tenant server built to pmcp's earlier tenant contract**,
+    which described `ttl` in seconds, it now receives milliseconds: a caller's
+    `ttl: 300` arrives as `ttl: 300000`. A server that reads that as seconds
+    keeps the task 1000× longer than asked. It must also return `ttl` and
+    `pollInterval` (or `poll_interval`) in milliseconds, or pmcp reports them
+    1000× too small: a returned `ttl: 300` is shown as `0.3` seconds. See
+    `specs/tenant-code-mode-host-contract.md`.
+  - Outbound: `task.ttl` is sent as `ttl` in milliseconds (seconds × 1000,
+    exact). `task.poll_interval` is sent as `pollInterval` × 1000. MCP's
+    `TaskMetadata` has no `pollInterval`, so a spec-conforming server ignores
+    it.
+  - Inbound: a downstream task's `ttl` and `pollInterval` are read in
+    milliseconds, and so is the snake_case `poll_interval` alias some servers
+    send. When both poll aliases are present, `pollInterval` wins if it is
+    usable after conversion to seconds; otherwise a usable `poll_interval`
+    does. pmcp reports and records them as `ttl` and `poll_interval`
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
 - **`pmcp config set-startup-policy add|remove|set --source project --apply` now carries your prior trust approval forward when it rewrites `.mcp.json`.** Setting the startup policy changes the file's bytes, and trust approval is content-keyed, so the edit used to silently invalidate your own `pmcp trust approve` of that file and the next startup refused it. When the pre-write bytes were approved, pmcp now re-records the approval for the exact bytes it writes — keyed on the opened descriptor's verified identity (the resolved key must name the same file the descriptor holds open), never re-approving a file that was not already approved, and never approving a substituted file. A target swapped or unlinked mid-operation is refused rather than mis-bound, and on POSIX a symlinked `.mcp.json` is refused up front. If re-recording ever fails because the trust store is unusable, the edit is still written and the failure is surfaced as a diagnostic rather than crashing (an unusable store also fails the approval check, so nothing is silently carried forward). See [Consiliency/pmcp#253](https://github.com/Consiliency/pmcp/issues/253).
 - **Every install spawn now logs the command it runs, at WARNING, before it
   runs.** `start_install`, the legacy `install_server` and `verify_installation`
diff --git a/README.md b/README.md
index f656342..be104a6 100644
--- a/README.md
+++ b/README.md
@@ -1549,7 +1549,13 @@ Tenant runs use the existing task broker. Submit long-running work with
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
index 7e56239..4d98bef 100644
--- a/specs/tenant-code-mode-host-contract.md
+++ b/specs/tenant-code-mode-host-contract.md
@@ -110,11 +110,38 @@ terminal records are retained, oldest pruned first. `gateway.list_pending` and
 tenant run/task IDs.
 
 The tenant server should treat `pollInterval` and `ttl` as hints and lifecycle
-metadata. PMCP forwards them when supplied and may surface returned values to
-clients, but PMCP does not persist task records past gateway process lifetime.
+metadata. PMCP converts a caller's values to milliseconds and forwards them
+when supplied, and surfaces returned values to clients in seconds (see *Units*
+below). PMCP does not persist task records past gateway process lifetime.
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
+  sent. `poll_interval` (snake_case), which PMCP also accepts, is in
+  milliseconds too; when both are sent, `pollInterval` is used if it is usable
+  after conversion, and `poll_interval` otherwise. A `ttl` must be an integer in [0, 2^63 − 1], or `null` for unlimited. A
+  `pollInterval` must be a finite number greater than 0. The value is then
+  divided by 1000, and PMCP reports and records it as `ttl` and `poll_interval`
+  in seconds. Both are numbers that may be fractional: `ttl: 1500` becomes
+  `1.5`. A `ttl` of `null` stays `null`, which means unlimited. A
+  `pollInterval` so small that it divides to 0 seconds is unusable.
+- The task's `raw` object, and any result PMCP relays as sent, keep the tenant
+  server's own values and units.
+
+**Changed in Consiliency/pmcp#330.** Earlier versions of this contract described
+`ttl` in seconds and PMCP passed the number through unchanged. A tenant server
+built to that text now receives milliseconds (a caller's `ttl: 300` arrives as
+`ttl: 300000`), and must return `ttl` and `pollInterval` in milliseconds:
+seconds it returns are reported 1000× too small (`ttl: 300` shows as `0.3`).
+
 ## Metadata Forwarding Contract
 
 PMCP can forward OpenTelemetry-style trace context through
@@ -125,8 +152,12 @@ documented. These values are strings only and are metadata, not authentication.
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
index a73acb3..a1fe888 100644
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
@@ -609,10 +633,62 @@ _TASK_HINT_CHECKS: dict[str, Any] = {
 }
 
 
+#: The checks a downstream value gets AS SENT, before any conversion: the
+#: durations are checked in the wire's milliseconds (Consiliency/pmcp#330).
+_WIRE_TASK_HINT_CHECKS: dict[str, Any] = {
+    **_TASK_HINT_CHECKS,
+    "ttl": _usable_wire_task_ttl,
+}
+
+
+#: Task fields MCP carries in milliseconds and pmcp holds in seconds.
+_TASK_DURATIONS = frozenset({"ttl", "poll_interval"})
+
+
+def _wire_duration_seconds(name: str, value: Any) -> Any:
+    """A downstream duration in milliseconds as seconds, or ``None`` (not sent)
+    or ``_UNUSABLE``. Checked twice: in milliseconds as sent (the
+    Consiliency/pmcp#298 rule), then in seconds after division, so a value
+    that underflows to 0 s is unusable here, not later (Consiliency/pmcp#330)."""
+    if value is None:
+        return None
+    usable = _WIRE_TASK_HINT_CHECKS[name](value)
+    if usable is _UNUSABLE:
+        return _UNUSABLE
+    return _TASK_HINT_CHECKS[name](usable / MS_PER_SECOND)
+
+
 def task_hint_is_usable(name: str, value: Any) -> bool:
-    """Whether ``value`` passes the check for task field ``name`` -- for the
-    downstream parser choosing among a field's wire aliases."""
-    return value is not None and _TASK_HINT_CHECKS[name](value) is not _UNUSABLE
+    """Whether wire value ``value`` passes the check for task field ``name`` --
+    for the downstream parser choosing among a field's wire aliases. A duration
+    is judged as pmcp will hold it, in seconds after conversion, so an alias
+    that underflows to 0 s does not hide a usable one (Consiliency/pmcp#330)."""
+    if value is None:
+        return False
+    if name in _TASK_DURATIONS:
+        seconds = _wire_duration_seconds(name, value)
+        return seconds is not None and seconds is not _UNUSABLE
+    return _WIRE_TASK_HINT_CHECKS[name](value) is not _UNUSABLE
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
+    ``ttl`` / ``pollInterval`` / ``poll_interval`` in milliseconds, checked in
+    milliseconds (the Consiliency/pmcp#298 rule), as float seconds.
+
+    ``None`` (not sent; for ``ttl`` also a sent ``null``, MCP's "unlimited")
+    stays ``None``. A value the wire check refuses, one that divides to a value
+    the seconds check refuses, and the parser's ``UNUSABLE_TASK_VALUE`` come
+    back as ``UNUSABLE_TASK_VALUE`` for the model to report as unusable."""
+    return _wire_duration_seconds(name, value)
 
 
 class McpTaskInfo(BaseModel):
@@ -631,7 +707,10 @@ class McpTaskInfo(BaseModel):
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
@@ -694,10 +773,14 @@ class TaskMetadataInput(GatewayArguments):
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
@@ -706,10 +789,14 @@ class TaskMetadataInput(GatewayArguments):
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
index e50ba2c..6299df3 100644
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
index 0000000..cea7ac4
--- /dev/null
+++ b/tests/test_task_units.py
@@ -0,0 +1,986 @@
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
+from pydantic import BaseModel, ValidationError
+
+from pmcp.client.manager import ClientManager, ManagedClient
+from pmcp.policy.policy import PolicyManager
+from pmcp.tools.handlers import GatewayTools, get_gateway_tool_definitions
+from pmcp.tools.schema import GATE_VALIDATOR
+import pmcp.types as pmcp_types
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
+# --- every reply path, by behaviour (the backstop) -------------------------------
+#
+# The structural guards below catch a task model built directly by class name.
+# They cannot enumerate every way Python can build or patch an object
+# (`TypeAdapter`, a classmethod through an instance, `__dict__`), so this table
+# is the backstop (#330 board round 2): every reply path of every task
+# operation, against a downstream that answers in the spec's shapes and
+# milliseconds -- and also echoes stray ms fields on replies that carry no task,
+# so a path that spreads a reply into a task model shows up in the units.
+
+_WIRE_TASK: dict[str, Any] = {
+    "taskId": "t1",
+    "status": "working",
+    "createdAt": "2026-10-04T00:00:00Z",
+    "lastUpdatedAt": "2026-10-04T00:00:00Z",
+    "ttl": 300_000,
+    "pollInterval": 2500,
+}
+_STRAY_MS = {"ttl": 300_000, "pollInterval": 2500}
+
+
+def _spec_reply(shape: str, status: str) -> dict[str, Any]:
+    task = {**_WIRE_TASK, "status": status}
+    if shape == "create":  # CreateTaskResult
+        return {"task": task}
+    if shape == "top":  # GetTaskResult / CancelTaskResult: the Task itself
+        return task
+    if shape == "wrapped":  # also accepted by pmcp
+        return {"task": task}
+    if shape == "list":  # ListTasksResult
+        return {"tasks": [task]}
+    if shape == "result-with-task":
+        return {"task": task, "result": {"content": []}}
+    if shape == "no-task":  # e.g. tasks/result's CallToolResult, plus stray ms
+        return {
+            "content": [],
+            "_meta": {"io.modelcontextprotocol/related-task": {"taskId": "t1"}},
+            **_STRAY_MS,
+        }
+    raise AssertionError(shape)
+
+
+# (manager operation, gateway tool, {method: reply shape}, expected durations)
+REPLY_PATHS: list[tuple[str, str, dict[str, str], tuple[Any, Any]]] = [
+    ("call_tool", "invoke", {"tools/call": "create"}, (300.0, 2.5)),
+    ("get_task", "tasks_get", {"tasks/get": "top"}, (300.0, 2.5)),
+    ("get_task", "tasks_get", {"tasks/get": "wrapped"}, (300.0, 2.5)),
+    ("list_tasks", "tasks_list", {"tasks/list": "list"}, (300.0, 2.5)),
+    (
+        "get_task_result",
+        "tasks_result",
+        {"tasks/result": "result-with-task"},
+        (300.0, 2.5),
+    ),
+    # the spec's shape: no task in the result, so pmcp asks tasks/get
+    (
+        "get_task_result",
+        "tasks_result",
+        {"tasks/result": "no-task", "tasks/get": "top"},
+        (300.0, 2.5),
+    ),
+    ("cancel_task", "tasks_cancel", {"tasks/cancel": "top"}, (300.0, 2.5)),
+    ("cancel_task", "tasks_cancel", {"tasks/cancel": "wrapped"}, (300.0, 2.5)),
+    # the fallback: no task in the reply, so pmcp records none of its fields
+    ("cancel_task", "tasks_cancel", {"tasks/cancel": "no-task"}, (None, None)),
+]
+
+
+@pytest.mark.parametrize(
+    ("operation", "tool", "shapes", "expected"),
+    REPLY_PATHS,
+    ids=[f"{op}-{'+'.join(sh.values())}" for op, _, sh, _ in REPLY_PATHS],
+)
+@pytest.mark.asyncio
+async def test_every_reply_path_reports_and_records_seconds(
+    operation: str, tool: str, shapes: dict[str, str], expected: tuple[Any, Any]
+) -> None:
+    manager = _manager()
+    status = {"tasks/cancel": "cancelled", "tasks/result": "completed"}
+
+    async def downstream(
+        managed: Any, method: str, params: dict[str, Any], **_: Any
+    ) -> Any:
+        assert method in shapes, method
+        return _spec_reply(shapes[method], status.get(method, "working"))
+
+    manager._send_request = downstream  # type: ignore[method-assign]
+    if operation != "call_tool":
+        manager._record_task(SERVER, McpTaskInfo(task_id="t1", status="working"))
+    gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
+    args: dict[str, Any] = (
+        {"tool_id": TOOL_ID, "task": {"ttl": 300}}
+        if tool == "invoke"
+        else {"server_name": SERVER}
+        if tool == "tasks_list"
+        else {"server_name": SERVER, "task_id": "t1"}
+    )
+    output = await getattr(gateway, tool)(args)
+    assert output.ok, output
+    returned = list(getattr(output, "tasks", None) or [])
+    if getattr(output, "task", None) is not None:
+        returned.append(output.task)
+    assert returned, output
+    seen = returned + manager.get_tracked_tasks()
+    for task in seen:
+        assert (task.ttl, task.poll_interval) == expected, (operation, shapes, task)
+        assert task.unusable_fields == [], task
+
+
+def test_the_reply_path_table_covers_every_task_operation() -> None:
+    """Derived from the code: every `ClientManager` method that turns a
+    downstream reply into a task (calls `_task_info_from_payload`) has a row in
+    `REPLY_PATHS`, and so does each one with a no-task fallback branch."""
+    funcs = dict(((p, f.name), f) for p, f in _functions())
+    parsers = {
+        name
+        for (path, name), func in funcs.items()
+        if path == "client/manager.py"
+        and name != "_task_info_from_payload"
+        and any(_called(n) == "_task_info_from_payload" for n in ast.walk(func))
+    }
+    assert parsers == {op for op, _, _, _ in REPLY_PATHS}, parsers
+    no_task_rows = {op for op, _, sh, _ in REPLY_PATHS if "no-task" in sh.values()}
+    assert no_task_rows == {"get_task_result", "cancel_task"}
+    # get_task's no-task branch raises: test_a_get_reply_without_a_task_reports_no_task
+
+
+@pytest.mark.asyncio
+async def test_a_result_reply_without_a_task_never_records_wire_durations() -> None:
+    """The round-2 seat's falsifier: the spec-shaped `tasks/result` (a
+    CallToolResult, no task, stray ms fields) refreshes the task through
+    `tasks/get` and records seconds."""
+    manager = _manager()
+
+    async def reply(managed: Any, method: str, params: dict[str, Any], **_: Any) -> Any:
+        if method == "tasks/get":
+            return {
+                "taskId": "t1",
+                "status": "completed",
+                "ttl": 300_000,
+                "pollInterval": 2500,
+            }
+        return {"content": [], "ttl": 300_000, "pollInterval": 2500}
+
+    manager._send_request = reply  # type: ignore[method-assign]
+    manager._record_task(SERVER, McpTaskInfo(task_id="t1", status="working"))
+    await manager.get_task_result(SERVER, "t1")
+    record = manager.get_task_record(SERVER, "t1")
+    assert record is not None
+    assert (record.ttl, record.poll_interval) == (300.0, 2.5), record
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
+    "payload",
+    [
+        {"pollInterval": 5e-324, "poll_interval": 2500},
+        {"poll_interval": 2500, "pollInterval": 5e-324},
+        {"pollInterval": 0, "poll_interval": 2500},
+    ],
+    ids=["camel-underflows", "snake-first-in-dict", "camel-zero"],
+)
+def test_the_alias_is_chosen_by_usability_in_seconds(payload: dict[str, Any]) -> None:
+    """`pollInterval` is preferred over `poll_interval`, but only when it is
+    usable AS SECONDS: a camelCase value that divides to 0 s must not hide a
+    usable snake_case one (#298: the first usable alias wins; #330 board F002).
+    The snake_case alias is milliseconds too."""
+    info = ClientManager()._task_info_from_payload({"taskId": "t", **payload})
+    assert info is not None
+    assert info.poll_interval == 2.5 and info.unusable_fields == [], info
+
+
+def test_the_camel_case_alias_wins_when_both_are_usable() -> None:
+    info = ClientManager()._task_info_from_payload(
+        {"taskId": "t", "poll_interval": 9000, "pollInterval": 2500}
+    )
+    assert info is not None and info.poll_interval == 2.5
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
+
+
+# --- structure: direct constructions of a task model, by class name ----------------
+#
+# The guards above see a duration that NAMES a wire key or a `ttl=`/
+# `poll_interval=` keyword. A task model built by spreading a downstream reply
+# (`McpTaskInfo.model_validate({**result, ...})`) names neither (#330 board
+# F003, mutants X1/X2). So every construction that names a task-carrying model
+# class is listed below with the reason its input is already in seconds, and a
+# new or changed one fails until it is reviewed. This catches construction BY
+# CLASS NAME only; `TypeAdapter`, a classmethod through an instance or
+# `__dict__` are not seen here (round 2, Y1/Y2/Y4). The per-path behavioural
+# table above is the backstop for those.
+
+
+def _task_carrying_models() -> set[str]:
+    models = {
+        name: obj
+        for name, obj in vars(pmcp_types).items()
+        if isinstance(obj, type) and issubclass(obj, BaseModel)
+    }
+    carrying = {n for n, m in models.items() if issubclass(m, McpTaskInfo)}
+    grew = True
+    while grew:
+        grew = False
+        for name, model in models.items():
+            if name not in carrying and any(
+                any(c in str(field.annotation) for c in carrying)
+                for field in model.model_fields.values()
+            ):
+                carrying.add(name)
+                grew = True
+    return carrying
+
+
+_TASK_DATA_KEYWORDS = {"task", "tasks", "ttl", "poll_interval", "raw", None}
+
+#: (file, function, model, how) -> (task-data arguments as source, why it is safe)
+TASK_MODEL_CONSTRUCTIONS: dict[tuple[str, str, str, str], tuple[str, str]] = {
+    ("client/manager.py", "_task_info_from_payload", "McpTaskInfo", "call"): (
+        "poll_interval=task_duration_from_wire('poll_interval', poll_interval); "
+        "raw=payload; ttl=task_duration_from_wire('ttl', payload.get('ttl'))",
+        "THE inbound converter",
+    ),
+    ("client/manager.py", "_record_task", "McpTaskRecord", "call"): (
+        "poll_interval=task_info.poll_interval; raw=task_info.raw; ttl=task_info.ttl",
+        "copies a parsed McpTaskInfo (seconds)",
+    ),
+    ("client/manager.py", "cancel_task", "McpTaskInfo", "call"): (
+        "raw=result",
+        "no duration: task_id, status, updated_at; `raw` is verbatim by design",
+    ),
+    ("tools/handlers.py", "tasks_list", "McpTaskInfo", "call"): (
+        "**task",
+        "`task` is a `record.model_dump()` from ClientManager.list_tasks "
+        "(pinned by test_list_tasks_returns_only_record_dumps)",
+    ),
+    (
+        "tools/handlers.py",
+        "_sanitize_task_for_output",
+        "McpTaskInfo",
+        "model_validate",
+    ): (
+        "task_data",
+        "`task_data` is `task.model_dump(mode='json')` of a model "
+        "(pinned by test_output_sanitising_revalidates_only_a_model_dump)",
+    ),
+    ("tools/handlers.py", "invoke", "InvokeOutput", "call"): (
+        "task=public_task",
+        "public_task is _sanitize_task_for_output(record) or None",
+    ),
+    ("tools/handlers.py", "tasks_list", "TasksListOutput", "call"): (
+        "tasks=tasks",
+        "a list of _sanitize_task_for_output results",
+    ),
+    ("tools/handlers.py", "tasks_get", "TasksGetOutput", "call"): (
+        "task=self._sanitize_task_for_output(task)",
+        "the record ClientManager.get_task returned",
+    ),
+    ("tools/handlers.py", "tasks_result", "TasksResultOutput", "call"): (
+        "task=self._sanitize_task_for_output(task) if task is not None else None",
+        "the registry record",
+    ),
+    ("tools/handlers.py", "tasks_cancel", "TasksCancelOutput", "call"): (
+        "task=task",
+        "the record (or None) ClientManager.cancel_task returned",
+    ),
+}
+
+
+def _task_model_constructions() -> dict[tuple[str, str, str, str], set[str]]:
+    carrying = _task_carrying_models()
+    found: dict[tuple[str, str, str, str], set[str]] = {}
+    for path, func in _functions():
+        for node in ast.walk(func):
+            if not isinstance(node, ast.Call):
+                continue
+            target = node.func
+            if isinstance(target, ast.Name) and target.id in carrying:
+                model, how = target.id, "call"
+            elif (
+                isinstance(target, ast.Attribute)
+                and isinstance(target.value, ast.Name)
+                and target.value.id in carrying
+            ):
+                model, how = target.value.id, target.attr
+            else:
+                continue
+            data = [ast.unparse(arg) for arg in node.args]
+            data += [
+                f"**{ast.unparse(kw.value)}"
+                if kw.arg is None
+                else f"{kw.arg}={ast.unparse(kw.value)}"
+                for kw in node.keywords
+                if kw.arg in _TASK_DATA_KEYWORDS
+            ]
+            if not data and model not in ("McpTaskInfo", "McpTaskRecord"):
+                continue  # an error-only output: carries no task
+            key = (path, func.name, model, how)
+            found.setdefault(key, set()).add(
+                "; ".join(sorted(d.replace('"', "'") for d in data))
+            )
+    return found
+
+
+def test_every_task_model_construction_is_a_reviewed_one() -> None:
+    """Every place in `src/pmcp` that builds a task-carrying model BY CLASS NAME
+    (`Model(...)`, `Model(**x)`, `Model.model_validate(x)`,
+    `Model.model_construct(...)`) with task data is listed, with exactly the
+    arguments listed. Other forms are caught by
+    `test_every_reply_path_reports_and_records_seconds`."""
+    found = _task_model_constructions()
+    expected = {key: {args} for key, (args, _) in TASK_MODEL_CONSTRUCTIONS.items()}
+    assert found == expected
+
+
+def test_no_task_duration_is_assigned_or_copied_around_validation() -> None:
+    """Pydantic validates on construction only: an attribute store or a
+    `model_copy(update=...)` would put an unconverted value in a task model
+    without any check. Neither exists in `src/pmcp`."""
+    for path, func in _functions():
+        for node in ast.walk(func):
+            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
+                assert node.attr not in ("ttl", "poll_interval"), (path, func.name)
+            if _called(node) in ("model_copy", "model_construct"):
+                assert not any(
+                    kw.arg == "update"
+                    for kw in node.keywords  # type: ignore[attr-defined]
+                ), (path, func.name, ast.unparse(node))
+
+
+def test_list_tasks_returns_only_record_dumps() -> None:
+    """The provenance the `McpTaskInfo(**task)` in `gateway.tasks_list` relies
+    on: every listed entry is the dump of a record `_record_task` just built."""
+    funcs = dict(((p, f.name), f) for p, f in _functions())
+    func = funcs[("client/manager.py", "list_tasks")]
+    records = {
+        node.targets[0].id
+        for node in ast.walk(func)
+        if isinstance(node, ast.Assign)
+        and isinstance(node.targets[0], ast.Name)
+        and _called(node.value) == "_record_task"
+    }
+    appended = [
+        node.args[0]
+        for node in ast.walk(func)
+        if isinstance(node, ast.Call)
+        and isinstance(node.func, ast.Attribute)
+        and node.func.attr == "append"
+    ]
+    assert appended and records
+    for arg in appended:
+        assert (
+            _called(arg) == "model_dump"
+            and isinstance(arg.func.value, ast.Name)  # type: ignore[attr-defined]
+            and arg.func.value.id in records  # type: ignore[attr-defined]
+        ), ast.unparse(arg)
+
+
+def test_output_sanitising_revalidates_only_a_model_dump() -> None:
+    funcs = dict(((p, f.name), f) for p, f in _functions())
+    func = funcs[("tools/handlers.py", "_sanitize_task_for_output")]
+    (source,) = [
+        node.value
+        for node in ast.walk(func)
+        if isinstance(node, ast.Assign)
+        and isinstance(node.targets[0], ast.Name)
+        and node.targets[0].id == "task_data"
+    ]
+    assert _called(source) == "model_dump", ast.unparse(source)
+
+
+@pytest.mark.asyncio
+async def test_the_cancel_fallback_never_reports_a_wire_duration_as_seconds() -> None:
+    """Site I3 bound behaviourally (#330 board F003, the seat's falsifier):
+    a cancel reply with no task in it is not read for durations."""
+    manager = _manager()
+
+    async def reply(managed: Any, method: str, params: dict[str, Any], **_: Any) -> Any:
+        return {"ttl": 300_000, "pollInterval": 2500}  # no taskId: the fallback
+
+    manager._send_request = reply  # type: ignore[method-assign]
+    manager._record_task(SERVER, McpTaskInfo(task_id="t1", status="working"))
+    ok, task, _ = await manager.cancel_task(SERVER, "t1")
+    assert ok and task is not None
+    assert task.ttl in (None, 300.0) and task.poll_interval in (None, 2.5), task
+
+
+@pytest.mark.asyncio
+async def test_a_get_reply_without_a_task_reports_no_task() -> None:
+    """`tasks/get` with no task in the reply is "not found", never a task
+    built from the reply's fields (#330 board F003, mutant X2)."""
+    manager = _manager()
+
+    async def reply(managed: Any, method: str, params: dict[str, Any], **_: Any) -> Any:
+        return {"ttl": 300_000, "pollInterval": 2500, "status": "working"}
+
+    manager._send_request = reply  # type: ignore[method-assign]
+    with pytest.raises(KeyError):
+        await manager.get_task(SERVER, "t1")
+    gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
+    got = await gateway.tasks_get({"server_name": SERVER, "task_id": "t1"})
+    assert not got.ok and got.task is None
+
+
+@pytest.mark.asyncio
+async def test_a_listed_task_without_a_record_is_still_reported_in_seconds(
+    monkeypatch: pytest.MonkeyPatch,
+) -> None:
+    """`gateway.tasks_list`'s `McpTaskInfo(**task)` path (no record found, e.g.
+    evicted): the listed dump is already in seconds."""
+    gateway, _ = _gateway()
+    await gateway.invoke({"tool_id": TOOL_ID, "task": {"ttl": 300}})
+    monkeypatch.setattr(gateway._client_manager, "get_task_record", lambda *a: None)
+    listed = await gateway.tasks_list({"server_name": SERVER})
+    assert listed.ok, listed.errors
+    (task,) = listed.tasks
+    _assert_seconds(task)
+
+
+# --- docs -------------------------------------------------------------------------
+
+ROOT = SRC.parent.parent
+
+
+def test_the_changelog_and_contract_state_the_units_for_tenant_servers() -> None:
+    """#330 board F001: the release note warns tenant servers built to the old
+    seconds contract, and both docs give the unit of the snake_case alias."""
+    text = (ROOT / "CHANGELOG.md").read_text()
+    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
+    start = unreleased.index("converted between pmcp's seconds")
+    entry = unreleased[start:].split("\n- **", 1)[0]
+    assert "tenant server" in entry.lower(), entry
+    assert "`poll_interval`" in entry and "milliseconds" in entry
+    contract = (ROOT / "specs" / "tenant-code-mode-host-contract.md").read_text()
+    assert "PMCP forwards them when supplied" not in contract
+    assert "`poll_interval` (snake_case)" in contract
````
