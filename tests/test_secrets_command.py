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
from pmcp.env_store import read_env_file


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
        assert output["key"] == "OPENAI_API_KEY"
        assert output["value"] == "*******"
        assert env_path.exists()
        assert read_env_file(env_path)["OPENAI_API_KEY"] == "sk-test"
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600

    @pytest.mark.asyncio
    async def test_run_secrets_set_round_trips_shell_significant_values(
        self, tmp_path: Path
    ) -> None:
        """set preserves shell-significant credential characters."""
        credential = r'token with spaces # "quotes" and \ slash = value'
        args = argparse.Namespace(
            scope="project",
            key="OPENAI_API_KEY",
            value=credential,
            project=tmp_path,
        )

        output = await run_secrets_set(args)

        assert output["ok"] is True
        assert output["value"] == "********"
        assert read_env_file(tmp_path / ".env.pmcp")["OPENAI_API_KEY"] == credential

    @pytest.mark.asyncio
    async def test_run_secrets_set_rejects_injection_before_write(
        self, tmp_path: Path
    ) -> None:
        """set rejects invalid keys and multiline credentials before writes."""
        env_path = tmp_path / ".env.pmcp"

        with pytest.raises(ValueError):
            await run_secrets_set(
                argparse.Namespace(
                    scope="project",
                    key="GOOD=bad",
                    value="secret",
                    project=tmp_path,
                )
            )
        with pytest.raises(ValueError):
            await run_secrets_set(
                argparse.Namespace(
                    scope="project",
                    key="OPENAI_API_KEY",
                    value="first\nINJECTED=second",
                    project=tmp_path,
                )
            )

        assert not env_path.exists()

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
    async def test_run_secrets_sync_rejects_injection_before_write(
        self, tmp_path: Path
    ) -> None:
        """sync rejects multiline values before writing merged output."""
        home = tmp_path / "home"
        project = tmp_path / "project"
        user_env = home / ".config" / "pmcp" / "pmcp.env"
        project_env = project / ".env.pmcp"

        user_env.parent.mkdir(parents=True, exist_ok=True)
        project.mkdir(parents=True, exist_ok=True)
        user_env.write_text('GOOD="first\nINJECTED=second"\n')
        project_env.write_text("LOCAL=ok\n")

        with patch.dict("os.environ", {"HOME": str(home)}):
            args = argparse.Namespace(
                from_scope="user",
                to_scope="project",
                overwrite=True,
                project=project,
            )
            with pytest.raises(ValueError):
                await run_secrets_sync(args)

        assert project_env.read_text() == "LOCAL=ok\n"

    @pytest.mark.asyncio
    async def test_run_secrets_sync_rejects_invalid_keys_before_write(
        self, tmp_path: Path
    ) -> None:
        """sync validates source and target keys before writing merged output."""
        home = tmp_path / "home"
        project = tmp_path / "project"
        user_env = home / ".config" / "pmcp" / "pmcp.env"
        project_env = project / ".env.pmcp"

        user_env.parent.mkdir(parents=True, exist_ok=True)
        project.mkdir(parents=True, exist_ok=True)
        user_env.write_text("GOOD=first\n")
        project_env.write_text("BAD-NAME=local\n")

        with patch.dict("os.environ", {"HOME": str(home)}):
            args = argparse.Namespace(
                from_scope="user",
                to_scope="project",
                overwrite=True,
                project=project,
            )
            with pytest.raises(ValueError):
                await run_secrets_sync(args)

        assert project_env.read_text() == "BAD-NAME=local\n"

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

    @pytest.mark.asyncio
    async def test_run_secrets_check_includes_remote_header_placeholders(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        project = tmp_path / "project"
        user_env = home / ".config" / "pmcp" / "pmcp.env"
        project_env = project / ".env.pmcp"
        config_path = project / ".mcp.json"

        user_env.parent.mkdir(parents=True, exist_ok=True)
        project.mkdir(parents=True, exist_ok=True)
        user_env.write_text("")
        project_env.write_text("")
        config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "remote-api": {
                            "type": "remote",
                            "url": "https://example.com/sse",
                            "headers": {"Authorization": "Bearer ${REMOTE_API_TOKEN}"},
                        }
                    }
                }
            )
        )

        with patch.dict("os.environ", {"HOME": str(home)}):
            args = argparse.Namespace(project=project)
            output = await run_secrets_check(args)

        assert "REMOTE_API_TOKEN" in output["required_keys"]
        assert output["required_by_server"]["remote-api"] == ["REMOTE_API_TOKEN"]
        assert "REMOTE_API_TOKEN" in output["missing_keys"]

    @pytest.mark.asyncio
    async def test_run_secrets_check_combines_local_and_remote_auth_requirements(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        project = tmp_path / "project"
        user_env = home / ".config" / "pmcp" / "pmcp.env"
        config_path = project / ".mcp.json"

        user_env.parent.mkdir(parents=True, exist_ok=True)
        project.mkdir(parents=True, exist_ok=True)
        user_env.write_text("LOCAL_API_KEY=stored-local\n")
        config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "local-api": {
                            "command": "local-api",
                            "env": {"LOCAL_API_KEY": "${LOCAL_API_KEY}"},
                        },
                        "remote-api": {
                            "type": "streamable-http",
                            "url": "https://example.com/mcp",
                            "headers": {"Authorization": "Bearer ${REMOTE_API_TOKEN}"},
                        },
                    }
                }
            )
        )

        with patch.dict("os.environ", {"HOME": str(home)}, clear=True):
            output = await run_secrets_check(argparse.Namespace(project=project))

        assert output["required_by_server"]["local-api"] == ["LOCAL_API_KEY"]
        assert output["required_by_server"]["remote-api"] == ["REMOTE_API_TOKEN"]
        assert output["required_keys"] == ["LOCAL_API_KEY", "REMOTE_API_TOKEN"]
        assert output["available_keys"] == ["LOCAL_API_KEY"]
        assert output["missing_keys"] == ["REMOTE_API_TOKEN"]
        assert "stored-local" not in json.dumps(output)
