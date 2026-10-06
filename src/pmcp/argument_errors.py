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
``input`` or ``ctx`` (a constraint is read from the gateway's own schema, so
a custom error that reuses a pydantic type cannot smuggle a value in through
its context).

The same rule covers every other place pmcp turns an exception into text
(Consiliency/pmcp#297, rev 2): :func:`exception_text` is ``str(error)``
unless the error is, or embeds the text of, a validation error, and
:func:`safe_exc_info` withholds a traceback whose chain holds one.
``tests/test_argument_error_echo.py`` checks every ``except`` in ``src/pmcp``
(bar the CLI) that can catch one renders it only through these.
"""

from __future__ import annotations

import functools
import json
import logging
import sys
import traceback
from collections.abc import Iterable, Iterator
from types import ModuleType
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
PACKAGE_PATTERN_VERSIONED = "package_pattern_versioned"

_PMCP_MESSAGES: dict[str, str] = {
    CORRELATION_ID_CHARSET: "correlation IDs may contain only alphanumerics and ._:-",
    SCOPED_CORRELATION_INCOMPLETE: (
        "scoped advisor correlation fields must be supplied together"
    ),
    PACKAGE_NAME_INVALID: (
        "package must be a valid npm/pypi identifier (no leading dash, "
        "whitespace, path separators, or shell metacharacters)"
    ),
    PACKAGE_PATTERN_VERSIONED: (
        "a package pattern names a version; package patterns match the "
        "package name only"
    ),
}


def argument_error(error_type: str) -> PydanticCustomError:
    """The error a pmcp validator raises for ``error_type`` (a constant above)."""
    return PydanticCustomError(error_type, _PMCP_MESSAGES[error_type])


# --- pydantic ----------------------------------------------------------------

#: Error types whose phrase needs no constraint.
_FIXED_PHRASES: dict[str, str] = {
    "missing": "is required",
    "extra_forbidden": "is not an accepted argument",
    "string_type": "must be a string",
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
    **_PMCP_MESSAGES,
}

#: Error types whose phrase names a constraint: ``(the JSON Schema keyword
#: that holds it in the gateway's own schema, the phrase with it, the phrase
#: without it)``. The constraint is read from the schema node at the error's
#: location, never from pydantic's ``ctx`` (Consiliency/pmcp#297 rev 2, N2).
_CONSTRAINED_PHRASES: dict[str, tuple[str, str, str]] = {
    "string_too_short": ("minLength", "must be at least {characters}", "is too short"),
    "string_too_long": ("maxLength", "must be at most {characters}", "is too long"),
    "string_pattern_mismatch": (
        "pattern",
        "must match the pattern {text}",
        "does not match the required pattern",
    ),
    "too_short": ("minItems", "must have at least {items}", "has too few items"),
    "too_long": ("maxItems", "must have at most {items}", "has too many items"),
    "literal_error": ("enum", "must be one of {json}", "is not an allowed value"),
    "enum": ("enum", "must be one of {json}", "is not an allowed value"),
    "greater_than": (
        "exclusiveMinimum",
        "must be greater than {number}",
        "is too small",
    ),
    "greater_than_equal": (
        "minimum",
        "must be greater than or equal to {number}",
        "is too small",
    ),
    "less_than": ("exclusiveMaximum", "must be less than {number}", "is too large"),
    "less_than_equal": (
        "maximum",
        "must be less than or equal to {number}",
        "is too large",
    ),
}


#: `_declared_names` for a given set of loaded modules (its key).
_declared_cache: tuple[int, frozenset[str]] | None = None


def _declared_names() -> frozenset[str]:
    """Every field name and alias of every pydantic model pmcp defines.

    Written by pmcp's authors, never by a caller or a downstream server, so a
    location segment equal to one discloses nothing pmcp's own source does not.
    Read from the ``pmcp.*`` modules' namespaces, and recomputed only when a
    module has been imported since.
    """
    global _declared_cache
    from pydantic import BaseModel

    import pmcp.types  # noqa: F401 -- the argument models, at least

    key = len(sys.modules)
    if _declared_cache is not None and _declared_cache[0] == key:
        return _declared_cache[1]
    names: set[str] = set()
    for module_name, module in list(sys.modules.items()):
        if module is None or not module_name.startswith("pmcp."):
            continue
        for value in list(vars(module).values()):
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module_name
            ):
                for name, field in value.model_fields.items():
                    names.add(name)
                    if isinstance(field.alias, str):
                        names.add(field.alias)
    _declared_cache = (key, frozenset(names))
    return _declared_cache[1]


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
    declared = _declared_names() | declared_property_names(schema)
    items = error.errors(include_url=False, include_input=False, include_context=False)
    loc = tuple(items[0]["loc"]) if items else ()
    return _model_error_path(loc, arguments, declared)


def _schema_node_at(schema: Any, loc: tuple[Any, ...]) -> Any:
    """The node of the gateway's own schema at a pydantic location, or None."""
    node = schema
    for segment in loc:
        if not isinstance(node, dict):
            return None
        if type(segment) is int:
            node = node.get("items")
        else:
            properties = node.get("properties")
            node = properties.get(segment) if isinstance(properties, dict) else None
    return node if isinstance(node, dict) else None


def _constraint_text(kind: str, value: Any) -> str | None:
    if kind in ("characters", "items"):
        noun = kind[:-1]
        return _count(value, noun) if type(value) is int else None
    if kind == "number":
        return str(value) if type(value) in (int, float) else None
    if kind == "text":
        return value if isinstance(value, str) else None
    if isinstance(value, list):  # "json": an enum of the schema's literals
        return json.dumps(value)
    return None


def _model_phrase(error_type: Any, node: Any) -> str:
    """The phrase for ``error_type``, its constraint read from ``node`` (the
    gateway's schema at the error's location; ``None`` when there is none)."""
    if not isinstance(error_type, str):
        return "is invalid"
    if error_type in _FIXED_PHRASES:
        return _FIXED_PHRASES[error_type]
    if error_type not in _CONSTRAINED_PHRASES:
        return "is invalid"
    keyword, with_constraint, without = _CONSTRAINED_PHRASES[error_type]
    if not isinstance(node, dict) or keyword not in node:
        return without
    kind = with_constraint.split("{", 1)[1].split("}", 1)[0]
    text = _constraint_text(kind, node[keyword])
    return without if text is None else with_constraint.replace("{" + kind + "}", text)


def describe_model_error(error: ValidationError, schema: Any, arguments: Any) -> str:
    """``$.<loc>: <phrase>`` for each of pydantic's errors, joined by ``; ``."""
    try:
        return _describe_model_error(error, schema, arguments)
    except Exception:
        return _UNDESCRIBED


def _describe_model_error(error: ValidationError, schema: Any, arguments: Any) -> str:
    declared = _declared_names() | declared_property_names(schema)
    items = error.errors(include_url=False, include_input=False, include_context=False)
    parts = []
    for item in items[:_MAX_MODEL_ERRORS]:
        loc = tuple(item["loc"])
        path = _render_path(_model_error_path(loc, arguments, declared))
        parts.append(
            f"{path}: {_model_phrase(item['type'], _schema_node_at(schema, loc))}"
        )
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
        # X's own type inside a nullable union is X or null (rev 20,
        # Consiliency/pmcp#371): the union is read from our schema.
        schema_path = list(error.absolute_schema_path)
        if schema_path[-3:-1] == ["anyOf", 0]:
            try:
                union = _schema_node(schema, schema_path[:-3])
            except (KeyError, IndexError, TypeError):
                union = None
            from pmcp.tools.schema import _is_nullable_union

            if _is_nullable_union(union):
                names = constraint if isinstance(constraint, list) else [constraint]
                constraint = [*names, "null"]
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
    if keyword == "anyOf":
        # A nullable union refused as a whole (Consiliency/pmcp#371): the gate
        # reports what X refused instead (`gate_error_for`); an error that did
        # not come through it is read from our schema only.
        from pmcp.tools.schema import _is_nullable_union

        if _is_nullable_union(node):
            inner = constraint[0] if isinstance(constraint, list) else None
            kind = inner.get("type") if isinstance(inner, dict) else None
            if kind is not None:
                return f"must be null or a valid {_type_names(kind)}", None
            return "must be null or match its schema", None
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
    error of either library, checked against ``schema``, else ``None``."""
    if isinstance(error, ValidationError):
        return describe_model_error(error, schema, arguments)
    if isinstance(error, jsonschema.ValidationError):
        return describe_schema_error(error, schema, arguments)
    return None


# --- any exception pmcp renders ------------------------------------------------


def _parse_error_types() -> tuple[type[BaseException], ...]:
    """The parse errors of every structured-text parser pmcp uses (rev 6).

    Their text can quote what they rejected: PyYAML's ``MarkedYAMLError``
    renders a snippet of the input around the mark, and a constructor error
    names the input's tag; ``tomllib``'s message can quote a key. JSON's
    message is fixed vocabulary, but its ``doc`` holds the input, so it is
    rendered the same way for one rule. python-dotenv does not raise on bad
    input (it logs the line number only).
    """
    import yaml

    types: list[type[BaseException]] = [yaml.YAMLError, json.JSONDecodeError]
    try:
        import tomllib  # Python 3.11+

        types.append(tomllib.TOMLDecodeError)
    except ImportError:  # pragma: no cover - Python 3.10
        pass
    return tuple(types)


_PARSE_ERRORS: tuple[type[BaseException], ...] = ()


def _is_parse_error(error: BaseException) -> bool:
    """Whether a value-bearing error is a parser's (and so is described by
    :func:`_parse_text`). Which errors are value-bearing at all, and the
    :class:`pmcp.parsing.ParseError` exemption, are the registry's decision
    alone (:func:`_is_validation_error`, rev 18)."""
    global _PARSE_ERRORS
    if not _PARSE_ERRORS:
        _PARSE_ERRORS = _parse_error_types()
    return isinstance(error, _PARSE_ERRORS)


def _value_bearing_types() -> tuple[type[BaseException], ...]:
    """The registry of value-bearing exception types (rev 18): every type
    whose own text, ``repr`` or attributes can carry the input it rejected.
    It is the one place that decides which exceptions are described from
    their structure, and which wrappers are never rendered from their own
    message: pydantic's and jsonschema's validation errors (jsonschema's
    ``SchemaError`` renders the schema it rejected), and every parser's
    error (:func:`_parse_error_types`). :data:`_VALUE_FREE_TYPES` is the
    registry's only exemption."""
    return (
        ValidationError,
        jsonschema.ValidationError,
        jsonschema.SchemaError,
        *_parse_error_types(),
        *_response_decode_types(),
    )


#: Every HTTP client module pmcp uses -- the clients it imports and their
#: transports -- and the base classes of the exceptions whose message can
#: carry bytes of a response pmcp rejected: a status line, a header, chunk
#: framing, a body, a MIME type, a reason phrase (rev 22 round-20 codex F001;
#: rev 23 round-21 claude/grok/codex F001). Registered by class, so every
#: subclass is covered. `tests/test_parse_error_echo.py` derives the modules
#: from pmcp's imports and the clients' requirements, and pins every
#: exception class each defines as registered here or value-free, exactly.
HTTP_RESPONSE_ERRORS: dict[str, tuple[str, ...]] = {
    "aiohttp": (
        "ClientResponseError",  # parser failures, ContentTypeError, statuses
        "ClientPayloadError",  # chunk framing and body transfer errors
        "ServerDisconnectedError",  # may carry the partial response message
        "BadContentDispositionHeader",
        "BadContentDispositionParam",
        "RedirectClientError",  # a rejected `Location` from the response
        "WebSocketError",  # a server's close message
    ),
    "aiohttp.http_exceptions": ("HttpProcessingError",),  # every parser error
    "httpx": ("ProtocolError", "DecodingError", "HTTPStatusError"),
    "httpx2": ("ProtocolError", "DecodingError", "HTTPStatusError", "SSEError"),
    "httpcore": ("ProtocolError",),
    "httpcore2": ("ProtocolError",),
    "h11": ("ProtocolError",),
    "http.client": ("HTTPException",),  # BadStatusLine, LineTooLong, ...
    "urllib.error": ("HTTPError",),  # the reason phrase
}

#: Of those, the ones that failed to decode a body that parsed: described as
#: such (rev 22's wording).
_DECODE_ERROR_NAMES = frozenset({"DecodingError", "ContentTypeError"})


@functools.cache
def _response_decode_types() -> tuple[type[BaseException], ...]:
    """The registered HTTP response exception types, plus
    ``UnicodeDecodeError`` (``.text()``/``.decode()`` of a body)."""
    import importlib

    types: list[type[BaseException]] = [UnicodeDecodeError]
    for module_name, names in HTTP_RESPONSE_ERRORS.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError:  # pragma: no cover - an optional client
            continue
        types.extend(getattr(module, name) for name in names)
    return tuple(types)


def _is_response_decode_error(error: BaseException) -> bool:
    return isinstance(error, _response_decode_types()) and not _is_parse_error(error)


def _response_status(error: BaseException) -> int | None:
    """The HTTP status a response error records, as an int, if any."""
    for value in (
        getattr(error, "status", None),
        getattr(error, "code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        if type(value) is int:
            return value
    return None


_VALUE_BEARING: tuple[type[BaseException], ...] = ()


def _value_free_types() -> tuple[type[BaseException], ...]:
    """Subclasses of registered types whose text is value-free by
    construction: :class:`pmcp.parsing.ParseError` (rev 7), raised outside
    the parser's ``except`` so that it chains nothing."""
    from pmcp.parsing import ParseError

    return (ParseError,)


def _is_validation_error(error: BaseException) -> bool:
    """A value-bearing error: an instance of a registered type
    (:func:`_value_bearing_types`) that is not exempt."""
    global _VALUE_BEARING
    if not _VALUE_BEARING:
        _VALUE_BEARING = _value_bearing_types()
    return isinstance(error, _VALUE_BEARING) and not isinstance(
        error, _value_free_types()
    )


def _parse_text(error: BaseException) -> str:
    """A parse error without the input: the format, the error's class and,
    where the parser records it, the line and column (never PyYAML's
    ``problem``/``context`` text or snippet, JSON's ``msg``/``doc``, or
    TOML's message)."""
    kind = "JSON" if isinstance(error, json.JSONDecodeError) else None
    line = column = None
    if kind == "JSON":
        line, column = getattr(error, "lineno", None), getattr(error, "colno", None)
    elif type(error).__module__.startswith("yaml"):
        kind = "YAML"
        mark = getattr(error, "problem_mark", None) or getattr(
            error, "context_mark", None
        )
        if mark is not None:
            line, column = getattr(mark, "line", None), getattr(mark, "column", None)
            line = line + 1 if isinstance(line, int) else None
            column = column + 1 if isinstance(column, int) else None
    else:
        kind = "TOML"
        line, column = getattr(error, "lineno", None), getattr(error, "colno", None)
    where = (
        f" at line {line}, column {column}"
        if isinstance(line, int) and isinstance(column, int)
        else ""
    )
    return f"could not parse {kind} ({type(error).__name__}){where}"


def _chain(error: BaseException) -> Iterator[BaseException]:
    """``error``, its ``__cause__``/``__context__`` chain and every exception
    in a group, each once."""
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
        members = getattr(current, "exceptions", None)
        if isinstance(members, (tuple, list)):
            pending.extend(m for m in members if isinstance(m, BaseException))


def _validation_text(error: BaseException) -> str:
    """A validation error described without the schema that raised it: the
    path (names pmcp's models declare, list indexes, ``*``) and a phrase
    without constraints, since the schema is not known here (rev 2, N6).
    A parse error is described by :func:`_parse_text` (rev 6)."""
    if _is_parse_error(error):
        return _parse_text(error)
    if isinstance(error, UnicodeDecodeError):
        # The codec and class only: never the undecodable bytes.
        return f"could not decode {error.encoding} text (UnicodeDecodeError)"
    if _is_response_decode_error(error):
        name = type(error).__name__
        if name in _DECODE_ERROR_NAMES:
            # Format and class only: never the MIME type or the body.
            return f"could not decode an HTTP response ({name})"
        # The class and the status number: never the status line, a header,
        # the framing, the body or the reason phrase (rev 23).
        status = _response_status(error)
        where = f", status {status}" if status is not None else ""
        return f"rejected an HTTP response ({name}{where})"
    if isinstance(error, ValidationError):
        count = error.error_count()
        plural = "" if count == 1 else "s"
        return (
            f"{count} validation error{plural} for {error.title}: "
            f"{describe_model_error(error, None, None)}"
        )
    assert isinstance(error, (jsonschema.ValidationError, jsonschema.SchemaError))
    try:
        declared = _declared_names()
        path = [
            segment
            if type(segment) is int or (type(segment) is str and segment in declared)
            else None
            for segment in error.absolute_path
        ]
        keyword = error.validator if isinstance(error.validator, str) else None
        phrase = (
            f"fails its {keyword} constraint"
            if keyword in jsonschema.validators.Draft202012Validator.VALIDATORS
            else "is invalid"
        )
        return f"schema validation error: {_render_path(path)}: {phrase}"
    except Exception:
        return f"schema validation error: {_UNDESCRIBED}"


def exception_text(error: BaseException) -> str:
    """``str(error)``, unless ``error``'s chain holds a value-bearing error.

    A value-bearing error (a registered type, :func:`_value_bearing_types`)
    is described from its structure. An exception whose ``__cause__`` /
    ``__context__`` chain (or exception group) holds one, at any depth and
    whether or not the context is suppressed, is never rendered from its own
    message: it reads ``<its class name>: <that error's description>``.
    Whatever built the wrapper's message -- ``f"{e}"``, ``{e!r}``,
    ``format()``, ``%r``, a slice, ``e.message`` or ``e.instance``, a nested
    wrapper -- nothing of it is shown, so no form of it can carry the value
    (rev 18; until rev 17 the message was kept unless it contained
    ``str(e)``, which ``repr(e)`` evades). Every other exception is
    ``str(error)`` unchanged.

    Text that arrives as a plain string, with no exception chained, is not
    recognised -- which is why pmcp never builds such a copy
    (``tests/test_exception_text_sinks.py`` flags the construction site) and
    replaces the SDK's stringified parse errors where it receives them
    (``pmcp.client.manager._downstream_error``).
    """
    if _is_validation_error(error):
        return _validation_text(error)
    linked = _chained_value_bearing(error)
    if linked is not None:
        return f"{type(error).__name__}: {_validation_text(linked)}"
    return str(error)


def _chained_value_bearing(error: BaseException) -> BaseException | None:
    """The first value-bearing error in ``error``'s chain other than
    ``error`` itself: through ``__cause__`` and ``__context__`` (suppressed
    or not) and exception-group members, at any depth."""
    for linked in _chain(error):
        if linked is not error and _is_validation_error(linked):
            return linked
    return None


def safe_exc_info(error: BaseException) -> BaseException | None:
    """``exc_info=`` for a log call: the exception, unless its chain holds a
    validation error, whose rendered traceback would carry the value."""
    if any(_is_validation_error(linked) for linked in _chain(error)):
        return None
    return error


def _qualified_name(kind: type[BaseException]) -> str:
    """``module.QualName`` as the interpreter prints it (bare for builtins)."""
    module = getattr(kind, "__module__", None)
    name = getattr(kind, "__qualname__", kind.__name__)
    if module in (None, "builtins", "__main__"):
        return str(name)
    return f"{module}.{name}"


def safe_traceback_text(error: BaseException) -> str:
    """The formatted traceback. When the chain holds a validation or parse
    error, every exception in it is rendered as its frames (file, line,
    source) and ``Type: <text>``, where the text is a value-bearing error's
    description, a wrapper's description of what it chains (never its own
    message, rev 18) or else ``str()`` -- the frames never carry an
    exception's text -- so it stays a usable traceback (rev 6)."""
    if safe_exc_info(error) is not None:
        return "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
    parts: list[str] = []
    seen: set[int] = set()

    def render(current: BaseException) -> None:
        seen.add(id(current))
        cause, context = current.__cause__, current.__context__
        if cause is not None and id(cause) not in seen:
            render(cause)
            parts.append(
                "\nThe above exception was the direct cause of the following "
                "exception:\n\n"
            )
        elif (
            context is not None
            and id(context) not in seen
            and not current.__suppress_context__
        ):
            render(context)
            parts.append(
                "\nDuring handling of the above exception, another exception "
                "occurred:\n\n"
            )
        if current.__traceback__ is not None:
            parts.append("Traceback (most recent call last):\n")
            parts.extend(traceback.format_tb(current.__traceback__))
        # The class is printed once: a wrapper's line is its qualified name
        # and the description of what it chains (rev 18).
        linked = (
            None if _is_validation_error(current) else _chained_value_bearing(current)
        )
        text = exception_text(current) if linked is None else _validation_text(linked)
        parts.append(f"{_qualified_name(type(current))}: {text}\n")

    render(error)
    return "".join(parts)


# --- every log record, whoever logs it (rev 3, widened in rev 4) ------------


# --- JSON-RPC messages rendered as their structure (rev 10) -------------------

_MESSAGE_CLASS_NAMES = (
    "JSONRPCRequest",
    "JSONRPCNotification",
    "JSONRPCResponse",
    "JSONRPCError",
)
_KNOWN_METHODS: frozenset[str] | None = None


def _known_methods() -> frozenset[str]:
    """Every method name the MCP SDK's own types declare (a `Literal` on a
    model's `method` field). A method outside this set is a downstream's or
    a caller's string, and is not shown."""
    global _KNOWN_METHODS
    if _KNOWN_METHODS is None:
        import importlib
        import inspect
        import pkgutil
        import typing

        from pydantic import BaseModel

        found: set[str] = set()
        try:
            import mcp_types

            for info in pkgutil.walk_packages(mcp_types.__path__, "mcp_types."):
                module = importlib.import_module(info.name)
                for value in vars(module).values():
                    if not (inspect.isclass(value) and issubclass(value, BaseModel)):
                        continue
                    field = value.model_fields.get("method")
                    if (
                        field is not None
                        and typing.get_origin(field.annotation) is typing.Literal
                    ):
                        found |= {
                            arg
                            for arg in typing.get_args(field.annotation)
                            if isinstance(arg, str)
                        }
        except Exception:  # pragma: no cover - no SDK types: show no method
            found = set()
        _KNOWN_METHODS = frozenset(found)
    return _KNOWN_METHODS


def describe_value(value: Any) -> str:
    """A rejected value's structure, never its content (rev 12): an object's
    key count, an array's length, or a scalar's type. A string is just "a
    string": its length would follow the value's, which the sweeps' pair
    differential treats as a channel."""
    if isinstance(value, str):
        return "a string"
    return _shape(value)


def _shape(value: Any) -> str:
    """A value's structure, never its content: an object's key count, an
    array's length, or a scalar's type."""
    if isinstance(value, dict):
        return f"object ({len(value)} key{'' if len(value) == 1 else 's'})"
    if isinstance(value, (list, tuple)):
        return f"array ({len(value)} item{'' if len(value) == 1 else 's'})"
    return "null" if value is None else type(value).__name__


def _message_fields(message: Any) -> dict[str, Any] | None:
    """The JSON-RPC fields of an SDK message object or a dict shaped like
    one, or None when `message` is neither."""
    if isinstance(message, dict):
        if "jsonrpc" in message and any(
            key in message for key in ("method", "result", "error", "params", "id")
        ):
            return message
        return None
    if type(message).__name__ in _MESSAGE_CLASS_NAMES and type(
        message
    ).__module__.startswith(("mcp", "mcp_types")):
        return {
            name: getattr(message, name)
            for name in ("method", "id", "params", "result", "error")
            if hasattr(message, name)
        }
    return None


def describe_jsonrpc_message(message: Any) -> str:
    """A JSON-RPC message as its structure (Consiliency/pmcp#297, rev 10):
    the kind, the method when the SDK declares it, the id's type and the
    shape of `params` / `result` / `error` -- never their content, and never
    an id's value. Every SDK log line that renders a message, incoming or
    outgoing, shows this instead of the payload."""
    fields = _message_fields(message) or {}
    method = fields.get("method")
    if method is not None:
        kind = "request" if fields.get("id") is not None else "notification"
    elif fields.get("error") is not None:
        kind = "error"
    else:
        kind = "response"
    parts = []
    if method is not None:
        parts.append(
            f"method {method!r}"
            if isinstance(method, str) and method in _known_methods()
            else "an undeclared method"
        )
    if fields.get("id") is not None:
        parts.append(f"id: {_shape(fields['id'])}")
    for name in ("params", "result"):
        if fields.get(name) is not None:
            value = fields[name]
            dumped = value.model_dump() if hasattr(value, "model_dump") else value
            parts.append(f"{name}: {_shape(dumped)}")
    error = fields.get("error")
    if error is not None:
        code = getattr(error, "code", None)
        if code is None and isinstance(error, dict):
            code = error.get("code")
        parts.append(f"error code: {_shape(code)}")
    return f"<JSON-RPC {kind}" + (": " + ", ".join(parts) if parts else "") + ">"


def _render_message(self: Any) -> str:
    try:
        return describe_jsonrpc_message(self)
    except Exception:  # a log call must never fail because of the render
        return "<JSON-RPC message>"


def _install_message_rendering() -> None:
    """Make every rendering of an MCP SDK JSON-RPC message object -- an
    f-string, `%s`, `%r`, a containing `SessionMessage`'s dataclass repr --
    its structure. The SDK's transports log each message they receive and
    send at DEBUG (`mcp/client/sse.py` "Received server message: {message}"
    and "Sending client message: {session_message}",
    `mcp/client/streamable_http.py` "SSE message: {message}" and "Sending
    client message: {message}"): after the envelope is accepted, but before
    pmcp validates the payload -- and outgoing ones carry the caller's
    arguments. A preformatted f-string leaves no object for a record
    scrubber to find, so the object renders itself. Idempotent."""
    try:
        import mcp.types as mcp_types_module
    except Exception:  # pragma: no cover - the SDK is a dependency
        return
    for name in _MESSAGE_CLASS_NAMES:
        cls = getattr(mcp_types_module, name, None)
        if cls is None or getattr(cls, "pmcp_structural_render", False):
            continue
        cls.__repr__ = _render_message  # type: ignore[method-assign]
        cls.__str__ = _render_message  # type: ignore[method-assign]
        cls.pmcp_structural_render = True
    # The transport wrapper and its metadata: `ClientMessageMetadata.headers`
    # can carry `Mcp-Param-*` headers, which hold tool-argument values
    # (`mcp/shared/inbound.py`, `x-mcp-header`), and the SDK logs a whole
    # `SessionMessage` ("Sending client message: {session_message}").
    try:
        from mcp.shared import message as session_module
    except Exception:  # pragma: no cover
        return
    for name, render in (
        ("SessionMessage", _render_session_message),
        ("ClientMessageMetadata", _render_metadata),
        ("ServerMessageMetadata", _render_metadata),
    ):
        cls = getattr(session_module, name, None)
        if cls is None or getattr(cls, "pmcp_structural_render", False):
            continue
        cls.__repr__ = render  # type: ignore[method-assign]
        cls.__str__ = render  # type: ignore[method-assign]
        cls.pmcp_structural_render = True


def _render_metadata(self: Any) -> str:
    """A transport metadata object as its type and header *names*."""
    try:
        headers = getattr(self, "headers", None)
        names = sorted(headers) if isinstance(headers, dict) else []
        shown = f" headers: {', '.join(names)}" if names else ""
        return f"<{type(self).__name__}{shown}>"
    except Exception:  # a log call must never fail because of the render
        return "<message metadata>"


def _render_session_message(self: Any) -> str:
    try:
        metadata = getattr(self, "metadata", None)
        tail = "" if metadata is None else f", {_render_metadata(metadata)}"
        return (
            f"SessionMessage({_render_message(getattr(self, 'message', None))}{tail})"
        )
    except Exception:  # a log call must never fail because of the render
        return "SessionMessage(<JSON-RPC message>)"


def _scrubbed(value: Any, depth: int = 0) -> Any:
    """`value` with every exception whose chain holds a validation error
    replaced by its :func:`exception_text`, and every JSON-RPC message (an
    SDK object or a dict shaped like one) by
    :func:`describe_jsonrpc_message`, looking inside tuples, lists, sets and
    dicts (keys and values)."""
    if isinstance(value, BaseException):
        return exception_text(value) if safe_exc_info(value) is None else value
    if _message_fields(value) is not None:
        return describe_jsonrpc_message(value)
    if depth > 8:
        return value
    if isinstance(value, tuple):
        return tuple(_scrubbed(item, depth + 1) for item in value)
    if isinstance(value, list):
        return [_scrubbed(item, depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        return type(value)(_scrubbed(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return {
            _scrubbed(key, depth + 1): _scrubbed(item, depth + 1)
            for key, item in value.items()
        }
    return value


def scrub_record(record: logging.LogRecord) -> logging.LogRecord:
    """Remove a validation error's text from `record`, in place.

    - ``msg`` that is itself such an exception becomes its
      :func:`exception_text`;
    - ``args`` -- a tuple, or the mapping of a ``%(name)s`` message -- have
      every such exception, however nested in containers, replaced;
    - ``exc_info`` whose chain holds one is dropped, and the message gets
      the exception's :func:`exception_text` appended, so the record still
      says what failed.

    ``stack_info`` needs nothing: it renders frames and source lines, never
    an exception's text. Every other record is returned unchanged.
    """
    try:
        # The MCP SDK's server-side loggers and sse_starlette log request
        # content as text (rev 20): masked before anything else.
        from pmcp.sdk_rejections import scrub_sdk_record

        scrub_sdk_record(record)
        if isinstance(record.msg, BaseException):
            record.msg = _scrubbed(record.msg)
        if record.args:
            if (
                isinstance(record.args, dict)
                and _message_fields(record.args) is not None
            ):
                # A single mapping argument that is itself a message: keep
                # `%(name)s` keys working, but with no payload in them.
                described = describe_jsonrpc_message(record.args)
                if "%(" in str(record.msg):
                    record.args = {
                        key: (
                            "<omitted>"
                            if key in ("params", "result", "error", "id")
                            else value
                        )
                        for key, value in record.args.items()
                    }
                else:
                    record.args = (described,)
            else:
                record.args = _scrubbed(record.args)
        error = record.exc_info[1] if isinstance(record.exc_info, tuple) else None
        if isinstance(error, BaseException) and safe_exc_info(error) is None:
            try:
                message = record.getMessage()
            except Exception:
                message = str(record.msg)
            record.msg = f"{message} ({exception_text(error)})"
            record.args = None
            record.exc_info = None
            record.exc_text = None
    except Exception:
        pass  # a log call must never fail because of the scrub
    return record


def _scrubbing_factory(previous: Any) -> Any:
    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        return scrub_record(previous(*args, **kwargs))

    factory.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
    factory.previous = previous  # type: ignore[attr-defined]
    return factory


# --- rev 13: the JSON-RPC 2.0 envelope (round-12 codex B2) -------------------


#: JSON's type names, for a value `json` produced (round-13 claude nit: the
#: reasons promise JSON type names, not Python's).
_JSON_TYPE_NAMES = {
    dict: "object",
    list: "array",
    str: "string",
    int: "number",
    float: "number",
    bool: "boolean",
    type(None): "null",
}


def _type_of(value: Any) -> str:
    return _JSON_TYPE_NAMES.get(type(value), "value")


def _is_request_id(value: Any) -> bool:
    """MCP's `RequestId`: a string or an integer. Not a bool (`True == 1`
    would resolve request 1) and not a float (`1.0 == 1` likewise)."""
    return isinstance(value, (str, int)) and not isinstance(value, bool)


def jsonrpc_envelope_problem(frame: Any) -> str | None:
    """Why `frame` is not a JSON-RPC 2.0 message as MCP defines one, or None.

    Every rule is the specification's, not a list of bad frames
    (Consiliency/pmcp#297, rev 13): JSON-RPC 2.0 sections 4 and 5, and MCP's
    `JSONRPCRequest` / `JSONRPCNotification` / `JSONRPCResultResponse` /
    `JSONRPCErrorResponse`.

    - `jsonrpc` is exactly the string `"2.0"`;
    - a request has a string `method` and a `RequestId`; a notification has a
      string `method` and no `id` -- or `id: null`, which main, and the MCP
      SDK's own parser, read as a notification (rev 15, round-14 grok F004;
      nothing in it is echoed); `params`, when present, is an object; a
      request or notification carries neither `result` nor `error`;
    - a response has an `id` member (a `RequestId`, or null for an error the
      server could not attribute) and exactly one of `result` and `error`;
    - `result` is an object (MCP's `Result`);
    - `error` is an object whose `code` is an integer (not a bool) and whose
      `message` is a string; `data` is optional and unconstrained.

    Extra members are allowed (neither specification forbids them, and
    nothing reads them). The reason is value-free: fixed text and JSON type
    names only, so it can be logged for any frame.
    """
    if not isinstance(frame, dict):
        return f"not a JSON object ({_type_of(frame)})"
    if frame.get("jsonrpc") != "2.0" or not isinstance(frame.get("jsonrpc"), str):
        return "the jsonrpc member is not the string 2.0"
    if "method" in frame:
        method = frame["method"]
        if not isinstance(method, str):
            return f"non-string method ({_type_of(method)})"
        msg_id = frame.get("id")
        if msg_id is not None and not _is_request_id(msg_id):
            return f"id of type {_type_of(msg_id)}"
        # `params: null` is outside JSON-RPC 2.0, but the SDK's models accept
        # it and it carries nothing: it reads as absent (round-13 claude N1),
        # so a `ping` sent that way is still answered.
        params = frame.get("params")
        if params is not None and not isinstance(params, dict):
            return f"params of type {_type_of(params)}"
        if "result" in frame or "error" in frame:
            return "a request or notification carrying result or error"
        return None
    if "id" not in frame:
        return "a response without an id"
    msg_id = frame["id"]
    if msg_id is not None and not _is_request_id(msg_id):
        return f"id of type {_type_of(msg_id)}"
    has_result, has_error = "result" in frame, "error" in frame
    if has_result == has_error:
        return "both result and error" if has_result else "neither result nor error"
    if has_result:
        if msg_id is None:
            return "a result with a null id"
        if not isinstance(frame["result"], dict):
            return f"result of type {_type_of(frame['result'])}"
        return None
    error = frame["error"]
    if not isinstance(error, dict):
        return f"error of type {_type_of(error)}"
    if type(error.get("code")) is not int:
        return f"error code of type {_type_of(error.get('code'))}"
    if not isinstance(error.get("message"), str):
        return f"error message of type {_type_of(error.get('message'))}"
    return None


class _StrictMessageAdapter:
    """The SDK client transports' `jsonrpc_message_adapter`, with
    `jsonrpc_envelope_problem` applied first (rev 13, round-12 codex B2).

    The SDK's models are lax where the specification is not: they coerce an
    `error.code` of `true`, `"5"` or `5.0` to an integer, and ignore an extra
    member, so a frame with both `result` and `error` validates as an error
    response. Its `message` then reached pmcp as if it were the downstream's
    error. A frame this adapter rejects raises a pydantic `ValidationError`
    whose text is the value-free reason, which each transport already
    handles as a frame it could not parse: the SSE readers put it on the
    read stream, where `_read_sse` drops it, and the JSON-response path turns
    it into a `-32700`, whose message `_downstream_error` replaces.
    Everything else is the SDK's own adapter.
    """

    def __init__(self, base: Any) -> None:
        self._base = base

    def validate_json(self, data: Any, /, *args: Any, **kwargs: Any) -> Any:
        from pmcp.parsing import JSONParseError, load_json

        problem: str | None
        try:
            value = load_json(data, source="downstream JSON-RPC message")
        except JSONParseError:
            # Not JSON to Python's parser (or past a parser limit): rejected
            # here, not handed to the SDK's own parser, so nothing depends on
            # the two parsers agreeing (round-13 claude N3).
            problem = "not parseable as JSON"
        else:
            problem = jsonrpc_envelope_problem(value)
        if problem is not None:
            raise ValidationError.from_exception_data(
                "JSONRPCMessage",
                [
                    {
                        "type": PydanticCustomError(
                            "jsonrpc_envelope",
                            "malformed JSON-RPC envelope: {reason}",
                            {"reason": problem},
                        ),
                        "loc": (),
                        "input": None,
                    }
                ],
            )
        return self._base.validate_json(data, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


class _ClientTypesView(ModuleType):
    """`mcp_types` as the SDK's SSE and stdio clients see it: the same module,
    but with the strict adapter. Those clients read
    `types.jsonrpc_message_adapter` at call time, and the server side uses the
    same module, so the module itself is left alone."""

    def __init__(self, base: ModuleType, adapter: Any) -> None:
        super().__init__(base.__name__)
        self._base = base
        self.jsonrpc_message_adapter = adapter

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _install_strict_client_envelopes() -> None:
    """Point the SDK's three client transports at the strict adapter: the
    gateway's remote transports, and `stdio_client`, which `pmcp refresh`
    and the startup description refresh use. Installed on `import pmcp`
    with the record scrubber. Idempotent; the server-side transports are
    untouched."""
    try:
        import mcp.client.sse as sse_module
        import mcp.client.stdio as stdio_module
        import mcp.client.streamable_http as streamable_module
    except Exception:  # pragma: no cover - the SDK is a dependency
        return

    for module in (sse_module, stdio_module):
        current = getattr(module, "types")
        if not isinstance(current, _ClientTypesView):
            strict = _StrictMessageAdapter(current.jsonrpc_message_adapter)
            setattr(module, "types", _ClientTypesView(current, strict))
    adapter = getattr(streamable_module, "jsonrpc_message_adapter")
    if not isinstance(adapter, _StrictMessageAdapter):
        setattr(
            streamable_module, "jsonrpc_message_adapter", _StrictMessageAdapter(adapter)
        )


def install_log_scrubber() -> None:
    """Scrub every `LogRecord` at creation, whatever logger creates it.

    Wraps the current ``logging`` record factory, so every record any logger
    creates -- the MCP SDK's (including ``"client"``, outside ``mcp.*``),
    uvicorn's, httpx's, any other -- is scrubbed before any handler sees it,
    regardless of propagation. It scrubs what a record *carries* (its
    traceback, arguments, an exception as ``msg``); text a library has
    already formatted into the message string is out of its reach.
    Idempotent: installing twice keeps one wrapper. Installed on ``import
    pmcp`` (so by every entry point), and again at ``pmcp.client.manager``
    import and in ``GatewayServer.__init__``.
    """
    current = logging.getLogRecordFactory()
    if not getattr(current, "pmcp_validation_scrubber", False):
        logging.setLogRecordFactory(_scrubbing_factory(current))
    _install_excepthook()
    _install_threading_excepthook()
    _install_handle_error()
    _install_message_rendering()
    _install_strict_client_envelopes()
    from pmcp.sdk_rejections import install_value_free_sdk_errors

    install_value_free_sdk_errors()


def _install_handle_error() -> None:
    """A handler whose ``emit`` raises makes ``logging.Handler.handleError``
    print "--- Logging error ---" and the exception it is handling, chain and
    all, to stderr -- past every scrub, and while an ``except`` block for a
    validation or parse error is active that chain holds it (rev 6; rev 3
    listed it as unverified). Such a chain is printed by
    :func:`safe_traceback_text`; everything else by the original method.
    Idempotent."""
    original = logging.Handler.handleError
    if getattr(original, "pmcp_validation_scrubber", False):
        return

    def handle_error(self: logging.Handler, record: logging.LogRecord) -> None:
        error = sys.exc_info()[1]
        if not isinstance(error, BaseException) or safe_exc_info(error) is not None:
            original(self, record)
            return
        if not logging.raiseExceptions or sys.stderr is None:
            return
        try:
            sys.stderr.write(
                "--- Logging error ---\n"
                + safe_traceback_text(error)
                + f"Message: {scrub_record(record).msg!r}\n"
            )
        except OSError:  # pragma: no cover - stderr closed, as the original
            pass

    handle_error.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
    handle_error.original = original  # type: ignore[attr-defined]
    logging.Handler.handleError = handle_error  # type: ignore[method-assign]


def _install_excepthook() -> None:
    """An uncaught exception is printed by ``sys.excepthook``, chain and all:
    a ``raise ValueError(...) from e`` whose cause is a validation or parse
    error would print the input past every log scrub (rev 6). Such a chain is
    printed by :func:`safe_traceback_text` instead; every other exception by
    the previous hook, unchanged. Idempotent."""
    previous = sys.excepthook
    if getattr(previous, "pmcp_validation_scrubber", False):
        return

    def hook(kind: Any, value: Any, tb: Any) -> None:
        if isinstance(value, BaseException) and safe_exc_info(value) is None:
            # The default hook prints nothing when there is no stderr.
            if sys.stderr is not None:
                sys.stderr.write(safe_traceback_text(value) + "\n")
            return
        previous(kind, value, tb)

    hook.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
    hook.previous = previous  # type: ignore[attr-defined]
    sys.excepthook = hook


def _install_threading_excepthook() -> None:
    """``threading.excepthook`` prints an uncaught exception in a thread the
    same way (rev 7, N3): such a chain is printed by
    :func:`safe_traceback_text` under the interpreter's own header; every
    other exception by the previous hook, unchanged. Idempotent."""
    import threading

    previous = threading.excepthook
    if getattr(previous, "pmcp_validation_scrubber", False):
        return

    def hook(args: Any) -> None:
        value = getattr(args, "exc_value", None)
        if (
            getattr(args, "exc_type", None) is not SystemExit
            and isinstance(value, BaseException)
            and safe_exc_info(value) is None
        ):
            if sys.stderr is not None:
                thread = getattr(args, "thread", None)
                name = getattr(thread, "name", None) or "unknown"
                sys.stderr.write(
                    f"Exception in thread {name}:\n" + safe_traceback_text(value) + "\n"
                )
            return
        previous(args)

    hook.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
    hook.previous = previous  # type: ignore[attr-defined]
    threading.excepthook = hook
