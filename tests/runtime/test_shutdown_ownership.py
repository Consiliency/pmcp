"""Consiliency/pmcp#445: separate owned-fixture order from product closure.

A deliberately missed SSE shutdown observation models the fixture dependency
deterministically. It does not assert that a stale watcher caused hosted CI.
The normal-operation test uses the unmodified watcher and a real PMCP client;
forced cleanup is in finally, after the closure oracle, and cannot make it pass.
"""

from __future__ import annotations

import asyncio
import json
import sys
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
    await fake_remote.disconnect_owned_client(manager)


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
    if not disconnect_first:
        monkeypatch.setattr(fake_remote, "SERVE_STOP_TIMEOUT", 1.5)
    manager = ClientManager()

    async def body() -> None:
        context = (
            fake_remote.run_owned_remote(
                manager, alloc_port(), expected_auth_value=AUTH_VALUE
            )
            if disconnect_first
            else fake_remote.run_fake_remote(
                alloc_port(), expected_auth_value=AUTH_VALUE
            )
        )
        async with context as remote:
            remote.observe_client(manager)
            assert (
                await manager.connect_server(
                    _remote_config("owned-order", remote.mcp_url)
                )
                == []
            )
            await eventually(watcher_ready.is_set, timeout=3)

    try:
        if disconnect_first:
            await body()
        else:
            with pytest.raises(
                RuntimeError, match="ended only once cancelled"
            ) as failure:
                await body()
            milestones = json.loads(str(failure.value).split("lifecycle=", 1)[1])
            assert [item["stage"] for item in milestones] == [
                "before_signal",
                "stop_deadline",
                "after_cancel",
            ]
            assert milestones[1]["client_readers_pending"] == 1
            assert milestones[1]["client_owners_pending"] == 1
            assert milestones[1]["app_should_exit"] is False
            assert all("elapsed_s" in item and "worker" in item for item in milestones)
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
            remote.observe_client(manager)
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


@pytest.mark.asyncio
async def test_owned_remote_disconnects_even_when_the_body_asserts() -> None:
    manager = ClientManager()
    managed = None
    with pytest.raises(AssertionError, match="body sentinel"):
        async with fake_remote.run_owned_remote(
            manager, alloc_port(), expected_auth_value=AUTH_VALUE
        ) as remote:
            assert (
                await manager.connect_server(
                    _remote_config("body-error", remote.mcp_url)
                )
                == []
            )
            managed = manager._clients["body-error"]
            raise AssertionError("body sentinel")
    assert managed is not None
    assert managed.read_task is not None and managed.read_task.done()
    assert (
        managed.transport_owner_task is not None and managed.transport_owner_task.done()
    )
    assert (
        managed.remote_http_client is not None and managed.remote_http_client.is_closed
    )
    assert not manager._clients and not manager._reconnect_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resist_cancel", [False, True], ids=["never-finishes", "cancel-resistant"]
)
async def test_owned_disconnect_cannot_hide_a_pending_task(
    monkeypatch: pytest.MonkeyPatch, resist_cancel: bool
) -> None:
    manager = ClientManager()
    escape = asyncio.Event()
    tasks = []

    async def stuck_disconnect() -> None:
        tasks.append(asyncio.current_task())
        try:
            await escape.wait()
        except asyncio.CancelledError:
            if not resist_cancel:
                raise
            await escape.wait()

    monkeypatch.setattr(manager, "disconnect_all", stuck_disconnect)
    monkeypatch.setattr(fake_remote, "CLIENT_STOP_TIMEOUT", 0.05)
    monkeypatch.setattr(fake_remote, "CANCEL_GRACE", 0.05)
    try:
        with pytest.raises(RuntimeError) as failure:
            await fake_remote.disconnect_owned_client(manager)
        assert "exceeded 0.05s" in str(failure.value)
        assert f"survived_cancellation={resist_cancel}" in str(failure.value)
    finally:
        escape.set()
        _, pending = await asyncio.wait(set(tasks), timeout=1)
        assert not pending, "test mutant itself was not retired"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "resist_cancel", "second_cancel"),
    [
        ("initial_wait", False, False),
        ("initial_wait", True, False),
        ("cancellation_grace", False, False),
        ("cancellation_grace", True, False),
        ("initial_wait", True, True),
    ],
    ids=[
        "initial-honouring",
        "initial-resistant",
        "grace-honouring",
        "grace-resistant",
        "supervision-interrupted",
    ],
)
async def test_caller_cancellation_keeps_disconnect_supervised(
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    resist_cancel: bool,
    second_cancel: bool,
) -> None:
    manager = ClientManager()
    started = asyncio.Event()
    got_cancel = asyncio.Event()
    release = asyncio.Event()
    children = []
    delivered = []
    helper_cancellations = []
    child_cancellations = 0
    expected_cancellations = (
        2 if second_cancel or (stage == "cancellation_grace" and resist_cancel) else 1
    )
    enough_cancellations = asyncio.get_running_loop().create_future()
    original_wait = asyncio.wait
    wait_calls = 0

    async def disconnect() -> None:
        nonlocal child_cancellations
        children.append(asyncio.current_task())
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                child_cancellations += 1
                if child_cancellations == expected_cancellations:
                    enough_cancellations.set_result(None)
                got_cancel.set()
                if not resist_cancel:
                    raise

    async def observed_wait(tasks, *, timeout):
        nonlocal wait_calls
        if asyncio.current_task() is caller:
            wait_calls += 1
            cancel_at = 1 if stage == "initial_wait" else 2
            if wait_calls == cancel_at:
                await started.wait()
                asyncio.get_running_loop().call_soon(caller.cancel, "caller sentinel")
            elif second_cancel and wait_calls == cancel_at + 1:
                asyncio.get_running_loop().call_soon(caller.cancel, "second sentinel")
            try:
                return await original_wait(tasks, timeout=timeout)
            except asyncio.CancelledError as cancellation:
                delivered.append(cancellation)
                raise
        return await original_wait(tasks, timeout=timeout)

    monkeypatch.setattr(manager, "disconnect_all", disconnect)
    monkeypatch.setattr(fake_remote, "CLIENT_STOP_TIMEOUT", 0.01)
    monkeypatch.setattr(fake_remote, "CANCEL_GRACE", 0.02)
    monkeypatch.setattr(fake_remote.asyncio, "wait", observed_wait)

    async def call_disconnect() -> None:
        try:
            await fake_remote.disconnect_owned_client(manager)
        except asyncio.CancelledError as cancellation:
            helper_cancellations.append(cancellation)
            raise

    caller = asyncio.create_task(call_disconnect())
    try:
        _, pending = await original_wait({caller}, timeout=1)
        assert not pending, "caller cancellation escaped the finite cleanup bound"
        assert caller.cancelled()
        if sys.version_info >= (3, 11):
            with pytest.raises(
                asyncio.CancelledError, match="caller sentinel"
            ) as failure:
                await caller
            assert failure.value is delivered[0]
            assert failure.value.__notes__ == helper_cancellations[0].__notes__
        else:
            # Python 3.10's Task boundary creates a new, message-less exception.
            with pytest.raises(asyncio.CancelledError):
                await caller
        (recorded,) = helper_cancellations
        assert recorded is delivered[0]
        assert recorded.args == ("caller sentinel",)
        assert got_cancel.is_set()
        assert len(children) == 1
        assert children[0].done() is not resist_cancel
        note = json.loads(recorded.__notes__[-1])
        assert note == {
            "stage": stage,
            "supervision_interrupted": second_cancel,
            "survived_cancellation": resist_cancel,
        }
        if not resist_cancel:
            assert children[0].cancelled()
        if second_cancel:
            assert len(delivered) == 2
        _, pending = await original_wait({enough_cancellations}, timeout=1)
        assert not pending, "owned child missed a cancellation delivery"
        assert child_cancellations == expected_cancellations
    finally:
        if not enough_cancellations.done():
            enough_cancellations.cancel()
        release.set()
        if not caller.done():
            caller.cancel()
        done, pending = await original_wait({caller, *children}, timeout=1)
        assert not pending, "caller-cancellation mutant itself was not retired"
        for task in done:
            if not task.cancelled():
                task.result()


@pytest.mark.parametrize("legacy_note_api", [False, True], ids=["native", "fallback"])
def test_cancellation_note_preserves_exception_and_existing_notes(
    legacy_note_api: bool,
) -> None:
    class LegacyCancellation(asyncio.CancelledError):
        add_note = None

    cancellation = (
        LegacyCancellation("caller sentinel")
        if legacy_note_api
        else asyncio.CancelledError("caller sentinel")
    )
    existing = "existing safe note"
    vars(cancellation)["__notes__"] = [existing]
    note = json.dumps(
        {
            "stage": "initial_wait",
            "supervision_interrupted": False,
            "survived_cancellation": True,
        },
        sort_keys=True,
    )
    assert fake_remote._add_cancellation_note(cancellation, note) is None
    assert cancellation.args == ("caller sentinel",)
    assert cancellation.__notes__ == [existing, note]


@pytest.mark.asyncio
async def test_owned_disconnect_retrieves_a_late_failing_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LateDisconnectFailure(Exception):
        pass

    manager = ClientManager()
    loop = asyncio.get_running_loop()
    started = loop.create_future()
    callbacks_finished = loop.create_future()
    release = asyncio.Event()
    children = []
    caller = None

    async def disconnect() -> None:
        children.append(asyncio.current_task())
        started.set_result(None)
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass
        raise LateDisconnectFailure("late disconnect sentinel")

    monkeypatch.setattr(manager, "disconnect_all", disconnect)
    monkeypatch.setattr(fake_remote, "CANCEL_GRACE", 0.02)
    try:
        caller = asyncio.create_task(fake_remote.disconnect_owned_client(manager))
        _, pending = await asyncio.wait({started}, timeout=1)
        assert not pending, "late-failure child did not start"
        caller.cancel("caller sentinel")
        _, pending = await asyncio.wait({caller}, timeout=1)
        assert not pending, "caller cancellation escaped the finite cleanup bound"
        assert caller.cancelled()
        (child,) = children
        assert not child.done()
        # Registered after the helper's retrieval callback: observing this future
        # means that earlier callback ran, without retrieving the outcome here.
        child.add_done_callback(lambda _: callbacks_finished.set_result(None))
        release.set()
        _, pending = await asyncio.wait({callbacks_finished}, timeout=1)
        assert not pending, "late-failure callbacks did not finish"
        assert child.done() and not child.cancelled()
        assert child._log_traceback is False
        assert isinstance(child.exception(), LateDisconnectFailure)
    finally:
        release.set()
        tasks = {*children}
        if caller is not None:
            tasks.add(caller)
            if not caller.done():
                caller.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=1)
            assert not pending, "late-failure mutant itself was not retired"
            for task in done:
                if not task.cancelled():
                    task.exception()


@pytest.mark.asyncio
async def test_owned_disconnect_rejects_a_late_reconnect_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ClientManager()
    release = asyncio.Event()
    late = asyncio.create_task(release.wait())
    manager._reconnect_tasks["late-mutant"] = late

    async def incomplete_disconnect() -> None:
        manager._reconnect_tasks.clear()

    monkeypatch.setattr(manager, "disconnect_all", incomplete_disconnect)
    try:
        with pytest.raises(AssertionError, match="owned reconnect task still pending"):
            await fake_remote.disconnect_owned_client(manager)
        assert not late.done()
    finally:
        release.set()
        _, pending = await asyncio.wait({late}, timeout=1)
        assert not pending, "late-task mutant itself was not retired"
