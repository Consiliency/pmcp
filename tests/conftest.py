"""Shared pytest fixtures for MCP Gateway tests.

READ THIS IF A TEST ASSERTS AN npm/npx PACKAGE IDENTITY
=======================================================

`detect_package_type` asks the host npm's own parser (Consiliency/pmcp#195) and
**refuses** rather than guess when anything could redirect npm's resolution. One
of those refusal gates is npm's own local-prefix rule: walking up from the
process's working directory, a `package.json` or a `node_modules` in ANY ancestor
means a project `.npmrc` -- or a `node_modules/.bin` entry, which npm runs
without ever reaching the registry -- could change which package runs.

pytest puts `tmp_path` under the system temp directory, and **that directory is
not ours**. A `/tmp/package.json` or `/tmp/node_modules` (both present on the
development host for #195, the latter holding real executables) silently flips
every `monkeypatch.chdir(tmp_path)` test from resolving to refusing.

Two autouse fixtures below now act on this rather than only describing it
(Consiliency/pmcp#235, #261):

* `isolate_cwd` runs every test from a private directory under the temp root, so
  the repository's own ancestors -- a `node_modules` at a storage-volume root,
  say -- leave the walk entirely. It also closes #261, where the `HOME` redirect
  in `isolate_trust_store` defeated the `$HOME` stop in the project-manifest walk
  and let it reach the developer's real `~/.pmcp/manifest.yaml`.
* `assert_clean_ancestor_chain` fails the session ONCE, naming the path, when the
  temp root's own ancestors carry a `package.json`/`node_modules` -- the one case
  `isolate_cwd` cannot escape, since `tmp_path` lives under the temp directory.

Neither touches the production walk: `_has_local_prefix` replicates npm's real
local-prefix rule, and weakening it to make tests pass would be the actual bug.

The failure mode is nasty in one direction only: a test that asserts a REFUSAL
passes for the wrong reason and looks green forever. So:

* a test asserting a refusal must not rely on cwd unless cwd IS its subject;
* a test asserting a RESOLVED identity from a `chdir`-ed temp directory must pin
  the identity path explicitly (see
  `tests/test_cli.py::TestCheckVersionsUnverifiableDisplay`) or run from the
  repository root, which this suite does control;
* `tests/test_npm_resolver.py` states the local-prefix property as *which*
  directory the walk reports, so it holds under an ambient prefix too.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pmcp import trust_store
from pmcp.manifest import npm_resolver, package_identity, registry, version_checker
from pmcp.transport import http as transport_http
from pmcp.env_store import reset_dotenv_keys, reset_pmcp_introduced_keys
from pmcp.policy.policy import PolicyManager
from pmcp.types import (
    LocalMcpServerConfig,
    ResolvedServerConfig,
    RiskHint,
    ServerStatus,
    ServerStatusEnum,
    ToolInfo,
)


#: The developer's real home, captured at import time -- BEFORE any fixture has
#: redirected ``HOME``. After the redirect ``Path.home()`` reports the fake one,
#: so a check written against a live ``Path.home()`` would compare the fake home
#: with itself and pass however broken the redirect was.
_REAL_HOME = Path.home().resolve()


@pytest.fixture(scope="session", autouse=True)
def assert_clean_ancestor_chain(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Fail ONCE, by name, if the temp root sits under an npm local prefix.

    ``isolate_cwd`` moves every test onto a directory under pytest's temp root,
    which removes the repository's own ancestors -- including a ``node_modules``
    at the root of a storage volume, the case that inverted ~107 npm-identity
    tests in ``/mnt/<volume>/worktrees`` checkouts -- from the walk entirely.
    What it cannot escape is the temp root's OWN ancestors: ``tmp_path`` lives
    under ``tempfile.gettempdir()``, so a ``/tmp/package.json`` is still an
    ancestor of every isolated cwd. Detection is the honest answer there.

    This walks **only** that chain, never ``Path.cwd()``. A session-scoped
    fixture observes the *invocation* directory (the repository), never the
    per-test isolated cwd, so walking cwd here would fail the whole session on
    exactly the hosts ``isolate_cwd`` has already fixed.

    The chain is derived from ``tmp_path_factory.getbasetemp()`` rather than
    assuming ``/tmp``: ``--basetemp`` or ``TMPDIR`` relocates the temp root, and
    the guard must follow it. This repository sets no ``basetemp`` override.

    One named error beats ~107 refusals that each look like a real assertion
    failure -- the state this file used to only document.
    """
    basetemp = tmp_path_factory.getbasetemp().resolve()
    polluted = [
        directory / marker
        for directory in (basetemp, *basetemp.parents)
        for marker in ("package.json", "node_modules")
        if (directory / marker).exists()
    ]
    if polluted:
        listing = "\n  ".join(str(path) for path in polluted)
        # `pytest.exit` rather than `raise`: a session-scoped fixture that raises
        # has its setup failure CACHED and re-reported against every dependent
        # test, so the "one clear error" this guard exists to give becomes the
        # very cascade it is replacing (measured: 100 errors for one planted
        # file). `pytest.exit` ends the session at the first detection.
        pytest.exit(
            "npm local-prefix pollution on the temp-root ancestor chain:\n  "
            f"{listing}\n"
            "npm's own local-prefix rule walks up from the working directory, so "
            "every npm-identity test run from a temp directory will invert from "
            "resolving to REFUSING -- roughly 107 tests failing for a reason that "
            "has nothing to do with the code under test. Remove the path above, or "
            "point pytest's temp root elsewhere with --basetemp/TMPDIR. "
            "(pmcp's production walk is faithful to npm and is deliberately not "
            "changed to paper over this.)",
            returncode=1,
        )


@pytest.fixture(autouse=True)
def isolate_trust_store(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Point the user-scoped trust store at a per-test home. **Autouse.**

    The store lives at ``~/.config/pmcp/trust.json`` (`trust_store.py`), so a
    test that records an approval without this fixture writes a real, permanent
    approval into the developer's own store -- the T-08 hazard the 2026-09-01
    review flagged. Redirecting ``HOME`` is the seam, chosen over monkeypatching
    ``trust_store.trust_store_path``: ``tests/test_trust_store.py`` imports that
    function *by name*, so patching the module attribute would leave its direct
    calls on the real function while ``record``/``is_approved`` (which look the
    name up in module globals) used the patched one, and the two would disagree
    about where the store is. ``HOME`` is also what TRUST's own suite already
    uses, and it keeps the store's checkout-residency check live rather than
    stubbing it out.

    It deliberately **approves nothing**. An autouse approval would make every
    refusal test in the consent lanes vacuously green -- the gate would be
    agreeing with a fixture, not with an operator.

    Tests needing an approval record one explicitly; ``approve_project_file``
    below is the shorthand.

    The fake home is a **sibling** of the test's own ``tmp_path``, never inside
    it. Measured, not stylistic: with the home nested under ``tmp_path``,
    `tests/test_registry.py::test_default_cache_path_is_not_cwd_relative`
    started failing, because it uses ``tmp_path`` as its stand-in for the cwd
    and asserts a ``~``-derived path does not sit under it. A home inside
    ``tmp_path`` makes every home-derived path look cwd-relative to any test
    asking that question.
    """
    fake_home = tmp_path_factory.mktemp("trust-home")
    monkeypatch.setenv("HOME", str(fake_home))

    # Assert the redirect took effect BEFORE any test body runs. A silently
    # ineffective redirect is the one failure mode that must never be quiet:
    # it does not fail a test, it writes the operator's real trust store.
    store = trust_store.trust_store_path()
    if not store.is_relative_to(fake_home.resolve()) or store.is_relative_to(
        _REAL_HOME
    ):
        raise RuntimeError(
            f"Trust store isolation failed: trust_store_path() is {store}, "
            f"expected somewhere under {fake_home}. Refusing to run a test that "
            f"could write the real store under {_REAL_HOME}."
        )

    yield fake_home


@pytest.fixture(autouse=True)
def isolate_cwd(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    isolate_trust_store: Path,
) -> Iterator[Path | None]:
    """Run each test from a private directory under the temp root. **Autouse.**

    Two separate leaks close here, both caused by tests inheriting the
    developer's real working directory:

    * **The project-manifest walk (Consiliency/pmcp#261).**
      ``manifest.loader._find_project_manifest`` walks up from ``Path.cwd()`` and
      stops at ``Path.home()`` -- a guard added by #243 so a startup beneath
      ``$HOME`` does not ask the operator to approve their own config. But
      ``isolate_trust_store`` redirects ``HOME`` to a fake directory, so that stop
      compares against the fake home and never matches the real one. Run from a
      checkout under the real home, the walk sails past it, finds the real
      ``~/.pmcp/manifest.yaml`` and gates it as a *project* overlay -- a second
      consent refusal that broke
      ``test_a_consent_refusal_reaches_the_operator_as_one_warning_naming_its_remedy``.
      The isolation fixture caused that failure; chdir-ing under the temp root
      makes the walk's existing ``temp_root`` stop fire instead.
    * **The npm local-prefix walk.** A repository under a directory that holds
      ``node_modules`` (a storage volume root, say) put that directory on every
      un-chdir'ed test's ancestor chain and inverted npm-identity assertions.

    The directory is a **sibling** of ``tmp_path``, via ``mktemp``, never a child
    such as ``tmp_path / "cwd"``: a child would make every ``package.json`` /
    ``.mcp.json`` / ``pyproject.toml`` a test writes into ``tmp_path`` an
    *ancestor* of the working directory, re-creating the very hazard this closes.
    The fake home is a sibling for the same measured reason.

    ``isolate_trust_store`` is requested explicitly rather than relied on by
    declaration order -- pytest does not guarantee autouse ordering, and the
    fake home must exist before the chdir for the HOME-vs-cwd relationship above
    to hold.

    Opt out with ``@pytest.mark.real_cwd`` **only** where the invocation
    directory is the test's subject. It is not an escape hatch for refusal
    tests: a refusal test that leans on an ambient prefix passes for the wrong
    reason, which is the failure this file has always warned about. Such tests
    should build their own local prefix explicitly.
    """
    if request.node.get_closest_marker("real_cwd") is not None:
        yield None
        return

    isolated = tmp_path_factory.mktemp("cwd")
    monkeypatch.chdir(isolated)
    yield isolated


@pytest.fixture
def approve_project_file() -> Callable[[Path], None]:
    """Approve a project file's *current* bytes, the way an operator would.

    Mirrors `pmcp trust approve`: read what is on disk now, record an approval
    for exactly those bytes. Editing the file afterwards therefore revokes the
    approval, which is the property the consent lanes assert.

    Pass ``home`` when the test drives code under a different HOME than the
    autouse isolation provides; the approval is then recorded in that home.

    ``scope`` is descriptive metadata only -- ``is_approved(path, content)``
    takes no scope argument and never consults it -- so no test should assert
    on it. (`pmcp trust approve` itself records ``"user"``, `cli.py:2489`.)
    """

    def approve(path: Path, home: Path | None = None) -> None:
        if home is None:
            trust_store.record(path, path.read_bytes(), "project", trust_store.APPROVED)
            return
        # Some suites point HOME at a directory of their own (and some then hand
        # that HOME to a subprocess). The store is resolved from HOME at call
        # time, so an approval recorded under the autouse isolated home would be
        # invisible to code running under theirs. Record it where they will look.
        previous = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            trust_store.record(path, path.read_bytes(), "project", trust_store.APPROVED)
        finally:
            if previous is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous

    return approve


@pytest.fixture(autouse=True)
def _reset_dotenv_provenance() -> Iterator[None]:
    """Keep the dotenv provenance registry from leaking between tests.

    ``env_store``'s registry of keys PMCP loaded from a dotenv file
    (Consiliency/pmcp#229) is process-global, and production paths that tests
    exercise write to it: any test that drives ``_check_api_key_available``
    against a real ``.env`` records that file's keys. Without this reset, those
    keys would be stripped from every LATER test's
    ``sanitized_subprocess_env()`` -- an order-dependent failure in tests that
    have nothing to do with #229.
    """
    reset_dotenv_keys()
    yield
    reset_dotenv_keys()


@pytest.fixture(autouse=True)
def _no_live_npm_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Fail any test whose package-identity lookup reaches the real npm registry.

    ``gateway.register_discovered_server`` resolves the package against the
    registry before it registers anything (Consiliency/pmcp#230). A test that
    forgets to stub the lookup would otherwise pass or fail on whatever
    registry.npmjs.org answered that day, and ``resolve_package_identity`` turns
    a network error into a quiet ``None`` -- so a refusal assertion could pass
    for the wrong reason offline. Only the opener's ``open`` is replaced, so
    tests that inspect ``_OPENER``'s handlers still see the real ones; a test
    that stubs ``_fetch_packument`` (see ``fake_npm_registry``) never gets here.
    """
    attempts: list[str] = []

    def _refuse(request: Any, *args: Any, **kwargs: Any) -> Any:
        attempts.append(getattr(request, "full_url", str(request)))
        raise OSError("live npm registry lookups are disabled in tests")

    monkeypatch.setattr(package_identity._OPENER, "open", _refuse)
    yield attempts
    assert attempts == [], (
        f"a test reached the npm registry: {attempts}; "
        "use the fake_npm_registry fixture"
    )


@pytest.fixture(autouse=True)
def _reset_process_global_state() -> Iterator[None]:
    """Reset process-global module state around every test. **Autouse.**

    Each of these is a module-level mutable that outlives a test and makes a
    later test's result depend on which tests ran before it -- the class the
    2026-09-01 review logged as T-07. One line each, naming the consumer that
    turns the leak into an ordering hazard:

    * ``env_store._PMCP_INTRODUCED_KEYS`` -- provenance for
      ``env_key_is_operator_supplied()``, the gate deciding whether a
      ``PMCP_CONFIG`` / ``PMCP_POLICY`` / ``PMCP_MANIFEST_PATH`` override was
      exported by the operator or planted by a checkout. A stale key makes a
      later test's own exported redirect look PMCP-introduced and be ignored.
    * ``version_checker._version_cache`` -- resolved npm versions; a cached
      answer is returned for a lookup a later test believes it performed.
    * ``registry._IN_PROCESS_CACHE`` / ``_IN_PROCESS_TASKS`` -- a 300 s registry
      payload cache plus live tasks on loops pytest-asyncio has closed.
    * ``transport.http._rl_store`` / ``_rl_lock`` -- per-IP rate-limit buckets;
      every TestClient request shares the ``testclient`` IP, so a filled bucket
      429s the next test's first request.
    * ``transport.http._metrics`` -- request counters a later assertion reads as
      its own delta.
    * ``npm_resolver._resolver`` -- a process-wide singleton holding a spawned
      node child, with sticky-failure and warned flags that must not be
      inherited.

    Composition lives here rather than in a ``src/`` aggregator by rule, not by
    taste: ``tests/test_feedback_provenance.py:226`` AST-walks ``src/pmcp`` and
    asserts no production caller of ``reset_pmcp_introduced_keys`` exists,
    because one would make the gate's provenance evidence erasable. A complete
    reset can therefore only be assembled in ``tests/``.

    The resolver goes last: it is the only helper that touches a process and the
    only one that can block (``proc.wait(timeout=2.0)``). Dropping it re-spawns
    the node child for the next test that resolves an npm identity -- about 43 ms
    -- which is the price of never inheriting another test's resolver state.
    """

    def _reset() -> None:
        reset_pmcp_introduced_keys()
        version_checker.clear_version_cache()
        registry.clear_in_process_cache()
        transport_http.reset_rate_limit_state()
        transport_http.reset_request_metrics()
        npm_resolver.reset_resolver_for_tests()

    _reset()
    yield
    _reset()


@pytest.fixture
def fake_npm_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """An offline npm registry: set ``fake_npm_registry[name] = version``.

    Names not in the mapping resolve to nothing, as an unknown package does.
    """
    known: dict[str, str] = {}

    def _fetch(name: str) -> dict[str, Any] | None:
        version = known.get(name)
        if version is None:
            return None
        return {
            "name": name,
            "dist-tags": {"latest": version},
            "versions": {version: {"dist": {"integrity": f"sha512-{name}"}}},
        }

    monkeypatch.setattr(package_identity, "_fetch_packument", _fetch)
    return known


# === Sample Data Factories ===


def create_tool_info(
    server_name: str = "test-server",
    tool_name: str = "test_tool",
    description: str = "A test tool",
    risk_hint: RiskHint = RiskHint.LOW,
    tags: list[str] | None = None,
    input_schema: dict[str, Any] | None = None,
) -> ToolInfo:
    """Factory for creating ToolInfo objects."""
    return ToolInfo(
        tool_id=f"{server_name}::{tool_name}",
        server_name=server_name,
        tool_name=tool_name,
        description=description,
        short_description=description[:100] if len(description) > 100 else description,
        input_schema=input_schema or {"type": "object", "properties": {}},
        tags=tags or [server_name],
        risk_hint=risk_hint,
    )


def create_server_status(
    name: str = "test-server",
    status: ServerStatusEnum = ServerStatusEnum.ONLINE,
    tool_count: int = 5,
    last_error: str | None = None,
) -> ServerStatus:
    """Factory for creating ServerStatus objects."""
    return ServerStatus(
        name=name,
        status=status,
        tool_count=tool_count,
        last_error=last_error,
        last_connected_at=1234567890.0 if status == ServerStatusEnum.ONLINE else None,
    )


def create_server_config(
    name: str = "test-server",
    command: str = "echo",
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> ResolvedServerConfig:
    """Factory for creating ResolvedServerConfig objects."""
    return ResolvedServerConfig(
        name=name,
        source="project",
        config=LocalMcpServerConfig(
            command=command,
            args=args or [],
            env=env,
        ),
    )


# === Sample Data Fixtures ===


@pytest.fixture
def sample_tools() -> list[ToolInfo]:
    """Sample list of tools for testing."""
    return [
        create_tool_info(
            server_name="github",
            tool_name="create_issue",
            description="Create a new issue in a GitHub repository",
            risk_hint=RiskHint.HIGH,
            tags=["github", "git", "issue"],
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Issue title"},
                    "body": {"type": "string", "description": "Issue body"},
                },
                "required": ["title"],
            },
        ),
        create_tool_info(
            server_name="github",
            tool_name="list_issues",
            description="List issues in a repository",
            risk_hint=RiskHint.LOW,
            tags=["github", "git", "search"],
        ),
        create_tool_info(
            server_name="jira",
            tool_name="search_issues",
            description="Search for Jira issues using JQL",
            risk_hint=RiskHint.LOW,
            tags=["jira", "search"],
        ),
        create_tool_info(
            server_name="filesystem",
            tool_name="delete_file",
            description="Delete a file from the filesystem",
            risk_hint=RiskHint.HIGH,
            tags=["fs", "file", "delete"],
        ),
        create_tool_info(
            server_name="filesystem",
            tool_name="read_file",
            description="Read contents of a file",
            risk_hint=RiskHint.LOW,
            tags=["fs", "file", "read"],
        ),
    ]


@pytest.fixture
def sample_server_statuses() -> list[ServerStatus]:
    """Sample list of server statuses for testing."""
    return [
        create_server_status("github", ServerStatusEnum.ONLINE, 10),
        create_server_status("jira", ServerStatusEnum.ONLINE, 5),
        create_server_status(
            "filesystem", ServerStatusEnum.OFFLINE, 0, "Connection refused"
        ),
    ]


@pytest.fixture
def sample_server_configs() -> list[ResolvedServerConfig]:
    """Sample list of server configs for testing."""
    return [
        create_server_config(
            "github",
            "npx",
            ["-y", "@modelcontextprotocol/server-github"],
            {"GITHUB_TOKEN": "test-token"},
        ),
        create_server_config(
            "filesystem",
            "npx",
            ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        ),
    ]


# === Mock Client Manager ===


class MockClientManager:
    """Mock client manager for testing gateway tools."""

    def __init__(
        self,
        tools: list[ToolInfo] | None = None,
        server_statuses: list[ServerStatus] | None = None,
    ) -> None:
        self._tools = {t.tool_id: t for t in (tools or [])}
        self._server_statuses = {s.name: s for s in (server_statuses or [])}
        self._online_servers: set[str] = {
            s.name
            for s in (server_statuses or [])
            if s.status == ServerStatusEnum.ONLINE
        }
        self._revision_id = "test-rev-001"
        self._last_refresh_ts = 1234567890.0
        self._call_tool_response: Any = {
            "content": [{"type": "text", "text": "result"}]
        }
        self._call_tool_error: Exception | None = None

    def get_all_tools(self) -> list[ToolInfo]:
        return list(self._tools.values())

    def get_tool(self, tool_id: str) -> ToolInfo | None:
        return self._tools.get(tool_id)

    def is_server_online(self, name: str) -> bool:
        return name in self._online_servers

    def is_lazy_server(self, name: str) -> bool:
        """Mock: no servers are lazy by default."""
        return False

    def set_server_online(self, name: str, online: bool = True) -> None:
        if online:
            self._online_servers.add(name)
        else:
            self._online_servers.discard(name)

    def get_server_status(self, name: str) -> ServerStatus | None:
        return self._server_statuses.get(name)

    def get_all_server_statuses(self) -> list[ServerStatus]:
        return list(self._server_statuses.values())

    def get_registry_meta(self) -> tuple[str, float]:
        return (self._revision_id, self._last_refresh_ts)

    async def call_tool(
        self, tool_id: str, args: dict[str, Any], timeout_ms: int
    ) -> Any:
        if self._call_tool_error:
            raise self._call_tool_error
        return self._call_tool_response

    async def refresh(self, configs: list[Any]) -> list[str]:
        return []

    def set_call_tool_response(self, response: Any) -> None:
        """Set the response for call_tool."""
        self._call_tool_response = response
        self._call_tool_error = None

    def set_call_tool_error(self, error: Exception) -> None:
        """Set an error to raise from call_tool."""
        self._call_tool_error = error


@pytest.fixture
def mock_client_manager(
    sample_tools: list[ToolInfo],
    sample_server_statuses: list[ServerStatus],
) -> MockClientManager:
    """Create a mock client manager with sample data."""
    return MockClientManager(
        tools=sample_tools,
        server_statuses=sample_server_statuses,
    )


# === Mock Policy Manager ===


@pytest.fixture
def mock_policy_manager() -> PolicyManager:
    """Create a permissive policy manager for testing."""
    return PolicyManager()


@pytest.fixture
def strict_policy_manager() -> PolicyManager:
    """Create a strict policy manager that blocks high-risk tools."""
    manager = PolicyManager()
    manager._tool_denylist = ["*::delete_*", "*::execute_*"]
    return manager


# === Temporary Directory Fixtures ===


@pytest.fixture
def temp_dir() -> Path:
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_config_dir(temp_dir: Path) -> Path:
    """Create a temporary directory with sample config files."""
    config_dir = temp_dir / ".claude"
    config_dir.mkdir(parents=True)

    # Create sample mcp.json
    mcp_json = config_dir / "mcp.json"
    mcp_json.write_text(
        """{
  "mcpServers": {
    "test-server": {
      "command": "echo",
      "args": ["hello"]
    }
  }
}"""
    )

    # Create sample policy file
    policy_yaml = config_dir / "gateway-policy.yaml"
    policy_yaml.write_text(
        """servers:
  allowlist:
    - "*"
  denylist: []

tools:
  allowlist:
    - "*"
  denylist:
    - "*::delete_*"

output:
  max_size_bytes: 50000
  max_tokens: 10000

redaction:
  enabled: true
  patterns:
    - "sk-[a-zA-Z0-9]{48}"
"""
    )

    return config_dir


# === Async Mock Helpers ===


@pytest.fixture
def mock_aiohttp_session() -> MagicMock:
    """Create a mock aiohttp ClientSession."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


def create_mock_response(
    status: int = 200,
    json_data: dict[str, Any] | None = None,
    text_data: str = "",
) -> MagicMock:
    """Create a mock aiohttp response."""
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=json_data or {})
    response.text = AsyncMock(return_value=text_data)
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=None)
    return response


# === Process Mock Helpers ===


def create_mock_process(
    returncode: int | None = None,
    stdout_data: bytes = b"",
    stderr_data: bytes = b"",
) -> MagicMock:
    """Create a mock asyncio subprocess."""
    process = MagicMock()
    process.returncode = returncode
    process.pid = 12345
    process.stdout = MagicMock()
    process.stdout.readline = AsyncMock(return_value=stdout_data)
    process.stdout.read = AsyncMock(return_value=stdout_data)
    process.stderr = MagicMock()
    process.stderr.read = AsyncMock(return_value=stderr_data)
    process.stdin = MagicMock()
    process.stdin.write = MagicMock()
    process.stdin.drain = AsyncMock()
    process.wait = AsyncMock(return_value=returncode)
    process.terminate = MagicMock()
    process.kill = MagicMock()
    return process
