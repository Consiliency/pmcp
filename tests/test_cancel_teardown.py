"""Consiliency/pmcp#324: a caller cancelled during a teardown keeps its
cancellation, and the teardown it abandons cannot leave a process, a client
entry or a held lock behind.

Before this module, every teardown in `ClientManager` cancelled child tasks
and awaited them through `except (CancelledError, Exception): pass`, which
also absorbed the *caller's* cancellation: the caller raised the handshake
error instead, and `_connect_with_retry` started another attempt.

The rule these tests pin (round 3 of the plan):

* Child waits go through `asyncio.wait`, which never raises the child's
  outcome, so a `CancelledError` out of a teardown await is the caller's.
* On it, the teardown finishes SYNCHRONOUSLY (`_abandon_client_io`): SIGKILL
  the process tree, cancel the client's tasks without awaiting them, drop the
  registries -- then re-raise. No await, so nothing in it can be cancelled
  before it runs, hang, or hold the lifecycle lock across a suspension.
* Without a caller cancel the graceful teardown is unchanged, and a child's
  own cancellation is still absorbed.

Deterministic: every suspension point is reached through an `asyncio.Event`
gate, and the cancel is delivered while the caller is parked there. The gate
is released only AFTER the caller's outcome is read: a caller that waited for
its teardown would hit the hang guard instead of passing. No timed sleeps;
`asyncio.wait_for(..., _HANG_GUARD_S)` is a hang guard only (tests/_timing.py).
"""

from __future__ import annotations

import ast
import asyncio
import builtins
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.client import manager as manager_mod
from pmcp.client.manager import ClientManager, ManagedClient
from pmcp.types import (
    LocalMcpServerConfig,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    ServerStatus,
    ServerStatusEnum,
)
from tests._timing import eventually, eventually_sync

_HANG_GUARD_S = 10.0
_CHILD_POINTS = {"read_task", "stderr_task", "outbound_writer"}
_PKG = Path(manager_mod.__file__).resolve().parents[1]  # src/pmcp


_ALL_GATES: list[_Gate] = []


class _Gate:
    """One suspension point: `entered` is set once the caller is parked on
    it; `release` lets it finish (only ever after the caller's outcome)."""

    def __init__(self) -> None:
        self.parked = asyncio.Event()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        _ALL_GATES.append(self)


@pytest.fixture(autouse=True)
async def _release_every_gate() -> Any:
    """However a test ends -- including on a tree where it fails early --
    release every gate it made, so a child that ignores cancellation cannot
    hang the event loop's own teardown."""
    yield
    for gate in _ALL_GATES:
        gate.release.set()
    _ALL_GATES.clear()
    await asyncio.sleep(0)


def _child(gate: _Gate | None) -> asyncio.Task[None]:
    """A child task a teardown will cancel. With a gate it *defers* its own
    cancellation until released, so a teardown that awaited it would park."""

    async def body() -> None:
        try:
            if gate is not None:
                gate.parked.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if gate is not None:
                gate.entered.set()
                await _wait_ignoring_cancels(gate.release)
            raise

    return asyncio.create_task(body())


async def _wait_ignoring_cancels(event: asyncio.Event) -> None:
    """Wait for ``event`` through any number of cancellations -- a child or
    a transport finalizer that does not stop when told to. Only waiting on
    it can then hold a caller; re-cancelling it does not help."""
    while not event.is_set():
        try:
            await event.wait()
        except asyncio.CancelledError:
            continue


def _gated_call(gate: _Gate | None, record: list[str], label: str) -> Any:
    """An async stand-in for `_terminate_process_tree` /
    `_close_remote_transport` that parks on `gate`."""

    async def call(*_a: Any, **_k: Any) -> None:
        if gate is not None:
            gate.entered.set()
            await gate.release.wait()
        record.append(label)

    return call


def _recording_kill(record: list[str], *, fail: bool = False) -> Any:
    def kill(process: Any, **_k: Any) -> None:
        record.append("killed")
        if fail:
            raise OSError("secret-ish detail")

    return kill


async def _cancel_parked(task: asyncio.Task[Any], gate: _Gate) -> None:
    await asyncio.wait_for(gate.entered.wait(), _HANG_GUARD_S)
    assert not task.done(), "caller finished before reaching the gate"
    task.cancel()


async def _outcome(task: asyncio.Task[Any]) -> tuple[str, Any]:
    """('cancelled', None) | ('raised', exc) | ('returned', value)."""
    try:
        value = await asyncio.wait_for(asyncio.shield(task), _HANG_GUARD_S)
    except asyncio.CancelledError:
        if task.cancelled():
            return ("cancelled", None)
        raise
    except BaseException as exc:  # noqa: BLE001 - classify, don't propagate
        return ("raised", exc)
    return ("returned", value)


async def _ends_within_yields(task: asyncio.Future[Any], yields: int = 100) -> bool:
    """Whether ``task`` ends within ``yields`` loop iterations -- a task
    that was cancelled or signalled ends in a few; one nobody told to stop
    never does. A yield count, not a timer."""
    for _ in range(yields):
        if task.done():
            return True
        await asyncio.sleep(0)
    return task.done()


async def _drain(mgr: ClientManager, *gates: _Gate) -> None:
    """Test cleanup: release every gate, end every background task."""
    for gate in gates:
        gate.release.set()
    pending = [t for t in mgr._background_tasks if not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.wait(pending, timeout=_HANG_GUARD_S)


def _stdio_config(name: str = "srv") -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name, source="project", config=LocalMcpServerConfig(command="fake")
    )


def _remote_config(name: str = "remote") -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name,
        source="custom",
        config=RemoteMcpServerConfig(
            type="streamable-http", url="http://example.invalid/mcp"
        ),
    )


# --- the handshake-failure teardowns -----------------------------------------

_STDIO_POINTS = ["read_task", "stderr_task", "outbound_writer", "terminate"]


def _stdio_harness(
    mgr: ClientManager, point: str | None, gate: _Gate, record: list[str]
) -> tuple[Any, Any, Any]:
    """Patch `_connect_stdio`'s collaborators so its handshake fails and its
    graceful teardown parks at `point`."""
    process = MagicMock()
    process.returncode = None
    process.stderr = MagicMock()

    async def _read_stdout(name: str, managed: ManagedClient) -> None:
        await _child(gate if point == "read_task" else None)

    async def _read_stderr(name: str, stderr: Any) -> None:
        await _child(gate if point == "stderr_task" else None)

    async def _send_initialize(managed: ManagedClient) -> None:
        managed.outbound_writer = _child(gate if point == "outbound_writer" else None)
        if point in _CHILD_POINTS:  # the gated child must be inside its `try`
            await asyncio.wait_for(gate.parked.wait(), _HANG_GUARD_S)
        raise RuntimeError("handshake failed")

    mgr._read_stdout = _read_stdout  # type: ignore[method-assign]
    mgr._read_stderr = _read_stderr  # type: ignore[method-assign]
    mgr._send_initialize = _send_initialize  # type: ignore[method-assign]
    spawn = patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    term = patch(
        "pmcp.client.manager._terminate_process_tree",
        _gated_call(gate if point == "terminate" else None, record, "terminated"),
    )
    kill = patch(
        "pmcp.client.manager._kill_process_tree_now",
        _recording_kill(record),
        create=True,
    )
    return spawn, term, kill


@pytest.mark.parametrize("point", _STDIO_POINTS)
async def test_stdio_handshake_teardown_keeps_caller_cancel(point: str) -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, point, gate, record)
    with spawn, term, kill:
        task = asyncio.create_task(mgr._connect_stdio(_stdio_config()))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)  # gate still closed
        finally:
            await _drain(mgr, gate)

    assert kind == "cancelled", (
        f"caller cancelled at {point} lost its cancellation: {kind} {detail!r}"
    )
    assert record == ["killed"], f"tree not killed synchronously: {record}"
    assert "srv" not in mgr._clients, f"stale client left after cancel at {point}"


_REMOTE_POINTS = ["read_task", "outbound_writer", "close_transport"]


def _remote_task(
    mgr: ClientManager,
    point: str | None,
    gate: _Gate,
    record: list[str],
    seen: list[ManagedClient] | None = None,
) -> asyncio.Task[None]:
    class _Transport:
        """`__aexit__` blocks until cancelled -- a dead peer's graceful
        close. Only an owner that is *cancelled* (not merely signalled to
        shut down) gets past it, which is what the abandon path owes."""

        async def __aenter__(self) -> tuple[Any, Any]:
            return (MagicMock(), MagicMock())

        async def __aexit__(self, *exc_info: Any) -> None:
            await asyncio.Event().wait()

    async def _read_sse(name: str, managed: ManagedClient, read_stream: Any) -> None:
        await _child(gate if point == "read_task" else None)

    async def _send_initialize(managed: ManagedClient) -> None:
        if seen is not None:
            seen.append(managed)
        managed.outbound_writer = _child(gate if point == "outbound_writer" else None)
        if point in _CHILD_POINTS:
            await asyncio.wait_for(gate.parked.wait(), _HANG_GUARD_S)
        raise RuntimeError("handshake failed")

    real_close = mgr._close_remote_transport
    gated = _gated_call(gate if point == "close_transport" else None, record, "closed")

    async def _close(name: str, managed: ManagedClient, timeout: float = 5.0) -> None:
        await gated()
        await real_close(name, managed, timeout)

    mgr._read_sse = _read_sse  # type: ignore[method-assign]
    mgr._send_initialize = _send_initialize  # type: ignore[method-assign]
    mgr._close_remote_transport = _close  # type: ignore[method-assign]
    return asyncio.create_task(
        mgr._connect_remote_stream(_remote_config(), _Transport(), transport_name="t")
    )


@pytest.mark.parametrize("point", _REMOTE_POINTS)
async def test_remote_handshake_teardown_keeps_caller_cancel(point: str) -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    seen: list[ManagedClient] = []
    task = _remote_task(mgr, point, gate, record, seen)
    try:
        await _cancel_parked(task, gate)
        kind, detail = await _outcome(task)
        owner = seen[0].transport_owner_task
        assert owner is not None
        owner_ended = await _ends_within_yields(owner)
    finally:
        await _drain(mgr, gate)

    assert kind == "cancelled", (
        f"caller cancelled at {point} lost its cancellation: {kind} {detail!r}"
    )
    assert "remote" not in mgr._clients, f"stale client left after cancel at {point}"
    assert owner_ended, "transport owner not signalled by the abandon path"


async def test_a_lost_cancel_does_not_start_another_connect_attempt() -> None:
    """`_connect_with_retry` catches `Exception`: on main the cancel came out
    as the handshake error and attempts 2 and 3 ran."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, "read_task", gate, record)
    attempts = 0
    real_connect = mgr._connect_server

    async def _counting(config: ResolvedServerConfig) -> None:
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise RuntimeError("second attempt started")
        await real_connect(config)

    mgr._connect_server = _counting  # type: ignore[method-assign]
    with spawn, term, kill, patch.object(manager_mod, "RETRY_DELAYS", [0.0] * 3):
        task = asyncio.create_task(mgr._connect_with_retry(_stdio_config()))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert (kind, attempts) == ("cancelled", 1), (
        f"cancelled connect retried: {kind} {detail!r}, attempts={attempts}"
    )


# --- the other teardowns in the class --------------------------------------------

_CLEANUP_POINTS = ["read_task", "stderr_task", "outbound_writer", "terminate"]


def _stdio_managed(gate: _Gate | None, point: str | None) -> ManagedClient:
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
    managed.config.source = "custom"
    managed.is_remote = False
    managed.process.returncode = None
    managed.read_task = _child(gate if point == "read_task" else None)
    managed.stderr_task = _child(gate if point == "stderr_task" else None)
    managed.outbound_writer = _child(gate if point == "outbound_writer" else None)
    return managed


@pytest.mark.parametrize("point", _CLEANUP_POINTS)
async def test_cleanup_client_keeps_caller_cancel(point: str) -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    managed = _stdio_managed(gate, point)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    await asyncio.sleep(0)  # let the children park before they are cancelled
    with (
        patch(
            "pmcp.client.manager._terminate_process_tree",
            _gated_call(gate if point == "terminate" else None, record, "terminated"),
        ),
        patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record),
            create=True,
        ),
    ):
        task = asyncio.create_task(mgr._cleanup_client("srv", managed))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert kind == "cancelled", f"cancel at {point} lost: {kind} {detail!r}"
    assert record == ["killed"]
    assert "srv" not in mgr._clients and "srv" not in mgr._servers


_DISCONNECT_POINTS = ["read_task", "outbound_writer", "terminate", "sweep"]


@pytest.mark.parametrize("point", _DISCONNECT_POINTS)
async def test_disconnect_server_keeps_caller_cancel(point: str) -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    managed = _stdio_managed(gate, point)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    if point == "sweep":
        # A background task scoped to the server that defers its own
        # cancellation: `gather` would hold a cancelled caller until it ends.
        mgr._track_background_task(_child(gate), "srv")
    await asyncio.sleep(0)
    with (
        patch(
            "pmcp.client.manager._terminate_process_tree",
            _gated_call(gate if point == "terminate" else None, record, "terminated"),
        ),
        patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record),
            create=True,
        ),
    ):
        task = asyncio.create_task(mgr.disconnect_server("srv", force=True))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
            locked = mgr._lifecycle_lock.locked()
        finally:
            await _drain(mgr, gate)

    assert kind == "cancelled", f"cancel at {point} lost: {kind} {detail!r}"
    # At "sweep" the close already ran gracefully; the cancel came after it.
    assert record == (["terminated", "killed"] if point == "sweep" else ["killed"])
    assert "srv" not in mgr._clients
    assert mgr._servers["srv"].status is ServerStatusEnum.LAZY
    assert not locked, "lifecycle lock held after a cancelled disconnect"


async def test_disconnect_server_does_not_cancel_its_own_caller() -> None:
    """A caller that is itself a background task scoped to the server name
    (a reconnect path) must not be swept by its own disconnect."""
    mgr = ClientManager()
    managed = _stdio_managed(None, None)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    with patch("pmcp.client.manager._terminate_process_tree", AsyncMock()):
        task = mgr._track_background_task(
            asyncio.create_task(mgr.disconnect_server("srv", force=True)), "srv"
        )
        kind, value = await _outcome(task)
    assert (kind, value) == ("returned", (True, 0, None)), f"{kind} {value!r}"


# --- round 2 of the panel (codex B2): a hung escalation must not hold the lock --


class _FinalizingTransport:
    """`__aexit__`, once cancelled, awaits a further cleanup step in its
    `finally` that does not finish until the test releases it."""

    def __init__(self) -> None:
        self.finalizer_started = asyncio.Event()
        self.release = asyncio.Event()

    async def __aenter__(self) -> tuple[Any, Any]:
        return (MagicMock(), MagicMock())

    async def __aexit__(self, *exc_info: Any) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            self.finalizer_started.set()
            await _wait_ignoring_cancels(self.release)


async def test_a_cancelled_disconnect_does_not_wait_on_a_hung_escalation() -> None:
    """Codex, round 2: the graceful wait times out, the escalation cancels
    the owner, whose `__aexit__` then blocks in a `finally`; a caller cancel
    after that must still return, drop the client and release the lock."""
    mgr = ClientManager()
    transport = _FinalizingTransport()

    async def _read_sse(name: str, managed: ManagedClient, read_stream: Any) -> None:
        await asyncio.Event().wait()

    mgr._read_sse = _read_sse  # type: ignore[method-assign]
    mgr._send_initialize = AsyncMock()  # type: ignore[method-assign]
    mgr._index_capabilities = AsyncMock(return_value=(0, 0, 0))  # type: ignore[method-assign]
    real_close = mgr._close_remote_transport

    async def _close(name: str, managed: ManagedClient, timeout: float = 5.0) -> None:
        await real_close(name, managed, 0.0)  # graceful budget spent at once

    mgr._close_remote_transport = _close  # type: ignore[method-assign]
    await asyncio.wait_for(
        mgr._connect_remote_stream(_remote_config(), transport, transport_name="t"),
        _HANG_GUARD_S,
    )
    task = asyncio.create_task(mgr.disconnect_server("remote", force=True))
    try:
        await asyncio.wait_for(transport.finalizer_started.wait(), _HANG_GUARD_S)
        task.cancel()  # once: a second cancel would also break an awaiting handler
        kind, detail = await _outcome(task)
        locked = mgr._lifecycle_lock.locked()
    finally:
        transport.release.set()
        await _drain(mgr)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert "remote" not in mgr._clients
    assert not locked, "lifecycle lock held behind a hung owner"


# --- what must NOT change -----------------------------------------------------------


async def test_child_cancellation_is_still_absorbed_without_a_caller_cancel() -> None:
    mgr = ClientManager()
    record: list[str] = []
    spawn, term, kill = _stdio_harness(mgr, None, _Gate(), record)
    with spawn, term, kill:
        with pytest.raises(RuntimeError, match="handshake failed"):
            await asyncio.wait_for(mgr._connect_stdio(_stdio_config()), _HANG_GUARD_S)
    assert record == ["terminated"], "the graceful path must still SIGTERM first"
    assert "srv" not in mgr._clients


async def test_child_cancellation_is_still_absorbed_on_disconnect() -> None:
    mgr = ClientManager()
    managed = _stdio_managed(None, None)
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    await asyncio.sleep(0)
    with patch("pmcp.client.manager._terminate_process_tree", AsyncMock()):
        ok, _cancelled, err = await asyncio.wait_for(
            mgr.disconnect_server("srv", force=True), _HANG_GUARD_S
        )
    assert (ok, err) == (True, None)


async def test_one_caller_cancel_is_delivered_exactly_once() -> None:
    """Not lost, and not doubled -- also for two cancels before it runs."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, "read_task", gate, record)
    seen: dict[str, Any] = {}

    async def caller() -> str:
        try:
            await mgr._connect_stdio(_stdio_config())
        except asyncio.CancelledError:
            current = asyncio.current_task()
            assert current is not None
            if sys.version_info >= (3, 11):
                seen["cancelling"] = current.cancelling()
            await asyncio.sleep(0)  # a second, queued cancel would land here
            return "caught once"
        return "not cancelled"

    with spawn, term, kill:
        task = asyncio.create_task(caller())
        try:
            await _cancel_parked(task, gate)
            task.cancel()  # a second request in the same iteration
            kind, value = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert (kind, value) == ("returned", "caught once")
    if sys.version_info >= (3, 11):
        assert seen["cancelling"] == 2  # both requests counted, one delivered


async def test_a_failing_terminate_still_drops_the_stale_client() -> None:
    mgr = ClientManager()
    record: list[str] = []
    spawn, _term, kill = _stdio_harness(mgr, None, _Gate(), record)
    with (
        spawn,
        kill,
        patch(
            "pmcp.client.manager._terminate_process_tree",
            AsyncMock(side_effect=OSError("kill failed")),
        ),
    ):
        with pytest.raises(OSError, match="kill failed"):
            await asyncio.wait_for(mgr._connect_stdio(_stdio_config()), _HANG_GUARD_S)
    assert "srv" not in mgr._clients


async def test_a_failing_transport_close_still_drops_the_stale_client() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    task = _remote_task(mgr, None, gate, record)
    mgr._close_remote_transport = AsyncMock(  # type: ignore[method-assign]
        side_effect=OSError("close failed")
    )
    try:
        kind, detail = await _outcome(task)
    finally:
        await _drain(mgr)
    assert kind == "raised" and isinstance(detail, OSError), f"{kind} {detail!r}"
    assert "remote" not in mgr._clients


# --- a cancel DURING the handshake -------------------------------------------------


def _parking_handshake(gate: _Gate) -> Any:
    async def _send_initialize(managed: ManagedClient) -> None:
        gate.entered.set()
        await asyncio.Event().wait()

    return _send_initialize


async def test_stdio_cancel_during_handshake_kills_without_a_graceful_teardown() -> (
    None
):
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, None, gate, record)
    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
    with spawn, term, kill:
        task = asyncio.create_task(mgr._connect_stdio(_stdio_config()))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert record == ["killed"], "SIGKILL now, no SIGTERM grace for a cancel"
    assert "srv" not in mgr._clients
    assert mgr._servers["srv"].last_error == "Connection cancelled"


async def test_remote_cancel_during_handshake_abandons_the_owner() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    task = _remote_task(mgr, None, gate, record)
    seen: list[ManagedClient] = []
    parking = _parking_handshake(gate)

    async def _send_initialize(managed: ManagedClient) -> None:
        seen.append(managed)
        await parking(managed)

    mgr._send_initialize = _send_initialize  # type: ignore[method-assign]
    try:
        await _cancel_parked(task, gate)
        kind, detail = await _outcome(task)
        owner = seen[0].transport_owner_task
        assert owner is not None
        owner_ended = await _ends_within_yields(owner)
    finally:
        await _drain(mgr)
    assert owner_ended, "transport owner not signalled by the abandon path"

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert record == [], "no graceful close for a cancelled caller"
    assert "remote" not in mgr._clients


def _adopt_task(mgr: ClientManager, gate: _Gate) -> asyncio.Task[None]:
    process = MagicMock()
    process.returncode = None
    process.stderr = None

    async def _read_stdout(name: str, managed: ManagedClient) -> None:
        await asyncio.Event().wait()

    mgr._read_stdout = _read_stdout  # type: ignore[method-assign]
    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
    return asyncio.create_task(mgr.adopt_process("srv", process, _stdio_config()))


async def test_adopt_cancel_during_handshake_kills_without_a_graceful_teardown() -> (
    None
):
    mgr = ClientManager()
    gate, record = _Gate(), []
    with (
        patch(
            "pmcp.client.manager._terminate_process_tree",
            _gated_call(None, record, "terminated"),
        ),
        patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record),
            create=True,
        ),
    ):
        task = _adopt_task(mgr, gate)
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr)

    assert kind == "cancelled", f"{kind} {detail!r}"
    assert record == ["killed"]
    assert "srv" not in mgr._clients


@pytest.mark.parametrize("site", ["stdio", "adopt"])
async def test_a_failing_kill_cannot_replace_the_cancel(
    site: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 1, B1 (P1/P1b): a teardown error raised while handling a caller
    cancel must not replace it. The synchronous path logs it by type only."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    with caplog.at_level("WARNING", logger="pmcp.client.manager"):
        with patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record, fail=True),
            create=True,
        ):
            if site == "stdio":
                spawn, term, _kill = _stdio_harness(mgr, None, gate, record)
                mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
                with spawn, term:
                    task = asyncio.create_task(mgr._connect_stdio(_stdio_config()))
                    await _cancel_parked(task, gate)
                    kind, detail = await _outcome(task)
            else:
                task = _adopt_task(mgr, gate)
                await _cancel_parked(task, gate)
                kind, detail = await _outcome(task)
        await _drain(mgr)

    assert kind == "cancelled", f"{site}: the cancel was lost: {kind} {detail!r}"
    assert "OSError" in caplog.text and "secret-ish detail" not in caplog.text
    assert "srv" not in mgr._clients


async def test_a_cancel_caught_in_the_handshake_is_not_retried() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, _kill = _stdio_harness(mgr, None, gate, record)
    mgr._send_initialize = _parking_handshake(gate)  # type: ignore[method-assign]
    attempts = 0
    real_connect = mgr._connect_server

    async def _counting(config: ResolvedServerConfig) -> None:
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise RuntimeError("second attempt started")
        await real_connect(config)

    mgr._connect_server = _counting  # type: ignore[method-assign]
    with (
        spawn,
        term,
        patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record, fail=True),
            create=True,
        ),
        patch.object(manager_mod, "RETRY_DELAYS", [0.0] * 3),
    ):
        task = asyncio.create_task(mgr._connect_with_retry(_stdio_config()))
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr)
    assert (kind, attempts) == ("cancelled", 1), f"{kind} {detail!r} {attempts}"


# --- scopes that cancel on the caller's behalf ------------------------------------


async def test_a_cancel_scope_gets_its_cancel_without_waiting_on_the_teardown() -> None:
    """anyio (the MCP SDK's request handlers) re-delivers a scope's cancel on
    every loop iteration; the round-2 deferral spun the CPU for the whole
    teardown. Now the caller leaves at once, before the parked child ends."""
    import anyio

    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, "read_task", gate, record)
    holder: dict[str, Any] = {}

    async def caller() -> bool:
        with anyio.CancelScope() as scope:
            holder["scope"] = scope
            await mgr._connect_stdio(_stdio_config())
        return scope.cancelled_caught

    with spawn, term, kill:
        task = asyncio.create_task(caller())
        try:
            await asyncio.wait_for(gate.entered.wait(), _HANG_GUARD_S)
            holder["scope"].cancel()
            kind, value = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert (kind, value) == ("returned", True)
    assert record == ["killed"]


@pytest.mark.skipif(sys.version_info < (3, 11), reason="asyncio.timeout is 3.11+")
async def test_a_timeout_scope_around_the_teardown_still_reports_timeout() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, "terminate", gate, record)
    holder: dict[str, Any] = {}

    async def caller() -> str:
        try:
            async with asyncio.timeout(None) as scope:  # type: ignore[attr-defined]
                holder["scope"] = scope
                await mgr._connect_stdio(_stdio_config())
        except TimeoutError:
            return "timeout"
        return "no timeout"

    with spawn, term, kill:
        task = asyncio.create_task(caller())
        try:
            await asyncio.wait_for(gate.entered.wait(), _HANG_GUARD_S)
            holder["scope"].reschedule(asyncio.get_running_loop().time())
            kind, value = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert (kind, value) == ("returned", "timeout")
    assert record == ["killed"] and "srv" not in mgr._clients


@pytest.mark.skipif(sys.version_info < (3, 11), reason="TaskGroup is 3.11+")
async def test_a_taskgroup_sibling_failure_kills_the_tree() -> None:
    mgr = ClientManager()
    gate, record = _Gate(), []
    spawn, term, kill = _stdio_harness(mgr, "terminate", gate, record)

    async def sibling() -> None:
        await asyncio.wait_for(gate.entered.wait(), _HANG_GUARD_S)
        raise ValueError("sibling")

    async def group() -> None:
        async with asyncio.TaskGroup() as tg:  # type: ignore[attr-defined]
            tg.create_task(mgr._connect_stdio(_stdio_config()))
            tg.create_task(sibling())

    with spawn, term, kill:
        task = asyncio.create_task(group())
        try:
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    group_type = getattr(builtins, "BaseExceptionGroup")  # 3.11+ only
    assert kind == "raised" and isinstance(detail, group_type)
    assert [type(e) for e in detail.exceptions] == [ValueError]
    assert record == ["killed"] and "srv" not in mgr._clients


async def test_disconnect_all_redelivers_a_cancel_and_kills_now() -> None:
    """`_shutdown_one` runs as a `gather` child; on cancel it now kills and
    re-raises, and the gather re-raises to disconnect_all's caller."""
    mgr = ClientManager()
    gate, record = _Gate(), []
    managed = _stdio_managed(gate, "read_task")
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    await asyncio.wait_for(gate.parked.wait(), _HANG_GUARD_S)
    with (
        patch(
            "pmcp.client.manager._terminate_process_tree",
            _gated_call(None, record, "terminated"),
        ),
        patch(
            "pmcp.client.manager._kill_process_tree_now",
            _recording_kill(record),
            create=True,
        ),
    ):
        task = asyncio.create_task(mgr.disconnect_all())
        try:
            await _cancel_parked(task, gate)
            kind, detail = await _outcome(task)
        finally:
            await _drain(mgr, gate)

    assert kind == "cancelled", f"{kind} {detail!r}"
    # The worker's handler and the parent-level fallback both kill (harmless).
    assert set(record) == {"killed"}, record


# --- real processes -------------------------------------------------------------------


async def test_a_cancelled_terminate_kills_a_sigterm_ignoring_tree() -> None:
    """`_terminate_process_tree`'s own cancel path, for its callers that do not
    abandon on their own (the update probe's timeout branch): cancelled during
    the SIGTERM grace, it SIGKILLs synchronously before re-raising."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(300)",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None
        await asyncio.wait_for(process.stdout.readline(), _HANG_GUARD_S)
        task = asyncio.create_task(manager_mod._terminate_process_tree(process, "t"))
        await asyncio.sleep(0)  # SIGTERM sent (ignored); parked in the grace wait
        await asyncio.sleep(0)
        task.cancel()
        kind, _ = await _outcome(task)
        returncode = await asyncio.wait_for(process.wait(), _HANG_GUARD_S)
    finally:
        if process.returncode is None:
            process.kill()
    assert kind == "cancelled"
    assert returncode == -signal.SIGKILL


# --- round 3 of the panel (codex): fan-out and handoff ------------------------------


async def _sigterm_ignoring_process() -> asyncio.subprocess.Process:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(300)",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    await asyncio.wait_for(process.stdout.readline(), _HANG_GUARD_S)
    return process


async def test_disconnect_all_cancelled_before_its_workers_start_kills_every_tree() -> (
    None
):
    """Codex, round 3: `call_soon(task.cancel)` right after creating the
    `disconnect_all` task cancels the gather's `_shutdown_one` workers before
    their first instruction, so no per-server handler runs. The parent-level
    fallback kills every tree anyway (real processes)."""
    mgr = ClientManager()
    processes = [await _sigterm_ignoring_process() for _ in range(2)]
    try:
        for i, process in enumerate(processes):
            status = ServerStatus(
                name=f"s{i}", status=ServerStatusEnum.ONLINE, tool_count=0
            )
            managed = ManagedClient(config=MagicMock(), process=process, status=status)
            managed.is_remote = False
            mgr._clients[f"s{i}"] = managed
            mgr._servers[f"s{i}"] = status
        task = asyncio.create_task(mgr.disconnect_all())
        asyncio.get_running_loop().call_soon(task.cancel)
        kind, detail = await _outcome(task)
        codes = [await asyncio.wait_for(p.wait(), _HANG_GUARD_S) for p in processes]
    finally:
        for p in processes:
            if p.returncode is None:
                p.kill()
    assert kind == "cancelled", f"{kind} {detail!r}"
    assert codes == [-signal.SIGKILL, -signal.SIGKILL], codes
    assert mgr._clients == {} and not mgr._lifecycle_lock.locked()


_MCP_SERVER = r"""
import json, os, signal, subprocess, sys
if sys.argv[2:] != ["default-sigterm"]:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
g = subprocess.Popen(["sleep", "300"])
open(sys.argv[1], "w").write(f"{os.getpid()} {g.pid}")
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": []}
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
    sys.stdout.flush()
"""


def _pid_alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        # gone before (or while) its stat file was read
        return False


async def _cancelled_disconnect_all_case(
    k: int, tmp_path: Path
) -> tuple[ClientManager, list[int]]:
    """Two real, connected, SIGTERM-ignoring servers -- each with a real
    reader task and a `sleep 300` grandchild -- then `disconnect_all`
    cancelled after k loop steps (k = 0 is `call_soon(task.cancel)`, before
    any worker runs)."""
    mgr = ClientManager()
    pidfiles = [tmp_path / f"k{k}-s{i}.pids" for i in range(2)]
    for i, pidfile in enumerate(pidfiles):
        cfg = ResolvedServerConfig(
            name=f"s{i}",
            source="project",
            config=LocalMcpServerConfig(
                command=sys.executable, args=["-c", _MCP_SERVER, str(pidfile)]
            ),
        )
        await asyncio.wait_for(mgr._connect_stdio(cfg), _HANG_GUARD_S)
        assert mgr._clients[f"s{i}"].read_task is not None
        assert mgr._clients[f"s{i}"].status.status == ServerStatusEnum.ONLINE
    pids = [int(p) for f in pidfiles for p in f.read_text().split()]
    task = asyncio.create_task(mgr.disconnect_all())
    if k == 0:
        asyncio.get_running_loop().call_soon(task.cancel)
    else:
        for _ in range(k):
            await asyncio.sleep(0)
        task.cancel()
    kind, detail = await _outcome(task)
    assert kind == "cancelled", f"k={k}: {kind} {detail!r}"
    return mgr, pids


async def test_a_cancelled_disconnect_all_does_not_respawn_its_servers(
    tmp_path: Path,
) -> None:
    """Claude round 4, F1: with real reader tasks, the fallback cancelled the
    readers of clients still marked ONLINE, and each reader's `finally`
    scheduled an auto-reconnect that respawned the server ~5 s later, with its
    old config. Cancel at 0, 1 and 2 loop steps, wait past the first reconnect
    delay (5 s): no reconnect task, no respawn, no live leader or grandchild."""
    spawns: list[str] = []
    real_exec = asyncio.create_subprocess_exec
    cases = [await _cancelled_disconnect_all_case(k, tmp_path) for k in (0, 1, 2)]

    async def counting_exec(*args: Any, **kwargs: Any) -> Any:
        spawns.append(str(args[:1]))
        return await real_exec(*args, **kwargs)

    try:
        with patch("asyncio.create_subprocess_exec", counting_exec):
            for _ in range(10):  # let every cancelled reader run its `finally`
                await asyncio.sleep(0)
            for k, (mgr, _pids) in zip((0, 1, 2), cases):
                assert mgr._reconnect_tasks == {}, f"k={k}: {mgr._reconnect_tasks}"
                pending = [t.get_name() for t in mgr._background_tasks if not t.done()]
                assert not [n for n in pending if n.startswith("reconnect-")], (
                    f"k={k}: {pending}"
                )
            await asyncio.sleep(5.5)  # past the first reconnect delay
        for k, (mgr, pids) in zip((0, 1, 2), cases):
            assert mgr._clients == {}, f"k={k}: {list(mgr._clients)}"
            await eventually(
                lambda pids=pids: not any(_pid_alive(p) for p in pids),
                timeout=_HANG_GUARD_S,
                message=f"k={k}: a leader or grandchild is still alive",
            )
        assert spawns == [], spawns
    finally:
        for _mgr, pids in cases:
            for p in pids:
                try:
                    os.kill(p, signal.SIGKILL)
                except ProcessLookupError:
                    pass


# --- round 6 of the panel: `wait_for` swallowing a same-turn cancel (grok) ---------


def _cancel_in_the_turn_it_finishes(
    obj: Any, attr: str, victim: list[asyncio.Task[Any]]
) -> None:
    """Wrap the coroutine method ``obj.<attr>`` so that, as the real one
    returns, the cancel of ``victim[0]`` is scheduled with `call_soon`: it
    then lands in the same loop turn as the awaited work completes, before
    the waiter resumes -- the window in which `asyncio.wait_for` on 3.10 and
    3.11 returns the result instead of raising (grok round 5)."""
    real = getattr(obj, attr)

    async def finishing_then_cancelled(*args: Any, **kwargs: Any) -> Any:
        result = await real(*args, **kwargs)
        asyncio.get_running_loop().call_soon(victim[0].cancel)
        return result

    setattr(obj, attr, finishing_then_cancelled)


async def test_bounded_wait_keeps_a_cancel_that_lands_as_the_work_finishes() -> None:
    """The helper itself: the work completes and the caller is cancelled in
    the same turn. `bounded_wait` raises the caller's `CancelledError` on
    every version; on 3.11+ the task's `cancelling()` is 1 when it is caught
    (delivered once) and an `uncancel()` -- what any scope does -- leaves a
    later `asyncio.timeout()` working. The stdlib `wait_for` is measured
    alongside as the control (it swallows on 3.10/3.11)."""

    async def work() -> str:
        return "done"

    async def caller(waiter: Any, victim: list[asyncio.Task[Any]]) -> str:
        holder = type("H", (), {})()
        holder.run = work
        _cancel_in_the_turn_it_finishes(holder, "run", victim)
        try:
            await waiter(holder.run(), 5.0)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            assert task is not None
            if sys.version_info >= (3, 11):
                assert task.cancelling() == 1
                task.uncancel()
                assert task.cancelling() == 0
                try:
                    async with asyncio.timeout(0.01):
                        await asyncio.sleep(1)
                except TimeoutError:
                    return "cancelled; a later timeout still reports TimeoutError"
                return "cancelled; a later timeout was poisoned"
            return "cancelled"
        return "swallowed"

    victim: list[asyncio.Task[Any]] = []
    task = asyncio.create_task(caller(manager_mod.bounded_wait, victim))
    victim.append(task)
    outcome = await asyncio.wait_for(task, _HANG_GUARD_S)
    if sys.version_info >= (3, 11):
        assert outcome == "cancelled; a later timeout still reports TimeoutError"
    else:
        assert outcome == "cancelled"

    control: list[asyncio.Task[Any]] = []
    ctask = asyncio.create_task(caller(asyncio.wait_for, control))
    control.append(ctask)
    try:
        control_outcome = await asyncio.wait_for(ctask, _HANG_GUARD_S)
    except asyncio.CancelledError:
        control_outcome = "cancelled late"
    if sys.version_info < (3, 12):
        assert control_outcome in ("swallowed", "cancelled late"), control_outcome


async def test_a_cancel_during_the_post_timeout_wait_retrieves_the_outcome() -> None:
    """Claude round 6, N1: the caller is cancelled while `bounded_wait` waits
    for the timed-out work to unwind, and the work then raises. Its outcome
    is retrieved, so the loop reports no "Task exception was never
    retrieved" when the task is collected (checked through the task's own
    pending-report flag, `_log_traceback`, which is what its `__del__`
    reports from -- deterministic, unlike waiting for a collection)."""
    unwinding = asyncio.Event()
    release = asyncio.Event()
    work_tasks: list[asyncio.Task[Any]] = []

    async def work() -> None:
        current = asyncio.current_task()
        assert current is not None
        work_tasks.append(current)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            unwinding.set()
            await release.wait()
            raise ValueError("failed while unwinding") from None

    async def caller() -> None:
        await manager_mod.bounded_wait(work(), timeout=0.01)

    task = asyncio.create_task(caller())
    await asyncio.wait_for(unwinding.wait(), _HANG_GUARD_S)
    task.cancel()
    kind, _ = await _outcome(task)
    assert kind == "cancelled"
    release.set()
    (work_task,) = work_tasks
    await asyncio.wait({work_task}, timeout=_HANG_GUARD_S)
    for _ in range(3):
        await asyncio.sleep(0)  # let the done-callbacks run
    assert work_task.done() and not work_task.cancelled()
    # Read the flag BEFORE anything here calls `exception()`, which would
    # retrieve the outcome itself.
    assert work_task._log_traceback is False  # type: ignore[attr-defined]
    assert isinstance(work_task.exception(), ValueError)


def test_no_wait_for_in_src_pmcp() -> None:
    """The class (grok round 5): every bounded wait in `src/pmcp` goes through
    `pmcp.waits.bounded_wait`. No `wait_for` call -- `asyncio.wait_for`, a
    bare `wait_for`, or one reached through any alias -- and no import of
    the name. No exemptions."""
    found = []
    for path in sorted(_PKG.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "wait_for":
                found.append(f"{path.relative_to(_PKG)}:{node.lineno}")
            if isinstance(node, ast.Name) and node.id == "wait_for":
                found.append(f"{path.relative_to(_PKG)}:{node.lineno}")
            if isinstance(node, ast.ImportFrom) and any(
                a.name == "wait_for" for a in node.names
            ):
                found.append(f"{path.relative_to(_PKG)}:{node.lineno}")
    assert found == [], found


async def _real_server(
    tmp_path: Path, name: str, *extra: str
) -> tuple[ClientManager, ResolvedServerConfig, list[int]]:
    mgr = ClientManager()
    pidfile = tmp_path / f"{name}.pids"
    cfg = ResolvedServerConfig(
        name=name,
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable,
            args=["-c", _MCP_SERVER, str(pidfile), *extra],
        ),
    )
    await asyncio.wait_for(mgr._connect_stdio(cfg), _HANG_GUARD_S)
    return mgr, cfg, [int(p) for p in pidfile.read_text().split()]


def _kill_pids(pids: list[int]) -> None:
    for p in pids:
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def test_a_cancel_as_the_leader_exits_is_not_swallowed_by_terminate(
    tmp_path: Path,
) -> None:
    """Grok round 5, `_terminate_process_tree`: a real child exits on SIGTERM
    and the caller is cancelled in the same turn. The cancel propagates (on
    3.10/3.11 `wait_for` returned normally), the tree is dead."""
    mgr, _cfg, pids = await _real_server(tmp_path, "srv", "default-sigterm")
    managed = mgr._clients["srv"]
    victim: list[asyncio.Task[Any]] = []
    try:
        _cancel_in_the_turn_it_finishes(managed.process, "wait", victim)
        task = asyncio.create_task(
            manager_mod._terminate_process_tree(
                managed.process, "srv", group_pgid=managed.group_pgid
            )
        )
        victim.append(task)
        kind, detail = await _outcome(task)
        assert kind == "cancelled", f"{kind} {detail!r}"
        await eventually(
            lambda: not any(_pid_alive(p) for p in pids), timeout=_HANG_GUARD_S
        )
    finally:
        _kill_pids(pids)
        for t in list(mgr._background_tasks):
            t.cancel()


async def test_a_restart_cancelled_as_the_old_server_exits_spawns_nothing(
    tmp_path: Path,
) -> None:
    """Grok round 5, `restart_server`: the old server exits on SIGTERM and
    the restart is cancelled in that same turn. On 3.10/3.11 `wait_for`
    swallowed the cancel, `disconnect_server` returned normally and the
    restart spawned a replacement. Now: `CancelledError`, nothing spawned,
    no client registered, the old tree dead."""
    mgr, cfg, pids = await _real_server(tmp_path, "srv", "default-sigterm")
    managed = mgr._clients["srv"]
    spawns: list[str] = []
    real_exec = asyncio.create_subprocess_exec

    async def counting_exec(*args: Any, **kwargs: Any) -> Any:
        spawns.append("spawn")
        return await real_exec(*args, **kwargs)

    victim: list[asyncio.Task[Any]] = []
    try:
        _cancel_in_the_turn_it_finishes(managed.process, "wait", victim)
        with patch("asyncio.create_subprocess_exec", counting_exec):
            task = asyncio.create_task(mgr.restart_server(cfg, force=True))
            victim.append(task)
            kind, detail = await _outcome(task)
            for _ in range(10):
                await asyncio.sleep(0)
        assert kind == "cancelled", f"{kind} {detail!r}"
        assert spawns == [], spawns
        assert "srv" not in mgr._clients
        await eventually(
            lambda: not any(_pid_alive(p) for p in pids), timeout=_HANG_GUARD_S
        )
    finally:
        _kill_pids(pids)
        for t in list(mgr._background_tasks):
            t.cancel()


async def test_an_update_probe_cancelled_as_it_finishes_is_cancelled(
    tmp_path: Path,
) -> None:
    """Grok round 5, the update probe: `communicate()` completes and the
    caller is cancelled in the same turn. On 3.10/3.11 `wait_for` returned
    the probe result and skipped the kill. Now the cancel propagates."""
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools

    gt = GatewayTools(client_manager=MagicMock(), policy_manager=PolicyManager())
    victim: list[asyncio.Task[Any]] = []
    real_exec = asyncio.create_subprocess_exec

    async def hooked_exec(*args: Any, **kwargs: Any) -> Any:
        process = await real_exec(*args, **kwargs)
        _cancel_in_the_turn_it_finishes(process, "communicate", victim)
        return process

    with patch("asyncio.create_subprocess_exec", hooked_exec):
        task = asyncio.create_task(
            gt._run_update_probe_command([sys.executable, "-c", "print('ok')"])
        )
        victim.append(task)
        kind, detail = await _outcome(task)
    assert kind == "cancelled", f"{kind} {detail!r}"


# --- round 6 of the panel: a grandchild that outlives its reaped leader (codex) --


_EXITING_LEADER = (
    "import os, subprocess, sys\n"
    "g = subprocess.Popen(['sleep', '300'])\n"
    "print(g.pid, flush=True)\n"
    "sys.stdin.readline()\n"  # stay alive until the client has been created
)


async def _reaped_leader_with_grandchild() -> tuple[ManagedClient, int]:
    """A session leader (as pmcp spawns stdio servers) that starts `sleep
    300` -- inheriting its pipes -- and then exits 0. The `ManagedClient` is
    created while the leader lives, as `_connect_stdio` creates it; then the
    leader exits and asyncio reaps it."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _EXITING_LEADER,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stdin is not None
    grandchild = int(
        (await asyncio.wait_for(process.stdout.readline(), _HANG_GUARD_S)).strip()
    )
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    # As `_connect_stdio` does (round 7): the group id from the spawn
    # contract, not a lookup.
    managed = ManagedClient(
        config=MagicMock(), process=process, status=status, group_pgid=process.pid
    )
    managed.is_remote = False
    assert os.getpgid(process.pid) == process.pid  # start_new_session: pgid == pid
    process.stdin.write(b"exit\n")
    # Not `process.wait()`: on 3.11+ it also waits for the pipes to close,
    # and the grandchild holds them. The child watcher sets `returncode`
    # when it reaps the leader.
    await eventually(lambda: process.returncode is not None, timeout=_HANG_GUARD_S)
    assert process.returncode == 0 and _pid_alive(grandchild)
    return managed, grandchild


@pytest.mark.parametrize(
    "how",
    ["disconnect_all_call_soon", "disconnect_server_cancelled", "disconnect_server"],
)
async def test_a_grandchild_of_a_reaped_leader_is_killed(how: str) -> None:
    """Codex round 5: once asyncio has reaped the leader, `getpgid(pid)` no
    longer answers, so no kill path reached the group and the grandchild
    survived every teardown (pre-existing on main). The group id is now
    retained when the client is created and used by every kill path; the
    leader's pid is never signalled."""
    managed, grandchild = await _reaped_leader_with_grandchild()
    mgr = ClientManager()
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = managed.status
    try:
        if how == "disconnect_all_call_soon":
            task = asyncio.create_task(mgr.disconnect_all())
            asyncio.get_running_loop().call_soon(task.cancel)
            kind, detail = await _outcome(task)
            assert kind == "cancelled", f"{kind} {detail!r}"
        elif how == "disconnect_server_cancelled":
            task = asyncio.create_task(mgr.disconnect_server("srv", force=True))
            for _ in range(2):
                await asyncio.sleep(0)
            task.cancel()
            kind, detail = await _outcome(task)
            assert kind in ("cancelled", "returned"), f"{kind} {detail!r}"
        else:
            await asyncio.wait_for(
                mgr.disconnect_server("srv", force=True), _HANG_GUARD_S
            )
        await eventually(
            lambda: not _pid_alive(grandchild),
            timeout=_HANG_GUARD_S,
            message=f"{how}: the grandchild {grandchild} survived",
        )
    finally:
        _kill_pids([grandchild])


_FAST_EXIT = "sleep 300 & echo $! > {pidfile}"


async def test_fast_exiting_leaders_never_leave_a_grandchild(tmp_path: Path) -> None:
    """Codex round 6: a leader that exits at once (`/bin/sh -c 'sleep 300 &
    ...'`) can be reaped before `create_subprocess_exec` returns, so a group
    id looked up afterwards is `None` and the grandchild survives (codex:
    40/40 on every Python). The group id now comes from the spawn contract.
    Five rounds of eight concurrent connects through the real
    `_connect_stdio`, each handshake failing at once: every grandchild dies."""
    mgr = ClientManager()

    async def failing_handshake(managed: ManagedClient) -> None:
        # Fail once the leader has started its grandchild and recorded it:
        # a kill before `echo` ran would leave nothing to check. The leader
        # exits right after, usually before this returns.
        assert managed.config is not None
        pidfile = tmp_path / f"{managed.config.name}.pid"
        while not (pidfile.exists() and pidfile.read_text().strip()):
            await asyncio.sleep(0.005)
        raise RuntimeError("handshake failed")

    mgr._send_initialize = failing_handshake  # type: ignore[method-assign]
    pidfiles: list[Path] = []
    try:
        for round_ in range(5):
            cfgs = []
            for i in range(8):
                pidfile = tmp_path / f"s{round_}-{i}.pid"
                pidfiles.append(pidfile)
                cfgs.append(
                    ResolvedServerConfig(
                        name=f"s{round_}-{i}",
                        source="project",
                        config=LocalMcpServerConfig(
                            command="/bin/sh",
                            args=["-c", _FAST_EXIT.format(pidfile=pidfile)],
                        ),
                    )
                )
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(mgr._connect_stdio(c) for c in cfgs), return_exceptions=True
                ),
                _HANG_GUARD_S,
            )
            assert all(isinstance(r, RuntimeError) for r in results), results
        await eventually(
            lambda: all(p.exists() and p.read_text().strip() for p in pidfiles),
            timeout=_HANG_GUARD_S,
        )
        grandchildren = [int(p.read_text()) for p in pidfiles]
        await eventually(
            lambda: not [g for g in grandchildren if _pid_alive(g)],
            timeout=_HANG_GUARD_S,
            message="a grandchild of a fast-exiting leader survived",
        )
        assert mgr._clients == {}
    finally:
        _kill_pids(
            [
                int(p.read_text())
                for p in pidfiles
                if p.exists() and p.read_text().strip()
            ]
        )
        for t in list(mgr._background_tasks):
            t.cancel()


async def test_a_fast_exiting_update_probe_never_leaves_a_grandchild(
    tmp_path: Path,
) -> None:
    """The same spawn contract for the update probe: 24 concurrent probes
    whose leaders exit at once while a grandchild holds their pipes time
    out, and each timeout's terminate kills the grandchild through the group
    id taken at spawn."""
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools import handlers as handlers_mod
    from pmcp.tools.handlers import GatewayTools

    gt = GatewayTools(client_manager=MagicMock(), policy_manager=PolicyManager())
    pidfiles = [tmp_path / f"probe{i}.pid" for i in range(24)]
    real_wait = handlers_mod.bounded_wait

    async def short(aw: Any, timeout: float | None) -> Any:
        return await real_wait(aw, timeout=0.5)

    try:
        with patch.object(handlers_mod, "bounded_wait", short):
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        gt._run_update_probe_command(
                            ["/bin/sh", "-c", _FAST_EXIT.format(pidfile=p)]
                        )
                        for p in pidfiles
                    ),
                    return_exceptions=True,
                ),
                _HANG_GUARD_S,
            )
        assert all(isinstance(r, TimeoutError) for r in results), results
        await eventually(
            lambda: all(p.exists() and p.read_text().strip() for p in pidfiles)
        )
        grandchildren = [int(p.read_text()) for p in pidfiles]
        await eventually(
            lambda: not [g for g in grandchildren if _pid_alive(g)],
            timeout=_HANG_GUARD_S,
            message="a probe's grandchild survived",
        )
    finally:
        _kill_pids(
            [
                int(p.read_text())
                for p in pidfiles
                if p.exists() and p.read_text().strip()
            ]
        )


_GATEWAY_SHUTDOWN_SCRIPT = r"""
import asyncio, sys
from pmcp.client.manager import ClientManager
from pmcp.server import GatewayServer
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

how, server_script, pidfile = sys.argv[1], sys.argv[2], sys.argv[3]
state = {}

async def main():
    server = GatewayServer()
    mgr = server._client_manager
    state["mgr"] = mgr
    cfg = ResolvedServerConfig(
        name="srv", source="project",
        config=LocalMcpServerConfig(command=sys.executable, args=["-c", server_script, pidfile]),
    )
    await asyncio.wait_for(mgr._connect_stdio(cfg), 20)
    task = asyncio.ensure_future(server.shutdown())
    await asyncio.sleep(0)  # shutdown has started; its disconnect_all task has not
    if how == "stop-loop":
        # codex round 6: stop the loop while main is still suspended; then
        # asyncio.run cancels every task, the disconnect_all task before its
        # first instruction
        asyncio.get_running_loop().stop()
        await asyncio.sleep(3600)
    # "main-returns": asyncio.run cancels the rest
    state["task"] = task

try:
    asyncio.run(main())
except RuntimeError as exc:  # "Event loop stopped before Future completed."
    print(f"loop-stopped: {exc}", flush=True)
print(f"shutdown-complete clients={sorted(state['mgr']._clients)}", flush=True)
"""


@pytest.mark.parametrize("how", ["stop-loop", "main-returns"])
def test_gateway_shutdown_cancelled_before_disconnect_all_starts_kills_the_tree(
    how: str, tmp_path: Path
) -> None:
    """Codex round 6, through the real `GatewayServer.shutdown()`: it bounds
    `disconnect_all()` with `bounded_wait`, which runs it as its own task;
    loop shutdown can cancel that task before its first instruction, so
    neither its workers' handlers nor its fallback run (round 6: the server
    stayed alive and registered on every Python). `shutdown`'s own handler
    now calls `abandon_all_now()` and re-raises. A real SIGTERM-ignoring
    server with a grandchild; the tree must be dead and `_clients` empty."""
    pidfile = tmp_path / "pids"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _GATEWAY_SHUTDOWN_SCRIPT,
            how,
            _MCP_SERVER,
            str(pidfile),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )
    assert "shutdown-complete" in result.stdout, result.stderr[-2000:]
    pids = [int(x) for x in pidfile.read_text().split()]
    try:
        eventually_sync(
            lambda: not any(_pid_alive(p) for p in pids),
            timeout=5.0,
            interval=0.05,
            message=f"{how}: the tree outlived shutdown: {result.stdout.strip()}",
        )
    finally:
        _kill_pids(pids)
    assert "clients=[]" in result.stdout, result.stdout


async def test_without_process_groups_every_kill_path_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex round 7, Windows: without `os.killpg`/`os.getpgid` a retained
    group id must never reach a group call. With the APIs removed (as
    `tests/test_client_manager.py`'s Windows fallback test does) and an id
    passed explicitly through every kill path: no `AttributeError`, the
    single-process fallback runs, `disconnect_server` succeeds and drops the
    client, and the spawn contract records no id."""
    monkeypatch.delattr(manager_mod.os, "killpg", raising=False)
    monkeypatch.delattr(manager_mod.os, "getpgid", raising=False)

    def fake_process(returncode: int | None = None) -> MagicMock:
        process = MagicMock()
        process.pid = 4321
        process.returncode = returncode
        process.wait = AsyncMock(return_value=0)
        return process

    assert manager_mod._spawned_group_pgid(fake_process()) is None
    assert manager_mod._own_group_pgid(fake_process()) is None

    alive = fake_process()
    manager_mod._kill_process_tree_now(alive, group_pgid=4321)
    alive.kill.assert_called_once()
    manager_mod._kill_process_tree_now(fake_process(returncode=0), group_pgid=4321)
    manager_mod._kill_retained_group(4321)

    graceful = fake_process()
    await manager_mod._terminate_process_tree(graceful, "srv", group_pgid=4321)
    graceful.terminate.assert_called_once()
    await manager_mod._terminate_process_tree(
        fake_process(returncode=0), "srv", group_pgid=4321
    )

    mgr = ClientManager()
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(
        config=MagicMock(), process=fake_process(), status=status, group_pgid=4321
    )
    managed.is_remote = False
    mgr._clients["srv"] = managed
    mgr._servers["srv"] = status
    ok, _cancelled, error = await mgr.disconnect_server("srv", force=True)
    assert ok and error is None and "srv" not in mgr._clients

    abandoned = fake_process()
    mgr._clients["other"] = ManagedClient(
        config=MagicMock(), process=abandoned, status=status, group_pgid=4321
    )
    mgr.abandon_all_now()
    abandoned.kill.assert_called_once()
    assert mgr._clients == {}

    from pmcp.policy.policy import PolicyManager
    from pmcp.tools import handlers as handlers_mod
    from pmcp.tools.handlers import GatewayTools

    gt = GatewayTools(client_manager=MagicMock(), policy_manager=PolicyManager())
    for outcome in ("timeout", "cancel"):
        probe = fake_process()

        async def fake_exec(*args: Any, **kwargs: Any) -> MagicMock:
            return probe

        async def ending(aw: Any, timeout: float | None) -> Any:
            aw.close()
            if outcome == "timeout":
                raise asyncio.TimeoutError
            raise asyncio.CancelledError

        with (
            patch("asyncio.create_subprocess_exec", fake_exec),
            patch.object(handlers_mod, "bounded_wait", ending),
        ):
            expected = TimeoutError if outcome == "timeout" else asyncio.CancelledError
            with pytest.raises(expected):
                await gt._run_update_probe_command(["probe", "--help"])
        if outcome == "timeout":
            probe.terminate.assert_called_once()
        else:
            probe.kill.assert_called_once()


# --- implementation additions (round-8 panel, codex F025-F033) -------------------


@pytest.mark.parametrize(
    "how", ["shutdown_lock_held", "shutdown_disconnect_stuck", "cancel_disconnect_all"]
)
async def test_a_teardown_waiting_for_the_lifecycle_lock_still_kills_the_tree(
    how: str, tmp_path: Path
) -> None:
    """Another operation holds `_lifecycle_lock`. (a) The real
    `GatewayServer.shutdown()` with its budget (shortened to 0.5 s here; the
    production 10 s behaves the same): on main it logged the timeout and
    returned with the SIGTERM-ignoring server, its grandchild, and
    `_clients == ['srv']`. (b) The budget runs out while `disconnect_all`
    itself is stuck (a stub that just waits): shutdown's own `TimeoutError`
    handler must abandon. (c) `disconnect_all()` cancelled while waiting for
    the lock. All now abandon synchronously: tree dead, `_clients` empty."""
    from pmcp import server as server_mod
    from pmcp.server import GatewayServer

    gateway = GatewayServer()
    mgr = gateway._client_manager
    pidfile = tmp_path / "srv.pids"
    cfg = ResolvedServerConfig(
        name="srv",
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable, args=["-c", _MCP_SERVER, str(pidfile)]
        ),
    )
    await asyncio.wait_for(mgr._connect_stdio(cfg), _HANG_GUARD_S)
    pids = [int(p) for p in pidfile.read_text().split()]
    holder_release = asyncio.Event()
    holding = asyncio.Event()

    async def hold_the_lock() -> None:
        async with mgr._lifecycle_lock:
            holding.set()
            await holder_release.wait()

    holder = asyncio.create_task(hold_the_lock())
    try:
        await asyncio.wait_for(holding.wait(), _HANG_GUARD_S)
        if how.startswith("shutdown"):
            real_wait = server_mod.bounded_wait

            async def short_budget(aw: Any, timeout: float | None) -> Any:
                return await real_wait(aw, timeout=0.5)

            async def stuck() -> None:
                await asyncio.sleep(3600)

            with (
                patch.object(server_mod, "bounded_wait", short_budget),
                patch.object(server_mod, "release_singleton_lock", lambda: None),
            ):
                if how == "shutdown_disconnect_stuck":
                    holder_release.set()  # the lock is not the obstacle here
                    with patch.object(mgr, "disconnect_all", stuck):
                        await asyncio.wait_for(gateway.shutdown(), _HANG_GUARD_S)
                else:
                    await asyncio.wait_for(gateway.shutdown(), _HANG_GUARD_S)
        else:
            task = asyncio.create_task(mgr.disconnect_all())
            for _ in range(3):
                await asyncio.sleep(0)
            task.cancel()
            kind, detail = await _outcome(task)
            assert kind == "cancelled", f"{kind} {detail!r}"
        assert mgr._clients == {}, list(mgr._clients)
        await eventually(
            lambda: not any(_pid_alive(p) for p in pids),
            timeout=_HANG_GUARD_S,
            message=f"{how}: the tree survived",
        )
    finally:
        holder_release.set()
        await asyncio.wait({holder}, timeout=_HANG_GUARD_S)
        _kill_pids(pids)
        for t in list(mgr._background_tasks):
            t.cancel()


_TASK_SERVER = r"""
import json, os, signal, subprocess, sys
signal.signal(signal.SIGTERM, signal.SIG_IGN)
g = subprocess.Popen(["sleep", "300"])
open(sys.argv[1], "w").write(f"{os.getpid()} {g.pid}")
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "tasks/cancel":
        open(sys.argv[1] + ".cancel", "w").write("seen")
        continue  # withhold the reply
    if method == "initialize":
        result = {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}, "tasks": {"cancel": {}}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": []}
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
    sys.stdout.flush()
"""


async def test_a_forced_disconnect_cancelled_during_its_task_cancel_kills_the_tree(
    tmp_path: Path,
) -> None:
    """`disconnect_server(force=True)` with an active MCP task sends
    `tasks/cancel` and awaits the reply before it takes the lock. The server
    withholds the reply and the caller is cancelled: on main the caller got
    `CancelledError` while the leader and grandchild survived and the client
    stayed ONLINE. Now the pre-lock await is inside the protected teardown:
    tree dead, client gone."""
    mgr = ClientManager()
    pidfile = tmp_path / "srv.pids"
    cfg = ResolvedServerConfig(
        name="srv",
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable, args=["-c", _TASK_SERVER, str(pidfile)]
        ),
    )
    await asyncio.wait_for(mgr._connect_stdio(cfg), _HANG_GUARD_S)
    pids = [int(p) for p in pidfile.read_text().split()]
    active = MagicMock()
    active.task_id = "task-1"
    seen = tmp_path / "srv.pids.cancel"
    try:
        record = MagicMock(requestor_context=None)
        with (
            patch.object(mgr, "get_active_tasks", lambda name: [active]),
            patch.object(mgr, "get_task_record", lambda server, task_id: record),
            patch.object(mgr, "_terminal_task", lambda rec: False),
        ):
            task = asyncio.create_task(mgr.disconnect_server("srv", force=True))
            await eventually(seen.exists, timeout=_HANG_GUARD_S)
            for _ in range(3):
                await asyncio.sleep(0)
            assert not task.done()  # parked on the withheld reply
            task.cancel()
            kind, detail = await _outcome(task)
        assert kind == "cancelled", f"{kind} {detail!r}"
        assert "srv" not in mgr._clients
        await eventually(
            lambda: not any(_pid_alive(p) for p in pids),
            timeout=_HANG_GUARD_S,
            message="the tree survived the cancelled forced disconnect",
        )
    finally:
        _kill_pids(pids)
        for t in list(mgr._background_tasks):
            t.cancel()


def test_a_reused_group_id_is_not_signalled() -> None:
    """The guard on the retained id: when a live process holds the leader's
    pid, the id was reused after our group emptied, and nothing is sent."""
    sent: list[tuple[int, int]] = []

    def fake_killpg(pgid: int, sig: int) -> None:
        sent.append((pgid, sig))

    with (
        patch.object(manager_mod.os, "killpg", fake_killpg),
        patch.object(manager_mod.os, "kill", lambda pid, sig: None),
    ):
        manager_mod._kill_retained_group(4242)
    assert sent == [(4242, 0)], sent


async def test_connect_all_cancelled_before_its_workers_start_spawns_nothing(
    tmp_path: Path,
) -> None:
    """The other fan-out that owns processes. A connect worker cancelled
    before its first instruction has spawned nothing, and one cancelled at
    any later await runs its own handler (asyncio's own subprocess creation
    kills a half-made process if cancelled), so connect_all needs no
    parent-level kill -- and must not tear down servers that did connect."""
    mgr = ClientManager()
    pidfile = tmp_path / "pid"
    config = ResolvedServerConfig(
        name="srv",
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable,
            args=["-c", f"open({str(pidfile)!r}, 'w').write('x')"],
        ),
    )
    task = asyncio.create_task(mgr.connect_all([config], retry=False))
    asyncio.get_running_loop().call_soon(task.cancel)
    kind, detail = await _outcome(task)
    for _ in range(20):
        await asyncio.sleep(0)
    assert kind == "cancelled", f"{kind} {detail!r}"
    assert mgr._clients == {} and not pidfile.exists()


async def test_a_cancel_at_remote_handoff_abandons_the_owner() -> None:
    """Codex, round 3: the caller is cancelled while the transport is
    being entered. The owner publishes `ready`, parks on `shutdown.wait()`,
    and a single immediate cancel is consumed there -- its `__aexit__` then
    blocks uncancelled. `_abandon_owner` re-cancels until it ends."""
    mgr = ClientManager()
    holder: dict[str, Any] = {}
    exit_cancelled: list[bool] = []

    class _Transport:
        async def __aenter__(self) -> tuple[Any, Any]:
            asyncio.get_running_loop().call_soon(holder["connect"].cancel)
            return (MagicMock(), MagicMock())

        async def __aexit__(self, *exc_info: Any) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                exit_cancelled.append(True)
                raise

    owners: list[asyncio.Task[Any]] = []
    real_own = mgr._own_remote_transport

    async def _own(*args: Any) -> None:
        task = asyncio.current_task()
        assert task is not None
        owners.append(task)
        await real_own(*args)

    mgr._own_remote_transport = _own  # type: ignore[method-assign]
    task = asyncio.create_task(
        mgr._connect_remote_stream(_remote_config(), _Transport(), transport_name="t")
    )
    holder["connect"] = task
    try:
        kind, detail = await _outcome(task)
        owner_ended = await _ends_within_yields(owners[0])
    finally:
        await _drain(mgr)
    assert kind == "cancelled", f"{kind} {detail!r}"
    assert owner_ended and exit_cancelled == [True], "owner left in its __aexit__"
    assert "remote" not in mgr._clients


def test_an_owner_is_cancelled_only_through_abandon_owner() -> None:
    """Every cancel of a remote transport owner on a cancel path goes through
    `_abandon_owner` (a single immediate cancel is consumed before the
    owner's `__aexit__`). The graceful escalation in `_close_remote_transport`
    cancels its local `task` and then waits for it, on the uncancelled path."""
    tree = dict(_modules())["client/manager.py"]
    bad: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name == "_abandon_owner":
            continue
        for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
            if _callee(call) == "_cancel_without_waiting" and any(
                "owner" in ast.unparse(a) for a in call.args
            ):
                bad.append(f"{fn.name}: {ast.unparse(call)}")
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "cancel"
                and "owner" in ast.unparse(call.func.value)
            ):
                bad.append(f"{fn.name}: {ast.unparse(call)}")
    assert bad == [], bad


# --- real processes: loop shutdown in every phase ----------------------------------

_SHUTDOWN_SCRIPT = r"""
import asyncio, os, sys, time
from pmcp.client import manager as m
from pmcp.client.manager import ClientManager
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

phase, pidfile = sys.argv[1], sys.argv[2]
child = (
    "import os, signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "g = subprocess.Popen(['sleep', '300'])\n"
    "open(sys.argv[1], 'w').write(f'{os.getpid()} {g.pid}')\n"
    "time.sleep(300)\n"
)
state = {"terminate_entered": False}

async def main():
    parked = asyncio.Event()
    failed = asyncio.Event()
    release = asyncio.Event()
    real = m._terminate_process_tree

    async def term(process, name, **kwargs):
        state["terminate_entered"] = True
        parked.set()
        await real(process, name, **kwargs)

    m._terminate_process_tree = term
    mgr = ClientManager()
    state["mgr"] = mgr

    async def handshake(managed):
        while not (os.path.exists(pidfile) and open(pidfile).read().strip()):
            await asyncio.sleep(0.01)
        if phase == "mid-handshake":
            parked.set()
            await asyncio.Event().wait()
        await release.wait()
        failed.set()
        raise RuntimeError("handshake failed")

    mgr._send_initialize = handshake
    cfg = ResolvedServerConfig(
        name="srv", source="project",
        config=LocalMcpServerConfig(command=sys.executable, args=["-c", child, pidfile]),
    )
    asyncio.ensure_future(mgr._connect_stdio(cfg))
    if phase == "just-failed":
        # codex, round 2: let the handshake fail, then stop the loop at once,
        # so shutdown cancels every task -- in round 2 that included a
        # teardown task scheduled but not yet started
        while not (os.path.exists(pidfile) and open(pidfile).read().strip()):
            await asyncio.sleep(0.01)
        release.set()
        await failed.wait()
        asyncio.get_running_loop().stop()
        return
    if phase == "mid-terminate":
        release.set()
    await asyncio.wait_for(parked.wait(), 20)
    for _ in range(3):
        await asyncio.sleep(0)
    # main returns: asyncio.run cancels every task still pending

t0 = time.monotonic()
try:
    asyncio.run(main())
except RuntimeError as exc:  # "Event loop stopped before Future completed."
    print(f"loop-stopped: {exc}", flush=True)
print(f"shutdown-complete elapsed={time.monotonic() - t0:.3f} "
      f"terminate_entered={state['terminate_entered']} "
      f"clients={sorted(state['mgr']._clients)}", flush=True)
"""


@pytest.mark.parametrize("phase", ["mid-handshake", "just-failed", "mid-terminate"])
def test_loop_shutdown_kills_the_process_tree(phase: str, tmp_path: Path) -> None:
    """A real SIGTERM-ignoring server with a grandchild, and `asyncio.run`
    returning while the connect is (a) parked in the handshake, (b) has just
    failed its handshake (codex round 2: the round-2 private teardown task was
    cancelled before its first instruction), (c) parked in the graceful
    terminate (codex round 1). The tree must be dead and `_clients` empty."""
    pidfile = tmp_path / "pids"
    result = subprocess.run(
        [sys.executable, "-c", _SHUTDOWN_SCRIPT, phase, str(pidfile)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "shutdown-complete" in result.stdout, result.stderr[-2000:]
    leader, grandchild = (int(x) for x in pidfile.read_text().split())

    def gone(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    try:
        eventually_sync(
            lambda: gone(leader) and gone(grandchild),
            timeout=5.0,
            interval=0.05,
            message=f"{phase}: tree outlived shutdown: {result.stdout.strip()}",
        )
    finally:
        for pid in (leader, grandchild):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    assert "clients=[]" in result.stdout, result.stdout
    if phase == "mid-handshake":
        # claude N2: a cancel mid-handshake is a synchronous SIGKILL, not the
        # 5-11 s graceful terminate the round-2 private task ran.
        assert "terminate_entered=False" in result.stdout, result.stdout


# --- the class: every handler that catches a cancellation is classified --------

# (module, enclosing function, "reraises" | "absorbs") -> (count, why).
_CANCEL_HANDLERS: dict[tuple[str, str, str], tuple[int, str]] = {
    ("cli.py", "run_server", "absorbs"): (1, "task root: logs, then the process exits"),
    ("waits.py", "bounded_wait", "reraises"): (
        2,
        "round 6: cancels the work without waiting, then re-raises the caller's "
        "cancel; round 7 (N1): a cancel during the post-timeout wait retrieves "
        "the work's outcome later, then re-raises",
    ),
    ("server.py", "GatewayServer.shutdown", "reraises"): (
        1,
        "round 7: `abandon_all_now()` synchronously (the `disconnect_all` task "
        "may have been cancelled before it started), then re-raises",
    ),
    ("client/manager.py", "_terminate_process_tree", "reraises"): (
        1,
        "SIGKILLs the tree synchronously, then re-raises",
    ),
    ("client/manager.py", "ClientManager.disconnect_server", "reraises"): (
        1,
        "implementation addition: a cancel during the forced `cancel_task` "
        "awaits or the lock wait abandons synchronously, then re-raises",
    ),
    ("client/manager.py", "ClientManager._disconnect_server_locked", "reraises"): (
        1,
        "abandons synchronously, then re-raises (the plan's handler, moved "
        "with the locked body)",
    ),
    ("client/manager.py", "ClientManager.disconnect_all", "reraises"): (
        1,
        "implementation addition: a cancel during the lock wait calls "
        "`abandon_all_now()`, then re-raises",
    ),
    ("client/manager.py", "ClientManager._connect_stdio", "reraises"): (
        2,
        "cancel during the handshake; cancel during the graceful teardown",
    ),
    ("client/manager.py", "ClientManager._connect_remote_stream", "reraises"): (
        3,
        "pre-handoff BaseException; the two handshake handlers",
    ),
    ("client/manager.py", "ClientManager._close_remote_transport", "reraises"): (
        1,
        "escalates to the owner without waiting, then re-raises",
    ),
    ("client/manager.py", "ClientManager._disconnect_all_unlocked", "reraises"): (
        1,
        "parent-level fallback: abandons every client, then re-raises",
    ),
    ("client/manager.py", "ClientManager._cleanup_client", "reraises"): (
        1,
        "abandons synchronously, then re-raises",
    ),
    ("client/manager.py", "ClientManager.adopt_process", "reraises"): (
        1,
        "round 6: a cancel while waiting for the lifecycle lock kills the "
        "handed-over process synchronously, then re-raises",
    ),
    ("client/manager.py", "ClientManager._adopt_process_locked", "reraises"): (
        1,
        "cancel during the handshake",
    ),
    (
        "client/manager.py",
        "ClientManager._disconnect_all_unlocked._shutdown_one",
        "reraises",
    ): (1, "abandons synchronously, then re-raises into the gather"),
    ("client/manager.py", "ClientManager._health_monitor_loop", "absorbs"): (
        1,
        "task root: its own cancel ends the loop",
    ),
    ("client/manager.py", "ClientManager._own_remote_transport", "reraises"): (
        1,
        "hands a pre-handoff failure to `ready`, otherwise re-raises",
    ),
    ("client/manager.py", "ClientManager._reconcile_server_catalog", "reraises"): (
        1,
        "re-raise",
    ),
    ("client/manager.py", "ClientManager._send_request", "reraises"): (1, "re-raise"),
    ("env_store.py", "write_env_file", "reraises"): (1, "sync temp-file cleanup"),
    ("manifest/installer.py", "JobManager._handle_task_exception", "absorbs"): (
        1,
        "sync done-callback reading a finished task's result; no await",
    ),
    ("manifest/installer.py", "JobManager._monitor_install", "absorbs"): (
        1,
        "task root: its own cancel ends the job",
    ),
    ("manifest/installer.py", "JobManager._monitor_install", "reraises"): (
        1,
        "re-raise",
    ),
    ("manifest/registry.py", "save_registry_cache", "reraises"): (
        1,
        "sync temp-file cleanup",
    ),
    ("package_approvals.py", "_write_store", "reraises"): (1, "sync temp-file cleanup"),
    ("tools/handlers.py", "GatewayTools._run_update_probe_command", "reraises"): (
        1,
        "SIGKILLs the probe synchronously, then re-raises",
    ),
    ("trust_store.py", "_write_store", "reraises"): (1, "sync temp-file cleanup"),
}

# Handlers allowed to await: task roots whose own cancellation ends them.
_AWAITING_HANDLERS = {("manifest/installer.py", "JobManager._monitor_install")}


def _catches_cancellation(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for node in names:
        name = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
        if name in {"CancelledError", "BaseException"}:
            return True
    return False


def _modules() -> list[tuple[str, ast.Module]]:
    return [
        (path.relative_to(_PKG).as_posix(), ast.parse(path.read_text()))
        for path in sorted(_PKG.rglob("*.py"))
    ]


def _handlers() -> list[tuple[str, str, ast.ExceptHandler]]:
    found: list[tuple[str, str, ast.ExceptHandler]] = []
    for rel, tree in _modules():

        def visit(node: ast.AST, scope: list[str]) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    visit(child, [*scope, child.name])
                    continue
                if isinstance(child, ast.ExceptHandler) and _catches_cancellation(
                    child
                ):
                    found.append((rel, ".".join(scope), child))
                visit(child, scope)

        visit(tree, [])
    return found


def _scan() -> dict[tuple[str, str, str], int]:
    counts: dict[tuple[str, str, str], int] = {}
    for rel, scope, handler in _handlers():
        raises = any(isinstance(n, ast.Raise) for n in ast.walk(handler))
        key = (rel, scope, "reraises" if raises else "absorbs")
        counts[key] = counts.get(key, 0) + 1
    return counts


def test_every_cancellation_handler_is_classified() -> None:
    found = _scan()
    expected = {k: n for k, (n, _why) in _CANCEL_HANDLERS.items()}
    assert found == expected, (
        f"unclassified or changed: {sorted(set(found.items()) - set(expected.items()))}; "
        f"gone: {sorted(set(expected.items()) - set(found.items()))}"
    )


def test_no_cancellation_handler_awaits() -> None:
    """The class rule (round 3): a handler that catches a cancellation does
    only synchronous work. An await there can itself be cancelled before it
    runs (loop shutdown), hang, or hold a lock -- the three round-2 defects."""
    awaiting = sorted(
        (rel, scope, handler.lineno)
        for rel, scope, handler in _handlers()
        if (rel, scope) not in _AWAITING_HANDLERS
        and any(isinstance(n, ast.Await) for n in ast.walk(handler))
    )
    assert awaiting == [], awaiting


# Each teardown entry point, and the synchronous step its CancelledError
# handler must call.
_TEARDOWN_ENTRIES = {
    "ClientManager._connect_stdio": "_abandon_client_io",
    "ClientManager._connect_remote_stream": "_abandon_client_io",
    "ClientManager.adopt_process": "_kill_process_tree_now",
    "ClientManager._adopt_process_locked": "_abandon_client_io",
    "ClientManager.disconnect_server": "_abandon_client_io",
    "ClientManager._disconnect_server_locked": "_abandon_client_io",
    "ClientManager.disconnect_all": "abandon_all_now",
    "ClientManager._cleanup_client": "_abandon_client_io",
    "ClientManager._disconnect_all_unlocked._shutdown_one": "_abandon_client_io",
    "ClientManager._disconnect_all_unlocked": "_abandon_client_io",
    "_terminate_process_tree": "_kill_process_tree_now",
}


def _callee(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def test_every_teardown_abandons_synchronously_on_cancel() -> None:
    by_scope: dict[str, list[ast.ExceptHandler]] = {}
    for rel, scope, handler in _handlers():
        if rel == "client/manager.py":
            by_scope.setdefault(scope, []).append(handler)
    missing = []
    for scope, step in _TEARDOWN_ENTRIES.items():
        ok = [
            h
            for h in by_scope.get(scope, [])
            if any(isinstance(n, ast.Call) and _callee(n) == step for n in ast.walk(h))
            and isinstance(h.body[-1], ast.Raise)
        ]
        if not ok:
            missing.append(f"{scope}: no CancelledError handler calling {step}")
    assert missing == [], missing


def test_abandon_is_synchronous_and_child_waits_never_absorb() -> None:
    """`_abandon_client_io` and `_kill_process_tree_now` are plain functions,
    and `_reap_child` waits with `asyncio.wait` and catches no cancellation."""
    tree = dict(_modules())["client/manager.py"]
    defs = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert isinstance(defs["_abandon_client_io"], ast.FunctionDef)
    assert isinstance(defs["_kill_process_tree_now"], ast.FunctionDef)
    reap = defs["_reap_child"]
    assert not any(
        isinstance(h, ast.ExceptHandler) and _catches_cancellation(h)
        for h in ast.walk(reap)
    )
    assert any(
        isinstance(c, ast.Call) and ast.unparse(c.func) == "asyncio.wait"
        for c in ast.walk(reap)
    )
    assert not any(
        isinstance(c, ast.Call) and ast.unparse(c.func) == "asyncio.shield"
        for c in ast.walk(reap)
    )


# --- codex, round 1 on the implementation (PR #345) ---------------------------------

_COUNTING_FAILURE = (
    "import sys; open(sys.argv[1], 'a').write('spawn\\n')"  # then exit: no handshake
)


def _spawns(path: Path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


async def test_a_cancelled_disconnect_stops_a_connect_in_its_retry_backoff(
    tmp_path: Path,
) -> None:
    """Codex F045-F047: `connect_server` is in its retry backoff -- holding
    the lifecycle lock, with no client registered -- when a forced
    `disconnect_server` waiting for that lock is cancelled. The pre-lock
    handler cancelled the connect task only inside `if client registered`,
    so `_connect_tasks['srv']` survived and attempt 2 spawned the server
    after the disconnect. Now: the entry is gone at once, attempt 2 never
    spawns (real processes; the backoff is shortened to 0.5 s)."""
    mgr = ClientManager()
    counter = tmp_path / "spawns"
    cfg = ResolvedServerConfig(
        name="srv",
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable, args=["-c", _COUNTING_FAILURE, str(counter)]
        ),
    )
    in_backoff = asyncio.Event()
    real_warning = manager_mod.logger.warning

    def noting_warning(msg: Any, *args: Any, **kwargs: Any) -> None:
        if "retrying in" in str(msg):
            in_backoff.set()
        real_warning(msg, *args, **kwargs)

    with (
        patch.object(manager_mod, "RETRY_DELAYS", [0.5, 0.5, 0.5]),
        patch.object(manager_mod.logger, "warning", noting_warning),
    ):
        connecting = asyncio.create_task(mgr.connect_server(cfg))
        try:
            await asyncio.wait_for(in_backoff.wait(), _HANG_GUARD_S)
            assert _spawns(counter) == 1
            assert "srv" not in mgr._clients and "srv" in mgr._connect_tasks
            assert mgr._lifecycle_lock.locked()
            disconnecting = asyncio.create_task(
                mgr.disconnect_server("srv", force=True)
            )
            for _ in range(3):
                await asyncio.sleep(0)
            disconnecting.cancel()
            kind, detail = await _outcome(disconnecting)
            assert kind == "cancelled", f"{kind} {detail!r}"
            assert mgr._connect_tasks == {}, list(mgr._connect_tasks)
            await asyncio.wait({connecting}, timeout=_HANG_GUARD_S)
            await asyncio.sleep(1.0)  # past the shortened backoff
            assert _spawns(counter) == 1, "attempt 2 spawned after the disconnect"
            assert "srv" not in mgr._clients
        finally:
            connecting.cancel()
            await _drain(mgr)


@pytest.mark.parametrize("how", ["abandon_all_now", "gateway_shutdown"])
async def test_a_refresh_in_flight_through_abandonment_spawns_nothing(
    how: str, tmp_path: Path
) -> None:
    """Codex F042-F044: a `refresh()` holds the lifecycle lock inside
    `_disconnect_all_unlocked` (its SIGTERM-ignoring server keeps it in the
    terminate grace, entered once the group SIGTERM is sent) when the gateway abandons every client -- directly, or
    through the real `GatewayServer.shutdown()` whose budget (shortened to
    0.5 s) runs out waiting for that lock. `abandon_all_now` cancelled the
    background tasks but not the lock holder, which went on to
    `_connect_all_unlocked(configs)` and respawned the server after
    shutdown. Now nothing spawns and no client is registered."""
    from pmcp import server as server_mod
    from pmcp.server import GatewayServer

    gateway = GatewayServer()
    mgr = gateway._client_manager
    pidfile = tmp_path / "srv.pids"
    cfg = ResolvedServerConfig(
        name="srv",
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable, args=["-c", _MCP_SERVER, str(pidfile)]
        ),
    )
    await asyncio.wait_for(mgr._connect_stdio(cfg), _HANG_GUARD_S)
    pids = [int(p) for p in pidfile.read_text().split()]
    spawns: list[str] = []
    real_exec = asyncio.create_subprocess_exec

    async def counting_exec(*args: Any, **kwargs: Any) -> Any:
        spawns.append("spawn")
        return await real_exec(*args, **kwargs)

    terminating = asyncio.Event()
    real_killpg = os.killpg

    def noting_killpg(pgid: int, sig: int) -> None:
        real_killpg(pgid, sig)
        if sig == signal.SIGTERM:
            terminating.set()

    try:
        with (
            patch("asyncio.create_subprocess_exec", counting_exec),
            patch.object(manager_mod.os, "killpg", noting_killpg),
        ):
            refreshing = asyncio.create_task(mgr.refresh([cfg]))
            await asyncio.wait_for(terminating.wait(), _HANG_GUARD_S)
            assert mgr._lifecycle_lock.locked()
            if how == "abandon_all_now":
                mgr.abandon_all_now()
            else:
                real_wait = server_mod.bounded_wait

                async def short_budget(aw: Any, timeout: float | None) -> Any:
                    return await real_wait(aw, timeout=0.5)

                with (
                    patch.object(server_mod, "bounded_wait", short_budget),
                    patch.object(server_mod, "release_singleton_lock", lambda: None),
                ):
                    await asyncio.wait_for(gateway.shutdown(), _HANG_GUARD_S)
            kind, detail = await _outcome(refreshing)
            for _ in range(10):
                await asyncio.sleep(0)
        assert kind in ("returned", "cancelled"), f"{kind} {detail!r}"
        if kind == "returned":
            assert detail == [], detail
        assert spawns == [], spawns
        assert mgr._clients == {}, list(mgr._clients)
        await eventually(
            lambda: not any(_pid_alive(p) for p in pids),
            timeout=_HANG_GUARD_S,
            message=f"{how}: the tree survived",
        )
    finally:
        _kill_pids(pids)
        await _drain(mgr)


async def test_a_connect_after_abandonment_is_refused_without_retrying(
    tmp_path: Path,
) -> None:
    """The other half of F042-F044: an operation queued behind the lock
    holder, or started after `abandon_all_now`, reaches `_connect_server`
    and is refused there -- at once, not after the retry backoff
    (`_connect_with_retry` re-raises the refusal), and without spawning."""
    mgr = ClientManager()
    counter = tmp_path / "spawns"
    cfg = ResolvedServerConfig(
        name="srv",
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable, args=["-c", _COUNTING_FAILURE, str(counter)]
        ),
    )
    mgr.abandon_all_now()
    loop = asyncio.get_running_loop()
    started = loop.time()
    errors = await asyncio.wait_for(mgr.connect_server(cfg), _HANG_GUARD_S)
    elapsed = loop.time() - started
    assert len(errors) == 1 and "abandoned" in errors[0], errors
    assert elapsed < manager_mod.RETRY_DELAYS[0], f"retried: {elapsed:.2f}s"
    assert _spawns(counter) == 0
    assert mgr._clients == {} and mgr._connect_tasks == {}


async def test_adoption_after_abandonment_is_refused_and_registers_nothing() -> None:
    """Codex round 2 (F002): `adopt_process` registers a client without going
    through `_connect_server`, so it used to register an ONLINE client after
    `abandon_all_now()`. It now refuses with `_ManagerAbandoned` before
    touching any registry, leaving the process to its caller (real process)."""
    mgr = ClientManager()
    cfg = _stdio_config("adopted")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(300)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        mgr.abandon_all_now()
        with pytest.raises(manager_mod._ManagerAbandoned):
            await asyncio.wait_for(
                mgr.adopt_process("adopted", process, cfg), _HANG_GUARD_S
            )
        assert "adopted" not in mgr._clients
        assert "adopted" not in mgr._servers
        assert process.returncode is None  # the caller owns its cleanup
    finally:
        process.kill()
        await asyncio.wait_for(process.wait(), _HANG_GUARD_S)


async def _spawn_handover(pidfile: Path) -> asyncio.subprocess.Process:
    """A process as the installer hands it to `adopt_process`."""
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _MCP_SERVER,
        str(pidfile),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )


def _reconnects(mgr: ClientManager) -> list[str]:
    return [
        t.get_name()
        for t in mgr._background_tasks
        if not t.done() and t.get_name().startswith("reconnect-")
    ]


@pytest.mark.parametrize("cancel", [None, "disconnect_all", "adopt"])
async def test_a_client_adopted_during_disconnect_all_is_not_left_running(
    cancel: str | None, tmp_path: Path
) -> None:
    """Codex round 5, grok round 6: adoption registered a client without the
    lifecycle lock, so a client adopted while `disconnect_all` was suspended
    (here in a SIGTERM-ignoring server's grace) was dropped alive by the
    registry clear, or -- uncancelled -- swept while ONLINE, which scheduled
    a reconnect that respawned it after `disconnect_all` returned. Now
    adoption waits for the lock: nothing is registered until the teardown
    is over (real processes).

    * uncancelled: the adoption, requested after the teardown began, runs
      after it, exactly once -- no reconnect, no respawn 5.5 s later;
    * `disconnect_all` cancelled: its teardown still kills what it held,
      and the adoption then runs after it, as one requested later should;
    * the adoption cancelled while it waits: its process (here a group
      leader) is killed at once, group and all.
    """
    mgr, _cfg, pids = await _real_server(tmp_path, "slow")  # ignores SIGTERM
    late_pidfile = tmp_path / "late.pids"
    late_cfg = ResolvedServerConfig(
        name="late",
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable, args=["-c", _MCP_SERVER, str(late_pidfile)]
        ),
    )
    late_pids: list[int] = []
    spawned: list[int] = []
    terminating = asyncio.Event()
    real_killpg = os.killpg
    real_exec = asyncio.create_subprocess_exec

    def noting_killpg(pgid: int, sig: int) -> None:
        real_killpg(pgid, sig)
        if sig == signal.SIGTERM:
            terminating.set()

    async def counting_exec(*args: Any, **kwargs: Any) -> Any:
        proc = await real_exec(*args, **kwargs)
        spawned.append(proc.pid)
        return proc

    try:
        with (
            patch.object(manager_mod.os, "killpg", noting_killpg),
            patch("asyncio.create_subprocess_exec", counting_exec),
        ):
            disconnecting = asyncio.create_task(mgr.disconnect_all())
            await asyncio.wait_for(terminating.wait(), _HANG_GUARD_S)
            late = await _spawn_handover(late_pidfile)
            await eventually(late_pidfile.exists, timeout=_HANG_GUARD_S)
            late_pids = [int(p) for p in late_pidfile.read_text().split()]
            adopting = asyncio.create_task(mgr.adopt_process("late", late, late_cfg))
            for _ in range(10):
                await asyncio.sleep(0)
            assert not adopting.done() and "late" not in mgr._clients
            if cancel == "adopt":
                adopting.cancel()
                assert (await _outcome(adopting))[0] == "cancelled"
                assert await asyncio.wait_for(late.wait(), _HANG_GUARD_S) == (
                    -signal.SIGKILL
                )
                assert (await _outcome(disconnecting))[0] == "returned"
            elif cancel == "disconnect_all":
                # Its cancelled teardown kills what it was tearing down; the
                # adoption, requested after it began, then runs after it.
                disconnecting.cancel()
                assert (await _outcome(disconnecting))[0] == "cancelled"
                assert (await _outcome(adopting))[0] == "returned"
                assert mgr._clients["late"].process is late
                assert list(mgr._clients) == ["late"]
            else:
                assert (await _outcome(disconnecting))[0] == "returned"
                assert (await _outcome(adopting))[0] == "returned"
                assert mgr._clients["late"].process is late
                assert mgr.is_server_online("late")
            assert _reconnects(mgr) == []
            spawned_at_return = len(spawned)
            if cancel is None:
                await asyncio.sleep(5.5)  # past the reconnect loop's first delay
                assert mgr._clients["late"].process is late, "respawned"
                assert _reconnects(mgr) == []
            assert spawned[spawned_at_return:] == []
        await eventually(
            lambda: not any(_pid_alive(p) for p in pids),
            timeout=_HANG_GUARD_S,
            message=f"cancel={cancel}: the torn-down server survived",
        )
        if cancel == "adopt":
            assert mgr._clients == {}, list(mgr._clients)
            await eventually(
                lambda: not any(_pid_alive(p) for p in late_pids),
                timeout=_HANG_GUARD_S,
                message="the handed-over process survived its cancelled adoption",
            )
    finally:
        _kill_pids(pids + late_pids + spawned)
        await _drain(mgr)


@pytest.mark.parametrize("cancel", [True, False])
@pytest.mark.parametrize("entrypoint", ["disconnect_server", "_cleanup_client"])
async def test_an_adoption_waits_for_a_per_server_teardown(
    entrypoint: str, cancel: bool, tmp_path: Path
) -> None:
    """Codex/grok round 6: `disconnect_server` and `_cleanup_client` abandon
    the client they captured and then drop `_clients[name]` by name, so a
    same-name client adopted while they were suspended was forgotten alive.
    Adoption now waits for the lifecycle lock those teardowns run under
    (`_cleanup_client` is only ever called with it held: by the connect
    funnel and by adoption itself), so the replacement is registered after
    the teardown and `abandon_all_now()` reaches it."""
    mgr, cfg, old_pids = await _real_server(tmp_path, "srv")  # ignores SIGTERM
    late_pidfile = tmp_path / "late.pids"
    late_pids: list[int] = []
    terminating = asyncio.Event()
    real_killpg = os.killpg

    def noting_killpg(pgid: int, sig: int) -> None:
        real_killpg(pgid, sig)
        if sig == signal.SIGTERM:
            terminating.set()

    async def cleanup_under_lock() -> None:
        async with mgr._lifecycle_lock:
            await mgr._cleanup_client("srv", mgr._clients["srv"])

    try:
        with patch.object(manager_mod.os, "killpg", noting_killpg):
            tearing = asyncio.create_task(
                mgr.disconnect_server("srv", force=True)
                if entrypoint == "disconnect_server"
                else cleanup_under_lock()
            )
            await asyncio.wait_for(terminating.wait(), _HANG_GUARD_S)
            late = await _spawn_handover(late_pidfile)
            await eventually(late_pidfile.exists, timeout=_HANG_GUARD_S)
            late_pids = [int(p) for p in late_pidfile.read_text().split()]
            adopting = asyncio.create_task(mgr.adopt_process("srv", late, cfg))
            for _ in range(10):
                await asyncio.sleep(0)
            assert not adopting.done()
            assert mgr._clients.get("srv") is None or (
                mgr._clients["srv"].process is not late
            )
            if cancel:
                tearing.cancel()
                assert (await _outcome(tearing))[0] == "cancelled"
            else:
                assert (await _outcome(tearing))[0] == "returned"
            assert (await _outcome(adopting))[0] == "returned"
            assert mgr._clients["srv"].process is late
            await eventually(
                lambda: not any(_pid_alive(p) for p in old_pids),
                timeout=_HANG_GUARD_S,
                message="the torn-down server survived",
            )
            mgr.abandon_all_now()
            assert await asyncio.wait_for(late.wait(), _HANG_GUARD_S) == -signal.SIGKILL
            await eventually(
                lambda: not any(_pid_alive(p) for p in late_pids),
                timeout=_HANG_GUARD_S,
                message="the adopted replacement survived abandon_all_now",
            )
    finally:
        _kill_pids(old_pids + late_pids)
        await _drain(mgr)


async def test_a_cancel_while_adoption_waits_for_the_lock_kills_the_process(
    tmp_path: Path,
) -> None:
    """Nothing is registered while adoption waits for the lifecycle lock, and
    its caller catches only `Exception`: a cancel there kills the handed-over
    process synchronously, before the cancel propagates."""
    mgr = ClientManager()
    late = await _spawn_handover(tmp_path / "late.pids")
    seen: list[Any] = []

    async def caller() -> None:
        try:
            await mgr.adopt_process("late", late, _stdio_config("late"))
        except asyncio.CancelledError:
            seen.append(late.returncode is not None or "kill-sent")
            raise

    await mgr._lifecycle_lock.acquire()
    try:
        with patch.object(late, "kill", wraps=late.kill) as kill:
            task = asyncio.create_task(caller())
            for _ in range(5):
                await asyncio.sleep(0)
            assert not task.done()
            task.cancel()
            assert (await _outcome(task))[0] == "cancelled"
            assert kill.call_count == 1 and seen, seen
        assert await asyncio.wait_for(late.wait(), _HANG_GUARD_S) == -signal.SIGKILL
        assert mgr._clients == {} and mgr._background_tasks == set()
    finally:
        mgr._lifecycle_lock.release()
        if late.returncode is None:
            late.kill()
            await late.wait()


async def test_an_adoption_waiting_for_the_lock_through_abandonment_registers_nothing(
    tmp_path: Path,
) -> None:
    """Abandonment can run while adoption waits for the lifecycle lock; the
    check is repeated under the lock, with no await before registration."""
    mgr = ClientManager()
    late = await _spawn_handover(tmp_path / "late.pids")
    await mgr._lifecycle_lock.acquire()
    try:
        adopting = asyncio.create_task(
            mgr.adopt_process("late", late, _stdio_config("late"))
        )
        for _ in range(5):
            await asyncio.sleep(0)
        assert not adopting.done()
        mgr.abandon_all_now()
        mgr._lifecycle_lock.release()
        kind, detail = await _outcome(adopting)
        assert kind == "raised" and "abandoned" in str(detail), (kind, detail)
        assert mgr._clients == {} and mgr._servers == {}
    finally:
        if mgr._lifecycle_lock.locked():
            mgr._lifecycle_lock.release()
        if late.returncode is None:
            late.kill()
        await late.wait()
        await _drain(mgr)


async def test_a_lock_wait_cancel_kills_a_group_whose_leader_already_exited(
    tmp_path: Path,
) -> None:
    """Codex round 7: the handed-over leader leads its own group and exits
    while adoption waits for the lock, leaving a descendant. The group is
    read before the wait, so the cancel still kills the descendant."""
    mgr = ClientManager()
    script = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "print(p.pid,flush=True); sys.stdin.readline()"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stdin is not None
    child = int(await asyncio.wait_for(process.stdout.readline(), _HANG_GUARD_S))
    await mgr._lifecycle_lock.acquire()
    try:
        adopting = asyncio.create_task(
            mgr.adopt_process("handoff", process, _stdio_config("handoff"))
        )
        await asyncio.sleep(0)
        assert not adopting.done() and process.returncode is None
        process.stdin.write(b"\n")
        assert await asyncio.wait_for(process.wait(), _HANG_GUARD_S) == 0
        adopting.cancel()
        assert (await _outcome(adopting))[0] == "cancelled"
        await eventually(
            lambda: not _pid_alive(child),
            timeout=_HANG_GUARD_S,
            message="the exited leader's descendant survived the cancel",
        )
        assert mgr._clients == {}
    finally:
        mgr._lifecycle_lock.release()
        _kill_pids([child])


async def test_a_lock_wait_cancel_keeps_its_cancel_when_the_kill_fails() -> None:
    """Codex round 7: a kill that raises (here `PermissionError`) must not
    replace the caller's `CancelledError`."""
    mgr = ClientManager()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import sys; sys.stdin.readline()",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    await mgr._lifecycle_lock.acquire()
    try:
        adopting = asyncio.create_task(
            mgr.adopt_process("handoff", process, _stdio_config("handoff"))
        )
        await asyncio.sleep(0)
        assert not adopting.done()
        with patch.object(process, "kill", side_effect=PermissionError("denied")):
            adopting.cancel()
            kind, detail = await _outcome(adopting)
        assert kind == "cancelled", f"{kind} {detail!r}"
        assert mgr._clients == {}
    finally:
        mgr._lifecycle_lock.release()
        if process.returncode is None:
            process.kill()
        await process.wait()
