#!/usr/bin/env python3
"""Synthetic stdio MCP server for reproducing PMCP issue #79 symptom 1b.

Raw JSON-RPC over stdin/stdout (no SDK) so we control output timing and size.
Exposes three tools that exercise the three candidate causes of the
"MCP server pmcp session expired" symptom:

  - slow_stream(seconds): runs for `seconds`, emitting a JSON-RPC progress
    notification (id-less) ~every 0.5s, then returns. Tests whether downstream
    output during a long in-flight call keeps the OUTER session alive.
  - slow_silent(seconds): sleeps for `seconds` with NO output, then returns.
    Tests the long-call path with zero keepalive traffic.
  - big_output(mib): returns a result whose text content is ~`mib` MiB. Tests
    the 10 MiB stdio read-limit disconnect (PMCP_STDIO_READ_LIMIT) — a candidate
    cause where a large downstream line tears down the connection and the NEXT
    call fails.

All stdout writes are flushed immediately.
"""
from __future__ import annotations

import json
import sys
import time

PROTOCOL_VERSION = "2025-06-18"


def _write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _log(text: str) -> None:
    # stderr is captured by PMCP's _read_stderr; handy for correlating timing.
    sys.stderr.write(f"[slow_server] {text}\n")
    sys.stderr.flush()


TOOLS = [
    {
        "name": "slow_stream",
        "description": "Run for N seconds, emitting periodic progress notifications.",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number", "default": 8}},
        },
    },
    {
        "name": "slow_silent",
        "description": "Sleep N seconds with no output, then return.",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number", "default": 8}},
        },
    },
    {
        "name": "big_output",
        "description": "Return ~N MiB of text (exercises the stdio read limit).",
        "inputSchema": {
            "type": "object",
            "properties": {"mib": {"type": "number", "default": 12}},
        },
    },
]


def _result(request_id, text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": text}]},
    }


def _handle_call(request_id, name: str, arguments: dict) -> None:
    if name == "slow_stream":
        seconds = float(arguments.get("seconds", 8))
        _log(f"slow_stream start seconds={seconds}")
        deadline = time.monotonic() + seconds
        token = arguments.get("_progressToken", "p")
        n = 0
        while time.monotonic() < deadline:
            time.sleep(0.5)
            n += 1
            # id-less JSON-RPC notification = the keepalive signal under test.
            _write(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"progressToken": token, "progress": n, "total": None},
                }
            )
        _log(f"slow_stream done emitted={n}")
        _write(_result(request_id, f"slow_stream completed after {seconds}s ({n} pings)"))
    elif name == "slow_silent":
        seconds = float(arguments.get("seconds", 8))
        _log(f"slow_silent start seconds={seconds}")
        time.sleep(seconds)
        _log("slow_silent done")
        _write(_result(request_id, f"slow_silent completed after {seconds}s"))
    elif name == "big_output":
        mib = int(arguments.get("mib", 12))
        _log(f"big_output start mib={mib}")
        payload = "x" * (mib * 1024 * 1024)
        _write(_result(request_id, payload))
        _log("big_output done")
    else:
        _write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Unknown tool: {name}"},
            }
        )


def main() -> None:
    _log("started")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        request_id = msg.get("id")
        if method == "initialize":
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "slow_server", "version": "1.0.0"},
                    },
                }
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _write({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
        elif method in ("resources/list", "prompts/list"):
            key = method.split("/")[0]
            _write({"jsonrpc": "2.0", "id": request_id, "result": {key: []}})
        elif method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            meta = params.get("_meta", {}) or {}
            if "progressToken" in meta:
                arguments["_progressToken"] = meta["progressToken"]
            _handle_call(request_id, name, arguments)
        elif request_id is not None:
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Unknown method: {method}"},
                }
            )


if __name__ == "__main__":
    main()
