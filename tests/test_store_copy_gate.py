"""Nothing moves from a repository store into an operator's store unchecked.

Board round 5 on Consiliency/pmcp#372, claude F001: ``pmcp secrets sync
--from-scope project --to-scope user`` copied every key of a repository's
``.env.pmcp`` -- ``LD_PRELOAD``, ``Https_Proxy``, ``PMCP_AUTH_TOKEN`` -- into the
user store, which pmcp loads into its own environment at every start, from any
directory. Every path that moves values from a repository-controlled store into
an operator-trusted one (the user store, ``os.environ``) applies the rule a
lookup applies (``env_store.repository_may_supply``), and refuses a value that
holds ``${`` (the user store is loaded with expansion). ``gateway.auth_connect``,
which writes agent-supplied values into those stores, applies the same rule.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pmcp import env_store
from pmcp.cli_commands.secrets import run_secrets_sync

ALLOWED = {"BRAVE_API_KEY": "legit", "POSTGRES_URL": "postgres://x"}
REFUSED = [
    "PMCP_AUTH_TOKEN",
    "pmcp_log_level",
    "Https_Proxy",
    "all_proxy",
    "SSLKEYLOGFILE",
    "HOME",
    "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES",
    "PYTHONPATH",
    "NODE_OPTIONS",
    "PATH",
    "NPM_CONFIG_REGISTRY",
    "npm_config__auth",
    "NODE_AUTH_TOKEN",
    "COREPACK_NPM_REGISTRY",
]


@pytest.fixture
def lay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    base = Path(os.path.realpath(tmp_path))
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    project = base / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    return {"home": home, "project": project}


def _sync(project: Path, from_scope: str, to_scope: str) -> dict[str, object]:
    return asyncio.run(
        run_secrets_sync(
            argparse.Namespace(
                from_scope=from_scope,
                to_scope=to_scope,
                project=project,
                overwrite=True,
            )
        )
    )


def test_the_boards_falsifier(lay: dict[str, Path]) -> None:
    """Board 372 r5 F001, verbatim in substance (inert values)."""
    blocked = ("Https_Proxy", "PMCP_AUTH_TOKEN", "LD_PRELOAD")
    (lay["project"] / ".env.pmcp").write_text(
        "BRAVE_API_KEY=legit\n" + "".join(f"{k}=inert\n" for k in blocked)
    )
    _sync(lay["project"], "project", "user")
    user = env_store.read_env_file(env_store.resolve_scope_path("user"))
    assert sorted(k for k in blocked if k in user) == []
    assert user["BRAVE_API_KEY"] == "legit"


def test_sync_from_a_project_copies_credentials_only(
    lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    body = "".join(f"{k}={v}\n" for k, v in ALLOWED.items())
    body += "".join(f"{k}=value-372-{i}\n" for i, k in enumerate(REFUSED))
    body += "LAUNDER=${GITHUB_TOKEN}\n"
    (lay["project"] / ".env.pmcp").write_text(body)
    out = _sync(lay["project"], "project", "user")
    assert out["ok"] is True
    assert out["refused"] == sorted([*REFUSED, "LAUNDER"])
    assert sorted(out["added"]) == sorted(ALLOWED)  # type: ignore[arg-type]
    user = env_store.read_env_file(env_store.resolve_scope_path("user"))
    assert user == ALLOWED
    err = capfd.readouterr().err
    for name in [*REFUSED, "LAUNDER"]:
        assert err.count(f"pmcp: Not copying {name} from .env.pmcp:") == 1, name
    assert "value-372" not in err and "GITHUB_TOKEN" not in err


def test_a_refused_name_never_reaches_the_environment_at_the_next_start(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    (lay["project"] / ".env.pmcp").write_text(
        "LD_PRELOAD=inert\nHttps_Proxy=http://127.0.0.1:9\nBRAVE_API_KEY=legit\n"
    )
    _sync(lay["project"], "project", "user")
    elsewhere = lay["home"] / "elsewhere"
    elsewhere.mkdir()
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in ("LD_PRELOAD", "HTTPS_PROXY", "BRAVE_API_KEY")
    }
    env["HOME"] = str(lay["home"])
    probe = (
        "import json, os\n"
        "from pmcp.cli import load_startup_env\n"
        "load_startup_env(dotenv_path='/nonexistent')\n"
        "print(json.dumps([os.environ.get(k) for k in "
        "('LD_PRELOAD', 'Https_Proxy', 'BRAVE_API_KEY')]))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-600:]
    assert proc.stdout.strip().splitlines()[-1] == '[null, null, "legit"]'


def test_sync_from_the_user_store_is_unchanged(lay: dict[str, Path]) -> None:
    """The operator's own store is not filtered: it is theirs."""
    user = env_store.resolve_scope_path("user")
    user.write_text("HTTPS_PROXY=http://corp:3128\nBRAVE_API_KEY=legit\n")
    out = _sync(lay["project"], "user", "project")
    assert out["ok"] is True and out["refused"] == []
    assert env_store.read_env_file(lay["project"] / ".env.pmcp") == {
        "HTTPS_PROXY": "http://corp:3128",
        "BRAVE_API_KEY": "legit",
    }


# --------------------------------------------------------------------------- #
# gateway.auth_connect: the same rule, and no value that would expand.
# --------------------------------------------------------------------------- #


def _gateway():  # type: ignore[no-untyped-def]
    from tests.test_provision_validation import _make_gateway

    return _make_gateway()


@pytest.mark.parametrize(
    "name", ["PMCP_AUTH_TOKEN", "NODE_AUTH_TOKEN", "PMCP_FEEDBACK_TOKEN"]
)
def test_auth_connect_refuses_a_name_a_repository_may_not_supply(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway = _gateway()
    write_secret = MagicMock()
    gateway._write_secret = write_secret  # type: ignore[method-assign]
    monkeypatch.delenv(name, raising=False)
    out = asyncio.run(
        gateway.auth_connect(
            {"server_name": "whatever", "credential": "x", "env_var": name}
        )
    )
    assert not env_store.repository_may_supply(name)
    assert out.ok is False
    write_secret.assert_not_called()
    assert name not in os.environ


def test_auth_connect_refuses_a_value_that_would_expand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _gateway()
    write_secret = MagicMock()
    gateway._write_secret = write_secret  # type: ignore[method-assign]
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    out = asyncio.run(
        gateway.auth_connect(
            {
                "server_name": "brave-search",
                "credential": "${GITHUB_TOKEN}",
                "env_var": "BRAVE_API_KEY",
            }
        )
    )
    assert out.ok is False
    assert "GITHUB_TOKEN" not in out.message
    write_secret.assert_not_called()
    assert "BRAVE_API_KEY" not in os.environ


@pytest.mark.parametrize("name", REFUSED)
def test_every_refused_name_is_refused_by_the_lookup_gate_too(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One predicate: what sync refuses, credential_value never answers."""
    assert not env_store.repository_may_supply(name)
    monkeypatch.delenv(name, raising=False)
    assert env_store.credential_value(name, repository={name: "x"}) is None
