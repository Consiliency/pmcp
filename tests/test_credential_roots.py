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
    assert check(object(), "ONE_FILE_TOKEN") is True  # type: ignore[arg-type]
    assert "ONE_FILE_TOKEN" not in os.environ
