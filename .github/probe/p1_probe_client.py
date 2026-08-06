"""Drive a real downstream tool call through a running gateway.

Usage: p1_probe_client.py <gateway streamable-http url>   (e.g. .../mcp)

Used by the `min-version-smoke` CI job as the acceptance step for the declared
`mcp` floor. It connects to the gateway over Streamable HTTP, brings the
`p1probe` stdio fixture online via `gateway.connect_server`, invokes
`p1probe::p1_echo` via `gateway.invoke`, and asserts the fixture's payload came
back. That client -> gateway -> stdio downstream -> back round trip is the proof;
a successful import and a green `/health` are preconditions only.

Exits non-zero with an explanatory message on any failure so the CI step fails.
"""

import asyncio
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SENTINEL = "p1-floor-ok:"


def _text_of(result: object) -> str:
    """Flatten a CallToolResult's content blocks into one searchable string."""
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


async def main(url: str) -> None:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            connected = await session.call_tool(
                "gateway.connect_server", {"server_name": "p1probe"}
            )
            if connected.isError:
                sys.exit(f"gateway.connect_server failed: {_text_of(connected)}")

            invoked = await session.call_tool(
                "gateway.invoke",
                {
                    "tool_id": "p1probe::p1_echo",
                    "arguments": {"text": "floor"},
                },
            )
            if invoked.isError:
                sys.exit(f"gateway.invoke failed: {_text_of(invoked)}")

            payload = _text_of(invoked)
            if SENTINEL not in payload:
                sys.exit(
                    f"downstream tool call did not round-trip: "
                    f"expected {SENTINEL!r} in {payload!r}"
                )
            print(f"OK: downstream tool call round-tripped: {payload}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <gateway streamable-http url>")
    asyncio.run(main(sys.argv[1]))
