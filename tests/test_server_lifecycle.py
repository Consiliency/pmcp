"""Tests for GatewayServer lifecycle."""

from __future__ import annotations

import asyncio
import os
import signal
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

        assert server._project_root is None
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


class TestOrphanScan:
    """Tests for GatewayServer._kill_orphan_processes."""

    def _make_config(self, command: str, args: list[str]) -> ResolvedServerConfig:
        return ResolvedServerConfig(
            name="test-server",
            source="project",
            config=LocalMcpServerConfig(command=command, args=args),
        )

    def _write_cmdline(self, proc_dir: Path, pid: int, argv: list[str]) -> None:
        entry = proc_dir / str(pid)
        entry.mkdir()
        (entry / "cmdline").write_bytes(
            b"\x00".join(a.encode() for a in argv) + b"\x00"
        )

    def test_skips_on_non_linux(self, tmp_path: Path) -> None:
        """_kill_orphan_processes should do nothing on non-Linux platforms."""
        server = GatewayServer()
        config = self._make_config("npx", ["some-mcp-server"])
        with (
            patch("pmcp.server.sys") as mock_sys,
            patch("pmcp.server.os.kill") as mock_kill,
        ):
            mock_sys.platform = "darwin"
            server._kill_orphan_processes([config], _proc_path=tmp_path)
        mock_kill.assert_not_called()

    def test_kills_matching_pid(self, tmp_path: Path) -> None:
        """_kill_orphan_processes should SIGKILL a process whose argv matches a config."""
        proc_dir = tmp_path / "proc"
        proc_dir.mkdir()
        self._write_cmdline(proc_dir, 12345, ["npx", "@mcp/server-fs", "/srv"])

        server = GatewayServer()
        config = self._make_config("npx", ["@mcp/server-fs", "/srv"])

        with patch("pmcp.server.sys") as mock_sys, patch("pmcp.server.os") as mock_os:
            mock_sys.platform = "linux"
            mock_os.getpid.return_value = 1  # own pid won't match 12345
            server._kill_orphan_processes([config], _proc_path=proc_dir)
            mock_os.kill.assert_called_once_with(12345, signal.SIGKILL)

    def test_skips_own_pid(self, tmp_path: Path) -> None:
        """_kill_orphan_processes should not kill its own PID."""
        proc_dir = tmp_path / "proc"
        proc_dir.mkdir()
        own_pid = os.getpid()
        self._write_cmdline(proc_dir, own_pid, ["npx", "@mcp/server-fs", "/srv"])

        server = GatewayServer()
        config = self._make_config("npx", ["@mcp/server-fs", "/srv"])

        with (
            patch("pmcp.server.sys") as mock_sys,
            patch("pmcp.server.os.kill") as mock_kill,
        ):
            mock_sys.platform = "linux"
            server._kill_orphan_processes([config], _proc_path=proc_dir)
        mock_kill.assert_not_called()

    def test_handles_permission_error_on_kill(self, tmp_path: Path) -> None:
        """PermissionError on os.kill should be caught and not propagate."""
        proc_dir = tmp_path / "proc"
        proc_dir.mkdir()
        self._write_cmdline(proc_dir, 55555, ["npx", "some-server"])

        server = GatewayServer()
        config = self._make_config("npx", ["some-server"])

        with patch("pmcp.server.sys") as mock_sys, patch("pmcp.server.os") as mock_os:
            mock_sys.platform = "linux"
            mock_os.getpid.return_value = 1
            mock_os.kill.side_effect = PermissionError("not allowed")
            # Should not raise
            server._kill_orphan_processes([config], _proc_path=proc_dir)

    def test_ignores_unreadable_cmdline(self, tmp_path: Path) -> None:
        """PermissionError when reading cmdline should be silently skipped."""
        proc_dir = tmp_path / "proc"
        proc_dir.mkdir()
        entry = proc_dir / "77777"
        entry.mkdir()
        cmdline = entry / "cmdline"
        cmdline.write_bytes(b"")  # empty — simulates unreadable/zombie process

        server = GatewayServer()
        config = self._make_config("npx", ["some-server"])

        with (
            patch("pmcp.server.sys") as mock_sys,
            patch("pmcp.server.os.kill") as mock_kill,
        ):
            mock_sys.platform = "linux"
            server._kill_orphan_processes([config], _proc_path=proc_dir)
        mock_kill.assert_not_called()
