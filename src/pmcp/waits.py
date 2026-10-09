"""A bounded wait that never loses its caller's cancellation (Consiliency/pmcp#324).

`asyncio.wait_for` on Python 3.10 and 3.11 ends its ``except CancelledError``
with ``if fut.done(): return fut.result()``: when the awaited work finishes in
the same loop turn as a cancel of the *caller* lands, the cancel is swallowed
and the result returned as if nothing happened (and on 3.11 the task's
``cancelling()`` count is left raised, so a later ``asyncio.timeout()``
reports a cancellation instead of a timeout). A teardown that "finished" that
way lets its caller carry on -- `restart_server` spawns a replacement,
`refresh` reconnects, the update probe returns its result -- after it was
cancelled.

`bounded_wait` is the replacement used everywhere in ``src/pmcp``
(``tests/test_cancel_teardown.py`` bans ``wait_for`` there). It waits with
`asyncio.wait`, which never raises the awaited work's outcome: a
``CancelledError`` out of it can only be the caller's, and it always
propagates -- also when the work finished in the same turn.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, TypeVar

_T = TypeVar("_T")


def _retrieve(task: asyncio.Future[Any]) -> None:
    """Mark a finished task's outcome retrieved (no "never retrieved" noise)."""
    if not task.cancelled():
        task.exception()


async def bounded_wait(aw: Awaitable[_T], timeout: float | None) -> _T:
    """Await ``aw`` for at most ``timeout`` seconds, like `asyncio.wait_for`.

    * Done in time: its result, or its exception, is returned or raised.
    * Timed out: the work is cancelled and waited for, as `wait_for` does,
      then `asyncio.TimeoutError` is raised (the work's result is returned
      instead if it finished anyway).
    * The caller is cancelled: the work is cancelled *without* being waited
      for -- a cancelled caller does not wait on anything -- and the
      caller's ``CancelledError`` propagates, even when the work had already
      finished in the same turn.

    ``timeout=0`` differs from `wait_for` (which on 3.10/3.11 raises before
    the work runs): `asyncio.wait` lets the work take one step first. No
    caller passes 0.
    """
    task = asyncio.ensure_future(aw)
    try:
        await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
            task.add_done_callback(_retrieve)
        else:
            _retrieve(task)
        raise
    if task.done():
        return task.result()
    task.cancel()
    try:
        await asyncio.wait({task})
    except asyncio.CancelledError:
        # Cancelled while the timed-out work unwinds: leave it, but retrieve
        # its outcome when it ends (claude round 6, N1: else 3.11+ logs
        # "Task exception was never retrieved").
        task.add_done_callback(_retrieve)
        raise
    if task.cancelled():
        raise asyncio.TimeoutError
    return task.result()
