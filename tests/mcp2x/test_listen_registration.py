"""SL-2.1 — pin IF-0-P3B-2: listen-handler registration, construction order,
and shutdown.

Drives `GatewayServer._server` over a real anyio memory-stream duplex using
hand-built modern-envelope frames, exactly as measured spike 1 in the phase
plan's Context did (`plans/phase-plan-v11-P3B.md`, "Measured spike 1") --
this exercises the SDK's real dispatcher (era detection, request/response
framing, `notifications/cancelled` cancellation), not a handler called
directly, so the frame-by-frame assertions below are proof of the wired
system rather than of the SDK in isolation.

**Deliberately not covered here, and why**: IF-0-P3B-2 also calls for
asserting `gw._client_manager` holds the same `CatalogEventSink` object as
`gw._catalog_events` (identity). That requires `ClientManager.__init__` to
accept a `catalog_events` keyword -- SL-3's interface
(`ClientManager(catalog_events=...)`), owned file `src/pmcp/client/manager.py`,
not this lane's. On this branch (based on `origin/exec/v11-p3b` at SL-1 only)
that kwarg does not exist yet, so asserting it here would either edit a file
this lane does not own or hard-fail this module for a reason outside this
lane's control -- both wrong per the plan's single-writer rule. Reported to
the orchestrator; to be added (one line in `server.py`'s `ClientManager(...)`
call, one assertion here) once SL-3 merges into the integration branch, which
must happen before SL-5 starts in any case. What *is* proven below instead:
the bus, the sink, and the listen handler are constructed in the frozen
order and the sink and the handler demonstrably share one bus (an event
published through the sink reaches a stream opened via the handler) --
i.e. everything IF-0-P3B-2 requires that does not need `ClientManager`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import mcp_types
import pytest
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.message import SessionMessage
from mcp.shared.subscriptions import (
    SUBSCRIPTION_ID_META_KEY,
    PromptsListChanged,
    ToolsListChanged,
)
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY
from mcp.types.version import LATEST_MODERN_VERSION

from pmcp.server import GatewayServer
from pmcp.subscriptions import BusCatalogEventSink


def _new_server() -> GatewayServer:
    gw = GatewayServer()
    gw._create_server(instructions="test instructions")
    return gw


def _modern_envelope(
    request_id: int, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    """A `subscriptions/listen`-shaped modern-era frame (IF-0-P3B-4): both
    `_meta` keys the 2026-07-28 envelope requires, wire (camelCase)
    `notifications` filter -- built as raw dicts, deliberately, so this test
    exercises exactly what a real client sends rather than a Python-side
    convenience the SDK never sees on the wire."""
    p = dict(params)
    p["_meta"] = {
        PROTOCOL_VERSION_META_KEY: LATEST_MODERN_VERSION,
        CLIENT_CAPABILITIES_META_KEY: {},
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": p}


def _cancelled_notification(request_id: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "notifications/cancelled",
        "params": {"requestId": request_id},
    }


class DuplexSession:
    """Send/receive helper bound to one `Server.run` duplex."""

    def __init__(
        self,
        to_server_send: anyio.abc.ObjectSendStream[SessionMessage | Exception],
        from_server_recv: anyio.abc.ObjectReceiveStream[SessionMessage],
    ) -> None:
        self._to_server_send = to_server_send
        self._from_server_recv = from_server_recv

    async def send(self, envelope: dict[str, Any]) -> None:
        model = mcp_types.jsonrpc_message_adapter.validate_python(envelope)
        await self._to_server_send.send(SessionMessage(message=model))

    async def recv(self, timeout: float = 5.0) -> dict[str, Any]:
        with anyio.fail_after(timeout):
            msg = await self._from_server_recv.receive()
        return msg.message.model_dump(by_alias=True, mode="json", exclude_none=True)


@asynccontextmanager
async def duplex_session(gw: GatewayServer) -> AsyncIterator[DuplexSession]:
    """Drive `gw._server` over a live anyio memory-stream duplex pair,
    exactly as a real stdio transport would (`Server.run`), rather than
    calling the registered handler directly -- this is what makes era
    detection, `notifications/cancelled` cancellation, and result framing
    the SDK's own dispatcher rather than an assumption this test makes
    about it."""
    assert gw._server is not None
    to_server_send, to_server_recv = anyio.create_memory_object_stream[
        SessionMessage | Exception
    ](32)
    from_server_send, from_server_recv = anyio.create_memory_object_stream[
        SessionMessage
    ](32)
    async with anyio.create_task_group() as tg:

        async def run() -> None:
            await gw._server.run(  # type: ignore[union-attr]
                to_server_recv,
                from_server_send,
                gw._server.create_initialization_options(),  # type: ignore[union-attr]
            )

        tg.start_soon(run)
        try:
            yield DuplexSession(to_server_send, from_server_recv)
        finally:
            tg.cancel_scope.cancel()


# --- Registration: IF-0-P3B-2, through the P2 constructor form ------------


async def test_listen_handler_registered_by_identity() -> None:
    """Registered through `Server.__init__(on_subscriptions_listen=...)`
    (the P2 IF-0-P2-1 constructor form, not `add_request_handler`): the
    registered handler *is* `GatewayServer._listen_handler` -- identity, not
    truthiness, per the P1 false-green lesson a `getattr` default would
    reproduce."""
    gw = _new_server()
    assert gw._server is not None
    entry = gw._server.get_request_handler("subscriptions/listen")
    assert entry is not None, "no handler registered for subscriptions/listen"
    assert entry.handler is gw._listen_handler
    assert entry.params_type is mcp_types.SubscriptionsListenRequestParams


# --- Construction order: IF-0-P3B-2 ----------------------------------------


def test_bus_sink_and_listen_handler_constructed_with_correct_types() -> None:
    gw = GatewayServer()
    assert isinstance(gw._subscription_bus, InMemorySubscriptionBus)
    assert isinstance(gw._catalog_events, BusCatalogEventSink)
    assert isinstance(gw._listen_handler, ListenHandler)


async def test_catalog_events_sink_and_listen_handler_share_one_bus() -> None:
    """The bus is constructed once and shared: an event published through
    `gw._catalog_events` (the sink SL-3 will wire `ClientManager` to) is
    delivered on a stream opened through `gw._listen_handler` (the handler
    registered on `gw._server`). This is IF-0-P3B-2's construction-order
    guarantee proven the way that does not require `ClientManager`."""
    gw = _new_server()
    async with duplex_session(gw) as duplex:
        await duplex.send(
            _modern_envelope(
                1, "subscriptions/listen", {"notifications": {"toolsListChanged": True}}
            )
        )
        ack = await duplex.recv()
        assert ack["method"] == "notifications/subscriptions/acknowledged"

        gw._catalog_events.note_tools_changed()
        await gw._catalog_events.flush()

        frame = await duplex.recv()
        assert frame["method"] == "notifications/tools/list_changed"
        assert frame["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 1


# --- IF-0-P3B-4 duplex flow -------------------------------------------------


async def test_ack_is_first_frame_and_stamped_with_request_id() -> None:
    gw = _new_server()
    async with duplex_session(gw) as duplex:
        await duplex.send(
            _modern_envelope(
                7, "subscriptions/listen", {"notifications": {"toolsListChanged": True}}
            )
        )
        ack = await duplex.recv()
        assert ack["method"] == "notifications/subscriptions/acknowledged"
        assert ack["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 7

        await gw._subscription_bus.publish(ToolsListChanged())
        frame = await duplex.recv()
        assert frame["method"] == "notifications/tools/list_changed"
        assert frame["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 7


async def test_unrequested_kind_is_never_delivered() -> None:
    """A tools-only filter never receives a published `PromptsListChanged`."""
    gw = _new_server()
    async with duplex_session(gw) as duplex:
        await duplex.send(
            _modern_envelope(
                7, "subscriptions/listen", {"notifications": {"toolsListChanged": True}}
            )
        )
        await duplex.recv()  # ack

        await gw._subscription_bus.publish(PromptsListChanged())
        # Nothing honored by subscription 7 was published; a second,
        # honored publish is the proof that the first was skipped rather
        # than merely slow -- if it had been (wrongly) queued, it would
        # arrive here instead of the tools event.
        await gw._subscription_bus.publish(ToolsListChanged())
        frame = await duplex.recv()
        assert frame["method"] == "notifications/tools/list_changed"


async def test_two_concurrent_subscriptions_demultiplexed_by_id() -> None:
    gw = _new_server()
    async with duplex_session(gw) as duplex:
        await duplex.send(
            _modern_envelope(
                7, "subscriptions/listen", {"notifications": {"toolsListChanged": True}}
            )
        )
        ack7 = await duplex.recv()
        assert ack7["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 7

        await duplex.send(
            _modern_envelope(
                9, "subscriptions/listen", {"notifications": {"promptsListChanged": True}}
            )
        )
        ack9 = await duplex.recv()
        assert ack9["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 9

        await gw._subscription_bus.publish(ToolsListChanged())
        frame7 = await duplex.recv()
        assert frame7["method"] == "notifications/tools/list_changed"
        assert frame7["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 7

        await gw._subscription_bus.publish(PromptsListChanged())
        frame9 = await duplex.recv()
        assert frame9["method"] == "notifications/prompts/list_changed"
        assert frame9["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 9


async def test_cancelled_notification_ends_subscription() -> None:
    """`notifications/cancelled` carrying the listen request id ends that
    subscription with **no** result frame and no later delivery. A second,
    live subscription proves the window: after cancelling id 1, publishing
    both kinds yields exactly the second subscription's frame, meaning
    nothing was queued for the cancelled one."""
    gw = _new_server()
    async with duplex_session(gw) as duplex:
        await duplex.send(
            _modern_envelope(
                1, "subscriptions/listen", {"notifications": {"toolsListChanged": True}}
            )
        )
        await duplex.recv()  # ack id 1

        await duplex.send(
            _modern_envelope(
                2, "subscriptions/listen", {"notifications": {"promptsListChanged": True}}
            )
        )
        await duplex.recv()  # ack id 2

        await duplex.send(_cancelled_notification(1))
        # Let the cancellation land before publishing -- notifications and
        # the cancellation share no explicit ordering guarantee otherwise.
        await anyio.sleep(0.2)

        await gw._subscription_bus.publish(ToolsListChanged())  # only id 1 wants this
        await gw._subscription_bus.publish(PromptsListChanged())  # only id 2 wants this

        frame = await duplex.recv()
        assert frame["method"] == "notifications/prompts/list_changed"
        assert frame["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 2
        assert "id" not in frame, "a notification must not carry a JSON-RPC id"


async def test_shutdown_sends_listen_result_before_close() -> None:
    """`GatewayServer.shutdown()` calls `self._listen_handler.close()` as
    its first statement; that ends every open stream gracefully, and the
    final frame on the wire is the JSON-RPC response
    `{"id": <subscription id>, "result": {..., "resultType": "complete"}}`."""
    gw = _new_server()
    async with duplex_session(gw) as duplex:
        await duplex.send(
            _modern_envelope(
                42, "subscriptions/listen", {"notifications": {"toolsListChanged": True}}
            )
        )
        await duplex.recv()  # ack

        await gw.shutdown()

        final = await duplex.recv()
        assert final.get("id") == 42
        assert "method" not in final, "the terminal frame is a response, not a notification"
        assert final["result"]["resultType"] == "complete"
        assert final["result"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 42


# --- subscriptions/listen is modern-era only --------------------------------


async def test_handshake_era_listen_is_method_not_found() -> None:
    """A `subscriptions/listen` with no modern `_meta` opens (and stays on)
    the legacy handshake era, where `validate_client_request` raises
    `KeyError` for the method -- `runner.py` turns that into
    `METHOD_NOT_FOUND` (-32601)."""
    gw = _new_server()
    async with duplex_session(gw) as duplex:
        await duplex.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "subscriptions/listen",
                "params": {"notifications": {"toolsListChanged": True}},
            }
        )
        response = await duplex.recv()
        assert response.get("id") == 1
        assert response["error"]["code"] == mcp_types.METHOD_NOT_FOUND


# --- PMCP_MAX_LISTEN_STREAMS concurrency cap --------------------------------


async def test_max_listen_streams_rejects_before_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`PMCP_MAX_LISTEN_STREAMS` is read at construction (`_env_int`,
    default 64); with the cap at 1, a second concurrent listen is rejected
    -- an error response, never an ack -- while the first stays open. This
    is the deliberate one-for-one replacement for the
    `PMCP_MAX_KEEPALIVE_STREAMS` DoS guard SL-4 deletes."""
    monkeypatch.setenv("PMCP_MAX_LISTEN_STREAMS", "1")
    gw = _new_server()
    assert gw._listen_handler._max_subscriptions == 1

    async with duplex_session(gw) as duplex:
        await duplex.send(
            _modern_envelope(
                1, "subscriptions/listen", {"notifications": {"toolsListChanged": True}}
            )
        )
        ack = await duplex.recv()
        assert ack["method"] == "notifications/subscriptions/acknowledged"

        await duplex.send(
            _modern_envelope(
                2, "subscriptions/listen", {"notifications": {"toolsListChanged": True}}
            )
        )
        rejection = await duplex.recv()
        assert rejection.get("id") == 2
        assert "error" in rejection, "the second listen must be rejected, not acked"
