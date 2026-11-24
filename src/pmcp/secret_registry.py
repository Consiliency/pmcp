"""Exact-value redaction of resolved secrets (Consiliency/pmcp#487 plan 3.7).

A process-local registry of every value pmcp itself must never show: the
1Password service-account token, as soon as it is read, and every value an
``op://`` reference resolved to, before any child that receives it is
spawned. Values are kept for the life of the process.

Redaction here is UNCONDITIONAL -- it does not depend on
``options.redact_secrets`` or on any pattern -- and callers run it BEFORE they
truncate or serialize, so a value cut in half by a size cap is never shown
either. Each value is matched in its raw form and its JSON-string-escaped
forms (``json.dumps`` with and without ``ensure_ascii``), longest first, so a
value inside a JSON document is caught too. Transformed forms (base64, split
across chunks, re-encoded by a child) are out of scope and documented.

No pmcp imports at load time: every layer -- logging, policy, the manager,
the installer, the handlers -- imports this without a cycle.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

#: What a registered value is replaced with.
REDACTION_MARKER = "[REDACTED]"

#: Values shorter than this are never registered: they are refused as
#: resolved values anyway (``output_invalid``), and redacting a short common
#: string would mangle unrelated output.
_MIN_LENGTH = 8

_lock = threading.Lock()
_values: set[str] = set()
_pattern: re.Pattern[str] | None = None


def _forms(value: str) -> set[str]:
    forms = {value}
    for ensure_ascii in (True, False):
        escaped = json.dumps(value, ensure_ascii=ensure_ascii)[1:-1]
        forms.add(escaped)
    return {form for form in forms if len(form) >= _MIN_LENGTH}


def register_secret_value(value: str | None) -> None:
    """Remember ``value`` (and its JSON-escaped forms) for exact redaction."""
    global _pattern
    if not value or len(value) < _MIN_LENGTH:
        return
    forms = _forms(value)
    with _lock:
        if forms <= _values:
            return
        _values.update(forms)
        ordered = sorted(_values, key=len, reverse=True)
        _pattern = re.compile("|".join(re.escape(form) for form in ordered))


def has_registered_secrets() -> bool:
    """Whether any value is registered (cheap fast path for callers)."""
    return _pattern is not None


def redact_registered(text: str) -> str:
    """``text`` with every registered value replaced by :data:`REDACTION_MARKER`."""
    pattern = _pattern
    if pattern is None or not isinstance(text, str) or not text:
        return text
    if pattern.search(text) is None:
        return text
    # Every occurrence of every registered form, overlapping ones included,
    # merged into spans: two registered values that overlap in the text
    # leave no residue of either (#499 r1 T1).
    with _lock:
        forms = list(_values)
    spans: list[tuple[int, int]] = []
    for form in forms:
        start = text.find(form)
        while start != -1:
            spans.append((start, start + len(form)))
            start = text.find(form, start + 1)
    spans.sort()
    merged: list[list[int]] = []
    for begin, end in spans:
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([begin, end])
    out: list[str] = []
    cursor = 0
    for begin, end in merged:
        out.append(text[cursor:begin])
        out.append(REDACTION_MARKER)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


#: Nesting beyond this is replaced by the marker (fail closed), never walked
#: into a ``RecursionError`` (#499 r2).
_MAX_DEPTH = 200


def redact_registered_in(value: Any) -> Any:
    """Redact every registered value inside ``value``, recursively.

    Strings are redacted in place; mapping keys too. A non-string scalar
    (a number, an unknown object) is judged by its rendering -- a JSON
    number is the same digits as the registered value it came from -- and
    replaced by its redacted rendering, a string, when it holds one (#499 r2
    A7): the result stays valid JSON. Nesting deeper than ``_MAX_DEPTH`` is
    replaced by :data:`REDACTION_MARKER`. When nothing registered occurs in
    the value's JSON rendering it is returned untouched.
    """
    pattern = _pattern
    if pattern is None:
        return value
    try:
        rendered: str | None = json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        rendered = None
    if rendered is not None and pattern.search(rendered) is None:
        return value
    return _redact_in(value, 0)


def _redact_scalar(value: Any) -> Any:
    rendered = json.dumps(value) if isinstance(value, (int, float)) else str(value)
    redacted = redact_registered(rendered)
    return value if redacted == rendered else redacted


def _redact_in(value: Any, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return REDACTION_MARKER
    if isinstance(value, str):
        return redact_registered(value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, list):
        return [_redact_in(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_in(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return {
            (
                redact_registered(key) if isinstance(key, str) else _redact_scalar(key)
            ): _redact_in(item, depth + 1)
            for key, item in value.items()
        }
    return _redact_scalar(value)


def redact_registered_in_place(value: dict[str, Any]) -> None:
    """:func:`redact_registered_in` applied to ``value`` itself (keys too)."""
    if _pattern is None:
        return
    items = list(value.items())
    value.clear()
    for key, item in items:
        value[redact_registered_in(key)] = redact_registered_in(item)


def reset_registered_secrets() -> None:
    """Forget every registered value. **Test-only seam.**"""
    global _pattern
    with _lock:
        _values.clear()
        _pattern = None


class SecretValueFilter(logging.Filter):
    """Redact registered values from a record's FULLY formatted message.

    Installed on the root logger. When the formatted message (``msg % args``)
    carries a registered value, the record's ``msg`` becomes the redacted
    text and its ``args`` are cleared; a precomputed ``exc_text`` and
    ``stack_info`` are redacted too. A record with nothing registered in it
    is left as it is.

    A filter on the root LOGGER alone never sees a record logged on a child
    logger (propagation skips ancestor loggers' filters), so the rendering
    itself is redacted as well, for every record whatever its path:
    ``LogRecord.getMessage``, ``Formatter.formatException`` and
    ``Formatter.format`` (``pmcp.argument_errors._install_display_escaping``).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if _pattern is not None:
            _redact_record(record)
        return True


def _redact_record(record: logging.LogRecord) -> None:
    try:
        message = record.getMessage()
    except Exception:  # a malformed record: logging reports it itself
        return
    redacted = redact_registered(message)
    if redacted != message:
        record.msg = redacted
        record.args = None
    if record.exc_text:
        record.exc_text = redact_registered(record.exc_text)
    if record.stack_info:
        record.stack_info = redact_registered(record.stack_info)


_installed = False
_install_lock = threading.Lock()


def install_log_redaction() -> None:
    """Install exact-value redaction on logging. Idempotent.

    The :class:`SecretValueFilter` on the root logger, and the rendering
    hooks of ``pmcp.argument_errors`` (installed on ``import pmcp``; ensured
    here).
    """
    global _installed
    with _install_lock:
        if _installed:
            return
        _installed = True
        logging.getLogger().addFilter(SecretValueFilter())
    from pmcp.argument_errors import install_log_scrubber

    install_log_scrubber()
