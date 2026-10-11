"""Text shown to a human: every character a terminal may act on, escaped.

One function, :func:`display_safe`, for anything pmcp shows to a person -- a
CLI line, a log line, a remediation command (Consiliency/pmcp#297
implementation, Consiliency/pmcp#388 round 2). A character is shown as it is
only when Python counts it printable (``str.isprintable``); every other one
is replaced by its backslash escape (``\\x1b``, ``\\x9b``, ``\\u202e``). That
escapes:

- C0 controls (U+0000-U+001F), DEL (U+007F) and C1 controls (U+0080-U+009F,
  among them U+009B, the one-byte CSI a terminal acts on);
- format characters (Cf): the bidi controls (U+061C, U+200E-U+200F,
  U+202A-U+202E, U+2066-U+2069), the zero-width characters (U+200B-U+200D,
  U+2060, U+FEFF) and the soft hyphen;
- line and paragraph separators (U+2028, U+2029) and every space but the
  ASCII one;
- surrogates, private-use and unassigned code points.

The module imports nothing of pmcp's, so the consent modules, which must
import first, can use it.
"""

from __future__ import annotations

#: The two layout characters a multi-line log message may keep
#: (:func:`display_safe` with ``keep=LOG_LAYOUT``). A one-line display keeps
#: neither.
LOG_LAYOUT = "\n\t"


def _escaped(ch: str) -> str:
    return repr(ch)[1:-1]


def display_safe(value: object, *, keep: str = "") -> str:
    """``value`` as text, with every non-printable character escaped.

    ``keep`` names characters left as they are (only :data:`LOG_LAYOUT`, for
    a log message pmcp lays out over several lines). Never raises: a value
    whose ``str`` fails reads as its class.
    """
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:  # noqa: BLE001 -- a display must never fail
        text = f"<{type(value).__name__}>"
    if text.isprintable():
        return text
    return "".join(
        ch if ch.isprintable() or ch in keep else _escaped(ch) for ch in text
    )
