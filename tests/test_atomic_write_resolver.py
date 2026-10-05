"""The confined resolver follows the kernel's rules, component by component.

Round-2 board findings on Consiliency/pmcp#366: the confined walk collapsed
``..`` lexically before resolving the directory symlinks in front of it (codex
F001, grok F001 part 2), split link text on ``os.sep`` only (grok F001 part 1),
and on a platform without ``dir_fd`` wrote by the pathname it had checked, so a
swap in between redirected the write (codex F002).

The generated cases below build link texts from {relative, absolute} x {``..``
after a directory symlink at each depth, ``..`` before one} x {each separator
the platform reads} x {landing inside, leaving}. The oracle is the kernel: after
a confined write, ``open()`` of the ORIGINAL link path must read back exactly the
bytes written; and a write whose walk leaves the project must be refused with
nothing written anywhere.
"""

from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from pmcp import atomic_write as atomic_write_module
from pmcp.atomic_write import ConfinedWriteError, atomic_write, split_link_text

DATA = b"K=generated\n"


@dataclass(frozen=True)
class Case:
    id: str
    text: str  # link text, relative to the project root, with "/" separators
    leaves: bool


def _cases() -> list[Case]:
    """Every shape, derived from the layout's grammar rather than listed by hand.

    Layout: real directories ``a/b/c``; ``s1 -> a``, ``s2 -> a/b``, ``s3 -> a/b/c``
    at the project root; ``o -> ../outside/x/y`` leaving it.
    """
    cases: list[Case] = []
    real = ["a", "b", "c"]
    for k in (1, 2, 3):
        # `..` AFTER a directory symlink of depth k: the kernel resolves s_k
        # first, so n dot-dots land at depth k - n -- never where a lexical
        # collapse of "s_k/.." would put them.
        for n in range(0, k + 2):
            cases.append(
                Case(
                    f"s{k}+{n}dotdot",
                    "/".join([f"s{k}", *[".."] * n, "f.env"]),
                    leaves=k - n < 0,
                )
            )
        # `..` BEFORE the symlink, at each depth d: d real dirs, d dot-dots
        # back to the root, then the link; one extra `..` leaves the root.
        for d in (1, 2):
            for extra in (0, 1):
                cases.append(
                    Case(
                        f"{d}deep{'+1' if extra else ''}..-then-s{k}..",
                        "/".join(
                            [*real[:d], *[".."] * (d + extra), f"s{k}", "..", "f.env"]
                        ),
                        leaves=bool(extra),
                    )
                )
    # A directory link that leaves: out, out-and-up, out-and-back-in.
    cases += [
        Case("o/f", "o/f.env", leaves=True),
        Case("o/../f", "o/../f.env", leaves=True),
        Case("o/../../../project/f", "o/../../../project/f.env", leaves=True),
    ]
    return cases


CASES = _cases()
SEPARATORS = sorted({"/", os.sep})


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    base = Path(os.path.realpath(tmp_path))
    project = base / "project"
    (project / "a" / "b" / "c").mkdir(parents=True)
    (base / "outside" / "x" / "y").mkdir(parents=True)
    os.symlink("a", project / "s1")
    os.symlink("a/b", project / "s2")
    os.symlink("a/b/c", project / "s3")
    os.symlink("../outside/x/y", project / "o")
    return base, project


def _every_f_env(base: Path) -> list[Path]:
    found = []
    for dirpath, _dirs, files in os.walk(base, followlinks=False):
        found += [Path(dirpath, f) for f in files if f == "f.env"]
    return found


@pytest.mark.parametrize("sep", SEPARATORS, ids=[repr(s) for s in SEPARATORS])
@pytest.mark.parametrize("spelling", ["relative", "absolute"])
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_a_confined_write_lands_where_the_kernel_resolves_or_is_refused(
    case: Case, spelling: str, sep: str, tree: tuple[Path, Path]
) -> None:
    base, project = tree
    text = case.text.replace("/", sep)
    if spelling == "absolute":
        text = str(project) + sep + text
    link = project / ".env.pmcp"
    os.symlink(text, link)
    kernel_target = Path(os.path.realpath(link))

    if case.leaves:
        with pytest.raises(ConfinedWriteError):
            atomic_write(link, DATA, confine_to=project)
        assert _every_f_env(base) == [], "a refused write still created a file"
    else:
        assert kernel_target.is_relative_to(project), case
        written = atomic_write(link, DATA, confine_to=project)
        assert written == kernel_target
        # The kernel oracle: opening the ORIGINAL path reads what was written.
        with open(link, "rb") as handle:
            assert handle.read() == DATA
        assert _every_f_env(base) == [kernel_target]
    assert os.path.islink(link) and os.readlink(link) == text


def test_the_generator_covers_both_outcomes_at_every_depth() -> None:
    """Positive control: the grammar is not vacuously one-sided."""
    for k in (1, 2, 3):
        mine = [c for c in CASES if c.id.startswith(f"s{k}+")]
        assert any(c.leaves for c in mine) and any(not c.leaves for c in mine)


def test_link_text_is_split_on_every_separator_the_platform_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Git records `../.bashrc` with `/`; Windows also reads `\\`. Both split."""
    monkeypatch.setattr(os, "sep", "\\")
    monkeypatch.setattr(os, "altsep", "/")
    assert split_link_text("../.bashrc") == ["..", ".bashrc"]
    assert split_link_text("..\\.bashrc") == ["..", ".bashrc"]
    assert split_link_text("a/b\\c") == ["a", "b", "c"]


# --------------------------------------------------------------------------- #
# The round-2 board falsifiers, as written.
# --------------------------------------------------------------------------- #


def test_codex_f001_absolute_link_dotdot_writes_the_real_target(
    tmp_path: Path,
) -> None:
    tmp_path = Path(os.path.realpath(tmp_path))
    project = tmp_path / "project"
    (project / "storage" / "child").mkdir(parents=True)
    (project / "hop").symlink_to("storage/child", target_is_directory=True)
    target = project / "storage" / "pmcp.env"
    target.write_bytes(b"OLD=1\n")
    unrelated = project / "pmcp.env"
    unrelated.write_bytes(b"UNRELATED=1\n")
    link = project / ".env.pmcp"
    link.symlink_to(str(project / "hop") + "/../pmcp.env")
    assert link.resolve() == target

    atomic_write(link, b"OLD=1\nNEW=2\n", confine_to=project)

    assert unrelated.read_bytes() == b"UNRELATED=1\n"
    assert target.read_bytes() == b"OLD=1\nNEW=2\n"
    assert link.is_symlink()


def test_codex_f002_fallback_cannot_write_through_a_swapped_directory(
    tmp_path: Path,
) -> None:
    writer = atomic_write_module
    project = tmp_path / "project"
    sub = project / "sub"
    sub.mkdir(parents=True)
    (sub / "pmcp.env").write_bytes(b"OLD=1\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "pmcp.env"
    victim.write_bytes(b"OUTSIDE=untouched\n")
    link = project / ".env.pmcp"
    link.symlink_to("sub/pmcp.env")
    resolve = writer.resolve_confined_target

    def resolve_then_swap(path: Path, confine_to: Path, label: str) -> Path:
        target = resolve(path, confine_to, label)
        sub.rename(project / "sub-old")
        sub.symlink_to("../outside", target_is_directory=True)
        return target

    with (
        patch.object(writer, "_DIR_FD_SUPPORTED", False),
        patch.object(writer, "resolve_confined_target", resolve_then_swap),
    ):
        try:
            writer.atomic_write(link, b"USER_SECRET=private\n", confine_to=project)
        except OSError:
            pass

    assert victim.read_bytes() == b"OUTSIDE=untouched\n"


_ORIGINAL = "ORIGINAL\n"


def test_grok_f001_a_project_symlink_that_leaves_is_refused_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures: list[str] = []
    tmp_path = Path(os.path.realpath(tmp_path))

    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    victim = outside / "victim"
    victim.write_text(_ORIGINAL, encoding="utf-8")
    (project / "escape").symlink_to(outside)
    absolute = str(project / "escape" / ".." / "planted")
    (project / ".env.pmcp").symlink_to(absolute)
    try:
        atomic_write(project / ".env.pmcp", b"K=secret\n", confine_to=project)
    except ConfinedWriteError:
        pass
    else:
        failures.append(
            f"absolute link {absolute} was written to the lexically collapsed path"
        )
    if (project / "planted").exists():
        failures.append("secret written to planted inside the project")
    if (tmp_path / "planted").exists():
        failures.append("secret written outside the project via escape/..")
    if victim.read_text(encoding="utf-8") != _ORIGINAL:
        failures.append("outside victim was modified")
    if not os.path.islink(project / ".env.pmcp"):
        failures.append("the leaving symlink was replaced")

    root = "C:\\work\\project"
    store = root + "\\.env.pmcp"
    link_text = "../.bashrc"
    escaped = ntpath.normpath(ntpath.join(root, link_text))
    assert not (escaped == root or escaped.startswith(root.rstrip("\\") + "\\")), (
        escaped
    )

    def islink(path: object) -> bool:
        return ntpath.normpath(str(path)) == ntpath.normpath(store)

    def isdir(path: object) -> bool:
        # Win32 stat() resolves ".." and accepts "/"; C:\work exists.
        return ntpath.normpath(str(path)) in {"C:\\", "C:\\work", "C:\\work\\project"}

    def readlink(path: object) -> str:
        if ntpath.normpath(str(path)) == ntpath.normpath(store):
            return link_text
        raise OSError(path)

    written: list[str] = []

    def record_write(
        target: Path, data: bytes, *, mode: int, prefix: str, suffix: str
    ) -> None:
        written.append(str(target))

    monkeypatch.setattr(os, "sep", "\\")
    monkeypatch.setattr(os, "path", ntpath)
    monkeypatch.setattr(os, "readlink", readlink)
    monkeypatch.setattr(ntpath, "islink", islink)
    monkeypatch.setattr(ntpath, "isdir", isdir)
    monkeypatch.setattr(atomic_write_module, "_DIR_FD_SUPPORTED", False)
    monkeypatch.setattr(atomic_write_module, "_write_by_path", record_write)

    try:
        atomic_write(Path(store), b"K=secret\n", confine_to=Path(root))
    except ConfinedWriteError:
        pass
    else:
        norm = ntpath.normpath(written[0]) if written else escaped
        failures.append(
            f"windows target {link_text!r} normalizes to {norm}, outside {root}"
        )
    if written:
        norm = ntpath.normpath(written[0])
        if not (norm == root or norm.startswith(root.rstrip("\\") + "\\")):
            failures.append(f"windows write path {norm} leaves {root}")

    monkeypatch.undo()
    assert failures == []


@pytest.mark.skipif(os.name != "posix", reason="dir_fd walk is POSIX-only")
def test_a_directory_swapped_between_lstat_and_open_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window inside the walk: `lstat` saw a directory, `open` meets a link.

    The directory open is O_NOFOLLOW, so the swapped-in link fails it (ELOOP)
    instead of carrying the walk -- and the write -- outside the project.
    """
    base = Path(os.path.realpath(tmp_path))
    project = base / "project"
    (project / "sub").mkdir(parents=True)
    outside = base / "outside"
    outside.mkdir()
    os.symlink("sub/x.env", project / ".env.pmcp")
    real_stat = os.stat
    swapped: list[bool] = []

    def stat_then_swap(name: object, *args: object, **kwargs: object) -> object:
        result = real_stat(name, *args, **kwargs)  # type: ignore[arg-type]
        if name == "sub" and kwargs.get("dir_fd") is not None and not swapped:
            swapped.append(True)
            os.rename(project / "sub", project / "sub-old")
            os.symlink(outside, project / "sub")
        return result

    monkeypatch.setattr(os, "stat", stat_then_swap)
    with pytest.raises(OSError):
        atomic_write(project / ".env.pmcp", DATA, confine_to=project)
    monkeypatch.undo()

    assert swapped, "the seam never fired"
    assert list(outside.iterdir()) == []


def test_grok_round3_f001_unreadable_project_store_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-3 board falsifier (grok F001), as written."""
    import argparse
    import asyncio

    from pmcp.cli_commands.secrets import run_secrets_set, run_secrets_sync

    refusal = "refusing to write .env.pmcp: it is a symlink that leaves the project"
    if os.geteuid() == 0:
        pytest.skip("root ignores mode 000")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    victim = tmp_path / "outside" / "secret"
    victim.parent.mkdir()
    victim.write_text("ORIGINAL\n", encoding="utf-8")
    os.chmod(victim, 0)
    os.symlink(victim, project / ".env.pmcp")

    async def _set() -> dict[str, object]:
        return await run_secrets_set(
            argparse.Namespace(scope="project", key="K", value="v", project=project)
        )

    async def _sync() -> dict[str, object]:
        return await run_secrets_sync(
            argparse.Namespace(
                from_scope="user",
                to_scope="project",
                project=project,
                overwrite=False,
            )
        )

    try:
        for out in (asyncio.run(_set()), asyncio.run(_sync())):
            assert out["ok"] is False
            message = str(out["error"])
            assert message == refusal
            assert str(tmp_path) not in message
            assert "secret" not in message
            assert "ORIGINAL" not in message
    finally:
        os.chmod(victim, 0o600)

    assert victim.read_text(encoding="utf-8") == "ORIGINAL\n"
    assert os.path.islink(project / ".env.pmcp")
