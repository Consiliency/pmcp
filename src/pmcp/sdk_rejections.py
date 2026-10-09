"""The MCP SDK's own rejections and server-side logs, value-free (Consiliency/pmcp#297 rev 20).

Before any pmcp handler runs, the MCP SDK answers some requests itself: an
unknown method (``METHOD_NOT_FOUND`` with the caller's method as ``data``),
an unsupported protocol version (``data.requested``, the caller's string),
an ``initialize`` on a modern connection, the per-request envelope's ladder.
Those answers leave through one function on every transport -- stdio, the
legacy and the modern streamable HTTP paths -- the SDK's
``handler_exception_to_error_data``, which turns the exception a request
raised into its wire ``ErrorData``; the modern HTTP path's envelope ladder
writes its own (``_write_rejection``). pmcp wraps both. An error raised by
pmcp's own handlers passes unchanged: ``pmcp.server._described_errors`` marks
every exception it lets escape (:data:`PMCP_HANDLER_MARK`), and has already
applied pmcp's rule to it. Every other error is the SDK's, and is rebuilt
from what pmcp has reviewed:

- the code is kept, and so is the request id (the dispatcher writes it);
- the message is kept when it is a string literal in the SDK's source, or
  matches an f-string template whose placeholders pmcp has reviewed as SDK
  constants or pmcp's own schema (:data:`REVIEWED_MESSAGE_TEMPLATES`);
  otherwise it is a fixed phrase for the code. An SDK upgrade that adds a
  template fails closed until it is reviewed;
- ``data`` is kept only in a reviewed shape: the unsupported-version payload
  with ``supported`` and a ``requested`` that is a protocol revision (else
  ``""``), ``""``, or a literal ``reason``. Anything else -- the method name
  among it -- is dropped.

The SDK's server-side loggers (``mcp.server.*``, ``mcp.shared.*``) and
``sse_starlette`` log request content too (the method of a dropped
notification, every SSE chunk verbatim). Their records are scrubbed at
creation (:func:`scrub_sdk_record`): every text or bytes ``%``-argument
becomes ``<text>``, and a message the SDK pre-formatted with an f-string has
each placeholder replaced by ``<...>``.

``tests/test_http_transport.py`` derives the SDK's error and log constructions
from the whole installed ``mcp`` package by AST and pins their classification
exactly, both ways.
"""

from __future__ import annotations

import ast
import functools
import logging
import re
from pathlib import Path
from typing import Any

#: Set on every exception ``pmcp.server._described_errors`` lets escape: an
#: error pmcp's own handler raised, already rendered by pmcp's rule.
PMCP_HANDLER_MARK = "pmcp_handler_error"

#: The constructions whose message reaches the wire, by name, and where their
#: message argument sits: (keyword, positional index or ``None``).
_MESSAGE_ARGUMENTS: dict[str, tuple[str, int | None]] = {
    "ErrorData": ("message", None),
    "MCPError": ("message", 1),
    "McpError": ("message", 1),
    "InboundLadderRejection": ("message", None),
    "_create_error_response": ("error_message", 0),
}

#: f-string message templates (their source, as ``ast.unparse`` prints it)
#: that pmcp keeps, each bound to the one code its site raises and, per
#: placeholder, to the vocabulary it may take (rev 21, round-19 claude N2: a
#: template no longer matches across sites, and a placeholder never matches
#: arbitrary text):
#: - ``constant``: the placeholder's own expression, evaluated in the SDK
#:   module (an SDK constant);
#: - ``routing_header``: the SDK's fixed routing-header names;
#: - ``meta_keys``: a ``", "``-join of the two envelope keys, in order;
#: - ``name_key``: a parameter name from the SDK's ``NAME_BEARING_METHODS``;
#: - ``param_header`` / ``param_argument``: an ``x-mcp-header`` token, and
#:   the argument path declaring it, from pmcp's own gateway tool schemas
#:   (pmcp declares none today, so those templates match nothing).
REVIEWED_MESSAGE_TEMPLATES: dict[str, tuple[int, tuple[str, ...]]] = {
    "f'{duplicated} header appears more than once'": (-32020, ("routing_header",)),
    "f'params._meta must be an object carrying the required {PROTOCOL_VERSION_META_KEY!r} and {CLIENT_CAPABILITIES_META_KEY!r} envelope keys'": (
        -32602,
        ("constant", "constant"),
    ),
    "f\"params._meta is missing the required envelope key(s): {', '.join(missing)}\"": (
        -32602,
        ("meta_keys",),
    ),
    'f"{MCP_PROTOCOL_VERSION_HEADER} header does not match the request envelope\'s protocol version"': (
        -32020,
        ("constant",),
    ),
    'f"{MCP_METHOD_HEADER} header does not match the request body\'s method"': (
        -32020,
        ("constant",),
    ),
    'f"{MCP_NAME_HEADER} header does not match the request body\'s {name_key!r} parameter"': (
        -32020,
        ("constant", "name_key"),
    ),
    "f'{header_name} header appears more than once'": (-32020, ("param_header",)),
    'f"{header_name} header is present but the request body\'s {argument!r} argument is absent"': (
        -32020,
        ("param_header", "param_argument"),
    ),
    'f"{header_name} header does not match the request body\'s {argument!r} argument"': (
        -32020,
        ("param_header", "param_argument"),
    ),
    'f"{header_name} header is missing but the request body\'s {argument!r} argument is present"': (
        -32020,
        ("param_header", "param_argument"),
    ),
    "f'{header_name} header carries a malformed base64 sentinel value'": (
        -32020,
        ("param_header",),
    ),
}

#: The phrase for a code when the SDK's message is not one pmcp has reviewed.
_CODE_PHRASES: dict[int, str] = {
    -32700: "Parse error",
    -32600: "Invalid request",
    -32601: "Method not found",
    -32602: "Invalid params",
    -32603: "Internal error",
    -32022: "Unsupported protocol version",
    -32021: "Missing required client capability",
    -32020: "Header mismatch",
    -32001: "Request timed out",
    -32000: "Connection closed",
}
_DEFAULT_PHRASE = "Request failed"

_PROTOCOL_REVISION = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Logger-name prefixes whose records are scrubbed by :func:`scrub_sdk_record`:
#: the whole `mcp` package -- server, shared and client (rev 26, round-24
#: claude F001: `mcp.client.*` logged `Unknown SSE event: <name>` and a
#: rejected endpoint URL) -- and `sse_starlette`. The SDK also names loggers
#: by literal (`mcp/client/session.py` logs as ``"client"``); those come from
#: the installed package's source (:func:`sdk_logger_names`).
SDK_LOGGERS = ("mcp", "sse_starlette")
#: The HTTP client stacks' loggers (rev 23, round-21 claude F001): their
#: traces quote response bytes (`receive_response_headers.failed
#: exception=RemoteProtocolError(... b'<status line>')`, httpx's
#: `HTTP Request: ... "HTTP/1.1 500 <reason>"`). Masked like the SDK's, and a
#: pre-formatted message keeps only its first word, the trace event's name.
HTTP_CLIENT_LOGGERS = (
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
    "h11",
    "aiohttp",
    "urllib3",
)
_TEXT = "<text>"
_PLACEHOLDER = "<...>"


def _mcp_root() -> Path:
    import mcp

    return Path(mcp.__file__).parent


_LOGGER_FACTORIES = ("getLogger", "get_logger")


def logger_name_arguments(tree: ast.AST) -> list[ast.expr]:
    """The name argument of every ``getLogger``/``get_logger`` call."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) in _LOGGER_FACTORIES:
            if node.args:
                found.append(node.args[0])
    return found


@functools.cache
def sdk_logger_names() -> tuple[str, ...]:
    """Every logger-name prefix the installed SDK logs under: the package
    (``__name__`` in every module) and each literal name it passes to a
    logger factory (rev 26)."""
    names = set(SDK_LOGGERS)
    for path in sorted(_mcp_root().rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for argument in logger_name_arguments(tree):
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                names.add(argument.value)
    return tuple(sorted(names))


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def message_arguments(tree: ast.AST) -> list[ast.expr]:
    """Every message argument of a wire-error construction in ``tree``."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        spec = _MESSAGE_ARGUMENTS.get(_call_name(node) or "")
        if spec is None:
            continue
        keyword, index = spec
        for item in node.keywords:
            if item.arg == keyword:
                found.append(item.value)
        if index is not None and len(node.args) > index:
            found.append(node.args[index])
    return found


def _template_pattern(node: ast.JoinedStr) -> re.Pattern[str]:
    """A template with every placeholder matching anything (for logs)."""
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(re.escape(value.value))
        else:
            parts.append("(.*?)")
    return re.compile("".join(parts), re.DOTALL)


def _converted(value: Any, conversion: int) -> str:
    if conversion == ord("r"):
        return repr(value)
    if conversion == ord("a"):
        return ascii(value)
    return str(value)


@functools.cache
def _pmcp_header_positions() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The ``x-mcp-header`` tokens pmcp's own gateway tools declare, as
    header names, and the argument paths that declare them."""
    from mcp.shared.inbound import MCP_PARAM_HEADER_PREFIX, _annotated_positions

    from pmcp.tools.handlers import get_gateway_tool_definitions

    headers: set[str] = set()
    arguments: set[str] = set()
    for tool in get_gateway_tool_definitions():
        for path, token, _schema in _annotated_positions(tool.input_schema):
            headers.add(f"{MCP_PARAM_HEADER_PREFIX}{token}")
            arguments.add(".".join(path))
    return tuple(sorted(headers)), tuple(sorted(arguments))


def _vocabulary(kind: str, node: ast.FormattedValue, module: Any) -> list[str]:
    """The texts placeholder ``node`` (of vocabulary ``kind``) may render."""
    import mcp.shared.inbound as inbound

    conversion = node.conversion
    if kind == "constant":
        value = eval(ast.unparse(node.value), vars(module))  # noqa: S307 -- SDK source
        return [_converted(value, conversion)]
    if kind == "routing_header":
        return [_converted(name, conversion) for name in inbound._ROUTING_HEADER_NAMES]
    if kind == "meta_keys":
        keys = [inbound.PROTOCOL_VERSION_META_KEY, inbound.CLIENT_CAPABILITIES_META_KEY]
        return [keys[0], keys[1], ", ".join(keys)]
    if kind == "name_key":
        return [
            _converted(name, conversion)
            for name in sorted(set(inbound.NAME_BEARING_METHODS.values()))
        ]
    headers, arguments = _pmcp_header_positions()
    if kind == "param_header":
        return [_converted(name, conversion) for name in headers]
    if kind == "param_argument":
        return [_converted(name, conversion) for name in arguments]
    raise ValueError(kind)


def _bound_pattern(
    node: ast.JoinedStr, kinds: tuple[str, ...], module: Any
) -> re.Pattern[str]:
    """``node`` with each placeholder matching only its vocabulary."""
    parts: list[str] = []
    placeholders = iter(kinds)
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(re.escape(value.value))
        elif isinstance(value, ast.FormattedValue):
            words = _vocabulary(next(placeholders), value, module)
            parts.append(
                "(?:" + "|".join(re.escape(w) for w in words) + ")" if words else "(?!)"
            )
    return re.compile("".join(parts))


@functools.cache
def _message_registry() -> tuple[
    frozenset[str], tuple[tuple[int, re.Pattern[str]], ...]
]:
    """The SDK's literal wire messages, and each reviewed template's (code,
    bound pattern), read from the installed package's source once."""
    import importlib

    literals: set[str] = set()
    patterns: list[tuple[int, re.Pattern[str]]] = []
    root = _mcp_root()
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module = None
        for argument in message_arguments(tree):
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                literals.add(argument.value)
            elif isinstance(argument, ast.JoinedStr):
                reviewed = REVIEWED_MESSAGE_TEMPLATES.get(ast.unparse(argument))
                if reviewed is None:
                    continue
                if module is None:
                    name = (
                        path.relative_to(root.parent)
                        .with_suffix("")
                        .as_posix()
                        .replace("/", ".")
                    )
                    module = importlib.import_module(name)
                code, kinds = reviewed
                patterns.append((code, _bound_pattern(argument, kinds, module)))
    return frozenset(literals), tuple(patterns)


def _reviewed_message(message: Any, code: Any = None) -> bool:
    if not isinstance(message, str):
        return False
    literals, patterns = _message_registry()
    return message in literals or any(
        bound == code and pattern.fullmatch(message) for bound, pattern in patterns
    )


def _reviewed_data(code: Any, data: Any) -> Any:
    """``data`` in a reviewed shape, or ``None``."""
    if data is None or data == "":
        return data
    if isinstance(data, dict):
        if code == -32022:
            supported = data.get("supported")
            requested = data.get("requested")
            out: dict[str, Any] = {}
            if isinstance(supported, list) and all(
                isinstance(item, str) and _PROTOCOL_REVISION.fullmatch(item)
                for item in supported
            ):
                out["supported"] = list(supported)
            if "requested" in data:
                out["requested"] = (
                    requested
                    if isinstance(requested, str)
                    and _PROTOCOL_REVISION.fullmatch(requested)
                    else ""
                )
            return out
        if data == {"reason": "invalid_request_state"}:  # request_state.py, literal
            return data
    return None


def value_free_error_data(error: Any) -> Any:
    """``error`` (an SDK ``ErrorData``) rebuilt from what pmcp has reviewed:
    the code; the message if reviewed, else the code's phrase; ``data`` only
    in a reviewed shape."""
    from mcp_types import ErrorData

    raw_code = getattr(error, "code", None)
    code: int = raw_code if isinstance(raw_code, int) else 0
    raw_message = getattr(error, "message", None)
    message: str = (
        raw_message
        if isinstance(raw_message, str) and _reviewed_message(raw_message, code)
        else _CODE_PHRASES.get(code, _DEFAULT_PHRASE)
    )
    data = _reviewed_data(code, getattr(error, "data", None))
    if data is None:
        return ErrorData(code=code, message=message)
    return ErrorData(code=code, message=message, data=data)


def _is_pmcp_handler_error(exc: BaseException) -> bool:
    return bool(getattr(exc, PMCP_HANDLER_MARK, False))


def _wrap_exception_mapping(original: Any) -> Any:
    def mapping(exc: BaseException) -> Any:
        error = original(exc)
        if _is_pmcp_handler_error(exc):
            return error
        if error is not None:
            return value_free_error_data(error)
        # An SDK-internal failure: the dispatcher would send `str(exc)`.
        from pmcp.argument_errors import safe_exc_info

        logging.getLogger("pmcp.sdk_rejections").error(
            "MCP SDK request handling raised %s",
            type(exc).__name__,
            exc_info=safe_exc_info(exc),
        )
        from mcp_types import ErrorData

        return ErrorData(code=0, message=_DEFAULT_PHRASE)

    mapping.pmcp_value_free = True  # type: ignore[attr-defined]
    mapping.original = original  # type: ignore[attr-defined]
    return mapping


def _wrap_write_rejection(original: Any) -> Any:
    async def write_rejection(rejection: Any, *args: Any, **kwargs: Any) -> None:
        from mcp.shared.inbound import InboundLadderRejection

        error = value_free_error_data(rejection)
        rebuilt = InboundLadderRejection(
            code=error.code, message=error.message, data=error.data
        )
        await original(rebuilt, *args, **kwargs)

    write_rejection.pmcp_value_free = True  # type: ignore[attr-defined]
    write_rejection.original = original  # type: ignore[attr-defined]
    return write_rejection


def install_value_free_sdk_errors() -> None:
    """Wrap the SDK's exception-to-wire mapping wherever it is bound, and the
    modern HTTP path's ladder writer. Idempotent."""
    import mcp.server._streamable_http_modern as modern
    import mcp.server.runner as runner
    import mcp.shared.jsonrpc_dispatcher as dispatcher

    for module in (dispatcher, runner):
        current = getattr(module, "handler_exception_to_error_data")
        if not getattr(current, "pmcp_value_free", False):
            setattr(
                module,
                "handler_exception_to_error_data",
                _wrap_exception_mapping(current),
            )
    current = modern._write_rejection
    if not getattr(current, "pmcp_value_free", False):
        modern._write_rejection = _wrap_write_rejection(current)


# --- errors the SDK raises, client side included (rev 26) ---------------------

#: Raise sites (``path::function`` under the `mcp` package) of an `MCPError`
#: that relays an `ErrorData` built elsewhere: a peer's response
#: (`send_raw_request`), or pmcp's own handler or callback result. Its message
#: is kept unless it matches a template the SDK itself builds -- the SDK's
#: client transport hands its own `ErrorData` (`Unexpected content type:
#: <type>`) to the session as if the peer had sent it (round-24 claude F001).
#: `tests/test_http_transport.py` pins this set against the SDK's raises.
RECEIVED_ERROR_SITES = frozenset(
    {
        "client/_input_required.py::_dispatch_all",
        "client/session.py::_on_request",
        "server/runner.py::_dump_result",
        "server/runner.py::_inner",
        "server/runner.py::on_request",
        "shared/jsonrpc_dispatcher.py::send_raw_request",
    }
)


def _is_error_call(name: str | None) -> bool:
    return bool(name) and (
        name in _MESSAGE_ARGUMENTS
        or str(name).endswith(("Error", "Exception", "Warning"))
    )


def error_message_arguments(tree: ast.AST) -> list[tuple[str, ast.expr]]:
    """(callee, message argument) of every exception or wire-error
    construction in ``tree``: the ``message``/``msg`` keyword, else the
    message's position (the second for ``MCPError``, else the first)."""
    found: list[tuple[str, ast.expr]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if not _is_error_call(name):
            continue
        keyword, index = _MESSAGE_ARGUMENTS.get(str(name), ("message", 0))
        value = next(
            (item.value for item in node.keywords if item.arg in (keyword, "msg")),
            None,
        )
        if value is None and index is not None and len(node.args) > index:
            value = node.args[index]
        if value is not None:
            found.append((str(name), value))
    return found


def _message_pattern(node: ast.expr) -> re.Pattern[str] | None:
    """A pattern for a message the SDK formats: an f-string, a ``%``-format
    or a ``.format`` of a literal, each placeholder matching anything."""
    if isinstance(node, ast.JoinedStr):
        return _template_pattern(node)
    literal: str | None = None
    placeholder: re.Pattern[str] | None = None
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Mod)
        and isinstance(node.left, ast.Constant)
        and isinstance(node.left.value, str)
    ):
        literal, placeholder = (
            node.left.value,
            re.compile(r"%[-#0 +]*\d*(?:\.\d+)?[sdirfx%]"),
        )
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
    ):
        literal, placeholder = node.func.value.value, re.compile(r"\{[^{}]*\}")
    if literal is None or placeholder is None:
        return None
    parts = placeholder.split(literal)
    return re.compile("(.*?)".join(re.escape(part) for part in parts), re.DOTALL)


@functools.cache
def _sdk_message_texts() -> tuple[frozenset[str], tuple[re.Pattern[str], ...]]:
    """Every literal message the SDK raises or answers with, and a pattern
    for every message it formats, from the installed package's source."""
    literals: set[str] = set()
    patterns: list[re.Pattern[str]] = []
    for path in sorted(_mcp_root().rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for _name, argument in error_message_arguments(tree):
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                literals.add(argument.value)
                continue
            pattern = _message_pattern(argument)
            if pattern is not None:
                patterns.append(pattern)
    return frozenset(literals), tuple(patterns)


@functools.cache
def _mcp_prefix() -> str:
    return str(_mcp_root()) + "/"


def raise_site(error: BaseException) -> str | None:
    """``path::function`` of the SDK frame that decided ``error``'s origin
    (the same walk as :func:`pmcp.argument_errors.exception_origin`, rev
    27), or ``None`` when its origin is not the SDK."""
    from pmcp.argument_errors import exception_walk

    walk = exception_walk(error)
    if walk.kind != "mcp" or walk.filename is None:
        return None
    prefix = _mcp_prefix()
    if not walk.filename.startswith(prefix):
        return None
    return f"{walk.filename[len(prefix) :]}::{walk.function}"


def withheld_sdk_error(error: BaseException) -> bool:
    """Whether ``error``, raised under the SDK, is withheld (rev 26; fail
    closed since rev 27). An exception whose origin is the SDK keeps its
    text only when it is positively matched where the SDK raised it itself
    (the deciding frame is the raising frame): an empty message, a literal
    of the SDK's source, a reviewed template, or -- at a
    :data:`RECEIVED_ERROR_SITES` raise -- a relayed message that matches no
    template the SDK builds. Everything else is withheld: text the SDK
    formats, ``str(e)``, and anything a library helper raised beneath an SDK
    frame (`ipaddress` quoting a rejected endpoint host, round-25 grok and
    codex F001). A missing site never lets an error through."""
    from pmcp.argument_errors import exception_walk

    walk = exception_walk(error)
    if walk.kind != "mcp":
        return False
    site = raise_site(error)
    if site is None or not walk.direct:
        return True
    try:
        text = str(error)
    except Exception:  # noqa: BLE001 -- an unprintable error is withheld
        return True
    if not text:
        return False
    literals, _patterns = _sdk_message_texts()
    if text in literals or _reviewed_message(text, getattr(error, "code", None)):
        return False
    if site not in RECEIVED_ERROR_SITES:
        return True
    return sdk_built_message(text, getattr(error, "code", None))


#: A formatted SDK message with less literal text than this is too general
#: to recognise a relayed message by (``f"{error}"`` would match anything);
#: `tests/test_http_transport.py` pins that the SDK has none today.
_MIN_TEMPLATE_LITERAL = 8


def _literal_length(pattern: re.Pattern[str]) -> int:
    return len(re.sub(r"\\(.)", r"\1", pattern.pattern.replace("(.*?)", "")))


def sdk_built_message(message: Any, code: Any = None) -> bool:
    """Whether ``message`` -- relayed as a peer's -- is one the SDK formats
    itself and pmcp has not reviewed: the SDK's client transport answers a
    request with its own `ErrorData` (`Unexpected content type: <type>`,
    round-24 claude F001), which reaches pmcp as if the downstream sent it."""
    if not isinstance(message, str) or _reviewed_message(message, code):
        return False
    _literals, patterns = _sdk_message_texts()
    return any(
        _literal_length(pattern) >= _MIN_TEMPLATE_LITERAL and pattern.fullmatch(message)
        for pattern in patterns
    )


def sdk_message_phrase(code: Any) -> str:
    """The fixed phrase for ``code`` (:data:`_CODE_PHRASES`)."""
    return (
        _CODE_PHRASES.get(code, _DEFAULT_PHRASE)
        if isinstance(code, int)
        else (_DEFAULT_PHRASE)
    )


def sdk_error_text(error: BaseException) -> str:
    """A withheld SDK error: its code's phrase, class and code, or its
    class alone."""
    code = getattr(error, "code", None)
    name = type(error).__name__
    if type(code) is int:
        return f"{_CODE_PHRASES.get(code, _DEFAULT_PHRASE)} ({name}, code {code})"
    return f"the MCP SDK raised {name}"


# --- logs ---------------------------------------------------------------------


def log_messages(tree: ast.AST) -> list[ast.expr]:
    """The message argument of every logger call in ``tree``."""
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr
            in ("debug", "info", "warning", "error", "exception", "critical", "log")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("logger", "log", "_logger")
        ):
            args = node.args[1:] if node.func.attr == "log" else node.args
            if args:
                found.append(args[0])
    return found


@functools.cache
def _log_literals() -> frozenset[str]:
    """Every literal message an SDK logger call is given (rev 26): a record
    of an SDK logger whose message is neither one of these nor an f-string
    template's is masked whole -- `logger.error(error_msg)` logged a
    pre-built `Endpoint origin does not match connection origin: <url>`."""
    literals: set[str] = set()
    for path in sorted(_mcp_root().rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for message in log_messages(tree):
            if isinstance(message, ast.Constant) and isinstance(message.value, str):
                literals.add(message.value)
    return frozenset(literals)


def log_templates(tree: ast.AST) -> list[ast.JoinedStr]:
    """Every f-string a logger call in ``tree`` is given as its message."""
    found: list[ast.JoinedStr] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr
            in ("debug", "info", "warning", "error", "exception", "critical", "log")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("logger", "log", "_logger")
        ):
            args = node.args[1:] if node.func.attr == "log" else node.args
            if args and isinstance(args[0], ast.JoinedStr):
                found.append(args[0])
    return found


@functools.cache
def _log_patterns() -> tuple[tuple[re.Pattern[str], str], ...]:
    """(pattern, the message with each placeholder as ``<...>``) for every
    f-string log message anywhere in the SDK (rev 26: the client too)."""
    root = _mcp_root()
    out: list[tuple[re.Pattern[str], str]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for template in log_templates(tree):
            masked = "".join(
                value.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
                else _PLACEHOLDER
                for value in template.values
            )
            out.append((_template_pattern(template), masked))
    # Longest literal text first, so a specific template wins over a general one.
    out.sort(key=lambda item: -len(item[1].replace(_PLACEHOLDER, "")))
    return tuple(out)


#: The argument types a masked record keeps, by exact type (rev 30, round-28
#: codex F001: an HTTP client's URL object survived the old rule, which
#: masked only text and bytes). Everything else -- text, bytes, URLs, paths,
#: exceptions, containers, any object -- is masked whole. Enum members of
#: pmcp's, `mcp_types`' or the SDK's own enums are kept too
#: (:func:`_value_free_argument`). `tests/test_http_transport.py` pins this
#: set exactly.
VALUE_FREE_ARGUMENT_TYPES: frozenset[type] = frozenset({int, float, bool, type(None)})
_ENUM_PACKAGES = ("pmcp", "mcp_types", "mcp")


def _value_free_argument(value: Any) -> bool:
    if type(value) in VALUE_FREE_ARGUMENT_TYPES:
        return True
    import enum

    kind = type(value)
    return isinstance(value, enum.Enum) and (
        str(getattr(kind, "__module__", "")).split(".")[0] in _ENUM_PACKAGES
    )


def _masked_argument(value: Any) -> Any:
    """``value`` if its type is value-free by the allowlist, else ``<text>``."""
    return value if _value_free_argument(value) else _TEXT


#: The attributes every `LogRecord` has; any other is an `extra=` field.
RECORD_ATTRIBUTES = frozenset(
    vars(logging.LogRecord("n", 0, "p", 0, "m", None, None))
) | {"message", "asctime"}


def mask_extras(record: logging.LogRecord) -> None:
    """Mask every `extra=` field of ``record`` by the same allowlist."""
    for key, value in list(vars(record).items()):
        if key not in RECORD_ATTRIBUTES and not _value_free_argument(value):
            setattr(record, key, _TEXT)


def _under(name: Any, prefixes: tuple[str, ...]) -> bool:
    return isinstance(name, str) and any(
        name == prefix or name.startswith(prefix + ".") for prefix in prefixes
    )


def is_sdk_logger(name: Any) -> bool:
    return _under(name, sdk_logger_names()) or _under(name, HTTP_CLIENT_LOGGERS)


def scrub_sdk_record(record: logging.LogRecord) -> None:
    """For a record of an SDK server-side logger, ``sse_starlette`` or an
    HTTP client stack: mask every text ``%``-argument, and every placeholder
    of a message the SDK pre-formatted with an f-string (an HTTP client's
    pre-formatted message keeps only its first word). In place; never
    raises."""
    if not is_sdk_logger(record.name):
        return
    try:
        from pmcp.argument_errors import safe_exc_info

        error = record.exc_info[1] if isinstance(record.exc_info, tuple) else None
        if isinstance(error, BaseException) and safe_exc_info(error) is not None:
            # A chain that holds a registered error is described by the
            # record scrubber; any other is kept as frames and classes.
            # The traceback's frames, and each exception by its class alone
            # (rev 26: `logger.exception("Error in sse_reader")` printed a
            # `ValueError` naming a rejected endpoint URL).
            from pmcp.argument_errors import class_only_traceback_text

            record.exc_text = class_only_traceback_text(error)
            record.exc_info = None
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_masked_argument(item) for item in args)
        elif isinstance(args, dict):
            record.args = {key: _masked_argument(item) for key, item in args.items()}
        elif args is not None:
            record.args = (_TEXT,)
        mask_extras(record)
        try:
            record.getMessage()
        except Exception:  # noqa: BLE001 -- a masked argument a format rejects
            record.msg, record.args = _PLACEHOLDER, None
            return
        if _under(record.name, HTTP_CLIENT_LOGGERS):
            if not args and isinstance(record.msg, str):
                head, _, rest = record.msg.partition(" ")
                if rest:
                    record.msg = f"{head} {_PLACEHOLDER}"
            return
        if _under(record.name, ("sse_starlette",)):
            return  # its `%`-arguments are masked; its messages are literals
        if isinstance(record.msg, BaseException):
            return  # rendered by the record scrubber (`exception_text`)
        if not isinstance(record.msg, str):
            record.msg, record.args = _PLACEHOLDER, None
            return
        if record.msg in _log_literals():
            return
        if not args:
            for pattern, masked in _log_patterns():
                if pattern.fullmatch(record.msg):
                    record.msg = masked
                    return
        # A message the SDK built some other way (a variable, `%` or
        # `.format` before the call): masked whole (rev 26).
        record.msg, record.args = _PLACEHOLDER, None
    except Exception:  # noqa: BLE001 -- a log call must never fail here
        pass
