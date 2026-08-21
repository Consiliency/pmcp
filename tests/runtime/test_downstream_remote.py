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
     it.

These two causes are unrelated — the earlier "two symptoms, one root"
framing in this docstring was wrong. Both are fixed now, independently, and
the file is split.
"""

from __future__ import annotations

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
