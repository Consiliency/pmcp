"""EC-PKGID-4's two remaining spawn sites log a rendered argv (Consiliency/pmcp#230).

SL-3 logged the three `installer.py` spawns. But `npx -y` fetches and runs a
package when it spawns, so two more spawns are install spawns too, and they
logged no argv:

- the client manager's stdio spawn, on every connect and restart; and
- `update_server`'s probe, `npx -y <pkg>@latest --help`.

Both now log SL-3's rendered argv at WARNING, before they spawn, so a spawn that
raises still leaves its record. The client manager warns only when the
executable is a package runner: an operator's local binary server is not an
install, and must not warn on every start.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NoReturn

import pytest

from pmcp.client.manager import ClientManager
from pmcp.manifest.installer import _render_install_argv
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

MANAGER_LOGGER = "pmcp.client.manager"
HANDLERS_LOGGER = "pmcp.tools.handlers"
SECRET = "sk-live-0123456789abcdef"


class _SpawnRefused(Exception):
    """Raised by the fake spawn once it has checked the log."""


def _refusing_spawn(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    logger_name: str,
    seen: list[list[logging.LogRecord]],
) -> None:
    """Replace the spawn with one that snapshots the WARNINGs logged so far."""

    async def spawn(*args: Any, **kwargs: Any) -> NoReturn:
        seen.append(
            [
                r
                for r in caplog.records
                if r.name == logger_name and r.levelno >= logging.WARNING
            ]
        )
        raise _SpawnRefused(args)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)


def _stdio(command: str, args: list[str]) -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name="spawn-logging",
        source="manifest",
        config=LocalMcpServerConfig(command=command, args=args),
    )


# ---------------------------------------------------------------------------
# The client manager's stdio spawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "npx",
        "npx.cmd",
        "npx.exe",
        "/usr/local/bin/npx",
        "uvx",
        "pnpx",
        "bunx",
        # Windows spellings: one .exe/.cmd/.bat suffix, any case, either
        # path separator.
        "uvx.exe",
        "UVX.EXE",
        "pnpx.cmd",
        "bunx.exe",
        "npx.bat",
        "C:\\tools\\npx.cmd",
        # POSIX spellings a UNC reading of the path would lose.
        "//bin/npx",
        "///usr//bin//uvx",
        "\\\\srv\\share\\npx.exe",
        "C:/tools/npx.CMD",
        "C:npx.cmd",
        "D:uvx.exe",
    ],
)
async def test_the_stdio_spawn_of_a_package_runner_logs_its_argv_before_spawning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, command: str
) -> None:
    seen: list[list[logging.LogRecord]] = []
    _refusing_spawn(monkeypatch, caplog, MANAGER_LOGGER, seen)
    manager = ClientManager()
    argv = ["-y", "@scope/pkg@1.2.3", f"--token={SECRET}"]

    with caplog.at_level(logging.WARNING, logger=MANAGER_LOGGER):
        with pytest.raises(_SpawnRefused):
            await manager._connect_stdio(_stdio(command, argv))

    assert len(seen) == 1, "the spawn was not reached"
    messages = [r.getMessage() for r in seen[0]]
    assert len(messages) == 1, messages
    assert "spawn-logging" in messages[0]
    rendered = _render_install_argv([command, *argv])
    assert rendered.endswith(" -y @scope/pkg@1.2.3 <redacted>")
    assert messages[0].endswith(rendered)
    assert SECRET not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        "/opt/servers/local-mcp",
        "python3",
        "node",
        "npx-helper",
        "npx-helper.exe",
        "node.exe",
        "npx.cmd.exe",
    ],
)
async def test_the_stdio_spawn_of_a_local_binary_emits_no_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, command: str
) -> None:
    seen: list[list[logging.LogRecord]] = []
    _refusing_spawn(monkeypatch, caplog, MANAGER_LOGGER, seen)
    manager = ClientManager()

    with caplog.at_level(logging.INFO, logger=MANAGER_LOGGER):
        with pytest.raises(_SpawnRefused):
            await manager._connect_stdio(_stdio(command, ["server.js", SECRET]))

    assert seen == [[]]
    assert any(
        r.levelno == logging.INFO and "Connecting to MCP server" in r.getMessage()
        for r in caplog.records
    )
    assert SECRET not in caplog.text


# ---------------------------------------------------------------------------
# update_server's probe
# ---------------------------------------------------------------------------


def _gateway() -> GatewayTools:
    return GatewayTools(
        client_manager=ClientManager(), policy_manager=PolicyManager(policy_path=None)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        ["npx", "-y", "@scope/pkg@latest", "--help"],
        ["uvx", "--refresh", "mcp-server-fetch", "--help"],
        ["docker", "pull", "acme/server:latest"],
    ],
)
async def test_the_update_probe_logs_its_argv_before_spawning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    command: list[str],
) -> None:
    seen: list[list[logging.LogRecord]] = []
    _refusing_spawn(monkeypatch, caplog, HANDLERS_LOGGER, seen)

    with caplog.at_level(logging.WARNING, logger=HANDLERS_LOGGER):
        with pytest.raises(_SpawnRefused):
            await _gateway()._run_update_probe_command(command)

    assert len(seen) == 1, "the spawn was not reached"
    messages = [r.getMessage() for r in seen[0]]
    assert len(messages) == 1, messages
    # SL-3's renderer, reused rather than restated.
    assert messages[0].endswith(_render_install_argv(command))
    assert messages[0].split(": ", 1)[1].startswith(command[0] + " ")


@pytest.mark.asyncio
async def test_the_update_probe_redacts_a_credential_but_not_a_pinned_package(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    seen: list[list[logging.LogRecord]] = []
    _refusing_spawn(monkeypatch, caplog, HANDLERS_LOGGER, seen)

    with caplog.at_level(logging.WARNING, logger=HANDLERS_LOGGER):
        with pytest.raises(_SpawnRefused):
            await _gateway()._run_update_probe_command(
                ["npx", "-y", "@scope/pkg@1.2.3", "--token", SECRET]
            )

    assert len(seen) == 1
    assert "npx -y @scope/pkg@1.2.3 <redacted> <redacted>" in seen[0][0].getMessage()
    assert SECRET not in caplog.text
