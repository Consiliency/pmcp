"""Tests for pmcp secrets command handlers."""

from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from pmcp.cli import parse_args
from pmcp.cli_commands.secrets import (
    run_secrets_check,
    run_secrets_set,
    run_secrets_sync,
)


class TestSecretsParseArgs:
    """Secrets CLI argument parsing."""

    def test_parse_secrets_set(self) -> None:
        """Parses secrets set command and options."""
        with patch(
            "sys.argv",
            ["pmcp", "secrets", "set", "OPENAI_API_KEY", "abc", "--scope", "user"],
        ):
            with patch("importlib.metadata.version", return_value="0.0.0"):
                args = parse_args()

        assert args.command == "secrets"
        assert args.secrets_command == "set"
        assert args.key == "OPENAI_API_KEY"
        assert args.value == "abc"
        assert args.scope == "user"


class TestSecretsHandlers:
    """Secrets handler behavior."""

    @pytest.mark.asyncio
    async def test_run_secrets_set_writes_project_env_0600(
        self, tmp_path: Path
    ) -> None:
        """set writes project env file with strict permissions."""
        args = argparse.Namespace(
            scope="project",
            key="OPENAI_API_KEY",
            value="sk-test",
            project=tmp_path,
        )

        output = await run_secrets_set(args)
        env_path = tmp_path / ".env.pmcp"

        assert output["ok"] is True
        assert output["command"] == "secrets.set"
        assert output["path"] == str(env_path)
        assert env_path.exists()
        assert "OPENAI_API_KEY=sk-test" in env_path.read_text()
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600

    @pytest.mark.asyncio
    async def test_run_secrets_sync_user_to_project(self, tmp_path: Path) -> None:
        """sync copies missing keys and skips existing by default."""
        home = tmp_path / "home"
        project = tmp_path / "project"
        user_env = home / ".config" / "pmcp" / "pmcp.env"
        project_env = project / ".env.pmcp"

        user_env.parent.mkdir(parents=True, exist_ok=True)
        project.mkdir(parents=True, exist_ok=True)
        user_env.write_text("A=1\nB=2\n")
        project_env.write_text("B=local\nC=3\n")

        with patch.dict("os.environ", {"HOME": str(home)}):
            args = argparse.Namespace(
                from_scope="user",
                to_scope="project",
                overwrite=False,
                project=project,
            )
            output = await run_secrets_sync(args)

        assert output["ok"] is True
        assert output["added"] == ["A"]
        assert output["updated"] == []
        assert "B" in output["skipped"]

        merged = project_env.read_text()
        assert "A=1" in merged
        assert "B=local" in merged
        assert "C=3" in merged
        assert stat.S_IMODE(project_env.stat().st_mode) == 0o600

    @pytest.mark.asyncio
    async def test_run_secrets_check_reports_missing_required(
        self, tmp_path: Path
    ) -> None:
        """check reports missing keys from MCP config env refs."""
        home = tmp_path / "home"
        project = tmp_path / "project"
        user_env = home / ".config" / "pmcp" / "pmcp.env"
        project_env = project / ".env.pmcp"
        config_path = project / ".mcp.json"

        user_env.parent.mkdir(parents=True, exist_ok=True)
        project.mkdir(parents=True, exist_ok=True)

        user_env.write_text("OPENAI_API_KEY=sk-test\n")
        project_env.write_text("")

        config = {
            "mcpServers": {
                "github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {
                        "OPENAI_API_KEY": "${OPENAI_API_KEY}",
                        "GITHUB_TOKEN": "$GITHUB_TOKEN",
                    },
                }
            }
        }
        config_path.write_text(json.dumps(config))

        with patch.dict("os.environ", {"HOME": str(home)}):
            args = argparse.Namespace(project=project)
            output = await run_secrets_check(args)

        assert output["command"] == "secrets.check"
        assert "OPENAI_API_KEY" in output["required_keys"]
        assert "GITHUB_TOKEN" in output["required_keys"]
        assert "OPENAI_API_KEY" not in output["missing_keys"]
        assert "GITHUB_TOKEN" in output["missing_keys"]
