"""SL-2.1 — pin the mcp 2.x upstream server-handler contract (IF-0-P2-1).

Exercises the six `_handle_*` adapters `GatewayServer` registers through
`Server.__init__(on_*=...)` directly against the real mcp 2.0.0 dispatch
surface: each method's `HandlerEntry.params_type` is the SDK's canonical
model, and each handler's return value is the SDK's canonical result model.

Also pins the Trap 5 model-reshape facts SL-2 carries through `server.py`:
`Resource`/`TextResourceContents.uri` is `str` (not `AnyUrl`), `Tool`'s
schema field is `input_schema` (not `inputSchema`), and the dropped
`extra="allow"` on `Tool` is the proxying-fidelity risk EC-P2-3 guards —
every gateway tool must survive a `model_dump(by_alias=True)` round trip
with an identical re-dump.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from mcp.server.connection import Connection
from mcp.server.context import ServerRequestContext
from mcp.server.session import ServerSession
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    Tool,
)

from pmcp.server import GatewayServer
from pmcp.tools.handlers import get_gateway_tool_definitions
from pmcp.types import PromptArgumentInfo, PromptInfo, ResourceInfo

# A HANDSHAKE_PROTOCOL_VERSIONS member; the specific value is irrelevant to
# these handlers, which read no protocol-version-gated behaviour off `ctx`.
PROTOCOL_VERSION = "2025-11-25"

METHOD_PARAMS_TYPES: dict[str, type] = {
    "tools/list": PaginatedRequestParams,
    "tools/call": CallToolRequestParams,
    "resources/list": PaginatedRequestParams,
    "resources/read": ReadResourceRequestParams,
    "prompts/list": PaginatedRequestParams,
    "prompts/get": GetPromptRequestParams,
}


def _make_ctx() -> ServerRequestContext[Any, Any]:
    """A real `ServerRequestContext` with a stubbed back-channel.

    None of the six handlers touch `ctx.session` or any other `ctx` field —
    they close over `self` exactly as the original 1.x closures did — so the
    `DispatchContext` backing the session is a bare mock; `Connection`,
    `ServerSession`, and `ServerRequestContext` themselves are the genuine
    mcp 2.0.0 types the real runner builds.
    """
    connection = Connection.from_envelope(PROTOCOL_VERSION, None, None)
    session = ServerSession(MagicMock(), connection)
    return ServerRequestContext(
        session=session,
        lifespan_context={},
        protocol_version=PROTOCOL_VERSION,
        method="test",
    )


@pytest.fixture
def server() -> GatewayServer:
    srv = GatewayServer()
    srv._create_server(instructions="test instructions")
    return srv


class TestRegistration:
    """IF-0-P2-1: registered through `Server.__init__(on_*=...)`, canonical params_type."""

    @pytest.mark.parametrize("method", list(METHOD_PARAMS_TYPES))
    def test_params_type_is_canonical(self, server: GatewayServer, method: str) -> None:
        assert server._server is not None
        entry = server._server.get_request_handler(method)
        assert entry is not None, f"no handler registered for {method!r}"
        assert entry.params_type is METHOD_PARAMS_TYPES[method]

    def test_server_discover_is_registered(self, server: GatewayServer) -> None:
        assert server._server is not None
        assert server._server.get_request_handler("server/discover") is not None

    def test_middleware_keeps_default_otel(self, server: GatewayServer) -> None:
        """Decision 3: the default OpenTelemetryMiddleware is kept, explicitly."""
        assert server._server is not None
        assert len(server._server.middleware) == 1
        assert type(server._server.middleware[0]).__name__ == "OpenTelemetryMiddleware"


class TestListTools:
    @pytest.mark.asyncio
    async def test_returns_typed_result(self, server: GatewayServer) -> None:
        assert server._server is not None
        entry = server._server.get_request_handler("tools/list")
        assert entry is not None
        result = await entry.handler(_make_ctx(), PaginatedRequestParams())
        assert isinstance(result, ListToolsResult)
        assert len(result.tools) > 0
        assert all(isinstance(t, Tool) for t in result.tools)
        names = [t.name for t in result.tools]
        assert names == sorted(names)


class TestCallTool:
    @pytest.mark.asyncio
    async def test_health_call_returns_typed_result(
        self, server: GatewayServer
    ) -> None:
        assert server._server is not None
        entry = server._server.get_request_handler("tools/call")
        assert entry is not None
        params = CallToolRequestParams(name="gateway.health", arguments={})
        result = await entry.handler(_make_ctx(), params)
        assert isinstance(result, CallToolResult)
        assert result.is_error is False
        assert len(result.content) == 1

    @pytest.mark.asyncio
    async def test_invalid_arguments_fail_schema_validation(
        self, server: GatewayServer
    ) -> None:
        """EC-P2-3: `gateway.describe` requires `tool_id: string`; feed it an int."""
        assert server._server is not None
        entry = server._server.get_request_handler("tools/call")
        assert entry is not None
        params = CallToolRequestParams(
            name="gateway.describe", arguments={"tool_id": 12345}
        )
        result = await entry.handler(_make_ctx(), params)
        assert isinstance(result, CallToolResult)
        assert result.is_error is True
        assert result.content[0].text.startswith("Input validation error:")  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_unknown_tool_skips_schema_check_and_errors_via_body(
        self, server: GatewayServer
    ) -> None:
        """No schema to validate against for an unknown tool; the original
        body's `Unknown tool` ValueError still surfaces as a caught error
        result (preserving 1.x behaviour: call_tool itself never raises)."""
        assert server._server is not None
        entry = server._server.get_request_handler("tools/call")
        assert entry is not None
        params = CallToolRequestParams(name="gateway.does_not_exist", arguments={})
        result = await entry.handler(_make_ctx(), params)
        assert isinstance(result, CallToolResult)
        assert result.is_error is False  # unchanged 1.x behaviour: not flagged isError
        assert "Unknown tool" in result.content[0].text  # type: ignore[union-attr]


class TestListResources:
    @pytest.mark.asyncio
    async def test_returns_typed_result_with_str_uri(
        self, server: GatewayServer
    ) -> None:
        server._client_manager._resources = {
            "test::file:///readme.md": ResourceInfo(
                resource_id="test::file:///readme.md",
                server_name="test",
                uri="file:///readme.md",
                name="README",
                description="Project readme",
                mime_type="text/markdown",
            ),
        }
        assert server._server is not None
        entry = server._server.get_request_handler("resources/list")
        assert entry is not None
        result = await entry.handler(_make_ctx(), PaginatedRequestParams())
        assert isinstance(result, ListResourcesResult)
        assert len(result.resources) >= 1
        for resource in result.resources:
            # Trap 5: Resource.uri is str in 2.0.0; constructing with AnyUrl
            # raises ValidationError, so this only passes if server.py builds
            # Resource(uri=<str>, ...) rather than Resource(uri=AnyUrl(...), ...).
            assert isinstance(resource.uri, str)


class TestReadResource:
    @pytest.mark.asyncio
    async def test_unknown_uri_raises(self, server: GatewayServer) -> None:
        """Exception -> JSON-RPC error mapping: the runner maps an uncaught
        ValueError, so the adapter must let it propagate rather than catch it."""
        assert server._server is not None
        entry = server._server.get_request_handler("resources/read")
        assert entry is not None
        params = ReadResourceRequestParams(uri="file:///does-not-exist")
        with pytest.raises(ValueError, match="Unknown resource"):
            await entry.handler(_make_ctx(), params)

    @pytest.mark.asyncio
    async def test_guidance_resource_returns_typed_result_with_str_uri(
        self, server: GatewayServer
    ) -> None:
        assert server._server is not None
        entry = server._server.get_request_handler("resources/read")
        assert entry is not None
        params = ReadResourceRequestParams(uri="pmcp://guidance/code-execution")
        result = await entry.handler(_make_ctx(), params)
        assert isinstance(result, ReadResourceResult)
        assert len(result.contents) == 1
        assert isinstance(result.contents[0].uri, str)


class TestListPrompts:
    @pytest.mark.asyncio
    async def test_returns_typed_result(self, server: GatewayServer) -> None:
        server._client_manager._prompts = {
            "test::greeting": PromptInfo(
                prompt_id="test::greeting",
                server_name="test",
                name="greeting",
                description="Generate a greeting",
                arguments=[
                    PromptArgumentInfo(name="name", description="Name", required=True)
                ],
            ),
        }
        assert server._server is not None
        entry = server._server.get_request_handler("prompts/list")
        assert entry is not None
        result = await entry.handler(_make_ctx(), PaginatedRequestParams())
        assert isinstance(result, ListPromptsResult)
        assert len(result.prompts) == 1


class TestGetPrompt:
    @pytest.mark.asyncio
    async def test_policy_blocked_prompt_raises(self, tmp_path: Any) -> None:
        """Exception -> JSON-RPC error mapping for the policy-denial path."""
        policy_file = tmp_path / "policy.json"
        policy_file.write_text('{"prompts": {"denylist": ["test::secret"]}}')
        srv = GatewayServer(policy_path=policy_file)
        srv._create_server(instructions="test")
        assert srv._server is not None
        entry = srv._server.get_request_handler("prompts/get")
        assert entry is not None
        params = GetPromptRequestParams(name="test::secret", arguments=None)
        with pytest.raises(ValueError, match="blocked by policy"):
            await entry.handler(_make_ctx(), params)

    @pytest.mark.asyncio
    async def test_returns_typed_result(self, server: GatewayServer) -> None:
        server._client_manager._prompts = {
            "test::greeting": PromptInfo(
                prompt_id="test::greeting",
                server_name="test",
                name="greeting",
                description="Generate a greeting",
                arguments=None,
            ),
        }

        async def fake_get_prompt(
            prompt_id: str, arguments: dict[str, str] | None
        ) -> dict[str, Any]:
            return {
                "description": "hi",
                "messages": [{"role": "user", "content": {"text": "hello"}}],
            }

        server._client_manager.get_prompt = fake_get_prompt  # type: ignore[method-assign]
        assert server._server is not None
        entry = server._server.get_request_handler("prompts/get")
        assert entry is not None
        params = GetPromptRequestParams(name="test::greeting", arguments=None)
        result = await entry.handler(_make_ctx(), params)
        assert isinstance(result, GetPromptResult)
        assert result.messages[0].content.text == "hello"  # type: ignore[union-attr]


class TestModelReshape:
    """Trap 5, pinned as a build-breaking assertion rather than a note."""

    def test_tool_schema_field_is_snake_case(self) -> None:
        assert "input_schema" in Tool.model_fields
        assert "inputSchema" not in Tool.model_fields

    def test_gateway_tools_survive_alias_round_trip(self) -> None:
        """Proxying-fidelity: catches the dropped `extra="allow"` on `Tool` —
        a field that silently disappeared in transit would fail this."""
        for tool in get_gateway_tool_definitions():
            dumped = tool.model_dump(by_alias=True)
            revalidated = Tool.model_validate(dumped)
            assert revalidated.model_dump(by_alias=True) == dumped, tool.name
