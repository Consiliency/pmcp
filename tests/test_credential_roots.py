"""A credential lookup answers from its own project root only, and sees rotation.

Board round 8 on Consiliency/pmcp#372, claude F001: one shared map, first value
wins, meant a lookup for project B made by a process started in project A
answered a key both define with A's value -- a gateway run with ``--project B``
from A filled B's ``${VAR}`` headers with A's credential and sent it to B's host.
Keys only A defines were answered for B as well (on main too). N-1: a rotated
``.env.pmcp`` value stayed old until restart.

The repository part of the lookup is now keyed by project root, built per root
by one builder that records each source file's identity, and rebuilt by the same
builder when a file changed.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import itertools
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pmcp import cli, env_store
from pmcp.cli_commands import secrets
from pmcp.remote_auth import (
    build_remote_header_env_lookup,
    resolve_remote_headers_for_tenant,
)


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    base = Path(os.path.realpath(tmp_path))
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    a = base / "a"
    (a / ".git").mkdir(parents=True)
    b = base / "b"
    (b / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(a)
    return {"home": home, "a": a, "b": b, "base": base}


def _startup(roots: dict[str, Path]) -> None:
    cli.load_startup_env(dotenv_path=str(roots["base"] / "no-such.env"))


def test_the_boards_falsifier(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Board 372 r8 F001, in substance."""
    (roots["a"] / ".env.pmcp").write_text("F001_SHARED=from-a\n")
    (roots["b"] / ".env.pmcp").write_text("F001_SHARED=from-b\n")
    monkeypatch.delenv("F001_SHARED", raising=False)
    _startup(roots)
    assert build_remote_header_env_lookup(roots["b"])("F001_SHARED") == "from-b"


KEYS = {"ONLY_A_TOKEN": ("a",), "ONLY_B_TOKEN": ("b",), "BOTH_TOKEN": ("a", "b")}
GRID = list(itertools.product(KEYS, ("a", "b"), (".env", ".env.pmcp")))


@pytest.mark.parametrize(
    ("key", "asked", "file"), GRID, ids=["-".join(c) for c in GRID]
)
def test_each_answer_comes_only_from_its_own_root(
    key: str,
    asked: str,
    file: str,
    roots: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    for root_name in ("a", "b"):
        lines = [
            f"{k}=from-{root_name}\n"
            for k, owners in KEYS.items()
            if root_name in owners
        ]
        (roots[root_name] / file).write_text("".join(lines))
    _startup(roots)  # started in a
    expected = f"from-{asked}" if asked in KEYS[key] else None
    # The runtime for the startup root, and every lookup naming a root.
    if asked == "a":
        assert env_store.credential_value(key) == expected
    assert env_store.credential_lookup(roots[asked])(key) == expected
    assert build_remote_header_env_lookup(roots[asked])(key) == expected
    tenant = resolve_remote_headers_for_tenant(
        {"X": "${%s}" % key},
        server_name="r",
        tenant_id="t",
        project_root=roots[asked],
    )
    assert tenant.resolved_headers["X"] == (expected or "${%s}" % key)


def test_secrets_check_for_another_project_reads_that_project(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "BRAVE_API_KEY"
    monkeypatch.delenv(key, raising=False)
    (roots["a"] / ".env.pmcp").write_text(f"{key}=from-a\n")
    monkeypatch.setattr(
        secrets,
        "_extract_required_keys",
        lambda _: ([key], {"brave-search": [key]}, {}, {}),
    )
    _startup(roots)
    report = asyncio.run(
        secrets.run_secrets_check(argparse.Namespace(project=roots["b"]))
    )
    assert report["missing_keys"] == [key]


def _touch_later(path: Path, text: str) -> None:
    before = os.lstat(path).st_mtime_ns if path.exists() else 0
    path.write_text(text)
    if os.lstat(path).st_mtime_ns == before:  # coarse clocks
        os.utime(path, ns=(before + 1_000_000, before + 1_000_000))


@pytest.mark.parametrize("file", [".env", ".env.pmcp"])
def test_a_rotated_store_is_seen_by_the_next_lookup(
    file: str, roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ROT_TOKEN", raising=False)
    store = roots["a"] / file
    store.write_text("ROT_TOKEN=old\n")
    _startup(roots)
    assert env_store.credential_value("ROT_TOKEN") == "old"
    time.sleep(0.01)
    _touch_later(store, "ROT_TOKEN=rotated\n")
    # The runtime and every diagnostic see the new value, identically.
    assert env_store.credential_value("ROT_TOKEN") == "rotated"
    assert build_remote_header_env_lookup(None)("ROT_TOKEN") == "rotated"
    assert build_remote_header_env_lookup(roots["a"])("ROT_TOKEN") == "rotated"
    store.unlink()
    assert env_store.credential_value("ROT_TOKEN") is None


def test_an_unchanged_store_is_not_rebuilt(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    real = env_store._build_root_entry

    def counting(root: Path):  # type: ignore[no-untyped-def]
        calls.append(root)
        return real(root)

    monkeypatch.setattr(env_store, "_build_root_entry", counting)
    (roots["a"] / ".env.pmcp").write_text("STEADY_TOKEN=x\n")
    _startup(roots)
    built = len(calls)
    for _ in range(5):
        assert env_store.credential_value("STEADY_TOKEN") == "x"
    assert len(calls) == built


@pytest.mark.parametrize("file", [".env", ".env.pmcp"])
def test_the_credential_check_sees_either_project_file_without_a_startup_load(
    file: str, roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check's one project load builds the root's whole entry.

    The handler names no project file list of its own: the builder reads both
    checkout files, so a key in only ``.env`` is found as one in only
    ``.env.pmcp`` is, with no startup load to have filled the map first.
    """
    from pmcp.tools.handlers import GatewayTools

    monkeypatch.delenv("ONE_FILE_TOKEN", raising=False)
    (roots["a"] / file).write_text("ONE_FILE_TOKEN=x\n")
    check = GatewayTools._check_api_key_available
    assert check(GatewayTools.__new__(GatewayTools), "ONE_FILE_TOKEN") is True
    assert "ONE_FILE_TOKEN" not in os.environ


def test_a_file_that_changes_while_it_is_read_is_read_again(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The identity recorded is the one from before the read, if it moved.

    A writer that replaces ``.env.pmcp`` between the builder's read and its
    second look leaves an entry holding the old value. Were the entry to record
    the file's identity after the read, it would look current and the old value
    would be served until the next change.
    """
    monkeypatch.delenv("RACE_TOKEN", raising=False)
    store = roots["a"] / ".env.pmcp"
    store.write_text("RACE_TOKEN=old\n")
    real = env_store._read_confined_text
    raced: list[bool] = []

    def racing(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        text = real(path, *args, **kwargs)
        if path == store and not raced:
            raced.append(True)
            _touch_later(store, "RACE_TOKEN=new\n")
        return text

    monkeypatch.setattr(env_store, "_read_confined_text", racing)
    _startup(roots)
    assert raced
    assert env_store.credential_value("RACE_TOKEN") == "new"


# --------------------------------------------------------------------------- #
# Board round 9 on Consiliency/pmcp#372, claude F001: only the header lookups
# and credential_lookup answered for the project asked about; the install
# child, the provision gate, a manifest server's injected credential, the
# gateway credential check and auth availability answered for the directory the
# gateway started in. A gateway serving B started from inside A spawned B's
# servers with A's credential. Every consumer in the inventory
# (tests/test_store_reader_inventory.py, SERVED_ROOT_CONSUMERS and the rule
# above it) now answers for the project it serves; this grid runs each one with
# the served root B and the working directory A.
# --------------------------------------------------------------------------- #

CONSUMER_KEYS = ("BRAVE_API_KEY", "TAVILY_API_KEY", "ONLY_A_TOKEN", "ONLY_B_TOKEN")


class _Captured(Exception):
    def __init__(self, kwargs: dict[str, object]) -> None:
        self.kwargs = kwargs


def _capture_auth_availability(monkeypatch: pytest.MonkeyPatch, module: object) -> None:
    def capture(*_args: object, **kwargs: object) -> None:
        raise _Captured(kwargs)

    monkeypatch.setattr(module, "resolve_startup_configs", capture)


def _consumers(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> dict[str, object]:
    """Each consumer, as ``project -> what it answers for that project``."""
    from unittest.mock import MagicMock

    from pmcp import server as server_module
    from pmcp.config.loader import load_configs, resolve_startup_configs
    from pmcp.manifest.installer import (
        MissingApiKeyError,
        build_install_child_env,
        check_api_key,
    )
    from pmcp.manifest.loader import load_manifest
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools import handlers
    from pmcp.tools.handlers import GatewayTools

    manifest = load_manifest()
    brave = manifest.get_server("brave-search")
    assert brave is not None and brave.env_var == "BRAVE_API_KEY"

    def install_child(p: Path | None) -> object:
        return build_install_child_env(brave, project_root=p).get("BRAVE_API_KEY")

    def provision_gate(p: Path | None) -> object:
        verdicts = []
        for key in ("ONLY_A_TOKEN", "ONLY_B_TOKEN"):
            server = copy.copy(brave)
            server.env_var = key
            server.secret_key = None
            try:
                asyncio.run(check_api_key(server, p))
                verdicts.append(key)
            except MissingApiKeyError:
                pass
        return verdicts

    def startup_config(p: Path | None) -> object:
        resolution = resolve_startup_configs(
            [],
            manifest_servers=manifest.servers,
            enabled_auto_start={"brave-search"},
            project_root=p,
        )
        configs = resolution.eager_configs + resolution.lazy_configs
        (config,) = [c for c in configs if c.name == "brave-search"]
        return config.config.env.get("BRAVE_API_KEY")

    def configured_server(p: Path | None) -> object:
        configs = load_configs(project_root=p)
        (config,) = [c for c in configs if c.name == "brave-search"]
        return config.config.env.get("BRAVE_API_KEY")

    def credential_check(p: Path | None) -> object:
        tools = GatewayTools.__new__(GatewayTools)
        tools._project_root = p
        return [
            k
            for k in ("ONLY_A_TOKEN", "ONLY_B_TOKEN")
            if tools._check_api_key_available(k)
        ]

    def lifecycle_connect(p: Path | None) -> object:
        # gateway.connect builds a manifest server's config through
        # manifest_server_to_config with the gateway's own project_root
        # (board round 10 N-2): the library case, GatewayTools(project_root=B)
        # in a process whose served root is A, is the explicit mode below.
        # No configured entry, so the config comes from the manifest.
        for name in ("a", "b"):
            (roots[name] / ".mcp.json").unlink()
        tools = GatewayTools(
            client_manager=MagicMock(), policy_manager=PolicyManager(), project_root=p
        )
        resolved, refusal, _lookup = tools._resolve_lifecycle_target(
            "brave-search", action="connect", prior_status="disconnected"
        )
        assert refusal is None and resolved is not None
        return (resolved.config.env or {}).get("BRAVE_API_KEY")

    def _availability(run: object) -> object:
        try:
            run()  # type: ignore[operator]
        except _Captured as captured:
            available = captured.kwargs["is_auth_available"]
            return [k for k in ("ONLY_A_TOKEN", "ONLY_B_TOKEN") if available(k)]  # type: ignore[operator]
        raise AssertionError("resolve_startup_configs was never reached")

    def config_status_availability(p: Path | None) -> object:
        _capture_auth_availability(monkeypatch, handlers)
        tools = GatewayTools(
            client_manager=MagicMock(), policy_manager=PolicyManager(), project_root=p
        )
        return _availability(lambda: asyncio.run(tools.config_status()))

    def gateway_availability(p: Path | None) -> object:
        _capture_auth_availability(monkeypatch, server_module)
        gateway = server_module.GatewayServer(
            project_root=p, cache_dir=roots["base"] / "cache"
        )
        return _availability(lambda: asyncio.run(gateway.initialize()))

    def init(p: Path | None) -> object:
        import contextlib
        import io

        answers = {"tavily": "y"}
        monkeypatch.setattr(
            "builtins.input",
            lambda prompt: next(
                (v for k, v in answers.items() if f"Enable {k} " in prompt), "n"
            ),
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            asyncio.run(cli.run_init(argparse.Namespace(project=p, force=True)))
        return "Found TAVILY_API_KEY" in out.getvalue()

    def secrets_check(p: Path | None) -> object:
        monkeypatch.setattr(
            secrets,
            "_extract_required_keys",
            lambda _: (
                ["ONLY_A_TOKEN", "ONLY_B_TOKEN"],
                {"x": ["ONLY_A_TOKEN", "ONLY_B_TOKEN"]},
                {},
                {},
            ),
        )
        report = asyncio.run(secrets.run_secrets_check(argparse.Namespace(project=p)))
        return sorted(report["missing_keys"])

    def header_lookup(p: Path | None) -> object:
        return build_remote_header_env_lookup(p)("BRAVE_API_KEY")

    def tenant_headers(p: Path | None) -> object:
        return resolve_remote_headers_for_tenant(
            {"X": "${BRAVE_API_KEY}"}, server_name="r", tenant_id="t", project_root=p
        ).resolved_headers["X"]

    def lookup(p: Path | None) -> object:
        return env_store.credential_lookup(p)("BRAVE_API_KEY")

    return {
        "install child": install_child,
        "provision gate": provision_gate,
        "startup config": startup_config,
        "configured server": configured_server,
        "credential check": credential_check,
        "lifecycle connect": lifecycle_connect,
        "config_status availability": config_status_availability,
        "gateway availability": gateway_availability,
        "init": init,
        "secrets check": secrets_check,
        "header lookup": header_lookup,
        "tenant headers": tenant_headers,
        "credential_lookup": lookup,
    }


#: What each consumer answers when it answers for B.
B_ANSWERS: dict[str, object] = {
    "install child": "from-b",
    "provision gate": ["ONLY_B_TOKEN"],
    "startup config": "from-b",
    "configured server": "from-b",
    "credential check": ["ONLY_B_TOKEN"],
    "lifecycle connect": "from-b",
    "config_status availability": ["ONLY_B_TOKEN"],
    "gateway availability": ["ONLY_B_TOKEN"],
    "init": True,
    "secrets check": ["ONLY_A_TOKEN"],
    "header lookup": "from-b",
    "tenant headers": "tenant-from-b",
    "credential_lookup": "from-b",
}

#: Consumers whose project is the one their caller names, never an implicit
#: one: the config load reads ``.mcp.json`` from ``project_root`` (else the
#: root discovered from the working directory) and injects credentials for that
#: same project; ``pmcp init`` configures ``--project`` (else the working
#: directory). Production passes the served project to both (GatewayServer and
#: the init command get ``args.project``), so only the explicit mode applies.
EXPLICIT_ONLY = frozenset({"configured server", "init"})

CONSUMER_GRID = [
    (consumer, mode)
    for consumer in B_ANSWERS
    for mode in ("explicit", "served")
    if not (mode == "served" and consumer in EXPLICIT_ONLY)
]


@pytest.mark.parametrize(
    ("consumer", "mode"), CONSUMER_GRID, ids=[f"{c}-{m}" for c, m in CONSUMER_GRID]
)
def test_every_consumer_answers_for_the_project_it_serves(
    consumer: str,
    mode: str,
    roots: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Served root B, working directory A: each consumer answers with B's credential.

    ``explicit``: the consumer is handed B (``project_root=B``, as the gateway
    hands it ``args.project``). ``served``: it is handed nothing and B is the
    served root (``pmcp --project B``, :func:`cli.serve_project`).
    """
    from pmcp import trust_store

    for key in CONSUMER_KEYS:
        monkeypatch.delenv(key, raising=False)
    for name in ("a", "b"):
        (roots[name] / ".env.pmcp").write_text(
            f"BRAVE_API_KEY=from-{name}\n"
            f"ONLY_{name.upper()}_TOKEN=x\n"
            # Only B has it, so `pmcp init` finding it means it read B.
            + ("TAVILY_API_KEY=from-b\n" if name == "b" else "")
        )
        # A non-empty tenant store under BOTH roots (board round 10 codex F001:
        # with B served, the tenant lookup read A's tenant store).
        tenant = roots[name] / ".pmcp" / "tenants" / "t" / "pmcp.env"
        tenant.parent.mkdir(parents=True)
        tenant.write_text(f"BRAVE_API_KEY=tenant-from-{name}\n")
        mcp = roots[name] / ".mcp.json"
        mcp.write_text(
            '{"mcpServers": {"brave-search": {"command": "npx", '
            '"args": ["-y", "@brave/brave-search-mcp-server"]}}}'
        )
        trust_store.record(mcp, mcp.read_bytes(), "project", trust_store.APPROVED)
    run = _consumers(roots, monkeypatch)[consumer]
    _startup(roots)  # in a
    if mode == "served":
        cli.serve_project(roots["b"])
        project = None
    else:
        project = roots["b"]
    assert run(project) == B_ANSWERS[consumer]  # type: ignore[operator]


def test_the_grid_covers_every_consumer_in_the_inventory() -> None:
    """A new consumer joins the inventory; it must join this grid too."""
    import ast
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    import test_store_reader_inventory as inventory

    consumers = set()
    for module, source in inventory._src_sources().items():
        for name, fn in inventory._qualified_functions(ast.parse(source)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    f = node.func
                    called = getattr(f, "id", getattr(f, "attr", ""))
                    if called in ("credential_value", "credential_lookup"):
                        consumers.add(f"{module}:{name}")
    assert consumers == set(GRID_COVERAGE)


#: Every function in src/pmcp that calls credential_value or credential_lookup,
#: and the grid row that runs it.
GRID_COVERAGE = {
    "pmcp.manifest.installer:build_install_child_env": "install child",
    "pmcp.manifest.installer:check_api_key": "provision gate",
    "pmcp.config.loader:_credential_value_for": "startup config",
    "pmcp.tools.handlers:GatewayTools._check_api_key_available": "credential check",
    "pmcp.tools.handlers:GatewayTools.config_status": "config_status availability",
    "pmcp.server:GatewayServer.initialize": "gateway availability",
    "pmcp.cli:run_init": "init",
    "pmcp.cli_commands.secrets:run_secrets_check": "secrets check",
    "pmcp.remote_auth:build_remote_header_env_lookup": "header lookup",
    "pmcp.remote_auth:resolve_remote_headers_for_tenant": "tenant headers",
    "pmcp.env_store:credential_lookup": "credential_lookup",
}


def test_pmcp_project_serves_that_project(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring: ``pmcp --project B`` run from A answers for B, through main()."""
    import sys

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    (roots["a"] / ".env.pmcp").write_text("BRAVE_API_KEY=from-a\n")
    (roots["b"] / ".env.pmcp").write_text("BRAVE_API_KEY=from-b\n")
    seen: list[object] = []

    async def report(_args: argparse.Namespace) -> None:
        seen.append(env_store.credential_value("BRAVE_API_KEY"))

    monkeypatch.setattr(cli, "async_main", report)
    monkeypatch.setattr(cli, "find_dotenv", lambda: str(roots["base"] / "no-such.env"))
    monkeypatch.setattr(sys, "argv", ["pmcp", "--project", str(roots["b"])])
    cli.main()
    assert seen == ["from-b"]


# --------------------------------------------------------------------------- #
# Board round 9 N-1: an entry keyed on the root's inode alone could be found by
# a new directory that reused a deleted one's inode.
# --------------------------------------------------------------------------- #


def test_a_directory_that_reuses_an_inode_gets_its_own_entry(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inode reuse, made deterministic: both directories report one inode and ctime.

    The reviewer's case: a root looked up while it had no store, then deleted,
    and a new directory elsewhere given its inode. The ctime is pinned equal
    too, so only the path in the key tells the two apart (the ctime check has
    its own test below).
    """
    monkeypatch.delenv("REUSE_TOKEN", raising=False)
    _startup(roots)
    old = roots["base"] / "old"
    new = roots["base"] / "new"
    real_identity = env_store._identity
    shared = real_identity(roots["base"])
    monkeypatch.setattr(
        env_store,
        "_identity",
        lambda p: shared if Path(p) in (old, new) else real_identity(p),
    )
    real_stamp = env_store._root_stamp
    monkeypatch.setattr(
        env_store,
        "_root_stamp",
        lambda p: 1 if Path(p) in (old, new) else real_stamp(p),
    )
    old.mkdir()
    assert env_store.credential_value("REUSE_TOKEN", root=old) is None
    old.rmdir()
    new.mkdir()
    (new / ".env.pmcp").write_text("REUSE_TOKEN=new\n")
    assert env_store.credential_value("REUSE_TOKEN", root=new) == "new"


def test_a_change_to_the_root_directory_rebuilds_its_entry(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry records the root's ``st_ctime_ns``; a change there rebuilds."""
    calls: list[Path] = []
    real = env_store._build_root_entry

    def counting(root: Path):  # type: ignore[no-untyped-def]
        calls.append(root)
        return real(root)

    monkeypatch.setattr(env_store, "_build_root_entry", counting)
    (roots["a"] / ".env.pmcp").write_text("STAMP_TOKEN=x\n")
    _startup(roots)
    env_store.credential_value("STAMP_TOKEN")
    built = len(calls)
    before = os.stat(roots["a"]).st_ctime_ns
    while os.stat(roots["a"]).st_ctime_ns == before:
        (roots["a"] / "unrelated").touch()
        (roots["a"] / "unrelated").unlink()
    assert env_store.credential_value("STAMP_TOKEN") == "x"
    assert len(calls) == built + 1


# --------------------------------------------------------------------------- #
# Board round 9, external seats (reviewed 078084c). Their falsifiers, kept as
# regression tests. codex F002 is new: a cached entry was validated against
# the PATHS recorded when it was built, so a renamed root was answered from the
# file moved back to its old path. A cached entry now records file NAMES and is
# checked against the files under the root it is asked about.
# --------------------------------------------------------------------------- #


def test_codex_f002_a_renamed_root_does_not_read_the_old_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r9 F002, verbatim in substance."""
    home, original, renamed = [
        tmp_path / name for name in ("home", "original", "renamed")
    ]
    home.mkdir()
    original.mkdir()
    key = "F002_API_KEY"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(env_store, "_PINNED_USER_STORE", None)
    monkeypatch.setattr(env_store, "_REPO_CREDENTIALS", {})
    monkeypatch.setattr(env_store, "_DEFAULT_ROOT", None)
    (original / ".env.pmcp").write_text(f"{key}=original-secret\n")
    assert env_store.credential_value(key, root=original) == "original-secret"
    original.rename(renamed)
    original.mkdir()
    (renamed / ".env.pmcp").rename(original / ".env.pmcp")
    assert env_store.read_store("project", project=renamed) == {}
    assert env_store.credential_value(key, root=renamed) is None
    assert build_remote_header_env_lookup(renamed)(key) is None
    # The file is original's now, and original answers with it.
    assert env_store.credential_value(key, root=original) == "original-secret"


@pytest.mark.parametrize("keep_in_renamed", [False, True], ids=["moved", "both"])
def test_codex_f002_a_hard_linked_store_answers_only_where_it_is(
    keep_in_renamed: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The F002 sequence with the store hard-linked back, not moved.

    One inode, so an identity check alone cannot tell the two names apart:
    ``moved`` unlinks the renamed root's name afterwards, ``both`` keeps it.
    Each root answers exactly when a store is under it.
    """
    home, original, renamed = [
        tmp_path / name for name in ("home", "original", "renamed")
    ]
    home.mkdir()
    original.mkdir()
    key = "F002_LINK_KEY"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(key, raising=False)
    (original / ".env.pmcp").write_text(f"{key}=original-secret\n")
    assert env_store.credential_value(key, root=original) == "original-secret"
    original.rename(renamed)
    original.mkdir()
    os.link(renamed / ".env.pmcp", original / ".env.pmcp")
    if not keep_in_renamed:
        (renamed / ".env.pmcp").unlink()
    expected = "original-secret" if keep_in_renamed else None
    assert env_store.credential_value(key, root=renamed) == expected
    assert env_store.credential_value(key, root=original) == "original-secret"


def test_a_file_replaced_with_its_old_inode_and_mtime_is_reread(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """grok r9: a replacement that reuses the inode and restores the old
    modification time and size still has a new change time.

    Made deterministic by rewriting the file in place (same inode) and
    restoring its modification time; the size is kept equal.
    """
    monkeypatch.delenv("CTIME_TOKEN", raising=False)
    store = roots["a"] / ".env.pmcp"
    store.write_text("CTIME_TOKEN=old-1\n")
    _startup(roots)
    assert env_store.credential_value("CTIME_TOKEN") == "old-1"
    before = os.lstat(store)
    while os.lstat(store).st_ctime_ns == before.st_ctime_ns:
        with open(store, "r+") as handle:  # same inode
            handle.write("CTIME_TOKEN=new-2\n")
        os.utime(store, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = os.lstat(store)
    assert (after.st_ino, after.st_mtime_ns, after.st_size) == (
        before.st_ino,
        before.st_mtime_ns,
        before.st_size,
    )
    assert env_store.credential_value("CTIME_TOKEN") == "new-2"


def test_codex_f001_the_install_child_uses_the_requested_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r9 F001, verbatim in substance."""
    from pmcp.manifest.installer import build_install_child_env
    from pmcp.manifest.loader import ServerConfig

    home, a, b = [tmp_path / name for name in ("home", "a", "b")]
    for directory in (home, a, b):
        directory.mkdir()
    key = "F001_API_KEY"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(env_store, "_PINNED_USER_STORE", None)
    monkeypatch.setattr(env_store, "_REPO_CREDENTIALS", {})
    monkeypatch.setattr(env_store, "_DEFAULT_ROOT", a)
    (a / ".env.pmcp").write_text(f"{key}=from-a\n")
    (b / ".env.pmcp").write_text(f"{key}=from-b\n")
    env_store.load_store("project", project=a)
    server = ServerConfig(
        name="probe",
        description="probe",
        keywords=[],
        install={},
        command="unused",
        args=[],
        requires_api_key=True,
        env_var=key,
    )
    assert env_store.credential_value(key, root=b) == "from-b"
    with_b_key = build_install_child_env(server, b).get(key)
    (b / ".env.pmcp").unlink()
    assert env_store.credential_value(key, root=b) is None
    without_b_key = build_install_child_env(server, b).get(key)
    assert (with_b_key, without_b_key) == ("from-b", None)


def test_gemini_f001_startup_configs_for_another_project(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """gemini r9 F001, verbatim in substance."""
    from pmcp.config.loader import resolve_startup_configs
    from pmcp.manifest.loader import load_manifest

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    (roots["a"] / ".env.pmcp").write_text("BRAVE_API_KEY=from-a\n")
    (roots["b"] / ".env.pmcp").write_text("BRAVE_API_KEY=from-b\n")
    _startup(roots)
    server = load_manifest().get_server("brave-search")
    assert server is not None
    resolution = resolve_startup_configs(
        [],
        manifest_servers={"brave-search": server},
        enabled_auto_start=["brave-search"],
        project_root=roots["b"],
    )
    [config] = resolution.eager_configs
    assert config.config.env.get("BRAVE_API_KEY") == "from-b"


def test_grok_f001_the_served_project_never_gets_the_launch_credential(
    roots: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """grok r9 F001, verbatim in substance -- including ``pmcp doctor --project``."""
    from pmcp.config.loader import resolve_startup_configs
    from pmcp.manifest.installer import build_install_child_env
    from pmcp.manifest.loader import load_manifest

    launch, served = roots["a"], roots["b"]
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("ONLY_A_TOKEN", raising=False)
    (launch / ".env.pmcp").write_text("BRAVE_API_KEY=from-a\nONLY_A_TOKEN=only-a\n")
    (served / ".env.pmcp").write_text("BRAVE_API_KEY=from-b\n")
    (served / ".mcp.json").write_text(
        '{"mcpServers":{"remote-b":{"type":"http",'
        '"url":"https://example.invalid/mcp",'
        '"headers":{"Authorization":"Bearer ${ONLY_A_TOKEN}"}}}}\n'
    )
    _startup(roots)

    server = load_manifest().get_server("brave-search")
    assert server is not None and server.env_var == "BRAVE_API_KEY"
    assert build_install_child_env(server, served).get("BRAVE_API_KEY") == "from-b"
    resolution = resolve_startup_configs(
        [],
        manifest_servers={"brave-search": server},
        enabled_auto_start={"brave-search"},
        is_auth_available=lambda env_var: bool(env_store.credential_value(env_var)),
        project_root=served,
    )
    spawned = next(
        config for config in resolution.eager_configs if config.name == "brave-search"
    )
    assert spawned.config.env is not None
    assert spawned.config.env.get("BRAVE_API_KEY") == "from-b"

    async def _health_down(_timeout: float) -> tuple[bool, str, int | None]:
        return False, "down", None

    monkeypatch.setattr(cli, "_probe_http_health", _health_down)
    monkeypatch.setattr(cli, "_is_pmcp_system_service_active", lambda: False)
    capsys.readouterr()
    asyncio.run(
        cli.run_doctor(
            argparse.Namespace(project=served, log_level="error", timeout=0.1)
        )
    )
    assert "missing_env=ONLY_A_TOKEN" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Board round 10, codex F001: the tenant lookup read the tenant store of the
# working directory's project (repository_values(project=None) discovered the
# cwd), so with B served, A's tenant credential answered for B. A project or
# tenant store located with no explicit project is now the SERVED project's
# (env_store.resolve_project_root). The lifecycle half is round 10's N-2.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("consumer", ["tenant", "lifecycle"])
def test_codex_r10_f001_a_consumer_keeps_its_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str
) -> None:
    """codex r10 F001, verbatim in substance."""
    from types import SimpleNamespace

    from pmcp.manifest.loader import ServerConfig
    from pmcp.tools import handlers

    key = "REVIEW_PROJECT_TOKEN"
    home = tmp_path / "home"
    home.mkdir()
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        (root / ".git").mkdir(parents=True)
        (root / ".env.pmcp").write_text(f"{key}=from-{root.name}\n")
        tenant = root / ".pmcp" / "tenants" / "acme" / "pmcp.env"
        tenant.parent.mkdir(parents=True)
        tenant.write_text(f"{key}=from-{root.name}\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(a)
    cli.load_startup_env(dotenv_path=home / "absent.env")

    if consumer == "tenant":
        cli.serve_project(b)
        result = resolve_remote_headers_for_tenant(
            {"Authorization": "${" + key + "}"},
            server_name="review-server",
            tenant_id="acme",
        )
        actual = result.resolved_headers["Authorization"]
    else:
        server = ServerConfig(
            name="review-server",
            description="root-isolation probe",
            keywords=[],
            install={},
            command="unused",
            args=[],
            requires_api_key=True,
            env_var=key,
        )
        monkeypatch.setattr(
            handlers,
            "load_manifest",
            lambda **_k: SimpleNamespace(get_server=lambda name: server),
        )
        monkeypatch.setattr(
            handlers,
            "evaluate_provision",
            lambda *args, **kwargs: SimpleNamespace(allowed=True),
        )
        gateway = handlers.GatewayTools.__new__(handlers.GatewayTools)
        gateway._project_root = b
        gateway._policy_manager = SimpleNamespace(is_server_allowed=lambda name: True)
        gateway._load_all_configured_servers = lambda: {}
        assert gateway._check_api_key_available(key)
        config, failure, source = gateway._resolve_lifecycle_target(
            server.name, action="connect", prior_status="offline"
        )
        assert failure is None and source == "manifest"
        actual = config.config.env[key]

    assert actual == "from-b", f"{consumer} used another project's credential"


def test_an_unspecified_project_is_the_served_one(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every store located without a project: the served project's."""
    _startup(roots)  # in a
    assert env_store.resolve_project_root(None) == roots["a"]
    cli.serve_project(roots["b"])
    assert env_store.resolve_project_root(None) == roots["b"]
    assert env_store.resolve_scope_path("project") == roots["b"] / ".env.pmcp"
    # An explicit project is still that project.
    assert env_store.resolve_project_root(roots["a"]) == roots["a"]


# --------------------------------------------------------------------------- #
# Board round 11, codex F001: ``cli.serve_project(B)`` switched credentials to
# B while the project manifest overlay was still found by walking up from the
# working directory, so ``pmcp --project B`` started inside A paired A's
# approved overlay endpoint with B's token. Every project-scoped input now
# follows the served root (or an explicit project); this grid gives each root
# its own manifest overlay, policy and ``.mcp.json`` and checks each one.
# --------------------------------------------------------------------------- #


def _distinct_projects(roots: dict[str, Path], key: str) -> None:
    """Per root: an approved overlay, an approved policy, an approved .mcp.json."""
    import json

    from pmcp import trust_store

    for name in ("a", "b"):
        root = roots[name]
        overlay = root / ".pmcp" / "manifest.yaml"
        overlay.parent.mkdir(exist_ok=True)
        overlay.write_text(
            json.dumps(
                {
                    "servers": {
                        "review-remote": {
                            "description": "Project-specific remote",
                            "keywords": ["review"],
                            "transport": "streamable-http",
                            "url": f"https://{name}.example.invalid/mcp",
                            "headers": {"Authorization": "Bearer ${" + key + "}"},
                        },
                        # A local server a command-less .mcp.json entry
                        # inherits from: its args and credential name say
                        # which project's overlay answered.
                        "review-local": {
                            "description": "Project-specific local",
                            "keywords": ["review"],
                            "command": "echo",
                            "args": [f"--from-{name}"],
                            "requires_api_key": True,
                            "env_var": f"OVERLAY_{name.upper()}_LOCAL_KEY",
                        },
                    }
                }
            )
        )
        policy = root / ".mcp-gateway-policy.yaml"
        policy.write_text(f"servers:\n  denylist:\n    - only-{name}-denies\n")
        mcp = root / ".mcp.json"
        mcp.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        f"local-{name}": {"command": "true"},
                        "review-local": {"args": []},
                    }
                }
            )
        )
        (root / ".env.pmcp").write_text(f"{key}=credential-{name}\n")
        for path, scope in (
            (overlay, "project_manifest"),
            (policy, "project_policy"),
            (mcp, "project"),
        ):
            trust_store.record(path, path.read_bytes(), "project", trust_store.APPROVED)


@pytest.mark.parametrize("mode", ["served", "explicit"])
def test_every_project_scoped_input_comes_from_the_served_project(
    mode: str, roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Started in A, serving B: manifest overlay, policy, .mcp.json, credentials -- B's.

    ``served``: the ``pmcp --project B`` startup (``serve_project(B)``), every
    consumer handed nothing. ``explicit``: the process serves A, and each
    consumer is handed B (``GatewayServer(project_root=B)`` in library use).
    """
    from pmcp.client.manager import _remote_headers
    from pmcp.config.loader import load_configs, resolve_startup_configs
    from pmcp.manifest.loader import load_manifest
    from pmcp.policy.policy import PolicyManager

    key = "REVIEW_SHARED_TOKEN"
    monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    _distinct_projects(roots, key)
    _startup(roots)  # in a
    if mode == "served":
        cli.serve_project(roots["b"])
        project = None
    else:
        project = roots["b"]

    manifest = load_manifest(project_root=project)
    assert manifest.servers["review-remote"].url == "https://b.example.invalid/mcp"

    configs = {c.name: c for c in load_configs(project_root=project)}
    assert "local-b" in configs and "local-a" not in configs
    # The configured entry inherits B's overlay defaults, not A's.
    assert configs["review-local"].config.args == ["--from-b"]  # type: ignore[union-attr]

    # gateway.connect resolves the manifest server from B's overlay.
    from unittest.mock import MagicMock

    from pmcp.tools.handlers import GatewayTools

    tools = GatewayTools(
        client_manager=MagicMock(),
        policy_manager=PolicyManager(project_root=project),
        project_root=project,
    )
    resolved, refusal, _lookup = tools._resolve_lifecycle_target(
        "review-remote", action="connect", prior_status="offline"
    )
    assert refusal is None and resolved is not None
    assert resolved.config.url == "https://b.example.invalid/mcp"  # type: ignore[union-attr]

    # pmcp secrets check asks for the credential B's overlay declares.
    for name in ("A", "B"):
        monkeypatch.delenv(f"OVERLAY_{name}_LOCAL_KEY", raising=False)
    report = asyncio.run(secrets.run_secrets_check(argparse.Namespace(project=project)))
    assert "OVERLAY_B_LOCAL_KEY" in report["missing_keys"]
    assert "OVERLAY_A_LOCAL_KEY" not in report["missing_keys"]

    policy = PolicyManager(project_root=project)
    assert not policy.is_server_allowed("only-b-denies")
    assert policy.is_server_allowed("only-a-denies")

    resolution = resolve_startup_configs(
        [],
        manifest_servers=manifest.servers,
        enabled_auto_start={"review-remote"},
        project_root=project,
    )
    [target] = [c for c in resolution.eager_configs if c.name == "review-remote"]
    headers = _remote_headers(target.name, target.config, project_root=project)
    # Endpoint and credential: always one project's.
    assert (target.config.url, headers) == (
        "https://b.example.invalid/mcp",
        {"Authorization": "Bearer credential-b"},
    )


def test_codex_r11_f001_manifest_and_credentials_stay_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r11 F001, verbatim in substance."""
    import json

    from pmcp import trust_store
    from pmcp.client.manager import _remote_headers
    from pmcp.config.loader import resolve_startup_configs
    from pmcp.manifest.loader import load_manifest

    home = tmp_path / "home"
    home.mkdir()
    a, b = tmp_path / "a", tmp_path / "b"
    key = "REVIEW_SHARED_TOKEN"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    monkeypatch.setattr(trust_store, "_active_project_root", None)
    for root in (a, b):
        (root / ".git").mkdir(parents=True)
        overlay = root / ".pmcp" / "manifest.yaml"
        overlay.parent.mkdir()
        overlay.write_text(
            json.dumps(
                {
                    "servers": {
                        "review-remote": {
                            "description": "Project-specific remote",
                            "keywords": ["review"],
                            "transport": "streamable-http",
                            "url": f"https://{root.name}.example.invalid/mcp",
                            "headers": {"Authorization": "Bearer ${" + key + "}"},
                        }
                    }
                }
            )
        )
        (root / ".env.pmcp").write_text(f"{key}=credential-{root.name}\n")
    monkeypatch.chdir(a)
    for root in (a, b):
        overlay = root / ".pmcp" / "manifest.yaml"
        trust_store.record(
            overlay, overlay.read_bytes(), "project", trust_store.APPROVED
        )

    cli.load_startup_env(dotenv_path=home / "absent.env")
    cli.serve_project(b)
    trust_store.set_active_project_root(b)
    manifest = load_manifest()
    resolution = resolve_startup_configs(
        [],
        manifest_servers=manifest.servers,
        enabled_auto_start={"review-remote"},
        project_root=b,
    )
    [target] = [c for c in resolution.eager_configs if c.name == "review-remote"]
    headers = _remote_headers(target.name, target.config, project_root=b)
    assert (target.config.url, headers) == (
        "https://b.example.invalid/mcp",
        {"Authorization": "Bearer credential-b"},
    ), "A's manifest must not receive B's project credential"


@pytest.mark.parametrize("mode", ["served", "explicit"])
def test_the_gateway_and_the_cli_read_the_served_project(
    mode: str, roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same rule through the gateway's own construction and the CLI helpers."""
    import contextlib
    import io

    from pmcp import server as server_module

    key = "REVIEW_SHARED_TOKEN"
    monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    _distinct_projects(roots, key)
    _startup(roots)  # in a
    if mode == "served":
        cli.serve_project(roots["b"])
        project = None
    else:
        project = roots["b"]

    # The gateway: its policy, and the manifest its startup resolves.
    gateway = server_module.GatewayServer(
        project_root=project, cache_dir=roots["base"] / "cache"
    )
    assert not gateway._policy_manager.is_server_allowed("only-b-denies")
    assert gateway._policy_manager.is_server_allowed("only-a-denies")
    captured: dict[str, object] = {}

    def capture(*_a: object, **kwargs: object) -> None:
        captured.update(kwargs)
        raise _Captured(kwargs)

    monkeypatch.setattr(server_module, "resolve_startup_configs", capture)
    with pytest.raises(_Captured):
        asyncio.run(gateway.initialize())
    servers = captured["manifest_servers"]
    assert servers["review-remote"].url == "https://b.example.invalid/mcp"  # type: ignore[index]

    # The CLI's local .mcp.json (pmcp doctor), and where pmcp init writes.
    path, data = cli._load_local_mcp_json(project)
    assert path == roots["b"] / ".mcp.json"
    assert data is not None and "local-b" in data["mcpServers"]
    (roots["b"] / ".mcp.json").unlink()
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    with contextlib.redirect_stdout(io.StringIO()):
        asyncio.run(cli.run_init(argparse.Namespace(project=project, force=True)))
    assert (roots["b"] / ".mcp.json").exists()
    assert "local-a" in (roots["a"] / ".mcp.json").read_text()


@pytest.mark.parametrize("mode", ["served", "explicit"])
def test_secrets_check_reads_the_served_projects_manifest_overlay(
    mode: str, roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``pmcp secrets check`` asks for the header keys of B's overlay, not A's."""
    import json

    from pmcp import trust_store

    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    for name in ("a", "b"):
        overlay = roots[name] / ".pmcp" / "manifest.yaml"
        overlay.parent.mkdir(exist_ok=True)
        var = f"OVERLAY_{name.upper()}_TOKEN"
        monkeypatch.delenv(var, raising=False)
        overlay.write_text(
            json.dumps(
                {
                    "servers": {
                        "review-remote": {
                            "description": "Project-specific remote",
                            "keywords": ["review"],
                            "transport": "streamable-http",
                            "url": f"https://{name}.example.invalid/mcp",
                            "headers": {"Authorization": "Bearer ${" + var + "}"},
                        }
                    }
                }
            )
        )
        trust_store.record(
            overlay, overlay.read_bytes(), "project", trust_store.APPROVED
        )
    _startup(roots)  # in a
    if mode == "served":
        cli.serve_project(roots["b"])
        project = None
    else:
        project = roots["b"]
    report = asyncio.run(secrets.run_secrets_check(argparse.Namespace(project=project)))
    assert "OVERLAY_B_TOKEN" in report["missing_keys"]
    assert "OVERLAY_A_TOKEN" not in report["missing_keys"]


# --------------------------------------------------------------------------- #
# Board round 12, codex F001: the config loader fed its SOURCE classification
# (project_scope_root, None for the home directory) to the credential lookup,
# where None means "the served project" -- so GatewayServer(project_root=HOME)
# served from A gave home's configured server A's credential. Credentials come
# from the root the caller named; classification is a separate value.
# --------------------------------------------------------------------------- #


def test_codex_r12_f001_an_explicit_home_keeps_its_own_credential(
    tmp_path: Path,
) -> None:
    """codex r12 F001, verbatim in substance."""
    import json
    from unittest.mock import patch

    from pmcp.config.loader import load_configs

    home = tmp_path / "home"
    served = tmp_path / "served"
    home.mkdir()
    served.mkdir()
    (served / ".env.pmcp").write_text("BRAVE_API_KEY=served-token\n")
    (home / ".env.pmcp").write_text("BRAVE_API_KEY=home-token\n")
    (home / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"brave-search": {"command": "echo"}}})
    )
    with (
        patch.dict(os.environ, {"HOME": str(home)}, clear=True),
        patch.object(env_store, "_DEFAULT_ROOT", None),
        patch.object(env_store, "_REPO_CREDENTIALS", {}),
        patch.object(env_store, "_PINNED_USER_STORE", None),
    ):
        env_store.serve_project_root(served)
        assert env_store.credential_value("BRAVE_API_KEY", root=home) == "home-token"
        config = next(
            c for c in load_configs(project_root=home) if c.name == "brave-search"
        )
        assert config.source == "user"
        assert (config.config.env or {}).get("BRAVE_API_KEY") == "home-token"


def test_a_root_classification_rejects_still_supplies_its_own_credentials(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The explicit root is HOME (no project config source), the served root A.

    Every consumer handed HOME answers with HOME's credential, never A's.
    """
    import json

    from pmcp.config.loader import load_configs, resolve_startup_configs
    from pmcp.manifest.installer import build_install_child_env
    from pmcp.manifest.loader import load_manifest

    home = roots["home"]
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    (roots["a"] / ".env.pmcp").write_text("BRAVE_API_KEY=from-a\n")
    (home / ".env.pmcp").write_text("BRAVE_API_KEY=from-home\n")
    (home / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"brave-search": {"command": "echo"}}})
    )
    _startup(roots)  # in a, which is served
    assert env_store.project_scope_root(home) is None  # classification rejects it

    server = load_manifest(project_root=home).get_server("brave-search")
    assert server is not None
    answers = {
        "configured server": (
            next(
                c for c in load_configs(project_root=home) if c.name == "brave-search"
            ).config.env
            or {}
        ).get("BRAVE_API_KEY"),
        "startup config": next(
            c
            for c in resolve_startup_configs(
                [],
                manifest_servers={"brave-search": server},
                enabled_auto_start={"brave-search"},
                project_root=home,
            ).eager_configs
            if c.name == "brave-search"
        ).config.env.get("BRAVE_API_KEY"),
        "install child": build_install_child_env(server, home).get("BRAVE_API_KEY"),
        "credential_lookup": env_store.credential_lookup(home)("BRAVE_API_KEY"),
        "header lookup": build_remote_header_env_lookup(home)("BRAVE_API_KEY"),
    }
    assert answers == dict.fromkeys(answers, "from-home")


# --------------------------------------------------------------------------- #
# Board round 13, grok: `pmcp --project B doctor` parsed `project` as None --
# the subcommand redefined --project and its default overwrote the top-level
# value -- so the served root silently fell back to the working directory.
# Both flag positions now mean the same, for every subcommand that takes it.
# --------------------------------------------------------------------------- #


def _subcommands_taking_project() -> list[tuple[list[str], list[str]]]:
    """``(path, required filler)`` for every (nested) subcommand with --project."""
    import argparse as ap

    parser = cli.build_parser() if hasattr(cli, "build_parser") else None
    if parser is None:
        captured: dict[str, ap.ArgumentParser] = {}
        real = ap.ArgumentParser.parse_args

        def grab(self: ap.ArgumentParser, *a: object, **k: object) -> object:
            captured["p"] = self
            raise SystemExit(0)

        ap.ArgumentParser.parse_args = grab  # type: ignore[method-assign]
        try:
            with pytest.raises(SystemExit):
                cli.parse_args()
        finally:
            ap.ArgumentParser.parse_args = real  # type: ignore[method-assign]
        parser = captured["p"]

    found: list[tuple[list[str], list[str]]] = []

    def walk(p: ap.ArgumentParser, path: list[str]) -> None:
        options = {s for a in p._actions for s in a.option_strings}
        if path and "--project" in options:
            filler = [
                "X"
                for a in p._actions
                if not a.option_strings
                and not isinstance(a, ap._SubParsersAction)
                and a.nargs not in ("?", "*")
            ]
            found.append((path, filler))
        for action in p._actions:
            if isinstance(action, ap._SubParsersAction):
                for name, sub in action.choices.items():
                    walk(sub, [*path, name])

    walk(parser, [])
    return found


SUBCOMMANDS = _subcommands_taking_project()


def test_the_subcommand_inventory_is_not_empty() -> None:
    names = {" ".join(p) for p, _ in SUBCOMMANDS}
    assert {"doctor", "secrets check", "status", "init"} <= names


@pytest.mark.parametrize(
    ("path", "filler"), SUBCOMMANDS, ids=[" ".join(p) for p, _ in SUBCOMMANDS]
)
def test_both_project_flag_positions_mean_the_same(
    path: list[str],
    filler: list[str],
    roots: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    b = str(roots["b"])
    seen = []
    for argv in (
        ["pmcp", "--project", b, *path, *filler],
        ["pmcp", *path, "--project", b, *filler],
    ):
        monkeypatch.setattr(sys, "argv", argv)
        args = cli.parse_args()
        seen.append(getattr(args, "project", None))
    assert seen == [roots["b"], roots["b"]]
    # And no subcommand flag silently resets it.
    monkeypatch.setattr(sys, "argv", ["pmcp", "--project", b, *path, *filler])
    _startup(roots)
    cli.serve_project(cli.parse_args().project)
    assert env_store.resolve_project_root(None) == roots["b"]


def test_codex_r13_f001_a_lazy_startup_keeps_the_served_project(
    tmp_path: Path,
) -> None:
    """codex r13 F001, verbatim in substance."""
    from unittest.mock import patch

    home = tmp_path / "home"
    launch = tmp_path / "launch"
    served = tmp_path / "served"
    home.mkdir()
    for root in (launch, served):
        (root / ".git").mkdir(parents=True)
    with (
        patch.dict(os.environ, {"HOME": str(home)}, clear=True),
        patch.object(Path, "cwd", return_value=launch),
        patch.object(cli, "find_dotenv", return_value=""),
        patch.multiple(
            env_store,
            _DEFAULT_ROOT=None,
            _REPO_CREDENTIALS={},
            _PINNED_USER_STORE=None,
            _STARTUP_LOADED=False,
        ),
    ):
        env_store.serve_project_root(served)
        assert env_store.resolve_project_root() == served
        build_remote_header_env_lookup()
        assert env_store.resolve_project_root() == served


def test_an_explicit_root_then_a_lazy_load_answers_for_the_explicit_root(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit first, lazy load second: credentials and inputs stay B's."""
    monkeypatch.delenv("LAZY_TOKEN", raising=False)
    (roots["a"] / ".env.pmcp").write_text("LAZY_TOKEN=from-a\n")
    (roots["b"] / ".env.pmcp").write_text("LAZY_TOKEN=from-b\n")
    env_store.serve_project_root(roots["b"])  # library use, cwd is a
    assert env_store.credential_lookup()("LAZY_TOKEN") == "from-b"  # lazy load
    assert env_store.credential_value("LAZY_TOKEN") == "from-b"
    assert env_store.resolve_project_root(None) == roots["b"]


# --------------------------------------------------------------------------- #
# Board round 14, codex F001: before any startup load, a library caller's
# first credential answer came from the project file -- the user store was not
# in the environment yet -- and later answers from the user store. Every
# credential answer now loads the user store first (env_store.credential_value
# -> ensure_startup_load, idempotent, never re-choosing the served root).
# --------------------------------------------------------------------------- #


def test_codex_r14_f001_a_cold_library_startup_keeps_user_precedence(
    tmp_path: Path,
) -> None:
    """codex r14 F001, verbatim in substance."""
    import json
    from unittest.mock import patch

    from pmcp.config.loader import load_configs, resolve_startup_configs
    from pmcp.manifest.loader import load_manifest

    home = tmp_path / "home"
    user_store = home / ".config" / "pmcp" / "pmcp.env"
    user_store.parent.mkdir(parents=True)
    user_store.write_text("BRAVE_API_KEY=operator-token\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env.pmcp").write_text("BRAVE_API_KEY=project-token\n")
    (home / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"brave-search": {"command": "echo"}}})
    )
    with (
        patch.dict(os.environ, {"HOME": str(home)}, clear=True),
        patch.object(cli, "find_dotenv", return_value=""),
        patch.multiple(
            env_store,
            _DEFAULT_ROOT=None,
            _REPO_CREDENTIALS={},
            _PINNED_USER_STORE=None,
            _STARTUP_LOADED=False,
            _DOTENV_SOURCED_KEYS=set(),
            _PMCP_INTRODUCED_KEYS=set(),
            _WARNED_REFUSALS=set(),
        ),
    ):
        env_store.serve_project_root(project)
        configs = load_configs(project_root=project)
        manifest = load_manifest(project_root=project)
        resolution = resolve_startup_configs(
            configs,
            manifest_servers={"brave-search": manifest.servers["brave-search"]},
            enabled_auto_start={"brave-search"},
            project_root=project,
            is_auth_available=lambda key: bool(
                env_store.credential_value(key, root=project)
            ),
        )
        assert env_store.credential_lookup(project)("BRAVE_API_KEY") == (
            "operator-token"
        )
        [server] = resolution.eager_configs
        assert server.config.env["BRAVE_API_KEY"] == "operator-token"


COLD_ENTRIES = ["credential_value", "credential_lookup", "load_configs", "gateway"]


@pytest.mark.parametrize("entry", COLD_ENTRIES)
def test_with_no_prior_startup_every_entry_answers_the_user_store_first(
    entry: str, roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh process, no startup load: the FIRST answer already has user precedence.

    And the cold load keeps a served root set before it (round 14).
    """
    import json

    from pmcp import server as server_module
    from pmcp.config.loader import load_configs

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr(cli, "find_dotenv", lambda: "")
    user_store = roots["home"] / ".config" / "pmcp" / "pmcp.env"
    user_store.write_text("BRAVE_API_KEY=operator-token\n")
    (roots["b"] / ".env.pmcp").write_text("BRAVE_API_KEY=project-token\n")
    (roots["home"] / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"brave-search": {"command": "echo"}}})
    )
    assert env_store._STARTUP_LOADED is False
    env_store.serve_project_root(roots["b"])  # library use; cwd is a

    if entry == "credential_value":
        first = env_store.credential_value("BRAVE_API_KEY")
    elif entry == "credential_lookup":
        first = env_store.credential_lookup(roots["b"])("BRAVE_API_KEY")
    elif entry == "load_configs":
        [config] = [c for c in load_configs() if c.name == "brave-search"]
        first = (config.config.env or {}).get("BRAVE_API_KEY")
    else:
        captured: dict[str, object] = {}

        def capture(configs: object, **kwargs: object) -> None:
            captured["configs"] = configs
            raise _Captured(kwargs)

        monkeypatch.setattr(server_module, "resolve_startup_configs", capture)
        gateway = server_module.GatewayServer(
            project_root=roots["b"], cache_dir=roots["base"] / "cache"
        )
        with pytest.raises(_Captured):
            asyncio.run(gateway.initialize())
        [config] = [
            c
            for c in captured["configs"]  # type: ignore[attr-defined]
            if c.name == "brave-search"
        ]
        first = (config.config.env or {}).get("BRAVE_API_KEY")

    assert first == "operator-token"
    assert env_store.credential_value("BRAVE_API_KEY") == "operator-token"
    assert env_store.resolve_project_root(None) == roots["b"]


def test_a_lazy_load_with_no_served_root_chooses_none(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lazy load loads; it does not fix the project.

    A library process that served no root and changes directory keeps finding
    the project from where it now is -- only ``pmcp`` itself (main) or an
    explicit ``serve_project_root`` fixes one.
    """
    monkeypatch.delenv("LAZY_TOKEN", raising=False)
    monkeypatch.setattr(cli, "find_dotenv", lambda: "")
    (roots["a"] / ".env.pmcp").write_text("LAZY_TOKEN=from-a\n")
    (roots["b"] / ".env.pmcp").write_text("LAZY_TOKEN=from-b\n")
    assert env_store.credential_value("LAZY_TOKEN") == "from-a"  # cold, in a
    assert env_store.served_project_root() is None
    monkeypatch.chdir(roots["b"])
    assert env_store.credential_value("LAZY_TOKEN") == "from-b"


def test_the_startup_load_keeps_a_root_served_before_it(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``load_startup_env`` (main's, which may choose) keeps an already-served root."""
    monkeypatch.setattr(cli, "find_dotenv", lambda: "")
    env_store.serve_project_root(roots["b"])  # cwd is a
    cli.load_startup_env()
    assert env_store.served_project_root() == roots["b"]


# --------------------------------------------------------------------------- #
# Board round 15, claude F001: in a process that never ran main(), a gateway
# object built with no project kept project_root=None and re-resolved the
# project from the CURRENT working directory on every lookup -- after a chdir,
# a lazy connect paired A's endpoint with B's token. Each long-lived object now
# binds a concrete root when it is built (env_store.bind_project_root).
# --------------------------------------------------------------------------- #


def test_claude_r15_f001_a_gateway_object_keeps_its_projects_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """claude r15 F001, verbatim in substance."""
    import contextlib
    import io

    home = tmp_path / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("F001_TOKEN", raising=False)
    project = {}
    for name in ("a", "b"):
        root = tmp_path / name
        (root / ".git").mkdir(parents=True)
        (root / ".env.pmcp").write_text(f"F001_TOKEN=token-of-{name}\n")
        project[name] = root
    remote = {
        "type": "streamable-http",
        "url": "http://127.0.0.1:9/a",
        "headers": {"Authorization": "Bearer ${F001_TOKEN}"},
    }
    with contextlib.redirect_stderr(io.StringIO()):
        from pmcp.client import manager as mgr
        from pmcp.config.loader import RemoteMcpServerConfig

        monkeypatch.chdir(project["a"])
        client_manager = mgr.ClientManager(project_root=None)
        config = RemoteMcpServerConfig(**remote)
        first = mgr._remote_headers(
            "remote-a", config, project_root=client_manager._project_root
        )
        assert first == {"Authorization": "Bearer token-of-a"}
        monkeypatch.chdir(project["b"])
        headers = mgr._remote_headers(
            "remote-a", config, project_root=client_manager._project_root
        )
    assert headers == {"Authorization": "Bearer token-of-a"}


@pytest.mark.parametrize(
    "obj", ["ClientManager", "GatewayTools", "GatewayServer", "PolicyManager"]
)
def test_a_long_lived_object_built_in_a_stays_in_a_after_a_chdir(
    obj: str, roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No main(), no served root: built in A, used after chdir(B) -- A's, throughout."""
    from unittest.mock import MagicMock

    from pmcp import server as server_module
    from pmcp.client.manager import ClientManager, _remote_headers
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools

    key = "REVIEW_SHARED_TOKEN"
    monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    monkeypatch.setattr(cli, "find_dotenv", lambda: "")
    _distinct_projects(roots, key)
    assert env_store.served_project_root() is None  # a library process
    # cwd is a (fixture)

    if obj == "ClientManager":
        built = ClientManager()
        monkeypatch.chdir(roots["b"])
        from pmcp.config.loader import RemoteMcpServerConfig

        config = RemoteMcpServerConfig(
            type="streamable-http",
            url="https://a.example.invalid/mcp",
            headers={"Authorization": "Bearer ${" + key + "}"},
        )
        answer = _remote_headers(
            "review-remote", config, project_root=built._project_root
        )
        assert answer == {"Authorization": "Bearer credential-a"}
    elif obj == "GatewayTools":
        built = GatewayTools(client_manager=MagicMock(), policy_manager=PolicyManager())
        monkeypatch.chdir(roots["b"])
        resolved, refusal, _lookup = built._resolve_lifecycle_target(
            "review-remote", action="connect", prior_status="offline"
        )
        assert refusal is None and resolved is not None
        assert resolved.config.url == "https://a.example.invalid/mcp"  # type: ignore[union-attr]
        assert built._check_api_key_available(key)
        names = {c.name for c in built._load_all_configured_servers().values()}
        assert "local-a" in names and "local-b" not in names
    elif obj == "GatewayServer":
        built = server_module.GatewayServer(cache_dir=roots["base"] / "cache")
        monkeypatch.chdir(roots["b"])
        captured: dict[str, object] = {}

        def capture(configs: object, **kwargs: object) -> None:
            captured["configs"] = configs
            captured.update(kwargs)
            raise _Captured(kwargs)

        monkeypatch.setattr(server_module, "resolve_startup_configs", capture)
        with pytest.raises(_Captured):
            asyncio.run(built.initialize())
        names = {c.name for c in captured["configs"]}  # type: ignore[attr-defined]
        assert "local-a" in names and "local-b" not in names
        servers = captured["manifest_servers"]
        assert servers["review-remote"].url == "https://a.example.invalid/mcp"  # type: ignore[index]
        assert not built._policy_manager.is_server_allowed("only-a-denies")
    else:
        built = PolicyManager()
        monkeypatch.chdir(roots["b"])
        assert not built.is_server_allowed("only-a-denies")
        assert built.is_server_allowed("only-b-denies")
    assert built._project_root == roots["a"]


# --------------------------------------------------------------------------- #
# Boards round 16, grok and codex F001: the trust-store residency guard judged
# with the launch checkout (cwd) and a global, not the object's bound project.
# A gateway built in A, run after chdir(B), read A's files while the guard no
# longer covered A -- so a ~/.config/pmcp linked into A let A's trust.json
# approve A's own .mcp.json. The guard now judges against the approved file's
# own checkout and the reader's bound root as well.
# --------------------------------------------------------------------------- #


def test_grok_r16_f001_a_bound_gateway_refuses_its_checkouts_trust_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """grok r16 F001, verbatim in substance."""
    import json

    from pmcp import trust_store
    from pmcp.config.loader import load_configs
    from pmcp.server import GatewayServer

    key = "OPERATOR_TOKEN"
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(key, "operator-secret")
    monkeypatch.delenv("PMCP_CONFIG", raising=False)
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    trust_store.set_active_project_root(None)
    checkout = tmp_path / "checkout"
    elsewhere = tmp_path / "elsewhere"
    for root in (checkout, elsewhere):
        (root / ".git").mkdir(parents=True)
    project_config = checkout / ".mcp.json"
    project_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "review-remote": {
                        "type": "streamable-http",
                        "url": "https://checkout.example.invalid/mcp",
                        "headers": {"Authorization": "Bearer ${" + key + "}"},
                    }
                }
            }
        )
    )
    planted = checkout / "planted-config"
    planted.mkdir()
    (home / ".config" / "pmcp").symlink_to(planted)
    monkeypatch.chdir(elsewhere)
    trust_store.record(
        project_config,
        project_config.read_bytes(),
        trust_store.PROJECT_SCOPE,
        trust_store.APPROVED,
    )
    monkeypatch.chdir(checkout)
    gateway = GatewayServer(cache_dir=tmp_path / "cache")
    inside = {c.name for c in load_configs(project_root=gateway._project_root)}
    assert "review-remote" not in inside
    monkeypatch.chdir(elsewhere)
    after = {c.name for c in load_configs(project_root=gateway._project_root)}
    assert gateway._project_root == checkout.resolve()
    assert "review-remote" not in after


def test_codex_r16_f001_a_bound_gateway_refuses_checkout_resident_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r16 F001, verbatim in substance."""
    import hashlib
    import json

    from pmcp import trust_store
    from pmcp.server import GatewayServer

    monkeypatch.setattr(trust_store, "_active_project_root", None)
    monkeypatch.setattr(cli, "find_dotenv", lambda: "")
    for key in ("PMCP_CONFIG", "PMCP_POLICY", "PMCP_MANIFEST_PATH"):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    for project in (project_a, project_b):
        (project / ".git").mkdir(parents=True)
    store_dir = project_a / "store"
    store_dir.mkdir()
    (home / ".config" / "pmcp").symlink_to(store_dir, target_is_directory=True)
    config = project_a / ".mcp.json"
    content = json.dumps(
        {"mcpServers": {"review-unapproved": {"command": "echo"}}}
    ).encode()
    config.write_bytes(content)
    (store_dir / "trust.json").write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "absolute_path": str(config.resolve()),
                        "content_sha256": hashlib.sha256(content).hexdigest(),
                        "scope": "project",
                        "decision": "approved",
                        "recorded_at": "2026-10-07T00:00:00+00:00",
                    }
                ],
            }
        )
    )
    monkeypatch.chdir(project_a)
    gateway = GatewayServer(cache_dir=tmp_path / "cache")
    assert "review-unapproved" not in (
        gateway._gateway_tools._load_all_configured_servers()
    )
    monkeypatch.chdir(project_b)
    assert gateway._project_root == project_a
    assert "review-unapproved" not in (
        gateway._gateway_tools._load_all_configured_servers()
    )


def _plant_store_in(roots: dict[str, Path]) -> None:
    """~/.config/pmcp is a link into A: every approval store resolves inside A."""
    import shutil

    target = roots["a"] / "planted-config"
    target.mkdir()
    pmcp_dir = roots["home"] / ".config" / "pmcp"
    shutil.rmtree(pmcp_dir)
    pmcp_dir.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("obj", ["GatewayServer", "GatewayTools", "PolicyManager"])
def test_a_bound_object_refuses_its_projects_self_approval_after_a_chdir(
    obj: str, roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Built in A, used after chdir(B): A's own approval store is still refused."""
    from unittest.mock import MagicMock

    from pmcp import trust_store
    from pmcp import server as server_module
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools

    key = "REVIEW_SHARED_TOKEN"
    monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    monkeypatch.setattr(cli, "find_dotenv", lambda: "")
    trust_store.set_active_project_root(None)
    # Approvals recorded while the store was the operator's, then the store is
    # moved inside A -- as a repository that ships ~/.config/pmcp would.
    _distinct_projects(roots, key)
    approvals = (roots["home"] / ".config" / "pmcp" / "trust.json").read_bytes()
    _plant_store_in(roots)
    (roots["a"] / "planted-config" / "trust.json").write_bytes(approvals)

    # cwd is a (fixture)
    if obj == "GatewayServer":
        built = server_module.GatewayServer(cache_dir=roots["base"] / "cache")
        monkeypatch.chdir(roots["b"])
        names = set(built._gateway_tools._load_all_configured_servers())
        assert "local-a" not in names
        assert built._policy_manager.is_server_allowed("only-a-denies")
    elif obj == "GatewayTools":
        built = GatewayTools(client_manager=MagicMock(), policy_manager=PolicyManager())
        monkeypatch.chdir(roots["b"])
        assert "local-a" not in set(built._load_all_configured_servers())
        from pmcp.manifest.loader import load_manifest

        overlay = load_manifest(project_root=built._project_root)
        assert "review-remote" not in overlay.servers
    else:
        monkeypatch.chdir(roots["a"])
        built = PolicyManager()  # discovers at construction, in a
        monkeypatch.chdir(roots["b"])
        assert built.is_server_allowed("only-a-denies")
    assert built._project_root == roots["a"]


def test_a_bound_policy_refuses_its_projects_package_approvals_after_a_chdir(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provision gate judges package-approval residency by the bound project."""
    from pmcp import trust_store
    from pmcp.manifest.loader import ServerConfig
    from pmcp.package_approvals import approve_package
    from pmcp.policy.policy import PolicyManager
    from pmcp.provision_gate import evaluate_provision
    from pmcp.manifest.package_identity import PackageIdentity

    trust_store.set_active_project_root(None)
    identity = PackageIdentity("npm", "example-mcp", "1.2.3", None)
    config = ServerConfig(
        name="srv",
        description="",
        keywords=[],
        install={"linux": ["npx", "-y", "example-mcp@1.2.3"]},
        command="npx",
        args=["-y", "example-mcp@1.2.3"],
    )
    approve_package(identity)  # by the operator, store outside every checkout
    stored = (
        roots["home"] / ".config" / "pmcp" / "package_approvals.json"
    ).read_bytes()
    _plant_store_in(roots)  # now the store resolves inside A
    (roots["a"] / "planted-config" / "package_approvals.json").write_bytes(stored)

    policy = PolicyManager()  # bound in a
    monkeypatch.chdir(roots["b"])
    decision = evaluate_provision(config, identity, source="configured", policy=policy)
    assert decision.reason != "package_approved"
    assert not decision.allowed


# --------------------------------------------------------------------------- #
# Board round 17. codex F001: the launch checkout was re-read from the cwd on
# every judgement, so after chdir(B) approvals stored inside the launch
# checkout A granted the served project C. grok F001: a bound root with no
# checkout marker contributed nothing, so a store linked into it approved its
# own packages and policy (while --project on it refused). Now: the launch
# directory is captured once; every root follows one rule -- its enclosing
# checkouts, else itself unless it is the home directory or above.
# --------------------------------------------------------------------------- #


def test_codex_r17_f001_the_launch_checkout_stays_refused_after_a_chdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r17 F001, verbatim in substance."""
    import hashlib
    import json
    import tempfile

    from pmcp import trust_store
    from pmcp.project_consent import read_and_gate

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(trust_store, "_active_project_root", None)
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    launch, later, served = (tmp_path / name for name in ("a", "b", "c"))
    for root in (launch, later, served):
        (root / ".git").mkdir(parents=True)
    store = launch / "store"
    store.mkdir()
    (home / ".config" / "pmcp").symlink_to(store, target_is_directory=True)
    config = served / ".mcp.json"
    content = b'{"mcpServers":{"repo-server":{"command":"echo"}}}'
    config.write_bytes(content)
    (store / "trust.json").write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "absolute_path": str(config.resolve()),
                        "content_sha256": hashlib.sha256(content).hexdigest(),
                        "scope": "user",
                        "decision": "approved",
                        "recorded_at": "2026-10-07T00:00:00+00:00",
                    }
                ],
            }
        )
    )
    monkeypatch.chdir(launch)
    trust_store.reset_launch_directory()  # a process launched in a
    trust_store.set_active_project_root(served)
    try:
        assert not read_and_gate(config, "project_mcp_json", project_root=served)[
            1
        ].allowed
        monkeypatch.chdir(later)
        assert not read_and_gate(config, "project_mcp_json", project_root=served)[
            1
        ].allowed
    finally:
        trust_store.set_active_project_root(None)


def _markerless_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    """A package approval whose store is linked into a plain directory D."""
    import shutil

    from pmcp.manifest.loader import ServerConfig
    from pmcp.manifest.package_identity import PackageIdentity
    from pmcp.package_approvals import approve_package

    home = Path(os.environ["HOME"])
    plain = tmp_path / "plain"
    plain.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    identity = PackageIdentity("npm", "example-mcp", "1.2.3", None)
    config = ServerConfig(
        name="srv",
        description="",
        keywords=[],
        install={"linux": ["npx", "-y", "example-mcp@1.2.3"]},
        command="npx",
        args=["-y", "example-mcp@1.2.3"],
    )
    monkeypatch.chdir(other)
    approve_package(identity)
    stored = (home / ".config" / "pmcp" / "package_approvals.json").read_bytes()
    planted = plain / "planted-config"
    planted.mkdir()
    pmcp_dir = home / ".config" / "pmcp"
    shutil.rmtree(pmcp_dir)
    pmcp_dir.symlink_to(planted, target_is_directory=True)
    (planted / "package_approvals.json").write_bytes(stored)
    return plain, other, identity, config


@pytest.mark.parametrize("path", ["served", "bound"])
def test_a_markerless_root_refuses_a_store_inside_it(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """grok r17 F001, both the CLI shape (served) and the library shape (bound)."""
    from pmcp import trust_store
    from pmcp.config.loader import find_project_root
    from pmcp.package_approvals import is_package_approved
    from pmcp.policy.policy import PolicyManager
    from pmcp.provision_gate import evaluate_provision

    trust_store.set_active_project_root(None)
    plain, other, identity, config = _markerless_store(tmp_path, monkeypatch)
    assert find_project_root(plain) is None
    trust_store.reset_launch_directory()  # launched in `other`
    try:
        if path == "served":
            trust_store.set_active_project_root(plain)  # pmcp --project D
            assert is_package_approved(identity) is False
        else:
            policy = PolicyManager(project_root=plain)
            monkeypatch.chdir(other)
            decision = evaluate_provision(
                config, identity, source="configured", policy=policy
            )
            assert decision.reason != "package_approved"
            assert decision.allowed is False
    finally:
        trust_store.set_active_project_root(None)


@pytest.mark.parametrize("path", ["served", "bound", "launched"])
def test_the_home_directory_as_a_root_still_accepts_the_operators_store(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HOME (or above) is never a boundary: the operator's own store works."""
    from pmcp import trust_store
    from pmcp.manifest.package_identity import PackageIdentity
    from pmcp.package_approvals import approve_package, is_package_approved

    home = Path(os.environ["HOME"])
    identity = PackageIdentity("npm", "example-mcp", "1.2.3", None)
    approve_package(identity)
    monkeypatch.chdir(home if path == "launched" else tmp_path)
    trust_store.reset_launch_directory()
    try:
        if path == "served":
            trust_store.set_active_project_root(home)
            assert is_package_approved(identity) is True
        elif path == "bound":
            assert is_package_approved(identity, project_root=home) is True
        else:
            assert is_package_approved(identity) is True
    finally:
        trust_store.set_active_project_root(None)


# --------------------------------------------------------------------------- #
# Board round 18. codex F001: home was compared by path spelling, so with HOME
# spelled through a link, a launch directory holding a link to that home looked
# like home's ancestor, dropped out of the guard, and a trust.json planted in it
# approved the served project. claude N-1: launching from ~/.config or
# ~/.config/pmcp refused the operator's own store. Home-ness is now decided by
# identity (pmcp.home_identity), with the default store's real directories
# exempt too.
# --------------------------------------------------------------------------- #


def _approval_for(config: Path, content: bytes) -> str:
    import hashlib
    import json

    return json.dumps(
        {
            "version": 1,
            "records": [
                {
                    "absolute_path": str(config.resolve()),
                    "content_sha256": hashlib.sha256(content).hexdigest(),
                    "scope": "user",
                    "decision": "approved",
                    "recorded_at": "2026-10-07T00:00:00+00:00",
                }
            ],
        }
    )


def test_codex_r18_f001_a_home_alias_cannot_exempt_the_launch_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r18 F001, verbatim in substance."""
    import tempfile

    from pmcp import trust_store

    tmp_path = tmp_path.resolve()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(trust_store, "_active_project_root", None)
    launch, home, served = (tmp_path / n for n in ("launch", "home", "served"))
    launch.mkdir()
    (home / ".config").mkdir(parents=True)
    (served / ".git").mkdir(parents=True)
    home_alias = launch / "home-link"
    home_alias.symlink_to(home, target_is_directory=True)
    store = launch / "store"
    store.mkdir()
    (home / ".config" / "pmcp").symlink_to(store, target_is_directory=True)
    config = served / ".mcp.json"
    content = b'{"mcpServers":{"unapproved":{"command":"echo"}}}'
    config.write_bytes(content)
    (store / "trust.json").write_text(_approval_for(config, content))
    monkeypatch.chdir(launch)
    trust_store.reset_launch_directory()
    trust_store.set_active_project_root(served)
    try:
        monkeypatch.setenv("HOME", str(home))
        assert not trust_store.is_approved(config, content, project_root=served)
        monkeypatch.setenv("HOME", str(home_alias))
        assert Path.home().resolve() == home
        assert not trust_store.is_approved(config, content, project_root=served)
    finally:
        trust_store.set_active_project_root(None)


@pytest.mark.parametrize("as_root", ["launch", "served", "bound"])
def test_a_home_spelled_through_a_link_is_still_home(
    as_root: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HOME via a link, as each kind of root: the operator's store still works."""
    from pmcp import trust_store
    from pmcp.manifest.package_identity import PackageIdentity
    from pmcp.package_approvals import approve_package, is_package_approved

    real = tmp_path.resolve() / "real-home"
    (real / ".config" / "pmcp").mkdir(parents=True)
    alias = tmp_path.resolve() / "home-alias"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("HOME", str(alias))
    identity = PackageIdentity("npm", "example-mcp", "1.2.3", None)
    approve_package(identity)
    monkeypatch.chdir(alias if as_root == "launch" else tmp_path)
    trust_store.reset_launch_directory()
    try:
        if as_root == "served":
            trust_store.set_active_project_root(alias)
            assert is_package_approved(identity) is True
        elif as_root == "bound":
            assert is_package_approved(identity, project_root=alias) is True
        else:
            assert is_package_approved(identity) is True
    finally:
        trust_store.set_active_project_root(None)


@pytest.mark.parametrize("where", [".config", ".config/pmcp"])
def test_launching_from_the_default_store_path_accepts_the_operators_store(
    where: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """claude r18 N-1: ~/.config or ~/.config/pmcp as the launch directory."""
    from pmcp import trust_store
    from pmcp.manifest.package_identity import PackageIdentity
    from pmcp.package_approvals import approve_package, is_package_approved

    home = Path(os.environ["HOME"])
    (home / ".config" / "pmcp").mkdir(parents=True, exist_ok=True)
    identity = PackageIdentity("npm", "example-mcp", "1.2.3", None)
    approve_package(identity)
    monkeypatch.chdir(home / where)
    trust_store.reset_launch_directory()
    assert is_package_approved(identity) is True
    assert (
        trust_store.trust_store_path().parent == (home / ".config" / "pmcp").resolve()
    )


def test_a_store_linked_from_home_into_a_plain_launch_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default-store exemption is for the REAL directories only.

    ~/.config/pmcp linked into a plain launch directory: the store resolves in
    the launch directory, which is a boundary, and the refusal names a
    directory (not a checkout).
    """
    import shutil

    from pmcp import trust_store
    from pmcp.trust_store import TrustStoreError

    home = Path(os.environ["HOME"])
    launch = tmp_path.resolve() / "launch"
    (launch / "store").mkdir(parents=True)
    pmcp_dir = home / ".config" / "pmcp"
    if pmcp_dir.exists():
        shutil.rmtree(pmcp_dir)
    pmcp_dir.parent.mkdir(parents=True, exist_ok=True)
    pmcp_dir.symlink_to(launch / "store", target_is_directory=True)
    monkeypatch.chdir(launch)
    trust_store.reset_launch_directory()
    with pytest.raises(TrustStoreError, match="inside the directory at"):
        trust_store.trust_store_path()


def test_launching_inside_the_target_of_a_linked_store_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exemption covers the REAL ~/.config/pmcp, never what a link names.

    ~/.config/pmcp linked to a plain directory, and pmcp launched IN that
    directory: it is the launch boundary and is not the operator's area.
    """
    import shutil

    from pmcp import trust_store
    from pmcp.trust_store import TrustStoreError

    home = Path(os.environ["HOME"])
    target = tmp_path.resolve() / "elsewhere" / "store"
    target.mkdir(parents=True)
    pmcp_dir = home / ".config" / "pmcp"
    if pmcp_dir.exists():
        shutil.rmtree(pmcp_dir)
    pmcp_dir.parent.mkdir(parents=True, exist_ok=True)
    pmcp_dir.symlink_to(target, target_is_directory=True)
    monkeypatch.chdir(target)
    trust_store.reset_launch_directory()
    with pytest.raises(TrustStoreError, match="inside the directory at"):
        trust_store.trust_store_path()


# --------------------------------------------------------------------------- #
# Boards round 19, grok and codex F001: the residency walk reused project
# discovery, which stops at the home directory, so a checkout ENCLOSING home
# was invisible -- with HOME at <checkout>/home, a repository-shipped
# HOME/.config/pmcp/trust.json approved HOME/app/.mcp.json. Residency now walks
# up to / on its own; only home itself (by identity) is never a checkout.
# --------------------------------------------------------------------------- #


def test_grok_r19_f001_a_checkout_enclosing_home_supplies_no_trust_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """grok r19 F001, verbatim in substance."""
    from pmcp import trust_store
    from pmcp.project_consent import gate_bytes

    repo = tmp_path / "repo"
    home = repo / "userhome"
    app = home / "app"
    (repo / ".git").mkdir(parents=True)
    (home / ".config" / "pmcp").mkdir(parents=True)
    app.mkdir()
    content = b'{"mcpServers":{"evil":{"command":"echo"}}}'
    config = app / ".mcp.json"
    config.write_bytes(content)
    (home / ".config" / "pmcp" / "trust.json").write_text(
        _approval_for(config, content)
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(app)
    trust_store.reset_launch_directory()
    trust_store.set_active_project_root(app)
    try:
        assert (
            gate_bytes(config, content, "project_mcp_json", project_root=app).allowed
            is False
        )
        assert trust_store.is_approved(config, content, project_root=app) is False
    finally:
        trust_store.set_active_project_root(None)


def test_codex_r19_f001_home_inside_a_checkout_cannot_exempt_an_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r19 F001, verbatim in substance."""
    import tempfile

    from pmcp import trust_store
    from pmcp.project_consent import read_and_gate

    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    home = checkout / "home"
    store = home / ".config" / "pmcp"
    store.mkdir(parents=True)
    served = home / "app"
    served.mkdir()
    config = served / ".mcp.json"
    content = b'{"mcpServers":{"unapproved":{"command":"echo"}}}'
    config.write_bytes(content)
    (store / "trust.json").write_text(_approval_for(config, content))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(trust_store, "_LAUNCH_DIRECTORY", None)
    monkeypatch.setattr(trust_store, "_active_project_root", served)
    monkeypatch.chdir(home / ".config")
    accepted, decision = read_and_gate(config, "project_mcp_json", project_root=served)
    assert not decision.allowed
    assert accepted is None


HOME_IN_CHECKOUT = [
    (launch, mode, depth)
    for launch in ("home", ".config", "app")
    for mode in ("served", "bound")
    for depth in (1, 2)
]


@pytest.mark.parametrize(
    ("launch", "mode", "depth"),
    HOME_IN_CHECKOUT,
    ids=[f"{w}-{m}-depth{d}" for w, m, d in HOME_IN_CHECKOUT],
)
def test_a_home_inside_a_checkout_never_approves_from_its_default_store(
    launch: str,
    mode: str,
    depth: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HOME at <checkout>/home (depth 1) or <checkout>/x/home (depth 2)."""
    from pmcp import trust_store
    from pmcp.project_consent import read_and_gate

    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    home = checkout / "home" if depth == 1 else checkout / "x" / "home"
    store = home / ".config" / "pmcp"
    store.mkdir(parents=True)
    app = home / "app"
    app.mkdir()
    config = app / ".mcp.json"
    content = b'{"mcpServers":{"planted":{"command":"echo"}}}'
    config.write_bytes(content)
    (store / "trust.json").write_text(_approval_for(config, content))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir({"home": home, ".config": home / ".config", "app": app}[launch])
    trust_store.reset_launch_directory()
    try:
        if mode == "served":
            trust_store.set_active_project_root(app)
            accepted, decision = read_and_gate(config, "project_mcp_json")
        else:
            accepted, decision = read_and_gate(
                config, "project_mcp_json", project_root=app
            )
        assert not decision.allowed and accepted is None
    finally:
        trust_store.set_active_project_root(None)


@pytest.mark.parametrize("launch", ["home", ".config", "app"])
def test_an_ordinary_home_with_no_enclosing_checkout_still_approves(
    launch: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator's normal home: the same layout, no checkout above it, works."""
    from pmcp import trust_store
    from pmcp.project_consent import read_and_gate

    home = Path(os.environ["HOME"]).resolve()
    (home / ".config" / "pmcp").mkdir(parents=True, exist_ok=True)
    app = home / "app"
    (app / ".git").mkdir(parents=True)
    config = app / ".mcp.json"
    content = b'{"mcpServers":{"approved":{"command":"echo"}}}'
    config.write_bytes(content)
    monkeypatch.chdir({"home": home, ".config": home / ".config", "app": app}[launch])
    trust_store.reset_launch_directory()
    trust_store.record(config, content, "project", trust_store.APPROVED)
    accepted, decision = read_and_gate(config, "project_mcp_json", project_root=app)
    assert decision.allowed and accepted == content


def test_a_home_kept_in_its_own_repository_still_approves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dotfiles repository AT home is the operator's, not a checkout boundary."""
    from pmcp import trust_store
    from pmcp.project_consent import read_and_gate

    home = Path(os.environ["HOME"]).resolve()
    (home / ".git").mkdir(exist_ok=True)
    (home / ".config" / "pmcp").mkdir(parents=True, exist_ok=True)
    app = home / "code" / "app"
    (app / ".git").mkdir(parents=True)
    config = app / ".mcp.json"
    content = b'{"mcpServers":{"approved":{"command":"echo"}}}'
    config.write_bytes(content)
    monkeypatch.chdir(app)
    trust_store.reset_launch_directory()
    trust_store.record(config, content, "project", trust_store.APPROVED)
    accepted, decision = read_and_gate(config, "project_mcp_json", project_root=app)
    assert decision.allowed and accepted == content


# --------------------------------------------------------------------------- #
# Boards round 20. codex F001: HOME=<checkout>/home with a repository-shipped
# `home -> .` resolved home to the checkout itself, whose identity the walk
# exempted as "home", so its planted trust.json approved home/app/.mcp.json.
# grok F001: a checkout ENCLOSING home owns home's ancestors, so its `.env`
# loaded into the environment as "the operator's", and with HOME unset a
# shipped HOME=. made the checkout the exempt home. Operator ownership now
# requires that no checkout control the path along its unresolved spelling;
# HOME is never taken from a file.
# --------------------------------------------------------------------------- #


def test_codex_r20_f001_a_repository_home_link_cannot_exempt_the_checkout(
    tmp_path: Path,
) -> None:
    """codex r20 F001, verbatim in substance."""
    from unittest.mock import patch

    from pmcp import trust_store
    from pmcp.project_consent import read_and_gate

    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    home = checkout / "home"
    home.symlink_to(".", target_is_directory=True)
    store = home / ".config" / "pmcp"
    store.mkdir(parents=True)
    project = home / "app"
    project.mkdir()
    config = project / ".mcp.json"
    content = b'{"mcpServers":{"unapproved":{"command":"echo"}}}'
    config.write_bytes(content)
    (store / "trust.json").write_text(_approval_for(config, content))
    previous = Path.cwd()
    try:
        os.chdir(home / ".config")
        with (
            patch.dict(os.environ, {"HOME": str(home)}),
            patch.object(trust_store, "_LAUNCH_DIRECTORY", None),
            patch.object(trust_store, "_active_project_root", None),
        ):
            accepted, decision = read_and_gate(
                config, "project_mcp_json", project_root=project
            )
            assert accepted is None and not decision.allowed
    finally:
        os.chdir(previous)


_GROK_R20_CHILD = """\
import os, sys
from pathlib import Path
mode, repo, home, app = sys.argv[1:]
repo, home, app = Path(repo), Path(home), Path(app)
os.chdir(repo if mode == "unset" else app)
if mode == "unset":
    import pwd
    class _Pw:
        pw_dir = str(home)
    pwd.getpwuid = lambda _uid: _Pw()
    os.environ.pop("HOME", None)
else:
    os.environ["HOME"] = str(home)
for key in ("HTTP_PROXY", "HTTPS_PROXY", "LD_PRELOAD", "SSLKEYLOGFILE", "NODE_OPTIONS"):
    os.environ.pop(key, None)
from pmcp.cli import load_startup_env
load_startup_env()
from pmcp import trust_store
from pmcp.project_consent import read_and_gate
from pmcp.env_store import child_process_env
trust_store.reset_launch_directory()
decision = read_and_gate(app / ".mcp.json", "project_mcp_json", project_root=app)[1]
child = child_process_env()
print("RESOLVED_HOME=" + str(Path.home().resolve()))
print("ALLOWED=" + str(bool(decision.allowed)))
for key in ("HTTP_PROXY", "HTTPS_PROXY", "LD_PRELOAD", "SSLKEYLOGFILE", "NODE_OPTIONS"):
    print(f"ENV {key}={os.environ.get(key)}")
    print(f"CHILD {key}={child.get(key)}")
"""


@pytest.mark.parametrize("mode", ["unset", "set"])
def test_grok_r20_f001_a_checkout_enclosing_home_neither_approves_nor_loads(
    mode: str, tmp_path: Path
) -> None:
    """grok r20 F001, verbatim in substance, in a fresh process."""
    import hashlib
    import json
    import subprocess
    import sys

    import pmcp

    repo = tmp_path.resolve() / "repo"
    home = repo / "home"
    app = home / "app"
    (repo / ".git").mkdir(parents=True)
    (repo / ".config" / "pmcp").mkdir(parents=True)
    (home / ".config" / "pmcp").mkdir(parents=True)
    app.mkdir(parents=True)
    content = b'{"mcpServers":{"evil":{"command":"echo"}}}'
    (app / ".mcp.json").write_bytes(content)
    (repo / ".env").write_text(
        "HOME=.\nHTTP_PROXY=http://127.0.0.1:9\nSSLKEYLOGFILE=/tmp/keys.log\n"
        "NODE_OPTIONS=--require /evil.js\nLD_PRELOAD=/evil.so\n"
    )
    (home / ".config" / "pmcp" / "pmcp.env").write_text(
        "HTTPS_PROXY=http://127.0.0.1:8\n"
    )
    (repo / ".config" / "pmcp" / "trust.json").write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "absolute_path": str((app / ".mcp.json").resolve()),
                        "content_sha256": hashlib.sha256(content).hexdigest(),
                        "scope": "user",
                        "decision": "approved",
                        "recorded_at": "2026-10-07T00:00:00+00:00",
                    }
                ],
            }
        )
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("HTTP_PROXY", "HTTPS_PROXY", "LD_PRELOAD", "NODE_OPTIONS")
    }
    env["PYTHONPATH"] = str(Path(pmcp.__file__).resolve().parent.parent)
    proc = subprocess.run(
        [sys.executable, "-c", _GROK_R20_CHILD, mode, str(repo), str(home), str(app)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    out = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    assert out["ALLOWED"] == "False"
    assert out["RESOLVED_HOME"] != str(repo)
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LD_PRELOAD",
        "SSLKEYLOGFILE",
        "NODE_OPTIONS",
    ):
        assert out[f"ENV {key}"] == "None", (key, out)
        assert out[f"CHILD {key}"] == "None", (key, out)


def test_home_is_never_taken_from_a_loaded_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HOME unset, a user store that sets HOME: the passwd home stands."""
    import pwd

    from pmcp import env_store

    home = Path(os.environ["HOME"])
    store = home / ".config" / "pmcp" / "pmcp.env"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("HOME=/somewhere/else\nLOADED_OK=1\n")
    monkeypatch.delenv("LOADED_OK", raising=False)

    class _Pw:
        pw_dir = str(home)

    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: _Pw())
    monkeypatch.delenv("HOME")
    env_store.load_store("user")
    assert "HOME" not in os.environ
    assert os.environ.get("LOADED_OK") == "1"
    monkeypatch.delenv("LOADED_OK")


def test_a_checkout_above_home_keeps_its_env_out_of_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The startup `.env` in an ancestor of home that a checkout controls."""
    repo = tmp_path.resolve() / "repo"
    home = repo / "home"
    (repo / ".git").mkdir(parents=True)
    (home / ".config" / "pmcp").mkdir(parents=True)
    (repo / ".env").write_text("CHECKOUT_ENV_372=leaked\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CHECKOUT_ENV_372", raising=False)
    monkeypatch.chdir(home)
    cli.load_startup_env(dotenv_path=str(repo / ".env"))
    assert "CHECKOUT_ENV_372" not in os.environ


def test_a_clean_dotfiles_home_still_loads_its_user_store_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repository AT home, reached without repository links, is the operator's."""
    home = Path(os.environ["HOME"]).resolve()
    (home / ".git").mkdir(exist_ok=True)
    (home / ".config" / "pmcp").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "pmcp" / "pmcp.env").write_text("DOTFILES_USER_372=ok\n")
    (home / ".env").write_text("DOTFILES_ENV_372=ok\n")
    for key in ("DOTFILES_USER_372", "DOTFILES_ENV_372"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(home)
    cli.load_startup_env(dotenv_path=str(home / ".env"))
    try:
        assert os.environ.get("DOTFILES_USER_372") == "ok"
        assert os.environ.get("DOTFILES_ENV_372") == "ok"
    finally:
        for key in ("DOTFILES_USER_372", "DOTFILES_ENV_372"):
            os.environ.pop(key, None)


def test_a_launch_from_home_config_inside_a_checkout_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exemption is decided only after the checkout walk.

    HOME inside a checkout, launched from HOME/.config, approving a project
    OUTSIDE the checkout: the launch directory is the only root that reaches
    the store, and it looks like the default store's directory. The checkout
    above it still makes it a boundary.
    """
    from pmcp import trust_store
    from pmcp.project_consent import read_and_gate

    checkout = tmp_path.resolve() / "checkout"
    (checkout / ".git").mkdir(parents=True)
    home = checkout / "home"
    store = home / ".config" / "pmcp"
    store.mkdir(parents=True)
    elsewhere = tmp_path.resolve() / "elsewhere"
    (elsewhere / ".git").mkdir(parents=True)
    config = elsewhere / ".mcp.json"
    content = b'{"mcpServers":{"planted":{"command":"echo"}}}'
    config.write_bytes(content)
    (store / "trust.json").write_text(_approval_for(config, content))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home / ".config")
    trust_store.reset_launch_directory()
    trust_store.set_active_project_root(elsewhere)
    try:
        accepted, decision = read_and_gate(
            config, "project_mcp_json", project_root=elsewhere
        )
        assert accepted is None and not decision.allowed
    finally:
        trust_store.set_active_project_root(None)


# --------------------------------------------------------------------------- #
# Board round 21, grok F001: with a repository-shipped `home -> .` and
# HOME=<checkout>/home, the checkout's .mcp.json, .pmcp/manifest.yaml and
# .claude/gateway-policy.yaml were read as the operator's user config, user
# manifest overlay and base policy. Every home-scoped file now goes through
# one gate (pmcp.home_identity) that answers only while HOME is the operator's.
# --------------------------------------------------------------------------- #


def _repository_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout shipping `home -> .`, with HOME pointed at it; cwd home/app."""
    from pmcp import home_identity

    checkout = tmp_path.resolve() / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "app").mkdir()
    home = checkout / "home"
    home.symlink_to(".", target_is_directory=True)
    (checkout / ".mcp.json").write_text(
        '{"mcpServers":{"planted-user":{"command":"echo","args":["pwned"]}}}'
    )
    (checkout / ".pmcp").mkdir()
    (checkout / ".pmcp" / "manifest.yaml").write_text(
        "servers:\n  planted-manifest:\n    description: planted\n"
        "    keywords: [plantedwidget]\n    command: echo\n"
        '    args: ["manifest"]\n'
    )
    (checkout / ".claude").mkdir()
    (checkout / ".claude" / "gateway-policy.yaml").write_text(
        'servers:\n  denylist: ["from-repo-policy"]\n'
    )
    (checkout / ".claude" / "gateway-guidance.yaml").write_text("level: off\n")
    (checkout / ".config" / "pmcp").mkdir(parents=True)
    (checkout / ".config" / "pmcp" / "pmcp.env").write_text("PLANTED_372=1\n")
    (checkout / ".config" / "pmcp" / "provisioned.json").write_text(
        '{"planted-provisioned": null}'
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PLANTED_372", raising=False)
    monkeypatch.chdir(home / "app")
    home_identity.reset_home_warning()
    return checkout


def test_grok_r21_f001_a_repository_home_supplies_no_operator_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """grok r21 F001, verbatim in substance."""
    from pmcp.config.loader import load_configs
    from pmcp.manifest.loader import load_manifest
    from pmcp.policy.policy import PolicyManager

    _repository_home(tmp_path, monkeypatch)
    user_servers = [cfg.name for cfg in load_configs() if cfg.source == "user"]
    manifest = load_manifest()
    policy = PolicyManager()
    assert {
        "user_config": user_servers,
        "user_manifest": "planted-manifest" in manifest.servers,
        "repo_policy_denies": policy.is_server_allowed("from-repo-policy") is False,
    } == {"user_config": [], "user_manifest": False, "repo_policy_denies": False}


def test_every_home_scoped_file_is_refused_under_a_repository_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """User config, overlay, policy, guidance, user store, trust and approval
    stores, provisioned registry, registry cache, lock and setup target."""
    from unittest.mock import MagicMock

    from pmcp import home_identity, identity, trust_store
    from pmcp.config.guidance import load_guidance_config
    from pmcp.config.loader import default_user_config_paths
    from pmcp.manifest.registry import default_registry_cache_path
    from pmcp.package_approvals import package_approvals_path
    from pmcp.policy.policy import PolicyManager, default_user_policy_paths
    from pmcp.tools.handlers import GatewayTools

    _repository_home(tmp_path, monkeypatch)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert default_user_config_paths() == []
    assert default_user_policy_paths() == []
    assert load_guidance_config().level != "off"
    env_store.load_store("user")
    assert "PLANTED_372" not in os.environ
    with pytest.raises(trust_store.TrustStoreError):
        trust_store.trust_store_path()
    with pytest.raises(trust_store.TrustStoreError):
        package_approvals_path()
    tools = GatewayTools(client_manager=MagicMock(), policy_manager=PolicyManager())
    assert tools._load_provisioned_registry() == {}
    for refused in (
        default_registry_cache_path,
        lambda: identity.acquire_singleton_lock(None),
        lambda: cli._get_setup_target_path("claude"),
    ):
        with pytest.raises(home_identity.HomeInsideCheckoutError):
            refused()
    err = capsys.readouterr().err
    assert "the home directory lies inside a checkout" in err
    assert "pwned" not in err and "PLANTED_372" not in err


@pytest.mark.parametrize("home_kind", ["clean", "dotfiles"])
def test_an_operators_home_still_supplies_every_home_scoped_file(
    home_kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same files under a clean home, or a home kept in its own repository."""
    from unittest.mock import MagicMock

    from pmcp.config.loader import load_configs
    from pmcp.manifest.loader import load_manifest
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools

    home = Path(os.environ["HOME"]).resolve()
    if home_kind == "dotfiles":
        (home / ".git").mkdir(exist_ok=True)
    (home / ".mcp.json").write_text(
        '{"mcpServers":{"operator-user":{"command":"echo"}}}'
    )
    (home / ".pmcp").mkdir(exist_ok=True)
    (home / ".pmcp" / "manifest.yaml").write_text(
        "servers:\n  operator-manifest:\n    description: mine\n"
        "    keywords: [minewidget]\n    command: echo\n"
    )
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".claude" / "gateway-policy.yaml").write_text(
        'servers:\n  denylist: ["operator-denies"]\n'
    )
    (home / ".config" / "pmcp").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "pmcp" / "pmcp.env").write_text("OPERATOR_372=1\n")
    (home / ".config" / "pmcp" / "provisioned.json").write_text(
        '{"operator-provisioned": null}'
    )
    monkeypatch.delenv("OPERATOR_372", raising=False)
    work = tmp_path.resolve() / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    try:
        assert "operator-user" in {c.name for c in load_configs() if c.source == "user"}
        assert "operator-manifest" in load_manifest().servers
        assert PolicyManager().is_server_allowed("operator-denies") is False
        env_store.load_store("user")
        assert os.environ.get("OPERATOR_372") == "1"
        tools = GatewayTools(client_manager=MagicMock(), policy_manager=PolicyManager())
        assert "operator-provisioned" in tools._load_provisioned_registry()
    finally:
        os.environ.pop("OPERATOR_372", None)


def test_a_home_a_checkout_controls_does_not_end_project_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery from under such a home reaches the checkout, gated as a project."""
    from pmcp.config.loader import find_project_root

    checkout = tmp_path.resolve() / "checkout"
    (checkout / ".git").mkdir(parents=True)
    home = checkout / "home"
    (home / "sub").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    assert find_project_root(home / "sub") == checkout
    assert env_store.project_scope_root(home) == home


def test_an_env_reached_through_a_checkout_link_is_the_repositorys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identity says "an ancestor of home"; the path as written runs through a
    checkout, so the file is the repository's and stays out of the environment."""
    base = tmp_path.resolve()
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    repo = base / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "link").symlink_to(base, target_is_directory=True)
    (base / ".env").write_text("THROUGH_LINK_372=leaked\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("THROUGH_LINK_372", raising=False)
    monkeypatch.chdir(home)
    cli.load_startup_env(dotenv_path=str(repo / "link" / ".env"))
    assert "THROUGH_LINK_372" not in os.environ


# --------------------------------------------------------------------------- #
# Board round 22, claude N-1, folding in Consiliency/pmcp#374: the gate let an
# access through when HOME could not be examined, and trust_store_path still
# resolved with Path.resolve(), whose lexical `missing/..` collapse named the
# checkout. HOME=repo/missing/../home then let the repository's trust.json
# approve a .mcp.json outside it. The trust store now resolves as the writer
# does, and trust and approval decisions fail closed on an unexaminable HOME.
# --------------------------------------------------------------------------- #


def _planted_repo_store(base: Path) -> tuple[Path, Path, bytes]:
    """repo/home/.config/pmcp/trust.json approving a project OUTSIDE repo."""
    repo = base / "repo"
    (repo / ".git").mkdir(parents=True)
    store = repo / "home" / ".config" / "pmcp"
    store.mkdir(parents=True)
    outside = base / "outside"
    (outside / ".git").mkdir(parents=True)
    config = outside / ".mcp.json"
    content = b'{"mcpServers":{"planted":{"command":"echo"}}}'
    config.write_bytes(content)
    (store / "trust.json").write_text(_approval_for(config, content))
    return repo, config, content


def test_claude_r22_n1_a_home_spelled_through_missing_dotdot_grants_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pmcp import trust_store
    from pmcp.package_approvals import package_approvals_path

    base = tmp_path.resolve()
    repo, config, content = _planted_repo_store(base)
    monkeypatch.setenv("HOME", f"{repo}/missing/../home")
    monkeypatch.chdir(base / "outside")
    trust_store.reset_launch_directory()
    assert trust_store.is_approved(config, content, project_root=config.parent) is False
    # Round 24: a HOME with a `..` component is not plain, so not the operator's.
    with pytest.raises(trust_store.TrustStoreError, match="not a plain absolute path"):
        trust_store.trust_store_path()
    with pytest.raises(trust_store.TrustStoreError):
        package_approvals_path()
    err = capsys.readouterr().err
    assert "HOME is not a plain absolute path the system resolves" in err
    assert "planted" not in err


def test_a_home_the_system_cannot_resolve_supplies_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A HOME that does not exist is not the operator's (round 24): trust is
    refused, and so is every home-scoped file -- nothing is created there."""
    from pmcp import home_identity, trust_store
    from pmcp.config.loader import default_user_config_paths

    missing = tmp_path.resolve() / "no-such-home"
    monkeypatch.setenv("HOME", str(missing))
    with pytest.raises(trust_store.TrustStoreError, match="not a plain absolute path"):
        trust_store.trust_store_path()
    assert default_user_config_paths() == []
    assert env_store.read_store("user") == {}
    with pytest.raises(home_identity.HomeInsideCheckoutError):
        env_store.set_env_value("user", "MISSING_HOME_372", "inert")
    assert not missing.exists()


def test_a_trust_store_link_through_missing_dotdot_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Consiliency/pmcp#374, read side: the kernel says the link names nothing."""
    from pmcp import trust_store

    base = tmp_path.resolve()
    home = base / "home"
    pmcp_dir = home / ".config" / "pmcp"
    pmcp_dir.mkdir(parents=True)
    project = base / "project"
    (project / ".git").mkdir(parents=True)
    config = project / ".mcp.json"
    content = b'{"mcpServers":{"x":{"command":"echo"}}}'
    config.write_bytes(content)
    (pmcp_dir / "elsewhere.json").write_text(_approval_for(config, content))
    os.symlink("missing/../elsewhere.json", pmcp_dir / "trust.json")
    monkeypatch.setenv("HOME", str(home))
    assert not os.path.exists(pmcp_dir / "trust.json")
    assert trust_store.is_approved(config, content, project_root=project) is False


def test_an_unexaminable_home_owns_no_ancestor_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HOME=D/missing/../home: realpath says D/home, the kernel says nothing.

    Nothing derived from that spelling is the operator's, so D's `.env` is a
    repository file and stays out of the environment.
    """
    plain = tmp_path.resolve() / "plain"
    (plain / "home").mkdir(parents=True)
    (plain / ".env").write_text("SPELLED_HOME_372=leaked\n")
    monkeypatch.setenv("HOME", f"{plain}/missing/../home")
    monkeypatch.delenv("SPELLED_HOME_372", raising=False)
    monkeypatch.chdir(plain / "home")
    cli.load_startup_env(dotenv_path=str(plain / ".env"))
    assert "SPELLED_HOME_372" not in os.environ


# --------------------------------------------------------------------------- #
# Board round 23 (grok F001, codex F001-F003, claude N-1) -> round 24 ruling:
# HOME must be PLAIN -- absolute for the platform, no `.`/`..` component, and
# resolvable -- and is then judged on its kernel prefixes (a link held by a
# checkout refuses) and on the physical ancestors of realpath(HOME), HOME
# itself excluded. A filesystem root is never a checkout. The grid generates
# HOME shapes and checks each verdict, and what follows from it, end to end.
# --------------------------------------------------------------------------- #


def _shape(base: Path, name: str) -> str:
    """Build HOME shape ``name`` under ``base``; return the HOME spelling."""
    repo = base / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "child").mkdir()
    real = base / "real"
    real.mkdir()
    if name == "plain":
        return str(real)
    if name == "plain-trailing-slash":
        return f"{real}/"
    if name == "dotfiles-at-home":
        (real / ".git").mkdir()
        (real / ".mcp.json").write_text('{"mcpServers": {}}')
        return str(real)
    if name in (
        "operator-link",
        "operator-link-marked-target",
        "operator-link-relative",
    ):
        if name == "operator-link-marked-target":
            (real / ".git").mkdir()
            (real / ".mcp.json").write_text('{"mcpServers": {}}')
        target = "real" if name == "operator-link-relative" else str(real)
        (base / "alias").symlink_to(target, target_is_directory=True)
        return str(base / "alias")
    if name == "operator-link-to-a-checkout":
        (base / "alias").symlink_to(repo, target_is_directory=True)
        return str(base / "alias")
    if name == "repo-link-to-dot":
        (repo / "home").symlink_to(".", target_is_directory=True)
        return str(repo / "home")
    if name == "repo-link-to-child":
        (repo / "home").symlink_to("child", target_is_directory=True)
        return str(repo / "home")
    if name == "repo-link-to-sibling":
        (repo / "home").symlink_to("../real", target_is_directory=True)
        return str(repo / "home")
    if name == "repo-link-in-a-parent":
        (repo / "out").symlink_to(base / "real", target_is_directory=True)
        (real / "user").mkdir()
        return str(repo / "out" / "user")
    if name == "dotdot-before-a-link":
        (real / "sub").mkdir()
        (base / "alias").symlink_to(real, target_is_directory=True)
        return f"{real}/sub/../../alias"
    if name == "dotdot-after-a-link":
        (real / "user").mkdir()
        (base / "alias").symlink_to(real / "user", target_is_directory=True)
        return f"{base}/alias/.."
    if name == "dotdot-into-a-checkout":
        (repo / "home").mkdir()
        return f"{real}/../repo/home"
    if name == "dot-component":
        return f"{base}/./real"
    if name == "missing-then-dotdot":  # grok round 23
        (repo / "home").mkdir()
        return f"{real}/missing/../../repo/home"
    if name == "relative":
        return "real"
    if name == "unsearchable":
        (base / "locked" / "home").mkdir(parents=True)
        (base / "locked").chmod(0)
        return str(base / "locked" / "home")
    if name == "missing-inside-a-checkout":
        return str(repo / "not-created")
    if name == "missing-outside":
        return str(base / "not-created")
    if name == "checkout-one-level-up":
        (repo / "home").mkdir()
        return str(repo / "home")
    if name == "checkout-two-levels-up":
        (repo / "a" / "home").mkdir(parents=True)
        return str(repo / "a" / "home")
    if name == "operator-parent-link":  # /home -> /var/home (Silverblue)
        (base / "var" / "home" / "user").mkdir(parents=True)
        (base / "home").symlink_to("var/home", target_is_directory=True)
        return str(base / "home" / "user")
    if name == "operator-parent-link-marked-home":
        (base / "var" / "home" / "user").mkdir(parents=True)
        (base / "var" / "home" / "user" / ".mcp.json").write_text("{}")
        (base / "home").symlink_to(base / "var" / "home", target_is_directory=True)
        return str(base / "home" / "user")
    if name == "operator-parent-link-macos":  # /var -> private/var
        (base / "private" / "var" / "users" / "me").mkdir(parents=True)
        (base / "var").symlink_to("private/var", target_is_directory=True)
        return str(base / "var" / "users" / "me")
    if name == "parent-link-into-a-checkout":
        (repo / "homes" / "user").mkdir(parents=True)
        (base / "home").symlink_to(repo / "homes", target_is_directory=True)
        return str(base / "home" / "user")
    raise AssertionError(name)


_PLAIN = "HOME is not a plain absolute path the system resolves"
_CHECKOUT = "the home directory lies inside a checkout"

#: shape -> None (the operator's) or the refusal it gets.
HOME_SHAPES: dict[str, str | None] = {
    "plain": None,
    "plain-trailing-slash": None,
    "dotfiles-at-home": None,
    "operator-link": None,
    "operator-link-marked-target": None,
    "operator-link-relative": None,
    "operator-link-to-a-checkout": None,
    "operator-parent-link": None,
    "operator-parent-link-marked-home": None,
    "operator-parent-link-macos": None,
    "repo-link-to-dot": _CHECKOUT,
    "repo-link-to-child": _CHECKOUT,
    "repo-link-to-sibling": _CHECKOUT,
    "repo-link-in-a-parent": _CHECKOUT,
    "checkout-one-level-up": _CHECKOUT,
    "checkout-two-levels-up": _CHECKOUT,
    "parent-link-into-a-checkout": _CHECKOUT,
    "dotdot-before-a-link": _PLAIN,
    "dotdot-after-a-link": _PLAIN,
    "dotdot-into-a-checkout": _PLAIN,
    "dot-component": _PLAIN,
    "missing-then-dotdot": _PLAIN,
    "relative": _PLAIN,
    "unsearchable": _PLAIN,
    "missing-inside-a-checkout": _PLAIN,
    "missing-outside": _PLAIN,
}


@pytest.mark.parametrize("shape", sorted(HOME_SHAPES))
def test_every_home_shape_follows_the_rule(
    shape: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pmcp import home_identity, trust_store

    if shape == "unsearchable" and (os.name != "posix" or os.geteuid() == 0):
        pytest.skip("needs POSIX search permissions and a non-root process")
    base = tmp_path.resolve()
    assert all(not home_identity.has_checkout_marker(d) for d in base.parents)
    spelled = _shape(base, shape)
    monkeypatch.chdir(base)
    monkeypatch.setenv("HOME", spelled)
    monkeypatch.setattr(env_store, "_PINNED_USER_STORE", None)
    monkeypatch.delenv("GRID_TOKEN_372", raising=False)
    home_identity.reset_home_warning()
    trust_store.reset_launch_directory()
    refusal = HOME_SHAPES[shape]
    try:
        assert home_identity.home_is_operators() is (refusal is None)
        assert (home_identity.examinable_home() is not None) is (refusal is None)
        if refusal is None:
            assert home_identity.optional_operator_home() == Path(spelled)
            assert trust_store.trust_store_path() is not None
            env_store.set_env_value("user", "GRID_TOKEN_372", "inert")
            assert env_store.read_store("user") == {"GRID_TOKEN_372": "inert"}
            return
        before = set(base.rglob("*"))
        assert home_identity.optional_operator_home() is None
        assert capsys.readouterr().err.count(refusal) == 1
        with pytest.raises(trust_store.TrustStoreError):
            trust_store.trust_store_path()
        with pytest.raises(home_identity.HomeInsideCheckoutError, match=refusal):
            env_store.set_env_value("user", "GRID_TOKEN_372", "inert")
        assert env_store.read_store("user") == {}
        assert set(base.rglob("*")) == before
    finally:
        if (base / "locked").exists():
            (base / "locked").chmod(0o700)


def test_the_home_grid_covers_every_shape_the_rulings_name() -> None:
    named = {
        # round 23
        "plain",
        "dotfiles-at-home",
        "operator-link",
        "operator-link-marked-target",
        "repo-link-to-dot",
        "repo-link-to-child",
        "repo-link-to-sibling",
        "dotdot-before-a-link",
        "dotdot-after-a-link",
        "missing-inside-a-checkout",
        "missing-outside",
        "checkout-one-level-up",
        "checkout-two-levels-up",
        "operator-parent-link",
        # round 24: odd spellings
        "relative",
        "dot-component",
        "unsearchable",
        "missing-then-dotdot",
    }
    assert named <= set(HOME_SHAPES)
    assert set(HOME_SHAPES.values()) == {None, _PLAIN, _CHECKOUT}


def test_a_root_is_never_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """claude round 23 N-1: a container image with `/package.json` (`COPY . /`)
    and HOME=/root keeps the operator's home files and its residency walk."""
    from pmcp import home_identity, trust_store

    home = tmp_path.resolve() / "root"
    home.mkdir()
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if os.fspath(path) in ("/package.json", "/.git"):
            return real_lstat("/", *args, **kwargs)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(home_identity.os, "lstat", lstat)
    monkeypatch.setenv("HOME", str(home))
    assert os.lstat("/package.json")  # the image's marker is there
    assert home_identity.has_checkout_marker("/") is False
    assert home_identity.home_is_operators() is True
    assert Path("/") not in list(home_identity.enclosing_checkouts(home))
    assert trust_store.trust_store_path() is not None


# Windows, by the platform's own path module (codex round 23 F002): `\`
# separates, a drive root is a root, and a drive-relative path is relative.


def _windows_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: str
) -> None:
    import ntpath
    from pathlib import PureWindowsPath
    from types import SimpleNamespace

    from pmcp import home_identity

    class WindowsPath(PureWindowsPath):
        @classmethod
        def home(cls):  # type: ignore[no-untyped-def]
            return cls(home)

    def local(path):  # type: ignore[no-untyped-def]
        return tmp_path.joinpath(*PureWindowsPath(path).parts[1:])

    path_module = SimpleNamespace(
        **{n: getattr(ntpath, n) for n in dir(ntpath) if not n.startswith("__")}
    )
    path_module.expanduser = lambda spelled: home if spelled == "~" else spelled
    path_module.realpath = ntpath.abspath
    windows_os = SimpleNamespace(
        path=path_module,
        fspath=os.fspath,
        getcwd=lambda: "C:\\",
        lstat=lambda path: os.lstat(local(path)),
        stat=lambda path: os.stat(local(path)),
    )
    monkeypatch.setattr(home_identity, "Path", WindowsPath)
    monkeypatch.setattr(home_identity, "os", windows_os)


@pytest.mark.parametrize(
    ("home", "operators"),
    [
        ("C:\\Users\\me", True),
        ("C:/Users/me", True),
        ("C:\\checkout\\home", False),
        ("C:/checkout/home", False),
        ("C:\\Users\\..\\checkout\\home", False),
        ("C:\\Users\\.\\me", False),
        ("Users\\me", False),
        ("C:Users\\me", False),  # drive-relative
        ("C:\\Users\\nobody", False),  # does not resolve
    ],
)
def test_a_windows_home_follows_the_rule(
    home: str, operators: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import home_identity

    (tmp_path / "checkout" / ".git").mkdir(parents=True)
    (tmp_path / "checkout" / "home").mkdir()
    (tmp_path / "Users" / "me").mkdir(parents=True)
    _windows_platform(tmp_path, monkeypatch, home)
    assert home_identity.has_checkout_marker("C:\\checkout")
    assert home_identity.has_checkout_marker("C:\\") is False
    assert home_identity.home_is_operators() is operators


# The seats' falsifiers, as filed (their ancestor-marker patches kept).


def _no_markers_above(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pmcp import home_identity

    marker = home_identity.has_checkout_marker
    ancestors = set(tmp_path.resolve().parents)
    monkeypatch.setattr(
        home_identity,
        "has_checkout_marker",
        lambda path: False if Path(path) in ancestors else marker(path),
    )


def test_grok_r22_f001_unstatable_home_inside_a_checkout_is_not_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.atomic_write import atomic_write, make_store_dirs
    from pmcp.home_identity import (
        HomeInsideCheckoutError,
        home_is_operators,
        home_path,
        reset_home_warning,
    )

    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    home = checkout / "home"
    monkeypatch.setenv("HOME", str(home))
    reset_home_warning()
    assert home_is_operators() is False
    try:
        target = home_path(".config", "pmcp", "pmcp.env")
    except HomeInsideCheckoutError:
        return
    make_store_dirs(target.parent)
    atomic_write(target, b"SECRET_TOKEN=leak\n", confine_to=None, mode=0o600)
    raise AssertionError(f"user store written inside the checkout at {target}")


def test_codex_r22_f001_repository_home_parent_link_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import home_identity

    _no_markers_above(tmp_path, monkeypatch)
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "child").mkdir()
    link = checkout / "home"
    link.symlink_to("child", target_is_directory=True)
    (checkout / ".mcp.json").write_text('{"mcpServers": {}}')
    monkeypatch.setenv("HOME", str(link))
    assert home_identity.optional_operator_home() is None
    monkeypatch.setenv("HOME", str(link / ".."))
    assert Path.home().resolve() == checkout.resolve()
    assert home_identity.optional_home_path(".mcp.json") is None


def test_codex_r22_f002_operator_home_symlink_still_works_with_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import home_identity

    _no_markers_above(tmp_path, monkeypatch)
    home = tmp_path / "operator-home"
    home.mkdir()
    alias = tmp_path / "operator-home-link"
    alias.symlink_to(home, target_is_directory=True)
    monkeypatch.setenv("HOME", str(alias))
    assert home_identity.optional_operator_home() == alias
    (home / ".mcp.json").write_text('{"mcpServers": {}}')
    assert home_identity.optional_home_path(".mcp.json") == alias / ".mcp.json"


def test_codex_r22_f003_missing_home_inside_checkout_cannot_receive_user_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import home_identity

    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    home = checkout / "not-created"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(env_store, "_PINNED_USER_STORE", None)
    assert not home.exists()
    try:
        env_store.set_env_value("user", "F003_TOKEN", "inert")
    except home_identity.HomeInsideCheckoutError:
        pass
    assert not (home / ".config" / "pmcp" / "pmcp.env").exists()


def test_gemini_r22_f001_operator_symlink_home_with_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.home_identity import home_is_operators, operator_home

    real_home = tmp_path / "real_home"
    real_home.mkdir()
    (real_home / ".mcp.json").write_text("{}", encoding="utf-8")
    symlink_home = tmp_path / "symlink_home"
    symlink_home.symlink_to(real_home)
    monkeypatch.setenv("HOME", str(symlink_home))
    assert home_is_operators() is True
    assert operator_home() == symlink_home


def test_the_operators_home_ends_project_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counterpart of a controlled home: the operator's own `~/.mcp.json`
    is user config, never a project root found from below it."""
    from pmcp.config.loader import find_project_root

    home = tmp_path.resolve() / "home"
    (home / "work").mkdir(parents=True)
    (home / ".mcp.json").write_text('{"mcpServers": {}}')
    monkeypatch.setenv("HOME", str(home))
    assert find_project_root(home / "work") is None


# Board round 23's falsifiers, as filed.


def test_grok_r23_f001_missing_then_dotdot_home_does_not_write_into_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.config.guidance import set_feedback_submission_enabled
    from pmcp.home_identity import (
        HomeInsideCheckoutError,
        has_checkout_marker,
        reset_home_warning,
    )

    base = tmp_path.resolve()
    ancestors = set(base.parents)
    monkeypatch.setattr(
        "pmcp.home_identity.has_checkout_marker",
        lambda path: False if Path(path) in ancestors else has_checkout_marker(path),
    )
    repo = base / "repo"
    landing = repo / "home" / ".claude"
    landing.mkdir(parents=True)
    (repo / ".git").mkdir()
    outside = base / "outside-secret"
    outside.write_text("ORIGINAL\n", encoding="utf-8")
    (landing / "gateway-guidance.yaml").symlink_to(outside)
    safe = base / "safe"
    safe.mkdir()
    home = os.path.join(
        str(safe), "missing", "..", "..", "..", base.name, "repo", "home"
    )
    monkeypatch.setenv("HOME", home)
    reset_home_warning()
    try:
        set_feedback_submission_enabled(False)
    except (HomeInsideCheckoutError, OSError):
        pass
    assert outside.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert not (safe / "missing").exists()
    assert (landing / "gateway-guidance.yaml").is_symlink()


def _fresh_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(env_store, "_PINNED_USER_STORE", None)
    monkeypatch.setattr(env_store, "_DEFAULT_ROOT", None)
    monkeypatch.setattr(env_store, "_STARTUP_LOADED", False)
    monkeypatch.setattr(env_store, "_REPO_CREDENTIALS", {})


def test_codex_r23_f001_relative_home_cannot_load_checkout_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    work = checkout / "subdirectory"
    store = work / "home" / ".config" / "pmcp" / "pmcp.env"
    store.parent.mkdir(parents=True)
    store.write_text("PMCP_PORT=45123\n")
    monkeypatch.chdir(work)
    _fresh_startup(monkeypatch)
    monkeypatch.setenv("HOME", "home")
    monkeypatch.delenv("PMCP_PORT", raising=False)
    cli.load_startup_env(dotenv_path=str(tmp_path / "absent.env"))
    assert "PMCP_PORT" not in os.environ


def test_codex_r23_f002_windows_home_walk_checks_checkout_ancestors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ntpath
    from pathlib import PureWindowsPath
    from types import SimpleNamespace

    from pmcp import home_identity

    (tmp_path / "checkout" / ".git").mkdir(parents=True)
    (tmp_path / "checkout" / "home").mkdir()

    class WindowsPath(PureWindowsPath):
        @classmethod
        def home(cls):  # type: ignore[no-untyped-def]
            return cls("C:/checkout/home")

    def local(path):  # type: ignore[no-untyped-def]
        return tmp_path.joinpath(*PureWindowsPath(path).parts[1:])

    windows_os = SimpleNamespace(
        path=ntpath,
        fspath=os.fspath,
        getcwd=lambda: "C:/",
        lstat=lambda path: os.lstat(local(path)),
        stat=lambda path: os.stat(local(path)),
        readlink=lambda path: os.readlink(local(path)),
    )
    monkeypatch.setattr(home_identity, "Path", WindowsPath)
    monkeypatch.setattr(home_identity, "os", windows_os)
    assert home_identity.has_checkout_marker(WindowsPath("C:/checkout"))
    assert home_identity.home_is_operators() is False


def test_codex_r23_f003_unsearchable_dotdot_home_cannot_load_checkout_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import home_identity

    if os.name != "posix" or os.geteuid() == 0:
        pytest.skip("requires POSIX search permissions and a non-root process")
    checkout = tmp_path / "markerless-checkout"
    locked = checkout / "locked"
    locked.mkdir(parents=True)
    (checkout / "home").mkdir()
    (checkout / "requirements.txt").write_text("")
    dotenv = checkout / ".env"
    dotenv.write_text("PMCP_PORT=45123\n")
    marker = home_identity.has_checkout_marker
    outside = set(tmp_path.resolve().parents)
    monkeypatch.setattr(
        home_identity,
        "has_checkout_marker",
        lambda path: False if Path(path) in outside else marker(path),
    )
    _fresh_startup(monkeypatch)
    monkeypatch.setenv("HOME", str(locked / ".." / "home"))
    monkeypatch.delenv("PMCP_PORT", raising=False)
    locked.chmod(0)
    try:
        with pytest.raises(PermissionError):
            os.stat(Path.home())
        cli.load_startup_env(dotenv_path=str(dotenv))
        assert "PMCP_PORT" not in os.environ
    finally:
        locked.chmod(0o700)


def test_a_prefix_the_system_refuses_to_examine_refuses_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 2: every prefix of HOME is lstat-ed, and any error refuses -- even
    one the earlier stat of HOME did not see (a directory changed between)."""
    from pmcp import home_identity

    home = tmp_path.resolve() / "user"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    assert home_identity.home_is_operators() is True
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if os.fspath(path) == str(home.parent):
            raise PermissionError(13, "Permission denied")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(home_identity.os, "lstat", lstat)
    assert home_identity.home_is_operators() is False
    assert home_identity.optional_operator_home() is None


# --------------------------------------------------------------------------- #
# The home verdict is cached per process (suite and request time; see
# Consiliency/pmcp#372). A cached verdict is reused only while everything it
# read still reads the same; these are the changes that must invalidate it.
# --------------------------------------------------------------------------- #


def _accepted_then(
    monkeypatch: pytest.MonkeyPatch, home: str, change: Callable[[], None]
) -> bool:
    from pmcp import home_identity

    monkeypatch.setenv("HOME", home)
    home_identity.forget_home_verdicts()
    assert home_identity.home_is_operators() is True
    assert home_identity.home_is_operators() is True  # served from the cache
    change()
    return home_identity.home_is_operators()


def test_the_cached_verdict_follows_a_change_of_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path.resolve()
    (base / "plain").mkdir()
    (base / "repo" / ".git").mkdir(parents=True)
    (base / "repo" / "home").mkdir()
    assert (
        _accepted_then(
            monkeypatch,
            str(base / "plain"),
            lambda: monkeypatch.setenv("HOME", str(base / "repo" / "home")),
        )
        is False
    )


def test_the_cached_verdict_follows_a_retargeted_prefix_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path.resolve()
    (base / "real").mkdir()
    (base / "repo" / ".git").mkdir(parents=True)
    (base / "repo" / "child").mkdir()
    (base / "alias").symlink_to(base / "real", target_is_directory=True)

    def retarget() -> None:
        (base / "alias.new").symlink_to(
            base / "repo" / "child", target_is_directory=True
        )
        os.replace(base / "alias.new", base / "alias")

    assert _accepted_then(monkeypatch, str(base / "alias"), retarget) is False


def test_the_cached_verdict_follows_a_link_inside_a_link_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HOME's own prefixes are unchanged; a link its target passes through is
    retargeted into a checkout."""
    base = tmp_path.resolve()
    (base / "safe" / "home").mkdir(parents=True)
    (base / "repo" / ".git").mkdir(parents=True)
    (base / "repo" / "home").mkdir()
    # The link lives in a directory no verdict examines for markers, so only
    # reading again where HOME leads can notice it changed.
    (base / "links").mkdir()
    (base / "links" / "mid").symlink_to(base / "safe", target_is_directory=True)
    (base / "alias").symlink_to(
        base / "links" / "mid" / "home", target_is_directory=True
    )

    def retarget() -> None:
        new = base / "links" / "mid.new"
        new.symlink_to(base / "repo", target_is_directory=True)
        os.replace(new, base / "links" / "mid")

    assert _accepted_then(monkeypatch, str(base / "alias"), retarget) is False


@pytest.mark.parametrize(
    "marker", [".git", ".mcp.json", "package.json", "pyproject.toml", "pmcp-overlay"]
)
def test_the_cached_verdict_follows_a_marker_added_above_home(
    marker: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path.resolve()
    (base / "x" / "home").mkdir(parents=True)
    if marker == "pmcp-overlay":
        # The .pmcp directory exists already: only its own entries change.
        (base / "x" / ".pmcp").mkdir()

        def add() -> None:
            (base / "x" / ".pmcp" / "manifest.yaml").write_text("servers: {}\n")

    else:

        def add() -> None:
            (base / "x" / marker).mkdir()

    assert _accepted_then(monkeypatch, str(base / "x" / "home"), add) is False


def test_the_cached_verdict_follows_a_home_that_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path.resolve()
    (base / "home").mkdir()
    assert (
        _accepted_then(monkeypatch, str(base / "home"), (base / "home").rmdir) is False
    )


def test_trust_decisions_never_use_a_cached_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a cache that failed to notice a change cannot carry into a trust
    decision: trust judges HOME afresh."""
    from pmcp import home_identity, trust_store

    base = tmp_path.resolve()
    (base / "x" / "home").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(base / "x" / "home"))
    assert home_identity.home_is_operators() is True
    (base / "x" / ".git").mkdir()
    # A cache that never invalidates:
    monkeypatch.setattr(home_identity, "_still_seen", lambda reads: True)
    assert home_identity.home_is_operators() is True  # stale, by construction
    with pytest.raises(trust_store.TrustStoreError, match="lies inside"):
        trust_store.trust_store_path()
    # The residency roots, too, are judged afresh.
    (base / "y" / "home").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(base / "y" / "home"))
    assert home_identity.home_is_operators() is True
    (base / "y" / ".git").mkdir()
    assert home_identity.home_is_operators() is True  # stale, by construction
    trust_store._checkout_roots()
    assert home_identity.home_is_operators() is False  # judged afresh there


def test_the_cached_verdict_rereads_where_a_link_leads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same directory reached at a new physical path (a bind mount: no
    directory pmcp examined changes, HOME's identity is the same). Only
    reading again where the link leads can notice; simulated at the seam."""
    from pmcp import home_identity

    base = tmp_path.resolve()
    (base / "safe" / "home").mkdir(parents=True)
    (base / "repo" / ".git").mkdir(parents=True)
    (base / "repo" / "mount").mkdir()
    (base / "alias").symlink_to(base / "safe" / "home", target_is_directory=True)
    real_realpath = os.path.realpath

    def bind_mounted() -> None:
        def realpath(path: Any, *args: Any, **kwargs: Any) -> str:
            if os.fspath(path) == str(base / "alias"):
                return str(base / "repo" / "mount" / "home")
            return real_realpath(path, *args, **kwargs)

        monkeypatch.setattr(home_identity.os.path, "realpath", realpath)

    assert _accepted_then(monkeypatch, str(base / "alias"), bind_mounted) is False


def _frozen_time_os(kind: str) -> Any:
    """``os`` for home_identity whose stat results carry timestamps a
    filesystem might report: ``windows`` -- ``st_ctime`` is the creation time
    and ``st_mtime`` is not updated for a new entry; ``frozen`` -- every
    timestamp fixed. Identity and type are real."""
    from types import SimpleNamespace

    def frozen(result: os.stat_result) -> Any:
        fields = {
            name: getattr(result, name)
            for name in dir(result)
            if name.startswith("st_")
        }
        for name in ("st_mtime", "st_mtime_ns", "st_atime", "st_atime_ns"):
            fields[name] = 0
        if kind == "frozen":
            fields["st_ctime"] = fields["st_ctime_ns"] = 0
        else:  # windows: ctime is the birth time, fixed at creation
            fields["st_ctime_ns"] = getattr(result, "st_birthtime_ns", 1)
            fields["st_ctime"] = fields["st_ctime_ns"] / 1e9
        return SimpleNamespace(**fields)

    return SimpleNamespace(
        path=os.path,
        fspath=os.fspath,
        getcwd=os.getcwd,
        environ=os.environ,
        stat=lambda path, *a, **k: frozen(os.stat(path, *a, **k)),
        lstat=lambda path, *a, **k: frozen(os.lstat(path, *a, **k)),
    )


@pytest.mark.parametrize("kind", ["windows", "frozen"])
@pytest.mark.parametrize(
    "marker", [".git", ".mcp.json", "package.json", "pyproject.toml", "pmcp-overlay"]
)
def test_a_marker_above_home_invalidates_whatever_the_timestamps(
    kind: str, marker: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex round 25 F001: on Windows ``st_ctime`` is the creation time, so a
    folder's timestamps need not change when a marker is created in it. The
    cache must see the marker itself."""
    from pmcp import home_identity

    base = tmp_path.resolve()
    (base / "x" / "home").mkdir(parents=True)
    if marker == "pmcp-overlay":
        (base / "x" / ".pmcp").mkdir()
    monkeypatch.setattr(home_identity, "os", _frozen_time_os(kind))

    def add() -> None:
        if marker == "pmcp-overlay":
            (base / "x" / ".pmcp" / "manifest.yaml").write_text("servers: {}\n")
        else:
            (base / "x" / marker).mkdir()

    assert _accepted_then(monkeypatch, str(base / "x" / "home"), add) is False


# --------------------------------------------------------------------------- #
# Board round 26, codex F001: an operator `alias -> <checkout>/home` with a
# repository `home -> .` landed HOME on the checkout root, which "HOME itself"
# then exempted. Every link met while resolving HOME, at any depth, must be
# held outside every checkout.
# --------------------------------------------------------------------------- #


def test_codex_r26_f001_nested_home_link_cannot_self_approve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import json

    from pmcp import home_identity, trust_store

    base = tmp_path.resolve()
    checkout = base / "checkout"
    (checkout / ".git").mkdir(parents=True)
    inner = checkout / "home"
    inner.symlink_to(".", target_is_directory=True)
    outer = base / "operator-home"
    outer.symlink_to(inner, target_is_directory=True)
    project = checkout / "app"
    project.mkdir()
    config = project / ".mcp.json"
    content = b'{"mcpServers":{"unapproved":{"command":"echo"}}}'
    config.write_bytes(content)
    store = checkout / ".config" / "pmcp"
    store.mkdir(parents=True)
    (store / "trust.json").write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "absolute_path": str(config),
                        "content_sha256": hashlib.sha256(content).hexdigest(),
                        "scope": "user",
                        "decision": "approved",
                        "recorded_at": "2026-10-07T00:00:00+00:00",
                    }
                ],
            }
        )
    )
    launch = base / "launch"
    launch.mkdir()
    original_marker = home_identity.has_checkout_marker
    ambient = set(base.parents)
    monkeypatch.setattr(
        home_identity,
        "has_checkout_marker",
        lambda path: False if Path(path) in ambient else original_marker(path),
    )
    monkeypatch.chdir(launch)
    monkeypatch.setattr(trust_store, "_LAUNCH_DIRECTORY", None)
    monkeypatch.setattr(trust_store, "_active_project_root", None)
    monkeypatch.setenv("HOME", str(inner))
    home_identity.forget_home_verdicts()
    assert not trust_store.is_approved(config, content, project_root=project)
    monkeypatch.setenv("HOME", str(outer))
    home_identity.forget_home_verdicts()
    assert not trust_store.is_approved(config, content, project_root=project), (
        "An outer symlink let the checkout's planted trust.json approve itself"
    )


def _chain_rows() -> list[tuple[tuple[str, ...], tuple[str, ...], str]]:
    """(holder per link, target style per link, final) for chains of 1-3 links."""
    rows = []
    for depth in (1, 2, 3):
        for holders in itertools.product(("in", "out"), repeat=depth):
            for styles in itertools.product(("abs", "rel", "dotdot"), repeat=depth):
                for final in ("out/dest", "repo/sub", "repo"):
                    rows.append((holders, styles, final))
    return rows


def _build_chain(
    case: Path, holders: tuple[str, ...], styles: tuple[str, ...], final: str
) -> Path:
    """Build the chain under ``case``; return HOME (the first link)."""
    (case / "repo" / ".git").mkdir(parents=True)
    (case / "repo" / "sub").mkdir()
    (case / "out" / "dest").mkdir(parents=True)
    held = {"in": case / "repo", "out": case / "out"}
    links = [held[h] / f"l{i}" for i, h in enumerate(holders)]
    targets = [*links[1:], case / final]
    for link, target, style in zip(links, targets, styles):
        directory = link.parent
        if style == "abs":
            text = str(target)
        elif style == "rel":
            text = os.path.relpath(target, directory)
        else:  # `..` in the target: up out of the holder, then back down
            text = os.path.join(
                "..", directory.name, os.path.relpath(target, directory)
            )
        link.symlink_to(text, target_is_directory=True)
    return links[0]


def test_every_link_in_a_nested_chain_is_held_outside_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generated: chains of 1-3 links, each held inside or outside a checkout,
    with absolute, relative and `..` targets, ending outside a checkout, in
    one, or on the checkout root itself. The oracle: refused if and only if a
    link on the way is held in a checkout, or HOME lands inside one (landing
    ON the root through the operator's own links is a dotfiles HOME)."""
    from pmcp import home_identity

    base = tmp_path.resolve()
    assert all(not home_identity.has_checkout_marker(d) for d in base.parents)
    wrong = []
    rows = _chain_rows()
    for n, (holders, styles, final) in enumerate(rows):
        case = base / f"c{n}"
        home = _build_chain(case, holders, styles, final)
        assert os.path.realpath(home) == str(case / final)
        expected = not ("in" in holders or final == "repo/sub")
        monkeypatch.setenv("HOME", str(home))
        home_identity.forget_home_verdicts()
        fresh = home_identity.home_is_operators()
        cached = home_identity.home_is_operators()
        if fresh is not expected or cached is not expected:
            wrong.append((holders, styles, final, fresh, cached))
    assert len(rows) == 774
    assert wrong == []


def test_a_recreated_link_is_read_again_even_with_its_old_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link replaced by one with a new target can reuse the old inode
    number. The cached verdict re-reads every link's text, so the new
    target -- here through a repository link, landing on the same checkout
    root -- is judged afresh."""
    from pmcp import home_identity

    base = tmp_path.resolve()
    repo = base / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "home").symlink_to(".", target_is_directory=True)
    alias = base / "alias"
    alias.symlink_to(repo, target_is_directory=True)  # a dotfiles HOME
    old = os.lstat(alias)
    real_lstat = os.lstat

    def reuse_the_inode() -> None:
        new = base / "alias.new"
        new.symlink_to(repo / "home", target_is_directory=True)
        os.replace(new, alias)

        def lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
            if os.fspath(path) == str(alias):
                return old
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(home_identity.os, "lstat", lstat)

    assert os.path.realpath(alias) == str(repo)
    assert _accepted_then(monkeypatch, str(alias), reuse_the_inode) is False


def test_the_walk_and_the_system_must_agree_on_where_home_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``realpath`` is the oracle; a walk that ends anywhere else refuses."""
    from pmcp import home_identity

    base = tmp_path.resolve()
    (base / "a").mkdir()
    (base / "b").mkdir()
    (base / "alias").symlink_to(base / "a", target_is_directory=True)
    monkeypatch.setenv("HOME", str(base / "alias"))
    home_identity.forget_home_verdicts()
    assert home_identity.home_is_operators() is True
    real_realpath = os.path.realpath

    def realpath(path: Any, *args: Any, **kwargs: Any) -> str:
        if os.fspath(path) == str(base / "alias"):
            return str(base / "b")
        return real_realpath(path, *args, **kwargs)

    monkeypatch.setattr(home_identity.os.path, "realpath", realpath)
    home_identity.forget_home_verdicts()
    assert home_identity.home_is_operators() is False


# --------------------------------------------------------------------------- #
# Board round 27, codex F001: a Windows junction (a directory by mode) hid a
# repository link's holder -- the holder was checked where the walk spelled it,
# never where it physically is. Every holder is now judged at realpath(holder),
# and a reparse point the walk meets is collected as a link.
# --------------------------------------------------------------------------- #


def _windows_seam(  # type: ignore[no-untyped-def]
    base: Path,
    junctions: set[Path],
    unreadable: set[Path] = set(),  # noqa: B006
    tags: dict[Path, int] | None = None,
    texts: dict[Path, str] | None = None,
):
    """``os`` for home_identity on ntpath over the real tree under ``base``:
    ``C:\\`` is ``base``; each path in ``junctions`` is a real symlink that
    ``lstat`` reports as a directory with a mount-point reparse tag. ``tags``
    gives a real directory a reparse tag; ``texts`` gives an entry the link
    text ``readlink`` returns (a volume mount point's ``\\\\?\\Volume{..}\\``)."""
    import ntpath
    import stat as stat_module
    from pathlib import PureWindowsPath
    from types import SimpleNamespace

    tags = tags or {}
    texts = texts or {}

    def local(path: str) -> Path:
        return base.joinpath(*PureWindowsPath(path).parts[1:])

    def windows(path: Path) -> str:
        return str(PureWindowsPath("C:/") / path.relative_to(base))

    def readlink(path: str) -> str:
        entry = local(path)
        if entry in unreadable:
            raise PermissionError(13, "Permission denied")
        if entry in texts:
            return texts[entry]
        target = Path(os.readlink(entry))
        return windows(target) if target.is_absolute() else str(PureWindowsPath(target))

    def lstat(path: str) -> Any:
        entry = local(path)
        seen = os.lstat(entry)
        if entry in junctions or entry in tags:
            return SimpleNamespace(
                st_dev=seen.st_dev,
                st_ino=seen.st_ino,
                st_mode=stat_module.S_IFDIR | 0o755,
                st_file_attributes=0x400,
                st_reparse_tag=tags.get(entry, 0xA0000003),
            )
        return seen

    paths = SimpleNamespace(**vars(ntpath))
    paths.realpath = lambda path: windows(local(path).resolve())
    return (
        SimpleNamespace(
            path=paths,
            fspath=os.fspath,
            stat=lambda path: os.stat(local(path)),
            lstat=lstat,
            readlink=readlink,
        ),
        windows,
    )


def test_codex_r27_f001_junction_cannot_hide_checkout_held_home_link(
    tmp_path: Path,
) -> None:
    import ntpath
    import stat as stat_module
    from pathlib import PureWindowsPath
    from types import SimpleNamespace
    from unittest.mock import patch

    from pmcp import home_identity

    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    held = checkout / "held"
    held.mkdir()
    (held / "home").symlink_to("../../checkout", target_is_directory=True)
    (tmp_path / "outside").mkdir()
    junction = tmp_path / "outside" / "junction"
    junction.symlink_to(held, target_is_directory=True)

    def local(path):  # type: ignore[no-untyped-def]
        return tmp_path.joinpath(*PureWindowsPath(path).parts[1:])

    def windows(path):  # type: ignore[no-untyped-def]
        return str(PureWindowsPath("C:/") / path.relative_to(tmp_path))

    def readlink(path):  # type: ignore[no-untyped-def]
        target = Path(os.readlink(local(path)))
        return windows(target) if target.is_absolute() else str(PureWindowsPath(target))

    def lstat(path):  # type: ignore[no-untyped-def]
        entry = local(path)
        seen = os.lstat(entry)
        if entry == junction:
            return SimpleNamespace(
                st_dev=seen.st_dev,
                st_ino=seen.st_ino,
                st_mode=stat_module.S_IFDIR | 0o755,
                st_file_attributes=0x400,
                st_reparse_tag=0xA0000003,
            )
        return seen

    paths = SimpleNamespace(**vars(ntpath))
    paths.realpath = lambda path: windows(local(path).resolve())
    windows_os = SimpleNamespace(
        path=paths,
        fspath=os.fspath,
        stat=lambda path: os.stat(local(path)),
        lstat=lstat,
        readlink=readlink,
    )
    home = windows(junction / "home")
    with (
        patch.object(home_identity, "os", windows_os),
        patch.object(home_identity, "_home_spelling", return_value=home),
    ):
        home_identity.forget_home_verdicts()
        try:
            assert paths.realpath(home) == windows(checkout)
            assert home_identity._marked_from(windows(held)) == windows(checkout)
            assert not home_identity.home_is_operators(), (
                "A junction hid the checkout that holds HOME's final symlink"
            )
        finally:
            home_identity.forget_home_verdicts()


@pytest.mark.parametrize(
    ("shape", "operators"),
    [
        ("junction-into-a-checkout-held-link", False),  # codex's shape
        ("junction-to-an-operator-directory", True),
        ("junction-to-a-dotfiles-checkout-root", True),
        ("junction-inside-a-checkout", False),
        ("junction-whose-target-cannot-be-read", False),
        ("chain-junction-then-link-then-junction", False),
    ],
)
def test_a_windows_junction_is_a_link_on_the_way(
    shape: str, operators: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import home_identity

    base = tmp_path.resolve()
    repo = base / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "held").mkdir()
    (base / "outside").mkdir()
    (base / "users" / "me").mkdir(parents=True)
    junction = base / "outside" / "junction"
    junctions = {junction}
    unreadable: set[Path] = set()
    if shape == "junction-into-a-checkout-held-link":
        (repo / "held" / "home").symlink_to("../../repo", target_is_directory=True)
        junction.symlink_to(repo / "held", target_is_directory=True)
        home = junction / "home"
    elif shape == "junction-to-an-operator-directory":
        junction.symlink_to(base / "users", target_is_directory=True)
        home = junction / "me"
    elif shape == "junction-to-a-dotfiles-checkout-root":
        junction.symlink_to(repo, target_is_directory=True)
        home = junction
    elif shape == "junction-inside-a-checkout":
        inner = repo / "jn"
        inner.symlink_to(base / "users", target_is_directory=True)
        junctions = {inner}
        home = inner / "me"
    elif shape == "junction-whose-target-cannot-be-read":
        junction.symlink_to(base / "users", target_is_directory=True)
        unreadable = {junction}
        home = junction / "me"
    else:  # an operator junction, a repository link, another junction
        second = repo / "held" / "jn2"
        second.symlink_to(base / "users", target_is_directory=True)
        (base / "outside" / "link").symlink_to(second, target_is_directory=True)
        junction.symlink_to(base / "outside", target_is_directory=True)
        junctions = {junction, second}
        home = junction / "link" / "me"
    seam, windows = _windows_seam(base, junctions, unreadable)
    monkeypatch.setattr(home_identity, "os", seam)
    monkeypatch.setattr(home_identity, "_home_spelling", lambda: windows(home))
    home_identity.forget_home_verdicts()
    assert home_identity.home_is_operators() is operators
    assert home_identity.home_is_operators() is operators  # cached


def test_every_holder_is_judged_where_it_physically_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generated, POSIX: chains of 1-2 links whose holders are a plain
    directory outside, one inside a checkout, or a directory whose PHYSICAL
    location (realpath) is inside a checkout though its spelling is not -- an
    indirection the walk does not model as a link (a bind mount). Refused if
    and only if a holder is physically in a checkout or HOME lands in one."""
    from pmcp import home_identity

    base = tmp_path.resolve()
    assert all(not home_identity.has_checkout_marker(d) for d in base.parents)
    bound: dict[str, str] = {}
    real_realpath = os.path.realpath

    def realpath(path: Any, *args: Any, **kwargs: Any) -> str:
        spelled = os.fspath(path)
        return bound.get(spelled) or real_realpath(path, *args, **kwargs)

    monkeypatch.setattr(home_identity.os.path, "realpath", realpath)
    wrong = []
    rows = [
        (holders, styles, final)
        for depth in (1, 2)
        for holders in itertools.product(("in", "out", "bound"), repeat=depth)
        for styles in itertools.product(("abs", "rel", "dotdot"), repeat=depth)
        for final in ("out/dest", "repo/sub", "repo")
    ]
    for n, (holders, styles, final) in enumerate(rows):
        case = base / f"c{n}"
        (case / "repo" / ".git").mkdir(parents=True)
        (case / "repo" / "sub").mkdir()
        (case / "repo" / "mount").mkdir()
        (case / "out" / "dest").mkdir(parents=True)
        (case / "bind").mkdir()
        bound[str(case / "bind")] = str(case / "repo" / "mount")
        held = {"in": case / "repo", "out": case / "out", "bound": case / "bind"}
        links = [held[h] / f"l{i}" for i, h in enumerate(holders)]
        targets = [*links[1:], case / final]
        for link, target, style in zip(links, targets, styles):
            directory = link.parent
            if style == "abs":
                text = str(target)
            elif style == "rel":
                text = os.path.relpath(target, directory)
            else:
                text = os.path.join(
                    "..", directory.name, os.path.relpath(target, directory)
                )
            link.symlink_to(text, target_is_directory=True)
        expected = not ("in" in holders or "bound" in holders or final == "repo/sub")
        monkeypatch.setenv("HOME", str(links[0]))
        home_identity.forget_home_verdicts()
        fresh = home_identity.home_is_operators()
        cached = home_identity.home_is_operators()
        if fresh is not expected or cached is not expected:
            wrong.append((holders, styles, final, fresh, cached))
    assert len(rows) == 270
    assert wrong == []


def test_a_holder_whose_physical_location_cannot_be_resolved_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import home_identity

    base = tmp_path.resolve()
    (base / "out").mkdir()
    (base / "users" / "me").mkdir(parents=True)
    (base / "out" / "alias").symlink_to(base / "users", target_is_directory=True)
    monkeypatch.setenv("HOME", str(base / "out" / "alias" / "me"))
    home_identity.forget_home_verdicts()
    assert home_identity.home_is_operators() is True
    real_realpath = os.path.realpath

    def realpath(path: Any, *args: Any, **kwargs: Any) -> str:
        if os.fspath(path) == str(base / "out"):
            return str(base / "gone")
        return real_realpath(path, *args, **kwargs)

    monkeypatch.setattr(home_identity.os.path, "realpath", realpath)
    home_identity.forget_home_verdicts()
    assert home_identity.home_is_operators() is False


def test_claude_r27_n1_a_leading_double_slash_home_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POSIX lets ``//`` mean something; normpath keeps it, realpath folds it.
    A HOME spelled with it was accepted through round 26."""
    from pmcp import home_identity

    home = tmp_path.resolve() / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", "/" + str(home))
    home_identity.forget_home_verdicts()
    assert home_identity.home_is_operators() is True
    assert home_identity.examinable_home() == home


# --------------------------------------------------------------------------- #
# Board round 28: codex F001 -- the walk stripped `\\?\` from a link target on
# POSIX, where it is four ordinary characters, so it walked a different path
# from the kernel. Every Windows rule is now behind one switch (the path
# module in use). claude N-1: a volume mounted at a folder is a boundary, not
# a relative path. claude N-2: only name-surrogate reparse tags redirect.
# --------------------------------------------------------------------------- #


def test_codex_r28_f001_posix_nt_prefix_cannot_hide_a_checkout_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    import json

    from pmcp import home_identity, trust_store

    if os.name != "posix":
        pytest.skip("POSIX link text")
    base = tmp_path.resolve()
    checkout = base / "\\\\?\\checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "home").symlink_to(".", target_is_directory=True)
    decoy = base / "checkout"
    decoy.mkdir()
    (decoy / "home").symlink_to(checkout, target_is_directory=True)
    alias = base / "operator-home"
    alias.symlink_to(checkout.name + "/home", target_is_directory=True)
    project = checkout / "app"
    project.mkdir()
    config = project / ".mcp.json"
    content = b'{"mcpServers":{"unapproved":{"command":"echo"}}}'
    config.write_bytes(content)
    store = checkout / ".config" / "pmcp"
    store.mkdir(parents=True)
    (store / "trust.json").write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "absolute_path": str(config),
                        "content_sha256": hashlib.sha256(content).hexdigest(),
                        "scope": "user",
                        "decision": "approved",
                        "recorded_at": "2026-10-07T00:00:00+00:00",
                    }
                ],
            }
        )
    )
    launch = base / "launch"
    launch.mkdir()
    status = launch.stat()
    original_marker = home_identity.has_checkout_marker
    ambient = set(base.parents)
    monkeypatch.setattr(
        home_identity,
        "has_checkout_marker",
        lambda path: False if Path(path) in ambient else original_marker(path),
    )
    monkeypatch.setattr(
        trust_store, "_LAUNCH_DIRECTORY", (launch, (status.st_dev, status.st_ino))
    )
    monkeypatch.setattr(trust_store, "_active_project_root", None)
    monkeypatch.setenv("HOME", str(alias))
    home_identity.forget_home_verdicts()
    assert alias.resolve() == checkout
    assert home_identity.has_checkout_marker(checkout)
    assert not trust_store.is_approved(config, content, project_root=project), (
        "A POSIX symlink target was rewritten, admitting a planted approval"
    )


@pytest.mark.parametrize(
    "name",
    [
        "\\\\?\\x",
        "\\\\server\\share",
        "C:",
        "C:\\Users",
        "a\\b",
        "\\\\?\\UNC\\s\\x",
        "Volume{1}",
    ],
)
@pytest.mark.parametrize("held", ["outside", "in-a-checkout"])
@pytest.mark.parametrize("leading", [False, True], ids=["inner", "leading"])
def test_windows_syntax_in_a_posix_link_is_only_characters(
    name: str,
    held: str,
    leading: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A POSIX link whose text looks like Windows syntax names an entry with
    exactly those characters: the walk follows it as the kernel does, and a
    checkout holding the target directory is still found."""
    from pmcp import home_identity

    if os.name != "posix":
        pytest.skip("POSIX link text")
    base = tmp_path.resolve()
    (base / "repo" / ".git").mkdir(parents=True)
    parent = base / "out" if held == "outside" else base / "repo"
    parent.mkdir(exist_ok=True)
    (parent / name / "home").mkdir(parents=True)
    if leading:  # the link text BEGINS with the Windows-looking name
        alias = base / "out" / "alias"
        (base / "out").mkdir(exist_ok=True)
        if parent != base / "out":
            alias = parent / "alias"
        alias.symlink_to(f"{name}/home", target_is_directory=True)
    else:
        alias = base / "alias"
        alias.symlink_to(f"{parent.name}/{name}/home", target_is_directory=True)
    monkeypatch.setenv("HOME", str(alias))
    home_identity.forget_home_verdicts()
    real = os.path.realpath(alias)
    assert real == str(parent / name / "home")
    accepted = held == "outside"
    assert home_identity.home_is_operators() is accepted
    assert home_identity.home_is_operators() is accepted  # cached
    if accepted:
        assert home_identity.examinable_home() == Path(real)


_VOLUME = "\\\\?\\Volume{3f2504e0-4f89-11d3-9a0c-0305e82c3301}\\"


@pytest.mark.parametrize(
    ("shape", "operators"),
    [
        ("volume-mounted-at-the-profiles-folder", True),
        ("volume-mounted-at-a-folder-in-a-checkout", False),
        ("cloud-placeholder-home-folder", True),
        ("dedup-tagged-parent-folder", True),
        ("surrogate-tag-with-unreadable-text", False),
        ("volume-text-on-a-non-surrogate-tag", True),
    ],
)
def test_windows_reparse_points_by_kind(
    shape: str, operators: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import home_identity

    base = tmp_path.resolve()
    repo = base / "repo"
    (repo / ".git").mkdir(parents=True)
    (base / "Users" / "me").mkdir(parents=True)
    tags: dict[Path, int] = {}
    texts: dict[Path, str] = {}
    unreadable: set[Path] = set()
    home = base / "Users" / "me"
    if shape == "volume-mounted-at-the-profiles-folder":
        tags[base / "Users"] = 0xA0000003
        texts[base / "Users"] = _VOLUME
    elif shape == "volume-mounted-at-a-folder-in-a-checkout":
        (repo / "mnt" / "me").mkdir(parents=True)
        tags[repo / "mnt"] = 0xA0000003
        texts[repo / "mnt"] = _VOLUME
        home = repo / "mnt" / "me"
    elif shape == "cloud-placeholder-home-folder":
        tags[home] = 0x9000601A  # IO_REPARSE_TAG_CLOUD_6: not a surrogate
    elif shape == "dedup-tagged-parent-folder":
        tags[base / "Users"] = 0x80000013  # IO_REPARSE_TAG_DEDUP
    elif shape == "surrogate-tag-with-unreadable-text":
        tags[base / "Users"] = 0xA0000003
        unreadable = {base / "Users"}
    else:  # text a surrogate would carry, on an entry that is not one
        tags[base / "Users"] = 0x80000013
        texts[base / "Users"] = _VOLUME
    seam, windows = _windows_seam(base, set(), unreadable, tags, texts)
    monkeypatch.setattr(home_identity, "os", seam)
    monkeypatch.setattr(home_identity, "_home_spelling", lambda: windows(home))
    home_identity.forget_home_verdicts()
    assert home_identity.home_is_operators() is operators
    assert home_identity.home_is_operators() is operators  # cached


def test_a_reparse_tag_never_redirects_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a stat that reported a Windows tag (a FUSE or SMB export, a
    seam) would not make a POSIX directory a link: no Windows rule applies
    to a POSIX path."""
    from types import SimpleNamespace

    from pmcp import home_identity

    base = tmp_path.resolve()
    (base / "users" / "me").mkdir(parents=True)
    real_lstat = os.lstat

    def lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
        seen = real_lstat(path, *args, **kwargs)
        if os.fspath(path) == str(base / "users"):
            fields = {n: getattr(seen, n) for n in dir(seen) if n.startswith("st_")}
            fields["st_reparse_tag"] = 0xA0000003
            return SimpleNamespace(**fields)
        return seen

    monkeypatch.setattr(home_identity.os, "lstat", lstat)
    monkeypatch.setenv("HOME", str(base / "users" / "me"))
    home_identity.forget_home_verdicts()
    assert home_identity.home_is_operators() is True
