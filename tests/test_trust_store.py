"""Tests for the user-scoped trust store (Consiliency/pmcp#230, IF-0-TRUST-1).

Every test runs with ``cwd`` inside a *fake checkout* and ``HOME`` outside it,
because the store's residency rule is a property of where the store file lives
relative to the checkout being examined. Approving files that live *inside* the
checkout is the normal case -- that is what every CONSENT caller will do.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from datetime import datetime
from pathlib import Path

import pytest

from pmcp.trust_store import (
    TrustStoreError,
    is_approved,
    list_records,
    record,
    revoke,
    trust_store_path,
)


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout to work in, with the user's home deliberately outside it."""
    checkout_dir = tmp_path / "checkout"
    (checkout_dir / ".git").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(checkout_dir)
    return checkout_dir


def _write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_a_record_round_trips(checkout: Path) -> None:
    """An approval survives a write/read cycle, and re-approving replaces it."""
    target = _write(checkout / "server.py", b"print('hello')")

    written = record(target, b"print('hello')", "user", "approved")

    assert written.absolute_path == target.resolve()
    assert written.decision == "approved"
    assert written.scope == "user"
    assert written.recorded_at.tzinfo is not None
    assert isinstance(written.recorded_at, datetime)

    store = trust_store_path()
    assert store.exists(), "record() must persist to the store file"
    assert stat.S_IMODE(store.stat().st_mode) == 0o600

    # Read back through a fresh call, not the returned object.
    (loaded,) = list_records()
    assert loaded.absolute_path == target.resolve()
    assert loaded.content_sha256 == written.content_sha256
    assert loaded.decision == "approved"
    assert is_approved(target, b"print('hello')") is True

    # One record per absolute path: re-approving replaces, it never appends.
    record(target, b"print('goodbye')", "user", "approved")
    assert len(list_records()) == 1
    assert is_approved(target, b"print('goodbye')") is True
    assert is_approved(target, b"print('hello')") is False

    assert revoke(target) is True
    assert list_records() == []
    assert revoke(target) is False


def test_changed_content_is_no_longer_approved(checkout: Path) -> None:
    """Approval binds to the content that was approved, not to the path."""
    target = _write(checkout / "server.py", b"safe")
    record(target, b"safe", "user", "approved")

    assert is_approved(target, b"safe") is True
    assert is_approved(target, b"safe ") is False
    assert is_approved(target, b"rm -rf /") is False


def test_an_unknown_path_is_not_approved(checkout: Path) -> None:
    """Absence is never assent -- with an empty store or a populated one."""
    unknown = _write(checkout / "unknown.py", b"whatever")

    assert is_approved(unknown, b"whatever") is False

    other = _write(checkout / "known.py", b"known")
    record(other, b"known", "user", "approved")

    assert is_approved(unknown, b"whatever") is False


def test_an_unreadable_store_fails_closed(checkout: Path) -> None:
    """Any I/O or parse failure answers False; it never raises to the caller.

    A caller that treats an exception as permission would be granted trust by a
    corrupt file, so the predicate must swallow and refuse.
    """
    target = _write(checkout / "server.py", b"payload")
    record(target, b"payload", "user", "approved")
    store = trust_store_path()
    assert is_approved(target, b"payload") is True

    store.write_text("{ not json at all", encoding="utf-8")
    assert is_approved(target, b"payload") is False

    store.write_text(json.dumps(["not", "a", "store"]), encoding="utf-8")
    assert is_approved(target, b"payload") is False

    store.write_text(
        json.dumps({"records": [{"absolute_path": str(target)}]}), encoding="utf-8"
    )
    assert is_approved(target, b"payload") is False

    # An unreadable store: a directory where the file should be (an OSError that
    # does not depend on this process's uid, unlike chmod 0o000 under root).
    store.unlink()
    store.mkdir()
    assert is_approved(target, b"payload") is False


def test_a_store_inside_the_checkout_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout-resident store would let a repo ship its own approval."""
    checkout_dir = tmp_path / "checkout"
    (checkout_dir / ".git").mkdir(parents=True)
    home = checkout_dir / "home"  # HOME *inside* the checkout
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(checkout_dir)

    target = _write(checkout_dir / "server.py", b"payload")

    with pytest.raises(TrustStoreError):
        trust_store_path()
    with pytest.raises(TrustStoreError):
        record(target, b"payload", "user", "approved")

    assert is_approved(target, b"payload") is False
    assert not (home / ".config" / "pmcp" / "trust.json").exists()


def test_a_symlinked_store_into_the_checkout_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The residency check resolves symlinks before comparing.

    HOME is outside the checkout here, so only a check that resolves the store
    path sees that the store really lands inside the repository.
    """
    checkout_dir = tmp_path / "checkout"
    (checkout_dir / ".git").mkdir(parents=True)
    (checkout_dir / "planted").mkdir()
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".config" / "pmcp").symlink_to(checkout_dir / "planted")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(checkout_dir)

    target = _write(checkout_dir / "server.py", b"payload")

    with pytest.raises(TrustStoreError):
        trust_store_path()
    with pytest.raises(TrustStoreError):
        record(target, b"payload", "user", "approved")

    assert is_approved(target, b"payload") is False
    assert not (checkout_dir / "planted" / "trust.json").exists()


def test_is_approved_judges_supplied_bytes_not_the_path(checkout: Path) -> None:
    """The predicate hashes the bytes the caller is about to apply.

    Re-reading the file would reopen the check-then-use window the freeze exists
    to close, so the on-disk bytes must not influence the answer at all.
    """
    target = _write(checkout / "server.py", b"A")
    record(target, b"A", "user", "approved")

    # On-disk bytes are still A; the caller is about to apply B.
    assert target.read_bytes() == b"A"
    assert is_approved(target, b"B") is False

    # On-disk bytes swapped to C after approval; the caller still applies A.
    target.write_bytes(b"C")
    assert is_approved(target, b"A") is True
    assert is_approved(target, b"C") is False

    # A deleted file is not an error: the bytes, not the path, are the subject.
    target.unlink()
    assert is_approved(target, b"A") is True


def test_a_denied_record_is_not_approved(checkout: Path) -> None:
    """``denied`` is a recorded decision, not merely an absent one."""
    target = _write(checkout / "server.py", b"payload")

    denied = record(target, b"payload", "user", "denied")
    assert denied.decision == "denied"

    (loaded,) = list_records()
    assert loaded.decision == "denied"  # present in the store...
    assert is_approved(target, b"payload") is False  # ...but never approved

    # The vocabulary is closed: nothing else may be stored.
    with pytest.raises(ValueError):
        record(target, b"payload", "user", "approved-ish")
    with pytest.raises(ValueError):
        record(target, b"payload", "user", "APPROVED")

    # A denial is replaced by a later approval, one record per path throughout.
    record(target, b"payload", "user", "approved")
    assert len(list_records()) == 1
    assert is_approved(target, b"payload") is True


def test_concurrent_record_and_revoke_do_not_resurrect_an_approval(
    checkout: Path,
) -> None:
    """A revoke must not be undone by a concurrent approve's stale snapshot.

    ``record`` and ``revoke`` each read every record, change one, and write the
    whole set back. Unserialized, they interleave into a lost update: record
    reads a snapshot containing A, revoke removes A, record writes its snapshot
    back and A is approved again -- an operator's withdrawal silently reversed.
    Both now hold an exclusive lock for the whole read-modify-write.

    Falsifier: remove the `_store_lock` from either function and this resurrects
    `victim` within a few iterations.
    """
    victim = _write(checkout / "victim.json", b"victim")
    other = _write(checkout / "other.json", b"other")

    for _ in range(40):
        record(victim, b"victim", "user", "approved")
        barrier = threading.Barrier(2)

        def approve_other() -> None:
            barrier.wait()
            record(other, b"other", "user", "approved")

        def revoke_victim() -> None:
            barrier.wait()
            revoke(victim)

        threads = [
            threading.Thread(target=approve_other),
            threading.Thread(target=revoke_victim),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not is_approved(victim, b"victim"), (
            "a concurrent approve resurrected a revoked approval"
        )
        revoke(other)


def test_a_store_directory_pmcp_creates_is_not_group_or_world_accessible(
    checkout: Path,
) -> None:
    """A directory PMCP creates for the store is 0o700, not the umask default.

    The store file is 0o600, but the directory around it matters too: at the
    common umask of 022 an untightened directory is 0o755, and at 002 it is
    0o775 -- group-writable, so another account in the group can rename or
    replace `trust.json` wholesale even though it cannot read the old one.

    Falsifier: this caught a real regression. Adding the lock introduced a
    second `mkdir` that ran before `_write_store`'s, so `_write_store` saw an
    existing directory, skipped its chmod, and a fresh install silently got the
    umask default. Give `_store_lock` and `_write_store` independent "did we
    create it?" decisions again and this fails.
    """
    target = _write(checkout / "server.json", b"payload")
    record(target, b"payload", "user", "approved")

    store = trust_store_path()
    assert store.stat().st_mode & 0o777 == 0o600
    assert store.parent.stat().st_mode & 0o777 == 0o700, (
        "the store directory must not be group- or world-accessible"
    )


def test_every_directory_pmcp_creates_is_private_including_intermediates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No component is group- or world-accessible, even transiently.

    Two measured facts drive this. `Path.mkdir(parents=True, mode=...)` ignores
    `mode` for the intermediates it creates, so a freshly created `~/.config`
    lands 0o775 under umask 002 while only the leaf is tightened. And creating
    loosely then tightening leaves a window in which another account in the
    user's group can insert a forged `trust.json` that a later `record()` reads
    and carries forward -- the store file being 0o600 does not help, because the
    attack replaces the file rather than reading it.

    Falsifier: restore `parent.mkdir(parents=True)` plus a trailing chmod and
    the intermediate assertion fails under this umask.
    """
    home = tmp_path / "home"
    home.mkdir()
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(checkout)
    monkeypatch.setattr(os, "umask", lambda _mask: 0o002, raising=False)
    old_mask = os.umask(0o002)
    try:
        target = _write(checkout / "server.json", b"payload")
        record(target, b"payload", "user", "approved")

        store = trust_store_path()
        assert store.parent.stat().st_mode & 0o777 == 0o700
        # the intermediate PMCP created on the way down, e.g. ~/.config
        intermediate = store.parent.parent
        assert intermediate != home, "test must exercise a created intermediate"
        assert intermediate.stat().st_mode & 0o777 == 0o700, (
            f"{intermediate} was created group/world-accessible"
        )
    finally:
        os.umask(old_mask)


def test_a_directory_fsync_failure_does_not_fail_a_completed_write(
    checkout: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Durability is best effort; the write already landed.

    Directory fsync is unsupported on macOS (EINVAL), on NFS and some overlay
    mounts (ENOTSUP), and `os.open` on a directory fails outright on Windows.
    Because the fsync runs AFTER `os.replace`, raising would report a failed
    `revoke` for a revoke that succeeded -- and an operator told their
    withdrawal did not take, when it did, is the worst direction for this tool
    to be wrong in.

    Falsifier: drop the `except OSError` around the fsync and this raises
    OSError(22) while `is_approved` already reads False.
    """
    target = _write(checkout / "server.json", b"payload")
    record(target, b"payload", "user", "approved")

    real_fsync = os.fsync

    def fails_on_directories(fd: int) -> None:
        if os.fstat(fd).st_mode & 0o170000 == 0o040000:
            raise OSError(22, "Invalid argument")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fails_on_directories)

    assert revoke(target) is True, "a completed revoke must not report failure"
    assert not is_approved(target, b"payload")
