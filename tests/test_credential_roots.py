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
