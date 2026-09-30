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

import json
import logging
import sys
import traceback
from collections.abc import Iterable, Iterator
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
    global _PARSE_ERRORS
    if not _PARSE_ERRORS:
        _PARSE_ERRORS = _parse_error_types()
    return isinstance(error, _PARSE_ERRORS)


def _is_validation_error(error: BaseException) -> bool:
    """A validation *or parse* error: one whose own text can carry the input
    it rejected (the parse half since rev 6)."""
    return isinstance(
        error, (ValidationError, jsonschema.ValidationError)
    ) or _is_parse_error(error)


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
    if isinstance(error, ValidationError):
        count = error.error_count()
        plural = "" if count == 1 else "s"
        return (
            f"{count} validation error{plural} for {error.title}: "
            f"{describe_model_error(error, None, None)}"
        )
    assert isinstance(error, jsonschema.ValidationError)
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
    """``str(error)``, except where that would carry a validation error's text.

    A pydantic or jsonschema ``ValidationError`` renders the rejected value;
    so does any exception whose own text embeds one it chains
    (``RuntimeError(f"... {e}") from e``). Either is described from its
    structure instead. Every other exception is ``str(error)`` unchanged.

    The embedding check is an exact-substring backstop for wrappers built
    with ``f"{e}"``/``f"{e!r}"``. It does not recognise a truncated or
    reformatted copy (``str(e)[:200]``, ``e.errors()``), nor validation text
    that arrives as a plain string -- which is why pmcp never builds such a
    copy (``tests/test_exception_text_sinks.py`` flags the construction
    site) and replaces the SDK's stringified parse errors where it receives
    them (``pmcp.client.manager._downstream_error``).
    """
    if _is_validation_error(error):
        return _validation_text(error)
    text = str(error)
    for linked in _chain(error):
        if linked is not error and _is_validation_error(linked):
            try:
                embedded = str(linked)
            except Exception:
                embedded = ""
            if embedded and embedded in text:
                return f"{type(error).__name__}: {_validation_text(linked)}"
    return text


def safe_exc_info(error: BaseException) -> BaseException | None:
    """``exc_info=`` for a log call: the exception, unless its chain holds a
    validation error, whose rendered traceback would carry the value."""
    if any(_is_validation_error(linked) for linked in _chain(error)):
        return None
    return error


def safe_traceback_text(error: BaseException) -> str:
    """The formatted traceback. When the chain holds a validation or parse
    error, every exception in it is rendered as its frames (file, line,
    source) and ``Type: exception_text(...)`` -- the frames never carry an
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
        parts.append(f"{type(current).__name__}: {exception_text(current)}\n")

    render(error)
    return "".join(parts)


# --- every log record, whoever logs it (rev 3, widened in rev 4) ------------


def _scrubbed(value: Any, depth: int = 0) -> Any:
    """`value` with every exception whose chain holds a validation error
    replaced by its :func:`exception_text`, looking inside tuples, lists,
    sets and dicts (keys and values)."""
    if isinstance(value, BaseException):
        return exception_text(value) if safe_exc_info(value) is None else value
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
        if isinstance(record.msg, BaseException):
            record.msg = _scrubbed(record.msg)
        if record.args:
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
            sys.stderr.write(safe_traceback_text(value) + "\n")
            return
        previous(kind, value, tb)

    hook.pmcp_validation_scrubber = True  # type: ignore[attr-defined]
    hook.previous = previous  # type: ignore[attr-defined]
    sys.excepthook = hook
