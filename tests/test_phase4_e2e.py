"""Phase 4 end-to-end tests for setup, doctor, and secrets commands."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path


import site as _site

_SRC_DIR = str(Path(__file__).parent.parent / "src")
# User site-packages may not be loaded when HOME is overridden; pin the absolute path.
_USER_SITE = _site.getusersitepackages()


def _run_pmcp(
    args: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    """Run pmcp CLI as a subprocess and capture text output."""
    env = dict(env)
    existing = env.get("PYTHONPATH", "")
    extra = f"{_SRC_DIR}:{_USER_SITE}"
    env["PYTHONPATH"] = f"{extra}:{existing}" if existing else extra
    return subprocess.run(
        [sys.executable, "-m", "pmcp", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def test_phase4_setup_writes_opencode_sse_config(tmp_path: Path) -> None:
    """pmcp setup writes OpenCode SSE config and exits successfully."""
    home = tmp_path / "home"
    home.mkdir(parents=True)

    env = os.environ.copy()
    env["HOME"] = str(home)

    result = _run_pmcp(
        ["setup", "--client", "opencode", "--mode", "sse", "--write"],
        env=env,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert "Wrote PMCP setup to:" in result.stdout

    config_path = home / ".config" / "opencode" / "opencode.json"
    parsed = json.loads(config_path.read_text())
    assert parsed["mcp"]["pmcp"] == {
        "type": "remote",
        "url": "http://127.0.0.1:3344/mcp",
        "enabled": True,
    }


def test_phase4_doctor_handles_stale_lock_gracefully(tmp_path: Path) -> None:
    """pmcp doctor warns on lock file and keeps successful exit."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    lock_file = home / ".pmcp" / "gateway.lock"

    project.mkdir(parents=True)
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("99999")

    env = os.environ.copy()
    env["HOME"] = str(home)

    result = _run_pmcp(["doctor", "--project", str(project)], env=env, cwd=project)

    assert result.returncode == 0, result.stderr
    assert "PMCP Doctor" in result.stdout
    assert "[WARN] lock:" in result.stdout
    assert "[FAIL]" not in result.stdout


def test_phase4_doctor_warns_for_unreachable_http_health(tmp_path: Path) -> None:
    """pmcp doctor warns when gateway /health is unreachable."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir(parents=True)
    home.mkdir(parents=True)

    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PMCP_GATEWAY_URL"] = "http://127.0.0.1:9/mcp"

    result = _run_pmcp(
        ["doctor", "--project", str(project), "--timeout", "0.2"],
        env=env,
        cwd=project,
    )

    assert result.returncode == 0
    assert "[WARN] http:" in result.stdout
    assert "http://127.0.0.1:9/health" in result.stdout


def test_phase4_secrets_reports_missing_and_success_paths(tmp_path: Path) -> None:
    """pmcp secrets handles set/check/sync outcomes with stable exit codes."""
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir(parents=True)
    project.mkdir(parents=True)

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
    (project / ".mcp.json").write_text(json.dumps(config))

    env = os.environ.copy()
    env["HOME"] = str(home)

    set_result = _run_pmcp(
        [
            "secrets",
            "set",
            "OPENAI_API_KEY",
            "sk-test",
            "--scope",
            "project",
            "--project",
            str(project),
        ],
        env=env,
        cwd=project,
    )
    assert set_result.returncode == 0, set_result.stderr
    set_output = json.loads(set_result.stdout)
    assert set_output["ok"] is True
    assert set_output["command"] == "secrets.set"

    env_path = project / ".env.pmcp"
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600

    check_result = _run_pmcp(
        ["secrets", "check", "--project", str(project)],
        env=env,
        cwd=project,
    )
    assert check_result.returncode == 0, check_result.stderr
    check_output = json.loads(check_result.stdout)
    assert check_output["ok"] is False
    assert "OPENAI_API_KEY" not in check_output["missing_keys"]
    assert "GITHUB_TOKEN" in check_output["missing_keys"]

    sync_result = _run_pmcp(
        ["secrets", "sync", "--from-scope", "project", "--to-scope", "project"],
        env=env,
        cwd=project,
    )
    assert sync_result.returncode == 0, sync_result.stderr
    sync_output = json.loads(sync_result.stdout)
    assert sync_output["ok"] is False
    assert "must differ" in sync_output["error"]
