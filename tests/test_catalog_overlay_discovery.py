"""An overlay never hides another server from discovery (Consiliency/pmcp#342).

Two rules, both measured on main ``c9206a9`` and on ``a622ec5`` (before the
manifest cache), through a real ``pmcp --transport http`` gateway:

* **R1, weights from the base.** Discovery weighs a keyword by how many servers
  declare it (``max(1/N, 0.5)``) and needs ``weight / 3 >= 0.2``. A keyword that
  one server declares weighs 1.0 and scores 0.333. Main counted over the MERGED
  manifest, so an approved project overlay whose server shared a keyword halved
  its weight for every other server: 0.5 / 3 = 0.167, below the threshold. A
  query on that keyword then returned no manifest candidate at all, not the
  user overlay's server, not the shipped one, not even the project's own. The
  weights now come from the base manifest alone, before any overlay.
* **R2, one entry never takes down the others.** No overlay entry was
  type-checked, and consumers iterate every entry. ``keywords: null`` made every
  ``catalog_search`` raise; a non-string ``transport`` made any query that
  ranked the entry raise; a bad CLI alternative made every query raise
  (``rank_cli_hints``); ``args``/``command``/``headers``/a metadata URL of the
  wrong type aborted startup and ``gateway.refresh`` for every server. Since
  revision 2, an overlay entry is built into every typed model its consumers
  build, and is skipped at parse time if any rejects it, with a warning that
  carries no value and no overlay name. A YAML ``null`` means "absent" for every
  field. Each consumer also guards each entry, as a second line.

Everything runs through the real loader, the real consent gate, and the real
``pmcp trust approve|revoke`` CLI entry path; only the client manager (no
servers running) is a stub.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import dataclasses
import re
import sys
import itertools
import json
import os
import warnings
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pydantic
import pytest
import yaml

from pmcp.cli import async_main, parse_args
from pmcp.cli_commands.secrets import _extract_required_keys
from pmcp.config.loader import load_configs, resolve_startup_configs
from pmcp.identity import filter_self_references
from pmcp.manifest import loader
from pmcp.manifest.environment import probe_clis
from pmcp.manifest.loader import (
    _SHIPPED_MANIFEST_PATH,
    CLIAlternative,
    Manifest,
    ServerConfig,
    load_manifest,
)
from pmcp.manifest.matcher import (
    _keyword_match,
    _manifest_keyword_weights,
    rank_cli_hints,
)
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from tests.test_tools import MockClientManager as RefreshClientManager

USER_OVERLAY = """
servers:
  useronly:
    description: User overlay demo server
    keywords: [widget, demo]
    command: npx
    args: ["-y", "useronly-mcp@1.0.0"]
"""

# Shares `demo` with the user overlay and `screenshot` with shipped playwright,
# where each is otherwise declared by exactly one server.
PROJECT_OVERLAY = """
servers:
  projonly:
    description: Project overlay demo server
    keywords: [gadget, demo, screenshot]
    command: npx
    args: ["-y", "projonly-mcp@1.0.0"]
"""


@pytest.fixture(autouse=True)
def _no_env_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)


# Each fresh GatewayTools probes every shipped CLI once (`gcloud --version`,
# `az --version`, ... take up to a second each), and these tests build many.
# A CLI's probe result depends only on its name, its exact check_command and
# PATH, so the real `check_cli` runs once per distinct (name, command, PATH)
# for the module, and the result is reused. `probe_clis` itself, its guard and
# its log lines run for real every time; an exception is never cached.
_CHECK_CLI_RESULTS: dict[tuple[Any, ...], Any] = {}


@pytest.fixture(autouse=True)
def _probe_each_cli_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from pmcp.manifest import environment

    real = environment.check_cli

    async def check_cli(name: str, check_command: list[str]) -> Any:
        key = (name, tuple(check_command), os.environ.get("PATH", ""))
        if key not in _CHECK_CLI_RESULTS:
            _CHECK_CLI_RESULTS[key] = await real(name, check_command)
        return _CHECK_CLI_RESULTS[key]

    monkeypatch.setattr(environment, "check_cli", check_cli)


@pytest.fixture(autouse=True)
def _no_live_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here reads the MCP Registry, but `catalog_search` and the
    registry tier fetch it live when no cache exists, and every test starts
    with an empty HOME: one network fetch (up to 5 pages, 5 s timeout) per
    search. An empty registry keeps these tests hermetic and fast; the
    registry tier itself is driven with stubbed candidates where it matters
    (codex's round-6 F001 and the per-tier differential)."""
    from pmcp.manifest.registry import RegistryCache

    async def no_registry(endpoint: str, **_: Any) -> RegistryCache:
        return RegistryCache(
            schema_version="test", source_endpoint=endpoint, fetched_at="never"
        )

    monkeypatch.setattr("pmcp.tools.handlers.fetch_registry_servers", no_registry)


@contextlib.contextmanager
def _one_manifest_parse() -> Any:
    """Share one ``load_manifest()`` result across the consumers called inside.

    ``load_manifest()`` returns a fresh copy of the cached manifest on every
    call (a pickle round trip, about 2 ms), and `request_capability`'s
    category tier calls it once per candidate server, so the differentials
    spent most of their time copying. Inside this block the overlay files do
    not change, so every no-argument call returns one parse; an explicit
    path still loads for real. The parse is fingerprinted on entry and
    checked on exit, so a consumer that mutated the shared manifest fails
    the test instead of being hidden by the sharing.
    """
    import pickle
    from unittest.mock import patch as _patch

    real = loader.load_manifest
    shared: dict[Any, Any] = {}
    fingerprints: dict[Any, bytes] = {}

    def load(*args: Any, **kwargs: Any) -> Any:
        # The tools bind their project root (Consiliency/pmcp#372) and ask
        # for that root's manifest: shared per root, like the no-argument load.
        if args or set(kwargs) - {"project_root"}:
            return real(*args, **kwargs)
        root = kwargs.get("project_root")
        if root not in shared:
            shared[root] = real(**kwargs)
            fingerprints[root] = pickle.dumps(shared[root])
        return shared[root]

    with (
        _patch.object(loader, "load_manifest", load),
        _patch("pmcp.tools.handlers.load_manifest", load),
        _patch("pmcp.cli_commands.secrets.load_manifest", load),
    ):
        yield
    for root, manifest in shared.items():
        assert pickle.dumps(manifest) == fingerprints[root], (
            "a consumer mutated the manifest load_manifest() returned"
        )


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _run_cli(*argv: str) -> None:
    """One `pmcp` invocation, dispatched exactly as `main()` would."""
    with patch("sys.argv", ["pmcp", *argv]):
        args = parse_args()
    asyncio.run(async_main(args))


def _gateway() -> GatewayTools:
    """GatewayTools with the real PolicyManager and no running servers."""
    client_manager = MagicMock()
    client_manager.get_all_tools.return_value = []
    client_manager.is_server_online.return_value = False
    client_manager.get_all_server_statuses.return_value = []
    return GatewayTools(client_manager=client_manager, policy_manager=PolicyManager())


def _candidates(tools: GatewayTools, query: str) -> list[str]:
    result = asyncio.run(
        tools.catalog_search({"query": query, "include_offline": True})
    )
    return sorted(c.name for c in result.manifest_candidates)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A user overlay, and a project overlay the gateway's cwd discovers."""
    _write(Path.home() / ".pmcp" / "manifest.yaml", USER_OVERLAY)
    project_dir = tmp_path / "proj"
    overlay = _write(project_dir / ".pmcp" / "manifest.yaml", PROJECT_OVERLAY)
    monkeypatch.chdir(project_dir)
    return overlay


# --- R1: the end-to-end the issue asks for -----------------------------------


def test_approve_then_revoke_through_the_cli_keeps_every_source_discoverable(
    project: Path,
) -> None:
    """approve -> shipped + user + project candidates; revoke -> shipped + user."""
    tools = _gateway()

    # Unapproved: the gate keeps the project overlay out entirely.
    assert _candidates(tools, "demo") == ["useronly"]
    assert _candidates(tools, "screenshot") == ["playwright"]
    assert _candidates(tools, "gadget") == []

    _run_cli("trust", "approve", str(project))
    manifest = load_manifest()
    assert {"useronly", "projonly", "playwright"} <= set(manifest.servers)
    # main: [] and [] -- the shared keyword sank every server below 0.2.
    assert _candidates(tools, "demo") == ["projonly", "useronly"]
    assert _candidates(tools, "screenshot") == ["playwright", "projonly"]
    assert _candidates(tools, "widget") == ["useronly"]
    assert _candidates(tools, "gadget") == ["projonly"]

    _run_cli("trust", "revoke", str(project))
    assert "projonly" not in load_manifest().servers
    assert _candidates(tools, "demo") == ["useronly"]
    assert _candidates(tools, "screenshot") == ["playwright"]
    assert _candidates(tools, "gadget") == []


def test_no_query_returns_no_manifest_candidates_whatever_the_consent(
    project: Path,
) -> None:
    """Without a query there is nothing to match (#78's design), approved or not."""
    tools = _gateway()

    def no_query() -> list[str]:
        result = asyncio.run(tools.catalog_search({"include_offline": True}))
        return [c.name for c in result.manifest_candidates]

    assert no_query() == []
    _run_cli("trust", "approve", str(project))
    assert no_query() == []


# --- R1: the class -- every source, every shipped keyword ----------------------


def _base_weights() -> dict[str, float]:
    # An explicit path applies no overlay, so its weights are the shipped ones.
    return _manifest_keyword_weights(load_manifest(_SHIPPED_MANIFEST_PATH))


@pytest.mark.parametrize("source", ["user", "project", "env"])
def test_no_overlay_source_changes_a_keyword_weight(
    source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Any,
) -> None:
    """Every overlay source, with a server declaring EVERY shipped keyword."""
    base = _base_weights()
    copycat = yaml.safe_dump(
        {
            "servers": {
                "copycat": {
                    "description": "declares every shipped keyword",
                    "keywords": sorted(base),
                    "command": "npx",
                }
            }
        }
    )
    if source == "user":
        _write(Path.home() / ".pmcp" / "manifest.yaml", copycat)
    elif source == "project":
        overlay = _write(tmp_path / "proj" / ".pmcp" / "manifest.yaml", copycat)
        approve_project_file(overlay)
        monkeypatch.chdir(tmp_path / "proj")
    else:
        env_path = _write(tmp_path / "env.yaml", copycat)
        monkeypatch.setenv("PMCP_MANIFEST_PATH", str(env_path))

    first = load_manifest()
    second = load_manifest()  # a cache hit: the weights survive the round trip
    assert "copycat" in first.servers
    assert _manifest_keyword_weights(first) == base
    assert _manifest_keyword_weights(second) == base


def test_a_copycat_overlay_hides_no_shipped_server_on_any_shipped_keyword(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approve_project_file: Any
) -> None:
    """Derived from the shipped vocabulary, not from the issue's two examples.

    For every keyword the shipped manifest declares, the servers that query
    surfaces before an approved overlay declaring ALL of them must still
    surface after it. `limit` is lifted so top-N displacement, which is
    intended ranking, cannot mask a dropped candidate.
    """
    tools = _gateway()
    shipped_keywords = sorted(_base_weights())

    def surfaced(query: str) -> set[str]:
        return {
            c.name
            for c in tools._manifest_candidates_for_query(
                query,
                manifest=load_manifest(),
                configured_servers={},
                exclude_servers=set(),
                limit=1000,
            )
        }

    before = {q: surfaced(q) for q in shipped_keywords}
    overlay = _write(
        tmp_path / "proj" / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "copycat": {
                        "description": "declares every shipped keyword",
                        "keywords": shipped_keywords,
                        "command": "npx",
                    }
                }
            }
        ),
    )
    approve_project_file(overlay)
    monkeypatch.chdir(tmp_path / "proj")
    assert "copycat" in load_manifest().servers

    lost = {q: before[q] - surfaced(q) for q in shipped_keywords}
    assert {q: s for q, s in lost.items() if s} == {}
    # And the queries that surfaced anything are not a vacuous handful.
    assert sum(1 for q in shipped_keywords if before[q]) > 100


def test_match_capability_keeps_its_match_when_an_overlay_shares_the_keyword(
    project: Path,
) -> None:
    """The exported single-best matcher uses the same weights."""
    assert _keyword_match("demo", load_manifest(), set()).entry_name == "useronly"
    assert _keyword_match("screenshot", load_manifest(), set()).entry_name == (
        "playwright"
    )
    _run_cli("trust", "approve", str(project))
    demo = _keyword_match("demo", load_manifest(), set())
    shot = _keyword_match("screenshot", load_manifest(), set())
    assert demo.matched and demo.entry_name in {"useronly", "projonly"}
    assert shot.matched and shot.entry_name in {"playwright", "projonly"}


def test_a_hand_built_manifest_is_still_weighted_by_its_own_servers() -> None:
    """No base: corpus weights, so generic keywords still sink (unchanged)."""
    servers = {
        f"s{i}": ServerConfig(
            name=f"s{i}",
            description="",
            keywords=["api"],
            install={},
            command="npx",
            args=[],
        )
        for i in range(4)
    }
    manifest = Manifest("1.0", {}, servers, ".mcp-gateway/discovery_queue.json")
    assert getattr(manifest, "base_keyword_weights", None) is None
    assert _manifest_keyword_weights(manifest) == {"api": 0.5}
    assert _keyword_match("api", manifest, set()).matched is False


def test_the_config_server_view_keeps_the_base_weights(project: Path) -> None:
    """request_capability's merged view must not fall back to corpus weights."""
    manifest = load_manifest()
    view = _gateway()._build_manifest_with_config_servers(manifest, {})
    assert view.base_keyword_weights is not None
    assert view.base_keyword_weights == manifest.base_keyword_weights
    assert view.base_category_keywords is not None
    assert view.base_category_keywords == manifest.base_category_keywords
    assert view.overlay_cli_names == manifest.overlay_cli_names


def test_an_explicit_path_load_weights_that_file_alone(tmp_path: Path) -> None:
    """An explicit path applies no overlay, and its weights are its own."""
    _write(Path.home() / ".pmcp" / "manifest.yaml", USER_OVERLAY)
    path = _write(
        tmp_path / "only.yaml",
        "servers:\n  a: {keywords: [x, y], command: npx}\n"
        "  b: {keywords: [x], command: npx}\n",
    )
    manifest = load_manifest(path)
    assert set(manifest.servers) == {"a", "b"}
    assert _manifest_keyword_weights(manifest) == {"x": 0.5, "y": 1.0}


# --- R2: an entry a consumer would reject is skipped at parse time ---------------
#
# Revision 2 (board round 1 on Consiliency/pmcp#343): the rule is no longer a
# list of fields. An overlay entry is built into every typed model its
# consumers build -- ServerConfig / CLIAlternative's own annotations, the
# CapabilityCandidate catalog_search builds, the Local/RemoteMcpServerConfig
# startup and refresh build, the CLIHint rank_cli_hints builds -- and is
# skipped if any of them rejects it. These tests walk EVERY field of the two
# entry models with every bad shape and drive every consumer.

SECRET_NAME = "tok-NAME-sentinel-5f1c9a"
SECRET = "sk-live-VALUE-sentinel-0123456789"
HUGE = "H" * 100_000 + SECRET
# null, a wrong scalar (int, bool, str), a list, a dict, huge values. Each
# container carries the value sentinel so a log line that echoes it is caught.
SHAPES: list[tuple[str, Any]] = [
    ("null", None),
    ("int", 5),
    ("bool", True),
    ("str", "zz-" + SECRET),
    ("list", [SECRET, 1]),
    ("dict", {SECRET: [1]}),
    ("huge-str", HUGE),
    ("huge-int", 10**40),
]
SERVER_FIELDS = [f.name for f in dataclasses.fields(ServerConfig) if f.name != "name"]
CLI_FIELDS = [f.name for f in dataclasses.fields(CLIAlternative) if f.name != "name"]
GOOD_SERVER = {"keywords": ["zzgood"], "command": "npx", "args": ["-y", "good@1.0.0"]}
GOOD_REMOTE = {
    "keywords": ["zzremote"],
    "url": "https://example.invalid/remote",
    "oidc_issuer_url": "https://issuer.invalid",
}
# ~/.mcp.json for the walk: a partial (command-less) entry named like the bad
# overlay entry makes load_configs inherit its defaults (codex F051), next to a
# healthy sibling that must always load.
PARTIAL_CONFIGS = {
    "mcpServers": {
        "puppeteer": {"args": []},
        "healthy": {"command": "healthy-command"},
    }
}


def _refresh_tools(monkeypatch: pytest.MonkeyPatch) -> GatewayTools:
    """A GatewayTools whose refresh runs on the REAL load_manifest and the
    REAL load_configs (rev 4: stubbing load_configs hid codex F051)."""
    tools = GatewayTools(
        client_manager=RefreshClientManager(),  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
    )
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_enabled_auto_start", lambda **_: set()
    )
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_disabled_auto_start", lambda **_: set()
    )
    monkeypatch.setattr(tools, "_load_provisioned_registry", lambda: {})
    return tools


def _drive_every_consumer(tools: GatewayTools) -> None:
    """Every consumer of the merged manifest; none may raise."""
    manifest = load_manifest()
    assert "good" in manifest.servers
    assert "goodcli" in manifest.cli_alternatives
    assert "git" in manifest.cli_alternatives

    for include_offline in (True, False):
        for query in ("zzbad screenshot", "zzgood", "git commits", "playwright"):
            asyncio.run(
                tools.catalog_search(
                    {"query": query, "include_offline": include_offline}
                )
            )
    found = asyncio.run(
        tools.catalog_search({"query": "zzbad screenshot", "include_offline": True})
    )
    assert "playwright" in {c.name for c in found.manifest_candidates}
    good = asyncio.run(
        tools.catalog_search({"query": "zzgood", "include_offline": True})
    )
    assert [c.name for c in good.manifest_candidates] == ["good"]

    for query in ("screenshot", "zzgood", "git commits", "database sql"):
        asyncio.run(tools.request_capability({"query": query}))

    resolution = resolve_startup_configs([], manifest_servers=manifest.servers)
    names = {c.name for c in resolution.lazy_configs + resolution.eager_configs}
    assert {"good", "playwright"} <= names

    refreshed = asyncio.run(tools.refresh({"reason": "test"}))
    assert refreshed.ok is True

    # Configured-default inheritance (rev 4): ~/.mcp.json holds partial
    # entries named like the overlay entries, plus a healthy sibling.
    configured = {c.name for c in load_configs()}
    assert "healthy" in configured
    # pmcp secrets: one entry must not drop a later server's metadata.
    _, _, auth_metadata, _ = _extract_required_keys(Path.cwd())
    assert "zz-good-remote" in auth_metadata


def _assert_nothing_leaked(caplog: pytest.LogCaptureFixture) -> None:
    for record in caplog.records:
        message = record.getMessage()
        assert SECRET not in message, message[:200]
        assert SECRET_NAME not in message, message[:200]
        # The parse-time check must catch every entry a consumer would fail
        # on, so no consumer guard (the second line) ever fires for an overlay
        # entry. A guard firing here means the check missed a consumer.
        assert "skipping an unusable" not in message, message[:200]
        assert "skipping unusable keywords" not in message, message[:200]


@pytest.mark.parametrize("field_name", SERVER_FIELDS)
def test_every_server_field_with_every_bad_shape_is_contained(
    field_name: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tools = _refresh_tools(monkeypatch)
    _write(Path.home() / ".mcp.json", json.dumps(PARTIAL_CONFIGS))
    # Every shape, both remote and local (rev 4: rev 3 stripped `url` for some
    # shapes, so a remote entry's `args` was never walked).
    for (_, value), remote in itertools.product(SHAPES, (True, False)):
        bad = {"keywords": ["zzbad", "screenshot"], "command": "npx"}
        if remote:
            bad["url"] = "https://example.invalid/mcp"
        bad[field_name] = value
        overlay = {
            "servers": {
                # aaa- sorts first, so `pmcp secrets` meets it before the good
                # remote sibling (claude F1).
                "aaa-" + SECRET_NAME: bad,
                SECRET_NAME: bad,
                "puppeteer": bad,
                "good": GOOD_SERVER,
                "zz-good-remote": GOOD_REMOTE,
            },
            "cli_alternatives": {"goodcli": {"keywords": ["zzcli"]}},
        }
        _write(Path.home() / ".pmcp" / "manifest.yaml", yaml.safe_dump(overlay))
        with (
            caplog.at_level(logging.DEBUG),
            warnings.catch_warnings(record=True) as seen,
        ):
            warnings.simplefilter("always")
            with _one_manifest_parse():
                _drive_every_consumer(tools)
        _assert_nothing_leaked(caplog)
        # rev 4: a pydantic serializer warning quoted the value (input_value=)
        assert not [
            w
            for w in seen
            if SECRET in str(w.message) or "input_value" in str(w.message)
        ]
        caplog.clear()


@pytest.mark.parametrize("field_name", CLI_FIELDS)
def test_every_cli_field_with_every_bad_shape_is_contained(
    field_name: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`git` is overridden too: it is on PATH, so rank_cli_hints scores it."""
    tools = _refresh_tools(monkeypatch)
    _write(Path.home() / ".mcp.json", json.dumps(PARTIAL_CONFIGS))
    for _, value in SHAPES:
        bad: dict[str, Any] = {"keywords": ["git", "commits"]}
        bad[field_name] = value
        overlay = {
            "servers": {"good": GOOD_SERVER, "zz-good-remote": GOOD_REMOTE},
            "cli_alternatives": {SECRET_NAME: bad, "git": bad, "goodcli": {}},
        }
        _write(Path.home() / ".pmcp" / "manifest.yaml", yaml.safe_dump(overlay))
        with (
            caplog.at_level(logging.DEBUG),
            warnings.catch_warnings(record=True) as seen,
        ):
            warnings.simplefilter("always")
            with _one_manifest_parse():
                _drive_every_consumer(tools)
        _assert_nothing_leaked(caplog)
        # rev 4: a pydantic serializer warning quoted the value (input_value=)
        assert not [
            w
            for w in seen
            if SECRET in str(w.message) or "input_value" in str(w.message)
        ]
        caplog.clear()


@pytest.mark.parametrize(
    ("key", "label"), [(5, "int"), (True, "bool"), (None, "null"), ("", "empty")]
)
def test_a_non_string_server_or_cli_key_is_contained(
    key: Any, label: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = _refresh_tools(monkeypatch)
    _write(Path.home() / ".mcp.json", json.dumps(PARTIAL_CONFIGS))
    overlay = {
        "servers": {
            key: {"keywords": ["screenshot"]},
            "good": GOOD_SERVER,
            "zz-good-remote": GOOD_REMOTE,
        },
        "cli_alternatives": {key: {"keywords": ["git"]}, "goodcli": {}},
    }
    _write(Path.home() / ".pmcp" / "manifest.yaml", yaml.safe_dump(overlay))
    _drive_every_consumer(tools)
    assert all(isinstance(n, str) for n in load_manifest().servers)


def test_the_board_repros_are_skipped_with_a_value_free_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 1's concrete shapes, named, so a regression reads plainly."""
    rows = {
        "transport-yaml-on": {"keywords": ["screenshot"], "transport": True},
        "transport-int": {"keywords": ["screenshot"], "transport": 5},
        "transport-list": {"keywords": ["screenshot"], "transport": ["sse"]},
        "transport-unknown-with-url": {
            "keywords": ["screenshot"],
            "transport": "carrier-pigeon",
            "url": "https://example.invalid/mcp",
        },
        "args-str": {"keywords": ["screenshot"], "args": "x"},
        "command-int": {"keywords": ["screenshot"], "command": 5},
        "headers-int": {"url": "https://example.invalid/mcp", "headers": 5},
        "prm-url-int": {
            "url": "https://example.invalid/mcp",
            "protected_resource_metadata_url": 5,
        },
        "oidc-issuer-int": {"url": "https://example.invalid/mcp", "oidc_issuer_url": 5},
        # rev 3: caught by the consumers' scoring and credential code, not by
        # an annotation check
        "keywords-ints": {"keywords": [5]},
        "secret-key-int": {
            "keywords": ["zz"],
            "requires_api_key": True,
            "env_var": "X",
            "secret_key": 5,
        },
    }
    clis = {
        "cli-check-int": {"check_command": 5},
        "cli-check-empty": {"check_command": []},
        "cli-description-int": {"description": 5},
        "cli-keywords-ints": {"keywords": [5]},
        "cli-examples-ints": {"examples": [5]},
        "cli-prefer-str": {"prefer_mcp_for": "x"},
    }
    overlay = {
        "servers": {**rows, "good": GOOD_SERVER},
        "cli_alternatives": {**clis, "goodcli": {}},
    }
    _write(Path.home() / ".pmcp" / "manifest.yaml", yaml.safe_dump(overlay))
    with caplog.at_level(logging.WARNING):
        manifest = load_manifest()
    assert not set(rows) & set(manifest.servers)
    assert not set(clis) & set(manifest.cli_alternatives)
    assert "good" in manifest.servers and "goodcli" in manifest.cli_alternatives
    skips = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("Skipping invalid")
    ]
    assert len(skips) == len(rows) + len(clis)
    # Overlay names are never shown; the reason names a field, not a value.
    assert all("(name not shown)" in m for m in skips)
    assert not any(name in m for m in skips for name in [*rows, *clis])
    assert any("'transport'" in m for m in skips)
    assert any("'check_command'" in m for m in skips)


def test_a_bad_cli_in_an_approved_project_overlay_is_skipped(project: Path) -> None:
    """Round 1's F1, behind the consent gate, via the real CLI."""
    project.write_text(
        yaml.safe_dump(
            {
                "servers": {"projonly": {"keywords": ["gadget"]}},
                "cli_alternatives": {"zzcli": {"check_command": 5}},
            }
        )
    )
    _run_cli("trust", "approve", str(project))
    tools = _gateway()
    assert "zzcli" not in load_manifest().cli_alternatives
    assert _candidates(tools, "screenshot") == ["playwright"]
    assert _candidates(tools, "gadget") == ["projonly"]


# --- null means absent (round 1, F3) -------------------------------------------


def test_null_fields_take_their_defaults_and_keep_the_entry() -> None:
    """A blank `description:` must not drop an override back to shipped."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        """
servers:
  github:
    description:
    command: my-github-fork
    keywords:
    args:
    declared_scopes:
  mytool:
    description:
    command: npx
cli_alternatives:
  mycli:
    help_command:
    keywords:
""",
    )
    manifest = load_manifest()
    assert manifest.servers["github"].command == "my-github-fork"
    assert manifest.servers["github"].description == ""
    assert manifest.servers["github"].keywords == []
    assert manifest.servers["github"].args == []
    assert manifest.servers["mytool"].description == ""
    assert manifest.cli_alternatives["mycli"].help_command == ["mycli", "--help"]
    assert manifest.cli_alternatives["mycli"].keywords == []


def test_every_shipped_entry_passes_every_consumer_check() -> None:
    """The shipped parse is not checked at load (it has no per-entry skip);
    this test is what holds it to the same rule."""
    manifest = load_manifest(_SHIPPED_MANIFEST_PATH)
    assert len(manifest.servers) > 100
    for server in manifest.servers.values():
        loader._canonical_server(server)
    for cli in manifest.cli_alternatives.values():
        loader._canonical_cli(cli)


# --- the second line: each consumer guards each entry ------------------------------
#
# Hand-built manifests bypass the parse-time check, so these prove each guard on
# its own. Every guard has its own mutant.


def _bad_server(**overrides: Any) -> ServerConfig:
    base: dict[str, Any] = {
        "name": "bad",
        "description": "bad",
        "keywords": ["screenshot"],
        "install": {},
        "command": "npx",
        "args": [],
    }
    base.update(overrides)
    return ServerConfig(**base)


def _with(
    servers: dict[str, ServerConfig], clis: dict[str, Any] | None = None
) -> Manifest:
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    return Manifest(
        "1.0",
        {**shipped.cli_alternatives, **(clis or {})},
        {**shipped.servers, **servers},
        ".mcp-gateway/discovery_queue.json",
    )


def test_guard_keyword_weights_skip_an_unusable_server() -> None:
    weights = loader.keyword_weights(
        [_bad_server(keywords=None), _bad_server(keywords=["a"])]
    )
    assert weights == {"a": 1.0}


def test_guard_catalog_scoring_skips_an_unusable_server() -> None:
    manifest = _with({"bad": _bad_server(keywords=[5])})
    found = _gateway()._manifest_candidates_for_query(
        "screenshot", manifest=manifest, configured_servers={}, exclude_servers=set()
    )
    assert [c.name for c in found] == ["playwright"]


def test_guard_catalog_candidate_build_skips_an_unusable_server() -> None:
    # A keyword of its own, so it scores without diluting playwright's.
    manifest = _with({"bad": _bad_server(transport=5, keywords=["zzbad"])})
    found = _gateway()._manifest_candidates_for_query(
        "zzbad screenshot",
        manifest=manifest,
        configured_servers={},
        exclude_servers=set(),
    )
    assert [c.name for c in found] == ["playwright"]


def test_guard_rank_cli_hints_skips_an_unusable_cli() -> None:
    bad = CLIAlternative("git", None, ["git", "--version"], ["git", "--help"], "x")  # type: ignore[arg-type]
    good = CLIAlternative("zzcli", ["zzq"], ["zzcli"], ["zzcli"], "zz")
    manifest = Manifest("1.0", {"git": bad, "zzcli": good}, {}, "q.json")
    hints = rank_cli_hints("zzq git", manifest, available_clis={"git", "zzcli"})
    assert [h.hint.name for h in hints] == ["zzcli"]


def test_guard_probe_clis_treats_an_unusable_command_as_not_detected() -> None:
    detected = asyncio.run(
        probe_clis(
            {
                "empty": {"check_command": []},
                "git": {"check_command": ["git", "--version"]},
            }
        )
    )
    assert "empty" not in detected


def test_guard_startup_skips_an_unusable_server() -> None:
    good = _bad_server(name="good", keywords=["zzgood"])
    resolution = resolve_startup_configs(
        [], manifest_servers={"bad": _bad_server(args=None), "good": good}
    )
    assert [c.name for c in resolution.lazy_configs] == ["good"]


def test_guard_refresh_survives_an_unusable_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _refresh_tools(monkeypatch)
    manifest = _with({"bad": _bad_server(command=5)})
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda **_k: manifest)
    assert asyncio.run(tools.refresh({"reason": "test"})).ok is True


def test_guard_match_capability_skips_an_unusable_server() -> None:
    manifest = _with({"bad": _bad_server(keywords=[5])})
    assert _keyword_match("screenshot", manifest, set()).entry_name == "playwright"


# --- the parse-time check IS each consumer's model, not a copy of it ------------
#
# Today ServerConfig's annotations are as strict as every consumer model, so a
# consumer projection only adds protection when a consumer becomes stricter.
# These tests make a consumer stricter and prove the parse-time check follows.

MARK = "zz-consumer-rejects-this"


def _overlay_with_marked_entry() -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "marked": {
                        "description": MARK,
                        "url": "https://example.invalid/mcp",
                        "keywords": ["zzmark"],
                    },
                    "good": GOOD_SERVER,
                },
                "cli_alternatives": {"markedcli": {"description": MARK}},
            }
        ),
    )


def test_a_stricter_candidate_model_is_enforced_at_parse_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pmcp.types as types_module

    class Stricter(types_module.CapabilityCandidate):
        @pydantic.field_validator("reasoning")
        @classmethod
        def _no_mark(cls, value: str) -> str:
            if value == MARK:
                raise ValueError("rejected")
            return value

    monkeypatch.setattr(types_module, "CapabilityCandidate", Stricter)
    _overlay_with_marked_entry()
    servers = load_manifest().servers
    assert "marked" not in servers and "good" in servers


def test_a_stricter_startup_conversion_is_enforced_at_parse_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pmcp.config.loader as config_loader

    real = config_loader._manifest_server_to_config

    def stricter(server: Any, env_lookup: Any) -> Any:
        if server.description == MARK:
            raise ValueError("rejected")
        return real(server, env_lookup)

    monkeypatch.setattr(config_loader, "_manifest_server_to_config", stricter)
    _overlay_with_marked_entry()
    servers = load_manifest().servers
    assert "marked" not in servers and "good" in servers


def test_a_stricter_cli_hint_model_is_enforced_at_parse_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pmcp.types as types_module

    class Stricter(types_module.CLIHint):
        @pydantic.field_validator("description")
        @classmethod
        def _no_mark(cls, value: str) -> str:
            if value == MARK:
                raise ValueError("rejected")
            return value

    monkeypatch.setattr(types_module, "CLIHint", Stricter)
    monkeypatch.setattr("pmcp.manifest.matcher.CLIHint", Stricter)
    _overlay_with_marked_entry()
    assert "markedcli" not in load_manifest().cli_alternatives


def test_the_marked_entries_load_when_no_consumer_is_stricter() -> None:
    """The control for the three tests above: they are not vacuous."""
    _overlay_with_marked_entry()
    manifest = load_manifest()
    assert "marked" in manifest.servers
    assert "markedcli" in manifest.cli_alternatives


def _request(monkeypatch: pytest.MonkeyPatch, manifest: Manifest, query: str) -> Any:
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda **_k: manifest)
    return asyncio.run(_gateway().request_capability({"query": query}))


def test_guard_request_capability_name_tier_skips_an_unusable_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _with({5: _bad_server(name=5)})  # type: ignore[dict-item]
    result = _request(monkeypatch, manifest, "github pull request")
    assert [c.name for c in result.candidates or []] == ["github"]


def test_guard_request_capability_name_match_skips_an_unusable_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _with({"puppeteer": _bad_server(name="puppeteer", env_var=5)})
    result = _request(monkeypatch, manifest, "puppeteer")
    assert result.status != "candidates" or all(
        c.name != "puppeteer" for c in result.candidates or []
    )


def test_guard_request_capability_category_keywords_skip_an_unusable_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _with({"puppeteer": _bad_server(name="puppeteer", keywords=None)})
    result = _request(monkeypatch, manifest, "browser automation screenshot")
    assert "playwright" in [c.name for c in result.candidates or []]


def test_guard_request_capability_category_candidate_skips_an_unusable_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _with({"puppeteer": _bad_server(name="puppeteer", description=5)})
    result = _request(monkeypatch, manifest, "browser automation screenshot")
    names = [c.name for c in result.candidates or []]
    assert "playwright" in names and "puppeteer" not in names


# --- revision 3: no stricter than the consumers (round 2, claude N1) ------------


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("transport", "stdio"),
        ("transport", "Local"),
        ("transport", "carrier-pigeon"),
        ("status", 5),
        ("source", ["x"]),
        ("replacement", {"x": 1}),
        ("discovery_diagnostics", [1]),
        ("auto_start", "maybe"),
        ("requires_api_key", "maybe"),
        ("keywords", "zzodd"),
    ],
)
def test_values_main_accepted_and_used_still_load(field_name: str, value: Any) -> None:
    """No consumer fails on these with no `url`, so main loaded and used them."""
    entry: dict[str, Any] = {"keywords": ["zzodd"], "command": "my-cmd"}
    entry[field_name] = value
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"servers": {"odd": entry}}),
    )
    manifest = load_manifest()
    assert "odd" in manifest.servers
    resolution = resolve_startup_configs([], manifest_servers=manifest.servers)
    started = {c.name: c for c in resolution.lazy_configs + resolution.eager_configs}
    assert started["odd"].config.command == "my-cmd"  # starts as local, as on main


def test_a_stdio_override_of_a_shipped_server_keeps_the_override() -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "github": {
                        "description": "my fork",
                        "transport": "stdio",
                        "command": "my-github-fork",
                        "keywords": ["github"],
                    }
                }
            }
        ),
    )
    assert load_manifest().servers["github"].command == "my-github-fork"
    assert "github" in _candidates(_gateway(), "github")


@pytest.mark.parametrize(
    ("entry", "loads"),
    [
        ({"keywords": "git"}, True),  # scored as characters, as on main
        ({"help_command": [1]}, False),  # CLIHint rejects it
        ({"check_command": "git"}, False),  # CLIHint rejects a str
        ({"check_command": []}, False),  # probe_clis reads slot 0
    ],
)
def test_a_cli_is_skipped_only_where_a_consumer_fails(
    entry: dict[str, Any], loads: bool
) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"cli_alternatives": {"odd": entry, "ok": {}}}),
    )
    clis = load_manifest().cli_alternatives
    assert ("odd" in clis) is loads and "ok" in clis


# --- revision 3: main's keyword counting, exactly (round 2, codex) ---------------


def _mains_manifest_keyword_weights(servers: Any) -> dict[str, float]:
    """`matcher._manifest_keyword_weights` on main c9206a9, verbatim."""
    frequencies: dict[str, int] = {}
    for server in servers:
        for keyword in set(server.keywords):
            keyword_norm = keyword.lower().replace("-", " ").replace("_", " ")
            frequencies[keyword_norm] = frequencies.get(keyword_norm, 0) + 1

    return {
        keyword: max(1.0 / frequency, 0.5) for keyword, frequency in frequencies.items()
    }


def _generated_keyword_sets(seed: int) -> list[list[str]]:
    """Keyword lists full of normalisation aliases: -, _, space, case, repeats."""
    import random

    rng = random.Random(seed)
    stems = ["alpha beta", "web search", "db", "Sql", "x"]
    out = []
    for _ in range(rng.randint(2, 9)):
        words = []
        for _ in range(rng.randint(0, 6)):
            stem = rng.choice(stems)
            sep = rng.choice([" ", "-", "_"])
            word = stem.replace(" ", sep)
            word = rng.choice([word, word.upper(), word.title()])
            words.append(word)
            if rng.random() < 0.3:
                words.append(word)  # a verbatim repeat
        out.append(words)
    return out


@pytest.mark.parametrize("seed", range(40))
def test_weights_equal_mains_for_hand_built_and_explicit_path_manifests(
    seed: int, tmp_path: Path
) -> None:
    keyword_sets = _generated_keyword_sets(seed)
    servers = {
        f"s{i}": ServerConfig(
            name=f"s{i}",
            description="",
            keywords=kws,
            install={},
            command="npx",
            args=[],
        )
        for i, kws in enumerate(keyword_sets)
    }
    expected = _mains_manifest_keyword_weights(servers.values())

    hand_built = Manifest("1.0", {}, servers, "q.json")
    assert _manifest_keyword_weights(hand_built) == expected

    path = _write(
        tmp_path / f"m{seed}.yaml",
        yaml.safe_dump(
            {"servers": {n: {"keywords": s.keywords} for n, s in servers.items()}}
        ),
    )
    assert _manifest_keyword_weights(load_manifest(path)) == expected


def test_the_round_2_alias_repro_picks_mains_match() -> None:
    servers = {
        "a-other": ServerConfig("a-other", "", ["alpha"], {}, "npx", []),
        "z-dupe": ServerConfig(
            "z-dupe", "", ["alpha-beta", "alpha_beta"], {}, "npx", []
        ),
    }
    manifest = Manifest("1.0", {}, servers, "q.json")
    assert _manifest_keyword_weights(manifest)["alpha beta"] == 0.5
    assert _keyword_match("alpha beta", manifest, set()).entry_name == "a-other"


# --- revision 3: no overlay name or value in any log line on these paths -------
# (round 2, codex F1 + claude N2). Covered: loading, discovery (catalog_search,
# request_capability, match_capability), CLI probing, startup and refresh
# resolution (skip lines), and lazy registration.

VALUE_SENTINEL = "sk-test-SENTINEL-value-77ab"
NAME_SENTINEL = "tok-SENTINEL-name-77ab"


def test_a_self_relaxing_credential_is_refused_without_its_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "relaxer": {
                        "command": "npx",
                        "requires_api_key": True,
                        "env_var": VALUE_SENTINEL,
                        "api_key_optional_when": [VALUE_SENTINEL],
                    }
                }
            }
        ),
    )
    with caplog.at_level(logging.DEBUG):
        manifest = load_manifest()
    assert manifest.servers["relaxer"].api_key_optional_when == []
    assert any("cannot relax itself" in r.getMessage() for r in caplog.records)
    assert not any(VALUE_SENTINEL in r.getMessage() for r in caplog.records)


def test_probe_clis_never_logs_an_overlay_cli_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        detected = asyncio.run(
            probe_clis({NAME_SENTINEL: {"check_command": ["git", "--version"]}})
        )
    assert NAME_SENTINEL in detected  # it was probed and found
    assert any("Detected" in r.getMessage() for r in caplog.records)
    assert not any(NAME_SENTINEL in r.getMessage() for r in caplog.records)


def test_no_overlay_name_or_value_reaches_any_log_on_these_paths(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A sweep: valid, KEPT overlay entries named and valued by sentinels."""
    from pmcp.client.manager import ClientManager
    from pmcp.config.loader import startup_skip_message

    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    NAME_SENTINEL: {
                        "description": "sentinel server",
                        "keywords": ["screenshot", "zzsent"],
                        "command": "npx",
                        "requires_api_key": True,
                        "env_var": VALUE_SENTINEL,
                        "api_key_optional_when": [VALUE_SENTINEL],
                    },
                    "good": GOOD_SERVER,
                },
                "cli_alternatives": {
                    NAME_SENTINEL + "-cli": {
                        "keywords": ["zzsent"],
                        "check_command": ["git", "--version"],
                    }
                },
            }
        ),
    )
    tools = _refresh_tools(monkeypatch)
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_enabled_auto_start", lambda **_: {NAME_SENTINEL}
    )
    with caplog.at_level(logging.DEBUG):
        manifest = load_manifest()
        assert NAME_SENTINEL in manifest.servers
        for query in ("zzsent", "screenshot", "zzsent git"):
            asyncio.run(tools.catalog_search({"query": query, "include_offline": True}))
            asyncio.run(tools.request_capability({"query": query}))
        _keyword_match("zzsent", manifest, set())
        resolution = resolve_startup_configs(
            [],
            manifest_servers=manifest.servers,
            enabled_auto_start={NAME_SENTINEL},
            is_auth_available=lambda _key: False,
        )
        assert any(s.name == NAME_SENTINEL for s in resolution.skipped)
        for skipped in resolution.skipped:
            logging.getLogger("pmcp.server").info(
                startup_skip_message("startup", skipped)
            )
        asyncio.run(tools.refresh({"reason": "sweep"}))
        lazy = resolve_startup_configs([], manifest_servers=manifest.servers)
        ClientManager().register_lazy_configs(lazy.lazy_configs)
    messages = [r.getMessage() for r in caplog.records]
    assert any("Registered lazy server" in m for m in messages)
    assert any("Skipping startup entry" in m for m in messages)
    leaks = [m[:160] for m in messages if NAME_SENTINEL in m or VALUE_SENTINEL in m]
    assert leaks == []


# --- revision 4: every scoring statistic is base-only (round 3, grok F001) -----


def _ask(
    query: str, available: tuple[str, ...] = (), tools: GatewayTools | None = None
) -> tuple[str, list[str]]:
    result = asyncio.run(
        (tools or _gateway()).request_capability(
            {"query": query, "available_clis": list(available)}
        )
    )
    return result.status, sorted(c.name for c in result.candidates or [])


def test_a_replaced_category_server_does_not_empty_another_category() -> None:
    """Grok F001's falsifier: `markdown` spans 2 categories; a replacement of
    `playwright` with `keywords: [markdown]` made it 3 (weight 0.7 -> 0.3)."""
    before = _ask("markdown")
    assert before[0] == "pick_from_category"
    assert {"firecrawl", "jina"} <= set(before[1])
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        "servers:\n  playwright:\n    description: x\n    command: npx\n"
        "    keywords: [markdown]\n",
    )
    assert _ask("markdown") == before


@functools.lru_cache(maxsize=1)
def _category_vocabulary_cached() -> tuple[str, ...]:
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    words: set[str] = set()
    for names in loader._CATEGORY_MAP.values():
        for name in names:
            if name in shipped.servers:
                words.update(shipped.servers[name].keywords)
    words.update(loader._CATEGORY_MAP)
    return tuple(sorted(words))


def _category_vocabulary() -> list[str]:
    """Every category word: each mapped shipped server's keywords and every
    category name (built once; a fresh list each call)."""
    return list(_category_vocabulary_cached())


@functools.lru_cache(maxsize=1)
def _overlays_cached() -> dict[str, dict[str, Any]]:
    """Generated overlays: additive, replacing a shipped name, replacing a
    `_CATEGORY_MAP` name -- each declaring keywords from OTHER categories."""
    import random

    rng = random.Random(342)
    vocab = _category_vocabulary()
    mapped = sorted({n for ns in loader._CATEGORY_MAP.values() for n in ns})
    shipped = sorted(load_manifest(_SHIPPED_MANIFEST_PATH).servers)
    unmapped = [n for n in shipped if n not in mapped]

    def entry() -> dict[str, Any]:
        return {"command": "npx", "keywords": rng.sample(vocab, 25)}

    def cli() -> dict[str, Any]:
        return {"keywords": rng.sample(vocab, 25), "check_command": ["git"]}

    return {
        "additive": {"servers": {f"zz-added-{i}": entry() for i in range(4)}},
        "replace-shipped": {
            "servers": {rng.choice(unmapped): entry() for _ in range(3)}
        },
        "replace-mapped": {
            "servers": {name: entry() for name in rng.sample(mapped, 6)}
        },
        "grok-f001": {
            "servers": {"playwright": {"command": "npx", "keywords": ["markdown"]}}
        },
        "everything": {"servers": {"zz-all": {"command": "npx", "keywords": vocab}}},
        # rev 5 (claude F001): overlay CLIs, new and overriding shipped `git`,
        # each AVAILABLE, competing with the category tier.
        "cli-new": {"cli_alternatives": {"zzcli": cli()}},
        "cli-override-git": {"cli_alternatives": {"git": cli()}},
        "cli-everything": {
            "cli_alternatives": {
                "zzcli-all": {"keywords": vocab, "check_command": ["git"]}
            }
        },
    }


def _overlays() -> dict[str, dict[str, Any]]:
    """The generated overlays (seeded, so built once; a deep copy each call)."""
    import copy

    return copy.deepcopy(_overlays_cached())


_NO_OVERLAY_SNAPSHOTS: dict[tuple[str, ...], dict[str, Any]] = {}


@pytest.mark.parametrize("case", sorted(_overlays()))
def test_no_overlay_removes_a_non_overlay_server_from_any_discovery_entry_point(
    case: str,
) -> None:
    """Differential over the category vocabulary, on request_capability,
    catalog_search (limit lifted: top-N displacement is the documented
    exception) and match_capability (top-1: displaced only by an overlay).

    Exception, documented in D2: a query naming an overlay server resolves to
    it in request_capability's name tier (precedence, like top-N)."""
    document = _overlays()[case]
    overlay_servers = document.get("servers", {})
    available = tuple(document.get("cli_alternatives", {}))
    queries = _category_vocabulary()
    tools = _gateway()

    def snapshot() -> dict[str, Any]:
        manifest = load_manifest()
        out: dict[str, Any] = {}
        for q in queries:
            catalog = {
                c.name
                for c in tools._manifest_candidates_for_query(
                    q,
                    manifest=manifest,
                    configured_servers={},
                    exclude_servers=set(),
                    limit=1000,
                )
            }
            km = _keyword_match(q, manifest, set())
            out[q] = (
                _ask(q, available, tools),
                catalog,
                km.entry_name if km.matched else None,
            )
        return out

    # Before any overlay the snapshot depends only on `available`: every test
    # starts from the same empty HOME and the shipped manifest. The cases that
    # pass the same `available` share one.
    if available not in _NO_OVERLAY_SNAPSHOTS:
        with _one_manifest_parse():
            _NO_OVERLAY_SNAPSHOTS[available] = snapshot()
    before = _NO_OVERLAY_SNAPSHOTS[available]
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(document),
    )
    with _one_manifest_parse():
        after = snapshot()
    ours = set(overlay_servers)
    names = {loader.normalized_server_name(n) for n in ours}
    lost = []
    for q in queries:
        (st0, rc0), cat0, km0 = before[q]
        (st1, rc1), cat1, km1 = after[q]
        if (
            not set(q.lower().replace("-", " ").replace("_", " ").split())
            & {w for n in ours for w in n.lower().replace("-", " ").split()}
            and loader.normalized_server_name(q) not in names
        ):
            gone = (set(rc0) - ours) - set(rc1)
            if gone:
                lost.append((q, "request_capability", sorted(gone)))
        gone = (cat0 - ours) - cat1
        if gone:
            lost.append((q, "catalog_search", sorted(gone)))
        if km0 and km0 not in ours and km1 != km0 and km1 not in ours:
            lost.append((q, "match_capability", [km0]))
    assert lost == []
    assert sum(1 for q in queries if before[q][0][1]) > 50  # not vacuous


def test_an_overlay_name_aliasing_a_shipped_name_keeps_the_shipped_match() -> None:
    """request_capability's name index: the base name wins a normalised tie."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"servers": {"play_wright": {"command": "npx"}}}),
    )
    assert _ask("playwright")[1] == ["playwright"]


# --- revision 4: every field against every consumer that reads it (codex F051) --


def test_a_remote_entry_with_bad_args_cannot_abort_load_configs() -> None:
    """Codex F051's falsifier, through the real loader and load_configs."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        "servers:\n  remote-bad:\n    url: https://example.invalid/mcp\n"
        "    command: npx\n    args: 5\n",
    )
    config = _write(
        Path.home() / ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "remote-bad": {"args": []},
                    "healthy": {"command": "healthy-command"},
                }
            }
        ),
    )
    assert "remote-bad" not in load_manifest().servers
    resolved = load_configs(user_config_paths=[config])
    assert "healthy" in {entry.name for entry in resolved}


def test_guard_configured_default_inheritance_contains_one_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The second line: a manifest that bypassed the overlay check."""
    bad = _bad_server(name="remote-bad", args=5, url="https://example.invalid/mcp")
    manifest = _with({"remote-bad": bad})
    monkeypatch.setattr("pmcp.manifest.loader.load_manifest", lambda **_k: manifest)
    config = _write(
        tmp_path / "cfg.json",
        json.dumps(
            {
                "mcpServers": {
                    # partial: inheriting the bad entry's `args` is what raised
                    "remote-bad": {"args": []},
                    "healthy": {"command": "healthy-command"},
                }
            }
        ),
    )
    resolved = {c.name: c for c in load_configs(user_config_paths=[config])}
    assert "healthy" in resolved
    # With no usable defaults and no command, it is skipped, exactly as when
    # the manifest is unavailable; it never takes its siblings down.
    assert "remote-bad" not in resolved


def test_guard_secrets_contains_one_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude F1: one bad entry dropped every later server's auth metadata."""
    bad = _bad_server(name="aaa-bad", headers=5)
    good = _bad_server(
        name="zz-good-remote",
        url="https://example.invalid/r",
        oidc_issuer_url="https://issuer.invalid",
    )
    manifest = _with({"aaa-bad": bad, "zz-good-remote": good})
    monkeypatch.setattr(
        "pmcp.cli_commands.secrets.load_manifest", lambda **_k: manifest
    )
    _, _, auth_metadata, _ = _extract_required_keys(Path.cwd())
    assert "zz-good-remote" in auth_metadata


@pytest.mark.parametrize(
    ("entry", "loads"),
    [
        ({"url": "https://example.invalid/m", "args": 5}, False),
        ({"url": "https://example.invalid/m", "command": 5}, False),
        ({"url": "https://example.invalid/m", "args": "x"}, True),  # chars, as on main
        ({"command": "npx", "headers": 5}, False),  # pmcp secrets reads it
        ({"url": "https://example.invalid/m", "extra_env": {"A": 1}}, True),
    ],
)
def test_a_field_is_checked_against_every_consumer_that_reads_it(
    entry: dict[str, Any], loads: bool
) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"servers": {"odd": entry, "good": GOOD_SERVER}}),
    )
    servers = load_manifest().servers
    assert ("odd" in servers) is loads and "good" in servers


# --- revision 5: YAML errors are value-free (round 4, codex F002 / grok F001) ---

YAML_SENTINEL = "sk-yaml-SENTINEL-9c1e"


def _malformed_documents() -> dict[str, str]:
    """Malformed YAML from the grammar, the sentinel at different positions."""
    s = YAML_SENTINEL
    good = "  good: {keywords: [zzgood], command: npx}\n"
    return {
        "flow-seq-unterminated": f"servers:\n  bad:\n    args: [{s}\n" + good,
        "flow-seq-unterminated-first": f"servers:\n  bad: {{args: [{s}, x\n",
        "flow-map-unterminated": f"servers:\n  bad: {{command: {s}\n" + good,
        "bad-indentation": f"servers:\n  bad:\n    command: npx\n   args: {s}\n",
        "tab-indentation": f"servers:\n  bad:\n\tcommand: {s}\n",
        "unclosed-double-quote": f'servers:\n  bad:\n    command: "{s}\n',
        "unclosed-single-quote": f"servers:\n  bad:\n    command: '{s}\n",
        "invalid-escape": f'servers:\n  bad:\n    command: "\\q{s}"\n',
        "bad-tag": f"servers:\n  bad:\n    command: !<!{s}> x\n",
        "python-tag": f"servers:\n  bad:\n    command: !!python/object:os.{s} x\n",
        "undefined-alias": f"servers:\n  bad:\n    command: *{s}\n",
        "key-in-error-token": f"servers:\n  {s}: [\n",
        "sentinel-last-line": f"servers:\n  bad:\n    args: [x\n# {s}\n",
        "block-in-flow": f"servers: [{s}, {{a: b}}\n  c: d]\n",
    }


@pytest.mark.parametrize("case", sorted(_malformed_documents()))
@pytest.mark.parametrize("source", ["user", "env", "project"])
def test_a_malformed_overlay_is_reported_without_any_value(
    case: str,
    source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = _malformed_documents()[case]
    if source == "user":
        path = _write(Path.home() / ".pmcp" / "manifest.yaml", text)
    elif source == "env":
        path = _write(tmp_path / "env.yaml", text)
        monkeypatch.setenv("PMCP_MANIFEST_PATH", str(path))
    else:
        path = _write(tmp_path / "proj" / ".pmcp" / "manifest.yaml", text)
        approve_project_file(path)
        monkeypatch.chdir(tmp_path / "proj")
    with caplog.at_level(logging.DEBUG):
        manifest = load_manifest()
        _candidates(_gateway(), "screenshot")
    assert "playwright" in manifest.servers  # the shipped manifest still loads
    messages = [r.getMessage() for r in caplog.records]
    assert not [m for m in messages if YAML_SENTINEL in m]
    unreadable = [m for m in messages if "Skipping unreadable manifest overlay" in m]
    assert unreadable, messages[-5:]
    # The parse failure's value-free description: format, source, position
    # and class (Consiliency/pmcp#297).
    assert all(
        re.search(
            r": could not parse YAML manifest overlay at line \d+, column \d+ \(\w+\)$",
            m,
        )
        for m in unreadable
    ), unreadable


def test_codex_f002_falsifier_parse_overlay_document_directly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    content = (
        f"servers:\n  malformed:\n    command: npx\n    args: [{YAML_SENTINEL}\n"
    ).encode()
    with caplog.at_level(logging.WARNING):
        result = loader._parse_overlay_document(Path("overlay.yaml"), content)
    assert result == ({}, {}, {}, {})
    assert YAML_SENTINEL not in caplog.text


def test_a_load_failure_is_logged_by_class_on_every_catch_site(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The sites that catch load_manifest() and log it (refresh, load_configs)."""

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise yaml.YAMLError(f"bad token {YAML_SENTINEL}")

    tools = _refresh_tools(monkeypatch)
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", boom)
    monkeypatch.setattr("pmcp.manifest.loader.load_manifest", boom)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(tools.refresh({"reason": "test"}))
        load_configs()
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "Failed to load manifest startup configs: YAMLError" in m for m in messages
    )
    assert any("Manifest defaults unavailable" in m for m in messages)
    assert not [m for m in messages if YAML_SENTINEL in m]


# --- revision 5: parse, don't validate (round 4, codex F001) -------------------

YAML_TAG_VALUES = {
    "binary": "!!binary bnB4",
    "timestamp": "2001-12-14t21:59:43.10-05:00",
    "set": "!!set {a, b}",
    "omap": "!!omap [{a: 1}]",
    "float-tag": '!!float "1.5"',
    "inf": ".inf",
    "nan": ".nan",
    "python-tag": "!!python/name:os.system",
}


def _json_native(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, list):
        return all(_json_native(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _json_native(v) for k, v in value.items())
    return False


@pytest.mark.parametrize("tag", sorted(YAML_TAG_VALUES))
def test_no_consumer_ever_sees_a_non_json_value(
    tag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A YAML tag on EVERY field, one entry per field, local and remote, each
    with a partial `.mcp.json` entry of the same name: the kept entries are
    JSON-native all the way down, and every consumer runs."""
    value = YAML_TAG_VALUES[tag]
    lines = ["base: &base {keywords: [zzbad]}", "servers:"]
    partial: dict[str, Any] = {"healthy": {"command": "healthy-command"}}
    for field_name in SERVER_FIELDS:
        for kind, extra in (
            ("local", "command: npx"),
            ("remote", "url: https://example.invalid/mcp"),
        ):
            # zz- names: ties rank by name, so playwright stays in the top 5
            name = f"zz-bad-{kind}-{field_name.replace('_', '-')}"
            lines += [
                f"  {name}:",
                "    <<: *base",
                f"    {extra}",
                f"    {field_name}: {value}",
            ]
            partial[name] = {"args": []}
    lines += [
        "  good: {keywords: [zzgood], command: npx}",
        "  zz-good-remote: {keywords: [zzremote], url: https://example.invalid/r, "
        "oidc_issuer_url: https://issuer.invalid}",
        "cli_alternatives:",
        "  goodcli: {keywords: [zzcli]}",
    ]
    for field_name in CLI_FIELDS:
        lines += [f"  badcli-{field_name.replace('_', '-')}: {{{field_name}: {value}}}"]
    _write(Path.home() / ".pmcp" / "manifest.yaml", "\n".join(lines) + "\n")
    _write(Path.home() / ".mcp.json", json.dumps({"mcpServers": partial}))
    manifest = load_manifest()
    for server in manifest.servers.values():
        assert _json_native(dataclasses.asdict(server)), server.name
    for cli in manifest.cli_alternatives.values():
        assert _json_native(dataclasses.asdict(cli)), cli.name
    if tag == "python-tag":
        assert "good" not in manifest.servers  # the whole document is refused
        return
    assert "good" in manifest.servers
    _drive_every_consumer(_refresh_tools(monkeypatch))
    configured = load_configs()
    assert "healthy" in {c.name for c in filter_self_references(configured)}


def test_codex_f001_falsifier_binary_command_does_not_abort_siblings() -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        "servers:\n  bad:\n    command: !!binary bnB4\n    args: []\n",
    )
    config = _write(
        Path.home() / ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "bad": {"args": []},
                    "healthy": {"command": "healthy-command"},
                }
            }
        ),
    )
    configs = load_configs(user_config_paths=[config])
    assert "healthy" in {entry.name for entry in configs}
    usable = filter_self_references(configs)
    assert "healthy" in {entry.name for entry in usable}


def test_consumers_read_the_validated_value_not_the_raw_one() -> None:
    """`supports_url_elicitation: "false"` validates to False; on rev 4 the kept
    entry held the raw (truthy) string, so `pmcp secrets` reported "false" as
    an elicitation-capable server's metadata."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "remote": {
                        "url": "https://example.invalid/m",
                        "supports_url_elicitation": "false",
                        "declared_scopes": ["a"],
                    }
                }
            }
        ),
    )
    server = load_manifest().servers["remote"]
    assert server.supports_url_elicitation is False
    _, _, auth_metadata, _ = _extract_required_keys(Path.cwd())
    assert "supports_url_elicitation" not in auth_metadata.get("remote", {})


def test_every_shipped_entry_is_already_canonical() -> None:
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    for server in shipped.servers.values():
        assert loader._canonical_server(server) == server, server.name
    for cli in shipped.cli_alternatives.values():
        assert loader._canonical_cli(cli) == cli, cli.name


@pytest.mark.parametrize(
    ("entry", "loads"),
    [
        ({"command": "npx", "oidc_issuer_url": 5}, False),  # claude N1
        ({"command": "npx", "supports_url_elicitation": "maybe"}, False),
        ({"command": "npx", "supports_url_elicitation": "true"}, True),
        ({"command": "npx", "headers": {"X-Key": "${TOKEN}"}}, True),
    ],
)
def test_metadata_fields_are_checked_on_local_entries_too(
    entry: dict[str, Any], loads: bool
) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"servers": {"odd": entry, "good": GOOD_SERVER}}),
    )
    servers = load_manifest().servers
    assert ("odd" in servers) is loads and "good" in servers


# --- revision 5: an overlay CLI never pre-empts the server tiers (claude F001) ---


def test_claude_f001_falsifier_an_overlay_cli_does_not_hide_category_servers() -> None:
    assert "playwright" in _ask("browser automation")[1]
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "cli_alternatives": {
                    "zzcli": {
                        "description": "d",
                        "keywords": ["browser automation"],
                        "check_command": [sys.executable, "--version"],
                    }
                }
            }
        ),
    )
    result = asyncio.run(_gateway().request_capability({"query": "browser automation"}))
    assert "playwright" in {c.name for c in result.candidates or []}


def test_an_overlay_cli_still_answers_when_no_server_tier_does() -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "cli_alternatives": {
                    "zzcli": {"keywords": ["zzonlycli"], "check_command": ["git"]}
                }
            }
        ),
    )
    result = asyncio.run(
        _gateway().request_capability(
            {"query": "zzonlycli", "available_clis": ["zzcli"]}
        )
    )
    assert result.status == "use_cli"


def test_a_shipped_cli_keeps_mains_precedence() -> None:
    """Only an overlay CLI yields; shipped `git` answers as on main."""
    status, _ = _ask("git commits", ("git",))
    assert status == "use_cli"


def test_inherited_fields_are_kept_converted() -> None:
    """A remote entry's `args: "ab"` is read by configured-default inheritance
    as characters (as on main); the KEPT entry holds that converted list, so
    no other consumer reads the raw string."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {"servers": {"remote": {"url": "https://example.invalid/m", "args": "ab"}}}
        ),
    )
    assert load_manifest().servers["remote"].args == ["a", "b"]


def test_an_overlay_cli_is_recorded_as_one() -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"cli_alternatives": {"zzcli": {}, "git": {}}}),
    )
    assert load_manifest().overlay_cli_names == frozenset({"zzcli", "git"})
    assert load_manifest(_SHIPPED_MANIFEST_PATH).overlay_cli_names == frozenset()


# --- revision 6: an overlay CLI never outranks a server (round 5, F001/F003) ----


def test_claude_f001_falsifier_an_overlay_cli_named_like_a_server() -> None:
    """Round 5: `request_capability("airbnb")` went to `use_cli`, no candidates."""
    assert "airbnb" in _ask("airbnb")[1]
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "cli_alternatives": {
                    "airbnb": {
                        "description": "d",
                        "keywords": ["airbnb"],
                        "check_command": [sys.executable, "--version"],
                    }
                }
            }
        ),
    )
    result = asyncio.run(_gateway().request_capability({"query": "airbnb"}))
    assert "airbnb" in {c.name for c in result.candidates or []}


def test_codex_f003_falsifier_an_overlay_cli_cannot_hide_an_exact_match() -> None:
    before = _ask("redis", ("redis",))
    assert "redis" in before[1]
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "cli_alternatives": {
                    "redis": {"keywords": ["redis"], "check_command": ["git"]}
                }
            }
        ),
    )
    assert "redis" in _ask("redis", ("redis",))[1]


def _every_discovery_term() -> list[str]:
    """Every shipped server name, every category word, every shipped keyword."""
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    terms: set[str] = set(shipped.servers)
    for cat_name in loader._CATEGORY_MAP:
        terms.update(cat_name.replace("/", " ").split())
        terms.add(cat_name)
    for server in shipped.servers.values():
        terms.update(server.keywords)
    return sorted(terms)


def test_no_overlay_cli_outranks_a_server_or_a_shipped_cli_anywhere() -> None:
    """The class, derived from the tiers: one overlay CLI per discovery term --
    named like the term when it is a server name -- all available at once. For
    every term: request_capability keeps every non-overlay server and every
    shipped-CLI answer; catalog_search's manifest candidates are unchanged;
    match_capability keeps its server or shipped-CLI match."""
    terms = _every_discovery_term()
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    clis: dict[str, Any] = {}
    for i, term in enumerate(terms):
        name = term if term in shipped.servers else f"zzcli-{i}"
        if name in shipped.cli_alternatives:
            name = f"zzcli-{i}"
        clis[name] = {"keywords": [term], "check_command": ["git"]}
    available = tuple(clis)
    tools = _gateway()

    def snapshot() -> dict[str, Any]:
        manifest = load_manifest()
        out: dict[str, Any] = {}
        for term in terms:
            result = asyncio.run(
                tools.request_capability(
                    {"query": term, "available_clis": list(available)}
                )
            )
            catalog = {
                c.name
                for c in tools._manifest_candidates_for_query(
                    term,
                    manifest=manifest,
                    configured_servers={},
                    exclude_servers=set(),
                    limit=1000,
                )
            }
            km = _keyword_match(term, manifest, set(available))
            out[term] = (
                result.status,
                result.cli.name if result.cli else None,
                {c.name for c in result.candidates or []},
                catalog,
                (km.entry_type, km.entry_name) if km.matched else None,
            )
        return out

    with _one_manifest_parse():
        before = snapshot()
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"cli_alternatives": clis}),
    )
    assert set(load_manifest().overlay_cli_names) == set(clis)
    with _one_manifest_parse():
        after = snapshot()
    lost = []
    for term in terms:
        st0, cli0, rc0, cat0, km0 = before[term]
        st1, cli1, rc1, cat1, km1 = after[term]
        if rc0 - rc1:
            lost.append((term, "request_capability servers", sorted(rc0 - rc1)))
        if st0 == "use_cli" and (st1, cli1) != (st0, cli0):
            lost.append((term, "request_capability shipped CLI", cli0))
        if cat0 != cat1:
            lost.append((term, "catalog_search", sorted(cat0 ^ cat1)))
        if km0 is not None and km1 != km0:
            lost.append((term, "match_capability", km0))
    assert lost == []
    # Not vacuous: an overlay CLI still answers where nothing else does.
    gained = [
        t for t in terms if before[t][0] == "not_available" and after[t][0] == "use_cli"
    ]
    assert gained
    assert sum(1 for t in terms if before[t][2]) > 100


def test_gemini_note_category_selection_reads_no_cli() -> None:
    """Round 5 (gemini): the claimed `kw in category_keywords.get(best_cat, [])`
    CLI comparison is not in the code; category selection reads servers only,
    so an overlay CLI with a category's whole vocabulary changes nothing."""
    import inspect

    source = inspect.getsource(Manifest.get_servers_in_category)
    assert "cli_alternatives" not in source
    before = load_manifest().get_servers_in_category("browser automation")
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {"cli_alternatives": {"zzcli": {"keywords": _category_vocabulary()}}}
        ),
    )
    assert load_manifest().get_servers_in_category("browser automation") == before


# --- revision 6: only schema fields are checked (round 5, codex F002 / claude F002) --


def test_claude_f002_falsifier_unknown_keys_neither_drop_nor_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "sk_live_planted_4f9a"
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        "servers:\n"
        "  mytool:\n"
        "    command: npx\n"
        "    keywords: [mytoolkw]\n"
        "    added: 2026-10-04\n"
        f"    {secret}: !!binary aGVsbG8=\n",
    )
    with caplog.at_level(logging.DEBUG):
        manifest = load_manifest()
    assert "mytool" in manifest.servers
    assert not any(secret in r.getMessage() for r in caplog.records)


def test_codex_f002_falsifier_a_rejection_names_no_overlay_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "sk-review-secret-field"
    document = (
        f"servers:\n  bad:\n    {sentinel}: .nan\n"
        f"    extra_env: {{{sentinel}-k: .nan}}\n"
        f"    args: [!!binary aGVsbG8=]\n"
    ).encode()
    with caplog.at_level(logging.DEBUG):
        loader._parse_overlay_document(Path("overlay.yaml"), document)
    assert caplog.records
    assert sentinel not in caplog.text
    assert any(
        "'args' holds a value that is not JSON" in r.getMessage()
        for r in caplog.records
    )


STORED_ONLY = [
    "discovery_diagnostics",
    "discovery_metadata",
    "replacement",
    "source",
    "status",
]


def test_the_stored_only_keys_are_the_loaders() -> None:
    assert set(STORED_ONLY) == set(loader._STORED_ONLY_KEYS)


@pytest.mark.parametrize("field_name", STORED_ONLY)
def test_a_stored_only_field_with_a_yaml_value_keeps_the_entry(field_name: str) -> None:
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        f"servers:\n  odd:\n    command: npx\n    {field_name}: 2026-10-04\n",
    )
    assert "odd" in load_manifest().servers


def test_the_schema_keys_are_exactly_the_keys_the_parsers_read() -> None:
    """Derived from the parsers' own source: every `data.get("<key>")`."""
    import ast
    import inspect

    def read_keys(func: Any) -> set[str]:
        tree = ast.parse(inspect.getsource(func))
        return {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "data"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        }

    assert read_keys(loader._parse_server_config) == set(loader._SERVER_SCHEMA_KEYS)
    assert read_keys(loader._parse_cli_alternative) == set(loader._CLI_SCHEMA_KEYS)


# --- revision 6: every exception from the YAML load is contained (codex F001) ----

TAG_SENTINEL = "sk-tag-SENTINEL-61d"


def _invalid_tag_documents() -> dict[str, str]:
    s = TAG_SENTINEL
    entry = "servers:\n  bad:\n    command: {}\n  good: {{keywords: [zzgood], command: npx}}\n"
    docs = {
        tag: entry.format(value)
        for tag, value in {
            "int": f"!!int {s}",
            "float": f"!!float {s}",
            "bool": f"!!bool {s}",
            "timestamp": f"!!timestamp {s}",
            "binary": f"!!binary {s}!",
            "set": f"!!set [{s}]",
            "omap": f"!!omap {{{s}: 1}}",
            "pairs": f"!!pairs {s}",
            "map": f"!!map [{s}]",
            "seq": f"!!seq {{{s}: 1}}",
            "str-map": f"!!str {{{s}: 1}}",
            "custom": f"!custom {s}",
            "python": f"!!python/name:{s}",
        }.items()
    }
    docs["merge-scalar"] = f"servers:\n  bad:\n    <<: {s}\n    command: npx\n"
    docs["merge-list-scalar"] = f"servers:\n  bad:\n    <<: [{s}]\n    command: npx\n"
    return docs


@pytest.mark.parametrize("case", sorted(_invalid_tag_documents()))
@pytest.mark.parametrize("source", ["user", "env", "project"])
def test_an_invalid_tag_is_contained_without_any_value(
    case: str,
    source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    approve_project_file: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = _invalid_tag_documents()[case]
    if source == "user":
        _write(Path.home() / ".pmcp" / "manifest.yaml", text)
    elif source == "env":
        monkeypatch.setenv("PMCP_MANIFEST_PATH", str(_write(tmp_path / "e.yaml", text)))
    else:
        path = _write(tmp_path / "proj" / ".pmcp" / "manifest.yaml", text)
        approve_project_file(path)
        monkeypatch.chdir(tmp_path / "proj")
    with caplog.at_level(logging.DEBUG):
        manifest = load_manifest()
        _candidates(_gateway(), "screenshot")
    assert "playwright" in manifest.servers
    assert "bad" not in manifest.servers
    messages = [r.getMessage() for r in caplog.records]
    assert not [m for m in messages if TAG_SENTINEL.lower() in m.lower()]
    # Either the document is refused (the load raised), or it parses and the
    # entry is skipped (`!!binary` with lenient base64 decodes to bytes).
    assert any(
        "Skipping unreadable manifest overlay" in m
        or "Skipping invalid server entry" in m
        for m in messages
    )


def test_codex_f001_falsifier_a_bad_numeric_tag_keeps_shipped_servers() -> None:
    base = loader._SHIPPED_MANIFEST_PATH
    overlay = loader._OverlaySource(
        "env",
        Path("overlay.yaml"),
        b"servers:\n  bad: {command: !!int sk-review-sentinel}\n",
        "read",
    )
    manifest, _ = loader._build_manifest(
        base, base.read_bytes(), [overlay], trusted=True
    )
    assert "playwright" in manifest.servers


# --- revision 7: one declared tier order; an overlay CLI is the last tier ----------
# (round 6: codex F001 -- an overlay CLI pre-empted the registry tier; claude N1 --
# an overlay CLI outranked a shipped one in `cli_hints`.) `REQUEST_CAPABILITY_TIERS`
# is the only statement of the order. The differential below is generated per
# declared tier, and a tier without a generator fails the coverage test.


def _codex_r6_registry_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[GatewayTools, Any]:
    from unittest.mock import AsyncMock

    from pmcp.types import CapabilityCandidate

    tools = _gateway()
    monkeypatch.setattr(tools, "_load_provisioned_registry", lambda: {})

    async def registry(query: str, **_: Any) -> list[CapabilityCandidate]:
        word = next(w for w in query.split()[1:] if re.fullmatch(r"[A-Z][a-z]{3,}", w))
        return [
            CapabilityCandidate(
                name=f"{word.lower()}-registry-server",
                candidate_type="server",
                relevance_score=1.0,
                reasoning="Registry match",
                source="registry",
            )
        ]

    spy = AsyncMock(side_effect=registry)
    monkeypatch.setattr(tools, "_registry_candidates_for_query", spy)
    return tools, spy


def test_codex_r6_f001_falsifier_an_overlay_cli_keeps_registry_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 6, codex F001: `connect Reviewservice` went to `use_cli: reviewcli`
    and the registry was never asked."""
    tools, registry = _codex_r6_registry_tools(monkeypatch)
    request = {"query": "connect Reviewservice", "available_clis": ["reviewcli"]}
    before = asyncio.run(tools.request_capability(request))
    assert before.status == "candidates"
    assert [c.name for c in before.candidates or []] == [
        "reviewservice-registry-server"
    ]
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "cli_alternatives": {
                    "reviewcli": {
                        "keywords": ["reviewservice"],
                        "check_command": ["git"],
                    }
                }
            }
        ),
    )
    assert "reviewcli" in load_manifest().overlay_cli_names
    after = asyncio.run(tools.request_capability(request))
    assert after.model_dump() == before.model_dump()
    assert registry.await_count == 2


def test_claude_r6_n1_falsifier_a_shipped_cli_hint_precedes_an_overlay_one() -> None:
    """Round 6, claude N1: overlay `aaa` (keyword `json`) was listed above `jq`."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "cli_alternatives": {
                    "aaa": {
                        "description": "x",
                        "keywords": ["json"],
                        "check_command": ["aaa", "--version"],
                    }
                }
            }
        ),
    )
    tools = _gateway()
    tools._detected_clis = {"jq", "aaa"}
    result = asyncio.run(
        tools.catalog_search({"query": "json", "include_offline": True})
    )
    names = [h.name for h in result.cli_hints]
    assert names.index("jq") < names.index("aaa")


def test_every_shipped_cli_hint_precedes_every_overlay_cli_hint() -> None:
    """The class for N1: for every keyword of every shipped CLI, an overlay CLI
    that sorts first by name and inflates its score by repeating the keyword
    still lists after every shipped hint, and no shipped hint is lost."""
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    queries = sorted(
        {kw for cli in shipped.cli_alternatives.values() for kw in cli.keywords}
    )
    overlay = {
        f"aaa-{i}": {"keywords": [kw] * 5, "check_command": ["git"]}
        for i, kw in enumerate(queries)
    }
    everything = set(shipped.cli_alternatives) | set(overlay)

    def hints() -> dict[str, list[str]]:
        manifest = load_manifest()
        return {
            q: [
                m.hint.name
                for m in rank_cli_hints(q, manifest, available_clis=everything)
            ]
            for q in queries
        }

    before = hints()
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"cli_alternatives": overlay}),
    )
    after = hints()
    wrong = []
    for q in queries:
        origin = [name in overlay for name in after[q]]
        if origin != sorted(origin):
            wrong.append((q, after[q][:6]))
        if [n for n in after[q] if n not in overlay] != before[q]:
            wrong.append((q, "shipped hints changed"))
    assert wrong == []
    assert sum(1 for q in queries if any(n in overlay for n in after[q])) == len(
        queries
    )


def test_the_declared_tiers_are_the_only_tiers_and_the_overlay_cli_is_last() -> None:
    """`request_capability` returns a tier's answer only from the loop over
    `REQUEST_CAPABILITY_TIERS`; every declared tier has its method and every
    tier method is declared."""
    import ast
    import inspect
    import textwrap

    from pmcp.tools import handlers

    tiers = handlers.REQUEST_CAPABILITY_TIERS
    assert tiers[-1] == "overlay_cli"
    assert len(set(tiers)) == len(tiers)
    methods = {
        name.removeprefix("_capability_tier_")
        for name in vars(GatewayTools)
        if name.startswith("_capability_tier_")
    }
    assert methods == set(tiers)
    tree = ast.parse(
        textwrap.dedent(inspect.getsource(GatewayTools.request_capability))
    )
    returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
    # One return inside the tier loop, one `not_available` after it.
    assert len(returns) == 2
    loop = next(n for n in ast.walk(tree) if isinstance(n, ast.For))
    assert ast.unparse(loop.iter) == "REQUEST_CAPABILITY_TIERS"
    in_loop = [r for r in returns if r in list(ast.walk(loop))]
    after_loop = [r for r in returns if r not in in_loop]
    assert len(in_loop) == 1 and len(after_loop) == 1
    assert "not_available" in ast.unparse(after_loop[0])


def _tier_scenarios_name() -> list[tuple[str, dict[str, Any]]]:
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    return [(name, {"query": name}) for name in sorted(shipped.servers)]


def _tier_scenarios_shipped_cli() -> list[tuple[str, dict[str, Any]]]:
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    return [
        (kw, {"query": kw, "available_clis": [name]})
        for name, cli in sorted(shipped.cli_alternatives.items())
        for kw in cli.keywords
    ]


def _tier_scenarios_category() -> list[tuple[str, dict[str, Any]]]:
    return [(word, {"query": word}) for word in _category_vocabulary()]


CONFIGURED_SERVERS = {f"zzconf-{i}": f"zzconfarg{i}" for i in range(8)}


def _tier_scenarios_configured() -> list[tuple[str, dict[str, Any]]]:
    return [(arg, {"query": f"use {arg}"}) for arg in CONFIGURED_SERVERS.values()]


REGISTRY_WORDS = [
    "Reviewservice",
    "Ledgerbook",
    "Paymentsco",
    "Mailroom",
    "Ticketdesk",
    "Shipmate",
    "Calendarly",
    "Invoicely",
]


def _tier_scenarios_registry() -> list[tuple[str, dict[str, Any]]]:
    return [(word.lower(), {"query": f"connect {word}"}) for word in REGISTRY_WORDS]


# One generator per declared tier except the overlay CLI's own.
TIER_SCENARIOS = {
    "name": _tier_scenarios_name,
    "shipped_cli": _tier_scenarios_shipped_cli,
    "category": _tier_scenarios_category,
    "configured": _tier_scenarios_configured,
    "registry": _tier_scenarios_registry,
}


def test_every_declared_tier_has_a_scenario_generator() -> None:
    from pmcp.tools.handlers import REQUEST_CAPABILITY_TIERS

    assert set(TIER_SCENARIOS) == set(REQUEST_CAPABILITY_TIERS) - {"overlay_cli"}


def _record_tiers(tools: GatewayTools, answered: list[str]) -> None:
    """Wrap every declared tier method so a run records which tier answered."""
    from pmcp.tools.handlers import REQUEST_CAPABILITY_TIERS

    for tier in REQUEST_CAPABILITY_TIERS:
        method = getattr(tools, f"_capability_tier_{tier}")

        async def spy(request: Any, _method: Any = method, _tier: str = tier) -> Any:
            answer = await _method(request)
            if answer is not None:
                answered.append(_tier)
            return answer

        setattr(tools, f"_capability_tier_{tier}", spy)


def test_an_overlay_cli_answers_after_every_declared_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The differential, generated per declared tier: every scenario, then the
    same scenario with an AVAILABLE overlay CLI for its term (named like the
    term where it can be) -- the same tier answers, with the same response.
    Every tier other than the overlay CLI's answers at least once, so no tier
    goes unexercised (codex F001: the registry tier was never generated)."""
    from pmcp.tools.handlers import REQUEST_CAPABILITY_TIERS

    _write(
        Path.home() / ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    name: {"command": "zzconfcmd", "args": [arg]}
                    for name, arg in CONFIGURED_SERVERS.items()
                }
            }
        ),
    )
    scenarios = [
        (tier, term, request)
        for tier, generate in TIER_SCENARIOS.items()
        for term, request in generate()
    ]
    shipped = load_manifest(_SHIPPED_MANIFEST_PATH)
    clis: dict[str, Any] = {}
    for i, (_tier, term, _request) in enumerate(scenarios):
        name = term if term not in shipped.cli_alternatives else f"zzcli-{i}"
        if name in clis:
            name = f"zzcli-{i}"
        clis[name] = {"keywords": [term], "check_command": ["git"]}
    tools, _registry = _codex_r6_registry_tools(monkeypatch)
    answered: list[str] = []
    _record_tiers(tools, answered)

    def run() -> list[tuple[str, dict[str, Any]]]:
        out = []
        for _tier, _term, request in scenarios:
            answered.clear()
            available = list(request.get("available_clis", [])) + list(clis)
            result = asyncio.run(
                tools.request_capability({**request, "available_clis": available})
            )
            out.append(
                (answered[0] if answered else "not_available", result.model_dump())
            )
        return out

    with _one_manifest_parse():
        before = run()
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump({"cli_alternatives": clis}),
    )
    assert set(load_manifest().overlay_cli_names) == set(clis)
    with _one_manifest_parse():
        after = run()
    changed = [
        (tier, term, b[0], a[0])
        for (tier, term, _), b, a in zip(scenarios, before, after)
        if b != a and b[0] != "not_available"
    ]
    assert changed == []
    # Every declared tier but the overlay CLI's answered in the generated set.
    assert {b[0] for b in before} >= set(REQUEST_CAPABILITY_TIERS) - {"overlay_cli"}
    # Not vacuous: the overlay CLI still answers where no other tier does.
    assert any(
        b[0] == "not_available" and a[0] == "overlay_cli" for b, a in zip(before, after)
    )


# --- revision 7: inherited overlay data reaches no diagnostic (round 6, codex F002) --
# A command-less `.mcp.json` entry inherits `command`, `args`, `extra_env` and the
# credential from the manifest entry of the same name -- an overlay's, when an
# overlay adds or replaces it. Every diagnostic on the load, discovery, startup,
# refresh and lazy-registration paths renders a config field through
# `config_field_diagnostic` (name and field name, never the value), and
# `test_no_diagnostic_on_these_paths_reads_a_config_field` derives the sites.

R7_CRED = "sk-r7-credential-v4lue-5e1f"


def _r7_sentinel(field_name: str) -> str:
    return f"sk-r7-{field_name.replace('_', '-')}-v4lue"


def _r7_value(field_name: str) -> Any:
    """A KEPT value for ``field_name`` carrying its sentinel, or None when the
    field's type cannot hold one (a bool or a Literal)."""
    import typing

    hint = str(typing.get_type_hints(ServerConfig)[field_name])
    s = _r7_sentinel(field_name)
    if field_name in ("env_var", "secret_key"):
        return s.upper().replace("-", "_")
    if field_name == "url" or field_name.endswith("_url"):
        return f"https://{s}.example.invalid/x"
    if field_name == "install":
        return {p: ["npx", "-y", s] for p in ("mac", "wsl", "linux", "windows")}
    if field_name == "extra_env":
        return {"ZZ_R7_EXTRA": s}
    if field_name == "api_key_optional_when":
        return ["ZZ_R7_EXTRA"]
    if hint.startswith("list[str]"):
        return [s]
    if hint.startswith("dict[str, str]") or hint.startswith("dict[str, typing.Any]"):
        return {"zzr7key": s}
    if hint.startswith("<class 'str'>") or hint.startswith("str"):
        return s
    return None


R7_FIELDS = [f for f in SERVER_FIELDS if _r7_value(f) is not None]

# Self-referencing entries: the gateway's own command, reached by a path or a
# package manager, each carrying a sentinel that only the overlay holds.
R7_SELF_REFERENCES = {
    "zz-r7-self-cmd": {"command": "/opt/sk-r7-selfref-cmd-v4lue/pmcp"},
    "zz-r7-self-uvx": {
        "command": "uvx",
        "args": ["/opt/sk-r7-selfref-uvx-v4lue/pmcp", "sk-r7-selfref-arg-v4lue"],
    },
    "zz-r7-self-py": {
        "command": "python",
        "args": ["-m", "pmcp", "sk-r7-selfref-py-v4lue"],
    },
    # codex F002's own case: a shipped name, replaced, inherited.
    "puppeteer": {"command": "pmcp", "args": ["sk-review-overlay-only-61d"]},
}
R7_SELF_SENTINELS = [
    "sk-r7-selfref-cmd-v4lue",
    "sk-r7-selfref-uvx-v4lue",
    "sk-r7-selfref-arg-v4lue",
    "sk-r7-selfref-py-v4lue",
    "sk-review-overlay-only-61d",
]


def _r7_overlay(field_name: str | None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "description": "r7 walk entry",
        "keywords": ["zzr7kw", "screenshot"],
        "command": "npx",
        "args": ["-y", "zz-r7@1.0.0"],
        "env_var": "ZZ_R7_CRED_TOKEN",
    }
    servers: dict[str, Any] = {}
    for name in ("zz-r7-local", "github"):  # overlay-only, and a shipped name
        servers[name] = dict(entry)
    servers["zz-r7-remote"] = {
        "description": "r7 walk remote",
        "keywords": ["zzr7kw"],
        "url": "https://example.invalid/r7",
    }
    for name, self_ref in R7_SELF_REFERENCES.items():
        servers[name] = {**entry, **self_ref}
    if field_name is not None:
        value = _r7_value(field_name)
        for name, server in servers.items():
            if name in R7_SELF_REFERENCES and field_name in ("command", "args"):
                continue  # keep the self-reference itself
            server[field_name] = value
    return {"servers": servers}


def _r7_drive(tools: GatewayTools, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every load and discovery path, as the gateway runs them; the response
    bodies, serialised, are returned for the response check."""
    from pmcp.client.manager import ClientManager
    from pmcp.config.loader import startup_skip_message

    responses: list[str] = []
    manifest = load_manifest()
    configs = load_configs()
    # Gateway startup's first two calls (server.initialize), then its resolution.
    configs = filter_self_references(configs)
    resolution = resolve_startup_configs(
        configs,
        manifest_servers=manifest.servers,
        enabled_auto_start=set(manifest.servers),
        is_auth_available=lambda _key: False,
    )
    for skipped in resolution.skipped:
        logging.getLogger("pmcp.server").info(startup_skip_message("startup", skipped))
    for query in ("zzr7kw", "screenshot", "github", "puppeteer", "zz r7 local"):
        for include_offline in (True, False):
            responses.append(
                asyncio.run(
                    tools.catalog_search(
                        {"query": query, "include_offline": include_offline}
                    )
                ).model_dump_json()
            )
        responses.append(
            asyncio.run(tools.request_capability({"query": query})).model_dump_json()
        )
        _keyword_match(query, manifest, set())
    assert asyncio.run(tools.refresh({"reason": "r7 walk"})).ok is True
    lazy = resolve_startup_configs(configs, manifest_servers=manifest.servers)
    ClientManager().register_lazy_configs(lazy.lazy_configs)
    _extract_required_keys(Path.cwd())
    return responses


# The fields a discovery response is FOR: what a candidate copies
# (`manifest_candidate_fields`), its credential metadata, and the keywords the
# configured tier quotes as its reason. A response may carry these; never
# another field's value.
R7_RESPONSE_FIELDS = {
    "description",
    "transport",
    "url",
    "package",
    "server_card_url",
    "declared_scopes",
    "declared_capabilities",
    "env_var",
    "env_instructions",
    "keywords",
}


def test_the_response_fields_are_the_candidate_projection() -> None:
    import inspect

    source = inspect.getsource(loader.manifest_candidate_fields)
    read = set(re.findall(r"server\.(\w+)", source))
    assert read <= R7_RESPONSE_FIELDS
    assert R7_RESPONSE_FIELDS - read == {"env_var", "env_instructions", "keywords"}


@pytest.mark.parametrize("field_name", [None, *R7_FIELDS])
def test_no_inherited_overlay_value_reaches_any_diagnostic(
    field_name: str | None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 6 codex F002, as a class: every server field (each shape it can
    keep) in overlay entries a command-less `.mcp.json` entry inherits from --
    local and self-referencing, overlay-only and shipped names, plus a remote
    entry -- driven through load, startup, discovery, refresh and lazy
    registration at DEBUG. No log record or Python warning carries a value an
    overlay holds; no response carries one outside the response fields."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(_r7_overlay(field_name)),
    )
    partial = {name: {"args": []} for name in _r7_overlay(None)["servers"]}
    partial.pop("zz-r7-remote")
    partial["healthy"] = {"command": "healthy-command"}
    _write(Path.home() / ".mcp.json", json.dumps({"mcpServers": partial}))
    # The credential configured-default inheritance injects into `env`.
    monkeypatch.setenv("ZZ_R7_CRED_TOKEN", R7_CRED)
    if field_name in ("env_var", "secret_key"):
        monkeypatch.setenv(_r7_value(field_name), R7_CRED)

    manifest = load_manifest()
    kept = {"zz-r7-local", "github", "zz-r7-remote", *R7_SELF_REFERENCES}
    assert kept <= set(manifest.servers), sorted(kept - set(manifest.servers))
    sentinels = [*R7_SELF_SENTINELS, R7_CRED]
    if field_name is not None:
        sentinels.append(_r7_sentinel(field_name))
    tools = _refresh_tools(monkeypatch)
    with warnings.catch_warnings(record=True) as caught, caplog.at_level(logging.DEBUG):
        warnings.simplefilter("always")
        responses = _r7_drive(tools, monkeypatch)
    inherited = {c.name: c for c in load_configs()}
    assert "healthy" in inherited
    # The walk exercises the credential injection it checks for.
    assert R7_CRED in (inherited["zz-r7-local"].config.env or {}).values()
    text = (caplog.text + "\n".join(str(w.message) for w in caught)).lower()
    assert [s for s in sentinels if s.lower() in text] == []
    assert any("recursive spawning" in r.getMessage() for r in caplog.records)
    body = "\n".join(responses).lower()
    leaked = [s for s in sentinels if s.lower() in body]
    if field_name in R7_RESPONSE_FIELDS:
        leaked = [s for s in leaked if s != _r7_sentinel(field_name)]
    assert leaked == []


def _config_field_diagnostics() -> set[tuple[str, str]]:
    """Every diagnostic in src/pmcp that renders a config field's value, derived.

    A diagnostic is a logger/logging call, ``warnings.warn`` or a raised
    exception's arguments. It renders a field when an argument reads an
    attribute, a subscript key, a ``getattr``/``.get`` name that is a field of
    ``ServerConfig``, ``CLIAlternative``, ``LocalMcpServerConfig`` or
    ``RemoteMcpServerConfig`` -- directly, through a local assigned from one (a
    fixpoint over assignments and loop targets), or through a parameter named
    like a field. Returns (path, function) pairs.
    """
    import ast

    from pmcp.types import LocalMcpServerConfig, RemoteMcpServerConfig

    fields = {f.name for f in dataclasses.fields(ServerConfig)}
    fields |= {f.name for f in dataclasses.fields(CLIAlternative)}
    fields |= set(LocalMcpServerConfig.model_fields)
    fields |= set(RemoteMcpServerConfig.model_fields)
    # `name` is D9's label rule (entry_log_name); `type` and `description` are
    # a Literal and the response text discovery exists to return.
    fields -= {"name", "type", "description"}
    levels = {
        "debug",
        "info",
        "warning",
        "warn",
        "error",
        "exception",
        "critical",
        "log",
    }

    def reads(node: ast.AST, tainted: set[str]) -> bool:
        called = {id(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)}
        for n in ast.walk(node):
            if (
                isinstance(n, ast.Attribute)
                and n.attr in fields
                and id(n) not in called
            ):
                return True
            if (
                isinstance(n, ast.Subscript)
                and isinstance(n.slice, ast.Constant)
                and n.slice.value in fields
            ):
                return True
            if (
                isinstance(n, ast.Call)
                and n.args[1:2]
                and isinstance(n.args[1], ast.Constant)
                and n.args[1].value in fields
                and isinstance(n.func, ast.Name)
                and n.func.id == "getattr"
            ):
                return True
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get"
                and n.args
                and isinstance(n.args[0], ast.Constant)
                and n.args[0].value in fields
            ):
                return True
            if isinstance(n, ast.Name) and n.id in tainted:
                return True
        return False

    def names(target: ast.AST) -> list[str]:
        return [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]

    root = Path(loader.__file__).resolve().parents[1]
    sites: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs
            tainted = {a.arg for a in params if a.arg in fields}
            changed = True
            while changed:
                changed = False
                for n in ast.walk(fn):
                    if isinstance(n, ast.Assign):
                        source, targets = n.value, n.targets
                    elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and n.value:
                        source, targets = n.value, [n.target]
                    elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
                        source, targets = n.iter, [n.target]
                    else:
                        continue
                    if reads(source, tainted):
                        new = {x for t in targets for x in names(t)} - tainted
                        if new:
                            tainted |= new
                            changed = True
            for n in ast.walk(fn):
                args: list[ast.AST] = []
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                    owner = n.func.value
                    if (
                        n.func.attr in levels
                        and isinstance(owner, ast.Name)
                        and owner.id in {"logger", "log", "logging", "_logger"}
                    ) or (
                        n.func.attr == "warn"
                        and isinstance(owner, ast.Name)
                        and owner.id == "warnings"
                    ):
                        args = [*n.args, *(k.value for k in n.keywords)]
                elif isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call):
                    args = list(n.exc.args)
                if any(reads(a, tainted) for a in args):
                    sites.add((path.relative_to(root).as_posix(), fn.name))
    return sites


# Every function the derivation finds, and why it is outside D9's paths (load,
# discovery, CLI probing, startup and refresh resolution, lazy registration).
# Fail-closed: a NEW site anywhere fails the test until it is made value-free or
# placed here with its reason.
_LIFECYCLE = "lifecycle: from the first start attempt (D9 out of scope)"
_PROVISION = (
    "provisioning, install or update: an explicit operator action (D9 out of scope)"
)
_REFRESHER = "`pmcp refresh` (the descriptions refresher; D9 out of scope)"
_NOT_A_FIELD = (
    "not a config field: a count, an HTTP status, a request URL or header, or an "
    "argparse namespace that happens to be named `args`"
)
_NOT_MANIFEST = "a discovered or registry package, not manifest input"
_PARSE_SOURCE = (
    "`source` is the label pmcp chose for the parsed text (a path or a fixed "
    "description), not a config's `source` field (Consiliency/pmcp#297)"
)
DIAGNOSTIC_SITES_OUTSIDE_D9 = {
    ("auth.py", "redirect_request"): _NOT_A_FIELD,
    ("feedback_egress.py", "redirect_request"): _NOT_A_FIELD,
    ("manifest/package_identity.py", "redirect_request"): _NOT_A_FIELD,
    ("transport/http.py", "create_http_app"): _NOT_A_FIELD,
    ("transport/http.py", "handle_mcp"): _NOT_A_FIELD,
    ("manifest/loader.py", "_build_manifest"): _NOT_A_FIELD,
    ("server.py", "initialize"): _NOT_A_FIELD,
    ("tools/handlers.py", "provision_status"): _NOT_A_FIELD,
    ("manifest/version_checker.py", "get_npm_version"): _NOT_A_FIELD,
    ("manifest/version_checker.py", "get_pypi_version"): _NOT_A_FIELD,
    ("manifest/version_checker.py", "get_cargo_version"): _NOT_A_FIELD,
    ("manifest/version_checker.py", "get_docker_version"): _NOT_A_FIELD,
    # Consiliency/pmcp#376 (merged from main) moved `call_tool`'s
    # connection-status line here.
    ("client/manager.py", "call_tool_with_task"): _NOT_A_FIELD,
    ("client/manager.py", "read_resource"): _NOT_A_FIELD,
    ("client/manager.py", "get_prompt"): _NOT_A_FIELD,
    ("client/manager.py", "_remote_headers"): _LIFECYCLE,
    ("client/manager.py", "_connect_stdio"): _LIFECYCLE,
    ("cli.py", "run_status"): _NOT_A_FIELD,
    ("manifest/installer.py", "start_install"): _PROVISION,
    ("manifest/installer.py", "install_server"): _PROVISION,
    ("manifest/installer.py", "verify_installation"): _PROVISION,
    ("manifest/installer.py", "_monitor_install"): _PROVISION,
    ("manifest/version_checker.py", "_npm_package_arg"): _PROVISION,
    ("tools/handlers.py", "_run_update_probe_command"): _PROVISION,
    ("manifest/refresher.py", "refresh_server"): _REFRESHER,
    ("manifest/refresher.py", "refresh_all"): _REFRESHER,
    ("manifest/refresher.py", "refresh_target"): _REFRESHER,
    ("manifest/refresher.py", "check_staleness"): _REFRESHER,
    ("tools/handlers.py", "register_discovered_server"): _NOT_MANIFEST,
    ("package_approvals.py", "_require_identity_fields"): _NOT_MANIFEST,
    ("cli.py", "_run_trust_revoke_package"): _NOT_A_FIELD,
    ("parsing.py", "load_yaml"): _PARSE_SOURCE,
    ("parsing.py", "load_json"): _PARSE_SOURCE,
    ("parsing.py", "parse_timestamp"): _PARSE_SOURCE,
}


def test_no_diagnostic_on_these_paths_reads_a_config_field() -> None:
    """Round 6 codex F002, derived: `filter_self_references` and
    `is_self_reference` printed `command` and `args`, which a `.mcp.json` entry
    can inherit from an overlay. Every diagnostic that reads a config field is
    now either outside D9's paths, with its reason, or gone."""
    sites = _config_field_diagnostics()
    assert sites - set(DIAGNOSTIC_SITES_OUTSIDE_D9) == set()
    # The table holds no stale row (each still exists), so it cannot grow
    # into a list of names nobody re-checks.
    assert set(DIAGNOSTIC_SITES_OUTSIDE_D9) - sites == set()
    assert not any(path == "identity.py" for path, _ in sites)


def test_codex_r6_f002_falsifier_an_inherited_argument_reaches_no_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 6, codex F002, verbatim in substance: a partial `.mcp.json` entry
    inherits `command: pmcp` and an argument only the overlay holds."""
    from pmcp.config.loader import (
        _credential_value_for,
        _merge_manifest_defaults,
        parse_config_bytes,
    )
    from pmcp.types import ResolvedServerConfig

    secret = "sk-review-overlay-only-61d"
    document = (
        f"servers:\n  puppeteer:\n    command: pmcp\n    args: [{secret}]\n"
    ).encode()
    servers, _, _, _ = loader._parse_overlay_document(Path("overlay.yaml"), document)
    assert "puppeteer" in servers
    configured = parse_config_bytes(
        b'{"mcpServers":{"puppeteer":{"args":[]}}}', Path("user.mcp.json")
    )
    assert configured is not None
    partial = configured.mcpServers["puppeteer"]
    assert secret not in repr(partial)
    inherited = _merge_manifest_defaults(
        "puppeteer", partial, servers, _credential_value_for(None)
    )  # type: ignore[arg-type]
    assert inherited is not None and secret in inherited.args
    resolved = ResolvedServerConfig(name="puppeteer", source="user", config=inherited)
    with caplog.at_level(logging.DEBUG):
        assert filter_self_references([resolved]) == []
    assert secret not in caplog.text
    assert "'puppeteer' (field 'command')" in caplog.text


def test_the_field_diagnostic_names_a_field_never_a_value() -> None:
    """`config_field_diagnostic` is the one renderer: a `.mcp.json` entry by
    its name, a manifest-derived one only if pmcp ships the name, and the
    field by its name."""
    from pmcp.identity import config_field_diagnostic
    from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

    def config(name: str, source: Any) -> ResolvedServerConfig:
        return ResolvedServerConfig(
            name=name,
            source=source,
            config=LocalMcpServerConfig(command="pmcp", args=[VALUE_SENTINEL]),
        )

    assert (
        config_field_diagnostic(config("mine", "user"), "args")
        == "server 'mine' (field 'args')"
    )
    overlay_only = config_field_diagnostic(config(NAME_SENTINEL, "manifest"), "command")
    assert NAME_SENTINEL not in overlay_only and "field 'command'" in overlay_only
    assert "'github'" in config_field_diagnostic(config("github", "manifest"), "args")
    assert VALUE_SENTINEL not in config_field_diagnostic(config("x", "user"), "args")


# --- the docs' field claims, generated and held to the code -----------------------
# (Consiliency/pmcp#375 board rounds 1 and 2: the docs drifted from the code
# twice.) DOC_CLAIMS is the only statement of what README, CHANGELOG and
# MIGRATING.md say about which overlay fields are skipped or accepted. Each
# claim quotes the doc text it supports and lists the rows that prove it:
# (kind, field, YAML value, base) with base `local` (no url), `url` or `cli`.
# `branch` is the expected outcome here; `main` is the expected outcome on
# main, checked by `python tests/overlay_field_table.py --claims main` with
# PYTHONPATH at main's src (it cannot run in this tree). The behaviour table
# itself (every field x every shape x both bases) is `overlay_field_table`.
# DOC_CLAIMS, _ANY and _NOT_A_STRING use no pmcp import, so that script can run
# them against another tree.

_ANY = [
    "null",
    "5",
    "true",
    "1.5",
    '"zz-text"',
    '["a", "b"]',
    "[1]",
    "{a: b}",
    "2026-10-04",
    "2026-10-04T12:00:00Z",
    "!!binary aGVsbG8=",
    "!!set {a: null, b: null}",
    "[{a: [1]}]",
]  # noqa: E501 -- mirrors overlay_field_table.SHAPES; a test checks they agree
_NOT_A_STRING = [v for v in _ANY if v not in ("null", '"zz-text"')]

DOC_CLAIMS: list[dict[str, Any]] = [
    {
        "id": "keywords-number",
        "docs": [
            ("README.md", "such as `keywords: [1]`"),
            ("CHANGELOG.md", "(`keywords: [1]`, an `args` list"),
            ("MIGRATING.md", "`keywords: [1]`, a number in `args` or `command`"),
        ],
        "mentions": ["keywords"],
        "rows": [("server", "keywords", "[1]", b) for b in ("local", "url")]
        + [("server", "keywords", '["a", 1]', b) for b in ("local", "url")],
        "branch": "skipped",
        "main": "loaded",
    },
    {
        "id": "args-number",
        "docs": [
            ("README.md", "an `args` list holding a number"),
            ("CHANGELOG.md", "an `args` list holding a\n  number"),
            ("MIGRATING.md", "a number in `args` or `command`"),
        ],
        "mentions": ["args"],
        "rows": [
            ("server", "args", v, b)
            for v in ("[1]", '["-y", 5]', '["-y", 1.5]')
            for b in ("local", "url")
        ],
        "branch": "skipped",
        "main": "loaded",
    },
    {
        "id": "command-not-a-string",
        "docs": [
            ("README.md", "a `command` or `transport` that is not a\nstring"),
            ("CHANGELOG.md", "a `command` or `transport` that is not a string"),
            ("MIGRATING.md", "a number in `args` or `command`"),
        ],
        "mentions": ["command"],
        "rows": [
            ("server", "command", v, b) for v in _NOT_A_STRING for b in ("local", "url")
        ],
        "branch": "skipped",
        "main": "loaded",
    },
    {
        "id": "transport-not-a-string",
        "docs": [
            ("README.md", "a `command` or `transport` that is not a\nstring"),
            ("CHANGELOG.md", "a `command` or `transport` that is not a string"),
            ("MIGRATING.md", "a `transport` that is not a\nstring"),
        ],
        "mentions": ["transport"],
        "rows": [
            ("server", "transport", v, b)
            for v in _NOT_A_STRING
            for b in ("local", "url")
        ],
        "branch": "skipped",
        "main": "loaded",
    },
    {
        "id": "check-command-empty",
        "docs": [
            ("README.md", "a CLI alternative with an empty `check_command`"),
            (
                "CHANGELOG.md",
                "a `cli_alternatives`\n  entry with an empty `check_command`",
            ),
            (
                "MIGRATING.md",
                "a `cli_alternatives` entry with an empty `check_command`",
            ),
        ],
        "mentions": ["check_command"],
        "rows": [("cli", "check_command", "[]", "cli")],
        "branch": "skipped",
        "main": "loaded",
    },
    {
        "id": "stored-or-truth-only-any-value",
        "docs": [
            (
                "README.md",
                "Fields pmcp stores\nbut never reads (`status`), or reads only as true or false (`auto_start`,\n`requires_api_key`), are accepted whatever their value, as before.",
            ),
        ],
        "mentions": ["status", "auto_start", "requires_api_key"],
        "rows": [
            ("server", f, v, b)
            for f in ("status", "auto_start", "requires_api_key")
            for v in _ANY
            for b in ("local", "url")
        ],
        "branch": "loaded",
        "main": "loaded",
    },
    {
        "id": "transport-string-without-url",
        "docs": [
            (
                "README.md",
                "A\n`transport` string without a `url` is accepted as before, whatever the string;",
            ),
        ],
        "mentions": ["transport", "url"],
        "rows": [
            ("server", "transport", v, "local")
            for v in ("stdio", '"zz-text"', "sse", "local", '""')
        ],
        "branch": "loaded",
        "main": "loaded",
    },
    {
        "id": "transport-with-url-remote-only-accepted",
        "docs": [
            (
                "README.md",
                "with a `url` it must be one of pmcp's transport names (`local`, `remote`,\n`sse`, `http`, `streamable-http`).",
            ),
        ],
        "mentions": ["transport", "url"],
        "rows": [
            ("server", "transport", v, "url")
            for v in ("local", "remote", "sse", "http", "streamable-http")
        ],
        "branch": "loaded",
        "main": "loaded",
    },
    {
        "id": "transport-with-url-other-string-skipped",
        "docs": [
            ("README.md", "with a `url` it must be one of pmcp's transport names"),
        ],
        "mentions": ["transport", "url"],
        "rows": [
            ("server", "transport", v, "url") for v in ("stdio", '"zz-text"', '""')
        ],
        "branch": "skipped",
        "main": "loaded",
    },
    {
        "id": "blank-means-not-set",
        "docs": [
            (
                "README.md",
                'A blank field\n(`description:` with nothing after it) means "not set".',
            ),
            (
                "MIGRATING.md",
                'A blank field (`description:` with nothing\nafter it) means "not set" and takes its default.',
            ),
        ],
        "mentions": ["description"],
        "rows": [("server", "description", "null", b) for b in ("local", "url")],
        "branch": "loaded ''",
        "main": "loaded",
    },
]

DOC_CLAIMS += [
    {
        # The warning-text column: every one of these is also checked for
        # every skipped row of the table by
        # test_every_skip_warning_names_its_field.
        "id": "warning-names-the-field-not-the-value",
        "docs": [
            ("README.md", "The warning names the field, not\nits value"),
            ("CHANGELOG.md", "with a WARNING naming the field but not its value"),
            ("CHANGELOG.md", "The warning names the field, never its value"),
            ("MIGRATING.md", "WARNING that names the field but not its value"),
        ],
        "mentions": [],
        "rows": [
            ("server", "keywords", "[1]", "local"),
        ],
        "branch": "skipped [name not shown] 'keywords' ",
        "main": "loaded",
    },
    {
        "id": "warning-args",
        "docs": [("README.md", "an `args` list holding a number")],
        "mentions": [],
        "rows": [("server", "args", "[1]", b) for b in ("local", "url")],
        "branch": "skipped [name not shown] 'args' ",
        "main": "loaded",
    },
    {
        "id": "warning-command",
        "docs": [("README.md", "a `command` or `transport` that is not a\nstring")],
        "mentions": [],
        "rows": [("server", "command", "5", b) for b in ("local", "url")],
        "branch": "skipped [name not shown] 'command' ",
        "main": "loaded",
    },
    {
        "id": "warning-transport",
        "docs": [("README.md", "a `command` or `transport` that is not a\nstring")],
        "mentions": [],
        "rows": [("server", "transport", "5", b) for b in ("local", "url")],
        "branch": "skipped [name not shown] 'transport' ",
        "main": "loaded",
    },
    {
        "id": "warning-check-command",
        "docs": [("README.md", "a CLI alternative with an empty `check_command`")],
        "mentions": [],
        "rows": [("cli", "check_command", "[]", "cli")],
        "branch": "skipped [name not shown] 'check_command' ",
        "main": "loaded",
    },
    {
        "id": "warning-shows-only-a-shipped-name",
        "docs": [
            ("README.md", "does not show the entry's name unless pmcp ships it"),
            ("MIGRATING.md", "and not the entry's name\nunless pmcp ships it"),
            (
                "CHANGELOG.md",
                "shows an overlay entry's\n  name only when pmcp ships that name",
            ),
        ],
        "mentions": [],
        "rows": [
            ("server", "keywords", "[1]", "local", "playwright"),
            ("server", "command", "5", "url", "github"),
            ("cli", "check_command", "[]", "cli", "git"),
        ],
        "branch": "skipped [shipped name shown] ",
        "main": "loaded",
    },
]


@pytest.fixture(scope="module")
def overlay_table(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[list[tuple[Any, ...]], list[tuple[str, str]]]:
    """The generated behaviour table, built once for the module, with every
    record any logger emitted during an attribution pass while building it
    (plus the round-3 falsifier entries). Each row loads in its own HOME, so
    sharing the build changes nothing a row observes."""
    from pmcp.manifest.attribution import ATTRIBUTION_PROBE
    from tests.overlay_field_table import generate, observe

    home_root = tmp_path_factory.mktemp("overlay-table")
    during: list[tuple[str, str]] = []
    seen = [0]

    class _Watch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen[0] += 1
            if ATTRIBUTION_PROBE.get():
                during.append((record.name, record.getMessage()[:80]))

    # On the root AND on `pmcp`: `observe` stops pmcp records from
    # propagating past `pmcp` while it collects a row's warnings.
    handler = _Watch(level=logging.DEBUG)
    root, pmcp_log = logging.getLogger(), logging.getLogger("pmcp")
    saved = root.level, pmcp_log.level
    root.addHandler(handler)
    pmcp_log.addHandler(handler)
    root.setLevel(logging.DEBUG)
    pmcp_log.setLevel(logging.DEBUG)
    try:
        rows = generate(home_root)
        for extra in (
            "version: latest\n    url: 5",
            "env_var: ZZ_KEY\n    api_key_optional_when: [ZZ_KEY]\n    url: 5",
            "extra_env: 5\n    keywords: [1]",
        ):
            observe("server", "description", '"d"\n    ' + extra, "local", home_root)
    finally:
        root.removeHandler(handler)
        pmcp_log.removeHandler(handler)
        root.setLevel(saved[0])
        pmcp_log.setLevel(saved[1])
    assert seen[0] > 1000  # the watch saw the loader's records: not vacuous
    return rows, during


# The doc passages whose field mentions DOC_CLAIMS must cover: (file, first
# words, last words) of each passage.
DOC_PASSAGES = [
    (
        "README.md",
        "Overlay loading is **fail-soft**",
        "the shipped manifest always still loads.",
    ),
    ("CHANGELOG.md", "- **An overlay entry pmcp cannot use is skipped.**", "*Fixed*"),
    (
        "CHANGELOG.md",
        "- **An overlay no longer hides other servers from discovery",
        "(https://github.com/Consiliency/pmcp/issues/342).",
    ),
    (
        "MIGRATING.md",
        "### An overlay entry pmcp cannot use is skipped",
        "**What to do.**",
    ),
]


def _repo_file(name: str) -> str:
    root = Path(loader.__file__).resolve().parents[3]
    return (root / name).read_text(encoding="utf-8")


def _passage(name: str, start: str, end: str) -> str:
    text = _repo_file(name)
    i = text.index(start)
    return text[i : text.index(end, i) + len(end)]


def test_doc_claims_quote_the_docs() -> None:
    """Every claim's quoted text is in the doc, inside a covered passage."""
    passages: dict[str, str] = {}
    for f, a, b in DOC_PASSAGES:
        passages[f] = passages.get(f, "") + _passage(f, a, b)
    missing = [
        (c["id"], f, q[:50])
        for c in DOC_CLAIMS
        for f, q in c["docs"]
        if q not in passages[f]
    ]
    assert missing == []


def test_every_field_the_docs_name_has_a_claim() -> None:
    """The prose says only what DOC_CLAIMS says: every schema field named in
    backticks in a covered passage is a claim's `mentions`."""
    fields = set(loader._SERVER_SCHEMA_KEYS) | set(loader._CLI_SCHEMA_KEYS)
    covered = {m for c in DOC_CLAIMS for m in c["mentions"]}
    named = set()
    for f, a, b in DOC_PASSAGES:
        for token in re.findall(r"`([a-z_]+)(?::[^`]*)?`", _passage(f, a, b)):
            if token in fields:
                named.add(token)
    assert named - covered == set()
    assert covered - named <= {"url"}  # `url` is named as the condition


def test_every_skip_warning_names_its_field(
    overlay_table: tuple[list[tuple[Any, ...]], list[tuple[str, str]]],
) -> None:
    """The warning-text column, for every row of the generated table: a skipped
    entry's WARNING names the field the row set (grok F001: `keywords: [1]`
    said `AttributeError while parsing`), only field names and error kinds,
    never `while parsing` or an unattributed reason, and no unshipped name."""
    rows, _during = overlay_table
    reason = re.compile(r"^'[a-z_]+'(, '[a-z_]+')* [A-Za-z_]+(, [A-Za-z_]+)*$")
    not_json = re.compile(r"^'[a-z_]+' holds a value that is not JSON$")
    wrong = []
    for kind, field_name, shape, base, got, _count, _repeats in rows:
        if not got.startswith("skipped"):
            continue
        prefix = "skipped [name not shown] "
        text = got[len(prefix) :]
        if (
            not got.startswith(prefix)
            or f"'{field_name}'" not in text
            or not (reason.match(text) or not_json.match(text))
        ):
            wrong.append((kind, field_name, shape, base, got))
    assert wrong == []
    assert sum(r[4].startswith("skipped") for r in rows) > 400  # not vacuous


def test_the_claim_shapes_are_the_table_shapes() -> None:
    from tests.overlay_field_table import SHAPES

    assert _ANY == [value for _shape, value in SHAPES]


@pytest.mark.parametrize("claim_id", [c["id"] for c in DOC_CLAIMS])
def test_every_doc_claim_holds(claim_id: str, tmp_path: Path) -> None:
    from tests.overlay_field_table import check_claims

    claim = next(c for c in DOC_CLAIMS if c["id"] == claim_id)
    assert check_claims([claim], tmp_path, "branch") == []


def test_the_behaviour_table_covers_every_field_and_loads_the_shipped_manifest(
    overlay_table: tuple[list[tuple[Any, ...]], list[tuple[str, str]]],
) -> None:
    """The generated table: every field x shape x base, and no row raises."""
    from tests.overlay_field_table import SERVER_BASES, SHAPES

    rows, _during = overlay_table
    expected = (len(SERVER_FIELDS) * len(SERVER_BASES) + len(CLI_FIELDS)) * len(SHAPES)
    assert len(rows) == expected
    assert all(r[4].startswith(("skipped [", "loaded ")) for r in rows)


def test_a_yaml_typed_truth_only_field_keeps_mains_meaning() -> None:
    """Round 2, claude F002: `auto_start: 2026-10-04` skipped the entry, where
    main loads it and auto-starts it (a date is true). It loads as `True`."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        "servers:\n  zz:\n    command: npx\n    keywords: [zz]\n"
        "    auto_start: 2026-10-04\n    requires_api_key: !!binary aGVsbG8=\n",
    )
    server = load_manifest().servers["zz"]
    assert server.auto_start is True and server.requires_api_key is True


def test_the_truth_only_keys_are_derived_from_every_read() -> None:
    """`_TRUTH_ONLY_KEYS` is exactly the schema fields whose every attribute
    read in src/pmcp is a truth test (`if`, `not`, `and`/`or`, a comprehension
    filter, `bool(...)`). A read of another kind removes a field from it."""
    import ast

    root = Path(loader.__file__).resolve().parents[1]
    fields = set(loader._SERVER_SCHEMA_KEYS) - set(loader._STORED_ONLY_KEYS)
    reads: dict[str, list[bool]] = {f: [] for f in fields}

    def truth(node: ast.AST, parents: dict[int, ast.AST]) -> bool:
        parent = parents.get(id(node))
        if isinstance(parent, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            return parent.test is node
        if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
            return True
        if isinstance(parent, ast.BoolOp):
            return truth(parent, parents)
        if isinstance(parent, ast.comprehension):
            return node in parent.ifs
        if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name):
            return parent.func.id == "bool" and parent.args[:1] == [node]
        return False

    for path in root.rglob("*.py"):
        if "baml_client" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in fields
                and isinstance(node.ctx, ast.Load)
            ):
                reads[node.attr].append(truth(node, parents))
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and node.args[1:2]
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in fields
            ):
                reads[node.args[1].value].append(truth(node, parents))
    derived = {f for f, r in reads.items() if r and all(r)}
    assert derived == set(loader._TRUTH_ONLY_KEYS)


def test_claude_375_f001_a_non_string_transport_without_a_url_is_skipped() -> None:
    """Round 1's falsifier, with the corrected claim: `transport: 5` and no
    `url` is skipped (main loaded it, then failed every query that matched it);
    a string `transport` without a `url` still loads."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "zz": {"command": "npx", "keywords": ["zz"], "transport": 5},
                    "zzs": {"command": "npx", "keywords": ["zz"], "transport": "stdio"},
                }
            }
        ),
    )
    servers = load_manifest().servers
    assert "zz" not in servers
    assert "zzs" in servers


def test_grok_375_f001_falsifier_the_keywords_example_names_keywords(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Round 2, grok F001: the docs' own example, `keywords: [1]`, logged
    `AttributeError while parsing` with no field named."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {
                "servers": {
                    "zz": {"command": "npx", "keywords": [1], "args": ["-y", "pkg"]}
                }
            }
        ),
    )
    with caplog.at_level(logging.DEBUG):
        servers = load_manifest().servers
    assert "zz" not in servers and "playwright" in servers
    skips = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("Skipping invalid")
    ]
    assert len(skips) == 1
    assert skips[0].endswith(": 'keywords' AttributeError")
    assert "while parsing" not in skips[0]


def test_every_row_logs_each_warning_once(
    overlay_table: tuple[list[tuple[Any, ...]], list[tuple[str, str]]],
) -> None:
    """The warning-count column (round 3, claude F001): no generated row logs
    any WARNING more than once. `_checked_entry` re-runs an entry's check to
    find the bad field; those passes log nothing."""
    rows, _during = overlay_table
    assert [r[:5] for r in rows if r[6] > 1] == []
    assert sum(1 for r in rows if r[5]) > 50  # not vacuous: rows that warn


@pytest.mark.parametrize(
    "entry,marker",
    [
        ({"version": "latest", "url": 5}, "'version' pin"),
        (
            {"env_var": "ZZ_KEY", "api_key_optional_when": ["ZZ_KEY"], "url": 5},
            "cannot relax itself",
        ),
        ({"extra_env": 5, "keywords": [1]}, "'extra_env'"),
    ],
    ids=["version-pin", "self-relax", "extra-env"],
)
def test_claude_375_r3_f001_a_skipped_entry_logs_each_parser_warning_once(
    entry: dict[str, Any], marker: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 3, claude F001: each attribution pass repeated the parser's own
    WARNINGs (3-5 times where main logs one)."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        yaml.safe_dump(
            {"servers": {"zz": {"command": "npx", "keywords": ["zz"], **entry}}}
        ),
    )
    with caplog.at_level(logging.DEBUG):
        servers = load_manifest().servers
    assert "zz" not in servers
    messages = [r.getMessage() for r in caplog.records]
    assert sum(marker in m for m in messages) == 1, messages
    assert sum(m.startswith("Skipping invalid") for m in messages) == 1


def test_attribution_passes_do_not_silence_another_context() -> None:
    """The silence is scoped to the attribution pass's own context."""
    import contextvars

    from pmcp.manifest.attribution import attribution_pass, quiet_during_attribution

    seen: list[str] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    handler = _Keep(level=logging.WARNING)
    base = logging.getLogger("pmcp.manifest.loader")
    base.addHandler(handler)
    log = quiet_during_attribution("pmcp.manifest.loader")
    try:
        with attribution_pass():
            contextvars.Context().run(log.warning, "outside the pass")
            log.warning("inside the pass")
        log.warning("after the pass")
    finally:
        base.removeHandler(handler)
    assert seen == ["outside the pass", "after the pass"]


def test_no_logger_emits_during_an_attribution_pass(
    overlay_table: tuple[list[tuple[Any, ...]], list[tuple[str, str]]],
) -> None:
    """Derived, not listed: across every row of the generated table and the
    round-3 falsifier entries, no record from ANY logger is emitted while an
    attribution pass runs. A module the check reaches that logs through a
    plain logger fails here."""
    _rows, during = overlay_table
    assert during == []
