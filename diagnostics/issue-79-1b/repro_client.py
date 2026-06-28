#!/usr/bin/env python3
"""Streamable-HTTP client that reproduces PMCP issue #79 symptom 1b.

Connects to a running PMCP gateway over the same streamable-HTTP transport
Claude Code uses, then issues a sequence of long downstream tool calls via
gateway.invoke — including the "2nd long call" and a >10 MiB call — recording
per-call success/elapsed/error so we can see WHERE the session breaks.

The client read timeout is deliberately generous (120s) so we observe the
GATEWAY/transport tearing the session down, not a client-side timeout.

Usage: repro_client.py [URL]   (default http://127.0.0.1:3344/mcp)
"""
from __future__ import annotations

import asyncio
import sys
import time
import traceback
from datetime import timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3344/mcp"

# (label, tool, arguments, gateway timeout_ms)
SEQUENCE = [
    ("1: slow_stream 8s", "slowsrv::slow_stream", {"seconds": 8}, 60000),
    ("2: slow_stream 8s (the reported 2nd-call failure)", "slowsrv::slow_stream", {"seconds": 8}, 60000),
    ("3: slow_silent 8s (no keepalive traffic)", "slowsrv::slow_silent", {"seconds": 8}, 60000),
    ("4: big_output 12MiB (read-limit candidate)", "slowsrv::big_output", {"mib": 12}, 60000),
    ("5: slow_stream 3s (does the conn survive #4?)", "slowsrv::slow_stream", {"seconds": 3}, 60000),
]

READ_TIMEOUT = timedelta(seconds=120)


async def main() -> int:
    print(f"[client] connecting to {URL}", flush=True)
    results: list[tuple[str, bool, float, str]] = []
    async with streamablehttp_client(URL) as (read, write, _get_sid):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(
                f"[client] gateway tools: {[t.name for t in tools.tools]}",
                flush=True,
            )
            for label, tool_id, arguments, timeout_ms in SEQUENCE:
                start = time.monotonic()
                print(f"\n[client] >>> {label}", flush=True)
                try:
                    res = await session.call_tool(
                        "gateway.invoke",
                        {
                            "tool_id": tool_id,
                            "arguments": arguments,
                            "timeout_ms": timeout_ms,
                        },
                        read_timeout_seconds=READ_TIMEOUT,
                    )
                    elapsed = time.monotonic() - start
                    is_err = bool(getattr(res, "isError", False))
                    # gateway.invoke returns ok/errors inside its payload too.
                    text = ""
                    for c in res.content:
                        text += getattr(c, "text", "")
                    ok = not is_err and '"ok": false' not in text.lower()
                    snippet = text[:200].replace("\n", " ")
                    print(
                        f"[client] <<< ok={ok} isError={is_err} elapsed={elapsed:.1f}s "
                        f"payload[:200]={snippet!r}",
                        flush=True,
                    )
                    results.append((label, ok, elapsed, snippet if not ok else ""))
                except Exception as exc:  # noqa: BLE001 - we want every failure mode
                    elapsed = time.monotonic() - start
                    err = f"{type(exc).__name__}: {exc}"
                    print(
                        f"[client] <<< EXCEPTION elapsed={elapsed:.1f}s {err}",
                        flush=True,
                    )
                    traceback.print_exc()
                    results.append((label, False, elapsed, err))

    print("\n========== SUMMARY ==========", flush=True)
    for label, ok, elapsed, err in results:
        status = "OK " if ok else "FAIL"
        line = f"{status}  {elapsed:5.1f}s  {label}"
        if err:
            line += f"   -> {err}"
        print(line, flush=True)
    failures = [r for r in results if not r[1]]
    print(f"\n{len(failures)} failure(s) of {len(results)} calls.", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
