"""No caller collapses a store path before the writer's resolver sees it.

Round 7 on Consiliency/pmcp#366 (codex F003): ``package_approvals_path()`` ran
non-strict ``os.path.realpath`` on the store path before calling the writer. With
``package_approvals.json -> missing/../unrelated.json`` the kernel refuses
(``missing`` does not exist), but realpath collapsed it lexically onto
``unrelated.json``, and ``approve_package()`` rewrote that unrelated file -- the
walker never saw the original path. The class: every function that writes a
store, and every function that produces a store's path, passes the path as
spelled and leaves its resolution to the kernel, through the writer.

1. An AST inventory finds every caller of the writers and pins it; neither it
   nor any store-path producer it calls may call ``realpath`` (unless
   ``strict=True``), ``.resolve()``, ``abspath`` or ``normpath``.
2. For every writer caller the inventory finds, a driver plants the
   ``missing/../unrelated`` shape at that caller's store and asserts the
   unrelated file is untouched -- so a new writer caller fails until it has one.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import errno
import os
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pmcp"

WRITERS = {"atomic_write", "write_env_file"}

#: Functions that produce a store's path for a writer (file, qualname).
PRODUCERS = {
    ("trust_store.py", "trust_store_path"),
    ("package_approvals.py", "package_approvals_path"),
    ("env_store.py", "resolve_scope_path"),
    ("env_store.py", "resolve_project_root"),
    ("env_store.py", "scope_confinement"),
    ("manifest/registry.py", "default_registry_cache_path"),
    ("manifest/registry.py", "_cache_path"),
    ("cli.py", "_get_setup_target_path"),
}

LEXICAL = {"realpath", "resolve", "abspath", "normpath"}

#: Known exceptions, each with its reason and tracking issue.
EXEMPT = {
    ("trust_store.py", "trust_store_path"): (
        "pre-#366 `Path.resolve()` kept by the owner's round-8 ruling; a "
        "`trust.json` linked through a missing directory is Consiliency/pmcp#374"
    ),
    ("trust_store.py", "revoke"): (
        "resolves the APPROVED file's path -- the record's lookup key, the same "
        "canonicalisation `record` uses -- never the trust store's own path"
    ),
}


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Local names bound by `from x import y as z` to a writer or a resolver."""
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in WRITERS | LEXICAL:
                    bound[alias.asname or alias.name] = alias.name
    return bound


_ALIASES: dict[str, dict[str, str]] = {}


def _functions() -> dict[tuple[str, str], ast.AST]:
    out: dict[tuple[str, str], ast.AST] = {}

    def visit(node: ast.AST, scope: list[str], rel: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[(rel, ".".join([*scope, child.name]))] = child
                visit(child, [*scope, child.name], rel)
            elif isinstance(child, ast.ClassDef):
                visit(child, [*scope, child.name], rel)
            else:
                visit(child, scope, rel)

    for source in sorted(SRC.rglob("*.py")):
        rel = source.relative_to(SRC).as_posix()
        tree = ast.parse(source.read_text(encoding="utf-8"))
        _ALIASES[rel] = _aliases(tree)
        visit(tree, [], rel)
    return out


def _callee(call: ast.Call, rel: str | None = None) -> str | None:
    """The called name, through `from x import y as z` aliases (N-1, round 8)."""
    f = call.func
    if isinstance(f, ast.Name):
        return _ALIASES.get(rel or "", {}).get(f.id, f.id)
    return f.attr if isinstance(f, ast.Attribute) else None


def _writer_callers() -> set[tuple[str, str]]:
    return {
        key
        for key, fn in _functions().items()
        if key[0] != "atomic_write.py"
        and any(
            isinstance(n, ast.Call) and _callee(n, key[0]) in WRITERS
            for n in ast.walk(fn)
        )
    }


def _callers_of(targets: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Functions that call one of ``targets`` by name (one level up)."""
    names = {name.split(".")[-1] for _rel, name in targets}
    return {
        key
        for key, fn in _functions().items()
        if key[0] != "atomic_write.py"
        and any(
            isinstance(n, ast.Call) and _callee(n, key[0]) in names
            for n in ast.walk(fn)
        )
    }


def _lexical_calls(fn: ast.AST, rel: str | None = None) -> list[str]:
    bad = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and _callee(n, rel) in LEXICAL:
            strict = any(
                k.arg == "strict"
                and isinstance(k.value, ast.Constant)
                and k.value.value is True
                for k in n.keywords
            )
            if not (_callee(n, rel) == "realpath" and strict):
                bad.append(f"{_callee(n, rel)} at line {n.lineno}")
    return bad


def test_no_writer_caller_or_store_path_producer_resolves_lexically() -> None:
    functions = _functions()
    writers = _writer_callers()
    # One level up too: a caller that hands a writer caller a path it already
    # resolved (round 8 N-1) is as bad as resolving it in place.
    checked = writers | _callers_of(writers) | PRODUCERS
    assert PRODUCERS <= set(functions), PRODUCERS - set(functions)
    offenders = {
        f"{rel}:{name}": bad
        for rel, name in sorted(checked)
        if (rel, name) not in EXEMPT
        and (bad := _lexical_calls(functions[(rel, name)], rel))
    }
    assert offenders == {}, (
        "a store path is resolved before the writer sees it -- pass the path as "
        f"spelled and let the writer and the kernel resolve it: {offenders}"
    )


def test_every_path_helper_a_writer_caller_uses_is_a_checked_producer() -> None:
    """A new path helper called by a writer caller must join PRODUCERS."""
    functions = _functions()
    defined = {name.split(".")[-1]: key for key, name in [(k, k[1]) for k in functions]}
    for key in _writer_callers():
        for n in ast.walk(functions[key]):
            if isinstance(n, ast.Call) and (name := _callee(n)) and "path" in name:
                where = defined.get(name)
                if where is not None and where[0] != "atomic_write.py":
                    assert where in PRODUCERS, (key, name)


def test_the_lexical_scan_sees_each_form() -> None:
    """Positive control, aliases included (round 8 N-1)."""
    tree = ast.parse(
        "from os.path import realpath as _rp\n"
        "from pmcp.atomic_write import atomic_write as _aw\n"
        "def f(p):\n"
        "    os.path.realpath(p)\n"
        "    p.resolve()\n"
        "    os.path.abspath(p)\n"
        "    os.path.normpath(p)\n"
        "    os.path.realpath(p, strict=True)\n"
        "    _rp(p)\n"
        "    _aw(p, b'', confine_to=None)\n"
    )
    _ALIASES["<control>"] = _aliases(tree)
    assert len(_lexical_calls(tree, "<control>")) == 5
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert sum(_callee(n, "<control>") == "atomic_write" for n in calls) == 1


# --------------------------------------------------------------------------- #
# Behaviour, generated from the inventory: `missing/../unrelated` at each store.
# --------------------------------------------------------------------------- #

UNRELATED = b"UNRELATED=keep\n"


def _plant(link: Path, content: bytes = UNRELATED) -> Path:
    """`link -> missing/../unrelated.x`; return the unrelated file."""
    link.parent.mkdir(parents=True, exist_ok=True)
    unrelated = link.parent / "unrelated.x"
    unrelated.write_bytes(content)
    os.symlink("missing/../unrelated.x", link)
    # The kernel's own reading: `missing` does not exist, so the link names
    # nothing (while a lexical collapse would name the unrelated file).
    assert not os.path.exists(link)
    return unrelated


def _user_store(home: Path) -> Path:
    return home / ".config" / "pmcp" / "pmcp.env"


def _drive_set_env_value(home: Path, project: Path) -> Path:
    from pmcp.env_store import set_env_value

    unrelated = _plant(_user_store(home))
    with pytest.raises(OSError):
        set_env_value("user", "K", "v")
    unrelated_p = _plant(project / ".env.pmcp")
    with pytest.raises(OSError):
        set_env_value("project", "K", "v", project)
    assert unrelated_p.read_bytes() == UNRELATED
    return unrelated


def _drive_write_env_file(home: Path, project: Path) -> Path:
    from pmcp.env_store import write_env_file

    unrelated = _plant(_user_store(home))
    with pytest.raises(OSError):
        write_env_file(_user_store(home), {"K": "v"}, confine_to=None)
    return unrelated


def _drive_secrets_sync(home: Path, project: Path) -> Path:
    from pmcp.cli_commands.secrets import run_secrets_sync

    (project / ".env.pmcp").write_text("A=1\n", encoding="utf-8")
    unrelated = _plant(_user_store(home))
    out = asyncio.run(
        run_secrets_sync(
            argparse.Namespace(
                from_scope="project", to_scope="user", project=project, overwrite=False
            )
        )
    )
    assert out["ok"] is False
    return unrelated


def _drive_trust_store(home: Path, project: Path) -> Path:
    from pmcp import trust_store

    # A VALID empty store, so a lexical collapse reaches the write (#374).
    unrelated = _plant(
        home / ".config" / "pmcp" / "trust.json", b'{"version": 1, "records": []}\n'
    )
    approved = home / "approved.json"
    approved.write_bytes(b"{}")
    with pytest.raises((OSError, trust_store.TrustStoreError)):
        trust_store.record(
            approved, b"{}", trust_store.PROJECT_SCOPE, trust_store.APPROVED
        )
    return unrelated


def _drive_package_approvals(home: Path, project: Path) -> Path:
    """Codex round-7 F003, the falsifier's shape."""
    from pmcp.manifest.package_identity import PackageIdentity
    from pmcp import package_approvals
    from pmcp.trust_store import TrustStoreError

    unrelated = _plant(home / ".config" / "pmcp" / "package_approvals.json")
    identity = PackageIdentity(
        registry="npm", name="example-mcp", resolved_version="1.2.3", integrity=None
    )
    with pytest.raises((OSError, TrustStoreError)):
        package_approvals.approve_package(identity)
    assert package_approvals.is_package_approved(identity) is False
    return unrelated


def _drive_registry_cache(home: Path, project: Path) -> Path:
    from pmcp.manifest.registry import RegistryCache, save_registry_cache

    cache = home / ".cache" / "pmcp" / "registry-cache.json"
    unrelated = _plant(cache)
    with pytest.raises(OSError):
        save_registry_cache(
            RegistryCache(
                schema_version="registry-cache.v1",
                source_endpoint="https://registry.example.invalid/v0/servers",
                fetched_at="2026-10-05T00:00:00Z",
            ),
            cache,
        )
    return unrelated


def _drive_setup(home: Path, project: Path) -> Path:
    from pmcp.cli import _atomic_write_json

    target = home / ".config" / "opencode" / "opencode.json"
    unrelated = _plant(target)
    with pytest.raises(OSError):
        _atomic_write_json(target, {"mcpServers": {}})
    return unrelated


DRIVERS: dict[tuple[str, str], Callable[[Path, Path], Path]] = {
    ("env_store.py", "write_env_file"): _drive_write_env_file,
    ("env_store.py", "set_env_value"): _drive_set_env_value,
    ("cli_commands/secrets.py", "run_secrets_sync"): _drive_secrets_sync,
    ("trust_store.py", "_write_store"): _drive_trust_store,
    ("package_approvals.py", "_write_store"): _drive_package_approvals,
    ("manifest/registry.py", "save_registry_cache"): _drive_registry_cache,
    ("cli.py", "_atomic_write_json"): _drive_setup,
}


def test_every_writer_caller_has_a_missing_dotdot_driver() -> None:
    assert set(DRIVERS) == _writer_callers()


#: Writer callers whose `missing/../x` behaviour is a tracked known issue.
#: Empty: trust_store_path resolves as the writer does since Consiliency/pmcp#372
#: round 23 (see Consiliency/pmcp#374).
KNOWN_ISSUES: dict[tuple[str, str], str] = {}


@pytest.mark.parametrize(
    "caller",
    [
        pytest.param(k, marks=pytest.mark.xfail(strict=True, reason=KNOWN_ISSUES[k]))
        if k in KNOWN_ISSUES
        else k
        for k in sorted(DRIVERS)
    ],
    ids=lambda k: f"{k[0]}:{k[1]}",
)
def test_a_missing_dotdot_link_never_lands_on_the_unrelated_file(
    caller: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = Path(os.path.realpath(tmp_path))
    home = base / "home"
    project = base / "project"
    (project / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.chdir(base)
    unrelated = DRIVERS[caller](home, project)
    planted = (
        b'{"version": 1, "records": []}\n'
        if caller == ("trust_store.py", "_write_store")
        else UNRELATED
    )
    assert unrelated.read_bytes() == planted


# --------------------------------------------------------------------------- #
# Round 8 (codex F001): trust and residency decisions compare FILE IDENTITY.
# `//x` and `/x` are one directory but unequal strings, which let a `//`-spelled
# link past a string containment check. No string containment in these modules.
# --------------------------------------------------------------------------- #

IDENTITY_MODULES = (
    "trust_store.py",
    "package_approvals.py",
    "env_store.py",
    "atomic_write.py",
    "project_consent.py",
    "policy/policy.py",
)
STRING_CONTAINMENT = {"is_relative_to", "commonpath", "commonprefix", "startswith"}


def _string_containment(tree: ast.AST) -> list[str]:
    found = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in STRING_CONTAINMENT:
                found.append(f"{n.func.attr} at line {n.lineno}")
    return found


def test_trust_and_residency_modules_compare_identity_not_strings() -> None:
    offenders = {}
    for rel in IDENTITY_MODULES:
        source = SRC / rel
        if not source.exists():
            continue
        bad = _string_containment(ast.parse(source.read_text(encoding="utf-8")))
        if bad:
            offenders[rel] = bad
    assert offenders == {}, (
        "a trust or residency decision compares path strings; compare "
        f"(st_dev, st_ino) instead: {offenders}"
    )


def test_the_containment_scan_sees_each_form() -> None:
    tree = ast.parse(
        "p.is_relative_to(q)\nos.path.commonpath([a, b])\nstr(p).startswith(r)\n"
    )
    assert len(_string_containment(tree)) == 3


@pytest.mark.parametrize("spelling", ["/", "//"], ids=["single-slash", "double-slash"])
def test_a_package_approvals_link_into_the_checkout_is_refused_however_spelled(
    spelling: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import package_approvals
    from pmcp.trust_store import TrustStoreError

    base = Path(os.path.realpath(tmp_path))
    checkout = base / "checkout"
    (checkout / ".git").mkdir(parents=True)
    planted = checkout / "approvals.json"
    planted.write_text('{"version": 1, "records": []}\n', encoding="utf-8")
    home = base / "home"
    store = home / ".config" / "pmcp" / "package_approvals.json"
    store.parent.mkdir(parents=True)
    os.symlink(spelling + str(planted).lstrip("/"), store)
    assert os.path.samefile(store, planted)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(checkout)
    with pytest.raises(TrustStoreError, match="inside the checkout"):
        package_approvals.package_approvals_path()


# --------------------------------------------------------------------------- #
# Round 9 (codex F002): "is the store there?" is the kernel's ENOENT/ENOTDIR,
# never a boolean that answers False for EVERY failed lookup (ELOOP, EACCES).
# --------------------------------------------------------------------------- #

BOOLEAN_EXISTENCE = {
    "exists",
    "lexists",
    "is_file",
    "isfile",
    "is_symlink",
    "islink",
    "is_dir",
    "isdir",
}
EXISTENCE_MODULES = (
    "env_store.py",
    "atomic_write.py",
    "trust_store.py",
    "package_approvals.py",
)
#: Config writers that live in larger modules: checked function by function.
EXISTENCE_FUNCTIONS = {
    ("cli.py", "run_setup"),
    ("cli.py", "_atomic_write_json"),
    ("cli.py", "_load_project_store_at_startup"),
    ("cli.py", "load_startup_env"),
    ("cli_commands/secrets.py", "run_secrets_set"),
    ("cli_commands/secrets.py", "run_secrets_sync"),
}


def _boolean_existence(tree: ast.AST) -> list[str]:
    return [
        f"{n.func.attr} at line {n.lineno}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in BOOLEAN_EXISTENCE
    ]


def test_store_absence_is_never_decided_by_a_boolean_existence_check() -> None:
    offenders: dict[str, list[str]] = {}
    for rel in EXISTENCE_MODULES:
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        if bad := _boolean_existence(tree):
            offenders[rel] = bad
    functions = _functions()
    for key in sorted(EXISTENCE_FUNCTIONS):
        assert key in functions, key
        if bad := _boolean_existence(functions[key]):
            offenders[f"{key[0]}:{key[1]}"] = bad
    assert offenders == {}, (
        "a store's absence is decided by a boolean that is False for every "
        f"failed lookup; use atomic_write.is_absent: {offenders}"
    )


def test_the_existence_scan_sees_each_form() -> None:
    tree = ast.parse(
        "p.exists()\nos.path.lexists(p)\np.is_file()\nos.path.isfile(p)\n"
        "p.is_symlink()\nos.path.islink(p)\np.is_dir()\nos.path.isdir(p)\n"
    )
    assert len(_boolean_existence(tree)) == 8


def test_a_deep_checkout_cannot_host_package_approvals_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex round-9 F001's shape, for the store that shares the residency walk."""
    from pmcp import package_approvals, trust_store
    from pmcp.trust_store import TrustStoreError

    base = Path(os.path.realpath(tmp_path))
    checkout = base / "checkout"
    (checkout / ".git").mkdir(parents=True)
    directory = checkout
    while len(os.fsencode(directory)) < 3800:
        directory = directory / ("d" * 19)
        directory.mkdir()
    planted = directory / "approvals.json"
    planted.write_text('{"version": 1, "records": []}\n', encoding="utf-8")
    home = base / "home"
    store = home / ".config" / "pmcp" / "package_approvals.json"
    store.parent.mkdir(parents=True)
    os.symlink(planted, store)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(trust_store, "_checkout_roots", lambda *_a: (checkout,))
    with pytest.raises(TrustStoreError, match="inside the checkout"):
        package_approvals.package_approvals_path()


def test_residency_that_cannot_be_established_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: an error during the walk refuses the store."""
    from pmcp import trust_store
    from pmcp.trust_store import TrustStoreError

    base = Path(os.path.realpath(tmp_path))
    (base / "checkout").mkdir()
    store = base / "home" / "trust.json"
    store.parent.mkdir()
    real_open = os.open

    def failing_open(p: object, flags: int, *a: object, **kw: object) -> int:
        if p == os.pardir:
            raise OSError(errno.ENAMETOOLONG, "File name too long")
        return real_open(p, flags, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(
        trust_store, "_checkout_roots", lambda *_a: (base / "checkout",)
    )
    monkeypatch.setattr(os, "open", failing_open)
    with pytest.raises(TrustStoreError, match="cannot establish"):
        trust_store.refuse_checkout_resident(store, "Trust store")


# --------------------------------------------------------------------------- #
# Round 10: the residency walk without dir_fd/O_DIRECTORY (Windows), and a
# served project root that does not exist yet.
# --------------------------------------------------------------------------- #


def _windows_os():  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    windows = SimpleNamespace(
        **{n: getattr(os, n) for n in dir(os) if n not in {"O_DIRECTORY", "O_PATH"}}
    )
    windows.name = "nt"
    windows.supports_dir_fd = set()
    return windows


def _planted_store(base: Path) -> tuple[Path, Path, bytes]:
    import hashlib
    import json

    checkout = base / "checkout"
    (checkout / ".git").mkdir(parents=True)
    config = checkout / ".mcp.json"
    content = b"{}"
    config.write_bytes(content)
    planted = checkout / "trust.json"
    planted.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "absolute_path": str(config),
                        "content_sha256": hashlib.sha256(content).hexdigest(),
                        "scope": "user",
                        "decision": "approved",
                        "recorded_at": "2026-10-05T00:00:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return checkout, planted, content


def test_the_windows_residency_fallback_refuses_a_planted_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import trust_store

    base = Path(os.path.realpath(tmp_path))
    checkout, planted, content = _planted_store(base)
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    os.symlink(planted, home / ".config" / "pmcp" / "trust.json")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(trust_store, "_checkout_roots", lambda *_a: (checkout,))
    monkeypatch.setattr(trust_store, "os", _windows_os())
    assert not trust_store.is_approved(checkout / ".mcp.json", content)


def test_the_windows_residency_fallback_refuses_on_any_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import trust_store
    from pmcp.trust_store import TrustStoreError

    base = Path(os.path.realpath(tmp_path))
    (base / "checkout").mkdir()
    store = base / "home" / "trust.json"
    store.parent.mkdir()
    windows = _windows_os()
    real_stat = os.stat

    def failing_stat(p: object, *a: object, **kw: object) -> os.stat_result:
        if Path(os.fspath(p)) == base:  # an ancestor the walk must compare
            raise OSError(errno.EACCES, "Permission denied")
        return real_stat(p, *a, **kw)  # type: ignore[arg-type]

    windows.stat = failing_stat
    monkeypatch.setattr(
        trust_store, "_checkout_roots", lambda *_a: (base / "checkout",)
    )
    monkeypatch.setattr(trust_store, "os", windows)
    with pytest.raises(TrustStoreError, match="cannot establish"):
        trust_store.refuse_checkout_resident(store, "Trust store")


@pytest.mark.parametrize("walk", ["descriptors", "windows fallback"])
def test_a_served_root_that_does_not_exist_yet_refuses_nothing(
    walk: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 10 N-2: a missing root holds no store; the user's own stays usable."""
    from pmcp import trust_store

    base = Path(os.path.realpath(tmp_path))
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(
        trust_store, "_checkout_roots", lambda *_a: (base / "not-created-yet",)
    )
    if walk == "windows fallback":
        monkeypatch.setattr(trust_store, "os", _windows_os())
    approved = base / "x.json"
    approved.write_bytes(b"{}")
    trust_store.record(approved, b"{}", trust_store.PROJECT_SCOPE, trust_store.APPROVED)
    assert trust_store.is_approved(approved, b"{}")


# --------------------------------------------------------------------------- #
# Round 11 (codex F001): ONE directory opener and ONE fallback rule for every
# caller. Without O_PATH, a directory the user may search and write but not
# list (0300/0311) must not refuse approval reads, approval writes or the
# residency walk -- as a target directory or as an ancestor.
# --------------------------------------------------------------------------- #

DIRECTORY_FLAG_NAMES = {"O_DIRECTORY", "O_PATH", "_walk_flags"}
OPENER_MODULES = (
    "atomic_write.py",
    "trust_store.py",
    "package_approvals.py",
    "env_store.py",
)
#: Functions allowed to open a directory directly, with the reason.
OPENER_ALLOWED = {
    ("atomic_write.py", "open_directory"): "the one opener",
    ("atomic_write.py", "_fsync_dir"): "reopens a held directory for reading, "
    "only to fsync it, best effort; never used to walk or anchor an operation",
}


def _directory_opens(tree: ast.AST) -> list[tuple[int, str]]:
    found = []

    def mentions_directory_flag(node: ast.AST) -> bool:
        return any(
            (isinstance(n, ast.Attribute) and n.attr in DIRECTORY_FLAG_NAMES)
            or (isinstance(n, ast.Name) and n.id in DIRECTORY_FLAG_NAMES)
            for n in ast.walk(node)
        )

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "open"
                and any(mentions_directory_flag(a) for a in child.args[1:2])
            ):
                found.append((child.lineno, scope))
            visit(child, name)

    visit(tree, "<module>")
    return found


def test_every_directory_is_opened_through_the_one_opener() -> None:
    offenders = []
    for rel in OPENER_MODULES:
        tree = ast.parse((SRC / rel).read_text(encoding="utf-8"))
        for line, scope in _directory_opens(tree):
            if (rel, scope) not in OPENER_ALLOWED:
                offenders.append(f"{rel}:{line} in {scope}")
    assert offenders == [], (
        "a directory is opened directly; go through atomic_write.open_directory "
        f"and apply falls_back_to_pathname: {offenders}"
    )


def test_the_directory_open_scan_sees_each_form() -> None:
    tree = ast.parse(
        "def f(p, fd):\n"
        "    os.open(p, os.O_RDONLY | os.O_DIRECTORY)\n"
        "    os.open(p, _walk_flags(), dir_fd=fd)\n"
        "    os.open(p, flags)\n"
        "    os.open(p, os.O_PATH)\n"
    )
    assert len(_directory_opens(tree)) == 3


def _approval_payload() -> bytes:
    import json

    return json.dumps(
        {
            "version": 1,
            "records": [
                {
                    "registry": "npm",
                    "name": "example-mcp",
                    "resolved_version": "1.2.3",
                    "integrity": None,
                    "decision": "approved",
                    "recorded_at": "2026-10-05T00:00:00+00:00",
                }
            ],
        }
    ).encode()


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="POSIX, non-root")
@pytest.mark.parametrize("o_path", [True, False], ids=["O_PATH", "no O_PATH"])
@pytest.mark.parametrize("where", ["target directory", "ancestor"])
@pytest.mark.parametrize("dir_mode", [0o300, 0o311, 0o700])
def test_approval_reads_writes_and_residency_through_a_search_only_directory(
    dir_mode: int,
    where: str,
    o_path: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from pmcp import atomic_write as writer
    from pmcp import package_approvals, trust_store
    from pmcp.manifest.package_identity import PackageIdentity

    if o_path and not writer._O_PATH:
        pytest.skip("no O_PATH")
    if not o_path:
        monkeypatch.setattr(writer, "_O_PATH", 0)
        monkeypatch.setattr(
            trust_store,
            "os",
            SimpleNamespace(**{n: getattr(os, n) for n in dir(os) if n != "O_PATH"}),
        )
    base = Path(os.path.realpath(tmp_path))
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    checkout = base / "checkout"
    (checkout / ".git").mkdir(parents=True)
    locked = base / "vault"
    target_dir = locked if where == "target directory" else locked / "inner"
    target_dir.mkdir(parents=True)
    # trust.json is linked into the locked directory; package_approvals.json
    # lives beside the RESOLVED trust store (its pre-#366 location), so both
    # stores are read, written and residency-checked through that directory.
    (target_dir / "package_approvals.json").write_bytes(_approval_payload())
    trust_target = target_dir / "trust.json"
    trust_target.write_text('{"version": 1, "records": []}\n', encoding="utf-8")
    os.symlink(trust_target, home / ".config" / "pmcp" / "trust.json")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(trust_store, "_checkout_roots", lambda *_a: (checkout,))
    identity = PackageIdentity(
        registry="npm", name="example-mcp", resolved_version="1.2.3", integrity=None
    )
    other = PackageIdentity(
        registry="npm", name="example-mcp", resolved_version="2.0.0", integrity=None
    )
    approved_file = base / "approved.json"
    approved_file.write_bytes(b"{}")
    os.chmod(locked, dir_mode)
    try:
        assert package_approvals.is_package_approved(identity)
        package_approvals.approve_package(other)  # a write through the link
        assert package_approvals.is_package_approved(other)
        trust_store.record(
            approved_file, b"{}", trust_store.PROJECT_SCOPE, trust_store.APPROVED
        )
        assert trust_store.is_approved(approved_file, b"{}")
    finally:
        os.chmod(locked, 0o700)
    assert os.path.islink(home / ".config" / "pmcp" / "trust.json")


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="POSIX, non-root")
def test_the_pathname_fallback_still_refuses_a_planted_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falling back to pathname identity changes HOW, not WHETHER, residency holds."""
    from types import SimpleNamespace

    from pmcp import atomic_write as writer
    from pmcp import package_approvals, trust_store
    from pmcp.trust_store import TrustStoreError

    monkeypatch.setattr(writer, "_O_PATH", 0)
    monkeypatch.setattr(
        trust_store,
        "os",
        SimpleNamespace(**{n: getattr(os, n) for n in dir(os) if n != "O_PATH"}),
    )
    base = Path(os.path.realpath(tmp_path))
    checkout = base / "checkout"
    vault = checkout / "vault"
    vault.mkdir(parents=True)
    (checkout / ".git").mkdir()
    (vault / "approvals.json").write_bytes(_approval_payload())
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    os.symlink(
        vault / "approvals.json", home / ".config" / "pmcp" / "package_approvals.json"
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(trust_store, "_checkout_roots", lambda *_a: (checkout,))
    os.chmod(vault, 0o311)
    try:
        with pytest.raises(TrustStoreError, match="inside the checkout"):
            package_approvals.package_approvals_path()
    finally:
        os.chmod(vault, 0o700)


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="POSIX, non-root")
@pytest.mark.parametrize("dir_mode", [0o300, 0o311, 0o700])
@pytest.mark.parametrize("o_path", [True, False], ids=["O_PATH", "no O_PATH"])
def test_a_project_store_under_a_search_only_project_directory(
    o_path: bool, dir_mode: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confined walk applies the same rule: a 0300/0311 project directory."""
    from pmcp import atomic_write as writer
    from pmcp.env_store import read_store_for_update, set_env_value

    if o_path and not writer._O_PATH:
        pytest.skip("no O_PATH")
    if not o_path:
        monkeypatch.setattr(writer, "_O_PATH", 0)
    project = Path(os.path.realpath(tmp_path)) / "proj"
    project.mkdir()
    (project / ".env.pmcp").write_text("KEEP=1\n", encoding="utf-8")
    os.chmod(project, dir_mode)
    try:
        set_env_value("project", "NEW", "v", project)
        assert read_store_for_update("project", project / ".env.pmcp") == {
            "KEEP": "1",
            "NEW": "v",
        }
    finally:
        os.chmod(project, 0o700)
