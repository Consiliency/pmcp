"""Tests for CLI module."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pmcp.cli import async_main, parse_args, setup_logging


class TestParseArgs:
    """Tests for argument parsing."""

    def test_default_args(self) -> None:
        """Test default argument values."""
        with patch("sys.argv", ["mcp-gateway"]):
            args = parse_args()

        assert args.command is None
        assert args.project is None
        assert args.config is None
        assert args.policy is None
        assert args.log_level == "info"
        assert args.debug is False
        assert args.quiet is False

    def test_debug_flag(self) -> None:
        """Test debug flag parsing."""
        with patch("sys.argv", ["mcp-gateway", "--debug"]):
            args = parse_args()
        assert args.debug is True

    def test_quiet_flag(self) -> None:
        """Test quiet flag parsing."""
        with patch("sys.argv", ["mcp-gateway", "-q"]):
            args = parse_args()
        assert args.quiet is True

    def test_log_level(self) -> None:
        """Test log level argument."""
        with patch("sys.argv", ["mcp-gateway", "-l", "debug"]):
            args = parse_args()
        assert args.log_level == "debug"

    def test_project_path(self, tmp_path: Path) -> None:
        """Test project path argument."""
        with patch("sys.argv", ["mcp-gateway", "--project", str(tmp_path)]):
            args = parse_args()
        assert args.project == tmp_path

    def test_config_path(self, tmp_path: Path) -> None:
        """Test config path argument."""
        config_file = tmp_path / "config.json"
        config_file.touch()

        with patch("sys.argv", ["mcp-gateway", "--config", str(config_file)]):
            args = parse_args()
        assert args.config == config_file

    def test_policy_path(self, tmp_path: Path) -> None:
        """Test policy path argument."""
        policy_file = tmp_path / "policy.yaml"

        with patch("sys.argv", ["mcp-gateway", "--policy", str(policy_file)]):
            args = parse_args()
        assert args.policy == policy_file

    def test_refresh_command(self) -> None:
        """Test refresh subcommand."""
        with patch("sys.argv", ["mcp-gateway", "refresh"]):
            args = parse_args()
        assert args.command == "refresh"

    def test_refresh_with_server(self) -> None:
        """Test refresh with specific server."""
        with patch("sys.argv", ["mcp-gateway", "refresh", "--server", "github"]):
            args = parse_args()
        assert args.command == "refresh"
        assert args.server == "github"

    def test_refresh_with_force(self) -> None:
        """Test refresh with force flag."""
        with patch("sys.argv", ["mcp-gateway", "refresh", "--force"]):
            args = parse_args()
        assert args.command == "refresh"
        assert args.force is True

    def test_update_command_with_server(self) -> None:
        """Test update subcommand with explicit server."""
        with patch("pmcp.cli.importlib.metadata.version", return_value="0.0.0"):
            with patch("sys.argv", ["mcp-gateway", "update", "browser-use"]):
                args = parse_args()
        assert args.command == "update"
        assert args.server == "browser-use"
        assert args.all is False

    def test_update_command_with_all(self) -> None:
        """Test update subcommand with --all."""
        with patch("pmcp.cli.importlib.metadata.version", return_value="0.0.0"):
            with patch("sys.argv", ["mcp-gateway", "update", "--all"]):
                args = parse_args()
        assert args.command == "update"
        assert args.all is True

    def test_status_command(self) -> None:
        """Test status subcommand."""
        with patch("sys.argv", ["mcp-gateway", "status"]):
            args = parse_args()
        assert args.command == "status"

    def test_status_with_json(self) -> None:
        """Test status with json flag."""
        with patch("sys.argv", ["mcp-gateway", "status", "--json"]):
            args = parse_args()
        assert args.command == "status"
        assert args.json is True

    def test_status_with_server_filter(self) -> None:
        """Test status with specific server filter."""
        with patch("sys.argv", ["mcp-gateway", "status", "--server", "playwright"]):
            args = parse_args()
        assert args.command == "status"
        assert args.server == "playwright"

    def test_status_with_pending(self) -> None:
        """Test status with pending flag."""
        with patch("sys.argv", ["mcp-gateway", "status", "--pending"]):
            args = parse_args()
        assert args.command == "status"
        assert args.pending is True

    def test_status_with_verbose(self) -> None:
        """Test status with verbose flag."""
        with patch("sys.argv", ["mcp-gateway", "status", "-v"]):
            args = parse_args()
        assert args.command == "status"
        assert args.verbose is True

    def test_status_all_options(self) -> None:
        """Test status with all options combined."""
        with patch(
            "sys.argv",
            [
                "mcp-gateway",
                "status",
                "--json",
                "--server",
                "github",
                "--pending",
                "-v",
            ],
        ):
            args = parse_args()
        assert args.command == "status"
        assert args.json is True
        assert args.server == "github"
        assert args.pending is True
        assert args.verbose is True

    def test_guidance_with_telemetry_toggle(self) -> None:
        """Test guidance command telemetry toggle parsing."""
        with patch("pmcp.cli.importlib.metadata.version", return_value="0.0.0"):
            with patch("sys.argv", ["pmcp", "guidance", "--telemetry", "off"]):
                args = parse_args()
        assert args.command == "guidance"
        assert args.telemetry == "off"

    def test_doctor_command(self) -> None:
        """Test doctor subcommand."""
        with patch("pmcp.cli.importlib.metadata.version", return_value="0.0.0"):
            with patch("sys.argv", ["mcp-gateway", "doctor"]):
                args = parse_args()
        assert args.command == "doctor"

    def test_doctor_with_options(self, tmp_path: Path) -> None:
        """Test doctor command options."""
        with patch("pmcp.cli.importlib.metadata.version", return_value="0.0.0"):
            with patch(
                "sys.argv",
                [
                    "mcp-gateway",
                    "doctor",
                    "--project",
                    str(tmp_path),
                    "--timeout",
                    "5",
                ],
            ):
                args = parse_args()
        assert args.command == "doctor"
        assert args.project == tmp_path
        assert args.timeout == 5

    def test_version_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test --version output uses pmcp naming."""
        with patch("sys.argv", ["pmcp", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                parse_args()

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert output.startswith("pmcp ")


class TestSetupLogging:
    """Tests for logging setup."""

    def test_info_logging_level(self) -> None:
        """Test info logging level maps correctly."""
        # Reset logging handlers for clean test
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        root.setLevel(logging.NOTSET)

        setup_logging("info")
        assert root.level == logging.INFO

    def test_debug_logging_level(self) -> None:
        """Test debug logging level maps correctly."""
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        root.setLevel(logging.NOTSET)

        setup_logging("debug")
        assert root.level == logging.DEBUG

    def test_error_logging_level(self) -> None:
        """Test error logging level maps correctly."""
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)
        root.setLevel(logging.NOTSET)

        setup_logging("error")
        assert root.level == logging.ERROR


class TestMain:
    """Tests for main entry point."""

    def test_main_loads_dotenv(self) -> None:
        """Test that main loads .env file."""
        from pmcp.cli import main

        with patch("pmcp.cli.load_dotenv") as mock_dotenv:
            with patch("pmcp.cli.parse_args") as mock_parse:
                mock_parse.return_value = argparse.Namespace(
                    command=None,
                    project=None,
                    config=None,
                    policy=None,
                    log_level="info",
                    debug=False,
                    quiet=False,
                )

                with patch("asyncio.run") as mock_run:
                    mock_run.side_effect = KeyboardInterrupt()

                    main()

            # main() loads .env, then two pmcp env stores — 3 calls total
            assert mock_dotenv.call_count == 3

    def test_main_handles_keyboard_interrupt(self) -> None:
        """Test that main handles KeyboardInterrupt gracefully."""
        from pmcp.cli import main

        with patch("pmcp.cli.load_dotenv"):
            with patch("pmcp.cli.parse_args") as mock_parse:
                mock_parse.return_value = argparse.Namespace(
                    command=None,
                    project=None,
                    config=None,
                    policy=None,
                    log_level="info",
                    debug=False,
                    quiet=False,
                )

                with patch("asyncio.run") as mock_run:
                    mock_run.side_effect = KeyboardInterrupt()

                    # Should not raise
                    main()

    def test_main_exits_on_error(self) -> None:
        """Test that main exits with code 1 on error."""
        from pmcp.cli import main

        with patch("pmcp.cli.load_dotenv"):
            with patch("pmcp.cli.parse_args") as mock_parse:
                mock_parse.return_value = argparse.Namespace(
                    command=None,
                    project=None,
                    config=None,
                    policy=None,
                    log_level="info",
                    debug=False,
                    quiet=False,
                )

                with patch("asyncio.run") as mock_run:
                    mock_run.side_effect = RuntimeError("Fatal error")

                    with pytest.raises(SystemExit) as exc_info:
                        main()

                    assert exc_info.value.code == 1


class TestRunStatus:
    """Tests for run_status function."""

    @pytest.fixture
    def status_args(self) -> argparse.Namespace:
        """Create default status args."""
        return argparse.Namespace(
            command="status",
            json=False,
            server=None,
            pending=False,
            verbose=False,
            project=None,
            config=None,
            policy=None,
            log_level="warn",
        )

    @pytest.mark.asyncio
    async def test_status_no_servers(
        self, status_args: argparse.Namespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test status output when no servers configured."""
        from pmcp.cli import run_status

        with patch(
            "pmcp.cli._query_running_gateway_status", new=AsyncMock(return_value=None)
        ):
            with patch("pmcp.config.loader.load_configs", return_value=[]):
                await run_status(status_args)

        captured = capsys.readouterr()
        assert "No MCP servers configured" in captured.out

    @pytest.mark.asyncio
    async def test_status_json_no_servers(
        self, status_args: argparse.Namespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test JSON output when no servers configured."""
        import json

        from pmcp.cli import run_status

        status_args.json = True

        with patch(
            "pmcp.cli._query_running_gateway_status", new=AsyncMock(return_value=None)
        ):
            with patch("pmcp.config.loader.load_configs", return_value=[]):
                await run_status(status_args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["servers"] == []
        assert output["tools"] == 0

    @pytest.mark.asyncio
    async def test_status_filters_self_referential_gateway_config(
        self, status_args: argparse.Namespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Status should exclude gateway self-reference configs before connect."""
        from pmcp.cli import run_status
        from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

        gateway_config = ResolvedServerConfig(
            name="gateway",
            source="user",
            config=LocalMcpServerConfig(command="pmcp", args=[]),
        )

        with patch(
            "pmcp.cli._query_running_gateway_status", new=AsyncMock(return_value=None)
        ):
            with patch(
                "pmcp.config.loader.load_configs", return_value=[gateway_config]
            ):
                with patch(
                    "pmcp.client.manager.ClientManager.connect_all", new=AsyncMock()
                ) as mock_connect_all:
                    await run_status(status_args)

        captured = capsys.readouterr()
        assert "No MCP servers configured" in captured.out
        mock_connect_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_status_prefers_live_gateway_snapshot(
        self, status_args: argparse.Namespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Status should use live gateway health when available."""
        from pmcp.cli import run_status

        live_snapshot = {
            "revision_id": "live-rev",
            "last_refresh_ts": 1_700_000_000.0,
            "servers": [
                {"name": "context7", "status": "online", "tool_count": 2},
                {"name": "browser-use", "status": "lazy", "tool_count": 0},
            ],
            "total_tools": 2,
        }

        with patch(
            "pmcp.cli._query_running_gateway_status",
            new=AsyncMock(return_value=live_snapshot),
        ):
            with patch("pmcp.config.loader.load_configs", return_value=[]):
                await run_status(status_args)

        captured = capsys.readouterr()
        assert "PMCP Gateway: reachable" in captured.out
        assert "Downstream Server State" in captured.out
        assert "context7" in captured.out
        assert "browser-use" in captured.out
        assert "No MCP servers configured" not in captured.out

    @pytest.mark.asyncio
    async def test_status_live_snapshot_json_with_pending(
        self, status_args: argparse.Namespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """JSON status should include pending requests from live gateway."""
        import json

        from pmcp.cli import run_status

        status_args.json = True
        status_args.pending = True
        live_snapshot = {
            "revision_id": "live-rev",
            "last_refresh_ts": 1_700_000_000.0,
            "servers": [{"name": "context7", "status": "online", "tool_count": 2}],
            "total_tools": 2,
            "pending_requests": [
                {
                    "request_id": "context7::1",
                    "server_name": "context7",
                    "tool_id": "context7::search",
                    "elapsed_seconds": 1.2,
                    "state": "active",
                }
            ],
        }

        with patch(
            "pmcp.cli._query_running_gateway_status",
            new=AsyncMock(return_value=live_snapshot),
        ):
            with patch("pmcp.config.loader.load_configs", return_value=[]):
                await run_status(status_args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["revision_id"] == "live-rev"
        assert output["pending_requests"][0]["request_id"] == "context7::1"

    @pytest.mark.asyncio
    async def test_status_live_snapshot_human_with_pending_and_verbose(
        self, status_args: argparse.Namespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Human status should render live pending and lifecycle visibility."""
        from pmcp.cli import run_status

        status_args.pending = True
        status_args.verbose = True
        live_snapshot = {
            "revision_id": "live-rev",
            "last_refresh_ts": 1_700_000_000.0,
            "servers": [
                {
                    "name": "context7",
                    "status": "online",
                    "tool_count": 2,
                    "startup_policy": "eager",
                    "startup_source": "project",
                },
                {
                    "name": "browser-use",
                    "status": "lazy",
                    "tool_count": 0,
                    "startup_policy": "lazy",
                    "startup_source": "manifest",
                },
            ],
            "total_tools": 2,
            "pending_requests": [
                {
                    "request_id": "context7::1",
                    "server_name": "context7",
                    "tool_id": "context7::search",
                    "elapsed_seconds": 2.5,
                    "state": "pending",
                }
            ],
        }

        with patch(
            "pmcp.cli._query_running_gateway_status",
            new=AsyncMock(return_value=live_snapshot),
        ):
            await run_status(status_args)

        captured = capsys.readouterr()
        assert "PMCP Gateway: reachable" in captured.out
        assert "context7" in captured.out
        assert "browser-use" in captured.out
        assert "policy=eager" in captured.out
        assert "policy=lazy" in captured.out
        assert "Pending Requests (1)" in captured.out
        assert "context7::search" in captured.out
        assert "[pending]" in captured.out

    @pytest.mark.asyncio
    async def test_status_verbose_prints_live_startup_policy(
        self, status_args: argparse.Namespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Verbose live status should show startup policy details."""
        from pmcp.cli import run_status

        status_args.verbose = True
        live_snapshot = {
            "revision_id": "live-rev",
            "last_refresh_ts": 1_700_000_000.0,
            "servers": [
                {
                    "name": "context7",
                    "status": "online",
                    "tool_count": 2,
                    "startup_policy": "eager",
                    "startup_source": "project",
                },
                {
                    "name": "needs-key",
                    "status": "offline",
                    "tool_count": 0,
                    "startup_policy": "skipped",
                    "startup_source": "manifest",
                    "startup_skip_reason": "missing_auth",
                    "startup_env_var": "CONTEXT7_API_KEY",
                },
            ],
            "total_tools": 2,
        }

        with patch(
            "pmcp.cli._query_running_gateway_status",
            new=AsyncMock(return_value=live_snapshot),
        ):
            await run_status(status_args)

        captured = capsys.readouterr()
        assert "policy=eager" in captured.out
        assert "policy=skipped" in captured.out
        assert "reason=missing_auth" in captured.out
        assert "env=CONTEXT7_API_KEY" in captured.out

    @pytest.mark.asyncio
    async def test_status_json_preserves_live_startup_policy(
        self, status_args: argparse.Namespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """JSON live status should pass health fields through unchanged."""
        import json

        from pmcp.cli import run_status

        status_args.json = True
        live_snapshot = {
            "revision_id": "live-rev",
            "last_refresh_ts": 1_700_000_000.0,
            "servers": [
                {
                    "name": "needs-key",
                    "status": "offline",
                    "tool_count": 0,
                    "startup_policy": "skipped",
                    "startup_skip_reason": "missing_auth",
                    "startup_env_var": "CONTEXT7_API_KEY",
                }
            ],
            "total_tools": 0,
        }

        with patch(
            "pmcp.cli._query_running_gateway_status",
            new=AsyncMock(return_value=live_snapshot),
        ):
            await run_status(status_args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        server = output["servers"][0]
        assert server["startup_policy"] == "skipped"
        assert server["startup_skip_reason"] == "missing_auth"
        assert server["startup_env_var"] == "CONTEXT7_API_KEY"

    @pytest.mark.asyncio
    async def test_status_verbose_local_fallback_does_not_connect(
        self, status_args: argparse.Namespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Verbose local fallback should not eagerly spawn configured servers."""
        from pmcp.cli import run_status
        from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

        status_args.verbose = True
        config = ResolvedServerConfig(
            name="configured",
            source="project",
            config=LocalMcpServerConfig(command="configured-cmd"),
        )

        with patch(
            "pmcp.cli._query_running_gateway_status", new=AsyncMock(return_value=None)
        ):
            with patch("pmcp.config.loader.load_configs", return_value=[config]):
                with patch(
                    "pmcp.client.manager.ClientManager.connect_all", new=AsyncMock()
                ) as mock_connect_all:
                    await run_status(status_args)

        captured = capsys.readouterr()
        assert "configured" in captured.out
        assert "lazy" in captured.out
        mock_connect_all.assert_not_awaited()


class TestLogsCommand:
    """Tests for logs command parsing."""

    def test_logs_command(self) -> None:
        """Test logs subcommand."""
        with patch("sys.argv", ["mcp-gateway", "logs"]):
            args = parse_args()
        assert args.command == "logs"

    def test_logs_with_follow(self) -> None:
        """Test logs with follow flag."""
        with patch("sys.argv", ["mcp-gateway", "logs", "--follow"]):
            args = parse_args()
        assert args.command == "logs"
        assert args.follow is True

    def test_logs_with_tail(self) -> None:
        """Test logs with tail option."""
        with patch("sys.argv", ["mcp-gateway", "logs", "--tail", "100"]):
            args = parse_args()
        assert args.command == "logs"
        assert args.tail == 100

    def test_logs_with_level(self) -> None:
        """Test logs with level filter."""
        with patch("sys.argv", ["mcp-gateway", "logs", "--level", "error"]):
            args = parse_args()
        assert args.command == "logs"
        assert args.level == "error"

    def test_logs_with_server(self) -> None:
        """Test logs with server filter."""
        with patch("sys.argv", ["mcp-gateway", "logs", "--server", "github"]):
            args = parse_args()
        assert args.command == "logs"
        assert args.server == "github"


class TestInitCommand:
    """Tests for init command parsing."""

    def test_init_command(self) -> None:
        """Test init subcommand."""
        with patch("sys.argv", ["mcp-gateway", "init"]):
            args = parse_args()
        assert args.command == "init"

    def test_init_with_project(self, tmp_path: Path) -> None:
        """Test init with project directory."""
        with patch("sys.argv", ["mcp-gateway", "init", "--project", str(tmp_path)]):
            args = parse_args()
        assert args.command == "init"
        assert args.project == tmp_path

    def test_init_with_force(self) -> None:
        """Test init with force flag."""
        with patch("sys.argv", ["mcp-gateway", "init", "--force"]):
            args = parse_args()
        assert args.command == "init"
        assert args.force is True


class TestRunLogs:
    """Tests for run_logs function."""

    @pytest.mark.asyncio
    async def test_logs_no_file(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Test logs output when no log file exists."""
        from pmcp.cli import run_logs

        # Create args
        args = argparse.Namespace(
            command="logs",
            follow=False,
            tail=50,
            level=None,
            server=None,
        )

        # Patch LOG_FILE to non-existent path
        with patch("pmcp.cli.LOG_FILE", tmp_path / "nonexistent.log"):
            await run_logs(args)

        captured = capsys.readouterr()
        assert "No log file found" in captured.out

    @pytest.mark.asyncio
    async def test_logs_reads_file(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Test logs reads existing log file."""
        from pmcp.cli import run_logs

        # Create test log file
        log_file = tmp_path / "test.log"
        log_file.write_text(
            "[2024-01-01T00:00:00] [INFO] Test message 1\n"
            "[2024-01-01T00:00:01] [INFO] Test message 2\n"
        )

        args = argparse.Namespace(
            command="logs",
            follow=False,
            tail=50,
            level=None,
            server=None,
        )

        with patch("pmcp.cli.LOG_FILE", log_file):
            await run_logs(args)

        captured = capsys.readouterr()
        assert "Test message 1" in captured.out
        assert "Test message 2" in captured.out

    @pytest.mark.asyncio
    async def test_logs_with_level_filter(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Test logs filters by level."""
        from pmcp.cli import run_logs

        log_file = tmp_path / "test.log"
        log_file.write_text(
            "[2024-01-01T00:00:00] [INFO] Info message\n"
            "[2024-01-01T00:00:01] [ERROR] Error message\n"
            "[2024-01-01T00:00:02] [DEBUG] Debug message\n"
        )

        args = argparse.Namespace(
            command="logs",
            follow=False,
            tail=50,
            level="error",
            server=None,
        )

        with patch("pmcp.cli.LOG_FILE", log_file):
            await run_logs(args)

        captured = capsys.readouterr()
        assert "Error message" in captured.out
        assert "Info message" not in captured.out

    @pytest.mark.asyncio
    async def test_logs_with_server_filter(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Test logs filters by server name."""
        from pmcp.cli import run_logs

        log_file = tmp_path / "test.log"
        log_file.write_text(
            "[2024-01-01T00:00:00] [INFO] [github] GitHub message\n"
            "[2024-01-01T00:00:01] [INFO] [playwright] Playwright message\n"
        )

        args = argparse.Namespace(
            command="logs",
            follow=False,
            tail=50,
            level=None,
            server="github",
        )

        with patch("pmcp.cli.LOG_FILE", log_file):
            await run_logs(args)

        captured = capsys.readouterr()
        assert "GitHub message" in captured.out
        assert "Playwright message" not in captured.out

    @pytest.mark.asyncio
    async def test_logs_with_tail_limit(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Test logs respects tail limit."""
        from pmcp.cli import run_logs

        log_file = tmp_path / "test.log"
        lines = [f"[2024-01-01T00:00:{i:02d}] [INFO] Line {i}\n" for i in range(10)]
        log_file.write_text("".join(lines))

        args = argparse.Namespace(
            command="logs",
            follow=False,
            tail=3,
            level=None,
            server=None,
        )

        with patch("pmcp.cli.LOG_FILE", log_file):
            await run_logs(args)

        captured = capsys.readouterr()
        # Should only show last 3 lines
        assert "Line 7" in captured.out
        assert "Line 8" in captured.out
        assert "Line 9" in captured.out
        assert "Line 0" not in captured.out


class TestRunInit:
    """Tests for run_init function."""

    @pytest.mark.asyncio
    async def test_init_creates_config(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Test init creates .mcp.json file."""
        from pmcp.cli import run_init

        args = argparse.Namespace(
            command="init",
            project=tmp_path,
            force=False,
        )

        # Mock user input to select no servers
        with patch("builtins.input", return_value=""):
            await run_init(args)

        config_file = tmp_path / ".mcp.json"
        assert config_file.exists()

    @pytest.mark.asyncio
    async def test_init_aborts_if_exists(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Test init aborts if config already exists."""
        from pmcp.cli import run_init

        # Create existing config
        config_file = tmp_path / ".mcp.json"
        config_file.write_text('{"mcpServers": {}}')

        args = argparse.Namespace(
            command="init",
            project=tmp_path,
            force=False,
        )

        await run_init(args)

        captured = capsys.readouterr()
        assert "already exists" in captured.out

    @pytest.mark.asyncio
    async def test_init_force_overwrites(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Test init --force overwrites existing config."""
        from pmcp.cli import run_init

        # Create existing config
        config_file = tmp_path / ".mcp.json"
        config_file.write_text('{"mcpServers": {"old": {}}}')

        args = argparse.Namespace(
            command="init",
            project=tmp_path,
            force=True,
        )

        with patch("builtins.input", return_value=""):
            await run_init(args)

        # Config should be overwritten
        content = config_file.read_text()
        assert "old" not in content

    @pytest.mark.asyncio
    async def test_init_can_enable_browser_use(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Init prompt should allow enabling browser-use from manifest."""
        from pmcp.cli import run_init

        args = argparse.Namespace(
            command="init",
            project=tmp_path,
            force=False,
        )

        def _select_browser_use(prompt: str) -> str:
            if "Enable browser-use" in prompt:
                return "y"
            return ""

        with patch("builtins.input", side_effect=_select_browser_use):
            await run_init(args)

        content = (tmp_path / ".mcp.json").read_text()
        assert '"browser-use"' in content
        assert "browser-use[cli]" in content


class TestRunDoctor:
    """Tests for run_doctor function."""

    @pytest.mark.asyncio
    async def test_doctor_mode_conflict_exits(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Doctor should fail when service active with command mode config."""
        from pmcp.cli import run_doctor

        lock_file = tmp_path / ".pmcp" / "gateway.lock"
        lock_file.parent.mkdir(parents=True)
        lock_file.write_text("")

        args = argparse.Namespace(
            command="doctor", project=None, timeout=3.0, log_level="warn"
        )

        with patch("pmcp.cli.Path.home", return_value=tmp_path):
            with patch("pmcp.cli._is_pmcp_system_service_active", return_value=True):
                with patch(
                    "pmcp.cli._load_local_mcp_json",
                    return_value=(
                        tmp_path / ".mcp.json",
                        {"mcpServers": {"gateway": {"command": "pmcp", "args": []}}},
                    ),
                ):
                    with patch(
                        "pmcp.cli._probe_http_health",
                        new=AsyncMock(
                            return_value=(
                                True,
                                "http://127.0.0.1:3344/health reachable",
                            )
                        ),
                    ):
                        with pytest.raises(SystemExit) as exc_info:
                            await run_doctor(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Use remote URL instead of command" in captured.out

    @pytest.mark.asyncio
    async def test_doctor_http_probe_reachable_reports_ok(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Doctor should report a named http check when /health is reachable."""
        from pmcp.cli import run_doctor

        args = argparse.Namespace(
            command="doctor", project=None, timeout=2.0, log_level="warn"
        )

        with patch("pmcp.cli.Path.home", return_value=tmp_path):
            with patch("pmcp.cli._is_pmcp_system_service_active", return_value=False):
                with patch(
                    "pmcp.cli._load_local_mcp_json",
                    return_value=(tmp_path / ".mcp.json", {"mcpServers": {}}),
                ):
                    with patch(
                        "pmcp.cli._probe_http_health",
                        new=AsyncMock(
                            return_value=(
                                True,
                                "http://127.0.0.1:3344/health reachable",
                            )
                        ),
                    ):
                        await run_doctor(args)

        captured = capsys.readouterr()
        assert "[OK] http:" in captured.out
        assert "/health" in captured.out

    @pytest.mark.asyncio
    async def test_doctor_http_probe_unreachable_warns_without_secret_leak(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Doctor should warn on HTTP reachability without leaking URL credentials."""
        from pmcp.cli import run_doctor

        args = argparse.Namespace(
            command="doctor", project=None, timeout=2.0, log_level="warn"
        )

        with patch.dict(
            "pmcp.cli.os.environ",
            {"PMCP_GATEWAY_URL": "http://user:secret-token@127.0.0.1:3344/mcp"},
        ):
            with patch("pmcp.cli.Path.home", return_value=tmp_path):
                with patch(
                    "pmcp.cli._is_pmcp_system_service_active", return_value=False
                ):
                    with patch(
                        "pmcp.cli._load_local_mcp_json",
                        return_value=(tmp_path / ".mcp.json", {"mcpServers": {}}),
                    ):
                        with patch(
                            "pmcp.cli._probe_http_health",
                            new=AsyncMock(
                                return_value=(
                                    False,
                                    "http://127.0.0.1:3344/health "
                                    "unreachable (ConnectError)",
                                )
                            ),
                        ):
                            await run_doctor(args)

        captured = capsys.readouterr()
        assert "[WARN] http:" in captured.out
        assert "http://127.0.0.1:3344/health" in captured.out
        assert "secret-token" not in captured.out
        assert "user:" not in captured.out

    @pytest.mark.asyncio
    async def test_doctor_warns_on_missing_remote_header_env(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Doctor should warn when remote header interpolation is unresolved."""
        from pmcp.cli import run_doctor

        args = argparse.Namespace(
            command="doctor", project=None, timeout=2.0, log_level="warn"
        )

        with patch.dict("pmcp.cli_commands.doctor.os.environ", {}, clear=True):
            with patch("pmcp.cli.Path.home", return_value=tmp_path):
                with patch("pmcp.cli_commands.doctor.Path.home", return_value=tmp_path):
                    with patch(
                        "pmcp.cli._is_pmcp_system_service_active",
                        return_value=False,
                    ):
                        with patch(
                            "pmcp.cli._load_local_mcp_json",
                            return_value=(
                                tmp_path / ".mcp.json",
                                {
                                    "mcpServers": {
                                        "remote-api": {
                                            "type": "remote",
                                            "url": "https://example.com/sse",
                                            "headers": {
                                                "Authorization": "Bearer ${REMOTE_API_TOKEN}"
                                            },
                                        }
                                    }
                                },
                            ),
                        ):
                            with patch(
                                "pmcp.cli._probe_http_health",
                                new=AsyncMock(
                                    return_value=(
                                        True,
                                        "http://127.0.0.1:3344/health reachable",
                                    )
                                ),
                            ):
                                await run_doctor(args)

        captured = capsys.readouterr()
        assert "[WARN] remote:" in captured.out
        assert "REMOTE_API_TOKEN" in captured.out

    @pytest.mark.asyncio
    async def test_doctor_accepts_remote_header_env_from_pmcp_env(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """Doctor should accept ${VAR} headers resolved from user pmcp.env."""
        from pmcp.cli import run_doctor

        env_path = tmp_path / ".config" / "pmcp" / "pmcp.env"
        env_path.parent.mkdir(parents=True)
        env_path.write_text("REMOTE_API_TOKEN=test-token\n")

        args = argparse.Namespace(
            command="doctor", project=None, timeout=2.0, log_level="warn"
        )

        with patch.dict("pmcp.cli_commands.doctor.os.environ", {}, clear=True):
            with patch("pmcp.cli.Path.home", return_value=tmp_path):
                with patch("pmcp.cli_commands.doctor.Path.home", return_value=tmp_path):
                    with patch(
                        "pmcp.cli._is_pmcp_system_service_active",
                        return_value=False,
                    ):
                        with patch(
                            "pmcp.cli._load_local_mcp_json",
                            return_value=(
                                tmp_path / ".mcp.json",
                                {
                                    "mcpServers": {
                                        "remote-api": {
                                            "type": "remote",
                                            "url": "https://example.com/sse",
                                            "headers": {
                                                "Authorization": "Bearer ${REMOTE_API_TOKEN}"
                                            },
                                        }
                                    }
                                },
                            ),
                        ):
                            with patch(
                                "pmcp.cli._probe_http_health",
                                new=AsyncMock(
                                    return_value=(
                                        True,
                                        "http://127.0.0.1:3344/health reachable",
                                    )
                                ),
                            ):
                                await run_doctor(args)

        captured = capsys.readouterr()
        assert "[WARN] remote:" not in captured.out
        assert (
            "[OK] remote: No remote downstream header issues detected." in captured.out
        )


class TestDoctorAndSecretsIntegration:
    """Integration-style tests for doctor/secrets parse and dispatch."""

    def test_parse_doctor_timeout_and_project(self, tmp_path: Path) -> None:
        """Doctor parser should accept timeout and project options."""
        with patch("pmcp.cli.importlib.metadata.version", return_value="0.0.0"):
            with patch(
                "sys.argv",
                [
                    "pmcp",
                    "doctor",
                    "--project",
                    str(tmp_path),
                    "--timeout",
                    "1.25",
                ],
            ):
                args = parse_args()

        assert args.command == "doctor"
        assert args.project == tmp_path
        assert args.timeout == 1.25

    def test_parse_secrets_sync_options(self, tmp_path: Path) -> None:
        """Secrets sync parser should bind scope and project options."""
        with patch("pmcp.cli.importlib.metadata.version", return_value="0.0.0"):
            with patch(
                "sys.argv",
                [
                    "pmcp",
                    "secrets",
                    "sync",
                    "--from-scope",
                    "project",
                    "--to-scope",
                    "user",
                    "--overwrite",
                    "--project",
                    str(tmp_path),
                ],
            ):
                args = parse_args()

        assert args.command == "secrets"
        assert args.secrets_command == "sync"
        assert args.from_scope == "project"
        assert args.to_scope == "user"
        assert args.overwrite is True
        assert args.project == tmp_path

    def test_parse_auth_connect_options(self) -> None:
        """Auth connect parser should bind auth options."""
        with patch("pmcp.cli.importlib.metadata.version", return_value="0.0.0"):
            with patch(
                "sys.argv",
                [
                    "pmcp",
                    "auth",
                    "connect",
                    "browser-use",
                    "--scope",
                    "user",
                    "--env-var",
                    "OPENAI_API_KEY",
                    "--no-provision",
                    "--json",
                ],
            ):
                args = parse_args()

        assert args.command == "auth"
        assert args.auth_command == "connect"
        assert args.server_name == "browser-use"
        assert args.scope == "user"
        assert args.env_var == "OPENAI_API_KEY"
        assert args.no_provision is True
        assert args.json is True

    @pytest.mark.asyncio
    async def test_async_main_dispatches_doctor(self) -> None:
        """async_main should invoke doctor runner for doctor command."""
        args = argparse.Namespace(command="doctor")

        with patch("pmcp.cli.run_doctor", new=AsyncMock()) as mock_run_doctor:
            await async_main(args)

        mock_run_doctor.assert_awaited_once_with(args)

    @pytest.mark.asyncio
    async def test_async_main_dispatches_secrets_set_and_prints_json(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """async_main should invoke secrets.set and print returned JSON."""
        args = argparse.Namespace(command="secrets", secrets_command="set")
        payload = {"ok": True, "command": "secrets.set", "scope": "project"}

        with patch(
            "pmcp.cli.run_secrets_set", new=AsyncMock(return_value=payload)
        ) as mock_set:
            await async_main(args)

        mock_set.assert_awaited_once_with(args)
        output = capsys.readouterr().out
        assert '"command": "secrets.set"' in output
        assert '"ok": true' in output

    @pytest.mark.asyncio
    async def test_async_main_dispatches_auth_connect(self) -> None:
        """async_main should invoke auth connect runner."""
        args = argparse.Namespace(command="auth", auth_command="connect")

        with patch("pmcp.cli.run_auth_connect", new=AsyncMock()) as mock_auth:
            await async_main(args)

        mock_auth.assert_awaited_once_with(args)

    @pytest.mark.asyncio
    async def test_async_main_dispatches_update(self) -> None:
        """async_main should invoke update runner."""
        args = argparse.Namespace(command="update")

        with patch("pmcp.cli.run_update", new=AsyncMock()) as mock_update:
            await async_main(args)

        mock_update.assert_awaited_once_with(args)


class TestGatewayCliRemoteConfig:
    """Tests for CLI commands that talk to the running PMCP gateway."""

    def test_get_gateway_url_prefers_new_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PMCP_GATEWAY_URL should override the legacy SSE alias."""
        from pmcp.cli import _get_gateway_url

        monkeypatch.setenv("PMCP_STATUS_SSE_URL", "http://legacy.example/mcp")
        monkeypatch.setenv("PMCP_GATEWAY_URL", "http://gateway.example/mcp")

        assert _get_gateway_url() == "http://gateway.example/mcp"

    def test_get_gateway_health_url_derives_health_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HTTP doctor should probe /health derived from PMCP_GATEWAY_URL."""
        from pmcp.cli import _get_gateway_health_url

        monkeypatch.setenv("PMCP_GATEWAY_URL", "http://gateway.example:3344/mcp")

        assert _get_gateway_health_url() == "http://gateway.example:3344/health"

    def test_redact_url_credentials_hides_userinfo(self) -> None:
        """Printed diagnostics should not reveal URL credentials."""
        from pmcp.cli import _redact_url_credentials

        url = _redact_url_credentials("http://user:secret@gateway.example:3344/health")

        assert url == "http://gateway.example:3344/health"
        assert "secret" not in url

    @pytest.mark.asyncio
    async def test_run_update_exits_when_gateway_connect_fails(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """run_update should stop before tool calls when the gateway is unreachable."""
        from pmcp.cli import run_update

        args = argparse.Namespace(
            command="update",
            server="context7",
            all=False,
            json=False,
            policy=None,
            log_level="warn",
        )

        with patch(
            "pmcp.client.manager.ClientManager.connect_all",
            new=AsyncMock(return_value=["Failed to connect to pmcp-gateway: boom"]),
        ) as mock_connect_all:
            with patch(
                "pmcp.client.manager.ClientManager.call_tool", new=AsyncMock()
            ) as mock_call_tool:
                with patch(
                    "pmcp.client.manager.ClientManager.disconnect_all",
                    new=AsyncMock(),
                ):
                    with pytest.raises(SystemExit) as exc_info:
                        await run_update(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "cannot reach PMCP gateway" in captured.err
        gateway_config = mock_connect_all.await_args.args[0][0]
        assert gateway_config.config.type == "streamable-http"
        mock_call_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_query_running_gateway_status_returns_none_on_connect_failure(
        self,
    ) -> None:
        """Live status query failures should preserve static-config fallback."""
        from pmcp.cli import _query_running_gateway_status

        class Policy:
            def get_max_tools_per_server(self) -> int:
                return 100

        args = argparse.Namespace(server=None, pending=False)

        with patch(
            "pmcp.client.manager.ClientManager.connect_all",
            new=AsyncMock(return_value=["Failed to connect to pmcp-gateway: boom"]),
        ) as mock_connect_all:
            with patch(
                "pmcp.client.manager.ClientManager.call_tool", new=AsyncMock()
            ) as mock_call_tool:
                with patch(
                    "pmcp.client.manager.ClientManager.disconnect_all",
                    new=AsyncMock(),
                ):
                    result = await _query_running_gateway_status(args, Policy())

        assert result is None
        gateway_config = mock_connect_all.await_args.args[0][0]
        assert gateway_config.config.type == "streamable-http"
        mock_call_tool.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_auth_connect_exits_when_gateway_connect_fails(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """auth connect should use streamable HTTP and stop on connect errors."""
        from pmcp.cli import run_auth_connect

        args = argparse.Namespace(
            command="auth",
            auth_command="connect",
            server_name="browser-use",
            credential=None,
            policy=None,
            log_level="warn",
            json=False,
        )

        with patch(
            "pmcp.client.manager.ClientManager.connect_all",
            new=AsyncMock(return_value=["Failed to connect to pmcp-gateway: boom"]),
        ) as mock_connect_all:
            with patch(
                "pmcp.client.manager.ClientManager.call_tool", new=AsyncMock()
            ) as mock_call_tool:
                with patch(
                    "pmcp.client.manager.ClientManager.disconnect_all",
                    new=AsyncMock(),
                ):
                    with pytest.raises(SystemExit) as exc_info:
                        await run_auth_connect(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "cannot reach PMCP gateway" in captured.err
        gateway_config = mock_connect_all.await_args.args[0][0]
        assert gateway_config.config.type == "streamable-http"
        mock_call_tool.assert_not_awaited()


class TestRunStatusWithData:
    """Tests for run_status with actual server data."""

    @pytest.fixture
    def mock_server_status(self) -> object:
        """Create mock server status."""
        from pmcp.types import ServerStatus, ServerStatusEnum

        return ServerStatus(
            name="test-server",
            status=ServerStatusEnum.ONLINE,
            tool_count=5,
            resource_count=2,
            prompt_count=1,
        )

    @pytest.mark.asyncio
    async def test_status_shows_servers(
        self,
        capsys: pytest.CaptureFixture[str],
        mock_server_status: object,
    ) -> None:
        """Test status shows server information."""
        from pmcp.cli import run_status

        args = argparse.Namespace(
            command="status",
            json=False,
            server=None,
            pending=False,
            verbose=False,
            project=None,
            config=None,
            policy=None,
            log_level="warn",
        )

        with patch("pmcp.config.loader.load_configs", return_value=[]):
            with patch(
                "pmcp.client.ClientManager.get_all_server_statuses",
                return_value=[mock_server_status],
            ):
                with patch(
                    "pmcp.client.ClientManager.connect_all",
                    return_value=[],
                ):
                    await run_status(args)

        captured = capsys.readouterr()
        assert "test-server" in captured.out or "No MCP servers" in captured.out

    @pytest.mark.asyncio
    async def test_status_json_with_data(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Test status JSON output includes server data."""
        import json

        from pmcp.cli import run_status

        args = argparse.Namespace(
            command="status",
            json=True,
            server=None,
            pending=False,
            verbose=False,
            project=None,
            config=None,
            policy=None,
            log_level="warn",
        )

        with patch(
            "pmcp.cli._query_running_gateway_status", new=AsyncMock(return_value=None)
        ):
            with patch("pmcp.config.loader.load_configs", return_value=[]):
                await run_status(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "servers" in output
        assert "tools" in output or "total_tools" in output
