"""EC-SEAL-4: an operator who has none of v13 sees none of v13 (SL-4).

Five phases added gates. This file is the regression half of all of them: the
proof that the ordinary path -- a fresh operator, no trust store, no package
approvals, no project files, no guidance config -- still works exactly as it did
before any of it landed. Assumption 5 of the roadmap says it plainly: a
default-deny that silently breaks a working setup is a failed phase, not a
secured one.

**The premise is asserted before it is relied on.**
``test_a_fresh_home_holds_no_trust_store_and_no_package_approvals`` runs first
in spirit and in fact: ``tests/conftest.py``'s autouse ``isolate_trust_store``
redirects ``HOME`` and **approves nothing**, which is exactly the fresh
operator -- but a fixture that silently stopped redirecting would make every
other test here vacuously green, because a gate consulting the developer's own
approvals would allow everything. So the absence of the artifacts is an
assertion, not an assumption.

**The gates are driven, not imagined.** The three lifecycle tests go through
``gateway.provision``, ``gateway.connect_server`` and ``gateway.restart_server``
for a server taken out of the **shipped** ``manifest.yaml``, not a synthetic
``ServerConfig`` shaped to pass. A synthetic entry proves the gate's logic; only
a shipped one proves that what pmcp actually ships still starts.

**Nothing here reaches the network or spawns a process.** The job manager and
client manager are recording stand-ins, and ``_no_spawn`` fails any test that
gets past them -- modelled on ``tests/conftest.py``'s ``_no_live_npm_registry``
(``:168``), which stays active underneath and covers the registry.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from pmcp import trust_store
from pmcp.config import loader as config_loader
from pmcp.config.loader import load_configs
from pmcp.manifest import loader as manifest_loader
from pmcp.manifest.loader import Manifest, ServerConfig, load_manifest
from pmcp.package_approvals import package_approvals_path
from pmcp.policy import policy as policy_module
from pmcp.policy.policy import PolicyManager
from pmcp.provision_gate import _manifest_package_names, evaluate_provision
from pmcp.tools import handlers as handlers_module
from pmcp.tools.handlers import GatewayTools

#: The shipped manifest, read from the packaged file with no overlays at all.
#: `load_manifest()` with no argument merges user/project/env overlays; passing
#: the path explicitly is what makes this "what pmcp ships" rather than "what
#: this machine happens to have".
SHIPPED_MANIFEST_PATH = Path(manifest_loader.__file__).parent / "manifest.yaml"


def _shipped_manifest() -> Manifest:
    return load_manifest(SHIPPED_MANIFEST_PATH)


def _a_shipped_npx_server() -> ServerConfig:
    """One shipped, npx-backed, credential-free entry, chosen deterministically.

    Deterministic because a randomly-picked entry makes a failure unreproducible;
    npx-backed because that is the shape the package-identity gate judges, so a
    remote or binary entry would pass by never reaching rule 1 at all.
    """
    manifest = _shipped_manifest()
    for name in sorted(manifest.servers):
        server = manifest.servers[name]
        if server.command == "npx" and not server.requires_api_key:
            return server
    raise AssertionError(
        "the shipped manifest has no credential-free npx server; this test's "
        "subject no longer exists and the test must be re-aimed"
    )


# --------------------------------------------------------------------------- #
# Fixtures.
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_spawn(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """Fail any test that gets far enough to start a real process."""
    calls: list[Any] = []

    async def _refuse(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError(f"a subprocess was spawned: {args}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _refuse)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _refuse)
    yield calls
    assert calls == [], f"a fresh-operator case spawned a process: {calls}"


class _RecordingClientManager:
    """Records the configs the lifecycle handlers would have spawned."""

    def __init__(self) -> None:
        self.connected: list[Any] = []
        self.restarted: list[Any] = []

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

    async def connect_server(self, config: Any) -> list[str]:
        self.connected.append(config)
        return []

    async def restart_server(
        self, config: Any, force: bool = False
    ) -> tuple[bool, int, list[str]]:
        self.restarted.append(config)
        return (True, 0, [])

    async def disconnect_server(
        self, name: str, force: bool = False
    ) -> tuple[bool, int, str | None]:
        return (True, 0, None)

    def get_active_tasks(self, name: str | None = None) -> list[Any]:
        return []


class _RecordingJobManager:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], ServerConfig]] = []

    async def start_install(self, server_config: ServerConfig, platform: str) -> str:
        self.calls.append(
            (list(server_config.install.get(platform) or []), server_config)
        )
        return f"job-{len(self.calls)}"


@pytest.fixture
def fresh_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[GatewayTools, _RecordingClientManager, _RecordingJobManager]:
    """A gateway holding the shipped manifest and the default (empty) policy.

    ``PolicyManager(policy_path=None)`` under a fresh ``HOME`` is what a fresh
    operator has: no ``~/.claude/gateway-policy.yaml``, therefore no allowlist,
    no denylist, and no package rules of any kind.
    """
    manifest = _shipped_manifest()
    jobs = _RecordingJobManager()
    monkeypatch.setattr(handlers_module, "load_manifest", lambda: manifest)
    monkeypatch.setattr(handlers_module, "load_configs", lambda **_: [])
    monkeypatch.setattr(handlers_module, "get_job_manager", lambda: jobs)

    manager = _RecordingClientManager()
    gateway = GatewayTools(
        client_manager=cast(Any, manager),
        policy_manager=PolicyManager(policy_path=None),
    )
    cast(Any, gateway)._platform = "linux"
    return gateway, manager, jobs


# --------------------------------------------------------------------------- #
# The premise.
# --------------------------------------------------------------------------- #


def test_a_fresh_home_holds_no_trust_store_and_no_package_approvals(
    isolate_trust_store: Path,
) -> None:
    """Assert the baseline is a baseline, before six other tests rely on it.

    Both stores are user-scoped under ``HOME``. If the autouse redirect ever
    stopped working, these paths would resolve into the developer's real home,
    the records there would answer the gates, and every lifecycle test below
    would pass for a reason that has nothing to do with a fresh operator.
    """
    store = trust_store.trust_store_path()
    approvals = package_approvals_path()

    assert store.is_relative_to(isolate_trust_store.resolve()), store
    assert approvals.is_relative_to(isolate_trust_store.resolve()), approvals
    assert not store.exists(), f"a fresh home already holds a trust store: {store}"
    assert not approvals.exists(), (
        f"a fresh home already holds package approvals: {approvals}"
    )
    assert trust_store.list_records() == []


# --------------------------------------------------------------------------- #
# The lifecycle, for a server pmcp actually ships.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_manifest_backed_server_provisions_with_no_trust_store(
    fresh_gateway: tuple[GatewayTools, _RecordingClientManager, _RecordingJobManager],
) -> None:
    """`gateway.provision` starts a shipped server with nothing approved."""
    gateway, _, jobs = fresh_gateway
    server = _a_shipped_npx_server()

    result = await gateway.provision({"server_name": server.name})

    assert result.ok is True, result.message
    assert result.status == "started"
    assert jobs.calls == [(list(server.install["linux"]), server)]
    # And the run left no record behind: provisioning a shipped server is not an
    # operator decision, so nothing should have been written to either store.
    assert not package_approvals_path().exists()
    assert not trust_store.trust_store_path().exists()


@pytest.mark.asyncio
async def test_a_manifest_backed_server_connects_with_no_trust_store(
    fresh_gateway: tuple[GatewayTools, _RecordingClientManager, _RecordingJobManager],
) -> None:
    """`gateway.connect_server` spawns the shipped argv unchanged."""
    gateway, manager, _ = fresh_gateway
    server = _a_shipped_npx_server()

    result = await gateway.connect_server({"server_name": server.name})

    assert result.ok is True, result.message
    assert [config.name for config in manager.connected] == [server.name]
    spawned = manager.connected[0].config
    assert (spawned.command, list(spawned.args)) == (server.command, list(server.args))
    assert not package_approvals_path().exists()


@pytest.mark.asyncio
async def test_a_manifest_backed_server_restarts_with_no_trust_store(
    fresh_gateway: tuple[GatewayTools, _RecordingClientManager, _RecordingJobManager],
) -> None:
    """`gateway.restart_server` re-spawns the shipped argv unchanged."""
    gateway, manager, _ = fresh_gateway
    server = _a_shipped_npx_server()

    result = await gateway.restart_server({"server_name": server.name})

    assert result.ok is True, result.message
    assert [config.name for config in manager.restarted] == [server.name]
    spawned = manager.restarted[0].config
    assert (spawned.command, list(spawned.args)) == (server.command, list(server.args))
    assert not package_approvals_path().exists()


# --------------------------------------------------------------------------- #
# The consent side: a gate that fires with nothing to gate is a behaviour change.
# --------------------------------------------------------------------------- #


def test_no_project_file_means_no_consent_gate_is_consulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With no project files, the consent gate is never asked and says nothing.

    The spy is installed on each **consumer** module, never on
    ``pmcp.project_consent`` itself: all three consumers do
    ``from pmcp.project_consent import read_and_gate``, so the name they call is
    bound at import and patching the definition site would leave the real
    function in place -- a spy that records nothing and a test that proves
    nothing.

    The second half is a positive control, and it is what makes the first half
    an assertion rather than a coincidence: with all three project files present
    the same spy, over the same call, records all three. Without it, a reader
    could not tell "the gate was not consulted" from "the spy was never wired
    up".
    """
    consulted: list[tuple[str, Path]] = []

    def _install_spy() -> None:
        for module, label in (
            (manifest_loader, "manifest.loader"),
            (config_loader, "config.loader"),
            (policy_module, "policy.policy"),
        ):
            real = module.read_and_gate

            def _record(
                path: Path, kind: str, _real: Any = real, _label: str = label
            ) -> Any:
                consulted.append((_label, path))
                return _real(path, kind)

            monkeypatch.setattr(module, "read_and_gate", _record)

    project = tmp_path / "plain-checkout"
    project.mkdir()
    assert not (project / ".mcp.json").exists()
    assert not (project / ".mcp-gateway-policy.yaml").exists()
    assert not (project / ".pmcp" / "manifest.yaml").exists()
    monkeypatch.chdir(project)
    _install_spy()

    with caplog.at_level(logging.WARNING):
        manifest = load_manifest()
        policy = PolicyManager(policy_path=None)
        configs = load_configs(project_root=project, user_config_paths=[])

    assert consulted == []
    assert manifest.servers, "the shipped manifest did not load at all"
    assert configs == []
    assert policy.evaluate_package_policy(None) == "unspecified"
    assert [
        record.getMessage()
        for record in caplog.records
        if "To use it, run:" in record.getMessage()
    ] == []

    # Positive control: the same spy, the same calls, with the files present.
    checkout = tmp_path / "repo-checkout"
    (checkout / ".pmcp").mkdir(parents=True)
    (checkout / ".pmcp" / "manifest.yaml").write_text("servers: {}\n", encoding="utf-8")
    (checkout / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
    (checkout / ".mcp-gateway-policy.yaml").write_text(
        "servers: {}\n", encoding="utf-8"
    )
    (checkout / ".git").mkdir()
    monkeypatch.chdir(checkout)

    load_manifest()
    PolicyManager(policy_path=None)
    load_configs(project_root=checkout, user_config_paths=[])

    assert {label for label, _ in consulted} == {
        "manifest.loader",
        "config.loader",
        "policy.policy",
    }, consulted


def test_a_fresh_operator_sees_no_new_startup_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The whole startup read, from an empty checkout, emits no pmcp warning.

    Broader than the consent check above on purpose: it does not ask whether a
    particular gate fired, it asks whether the operator is told anything new at
    all. Any WARNING or worse from a ``pmcp.*`` logger is a line that did not
    exist before v13 for this operator, and a line they would have to learn to
    ignore.

    A positive control follows, for the reason it always does here: a capture
    that silently caught nothing would make "no warnings" the same result as "no
    logging". The operator WITH a repository-supplied file must see the warning
    the fresh one does not.
    """
    project = tmp_path / "empty-checkout"
    project.mkdir()
    monkeypatch.chdir(project)

    def _pmcp_warnings() -> list[str]:
        return [
            f"{record.name}: {record.getMessage()}"
            for record in caplog.records
            if record.levelno >= logging.WARNING and record.name.startswith("pmcp")
        ]

    with caplog.at_level(logging.WARNING):
        load_manifest()
        PolicyManager(policy_path=None)
        load_configs(project_root=project, user_config_paths=[])

    assert _pmcp_warnings() == [], _pmcp_warnings()

    # Positive control: one repository-supplied file, one warning.
    checkout = tmp_path / "repo-checkout"
    checkout.mkdir()
    (checkout / ".mcp.json").write_text(
        '{"mcpServers": {"repo-server": {"command": "node", "args": ["evil.js"]}}}',
        encoding="utf-8",
    )
    monkeypatch.chdir(checkout)

    with caplog.at_level(logging.WARNING):
        load_configs(project_root=checkout, user_config_paths=[])

    assert any("To use it, run:" in message for message in _pmcp_warnings()), (
        _pmcp_warnings()
    )


# --------------------------------------------------------------------------- #
# PKGID's one documented exception, restated as a fresh-operator property.
# --------------------------------------------------------------------------- #


def test_every_shipped_manifest_entry_is_determinable_without_a_denylist() -> None:
    """Nothing pmcp ships is refused as undetermined on a default configuration.

    PKGID amendment 9's G1 property, from the fresh operator's side. Two
    assertions, because the weaker one alone would be vacuous: with no
    ``packages.denylist`` in force the gate does not even *look* at whether an
    entry's argv could be read, so "it was allowed" proves nothing about
    determinability. The first assertion is therefore the real one -- every
    shipped entry's npx argv can in fact be placed -- and the second is the
    behaviour that follows from it.
    """
    policy = PolicyManager(policy_path=None)
    assert policy.has_package_denylist() is False, (
        "a fresh operator's default policy carries a packages.denylist; this "
        "test's premise is gone"
    )

    manifest = _shipped_manifest()
    assert manifest.servers, "the shipped manifest is empty"

    undetermined = {
        name: _manifest_package_names(server)[1]
        for name, server in manifest.servers.items()
        if _manifest_package_names(server)[1] is not None
    }
    assert undetermined == {}, (
        "shipped manifest entries whose install argv cannot be read: "
        f"{undetermined}. Each would be refused the moment an operator adds any "
        "packages.denylist."
    )

    refused = {
        name: decision.reason
        for name, server in manifest.servers.items()
        for decision in [
            evaluate_provision(server, None, source="manifest", policy=policy)
        ]
        if not decision.allowed
    }
    assert refused == {}, f"shipped manifest entries refused by the gate: {refused}"
