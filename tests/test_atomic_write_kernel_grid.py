"""Every walk the writer can take agrees with the kernel, on a generated grid.

Round 7 on Consiliency/pmcp#366 (claude seat):

* F002 -- an ABSOLUTE link ending in ``/`` or ``/.`` was resolved as if the
  trailing part were absent; the kernel refuses such a path (``ENOTDIR``), so
  the user store read as empty and ``secrets set`` rewrote the linked file with
  only the new key. Relative links were already right.
* F001 -- the walk opened every directory it passed ``O_RDONLY``, which needs
  READ permission; the kernel needs only SEARCH. A search-only ancestor (a
  hardened ``/home`` at 0711) refused every user-scope write.

The grid is generated from a small grammar, never hand-listed: link texts are
{relative, absolute} spellings of a base path, x {trailing nothing, ``/``,
``/.``, ``//``, ``/./``}, x {the link as the FINAL component, or as an
INTERMEDIATE directory component}, over bases that are a file, a directory, a
missing name, a missing directory, a directory link and ``..`` after one. Each
case runs on every walk the writer has: descriptors confined and unconfined,
with and without ``O_PATH``, and the no-``dir_fd`` fallback. The oracle is the
kernel itself: ``open(O_WRONLY|O_CREAT|O_TRUNC)`` through the same link in an
identical copy of the tree. Outcome (refused or not) and every byte of the
resulting tree must match. The fallback's confined walk refuses every link, so
there the assertion is a refusal that changes nothing.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

from pmcp import atomic_write as writer
from pmcp.atomic_write import atomic_write, read_confined

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX symlink semantics")

DATA = b"K=grid\n"

#: Bases a FINAL link may point at, relative to the project root.
FINAL_BASES = {
    "file": "conf/b.env",
    "dir": "conf",
    "missing": "conf/new.env",
    "missing-dir": "nodir/new.env",
    "dotdot-after-dir-link": "hop/../x.env",
    "root-file": "x.env",
}
#: Bases an INTERMEDIATE (directory) link may point at.
MID_BASES = {
    "dir": "conf",
    "subdir": "conf/sub",
    "dir-link": "hop",
    "dotdot-after-dir-link": "hop/..",
    "missing-dir": "nodir",
    "file": "conf/b.env",
}
SUFFIXES = {
    "none": "",
    "slash": "/",
    "slash-dot": "/.",
    "slash-slash": "//",
    "slash-dot-slash": "/./",
}
SPELLINGS = ("relative", "absolute")


@dataclass(frozen=True)
class Case:
    id: str
    position: str  # "final" | "intermediate"
    spelling: str
    base: str
    suffix: str


def _cases() -> list[Case]:
    cases = []
    for position, bases in (("final", FINAL_BASES), ("intermediate", MID_BASES)):
        for base_id, base in bases.items():
            for suffix_id, suffix in SUFFIXES.items():
                for spelling in SPELLINGS:
                    cases.append(
                        Case(
                            f"{position}-{spelling}-{base_id}-{suffix_id}",
                            position,
                            spelling,
                            base,
                            suffix,
                        )
                    )
    return cases


CASES = _cases()
WALKS = (
    "dir_fd confined",
    "dir_fd unconfined",
    "dir_fd confined, no O_PATH",
    "dir_fd unconfined, no O_PATH",
    "fallback unconfined",
    "fallback confined",
)


def _build(root: Path, case: Case) -> Path:
    """The tree, and the store link for ``case``, under ``root``."""
    project = root / "project"
    (project / "conf" / "sub").mkdir(parents=True)
    (project / "conf" / "b.env").write_bytes(b"B=1\n")
    (project / "x.env").write_bytes(b"X=1\n")
    os.symlink("conf/sub", project / "hop")
    text = case.base + case.suffix
    if case.spelling == "absolute":
        text = str(project) + "/" + text
    store = project / ".env.pmcp"
    if case.position == "final":
        os.symlink(text, store)
    else:
        os.symlink(text, project / "mid")
        os.symlink("mid/b2.env", store)
    return store


def _tree(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            p = Path(dirpath, name)
            if not p.is_symlink() and not name.startswith(".pmcp-"):
                out[str(p.relative_to(root))] = p.read_bytes()
    return out


def _kernel(store: Path) -> bool:
    """Write through ``store`` the way the kernel resolves it; True if refused."""
    try:
        fd = os.open(store, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except OSError:
        return True
    try:
        os.write(fd, DATA)
    finally:
        os.close(fd)
    return False


def _configure(walk: str, monkeypatch: pytest.MonkeyPatch) -> bool:
    """Select the walk; return whether it is confined."""
    if walk.startswith("fallback"):
        monkeypatch.setattr(writer, "_DIR_FD_SUPPORTED", False)
    elif not writer._DIR_FD_SUPPORTED:
        pytest.skip("no dir_fd on this platform")
    if "no O_PATH" in walk:
        monkeypatch.setattr(writer, "_O_PATH", 0)
    elif walk.startswith("dir_fd") and not writer._O_PATH:
        pytest.skip("no O_PATH on this platform")
    return "unconfined" not in walk


@pytest.mark.parametrize("walk", WALKS)
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_the_writer_resolves_every_shape_as_the_kernel_does(
    case: Case, walk: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = Path(os.path.realpath(tmp_path))
    kernel_store = _build(base / "kernel", case)
    writer_store = _build(base / "writer", case)
    before = _tree(base / "writer")
    confined = _configure(walk, monkeypatch)

    kernel_refused = _kernel(kernel_store)
    try:
        atomic_write(
            writer_store,
            DATA,
            confine_to=writer_store.parent if confined else None,
        )
    except OSError:
        writer_refused = True
    else:
        writer_refused = False

    if walk == "fallback confined":
        # Every case has a link on the way; the no-dir_fd confined walk
        # refuses them all and changes nothing.
        assert writer_refused, case.id
        assert _tree(base / "writer") == before
        return
    assert writer_refused == kernel_refused, (case.id, walk)
    assert _tree(base / "writer") == _tree(base / "kernel"), (case.id, walk)


@pytest.mark.parametrize("walk", ("dir_fd confined", "dir_fd confined, no O_PATH"))
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_the_confined_read_agrees_with_the_kernel_too(
    case: Case, walk: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read half of a confined rewrite resolves the same way."""
    base = Path(os.path.realpath(tmp_path))
    store = _build(base, case)
    _configure(walk, monkeypatch)
    try:
        with open(store, "rb") as handle:
            kernel: bytes | None | str = handle.read()
    except FileNotFoundError:
        kernel = None
    except OSError:
        kernel = "refused"
    # ENOENT is "not there" on both sides: the kernel's open says it for a
    # missing directory on the way as well as for a missing file, and so may
    # the walk (the write it precedes would fail the same way).
    try:
        got: bytes | None | str = read_confined(store, store.parent)
    except FileNotFoundError:
        got = None
    except OSError:
        got = "refused"
    assert got == kernel, (case.id, walk)


def test_the_grid_covers_both_outcomes_for_each_suffix() -> None:
    """Positive control: per suffix, the kernel both accepts and refuses something."""
    import tempfile

    outcomes: dict[str, set[bool]] = {}
    with tempfile.TemporaryDirectory() as d:
        for i, case in enumerate(CASES):
            store = _build(Path(os.path.realpath(d)) / str(i), case)
            outcomes.setdefault(case.suffix, set()).add(_kernel(store))
    assert all(v == {True, False} for v in outcomes.values()), outcomes


# --------------------------------------------------------------------------- #
# F001: search-only directories on the way, as the kernel allows.
# --------------------------------------------------------------------------- #

needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores directory permissions"
)


@needs_non_root
@pytest.mark.parametrize("use_o_path", [True, False], ids=["O_PATH", "no O_PATH"])
def test_a_user_store_under_a_search_only_ancestor_is_written(
    use_o_path: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hardened /home (0711): the user can search it, not list it."""
    from pmcp.env_store import read_env_file, set_env_value

    if use_o_path and not writer._O_PATH:
        pytest.skip("no O_PATH")
    if not use_o_path:
        monkeypatch.setattr(writer, "_O_PATH", 0)
    homes = Path(os.path.realpath(tmp_path)) / "homes"
    home = homes / "u"
    store = home / ".config" / "pmcp" / "pmcp.env"
    store.parent.mkdir(parents=True)
    store.write_text("KEEP=1\n", encoding="utf-8")
    # A dotfiles target under a second search-only directory, too.
    dots = Path(os.path.realpath(tmp_path)) / "dots"
    (dots / "d").mkdir(parents=True)
    (dots / "d" / "trust.json").write_text("{}\n", encoding="utf-8")
    os.chmod(homes, 0o311)
    os.chmod(dots, 0o311)
    monkeypatch.setenv("HOME", str(home))
    try:
        set_env_value("user", "NEW", "v")
        assert read_env_file(store) == {"KEEP": "1", "NEW": "v"}
        link = home / "linked.json"
        os.symlink(dots / "d" / "trust.json", link)
        atomic_write(link, b"[]\n", confine_to=None)
        assert (dots / "d" / "trust.json").read_bytes() == b"[]\n"
    finally:
        os.chmod(homes, 0o755)
        os.chmod(dots, 0o755)


@needs_non_root
@pytest.mark.parametrize("use_o_path", [True, False], ids=["O_PATH", "no O_PATH"])
def test_a_project_under_a_search_only_ancestor_is_written_and_read(
    use_o_path: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.env_store import read_store_for_update, set_env_value

    if use_o_path and not writer._O_PATH:
        pytest.skip("no O_PATH")
    if not use_o_path:
        monkeypatch.setattr(writer, "_O_PATH", 0)
    ancestor = Path(os.path.realpath(tmp_path)) / "srv"
    project = ancestor / "proj"
    (project / ".git").mkdir(parents=True)
    (project / ".env.pmcp").write_text("KEEP=1\n", encoding="utf-8")
    os.chmod(ancestor, 0o311)
    try:
        set_env_value("project", "NEW", "v", project)
        assert read_store_for_update("project", project / ".env.pmcp") == {
            "KEEP": "1",
            "NEW": "v",
        }
    finally:
        os.chmod(ancestor, 0o755)


@needs_non_root
def test_a_search_only_directory_inside_the_project_is_walked_with_o_path(
    tmp_path: Path,
) -> None:
    """Inside the root, too: only O_PATH lets a descriptor walk pass it."""
    if not writer._O_PATH or not writer._DIR_FD_SUPPORTED:
        pytest.skip("needs O_PATH and dir_fd")
    project = Path(os.path.realpath(tmp_path)) / "proj"
    target_dir = project / "vault" / "real"
    target_dir.mkdir(parents=True)
    os.symlink("vault/real/s.env", project / ".env.pmcp")
    os.chmod(project / "vault", 0o311)
    try:
        atomic_write(project / ".env.pmcp", DATA, confine_to=project)
        assert read_confined(project / ".env.pmcp", project) == DATA
        assert stat.S_IMODE((target_dir / "s.env").stat().st_mode) == 0o600
    finally:
        os.chmod(project / "vault", 0o755)


@pytest.mark.skipif(not getattr(os, "O_PATH", 0), reason="needs O_PATH")
def test_an_o_path_walk_still_refuses_a_directory_swapped_for_a_link(
    tmp_path: Path,
) -> None:
    """The confinement property O_PATH must not change: a link is not entered."""
    base = Path(os.path.realpath(tmp_path))
    (base / "d").mkdir()
    os.symlink("d", base / "l")
    root = os.open(base, writer._walk_flags())
    try:
        with pytest.raises(OSError):
            os.open("l", writer._walk_flags() | os.O_NOFOLLOW, dir_fd=root)
    finally:
        os.close(root)


def test_board_r7_f002_absolute_link_with_trailing_slash_is_refused_like_the_kernel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seat's data-loss shape: a user store linked to `<dotfiles>/pmcp.env/`."""
    from pmcp.env_store import set_env_value

    base = Path(os.path.realpath(tmp_path))
    dotfiles = base / "dotfiles" / "pmcp.env"
    dotfiles.parent.mkdir()
    dotfiles.write_text("KEEP1=alpha\nKEEP2=beta\n", encoding="utf-8")
    home = base / "home"
    link = home / ".config" / "pmcp" / "pmcp.env"
    link.parent.mkdir(parents=True)
    os.symlink(str(dotfiles) + "/", link)
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(OSError):
        set_env_value("user", "NEW", "v")
    assert dotfiles.read_text(encoding="utf-8") == "KEEP1=alpha\nKEEP2=beta\n"
