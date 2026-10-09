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


# --------------------------------------------------------------------------- #
# Round 6 on Consiliency/pmcp#372: the copy rule is an ALLOWLIST.
# --------------------------------------------------------------------------- #

#: Not credentials, not on any denylist: installer, TLS, git and loader
#: configuration a repository could otherwise plant in the user store.
NOT_CREDENTIALS = [
    "UV_INDEX_URL",
    "UV_CONFIG_FILE",
    "UV_DEFAULT_INDEX",
    "UV_EXTRA_INDEX_URL",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_CONFIG_FILE",
    "PIP_TRUSTED_HOST",
    "GIT_CONFIG_GLOBAL",
    "JAVA_TOOL_OPTIONS",
    "OPENSSL_CONF",
    "GCONV_PATH",
    "GH_HOST",
    "PMCP_SOME_FUTURE_SETTING",
    "DATABASE_URL",
]


def test_the_round6_falsifier(lay: dict[str, Path]) -> None:
    """Board 372 r6 F001, in substance: a real credential still syncs."""
    names = ("UV_INDEX_URL", "PIP_INDEX_URL", "UV_CONFIG_FILE", "PIP_CONFIG_FILE")
    (lay["project"] / ".env.pmcp").write_text(
        "BRAVE_API_KEY=dummy\n" + "".join(f"{k}=dummy\n" for k in names)
    )
    _sync(lay["project"], "project", "user")
    user = env_store.read_env_file(env_store.resolve_scope_path("user"))
    assert user.get("BRAVE_API_KEY") == "dummy"
    assert sorted(k for k in names if k in user) == []


@pytest.mark.parametrize("name", NOT_CREDENTIALS)
def test_a_name_that_is_not_a_credential_is_never_copied(
    name: str, lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    (lay["project"] / ".env.pmcp").write_text(f"{name}=value-372\nGITHUB_TOKEN=t\n")
    out = _sync(lay["project"], "project", "user")
    assert out["refused"] == [name]
    user = env_store.read_env_file(env_store.resolve_scope_path("user"))
    assert user == {"GITHUB_TOKEN": "t"}
    err = capfd.readouterr().err
    assert err.count(f"pmcp: Not copying {name} from .env.pmcp:") == 1
    assert "value-372" not in err


def test_a_manifest_declared_credential_is_copied_though_not_credential_shaped(
    lay: dict[str, Path],
) -> None:
    """``POSTGRES_URL`` is the shipped postgres server's declared credential."""
    assert "POSTGRES_URL" in env_store.declared_credential_names()
    (lay["project"] / ".env.pmcp").write_text("POSTGRES_URL=postgres://x\n")
    out = _sync(lay["project"], "project", "user")
    assert out["refused"] == []
    assert env_store.read_env_file(env_store.resolve_scope_path("user")) == {
        "POSTGRES_URL": "postgres://x"
    }


def test_the_declared_arm_is_what_admits_a_non_credential_shaped_name() -> None:
    values = {"MY_SERVICE_URL": "u", "OTHER_URL": "v", "SVC_TOKEN": "t"}
    copied, refused = env_store.copyable_from_repository(
        values, ".env.pmcp", declared={"MY_SERVICE_URL"}
    )
    assert copied == {"MY_SERVICE_URL": "u", "SVC_TOKEN": "t"}
    assert refused == ["OTHER_URL"]


def test_the_denylist_stays_a_second_layer_for_copies() -> None:
    """``NODE_AUTH_TOKEN`` is credential-shaped, and still never copied."""
    copied, refused = env_store.copyable_from_repository(
        {"NODE_AUTH_TOKEN": "x", "PMCP_AUTH_TOKEN": "y"},
        ".env.pmcp",
        declared={"NODE_AUTH_TOKEN", "PMCP_AUTH_TOKEN"},
    )
    assert copied == {} and sorted(refused) == ["NODE_AUTH_TOKEN", "PMCP_AUTH_TOKEN"]


@pytest.mark.parametrize("name", ["UV_INDEX_URL", "PIP_CONFIG_FILE", "GH_HOST"])
def test_auth_connect_refuses_a_name_that_is_not_a_credential(
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
    assert out.ok is False
    write_secret.assert_not_called()
    assert name not in os.environ


def test_pmcp_upgrade_keeps_the_operators_own_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corporate mirror the operator exported still reaches ``pmcp upgrade``."""
    import argparse

    from pmcp import cli

    monkeypatch.setenv("UV_INDEX_URL", "https://mirror.corp.example/simple")
    seen: dict[str, str] = {}

    def spy(*a: object, **k: object):  # type: ignore[no-untyped-def]
        seen.update(k.get("env") or {})  # type: ignore[arg-type]
        raise FileNotFoundError

    monkeypatch.setattr(cli.subprocess, "run", spy)
    with pytest.raises(SystemExit):
        asyncio.run(
            cli.run_upgrade(
                argparse.Namespace(log_level="warning", method="uv", dry_run=False)
            )
        )
    assert seen.get("UV_INDEX_URL") == "https://mirror.corp.example/simple"


def test_a_multi_line_value_is_skipped_and_the_rest_still_sync(
    lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    (lay["project"] / ".env.pmcp").write_text(
        'MULTI_TOKEN="line one\nline two"\nGITHUB_TOKEN=t\n'
    )
    out = _sync(lay["project"], "project", "user")
    assert out["ok"] is True
    assert out["refused"] == ["MULTI_TOKEN"]
    assert env_store.read_env_file(env_store.resolve_scope_path("user")) == {
        "GITHUB_TOKEN": "t"
    }
    err = capfd.readouterr().err
    assert "pmcp: Not copying MULTI_TOKEN from .env.pmcp:" in err
    assert "line one" not in err


def test_a_credential_whose_value_would_expand_is_not_copied(
    lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    """A credential-shaped name passes the allowlist; its ``${`` value does not."""
    (lay["project"] / ".env.pmcp").write_text(
        "LEAK_TOKEN='${GITHUB_TOKEN}'\nOTHER_API_KEY=plain\n"
    )
    out = _sync(lay["project"], "project", "user")
    assert out["refused"] == ["LEAK_TOKEN"]
    assert env_store.read_env_file(env_store.resolve_scope_path("user")) == {
        "OTHER_API_KEY": "plain"
    }
    user_text = env_store.resolve_scope_path("user").read_text()
    assert "${" not in user_text and "LEAK_TOKEN" not in user_text
    assert "pmcp: Not copying LEAK_TOKEN from .env.pmcp: its value refers" in (
        capfd.readouterr().err
    )


#: The names board round 6 (grok and codex) showed reaching ``pmcp upgrade``.
UPGRADE_STEERING = [
    "PIP_INDEX_URL",
    "UV_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "PIP_TRUSTED_HOST",
    "OPENSSL_CONF",
]


def test_a_synced_project_cannot_steer_the_next_upgrade(lay: dict[str, Path]) -> None:
    """sync -> a fresh pmcp process -> the environment ``pmcp upgrade`` hands uv/pip.

    The project's installer settings are refused by the sync, so a later
    ``pmcp upgrade`` -- in a new process, from another directory -- never sees
    them, while a credential the sync did copy is still there.
    """
    import subprocess

    (lay["project"] / ".env.pmcp").write_text(
        "BRAVE_API_KEY=legit\n"
        + "".join(
            f"{k}=https://evil.invalid/{i}\n" for i, k in enumerate(UPGRADE_STEERING)
        )
    )
    out = _sync(lay["project"], "project", "user")
    assert sorted(out["refused"]) == sorted(UPGRADE_STEERING)  # type: ignore[arg-type]

    elsewhere = lay["home"] / "elsewhere"
    elsewhere.mkdir()
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in {n.upper() for n in UPGRADE_STEERING} | {"BRAVE_API_KEY"}
        and not k.startswith("PMCP")
    }
    env["HOME"] = str(lay["home"])
    probe = (
        "import argparse, asyncio, json, sys\n"
        "from pmcp import cli\n"
        "cli.load_startup_env(dotenv_path='/nonexistent')\n"
        "seen = {}\n"
        "def spy(*a, **k):\n"
        "    seen.update(k.get('env') or {})\n"
        "    raise FileNotFoundError\n"
        "cli.subprocess.run = spy\n"
        "try:\n"
        "    asyncio.run(cli.run_upgrade(argparse.Namespace(\n"
        "        log_level='warning', method='pip', dry_run=False)))\n"
        "except SystemExit:\n"
        "    pass\n"
        "keys = sys.argv[1:]\n"
        "print(json.dumps({k: seen.get(k) for k in keys}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe, *UPGRADE_STEERING, "BRAVE_API_KEY"],
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-600:]
    import json

    seen = json.loads(proc.stdout.strip().splitlines()[-1])
    assert seen == {**dict.fromkeys(UPGRADE_STEERING), "BRAVE_API_KEY": "legit"}
    assert "evil.invalid" not in proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# Round 7 N-1: the whole PMCP_ namespace, in any case, classified or not.
# --------------------------------------------------------------------------- #

UNCLASSIFIED_PMCP = ["PMCP_NEW_TOKEN", "pmcp_future_api_key", "Pmcp_Something_Secret"]


@pytest.mark.parametrize("name", UNCLASSIFIED_PMCP)
def test_an_unclassified_pmcp_name_is_never_taken_from_a_repository(
    name: str, lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.validation import is_credential_shaped

    assert not env_store.is_pmcp_environment_name(name)  # not classified (yet)
    assert is_credential_shaped(name.upper()) or name != name.upper()
    assert not env_store.repository_may_supply(name)
    # Never copied ...
    copied, refused = env_store.copyable_from_repository(
        {name: "x"}, ".env.pmcp", declared={name}
    )
    assert copied == {} and refused == [name]
    # ... never answered from a repository source ...
    monkeypatch.delenv(name, raising=False)
    assert env_store.credential_value(name, repository={name: "x"}) is None
    # ... and never accepted by auth_connect.
    gateway = _gateway()
    write_secret = MagicMock()
    gateway._write_secret = write_secret  # type: ignore[method-assign]
    out = asyncio.run(
        gateway.auth_connect(
            {"server_name": "whatever", "credential": "x", "env_var": name}
        )
    )
    assert out.ok is False
    write_secret.assert_not_called()


def test_an_operator_exported_pmcp_name_still_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prefix rule is about repository sources only."""
    monkeypatch.setenv("PMCP_NEW_TOKEN", "operator")
    assert env_store.credential_value("PMCP_NEW_TOKEN", repository={}) == "operator"
