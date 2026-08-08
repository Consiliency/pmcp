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

All five properties below share ONE `run_fake_remote` lifecycle inside ONE
test function, rather than one `run_fake_remote` per test — empirically,
not a style choice: a real streamable-http client connection that fully
establishes its standalone SSE listener and is then torn down leaves
something in anyio's process-global task/cancel-scope bookkeeping that
breaks the *next independent* event loop's uvicorn server (reproduced with
`git bisect`-style isolation — a connect-only cycle, or a second server
with no client at all, does not trigger it; only a fully connected-then-
disconnected client followed by a *separate* `asyncio.run()`/event-loop
boundary does). That is orthogonal to anything IF-0-P2-2 changed — it
reproduces with a plain, non-redirected, non-authenticated connect too —
and looks like an anyio/httpx2/uvicorn interaction rather than a pmcp bug;
reported to the team lead rather than routed around silently. Keeping
everything in one test function's one event loop avoids it and keeps the
evidence real.
"""

from __future__ import annotations

import pytest

from pmcp.client.manager import ClientManager
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from pmcp.types import RemoteMcpServerConfig, ResolvedServerConfig
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
async def test_ec_p2_7_headers_redirect_and_reconnect_leak() -> None:
    manager = ClientManager()
    connected_names: list[str] = []
    try:
        async with run_fake_remote(
            alloc_port(), expected_auth_value=AUTH_VALUE
        ) as remote:
            # 1. Headers set by IF-0-P2-2's httpx2.AsyncClient construction
            #    really reach the peer: the fake remote 401s without the
            #    exact configured Authorization value, so a successful
            #    connect + real gateway.invoke proves the rebuilt client
            #    carries them, not just that a mock recorded a kwarg.
            errors = await manager.connect_server(
                _config(
                    "fr-auth", remote.mcp_url, headers={"Authorization": AUTH_VALUE}
                )
            )
            assert errors == []
            connected_names.append("fr-auth")
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

            # 2. Negative case: without this, (1) would be hollow — a fake
            #    remote that accepts everything can't prove headers matter.
            #    Confirms the 401 gate is live, not dead code.
            errors = await manager.connect_server(_config("fr-noauth", remote.mcp_url))
            assert errors != []
            assert manager.is_server_online("fr-noauth") is False

            # 2b. Wrong value, not just absent.
            errors = await manager.connect_server(
                _config(
                    "fr-wrongauth",
                    remote.mcp_url,
                    headers={"Authorization": "Bearer not-the-right-token"},
                )
            )
            assert errors != []

            # 3. Proves follow_redirects=True (IF-0-P2-2): the configured
            #    URL points at /relocated, which 307s to /mcp. httpx2's own
            #    default is follow_redirects=False, so this fails if pmcp
            #    ever drops the explicit override.
            errors = await manager.connect_server(
                _config(
                    "fr-redirect",
                    remote.redirect_url,
                    headers={"Authorization": AUTH_VALUE},
                )
            )
            assert errors == []
            connected_names.append("fr-redirect")
            assert manager.is_server_online("fr-redirect") is True

            # 4. The leak proof. Calls the private `_connect_streamable_http`
            #    directly (not the public `connect_server`, whose
            #    singleflight guard short-circuits as a no-op when the
            #    server is already ONLINE — manager.py:580-581 — and so
            #    never reaches the reconnect branch at all): each call finds
            #    `name in self._clients` from the previous iteration and
            #    takes `_connect_remote_stream`'s "existing live connection"
            #    branch, which calls `_cleanup_client` — the method
            #    IF-0-P2-2 taught to close `managed.sse_exit_stack` (and
            #    therefore the owned `httpx2.AsyncClient`) for remote
            #    clients; before this phase it did not.
            reconnect_config = _config(
                "fr-reconnect", remote.mcp_url, headers={"Authorization": AUTH_VALUE}
            )
            prior_clients = []
            socket_counts_by_cycle = []
            for _ in range(5):
                await manager._connect_streamable_http(reconnect_config)
                managed = manager._clients["fr-reconnect"]
                assert managed.remote_http_client is not None
                prior_clients.append(managed.remote_http_client)
                socket_counts_by_cycle.append(open_socket_fd_count())
            connected_names.append("fr-reconnect")

            for closed_client in prior_clients[:-1]:
                assert closed_client.is_closed is True
            assert prior_clients[-1].is_closed is False

            # Compare against the count right after the FIRST cycle, not
            # before it: establishing this server's very first connection
            # has its own one-time socket cost (control connection +
            # standalone SSE listener), which is not what this proves.
            # What matters is that cycles 2-5 add nothing further — every
            # count in socket_counts_by_cycle[1:] must equal the first.
            baseline = socket_counts_by_cycle[0]
            assert all(count == baseline for count in socket_counts_by_cycle[1:]), (
                f"socket count grew across reconnect cycles: {socket_counts_by_cycle}"
            )
    finally:
        # disconnect_server (not disconnect_all, whose concurrent gather of
        # every managed client's shutdown can trip anyio's cross-task
        # cancel-scope guard — see the module docstring) closes each
        # connected client explicitly.
        for name in connected_names:
            if manager.is_server_online(name):
                await manager.disconnect_server(name, force=True)

    # The final reconnect-cycle client is closed too, by the disconnect
    # above rather than by a further reconnect.
    assert prior_clients[-1].is_closed is True
