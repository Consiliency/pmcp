"""SL-3 (FANOUT) — EC-FANOUT-1/2/3/9: catalog freshness on the stdio dispatch
path, proven through `gateway.catalog_search` and `gateway.invoke` -- never
merely through "a notification arrived."

`tests/runtime/test_emitter_harness.py` (SL-2) already proves the emitter's
frames reach `ClientManager._handle_stdout_line`; that is necessary but not
sufficient evidence the gateway is actually usable afterwards. A
forward-to-sink implementation that never re-indexes would also make SL-2's
tests pass, because they only spy on the raw line reaching the dispatch
function. This file is the other half: after a real downstream emission, a
tool the fake stdio server ADDED is returned by `gateway.catalog_search` and
answers a real `gateway.invoke` call, and a tool it REMOVED is gone from
both -- backed by `ClientManager.get_tool`/`get_all_tools` (the index,
`self._tools`), never by peeking at `manager._tools` directly from the test.

`tests/runtime/test_downstream_remote.py` is this file's remote-transport
twin; EC-FANOUT-3 requires both to pass independently, neither may be
skipped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from pmcp.client.manager import ClientManager
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools

from tests.runtime.fake_stdio_server import build_fake_stdio_downstream
from tests.runtime.harness import alloc_port


async def _catalog_tool_ids(gateway_tools: GatewayTools, server: str) -> set[str]:
    """The tool_ids `gateway.catalog_search` currently returns for `server`,
    with no text query -- a query is optional (`CatalogSearchInput.query`),
    and the server filter alone is enough to scope this to one downstream."""
    output = await gateway_tools.catalog_search(
        {"filters": {"server": server}, "limit": 100}
    )
    return {card.tool_id for card in output.results}


async def _wait_until(
    predicate: Callable[[], Awaitable[bool]],
    *,
    attempts: int = 50,
    interval: float = 0.05,
) -> bool:
    """Poll an async predicate until it is True or `attempts` is exhausted.
    Returns the last observed value, so a failing assertion shows what the
    catalog actually held rather than just "timed out"."""
    result = False
    for _ in range(attempts):
        result = await predicate()
        if result:
            return True
        await asyncio.sleep(interval)
    return result


@pytest.mark.asyncio
class TestStdioDownstreamNotificationCatalogFreshness:
    async def test_downstream_notification_added_tool_is_searchable_and_invocable(
        self,
    ) -> None:
        """EC-FANOUT-1/9, stdio half of EC-FANOUT-3: a tool ADDED by the
        downstream after connect is returned by `gateway.catalog_search`, and
        `gateway.invoke` resolves it through the index and dispatches a real
        downstream call -- proof the index actually moved, not just that a
        notification was received.

        `fake_stdio_server.py` (SL-2, owned by that lane, not editable here)
        deliberately implements only `initialize` and `tools/list` for real
        -- its own docstring states resources/prompts get a "method not
        found" response and is silent on `tools/call`, which turns out to
        get the same treatment. So the downstream call itself always fails
        here; what proves reconciliation is *which* error comes back.
        `gateway.invoke` resolves `tool_id` through `ClientManager.get_tool`
        BEFORE it ever calls the downstream (`handlers.py` — the E301 branch
        is reached only when the registry lookup misses); a lookup miss
        would short-circuit to E301_TOOL_NOT_FOUND without a downstream
        round trip at all. Getting back E302 (downstream execution failed)
        instead of E301 (not found) is the proof the tool was actually in
        the reconciled index."""
        name = "stdio-fanout-add"
        downstream = build_fake_stdio_downstream(name, control_port=alloc_port())
        manager = ClientManager()
        try:
            errors = await manager.connect_server(downstream.config)
            assert errors == []
            gateway_tools = GatewayTools(
                client_manager=manager, policy_manager=PolicyManager()
            )
            tool_id = f"{name}::sf_dyn"

            # Baseline: the fake stdio server starts with an empty catalog,
            # so the tool must not be findable before the mutation + emit.
            assert tool_id not in await _catalog_tool_ids(gateway_tools, name)

            await downstream.emitter.add_tool("sf_dyn", description="dynamic")
            await downstream.emitter.emit("notifications/tools/list_changed")

            found = await _wait_until(
                lambda: _tool_present(gateway_tools, name, tool_id)
            )
            assert found, "gateway.catalog_search never reflected the added tool"

            result = await gateway_tools.invoke(
                {"tool_id": tool_id, "arguments": {"text": "hi"}}
            )
            assert result.ok is False, (
                "unexpectedly succeeded -- fake_stdio_server has no tools/call "
                "handler, so a real success here would be surprising"
            )
            errors_joined = " ".join(result.errors or [])
            assert '"code":"E301"' not in errors_joined, (
                f"gateway.invoke returned E301_TOOL_NOT_FOUND for a tool the "
                f"catalog just reported present -- the index was not actually "
                f"updated: {result.errors}"
            )
            assert '"code":"E302"' in errors_joined, (
                f"expected a downstream dispatch failure (E302), got: {result.errors}"
            )

        finally:
            await manager.disconnect_all()

    async def test_downstream_notification_removed_tool_disappears_from_catalog_and_invoke(
        self,
    ) -> None:
        """EC-FANOUT-2, stdio half of EC-FANOUT-3: the flip side. A tool the
        downstream REMOVES is gone from both `gateway.catalog_search` and
        `gateway.invoke` after the server announces the change."""
        name = "stdio-fanout-remove"
        downstream = build_fake_stdio_downstream(name, control_port=alloc_port())
        manager = ClientManager()
        try:
            errors = await manager.connect_server(downstream.config)
            assert errors == []
            gateway_tools = GatewayTools(
                client_manager=manager, policy_manager=PolicyManager()
            )
            tool_id = f"{name}::sf_removable"

            await downstream.emitter.add_tool("sf_removable")
            await downstream.emitter.emit("notifications/tools/list_changed")
            found = await _wait_until(
                lambda: _tool_present(gateway_tools, name, tool_id)
            )
            assert found, "setup: added tool never appeared in the catalog"

            await downstream.emitter.remove_tool("sf_removable")
            await downstream.emitter.emit("notifications/tools/list_changed")

            gone = await _wait_until(lambda: _tool_absent(gateway_tools, name, tool_id))
            assert gone, (
                "gateway.catalog_search still lists a tool the downstream removed"
            )

            result = await gateway_tools.invoke({"tool_id": tool_id, "arguments": {}})
            assert result.ok is False
            errors_joined = " ".join(result.errors or [])
            assert '"code":"E301"' in errors_joined, (
                f"expected E301_TOOL_NOT_FOUND for a tool the downstream removed, "
                f"got: {result.errors}"
            )

        finally:
            await manager.disconnect_all()


async def _tool_present(gateway_tools: GatewayTools, server: str, tool_id: str) -> bool:
    return tool_id in await _catalog_tool_ids(gateway_tools, server)


async def _tool_absent(gateway_tools: GatewayTools, server: str, tool_id: str) -> bool:
    return tool_id not in await _catalog_tool_ids(gateway_tools, server)
