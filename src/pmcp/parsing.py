"""Parse structured text without ever echoing it (Consiliency/pmcp#297, rev 7).

A parser's failure can quote what it rejected, and not only through its own
error type: PyYAML's ``MarkedYAMLError`` renders a snippet of the input, and
a YAML tag makes it run a *constructor* whose plain ``ValueError`` /
``KeyError`` quotes the value (``k: !!int <value>`` raises
``ValueError: invalid literal for int() with base 10: '<value>'``). So a
failure is classified by **where it happened**, not by its type: every parse
of structured text in pmcp goes through one helper per format below, and any
exception raised while parsing or constructing becomes a :class:`ParseError`
that names the format, the source pmcp chose to describe (a path, "npm
registry response") and, where the parser recorded one, the line and column
-- never the input. It is raised outside the ``except`` block, so it chains
nothing (``__cause__`` and ``__context__`` are both ``None``).

Each :class:`ParseError` also subclasses the error type its format's callers
already catch (``yaml.YAMLError``, ``json.JSONDecodeError`` and so
``ValueError``), so no caller's ``except`` clause changes meaning.
``tests/test_parse_error_echo.py`` fails on any direct parser call in
``src/pmcp`` outside this module.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import IO, Any

import yaml


class ParseError(ValueError):
    """A structured-text parse failed. Its text is value-free by construction."""

    def __init__(
        self,
        kind: str,
        source: str,
        line: int | None = None,
        column: int | None = None,
        cause: str | None = None,
    ) -> None:
        self.kind = kind
        self.source = source
        self.line = line
        self.column = column
        #: The class of the exception the parser raised (``ParserError``,
        #: ``ValueError`` from a tag's constructor, ``RecursionError``, ...).
        self.cause = cause
        ValueError.__init__(self, self._text())

    def _text(self) -> str:
        where = (
            f" at line {self.line}, column {self.column}"
            if isinstance(self.line, int) and isinstance(self.column, int)
            else ""
        )
        why = f" ({self.cause})" if self.cause else ""
        return f"could not parse {self.kind} {self.source}{where}{why}"

    def __str__(self) -> str:
        return self._text()

    def __reduce__(self) -> Any:  # pragma: no cover - pickling support
        return (
            type(self),
            (self.kind, self.source, self.line, self.column, self.cause),
        )


class YAMLParseError(ParseError, yaml.YAMLError):
    """A YAML parse (or construction) failed."""


class JSONParseError(ParseError, json.JSONDecodeError):
    """A JSON parse failed. ``doc`` is empty: it would be the input."""

    def __init__(
        self,
        kind: str,
        source: str,
        line: int | None = None,
        column: int | None = None,
        cause: str | None = None,
    ) -> None:
        ParseError.__init__(self, kind, source, line, column, cause)
        # `json.JSONDecodeError`'s attributes, without the input.
        self.msg = "could not parse JSON"
        self.doc = ""
        self.pos = 0
        self.lineno = line if isinstance(line, int) else 0
        self.colno = column if isinstance(column, int) else 0


class TimestampParseError(ParseError):
    """An ISO-8601 timestamp parse failed."""


def _yaml_position(error: BaseException) -> tuple[int | None, int | None]:
    if isinstance(error, yaml.MarkedYAMLError):
        mark = error.problem_mark or error.context_mark
        if mark is not None:
            return mark.line + 1, mark.column + 1
    return None, None


def safe_yaml_loader(*, fast: bool) -> Any:
    """A safe YAML loader class for :func:`load_yaml`: with ``fast``, libyaml's
    ``CSafeLoader`` when PyYAML was built with it, else ``SafeLoader``."""
    if fast:
        return getattr(yaml, "CSafeLoader", None) or yaml.SafeLoader
    return yaml.SafeLoader


def load_yaml(stream: str | bytes | IO[Any], *, source: str, loader: Any = None) -> Any:
    """``yaml.safe_load(stream)``; any failure is a :class:`YAMLParseError`.

    ``loader`` picks a safe loader class -- ``yaml.SafeLoader`` or libyaml's
    ``yaml.CSafeLoader`` (the shipped manifest's fast path) -- and nothing
    else."""
    safe = {yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)}
    if loader is not None and loader not in safe:
        raise ValueError("load_yaml takes only a safe YAML loader")
    failure: tuple[int | None, int | None, str] | None = None
    try:
        if loader is not None:
            return yaml.load(stream, Loader=loader)  # noqa: S506 -- safe loaders only
        return yaml.safe_load(stream)
    except Exception as error:  # noqa: BLE001 -- classified by origin
        line, column = _yaml_position(error)
        failure = (line, column, type(error).__name__)
    line, column, cause = failure
    raise YAMLParseError("YAML", source, line, column, cause=cause)


def load_json(
    text: str | bytes | bytearray, *, source: str, encoding: str | None = None
) -> Any:
    """``json.loads(text)``; any failure is a :class:`JSONParseError`.

    With ``encoding``, bytes are decoded first (``json.loads(b.decode(enc))``)
    inside the same classification, so a ``UnicodeDecodeError`` -- whose text
    quotes the offending byte -- is a parse failure too. Without it, bytes go
    to ``json.loads`` as they are, which detects UTF-8/16/32 itself.
    """
    failure: tuple[int | None, int | None, str] | None = None
    try:
        if encoding is not None and isinstance(text, (bytes, bytearray)):
            text = bytes(text).decode(encoding)
        return json.loads(text)
    except Exception as error:  # noqa: BLE001 -- classified by origin
        if isinstance(error, json.JSONDecodeError):
            failure = (error.lineno, error.colno, type(error).__name__)
        else:
            failure = (None, None, type(error).__name__)
    line, column, cause = failure
    raise JSONParseError("JSON", source, line, column, cause=cause)


def load_json_file(handle: IO[Any], *, source: str) -> Any:
    """``json.load(handle)`` through :func:`load_json`."""
    return load_json(handle.read(), source=source)


def parse_timestamp(text: str, *, source: str) -> datetime:
    """``datetime.fromisoformat(text)``; any failure is a
    :class:`TimestampParseError`."""
    failure: str | None = None
    try:
        return datetime.fromisoformat(text)
    except Exception as error:  # noqa: BLE001 -- classified by origin
        failure = type(error).__name__
    raise TimestampParseError("timestamp", source, cause=failure)
