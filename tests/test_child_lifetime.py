"""No child outlives its owner (Consiliency/pmcp#388 round 9).

The class rule: no thread, descriptor or process pmcp creates outlives its
owner by more than a fixed bound, whatever the downstream does -- keeps a
pipe open, writes without pause, exits at once, or forks a grandchild.

One table: every spawn site pmcp drives in-process, against every hostile
behaviour. After the owner's teardown and :data:`BOUND` seconds, no process
the child or its descendants started remains, no drain thread remains, and
the descriptor count is back at its baseline.

POSIX only: the behaviours fork through process groups. The Windows paths
(Job Objects, the polling drain) have their own seams and real-host tests
in ``tests/test_child_output.py``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import textwrap
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/fd").exists(), reason="Linux /proc and process groups"
)

#: Seconds after the owner's teardown by which everything must be gone.
BOUND = 10.0

#: Each behaviour records every pid it starts in `piddir`.
_HOSTILE = textwrap.dedent(
    """
    import os, subprocess, sys, time
    mode, piddir, leader = {mode!r}, {piddir!r}, {leader!r}

    def note(pid):
        open(os.path.join(piddir, str(pid)), "w").close()

    note(os.getpid())
    open(leader, "w").write(str(os.getpid()))
    # What an installer's server prints when it is up: an install job hands
    # it off (`server_ready`); every other site ignores it.
    print("Server started on stdio", flush=True)
    if mode == "cancel":
        # Ignores SIGTERM (so every graceful wait really waits), and holds
        # the pipes through a grandchild that ignores it too.
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        child = subprocess.Popen([sys.executable, "-c",
            "import signal, time\\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n"
            "time.sleep(60)\\n"])
        note(child.pid)
        time.sleep(60)
    if mode in ("hold", "early"):
        # A grandchild that inherits stdout and stderr and keeps them open.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        note(child.pid)
        if mode == "early":
            sys.exit(0)
        time.sleep(60)
    elif mode == "flood":
        # A grandchild and the leader both writing without pause.
        child = subprocess.Popen([sys.executable, "-c",
            "import sys, time\\n"
            "while True:\\n"
            "    sys.stderr.write('e' * 4096 + '\\\\n'); sys.stderr.flush()\\n"
            "    time.sleep(0.001)\\n"])
        note(child.pid)
        while True:
            sys.stdout.write("o" * 4096 + "\\n")
            sys.stdout.flush()
            sys.stderr.write("e" * 4096 + "\\n")
            sys.stderr.flush()
            time.sleep(0.001)
    """
)

BEHAVIOURS = ("hold", "early", "flood", "cancel")

#: The owners whose teardown awaits the child's exit -- the `cancel`
#: dimension cancels that await. The rest kill synchronously (`owned()`,
#: the npm resolver), or are test harnesses a cancel would SIGKILL.
CANCELLABLE = (
    "install_server (timeout)",
    "install_server (cancelled)",
    "update probe",
    "ClientManager server",
    "install job (cancelled)",
    "handoff: adopted",
    "handoff: adoption fails",
    "handoff: adoption straddles the deadline",
)


def _script(tmp_path: Path, mode: str) -> tuple[Path, Path]:
    piddir = tmp_path / "pids"
    piddir.mkdir()
    script = tmp_path / "hostile.py"
    script.write_text(
        _HOSTILE.format(mode=mode, piddir=str(piddir), leader=str(tmp_path / "leader"))
    )
    return script, piddir


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


def _drains() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith("pmcp-errlog-")]


def _fds() -> int:
    return len(os.listdir("/proc/self/fd"))


async def _until(check: Callable[[], bool], within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if check():
            return True
        await asyncio.sleep(0.05)
    return check()


async def _short(module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The site's own timeout, shortened to one second."""
    from pmcp import waits

    real = waits.bounded_wait

    async def short(aw: Any, timeout: float | None) -> Any:
        return await real(aw, 1.0)

    monkeypatch.setattr(module, "bounded_wait", short)


def _cancel_every_exit_wait(mp: Any) -> None:
    """The `cancel` dimension (round 11, codex F001): every await on a
    child's exit made through `bounded_wait` -- a teardown's graceful wait --
    is cancelled instead. A kill that sits behind such an await is lost."""
    from pmcp import waits
    from pmcp.client import manager
    from pmcp.manifest import environment, installer
    from pmcp.tools import handlers

    real = waits.bounded_wait

    async def cancelling(aw: Any, timeout: float | None) -> Any:
        if getattr(aw, "__qualname__", "").endswith("Process.wait"):
            aw.close()
            raise asyncio.CancelledError
        return await real(aw, timeout)

    mp.setattr(waits, "bounded_wait", cancelling)
    for module in (manager, installer, handlers, environment):
        mp.setattr(module, "bounded_wait", cancelling)


async def _wait_started(piddir: Path, mode: str) -> None:
    want = 1 if mode == "flood" else 2
    await _until(lambda: len(list(piddir.iterdir())) >= want, 10.0)


# --- the owners: each drives one spawn site to its teardown -----------------------


async def _check_cli(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    from pmcp.manifest import environment

    await _short(environment, mp)
    await environment.check_cli("hostile", [sys.executable, str(script)])


async def _cli_help(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    from pmcp.manifest import environment

    await _short(environment, mp)
    await environment.get_cli_help("hostile", [sys.executable, str(script)])


def _server(script: Path) -> Any:
    from pmcp.manifest.loader import ServerConfig

    return ServerConfig(
        name="memory",
        description="memory",
        keywords=["memory"],
        install={"linux": [sys.executable, str(script)]},
        command=sys.executable,
        args=[str(script)],
        requires_api_key=False,
    )


async def _install(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    from pmcp.manifest import installer

    try:
        await installer.install_server(_server(script), "linux", timeout=1.0)
    except installer.InstallError:
        pass


async def _install_cancelled(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    from pmcp.manifest import installer

    task = asyncio.create_task(
        installer.install_server(_server(script), "linux", timeout=60.0)
    )
    await _wait_started(piddir, mode)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, installer.InstallError):
        pass


async def _verify(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    from pmcp.manifest import installer

    await _short(installer, mp)
    await installer.verify_installation(_server(script))


async def _update_probe(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    from pmcp.tools import handlers
    from pmcp.tools.handlers import GatewayTools

    await _short(handlers, mp)
    tools = GatewayTools.__new__(GatewayTools)
    try:
        await tools._run_update_probe_command([sys.executable, str(script)], env=None)
    except TimeoutError:
        pass


async def _server_teardown(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    """A downstream server whose handshake fails once it has started its
    behaviour: `ClientManager` tears it down."""
    from pmcp.client.manager import ClientManager
    from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

    mgr = ClientManager()

    async def failing_handshake(managed: Any) -> None:
        await _wait_started(piddir, mode)
        raise RuntimeError("handshake failed")

    mgr._send_initialize = failing_handshake  # type: ignore[method-assign]
    try:
        await mgr._connect_stdio(
            ResolvedServerConfig(
                name="hostile",
                source="project",
                config=LocalMcpServerConfig(command=sys.executable, args=[str(script)]),
            )
        )
    except RuntimeError:
        pass
    for task in list(mgr._background_tasks):
        task.cancel()


async def _install_job(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    """A background install job (`gateway.provision`), cancelled once its
    behaviour has started."""
    from pmcp.manifest import installer

    jobs = installer.JobManager()
    job_id = await jobs.start_install(_server(script), "linux")
    await _wait_started(piddir, mode)
    await jobs.cancel_job(job_id)
    monitor = jobs._jobs[job_id]._monitor_task
    if monitor is not None:
        await asyncio.gather(monitor, return_exceptions=True)


async def _errlog(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    """A server's stderr drain (`pmcp refresh`'s errlog): its child is killed
    by its own owner (the SDK); the drain must stop regardless."""
    import logging
    import subprocess

    from pmcp import child_output
    from pmcp.child_output import detached, kill_tree, track

    mp.setattr(child_output, "_DRAIN_JOIN_SECONDS", 0.5)
    async with child_output.drained_errlog("hostile", logging.getLogger("t")) as err:
        process = subprocess.Popen(
            [sys.executable, str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err,
            **detached(),
        )
        track(process)
        await _wait_started(piddir, mode)
        kill_tree(process)  # the SDK's own teardown of its child
        process.wait()


def _posix_peek(fd: int) -> int | None:
    """`PeekNamedPipe`'s answer from a POSIX pipe (the seam the Windows
    drain reads through): the bytes ready, or `None` at the pipe's end."""
    import fcntl
    import select
    import struct
    import termios

    ready, _, _ = select.select([fd], [], [], 0)
    if not ready:
        return 0
    count = struct.unpack("i", fcntl.ioctl(fd, termios.FIONREAD, b"\0\0\0\0"))[0]
    return count or None


async def _errlog_windows(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    """The same drain with the Windows reader (`PeekNamedPipe` polling and
    a stop event) through its seam (round 9, codex F001: a flood outran the
    stop)."""
    import logging
    import signal
    import subprocess

    from pmcp import child_output

    mp.setattr(child_output, "_DRAIN_JOIN_SECONDS", 0.5)
    mp.setattr(child_output, "_windows", lambda: True)
    mp.setattr(child_output, "_peek_pipe", _posix_peek)
    async with child_output.drained_errlog("hostile", logging.getLogger("t")) as err:
        process = subprocess.Popen(
            [sys.executable, str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err,
            start_new_session=True,
        )
        await _wait_started(piddir, mode)
    # The drain must have stopped while the writers still live; then the
    # owner of the processes (the SDK) ends them.
    assert await _until(lambda: not _drains_named("hostile"), BOUND), (
        "the Windows drain outlived its deadline while its writers lived"
    )
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _drains_named(label: str) -> list[str]:
    return [t for t in _drains() if t == f"pmcp-errlog-{label}"]


async def _handoff(script: Path, piddir: Path, mode: str, mp: Any, how: str) -> None:
    """A `server_ready` install handoff (round 10): the deadline expires
    with nobody adopting; ClientManager adopts it; adoption fails; or
    adoption straddles the deadline -- which must not kill a server being
    adopted. Then ClientManager, its owner, disconnects."""
    from pmcp.client.manager import ClientManager
    from pmcp.manifest import installer
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools

    deadline = 0.5 if how in ("expiry", "straddle") else 60.0
    mp.setattr(installer, "HANDOFF_DEADLINE_SECONDS", deadline)
    jobs = installer.JobManager()
    job_id = await jobs.start_install(_server(script), "linux")
    job = jobs._jobs[job_id]
    await _until(lambda: job.status not in ("pending", "installing"), 15)
    if how == "expiry":
        await _until(lambda: job.status != "server_ready", 10)
        return
    mgr = ClientManager()

    async def initialize(managed: Any) -> None:
        if how == "straddle":
            await asyncio.sleep(deadline * 3)  # the deadline passes meanwhile
        if how == "fails":
            raise RuntimeError("handshake failed")

    async def no_capabilities(managed: Any) -> tuple[int, int, int]:
        return 0, 0, 0

    mp.setattr(mgr, "_send_initialize", initialize)
    mp.setattr(mgr, "_index_capabilities", no_capabilities)
    tools = GatewayTools(client_manager=mgr, policy_manager=PolicyManager())
    try:
        if job.status == "server_ready":
            await tools._finalize_server_ready(job, job_id, 0.0)
            if how == "straddle" and mode != "early":
                leader = int((script.parent / "leader").read_text())
                assert job.status == "complete", job.error
                assert _alive(leader), "the deadline killed a server being adopted"
    finally:
        await mgr.disconnect_all()
        for task in list(mgr._background_tasks):
            task.cancel()


def _handoff_owner(how: str) -> Callable[[Path, Path, str, Any], Awaitable[None]]:
    async def owner(script: Path, piddir: Path, mode: str, mp: Any) -> None:
        await _handoff(script, piddir, mode, mp, how)

    return owner


async def _run_captured_timeout(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    import subprocess

    from pmcp.child_output import run_captured

    def run() -> None:
        try:
            run_captured([sys.executable, str(script)], timeout=1.0)
        except subprocess.TimeoutExpired:
            pass

    await asyncio.to_thread(run)


def _ctrl_c(runner: str) -> Callable[[Path, Path, str, Any], Awaitable[None]]:
    """The operator's Ctrl-C during `pmcp upgrade` (`run_echoed`) or a
    service check (`run_captured`), in a real process."""

    async def owner(script: Path, piddir: Path, mode: str, mp: Any) -> None:
        import signal
        import subprocess

        harness = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import faulthandler, os, signal, sys\n"
                "_trace = open(os.environ['PMCP_HARNESS_TRACE'], 'w')\n"
                "faulthandler.dump_traceback_later(20, file=_trace)\n"
                "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
                f"from pmcp.child_output import {runner}\n"
                "try:\n"
                f"    {runner}([sys.executable, {str(script)!r}])\n"
                "except KeyboardInterrupt:\n"
                "    pass\n",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "PMCP_HARNESS_TRACE": str(script.parent / "trace")},
        )
        try:
            await _wait_started(piddir, mode)
            os.kill(harness.pid, signal.SIGINT)
            try:
                await asyncio.to_thread(harness.wait, 30)
            except subprocess.TimeoutExpired:
                trace = script.parent / "trace"
                raise AssertionError(
                    "the harness did not exit; its stack at 20 s:\n"
                    + (trace.read_text() if trace.exists() else "(none)")
                ) from None
        finally:
            if harness.poll() is None:
                harness.kill()
                harness.wait()

    return owner


async def _npm_helper(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    """The npm resolver's long-lived helper, here a downstream that never
    completes the handshake: the resolver terminates it, then closes."""
    from pmcp.manifest.npm_resolver import NpmResolver

    shim = script.parent / "bin"
    shim.mkdir()
    node = shim / "node"
    node.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    node.chmod(0o755)
    mp.setenv("PATH", f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")
    resolver = NpmResolver()
    resolver._helper = script
    await asyncio.to_thread(resolver._spawn)
    resolver.close()


async def _refresh_child(script: Path, piddir: Path, mode: str, mp: Any) -> None:
    """`pmcp refresh`'s child itself (the MCP SDK spawns it), not only its
    drain: a downstream that never answers."""
    from pmcp.manifest import refresher

    async def no_version(*a: Any, **k: Any) -> tuple[None, None]:
        return None, None

    mp.setattr(refresher, "get_package_version", no_version)
    mp.setattr(refresher, "REFRESH_READ_TIMEOUT_SECONDS", 1.0)
    await refresher.refresh_server(_server(script), None, True)


OWNERS: dict[str, Callable[[Path, Path, str, Any], Awaitable[None]]] = {
    "check_cli": _check_cli,
    "get_cli_help": _cli_help,
    "install_server (timeout)": _install,
    "install_server (cancelled)": _install_cancelled,
    "verify_installation": _verify,
    "update probe": _update_probe,
    "ClientManager server": _server_teardown,
    "install job (cancelled)": _install_job,
    "stderr drain": _errlog,
    "stderr drain (Windows reader)": _errlog_windows,
    "handoff: deadline expires": _handoff_owner("expiry"),
    "handoff: adopted": _handoff_owner("adopted"),
    "handoff: adoption fails": _handoff_owner("fails"),
    "handoff: adoption straddles the deadline": _handoff_owner("straddle"),
    "run_captured (timeout)": _run_captured_timeout,
    "run_captured (Ctrl-C)": _ctrl_c("run_captured"),
    "run_echoed (Ctrl-C)": _ctrl_c("run_echoed"),
    "npm resolver helper": _npm_helper,
    "refresh child": _refresh_child,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", BEHAVIOURS)
@pytest.mark.parametrize("owner", list(OWNERS))
async def test_nothing_outlives_its_owner(
    owner: str, mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gc

    if mode == "cancel" and owner not in CANCELLABLE:
        pytest.skip("no await in this owner's teardown to cancel")
    script, piddir = _script(tmp_path, mode)
    if mode == "cancel":
        _cancel_every_exit_wait(monkeypatch)
    # Descriptors must be closed, not collected (round 10, codex F001): no
    # automatic garbage collection while the owner runs and is checked.
    gc.collect()
    gc.disable()
    baseline_fds = _fds()
    baseline_drains = set(_drains())
    try:
        try:
            await asyncio.wait_for(OWNERS[owner](script, piddir, mode, monkeypatch), 60)
        except asyncio.CancelledError:
            if mode != "cancel":
                raise  # only the cancel dimension's own cancellations end here
        pids = [int(p.name) for p in piddir.iterdir()]
        assert pids, "the behaviour never started"
        assert await _until(lambda: not any(_alive(p) for p in pids), BOUND), (
            f"{owner}/{mode}: a process outlived its owner: "
            f"{[p for p in pids if _alive(p)]}"
        )
        assert await _until(lambda: set(_drains()) <= baseline_drains, BOUND), (
            f"{owner}/{mode}: a drain thread outlived its owner: {_drains()}"
        )
        assert await _until(lambda: _fds() <= baseline_fds, BOUND), (
            f"{owner}/{mode}: descriptors outlived their owner: "
            f"{_fds()} > {baseline_fds}"
        )
    finally:
        gc.enable()
        import signal

        for entry in piddir.iterdir():
            pid = int(entry.name)
            if _alive(pid):
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as handle:
                        ours = str(script).encode() in handle.read() or b"time" in (
                            Path(f"/proc/{pid}/cmdline").read_bytes()
                        )
                except OSError:
                    ours = False
                if ours:
                    os.kill(pid, signal.SIGKILL)
