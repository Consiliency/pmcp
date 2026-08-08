"""SL-4.2 — EC-P2-2, EC-P2-3: handshake-era deployed-wire acceptance.

Drives a real `mcp.ClientSession` over `streamable_http_client` against a
booted gateway's deployed `/mcp` endpoint (handshake era: the ordinary
`initialize` negotiation, not the modern per-request `_meta` envelope —
that's SL-4.3). Every method's response is already the SDK's own typed
result model — `ClientSession` parses the wire response into it as part of
a real client's normal codepath, so there is no hand-rolled JSON parsing
here to accidentally paper over a wire-framing bug.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
    Tool,
)

from tests.runtime.harness import BootedGateway


@asynccontextmanager
async def _session(gw: BootedGateway) -> AsyncIterator[ClientSession]:
    """Not a pytest fixture, deliberately: an async-generator fixture whose
    `async with streamable_http_client(...)` spans setup and teardown across
    pytest-asyncio's per-test event-loop boundary trips anyio's "cancel scope
    in a different task" guard on teardown. Opening and closing the session
    entirely inside one test's own coroutine avoids that class of failure."""
    async with streamable_http_client(gw.mcp_url) as (read, write):
        async with ClientSession(read, write) as sess:
            await sess.initialize()
            yield sess


class TestHandshakeEraTypedResults:
    """EC-P2-3: each of the six proxied methods answers with a typed result."""

    @pytest.mark.asyncio
    async def test_tools_list_returns_typed_result(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        async with _session(gateway_on_spare_port) as session:
            result = await session.list_tools()
        assert isinstance(result, ListToolsResult)
        assert all(isinstance(t, Tool) for t in result.tools)
        assert any(t.name == "gateway.health" for t in result.tools)

    @pytest.mark.asyncio
    async def test_tools_call_returns_typed_result(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        async with _session(gateway_on_spare_port) as session:
            result = await session.call_tool("gateway.health", {})
        assert isinstance(result, CallToolResult)
        assert result.is_error is False
        assert len(result.content) == 1

    @pytest.mark.asyncio
    async def test_tools_call_invalid_arguments_fails_schema_validation(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        """`gateway.describe` requires `tool_id: string`; feed it an int."""
        async with _session(gateway_on_spare_port) as session:
            result = await session.call_tool("gateway.describe", {"tool_id": 12345})
        assert isinstance(result, CallToolResult)
        assert result.is_error is True
        text = result.content[0].text  # type: ignore[union-attr]
        assert text.startswith("Input validation error:")

    @pytest.mark.asyncio
    async def test_resources_list_returns_typed_result(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        async with _session(gateway_on_spare_port) as session:
            result = await session.list_resources()
        assert isinstance(result, ListResourcesResult)
        assert any(r.uri == "pmcp://guidance/code-execution" for r in result.resources)

    @pytest.mark.asyncio
    async def test_resources_read_returns_typed_result(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        async with _session(gateway_on_spare_port) as session:
            result = await session.read_resource("pmcp://guidance/code-execution")
        assert isinstance(result, ReadResourceResult)
        assert len(result.contents) == 1
        assert isinstance(result.contents[0].uri, str)

    @pytest.mark.asyncio
    async def test_prompts_list_returns_typed_result(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        async with _session(gateway_on_spare_port) as session:
            result = await session.list_prompts()
        assert isinstance(result, ListPromptsResult)
        assert any(p.name == "rt-fixture::rt_greeting" for p in result.prompts)

    @pytest.mark.asyncio
    async def test_prompts_get_returns_typed_result(
        self, gateway_on_spare_port: BootedGateway
    ) -> None:
        async with _session(gateway_on_spare_port) as session:
            result = await session.get_prompt(
                "rt-fixture::rt_greeting", {"name": "wire-test"}
            )
        assert isinstance(result, GetPromptResult)
        text = result.messages[0].content.text  # type: ignore[union-attr]
        assert "wire-test" in text


def test_boot_log_has_no_fatal_error(gateway_on_spare_port: BootedGateway) -> None:
    """EC-P2-2's other half: the shared boot produced no fatal error."""
    log_text = gateway_on_spare_port.boot_log.read_text()
    assert "Fatal error" not in log_text
