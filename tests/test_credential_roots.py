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
from pathlib import Path

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
    with pytest.raises(trust_store.TrustStoreError, match="cannot be examined"):
        trust_store.trust_store_path()
    with pytest.raises(trust_store.TrustStoreError):
        package_approvals_path()
    assert "the home directory cannot be examined" in capsys.readouterr().err


def test_an_unexaminable_home_refuses_trust_but_plain_reads_report_their_own_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A HOME that does not exist: trust refused; a plain read is simply absent."""
    from pmcp import trust_store
    from pmcp.config.loader import default_user_config_paths

    missing = tmp_path.resolve() / "no-such-home"
    monkeypatch.setenv("HOME", str(missing))
    with pytest.raises(trust_store.TrustStoreError, match="cannot be examined"):
        trust_store.trust_store_path()
    # Plain reads go through and find nothing there.
    assert default_user_config_paths() == [
        missing / ".mcp.json",
        missing / ".claude" / ".mcp.json",
    ]
    assert env_store.read_store("user") == {}


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
