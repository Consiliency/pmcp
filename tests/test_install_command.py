"""Tests for install-method detection and the `pmcp upgrade` subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from pmcp.cli import run_upgrade
from pmcp.cli_commands import install as install_mod
from pmcp.cli_commands.install import (
    InstallDrift,
    detect_install_drift,
    detect_install_method,
)


class TestDetectInstallMethod:
    """Install-method detection heuristic."""

    def test_detects_uv_from_sys_executable(self, tmp_path: Path) -> None:
        """sys.executable under the uv tool dir => uv."""
        uv_root = tmp_path / "uv" / "tools"
        exe = uv_root / "pmcp" / "bin" / "python"
        exe.parent.mkdir(parents=True)
        exe.write_text("")

        with patch.object(install_mod, "sys") as sys_mock:
            sys_mock.executable = str(exe)
            with patch.object(install_mod, "_uv_tool_dir", return_value=uv_root):
                with patch.object(
                    install_mod, "_has_pip_user_pmcp", return_value=False
                ):
                    assert detect_install_method() == "uv"

    def test_detects_pip_when_user_site_has_pmcp(self, tmp_path: Path) -> None:
        """Non-uv sys.executable + pmcp in user-site => pip."""
        with patch.object(install_mod, "_uv_tool_dir", return_value=tmp_path / "uv"):
            with patch.object(install_mod, "_has_pip_user_pmcp", return_value=True):
                with patch.object(install_mod, "_has_uv_tool_pmcp", return_value=False):
                    assert detect_install_method() == "pip"

    def test_falls_back_to_uv_tool_dir(self, tmp_path: Path) -> None:
        """sys.executable outside uv + no pip-user but uv tool dir exists => uv."""
        with patch.object(install_mod, "_uv_tool_dir", return_value=tmp_path / "uv"):
            with patch.object(install_mod, "_has_pip_user_pmcp", return_value=False):
                with patch.object(install_mod, "_has_uv_tool_pmcp", return_value=True):
                    assert detect_install_method() == "uv"

    def test_returns_unknown_when_nothing_matches(self, tmp_path: Path) -> None:
        with patch.object(install_mod, "_uv_tool_dir", return_value=tmp_path / "uv"):
            with patch.object(install_mod, "_has_pip_user_pmcp", return_value=False):
                with patch.object(install_mod, "_has_uv_tool_pmcp", return_value=False):
                    assert detect_install_method() == "unknown"


class TestDetectInstallDrift:
    """Drift detection: both uv and pip --user have pmcp."""

    def test_drift_when_both_present(self, tmp_path: Path) -> None:
        uv_root = tmp_path / "uv" / "tools"
        (uv_root / "pmcp").mkdir(parents=True)
        user_site = tmp_path / "user_site"
        user_site.mkdir()

        with patch.object(install_mod, "_uv_tool_dir", return_value=uv_root):
            with patch.object(install_mod, "_has_pip_user_pmcp", return_value=True):
                with patch.object(install_mod, "site") as site_mock:
                    site_mock.getusersitepackages.return_value = str(user_site)
                    drift = detect_install_drift()

        assert drift.has_drift is True
        assert drift.uv_path == uv_root / "pmcp"
        assert drift.pip_user_site == user_site

    def test_no_drift_when_only_one(self, tmp_path: Path) -> None:
        uv_root = tmp_path / "uv" / "tools"
        with patch.object(install_mod, "_uv_tool_dir", return_value=uv_root):
            with patch.object(install_mod, "_has_pip_user_pmcp", return_value=False):
                drift = detect_install_drift()
        assert drift.has_drift is False


class TestUpgradeCommand:
    """`pmcp upgrade` subcommand."""

    def test_parser_defaults(self) -> None:
        from pmcp.cli import parse_args

        with patch("sys.argv", ["pmcp", "upgrade"]):
            args = parse_args()
        assert args.command == "upgrade"
        assert args.method == "auto"
        assert args.restart_service is False
        assert args.dry_run is False

    def test_parser_flags(self) -> None:
        from pmcp.cli import parse_args

        with patch(
            "sys.argv",
            ["pmcp", "upgrade", "--method", "pip", "--restart-service", "--dry-run"],
        ):
            args = parse_args()
        assert args.method == "pip"
        assert args.restart_service is True
        assert args.dry_run is True

    @pytest.mark.asyncio
    async def test_dry_run_auto_uv(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = argparse.Namespace(
            command="upgrade",
            method="auto",
            restart_service=False,
            dry_run=True,
            log_level="warn",
        )
        with patch("pmcp.cli.detect_install_method", return_value="uv"):
            await run_upgrade(args)

        out = capsys.readouterr().out
        assert "[dry-run]" in out
        assert "uv tool upgrade pmcp" in out

    @pytest.mark.asyncio
    async def test_dry_run_explicit_pip(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = argparse.Namespace(
            command="upgrade",
            method="pip",
            restart_service=False,
            dry_run=True,
            log_level="warn",
        )
        await run_upgrade(args)

        out = capsys.readouterr().out
        assert "[dry-run]" in out
        assert "pip install -U --user --break-system-packages pmcp" in out

    @pytest.mark.asyncio
    async def test_auto_detect_unknown_exits(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = argparse.Namespace(
            command="upgrade",
            method="auto",
            restart_service=False,
            dry_run=True,
            log_level="warn",
        )
        with patch("pmcp.cli.detect_install_method", return_value="unknown"):
            with pytest.raises(SystemExit) as exc_info:
                await run_upgrade(args)

        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "could not auto-detect" in err

    @pytest.mark.asyncio
    async def test_runs_subprocess_for_uv(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = argparse.Namespace(
            command="upgrade",
            method="uv",
            restart_service=False,
            dry_run=False,
            log_level="warn",
        )
        fake_result = type("R", (), {"returncode": 0})()
        no_drift = InstallDrift(has_drift=False, uv_path=None, pip_user_site=None)

        with patch("pmcp.cli.subprocess.run", return_value=fake_result) as run_mock:
            with patch("pmcp.cli.detect_install_drift", return_value=no_drift):
                await run_upgrade(args)

        cmd = run_mock.call_args.args[0]
        assert cmd == ["uv", "tool", "upgrade", "pmcp"]

    @pytest.mark.asyncio
    async def test_drift_warning_after_upgrade(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        args = argparse.Namespace(
            command="upgrade",
            method="pip",
            restart_service=False,
            dry_run=False,
            log_level="warn",
        )
        fake_result = type("R", (), {"returncode": 0})()
        drift = InstallDrift(
            has_drift=True,
            uv_path=tmp_path / "uv" / "pmcp",
            pip_user_site=tmp_path / "user_site",
        )

        with patch("pmcp.cli.subprocess.run", return_value=fake_result):
            with patch("pmcp.cli.detect_install_drift", return_value=drift):
                await run_upgrade(args)

        out = capsys.readouterr().out
        assert "BOTH uv tool and pip --user" in out
        assert "pmcp doctor" in out


class TestDoctorDriftCheck:
    """run_doctor surfaces an install-drift warning."""

    @pytest.mark.asyncio
    async def test_doctor_warns_on_drift(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        from pmcp.cli import run_doctor

        args = argparse.Namespace(
            command="doctor", project=None, timeout=2.0, log_level="warn"
        )
        drift = InstallDrift(
            has_drift=True,
            uv_path=tmp_path / "uv" / "pmcp",
            pip_user_site=tmp_path / "user_site",
        )

        with patch("pmcp.cli.Path.home", return_value=tmp_path):
            with patch("pmcp.cli._is_pmcp_system_service_active", return_value=False):
                with patch(
                    "pmcp.cli._load_local_mcp_json",
                    return_value=(tmp_path / ".mcp.json", {"mcpServers": {}}),
                ):
                    with patch("pmcp.cli.detect_install_drift", return_value=drift):
                        await run_doctor(args)

        out = capsys.readouterr().out
        assert "[WARN] install" in out
        assert "uv tool uninstall pmcp" in out

    @pytest.mark.asyncio
    async def test_doctor_ok_without_drift(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        from pmcp.cli import run_doctor

        args = argparse.Namespace(
            command="doctor", project=None, timeout=2.0, log_level="warn"
        )
        no_drift = InstallDrift(has_drift=False, uv_path=None, pip_user_site=None)

        with patch("pmcp.cli.Path.home", return_value=tmp_path):
            with patch("pmcp.cli._is_pmcp_system_service_active", return_value=False):
                with patch(
                    "pmcp.cli._load_local_mcp_json",
                    return_value=(tmp_path / ".mcp.json", {"mcpServers": {}}),
                ):
                    with patch("pmcp.cli.detect_install_drift", return_value=no_drift):
                        with patch(
                            "pmcp.cli.detect_install_method", return_value="pip"
                        ):
                            await run_doctor(args)

        out = capsys.readouterr().out
        assert "[OK] install" in out
        assert "pmcp install method: pip" in out
