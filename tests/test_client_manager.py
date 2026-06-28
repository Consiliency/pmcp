"""Tests for ClientManager."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.client.manager import (
    ClientManager,
    DEFAULT_SCHEMA_DIALECT,
    ManagedClient,
    PendingRequest,
    PREFERRED_PROTOCOL_VERSION,
    _extract_tags,
    _infer_risk_hint,
    _remote_headers,
    _truncate_description,
)
from pmcp.env_store import write_env_file
from pmcp.remote_auth import MissingRemoteHeaderAuthError
from pmcp.types import (
    LocalMcpServerConfig,
    McpTaskInfo,
    PromptInfo,
    RemoteMcpServerConfig,
    ResourceInfo,
    ResolvedServerConfig,
    RiskHint,
    ServerStatus,
    ServerStatusEnum,
    ToolInfo,
)


class TestHelperFunctions:
    """Tests for helper functions."""

    def test_infer_risk_hint_low(self) -> None:
        """Test low risk hint inference."""
        assert _infer_risk_hint("read_file", "Read a file") == RiskHint.LOW
        assert _infer_risk_hint("list_items", "List all items") == RiskHint.LOW
        assert _infer_risk_hint("search", "Search for content") == RiskHint.LOW

    def test_infer_risk_hint_high(self) -> None:
        """Test high risk hint inference."""
        assert _infer_risk_hint("delete_file", "Delete a file") == RiskHint.HIGH
        assert _infer_risk_hint("execute_command", "Run a command") == RiskHint.HIGH
        assert _infer_risk_hint("write_data", "Write data to disk") == RiskHint.HIGH

    def test_infer_risk_hint_medium(self) -> None:
        """Test medium risk hint inference (default)."""
        assert _infer_risk_hint("process_item", "Process an item") == RiskHint.MEDIUM

    def test_extract_tags(self) -> None:
        """Test tag extraction."""
        tags = _extract_tags("github", "create_issue", "Create a GitHub issue")
        assert "github" in tags

        tags = _extract_tags("fs", "read_file", "Read a file from the filesystem")
        assert "fs" in tags
        assert "file" in tags

    def test_truncate_description(self) -> None:
        """Test description truncation."""
        short = "Short description"
        assert _truncate_description(short) == short

        long = "A" * 200
        truncated = _truncate_description(long, max_length=100)
        assert len(truncated) == 100
        assert truncated.endswith("...")

        assert _truncate_description("") == ""

    def test_remote_headers_reads_explicit_project_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "project"
        other = tmp_path / "other"
        project.mkdir()
        other.mkdir()
        write_env_file(project / ".env.pmcp", {"REMOTE_TOKEN": "project-token"})
        monkeypatch.chdir(other)
        monkeypatch.delenv("REMOTE_TOKEN", raising=False)

        headers = _remote_headers(
            "remote",
            RemoteMcpServerConfig(
                type="streamable-http",
                url="https://remote.example/mcp",
                headers={"Authorization": "Bearer ${REMOTE_TOKEN}"},
            ),
            project_root=project,
        )

        assert headers == {"Authorization": "Bearer project-token"}

    def test_remote_headers_process_env_precedence_with_project_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        write_env_file(tmp_path / ".env.pmcp", {"REMOTE_TOKEN": "project-token"})
        monkeypatch.setenv("REMOTE_TOKEN", "process-token")

        headers = _remote_headers(
            "remote",
            RemoteMcpServerConfig(
                type="sse",
                url="https://remote.example/sse",
                headers={"Authorization": "Bearer ${REMOTE_TOKEN}"},
            ),
            project_root=tmp_path,
        )

        assert headers == {"Authorization": "Bearer process-token"}


def make_managed_for_protocol_tests() -> tuple[ResolvedServerConfig, ManagedClient]:
    config = ResolvedServerConfig(
        name="server",
        source="custom",
        config=LocalMcpServerConfig(command="cmd"),
    )
    status = ServerStatus(
        name="server",
        status=ServerStatusEnum.CONNECTING,
        tool_count=0,
    )
    write_stream = AsyncMock()
    managed = ManagedClient(
        config=config,
        is_remote=True,
        write_stream=write_stream,
        status=status,
    )
    return config, managed


class TestClientManager:
    """Tests for ClientManager class."""

    @pytest.fixture
    def manager(self) -> ClientManager:
        """Create a ClientManager instance."""
        return ClientManager(max_tools_per_server=100)

    @pytest.mark.asyncio
    async def test_send_initialize_prefers_current_protocol_and_records_metadata(
        self,
    ) -> None:
        """Initialize should send the preferred protocol and record server response."""
        manager = ClientManager()
        _, managed = make_managed_for_protocol_tests()
        manager._send_request = AsyncMock(
            return_value={
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {"listChanged": True}},
            }
        )

        await manager._send_initialize(managed)

        manager._send_request.assert_awaited_once()
        params = manager._send_request.await_args.args[2]
        assert params["protocolVersion"] == PREFERRED_PROTOCOL_VERSION
        assert managed.status.protocol_version == "2025-11-25"
        assert managed.status.server_capabilities == {"tools": {"listChanged": True}}
        managed.write_stream.send.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "protocol_version",
        ["2024-11-05", "2025-03-26", "2025-06-18"],
    )
    async def test_send_initialize_records_supported_older_protocol_versions(
        self, protocol_version: str
    ) -> None:
        manager = ClientManager()
        _, managed = make_managed_for_protocol_tests()
        manager._send_request = AsyncMock(
            return_value={"protocolVersion": protocol_version, "capabilities": {}}
        )

        await manager._send_initialize(managed)

        assert managed.status.protocol_version == protocol_version

    @pytest.mark.asyncio
    async def test_send_initialize_retries_legacy_on_protocol_error(self) -> None:
        manager = ClientManager()
        _, managed = make_managed_for_protocol_tests()
        manager._send_request = AsyncMock(
            side_effect=[
                Exception("initialize unsupported protocol version"),
                {"protocolVersion": "2024-11-05", "capabilities": {}},
            ]
        )

        await manager._send_initialize(managed)

        first_params = manager._send_request.await_args_list[0].args[2]
        second_params = manager._send_request.await_args_list[1].args[2]
        assert first_params["protocolVersion"] == "2025-11-25"
        assert second_params["protocolVersion"] == "2024-11-05"
        assert managed.status.protocol_version == "2024-11-05"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "protocol_version",
        ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"],
    )
    async def test_conformance_initialize_supported_protocol_versions(
        self, protocol_version: str
    ) -> None:
        manager = ClientManager()
        _, managed = make_managed_for_protocol_tests()
        capabilities = {"tools": {"listChanged": True}}
        manager._send_request = AsyncMock(
            return_value={
                "protocolVersion": protocol_version,
                "capabilities": capabilities,
            }
        )

        await manager._send_initialize(managed)

        params = manager._send_request.await_args.args[2]
        assert params["protocolVersion"] == PREFERRED_PROTOCOL_VERSION
        assert managed.status.protocol_version == protocol_version
        assert managed.status.server_capabilities == capabilities

    def test_conformance_old_and_current_fake_payload_metadata(self) -> None:
        manager = ClientManager()

        old_tool_count = manager._index_tools(
            "old-stdio",
            [{"name": "ping", "description": "Ping", "inputSchema": {}}],
        )
        old_resource_count = manager._index_resources(
            "old-stdio",
            [{"uri": "file:///old.txt", "name": "old"}],
        )
        old_prompt_count = manager._index_prompts(
            "old-stdio",
            [{"name": "old_prompt", "description": "Old prompt"}],
        )
        current_tool_count = manager._index_tools(
            "current",
            [
                {
                    "name": "render",
                    "title": "Render",
                    "description": "Render with modern metadata",
                    "inputSchema": {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                    },
                    "outputSchema": {"type": "object", "properties": {}},
                    "icons": [{"src": "render.svg"}],
                    "annotations": {"readOnlyHint": False},
                    "execution": {"taskSupport": "optional"},
                    "x-additive": {"preserved": True},
                }
            ],
        )
        current_resource_count = manager._index_resources(
            "current",
            [
                {
                    "uri": "file:///current.txt",
                    "name": "current",
                    "title": "Current Resource",
                    "icons": [{"src": "resource.svg"}],
                    "annotations": {"audience": ["assistant"]},
                    "x-resource": "kept",
                }
            ],
        )
        current_prompt_count = manager._index_prompts(
            "current",
            [
                {
                    "name": "current_prompt",
                    "title": "Current Prompt",
                    "icons": [{"src": "prompt.svg"}],
                    "annotations": {"priority": 1},
                    "arguments": [{"name": "topic", "x-arg": "kept"}],
                    "x-prompt": "kept",
                }
            ],
        )

        old_tool = manager.get_tool("old-stdio::ping")
        current_tool = manager.get_tool("current::render")
        current_resource = manager.get_resource("current::file:///current.txt")
        current_prompt = manager.get_prompt_info("current::current_prompt")

        assert (old_tool_count, old_resource_count, old_prompt_count) == (1, 1, 1)
        assert (current_tool_count, current_resource_count, current_prompt_count) == (
            1,
            1,
            1,
        )
        assert old_tool is not None
        assert old_tool.title is None
        assert old_tool.schema_dialect == DEFAULT_SCHEMA_DIALECT
        assert old_tool.raw_metadata is None
        assert current_tool is not None
        assert current_tool.title == "Render"
        assert current_tool.icons == [{"src": "render.svg"}]
        assert current_tool.output_schema == {"type": "object", "properties": {}}
        assert current_tool.annotations == {"readOnlyHint": False}
        assert current_tool.execution == {"taskSupport": "optional"}
        assert current_tool.schema_dialect == (
            "https://json-schema.org/draft/2020-12/schema"
        )
        assert current_tool.raw_metadata == {"x-additive": {"preserved": True}}
        assert current_resource is not None
        assert current_resource.raw_metadata == {"x-resource": "kept"}
        assert current_prompt is not None
        assert current_prompt.raw_metadata == {"x-prompt": "kept"}
        assert current_prompt.arguments is not None
        assert current_prompt.arguments[0].raw_metadata == {"x-arg": "kept"}

    def test_index_tools_preserves_modern_metadata_and_schema_dialect(self) -> None:
        manager = ClientManager()

        count = manager._index_tools(
            "server",
            [
                {
                    "name": "modern",
                    "title": "Modern Tool",
                    "description": "Uses modern metadata",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {
                        "$schema": "https://json-schema.org/draft/2019-09/schema",
                        "type": "object",
                    },
                    "icons": [{"src": "tool.svg", "mimeType": "image/svg+xml"}],
                    "annotations": {"readOnlyHint": True},
                    "execution": {"taskSupport": "optional"},
                    "extraField": {"kept": True},
                }
            ],
        )

        tool = manager.get_tool("server::modern")
        assert count == 1
        assert tool is not None
        assert tool.title == "Modern Tool"
        assert tool.icons == [{"src": "tool.svg", "mimeType": "image/svg+xml"}]
        assert tool.output_schema == {
            "$schema": "https://json-schema.org/draft/2019-09/schema",
            "type": "object",
        }
        assert tool.annotations == {"readOnlyHint": True}
        assert tool.execution == {"taskSupport": "optional"}
        assert tool.schema_dialect == "https://json-schema.org/draft/2019-09/schema"
        assert tool.raw_metadata == {"extraField": {"kept": True}}

    def test_index_tools_defaults_schema_dialect_and_accepts_old_payloads(
        self,
    ) -> None:
        manager = ClientManager()

        manager._index_tools(
            "server",
            [{"name": "old", "description": "Old payload", "inputSchema": {}}],
        )

        tool = manager.get_tool("server::old")
        assert tool is not None
        assert tool.title is None
        assert tool.output_schema is None
        assert tool.schema_dialect == DEFAULT_SCHEMA_DIALECT
        assert tool.raw_metadata is None

    def test_index_resources_and_prompts_preserve_metadata(self) -> None:
        manager = ClientManager()

        resource_count = manager._index_resources(
            "server",
            [
                {
                    "uri": "file://one",
                    "name": "one",
                    "title": "One",
                    "icons": [{"src": "resource.png"}],
                    "annotations": {"audience": ["assistant"]},
                    "extra": "resource-extra",
                }
            ],
        )
        prompt_count = manager._index_prompts(
            "server",
            [
                {
                    "name": "summarize",
                    "title": "Summarize",
                    "icons": [{"src": "prompt.png"}],
                    "annotations": {"priority": 1},
                    "arguments": [
                        {
                            "name": "topic",
                            "title": "Topic",
                            "required": True,
                            "extra": "argument-extra",
                        }
                    ],
                    "extra": "prompt-extra",
                }
            ],
        )

        resource = manager.get_resource("server::file://one")
        prompt = manager.get_prompt_info("server::summarize")
        assert resource_count == 1
        assert prompt_count == 1
        assert resource is not None
        assert resource.title == "One"
        assert resource.icons == [{"src": "resource.png"}]
        assert resource.annotations == {"audience": ["assistant"]}
        assert resource.raw_metadata == {"extra": "resource-extra"}
        assert prompt is not None
        assert prompt.title == "Summarize"
        assert prompt.icons == [{"src": "prompt.png"}]
        assert prompt.annotations == {"priority": 1}
        assert prompt.raw_metadata == {"extra": "prompt-extra"}
        assert prompt.arguments is not None
        assert prompt.arguments[0].title == "Topic"
        assert prompt.arguments[0].raw_metadata == {"extra": "argument-extra"}

    def test_init(self, manager: ClientManager) -> None:
        """Test ClientManager initialization."""
        assert manager._clients == {}
        assert manager._tools == {}
        assert manager._servers == {}
        assert manager._max_tools_per_server == 100

    def test_get_tool_not_found(self, manager: ClientManager) -> None:
        """Test get_tool returns None for unknown tools."""
        assert manager.get_tool("unknown::tool") is None

    def test_get_all_tools_empty(self, manager: ClientManager) -> None:
        """Test get_all_tools returns empty list initially."""
        assert manager.get_all_tools() == []

    def test_get_server_status_not_found(self, manager: ClientManager) -> None:
        """Test get_server_status returns None for unknown servers."""
        assert manager.get_server_status("unknown") is None

    def test_is_server_online_false(self, manager: ClientManager) -> None:
        """Test is_server_online returns False for unknown servers."""
        assert manager.is_server_online("unknown") is False

    def test_get_registry_meta(self, manager: ClientManager) -> None:
        """Test get_registry_meta returns revision and timestamp."""
        revision_id, last_refresh_ts = manager.get_registry_meta()
        assert revision_id.startswith("rev-")
        assert last_refresh_ts > 0

    def test_snapshot_getters_are_sorted_by_public_ids(
        self, manager: ClientManager
    ) -> None:
        manager._tools["z::beta"] = ToolInfo(
            tool_id="z::beta",
            server_name="z",
            tool_name="beta",
            description="Beta",
            short_description="Beta",
            input_schema={},
            tags=[],
            risk_hint=RiskHint.LOW,
        )
        manager._tools["a::alpha"] = ToolInfo(
            tool_id="a::alpha",
            server_name="a",
            tool_name="alpha",
            description="Alpha",
            short_description="Alpha",
            input_schema={},
            tags=[],
            risk_hint=RiskHint.LOW,
        )
        manager._resources["z::file:///z"] = ResourceInfo(
            resource_id="z::file:///z", server_name="z", uri="file:///z"
        )
        manager._resources["a::file:///a"] = ResourceInfo(
            resource_id="a::file:///a", server_name="a", uri="file:///a"
        )
        manager._prompts["z::beta"] = PromptInfo(
            prompt_id="z::beta", server_name="z", name="beta"
        )
        manager._prompts["a::alpha"] = PromptInfo(
            prompt_id="a::alpha", server_name="a", name="alpha"
        )
        manager._servers["z"] = ServerStatus(
            name="z", status=ServerStatusEnum.LAZY, tool_count=0
        )
        manager._servers["a"] = ServerStatus(
            name="a", status=ServerStatusEnum.LAZY, tool_count=0
        )

        assert [tool.tool_id for tool in manager.get_all_tools()] == [
            "a::alpha",
            "z::beta",
        ]
        assert [resource.resource_id for resource in manager.get_all_resources()] == [
            "a::file:///a",
            "z::file:///z",
        ]
        assert [prompt.prompt_id for prompt in manager.get_all_prompts()] == [
            "a::alpha",
            "z::beta",
        ]
        assert [status.name for status in manager.get_all_server_statuses()] == [
            "a",
            "z",
        ]


class TestDisconnectAll:
    """Tests for disconnect_all method."""

    @pytest.fixture
    def manager_with_client(self) -> tuple[ClientManager, ManagedClient]:
        """Create a ClientManager with a mock client."""
        manager = ClientManager()

        # Create mock process
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)

        # Create mock status
        status = ServerStatus(
            name="test",
            status=ServerStatusEnum.ONLINE,
            tool_count=5,
        )

        # Create managed client
        managed = ManagedClient(
            config=MagicMock(),
            process=mock_process,
            status=status,
        )
        managed.read_task = None

        manager._clients["test"] = managed
        manager._servers["test"] = status

        return manager, managed

    @pytest.mark.asyncio
    async def test_disconnect_all_terminates_process(
        self, manager_with_client: tuple[ClientManager, ManagedClient]
    ) -> None:
        """Test that disconnect_all terminates processes."""
        manager, managed = manager_with_client

        await manager.disconnect_all()

        process = managed.process
        assert process is not None
        cast(Any, process).terminate.assert_called_once()
        assert manager._clients == {}
        assert manager._servers == {}

    @pytest.mark.asyncio
    async def test_disconnect_all_cancels_pending_requests(
        self, manager_with_client: tuple[ClientManager, ManagedClient]
    ) -> None:
        """Test that disconnect_all cancels pending requests."""
        manager, managed = manager_with_client

        # Add pending request using PendingRequest
        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        pending = PendingRequest(
            request_id=1,
            server_name="test",
            tool_id="test::tool",
            started_at=time.time(),
            last_heartbeat=time.time(),
            timeout_ms=30000,
            future=future,
        )
        managed.pending_requests[1] = pending

        await manager.disconnect_all()

        assert future.cancelled()
        assert managed.pending_requests == {}

    @pytest.mark.asyncio
    async def test_disconnect_all_handles_timeout(
        self, manager_with_client: tuple[ClientManager, ManagedClient]
    ) -> None:
        """Test that disconnect_all kills process on timeout."""
        manager, managed = manager_with_client

        # Make wait timeout
        process = managed.process
        assert process is not None
        process.wait = AsyncMock(side_effect=asyncio.TimeoutError())

        await manager.disconnect_all()

        cast(Any, process).terminate.assert_called_once()
        cast(Any, process).kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_all_closes_remote_stack(self) -> None:
        """Test that disconnect_all closes remote SSE transports."""
        manager = ClientManager()
        status = ServerStatus(
            name="remote", status=ServerStatusEnum.ONLINE, tool_count=0
        )

        sse_stack = MagicMock()
        sse_stack.aclose = AsyncMock()

        managed = ManagedClient(
            config=MagicMock(),
            process=None,
            is_remote=True,
            sse_exit_stack=sse_stack,
            write_stream=MagicMock(),
            status=status,
        )
        manager._clients["remote"] = managed
        manager._servers["remote"] = status

        await manager.disconnect_all()

        sse_stack.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_disconnect_all_ignores_cancel_scope_task_mismatch(self) -> None:
        """Benign anyio cancel-scope mismatch should not be logged as warning."""
        manager = ClientManager()
        status = ServerStatus(
            name="remote", status=ServerStatusEnum.ONLINE, tool_count=0
        )

        sse_stack = MagicMock()
        sse_stack.aclose = AsyncMock(
            side_effect=RuntimeError(
                "Attempted to exit cancel scope in a different task than it was entered in"
            )
        )

        managed = ManagedClient(
            config=MagicMock(),
            process=None,
            is_remote=True,
            sse_exit_stack=sse_stack,
            write_stream=MagicMock(),
            status=status,
        )
        manager._clients["remote"] = managed
        manager._servers["remote"] = status

        with patch("pmcp.client.manager.logger.warning") as mock_warning:
            await manager.disconnect_all()

        assert manager._clients == {}
        assert manager._servers == {}
        mock_warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_disconnect_all_uses_stable_client_snapshot(self) -> None:
        """disconnect_all should not iterate a live _clients view while awaiting."""
        manager = ClientManager()

        process = MagicMock()
        process.returncode = None
        process.terminate = MagicMock()
        process.kill = MagicMock()

        async def wait_with_mutation() -> int:
            manager._clients["late"] = ManagedClient(
                config=MagicMock(),
                status=ServerStatus(
                    name="late", status=ServerStatusEnum.ONLINE, tool_count=0
                ),
            )
            await asyncio.sleep(0)
            return 0

        process.wait = AsyncMock(side_effect=wait_with_mutation)
        status = ServerStatus(name="test", status=ServerStatusEnum.ONLINE, tool_count=0)
        manager._clients["test"] = ManagedClient(
            config=MagicMock(), process=process, status=status
        )
        manager._servers["test"] = status

        await manager.disconnect_all()

        assert manager._clients == {}
        assert manager._servers == {}


class TestTargetServerLifecycle:
    """Tests for target-server lifecycle helpers."""

    def _add_client(self, manager: ClientManager, name: str) -> ManagedClient:
        process = MagicMock()
        process.returncode = None
        process.terminate = MagicMock()
        process.kill = MagicMock()
        process.wait = AsyncMock(return_value=0)
        status = ServerStatus(name=name, status=ServerStatusEnum.ONLINE, tool_count=1)
        config = ResolvedServerConfig(
            name=name,
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        managed = ManagedClient(config=config, process=process, status=status)
        manager._clients[name] = managed
        manager._servers[name] = status
        manager._tools[f"{name}::tool"] = ToolInfo(
            tool_id=f"{name}::tool",
            server_name=name,
            tool_name="tool",
            description="tool",
            short_description="tool",
            input_schema={},
            tags=[],
            risk_hint=RiskHint.LOW,
        )
        manager._resources[f"{name}::resource"] = ResourceInfo(
            resource_id=f"{name}::resource",
            server_name=name,
            uri=f"{name}:resource",
        )
        manager._prompts[f"{name}::prompt"] = PromptInfo(
            prompt_id=f"{name}::prompt",
            server_name=name,
            name="prompt",
        )
        return managed

    def _add_pending(
        self, managed: ManagedClient, request_id: int = 1
    ) -> asyncio.Future[Any]:
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        managed.pending_requests[request_id] = PendingRequest(
            request_id=request_id,
            server_name=managed.config.name,
            tool_id=f"{managed.config.name}::tool",
            started_at=time.time(),
            last_heartbeat=time.time(),
            timeout_ms=30000,
            future=future,
        )
        managed.status.pending_request_count = len(managed.pending_requests)
        return future

    @pytest.mark.asyncio
    async def test_disconnect_server_removes_only_target_indexes(self) -> None:
        manager = ClientManager()
        target = self._add_client(manager, "target")
        other = self._add_client(manager, "other")

        disconnected, cancelled, error = await manager.disconnect_server("target")

        assert disconnected is True
        assert cancelled == 0
        assert error is None
        assert target.process is not None
        target.process.terminate.assert_called_once()
        assert "target" not in manager._clients
        assert "other" in manager._clients
        assert manager._clients["other"] is other
        assert "target::tool" not in manager._tools
        assert "target::resource" not in manager._resources
        assert "target::prompt" not in manager._prompts
        assert "other::tool" in manager._tools
        assert "other::resource" in manager._resources
        assert "other::prompt" in manager._prompts

    @pytest.mark.asyncio
    async def test_disconnect_server_refuses_pending_without_force(self) -> None:
        manager = ClientManager()
        managed = self._add_client(manager, "target")
        future = self._add_pending(managed)

        disconnected, cancelled, error = await manager.disconnect_server("target")

        assert disconnected is False
        assert cancelled == 0
        assert error is not None
        assert "pending requests" in error
        assert future.cancelled() is False
        assert "target" in manager._clients

    @pytest.mark.asyncio
    async def test_force_disconnect_cancels_only_target_pending_requests(self) -> None:
        manager = ClientManager()
        target = self._add_client(manager, "target")
        other = self._add_client(manager, "other")
        target_future = self._add_pending(target)
        other_future = self._add_pending(other)

        disconnected, cancelled, error = await manager.disconnect_server(
            "target", force=True
        )

        assert disconnected is True
        assert error is None
        assert cancelled == 1
        assert target_future.cancelled()
        assert other_future.cancelled() is False
        assert manager.get_pending_requests("target") == []
        assert len(manager.get_pending_requests("other")) == 1

    @pytest.mark.asyncio
    async def test_restart_server_disconnects_before_singleflight_connect(self) -> None:
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="target",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        events: list[str] = []

        async def disconnect(
            name: str, force: bool = False
        ) -> tuple[bool, int, str | None]:
            events.append(f"disconnect:{name}:{force}")
            return (True, 0, None)

        async def connect(
            config: ResolvedServerConfig, retry: bool = True
        ) -> list[str]:
            events.append(f"connect:{config.name}:{retry}")
            return []

        manager.disconnect_server = disconnect  # type: ignore[method-assign]
        manager.connect_server = connect  # type: ignore[method-assign]

        ok, cancelled, errors = await manager.restart_server(config, force=True)

        assert ok is True
        assert cancelled == 0
        assert errors == []
        assert events == ["disconnect:target:True", "connect:target:True"]

    @pytest.mark.asyncio
    async def test_disconnect_server_preserves_lazy_status_for_known_config(
        self,
    ) -> None:
        manager = ClientManager()
        self._add_client(manager, "target")

        async def fail_connect(config: ResolvedServerConfig) -> None:
            raise RuntimeError("connection failed")

        manager._connect_server = fail_connect  # type: ignore[method-assign]

        disconnected, _cancelled, _error = await manager.disconnect_server("target")

        assert disconnected is True
        assert manager.is_lazy_server("target") is True
        status = manager.get_server_status("target")
        assert status is not None
        assert status.status == ServerStatusEnum.LAZY
        assert await manager.ensure_connected("target") is False


class TestRemoteSendRequest:
    """Tests for remote request transport."""

    @pytest.mark.asyncio
    async def test_send_request_remote_uses_write_stream(self) -> None:
        """Remote requests should be sent via write_stream.send."""
        manager = ClientManager()
        status = ServerStatus(
            name="remote", status=ServerStatusEnum.ONLINE, tool_count=0
        )

        write_stream = MagicMock()
        write_stream.send = AsyncMock()

        managed = ManagedClient(
            config=MagicMock(name="remote"),
            process=None,
            is_remote=True,
            write_stream=write_stream,
            status=status,
        )
        managed.config.name = "remote"

        request_task = asyncio.create_task(
            manager._send_request(managed, "tools/list", {}, timeout_ms=500)
        )
        await asyncio.sleep(0)

        pending = managed.pending_requests[1]
        pending.future.set_result({"tools": []})

        result = await request_task
        assert result == {"tools": []}
        write_stream.send.assert_awaited_once()


class TestRemoteConnectSseHeaders:
    """Tests for remote SSE header interpolation."""

    @pytest.mark.asyncio
    async def test_connect_sse_interpolates_headers_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Header values like ${VAR} should be resolved from os.environ."""
        manager = ClientManager()
        monkeypatch.setenv("PMCP_TEST_TOKEN", "test-token")

        config = ResolvedServerConfig(
            name="remote",
            source="custom",
            config=RemoteMcpServerConfig(
                url="https://example.com/sse",
                headers={
                    "Authorization": "Bearer ${PMCP_TEST_TOKEN}",
                    "X-Static": "literal-value",
                },
            ),
        )

        captured_headers: dict[str, str] = {}

        class EmptyReadStream:
            def __aiter__(self) -> "EmptyReadStream":
                return self

            async def __anext__(self) -> None:
                raise StopAsyncIteration

        @asynccontextmanager
        async def mock_sse_client(url: str, headers: dict[str, str] | None = None):
            assert url == "https://example.com/sse"
            captured_headers.update(headers or {})
            yield EmptyReadStream(), MagicMock()

        manager._send_initialize = AsyncMock()

        async def mock_send_request(*args: object, **kwargs: object) -> dict:
            method = args[1]
            if method == "tools/list":
                return {"tools": []}
            if method == "resources/list":
                return {"resources": []}
            if method == "prompts/list":
                return {"prompts": []}
            return {}

        manager._send_request = AsyncMock(side_effect=mock_send_request)
        manager._read_sse = AsyncMock()

        with patch("pmcp.client.manager.sse_client", mock_sse_client):
            await manager._connect_sse(config)

        assert captured_headers == {
            "Authorization": "Bearer test-token",
            "X-Static": "literal-value",
        }

        await manager.disconnect_all()

    @pytest.mark.asyncio
    async def test_connect_streamable_http_interpolates_headers_from_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Streamable-HTTP remote configs should use the same header interpolation."""
        manager = ClientManager()
        monkeypatch.setenv("PMCP_TEST_TOKEN", "test-token")

        config = ResolvedServerConfig(
            name="remote-http",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http",
                url="https://example.com/mcp",
                headers={
                    "Authorization": "Bearer ${PMCP_TEST_TOKEN}",
                    "X-Static": "literal-value",
                },
            ),
        )

        captured_headers: dict[str, str] = {}

        class EmptyReadStream:
            def __aiter__(self) -> "EmptyReadStream":
                return self

            async def __anext__(self) -> None:
                raise StopAsyncIteration

        @asynccontextmanager
        async def mock_streamablehttp_client(
            url: str, headers: dict[str, str] | None = None
        ):
            assert url == "https://example.com/mcp"
            captured_headers.update(headers or {})
            yield EmptyReadStream(), MagicMock(), MagicMock(return_value=None)

        manager._send_initialize = AsyncMock()

        async def mock_send_request(*args: object, **kwargs: object) -> dict:
            method = args[1]
            if method == "tools/list":
                return {"tools": []}
            if method == "resources/list":
                return {"resources": []}
            if method == "prompts/list":
                return {"prompts": []}
            return {}

        manager._send_request = AsyncMock(side_effect=mock_send_request)
        manager._read_sse = AsyncMock()

        with patch(
            "pmcp.client.manager.streamablehttp_client", mock_streamablehttp_client
        ):
            await manager._connect_streamable_http(config)

        assert captured_headers == {
            "Authorization": "Bearer test-token",
            "X-Static": "literal-value",
        }

        await manager.disconnect_all()

    @pytest.mark.asyncio
    async def test_connect_streamable_http_interpolates_headers_from_project_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager = ClientManager()
        credential = r'token with spaces # "quotes" and \ slash = value'
        write_env_file(tmp_path / ".env.pmcp", {"PMCP_TEST_TOKEN": credential})
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("PMCP_TEST_TOKEN", raising=False)

        config = ResolvedServerConfig(
            name="remote-http",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http",
                url="https://example.com/mcp",
                headers={"Authorization": "Bearer ${PMCP_TEST_TOKEN}"},
            ),
        )
        captured_headers: dict[str, str] = {}

        class EmptyReadStream:
            def __aiter__(self) -> "EmptyReadStream":
                return self

            async def __anext__(self) -> None:
                raise StopAsyncIteration

        @asynccontextmanager
        async def mock_streamablehttp_client(
            url: str, headers: dict[str, str] | None = None
        ):
            assert url == "https://example.com/mcp"
            captured_headers.update(headers or {})
            yield EmptyReadStream(), MagicMock(), MagicMock(return_value=None)

        manager._send_initialize = AsyncMock()
        manager._send_request = AsyncMock(return_value={"tools": []})
        manager._read_sse = AsyncMock()

        with patch(
            "pmcp.client.manager.streamablehttp_client", mock_streamablehttp_client
        ):
            await manager._connect_streamable_http(config)

        assert captured_headers == {"Authorization": f"Bearer {credential}"}
        await manager.disconnect_all()

    @pytest.mark.asyncio
    async def test_connect_sse_missing_placeholder_does_not_open_transport(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing SSE header placeholders should fail before sse_client is called."""
        manager = ClientManager()
        monkeypatch.delenv("PMCP_TEST_TOKEN", raising=False)
        config = ResolvedServerConfig(
            name="remote",
            source="custom",
            config=RemoteMcpServerConfig(
                url="https://example.com/sse",
                headers={"Authorization": "Bearer ${PMCP_TEST_TOKEN}"},
            ),
        )

        mock_sse_client = MagicMock()
        with patch("pmcp.client.manager.sse_client", mock_sse_client):
            with pytest.raises(MissingRemoteHeaderAuthError) as exc_info:
                await manager._connect_sse(config)

        assert exc_info.value.missing_env_vars == ["PMCP_TEST_TOKEN"]
        mock_sse_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_connect_streamable_http_missing_placeholders_are_deduped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing streamable HTTP header placeholders should be sorted and deduped."""
        manager = ClientManager()
        monkeypatch.delenv("PMCP_TEST_TOKEN", raising=False)
        monkeypatch.delenv("PMCP_OTHER_TOKEN", raising=False)
        config = ResolvedServerConfig(
            name="remote-http",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http",
                url="https://example.com/mcp",
                headers={
                    "Authorization": "Bearer ${PMCP_TEST_TOKEN}",
                    "X-Api-Key": "${PMCP_TEST_TOKEN}:${PMCP_OTHER_TOKEN}",
                },
            ),
        )

        mock_streamablehttp_client = MagicMock()
        with patch(
            "pmcp.client.manager.streamablehttp_client", mock_streamablehttp_client
        ):
            with pytest.raises(MissingRemoteHeaderAuthError) as exc_info:
                await manager._connect_streamable_http(config)

        assert exc_info.value.missing_env_vars == [
            "PMCP_OTHER_TOKEN",
            "PMCP_TEST_TOKEN",
        ]
        mock_streamablehttp_client.assert_not_called()

    def test_remote_headers_passes_tenant_context_to_resolver(self) -> None:
        config = RemoteMcpServerConfig(
            type="streamable-http",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer ${PMCP_TEST_TOKEN}"},
        )

        with patch("pmcp.client.manager.resolve_remote_headers_for_tenant") as resolver:
            resolver.return_value.resolved_headers = {
                "Authorization": "Bearer tenant-secret"
            }
            resolver.return_value.missing_env_vars = []
            headers = _remote_headers("remote-http", config, tenant_id="tenant-a")

        assert headers == {"Authorization": "Bearer tenant-secret"}
        resolver.assert_called_once_with(
            config.headers,
            server_name="remote-http",
            tenant_id="tenant-a",
            project_root=None,
        )


class TestRemoteConnectTransportDispatch:
    """Tests for remote transport dispatch."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("config_type", ["http", "streamable-http"])
    async def test_http_remote_types_use_streamable_http(
        self, config_type: str
    ) -> None:
        """HTTP-style remote configs should use the streamable-HTTP client."""
        manager = ClientManager()
        config = ResolvedServerConfig(
            name=f"remote-{config_type}",
            source="custom",
            config=RemoteMcpServerConfig(
                type=cast(Any, config_type),
                url="https://example.com/mcp",
            ),
        )
        manager._connect_streamable_http = AsyncMock()  # type: ignore[method-assign]
        manager._connect_sse = AsyncMock()  # type: ignore[method-assign]

        await manager._connect_server(config)

        manager._connect_streamable_http.assert_awaited_once_with(config)  # type: ignore[attr-defined]
        manager._connect_sse.assert_not_awaited()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("config_type", ["sse", "remote"])
    async def test_legacy_remote_types_use_sse(self, config_type: str) -> None:
        """SSE and legacy remote configs should keep using the SSE client."""
        manager = ClientManager()
        config = ResolvedServerConfig(
            name=f"remote-{config_type}",
            source="custom",
            config=RemoteMcpServerConfig(
                type=cast(Any, config_type),
                url="https://example.com/sse",
            ),
        )
        manager._connect_streamable_http = AsyncMock()  # type: ignore[method-assign]
        manager._connect_sse = AsyncMock()  # type: ignore[method-assign]

        await manager._connect_server(config)

        manager._connect_sse.assert_awaited_once_with(config)  # type: ignore[attr-defined]
        manager._connect_streamable_http.assert_not_awaited()  # type: ignore[attr-defined]


class TestCallTool:
    """Tests for call_tool method."""

    @pytest.fixture
    def manager_with_tool(self) -> ClientManager:
        """Create a ClientManager with a mock tool."""
        manager = ClientManager()

        # Add a tool
        from pmcp.types import ToolInfo

        tool = ToolInfo(
            tool_id="test::echo",
            server_name="test",
            tool_name="echo",
            description="Echo input",
            short_description="Echo input",
            input_schema={"type": "object"},
            tags=["test"],
            risk_hint=RiskHint.LOW,
        )
        manager._tools["test::echo"] = tool

        return manager

    @pytest.mark.asyncio
    async def test_call_tool_unknown_tool(
        self, manager_with_tool: ClientManager
    ) -> None:
        """Test call_tool raises for unknown tools."""
        with pytest.raises(ValueError, match="Unknown tool"):
            await manager_with_tool.call_tool("unknown::tool", {})

    @pytest.mark.asyncio
    async def test_call_tool_server_not_connected(
        self, manager_with_tool: ClientManager
    ) -> None:
        """Test call_tool raises when server not connected."""
        with pytest.raises(RuntimeError, match="not connected"):
            await manager_with_tool.call_tool("test::echo", {})

    @pytest.mark.asyncio
    async def test_call_tool_optional_task_records_downstream_task(
        self, manager_with_tool: ClientManager
    ) -> None:
        tool = manager_with_tool._tools["test::echo"]
        tool.execution = {"taskSupport": "optional"}
        managed = ManagedClient(
            config=ResolvedServerConfig(
                name="test",
                source="custom",
                config=LocalMcpServerConfig(command="test"),
            ),
            is_remote=True,
            write_stream=MagicMock(),
            status=ServerStatus(
                name="test",
                status=ServerStatusEnum.ONLINE,
                tool_count=1,
                server_capabilities={"tasks": {}},
            ),
        )
        manager_with_tool._clients["test"] = managed
        manager_with_tool._send_request = AsyncMock(
            return_value={"task": {"taskId": "downstream-1", "status": "working"}}
        )

        result = await manager_with_tool.call_tool(
            "test::echo", {"x": 1}, task={"metadata": {"kind": "slow"}}
        )

        assert result["task"]["taskId"] == "downstream-1"
        manager_with_tool._send_request.assert_awaited_once()
        params = manager_with_tool._send_request.await_args.args[2]
        assert params == {
            "name": "echo",
            "arguments": {"x": 1},
            "task": {"metadata": {"kind": "slow"}},
        }
        record = manager_with_tool.get_task_record("test", "downstream-1")
        assert record is not None
        assert record.status == "working"
        assert record.tool_id == "test::echo"

    @pytest.mark.asyncio
    async def test_tenant_code_mode_call_forwards_task_and_trace_metadata(
        self, manager_with_tool: ClientManager
    ) -> None:
        manager_with_tool._tools["tenant-code-mode::run_script"] = ToolInfo(
            tool_id="tenant-code-mode::run_script",
            server_name="tenant-code-mode",
            tool_name="run_script",
            description="Submit sandbox code for execution",
            short_description="Run sandbox code",
            input_schema={"type": "object", "properties": {}},
            tags=["tenant", "sandbox"],
            risk_hint=RiskHint.MEDIUM,
            execution={"taskSupport": "optional"},
        )
        managed = ManagedClient(
            config=ResolvedServerConfig(
                name="tenant-code-mode",
                source="custom",
                config=RemoteMcpServerConfig(
                    type="streamable-http", url="https://tenant.example/mcp"
                ),
            ),
            is_remote=True,
            write_stream=MagicMock(),
            status=ServerStatus(
                name="tenant-code-mode",
                status=ServerStatusEnum.ONLINE,
                tool_count=1,
                server_capabilities={"tasks": {}},
            ),
        )
        manager_with_tool._clients["tenant-code-mode"] = managed
        manager_with_tool._send_request = AsyncMock(
            return_value={
                "task": {
                    "task_id": "tenant-run-1",
                    "status": "working",
                    "ttl": 300,
                    "poll_interval": 2.5,
                    "diagnostics": {"summary": "queued"},
                }
            }
        )

        result = await manager_with_tool.call_tool(
            "tenant-code-mode::run_script",
            {"language": "python"},
            task={
                "metadata": {"run_kind": "smoke"},
                "ttl": 300,
                "poll_interval": 2.5,
                "requestor_context": {"client": "mobile"},
            },
            trace_context={
                "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
                "tracestate": "tenant=dev",
                "baggage": "request=hostmeta",
            },
        )

        assert result["task"]["task_id"] == "tenant-run-1"
        params = manager_with_tool._send_request.await_args.args[2]
        assert params == {
            "name": "run_script",
            "arguments": {"language": "python"},
            "_meta": {
                "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
                "tracestate": "tenant=dev",
                "baggage": "request=hostmeta",
            },
            "task": {
                "metadata": {"run_kind": "smoke"},
                "ttl": 300,
                "pollInterval": 2.5,
                "requestorContext": {"client": "mobile"},
            },
        }
        record = manager_with_tool.get_task_record("tenant-code-mode", "tenant-run-1")
        assert record is not None
        assert record.tool_id == "tenant-code-mode::run_script"
        assert record.requestor_context == {"client": "mobile"}
        assert record.ttl == 300
        assert record.poll_interval == 2.5
        assert record.raw["diagnostics"] == {"summary": "queued"}

    @pytest.mark.asyncio
    async def test_call_tool_preserves_trace_context_in_meta(
        self, manager_with_tool: ClientManager
    ) -> None:
        managed = ManagedClient(
            config=ResolvedServerConfig(
                name="test",
                source="custom",
                config=LocalMcpServerConfig(command="test"),
            ),
            is_remote=True,
            write_stream=MagicMock(),
            status=ServerStatus(
                name="test",
                status=ServerStatusEnum.ONLINE,
                tool_count=1,
            ),
        )
        manager_with_tool._clients["test"] = managed
        manager_with_tool._send_request = AsyncMock(return_value={"ok": True})

        await manager_with_tool.call_tool(
            "test::echo",
            {"x": 1},
            trace_context={"traceparent": "00-abc-123-01", "baggage": "tenant=dev"},
        )

        params = manager_with_tool._send_request.await_args.args[2]
        assert params["_meta"] == {
            "traceparent": "00-abc-123-01",
            "baggage": "tenant=dev",
        }

    @pytest.mark.asyncio
    async def test_call_tool_required_task_without_server_capability_fails(
        self, manager_with_tool: ClientManager
    ) -> None:
        tool = manager_with_tool._tools["test::echo"]
        tool.execution = {"taskSupport": "required"}
        managed = ManagedClient(
            config=ResolvedServerConfig(
                name="test",
                source="custom",
                config=LocalMcpServerConfig(command="test"),
            ),
            is_remote=True,
            write_stream=MagicMock(),
            status=ServerStatus(
                name="test",
                status=ServerStatusEnum.ONLINE,
                tool_count=1,
                server_capabilities={},
            ),
        )
        manager_with_tool._clients["test"] = managed

        with pytest.raises(RuntimeError, match="does not advertise MCP task support"):
            await manager_with_tool.call_tool("test::echo", {})

    @pytest.mark.asyncio
    async def test_task_proxy_methods_update_registry(
        self, manager_with_tool: ClientManager
    ) -> None:
        managed = ManagedClient(
            config=ResolvedServerConfig(
                name="test",
                source="custom",
                config=LocalMcpServerConfig(command="test"),
            ),
            is_remote=True,
            write_stream=MagicMock(),
            status=ServerStatus(
                name="test",
                status=ServerStatusEnum.ONLINE,
                tool_count=1,
                server_capabilities={"tasks": {}},
            ),
        )
        manager_with_tool._clients["test"] = managed
        manager_with_tool._send_request = AsyncMock(
            side_effect=[
                {
                    "tasks": [
                        {
                            "taskId": "t1",
                            "status": "input_required",
                            "statusMessage": "needs approval",
                            "createdAt": "2026-01-02T03:04:05Z",
                            "lastUpdatedAt": "2026-01-02T03:04:06Z",
                            "ttl": 300,
                            "pollInterval": 2,
                            "metadata": {"unknown": "kept"},
                        },
                        {
                            "taskId": "opaque/downstream#2",
                            "status": "host_custom_waiting",
                            "created_at": 1760000000,
                            "last_updated_at": 1760000001.5,
                            "ttl": 120,
                            "poll_interval": 0.5,
                        },
                    ]
                },
                {
                    "task": {
                        "taskId": "t1",
                        "status": "completed",
                        "updatedAt": "2026-01-02T03:04:07Z",
                    }
                },
                {
                    "result": {"ok": True},
                    "task": {
                        "taskId": "t1",
                        "status": "completed",
                        "lastUpdatedAt": "2026-01-02T03:04:08Z",
                    },
                },
                {
                    "task": {
                        "taskId": "opaque/downstream#2",
                        "status": "cancelled",
                        "statusMessage": "cancelled by client",
                        "lastUpdatedAt": "2026-01-02T03:04:09Z",
                    }
                },
            ]
        )

        listed = await manager_with_tool.list_tasks("test")
        got = await manager_with_tool.get_task("test", "t1")
        result = await manager_with_tool.get_task_result("test", "t1")
        ok, cancelled, message = await manager_with_tool.cancel_task(
            "test", "opaque/downstream#2"
        )

        assert listed["tasks"][0]["task_id"] == "t1"
        assert listed["tasks"][0]["status_message"] == "needs approval"
        assert listed["tasks"][0]["created_at"] == pytest.approx(
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp()
        )
        assert listed["tasks"][0]["updated_at"] == pytest.approx(
            datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc).timestamp()
        )
        assert listed["tasks"][0]["ttl"] == 300
        assert listed["tasks"][0]["poll_interval"] == 2
        assert listed["tasks"][0]["raw"]["metadata"] == {"unknown": "kept"}
        assert listed["tasks"][1]["task_id"] == "opaque/downstream#2"
        assert listed["tasks"][1]["status"] == "host_custom_waiting"
        assert listed["tasks"][1]["updated_at"] == 1760000001.5
        assert got.status == "completed"
        assert result["result"] == {"ok": True}
        assert manager_with_tool.get_task_record("test", "t1").status == "completed"
        assert ok is True
        assert cancelled is not None
        assert cancelled.task_id == "opaque/downstream#2"
        assert cancelled.status == "cancelled"
        assert cancelled.status_message == "cancelled by client"
        assert message == "Task cancelled"

    def test_task_info_normalizes_sdk_timestamp_inputs(self) -> None:
        created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        parsed = McpTaskInfo(
            task_id="t1",
            created_at=created,
            updated_at="2026-01-02T03:04:06+00:00",
        )

        assert parsed.created_at == pytest.approx(created.timestamp())
        assert parsed.updated_at == pytest.approx(
            datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc).timestamp()
        )

    @pytest.mark.asyncio
    async def test_cancel_task_is_idempotent_for_terminal_tasks(
        self, manager_with_tool: ClientManager
    ) -> None:
        task = manager_with_tool._record_task(
            "test",
            manager_with_tool._task_info_from_payload(
                {"taskId": "done", "status": "completed"}
            ),
        )

        ok, returned, message = await manager_with_tool.cancel_task("test", "done")

        assert ok is True
        assert returned == task
        assert "already terminal" in message


class TestServerHealthTracking:
    """Tests for server health tracking."""

    @pytest.mark.asyncio
    async def test_read_stdout_marks_server_offline_on_eof(self) -> None:
        """Test that _read_stdout marks server offline when EOF received."""
        manager = ClientManager()

        # Create mock status
        status = ServerStatus(
            name="test",
            status=ServerStatusEnum.ONLINE,
            tool_count=5,
        )

        # Create mock process with empty stdout (EOF)
        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(return_value=b"")

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout

        managed = ManagedClient(
            config=MagicMock(),
            process=mock_process,
            status=status,
        )

        # Run _read_stdout
        await manager._read_stdout("test", managed)

        # Status should be ERROR after EOF
        assert status.status == ServerStatusEnum.ERROR
        assert status.last_error == "Server process exited"

    @pytest.mark.asyncio
    async def test_read_stdout_cancels_pending_on_eof(self) -> None:
        """Test that _read_stdout cancels pending requests on EOF."""
        manager = ClientManager()

        status = ServerStatus(
            name="test",
            status=ServerStatusEnum.ONLINE,
            tool_count=5,
        )

        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(return_value=b"")

        mock_process = MagicMock()
        mock_process.stdout = mock_stdout

        managed = ManagedClient(
            config=MagicMock(),
            process=mock_process,
            status=status,
        )

        # Add pending request using PendingRequest
        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        pending = PendingRequest(
            request_id=1,
            server_name="test",
            tool_id="test::tool",
            started_at=time.time(),
            last_heartbeat=time.time(),
            timeout_ms=30000,
            future=future,
        )
        managed.pending_requests[1] = pending

        await manager._read_stdout("test", managed)

        # Request should be failed with ConnectionError
        assert future.done()
        with pytest.raises(ConnectionError):
            future.result()


class TestResourcesAndPrompts:
    """Tests for resource and prompt support."""

    @pytest.fixture
    def manager(self) -> ClientManager:
        """Create a ClientManager instance."""
        return ClientManager()

    def test_init_has_resources_and_prompts(self, manager: ClientManager) -> None:
        """Test ClientManager initializes with empty resources and prompts."""
        assert manager._resources == {}
        assert manager._prompts == {}

    def test_get_resource_not_found(self, manager: ClientManager) -> None:
        """Test get_resource returns None for unknown resources."""
        assert manager.get_resource("unknown::resource") is None

    def test_get_all_resources_empty(self, manager: ClientManager) -> None:
        """Test get_all_resources returns empty list initially."""
        assert manager.get_all_resources() == []

    def test_get_prompt_info_not_found(self, manager: ClientManager) -> None:
        """Test get_prompt_info returns None for unknown prompts."""
        assert manager.get_prompt_info("unknown::prompt") is None

    def test_get_all_prompts_empty(self, manager: ClientManager) -> None:
        """Test get_all_prompts returns empty list initially."""
        assert manager.get_all_prompts() == []

    @pytest.fixture
    def manager_with_resources(self) -> ClientManager:
        """Create a ClientManager with test resources."""
        from pmcp.types import ResourceInfo

        manager = ClientManager()

        resource = ResourceInfo(
            resource_id="test::file:///test.txt",
            server_name="test",
            uri="file:///test.txt",
            name="test.txt",
            description="A test file",
            mime_type="text/plain",
        )
        manager._resources["test::file:///test.txt"] = resource

        return manager

    @pytest.fixture
    def manager_with_prompts(self) -> ClientManager:
        """Create a ClientManager with test prompts."""
        from pmcp.types import PromptArgumentInfo, PromptInfo

        manager = ClientManager()

        prompt = PromptInfo(
            prompt_id="test::greeting",
            server_name="test",
            name="greeting",
            description="A greeting prompt",
            arguments=[
                PromptArgumentInfo(
                    name="name",
                    description="Name to greet",
                    required=True,
                )
            ],
        )
        manager._prompts["test::greeting"] = prompt

        return manager

    def test_get_resource_found(self, manager_with_resources: ClientManager) -> None:
        """Test get_resource returns resource info."""
        resource = manager_with_resources.get_resource("test::file:///test.txt")
        assert resource is not None
        assert resource.name == "test.txt"
        assert resource.mime_type == "text/plain"

    def test_get_all_resources(self, manager_with_resources: ClientManager) -> None:
        """Test get_all_resources returns all resources."""
        resources = manager_with_resources.get_all_resources()
        assert len(resources) == 1
        assert resources[0].uri == "file:///test.txt"

    def test_get_prompt_info_found(self, manager_with_prompts: ClientManager) -> None:
        """Test get_prompt_info returns prompt info."""
        prompt = manager_with_prompts.get_prompt_info("test::greeting")
        assert prompt is not None
        assert prompt.name == "greeting"
        assert prompt.arguments is not None
        assert len(prompt.arguments) == 1

    def test_get_all_prompts(self, manager_with_prompts: ClientManager) -> None:
        """Test get_all_prompts returns all prompts."""
        prompts = manager_with_prompts.get_all_prompts()
        assert len(prompts) == 1
        assert prompts[0].name == "greeting"

    @pytest.mark.asyncio
    async def test_read_resource_unknown(
        self, manager_with_resources: ClientManager
    ) -> None:
        """Test read_resource raises for unknown resources."""
        with pytest.raises(ValueError, match="Unknown resource"):
            await manager_with_resources.read_resource("unknown::resource")

    @pytest.mark.asyncio
    async def test_read_resource_server_not_connected(
        self, manager_with_resources: ClientManager
    ) -> None:
        """Test read_resource raises when server not connected."""
        with pytest.raises(RuntimeError, match="not connected"):
            await manager_with_resources.read_resource("test::file:///test.txt")

    @pytest.mark.asyncio
    async def test_get_prompt_unknown(
        self, manager_with_prompts: ClientManager
    ) -> None:
        """Test get_prompt raises for unknown prompts."""
        with pytest.raises(ValueError, match="Unknown prompt"):
            await manager_with_prompts.get_prompt("unknown::prompt")

    @pytest.mark.asyncio
    async def test_get_prompt_server_not_connected(
        self, manager_with_prompts: ClientManager
    ) -> None:
        """Test get_prompt raises when server not connected."""
        with pytest.raises(RuntimeError, match="not connected"):
            await manager_with_prompts.get_prompt("test::greeting")


class TestParallelConnections:
    """Tests for parallel connection behavior."""

    @pytest.mark.asyncio
    async def test_connect_all_empty_list(self) -> None:
        """Test connect_all with empty config list."""
        manager = ClientManager()
        errors = await manager.connect_all([])
        assert errors == []

    @pytest.mark.asyncio
    async def test_connect_all_parallel_execution(self) -> None:
        """Test that connect_all runs connections in parallel."""
        manager = ClientManager()
        call_times: list[float] = []

        async def mock_connect(config: MagicMock) -> None:
            call_times.append(time.time())
            await asyncio.sleep(0.1)  # Simulate connection time

        # Patch the connection method
        manager._connect_server = mock_connect  # type: ignore[method-assign]

        # Create mock configs
        configs = [MagicMock(name=f"server{i}") for i in range(3)]

        start = time.time()
        await manager.connect_all(configs, retry=False)  # type: ignore[arg-type]
        elapsed = time.time() - start

        # If parallel, should complete in ~0.1s, not ~0.3s
        assert elapsed < 0.2, f"Expected parallel execution, took {elapsed}s"
        assert len(call_times) == 3

    @pytest.mark.asyncio
    async def test_connect_all_collects_errors(self) -> None:
        """Test that connect_all collects errors from failed connections."""
        manager = ClientManager()

        async def mock_connect(config: MagicMock) -> None:
            if getattr(config, "_server_name", "") == "fail":
                raise RuntimeError("Connection failed")

        manager._connect_server = mock_connect  # type: ignore[method-assign]

        # Create configs with server names
        configs = []
        for name in ["success", "fail", "success2"]:
            config = MagicMock()
            config._server_name = name
            config.name = name
            configs.append(config)

        errors = await manager.connect_all(configs, retry=False)  # type: ignore[arg-type]
        assert len(errors) == 1
        assert "fail" in errors[0]
        assert "Connection failed" in errors[0]

    @pytest.mark.asyncio
    async def test_connect_all_deduplicates_same_name_configs(self) -> None:
        """Duplicate server names should share one connection attempt."""
        manager = ClientManager()
        calls: list[str] = []

        async def mock_connect(config: ResolvedServerConfig) -> None:
            calls.append(config.name)
            await asyncio.sleep(0.05)

        manager._connect_server = mock_connect  # type: ignore[method-assign]
        same_a = ResolvedServerConfig(
            name="same",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        same_b = ResolvedServerConfig(
            name="same",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        other = ResolvedServerConfig(
            name="other",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )

        start = time.time()
        errors = await manager.connect_all([same_a, same_b, other], retry=False)

        assert errors == []
        assert sorted(calls) == ["other", "same"]
        assert time.time() - start < 0.09

    @pytest.mark.asyncio
    async def test_concurrent_connect_all_calls_share_same_server_attempt(self) -> None:
        """Concurrent callers for one server should observe the same connect task."""
        manager = ClientManager()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0
        config = ResolvedServerConfig(
            name="shared",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )

        async def mock_connect(config: ResolvedServerConfig) -> None:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            manager._servers[config.name] = ServerStatus(
                name=config.name,
                status=ServerStatusEnum.ONLINE,
                tool_count=0,
            )

        manager._connect_server = mock_connect  # type: ignore[method-assign]

        first = asyncio.create_task(manager.connect_all([config], retry=False))
        second = asyncio.create_task(manager.connect_all([config], retry=False))
        await started.wait()
        release.set()

        assert await first == []
        assert await second == []
        assert calls == 1

    @pytest.mark.asyncio
    async def test_soak_concurrent_lazy_invokes_share_one_connect_attempt(self) -> None:
        """Bounded concurrent lazy users should share one downstream startup."""
        manager = ClientManager()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0
        config = ResolvedServerConfig(
            name="lazy",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        manager.register_lazy_configs([config])

        async def mock_connect(config: ResolvedServerConfig) -> None:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            process = MagicMock()
            process.returncode = None
            manager._clients[config.name] = ManagedClient(
                config=config,
                process=process,
                status=ServerStatus(
                    name=config.name,
                    status=ServerStatusEnum.ONLINE,
                    tool_count=1,
                ),
            )
            manager._servers[config.name] = manager._clients[config.name].status
            manager._tools[f"{config.name}::echo"] = ToolInfo(
                tool_id=f"{config.name}::echo",
                server_name=config.name,
                tool_name="echo",
                description="Echo",
                short_description="Echo",
                input_schema={},
                tags=[],
                risk_hint=RiskHint.LOW,
            )

        async def mock_send_request(
            managed: ManagedClient,
            method: str,
            params: dict[str, Any],
            tool_id: str = "",
            timeout_ms: int = 30000,
        ) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": params["name"]}]}

        manager._connect_server = mock_connect  # type: ignore[method-assign]
        manager._send_request = mock_send_request  # type: ignore[method-assign]

        async def client_call() -> Any:
            assert await manager.ensure_connected("lazy") is True
            return await manager.call_tool("lazy::echo", {}, timeout_ms=1000)

        tasks = [asyncio.create_task(client_call()) for _ in range(5)]
        await started.wait()
        assert calls == 1
        release.set()

        results = await asyncio.gather(*tasks)

        assert calls == 1
        assert len(results) == 5
        assert manager.is_server_online("lazy") is True
        assert manager.is_lazy_server("lazy") is False

    @pytest.mark.asyncio
    async def test_soak_active_tool_call_refuses_default_disconnect(self) -> None:
        """Pending request visibility should stay stable during refused lifecycle."""
        manager = ClientManager()
        started = asyncio.Event()
        release = asyncio.Event()
        config = ResolvedServerConfig(
            name="active",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        process = MagicMock()
        process.returncode = None
        status = ServerStatus(
            name="active", status=ServerStatusEnum.ONLINE, tool_count=1
        )
        managed = ManagedClient(config=config, process=process, status=status)
        manager._clients["active"] = managed
        manager._servers["active"] = status
        manager._tools["active::echo"] = ToolInfo(
            tool_id="active::echo",
            server_name="active",
            tool_name="echo",
            description="Echo",
            short_description="Echo",
            input_schema={},
            tags=[],
            risk_hint=RiskHint.LOW,
        )

        async def mock_send_request(
            managed: ManagedClient,
            method: str,
            params: dict[str, Any],
            tool_id: str = "",
            timeout_ms: int = 30000,
        ) -> dict[str, Any]:
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            pending = PendingRequest(
                request_id=7,
                server_name="active",
                tool_id=tool_id,
                started_at=time.time(),
                last_heartbeat=time.time(),
                timeout_ms=timeout_ms,
                future=future,
            )
            managed.pending_requests[7] = pending
            managed.status.pending_request_count = len(managed.pending_requests)
            started.set()
            await release.wait()
            future.set_result({"ok": True})
            managed.pending_requests.pop(7, None)
            managed.status.pending_request_count = len(managed.pending_requests)
            return {"content": [{"type": "text", "text": "ok"}]}

        manager._send_request = mock_send_request  # type: ignore[method-assign]

        call = asyncio.create_task(
            manager.call_tool("active::echo", {}, timeout_ms=30000)
        )
        await started.wait()

        pending = manager.get_pending_requests("active")
        assert [p.request_id for p in pending] == [7]
        disconnected, cancelled, error = await manager.disconnect_server("active")

        assert disconnected is False
        assert cancelled == 0
        assert error is not None
        assert "pending requests" in error
        assert manager.get_pending_requests("active")[0].request_id == 7
        assert process.terminate.call_count == 0

        release.set()
        assert await call == {"content": [{"type": "text", "text": "ok"}]}
        assert manager.get_pending_requests("active") == []

    @pytest.mark.asyncio
    async def test_refresh_serializes_disconnect_and_connect_cycles(self) -> None:
        """Concurrent refresh calls should not interleave lifecycle replacement."""
        manager = ClientManager()
        active = 0
        max_active = 0
        events: list[str] = []

        async def disconnect() -> None:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            events.append("disconnect:start")
            await asyncio.sleep(0.01)
            events.append("disconnect:end")
            active -= 1

        async def connect(
            configs: list[ResolvedServerConfig], retry: bool = True
        ) -> list[str]:
            events.append("connect:start")
            await asyncio.sleep(0.01)
            events.append("connect:end")
            return []

        manager._disconnect_all_unlocked = disconnect  # type: ignore[method-assign]
        manager._connect_all_unlocked = connect  # type: ignore[method-assign]

        await asyncio.gather(manager.refresh([]), manager.refresh([]))

        assert max_active == 1
        assert events == [
            "disconnect:start",
            "disconnect:end",
            "connect:start",
            "connect:end",
            "disconnect:start",
            "disconnect:end",
            "connect:start",
            "connect:end",
        ]

    @pytest.mark.asyncio
    async def test_refresh_does_not_deadlock_when_reconnecting_inside_lock(
        self,
    ) -> None:
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="lazy",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        calls = 0

        async def connect_server(cfg: ResolvedServerConfig) -> None:
            nonlocal calls
            calls += 1

        manager._connect_server = connect_server  # type: ignore[method-assign]

        await asyncio.wait_for(manager.refresh([config]), timeout=1.0)

        assert calls == 1

    @pytest.mark.asyncio
    async def test_concurrent_connect_all_serializes_duplicate_server_starts(
        self,
    ) -> None:
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="same",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def connect_server(cfg: ResolvedServerConfig) -> None:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            manager._servers[cfg.name] = ServerStatus(
                name=cfg.name,
                status=ServerStatusEnum.ONLINE,
                tool_count=0,
            )

        manager._connect_server = connect_server  # type: ignore[method-assign]

        first = asyncio.create_task(manager.connect_all([config]))
        await entered.wait()
        second = asyncio.create_task(manager.connect_all([config]))
        await asyncio.sleep(0)
        release.set()

        assert await asyncio.gather(first, second) == [[], []]
        assert calls == 1

    @pytest.mark.asyncio
    async def test_shutdown_disconnect_all_awaits_background_task_registry(
        self,
    ) -> None:
        manager = ClientManager()
        cancelled = asyncio.Event()

        async def background() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = manager._track_background_task(asyncio.create_task(background()), "test")
        await asyncio.sleep(0)

        await manager.disconnect_all()

        assert task.done()
        assert cancelled.is_set()
        assert manager._background_tasks == set()

    @pytest.mark.asyncio
    async def test_request_ids_are_monotonic_across_reconnects(self) -> None:
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="srv",
            source="project",
            config=LocalMcpServerConfig(command="test"),
        )

        async def send_and_leave_pending(managed: ManagedClient) -> int:
            task = asyncio.create_task(
                manager._send_request(managed, "tools/list", {}, timeout_ms=1000)
            )
            await asyncio.sleep(0)
            request_id = next(iter(managed.pending_requests))
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return request_id

        first = ManagedClient(
            config=config,
            is_remote=True,
            write_stream=MagicMock(send=AsyncMock()),
            status=ServerStatus(
                name="srv", status=ServerStatusEnum.ONLINE, tool_count=0
            ),
        )
        assert await send_and_leave_pending(first) == 1

        second = ManagedClient(
            config=config,
            is_remote=True,
            write_stream=MagicMock(send=AsyncMock()),
            status=ServerStatus(
                name="srv", status=ServerStatusEnum.ONLINE, tool_count=0
            ),
        )
        assert await send_and_leave_pending(second) == 2

    @pytest.mark.asyncio
    async def test_stale_cancel_does_not_cancel_replacement_request(self) -> None:
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="srv",
            source="project",
            config=LocalMcpServerConfig(command="test"),
        )
        old_future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        old = ManagedClient(
            config=config,
            is_remote=True,
            write_stream=MagicMock(),
            status=ServerStatus(
                name="srv", status=ServerStatusEnum.ONLINE, tool_count=0
            ),
        )
        old.pending_requests[1] = PendingRequest(
            request_id=1,
            server_name="srv",
            tool_id="srv::tool",
            started_at=time.time() - 120,
            last_heartbeat=time.time() - 120,
            timeout_ms=30000,
            future=old_future,
        )
        manager._request_counters["srv"] = 1

        new_future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        new = ManagedClient(
            config=config,
            is_remote=True,
            write_stream=MagicMock(),
            status=ServerStatus(
                name="srv", status=ServerStatusEnum.ONLINE, tool_count=0
            ),
        )
        new.pending_requests[2] = PendingRequest(
            request_id=2,
            server_name="srv",
            tool_id="srv::tool",
            started_at=time.time() - 120,
            last_heartbeat=time.time() - 120,
            timeout_ms=30000,
            future=new_future,
        )
        manager._clients["srv"] = new

        status, _message, _was_stalled, _elapsed = await manager.cancel_request(
            "srv::1", force=True
        )

        assert status == "not_found"
        assert new_future.cancelled() is False
        assert 2 in new.pending_requests

    @pytest.mark.asyncio
    async def test_reconnect_guard_survives_managed_client_replacement(self) -> None:
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="srv",
            source="project",
            config=LocalMcpServerConfig(command="test"),
        )
        created = 0
        original_create_task = asyncio.create_task

        def fake_create_task(coro: Any, *args: Any, **kwargs: Any) -> asyncio.Task[Any]:
            nonlocal created
            created += 1
            coro.close()
            task: asyncio.Task[Any] = original_create_task(asyncio.sleep(3600))
            return task

        with patch("asyncio.create_task", side_effect=fake_create_task):
            first = ManagedClient(
                config=config,
                status=ServerStatus(
                    name="srv", status=ServerStatusEnum.ONLINE, tool_count=0
                ),
            )
            second = ManagedClient(
                config=config,
                status=ServerStatus(
                    name="srv", status=ServerStatusEnum.ONLINE, tool_count=0
                ),
            )
            manager._schedule_reconnect("srv", first.config)
            manager._clients["srv"] = second
            manager._schedule_reconnect("srv", second.config)

        await manager.disconnect_all()

        assert created == 1

    def test_snapshot_methods_return_new_collection_containers(self) -> None:
        """Read methods should not expose manager-owned collection containers."""
        manager = ClientManager()
        manager._tools["server::tool"] = ToolInfo(
            tool_id="server::tool",
            server_name="server",
            tool_name="tool",
            description="tool",
            short_description="tool",
            input_schema={},
            tags=[],
            risk_hint=RiskHint.LOW,
        )
        manager._resources["server::resource"] = ResourceInfo(
            resource_id="server::resource",
            server_name="server",
            uri="resource",
        )
        manager._prompts["server::prompt"] = PromptInfo(
            prompt_id="server::prompt",
            server_name="server",
            name="prompt",
        )
        manager._servers["server"] = ServerStatus(
            name="server", status=ServerStatusEnum.LAZY, tool_count=0
        )
        manager._lazy_configs["server"] = ResolvedServerConfig(
            name="server",
            source="project",
            config=LocalMcpServerConfig(command="echo"),
        )

        manager.get_all_tools().clear()
        manager.get_all_resources().clear()
        manager.get_all_prompts().clear()
        manager.get_all_server_statuses().clear()
        manager.get_lazy_server_names().clear()

        assert len(manager._tools) == 1
        assert len(manager._resources) == 1
        assert len(manager._prompts) == 1
        assert len(manager._servers) == 1
        assert len(manager._lazy_configs) == 1

    @pytest.mark.asyncio
    async def test_get_pending_requests_returns_stable_list_snapshot(self) -> None:
        """Pending request snapshots should not expose the manager-owned list."""
        manager = ClientManager()
        status = ServerStatus(
            name="server", status=ServerStatusEnum.ONLINE, tool_count=0
        )
        managed = ManagedClient(config=MagicMock(), status=status)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        pending = PendingRequest(
            request_id=1,
            server_name="server",
            tool_id="server::tool",
            started_at=time.time(),
            last_heartbeat=time.time(),
            timeout_ms=30000,
            future=future,
        )
        managed.pending_requests[1] = pending
        manager._clients["server"] = managed

        snapshot = manager.get_pending_requests()
        snapshot.clear()

        assert len(manager._clients["server"].pending_requests) == 1
        assert manager.get_pending_requests() == [pending]

    @pytest.mark.asyncio
    async def test_cancel_all_pending_requests_cancels_and_clears_each_client(
        self,
    ) -> None:
        """Bulk cancellation should clear all client pending registries."""
        manager = ClientManager()
        for name in ("one", "two"):
            status = ServerStatus(
                name=name, status=ServerStatusEnum.ONLINE, tool_count=0
            )
            managed = ManagedClient(config=MagicMock(), status=status)
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            managed.pending_requests[1] = PendingRequest(
                request_id=1,
                server_name=name,
                tool_id=f"{name}::tool",
                started_at=time.time(),
                last_heartbeat=time.time(),
                timeout_ms=30000,
                future=future,
            )
            managed.status.pending_request_count = 1
            manager._clients[name] = managed

        cancelled = manager.cancel_all_pending_requests()

        assert cancelled == 2
        for managed in manager._clients.values():
            assert managed.pending_requests == {}
            assert managed.status.pending_request_count == 0

    @pytest.mark.asyncio
    async def test_cancel_all_pending_requests_removes_completed_without_counting(
        self,
    ) -> None:
        """Completed futures should be removed without inflating cancelled count."""
        manager = ClientManager()
        status = ServerStatus(
            name="server", status=ServerStatusEnum.ONLINE, tool_count=0
        )
        managed = ManagedClient(config=MagicMock(), status=status)
        pending_future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        completed_future: asyncio.Future[Any] = (
            asyncio.get_running_loop().create_future()
        )
        completed_future.set_result("done")
        managed.pending_requests[1] = PendingRequest(
            request_id=1,
            server_name="server",
            tool_id="server::pending",
            started_at=time.time(),
            last_heartbeat=time.time(),
            timeout_ms=30000,
            future=pending_future,
        )
        managed.pending_requests[2] = PendingRequest(
            request_id=2,
            server_name="server",
            tool_id="server::done",
            started_at=time.time(),
            last_heartbeat=time.time(),
            timeout_ms=30000,
            future=completed_future,
        )
        managed.status.pending_request_count = 2
        manager._clients["server"] = managed

        cancelled = manager.cancel_all_pending_requests()

        assert cancelled == 1
        assert pending_future.cancelled()
        assert completed_future.result() == "done"
        assert managed.pending_requests == {}
        assert managed.status.pending_request_count == 0


class TestConnectionRetry:
    """Tests for connection retry behavior."""

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self) -> None:
        """Test that retry succeeds after initial failure."""
        manager = ClientManager()
        attempts = 0

        async def mock_connect(config: MagicMock) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise RuntimeError("Transient failure")

        manager._connect_server = mock_connect  # type: ignore[method-assign]

        config = MagicMock(name="retry-server")
        await manager._connect_with_retry(config)

        assert attempts == 2  # First failed, second succeeded

    @pytest.mark.asyncio
    async def test_retry_exhausts_all_attempts(self) -> None:
        """Test that retry raises after all attempts fail."""
        manager = ClientManager()
        attempts = 0

        async def mock_connect(config: MagicMock) -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError(f"Failure {attempts}")

        manager._connect_server = mock_connect  # type: ignore[method-assign]

        config = MagicMock(name="always-fail")

        with pytest.raises(RuntimeError, match="Failure 3"):
            await manager._connect_with_retry(config)

        assert attempts == 3  # All retries exhausted

    @pytest.mark.asyncio
    async def test_retry_disabled(self) -> None:
        """Test that retry can be disabled."""
        manager = ClientManager()
        attempts = 0

        async def mock_connect(config: MagicMock) -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("Failure")

        manager._connect_server = mock_connect  # type: ignore[method-assign]

        configs = [MagicMock(name="no-retry")]
        errors = await manager.connect_all(configs, retry=False)  # type: ignore[arg-type]

        assert attempts == 1  # No retry
        assert len(errors) == 1


class TestCleanupClient:
    """Tests for _cleanup_client helper."""

    def _make_manager_with_client(
        self, returncode: int | None = None, task_done: bool = False
    ) -> tuple[ClientManager, ManagedClient]:
        manager = ClientManager()
        mock_process = MagicMock()
        mock_process.returncode = returncode
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)

        mock_task = MagicMock()
        mock_task.done = MagicMock(return_value=task_done)
        mock_task.cancel = MagicMock()

        status = ServerStatus(name="test", status=ServerStatusEnum.ONLINE, tool_count=0)
        managed = ManagedClient(config=MagicMock(), process=mock_process, status=status)
        managed.read_task = mock_task  # type: ignore[assignment]

        manager._clients["test"] = managed
        manager._servers["test"] = status
        return manager, managed

    @pytest.mark.asyncio
    async def test_cleanup_client_cancels_read_task_and_kills_process(self) -> None:
        """_cleanup_client should cancel the read task and kill a running process."""
        manager, managed = self._make_manager_with_client(
            returncode=None, task_done=False
        )

        await manager._cleanup_client("test", managed)

        managed.read_task.cancel.assert_called_once()  # type: ignore[union-attr]
        managed.process.kill.assert_called_once()  # type: ignore[union-attr]
        assert "test" not in manager._clients
        assert "test" not in manager._servers

    @pytest.mark.asyncio
    async def test_cleanup_client_skips_cancel_if_task_done(self) -> None:
        """_cleanup_client should not cancel an already-done read task."""
        manager, managed = self._make_manager_with_client(
            returncode=None, task_done=True
        )

        await manager._cleanup_client("test", managed)

        managed.read_task.cancel.assert_not_called()  # type: ignore[union-attr]
        managed.process.kill.assert_called_once()  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_cleanup_client_skips_kill_if_process_exited(self) -> None:
        """_cleanup_client should not kill a process that has already exited."""
        manager, managed = self._make_manager_with_client(returncode=0, task_done=False)

        await manager._cleanup_client("test", managed)

        managed.read_task.cancel.assert_called_once()  # type: ignore[union-attr]
        managed.process.kill.assert_not_called()  # type: ignore[union-attr]


class TestConnectStdioGuard:
    """Tests for the pre-spawn guard in _connect_stdio."""

    @pytest.mark.asyncio
    async def test_connect_stdio_calls_cleanup_when_existing_client_present(
        self,
    ) -> None:
        """If _clients already has an entry for a server, _cleanup_client must be called."""
        manager = ClientManager()

        existing_status = ServerStatus(
            name="test", status=ServerStatusEnum.ONLINE, tool_count=0
        )
        existing_managed = ManagedClient(
            config=MagicMock(), process=MagicMock(), status=existing_status
        )
        manager._clients["test"] = existing_managed
        manager._servers["test"] = existing_status

        cleanup_mock = AsyncMock()
        manager._cleanup_client = cleanup_mock  # type: ignore[method-assign]

        # Make _connect_stdio fail fast after the guard so we don't need full MCP wiring
        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("abort")):
            with pytest.raises(RuntimeError, match="abort"):
                from pmcp.types import LocalMcpServerConfig

                config = ResolvedServerConfig(
                    name="test",
                    source="project",
                    config=LocalMcpServerConfig(command="fake", args=[]),
                )
                await manager._connect_stdio(config)

        cleanup_mock.assert_awaited_once_with("test", existing_managed)


class TestDisconnectAllPostKill:
    """Additional disconnect_all tests for post-SIGKILL wait behaviour."""

    @pytest.mark.asyncio
    async def test_disconnect_all_waits_after_sigkill_and_logs_on_dstate(self) -> None:
        """After SIGKILL, disconnect_all should wait up to 3s and warn if still alive."""
        manager = ClientManager()

        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.pid = 99999
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        # First call (SIGTERM wait) times out; second call (post-SIGKILL wait) also times out
        mock_process.wait = AsyncMock(side_effect=asyncio.TimeoutError())

        status = ServerStatus(name="slow", status=ServerStatusEnum.ONLINE, tool_count=0)
        managed = ManagedClient(config=MagicMock(), process=mock_process, status=status)
        managed.read_task = None
        manager._clients["slow"] = managed
        manager._servers["slow"] = status

        with patch("pmcp.client.manager.logger") as mock_logger:
            await manager.disconnect_all()

        mock_process.kill.assert_called_once()
        # Warning should mention SIGKILL or D-state
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("SIGKILL" in w or "D-state" in w for w in warning_calls)

    @pytest.mark.asyncio
    async def test_disconnect_all_second_wait_succeeds_after_sigkill(self) -> None:
        """After SIGKILL, if the process exits within 3s, no warning should be logged."""
        manager = ClientManager()

        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.pid = 11111
        mock_process.terminate = MagicMock()
        mock_process.kill = MagicMock()
        # SIGTERM wait times out; post-SIGKILL wait succeeds
        mock_process.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), 0])

        status = ServerStatus(
            name="slow2", status=ServerStatusEnum.ONLINE, tool_count=0
        )
        managed = ManagedClient(config=MagicMock(), process=mock_process, status=status)
        managed.read_task = None
        manager._clients["slow2"] = managed
        manager._servers["slow2"] = status

        with patch("pmcp.client.manager.logger") as mock_logger:
            await manager.disconnect_all()

        mock_process.kill.assert_called_once()
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert not any("SIGKILL" in w or "D-state" in w for w in warning_calls)


# ---------------------------------------------------------------------------
# Reconnect storm guard
# ---------------------------------------------------------------------------


class TestReconnectStormGuard:
    """ManagedClient.reconnecting flag prevents duplicate _reconnect_loop tasks."""

    def test_managed_client_reconnecting_default_false(self) -> None:
        """reconnecting field must start as False."""
        managed = ManagedClient(
            config=MagicMock(),
            status=ServerStatus(
                name="t", status=ServerStatusEnum.OFFLINE, tool_count=0
            ),
        )
        assert managed.reconnecting is False

    @pytest.mark.asyncio
    async def test_reconnect_loop_clears_flag_on_success(self) -> None:
        """_reconnect_loop sets reconnecting=False after a successful reconnect."""
        manager = ClientManager()
        config = MagicMock()
        status = ServerStatus(name="s", status=ServerStatusEnum.ERROR, tool_count=0)
        managed = ManagedClient(config=config, status=status)
        managed.reconnecting = True
        manager._servers["s"] = status
        manager._clients["s"] = managed

        with patch.object(manager, "_connect_with_retry", new=AsyncMock()):
            with patch("asyncio.sleep", new=AsyncMock()):
                await manager._reconnect_loop("s", config)

        assert managed.reconnecting is False

    @pytest.mark.asyncio
    async def test_reconnect_loop_clears_flag_after_all_failures(self) -> None:
        """_reconnect_loop sets reconnecting=False even when all 3 attempts fail."""
        manager = ClientManager()
        config = MagicMock()
        status = ServerStatus(name="s", status=ServerStatusEnum.ERROR, tool_count=0)
        managed = ManagedClient(config=config, status=status)
        managed.reconnecting = True
        manager._clients["s"] = managed

        with patch.object(
            manager, "_connect_with_retry", new=AsyncMock(side_effect=Exception("down"))
        ):
            with patch("asyncio.sleep", new=AsyncMock()):
                await manager._reconnect_loop("s", config)

        assert managed.reconnecting is False

    @pytest.mark.asyncio
    async def test_reconnect_loop_clears_flag_when_already_online(self) -> None:
        """_reconnect_loop exits early if server is already ONLINE, still clears flag."""
        manager = ClientManager()
        config = MagicMock()
        # Already ONLINE — someone else reconnected
        status = ServerStatus(name="s", status=ServerStatusEnum.ONLINE, tool_count=0)
        managed = ManagedClient(config=config, status=status)
        managed.reconnecting = True
        manager._clients["s"] = managed

        with patch("asyncio.sleep", new=AsyncMock()):
            await manager._reconnect_loop("s", config)

        assert managed.reconnecting is False

    @pytest.mark.asyncio
    async def test_reconnect_loop_serializes_connect_attempt_with_refresh(
        self,
    ) -> None:
        manager = ClientManager()
        config = MagicMock()
        status = ServerStatus(name="s", status=ServerStatusEnum.ERROR, tool_count=0)
        managed = ManagedClient(config=config, status=status)
        managed.reconnecting = True
        manager._clients["s"] = managed
        events: list[str] = []
        connect_entered = asyncio.Event()
        release_connect = asyncio.Event()

        async def connect(cfg: object, retry: bool = True) -> None:
            events.append("reconnect:start")
            connect_entered.set()
            await release_connect.wait()
            events.append("reconnect:end")

        async def refresh_during_reconnect() -> list[str]:
            await connect_entered.wait()
            events.append("refresh:waiting")
            result = await manager.refresh([])
            events.append("refresh:done")
            return result

        manager._connect_singleflight = connect  # type: ignore[method-assign]

        with patch("asyncio.sleep", new=AsyncMock()):
            reconnect_task = asyncio.create_task(manager._reconnect_loop("s", config))
            refresh_task = asyncio.create_task(refresh_during_reconnect())
            await connect_entered.wait()
            await asyncio.sleep(0)
            assert events == ["reconnect:start", "refresh:waiting"]
            release_connect.set()
            await asyncio.gather(reconnect_task, refresh_task)

        assert events == [
            "reconnect:start",
            "refresh:waiting",
            "reconnect:end",
            "refresh:done",
        ]
        assert managed.reconnecting is False

    @pytest.mark.asyncio
    async def test_reconnect_loop_does_not_hold_lifecycle_lock_during_backoff(
        self,
    ) -> None:
        manager = ClientManager()
        config = MagicMock()
        status = ServerStatus(name="s", status=ServerStatusEnum.ERROR, tool_count=0)
        managed = ManagedClient(config=config, status=status)
        managed.reconnecting = True
        manager._servers["s"] = status
        manager._clients["s"] = managed
        sleep_entered = asyncio.Event()
        release_sleep = asyncio.Event()

        async def sleep(delay: float) -> None:
            sleep_entered.set()
            await release_sleep.wait()

        async def connect(cfg: object, retry: bool = True) -> None:
            managed.status.status = ServerStatusEnum.ONLINE

        manager._connect_singleflight = connect  # type: ignore[method-assign]

        with patch("asyncio.sleep", new=sleep):
            reconnect_task = asyncio.create_task(manager._reconnect_loop("s", config))
            await sleep_entered.wait()
            await asyncio.wait_for(manager.ensure_connected("s"), timeout=1.0)
            release_sleep.set()
            await reconnect_task

        assert managed.reconnecting is False

    @pytest.mark.asyncio
    async def test_storm_guard_prevents_second_task_while_first_runs(self) -> None:
        """If reconnecting is True, a second _read_stdout finally block skips create_task."""
        manager = ClientManager()
        config = MagicMock()
        status = ServerStatus(name="s", status=ServerStatusEnum.ONLINE, tool_count=5)
        managed = ManagedClient(config=config, status=status)
        managed.reconnecting = False
        manager._clients["s"] = managed

        tasks_created: list[str] = []

        async def fake_reconnect_loop(name: str, cfg: object) -> None:
            tasks_created.append(name)

        # Simulate what _read_stdout finally block does, twice in rapid succession
        def _schedule_reconnect() -> None:
            managed.status.status = ServerStatusEnum.ERROR
            if managed.config is not None and not managed.reconnecting:
                managed.reconnecting = True
                asyncio.ensure_future(fake_reconnect_loop("s", config))

        _schedule_reconnect()  # first failure — should schedule
        _schedule_reconnect()  # second failure — should be a no-op

        await asyncio.sleep(0)  # let tasks run
        assert len(tasks_created) == 1, "Only one reconnect task should be created"


class TestIdleTimeout:
    """Tests for the inactivity (idle) timeout on downstream requests (#79/1a)."""

    @staticmethod
    def _managed(remote: bool = False) -> ManagedClient:
        """Build a ManagedClient with a mock process suitable for _send_request."""
        config = MagicMock()
        config.name = "test"
        status = ServerStatus(
            name="test", status=ServerStatusEnum.ONLINE, tool_count=0
        )
        process = MagicMock()
        process.returncode = None
        process.stdin = MagicMock()
        process.stdin.write = MagicMock()
        process.stdin.drain = AsyncMock()
        process.stdout = MagicMock()
        return ManagedClient(
            config=config, process=process, status=status, is_remote=remote
        )

    @pytest.mark.asyncio
    async def test_idle_timeout_survives_periodic_output(self) -> None:
        """A call that keeps producing output past the idle window completes."""
        manager = ClientManager()
        managed = self._managed()
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        now = time.time()
        pending = PendingRequest(
            request_id=1,
            server_name="test",
            tool_id="t::x",
            started_at=now,
            last_heartbeat=now,
            timeout_ms=300,
            future=future,
        )
        managed.pending_requests[1] = pending

        async def keepalive() -> None:
            # Bump well past the 0.3s idle window, then resolve.
            for _ in range(5):
                await asyncio.sleep(0.1)
                pending.last_heartbeat = time.time()
            future.set_result({"ok": True})

        task = asyncio.create_task(keepalive())
        result = await manager._await_with_idle_timeout(
            managed, 1, pending, future, idle_timeout_s=0.3, ceiling_s=100.0
        )
        await task
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_idle_timeout_fires_when_silent(self) -> None:
        """A silent downstream times out at the idle threshold and is removed."""
        manager = ClientManager()
        managed = self._managed()

        with pytest.raises(TimeoutError):
            await manager._send_request(
                managed, "tools/call", {}, tool_id="t::x", timeout_ms=200
            )

        assert managed.pending_requests == {}
        assert managed.status.pending_request_count == 0

    @pytest.mark.asyncio
    async def test_absolute_ceiling_fires_for_chatty_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A continuously-heartbeating call that never resolves hits the ceiling."""
        monkeypatch.setenv("PMCP_REQUEST_CEILING_MS", "200")
        manager = ClientManager()
        managed = self._managed()

        async def chatty() -> None:
            try:
                while True:
                    await asyncio.sleep(0.05)
                    for req in managed.pending_requests.values():
                        req.last_heartbeat = time.time()
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(chatty())
        try:
            with pytest.raises(TimeoutError):
                # idle window (400ms) never elapses thanks to chatty bumps, so the
                # 200ms ceiling is what fires.
                await manager._send_request(
                    managed, "tools/call", {}, tool_id="t::x", timeout_ms=400
                )
        finally:
            task.cancel()
            await task

        assert managed.pending_requests == {}

    @pytest.mark.asyncio
    async def test_progress_notification_bumps_pending_heartbeat_stdout(self) -> None:
        """An id:null JSON notification advances in-flight last_heartbeat (stdio)."""
        manager = ClientManager()
        managed = self._managed()
        # Graceful branch in finally; avoid scheduling a reconnect task.
        managed.status.status = ServerStatusEnum.OFFLINE
        stale = time.time() - 10
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        pending = PendingRequest(
            request_id=1,
            server_name="test",
            tool_id="t::x",
            started_at=stale,
            last_heartbeat=stale,
            timeout_ms=30000,
            future=future,
        )
        managed.pending_requests[1] = pending

        notif = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"progress": 1},
                }
            )
            + "\n"
        )
        cast(Any, managed.process).stdout.readline = AsyncMock(
            side_effect=[notif.encode(), b""]
        )

        await manager._read_stdout("test", managed)

        assert pending.last_heartbeat > stale
        # Retrieve the ConnectionError set by the EOF finally so it is not logged.
        with contextlib.suppress(Exception):
            pending.future.exception()

    @pytest.mark.asyncio
    async def test_progress_notification_bumps_pending_heartbeat_sse(self) -> None:
        """An id:null JSON notification advances in-flight last_heartbeat (SSE)."""
        manager = ClientManager()
        managed = self._managed(remote=True)
        managed.status.status = ServerStatusEnum.OFFLINE
        stale = time.time() - 10
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        pending = PendingRequest(
            request_id=1,
            server_name="test",
            tool_id="t::x",
            started_at=stale,
            last_heartbeat=stale,
            timeout_ms=30000,
            future=future,
        )
        managed.pending_requests[1] = pending

        msg = MagicMock()
        msg.message.model_dump.return_value = {
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": {},
        }

        async def stream() -> Any:
            yield msg

        await manager._read_sse("test", managed, stream())

        assert pending.last_heartbeat > stale
        with contextlib.suppress(Exception):
            pending.future.exception()

    def test_request_ceiling_ms_env_parsing(self) -> None:
        """_request_ceiling_ms parses valid values and falls back on bad ones."""
        from pmcp.client.manager import (
            DEFAULT_REQUEST_CEILING_MS,
            _request_ceiling_ms,
        )

        assert DEFAULT_REQUEST_CEILING_MS == 600000

        import os as _os

        with patch.dict("os.environ", {}, clear=False):
            _os.environ.pop("PMCP_REQUEST_CEILING_MS", None)
            assert _request_ceiling_ms() == DEFAULT_REQUEST_CEILING_MS

        with patch.dict("os.environ", {"PMCP_REQUEST_CEILING_MS": "1234"}):
            assert _request_ceiling_ms() == 1234

        for bad in ("not-a-number", "-1", "0"):
            with patch.dict("os.environ", {"PMCP_REQUEST_CEILING_MS": bad}):
                assert _request_ceiling_ms() == DEFAULT_REQUEST_CEILING_MS


class TestStdioReadLimit:
    """Regression tests for the stdout-line-too-long flake (was: 64 KiB asyncio default)."""

    def test_default_limit_is_10mb(self) -> None:
        """The shipped default must comfortably exceed real-world MCP responses."""
        from pmcp.client.manager import DEFAULT_STDIO_READ_LIMIT, _stdio_read_limit

        assert DEFAULT_STDIO_READ_LIMIT == 10 * 1024 * 1024
        # When env is unset (clear it for the duration of the test), the resolver
        # must return the default constant.
        with patch.dict("os.environ", {}, clear=False):
            import os as _os

            _os.environ.pop("PMCP_STDIO_READ_LIMIT", None)
            assert _stdio_read_limit() == DEFAULT_STDIO_READ_LIMIT

    def test_env_override_accepts_positive_int(self) -> None:
        from pmcp.client.manager import _stdio_read_limit

        with patch.dict("os.environ", {"PMCP_STDIO_READ_LIMIT": "1234567"}):
            assert _stdio_read_limit() == 1234567

    def test_env_override_falls_back_when_invalid(self) -> None:
        from pmcp.client.manager import DEFAULT_STDIO_READ_LIMIT, _stdio_read_limit

        with patch.dict("os.environ", {"PMCP_STDIO_READ_LIMIT": "not-a-number"}):
            assert _stdio_read_limit() == DEFAULT_STDIO_READ_LIMIT
        with patch.dict("os.environ", {"PMCP_STDIO_READ_LIMIT": "-1"}):
            assert _stdio_read_limit() == DEFAULT_STDIO_READ_LIMIT
        with patch.dict("os.environ", {"PMCP_STDIO_READ_LIMIT": "0"}):
            assert _stdio_read_limit() == DEFAULT_STDIO_READ_LIMIT

    @pytest.mark.asyncio
    async def test_connect_stdio_passes_limit_kwarg(self) -> None:
        """_connect_stdio must forward limit= so large lines do not LimitOverrun."""
        manager = ClientManager()
        captured: dict[str, Any] = {}

        async def fake_create(*args: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            raise RuntimeError("stop here, we only care about the kwargs")

        config = ResolvedServerConfig(
            name="x",
            source="project",
            config=LocalMcpServerConfig(command="fake", args=[]),
        )
        with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
            with pytest.raises(RuntimeError, match="stop here"):
                await manager._connect_stdio(config)

        assert "limit" in captured, "create_subprocess_exec must be called with limit="
        assert captured["limit"] == 10 * 1024 * 1024


class TestReadStdoutFailureSurfacing:
    """A LimitOverrunError or other read failure must surface its cause, not the
    misleading generic 'Server process exited' message."""

    @pytest.mark.asyncio
    async def test_limit_overrun_sets_descriptive_last_error(self) -> None:
        manager = ClientManager()

        # Build a fake StreamReader that raises LimitOverrunError on first readline.
        fake_stdout = AsyncMock()
        fake_stdout.readline = AsyncMock(
            side_effect=asyncio.LimitOverrunError(
                "Separator is found, but chunk is longer than limit", 65536
            )
        )
        fake_process = MagicMock()
        fake_process.stdout = fake_stdout
        fake_process.returncode = None

        status = ServerStatus(name="big", status=ServerStatusEnum.ONLINE, tool_count=1)
        managed = ManagedClient(config=MagicMock(), process=fake_process, status=status)
        managed.config = None  # disable the auto-reconnect path for this unit test
        manager._clients["big"] = managed

        await manager._read_stdout("big", managed)

        assert status.status == ServerStatusEnum.ERROR
        assert status.last_error is not None
        assert "read limit" in status.last_error
        assert "PMCP_STDIO_READ_LIMIT" in status.last_error

    @pytest.mark.asyncio
    async def test_generic_read_error_surfaces_in_last_error(self) -> None:
        manager = ClientManager()

        fake_stdout = AsyncMock()
        fake_stdout.readline = AsyncMock(side_effect=ConnectionResetError("pipe gone"))
        fake_process = MagicMock()
        fake_process.stdout = fake_stdout
        fake_process.returncode = None

        status = ServerStatus(name="dead", status=ServerStatusEnum.ONLINE, tool_count=1)
        managed = ManagedClient(config=MagicMock(), process=fake_process, status=status)
        managed.config = None
        manager._clients["dead"] = managed

        await manager._read_stdout("dead", managed)

        assert status.status == ServerStatusEnum.ERROR
        assert status.last_error is not None
        assert "pipe gone" in status.last_error
