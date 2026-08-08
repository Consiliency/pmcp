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
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

SENTINEL = "p1-floor-ok:"


class ProbeFailure(Exception):
    """A probe assertion failed. Raised, not sys.exit'ed, so the message
    survives anyio's task groups: calling sys.exit() inside the ClientSession
    context gets wrapped in a BaseExceptionGroup and the actual reason ends up
    buried under ~40 lines of traceback. __main__ unwraps and prints it alone."""


def _text_of(result: object) -> str:
    """Flatten a CallToolResult's content blocks into one searchable string."""
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


def _check(label: str, result: object) -> dict:
    """Assert a gateway tool call succeeded, and return its parsed body.

    `isError` alone is NOT sufficient. Gateway tools return their outcome as an
    ordinary JSON text block (`server.py:318` json.dumps'es the model_dump and
    never sets isError), so a genuine failure — a server that could not start,
    a missing credential — arrives as `{"ok": false, "message": "..."}` with
    isError FALSE. Checking only isError sails straight past it.

    That matters more than it looks: a failed explicit connect leaves the
    fixture registered as *lazy*, so the following invoke silently reconnects
    on demand and returns the sentinel anyway. The job would go green while
    gateway.connect_server was broken — exactly the "passes while something
    real is broken" failure this phase exists to prevent.

    Both checks are kept: isError catches transport-level failures that never
    produce a JSON body at all, and `ok` catches gateway-level ones.
    """
    payload = _text_of(result)
    if getattr(result, "is_error", False):
        raise ProbeFailure(f"{label} failed at the transport level: {payload}")

    try:
        body = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ProbeFailure(
            f"{label} did not return a JSON body ({exc}): {payload!r}"
        ) from exc

    if not isinstance(body, dict):
        raise ProbeFailure(f"{label} returned a non-object body: {payload!r}")

    if body.get("ok") is not True:
        detail = body.get("message") or body.get("errors") or payload
        raise ProbeFailure(
            f"{label} reported failure (ok={body.get('ok')!r}): {detail}"
        )

    return body


async def main(url: str) -> None:
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            connected = _check(
                "gateway.connect_server",
                await session.call_tool(
                    "gateway.connect_server", {"server_name": "p1probe"}
                ),
            )
            print(f"connected: {connected.get('new_status')!r}")

            invoked = await session.call_tool(
                "gateway.invoke",
                {
                    "tool_id": "p1probe::p1_echo",
                    "arguments": {"text": "floor"},
                },
            )
            # Same reasoning as the connect above: gateway.invoke reports its own
            # outcome as `ok` in the body, so check it explicitly rather than
            # inferring failure from the sentinel being absent.
            _check("gateway.invoke", invoked)

            payload = _text_of(invoked)
            if SENTINEL not in payload:
                raise ProbeFailure(
                    f"downstream tool call did not round-trip: "
                    f"expected {SENTINEL!r} in {payload!r}"
                )
            print(f"OK: downstream tool call round-tripped: {payload}")


def _flatten(exc: BaseException) -> "list[BaseException]":
    """Walk anyio's nested exception groups down to the real exceptions.

    Duck-typed on `.exceptions` rather than `isinstance(exc, BaseExceptionGroup)`
    because that builtin only exists on Python 3.11+, and this package declares
    support from 3.10. The job itself runs 3.12, but a NameError here would only
    surface on the failure path — the one path a guard must not break on.
    """
    nested = getattr(exc, "exceptions", None)
    if nested is None:
        return [exc]
    return [sub for inner in nested for sub in _flatten(inner)]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} <gateway streamable-http url>")
    try:
        asyncio.run(main(sys.argv[1]))
    except BaseException as exc:  # noqa: BLE001 - re-raised unless it is ours
        failures = [e for e in _flatten(exc) if isinstance(e, ProbeFailure)]
        if not failures:
            raise
        sys.exit(f"PROBE FAILED: {failures[0]}")
