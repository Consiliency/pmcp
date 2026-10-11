"""Nothing pmcp shows a person carries a character a terminal acts on.

Consiliency/pmcp#388 round 2: grok F001 (a C1 CSI, U+009B, in a
``.mcp.json`` metadata URL reached ``pmcp doctor``'s stdout), codex F001
(``urlparse`` raised a ``ValueError`` quoting a URL's ESC and BEL, and the
CLI printed it) and claude N-A (a ``.mcp.json`` server name with an escape
sequence reached a WARNING; a rejected ``allowPrivateRegistry`` value was
echoed through ``%r``). One function, :func:`pmcp.display.display_safe`,
escapes for every renderer; a text parser's own error is described by its
class; every log line is escaped where it is rendered.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable
from pathlib import Path

import pytest

#: Every character class the escape set covers, each in full.
_C0 = [chr(c) for c in range(0x00, 0x20)]
_C1 = [chr(c) for c in range(0x80, 0xA0)]
_DEL = ["\x7f"]
_BIDI = [
    "؜",
    "‎",
    "‏",
    *(chr(c) for c in range(0x202A, 0x202F)),
    *(chr(c) for c in range(0x2066, 0x206A)),
]
_ZERO_WIDTH = ["​", "‌", "‍", "⁠", "﻿", "­"]
_SEPARATORS = [" ", " ", " ", "　"]
_CLASSES = {
    "C0": _C0,
    "DEL": _DEL,
    "C1": _C1,
    "bidi": _BIDI,
    "zero-width": _ZERO_WIDTH,
    "separators": _SEPARATORS,
}
_PLAIN = "plain text: café 中文 -- (a/b) 'q' \"q\" $x `y` \\z ~ #1"


def _printable(text: str, keep: str = "") -> bool:
    return all(ch.isprintable() or ch in keep for ch in text)


def _renderers() -> dict[str, tuple[Callable[[str], str], str]]:
    """(render, characters it may keep) for every human-facing renderer."""
    from pmcp.argument_errors import exception_text
    from pmcp.display import LOG_LAYOUT, display_safe
    from pmcp.manifest.installer import _operator_safe as installer_safe
    from pmcp.project_consent import _operator_safe as consent_safe
    from pmcp.provision_gate import operator_safe

    import pmcp  # noqa: F401 - installs the log escaping

    def log_line(text: str) -> str:
        record = logging.LogRecord(
            "pmcp.test", logging.WARNING, "f", 1, "%s", (text,), None
        )
        return logging.Formatter("%(message)s").format(record)

    def log_traceback(text: str) -> str:
        try:
            raise RuntimeError(text)
        except RuntimeError:
            import sys

            return logging.Formatter().formatException(sys.exc_info())

    def raised(text: str) -> str:
        try:
            raise RuntimeError(text)
        except RuntimeError as error:
            return exception_text(error)

    return {
        "display_safe": (display_safe, ""),
        "operator_safe": (operator_safe, ""),
        "project_consent._operator_safe": (consent_safe, ""),
        "installer._operator_safe": (installer_safe, ""),
        "exception_text": (raised, LOG_LAYOUT),
        "log message": (log_line, LOG_LAYOUT),
        "log traceback": (log_traceback, LOG_LAYOUT),
    }


@pytest.mark.parametrize("renderer", sorted(_renderers()))
@pytest.mark.parametrize("klass", sorted(_CLASSES))
def test_no_renderer_shows_a_character_a_terminal_acts_on(
    renderer: str, klass: str
) -> None:
    render, keep = _renderers()[renderer]
    for ch in _CLASSES[klass]:
        shown = render(f"SENTINEL{ch}X")
        assert _printable(shown, keep), (renderer, klass, hex(ord(ch)), shown)
        if ch not in keep:
            assert ch not in shown, (renderer, hex(ord(ch)))


@pytest.mark.parametrize("renderer", sorted(_renderers()))
def test_plain_text_is_unchanged(renderer: str) -> None:
    render, _keep = _renderers()[renderer]
    shown = render(_PLAIN)
    if renderer.endswith("operator_safe"):
        import shlex

        assert shlex.split(shown) == [_PLAIN]
    else:
        assert _PLAIN in shown, shown


def test_the_escape_set_is_every_non_printable_character() -> None:
    """Over the whole Basic Multilingual Plane: a character is shown as it
    is exactly when Python counts it printable."""
    from pmcp.display import display_safe

    for code in range(0x10000):
        ch = chr(code)
        if 0xD800 <= code <= 0xDFFF:
            continue
        assert (display_safe(ch) == ch) is ch.isprintable(), hex(code)


# --- grok F001 and codex F001: `pmcp doctor` ----------------------------------

_DOCTOR_URL_FIELDS = (
    "url",
    "protected_resource_metadata_url",
    "authorization_server_metadata_url",
    "oidc_issuer_url",
    "oidc_discovery_url",
    "client_id_metadata_document_url",
)

#: URLs `urlparse` (or `.port`) rejects, each quoting what it rejected, and
#: one a terminal would act on that parses: the NFKC check (codex's), a
#: non-numeric port, an unclosed IPv6 bracket, and grok's C1 CSI.
_BAD_URLS = {
    "nfkc": "https://SENTINEL\x1bc\x07：80/",
    "port": "https://host:9\x1b[2J/",
    "ipv6": "https://[SENTINEL\x1b/",
    "c1": "https://example.com/meta\x9b2Jcleared",
    "idna": "https://SENTINEL\x1b​.éxample/",
}


def _doctor_lines(field: str, url: str, tmp_path: Path) -> list[str]:
    from pmcp.cli_commands.doctor import collect_remote_header_diagnostics

    entry = {"type": "http", "url": "https://example.com/mcp", field: url}
    checks = collect_remote_header_diagnostics({"mcpServers": {"s": entry}}, tmp_path)
    return [message for _area, _status, message in checks]


@pytest.mark.parametrize("shape", sorted(_BAD_URLS))
@pytest.mark.parametrize("field", _DOCTOR_URL_FIELDS)
def test_doctor_shows_no_url_raw(field: str, shape: str, tmp_path: Path) -> None:
    """Every doctor URL field, with each parse failure and grok's C1: the
    checks never raise, and every line is printable (a URL that parses and
    is not checked further gives no line)."""
    lines = _doctor_lines(field, _BAD_URLS[shape], tmp_path)
    for line in lines:
        assert _printable(line), (field, shape, line)


def test_grok_f001_a_c1_csi_in_a_metadata_url(tmp_path: Path) -> None:
    lines = _doctor_lines(
        "protected_resource_metadata_url",
        "https://example.com/meta\x9b2Jcleared",
        tmp_path,
    )
    assert not any("\x9b" in line for line in lines), lines
    assert any("\\x9b" in line for line in lines), lines


def test_codex_f001_an_unparseable_url_is_reported_not_raised(tmp_path: Path) -> None:
    lines = _doctor_lines("url", "https://SENTINEL\x1bc\x07：80/", tmp_path)
    assert any("is invalid" in line for line in lines), lines
    assert not any("\x1b" in line or "\x07" in line for line in lines), lines


#: Built at run time, so the test's own source lines (which a traceback
#: shows) do not hold it.
_S = "SENT" + "INEL"


@pytest.mark.parametrize(
    "parse",
    [
        lambda: __import__("urllib.parse").parse.urlparse(
            f"https://{_S}\x1bc\x07\uff1a80/"
        ),
        lambda: __import__("urllib.parse").parse.urlparse(f"https://h:9\x1b{_S}/").port,
        lambda: __import__("ipaddress").ip_address(f"{_S}\x1b[31m"),
        lambda: __import__("idna").encode(f"{_S}\x1b.com"),
        lambda: __import__("shlex").split(f"'{_S}\x1b"),
    ],
    ids=["urlparse-nfkc", "urlparse-port", "ipaddress", "idna", "shlex"],
)
def test_a_text_parsers_error_is_described_by_its_class(
    parse: Callable[[], object],
) -> None:
    """The funnel behind the parse-site catches: an error a text-parsing
    module raises quotes what it rejected, so it reads by its class."""
    from pmcp.argument_errors import exception_text, safe_traceback_text

    with pytest.raises(Exception) as caught:
        parse()
    text = exception_text(caught.value)
    assert _S.lower() not in text.lower() and _printable(text), text
    assert text.startswith("could not parse the text ("), text
    assert _S.lower() not in safe_traceback_text(caught.value).lower()


# --- claude N-A: log lines -----------------------------------------------------


def _log_stream() -> tuple[io.StringIO, logging.Handler]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logging.getLogger().addHandler(handler)
    return stream, handler


def test_a_config_server_name_is_escaped_in_the_skip_warning(tmp_path: Path) -> None:
    import pmcp  # noqa: F401
    from pmcp.config.loader import _merge_manifest_defaults
    from pmcp.types import LocalMcpServerConfig

    name = "SENTINEL297\x1b]0;title\x07‮"
    stream, handler = _log_stream()
    previous = logging.getLogger().level
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        result = _merge_manifest_defaults(
            name, LocalMcpServerConfig(args=[]), {}, lambda _key: None
        )
    finally:
        logging.getLogger().removeHandler(handler)
        logging.getLogger().setLevel(previous)
    assert result is None
    text = stream.getvalue()
    assert "Skipping server" in text, text
    assert _printable(text, "\n"), text
    assert "\\x1b" in text and "\\u202e" in text, text


def test_a_rejected_allow_private_registry_value_is_named_by_type(
    tmp_path: Path,
) -> None:
    import pmcp  # noqa: F401
    from pmcp.config.loader import registry_allow_private_from_config

    config = tmp_path / "user.mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {}, "allowPrivateRegistry": "SENTINEL297\x1b[31m"})
    )
    stream, handler = _log_stream()
    previous = logging.getLogger().level
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        assert (
            registry_allow_private_from_config(None, user_config_paths=[config]) is None
        )
    finally:
        logging.getLogger().removeHandler(handler)
        logging.getLogger().setLevel(previous)
    text = stream.getvalue()
    assert "allowPrivateRegistry" in text and "got a string" in text, text
    assert "SENTINEL297" not in text, text


# --- claude N-B: a message over the length cap ---------------------------------


def test_a_message_over_the_cap_says_why_it_is_not_shown() -> None:
    from pmcp.client.manager import _downstream_error
    from pmcp.sdk_rejections import MAX_CLASSIFIED_LENGTH, sdk_message_phrase

    error = _downstream_error(
        {"code": -32603, "message": "x" * (MAX_CLASSIFIED_LENGTH + 1)}
    )
    assert str(error) == (
        f"{sdk_message_phrase(-32603)} (the server's message was not shown: "
        f"over {MAX_CLASSIFIED_LENGTH} characters)"
    )


def test_a_wrapped_parser_error_still_describes_its_wrapper() -> None:
    """Only a context pmcp suppressed (`raise ... from None`, its own fixed
    refusal) is passed over; a parser error as a cause or a plain context
    still reads by its class, and the wrapper's own text is not shown."""
    from urllib.parse import urlparse

    from pmcp.argument_errors import exception_text

    def port() -> object:
        return urlparse(f"https://h:9{_S}/").port

    for link in ("cause", "context", "suppressed"):
        try:
            try:
                port()
            except ValueError as error:
                if link == "cause":
                    raise RuntimeError(f"wrapped {_S}") from error
                if link == "context":
                    raise RuntimeError(f"wrapped {_S}")  # noqa: B904 - the shape
                raise RuntimeError("pmcp's fixed refusal") from None
        except RuntimeError as wrapper:
            text = exception_text(wrapper)
        if link == "suppressed":
            assert text == "pmcp's fixed refusal", text
        else:
            assert text == "RuntimeError: could not parse the text (ValueError)", text
