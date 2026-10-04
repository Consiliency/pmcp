"""Consiliency/pmcp#324, round 2 on the implementation (PR #345): a
`disconnect_server(name)` cancelled before it takes the lifecycle lock
supersedes every connect of `name` requested before it; `abandon_all_now()`
supersedes every connect, and `adopt_process` honours it.

Uncancelled, such a disconnect runs after every connect of `name` requested
before it (the lifecycle lock is FIFO) and tears down what they produced.
Cancelled, it never runs -- so before this change a connect queued on the
lock (codex F001), held up behind a lock holder that had not started its
per-name task yet (claude F001: a `refresh` in its disconnect phase), a lazy
start or a reconnect still spawned the server afterwards. Now each request
captures the connect generation when it is requested, and `_admit_connect`
refuses it once a cancelled disconnect of that name (or abandonment) has
superseded it, leaving the end state the uncancelled ordering leaves.

The matrix below runs every connect entry point, in every state it can be in
when the disconnect is cancelled, against both triggers, with real
processes; for the cancelled-disconnect trigger it also runs the same
ordering uncancelled and compares the end states.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.client import manager as manager_mod
from pmcp.client.manager import (
    ClientManager,
    _ConnectSuperseded,
    _ManagerAbandoned,
)
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig, ServerStatusEnum
from tests._timing import eventually
from tests.test_cancel_teardown import _HANG_GUARD_S, _MCP_SERVER, _outcome

# A real MCP server that appends its pid to argv[1] when it starts. In mode
# "fail-first" the first spawn exits at once (a failed attempt, so the
# connect enters its retry backoff); every later spawn serves.
_COUNTED_SERVER = r"""
import json, os, sys
counter, mode = sys.argv[1], sys.argv[2]
first = not os.path.exists(counter)
with open(counter, "a") as f:
    f.write(f"{os.getpid()}\n")
if mode == "fail-first" and first:
    sys.exit(1)
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method in ("tools/list", "resources/list", "prompts/list"):
        result = {method.split("/")[0]: []}
    else:
        result = {}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}) + "\n")
    sys.stdout.flush()
"""

_SUPERSEDED = "superseded by a disconnect_server(srv)"
_ABANDONED = "the client manager was abandoned"


def _counted(name: str, counter: Path, mode: str = "ok") -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name,
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable, args=["-c", _COUNTED_SERVER, str(counter), mode]
        ),
    )


def _spawned(counter: Path) -> list[int]:
    return [int(p) for p in counter.read_text().split()] if counter.exists() else []


def _kill_groups(pids: list[int]) -> None:
    """Every spawn leads its own group (`start_new_session=True`): kill each
    by its exact pid as pgid."""
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


async def _yields(n: int = 5) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


def _end_state(mgr: ClientManager) -> tuple[list[str], str | None, bool]:
    status = mgr._servers.get("srv")
    return (
        sorted(mgr._clients),
        status.status.value if status is not None else None,
        "srv" in mgr._lazy_configs,
    )


ENTRIES = ["connect_server", "connect_all", "refresh", "ensure_connected", "reconnect"]
# "holder": the entry point holds the lifecycle lock, with awaits still to
# come before it starts its per-name connect task. Only `refresh` has such a
# window (its disconnect phase). `connect_server` and the reconnect loop
# start the per-name task in the step they acquire the lock; `connect_all`
# starts a request task per server in that step, which starts the per-name
# task in its first step, after admission (a cancel in between leaves the
# request task stale, and `_connect_singleflight` refuses it); and
# `ensure_connected` starts it under its first lock hold, after which the
# task queues on the lock again: the "queued" state.
STATES = ["queued", "holder", "backoff", "midspawn"]
TRIGGERS = ["cancelled_disconnect", "abandon_all_now"]
CELLS = [
    (entry, state, trigger)
    for entry in ENTRIES
    for state in STATES
    for trigger in TRIGGERS
    if state != "holder" or entry == "refresh"
]


@contextlib.contextmanager
def _instrumented(srv_counter: Path, *, park_spawn: bool) -> Iterator[SimpleNamespace]:
    """Short retry backoff, immediate reconnect, and events for: the connect
    entering its backoff, the srv spawn parked (when `park_spawn`), and a
    group SIGTERM (a refresh in its disconnect phase)."""
    probe = SimpleNamespace(
        in_backoff=asyncio.Event(),
        parked=asyncio.Event(),
        gate=asyncio.Event(),
        terminating=asyncio.Event(),
    )
    real_exec = asyncio.create_subprocess_exec
    real_warning = manager_mod.logger.warning
    real_killpg = os.killpg

    async def gated_exec(*args: Any, **kwargs: Any) -> Any:
        if park_spawn and str(srv_counter) in args:
            probe.parked.set()
            await probe.gate.wait()
        return await real_exec(*args, **kwargs)

    def noting_warning(msg: Any, *args: Any, **kwargs: Any) -> None:
        if "retrying in" in str(msg):
            probe.in_backoff.set()
        real_warning(msg, *args, **kwargs)

    def noting_killpg(pgid: int, sig: int) -> None:
        real_killpg(pgid, sig)
        if sig == signal.SIGTERM:
            probe.terminating.set()

    with (
        patch.object(manager_mod, "RETRY_DELAYS", [0.5, 0.5, 0.5]),
        patch.object(manager_mod, "RECONNECT_DELAYS", (0.0, 0.0, 0.0)),
        patch("asyncio.create_subprocess_exec", gated_exec),
        patch.object(manager_mod.logger, "warning", noting_warning),
        patch.object(manager_mod.os, "killpg", noting_killpg),
    ):
        yield probe


def _start(
    mgr: ClientManager, entry: str, cfgs: list[ResolvedServerConfig]
) -> asyncio.Task[Any]:
    srv = cfgs[-1]
    if entry == "connect_server":
        return asyncio.create_task(mgr.connect_server(srv))
    if entry == "connect_all":
        return asyncio.create_task(mgr.connect_all(cfgs))
    if entry == "refresh":
        return asyncio.create_task(mgr.refresh(cfgs))
    if entry == "ensure_connected":
        return asyncio.create_task(mgr.ensure_connected("srv"))
    assert entry == "reconnect"
    mgr._schedule_reconnect("srv", srv)
    return mgr._reconnect_tasks["srv"]


async def _run_cell(
    entry: str, state: str, trigger: str, cancel: bool, tmp_path: Path
) -> dict[str, Any]:
    """One ordering: `entry` reaches `state`, then the trigger. With
    `cancel=False` (cancelled_disconnect only) the disconnect is left to run:
    the uncancelled reference."""
    tag = f"{entry}-{state}-{trigger}-{'cancel' if cancel else 'ref'}"
    counter = tmp_path / f"{tag}.srv"
    slow_pids = tmp_path / f"{tag}.slow"
    mgr = ClientManager()
    srv = _counted("srv", counter, "fail-first" if state == "backoff" else "ok")
    cfgs = [srv]
    if state == "holder":
        # A SIGTERM-ignoring server keeps the refresh in its terminate grace,
        # holding the lock, before it starts any connect task.
        slow = ResolvedServerConfig(
            name="slow",
            source="project",
            config=LocalMcpServerConfig(
                command=sys.executable, args=["-c", _MCP_SERVER, str(slow_pids)]
            ),
        )
        await asyncio.wait_for(mgr._connect_stdio(slow), _HANG_GUARD_S)
        await asyncio.wait_for(mgr._connect_stdio(srv), _HANG_GUARD_S)
        cfgs = [slow, srv]
    if entry == "ensure_connected":
        mgr.register_lazy_configs([srv])
    held = False
    task: asyncio.Task[Any] | None = None
    disconnecting: asyncio.Task[Any] | None = None
    result: dict[str, Any] = {}
    try:
        with _instrumented(counter, park_spawn=state == "midspawn") as probe:
            if state == "queued":
                await mgr._lifecycle_lock.acquire()
                held = True
            task = _start(mgr, entry, cfgs)
            if state == "queued":
                await _yields()
                assert not task.done() and _spawned(counter) == []
            elif state == "holder":
                await asyncio.wait_for(probe.terminating.wait(), _HANG_GUARD_S)
            elif state == "backoff":
                await asyncio.wait_for(probe.in_backoff.wait(), _HANG_GUARD_S)
            else:
                await asyncio.wait_for(probe.parked.wait(), _HANG_GUARD_S)
            assert mgr._lifecycle_lock.locked()
            spawned_before = len(_spawned(counter))

            if trigger == "abandon_all_now":
                mgr.abandon_all_now()
            else:
                disconnecting = asyncio.create_task(
                    mgr.disconnect_server("srv", force=True)
                )
                await _yields(3)
                assert not disconnecting.done()
                if cancel:
                    disconnecting.cancel()
                    kind, detail = await _outcome(disconnecting)
                    assert kind == "cancelled", f"{tag}: {kind} {detail!r}"

            if held:
                mgr._lifecycle_lock.release()
                held = False
            probe.gate.set()
            if disconnecting is not None and not cancel:
                kind, detail = await _outcome(disconnecting)
                assert kind == "returned" and detail[0], f"{tag}: {kind} {detail!r}"
            result["outcome"] = await _outcome(task)
            if state == "backoff":
                await asyncio.sleep(0.75)  # past the shortened backoff
            await _yields(10)
            result["spawned_after"] = len(_spawned(counter)) - spawned_before
            result["end"] = _end_state(mgr)
            if "srv" not in mgr._clients:
                # Nothing of srv's may outlive the supersession: a SIGKILL is
                # final, so poll (a hang guard, not a measurement).
                await eventually(
                    lambda: not any(_alive(p) for p in _spawned(counter)),
                    timeout=_HANG_GUARD_S,
                    message=f"{tag}: a srv process survived",
                )
    finally:
        if held:
            mgr._lifecycle_lock.release()
        if task is not None and not task.done():
            task.cancel()
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))
        if slow_pids.exists():
            _kill_pids_exact([int(p) for p in slow_pids.read_text().split()])
        pending = [t for t in mgr._background_tasks if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.wait(pending, timeout=_HANG_GUARD_S)
    return result


def _kill_pids_exact(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def _assert_refused(entry: str, outcome: tuple[str, Any], words: str) -> None:
    kind, detail = outcome
    if entry in ("connect_server", "connect_all", "refresh"):
        assert kind == "returned", (kind, detail)
        srv_errors = [e for e in detail if e.startswith("Failed to connect to srv:")]
        assert len(srv_errors) == 1 and words in srv_errors[0], detail
    elif entry == "ensure_connected":
        assert outcome == ("returned", False), outcome
    else:  # reconnect: a background task; it ends, by refusal or its cancel
        assert kind in ("returned", "cancelled"), outcome


# Lazy start's two pre-existing departures from request order, which the
# uncancelled reference shows and the cancelled path does not:
#
# * It locks twice: `ensure_connected` starts its connect task under one lock
#   hold, and the task waits for the lock again. Uncancelled, a disconnect
#   queued between the two runs first, and the lazy start then connects AFTER
#   it although it was requested before it ("queued": the reference ends
#   ONLINE). Cancelled, the disconnect supersedes the lazy start as it does
#   every earlier request.
# * After its connect it pops the lazy config under a second lock wait, so
#   when the disconnect runs between the connect and that pop, the pop undoes
#   the disconnect's lazy re-registration (the reference ends LAZY with no
#   lazy config).
#
# So for `ensure_connected` the test compares the clients and the status the
# reference can be expected to share, and asserts the cancelled side ends as
# a request-ordered disconnect leaves the server: LAZY, lazily startable.


@pytest.mark.parametrize(("entry", "state", "trigger"), CELLS)
async def test_a_superseded_connect_spawns_nothing(
    entry: str, state: str, trigger: str, tmp_path: Path
) -> None:
    got = await _run_cell(entry, state, trigger, True, tmp_path)
    assert got["spawned_after"] == 0, got
    clients, status, lazy = got["end"]
    if trigger == "abandon_all_now":
        assert clients == [], got
        _assert_refused(entry, got["outcome"], _ABANDONED)
        return
    assert "srv" not in clients, got
    _assert_refused(entry, got["outcome"], _SUPERSEDED)
    reference = await _run_cell(entry, state, trigger, False, tmp_path)
    if entry == "ensure_connected":
        # See the note above the test.
        assert got["end"] == (clients, "lazy", True), got
        if state == "queued":
            assert reference["end"][:2] == (["srv"], "online"), reference
        else:
            assert reference["end"][:2] == (clients, "lazy"), reference
        return
    assert got["end"] == reference["end"], (got, reference)


# --- the board's falsifiers, adapted -------------------------------------------


async def test_codex_f001_a_cancelled_disconnect_stops_an_already_queued_connect() -> (
    None
):
    manager = ClientManager()
    config = ResolvedServerConfig(
        name="srv", source="project", config=LocalMcpServerConfig(command="unused")
    )
    manager._connect_stdio = AsyncMock()  # type: ignore[method-assign]
    await manager._lifecycle_lock.acquire()
    connecting = asyncio.create_task(manager.connect_server(config, retry=False))
    disconnecting: asyncio.Task[Any] | None = None
    try:
        await asyncio.sleep(0)
        assert not connecting.done()
        assert not manager._background_tasks
        disconnecting = asyncio.create_task(
            manager.disconnect_server("srv", force=True)
        )
        await asyncio.sleep(0)
        assert not disconnecting.done()
        disconnecting.cancel()
        (result,) = await asyncio.gather(disconnecting, return_exceptions=True)
        assert isinstance(result, asyncio.CancelledError)
        manager._lifecycle_lock.release()
        errors = await asyncio.wait_for(connecting, _HANG_GUARD_S)
        assert manager._connect_stdio.await_count == 0, (
            "A connect queued before the cancelled disconnect still reached spawn"
        )
        assert len(errors) == 1 and _SUPERSEDED in errors[0], errors
        # The state the uncancelled ordering leaves: disconnected, lazy.
        assert manager._servers["srv"].status == ServerStatusEnum.LAZY
        assert manager._lazy_configs["srv"] is config
    finally:
        if manager._lifecycle_lock.locked():
            manager._lifecycle_lock.release()
        tasks = [connecting] + ([disconnecting] if disconnecting else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_codex_f002_an_abandoned_manager_refuses_process_adoption() -> None:
    manager = ClientManager()
    config = ResolvedServerConfig(
        name="srv", source="project", config=LocalMcpServerConfig(command="unused")
    )
    process = await asyncio.create_subprocess_exec(
        "sleep",
        "300",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    manager._send_initialize = AsyncMock()  # type: ignore[method-assign]
    manager._index_capabilities = AsyncMock(return_value=(0, 0, 0))  # type: ignore[method-assign]
    manager.abandon_all_now()
    try:
        with pytest.raises(_ManagerAbandoned, match="abandoned"):
            await manager.adopt_process("srv", process, config)
        assert "srv" not in manager._clients, (
            "adopt_process registered a client after terminal abandonment"
        )
        assert manager._background_tasks == set()
        # Left to the caller, which spawned it: still running here.
        assert process.returncode is None
    finally:
        _kill_groups([process.pid])
        await process.wait()


async def test_provisioning_kills_a_process_an_abandoned_manager_refuses() -> None:
    """`_finalize_server_ready` owns the process it hands over: on the
    refusal it marks the job failed, clears its process, and kills it."""
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools import handlers as handlers_module
    from pmcp.tools.handlers import GatewayTools

    manager = ClientManager()
    gateway = GatewayTools(client_manager=manager, policy_manager=PolicyManager())
    process = await asyncio.create_subprocess_exec(
        "sleep",
        "300",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    job = SimpleNamespace(
        id="job-1",
        server_name="srv",
        status="server_ready",
        progress=90,
        output_lines=["line"],
        started_at=0.0,
        process=process,
        error=None,
    )
    srv_config = ResolvedServerConfig(
        name="srv", source="project", config=LocalMcpServerConfig(command="unused")
    )
    gateway._register_provisioned_server = MagicMock()  # type: ignore[method-assign]
    manager.abandon_all_now()
    try:
        with (
            patch.object(
                handlers_module,
                "get_job_manager",
                lambda: SimpleNamespace(get_job=lambda _id: job),
            ),
            patch.object(
                handlers_module,
                "load_manifest",
                lambda: SimpleNamespace(
                    get_server=lambda _n: SimpleNamespace(env_var=None)
                ),
            ),
            patch.object(
                handlers_module, "manifest_server_to_config", lambda _c: srv_config
            ),
        ):
            status = await gateway.provision_status({"job_id": "job-1"})
        assert status.status == "failed", status
        assert "abandoned" in (status.error or ""), status
        assert job.status == "failed" and job.process is None
        assert await asyncio.wait_for(process.wait(), _HANG_GUARD_S) == -signal.SIGKILL
        assert "srv" not in manager._clients
        gateway._register_provisioned_server.assert_not_called()
    finally:
        _kill_groups([process.pid])
        await process.wait()


async def test_claude_f001_a_cancelled_disconnect_during_a_refresh_is_not_undone(
    tmp_path: Path,
) -> None:
    got = await _run_cell("refresh", "holder", "cancelled_disconnect", True, tmp_path)
    assert got["spawned_after"] == 0, "srv was respawned after its forced disconnect"
    assert "srv" not in got["end"][0]
    assert got["end"][1:] == ("lazy", True), got


async def test_a_callers_own_cancel_is_kept_when_it_lands_after_the_supersession(
    tmp_path: Path,
) -> None:
    """`connect_server`'s connect task is parked mid-spawn when a forced
    disconnect waiting for the lock is cancelled; its handler cancels that
    task, and in the very next step -- before the task has unwound -- the
    `connect_server` caller is cancelled too. The caller must end cancelled:
    its own cancel is not the supersession's refusal. (A cancel-message
    heuristic got this wrong on 3.10, where the task's message-bearing
    `CancelledError` reached the caller either way.)"""
    mgr = ClientManager()
    counter = tmp_path / "srv"
    cfg = _counted("srv", counter)
    try:
        with _instrumented(counter, park_spawn=True) as probe:
            connecting = asyncio.create_task(mgr.connect_server(cfg))
            await asyncio.wait_for(probe.parked.wait(), _HANG_GUARD_S)
            disconnecting = asyncio.create_task(
                mgr.disconnect_server("srv", force=True)
            )
            await _yields(3)
            assert not disconnecting.done()
            disconnecting.cancel()
            await asyncio.sleep(0)  # its handler runs: the connect task is cancelled
            assert disconnecting.done() and not connecting.done()
            connecting.cancel()
            kind, detail = await _outcome(connecting)
            assert kind == "cancelled", f"{kind} {detail!r}"
            probe.gate.set()
            await _yields(10)
        assert _spawned(counter) == [] and mgr._clients == {}
        assert not mgr._lifecycle_lock.locked()
    finally:
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))


async def test_a_cancelled_caller_still_stops_its_connect(tmp_path: Path) -> None:
    """The other direction, unchanged by the refusal reporting: cancelling the
    `connect_server` caller while its connect task is parked mid-spawn
    cancels that task too (as `await task` did), so nothing spawns once the
    spawn is released and no client is registered."""
    mgr = ClientManager()
    counter = tmp_path / "srv"
    cfg = _counted("srv", counter)
    try:
        with _instrumented(counter, park_spawn=True) as probe:
            connecting = asyncio.create_task(mgr.connect_server(cfg))
            await asyncio.wait_for(probe.parked.wait(), _HANG_GUARD_S)
            task = mgr._connect_tasks["srv"]
            connecting.cancel()
            kind, detail = await _outcome(connecting)
            assert kind == "cancelled", f"{kind} {detail!r}"
            probe.gate.set()
            await asyncio.wait_for(asyncio.wait({task}), _HANG_GUARD_S)
            assert task.cancelled()
            await _yields(10)
        assert _spawned(counter) == [] and mgr._clients == {}
        assert mgr._connect_tasks == {} and not mgr._lifecycle_lock.locked()
    finally:
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))


# --- what a supersession does not refuse ----------------------------------------


async def test_a_connect_requested_after_the_cancelled_disconnect_connects(
    tmp_path: Path,
) -> None:
    mgr = ClientManager()
    counter = tmp_path / "srv"
    cfg = _counted("srv", counter)
    await mgr._lifecycle_lock.acquire()
    stale = asyncio.create_task(mgr.connect_server(cfg))
    try:
        await _yields()
        disconnecting = asyncio.create_task(mgr.disconnect_server("srv", force=True))
        await _yields(3)
        disconnecting.cancel()
        assert (await _outcome(disconnecting))[0] == "cancelled"
        fresh = asyncio.create_task(mgr.connect_server(cfg))
        await _yields()
        mgr._lifecycle_lock.release()
        stale_errors = await asyncio.wait_for(stale, _HANG_GUARD_S)
        fresh_errors = await asyncio.wait_for(fresh, _HANG_GUARD_S)
        assert len(stale_errors) == 1 and _SUPERSEDED in stale_errors[0]
        assert fresh_errors == []
        assert mgr.is_server_online("srv") and len(_spawned(counter)) == 1
        # And an ordinary later connect cycle is unaffected.
        assert (await mgr.disconnect_server("srv"))[0]
        assert await mgr.connect_server(cfg) == []
        assert mgr.is_server_online("srv") and len(_spawned(counter)) == 2
    finally:
        if mgr._lifecycle_lock.locked():
            mgr._lifecycle_lock.release()
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))


async def test_another_servers_connect_is_not_superseded(tmp_path: Path) -> None:
    mgr = ClientManager()
    other_counter = tmp_path / "other"
    other = _counted("other", other_counter)
    await mgr._lifecycle_lock.acquire()
    connecting = asyncio.create_task(mgr.connect_server(other))
    try:
        await _yields()
        disconnecting = asyncio.create_task(mgr.disconnect_server("srv", force=True))
        await _yields(3)
        disconnecting.cancel()
        assert (await _outcome(disconnecting))[0] == "cancelled"
        mgr._lifecycle_lock.release()
        assert await asyncio.wait_for(connecting, _HANG_GUARD_S) == []
        assert mgr.is_server_online("other")
    finally:
        if mgr._lifecycle_lock.locked():
            mgr._lifecycle_lock.release()
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(other_counter))


# --- single flight ------------------------------------------------------------


async def test_a_stale_and_a_current_lazy_start_queued_together(
    tmp_path: Path,
) -> None:
    """Lazy start A is queued on the lock, a forced disconnect of srv is
    cancelled, then lazy start B queues. A takes the lock first and starts
    its connect task T (stale: it carries A's ticket), which queues behind B;
    B finds T registered and must start its own task rather than join T and
    be refused with it (the joiner rule). A is refused (False) without
    stamping ERROR; B connects srv, once."""
    mgr = ClientManager()
    counter = tmp_path / "srv"
    cfg = _counted("srv", counter)
    mgr.register_lazy_configs([cfg])
    await mgr._lifecycle_lock.acquire()
    a = asyncio.create_task(mgr.ensure_connected("srv"))
    try:
        await _yields()
        d = asyncio.create_task(mgr.disconnect_server("srv", force=True))
        await _yields(3)
        d.cancel()
        assert (await _outcome(d))[0] == "cancelled"
        b = asyncio.create_task(mgr.ensure_connected("srv"))
        await _yields()
        mgr._lifecycle_lock.release()
        assert await asyncio.wait_for(a, _HANG_GUARD_S) is False
        assert await asyncio.wait_for(b, _HANG_GUARD_S) is True
        assert mgr.is_server_online("srv") and len(_spawned(counter)) == 1
        assert mgr._servers["srv"].status == ServerStatusEnum.ONLINE
    finally:
        if mgr._lifecycle_lock.locked():
            mgr._lifecycle_lock.release()
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))


async def _hold(mgr: ClientManager, until: asyncio.Event) -> None:
    async with mgr._lifecycle_lock:
        await until.wait()


async def test_a_current_request_does_not_join_a_superseded_connect_task(
    tmp_path: Path,
) -> None:
    """The joiner rule. Lazy start A starts its connect task T, which queues
    on the lock; then srv is superseded and lazy start B (current) reaches
    `_connect_tasks` while T is still registered there. B must start its own
    task, not join T and be refused with it; and A's refusal must not stamp
    ERROR over B's ONLINE server.

    Here T exists BEFORE the supersession, and a cancelled disconnect would
    also cancel it (it is srv's per-name task), so the test bumps the
    generation alone (`_supersede_connects`) to show the rule holds for the
    generation by itself. The previous test reaches the rule through a real
    cancelled disconnect."""
    mgr = ClientManager()
    counter = tmp_path / "srv"
    cfg = _counted("srv", counter)
    mgr.register_lazy_configs([cfg])
    until = asyncio.Event()
    await mgr._lifecycle_lock.acquire()
    a = asyncio.create_task(mgr.ensure_connected("srv"))
    await _yields()
    # Queued behind A, so when A releases the lock after starting T, this
    # takes it ahead of T, and T stays queued.
    holder = asyncio.create_task(_hold(mgr, until))
    b: asyncio.Task[Any] | None = None
    try:
        await _yields()
        mgr._lifecycle_lock.release()
        await _yields(10)
        t = mgr._connect_tasks["srv"]
        assert not t.done() and mgr._lifecycle_lock.locked()
        mgr._supersede_connects("srv")
        b = asyncio.create_task(mgr.ensure_connected("srv"))
        await _yields()
        until.set()
        assert await asyncio.wait_for(a, _HANG_GUARD_S) is False
        assert await asyncio.wait_for(b, _HANG_GUARD_S) is True, _end_state(mgr)
        assert t.done()
        assert mgr.is_server_online("srv") and len(_spawned(counter)) == 1
        assert mgr._servers["srv"].status == ServerStatusEnum.ONLINE
    finally:
        until.set()
        await asyncio.wait_for(holder, _HANG_GUARD_S)
        if b is not None and not b.done():
            b.cancel()
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))


async def test_a_superseded_request_never_joins_a_current_connect_task(
    tmp_path: Path,
) -> None:
    """A stale reconnect (scheduled before the supersession) takes the lock
    while a current lazy start's connect task T waits for it. It must be
    refused: not join T -- which, holding the lock, it would wait on
    forever -- and not report T's connect as its own success."""
    mgr = ClientManager()
    counter = tmp_path / "srv"
    cfg = _counted("srv", counter)
    mgr.register_lazy_configs([cfg])
    refused: list[str] = []
    real_info = manager_mod.logger.info

    def noting_info(msg: Any, *args: Any, **kwargs: Any) -> None:
        if "reconnect stopped" in str(msg):
            refused.append(str(msg))
        real_info(msg, *args, **kwargs)

    await mgr._lifecycle_lock.acquire()
    current: asyncio.Task[Any] | None = None
    try:
        with (
            patch.object(manager_mod, "RECONNECT_DELAYS", (0.0, 0.0, 0.0)),
            patch.object(manager_mod.logger, "info", noting_info),
        ):
            mgr._schedule_reconnect("srv", cfg)  # stale: requested first
            reconnect = mgr._reconnect_tasks["srv"]
            mgr._supersede_connects("srv")
            current = asyncio.create_task(mgr.ensure_connected("srv"))
            # The reconnect's `sleep(0)` lets `current` queue on the lock
            # first; the reconnect queues behind it.
            await _yields()
            assert len(mgr._lifecycle_lock._waiters or ()) == 2
            assert not current.done() and not reconnect.done()
            mgr._lifecycle_lock.release()
            # `current` starts T and releases; the reconnect takes the lock
            # ahead of T (it queued first).
            assert await asyncio.wait_for(current, _HANG_GUARD_S) is True
            await asyncio.wait_for(asyncio.wait({reconnect}), _HANG_GUARD_S)
        assert len(refused) == 1 and _SUPERSEDED in refused[0], refused
        assert mgr.is_server_online("srv") and len(_spawned(counter)) == 1
    finally:
        if mgr._lifecycle_lock.locked() and current is not None and not current.done():
            mgr._lifecycle_lock.release()
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))


# --- the funnel ------------------------------------------------------------------


async def test_the_funnel_refuses_a_stale_ticket_without_spawning_or_retrying(
    tmp_path: Path,
) -> None:
    """Every attempt passes `_connect_server`; a request superseded while it
    runs is refused there, at once (no backoff), and nothing spawns."""
    mgr = ClientManager()
    counter = tmp_path / "srv"
    cfg = _counted("srv", counter)
    # A retried refusal would sleep an hour: the hang guard catches it.
    with patch.object(manager_mod, "RETRY_DELAYS", [3600.0] * 3):
        with mgr._connect_request():
            mgr._supersede_connects("srv")
            with pytest.raises(_ConnectSuperseded):
                await mgr._connect_server(cfg)
            with pytest.raises(_ConnectSuperseded):
                await asyncio.wait_for(mgr._connect_with_retry(cfg), _HANG_GUARD_S)
    assert _spawned(counter) == [] and mgr._clients == {}
    # Outside the superseded request, the same connect is admitted.
    try:
        await asyncio.wait_for(mgr._connect_server(cfg), _HANG_GUARD_S)
        assert mgr.is_server_online("srv")
    finally:
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))
