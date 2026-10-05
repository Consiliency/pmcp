"""No caller collapses a store path before the writer's resolver sees it.

Round 7 on Consiliency/pmcp#366 (codex F003): ``package_approvals_path()`` ran
non-strict ``os.path.realpath`` on the store path before calling the writer. With
``package_approvals.json -> missing/../unrelated.json`` the kernel refuses
(``missing`` does not exist), but realpath collapsed it lexically onto
``unrelated.json``, and ``approve_package()`` rewrote that unrelated file -- the
walker never saw the original path. The class: every function that writes a
store, and every function that produces a store's path, must leave resolution to
the single resolver (``atomic_write._walk`` / ``resolve_store_path``).

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
    ("trust_store.py", "resolve_trust_path"),
    ("package_approvals.py", "package_approvals_path"),
    ("env_store.py", "resolve_scope_path"),
    ("env_store.py", "resolve_project_root"),
    ("env_store.py", "scope_confinement"),
    ("manifest/registry.py", "default_registry_cache_path"),
    ("manifest/registry.py", "_cache_path"),
    ("cli.py", "_get_setup_target_path"),
}

LEXICAL = {"realpath", "resolve", "abspath", "normpath"}


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
        visit(ast.parse(source.read_text(encoding="utf-8")), [], rel)
    return out


def _callee(call: ast.Call) -> str | None:
    f = call.func
    return (
        f.id
        if isinstance(f, ast.Name)
        else f.attr
        if isinstance(f, ast.Attribute)
        else None
    )


def _writer_callers() -> set[tuple[str, str]]:
    return {
        key
        for key, fn in _functions().items()
        if key[0] != "atomic_write.py"
        and any(isinstance(n, ast.Call) and _callee(n) in WRITERS for n in ast.walk(fn))
    }


def _lexical_calls(fn: ast.AST) -> list[str]:
    bad = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and _callee(n) in LEXICAL:
            strict = any(
                k.arg == "strict"
                and isinstance(k.value, ast.Constant)
                and k.value.value is True
                for k in n.keywords
            )
            if not (_callee(n) == "realpath" and strict):
                bad.append(f"{_callee(n)} at line {n.lineno}")
    return bad


def test_no_writer_caller_or_store_path_producer_resolves_lexically() -> None:
    functions = _functions()
    checked = _writer_callers() | PRODUCERS
    assert PRODUCERS <= set(functions), PRODUCERS - set(functions)
    offenders = {
        f"{rel}:{name}": bad
        for rel, name in sorted(checked)
        if (bad := _lexical_calls(functions[(rel, name)]))
    }
    assert offenders == {}, (
        "a store path is resolved outside the writer's resolver -- pass the "
        "original path, or get the resolved one from atomic_write."
        f"resolve_store_path: {offenders}"
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
    """Positive control."""
    tree = ast.parse(
        "def f(p):\n"
        "    os.path.realpath(p)\n"
        "    p.resolve()\n"
        "    os.path.abspath(p)\n"
        "    os.path.normpath(p)\n"
        "    os.path.realpath(p, strict=True)\n"
    )
    assert len(_lexical_calls(tree)) == 4


# --------------------------------------------------------------------------- #
# Behaviour, generated from the inventory: `missing/../unrelated` at each store.
# --------------------------------------------------------------------------- #

UNRELATED = b"UNRELATED=keep\n"


def _plant(link: Path) -> Path:
    """`link -> missing/../unrelated.x`; return the unrelated file."""
    link.parent.mkdir(parents=True, exist_ok=True)
    unrelated = link.parent / "unrelated.x"
    unrelated.write_bytes(UNRELATED)
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

    unrelated = _plant(home / ".config" / "pmcp" / "trust.json")
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


@pytest.mark.parametrize("caller", sorted(DRIVERS), ids=lambda k: f"{k[0]}:{k[1]}")
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
    assert unrelated.read_bytes() == UNRELATED
