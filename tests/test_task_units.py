"""Task durations: seconds in pmcp, milliseconds on the MCP wire
(Consiliency/pmcp#330).

MCP 2025-11-25 carries `TaskMetadata.ttl`, `Task.ttl` and `Task.pollInterval`
in milliseconds. pmcp's own interface (`gateway.invoke`'s `task.ttl` and
`task.poll_interval`, and the `ttl`/`poll_interval` of every task it returns
or records) is in seconds. The conversion happens at one choke point per
direction:
- outbound: `_task_wire_metadata` -> `task_seconds_to_wire`;
- inbound: `_task_info_from_payload` -> `task_duration_from_wire`.

The Consiliency/pmcp#298 bounds apply on the side where the value is in the
unit they were written for: the caller's bound is restated in seconds so the
forwarded milliseconds stay within I-JSON, and a downstream value is checked in
milliseconds, as sent, before it is divided.
"""

from __future__ import annotations

import ast
import math
import random
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ValidationError

from pmcp.client.manager import ClientManager, ManagedClient
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools, get_gateway_tool_definitions
from pmcp.tools.schema import GATE_VALIDATOR
import pmcp.types as pmcp_types
from pmcp.types import (
    MAX_FORWARDED_TASK_NUMBER,
    MAX_TASK_SECONDS,
    McpTaskInfo,
    McpTaskRecord,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    RiskHint,
    ServerStatus,
    ServerStatusEnum,
    TaskMetadataInput,
    ToolInfo,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "pmcp"
SERVER = "spec"
TOOL_ID = f"{SERVER}::run"
INT64_MAX = 2**63 - 1


def _manager() -> ClientManager:
    manager = ClientManager()
    manager._tools[TOOL_ID] = ToolInfo(
        tool_id=TOOL_ID,
        server_name=SERVER,
        tool_name="run",
        description="d",
        short_description="d",
        input_schema={"type": "object"},
        tags=[],
        risk_hint=RiskHint.LOW,
        execution={"taskSupport": "optional"},
    )
    status = ServerStatus(
        name=SERVER,
        status=ServerStatusEnum.ONLINE,
        tool_count=1,
        server_capabilities={"tasks": {}},
        protocol_version="2025-11-25",
    )
    manager._servers[SERVER] = status
    manager._clients[SERVER] = ManagedClient(
        config=ResolvedServerConfig(
            name=SERVER,
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http", url="https://spec.example/mcp"
            ),
        ),
        is_remote=True,
        write_stream=MagicMock(),
        status=status,
    )
    return manager


class SpecDownstream:
    """A downstream that honours MCP 2025-11-25: `params.task.ttl` is the
    retention in MILLISECONDS from creation, and a task past it is gone. It
    reports `ttl` and `pollInterval` back in milliseconds, under the alias the
    test picks."""

    def __init__(self, clock: list[float], poll_key: str = "pollInterval") -> None:
        self.clock = clock
        self.poll_key = poll_key
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.tasks: dict[str, dict[str, Any]] = {}

    def _wire(self, task_id: str, status: str | None = None) -> dict[str, Any]:
        task = self.tasks[task_id]
        if status is not None:
            task["status"] = status
        return {
            "taskId": task_id,
            "status": task["status"],
            "createdAt": "2026-10-04T00:00:00Z",
            "lastUpdatedAt": "2026-10-04T00:00:00Z",
            "ttl": task["ttl"],
            self.poll_key: 2500,
        }

    def _live(self, task_id: str) -> None:
        task = self.tasks.get(task_id)
        if task is None or self.clock[0] - task["created"] >= task["ttl"] / 1000:
            self.tasks.pop(task_id, None)
            raise RuntimeError(f"Task not found: {task_id}")

    async def __call__(
        self, managed: Any, method: str, params: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        self.sent.append((method, params))
        if method == "tools/call":
            self.tasks["t1"] = {
                "created": self.clock[0],
                "ttl": params["task"]["ttl"],
                "status": "working",
            }
            return {"task": self._wire("t1")}
        if method == "tasks/list":
            for task_id in list(self.tasks):
                try:
                    self._live(task_id)
                except RuntimeError:
                    pass
            return {"tasks": [self._wire(task_id) for task_id in self.tasks]}
        self._live(params["taskId"])
        if method == "tasks/get":
            return {"task": self._wire(params["taskId"])}
        if method == "tasks/result":
            return {
                "task": self._wire(params["taskId"], "completed"),
                "result": {"ok": True},
            }
        if method == "tasks/cancel":
            return {"task": self._wire(params["taskId"], "cancelled")}
        raise AssertionError(method)


def _gateway(poll_key: str = "pollInterval") -> tuple[GatewayTools, SpecDownstream]:
    manager = _manager()
    clock = [0.0]
    downstream = SpecDownstream(clock, poll_key)
    manager._send_request = downstream  # type: ignore[method-assign]
    return GatewayTools(client_manager=manager, policy_manager=PolicyManager()), (
        downstream
    )


# --- the repro ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_ttl_of_300_lasts_300_seconds_on_a_spec_downstream() -> None:
    """The issue's repro: `task: {ttl: 300}` is five minutes in pmcp's docs.
    Before #330 the downstream got `ttl: 300` -- 300 ms -- and the task was
    gone after 0.3 s."""
    gateway, downstream = _gateway()
    invoked = await gateway.invoke({"tool_id": TOOL_ID, "task": {"ttl": 300}})
    assert invoked.ok and invoked.task is not None
    assert downstream.sent[0][1]["task"]["ttl"] == 300_000

    downstream.clock[0] = 0.5  # past 300 ms
    alive = await gateway.tasks_get({"server_name": SERVER, "task_id": "t1"})
    assert alive.ok, alive.errors
    downstream.clock[0] = 299.999
    alive = await gateway.tasks_get({"server_name": SERVER, "task_id": "t1"})
    assert alive.ok, alive.errors
    downstream.clock[0] = 300.0
    gone = await gateway.tasks_get({"server_name": SERVER, "task_id": "t1"})
    assert not gone.ok


# --- per boundary site ------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_ttl_and_poll_interval_are_sent_in_milliseconds() -> None:
    """Site O1 (`ttl`) and O2 (`pollInterval`), `_task_wire_metadata`: the only
    place task durations go downstream."""
    gateway, downstream = _gateway()
    await gateway.invoke(
        {"tool_id": TOOL_ID, "task": {"ttl": 300, "poll_interval": 2.5}}
    )
    ((method, params),) = downstream.sent
    assert method == "tools/call"
    assert params["task"] == {"ttl": 300_000, "pollInterval": 2500.0}
    assert type(params["task"]["ttl"]) is int


def _assert_seconds(task: Any) -> None:
    data = task if isinstance(task, dict) else task.model_dump(mode="json")
    assert data["ttl"] == 300.0 and data["poll_interval"] == 2.5, data
    assert data["unusable_fields"] == [], data
    # `raw` is what the downstream sent, in its own units.
    assert data["raw"]["ttl"] == 300_000, data


@pytest.mark.parametrize("poll_key", ["pollInterval", "poll_interval"])
@pytest.mark.asyncio
async def test_every_inbound_path_reports_seconds(poll_key: str) -> None:
    """Sites I1 (`ttl`) and I2 (`pollInterval`/`poll_interval`) are read in
    `_task_info_from_payload`, which each of the five downstream replies that
    carry a task goes through. Each path is checked in what the caller gets
    back AND in what pmcp records."""
    gateway, _ = _gateway(poll_key)
    manager = gateway._client_manager

    def recorded() -> McpTaskRecord:
        record = manager.get_task_record(SERVER, "t1")
        assert record is not None
        return record

    invoked = await gateway.invoke(  # tools/call
        {"tool_id": TOOL_ID, "task": {"ttl": 300}}
    )
    _assert_seconds(invoked.task)
    _assert_seconds(recorded())

    listed = await gateway.tasks_list({"server_name": SERVER})  # tasks/list
    assert listed.ok, listed.errors
    (task,) = listed.tasks
    _assert_seconds(task)
    _assert_seconds(recorded())

    got = await gateway.tasks_get({"server_name": SERVER, "task_id": "t1"})
    assert got.ok, got.errors
    _assert_seconds(got.task)
    _assert_seconds(recorded())

    result = await gateway.tasks_result({"server_name": SERVER, "task_id": "t1"})
    assert result.ok, result.errors
    _assert_seconds(result.task)
    _assert_seconds(recorded())

    manager._record_task(SERVER, McpTaskInfo(task_id="t1", status="working"))
    cancelled = await gateway.tasks_cancel({"server_name": SERVER, "task_id": "t1"})
    assert cancelled.ok
    _assert_seconds(cancelled.task)
    _assert_seconds(recorded())


# --- every reply path, by behaviour (the backstop) -------------------------------
#
# The structural guards below catch a task model built directly by class name.
# They cannot enumerate every way Python can build or patch an object
# (`TypeAdapter`, a classmethod through an instance, `__dict__`), so this table
# is the backstop (#330 board round 2): every reply path of every task
# operation, against a downstream that answers in the spec's shapes and
# milliseconds -- and also echoes stray ms fields on replies that carry no task,
# so a path that spreads a reply into a task model shows up in the units.

_WIRE_TASK: dict[str, Any] = {
    "taskId": "t1",
    "status": "working",
    "createdAt": "2026-10-04T00:00:00Z",
    "lastUpdatedAt": "2026-10-04T00:00:00Z",
    "ttl": 300_000,
    "pollInterval": 2500,
}
_STRAY_MS = {"ttl": 300_000, "pollInterval": 2500}


def _spec_reply(shape: str, status: str) -> dict[str, Any]:
    task = {**_WIRE_TASK, "status": status}
    if shape == "create":  # CreateTaskResult
        return {"task": task}
    if shape == "top":  # GetTaskResult / CancelTaskResult: the Task itself
        return task
    if shape == "wrapped":  # also accepted by pmcp
        return {"task": task}
    if shape == "list":  # ListTasksResult
        return {"tasks": [task]}
    if shape == "result-with-task":
        return {"task": task, "result": {"content": []}}
    if shape == "no-task":  # e.g. tasks/result's CallToolResult, plus stray ms
        return {
            "content": [],
            "_meta": {"io.modelcontextprotocol/related-task": {"taskId": "t1"}},
            **_STRAY_MS,
        }
    raise AssertionError(shape)


# (manager operation, gateway tool, {method: reply shape}, expected durations)
REPLY_PATHS: list[tuple[str, str, dict[str, str], tuple[Any, Any]]] = [
    ("call_tool", "invoke", {"tools/call": "create"}, (300.0, 2.5)),
    # the task at the top level of the reply, which call_tool also accepts
    # (board round 3 F001: invoke used to return task None and relay it in ms)
    ("call_tool", "invoke", {"tools/call": "top"}, (300.0, 2.5)),
    ("get_task", "tasks_get", {"tasks/get": "top"}, (300.0, 2.5)),
    ("get_task", "tasks_get", {"tasks/get": "wrapped"}, (300.0, 2.5)),
    ("list_tasks", "tasks_list", {"tasks/list": "list"}, (300.0, 2.5)),
    (
        "get_task_result",
        "tasks_result",
        {"tasks/result": "result-with-task"},
        (300.0, 2.5),
    ),
    # the spec's shape: no task in the result, so pmcp asks tasks/get
    (
        "get_task_result",
        "tasks_result",
        {"tasks/result": "no-task", "tasks/get": "top"},
        (300.0, 2.5),
    ),
    ("cancel_task", "tasks_cancel", {"tasks/cancel": "top"}, (300.0, 2.5)),
    ("cancel_task", "tasks_cancel", {"tasks/cancel": "wrapped"}, (300.0, 2.5)),
    # the fallback: no task in the reply, so pmcp records none of its fields
    ("cancel_task", "tasks_cancel", {"tasks/cancel": "no-task"}, (None, None)),
]


@pytest.mark.parametrize(
    ("operation", "tool", "shapes", "expected"),
    REPLY_PATHS,
    ids=[f"{op}-{'+'.join(sh.values())}" for op, _, sh, _ in REPLY_PATHS],
)
@pytest.mark.asyncio
async def test_every_reply_path_reports_and_records_seconds(
    operation: str, tool: str, shapes: dict[str, str], expected: tuple[Any, Any]
) -> None:
    manager = _manager()
    status = {"tasks/cancel": "cancelled", "tasks/result": "completed"}

    async def downstream(
        managed: Any, method: str, params: dict[str, Any], **_: Any
    ) -> Any:
        assert method in shapes, method
        return _spec_reply(shapes[method], status.get(method, "working"))

    manager._send_request = downstream  # type: ignore[method-assign]
    if operation != "call_tool":
        manager._record_task(SERVER, McpTaskInfo(task_id="t1", status="working"))
    gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    args: dict[str, Any] = (
        {"tool_id": TOOL_ID, "task": {"ttl": 300}}
        if tool == "invoke"
        else {"server_name": SERVER}
        if tool == "tasks_list"
        else {"server_name": SERVER, "task_id": "t1"}
    )
    output = await getattr(gateway, tool)(args)
    assert output.ok, output
    returned = list(getattr(output, "tasks", None) or [])
    if getattr(output, "task", None) is not None:
        returned.append(output.task)
    assert returned, output
    seen = returned + manager.get_tracked_tasks()
    for task in seen:
        assert (task.ttl, task.poll_interval) == expected, (operation, shapes, task)
        assert task.unusable_fields == [], task


def test_the_reply_path_table_covers_every_task_operation() -> None:
    """Derived from the code: every `ClientManager` method that turns a
    downstream reply into a task (calls `_task_info_from_payload`) has a row in
    `REPLY_PATHS`, and so does each one with a no-task fallback branch."""
    funcs = dict(((p, f.name), f) for p, f in _functions())
    parsers = {
        name
        for (path, name), func in funcs.items()
        if path == "client/manager.py"
        and name != "_task_info_from_payload"
        and any(_called(n) == "_task_info_from_payload" for n in ast.walk(func))
    }
    assert parsers == {op for op, _, _, _ in REPLY_PATHS}, parsers
    no_task_rows = {op for op, _, sh, _ in REPLY_PATHS if "no-task" in sh.values()}
    assert no_task_rows == {"get_task_result", "cancel_task"}
    # get_task's no-task branch raises: test_a_get_reply_without_a_task_reports_no_task


@pytest.mark.asyncio
async def test_a_result_reply_without_a_task_never_records_wire_durations() -> None:
    """The round-2 seat's falsifier: the spec-shaped `tasks/result` (a
    CallToolResult, no task, stray ms fields) refreshes the task through
    `tasks/get` and records seconds."""
    manager = _manager()

    async def reply(managed: Any, method: str, params: dict[str, Any], **_: Any) -> Any:
        if method == "tasks/get":
            return {
                "taskId": "t1",
                "status": "completed",
                "ttl": 300_000,
                "pollInterval": 2500,
            }
        return {"content": [], "ttl": 300_000, "pollInterval": 2500}

    manager._send_request = reply  # type: ignore[method-assign]
    manager._record_task(SERVER, McpTaskInfo(task_id="t1", status="working"))
    await manager.get_task_result(SERVER, "t1")
    record = manager.get_task_record(SERVER, "t1")
    assert record is not None
    assert (record.ttl, record.poll_interval) == (300.0, 2.5), record


@pytest.mark.asyncio
async def test_invoke_reports_a_top_level_task_in_seconds() -> None:
    """Board round 3 F001's falsifier: a `tools/call` reply whose task is at the
    top level is recorded in seconds by the manager, and `gateway.invoke`
    reports that same task -- not `task: None` with the reply relayed in ms as
    an unredacted `result`."""
    manager = _manager()

    async def reply(managed: Any, method: str, params: dict[str, Any], **_: Any) -> Any:
        return {
            "taskId": "t1",
            "status": "working",
            "ttl": 300_000,
            "pollInterval": 2500,
        }

    manager._send_request = reply  # type: ignore[method-assign]
    gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    out = await gateway.invoke({"tool_id": TOOL_ID, "task": {"ttl": 300}})
    record = manager.get_task_record(SERVER, "t1")
    assert record is not None and record.ttl == 300.0
    assert out.task is not None, out.result
    assert (out.task.ttl, out.task.poll_interval) == (300.0, 2.5)
    assert out.result is None


@pytest.mark.asyncio
async def test_a_call_not_run_as_a_task_never_reports_a_task() -> None:
    """The recogniser runs only when the call ran as a task, as the manager
    decides: a plain tool result that happens to carry `taskId` stays a result,
    even when an earlier task call left a record under that id."""
    manager = _manager()
    manager._record_task(SERVER, McpTaskInfo(task_id="t1", status="working"))

    async def reply(managed: Any, method: str, params: dict[str, Any], **_: Any) -> Any:
        assert "task" not in params
        return {"taskId": "t1", "content": [{"type": "text", "text": "hi"}]}

    manager._send_request = reply  # type: ignore[method-assign]
    gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    for args in (
        {"tool_id": TOOL_ID},
        {"tool_id": TOOL_ID, "task": {"enabled": False}},
    ):
        out = await gateway.invoke(args)
        assert out.ok and out.task is None and out.result is not None, out


# --- round trip -----------------------------------------------------------------


def _round_trip(**task: Any) -> McpTaskInfo:
    manager = ClientManager()
    wire = manager._task_wire_metadata(task)
    info = manager._task_info_from_payload({"taskId": "t", **wire})
    assert info is not None and info.unusable_fields == [], (task, wire, info)
    return info


def test_ttl_round_trips_exactly() -> None:
    """Integer seconds -> ms -> seconds is exact over the whole caller range."""
    rng = random.Random(330)
    samples = [1, 2, 59, 60, 300, 3600, 86_400, 10**9, MAX_TASK_SECONDS - 1]
    samples += [MAX_TASK_SECONDS]
    samples += [rng.randint(1, MAX_TASK_SECONDS) for _ in range(2000)]
    for seconds in samples:
        assert _round_trip(ttl=seconds).ttl == seconds


def test_poll_interval_round_trips_to_within_rounding() -> None:
    """Float seconds -> ms -> seconds: one multiply and one divide, each
    correctly rounded, so the result is within 2 ulp of what was sent."""
    rng = random.Random(330)
    samples = [5e-324, 1e-300, 0.001, 0.1, 0.25, 1.0, 2.5, 30.0, MAX_TASK_SECONDS]
    samples += [
        10 ** rng.uniform(-300, math.log10(MAX_TASK_SECONDS)) for _ in range(2000)
    ]
    for seconds in samples:
        back = _round_trip(poll_interval=seconds).poll_interval
        assert back is not None
        assert abs(back - seconds) <= 2 * math.ulp(seconds), (seconds, back)


# --- the bounds, in the unit of each side --------------------------------------


def _invoke_schema() -> dict[str, Any]:
    (tool,) = [t for t in get_gateway_tool_definitions() if t.name == "gateway.invoke"]
    return tool.input_schema


@pytest.mark.parametrize(
    ("field", "value", "accepted"),
    [
        ("ttl", 1, True),
        ("ttl", MAX_TASK_SECONDS, True),
        ("ttl", MAX_TASK_SECONDS + 1, False),
        ("ttl", MAX_FORWARDED_TASK_NUMBER, False),  # the old, millisecond-sized bound
        ("ttl", 0, False),
        ("poll_interval", 5e-324, True),
        ("poll_interval", float(MAX_TASK_SECONDS), True),
        ("poll_interval", MAX_TASK_SECONDS + 0.5, False),
        ("poll_interval", float(MAX_FORWARDED_TASK_NUMBER), False),
        ("poll_interval", 0, False),
    ],
)
def test_caller_bounds_are_in_seconds(field: str, value: Any, accepted: bool) -> None:
    """The #298 range, restated: the caller's bound is in seconds so that, times
    1000, it is still at most 2**53 - 1 ms. Gate and model agree."""
    args = {"tool_id": "a::b", "task": {field: value}}
    gate_ok = not list(GATE_VALIDATOR(_invoke_schema()).iter_errors(args))
    try:
        TaskMetadataInput.model_validate({field: value})
        model_ok = True
    except ValidationError:
        model_ok = False
    assert (gate_ok, model_ok) == (accepted, accepted)


def test_the_largest_accepted_value_does_not_overflow_on_the_wire() -> None:
    assert MAX_TASK_SECONDS == 9_007_199_254_740
    wire = ClientManager()._task_wire_metadata(
        {"ttl": MAX_TASK_SECONDS, "poll_interval": float(MAX_TASK_SECONDS)}
    )
    assert wire["ttl"] == 9_007_199_254_740_000 <= MAX_FORWARDED_TASK_NUMBER
    assert type(wire["ttl"]) is int
    assert wire["pollInterval"] == 9_007_199_254_740_000.0 <= MAX_FORWARDED_TASK_NUMBER
    # the smallest accepted poll interval does not underflow to 0 ms
    tiny = ClientManager()._task_wire_metadata({"poll_interval": 5e-324})
    assert tiny["pollInterval"] > 0


@pytest.mark.parametrize("value", ["300", True, [300], {"s": 300}, None])
def test_a_non_numeric_caller_duration_is_still_refused(value: Any) -> None:
    """Unchanged by #330: the gate refuses a string, a boolean or a container
    for either field (`null` is accepted as "not given")."""
    for field in ("ttl", "poll_interval"):
        args = {"tool_id": "a::b", "task": {field: value}}
        errors = list(GATE_VALIDATOR(_invoke_schema()).iter_errors(args))
        assert (errors == []) == (value is None), (field, value)


@pytest.mark.parametrize(
    ("wire", "attr", "value", "kept"),
    [
        ("ttl", "ttl", 0, 0.0),
        ("ttl", "ttl", 1, 0.001),
        ("ttl", "ttl", 1500, 1.5),
        ("ttl", "ttl", 300_000.0, 300.0),
        ("ttl", "ttl", INT64_MAX, INT64_MAX / 1000),
        ("pollInterval", "poll_interval", 1, 0.001),
        ("pollInterval", "poll_interval", 2.5, 0.0025),
        ("pollInterval", "poll_interval", 1e308, 1e305),
    ],
)
def test_a_usable_downstream_duration_is_reported_in_seconds(
    wire: str, attr: str, value: Any, kept: float
) -> None:
    info = ClientManager()._task_info_from_payload({"taskId": "t", wire: value})
    assert info is not None
    assert getattr(info, attr) == kept and type(getattr(info, attr)) is float
    assert info.unusable_fields == []


@pytest.mark.parametrize(
    ("wire", "attr", "value"),
    [
        # the #298 rule, applied to the value AS SENT, in milliseconds
        ("ttl", "ttl", 1.5),  # not an integer number of ms
        ("ttl", "ttl", -1),
        ("ttl", "ttl", INT64_MAX + 1),
        ("ttl", "ttl", "300000"),
        ("ttl", "ttl", True),
        ("ttl", "ttl", float("nan")),
        ("pollInterval", "poll_interval", 0),
        ("pollInterval", "poll_interval", "2500"),
        ("pollInterval", "poll_interval", float("inf")),
        ("pollInterval", "poll_interval", None),
        # usable in ms, but 5e-324 / 1000 underflows to 0 s: unusable
        ("pollInterval", "poll_interval", 5e-324),
    ],
)
def test_an_unusable_downstream_duration_is_named_not_converted(
    wire: str, attr: str, value: Any
) -> None:
    info = ClientManager()._task_info_from_payload({"taskId": "t", wire: value})
    assert info is not None
    assert getattr(info, attr) is None and info.unusable_fields == [attr]


def test_a_null_or_absent_ttl_stays_unlimited() -> None:
    manager = ClientManager()
    for payload in ({"taskId": "t", "ttl": None}, {"taskId": "t"}):
        info = manager._task_info_from_payload(payload)
        assert info is not None and info.ttl is None and info.unusable_fields == []


@pytest.mark.parametrize(
    "payload",
    [
        {"pollInterval": 5e-324, "poll_interval": 2500},
        {"poll_interval": 2500, "pollInterval": 5e-324},
        {"pollInterval": 0, "poll_interval": 2500},
    ],
    ids=["camel-underflows", "snake-first-in-dict", "camel-zero"],
)
def test_the_alias_is_chosen_by_usability_in_seconds(payload: dict[str, Any]) -> None:
    """`pollInterval` is preferred over `poll_interval`, but only when it is
    usable AS SECONDS: a camelCase value that divides to 0 s must not hide a
    usable snake_case one (#298: the first usable alias wins; #330 board F002).
    The snake_case alias is milliseconds too."""
    info = ClientManager()._task_info_from_payload({"taskId": "t", **payload})
    assert info is not None
    assert info.poll_interval == 2.5 and info.unusable_fields == [], info


def test_the_camel_case_alias_wins_when_both_are_usable() -> None:
    info = ClientManager()._task_info_from_payload(
        {"taskId": "t", "poll_interval": 9000, "pollInterval": 2500}
    )
    assert info is not None and info.poll_interval == 2.5


@pytest.mark.parametrize(
    ("attr", "value", "usable"),
    [
        ("ttl", 1.5, True),
        ("ttl", INT64_MAX / 1000, True),
        ("ttl", 1e300, False),
        ("ttl", -1.0, False),
        ("poll_interval", 0.0025, True),
        ("poll_interval", 0.0, False),
    ],
)
def test_the_model_checks_seconds(attr: str, value: float, usable: bool) -> None:
    """`McpTaskInfo` holds seconds and is re-validated on every record, list
    and output: its own check is in seconds, the wire check in ms."""
    info = McpTaskInfo(task_id="t", **{attr: value})
    assert (getattr(info, attr) == value) is usable
    assert info.unusable_fields == ([] if usable else [attr])


def test_seconds_survive_every_revalidation_unchanged() -> None:
    """A converted task is validated again by `_record_task`, by
    `gateway.tasks_list` (`McpTaskInfo(**record)`) and by
    `_sanitize_task_for_output` (JSON dump, then validate). None of those may
    convert again or re-apply the millisecond check."""
    manager = ClientManager()
    info = manager._task_info_from_payload(
        {"taskId": "t", "ttl": 1500, "pollInterval": 250}
    )
    assert info is not None and (info.ttl, info.poll_interval) == (1.5, 0.25)
    record = manager._record_task("s", info)
    again = McpTaskInfo(
        **{
            k: v
            for k, v in record.model_dump().items()
            if k in McpTaskInfo.model_fields
        }
    )
    output = GatewayTools(
        client_manager=manager, policy_manager=PolicyManager()
    )._sanitize_task_for_output(again)
    for task in (record, again, output):
        assert (task.ttl, task.poll_interval) == (1.5, 0.25), task
        assert task.unusable_fields == []


# --- structure: every boundary site goes through the converter ---------------------

_WIRE_KEYS = {"ttl", "pollInterval", "poll_interval"}
OUTBOUND = ("client/manager.py", "_task_wire_metadata")
INBOUND = ("client/manager.py", "_task_info_from_payload")


def _functions() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                found.append((str(path.relative_to(SRC)), node))
    return found


def _called(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return None


def test_only_the_two_choke_points_name_a_task_duration_wire_key() -> None:
    """A task duration crosses the downstream boundary only where its wire key
    is named. Derived from the code: every function in `src/pmcp` that names
    `ttl`, `pollInterval` or `poll_interval` as a string is one of the two
    choke points. A new site (a second payload reader or writer) fails here
    until it routes through the converter and is added."""
    found = {
        (path, func.name)
        for path, func in _functions()
        for node in ast.walk(func)
        if isinstance(node, ast.Constant) and node.value in _WIRE_KEYS
    }
    assert found == {OUTBOUND, INBOUND}, found


def test_the_outbound_choke_point_converts_every_duration_it_writes() -> None:
    funcs = dict(((p, f.name), f) for p, f in _functions())
    writes = [
        node
        for node in ast.walk(funcs[OUTBOUND])
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Subscript)
        and isinstance(node.targets[0].slice, ast.Constant)
        and node.targets[0].slice.value in _WIRE_KEYS
    ]
    assert {w.targets[0].slice.value for w in writes} == {"ttl", "pollInterval"}  # type: ignore[attr-defined]
    for write in writes:
        assert _called(write.value) == "task_seconds_to_wire", ast.unparse(write)


def test_the_inbound_choke_point_converts_every_duration_it_reads() -> None:
    funcs = dict(((p, f.name), f) for p, f in _functions())
    (build,) = [
        node for node in ast.walk(funcs[INBOUND]) if _called(node) == "McpTaskInfo"
    ]
    durations = {
        kw.arg: kw.value
        for kw in build.keywords  # type: ignore[attr-defined]
        if kw.arg in ("ttl", "poll_interval")
    }
    assert set(durations) == {"ttl", "poll_interval"}
    for value in durations.values():
        assert _called(value) == "task_duration_from_wire", ast.unparse(value)


def test_every_other_duration_assignment_copies_seconds_from_a_model() -> None:
    """Outside the inbound choke point, a `ttl=`/`poll_interval=` keyword in
    `src/pmcp` only copies an already-converted model attribute (seconds to
    seconds), so nothing else can feed a raw wire value into a task model."""
    for path, func in _functions():
        if (path, func.name) == INBOUND:
            continue
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg in ("ttl", "poll_interval"):
                    assert (
                        isinstance(kw.value, ast.Attribute) and kw.value.attr == kw.arg
                    ), (path, func.name, ast.unparse(kw.value))


def test_each_converter_has_exactly_one_caller() -> None:
    callers: dict[str, set[tuple[str, str]]] = {
        "task_seconds_to_wire": set(),
        "task_duration_from_wire": set(),
    }
    for path, func in _functions():
        for node in ast.walk(func):
            name = _called(node)
            if name in callers:
                callers[name].add((path, func.name))
    assert callers == {
        "task_seconds_to_wire": {OUTBOUND},
        "task_duration_from_wire": {INBOUND},
    }


# --- structure: direct constructions of a task model, by class name ----------------
#
# The guards above see a duration that NAMES a wire key or a `ttl=`/
# `poll_interval=` keyword. A task model built by spreading a downstream reply
# (`McpTaskInfo.model_validate({**result, ...})`) names neither (#330 board
# F003, mutants X1/X2). So every construction that names a task-carrying model
# class is listed below with the reason its input is already in seconds, and a
# new or changed one fails until it is reviewed. This catches construction BY
# CLASS NAME only; `TypeAdapter`, a classmethod through an instance or
# `__dict__` are not seen here (round 2, Y1/Y2/Y4). The per-path behavioural
# table above is the backstop for those.


def _task_carrying_models() -> set[str]:
    models = {
        name: obj
        for name, obj in vars(pmcp_types).items()
        if isinstance(obj, type) and issubclass(obj, BaseModel)
    }
    carrying = {n for n, m in models.items() if issubclass(m, McpTaskInfo)}
    grew = True
    while grew:
        grew = False
        for name, model in models.items():
            if name not in carrying and any(
                any(c in str(field.annotation) for c in carrying)
                for field in model.model_fields.values()
            ):
                carrying.add(name)
                grew = True
    return carrying


_TASK_DATA_KEYWORDS = {"task", "tasks", "ttl", "poll_interval", "raw", None}

#: (file, function, model, how) -> (task-data arguments as source, why it is safe)
TASK_MODEL_CONSTRUCTIONS: dict[tuple[str, str, str, str], tuple[str, str]] = {
    ("client/manager.py", "_task_info_from_payload", "McpTaskInfo", "call"): (
        "poll_interval=task_duration_from_wire('poll_interval', poll_interval); "
        "raw=payload; ttl=task_duration_from_wire('ttl', payload.get('ttl'))",
        "THE inbound converter",
    ),
    ("client/manager.py", "_record_task", "McpTaskRecord", "call"): (
        "poll_interval=task_info.poll_interval; raw=task_info.raw; ttl=task_info.ttl",
        "copies a parsed McpTaskInfo (seconds)",
    ),
    ("client/manager.py", "cancel_task", "McpTaskInfo", "call"): (
        "raw=result",
        "no duration: task_id, status, updated_at; `raw` is verbatim by design",
    ),
    ("tools/handlers.py", "tasks_list", "McpTaskInfo", "call"): (
        "**task",
        "`task` is a `record.model_dump()` from ClientManager.list_tasks "
        "(pinned by test_list_tasks_returns_only_record_dumps)",
    ),
    (
        "tools/handlers.py",
        "_sanitize_task_for_output",
        "McpTaskInfo",
        "model_validate",
    ): (
        "task_data",
        "`task_data` is `task.model_dump(mode='json')` of a model "
        "(pinned by test_output_sanitising_revalidates_only_a_model_dump)",
    ),
    ("tools/handlers.py", "invoke", "InvokeOutput", "call"): (
        "task=public_task",
        "public_task is _sanitize_task_for_output(record) or None",
    ),
    ("tools/handlers.py", "tasks_list", "TasksListOutput", "call"): (
        "tasks=tasks",
        "a list of _sanitize_task_for_output results",
    ),
    ("tools/handlers.py", "tasks_get", "TasksGetOutput", "call"): (
        "task=self._sanitize_task_for_output(task)",
        "the record ClientManager.get_task returned",
    ),
    ("tools/handlers.py", "tasks_result", "TasksResultOutput", "call"): (
        "task=self._sanitize_task_for_output(task) if task is not None else None",
        "the registry record",
    ),
    ("tools/handlers.py", "tasks_cancel", "TasksCancelOutput", "call"): (
        "task=task",
        "the record (or None) ClientManager.cancel_task returned",
    ),
}


def _task_model_constructions() -> dict[tuple[str, str, str, str], set[str]]:
    carrying = _task_carrying_models()
    found: dict[tuple[str, str, str, str], set[str]] = {}
    for path, func in _functions():
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            if isinstance(target, ast.Name) and target.id in carrying:
                model, how = target.id, "call"
            elif (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id in carrying
            ):
                model, how = target.value.id, target.attr
            else:
                continue
            data = [ast.unparse(arg) for arg in node.args]
            data += [
                f"**{ast.unparse(kw.value)}"
                if kw.arg is None
                else f"{kw.arg}={ast.unparse(kw.value)}"
                for kw in node.keywords
                if kw.arg in _TASK_DATA_KEYWORDS
            ]
            if not data and model not in ("McpTaskInfo", "McpTaskRecord"):
                continue  # an error-only output: carries no task
            key = (path, func.name, model, how)
            found.setdefault(key, set()).add(
                "; ".join(sorted(d.replace('"', "'") for d in data))
            )
    return found


def test_every_task_model_construction_is_a_reviewed_one() -> None:
    """Every place in `src/pmcp` that builds a task-carrying model BY CLASS NAME
    (`Model(...)`, `Model(**x)`, `Model.model_validate(x)`,
    `Model.model_construct(...)`) with task data is listed, with exactly the
    arguments listed. Other forms are caught by
    `test_every_reply_path_reports_and_records_seconds`."""
    found = _task_model_constructions()
    expected = {key: {args} for key, (args, _) in TASK_MODEL_CONSTRUCTIONS.items()}
    assert found == expected


def test_no_task_duration_is_assigned_or_copied_around_validation() -> None:
    """Pydantic validates on construction only: an attribute store or a
    `model_copy(update=...)` would put an unconverted value in a task model
    without any check. Neither exists in `src/pmcp`."""
    for path, func in _functions():
        for node in ast.walk(func):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                assert node.attr not in ("ttl", "poll_interval"), (path, func.name)
            if _called(node) in ("model_copy", "model_construct"):
                assert not any(
                    kw.arg == "update"
                    for kw in node.keywords  # type: ignore[attr-defined]
                ), (path, func.name, ast.unparse(node))


def test_list_tasks_returns_only_record_dumps() -> None:
    """The provenance the `McpTaskInfo(**task)` in `gateway.tasks_list` relies
    on: every listed entry is the dump of a record `_record_task` just built."""
    funcs = dict(((p, f.name), f) for p, f in _functions())
    func = funcs[("client/manager.py", "list_tasks")]
    records = {
        node.targets[0].id
        for node in ast.walk(func)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and _called(node.value) == "_record_task"
    }
    appended = [
        node.args[0]
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
    ]
    assert appended and records
    for arg in appended:
        assert (
            _called(arg) == "model_dump"
            and isinstance(arg.func.value, ast.Name)  # type: ignore[attr-defined]
            and arg.func.value.id in records  # type: ignore[attr-defined]
        ), ast.unparse(arg)


def test_output_sanitising_revalidates_only_a_model_dump() -> None:
    funcs = dict(((p, f.name), f) for p, f in _functions())
    func = funcs[("tools/handlers.py", "_sanitize_task_for_output")]
    (source,) = [
        node.value
        for node in ast.walk(func)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "task_data"
    ]
    assert _called(source) == "model_dump", ast.unparse(source)


@pytest.mark.asyncio
async def test_the_cancel_fallback_never_reports_a_wire_duration_as_seconds() -> None:
    """Site I3 bound behaviourally (#330 board F003, the seat's falsifier):
    a cancel reply with no task in it is not read for durations."""
    manager = _manager()

    async def reply(managed: Any, method: str, params: dict[str, Any], **_: Any) -> Any:
        return {"ttl": 300_000, "pollInterval": 2500}  # no taskId: the fallback

    manager._send_request = reply  # type: ignore[method-assign]
    manager._record_task(SERVER, McpTaskInfo(task_id="t1", status="working"))
    ok, task, _ = await manager.cancel_task(SERVER, "t1")
    assert ok and task is not None
    assert task.ttl in (None, 300.0) and task.poll_interval in (None, 2.5), task


@pytest.mark.asyncio
async def test_a_get_reply_without_a_task_reports_no_task() -> None:
    """`tasks/get` with no task in the reply is "not found", never a task
    built from the reply's fields (#330 board F003, mutant X2)."""
    manager = _manager()

    async def reply(managed: Any, method: str, params: dict[str, Any], **_: Any) -> Any:
        return {"ttl": 300_000, "pollInterval": 2500, "status": "working"}

    manager._send_request = reply  # type: ignore[method-assign]
    with pytest.raises(KeyError):
        await manager.get_task(SERVER, "t1")
    gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    got = await gateway.tasks_get({"server_name": SERVER, "task_id": "t1"})
    assert not got.ok and got.task is None


@pytest.mark.asyncio
async def test_a_listed_task_without_a_record_is_still_reported_in_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gateway.tasks_list`'s `McpTaskInfo(**task)` path (no record found, e.g.
    evicted): the listed dump is already in seconds."""
    gateway, _ = _gateway()
    await gateway.invoke({"tool_id": TOOL_ID, "task": {"ttl": 300}})
    monkeypatch.setattr(gateway._client_manager, "get_task_record", lambda *a: None)
    listed = await gateway.tasks_list({"server_name": SERVER})
    assert listed.ok, listed.errors
    (task,) = listed.tasks
    _assert_seconds(task)


# --- docs -------------------------------------------------------------------------

ROOT = SRC.parent.parent


def test_the_changelog_and_contract_state_the_units_for_tenant_servers() -> None:
    """#330 board F001: the release note warns tenant servers built to the old
    seconds contract, and both docs give the unit of the snake_case alias."""
    text = (ROOT / "CHANGELOG.md").read_text()
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    start = unreleased.index("converted between pmcp's seconds")
    entry = unreleased[start:].split("\n- **", 1)[0]
    assert "tenant server" in entry.lower(), entry
    assert "`poll_interval`" in entry and "milliseconds" in entry
    contract = (ROOT / "specs" / "tenant-code-mode-host-contract.md").read_text()
    assert "PMCP forwards them when supplied" not in contract
    # board round 3 F002: the compliance doc no longer lists the fixed deviation
    compliance = (ROOT / "SPEC_COMPLIANCE.md").read_text()
    assert "forwards them unchanged" not in compliance, "stale #330 deviation"
    assert "`poll_interval` (snake_case)" in contract
