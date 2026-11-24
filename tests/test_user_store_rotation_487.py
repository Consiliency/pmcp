"""Rotation of plain user-store values without a gateway restart (Consiliency/pmcp#487 P1).

Plan section 3.6: a lookup gives the answer a freshly started gateway would
give, except for the environment it actually started with. The startup copy of
a user-store value is never consulted; the current store is. A key absent from
the store continues to the tenant and project layers and then the legacy key,
exactly as after a restart (plan section 9). A store that exists but cannot be
read is a typed ``credential_unavailable`` (``store_unreadable``) that stops the
lookup, refuses the spawn, and is contained to the entry that asked.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from pmcp import cli, env_store
from pmcp.cli_commands import secrets
from pmcp.cli_commands.doctor import collect_credential_diagnostics
from pmcp.config.loader import manifest_server_to_config
from pmcp.env_store import CredentialUnavailableError
from pmcp.manifest.installer import (
    MissingApiKeyError,
    build_install_child_env,
    check_api_key,
)
from pmcp.manifest.loader import load_manifest
from pmcp.policy.policy import PolicyManager
from pmcp.remote_auth import resolve_remote_headers_for_tenant
from pmcp.tools.handlers import GatewayTools
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig
from tests.test_tools import MockClientManager

NAMESPACED = "BRIGHTDATA_API_TOKEN"
LEGACY = "API_TOKEN"
EXA = "EXA_API_KEY"
KEYS = (
    NAMESPACED,
    LEGACY,
    EXA,
    "BRAVE_API_KEY",
    "ROT_BASE",
    "ROT_DERIVED",
    "ROT_ONLY",
)


@dataclass
class Host:
    home: Path
    root: Path
    store: Path

    def write_store(self, text: str) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(text)


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Host:
    base = Path(os.path.realpath(tmp_path))
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    root = base / "project"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(root)
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    return Host(home=home, root=root, store=home / ".config" / "pmcp" / "pmcp.env")


def _start(host: Host, *, home_dotenv: bool = False) -> None:
    """The startup load a real ``pmcp`` process runs."""
    dotenv = host.home / ".env" if home_dotenv else host.root / "no-such.env"
    cli.load_startup_env(dotenv_path=str(dotenv))


def _brightdata() -> Any:
    server = load_manifest().get_server("brightdata")
    assert server is not None
    return server


def _spawn_token(host: Host) -> str | None:
    """What a Bright Data spawn receives as API_TOKEN (the restart path)."""
    env = manifest_server_to_config(_brightdata(), host.root).config.env or {}
    return env.get(LEGACY)


def _tools(host: Host) -> GatewayTools:
    client_manager = MagicMock()
    client_manager.get_all_tools.return_value = []
    client_manager.is_server_online.return_value = False
    client_manager.get_all_server_statuses.return_value = []
    client_manager.get_server_status.return_value = None
    return GatewayTools(
        client_manager=client_manager,
        policy_manager=PolicyManager(),
        project_root=host.root,
    )


def _unreadable(host: Host) -> None:
    """A store that exists but cannot be read, as any uid sees it: a directory."""
    if host.store.exists():
        host.store.unlink()
    host.store.mkdir()


# --------------------------------------------------------------------------- #
# The fleet layout and rotation
# --------------------------------------------------------------------------- #


def test_a_store_holding_only_the_legacy_key_serves_brightdata(host: Host) -> None:
    host.write_store(f"{LEGACY}=legacy-token\n")
    _start(host)
    assert _spawn_token(host) == "legacy-token"
    assert build_install_child_env(_brightdata(), host.root)[LEGACY] == "legacy-token"


def test_the_namespaced_key_added_rotated_then_deleted(host: Host) -> None:
    host.write_store(f"{LEGACY}=legacy-token\n")
    _start(host)
    host.write_store(f"{LEGACY}=legacy-token\n{NAMESPACED}=first\n")
    assert _spawn_token(host) == "first"  # added after start
    host.write_store(f"{LEGACY}=legacy-token\n{NAMESPACED}=second\n")
    assert _spawn_token(host) == "second"  # rotated
    host.write_store(f"{LEGACY}=legacy-token\n")
    assert _spawn_token(host) == "legacy-token"  # deleted: falls to the legacy key
    host.write_store("")
    assert _spawn_token(host) is None
    with pytest.raises(MissingApiKeyError):
        asyncio.run(check_api_key(_brightdata(), host.root))


def test_a_rotation_reaches_a_key_loaded_at_startup(host: Host) -> None:
    host.write_store(f"{NAMESPACED}=at-start\n")
    _start(host)
    assert os.environ[NAMESPACED] == "at-start"  # the startup copy stays...
    assert env_store.env_origin(NAMESPACED) == "user_store"
    host.write_store(f"{NAMESPACED}=rotated\n")
    assert env_store.credential_value(NAMESPACED) == "rotated"  # ...but is not used
    assert _spawn_token(host) == "rotated"


def test_a_credential_only_in_env_pmcp_with_a_store_present(host: Host) -> None:
    host.write_store("ROT_ONLY=x\n")
    (host.root / ".env.pmcp").write_text(f"{NAMESPACED}=from-project\n")
    _start(host)
    assert _spawn_token(host) == "from-project"


def test_a_tenant_header_with_a_store_present(host: Host) -> None:
    tenant = host.root / ".pmcp" / "tenants" / "t" / "pmcp.env"
    tenant.parent.mkdir(parents=True)
    tenant.write_text("BRAVE_API_KEY=from-tenant\n")
    host.write_store("ROT_ONLY=x\n")
    _start(host)

    def header(include_process_env: bool) -> str:
        return resolve_remote_headers_for_tenant(
            {"X": "${BRAVE_API_KEY}"},
            server_name="r",
            tenant_id="t",
            project_root=host.root,
            include_process_env=include_process_env,
        ).resolved_headers["X"]

    assert header(True) == "from-tenant"  # absent from the store: continues
    host.write_store("BRAVE_API_KEY=from-store\n")
    assert header(True) == "from-store"  # the store outranks the tenant
    assert header(False) == "from-tenant"  # tenant isolation never sees it


def test_environ_false_never_reads_the_store(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_store("ROT_ONLY=from-store\n")
    _start(host)
    assert env_store.credential_value("ROT_ONLY", environ=False) is None

    def boom() -> None:
        raise AssertionError("environ=False read the user store")

    monkeypatch.setattr(env_store, "_read_user_store_live", boom)
    assert env_store.credential_value("ROT_ONLY", environ=False) is None


def test_no_store_file_continues_to_the_project(host: Host) -> None:
    host.store.unlink(missing_ok=True)
    (host.root / ".env.pmcp").write_text(f"{LEGACY}=from-project\n")
    _start(host)
    assert _spawn_token(host) == "from-project"


def test_a_store_created_after_start(host: Host) -> None:
    host.store.unlink(missing_ok=True)
    _start(host)
    assert _spawn_token(host) is None
    host.write_store(f"{NAMESPACED}=late\n")
    assert _spawn_token(host) == "late"


def test_a_symlinked_store_edited_and_its_target_replaced(host: Host) -> None:
    dotfiles = host.home / "dotfiles"
    dotfiles.mkdir()
    first = dotfiles / "pmcp.env"
    first.write_text(f"{NAMESPACED}=one\n")
    host.store.symlink_to(first)
    _start(host)
    assert _spawn_token(host) == "one"
    first.write_text(f"{NAMESPACED}=two\n")  # edited through the link
    assert _spawn_token(host) == "two"
    replacement = dotfiles / "next.env"
    replacement.write_text(f"{NAMESPACED}=three\n")
    os.replace(replacement, first)  # target replaced (new inode)
    assert _spawn_token(host) == "three"
    other = dotfiles / "other.env"
    other.write_text(f"{NAMESPACED}=four\n")
    host.store.unlink()
    host.store.symlink_to(other)  # the link re-pointed
    assert _spawn_token(host) == "four"


def test_an_unchanged_store_is_answered_from_the_cache(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_store(f"{NAMESPACED}=cached\n")
    old = time.time() - 60
    os.utime(host.store, (old, old))
    _start(host)
    assert env_store.credential_value(NAMESPACED) == "cached"

    def no_read(_path: Path) -> str:
        raise AssertionError("an unchanged store was read again")

    with monkeypatch.context() as patched:
        patched.setattr(env_store, "read_env_text", no_read)
        assert env_store.credential_value(NAMESPACED) == "cached"
    replacement = host.store.with_name("pmcp.env.new")
    replacement.write_text(f"{NAMESPACED}=changed\n")
    os.replace(replacement, host.store)
    assert env_store.credential_value(NAMESPACED) == "changed"


# --------------------------------------------------------------------------- #
# ~/.env and the external environment
# --------------------------------------------------------------------------- #


def test_a_store_value_interpolating_a_home_dotenv_value(host: Host) -> None:
    (host.home / ".env").write_text("ROT_BASE=from-home\n")
    host.write_store("ROT_DERIVED=${ROT_BASE}-suffix\n")
    _start(host, home_dotenv=True)
    assert env_store.env_origin("ROT_BASE") == "home_dotenv"
    assert os.environ["ROT_DERIVED"] == "from-home-suffix"  # startup's expansion
    assert env_store.credential_value("ROT_DERIVED") == "from-home-suffix"
    host.write_store("ROT_DERIVED=${ROT_BASE}-rotated\n")
    assert env_store.credential_value("ROT_DERIVED") == "from-home-rotated"


def test_a_stale_startup_copy_is_never_an_interpolation_source(host: Host) -> None:
    host.write_store("ROT_BASE=a\nROT_DERIVED=${ROT_BASE}\n")
    _start(host)
    host.write_store("ROT_DERIVED=${ROT_BASE}\n")  # ROT_BASE deleted
    assert os.environ["ROT_BASE"] == "a"  # the startup copy is still there
    assert env_store.credential_value("ROT_DERIVED") is None  # as after a restart


def test_a_key_only_in_home_dotenv(host: Host) -> None:
    (host.home / ".env").write_text(f"{NAMESPACED}=from-home\n")
    host.write_store("ROT_ONLY=x\n")
    _start(host, home_dotenv=True)
    assert _spawn_token(host) == "from-home"


def test_a_key_in_home_dotenv_and_the_store(host: Host) -> None:
    (host.home / ".env").write_text(f"{NAMESPACED}=from-home\n")
    host.write_store(f"{NAMESPACED}=from-store\n")
    _start(host, home_dotenv=True)
    assert _spawn_token(host) == "from-home"  # ~/.env outranks the store
    host.write_store(f"{NAMESPACED}=rotated\n")
    assert _spawn_token(host) == "from-home"  # static until restart, as today


def test_the_external_environment_wins(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(NAMESPACED, "from-shell")
    host.write_store(f"{NAMESPACED}=from-store\n")
    _start(host)
    assert env_store.env_origin(NAMESPACED) == "external"
    host.write_store(f"{NAMESPACED}=rotated\n")
    assert _spawn_token(host) == "from-shell"


def test_the_origin_registry_classes_every_startup_insertion(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROT_ONLY", "shell")
    (host.home / ".env").write_text("ROT_BASE=home\n")
    host.write_store("ROT_DERIVED=store\n")
    _start(host, home_dotenv=True)
    origins = env_store.env_origins()
    assert origins["ROT_ONLY"] == "external"
    assert origins["ROT_BASE"] == "home_dotenv"
    assert origins["ROT_DERIVED"] == "user_store"


def test_the_availability_check_no_longer_loads_the_store(host: Host) -> None:
    host.store.unlink(missing_ok=True)
    _start(host)
    host.write_store(f"{NAMESPACED}=late\n")
    tools = _tools(host)
    assert tools._check_api_key_available(NAMESPACED) is True
    assert NAMESPACED not in os.environ  # nothing planted into the environment


# --------------------------------------------------------------------------- #
# gateway.auth_connect
# --------------------------------------------------------------------------- #


def _auth_connect(host: Host, scope: str, value: str) -> Any:
    return asyncio.run(
        _tools(host).auth_connect(
            {"server_name": "brightdata", "credential": value, "scope": scope}
        )
    )


def test_user_scope_auth_connect_then_a_store_rotation(host: Host) -> None:
    _start(host)
    result = _auth_connect(host, "user", "connected")
    assert result.ok, result.message
    assert env_store.env_origin(NAMESPACED) == "user_store"
    assert _spawn_token(host) == "connected"
    host.write_store(f"{NAMESPACED}=rotated\n")
    assert _spawn_token(host) == "rotated"


def test_project_scope_auth_connect_wins_in_process(host: Host) -> None:
    host.write_store(f"{NAMESPACED}=from-store\n")
    _start(host)
    result = _auth_connect(host, "project", "from-project-connect")
    assert result.ok, result.message
    assert env_store.env_origin(NAMESPACED) == "auth_connect_project"
    assert _spawn_token(host) == "from-project-connect"
    host.write_store(f"{NAMESPACED}=rotated\n")
    assert _spawn_token(host) == "from-project-connect"  # until the next start


@pytest.mark.parametrize("scope", ["user", "project"])
def test_auth_connect_never_overwrites_an_external_value(
    host: Host, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    monkeypatch.setenv(NAMESPACED, "from-shell")
    _start(host)
    result = _auth_connect(host, scope, "connected")
    assert result.ok
    assert "takes precedence" in result.message
    assert os.environ[NAMESPACED] == "from-shell"
    assert env_store.env_origin(NAMESPACED) == "external"
    assert _spawn_token(host) == "from-shell"


def test_auth_connect_never_overwrites_a_home_dotenv_value(host: Host) -> None:
    (host.home / ".env").write_text(f"{NAMESPACED}=from-home\n")
    _start(host, home_dotenv=True)
    result = _auth_connect(host, "user", "connected")
    assert result.ok and "takes precedence" in result.message
    assert os.environ[NAMESPACED] == "from-home"
    assert _spawn_token(host) == "from-home"


# --------------------------------------------------------------------------- #
# An unreadable store: typed, stops the lookup, refuses the spawn
# --------------------------------------------------------------------------- #


def test_an_unreadable_store_is_typed_and_stops_before_the_legacy_key(
    host: Host,
) -> None:
    (host.root / ".env.pmcp").write_text(f"{LEGACY}=from-project\n")
    _start(host)
    _unreadable(host)
    with pytest.raises(CredentialUnavailableError) as raised:
        env_store.credential_value(NAMESPACED, root=host.root)
    assert raised.value.reason == "store_unreadable"
    with pytest.raises(CredentialUnavailableError):
        build_install_child_env(_brightdata(), host.root)  # spawn refused
    with pytest.raises(CredentialUnavailableError):
        asyncio.run(check_api_key(_brightdata(), host.root))
    assert (
        env_store.credential_unavailable_reason([NAMESPACED, LEGACY], root=host.root)
        == "store_unreadable"
    )


def test_an_unreadable_store_never_hides_an_external_value(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(NAMESPACED, "from-shell")
    _start(host)
    _unreadable(host)
    assert _spawn_token(host) == "from-shell"  # never consulted


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_a_mode_000_store_is_typed(host: Host) -> None:
    host.write_store(f"{NAMESPACED}=x\n")
    _start(host)
    host.store.chmod(0)
    try:
        with pytest.raises(CredentialUnavailableError):
            env_store.credential_value(NAMESPACED)
    finally:
        host.store.chmod(0o600)


def test_connect_server_reports_credential_unavailable(host: Host) -> None:
    _start(host)
    _unreadable(host)
    tools = _tools(host)
    result = asyncio.run(tools.connect_server({"server_name": "brightdata"}))
    assert result.ok is False
    assert result.auth_state == "credential_unavailable"
    assert result.credential_reason == "store_unreadable"
    assert result.missing_env_vars == []
    restarted = asyncio.run(tools.restart_server({"server_name": "brightdata"}))
    assert restarted.auth_state == "credential_unavailable"
    tools._client_manager.disconnect_server.assert_not_called()  # left running
    provisioned = asyncio.run(tools.provision({"server_name": "brightdata"}))
    assert provisioned.ok is False
    assert provisioned.auth_state == "credential_unavailable"
    assert provisioned.credential_reason == "store_unreadable"


def test_secrets_check_reports_the_typed_state(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    _start(host)
    _unreadable(host)
    monkeypatch.setattr(
        secrets,
        "_extract_required_keys",
        lambda _: (
            [NAMESPACED],
            {"brightdata": [NAMESPACED]},
            {},
            {NAMESPACED: LEGACY},
        ),
    )
    report = asyncio.run(
        secrets.run_secrets_check(argparse.Namespace(project=host.root))
    )
    assert report["ok"] is False
    assert report["unavailable_keys"] == {NAMESPACED: "store_unreadable"}
    assert report["missing_keys"] == []


# --------------------------------------------------------------------------- #
# pmcp doctor
# --------------------------------------------------------------------------- #


def _messages(host: Host) -> list[str]:
    return [m for _c, _s, m in collect_credential_diagnostics(host.root)]


def test_doctor_warns_about_an_inherited_runtime_variable(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LEGACY, "sk-doctor-SENTINEL")
    _start(host)
    messages = _messages(host)
    assert any(
        m.startswith(f"{LEGACY} (the runtime variable of") and "inherits it" in m
        for m in messages
    ), messages
    assert not any("sk-doctor-SENTINEL" in m for m in messages)


def test_doctor_is_quiet_when_the_runtime_variable_is_a_store_name(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LEGACY, "x")
    host.write_store(f"{LEGACY}=x\n")
    _start(host)
    assert not any(m.startswith(f"{LEGACY} (") for m in _messages(host))


def test_doctor_flags_a_legacy_key_next_to_its_namespaced_key(host: Host) -> None:
    host.write_store(f"{NAMESPACED}=new\n{LEGACY}=old\n")
    _start(host)
    messages = _messages(host)
    assert any(
        "a namespaced key and its legacy key are both set" in m
        and f"{NAMESPACED} in user store" in m
        and f"{LEGACY} in user store" in m
        for m in messages
    ), messages
    assert not any("new" in m.split() or "old" in m.split() for m in messages)


def test_doctor_flags_a_key_held_in_two_layers(host: Host) -> None:
    host.write_store(f"{NAMESPACED}=a\n")
    (host.root / ".env.pmcp").write_text(f"{NAMESPACED}=b\n")
    _start(host)
    assert any(
        f"{NAMESPACED} is set in more than one place (user store, project files)" in m
        for m in _messages(host)
    )


def test_doctor_lists_op_values_and_op_keys_by_server_and_source(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    config = host.home / ".mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "agent-board": {
                        "command": "op",
                        "args": ["run", "--", "board"],
                        "env": {
                            "BOARD_KEY": "op://Vault/item/secret-SENTINEL",
                            "OP_SERVICE_ACCOUNT_TOKEN": "ops_SENTINEL",
                        },
                    }
                }
            }
        )
    )
    host.write_store(f"{NAMESPACED}=op://PMCP Gateway/BrightData/SENTINEL\n")
    _start(host)
    messages = _messages(host)
    assert any(
        m.startswith("agent-board: env BOARD_KEY is an op:// value in the user config")
        for m in messages
    ), messages
    assert any(
        m.startswith("agent-board: env OP_SERVICE_ACCOUNT_TOKEN is an OP_* key")
        for m in messages
    ), messages
    assert any(
        f"credential {NAMESPACED} is an op:// reference (from user store)" in m
        for m in messages
    ), messages
    assert not any("SENTINEL" in m for m in messages)


def test_doctor_reports_an_unreadable_store(host: Host) -> None:
    _start(host)
    _unreadable(host)
    checks = collect_credential_diagnostics(host.root)
    assert any(
        status == "fail" and "cannot be read" in message
        for _c, status, message in checks
    )


# --------------------------------------------------------------------------- #
# Consiliency/pmcp#489 round 1
# --------------------------------------------------------------------------- #


def _live_tools(
    host: Host, *, config: Path | None = None
) -> tuple[GatewayTools, MockClientManager]:
    manager = MockClientManager()
    tools = GatewayTools(
        client_manager=manager,  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
        project_root=host.root,
        custom_config_path=config,
    )
    return tools, manager


def _write_config(host: Host, servers: dict[str, Any]) -> Path:
    import json

    path = host.home / "pmcp.json"
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


def _startup_config(host: Host, name: str) -> ResolvedServerConfig:
    """The config the gateway connected ``name`` with at startup."""
    server = load_manifest().get_server(name)
    assert server is not None
    return manifest_server_to_config(server, host.root)


def _no_auto_start(
    monkeypatch: pytest.MonkeyPatch, tools: GatewayTools, enabled: set[str]
) -> None:
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_enabled_auto_start", lambda **_: set(enabled)
    )
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_disabled_auto_start", lambda **_: set()
    )
    monkeypatch.setattr(tools, "_load_provisioned_registry", lambda: {})


# ASTRA-1: a configured literal credential never depends on the user store.


def test_a_configured_literal_credential_starts_with_an_unreadable_store(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.config.loader import (
        StartupSkipReason,
        load_configs,
        resolve_startup_configs,
    )

    config = _write_config(
        host,
        {
            "brightdata": {
                "command": "npx",
                "args": ["-y", "@brightdata/mcp"],
                "env": {LEGACY: "literal-token"},
            }
        },
    )
    _start(host)
    _unreadable(host)
    configs = load_configs(project_root=host.root, custom_config_path=config)
    (brightdata,) = [c for c in configs if c.name == "brightdata"]
    assert brightdata.credential_unavailable is None
    resolution = resolve_startup_configs(
        configs,
        manifest_servers=load_manifest().servers,
        enabled_auto_start={"brightdata", "exa"},
        is_auth_available=lambda key: bool(
            env_store.credential_value(key, root=host.root)
        ),
        project_root=host.root,
    )
    assert "brightdata" in {c.name for c in resolution.eager_configs}
    # A server that really depends on the store is still refused, typed.
    (exa,) = [s for s in resolution.skipped if s.name == "exa"]
    assert exa.reason == StartupSkipReason.CREDENTIAL_UNAVAILABLE
    assert exa.credential_reason == "store_unreadable"

    # refresh starts it too, and connect_server accepts it.
    tools, manager = _live_tools(host, config=config)
    _no_auto_start(monkeypatch, tools, {"brightdata"})
    assert asyncio.run(tools.refresh({"reason": "test"})).ok is True
    assert "brightdata" in {c.name for c in manager.connected_configs}
    asyncio.run(manager.disconnect_server("brightdata"))
    connected = asyncio.run(tools.connect_server({"server_name": "brightdata"}))
    assert connected.ok is True, connected


# CLAUDE-C1: pmcp status --probe never starts a credential_unavailable entry.


@pytest.mark.parametrize("probe", [True, False])
def test_status_reports_credential_unavailable_and_never_probes_it(
    capsys: pytest.CaptureFixture[str], probe: bool
) -> None:
    import json
    from unittest.mock import AsyncMock, patch

    unavailable = ResolvedServerConfig(
        name="brightdata",
        source="user",
        config=LocalMcpServerConfig(command="npx", args=["-y", "@brightdata/mcp"]),
        credential_unavailable="store_unreadable",
    )
    healthy = ResolvedServerConfig(
        name="healthy",
        source="user",
        config=LocalMcpServerConfig(command="healthy-command"),
    )
    args = argparse.Namespace(
        json=True,
        server=None,
        pending=False,
        verbose=False,
        probe=probe,
        project=None,
        config=None,
        policy=None,
        log_level="warn",
    )
    with (
        patch(
            "pmcp.cli._query_running_gateway_status", new=AsyncMock(return_value=None)
        ),
        patch("pmcp.config.loader.load_configs", return_value=[unavailable, healthy]),
        patch(
            "pmcp.client.manager.ClientManager.connect_all", new=AsyncMock()
        ) as connect_all,
        patch("pmcp.client.manager.ClientManager.disconnect_all", new=AsyncMock()),
    ):
        asyncio.run(cli.run_status(args))
    if probe:
        (probed,) = connect_all.await_args.args
        assert [c.name for c in probed] == ["healthy"]
    else:
        connect_all.assert_not_awaited()
    servers = {s["name"]: s for s in json.loads(capsys.readouterr().out)["servers"]}
    assert servers["brightdata"]["auth_state"] == "credential_unavailable"
    assert servers["brightdata"]["credential_reason"] == "store_unreadable"
    if not probe:  # a mocked connect_all registers no status for it
        assert servers["healthy"]["auth_state"] == "none"


# GEMINI-G1 / CLAUDE-C2: provision reports credential_unavailable, never spawns.


def test_provision_of_the_fleet_brightdata_reports_credential_unavailable(
    host: Host,
) -> None:
    config = _write_config(
        host, {"brightdata": {"command": "npx", "args": ["-y", "@brightdata/mcp"]}}
    )
    _start(host)
    _unreadable(host)
    tools, manager = _live_tools(host, config=config)
    result = asyncio.run(tools.provision({"server_name": "brightdata"}))
    assert result.ok is False
    assert result.auth_state == "credential_unavailable"
    assert result.credential_reason == "store_unreadable"
    assert manager.ensure_connected_calls == []


def test_provision_never_starts_a_lazily_registered_unavailable_entry(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configured entry with a relaxed credential, registered lazily before
    the store became unreadable: provision must not reach ensure_connected."""
    _start(host)
    tools, manager = _live_tools(host)
    manager.add_lazy_server("relaxed")
    entry = ResolvedServerConfig(
        name="relaxed",
        source="user",
        config=LocalMcpServerConfig(command="relaxed-command"),
        credential_unavailable="store_unreadable",
    )
    monkeypatch.setattr(tools, "_load_configured_servers", lambda: {"relaxed": entry})
    result = asyncio.run(tools.provision({"server_name": "relaxed"}))
    assert result.auth_state == "credential_unavailable"
    assert manager.ensure_connected_calls == []


def test_a_remote_configured_duplicate_reports_credential_unavailable(
    host: Host,
) -> None:
    """No manifest defaults are merged into a remote entry, so only the gate's
    own lookup sees the unreadable store: it must not answer missing_auth."""
    config = _write_config(host, {"brightdata": {"url": "https://example.invalid/mcp"}})
    _start(host)
    _unreadable(host)
    tools, manager = _live_tools(host, config=config)
    provisioned = asyncio.run(tools.provision({"server_name": "brightdata"}))
    assert provisioned.auth_state == "credential_unavailable"
    assert provisioned.credential_reason == "store_unreadable"
    connected = asyncio.run(tools.connect_server({"server_name": "brightdata"}))
    assert connected.auth_state == "credential_unavailable"
    assert connected.credential_reason == "store_unreadable"
    assert manager.ensure_connected_calls == []
    assert manager.connected_configs == []


def test_a_configured_remote_header_with_an_unreadable_store(host: Host) -> None:
    config = _write_config(
        host,
        {
            "remote": {
                "url": "https://example.invalid/mcp",
                "headers": {"Authorization": "Bearer ${ROT_ONLY}"},
            }
        },
    )
    _start(host)
    _unreadable(host)
    tools, manager = _live_tools(host, config=config)
    result = asyncio.run(tools.provision({"server_name": "remote"}))
    assert result.auth_state == "credential_unavailable"
    assert result.credential_reason == "store_unreadable"
    assert manager.ensure_connected_calls == []


# GROK-B1: a key deleted from every layer stops the running instance.

_DELETED_CASES = [
    pytest.param("brightdata", NAMESPACED, False, id="namespaced"),
    pytest.param("exa", EXA, False, id="same-name"),
    pytest.param("brightdata", NAMESPACED, True, id="configured-fleet"),
]


def _online_then_deleted(
    host: Host, name: str, key: str, configured: bool
) -> tuple[GatewayTools, MockClientManager]:
    config = (
        _write_config(
            host, {name: {"command": "npx", "args": ["-y", "@brightdata/mcp"]}}
        )
        if configured
        else None
    )
    host.write_store(f"{key}=before-deletion\n")
    _start(host)
    tools, manager = _live_tools(host, config=config)
    manager.add_connected_server(_startup_config(host, name))
    manager.register_lazy_configs([_startup_config(host, name)])
    host.write_store("ROT_ONLY=x\n")  # the key is gone from every layer
    return tools, manager


@pytest.mark.parametrize(("name", "key", "configured"), _DELETED_CASES)
@pytest.mark.parametrize("action", ["restart", "connect"])
def test_a_deleted_key_stops_the_running_instance(
    host: Host, name: str, key: str, configured: bool, action: str
) -> None:
    tools, manager = _online_then_deleted(host, name, key, configured)
    handler = tools.restart_server if action == "restart" else tools.connect_server
    result = asyncio.run(handler({"server_name": name}))
    assert result.ok is False
    assert result.auth_state == "missing_auth"
    assert not manager.is_server_online(name)
    assert result.new_status != "online"
    assert f"disconnect_server:{name}:False" in manager.events
    assert not any(
        e.startswith(("connect_server:", "restart_server:")) for e in manager.events
    )
    # Not respawned on the next tool call with the value it was started with.
    assert name not in manager.get_lazy_server_names()


@pytest.mark.parametrize(("name", "key", "configured"), _DELETED_CASES)
def test_disconnect_is_never_refused_for_a_deleted_key(
    host: Host, name: str, key: str, configured: bool
) -> None:
    tools, manager = _online_then_deleted(host, name, key, configured)
    result = asyncio.run(tools.disconnect_server({"server_name": name}))
    assert result.ok is True, result
    assert not manager.is_server_online(name)


def test_an_unreadable_store_still_leaves_the_running_instance_up(host: Host) -> None:
    host.write_store(f"{EXA}=running\n")
    _start(host)
    tools, manager = _live_tools(host)
    manager.add_connected_server(_startup_config(host, "exa"))
    _unreadable(host)
    for handler in (tools.restart_server, tools.connect_server):
        result = asyncio.run(handler({"server_name": "exa"}))
        assert result.auth_state == "credential_unavailable"
        assert manager.is_server_online("exa")
    assert not any(e.startswith("disconnect_server:") for e in manager.events)


# GROK-B2 / GEMINI-G2: refresh applies a deletion or rotation of a same-name
# store key to a lazy (not auto-started) server.


def _lazy_exa_online(
    host: Host, monkeypatch: pytest.MonkeyPatch, startup_store: str
) -> tuple[GatewayTools, MockClientManager]:
    host.write_store(startup_store)
    _start(host)
    tools, manager = _live_tools(host)
    _no_auto_start(monkeypatch, tools, set())
    manager.add_connected_server(_startup_config(host, "exa"))
    return tools, manager


def test_refresh_stops_a_lazy_server_whose_same_name_key_was_deleted(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools, manager = _lazy_exa_online(host, monkeypatch, f"{EXA}=at-start\n")
    assert os.environ[EXA] == "at-start"  # the stale startup copy
    host.write_store("ROT_ONLY=x\n")
    assert asyncio.run(tools.refresh({"reason": "test"})).ok is True
    assert "disconnect_server:exa:True" in manager.events
    assert not manager.is_server_online("exa")
    assert "exa" not in {c.name for c in manager.connected_configs}


def test_refresh_restarts_a_lazy_server_whose_same_name_key_was_rotated(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools, manager = _lazy_exa_online(host, monkeypatch, f"{EXA}=at-start\n")
    host.write_store(f"{EXA}=rotated\n")
    assert asyncio.run(tools.refresh({"reason": "test"})).ok is True
    assert "disconnect_server:exa:True" in manager.events
    (lazy,) = [c for c in manager.lazy_configs if c.name == "exa"]
    assert (lazy.config.env or {})[EXA] == "rotated"  # its next start


def test_refresh_restarts_a_server_whose_key_came_back_with_its_startup_value(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Started while the key was deleted, then restored to the value the
    gateway started with: the running child has no credential."""
    host.write_store(f"{EXA}=at-start\n")
    _start(host)
    tools, manager = _live_tools(host)
    _no_auto_start(monkeypatch, tools, set())
    host.write_store("ROT_ONLY=x\n")
    manager.add_connected_server(_startup_config(host, "exa"))  # no EXA_API_KEY
    host.write_store(f"{EXA}=at-start\n")
    assert asyncio.run(tools.refresh({"reason": "test"})).ok is True
    assert "disconnect_server:exa:True" in manager.events


def test_refresh_keeps_an_unchanged_lazy_server(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #79 guard: an unchanged store key never tears the server down."""
    tools, manager = _lazy_exa_online(host, monkeypatch, f"{EXA}=at-start\n")
    assert asyncio.run(tools.refresh({"reason": "test"})).ok is True
    assert manager.is_server_online("exa")
    assert not any(e.startswith("disconnect_server:") for e in manager.events)


# ASTRA-2 / GROK-N2: doctor's layer checks.


def test_doctor_never_reports_an_unreadable_store_as_holding_a_key(host: Host) -> None:
    (host.root / ".env.pmcp").write_text(f"{NAMESPACED}=from-project\n")
    _start(host)
    _unreadable(host)
    messages = _messages(host)
    assert any("cannot be read" in m for m in messages)
    assert not any("both set" in m for m in messages), messages
    assert not any("more than one place" in m for m in messages), messages
    assert not any("user store (unreadable)" in m for m in messages), messages


def test_doctor_names_a_tenant_store_layer(host: Host) -> None:
    tenant = host.root / ".pmcp" / "tenants" / "t1" / "pmcp.env"
    tenant.parent.mkdir(parents=True)
    tenant.write_text(f"{NAMESPACED}=from-tenant\n")
    host.write_store(f"{NAMESPACED}=from-store\n")
    _start(host)
    assert env_store.credential_layers(NAMESPACED, root=host.root) == [
        "user store",
        "tenant store",
    ]
    assert any(
        f"{NAMESPACED} is set in more than one place (user store, tenant store)" in m
        for m in _messages(host)
    )
