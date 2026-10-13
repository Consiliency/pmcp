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
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
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


def _sigkill() -> int:
    """SIGKILL, read at call time: Windows has no ``signal.SIGKILL``, and a
    default argument evaluated at import made ``import pmcp`` fail there
    (Consiliency/pmcp#388 round 8, codex/grok F001)."""
    return int(getattr(signal, "SIGKILL", signal.SIGTERM))


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


# --- trusted groups (round 8, claude N-A) ---------------------------------------
#
# A retained group id is signalled only while pmcp still trusts it: from the
# spawn (or adoption) of the process that leads it until the group is first
# observed empty. Once empty, the id is free and could be handed to a new
# group after the pid space wraps (likely with pid_max 32768, in a
# container); a later teardown must not reach that foreign group. Each id is
# tied to the process object that owns it, held weakly, so a stale record of
# one child never vouches for another child's group either.

_TRUSTED_GROUPS: weakref.WeakValueDictionary[int, Any] = weakref.WeakValueDictionary()


def trust_group(process: Any, pgid: int | None) -> None:
    """Record that ``process`` leads group ``pgid`` (verified by the caller:
    the spawn contract, or ``getpgid`` while the process was ours)."""
    if pgid is None or process is None:
        return
    with contextlib.suppress(TypeError):
        _TRUSTED_GROUPS[pgid] = process


def group_trusted(pgid: int | None, owner: Any = None) -> bool:
    """Whether ``pgid`` is still trusted -- for ``owner`` when one is given."""
    if pgid is None:
        return False
    held = _TRUSTED_GROUPS.get(pgid)
    if held is None:
        return False
    return owner is None or held is owner


def forget_group(pgid: int | None, owner: Any = None) -> None:
    """Stop trusting ``pgid`` (for ``owner`` only, when one is given)."""
    if pgid is not None and group_trusted(pgid, owner):
        _TRUSTED_GROUPS.pop(pgid, None)


def observe_group(pgid: int | None, owner: Any = None) -> bool:
    """Probe a trusted group: ``True`` while it has a member. The first
    probe that finds it empty forgets it, for good."""
    if not group_trusted(pgid, owner) or not process_groups_supported():
        return False
    try:
        os.killpg(pgid, 0)  # type: ignore[arg-type]
    except ProcessLookupError:
        forget_group(pgid, owner)
        return False
    except (PermissionError, OSError):
        return False
    return True


def signal_retained_group(
    group_pgid: int | None, sig: int | None = None, *, owner: Any = None
) -> None:
    """Signal a group whose leader has been reaped, synchronously.

    Only while (1) pmcp still trusts the id -- it has not been observed
    empty since its leader was spawned, and it belongs to ``owner`` when one
    is given (round 8, claude N-A) --, (2) the group still has a member
    (``killpg(pgid, 0)``; finding none forgets the id), and (3) no process
    holds the leader's pid (``kill(pgid, 0)`` fails with
    ``ProcessLookupError``). While any member of our group lives, the kernel
    keeps the id in use, so it cannot have been handed to a new process; a
    process holding that pid therefore means the id was reused after our
    group emptied, and the group is left alone (Consiliency/pmcp#324).
    """
    if group_pgid is None or not process_groups_supported():
        return
    if not observe_group(group_pgid, owner):
        return  # untrusted, or no member left (now forgotten)
    try:
        os.kill(group_pgid, 0)
        return  # a live process holds the leader's pid: the id was reused
    except ProcessLookupError:
        pass
    except (PermissionError, OSError):
        return
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(group_pgid, _sigkill() if sig is None else sig)


def kill_process_tree_now(
    process: Any, *, group_pgid: int | None = None, sig: int | None = None
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
    if sig is None:
        sig = _sigkill()
    if not process_groups_supported():
        group_pgid = None
    if process is None:
        return  # no owner to vouch for a retained id (round 8, claude N-A)
    if not leader_reserved(process):
        signal_retained_group(group_pgid, sig, owner=process)
        return
    pid = process.pid
    if isinstance(pid, int) and hasattr(os, "getpgid"):
        # The leader is reserved (checked just above): its group is the one
        # signalled directly -- a passed id only when it IS that group.
        try:
            own = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            own = None
        if own is None and group_pgid is not None:
            # Reaped between the check above and the lookup (asyncio's watcher
            # thread, 3.10/3.11): the passed id goes through the retained-group
            # checks instead of being dropped.
            signal_retained_group(group_pgid, sig, owner=process)
            return
        if group_pgid is None and own == pid:
            group_pgid = pid
        if group_pgid is not None and own != group_pgid:
            group_pgid = None
        if group_pgid is not None:
            trust_group(process, group_pgid)
    # The group first, while the leader (checked just above) still reserves
    # its id: signalling the leader first lets asyncio's watcher thread reap
    # it before the group signal, and the id could be reused in that gap.
    if group_pgid is not None and hasattr(os, "killpg"):
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(group_pgid, sig)
    if isinstance(pid, int):
        with contextlib.suppress(Exception):  # never raises, whatever `process` is
            if sig == _sigkill():
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
    sig = signal.SIGTERM if terminate else _sigkill()
    # The spawn contract: a detached child's pgid is its pid. While it is
    # reserved, it is confirmed; once reaped, the retained-group checks apply.
    group: int | None = process.pid
    if leader_reserved(process):
        try:
            group = process.pid if os.getpgid(process.pid) == process.pid else None
        except (ProcessLookupError, PermissionError, OSError):
            # Reaped between the check and the lookup (asyncio's watcher
            # thread, 3.10/3.11): keep the spawn contract's id; the retained
            # group checks -- trusted for this process, not empty -- decide
            # (round 9, codex F003).
            pass
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
        # The spawn contract: a detached child leads a group whose id is its
        # pid. Trusted from here until the group is observed empty.
        if _is_child(process) and process_groups_supported():
            trust_group(process, process.pid)
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


#: How often a Windows drain looks at its pipe and its stop event.
_POLL_SECONDS = 0.05

#: The most a stopped drain still reads: one final read, so a descendant
#: writing without pause cannot keep the drain past its stop (round 9).
_FINAL_READ_BUDGET = 64 * 1024


def _peek_pipe(fd: int) -> int | None:  # pragma: no cover - Windows only
    """Bytes ready in a Windows pipe, without blocking (``PeekNamedPipe``);
    ``None`` at the pipe's end (every writer closed) or on any failure."""
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined]
        available = wintypes.DWORD(0)
        ok = _kernel32().PeekNamedPipe(
            wintypes.HANDLE(handle), None, 0, None, ctypes.byref(available), None
        )
    except Exception:  # noqa: BLE001
        return None
    if not ok:
        return None
    return int(available.value)


class _PollingReader:
    """``read1`` for Windows, where ``select`` takes sockets only (round 7,
    codex F003): it reads only what :func:`_peek_pipe` reports ready, so it
    never blocks in ``ReadFile``, and it checks the stop event between
    looks. Its lifetime is pmcp's, not the downstream's (round 8, codex
    F004): once stopped it reads what is already there once and ends,
    within :data:`_POLL_SECONDS`, even while a descendant holds the write
    end open forever."""

    def __init__(self, fd: int, stop: threading.Event) -> None:
        self._fd = fd
        self._stop = stop
        self._stopping = False

    def read1(self, size: int) -> bytes:
        if self._stopping:
            return b""
        while True:
            # The stop first, on every iteration (round 9, codex F001): a
            # descendant that keeps the pipe readable must not outrun it.
            if self._stop.is_set():
                self._stopping = True
                # One last read of what is already there, at most
                # :data:`_FINAL_READ_BUDGET` bytes, then the end.
                available = _peek_pipe(self._fd)
                if available:
                    return os.read(self._fd, min(size, available, _FINAL_READ_BUDGET))
                return b""
            available = _peek_pipe(self._fd)
            if available is None:
                return b""  # the pipe's end
            if available:
                return os.read(self._fd, min(size, available))
            self._stop.wait(_POLL_SECONDS)

    def close(self) -> None:
        pass


def _drain_until_stopped(
    fd: int, stop_fd: int, emit: Any, stop: threading.Event | None = None
) -> None:
    """The drain thread: reads ``fd`` until its end or a stop signal, then
    closes both read ends itself -- the only closer of ``fd``, so no other
    thread can close a descriptor number this one is still reading."""
    reader: _StoppableReader | _PollingReader | None = None
    try:
        # POSIX: wait on the pipe and the stop pipe together. Windows: `select`
        # takes sockets only -- it accepts a pipe's registration and fails
        # only when it waits -- so a polling read that also watches the stop
        # event (round 7, codex F003; round 8, codex F004).
        reader = (
            _PollingReader(fd, stop or threading.Event())
            if _windows()
            else _StoppableReader(fd, stop_fd)
        )
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
    stop = threading.Event()
    writer = os.fdopen(write_fd, "w", encoding="utf-8", errors="replace")

    def emit(line: str) -> None:
        logger.debug("[%s] stderr: %s", display_safe(label), line)

    thread = threading.Thread(
        target=_drain_until_stopped,
        args=(read_fd, stop_read, emit, stop),
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
            stop.set()
            with contextlib.suppress(OSError):
                os.close(stop_write)
            with anyio.CancelScope(shield=True), contextlib.suppress(Exception):
                await anyio.to_thread.run_sync(thread.join, _DRAIN_JOIN_SECONDS)


async def spawn_owned(start: Callable[[], Awaitable[Any]]) -> Any:
    """Spawn a child so that no cancellation can land between its creation
    and its ownership (Consiliency/pmcp#388 round 13, grok F001 = codex
    F001). Every async spawn in ``src/pmcp`` goes through here (the spawn
    guard in ``tests/test_child_output.py`` pins it).

    ``start`` makes the child (``asyncio.create_subprocess_exec``, or the
    MCP SDK's factory). The child already runs while that awaits its pipes;
    cancelled there, asyncio kills the leader's pid alone, and a grandchild
    it forked meanwhile survives with no owner. So the spawn runs shielded:
    a caller's cancellation does not reach it. If the caller was cancelled
    meanwhile, the child is owned the moment it exists -- :func:`track`ed
    (its Job Object on Windows, its group trusted on POSIX), killed with
    everything it started, its pipes closed -- and the cancellation
    propagates. Otherwise it is tracked and returned.

    Not covered: a loop being torn down cancels the shielded spawn itself,
    and asyncio then kills the leader alone; the gateway abandons its
    servers before that point."""
    spawning = asyncio.ensure_future(start())
    try:
        process = await asyncio.shield(spawning)
    except asyncio.CancelledError:
        spawning.add_done_callback(_own_abandoned_spawn)
        raise
    track(process)
    return process


def _own_abandoned_spawn(spawning: asyncio.Future[Any]) -> None:
    """The spawn whose caller was cancelled has finished: kill what it made
    with its group, close its pipes. Never raises."""
    if spawning.cancelled():
        return
    failed = spawning.exception()
    if failed is not None:
        return  # nothing was made
    process = _unwrapped(spawning.result())
    with contextlib.suppress(Exception):
        track(process)
        kill_tree(process)  # the group first, while the leader reserves it
        release(process)


def _unwrapped(process: Any) -> Any:
    """The asyncio child inside an anyio ``Process`` (the MCP SDK's spawn),
    so it is killed by the one rule -- the group while the leader is
    reserved -- not by its leader alone."""
    inner = getattr(process, "_process", None)
    return inner if _is_child(inner) else process


def release(process: Any) -> None:
    """Close the parent's ends of a child's pipes, now (round 10, codex
    F001): an asyncio child's pipe transports -- one a flooding downstream
    left paused and unread is otherwise held until garbage collection --
    or a ``Popen``'s stream objects. Every site that drops a child calls
    this after its kill. Safe to repeat; never raises."""
    if process is None:
        return
    if isinstance(process, subprocess.Popen):
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()
        return
    transport = getattr(process, "_transport", None)
    if transport is None:
        return
    # The pipes, always: closing them never touches the process.
    for fd in (0, 1, 2):
        with contextlib.suppress(Exception):
            pipe = transport.get_pipe_transport(fd)
            if pipe is not None:
                pipe.close()
    # The subprocess transport only once the child is reaped: closing it
    # earlier polls -- and so reaps -- the child behind asyncio's own
    # watcher, which then reports exit status 255 instead of the real one.
    if process.returncode is not None:
        with contextlib.suppress(Exception):
            transport.close()


def _quiet_broken_stdout() -> None:
    """After a broken pipe on the operator's stdout: point descriptor 1 at
    the null device, so the interpreter's flush at exit neither raises nor
    prints a traceback. Only for the human CLI's display stream (the one
    :func:`run_echoed` writes through), never a gateway's protocol stream."""
    from pmcp.display import DisplaySafeStream

    if not isinstance(sys.stdout, DisplaySafeStream):
        return
    with contextlib.suppress(OSError):
        # stderr too when it is the same broken pipe (`2>&1 | head`, round
        # 12, claude): pmcp's own last words must not turn the installer's
        # exit status into a broken-pipe one.
        out, err = os.fstat(1), os.fstat(2)
        same = (out.st_dev, out.st_ino) == (err.st_dev, err.st_ino)
        null = os.open(os.devnull, os.O_WRONLY)
        os.dup2(null, 1)
        if same:
            os.dup2(null, 2)
        os.close(null)


#: How long :func:`owned` waits to reap a killed synchronous child.
_REAP_SECONDS = 2.0


@contextlib.contextmanager
def owned(process: Any) -> Iterator[Any]:
    """The owner's scope of a child pmcp spawned (round 8, codex F003): on
    every exit from it -- return, error, timeout, cancellation -- the child
    and everything it started are killed through the one kill rule
    (:func:`kill_tree`), synchronously, so no await can skip it. A child
    that already exited costs one probe of its group; a descendant it left
    running is killed. A synchronous child is then reaped."""
    try:
        yield process
    finally:
        kill_tree(process)
        if isinstance(process, subprocess.Popen) and process.returncode is None:
            with contextlib.suppress(Exception):
                process.wait(timeout=_REAP_SECONDS)
        release(process)


def run_captured(
    command: Sequence[str], *, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """``subprocess.run(command, capture_output=True, text=True)`` for a
    short probe (a service check): detached, stdin closed, the environment
    from the one spawn builder, and owned -- killed with its group on every
    exit, a timeout or an interrupt included. Raises ``OSError`` as
    ``subprocess.run`` does, and ``subprocess.TimeoutExpired``."""
    from pmcp.env_store import child_process_env

    process = subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=child_process_env(),
        **detached(),
    )
    # Owned from the moment it exists (round 10): an interrupt landing
    # before the communicate still kills it with its group.
    with owned(process):
        track(process)
        stdout, stderr = process.communicate(timeout=timeout)
    return subprocess.CompletedProcess(
        list(command), int(process.returncode), stdout, stderr
    )


#: How often :func:`watch_group` probes a group its leader left behind.
_WATCH_SECONDS = 0.5


async def watch_group(process: Any, pgid: int | None) -> None:
    """Forget a long-lived child's group as soon as it is empty (round 8,
    claude N-A): the group is probed every :data:`_WATCH_SECONDS` until the
    first probe that finds no member, which forgets the id -- so it is not
    trusted across a pid wrap. It never waits on the process or its pipes
    (round 9, codex F002: an asyncio ``wait()`` also waits for the pipes to
    close, and a descendant holding them kept an empty group trusted). Ends
    at once for an untrusted id; never raises."""
    while observe_group(pgid, process):
        await asyncio.sleep(_WATCH_SECONDS)


def run_echoed(command: Sequence[str]) -> int:
    """Run ``command`` for the operator, with its stdout and stderr piped
    and each line then echoed to pmcp's stdout escaped. Returns the exit
    code. Raises ``OSError`` (``FileNotFoundError``) as ``subprocess.run``
    does when the executable is missing. Each line is echoed as the child
    writes it (round 10: streamed, so a child that never stops writing can
    still be interrupted, and memory stays bounded). An operator's Ctrl-C is forwarded
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
    # Owned from the moment it exists (round 10): a Ctrl-C landing before
    # the communicate is forwarded and the child killed with its group too.
    with owned(process):
        try:
            track(process)
            # Streamed, a chunk at a time, not `communicate()` (round 10):
            # with one pipe it reads to the end in a single C loop that
            # never returns to the interpreter while the child keeps writing,
            # so an operator's Ctrl-C was never delivered -- and the whole
            # output was held in memory. Each line is echoed as it arrives;
            # memory stays at one capped line.
            assert process.stdout is not None
            echoing = True
            for raw, truncated in bounded_lines(process.stdout):
                if not echoing:
                    continue  # drained and discarded: the child must not block
                try:
                    print(_render(raw, truncated), file=sys.stdout, flush=True)
                except BrokenPipeError:
                    # `pmcp upgrade | head` (round 11, claude N-A): the reader
                    # went away. Stop echoing, keep draining until the child
                    # exits on its own -- never kill an installer mid-install
                    # for it -- and report its exit status.
                    echoing = False
                    _quiet_broken_stdout()
            process.wait()
        except KeyboardInterrupt:
            interrupt_tree(process)
            raise
    return int(process.returncode)
