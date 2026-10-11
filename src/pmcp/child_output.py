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
from collections.abc import Iterator, Sequence
from typing import IO, Any, TextIO

from pmcp.display import display_safe

#: How long, after the child is done, the drain may take to reach the end of
#: the pipe: a grandchild that kept the pipe open does not hold pmcp up.
_DRAIN_JOIN_SECONDS = 5.0


def _drain(stream: IO[bytes], emit: Any) -> None:
    try:
        for raw in stream:
            emit(display_safe(raw.decode("utf-8", "replace").rstrip("\r\n"), keep="\t"))
    except Exception:  # noqa: BLE001 -- a drain must never raise
        pass
    finally:
        with contextlib.suppress(Exception):
            stream.close()


@contextlib.contextmanager
def drained_errlog(label: str, logger: logging.Logger) -> Iterator[TextIO]:
    """A writable file for a child's stderr (the SDK's ``errlog``): the
    write end of a pipe, whose lines a thread reads to the end and logs at
    DEBUG as ``[<label>] stderr: <line>``, escaped -- as ``ClientManager``
    logs its own children's stderr. The pipe is drained as the child
    writes, so a chatty child never blocks on a full pipe."""
    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "rb")
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
        # drain see the end once the child is gone.
        with contextlib.suppress(Exception):
            writer.close()
        thread.join(_DRAIN_JOIN_SECONDS)


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
    )
    output = getattr(result, "stdout", None)
    if isinstance(output, bytes) and output:
        for raw in output.splitlines():
            line = raw.decode("utf-8", "replace")
            print(display_safe(line, keep="\t"), file=sys.stdout, flush=True)
    return int(result.returncode)
