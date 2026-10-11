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


def _drain(stream: IO[bytes], emit: Any) -> None:
    try:
        for raw, truncated in bounded_lines(stream):
            emit(_render(raw, truncated))
    except Exception:  # noqa: BLE001 -- a drain must never raise
        pass
    finally:
        with contextlib.suppress(Exception):
            stream.close()


@contextlib.asynccontextmanager
async def drained_errlog(label: str, logger: logging.Logger) -> AsyncIterator[TextIO]:
    """A writable file for a child's stderr (the SDK's ``errlog``): the
    write end of a pipe, whose lines a thread reads to the end and logs at
    DEBUG as ``[<label>] stderr: <line>``, escaped and capped at
    :data:`LINE_CAP` -- as ``ClientManager`` logs its own children's
    stderr. The pipe is drained as the child writes, so a chatty child
    never blocks on a full pipe.

    On exit the drain is waited for off the event loop, at most
    :data:`_DRAIN_JOIN_SECONDS` (round 5, claude N-A: a blocking join
    stalled the loop while a grandchild kept the pipe open). After that it
    is left to finish on its own: a daemon thread whose only open end is
    the grandchild's, so it ends when that does."""
    import anyio

    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb", buffering=0)
    writer = os.fdopen(write_fd, "w", encoding="utf-8", errors="replace")

    def emit(line: str) -> None:
        logger.debug("[%s] stderr: %s", display_safe(label), line)

    thread = threading.Thread(
        target=_drain, args=(reader, emit), name=f"pmcp-errlog-{label}", daemon=True
    )
    thread.start()
    try:
        yield writer
    finally:
        # The child holds its own copy of the write end; closing ours lets the
        # drain see the end once the child (and any grandchild) is gone.
        with contextlib.suppress(Exception):
            writer.close()
        with contextlib.suppress(Exception):
            await anyio.to_thread.run_sync(thread.join, _DRAIN_JOIN_SECONDS)


def run_echoed(command: Sequence[str]) -> int:
    """Run ``command`` for the operator, with its stdout and stderr piped
    and each line then echoed to pmcp's stdout escaped. Returns the exit
    code. Raises ``OSError`` (``FileNotFoundError``) as ``subprocess.run``
    does when the executable is missing. The commands it runs (an installer
    upgrade, a service restart) are short, so the output is echoed when the
    child is done rather than streamed."""
    from pmcp.env_store import child_process_env

    # The environment from the one spawn builder (env_store.child_process_env).
    result = subprocess.run(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=child_process_env(),
        **detached(),
    )
    output = getattr(result, "stdout", None)
    if isinstance(output, bytes) and output:
        import io

        for raw, truncated in bounded_lines(io.BytesIO(output)):
            print(_render(raw, truncated), file=sys.stdout, flush=True)
    return int(result.returncode)
