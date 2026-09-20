"""Composition seams: the holes that exist only BETWEEN two gates that pass.

SEAL SL-3, EC-SEAL-5 (see Consiliency/pmcp#230). Every earlier phase proved its
own gate against its own criteria, and each of those criteria is satisfied by
the shipped code. A composition seam is what is left over: a case that passes
every per-phase criterion and fails at the join, which no per-phase suite can
see by construction, because seeing it needs two phases' arrangements in one
test.

Four seams, driven through real handlers and real loaders:

* **CONSENT x PKGID, the add path (EC-CONSENT-6).** CONSENT refuses a project
  overlay that *replaces* a shipped server; PKGID's default-deny *exempts
  manifest-backed servers*. An unapproved overlay that introduces a server name
  the shipped manifest does not carry therefore has to be refused twice: it must
  not be applied, and the name must not be treated as manifest-backed by
  ``gateway.provision`` or ``gateway.connect_server``. Proving that needs an
  unapproved overlay AND a provisioning attempt in one test; neither phase's
  file has both.
* **TRUST x CONSENT, the shipped approval record (EC-TRUST-5).** TRUST refuses a
  checkout-resident trust store; CONSENT keys approval on content. The
  composition is a repository that ships its own approval record and expects it
  to be believed. ``trust_store_path`` *raises* for such a store
  (``src/pmcp/trust_store.py``), and ``is_approved`` swallows every exception --
  so a caller reads the refusal as "no record", **which is the same answer for
  the wrong reason**. Each of the three tests below therefore carries a control
  that moves the *same store bytes* outside the checkout and shows they grant:
  the refusal is residency, not absence.
* **Two seams inside PKGID's own frozen decision order.** Rule 1 (a
  ``packages.denylist`` entry) must outrank rule 5 (a recorded operator
  approval), or a denylist silently never fires for exactly the packages an
  operator trusted. Rule 4 (the unpinned-argv deny) must stay ahead of both
  allow rules, or an approved server whose argv is a bare ``npx -y pkg``
  re-resolves ``latest`` at spawn -- S-01 again, for trusted servers.
* **EGRESS x PKGID, the install-spawn credential strip.** The seam where this
  phase found a live leak: ``build_install_child_env`` was the only
  ``sanitized_subprocess_env`` caller that omitted the project root, so under
  ``pmcp serve --project X`` started from another directory the install child
  inherited another server's PMCP-stored credential. SL-0 fixed it; the two
  tests here assert the fixed behaviour through the *composition* SL-0's own
  suite does not drive -- the credential is written by the real
  ``gateway.auth_connect``, in both scopes, and read back off the real install
  spawn's recorded child environment.

**Every refusal carries a positive control.** "Nothing was applied" and "nothing
was spawned" are equally true of a gateway that can no longer do anything at
all, so each refusal is paired, in the same test, with the arrangement that must
still succeed: the operator approves the overlay and it applies; the same store
outside the checkout grants; an approved package that is not denylisted
provisions; the pinned argv provisions.

**Nothing here reaches the network or spawns a process.** The guards below are
autouse and modelled on ``tests/conftest.py``'s ``_no_live_npm_registry``;
``test_no_composition_case_in_this_suite_reached_the_network`` makes that a node
id EC-SEAL-5 runs rather than a fixture side effect nobody is required to prove.
Install spawns ARE driven here -- two tests exist to read the child environment
of one -- so ``asyncio.create_subprocess_exec`` is *recorded* rather than
refused, and the assertion that nothing real ran is that every recorded spawn
went through this module's recorder and that no blocking ``subprocess`` door was
used at all.

Module-level functions throughout: the node ids are frozen by
``plans/phase-plan-v13-SEAL.md`` and run verbatim by EC-SEAL-5, and a class would
change every one of them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from pmcp import package_approvals, trust_store
from pmcp.config.loader import load_configs
from pmcp.env_store import reset_pmcp_introduced_keys
from pmcp.manifest import loader as manifest_loader
from pmcp.manifest import package_identity
from pmcp.manifest.installer import JobManager
from pmcp.manifest.loader import ServerConfig, load_manifest
from pmcp.manifest.package_identity import NPM_REGISTRY, PackageIdentity
from pmcp.package_approvals import approve_package
from pmcp.policy import policy as policy_module
from pmcp.policy.policy import PolicyManager
from pmcp.tools import handlers as handlers_module
from pmcp.tools.handlers import GatewayTools
from pmcp.trust_store import TrustStoreError

# --------------------------------------------------------------------------- #
# The arrangement every seam below is built from.
# --------------------------------------------------------------------------- #

#: A server name the shipped manifest does not carry. The add seam is about a
#: name that did not exist, which is exactly what CONSENT's replace-only
#: reproduction cannot reach.
ADDED_SERVER = "repo-added-server"
ADDED_PACKAGE = "totally-arbitrary-evil-package"
ADDED_VERSION = "6.6.6"

#: A second package, approved and never denylisted, for the controls.
LEGIT_PACKAGE = "legit-mcp"
LEGIT_VERSION = "1.2.3"

#: A manifest-backed server that needs no credential and does install, so
#: `provision` routes it straight to `JobManager.start_install` -- the
#: production spawn path the strip tests read the child environment off.
INSTALLABLE_SERVER = "context7"

#: A manifest server that declares a credential, so `gateway.auth_connect` has
#: somewhere real to write one. Its storage key is namespaced, which is the
#: shape the strip has to recognise.
CREDENTIAL_SERVER = "brightdata"
OTHER_CREDENTIAL_SERVER = "github"

PLATFORMS = ("mac", "linux", "wsl", "windows")


# --------------------------------------------------------------------------- #
# Isolation this file declares for itself.
# --------------------------------------------------------------------------- #

#: Every outbound attempt any guard here intercepted, for the whole module run.
#: Module-global on purpose: a per-test list cannot be asserted by a later node
#: id, and EC-SEAL-5 wants that assertion to *be* a node id.
_NETWORK_ATTEMPTS: list[str] = []

#: Every async spawn this module's code made. Recorded, not refused: two tests
#: below exist to read the environment of a real install spawn.
_INTERCEPTED_SPAWNS: list[tuple[list[str], dict[str, str]]] = []

#: Every blocking `subprocess` call. Nothing in these paths may use one, so this
#: list staying empty is part of the isolation proof.
_ESCAPED_PROCESSES: list[list[str]] = []


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Close the npm opener and the socket layer underneath it.

    The named opener is the seam `tests/conftest.py` already chose; this one
    shadows it so the module accumulator sees the attempt too. The socket guard
    is the backstop for a door nobody enumerated, which is the failure mode both
    PKGID amendment 1 and EGRESS amendment 1 describe. It raises ``OSError``, a
    shape these callers already handle, so a leak surfaces as this file's own
    assertion rather than as an unhandled error deep inside a handler.
    """

    def _refuse_open(request: Any, *args: Any, **kwargs: Any) -> Any:
        _NETWORK_ATTEMPTS.append(str(getattr(request, "full_url", request)))
        raise OSError("outbound requests are disabled in this suite")

    def _refuse_connect(self: Any, address: Any, *args: Any, **kwargs: Any) -> Any:
        _NETWORK_ATTEMPTS.append(f"socket.connect{address!r}")
        raise OSError("outbound sockets are disabled in this suite")

    monkeypatch.setattr(package_identity._OPENER, "open", _refuse_open)
    monkeypatch.setattr(socket.socket, "connect", _refuse_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse_connect)
    yield
    assert _NETWORK_ATTEMPTS == [], f"a test reached the network: {_NETWORK_ATTEMPTS}"


class _FakeProcess:
    """What a recorded spawn hands back: enough surface to be awaited and read."""

    returncode = None
    pid = -1
    stdout = None
    stderr = None
    stdin = None

    async def communicate(self, _input: bytes | None = None) -> tuple[bytes, bytes]:
        return (b"", b"this process was never really spawned")

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_real_process(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Record every async spawn with its environment; refuse the blocking doors.

    Recorded rather than refused, which is EGRESS's finding restated: a raiser
    fails somewhere inside the handler and the test then reports whatever the
    handler made of the exception. It is also what the two strip tests need --
    the child environment of the install spawn is their subject, so the spawn
    has to be allowed to happen and be captured rather than blocked.
    """

    async def _record(*cmd: Any, **kwargs: Any) -> Any:
        _INTERCEPTED_SPAWNS.append(
            ([str(part) for part in cmd], dict(kwargs.get("env") or {}))
        )
        return _FakeProcess()

    def _refuse_blocking(*cmd: Any, **kwargs: Any) -> Any:
        _ESCAPED_PROCESSES.append([str(part) for part in cmd])
        raise OSError("blocking subprocess calls are disabled in this suite")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _record)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _record)
    monkeypatch.setattr(subprocess, "Popen", _refuse_blocking)
    monkeypatch.setattr(subprocess, "run", _refuse_blocking)
    # The install monitor would wait on a process that does not exist.
    monkeypatch.setattr(JobManager, "_monitor_install", _no_monitoring)
    yield
    assert _ESCAPED_PROCESSES == [], (
        f"a test used a blocking subprocess door: {_ESCAPED_PROCESSES}"
    )


async def _no_monitoring(self: Any, job: Any) -> None:
    """Stand in for `JobManager._monitor_install`; the process is a fake."""
    return None


@pytest.fixture(autouse=True)
def _no_ambient_project_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """No overlay and no policy may be inherited from the developer's machine.

    ``$PMCP_MANIFEST_PATH`` and the policy discovery paths are ambient inputs,
    and this suite asserts about what a *checkout* contributes. A test that means
    to write the only project source there is opts back in by chdir-ing into its
    own checkout.
    """
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    monkeypatch.setattr(policy_module, "USER_POLICY_PATHS", [])
    monkeypatch.setattr(policy_module, "DEFAULT_POLICY_PATHS", [])


@pytest.fixture(autouse=True)
def _contain_process_global_state() -> Iterator[None]:
    """The PMCP-introduced provenance registry is process-global; contain it."""
    reset_pmcp_introduced_keys()
    yield
    reset_pmcp_introduced_keys()


# --------------------------------------------------------------------------- #
# Harness -- the production objects, with only the process and the client
# connection stood in for. Nothing about a gate is stubbed, and `load_manifest`
# is the REAL one: the overlay walk-up it performs is half of the add seam.
# --------------------------------------------------------------------------- #


class _RecordingClientManager:
    """Records the configs the gateway asked it to connect; connects nothing."""

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

    async def call_tool(self, tool_id: str, args: Any, timeout_ms: int) -> Any:
        return {"content": [{"type": "text", "text": ""}]}

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
    """Stands in for `JobManager`: records every install it is asked to spawn.

    ``start_install`` mirrors the CURRENT production signature explicitly rather
    than taking ``*args, **kwargs``. SL-2 and SL-4 wrote doubles against the
    pre-SL-0 signature and broke on merge; a double that spells the signature out
    fails loudly on the day the real one moves, which is the point of having it.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], ServerConfig]] = []
        self.project_roots: list[Path | None] = []

    async def start_install(
        self,
        server_config: ServerConfig,
        platform: str,
        project_root: Path | None = None,
    ) -> str:
        self.calls.append(
            (list(server_config.install.get(platform) or []), server_config)
        )
        self.project_roots.append(project_root)
        return f"job-{len(self.calls)}"


def _write_policy(path: Path, body: str) -> PolicyManager:
    """An explicit operator policy, which is what `--policy` gives PolicyManager."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return PolicyManager(policy_path=path)


def _gateway(
    monkeypatch: pytest.MonkeyPatch,
    policy: PolicyManager,
    *,
    project_root: Path | None = None,
    job_manager: Any | None = None,
) -> tuple[GatewayTools, _RecordingClientManager, _RecordingJobManager]:
    """A real `GatewayTools`, with the real `load_manifest` left in place.

    Only `load_configs` is pinned (to nothing), so no `.mcp.json` from the
    developer's machine can answer a lookup before the manifest does. The
    manifest loader itself is production's, because its project-overlay walk-up
    IS the seam under test.
    """
    jobs = job_manager if job_manager is not None else _RecordingJobManager()
    manager = _RecordingClientManager()
    monkeypatch.setattr(handlers_module, "load_configs", lambda **_: [])
    # A per-test job manager either way: the recorder for the tests that assert
    # about the install ARGUMENTS, and a real `JobManager` for the two that need
    # the real spawn. Never the process-wide singleton, whose jobs outlive a test.
    monkeypatch.setattr(handlers_module, "get_job_manager", lambda: jobs)
    gateway = GatewayTools(
        client_manager=cast(Any, manager),
        policy_manager=policy,
        project_root=project_root,
    )
    cast(Any, gateway)._platform = "linux"
    return gateway, manager, cast(_RecordingJobManager, jobs)


def _spawns_since(mark: int) -> list[tuple[list[str], dict[str, str]]]:
    """Spawns recorded since ``mark``, so a test asserts about its OWN run.

    Two tests below drive a real install spawn, so the module accumulator is not
    empty for the whole file; a test that means "nothing was spawned here" has to
    say so relative to where it started.
    """
    return _INTERCEPTED_SPAWNS[mark:]


def _checkout(tmp_path: Path, name: str = "checkout") -> Path:
    """A directory that looks like a cloned repository to every walk-up here."""
    path = tmp_path / name
    (path / ".git").mkdir(parents=True, exist_ok=True)
    return path


def _overlay_adding(server_name: str, args: list[str]) -> str:
    """An overlay document that ADDS a server, rather than replacing one."""
    return (
        "servers:\n"
        f"  {server_name}:\n"
        '    description: "added by the checkout"\n'
        "    keywords: [sealkw]\n"
        "    install:\n"
        + "".join(f"      {p}: [npx, {', '.join(args)}]\n" for p in PLATFORMS)
        + '    command: "npx"\n'
        f"    args: [{', '.join(args)}]\n"
    )


def _write_added_overlay(checkout: Path, args: list[str] | None = None) -> Path:
    overlay = checkout / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(
        _overlay_adding(ADDED_SERVER, args or ["-y", ADDED_PACKAGE]),
        encoding="utf-8",
    )
    return overlay


async def _register(gateway: GatewayTools, server_name: str, package: str) -> Any:
    """Register a discovered server through the real handler."""
    return await gateway.register_discovered_server(
        {"server_name": server_name, "package": package}
    )


def _npm_identity(name: str, version: str) -> PackageIdentity:
    return PackageIdentity(
        registry=NPM_REGISTRY, name=name, resolved_version=version, integrity=None
    )


_ADDED_REMEDY = f"pmcp trust approve-package {ADDED_PACKAGE}@{ADDED_VERSION}"


# --------------------------------------------------------------------------- #
# Seam 1 -- CONSENT x PKGID: an unapproved overlay that ADDS a server.
#
# EC-CONSENT-6. CONSENT refuses replacement; PKGID exempts manifest-backed
# servers. The add path runs between the two.
# --------------------------------------------------------------------------- #


def test_an_unapproved_overlay_that_adds_a_server_is_not_applied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    approve_project_file: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A checkout may not introduce a server the shipped manifest never carried.

    CONSENT's own reproduction replaces a shipped entry; this one adds a name
    that did not exist, which is the half the replace test cannot reach -- an
    added entry collides with nothing, so a loader that only guarded overwrites
    would apply it silently.
    """
    shipped = load_manifest()
    assert shipped.get_server(ADDED_SERVER) is None, (
        "the shipped manifest now carries the name this seam uses; pick another"
    )

    checkout = _checkout(tmp_path)
    overlay = _write_added_overlay(checkout)
    monkeypatch.chdir(checkout)

    with caplog.at_level(logging.WARNING, logger="pmcp.manifest.loader"):
        refused = load_manifest()

    assert refused.get_server(ADDED_SERVER) is None
    # The loader still works: every shipped server is still there, so "the
    # overlay contributed nothing" is not "the manifest stopped loading".
    assert len(refused.servers) == len(shipped.servers)
    remediation = f"pmcp trust approve {overlay.resolve()}"
    assert [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and remediation in record.getMessage()
    ], "the refusal did not name the command that would grant it"

    # Positive control: the operator approves exactly these bytes and the same
    # overlay adds the same server. Without it, "the server is absent" is
    # equally true of a loader that stopped reading project overlays at all.
    approve_project_file(overlay)
    approved = load_manifest().get_server(ADDED_SERVER)
    assert approved is not None
    assert (approved.command, list(approved.args)) == ("npx", ["-y", ADDED_PACKAGE])


async def test_an_added_server_is_not_manifest_backed_at_provision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    approve_project_file: Any,
    fake_npm_registry: dict[str, str],
) -> None:
    """The half a per-phase suite cannot reach: what PKGID makes of the name.

    Refusing to apply the overlay is CONSENT's property. The composition
    property is that the name the overlay tried to introduce is not
    *manifest-backed* afterwards -- PKGID's rule 2 exempts a manifest lookup
    from every approval requirement, so a name that slipped into that lookup
    would provision an arbitrary package with no operator decision anywhere.

    Driven through the real `gateway.provision`, with the same name also
    registered as a discovered server. That is what makes the assertion sharp:
    if the overlay had been applied, the manifest lookup would win and the gate
    would answer ``manifest_backed``; because it was not, the discovered lookup
    answers and the refusal carries the package gate's pinned remedy.
    """
    fake_npm_registry[ADDED_PACKAGE] = ADDED_VERSION
    checkout = _checkout(tmp_path)
    overlay = _write_added_overlay(checkout)
    monkeypatch.chdir(checkout)

    policy = _write_policy(tmp_path / "policy" / "gateway-policy.yaml", "servers: {}\n")
    gateway, manager, jobs = _gateway(monkeypatch, policy)
    spawn_mark = len(_INTERCEPTED_SPAWNS)

    # The name does not exist at all: the overlay contributed nothing, not even
    # an entry that is then refused for some other reason.
    unknown = await gateway.provision({"server_name": ADDED_SERVER})
    assert unknown.ok is False
    assert "not found in manifest" in unknown.message
    assert jobs.calls == []

    registered = await _register(gateway, ADDED_SERVER, ADDED_PACKAGE)
    assert registered.registered is True, registered.message

    refused = await gateway.provision({"server_name": ADDED_SERVER})
    assert refused.ok is False
    assert refused.status == "failed"
    # The PACKAGE gate refused it, identifiably: a manifest-backed answer would
    # have allowed it with no remedy at all.
    assert _ADDED_REMEDY in refused.message
    assert jobs.calls == []
    assert manager.connected == []
    assert _spawns_since(spawn_mark) == []

    # Positive control: the operator approves the overlay's bytes, the entry
    # becomes genuinely manifest-backed, and the same call provisions. This is
    # what proves the exemption exists and that consent is the only thing
    # standing between the checkout and it.
    approve_project_file(overlay)
    allowed = await gateway.provision({"server_name": ADDED_SERVER})
    assert allowed.ok is True, allowed.message
    assert [argv for argv, _config in jobs.calls] == [["npx", "-y", ADDED_PACKAGE]]


async def test_an_added_server_is_not_manifest_backed_at_connect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    approve_project_file: Any,
    fake_npm_registry: dict[str, str],
) -> None:
    """The second door onto the same question, for the same reason PKGID's own
    amendment gives: ``npx -y`` fetches and runs when it SPAWNS, so
    `connect_server` is an install path too, and a name that is manifest-backed
    here never reaches the approval rules at all.
    """
    fake_npm_registry[ADDED_PACKAGE] = ADDED_VERSION
    checkout = _checkout(tmp_path)
    overlay = _write_added_overlay(checkout)
    monkeypatch.chdir(checkout)

    policy = _write_policy(tmp_path / "policy" / "gateway-policy.yaml", "servers: {}\n")
    gateway, manager, jobs = _gateway(monkeypatch, policy)
    spawn_mark = len(_INTERCEPTED_SPAWNS)

    registered = await _register(gateway, ADDED_SERVER, ADDED_PACKAGE)
    assert registered.registered is True, registered.message

    refused = await gateway.connect_server({"server_name": ADDED_SERVER})
    assert refused.ok is False
    assert _ADDED_REMEDY in refused.message
    assert manager.connected == []
    assert jobs.calls == []
    assert _spawns_since(spawn_mark) == []

    approve_project_file(overlay)
    allowed = await gateway.connect_server({"server_name": ADDED_SERVER})
    assert allowed.ok is True, allowed.message
    spawned = manager.connected[0].config
    assert (spawned.command, list(spawned.args)) == ("npx", ["-y", ADDED_PACKAGE])


# --------------------------------------------------------------------------- #
# Seam 2 -- TRUST x CONSENT: an approval record shipped inside the checkout.
#
# EC-TRUST-5. `trust_store_path` RAISES for a checkout-resident store, and
# `is_approved` swallows every exception -- so the refusal and "nothing was ever
# approved" reach a caller as the same answer. Each test distinguishes them by
# moving the SAME store bytes outside the checkout and showing they grant.
# --------------------------------------------------------------------------- #


def _repo_mcp_json(checkout: Path) -> Path:
    """A project config the repository would like applied."""
    path = checkout / ".mcp.json"
    path.write_text(
        json.dumps(
            {"mcpServers": {"repo-server": {"command": "echo", "args": ["hello"]}}}
        ),
        encoding="utf-8",
    )
    return path


def _ship_an_approval_inside(
    checkout: Path, target: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Record a real approval into a store that lives inside the checkout.

    Written by ``trust_store.record`` itself, from a working directory outside
    the checkout, because that is the one way the production writer will produce
    these bytes -- and a repository ships bytes, not a call. The record is
    genuine and matches ``target``'s current content exactly, which is what makes
    the later refusal attributable to residency rather than to absence.
    """
    home = checkout / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(outside)
    trust_store.record(target, target.read_bytes(), "project", trust_store.APPROVED)
    store = trust_store.trust_store_path()
    assert store.is_relative_to(checkout.resolve())
    return store


def test_a_trust_store_shipped_inside_the_checkout_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A repository that ships its own approval record must not be believed.

    The store here holds a real, matching, ``approved`` record for the
    repository's own ``.mcp.json``. Everything that can raise does raise, naming
    the checkout; the consumer refuses; and the control shows the very same
    bytes, read from outside the checkout, grant the very same file.
    """
    checkout = _checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    config = _repo_mcp_json(checkout)
    store = _ship_an_approval_inside(checkout, config, outside, monkeypatch)

    monkeypatch.chdir(checkout)

    with pytest.raises(TrustStoreError) as raised:
        trust_store.trust_store_path()
    assert str(checkout.resolve()) in str(raised.value)
    # The operator-facing surfaces raise rather than answer, which is the
    # difference between "refused" and "nothing is recorded".
    with pytest.raises(TrustStoreError):
        trust_store.list_records()
    with pytest.raises(TrustStoreError):
        package_approvals.package_approvals_path()

    names = [c.name for c in load_configs(project_root=checkout, user_config_paths=[])]
    assert "repo-server" not in names

    # Positive control: the SAME store file, moved to a home outside the
    # checkout, grants. So the record is valid, matching and live -- the refusal
    # above was residency, not an absent or unreadable record.
    outside_home = tmp_path / "operator-home"
    shutil.copytree(checkout / "home", outside_home)
    monkeypatch.setenv("HOME", str(outside_home))
    assert trust_store.trust_store_path() != store
    assert [record.absolute_path for record in trust_store.list_records()] == [
        config.resolve()
    ]
    granted = [
        c.name for c in load_configs(project_root=checkout, user_config_paths=[])
    ]
    assert "repo-server" in granted


def test_a_checkout_resident_store_is_refused_through_a_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same rule when the residency is only reachable by resolving symlinks.

    ``~/.config/pmcp`` is a path the operator controls, so the realistic shape
    is not a store written inside the repository but a home directory that
    *points* there -- a dotfiles checkout, say. The check resolves before it
    compares, which is the only reason this is caught.
    """
    checkout = _checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    config = _repo_mcp_json(checkout)

    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    vendored = checkout / "vendor" / "pmcp"
    vendored.mkdir(parents=True)
    (home / ".config" / "pmcp").symlink_to(vendored, target_is_directory=True)
    monkeypatch.setenv("HOME", str(home))

    # Recorded from outside the checkout: the write lands in the repository
    # through the symlink, which is exactly what a committed store would be.
    monkeypatch.chdir(outside)
    trust_store.record(config, config.read_bytes(), "project", trust_store.APPROVED)
    assert (vendored / "trust.json").exists()

    monkeypatch.chdir(checkout)
    with pytest.raises(TrustStoreError) as raised:
        trust_store.trust_store_path()
    assert str(checkout.resolve()) in str(raised.value)
    names = [c.name for c in load_configs(project_root=checkout, user_config_paths=[])]
    assert "repo-server" not in names

    # Positive control: repoint the same symlink at a directory outside the
    # checkout holding the same store file. The record grants again, so the
    # refusal was the residency of the RESOLVED path and nothing else.
    elsewhere = tmp_path / "elsewhere-store"
    shutil.copytree(vendored, elsewhere)
    (home / ".config" / "pmcp").unlink()
    (home / ".config" / "pmcp").symlink_to(elsewhere, target_is_directory=True)
    assert trust_store.trust_store_path().is_relative_to(elsewhere.resolve())
    granted = [
        c.name for c in load_configs(project_root=checkout, user_config_paths=[])
    ]
    assert "repo-server" in granted


async def test_an_approval_in_a_checkout_resident_store_grants_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_npm_registry: dict[str, str],
) -> None:
    """Both stores, driven through their real consumers, grant nothing.

    The checkout ships an approval for its own manifest overlay AND a package
    approval for the package that overlay would run. Neither is believed while
    the store is resident, and both are believed the moment the identical bytes
    are read from outside the checkout -- which is what separates "refused" from
    "there was nothing there".
    """
    fake_npm_registry[ADDED_PACKAGE] = ADDED_VERSION
    checkout = _checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    overlay = _write_added_overlay(checkout)
    _ship_an_approval_inside(checkout, overlay, outside, monkeypatch)
    # ... and a package approval, in the store beside it, for the same package
    # the overlay runs. `approve_package` resolves its path through
    # `trust_store_path`, so it is written from outside the checkout too.
    approve_package(_npm_identity(ADDED_PACKAGE, ADDED_VERSION))
    assert (checkout / "home" / ".config" / "pmcp" / "package_approvals.json").exists()

    monkeypatch.chdir(checkout)
    with pytest.raises(TrustStoreError):
        trust_store.trust_store_path()

    assert load_manifest().get_server(ADDED_SERVER) is None

    policy = _write_policy(tmp_path / "policy" / "gateway-policy.yaml", "servers: {}\n")
    gateway, manager, jobs = _gateway(monkeypatch, policy)
    registered = await _register(gateway, ADDED_SERVER, ADDED_PACKAGE)
    assert registered.registered is True, registered.message

    refused = await gateway.provision({"server_name": ADDED_SERVER})
    assert refused.ok is False
    assert _ADDED_REMEDY in refused.message, (
        "the shipped package approval was believed, or some other gate answered"
    )
    assert jobs.calls == []
    assert manager.connected == []

    # Positive control: the same two store files, outside the checkout. The
    # overlay applies and the package provisions -- so every refusal above was
    # the residency rule, not a missing or malformed record.
    outside_home = tmp_path / "operator-home"
    shutil.copytree(checkout / "home", outside_home)
    monkeypatch.setenv("HOME", str(outside_home))

    added = load_manifest().get_server(ADDED_SERVER)
    assert added is not None, "the shipped overlay approval was not a valid record"
    granted = await gateway.provision({"server_name": ADDED_SERVER})
    assert granted.ok is True, granted.message
    assert [argv for argv, _config in jobs.calls] == [["npx", "-y", ADDED_PACKAGE]]


# --------------------------------------------------------------------------- #
# Seam 3 -- inside PKGID's own frozen decision order.
#
# Rule 1 before rule 5, and rule 4 before both allow rules. Each ordering is a
# seam between two lanes of PKGID that pass their own criteria separately.
# --------------------------------------------------------------------------- #


async def test_a_denylisted_package_outranks_an_operator_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_npm_registry: dict[str, str],
) -> None:
    """The policy lane composed with the approvals lane, at a real handler.

    Rule 1 must outrank rule 5, or a ``packages.denylist`` entry silently never
    fires for exactly the packages an operator once trusted -- the packages most
    worth denying later.
    """
    fake_npm_registry[ADDED_PACKAGE] = ADDED_VERSION
    fake_npm_registry[LEGIT_PACKAGE] = LEGIT_VERSION

    policy = _write_policy(
        tmp_path / "policy" / "gateway-policy.yaml",
        f"packages:\n  denylist:\n    - {ADDED_PACKAGE}\n",
    )
    assert policy.evaluate_package_name_policy(ADDED_PACKAGE) == "denied"
    assert policy.evaluate_package_name_policy(LEGIT_PACKAGE) == "unspecified"

    # The operator approved both packages. One of them is later denylisted.
    approve_package(_npm_identity(ADDED_PACKAGE, ADDED_VERSION))
    approve_package(_npm_identity(LEGIT_PACKAGE, LEGIT_VERSION))

    gateway, manager, jobs = _gateway(monkeypatch, policy)
    assert (await _register(gateway, "denied-tool", ADDED_PACKAGE)).registered is True
    assert (await _register(gateway, "allowed-tool", LEGIT_PACKAGE)).registered is True

    refused = await gateway.provision({"server_name": "denied-tool"})
    assert refused.ok is False
    assert refused.auth_state == "policy_denied"
    assert "packages.denylist" in refused.message
    # The denial does not advertise `approve-package`: rule 1 outranks it, so
    # offering it would send an operator to a command that cannot work.
    assert "trust approve-package" not in refused.message
    assert jobs.calls == []
    assert manager.connected == []

    # Positive control: the other approval, identical in every way except the
    # denylist entry, still provisions. So rule 5 is alive and rule 1 is what
    # refused -- not a store that stopped granting.
    allowed = await gateway.provision({"server_name": "allowed-tool"})
    assert allowed.ok is True, allowed.message
    assert [argv for argv, _config in jobs.calls] == [
        ["npx", "-y", f"{LEGIT_PACKAGE}@{LEGIT_VERSION}"]
    ]


async def test_an_approved_pin_still_refuses_an_unpinned_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_npm_registry: dict[str, str],
) -> None:
    """Rule 4 stays ahead of both allow rules (PKGID amendment 3).

    An approval names ``name@version``; ``npx -y pkg`` re-resolves ``latest``
    when it spawns. If the pin check ran after the approval check -- where the
    frozen decision order first put it -- an operator's approval of ``pkg@1.2.3``
    would license running whatever is published next, which is S-01 again for
    exactly the servers an operator trusted.

    **The unpinned config is installed into the handler's discovered table
    directly, and that is deliberate.** Production's only writer of that table is
    `register_discovered_server`, which pins both argvs at registration
    (`handlers.py`), so no shipped door composes this state today; the rule is a
    guard on the gate's ORDER for the day one does. Everything downstream of the
    table -- the lookup, the gate, the refusal, the spawn decision -- is the real
    handler.
    """
    fake_npm_registry[LEGIT_PACKAGE] = LEGIT_VERSION
    policy = _write_policy(tmp_path / "policy" / "gateway-policy.yaml", "servers: {}\n")
    gateway, manager, jobs = _gateway(monkeypatch, policy)

    registered = await _register(gateway, "pinned-tool", LEGIT_PACKAGE)
    assert registered.registered is True, registered.message
    approve_package(_npm_identity(LEGIT_PACKAGE, LEGIT_VERSION))

    pinned = gateway._discovered_server_configs["pinned-tool"]
    spec = f"{LEGIT_PACKAGE}@{LEGIT_VERSION}"
    assert list(pinned.args) == ["-y", spec], "registration no longer pins the argv"

    unpinned = ServerConfig(
        name=pinned.name,
        description=pinned.description,
        keywords=list(pinned.keywords),
        install={p: ["npx", "-y", LEGIT_PACKAGE] for p in PLATFORMS},
        command="npx",
        args=["-y", LEGIT_PACKAGE],
        package=LEGIT_PACKAGE,
    )
    gateway._discovered_server_configs["pinned-tool"] = unpinned

    refused = await gateway.provision({"server_name": "pinned-tool"})
    assert refused.ok is False
    assert "not pinned to the package version that was resolved" in refused.message
    assert f"npx -y {spec}" in refused.message
    assert jobs.calls == []
    assert manager.connected == []
    # The same refusal at the lifecycle door, which spawns the argv directly.
    refused_connect = await gateway.connect_server({"server_name": "pinned-tool"})
    assert refused_connect.ok is False
    assert manager.connected == []

    # Positive control: restore the pinned argv the approval was for, and the
    # same approval provisions it. The refusal was the argv, not the approval.
    gateway._discovered_server_configs["pinned-tool"] = pinned
    allowed = await gateway.provision({"server_name": "pinned-tool"})
    assert allowed.ok is True, allowed.message
    assert [argv for argv, _config in jobs.calls] == [["npx", "-y", spec]]


# --------------------------------------------------------------------------- #
# Seam 4 -- EGRESS x PKGID: the install-spawn credential strip.
#
# Where planning found a live leak, fixed by SL-0. Both tests read the child
# environment of a REAL install spawn, and the credential is written by the real
# `gateway.auth_connect` rather than by `env_store.set_env_value`, because the
# seam is between the writer's idea of the project root and the spawn's.
# --------------------------------------------------------------------------- #

#: An ambient variable no PMCP store knows about. It must SURVIVE into the
#: child: without it, "the credential is absent" is also satisfied by an empty
#: environment, and every assertion below would be vacuous.
_CONTROL_KEY = "SL3_UNMANAGED_CONTROL"
_CONTROL_VALUE = "ambient"


def _installed_child_env() -> dict[str, str]:
    """The environment of the install spawn this test drove, with checks.

    Fails loudly if no spawn was recorded or if the recorded environment is not
    a real inherited one -- the two ways an assertion about a missing key passes
    for the wrong reason.
    """
    assert _INTERCEPTED_SPAWNS, "no install spawn was recorded"
    argv, env = _INTERCEPTED_SPAWNS[-1]
    assert argv and argv[0] == "npx", argv
    assert env.get(_CONTROL_KEY) == _CONTROL_VALUE, (
        "the child did not inherit the gateway's ambient environment, so the "
        "absence of any other key proves nothing"
    )
    return env


async def _auth_connect(
    gateway: GatewayTools, server_name: str, credential: str, scope: str
) -> str:
    """Store a credential through the real handler; return the key it wrote."""
    result = await gateway.auth_connect(
        {"server_name": server_name, "credential": credential, "scope": scope}
    )
    assert result.ok is True, result.message
    assert result.env_var, result
    assert os.environ.get(result.env_var) == credential, (
        "auth_connect did not put the credential in the gateway's own "
        "environment, so there would be nothing for the spawn to inherit"
    )
    return str(result.env_var)


async def test_an_install_spawn_strips_the_credentials_pmcp_stores_hold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No install child inherits a credential PMCP holds for another server.

    Both stores, written by the real `gateway.auth_connect`: the user store
    (``~/.config/pmcp/pmcp.env``) and the project store
    (``<project>/.env.pmcp``). The install spawn is the real one --
    `JobManager.start_install` through `build_install_child_env` -- and the
    assertion reads the environment the child would have received.
    """
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv(_CONTROL_KEY, _CONTROL_VALUE)

    policy = _write_policy(tmp_path / "policy" / "gateway-policy.yaml", "servers: {}\n")
    gateway, _manager, _jobs = _gateway(
        monkeypatch, policy, project_root=project, job_manager=JobManager()
    )

    project_key = await _auth_connect(
        gateway, CREDENTIAL_SERVER, "project-scope-secret", "project"
    )
    user_key = await _auth_connect(
        gateway, OTHER_CREDENTIAL_SERVER, "user-scope-secret", "user"
    )
    assert (project / ".env.pmcp").exists()
    assert (Path.home() / ".config" / "pmcp" / "pmcp.env").exists()

    result = await gateway.provision({"server_name": INSTALLABLE_SERVER})
    assert result.ok is True, result.message
    assert result.status == "started"

    child_env = _installed_child_env()
    for key in (project_key, user_key):
        assert key not in child_env, (
            f"the install child inherited {key}, a credential PMCP stores for "
            "another server"
        )
        # The gateway itself still holds it: the strip is what the CHILD gets,
        # not a deletion from the gateway's own environment.
        assert key in os.environ


async def test_the_install_spawn_strip_uses_the_gateways_project_root_not_the_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The mechanism, which is the half that was actually wrong.

    ``pmcp serve --project X`` started from another directory: the credential is
    written through the gateway's root and the strip must resolve the project
    store from that same root. Two project stores exist here, and they
    discriminate in both directions -- the gateway's key must be gone, and the
    working directory's key, which no store of this gateway's holds, must
    survive. Under the pre-SL-0 cwd walk the answers were exactly reversed, so
    neither assertion can pass for the wrong reason.
    """
    gateway_root = tmp_path / "gateway-project"
    gateway_root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv(_CONTROL_KEY, _CONTROL_VALUE)

    policy = _write_policy(tmp_path / "policy" / "gateway-policy.yaml", "servers: {}\n")
    gateway, _manager, _jobs = _gateway(
        monkeypatch, policy, project_root=gateway_root, job_manager=JobManager()
    )

    # Written through the gateway's own root, by the real writer, from a
    # different working directory -- the `pmcp serve --project X` shape.
    monkeypatch.chdir(elsewhere)
    gateway_key = await _auth_connect(
        gateway, CREDENTIAL_SERVER, "the-gateways-secret", "project"
    )
    assert (gateway_root / ".env.pmcp").exists()
    assert not (elsewhere / ".env.pmcp").exists()

    # A second, unrelated project store in the working directory, holding a key
    # this gateway manages nowhere. It is the discriminator: a strip that walked
    # up from the cwd would remove this one and keep the gateway's.
    other_key = "SL3_OTHER_PROJECT_TOKEN"
    (elsewhere / ".env.pmcp").write_text(f"{other_key}=someone-elses\n", "utf-8")
    monkeypatch.setenv(other_key, "someone-elses")

    result = await gateway.provision({"server_name": INSTALLABLE_SERVER})
    assert result.ok is True, result.message

    child_env = _installed_child_env()
    assert gateway_key not in child_env, (
        f"the install child inherited {gateway_key} from "
        f"{gateway_root / '.env.pmcp'}: the strip resolved the project store "
        f"from the working directory ({elsewhere}) instead of from the root the "
        "gateway was given"
    )
    assert child_env.get(other_key) == "someone-elses", (
        "the strip consulted the working directory's store, which is the walk "
        "SL-0 removed"
    )


# --------------------------------------------------------------------------- #
# The isolation layer, as a node id rather than a fixture side effect.
#
# Last in the file on purpose: in a file run it sees every case above. Run alone
# it still asserts something real -- that the guards are installed -- because a
# guard that silently stopped applying is how EGRESS's red run reached a live
# `gh issue create` on the development host.
# --------------------------------------------------------------------------- #


def test_no_composition_case_in_this_suite_reached_the_network() -> None:
    """Nothing here reached the network, and no real process ever ran."""
    assert _NETWORK_ATTEMPTS == [], (
        f"a composition case reached the network: {_NETWORK_ATTEMPTS}"
    )
    assert _ESCAPED_PROCESSES == [], (
        f"a composition case used a blocking subprocess door: {_ESCAPED_PROCESSES}"
    )
    # Install spawns are DRIVEN here, so the proof that none was real is that
    # every one of them was this module's recorder, and that the recorder is the
    # thing installed right now.
    installed: list[Callable[..., Any]] = [
        package_identity._OPENER.open,
        cast(Any, socket.socket.connect),
        cast(Any, socket.socket.connect_ex),
        cast(Any, asyncio.create_subprocess_exec),
        cast(Any, asyncio.create_subprocess_shell),
        cast(Any, subprocess.Popen),
        cast(Any, subprocess.run),
    ]
    for guard in installed:
        assert getattr(guard, "__module__", None) == __name__, guard
    # ...and the manifest loader is NOT stubbed, at either name a seam here reads
    # it through: the overlay walk-up these cases depend on is production's.
    assert load_manifest.__module__ == manifest_loader.__name__
    assert handlers_module.load_manifest.__module__ == manifest_loader.__name__
