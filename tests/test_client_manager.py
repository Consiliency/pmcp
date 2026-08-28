"""Tests for ClientManager."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest

from pmcp.client.manager import (
    _MAX_LISTING_PAGES,
    ClientManager,
    DEFAULT_SCHEMA_DIALECT,
    ManagedClient,
    PendingRequest,
    PREFERRED_PROTOCOL_VERSION,
    _extract_tags,
    _infer_risk_hint,
    _remote_headers,
    _required_identity,
    _required_object,
    _terminate_process_tree,
    _truncate_description,
)
from pmcp.env_store import write_env_file
from pmcp.remote_auth import MissingRemoteHeaderAuthError
from pmcp.types import (
    LimitsPolicy,
    LocalMcpServerConfig,
    McpTaskInfo,
    McpTaskRecord,
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

    def test_risk_words_match_on_word_boundaries_not_substrings(self) -> None:
        """A risk word inside a longer word must not count.

        Observed against a real server: context7's `resolve-library-id` is a
        read-only documentation lookup, and its description contains "Source
        Reputation". Plain substring matching found "put" inside "reputation"
        and classified the tool HIGH, which then told users through
        `gateway.describe` that it "may modify data or have side effects".
        Long descriptions make that near-certain for almost any tool.
        """
        assert (
            _infer_risk_hint(
                "resolve-library-id",
                "Each result includes Source Reputation and Benchmark Score.",
            )
            != RiskHint.HIGH
        )
        # A few more of the same shape, so this is not a one-word fix.
        assert _infer_risk_hint("summarize", "Produces a runnable summary") != (
            RiskHint.HIGH
        )
        assert _infer_risk_hint("inspect", "Reads the input schema") != RiskHint.HIGH
        # Genuine whole-word matches must still be caught.
        assert _infer_risk_hint("delete_file", "Delete a file") == RiskHint.HIGH
        assert _infer_risk_hint("post_message", "Post a message") == RiskHint.HIGH

    def test_server_tool_annotations_win_over_the_keyword_guess(self) -> None:
        """MCP `ToolAnnotations` are authoritative; our heuristic is a fallback.

        A server declaring `readOnlyHint`/`destructiveHint` knows what its own
        tool does. Guessing from English prose and overriding that declaration
        is how a read-only lookup ends up labelled destructive.
        """
        # readOnly wins even when the prose screams high risk.
        assert (
            _infer_risk_hint(
                "delete_everything",
                "Delete and remove and drop all data",
                {"readOnlyHint": True},
            )
            == RiskHint.LOW
        )
        # destructive wins even when the prose looks safe.
        assert (
            _infer_risk_hint("list_items", "List all items", {"destructiveHint": True})
            == RiskHint.HIGH
        )
        # Both set: the unsafe reading is the safe default.
        assert (
            _infer_risk_hint(
                "thing", "does a thing", {"readOnlyHint": True, "destructiveHint": True}
            )
            == RiskHint.HIGH
        )
        # Absent or unrelated annotations fall through to the heuristic.
        assert _infer_risk_hint("read_file", "Read a file", None) == RiskHint.LOW
        assert (
            _infer_risk_hint("read_file", "Read a file", {"title": "x"}) == RiskHint.LOW
        )

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
        """disconnect_all signals a remote client's transport owner task and
        waits for it to unwind, rather than closing an exit stack directly
        from a foreign task (the anyio cancel-scope task-ownership fix)."""
        manager = ClientManager()
        status = ServerStatus(
            name="remote", status=ServerStatusEnum.ONLINE, tool_count=0
        )

        shutdown = asyncio.Event()

        async def owner() -> None:
            await shutdown.wait()

        owner_task = asyncio.create_task(owner())

        managed = ManagedClient(
            config=MagicMock(),
            process=None,
            is_remote=True,
            write_stream=MagicMock(),
            status=status,
            transport_owner_task=owner_task,
            transport_shutdown=shutdown,
        )
        manager._clients["remote"] = managed
        manager._servers["remote"] = status

        await manager.disconnect_all()

        assert shutdown.is_set()
        assert owner_task.done()

    @pytest.mark.asyncio
    async def test_close_remote_transport_escalates_to_cancel_on_timeout(self) -> None:
        """An owner task that ignores the graceful shutdown signal past the
        timeout budget is cancelled directly; _close_remote_transport still
        returns normally (the transport is closed either way) and logs a
        WARNING rather than raising."""
        manager = ClientManager()
        status = ServerStatus(
            name="remote", status=ServerStatusEnum.ONLINE, tool_count=0
        )
        shutdown = asyncio.Event()
        cancelled = asyncio.Event()

        async def stubborn_owner() -> None:
            # Ignores `shutdown` entirely -- only a direct task.cancel() ends
            # this, which is exactly the escalation being tested.
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        owner_task = asyncio.create_task(stubborn_owner())
        managed = ManagedClient(
            config=MagicMock(),
            process=None,
            is_remote=True,
            write_stream=MagicMock(),
            status=status,
            transport_owner_task=owner_task,
            transport_shutdown=shutdown,
        )

        with patch("pmcp.client.manager.logger.warning") as mock_warning:
            await manager._close_remote_transport("remote", managed, timeout=0.05)

        assert cancelled.is_set()
        assert owner_task.done()
        mock_warning.assert_called_once()
        assert "did not close within" in mock_warning.call_args[0][0]

    @pytest.mark.asyncio
    async def test_disconnect_server_reports_failure_when_transport_exit_raises(
        self,
    ) -> None:
        """A transport owner whose stack exit raises must be reported as a
        failed disconnect -- `(False, cancelled, "<msg>")` -- not
        `(True, ...)`. Without this, `disconnect_server`'s
        `except Exception -> return (False, cancelled, str(e))` contract is
        unenforced no matter what the design says."""
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="remote",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http", url="http://example.invalid/mcp"
            ),
        )
        status = ServerStatus(
            name="remote", status=ServerStatusEnum.ONLINE, tool_count=0
        )
        shutdown = asyncio.Event()

        async def raising_owner() -> None:
            await shutdown.wait()
            raise RuntimeError("transport exit boom")

        owner_task = asyncio.create_task(raising_owner())
        managed = ManagedClient(
            config=config,
            process=None,
            is_remote=True,
            write_stream=MagicMock(),
            status=status,
            transport_owner_task=owner_task,
            transport_shutdown=shutdown,
        )
        manager._clients["remote"] = managed
        manager._servers["remote"] = status

        ok, _cancelled, error = await manager.disconnect_server("remote", force=True)

        assert ok is False
        assert error is not None
        assert "transport exit boom" in error

    @pytest.mark.asyncio
    async def test_disconnect_server_reports_failure_when_owner_already_crashed(
        self,
    ) -> None:
        """An owner that crashes *before* anyone asks it to shut down (the
        crash-while-parked case `_on_transport_owner_done` only logs) must
        still surface its exception the next time `disconnect_server` looks
        -- not be silently treated as "already closed, nothing to report"
        just because `task.done()` is already True by the time we check.
        Board-review regression: `_close_remote_transport`'s early
        `if task.done(): return` used to discard the result entirely."""
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="remote",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http", url="http://example.invalid/mcp"
            ),
        )
        status = ServerStatus(
            name="remote", status=ServerStatusEnum.ONLINE, tool_count=0
        )
        shutdown = asyncio.Event()

        async def crashing_owner() -> None:
            # Crashes immediately, without ever waiting on `shutdown` --
            # simulates the transport dying while parked, before anyone
            # asked it to close.
            raise RuntimeError("owner crashed while parked")

        owner_task = asyncio.create_task(crashing_owner())
        # Let the owner actually finish before touching it, so `task.done()`
        # is already True when `_close_remote_transport` looks -- exercising
        # the early-return branch, not the awaited-task branch covered by
        # test_disconnect_server_reports_failure_when_transport_exit_raises.
        for _ in range(10):
            if owner_task.done():
                break
            await asyncio.sleep(0)
        assert owner_task.done()

        managed = ManagedClient(
            config=config,
            process=None,
            is_remote=True,
            write_stream=MagicMock(),
            status=status,
            transport_owner_task=owner_task,
            transport_shutdown=shutdown,
        )
        manager._clients["remote"] = managed
        manager._servers["remote"] = status

        ok, _cancelled, error = await manager.disconnect_server("remote", force=True)

        assert ok is False
        assert error is not None
        assert "owner crashed while parked" in error

    @pytest.mark.asyncio
    async def test_close_remote_transport_propagates_failure_during_timeout_escalation(
        self,
    ) -> None:
        """A transport-exit failure that surfaces while the owner unwinds
        under our own escalating `task.cancel()` (the timeout branch) must
        still propagate -- only the CancelledError our own cancel() causes
        may be swallowed. Distinct from the timeout-as-success contract,
        which stays (see test_close_remote_transport_escalates_to_cancel_on_timeout):
        that covers a *clean* forced unwind; this covers one that fails.
        Board-review regression: the escalation used to run under
        `asyncio.gather(task, return_exceptions=True)`, which discarded
        genuine failures indistinguishably from the expected CancelledError."""
        manager = ClientManager()
        status = ServerStatus(
            name="remote", status=ServerStatusEnum.ONLINE, tool_count=0
        )
        shutdown = asyncio.Event()

        async def owner_that_fails_on_cancel() -> None:
            # Ignores `shutdown` entirely, so the graceful wait always times
            # out. When escalated via task.cancel(), raises a genuine
            # failure instead of letting CancelledError propagate --
            # simulating __aexit__ swallowing the cancellation and
            # surfacing its own error, e.g. a broken connection encountered
            # during forced unwind.
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                raise RuntimeError("transport failed to unwind under cancel") from None

        owner_task = asyncio.create_task(owner_that_fails_on_cancel())
        managed = ManagedClient(
            config=MagicMock(),
            process=None,
            is_remote=True,
            write_stream=MagicMock(),
            status=status,
            transport_owner_task=owner_task,
            transport_shutdown=shutdown,
        )

        with pytest.raises(
            RuntimeError, match="transport failed to unwind under cancel"
        ):
            await manager._close_remote_transport("remote", managed, timeout=0.05)

    @pytest.mark.asyncio
    async def test_connect_cancelled_during_ownership_transfer_leaves_no_owner(
        self,
    ) -> None:
        """Cancelling the connect task while it is parked at `await ready`
        (the owner task already alive and entering the transport, but
        `ManagedClient` not yet published) must not park a live,
        unreferenced owner task. Regression for the ownership-transfer guard
        in `_connect_remote_stream` -- proven red without it during
        development (reverting `except BaseException` to `except Exception`
        there leaves the owner orphaned and this test fails)."""
        manager = ClientManager()
        config = ResolvedServerConfig(
            name="remote",
            source="custom",
            config=RemoteMcpServerConfig(
                type="streamable-http", url="http://example.invalid/mcp"
            ),
        )

        entered = asyncio.Event()
        release = asyncio.Event()

        class _HangingTransport:
            async def __aenter__(self) -> tuple[Any, Any]:
                entered.set()
                await release.wait()
                return (MagicMock(), MagicMock())

            async def __aexit__(self, *exc_info: Any) -> None:
                return None

        connect_task = asyncio.create_task(
            manager._connect_remote_stream(
                config, _HangingTransport(), transport_name="test"
            )
        )
        await entered.wait()

        connect_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await connect_task

        # Owner-task discard runs as a done-callback scheduled via
        # call_soon, not synchronously when the task completes -- yield once
        # so it has run before asserting.
        await asyncio.sleep(0)

        owner_tasks = [
            t
            for t in manager._background_tasks
            if manager._background_task_servers.get(t) == "remote"
        ]
        assert owner_tasks == []
        assert "remote" not in manager._clients

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

        # mcp 2.0.0's streamable_http_client() no longer builds its own httpx
        # client from a headers= kwarg (IF-0-P2-2) — pmcp resolves the headers
        # into an httpx2.AsyncClient it owns and passes that client in, so the
        # header assertion below spies on the AsyncClient construction rather
        # than on streamable_http_client's arguments.
        real_async_client = httpx2.AsyncClient

        def spy_async_client(*args: object, **kwargs: object) -> httpx2.AsyncClient:
            captured_headers.update(kwargs.get("headers") or {})
            return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

        @asynccontextmanager
        async def mock_streamable_http_client(
            url: str, *, http_client: httpx2.AsyncClient | None = None
        ):
            assert url == "https://example.com/mcp"
            assert http_client is not None
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

        with (
            patch("pmcp.client.manager.httpx2.AsyncClient", spy_async_client),
            patch(
                "pmcp.client.manager.streamable_http_client",
                mock_streamable_http_client,
            ),
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

        real_async_client = httpx2.AsyncClient

        def spy_async_client(*args: object, **kwargs: object) -> httpx2.AsyncClient:
            captured_headers.update(kwargs.get("headers") or {})
            return real_async_client(*args, **kwargs)  # type: ignore[arg-type]

        @asynccontextmanager
        async def mock_streamable_http_client(
            url: str, *, http_client: httpx2.AsyncClient | None = None
        ):
            assert url == "https://example.com/mcp"
            assert http_client is not None
            yield EmptyReadStream(), MagicMock()

        manager._send_initialize = AsyncMock()
        manager._send_request = AsyncMock(return_value={"tools": []})
        manager._read_sse = AsyncMock()

        with (
            patch("pmcp.client.manager.httpx2.AsyncClient", spy_async_client),
            patch(
                "pmcp.client.manager.streamable_http_client",
                mock_streamable_http_client,
            ),
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

        mock_streamable_http_client = MagicMock()
        with patch(
            "pmcp.client.manager.streamable_http_client", mock_streamable_http_client
        ):
            with pytest.raises(MissingRemoteHeaderAuthError) as exc_info:
                await manager._connect_streamable_http(config)

        assert exc_info.value.missing_env_vars == [
            "PMCP_OTHER_TOKEN",
            "PMCP_TEST_TOKEN",
        ]
        mock_streamable_http_client.assert_not_called()

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

    def test_terminal_task_records_are_evicted_past_cap(self) -> None:
        """Terminal task records are pruned past the cap; active ones survive."""
        manager = ClientManager()
        manager._max_terminal_tasks = 5

        # An active (non-terminal) record must never be evicted.
        manager._record_task(
            "srv",
            McpTaskInfo(task_id="active", status="working", updated_at=0.0),
        )

        # Record many terminal tasks with increasing updated_at timestamps.
        for i in range(20):
            manager._record_task(
                "srv",
                McpTaskInfo(
                    task_id=f"done-{i}", status="completed", updated_at=float(i + 1)
                ),
            )

        terminal = [
            t for t in manager.get_tracked_tasks("srv") if manager._terminal_task(t)
        ]
        assert len(terminal) == 5
        # Oldest terminal records were dropped; newest survive.
        surviving = {t.task_id for t in terminal}
        assert surviving == {f"done-{i}" for i in range(15, 20)}
        # The active task is untouched.
        assert manager.get_task_record("srv", "active") is not None


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
        mock_stdout.read = AsyncMock(return_value=b"")

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
        mock_stdout.read = AsyncMock(return_value=b"")

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
        # _terminate_process_tree gracefully SIGTERMs first (the process exits
        # within the wait window, so SIGKILL is never needed).
        managed.process.terminate.assert_called_once()  # type: ignore[union-attr]
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
        managed.process.terminate.assert_called_once()  # type: ignore[union-attr]

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

    @pytest.mark.asyncio
    async def test_local_config_env_reaches_subprocess(self) -> None:
        """A local server config's env dict is merged over os.environ and passed
        to the spawned subprocess (#89 AC3)."""
        manager = ClientManager()

        config = ResolvedServerConfig(
            name="index-it-mcp",
            source="project",
            config=LocalMcpServerConfig(
                command="fake",
                args=["stdio"],
                env={"MCP_ALLOWED_ROOTS": "/repo", "QDRANT_URL": "http://q"},
            ),
        )

        # Fail fast after the spawn call so we don't need full MCP wiring; the
        # env kwarg is captured from call_args.
        spawn = AsyncMock(side_effect=RuntimeError("abort"))
        with patch("asyncio.create_subprocess_exec", spawn):
            with pytest.raises(RuntimeError, match="abort"):
                await manager._connect_stdio(config)

        _, kwargs = spawn.call_args
        passed_env = kwargs["env"]
        # Custom keys present with their values...
        assert passed_env["MCP_ALLOWED_ROOTS"] == "/repo"
        assert passed_env["QDRANT_URL"] == "http://q"
        # ...merged OVER os.environ (a pre-existing key survives → not a replace).
        assert passed_env["PATH"] == os.environ["PATH"]


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

        with (
            patch("pmcp.client.manager.logger") as mock_logger,
            patch("pmcp.client.manager.os.getpgid", side_effect=ProcessLookupError),
        ):
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

        with (
            patch("pmcp.client.manager.logger") as mock_logger,
            patch("pmcp.client.manager.os.getpgid", side_effect=ProcessLookupError),
        ):
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
        status = ServerStatus(name="test", status=ServerStatusEnum.ONLINE, tool_count=0)
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
        cast(Any, managed.process).stdout.read = AsyncMock(
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

    @pytest.mark.asyncio
    async def test_connect_stdio_starts_new_session(self) -> None:
        """_connect_stdio must spawn with start_new_session=True so the whole
        process tree (e.g. browsers) can be reaped on disconnect (issue #79)."""
        manager = ClientManager()
        captured: dict[str, Any] = {}

        async def fake_create(*args: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            raise RuntimeError("stop here")

        config = ResolvedServerConfig(
            name="x",
            source="project",
            config=LocalMcpServerConfig(command="fake", args=[]),
        )
        with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
            with pytest.raises(RuntimeError, match="stop here"):
                await manager._connect_stdio(config)

        assert captured.get("start_new_session") is True


class TestTerminateProcessTree:
    """Tests for the group-aware _terminate_process_tree helper (issue #79)."""

    @pytest.mark.asyncio
    async def test_terminates_whole_process_group(self) -> None:
        """When the process leads its group, SIGTERM goes to the group; once the
        group is gone (probe raises) no SIGKILL escalation happens."""

        def killpg_side(pgid: int, sig: int) -> None:
            if sig == 0:  # liveness probe: report the group already gone
                raise ProcessLookupError
            return None

        process = MagicMock()
        process.pid = 4321
        process.returncode = None
        process.wait = AsyncMock(return_value=0)

        with (
            patch("pmcp.client.manager.os.getpgid", return_value=4321),
            patch(
                "pmcp.client.manager.os.killpg", side_effect=killpg_side
            ) as mock_killpg,
        ):
            await _terminate_process_tree(process, "browser")

        # SIGTERM was delivered to the group...
        assert any(c.args == (4321, signal.SIGTERM) for c in mock_killpg.call_args_list)
        # ...and since the group probe reported it gone, no SIGKILL escalation.
        assert not any(
            c.args == (4321, signal.SIGKILL) for c in mock_killpg.call_args_list
        )
        # Group signal succeeded, so we never fall back to single-process kill.
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_single_process_on_process_lookup_error(self) -> None:
        """If killpg raises ProcessLookupError, fall back to process.terminate()."""
        process = MagicMock()
        process.pid = 4321
        process.returncode = None
        process.wait = AsyncMock(return_value=0)

        with (
            patch("pmcp.client.manager.os.getpgid", return_value=4321),
            patch("pmcp.client.manager.os.killpg", side_effect=ProcessLookupError),
        ):
            await _terminate_process_tree(process, "browser")

        process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_noop_when_process_already_exited(self) -> None:
        """A process that already exited must not be signalled."""
        process = MagicMock()
        process.pid = 4321
        process.returncode = 0

        with patch("pmcp.client.manager.os.killpg") as mock_killpg:
            await _terminate_process_tree(process, "browser")

        mock_killpg.assert_not_called()
        process.terminate.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not hasattr(os, "killpg"), reason="process-group reaping is POSIX-only"
    )
    async def test_real_process_tree_is_reaped(self) -> None:
        """End-to-end: a grandchild spawned by the downstream process is killed.

        The mocked tests above only prove killpg is *called*; this proves the
        whole tree actually dies — the regression guard for orphaned Chrome
        holding the browser profile's SingletonLock (issue #79, symptom 1c).
        """
        # Parent (its own session, like _connect_stdio) spawns a child; the
        # parent prints the child PID then both sleep.
        script = (
            "import subprocess, time;"
            "c = subprocess.Popen(['sleep', '30']);"
            "print(c.pid, flush=True);"
            "time.sleep(30)"
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None
        child_pid = int(
            (await asyncio.wait_for(process.stdout.readline(), 10.0)).strip()
        )
        parent_pid = process.pid

        # Both are alive before reaping.
        os.kill(parent_pid, 0)
        os.kill(child_pid, 0)

        await _terminate_process_tree(process, "real")

        async def _gone(pid: int) -> bool:
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                await asyncio.sleep(0.1)
            return False

        assert await _gone(parent_pid), "parent process was not reaped"
        assert await _gone(child_pid), "grandchild process was orphaned, not reaped"

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not hasattr(os, "killpg"), reason="process-group reaping is POSIX-only"
    )
    async def test_sigterm_ignoring_grandchild_is_group_sigkilled(self) -> None:
        """A grandchild that ignores SIGTERM and outlives the leader must still be
        reaped via the group SIGKILL escalation (issue #79/1c — codex finding).

        The parent dies on SIGTERM (default disposition); the grandchild installs
        SIG_IGN for SIGTERM, so it survives the SIGTERM phase. The helper must
        then SIGKILL the surviving group rather than returning once the leader is
        gone.
        """
        child_code = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n"
        )
        parent_code = (
            "import subprocess, sys, time\n"
            f"c = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
            "print(c.pid, flush=True)\n"
            "time.sleep(60)\n"
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            parent_code,
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None
        child_pid = int(
            (await asyncio.wait_for(process.stdout.readline(), 10.0)).strip()
        )
        parent_pid = process.pid
        os.kill(parent_pid, 0)
        os.kill(child_pid, 0)

        await _terminate_process_tree(process, "ignore")

        async def _gone(pid: int) -> bool:
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    return True
                await asyncio.sleep(0.1)
            return False

        assert await _gone(parent_pid), "parent was not reaped"
        assert await _gone(child_pid), (
            "SIGTERM-ignoring grandchild was not group-SIGKILLed"
        )

    @pytest.mark.asyncio
    async def test_windows_falls_back_without_killpg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On platforms without os.killpg/os.getpgid (Windows), the helper must
        fall back to process.terminate()/kill() instead of raising AttributeError."""
        import pmcp.client.manager as mgr

        monkeypatch.delattr(mgr.os, "killpg", raising=False)
        monkeypatch.delattr(mgr.os, "getpgid", raising=False)

        process = MagicMock()
        process.pid = 4321
        process.returncode = None
        process.wait = AsyncMock(return_value=0)

        # Must not raise; must use the cross-platform single-process path.
        await _terminate_process_tree(process, "browser")
        process.terminate.assert_called_once()


class TestReadStdoutFailureSurfacing:
    """An oversized line must be dropped (failing only its request) without tearing
    down the server (issue #79/1b); a real read error must surface its cause."""

    @pytest.mark.asyncio
    async def test_oversized_line_recovers_without_disconnect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stdout line over the read limit fails the oldest in-flight request with
        an actionable message, and the server keeps processing later lines — it is
        NOT disconnected mid-stream (issue #79/1b)."""
        monkeypatch.setenv("PMCP_STDIO_READ_LIMIT", "50")
        manager = ClientManager()

        # Sequence: an oversized chunk with no newline (overflow), then the newline
        # ending that line, then a valid response to a DIFFERENT request, then EOF.
        resp2 = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"ok": True}}) + "\n"
        fake_stdout = AsyncMock()
        fake_stdout.read = AsyncMock(
            side_effect=[b"X" * 200, b"junk-tail\n", resp2.encode(), b""]
        )
        fake_process = MagicMock()
        fake_process.stdout = fake_stdout
        fake_process.returncode = None

        status = ServerStatus(name="big", status=ServerStatusEnum.ONLINE, tool_count=1)
        managed = ManagedClient(config=MagicMock(), process=fake_process, status=status)
        managed.config = None  # disable auto-reconnect for this unit test

        f1: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        f2: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        now = time.time()
        managed.pending_requests[1] = PendingRequest(
            request_id=1,
            server_name="big",
            tool_id="t::big",
            started_at=now,
            last_heartbeat=now,
            timeout_ms=30000,
            future=f1,
        )
        managed.pending_requests[2] = PendingRequest(
            request_id=2,
            server_name="big",
            tool_id="t::ok",
            started_at=now,
            last_heartbeat=now,
            timeout_ms=30000,
            future=f2,
        )
        manager._clients["big"] = managed

        await manager._read_stdout("big", managed)

        # Request 1 (oldest, the overflow) failed with the actionable limit message,
        # NOT a "disconnected" ConnectionError.
        assert f1.done()
        err = f1.exception()
        assert err is not None and "stdout line limit" in str(err)
        assert not isinstance(err, ConnectionError)
        # Request 2 arrived AFTER the oversized line and was processed normally —
        # proving the stream stayed aligned and the server was not torn down.
        assert f2.done() and f2.result() == {"ok": True}

    @pytest.mark.asyncio
    async def test_generic_read_error_surfaces_in_last_error(self) -> None:
        manager = ClientManager()

        fake_stdout = AsyncMock()
        fake_stdout.read = AsyncMock(side_effect=ConnectionResetError("pipe gone"))
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


class _RecordingSink:
    """Counts `CatalogEventSink` publishes so a test can assert on the exact
    number, not merely that something fired."""

    def __init__(self) -> None:
        self.tools = 0
        self.resources = 0
        self.prompts = 0

    def note_tools_changed(self) -> None:
        self.tools += 1

    def note_resources_changed(self) -> None:
        self.resources += 1

    def note_prompts_changed(self) -> None:
        self.prompts += 1

    async def flush(self) -> None:
        pass


class TestDownstreamReconcileScheduler:
    """FANOUT SL-1: the downstream-notification contract (IF-0-FANOUT-1) and the
    coalesced reconcile scheduler behind it.

    Owned by lane SL-1 inside a file lane SL-3 owns; kept to this one class, and
    deliberately avoiding the `-k` patterns SL-3.2 claims (`unchanged_catalog`,
    `unknown_notification`, `typed_downstream_error`).
    """

    @staticmethod
    def _managed(name: str) -> ManagedClient:
        status = ServerStatus(name=name, status=ServerStatusEnum.ONLINE, tool_count=0)
        managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
        managed.config.name = name
        return managed

    # ---- SL-1.1: IF-0-FANOUT-1 shape ------------------------------------

    def test_reconcile_contract_entry_point_has_frozen_signature(self) -> None:
        """IF-0-FANOUT-1's entry point exists, is synchronous (both dispatch paths
        call it, and the stdio one is not a coroutine), and takes the frozen
        `(name, managed, method)` parameters."""
        import inspect

        handler = ClientManager._handle_downstream_notification
        assert not inspect.iscoroutinefunction(handler), (
            "must be sync: _handle_stdout_line is a plain def"
        )
        assert list(inspect.signature(handler).parameters) == [
            "self",
            "name",
            "managed",
            "method",
        ]

    def test_reconcile_contract_docstring_states_the_four_guarantees(self) -> None:
        """The freeze is the docstring as much as the signature: SL-3 writes tests
        against this text on day 1."""
        import inspect

        doc = inspect.getdoc(ClientManager._handle_downstream_notification) or ""
        for fragment in (
            "notifications/tools/list_changed",
            "notifications/resources/list_changed",
            "notifications/prompts/list_changed",
        ):
            assert fragment in doc, f"method mapping missing {fragment!r}"
        lowered = doc.lower()
        assert "reconcile" in lowered and "publish" in lowered
        assert "no-op" in lowered or "no op" in lowered

    @pytest.mark.asyncio
    async def test_reconcile_contract_unrecognised_method_is_a_silent_noop(
        self,
    ) -> None:
        """An unrecognised `notifications/*` neither raises, nor publishes, nor
        schedules anything — it returns False. A raise here would tear down the
        SSE read loop through its blanket `except Exception`."""
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed

        assert (
            manager._handle_downstream_notification(
                "srv", managed, "notifications/message"
            )
            is False
        )
        assert (sink.tools, sink.resources, sink.prompts) == (0, 0, 0)
        assert not manager._background_tasks

    @pytest.mark.asyncio
    async def test_reconcile_contract_recognised_methods_are_accepted(self) -> None:
        """Each of the three `list_changed` methods is recognised and returns True."""
        for method in (
            "notifications/tools/list_changed",
            "notifications/resources/list_changed",
            "notifications/prompts/list_changed",
        ):
            manager = ClientManager()
            managed = self._managed("srv")
            manager._clients["srv"] = managed
            self._wire(manager, {})
            assert (
                manager._handle_downstream_notification("srv", managed, method) is True
            ), method
            await manager._cancel_background_tasks()

    # ---- SL-1.3: the coalesced scheduler --------------------------------

    @staticmethod
    async def _drain(manager: ClientManager, timeout: float = 5.0) -> None:
        """Wait until no reconcile is in flight."""

        async def _wait() -> None:
            while manager._reconcile_tasks:
                await asyncio.sleep(0.005)

        await asyncio.wait_for(_wait(), timeout)

    def _wire(
        self,
        manager: ClientManager,
        state: dict[str, list[str]],
        gate: asyncio.Event | None = None,
    ) -> list[str]:
        """Replace `_send_request` with a listing stub driven by `state`, and
        return the list that records every method it was asked for.

        `state` maps "tools"/"resources"/"prompts" to the identifier names the
        downstream server currently reports, so a test mutates `state` to
        simulate the downstream catalog moving underneath the gateway.
        """
        calls: list[str] = []

        async def fake_send(
            managed: ManagedClient,
            method: str,
            params: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            calls.append(method)
            if gate is not None:
                await gate.wait()
            if method == "tools/list":
                return {
                    "tools": [
                        {"name": n, "inputSchema": {}} for n in state.get("tools", [])
                    ]
                }
            if method == "resources/list":
                return {
                    "resources": [
                        {"uri": f"mem://{n}"} for n in state.get("resources", [])
                    ]
                }
            if method == "prompts/list":
                return {"prompts": [{"name": n} for n in state.get("prompts", [])]}
            return {}

        manager._send_request = fake_send  # type: ignore[method-assign]
        return calls

    @pytest.mark.asyncio
    async def test_reconcile_is_spawned_and_not_awaited_in_the_dispatch_path(
        self,
    ) -> None:
        """The handler must return *before* the reconcile's `tools/list` resolves.

        This encodes the self-deadlock hazard directly. `_index_capabilities`
        awaits `_send_request` (manager.py `:1292`) and those futures are
        resolved by the very read loop that received the notification --
        `pending.future.set_result` at `:1791` (stdio) and `:2010` (SSE). Here
        the stand-in for that loop is the test body: it releases the gate only
        after the handler has returned. An implementation that awaited the
        reconcile inline could never reach that release, and this test would
        time out rather than fail with a diff.
        """
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        gate = asyncio.Event()
        state = {"tools": ["alpha"]}
        calls = self._wire(manager, state, gate)

        assert (
            manager._handle_downstream_notification(
                "srv", managed, "notifications/tools/list_changed"
            )
            is True
        )
        # Give the spawned task room to start and block on the gate.
        for _ in range(10):
            await asyncio.sleep(0)
        assert "tools/list" in calls, "reconcile did not start"
        assert "srv::alpha" not in manager._tools, (
            "reconcile completed inline; it must not have, the gate is still shut"
        )
        # This used to assert `calls == ["tools/list"]`, which encoded the old
        # sequencing -- tools was awaited to completion before resources and
        # prompts were even started. Consiliency/pmcp#174 gathers all three
        # together so a failure on a later tools page cannot starve the other
        # two kinds, so all three requests are now in flight here. The property
        # under test is unchanged and still proven by the two assertions above:
        # the handler returned while the gate is shut, and nothing was indexed.
        assert set(calls) <= {"tools/list", "resources/list", "prompts/list"}

        gate.set()
        await self._drain(manager)
        assert "srv::alpha" in manager._tools

    @pytest.mark.asyncio
    async def test_reconcile_coalesces_to_one_in_flight_task_per_server(self) -> None:
        """A storm of notifications during an in-flight reconcile collapses to a
        single re-run, not one task per notification."""
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        gate = asyncio.Event()
        calls = self._wire(manager, {"tools": ["alpha"]}, gate)

        for _ in range(5):
            manager._handle_downstream_notification(
                "srv", managed, "notifications/tools/list_changed"
            )
            await asyncio.sleep(0)
        # Only the first reconcile has issued a listing; it is still gated.
        assert calls.count("tools/list") == 1
        assert len(manager._reconcile_tasks) == 1

        gate.set()
        await self._drain(manager)
        # The four later notifications collapsed into exactly one re-run.
        assert calls.count("tools/list") == 2

    @pytest.mark.asyncio
    async def test_reconcile_coalescing_is_per_server_not_global(self) -> None:
        """Two servers reconcile independently; one does not swallow the other."""
        manager = ClientManager()
        for name in ("one", "two"):
            managed = self._managed(name)
            manager._clients[name] = managed
        calls = self._wire(manager, {"tools": ["alpha"]})

        for name in ("one", "two"):
            manager._handle_downstream_notification(
                name, manager._clients[name], "notifications/tools/list_changed"
            )
        assert len(manager._reconcile_tasks) == 2
        await self._drain(manager)

        assert calls.count("tools/list") == 2
        assert "one::alpha" in manager._tools
        assert "two::alpha" in manager._tools

    @pytest.mark.asyncio
    async def test_reconcile_publishes_once_for_the_kind_that_changed(self) -> None:
        """A downstream that adds a tool produces exactly one `note_tools_changed`
        -- not one per index mutation -- and nothing for the untouched kinds."""
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        state: dict[str, list[str]] = {
            "tools": ["alpha"],
            "resources": ["r1"],
            "prompts": ["p1"],
        }
        self._wire(manager, state)

        # Prime the catalog as connect-time indexing would, then start counting.
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        sink.tools = sink.resources = sink.prompts = 0

        state["tools"] = ["alpha", "beta"]
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert "srv::beta" in manager._tools
        assert (sink.tools, sink.resources, sink.prompts) == (1, 0, 0)

    @pytest.mark.asyncio
    async def test_reconcile_publishes_nothing_when_identifier_sets_match(self) -> None:
        """Reconciliation over a catalog that did not move publishes nothing, even
        though `_remove_server_indexes` and `_index_*` both call `note_*`
        unconditionally."""
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire(
            manager, {"tools": ["alpha"], "resources": ["r1"], "prompts": ["p1"]}
        )

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        sink.tools = sink.resources = sink.prompts = 0

        for _ in range(3):
            manager._handle_downstream_notification(
                "srv", managed, "notifications/tools/list_changed"
            )
            await self._drain(manager)

        assert "srv::alpha" in manager._tools
        assert (sink.tools, sink.resources, sink.prompts) == (0, 0, 0)

    @pytest.mark.asyncio
    async def test_reconcile_publishes_on_rename_that_preserves_the_count(self) -> None:
        """The count-diffing trap: one tool renamed leaves the count identical, so
        only an identifier-set comparison detects it."""
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        state: dict[str, list[str]] = {"tools": ["alpha", "beta"]}
        self._wire(manager, state)

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        sink.tools = sink.resources = sink.prompts = 0

        state["tools"] = ["alpha", "gamma"]  # same count, different set
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert set(manager._tools) == {"srv::alpha", "srv::gamma"}
        assert sink.tools == 1

    @pytest.mark.asyncio
    async def test_reconcile_removes_entries_the_downstream_dropped(self) -> None:
        """`_index_*` only adds or overwrites, so reconciliation must pair a removal
        with the re-index or a dropped tool lingers forever."""
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        state: dict[str, list[str]] = {"tools": ["alpha", "beta"]}
        self._wire(manager, state)

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert set(manager._tools) == {"srv::alpha", "srv::beta"}

        state["tools"] = ["alpha"]
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert set(manager._tools) == {"srv::alpha"}

    @pytest.mark.asyncio
    async def test_reconcile_failure_leaves_the_catalog_intact(self) -> None:
        """A downstream that emits `list_changed` and then fails `tools/list` must
        not leave the catalog half-removed, and must publish nothing."""
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        state: dict[str, list[str]] = {"tools": ["alpha"], "prompts": ["p1"]}
        self._wire(manager, state)

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        before_tools = dict(manager._tools)
        before_prompts = dict(manager._prompts)
        assert before_tools and before_prompts
        sink.tools = sink.resources = sink.prompts = 0

        async def failing_send(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise ConnectionError("downstream went away mid-reconcile")

        manager._send_request = failing_send  # type: ignore[method-assign]
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert manager._tools == before_tools
        assert manager._prompts == before_prompts
        assert (sink.tools, sink.resources, sink.prompts) == (0, 0, 0)

    @pytest.mark.asyncio
    async def test_reconcile_preserves_tracked_task_records(self) -> None:
        """Reconciliation must not evict the server's in-flight `McpTaskRecord`s.

        `_remove_server_indexes` drops them, which is right for a disconnect and
        wrong for a `list_changed` — the downstream's tasks did not go anywhere.
        """
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire(manager, {"tools": ["alpha"]})
        record = McpTaskRecord(
            server_name="srv",
            tool_id="srv::alpha",
            task_id="t-1",
            status="working",
        )
        manager._tasks[("srv", "t-1")] = record

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert manager._tasks.get(("srv", "t-1")) is record

    @pytest.mark.asyncio
    async def test_reconcile_is_a_noop_when_the_server_is_gone(self) -> None:
        """A server disconnected between the notification and the reconcile must
        not be re-indexed down a dead connection."""
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        calls = self._wire(manager, {"tools": ["alpha"]})
        manager._clients.pop("srv")

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert calls == []
        assert manager._tools == {}

    @pytest.mark.asyncio
    async def test_reconcile_does_not_spin_when_the_server_answers_with_a_storm(
        self,
    ) -> None:
        """A downstream that emits `list_changed` in reply to the very
        `tools/list` reconciliation issues would drive the re-run loop forever.

        Coalescing alone does not bound this -- it bounds *concurrency*, and this
        loop is sequential. The re-run debounce is what bounds it, so this test
        asserts a hard ceiling on reconciles in a fixed window rather than a
        specific count.
        """
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        calls: list[str] = []

        async def storm_send(
            managed_arg: ManagedClient,
            method: str,
            params: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            calls.append(method)
            if method == "tools/list":
                # The server answers the listing by announcing another change.
                manager._handle_downstream_notification(
                    "srv", managed_arg, "notifications/tools/list_changed"
                )
                return {"tools": [{"name": "alpha", "inputSchema": {}}]}
            return {}

        manager._send_request = storm_send  # type: ignore[method-assign]
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await asyncio.sleep(0.6)
        spins = calls.count("tools/list")

        # Cancel the (deliberately endless) loop before asserting.
        await manager._cancel_background_tasks()

        # Without the debounce this is thousands. With it, ~1 + 0.6/0.25.
        assert spins <= 6, f"reconcile loop is spinning: {spins} passes in 0.6s"
        assert spins >= 1

    # ---- SL-fix: fetch-first atomicity, per-kind isolation, content ------

    @pytest.mark.asyncio
    async def test_reconcile_never_hides_a_live_tool_from_a_concurrent_read(
        self,
    ) -> None:
        """A `gateway.invoke` arriving mid-reconcile must still resolve a tool the
        downstream still has.

        Reconciliation is a spawned background task, so an ordinary production
        interleaving puts a lookup between its awaits. A remove-then-refetch
        order leaves the catalog *empty* for that server across three downstream
        round trips, and a concurrent invoke gets a spurious tool-not-found for a
        tool that exists. The poller below stands in for that traffic: it reads
        the catalog on every event-loop tick for the whole pass, so any await
        inside the mutation window gives it a chance to observe the hole.
        """
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        gate = asyncio.Event()
        state: dict[str, list[str]] = {
            "tools": ["alpha"],
            "resources": ["r1"],
            "prompts": ["p1"],
        }
        self._wire(manager, state, gate)

        # Prime the catalog as connect-time indexing would, ungated.
        gate.set()
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert "srv::alpha" in manager._tools
        gate.clear()

        misses = 0
        stop = asyncio.Event()

        async def poll() -> None:
            nonlocal misses
            while not stop.is_set():
                if "srv::alpha" not in manager._tools:
                    misses += 1
                await asyncio.sleep(0)

        poller = asyncio.create_task(poll())
        state["tools"] = ["alpha", "beta"]
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        # Let the reconcile reach its gated downstream request, with the poller
        # reading the catalog on every tick while it sits there.
        for _ in range(20):
            await asyncio.sleep(0)
        gate.set()
        await self._drain(manager)
        stop.set()
        await poller

        assert "srv::beta" in manager._tools, "the reconcile did not land"
        assert misses == 0, (
            f"a concurrent read saw srv::alpha missing {misses} times during "
            "reconciliation -- the catalog must never be emptied across an await"
        )

    @pytest.mark.asyncio
    async def test_reconcile_leaves_a_kind_whose_listing_failed_untouched(self) -> None:
        """`resources/list` failing while `tools/list` succeeds must not publish a
        removal of resources the server still has.

        `_index_capabilities` swallows resources/prompts listing failures on
        purpose -- a server that does not implement them is normal -- so those
        failures never reach the reconcile's abort path. Removing all three kinds
        up front therefore turns "we could not ask" into "they are gone", and
        publishes that false removal. Each kind has to be handled independently:
        a kind whose listing failed is left exactly as it was and is not
        published, while a kind that succeeded still reconciles normally.
        """
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        state: dict[str, list[str]] = {
            "tools": ["alpha"],
            "resources": ["r1"],
            "prompts": ["p1"],
        }
        self._wire(manager, state)

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        before_resources = dict(manager._resources)
        before_prompts = dict(manager._prompts)
        assert before_resources and before_prompts
        sink.tools = sink.resources = sink.prompts = 0

        healthy_send = manager._send_request

        async def resources_down(
            managed_arg: ManagedClient,
            method: str,
            params: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if method == "resources/list":
                raise ConnectionError("resources/list is down")
            return await healthy_send(managed_arg, method, params, *args, **kwargs)

        manager._send_request = resources_down  # type: ignore[method-assign]
        state["tools"] = ["alpha", "beta"]
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert manager._resources == before_resources, (
            "a kind whose listing failed was dropped from the catalog"
        )
        assert sink.resources == 0, (
            "a false resources removal was published for a listing that failed"
        )
        # The kinds that did answer still reconciled.
        assert "srv::beta" in manager._tools
        assert sink.tools == 1
        assert manager._prompts == before_prompts
        assert sink.prompts == 0

    @pytest.mark.asyncio
    async def test_reconcile_publishes_a_content_change_under_a_stable_name(
        self,
    ) -> None:
        """A tool whose description or schema changed under the same name is a real
        catalog change and must publish.

        Comparing identifier *sets* catches adds, removes, and renames and misses
        this entirely -- yet it is the common case for a server that re-lists
        after editing a tool in place. The comparison has to be over the entries
        themselves.
        """
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        entry: dict[str, Any] = {
            "name": "alpha",
            "description": "first",
            "inputSchema": {"type": "object", "properties": {}},
        }

        async def one_tool(
            managed_arg: ManagedClient,
            method: str,
            params: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if method == "tools/list":
                return {"tools": [dict(entry)]}
            return {}

        manager._send_request = one_tool  # type: ignore[method-assign]
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert set(manager._tools) == {"srv::alpha"}
        sink.tools = sink.resources = sink.prompts = 0

        # Same name, new description: the identifier set is unchanged.
        entry["description"] = "second"
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert manager._tools["srv::alpha"].description == "second"
        assert sink.tools == 1, (
            "a changed description under an unchanged tool name published nothing"
        )

        # Same name, same description, new input schema.
        entry["inputSchema"] = {
            "type": "object",
            "properties": {"q": {"type": "string"}},
        }
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert manager._tools["srv::alpha"].input_schema == entry["inputSchema"]
        assert sink.tools == 2, (
            "a changed input schema under an unchanged tool name published nothing"
        )

        # A re-list with nothing changed still publishes nothing.
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert sink.tools == 2

    # ---- SL-1.6: both dispatch paths, and typed errors ------------------

    @pytest.mark.asyncio
    async def test_stdio_dispatch_path_reaches_the_reconcile_scheduler(self) -> None:
        """`_handle_stdout_line` must recognise a notification. It has no `id`, so
        it falls through the pending-request gate with nothing to resolve."""
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire(manager, {"tools": ["alpha"]})

        line = json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
        ).encode()
        manager._handle_stdout_line("srv", managed, line, time.time())
        await self._drain(manager)

        assert "srv::alpha" in manager._tools

    @pytest.mark.asyncio
    async def test_sse_dispatch_path_reaches_the_reconcile_scheduler(self) -> None:
        """The `_read_sse` loop must recognise a notification, and must survive it:
        a raise inside that loop is caught by its blanket `except Exception`, which
        marks the server ERROR and reconnects."""
        manager = ClientManager()
        managed = self._managed("srv")
        managed.is_remote = True
        manager._clients["srv"] = managed
        self._wire(manager, {"tools": ["alpha"]})
        manager._schedule_reconnect = MagicMock()  # type: ignore[method-assign]

        def _frame(payload: dict[str, Any]) -> Any:
            frame = MagicMock()
            frame.message.model_dump.return_value = payload
            return frame

        async def stream() -> Any:
            yield _frame(
                {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
            )
            yield _frame({"jsonrpc": "2.0", "method": "notifications/message"})

        await manager._read_sse("srv", managed, stream())
        await self._drain(manager)

        assert "srv::alpha" in manager._tools

    @pytest.mark.asyncio
    async def test_stdio_dispatch_preserves_downstream_error_code_and_data(
        self,
    ) -> None:
        """A JSON-RPC error keeps `code` and `data` alongside `message`, and `str()`
        stays exactly the message so `gateway.invoke`'s E302 mapping is unchanged."""
        from pmcp.client.manager import DownstreamError

        manager = ClientManager()
        managed = self._managed("srv")
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        now = time.time()
        managed.pending_requests[7] = PendingRequest(
            request_id=7,
            server_name="srv",
            tool_id="srv::alpha",
            started_at=now,
            last_heartbeat=now,
            timeout_ms=30000,
            future=fut,
        )
        line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "error": {
                    "code": -32602,
                    "message": "Invalid params",
                    "data": {"field": "path"},
                },
            }
        ).encode()

        manager._handle_stdout_line("srv", managed, line, time.time())

        err = fut.exception()
        assert isinstance(err, DownstreamError)
        assert err.code == -32602
        assert err.data == {"field": "path"}
        assert str(err) == "Invalid params"

    @pytest.mark.asyncio
    async def test_sse_dispatch_preserves_downstream_error_code_and_data(self) -> None:
        """The same, through the remote dispatch path."""
        from pmcp.client.manager import DownstreamError

        manager = ClientManager()
        managed = self._managed("srv")
        managed.is_remote = True
        manager._clients["srv"] = managed
        manager._schedule_reconnect = MagicMock()  # type: ignore[method-assign]
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        now = time.time()
        managed.pending_requests[9] = PendingRequest(
            request_id=9,
            server_name="srv",
            tool_id="srv::alpha",
            started_at=now,
            last_heartbeat=now,
            timeout_ms=30000,
            future=fut,
        )

        async def stream() -> Any:
            frame = MagicMock()
            frame.message.model_dump.return_value = {
                "jsonrpc": "2.0",
                "id": 9,
                "error": {"code": -32000, "message": "boom", "data": ["a", "b"]},
            }
            yield frame

        await manager._read_sse("srv", managed, stream())

        err = fut.exception()
        assert isinstance(err, DownstreamError)
        assert err.code == -32000
        assert err.data == ["a", "b"]
        assert str(err) == "boom"


class TestDownstreamNotificationBehaviouralGuarantees:
    """FANOUT SL-3.2: storm suppression, the resources/prompts kinds, the
    unrecognised-method no-op, and typed downstream errors on both dispatch
    paths.

    Test names here are chosen to match the `-k` patterns the roadmap's
    acceptance criteria pin (EC-FANOUT-4, EC-FANOUT-5, EC-FANOUT-7).
    `TestDownstreamReconcileScheduler` above (SL-1's own tests) deliberately
    avoids those substrings so this lane could claim them without renaming
    anything -- see that class's docstring. This class duplicates the small
    `_managed`/`_wire`/`_drain` helpers rather than reaching into that
    class's internals, so neither lane's tests depend on the other's shape.
    """

    @staticmethod
    def _managed(name: str) -> ManagedClient:
        status = ServerStatus(name=name, status=ServerStatusEnum.ONLINE, tool_count=0)
        managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
        managed.config.name = name
        return managed

    @staticmethod
    async def _drain(manager: ClientManager, timeout: float = 5.0) -> None:
        """Wait until no reconcile is in flight."""

        async def _wait() -> None:
            while manager._reconcile_tasks:
                await asyncio.sleep(0.005)

        await asyncio.wait_for(_wait(), timeout)

    def _wire(self, manager: ClientManager, state: dict[str, list[str]]) -> list[str]:
        """Same shape as `TestDownstreamReconcileScheduler._wire`, without the
        gating hook that class's deadlock test needs -- none of this class's
        tests need to pause a reconcile mid-flight."""
        calls: list[str] = []

        async def fake_send(
            managed: ManagedClient,
            method: str,
            params: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            calls.append(method)
            if method == "tools/list":
                return {
                    "tools": [
                        {"name": n, "inputSchema": {}} for n in state.get("tools", [])
                    ]
                }
            if method == "resources/list":
                return {
                    "resources": [
                        {"uri": f"mem://{n}"} for n in state.get("resources", [])
                    ]
                }
            if method == "prompts/list":
                return {"prompts": [{"name": n} for n in state.get("prompts", [])]}
            return {}

        manager._send_request = fake_send  # type: ignore[method-assign]
        return calls

    # ---- EC-FANOUT-4: storm suppression -----------------------------------

    @pytest.mark.asyncio
    async def test_unchanged_catalog_publishes_nothing_across_a_notification_storm(
        self,
    ) -> None:
        """A downstream that emits `list_changed` repeatedly with nothing
        actually different in its catalog must publish exactly zero events,
        even though `_remove_server_indexes`/`_index_*` call `note_*`
        unconditionally on every pass -- the suppress-while-churning rule is
        what stands between this and a spam storm reaching subscribers."""
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire(
            manager, {"tools": ["alpha"], "resources": ["r1"], "prompts": ["p1"]}
        )

        # Prime the catalog, as connect-time indexing would.
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        sink.tools = sink.resources = sink.prompts = 0

        # A burst of ten notifications, the catalog never actually moving.
        for _ in range(10):
            manager._handle_downstream_notification(
                "srv", managed, "notifications/tools/list_changed"
            )
        await self._drain(manager)

        assert (sink.tools, sink.resources, sink.prompts) == (0, 0, 0)
        assert set(manager._tools) == {"srv::alpha"}

    # ---- EC-FANOUT-5: resources/prompts kinds and the unknown no-op -------

    @pytest.mark.asyncio
    async def test_resources_list_changed_reconciles_and_publishes_only_resources(
        self,
    ) -> None:
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        state: dict[str, list[str]] = {
            "tools": ["alpha"],
            "resources": ["r1"],
            "prompts": ["p1"],
        }
        self._wire(manager, state)

        manager._handle_downstream_notification(
            "srv", managed, "notifications/resources/list_changed"
        )
        await self._drain(manager)
        sink.tools = sink.resources = sink.prompts = 0

        state["resources"] = ["r1", "r2"]
        assert (
            manager._handle_downstream_notification(
                "srv", managed, "notifications/resources/list_changed"
            )
            is True
        )
        await self._drain(manager)

        assert "srv::mem://r2" in manager._resources
        assert (sink.tools, sink.resources, sink.prompts) == (0, 1, 0)

    @pytest.mark.asyncio
    async def test_prompts_list_changed_reconciles_and_publishes_only_prompts(
        self,
    ) -> None:
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        state: dict[str, list[str]] = {
            "tools": ["alpha"],
            "resources": ["r1"],
            "prompts": ["p1"],
        }
        self._wire(manager, state)

        manager._handle_downstream_notification(
            "srv", managed, "notifications/prompts/list_changed"
        )
        await self._drain(manager)
        sink.tools = sink.resources = sink.prompts = 0

        state["prompts"] = ["p1", "p2"]
        assert (
            manager._handle_downstream_notification(
                "srv", managed, "notifications/prompts/list_changed"
            )
            is True
        )
        await self._drain(manager)

        assert "srv::p2" in manager._prompts
        assert (sink.tools, sink.resources, sink.prompts) == (0, 0, 1)

    @pytest.mark.asyncio
    async def test_unknown_notification_is_a_noop_on_stdio_dispatch_and_does_not_kill_the_read_loop(
        self,
    ) -> None:
        """An unrecognised `notifications/*` on the stdio path must not raise,
        must not schedule a reconcile, and must not stop the next line -- a
        real request/response pair -- from being processed normally right
        after it."""
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed

        unknown_line = json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/message"}
        ).encode()
        manager._handle_stdout_line("srv", managed, unknown_line, time.time())

        assert not manager._reconcile_tasks
        assert (sink.tools, sink.resources, sink.prompts) == (0, 0, 0)

        # The read loop must still be alive to process a normal response.
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        now = time.time()
        managed.pending_requests[42] = PendingRequest(
            request_id=42,
            server_name="srv",
            tool_id="srv::alpha",
            started_at=now,
            last_heartbeat=now,
            timeout_ms=30000,
            future=fut,
        )
        response_line = json.dumps(
            {"jsonrpc": "2.0", "id": 42, "result": {"ok": True}}
        ).encode()
        manager._handle_stdout_line("srv", managed, response_line, time.time())

        assert fut.done()
        assert fut.result() == {"ok": True}

    @pytest.mark.asyncio
    async def test_unknown_notification_is_a_noop_on_sse_dispatch_and_does_not_kill_the_read_loop(
        self,
    ) -> None:
        """The same, through `_read_sse`: that loop's blanket `except
        Exception` would tear the connection down and force a reconnect if
        the handler ever raised on an unrecognised method. This proves it
        doesn't, by letting a real response frame flow right after it, in the
        same stream, and resolve normally."""
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        managed.is_remote = True
        manager._clients["srv"] = managed
        manager._schedule_reconnect = MagicMock()  # type: ignore[method-assign]

        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        now = time.time()
        managed.pending_requests[43] = PendingRequest(
            request_id=43,
            server_name="srv",
            tool_id="srv::alpha",
            started_at=now,
            last_heartbeat=now,
            timeout_ms=30000,
            future=fut,
        )

        def _frame(payload: dict[str, Any]) -> Any:
            frame = MagicMock()
            frame.message.model_dump.return_value = payload
            return frame

        async def stream() -> Any:
            yield _frame({"jsonrpc": "2.0", "method": "notifications/message"})
            yield _frame({"jsonrpc": "2.0", "id": 43, "result": {"ok": True}})

        await manager._read_sse("srv", managed, stream())

        assert not manager._reconcile_tasks
        assert (sink.tools, sink.resources, sink.prompts) == (0, 0, 0)
        assert fut.done()
        assert fut.result() == {"ok": True}

    # ---- EC-FANOUT-7: typed downstream errors, both dispatch paths --------

    @pytest.mark.asyncio
    async def test_typed_downstream_error_preserves_code_and_data_on_stdio_dispatch(
        self,
    ) -> None:
        """`code`/`data` survive alongside `message`, and `str()` stays
        exactly the downstream message -- `gateway.invoke` maps every
        exception to E302 through `str(e)`, so nothing about that mapping
        may change here."""
        from pmcp.client.manager import DownstreamError

        manager = ClientManager()
        managed = self._managed("srv")
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        now = time.time()
        managed.pending_requests[11] = PendingRequest(
            request_id=11,
            server_name="srv",
            tool_id="srv::alpha",
            started_at=now,
            last_heartbeat=now,
            timeout_ms=30000,
            future=fut,
        )
        line = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "error": {
                    "code": -32601,
                    "message": "Method not found",
                    "data": {"method": "tools/frobnicate"},
                },
            }
        ).encode()

        manager._handle_stdout_line("srv", managed, line, time.time())

        err = fut.exception()
        assert isinstance(err, DownstreamError)
        assert err.code == -32601
        assert err.data == {"method": "tools/frobnicate"}
        assert str(err) == "Method not found"

    @pytest.mark.asyncio
    async def test_typed_downstream_error_preserves_code_and_data_on_sse_dispatch(
        self,
    ) -> None:
        """The same guarantee, through the remote dispatch path."""
        from pmcp.client.manager import DownstreamError

        manager = ClientManager()
        managed = self._managed("srv")
        managed.is_remote = True
        manager._clients["srv"] = managed
        manager._schedule_reconnect = MagicMock()  # type: ignore[method-assign]
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        now = time.time()
        managed.pending_requests[12] = PendingRequest(
            request_id=12,
            server_name="srv",
            tool_id="srv::alpha",
            started_at=now,
            last_heartbeat=now,
            timeout_ms=30000,
            future=fut,
        )

        def _frame(payload: dict[str, Any]) -> Any:
            frame = MagicMock()
            frame.message.model_dump.return_value = payload
            return frame

        async def stream() -> Any:
            yield _frame(
                {
                    "jsonrpc": "2.0",
                    "id": 12,
                    "error": {
                        "code": -32000,
                        "message": "downstream unavailable",
                        "data": {"retry_after_ms": 500},
                    },
                }
            )

        await manager._read_sse("srv", managed, stream())

        err = fut.exception()
        assert isinstance(err, DownstreamError)
        assert err.code == -32000
        assert err.data == {"retry_after_ms": 500}
        assert str(err) == "downstream unavailable"


class TestReconcileMalformedEntryResilience:
    """FANOUT repair D5/D6: a malformed downstream entry must cost only itself,
    and the suppression counter must always unwind.

    Before FANOUT a downstream could not trigger a re-index mid-session, so a
    malformed entry could at worst fail a connect. Now `list_changed` re-enters
    the indexers at any time, and the apply block removes before it re-indexes
    -- so an entry that raises mid-apply takes the server's *previous* catalog
    with it, permanently, with a healthy read loop and no reconnect to heal it.
    """

    @staticmethod
    def _managed(name: str) -> ManagedClient:
        status = ServerStatus(name=name, status=ServerStatusEnum.ONLINE, tool_count=0)
        managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
        managed.config.name = name
        return managed

    @staticmethod
    async def _drain(manager: ClientManager, timeout: float = 5.0) -> None:
        async def _wait() -> None:
            while manager._reconcile_tasks:
                await asyncio.sleep(0.005)

        await asyncio.wait_for(_wait(), timeout)

    @staticmethod
    def _wire_listings(
        manager: ClientManager, listings: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Stub `_send_request` with raw per-kind payloads, malformed entries
        included -- `TestDownstreamReconcileScheduler._wire` only ever emits
        well-formed ones, which is precisely what this class must not do."""

        async def fake_send(
            managed: ManagedClient,
            method: str,
            params: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            kind = method.split("/")[0]
            return {kind: list(listings.get(kind, []))}

        manager._send_request = fake_send  # type: ignore[method-assign]

    # ---- D5: per-entry resilience ---------------------------------------

    @pytest.mark.asyncio
    async def test_malformed_tool_is_skipped_without_losing_the_prior_catalog(
        self,
    ) -> None:
        """One unparseable tool must not cost the other tools, and must never
        cost the tools that were already indexed.

        The malformed entry is deliberately *first*: with it last, the entries
        ahead of it would already have been re-added before the raise and the
        wipe would be invisible. First, it raises with the removal done and
        nothing re-added -- the lead's reproduction, `after: []`.
        """
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire_listings(manager, {"tools": [{"name": "alpha", "inputSchema": {}}]})

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert set(manager._tools) == {"srv::alpha"}

        # `inputSchema` as a string reaches `_schema_dialect`, which calls
        # `.get` on it -- an AttributeError raised mid-apply.
        self._wire_listings(
            manager,
            {
                "tools": [
                    {"name": "bad", "inputSchema": "not-a-dict"},
                    {"name": "alpha", "inputSchema": {}},
                    {"name": "beta", "inputSchema": {}},
                ]
            },
        )
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert set(manager._tools) == {"srv::alpha", "srv::beta"}, (
            "one malformed entry wiped the server's catalog"
        )

    @pytest.mark.asyncio
    async def test_malformed_resource_and_prompt_entries_are_skipped(self) -> None:
        """The same guarantee for the other two kinds, whose indexers construct
        pydantic models just as directly."""
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire_listings(
            manager,
            {
                "resources": [
                    {"uri": ["not", "a", "string"]},
                    {"uri": "mem://good"},
                ],
                "prompts": [
                    {"name": "bad", "arguments": ["not-a-mapping"]},
                    {"name": "good"},
                ],
            },
        )

        manager._handle_downstream_notification(
            "srv", managed, "notifications/resources/list_changed"
        )
        await self._drain(manager)

        assert set(manager._resources) == {"srv::mem://good"}
        assert set(manager._prompts) == {"srv::good"}

    def test_index_tools_guard_is_per_entry_not_per_call(self) -> None:
        """Called directly, not through reconciliation: a single `try` wrapped
        around the whole loop would still lose every entry *after* the bad one,
        so pin that entries on both sides of it survive."""
        manager = ClientManager()

        indexed = manager._index_tools(
            "srv",
            [
                {"name": "before", "inputSchema": {}},
                {"name": "bad", "inputSchema": "not-a-dict"},
                {"name": "after", "inputSchema": {}},
            ],
        )

        assert indexed == 2
        assert set(manager._tools) == {"srv::before", "srv::after"}

    def test_index_of_only_malformed_entries_publishes_nothing(self) -> None:
        """Nothing was indexed, so there is nothing to announce -- a note here
        would wake every subscriber for a catalog that did not move."""
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))

        assert manager._index_tools("srv", [{"name": "b", "inputSchema": "x"}]) == 0
        assert manager._index_resources("srv", [{"uri": ["nope"]}]) == 0
        assert (
            manager._index_prompts("srv", [{"name": "b", "arguments": ["nope"]}]) == 0
        )
        assert (sink.tools, sink.resources, sink.prompts) == (0, 0, 0)
        assert manager._tools == {} and manager._resources == {}
        assert manager._prompts == {}

    # ---- F1: a listing nobody could parse is a failed listing -----------

    @pytest.mark.asyncio
    async def test_all_malformed_relist_preserves_the_prior_catalog(self) -> None:
        """Offered entries, none of them parseable, over a *populated* catalog.

        The per-entry guard handles one bad entry among good ones; this is the
        corner where every entry is bad. Counting that as an answer would remove
        the prior entries, index nothing and publish the removal -- a false
        "those tools are gone" built out of "we could not read the answer".
        `test_index_of_only_malformed_entries_publishes_nothing` cannot catch it:
        it indexes into an empty catalog, so it has nothing to lose.
        """
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire_listings(manager, {"tools": [{"name": "alpha", "inputSchema": {}}]})

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert set(manager._tools) == {"srv::alpha"}
        published_while_priming = sink.tools

        self._wire_listings(
            manager,
            {"tools": [{"name": "bad", "inputSchema": "not-a-dict"}]},
        )
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert set(manager._tools) == {"srv::alpha"}, (
            "a listing whose every entry was unparseable wiped the prior catalog"
        )
        assert sink.tools == published_while_priming, (
            "published a removal for entries we merely failed to read"
        )

    @pytest.mark.asyncio
    async def test_all_malformed_relist_leaves_the_other_kinds_alone(self) -> None:
        """Per kind, still. Tools unreadable must not hold back an honest
        resources answer arriving in the same reconcile."""
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire_listings(
            manager,
            {
                "tools": [{"name": "alpha", "inputSchema": {}}],
                "resources": [{"uri": "mem://old"}],
            },
        )
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert set(manager._tools) == {"srv::alpha"}

        self._wire_listings(
            manager,
            {
                "tools": [{"name": "bad", "inputSchema": "not-a-dict"}],
                "resources": [{"uri": "mem://new"}],
            },
        )
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert set(manager._tools) == {"srv::alpha"}
        assert set(manager._resources) == {"srv::mem://new"}

    @pytest.mark.asyncio
    async def test_explicitly_empty_relist_still_clears_and_publishes(self) -> None:
        """The boundary of the rule above: *no* entries offered is an answer --
        the server emptied the kind -- and must clear and publish. Preserving
        here would strand entries the downstream has explicitly disowned."""
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire_listings(manager, {"tools": [{"name": "alpha", "inputSchema": {}}]})

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert set(manager._tools) == {"srv::alpha"}
        published_while_priming = sink.tools

        self._wire_listings(manager, {"tools": []})
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert manager._tools == {}
        assert sink.tools == published_while_priming + 1

    def test_partly_malformed_relist_still_replaces_the_kind(self) -> None:
        """Mixed listings keep the per-entry semantics: some entries parsed, so
        the kind answered and is replaced wholesale by what parsed."""
        manager = ClientManager()
        manager._index_tools("srv", [{"name": "gone", "inputSchema": {}}])

        assert (
            manager._index_tools(
                "srv",
                [
                    {"name": "bad", "inputSchema": "not-a-dict"},
                    {"name": "kept", "inputSchema": {}},
                ],
            )
            == 1
        )
        assert "srv::kept" in manager._tools

    # ---- D6: the suppression counter's `finally` ------------------------

    @pytest.mark.asyncio
    async def test_suppression_counter_unwinds_on_the_success_path(self) -> None:
        """A leaked entry would leave that server's sink suppressed forever:
        subscribed clients silently stop seeing its catalog changes, with no
        error anywhere to point at."""
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire_listings(manager, {"tools": [{"name": "alpha", "inputSchema": {}}]})

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert manager._tools, "reconcile did not run; the assertion below is vacuous"
        assert manager._catalog_suppressed == {}
        assert not manager._catalog_publishing_suppressed("srv")

    @pytest.mark.asyncio
    async def test_suppression_counter_unwinds_when_the_apply_block_raises(
        self,
    ) -> None:
        """The failure must be injected *inside* the apply block. A failed fetch
        returns before the counter is ever incremented, so it would leave this
        test green even with the decrement deleted.
        """
        manager = ClientManager()
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire_listings(manager, {"tools": [{"name": "alpha", "inputSchema": {}}]})

        def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("apply blew up after the counter went up")

        manager._remove_server_indexes = boom  # type: ignore[method-assign]

        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert manager._catalog_suppressed == {}
        assert not manager._catalog_publishing_suppressed("srv")


class TestListingPagination:
    """Consiliency/pmcp#173: `nextCursor` was never followed.

    The listing path sent `{}` once and kept page one, so a downstream with
    more entries than its page size had the rest silently missing -- and, once
    reconciliation began publishing, announced as removed. The truncation
    predated fan-out; asserting freshness over a partial view is what made it
    worth fixing.

    The rule these pin: a failure on ANY page makes the WHOLE kind unreadable,
    never partial. Merging the pages that did arrive is the same false-removal
    shape corrected four times over in this module -- entries the server still
    has, dropped and the drop published.
    """

    _managed = staticmethod(TestReconcileMalformedEntryResilience._managed)
    _drain = staticmethod(TestReconcileMalformedEntryResilience._drain)

    @staticmethod
    def _wire_pages(manager: ClientManager, pages: list[Any]) -> None:
        """Serve `tools/list` from `pages`, indexed by cursor.

        Cursor `pN` selects `pages[N]`; the first request carries none and gets
        `pages[0]`. An element that is an exception is raised instead of
        returned, which is how a mid-pagination transport failure is expressed.
        """

        async def fake_send(
            managed: ManagedClient,
            method: str,
            params: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if method != "tools/list":
                return {method.split("/")[0]: []}
            cursor = params.get("cursor")
            index = 0 if cursor is None else int(str(cursor)[1:])
            page = pages[index]
            if isinstance(page, BaseException):
                raise page
            return cast(dict[str, Any], page)

        manager._send_request = fake_send  # type: ignore[method-assign]

    async def _prime_then(
        self, pages: list[Any]
    ) -> tuple[ClientManager, _RecordingSink, int]:
        """Index one seed tool, then re-list `tools` from `pages`.

        Priming over a populated catalog is what makes a false removal visible;
        against an empty one there is nothing to lose.
        """
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        note = "notifications/tools/list_changed"

        self._wire_pages(manager, [{"tools": [{"name": "seed", "inputSchema": {}}]}])
        manager._handle_downstream_notification("srv", managed, note)
        await self._drain(manager)
        primed = sink.tools

        self._wire_pages(manager, pages)
        manager._handle_downstream_notification("srv", managed, note)
        await self._drain(manager)
        return manager, sink, primed

    @staticmethod
    def _tool(name: str) -> dict[str, Any]:
        return {"name": name, "inputSchema": {}}

    @pytest.mark.asyncio
    async def test_multi_page_listing_is_assembled_completely(self) -> None:
        """RED before the fix: page one was treated as the whole catalog."""
        manager, sink, primed = await self._prime_then(
            [
                {"tools": [self._tool("a"), self._tool("b")], "nextCursor": "p1"},
                {"tools": [self._tool("c")]},
            ]
        )
        assert sorted(manager._tools) == ["srv::a", "srv::b", "srv::c"], (
            "entries past page one were dropped; the cursor was not followed"
        )
        assert sink.tools == primed + 1

    @pytest.mark.asyncio
    async def test_snake_case_next_cursor_is_honoured(self) -> None:
        """`list_tasks` accepts both spellings; the listing path must too."""
        manager, _, _ = await self._prime_then(
            [
                {"tools": [self._tool("a")], "next_cursor": "p1"},
                {"tools": [self._tool("b")]},
            ]
        )
        assert sorted(manager._tools) == ["srv::a", "srv::b"]

    @pytest.mark.asyncio
    async def test_a_failed_later_page_preserves_the_prior_catalog(self) -> None:
        """A transport failure on page two must not publish a partial listing."""
        manager, sink, primed = await self._prime_then(
            [
                {"tools": [self._tool("a")], "nextCursor": "p1"},
                RuntimeError("page two never arrived"),
            ]
        )
        assert sorted(manager._tools) == ["srv::seed"], (
            "a partially-read listing replaced the catalog"
        )
        assert sink.tools == primed, "a partial listing was published as a change"

    @pytest.mark.asyncio
    async def test_an_unreadable_later_page_preserves_the_prior_catalog(self) -> None:
        """Same rule when page two arrives but is malformed rather than absent."""
        manager, sink, primed = await self._prime_then(
            [{"tools": [self._tool("a")], "nextCursor": "p1"}, {}]
        )
        assert sorted(manager._tools) == ["srv::seed"]
        assert sink.tools == primed

    @pytest.mark.asyncio
    async def test_a_repeated_cursor_terminates_and_is_unreadable(self) -> None:
        """A server handing back its own cursor must not spin the fetch forever."""
        manager, sink, primed = await self._prime_then(
            [{"tools": [self._tool("a")], "nextCursor": "p0"}]
        )
        assert sorted(manager._tools) == ["srv::seed"]
        assert sink.tools == primed

    @pytest.mark.asyncio
    async def test_a_deep_but_honest_catalog_assembles_completely(self) -> None:
        """A catalog many pages deep, still inside the cap, must assemble.

        Board review found the gap this closes: every other test here is two
        pages deep, so a mutant shrinking `_MAX_LISTING_PAGES` to 2 passed the
        entire suite. Nothing pinned that the cap is a runaway guard rather
        than a catalog-size limit.

        The failure direction is a freeze, not a false removal -- an honest
        server would be reported as having no tools at all, which is the same
        in-class defect that made the original cap of 50 wrong for a legal
        page size of 1.
        """
        depth = 10
        pages: list[Any] = [
            {
                "tools": [self._tool(f"t{i}")],
                **({"nextCursor": f"p{i + 1}"} if i < depth - 1 else {}),
            }
            for i in range(depth)
        ]
        manager, sink, primed = await self._prime_then(pages)
        assert sorted(manager._tools) == sorted(f"srv::t{i}" for i in range(depth)), (
            "a deep but honest catalog was not assembled; the page cap is "
            "acting as a catalog-size limit rather than a runaway guard"
        )
        assert sink.tools == primed + 1

    @pytest.mark.asyncio
    async def test_exceeding_the_page_cap_is_unreadable_not_truncated(self) -> None:
        """The cap must preserve, not index the pages it managed to read.

        Indexing a truncated view is precisely what Consiliency/pmcp#173 is
        about, so the bound cannot resolve to "keep what we got".
        """
        pages: list[Any] = [
            {"tools": [self._tool(f"t{i}")], "nextCursor": f"p{i + 1}"}
            for i in range(_MAX_LISTING_PAGES + 2)
        ]
        manager, sink, primed = await self._prime_then(pages)
        assert sorted(manager._tools) == ["srv::seed"]
        assert sink.tools == primed

    @pytest.mark.parametrize("cursor", [0, "", False])
    @pytest.mark.asyncio
    async def test_a_falsey_cursor_is_unusable_not_the_end(self, cursor: Any) -> None:
        """A falsey cursor must not read as "no more pages".

        Board review of this PR: detecting absence with `x or y` / `if not
        cursor` meant `nextCursor: 0` and `nextCursor: ""` were taken as the end
        of the listing, so page one was published as the whole catalog -- the
        exact truncation Consiliency/pmcp#173 is about -- and they also slipped
        past the non-string rejection. Presence is now decided by KEY.
        """
        manager, sink, primed = await self._prime_then(
            [
                {"tools": [self._tool("a")], "nextCursor": cursor},
                {"tools": [self._tool("b")]},
            ]
        )
        assert sorted(manager._tools) == ["srv::seed"], (
            "a falsey cursor was treated as the end of the listing"
        )
        assert sink.tools == primed

    @pytest.mark.asyncio
    async def test_an_explicit_null_cursor_ends_the_listing(self) -> None:
        """`nextCursor: null` is the protocol saying "no more" -- not a defect.

        The pin that keeps the falsey-cursor fix from over-reaching into
        treating a well-formed final page as unreadable.
        """
        manager, sink, primed = await self._prime_then(
            [{"tools": [self._tool("a")], "nextCursor": None}]
        )
        assert sorted(manager._tools) == ["srv::a"]
        assert sink.tools == primed + 1

    @pytest.mark.asyncio
    async def test_a_later_tools_page_failure_does_not_starve_other_kinds(
        self,
    ) -> None:
        """A tools page-two failure must not cost resources and prompts.

        Board review: tools was awaited to completion before the other two were
        started, so a later-page exception escaped and neither of them was ever
        requested -- a healthy resources change sat unapplied because an
        unrelated kind paginated badly. Only a page-ONE tools failure is a
        connect-time error.
        """
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        called: list[str] = []

        async def fake_send(
            _managed: ManagedClient,
            method: str,
            params: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            kind = method.split("/")[0]
            called.append(kind)
            if kind == "tools":
                if params.get("cursor") is None:
                    return {"tools": [self._tool("a")], "nextCursor": "p1"}
                raise RuntimeError("tools page two never arrived")
            if kind == "resources":
                return {"resources": [{"uri": "mem://r1", "name": "r1"}]}
            return {"prompts": []}

        manager._send_request = fake_send  # type: ignore[method-assign]
        listings = await manager._fetch_server_listings(managed)

        assert listings["tools"] is None, "a partial tools listing was kept"
        assert listings["resources"] == [{"uri": "mem://r1", "name": "r1"}], (
            "resources were never fetched because tools paginated badly"
        )
        assert {"tools", "resources", "prompts"} <= set(called)

    @pytest.mark.asyncio
    async def test_a_page_one_tools_failure_still_raises(self) -> None:
        """The connect-time contract survives gathering all three kinds."""
        manager = ClientManager(catalog_events=cast(Any, _RecordingSink()))
        managed = self._managed("srv")

        async def fake_send(
            _managed: ManagedClient,
            method: str,
            params: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if method == "tools/list":
                raise RuntimeError("this server cannot list tools")
            return {method.split("/")[0]: []}

        manager._send_request = fake_send  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="cannot list tools"):
            await manager._fetch_server_listings(managed)

    @pytest.mark.asyncio
    async def test_a_single_page_listing_still_clears_when_explicitly_empty(
        self,
    ) -> None:
        """The pagination loop must not weaken the empty-is-an-answer rule."""
        manager, sink, primed = await self._prime_then([{"tools": []}])
        assert manager._tools == {}
        assert sink.tools == primed + 1


class TestUnreadableListingIsNotAnEmptyOne:
    """FANOUT repair G1/G2: the two remaining routes by which "we could not read
    this" became "it is gone".

    `TestReconcileMalformedEntryResilience` covers the listing that *arrived*
    and could not be parsed. These cover the two steps on either side of it: a
    reply whose required collection never arrived readably at all (G1), and an
    entry that arrived carrying no identity (G2). Both used to be laundered into
    a valid-looking answer by a defaulting `.get` -- `result.get(kind, [])` and
    `entry.get(identity, "")` -- and published as a removal.
    """

    _managed = staticmethod(TestReconcileMalformedEntryResilience._managed)
    _drain = staticmethod(TestReconcileMalformedEntryResilience._drain)

    @staticmethod
    def _wire_raw(manager: ClientManager, replies: dict[str, Any]) -> None:
        """Stub `_send_request` with whole `result` objects, not per-kind entry
        lists.

        `TestReconcileMalformedEntryResilience._wire_listings` always wraps what
        it is given as `{kind: list(entries)}`, so it can express a malformed
        *entry* but never a malformed *reply* -- the absent collection and the
        non-list collection under test here are both unreachable through it.
        """

        async def fake_send(
            managed: ManagedClient,
            method: str,
            params: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            kind = method.split("/")[0]
            return cast(dict[str, Any], replies.get(kind, {kind: []}))

        manager._send_request = fake_send  # type: ignore[method-assign]

    async def _prime_and_relist(
        self, kind: str, primed: list[dict[str, Any]], relisted: Any
    ) -> tuple[ClientManager, _RecordingSink, int]:
        """Index a real catalog for `kind`, then re-list it with `relisted`.

        `primed` is a well-formed entry list and is wrapped for you; `relisted`
        is the whole `result` object, unwrapped, because the malformed replies
        under test are exactly the ones that cannot be expressed as an entry
        list.

        Priming over a *populated* catalog is the whole point: every one of
        these defects is invisible against an empty one, because there is
        nothing there to falsely remove.
        """
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        notification = f"notifications/{kind}/list_changed"

        self._wire_raw(manager, {kind: {kind: primed}})
        manager._handle_downstream_notification("srv", managed, notification)
        await self._drain(manager)
        published_while_priming = cast(int, getattr(sink, kind))

        self._wire_raw(manager, {kind: relisted})
        manager._handle_downstream_notification("srv", managed, notification)
        await self._drain(manager)
        return manager, sink, published_while_priming

    # ---- G1: an absent collection is not an explicit empty one ----------

    @pytest.mark.asyncio
    async def test_absent_tools_collection_preserves_and_publishes_nothing(
        self,
    ) -> None:
        """`result: {}` -- the reply is missing the collection the protocol
        requires. `result.get("tools", [])` turned that into `[]`, which the
        classifier read as "the server answered and it is empty", so the prior
        tools were cleared and a removal published. Two different meanings,
        one outcome.
        """
        manager, sink, primed_publishes = await self._prime_and_relist(
            "tools", [{"name": "alpha", "inputSchema": {}}], {}
        )

        assert set(manager._tools) == {"srv::alpha"}, (
            "a reply missing its required `tools` field was read as an empty "
            "catalog and wiped the prior tools"
        )
        assert sink.tools == primed_publishes, (
            "published a removal for a reply we could not read"
        )

    @pytest.mark.asyncio
    async def test_explicitly_empty_tools_collection_still_clears_and_publishes(
        self,
    ) -> None:
        """The other side of the same line, restated here rather than left to
        `test_explicitly_empty_relist_still_clears_and_publishes`: this pins the
        *distinction*, so a fix that preserved both would fail here even though
        the G1 test above would pass.
        """
        manager, sink, primed_publishes = await self._prime_and_relist(
            "tools", [{"name": "alpha", "inputSchema": {}}], {"tools": []}
        )

        assert manager._tools == {}
        assert sink.tools == primed_publishes + 1

    @pytest.mark.asyncio
    async def test_absent_collection_preserves_resources_and_prompts_too(
        self,
    ) -> None:
        """The same `.get(kind, [])` served all three kinds; resources and
        prompts reached it by a different line and must be fixed by the same
        rule."""
        resources, _, _ = await self._prime_and_relist(
            "resources", [{"uri": "mem://r1"}], {}
        )
        prompts, _, _ = await self._prime_and_relist("prompts", [{"name": "p1"}], {})

        assert set(resources._resources) == {"srv::mem://r1"}
        assert set(prompts._prompts) == {"srv::p1"}

    # ---- G3: a non-list where a list is expected ------------------------

    @pytest.mark.asyncio
    async def test_tools_collection_as_an_empty_mapping_preserves(self) -> None:
        """The fifth trigger. `list({})` is `[]`, so an object where the
        protocol requires an array was coerced into a perfectly valid-looking
        "the kind is empty" answer -- and cleared and published exactly like
        G1, one step further along.
        """
        manager, sink, primed_publishes = await self._prime_and_relist(
            "tools", [{"name": "alpha", "inputSchema": {}}], {"tools": {}}
        )

        assert set(manager._tools) == {"srv::alpha"}
        assert sink.tools == primed_publishes

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "collection",
        [
            pytest.param({"a": 1}, id="non-empty-mapping"),
            pytest.param("abc", id="string"),
            pytest.param(7, id="number"),
        ],
    )
    async def test_tools_collection_as_other_non_lists_preserves(
        self, collection: Any
    ) -> None:
        """These were preserved before the fix too, but only by accident: `list()`
        coerced them into entries (a mapping's keys, a string's characters)
        which then all happened to fail parsing, so the all-malformed rule
        caught them. `7` did not even get that far -- it raised `TypeError`.
        Pin the outcome so it stops depending on that chain.
        """
        manager, sink, primed_publishes = await self._prime_and_relist(
            "tools", [{"name": "alpha", "inputSchema": {}}], {"tools": collection}
        )

        assert set(manager._tools) == {"srv::alpha"}
        assert sink.tools == primed_publishes

    @pytest.mark.asyncio
    async def test_a_null_collection_costs_only_its_own_kind(self) -> None:
        """`{"tools": null}` used to raise `TypeError` out of
        `_fetch_server_listings` -- before resources and prompts were ever
        classified -- so one malformed kind aborted the whole reconcile and an
        honest resources answer arriving in the same pass was thrown away.
        Per kind, still.
        """
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))
        managed = self._managed("srv")
        manager._clients["srv"] = managed

        self._wire_raw(
            manager,
            {
                "tools": {"tools": [{"name": "alpha", "inputSchema": {}}]},
                "resources": {"resources": [{"uri": "mem://old"}]},
            },
        )
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)
        assert set(manager._tools) == {"srv::alpha"}

        self._wire_raw(
            manager,
            {
                "tools": {"tools": None},
                "resources": {"resources": [{"uri": "mem://new"}]},
            },
        )
        manager._handle_downstream_notification(
            "srv", managed, "notifications/tools/list_changed"
        )
        await self._drain(manager)

        assert set(manager._tools) == {"srv::alpha"}
        assert set(manager._resources) == {"srv::mem://new"}, (
            "an unreadable tools listing swallowed an honest resources answer"
        )

    # ---- G2: an entry with no identity is not an entry -------------------

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kind", "primed", "store_attr", "primed_id"),
        [
            pytest.param(
                "resources", [{"uri": "mem://r1"}], "_resources", "srv::mem://r1"
            ),
            pytest.param("prompts", [{"name": "p1"}], "_prompts", "srv::p1"),
        ],
    )
    async def test_empty_entry_objects_do_not_invent_an_identity(
        self, kind: str, primed: list[dict[str, Any]], store_attr: str, primed_id: str
    ) -> None:
        """`resource.get("uri", "")` and `prompt.get("name", "")` made `{}`
        parse successfully as an entry whose identity is `srv::`. It then
        replaced the real entry and the change was published -- a catalog entry
        the downstream never offered and the MCP models would reject.

        With the identity required, `{}` fails to parse, which routes it into
        the per-entry skip and -- since nothing else parsed -- into the
        all-malformed preserve-prior rule.
        """
        manager, sink, primed_publishes = await self._prime_and_relist(
            kind, primed, {kind: [{}]}
        )

        store = cast(dict[str, Any], getattr(manager, store_attr))
        assert set(store) == {primed_id}
        assert f"srv::{''}" not in store, "synthesized a bogus `srv::` identity"
        assert getattr(sink, kind) == primed_publishes

    @pytest.mark.asyncio
    async def test_an_empty_string_tool_name_is_not_an_identity_either(self) -> None:
        """Tools reached the same place by a different route. `tool["name"]`
        does not default, so a *missing* name already raised -- but a name of
        `""` sailed through and indexed as `srv::`, replacing the real tool.
        Requiring a non-empty string closes both at once.
        """
        manager, sink, primed_publishes = await self._prime_and_relist(
            "tools",
            [{"name": "alpha", "inputSchema": {}}],
            {"tools": [{"name": "", "inputSchema": {}}]},
        )

        assert set(manager._tools) == {"srv::alpha"}
        assert sink.tools == primed_publishes

    @pytest.mark.asyncio
    async def test_an_identity_less_entry_costs_only_itself(self) -> None:
        """The per-entry semantics are unchanged by the stricter identity: an
        entry with none is skipped, and a good entry beside it is still indexed
        and published. Without this, "fail to parse" could be over-applied into
        a rule that preserves whenever *any* entry is bad.
        """
        manager, sink, primed_publishes = await self._prime_and_relist(
            "resources",
            [{"uri": "mem://r1"}],
            {"resources": [{}, {"uri": "mem://r2"}]},
        )

        assert set(manager._resources) == {"srv::mem://r2"}
        assert sink.resources == primed_publishes + 1

    @pytest.mark.asyncio
    async def test_entries_that_are_not_objects_are_skipped_not_indexed(self) -> None:
        """A listing of bare strings. Each entry fails to parse rather than
        raising out of the parser, and with none of them parseable the kind is
        preserved."""
        manager, sink, primed_publishes = await self._prime_and_relist(
            "tools", [{"name": "alpha", "inputSchema": {}}], {"tools": ["a string", 3]}
        )

        assert set(manager._tools) == {"srv::alpha"}
        assert sink.tools == primed_publishes

    def test_required_identity_rejects_every_non_identity(self) -> None:
        """The helper directly, so the rule is pinned independently of the three
        parsers that call it."""
        assert _required_identity({"name": "ok"}, "name") == "ok"
        for entry in ({}, {"name": ""}, {"name": None}, {"name": 3}, {"name": ["a"]}):
            with pytest.raises((TypeError, ValueError)):
                _required_identity(entry, "name")
        for entry in ("a string", 3, None, ["a"]):
            with pytest.raises(TypeError):
                _required_identity(entry, "name")


class TestToolLimitIsEnforced:
    """#175 item 1: the truncation boundary itself, pinned.

    Nothing in this file asserted how many tools land when a server offers more
    than `max_tools_per_server`, and it showed: mutating the guard
    `len(entries) >= limit` to `> limit` was the sole survivor of nine mutants
    run against this file. That off-by-one lets a server put `limit + 1` tools
    in the catalog -- the guard is a resource bound, so one more than the bound
    every time is exactly the kind of drift a bound exists to stop.

    The assertions are therefore *exact* counts at `limit - 1`, `limit` and
    `limit + 1`. "Fewer than offered" would pass under the mutant at
    `limit + 1`, which is the only case that distinguishes `>=` from `>`.
    """

    LIMIT = 5

    @staticmethod
    def _listing(count: int) -> list[dict[str, Any]]:
        """`inputSchema` on every entry: #175 item 4 makes a tool without one
        unparseable, and a fixture that omitted it would report zero indexed
        here for a reason that has nothing to do with the limit."""
        return [
            {
                "name": f"t{i}",
                "description": f"tool {i}",
                "inputSchema": {"type": "object"},
            }
            for i in range(count)
        ]

    @pytest.mark.parametrize("offered", [LIMIT - 1, LIMIT, LIMIT + 1])
    def test_no_more_than_the_limit_is_ever_indexed(self, offered: int) -> None:
        manager = ClientManager(max_tools_per_server=self.LIMIT)

        indexed = manager._index_tools("srv", self._listing(offered))

        expected = min(offered, self.LIMIT)
        assert indexed == expected
        assert len(manager._tools) == expected
        assert set(manager._tools) == {f"srv::t{i}" for i in range(expected)}

    def test_truncation_keeps_the_first_entries_and_says_so(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The entries kept are the leading ones, and the drop is not silent --
        a bound that discards tools without a word is indistinguishable from a
        downstream that never offered them."""
        manager = ClientManager(max_tools_per_server=self.LIMIT)

        with caplog.at_level("WARNING", logger="pmcp.client.manager"):
            indexed = manager._index_tools("srv", self._listing(self.LIMIT + 3))

        assert indexed == self.LIMIT
        assert set(manager._tools) == {f"srv::t{i}" for i in range(self.LIMIT)}
        assert "srv::t5" not in manager._tools
        assert any("truncating" in record.message for record in caplog.records)


class TestDuplicateIdentitiesAreNotDoubleCounted:
    """#175 item 5: the count is of entries that landed, not entries offered.

    `_index_resources`' docstring promised exactly this -- "the count returned
    is what was actually indexed, not what was offered, so a caller reporting
    it is not overstating the catalog" -- while the code returned
    `len(entries)`. Two entries sharing an identity are two list items and one
    catalog key, so the promise was false precisely when identities collide,
    and a documented guarantee contradicted by the code is worse than an
    undocumented gap.

    Each kind is asserted against **its own** catalog: `_index_tools` against
    `_tools`, `_index_resources` against `_resources`, `_index_prompts` against
    `_prompts`. Comparing all three to `_tools` would prove nothing for the
    other two.
    """

    def test_duplicate_tools_are_counted_once_and_the_last_one_wins(self) -> None:
        manager = ClientManager()

        indexed = manager._index_tools(
            "srv",
            [
                {"name": "dup", "description": "first", "inputSchema": {}},
                {"name": "dup", "description": "second", "inputSchema": {}},
                {"name": "solo", "description": "only", "inputSchema": {}},
            ],
        )

        assert set(manager._tools) == {"srv::dup", "srv::solo"}
        assert indexed == len(manager._tools) == 2
        assert manager._tools["srv::dup"].description == "second"

    def test_duplicate_resources_are_counted_once(self) -> None:
        manager = ClientManager()

        indexed = manager._index_resources(
            "srv",
            [
                {"uri": "mem://dup", "name": "first"},
                {"uri": "mem://dup", "name": "second"},
                {"uri": "mem://solo"},
            ],
        )

        assert set(manager._resources) == {"srv::mem://dup", "srv::mem://solo"}
        assert indexed == len(manager._resources) == 2
        assert manager._resources["srv::mem://dup"].name == "second"

    def test_duplicate_prompts_are_counted_once(self) -> None:
        manager = ClientManager()

        indexed = manager._index_prompts(
            "srv",
            [
                {"name": "dup", "title": "first"},
                {"name": "dup", "title": "second"},
                {"name": "solo"},
            ],
        )

        assert set(manager._prompts) == {"srv::dup", "srv::solo"}
        assert indexed == len(manager._prompts) == 2
        assert manager._prompts["srv::dup"].title == "second"

    def test_a_listing_that_is_entirely_duplicates_counts_one(self) -> None:
        """The edge the plan calls out. One key lands, so the count is one --
        and it is emphatically not zero, which would route the listing into
        `_reconcile_once`'s offered-but-none-parseable rule and preserve a
        stale catalog on the strength of a duplicate."""
        manager = ClientManager()

        indexed = manager._index_tools(
            "srv",
            [
                {"name": "same", "inputSchema": {}},
                {"name": "same", "inputSchema": {}},
                {"name": "same", "inputSchema": {}},
            ],
        )

        assert indexed == len(manager._tools) == 1

    def test_the_collision_is_logged_so_it_is_diagnosable(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The honest count makes last-write-wins visible; the log is what
        makes it diagnosable. Logged at DEBUG, naming the colliding id."""
        manager = ClientManager()

        with caplog.at_level("DEBUG", logger="pmcp.client.manager"):
            manager._index_tools(
                "srv",
                [
                    {"name": "dup", "inputSchema": {}},
                    {"name": "dup", "inputSchema": {}},
                ],
            )

        assert any(
            "srv::dup" in record.message and record.levelname == "DEBUG"
            for record in caplog.records
        )

    def test_duplicates_publish_once_not_twice(self) -> None:
        """The publish gate keys off the count, so it must not have moved: a
        listing that indexed something still publishes exactly one note."""
        sink = _RecordingSink()
        manager = ClientManager(catalog_events=cast(Any, sink))

        manager._index_tools(
            "srv",
            [
                {"name": "dup", "inputSchema": {}},
                {"name": "dup", "inputSchema": {}},
            ],
        )

        assert (sink.tools, sink.resources, sink.prompts) == (1, 0, 0)


class TestZeroLimitLogsAccurately:
    """#175 item 3: `max_tools_per_server: 0` must not be reported as a
    downstream that sent garbage.

    A zero limit empties `_parse_tool_entries`' result before a single entry is
    examined, so `_reconcile_once`'s offered-but-none-parseable branch fired
    and announced "Every tools entry in the listing was unparseable" -- an
    accusation aimed at the server for a decision this gateway's own policy
    file made. The operator's next move is to fix the policy, and the log has
    to point there.

    Deliberately fixed in the *log* and not in the schema. `LimitsPolicy` gets
    no `Field(ge=1)`: `PolicyManager._load_policy(..., fatal=False)` -- the
    auto-discovery path -- swallows any validation exception and leaves the
    default allow-all `GatewayPolicy` in place, so making `0` schema-invalid
    would silently discard the operator's *entire* policy file, allow and deny
    lists and redaction included. That fail-open is #202 and is not this
    change's to fix; the test below pins that `0` still validates, so a future
    tightening cannot land here unnoticed.
    """

    @staticmethod
    def _managed(name: str) -> ManagedClient:
        status = ServerStatus(name=name, status=ServerStatusEnum.ONLINE, tool_count=0)
        managed = ManagedClient(config=MagicMock(), process=MagicMock(), status=status)
        managed.config.name = name
        return managed

    @staticmethod
    def _wire(manager: ClientManager, tools: list[dict[str, Any]]) -> None:
        async def fake_send(
            managed: ManagedClient,
            method: str,
            params: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            kind = method.split("/")[0]
            return {kind: list(tools) if kind == "tools" else []}

        manager._send_request = fake_send  # type: ignore[method-assign]

    def test_a_zero_limit_still_validates_on_the_policy_schema(self) -> None:
        """Rejecting it is what triggers #202's fail-open, so it must not be
        rejected."""
        assert LimitsPolicy(max_tools_per_server=0).max_tools_per_server == 0

    @pytest.mark.asyncio
    async def test_zero_limit_names_the_limit_and_blames_no_one(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        manager = ClientManager(max_tools_per_server=0)
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire(manager, [{"name": "alpha", "inputSchema": {}}])

        with caplog.at_level("WARNING", logger="pmcp.client.manager"):
            manager._handle_downstream_notification(
                "srv", managed, "notifications/tools/list_changed"
            )

            async def _wait() -> None:
                while manager._reconcile_tasks:
                    await asyncio.sleep(0.005)

            await asyncio.wait_for(_wait(), 5.0)

        messages = [record.message for record in caplog.records]
        assert any("max_tools_per_server is 0" in message for message in messages), (
            f"no message named the zero limit: {messages}"
        )
        assert not any("unparseable" in message for message in messages), (
            f"a zero limit was reported as a malformed listing: {messages}"
        )

    def test_a_positive_limit_still_reports_truncation_the_old_way(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The zero-limit wording is a special case, not a replacement: an
        ordinary overrun must still say it truncated."""
        manager = ClientManager(max_tools_per_server=1)

        with caplog.at_level("WARNING", logger="pmcp.client.manager"):
            manager._index_tools(
                "srv",
                [
                    {"name": "a", "inputSchema": {}},
                    {"name": "b", "inputSchema": {}},
                ],
            )

        assert any("truncating" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_a_genuinely_unparseable_listing_still_says_unparseable(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The other half of the same guarantee: the accusation is still made
        when it is true."""
        manager = ClientManager(max_tools_per_server=100)
        managed = self._managed("srv")
        manager._clients["srv"] = managed
        self._wire(manager, [{"name": ""}, {"nope": True}])

        with caplog.at_level("WARNING", logger="pmcp.client.manager"):
            manager._handle_downstream_notification(
                "srv", managed, "notifications/tools/list_changed"
            )

            async def _wait() -> None:
                while manager._reconcile_tasks:
                    await asyncio.sleep(0.005)

            await asyncio.wait_for(_wait(), 5.0)

        messages = [record.message for record in caplog.records]
        assert any("unparseable" in message for message in messages), messages


class TestAdoptProcessRemovesStaleIndexesFirst:
    """#175 item 2: `adopt_process` must clear the server's catalog entries
    before it indexes, like every other path into the indexers.

    `_connect_stdio`, `_reconcile_once` and `_cleanup_client` all remove this
    server's entries first. `adopt_process` did not, so adopting a server that
    had already been indexed under the same name left the previous listing's
    tools in the catalog alongside the new ones -- entries the adopted process
    does not serve, still routable, until something unrelated removed them.

    Asserted on catalog *contents*, not on whether `_remove_server_indexes` was
    called: a test that only checks the call passes just as happily if the
    removal runs after the index.
    """

    @staticmethod
    def _process() -> Any:
        process = MagicMock()
        process.returncode = None
        process.stdin = MagicMock()
        process.stdout = MagicMock()
        process.stderr = None
        return process

    @staticmethod
    def _config(name: str) -> Any:
        config = MagicMock()
        config.name = name
        return config

    async def _adopt(
        self, manager: ClientManager, name: str, tools: list[dict[str, Any]]
    ) -> None:
        async def fake_listings(managed: ManagedClient) -> dict[str, Any]:
            return {"tools": list(tools), "resources": [], "prompts": []}

        with (
            patch.object(manager, "_read_stdout", new=AsyncMock(return_value=None)),
            patch.object(manager, "_read_stderr", new=AsyncMock(return_value=None)),
            patch.object(manager, "_send_initialize", new=AsyncMock(return_value=None)),
            patch.object(manager, "_fetch_server_listings", new=fake_listings),
        ):
            await manager.adopt_process(name, self._process(), self._config(name))

    @pytest.mark.asyncio
    async def test_a_prior_listings_tools_do_not_survive_the_adopt(self) -> None:
        manager = ClientManager()
        manager._index_tools("srv", [{"name": "stale", "inputSchema": {}}])
        assert set(manager._tools) == {"srv::stale"}

        await self._adopt(manager, "srv", [{"name": "fresh", "inputSchema": {}}])

        assert set(manager._tools) == {"srv::fresh"}, (
            "adopt_process indexed on top of the previous catalog"
        )
        assert manager._servers["srv"].tool_count == 1

    @pytest.mark.asyncio
    async def test_resources_and_prompts_are_cleared_too(self) -> None:
        """The removal is per server, not per kind -- an adopt that replaced
        only the tools would leave a stale resource just as routable."""
        manager = ClientManager()
        manager._index_resources("srv", [{"uri": "mem://stale"}])
        manager._index_prompts("srv", [{"name": "stale"}])

        await self._adopt(manager, "srv", [{"name": "fresh", "inputSchema": {}}])

        assert manager._resources == {}
        assert manager._prompts == {}

    @pytest.mark.asyncio
    async def test_another_servers_entries_are_untouched(self) -> None:
        """`_remove_server_indexes` is per-server-name, and the adopt must not
        widen that: clearing the catalog wholesale would be a much worse bug
        than the one being fixed."""
        manager = ClientManager()
        manager._index_tools("other", [{"name": "keep", "inputSchema": {}}])

        await self._adopt(manager, "srv", [{"name": "fresh", "inputSchema": {}}])

        assert set(manager._tools) == {"other::keep", "srv::fresh"}

    @pytest.mark.asyncio
    async def test_adopting_a_name_with_no_prior_index_is_unchanged(self) -> None:
        """The edge the plan calls out: removing nothing must not be an error
        and must not stop the fresh listing being indexed."""
        manager = ClientManager()

        await self._adopt(manager, "srv", [{"name": "fresh", "inputSchema": {}}])

        assert set(manager._tools) == {"srv::fresh"}


class TestMissingInputSchemaIsUnparseable:
    """#175 item 4: a tool that declares no `inputSchema` is not indexed.

    `tool.get("inputSchema", {})` manufactured a schema the server never sent,
    and `{}` does not mean "we do not know" -- it means "any arguments at all
    are valid", which is then published to every caller and every model reading
    the catalog. MCP requires `inputSchema` on a tool, so a tool without one is
    a tool we could not read, and #172 already settled what happens to those:
    skip it and say so, rather than invent the missing value.

    This is the only behaviour change in #175 and is called out as such in the
    changelog: a downstream that omitted `inputSchema` used to be accepted with
    a permissive schema and is now skipped.
    """

    def test_a_tool_without_an_input_schema_is_skipped_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        manager = ClientManager()

        with caplog.at_level("WARNING", logger="pmcp.client.manager"):
            indexed = manager._index_tools("srv", [{"name": "schemaless"}])

        assert indexed == 0
        assert manager._tools == {}
        assert any(
            "unparseable tool" in record.message and "schemaless" in record.message
            for record in caplog.records
        ), [record.message for record in caplog.records]

    def test_a_tool_with_an_input_schema_is_indexed_unchanged(self) -> None:
        """The other half: nothing about a well-formed tool moved."""
        manager = ClientManager()
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}

        indexed = manager._index_tools(
            "srv", [{"name": "ok", "description": "d", "inputSchema": schema}]
        )

        tool = manager.get_tool("srv::ok")
        assert indexed == 1
        assert tool is not None
        assert tool.input_schema == schema

    def test_an_explicitly_empty_schema_is_still_an_answer(self) -> None:
        """`{}` is the server saying "any arguments", which is a thing it is
        entitled to say. Only the *absence* is unreadable -- rejecting `{}`
        would drop tools that are behaving correctly."""
        manager = ClientManager()

        assert manager._index_tools("srv", [{"name": "any", "inputSchema": {}}]) == 1
        assert set(manager._tools) == {"srv::any"}

    @pytest.mark.parametrize(
        "schema", [None, "not-a-dict", ["not", "a", "dict"], 3, True]
    )
    def test_a_non_object_schema_is_unparseable_too(self, schema: Any) -> None:
        """`inputSchema: null` is distinct from absent and just as unusable;
        so is any other non-object. Before this, `null` reached
        `_schema_dialect` and only failed there by accident of a `.get` call."""
        manager = ClientManager()

        assert (
            manager._index_tools("srv", [{"name": "bad", "inputSchema": schema}]) == 0
        )
        assert manager._tools == {}

    def test_a_schemaless_tool_costs_only_itself(self) -> None:
        """Per-entry, like every other parse failure: the tools either side of
        it are still indexed."""
        manager = ClientManager()

        indexed = manager._index_tools(
            "srv",
            [
                {"name": "before", "inputSchema": {}},
                {"name": "schemaless"},
                {"name": "after", "inputSchema": {}},
            ],
        )

        assert indexed == 2
        assert set(manager._tools) == {"srv::before", "srv::after"}

    def test_required_object_rejects_every_non_object(self) -> None:
        """The helper directly, so the rule is pinned independently of the
        parser that calls it -- and so the next reader can see why it is not
        `_required_identity`, which requires a non-empty *string* and would
        reject every valid tool."""
        assert _required_object({"inputSchema": {}}, "inputSchema") == {}
        assert _required_object({"inputSchema": {"type": "object"}}, "inputSchema") == {
            "type": "object"
        }
        for entry in (
            {},
            {"inputSchema": None},
            {"inputSchema": ""},
            {"inputSchema": "{}"},
            {"inputSchema": []},
            {"inputSchema": 0},
        ):
            with pytest.raises((TypeError, ValueError)):
                _required_object(entry, "inputSchema")
        for entry in ("a string", 3, None, ["a"]):
            with pytest.raises(TypeError):
                _required_object(entry, "inputSchema")
