"""A file whose path a repository controls is never written outside that repository.

``<project>/.env.pmcp`` lives in a checkout, so a cloned repository can ship it
as a symlink to ``../.bashrc`` or to any path the user can write. The user store
follows its links (a dotfiles repository, see ``test_atomic_write_symlinks``);
the project store must not follow one out of the project. Every shape that
leaves -- a relative link out, an absolute link out, a dangling link out, a
chain that leaves and comes back, a directory component that leaves -- is
REFUSED: the outside file is untouched and the link is left as it was. Since
round 8 a link that stays inside is refused too -- pmcp follows no symlink in a
project store at all. Driven through the three entry points that write the
project store: ``pmcp secrets set``, ``pmcp secrets sync --to-scope project``
and ``gateway.auth_connect`` with ``scope="project"``.
"""

from __future__ import annotations

import argparse
import ast
import errno
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pmcp import atomic_write as atomic_write_module
from pmcp.atomic_write import ConfinedWriteError, atomic_write
from pmcp.cli_commands.secrets import run_secrets_set, run_secrets_sync
from pmcp.env_store import (
    read_env_file,
    scope_confinement,
    set_env_value,
    store_refusal,
    write_env_file,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "pmcp"

VICTIM = 'alias ll="ls -l"\nexport X=1\n'


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A checkout and, beside it, a directory the checkout must never write into."""
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim"
    victim.write_text(VICTIM, encoding="utf-8")
    os.chmod(victim, 0o644)
    monkeypatch.chdir(tmp_path)
    return {"project": project, "outside": outside, "victim": victim, "base": tmp_path}


# --------------------------------------------------------------------------- #
# Shapes. Each plants `<project>/.env.pmcp` and returns the link text it wrote.
# --------------------------------------------------------------------------- #


def _rel_out(lay: dict[str, Path]) -> None:
    os.symlink("../outside/victim", lay["project"] / ".env.pmcp")


def _abs_out(lay: dict[str, Path]) -> None:
    os.symlink(lay["victim"], lay["project"] / ".env.pmcp")


def _dangling_out(lay: dict[str, Path]) -> None:
    os.symlink("../outside/not-yet", lay["project"] / ".env.pmcp")


def _leave_and_reenter(lay: dict[str, Path]) -> None:
    (lay["project"] / "real.env").write_text("", encoding="utf-8")
    os.symlink(lay["project"] / "real.env", lay["outside"] / "hop")
    os.symlink("../outside/hop", lay["project"] / ".env.pmcp")


def _dir_component_out(lay: dict[str, Path]) -> None:
    os.symlink("../outside", lay["project"] / "sub")
    os.symlink("sub/victim", lay["project"] / ".env.pmcp")


def _deep_dotdot_out(lay: dict[str, Path]) -> None:
    (lay["project"] / "a").mkdir()
    os.symlink("a/../../outside/victim", lay["project"] / ".env.pmcp")


LEAVING: dict[str, Callable[[dict[str, Path]], None]] = {
    "relative link out": _rel_out,
    "absolute link out": _abs_out,
    "dangling link out": _dangling_out,
    "chain that leaves and re-enters": _leave_and_reenter,
    "directory component out": _dir_component_out,
    "dotdot through a subdirectory out": _deep_dotdot_out,
}


def _outside_snapshot(lay: dict[str, Path]) -> dict[str, tuple[bytes | str, int]]:
    snap: dict[str, tuple[bytes | str, int]] = {}
    for entry in sorted(lay["outside"].iterdir()):
        st = entry.lstat()
        content: bytes | str = (
            os.readlink(entry) if entry.is_symlink() else entry.read_bytes()
        )
        snap[entry.name] = (content, stat.S_IMODE(st.st_mode))
    return snap


def _assert_refused_and_untouched(
    lay: dict[str, Path], before: dict[str, tuple[bytes | str, int]], link_text: str
) -> None:
    assert _outside_snapshot(lay) == before, "a file outside the project was written"
    link = lay["project"] / ".env.pmcp"
    assert os.path.islink(link), "the operator's link was replaced, not refused"
    assert os.readlink(link) == link_text
    stray = [p.name for p in lay["project"].iterdir() if p.name.startswith(".pmcp-")]
    assert stray == []


REFUSAL = "refusing to write .env.pmcp: it is a symlink"


class Reported(Exception):
    """An entry point answered ``ok: false``; ``message`` is what the operator saw."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def _reported(awaitable: Any) -> Any:
    """The operator-facing entry points must REPORT a failed write, never raise it."""
    try:
        return await awaitable
    except OSError as exc:
        raise AssertionError(
            f"the entry point raised {type(exc).__name__} instead of reporting it"
        ) from exc


async def _via_set_env_value(lay: dict[str, Path]) -> None:
    """The library call RAISES; the boundary helper is what turns it into a report."""
    try:
        set_env_value("project", "K", "v", lay["project"])
    except OSError as exc:
        raise Reported(store_refusal(lay["project"] / ".env.pmcp", exc)) from exc


async def _via_secrets_set(lay: dict[str, Path]) -> None:
    out = await _reported(
        run_secrets_set(
            argparse.Namespace(
                scope="project", key="K", value="v", project=lay["project"]
            )
        )
    )
    if out["ok"] is False:
        raise Reported(str(out["error"]))


async def _via_secrets_sync(lay: dict[str, Path]) -> None:
    user_store = Path.home() / ".config" / "pmcp" / "pmcp.env"
    write_env_file(user_store, {"OPENAI_API_KEY": "sk-user-secret"}, confine_to=None)
    out = await _reported(
        run_secrets_sync(
            argparse.Namespace(
                from_scope="user",
                to_scope="project",
                project=lay["project"],
                overwrite=False,
            )
        )
    )
    if out["ok"] is False:
        raise Reported(str(out["error"]))


async def _via_auth_connect(lay: dict[str, Path]) -> None:
    from tests.test_trust_boundaries_composition import (
        CREDENTIAL_SERVER,
        _gateway,
        _write_policy,
    )

    mp = pytest.MonkeyPatch()
    try:
        policy = _write_policy(
            lay["base"] / "policy" / "gateway-policy.yaml", "servers: {}\n"
        )
        gateway, _manager, _jobs = _gateway(mp, policy, project_root=lay["project"])
        result = await _reported(
            gateway.auth_connect(
                {
                    "server_name": CREDENTIAL_SERVER,
                    "credential": "project-secret",
                    "scope": "project",
                }
            )
        )
    finally:
        mp.undo()
    if result.env_var:
        os.environ.pop(result.env_var, None)
    if result.ok is False:
        raise Reported(result.message)


ENTRY_POINTS = {
    "set_env_value": _via_set_env_value,
    "pmcp secrets set": _via_secrets_set,
    "pmcp secrets sync --to-scope project": _via_secrets_sync,
    "gateway.auth_connect scope=project": _via_auth_connect,
}


@pytest.mark.parametrize("entry", list(ENTRY_POINTS), ids=list(ENTRY_POINTS))
@pytest.mark.parametrize("shape", list(LEAVING), ids=list(LEAVING))
async def test_a_project_store_link_that_leaves_the_project_is_refused(
    shape: str, entry: str, layout: dict[str, Path]
) -> None:
    LEAVING[shape](layout)
    link_text = os.readlink(layout["project"] / ".env.pmcp")
    before = _outside_snapshot(layout)

    with pytest.raises(Reported) as info:
        await ENTRY_POINTS[entry](layout)
    assert info.value.message == REFUSAL

    _assert_refused_and_untouched(layout, before, link_text)


# --------------------------------------------------------------------------- #
# Links that stay inside the project are followed, like the user store's.
# --------------------------------------------------------------------------- #


def _inside_relative(lay: dict[str, Path]) -> Path:
    target = lay["project"] / "secrets" / "real.env"
    target.parent.mkdir()
    target.write_text("", encoding="utf-8")
    os.symlink("secrets/real.env", lay["project"] / ".env.pmcp")
    return target


def _inside_absolute(lay: dict[str, Path]) -> Path:
    target = lay["project"].resolve() / "real.env"
    target.write_text("", encoding="utf-8")
    os.symlink(target, lay["project"] / ".env.pmcp")
    return target


def _inside_chain(lay: dict[str, Path]) -> Path:
    target = lay["project"] / "real.env"
    target.write_text("", encoding="utf-8")
    os.symlink("real.env", lay["project"] / "middle")
    os.symlink("middle", lay["project"] / ".env.pmcp")
    return target


def _inside_dangling(lay: dict[str, Path]) -> Path:
    os.symlink("not-yet.env", lay["project"] / ".env.pmcp")
    return lay["project"] / "not-yet.env"


def _inside_dotdot(lay: dict[str, Path]) -> Path:
    (lay["project"] / "a").mkdir()
    target = lay["project"] / "real.env"
    os.symlink("a/../real.env", lay["project"] / ".env.pmcp")
    return target


INSIDE: dict[str, Callable[[dict[str, Path]], Path]] = {
    "relative link inside": _inside_relative,
    "absolute link inside": _inside_absolute,
    "chain inside": _inside_chain,
    "dangling link inside": _inside_dangling,
    "dotdot that stays inside": _inside_dotdot,
}


@pytest.mark.parametrize("entry", list(ENTRY_POINTS), ids=list(ENTRY_POINTS))
@pytest.mark.parametrize("shape", list(INSIDE), ids=list(INSIDE))
async def test_a_project_store_link_that_stays_inside_is_refused_too(
    shape: str, entry: str, layout: dict[str, Path]
) -> None:
    """Round 8 (owner's ruling): no symlink is followed in a project store."""
    target = INSIDE[shape](layout)
    link = layout["project"] / ".env.pmcp"
    link_text = os.readlink(link)
    before_target = target.read_bytes() if target.exists() else None

    with pytest.raises(Reported) as info:
        await ENTRY_POINTS[entry](layout)

    assert info.value.message == REFUSAL
    assert os.path.islink(link) and os.readlink(link) == link_text
    assert (target.read_bytes() if target.exists() else None) == before_target


async def test_a_plain_project_store_is_still_written_at_0600(
    layout: dict[str, Path],
) -> None:
    set_env_value("project", "K", "v", layout["project"])
    store = layout["project"] / ".env.pmcp"
    assert not store.is_symlink()
    assert read_env_file(store) == {"K": "v"}
    assert stat.S_IMODE(store.stat().st_mode) == 0o600


def test_the_user_store_still_follows_its_link_out_of_any_checkout(
    layout: dict[str, Path],
) -> None:
    """Confinement is the project store's, not the user store's."""
    link = Path.home() / ".config" / "pmcp" / "pmcp.env"
    link.parent.mkdir(parents=True, exist_ok=True)
    target = layout["outside"] / "dotfiles.env"
    os.symlink(target, link)

    set_env_value("user", "K", "v")

    assert os.path.islink(link)
    assert read_env_file(target) == {"K": "v"}


def test_scope_confinement_is_derived_from_the_store_path() -> None:
    store = Path.cwd() / ".env.pmcp"
    assert scope_confinement("project", store) == store.parent
    assert scope_confinement("user", Path.home() / ".config/pmcp/pmcp.env") is None
    with pytest.raises(ValueError):
        scope_confinement("global", store)


def test_a_path_outside_the_root_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ConfinedWriteError, match="outside the project"):
        atomic_write(tmp_path / "elsewhere", b"x", confine_to=root)
    assert not (tmp_path / "elsewhere").exists()


def test_a_link_loop_inside_the_project_is_refused(layout: dict[str, Path]) -> None:
    project = layout["project"]
    os.symlink("b", project / "a")
    os.symlink("a", project / "b")
    os.symlink("a", project / ".env.pmcp")
    with pytest.raises(ConfinedWriteError, match="it is a symlink"):
        set_env_value("project", "K", "v", project)
    assert os.readlink(project / ".env.pmcp") == "a"


def test_a_confined_write_that_fails_leaves_no_temp(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = layout["project"]
    real_fsync = os.fsync

    def boom(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("simulated ENOSPC")
        real_fsync(fd)

    set_env_value("project", "KEEP", "1", project)
    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="ENOSPC"):
        set_env_value("project", "NEW", "2", project)
    monkeypatch.setattr(os, "fsync", real_fsync)
    assert read_env_file(project / ".env.pmcp") == {"KEEP": "1"}
    assert [p.name for p in project.iterdir() if p.name.startswith(".pmcp-")] == []


# --------------------------------------------------------------------------- #
# TOCTOU: a swap after the chain check must not redirect the write.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(os.name != "posix", reason="dir_fd walk is POSIX-only")
def test_confined_writes_use_the_dir_fd_walk_on_posix() -> None:
    """The TOCTOU-free path must be the one that runs, not the fallback."""
    assert atomic_write_module._DIR_FD_SUPPORTED is True


def _swap_after_check(
    monkeypatch: pytest.MonkeyPatch, swap: Callable[[], None]
) -> None:
    """Run ``swap`` after the WRITE's walk resolved the path, before it writes.

    Hooked at ``_write_in_dir`` -- the step that consumes the walk's directory
    descriptor -- so the swap lands between the write's own check and use, not
    during the read that precedes it.
    """
    real = atomic_write_module._write_in_dir

    def swapped_then_written(*args: Any, **kwargs: Any) -> None:
        swap()
        real(*args, **kwargs)

    monkeypatch.setattr(atomic_write_module, "_write_in_dir", swapped_then_written)


@pytest.mark.skipif(os.name != "posix", reason="dir_fd walk is POSIX-only")
def test_a_directory_swapped_for_a_link_after_the_walk_is_not_followed(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk HOLDS the directory it checked; the write lands in that directory.

    The swap moves the real directory aside and plants a link to the outside
    under its name. The write goes into the descriptor the walk opened -- the
    original directory, now ``sub-old``, still inside -- never through the link.
    """
    project = layout["project"]
    (project / "sub").mkdir()
    (project / "sub" / "real.env").write_text("", encoding="utf-8")
    before = _outside_snapshot(layout)

    def swap() -> None:
        os.rename(project / "sub", project / "sub-old")
        os.symlink("../outside", project / "sub")

    _swap_after_check(monkeypatch, swap)
    atomic_write(project / "sub" / "real.env", b"K=v\n", confine_to=project)

    assert _outside_snapshot(layout) == before
    assert read_env_file(project / "sub-old" / "real.env") == {"K": "v"}


@pytest.mark.parametrize("swap_kind", ["directory to link", "link retargeted"])
def test_a_forced_fallback_refuses_any_link_instead_of_following_a_swap(
    swap_kind: str, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without dir_fd (Windows) a confined write never follows a link at all.

    A pathname check followed by a pathname write is exactly the window a swap
    redirects; the fallback refuses instead (codex F002 on round 2).
    """
    project = layout["project"]
    (project / "sub").mkdir()
    (project / "sub" / "pmcp.env").write_text("OLD=1\n", encoding="utf-8")
    os.symlink("sub/pmcp.env", project / ".env.pmcp")
    before = _outside_snapshot(layout)
    monkeypatch.setattr(atomic_write_module, "_DIR_FD_SUPPORTED", False)
    if swap_kind == "directory to link":
        os.rename(project / "sub", project / "sub-old")
        os.symlink("../outside", project / "sub")
    else:
        os.unlink(project / ".env.pmcp")
        os.symlink("../outside/victim", project / ".env.pmcp")

    with pytest.raises(ConfinedWriteError, match="it is a symlink"):
        set_env_value("project", "K", "v", project)

    assert _outside_snapshot(layout) == before
    assert os.path.islink(project / ".env.pmcp")


def test_a_forced_fallback_still_writes_a_plain_project_store(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(atomic_write_module, "_DIR_FD_SUPPORTED", False)
    set_env_value("project", "K", "v", layout["project"])
    store = layout["project"] / ".env.pmcp"
    assert not store.is_symlink()
    assert read_env_file(store) == {"K": "v"}
    assert stat.S_IMODE(store.stat().st_mode) == 0o600


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_a_forced_fallback_writes_and_reads_a_store_in_nested_real_directories(
    depth: int, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 6 codex F002: below the root is fine when every directory is real."""
    from pmcp.atomic_write import read_confined

    monkeypatch.setattr(atomic_write_module, "_DIR_FD_SUPPORTED", False)
    sub = layout["project"].joinpath(*[f"d{i}" for i in range(depth)])
    sub.mkdir(parents=True)
    store = sub / ".env.pmcp"
    atomic_write(store, b"K=v\n", confine_to=layout["project"])
    assert store.read_bytes() == b"K=v\n" and not store.is_symlink()
    assert read_confined(store, layout["project"]) == b"K=v\n"


@pytest.mark.parametrize("where", ["directory link", "final link"])
def test_a_forced_fallback_still_refuses_any_link_on_a_nested_path(
    where: str, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.atomic_write import read_confined

    monkeypatch.setattr(atomic_write_module, "_DIR_FD_SUPPORTED", False)
    project = layout["project"]
    (project / "real").mkdir()
    if where == "directory link":
        os.symlink("real", project / "d0")
        store = project / "d0" / ".env.pmcp"
    else:
        (project / "d0").mkdir()
        (project / "real" / "s.env").write_bytes(b"K=old\n")
        os.symlink("../real/s.env", project / "d0" / ".env.pmcp")
        store = project / "d0" / ".env.pmcp"
    with pytest.raises(ConfinedWriteError, match="it is a symlink"):
        atomic_write(store, b"K=v\n", confine_to=project)
    with pytest.raises(ConfinedWriteError, match="it is a symlink"):
        read_confined(store, project)


@pytest.mark.skipif(os.name != "posix", reason="dir_fd walk is POSIX-only")
def test_a_final_component_swapped_for_a_link_is_replaced_in_place(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """rename(2) never follows the destination: the planted link is replaced, in-root."""
    project = layout["project"]
    store = project / ".env.pmcp"
    store.write_text("", encoding="utf-8")
    before = _outside_snapshot(layout)

    def swap() -> None:
        os.unlink(store)
        os.symlink(layout["victim"], store)

    _swap_after_check(monkeypatch, swap)
    set_env_value("project", "K", "v", project)

    assert _outside_snapshot(layout) == before
    assert not store.is_symlink()
    assert read_env_file(store) == {"K": "v"}


# --------------------------------------------------------------------------- #
# Structural: every call site states its confinement, and the choice is pinned.
# --------------------------------------------------------------------------- #

#: (file, enclosing function, callee) -> (confine_to expression, why).
CALL_SITES: dict[tuple[str, str, str], tuple[str, str]] = {
    ("env_store.py", "write_env_file", "atomic_write"): (
        "confine_to",
        "passes its own required confine_to through",
    ),
    ("env_store.py", "set_env_value", "write_env_file"): (
        "scope_confinement(scope, path)",
        "project store is repo-controlled -> project root; user store -> None",
    ),
    ("cli_commands/secrets.py", "run_secrets_sync", "write_env_file"): (
        "scope_confinement(to_scope, target_path)",
        "the sync TARGET may be the repo-controlled project store",
    ),
    ("trust_store.py", "_write_store", "atomic_write"): (
        "None",
        "~/.config/pmcp/trust.json, resolved and refused if checkout-resident (C-15)",
    ),
    ("package_approvals.py", "_write_store", "atomic_write"): (
        "None",
        "beside trust.json, resolved and refused if checkout-resident",
    ),
    ("manifest/registry.py", "save_registry_cache", "atomic_write"): (
        "None",
        "$XDG_CACHE_HOME or ~/.cache, or a library caller's explicit path",
    ),
    ("cli.py", "_atomic_write_json", "atomic_write"): (
        "None",
        "pmcp setup targets ~/.mcp.json / ~/.config/opencode/opencode.json",
    ),
}


def _confinement_calls() -> dict[tuple[str, str, str], str]:
    found: dict[tuple[str, str, str], str] = {}

    def visit(node: ast.AST, func: str, rel: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = func
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Call):
                callee = child.func
                callee_name = (
                    callee.id
                    if isinstance(callee, ast.Name)
                    else callee.attr
                    if isinstance(callee, ast.Attribute)
                    else None
                )
                if callee_name in {"atomic_write", "write_env_file"}:
                    kw = {k.arg: k.value for k in child.keywords}
                    key = (rel, func, callee_name)
                    assert key not in found, f"second call at {key}; pin it"
                    found[key] = (
                        ast.unparse(kw["confine_to"])
                        if "confine_to" in kw
                        else "<missing>"
                    )
            visit(child, name, rel)

    for source in sorted(SRC.rglob("*.py")):
        rel = source.relative_to(SRC).as_posix()
        visit(ast.parse(source.read_text(encoding="utf-8")), "<module>", rel)
    return found


def test_every_atomic_write_call_site_states_a_pinned_confinement() -> None:
    found = _confinement_calls()
    assert {k: v for k, v in found.items()} == {
        k: expr for k, (expr, _why) in CALL_SITES.items()
    }


def test_confine_to_is_required_and_keyword_only() -> None:
    import inspect

    for fn in (atomic_write, write_env_file):
        param = inspect.signature(fn).parameters["confine_to"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, fn
        assert param.default is inspect.Parameter.empty, fn


def test_project_env_link_out_of_checkout_is_not_written_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The panel's F001 falsifier, verbatim in substance: `.env.pmcp -> ../.bashrc`."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    victim = home / ".bashrc"
    original = 'alias ll="ls -l"\nPS1=foo\n'
    victim.write_text(original)
    project = home / "repo"
    (project / ".git").mkdir(parents=True)
    (project / ".env.pmcp").symlink_to("../.bashrc")
    with pytest.raises(ConfinedWriteError):
        set_env_value("project", "K", "v", project)
    assert victim.read_text() == original
    assert os.readlink(project / ".env.pmcp") == "../.bashrc"


# --------------------------------------------------------------------------- #
# Every OSError on the write is a REPORTED, value-free refusal at the boundary.
# --------------------------------------------------------------------------- #

_REPORTING = {k: v for k, v in ENTRY_POINTS.items() if k != "set_env_value"}


def _directory_at_store(lay: dict[str, Path]) -> None:
    (lay["project"] / ".env.pmcp").mkdir()


def _read_only_project(lay: dict[str, Path]) -> None:
    os.chmod(lay["project"], 0o500)


def _project_through_a_file(lay: dict[str, Path]) -> None:
    (lay["base"] / "afile").write_text("", encoding="utf-8")
    lay["project"] = lay["base"] / "afile" / ".." / "project"


def _project_through_a_missing_dir(lay: dict[str, Path]) -> None:
    lay["project"] = lay["base"] / "missing" / ".." / "project"


def _project_link_loop(lay: dict[str, Path]) -> None:
    os.symlink("loop-b", lay["base"] / "loop-a")
    os.symlink("loop-a", lay["base"] / "loop-b")
    lay["project"] = lay["base"] / "loop-a"


#: Shapes the SYSTEM refuses, each with the errno it reports. No symlink at
#: the store (that is the "it is a symlink" refusal, tested above): the
#: project path itself, or the store's own type or permissions.
ERRNO_SHAPES: dict[str, tuple[Callable[[dict[str, Path]], None], int]] = {
    "a directory at the store path": (_directory_at_store, errno.EISDIR),
    "a read-only project directory": (_read_only_project, errno.EACCES),
    "a --project through a regular file": (_project_through_a_file, errno.ENOTDIR),
    "a --project through a missing dir": (_project_through_a_missing_dir, errno.ENOENT),
    "a --project that is a link loop": (_project_link_loop, errno.ELOOP),
}


@pytest.mark.parametrize("entry", list(_REPORTING), ids=list(_REPORTING))
@pytest.mark.parametrize("shape", list(ERRNO_SHAPES), ids=list(ERRNO_SHAPES))
async def test_every_os_error_on_the_write_is_a_reported_value_free_refusal(
    shape: str, entry: str, layout: dict[str, Path]
) -> None:
    plant, expected_errno = ERRNO_SHAPES[shape]
    if expected_errno == errno.EACCES and os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    project = layout["project"]
    plant(layout)
    try:
        with pytest.raises(Reported) as info:
            await _REPORTING[entry](layout)
    finally:
        os.chmod(project, 0o700)

    assert info.value.message == (
        f"refusing to write .env.pmcp: {os.strerror(expected_errno)}"
    )
    assert str(layout["base"]) not in info.value.message


# --------------------------------------------------------------------------- #
# The root is the store path's parent by construction, not a second discovery.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "entry", ["set_env_value", "pmcp secrets sync --to-scope project"]
)
async def test_the_confinement_root_cannot_drift_from_the_store_path(
    entry: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second project-root discovery that lands on an ANCESTOR must not widen it.

    Discovery is made to answer the inner project first and its enclosing
    checkout afterwards (a marker vanishing mid-command). The store is the inner
    project's, so its link out to the ancestor must still be refused.
    """
    import pmcp.env_store as env_store_module

    outer = tmp_path / "outer"
    inner = outer / "inner"
    (inner / ".git").mkdir(parents=True)
    os.symlink("../escape.env", inner / ".env.pmcp")
    answers = iter([inner, outer, outer, outer])
    monkeypatch.setattr(
        env_store_module, "resolve_project_root", lambda _project=None: next(answers)
    )
    monkeypatch.chdir(inner)

    if entry == "set_env_value":
        with pytest.raises(ConfinedWriteError, match="it is a symlink"):
            set_env_value("project", "K", "v", None)
    else:
        out = await run_secrets_sync(
            argparse.Namespace(
                from_scope="user", to_scope="project", project=None, overwrite=False
            )
        )
        assert out["ok"] is False and out["error"] == REFUSAL

    assert not (outer / "escape.env").exists()


# --------------------------------------------------------------------------- #
# Round 3: the entry points read a project store only through the write's walk.
# A link the write would refuse is refused BEFORE it is read, and a store that
# is not a regular file is refused instead of read (a fifo would hang).
# --------------------------------------------------------------------------- #

NOT_REGULAR = "refusing to write .env.pmcp: it is not a regular file"
_ALL_ENTRIES = list(ENTRY_POINTS)


def _guarded(entry: str, lay: dict[str, Path]) -> BaseException | None:
    """Run an entry point in a daemon thread; fail if it does not finish in 10s."""
    import asyncio
    import threading

    outcome: list[BaseException | None] = []

    def run() -> None:
        try:
            asyncio.run(ENTRY_POINTS[entry](lay))
            outcome.append(None)
        except BaseException as exc:  # noqa: BLE001 - handed to the test
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(10)
    assert not worker.is_alive(), f"{entry} hung reading the store"
    return outcome[0]


@pytest.mark.parametrize("entry", _ALL_ENTRIES, ids=_ALL_ENTRIES)
def test_an_unreadable_target_outside_is_refused_before_it_is_read(
    entry: str, layout: dict[str, Path]
) -> None:
    """grok F001 (round 3): the leaving link is refused, not read and crashed on."""
    if os.geteuid() == 0:
        pytest.skip("root ignores mode 000")
    victim = layout["victim"]
    os.chmod(victim, 0)
    os.symlink(victim, layout["project"] / ".env.pmcp")
    try:
        outcome = _guarded(entry, layout)
    finally:
        os.chmod(victim, 0o644)
    assert isinstance(outcome, Reported), outcome
    assert outcome.message == REFUSAL
    assert victim.read_text(encoding="utf-8") == VICTIM
    assert os.path.islink(layout["project"] / ".env.pmcp")


def _fifo_store(lay: dict[str, Path]) -> None:
    os.mkfifo(lay["project"] / ".env.pmcp")


def _link_to_fifo(lay: dict[str, Path]) -> None:
    os.mkfifo(lay["project"] / "pipe")
    os.symlink("pipe", lay["project"] / ".env.pmcp")


def _link_to_socket(lay: dict[str, Path]) -> None:
    import socket

    # AF_UNIX paths are short; bind from inside the project by relative name.
    here = os.getcwd()
    os.chdir(lay["project"])
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind("sock")
        sock.close()
    finally:
        os.chdir(here)
    os.symlink("sock", lay["project"] / ".env.pmcp")


def _socket_store(lay: dict[str, Path]) -> None:
    import socket

    here = os.getcwd()
    os.chdir(lay["project"])
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(".env.pmcp")
        sock.close()
    finally:
        os.chdir(here)


#: Non-regular stores, each with the refusal it gets: the store itself is
#: "not a regular file"; a LINK to one is refused as a symlink before its target
#: is ever looked at.
NON_REGULAR = {
    "a fifo at the store path": (_fifo_store, "not regular"),
    "a socket at the store path": (_socket_store, "not regular"),
    "a link to a fifo inside": (_link_to_fifo, "symlink"),
    "a link to a socket inside": (_link_to_socket, "symlink"),
}


@pytest.mark.parametrize("entry", _ALL_ENTRIES, ids=_ALL_ENTRIES)
@pytest.mark.parametrize("shape", list(NON_REGULAR), ids=list(NON_REGULAR))
def test_a_non_regular_store_is_refused_without_hanging(
    shape: str, entry: str, layout: dict[str, Path]
) -> None:
    plant, kind = NON_REGULAR[shape]
    plant(layout)
    outcome = _guarded(entry, layout)
    assert isinstance(outcome, Reported), outcome
    assert outcome.message == (NOT_REGULAR if kind == "not regular" else REFUSAL)


@pytest.mark.skipif(not Path("/proc/self/fd/0").exists(), reason="needs /proc")
@pytest.mark.parametrize("entry", _ALL_ENTRIES, ids=_ALL_ENTRIES)
def test_a_link_to_proc_self_fd_0_is_refused_without_hanging(
    entry: str, layout: dict[str, Path]
) -> None:
    os.symlink("/proc/self/fd/0", layout["project"] / ".env.pmcp")
    outcome = _guarded(entry, layout)
    assert isinstance(outcome, Reported), outcome
    assert outcome.message == REFUSAL


def test_a_sync_from_a_leaving_project_store_is_refused_before_reading_it(
    layout: dict[str, Path],
) -> None:
    """The SOURCE of a sync is read through the walk too."""
    import asyncio

    os.symlink("../outside/victim", layout["project"] / ".env.pmcp")
    out = asyncio.run(
        run_secrets_sync(
            argparse.Namespace(
                from_scope="project",
                to_scope="user",
                project=layout["project"],
                overwrite=False,
            )
        )
    )
    # N2 (round 4): the project store is only the SOURCE here -- "read".
    assert out["ok"] is False and out["error"] == (
        "refusing to read .env.pmcp: it is a symlink"
    )
    assert not (Path.home() / ".config" / "pmcp" / "pmcp.env").exists()


def test_the_store_is_not_opened_before_the_walk_decides(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Order, observed: no open/stat of the outside target precedes the refusal."""
    import builtins

    os.symlink(layout["victim"], layout["project"] / ".env.pmcp")
    touched: list[str] = []
    real_open = builtins.open
    real_os_open = os.open

    def spy_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        touched.append(os.fspath(file) if not isinstance(file, int) else "<fd>")
        return real_open(file, *args, **kwargs)

    def spy_os_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        touched.append(os.fspath(file))
        return real_os_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os, "open", spy_os_open)
    with pytest.raises(ConfinedWriteError):
        set_env_value("project", "K", "v", layout["project"])
    monkeypatch.undo()

    assert not any(".env.pmcp" in t or "victim" in t for t in touched), touched


@pytest.mark.parametrize("entry", list(_REPORTING), ids=list(_REPORTING))
def test_a_write_failure_after_a_clean_read_is_reported(
    entry: str, layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ENOSPC at the write -- an error no read can raise -- is still `ok: false`."""
    if not Path("/proc/self/fd").is_dir():
        pytest.skip("needs /proc to tell the project write from the user store's")
    real_fsync = os.fsync
    project = str(layout["project"].resolve())

    def no_space(fd: int) -> None:
        # Only the PROJECT store's temp: the sync helper seeds the user store first.
        if stat.S_ISREG(os.fstat(fd).st_mode) and os.readlink(
            f"/proc/self/fd/{fd}"
        ).startswith(project + os.sep):
            raise OSError(errno.ENOSPC, "No space left on device")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", no_space)
    outcome = _guarded(entry, layout)
    monkeypatch.undo()
    assert isinstance(outcome, Reported), outcome
    assert outcome.message == (
        f"refusing to write .env.pmcp: {os.strerror(errno.ENOSPC)}"
    )
    assert not (layout["project"] / ".env.pmcp").exists()


# --------------------------------------------------------------------------- #
# Round 4 non-blocking findings.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("entry", _ALL_ENTRIES, ids=_ALL_ENTRIES)
def test_a_project_directory_that_does_not_exist_yet_is_created_not_refused(
    entry: str, tmp_path: Path
) -> None:
    """N1: a missing project directory is an EMPTY store, as on main and d50c4c2."""
    project = tmp_path / "new" / "project"
    lay = {"project": project, "outside": tmp_path, "base": tmp_path}
    outcome = _guarded(entry, lay)
    assert outcome is None, outcome
    assert read_env_file(project / ".env.pmcp")
    assert stat.S_IMODE((project / ".env.pmcp").stat().st_mode) == 0o600


@pytest.mark.parametrize("entry", list(_REPORTING), ids=list(_REPORTING))
def test_a_store_that_is_not_utf8_is_a_reported_value_free_refusal(
    entry: str, layout: dict[str, Path]
) -> None:
    """N3: a UnicodeDecodeError is reported, without the bytes, never raised."""
    store = layout["project"] / ".env.pmcp"
    store.write_bytes(b"K=\xff\xfe-not-utf8\n")
    outcome = _guarded(entry, layout)
    assert isinstance(outcome, Reported), outcome
    assert outcome.message == "refusing to write .env.pmcp: it is not valid UTF-8"
    assert store.read_bytes() == b"K=\xff\xfe-not-utf8\n"


@pytest.mark.parametrize(
    "entry",
    ["pmcp secrets set", "pmcp secrets sync --to-scope project"],
)
def test_a_store_holding_a_multiline_value_is_a_reported_refusal(
    entry: str, layout: dict[str, Path]
) -> None:
    """N3: the rewrite's newline check is reported, not raised, and nothing is written."""
    store = layout["project"] / ".env.pmcp"
    store.write_text('A="l1\nl2"\n', encoding="utf-8")
    outcome = _guarded(entry, layout)
    assert isinstance(outcome, Reported), outcome
    assert outcome.message == (
        "refusing to write .env.pmcp: Credential values must not contain newlines"
    )
    assert store.read_text(encoding="utf-8") == 'A="l1\nl2"\n'


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"BAD-NAME=x\n", "Env var name must match"),
        (b"K=\xff\n", "it is not valid UTF-8"),
    ],
    ids=["invalid key", "not utf-8"],
)
def test_a_sync_source_that_cannot_be_parsed_is_a_reported_read_refusal(
    content: bytes, reason: str, layout: dict[str, Path]
) -> None:
    """N2+N3: the SOURCE is read, so its refusal says "read" and is never raised."""
    import asyncio

    source = layout["project"] / ".env.pmcp"
    source.write_bytes(content)
    out = asyncio.run(
        run_secrets_sync(
            argparse.Namespace(
                from_scope="project",
                to_scope="user",
                project=layout["project"],
                overwrite=False,
            )
        )
    )
    assert out["ok"] is False
    assert str(out["error"]).startswith("refusing to read .env.pmcp: ")
    assert reason in str(out["error"])
    assert not (Path.home() / ".config" / "pmcp" / "pmcp.env").exists()
