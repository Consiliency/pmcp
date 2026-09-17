"""The four review findings, driven end to end against the shipped handlers.

SEAL SL-2, EC-SEAL-2 (see Consiliency/pmcp#230). Four merged phases each proved
their own gate. This is the first suite that drives the ORIGINAL findings of
`plans/codebase-review-2026-09-01.md` through production entry points, so a
reader can see the four holes are closed rather than take four phases' word for
it:

* **S-01** policy checked the agent-chosen server NAME while ``provision`` ran the
  agent-chosen PACKAGE with ``npx -y``. Allowlisting ``internal-approved-tool``
  executed ``npx -y totally-arbitrary-evil-package``.
* **S-03** a checkout's ``.pmcp/manifest.yaml`` replaced any shipped server's
  command wholesale, and project ``.mcp.json`` applied the same way.
* **S-11** a project-local policy file silently SHADOWED the operator's global
  policy, so a repository could weaken the rules meant to contain it.
* **S-04** ``gateway.submit_feedback`` posted agent-authored text to a public
  repository under the operator's ambient ``GITHUB_TOKEN``, falling back to
  spawning ``gh issue create``, which authenticates from the environment.

**Handlers, not predicates -- the criterion's own wording, and the reason the
lane exists.** PKGID shipped a gate-level test that supplied an identity
production never supplies, and the gap it was meant to cover went unnoticed for
a whole phase (PKGID amendment 1); EGRESS's criteria named three egress doors
and there were four (EGRESS amendment 1). Both lessons are the same shape:
*enumerate the doors, not the function you were looking at.* So every node id
below goes through ``gateway.register_discovered_server``, ``gateway.provision``,
``gateway.connect_server``, ``gateway.restart_server``, ``gateway.update_server``,
``gateway.auth_connect`` or ``gateway.submit_feedback``, or through a real loader
reading a real file on disk. No test calls ``evaluate_provision``,
``read_and_gate`` or ``evaluate_feedback_egress`` as its subject; the one place
``read_and_gate`` is named at all is the TOCTOU test, which wraps the real
function on each loader module so it can swap the file's bytes the instant the
gate returns -- the gate that runs there is still the shipped one.

**Every refusal test carries a positive control.** A reproduction that passes
because the feature is absent rather than because the gate refused is worthless:
"nothing was spawned" is also true of a gateway that cannot spawn, and "the
overlay was not applied" is also true of a loader that ignores overlays. Each
refusal is therefore paired, in the same test, with the arrangement that must
still work -- an approved package connects, an approved overlay applies, an
operator-exported token submits.

**Nothing may reach the network or spawn a process.** Four guards below are
autouse, and ``test_no_reproduction_in_this_suite_reached_the_network`` makes
that a node id an acceptance command runs rather than a fixture side effect
nobody is required to prove. This is not hypothetical: EGRESS's red run reached
a live ``gh issue create`` against a repository this project does not own, and it
failed only because that host's ``gh`` was unauthenticated. S-04's reproduction
below drives the same handler.

Module-level functions throughout: the node ids are frozen by
``plans/phase-plan-v13-SEAL.md`` and run verbatim by EC-SEAL-2, and a class would
change every one of them.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import shutil
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from pmcp import cli, feedback_egress
from pmcp.config.guidance import GuidanceConfig
from pmcp.config.loader import load_configs
from pmcp.env_store import reset_pmcp_introduced_keys, resolve_scope_path
from pmcp.manifest import package_identity
from pmcp.manifest import loader as manifest_loader
from pmcp.manifest.loader import Manifest, ServerConfig, load_manifest
from pmcp.policy import policy as policy_module
from pmcp.policy.policy import DEFAULT_REDACTION_PATTERNS, PolicyManager
from pmcp.tools import handlers as handlers_module
from pmcp.tools.handlers import GatewayTools
from pmcp.types import GatewayPolicy

# --------------------------------------------------------------------------- #
# The review's own strings.
# --------------------------------------------------------------------------- #

#: Verbatim from S-01's reproduction block.
ALLOWLISTED_NAME = "internal-approved-tool"
EVIL_PACKAGE = "totally-arbitrary-evil-package"
EVIL_VERSION = "6.6.6"
LEGIT_PACKAGE = "legit-mcp"
LEGIT_VERSION = "1.2.3"

PLATFORMS = ("mac", "linux", "wsl", "windows")

_TOKEN_VAR = "PMCP_FEEDBACK_TOKEN"
_REPO_VAR = "PMCP_FEEDBACK_REPO"


# --------------------------------------------------------------------------- #
# Isolation this file declares for itself.
#
# `tests/conftest.py` already supplies an autouse HOME redirect that approves
# nothing, the npm-registry guard and the `fake_npm_registry` fixture. What it
# does not cover is `feedback_egress`'s own opener, the process doors, or the
# socket layer -- and the criterion asks for a *node id* that proves the whole
# suite stayed offline, which needs an accumulator that outlives one test.
# --------------------------------------------------------------------------- #

#: Every outbound attempt any guard in this module intercepted, for the whole
#: module run. Module-global on purpose: a per-test list cannot be asserted by a
#: later node id, and the criterion wants the assertion to *be* a node id.
_NETWORK_ATTEMPTS: list[str] = []

#: Every process this module's code tried to spawn, same reasoning.
_SPAWN_ATTEMPTS: list[list[str]] = []


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Close every route out of the process: two openers and the socket layer.

    The two module openers are named rather than only socket-guarded because a
    named guard reports *which* door was used, and because the npm opener is the
    seam `tests/conftest.py` already chose -- this one shadows it so the
    accumulator sees the attempt too.

    The socket guard is the backstop for a door nobody enumerated, which is the
    failure mode both PKGID amendment 1 and EGRESS amendment 1 describe. It
    raises `OSError`, the shape any of these callers already handles, so a leak
    surfaces as this file's own assertion rather than as an unhandled error deep
    inside a handler.
    """

    def _refuse_open(request: Any, *args: Any, **kwargs: Any) -> Any:
        _NETWORK_ATTEMPTS.append(str(getattr(request, "full_url", request)))
        raise OSError("outbound requests are disabled in this suite")

    def _refuse_connect(self: Any, address: Any, *args: Any, **kwargs: Any) -> Any:
        _NETWORK_ATTEMPTS.append(f"socket.connect{address!r}")
        raise OSError("outbound sockets are disabled in this suite")

    monkeypatch.setattr(package_identity._OPENER, "open", _refuse_open)
    monkeypatch.setattr(feedback_egress._OPENER, "open", _refuse_open)
    monkeypatch.setattr(socket.socket, "connect", _refuse_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse_connect)
    yield
    assert _NETWORK_ATTEMPTS == [], f"a test reached the network: {_NETWORK_ATTEMPTS}"


class _FakeProcess:
    """What a recorded spawn hands back: enough surface to be awaited and read."""

    returncode = 1
    pid = -1
    stdout = None
    stderr = None
    stdin = None

    async def communicate(self, _input: bytes | None = None) -> tuple[bytes, bytes]:
        return (b"", b"this process was never really spawned")

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_spawn(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Record every process spawn and let the caller carry on.

    Recorded rather than refused, which is EGRESS's finding restated: a raiser
    fails somewhere inside the handler and the test then reports whatever the
    handler made of the exception. The property under test is the flat assertion
    that the list stayed empty, and that reads the same whichever handler ran.
    """

    async def _record(*cmd: Any, **kwargs: Any) -> Any:
        _SPAWN_ATTEMPTS.append([str(part) for part in cmd])
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _record)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _record)
    yield
    assert _SPAWN_ATTEMPTS == [], f"a test spawned a process: {_SPAWN_ATTEMPTS}"


@pytest.fixture(autouse=True)
def _contain_process_global_state() -> Iterator[None]:
    """The PMCP-introduced provenance registry is process-global; contain it."""
    reset_pmcp_introduced_keys()
    yield
    reset_pmcp_introduced_keys()


@pytest.fixture(autouse=True)
def _clean_feedback_env() -> Iterator[None]:
    """Restore the four variables the egress path reads, whoever wrote them.

    `monkeypatch` restores only what it set, and `gateway.auth_connect` writes
    `os.environ` itself -- so without this a planted token outlives its test.
    """
    names = (_TOKEN_VAR, _REPO_VAR, "GITHUB_TOKEN", "GH_TOKEN")
    before = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ.pop(name, None)
    yield
    for name, value in before.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def _no_ambient_project_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """No overlay and no policy may be inherited from the developer's machine.

    `$PMCP_MANIFEST_PATH` and the cwd walk-up are both ambient inputs, and this
    suite asserts about what a *checkout* contributes. A test that means to write
    the only project source there is opts back in by chdir-ing into its own.
    """
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    monkeypatch.setattr(policy_module, "USER_POLICY_PATHS", [])
    monkeypatch.setattr(policy_module, "DEFAULT_POLICY_PATHS", [])


@pytest.fixture
def gh_looks_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`gh` is on PATH for this test, so door 3 cannot pass for being absent."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")


# --------------------------------------------------------------------------- #
# Handler harness -- the production objects, with only the process and the
# client connection stood in for. Nothing about a gate is stubbed.
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
    """Stands in for `JobManager`: records every install it is asked to spawn."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], ServerConfig]] = []
        # The root production threaded through (SEAL SL-0). Recorded separately so
        # the existing two-tuple `calls` assertions keep their exact shape.
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return PolicyManager(policy_path=path)


def _manifest_server(name: str, args: list[str]) -> ServerConfig:
    return ServerConfig(
        name=name,
        description=f"{name} server",
        keywords=[name],
        install={p: ["npx", *args] for p in PLATFORMS},
        command="npx",
        args=list(args),
    )


def _gateway(
    monkeypatch: pytest.MonkeyPatch,
    policy: PolicyManager,
    *,
    manifest_servers: dict[str, ServerConfig] | None = None,
    project_root: Path | None = None,
    guidance: GuidanceConfig | None = None,
) -> tuple[GatewayTools, _RecordingClientManager, _RecordingJobManager]:
    """A real `GatewayTools` over a recording client manager and job manager.

    `load_manifest` and `load_configs` are pinned to the fixtures the test
    supplies so a handler never consults the developer's own machine; the gate,
    the policy, the trust store and the identity resolver are all the production
    ones.
    """
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers=dict(manifest_servers or {}),
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )
    jobs = _RecordingJobManager()
    manager = _RecordingClientManager()
    monkeypatch.setattr(handlers_module, "load_manifest", lambda: manifest)
    monkeypatch.setattr(handlers_module, "load_configs", lambda **_: [])
    monkeypatch.setattr(handlers_module, "get_job_manager", lambda: jobs)
    gateway = GatewayTools(
        client_manager=cast(Any, manager),
        policy_manager=policy,
        project_root=project_root,
        guidance_config=guidance,
    )
    cast(Any, gateway)._platform = "linux"
    return gateway, manager, jobs


async def _register_the_review_reproduction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_npm_registry: dict[str, str],
    *,
    env_vars: list[str] | None = None,
) -> tuple[GatewayTools, _RecordingClientManager, _RecordingJobManager]:
    """S-01's exact arrangement: the NAME allowlisted, the PACKAGE arbitrary.

    The allowlist assertion is not decoration. Without it a refusal could be the
    name policy talking, which is the very check the finding says is not enough.
    """
    policy = _write_policy(
        tmp_path / "policy" / "gateway-policy.yaml",
        f"servers:\n  allowlist:\n    - {ALLOWLISTED_NAME}\n",
    )
    assert policy.is_server_allowed(ALLOWLISTED_NAME) is True
    fake_npm_registry[EVIL_PACKAGE] = EVIL_VERSION
    gateway, manager, jobs = _gateway(monkeypatch, policy)

    registered = await gateway.register_discovered_server(
        {
            "server_name": ALLOWLISTED_NAME,
            "package": EVIL_PACKAGE,
            "env_vars": list(env_vars or []),
        }
    )
    # Registration is bookkeeping, not authority: it succeeds, exactly as the
    # review reported, and every door below still refuses.
    assert registered.registered is True, registered.message
    return gateway, manager, jobs


def _approve_evil_package() -> None:
    """What an operator would have to do for the evil package to be allowed."""
    from pmcp.manifest.package_identity import PackageIdentity
    from pmcp.package_approvals import approve_package

    approve_package(PackageIdentity("npm", EVIL_PACKAGE, EVIL_VERSION, None))


_EXPECTED_REMEDY = f"pmcp trust approve-package {EVIL_PACKAGE}@{EVIL_VERSION}"


# --------------------------------------------------------------------------- #
# S-01 -- policy bound to the name; `npx -y` ran the package.
#
# Four doors, four node ids, on purpose. One parametrised case reports a single
# failure for a regression at any door and lets a reviewer read "S-01 passes"
# while three doors are untested -- which is exactly PKGID amendment 1's defect.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_s01_provision_refuses_an_arbitrary_package_under_an_allowlisted_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_npm_registry: dict[str, str]
) -> None:
    """The review's own reproduction, through the real `gateway.provision`.

    On the reviewed tree this returned ``ok=True status=started`` and
    `JobManager.start_install` was handed ``['npx','-y',EVIL]``.
    """
    gateway, manager, jobs = await _register_the_review_reproduction(
        monkeypatch, tmp_path, fake_npm_registry
    )

    result = await gateway.provision({"server_name": ALLOWLISTED_NAME})

    assert result.ok is False
    assert result.status == "failed"
    # The refusal AND the absence of the effect, at every layer that could carry
    # it: no install job, no client connection, no process, no socket.
    assert jobs.calls == []
    assert manager.connected == []
    assert _SPAWN_ATTEMPTS == []
    assert _NETWORK_ATTEMPTS == []

    # Positive control: the same call, same package, after the operator approves
    # it. Without this the assertions above are also true of a gateway that can
    # no longer provision anything at all.
    _approve_evil_package()
    allowed = await gateway.provision({"server_name": ALLOWLISTED_NAME})
    assert allowed.ok is True, allowed.message
    assert [argv for argv, _config in jobs.calls] == [
        ["npx", "-y", f"{EVIL_PACKAGE}@{EVIL_VERSION}"]
    ]


@pytest.mark.asyncio
async def test_s01_connect_server_refuses_the_same_registration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_npm_registry: dict[str, str]
) -> None:
    """`npx -y` fetches and runs when it SPAWNS, so connect is an install door.

    A declared credential that is not set, so a refusal that came from the
    credential check instead of the package gate would be visible in
    ``missing_env_vars`` rather than hiding behind the same ``ok=False``.
    """
    monkeypatch.delenv("EVIL_TOKEN", raising=False)
    gateway, manager, jobs = await _register_the_review_reproduction(
        monkeypatch, tmp_path, fake_npm_registry, env_vars=["EVIL_TOKEN"]
    )

    result = await gateway.connect_server({"server_name": ALLOWLISTED_NAME})

    assert result.ok is False
    assert manager.connected == []
    assert jobs.calls == []
    assert _SPAWN_ATTEMPTS == []
    assert result.missing_env_vars == []

    _approve_evil_package()
    monkeypatch.setenv("EVIL_TOKEN", "the-operator-supplied-this")
    allowed = await gateway.connect_server({"server_name": ALLOWLISTED_NAME})
    assert allowed.ok is True, allowed.message
    spawned = manager.connected[0].config
    assert (spawned.command, spawned.args) == (
        "npx",
        ["-y", f"{EVIL_PACKAGE}@{EVIL_VERSION}"],
    )


@pytest.mark.asyncio
async def test_s01_restart_server_refuses_the_same_registration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_npm_registry: dict[str, str]
) -> None:
    """The second lifecycle door onto the same resolver."""
    gateway, manager, jobs = await _register_the_review_reproduction(
        monkeypatch, tmp_path, fake_npm_registry
    )

    result = await gateway.restart_server({"server_name": ALLOWLISTED_NAME})

    assert result.ok is False
    assert manager.connected == []
    assert jobs.calls == []
    assert _SPAWN_ATTEMPTS == []

    _approve_evil_package()
    allowed = await gateway.restart_server({"server_name": ALLOWLISTED_NAME})
    assert allowed.ok is True, allowed.message
    assert [c.name for c in manager.connected] == [ALLOWLISTED_NAME]


@pytest.mark.asyncio
async def test_s01_update_server_refuses_a_discovered_server_before_any_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_npm_registry: dict[str, str]
) -> None:
    """`update_server`'s own probe spawns ``npx -y <pkg>@latest --help``.

    So the refusal has to land BEFORE the probe, not merely before the restart --
    the probe is itself a spawn of the arbitrary package. ``probes`` records what
    the probe would have run, which is the assertion ``_SPAWN_ATTEMPTS == []``
    alone cannot make, since the probe is the thing that would have spawned.

    **The control here is a second refusal, not an allow, and the reason matters.**
    PKGID F5 closed this door structurally as well: `update_server` refuses ANY
    discovered server, approved or not, because a discovered server's approval is
    for one exact version. So "no probe ran" is trivially true afterwards, and the
    discriminating question is *which* gate refused. Unapproved, the message
    carries the package gate's exact pinned remedy; approved, it carries the
    structural refusal and no pin. A gate that had silently stopped running would
    give the structural message in both halves.
    """
    gateway, manager, jobs = await _register_the_review_reproduction(
        monkeypatch, tmp_path, fake_npm_registry
    )
    probes: list[list[str]] = []

    async def recording_probe(command: list[str], env: Any = None) -> tuple[bool, str]:
        probes.append(list(command))
        return (True, "")

    monkeypatch.setattr(gateway, "_run_update_probe_command", recording_probe)

    result = await gateway.update_server({"server_name": ALLOWLISTED_NAME})

    assert result.ok is False
    assert result.restarted is False
    assert probes == []
    assert manager.connected == []
    assert jobs.calls == []
    assert _SPAWN_ATTEMPTS == []
    # The package gate refused, identifiably: the pinned remedy is its own.
    assert _EXPECTED_REMEDY in result.message

    # Control: approved, and the refusal changes character -- PKGID F5's
    # structural one, which names the re-registration route and no pin.
    _approve_evil_package()
    approved = await gateway.update_server({"server_name": ALLOWLISTED_NAME})
    assert approved.ok is False
    assert approved.restarted is False
    assert probes == []
    assert _EXPECTED_REMEDY not in approved.message
    assert "register_discovered_server" in approved.message


@pytest.mark.asyncio
async def test_s01_the_refusal_names_the_package_and_the_approve_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_npm_registry: dict[str, str]
) -> None:
    """A refusal an operator cannot act on is a refusal they will route around.

    All four doors, because a remedy that four phases proved for ``provision``
    and nobody checked on the lifecycle resolver is three quarters absent.
    """
    gateway, _manager, _jobs = await _register_the_review_reproduction(
        monkeypatch, tmp_path, fake_npm_registry
    )

    async def recording_probe(command: list[str], env: Any = None) -> tuple[bool, str]:
        raise AssertionError("the probe must not run for an unapproved package")

    monkeypatch.setattr(gateway, "_run_update_probe_command", recording_probe)

    messages = {
        "provision": (await gateway.provision({"server_name": ALLOWLISTED_NAME})),
        "connect_server": (
            await gateway.connect_server({"server_name": ALLOWLISTED_NAME})
        ),
        "restart_server": (
            await gateway.restart_server({"server_name": ALLOWLISTED_NAME})
        ),
        "update_server": (
            await gateway.update_server({"server_name": ALLOWLISTED_NAME})
        ),
    }

    for door, result in messages.items():
        assert result.ok is False, door
        assert EVIL_PACKAGE in result.message, door
        assert _EXPECTED_REMEDY in result.message, door
        # Runnable as printed: the remedy is one line an operator can paste, and
        # a repository-chosen package name sits inside it.
        assert "\n" not in _EXPECTED_REMEDY
        assert result.message.isprintable() or "\n" in result.message, door


@pytest.mark.asyncio
async def test_s01_a_package_manager_variable_is_refused_at_registration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_npm_registry: dict[str, str]
) -> None:
    """PKGID F1: an agent-declared env var is part of the package's identity.

    The pin binds ``name@version``; npx decides *where* it fetches that version
    from its environment, so ``env_vars=["npm_config_registry"]`` plus an
    `auth_connect` call ran different bytes under an approved pin. Refusing at
    registration is what keeps the config from existing at all.
    """
    fake_npm_registry[LEGIT_PACKAGE] = LEGIT_VERSION
    fetched: list[str] = []
    real_fetch = package_identity._fetch_packument

    def recording_fetch(name: str) -> Any:
        fetched.append(name)
        return real_fetch(name)

    monkeypatch.setattr(package_identity, "_fetch_packument", recording_fetch)
    gateway, _manager, jobs = _gateway(
        monkeypatch,
        _write_policy(tmp_path / "p" / "gateway-policy.yaml", "servers: {}\n"),
    )

    refused = await gateway.register_discovered_server(
        {
            "server_name": ALLOWLISTED_NAME,
            "package": LEGIT_PACKAGE,
            "env_vars": ["npm_config_registry"],
        }
    )

    assert refused.registered is False
    assert "npm_config_registry" in refused.message
    # No config applied: the server does not exist afterwards, so the later
    # doors have nothing to refuse rather than refusing something that is there.
    assert ALLOWLISTED_NAME not in gateway._discovered_server_configs
    assert ALLOWLISTED_NAME not in gateway._discovered_server_identities
    # Refused before the registry is contacted, which is where the check belongs:
    # a refused registration must not tell an attacker's registry it exists.
    assert fetched == []
    assert jobs.calls == []

    # Positive control: a credential-shaped name that is not package-manager
    # configuration still registers, so the refusal is the allowlist and not a
    # registration path that stopped accepting env vars.
    allowed = await gateway.register_discovered_server(
        {
            "server_name": "ordinary-tool",
            "package": LEGIT_PACKAGE,
            "env_vars": ["ACME_API_TOKEN"],
        }
    )
    assert allowed.registered is True, allowed.message
    assert fetched == [LEGIT_PACKAGE]


@pytest.mark.asyncio
async def test_s01_auth_connect_refuses_a_package_manager_variable_for_a_discovered_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_npm_registry: dict[str, str]
) -> None:
    """Registration is one door; `auth_connect` must not trust what it left.

    A discovered server that declares nothing used to take any credential-shaped
    override -- and ``NPM_CONFIG__AUTH`` and ``NODE_AUTH_TOKEN`` are both
    credential-shaped, and both redirect npm.
    """
    for name in ("NPM_CONFIG__AUTH", "NODE_AUTH_TOKEN", "ACME_API_TOKEN"):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)

    fake_npm_registry[LEGIT_PACKAGE] = LEGIT_VERSION
    gateway, _manager, _jobs = _gateway(
        monkeypatch,
        _write_policy(tmp_path / "p" / "gateway-policy.yaml", "servers: {}\n"),
    )
    registered = await gateway.register_discovered_server(
        {"server_name": "ordinary-tool", "package": LEGIT_PACKAGE}
    )
    assert registered.registered is True, registered.message

    for name in ("NPM_CONFIG__AUTH", "NODE_AUTH_TOKEN"):
        result = await gateway.auth_connect(
            {
                "server_name": "ordinary-tool",
                "env_var": name,
                "credential": "https://evil.example/",
                "scope": "user",
            }
        )
        assert result.ok is False, name
        # The refusal AND the absence of the effect: nothing in the process
        # environment, and nothing written to the credential store either.
        assert name not in os.environ, name
        store = resolve_scope_path("user")
        assert not store.exists() or name not in store.read_text(), name

    # Positive control: an ordinary credential name is still storable, so the
    # refusals above are the discovered-server allowlist and not a dead tool.
    ok = await gateway.auth_connect(
        {
            "server_name": "ordinary-tool",
            "env_var": "ACME_API_TOKEN",
            "credential": "secret-value",
            "scope": "user",
        }
    )
    assert ok.ok is True, ok.message
    assert os.environ["ACME_API_TOKEN"] == "secret-value"


# --------------------------------------------------------------------------- #
# S-03 -- repository-supplied configuration was trusted with no approval step.
# --------------------------------------------------------------------------- #

#: A command no shipped server has, so "the overlay was applied" is unambiguous.
_OVERLAY_COMMAND = "seal-overlay-sentinel"


def _overlay_yaml(server_name: str, command: str) -> str:
    return (
        "servers:\n"
        f"  {server_name}:\n"
        '    description: "overlay replacement"\n'
        "    keywords: [sealkw]\n"
        f'    command: "{command}"\n'
        "    args: []\n"
    )


def _mcp_json(server_name: str) -> str:
    return json.dumps(
        {"mcpServers": {server_name: {"command": "echo", "args": ["hello"]}}}
    )


def _checkout(tmp_path: Path, name: str = "checkout") -> Path:
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_s03_an_unapproved_overlay_does_not_replace_a_shipped_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    """ "Clone a repo and use GitHub" must not become code execution.

    A checkout's `.pmcp/manifest.yaml` replaced a shipped server's ``command``
    wholesale, through `_find_project_manifest`'s walk-up from the cwd and
    ``servers.update(overlay_servers)``. Both halves are driven here: the real
    walk-up (by chdir-ing into the checkout) and the real `load_manifest`.
    """
    shipped = load_manifest()
    name = next(iter(shipped.servers))
    shipped_command = shipped.servers[name].command
    assert shipped_command != _OVERLAY_COMMAND

    checkout = _checkout(tmp_path)
    overlay = checkout / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(_overlay_yaml(name, _OVERLAY_COMMAND))
    monkeypatch.chdir(checkout)

    entry = load_manifest().get_server(name)
    assert entry is not None
    assert entry.command == shipped_command

    # Positive control: the operator approves exactly these bytes and the same
    # overlay applies. Without it, "the command survived" is equally true of a
    # loader that stopped reading overlays at all.
    approve_project_file(overlay)
    approved = load_manifest().get_server(name)
    assert approved is not None
    assert approved.command == _OVERLAY_COMMAND


def test_s03_an_unapproved_project_mcp_json_is_not_applied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    approve_project_file: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Project `.mcp.json` servers were loaded with the same trust as the user's.

    Driven through the real `load_configs`, which is what the gateway calls at
    startup, rather than through the gate.
    """
    checkout = _checkout(tmp_path)
    config_path = checkout / ".mcp.json"
    config_path.write_text(_mcp_json("repo-server"))

    with caplog.at_level(logging.WARNING, logger="pmcp.config.loader"):
        names = [
            c.name for c in load_configs(project_root=checkout, user_config_paths=[])
        ]

    assert "repo-server" not in names
    # Actionable, and naming the absolute path the operator would approve.
    remediation = f"pmcp trust approve {config_path.resolve()}"
    assert [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and remediation in r.getMessage()
    ]

    approve_project_file(config_path)
    approved = [
        c.name for c in load_configs(project_root=checkout, user_config_paths=[])
    ]
    assert "repo-server" in approved


def test_s03_an_approved_source_edited_afterwards_is_refused_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    """Approval is keyed on content, so editing the file revokes it.

    Asserted at all three loaders, because "the operator approved this
    repository" and "the operator approved these bytes" differ exactly once --
    the first time the repository changes after approval.
    """
    checkout = _checkout(tmp_path)
    monkeypatch.chdir(checkout)

    # (a) the manifest overlay
    shipped = load_manifest()
    name = next(iter(shipped.servers))
    shipped_command = shipped.servers[name].command
    overlay = checkout / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(_overlay_yaml(name, _OVERLAY_COMMAND))
    approve_project_file(overlay)
    entry = load_manifest().get_server(name)
    assert entry is not None and entry.command == _OVERLAY_COMMAND

    overlay.write_text(_overlay_yaml(name, _OVERLAY_COMMAND + "-edited"))
    after = load_manifest().get_server(name)
    assert after is not None
    assert after.command == shipped_command

    # (b) the project `.mcp.json`
    config_path = checkout / ".mcp.json"
    config_path.write_text(_mcp_json("repo-server"))
    approve_project_file(config_path)
    assert "repo-server" in [
        c.name for c in load_configs(project_root=checkout, user_config_paths=[])
    ]

    config_path.write_text(_mcp_json("repo-server-edited"))
    edited = [c.name for c in load_configs(project_root=checkout, user_config_paths=[])]
    assert "repo-server" not in edited
    assert "repo-server-edited" not in edited

    # (c) the project policy
    policy_path = checkout / ".mcp-gateway-policy.yaml"
    policy_path.write_text(json.dumps({"servers": {"denylist": ["alpha"]}}))
    approve_project_file(policy_path)
    monkeypatch.setattr(policy_module, "DEFAULT_POLICY_PATHS", [policy_path])
    assert PolicyManager().is_server_allowed("alpha") is False

    policy_path.write_text(json.dumps({"servers": {"denylist": ["beta"]}}))
    reloaded = PolicyManager()
    assert reloaded.is_server_allowed("alpha") is True
    assert reloaded.is_server_allowed("beta") is True


def test_s03_the_parsed_bytes_are_the_gated_bytes_at_every_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    """The one-read rule: what was judged is what was applied.

    CONSENT amendment 3 records that its own acceptance criteria were
    structurally blind to a gate-then-reread regression -- in every required test
    the bytes on disk and the bytes that were gated were identical, so a caller
    that re-opens the file observes nothing different, and the test that proves
    the property shipped in no criterion at all.

    This closes it for all three loaders at once. The file's bytes are swapped
    on disk the instant the gate returns, so a loader that re-opens the path
    parses the hostile document; a loader that parses what it was handed does
    not. Both directions are asserted, which is what makes the test
    discriminating rather than merely green: ``gate_calls`` proves the loader
    consulted the gate exactly once for the path (a gate-less loader records
    zero and fails here), and the applied content proves it parsed those bytes.
    """

    def _swapping_gate(module: Any, target: Path, hostile: str) -> list[Path]:
        """Wrap this module's `read_and_gate`: gate for real, then swap on disk."""
        seen: list[Path] = []
        real = module.read_and_gate

        def wrapper(path: Path, kind: str) -> Any:
            result = real(path, kind)
            if Path(path).resolve() == target.resolve():
                seen.append(Path(path))
                target.write_text(hostile)
            return result

        monkeypatch.setattr(module, "read_and_gate", wrapper)
        return seen

    checkout = _checkout(tmp_path)
    monkeypatch.chdir(checkout)

    # --- the manifest overlay ------------------------------------------------
    shipped = load_manifest()
    name = next(iter(shipped.servers))
    overlay = checkout / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(_overlay_yaml(name, _OVERLAY_COMMAND))
    approve_project_file(overlay)
    overlay_gated = _swapping_gate(
        manifest_loader, overlay, _overlay_yaml(name, "hostile-overlay-command")
    )

    entry = load_manifest().get_server(name)

    assert overlay_gated == [overlay], "the overlay loader never consulted the gate"
    assert entry is not None
    assert entry.command == _OVERLAY_COMMAND
    assert "hostile-overlay-command" in overlay.read_text()  # the swap really landed

    # --- the project `.mcp.json` --------------------------------------------
    from pmcp.config import loader as config_loader

    config_path = checkout / ".mcp.json"
    config_path.write_text(_mcp_json("gated-server"))
    approve_project_file(config_path)
    config_gated = _swapping_gate(
        config_loader, config_path, _mcp_json("hostile-server")
    )

    names = [c.name for c in load_configs(project_root=checkout, user_config_paths=[])]

    assert config_gated == [config_path], "load_configs never consulted the gate"
    assert "gated-server" in names
    assert "hostile-server" not in names

    # --- the project policy --------------------------------------------------
    policy_path = checkout / ".mcp-gateway-policy.yaml"
    policy_path.write_text(json.dumps({"servers": {"denylist": ["alpha"]}}))
    approve_project_file(policy_path)
    monkeypatch.setattr(policy_module, "DEFAULT_POLICY_PATHS", [policy_path])
    policy_gated = _swapping_gate(
        policy_module, policy_path, json.dumps({"servers": {"denylist": ["beta"]}})
    )

    manager = PolicyManager()

    assert policy_gated == [policy_path], "PolicyManager never consulted the gate"
    assert manager.is_server_allowed("alpha") is False
    assert manager.is_server_allowed("beta") is True


# --------------------------------------------------------------------------- #
# S-11 -- a project policy silently shadowed the operator's global policy.
# --------------------------------------------------------------------------- #


def _discovery(
    monkeypatch: pytest.MonkeyPatch, *, project: Path | None, user: Path | None
) -> None:
    """Point discovery at exactly these candidates; `USER_POLICY_PATHS` marks
    which discovered path is the operator's own and therefore ungated."""
    users = [user] if user is not None else []
    monkeypatch.setattr(policy_module, "USER_POLICY_PATHS", users)
    monkeypatch.setattr(
        policy_module,
        "DEFAULT_POLICY_PATHS",
        ([project] if project is not None else []) + users,
    )


def _user_only_digest(policy_data: dict[str, Any]) -> str:
    """The digest a manager with no project policy at all would produce.

    Computed from the model rather than from a second `PolicyManager`, so "the
    project file contributed nothing" is checked against a fixed value instead
    of against another run of the code under test.
    """
    payload = GatewayPolicy.model_validate(policy_data).model_dump_json(
        exclude_none=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_s11_an_unapproved_project_policy_is_not_read_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Refused bytes must never reach the parser, not merely never be applied.

    The probe is a project policy that is *schema-invalid* (``denylist`` as a
    scalar) and that would allow a server the operator denied. Consiliency/pmcp#202
    makes a discovered policy that parses but fails validation a startup refusal,
    so an implementation that parsed before gating -- or gated and parsed anyway
    -- raises here. The read counter closes the other half: the gate performs
    exactly one ``read_bytes`` to hash the content, and nothing opens the path
    again.

    Then the same manager is handed to a real `GatewayTools`, because S-11 is a
    finding about what the gateway *enforces*, not about what a policy object
    reports.
    """
    user_data = {"servers": {"denylist": ["shipped"]}}
    user = tmp_path / "user-policy.yaml"
    user.write_text(json.dumps(user_data))
    project = tmp_path / "checkout" / ".mcp-gateway-policy.yaml"
    project.parent.mkdir(parents=True)
    project.write_text("servers:\n  allowlist: [shipped]\n  denylist: not-a-list\n")
    _discovery(monkeypatch, project=project, user=user)

    reads: list[str] = []
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes

    def counting_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if Path(self).resolve() == project.resolve():
            reads.append("read_text")
        return real_read_text(self, *args, **kwargs)

    def counting_read_bytes(self: Path, *args: Any, **kwargs: Any) -> bytes:
        if Path(self).resolve() == project.resolve():
            reads.append("read_bytes")
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)

    manager = PolicyManager()

    assert manager.is_server_allowed("shipped") is False
    assert manager.policy_digest == _user_only_digest(user_data)
    assert reads == ["read_bytes"], reads

    monkeypatch.setattr(Path, "read_text", real_read_text)
    monkeypatch.setattr(Path, "read_bytes", real_read_bytes)

    servers = {"shipped": _manifest_server("shipped", ["-y", "@shipped/server"])}
    gateway, client, _jobs = _gateway(monkeypatch, manager, manifest_servers=servers)

    refused = await gateway.connect_server({"server_name": "shipped"})

    assert refused.ok is False
    assert client.connected == []
    assert _SPAWN_ATTEMPTS == []

    # Positive control: the operator's own policy allows it, and the same
    # manifest server connects -- so the refusal above is the user denial being
    # honoured and not a gateway that cannot connect anything.
    permissive = tmp_path / "permissive.yaml"
    permissive.write_text(json.dumps({"servers": {"allowlist": ["shipped"]}}))
    _discovery(monkeypatch, project=project, user=permissive)
    gateway2, client2, _jobs2 = _gateway(
        monkeypatch, PolicyManager(), manifest_servers=servers
    )
    allowed = await gateway2.connect_server({"server_name": "shipped"})
    assert allowed.ok is True, allowed.message
    assert [c.name for c in client2.connected] == ["shipped"]


def test_s11_an_approved_project_policy_cannot_widen_the_user_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    """Consent decides whether the file is read; it never makes it authoritative.

    Approval is the interesting case: the unapproved file is refused by the
    consent gate, which says nothing about composition. All five list surfaces
    are checked because a lane could compose ``servers`` and forget ``tools``,
    and the gap would show up nowhere else.
    """
    user = tmp_path / "user-policy.yaml"
    user.write_text(
        json.dumps(
            {
                "servers": {"denylist": ["evil"]},
                "tools": {"denylist": ["evil::*"]},
                "gateway_tools": {"denylist": ["gateway.provision"]},
                "resources": {"denylist": ["evil::*"]},
                "prompts": {"denylist": ["evil::*"]},
            }
        )
    )
    project = tmp_path / "checkout" / ".mcp-gateway-policy.yaml"
    project.parent.mkdir(parents=True)
    project.write_text(
        json.dumps(
            {
                "servers": {"allowlist": ["evil", "good"]},
                "tools": {"allowlist": ["evil::*"]},
                "gateway_tools": {"allowlist": ["gateway.provision"]},
                "resources": {"allowlist": ["evil::*"]},
                "prompts": {"allowlist": ["evil::*"]},
            }
        )
    )
    approve_project_file(project)
    _discovery(monkeypatch, project=project, user=user)

    manager = PolicyManager()

    assert manager.is_server_allowed("evil") is False
    assert manager.is_tool_allowed("evil::run") is False
    assert manager.is_gateway_tool_allowed("gateway.provision") is False
    assert manager.is_resource_allowed("evil::file") is False
    assert manager.is_prompt_allowed("evil::greet") is False

    # Positive control, in the only direction an approved project policy may
    # move: NARROWING. Without it every assertion above is equally true of an
    # implementation that discards project policies outright, which is a
    # different (and also wrong) design than the one shipped.
    narrowing = tmp_path / "checkout2" / ".mcp-gateway-policy.yaml"
    narrowing.parent.mkdir(parents=True)
    narrowing.write_text(json.dumps({"servers": {"denylist": ["good"]}}))
    approve_project_file(narrowing)
    permissive_user = tmp_path / "permissive-user.yaml"
    permissive_user.write_text(json.dumps({"servers": {"allowlist": ["*"]}}))
    _discovery(monkeypatch, project=narrowing, user=permissive_user)

    narrowed = PolicyManager()

    assert narrowed.is_server_allowed("good") is False
    assert narrowed.is_server_allowed("other") is True


def test_s11_an_approved_project_policy_cannot_drop_the_default_redaction_patterns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, approve_project_file: Any
) -> None:
    """An S-11-class widening whose only falsifier shipped in no criterion.

    ``_compile_redaction_patterns`` reads ``patterns or DEFAULT_REDACTION_PATTERNS``,
    so an empty user list means "the defaults". Unioning the *raw* lists gives
    ``[] + ["projtoken-..."]``, which is truthy -- the defaults stop applying and
    a repository file has quietly reduced secret redaction for the whole gateway.
    The word "redaction" appears zero times in CONSENT's acceptance criteria.
    """
    assert DEFAULT_REDACTION_PATTERNS  # guard: the constant still exists
    user = tmp_path / "user-policy.yaml"
    user.write_text(json.dumps({"servers": {"denylist": ["evil"]}}))  # no redaction
    project = tmp_path / "checkout" / ".mcp-gateway-policy.yaml"
    project.parent.mkdir(parents=True)
    project.write_text(
        json.dumps({"redaction": {"patterns": [r"projtoken-[A-Za-z0-9]+"]}})
    )
    approve_project_file(project)
    _discovery(monkeypatch, project=project, user=user)

    redacted = PolicyManager().redact_secrets(
        "ghp_abcdefghijklmnop and projtoken-deadbeef together"
    )

    assert "ghp_abcdefghijklmnop" not in redacted  # a DEFAULT pattern still applies
    assert "projtoken-deadbeef" not in redacted  # and the project pattern was added


# --------------------------------------------------------------------------- #
# S-04 -- feedback posted under the operator's ambient GitHub identity.
# --------------------------------------------------------------------------- #

_TITLE = "Gateway refuses to start after upgrade"
_DESCRIPTION = "The gateway exits during startup with no diagnostic."


class _Transport:
    """Stands where the real submitter stands, and records instead of sending."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> feedback_egress.FeedbackSubmission:
        self.calls.append(kwargs)
        return feedback_egress.FeedbackSubmission(
            outcome="created",
            issue_url="https://github.com/o/r/issues/7",
            issue_number=7,
            repository_visibility="public",
            detail="created",
        )


def _install_transport(monkeypatch: pytest.MonkeyPatch, transport: _Transport) -> None:
    """Replace the submitter at both names.

    The handler binds `submit_feedback_issue` at import, so patching only the
    `feedback_egress` attribute would not reach it.
    """
    monkeypatch.setattr(handlers_module, "submit_feedback_issue", transport)
    monkeypatch.setattr(feedback_egress, "submit_feedback_issue", transport)


def _feedback_gateway(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> GatewayTools:
    gateway, _client, _jobs = _gateway(
        monkeypatch,
        PolicyManager(policy_path=None),
        project_root=project_root,
        guidance=GuidanceConfig(enable_telemetry=True, enable_feedback_submission=True),
    )
    return gateway


async def _submit(gateway: GatewayTools, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "title": _TITLE,
        "description": _DESCRIPTION,
        "issue_type": "bug",
        "confirm_submission": True,
    }
    payload.update(overrides)
    return await gateway.submit_feedback(payload)


@pytest.mark.asyncio
async def test_s04_an_ambient_github_token_submits_nothing_and_spawns_no_gh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gh_looks_installed: None
) -> None:
    """The operator's own GitHub identity is not pmcp's to spend.

    Every door at once, which is EGRESS amendment 1's lesson: the criteria named
    three and there were four. Deleting ``GITHUB_TOKEN`` from the token lookup
    satisfies the literal criterion and changes nothing for the ``gh`` door,
    which authenticates from the inherited environment -- so ``gh`` is made to
    look installed here and the spawn accumulator is asserted empty.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-the-operators-personal-token")
    monkeypatch.setenv("GH_TOKEN", "ghp-the-operators-personal-token")
    transport = _Transport()
    _install_transport(monkeypatch, transport)
    project = _checkout(tmp_path)

    result = await _submit(_feedback_gateway(monkeypatch, project))

    assert transport.calls == [], "a submission was attempted under an ambient token"
    assert _SPAWN_ATTEMPTS == [], "the gh door was opened under an ambient token"
    assert _NETWORK_ATTEMPTS == [], "a request was attempted under an ambient token"
    assert result.submitted is False
    assert result.submission_outcome is None
    assert _TOKEN_VAR in result.message

    # Positive control: a token the OPERATOR exported, by no pmcp route at all,
    # and the same call submits. Without it "nothing was posted" is also true of
    # a handler whose submission path is simply gone.
    monkeypatch.setenv(_TOKEN_VAR, "ghp-operator-exported-secret-value")
    allowed = await _submit(_feedback_gateway(monkeypatch, project))
    assert len(transport.calls) == 1
    assert allowed.submitted is True, allowed.message
    assert _SPAWN_ATTEMPTS == []


@pytest.mark.asyncio
async def test_s04_a_checkout_supplied_token_or_repository_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, gh_looks_installed: None
) -> None:
    """A repository's `.env` reaches the gateway's own environment.

    Both halves of the finding: the credential pmcp would post WITH, and the
    destination it would post TO. The `.env` is loaded through the real
    `cli.load_startup_env`, so the provenance registry is populated exactly as
    a startup populates it.
    """
    transport = _Transport()
    _install_transport(monkeypatch, transport)

    # (a) the credential
    token_checkout = _checkout(tmp_path, "token-checkout")
    (token_checkout / ".env").write_text(f"{_TOKEN_VAR}=ghp-planted-by-a-checkout\n")
    monkeypatch.chdir(token_checkout)
    cli.load_startup_env(token_checkout / ".env")
    assert os.environ[_TOKEN_VAR] == "ghp-planted-by-a-checkout"

    refused = await _submit(_feedback_gateway(monkeypatch, token_checkout))

    assert transport.calls == [], "pmcp posted under a token a checkout supplied"
    assert refused.ok is False
    assert refused.submitted is False
    assert _SPAWN_ATTEMPTS == []

    # (b) the destination
    os.environ.pop(_TOKEN_VAR, None)
    repo_checkout = _checkout(tmp_path, "repo-checkout")
    hostile = "attacker/evil"
    (repo_checkout / ".env").write_text(
        f"{_REPO_VAR}={hostile}\n{_TOKEN_VAR}=ghp-planted-by-a-checkout\n"
    )
    monkeypatch.chdir(repo_checkout)
    cli.load_startup_env(repo_checkout / ".env")
    assert os.environ[_REPO_VAR] == hostile

    redirected = await _submit(_feedback_gateway(monkeypatch, repo_checkout))

    assert transport.calls == []
    assert redirected.ok is False
    assert redirected.submitted is False
    # The destination is resolved FIRST, so no attacker-chosen value reaches any
    # output -- not the repository field, and not the browser URL door 4 renders.
    assert redirected.repository == feedback_egress.PACKAGED_FEEDBACK_REPOSITORY
    assert redirected.issue_url is None
    assert hostile not in (redirected.issue_url or "")
    assert _NETWORK_ATTEMPTS == []


@pytest.mark.asyncio
async def test_s04_no_egress_door_remains_in_the_handler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Deleted rather than gated, and asserted structurally as well.

    A gate in front of an unattributable identity still posts under it, so the
    ``gh`` fallback is gone rather than guarded. A behavioural assertion alone
    cannot tell "the door is closed today" from "the door is gone": this reads
    the handler's own source, which is the form the property is true in.
    """
    source = inspect.getsource(GatewayTools.submit_feedback)

    assert not re.search(r"[\"']gh[\"']", source), "the gh door is still in the handler"
    assert "create_subprocess_exec" not in source
    assert "urlopen" not in source
    # The module no longer carries the names those doors were built from, so a
    # future edit cannot reach them without re-importing first.
    assert not hasattr(handlers_module, "urlopen")
    assert not hasattr(handlers_module, "urlencode")
    assert not hasattr(handlers_module, "shutil")

    # And behaviourally, with every door watched: an unauthorised submission
    # attempt leaves all three accumulators empty.
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    transport = _Transport()
    _install_transport(monkeypatch, transport)

    result = await _submit(_feedback_gateway(monkeypatch, _checkout(tmp_path)))

    assert result.submitted is False
    assert transport.calls == []
    assert _SPAWN_ATTEMPTS == []
    assert _NETWORK_ATTEMPTS == []


# --------------------------------------------------------------------------- #
# The isolation layer, as a node id rather than a fixture side effect.
#
# Last in the file on purpose: in a file run it sees every reproduction above.
# Run alone it still asserts something real -- that all four guards are actually
# installed -- because a guard that silently stopped applying is precisely how
# EGRESS's red run reached a live `gh issue create` on the development host.
# --------------------------------------------------------------------------- #


def test_no_reproduction_in_this_suite_reached_the_network() -> None:
    """Nothing in this suite reached the network or spawned a process."""
    assert _NETWORK_ATTEMPTS == [], (
        f"a reproduction in this suite reached the network: {_NETWORK_ATTEMPTS}"
    )
    assert _SPAWN_ATTEMPTS == [], (
        f"a reproduction in this suite spawned a process: {_SPAWN_ATTEMPTS}"
    )

    # The guards are this module's, and are in force right now. Asserted by
    # identity rather than by behaviour so the check itself cannot be the thing
    # that opens a door.
    installed: list[Callable[..., Any]] = [
        package_identity._OPENER.open,
        feedback_egress._OPENER.open,
        cast(Any, socket.socket.connect),
        cast(Any, socket.socket.connect_ex),
        cast(Any, asyncio.create_subprocess_exec),
        cast(Any, asyncio.create_subprocess_shell),
    ]
    for guard in installed:
        assert getattr(guard, "__module__", None) == __name__, guard
