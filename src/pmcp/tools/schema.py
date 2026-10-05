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
advertised shape is self-contained and title-free, and an optional field is
``anyOf: [X, {"type": "null"}]`` with no ``default: null`` -- accepting
``null`` exactly where the model does.

That nullable spelling is the one portable across host schema converters
(Consiliency/pmcp#369): ``null`` is a type branch, never an ``enum`` member,
a ``const``, a ``default`` or an entry in a ``type`` array. OpenCode's Google
provider stringifies every ``enum`` member (``null`` became the string
``"null"``) and turns ``type: [X, "null"]`` into a typeless node whose
``nullable`` the AI SDK then drops; the ``anyOf`` form is the one the AI SDK
folds into Gemini's canonical ``{type: X, nullable: true}``.
``tests/test_gateway_tool_schemas.py`` and
``tests/test_nullable_schema_portability.py`` pin that post-processing.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
import math
from typing import Any

import jsonschema
from jsonschema.exceptions import best_match
from pydantic import BaseModel

#: Advertised for gateway tools that take no arguments at all.
NO_ARGUMENTS_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}

_REF_PREFIX = "#/$defs/"


def _is_json_number(checker: Any, instance: Any) -> bool:
    """JSON Schema's ``number``, minus NaN and +-Infinity (Consiliency/pmcp#298).

    Neither is a JSON number (RFC 8259 s6), yet Python's ``json`` and pydantic's
    parser both read ``NaN``/``Infinity``/``1e400`` off the wire, and no bound
    keyword refuses NaN: every comparison with it is false. ``integer`` needs no
    change -- its check is ``float.is_integer()``, which both already fail.
    """
    return jsonschema.Draft202012Validator.TYPE_CHECKER.is_type(
        instance, "number"
    ) and not (isinstance(instance, float) and not math.isfinite(instance))


#: The validator the transport gate runs every advertised ``inputSchema`` with.
GATE_VALIDATOR = jsonschema.validators.extend(
    jsonschema.Draft202012Validator,
    type_checker=jsonschema.Draft202012Validator.TYPE_CHECKER.redefine(
        "number", _is_json_number
    ),
)
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
    """Inline ``$ref``s, drop ``title``s, and canonicalise nullable ``anyOf``s."""
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
    return _canonical_nullable(out)


def _canonical_nullable(node: dict[str, Any]) -> dict[str, Any]:
    """``anyOf: [X, null]`` -> ``anyOf: [X, {"type": "null"}]``, X first, no ``default: null``.

    pydantic spells ``X | None`` (with or without ``= None``) as a two-branch
    ``anyOf``. The advertised schema keeps that union -- the gate accepts
    exactly what the model accepts: an ``X``, ``null``, or (when optional)
    the field omitted -- and drops a ``default: null``, so ``null`` reaches a
    host only as the ``{"type": "null"}`` branch (Consiliency/pmcp#369).
    """
    any_of = node.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2 or _NULL_SCHEMA not in any_of:
        return node
    (inner,) = [branch for branch in any_of if branch != _NULL_SCHEMA]
    out = {k: v for k, v in node.items() if k != "anyOf"}
    if "description" in out:
        # The field's description, not an inlined model's docstring: a host
        # that folds the union (the AI SDK) would let the branch's win.
        inner = {k: v for k, v in inner.items() if k != "description"}
    if "default" in out and out["default"] is None:
        del out["default"]
    out["anyOf"] = [inner, dict(_NULL_SCHEMA)]
    return out


def _is_nullable_union(schema: Any) -> bool:
    any_of = schema.get("anyOf") if isinstance(schema, dict) else None
    return isinstance(any_of, list) and len(any_of) == 2 and any_of[1] == _NULL_SCHEMA


def _rerooted(
    parent: jsonschema.ValidationError, child: jsonschema.ValidationError
) -> jsonschema.ValidationError:
    """``child`` (an error inside ``parent``'s ``anyOf``) as a top-level error."""
    message, validator, validator_value, schema = (
        child.message,
        child.validator,
        child.validator_value,
        child.schema,
    )
    if child.validator == "type" and not child.relative_path:
        # The value is the wrong type for X; it is not null either. Spelled as
        # the ``type: [X, "null"]`` keyword spelled it, schema included, so the
        # error ranks (``_matches_type``) as that one did.
        types = [child.validator_value, "null"]
        message = f"{child.instance!r} is not of type {', '.join(map(repr, types))}"
        validator_value = types
        schema = {**child.schema, "type": types}
    return jsonschema.ValidationError(
        message,
        validator=validator,
        validator_value=validator_value,
        instance=child.instance,
        schema=schema,
        path=deque([*parent.absolute_path, *child.relative_path]),
        schema_path=deque([*parent.absolute_schema_path, *child.relative_schema_path]),
        context=list(child.context),
        type_checker=child._type_checker,
    )


def _unfolded(error: jsonschema.ValidationError) -> list[jsonschema.ValidationError]:
    """``error``, or -- for a non-null value a nullable union refused -- the
    errors its value branch produced, in order, as top-level errors."""
    if (
        error.validator != "anyOf"
        or not _is_nullable_union(error.schema)
        or error.instance is None
    ):
        return [error]
    return [
        unfolded
        for child in error.context
        if child.relative_schema_path[0] == 0  # the value branch, not the null one
        for unfolded in _unfolded(_rerooted(error, child))
    ]


def gate_error_for(
    instance: Any, schema: dict[str, Any]
) -> jsonschema.ValidationError | None:
    """The error the gate reports for ``instance``, or ``None`` if it passes.

    ``anyOf: [X, {"type": "null"}]`` refuses a bad value as a whole ("... is
    not valid under any of the given schemas"). Before ``best_match`` ranks
    the errors, each such refusal is replaced by the errors ``X`` produced,
    in place: so the gate picks the same error, with the same message,
    keyword and path, as under the ``type: [X, "null"]`` spelling
    (Consiliency/pmcp#369) -- the one difference being that an ``enum``'s
    message no longer lists ``None``. Which values pass is decided by the
    validator alone and is unchanged. A failure in the unfold or the ranking
    falls back to ``jsonschema``'s own choice, and failing that to the first
    raw error: a refused value always yields an error, so a refusal is never
    lost (nor left out of the audit).
    """
    errors = list(GATE_VALIDATOR(schema).iter_errors(instance))
    if not errors:
        return None
    chosen: jsonschema.ValidationError | None
    try:
        chosen = best_match(
            [unfolded for error in errors for unfolded in _unfolded(error)]
        )
    except Exception:  # noqa: BLE001 -- never lose a refusal to the fold
        chosen = None
    if chosen is None:
        try:
            chosen = best_match(errors)
        except Exception:  # noqa: BLE001 -- nor to the ranking
            chosen = None
    return chosen if chosen is not None else errors[0]


def validate_at_gate(instance: Any, schema: dict[str, Any]) -> None:
    """``jsonschema.validate`` as the transport gate runs it: raise the error
    :func:`gate_error_for` chooses. Every caller that reports a gate refusal
    (the server, the migration guide's checker) goes through this."""
    GATE_VALIDATOR.check_schema(schema)  # as ``jsonschema.validate`` does
    error = gate_error_for(instance, schema)
    if error is not None:
        raise error
