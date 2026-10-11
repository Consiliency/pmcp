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


def kill_tree(process: Any, *, terminate: bool = False) -> None:
    """Signal a child pmcp started with :func:`detached` and everything it
    started: on POSIX its whole process group (its pgid is its pid), so an
    npm lifecycle script or a node-gyp build it spawned does not run on
    (Consiliency/pmcp#388 round 6, claude N-B); elsewhere the child itself.
    SIGTERM with ``terminate``, else SIGKILL. Never raises."""
    pid = getattr(process, "pid", None)
    if os.name != "nt" and isinstance(pid, int) and hasattr(os, "killpg"):
        sig = signal.SIGTERM if terminate else signal.SIGKILL
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pid, sig)
        return
    with contextlib.suppress(ProcessLookupError, OSError):
        process.terminate() if terminate else process.kill()


def interrupt_tree(process: subprocess.Popen[bytes]) -> None:
    """Forward an operator's Ctrl-C to a detached child's group, which no
    longer receives the terminal's SIGINT (round 6, claude N-A): SIGINT to
    the group (``CTRL_BREAK_EVENT`` to the child's process group on
    Windows), up to :data:`_INTERRUPT_GRACE_SECONDS` for it to exit, then
    SIGKILL to whatever of the group is left. Never raises."""
    if os.name == "nt":
        with contextlib.suppress(Exception):
            process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(process.pid, signal.SIGINT)
    with contextlib.suppress(Exception):
        process.wait(timeout=_INTERRUPT_GRACE_SECONDS)
    # The leader may have exited and left children: the group, always.
    kill_tree(process)
    with contextlib.suppress(Exception):
        process.wait(timeout=2.0)


class _StoppableReader:
    """``read1`` over a pipe's read end that also returns ``b""`` once the
    stop end is signalled or closed: the drain can be told to end while a
    descendant still holds the pipe's write end open (round 6, codex F001)."""

    def __init__(self, fd: int, stop_fd: int) -> None:
        import selectors

        self._fd = fd
        self._selector = selectors.DefaultSelector()
        self._selector.register(fd, selectors.EVENT_READ, "data")
        self._selector.register(stop_fd, selectors.EVENT_READ, "stop")

    def read1(self, size: int) -> bytes:
        while True:
            events = self._selector.select()
            if any(key.data == "stop" for key, _mask in events):
                return b""
            if events:
                return os.read(self._fd, size)

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._selector.close()


def _drain_until_stopped(fd: int, stop_fd: int, emit: Any) -> None:
    """The drain thread: reads ``fd`` until its end or a stop signal, then
    closes both read ends itself -- the only closer of ``fd``, so no other
    thread can close a descriptor number this one is still reading."""
    reader = _StoppableReader(fd, stop_fd)
    try:
        for raw, truncated in bounded_lines(reader):  # type: ignore[arg-type]
            emit(_render(raw, truncated))
    except Exception:  # noqa: BLE001 -- a drain must never raise
        pass
    finally:
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
        # The child holds its own copy of the write end; closing ours lets the
        # drain see the end once the child (and any descendant) is gone.
        with contextlib.suppress(Exception):
            writer.close()
        with contextlib.suppress(Exception):
            await anyio.to_thread.run_sync(thread.join, _DRAIN_JOIN_SECONDS)
        # Stop it if it is still waiting on a descendant: closing the stop
        # pipe's write end wakes it, and it closes the read ends itself.
        with contextlib.suppress(OSError):
            os.close(stop_write)
        with contextlib.suppress(Exception):
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
