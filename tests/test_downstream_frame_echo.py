"""A downstream's malformed JSON-RPC reply never echoes into pmcp's output
(Consiliency/pmcp#297, rev 3).

The MCP SDK's client transports validate every frame a downstream sends. For
one they reject, the streamable-HTTP transport synthesises a JSON-RPC
`-32700` whose message is pydantic's text, rejected value included
(`mcp/client/streamable_http.py:188,407`), and every SDK transport logs the
failure with a traceback. The rev 2 board seat showed that value reaching the
`gateway.invoke` response, `gateway.health` and the log.

This sweep runs a **real** downstream on every transport pmcp speaks -- the
streamable-HTTP transport answering with JSON or with an event stream, the
legacy SSE transport, and stdio -- and connects it through the real
`ClientManager`. The downstream answers every request normally except one,
which it answers with a malformed frame carrying a sentinel. That covers
every request pmcp sends (`initialize`, the three listings, `tools/call`,
`resources/read`, `prompts/get`, `tasks/*`) and every envelope member the
SDK validates, made wrong: `result` not an object, `error.code` not an
integer, `error.message` not a string, `jsonrpc` not `"2.0"`, and a body
that is not JSON at all.

The value must not reach the response (unless pmcp accepted the frame and
returns its result as the product, which only stdio does, because pmcp parses
stdio frames without a model), the log (raw records, tracebacks,
`extra=`), `gateway.health`, stdout/stderr or warnings. One exact exemption
applies: stdio's `Non-JSON output` DEBUG line, which logs the downstream's
own non-protocol output by design (see the plan's *Non-goals*).
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import sys
import textwrap
import threading
import typing
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from mcp.types import (
    GetPromptRequestParams,
    ReadResourceRequestParams,
)

from pmcp.types import (
    LocalMcpServerConfig,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
)
from tests.test_argument_error_echo import (
    _FAMILIES,
    _call,
    _forbidden,
    _record_stable,
    _record_text,
    _server,
    _Tap,
)
from tests.test_scoped_advisor_audit import _make_ctx

_SHAPES = (
    "result-type",
    "error-code",
    "error-message",
    "jsonrpc",
    "not-json",
    # rev 8 (round-7 codex F028-F034): JSON-RPC-shaped frames a parser
    # rejects -- on a limit (nesting depth, an integer's digit count) or on
    # syntax. Each fails BEFORE the sentinel, so the failure's position does
    # not depend on the sentinel's length (the pair differential holds).
    "json-deep",
    "json-bigint",
    "json-syntax",
    # rev 10 (round-9 codex P1): a VALID envelope whose payload carries the
    # sentinel where the handler's own model rejects it -- the transport
    # accepts it, the SDK used to log it at DEBUG, then pmcp rejects it.
    "payload",
)
#: The shapes whose frame the parser rejects: stdio then also sends the real
#: reply, so the request completes instead of timing out.
_REJECTED_SHAPES = ("not-json", "json-deep", "json-bigint", "json-syntax")
_METHODS = (
    "initialize",
    "tools/list",
    "resources/list",
    "prompts/list",
    "tools/call",
    "resources/read",
    "prompts/get",
    "tasks/list",
    "tasks/get",
    "tasks/result",
    "tasks/cancel",
)
_TRANSPORTS = ("http-json", "http-sse", "legacy-sse", "stdio")
#: Three sentinel families: the shape-conditional axis is the argument
#: sweep's; here the transports and envelope positions are.
_FRAME_FAMILIES = ("hex", "alpha", "unicode")

#: The downstream's logic, shared by the HTTP handler and the stdio script:
#: answer `request` normally, unless it is the targeted method.
_DOWNSTREAM_LOGIC = textwrap.dedent(
    '''
    import json

    def normal(method, params):
        if method == "initialize":
            return {
                "protocolVersion": params.get("protocolVersion", "2025-11-25"),
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}, "tasks": {}},
                "serverInfo": {"name": "frames", "version": "1"},
            }
        if method == "tools/list":
            return {"tools": [{"name": "run", "inputSchema": {"type": "object"},
                               "execution": {"taskSupport": "optional"}}]}
        if method == "resources/list":
            return {"resources": [{"uri": "x://r", "name": "r"}]}
        if method == "prompts/list":
            return {"prompts": [{"name": "p"}]}
        if method == "tools/call":
            if params.get("task"):
                return {"task": {"taskId": "t", "status": "working"}}
            return {"content": [{"type": "text", "text": "ok"}]}
        if method == "resources/read":
            return {"contents": [{"uri": "x://r", "text": "ok"}]}
        if method == "prompts/get":
            return {"messages": []}
        if method == "tasks/list":
            return {"tasks": [{"taskId": "t", "status": "working"}]}
        if method in ("tasks/get", "tasks/cancel"):
            return {"task": {"taskId": "t", "status": "working"}}
        if method == "tasks/result":
            return {"task": {"taskId": "t", "status": "completed"}, "result": {"content": []}}
        return {}

    def invalid_payload(method, s, params):
        """A result the envelope accepts but the handler rejects: the
        sentinel where pmcp's model for that method requires another type."""
        bad = {"x": s}
        if method == "initialize":
            result = normal(method, params)
            result["serverInfo"] = {"name": bad, "version": "1"}
            return result
        return {
            "tools/list": {"tools": [{"name": "run", "inputSchema": s}]},
            "resources/list": {"resources": [{"uri": "x://r", "name": bad}]},
            "prompts/list": {"prompts": [{"name": bad}]},
            "tools/call": {"content": [{"type": "text", "text": bad}]},
            "resources/read": {"contents": [{"uri": "x://r", "text": bad}]},
            "prompts/get": {"messages": [{"role": bad, "content": {"type": "text", "text": "m"}}]},
            "tasks/list": {"tasks": [{"taskId": "t", "status": "working", "ttl": s}]},
            "tasks/get": {"task": {"taskId": "t", "status": "working", "ttl": s}},
            "tasks/cancel": {"task": {"taskId": "t", "status": "working", "ttl": s}},
            "tasks/result": {"task": {"taskId": "t", "status": "completed", "ttl": s}, "result": {"content": []}},
        }[method]

    def reply(request, state):
        """The raw reply body (bytes) for `request`, or None for a notification."""
        method, rid = request.get("method"), request.get("id")
        if rid is None:
            return None
        s = state["s"]
        if method != state["method"]:
            frame = {"jsonrpc": "2.0", "id": rid, "result": normal(method, request.get("params") or {})}
        elif state["shape"] == "result-type":
            frame = {"jsonrpc": "2.0", "id": rid, "result": s}
        elif state["shape"] == "error-code":
            frame = {"jsonrpc": "2.0", "id": rid, "error": {"code": s, "message": "m"}}
        elif state["shape"] == "error-message":
            frame = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": {s: s}}}
        elif state["shape"] == "jsonrpc":
            frame = {"jsonrpc": s, "id": rid, "result": {}}
        elif state["shape"] == "json-deep":
            head = '{"jsonrpc":"2.0","id":%s,"error":{"message":"m","data":' % json.dumps(rid)
            return (head + "[" * 1500 + "0" + "]" * 1500 + ',"code":%s}}' % json.dumps(s)).encode()
        elif state["shape"] == "json-bigint":
            head = '{"jsonrpc":"2.0","id":%s,"error":{"message":"m","data":' % json.dumps(rid)
            return (head + "7" * 5000 + ',"code":%s}}' % json.dumps(s)).encode()
        elif state["shape"] == "payload":
            frame = {"jsonrpc": "2.0", "id": rid, "result": invalid_payload(method, s, request.get("params") or {})}
        elif state["shape"] == "json-syntax":
            head = '{"jsonrpc":"2.0","id":%s,"error":{"message":"m",,' % json.dumps(rid)
            return (head + '"code":%s}}' % json.dumps(s)).encode()
        else:
            return (s + " is not JSON {").encode()
        return json.dumps(frame).encode()
    '''
)
_logic: dict[str, Any] = {}
exec(_DOWNSTREAM_LOGIC, _logic)  # noqa: S102 -- the test's own downstream logic


class _Downstream:
    """A streamable-HTTP (`/mcp`) and legacy-SSE (`/sse`, `/messages`)
    downstream in one local server; `state` picks the malformed reply."""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {
            "method": None,
            "shape": None,
            "s": "",
            "sse": False,
        }
        self.streams: list[queue.Queue[bytes | None]] = []
        self.seen: set[str | None] = set()
        downstream = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def _body(self) -> dict[str, Any]:
                return json.loads(self.rfile.read(int(self.headers["content-length"])))

            def do_POST(self) -> None:  # noqa: N802
                request = self._body()
                downstream.seen.add(request.get("method"))
                data = _logic["reply"](request, downstream.state)
                if self.path.startswith("/messages"):
                    if data is not None and downstream.streams:
                        downstream.streams[-1].put(data)
                        if request.get("method") == downstream.state["method"]:
                            # The read loop drops a frame that fails JSON-RPC
                            # validation and keeps reading (Consiliency/pmcp#287),
                            # as stdio does: follow it with the real reply, or
                            # the request only times out.
                            downstream.streams[-1].put(
                                _logic["reply"](
                                    request, {**downstream.state, "method": None}
                                )
                            )
                    self.send_response(202)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                if data is None:
                    self.send_response(202)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                if downstream.state["sse"]:
                    data = b"event: message\ndata: " + data + b"\n\n"
                    kind = "text/event-stream"
                else:
                    kind = "application/json"
                self.send_response(200)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                if not self.path.startswith("/sse"):
                    self.send_response(405)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                stream: queue.Queue[bytes | None] = queue.Queue()
                downstream.streams.append(stream)
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("cache-control", "no-cache")
                self.end_headers()
                self.wfile.write(b"event: endpoint\ndata: /messages?session=1\n\n")
                self.wfile.flush()
                while (data := stream.get()) is not None:
                    try:
                        self.wfile.write(b"event: message\ndata: " + data + b"\n\n")
                        self.wfile.flush()
                    except OSError:
                        return

            def do_DELETE(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("content-length", "0")
                self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self) -> None:
        for stream in self.streams:
            stream.put(None)
        self.httpd.shutdown()
        self.httpd.server_close()


_STDIO_SCRIPT = _DOWNSTREAM_LOGIC + textwrap.dedent(
    """
    import sys
    REJECTED = @@REJECTED@@
    state_path = sys.argv[1]
    for line in sys.stdin:
        with open(state_path) as handle:
            state = json.load(handle)
        request = json.loads(line)
        with open(state_path + ".seen", "a") as seen:
            seen.write(str(request.get("method")) + "\\n")
        data = reply(request, state)
        if data is None:
            continue
        if request.get("method") == state["method"] and state["shape"] in REJECTED:
            # stdio frames are lines: the rejected line, then the reply the
            # request is waiting for (else it would only time out).
            sys.stdout.buffer.write(data + b"\\n")
            data = reply(request, {**state, "method": None})
        sys.stdout.buffer.write(data + b"\\n")
        sys.stdout.flush()
    """
).replace("@@REJECTED@@", repr(_REJECTED_SHAPES))


def _config(transport: str, downstream: _Downstream, tmp: Path) -> ResolvedServerConfig:
    if transport == "stdio":
        script = tmp / "frames_downstream.py"
        script.write_text(_STDIO_SCRIPT)
        config: Any = LocalMcpServerConfig(
            command=sys.executable, args=[str(script), str(tmp / "state.json")]
        )
    elif transport == "legacy-sse":
        config = RemoteMcpServerConfig(type="sse", url=f"{downstream.base}/sse")
    else:
        config = RemoteMcpServerConfig(type="http", url=f"{downstream.base}/mcp")
    return ResolvedServerConfig(name="frames", source="custom", config=config)


async def _request(server: Any, method: str) -> str:
    """Drive `method` through pmcp's own surface; return what the caller sees."""
    if method == "tools/call":
        result = await _call(
            server,
            "gateway.invoke",
            {"tool_id": "frames::run", "options": {"timeout_ms": 3000}},
        )
    elif method.startswith("tasks/"):
        target = {"server_name": "frames", "task_id": "t"}
        if method == "tasks/cancel":
            from pmcp.types import McpTaskInfo

            server._client_manager._record_task(
                "frames", McpTaskInfo(task_id="t", status="working")
            )
        name = {
            "tasks/list": "gateway.tasks_list",
            "tasks/get": "gateway.tasks_get",
            "tasks/result": "gateway.tasks_result",
            "tasks/cancel": "gateway.tasks_cancel",
        }[method]
        args = {"server_name": "frames"} if method == "tasks/list" else target
        result = await _call(server, name, args)
    elif method == "resources/read":
        entry = server._server.get_request_handler("resources/read")
        try:
            result = await entry.handler(
                _make_ctx(), ReadResourceRequestParams(uri="x://r")
            )
        except Exception as error:  # noqa: BLE001 -- the SDK sends `str(error)`
            return f"raised {type(error).__name__}: {error}"
    elif method == "prompts/get":
        entry = server._server.get_request_handler("prompts/get")
        try:
            result = await entry.handler(
                _make_ctx(), GetPromptRequestParams(name="frames::p")
            )
        except Exception as error:  # noqa: BLE001 -- the SDK sends `str(error)`
            return f"raised {type(error).__name__}: {error}"
    else:
        return ""  # a connect-time method: the connect result is the output
    return result.model_dump_json()


def _accepted(response: str) -> bool:
    """A stdio frame pmcp accepted: its result is the product, by design."""
    try:
        payload = json.loads(response)
    except ValueError:
        return False
    content = payload.get("content") or []
    text = "".join(
        block.get("text", "") for block in content if isinstance(block, dict)
    )
    try:
        inner = json.loads(text)
    except ValueError:
        return False
    return isinstance(inner, dict) and (inner.get("ok") is True or "task" in inner)


#: The one timing-dependent line a teardown logs: whether a transport closed
#: inside its 5 s grace depends on the host, not on the frame.
_CLOSE_TIMEOUT = re.compile(
    r"\[frames\] remote transport did not close within [0-9.]+s; cancelling"
)


_NON_JSON_PREFIX = "[frames] Non-JSON output: "


#: The test's own statement of the JSON grammar (RFC 8259 section 3), not
#: the reader's: a value begins with one of these -- object, array, string,
#: number, `true`/`false`/`null` -- plus Python's `NaN`/`Infinity`.
_GRAMMAR_VALUE_START = '{["-0123456789tfnNI'
#: What opens a container or a string, where frame content can begin.
_GRAMMAR_OPENERS = '{["'


def _first_significant(line: str) -> str:
    """The first character a reader would see, written independently of the
    reader: skip whatever `str.isspace` calls space (all `.strip` removes),
    control and format characters (NUL, ESC, a BOM, ZWSP) and U+FFFD (an
    undecodable byte)."""
    for char in line:
        if not (
            char.isspace()
            or char == "\ufffd"
            or unicodedata.category(char) in ("Cc", "Cf")
        ):
            return char
    return ""


def _json_shaped(line: str) -> bool:
    """Rev 9's rule, from the grammar: a line whose first significant
    character can begin a JSON value is protocol data, never shown."""
    return _first_significant(line) in tuple(_GRAMMAR_VALUE_START)


def _carries_no_frame(shown: str) -> bool:
    """A shown text can be exempt only if it holds no character that could
    open frame content -- whatever the reader decided."""
    return not any(opener in shown for opener in _GRAMMAR_OPENERS)
    return False


def _non_json_line(record: logging.LogRecord) -> bool:
    """Exactly stdio's DEBUG line for a downstream's non-JSON output -- and
    only when what it shows holds no frame-opening character (rev 9): a
    record that could carry frame content is never exempt."""
    message = record.getMessage()
    return (
        record.name == "pmcp.client.manager"
        and record.levelno == logging.DEBUG
        and message.startswith(_NON_JSON_PREFIX)
        and _carries_no_frame(message[len(_NON_JSON_PREFIX) :])
        and not record.exc_info
    )


@pytest.fixture(autouse=True)
def _only_pytests_log_handlers() -> Any:
    """Detach root handlers other tests left behind (a CLI test's file
    handler whose directory is gone, say). A handler that fails while the
    SDK's `except` block is active makes `logging.Handler.handleError` print
    that exception's chain to stderr -- the rejected frame included -- past
    every filter; see the plan's *Unverified*. pytest's own handlers stay."""
    root = logging.getLogger()
    foreign = [h for h in root.handlers if not type(h).__module__.startswith("_pytest")]
    for handler in foreign:
        root.removeHandler(handler)
    yield
    for handler in foreign:
        root.addHandler(handler)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", _METHODS)
@pytest.mark.parametrize("transport", _TRANSPORTS)
async def test_no_malformed_frame_value_reaches_pmcps_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
    transport: str,
    method: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    # A failed connect is retried with back-off; the retries are the same
    # frames again, and waiting them out adds nothing.
    monkeypatch.setattr("pmcp.client.manager.RETRY_DELAYS", [0.0, 0.0, 0.0])
    downstream = _Downstream()
    downstream.state["sse"] = transport == "http-sse"
    server, _ = _server(tmp_path, audited=False)
    policy = server._policy_manager
    policy.is_server_allowed = lambda name: True  # type: ignore[method-assign]
    policy.is_tool_allowed = lambda tool_id: True  # type: ignore[method-assign]
    policy.is_resource_allowed = lambda resource_id: True  # type: ignore[method-assign]
    policy.is_prompt_allowed = lambda prompt_id: True  # type: ignore[method-assign]
    tap = _Tap(server, None, caplog, capfd, recwarn)
    manager = server._client_manager
    config = _config(transport, downstream, tmp_path)
    cases = 0
    try:
        for _ in (method,):
            for shape in _SHAPES:
                for family in _FRAME_FAMILIES:
                    seen: list[tuple[str, ...]] = []
                    for s in _FAMILIES[family]:
                        state = {
                            **downstream.state,
                            "method": method,
                            "shape": shape,
                            "s": s,
                        }
                        downstream.state.update(state)
                        (tmp_path / "state.json").write_text(json.dumps(state))
                        # A known task makes a forced disconnect send `tasks/cancel`,
                        # which may be the malformed method: forget it first.
                        manager._tasks.clear()
                        await manager.disconnect_server("frames", force=True)
                        # Same request ids for both sentinels of a pair.
                        manager._request_counters.pop("frames", None)
                        mark = tap.start()
                        errors = await manager.connect_server(config)
                        answer = await asyncio.wait_for(_request(server, method), 20)
                        accepted = _accepted(answer)
                        response = json.dumps(errors) + answer
                        health = "".join(
                            b.text
                            for b in (await _call(server, "gateway.health", {})).content
                        )
                        observed = tap.since(mark, response + health)
                        # The pair differential: the caller-facing text, and
                        # pmcp's own log records. The SDK's and httpx's
                        # transport chatter varies with task timing (how many
                        # messages were sent before a teardown); it is still
                        # in the leak check above.
                        pair_view = (
                            # An accepted answer is the downstream's data, with
                            # its timestamps: the pair compares rejections.
                            json.dumps(errors) + ("<accepted>" if accepted else answer),
                            # As a multiset: the transports' reader tasks log
                            # concurrently, so the order is not the pair's to
                            # keep.
                            "\n".join(
                                sorted(
                                    _record_stable(r)
                                    for r in caplog.records[mark[0] :]
                                    if r.name.split(".")[0] == "pmcp"
                                    and not _non_json_line(r)
                                    and not _CLOSE_TIMEOUT.fullmatch(r.getMessage())
                                )
                            ),
                            observed.streams,
                            observed.warnings,
                        )
                        records = caplog.records[mark[0] :]
                        if transport == "stdio":
                            observed = observed._replace(
                                raw_log="\n".join(
                                    _record_text(r)
                                    for r in records
                                    if not _non_json_line(r)
                                ),
                                log="\n".join(
                                    _record_stable(r)
                                    for r in records
                                    if not _non_json_line(r)
                                ),
                            )
                        leaks = observed.leaks(s)
                        if (
                            (transport == "stdio" or shape == "payload")
                            and leaks == ["response"]
                            and accepted
                        ):
                            # Accepted as the product, by design: the caller
                            # asked for this result. Never in the log.
                            leaks = []
                        assert leaks == [], (transport, method, shape, family, observed)
                        if transport == "stdio" and shape.startswith("json-"):
                            # No vacuous pass: the rejected frame reached the
                            # reader and was described, not exempted.
                            assert any(
                                "sent a JSON-RPC frame that could not be parsed"
                                in r.getMessage()
                                for r in records
                            ), (transport, method, shape, family)
                        seen.append(pair_view)
                    assert seen[0] == seen[1], (transport, method, shape, family, seen)
                    cases += 1
        assert cases == len(_SHAPES) * len(_FRAME_FAMILIES)
        # No vacuous pass: the downstream did answer the targeted method.
        seen_methods = set(downstream.seen)
        if transport == "stdio":
            seen_methods |= set((tmp_path / "state.json.seen").read_text().splitlines())
        assert method in seen_methods, seen_methods
    finally:
        manager._tasks.clear()
        await manager.disconnect_server("frames", force=True)
        await server.shutdown()
        downstream.close()


# --- the SDK's own ClientSession (rev 4, rev 3 board B1) ----------------------
#
# `refresh_server` (the gateway's startup description refresh) talks to a
# downstream through the SDK's `stdio_client` + `ClientSession`, whose logger
# is named "client" -- outside `mcp.*` -- and which logs a notification it
# rejects with `exc_info=True`. The downstream here sends one malformed
# message of each kind before it answers `tools/list`.

_SESSION_MESSAGES = {
    "notifications/message": lambda s: {"level": s, "data": "x"},
    "notifications/progress": lambda s: {"progressToken": {s: s}, "progress": 1},
    "notifications/resources/updated": lambda s: {"uri": [s]},
    "notifications/tools/list_changed": lambda s: s,
    "notifications/cancelled": lambda s: {"requestId": {s: s}},
    "request:roots/list": lambda s: s,
    "request:sampling/createMessage": lambda s: {s: s},
    "request:elicitation/create": lambda s: {"message": {s: s}},
    "request:ping": lambda s: [s],
}

#: The kinds whose envelope is well formed, so `ClientSession` itself (on the
#: "client" logger) is what rejects them.
_SESSION_LEVEL = {
    "notifications/message",
    "notifications/progress",
    "notifications/resources/updated",
    "notifications/cancelled",
}

_SESSION_SCRIPT = textwrap.dedent(
    """
    import json, sys
    with open(sys.argv[1]) as handle:  # the case, not on the command line:
        kind, params = json.load(handle)  # pmcp records the command line
    for line in sys.stdin:
        with open(sys.argv[1] + ".in", "a") as seen:
            seen.write(line)
        request = json.loads(line)
        rid, method = request.get("id"), request.get("method")
        if rid is None or method is None:
            continue
        if method == "initialize":
            result = {"protocolVersion": request["params"]["protocolVersion"],
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "d", "version": "1"}}
        else:
            if method == "tools/list":
                if kind.startswith("request:"):
                    frame = {"jsonrpc": "2.0", "id": 900, "method": kind[8:], "params": params}
                else:
                    frame = {"jsonrpc": "2.0", "method": kind, "params": params}
                sys.stdout.write(json.dumps(frame) + "\\n")
            result = {"tools": [{"name": "run", "inputSchema": {"type": "object"}}]} if method == "tools/list" else {}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\\n")
        sys.stdout.flush()
    """
)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(_SESSION_MESSAGES))
async def test_no_malformed_session_message_value_reaches_the_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
    kind: str,
) -> None:
    from pmcp.manifest.loader import ServerConfig
    from pmcp.manifest.refresher import refresh_server

    caplog.set_level(logging.DEBUG)
    script = tmp_path / "session_downstream.py"
    script.write_text(_SESSION_SCRIPT)
    tap = _Tap(typing.cast(Any, _NoServer()), None, caplog, capfd, recwarn)
    for family in _FRAME_FAMILIES:
        seen = []
        for s in _FAMILIES[family]:
            config = ServerConfig(
                name="d",
                description="d",
                keywords=[],
                install={},
                command=sys.executable,
                args=[str(script), str(tmp_path / "case.json")],
            )
            (tmp_path / "case.json").write_text(
                json.dumps([kind, _SESSION_MESSAGES[kind](s)])
            )
            mark = tap.start()
            result = await asyncio.wait_for(refresh_server(config, force=True), 30)
            observed = tap.since(mark, repr(result))
            assert observed.leaks(s) == [], (kind, family, observed)
            seen.append(
                "\\n".join(
                    sorted(
                        _record_stable(r)
                        for r in caplog.records[mark[0] :]
                        if r.name == "client" or r.name.split(".")[0] == "pmcp"
                    )
                )
            )
        assert seen[0] == seen[1], (kind, family, seen)
    # No vacuous pass: the SDK rejected the message -- in its envelope
    # (`mcp.client.stdio`) or, for a well-formed envelope, in `ClientSession`
    # (the "client" logger) -- and the scrubbed record says so.
    rejected = {
        r.name
        for r in caplog.records
        if r.name.split(".")[0] in ("client", "mcp")
        and "validation error" in r.getMessage()
    }
    replies = [
        json.loads(line)
        for line in (tmp_path / "case.json.in").read_text().splitlines()
    ]
    answered = any(r.get("id") == 900 and "error" in r for r in replies)
    assert rejected or answered, kind
    if kind in _SESSION_LEVEL:
        assert "client" in rejected, (kind, rejected)


class _NoServer:
    """`_Tap` outside a server: no gateway tools, so no audit-event buffer."""

    _gateway_tools = None


# --- rev 8: every JSON-RPC-shaped frame the stdio parser rejects ---------------


def _rejected_frames(s: str) -> list[tuple[str, bytes]]:
    """JSON-RPC-shaped stdio lines the real parser rejects, derived from the
    grammar rather than from findings (round-7 codex F028-F034 are two of
    them): every truncation point and every structural-character deletion of
    a frame carrying the sentinel, a doubled separator at every structural
    position, and both parser limits (nesting depth, an integer's digits),
    as an object and as a batch, bare and after leading whitespace."""
    frame = json.dumps(
        {"jsonrpc": "2.0", "id": 7, "result": {"k": s, "list": [1, s], "o": {"s": s}}}
    )
    variants: list[tuple[str, str]] = []
    for cut in range(1, len(frame)):
        variants.append((f"truncated@{cut}", frame[:cut]))
    for i, char in enumerate(frame):
        if char in '{}[]:,"':
            variants.append((f"deleted {char}@{i}", frame[:i] + frame[i + 1 :]))
        if char in ":,":
            variants.append((f"doubled {char}@{i}", frame[:i] + char + frame[i:]))
    variants.append(
        ("depth", frame[:-1] + ',"d":' + "[" * 1500 + "0" + "]" * 1500 + "}")
    )
    variants.append(("digits", frame[:-1] + ',"n":' + "7" * 5000 + "}"))
    # Codex's shapes without an object around them, and PR 321's bare array:
    variants.append(("bare-depth", "[" * 1500 + json.dumps(s) + "]" * 1500))
    variants.append(("bare-digits", "7" * 5000 + " " + json.dumps(s)))
    variants.append(("scalar-then-data", "42 " + json.dumps(s)))
    out: list[tuple[str, bytes]] = []
    for label, text in variants:
        for prefix, suffix, shape in (
            ("", "", "object"),
            ("  ", "", "indented"),
            ("[", "]", "batch"),
            ("\ufeff", "", "bom"),
        ):
            line = prefix + text + suffix
            try:
                json.loads(line)
            except Exception:  # noqa: BLE001 -- any rejection counts
                out.append((f"{shape} {label}", line.encode()))
    return out


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_no_rejected_json_rpc_frame_reaches_the_log(
    caplog: pytest.LogCaptureFixture, family: str
) -> None:
    """A JSON-RPC-shaped stdio line that the parser rejects -- on syntax or a
    limit -- is described, never logged raw; no record is exempted. A line
    that is not JSON-RPC-shaped is still the downstream's own output, logged
    as it is (the positive control for the boundary)."""
    import time
    from unittest.mock import MagicMock

    from pmcp.client.manager import ClientManager, ManagedClient
    from pmcp.types import ServerStatus, ServerStatusEnum

    caplog.set_level(logging.DEBUG)
    s = _FAMILIES[family][1]
    forbidden = _forbidden(s)
    manager = ClientManager()
    managed = ManagedClient(
        config=MagicMock(),
        process=MagicMock(),
        status=ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0),
    )
    frames = _rejected_frames(s)
    # No vacuous pass: the generator covers each class, and both limits.
    labels = " ".join(label for label, _ in frames)
    for kind in (
        "truncated",
        "deleted",
        "doubled",
        "depth",
        "digits",
        "batch",
        "indented",
        "bom",
        "bare-depth",
        "bare-digits",
        "scalar-then-data",
    ):
        assert kind in labels, kind
    described = 0
    for label, line in frames:
        start = len(caplog.records)
        manager._handle_stdout_line("srv", managed, line, time.time())
        records = caplog.records[start:]
        text = "\n".join(_record_text(r) for r in records)
        assert not any(form in text for form in forbidden), (label, text[:300])
        described += any(
            "sent a JSON-RPC frame that could not be parsed" in r.getMessage()
            for r in records
        )
    # The frames carry the sentinel as JSON spells it (`\\u` escapes for the
    # non-ASCII family), which is what a raw log line would show.
    spelled = json.dumps(s)[1:-1].encode("ascii")
    carrying = [label for label, line in frames if spelled in line]
    assert described == len(frames) and len(carrying) > 100, (described, len(frames))
    # The boundary, both ways. Output -- the parser rejects the first
    # character, and it opens nothing -- is logged as it is, by design; a
    # line that opens like JSON is described even if it is a log line.
    managed.stdio_frame_broken = False  # a fresh stream (rev 10)
    start = len(caplog.records)
    manager._handle_stdout_line(
        "srv", managed, f"server ready {s}".encode(), time.time()
    )
    assert any(
        r.getMessage() == f"[srv] Non-JSON output: server ready {s}"
        for r in caplog.records[start:]
    )
    for described_line in (f"[INFO] {s}", f"true {s}"):
        start = len(caplog.records)
        manager._handle_stdout_line(
            "srv", managed, described_line.encode(), time.time()
        )
        text = "\n".join(_record_text(r) for r in caplog.records[start:])
        assert not any(form in text for form in forbidden), (described_line, text)


# --- rev 9: where JSON starts, and what a shown line may hold -----------------

#: Lead characters a line can start with before its first significant one:
#: plain and Unicode whitespace (all of which `.strip` removes), control and
#: format characters, and the byte-order mark.
_LEADS = (
    "",
    " ",
    "   ",
    "\t",
    "\r",
    "\x0c",
    "\x0b",
    " ",
    " ",
    "　",
    "​",
    "﻿",
    "\x00",
    "\x1b",
    " ﻿\t",
)
#: Prefixes a downstream might write before a whole frame on the same line
#: (round-8 claude N1), each a different class of first character.
_FRAME_PREFIXES = (
    ",",
    "}",
    ":",
    "+",
    "ready",
    "ready ",
    "ready\r",
    "server ready: ",
    "\x1b[0m",
    "[INFO] ",
    "true ",
    "42 ",
)


def _line_cases(s: str) -> list[tuple[str, bytes]]:
    """Lines a stdio downstream could write, each carrying `s` only as JSON
    content (a string value, after an opener, or where a value begins):
    - every value-start character followed by the sentinel, and an
      unterminated string (round-8 codex P1), behind every lead;
    - a number or literal prefix followed by the sentinel;
    - every prefix class followed by a whole frame, behind every lead;
    - the whole frame in UTF-8 with a BOM, UTF-16 and UTF-32 (with and
      without a BOM, both byte orders), a non-UTF-8 byte before it;
    - CR-only separation and several frames on one line."""
    spelled = json.dumps(s)[1:-1]
    frame = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"k": s}})
    out: list[tuple[str, bytes]] = []
    for lead in _LEADS:
        tag = repr(lead)
        for char in _GRAMMAR_VALUE_START:
            out.append(
                (f"{tag} value-start {char!r}", (lead + char + spelled).encode())
            )
        out.append((f"{tag} unterminated string", (lead + '"' + spelled).encode()))
        out.append((f"{tag} unterminated key", (lead + '{"' + spelled).encode()))
        for literal in (
            "42 ",
            "-1 ",
            "0.5",
            "true ",
            "false",
            "null ",
            "NaN ",
            "Infinity ",
        ):
            out.append(
                (f"{tag} {literal!r} then data", (lead + literal + spelled).encode())
            )
        for prefix in _FRAME_PREFIXES:
            out.append((f"{tag} {prefix!r} + frame", (lead + prefix + frame).encode()))
    out.append(("\\xff + frame", b"\xff" + frame.encode()))
    out.append(("\\xfe\\xff + frame", b"\xfe\xff" + frame.encode()))
    for codec in (
        "utf-8-sig",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "utf-32",
        "utf-32-le",
        "utf-32-be",
    ):
        out.append((f"frame in {codec}", frame.encode(codec)))
    out.append(("CR-only", ("ready\r" + frame + "\r" + frame).encode()))
    out.append(("two frames", (frame + frame).encode()))
    out.append(("two frames, spaced", (frame + " " + frame).encode()))
    out.append(("frame + trailing banner", (frame + " done").encode()))
    return out


def _stdio_manager() -> tuple[Any, Any]:
    from unittest.mock import MagicMock

    from pmcp.client.manager import ClientManager, ManagedClient
    from pmcp.types import ServerStatus, ServerStatusEnum

    return ClientManager(), ManagedClient(
        config=MagicMock(),
        process=MagicMock(),
        status=ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0),
    )


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_no_stdio_line_shows_frame_content(
    caplog: pytest.LogCaptureFixture, family: str
) -> None:
    """Rev 9 (round-8 codex P1 and claude N1): no line a downstream writes
    shows frame content in the log. The expectation is the grammar's, not
    the reader's: a line whose first significant character can begin a
    JSON value is described; any other line is shown only up to its first
    frame-opening character. No record is exempt from the leak check."""
    import time

    caplog.set_level(logging.DEBUG)
    s = _FAMILIES[family][1]
    forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
    manager, managed = _stdio_manager()
    cases = _line_cases(s)
    shaped = 0
    for label, line in cases:
        decoded = line.decode("utf-8", "replace")
        # Each case is a stream of its own (rev 10: a rejected frame makes
        # the reader describe the lines after it until one parses).
        managed.stdio_frame_broken = False
        start = len(caplog.records)
        manager._handle_stdout_line("srv", managed, line, time.time())
        records = caplog.records[start:]
        text = "\n".join(_record_text(r) for r in records)
        # No record exempt: the sentinel, and its NUL-interleaved UTF-16/32
        # spelling, appear nowhere.
        flat = text.replace("\x00", "")
        assert not any(form in text or form in flat for form in forbidden), (
            label,
            text[:300],
        )
        shown = [
            r.getMessage()[len("[srv] Non-JSON output: ") :]
            for r in records
            if r.getMessage().startswith("[srv] Non-JSON output: ")
        ]
        described = any(
            "sent a JSON-RPC frame that could not be parsed" in r.getMessage()
            for r in records
        )
        try:
            json.loads(line)
            parsed = True
        except Exception:  # noqa: BLE001 -- any rejection
            parsed = False
        if parsed:
            continue  # valid JSON: the dispatcher's business, not this test's
        if _json_shaped(decoded):
            shaped += 1
            assert described and not shown, (label, [r.getMessage() for r in records])
        else:
            assert shown and not described, (label, [r.getMessage() for r in records])
            assert all(_carries_no_frame(text) for text in shown), (label, shown)
    # No vacuous pass: both sides of the boundary are exercised.
    assert shaped > 100 and len(cases) - shaped > 20, (shaped, len(cases))


def test_a_banner_stays_useful_on_stdio(caplog: pytest.LogCaptureFixture) -> None:
    """The operator-facing controls: plain output is shown as it is, digits
    included; a BOM or a control character before it is dropped; a banner
    before a frame keeps its own text."""
    import time

    caplog.set_level(logging.DEBUG)
    manager, managed = _stdio_manager()
    frame = json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"k": "frame-secret-9x"}})
    for line, shown in (
        ("server ready banner-ok-7f3a", "server ready banner-ok-7f3a"),
        ("listening on port 8080", "listening on port 8080"),
        ("﻿ready banner-ok-7f3a", "ready banner-ok-7f3a"),
        ("\x00  ready", "ready"),
        ("ready banner-ok-7f3a " + frame, "ready banner-ok-7f3a (rest omitted)"),
        ("warning: value is 'quoted'", "warning: value is 'quoted'"),
        ('warning: value is "quoted"', "warning: value is (rest omitted)"),
    ):
        start = len(caplog.records)
        manager._handle_stdout_line("srv", managed, line.encode(), time.time())
        messages = [r.getMessage() for r in caplog.records[start:]]
        assert messages == [f"[srv] Non-JSON output: {shown}"], (line, messages)


# --- rev 10: the SDK's traffic logging, outgoing --------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ("hex", "alpha", "unicode"))
@pytest.mark.parametrize("transport", _TRANSPORTS)
async def test_no_caller_argument_reaches_the_log_through_a_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
    transport: str,
    family: str,
) -> None:
    """Round-9 codex P1's other direction (and round-8 claude N2): with DEBUG
    on, the SDK logs each message it SENDS, and a `tools/call` request
    carries the caller's arguments. Over every real transport, a caller
    sentinel in `gateway.invoke`'s arguments reaches the downstream and is
    in no log record, stream or warning -- none exempt."""
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr("pmcp.client.manager.RETRY_DELAYS", [0.0, 0.0, 0.0])
    s = _FAMILIES[family][1]
    downstream = _Downstream()
    downstream.state["sse"] = transport == "http-sse"
    state = {**downstream.state, "method": None, "shape": None, "s": ""}
    (tmp_path / "state.json").write_text(json.dumps(state))
    server, _ = _server(tmp_path, audited=False)
    policy = server._policy_manager
    policy.is_server_allowed = lambda name: True  # type: ignore[method-assign]
    policy.is_tool_allowed = lambda tool_id: True  # type: ignore[method-assign]
    tap = _Tap(server, None, caplog, capfd, recwarn)
    manager = server._client_manager
    try:
        await manager.connect_server(_config(transport, downstream, tmp_path))
        mark = tap.start()
        answer = await asyncio.wait_for(
            _call(
                server,
                "gateway.invoke",
                {
                    "tool_id": "frames::run",
                    "arguments": {"q": s, "nested": {"k": [s]}},
                    "options": {"timeout_ms": 3000},
                },
            ),
            20,
        )
        observed = tap.since(mark, "")
        records = caplog.records[mark[0] :]
        # No record exempt: the raw log, every record's full text.
        text = "\n".join(_record_text(r) for r in records)
        forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
        assert not any(form in text for form in forbidden), (transport, text[:600])
        assert observed.leaks(s) == [], (transport, observed)
        # No vacuous pass: the call reached the downstream and answered.
        seen = set(downstream.seen)
        if transport == "stdio":
            seen |= set((tmp_path / "state.json.seen").read_text().splitlines())
        assert "tools/call" in seen, seen
        assert "ok" in "".join(getattr(b, "text", "") for b in answer.content)
        if transport != "stdio":
            # The SDK did log the outgoing request -- as its structure.
            assert any(
                "<JSON-RPC request: method 'tools/call'" in r.getMessage()
                for r in records
            ), [r.getMessage() for r in records][:20]
    finally:
        await manager.disconnect_server("frames", force=True)
        await server.shutdown()


# --- rev 10: a frame broken across lines (round-9 claude N1-r9) ---------------


def _broken_frame_streams(s: str) -> list[tuple[str, list[bytes]]]:
    """Streams of stdio lines in which a downstream broke a frame across
    lines, each followed by a well-formed frame and then a banner:
    - a raw newline inside a string, the tail line plain text, and again
      with the tail starting with a closer;
    - a caller's value echoed after the break;
    - a UTF-16 frame whose character holds byte 0x0A (U+010A in LE, U+0A00
      in BE), split by the line reader."""
    good = json.dumps({"jsonrpc": "2.0", "method": "notifications/message"})
    banner = "server ready banner-ok-7f3a"
    tails = [
        f'{{"jsonrpc":"2.0","id":7,"result":{{"text":"Config loaded\nAPI_KEY={s}\nDB={s}"}}}}',
        f'{{"jsonrpc":"2.0","id":7,"result":{{"text":"you sent: abc\n{s}"}}}}',
        f'{{"jsonrpc":"2.0","id":7,"result":{{"a":[1,\n], {s}]}}}}',
    ]
    streams = []
    for index, text in enumerate(tails):
        lines = [part.encode() for part in text.split("\n")]
        streams.append(
            (f"raw newline #{index}", lines + [good.encode(), banner.encode()])
        )
    for codec, char in (("utf-16-le", "Ċ"), ("utf-16-be", "਀")):
        data = json.dumps({"k": f"{char} {s}"}, ensure_ascii=False).encode(codec)
        lines = data.split(b"\n")
        assert len(lines) > 1, codec
        streams.append(
            (f"{codec} 0x0A split", lines + [good.encode(), banner.encode()])
        )
    return streams


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_a_frame_broken_across_lines_shows_none_of_it(
    caplog: pytest.LogCaptureFixture, family: str
) -> None:
    """Once a stdio line is described as a rejected frame, every following
    unparseable line is described too, until a line parses: the rest of a
    broken frame is frame data, not output. A banner after a well-formed
    line is shown again."""
    import time

    caplog.set_level(logging.DEBUG)
    s = _FAMILIES[family][1]
    forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
    for label, lines in _broken_frame_streams(s):
        manager, managed = _stdio_manager()
        start = len(caplog.records)
        for line in lines:
            manager._handle_stdout_line("srv", managed, line, time.time())
        records = caplog.records[start:]
        text = "\n".join(_record_text(r) for r in records)
        flat = text.replace("\x00", "")
        assert not any(f in text or f in flat for f in forbidden), (label, text[:400])
        shown = [
            r.getMessage()
            for r in records
            if r.getMessage().startswith("[srv] Non-JSON output: ")
        ]
        # Only the banner after the well-formed frame is shown.
        assert shown == ["[srv] Non-JSON output: server ready banner-ok-7f3a"], (
            label,
            shown,
        )
