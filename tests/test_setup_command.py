"""Tests for pmcp setup command."""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

_secrets_module = types.ModuleType("pmcp.cli_commands.secrets")


async def _noop_secrets(_: argparse.Namespace) -> str:
    return ""


_secrets_module.run_secrets_set = _noop_secrets
_secrets_module.run_secrets_sync = _noop_secrets
_secrets_module.run_secrets_check = _noop_secrets
sys.modules.setdefault("pmcp.cli_commands.secrets", _secrets_module)

from pmcp.cli import async_main, parse_args, run_setup  # noqa: E402


def test_parse_args_setup_command() -> None:
    """parse_args should parse setup-specific flags."""
    with patch("pmcp.cli.importlib.metadata.version", return_value="0.0.0"):
        with patch(
            "sys.argv",
            ["pmcp", "setup", "--mode", "sse", "--client", "opencode", "--write"],
        ):
            args = parse_args()

    assert args.command == "setup"
    assert args.mode == "sse"
    assert args.client == "opencode"
    assert args.write is True


def test_run_setup_renders_claude_sse_config(capsys) -> None:
    """run_setup should render Claude HTTP config to stdout (mode='sse' maps to http transport)."""
    args = argparse.Namespace(mode="sse", client="claude", write=False)

    run_setup(args)

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "mcpServers": {
            "pmcp": {
                "type": "http",
                "url": "http://127.0.0.1:3344/mcp",
            }
        }
    }


def test_run_setup_writes_and_merges_opencode_config(tmp_path: Path) -> None:
    """run_setup should merge PMCP entry into OpenCode config file."""
    config_path = tmp_path / ".config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "mcp": {
                    "existing": {
                        "type": "remote",
                        "url": "http://example.invalid/sse",
                        "enabled": False,
                    }
                },
                "theme": "light",
            }
        )
    )

    args = argparse.Namespace(mode="sse", client="opencode", write=True)

    with patch("pmcp.cli.Path.home", return_value=tmp_path):
        run_setup(args)

    merged = json.loads(config_path.read_text())
    assert merged["theme"] == "light"
    assert "existing" in merged["mcp"]
    assert merged["mcp"]["pmcp"] == {
        "type": "remote",
        "url": "http://127.0.0.1:3344/mcp",
        "enabled": True,
    }


def test_run_setup_renders_claude_stdio_config(capsys) -> None:
    """run_setup should render Claude stdio config when requested."""
    args = argparse.Namespace(mode="stdio", client="claude", write=False)

    run_setup(args)

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "mcpServers": {
            "pmcp": {
                "command": "pmcp",
                "args": [],
            }
        }
    }


@pytest.mark.asyncio
async def test_setup_command_dispatches_with_opencode_sse_write() -> None:
    """async_main should dispatch setup command with parsed OpenCode SSE args."""
    with patch("pmcp.cli.importlib.metadata.version", return_value="0.0.0"):
        with patch(
            "sys.argv",
            ["pmcp", "setup", "--client", "opencode", "--mode", "sse", "--write"],
        ):
            args = parse_args()

    assert args.command == "setup"
    assert args.client == "opencode"
    assert args.mode == "sse"
    assert args.write is True

    with patch("pmcp.cli.run_setup") as mock_run_setup:
        await async_main(args)

    mock_run_setup.assert_called_once_with(args)
