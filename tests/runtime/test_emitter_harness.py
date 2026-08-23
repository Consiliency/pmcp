"""SL-2.1 (FANOUT) — pins IF-0-FANOUT-2's shape and proves the emitter
reaches the gateway's dispatch, on both transports independently.

This module owns the emitter contract, not the reconciliation behaviour
built on it -- SL-1's scheduler may not exist yet (SL-1 and SL-2 are
parallel-safe, neither depends on the other), so "reaches the dispatch"
here means "the frame is fed into the exact function that would recognise
it": `ClientManager._handle_stdout_line` (stdio) and the per-message loop
inside `ClientManager._read_sse` (remote). Both already exist on `main` --
today they silently drop a notification (no `id`, no `else` branch) rather
than reconciling it, so this test does not assert reconciliation; SL-3
(`tests/runtime/test_downstream_remote.py`,
`tests/runtime/test_downstream_stdio.py`) asserts the catalog is actually
refreshed, once SL-1 lands.

`_read_sse` has no extracted per-message handler to spy on directly (unlike
`_handle_stdout_line`, which already is one) -- it's one monolithic
`async for message in read_stream:` loop. So the remote-side proof wraps
`read_stream` itself: an async generator that records each message as it
passes through, then re-yields it unchanged into the real `_read_sse`. The
real dispatch loop still consumes the exact same message; the wrapper only
observes.
"""

from __future__ import annotations

import asyncio
import json
from unittest import mock

import pytest

from pmcp.client.manager import ClientManager
from pmcp.types import RemoteMcpServerConfig, ResolvedServerConfig

from tests.runtime.fake_remote import (
    DownstreamEmitter,
    build_fake_remote_app,
    run_fake_remote,
)
from tests.runtime.fake_stdio_server import StdioEmitter, build_fake_stdio_downstream
from tests.runtime.harness import alloc_port

AUTH_VALUE = "Bearer emitter-harness-token"


def _remote_config(name: str, url: str) -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name,
        source="custom",
        config=RemoteMcpServerConfig(
            type="streamable-http", url=url, headers={"Authorization": AUTH_VALUE}
        ),
    )


def _spy_read_sse():
    """Patch target + call recorder for `ClientManager._read_sse`. Returns
    `(patcher, calls)`; `calls` accumulates each message's JSON-alias dict
    form, in the exact shape `_read_sse` itself decodes it to
    (`manager.py:1981`)."""
    calls: list[dict[str, object]] = []
    original = ClientManager._read_sse

    async def spy(self: ClientManager, name: str, managed: object, read_stream: object):
        async def _tap() -> object:
            async for message in read_stream:  # type: ignore[attr-defined]
                if not isinstance(message, Exception):
                    calls.append(
                        message.message.model_dump(
                            by_alias=True, mode="json", exclude_none=True
                        )
                    )
                yield message

        return await original(self, name, managed, _tap())

    return mock.patch.object(ClientManager, "_read_sse", spy), calls


def _spy_handle_stdout_line():
    """Patch target + call recorder for `ClientManager._handle_stdout_line`.
    `calls` accumulates each raw line's decoded JSON, exactly as
    `_handle_stdout_line` itself parses it (`manager.py:1765`)."""
    calls: list[dict[str, object]] = []
    original = ClientManager._handle_stdout_line

    def spy(
        self: ClientManager, name: str, managed: object, line: bytes, now: float
    ) -> None:
        calls.append(json.loads(line.decode()))
        return original(self, name, managed, line, now)

    return mock.patch.object(ClientManager, "_handle_stdout_line", spy), calls


class TestIF0Fanout2Shape:
    """Pins the emitter API's shape before SL-3 codes against it."""

    def test_remote_emitter_satisfies_downstream_emitter_protocol(self) -> None:
        app = build_fake_remote_app(expected_auth_value=AUTH_VALUE)
        assert isinstance(app.state.fake_remote_emitter, DownstreamEmitter)

    def test_stdio_emitter_satisfies_downstream_emitter_protocol(self) -> None:
        downstream = build_fake_stdio_downstream(
            "shape-check", control_port=alloc_port()
        )
        assert isinstance(downstream.emitter, StdioEmitter)
        assert isinstance(downstream.emitter, DownstreamEmitter)

    def test_emitter_methods_are_the_documented_three(self) -> None:
        for method_name in ("add_tool", "remove_tool", "emit"):
            assert callable(getattr(DownstreamEmitter, method_name))


@pytest.mark.asyncio
class TestRemoteEmitterReachesDispatch:
    async def test_add_tool_then_emit_reaches_read_sse(self) -> None:
        manager = ClientManager()
        patcher, calls = _spy_read_sse()
        try:
            async with run_fake_remote(
                alloc_port(), expected_auth_value=AUTH_VALUE
            ) as remote:
                with patcher:
                    errors = await manager.connect_server(
                        _remote_config("fr-emit-add", remote.mcp_url)
                    )
                    assert errors == []

                    await remote.emitter.add_tool("fr_dyn", description="dynamic")
                    await remote.emitter.emit("notifications/tools/list_changed")

                    for _ in range(50):
                        if any(
                            c.get("method") == "notifications/tools/list_changed"
                            for c in calls
                        ):
                            break
                        await asyncio.sleep(0.05)
        finally:
            await manager.disconnect_all()

        matches = [
            c for c in calls if c.get("method") == "notifications/tools/list_changed"
        ]
        assert matches, f"emitted notification never reached _read_sse: {calls}"

    async def test_emit_alone_reaches_read_sse(self) -> None:
        """Storm-suppression precondition: `emit()` with no prior catalog
        mutation still reaches the dispatch."""
        manager = ClientManager()
        patcher, calls = _spy_read_sse()
        try:
            async with run_fake_remote(
                alloc_port(), expected_auth_value=AUTH_VALUE
            ) as remote:
                with patcher:
                    errors = await manager.connect_server(
                        _remote_config("fr-emit-noop", remote.mcp_url)
                    )
                    assert errors == []

                    await remote.emitter.emit("notifications/tools/list_changed")

                    for _ in range(50):
                        if calls:
                            break
                        await asyncio.sleep(0.05)
        finally:
            await manager.disconnect_all()

        assert any(c.get("method") == "notifications/tools/list_changed" for c in calls)

    async def test_unrecognised_method_reaches_read_sse(self) -> None:
        """EC-FANOUT-5's no-op case starts here: the harness must be able to
        put an unrecognised `notifications/*` method on the wire at all."""
        manager = ClientManager()
        patcher, calls = _spy_read_sse()
        try:
            async with run_fake_remote(
                alloc_port(), expected_auth_value=AUTH_VALUE
            ) as remote:
                with patcher:
                    errors = await manager.connect_server(
                        _remote_config("fr-emit-unknown", remote.mcp_url)
                    )
                    assert errors == []

                    await remote.emitter.emit("notifications/something_unrecognised")

                    for _ in range(50):
                        if calls:
                            break
                        await asyncio.sleep(0.05)
        finally:
            await manager.disconnect_all()

        assert any(
            c.get("method") == "notifications/something_unrecognised" for c in calls
        )

    async def test_remove_tool_then_emit_reaches_read_sse(self) -> None:
        manager = ClientManager()
        patcher, calls = _spy_read_sse()
        try:
            async with run_fake_remote(
                alloc_port(), expected_auth_value=AUTH_VALUE
            ) as remote:
                with patcher:
                    errors = await manager.connect_server(
                        _remote_config("fr-emit-remove", remote.mcp_url)
                    )
                    assert errors == []

                    await remote.emitter.add_tool("fr_removable")
                    await remote.emitter.remove_tool("fr_removable")
                    await remote.emitter.emit("notifications/tools/list_changed")

                    for _ in range(50):
                        if calls:
                            break
                        await asyncio.sleep(0.05)
        finally:
            await manager.disconnect_all()

        assert any(c.get("method") == "notifications/tools/list_changed" for c in calls)


@pytest.mark.asyncio
class TestStdioEmitterReachesDispatch:
    async def test_add_tool_then_emit_reaches_handle_stdout_line(self) -> None:
        downstream = build_fake_stdio_downstream(
            "stdio-emit-add", control_port=alloc_port()
        )
        manager = ClientManager()
        patcher, calls = _spy_handle_stdout_line()
        try:
            with patcher:
                errors = await manager.connect_server(downstream.config)
                assert errors == []

                await downstream.emitter.add_tool("stdio_dyn", description="dynamic")
                await downstream.emitter.emit("notifications/tools/list_changed")

                for _ in range(50):
                    if any(
                        c.get("method") == "notifications/tools/list_changed"
                        for c in calls
                    ):
                        break
                    await asyncio.sleep(0.05)
        finally:
            await manager.disconnect_all()

        matches = [
            c for c in calls if c.get("method") == "notifications/tools/list_changed"
        ]
        assert matches, (
            f"emitted notification never reached _handle_stdout_line: {calls}"
        )

    async def test_emit_alone_reaches_handle_stdout_line(self) -> None:
        downstream = build_fake_stdio_downstream(
            "stdio-emit-noop", control_port=alloc_port()
        )
        manager = ClientManager()
        patcher, calls = _spy_handle_stdout_line()
        try:
            with patcher:
                errors = await manager.connect_server(downstream.config)
                assert errors == []

                await downstream.emitter.emit("notifications/tools/list_changed")

                for _ in range(50):
                    if any(
                        c.get("method") == "notifications/tools/list_changed"
                        for c in calls
                    ):
                        break
                    await asyncio.sleep(0.05)
        finally:
            await manager.disconnect_all()

        assert any(c.get("method") == "notifications/tools/list_changed" for c in calls)

    async def test_unrecognised_method_reaches_handle_stdout_line(self) -> None:
        downstream = build_fake_stdio_downstream(
            "stdio-emit-unknown", control_port=alloc_port()
        )
        manager = ClientManager()
        patcher, calls = _spy_handle_stdout_line()
        try:
            with patcher:
                errors = await manager.connect_server(downstream.config)
                assert errors == []

                await downstream.emitter.emit("notifications/something_unrecognised")

                for _ in range(50):
                    if any(
                        c.get("method") == "notifications/something_unrecognised"
                        for c in calls
                    ):
                        break
                    await asyncio.sleep(0.05)
        finally:
            await manager.disconnect_all()

        assert any(
            c.get("method") == "notifications/something_unrecognised" for c in calls
        )

    async def test_remove_tool_then_emit_reaches_handle_stdout_line(self) -> None:
        downstream = build_fake_stdio_downstream(
            "stdio-emit-remove", control_port=alloc_port()
        )
        manager = ClientManager()
        patcher, calls = _spy_handle_stdout_line()
        try:
            with patcher:
                errors = await manager.connect_server(downstream.config)
                assert errors == []

                await downstream.emitter.add_tool("stdio_removable")
                await downstream.emitter.remove_tool("stdio_removable")
                await downstream.emitter.emit("notifications/tools/list_changed")

                for _ in range(50):
                    if any(
                        c.get("method") == "notifications/tools/list_changed"
                        for c in calls
                    ):
                        break
                    await asyncio.sleep(0.05)
        finally:
            await manager.disconnect_all()

        assert any(c.get("method") == "notifications/tools/list_changed" for c in calls)

    async def test_remove_unknown_tool_raises(self) -> None:
        downstream = build_fake_stdio_downstream(
            "stdio-emit-remove-unknown", control_port=alloc_port()
        )
        manager = ClientManager()
        try:
            errors = await manager.connect_server(downstream.config)
            assert errors == []
            with pytest.raises(RuntimeError):
                await downstream.emitter.remove_tool("does-not-exist")
        finally:
            await manager.disconnect_all()
