"""A file whose path a repository controls is never written outside that repository.

``<project>/.env.pmcp`` lives in a checkout, so a cloned repository can ship it
as a symlink to ``../.bashrc`` or to any path the user can write. The user store
follows its links (a dotfiles repository, see ``test_atomic_write_symlinks``);
the project store must not follow one out of the project. Every shape that
leaves -- a relative link out, an absolute link out, a dangling link out, a
chain that leaves and comes back, a directory component that leaves -- is
REFUSED: the outside file is untouched and the link is left as it was. A link
that stays inside is followed. Driven through the three entry points that write
the project store: ``pmcp secrets set``, ``pmcp secrets sync --to-scope
project`` and ``gateway.auth_connect`` with ``scope="project"``.
"""

from __future__ import annotations

import argparse
import ast
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


REFUSAL = "refusing to write .env.pmcp: it is a symlink that leaves the project"


async def _reported(awaitable: Any) -> Any:
    """The operator-facing entry points must REPORT the refusal, not raise it."""
    try:
        return await awaitable
    except ConfinedWriteError as exc:
        raise AssertionError(
            "the entry point raised instead of reporting the refusal"
        ) from exc


async def _via_set_env_value(lay: dict[str, Path]) -> None:
    set_env_value("project", "K", "v", lay["project"])


async def _via_secrets_set(lay: dict[str, Path]) -> None:
    out = await _reported(
        run_secrets_set(
            argparse.Namespace(
                scope="project", key="K", value="v", project=lay["project"]
            )
        )
    )
    if out["ok"] is False:
        assert out["error"] == REFUSAL
        raise ConfinedWriteError(str(out["error"]))


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
        assert out["error"] == REFUSAL
        raise ConfinedWriteError(str(out["error"]))


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
        assert result.message == REFUSAL
        raise ConfinedWriteError(result.message)


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

    with pytest.raises(ConfinedWriteError, match="leaves the project"):
        await ENTRY_POINTS[entry](layout)

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
async def test_a_project_store_link_that_stays_inside_is_written_through(
    shape: str, entry: str, layout: dict[str, Path]
) -> None:
    target = INSIDE[shape](layout)
    link = layout["project"] / ".env.pmcp"
    link_text = os.readlink(link)
    before = _outside_snapshot(layout)

    await ENTRY_POINTS[entry](layout)

    assert os.path.islink(link) and os.readlink(link) == link_text
    assert target.is_file() and not target.is_symlink()
    assert read_env_file(target), "the target received no entries"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert _outside_snapshot(layout) == before


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


def test_scope_confinement() -> None:
    project = Path.cwd()
    assert scope_confinement("project", project) == project.resolve()
    assert scope_confinement("user") is None
    with pytest.raises(ValueError):
        scope_confinement("global")


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
    with pytest.raises(OSError) as info:
        set_env_value("project", "K", "v", project)
    assert info.value.errno == 40  # ELOOP
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
    real = atomic_write_module.resolve_confined_target

    def checked_then_swapped(path: Path, confine_to: Path, label: str) -> Path:
        result = real(path, confine_to, label)
        swap()
        return result

    monkeypatch.setattr(
        atomic_write_module, "resolve_confined_target", checked_then_swapped
    )


@pytest.mark.skipif(os.name != "posix", reason="dir_fd walk is POSIX-only")
def test_a_directory_swapped_for_a_link_after_the_check_is_not_followed(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project = layout["project"]
    (project / "sub").mkdir()
    (project / "sub" / "real.env").write_text("", encoding="utf-8")
    os.symlink("sub/real.env", project / ".env.pmcp")
    before = _outside_snapshot(layout)

    def swap() -> None:
        os.rename(project / "sub", project / "sub-old")
        os.symlink("../outside", project / "sub")

    _swap_after_check(monkeypatch, swap)
    with pytest.raises(OSError):
        set_env_value("project", "victim", "pwned", project)

    assert _outside_snapshot(layout) == before


@pytest.mark.skipif(os.name != "posix", reason="dir_fd walk is POSIX-only")
def test_a_final_component_swapped_for_a_link_is_replaced_in_place(
    layout: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """rename(2) never follows the destination: the planted link is replaced, in-root."""
    project = layout["project"]
    (project / "real.env").write_text("", encoding="utf-8")
    os.symlink("real.env", project / ".env.pmcp")
    before = _outside_snapshot(layout)

    def swap() -> None:
        os.unlink(project / "real.env")
        os.symlink(layout["victim"], project / "real.env")

    _swap_after_check(monkeypatch, swap)
    set_env_value("project", "K", "v", project)

    assert _outside_snapshot(layout) == before
    assert not (project / "real.env").is_symlink()
    assert read_env_file(project / "real.env") == {"K": "v"}


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
        "scope_confinement(scope, project)",
        "project store is repo-controlled -> project root; user store -> None",
    ),
    ("cli_commands/secrets.py", "run_secrets_sync", "write_env_file"): (
        "scope_confinement(to_scope, project)",
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
