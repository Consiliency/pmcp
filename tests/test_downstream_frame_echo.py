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
    # rev 12 (round-11 codex P1): a pagination cursor pmcp rejects by hand,
    # on every listing kind -- an object under `nextCursor`, a list under
    # `next_cursor`, and a string cursor the downstream repeats.
    "cursor-object",
    "cursor-list",
    "cursor-repeat",
    # rev 13 (round-12 codex B2): an error envelope that breaks one rule of
    # JSON-RPC 2.0 elsewhere, with the sentinel as its `message` -- the text a
    # caller renders. `jsonrpc` "1.0", no `jsonrpc`, both `result` and
    # `error`, and a bool `code` (which the SDK's model coerces to 1). The
    # property test below takes every rule; these four take every caller and
    # transport.
    "envelope-version",
    "envelope-unversioned",
    "envelope-both",
    "envelope-code",
)
#: The shapes whose frame pmcp rejects -- the parser, or (rev 13) the
#: envelope check, which leaves the request waiting: stdio then also sends the
#: real reply, so the request completes instead of timing out.
_REJECTED_SHAPES = (
    # rev 13: the envelope check drops these on stdio too (rev 12's
    # dispatcher acted on them), so they are followed by the real reply.
    "result-type",
    "error-code",
    "error-message",
    "jsonrpc",
    "not-json",
    "json-deep",
    "json-bigint",
    "json-syntax",
    "envelope-version",
    "envelope-unversioned",
    "envelope-both",
    "envelope-code",
)
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
        elif state["shape"].startswith("cursor-") and method in ("tools/list", "resources/list", "prompts/list"):
            result = normal(method, request.get("params") or {})
            if state["shape"] == "cursor-object":
                result["nextCursor"] = {"token": s}
            elif state["shape"] == "cursor-list":
                result["next_cursor"] = [s]
            else:
                result["nextCursor"] = s
            frame = {"jsonrpc": "2.0", "id": rid, "result": result}
        elif state["shape"].startswith("cursor-"):
            frame = {"jsonrpc": "2.0", "id": rid, "result": normal(method, request.get("params") or {})}
        elif state["shape"] == "payload":
            frame = {"jsonrpc": "2.0", "id": rid, "result": invalid_payload(method, s, request.get("params") or {})}
        elif state["shape"].startswith("envelope-"):
            error = {"code": -32000, "message": s}
            frame = {"jsonrpc": "2.0", "id": rid, "error": error}
            if state["shape"] == "envelope-version":
                frame["jsonrpc"] = "1.0"
            elif state["shape"] == "envelope-unversioned":
                del frame["jsonrpc"]
            elif state["shape"] == "envelope-both":
                frame["result"] = {}
            else:
                error["code"] = True
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


def _wire_error(error: BaseException) -> str:
    """What the SDK's dispatcher sends the caller for a handler's escaping
    exception (`handler_exception_to_error_data`, else `code=0, str(e)`),
    not `str(error)`: a bare `ValidationError` goes out as `-32602` with no
    text (rev 11 correction)."""
    from mcp.shared.jsonrpc_dispatcher import handler_exception_to_error_data

    data = handler_exception_to_error_data(error)
    if data is None:
        return f"raised code 0: {error}"
    return f"raised code {data.code}: {data.message} {data.data!r}"


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
        except Exception as error:  # noqa: BLE001 -- what the SDK sends
            return _wire_error(error)
    elif method == "prompts/get":
        entry = server._server.get_request_handler("prompts/get")
        try:
            result = await entry.handler(
                _make_ctx(), GetPromptRequestParams(name="frames::p")
            )
        except Exception as error:  # noqa: BLE001 -- what the SDK sends
            return _wire_error(error)
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
                                    and not _CLOSE_TIMEOUT.fullmatch(r.getMessage())
                                )
                            ),
                            observed.streams,
                            observed.warnings,
                        )
                        records = caplog.records[mark[0] :]
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
                        if transport == "stdio" and shape.startswith("envelope-"):
                            # No vacuous pass: the envelope reached the
                            # dispatcher and was dropped (rev 13).
                            assert any(
                                "dropped invalid frame" in r.getMessage()
                                for r in records
                            ), (transport, method, shape, family)
                        if transport == "stdio" and shape.startswith("json-"):
                            # No vacuous pass: the rejected frame reached the
                            # reader and got the fixed record (rev 12).
                            assert any(
                                "non-protocol stdout line" in r.getMessage()
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


# --- stdout line generators (revs 8-11), feeding rev 12's property ----------

#: Every character that can begin a JSON value, plus Python's NaN/Infinity:
#: generator input only (rev 12 has no classifier to oracle).
_VALUE_STARTS = '{["-0123456789tfnNI'

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
        for char in _VALUE_STARTS:
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
        f'ready {{"jsonrpc":"2.0","id":7,"result":{{"text":"abc\n{s}"}}}}',
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


# --- rev 11: describe mode ends only on a valid frame; oversized heads -------


async def _read_stream(
    manager: Any, managed: Any, data: bytes, monkeypatch: Any, limit: int | None = None
) -> None:
    """Feed `data` to the REAL `_read_stdout` loop, then EOF."""
    from pmcp.types import ServerStatusEnum

    if limit is not None:
        monkeypatch.setattr("pmcp.client.manager._stdio_read_limit", lambda: limit)
        # Chunks smaller than the limit, so a long line spans several reads
        # and meets the limit before its newline, as a 10 MiB line does.
        monkeypatch.setattr("pmcp.client.manager._STDIO_CHUNK_SIZE", limit // 2)
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    managed.process.stdout = reader
    managed.config = None  # no reconnect at EOF
    managed.status.status = ServerStatusEnum.OFFLINE
    await manager._read_stdout("srv", managed)


#: Lines that parse but are not a frame the dispatcher accepts: each kind of
#: JSON value, and objects that fail its guards.
_NOT_FRAMES = (
    "42",
    "-1",
    "0.5",
    "true",
    "false",
    "null",
    "[]",
    "[1, 2]",
    "{}",
    '"ok"',
    '{"jsonrpc": "2.0"}',
    '{"id": 7, "result": {}}',
    '{"jsonrpc": "1.0", "id": 7, "result": {}}',
    '{"jsonrpc": "2.0", "id": true, "result": {}}',
    '{"jsonrpc": "2.0", "id": 1.5, "result": {}}',
    '{"jsonrpc": "2.0", "method": 5}',
    '{"jsonrpc": "2.0", "id": 7}',
    '{"jsonrpc": "2.0", "id": 7, "result": {}, "error": {"code": 1, "message": "m"}}',
)


# --- rev 11: the handler wrapper keeps the SDK's wire codes (claude N2-r10) ---


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ("hex", "alpha", "unicode"))
async def test_a_wrapped_handler_keeps_the_wire_code(family: str) -> None:
    """`_described_errors` changes only the text the SDK would send, never
    the code: a bare `ValidationError` stays `-32602` (now with a
    structural message), an `MCPError` keeps its own code, any other
    exception stays the SDK's `code=0`. No value reaches the message or
    `data`; an unrelated exception passes unchanged."""
    from mcp.shared.exceptions import MCPError
    from mcp.shared.jsonrpc_dispatcher import handler_exception_to_error_data
    from mcp.types import INVALID_PARAMS
    from pydantic import ValidationError

    from pmcp.server import _described_errors
    from pmcp.types import McpTaskInfo

    s = _FAMILIES[family][1]
    forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])

    def invalid() -> ValidationError:
        try:
            McpTaskInfo.model_validate({"task_id": {"v": s}})
        except ValidationError as error:
            return error
        raise AssertionError("did not reject")

    async def bare() -> None:
        raise invalid()

    async def mcp_inside_except() -> None:
        try:
            raise invalid()
        except ValidationError:
            raise MCPError(-32002, "Resource not found") from None

    async def mcp_from_error() -> None:
        error = invalid()
        raise MCPError(-32602, str(error)) from error

    async def mcp_data_kept() -> None:
        try:
            raise invalid()
        except ValidationError:
            raise MCPError(-32002, "Resource not found", {"uri": "x://r"}) from None

    async def mcp_data_carries() -> None:
        error = invalid()
        # The value alone, though the rejected input was `{"v": <s>}`: a
        # string inside a container input is matched too (rev 13).
        raise MCPError(-32602, "bad input", {"input": s}) from error

    async def mcp_partial_message() -> None:
        error = invalid()
        raise MCPError(-32602, f"bad: {error.errors()[0]['input']}") from error

    async def wrapped() -> None:
        error = invalid()
        raise ValueError(f"could not build the result: {error}") from error

    async def unrelated() -> None:
        raise RuntimeError("plain failure")

    async def wire(handler: Any) -> tuple[int, str, Any]:  # (code, message, data)
        try:
            await _described_errors(handler)()
        except Exception as error:  # noqa: BLE001 -- inspected
            data = handler_exception_to_error_data(error)
            if data is None:
                return 0, str(error), None
            return data.code, data.message, data.data
        raise AssertionError("did not raise")

    cases = {
        "bare": (INVALID_PARAMS, "validation error"),
        "mcp_inside_except": (-32002, "Resource not found"),
        "mcp_from_error": (-32602, "validation error"),
        "wrapped": (0, "validation error"),
        "mcp_data_kept": (-32002, "Resource not found"),
        "mcp_data_carries": (-32602, "bad input"),
        "mcp_partial_message": (-32602, ""),
    }
    for name, handler in (
        ("bare", bare),
        ("mcp_inside_except", mcp_inside_except),
        ("mcp_from_error", mcp_from_error),
        ("wrapped", wrapped),
        ("mcp_data_kept", mcp_data_kept),
        ("mcp_data_carries", mcp_data_carries),
        ("mcp_partial_message", mcp_partial_message),
    ):
        code, message, data = await wire(handler)
        expected_code, expected_text = cases[name]
        assert code == expected_code, (name, code, message)
        assert expected_text in message, (name, message)
        assert not any(f in f"{message}{data!r}" for f in forbidden), (name, message)
        if name == "mcp_data_kept":
            # rev 12: `data` that carries nothing rejected is kept.
            assert data == {"uri": "x://r"}, data
        if name == "mcp_data_carries":
            assert data is None, data
    assert await wire(unrelated) == (0, "plain failure", None)


# --- rev 12: no non-protocol stdout line is shown, whatever it holds ----------


def _is_valid_frame(line: bytes) -> bool:
    """The test's own statement of a JSON-RPC 2.0 message as MCP defines one
    -- the only kind of stdout line whose content the reader may act on
    (rev 13: the specification's rules, written here independently of
    `jsonrpc_envelope_problem`). `"jsonrpc": "2.0"`; a request or
    notification has a string `method`, an object `params` if any (`null`
    reads as absent, rev 14), and no
    `result`/`error`; a response has an `id` and exactly one of an object
    `result` or an `error` object with an integer `code` and a string
    `message`."""
    try:
        value = json.loads(line)
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        return False

    def request_id(v: Any) -> bool:
        return type(v) in (str, int)

    if "method" in value:
        return (
            isinstance(value["method"], str)
            and ("id" not in value or request_id(value["id"]))
            and isinstance(value.get("params") or {}, dict)
            and "result" not in value
            and "error" not in value
        )
    if "id" not in value or ("result" in value) == ("error" in value):
        return False
    if "result" in value:
        return request_id(value["id"]) and isinstance(value["result"], dict)
    error = value["error"]
    return (
        (value["id"] is None or request_id(value["id"]))
        and isinstance(error, dict)
        and type(error.get("code")) is int
        and isinstance(error.get("message"), str)
    )


_MISSING = object()


def _envelope_violations(s: str, rid: Any) -> list[tuple[str, dict[str, Any]]]:
    """Rev 13 (round-12 codex B2): one frame per way a JSON-RPC 2.0 response
    can break the specification's envelope rules (JSON-RPC 2.0 sections 4-5,
    MCP's response types), each addressed to pending request `rid` and each
    carrying the sentinel where a caller would render it -- the error's
    `message` and `data`, or the result. Derived from the rules, not from the
    four frames codex sent."""
    err = {"code": -32000, "message": s, "data": {"detail": s}}
    base: dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "error": err}

    def with_member(frame: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
        out = dict(frame)
        if value is _MISSING:
            out.pop(key, None)
        else:
            out[key] = value
        return out

    cases: list[tuple[str, dict[str, Any]]] = []
    # `jsonrpc` MUST be exactly "2.0".
    for value in (_MISSING, "1.0", "2", "2.0 ", 2.0, 2, None, ["2.0"], {"v": "2.0"}):
        cases.append(
            (f"jsonrpc {type(value).__name__}", with_member(base, "jsonrpc", value))
        )
    # Exactly one of `result` and `error`.
    cases.append(("result and error", {**base, "result": {}}))
    cases.append(
        (
            "result and error, result first",
            {"jsonrpc": "2.0", "id": rid, "result": {"x": s}, "error": err},
        )
    )
    cases.append(
        ("neither result nor error", {"jsonrpc": "2.0", "id": rid, "message": s})
    )
    # `error.code` MUST be an integer.
    for code in (
        _MISSING,
        True,
        False,
        1.5,
        -32000.0,
        "-32000",
        None,
        {},
        [],
        {"c": s},
        [s],
    ):
        cases.append(
            (
                f"code {type(code).__name__}",
                {**base, "error": with_member(err, "code", code)},
            )
        )
    # `error.message` MUST be a string (the sentinel then rides in `data`).
    for message in (_MISSING, 5, None, True, [s], {s: s}):
        cases.append(
            (
                f"message {type(message).__name__}",
                {**base, "error": with_member(err, "message", message)},
            )
        )
    # `error` MUST be an object.
    for error in (s, [s], [err], 5, None, True):
        cases.append((f"error {type(error).__name__}", {**base, "error": error}))
    # A response's `id` is a string, an integer, or null (an error only):
    # not a bool, a float, an array or an object -- and it MUST be present.
    if isinstance(rid, int):
        for bad_id in (float(rid), [rid], {"id": rid}):
            cases.append((f"id {type(bad_id).__name__}", {**base, "id": bad_id}))
        cases.append(("id bool", {**base, "id": rid == 1}))
    cases.append(("no id", with_member(base, "id", _MISSING)))
    # `result` MUST be an object (MCP's `Result`).
    for result in (s, [s], 5, None, True):
        cases.append(
            (
                f"result {type(result).__name__}",
                {"jsonrpc": "2.0", "id": rid, "result": result},
            )
        )
    cases.append(
        ("result with null id", {"jsonrpc": "2.0", "id": None, "result": {"x": s}})
    )
    # A request or notification carries neither `result` nor `error`, and
    # its `params` is an object.
    cases.append(("request with error", {**base, "method": "x"}))
    cases.append(
        ("notification with error", {"jsonrpc": "2.0", "method": "x", "error": err})
    )
    cases.append(
        (
            "request with array params",
            {"jsonrpc": "2.0", "id": rid, "method": "x", "params": [s]},
        )
    )
    return cases


def _stdout_streams(s: str) -> list[tuple[str, list[bytes], int | None]]:
    """Every stdout stream the earlier rounds' generators produce, as
    (label, lines, line limit): grammar-derived rejected frames (rev 8),
    value-start and prefix lines behind every lead (rev 9), frames broken
    across lines (rev 10), a non-frame or a valid frame between a broken
    head and its continuation (rounds 10 and 11), an oversized head, and
    plain banners in each common shape."""
    head = b'{"jsonrpc":"2.0","id":7,"result":{"text":"start'
    tail = f'API_KEY={s} "}}}}'.encode()
    reply = json.dumps({"jsonrpc": "2.0", "id": 8, "result": {}}).encode()
    note = json.dumps({"jsonrpc": "2.0", "method": "notifications/message"}).encode()
    streams: list[tuple[str, list[bytes], int | None]] = []
    streams += [(label, [line], None) for label, line in _rejected_frames(s)]
    streams += [(label, [line], None) for label, line in _line_cases(s)]
    streams += [(label, lines, None) for label, lines in _broken_frame_streams(s)]
    streams += [(f"middle {m}", [head, m.encode(), tail], None) for m in _NOT_FRAMES]
    streams.append(("round-11 interleaved response", [head, reply, tail], None))
    streams.append(("interleaved notification", [head, note, tail], None))
    streams.append(("oversized head", [head + b"A" * 5000, tail, note], 2048))
    # Rev 13: malformed envelopes addressed to the pending request (id 8).
    streams += [
        (f"envelope: {label}", [json.dumps(frame).encode()], None)
        for label, frame in _envelope_violations(s, 8)
    ]
    for banner in (
        f"server ready {s}",
        f"INFO: {s}",
        f"NOTICE {s}",
        f"2026-10-01 12:00 ready {s}",
        f"token={s}",
        f"\x1b[32mready {s}\x1b[0m",
        f"warning: value is '{s}'",
    ):
        streams.append((f"banner {banner[:12]!r}", [banner.encode()], None))
    return streams


def _windows(text: str, size: int = 12) -> set[str]:
    return {text[i : i + size] for i in range(max(0, len(text) - size + 1))}


@pytest.mark.asyncio
@pytest.mark.parametrize("family", sorted(_FAMILIES))
async def test_no_record_shows_a_non_protocol_stdout_line(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, family: str
) -> None:
    """Rev 12 (round-11 codex P1, the class claude proposed): for any bytes a
    downstream writes to stdout that are not a valid JSON-RPC message, no log
    record carries any part of them -- through the real `_read_stdout`, with
    no record exempt. Each unparseable line gets the one fixed record."""
    import time

    from pmcp.client.manager import PendingRequest

    caplog.set_level(logging.DEBUG)
    s = _FAMILIES[family][1]
    forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
    streams = _stdout_streams(s)
    fixed = 0
    for label, lines, limit in streams:
        manager, managed = _stdio_manager()
        future = asyncio.get_running_loop().create_future()
        managed.pending_requests[8] = PendingRequest(
            request_id=8,
            server_name="srv",
            tool_id="",
            started_at=time.time(),
            last_heartbeat=time.time(),
            timeout_ms=1000,
            future=future,
        )
        start = len(caplog.records)
        await _read_stream(
            manager, managed, b"\n".join(lines) + b"\n", monkeypatch, limit=limit
        )
        records = caplog.records[start:]
        text = "\n".join(_record_text(r) for r in records)
        flat = text.replace("\x00", "")
        assert not any(f in text or f in flat for f in forbidden), (label, text[:400])
        record_windows = _windows(text) | _windows(flat)
        for line in lines:
            if _is_valid_frame(line):
                continue
            decoded = line.decode("utf-8", "replace")
            shown = _windows(decoded) & record_windows
            assert not shown, (label, sorted(shown)[:3])
        messages = [r.getMessage() for r in records]
        assert not any("Non-JSON output" in m for m in messages), (label, messages)
        if label.startswith("envelope"):
            # Rev 13: a malformed envelope never settles the request it names;
            # only the end of the stream does (a disconnect, not its error).
            from pmcp.client.manager import DownstreamError

            settled = future.exception() if future.done() else None
            assert not (future.done() and settled is None), label
            assert not isinstance(settled, DownstreamError), (label, settled)
        fixed += sum("non-protocol stdout line" in m for m in messages)
    # No vacuous pass: thousands of lines took the fixed record.
    assert fixed > 1000, fixed


def test_a_banner_line_gets_the_fixed_record(caplog: pytest.LogCaptureFixture) -> None:
    """The operator-visibility control, rev 12: a non-conforming server's
    stdout banner is no longer shown; it gets the fixed record (its log
    belongs on stderr, which is unchanged)."""
    import time

    caplog.set_level(logging.DEBUG)
    manager, managed = _stdio_manager()
    manager._handle_stdout_line(
        "srv", managed, b"server ready on port 8080", time.time()
    )
    assert [r.getMessage() for r in caplog.records] == [
        "[srv] non-protocol stdout line: could not parse JSON downstream stdio "
        "frame at line 1, column 1 (JSONDecodeError)"
    ]


# --- rev 13: a malformed envelope, through the caller waiting on it ------------


class _Stdin:
    """The stdio pipe pmcp writes requests to: each request line is handed
    to `respond`, which feeds the downstream's answer to the reader."""

    def __init__(self, respond: Any) -> None:
        self.respond = respond

    def write(self, data: bytes) -> None:
        for line in data.splitlines():
            self.respond(json.loads(line))

    async def drain(self) -> None:
        return None


_CONSUMERS = ("tools/list page 2", "tools/call", "tasks/get")


async def _through_a_caller(consumer: str, malformed: bytes) -> str:
    """Run `consumer` -- the code that waits on a request and renders what it
    gets -- against the REAL `_read_stdout`. The targeted request is answered
    with `malformed`, then with a valid reply; return what the caller got
    (its value, or its exception's text)."""
    from pmcp.types import ServerStatusEnum, ToolInfo

    manager, managed = _stdio_manager()
    managed.config.name = "srv"
    managed.status.server_capabilities = {"tasks": {}}
    manager._clients["srv"] = managed
    manager._tools["srv::run"] = ToolInfo(
        tool_id="srv::run",
        server_name="srv",
        tool_name="run",
        description="d",
        short_description="d",
        input_schema={"type": "object"},
        tags=[],
        risk_hint="low",
    )
    reader = asyncio.StreamReader()
    managed.process.stdout = reader
    method = consumer.split()[0]

    def respond(request: dict[str, Any]) -> None:
        rid, params = request["id"], request.get("params") or {}
        if method == "tools/list" and not params.get("cursor"):
            result: dict[str, Any] = {
                "tools": [{"name": "run", "inputSchema": {"type": "object"}}],
                "nextCursor": "page-2",
            }
            reader.feed_data(
                json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}).encode()
                + b"\n"
            )
            return
        valid = {
            "tools/list": {"tools": []},
            "tools/call": {"content": [{"type": "text", "text": "ok"}]},
            "tasks/get": {"task": {"taskId": "t", "status": "working"}},
        }[method]
        line = malformed.replace(b"@@ID@@", json.dumps(rid).encode())
        reply = json.dumps({"jsonrpc": "2.0", "id": rid, "result": valid}).encode()
        reader.feed_data(line + b"\n" + reply + b"\n")

    managed.process.stdin = _Stdin(respond)
    read_task = asyncio.create_task(manager._read_stdout("srv", managed))
    try:
        if method == "tools/list":
            outcome: Any = await manager._fetch_listing_pages(managed, "tools")
        elif method == "tools/call":
            outcome = await manager.call_tool("srv::run", {}, timeout_ms=5000)
        else:
            outcome = await manager.get_task("srv", "t")
        text = repr(outcome)
    except Exception as error:  # noqa: BLE001 -- what the caller would render
        text = f"{type(error).__name__}: {error}"
    finally:
        managed.config = None  # no reconnect at EOF
        managed.status.status = ServerStatusEnum.OFFLINE
        reader.feed_eof()
        await read_task
    return text


@pytest.mark.asyncio
@pytest.mark.parametrize("family", sorted(_FAMILIES))
@pytest.mark.parametrize("consumer", _CONSUMERS)
async def test_no_malformed_envelope_reaches_its_waiting_caller(
    caplog: pytest.LogCaptureFixture, consumer: str, family: str
) -> None:
    """Round-12 codex B2: `{"jsonrpc": "1.0", "id": 8, "error": {...}}` --
    or no `jsonrpc`, both `result` and `error`, an object `code` -- resolved
    the waiting request, and the caller logged `tools/list page 2 failed
    (<message>)`. The rev 12 property test stopped before any caller ran.
    Here each envelope violation `_envelope_violations` derives goes to a
    real caller (listing pagination, `call_tool`, `get_task`) through the real
    reader: no record and nothing the caller returns or raises carries any
    part of it, and the caller gets the valid reply that follows."""
    caplog.set_level(logging.DEBUG)
    s = _FAMILIES[family][1]
    forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
    cases = _envelope_violations(s, "@@ID@@")
    assert len(cases) > 40, len(cases)
    for label, frame in cases:
        malformed = json.dumps(frame).encode().replace(b'"@@ID@@"', b"@@ID@@")
        start = len(caplog.records)
        outcome = await asyncio.wait_for(_through_a_caller(consumer, malformed), 20)
        records = "\n".join(_record_text(r) for r in caplog.records[start:])
        text = records + "\n" + outcome
        assert not any(f in text for f in forbidden), (consumer, label, text[:600])
        shown = _windows(malformed.decode()) & (_windows(text))
        assert not shown, (consumer, label, sorted(shown)[:3])
        # A part of the value: `describe_exception` elides a long message to
        # its two ends, which the whole-value forms above do not match.
        part = _windows(s, 8) & _windows(text, 8)
        assert not part, (consumer, label, sorted(part)[:3])
        # The valid reply answered the caller: the malformed frame did not.
        assert "Error" not in outcome.split(":")[0], (consumer, label, outcome)


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", _CONSUMERS)
async def test_a_valid_error_still_reaches_its_caller(consumer: str) -> None:
    """The control: a well-formed error envelope is the downstream's own
    message, shown by design -- so the test above would see a leak."""
    frame = {
        "jsonrpc": "2.0",
        "id": "@@ID@@",
        "error": {"code": -32000, "message": "downstream says no"},
    }
    malformed = json.dumps(frame).encode().replace(b'"@@ID@@"', b"@@ID@@")
    outcome = await asyncio.wait_for(_through_a_caller(consumer, malformed), 20)
    if consumer.startswith("tools/list"):
        # Page 2 failed: the whole kind is unreadable (None), and logged.
        assert outcome == "None", outcome
    else:
        assert "downstream says no" in outcome, outcome


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_the_sdk_client_transports_check_the_envelope(family: str) -> None:
    """Round-12 codex B2 on SSE and streamable HTTP: the SDK's models coerce
    an `error.code` of `true`, `"5"` or `5.0` and ignore a `result` beside an
    `error`, so those frames validated and their `message` reached pmcp. The
    SDK's three client transports now validate through the strict adapter
    (installed on `import pmcp`): every envelope violation is a
    `ValidationError` whose text carries none of the frame, and a valid
    frame validates as before."""
    import mcp.client.sse as sse_module
    import mcp.client.stdio as stdio_module
    import mcp.client.streamable_http as streamable_module
    from pydantic import ValidationError

    s = _FAMILIES[family][1]
    forbidden = _forbidden(s) | _forbidden(json.dumps(s)[1:-1])
    adapters = {
        "sse": sse_module.types.jsonrpc_message_adapter,
        "stdio": stdio_module.types.jsonrpc_message_adapter,
        "streamable_http": streamable_module.jsonrpc_message_adapter,
    }
    for transport, adapter in adapters.items():
        for label, frame in _envelope_violations(s, 8):
            raw = json.dumps(frame)
            with pytest.raises(ValidationError) as caught:
                adapter.validate_json(raw, by_name=False)
            text = str(caught.value)
            assert not any(f in text for f in forbidden), (transport, label, text)
            assert not _windows(raw) & _windows(text), (transport, label, text)
        for frame in (
            {"jsonrpc": "2.0", "id": 8, "result": {"x": s}},
            {"jsonrpc": "2.0", "id": 8, "error": {"code": -1, "message": s}},
            {"jsonrpc": "2.0", "id": None, "error": {"code": -1, "message": s}},
            {"jsonrpc": "2.0", "method": "notifications/message", "params": {}},
            {"jsonrpc": "2.0", "id": "a", "method": "ping"},
        ):
            assert adapter.validate_json(json.dumps(frame), by_name=False)


def test_the_strict_envelope_is_installed_on_import_pmcp() -> None:
    """`pmcp refresh` reaches downstreams through the SDK's `stdio_client`
    without importing the client manager: the adapter is installed with the
    record scrubber, by `import pmcp` alone."""
    import subprocess

    code = (
        "import pmcp, mcp.client.sse as a, mcp.client.stdio as b, "
        "mcp.client.streamable_http as c, mcp.server.sse as d; "
        "print(type(a.types).__name__, type(b.types).__name__, "
        "type(c.jsonrpc_message_adapter).__name__, type(d.types).__name__ "
        "if hasattr(d, 'types') else 'module')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.split()
    assert out[:3] == ["_ClientTypesView", "_ClientTypesView", "_StrictMessageAdapter"]
    # The server side is untouched.
    assert out[3] != "_ClientTypesView", out


# --- rev 14: the SDK session pmcp builds is bounded (round-13 claude N2) -------

_INIT_SCRIPT = textwrap.dedent(
    """
    import json, sys
    with open(sys.argv[1]) as handle:
        kind, s = json.load(handle)
    for line in sys.stdin:
        request = json.loads(line)
        rid, method = request.get("id"), request.get("method")
        if rid is None or method != "initialize":
            continue
        error = {"code": -32000, "message": s}
        frame = {
            "result-and-error": {"jsonrpc": "2.0", "id": rid, "result": {}, "error": error},
            "string-code": {"jsonrpc": "2.0", "id": rid, "error": {"code": "5", "message": s}},
            "result-null": {"jsonrpc": "2.0", "id": rid, "result": None},
            "silent": None,
        }[kind]
        if frame is not None:
            sys.stdout.write(json.dumps(frame) + "\\n")
            sys.stdout.flush()
    """
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["result-and-error", "string-code", "result-null", "silent"]
)
async def test_a_malformed_initialize_reply_ends_the_refresh_in_bounded_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    recwarn: pytest.WarningsRecorder,
    kind: str,
) -> None:
    """Round-13 claude N2: the strict envelope drops a lax-malformed reply, and
    the one SDK `ClientSession` pmcp builds (`refresh_server`: `pmcp refresh`
    and the startup cache generation) had no timeout, so such a reply to
    `initialize` hung it. It now fails within its read timeout, value-free.
    (The gateway's own transports do not use an SDK session; their requests
    have pmcp's idle timeout and ceiling, §12.)"""
    import time

    import pmcp.manifest.refresher as refresher
    from pmcp.manifest.loader import ServerConfig

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(refresher, "REFRESH_READ_TIMEOUT_SECONDS", 1.0)
    monkeypatch.setattr(refresher, "REFRESH_TIMEOUT_SECONDS", 8.0)
    script = tmp_path / "init_downstream.py"
    script.write_text(_INIT_SCRIPT)
    tap = _Tap(typing.cast(Any, _NoServer()), None, caplog, capfd, recwarn)
    for family in ("hex", "token"):
        s = _FAMILIES[family][1]
        (tmp_path / "case.json").write_text(json.dumps([kind, s]))
        config = ServerConfig(
            name="d",
            description="d",
            keywords=[],
            install={},
            command=sys.executable,
            args=[str(script), str(tmp_path / "case.json")],
        )
        mark = tap.start()
        started = time.monotonic()
        result = await asyncio.wait_for(
            refresher.refresh_server(config, force=True), 20
        )
        assert time.monotonic() - started < 10, (kind, family)
        assert result is None, (kind, family)
        observed = tap.since(mark, repr(result))
        assert observed.leaks(s) == [], (kind, family, observed)
        assert any(
            "Failed to refresh d" in r.getMessage() for r in caplog.records[mark[0] :]
        ), kind


def test_every_sdk_session_pmcp_builds_has_a_read_timeout() -> None:
    """Every `ClientSession(...)` in `src/pmcp` passes `read_timeout_seconds`
    (round-13 claude N2, as a class: today there is one, in the refresher)."""
    import ast

    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    sessions = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ClientSession"
            ):
                keywords = {k.arg for k in node.keywords}
                sessions.append(
                    (path.name, node.lineno, "read_timeout_seconds" in keywords)
                )
    assert sessions, "no SDK session found"
    assert all(ok for _, _, ok in sessions), sessions


def test_params_null_reads_as_absent_and_a_ping_is_answered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round-13 claude N1: `params: null` is outside JSON-RPC 2.0, but the
    SDK's models accept it; a `ping` sent that way is answered, on stdio and
    through the SDK client adapter, while `params` of any other non-object
    type is still dropped."""
    import time

    import mcp.client.streamable_http as streamable_module
    from pydantic import ValidationError

    from pmcp.argument_errors import jsonrpc_envelope_problem

    ping = {"jsonrpc": "2.0", "id": 4, "method": "ping", "params": None}
    assert jsonrpc_envelope_problem(ping) is None
    assert streamable_module.jsonrpc_message_adapter.validate_json(json.dumps(ping))
    with pytest.raises(ValidationError):
        streamable_module.jsonrpc_message_adapter.validate_json(
            json.dumps({**ping, "params": [1]})
        )
    manager, managed = _stdio_manager()
    replies: list[tuple[Any, str]] = []
    manager._reply_to_downstream_request = (  # type: ignore[method-assign]
        lambda name, managed, msg_id, method: replies.append((msg_id, method))
    )
    manager._handle_stdout_line("srv", managed, json.dumps(ping).encode(), time.time())
    assert replies == [(4, "ping")]


def test_the_adapter_rejects_what_pythons_parser_cannot_read() -> None:
    """Round-13 claude N3: a frame Python's `json` cannot parse is rejected by
    the strict adapter itself, never handed to the SDK's own parser, so the
    rules do not depend on the two parsers agreeing."""
    import mcp.client.streamable_http as streamable_module
    from pydantic import ValidationError

    adapter = streamable_module.jsonrpc_message_adapter
    calls: list[Any] = []
    base = adapter._base

    class _Spy:
        def validate_json(self, *args: Any, **kwargs: Any) -> Any:
            calls.append(args)
            return base.validate_json(*args, **kwargs)

    adapter._base = _Spy()
    try:
        for raw in ('{"jsonrpc": "2.0", "id": 1, "result": {}', "[" * 5000, "7" * 5000):
            with pytest.raises(ValidationError) as caught:
                adapter.validate_json(raw)
            assert "not parseable as JSON" in str(caught.value)
        assert calls == []
    finally:
        adapter._base = base


def test_drop_reasons_use_json_type_names() -> None:
    """Round-13 claude nit: the reasons name JSON's types."""
    from pmcp.argument_errors import jsonrpc_envelope_problem

    assert jsonrpc_envelope_problem("x") == "not a JSON object (string)"
    assert jsonrpc_envelope_problem(None) == "not a JSON object (null)"
    error = {"jsonrpc": "2.0", "id": 1, "error": {"code": {}, "message": "m"}}
    assert jsonrpc_envelope_problem(error) == "error code of type object"
    result = {"jsonrpc": "2.0", "id": 1.5, "result": {}}
    assert jsonrpc_envelope_problem(result) == "id of type number"
