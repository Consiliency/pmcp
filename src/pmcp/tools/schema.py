"""Derive the gateway's advertised tool ``inputSchema`` from its argument model.

Every gateway tool validates its arguments with a pydantic model
(``pmcp.types.*Input``). The ``inputSchema`` advertised over ``tools/list`` —
and enforced by ``GatewayServer._handle_call_tool`` before the model ever
runs — is derived from that same model here, so the two cannot drift
(Consiliency/pmcp#236).

``model_json_schema()`` is not usable verbatim as an MCP ``inputSchema``:
it hoists nested models into ``$defs``/``$ref``, emits a ``title`` on every
property, spells optional fields as ``anyOf: [X, {"type": "null"}]`` with
``default: null``, and carries the model docstring as a top-level
``description``. :func:`input_schema_for` post-processes all four so the
advertised shape is self-contained and title-free, and an optional string is
``"type": ["string", "null"]`` — flat like the hand-written schemas were, but
accepting ``null`` exactly where the model does.
``tests/test_gateway_tool_schemas.py`` pins that post-processing.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel

#: Advertised for gateway tools that take no arguments at all.
NO_ARGUMENTS_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}

_REF_PREFIX = "#/$defs/"
_NULL_SCHEMA: dict[str, Any] = {"type": "null"}


def input_schema_for(model: type[BaseModel] | None) -> dict[str, Any]:
    """Return the MCP ``inputSchema`` for a gateway tool argument model.

    ``None`` means the tool takes no arguments.
    """
    if model is None:
        return deepcopy(NO_ARGUMENTS_SCHEMA)
    raw = model.model_json_schema(by_alias=True, mode="validation")
    defs = raw.pop("$defs", {})
    schema = _normalize(raw, defs)
    # The Tool carries its own description; the model docstring is not it.
    schema.pop("description", None)
    return schema


def _normalize(node: Any, defs: dict[str, Any]) -> Any:
    """Inline ``$ref``s, drop ``title``s, and collapse nullable ``anyOf``s."""
    if isinstance(node, list):
        return [_normalize(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith(_REF_PREFIX):
            raise ValueError(f"unsupported $ref in gateway tool schema: {ref}")
        target = _normalize(deepcopy(defs[ref[len(_REF_PREFIX) :]]), defs)
        siblings = _normalize({k: v for k, v in node.items() if k != "$ref"}, defs)
        return {**target, **siblings}
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title":
            continue  # pydantic's per-field/model title noise
        if key == "properties" and isinstance(value, dict):
            # Keys here are property NAMES (a field may be called "title").
            out[key] = {name: _normalize(prop, defs) for name, prop in value.items()}
        else:
            out[key] = _normalize(value, defs)
    return _collapse_nullable(out)


def _collapse_nullable(node: dict[str, Any]) -> dict[str, Any]:
    """``anyOf: [X, null]`` + ``default: null`` -> ``X`` with ``null`` still allowed.

    pydantic spells ``X | None = None`` as that ``anyOf``. The advertised
    schema keeps ``X``'s keywords flat (no ``anyOf``) and adds ``"null"`` to
    its ``type`` (and to its ``enum``, if any), so the gate accepts exactly
    what the model accepts: the field omitted, ``null``, or an ``X``. A
    nullable field WITHOUT a ``None`` default (required-but-nullable) is left
    as pydantic wrote it -- the collapse is only defined for the optional case.
    """
    any_of = node.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2 or _NULL_SCHEMA not in any_of:
        return node
    if "default" not in node or node["default"] is not None:
        return node
    (inner,) = [branch for branch in any_of if branch != _NULL_SCHEMA]
    rest = {k: v for k, v in node.items() if k not in ("anyOf", "default")}
    out = {**inner, **rest}
    inner_type = out.get("type")
    if isinstance(inner_type, str):
        out["type"] = [inner_type, "null"]
    elif isinstance(inner_type, list) and "null" not in inner_type:
        out["type"] = [*inner_type, "null"]
    if isinstance(out.get("enum"), list) and None not in out["enum"]:
        out["enum"] = [*out["enum"], None]
    return out
