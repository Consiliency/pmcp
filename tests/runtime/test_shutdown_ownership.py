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
async def test_normal_server_shutdown_closes_the_original_pmcp_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    servers = []
    original_server = fake_remote.uvicorn.Server

    class ObservedServer(original_server):
        def __init__(self, config):
            super().__init__(config)
            servers.append(self)

    monkeypatch.setattr(fake_remote.uvicorn, "Server", ObservedServer)
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
            # Signal only the server. No client close or manager disconnect is
            # allowed before this normal-operation closure assertion.
            servers[0].should_exit = True
            await eventually(
                lambda: reader.done() and owner.done() and client.is_closed,
                timeout=7,
                message="PMCP retained the original stream after server shutdown",
            )
    finally:
        await _disconnect(manager)
    assert not manager._clients
    assert not manager._reconnect_tasks
