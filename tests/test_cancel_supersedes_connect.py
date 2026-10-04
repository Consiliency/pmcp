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

import ast
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
    ManagedClient,
    _ConnectSuperseded,
    _ManagerAbandoned,
)
from pmcp.types import (
    LocalMcpServerConfig,
    ResolvedServerConfig,
    ServerStatus,
    ServerStatusEnum,
)
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


ENTRIES = [
    "connect_server",
    "connect_all",
    "refresh",
    "ensure_connected",
    "reconnect",
    "restart",
]
# "holder": the entry point holds the lifecycle lock, with awaits still to
# come before it starts its per-name connect task. Only `refresh` and
# `restart_server` have such a window (their disconnect phase, in the same
# lock hold as their connect). `connect_server` and the reconnect loop
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
    if state != "holder" or entry in ("refresh", "restart")
]


@contextlib.contextmanager
def _instrumented(
    srv_counter: Path, *, park_spawn: bool, newer_counter: Path | None = None
) -> Iterator[SimpleNamespace]:
    """Short retry backoff, immediate reconnect, and events for: the connect
    entering its backoff, the srv spawn parked (when `park_spawn`), the
    newer connect's spawn parked (always, when `newer_counter`), and a group
    SIGTERM (a refresh in its disconnect phase)."""
    probe = SimpleNamespace(
        in_backoff=asyncio.Event(),
        parked=asyncio.Event(),
        gate=asyncio.Event(),
        newer_parked=asyncio.Event(),
        newer_gate=asyncio.Event(),
        terminating=asyncio.Event(),
    )
    real_exec = asyncio.create_subprocess_exec
    real_warning = manager_mod.logger.warning
    real_killpg = os.killpg

    async def gated_exec(*args: Any, **kwargs: Any) -> Any:
        if newer_counter is not None and str(newer_counter) in args:
            probe.newer_parked.set()
            await probe.newer_gate.wait()
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
    if entry == "restart":
        return asyncio.create_task(mgr.restart_server(srv))
    assert entry == "reconnect"
    mgr._schedule_reconnect("srv", srv)
    return mgr._reconnect_tasks["srv"]


async def _run_cell(
    entry: str,
    state: str,
    trigger: str,
    cancel: bool,
    tmp_path: Path,
    *,
    newer: bool = False,
) -> dict[str, Any]:
    """One ordering: `entry` reaches `state`, then the trigger. With
    `cancel=False` (cancelled_disconnect only) the disconnect is left to run:
    the uncancelled reference. With `newer`, a `connect_server(srv)` is
    requested right after the trigger, and its spawn is held open while the
    superseded request winds down, so that request resumes while the newer
    connect is in flight."""
    tag = f"{entry}-{state}-{trigger}-{'cancel' if cancel else 'ref'}"
    tag += "-newer" if newer else ""
    counter = tmp_path / f"{tag}.srv"
    newer_counter = tmp_path / f"{tag}.newer"
    slow_pids = tmp_path / f"{tag}.slow"
    mgr = ClientManager()
    srv = _counted("srv", counter, "fail-first" if state == "backoff" else "ok")
    cfgs = [srv]
    if state == "holder" and entry == "restart":
        # The restart's own server ignores SIGTERM: the restart holds the lock
        # in its terminate grace, before its connect phase.
        slow_srv = ResolvedServerConfig(
            name="srv",
            source="project",
            config=LocalMcpServerConfig(
                command=sys.executable, args=["-c", _MCP_SERVER, str(slow_pids)]
            ),
        )
        await asyncio.wait_for(mgr._connect_stdio(slow_srv), _HANG_GUARD_S)
    elif state == "holder":
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
    newer_task: asyncio.Task[Any] | None = None
    result: dict[str, Any] = {}
    try:
        with _instrumented(
            counter,
            park_spawn=state == "midspawn",
            newer_counter=newer_counter if newer else None,
        ) as probe:
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
                    # One step: the disconnect's handler runs (the
                    # supersession), and nothing else has resumed yet.
                    await asyncio.sleep(0)
                    assert disconnecting.done()
            if newer:
                # Requested at once, before the superseded request resumes.
                newer_task = asyncio.create_task(
                    mgr.connect_server(_counted("srv", newer_counter))
                )
            if disconnecting is not None and cancel:
                kind, detail = await _outcome(disconnecting)
                assert kind == "cancelled", f"{tag}: {kind} {detail!r}"
            if newer:
                await _yields(3)

            if held:
                mgr._lifecycle_lock.release()
                held = False
            probe.gate.set()
            if newer_task is not None:
                # The newer connect is mid-spawn: let everything else run.
                await asyncio.wait_for(probe.newer_parked.wait(), _HANG_GUARD_S)
                if state == "backoff":
                    await asyncio.sleep(0.75)
                await _yields(30)
                await asyncio.sleep(0.05)
                await _yields(30)
                probe.newer_gate.set()
                result["newer_outcome"] = await _outcome(newer_task)
            if disconnecting is not None and not cancel:
                kind, detail = await _outcome(disconnecting)
                assert kind == "returned" and detail[0], f"{tag}: {kind} {detail!r}"
            result["outcome"] = await _outcome(task)
            if state == "backoff":
                await asyncio.sleep(0.75)  # past the shortened backoff
            await _yields(10)
            result["spawned_after"] = len(_spawned(counter)) - spawned_before
            result["end"] = _end_state(mgr)
            if newer:
                result["newer_spawns"] = len(_spawned(newer_counter))
                live = mgr._clients["srv"].process if "srv" in mgr._clients else None
                follow = await asyncio.wait_for(
                    mgr.ensure_connected("srv"), _HANG_GUARD_S
                )
                result["follow"] = (
                    follow,
                    len(_spawned(newer_counter)),
                    mgr._clients["srv"].process is live,
                )
            if newer or "srv" not in mgr._clients:
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
        _kill_groups(_spawned(newer_counter))
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
    if entry == "restart":
        assert kind == "returned" and detail[0] is False, outcome
        assert len(detail[2]) == 1 and words in detail[2][0], outcome
    elif entry in ("connect_server", "connect_all", "refresh"):
        assert kind == "returned", (kind, detail)
        srv_errors = [e for e in detail if e.startswith("Failed to connect to srv:")]
        assert len(srv_errors) == 1 and words in srv_errors[0], detail
    elif entry == "ensure_connected":
        assert outcome == ("returned", False), outcome
    else:  # reconnect: a background task; it ends, by refusal or its cancel
        assert kind in ("returned", "cancelled"), outcome


# Lazy start's one pre-existing departure from request order, which the
# uncancelled reference shows and the cancelled path does not: it locks
# twice -- `ensure_connected` starts its connect task under one lock hold,
# and the task waits for the lock again. Uncancelled, a disconnect queued
# between the two runs first, and the lazy start then connects AFTER it
# although it was requested before it ("queued": the reference ends ONLINE).
# Cancelled, the disconnect supersedes the lazy start as it does every
# earlier request, and the server ends LAZY, lazily startable.


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
    if (entry, state) == ("ensure_connected", "queued"):
        # See the note above the test.
        assert got["end"] == (clients, "lazy", True), got
        assert reference["end"][:2] == (["srv"], "online"), reference
        return
    assert got["end"] == reference["end"], (got, reference)


NEWER_CELLS = [
    (entry, state)
    for entry in ENTRIES
    for state in STATES
    if state != "holder" or entry in ("refresh", "restart")
]


@pytest.mark.parametrize(("entry", "state"), NEWER_CELLS)
async def test_a_newer_connect_keeps_its_state_through_the_superseded_one(
    entry: str, state: str, tmp_path: Path
) -> None:
    """Claude round 3, F001, as a dimension of the matrix: a `connect_server`
    requested right after the cancelled disconnect is mid-spawn while the
    superseded request winds down. The superseded request must not write the
    server's state over it: the newer connect ends ONLINE and reported
    online, its lazy config popped, and the next lazy use keeps the live
    server instead of spawning another."""
    got = await _run_cell(
        entry, state, "cancelled_disconnect", True, tmp_path, newer=True
    )
    assert got["spawned_after"] == 0, got
    _assert_refused(entry, got["outcome"], _SUPERSEDED)
    assert got["newer_outcome"] == ("returned", []), got
    assert got["newer_spawns"] == 1, got
    assert "srv" in got["end"][0] and got["end"][1:] == ("online", False), got
    assert got["follow"] == (True, 1, True), got
    reference = await _run_cell(
        entry, state, "cancelled_disconnect", False, tmp_path, newer=True
    )
    assert reference["end"] == got["end"], (got, reference)


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
        # The refused reconnect started no task of its own: the lazy start's
        # task is still the latest, so the lazy start owns the state and pops
        # the lazy config of the server it brought online.
        assert "srv" not in mgr._lazy_configs
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


# --- claude round 3: who writes a server's state after an await -----------------


async def test_claude_r3_f001_a_superseded_lazy_start_does_not_settle_over_a_current_connect(
    tmp_path: Path,
) -> None:
    """The seat's falsifier, adapted: the lazy start's spawn is cancelled by
    the superseding disconnect, and a `connect_server` requested right after
    is mid-spawn when the lazy caller resumes. The lazy caller's settle must
    leave that connect's state alone."""
    mgr = ClientManager()
    counter = tmp_path / "srv"
    cfg = _counted("srv", counter)
    mgr.register_lazy_configs([cfg])
    real_exec = asyncio.create_subprocess_exec
    first_in = asyncio.Event()
    calls = 0

    async def slow_first_spawn(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:  # the lazy start's spawn: still in flight when cancelled
            first_in.set()
            await asyncio.Event().wait()
        return await real_exec(*args, **kwargs)

    try:
        with patch("asyncio.create_subprocess_exec", slow_first_spawn):
            lazy = asyncio.create_task(mgr.ensure_connected("srv"))
            await asyncio.wait_for(first_in.wait(), _HANG_GUARD_S)
            disconnecting = asyncio.create_task(
                mgr.disconnect_server("srv", force=True)
            )
            await asyncio.sleep(0)
            disconnecting.cancel()
            await asyncio.sleep(0)
            assert disconnecting.cancelled()
            connecting = asyncio.create_task(mgr.connect_server(cfg))
            errors = await asyncio.wait_for(connecting, _HANG_GUARD_S)
            assert await asyncio.wait_for(lazy, _HANG_GUARD_S) is False
        assert errors == []
        assert "srv" in mgr._clients
        assert mgr.is_server_online("srv"), mgr._servers["srv"].status
        assert "srv" not in mgr._lazy_configs
        live = mgr._clients["srv"].process
        assert await asyncio.wait_for(mgr.ensure_connected("srv"), _HANG_GUARD_S)
        assert mgr._clients["srv"].process is live, "a live server was respawned"
    finally:
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))


async def test_a_superseded_lazy_start_that_connected_does_not_pop_the_lazy_config(
    tmp_path: Path,
) -> None:
    """The success side (codex round 3 F003, real processes). A lazy start's
    connect succeeds, and before its caller gets the lock back, a forced
    disconnect is cancelled: its handler tears the client down and makes the
    server lazy again. The caller's success is refused at its settle (it
    reports False), and it must not pop that lazy config (it would leave a
    LAZY server that can never lazy-start)."""
    mgr = ClientManager()
    counter = tmp_path / "srv"
    cfg = _counted("srv", counter)
    mgr.register_lazy_configs([cfg])
    until = asyncio.Event()
    holder: asyncio.Task[Any] | None = None
    try:
        with _instrumented(counter, park_spawn=True) as probe:
            lazy = asyncio.create_task(mgr.ensure_connected("srv"))
            await asyncio.wait_for(probe.parked.wait(), _HANG_GUARD_S)
            # Queued behind the lazy task: it takes the lock as the connect
            # finishes, ahead of the lazy caller's settle.
            holder = asyncio.create_task(_hold(mgr, until))
            await _yields()
            probe.gate.set()
            await eventually(lambda: mgr.is_server_online("srv"), timeout=_HANG_GUARD_S)
            await eventually(lambda: until is not None and mgr._lifecycle_lock.locked())
            disconnecting = asyncio.create_task(
                mgr.disconnect_server("srv", force=True)
            )
            await _yields(3)
            disconnecting.cancel()
            assert (await _outcome(disconnecting))[0] == "cancelled"
            assert "srv" in mgr._lazy_configs  # the handler made it lazy again
            until.set()
            assert await asyncio.wait_for(lazy, _HANG_GUARD_S) is False
        assert mgr._clients == {}
        assert mgr._servers["srv"].status == ServerStatusEnum.LAZY
        assert "srv" in mgr._lazy_configs, "the superseded lazy start popped it"
    finally:
        until.set()
        if holder is not None:
            await asyncio.wait_for(holder, _HANG_GUARD_S)
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))


async def _two_lazy_starts_sharing_one_task(
    mgr: ClientManager, probe: SimpleNamespace
) -> tuple[asyncio.Task[Any], asyncio.Task[Any], asyncio.Task[None]]:
    """Lazy starts A and B queued on the lock: A starts its connect task T
    and releases; B takes the lock before T does and joins T; T then takes
    the lock and parks in its spawn."""
    await mgr._lifecycle_lock.acquire()
    a = asyncio.create_task(mgr.ensure_connected("srv"))
    await _yields()
    b = asyncio.create_task(mgr.ensure_connected("srv"))
    await _yields()
    mgr._lifecycle_lock.release()
    await asyncio.wait_for(probe.parked.wait(), _HANG_GUARD_S)
    shared = mgr._connect_tasks["srv"]
    assert mgr._connect_waiters.get(shared) == 2
    return a, b, shared


async def test_a_cancelled_joiner_does_not_cancel_a_shared_connect(
    tmp_path: Path,
) -> None:
    """Two lazy starts share one connect task. Cancelling the first one's
    caller must not cancel the connect the second still awaits (it returns
    at once, without waiting for it); cancelling both does."""
    mgr = ClientManager()
    counter = tmp_path / "srv"
    mgr.register_lazy_configs([_counted("srv", counter)])
    try:
        with _instrumented(counter, park_spawn=True) as probe:
            a, b, shared = await _two_lazy_starts_sharing_one_task(mgr, probe)
            a.cancel()
            assert (await _outcome(a))[0] == "cancelled"
            assert not shared.done(), "a joiner's own cancel cancelled the connect"
            probe.gate.set()
            assert await asyncio.wait_for(b, _HANG_GUARD_S) is True
        assert mgr.is_server_online("srv") and len(_spawned(counter)) == 1
        assert mgr._connect_tasks == {} and mgr._connect_waiters == {}
    finally:
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))

    mgr = ClientManager()
    counter = tmp_path / "srv2"
    mgr.register_lazy_configs([_counted("srv", counter)])
    try:
        with _instrumented(counter, park_spawn=True) as probe:
            a, b, shared = await _two_lazy_starts_sharing_one_task(mgr, probe)
            a.cancel()
            b.cancel()
            assert (await _outcome(a))[0] == "cancelled"
            assert (await _outcome(b))[0] == "cancelled"
            await asyncio.wait_for(asyncio.wait({shared}), _HANG_GUARD_S)
            assert shared.cancelled()
            probe.gate.set()
            await _yields(10)
        assert _spawned(counter) == [] and mgr._clients == {}
        assert mgr._connect_tasks == {} and mgr._connect_waiters == {}
    finally:
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))


async def test_a_lock_holder_never_joins_a_lazy_start_waiting_for_the_lock(
    tmp_path: Path,
) -> None:
    """Pre-existing deadlock in the same join: a lazy start's task waits for
    the lifecycle lock, so a `connect_server` that holds the lock and joins it
    waits forever. The lock holder starts its own connect; the lazy task then
    finds the server online and spawns nothing."""
    mgr = ClientManager()
    counter = tmp_path / "srv"
    cfg = _counted("srv", counter)
    mgr.register_lazy_configs([cfg])
    await mgr._lifecycle_lock.acquire()
    lazy = asyncio.create_task(mgr.ensure_connected("srv"))
    try:
        await _yields()
        connecting = asyncio.create_task(mgr.connect_server(cfg))
        await _yields()
        # Queue: lazy, then connect_server. The lazy start takes the lock,
        # starts its task and releases; connect_server takes it before that
        # task does.
        mgr._lifecycle_lock.release()
        assert await asyncio.wait_for(connecting, _HANG_GUARD_S) == []
        assert await asyncio.wait_for(lazy, _HANG_GUARD_S) is True
        assert mgr.is_server_online("srv") and len(_spawned(counter)) == 1
        assert "srv" not in mgr._lazy_configs
    finally:
        if mgr._lifecycle_lock.locked() and not lazy.done():
            mgr._lifecycle_lock.release()
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))


# Every write to `_servers`, `_lazy_configs` or `_connect_tasks` that a
# ClientManager coroutine makes after its first await -- directly, or through
# a synchronous method that (transitively) makes one -- goes through
# `_settle_request` or `_start_connect_task` under the lifecycle lock, except
# in these functions, each of which owns the state it writes.
_POST_AWAIT_WRITERS = {
    "_disconnect_server": "the cancelled pre-lock handler: the superseding "
    "disconnect's synchronous teardown, at the supersession, before any "
    "current request of the server exists",
    "_disconnect_server_locked": "the disconnect's own teardown, lock held",
    "disconnect_all": "a cancelled lock wait abandons every client (`abandon_all_now`)",
    "_disconnect_all_unlocked": "the wholesale teardown, lock held",
    "_cleanup_client": "the connect replacing an existing client tears it "
    "down, inside its own per-name task, lock held by its request",
    "_connect_stdio": "the admitted connect writes its own CONNECTING status, "
    "inside its per-name task, lock held by its request",
    "_connect_remote_stream": "as `_connect_stdio`",
    "adopt_process": "a failed adoption drops the client it registered",
}
_SANCTIONED = {"_settle_request", "_start_connect_task"}
# Called only with the lifecycle lock held by their caller.
_LOCK_HELD = {
    "_connect_singleflight",
    "_connect_server_locked",
    "_connect_all_unlocked",
}
_STATE_FIELDS = {"_servers", "_lazy_configs", "_connect_tasks"}
# A cancelled request settles in its cancellation handler, which cannot wait
# for the lock (#324: no await in a cancellation handler): the settle is one
# synchronous, ownership-checked step.
_CANCEL_SETTLES = {"_await_connect_task"}


def _manager_methods() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(Path(manager_mod.__file__).read_text())
    cls = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "ClientManager"
    )
    return {
        f.name: f
        for f in cls.body
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _self_attr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _on_state(node: ast.AST) -> bool:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        if _self_attr(node) and node.attr in _STATE_FIELDS:  # type: ignore[union-attr]
            return True
        node = node.value
    return False


def _direct_state_writes(fn: ast.AST) -> list[int]:
    lines = []
    for n in ast.walk(fn):
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(
                isinstance(t, (ast.Attribute, ast.Subscript)) and _on_state(t)
                for t in targets
            ):
                lines.append(n.lineno)
        elif (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr in {"pop", "clear", "update", "setdefault", "popitem"}
            and _on_state(n.func.value)
        ):
            lines.append(n.lineno)
        elif isinstance(n, ast.Delete) and any(_on_state(t) for t in n.targets):
            lines.append(n.lineno)
    return lines


def _self_calls(fn: ast.AST, names: set[str]) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and _self_attr(n.func) and n.func.attr in names  # type: ignore[union-attr]
    ]


def test_only_the_settle_writes_a_servers_state_after_an_await() -> None:
    methods = _manager_methods()
    writers = {
        name
        for name, f in methods.items()
        if isinstance(f, ast.FunctionDef)
        and name not in _SANCTIONED
        and _direct_state_writes(f)
    }
    grew = True
    while grew:
        grew = False
        for name, f in methods.items():
            if (
                isinstance(f, ast.FunctionDef)
                and name not in writers | _SANCTIONED
                and _self_calls(f, writers)
            ):
                writers.add(name)
                grew = True
    assert "_settle_disconnected" in writers  # the scan sees through helpers
    found = {}
    for name, f in methods.items():
        if not isinstance(f, ast.AsyncFunctionDef):
            continue
        awaits = [
            n.lineno
            for n in ast.walk(f)
            if isinstance(n, (ast.Await, ast.AsyncWith, ast.AsyncFor))
        ]
        if not awaits:
            continue
        first = min(awaits)
        late = [line for line in _direct_state_writes(f) if line > first]
        late += [c.lineno for c in _self_calls(f, writers) if c.lineno > first]
        if late:
            found[name] = sorted(set(late))
    assert set(found) == set(_POST_AWAIT_WRITERS), found


def _under_lifecycle_lock(fn: ast.AST, call: ast.Call) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.AsyncWith) and any(
            ast.unparse(item.context_expr) == "self._lifecycle_lock"
            for item in node.items
        ):
            if any(n is call for n in ast.walk(node)):
                return True
    return False


def test_every_settle_runs_under_the_lifecycle_lock() -> None:
    methods = _manager_methods()
    calls = []
    for name, f in methods.items():
        for call in _self_calls(f, _SANCTIONED):
            calls.append((name, call.func.attr, call.lineno))  # type: ignore[attr-defined]
            if name in _LOCK_HELD or name in _CANCEL_SETTLES:
                continue
            assert _under_lifecycle_lock(f, call), (
                f"{name}:{call.lineno} calls {call.func.attr} outside the lock"  # type: ignore[attr-defined]
            )
    settled_in = {name for name, attr, _ in calls if attr == "_settle_request"}
    assert settled_in == {
        "_await_connect_task",
        "_connect_all_unlocked",
        "_ensure_connected_requested",
        "_connect_server_locked",
        "_reconnect_attempts",
    }, calls


# --- the round-3 board: grok F001, codex F001-F003 -------------------------------


async def test_grok_r3_f001_a_caller_cancelled_after_the_supersession_settles(
    tmp_path: Path,
) -> None:
    """The caller of a superseded connect is cancelled itself, in the step
    after the cancelled disconnect: it stays cancelled, and the server is
    still settled (LAZY, lazily startable), not left CONNECTING."""
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
            await asyncio.sleep(0)
            assert disconnecting.done() and not connecting.done()
            connecting.cancel()
            kind, detail = await _outcome(connecting)
            assert kind == "cancelled", f"{kind} {detail!r}"
            probe.gate.set()
            await _yields(10)
        assert _spawned(counter) == [] and mgr._clients == {}
        assert not mgr._lifecycle_lock.locked()
        assert mgr._servers["srv"].status == ServerStatusEnum.LAZY
        assert mgr._lazy_configs["srv"] is cfg
        assert await asyncio.wait_for(mgr.ensure_connected("srv"), _HANG_GUARD_S)
        assert mgr.is_server_online("srv")
    finally:
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))


async def test_codex_r3_f001_a_callers_cancel_tears_down_before_it_propagates() -> None:
    """The caller's own cancel lands mid-handshake, with the client
    registered: when the caller catches it, the client is already gone and
    its process tree already killed -- not on the connect task's next step."""
    mgr = ClientManager()
    config = ResolvedServerConfig(
        name="srv", source="project", config=LocalMcpServerConfig(command="unused")
    )
    process = MagicMock(returncode=None, stderr=None)
    parked = asyncio.Event()

    async def initialize(managed: Any) -> None:
        parked.set()
        await asyncio.Event().wait()

    mgr._send_initialize = initialize  # type: ignore[method-assign]
    mgr._read_stdout = AsyncMock()  # type: ignore[method-assign]
    seen: list[str] = []

    async def caller() -> None:
        try:
            await mgr.connect_server(config, retry=False)
        except asyncio.CancelledError:
            seen.append("srv" in mgr._clients and "registered" or "gone")
            seen.append(kill.called and "killed" or "alive")
            raise
        seen.append("swallowed")

    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        patch.object(manager_mod, "_kill_process_tree_now") as kill,
    ):
        task = asyncio.create_task(caller())
        try:
            await asyncio.wait_for(parked.wait(), _HANG_GUARD_S)
            task.cancel()
            assert (await _outcome(task))[0] == "cancelled"
            assert seen == ["gone", "killed"], seen
        finally:
            mgr.abandon_all_now()
            await asyncio.gather(*list(mgr._background_tasks), return_exceptions=True)


async def test_codex_r3_f002_a_restart_requested_before_a_cancelled_disconnect_is_superseded() -> (
    None
):
    mgr = ClientManager()
    config = ResolvedServerConfig(
        name="srv", source="project", config=LocalMcpServerConfig(command="unused")
    )
    status = ServerStatus(name="srv", status=ServerStatusEnum.ONLINE, tool_count=0)
    mgr._clients["srv"] = ManagedClient(
        config=config, process=MagicMock(returncode=None), status=status
    )
    mgr._servers["srv"] = status
    entered, release = asyncio.Event(), asyncio.Event()
    spawn = AsyncMock()
    mgr._connect_stdio = spawn  # type: ignore[method-assign]

    async def terminate(*args: Any, **kwargs: Any) -> None:
        entered.set()
        await release.wait()

    with (
        patch.object(manager_mod, "_terminate_process_tree", terminate),
        patch.object(manager_mod, "_kill_process_tree_now"),
    ):
        restarting = asyncio.create_task(mgr.restart_server(config, force=True))
        try:
            await asyncio.wait_for(entered.wait(), _HANG_GUARD_S)
            disconnecting = asyncio.create_task(
                mgr.disconnect_server("srv", force=True)
            )
            await asyncio.sleep(0)
            assert not disconnecting.done()
            disconnecting.cancel()
            (outcome,) = await asyncio.gather(disconnecting, return_exceptions=True)
            assert isinstance(outcome, asyncio.CancelledError)
            release.set()
            ok, _cancelled, errors = await asyncio.wait_for(restarting, _HANG_GUARD_S)
            spawn.assert_not_awaited()
            assert not ok and len(errors) == 1 and _SUPERSEDED in errors[0], errors
        finally:
            release.set()
            restarting.cancel()
            await asyncio.gather(restarting, return_exceptions=True)
            mgr.abandon_all_now()
            await asyncio.gather(*list(mgr._background_tasks), return_exceptions=True)


async def test_codex_r3_f003_a_completed_lazy_connect_cannot_undo_a_cancelled_disconnect() -> (
    None
):
    mgr = ClientManager()
    config = ResolvedServerConfig(
        name="srv", source="project", config=LocalMcpServerConfig(command="unused")
    )
    mgr.register_lazy_configs([config])
    process = MagicMock(returncode=None, stderr=None)
    entered, release = asyncio.Event(), asyncio.Event()
    mgr._send_initialize = AsyncMock()  # type: ignore[method-assign]
    mgr._read_stdout = AsyncMock()  # type: ignore[method-assign]

    async def index(managed: Any) -> tuple[int, int, int]:
        entered.set()
        await release.wait()
        return (0, 0, 0)

    mgr._index_capabilities = index  # type: ignore[method-assign]
    with (
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
        patch.object(manager_mod, "_kill_process_tree_now"),
    ):
        connecting = asyncio.create_task(mgr.ensure_connected("srv"))
        try:
            await asyncio.wait_for(entered.wait(), _HANG_GUARD_S)
            disconnecting = asyncio.create_task(
                mgr.disconnect_server("srv", force=True)
            )
            await asyncio.sleep(0)
            assert not disconnecting.done()
            release.set()
            asyncio.get_running_loop().call_soon(disconnecting.cancel)
            result = await asyncio.wait_for(connecting, _HANG_GUARD_S)
            (outcome,) = await asyncio.gather(disconnecting, return_exceptions=True)
            assert isinstance(outcome, asyncio.CancelledError)
            assert "srv" not in mgr._clients
            assert result is False and mgr._lazy_configs.get("srv") is config, (
                result,
                sorted(mgr._lazy_configs),
            )
        finally:
            release.set()
            connecting.cancel()
            await asyncio.gather(connecting, return_exceptions=True)
            mgr.abandon_all_now()
            await asyncio.gather(*list(mgr._background_tasks), return_exceptions=True)


# --- timing: when the child finishes and when the caller is cancelled ------------
#
# The supersession is one synchronous step (the cancelled disconnect's
# handler), so nothing finishes or is cancelled "during" it. The orderings that
# exist, relative to that step:
#
# * the child is still in flight: it is cancelled by the handler (the matrix's
#   "midspawn" and "backoff" states);
# * the child has finished its last step, and its request has not yet
#   resumed: "child_done_before" below;
# * the caller is cancelled before the disconnect: a plain cancel, nothing
#   superseded (`test_a_cancelled_caller_still_stops_its_connect`);
# * the caller is cancelled in the step after the handler, before its connect
#   task unwinds: "caller_cancelled_after" below.

TIMING_ENTRIES = [
    "connect_server",
    "connect_all",
    "refresh",
    "ensure_connected",
    "reconnect",
    "restart",
]
TIMINGS = ["child_done_before", "caller_cancelled_after"]


def _start_entry(mgr: ClientManager, entry: str, cfg: ResolvedServerConfig) -> Any:
    return _start(mgr, entry, [cfg])


def _entry_refused(entry: str, outcome: tuple[str, Any]) -> bool:
    kind, _detail = outcome
    if entry == "reconnect":
        return kind in ("returned", "cancelled")
    try:
        _assert_refused(entry, outcome, _SUPERSEDED)
    except AssertionError:
        return False
    return True


async def _timing_cell(
    entry: str, timing: str, cancel: bool, tmp_path: Path
) -> dict[str, Any]:
    tag = f"{entry}-{timing}-{'cancel' if cancel else 'ref'}"
    counter = tmp_path / f"{tag}.srv"
    cfg = _counted("srv", counter)
    mgr = ClientManager()
    if entry == "ensure_connected":
        mgr.register_lazy_configs([cfg])
    result: dict[str, Any] = {}
    task: Any = None
    disconnecting: asyncio.Task[Any] | None = None
    real_index = mgr._index_capabilities
    entered, go = asyncio.Event(), asyncio.Event()

    async def index_then_supersede(managed: Any) -> tuple[int, int, int]:
        # The child's last await; after it, the cancel of the queued
        # disconnect is delivered in the same step the child finishes.
        entered.set()
        await go.wait()
        indexed = await real_index(managed)
        if cancel and disconnecting is not None:
            disconnecting.cancel()
        return indexed

    try:
        with _instrumented(
            counter, park_spawn=timing == "caller_cancelled_after"
        ) as probe:
            if timing == "child_done_before":
                mgr._index_capabilities = index_then_supersede  # type: ignore[method-assign]
            task = _start_entry(mgr, entry, cfg)
            if timing == "child_done_before":
                await asyncio.wait_for(entered.wait(), _HANG_GUARD_S)
            else:
                await asyncio.wait_for(probe.parked.wait(), _HANG_GUARD_S)
            disconnecting = asyncio.create_task(
                mgr.disconnect_server("srv", force=True)
            )
            await _yields(3)
            assert not disconnecting.done()
            if timing == "child_done_before":
                go.set()
            else:
                disconnecting.cancel()
                await asyncio.sleep(0)
                assert disconnecting.done()
                if entry != "reconnect":
                    task.cancel()
                probe.gate.set()
            result["outcome"] = await _outcome(task)
            result["disconnect"] = (await _outcome(disconnecting))[0]
            await _yields(10)
            result["end"] = _end_state(mgr)
            result["lazy_is_cfg"] = mgr._lazy_configs.get("srv") is cfg
            await eventually(
                lambda: "srv" in mgr._clients
                or not any(_alive(p) for p in _spawned(counter)),
                timeout=_HANG_GUARD_S,
                message=f"{tag}: a srv process survived",
            )
            result["spawned"] = len(_spawned(counter))
            if "srv" not in mgr._clients:
                result["restarts"] = await asyncio.wait_for(
                    mgr.ensure_connected("srv"), _HANG_GUARD_S
                )
    finally:
        if task is not None and not task.done():
            task.cancel()
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))
    return result


@pytest.mark.parametrize(
    ("entry", "timing"), [(e, t) for e in TIMING_ENTRIES for t in TIMINGS]
)
async def test_supersession_timing(entry: str, timing: str, tmp_path: Path) -> None:
    got = await _timing_cell(entry, timing, True, tmp_path)
    assert got["disconnect"] == "cancelled", got
    assert got["end"] == ([], "lazy", True) and got["lazy_is_cfg"], got
    assert got["restarts"] is True, got  # lazily startable afterwards
    if timing == "caller_cancelled_after":
        assert got["spawned"] == 0, got
        if entry != "reconnect":
            assert got["outcome"][0] == "cancelled", got
        return
    assert got["spawned"] == 1, got
    assert _entry_refused(entry, got["outcome"]), got
    reference = await _timing_cell(entry, timing, False, tmp_path)
    assert reference["end"] == got["end"], (got, reference)


# --- composites: one ticket per lock hold ----------------------------------------
#
# A connect's ticket is captured when it is requested, before it waits for
# the lock. A composite that disconnects and then connects inside ONE lock
# hold (`refresh`, `restart_server`) is one request with one ticket. A
# composite that releases the lock between its phases (the gateway's
# `refresh`/`update_server`/provisioning handlers, the CLI) makes separate
# requests, and each phase's ticket is captured at its own request: that is
# the uncancelled ordering, in which a disconnect queued between the phases
# runs before the later connect, and the later connect wins.

_PUBLIC_CONNECTS = {
    "connect_all",
    "connect_server",
    "refresh",
    "ensure_connected",
    "restart_server",
}
_DISCONNECT_PHASES = {
    "_disconnect_all_unlocked",
    "_disconnect_server_locked",
    "disconnect_server",
    "disconnect_all",
}
_CONNECT_PHASES = {
    "_connect_all_unlocked",
    "_connect_server_locked",
    "_connect_singleflight",
    "connect_server",
    "connect_all",
}


def _awaited_self_calls(fn: ast.AST, names: set[str]) -> list[ast.Await]:
    return [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Await)
        and isinstance(n.value, ast.Call)
        and _self_attr(n.value.func)
        and n.value.func.attr in names  # type: ignore[attr-defined]
    ]


def _lock_holds(fn: ast.AST) -> list[ast.AST]:
    """`async with self._lifecycle_lock` blocks, and `try` blocks whose
    `finally` releases it (an explicit `acquire()` before them)."""
    holds: list[ast.AST] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.AsyncWith) and any(
            ast.unparse(i.context_expr) == "self._lifecycle_lock" for i in node.items
        ):
            holds.append(node)
        elif isinstance(node, ast.Try) and any(
            "self._lifecycle_lock.release()" in ast.unparse(stmt)
            for stmt in node.finalbody
        ):
            holds.append(ast.Module(body=node.body, type_ignores=[]))
    return holds


def test_every_public_connect_captures_its_ticket_before_its_first_await() -> None:
    methods = _manager_methods()
    for name in _PUBLIC_CONNECTS:
        fn = methods[name]
        scopes = [
            node
            for node in ast.walk(fn)
            if isinstance(node, ast.With)
            and any(
                ast.unparse(i.context_expr) == "self._connect_request()"
                for i in node.items
            )
        ]
        awaits = [n for n in ast.walk(fn) if isinstance(n, (ast.Await, ast.AsyncWith))]
        assert awaits, name
        uncovered = [
            a.lineno
            for a in awaits
            if not any(any(n is a for n in ast.walk(scope)) for scope in scopes)
        ]
        assert uncovered == [], (
            f"{name}: awaits outside its connect request {uncovered}"
        )


def test_a_composite_connects_in_the_same_lock_hold_as_its_disconnect() -> None:
    methods = _manager_methods()
    composites = {}
    for name, fn in methods.items():
        disconnects = _awaited_self_calls(fn, _DISCONNECT_PHASES)
        connects = [
            c
            for c in _awaited_self_calls(fn, _CONNECT_PHASES)
            if any(d.lineno < c.lineno for d in disconnects)
        ]
        if connects:
            composites[name] = (disconnects, connects)
    assert set(composites) == {"refresh", "_disconnect_server"}, sorted(composites)
    for name, (disconnects, connects) in composites.items():
        holds = _lock_holds(methods[name])
        for c in connects:
            for d in (d for d in disconnects if d.lineno < c.lineno):
                assert any(
                    any(n is c for n in ast.walk(h))
                    and any(n is d for n in ast.walk(h))
                    for h in holds
                ), f"{name}: line {d.lineno} and {c.lineno} are in different lock holds"
    # restart_server is that composite, as one request.
    restart = methods["restart_server"]
    assert _awaited_self_calls(restart, {"_disconnect_server"})
    assert not _awaited_self_calls(restart, _PUBLIC_CONNECTS | _CONNECT_PHASES)


def test_callers_outside_the_manager_use_only_its_public_connects() -> None:
    pkg = Path(manager_mod.__file__).resolve().parents[1]
    found = []
    for rel in ("tools/handlers.py", "server.py", "cli.py"):
        tree = ast.parse((pkg / rel).read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr.startswith(("_connect", "_disconnect", "_admit"))
                and "manager" in ast.unparse(node.value)
            ):
                found.append(f"{rel}:{node.lineno} {ast.unparse(node)}")
    assert found == [], found


async def test_a_superseded_lazy_caller_cancelled_mid_newer_connect_leaves_it_alone(
    tmp_path: Path,
) -> None:
    """Claude round 3 F001 with the superseded lazy caller cancelled itself:
    its settle then runs in its cancellation handler, without the lock, so
    only the ownership check keeps it off the newer connect's state. The
    steps are pinned: the disconnect's handler cancels the lazy task T; the
    newer `connect_server` C starts its task T2 as T releases the lock; the
    lazy caller is cancelled (a done-callback on T) before it resumes; T2
    reaches its spawn and writes CONNECTING; then the lazy caller settles."""
    mgr = ClientManager()
    counter = tmp_path / "srv"
    newer_counter = tmp_path / "newer"
    cfg = _counted("srv", counter)
    mgr.register_lazy_configs([cfg])
    try:
        with _instrumented(
            counter, park_spawn=True, newer_counter=newer_counter
        ) as probe:
            lazy = asyncio.create_task(mgr.ensure_connected("srv"))
            await asyncio.wait_for(probe.parked.wait(), _HANG_GUARD_S)
            lazy_task = mgr._connect_tasks["srv"]
            lazy_task.add_done_callback(lambda _t: lazy.cancel())
            disconnecting = asyncio.create_task(
                mgr.disconnect_server("srv", force=True)
            )
            await _yields(3)
            disconnecting.cancel()
            await asyncio.sleep(0)
            assert disconnecting.done() and not lazy_task.done()
            connecting = asyncio.create_task(
                mgr.connect_server(_counted("srv", newer_counter))
            )
            assert (await _outcome(lazy))[0] == "cancelled"
            assert probe.newer_parked.is_set()
            probe.newer_gate.set()
            probe.gate.set()
            assert await asyncio.wait_for(connecting, _HANG_GUARD_S) == []
        assert mgr.is_server_online("srv"), _end_state(mgr)
        assert "srv" not in mgr._lazy_configs
        assert _spawned(counter) == [] and len(_spawned(newer_counter)) == 1
    finally:
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(counter))
        _kill_groups(_spawned(newer_counter))


async def test_a_failed_lazy_start_does_not_stamp_error_over_a_newer_connect(
    tmp_path: Path,
) -> None:
    """A lazy start fails (every attempt), and a `connect_server` queued
    behind its connect task brings the server ONLINE before the lazy caller
    gets the lock back to settle. The lazy caller no longer owns the state:
    it must not stamp ERROR over the ONLINE server."""
    mgr = ClientManager()
    bad = tmp_path / "bad"
    good = tmp_path / "good"
    failing = ResolvedServerConfig(
        name="srv",
        source="project",
        config=LocalMcpServerConfig(
            command=sys.executable, args=["-c", "import sys; sys.exit(1)"]
        ),
    )
    mgr.register_lazy_configs([failing])
    try:
        with patch.object(manager_mod, "RETRY_DELAYS", [0.05, 0.05, 0.05]):
            lazy = asyncio.create_task(mgr.ensure_connected("srv"))
            await eventually(lambda: "srv" in mgr._connect_tasks, timeout=_HANG_GUARD_S)
            await eventually(
                lambda: mgr._lifecycle_lock.locked(), timeout=_HANG_GUARD_S
            )
            connecting = asyncio.create_task(mgr.connect_server(_counted("srv", good)))
            assert await asyncio.wait_for(lazy, _HANG_GUARD_S) is False
            assert await asyncio.wait_for(connecting, _HANG_GUARD_S) == []
        assert mgr.is_server_online("srv"), _end_state(mgr)
        assert mgr._servers["srv"].last_error is None
    finally:
        await asyncio.wait_for(mgr.disconnect_all(), _HANG_GUARD_S)
        _kill_groups(_spawned(good))
        _kill_groups(_spawned(bad))
