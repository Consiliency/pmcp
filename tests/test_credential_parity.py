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
CHECKOUT_ENV = ("set", "empty", "absent")
PROJECT = ("set", "empty", "absent")
GRID = list(itertools.product(SHELL, USER, CHECKOUT_ENV, PROJECT))
VALUES = {
    "shell": "from-shell",
    "user": "from-user",
    "dotenv": "from-dotenv",
    "project": "from-project",
}


def _winner(shell: str, user: str, dotenv: str, project: str) -> str | None:
    """The documented order, by presence: shell, user store, the checkout
    ``.env`` the startup walk found, then ``.env.pmcp``. An empty value at the
    first source that has the name is "unavailable"."""
    for source, state in (
        ("shell", shell),
        ("user", user),
        ("dotenv", dotenv),
        ("project", project),
    ):
        if state != "absent":
            return VALUES[source] if state == "set" else None
    return None


@pytest.mark.parametrize(
    ("shell", "user", "dotenv", "project"), GRID, ids=["-".join(c) for c in GRID]
)
def test_every_diagnostic_agrees_with_the_runtime(
    shell: str,
    user: str,
    dotenv: str,
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
        monkeypatch.setenv(KEY, VALUES["shell"] if shell == "set" else "")
    if user != "absent":
        (home / ".config" / "pmcp" / "pmcp.env").write_text(
            f"{KEY}={VALUES['user'] if user == 'set' else ''}\n"
        )
    if dotenv != "absent":
        # The checkout `.env` the startup walk reaches when pmcp is installed
        # in the checkout's `.venv` (codex, board round 7).
        (root / ".env").write_text(
            f"{KEY}={VALUES['dotenv'] if dotenv == 'set' else ''}\n"
        )
    if project != "absent":
        (root / ".env.pmcp").write_text(
            f"{KEY}={VALUES['project'] if project == 'set' else ''}\n"
        )
    monkeypatch.setattr(
        secrets,
        "_extract_required_keys",
        lambda _: ([KEY], {"brave-search": [KEY]}, {}, {}),
    )

    # What a real process does first: the walk finds the checkout `.env`.
    cli.load_startup_env(dotenv_path=str(root / ".env"))
    winner = _winner(shell, user, dotenv, project)
    expected = winner is not None

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


def test_a_diagnostic_without_a_startup_load_builds_the_same_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``secrets check`` in a process that never ran the startup load runs it,
    rather than reading the stores in an order of its own."""
    base = Path(os.path.realpath(tmp_path))
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    root = base / "project"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(root)
    monkeypatch.delenv(KEY, raising=False)
    monkeypatch.setattr(cli, "find_dotenv", lambda: str(root / ".env"))
    (root / ".env").write_text(f"{KEY}=\n")
    (root / ".env.pmcp").write_text(f"{KEY}=from-project\n")
    monkeypatch.setattr(
        secrets,
        "_extract_required_keys",
        lambda _: ([KEY], {"brave-search": [KEY]}, {}, {}),
    )
    report = asyncio.run(secrets.run_secrets_check(argparse.Namespace(project=root)))
    assert report["missing_keys"] == [KEY]
    assert env_store.credential_value(KEY) is None
