"""PKGID review-panel fixes (Consiliency/pmcp#230).

The assembled PKGID phase went through a cross-vendor review. Each section below
pins one operator decision, and every test drives a HANDLER, not only the gate:
the gap in F2 went unseen because a gate-level test proved `evaluate_provision`
with an identity production never supplies.

- F1: a discovered server may declare only credential-shaped env vars, and none
  that configure a package manager or runtime -- else an approved package's
  spawn can be pointed at a different registry.
- F2: `packages.denylist` binds manifest-backed servers too, by the package
  names their trusted config names.
- F4: `provision` consults the package gate before it asks for a credential.
- F5: `update_server` never probes or restarts a discovered server.

Offline and isolated: the registry is `fake_npm_registry`, and the autouse
`tests/conftest.py` fixture redirects HOME, so the package-approval store and
the user secret store both live under a per-test home.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from pmcp.manifest import package_identity
from pmcp.manifest.installer import build_install_child_env
from pmcp.manifest.loader import Manifest, ServerConfig
from pmcp.manifest.package_identity import PackageIdentity
from pmcp.package_approvals import approve_package
from pmcp.policy.policy import PolicyManager
from pmcp.provision_gate import evaluate_provision
from pmcp.tools import handlers as handlers_module
from pmcp.tools.handlers import GatewayTools
from pmcp.validation import discovered_env_var_allowed

PLATFORMS = ("mac", "linux", "wsl", "windows")
LEGIT = "legit-mcp"
LEGIT_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _ClientManager:
    """Records every config a lifecycle call would have spawned."""

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


class _JobManager:
    """Stands in for `JobManager`: records every install it is asked to spawn."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], ServerConfig]] = []
        # SEAL SL-0: the project root production hands start_install. Recorded
        # alongside `calls` rather than widened into it, so the 2-tuple equality
        # assertions below keep their shape; a double that accepted the argument
        # and dropped it could not notice a wrong root.
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


def _policy(tmp_path: Path, body: str) -> PolicyManager:
    path = tmp_path / "gateway-policy.yaml"
    path.write_text(body, encoding="utf-8")
    return PolicyManager(policy_path=path)


def _gateway(
    monkeypatch: pytest.MonkeyPatch,
    policy: PolicyManager,
    manifest_servers: dict[str, ServerConfig] | None = None,
) -> tuple[GatewayTools, _ClientManager, _JobManager]:
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers=dict(manifest_servers or {}),
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )
    jobs = _JobManager()
    manager = _ClientManager()
    monkeypatch.setattr(handlers_module, "load_manifest", lambda: manifest)
    monkeypatch.setattr(handlers_module, "load_configs", lambda **_: [])
    monkeypatch.setattr(handlers_module, "get_job_manager", lambda: jobs)
    gateway = GatewayTools(client_manager=cast(Any, manager), policy_manager=policy)
    cast(Any, gateway)._platform = "linux"
    return gateway, manager, jobs


def _manifest_server(
    name: str,
    *,
    command: str = "npx",
    args: list[str] | None = None,
    install: list[str] | None = None,
    **extra: Any,
) -> ServerConfig:
    argv = install if install is not None else [command, *(args or [])]
    return ServerConfig(
        name=name,
        description=f"{name} server",
        keywords=[name],
        install={p: list(argv) for p in PLATFORMS},
        command=command,
        args=list(args or []),
        **extra,
    )


@pytest.fixture
def scrubbed_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Unset env vars for one test, and remove anything a handler sets in them."""

    def scrub(*names: str) -> None:
        for name in names:
            # setenv-then-delenv records the ORIGINAL value, so monkeypatch's
            # undo also removes a value `auth_connect` writes during the test.
            monkeypatch.setenv(name, "placeholder")
            monkeypatch.delenv(name)

    return scrub


@pytest.fixture
def fetches(
    monkeypatch: pytest.MonkeyPatch, fake_npm_registry: dict[str, str]
) -> Iterator[list[str]]:
    """The fake registry, recording every package name it was asked for."""
    seen: list[str] = []
    fetch = package_identity._fetch_packument

    def recording(name: str) -> dict[str, Any] | None:
        seen.append(name)
        return fetch(name)

    monkeypatch.setattr(package_identity, "_fetch_packument", recording)
    fake_npm_registry[LEGIT] = LEGIT_VERSION
    yield seen


# ---------------------------------------------------------------------------
# F1 -- an agent-declared env var must not redirect an approved package
# ---------------------------------------------------------------------------


_REFUSED_DISCOVERED_NAMES = [
    # The panel's reproduction, and why a name-blocklist missed it.
    "npm_config_registry",
    # Credential-shaped, and still package-manager configuration.
    "NPM_CONFIG__AUTH",
    "Npm_Config__AUTH",
    "NODE_AUTH_TOKEN",
    "node_AUTH_TOKEN",
    "COREPACK_NPM_TOKEN",
    "YARN_NPM_AUTH_TOKEN",
    "PNPM_REGISTRY_TOKEN",
    "BUN_AUTH_TOKEN",
    # Code-loading, whatever it looks like.
    "LD_PRELOAD",
    "NODE_OPTIONS",
    "PYTHON_API_KEY",
    # Not credential-shaped: a discovered server has no manifest to vouch for it.
    "POSTGRES_URL",
    "HTTPS_PROXY",
]


@pytest.mark.parametrize("name", _REFUSED_DISCOVERED_NAMES)
def test_the_discovered_env_var_predicate_is_an_allowlist(name: str) -> None:
    assert discovered_env_var_allowed(name) is False


@pytest.mark.parametrize(
    "name", ["GITHUB_TOKEN", "ACME_API_KEY", "SENTRY_DSN", "STRIPE_SECRET"]
)
def test_the_discovered_env_var_predicate_admits_credential_names(name: str) -> None:
    assert discovered_env_var_allowed(name) is True


@pytest.mark.asyncio
async def test_registration_refuses_the_panel_reproduction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetches: list[str],
    scrubbed_env: Any,
) -> None:
    """register(env_vars=["npm_config_registry"]) -> auth_connect -> spawn env.

    Before the fix all three steps succeeded: the gate allowed the approved
    package, `auth_connect` stored the attacker's registry URL, and
    `build_install_child_env` injected it into the pinned `npx -y` spawn.
    """
    scrubbed_env("npm_config_registry")
    gateway, _manager, jobs = _gateway(monkeypatch, _policy(tmp_path, "servers: {}\n"))

    registered = await gateway.register_discovered_server(
        {
            "package": LEGIT,
            "server_name": "legit2",
            "env_vars": ["npm_config_registry"],
        }
    )

    assert registered.ok is False
    assert registered.registered is False
    assert "npm_config_registry" in registered.message
    assert "Nothing was registered" in registered.message
    assert "legit2" not in gateway._discovered_server_configs
    assert "legit2" not in gateway._discovered_server_identities
    assert fetches == [], "a refused registration must not reach the registry"

    stored = await gateway.auth_connect(
        {"server_name": "legit2", "credential": "https://evil.example/"}
    )
    assert stored.ok is False
    assert "npm_config_registry" not in os.environ
    assert jobs.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "env_vars",
    [
        ["NPM_CONFIG__AUTH"],
        ["NODE_AUTH_TOKEN"],
        # An extra var is exported by the operator or stored by auth_connect
        # too, so every entry is checked, not only the first.
        ["ACME_API_TOKEN", "npm_config_registry"],
        ["ACME_API_TOKEN", "YARN_NPM_AUTH_TOKEN"],
    ],
)
async def test_registration_refuses_any_disallowed_declared_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetches: list[str],
    env_vars: list[str],
) -> None:
    gateway, _manager, _jobs = _gateway(monkeypatch, _policy(tmp_path, "servers: {}\n"))

    registered = await gateway.register_discovered_server(
        {"package": LEGIT, "server_name": "legit2", "env_vars": env_vars}
    )

    assert (registered.ok, registered.registered) == (False, False)
    assert "legit2" not in gateway._discovered_server_configs
    assert fetches == []


@pytest.mark.asyncio
async def test_a_hostile_declared_name_is_rendered_inert_in_the_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fetches: list[str]
) -> None:
    gateway, _manager, _jobs = _gateway(monkeypatch, _policy(tmp_path, "servers: {}\n"))
    hostile = "X$(touch CANARY)\x1b[2J_TOKEN"

    registered = await gateway.register_discovered_server(
        {"package": LEGIT, "server_name": "legit2", "env_vars": [hostile]}
    )

    assert registered.registered is False
    assert registered.message.isprintable()
    assert "'X$(touch CANARY)\\x1b[2J_TOKEN'" in registered.message


@pytest.mark.asyncio
async def test_auth_connect_refuses_a_package_manager_override_for_a_discovered_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetches: list[str],
    scrubbed_env: Any,
) -> None:
    """A discovered server with NO declared var takes any credential-shaped
    override today -- and ``NPM_CONFIG__AUTH`` is credential-shaped."""
    scrubbed_env("NPM_CONFIG__AUTH", "NODE_AUTH_TOKEN")
    gateway, _manager, _jobs = _gateway(monkeypatch, _policy(tmp_path, "servers: {}\n"))
    registered = await gateway.register_discovered_server(
        {"package": LEGIT, "server_name": "legit2"}
    )
    assert registered.registered is True

    for name in ("NPM_CONFIG__AUTH", "NODE_AUTH_TOKEN"):
        result = await gateway.auth_connect(
            {"server_name": "legit2", "env_var": name, "credential": "secret-value"}
        )
        assert result.ok is False, name
        assert "not permitted" in result.message
        assert name not in os.environ


@pytest.mark.asyncio
async def test_auth_connect_refuses_a_disallowed_name_a_discovered_config_declares(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetches: list[str],
    scrubbed_env: Any,
) -> None:
    """Registration is one door; `auth_connect` must not trust the config it left.

    The config is planted directly, as a registration path that forgot the rule
    would leave it.
    """
    scrubbed_env("npm_config_registry")
    gateway, _manager, _jobs = _gateway(monkeypatch, _policy(tmp_path, "servers: {}\n"))
    spec = f"{LEGIT}@{LEGIT_VERSION}"
    gateway._discovered_server_configs["legit2"] = _manifest_server(
        "legit2",
        args=["-y", spec],
        requires_api_key=True,
        env_var="npm_config_registry",
    )

    result = await gateway.auth_connect(
        {"server_name": "legit2", "credential": "https://evil.example/"}
    )

    assert result.ok is False
    assert "npm_config_registry" not in os.environ


@pytest.mark.asyncio
async def test_the_auth_connect_rule_is_keyed_on_the_lookup_not_the_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scrubbed_env: Any
) -> None:
    """One non-credential name, declared twice: the manifest's is stored, the
    discovered config's is refused. Only which lookup found the config differs."""
    scrubbed_env("ACME_BASE_URL")
    shipped = _manifest_server(
        "shipped",
        args=["-y", "@shipped/server"],
        requires_api_key=True,
        env_var="ACME_BASE_URL",
    )
    gateway, _manager, _jobs = _gateway(
        monkeypatch,
        _policy(tmp_path, "servers: {}\n"),
        manifest_servers={"shipped": shipped},
    )
    gateway._discovered_server_configs["planted"] = _manifest_server(
        "planted",
        args=["-y", f"{LEGIT}@{LEGIT_VERSION}"],
        requires_api_key=True,
        env_var="ACME_BASE_URL",
    )

    planted = await gateway.auth_connect(
        {"server_name": "planted", "credential": "https://evil.example/"}
    )
    assert planted.ok is False
    assert "ACME_BASE_URL" not in os.environ

    manifest = await gateway.auth_connect(
        {"server_name": "shipped", "credential": "https://acme.internal/"}
    )
    assert manifest.ok is True, manifest.message
    assert os.environ["ACME_BASE_URL"] == "https://acme.internal/"


@pytest.mark.asyncio
async def test_a_credential_shaped_discovered_env_var_works_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetches: list[str],
    scrubbed_env: Any,
) -> None:
    scrubbed_env("ACME_API_TOKEN")
    gateway, _manager, jobs = _gateway(monkeypatch, _policy(tmp_path, "servers: {}\n"))

    registered = await gateway.register_discovered_server(
        {"package": LEGIT, "server_name": "legit2", "env_vars": ["ACME_API_TOKEN"]}
    )
    assert registered.registered is True
    approve_package(gateway._discovered_server_identities["legit2"])

    stored = await gateway.auth_connect(
        {"server_name": "legit2", "credential": "acme-secret"}
    )
    assert stored.ok is True, stored.message
    assert stored.env_var == "ACME_API_TOKEN"

    provisioned = await gateway.provision({"server_name": "legit2"})
    assert provisioned.ok is True, provisioned.message
    assert jobs.calls == [(["npx", "-y", f"{LEGIT}@{LEGIT_VERSION}"], jobs.calls[0][1])]
    child_env = build_install_child_env(jobs.calls[0][1])
    assert child_env["ACME_API_TOKEN"] == "acme-secret"


@pytest.mark.asyncio
async def test_a_manifest_server_keeps_a_non_credential_shaped_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scrubbed_env: Any
) -> None:
    """20 shipped manifest env_vars are not credential-shaped; they keep working.

    `POSTGRES_URL` is one the discovered-server allowlist refuses (see
    `test_the_discovered_env_var_predicate_is_an_allowlist`).
    """
    scrubbed_env("POSTGRES_URL")
    postgres = _manifest_server(
        "postgres",
        args=["-y", "@modelcontextprotocol/server-postgres"],
        requires_api_key=True,
        env_var="POSTGRES_URL",
    )
    gateway, _manager, _jobs = _gateway(
        monkeypatch,
        _policy(tmp_path, "servers: {}\n"),
        manifest_servers={"postgres": postgres},
    )

    result = await gateway.auth_connect(
        {"server_name": "postgres", "credential": "postgres://db.internal/app"}
    )

    assert result.ok is True, result.message
    assert os.environ["POSTGRES_URL"] == "postgres://db.internal/app"


# ---------------------------------------------------------------------------
# F2 -- packages.denylist binds manifest-backed servers
# ---------------------------------------------------------------------------


def _playwright() -> ServerConfig:
    return _manifest_server("playwright", args=["-y", "@playwright/mcp@latest"])


@pytest.mark.asyncio
async def test_a_denylisted_manifest_package_is_refused_by_every_spawning_door(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _policy(tmp_path, 'packages:\n  denylist:\n    - "@playwright/*"\n')
    gateway, manager, jobs = _gateway(
        monkeypatch, policy, manifest_servers={"playwright": _playwright()}
    )

    provisioned = await gateway.provision({"server_name": "playwright"})
    assert provisioned.ok is False
    assert provisioned.auth_state == "policy_denied"
    assert "@playwright/mcp" in provisioned.message
    assert "denylist" in provisioned.message
    assert "approve-package" not in provisioned.message
    assert jobs.calls == []

    for verb in ("connect_server", "restart_server"):
        result = await getattr(gateway, verb)({"server_name": "playwright"})
        assert result.ok is False, verb
        assert result.auth_state == "policy_denied", verb
        assert "@playwright/mcp" in result.message, verb
    assert manager.connected == []

    # Disconnect spawns nothing and stays ungated.
    disconnected = await gateway.disconnect_server({"server_name": "playwright"})
    assert disconnected.ok is True
    assert manager.disconnected == ["playwright"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        "servers: {}\n",
        'packages:\n  denylist:\n    - "@nothing/*"\n',
        # An allowlist miss is "unspecified", never a denial.
        'packages:\n  allowlist:\n    - "@nothing/*"\n',
    ],
)
async def test_a_manifest_server_no_denylist_names_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    server = _playwright()
    gateway, manager, jobs = _gateway(
        monkeypatch, _policy(tmp_path, body), manifest_servers={"playwright": server}
    )

    provisioned = await gateway.provision({"server_name": "playwright"})
    assert provisioned.ok is True, provisioned.message
    assert provisioned.status == "started"
    assert jobs.calls == [(["npx", "-y", "@playwright/mcp@latest"], server)]

    connected = await gateway.connect_server({"server_name": "playwright"})
    assert connected.ok is True, connected.message
    restarted = await gateway.restart_server({"server_name": "playwright"})
    assert restarted.ok is True, restarted.message
    assert len(manager.connected) == 2


@pytest.mark.asyncio
async def test_a_manifest_package_named_only_by_its_install_argv_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The server runs an installed binary; only the install argv fetches.
    server = ServerConfig(
        name="binary",
        description="binary",
        keywords=[],
        install={
            "linux": ["npx", "--yes", "-q", "denied-pkg@2.0.0"],
            "mac": ["brew", "install", "denied-pkg"],
        },
        command="denied-pkg-server",
        args=["--stdio"],
    )
    policy = _policy(tmp_path, "packages:\n  denylist:\n    - denied-pkg\n")
    gateway, manager, jobs = _gateway(
        monkeypatch, policy, manifest_servers={"binary": server}
    )

    provisioned = await gateway.provision({"server_name": "binary"})
    assert provisioned.ok is False
    assert provisioned.auth_state == "policy_denied"
    assert "denied-pkg" in provisioned.message
    assert jobs.calls == []
    connected = await gateway.connect_server({"server_name": "binary"})
    assert connected.ok is False
    assert manager.connected == []


@pytest.mark.asyncio
async def test_a_manifest_package_field_is_consulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = _manifest_server(
        "docker-backed",
        command="docker",
        args=["run", "-i", "acme/server"],
        package="@acme/mcp-server@3.1.0",
    )
    policy = _policy(tmp_path, 'packages:\n  denylist:\n    - "@acme/*"\n')
    gateway, _manager, jobs = _gateway(
        monkeypatch, policy, manifest_servers={"docker-backed": server}
    )

    provisioned = await gateway.provision({"server_name": "docker-backed"})

    assert provisioned.ok is False
    assert "@acme/mcp-server" in provisioned.message
    assert jobs.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("uvx", ["mcp-server-fetch"]),
        ("docker", ["run", "-i", "--rm", "mcp/fetch"]),
    ],
)
async def test_a_manifest_server_that_names_no_npm_package_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, args: list[str]
) -> None:
    # A denylist that matches EVERY name: nothing here names an npm package.
    server = _manifest_server("fetch", command=command, args=args)
    policy = _policy(tmp_path, 'packages:\n  denylist:\n    - "*"\n')
    gateway, manager, jobs = _gateway(
        monkeypatch, policy, manifest_servers={"fetch": server}
    )

    provisioned = await gateway.provision({"server_name": "fetch"})
    assert provisioned.ok is True, provisioned.message
    assert jobs.calls == [([command, *args], server)]
    connected = await gateway.connect_server({"server_name": "fetch"})
    assert connected.ok is True, connected.message
    assert len(manager.connected) == 1


@pytest.mark.asyncio
async def test_the_manifest_package_slot_is_found_by_position_not_by_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A denylisted name AFTER the package slot is an argument to the server.

    npx runs `allowed-pkg` and passes it the rest; nothing fetches
    `denied-pkg`. A rule that picked "whatever looks like a package" would
    refuse this server -- and would equally pick a credential that happens to be
    a valid package name.
    """
    server = _manifest_server(
        "positional",
        args=["-y", "allowed-pkg@1.0.0", "denied-pkg", "--profile", "denied-pkg@9.9.9"],
    )
    policy = _policy(tmp_path, "packages:\n  denylist:\n    - denied-pkg\n")
    gateway, _manager, jobs = _gateway(
        monkeypatch, policy, manifest_servers={"positional": server}
    )

    provisioned = await gateway.provision({"server_name": "positional"})

    assert provisioned.ok is True, provisioned.message
    assert len(jobs.calls) == 1


def test_the_name_policy_and_the_identity_policy_agree(tmp_path: Path) -> None:
    policy = _policy(
        tmp_path,
        "packages:\n  denylist:\n    - evil-*\n  allowlist:\n    - good-*\n    - evil-x\n",
    )
    for name, verdict in (
        ("evil-x", "denied"),
        ("good-y", "allowed"),
        ("other", "unspecified"),
    ):
        identity = PackageIdentity("npm", name, "1.0.0", None)
        assert policy.evaluate_package_name_policy(name) == verdict
        assert policy.evaluate_package_policy(identity) == verdict


def test_the_gate_denies_a_denylisted_manifest_config_without_an_identity(
    tmp_path: Path,
) -> None:
    policy = _policy(tmp_path, 'packages:\n  denylist:\n    - "@playwright/*"\n')

    decision = evaluate_provision(_playwright(), None, source="manifest", policy=policy)

    assert (decision.allowed, decision.reason, decision.identity) == (
        False,
        "denied",
        None,
    )
    assert decision.remedy is not None
    assert "@playwright/mcp" in decision.remedy
    assert "gateway-policy.yaml" in decision.remedy


@pytest.mark.asyncio
async def test_a_denylisted_manifest_server_is_refused_before_its_credential_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scrubbed_env: Any
) -> None:
    scrubbed_env("DENIED_API_TOKEN")
    server = _manifest_server(
        "keyed",
        args=["-y", "denied-pkg"],
        requires_api_key=True,
        env_var="DENIED_API_TOKEN",
    )
    policy = _policy(tmp_path, "packages:\n  denylist:\n    - denied-pkg\n")
    gateway, _manager, jobs = _gateway(
        monkeypatch, policy, manifest_servers={"keyed": server}
    )

    provisioned = await gateway.provision({"server_name": "keyed"})

    assert provisioned.ok is False
    assert provisioned.auth_state == "policy_denied"
    assert not provisioned.needs_api_key
    assert jobs.calls == []


# ---------------------------------------------------------------------------
# F4 -- provision gates the package before it asks for a credential
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unapproved_discovered_server_is_refused_before_its_credential_is_asked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetches: list[str],
    scrubbed_env: Any,
) -> None:
    scrubbed_env("ACME_API_TOKEN")
    gateway, _manager, jobs = _gateway(monkeypatch, _policy(tmp_path, "servers: {}\n"))
    registered = await gateway.register_discovered_server(
        {"package": LEGIT, "server_name": "legit2", "env_vars": ["ACME_API_TOKEN"]}
    )
    assert registered.registered is True

    provisioned = await gateway.provision({"server_name": "legit2"})

    assert provisioned.ok is False
    assert not provisioned.needs_api_key
    assert not provisioned.missing_env_vars
    assert provisioned.auth_state == "unknown"
    assert f"pmcp trust approve-package {LEGIT}@{LEGIT_VERSION}" in provisioned.message
    assert jobs.calls == []


@pytest.mark.asyncio
async def test_an_allowed_server_with_a_missing_credential_still_asks_for_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scrubbed_env: Any
) -> None:
    scrubbed_env("SHIPPED_API_TOKEN")
    server = _manifest_server(
        "shipped",
        args=["-y", "@shipped/server"],
        requires_api_key=True,
        env_var="SHIPPED_API_TOKEN",
    )
    gateway, _manager, jobs = _gateway(
        monkeypatch,
        _policy(tmp_path, "servers: {}\n"),
        manifest_servers={"shipped": server},
    )

    provisioned = await gateway.provision({"server_name": "shipped"})

    assert provisioned.ok is False
    assert provisioned.needs_api_key is True
    assert provisioned.env_var == "SHIPPED_API_TOKEN"
    assert jobs.calls == []


# ---------------------------------------------------------------------------
# F5 -- update_server never moves a discovered server
# ---------------------------------------------------------------------------


@pytest.fixture
def npm_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read npm argv with the node-less table scan, whatever npm the host has.

    `detect_package_type` asks the host npm's parser, which refuses under an
    ambient local prefix (see `tests/conftest.py`), and a refusal reads as
    "unknown package". These tests are about which spawns `update_server`
    reaches, so they must not depend on where the checkout lives.
    """
    from pmcp.manifest import version_checker

    def tables(
        args: list[str], command: str, env: Any = None, cwd: Any = None
    ) -> str | None:
        return version_checker._npm_package_arg_from_tables(args, command)

    monkeypatch.setattr(version_checker, "_npm_package_arg", tables)
    monkeypatch.setattr(handlers_module, "_npm_package_arg", tables)


async def _approved_discovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[GatewayTools, _ClientManager, list[list[str]]]:
    gateway, manager, _jobs = _gateway(monkeypatch, _policy(tmp_path, "servers: {}\n"))
    registered = await gateway.register_discovered_server(
        {"package": LEGIT, "server_name": "legit2"}
    )
    assert registered.registered is True
    approve_package(gateway._discovered_server_identities["legit2"])
    probes: list[list[str]] = []

    async def recording_probe(command: list[str], env: Any = None) -> tuple[bool, str]:
        probes.append(list(command))
        return (True, "ok")

    monkeypatch.setattr(gateway, "_run_update_probe_command", recording_probe)
    return gateway, manager, probes


def _assert_discovered_update_refusal(message: str) -> None:
    assert "register_discovered_server" in message
    assert "approve" in message
    assert "manifest entry" not in message


@pytest.mark.asyncio
async def test_update_server_refuses_a_discovered_server_even_if_pin_detection_misses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetches: list[str],
    npm_tables: None,
) -> None:
    gateway, manager, probes = await _approved_discovered(tmp_path, monkeypatch)
    monkeypatch.setattr(
        handlers_module, "_detect_effective_version_pin", lambda *a, **k: None
    )

    async def no_restart(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("update_server restarted a discovered server")

    monkeypatch.setattr(gateway, "_restart_resolved_server", no_restart)

    result = await gateway.update_server({"server_name": "legit2"})

    assert result.ok is False
    assert result.restarted is False
    assert probes == []
    assert manager.connected == []
    _assert_discovered_update_refusal(result.message)


@pytest.mark.asyncio
async def test_update_server_names_a_discovered_server_as_discovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetches: list[str],
    npm_tables: None,
) -> None:
    gateway, manager, probes = await _approved_discovered(tmp_path, monkeypatch)

    result = await gateway.update_server({"server_name": "legit2"})

    assert result.ok is False
    assert probes == []
    assert manager.connected == []
    _assert_discovered_update_refusal(result.message)


@pytest.mark.asyncio
async def test_update_server_still_probes_an_unpinned_manifest_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, npm_tables: None
) -> None:
    server = _manifest_server("shipped", args=["-y", "@shipped/server"])
    gateway, _manager, _jobs = _gateway(
        monkeypatch,
        _policy(tmp_path, "servers: {}\n"),
        manifest_servers={"shipped": server},
    )
    probes: list[list[str]] = []

    async def failing_probe(command: list[str], env: Any = None) -> tuple[bool, str]:
        probes.append(list(command))
        return (False, "probe output")

    monkeypatch.setattr(gateway, "_run_update_probe_command", failing_probe)

    result = await gateway.update_server({"server_name": "shipped"})

    assert probes == [["npx", "-y", "@shipped/server@latest", "--help"]]
    assert result.ok is False
    assert result.message == "Update command failed: probe output"


@pytest.mark.asyncio
async def test_update_server_still_refuses_a_pinned_manifest_server_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, npm_tables: None
) -> None:
    server = _manifest_server("shipped", args=["-y", "@shipped/server@1.2.3"])
    gateway, _manager, _jobs = _gateway(
        monkeypatch,
        _policy(tmp_path, "servers: {}\n"),
        manifest_servers={"shipped": server},
    )

    result = await gateway.update_server({"server_name": "shipped"})

    assert result.ok is False
    assert "is pinned to '1.2.3' in the manifest entry" in result.message
