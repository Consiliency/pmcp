"""Describe a rejected gateway-tool argument without the value that failed.

A gateway tool's arguments are checked twice: by the advertised JSON Schema
(``GatewayServer._handle_call_tool``'s gate) and by the tool's pydantic model
(``pmcp.types.*Input``). The text both libraries render for a failure embeds
the rejected value -- jsonschema's ``message`` (``'sk-...' is not of type
'object'``), pydantic's ``input_value=...`` and a custom validator's own
message -- and it went verbatim into the tool response and the gateway log
(Consiliency/pmcp#297). A caller can put a secret in any argument, so that
text may carry one.

These functions render a rejection from its *structure* instead, never from
its text:

- **where**: the failing location, as a JSON path (``$.options.timeout_ms``).
  A segment survives only as a list index or as a name an argument model or
  schema declares; any other key was chosen by the caller and may itself be a
  secret, so it becomes ``*``.
- **why**: a fixed phrase for the failing keyword (jsonschema) or error type
  (pydantic), filled only from the constraint the gateway's own schema or
  model defines (``must be at most 128 characters``). An unknown keyword or
  type renders as ``is invalid``.

Neither reads a value: not jsonschema's ``message``, ``instance``,
``validator_value``, ``context`` or ``cause``, and not pydantic's ``msg``,
``input`` or any ``ctx`` entry outside :data:`_CONSTRAINT_CONTEXT`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import jsonschema
from pydantic import ValidationError
from pydantic_core import PydanticCustomError

#: Stands in for a path segment the caller chose.
REDACTED_SEGMENT = "*"

#: What a rejection reads as if describing it fails (an in-process mapping
#: whose lookup raises, say); the exception itself is never rendered.
_UNDESCRIBED = "$: is invalid"

#: At most this many pydantic errors are described; the rest are counted.
_MAX_MODEL_ERRORS = 5

# --- pmcp's own validator errors -------------------------------------------
# A custom validator raises one of these instead of ``ValueError(...)``: its
# type selects a fixed message below, so no validator can interpolate the
# value it rejects into what the caller or the log sees.

CORRELATION_ID_CHARSET = "correlation_id_charset"
SCOPED_CORRELATION_INCOMPLETE = "scoped_correlation_incomplete"
PACKAGE_NAME_INVALID = "package_name_invalid"

_PMCP_MESSAGES: dict[str, str] = {
    CORRELATION_ID_CHARSET: "correlation IDs may contain only alphanumerics and ._:-",
    SCOPED_CORRELATION_INCOMPLETE: (
        "scoped advisor correlation fields must be supplied together"
    ),
    PACKAGE_NAME_INVALID: (
        "package must be a valid npm/pypi identifier (no leading dash, "
        "whitespace, path separators, or shell metacharacters)"
    ),
}


def argument_error(error_type: str) -> PydanticCustomError:
    """The error a pmcp validator raises for ``error_type`` (a constant above)."""
    return PydanticCustomError(error_type, _PMCP_MESSAGES[error_type])


# --- pydantic ----------------------------------------------------------------

#: The ``ctx`` keys a phrase below may name. Each is a constraint the model
#: declares (a length, a bound, a pattern, the allowed literals), never read
#: from the input. ``actual_length``, ``error``, ``tag`` and the rest are
#: left out on purpose.
_CONSTRAINT_CONTEXT = frozenset(
    {"min_length", "max_length", "pattern", "expected", "gt", "ge", "lt", "le"}
)

_MODEL_PHRASES: dict[str, str] = {
    "missing": "is required",
    "extra_forbidden": "is not an accepted argument",
    "string_type": "must be a string",
    "string_too_short": "must be at least {min_length} characters",
    "string_too_long": "must be at most {max_length} characters",
    "string_pattern_mismatch": "must match the pattern {pattern}",
    "int_type": "must be an integer",
    "int_parsing": "must be an integer",
    "int_from_float": "must be an integer",
    "float_type": "must be a number",
    "float_parsing": "must be a number",
    "bool_type": "must be a boolean",
    "bool_parsing": "must be a boolean",
    "dict_type": "must be an object",
    "model_type": "must be an object",
    "model_attributes_type": "must be an object",
    "list_type": "must be an array",
    "too_short": "must have at least {min_length} items",
    "too_long": "must have at most {max_length} items",
    "literal_error": "must be {expected}",
    "enum": "must be {expected}",
    "greater_than": "must be greater than {gt}",
    "greater_than_equal": "must be greater than or equal to {ge}",
    "less_than": "must be less than {lt}",
    "less_than_equal": "must be less than or equal to {le}",
    **_PMCP_MESSAGES,
}


def _argument_names() -> frozenset[str]:
    """Every field name and alias of every gateway argument model.

    Written by pmcp's authors, never by a caller, so a location segment equal
    to one discloses nothing the advertised schemas do not.
    """
    from pmcp.types import GatewayArguments

    names: set[str] = set()
    pending: list[type] = [GatewayArguments]
    while pending:
        model = pending.pop()
        pending.extend(model.__subclasses__())
        for name, field in getattr(model, "model_fields", {}).items():
            names.add(name)
            if isinstance(field.alias, str):
                names.add(field.alias)
    return frozenset(names)


def _render_path(segments: Iterable[str | int | None]) -> str:
    path = "$"
    for segment in segments:
        if segment is None:
            path += f".{REDACTED_SEGMENT}"
        elif isinstance(segment, int):
            path += f"[{segment}]"
        else:
            path += f".{segment}"
    return path


def _model_error_path(
    loc: tuple[Any, ...], arguments: Any, declared: frozenset[str]
) -> list[str | int | None]:
    """``loc`` with every caller-chosen key redacted; reads only container
    types from ``arguments`` (to tell a list index from an ``int`` key)."""
    path: list[str | int | None] = []
    node = arguments
    for segment in loc:
        if isinstance(node, list) and type(segment) is int:
            path.append(segment)
        elif type(segment) is str and segment in declared:
            path.append(segment)
        else:
            path.append(None)
        try:
            node = node[segment]
        except (KeyError, IndexError, TypeError):
            node = None
    return path


def model_error_path(
    error: ValidationError, schema: Any, arguments: Any
) -> list[str | int | None]:
    """The first error's location, redacted as :func:`schema_error_path` is."""
    declared = _argument_names() | declared_property_names(schema)
    items = error.errors(include_url=False, include_input=False, include_context=False)
    loc = tuple(items[0]["loc"]) if items else ()
    return _model_error_path(loc, arguments, declared)


def _model_phrase(error_type: Any, ctx: Any) -> str:
    phrase = _MODEL_PHRASES.get(error_type) if isinstance(error_type, str) else None
    if phrase is None:
        return "is invalid"
    context = ctx if isinstance(ctx, dict) else {}
    fields = {key: context.get(key) for key in _CONSTRAINT_CONTEXT if key in context}
    try:
        return phrase.format(**fields)
    except (KeyError, IndexError, ValueError):
        return "is invalid"


def describe_model_error(error: ValidationError, schema: Any, arguments: Any) -> str:
    """``$.<loc>: <phrase>`` for each of pydantic's errors, joined by ``; ``."""
    try:
        return _describe_model_error(error, schema, arguments)
    except Exception:
        return _UNDESCRIBED


def _describe_model_error(error: ValidationError, schema: Any, arguments: Any) -> str:
    declared = _argument_names() | declared_property_names(schema)
    items = error.errors(include_url=False, include_input=False, include_context=True)
    parts = [
        f"{_render_path(_model_error_path(tuple(item['loc']), arguments, declared))}: "
        f"{_model_phrase(item['type'], item.get('ctx'))}"
        for item in items[:_MAX_MODEL_ERRORS]
    ]
    if len(items) > _MAX_MODEL_ERRORS:
        parts.append(f"and {len(items) - _MAX_MODEL_ERRORS} more")
    return "; ".join(parts)


# --- jsonschema --------------------------------------------------------------


def declared_property_names(schema: Any) -> frozenset[str]:
    """Every key of every ``properties`` map anywhere in ``schema``.

    These strings are written by the schema's author, never by a caller, so a
    path segment equal to one of them discloses nothing the schema does not.
    """
    names: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                names.update(key for key in properties if isinstance(key, str))
            for child in node.values():
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    collect(schema)
    return frozenset(names)


def schema_error_path(
    error: jsonschema.ValidationError, schema: Any, arguments: Any
) -> list[str | int | None]:
    """The failing instance location, with every caller-chosen key redacted.

    Walks ``error.absolute_path`` (not ``.path``, which is relative when
    ``best_match`` returns an ``anyOf`` child). A segment survives only as an
    array index (an ``int`` whose container is a list) or as a key the schema
    declares under some ``properties``. Any other key -- one matched by
    ``additionalProperties`` or ``patternProperties``, or a non-``str`` key a
    caller built in-process (an ``int``, or a ``str`` subclass whose ``__eq__``
    could pass the membership test) -- is chosen by the caller and may itself
    be a secret, so it becomes ``None``. The walk reads only container types
    from ``arguments``, never a value.
    """
    declared = declared_property_names(schema)
    path: list[str | int | None] = []
    node = arguments
    for segment in error.absolute_path:
        if isinstance(node, list) and type(segment) is int:
            path.append(segment)
        elif isinstance(node, dict) and type(segment) is str and segment in declared:
            path.append(segment)
        else:
            path.append(None)
        try:
            node = node[segment]
        except (KeyError, IndexError, TypeError):
            node = None
    return path


def schema_error_keyword(error: jsonschema.ValidationError, schema: Any) -> str | None:
    """The failing keyword, if it is one the schema's draft defines."""
    keywords = jsonschema.validators.validator_for(schema).VALIDATORS
    validator = error.validator
    return validator if isinstance(validator, str) and validator in keywords else None


def _schema_node(schema: Any, schema_path: Iterable[Any]) -> Any:
    """The node of *our* schema that holds the failing keyword."""
    node = schema
    for segment in schema_path:
        node = node[segment]
    return node


def _count(number: Any, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _type_names(value: Any) -> str:
    names = value if isinstance(value, list) else [value]
    return " or ".join(str(name) for name in names)


def _missing_required(
    required: Any, error: jsonschema.ValidationError, arguments: Any
) -> str | None:
    """The first name ``required`` lists that the object lacks. Only the
    schema's own names are returned; the object is only tested for them."""
    node = arguments
    for segment in error.absolute_path:
        try:
            node = node[segment]
        except (KeyError, IndexError, TypeError):
            return None
    if not isinstance(node, dict) or not isinstance(required, list):
        return None
    for name in required:
        if isinstance(name, str) and name not in node:
            return name
    return None


def _schema_phrase(
    keyword: str | None,
    error: jsonschema.ValidationError,
    schema: Any,
    arguments: Any,
) -> tuple[str, str | None]:
    """``(phrase, extra path segment)`` for ``keyword``, read from our schema."""
    if keyword is None:
        return "is invalid", None
    try:
        node = _schema_node(schema, list(error.absolute_schema_path)[:-1])
        constraint = node[keyword]
    except (KeyError, IndexError, TypeError):
        return "is invalid", None
    if keyword == "type":
        return f"must be of type {_type_names(constraint)}", None
    if keyword == "enum":
        return f"must be one of {json.dumps(constraint)}", None
    if keyword == "const":
        return f"must be {json.dumps(constraint)}", None
    if keyword == "pattern":
        return f"must match the pattern {constraint}", None
    if keyword == "minLength":
        return f"must be at least {_count(constraint, 'character')}", None
    if keyword == "maxLength":
        return f"must be at most {_count(constraint, 'character')}", None
    if keyword == "minItems":
        return f"must have at least {_count(constraint, 'item')}", None
    if keyword == "maxItems":
        return f"must have at most {_count(constraint, 'item')}", None
    if keyword == "minimum":
        return f"must be greater than or equal to {constraint}", None
    if keyword == "maximum":
        return f"must be less than or equal to {constraint}", None
    if keyword == "exclusiveMinimum":
        return f"must be greater than {constraint}", None
    if keyword == "exclusiveMaximum":
        return f"must be less than {constraint}", None
    if keyword == "required":
        return "is required", _missing_required(constraint, error, arguments)
    if keyword in ("additionalProperties", "unevaluatedProperties"):
        return "has a property that is not accepted", None
    return f"fails the schema's {keyword} constraint", None


def describe_schema_error(
    error: jsonschema.ValidationError, schema: Any, arguments: Any
) -> str:
    """``$.<path>: <phrase>`` for one jsonschema error, from its structure."""
    try:
        return _describe_schema_error(error, schema, arguments)
    except Exception:
        return _UNDESCRIBED


def _describe_schema_error(
    error: jsonschema.ValidationError, schema: Any, arguments: Any
) -> str:
    path = schema_error_path(error, schema, arguments)
    phrase, missing = _schema_phrase(
        schema_error_keyword(error, schema), error, schema, arguments
    )
    if missing is not None:
        path = [*path, missing]
    return f"{_render_path(path)}: {phrase}"


def describe_argument_error(
    error: BaseException, schema: Any, arguments: Any
) -> str | None:
    """A value-free description of ``error`` if it is an argument-validation
    error of either library, else ``None``."""
    if isinstance(error, ValidationError):
        return describe_model_error(error, schema, arguments)
    if isinstance(error, jsonschema.ValidationError):
        return describe_schema_error(error, schema, arguments)
    return None
