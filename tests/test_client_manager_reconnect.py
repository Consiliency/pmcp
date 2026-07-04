"""Reconnect and failure-path recovery tests for ClientManager.

These exercise the *real* stdio connect path (spawning an actual subprocess)
rather than mocking the connect internals, because the auto-reconnect
self-cancel bug (RecursionError) only reproduces when a live connect task runs
through ``_cleanup_client`` -> ``_cancel_background_tasks`` while cancelling
tasks scoped to its own server name.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal

import pytest

from pmcp.client.manager import ClientManager, ManagedClient
from pmcp.types import (
    LocalMcpServerConfig,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    ServerStatus,
    ServerStatusEnum,
)

pytestmark = pytest.mark.asyncio

_SLOW_SERVER = (
    Path(__file__).resolve().parent.parent
    / "diagnostics"
    / "issue-79-1b"
    / "slow_server.py"
)


def _stdio_config(name: str) -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name,
        source="project",
        config=LocalMcpServerConfig(command="python3", args=[str(_SLOW_SERVER)]),
    )


async def _await_status(
    mgr: ClientManager,
    name: str,
    status: ServerStatusEnum,
    *,
    timeout: float,
    predicate=None,
) -> ServerStatus:
    """Poll until ``name`` reaches ``status`` (and optional predicate), or fail."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        current = mgr._servers.get(name)
        if (
            current is not None
            and current.status == status
            and (predicate is None or predicate())
        ):
            return current
        await asyncio.sleep(0.1)
    current = mgr._servers.get(name)
    raise AssertionError(
        f"{name} did not reach {status} within {timeout}s "
        f"(last status={current.status if current else None})"
    )


@pytest.fixture
async def manager() -> ClientManager:
    mgr = ClientManager()
    try:
        yield mgr
    finally:
        await mgr.disconnect_all()


@pytest.mark.skipif(not _SLOW_SERVER.exists(), reason="slow_server.py fixture missing")
async def test_stdio_server_reconnects_after_crash(manager: ClientManager) -> None:
    """A SIGKILLed stdio server auto-reconnects with a fresh process.

    On the pre-fix code this hangs in ERROR forever because the reconnect's
    connect task cancels a ``gather()`` containing itself (RecursionError).
    """
    name = "slow"
    cfg = _stdio_config(name)

    errors = await manager.connect_all([cfg])
    assert errors == [], errors

    online = await _await_status(manager, name, ServerStatusEnum.ONLINE, timeout=15.0)
    assert online.status == ServerStatusEnum.ONLINE

    managed = manager._clients[name]
    assert managed.process is not None
    old_pid = managed.process.pid

    os.kill(old_pid, signal.SIGKILL)

    # First reconnect attempt fires after the initial 5s back-off; allow for the
    # kill to be observed, the reconnect to run, and the new process to init.
    recovered = await _await_status(
        manager,
        name,
        ServerStatusEnum.ONLINE,
        timeout=25.0,
        predicate=lambda: (
            (m := manager._clients.get(name)) is not None
            and m.process is not None
            and m.process.pid != old_pid
        ),
    )
    assert recovered.status == ServerStatusEnum.ONLINE
    new_pid = manager._clients[name].process.pid
    assert new_pid != old_pid, "expected a brand-new process after reconnect"


async def test_cancel_background_tasks_excludes_current_task(
    manager: ClientManager,
) -> None:
    """A task cancelling its own server's background tasks survives.

    Reproduces the self-cancel path directly: the running task is registered as
    both a background task and the server's connect task, so it matches
    ``_cancel_background_tasks``'s scope. It must NOT be cancelled.
    """
    name = "self-cancel"
    reached_end = False

    async def worker() -> None:
        nonlocal reached_end
        current = asyncio.current_task()
        assert current is not None
        manager._track_background_task(current, name)
        # Match the exact bug branch: task is self._connect_tasks.get(name).
        manager._connect_tasks[name] = current
        await manager._cancel_background_tasks(server_name=name)
        reached_end = True

    task = asyncio.create_task(worker())
    await task

    assert reached_end, "current task was cancelled by its own cleanup"
    assert not task.cancelled()


async def test_remote_drop_schedules_reconnect(manager: ClientManager) -> None:
    """An unexpected remote (SSE) stream drop schedules an auto-reconnect."""
    name = "remote"
    cfg = ResolvedServerConfig(
        name=name,
        source="project",
        config=RemoteMcpServerConfig(type="sse", url="https://example.invalid/mcp"),
    )
    status = ServerStatus(name=name, status=ServerStatusEnum.ONLINE, tool_count=0)
    managed = ManagedClient(config=cfg, is_remote=True, status=status)
    manager._clients[name] = managed

    scheduled: list[str] = []
    manager._schedule_reconnect = (  # type: ignore[method-assign]
        lambda n, c: scheduled.append(n)
    )

    async def dropping_stream():
        raise ConnectionError("stream dropped")
        yield  # pragma: no cover - makes this an async generator

    await manager._read_sse(name, managed, dropping_stream())

    assert scheduled == [name], "remote drop did not schedule a reconnect"
    assert managed.reconnecting is True
    assert managed.status.status == ServerStatusEnum.ERROR


@pytest.mark.skipif(not _SLOW_SERVER.exists(), reason="slow_server.py fixture missing")
async def test_failed_connect_leaves_no_stale_client(
    manager: ClientManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connect that fails at initialize leaves no stale client or live tasks."""
    name = "failinit"
    cfg = _stdio_config(name)

    async def boom(managed: ManagedClient) -> None:
        raise RuntimeError("initialize failed")

    monkeypatch.setattr(manager, "_send_initialize", boom)

    with pytest.raises(RuntimeError, match="initialize failed"):
        await manager._connect_stdio(cfg)

    assert name not in manager._clients, "stale client left after failed connect"

    # No background task scoped to this server (stdout/stderr readers) stays live.
    alive: list[asyncio.Task] = []
    for _ in range(20):
        alive = [
            t
            for t in manager._background_tasks
            if manager._background_task_servers.get(t) == name and not t.done()
        ]
        if not alive:
            break
        await asyncio.sleep(0.05)
    assert not alive, "leaked live background task after failed connect"
