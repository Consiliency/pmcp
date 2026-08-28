"""SL-3.1 — pin `ClientManager(catalog_events=...)`: the production catalog
event publishers (roadmap Lane C).

`ClientManager` mutates `_tools`/`_resources`/`_prompts` from six write sites
(Context > "The publisher gap"). Five of them are sync mutators that call
`CatalogEventSink.note_*`; `__init__` is exempt (no sink or subscriber can
exist at construction). This module covers:

  - the null-sink default (`ClientManager()` with no `catalog_events`
    constructs and keeps working — the ~20 pre-P3B construction sites need
    zero edits);
  - each of `_index_tools` / `_index_resources` / `_index_prompts` /
    `_remove_server_indexes` records exactly the right `note_*` calls,
    including the "no-op index publishes nothing" and "never-connected
    server publishes nothing" negatives;
  - the integration half: a real `ClientManager` wired to a real
    `BusCatalogEventSink` over a real `InMemorySubscriptionBus`, driven
    through `connect_server`/`disconnect_server` against a real stdio
    downstream — no test in this half calls `note_*` or `flush()` itself,
    and no production `ClientManager` code path calls `flush()` either
    (grepping the module for a `flush(` call is empty): IF-0-P3B-1's
    self-scheduling drain is the only thing that can deliver these events,
    which is exactly what EC-P3B-4 needs to be proving;
  - the two regressions the board found: `refresh([])` (the
    `_disconnect_all_unlocked` silent-clear hole) and the lost-wakeup race,
    driven this time through the production mutators rather than the sink
    directly (that's SL-1's own coverage).
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from mcp.server.subscriptions import InMemorySubscriptionBus
from mcp.shared.subscriptions import (
    PromptsListChanged,
    ResourcesListChanged,
    ServerEvent,
    ToolsListChanged,
)

from pmcp.client.manager import ClientManager
from pmcp.subscriptions import BusCatalogEventSink, CatalogEventSink
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
from tests.runtime.harness import RT_FIXTURE_SRC

POLL_ATTEMPTS = 100
POLL_INTERVAL_S = 0.02


async def _poll_until(predicate: Callable[[], bool]) -> None:
    """Poll rather than assert-immediately-after-return: no production code
    path calls `flush()`, so delivery here always goes through
    `BusCatalogEventSink`'s self-scheduled drain, which is asynchronous
    relative to the `note_*` call that armed it."""
    for _ in range(POLL_ATTEMPTS):
        if predicate():
            return
        await asyncio.sleep(POLL_INTERVAL_S)
    assert predicate()  # final attempt — produces the real assertion failure


class _RecordingSink:
    """A `CatalogEventSink` that just records which `note_*` fired, in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def note_tools_changed(self) -> None:
        self.calls.append("tools")

    def note_resources_changed(self) -> None:
        self.calls.append("resources")

    def note_prompts_changed(self) -> None:
        self.calls.append("prompts")

    async def flush(self) -> None:  # pragma: no cover - not exercised here
        pass


class _GatedBus:
    """A `SubscriptionBus` whose `publish` suspends on a test-controlled
    `asyncio.Event` — same shape as SL-1's, reused here to prove the
    lost-wakeup fix holds when driven through `ClientManager`'s own
    mutators rather than the sink directly."""

    def __init__(self) -> None:
        self.published: list[ServerEvent] = []
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()

    async def publish(self, event: ServerEvent) -> None:
        self.entered.set()
        await self.gate.wait()
        self.published.append(event)

    def subscribe(self, listener: Callable[[ServerEvent], None]) -> Callable[[], None]:
        return lambda: None


def _stdio_fixture_config(
    tmp_path: Path, name: str = "rt-fixture"
) -> ResolvedServerConfig:
    """The tests/runtime stdio fixture (one tool, one prompt), reused here
    without editing SL-5's `tests/runtime/harness.py`. `sys.executable` is
    the `.venv` interpreter under `uv run pytest`."""
    fixture_py = tmp_path / "rt_fixture_server.py"
    fixture_py.write_text(RT_FIXTURE_SRC)
    return ResolvedServerConfig(
        name=name,
        source="project",
        config=LocalMcpServerConfig(command=sys.executable, args=[str(fixture_py)]),
    )


# --- the null-sink default -------------------------------------------------


def test_client_manager_with_no_catalog_events_constructs_with_a_null_sink() -> None:
    manager = ClientManager()
    assert isinstance(manager._catalog_events, CatalogEventSink)
    # And every existing call still works: a no-op index on a fresh manager
    # neither raises nor is a `_NullCatalogEventSink` special case.
    assert manager._index_tools("s", []) == 0
    assert manager._index_tools("s", [{"name": "t1", "inputSchema": {}}]) == 1


# --- _index_tools / _index_resources / _index_prompts ----------------------


def test_index_tools_records_exactly_one_note_for_one_tool() -> None:
    sink = _RecordingSink()
    manager = ClientManager(catalog_events=sink)

    indexed = manager._index_tools(
        "s", [{"name": "t1", "description": "d", "inputSchema": {}}]
    )

    assert indexed == 1
    assert sink.calls == ["tools"]


def test_index_resources_records_exactly_one_note_for_one_resource() -> None:
    sink = _RecordingSink()
    manager = ClientManager(catalog_events=sink)

    count = manager._index_resources("s", [{"uri": "file:///a", "name": "r1"}])

    assert count == 1
    assert sink.calls == ["resources"]


def test_index_prompts_records_exactly_one_note_for_one_prompt() -> None:
    sink = _RecordingSink()
    manager = ClientManager(catalog_events=sink)

    count = manager._index_prompts("s", [{"name": "p1"}])

    assert count == 1
    assert sink.calls == ["prompts"]


def test_index_tools_with_nothing_indexed_records_nothing() -> None:
    """A no-op index must not publish — a level trigger that fires on
    nothing trains clients to ignore it."""
    sink = _RecordingSink()
    manager = ClientManager(catalog_events=sink)

    indexed = manager._index_tools("s", [])

    assert indexed == 0
    assert sink.calls == []


def test_index_resources_and_prompts_with_nothing_indexed_record_nothing() -> None:
    sink = _RecordingSink()
    manager = ClientManager(catalog_events=sink)

    assert manager._index_resources("s", []) == 0
    assert manager._index_prompts("s", []) == 0
    assert sink.calls == []


# --- _remove_server_indexes --------------------------------------------


def test_remove_server_indexes_records_all_three_kinds_that_were_present() -> None:
    sink = _RecordingSink()
    manager = ClientManager(catalog_events=sink)
    manager._index_tools("s", [{"name": "t1", "inputSchema": {}}])
    manager._index_resources("s", [{"uri": "file:///a", "name": "r1"}])
    manager._index_prompts("s", [{"name": "p1"}])
    sink.calls.clear()

    manager._remove_server_indexes("s")

    assert set(sink.calls) == {"tools", "resources", "prompts"}
    assert len(sink.calls) == 3


def test_remove_server_indexes_never_connected_records_nothing() -> None:
    sink = _RecordingSink()
    manager = ClientManager(catalog_events=sink)

    manager._remove_server_indexes("never-connected")

    assert sink.calls == []


def test_remove_server_indexes_only_notes_kinds_that_actually_shrank() -> None:
    """Only tools were indexed for this server — removing it must not claim
    resources or prompts changed too."""
    sink = _RecordingSink()
    manager = ClientManager(catalog_events=sink)
    manager._index_tools("s", [{"name": "t1", "inputSchema": {}}])
    sink.calls.clear()

    manager._remove_server_indexes("s")

    assert sink.calls == ["tools"]


# --- the integration half: a real bus, a real sink, real connect/disconnect


async def test_connect_and_disconnect_publish_over_a_real_bus_no_manual_note_or_flush(
    tmp_path: Path,
) -> None:
    bus = InMemorySubscriptionBus()
    sink = BusCatalogEventSink(bus)
    manager = ClientManager(catalog_events=sink)
    published: list[ServerEvent] = []
    bus.subscribe(published.append)

    config = _stdio_fixture_config(tmp_path)
    try:
        errors = await manager.connect_server(config, retry=False)
        assert errors == [], errors

        await _poll_until(
            lambda: any(isinstance(e, ToolsListChanged) for e in published)
        )

        published.clear()
        disconnected, _cancelled, error = await manager.disconnect_server(
            config.name, force=True
        )
        assert disconnected, error

        await _poll_until(
            lambda: any(isinstance(e, ToolsListChanged) for e in published)
        )
    finally:
        await manager.disconnect_all()


# --- the board-found regression: refresh([]) must still announce the clear


async def test_refresh_with_empty_config_publishes_all_three_list_changed_events() -> (
    None
):
    """`ClientManager.refresh([])` -> `_disconnect_all_unlocked()` +
    `_connect_all_unlocked([])` (a no-op on an empty list). The wholesale
    clear bypasses `_remove_server_indexes` entirely (it clears all three
    dicts directly) — without SL-3.2's fix this empties every catalog and
    publishes nothing, the exact listener-with-no-publishers failure this
    phase exists to prevent."""
    bus = InMemorySubscriptionBus()
    sink = BusCatalogEventSink(bus)
    manager = ClientManager(catalog_events=sink)
    published: list[ServerEvent] = []
    bus.subscribe(published.append)

    manager._index_tools("s", [{"name": "t1", "inputSchema": {}}])
    manager._index_resources("s", [{"uri": "file:///a", "name": "r1"}])
    manager._index_prompts("s", [{"name": "p1"}])
    await sink.flush()
    published.clear()

    result = await manager.refresh([])

    assert result == []

    def _all_three_kinds_seen() -> bool:
        kinds = {type(e) for e in published}
        return {ToolsListChanged, ResourcesListChanged, PromptsListChanged} <= kinds

    await _poll_until(_all_three_kinds_seen)


# --- the board-found regression: the lost-wakeup race, via the mutators ----


async def test_note_during_a_suspended_publish_via_index_methods_is_not_stranded() -> (
    None
):
    """IF-0-P3B-1's drain-loop freeze, proven through `ClientManager`'s own
    mutators rather than the sink directly (SL-1 already covers the sink in
    isolation): `_index_tools` arms a drain that suspends inside
    `bus.publish(...)`; while it is suspended, `_index_prompts` runs and
    must not be stranded. No `flush()` call anywhere in this test."""
    bus = _GatedBus()
    sink = BusCatalogEventSink(bus)
    manager = ClientManager(catalog_events=sink)

    manager._index_tools("s", [{"name": "t1", "inputSchema": {}}])
    await bus.entered.wait()  # the drain is now suspended inside publish()

    manager._index_prompts("s", [{"name": "p1"}])  # lands in the lost-wakeup window

    bus.gate.set()  # release the first publish

    await _poll_until(lambda: len(bus.published) >= 2)

    assert bus.published == [ToolsListChanged(), PromptsListChanged()]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
