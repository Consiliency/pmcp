"""Every diagnostic answers what the runtime will do (Consiliency/pmcp#372 round 5).

Board round 5, grok F001 / codex F001: ``pmcp secrets check`` asked the stores
only (``environ=False``), so an exported empty ``BRAVE_API_KEY`` plus a project
entry read "available" while the runtime said unavailable, and an exported key
with no store entry read "missing" while the runtime said available.

For every combination of the shell (set / empty / absent) x the user store (set /
empty / absent) x the project store (set / absent), after the same startup load a
real ``pmcp`` process does, the diagnostics -- ``pmcp secrets check``, ``pmcp
doctor``'s header lookup -- must give the verdict the runtime gives: the
credential lookup, the provision gate, the gateway's credential check and the
install child's environment.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import os
from pathlib import Path

import pytest

from pmcp import cli, env_store
from pmcp.cli_commands import secrets
from pmcp.manifest.installer import (
    MissingApiKeyError,
    build_install_child_env,
    check_api_key,
)
from pmcp.manifest.loader import load_manifest
from pmcp.remote_auth import build_remote_header_env_lookup
from pmcp.tools.handlers import GatewayTools

KEY = "BRAVE_API_KEY"
SHELL = ("set", "empty", "absent")
USER = ("set", "empty", "absent")
PROJECT = ("set", "absent")
GRID = list(itertools.product(SHELL, USER, PROJECT))


def _expected(shell: str, user: str, project: str) -> bool:
    """The documented order, by presence: shell, user store, project store."""
    if shell != "absent":
        return shell == "set"
    if user != "absent":
        return user == "set"
    return project == "set"


@pytest.mark.parametrize(
    ("shell", "user", "project"), GRID, ids=["-".join(c) for c in GRID]
)
def test_every_diagnostic_agrees_with_the_runtime(
    shell: str,
    user: str,
    project: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = Path(os.path.realpath(tmp_path))
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    root = base / "project"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(root)
    monkeypatch.delenv(KEY, raising=False)
    if shell != "absent":
        monkeypatch.setenv(KEY, "from-shell" if shell == "set" else "")
    if user != "absent":
        (home / ".config" / "pmcp" / "pmcp.env").write_text(
            f"{KEY}={'from-user' if user == 'set' else ''}\n"
        )
    if project == "set":
        (root / ".env.pmcp").write_text(f"{KEY}=from-project\n")
    monkeypatch.setattr(
        secrets,
        "_extract_required_keys",
        lambda _: ([KEY], {"brave-search": [KEY]}, {}, {}),
    )

    # What a real process does first.
    cli.load_startup_env(dotenv_path=str(base / "no-such.env"))
    expected = _expected(shell, user, project)

    # The runtime.
    server = load_manifest().get_server("brave-search")
    assert server is not None
    runtime_value = env_store.credential_value(KEY)
    try:
        asyncio.run(check_api_key(server))
        gate = True
    except MissingApiKeyError:
        gate = False
    check = GatewayTools._check_api_key_available(object(), KEY)  # type: ignore[arg-type]
    child = build_install_child_env(server, root).get(KEY)
    runtime = {
        "credential_value": bool(runtime_value),
        "provision gate": gate,
        "credential check": check,
        "install child": bool(child),
    }

    # The diagnostics.
    report = asyncio.run(secrets.run_secrets_check(argparse.Namespace(project=root)))
    diagnostics = {
        "secrets check": KEY in report["available_keys"],  # type: ignore[operator]
        "header / doctor lookup": bool(build_remote_header_env_lookup(root)(KEY)),
    }

    verdicts = {**runtime, **diagnostics}
    assert set(verdicts.values()) == {expected}, verdicts
    if expected:
        # Same value too, not just the same verdict.
        winner = {"set": "from-shell"}.get(shell) or (
            "from-user" if user == "set" else "from-project"
        )
        assert runtime_value == winner
        assert build_remote_header_env_lookup(root)(KEY) == winner
        assert child == winner


def test_the_boards_falsifier_secrets_check_honours_an_exported_empty_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Board 372 r5 (codex F001), in substance."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(KEY, "")
    (tmp_path / ".env.pmcp").write_text(f"{KEY}=repository-credential\n")
    monkeypatch.setattr(
        secrets,
        "_extract_required_keys",
        lambda _: ([KEY], {"brave-search": [KEY]}, {}, {}),
    )
    assert build_remote_header_env_lookup(tmp_path)(KEY) is None
    result = asyncio.run(
        secrets.run_secrets_check(argparse.Namespace(project=tmp_path))
    )
    assert result["missing_keys"] == [KEY]
    assert KEY not in result["available_keys"]  # type: ignore[operator]
    assert result["ok"] is False


def test_secrets_check_sees_an_exported_key_with_no_store_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(KEY, "exported")
    monkeypatch.setattr(
        secrets,
        "_extract_required_keys",
        lambda _: ([KEY], {"brave-search": [KEY]}, {}, {}),
    )
    result = asyncio.run(
        secrets.run_secrets_check(argparse.Namespace(project=tmp_path))
    )
    assert result["missing_keys"] == []
    assert result["ok"] is True
