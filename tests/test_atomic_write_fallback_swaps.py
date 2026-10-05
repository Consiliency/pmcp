"""No confined open trusts a pathname between its check and its use.

Round 4 on Consiliency/pmcp#366 (codex F001): the no-``dir_fd`` (Windows) read
fallback ``lstat``-ed the store, then ``os.open``-ed the same NAME, which follows
a link -- so a store swapped for a link to an outside file in between was read.
The class is every open that comes after a pathname check, read or write:

* a read, on either path, must read exactly the file its check saw -- the
  descriptor's ``st_dev``/``st_ino`` must match the entry -- and opens
  ``O_NOFOLLOW`` wherever the platform has it;
* a write's temporary is created by name and renamed by name: the entry must
  still be the file just written when it is renamed, so a swap of the temp's
  name cannot move a link (or any other file) into place.

Each swap below is injected at the exact step between the check and the use.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from pmcp import atomic_write as writer
from pmcp.atomic_write import ConfinedWriteError, atomic_write, read_confined

OUTSIDE = b"OUTSIDE_TOKEN=synthetic-proof\n"


@pytest.fixture
def tree(tmp_path: Path) -> dict[str, Path]:
    base = Path(os.path.realpath(tmp_path))
    project = base / "project"
    project.mkdir()
    store = project / ".env.pmcp"
    store.write_bytes(b"LOCAL=1\n")
    outside = base / "outside.env"
    outside.write_bytes(OUTSIDE)
    return {"base": base, "project": project, "store": store, "outside": outside}


def _swap_on_open(
    monkeypatch: pytest.MonkeyPatch,
    match: Callable[[Any, dict[str, Any]], bool],
    swap: Callable[[], None],
) -> None:
    real_open = os.open
    fired: list[bool] = []

    def swapping_open(file: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if not fired and match(file, kwargs):
            fired.append(True)
            swap()
        return real_open(file, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)


def _to_outside_link(t: dict[str, Path]) -> Callable[[], None]:
    def swap() -> None:
        t["store"].unlink()
        t["store"].symlink_to(t["outside"])

    return swap


def _to_other_inside_file(t: dict[str, Path]) -> Callable[[], None]:
    def swap() -> None:
        other = t["project"] / "other"
        other.write_bytes(b"SWAPPED=1\n")
        os.replace(other, t["store"])

    return swap


# --------------------------------------------------------------------------- #
# The codex falsifier, as written.
# --------------------------------------------------------------------------- #


def test_codex_r4_f001_fallback_read_cannot_follow_swapped_symlink(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    store = project / ".env.pmcp"
    store.write_bytes(b"LOCAL=1\n")
    outside = tmp_path / "outside.env"
    outside.write_bytes(b"OUTSIDE_TOKEN=synthetic-proof\n")
    original_open = os.open

    def swap_then_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if os.fspath(path) == str(store):
            store.unlink()
            store.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    with patch.object(writer, "_DIR_FD_SUPPORTED", False):
        assert writer.read_confined(store, project) == b"LOCAL=1\n"
        with patch.object(writer.os, "open", swap_then_open):
            try:
                result = writer.read_confined(store, project)
            except OSError:
                return

    assert result != outside.read_bytes(), (
        "The confined fallback followed a swapped link and read outside the project"
    )


# --------------------------------------------------------------------------- #
# Reads: the descriptor must be the file the check saw.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "o_nofollow", [True, False], ids=["O_NOFOLLOW", "no-O_NOFOLLOW"]
)
@pytest.mark.parametrize("swap_kind", ["to an outside link", "to another inside file"])
def test_the_fallback_read_refuses_a_swap_between_its_check_and_its_open(
    swap_kind: str,
    o_nofollow: bool,
    tree: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even where the platform has no O_NOFOLLOW (Windows), identity decides."""
    monkeypatch.setattr(writer, "_DIR_FD_SUPPORTED", False)
    if not o_nofollow and hasattr(os, "O_NOFOLLOW"):
        monkeypatch.delattr(os, "O_NOFOLLOW")
    swap = (
        _to_outside_link(tree)
        if swap_kind == "to an outside link"
        else _to_other_inside_file(tree)
    )
    _swap_on_open(monkeypatch, lambda f, kw: os.fspath(f) == str(tree["store"]), swap)

    with pytest.raises(ConfinedWriteError) as info:
        read_confined(tree["store"], tree["project"])

    # Round 5 N-C: the O_NOFOLLOW open's ELOOP is the same refusal, not a raw
    # "Too many levels of symbolic links".
    assert str(info.value) == (
        "refusing to write .env.pmcp: it changed while it was being read"
    )


@pytest.mark.skipif(not writer._DIR_FD_SUPPORTED, reason="dir_fd walk")
def test_the_dir_fd_read_refuses_a_file_swapped_between_its_stat_and_its_open(
    tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _swap_on_open(
        monkeypatch,
        lambda f, kw: f == ".env.pmcp" and kw.get("dir_fd") is not None,
        _to_other_inside_file(tree),
    )
    with pytest.raises(ConfinedWriteError, match="it changed while it was being read"):
        read_confined(tree["store"], tree["project"])


def test_the_fallback_read_opens_o_nofollow_where_the_platform_has_it(
    tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("no O_NOFOLLOW on this platform")
    monkeypatch.setattr(writer, "_DIR_FD_SUPPORTED", False)
    real_open = os.open
    flags_seen: list[int] = []

    def spy(file: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if os.fspath(file) == str(tree["store"]):
            flags_seen.append(flags)
        return real_open(file, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    assert read_confined(tree["store"], tree["project"]) == b"LOCAL=1\n"
    assert flags_seen and all(f & os.O_NOFOLLOW for f in flags_seen)


# --------------------------------------------------------------------------- #
# Writes: the temporary's name must still be the file written when renamed.
# --------------------------------------------------------------------------- #


def _swap_temp_after_fsync(
    monkeypatch: pytest.MonkeyPatch, directory: Path, outside: Path
) -> list[str]:
    """After the temp's data fsync, replace the temp NAME with a link outside."""
    real_fsync = os.fsync
    swapped: list[str] = []

    def fsync_then_swap(fd: int) -> None:
        real_fsync(fd)
        if swapped:
            return
        temps = [p for p in directory.iterdir() if p.name.startswith(".pmcp-")]
        if len(temps) == 1 and not temps[0].is_symlink():
            swapped.append(temps[0].name)
            temps[0].unlink()
            temps[0].symlink_to(outside)

    monkeypatch.setattr(os, "fsync", fsync_then_swap)
    return swapped


@pytest.mark.parametrize(
    "path_kind", ["dir_fd write", "no-dir_fd write", "unconfined write"]
)
def test_a_temp_swapped_before_the_rename_is_refused_not_moved_into_place(
    path_kind: str, tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    if path_kind == "dir_fd write" and not writer._DIR_FD_SUPPORTED:
        pytest.skip("dir_fd walk")
    if path_kind == "no-dir_fd write":
        monkeypatch.setattr(writer, "_DIR_FD_SUPPORTED", False)
    swapped = _swap_temp_after_fsync(monkeypatch, tree["project"], tree["outside"])
    confine = None if path_kind == "unconfined write" else tree["project"]

    with pytest.raises(
        ConfinedWriteError,
        match="its temporary file changed before it could be committed",
    ):
        atomic_write(tree["store"], b"NEW=1\n", confine_to=confine)

    monkeypatch.undo()
    assert swapped, "the seam never fired"
    assert tree["store"].read_bytes() == b"LOCAL=1\n"
    assert not tree["store"].is_symlink()
    assert tree["outside"].read_bytes() == OUTSIDE
    assert [
        p.name for p in tree["project"].iterdir() if p.name.startswith(".pmcp-")
    ] == []


@pytest.mark.skipif(not writer._DIR_FD_SUPPORTED, reason="dir_fd walk")
def test_a_link_swapped_in_before_the_dir_fd_read_open_is_the_changed_refusal(
    tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """N-C on the dir_fd path: ELOOP from O_NOFOLLOW maps to the same message."""
    _swap_on_open(
        monkeypatch,
        lambda f, kw: f == ".env.pmcp" and kw.get("dir_fd") is not None,
        _to_outside_link(tree),
    )
    with pytest.raises(ConfinedWriteError) as info:
        read_confined(tree["store"], tree["project"])
    assert str(info.value) == (
        "refusing to write .env.pmcp: it changed while it was being read"
    )


# --------------------------------------------------------------------------- #
# The honest boundary (round 5, codex F001): a swap of the temporary's NAME in
# the instant between its last check and the rename is NOT refused -- POSIX has
# no rename-by-descriptor. That needs a concurrent process already running as
# the user and writing into the store's directory, which is out of scope (it can
# write the user's files directly). What still holds, and is pinned here:
# nothing is written outside, and the outside file is untouched.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", ["dir_fd", "fallback", "unconfined"])
def test_a_temp_swapped_after_its_last_check_is_out_of_scope_but_writes_nothing_outside(
    kind: str, tree: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    if kind == "dir_fd" and not writer._DIR_FD_SUPPORTED:
        pytest.skip("dir_fd walk")
    monkeypatch.setattr(writer, "_DIR_FD_SUPPORTED", kind != "fallback")
    real_replace = os.replace
    swapped: list[bool] = []

    def swap_then_replace(src: Any, dst: Any, **kwargs: Any) -> None:
        temporary = Path(src)
        if not temporary.is_absolute():
            temporary = tree["project"] / temporary
        temporary.unlink()
        temporary.symlink_to(tree["outside"])
        swapped.append(True)
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", swap_then_replace)
    atomic_write(
        tree["store"],
        b"NEW=submitted\n",
        confine_to=None if kind == "unconfined" else tree["project"],
    )
    monkeypatch.undo()

    assert swapped
    # The guarantee: nothing outside was written.
    assert tree["outside"].read_bytes() == OUTSIDE
    # The documented residual: the swapped-in entry was moved into place.
    assert tree["store"].is_symlink()
