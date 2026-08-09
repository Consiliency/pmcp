"""Catalog event sink (IF-0-P3B-1): bridges pmcp's sync catalog mutators to
`mcp` 2.0.0's async `subscriptions/listen` fan-out.

`ClientManager`'s catalog mutators (`_index_tools`, `_index_resources`,
`_index_prompts`, `_remove_server_indexes`, `_disconnect_all_unlocked`) are
synchronous; `mcp.server.subscriptions.SubscriptionBus.publish` is async.
`CatalogEventSink` is the seam: mutators call a sync `note_*` and the sink
takes care of getting the corresponding `ServerEvent` onto the bus.

No new event vocabulary is defined here. `ToolsListChanged`,
`ResourcesListChanged`, `PromptsListChanged` and `SubscriptionBus` are
imported from the SDK (`mcp.shared.subscriptions` / `mcp.server.subscriptions`)
and re-used as-is.

Correctness mechanism, and why it is written the way it is: each `note_*`
adds the corresponding event class to a pending *set* (so N index writes in
one connect coalesce into one event per kind -- these are level triggers,
"this changed, refetch if you care") and, if a running loop exists and no
drain is already scheduled, arms a self-draining task. The drain LOOPS until
the pending set is empty and clears its "live" flag only in the same
synchronous step as the `while` check that found it empty, with no `await`
in between. That shape is required, not stylistic: the naive "pop a
snapshot, publish it, exit" drain has a lost-wakeup race, because
`InMemorySubscriptionBus.publish` always suspends (it ends with an explicit
`anyio.lowlevel.checkpoint()`). A `note_*` landing in that suspended window
would see a live drain task and decline to re-arm; if the drain does not
re-check `pending` after its await returns, that event is stranded until
some unrelated later mutation happens to schedule a new drain. Looping
until the check-for-empty and the flag-clear are back-to-back with nothing
async between them closes that window.

`flush()` exists only so tests (and, optionally, call sites that want tight
coalescing around one operation) can drain deterministically. It is not the
correctness mechanism -- the self-scheduling drain is -- and no call site is
required to invoke it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable

from mcp.server.subscriptions import SubscriptionBus
from mcp.shared.subscriptions import (
    PromptsListChanged,
    ResourcesListChanged,
    ToolsListChanged,
)

__all__ = ["CatalogEventSink", "BusCatalogEventSink"]

logger = logging.getLogger(__name__)

# The three catalog-change events this sink publishes. `ResourceUpdated`
# (single-resource content changes) is out of this sink's scope -- it has no
# `note_*` here and is not part of IF-0-P3B-1.
_CatalogEvent = ToolsListChanged | PromptsListChanged | ResourcesListChanged
_CatalogEventClass = type[_CatalogEvent]

# Fixed publish order for a coalesced drain: tools, then resources, then
# prompts. Applies to both the self-scheduled drain and `flush()`.
_ORDER: tuple[_CatalogEventClass, ...] = (
    ToolsListChanged,
    ResourcesListChanged,
    PromptsListChanged,
)


@runtime_checkable
class CatalogEventSink(Protocol):
    """What a catalog mutator calls when the tool/resource/prompt list changes."""

    def note_tools_changed(self) -> None:
        """Record that the tool list changed. Sync, never raises."""
        ...

    def note_resources_changed(self) -> None:
        """Record that the resource list changed. Sync, never raises."""
        ...

    def note_prompts_changed(self) -> None:
        """Record that the prompt list changed. Sync, never raises."""
        ...

    async def flush(self) -> None:
        """Drain any pending events immediately. Not required for correctness."""
        ...


class BusCatalogEventSink:
    """`CatalogEventSink` that publishes onto a `SubscriptionBus`.

    See the module docstring for the drain-loop correctness argument.
    """

    def __init__(self, bus: SubscriptionBus) -> None:
        self._bus = bus
        self._pending: set[_CatalogEventClass] = set()
        self._draining = False
        # Strong references so a scheduled drain is never GC'd mid-flight.
        self._drain_tasks: set[asyncio.Task[None]] = set()

    def note_tools_changed(self) -> None:
        self._note(ToolsListChanged)

    def note_resources_changed(self) -> None:
        self._note(ResourcesListChanged)

    def note_prompts_changed(self) -> None:
        self._note(PromptsListChanged)

    def _note(self, kind: _CatalogEventClass) -> None:
        self._pending.add(kind)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (e.g. a sync unit test): record and return.
            # A later `flush()` or a note from inside a loop will drain it.
            return
        if self._draining:
            # A drain is already scheduled or running; it will see this
            # event because it loops until `_pending` is empty rather than
            # publishing one snapshot and exiting (see module docstring).
            return
        self._start_drain(loop)

    def _start_drain(self, loop: asyncio.AbstractEventLoop) -> None:
        """Mark a drain live and schedule it. Sole setter of `_draining=True`."""
        self._draining = True
        task = loop.create_task(self._drain())
        self._drain_tasks.add(task)
        task.add_done_callback(self._on_drain_done)

    def _on_drain_done(self, task: asyncio.Task[None]) -> None:
        """Clear `_draining` and re-arm if work remains. Sole clearer of the flag.

        This lives in the done-callback rather than in a `finally` inside
        `_drain` because a task cancelled *before it starts* never enters its
        body at all -- no `finally` can run, and the flag would stay set
        forever, wedging every later `note_*`. The callback fires for every
        terminal state: normal return, exception, cancellation mid-run, and
        cancellation before the first step.

        Re-arming matters as much as clearing. Events noted *while* a drain
        was in flight stay in `_pending`; if that drain is then cancelled,
        nothing is scheduled to publish them and they would sit there until
        some unrelated later mutation happened to arm a drain. A cancellation
        must delay delivery, never strand it.
        """
        self._drain_tasks.discard(task)
        self._draining = False
        if not self._pending:
            return
        try:
            self._start_drain(asyncio.get_running_loop())
        except RuntimeError:  # pragma: no cover - loop already closing
            pass

    async def _drain(self) -> None:
        """Self-scheduled drain: yield once, then loop until pending is empty.

        Deliberately does not touch `_draining` -- `_on_drain_done` owns it,
        which is what covers the cancelled-before-first-step path.
        """
        await asyncio.sleep(0)
        await self._drain_pending()

    async def flush(self) -> None:
        """Drain any pending events immediately, in tools/resources/prompts order.

        Idempotent; a no-op when nothing is pending. Not the correctness
        mechanism -- see module docstring -- call sites are never required
        to call this.
        """
        await self._drain_pending()

    async def _drain_pending(self) -> None:
        """The frozen drain loop: pop everything pending, publish it, repeat
        until a pop finds nothing left. Shared by `_drain` and `flush`."""
        while self._pending:
            kinds, self._pending = self._pending, set()
            for kind in _ORDER:
                if kind in kinds:
                    await self._publish(kind)

    async def _publish(self, kind: _CatalogEventClass) -> None:
        try:
            await self._bus.publish(kind())
        except Exception:
            # Isolate a raising bus from the drain, matching
            # `InMemorySubscriptionBus.publish`'s own listener-isolation
            # contract -- one bad publish must not stop the next drain.
            logger.exception("subscription bus publish raised; catalog event dropped")
