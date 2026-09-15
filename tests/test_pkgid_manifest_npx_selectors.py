"""packages.denylist reads npx's package selectors too (Consiliency/pmcp#230, G1).

F2 made a denylist bind manifest-backed servers by the package names their
trusted config spells out, but read only npx's positional slot. npx also
installs every package named by ``-p X`` / ``--package X`` / ``--package=X``,
any number of times, before it runs anything -- so
``npx -y -p denied-pkg some-bin`` named no package, and a denylist naming
``denied-pkg`` did not apply.

When an option ahead of the slot is one pmcp cannot place (``--registry X``,
``--call``, a selector with no value) nothing after it can be read either. That
is decided per policy: an operator with a non-empty ``packages.denylist`` is
refused, because the denylist cannot be checked; an operator without one sees
no change.

The discovered-server pin check (`_runs_exactly`) is deliberately NOT widened:
it already refuses these shapes, and a test below holds it to that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pmcp.manifest.loader import ServerConfig, load_manifest
from pmcp.manifest.package_identity import PackageIdentity
from pmcp.package_approvals import approve_package
from pmcp.provision_gate import evaluate_provision
from pmcp.validation import normalized_executable_name
from tests.test_pkgid_panel_fixes import (
    PLATFORMS,
    _gateway,
    _manifest_server,
    _policy,
)

NO_POLICY = "servers: {}\n"
UNRELATED_DENYLIST = 'packages:\n  denylist:\n    - "@nothing/*"\n'
DENIED = "packages:\n  denylist:\n    - denied-pkg\n"


async def _provision_and_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: ServerConfig, body: str
) -> tuple[Any, Any, Any, Any]:
    gateway, manager, jobs = _gateway(
        monkeypatch, _policy(tmp_path, body), manifest_servers={server.name: server}
    )
    provisioned = await gateway.provision({"server_name": server.name})
    connected = await gateway.connect_server({"server_name": server.name})
    return provisioned, connected, manager, jobs


def _assert_refused(provisioned: Any, connected: Any, manager: Any, jobs: Any) -> None:
    assert provisioned.ok is False
    assert provisioned.auth_state == "policy_denied"
    assert connected.ok is False
    assert connected.auth_state == "policy_denied"
    assert jobs.calls == []
    assert manager.connected == []


def _assert_unchanged(
    provisioned: Any, connected: Any, manager: Any, jobs: Any
) -> None:
    assert provisioned.ok is True, provisioned.message
    assert len(jobs.calls) == 1
    assert connected.ok is True, connected.message
    assert len(manager.connected) == 1


# ---------------------------------------------------------------------------
# Package selectors name packages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        ["-y", "-p", "denied-pkg", "mcp-bin"],
        ["-y", "--package", "denied-pkg", "mcp-bin"],
        ["--package=denied-pkg@1.0.0", "-y", "mcp-bin"],
        # Only the SECOND selector is denied: every one is read, not the first.
        ["-y", "-p", "ok-pkg", "-p", "denied-pkg", "mcp-bin"],
        ["-p", "ok-pkg", "--package=denied-pkg", "--yes", "mcp-bin"],
    ],
)
async def test_a_denylisted_package_named_by_a_selector_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    server = _manifest_server("selected", args=args)

    provisioned, connected, manager, jobs = await _provision_and_connect(
        tmp_path, monkeypatch, server, DENIED
    )

    _assert_refused(provisioned, connected, manager, jobs)
    assert "denied-pkg" in provisioned.message
    assert "denylist" in provisioned.message


@pytest.mark.asyncio
async def test_a_selector_in_the_install_argv_alone_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = ServerConfig(
        name="installer-only",
        description="",
        keywords=[],
        install={"linux": ["npx", "-y", "-p", "ok-pkg", "-p", "denied-pkg", "bin"]},
        command="installed-bin",
        args=[],
    )

    provisioned, connected, manager, jobs = await _provision_and_connect(
        tmp_path, monkeypatch, server, DENIED
    )

    _assert_refused(provisioned, connected, manager, jobs)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [NO_POLICY, UNRELATED_DENYLIST, DENIED])
async def test_a_selector_entry_no_denylist_names_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    # Placeable selectors are not "undetermined": an unrelated denylist, and a
    # denylist naming a package this entry does not select, change nothing.
    server = _manifest_server(
        "selected", args=["-y", "-p", "ok-pkg", "--package=other-pkg", "mcp-bin"]
    )

    provisioned, connected, manager, jobs = await _provision_and_connect(
        tmp_path, monkeypatch, server, body
    )

    _assert_unchanged(provisioned, connected, manager, jobs)
    assert jobs.calls[0][0] == [
        "npx",
        "-y",
        "-p",
        "ok-pkg",
        "--package=other-pkg",
        "mcp-bin",
    ]


@pytest.mark.asyncio
async def test_a_selector_after_the_package_slot_belongs_to_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _manifest_server("positional", args=["-y", "ok-pkg", "-p", "denied-pkg"])

    provisioned, connected, manager, jobs = await _provision_and_connect(
        tmp_path, monkeypatch, server, DENIED
    )

    _assert_unchanged(provisioned, connected, manager, jobs)


# ---------------------------------------------------------------------------
# An option pmcp cannot place: refused only under a denylist
# ---------------------------------------------------------------------------


_UNPLACEABLE = [
    ["-y", "--registry", "https://registry.example/", "denied-pkg"],
    ["--registry=https://registry.example/", "ok-pkg"],
    ["-c", "denied-pkg --help"],
    ["--call=denied-pkg"],
    ["-y", "-p"],
    ["-y", "-p", "--yes", "ok-pkg"],
    ["-y", "-p", "github:acme/tool", "mcp-bin"],
    ["-y", "./local-package"],
]


@pytest.mark.asyncio
@pytest.mark.parametrize("args", _UNPLACEABLE)
async def test_an_unplaceable_npx_option_is_refused_when_a_denylist_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    server = _manifest_server("opaque", args=args)

    provisioned, connected, manager, jobs = await _provision_and_connect(
        tmp_path, monkeypatch, server, UNRELATED_DENYLIST
    )

    _assert_refused(provisioned, connected, manager, jobs)
    assert "could not be determined" in provisioned.message
    assert "approve-package" not in provisioned.message
    assert provisioned.message.isprintable()


@pytest.mark.asyncio
@pytest.mark.parametrize("args", _UNPLACEABLE)
@pytest.mark.parametrize(
    "body", [NO_POLICY, 'packages:\n  allowlist:\n    - "@nothing/*"\n']
)
async def test_an_unplaceable_npx_option_is_unchanged_without_a_denylist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], body: str
) -> None:
    server = _manifest_server("opaque", args=args)

    provisioned, connected, manager, jobs = await _provision_and_connect(
        tmp_path, monkeypatch, server, body
    )

    _assert_unchanged(provisioned, connected, manager, jobs)


def test_either_policy_side_can_supply_the_denylist(tmp_path: Path) -> None:
    for sub in ("user", "project", "neither"):
        (tmp_path / sub).mkdir()
    user = _policy(tmp_path / "user", UNRELATED_DENYLIST)
    project = _policy(tmp_path / "project", UNRELATED_DENYLIST)
    neither = _policy(tmp_path / "neither", "packages:\n  allowlist:\n    - x\n")

    assert user.has_package_denylist() is True
    assert neither.has_package_denylist() is False
    # The project side alone: an approved project policy may add denials.
    neither._project_policy = project._policy
    assert neither.has_package_denylist() is True


def test_an_unplaceable_option_hostile_to_a_terminal_is_rendered_inert(
    tmp_path: Path,
) -> None:
    server = _manifest_server("opaque", args=["--x\x1b[2J$(id)", "pkg"])

    decision = evaluate_provision(
        server,
        None,
        source="manifest",
        policy=_policy(tmp_path, UNRELATED_DENYLIST),
    )

    assert (decision.allowed, decision.reason) == (False, "denied")
    assert decision.remedy is not None
    assert decision.remedy.isprintable()
    assert "'--x\\x1b[2J$(id)'" in decision.remedy


def test_no_shipped_manifest_entry_is_undetermined(tmp_path: Path) -> None:
    """An operator who adds any denylist keeps every shipped server."""
    policy = _policy(tmp_path, UNRELATED_DENYLIST)
    refused = {
        name: decision.remedy
        for name, server in load_manifest().servers.items()
        if not (
            decision := evaluate_provision(
                server, None, source="manifest", policy=policy
            )
        ).allowed
    }
    assert refused == {}


# ---------------------------------------------------------------------------
# Windows spellings of npx, for the manifest denylist only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("executable", "expected"),
    [
        ("npx", "npx"),
        ("/usr/local/bin/npx", "npx"),
        ("NPX.CMD", "npx"),
        ("npx.bat", "npx"),
        ("C:\\tools\\npx.cmd", "npx"),
        ("UVX.EXE", "uvx"),
        ("npx-helper.exe", "npx-helper"),
        ("npx.cmd.exe", "npx.cmd"),
        ("node.exe", "node"),
    ],
)
def test_executable_names_are_normalized(executable: str, expected: str) -> None:
    assert normalized_executable_name(executable) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["NPX.CMD", "npx.bat", "C:\\tools\\npx.cmd"])
@pytest.mark.parametrize("site", ["args", "install"])
async def test_a_windows_npx_spelling_is_read_for_the_denylist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, site: str
) -> None:
    # Each site on its own: the other one does not name the package.
    if site == "args":
        server = _manifest_server(
            "win", command=command, args=["-y", "denied-pkg"], install=["brew", "x"]
        )
    else:
        server = _manifest_server(
            "win",
            command="installed-bin",
            args=[],
            install=[command, "-y", "denied-pkg"],
        )

    provisioned, connected, manager, jobs = await _provision_and_connect(
        tmp_path, monkeypatch, server, DENIED
    )

    _assert_refused(provisioned, connected, manager, jobs)


# ---------------------------------------------------------------------------
# The discovered-server pin check stays strict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("npx", ["-p", "evil", "-y", "legit-mcp@1.0.0"]),
        ("npx", ["--package=evil", "-y", "legit-mcp@1.0.0"]),
        ("npx", ["-y", "-p", "legit-mcp@1.0.0", "legit-mcp@1.0.0"]),
        # A Windows spelling is not accepted as npx by the pin check: registration
        # only ever writes "npx", so widening it would admit a shape nothing needs.
        ("NPX.CMD", ["-y", "legit-mcp@1.0.0"]),
    ],
)
async def test_the_discovered_pin_check_still_refuses_selector_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    args: list[str],
) -> None:
    gateway, manager, jobs = _gateway(monkeypatch, _policy(tmp_path, NO_POLICY))
    identity = PackageIdentity("npm", "legit-mcp", "1.0.0", None)
    gateway._discovered_server_configs["planted"] = ServerConfig(
        name="planted",
        description="",
        keywords=[],
        install={p: [command, *args] for p in PLATFORMS},
        command=command,
        args=list(args),
    )
    gateway._discovered_server_identities["planted"] = identity
    approve_package(identity)

    provisioned = await gateway.provision({"server_name": "planted"})
    connected = await gateway.connect_server({"server_name": "planted"})

    assert provisioned.ok is False
    assert "not pinned" in provisioned.message
    assert connected.ok is False
    assert jobs.calls == []
    assert manager.connected == []
