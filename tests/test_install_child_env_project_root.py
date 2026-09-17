"""SEAL SL-0 — the install spawn strips credentials for the project root the
gateway was GIVEN, not for whatever directory it happens to be running in.

The defect these tests close (see Consiliency/pmcp#230, EC-SEAL-5):
``build_install_child_env`` ended in ``sanitized_subprocess_env(own_env)`` with
no ``project`` argument, so ``managed_secret_keys(None)`` resolved the project
store by walking up from ``Path.cwd()``. ``_write_secret`` writes a
project-scope credential through the gateway's own ``self._project_root``
(``handlers.py:3137``), and ``pmcp serve --project X`` sets that root
explicitly. Started from a different working directory, the write and the strip
therefore looked in two different places and the install child inherited
another server's secret.

Every behavioural assertion here reads the **recorded child environment** of a
real spawn driven through ``gateway.provision``. The helper called in isolation
cannot see this defect, which is why it survived the PKGID phase: with a single
project store in play the helper answers correctly no matter which root it
consulted. The divergence only exists when the cwd and the gateway's root are
two different directories, which is a property of the production call chain and
not of the helper.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import pmcp.manifest.loader as _loader_module
from pmcp import env_store
from pmcp.manifest.installer import JobManager, build_install_child_env
from pmcp.manifest.loader import ServerConfig, load_manifest
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools

# A credential PMCP holds for ANOTHER server: written into a project store, and
# present in the gateway's own environment (which is where a child inherits it
# from, and therefore where the strip has to remove it).
_SECRET_KEY = "SL0_OTHER_SERVER_TOKEN"
_SECRET_VALUE = "another-servers-secret"

# An ambient variable no PMCP store knows about. It must SURVIVE into the child:
# without it, "the secret is absent" would also be satisfied by an empty dict.
_CONTROL_KEY = "SL0_UNMANAGED_CONTROL"
_CONTROL_VALUE = "ambient"

# A manifest-backed, no-credential, installable server: provision routes it
# straight to JobManager.start_install, which is the production path under test.
_INSTALLABLE_SERVER = "context7"

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"

# The shipped manifest, loaded by explicit path so no private overlay applies.
_SHIPPED_MANIFEST = load_manifest(
    Path(_loader_module.__file__).parent / "manifest.yaml"
)


class _MinimalClientManager:
    """Nothing online, nothing registered — provision must reach the installer."""

    def get_all_tools(self) -> list[Any]:
        return []

    def get_tool(self, tool_id: str) -> None:
        return None

    def is_server_online(self, name: str) -> bool:
        return False

    def is_lazy_server(self, name: str) -> bool:
        return False

    def get_all_server_statuses(self) -> list[Any]:
        return []

    def get_registry_meta(self) -> tuple[str, float]:
        return ("test-rev", 0.0)

    async def call_tool(self, tool_id: str, args: Any, timeout_ms: int) -> Any:
        return {"content": [{"type": "text", "text": ""}]}

    async def refresh(self, configs: Any) -> list[Any]:
        return []

    async def connect_all(self, configs: Any, retry: bool = True) -> list[Any]:
        return []


def _gateway(project_root: Path) -> GatewayTools:
    """A gateway told, explicitly, which project root is its own.

    This is the ``pmcp serve --project X`` shape (``cli.py:2378``): the root is
    given, not discovered from the working directory.
    """
    return GatewayTools(
        client_manager=_MinimalClientManager(),  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
        project_root=project_root,
    )


def _no_credential_config() -> ServerConfig:
    """A manifest server that needs no credential of its own.

    Deliberately so: the only key in play is then another server's, and an
    assertion about it cannot be satisfied by this server's own ``own_env``
    being re-applied after the strip.
    """
    return ServerConfig(
        name="memory",
        description="memory",
        keywords=["memory"],
        install={"linux": ["npx", "-y", "@modelcontextprotocol/server-memory"]},
        command="npx",
        args=["-y", "@modelcontextprotocol/server-memory"],
        requires_api_key=False,
    )


@pytest.fixture
def scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Two directories and one leaked credential.

    ``gateway_root`` is the root the gateway is given and the root
    ``_write_secret`` writes through; ``elsewhere`` is where the operator
    happened to start it. Neither carries a project marker, so
    ``find_project_root`` declines both and ``resolve_project_root(None)``
    falls back to the cwd — which is precisely the walk the defect depended on.
    """
    gateway_root = tmp_path / "gateway-project"
    elsewhere = tmp_path / "elsewhere"
    gateway_root.mkdir()
    elsewhere.mkdir()

    written = env_store.set_env_value(
        "project", _SECRET_KEY, _SECRET_VALUE, gateway_root
    )
    assert written == gateway_root / ".env.pmcp"
    assert written.exists()

    monkeypatch.setenv(_SECRET_KEY, _SECRET_VALUE)
    monkeypatch.setenv(_CONTROL_KEY, _CONTROL_VALUE)
    return gateway_root, elsewhere


@pytest.fixture
def recorded_spawn(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record the environment the install child would really have received."""
    captured: dict[str, Any] = {}

    class _FakeProcess:
        returncode = None
        pid = 4321
        stdin = None
        stdout = None
        stderr = None

    async def _fake_exec(*args: Any, **kwargs: Any) -> _FakeProcess:
        captured["argv"] = list(args)
        captured["env"] = kwargs.get("env")
        return _FakeProcess()

    monkeypatch.setattr(
        "pmcp.manifest.installer.asyncio.create_subprocess_exec", _fake_exec
    )
    # Keep the background monitor away from the fake process.
    monkeypatch.setattr(JobManager, "_monitor_install", AsyncMock(return_value=None))
    return captured


def _use_shipped_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: _SHIPPED_MANIFEST)
    monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])


def _assert_child_env_is_real(child_env: Any) -> dict[str, str]:
    """The recorded env is a real inherited environment, not an empty dict."""
    assert isinstance(child_env, dict), f"no spawn recorded: {child_env!r}"
    assert child_env.get(_CONTROL_KEY) == _CONTROL_VALUE, (
        "the child did not inherit the gateway's ambient environment, so the "
        "absence of any other key proves nothing"
    )
    assert os.environ.get(_SECRET_KEY) == _SECRET_VALUE, (
        "the gateway was not holding the credential, so there was nothing to strip"
    )
    return child_env


async def test_an_install_spawn_does_not_inherit_a_project_scoped_credential_when_the_cwd_differs(
    scenario: tuple[Path, Path],
    recorded_spawn: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lane's reason for existing, asserted on the real child environment.

    Gateway given ``gateway_root``; cwd is ``elsewhere``; the credential lives
    in ``gateway_root/.env.pmcp``. Before the fix the strip consulted
    ``elsewhere``, found no store, and the install child was spawned holding
    another server's secret.
    """
    gateway_root, elsewhere = scenario
    monkeypatch.chdir(elsewhere)

    tools = _gateway(gateway_root)
    _use_shipped_manifest(monkeypatch)

    result = await tools.provision({"server_name": _INSTALLABLE_SERVER})
    assert result.ok, result.message
    assert result.status == "started"

    child_env = _assert_child_env_is_real(recorded_spawn["env"])
    assert _SECRET_KEY not in child_env, (
        f"the install child inherited {_SECRET_KEY}, a credential PMCP holds "
        f"for another server in {gateway_root / '.env.pmcp'}; the strip resolved "
        f"the project store from the working directory ({elsewhere}) instead of "
        f"from the root the gateway was given"
    )


async def test_the_production_install_path_passes_the_gateways_project_root(
    scenario: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half a helper test cannot see: what ``start_install`` is handed.

    Recorded with ``*args``/``**kwargs`` rather than a typed stub, so the
    assertion is "the gateway's root reached the installer" by whatever calling
    convention, not "it was passed the way this test expected".
    """
    gateway_root, elsewhere = scenario
    monkeypatch.chdir(elsewhere)

    received: dict[str, Any] = {}

    class _RecordingJobManager:
        async def start_install(self, *args: Any, **kwargs: Any) -> str:
            received["args"] = args
            received["kwargs"] = kwargs
            return "recorded-job-id"

    monkeypatch.setattr(
        "pmcp.tools.handlers.get_job_manager", lambda: _RecordingJobManager()
    )

    tools = _gateway(gateway_root)
    _use_shipped_manifest(monkeypatch)

    result = await tools.provision({"server_name": _INSTALLABLE_SERVER})
    assert result.ok, result.message
    assert result.job_id == "recorded-job-id", "start_install was never reached"

    assert tools._project_root == gateway_root
    # Arguments beyond (server_config, platform), in either position or keyword.
    extra = list(received["args"][2:]) + list(received["kwargs"].values())
    assert gateway_root in extra, (
        "the production install path did not hand start_install the gateway's "
        f"project root; it was called with args={received['args']!r} "
        f"kwargs={received['kwargs']!r}"
    )


async def test_a_project_scoped_credential_is_stripped_when_the_cwd_is_the_project_root(
    scenario: tuple[Path, Path],
    recorded_spawn: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The direction that already worked, pinned so the fix cannot trade one for
    the other.

    Cwd equals the gateway's root, so the old cwd walk and the threaded root
    name the same store. This test is green before the fix as well as after —
    which is the point: it is a no-regression pin, not a reproduction.
    """
    gateway_root, _elsewhere = scenario
    monkeypatch.chdir(gateway_root)

    tools = _gateway(gateway_root)
    _use_shipped_manifest(monkeypatch)

    result = await tools.provision({"server_name": _INSTALLABLE_SERVER})
    assert result.ok, result.message

    child_env = _assert_child_env_is_real(recorded_spawn["env"])
    assert _SECRET_KEY not in child_env, (
        "the case that already worked stopped working: with the cwd equal to "
        "the project root the credential must still be stripped"
    )


def test_the_helper_falls_back_to_the_working_directory_walk_when_given_no_root(
    scenario: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the optional default MEANS, so the structural rule has a behavioural
    partner.

    ``project_root`` is optional (a required parameter would have rewritten 55 call
    sites across three merged phases' evidence files), and an omitted argument
    therefore acquires the old cwd-walk behaviour — which is the defect itself.
    That is exactly why omitting it is forbidden at every production call site
    by ``test_no_production_call_site_omits_the_project_root``. This test states
    the cost of the default plainly rather than leaving it implied.
    """
    gateway_root, elsewhere = scenario
    config = _no_credential_config()

    # No root given, cwd IS the project root: the walk happens to find the store.
    monkeypatch.chdir(gateway_root)
    assert _SECRET_KEY not in build_install_child_env(config)

    # No root given, cwd elsewhere: the walk misses the store and the credential
    # survives into the child env. This is the defect, in one line, and it is
    # what an omitted argument still buys a caller.
    monkeypatch.chdir(elsewhere)
    assert build_install_child_env(config).get(_SECRET_KEY) == _SECRET_VALUE

    # Root given explicitly: the working directory stops mattering.
    assert _SECRET_KEY not in build_install_child_env(config, gateway_root)


def _calls_missing_a_project_argument() -> list[str]:
    """Production calls of either helper that pass no project argument.

    Parsed rather than grepped: a docstring mentioning the name, and the ``def``
    itself, are not ``ast.Call`` nodes, so neither needs an exception carved out
    of a regex.
    """
    targets = {"build_install_child_env", "sanitized_subprocess_env"}
    project_kwargs = {"project", "project_root"}
    offenders: list[str] = []
    seen = 0

    for path in sorted(_SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            else:
                continue
            if name not in targets:
                continue
            seen += 1
            has_kwarg = any(kw.arg in project_kwargs for kw in node.keywords)
            if len(node.args) >= 2 or has_kwarg:
                continue
            offenders.append(
                f"{path.relative_to(_REPO_ROOT)}:{node.lineno} {name}(...)"
            )

    # An empty scan is a failure, not a pass: it means the names were renamed
    # and this check silently stopped checking anything.
    assert seen >= 5, f"only {seen} call(s) of {sorted(targets)} found under src/"
    return offenders


def test_no_production_call_site_omits_the_project_root() -> None:
    """The structural half of the fix, as a node id rather than a plan grep.

    ``project_root`` is optional, so nothing in the type system stops a future call
    from omitting it and re-opening this leak. This asserts none does.

    **What it does not cover, stated rather than implied:** it is syntactic over
    *call sites*. An enclosing function that accepts a project root and then
    passes ``None`` onward satisfies it. That is acceptable today only because
    ``start_install`` is the sole enclosing function with a production caller
    and ``test_the_production_install_path_passes_the_gateways_project_root``
    asserts that path behaviourally. The day a second production caller appears,
    the behavioural half is what must be extended.
    """
    offenders = _calls_missing_a_project_argument()
    assert offenders == [], (
        "these production calls resolve the project store by walking up from the "
        "working directory, which re-opens the leak SL-0 closed: "
        + ", ".join(offenders)
    )
