"""SL-2 (FANOUT) — the stdio half of IF-0-FANOUT-2's emitter API.

`tests/runtime/fake_remote.py` serves a real `mcp.server.MCPServer` over
Streamable HTTP, so every existing runtime test built on it exercises
`ClientManager._read_sse` alone -- the stdio dispatch path
(`ClientManager._handle_stdout_line`) has no emitter at all. This module is
that emitter, deliberately NOT a real `mcp.server.MCPServer`: only
`initialize` and `tools/list` are implemented for real; `resources/list` and
`prompts/list` return a JSON-RPC "method not found" error, which
`_index_capabilities` already tolerates (treats as "server doesn't support
resources/prompts", `manager.py:1303`, `:1312`).

Two halves, in one file so the subprocess entry point and its test-side
launcher stay next to each other:

  - `__main__` -- the subprocess itself. Speaks newline-delimited JSON-RPC
    2.0 on stdin/stdout (matching `ClientManager._send_request`,
    `manager.py:2080`, and `_read_stdout`, `:1823`), and separately listens
    on a TCP "control" port for commands that mutate its own tool catalog or
    emit a `notifications/*` frame on stdout on cue. Only stdlib is imported
    at module level so this still runs when `ClientManager` spawns it with
    `sys.executable <this file> --control-port N` -- that invocation puts
    this file's own directory on `sys.path[0]`, not the repo root, so a
    repo-root-relative import (`tests.runtime.harness`, or `DownstreamEmitter`
    from `fake_remote.py`, which pulls in uvicorn/starlette) would crash the
    subprocess before it could even bind stdin. `pmcp.types` is safe to
    import unconditionally because `pmcp` is an installed package in the
    same venv as `sys.executable`, not a repo-root-relative one.
  - `build_fake_stdio_downstream()` -- the test-side launcher. Returns the
    `ResolvedServerConfig` a test passes to `ClientManager.connect_server`
    (which spawns the subprocess above as that server's downstream) plus a
    `StdioEmitter` already wired to the same control port. The caller
    allocates the port itself (`tests.runtime.harness.alloc_port()`) so this
    function doesn't need that harness import either.

`StdioEmitter` is not declared as inheriting `fake_remote.DownstreamEmitter`
(same import-safety reason) -- it satisfies that `Protocol` structurally;
`test_emitter_harness.py` pins that with an `isinstance` check against the
`@runtime_checkable` Protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

_THIS_FILE = Path(__file__).resolve()

_TOOLS_CHANGED = "notifications/tools/list_changed"


# ============================================================================
# Subprocess side (__main__)
# ============================================================================


@dataclass
class _ServerState:
    """The fake downstream's own tool catalog and the lock serializing every
    write to stdout -- both the JSON-RPC main loop's responses and the
    control thread's on-cue notifications write there, from different
    threads."""

    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    stdout_lock: threading.Lock = field(default_factory=threading.Lock)

    def handle_control(self, command: dict[str, Any]) -> dict[str, Any]:
        op = command.get("op")
        if op == "add_tool":
            name = command["name"]
            description = command.get("description") or f"dynamically added tool {name}"
            self.tools[name] = {
                "name": name,
                "description": description,
                "inputSchema": {"type": "object", "properties": {}},
            }
            return {"ok": True}
        if op == "remove_tool":
            name = command["name"]
            if name not in self.tools:
                return {"ok": False, "error": f"unknown tool: {name}"}
            del self.tools[name]
            return {"ok": True}
        if op == "emit":
            method = command.get("method", _TOOLS_CHANGED)
            self.write_stdout({"jsonrpc": "2.0", "method": method, "params": {}})
            return {"ok": True}
        return {"ok": False, "error": f"unknown control op: {op!r}"}

    def write_stdout(self, message: dict[str, Any]) -> None:
        line = (json.dumps(message) + "\n").encode()
        with self.stdout_lock:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()


class _ControlServer(threading.Thread):
    """A TCP control channel, separate from the stdio JSON-RPC channel that
    `ClientManager` owns -- the test process can't inject into that one
    without impersonating the gateway. Binds synchronously in `__init__`
    (before `main()` starts reading stdin), so by the time
    `ClientManager.connect_server` returns (which requires a successful
    `tools/list` round-trip, `manager.py:1292`), this port is guaranteed
    already listening -- no race for a caller to guard against."""

    def __init__(self, port: int, state: _ServerState) -> None:
        super().__init__(daemon=True)
        self._state = state
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", port))
        self._sock.listen(8)

    def run(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return  # socket closed under us -- process is exiting
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            for raw_line in conn.makefile("r", encoding="utf-8"):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    command = json.loads(line)
                except json.JSONDecodeError:
                    reply: dict[str, Any] = {"ok": False, "error": "invalid JSON"}
                else:
                    reply = self._state.handle_control(command)
                conn.sendall((json.dumps(reply) + "\n").encode())


def _handle_request(
    state: _ServerState, message: dict[str, Any]
) -> dict[str, Any] | None:
    """Build the JSON-RPC response for one inbound message, or `None` for a
    notification (no `id`) -- nothing to reply to, matching real MCP
    servers' handling of e.g. `notifications/initialized`."""
    msg_id = message.get("id")
    method = message.get("method")
    if msg_id is None:
        return None
    if method == "initialize":
        # Echo whatever protocolVersion the client asked for -- the
        # permissive choice, since this fake exists to prove notification
        # dispatch, not version negotiation.
        requested = (message.get("params") or {}).get("protocolVersion", "2025-06-18")
        result = {
            "protocolVersion": requested,
            "capabilities": {
                "tools": {"listChanged": True},
                "resources": {"listChanged": True},
                "prompts": {"listChanged": True},
            },
            "serverInfo": {"name": "fake-stdio", "version": "0.0.1"},
        }
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": list(state.tools.values())},
        }
    # resources/list, prompts/list, and anything else this fake doesn't
    # implement all get the same "method not found" -- tolerated by
    # `_index_capabilities` for resources/prompts (manager.py:1303, :1312).
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-port", type=int, required=True)
    args = parser.parse_args()

    state = _ServerState()
    _ControlServer(args.control_port, state).start()

    for raw_line in sys.stdin.buffer:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = _handle_request(state, message)
        if reply is not None:
            state.write_stdout(reply)
    # stdin closed (EOF) -- ClientManager tore down the pipe. Exit like any
    # other stdio downstream would.


# ============================================================================
# Test-process side
# ============================================================================


class StdioEmitter:
    """IF-0-FANOUT-2's stdio half: talks to `_ControlServer` above over a
    fresh TCP connection per command (simpler than holding one open, and the
    control channel is not itself the thing under test). Structurally
    matches `fake_remote.DownstreamEmitter` without importing it, per this
    module's docstring."""

    def __init__(self, *, host: str, port: int) -> None:
        self._host = host
        self._port = port

    async def _send(self, command: dict[str, Any]) -> dict[str, Any]:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            writer.write((json.dumps(command) + "\n").encode())
            await writer.drain()
            line = await reader.readline()
            if not line:
                raise RuntimeError(
                    "fake stdio server control channel closed without replying"
                )
            return json.loads(line.decode())
        finally:
            writer.close()

    async def add_tool(self, name: str, *, description: str = "") -> None:
        reply = await self._send(
            {"op": "add_tool", "name": name, "description": description}
        )
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "add_tool failed"))

    async def remove_tool(self, name: str) -> None:
        reply = await self._send({"op": "remove_tool", "name": name})
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "remove_tool failed"))

    async def emit(self, method: str = _TOOLS_CHANGED) -> None:
        reply = await self._send({"op": "emit", "method": method})
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "emit failed"))


@dataclass
class FakeStdioDownstream:
    config: ResolvedServerConfig
    emitter: StdioEmitter


def build_fake_stdio_downstream(name: str, *, control_port: int) -> FakeStdioDownstream:
    """Build the `ResolvedServerConfig` for a downstream named `name` that,
    once passed to `ClientManager.connect_server`, spawns this file as the
    subprocess, plus a `StdioEmitter` wired to `control_port`. The caller
    allocates `control_port` (`tests.runtime.harness.alloc_port()`) -- see
    this module's docstring for why that import doesn't belong here."""
    config = ResolvedServerConfig(
        name=name,
        source="custom",
        config=LocalMcpServerConfig(
            command=sys.executable,
            args=[str(_THIS_FILE), "--control-port", str(control_port)],
        ),
    )
    return FakeStdioDownstream(
        config=config, emitter=StdioEmitter(host="127.0.0.1", port=control_port)
    )


if __name__ == "__main__":
    _main()
