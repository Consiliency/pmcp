"""SL-1.1 — pin IF-0-P3B-1: the catalog event sink `src/pmcp/subscriptions.py`.

Published on day 1 so SL-2, SL-3 and SL-5 develop against a real type rather
than a reading of one. Covers the frozen shape end to end: the `Protocol`'s
exact member set, `note_*` outside and inside a running loop, set-coalescing,
fixed publish order, the deterministic lost-wakeup regression (a `note_*`
landing while a publish is suspended), listener isolation against a raising
bus, and the "no pmcp event type defined" AST guard.

Also carries the CHANGELOG/version half of EC-P3B-6 (SL-1.3): the `##
[2.0.0]` block, wherever it sits in the file, leads with `### Removed` and
names both the breaking change (`GET` / `405`) and its replacement
(`subscriptions/listen`), and the version strings in `pyproject.toml` and
`src/pmcp/__init__.py` agree with it. The V6 shell greps in the phase plan's
Verification section are the paired non-standalone half of this.
"""

from __future__ import annotations

import ast
import asyncio
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from mcp.shared.subscriptions import (
    PromptsListChanged,
    ResourcesListChanged,
    ServerEvent,
    ToolsListChanged,
)

from pmcp.subscriptions import BusCatalogEventSink, CatalogEventSink

REPO_ROOT = Path(__file__).resolve().parents[2]
SUBSCRIPTIONS_MODULE = REPO_ROOT / "src" / "pmcp" / "subscriptions.py"


class _RecordingBus:
    """A minimal `SubscriptionBus` that just records what was published."""

    def __init__(self) -> None:
        self.published: list[ServerEvent] = []

    async def publish(self, event: ServerEvent) -> None:
        self.published.append(event)

    def subscribe(self, listener: Callable[[ServerEvent], None]) -> Callable[[], None]:
        return lambda: None


class _GatedBus:
    """A `SubscriptionBus` whose `publish` suspends on a test-controlled
    `asyncio.Event`, so a test can land a `note_*` call inside the exact
    window where the naive "snapshot, publish, exit" drain drops it."""

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


class _RaisingBus:
    """A `SubscriptionBus` whose `publish` always raises."""

    def __init__(self) -> None:
        self.calls = 0

    async def publish(self, event: ServerEvent) -> None:
        self.calls += 1
        raise RuntimeError("boom")

    def subscribe(self, listener: Callable[[ServerEvent], None]) -> Callable[[], None]:
        return lambda: None


# --- IF-0-P3B-1: the Protocol's exact shape -------------------------------


def test_catalog_event_sink_is_a_runtime_checkable_protocol_with_four_members() -> None:
    assert getattr(CatalogEventSink, "_is_protocol", False) is True
    assert getattr(CatalogEventSink, "_is_runtime_protocol", False) is True
    members = {name for name in vars(CatalogEventSink) if not name.startswith("_")}
    assert members == {
        "note_tools_changed",
        "note_resources_changed",
        "note_prompts_changed",
        "flush",
    }


def test_bus_catalog_event_sink_satisfies_the_protocol() -> None:
    sink = BusCatalogEventSink(_RecordingBus())
    assert isinstance(sink, CatalogEventSink)


# --- note_* outside a running loop ----------------------------------------


def test_note_outside_running_loop_does_not_raise_and_leaves_it_pending() -> None:
    bus = _RecordingBus()
    sink = BusCatalogEventSink(bus)

    sink.note_tools_changed()  # no running loop here — must not raise

    assert bus.published == []  # nothing delivered yet: still pending

    asyncio.run(sink.flush())

    assert bus.published == [ToolsListChanged()]


# --- set semantics: N writes of one kind coalesce to one event -----------


async def test_three_calls_of_one_kind_coalesce_to_one_published_event() -> None:
    bus = _RecordingBus()
    sink = BusCatalogEventSink(bus)

    sink.note_tools_changed()
    sink.note_tools_changed()
    sink.note_tools_changed()
    await sink.flush()

    assert bus.published == [ToolsListChanged()]


# --- flush() publish order --------------------------------------------


async def test_flush_publishes_in_tools_resources_prompts_order() -> None:
    bus = _RecordingBus()
    sink = BusCatalogEventSink(bus)

    sink.note_prompts_changed()
    sink.note_tools_changed()
    sink.note_resources_changed()
    await sink.flush()

    assert bus.published == [
        ToolsListChanged(),
        ResourcesListChanged(),
        PromptsListChanged(),
    ]


async def test_flush_is_idempotent_and_a_noop_when_empty() -> None:
    bus = _RecordingBus()
    sink = BusCatalogEventSink(bus)

    await sink.flush()
    assert bus.published == []

    sink.note_tools_changed()
    await sink.flush()
    await sink.flush()  # second call: nothing left to drain
    assert bus.published == [ToolsListChanged()]


# --- self-scheduling drain: the correctness mechanism, not flush() --------


async def test_note_inside_running_loop_self_schedules_a_drain_with_no_flush_call() -> (
    None
):
    bus = _RecordingBus()
    sink = BusCatalogEventSink(bus)

    sink.note_tools_changed()  # no flush() anywhere in this test
    await asyncio.sleep(0.05)

    assert bus.published == [ToolsListChanged()]


# --- the deterministic lost-wakeup regression ------------------------------


async def test_note_during_a_suspended_publish_is_not_stranded() -> None:
    """IF-0-P3B-1's drain-loop freeze, proven deterministically.

    A `note_tools_changed()` arms a drain, which pops `{ToolsListChanged}`
    and suspends inside `bus.publish(...)` (held open by `bus.gate`). While
    it is suspended, `note_prompts_changed()` runs — the exact lost-wakeup
    window the plan names: the naive "snapshot, publish, exit" drain has
    already popped its snapshot and has a live drain task, so this note
    would decline to re-arm and its event would be stranded. Releasing the
    gate must still deliver `PromptsListChanged`, and with **no `flush()`
    call anywhere in this test** — only the self-scheduling drain loop can
    be responsible for it.
    """
    bus = _GatedBus()
    sink = BusCatalogEventSink(bus)

    sink.note_tools_changed()
    await bus.entered.wait()  # the drain is now suspended inside publish()

    sink.note_prompts_changed()  # lands in the lost-wakeup window

    bus.gate.set()  # release the first publish
    # Give the drain loop room to re-check `_pending`, find the prompts
    # event, and publish it too — no flush() call anywhere in this test.
    for _ in range(50):
        if len(bus.published) >= 2:
            break
        await asyncio.sleep(0.01)

    assert bus.published == [ToolsListChanged(), PromptsListChanged()]


def test_naive_snapshot_and_exit_drain_fails_the_lost_wakeup_regression() -> None:
    """Proof the regression above is real: replay it against the naive shape
    the plan describes (pop a snapshot, publish it, exit — no `while` loop,
    flag cleared unconditionally after the one publish) and confirm it drops
    the second event. This function is standalone (not a subclass of the
    frozen sink) so it cannot accidentally exercise the fixed implementation.
    """

    class _NaiveSink:
        def __init__(self, bus: _GatedBus) -> None:
            self._bus = bus
            self._pending: set[type] = set()
            self._draining = False

        def note_tools_changed(self) -> None:
            self._note(ToolsListChanged)

        def note_prompts_changed(self) -> None:
            self._note(PromptsListChanged)

        def _note(self, kind: type) -> None:
            self._pending.add(kind)
            if self._draining:
                return
            self._draining = True
            asyncio.get_running_loop().create_task(self._drain())

        async def _drain(self) -> None:
            # The naive shape: one snapshot, one publish pass, then exit —
            # no re-check of `_pending` after the `await` inside publish.
            kinds, self._pending = self._pending, set()
            for kind in (ToolsListChanged, ResourcesListChanged, PromptsListChanged):
                if kind in kinds:
                    await self._bus.publish(kind())
            self._draining = False

    async def run() -> list[ServerEvent]:
        bus = _GatedBus()
        naive = _NaiveSink(bus)
        naive.note_tools_changed()
        await bus.entered.wait()
        naive.note_prompts_changed()  # sees `_draining is True`, declines to re-arm
        bus.gate.set()
        await asyncio.sleep(0.1)
        return bus.published

    published = asyncio.run(run())
    # The naive shape drops the prompts event: it never sees a second drain.
    assert published == [ToolsListChanged()]


# --- a raising bus is isolated, and the sink keeps working afterward ------


async def test_raising_bus_is_isolated_and_the_sink_still_works_afterward() -> None:
    bus = _RaisingBus()
    sink = BusCatalogEventSink(bus)

    sink.note_tools_changed()
    await asyncio.sleep(0.05)  # the drain must not propagate the exception

    assert bus.calls == 1

    # The drain's live flag must have been cleared despite the exception, or
    # this second note is silently dropped (mistaken for "a drain is live").
    sink.note_resources_changed()
    await asyncio.sleep(0.05)

    assert bus.calls == 2


async def test_raising_bus_isolation_also_holds_for_flush() -> None:
    bus = _RaisingBus()
    sink = BusCatalogEventSink(bus)

    sink.note_tools_changed()
    await sink.flush()  # must not raise

    assert bus.calls == 1


# --- no pmcp event type is defined; the vocabulary is entirely SDK's ------


def test_module_imports_the_sdk_vocabulary_and_defines_no_event_class_of_its_own() -> (
    None
):
    tree = ast.parse(SUBSCRIPTIONS_MODULE.read_text())

    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in (
            "mcp.shared.subscriptions",
            "mcp.server.subscriptions",
        ):
            imported_names.update(alias.asname or alias.name for alias in node.names)

    assert {
        "ToolsListChanged",
        "ResourcesListChanged",
        "PromptsListChanged",
    } <= imported_names

    offending = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and not node.bases
        and node.name.endswith("Changed")
    ]
    assert offending == [], f"module defines its own event class(es): {offending}"


# --- EC-P3B-6: the release entry, wherever the [2.0.0] block sits --------


def _changelog_2_0_0_block() -> str:
    text = (REPO_ROOT / "CHANGELOG.md").read_text()
    match = re.search(
        r"^## \[2\.0\.0\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.DOTALL | re.MULTILINE
    )
    assert match, "CHANGELOG.md has no '## [2.0.0]' heading"
    return match.group(1)


def test_changelog_2_0_0_block_leads_with_removed_and_names_the_breaking_change() -> (
    None
):
    body = _changelog_2_0_0_block()

    subsections = re.findall(r"^### (\w+)", body, re.MULTILINE)
    assert subsections, f"'## [2.0.0]' block has no ### subsections:\n{body}"
    assert subsections[0] == "Removed", (
        f"'## [2.0.0]' block's first subsection is {subsections[0]!r}, expected 'Removed' "
        "(GET retirement must be the first thing a reader hits)"
    )

    removed_section = body.split("### ", 2)[1]  # the 'Removed' subsection's own text
    assert "GET" in removed_section and "405" in removed_section, removed_section
    assert "subscriptions/listen" in body, (
        "the replacement must be named in the [2.0.0] block"
    )


def test_pyproject_and_init_version_strings_agree_with_the_changelog_heading() -> None:
    pyproject_text = (REPO_ROOT / "pyproject.toml").read_text()
    init_text = (REPO_ROOT / "src" / "pmcp" / "__init__.py").read_text()

    assert re.search(r'^version = "2\.0\.0"', pyproject_text, re.MULTILINE), (
        pyproject_text
    )
    assert re.search(r'^__version__ = "2\.0\.0"', init_text, re.MULTILINE), init_text

    # And the [2.0.0] block itself must exist (redundant with the test
    # above, but this is the one place all three sites are checked together).
    _changelog_2_0_0_block()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
