"""Version pinning, piece 1 -- pins only (Consiliency/pmcp#294, proposal 1).

A manifest entry may carry ``version:``, and an overlay may set it on an
existing (e.g. shipped) entry with ``server_version:`` -- without restating the
install matrix. ``load_manifest`` materialises the pin into the npx package
slot of ``args`` AND every ``install`` argv, keeping the package NAME from the
entry, all or nothing; a refused pin leaves the entry unchanged and logs fixed
text. ``gateway.update_server`` / ``pmcp update`` report such a server as
``[PINNED]``; every other result is reported exactly as on main.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from pmcp.config.loader import _merge_manifest_defaults
from pmcp.manifest.loader import (
    Manifest,
    ServerConfig,
    load_manifest,
    split_plain_registry_spec,
)
from pmcp.policy.policy import PolicyManager
from pmcp.provision_gate import _config_runs_exactly
from pmcp.tools import handlers as handlers_module
from pmcp.tools.handlers import GatewayTools
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

PLATFORMS = ("mac", "linux", "wsl", "windows")


@pytest.fixture(autouse=True)
def _isolate_overlays(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No ambient overlay: HOME is already isolated by conftest; add a clean cwd."""
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)


def _user_overlay(text: str) -> None:
    path = Path.home() / ".pmcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# ---------------------------------------------------------------------------
# Materialisation and refusal
# ---------------------------------------------------------------------------


def test_server_version_pins_the_shipped_firecrawl_entry_everywhere_it_spawns() -> None:
    """One overlay line pins args AND every install argv; nothing else changes."""
    shipped = load_manifest().servers["firecrawl"]
    assert shipped.version is None
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')

    pinned = load_manifest().servers["firecrawl"]

    assert pinned.version == "3.25.5"
    assert pinned.args == ["-y", "firecrawl-mcp@3.25.5"]
    assert pinned.install == {
        p: ["npx", "-y", "firecrawl-mcp@3.25.5"] for p in PLATFORMS
    }
    # The provision gate's own definition of "runs exactly this spec".
    assert _config_runs_exactly(pinned, "firecrawl-mcp@3.25.5")
    for field_name in ("command", "env_var", "api_key_optional_when", "extra_env"):
        assert getattr(pinned, field_name) == getattr(shipped, field_name)


def test_version_replaces_an_existing_tag_on_a_scoped_package() -> None:
    """``@playwright/mcp@latest`` becomes ``@playwright/mcp@1.2.3``, scope kept."""
    _user_overlay('server_version:\n  playwright: "1.2.3"\n')

    pinned = load_manifest().servers["playwright"]

    assert pinned.args == ["-y", "@playwright/mcp@1.2.3"]
    assert _config_runs_exactly(pinned, "@playwright/mcp@1.2.3")


def test_version_key_on_a_whole_servers_entry_is_materialised() -> None:
    _user_overlay(
        """
servers:
  pinned-custom:
    description: "custom"
    keywords: [c]
    install:
      linux: ["npx", "-y", "custom-mcp"]
    command: "npx"
    args: ["-y", "custom-mcp", "--port", "3000"]
    version: "2.0.0-rc.1"
"""
    )

    entry = load_manifest().servers["pinned-custom"]

    assert entry.args == ["-y", "custom-mcp@2.0.0-rc.1", "--port", "3000"]
    assert entry.install == {"linux": ["npx", "-y", "custom-mcp@2.0.0-rc.1"]}


@pytest.mark.parametrize(
    "raw",
    [
        '"^3.25.5"',  # range: re-resolves at every spawn
        '"~3.25.5"',
        '"3.x"',
        '"*"',
        '">=3.25.0"',
        '"latest"',  # dist-tag: the registry moves it
        '"next"',
        '"v3.25.5"',  # not SemVer
        "3.25",  # YAML float
        '"3.25"',
        '"3.25.5 --registry=http://evil.test"',
        '"evil-pkg@1.0.0"',  # an attempt to name a different package
        '"npm:evil-pkg@1.0.0"',
        '"../../tmp/x"',
        '""',
        "true",
        '"3.25.5+evil"',  # build metadata: npm ignores it, reports would echo it
        # SemVer-shaped tarballs: npa checks isFileType BEFORE reading a version,
        # so each of these makes npx run a LOCAL FILE (round 3, B1').
        '"3.25.5-evil.tgz"',
        '"1.0.0-x.TAR"',
        '"1.0.0-a.tar.gz"',
        '"1.2.3-X.Tar.Gz"',
        # npm 10's isFileType (npm-package-arg 12.x) has an unescaped dot in
        # `tar.gz`, so `tar-gz` is a tarball too; main's rule follows it.
        '"1.0.0-x.tar-gz"',
        # A core part above 2**53-1 is a dist-TAG to npm, not a version.
        '"9007199254740992.0.0"',
    ],
)
def test_server_version_refuses_anything_but_one_exact_version(
    raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    shipped = load_manifest().servers["firecrawl"]
    _user_overlay(f"server_version:\n  firecrawl: {raw}\n")

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["firecrawl"]

    assert entry.version is None
    assert entry.args == shipped.args
    assert entry.install == shipped.install
    assert any("firecrawl" in m and "server_version" in m for m in _warnings(caplog))


def test_server_version_cannot_create_a_server(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _user_overlay('server_version:\n  no-such-server: "1.0.0"\n')

    with caplog.at_level(logging.WARNING):
        manifest = load_manifest()

    assert "no-such-server" not in manifest.servers
    # Round 1: an overlay's own key is never shown (only shipped names are).
    assert any("does not define (an overlay server" in m for m in _warnings(caplog))
    assert not any("no-such-server" in m for m in _warnings(caplog))


def test_unapproved_project_server_version_contributes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('server_version:\n  firecrawl: "3.25.5"\n')
    monkeypatch.chdir(tmp_path / "proj")

    entry = load_manifest().servers["firecrawl"]

    assert entry.version is None
    assert entry.args == ["-y", "firecrawl-mcp"]


def test_approved_project_server_version_applies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('server_version:\n  firecrawl: "3.25.5"\n')
    approve_project_file(overlay)
    monkeypatch.chdir(tmp_path / "proj")

    assert load_manifest().servers["firecrawl"].args == ["-y", "firecrawl-mcp@3.25.5"]


def test_version_on_a_uvx_server_is_refused_with_the_escape_hatch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shipped = load_manifest().servers["fetch"]
    assert shipped.command == "uvx"
    _user_overlay('server_version:\n  fetch: "1.0.0"\n')

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["fetch"]

    assert entry.version is None
    assert entry.args == shipped.args
    assert any("npx" in m and ".mcp.json" in m for m in _warnings(caplog))


def test_version_is_refused_when_an_install_argv_names_another_package(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pinning args but not install would approve X and run latest."""
    _user_overlay(
        """
servers:
  split-brain:
    description: "args and install disagree"
    keywords: [s]
    install:
      linux: ["npx", "-y", "other-mcp"]
    command: "npx"
    args: ["-y", "split-mcp"]
server_version:
  split-brain: "1.0.0"
"""
    )

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["split-brain"]

    assert entry.version is None
    assert entry.args == ["-y", "split-mcp"]
    assert entry.install == {"linux": ["npx", "-y", "other-mcp"]}


# One case per npm-package-arg class that is NOT a registry version/tag
# (the plan's class table). Round 2 (B1) found the tarball class missing: every
# earlier file/URL case carried a `:` prefix, which the old regex excluded
# anyway, so no test exercised a letter-led tarball.
_NON_PLAIN_SLOTS = {
    "alias": "myalias@npm:firecrawl-mcp@3.25.5",
    "url": "t@https://evil.test/t.tgz",
    "git-url": "t@git+https://evil.test/t.git",
    "hosted-shortcut": "t@github:evil/x",
    "hosted-path": "t@evil/x",
    "git-ssh": "t@git@github.com:evil/x",
    "file-prefix": "t@file:../evil",
    "relative-dir": "t@../evil",
    "home-dir": "t@~/evil",
    "absolute-dir": "t@/abs/evil",
    "tarball-tgz": "t@corp.tgz",
    "tarball-TGZ-mixed": "firecrawl-mcp@corp-mcp.TGZ",
    "tarball-tar": "t@x.tar",
    "tarball-tar.gz": "t@x.tar.gz",
    "tarball-Tar.Gz": "@s/p@X.Tar.Gz",
    "bare-tarball-tgz": "corp.tgz",
    "bare-tarball-TAR": "x.TAR",
    "tarball-name-with-version": "corp.tgz@3.25.5",
    "range-caret": "t@^3.25.0",
    "range-gte": "t@>=1.0.0",
    "range-x": "t@x",
    "range-X": "t@X",
    "range-v-partial": "t@v1.2.x",
    "tag-trailing-newline": "t@latest\n",
    "not-uri-safe-tag": "t@tag!",
    # Round 3 (B1'): a strict-SemVer selector with a tarball tail is a FILE to
    # npa, which checks isFileType before it reads a version. A fixed,
    # node-free sample of the generated corpus's classes (Verification step 10).
    "semver-tarball-prerelease": "firecrawl-mcp@3.25.5-corp.tgz",
    "semver-tarball-TAR": "t@1.0.0-x.TAR",
    "semver-tarball-build": "t@3.25.5+b.tar.gz",
    "semver-tarball-scoped": "@s/p@1.2.3-X.Tar.Gz",
    "semver-tarball-npm10-tar-gz": "t@1.0.0-x.tar-gz",
    "tarball-npm10-tar-gz": "t@corp.tar-gz",
    "oversized-core-is-a-tag": "t@9007199254740992.0.0",
    # Round 3 (N-a): loose SemVer allows any run of leading v's.
    "range-vv": "t@vv1",
    "range-vvX": "t@vvX",
    "range-v-xbeta": "t@v1.X.xbeta",
    "version-vv": "t@vv1.2.3",
    # Round 3 (N-c): validate-npm-package-name's exclusion list, any case.
    "excluded-node_modules": "node_modules",
    "excluded-Node_Modules-versioned": "Node_Modules@1.0.0",
    "excluded-favicon": "favicon.ico@latest",
}


def _odd_slot_overlay(slot: str, version: str = "3.25.5") -> None:
    _user_overlay(
        yaml.safe_dump(
            {
                "servers": {
                    "odd-slot": {
                        "description": "odd slot",
                        "keywords": ["o"],
                        "install": {"linux": ["npx", "-y", slot]},
                        "command": "npx",
                        "args": ["-y", slot],
                        "version": version,
                    }
                }
            }
        )
    )


@pytest.mark.parametrize(
    "slot", list(_NON_PLAIN_SLOTS.values()), ids=list(_NON_PLAIN_SLOTS)
)
def test_version_refuses_a_slot_that_is_not_a_plain_registry_spec(
    slot: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Only ``name`` or ``name@<version-or-tag>`` may have its version replaced."""
    _odd_slot_overlay(slot)

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["odd-slot"]

    assert split_plain_registry_spec(slot) is None
    assert entry.version is None
    assert entry.args == ["-y", slot]
    assert entry.install == {"linux": ["npx", "-y", slot]}
    assert any("plain registry" in m for m in _warnings(caplog))


@pytest.mark.parametrize(
    ("slot", "pinned"),
    [
        ("t", "t@3.25.5"),  # npa: registry range `*`
        ("t@1.0.0", "t@3.25.5"),  # npa: version
        ("t@1.0.0-rc.1", "t@3.25.5"),
        ("t@next", "t@3.25.5"),  # npa: tag
        ("t@beta-2.tgzx", "t@3.25.5"),  # tag: `.tgzx` is not a tarball suffix
        ("@s/p", "@s/p@3.25.5"),
        ("@s/p.tgz", "@s/p.tgz@3.25.5"),  # a SCOPED name is never a file to npa
        ("@s/p@latest", "@s/p@3.25.5"),
    ],
)
def test_version_pins_every_plain_registry_class(slot: str, pinned: str) -> None:
    _odd_slot_overlay(slot)

    entry = load_manifest().servers["odd-slot"]

    assert entry.args == ["-y", pinned]
    assert entry.version == "3.25.5"


def test_version_replaces_a_dist_tag_slot() -> None:
    """A dist-tag is part of the plain-registry grammar and is replaced."""
    _user_overlay(
        """
servers:
  tagged:
    description: "tagged"
    keywords: [t]
    install:
      linux: ["npx", "-y", "tagged-mcp@next"]
    command: "npx"
    args: ["-y", "tagged-mcp@next"]
    version: "1.0.0"
"""
    )

    assert load_manifest().servers["tagged"].args == ["-y", "tagged-mcp@1.0.0"]


@pytest.mark.parametrize(
    "shape",
    [
        'command: "npx"\n    args: ["-y", 123]',
        'command: "npx"\n    args: ["-y", "ok-mcp"]\n    install:\n      linux: ["npx", "-y", 123]',
        'command: 123\n    args: ["-y", "ok-mcp"]',
    ],
)
def test_a_pin_on_a_malformed_entry_costs_only_that_entry(
    shape: str, caplog: pytest.LogCaptureFixture
) -> None:
    """HEAD loads every entry of this overlay; the pin must not change that."""
    shipped_count = len(load_manifest().servers)
    _user_overlay(
        f"""
servers:
  malformed:
    description: "non-string argv"
    keywords: [m]
    {shape}
    version: "1.2.3"
"""
    )

    with caplog.at_level(logging.WARNING):
        manifest = load_manifest()  # must not raise

    assert len(manifest.servers) == shipped_count + 1
    assert manifest.servers["malformed"].version is None
    assert manifest.servers["firecrawl"].args == ["-y", "firecrawl-mcp"]
    assert any("an overlay server (name not shown)" in m for m in _warnings(caplog))
    assert not any("malformed" in m for m in _warnings(caplog))


def test_a_config_entry_without_a_command_inherits_the_pin() -> None:
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    manifest_servers = load_manifest().servers

    merged = _merge_manifest_defaults(
        "firecrawl", LocalMcpServerConfig(command="", args=[]), manifest_servers
    )

    assert merged is not None
    assert merged.args == ["-y", "firecrawl-mcp@3.25.5"]


def test_explicit_config_args_win_over_the_manifest_pin() -> None:
    """``.pmcp.json`` with its own command/args is the operator's override."""
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    manifest_servers = load_manifest().servers

    merged = _merge_manifest_defaults(
        "firecrawl",
        LocalMcpServerConfig(command="npx", args=["-y", "firecrawl-mcp@3.20.0"]),
        manifest_servers,
    )

    assert merged is not None
    assert merged.args == ["-y", "firecrawl-mcp@3.20.0"]


def test_a_remote_server_pin_is_refused(caplog: pytest.LogCaptureFixture) -> None:
    remote = next(n for n, s in load_manifest().servers.items() if s.url)
    _user_overlay(f'server_version:\n  {remote}: "1.0.0"\n')

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers[remote]

    assert entry.version is None
    assert any("remote server" in m for m in _warnings(caplog))


def test_a_later_overlay_source_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    """User overlay first, then the approved project overlay: the later pin
    is the one written into argv (materialised once, after every source)."""
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text('server_version:\n  firecrawl: "3.26.0"\n')
    approve_project_file(overlay)
    monkeypatch.chdir(tmp_path / "proj")

    entry = load_manifest().servers["firecrawl"]

    assert entry.version == "3.26.0"
    assert entry.args == ["-y", "firecrawl-mcp@3.26.0"]


def test_a_later_whole_entry_replace_drops_an_earlier_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        yaml.safe_dump(
            {
                "servers": {
                    "firecrawl": {
                        "description": "replaced",
                        "keywords": ["f"],
                        "install": {"linux": ["npx", "-y", "firecrawl-mcp"]},
                        "command": "npx",
                        "args": ["-y", "firecrawl-mcp"],
                    }
                }
            }
        )
    )
    approve_project_file(overlay)
    monkeypatch.chdir(tmp_path / "proj")

    entry = load_manifest().servers["firecrawl"]

    assert entry.version is None
    assert entry.args == ["-y", "firecrawl-mcp"]


# ---------------------------------------------------------------------------
# update_server / pmcp update: [PINNED] only for a materialised pin
# ---------------------------------------------------------------------------


class _ClientManager:
    connected: dict[str, ResolvedServerConfig] = {}

    def get_all_tools(self) -> list[Any]:
        return []

    def get_server_status(self, name: str) -> None:
        return None

    def get_all_server_statuses(self) -> list[Any]:
        return []

    def get_registry_meta(self) -> tuple[str, float]:
        return ("test-rev", 0.0)

    def get_connected_configs(self) -> dict[str, ResolvedServerConfig]:
        return {}


def _no_host_npm_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the HOST's npm configuration from this test's process env.

    main's resolver turns off npm package detection for the whole gateway
    process when its own environment sets any `npm_config_*` variable (any
    case) or `NODE_OPTIONS` -- team hosts export `npm_config_cache` -- and then
    `update_server` reports "Could not determine a registry package" instead
    of reaching the pinned branch. The [PINNED] tests assert the pinned branch,
    so they clear it themselves (Consiliency/pmcp#322, N2).
    """
    import os

    for key in list(os.environ):
        lowered = key.lower()
        if (
            lowered.startswith("npm_config_")
            or lowered.startswith("pnpm_config_")
            or key.upper() == "NODE_OPTIONS"
        ):
            monkeypatch.delenv(key)


def _gateway(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    manifest: Manifest,
    configured: list[ResolvedServerConfig] | None = None,
) -> GatewayTools:
    _no_host_npm_config(monkeypatch)
    monkeypatch.setattr(handlers_module, "load_manifest", lambda: manifest)
    monkeypatch.setattr(handlers_module, "load_configs", lambda **_: configured or [])
    policy_path = tmp_path / "gateway-policy.yaml"
    policy_path.write_text("servers: {}\n")
    gateway = GatewayTools(
        client_manager=cast(Any, _ClientManager()),
        policy_manager=PolicyManager(policy_path=policy_path),
    )
    cast(Any, gateway)._platform = "linux"

    async def probe(command: list[str], env: Any = None) -> tuple[bool, str]:
        raise AssertionError("a pinned server is never probed")

    monkeypatch.setattr(gateway, "_run_update_probe_command", probe)
    return gateway


@pytest.mark.asyncio
async def test_update_server_reports_a_materialised_pin_as_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pmcp.cli import _format_update_result

    monkeypatch.setenv("FIRECRAWL_API_KEY", "k-test")
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    gateway = _gateway(monkeypatch, tmp_path, load_manifest())

    result = await gateway.update_server({"server_name": "firecrawl"})

    assert result.ok is False
    assert result.pinned_version == "3.25.5"
    assert "is pinned to '3.25.5'" in result.message  # main's text, unchanged
    assert _format_update_result(result.model_dump()) == (
        "[PINNED] firecrawl: pinned at 3.25.5"
    )


@pytest.mark.parametrize(
    "configured_args",
    [
        ["-y", "firecrawl-mcp@3.20.0"],  # a configured pin of its own
        ["-y", "firecrawl-mcp@3.25.5", "--extra"],  # not the materialised argv
    ],
)
@pytest.mark.asyncio
async def test_update_server_keeps_mains_report_for_any_other_pin(
    configured_args: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only a pin pmcp materialised, running exactly as materialised, is
    [PINNED]; anything else is reported exactly as main reports it."""
    from pmcp.cli import _format_update_result

    monkeypatch.setenv("FIRECRAWL_API_KEY", "k-test")
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    configured = ResolvedServerConfig(
        name="firecrawl",
        source="project",
        config=LocalMcpServerConfig(command="npx", args=configured_args),
    )
    gateway = _gateway(monkeypatch, tmp_path, load_manifest(), [configured])

    result = await gateway.update_server({"server_name": "firecrawl"})

    assert result.ok is False
    assert result.pinned_version is None
    assert "is pinned to" in result.message  # main's refusal, unchanged
    assert _format_update_result(result.model_dump()).startswith("[FAILED] firecrawl: ")


def test_pmcp_update_prints_mains_lines_for_everything_else() -> None:
    from pmcp.cli import _format_update_result

    assert _format_update_result({"server": "a", "ok": True, "message": "m"}) == (
        "[OK] a: m"
    )
    assert _format_update_result({"server": "b", "ok": False, "message": "n"}) == (
        "[FAILED] b: n"
    )


# ---------------------------------------------------------------------------
# No refused pin value, and no credential-shaped value, is ever logged or
# reported (the pin logs are fixed text: reason category + server name)
# ---------------------------------------------------------------------------

_SENTINELS = ("fc-0a1b2c3d4e5f6a7b8c9d", "fc_live_0a1b2c3d4e5f6a7b")


@pytest.mark.parametrize("sentinel", _SENTINELS, ids=["alnum-dash", "underscore"])
@pytest.mark.asyncio
async def test_no_refused_pin_or_credential_reaches_a_log_or_update_output(
    sentinel: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every refusal path of `_parse_version_pin` and `_materialize_version_pin`
    (value grammar, non-npx, remote, non-plain slot, install mismatch,
    unreadable argv, unknown server). Two credential-shaped sentinels:

    * ``value`` only in refused pin VALUES and an overlay KEY: it never enters
      an argv, so it must appear in no log line of any logger, and in no
      update_server field or `pmcp update` line;
    * ``argv`` only in the entries' own argv: the pin logs are fixed text, so
      it must appear in no line the loader logs and in no `[PINNED]` output.
      (main's own messages that echo an entry's argv are unchanged by piece 1.)
    """
    from pmcp.cli import _format_update_result

    value, argv_s = sentinel, "zz" + sentinel[::-1]
    monkeypatch.setenv("FIRECRAWL_API_KEY", "k-test")
    remote = next(n for n, s in load_manifest().servers.items() if s.url)
    custom = {
        "description": "c",
        "keywords": ["c"],
        "command": "npx",
        "args": ["-y", "custom-mcp", "--api-key", argv_s],
        "install": {"linux": ["npx", "-y", "custom-mcp", "--api-key", argv_s]},
        "version": value,
    }
    overlay = {
        "servers": {
            "c-value": custom,
            "c-slot": {**custom, "args": ["-y", f"{argv_s}.tgz"], "version": "1.0.0"},
            "c-install": {
                **custom,
                "install": {"linux": ["npx", "-y", f"other-{argv_s}"]},
                "version": "1.0.0",
            },
            "c-uvx": {**custom, "command": "uvx", "version": "1.0.0"},
            "c-bad": {**custom, "args": ["-y", 5, argv_s], "version": "1.0.0"},
            "c-ok": {**custom, "version": "1.0.0"},
        },
        "server_version": {
            "firecrawl": f"3.25.5+{value}",
            "playwright": f"^1.0.0 --api-key {value}",
            remote: "1.0.0",
            f"https://user:{value}@example.test/x": "1.0.0",
            "fetch": value,
        },
    }
    _user_overlay(yaml.safe_dump(overlay))
    caplog.set_level(logging.DEBUG)

    manifest = load_manifest()
    loader_lines = [
        r.getMessage() for r in caplog.records if r.name == "pmcp.manifest.loader"
    ]
    gateway = _gateway(monkeypatch, tmp_path, manifest)
    monkeypatch.setattr(gateway, "_run_update_probe_command", _fake_probe)
    outputs = []
    for name in ("firecrawl", "playwright", "fetch", "c-value", "c-slot", "c-ok"):
        result = await gateway.update_server({"server_name": name})
        outputs.append((result, _format_update_result(result.model_dump())))

    refused = (
        "firecrawl",
        "playwright",
        "fetch",
        "c-value",
        "c-slot",
        "c-install",
        "c-uvx",
        "c-bad",
        remote,
    )
    assert all(manifest.servers[n].version is None for n in refused)
    assert manifest.servers["c-ok"].version == "1.0.0"
    assert (
        len([m for m in loader_lines if "Ignoring" in m or "does not define" in m])
        >= 10
    )
    assert value not in caplog.text
    assert all(
        value not in r.model_dump_json() and value not in line for r, line in outputs
    )
    assert all(argv_s not in m for m in loader_lines)
    pinned_lines = [line for r, line in outputs if r.pinned_version]
    assert pinned_lines == ["[PINNED] c-ok: pinned at 1.0.0"]


async def _fake_probe(command: list[str], env: Any = None) -> tuple[bool, str]:
    return (False, "probe output")


# ---------------------------------------------------------------------------
# Round 1 (board on 602064d): the entry's env and cwd; names shown only if
# shipped; a later refused pin; `server_version: null`
# ---------------------------------------------------------------------------

_REDIRECTING_KEYS = {
    "npm_config_package": "evil@1.0.0",  # npx runs the pinned spec as a command
    "NPM_CONFIG_PACKAGE": "evil@1.0.0",  # npm reads the prefix in any case
    "Npm_Config_Registry": "http://registry.test/",
    "npm_config_registry": "",  # an empty value still changes npm's reading
    "npm_config_cache": "/srv/cache",  # npx trusts a cached package.json
    "npm_config_//registry.npmjs.org/:_authToken": "tok-zz91",  # not on the allowlist
    "NODE_OPTIONS": "--require=/srv/x.js",
    "NODE_PATH": "/srv/lib",
    "PATH": "/srv/bin",
    "HOME": "/srv/home",
    "PREFIX": "/srv",
    "XDG_CONFIG_HOME": "/srv/cfg",
    "nvm_dir": "/srv/nvm",
    "HTTPS_PROXY": "http://proxy.test:3128",
    "SOME_UNKNOWN_KEY": "unknown-zz92",
}


@pytest.mark.parametrize("via", ["server_env", "extra_env"])
@pytest.mark.parametrize("key", list(_REDIRECTING_KEYS))
def test_a_pin_is_refused_when_the_entry_env_may_redirect_npm(
    key: str, via: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 1 codex B1: with the entry's `npm_config_package`, real npm 10 and
    11 ran the materialised `npx -y pkg@1.2.3` as a shell command from another
    package's environment (plan appendix). A pin piece 1 writes must not run a
    different package, so any entry env key not proven inert refuses the pin;
    the entry is unchanged."""
    shipped = load_manifest().servers["firecrawl"]
    value = _REDIRECTING_KEYS[key]
    overlay: dict[str, Any]
    if via == "server_env":
        overlay = {"server_env": {"firecrawl": {key: value}}}
    else:
        entry = {
            "description": "f",
            "keywords": ["f"],
            "command": "npx",
            "args": list(shipped.args),
            "install": dict(shipped.install),
            "extra_env": {key: value},
        }
        overlay = {"servers": {"firecrawl": entry}}
    overlay["server_version"] = {"firecrawl": "3.25.5"}
    _user_overlay(yaml.safe_dump(overlay))

    with caplog.at_level(logging.WARNING):
        pinned = load_manifest().servers["firecrawl"]

    assert pinned.version is None
    assert pinned.args == shipped.args
    assert any("may redirect npm" in m for m in _warnings(caplog))
    assert not any(value and value in m for m in _warnings(caplog))


@pytest.mark.parametrize(
    "env",
    [
        {"LANG": "C.UTF-8", "LC_ALL": "C", "TERM": "xterm", "NO_COLOR": "1"},
        {"npm_config_loglevel": "silent", "NPM_CONFIG_FUND": "false"},
        {"FIRECRAWL_API_URL": "http://self-hosted.test:3002"},  # shipped-declared
    ],
    ids=["locale", "npm-logging", "shipped-declared"],
)
def test_a_pin_holds_next_to_keys_proven_inert(env: dict[str, str]) -> None:
    overlay = {
        "server_env": {"firecrawl": env},
        "server_version": {"firecrawl": "3.25.5"},
    }
    _user_overlay(yaml.safe_dump(overlay))

    entry = load_manifest().servers["firecrawl"]

    assert entry.version == "3.25.5"
    assert entry.args == ["-y", "firecrawl-mcp@3.25.5"]


def test_an_overlay_credential_key_is_not_declared_by_the_shipped_manifest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An overlay's `env_var` is injected into the child, and only pmcp's
    shipped manifest can declare a key inert, so an overlay-only server with a
    credential cannot be pinned this way (pin it in `.mcp.json` args)."""
    entry = {
        "description": "c",
        "keywords": ["c"],
        "command": "npx",
        "args": ["-y", "custom-mcp"],
        "install": {"linux": ["npx", "-y", "custom-mcp"]},
        "env_var": "CUSTOM_API_KEY",
        "version": "1.0.0",
    }
    _user_overlay(yaml.safe_dump({"servers": {"custom-cred": entry}}))

    with caplog.at_level(logging.WARNING):
        loaded = load_manifest().servers["custom-cred"]

    assert loaded.version is None
    assert any("may redirect npm" in m for m in _warnings(caplog))


@pytest.mark.parametrize(
    ("cwd", "env", "pinned"),
    [
        (None, {}, True),
        (None, {"LANG": "C.UTF-8"}, True),
        ("/srv/project", {}, False),  # a project .npmrc / node_modules there
        (None, {"NODE_OPTIONS": "--require=/srv/x.js"}, False),
        (None, {"npm_config_package": "evil@1.0.0"}, False),
        (None, {"SOME_UNKNOWN_KEY": "u"}, False),
        (None, {"HTTPS_PROXY": "http://proxy.test:3128"}, False),
        (None, {"PATH": "/srv/bin"}, False),
        (None, {"NODE_PATH": "/srv/lib"}, False),
        (None, {"npm_config_cache": "/srv/cache"}, False),
    ],
    ids=[
        "inherits",
        "inert-env",
        "entry-cwd",
        "node-options",
        "npm-package",
        "unknown-key",
        "proxy",
        "path",
        "node-path",
        "npm-cache",
    ],
)
@pytest.mark.asyncio
async def test_pinned_is_withheld_when_the_config_env_or_cwd_may_redirect_npm(
    cwd: str | None,
    env: dict[str, str],
    pinned: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A config that runs the materialised argv (e.g. a `.pmcp.json` entry with
    no command) brings its OWN env and cwd; `[PINNED]` applies only if they
    cannot redirect npm (the same rule as materialisation)."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "k-test")
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    manifest = load_manifest()
    configured = ResolvedServerConfig(
        name="firecrawl",
        source="project",
        config=LocalMcpServerConfig(
            command="npx",
            args=list(manifest.servers["firecrawl"].args),
            env=env,
            cwd=cwd,
        ),
    )
    gateway = _gateway(monkeypatch, tmp_path, manifest, [configured])

    result = await gateway.update_server({"server_name": "firecrawl"})

    assert result.ok is False
    assert (result.pinned_version == "3.25.5") is pinned


_NAME_SENTINELS = ("fc-0a1b2c3d4e5f6a7b8c9d", "fc_live_0a1b2c3d4e5f6a7b")


@pytest.mark.parametrize("name", _NAME_SENTINELS, ids=["alnum-dash", "underscore"])
def test_no_overlay_server_name_is_ever_logged(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 1 codex B2 / claude N1: a bare credential-shaped KEY -- as a
    `server_version` key for no server, and as a `servers:` key with a refused
    value, a refused slot and an unreadable argv -- never reaches a log line.
    Only names in pmcp's shipped manifest are shown."""
    from pmcp.manifest.loader import _server_label

    base = {
        "description": "c",
        "keywords": ["c"],
        "command": "npx",
        "install": {"linux": ["npx", "-y", "custom-mcp"]},
    }
    overlay = {
        "servers": {
            name: {**base, "args": ["-y", "custom-mcp"], "version": "latest"},
            name + "-slot": {**base, "args": ["-y", "x.tgz"], "version": "1.0.0"},
            name + "-bad": {**base, "args": ["-y", 5], "version": "1.0.0"},
        },
        "server_version": {name + "-nope": "1.0.0"},
    }
    _user_overlay(yaml.safe_dump(overlay))
    caplog.set_level(logging.DEBUG)

    load_manifest()

    lines = [r.getMessage() for r in caplog.records if r.name == "pmcp.manifest.loader"]
    assert sum("an overlay server (name not shown)" in m for m in lines) >= 4
    assert not any(name in m for m in lines)
    assert _server_label("firecrawl") == "server 'firecrawl'"


def test_a_later_refused_pin_leaves_an_earlier_pin_standing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    approve_project_file: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 1 claude N2: the user overlay pins 1.0.0, then an approved project
    overlay says `latest` (refused). The earlier pin stands, and the warning
    says so, not "the server stays unpinned"."""
    _user_overlay('server_version:\n  firecrawl: "1.0.0"\n')
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("server_version:\n  firecrawl: latest\n")
    approve_project_file(overlay)
    monkeypatch.chdir(tmp_path / "proj")

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["firecrawl"]

    assert entry.version == "1.0.0"
    assert entry.args == ["-y", "firecrawl-mcp@1.0.0"]
    refusal = [m for m in _warnings(caplog) if "server_version" in m]
    assert refusal and all("stays unpinned" not in m for m in refusal)
    assert any("any pin from an earlier source stands" in m for m in refusal)


def test_server_version_null_sets_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    approve_project_file: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`server_version: {name: null}` means "no pin from this source": it
    neither pins nor unpins, and logs nothing. Only a whole-entry replace (or
    removing the earlier line) takes a pin away."""
    _user_overlay('server_version:\n  firecrawl: "1.0.0"\n')
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text("server_version:\n  firecrawl: null\n")
    approve_project_file(overlay)
    monkeypatch.chdir(tmp_path / "proj")

    with caplog.at_level(logging.WARNING):
        entry = load_manifest().servers["firecrawl"]

    assert entry.version == "1.0.0"
    assert not any("server_version" in m for m in _warnings(caplog))


# ---------------------------------------------------------------------------
# Round 2 (piece 1 rev 2.1): only a BARE npx launcher is pinned
# ---------------------------------------------------------------------------

_NOT_BARE_NPX = [
    "/tmp/x/npx",
    "./npx",
    "node_modules/.bin/npx",
    "/proc/self/cwd/npx",
    "~/bin/npx.cmd",
]


@pytest.mark.parametrize("where", ["command", "install"])
@pytest.mark.parametrize("spelling", _NOT_BARE_NPX)
def test_a_pin_is_refused_for_a_launcher_that_is_not_bare_npx(
    spelling: str, where: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 2 (claude N1): the loader decided "npx" by basename, so a program
    at some path merely NAMED npx got its argv pinned; provisioning would run
    that program. Only the bare spelling the PATH resolves is pinned."""
    command = spelling if where == "command" else "npx"
    install = spelling if where == "install" else "npx"
    entry = {
        "description": "c",
        "keywords": ["c"],
        "command": command,
        "args": ["-y", "custom-mcp"],
        "install": {"linux": [install, "-y", "custom-mcp"]},
        "version": "1.0.0",
    }
    _user_overlay(yaml.safe_dump({"servers": {"custom-path": entry}}))

    with caplog.at_level(logging.WARNING):
        loaded = load_manifest().servers["custom-path"]

    assert loaded.version is None
    assert loaded.args == ["-y", "custom-mcp"]
    assert any("bare `npx`" in m for m in _warnings(caplog))
    assert not any(spelling in m for m in _warnings(caplog))


@pytest.mark.parametrize(
    ("windows", "command", "pinned"),
    [
        (False, "npx", True),
        (False, "npx.cmd", False),
        (False, "NPX", False),
        (True, "npx.cmd", True),
        (True, "NPX.EXE", True),
        (True, "~/bin/npx.cmd", False),
        (True, "C:\\tools\\npx.cmd", False),
    ],
)
def test_the_bare_npx_spellings_per_platform(
    windows: bool, command: str, pinned: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.manifest import loader as loader_module

    monkeypatch.setattr(loader_module, "_on_windows", lambda: windows)
    entry = {
        "description": "c",
        "keywords": ["c"],
        "command": command,
        "args": ["-y", "custom-mcp"],
        "install": {"linux": [command, "-y", "custom-mcp"]},
        "version": "1.0.0",
    }
    _user_overlay(yaml.safe_dump({"servers": {"custom-win": entry}}))

    assert (load_manifest().servers["custom-win"].version == "1.0.0") is pinned


def test_a_failure_while_materialising_costs_only_that_entry(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Containment, tested directly: since round 2 the launcher check reads
    non-string commands without raising, so the malformed-entry tests no
    longer reach the per-entry guard. Any exception while writing one pin
    must cost that entry its pin, never the manifest or another pin."""
    from pmcp.manifest import loader as loader_module

    real = loader_module._pin_npx_args

    def flaky(args: list[str], version: str) -> Any:
        if "firecrawl-mcp" in args:
            raise RuntimeError("boom")
        return real(args, version)

    monkeypatch.setattr(loader_module, "_pin_npx_args", flaky)
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n  playwright: "1.2.3"\n')

    with caplog.at_level(logging.WARNING):
        manifest = load_manifest()  # must not raise

    assert manifest.servers["firecrawl"].version is None
    assert manifest.servers["firecrawl"].args == ["-y", "firecrawl-mcp"]
    assert manifest.servers["playwright"].args == ["-y", "@playwright/mcp@1.2.3"]
    assert any("could not be read (RuntimeError)" in m for m in _warnings(caplog))


# ---------------------------------------------------------------------------
# Consiliency/pmcp#322: the invalid-pin warning names the right consequence
# ---------------------------------------------------------------------------


def test_a_bad_version_on_a_replacing_entry_does_not_claim_an_earlier_pin_stands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The issue's repro (N1): the user overlay pins firecrawl to 3.25.5, then a
    `PMCP_MANIFEST_PATH` overlay replaces the whole entry with `version:
    latest`. The server ends up unpinned, so the only pin warning must say
    the entry replaced any earlier pin -- not that an earlier pin stands."""
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    shipped = load_manifest().servers["firecrawl"]
    explicit = tmp_path / "explicit.yaml"
    entry = {
        "description": "f",
        "keywords": ["f"],
        "command": "npx",
        "args": ["-y", "firecrawl-mcp"],
        "install": {"linux": ["npx", "-y", "firecrawl-mcp"]},
        "version": "latest",
    }
    explicit.write_text(yaml.safe_dump({"servers": {"firecrawl": entry}}))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))

    with caplog.at_level(logging.WARNING):
        loaded = load_manifest().servers["firecrawl"]

    assert shipped.version == "3.25.5"  # the earlier pin did exist
    assert loaded.version is None
    assert loaded.args == ["-y", "firecrawl-mcp"]
    pin_lines = [m for m in _warnings(caplog) if "pin for server 'firecrawl'" in m]
    assert len(pin_lines) == 1
    assert "'version' pin" in pin_lines[0]
    assert "replaced any earlier pin" in pin_lines[0]
    assert "stands" not in pin_lines[0]
    assert "latest" not in pin_lines[0].split("This pin is ignored")[1]


def test_a_bad_server_version_still_says_an_earlier_pin_stands(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _user_overlay("server_version:\n  firecrawl: latest\n")

    with caplog.at_level(logging.WARNING):
        load_manifest()

    pin_lines = [m for m in _warnings(caplog) if "pin for server 'firecrawl'" in m]
    assert len(pin_lines) == 1
    assert "'server_version' pin" in pin_lines[0]
    assert (
        "any pin from an earlier source stands, unless a 'servers:' entry"
        in pin_lines[0]
    )


# ---------------------------------------------------------------------------
# Consiliency/pmcp#322 round 1: the consequence text is true in EVERY
# precedence case, derived from the loader's merge order
# ---------------------------------------------------------------------------

_ENTRY_KINDS = (None, "plain", "good", "bad")  # a `servers:` entry for firecrawl
_SV_KINDS = (None, "good", "bad")  # a `server_version` for firecrawl
_SV_TEXT = (
    "any pin from an earlier source stands, unless a 'servers:' entry for this "
    "server in this or a later source replaced it, or a later source set another pin"
)
_VERSION_TEXT = (
    "the whole entry that carries it replaced any earlier pin, so the server is "
    "unpinned unless this source's 'server_version' or a later source pins it"
)


def _merge_model(
    sources: list[tuple[str | None, str | None]], goods: list[tuple[str, str]]
) -> list[str | None]:
    """The pin after each source, by the loader's merge order: shipped (no
    pin), then each overlay source in order; within a source, a `servers:`
    entry replaces the whole entry (and its pin) first, then `server_version`
    patches it. A bad pin is ignored. Returns [before source 0, after 0, ...]."""
    pin: str | None = None
    states = [pin]
    for (entry, sv), (entry_pin, sv_pin) in zip(sources, goods):
        if entry is not None:
            pin = entry_pin if entry == "good" else None
        if sv == "good":
            pin = sv_pin
        states.append(pin)
    return states


def _entry_doc(shipped: ServerConfig, kind: str | None, pin: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "description": "f",
        "keywords": ["f"],
        "command": "npx",
        "args": list(shipped.args),
        "install": {k: list(v) for k, v in shipped.install.items()},
    }
    if kind in ("good", "bad"):
        body["version"] = pin if kind == "good" else "latest"
    return body


@pytest.fixture
def _memoized_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse each distinct YAML text once for the exhaustive precedence tests.

    The two tests below call ``load_manifest()`` 432 times, and >99% of each
    call is pure-Python ``yaml.safe_load`` re-parsing the same 78 KB shipped
    manifest. Under coverage's C tracer on CPython 3.11 that alone ran past
    ``faulthandler_timeout`` (120 s) in CI, and the faulthandler dump of the
    still-running main thread then segfaulted the job (exit 139). Every
    overlay document is still parsed for real -- the cache is keyed on the
    text, a parse error is never cached, and each caller gets a deep copy so
    nothing the loader mutates can leak into the next case.
    """
    import copy

    real = yaml.safe_load
    cache: dict[str | bytes, Any] = {}

    def safe_load(stream: Any) -> Any:
        text = stream if isinstance(stream, (str, bytes)) else stream.read()
        if text not in cache:
            cache[text] = real(text)
        return copy.deepcopy(cache[text])

    monkeypatch.setattr(yaml, "safe_load", safe_load)


def _check_precedence_case(
    case: tuple[tuple[str | None, str | None], ...],
    goods: list[tuple[str, str]],
    writers: list[Any],
    shipped: ServerConfig,
    caplog: pytest.LogCaptureFixture,
    checked: dict[str, int],
) -> None:
    """Write one overlay document per source (in merge order), load, and check
    (1) the loader against the merge-order model, (2) the args against the
    final pin, and (3) every invalid-pin warning is TRUE against that state."""
    for (entry, sv), (entry_pin, sv_pin), write in zip(case, goods, writers):
        doc: dict[str, Any] = {}
        if entry is not None:
            doc["servers"] = {"firecrawl": _entry_doc(shipped, entry, entry_pin)}
        if sv is not None:
            doc["server_version"] = {"firecrawl": sv_pin if sv == "good" else "^3"}
        write(yaml.safe_dump(doc))
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        loaded = load_manifest().servers["firecrawl"]

    states = _merge_model(list(case), goods)
    final = states[-1]
    assert loaded.version == final, case
    expected_args = (
        ["-y", f"firecrawl-mcp@{final}"] if final else ["-y", "firecrawl-mcp"]
    )
    assert loaded.args == expected_args, case

    lines = [m for m in _warnings(caplog) if m.startswith("Ignoring a '")]
    expected = [
        (field, i)
        for i, (entry, sv) in enumerate(case)
        for field, bad in (("version", entry == "bad"), ("server_version", sv == "bad"))
        if bad
    ]
    assert len(lines) == len(expected), (case, lines)
    for line, (field, i) in zip(lines, expected):
        assert line.startswith(f"Ignoring a '{field}' pin for server 'firecrawl'")
        later_pins = any(e == "good" or s == "good" for e, s in case[i + 1 :])
        if field == "server_version":
            assert _SV_TEXT in line, (case, line)
            replaced = any(e is not None for e, _s in case[i:])
            earlier = states[i]
            if earlier is not None and not replaced and not later_pins:
                assert final == earlier, (case, line)  # "stands" is true
        else:
            assert _VERSION_TEXT in line, (case, line)
            if case[i][1] != "good" and not later_pins:
                assert final is None, (case, line)  # "unpinned" is true
        checked[field] += 1


def test_the_invalid_pin_text_is_true_in_every_two_source_case(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    _memoized_yaml: None,
) -> None:
    """Round 1 on Consiliency/pmcp#323 (F1, F2), same-file and later-file: two
    overlay sources (the user overlay, then `PMCP_MANIFEST_PATH`), each with any
    of {no entry, an entry without a pin, with a good pin, with a bad pin} x
    {no `server_version`, a good one, a bad one}: all 144 cases."""
    import itertools

    shipped = load_manifest().servers["firecrawl"]
    explicit = tmp_path / "explicit.yaml"
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))
    writers = [_user_overlay, explicit.write_text]
    one = list(itertools.product(_ENTRY_KINDS, _SV_KINDS))
    checked = {"server_version": 0, "version": 0}
    for case in itertools.product(one, repeat=2):
        _check_precedence_case(
            case,
            [("1.0.1", "1.0.2"), ("2.0.1", "2.0.2")],
            writers,
            shipped,
            caplog,
            checked,
        )
    assert checked == {"server_version": 96, "version": 72}


def test_the_invalid_pin_text_is_true_with_sources_before_and_after_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    approve_project_file: Any,
    caplog: pytest.LogCaptureFixture,
    _memoized_yaml: None,
) -> None:
    """Round 1 on Consiliency/pmcp#323 (c7): a bad pin with an earlier source
    AND a later one -- user overlay, approved project overlay, then
    `PMCP_MANIFEST_PATH`. The middle source always carries a bad pin (6
    kinds); the first carries each earlier-pin shape (none, a `server_version`,
    an entry with a pin, an entry without one); the last is any of all 12.
    288 cases."""
    import itertools

    shipped = load_manifest().servers["firecrawl"]
    project = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    project.parent.mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "proj")
    explicit = tmp_path / "explicit.yaml"
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(explicit))

    def write_project(text: str) -> None:
        project.write_text(text)
        approve_project_file(project)

    writers = [_user_overlay, write_project, explicit.write_text]
    firsts = [(None, None), (None, "good"), ("good", None), ("plain", None)]
    middles = [
        (e, s) for e, s in itertools.product(_ENTRY_KINDS, _SV_KINDS) if "bad" in (e, s)
    ]
    lasts = list(itertools.product(_ENTRY_KINDS, _SV_KINDS))
    goods = [("1.0.1", "1.0.2"), ("2.0.1", "2.0.2"), ("3.0.1", "3.0.2")]
    checked = {"server_version": 0, "version": 0}
    for case in itertools.product(firsts, middles, lasts):
        _check_precedence_case(case, goods, writers, shipped, caplog, checked)
    assert len(middles) * len(firsts) * len(lasts) == 288
    assert checked["server_version"] > 0 and checked["version"] > 0


@pytest.mark.parametrize("cause", ["gateway-env-npm-config", "local-prefix"])
@pytest.mark.asyncio
async def test_a_pinned_server_reports_failed_when_npm_config_is_out_of_sight(
    cause: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 1 on Consiliency/pmcp#323 (F4): locks the README's causes. With
    `npm_config_cache` in the gateway's own environment, or with a
    `package.json` in the directory the gateway runs in, main's resolver refuses
    package detection. `update_server` reports "Could not determine a registry
    package" instead of [PINNED], moves nothing, and the pin is still in args."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "k-test")
    _user_overlay('server_version:\n  firecrawl: "3.25.5"\n')
    manifest = load_manifest()
    gateway = _gateway(monkeypatch, tmp_path, manifest)  # clears host npm config
    if cause == "gateway-env-npm-config":
        monkeypatch.setenv("npm_config_cache", str(tmp_path / "npm-cache"))
    else:
        (Path.cwd() / "package.json").write_text("{}\n")

    result = await gateway.update_server({"server_name": "firecrawl"})

    assert result.ok is False
    assert result.pinned_version is None
    assert "Could not determine a registry package" in result.message
    assert manifest.servers["firecrawl"].args == ["-y", "firecrawl-mcp@3.25.5"]
    assert all(
        argv[1:] == ["-y", "firecrawl-mcp@3.25.5"]
        for argv in manifest.servers["firecrawl"].install.values()
        if argv
    )
