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


# --- implementation follow-ups (board round 3, R3-N2..N4) -------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"updatedAt": None, "lastUpdatedAt": "2025-11-25T10:00:00Z"},
        {"updatedAt": "2025-11-25T10:00:00Z", "lastUpdatedAt": None},
        {"lastUpdatedAt": "2025-11-25T10:00:00Z", "updatedAt": None},
        {"updatedAt": "not a time", "lastUpdatedAt": "2025-11-25T10:00:00Z"},
        {"updated_at": "", "last_updated_at": "2025-11-25T10:00:00Z"},
    ],
    ids=["null-first", "null-last", "null-inserted-second", "garbage", "blank"],
)
def test_a_usable_timestamp_alias_wins_over_an_unusable_one(
    payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3-N3: a `null` (or otherwise unusable) `updatedAt` next to a usable
    `lastUpdatedAt` must not hide it, whichever alias carries which."""
    monkeypatch.setattr("time.time", lambda: 1234.0)
    manager = ClientManager()
    info = manager._task_info_from_payload({"taskId": "t", **payload})
    assert info is not None
    assert info.updated_at == 1764064800.0 and info.unusable_fields == []
    record = manager._record_task("s", info)
    assert record.updated_at == 1764064800.0 and record.unusable_fields == []


@pytest.mark.parametrize(
    ("payload", "attr", "kept"),
    [
        ({"createdAt": None, "created_at": 5}, "created_at", 5.0),
        ({"pollInterval": None, "poll_interval": 2.5}, "poll_interval", 2.5),
        ({"pollInterval": 0, "poll_interval": 2.5}, "poll_interval", 2.5),
    ],
)
def test_every_aliased_hint_prefers_its_usable_alias(
    payload: dict[str, Any], attr: str, kept: Any
) -> None:
    info = ClientManager()._task_info_from_payload({"taskId": "t", **payload})
    assert info is not None
    assert getattr(info, attr) == kept and info.unusable_fields == []


def test_all_aliases_unusable_still_names_the_field() -> None:
    info = ClientManager()._task_info_from_payload(
        {"taskId": "t", "updatedAt": None, "lastUpdatedAt": ""}
    )
    assert info is not None
    assert info.updated_at is None and info.unusable_fields == ["updated_at"]


def test_an_honest_server_with_a_slow_clock_keeps_its_newer_records() -> None:
    """R3-N4: eviction is pmcp's own record order. A server whose clock runs
    ten minutes behind reports older `lastUpdatedAt`s, but its task was
    recorded after the other server's, so the other is evicted first."""
    manager = ClientManager()
    manager._max_terminal_tasks = 1
    now = 1_000_000.0
    manager._record_task(
        "on-time", McpTaskInfo(task_id="a", status="completed", updated_at=now)
    )
    manager._record_task(
        "slow-clock",
        McpTaskInfo(task_id="b", status="completed", updated_at=now - 600 + 1),
    )
    kept = {(t.server_name, t.task_id) for t in manager.get_tracked_tasks()}
    assert kept == {("slow-clock", "b")}, kept


def test_eviction_ignores_downstream_time_entirely() -> None:
    """Whatever the downstream claims -- far future, far past, or nothing --
    the record pmcp saw least recently goes first."""
    manager = ClientManager()
    manager._max_terminal_tasks = 2
    for task_id, stamp in (("x", 1e300), ("y", 0.0), ("z", None)):
        manager._record_task(
            "s", McpTaskInfo(task_id=task_id, status="completed", updated_at=stamp)
        )
    assert {t.task_id for t in manager.get_tracked_tasks()} == {"y", "z"}


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.asyncio
async def test_a_non_finite_listing_cursor_makes_the_listing_unreadable(
    transport: str,
) -> None:
    """R3-N2, a behaviour change pinned on purpose: on remote transports a
    `nextCursor: NaN` used to arrive as `null` ("no more pages"), so pmcp kept
    page one as the whole listing. It now arrives as NaN on every transport --
    as it always did on stdio -- and an unusable cursor makes the whole kind
    unreadable (the prior entries are kept, nothing is published)."""
    frame = (
        '{"jsonrpc":"2.0","id":1,"result":{"resources":'
        '[{"uri":"x://r","name":"r"}],"nextCursor":NaN}}'
    )
    result = await _through_transport(transport, frame)
    assert math.isnan(result["nextCursor"]), (transport, result)
    from tests.test_client_manager import _managed_remote

    manager = ClientManager()
    managed = _managed_remote("srv", [])
    manager._send_request = AsyncMock(return_value=result)  # type: ignore[method-assign]
    assert await manager._fetch_listing_pages(managed, "resources") is None


def test_a_task_pmcp_saw_again_is_the_newest_for_eviction() -> None:
    """Re-recording a task (a `tasks_get` refresh) moves it to the back of the
    eviction order: least recently *seen by pmcp* goes first."""
    manager = ClientManager()
    manager._max_terminal_tasks = 2
    for task_id in ("a", "b"):
        manager._record_task("s", McpTaskInfo(task_id=task_id, status="completed"))
    manager._record_task("s", McpTaskInfo(task_id="a", status="completed"))
    manager._record_task("s", McpTaskInfo(task_id="c", status="completed"))
    assert {t.task_id for t in manager.get_tracked_tasks()} == {"a", "c"}
