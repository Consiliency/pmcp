"""Both write rules agree with the kernel, on a grid generated from a grammar.

Round 8 on Consiliency/pmcp#366 (owner's ruling): stop reimplementing kernel
path resolution. The unconfined (operator-owned) rule follows only the final
link chain by pathname and hands every directory to the kernel; the confined
(repository-controlled) rule refuses any symlink and any ``..`` and otherwise
lets the kernel do the rest. Both should agree with the kernel by construction,
and this grid checks it.

Grammar, never hand-listed:

* a component's KIND on disk: real dir, link->dir, link->file, link->missing,
  regular file, missing, mode-000 dir, mode-311 dir;
* how the step through it is SPELLED: ``c/x``, ``c/./x``, ``c/../x``,
  ``c//x``, and a ``//``-prefixed absolute spelling;
* a TRAILING form on the end: nothing, ``/``, ``/.``.

Unconfined: the user store is a link whose text is that path; the oracle is the
kernel's ``open(O_WRONLY|O_CREAT|O_TRUNC)`` through the same link in an
identical tree, and outcome plus every byte must match. Confined: the store
path itself is built from the grammar (pathlib already folds ``.`` and empty
components); the oracle is the kernel, EXCEPT that a symlink anywhere or a
``..`` must be a refusal that changes nothing -- the ruling.
"""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

from pmcp import atomic_write as writer
from pmcp.atomic_write import ConfinedWriteError, atomic_write, read_confined

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX semantics")

DATA = b"K=grid\n"
KINDS = (
    "real dir",
    "link->dir",
    "link->file",
    "link->missing",
    "regular file",
    "missing",
    "mode-000 dir",
    "mode-311 dir",
)
SPELLINGS = ("name", "dot", "dotdot", "empty", "double-slash-absolute")
TRAILING = {"none": "", "slash": "/", "slash-dot": "/."}
_LINKS = {"link->dir", "link->file", "link->missing"}

needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores directory permissions"
)


@dataclass(frozen=True)
class Case:
    id: str
    kind: str
    spelling: str
    trailing: str


CASES = [
    Case(f"{k}-{sp}-{t}", k, sp, t) for k in KINDS for sp in SPELLINGS for t in TRAILING
]


def _build(base: Path, kind: str) -> Path:
    """A directory ``base`` holding component ``c`` of ``kind``; returns ``base``."""
    base.mkdir(parents=True)
    (base / "x.env").write_bytes(b"BASE=1\n")
    (base / "afile").write_bytes(b"AFILE=1\n")
    real = base / "realdir"
    real.mkdir()
    (real / "x.env").write_bytes(b"REAL=1\n")
    c = base / "c"
    if kind in ("real dir", "mode-000 dir", "mode-311 dir"):
        c.mkdir()
        (c / "x.env").write_bytes(b"C=1\n")
    elif kind == "link->dir":
        os.symlink("realdir", c)
    elif kind == "link->file":
        os.symlink("afile", c)
    elif kind == "link->missing":
        os.symlink("nowhere", c)
    elif kind == "regular file":
        c.write_bytes(b"CFILE=1\n")
    return base


def _lock(base: Path, kind: str) -> None:
    if kind == "mode-000 dir":
        os.chmod(base / "c", 0o000)
    elif kind == "mode-311 dir":
        os.chmod(base / "c", 0o311)


def _unlock(base: Path) -> None:
    c = base / "c"
    if c.is_dir() and not c.is_symlink():
        os.chmod(c, 0o755)


def _spell(base: Path, case: Case) -> str:
    step = {
        "name": "c/x.env",
        "dot": "c/./x.env",
        "dotdot": "c/../x.env",
        "empty": "c//x.env",
        "double-slash-absolute": "/" + str(base / "c" / "x.env"),
    }[case.spelling]
    if case.spelling != "double-slash-absolute":
        step = str(base / step)
    return step + TRAILING[case.trailing]


def _tree(root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            p = Path(dirpath, name)
            if not p.is_symlink() and not name.startswith(".pmcp-"):
                out[str(p.relative_to(root))] = p.read_bytes()
    return out


def _kernel_write(where: str) -> bool:
    """Open ``where`` for writing the kernel's way; True if refused."""
    try:
        fd = os.open(where, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except OSError:
        return True
    try:
        os.write(fd, DATA)
    finally:
        os.close(fd)
    return False


def _skip_if_root(case: Case) -> None:
    if os.geteuid() == 0 and case.kind.startswith("mode-"):
        pytest.skip("root ignores directory permissions")


# --------------------------------------------------------------------------- #
# Unconfined: the user store links to a grammar-built path.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("dir_fd", [True, False], ids=["dir_fd", "no dir_fd"])
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_an_unconfined_write_agrees_with_the_kernel(
    case: Case, dir_fd: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_if_root(case)
    monkeypatch.setattr(
        writer, "_DIR_FD_SUPPORTED", dir_fd and writer._DIR_FD_SUPPORTED
    )
    root = Path(os.path.realpath(tmp_path))
    k_base = _build(root / "kernel", case.kind)
    w_base = _build(root / "writer", case.kind)
    k_link = root / "kernel-store.env"
    w_link = root / "writer-store.env"
    os.symlink(_spell(k_base, case), k_link)
    os.symlink(_spell(w_base, case), w_link)
    _lock(k_base, case.kind)
    _lock(w_base, case.kind)
    try:
        kernel_refused = _kernel_write(str(k_link))
        try:
            atomic_write(w_link, DATA, confine_to=None)
            writer_refused = False
        except OSError:
            writer_refused = True
    finally:
        _unlock(k_base)
        _unlock(w_base)

    assert writer_refused == kernel_refused, case.id
    assert _tree(w_base) == _tree(k_base), case.id
    assert os.path.islink(w_link)


# --------------------------------------------------------------------------- #
# Confined: the store path itself is grammar-built, inside the project.
# --------------------------------------------------------------------------- #

CONFINED_CASES = [c for c in CASES if c.spelling != "double-slash-absolute"]


def _must_refuse(case: Case) -> bool:
    return case.kind in _LINKS or case.spelling == "dotdot"


@pytest.mark.parametrize("dir_fd", [True, False], ids=["dir_fd", "no dir_fd"])
@pytest.mark.parametrize("role", ["intermediate", "final"])
@pytest.mark.parametrize("case", CONFINED_CASES, ids=[c.id for c in CONFINED_CASES])
def test_a_confined_write_agrees_with_the_kernel_or_refuses_any_link(
    case: Case,
    role: str,
    dir_fd: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_if_root(case)
    monkeypatch.setattr(
        writer, "_DIR_FD_SUPPORTED", dir_fd and writer._DIR_FD_SUPPORTED
    )
    root = Path(os.path.realpath(tmp_path))
    k_base = _build(root / "kernel", case.kind)
    w_base = _build(root / "writer", case.kind)

    def store(base: Path) -> str:
        if role == "final":
            # The component IS the store; the spelling is the step to it.
            step = {
                "name": "c",
                "dot": "./c",
                "dotdot": "realdir/../c",
                "empty": "/c",
            }[case.spelling]
            return f"{base}/{step}"
        return _spell(base, case)[: -len(TRAILING[case.trailing]) or None]

    before = _tree(w_base)  # before any directory is locked
    _lock(k_base, case.kind)
    _lock(w_base, case.kind)
    try:
        kernel_refused = _kernel_write(store(k_base))
        try:
            atomic_write(Path(store(w_base)), DATA, confine_to=w_base)
            writer_refused = False
            refusal = None
        except OSError as exc:
            writer_refused = True
            refusal = exc
    finally:
        _unlock(k_base)
        _unlock(w_base)

    if _must_refuse(case):
        assert writer_refused, case.id
        assert isinstance(refusal, ConfinedWriteError), (case.id, refusal)
        assert _tree(w_base) == before
        return
    assert writer_refused == kernel_refused, (case.id, role, refusal)
    assert _tree(w_base) == _tree(k_base), (case.id, role)


@pytest.mark.parametrize("case", CONFINED_CASES, ids=[c.id for c in CONFINED_CASES])
def test_a_confined_read_agrees_with_the_kernel_or_refuses_any_link(
    case: Case, tmp_path: Path
) -> None:
    _skip_if_root(case)
    base = _build(Path(os.path.realpath(tmp_path)) / "p", case.kind)
    path = _spell(base, case)[: -len(TRAILING[case.trailing]) or None]
    _lock(base, case.kind)
    try:
        try:
            with open(path, "rb") as handle:
                kernel: object = handle.read()
        except FileNotFoundError:
            kernel = None
        except OSError:
            kernel = "refused"
        try:
            got: object = read_confined(Path(path), base)
        except FileNotFoundError:
            got = None
        except ConfinedWriteError:
            got = "link-refused"
        except OSError:
            got = "refused"
    finally:
        _unlock(base)
    if _must_refuse(case):
        assert got == "link-refused", case.id
    else:
        assert got == kernel, case.id


def test_the_grid_has_both_outcomes() -> None:
    """Positive control: the kernel accepts some unconfined shapes and refuses others."""
    import tempfile

    seen = set()
    with tempfile.TemporaryDirectory() as d:
        for i, case in enumerate(CASES):
            if case.kind.startswith("mode-"):
                continue
            base = _build(Path(os.path.realpath(d)) / str(i), case.kind)
            seen.add(_kernel_write(_spell(base, case)))
    assert seen == {True, False}


# --------------------------------------------------------------------------- #
# Search-only directories on the way, as the kernel allows.
# --------------------------------------------------------------------------- #


@needs_non_root
def test_a_user_store_under_a_search_only_ancestor_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hardened /home (0711): searchable, not listable."""
    from pmcp.env_store import read_env_file, set_env_value

    homes = Path(os.path.realpath(tmp_path)) / "homes"
    home = homes / "u"
    store = home / ".config" / "pmcp" / "pmcp.env"
    store.parent.mkdir(parents=True)
    store.write_text("KEEP=1\n", encoding="utf-8")
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
def test_a_project_under_a_search_only_ancestor_is_written_and_read(
    tmp_path: Path,
) -> None:
    from pmcp.env_store import read_store_for_update, set_env_value

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
def test_a_search_only_directory_inside_the_project_is_walked(tmp_path: Path) -> None:
    if not writer._O_PATH or not writer._DIR_FD_SUPPORTED:
        pytest.skip("needs O_PATH and dir_fd")
    project = Path(os.path.realpath(tmp_path)) / "proj"
    target_dir = project / "vault" / "real"
    target_dir.mkdir(parents=True)
    store = target_dir / "s.env"
    os.chmod(project / "vault", 0o311)
    try:
        atomic_write(store, DATA, confine_to=project)
        assert read_confined(store, project) == DATA
        assert stat.S_IMODE(store.stat().st_mode) == 0o600
    finally:
        os.chmod(project / "vault", 0o755)


@pytest.mark.skipif(not getattr(os, "O_PATH", 0), reason="needs O_PATH")
def test_an_o_path_walk_still_refuses_a_directory_swapped_for_a_link(
    tmp_path: Path,
) -> None:
    base = Path(os.path.realpath(tmp_path))
    (base / "d").mkdir()
    os.symlink("d", base / "l")
    root = os.open(base, writer._walk_flags())
    try:
        with pytest.raises(OSError) as info:
            os.open("l", writer._walk_flags() | os.O_NOFOLLOW, dir_fd=root)
        assert info.value.errno in (errno.ENOTDIR, errno.ELOOP)
    finally:
        os.close(root)


def test_o_path_is_used_wherever_the_platform_has_it() -> None:
    assert writer._O_PATH == getattr(os, "O_PATH", 0)
    if writer._O_PATH:
        assert writer._walk_flags() & writer._O_PATH


def test_board_r7_f002_absolute_link_with_trailing_slash_is_refused_like_the_kernel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round-7 seat's data-loss shape: a user store linked to `<file>/`."""
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


@pytest.mark.parametrize(
    "spelling",
    ["missing/../x", "afile/../x", "missing/./x", "plain/new/x"],
)
def test_make_store_dirs_creates_only_a_plain_missing_tail(
    spelling: str, tmp_path: Path
) -> None:
    """Never `mkdir -p` past a component the kernel refuses (round 8, codex F002)."""
    base = Path(os.path.realpath(tmp_path))
    (base / "afile").write_bytes(b"")
    (base / "x").mkdir()
    before = sorted(p.name for p in base.iterdir())
    target = f"{base}/{spelling}"
    if spelling == "plain/new/x":
        created = writer.make_store_dirs(target)
        assert [os.path.relpath(c, base) for c in created] == [
            "plain",
            "plain/new",
            "plain/new/x",
        ]
        assert all(stat.S_IMODE(os.stat(c).st_mode) == 0o700 for c in created)
        return
    with pytest.raises(OSError):
        writer.make_store_dirs(target)
    assert sorted(p.name for p in base.iterdir()) == before
