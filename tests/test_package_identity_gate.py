"""Provisioning binds to package identity (PKGID SL-1, Consiliency/pmcp#230).

Review finding S-01: `register_discovered_server` accepted any agent-chosen npm
package, `provision` ran it with `npx -y`, and policy only ever looked at the
agent-chosen server NAME -- so allowlisting `internal-approved-tool` executed
`npx -y totally-arbitrary-evil-package`. These tests pin the gate that closes
it (`pmcp.provision_gate.evaluate_provision`) and its two handler seams.

**The decision order is the contract** (IF-0-PKGID-1). The one that matters
most is rule 4 -- an argv that does not name the identity's exact
`name@resolved_version` is refused BEFORE any allow rule runs. An earlier plan
revision placed it after the approval and policy-allow rules, which reopened
S-01 for any approved server. The tests for it therefore always arrange an
approval or a policy allowlist first; a test that only covered an unapproved
server would pass under both orders and prove nothing.

**Offline.** No test here reaches the npm registry. `no_network` below replaces
the registry opener for every test and fails any test that tried; tests that
need a resolution stub `package_identity._fetch_packument`, the seam TRUST's own
suite uses.

**Isolation is declared here** (IF-0-PKGID-2), not inherited from
`tests/conftest.py`: an approval written to the operator's real store would
satisfy rule 5 and permit a real provision.
"""

from __future__ import annotations

import asyncio
import itertools
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from pmcp.manifest import package_identity
from pmcp.manifest.loader import Manifest, ServerConfig
from pmcp.manifest.package_identity import PackageIdentity
from pmcp.package_approvals import approve_package, package_approvals_path
from pmcp.policy.policy import PolicyManager
from pmcp.provision_gate import (
    ProvisionDecision,
    approve_package_command,
    evaluate_provision,
    operator_safe,
)
from pmcp.tools import handlers as handlers_module
from pmcp.tools.handlers import GatewayTools

_REAL_HOME = Path.home().resolve()
_REAL_STORE = _REAL_HOME / ".config" / "pmcp" / "package_approvals.json"

EVIL = "totally-arbitrary-evil-package"
EVIL_VERSION = "6.6.6"
PLATFORMS = ("mac", "linux", "wsl", "windows")


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolate_package_approvals(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Repoint HOME for this file's own tests and prove the store moved."""
    real_store_existed = _REAL_STORE.exists()
    home = tmp_path_factory.mktemp("package-gate-home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    store = package_approvals_path()
    if not store.is_relative_to(home.resolve()) or store.is_relative_to(_REAL_HOME):
        raise RuntimeError(
            f"Package approval store isolation failed: {store} is not under "
            f"{home}. Refusing to run a test that could write {_REAL_STORE}."
        )
    yield home
    assert _REAL_STORE.exists() == real_store_existed


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail any test that lets identity resolution reach the real registry."""
    attempts: list[str] = []

    class _Refusing:
        def open(self, request: Any, *args: Any, **kwargs: Any) -> Any:
            attempts.append(getattr(request, "full_url", str(request)))
            raise OSError("network disabled in tests")

    monkeypatch.setattr(package_identity, "_OPENER", _Refusing())
    yield attempts
    assert attempts == [], f"a test reached the network: {attempts}"


def _packument(name: str, version: str) -> dict[str, Any]:
    return {
        "name": name,
        "dist-tags": {"latest": version},
        "versions": {version: {"dist": {"integrity": f"sha512-{name}-{version}"}}},
    }


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A fake npm registry: `registry[name] = latest_version`."""
    known: dict[str, str] = {}

    def fetch(name: str) -> dict[str, Any] | None:
        version = known.get(name)
        return None if version is None else _packument(name, version)

    monkeypatch.setattr(package_identity, "_fetch_packument", fetch)
    return known


class _MinimalClientManager:
    def __init__(self) -> None:
        self.connected: list[Any] = []
        self.disconnected: list[str] = []

    def get_all_tools(self) -> list[Any]:
        return []

    def get_tool(self, tool_id: str) -> None:
        return None

    def is_server_online(self, name: str) -> bool:
        return False

    def is_lazy_server(self, name: str) -> bool:
        return False

    def get_server_status(self, name: str) -> None:
        return None

    def get_all_server_statuses(self) -> list[Any]:
        return []

    def get_registry_meta(self) -> tuple[str, float]:
        return ("test-rev", 0.0)

    async def refresh(self, configs: Any) -> list[str]:
        return []

    async def connect_all(self, configs: Any, retry: bool = True) -> list[str]:
        self.connected.extend(configs)
        return []

    # Lifecycle surface. Each records the configs it would have spawned.
    async def connect_server(self, config: Any) -> list[str]:
        self.connected.append(config)
        return []

    async def restart_server(
        self, config: Any, force: bool = False
    ) -> tuple[bool, int, list[str]]:
        self.connected.append(config)
        return (True, 0, [])

    async def disconnect_server(
        self, name: str, force: bool = False
    ) -> tuple[bool, int, str | None]:
        self.disconnected.append(name)
        return (True, 0, None)

    def get_active_tasks(self, name: str | None = None) -> list[Any]:
        return []


class _RecordingJobManager:
    """Stands in for `JobManager`: records every install it is asked to spawn."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], ServerConfig]] = []

    async def start_install(self, server_config: ServerConfig, platform: str) -> str:
        self.calls.append(
            (list(server_config.install.get(platform) or []), server_config)
        )
        return f"job-{len(self.calls)}"


def _write_policy(tmp_path: Path, body: str) -> PolicyManager:
    path = tmp_path / "gateway-policy.yaml"
    path.write_text(body, encoding="utf-8")
    return PolicyManager(policy_path=path)


def _gateway(
    monkeypatch: pytest.MonkeyPatch,
    policy: PolicyManager | None = None,
    manifest_servers: dict[str, ServerConfig] | None = None,
) -> tuple[GatewayTools, _RecordingJobManager]:
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers=dict(manifest_servers or {}),
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )
    jobs = _RecordingJobManager()
    monkeypatch.setattr(handlers_module, "load_manifest", lambda: manifest)
    monkeypatch.setattr(handlers_module, "load_configs", lambda **_: [])
    monkeypatch.setattr(handlers_module, "get_job_manager", lambda: jobs)
    gateway = GatewayTools(
        client_manager=cast(Any, _MinimalClientManager()),
        policy_manager=policy or PolicyManager(policy_path=None),
    )
    cast(Any, gateway)._platform = "linux"
    return gateway, jobs


def _identity(
    name: str = "example-mcp", version: str = "1.2.3", integrity: str | None = None
) -> PackageIdentity:
    return PackageIdentity("npm", name, version, integrity)


def _config(
    args: list[str],
    *,
    install: list[str] | None = None,
    command_override: str = "npx",
    **extra: Any,
) -> ServerConfig:
    return ServerConfig(
        name=extra.pop("name", "srv"),
        description="",
        keywords=[],
        install={
            p: list(install if install is not None else ["npx", *args])
            for p in PLATFORMS
        },
        command=command_override,
        args=list(args),
        **extra,
    )


class _StubPolicy:
    """A policy that answers one fixed package verdict."""

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict

    def evaluate_package_policy(self, identity: PackageIdentity | None) -> str:
        return "unspecified" if identity is None else self.verdict


def _empty_policy(tmp_path: Path) -> PolicyManager:
    return _write_policy(tmp_path, "servers: {}\n")


# ---------------------------------------------------------------------------
# EC-PKGID-1 -- the review's reproduction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_review_reproduction_does_not_spawn_npx_for_an_arbitrary_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: dict[str, str]
) -> None:
    """Allowlisting a server NAME must not execute whatever package it names."""
    policy = _write_policy(
        tmp_path, "servers:\n  allowlist:\n    - internal-approved-tool\n"
    )
    assert policy.is_server_allowed("internal-approved-tool")
    registry[EVIL] = EVIL_VERSION
    gateway, jobs = _gateway(monkeypatch, policy)

    registered = await gateway.register_discovered_server(
        {"server_name": "internal-approved-tool", "package": EVIL}
    )
    assert registered.registered is True  # it resolves, so it registers...

    result = await gateway.provision({"server_name": "internal-approved-tool"})

    assert jobs.calls == []  # ...but nothing is ever spawned
    assert result.ok is False
    assert result.status == "failed"
    assert EVIL in result.message
    assert "pmcp trust approve-package" in result.message

    # The same reproduction through a registry that does not describe the
    # package is refused one step earlier, and still spawns nothing.
    registry.clear()
    gateway2, jobs2 = _gateway(monkeypatch, policy)
    refused = await gateway2.register_discovered_server(
        {"server_name": "internal-approved-tool", "package": EVIL}
    )
    assert refused.registered is False
    after = await gateway2.provision({"server_name": "internal-approved-tool"})
    assert after.ok is False
    assert jobs2.calls == []


@pytest.mark.asyncio
async def test_the_refusal_names_the_package_and_the_approval_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: dict[str, str]
) -> None:
    registry[EVIL] = EVIL_VERSION
    gateway, jobs = _gateway(monkeypatch, _empty_policy(tmp_path))
    await gateway.register_discovered_server(
        {"server_name": "internal-approved-tool", "package": EVIL}
    )

    result = await gateway.provision({"server_name": "internal-approved-tool"})

    exact = f"pmcp trust approve-package {EVIL}@{EVIL_VERSION}"
    assert result.ok is False
    assert jobs.calls == []
    assert exact in result.message
    assert EVIL in result.message

    decision = evaluate_provision(
        _config(["-y", f"{EVIL}@{EVIL_VERSION}"]),
        _identity(EVIL, EVIL_VERSION),
        source="discovered",
        policy=_empty_policy(tmp_path),
    )
    assert decision == ProvisionDecision(
        allowed=False,
        reason="not_approved",
        remedy=exact,
        identity=_identity(EVIL, EVIL_VERSION),
    )


# ---------------------------------------------------------------------------
# EC-PKGID-3 -- default deny, manifest exemption, source from the lookup path
# ---------------------------------------------------------------------------


def test_a_configured_server_with_unpinned_argv_is_refused(tmp_path: Path) -> None:
    """Rule 4 runs BEFORE both allow rules.

    Every refused case below is arranged so that rule 5 (a recorded approval)
    or rule 6 (a policy allowlist) WOULD allow it. Under the superseded order,
    where rule 4 came after them, each of these returns allowed=True.
    """
    identity = _identity()
    approve_package(identity)
    allow_policy = _write_policy(
        tmp_path, "packages:\n  allowlist:\n    - example-mcp\n"
    )
    (tmp_path / "unspecified").mkdir()
    no_policy = _empty_policy(tmp_path / "unspecified")  # packages unspecified

    unpinned_shapes = [
        ["-y", "example-mcp"],  # re-resolves `latest` at every spawn
        ["-y", "example-mcp@1.2.4"],  # a different version than approved
        ["-y", "example-mcp@latest"],
        ["-y", "example-mcp@^1.2.3"],
        # The spec is present, but `-p` installs another package first.
        ["-p", "evil", "-y", "example-mcp@1.2.3"],
        ["--package=evil", "-y", "example-mcp@1.2.3"],
        # The spec is present, but not in the package slot.
        ["-y", "other-mcp", "example-mcp@1.2.3"],
        [],
    ]
    for args in unpinned_shapes:
        for policy in (no_policy, allow_policy):  # approved; approved + allowed
            decision = evaluate_provision(
                _config(args), identity, source="configured", policy=policy
            )
            assert decision.allowed is False, args
            assert decision.reason == "unpinned_configured_argv", args
            assert decision.remedy is not None
            assert "example-mcp@1.2.3" in decision.remedy
            assert "approve-package" not in decision.remedy

    # The spec in the package slot of something that is not npx is not pinned:
    # `bash example-mcp@1.2.3` merely spells the name.
    for policy in (no_policy, allow_policy):
        decision = evaluate_provision(
            _config(["example-mcp@1.2.3"], install=[], command_override="bash"),
            identity,
            source="configured",
            policy=policy,
        )
        assert (decision.allowed, decision.reason) == (
            False,
            "unpinned_configured_argv",
        )

    # Policy-allowed with NO approval: still refused by rule 4, not rule 6.
    from pmcp.package_approvals import revoke_package

    assert revoke_package("example-mcp") is True
    decision = evaluate_provision(
        _config(["-y", "example-mcp"]),
        identity,
        source="configured",
        policy=allow_policy,
    )
    assert (decision.allowed, decision.reason) == (False, "unpinned_configured_argv")

    # Pinned args but an install argv that is not: start_install would run it.
    approve_package(identity)
    decision = evaluate_provision(
        _config(["-y", "example-mcp@1.2.3"], install=["npx", "-y", "example-mcp"]),
        identity,
        source="discovered",
        policy=no_policy,
    )
    assert (decision.allowed, decision.reason) == (False, "unpinned_configured_argv")

    # Control: the same approval with pinned argv IS allowed, so the refusals
    # above are caused by the argv and not by a gate that denies everything.
    for args in (
        ["-y", "example-mcp@1.2.3"],
        ["example-mcp@1.2.3"],
        ["--yes", "example-mcp@1.2.3", "--port", "3000"],
    ):
        decision = evaluate_provision(
            _config(args), identity, source="configured", policy=no_policy
        )
        assert (decision.allowed, decision.reason) == (True, "package_approved"), args
        assert decision.remedy is None


@pytest.mark.asyncio
async def test_discovered_provisioning_is_denied_without_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: dict[str, str]
) -> None:
    registry["fresh-mcp"] = "0.4.0"
    gateway, jobs = _gateway(monkeypatch, _empty_policy(tmp_path))
    assert (
        await gateway.register_discovered_server(
            {"server_name": "fresh", "package": "fresh-mcp"}
        )
    ).registered is True

    result = await gateway.provision({"server_name": "fresh"})
    assert result.ok is False
    assert jobs.calls == []
    assert "pmcp trust approve-package fresh-mcp@0.4.0" in result.message

    # No `packages` section at all -- "unspecified" is not permission.
    decision = evaluate_provision(
        _config(["-y", "fresh-mcp@0.4.0"]),
        _identity("fresh-mcp", "0.4.0"),
        source="discovered",
        policy=_empty_policy(tmp_path),
    )
    assert (decision.allowed, decision.reason) == (False, "not_approved")

    # An unresolvable identity is refused too, with a lookup remedy, not an
    # approval command it could never satisfy.
    decision = evaluate_provision(
        _config(["-y", "fresh-mcp"], package="fresh-mcp"),
        None,
        source="discovered",
        policy=_empty_policy(tmp_path),
    )
    assert (decision.allowed, decision.reason) == (False, "unresolvable_identity")
    assert decision.remedy is not None
    assert "approve-package" not in decision.remedy
    assert "registry" in decision.remedy


@pytest.mark.asyncio
async def test_a_manifest_backed_server_provisions_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shipped manifest server needs no approval, no identity, no pin."""
    manifest_server = ServerConfig(
        name="shipped",
        description="A shipped server",
        keywords=["shipped"],
        install={p: ["npx", "-y", "@shipped/server"] for p in PLATFORMS},
        command="npx",
        args=["-y", "@shipped/server"],
    )
    gateway, jobs = _gateway(
        monkeypatch,
        _empty_policy(tmp_path),
        manifest_servers={"shipped": manifest_server},
    )

    result = await gateway.provision({"server_name": "shipped"})

    assert result.ok is True
    assert result.status == "started"
    assert jobs.calls == [(["npx", "-y", "@shipped/server"], manifest_server)]
    assert not package_approvals_path().exists()

    decision = evaluate_provision(
        manifest_server, None, source="manifest", policy=_empty_policy(tmp_path)
    )
    assert decision == ProvisionDecision(
        allowed=True, reason="manifest_backed", remedy=None, identity=None
    )


@pytest.mark.asyncio
async def test_source_is_taken_from_the_lookup_path_not_from_server_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: dict[str, str]
) -> None:
    registry["sneaky-mcp"] = "1.0.0"
    gateway, jobs = _gateway(monkeypatch, _empty_policy(tmp_path))
    await gateway.register_discovered_server(
        {"server_name": "sneaky", "package": "sneaky-mcp"}
    )

    # Dress the discovered config up as a manifest server in every field an
    # agent-influenced config carries.
    stored = gateway._discovered_server_configs["sneaky"]
    stored.declared_capabilities = ["manifest"]
    stored.source = "manifest"
    stored.status = "manifest"

    seen: list[str] = []
    real = handlers_module.evaluate_provision

    def spy(*args: Any, **kwargs: Any) -> ProvisionDecision:
        seen.append(kwargs["source"])
        return real(*args, **kwargs)

    monkeypatch.setattr(handlers_module, "evaluate_provision", spy)
    result = await gateway.provision({"server_name": "sneaky"})

    assert seen == ["discovered"]
    assert result.ok is False
    assert jobs.calls == []

    # The gate has no default source and never reads one off the config.
    with pytest.raises(TypeError):
        evaluate_provision(stored, None, policy=_empty_policy(tmp_path))  # type: ignore[call-arg]
    decision = evaluate_provision(
        stored,
        _identity("sneaky-mcp", "1.0.0"),
        source="discovered",
        policy=_empty_policy(tmp_path),
    )
    assert (decision.allowed, decision.reason) == (False, "not_approved")

    # A real manifest lookup is what yields "manifest".
    shipped = _config(["-y", "shipped"], name="shipped")
    gateway2, jobs2 = _gateway(
        monkeypatch, _empty_policy(tmp_path), manifest_servers={"shipped": shipped}
    )
    seen.clear()
    monkeypatch.setattr(handlers_module, "evaluate_provision", spy)
    assert (await gateway2.provision({"server_name": "shipped"})).ok is True
    assert seen == ["manifest"]


# ---------------------------------------------------------------------------
# EC-PKGID-2 -- policy on package identifiers composes with approvals
# ---------------------------------------------------------------------------


def test_policy_denylist_blocks_an_approved_package(tmp_path: Path) -> None:
    identity = _identity()
    approve_package(identity)
    deny = _write_policy(tmp_path, "packages:\n  denylist:\n    - example-*\n")

    for source in ("discovered", "configured", "manifest"):
        decision = evaluate_provision(
            _config(["-y", "example-mcp@1.2.3"]), identity, source=source, policy=deny
        )
        assert (decision.allowed, decision.reason) == (False, "denied"), source
        assert decision.remedy is not None
        # Approving again cannot help; the remedy points at the policy.
        assert "approve-package" not in decision.remedy
        assert "policy" in decision.remedy
        assert "denylist" in decision.remedy


@pytest.mark.asyncio
async def test_policy_package_allowlist_permits_provision_without_a_recorded_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: dict[str, str]
) -> None:
    policy = _write_policy(tmp_path, "packages:\n  allowlist:\n    - '@acme/*'\n")
    registry["@acme/tool-mcp"] = "3.1.0"
    gateway, jobs = _gateway(monkeypatch, policy)
    await gateway.register_discovered_server(
        {"server_name": "acme", "package": "@acme/tool-mcp"}
    )

    result = await gateway.provision({"server_name": "acme"})

    assert result.ok is True, result.message
    assert result.status == "started"
    assert [argv for argv, _ in jobs.calls] == [["npx", "-y", "@acme/tool-mcp@3.1.0"]]
    assert not package_approvals_path().exists()

    decision = evaluate_provision(
        _config(["-y", "@acme/tool-mcp@3.1.0"]),
        _identity("@acme/tool-mcp", "3.1.0"),
        source="discovered",
        policy=policy,
    )
    assert (decision.allowed, decision.reason) == (True, "policy_allowed")


@pytest.mark.asyncio
async def test_package_denylist_beats_a_recorded_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: dict[str, str]
) -> None:
    """Name allowlisted, package allowlisted AND approved, package denied: deny."""
    policy = _write_policy(
        tmp_path,
        "servers:\n  allowlist:\n    - internal-approved-tool\n"
        "packages:\n"
        f"  allowlist:\n    - {EVIL}\n"
        f"  denylist:\n    - {EVIL}\n",
    )
    registry[EVIL] = EVIL_VERSION
    approve_package(_identity(EVIL, EVIL_VERSION))
    gateway, jobs = _gateway(monkeypatch, policy)
    await gateway.register_discovered_server(
        {"server_name": "internal-approved-tool", "package": EVIL}
    )

    result = await gateway.provision({"server_name": "internal-approved-tool"})

    assert result.ok is False
    assert jobs.calls == []
    assert "denylist" in result.message
    assert "approve-package" not in result.message


# ---------------------------------------------------------------------------
# EC-PKGID-5 -- resolve and pin at registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_resolvable_registration_records_the_version_and_pins_the_install_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: dict[str, str]
) -> None:
    registry["@scope/pkg"] = "1.4.2"
    gateway, _ = _gateway(monkeypatch, _empty_policy(tmp_path))

    out = await gateway.register_discovered_server(
        {"server_name": "scoped", "package": "@scope/pkg"}
    )

    pinned = ["npx", "-y", "@scope/pkg@1.4.2"]
    assert out.ok is True
    assert out.registered is True
    assert out.install_command == pinned
    assert "@scope/pkg@1.4.2" in out.message

    stored = gateway._discovered_server_configs["scoped"]
    assert set(stored.install) == set(PLATFORMS)
    for platform in PLATFORMS:
        assert stored.install[platform] == pinned, platform
    # The argv `client/manager.py` actually spawns is pinned too.
    assert stored.command == "npx"
    assert stored.args == ["-y", "@scope/pkg@1.4.2"]

    identity = gateway._discovered_server_identities["scoped"]
    assert identity.name == "@scope/pkg"
    assert identity.resolved_version == "1.4.2"
    assert identity.integrity == "sha512-@scope/pkg-1.4.2"


@pytest.mark.asyncio
async def test_an_unresolvable_registration_is_refused_at_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: dict[str, str]
) -> None:
    gateway, jobs = _gateway(monkeypatch, _empty_policy(tmp_path))

    out = await gateway.register_discovered_server(
        {"server_name": "ghost", "package": "not-in-the-registry"}
    )

    assert out.ok is False
    assert out.registered is False
    assert out.install_command is None
    assert "not-in-the-registry" in out.message
    assert "registry" in out.message
    assert "ghost" not in gateway._discovered_server_configs
    assert "ghost" not in gateway._discovered_server_identities

    after = await gateway.provision({"server_name": "ghost"})
    assert after.ok is False
    assert jobs.calls == []


@pytest.mark.asyncio
async def test_a_resolved_version_that_fails_validation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registry data is semi-trusted: nothing unvalidated is composed into argv."""
    gateway, jobs = _gateway(monkeypatch, _empty_policy(tmp_path))
    hostile = [
        _identity("pkg", "1.0.0;$(id)"),
        _identity("pkg", "1.0.0 --registry=https://evil"),
        _identity("pkg", "-1"),
        _identity("pkg", "latest"),
        _identity("pkg", "^1.0.0"),
        _identity("pkg", ""),
        _identity("other-pkg", "1.0.0"),  # the registry named another package
        PackageIdentity("npm", "-g", "1.0.0", None),
    ]
    for identity in hostile:
        monkeypatch.setattr(
            handlers_module, "resolve_package_identity", lambda _spec, i=identity: i
        )
        out = await gateway.register_discovered_server(
            {"server_name": "hostile", "package": "pkg"}
        )
        assert out.registered is False, identity
        assert out.install_command is None
        assert "hostile" not in gateway._discovered_server_configs
        assert "hostile" not in gateway._discovered_server_identities

    assert (await gateway.provision({"server_name": "hostile"})).ok is False
    assert jobs.calls == []

    # The gate refuses such an identity on its own, too, whatever the argv says.
    decision = evaluate_provision(
        _config(["-y", "pkg@1.0.0;$(id)"]),
        _identity("pkg", "1.0.0;$(id)"),
        source="discovered",
        policy=cast(Any, _StubPolicy("allowed")),
    )
    assert (decision.allowed, decision.reason) == (False, "unresolvable_identity")


# ---------------------------------------------------------------------------
# EC-PKGID-6 -- the operator's round trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_approved_package_provisions_after_the_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: dict[str, str]
) -> None:
    registry["useful-mcp"] = "2.0.1"
    gateway, jobs = _gateway(monkeypatch, _empty_policy(tmp_path))
    await gateway.register_discovered_server(
        {"server_name": "useful", "package": "useful-mcp"}
    )

    refused = await gateway.provision({"server_name": "useful"})
    assert refused.ok is False
    assert jobs.calls == []

    # Exactly what the operator would do: run the command the refusal named.
    command = "pmcp trust approve-package useful-mcp@2.0.1"
    assert command in refused.message
    from pmcp.cli import async_main, parse_args
    from unittest.mock import patch

    with patch("sys.argv", ["mcp-gateway", *command.split()[1:]]):
        args = parse_args()
    await async_main(args)

    started = await gateway.provision({"server_name": "useful"})
    assert started.ok is True, started.message
    assert started.status == "started"
    assert [argv for argv, _ in jobs.calls] == [["npx", "-y", "useful-mcp@2.0.1"]]

    # The approval is for that version only: a re-registration that resolves
    # a newer release is refused again.
    registry["useful-mcp"] = "2.0.2"
    await gateway.register_discovered_server(
        {"server_name": "useful", "package": "useful-mcp"}
    )
    again = await gateway.provision({"server_name": "useful"})
    assert again.ok is False
    assert "useful-mcp@2.0.2" in again.message
    assert len(jobs.calls) == 1


# ---------------------------------------------------------------------------
# Decision order, exhaustively
# ---------------------------------------------------------------------------


def _reference_order(
    source: str,
    identity: PackageIdentity | None,
    pinned: bool,
    approved: bool,
    verdict: str,
) -> str:
    if identity is not None and verdict == "denied":
        return "denied"
    if source == "manifest":
        return "manifest_backed"
    if identity is None:
        return "unresolvable_identity"
    if not pinned:
        return "unpinned_configured_argv"
    if approved:
        return "package_approved"
    if verdict == "allowed":
        return "policy_allowed"
    return "not_approved"


def test_every_combination_follows_the_frozen_decision_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity()
    allowing = {"manifest_backed", "package_approved", "policy_allowed"}
    for source, ident, pinned, approved, verdict in itertools.product(
        ("manifest", "configured", "discovered"),
        (None, identity),
        (True, False),
        (True, False),
        ("denied", "allowed", "unspecified"),
    ):
        monkeypatch.setattr(
            "pmcp.provision_gate.is_package_approved", lambda _i, a=approved: a
        )
        args = ["-y", "example-mcp@1.2.3"] if pinned else ["-y", "example-mcp"]
        decision = evaluate_provision(
            _config(args),
            ident,
            source=cast(Any, source),
            policy=cast(Any, _StubPolicy(verdict)),
        )
        case = (source, ident, pinned, approved, verdict)
        expected = _reference_order(source, ident, pinned, approved, verdict)
        assert decision.reason == expected, case
        assert decision.allowed is (expected in allowing), case
        assert (decision.remedy is None) is decision.allowed, case
        assert decision.identity == ident, case


def test_a_policy_that_raises_denies_even_a_manifest_server() -> None:
    class _Exploding:
        def evaluate_package_policy(self, identity: Any) -> str:
            raise RuntimeError("policy unreadable")

    for source in ("manifest", "configured", "discovered"):
        decision = evaluate_provision(
            _config(["-y", "example-mcp@1.2.3"]),
            _identity(),
            source=cast(Any, source),
            policy=cast(Any, _Exploding()),
        )
        assert decision.allowed is False, source


# ---------------------------------------------------------------------------
# Operator-facing strings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shell", ["bash", "zsh", "sh"])
def test_a_pasted_remedy_expands_nothing(tmp_path: Path, shell: str) -> None:
    """A remedy built from registry-controlled values is inert when pasted.

    The values here are deliberately NOT valid package names or versions: the
    validators upstream would refuse them, and this proves the remedy does not
    depend on that.
    """
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} not installed")
    name = "x'$(touch CANARY1)'\"`touch CANARY2`\"‮"
    version = "1.0.0;touch CANARY3\n$HOME\x1b[2J​"
    remedy = approve_package_command(_identity(name, version))

    prefix = "pmcp trust approve-package "
    assert remedy.startswith(prefix)
    assert remedy.isprintable(), "the remedy must hold no terminal controls"

    tail = remedy[len(prefix) :]
    out = subprocess.run(
        [shell, "-c", f"printf '%s|' {tail}"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    escaped = "".join(
        ch if ch.isprintable() else repr(ch)[1:-1] for ch in f"{name}@{version}"
    )
    assert out == f"{escaped}|"  # one argument, literally
    assert sorted(p.name for p in tmp_path.iterdir()) == []

    # Every deny remedy that carries an UNVALIDATED value embeds it only in that
    # quoted form. (`unpinned_configured_argv` is not listed: rule 3 refuses an
    # identity that fails validation first, so its remedy never sees one.)
    for decision, value in (
        (
            evaluate_provision(
                _config(["-y", "pkg"], package=name),
                None,
                source="discovered",
                policy=cast(Any, _StubPolicy("unspecified")),
            ),
            name,
        ),
        (
            evaluate_provision(
                _config(["-y", "pkg"]),
                _identity(name, version),
                source="discovered",
                policy=cast(Any, _StubPolicy("denied")),
            ),
            name,
        ),
    ):
        assert decision.remedy is not None
        assert decision.remedy.isprintable()
        quoted = operator_safe(value)
        assert quoted.startswith("'")
        assert quoted in decision.remedy
        assert "$(touch" not in decision.remedy.replace(quoted, "")


@pytest.mark.asyncio
async def test_a_hostile_server_name_in_the_refusal_is_quoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: dict[str, str]
) -> None:
    registry["pkg-mcp"] = "1.0.0"
    gateway, jobs = _gateway(monkeypatch, _empty_policy(tmp_path))
    name = "srv$(id)\x1b]0;pwn\x07"
    await gateway.register_discovered_server(
        {"server_name": name, "package": "pkg-mcp"}
    )
    result = await gateway.provision({"server_name": name})
    assert result.ok is False
    assert jobs.calls == []
    assert result.message.isprintable()


# ---------------------------------------------------------------------------
# The registry lookup does not stall the gateway
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registration_resolves_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop_thread = threading.get_ident()
    fetch_threads: list[int] = []

    def slow_fetch(name: str) -> dict[str, Any]:
        fetch_threads.append(threading.get_ident())
        time.sleep(0.5)  # a blocking socket read
        return _packument(name, "1.0.0")

    monkeypatch.setattr(package_identity, "_fetch_packument", slow_fetch)
    gateway, _ = _gateway(monkeypatch, _empty_policy(tmp_path))

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        out = await gateway.register_discovered_server(
            {"server_name": "slow", "package": "slow-mcp"}
        )
    finally:
        task.cancel()

    assert out.registered is True
    assert fetch_threads and fetch_threads[0] != loop_thread
    # A blocked loop would tick ~0 times during the half-second fetch.
    assert ticks >= 10, ticks


@pytest.mark.asyncio
async def test_a_hung_registry_lookup_is_bounded_by_the_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def hung_fetch(name: str) -> dict[str, Any]:
        release.wait(30)  # a registry that never answers inside the socket timeout
        return _packument(name, "1.0.0")

    monkeypatch.setattr(package_identity, "_fetch_packument", hung_fetch)
    monkeypatch.setattr(handlers_module, "_REGISTRATION_RESOLVE_TIMEOUT_SECONDS", 0.3)
    gateway, jobs = _gateway(monkeypatch, _empty_policy(tmp_path))

    started = time.monotonic()
    try:
        out = await gateway.register_discovered_server(
            {"server_name": "hung", "package": "hung-mcp"}
        )
    finally:
        release.set()
    elapsed = time.monotonic() - started

    assert elapsed < 5, elapsed
    assert out.registered is False
    assert "hung-mcp" in out.message
    assert "hung" not in gateway._discovered_server_configs
    assert jobs.calls == []


# ---------------------------------------------------------------------------
# The lifecycle door: connect / restart / update resolve discovered configs too
# ---------------------------------------------------------------------------
#
# `provision` is not the only tool that spawns a registered server. connect_server
# and restart_server spawn the resolved argv, and update_server resolves through
# the same `_resolve_lifecycle_config` before its probe spawns `npx <pkg>`.
# Without the gate there, S-01 survives the provision fix intact: register, then
# connect. Each test below arranges the review's shape -- the server NAME
# allowlisted -- and asserts both the refusal and that nothing was spawned.


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Record (and refuse) every subprocess spawn attempted during a test."""
    calls: list[tuple[Any, ...]] = []

    async def refuse_spawn(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError(f"a subprocess was spawned: {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse_spawn)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", refuse_spawn)
    return calls


async def _registered_unapproved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: dict[str, str],
    *,
    env_vars: list[str] | None = None,
) -> tuple[GatewayTools, _MinimalClientManager]:
    policy = _write_policy(
        tmp_path, "servers:\n  allowlist:\n    - internal-approved-tool\n"
    )
    registry[EVIL] = EVIL_VERSION
    gateway, _ = _gateway(monkeypatch, policy)
    registered = await gateway.register_discovered_server(
        {
            "server_name": "internal-approved-tool",
            "package": EVIL,
            "env_vars": env_vars or [],
        }
    )
    assert registered.registered is True
    return gateway, cast(_MinimalClientManager, gateway._client_manager)


@pytest.mark.asyncio
async def test_connect_server_does_not_spawn_an_unapproved_discovered_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: dict[str, str],
    spawns: list[tuple[Any, ...]],
) -> None:
    # A declared credential that is NOT set: the refusal must come from the
    # package gate, before the credential check could prompt for auth.
    monkeypatch.delenv("EVIL_TOKEN", raising=False)
    gateway, manager = await _registered_unapproved(
        tmp_path, monkeypatch, registry, env_vars=["EVIL_TOKEN"]
    )

    result = await gateway.connect_server({"server_name": "internal-approved-tool"})

    assert result.ok is False
    assert manager.connected == []
    assert spawns == []
    assert f"pmcp trust approve-package {EVIL}@{EVIL_VERSION}" in result.message
    assert result.errors == [result.message]
    assert result.auth_state == "unknown"  # not a policy verdict, not missing auth
    assert result.missing_env_vars == []


@pytest.mark.asyncio
async def test_restart_server_does_not_spawn_an_unapproved_discovered_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: dict[str, str],
    spawns: list[tuple[Any, ...]],
) -> None:
    gateway, manager = await _registered_unapproved(tmp_path, monkeypatch, registry)

    result = await gateway.restart_server({"server_name": "internal-approved-tool"})

    assert result.ok is False
    assert manager.connected == []
    assert spawns == []
    assert EVIL in result.message
    assert "pmcp trust approve-package" in result.message


@pytest.mark.asyncio
async def test_update_server_does_not_probe_an_unapproved_discovered_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: dict[str, str],
    spawns: list[tuple[Any, ...]],
) -> None:
    gateway, manager = await _registered_unapproved(tmp_path, monkeypatch, registry)
    probes: list[list[str]] = []

    async def recording_probe(command: list[str], env: Any = None) -> tuple[bool, str]:
        probes.append(command)
        return (True, "")

    monkeypatch.setattr(gateway, "_run_update_probe_command", recording_probe)

    result = await gateway.update_server({"server_name": "internal-approved-tool"})

    assert result.ok is False
    assert result.restarted is False
    assert probes == []
    assert manager.connected == []
    assert spawns == []
    assert f"pmcp trust approve-package {EVIL}@{EVIL_VERSION}" in result.message


@pytest.mark.asyncio
async def test_an_approved_pinned_discovered_server_still_connects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: dict[str, str],
    spawns: list[tuple[Any, ...]],
) -> None:
    gateway, manager = await _registered_unapproved(tmp_path, monkeypatch, registry)
    approve_package(_identity(EVIL, EVIL_VERSION))

    result = await gateway.connect_server({"server_name": "internal-approved-tool"})

    assert result.ok is True, result.message
    assert len(manager.connected) == 1
    spawned = manager.connected[0].config
    assert spawned.command == "npx"
    assert spawned.args == ["-y", f"{EVIL}@{EVIL_VERSION}"]


@pytest.mark.asyncio
async def test_the_lifecycle_gate_keeps_existing_denials_and_spares_disconnect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: dict[str, str],
    spawns: list[tuple[Any, ...]],
) -> None:
    # A package denylist refusal is a policy verdict, and says so.
    policy = _write_policy(tmp_path, f"packages:\n  denylist:\n    - {EVIL}\n")
    registry[EVIL] = EVIL_VERSION
    gateway, _ = _gateway(monkeypatch, policy)
    await gateway.register_discovered_server({"server_name": "d", "package": EVIL})
    denied = await gateway.connect_server({"server_name": "d"})
    assert denied.ok is False
    assert denied.auth_state == "policy_denied"
    assert "denylist" in denied.message

    # A server-name denial is still reported exactly as before the gate existed.
    (tmp_path / "names").mkdir()
    name_policy = _write_policy(
        tmp_path / "names", "servers:\n  denylist:\n    - blocked\n"
    )
    gateway2, _ = _gateway(monkeypatch, name_policy)
    manager2 = cast(_MinimalClientManager, gateway2._client_manager)
    await gateway2.register_discovered_server(
        {"server_name": "blocked", "package": EVIL}
    )
    blocked = await gateway2.connect_server({"server_name": "blocked"})
    assert blocked.message == "Server 'blocked' is blocked by policy."
    assert blocked.auth_state == "policy_denied"
    assert manager2.connected == []

    # Disconnect spawns nothing, so the gate does not stand in its way: an
    # unapproved server that is somehow running can always be stopped.
    gateway3, manager3 = await _registered_unapproved(
        tmp_path / "names", monkeypatch, registry
    )
    stopped = await gateway3.disconnect_server(
        {"server_name": "internal-approved-tool"}
    )
    assert stopped.ok is True, stopped.message
    assert manager3.disconnected == ["internal-approved-tool"]
    assert spawns == []


@pytest.mark.asyncio
async def test_a_manifest_server_lifecycle_does_not_consult_the_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spawns: list[tuple[Any, ...]],
) -> None:
    """Only the discovered lookup is gated; a manifest hit is left as it was."""
    shipped = _config(["-y", "@shipped/server"], name="shipped")
    gateway, _ = _gateway(
        monkeypatch, _empty_policy(tmp_path), manifest_servers={"shipped": shipped}
    )
    manager = cast(_MinimalClientManager, gateway._client_manager)
    consulted: list[str] = []

    def spy(*args: Any, **kwargs: Any) -> ProvisionDecision:
        consulted.append(kwargs["source"])
        raise AssertionError("the lifecycle gate ran for a manifest server")

    monkeypatch.setattr(handlers_module, "evaluate_provision", spy)

    result = await gateway.connect_server({"server_name": "shipped"})

    assert result.ok is True, result.message
    assert consulted == []
    assert [c.name for c in manager.connected] == ["shipped"]
