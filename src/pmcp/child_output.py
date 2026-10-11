"""A child process never writes to the operator's terminal directly.

Consiliency/pmcp#388 round 4 (grok F001, claude N-B): a child that inherits
pmcp's stdout or stderr file descriptor writes straight to the terminal,
past every escape pmcp applies to its own output. A repository's manifest
server reached it through ``pmcp refresh`` (the MCP SDK's ``stdio_client``
passes its ``errlog`` to the child), and ``pmcp upgrade``'s installer and the
service restart inherited both streams.

Every child pmcp starts gets pipes instead. Its output is read line by line
and either logged (a server's stderr: :func:`drained_errlog`) or echoed to
the operator escaped (:func:`run_echoed`). ``tests/test_child_output.py``
pins that no spawn in ``src/pmcp`` inherits a stream.
"""

from __future__ import annotations

import asyncio.subprocess
import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import IO, Any, TextIO

from pmcp.display import display_safe


def detached() -> dict[str, Any]:
    """Spawn options that detach a child from pmcp's controlling terminal
    (Consiliency/pmcp#388 round 5): a new session on POSIX, so the child
    has no controlling terminal and opening ``/dev/tty`` fails; on Windows a
    new process group with no console. Every spawn in ``src/pmcp`` passes
    these (``**detached()``), with stdin ``DEVNULL`` or a pipe it owns
    (``tests/test_child_output.py`` pins all three properties)."""
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
        return {"creationflags": flags}
    return {"start_new_session": True}


#: How long, after the child is done, the drain may take to reach the end of
#: the pipe: a grandchild that kept the pipe open does not hold pmcp up.
_DRAIN_JOIN_SECONDS = 5.0

#: The longest line logged or echoed, in bytes, as ``ClientManager``'s own
#: stderr reader keeps it; the rest of a longer line is dropped with a
#: value-free note (Consiliency/pmcp#388 round 5, claude N-B: an unbounded
#: line buffered 223 MiB for 32 MiB of input).
LINE_CAP = 64 * 1024
_CHUNK = 64 * 1024
TRUNCATED = " (truncated)"


def bounded_lines(stream: IO[bytes]) -> Iterator[tuple[bytes, bool]]:
    """``(line, truncated)`` for each line ``stream`` holds, read in chunks:
    at most :data:`LINE_CAP` bytes of a line are kept, and memory stays
    near one cap plus one chunk however long a line is."""
    read = getattr(stream, "read1", None) or stream.read
    pending = bytearray()
    dropping = False
    while True:
        chunk = read(_CHUNK)
        if not chunk:
            break
        pending += chunk
        while True:
            end = pending.find(b"\n")
            if end < 0:
                if len(pending) > LINE_CAP:
                    if not dropping:
                        yield bytes(pending[:LINE_CAP]), True
                    dropping = True
                    del pending[:]
                break
            line = bytes(pending[:end])
            del pending[: end + 1]
            if dropping:
                dropping = False  # the end of a line already reported
                continue
            if len(line) > LINE_CAP:
                yield line[:LINE_CAP], True
            else:
                yield line, False
    if pending and not dropping:
        yield bytes(pending[:LINE_CAP]), len(pending) > LINE_CAP


def _render(raw: bytes, truncated: bool) -> str:
    text = display_safe(raw.decode("utf-8", "replace").rstrip("\r"), keep="\t")
    return text + TRUNCATED if truncated else text


#: How long a terminal interrupt gives a child to exit before its group is
#: killed (:func:`interrupt_tree`).
_INTERRUPT_GRACE_SECONDS = 5.0


# --- the one kill rule (Consiliency/pmcp#388 rounds 6-7) ----------------------
#
# Every child pmcp starts is detached (:func:`detached`): on POSIX it leads a
# process group whose id is its pid; on Windows it is put in a Job Object
# (:func:`track`). Every kill site goes through :func:`kill_process_tree_now`
# -- `ClientManager` and the update probe as well (``_kill_process_tree_now``
# there is this function) -- so one rule decides when a group may be
# signalled:
#
# * while the leader is alive or an unreaped zombie (:func:`leader_reserved`
#   -- `returncode` unset AND the kernel still holds it as our child, since on
#   3.10/3.11 asyncio's watcher thread reaps before `returncode` is set), its
#   pid reserves the group id: the group is signalled directly;
# * once the leader is reaped, only through :func:`signal_retained_group`,
#   which signals the group only while a member remains and no process holds
#   the leader's pid -- so a reused pid is never signalled, and descendants
#   of a leader that exited first are still reached (round 7, codex F002).


def process_groups_supported() -> bool:
    """Whether this platform has POSIX process groups to signal. Read at
    call time, so a test that removes the APIs sees it."""
    return hasattr(os, "killpg") and hasattr(os, "getpgid")


def _windows() -> bool:
    return os.name == "nt"


def _is_child(process: Any) -> bool:
    return isinstance(process, (subprocess.Popen, asyncio.subprocess.Process))


def leader_reserved(process: Any) -> bool:
    """Whether ``process`` is alive or an unreaped zombie of ours -- so its
    pid, and the group id it leads, cannot have been reused. ``returncode``
    unset is not enough on 3.10/3.11 (asyncio's watcher thread reaps first),
    so a real child is also asked of the kernel, without reaping it."""
    if getattr(process, "returncode", None) is not None:
        return False
    if not _is_child(process) or not hasattr(os, "waitid"):
        return True
    try:
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return False  # reaped already: the pid may be anyone's now
    except OSError:
        return True
    return True


def signal_retained_group(group_pgid: int | None, sig: int = signal.SIGKILL) -> None:
    """Signal a group whose leader has been reaped, synchronously.

    Only while (1) the group still has a member (``killpg(pgid, 0)``) and (2)
    no process holds the leader's pid (``kill(pgid, 0)`` fails with
    ``ProcessLookupError``). While any member of our group lives, the kernel
    keeps the id in use, so it cannot have been handed to a new process; a
    process holding that pid therefore means the id was reused after our
    group emptied, and the group is left alone (Consiliency/pmcp#324).
    """
    if group_pgid is None or not process_groups_supported():
        return
    try:
        os.killpg(group_pgid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return  # no member left (or not ours to signal)
    try:
        os.kill(group_pgid, 0)
        return  # a live process holds the leader's pid: the id was reused
    except ProcessLookupError:
        pass
    except (PermissionError, OSError):
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(group_pgid, sig)


def kill_process_tree_now(
    process: Any, *, group_pgid: int | None = None, sig: int = signal.SIGKILL
) -> None:
    """Signal a child and its group, synchronously (no await, so nothing can
    cancel or skip it). On Windows: terminate its Job Object, or the child
    alone if it has none. On POSIX: the leader only while
    :func:`leader_reserved`; the group then directly (from ``group_pgid`` or
    ``os.getpgid``), and once the leader is reaped through
    :func:`signal_retained_group`. Never raises."""
    if _windows():
        if process is not None and not _terminate_job(process):
            with contextlib.suppress(Exception):
                process.kill()
        return
    if not process_groups_supported():
        group_pgid = None
    if process is None:
        signal_retained_group(group_pgid, sig)
        return
    if not leader_reserved(process):
        signal_retained_group(group_pgid, sig)
        return
    pid = process.pid
    if isinstance(pid, int) and group_pgid is None and hasattr(os, "getpgid"):
        try:
            if os.getpgid(pid) == pid:
                group_pgid = pid
        except (ProcessLookupError, PermissionError, OSError):
            pass
    # The group first, while the leader (checked just above) still reserves
    # its id: signalling the leader first lets asyncio's watcher thread reap
    # it before the group signal, and the id could be reused in that gap.
    if group_pgid is not None and hasattr(os, "killpg"):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(group_pgid, sig)
    if isinstance(pid, int):
        with contextlib.suppress(ProcessLookupError, OSError):
            if sig == signal.SIGKILL:
                process.kill()
            else:
                process.send_signal(sig)


def kill_tree(process: Any, *, terminate: bool = False) -> None:
    """The kill for a child pmcp started with :func:`detached`: it and
    everything it started (an npm lifecycle script, a node-gyp build),
    whether or not it has already exited (round 7, codex F002: a leader that
    exited first left its grandchild running). SIGTERM with ``terminate``,
    else SIGKILL. Never raises.

    Only a real child is signalled by group: an object that merely has a
    ``pid`` (a test double, an adopted handle) is signalled through its own
    ``kill``/``terminate``, never ``killpg``."""
    if _windows() or not _is_child(process) or not process_groups_supported():
        if _windows() and _terminate_job(process):
            return
        with contextlib.suppress(ProcessLookupError, OSError, AttributeError):
            process.terminate() if terminate else process.kill()
        return
    sig = signal.SIGTERM if terminate else signal.SIGKILL
    # The spawn contract: a detached child's pgid is its pid. While it is
    # reserved, it is confirmed; once reaped, the retained-group checks apply.
    group: int | None = process.pid
    if leader_reserved(process):
        try:
            group = process.pid if os.getpgid(process.pid) == process.pid else None
        except (ProcessLookupError, PermissionError, OSError):
            group = None
    kill_process_tree_now(process, group_pgid=group, sig=sig)


def _exited_unreaped(pid: int, within: float) -> bool:
    """Wait up to ``within`` seconds for ``pid`` to exit WITHOUT reaping it
    (``WNOWAIT``), so its pid -- and the group id it leads -- stays reserved
    for the group signal that follows."""
    import time

    deadline = time.monotonic() + within
    while True:
        try:
            info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return True
        if info is not None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


#: On Windows a child cannot be sent an interrupt it would receive (its
#: console is its own, hidden one), so an interrupt waits this long for it
#: to finish on its own and then terminates its job.
_WINDOWS_INTERRUPT_WAIT_SECONDS = 1.0


def interrupt_tree(process: subprocess.Popen[bytes]) -> None:
    """Forward an operator's Ctrl-C to a detached child and everything it
    started (round 6, claude N-A). POSIX: SIGINT to its group, up to
    :data:`_INTERRUPT_GRACE_SECONDS` for it to exit, then SIGKILL to what
    is left -- sent before the leader is reaped, so the group id is still
    its own. Windows: no console control event at all (round 7, claude N-A:
    `CTRL_BREAK_EVENT` cannot reach a child on its own console, and may
    reach every process on pmcp's) -- a short wait, then its Job Object is
    terminated. Never raises."""
    if _windows() or not hasattr(os, "waitid"):
        with contextlib.suppress(Exception):
            process.wait(timeout=_WINDOWS_INTERRUPT_WAIT_SECONDS)
        kill_tree(process)
        with contextlib.suppress(Exception):
            process.wait(timeout=2.0)
        return
    pid = process.pid
    if leader_reserved(process) and _leads(pid):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pid, signal.SIGINT)
        _exited_unreaped(pid, _INTERRUPT_GRACE_SECONDS)
        # The leader is alive or an unreaped zombie: its group id is still
        # its own. Whatever of the group is left is killed.
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pid, signal.SIGKILL)
    else:
        kill_tree(process)
    with contextlib.suppress(Exception):
        process.wait(timeout=2.0)


def _leads(pid: int) -> bool:
    try:
        return os.getpgid(pid) == pid
    except (ProcessLookupError, PermissionError, OSError):
        return False


# --- Windows Job Objects (round 7, claude N-A and codex F002) ------------------

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

#: pid -> (job handle, process), for every tracked Windows child.
_JOBS: dict[int, tuple[Any, Any]] = {}


def _kernel32() -> Any:  # pragma: no cover - Windows only
    import ctypes

    return ctypes.windll.kernel32  # type: ignore[attr-defined]


def _limit_information() -> Any:
    """``JOBOBJECT_EXTENDED_LIMIT_INFORMATION`` with kill-on-close set."""
    import ctypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _Basic),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = _Extended()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    return info


def track(process: Any) -> None:
    """Put a just-spawned child in its own Job Object on Windows (a no-op
    elsewhere), so terminating the job reaches everything it started --
    Windows' group kill -- and closing pmcp's handle kills what is left.
    A child may start a process before it is assigned; that one is missed.
    If assignment fails (a nested-job restriction on old Windows), the child
    is tracked by nothing and its kill falls back to the child alone."""
    if not _windows():
        return
    _prune_jobs()
    import ctypes

    try:
        kernel32 = _kernel32()
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return
        info = _limit_information()
        handle = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, process.pid
        )
        assigned = bool(
            handle
            and kernel32.SetInformationJobObject(
                job,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            and kernel32.AssignProcessToJobObject(job, handle)
        )
        if handle:
            kernel32.CloseHandle(handle)
        if not assigned:
            kernel32.CloseHandle(job)
            return
        _JOBS[process.pid] = (job, process)
    except Exception:  # noqa: BLE001 -- tracking is best-effort
        return


def _terminate_job(process: Any) -> bool:
    """Terminate ``process``'s Job Object, if it has one. Never raises."""
    entry = _JOBS.pop(getattr(process, "pid", None), None)  # type: ignore[arg-type]
    if entry is None:
        return False
    job, _process = entry
    try:
        kernel32 = _kernel32()
        kernel32.TerminateJobObject(job, 1)
        kernel32.CloseHandle(job)
    except Exception:  # noqa: BLE001
        return False
    return True


def _prune_jobs() -> None:
    """Close the jobs of children that have exited: closing the last handle
    kills anything they left running (kill-on-close)."""
    for pid, (job, process) in list(_JOBS.items()):
        try:
            done = (
                process.poll() is not None
                if isinstance(process, subprocess.Popen)
                else process.returncode is not None
            )
        except Exception:  # noqa: BLE001
            done = True
        if done:
            _JOBS.pop(pid, None)
            with contextlib.suppress(Exception):
                _kernel32().CloseHandle(job)


# --- the stderr drain ------------------------------------------------------------


class _StoppableReader:
    """``read1`` over a pipe's read end that also returns ``b""`` once the
    stop end is signalled or closed: the drain can be told to end while a
    descendant still holds the pipe's write end open (round 6, codex F001).
    When data and the stop are both ready, the data is read first, once
    (round 7 nit): the last bytes the child wrote are not dropped."""

    def __init__(self, fd: int, stop_fd: int) -> None:
        import selectors

        self._fd = fd
        self._selector = selectors.DefaultSelector()
        try:
            self._selector.register(fd, selectors.EVENT_READ, "data")
            self._selector.register(stop_fd, selectors.EVENT_READ, "stop")
        except Exception:
            self._selector.close()
            raise
        self._stopping = False

    def read1(self, size: int) -> bytes:
        if self._stopping:
            return b""
        while True:
            events = self._selector.select()
            ready = {key.data for key, _mask in events}
            if "stop" in ready:
                self._stopping = True
                # One last read of what is already there, then the end.
                if "data" in ready or any(
                    key.data == "data" for key, _ in self._selector.select(0)
                ):
                    return os.read(self._fd, size)
                return b""
            if "data" in ready:
                return os.read(self._fd, size)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._selector.close()


class _BlockingReader:
    """``read1`` by a plain blocking read, where a pipe cannot be waited on
    with a selector -- Windows, where ``select`` takes sockets only (round 7,
    codex F003). It drains normally and ends at the pipe's end. It cannot be
    stopped early: if a descendant keeps the write end open past the
    context, this daemon thread and its read end live until that descendant
    closes it, and no longer -- the bounded leak documented in
    ``tests/test_child_output.py``."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def read1(self, size: int) -> bytes:
        return os.read(self._fd, size)

    def close(self) -> None:
        pass


def _drain_until_stopped(fd: int, stop_fd: int, emit: Any) -> None:
    """The drain thread: reads ``fd`` until its end or a stop signal, then
    closes both read ends itself -- the only closer of ``fd``, so no other
    thread can close a descriptor number this one is still reading."""
    reader: _StoppableReader | _BlockingReader | None = None
    try:
        # POSIX: wait on the pipe and the stop pipe together. Windows: `select`
        # takes sockets only -- it accepts a pipe's registration and fails
        # only when it waits -- so a blocking read (round 7, codex F003).
        reader = _BlockingReader(fd) if _windows() else _StoppableReader(fd, stop_fd)
        for raw, truncated in bounded_lines(reader):  # type: ignore[arg-type]
            emit(_render(raw, truncated))
    except Exception:  # noqa: BLE001 -- a drain must never raise
        pass
    finally:
        if reader is not None:
            reader.close()
        for owned in (fd, stop_fd):
            with contextlib.suppress(OSError):
                os.close(owned)


@contextlib.asynccontextmanager
async def drained_errlog(label: str, logger: logging.Logger) -> AsyncIterator[TextIO]:
    """A writable file for a child's stderr (the SDK's ``errlog``): the
    write end of a pipe, whose lines a thread reads and logs at DEBUG as
    ``[<label>] stderr: <line>``, escaped and capped at :data:`LINE_CAP` --
    as ``ClientManager`` logs its own children's stderr. The pipe is
    drained as the child writes, so a chatty child never blocks on a full
    pipe.

    On exit the drain is waited for off the event loop, at most
    :data:`_DRAIN_JOIN_SECONDS` (round 5, claude N-A). If a descendant still
    holds the write end then, the drain is told to stop through its own
    pipe and ends, closing the read end (round 6, codex F001: it used to
    wait on, one thread and one descriptor per context). Nothing it owned
    outlives the context."""
    import anyio

    read_fd, write_fd = os.pipe()
    stop_read, stop_write = os.pipe()
    writer = os.fdopen(write_fd, "w", encoding="utf-8", errors="replace")

    def emit(line: str) -> None:
        logger.debug("[%s] stderr: %s", display_safe(label), line)

    thread = threading.Thread(
        target=_drain_until_stopped,
        args=(read_fd, stop_read, emit),
        name=f"pmcp-errlog-{label}",
        daemon=True,
    )
    thread.start()
    try:
        yield writer
    finally:
        try:
            # The child holds its own copy of the write end; closing ours
            # lets the drain see the end once the child (and any descendant)
            # is gone.
            with contextlib.suppress(Exception):
                writer.close()
            with anyio.CancelScope(shield=True), contextlib.suppress(Exception):
                await anyio.to_thread.run_sync(thread.join, _DRAIN_JOIN_SECONDS)
        finally:
            # Always, cancellation included (round 7, codex F001): closing the
            # stop pipe's write end wakes the drain if it is still waiting on
            # a descendant, and it closes the read ends itself.
            with contextlib.suppress(OSError):
                os.close(stop_write)
            with anyio.CancelScope(shield=True), contextlib.suppress(Exception):
                await anyio.to_thread.run_sync(thread.join, _DRAIN_JOIN_SECONDS)


def run_echoed(command: Sequence[str]) -> int:
    """Run ``command`` for the operator, with its stdout and stderr piped
    and each line then echoed to pmcp's stdout escaped. Returns the exit
    code. Raises ``OSError`` (``FileNotFoundError``) as ``subprocess.run``
    does when the executable is missing. The commands it runs (an installer
    upgrade, a service restart) are short, so the output is echoed when the
    child is done rather than streamed. An operator's Ctrl-C is forwarded
    to the child's whole group (:func:`interrupt_tree`): a detached child no
    longer gets the terminal's SIGINT itself."""
    from pmcp.env_store import child_process_env

    # The environment from the one spawn builder (env_store.child_process_env).
    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=child_process_env(),
        **detached(),
    )
    track(process)
    try:
        output, _ = process.communicate()
    except KeyboardInterrupt:
        interrupt_tree(process)
        raise
    if isinstance(output, bytes) and output:
        import io

        for raw, truncated in bounded_lines(io.BytesIO(output)):
            print(_render(raw, truncated), file=sys.stdout, flush=True)
    return int(process.returncode)
