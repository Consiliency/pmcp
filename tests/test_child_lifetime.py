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
    mode, piddir = {mode!r}, {piddir!r}

    def note(pid):
        open(os.path.join(piddir, str(pid)), "w").close()

    note(os.getpid())
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

BEHAVIOURS = ("hold", "early", "flood")


def _script(tmp_path: Path, mode: str) -> tuple[Path, Path]:
    piddir = tmp_path / "pids"
    piddir.mkdir()
    script = tmp_path / "hostile.py"
    script.write_text(_HOSTILE.format(mode=mode, piddir=str(piddir)))
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
}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", BEHAVIOURS)
@pytest.mark.parametrize("owner", list(OWNERS))
async def test_nothing_outlives_its_owner(
    owner: str, mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script, piddir = _script(tmp_path, mode)
    baseline_fds = _fds()
    baseline_drains = set(_drains())
    try:
        await asyncio.wait_for(OWNERS[owner](script, piddir, mode, monkeypatch), 60)
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
