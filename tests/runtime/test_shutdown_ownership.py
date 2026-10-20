"""Consiliency/pmcp#445: separate owned-fixture order from product closure.

A deliberately missed SSE shutdown observation models the fixture dependency
deterministically. It does not assert that a stale watcher caused hosted CI.
The normal-operation test uses the unmodified watcher and a real PMCP client;
forced cleanup is in finally, after the closure oracle, and cannot make it pass.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sse_starlette import sse
import httpx2

from pmcp.client.manager import ClientManager
from tests._timing import eventually
from tests.runtime import fake_remote
from tests.runtime.harness import alloc_port
from tests.runtime.test_emitter_harness import AUTH_VALUE, _remote_config


async def _disconnect(manager: ClientManager) -> None:
    task = asyncio.create_task(manager.disconnect_all())
    done, pending = await asyncio.wait({task}, timeout=7)
    if pending:
        task.cancel()
        await asyncio.wait({task}, timeout=1)
        raise AssertionError("owned client disconnect exceeded seven seconds")
    next(iter(done)).result()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disconnect_first", [False, True], ids=["wrong-order", "owned-order"]
)
async def test_owned_order_removes_a_real_open_stream_dependency(
    monkeypatch: pytest.MonkeyPatch, disconnect_first: bool
) -> None:
    watcher_ready = asyncio.Event()

    def missed_shutdown() -> SimpleNamespace:
        watcher_ready.set()
        return SimpleNamespace(should_exit=False)

    # Fault injection only: the watcher never sees this server's exit flag.
    # The same real transport and uvicorn drain run in both orderings.
    monkeypatch.setattr(sse, "_get_uvicorn_server", missed_shutdown)
    monkeypatch.setattr(fake_remote, "SERVE_STOP_TIMEOUT", 1.5)
    manager = ClientManager()

    async def body() -> None:
        async with fake_remote.run_fake_remote(
            alloc_port(), expected_auth_value=AUTH_VALUE
        ) as remote:
            assert (
                await manager.connect_server(
                    _remote_config("owned-order", remote.mcp_url)
                )
                == []
            )
            await eventually(watcher_ready.is_set, timeout=3)
            if disconnect_first:
                await _disconnect(manager)

    try:
        if disconnect_first:
            await body()
        else:
            with pytest.raises(RuntimeError, match="ended only once cancelled"):
                await body()
    finally:
        await _disconnect(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hold_close", [False, True], ids=["normal", "leaked-stream-mutant"]
)
async def test_normal_server_shutdown_closes_the_original_http_stream(
    monkeypatch: pytest.MonkeyPatch,
    hold_close: bool,
) -> None:
    servers = []
    original_server = fake_remote.uvicorn.Server

    class ObservedServer(original_server):
        def __init__(self, config):
            super().__init__(config)
            servers.append(self)

    monkeypatch.setattr(fake_remote.uvicorn, "Server", ObservedServer)
    original_send = httpx2.AsyncClient.send
    responses = []
    release = asyncio.Event()

    async def observe_send(self, request, *args, **kwargs):
        response = await original_send(self, request, *args, **kwargs)
        if request.method == "GET":
            responses.append(response)
            if hold_close:
                close = response.aclose

                async def held_close():
                    await release.wait()
                    await close()

                response.aclose = held_close
        return response

    # Observe, then return the actual response unchanged in the normal case.
    # The mutant holds its close, rather than replacing the real transport.
    monkeypatch.setattr(httpx2.AsyncClient, "send", observe_send)
    manager = ClientManager()
    try:
        async with fake_remote.run_fake_remote(
            alloc_port(), expected_auth_value=AUTH_VALUE
        ) as remote:
            name = "normal-shutdown"
            assert (
                await manager.connect_server(_remote_config(name, remote.mcp_url)) == []
            )
            managed = manager._clients[name]
            reader = managed.read_task
            owner = managed.transport_owner_task
            client = managed.remote_http_client
            assert reader is not None and owner is not None and client is not None
            await eventually(lambda: responses, timeout=3)
            response = responses[0]
            assert not response.is_closed
            # Signal only the server. No client close or manager disconnect is
            # allowed before the *actual GET response* closure assertion.
            # Streamable HTTP permits SDK reconnects: its owner task and HTTP
            # pool need not close merely because one notification stream ends.
            servers[0].should_exit = True

            async def assert_closed():
                await eventually(
                    lambda: response.is_closed,
                    timeout=7,
                    message="PMCP retained the original HTTP stream after server shutdown",
                )

            if hold_close:
                with pytest.raises(
                    AssertionError, match="retained the original HTTP stream"
                ):
                    await assert_closed()
            else:
                await assert_closed()
            release.set()
            await _disconnect(manager)
            assert reader.done() and owner.done() and client.is_closed
    finally:
        release.set()
        await _disconnect(manager)
    assert not manager._clients
    assert not manager._reconnect_tasks
