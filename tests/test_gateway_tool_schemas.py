"""Advertised gateway tool schemas are derived from, and agree with, the
pydantic models that validate their arguments (Consiliency/pmcp#236).

`get_gateway_tool_definitions()` builds every `inputSchema` from
`GATEWAY_TOOL_INPUT_MODELS` via `input_schema_for`, and
`GatewayServer._handle_call_tool` enforces that advertised schema with
`jsonschema` before the handler's `Model.model_validate` runs. These tests
pin the three links in that chain — advertised == derived, derived == what
the handler validates with, and the post-processing that turns
`model_json_schema()` into an MCP `inputSchema` — so a hand edit to any one
of them fails here rather than drifting.

"Advertised == model" means advertised == the model's JSON-Schema PROJECTION.
Three things the models enforce are not expressible in the schema and stay
handler-only: `InvokeInput`'s correlation-ID charset validator and its
all-or-none `model_validator`, and `RegisterDiscoveredServerInput`'s
`_validate_package`. Everything the projection CAN express -- types, bounds,
enums, required, and (A1) `null` on optional fields -- agrees on what is
REQUIRED and on `null` (`test_optional_field_null_agrees_between_gate_and_model`
checks per field). It does not agree on type COERCION: pydantic's lax mode
accepts `1` / `"true"` for a boolean and `"5"` for a number, and the JSON-Schema
gate does not, so the gate is stricter there (stated in the plan). Nor, by
itself, on a regex `pattern`: the gate's Python `$` matches before a final
newline and pydantic's Rust `$` does not -- closed for the one `pattern` field
(`evidence_label_digest`) by length bounds, pinned by
`test_digest_pattern_agrees_between_gate_and_model`.
"""

from __future__ import annotations

import inspect
import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import jsonschema
import pytest
from pydantic import BaseModel, Field

from pmcp.server import GatewayServer
from pmcp.tools.handlers import (
    GATEWAY_TOOL_INPUT_MODELS,
    GatewayTools,
    get_gateway_tool_definitions,
)
from pmcp.tools.schema import NO_ARGUMENTS_SCHEMA, input_schema_for

SNAPSHOT = Path(__file__).parent / "fixtures" / "gateway_tool_schemas.json"

#: The smallest argument set each tool accepts. `{}` for the no-argument tools.
MINIMAL_VALID_ARGUMENTS: dict[str, dict[str, Any]] = {
    "gateway.catalog_search": {},
    "gateway.describe": {"tool_id": "srv::tool"},
    "gateway.invoke": {"tool_id": "srv::tool"},
    "gateway.refresh": {},
    "gateway.connect_server": {"server_name": "srv"},
    "gateway.disconnect_server": {"server_name": "srv"},
    "gateway.restart_server": {"server_name": "srv"},
    "gateway.health": {},
    "gateway.config_status": {},
    "gateway.get_startup_policy": {},
    "gateway.set_startup_policy": {"operation": "add"},
    "gateway.request_capability": {"query": "scrape a site"},
    "gateway.sync_environment": {},
    "gateway.provision": {"server_name": "srv"},
    "gateway.update_server": {"server_name": "srv"},
    "gateway.auth_connect": {"server_name": "srv"},
    "gateway.submit_feedback": {"title": "a title here", "description": "d"},
    "gateway.provision_status": {"job_id": "job"},
    "gateway.list_pending": {},
    "gateway.cancel": {"request_id": "srv::1"},
    "gateway.tasks_list": {},
    "gateway.tasks_get": {"server_name": "srv", "task_id": "t"},
    "gateway.tasks_result": {"server_name": "srv", "task_id": "t"},
    "gateway.tasks_cancel": {"server_name": "srv", "task_id": "t"},
    "gateway.search_registry": {"query": "github"},
    "gateway.register_discovered_server": {"package": "pkg", "server_name": "srv"},
}

TOOL_NAMES = [tool.name for tool in get_gateway_tool_definitions()]
TOOLS_WITH_MODELS = [n for n in TOOL_NAMES if GATEWAY_TOOL_INPUT_MODELS[n] is not None]
TOOLS_WITHOUT_MODELS = [n for n in TOOL_NAMES if GATEWAY_TOOL_INPUT_MODELS[n] is None]


def _model_types(annotation: Any) -> list[type[BaseModel]]:
    import typing

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [t for arg in typing.get_args(annotation) for t in _model_types(arg)]


def _tool(name: str):
    return next(t for t in get_gateway_tool_definitions() if t.name == name)


def _handler_method(name: str) -> str:
    return name.removeprefix("gateway.")


def _object_schemas(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Every object schema reachable from `schema`, itself included."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            kind = node.get("type")
            # An optional nested object is typed ["object", "null"] (A1).
            is_object = kind == "object" or (
                isinstance(kind, list) and "object" in kind
            )
            if is_object and "properties" in node:
                found.append(node)
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    for prop in value.values():
                        walk(prop)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return found


def _schema_keywords(schema: dict[str, Any]) -> set[str]:
    """Every dict key used as a schema keyword (property NAMES excluded)."""
    keys: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                keys.add(key)
                if key == "properties" and isinstance(value, dict):
                    for prop in value.values():
                        walk(prop)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return keys


# --- registry <-> advertised <-> handler --------------------------------------


def test_registry_lists_every_advertised_tool_in_order() -> None:
    assert list(GATEWAY_TOOL_INPUT_MODELS) == TOOL_NAMES
    assert set(MINIMAL_VALID_ARGUMENTS) == set(TOOL_NAMES)


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_advertised_schema_is_derived_from_registered_model(name: str) -> None:
    """A hand-edited inputSchema no longer matches its model and fails here."""
    assert _tool(name).input_schema == input_schema_for(GATEWAY_TOOL_INPUT_MODELS[name])


@pytest.mark.parametrize("name", TOOLS_WITH_MODELS)
def test_handler_validates_arguments_with_the_registered_model(name: str) -> None:
    """The model the schema is derived from is the one the handler runs."""
    model = GATEWAY_TOOL_INPUT_MODELS[name]
    assert model is not None
    source = inspect.getsource(getattr(GatewayTools, _handler_method(name)))
    assert re.search(rf"\b{model.__name__}\.model_validate\(", source), (
        f"{name}: handler does not validate with {model.__name__}"
    )


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_server_dispatch_agrees_with_registry(name: str) -> None:
    """Tools registered with no model are dispatched with no arguments."""
    source = inspect.getsource(GatewayServer._handle_call_tool)
    method = _handler_method(name)
    if GATEWAY_TOOL_INPUT_MODELS[name] is None:
        assert f"self._gateway_tools.{method}()" in source
    else:
        assert f"self._gateway_tools.{method}(" in source
        assert f"self._gateway_tools.{method}()" not in source


def test_every_dispatched_gateway_name_is_registered() -> None:
    """X1: `_handle_call_tool` validates only names it finds in the registry,
    so a dispatch branch for an unregistered name would skip the schema gate
    and hand raw arguments to its handler. Pin the two sets equal, both ways
    (the parametrised test above only proves registry -> dispatch)."""
    source = inspect.getsource(GatewayServer._handle_call_tool)
    dispatched = set(re.findall(r'\bname == "(gateway\.[a-z_]+)"', source))
    assert len(dispatched) >= len(TOOL_NAMES) - 1  # the pattern matched the branches
    assert dispatched == set(TOOL_NAMES), {
        "dispatched, not registered": sorted(dispatched - set(TOOL_NAMES)),
        "registered, not dispatched": sorted(set(TOOL_NAMES) - dispatched),
    }


@pytest.mark.parametrize("name", TOOLS_WITHOUT_MODELS)
def test_no_argument_tools_advertise_an_empty_object(name: str) -> None:
    assert _tool(name).input_schema == NO_ARGUMENTS_SCHEMA


# --- the advertised shape ------------------------------------------------------


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_advertised_schema_is_a_self_contained_mcp_input_schema(name: str) -> None:
    schema = _tool(name).input_schema
    assert schema["type"] == "object"
    keywords = _schema_keywords(schema)
    assert not keywords & {"$ref", "$defs", "title"}, keywords
    for obj in _object_schemas(schema):
        for prop_name, prop in obj["properties"].items():
            assert "anyOf" not in prop, f"{name}.{prop_name}: nullable anyOf survived"
            assert isinstance(prop.get("description"), str) and prop["description"], (
                f"{name}.{prop_name}: every advertised argument needs a description"
            )


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_advertised_schema_accepts_the_minimal_valid_arguments(name: str) -> None:
    arguments = MINIMAL_VALID_ARGUMENTS[name]
    jsonschema.validate(instance=arguments, schema=_tool(name).input_schema)
    model = GATEWAY_TOOL_INPUT_MODELS[name]
    if model is not None:
        model.model_validate(arguments)


def test_advertised_schemas_match_snapshot() -> None:
    """The agent-facing API changed: regenerate the snapshot deliberately with
    `PMCP_UPDATE_SCHEMA_SNAPSHOT=1 uv run pytest tests/test_gateway_tool_schemas.py`
    and review the diff."""
    current = {t.name: t.input_schema for t in get_gateway_tool_definitions()}
    if os.environ.get("PMCP_UPDATE_SCHEMA_SNAPSHOT") == "1":
        SNAPSHOT.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    assert json.loads(SNAPSHOT.read_text()) == current


# --- the post-processing itself ------------------------------------------------


class _Inner(BaseModel):
    """Inner doc."""

    level: Literal["a", "b"] | None = Field(default=None, description="Level")


class _Outer(BaseModel):
    """Outer doc (dropped: the Tool carries its own description)."""

    name: str = Field(min_length=1, description="Name")
    title: str | None = Field(default=None, description="A field called title")
    count: int = Field(default=3, ge=1, le=9, description="Count")
    inner: _Inner | None = Field(default=None, description="Inner")
    meta: dict[str, Any] | None = Field(default=None, alias="_meta", description="Meta")


def test_input_schema_for_normalises_pydantic_output() -> None:
    assert input_schema_for(_Outer) == {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {"type": "string", "minLength": 1, "description": "Name"},
            "title": {
                "type": ["string", "null"],
                "description": "A field called title",
            },
            "count": {
                "type": "integer",
                "default": 3,
                "minimum": 1,
                "maximum": 9,
                "description": "Count",
            },
            "inner": {
                "type": ["object", "null"],
                "description": "Inner",
                "properties": {
                    "level": {
                        "type": ["string", "null"],
                        "enum": ["a", "b", None],
                        "description": "Level",
                    }
                },
            },
            "_meta": {
                "type": ["object", "null"],
                "additionalProperties": True,
                "description": "Meta",
            },
        },
    }


class _RequiredNullable(BaseModel):
    value: str | None = Field(description="Nullable but required")


def test_required_nullable_field_is_left_as_pydantic_wrote_it() -> None:
    """The collapse is defined for `X | None = None` only; a required nullable
    field has no `default: null` and keeps its `anyOf`."""
    prop = input_schema_for(_RequiredNullable)["properties"]["value"]
    assert prop == {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "description": "Nullable but required",
    }


def _optional_fields(model: type[BaseModel]) -> list[str]:
    return [
        (f.alias or n)
        for n, f in model.model_fields.items()
        if not f.is_required() and f.default is None
    ]


def _nullable_cases() -> list[tuple[str, dict[str, Any]]]:
    """Every (tool, arguments) where one optional field -- top-level or one
    level down inside a nested argument model -- is sent as explicit null."""
    cases: list[tuple[str, dict[str, Any]]] = []
    for name in TOOLS_WITH_MODELS:
        model = GATEWAY_TOOL_INPUT_MODELS[name]
        assert model is not None
        base = MINIMAL_VALID_ARGUMENTS[name]
        for field in _optional_fields(model):
            cases.append((name, {**base, field: None}))
        for fname, finfo in model.model_fields.items():
            for nested in _model_types(finfo.annotation):
                for sub in _optional_fields(nested):
                    cases.append((name, {**base, (finfo.alias or fname): {sub: None}}))
    return cases


@pytest.mark.parametrize(("name", "arguments"), _nullable_cases())
def test_optional_field_null_agrees_between_gate_and_model(
    name: str, arguments: dict[str, Any]
) -> None:
    """A1: the gate accepts explicit null for an optional field iff the model
    does. On HEAD the hand-written schemas rejected null where the model took
    it (`invoke.task`, `tasks_*.requestor_context`, ...)."""
    from pydantic import ValidationError

    model = GATEWAY_TOOL_INPUT_MODELS[name]
    assert model is not None
    try:
        model.model_validate(arguments)
        model_accepts = True
    except ValidationError:
        model_accepts = False
    try:
        jsonschema.validate(instance=arguments, schema=_tool(name).input_schema)
        gate_accepts = True
    except jsonschema.ValidationError:
        gate_accepts = False
    assert gate_accepts == model_accepts, (
        f"{name} {arguments}: gate={gate_accepts} model={model_accepts}"
    )


def test_schemas_are_derived_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """A2: deriving 23 schemas costs ~13 ms; `GatewayServer` reads the tool list
    on every tools/call and tools/list, so it must be built once, not per call."""
    import pmcp.tools.handlers as handlers

    calls: list[Any] = []
    real = handlers.input_schema_for
    monkeypatch.setattr(
        handlers, "input_schema_for", lambda m: (calls.append(m), real(m))[1]
    )
    handlers._derived_gateway_tools.cache_clear()
    try:
        srv = GatewayServer()
        for _ in range(5):
            get_gateway_tool_definitions()
            srv._find_gateway_tool("gateway.describe")
        assert len(calls) == len(TOOL_NAMES), len(calls)
    finally:
        handlers._derived_gateway_tools.cache_clear()


def test_input_schema_for_none_is_the_empty_object() -> None:
    assert input_schema_for(None) == NO_ARGUMENTS_SCHEMA
    assert input_schema_for(None) is not NO_ARGUMENTS_SCHEMA


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_advertised_schema_is_valid_json_schema(name: str) -> None:
    jsonschema.Draft202012Validator.check_schema(_tool(name).input_schema)


# --- the gate now enforces what the model enforces ----------------------------


async def _call_through_gate(name: str, arguments: dict[str, Any]) -> Any:
    """Drive `tools/call` through `GatewayServer._handle_call_tool`, which runs
    the jsonschema gate against the advertised schema before dispatch."""
    from unittest.mock import MagicMock

    from mcp.server.connection import Connection
    from mcp.server.context import ServerRequestContext
    from mcp.server.session import ServerSession
    from mcp.types import CallToolRequestParams

    srv = GatewayServer()
    srv._create_server(instructions="test")
    assert srv._server is not None
    entry = srv._server.get_request_handler("tools/call")
    assert entry is not None
    connection = Connection.from_envelope("2025-11-25", None, None)
    ctx = ServerRequestContext(
        session=ServerSession(MagicMock(), connection),
        lifespan_context={},
        protocol_version="2025-11-25",
        method="test",
    )
    return await entry.handler(
        ctx, CallToolRequestParams(name=name, arguments=arguments)
    )


@pytest.mark.parametrize(
    ("name", "arguments", "fragment"),
    [
        ("gateway.describe", {"tool_id": ""}, "should be non-empty"),
        (
            "gateway.submit_feedback",
            {"title": "short", "description": "d"},
            "is too short",
        ),
        (
            "gateway.tasks_result",
            {"server_name": "s", "task_id": "t", "options": {"max_output_chars": 5}},
            "less than the minimum",
        ),
    ],
)
@pytest.mark.asyncio
async def test_server_gate_rejects_what_the_model_rejects(
    name: str, arguments: dict[str, Any], fragment: str
) -> None:
    """Constraints that only the model enforced on HEAD (minLength, minimum...)
    are now advertised, so the gate rejects them with the gate's error shape."""
    from mcp.types import CallToolResult

    result = await _call_through_gate(name, arguments)
    assert isinstance(result, CallToolResult)
    assert result.is_error is True
    text = result.content[0].text  # type: ignore[union-attr]
    assert text.startswith("Input validation error:"), text
    assert fragment in text, text


@pytest.mark.asyncio
async def test_a_dispatch_branch_for_an_unregistered_name_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """X1, structurally: a name the registry does not list never reaches a
    handler, even when `_handle_call_tool` has a dispatch branch for it (the
    board's bypass was `elif name == "gateway.health" or name == "gateway.health2"`,
    which the text pin above cannot see). Simulated by unregistering a name
    that has a branch: the gate is skipped, so dispatch must refuse it."""
    from mcp.types import CallToolRequestParams

    srv = GatewayServer()
    real_find = srv._find_gateway_tool
    monkeypatch.setattr(
        srv,
        "_find_gateway_tool",
        lambda name: None if name == "gateway.health" else real_find(name),
    )
    called: list[str] = []

    async def health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        called.append("health")
        return {}

    monkeypatch.setattr(srv._gateway_tools, "health", health)
    result = await srv._handle_call_tool(
        None,  # type: ignore[arg-type]
        CallToolRequestParams(name="gateway.health", arguments={"x": "Bearer sk-x"}),
    )
    assert called == [], "an ungated name reached its handler"
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert "Unknown tool: gateway.health" in text, text


@pytest.mark.asyncio
async def test_an_unregistered_invoke_never_reaches_the_scoped_audit_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-closed guard runs BEFORE the scoped-audit `InvokeInput` check:
    were `gateway.invoke` ever unregistered, its ungated arguments would
    otherwise reach pydantic, whose error echoes the value into the response
    and the log (board round 3, N1)."""
    from mcp.types import CallToolRequestParams

    srv = GatewayServer()
    srv._scoped_advisor_audit = object()  # type: ignore[assignment]
    monkeypatch.setattr(srv, "_require_scoped_audit", lambda: None)
    real_find = srv._find_gateway_tool
    monkeypatch.setattr(
        srv,
        "_find_gateway_tool",
        lambda name: None if name == "gateway.invoke" else real_find(name),
    )
    monkeypatch.setattr(srv, "_record_scoped_invocation", lambda **_kw: None)
    secret = "SECRETVALUE-" + "x" * 40
    result = await srv._handle_call_tool(
        None,  # type: ignore[arg-type]
        CallToolRequestParams(
            name="gateway.invoke",
            arguments={"tool_id": "a::b", "run_correlation_id": secret + "!"},
        ),
    )
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert "Unknown tool: gateway.invoke" in text, text
    assert "SECRETVALUE" not in text, text


def test_digest_pattern_agrees_between_gate_and_model() -> None:
    """A trailing newline passes Python's `$` but not pydantic's: the length
    bounds make the gate reject it too (board round 3, N2)."""
    from pmcp.types import InvokeInput

    schema = _tool("gateway.invoke").input_schema
    value = "a" * 64 + "\n"
    args = {"tool_id": "a::b", "evidence_label_digest": value}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(args, schema)
    with pytest.raises(Exception):
        InvokeInput.model_validate(args)
