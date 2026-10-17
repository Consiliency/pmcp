"""Tests for GatewayServer lifecycle."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.server import GatewayServer
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig


class TestGatewayServerInit:
    """Tests for GatewayServer initialization."""

    def test_init_defaults(self) -> None:
        """Test GatewayServer initializes with defaults."""
        server = GatewayServer()

        # Bound at construction (Consiliency/pmcp#372 round 16): with nothing
        # named or served, the project found from the working directory now.
        from pmcp.env_store import resolve_project_root

        assert server._project_root == resolve_project_root(None)
        assert server._custom_config_path is None
        assert server._cache_dir == Path(".mcp-gateway")
        assert server._server is None
        assert server._capability_summary == ""

    def test_init_with_paths(self, tmp_path: Path) -> None:
        """Test GatewayServer initializes with custom paths."""
        project_root = tmp_path / "project"
        config_path = tmp_path / "config.json"
        cache_dir = tmp_path / "cache"

        server = GatewayServer(
            project_root=project_root,
            custom_config_path=config_path,
            cache_dir=cache_dir,
        )

        assert server._project_root == project_root
        assert server._custom_config_path == config_path
        assert server._cache_dir == cache_dir


class TestGatewayServerShutdown:
    """Tests for GatewayServer shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_disconnects_clients(self) -> None:
        """Test that shutdown disconnects all clients."""
        server = GatewayServer()

        # Mock the client manager
        server._client_manager = MagicMock()
        server._client_manager.disconnect_all = AsyncMock()

        await server.shutdown()

        server._client_manager.disconnect_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_releases_singleton_after_manager_cleanup(self) -> None:
        """Shutdown should release the singleton lock after manager cleanup returns."""
        server = GatewayServer()
        events: list[str] = []

        async def disconnect_all() -> None:
            events.append("disconnect:start")
            await asyncio.sleep(0)
            events.append("disconnect:end")

        server._client_manager = MagicMock()
        server._client_manager.disconnect_all = disconnect_all

        with patch("pmcp.server.release_singleton_lock") as release_lock:
            release_lock.side_effect = lambda: events.append("release")
            await server.shutdown()

        assert events == ["disconnect:start", "disconnect:end", "release"]

    @pytest.mark.asyncio
    async def test_shutdown_handles_timeout(self) -> None:
        """Test that shutdown handles timeout gracefully."""
        server = GatewayServer()

        # Mock client manager that takes too long
        async def slow_disconnect() -> None:
            await asyncio.sleep(20)  # Longer than timeout

        server._client_manager = MagicMock()
        server._client_manager.disconnect_all = slow_disconnect

        # Should complete without error (timeout is 10s internally)
        # We'll just verify it doesn't raise
        await asyncio.wait_for(server.shutdown(), timeout=15)

    @pytest.mark.asyncio
    async def test_shutdown_handles_error(self) -> None:
        """Test that shutdown handles errors gracefully."""
        server = GatewayServer()

        # Mock client manager that raises
        server._client_manager = MagicMock()
        server._client_manager.disconnect_all = AsyncMock(
            side_effect=RuntimeError("Disconnect failed")
        )

        # Should not raise
        await server.shutdown()


class TestGatewayServerHandlers:
    """Tests for GatewayServer handler registration."""

    def test_create_server_registers_handlers(self) -> None:
        """Test that _create_server registers handlers."""
        server = GatewayServer()
        server._create_server(instructions="Test instructions")

        assert server._server is not None
        assert server._server.name == "mcp-gateway"

    def test_create_server_with_instructions(self) -> None:
        """Test that _create_server passes instructions."""
        server = GatewayServer()
        instructions = "Test capability summary"
        server._create_server(instructions=instructions)

        # Server should be created
        assert server._server is not None


class TestGatewayServerIntegration:
    """Integration tests for GatewayServer (requires mocking)."""

    @pytest.mark.asyncio
    async def test_initialize_no_configs(self) -> None:
        """Test initialize with no server configs."""
        with patch("pmcp.server.load_configs", return_value=[]):
            with patch("pmcp.server.load_manifest") as mock_manifest:
                mock_manifest.return_value = MagicMock()
                mock_manifest.return_value.servers = {}

                with (
                    patch("pmcp.server.load_enabled_auto_start", return_value=set()),
                    patch("pmcp.server.load_disabled_auto_start", return_value=set()),
                    patch("pmcp.server.load_descriptions_cache", return_value=None),
                    patch("pmcp.server.generate_capability_summary") as mock_summary,
                ):
                    mock_summary.return_value = "No tools available"

                    server = GatewayServer()
                    await server.initialize()

                    assert server._server is not None
                    assert server._capability_summary == "No tools available"


class TestStartupProcessOwnership:
    """Startup must not signal processes it did not start (Consiliency/pmcp#389)."""

    def _make_config(self, command: str, args: list[str]) -> ResolvedServerConfig:
        return ResolvedServerConfig(
            name="test-server",
            source="project",
            config=LocalMcpServerConfig(command=command, args=args),
        )

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux startup scan")
    @pytest.mark.parametrize("eager", [False, True])
    async def test_startup_leaves_matching_foreign_process_alive(
        self, tmp_path: Path, eager: bool
    ) -> None:
        args = ["-c", "import time; time.sleep(299.123)", str(tmp_path)]
        config = self._make_config(sys.executable, args)
        foreign = subprocess.Popen(
            [sys.executable, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        server = GatewayServer(project_root=tmp_path)
        try:
            with (
                patch("pmcp.server.load_configs", return_value=[config]),
                patch("pmcp.server._manifest_for", return_value=MagicMock(servers={})),
                patch(
                    "pmcp.server.load_enabled_auto_start",
                    return_value={config.name} if eager else set(),
                ),
                patch("pmcp.server.load_disabled_auto_start", return_value=set()),
                patch("pmcp.server.load_descriptions_cache", return_value=None),
                patch(
                    "pmcp.server.generate_capability_summary", return_value="No tools"
                ),
                patch.object(server._client_manager, "start_health_monitor"),
                patch.object(
                    server._client_manager, "connect_all", return_value=[]
                ) as connect,
                patch.object(server._client_manager, "register_lazy_configs") as lazy,
            ):
                await server.initialize()
                if eager:
                    connect.assert_awaited_once_with([config])
                else:
                    lazy.assert_called_once_with([config])
                with pytest.raises(subprocess.TimeoutExpired):
                    foreign.wait(timeout=0.2)
                await server.shutdown()
                with pytest.raises(subprocess.TimeoutExpired):
                    foreign.wait(timeout=0.2)
        finally:
            foreign.terminate()
            foreign.wait(timeout=5)
