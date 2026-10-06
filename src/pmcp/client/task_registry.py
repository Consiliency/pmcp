"""The registry of downstream MCP tasks pmcp tracks (Consiliency/pmcp#338).

`TaskRegistry.put` is the only way a record gets in, and it applies both caps
before it returns, so no record path can skip them:

- **Per server:** at most `per_server` records for one server, finished and
  unfinished alike. Past it, that server's own records are evicted, so one
  downstream cannot push out another's.
- **Total:** at most `total` records across all servers, as a backstop for a
  gateway with many servers. Past it, the evicted record comes from the server
  holding the most records.

Victim order, inside the chosen server:

1. Finished records (`completed`, `failed`, `cancelled`) go before unfinished
   ones. A finished record only caches a final status the downstream still
   serves. An unfinished one is what `tasks_cancel`, the disconnect refusal and
   the stored `requestor_context` depend on.
2. Within each class, the record pmcp recorded least recently goes first. This
   is pmcp's own sequence, which no downstream clock can move
   (Consiliency/pmcp#298).

The record `put` just stored is never evicted by that same `put`. The caller
reads it straight back, for example for `gateway.invoke`'s default redaction.

**A pinned record is never evicted** (Consiliency/pmcp#338, rev 9). A request
that captures state from a record (`tasks/get`, `tasks/result`,
`tasks/cancel`) pins its key for the request's whole duration
(`ClientManager._pinned`), so whatever happens while it awaits the downstream
-- newer calls, floods of other tasks -- the record it read is still there
when its reply lands. When the last pin on a key is released, the caps are
re-applied to its server at once.

**A forced teardown does not pin what it owes; it holds it** (rev 11). Its
obligation is every active task of its servers pmcp tracked when it was
requested, read before its first await into a `TaskCustody` (`watch`). The
registry still evicts those records freely; `_evict` hands each evicted record
it owes to the custody, so the teardown still cancels it, with the newest
context pmcp held for it. Nothing a teardown owes counts against the caps, so
overlapping teardowns cannot stack registry retention (round 9), and an
eviction while a teardown waits cannot drop what it owes (round 10). Like
`cancel_task`, it pins only the one record whose `tasks/cancel` is in flight.

**An owed task is still a tracked task** (rev 12, round-11 F001).
`owed_records` returns what custodies hold that the registry gave up, and
`held` one key's record. `ClientManager.get_tracked_tasks` and
`get_task_record` -- the two sources every reader goes through -- add them
(on the server's current connection). So a second teardown owes them too and
cancels them, a non-forced disconnect refuses on them, a refresh counts
them, and `tasks/get`, `tasks/result` and `tasks/cancel` find them with
their context. A request that pins a key held only in a custody puts the
record back in the registry (`restore`), where its pin keeps it: a record a
request is working on never lives only in a custody. And releasing a custody
does not itself discard a task that was not cancelled (rev 13): however the
teardown ended, each unfinished owed record that nothing else tracks is
returned to the registry on its server's current connection -- under the caps
again, so if the returned records (with what the server already holds) exceed
its cap, the cap evicts the excess like any record nothing owes or pins. A
returned record ranks as the newest, oldest-owed first: by design, a task
pmcp owed a cancel takes priority over newer tasks that were never owed, which
the cap gives up first (round-13 notes N1, N2).

**The bounds** (rev 12). Let R(s) be the `tasks/get`, `tasks/result` and
`tasks/cancel` requests on server s in flight *at this instant*, a teardown's
current `tasks/cancel` included -- each holds one pin, so R(s) is the sum of
s's pin counts -- and R the sum over all servers. After every `put` and
`unpin`, and so at every instant:

    records of server s <= max(per_server, R(s) + 1)
    all records         <= max(total, R + 1)

because eviction skips only pinned keys and the record `put` just stored, and
releasing the last pin on a key re-applies the caps at once: when requests
finish, the records they kept are given back in the same step. Past the
total, a smaller server may give up a record while a larger one is held only
by pins (`_largest_server` skips servers with nothing unpinned): transient,
and only above the total (round-9 N2).

Custodies are not registry retention, and their bound is not in live
requests. A custody is exactly its request-time snapshot (`receive` replaces
an entry, never adds one): what the registry held for its servers then, plus
what running teardowns already owed. The tasks pmcp tracks only through
custodies are at most the owed records the registry evicted during the
current unbroken chain of overlapping teardowns of that server: each a
distinct task some teardown in the chain was asked to cancel and none has
yet. That grows with the chain's history when no cancel is ever answered (a
handoff loop of forced disconnects that each abort the last), and is zero once
no teardown runs. Main kept every such task in the registry, uncapped.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping

from pmcp.types import McpTaskRecord

#: Statuses after which a downstream task cannot change again.
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})

#: Default per-server cap. This was the old shared cap on finished records; it
#: now applies to each server, counting finished and unfinished records.
DEFAULT_TASKS_PER_SERVER = 100

#: Default total cap: ten servers at their per-server cap.
DEFAULT_TASKS_TOTAL = 1000

TaskKey = tuple[str, str]


def task_is_terminal(record: McpTaskRecord) -> bool:
    return record.status in TERMINAL_TASK_STATUSES


class TaskCustody:
    """What one forced teardown owes (Consiliency/pmcp#338, rev 11): every
    record in its request-time snapshot, by `(server, task_id)`. Its keys are
    fixed when it is made; it never gains one. For each, it keeps the latest
    record pmcp held: the snapshot's, or the one the registry evicted since.
    (That record's context is already the newest pmcp held: `_record_task`
    merges a write with the custody's record when the registry no longer
    holds the key, so a record with no context never erases one.)"""

    def __init__(self, records: Iterable[McpTaskRecord]) -> None:
        self._held: dict[TaskKey, McpTaskRecord] = {
            (record.server_name, record.task_id): record for record in records
        }

    def __len__(self) -> int:
        return len(self._held)

    def __contains__(self, key: object) -> bool:
        return key in self._held

    def receive(self, victim: McpTaskRecord) -> None:
        """The registry evicted `victim`: keep it if this teardown owes it."""
        key = (victim.server_name, victim.task_id)
        if key in self._held:  # never a key it does not owe: it never grows
            self._held[key] = victim

    def held(self, key: TaskKey) -> McpTaskRecord:
        """The latest record pmcp held for an owed key."""
        return self._held[key]

    def items(self) -> Iterator[tuple[TaskKey, McpTaskRecord]]:
        """Every owed key, with the latest record pmcp held for it."""
        return iter(list(self._held.items()))


class TaskRegistry(Mapping[TaskKey, McpTaskRecord]):
    """Records keyed by `(server_name, task_id)`, with a cap per server and a
    total cap, both enforced in `put`."""

    def __init__(
        self,
        *,
        per_server: int = DEFAULT_TASKS_PER_SERVER,
        total: int = DEFAULT_TASKS_TOTAL,
    ) -> None:
        if per_server < 1 or total < 1:
            raise ValueError("task caps must be at least 1")
        self.per_server = per_server
        self.total = total
        # server -> task_id -> record. Each inner dict is kept in record order:
        # `put` removes a key before inserting it again, so iterating a bucket
        # yields its least recently recorded task first.
        self._by_server: dict[str, dict[str, McpTaskRecord]] = {}
        # (server, task_id) -> how many in-flight requests pinned it (rev 9)
        self._pins: dict[TaskKey, int] = {}
        # forced teardowns in flight, each holding what it owes (rev 11)
        self._custodies: list[TaskCustody] = []
        self._count = 0
        self._order = 0

    # -- read-only Mapping ----------------------------------------------------

    def __getitem__(self, key: TaskKey) -> McpTaskRecord:
        # Anything that is not a `(server, task_id)` tuple is simply absent:
        # `KeyError`, which `Mapping.get` and `in` turn into None and False.
        # (Unpacking alone would read the string "st" as ("s", "t").)
        if not (isinstance(key, tuple) and len(key) == 2):
            raise KeyError(key)
        server_name, task_id = key
        try:
            return self._by_server[server_name][task_id]
        except TypeError:  # an unhashable part
            raise KeyError(key) from None

    def __iter__(self) -> Iterator[TaskKey]:
        for server_name, bucket in self._by_server.items():
            for task_id in bucket:
                yield (server_name, task_id)

    def __len__(self) -> int:
        return self._count

    # -- writes ---------------------------------------------------------------

    def put(self, record: McpTaskRecord) -> list[McpTaskRecord]:
        """Store `record` as the most recently recorded task, then apply both
        caps. Returns the records evicted, in the order they went."""
        bucket = self._by_server.setdefault(record.server_name, {})
        if bucket.pop(record.task_id, None) is None:
            self._count += 1
        self._order += 1
        record._recorded_order = self._order
        bucket[record.task_id] = record

        return self._apply_caps(
            record.server_name, (record.server_name, record.task_id)
        )

    def restore(self, record: McpTaskRecord) -> list[McpTaskRecord]:
        """Put back a record a custody holds. Two callers: a request that has
        just pinned its key (rev 12, round-11 codex F001; `_pinned`), whose
        pin keeps it through the caps re-applied here; and a custody's
        release, for an owed task that was never cancelled (rev 13,
        round-12 F001; `_custody`), which comes back as an ordinary record.
        The restore takes a new `_recorded_order`, so it can evict another
        record at that moment -- even if the request that pinned it then
        fails (round-12 N1). Only for a key the registry does not hold."""
        bucket = self._by_server.setdefault(record.server_name, {})
        if record.task_id in bucket:
            raise ValueError(f"already tracked: {record.server_name}::{record.task_id}")
        self._count += 1
        self._order += 1
        record._recorded_order = self._order
        bucket[record.task_id] = record
        return self._apply_caps(record.server_name, None)

    def _apply_caps(
        self, server_name: str, new_key: TaskKey | None
    ) -> list[McpTaskRecord]:
        """Evict until `server_name` and the total are within their caps, or
        until only pinned records (and `new_key`) are left to give up."""
        evicted: list[McpTaskRecord] = []
        while len(self._by_server.get(server_name, {})) > self.per_server:
            victim = self._evict(server_name, new_key)
            if victim is None:
                break  # all pinned: over the cap until a pin is released
            evicted.append(victim)
        while self._count > self.total:
            largest = self._largest_server(new_key)
            if largest is None:
                break
            victim = self._evict(largest, new_key)
            assert victim is not None, "_largest_server picks a server with one"
            evicted.append(victim)
        return evicted

    # -- pins -----------------------------------------------------------------

    def pin(self, key: TaskKey) -> None:
        """Protect `key`'s record from eviction until the matching `unpin`.
        Counted, so concurrent requests on one key nest. The key need not
        hold a record yet."""
        self._pins[key] = self._pins.get(key, 0) + 1

    def unpin(self, key: TaskKey) -> list[McpTaskRecord]:
        """Release one pin. When the last pin on the key goes, re-apply the
        caps to its server: the slack pinning allowed is given back at once.
        An unpin without its pin raises `KeyError`: a leak or a double
        release is a bug, not a no-op."""
        remaining = self._pins[key] - 1
        if remaining:
            self._pins[key] = remaining
            return []
        del self._pins[key]
        return self._apply_caps(key[0], None)

    @property
    def pinned(self) -> dict[TaskKey, int]:
        """A copy of the pin counts (empty when no request is in flight)."""
        return dict(self._pins)

    # -- custody (rev 11) -----------------------------------------------------

    def watch(self, records: Iterable[McpTaskRecord]) -> TaskCustody:
        """Register what a forced teardown owes, so that an eviction hands an
        owed record to it instead of dropping it. Released by `unwatch`."""
        custody = TaskCustody(records)
        self._custodies.append(custody)
        return custody

    def unwatch(self, custody: TaskCustody) -> None:
        """Release a custody. Without its `watch` it raises `ValueError`."""
        self._custodies.remove(custody)

    def held(self, key: TaskKey) -> McpTaskRecord | None:
        """The latest record a teardown's custody holds for `key`, or None.
        `_record_task` merges with it when the registry no longer holds the
        key, so a record the cap evicted while a teardown owed it is not
        re-tracked without what pmcp still holds of it (rev 11).

        Any custody that owes the key will do: each received every eviction
        of it since its request (`_evict` hands a victim to all of them), so
        while the registry does not hold the key they all hold the same,
        latest record. (A disconnect drops records without an eviction, but
        then the connection is gone and `_record_task` merges nothing.)"""
        for custody in self._custodies:
            if key in custody:
                return custody.held(key)
        return None

    def owed_records(self, server_name: str | None = None) -> list[McpTaskRecord]:
        """The records running teardowns owe that the registry no longer holds
        (it evicted them into their custodies), one per key, for one server
        or all (rev 12). `ClientManager.get_tracked_tasks` adds them to what
        the registry holds: an owed task is still a task pmcp tracks, so every
        reader of active tasks -- a teardown's snapshot, a disconnect's
        refusal, the refresh gate and its counts -- sees it (round-11 F001).
        Any custody's record will do, for the reason `held` gives."""
        found: dict[TaskKey, McpTaskRecord] = {}
        for custody in self._custodies:
            for key, record in custody.items():
                if server_name not in (None, key[0]):
                    continue
                if key not in self and key not in found:
                    found[key] = record
        return list(found.values())

    @property
    def watching(self) -> int:
        """How many custodies are registered (zero when no teardown runs)."""
        return len(self._custodies)

    def drop_server(self, server_name: str) -> None:
        """Forget every record of one server (it disconnected)."""
        bucket = self._by_server.pop(server_name, None)
        if bucket:
            self._count -= len(bucket)

    def clear(self) -> None:
        self._by_server.clear()
        self._count = 0

    # -- eviction ---------------------------------------------------------------

    def _candidate(
        self, server_name: str, new_key: TaskKey | None
    ) -> McpTaskRecord | None:
        """The record `server_name` gives up next: its oldest finished record,
        else its oldest unfinished one. Never the record `put` just stored
        (`new_key`), and never a pinned record (rev 9); None when nothing is
        left to give up."""
        fallback: McpTaskRecord | None = None
        for task_id, record in self._by_server.get(server_name, {}).items():
            key = (server_name, task_id)
            if key == new_key or key in self._pins:
                continue
            if task_is_terminal(record):
                return record
            if fallback is None:
                fallback = record
        return fallback

    def _largest_server(self, new_key: TaskKey | None) -> str | None:
        """The server the total cap takes from: the one holding the most
        records. On a tie, the one whose next victim was recorded first. None
        when no server has an unpinned record to give up."""
        chosen: str | None = None
        chosen_rank: tuple[int, int] | None = None
        for server_name, bucket in self._by_server.items():
            victim = self._candidate(server_name, new_key)
            if victim is None:
                continue
            rank = (len(bucket), -victim._recorded_order)
            if chosen_rank is None or rank > chosen_rank:
                chosen, chosen_rank = server_name, rank
        return chosen

    def _evict(self, server_name: str, new_key: TaskKey | None) -> McpTaskRecord | None:
        victim = self._candidate(server_name, new_key)
        if victim is None:
            return None
        bucket = self._by_server[server_name]
        del bucket[victim.task_id]
        self._count -= 1
        if not bucket:
            del self._by_server[server_name]
        for custody in self._custodies:
            custody.receive(victim)
        return victim
