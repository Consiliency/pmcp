"""A symlinked PMCP file is written THROUGH, never replaced by its own atomic write.

Consiliency/pmcp#248 made the credential-store write atomic (temp file +
``os.replace``). Replacing over the path a user had symlinked from a dotfiles
repository swapped the link for a regular file, so the dotfiles copy silently
stopped receiving updates; 2.7.3's plain ``open(path, "w")`` had followed the
link. Every whole-file rewrite of a user-owned PMCP file now goes through
``pmcp.atomic_write.atomic_write``, which resolves the link and replaces the
TARGET. These tests drive each such writer through its real entry point on a
real filesystem, plus a structural check that no other code renames over a path.
"""

from __future__ import annotations

import argparse
import ast
import errno
import json
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from pmcp import package_approvals, trust_store
from pmcp.cli import _atomic_write_json as setup_write_json
from pmcp.cli_commands.secrets import run_secrets_set
from pmcp.env_store import read_env_file, set_env_value
from pmcp.manifest.package_identity import PackageIdentity
from pmcp.manifest.registry import (
    RegistryCache,
    save_registry_cache,
)
from pmcp.trust_store import TrustStoreError

SRC = Path(__file__).resolve().parents[1] / "src" / "pmcp"


# --------------------------------------------------------------------------- #
# The sites. Each names the path a user would symlink and drives the real
# writer for it; `check` asserts the written bytes are that writer's content.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Site:
    name: str
    path: Callable[[Path], Path]
    write: Callable[[Path], None]
    check: Callable[[bytes], None]
    #: A valid pre-existing file for this writer (the stores refuse to parse "").
    seed: bytes = b""


def _home() -> Path:
    return Path.home()


def _identity(version: str = "1.2.3") -> PackageIdentity:
    return PackageIdentity(
        registry="npm", name="example-mcp", resolved_version=version, integrity=None
    )


def _write_user_secret(_path: Path) -> None:
    set_env_value("user", "PMCP_LINK_TEST", "through-the-link")


def _check_user_secret(data: bytes) -> None:
    assert b"PMCP_LINK_TEST=through-the-link" in data


def _write_trust(_path: Path) -> None:
    approved = _home() / "approved.json"
    approved.write_bytes(b"{}")
    trust_store.record(approved, b"{}", trust_store.PROJECT_SCOPE, trust_store.APPROVED)


def _check_trust(data: bytes) -> None:
    records = json.loads(data)["records"]
    assert [r["absolute_path"] for r in records] == [
        str((_home() / "approved.json").resolve())
    ]


def _write_package_approval(_path: Path) -> None:
    package_approvals.approve_package(_identity())


def _check_package_approval(data: bytes) -> None:
    records = json.loads(data)["records"]
    assert [(r["name"], r["resolved_version"]) for r in records] == [
        ("example-mcp", "1.2.3")
    ]


def _write_registry_cache(path: Path) -> None:
    save_registry_cache(
        RegistryCache(
            schema_version="registry-cache.v1",
            source_endpoint="https://registry.example.invalid/v0/servers",
            fetched_at="2026-10-04T00:00:00Z",
        ),
        path,
    )


def _check_registry_cache(data: bytes) -> None:
    assert json.loads(data)["source_endpoint"] == (
        "https://registry.example.invalid/v0/servers"
    )


def _write_setup(path: Path) -> None:
    setup_write_json(path, {"mcpServers": {"pmcp": {"command": "pmcp"}}})


def _check_setup(data: bytes) -> None:
    assert json.loads(data) == {"mcpServers": {"pmcp": {"command": "pmcp"}}}


_EMPTY_STORE = b'{"version": 1, "records": []}\n'

SITES = [
    Site(
        "env_store user pmcp.env",
        lambda home: home / ".config" / "pmcp" / "pmcp.env",
        _write_user_secret,
        _check_user_secret,
    ),
    Site(
        "trust_store trust.json",
        lambda home: home / ".config" / "pmcp" / "trust.json",
        _write_trust,
        _check_trust,
        _EMPTY_STORE,
    ),
    Site(
        "package_approvals package_approvals.json",
        lambda home: home / ".config" / "pmcp" / "package_approvals.json",
        _write_package_approval,
        _check_package_approval,
        _EMPTY_STORE,
    ),
    Site(
        "registry cache registry-cache.json",
        lambda home: home / ".cache" / "pmcp" / "registry-cache.json",
        _write_registry_cache,
        _check_registry_cache,
    ),
    Site(
        "pmcp setup client config",
        lambda home: home / ".config" / "opencode" / "opencode.json",
        _write_setup,
        _check_setup,
    ),
]


@pytest.fixture
def dotfiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A dotfiles directory outside the home, and a cwd that is no checkout.

    The conftest autouse fixture already points HOME at a per-test directory;
    the cwd is moved off the repository so the trust-store residency walk judges
    no checkout here (residency is exercised separately below).
    """
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    repo = tmp_path / "dotfiles"
    repo.mkdir()
    return repo


def _link(link: Path, target: str | Path) -> Path:
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, link)
    return link


def _assert_written_through(
    link: Path, link_text: str, target: Path, site: Site
) -> None:
    assert os.path.islink(link), f"{site.name}: the symlink was replaced by a file"
    assert os.readlink(link) == link_text, f"{site.name}: the link was rewritten"
    assert target.is_file() and not target.is_symlink()
    site.check(target.read_bytes())
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    # No temp left beside the target or beside the link.
    for directory in {target.parent, link.parent}:
        leftovers = [
            p.name
            for p in directory.iterdir()
            if p.name.startswith(".") and p.name not in {link.name, target.name}
        ]
        assert leftovers == [], (site.name, leftovers)


def _ids(sites: list[Site]) -> list[str]:
    return [s.name for s in sites]


@pytest.mark.parametrize("site", SITES, ids=_ids(SITES))
def test_a_symlink_is_preserved_and_its_target_updated(
    site: Site, dotfiles: Path
) -> None:
    target = dotfiles / "file"
    target.write_bytes(site.seed)
    os.chmod(target, 0o644)
    link = _link(site.path(_home()), target)

    site.write(link)

    _assert_written_through(link, str(target), target, site)


@pytest.mark.parametrize("site", SITES, ids=_ids(SITES))
def test_a_second_write_still_goes_through_the_link(site: Site, dotfiles: Path) -> None:
    target = dotfiles / "file"
    link = _link(site.path(_home()), target)

    site.write(link)
    site.write(link)

    _assert_written_through(link, str(target), target, site)


@pytest.mark.parametrize("site", SITES, ids=_ids(SITES))
def test_a_symlink_chain_writes_the_final_target(site: Site, dotfiles: Path) -> None:
    target = dotfiles / "final"
    target.write_bytes(site.seed)
    middle = _link(dotfiles / "middle", target)
    link = _link(site.path(_home()), middle)

    site.write(link)

    _assert_written_through(link, str(middle), target, site)
    assert os.path.islink(middle) and os.readlink(middle) == str(target)


@pytest.mark.parametrize("site", SITES, ids=_ids(SITES))
def test_a_relative_symlink_is_resolved_against_the_links_directory(
    site: Site, dotfiles: Path
) -> None:
    target = dotfiles / "file"
    target.write_bytes(site.seed)
    link_path = site.path(_home())
    link_path.parent.mkdir(parents=True, exist_ok=True)
    relative = os.path.relpath(target, link_path.parent)
    assert not os.path.isabs(relative)
    link = _link(link_path, relative)

    site.write(link)

    _assert_written_through(link, relative, target, site)


@pytest.mark.parametrize("site", SITES, ids=_ids(SITES))
def test_a_dangling_symlink_creates_its_target_at_0600(
    site: Site, dotfiles: Path
) -> None:
    """The decided shape: a link to a not-yet-created file creates that file.

    It is the fresh-machine dotfiles shape (a committed link to a git-ignored
    secrets file), what 2.7.3 did, and what the read side already assumes: a
    store whose link target is gone is *not there*, not unreadable.
    """
    target = dotfiles / "not-yet"
    link = _link(site.path(_home()), target)
    assert not target.exists()

    site.write(link)

    _assert_written_through(link, str(target), target, site)


#: The approval stores resolve their path up front (for the residency check) and
#: then create the store directory 0700 themselves (`_ensure_store_dir`), as the
#: trust store always has; the helper's own refusal applies to every other site.
_RESOLVING_STORES = {
    "trust_store trust.json",
    "package_approvals package_approvals.json",
}
_NON_RESOLVING = [s for s in SITES if s.name not in _RESOLVING_STORES]
_RESOLVING = [s for s in SITES if s.name in _RESOLVING_STORES]


@pytest.mark.parametrize("site", _NON_RESOLVING, ids=_ids(_NON_RESOLVING))
def test_a_dangling_symlink_into_a_missing_directory_is_refused(
    site: Site, dotfiles: Path
) -> None:
    """Only the target FILE is created; directories at the link's destination are not."""
    target = dotfiles / "missing-dir" / "file"
    link = _link(site.path(_home()), target)

    with pytest.raises(FileNotFoundError, match="symlink target"):
        site.write(link)

    assert os.path.islink(link) and os.readlink(link) == str(target)
    assert not target.parent.exists()


@pytest.mark.parametrize("site", _RESOLVING, ids=_ids(_RESOLVING))
def test_an_approval_store_link_into_a_missing_directory_creates_it_owner_only(
    site: Site, dotfiles: Path
) -> None:
    """The approval stores' directory rule, applied to the resolved store path."""
    target = dotfiles / "missing-dir" / "file"
    link = _link(site.path(_home()), target)

    site.write(link)

    _assert_written_through(link, str(target), target, site)
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("site", SITES, ids=_ids(SITES))
def test_a_symlink_loop_is_refused_and_left_intact(site: Site, dotfiles: Path) -> None:
    """realpath does not raise on a loop on 3.10-3.12; the write must, not break it."""
    link_path = site.path(_home())
    other = _link(dotfiles / "loop-b", link_path)
    link = _link(link_path, other)

    # The trust store resolves its path with Path.resolve(), which raises
    # RuntimeError on a loop before 3.13 (OSError after); every other site
    # reaches the helper's ELOOP refusal.
    with pytest.raises((OSError, RuntimeError)) as info:
        site.write(link)

    if site.name != "trust_store trust.json":
        assert isinstance(info.value, OSError)
        assert info.value.errno == errno.ELOOP
    assert os.path.islink(link) and os.readlink(link) == str(other)
    assert os.path.islink(other) and os.readlink(other) == str(link_path)


@pytest.mark.parametrize("site", SITES, ids=_ids(SITES))
def test_a_regular_file_is_still_replaced_at_0600(site: Site, dotfiles: Path) -> None:
    """The unlinked path, unchanged: replaced in place, owner-only."""
    path = site.path(_home())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(site.seed)
    os.chmod(path, 0o644)

    site.write(path)

    assert path.is_file() and not path.is_symlink()
    site.check(path.read_bytes())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --------------------------------------------------------------------------- #
# The two user-facing writers the regression was reported through.
# --------------------------------------------------------------------------- #


async def test_pmcp_secrets_set_writes_through_a_symlinked_pmcp_env(
    dotfiles: Path,
) -> None:
    target = dotfiles / "pmcp.env"
    target.write_text("EXISTING=kept\n", encoding="utf-8")
    link = _link(_home() / ".config" / "pmcp" / "pmcp.env", target)

    out = await run_secrets_set(
        argparse.Namespace(scope="user", key="NEW_KEY", value="v", project=None)
    )

    assert out["ok"] is True
    assert os.path.islink(link) and os.readlink(link) == str(target)
    assert read_env_file(target) == {"EXISTING": "kept", "NEW_KEY": "v"}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


async def test_gateway_auth_connect_writes_through_a_symlinked_pmcp_env(
    dotfiles: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_trust_boundaries_composition import (
        CREDENTIAL_SERVER,
        _gateway,
        _write_policy,
    )

    target = dotfiles / "pmcp.env"
    target.write_text("EXISTING=kept\n", encoding="utf-8")
    link = _link(_home() / ".config" / "pmcp" / "pmcp.env", target)
    project = tmp_path / "project"
    project.mkdir()
    policy = _write_policy(tmp_path / "policy" / "gateway-policy.yaml", "servers: {}\n")
    gateway, _manager, _jobs = _gateway(monkeypatch, policy, project_root=project)

    result = await gateway.auth_connect(
        {
            "server_name": CREDENTIAL_SERVER,
            "credential": "linked-secret",
            "scope": "user",
        }
    )

    assert result.ok is True, result.message
    assert result.env_var
    os.environ.pop(result.env_var, None)
    assert os.path.islink(link) and os.readlink(link) == str(target)
    assert read_env_file(target) == {
        "EXISTING": "kept",
        result.env_var: "linked-secret",
    }
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


# --------------------------------------------------------------------------- #
# Concurrency: the stores that serialise their read-modify-write still do when
# the store is a link, and every writer's record lands in the TARGET.
# --------------------------------------------------------------------------- #


def test_concurrent_package_approvals_through_a_symlink_lose_nothing(
    dotfiles: Path,
) -> None:
    target = dotfiles / "package_approvals.json"
    link = _link(_home() / ".config" / "pmcp" / "package_approvals.json", target)
    versions = [f"1.0.{i}" for i in range(12)]
    errors: list[BaseException] = []

    def approve(version: str) -> None:
        try:
            package_approvals.approve_package(_identity(version))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=approve, args=(v,)) for v in versions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert os.path.islink(link)
    recorded = sorted(
        r["resolved_version"] for r in json.loads(target.read_bytes())["records"]
    )
    assert recorded == sorted(versions)


def test_concurrent_trust_records_through_a_symlink_lose_nothing(
    dotfiles: Path,
) -> None:
    target = dotfiles / "trust.json"
    link = _link(_home() / ".config" / "pmcp" / "trust.json", target)
    files = []
    for i in range(12):
        f = _home() / f"approved-{i}.json"
        f.write_bytes(b"{}")
        files.append(f)
    errors: list[BaseException] = []

    def record(path: Path) -> None:
        try:
            trust_store.record(
                path, b"{}", trust_store.PROJECT_SCOPE, trust_store.APPROVED
            )
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=record, args=(f,)) for f in files]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert os.path.islink(link)
    recorded = sorted(
        r["absolute_path"] for r in json.loads(target.read_bytes())["records"]
    )
    assert recorded == sorted(str(f.resolve()) for f in files)


# --------------------------------------------------------------------------- #
# Residency: following a link must not let a store land in a judged checkout.
# --------------------------------------------------------------------------- #


def test_a_package_approvals_link_into_the_checkout_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trust store's residency rule, applied to the approvals file's own link.

    The directory is the trust store's (already residency-checked); the file's
    final component is resolved and checked too, so writing through a link can
    never put approvals inside the checkout being judged, and reading through
    one grants nothing.
    """
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    monkeypatch.chdir(checkout)
    planted = checkout / "package_approvals.json"
    planted.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "registry": "npm",
                        "name": "example-mcp",
                        "resolved_version": "1.2.3",
                        "integrity": None,
                        "decision": "approved",
                        "recorded_at": "2026-10-04T00:00:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    before = planted.read_bytes()
    link = _link(_home() / ".config" / "pmcp" / "package_approvals.json", planted)

    with pytest.raises(TrustStoreError, match="inside the checkout"):
        package_approvals.package_approvals_path()
    with pytest.raises(TrustStoreError):
        package_approvals.approve_package(_identity("9.9.9"))
    assert package_approvals.is_package_approved(_identity()) is False
    assert planted.read_bytes() == before
    assert os.path.islink(link)


def test_a_trust_json_link_into_the_checkout_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    monkeypatch.chdir(checkout)
    planted = checkout / "trust.json"
    link = _link(_home() / ".config" / "pmcp" / "trust.json", planted)
    approved = tmp_path / "x.json"
    approved.write_bytes(b"{}")

    with pytest.raises(TrustStoreError, match="inside the checkout"):
        trust_store.record(
            approved, b"{}", trust_store.PROJECT_SCOPE, trust_store.APPROVED
        )
    assert not planted.exists()
    assert os.path.islink(link)


# --------------------------------------------------------------------------- #
# Structural: nothing in src/pmcp renames over a path except through the helper.
# --------------------------------------------------------------------------- #

#: The only file allowed to rename or create temp files for a whole-file write.
_HELPER = "atomic_write.py"

#: (file, enclosing function) pairs deliberately NOT routed through the helper.
#: config.loader._atomic_write_json writes a project `.mcp.json` to a key pinned
#: by an O_NOFOLLOW open and refuses a symlinked `.mcp.json` (symlinked_config,
#: a trust-store identity rule); re-resolving it would reopen the path-identity
#: race SECURITY.md C-38 characterises.
_ALLOWED = {("config/loader.py", "_atomic_write_json")}

_FORBIDDEN_DOTTED = {
    "os.replace",
    "os.rename",
    "os.renames",
    "shutil.move",
    "tempfile.mkstemp",
    "tempfile.NamedTemporaryFile",
}


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, ast.Name):
        return node.id
    return None


def _rename_calls(tree: ast.AST) -> list[tuple[int, str, str]]:
    """Every rename-over-path / temp-file call, with its enclosing function."""
    found: list[tuple[int, str, str]] = []

    def visit(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = func
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Call):
                dotted = _dotted(child.func)
                if dotted in _FORBIDDEN_DOTTED:
                    found.append((child.lineno, func, dotted))
                elif (
                    isinstance(child.func, ast.Attribute)
                    and child.func.attr in {"replace", "rename"}
                    and len(child.args) == 1
                    and not child.keywords
                    and not (
                        isinstance(child.args[0], ast.Constant)
                        and isinstance(child.args[0].value, str)
                    )
                    and dotted != "dataclasses.replace"
                ):
                    # Path.replace(target) / Path.rename(target): one non-literal
                    # positional argument. str.replace always takes two.
                    found.append((child.lineno, func, f".{child.func.attr}()"))
            visit(child, name)

    visit(tree, "<module>")
    return found


def test_no_code_renames_over_a_path_except_through_the_helper() -> None:
    offenders: list[str] = []
    seen_allowed: set[tuple[str, str]] = set()
    for source in sorted(SRC.rglob("*.py")):
        rel = source.relative_to(SRC).as_posix()
        if rel == _HELPER:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for lineno, func, call in _rename_calls(tree):
            if (rel, func) in _ALLOWED:
                seen_allowed.add((rel, func))
                continue
            offenders.append(f"{rel}:{lineno} {func}: {call}")
    assert offenders == [], (
        "rename-over-path outside pmcp.atomic_write -- route the write through "
        f"atomic_write() so a symlinked file is written through: {offenders}"
    )
    # The allowlist is not stale: the deliberate exception still exists.
    assert seen_allowed == _ALLOWED


def test_the_structural_scan_sees_what_it_forbids() -> None:
    """Positive control: the scan flags each shape it exists to catch."""
    sample = ast.parse(
        "import os, tempfile, shutil\n"
        "def w(tmp, path):\n"
        "    os.replace(tmp, path)\n"
        "    os.rename(tmp, path)\n"
        "    shutil.move(tmp, path)\n"
        "    tempfile.mkstemp()\n"
        "    tempfile.NamedTemporaryFile()\n"
        "    tmp.replace(path)\n"
        "    tmp.rename(path)\n"
        "    'a-b'.replace('-', '_')\n"
        "    s.replace(old, new)\n"
    )
    flagged = [call for _line, _func, call in _rename_calls(sample)]
    assert flagged == [
        "os.replace",
        "os.rename",
        "shutil.move",
        "tempfile.mkstemp",
        "tempfile.NamedTemporaryFile",
        ".replace()",
        ".rename()",
    ]


@pytest.mark.parametrize("site", SITES, ids=_ids(SITES))
def test_the_temp_file_is_created_beside_the_target_not_the_link(
    site: Site, dotfiles: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.replace`` is atomic only within one filesystem, and a dotfiles target
    commonly lives on another mount than ``~/.config`` -- so the temp must share
    the TARGET's directory. (Within one test filesystem a temp beside the link
    would still rename successfully, so this is observed where the temp is made:
    the directory descriptor the walk ended on, or the fallback's directory.)
    """
    from pmcp import atomic_write as writer

    dirs: list[str] = []
    real_in_dir = writer._write_in_dir
    real_by_path = writer._write_by_path

    def spy_in_dir(dir_fd: int, name: str, *args: Any, **kwargs: Any) -> None:
        same = os.path.samestat(os.fstat(dir_fd), os.stat(dotfiles))
        dirs.append(str(dotfiles.resolve()) if same else "/not-the-target-dir")
        real_in_dir(dir_fd, name, *args, **kwargs)

    def spy_by_path(target: Path, *args: Any, **kwargs: Any) -> None:
        dirs.append(os.fspath(target.parent))
        real_by_path(target, *args, **kwargs)

    monkeypatch.setattr(writer, "_write_in_dir", spy_in_dir)
    monkeypatch.setattr(writer, "_write_by_path", spy_by_path)
    target = dotfiles / "file"
    link = _link(site.path(_home()), target)

    site.write(link)

    assert dirs and {os.path.realpath(d) for d in dirs} == {str(dotfiles.resolve())}
    _assert_written_through(link, str(target), target, site)
