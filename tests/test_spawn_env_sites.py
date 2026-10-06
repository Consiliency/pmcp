"""No repository-chosen variable reaches any child pmcp spawns (Consiliency/pmcp#372 round 2).

Board round 2, claude F002: a regular project ``.env.pmcp`` set ``LD_PRELOAD``,
``NODE_OPTIONS``, ``UV_INDEX_URL``, ``PYTHONPATH``; five pmcp spawns inherited
``os.environ`` unfiltered, and the ``systemctl`` child of ``pmcp doctor`` loaded the
repository's library. A list of child-read variables can never be complete, so the
fix removes the degree of freedom: a repository file never populates
``os.environ``, and every spawn takes its ``env=`` from one builder
(``env_store.child_process_env``, or the server filters built on it).

The sites are not listed here by hand: they come from the spawn inventory in
``tests/test_store_reader_inventory.py``. Each needs a DRIVER below that reaches
it through its real function; a new spawn site with no driver fails
``test_every_spawn_site_has_a_driver``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import types
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from tests import test_store_reader_inventory as inventory
from pmcp import cli
from pmcp.client.manager import ClientManager
from pmcp.manifest import environment, installer, refresher
from pmcp.manifest.npm_resolver import NpmResolver
from pmcp.tools.handlers import GatewayTools
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

pytestmark = pytest.mark.skipif(os.name != "posix", reason="posix spawns")

PLANTED = "from-the-checkout-372"
#: What a repository would set to choose what a child loads or where it fetches.
STEERING = [
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "NODE_OPTIONS",
    "UV_INDEX_URL",
    "PIP_INDEX_URL",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "npm_config_registry",
    "GIT_SSH_COMMAND",
    "Http_Proxy",
    "SSLKEYLOGFILE",
    "STEER_372_CREDENTIAL",
]


class _Stop(BaseException):
    """Raised by a spy at the spawn: BaseException, so no ``except Exception``
    on the way swallows it before the driver sees that the spawn was reached."""


@pytest.fixture
def planted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    body = "".join(f"{k}={PLANTED}\n" for k in STEERING)
    (project / ".env.pmcp").write_text(body)
    (project / ".env").write_text(body)
    for key in STEERING:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    # Every load a running gateway does: startup (the discovered .env inside
    # the project, the user store, the cwd store) and the credential check.
    cli.load_startup_env(dotenv_path=str(project / ".env"))
    GatewayTools._check_api_key_available(object(), "UNSET_372")  # type: ignore[arg-type]
    return project


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    envs: list[Any] = []

    def _record(env: Any) -> None:
        envs.append("NO-ENV-KWARG" if env is None else dict(env))
        raise _Stop

    def _run(*args: Any, **kwargs: Any) -> Any:
        _record(kwargs.get("env"))

    def _popen(*args: Any, **kwargs: Any) -> Any:
        _record(kwargs.get("env"))

    async def _exec(*args: Any, **kwargs: Any) -> Any:
        _record(kwargs.get("env"))

    class _Params:
        def __init__(self, **kwargs: Any) -> None:
            _record(kwargs.get("env"))

    import mcp

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    monkeypatch.setattr(mcp, "StdioServerParameters", _Params)
    return envs


def _server() -> ServerConfig:
    return ServerConfig(
        name="memory",
        description="memory",
        keywords=["memory"],
        install={"linux": [sys.executable, "-c", "pass"]},
        command=sys.executable,
        args=["-c", "pass"],
        requires_api_key=False,
    )


async def _which_systemctl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    cli._is_pmcp_system_service_active()


async def _restart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        cli, "Path", lambda *a: types.SimpleNamespace(exists=lambda: True)
    )
    cli._restart_local_pmcp_service()


async def _upgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    await cli.run_upgrade(
        argparse.Namespace(log_level="warning", method="pip", dry_run=False)
    )


async def _connect_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    await ClientManager()._connect_stdio(
        ResolvedServerConfig(
            name="s372",
            source="custom",
            config=LocalMcpServerConfig(command=sys.executable, args=["-c", "pass"]),
        )
    )


async def _check_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    await environment.check_cli("python", [sys.executable, "--version"])


async def _cli_help(monkeypatch: pytest.MonkeyPatch) -> None:
    await environment.get_cli_help("python", [sys.executable, "--help"])


async def _install(monkeypatch: pytest.MonkeyPatch) -> None:
    await installer.install_server(_server(), "linux")


async def _verify(monkeypatch: pytest.MonkeyPatch) -> None:
    await installer.verify_installation(_server())


async def _start_install(monkeypatch: pytest.MonkeyPatch) -> None:
    await installer.JobManager().start_install(_server(), "linux")


async def _npm_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    NpmResolver()._spawn()


async def _refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_version(*a: Any, **k: Any) -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(refresher, "get_package_version", _no_version)
    await refresher.refresh_server(_server(), None, True)


async def _update_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = GatewayTools.__new__(GatewayTools)
    await tools._run_update_probe_command([sys.executable, "--help"], env=None)


#: (module, function) -> how to reach that spawn through its real function.
DRIVERS: dict[tuple[str, str], Callable[[pytest.MonkeyPatch], Awaitable[None]]] = {
    ("pmcp.cli", "_is_pmcp_system_service_active"): _which_systemctl,
    ("pmcp.cli", "_restart_local_pmcp_service"): _restart,
    ("pmcp.cli", "run_upgrade"): _upgrade,
    ("pmcp.client.manager", "_connect_stdio"): _connect_stdio,
    ("pmcp.manifest.environment", "check_cli"): _check_cli,
    ("pmcp.manifest.environment", "get_cli_help"): _cli_help,
    ("pmcp.manifest.installer", "install_server"): _install,
    ("pmcp.manifest.installer", "verify_installation"): _verify,
    ("pmcp.manifest.installer", "start_install"): _start_install,
    ("pmcp.manifest.npm_resolver", "_spawn"): _npm_spawn,
    ("pmcp.manifest.refresher", "refresh_server"): _refresh,
    ("pmcp.tools.handlers", "_run_update_probe_command"): _update_probe,
}

SITES = sorted(
    {
        (module, fn)
        for module, fn, _ok in inventory.spawn_sites(inventory._src_sources())
    }
)


def test_no_repository_value_is_in_the_gateway_environment(planted: Path) -> None:
    assert {k for k in STEERING if k in os.environ} == set()


def test_every_spawn_site_has_a_driver() -> None:
    assert set(SITES) == set(DRIVERS), (
        "a spawn site has no driver here (or a driver outlived its site): "
        f"{sorted(set(SITES) ^ set(DRIVERS))}"
    )


@pytest.mark.parametrize("site", SITES, ids=[f"{m}:{f}" for m, f in SITES])
def test_no_spawn_passes_a_repository_chosen_variable(
    site: tuple[str, str],
    planted: Path,
    captured: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = DRIVERS[site]
    with pytest.raises(_Stop):
        asyncio.run(driver(monkeypatch))
    assert captured, f"{site} was not reached"
    env = captured[0]
    assert env != "NO-ENV-KWARG", f"{site} spawned without env="
    leaked = {k for k, v in env.items() if v == PLANTED}
    assert leaked == set(), f"{site} passed {sorted(leaked)} to its child"
    # Not vacuous: the child gets a real environment.
    assert env.get("PATH") or env.get("HOME"), site
