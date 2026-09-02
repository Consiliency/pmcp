"""SL-4.4 — EC-P2-7: the rebuilt downstream Streamable HTTP client, proven
against a real HTTP peer.

Every existing remote-downstream test in this repo (`tests/test_client_manager.py`)
patches `streamable_http_client`; that's explicitly disqualified as evidence
for EC-P2-7 (Execution Notes > "EC-P2-7 is greenfield"), because it proves
nothing about whether the *rebuilt* client (IF-0-P2-2) actually carries
headers, actually follows redirects, or actually stops leaking clients
across reconnects. This module runs a real, in-process `ClientManager`
against `fake_remote.py`'s real HTTP server instead.

`ClientManager.remote_http_client.is_closed` and the process's open socket
count are internal-process state unreachable across a subprocess boundary,
so this must run in-process rather than through a booted gateway subprocess
— the "no mocks" bar applies to the fake remote peer, not to how the
gateway code runs.

Four independent properties, four independent tests, each with its own
`ClientManager`, its own `run_fake_remote(alloc_port(), ...)`, and a
`disconnect_all()` teardown. This file used to cram all four into one test
function sharing one `run_fake_remote` lifecycle, to dodge two bugs that
made splitting it fail:

  1. `disconnect_all()` in teardown raised
     `CancelledError: Cancelled via cancel scope <id> by <Task ...
     _disconnect_all_unlocked.<locals>._shutdown_one() ...
     cb=[gather.<locals>._done_callback()]`. Root cause: the remote
     transport's exit stack was entered in the task that performed the
     connect and closed in a *different* task — `_shutdown_one` under
     `asyncio.gather`, or a later loop's teardown. anyio cancel scopes are
     bound to the task that created them, so closing that stack elsewhere
     violated its invariant. Fixed in `src/pmcp/client/manager.py`: a
     long-lived per-client owner task now enters and unwinds the transport
     in itself, so ownership never crosses a task boundary.
  2. Independently, tests run *after* a completed connect-and-teardown
     failed their next connect with "SSE stream ended without a response".
     This was not a pmcp bug: `sse_starlette.sse.AppStatus.should_exit` is a
     process-global class attribute, latched `True` the first time any
     uvicorn server in the process shuts down and never reset, so every SSE
     stream created afterwards — in any loop, against any server —
     terminates immediately. Fixed in `tests/runtime/fake_remote.py`'s
     `run_fake_remote` `finally:` block, which is the one place that resets
     it. (Version note, Consiliency/pmcp#200: the installed sse_starlette is
     3.1.1, whose `AppStatus` has no `should_exit_event` — it keeps a
     per-event-loop `_ShutdownState` in a `contextvars.ContextVar` and reads
     the live server back out of `signal.getsignal(signal.SIGTERM).__self__`;
     the class attribute above, and this reset, are still exactly as
     described.)

These two causes are unrelated — the earlier "two symptoms, one root"
framing in this docstring was wrong. Both are fixed now, independently, and
the file is split.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from pmcp.client.manager import ClientManager
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from pmcp.types import RemoteMcpServerConfig, ResolvedServerConfig
from sse_starlette.sse import AppStatus

from tests.runtime.fake_remote import run_fake_remote
from tests.runtime.harness import alloc_port, open_socket_fd_count

AUTH_VALUE = "Bearer rt-secret-token"


def _config(
    name: str, url: str, *, headers: dict[str, str] | None = None
) -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name,
        source="custom",
        config=RemoteMcpServerConfig(type="streamable-http", url=url, headers=headers),
    )


@pytest.mark.asyncio
async def test_ec_p2_7_configured_headers_reach_peer() -> None:
    """Headers set by IF-0-P2-2's httpx2.AsyncClient construction really
    reach the peer: the fake remote 401s without the exact configured
    Authorization value, so a successful connect + real gateway.invoke
    proves the rebuilt client carries them, not just that a mock recorded a
    kwarg."""
    manager = ClientManager()
    try:
        async with run_fake_remote(
            alloc_port(), expected_auth_value=AUTH_VALUE
        ) as remote:
            errors = await manager.connect_server(
                _config(
                    "fr-auth", remote.mcp_url, headers={"Authorization": AUTH_VALUE}
                )
            )
            assert errors == []
            assert manager.is_server_online("fr-auth") is True

            gateway_tools = GatewayTools(
                client_manager=manager, policy_manager=PolicyManager()
            )
            result = await gateway_tools.invoke(
                {
                    "tool_id": "fr-auth::fr_echo",
                    "arguments": {"text": "wire-proof"},
                }
            )
            assert result.ok is True
            content = result.result["content"]  # type: ignore[index]
            assert content[0]["text"] == "fr-echo:wire-proof"
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_ec_p2_7_auth_gate_rejects_missing_and_wrong_header() -> None:
    """Negative case: without this, the positive header-carrying test would
    be hollow — a fake remote that accepts everything can't prove headers
    matter. Confirms the 401 gate is live, not dead code, both for a
    missing Authorization header and for the wrong value."""
    manager = ClientManager()
    try:
        async with run_fake_remote(
            alloc_port(), expected_auth_value=AUTH_VALUE
        ) as remote:
            errors = await manager.connect_server(_config("fr-noauth", remote.mcp_url))
            assert errors != []
            assert manager.is_server_online("fr-noauth") is False

            errors = await manager.connect_server(
                _config(
                    "fr-wrongauth",
                    remote.mcp_url,
                    headers={"Authorization": "Bearer not-the-right-token"},
                )
            )
            assert errors != []
            assert manager.is_server_online("fr-wrongauth") is False
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_ec_p2_7_follows_redirects() -> None:
    """Proves follow_redirects=True (IF-0-P2-2): the configured URL points
    at /relocated, which 307s to /mcp. httpx2's own default is
    follow_redirects=False, so this fails if pmcp ever drops the explicit
    override."""
    manager = ClientManager()
    try:
        async with run_fake_remote(
            alloc_port(), expected_auth_value=AUTH_VALUE
        ) as remote:
            errors = await manager.connect_server(
                _config(
                    "fr-redirect",
                    remote.redirect_url,
                    headers={"Authorization": AUTH_VALUE},
                )
            )
            assert errors == []
            assert manager.is_server_online("fr-redirect") is True
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_ec_p2_7_reconnect_does_not_leak_transports() -> None:
    """The leak proof. Calls the private `_connect_streamable_http` directly
    (not the public `connect_server`, whose singleflight guard short-circuits
    as a no-op when the server is already ONLINE, and so never reaches the
    reconnect branch at all): each call finds `name in self._clients` from
    the previous iteration and takes `_connect_remote_stream`'s "existing
    live connection" branch, which calls `_cleanup_client` — the method
    IF-0-P2-2 taught to close the remote transport (and therefore the owned
    `httpx2.AsyncClient`) for remote clients; before that phase it did not."""
    manager = ClientManager()
    prior_clients: list[object] = []
    try:
        async with run_fake_remote(
            alloc_port(), expected_auth_value=AUTH_VALUE
        ) as remote:
            reconnect_config = _config(
                "fr-reconnect", remote.mcp_url, headers={"Authorization": AUTH_VALUE}
            )
            socket_counts_by_cycle = []
            for _ in range(5):
                await manager._connect_streamable_http(reconnect_config)
                managed = manager._clients["fr-reconnect"]
                assert managed.remote_http_client is not None
                prior_clients.append(managed.remote_http_client)
                socket_counts_by_cycle.append(open_socket_fd_count())

            for closed_client in prior_clients[:-1]:
                assert closed_client.is_closed is True  # type: ignore[attr-defined]
            assert prior_clients[-1].is_closed is False  # type: ignore[attr-defined]

            # Compare against the count right after the FIRST cycle, not
            # before it: establishing this server's very first connection
            # has its own one-time socket cost (control connection +
            # standalone SSE listener), which is not what this proves.
            # What matters is that cycles 2-5 add nothing further — every
            # count in socket_counts_by_cycle[1:] must be no greater than
            # the first. Not strict equality (SL-5 fix, inherited from P2):
            # a late-closing socket from the *previous* cycle's teardown can
            # still be in FIN_WAIT/closing when the *next* cycle's count is
            # sampled, making a later sample transiently *lower* than an
            # earlier one (observed under concurrent load: [11,11,11,11,9]).
            # A strict `==` fails on that decrease and its own message then
            # claims the count "grew", sending the next reader hunting a
            # leak that isn't there. The property this proves is "no
            # growth", not "no fluctuation" — `<=` is what that means.
            baseline = socket_counts_by_cycle[0]
            assert all(count <= baseline for count in socket_counts_by_cycle[1:]), (
                "socket count grew across reconnect cycles (a leak): "
                f"baseline={baseline}, counts={socket_counts_by_cycle}"
            )
    finally:
        await manager.disconnect_all()

    # The final reconnect-cycle client is closed too, by disconnect_all
    # above rather than by a further reconnect.
    assert prior_clients[-1].is_closed is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_ec_p2_7_fake_remote_survives_a_pre_latched_appstatus() -> None:
    """A poisoned `AppStatus.should_exit` must not break the next server.

    Consiliency/pmcp#158. `sse_starlette.sse.AppStatus.should_exit` is a
    process-global class attribute latched True by uvicorn's shutdown handler
    and never reset. `run_fake_remote` cleared it on teardown, which protects
    against its OWN shutdown but not against a server started elsewhere in the
    interpreter -- `tests/mcp2x/test_listen_over_http.py` and
    `tests/test_http_dos.py` both stop uvicorn servers, and `tests/mcp2x` sorts
    immediately before `tests/runtime`.

    Inheriting a latched flag makes every SSE stream end instantly, which is
    why the leak test above failed intermittently in CI with "SSE stream ended
    without a response" while passing in isolation locally.

    This latches the flag deliberately -- the state a full-suite run can
    genuinely produce -- and asserts a connection still works.
    """
    AppStatus.should_exit = True
    manager = ClientManager()
    try:
        async with run_fake_remote(
            alloc_port(), expected_auth_value=AUTH_VALUE
        ) as remote:
            config = _config(
                "fr-latched", remote.mcp_url, headers={"Authorization": AUTH_VALUE}
            )
            await manager._connect_streamable_http(config)
            assert manager._clients["fr-latched"].remote_http_client is not None
    finally:
        await manager.disconnect_all()
        AppStatus.should_exit = False


# ============================================================================
# SL-3 (FANOUT) — EC-FANOUT-1/2/3/9: catalog freshness, remote transport.
#
# `tests/runtime/test_emitter_harness.py` (SL-2) already proves the emitter's
# frames reach `ClientManager._read_sse`; that only proves the notification
# arrived, which a forward-to-sink implementation that never re-indexes would
# also satisfy. This proves the CATALOG moved: after a real emission, an
# ADDED tool is returned by `gateway.catalog_search` and answers a real
# `gateway.invoke`, and a REMOVED tool is gone from both.
# `tests/runtime/test_downstream_stdio.py` is this section's stdio twin;
# EC-FANOUT-3 requires both to pass independently.
# ============================================================================


async def _catalog_tool_ids(gateway_tools: GatewayTools, server: str) -> set[str]:
    """The tool_ids `gateway.catalog_search` currently returns for `server`,
    with no text query -- `CatalogSearchInput.query` is optional, and the
    server filter alone is enough to scope this to one downstream."""
    output = await gateway_tools.catalog_search(
        {"filters": {"server": server}, "limit": 100}
    )
    return {card.tool_id for card in output.results}


async def _wait_until(
    predicate: Callable[[], Awaitable[bool]],
    *,
    attempts: int = 50,
    interval: float = 0.05,
) -> bool:
    """Poll an async predicate until it is True or `attempts` is exhausted.
    Returns the last observed value, so a failing assertion shows what the
    catalog actually held rather than just "timed out"."""
    result = False
    for _ in range(attempts):
        result = await predicate()
        if result:
            return True
        await asyncio.sleep(interval)
    return result


@pytest.mark.asyncio
class TestRemoteDownstreamNotificationCatalogFreshness:
    async def test_downstream_notification_added_tool_is_searchable_and_invocable(
        self,
    ) -> None:
        """EC-FANOUT-1/9, remote half of EC-FANOUT-3: a tool ADDED by the
        downstream after connect is returned by `gateway.catalog_search` and
        answers a real `gateway.invoke` call -- proof the index actually
        moved, not just that a notification was received."""
        manager = ClientManager()
        try:
            async with run_fake_remote(
                alloc_port(), expected_auth_value=AUTH_VALUE
            ) as remote:
                errors = await manager.connect_server(
                    _config(
                        "fr-fanout-add",
                        remote.mcp_url,
                        headers={"Authorization": AUTH_VALUE},
                    )
                )
                assert errors == []
                gateway_tools = GatewayTools(
                    client_manager=manager, policy_manager=PolicyManager()
                )
                tool_id = "fr-fanout-add::fr_dyn"

                assert tool_id not in await _catalog_tool_ids(
                    gateway_tools, "fr-fanout-add"
                )

                await remote.emitter.add_tool("fr_dyn", description="dynamic")
                await remote.emitter.emit("notifications/tools/list_changed")

                found = await _wait_until(
                    lambda: _tool_present(gateway_tools, "fr-fanout-add", tool_id)
                )
                assert found, "gateway.catalog_search never reflected the added tool"

                result = await gateway_tools.invoke(
                    {"tool_id": tool_id, "arguments": {"text": "hi"}}
                )
                assert result.ok is True, result.errors
        finally:
            await manager.disconnect_all()

    async def test_downstream_notification_removed_tool_disappears_from_catalog_and_invoke(
        self,
    ) -> None:
        """EC-FANOUT-2, remote half of EC-FANOUT-3: the flip side. A tool the
        downstream REMOVES is gone from both `gateway.catalog_search` and
        `gateway.invoke` after the server announces the change."""
        manager = ClientManager()
        try:
            async with run_fake_remote(
                alloc_port(), expected_auth_value=AUTH_VALUE
            ) as remote:
                errors = await manager.connect_server(
                    _config(
                        "fr-fanout-remove",
                        remote.mcp_url,
                        headers={"Authorization": AUTH_VALUE},
                    )
                )
                assert errors == []
                gateway_tools = GatewayTools(
                    client_manager=manager, policy_manager=PolicyManager()
                )
                tool_id = "fr-fanout-remove::fr_removable"

                await remote.emitter.add_tool("fr_removable")
                await remote.emitter.emit("notifications/tools/list_changed")
                found = await _wait_until(
                    lambda: _tool_present(gateway_tools, "fr-fanout-remove", tool_id)
                )
                assert found, "setup: added tool never appeared in the catalog"

                await remote.emitter.remove_tool("fr_removable")
                await remote.emitter.emit("notifications/tools/list_changed")

                gone = await _wait_until(
                    lambda: _tool_absent(gateway_tools, "fr-fanout-remove", tool_id)
                )
                assert gone, (
                    "gateway.catalog_search still lists a tool the downstream removed"
                )

                result = await gateway_tools.invoke(
                    {"tool_id": tool_id, "arguments": {}}
                )
                assert result.ok is False
                assert result.errors
        finally:
            await manager.disconnect_all()


async def _tool_present(gateway_tools: GatewayTools, server: str, tool_id: str) -> bool:
    return tool_id in await _catalog_tool_ids(gateway_tools, server)


async def _tool_absent(gateway_tools: GatewayTools, server: str, tool_id: str) -> bool:
    return tool_id not in await _catalog_tool_ids(gateway_tools, server)


# ============================================================================
# SL-3.3 (FANOUT) — EC-FANOUT-6: no self-deadlock.
#
# `_reconcile_server_catalog` must be scheduled with `asyncio.create_task`,
# never awaited inline from the dispatch path: `_index_capabilities` awaits
# `_send_request`, and that future is resolved by the very read loop
# (`_read_sse`) that received the notification. An inline await would freeze
# that loop permanently -- it would be waiting on a future only its own next
# iteration could resolve.
#
# The unrelated request MUST go to the same downstream whose read loop
# handled the notification. The loops are per-connection, so a request to a
# *different* server would stay unaffected by a broken (deadlocked) server's
# loop either way, and would not exercise this hazard at all.
# ============================================================================


@pytest.mark.asyncio
class TestRemoteSelfDeadlock:
    async def test_unrelated_request_to_same_downstream_completes_after_notification(
        self,
    ) -> None:
        manager = ClientManager()
        try:
            async with run_fake_remote(
                alloc_port(), expected_auth_value=AUTH_VALUE
            ) as remote:
                errors = await manager.connect_server(
                    _config(
                        "fr-deadlock",
                        remote.mcp_url,
                        headers={"Authorization": AUTH_VALUE},
                    )
                )
                assert errors == []
                gateway_tools = GatewayTools(
                    client_manager=manager, policy_manager=PolicyManager()
                )

                await remote.emitter.add_tool("fr_dyn_deadlock")
                await remote.emitter.emit("notifications/tools/list_changed")

                # Issue the unrelated request immediately, while the reconcile
                # the notification just triggered may still be in flight, to
                # the SAME downstream. A deadlocked read loop would never
                # resolve this -- bound it so a real deadlock fails this test
                # with a timeout instead of hanging the suite.
                result = await asyncio.wait_for(
                    gateway_tools.invoke(
                        {
                            "tool_id": "fr-deadlock::fr_echo",
                            "arguments": {"text": "still-alive"},
                        }
                    ),
                    timeout=5.0,
                )
                assert result.ok is True, result.errors
                content = result.result["content"]  # type: ignore[index]
                assert content[0]["text"] == "fr-echo:still-alive"
        finally:
            await manager.disconnect_all()
