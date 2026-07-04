"""P4 credential-hardening tests: lazy status and auth-aware doctor.

These live in a dedicated module (rather than test_cli.py) so the P4 changes to
`run_status` (lazy-by-default) and `run_doctor` (401/403 vs unreachable) are
pinned without disturbing the existing test_cli.py contracts.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def _status_args(**overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "json": False,
        "server": None,
        "pending": False,
        "verbose": False,
        "probe": False,
        "project": None,
        "config": None,
        "policy": None,
        "log_level": "warn",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestStatusLazyByDefault:
    """`pmcp status` with no live gateway must not connect servers by default."""

    @pytest.mark.asyncio
    async def test_status_without_probe_does_not_connect(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Default status reports a LAZY view without calling connect_all."""
        from pmcp.cli import run_status
        from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

        downstream = ResolvedServerConfig(
            name="github",
            source="user",
            config=LocalMcpServerConfig(command="npx", args=["-y", "server-github"]),
        )

        with patch(
            "pmcp.cli._query_running_gateway_status", new=AsyncMock(return_value=None)
        ):
            with patch("pmcp.config.loader.load_configs", return_value=[downstream]):
                with patch(
                    "pmcp.client.manager.ClientManager.connect_all", new=AsyncMock()
                ) as mock_connect_all:
                    await run_status(_status_args())

        mock_connect_all.assert_not_awaited()
        captured = capsys.readouterr()
        assert "lazy" in captured.out.lower()
        assert "github" in captured.out

    @pytest.mark.asyncio
    async def test_status_with_probe_connects(self) -> None:
        """--probe opts into active connection via connect_all."""
        from pmcp.cli import run_status
        from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

        downstream = ResolvedServerConfig(
            name="github",
            source="user",
            config=LocalMcpServerConfig(command="npx", args=["-y", "server-github"]),
        )

        with patch(
            "pmcp.cli._query_running_gateway_status", new=AsyncMock(return_value=None)
        ):
            with patch("pmcp.config.loader.load_configs", return_value=[downstream]):
                with patch(
                    "pmcp.client.manager.ClientManager.connect_all", new=AsyncMock()
                ) as mock_connect_all:
                    with patch(
                        "pmcp.client.manager.ClientManager.disconnect_all",
                        new=AsyncMock(),
                    ):
                        await run_status(_status_args(probe=True))

        mock_connect_all.assert_awaited_once()


class TestDoctorAuthAware:
    """doctor distinguishes an authenticating gateway from an unreachable one."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status_code", [401, 403])
    async def test_doctor_reports_auth_required(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        status_code: int,
    ) -> None:
        """A 401/403 /health response reports 'gateway up but requires auth'."""
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
                                False,
                                f"http://127.0.0.1:3344/health returned HTTP {status_code}",
                                status_code,
                            )
                        ),
                    ):
                        await run_doctor(args)

        captured = capsys.readouterr()
        assert "[WARN] http:" in captured.out
        assert "requires authentication" in captured.out
        assert str(status_code) in captured.out
        # Must NOT emit the generic unreachable guidance.
        assert "Start pmcp --transport http" not in captured.out

    @pytest.mark.asyncio
    async def test_doctor_unreachable_uses_generic_guidance(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """A truly unreachable gateway keeps the generic warning."""
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
                                False,
                                "http://127.0.0.1:3344/health unreachable (ConnectError)",
                                None,
                            )
                        ),
                    ):
                        await run_doctor(args)

        captured = capsys.readouterr()
        assert "[WARN] http:" in captured.out
        assert "requires authentication" not in captured.out
        assert "Start pmcp --transport http" in captured.out
