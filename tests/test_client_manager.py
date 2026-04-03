"""Tests for ClientManager."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import time
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.client.manager import (
    ClientManager,
    ManagedClient,
    PendingRequest,
    _extract_tags,
    _infer_risk_hint,
    _truncate_description,
)
from pmcp.types import (
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    RiskHint,
    ServerStatus,
    ServerStatusEnum,
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


class TestClientManager:
    """Tests for ClientManager class."""

    @pytest.fixture
    def manager(self) -> ClientManager:
        """Create a ClientManager instance."""
        return ClientManager(max_tools_per_server=100)

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
                    "Authorization": "${PMCP_TEST_TOKEN}",
                    "X-Static": "literal-value",
                    "X-Missing": "${PMCP_MISSING}",
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
            "Authorization": "test-token",
            "X-Static": "literal-value",
            "X-Missing": "",
        }

        await manager.disconnect_all()


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
        manager, managed = self._make_manager_with_client(returncode=None, task_done=False)

        await manager._cleanup_client("test", managed)

        managed.read_task.cancel.assert_called_once()  # type: ignore[union-attr]
        managed.process.kill.assert_called_once()  # type: ignore[union-attr]
        assert "test" not in manager._clients
        assert "test" not in manager._servers

    @pytest.mark.asyncio
    async def test_cleanup_client_skips_cancel_if_task_done(self) -> None:
        """_cleanup_client should not cancel an already-done read task."""
        manager, managed = self._make_manager_with_client(returncode=None, task_done=True)

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
    async def test_connect_stdio_calls_cleanup_when_existing_client_present(self) -> None:
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
        mock_process.wait = AsyncMock(
            side_effect=[asyncio.TimeoutError(), 0]
        )

        status = ServerStatus(name="slow2", status=ServerStatusEnum.ONLINE, tool_count=0)
        managed = ManagedClient(config=MagicMock(), process=mock_process, status=status)
        managed.read_task = None
        manager._clients["slow2"] = managed
        manager._servers["slow2"] = status

        with patch("pmcp.client.manager.logger") as mock_logger:
            await manager.disconnect_all()

        mock_process.kill.assert_called_once()
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert not any("SIGKILL" in w or "D-state" in w for w in warning_calls)
