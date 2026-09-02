"""SL-4.4 — a real mcp 2.x Streamable HTTP downstream, for EC-P2-7.

Every existing remote-downstream test in this repo patches
`pmcp.client.manager.streamable_http_client`; Execution Notes >
"EC-P2-7 is greenfield" explicitly disqualifies that as evidence here — the
whole point is proving the *rebuilt* transport really carries headers,
really follows redirects, and really doesn't leak clients across
reconnects, none of which a patched transport can show. This module is a
genuine `mcp.server.MCPServer` served over Streamable HTTP by uvicorn's
programmatic `Server` API, run in-process as a background task on the
current event loop so a real, in-process `ClientManager` can connect to it
within the same test.

SL-2 (FANOUT) — `DownstreamEmitter` / `RemoteEmitter` here are IF-0-FANOUT-2's
remote half: the ability for a test to command this fake to mutate its own
tool catalog and emit an arbitrary `notifications/*` frame on cue, so the
gateway's SSE dispatch path (`ClientManager._read_sse`) has something to
prove itself against. `tests/runtime/fake_stdio_server.py` is the stdio half,
behind the same shape.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import uvicorn
from mcp.server import MCPServer
from mcp.server.context import ServerRequestContext
from mcp.server.session import ServerSession
from mcp_types import PaginatedRequestParams
from sse_starlette.sse import AppStatus
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

AUTH_HEADER = "authorization"

# Bounds on `run_fake_remote`'s stop sequence (Consiliency/pmcp#200). uvicorn's
# graceful drain here is unbounded -- the `uvicorn.Config` below is constructed
# without `timeout_graceful_shutdown` -- so `await server.serve()`'s task could
# never return, and did: five CI jobs died to a 25-minute silent cancel with no
# traceback. A healthy stop in this harness is sub-second, so 10 s is ~20x the
# observed cost and far below any pytest-level timeout. `CANCEL_GRACE` is what
# a cancelled `serve()` gets to unwind before the stop sequence gives up on it
# and says so out loud.
SERVE_STOP_TIMEOUT = 10.0
CANCEL_GRACE = 5.0

# The three notification methods a real downstream can claim through the
# SDK's typed `ServerSession` helpers -- anything else (including an
# unrecognised method, EC-FANOUT-5's no-op case) has no typed helper and
# falls to the raw-notify branch in `RemoteEmitter.emit`.
_TOOLS_CHANGED = "notifications/tools/list_changed"
_RESOURCES_CHANGED = "notifications/resources/list_changed"
_PROMPTS_CHANGED = "notifications/prompts/list_changed"


@runtime_checkable
class DownstreamEmitter(Protocol):
    """IF-0-FANOUT-2: the emitter API, identical across stdio
    (`fake_stdio_server.StdioEmitter`) and remote (`RemoteEmitter` below), so
    SL-3 writes one test body per scenario instead of two.

    `add_tool`/`remove_tool` mutate the fake downstream's own catalog only --
    neither sends anything on the wire by itself. `emit()` is the only thing
    that puts a `notifications/*` frame on the wire, so a caller composes:

      - `add_tool()` then `emit()` -- a real catalog change, notified
      - `remove_tool()` then `emit()` -- a real catalog change, notified
      - `emit()` alone -- notify with nothing changed (storm-suppression probe)

    Both mutators and `emit()` require the downstream to already be
    connected (a session/control-channel must exist); calling them first
    raises.
    """

    async def add_tool(self, name: str, *, description: str = "") -> None:
        """Add one real, invocable tool named `name` to the fake
        downstream's catalog. Sends no notification."""
        ...

    async def remove_tool(self, name: str) -> None:
        """Remove `name` from the fake downstream's catalog. Sends no
        notification. Raises if `name` isn't present."""
        ...

    async def emit(self, method: str = _TOOLS_CHANGED) -> None:
        """Put exactly this JSON-RPC notification method on the wire now.
        Defaults to the tools variant; also accepts the resources/prompts
        variants or any other string, including a method the gateway
        doesn't recognise (EC-FANOUT-5)."""
        ...


class _AuthGate:
    """ASGI middleware: 401s any `/mcp` request whose `Authorization` header
    doesn't match exactly. Wraps the MCPServer's own Starlette app rather
    than replacing it, so the real Streamable HTTP session-manager lifespan
    (background tasks, event store) is untouched."""

    def __init__(self, app: ASGIApp, *, expected_value: str) -> None:
        self.app = app
        self._expected = expected_value.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == "/mcp":
            headers = dict(scope["headers"])
            actual = headers.get(AUTH_HEADER.encode(), b"")
            if actual != self._expected:
                response = PlainTextResponse("Unauthorized", status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


@dataclass
class RemoteEmitter:
    """IF-0-FANOUT-2's remote half. `_server.add_tool`/`remove_tool` mutate
    the real `MCPServer`'s catalog directly -- same process, same event
    loop, no wire round-trip needed for the mutation itself. `emit()` routes
    through the most recently captured `ServerSession` (`_session_box`,
    populated by `build_fake_remote_app`'s wrapped `tools/list` handler): a
    session is guaranteed available once the caller has connected at least
    once, because `tools/list` is the FIRST request `_index_capabilities`
    sends (`manager.py:1292`)."""

    _server: MCPServer
    _session_box: list[ServerSession]

    async def add_tool(self, name: str, *, description: str = "") -> None:
        def _dyn_tool(text: str = "") -> str:
            return f"{name}:{text}"

        _dyn_tool.__doc__ = description or f"dynamically added tool {name}"
        self._server.add_tool(_dyn_tool, name=name, description=description or None)

    async def remove_tool(self, name: str) -> None:
        self._server.remove_tool(name)

    async def emit(self, method: str = _TOOLS_CHANGED) -> None:
        if not self._session_box:
            raise RuntimeError(
                "RemoteEmitter.emit() called before any tools/list request "
                "captured a session -- connect the downstream first"
            )
        session = self._session_box[-1]
        if method == _TOOLS_CHANGED:
            await session.send_tool_list_changed()
        elif method == _RESOURCES_CHANGED:
            await session.send_resource_list_changed()
        elif method == _PROMPTS_CHANGED:
            await session.send_prompt_list_changed()
        else:
            # The typed `ServerNotification` union only covers the three
            # variants above, by design -- the SDK doesn't let a server
            # claim to be sending anything else. Reach past it to the
            # session's own connection-scoped outbound channel so a test can
            # still prove the gateway's dispatch treats an unrecognised
            # method as a no-op (EC-FANOUT-5), not merely untested.
            await session._connection.notify(method, {})


def build_fake_remote_app(*, expected_auth_value: str) -> Starlette:
    """One real tool behind the auth gate, plus `/relocated`, which
    307-redirects to `/mcp` (307 preserves method + body, unlike 301/302/303
    — required for a POST-based MCP session to still resolve past it, and
    the property that proves IF-0-P2-2's `follow_redirects=True`).

    Also wires up SL-2's `RemoteEmitter` (IF-0-FANOUT-2), stashed at
    `app.state.fake_remote_emitter` so `run_fake_remote` can hand it to
    callers without changing this function's return type -- preserved
    because `scripts/probes/_serverkill_runner.py` and
    `scripts/probes/sse_flake_probe_serverkill.py` call this directly and
    expect a bare `Starlette`."""
    mcp = MCPServer("fake-remote")

    @mcp.tool()
    def fr_echo(text: str) -> str:
        """Echo the supplied text back with a fixed, greppable prefix."""
        return f"fr-echo:{text}"

    app = mcp.streamable_http_app()

    # Capture the live `ServerSession` on every `tools/list` call -- the
    # only public hook the SDK offers into a request's connection-scoped
    # session (`ServerRequestContext.session`, mcp/server/context.py:40) --
    # by re-registering the handler `MCPServer.__init__` already wired
    # (`add_request_handler` overwrites the dict entry; verified empirically,
    # see the FANOUT plan). Keeping only the latest session means a
    # reconnect's `tools/list` naturally supersedes a stale one.
    session_box: list[ServerSession] = []
    original_list_tools = mcp._handle_list_tools

    async def _capturing_list_tools(
        ctx: ServerRequestContext[object], params: PaginatedRequestParams | None
    ) -> object:
        session_box[:] = [ctx.session]
        return await original_list_tools(ctx, params)

    mcp._lowlevel_server.add_request_handler(
        "tools/list", PaginatedRequestParams, _capturing_list_tools
    )
    app.state.fake_remote_emitter = RemoteEmitter(_server=mcp, _session_box=session_box)

    async def redirect_to_mcp(request: Request) -> RedirectResponse:
        return RedirectResponse(url="/mcp", status_code=307)

    app.add_route("/relocated", redirect_to_mcp, methods=["POST", "GET"])
    app.add_middleware(_AuthGate, expected_value=expected_auth_value)
    return app


@dataclass
class RunningFakeRemote:
    port: int
    base_url: str
    mcp_url: str
    redirect_url: str
    emitter: RemoteEmitter


@contextlib.asynccontextmanager
async def run_fake_remote(
    port: int, *, expected_auth_value: str
) -> AsyncIterator[RunningFakeRemote]:
    """Serve `build_fake_remote_app()` in-process on `port`, as a background
    asyncio task on the caller's own event loop, for the duration of the
    `async with` block."""
    app = build_fake_remote_app(expected_auth_value=expected_auth_value)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)
    # Clear the latch on the way IN, not only on the way out
    # (Consiliency/pmcp#158). The teardown below resets it after this server
    # stops, but that only protects against OUR OWN shutdown -- it does nothing
    # about a server started elsewhere in this interpreter that latched the flag
    # and never cleared it (tests/mcp2x/test_listen_over_http.py and
    # tests/test_http_dos.py both stop uvicorn servers). `tests/mcp2x` sorts
    # immediately before `tests/runtime`, so in a full-suite run this server
    # would start with the flag already True and every SSE stream it served
    # would end instantly -- "SSE stream ended without a response", the exact
    # error that made test_ec_p2_7_reconnect_does_not_leak_transports flaky.
    # Verified by latching the flag directly: connect fails with that message
    # and succeeds without it.
    AppStatus.should_exit = False
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("fake remote server never started")
        yield RunningFakeRemote(
            port=port,
            base_url=f"http://127.0.0.1:{port}",
            mcp_url=f"http://127.0.0.1:{port}/mcp",
            redirect_url=f"http://127.0.0.1:{port}/relocated",
            emitter=app.state.fake_remote_emitter,
        )
    finally:
        try:
            server.should_exit = True
            # Bounded, so a server that never stops fails this test in 15 s
            # with a message instead of stalling the job for 25 minutes
            # (Consiliency/pmcp#200).
            #
            # NOT `asyncio.wait_for`, which cannot bound a cancellation-
            # resistant task: it cancels, then waits for the cancellation to
            # *finish*, so a `serve()` that swallows `CancelledError` hangs it
            # exactly as hard as the bare `await task` this replaces. Verified
            # empirically -- such a coroutine survived `wait_for`, an outer
            # `wait_for` guard, and `asyncio.run`'s own shutdown. `asyncio.wait`
            # never awaits a pending task: it reports it and moves on, which is
            # why the raise below happens whether or not the cancel took.
            # `tests/runtime/test_hang_diagnostics.py` pins both mutants.
            _, pending = await asyncio.wait({task}, timeout=SERVE_STOP_TIMEOUT)
            if pending:
                task.cancel()
                _, pending = await asyncio.wait({task}, timeout=CANCEL_GRACE)
                survived = bool(pending)
                # Whether the task survived cancellation is the single most
                # valuable fact for root-causing this, and no stack dump
                # reports it -- an idle loop parked in `epoll.poll()` names
                # nothing.
                raise RuntimeError(
                    f"fake remote on port {port} did not stop within "
                    f"{SERVE_STOP_TIMEOUT}s of should_exit=True; "
                    + (
                        f"the serve() task SURVIVED cancellation after "
                        f"{CANCEL_GRACE}s and is now orphaned"
                        if survived
                        else "the serve() task ended only once cancelled"
                    )
                    + f" [server.started={server.started!r} "
                    f"server.should_exit={server.should_exit!r} "
                    f"AppStatus.should_exit={AppStatus.should_exit!r}]"
                    " -- see Consiliency/pmcp#200"
                )
            # Healthy path: surface whatever `serve()` itself raised, exactly
            # as the previous bare `await task` did.
            task.result()
        finally:
            # sse_starlette.sse.AppStatus.should_exit is a process-global class
            # attribute, latched True by its uvicorn-shutdown signal handler and
            # never reset. Left alone, every SSE stream created afterwards — in
            # any event loop, against any server — terminates immediately, which
            # poisons whichever test runs next. This is the one site that resets
            # it; nothing else in the repo should. It is cleared on entry too, so
            # a server started elsewhere in this interpreter cannot poison this
            # one.
            #
            # The reset is unconditional and nested because the stop sequence
            # above can now raise. Before Consiliency/pmcp#200 it sat after
            # `await task`, so any exception on the way out would have skipped
            # it and poisoned the next test with the very latch this harness
            # exists to clear.
            #
            # The *mechanism* this comment used to describe was written against
            # an older sse_starlette. Installed here is 3.1.1, where
            # `vars(AppStatus)` is exactly
            # `{should_exit, original_handler, handle_exit}`: there is no
            # `should_exit_event`, so the fix floated in #200's early comment
            # ("reset `should_exit_event = None`") cannot be written as stated.
            # 3.1.1 keeps a per-event-loop `_ShutdownState` in a
            # `contextvars.ContextVar`, and `_get_uvicorn_server()` reads
            # `signal.getsignal(signal.SIGTERM).__self__` and polls *that*
            # object's `should_exit`. Two consequences follow, both plausible
            # and neither verified: a stale SIGTERM handler left by an earlier
            # uvicorn server can make a later loop's watcher see
            # `should_exit == True`, and a `_ShutdownState` registered on one
            # loop is never signalled by a watcher on another. The reset below
            # remains correct and necessary either way.
            AppStatus.should_exit = False
