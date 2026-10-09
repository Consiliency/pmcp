"""Advertised nullable arguments survive host schema converters (Consiliency/pmcp#369).

Since Consiliency/pmcp#300 an optional argument was advertised as
``type: [X, "null"]`` and, for an enum, with ``null`` as an ``enum`` member.
OpenCode's Google provider stringifies every ``enum`` member, so ``null``
reached Gemini as the string ``"null"``, and the AI SDK's OpenAPI conversion
turned each type array into a typeless node whose ``nullable`` it dropped.

The advertised spelling is now ``anyOf: [X, {"type": "null"}]``. These tests
pin three things, each derived over every tool and argument rather than
listed:

* the structural invariant: ``null`` appears only as that second branch --
  never as a JSON ``null`` value (``enum``, ``const``, ``default``), never in
  a ``type`` array -- and the nullable sites are exactly the model fields
  that admit ``None``;
* the gate: an explicit ``null`` is accepted at every nullable site, and the
  string ``"null"`` is refused wherever it is not a legitimate value;
* the hosts: the advertised schemas run through a port of OpenCode
  1.18.34's Google conversion chain, and through its newer ``GeminiToolSchema``
  converter, keep every optional argument omittable and nullable and turn
  nothing into a string ``"null"``.

The ports are transcribed from the minified JavaScript bundled in the
``opencode-linux-x64`` 1.18.34 binary (``strings`` byte offsets ~11331089 for
``ProviderTransform.schema``'s google branch, ~31666960 for the AI SDK's
``convertJSONSchemaToOpenAPISchema``, ~17674026 for ``GeminiToolSchema``).
They prove the shape that reaches the Gemini API, not that the API accepts
it: no live Gemini call was made.
"""

from __future__ import annotations

import math
import typing
from copy import deepcopy
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel

from pmcp.server import GatewayServer
from pmcp.tools.handlers import GATEWAY_TOOL_INPUT_MODELS, get_gateway_tool_definitions
from pmcp.tools.schema import GATE_VALIDATOR

from tests.test_gateway_tool_schemas import MINIMAL_VALID_ARGUMENTS

NULL_BRANCH = {"type": "null"}
TOOLS = {tool.name: tool for tool in get_gateway_tool_definitions()}
TOOL_NAMES = list(TOOLS)

Path = tuple[str, ...]


# --- deriving the nullable sites -----------------------------------------------


def _admits_none(annotation: Any) -> bool:
    return (
        annotation is None
        or annotation is type(None)
        or any(_admits_none(arg) for arg in typing.get_args(annotation))
    )


def _nested_models(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    if typing.get_origin(annotation) in (dict, list):
        return []  # a container of models is not an argument path
    return [m for arg in typing.get_args(annotation) for m in _nested_models(arg)]


def _model_nullable_paths(model: type[BaseModel], prefix: Path = ()) -> set[Path]:
    """Every argument path whose model field admits ``None``."""
    found: set[Path] = set()
    for name, info in model.model_fields.items():
        path = (*prefix, info.alias or name)
        if _admits_none(info.annotation):
            found.add(path)
        for nested in _nested_models(info.annotation):
            found |= _model_nullable_paths(nested, path)
    return found


def _object_branch(node: dict[str, Any]) -> dict[str, Any]:
    """The schema that describes a non-null value: a union's first branch
    that is not ``null`` (whatever the branch order or spelling)."""
    for branch in node.get("anyOf") or []:
        if isinstance(branch, dict) and branch.get("type") not in (None, "null"):
            return branch
    return node


def _schema_nullable_sites(schema: dict[str, Any]) -> dict[Path, dict[str, Any]]:
    """Every argument path whose advertised schema admits ``null``."""
    sites: dict[Path, dict[str, Any]] = {}

    def walk(node: dict[str, Any], prefix: Path) -> None:
        for name, prop in (node.get("properties") or {}).items():
            path = (*prefix, name)
            if NULL_BRANCH in (prop.get("anyOf") or []):
                sites[path] = prop
            value = _object_branch(prop)
            if value.get("type") == "object":
                walk(value, path)

    walk(schema, ())
    return sites


def _all_sites() -> list[tuple[str, Path]]:
    """From the models, not the schemas: a site the schema forgot to make
    nullable is still tested (and red), whatever the spelling."""
    return [
        (name, path)
        for name in TOOL_NAMES
        if (model := GATEWAY_TOOL_INPUT_MODELS[name]) is not None
        for path in sorted(_model_nullable_paths(model))
    ]


SITES = _all_sites()


def _advertised_node(name: str, path: Path) -> dict[str, Any]:
    node = TOOLS[name].input_schema
    for key in path:
        node = (_object_branch(node).get("properties") or {}).get(key) or {}
    return node


def _value_schema(node: dict[str, Any]) -> dict[str, Any]:
    """What a non-null value must match, in either spelling (the pre-#369
    ``type: [X, "null"]`` one too, so these tests also run on main)."""
    if "anyOf" in node:
        return _object_branch(node)
    if isinstance(node.get("type"), list):
        out = {**node, "type": [t for t in node["type"] if t != "null"][0]}
        if "enum" in out:
            out["enum"] = [v for v in out["enum"] if v is not None]
        return out
    return node


def _with(arguments: dict[str, Any], path: Path, value: Any) -> dict[str, Any]:
    out = deepcopy(arguments)
    node = out
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value
    return out


def _walk(
    node: Any, path: tuple[Any, ...] = ()
) -> typing.Iterator[tuple[tuple[Any, ...], Any]]:
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, (*path, key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, (*path, index))


# --- 1. the structural invariant ----------------------------------------------


def test_sites_were_derived() -> None:
    """The four enums Consiliency/pmcp#369 names are among the derived sites."""
    assert len(SITES) >= 40, len(SITES)
    for site in [
        ("gateway.catalog_search", ("filters", "risk_max")),
        ("gateway.refresh", ("source",)),
        ("gateway.set_startup_policy", ("source",)),
        ("gateway.sync_environment", ("platform",)),
    ]:
        assert site in SITES


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_null_is_only_ever_a_type_branch(name: str) -> None:
    """No JSON ``null`` anywhere in an advertised schema (``enum``, ``const``,
    ``default``, ``examples`` ...), no ``type`` array, and every
    ``{"type": "null"}`` is the second of exactly two ``anyOf`` branches whose
    first branch has one scalar ``type``."""
    schema = TOOLS[name].input_schema
    problems: list[str] = []
    for path, node in _walk(schema):
        if node is None:
            problems.append(f"JSON null at {path}")
        if not isinstance(node, dict):
            continue
        if isinstance(node.get("type"), list):
            problems.append(f"type array at {path}: {node['type']}")
        if node.get("type") == "null":
            parent = path[:-2]
            any_of = schema
            for key in parent:
                any_of = any_of[key]
            ok = (
                path[-2:] == ("anyOf", 1)
                and isinstance(any_of.get("anyOf"), list)
                and len(any_of["anyOf"]) == 2
                and isinstance(any_of["anyOf"][0].get("type"), str)
                and any_of["anyOf"][0]["type"] != "null"
            )
            if not ok:
                problems.append(f"null branch outside [X, null] at {path}")
        for keyword in ("oneOf", "allOf", "not", "const", "nullable"):
            if keyword in node and path[-1:] != ("properties",):  # not a NAME
                problems.append(f"non-portable {keyword!r} at {path}")
    assert problems == [], problems


@pytest.mark.parametrize(
    "name", [n for n in TOOL_NAMES if GATEWAY_TOOL_INPUT_MODELS[n]]
)
def test_nullable_sites_are_exactly_the_fields_that_admit_none(name: str) -> None:
    """The class, from the models: every field that admits ``None`` -- top
    level or nested -- is advertised nullable, and nothing else is."""
    model = GATEWAY_TOOL_INPUT_MODELS[name]
    assert model is not None
    advertised = set(_schema_nullable_sites(TOOLS[name].input_schema))
    assert advertised == _model_nullable_paths(model)


# --- 2. the gate --------------------------------------------------------------


def _gate_accepts(name: str, arguments: dict[str, Any]) -> bool:
    try:
        jsonschema.validate(arguments, TOOLS[name].input_schema, cls=GATE_VALIDATOR)
    except jsonschema.ValidationError:
        return False
    return True


@pytest.mark.parametrize(("name", "path"), SITES)
def test_gate_accepts_explicit_null_at_every_nullable_site(
    name: str, path: Path
) -> None:
    """Consiliency/pmcp#300's promise, per site: ``null`` passes the gate."""
    assert _gate_accepts(name, _with(MINIMAL_VALID_ARGUMENTS[name], path, None))


@pytest.mark.parametrize(
    ("name", "path"),
    [
        (name, path)
        for name, path in SITES
        if _value_schema(_advertised_node(name, path)).get("type") != "string"
        or "enum" in _value_schema(_advertised_node(name, path))
    ],
)
def test_gate_refuses_the_string_null_where_it_is_not_a_value(
    name: str, path: Path
) -> None:
    """``"null"`` is not ``null``: refused at every enum and every non-string
    nullable site. (A free-form string argument legitimately takes it.)"""
    assert not _gate_accepts(name, _with(MINIMAL_VALID_ARGUMENTS[name], path, "null"))


ENUM_SITES = [
    (name, path)
    for name, path in SITES
    if "enum" in _value_schema(_advertised_node(name, path))
]


@pytest.mark.parametrize(("name", "path"), ENUM_SITES)
@pytest.mark.asyncio
async def test_server_gate_on_every_nullable_enum(
    name: str, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through ``_handle_call_tool``: ``null`` reaches the handler,
    ``"null"`` is an ``Input validation error`` and never does."""
    from mcp.types import CallToolRequestParams

    srv = GatewayServer()
    seen: list[dict[str, Any]] = []

    async def handler(arguments: dict[str, Any], *_a: Any, **_k: Any) -> dict[str, Any]:
        seen.append(arguments)
        return {"ok": True}

    monkeypatch.setattr(srv._gateway_tools, name.removeprefix("gateway."), handler)
    base = MINIMAL_VALID_ARGUMENTS[name]
    refused = await srv._handle_call_tool(
        None,  # type: ignore[arg-type]
        CallToolRequestParams(name=name, arguments=_with(base, path, "null")),
    )
    text = " ".join(getattr(c, "text", "") for c in refused.content)
    assert refused.is_error is True and text.startswith("Input validation error:"), text
    assert seen == []
    await srv._handle_call_tool(
        None,  # type: ignore[arg-type]
        CallToolRequestParams(name=name, arguments=_with(base, path, None)),
    )
    assert seen == [_with(base, path, None)]


# --- 3. the hosts: ports of OpenCode 1.18.34's Gemini conversions -------------


def _js_string(value: Any) -> str:
    """JavaScript's ``String(v)`` for a JSON value."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return str(int(value))
    return str(value)


def _js_truthy(value: Any) -> bool:
    """JavaScript truthiness for a JSON value (``[]`` and ``{}`` are truthy)."""
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0 and not (isinstance(value, float) and math.isnan(value))
    if isinstance(value, str):
        return value != ""
    return True


def _has_combiner(node: Any) -> bool:
    return isinstance(node, dict) and any(
        isinstance(node.get(k), list) for k in ("anyOf", "oneOf", "allOf")
    )


_SCHEMA_KEYS = (
    "type", "properties", "items", "prefixItems", "enum", "const", "$ref",
    "additionalProperties", "patternProperties", "required", "not", "if",
    "then", "else",
)  # fmt: skip


def _is_schema(node: Any) -> bool:
    return isinstance(node, dict) and (
        _has_combiner(node) or any(k in node for k in _SCHEMA_KEYS)
    )


def opencode_google_transform(node: Any) -> Any:
    """``ProviderTransform.schema``, the ``providerID === "google" ||
    api.id.includes("gemini")`` branch (OpenCode 1.18.34)."""
    if node is None or not isinstance(node, (dict, list)):
        return node
    if isinstance(node, list):
        return [opencode_google_transform(item) for item in node]
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "enum" and isinstance(value, list):
            out[key] = [_js_string(v) for v in value]
            if out.get("type") in ("integer", "number"):
                out["type"] = "string"
        elif isinstance(value, (dict, list)):
            out[key] = opencode_google_transform(value)
        else:
            out[key] = value
    if isinstance(out.get("type"), list):
        has_null = "null" in out["type"]
        rest = [t for t in out["type"] if t != "null"]
        if not rest:
            out["type"] = "null"
        else:
            del out["type"]
            out["anyOf"] = [{"type": t} for t in rest]
            if has_null:
                out["nullable"] = True
    if (
        out.get("type") == "object"
        and _js_truthy(out.get("properties"))
        and isinstance(out.get("required"), list)
    ):
        out["required"] = [k for k in out["required"] if k in out["properties"]]
    if out.get("type") == "array" and not _has_combiner(out):
        if out.get("items") is None:
            out["items"] = {}
        if isinstance(out["items"], dict) and not _is_schema(out["items"]):
            out["items"]["type"] = "string"
    if (
        _js_truthy(out.get("type"))
        and out["type"] != "object"
        and not _has_combiner(out)
    ):
        out.pop("properties", None)
        out.pop("required", None)
    return out


def _is_empty_object(node: Any) -> bool:
    return (
        isinstance(node, dict)
        and node.get("type") == "object"
        and (node.get("properties") is None or len(node["properties"]) == 0)
        and not _js_truthy(node.get("additionalProperties"))
    )


def ai_sdk_google_openapi(node: Any, is_root: bool = True) -> Any:
    """``@ai-sdk/google``'s ``convertJSONSchemaToOpenAPISchema`` as bundled."""
    if node is None:
        return None
    if _is_empty_object(node):
        if is_root:
            return None
        if _js_truthy(node.get("description")):
            return {"type": "object", "description": node["description"]}
        return {"type": "object"}
    if isinstance(node, bool):
        return {"type": "boolean", "properties": {}}
    out: dict[str, Any] = {}
    if _js_truthy(node.get("description")):
        out["description"] = node["description"]
    if _js_truthy(node.get("required")):
        out["required"] = node["required"]
    if _js_truthy(node.get("format")):
        out["format"] = node["format"]
    if "const" in node:
        out["enum"] = [node["const"]]
    kind = node.get("type")
    if _js_truthy(kind):
        if isinstance(kind, list):
            rest = [t for t in kind if t != "null"]
            if not rest:
                out["type"] = "null"
            else:
                out["anyOf"] = [{"type": t} for t in rest]
                if "null" in kind:
                    out["nullable"] = True
        else:
            out["type"] = kind
    if "enum" in node:
        out["enum"] = node["enum"]
    if node.get("properties") is not None:
        out["properties"] = {
            k: ai_sdk_google_openapi(v, False) for k, v in node["properties"].items()
        }
    items = node.get("items")
    if _js_truthy(items):
        out["items"] = (
            [ai_sdk_google_openapi(i, False) for i in items]
            if isinstance(items, list)
            else ai_sdk_google_openapi(items, False)
        )
    if _js_truthy(node.get("allOf")):
        out["allOf"] = [ai_sdk_google_openapi(b, False) for b in node["allOf"]]
    any_of = node.get("anyOf")
    if _js_truthy(any_of):
        if any(isinstance(b, dict) and b.get("type") == "null" for b in any_of):
            rest = [
                b
                for b in any_of
                if not (isinstance(b, dict) and b.get("type") == "null")
            ]
            if len(rest) == 1:
                converted = ai_sdk_google_openapi(rest[0], False)
                if isinstance(converted, dict):
                    out["nullable"] = True
                    out.update(converted)
            else:
                out["anyOf"] = [ai_sdk_google_openapi(b, False) for b in rest]
                out["nullable"] = True
        else:
            out["anyOf"] = [ai_sdk_google_openapi(b, False) for b in any_of]
    if _js_truthy(node.get("oneOf")):
        out["oneOf"] = [ai_sdk_google_openapi(b, False) for b in node["oneOf"]]
    if "minLength" in node:
        out["minLength"] = node["minLength"]
    return out


def opencode_gemini_request_schema(schema: dict[str, Any]) -> Any:
    """What OpenCode's Google provider sends as a function's ``parameters``."""
    return ai_sdk_google_openapi(opencode_google_transform(deepcopy(schema)))


def _gts_sanitize(node: Any) -> Any:
    """``GeminiToolSchema``'s first pass (``Q1``)."""
    if not isinstance(node, dict):
        return [_gts_sanitize(i) for i in node] if isinstance(node, list) else node
    out = {
        k: ([_js_string(v) for v in value] if k == "enum" and isinstance(value, list) else _gts_sanitize(value))
        for k, value in node.items()
    }  # fmt: skip
    if isinstance(out.get("enum"), list) and out.get("type") in ("integer", "number"):
        out["type"] = "string"
    props = out.get("properties")
    if (
        out.get("type") == "object"
        and isinstance(props, dict)
        and isinstance(out.get("required"), list)
    ):
        out["required"] = [
            r for r in out["required"] if isinstance(r, str) and r in props
        ]
    if out.get("type") == "array" and not _has_combiner(out):
        out["items"] = out.get("items") if out.get("items") is not None else {}
        if isinstance(out["items"], dict) and not _is_schema(out["items"]):
            out["items"] = {**out["items"], "type": "string"}
    if (
        isinstance(out.get("type"), str)
        and out["type"] != "object"
        and not _has_combiner(out)
    ):
        out.pop("properties", None)
        out.pop("required", None)
    return out


def _gts_project(node: Any) -> Any:
    """``GeminiToolSchema``'s second pass (``ix``)."""
    if not isinstance(node, dict) or (
        node.get("type") == "object"
        and (not isinstance(node.get("properties"), dict) or not node["properties"])
        and not _js_truthy(node.get("additionalProperties"))
    ):
        return None
    kind = node.get("type")
    entries = {
        "description": node.get("description"),
        "required": node.get("required"),
        "format": node.get("format"),
        "type": ([t for t in kind if t != "null"] or [None])[0] if isinstance(kind, list) else kind,
        "nullable": True if isinstance(kind, list) and "null" in kind else None,
        "enum": [node["const"]] if "const" in node else node.get("enum"),
        "properties": (
            {k: v for k, v in ((k, _gts_project(p)) for k, p in node["properties"].items()) if v is not None}
            if isinstance(node.get("properties"), dict) else None
        ),
        "items": (
            [_gts_project(i) for i in node["items"]] if isinstance(node.get("items"), list)
            else None if "items" not in node else _gts_project(node["items"])
        ),
        "allOf": [_gts_project(b) for b in node["allOf"]] if isinstance(node.get("allOf"), list) else None,
        "anyOf": [_gts_project(b) for b in node["anyOf"]] if isinstance(node.get("anyOf"), list) else None,
        "oneOf": [_gts_project(b) for b in node["oneOf"]] if isinstance(node.get("oneOf"), list) else None,
        "minLength": node.get("minLength"),
    }  # fmt: skip
    return {k: v for k, v in entries.items() if v is not None}


def opencode_gemini_tool_schema(schema: dict[str, Any]) -> Any:
    """OpenCode's newer ``GeminiToolSchema.convert`` (``ix(Q1(x))``)."""
    return _gts_project(_gts_sanitize(deepcopy(schema)))


#: Gemini API ``Schema`` fields and ``Type`` values (ai.google.dev, ``Schema``).
GEMINI_SCHEMA_FIELDS = {
    "type", "format", "title", "description", "nullable", "enum", "maxItems",
    "minItems", "properties", "required", "minProperties", "maxProperties",
    "minLength", "maxLength", "pattern", "example", "anyOf", "propertyOrdering",
    "default", "items", "minimum", "maximum",
}  # fmt: skip
GEMINI_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}


def _converted_site(converted: Any, path: Path) -> dict[str, Any]:
    node = converted
    for key in path:
        node = _object_branch(node)["properties"][key]
    return node


def _enum_strings_null(converted: Any) -> list[tuple[Any, ...]]:
    return [
        path
        for path, node in _walk(converted)
        if isinstance(node, dict)
        and isinstance(node.get("enum"), list)
        and any(v is None or v == "null" for v in node["enum"])
    ]


def test_ports_reproduce_the_captured_defect() -> None:
    """Fidelity check of the ports against Consiliency/pmcp#369's capture: the
    pre-fix spelling of ``refresh.source`` reaches Gemini with the STRING
    ``"null"`` in its enum and its ``null`` branch dropped."""
    pre_fix = {
        "type": "object",
        "properties": {
            "source": {
                "description": "Config source to reload from",
                "enum": ["claude_config", "custom", None],
                "type": ["string", "null"],
            }
        },
    }
    sent = opencode_gemini_request_schema(pre_fix)["properties"]["source"]
    assert sent["enum"] == ["claude_config", "custom", "null"]
    assert sent["anyOf"] == [{"type": "string"}]
    assert "nullable" not in sent and "type" not in sent
    newer = opencode_gemini_tool_schema(pre_fix)["properties"]["source"]
    assert newer["enum"] == ["claude_config", "custom", "null"]


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_no_string_null_reaches_gemini(name: str) -> None:
    schema = TOOLS[name].input_schema
    assert _enum_strings_null(opencode_gemini_request_schema(schema)) == []
    assert _enum_strings_null(opencode_gemini_tool_schema(schema)) == []


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_gemini_request_schema_is_in_gemini_subset(name: str) -> None:
    """Every node OpenCode's Google provider sends uses only Gemini ``Schema``
    fields, has one scalar ``type``, string-only enums, and arrays with
    ``items`` (Gemini's documented function-declaration subset)."""
    sent = opencode_gemini_request_schema(TOOLS[name].input_schema)
    if sent is None:  # a no-argument tool: the AI SDK sends no parameters
        assert GATEWAY_TOOL_INPUT_MODELS[name] is None
        return
    problems: list[str] = []

    def check(node: Any, path: Path) -> None:
        extra = set(node) - GEMINI_SCHEMA_FIELDS
        if extra:
            problems.append(f"{path}: fields {sorted(extra)}")
        if node.get("type") not in GEMINI_TYPES:
            problems.append(f"{path}: type {node.get('type')!r}")
        if "enum" in node and (
            node.get("type") != "string"
            or not all(isinstance(v, str) for v in node["enum"])
        ):
            problems.append(f"{path}: non-string enum")
        if node.get("type") == "array" and not isinstance(node.get("items"), dict):
            problems.append(f"{path}: array without items")
        for key, prop in (node.get("properties") or {}).items():
            check(prop, (*path, key))
        if isinstance(node.get("items"), dict):
            check(node["items"], (*path, "[]"))
        for i, branch in enumerate(node.get("anyOf") or []):
            check(branch, (*path, f"anyOf{i}"))

    check(sent, ())
    assert problems == [], problems


@pytest.mark.parametrize(("name", "path"), SITES)
def test_every_nullable_argument_stays_nullable_and_omittable_for_gemini(
    name: str, path: Path
) -> None:
    """Per site, through the AI SDK chain: the node is Gemini's canonical
    ``{type: X, nullable: true}`` carrying X's own keywords (enum, items,
    properties), and the argument is not in its parent's ``required``."""
    schema = TOOLS[name].input_schema
    value = _value_schema(_advertised_node(name, path))
    sent = opencode_gemini_request_schema(schema)
    node = _converted_site(sent, path)
    assert node.get("nullable") is True, node
    assert node.get("type") == value["type"], node
    if "enum" in value:
        assert node["enum"] == value["enum"]
    if value["type"] == "array":
        assert isinstance(node.get("items"), dict), node
    if value.get("properties"):
        assert set(node.get("properties") or {}) == set(value["properties"]), node
    parent = _converted_site(sent, path[:-1])
    assert path[-1] not in (parent.get("required") or [])

    newer = _converted_site(opencode_gemini_tool_schema(schema), path)
    branches = newer.get("anyOf")
    assert branches is not None and branches[1] == {"type": "null"}, newer
    assert branches[0].get("type") == value["type"], newer


@pytest.mark.parametrize(
    "name", [n for n in TOOL_NAMES if GATEWAY_TOOL_INPUT_MODELS[n]]
)
def test_gemini_required_is_the_models_required(name: str) -> None:
    """Every optional argument stays omittable: the converted top-level
    ``required`` is exactly the model's required fields."""
    model = GATEWAY_TOOL_INPUT_MODELS[name]
    assert model is not None
    sent = opencode_gemini_request_schema(TOOLS[name].input_schema)
    expected = {
        (f.alias or n) for n, f in model.model_fields.items() if f.is_required()
    }
    assert set(sent.get("required") or []) == expected


# --- 4. the gate reports what it reported under the old spelling --------------


def _type_array_spelling(node: Any) -> Any:
    """The advertised schema as Consiliency/pmcp#300 spelled it: each
    ``anyOf: [X, null]`` folded back to ``X`` with ``"null"`` added to its
    ``type`` (and ``None`` to its ``enum``). The oracle for the messages."""
    if isinstance(node, list):
        return [_type_array_spelling(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {
        k: (
            {name: _type_array_spelling(p) for name, p in v.items()}
            if k == "properties"
            else _type_array_spelling(v)
        )
        for k, v in node.items()
    }
    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and any_of[1] == NULL_BRANCH:
        inner = {**any_of[0], **{k: v for k, v in out.items() if k != "anyOf"}}
        inner["type"] = [inner["type"], "null"]
        if "enum" in inner:
            inner["enum"] = [*inner["enum"], None]
        return inner
    return out


#: Values of every JSON type, plus ones that break a bound or a length, and
#: ones with two failures inside one value (two bad items, two bad fields).
PROBES: list[Any] = [
    5, 1.5, -1, 0, 10**20, float("nan"), True, "x", "null", "a" * 64 + "\n",
    [], ["x", 1], [1, 2], {}, {"a": 1}, {"a": 1, "b": 2}, None,
]  # fmt: skip


def _reported(schema: dict[str, Any], arguments: dict[str, Any], *, gate: bool) -> Any:
    """What a refusal reports: through the gate's selection, or through
    ``jsonschema.validate``'s own (``best_match`` over the raw errors)."""
    from jsonschema.exceptions import best_match

    from pmcp.tools.schema import gate_error_for

    if gate:
        error = gate_error_for(arguments, schema)
    else:
        error = best_match(GATE_VALIDATOR(schema).iter_errors(arguments))
    if error is None:
        return "accepted"
    message = error.message
    if error.validator == "enum" and not gate:
        message = message.replace(", None]", "]")  # the one intended difference
    return message, error.validator, list(error.absolute_path)


def _assert_reports_match(name: str, arguments: dict[str, Any]) -> None:
    schema = TOOLS[name].input_schema
    got = _reported(schema, arguments, gate=True)
    want = _reported(_type_array_spelling(schema), arguments, gate=False)
    assert got == want, (name, arguments, got, want)


@pytest.mark.parametrize(("name", "path"), SITES)
def test_gate_reports_what_the_type_array_spelling_reported(
    name: str, path: Path
) -> None:
    """One value at one site, every probe: the gate's message, failing keyword
    and argument path equal what ``jsonschema`` reported under the pre-#369
    spelling -- except that an enum's message no longer lists ``None``."""
    for value in PROBES:
        _assert_reports_match(name, _with(MINIMAL_VALID_ARGUMENTS[name], path, value))


#: Values for the multi-failure grid: wrong scalar, wrong string, two bad
#: items, two bad fields, null, and the string "null".
GRID_VALUES: list[Any] = [5, "x", [1, 2], {"a": 1, "b": 2}, None, "null"]


@pytest.mark.parametrize("name", sorted({name for name, _ in SITES}))
def test_gate_reports_what_the_type_array_spelling_reported_for_many_failures(
    name: str,
) -> None:
    """Two or more failures in one call -- at two sites, or two inside one
    value -- are where ``best_match``'s ranking decides (it takes the LAST
    sibling at the top level but descends into an ``anyOf`` for the FIRST).
    Every ordered pair of this tool's sites x every pair of grid values,
    plus 300 seeded calls with three sites each, agree with the oracle."""
    import itertools
    import random

    paths = [path for tool, path in SITES if tool == name]
    base = MINIMAL_VALID_ARGUMENTS[name]
    for first, second in itertools.permutations(paths, 2):
        if first[: len(second)] == second or second[: len(first)] == first:
            continue  # one is inside the other: the second write replaces it
        for a, b in itertools.product(GRID_VALUES, repeat=2):
            _assert_reports_match(name, _with(_with(base, first, a), second, b))
    rng = random.Random(f"369:{name}")
    for _ in range(300):
        arguments = base
        chosen: list[Path] = []
        for path in rng.sample(paths, min(3, len(paths))):
            if any(c[: len(path)] == path or path[: len(c)] == c for c in chosen):
                continue  # nested in another chosen site
            chosen.append(path)
            arguments = _with(arguments, path, rng.choice(PROBES))
        _assert_reports_match(name, arguments)


@pytest.mark.parametrize(
    ("name", "arguments", "reported"),
    [
        (
            "gateway.sync_environment",
            {"detected_clis": [1, 2]},
            ("2 is not of type 'string'", "type", ["detected_clis", 1]),
        ),
        (
            "gateway.request_capability",
            {"query": "q", "available_clis": [1, 2]},
            ("2 is not of type 'string'", "type", ["available_clis", 1]),
        ),
        (
            "gateway.invoke",
            {"tool_id": "a::b", "task": {"enabled": "x", "ttl": "5"}},
            ("'5' is not of type 'integer', 'null'", "type", ["task", "ttl"]),
        ),
        (
            "gateway.catalog_search",
            {"filters": {"server": {}, "tags": "x"}},
            ("'x' is not of type 'array', 'null'", "type", ["filters", "tags"]),
        ),
    ],
)
def test_round_one_multi_failure_cases(
    name: str, arguments: dict[str, Any], reported: tuple[Any, ...]
) -> None:
    """The four cases the #370 round-1 review measured diverging from 3.0."""
    assert _reported(TOOLS[name].input_schema, arguments, gate=True) == reported


def test_gate_error_for_falls_back_when_the_fold_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fold that raises never loses the refusal: the raw error is reported."""
    import pmcp.tools.schema as schema_module

    def boom(error: Any) -> Any:
        raise KeyError("type")

    monkeypatch.setattr(schema_module, "_unfolded", boom)
    error = schema_module.gate_error_for(
        {"source": 5}, TOOLS["gateway.refresh"].input_schema
    )
    assert error is not None and list(error.absolute_path) == ["source"]


@pytest.mark.asyncio
async def test_server_message_for_a_wrongly_typed_nullable_argument() -> None:
    """The 3.0 migration guide's documented case, end to end."""
    from mcp.types import CallToolRequestParams

    srv = GatewayServer()
    result = await srv._handle_call_tool(
        None,  # type: ignore[arg-type]
        CallToolRequestParams(
            name="gateway.invoke",
            arguments={"tool_id": "a::b", "task": {"enabled": True, "ttl": "5"}},
        ),
    )
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert text == "Input validation error: '5' is not of type 'integer', 'null'"


@pytest.mark.asyncio
async def test_a_refusal_survives_a_ranking_that_raises(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``best_match`` itself raising -- on the unfolded errors and on the raw
    ones -- still leaves the call refused at the gate, and recorded in the
    scoped audit as a rejection: the fallback holds by construction, not
    because ranking happens never to fail."""
    import json as _json

    from mcp.types import CallToolRequestParams

    import pmcp.tools.schema as schema_module
    from pmcp.scoped_advisor_audit import ScopedAdvisorAudit

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("ranking failed")

    monkeypatch.setattr(schema_module, "best_match", boom)
    error = schema_module.gate_error_for(
        {"source": 5}, TOOLS["gateway.refresh"].input_schema
    )
    assert error is not None  # the first raw error

    srv = GatewayServer()
    audit_path = tmp_path / "audit.jsonl"
    srv._scoped_advisor_audit = ScopedAdvisorAudit(audit_path, policy_digest="e" * 64)
    reached: list[Any] = []

    async def handler(*args: Any, **_kwargs: Any) -> dict[str, Any]:
        reached.append(args)
        return {}

    monkeypatch.setattr(srv._gateway_tools, "refresh", handler)
    result = await srv._handle_call_tool(
        None,  # type: ignore[arg-type]
        CallToolRequestParams(name="gateway.refresh", arguments={"source": 5}),
    )
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert result.is_error is True and text.startswith("Input validation error:"), text
    assert reached == []
    events = [_json.loads(line) for line in audit_path.read_text().splitlines()]
    rejections = [e for e in events if e.get("event") == "audit.rejection"]
    assert len(rejections) == 1, events
    assert rejections[0]["terminal_status"] == "invalid_arguments"
