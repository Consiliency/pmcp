"""Tests for singleton lock scope behavior.

These tests verify that:
1. Global lock is used by default (per-user, not per-project)
2. CLI --lock-dir flag overrides the default
3. Environment variable PMCP_LOCK_DIR provides alternative override
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


from pmcp.server import GatewayServer
from pmcp.identity import acquire_singleton_lock, release_singleton_lock


class TestGlobalLockDefault:
    """Verify global lock is used by default."""

    def setup_method(self) -> None:
        """Release any held locks before each test."""
        release_singleton_lock()

    def teardown_method(self) -> None:
        """Release locks after each test."""
        release_singleton_lock()

    def test_gateway_server_lock_dir_defaults_to_none(self) -> None:
        """GatewayServer should default lock_dir to None (global lock)."""
        server = GatewayServer()
        assert server._lock_dir is None, (
            "GatewayServer._lock_dir should default to None for global lock"
        )

    def test_gateway_server_accepts_explicit_lock_dir(self, tmp_path: Path) -> None:
        """GatewayServer should accept explicit lock_dir parameter."""
        server = GatewayServer(lock_dir=tmp_path)
        assert server._lock_dir == tmp_path, (
            "GatewayServer should store explicit lock_dir"
        )

    def test_gateway_server_lock_dir_converts_string_to_path(
        self, tmp_path: Path
    ) -> None:
        """GatewayServer should convert string lock_dir to Path."""
        server = GatewayServer(lock_dir=str(tmp_path))
        assert server._lock_dir == tmp_path, (
            "GatewayServer should convert string lock_dir to Path"
        )


class TestCLILockDirFlag:
    """Verify --lock-dir CLI flag works correctly."""

    def test_cli_has_lock_dir_option(self) -> None:
        """CLI should have --lock-dir option."""
        # Run pmcp --help and check output
        result = subprocess.run(
            [sys.executable, "-m", "pmcp", "--help"],
            capture_output=True,
            text=True,
        )

        assert "--lock-dir" in result.stdout, "CLI should have --lock-dir option"

    def test_cli_lock_dir_help_text(self) -> None:
        """CLI --lock-dir should have descriptive help text."""
        result = subprocess.run(
            [sys.executable, "-m", "pmcp", "--help"],
            capture_output=True,
            text=True,
        )

        # Check that help text mentions the default location
        assert "lock" in result.stdout.lower(), (
            "CLI help should mention lock functionality"
        )

    def test_cli_help_includes_examples_and_env_overrides(self) -> None:
        """Top-level help should include usage examples and env vars."""
        result = subprocess.run(
            [sys.executable, "-m", "pmcp", "--help"],
            capture_output=True,
            text=True,
        )

        assert "Examples:" in result.stdout
        assert "pmcp refresh --force" in result.stdout
        assert "PMCP_CONFIG" in result.stdout

    def test_lock_dir_env_var_recognized(self) -> None:
        """PMCP_LOCK_DIR environment variable should be documented in CLI."""
        # The env var support is verified by checking the parse_args implementation
        import inspect
        from pmcp.cli import parse_args

        source = inspect.getsource(parse_args)
        # Check that PMCP_LOCK_DIR is referenced in the parser setup
        # Note: argparse handles env vars differently than click, so we check
        # the run_server function instead
        from pmcp.cli import run_server

        source = inspect.getsource(run_server)
        assert "PMCP_LOCK_DIR" in source, (
            "run_server should check PMCP_LOCK_DIR environment variable"
        )


class TestLockScopeIntegration:
    """Integration tests for lock scope behavior."""

    def setup_method(self) -> None:
        release_singleton_lock()

    def teardown_method(self) -> None:
        release_singleton_lock()

    def test_two_servers_same_lock_dir_fails(self, tmp_path: Path) -> None:
        """Two lock acquisitions with same lock_dir should conflict."""
        lock_dir = tmp_path / "locks"
        lock_dir.mkdir()

        # First acquisition succeeds
        result1 = acquire_singleton_lock(lock_dir)
        assert result1 is True

        # Second acquisition fails
        result2 = acquire_singleton_lock(lock_dir)
        assert result2 is False

    def test_release_then_reacquire_succeeds(self, tmp_path: Path) -> None:
        """Lock can be reacquired after release."""
        lock_dir = tmp_path / "locks"
        lock_dir.mkdir()

        # First acquisition
        result1 = acquire_singleton_lock(lock_dir)
        assert result1 is True

        # Release
        release_singleton_lock()

        # Second acquisition succeeds after release
        result2 = acquire_singleton_lock(lock_dir)
        assert result2 is True

    def test_gateway_server_run_stdio_uses_lock_dir(self) -> None:
        """Verify _run_stdio uses self._lock_dir."""
        import inspect
        from pmcp.server import GatewayServer

        source = inspect.getsource(GatewayServer._run_stdio)
        assert "acquire_singleton_lock(self._lock_dir)" in source, (
            "_run_stdio should pass self._lock_dir to acquire_singleton_lock"
        )

    def test_gateway_server_run_http_uses_lock_dir(self) -> None:
        """Verify _run_http uses self._lock_dir."""
        import inspect
        from pmcp.server import GatewayServer

        source = inspect.getsource(GatewayServer._run_http)
        assert "acquire_singleton_lock(self._lock_dir)" in source, (
            "_run_http should pass self._lock_dir to acquire_singleton_lock"
        )


class TestLockDirDocumentation:
    """Verify lock_dir is properly documented."""

    def test_readme_documents_lock_behavior(self) -> None:
        """README should document singleton lock behavior."""
        readme_path = Path(__file__).parent.parent / "README.md"
        readme_content = readme_path.read_text()

        # Check for lock documentation
        assert "lock" in readme_content.lower(), (
            "README should document singleton lock behavior"
        )

    def test_readme_documents_lock_dir_override(self) -> None:
        """README should document --lock-dir override."""
        readme_path = Path(__file__).parent.parent / "README.md"
        readme_content = readme_path.read_text()

        assert "--lock-dir" in readme_content, (
            "README should document --lock-dir CLI option"
        )


class TestWindowsLockPath:
    """On native Windows the singleton lock must use msvcrt, never the Unix-only
    fcntl module whose import crashed gateway startup (issue #84)."""

    def setup_method(self) -> None:
        release_singleton_lock()

    def teardown_method(self) -> None:
        release_singleton_lock()

    def test_windows_uses_msvcrt_and_never_imports_fcntl(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        import types

        import pmcp.identity as identity

        calls: list[int] = []

        fake_msvcrt = types.ModuleType("msvcrt")
        fake_msvcrt.LK_NBLCK = 2  # type: ignore[attr-defined]
        fake_msvcrt.LK_UNLCK = 0  # type: ignore[attr-defined]

        def _locking(fileno: int, mode: int, nbytes: int) -> None:
            calls.append(mode)

        fake_msvcrt.locking = _locking  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

        # Force the Windows branch, and make `import fcntl` raise — so if the code
        # ever falls back to fcntl on Windows the test fails (the #84 regression).
        monkeypatch.setattr(identity.sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "fcntl", None)

        lock_dir = tmp_path / "locks"
        assert acquire_singleton_lock(lock_dir) is True
        assert (lock_dir / "gateway.lock").exists()
        # Took a non-blocking exclusive lock via msvcrt.
        assert fake_msvcrt.LK_NBLCK in calls  # type: ignore[attr-defined]

        release_singleton_lock()
        # Released via msvcrt as well.
        assert fake_msvcrt.LK_UNLCK in calls  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Board round 31 (codex F001, claude N-1): removing the lock file at shutdown,
# even by identity, let a successor that locked the same inode in between lose
# its lock to a third gateway creating a fresh file -- two "singletons". The
# lock file is now never removed: release is unlock and close. A gateway that
# locks a file the path no longer names retries from the open.
# --------------------------------------------------------------------------- #


def _gateways(n: int) -> list[Any]:
    """Independent copies of pmcp.identity: separate opens take independent,
    real OS locks even within one process."""
    import importlib.util

    from pmcp import identity

    copies = []
    for number in range(n):
        spec = importlib.util.spec_from_file_location(
            f"gateway_{number}", identity.__file__
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Contention is expected in these tests: a short probe-wait window.
        module._CONTENDED_WAIT_SECONDS = 0.1
        copies.append(module)
    return copies


def _holds_the_file_at(fd: Any, path: Path) -> bool:
    """Is the locked descriptor the entry at ``path`` itself (not followed)?"""
    held = os.fstat(fd.fileno())
    try:
        named = os.lstat(path)
    except OSError:
        return False
    return (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino)


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")


@posix_only
def test_codex_r31_f001_shutdown_preserves_the_next_gateways_lock(
    tmp_path: Path,
) -> None:
    """As filed, with the injection point it patched (the removed
    ``_remove_own_lock``) replaced by the moment after release returns --
    after unlock and close, which is all release does now."""
    old, successor, contender = _gateways(3)
    try:
        assert old.acquire_singleton_lock(tmp_path)
        assert not successor.acquire_singleton_lock(tmp_path)
        old.release_singleton_lock()
        assert successor.acquire_singleton_lock(tmp_path)
        assert successor._LOCK_FD is not None
        assert not contender.acquire_singleton_lock(tmp_path), (
            "Shutdown unlinked the successor's live lock; a third gateway "
            "acquired a different inode while the successor still held its lock"
        )
    finally:
        for gateway in (old, successor, contender):
            gateway.release_singleton_lock()


@posix_only
@pytest.mark.parametrize(
    "between",
    [
        "A releases",
        "A releases, C acquires",
        "A releases, the file is unlinked, C acquires",
        "A releases, the file is replaced, C acquires",
        "C tries, A releases",
    ],
)
def test_claude_r31_n1_three_instances_never_two_holders(
    between: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A holds the singleton; B is starting, and between B's OPEN and B's
    LOCK the steps named happen (the unlink stands in for an older pmcp that
    removed the file). Afterwards at most one gateway holds a lock, and the
    one that does holds the file at the path."""
    a, b, c = _gateways(3)
    lock = tmp_path / "gateway.lock"
    assert a.acquire_singleton_lock(tmp_path)
    real = b._lock_fd_exclusive
    fired: list[bool] = []

    def interleaved(fd: Any) -> None:
        if not fired:
            fired.append(True)
            for step in [s.strip() for s in between.split(",")]:
                if step == "A releases":
                    a.release_singleton_lock()
                elif step == "C acquires":
                    assert c.acquire_singleton_lock(tmp_path)
                elif step == "C tries":
                    assert not c.acquire_singleton_lock(tmp_path)
                elif step == "the file is unlinked":
                    lock.unlink()
                elif step == "the file is replaced":
                    (tmp_path / "gateway.lock.new").write_text("")
                    os.replace(tmp_path / "gateway.lock.new", lock)
        real(fd)

    monkeypatch.setattr(b, "_lock_fd_exclusive", interleaved)
    try:
        b.acquire_singleton_lock(tmp_path)
        assert fired
        holders = [m for m in (a, b, c) if m._LOCK_FD is not None]
        assert len(holders) == 1, f"{between}: {len(holders)} holders"
        (holder,) = holders
        assert _holds_the_file_at(holder._LOCK_FD, lock)
        # And nobody else can take it now.
        (late,) = _gateways(1)
        assert not late.acquire_singleton_lock(tmp_path)
    finally:
        for gateway in (a, b, c):
            gateway.release_singleton_lock()


@posix_only
@pytest.mark.parametrize("change", ["unlinked", "replaced"])
def test_a_lock_file_changed_between_open_and_lock_is_retried(
    change: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acquirer never treats a lock on a file the path no longer names as
    held: it re-opens and ends up holding the inode at the path."""
    from pmcp import identity

    lock = tmp_path / "gateway.lock"
    real = identity._lock_fd_exclusive
    changed: list[int] = []

    def lock_after_a_change(fd: Any) -> None:
        if not changed:
            changed.append(os.fstat(fd.fileno()).st_ino)
            if change == "unlinked":
                lock.unlink()
            else:
                (tmp_path / "gateway.lock.new").write_text("")
                os.replace(tmp_path / "gateway.lock.new", lock)
        real(fd)

    monkeypatch.setattr(identity, "_lock_fd_exclusive", lock_after_a_change)
    monkeypatch.setattr(identity, "_LOCK_FD", None)
    try:
        assert identity.acquire_singleton_lock(tmp_path)
        assert changed, "the seam never fired"
        held = os.fstat(identity._LOCK_FD.fileno())
        named = os.stat(lock)
        assert (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino)
    finally:
        identity.release_singleton_lock()


def test_a_leftover_lock_file_never_blocks_a_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file a dead gateway left (its PID inside, nobody holding it)."""
    from pmcp import identity

    lock = tmp_path / "gateway.lock"
    lock.write_text("99999")
    monkeypatch.setattr(identity, "_LOCK_FD", None)
    try:
        assert identity.acquire_singleton_lock(tmp_path)
        assert lock.read_text() == str(os.getpid())
    finally:
        identity.release_singleton_lock()


def test_release_never_removes_the_lock_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    monkeypatch.setattr(identity, "_LOCK_FD", None)
    assert identity.acquire_singleton_lock(tmp_path)
    held = identity._LOCK_FD
    identity.release_singleton_lock()
    assert held is not None and held.closed
    assert (tmp_path / "gateway.lock").exists()
    assert identity.singleton_lock_held(tmp_path) == "free"


def test_the_doctor_probe_sees_a_held_lock(tmp_path: Path) -> None:
    from pmcp import identity

    assert identity.singleton_lock_held(tmp_path) == "absent"
    (gateway,) = _gateways(1)
    try:
        assert gateway.acquire_singleton_lock(tmp_path)
        assert identity.singleton_lock_held(tmp_path) == "held"
    finally:
        gateway.release_singleton_lock()
    assert identity.singleton_lock_held(tmp_path) == "free"


# --------------------------------------------------------------------------- #
# Board round 32, codex F001 (claude N-2): a checkout-supplied lock directory
# (`--lock-dir ./.mcp-gateway`) could plant gateway.lock as a link, and
# acquisition truncated the link's target and wrote a PID into it. The lock is
# now opened once, never following a link, and only a plain regular file (one
# name) is a lock file; nothing is written before the lock is held and checked.
# claude N-1: a starting gateway waits out doctor's momentary probe.
# --------------------------------------------------------------------------- #


def _fresh(monkeypatch: pytest.MonkeyPatch) -> Any:
    from pmcp import identity

    monkeypatch.setattr(identity, "_LOCK_FD", None)
    monkeypatch.setattr(identity, "_LOCK_FILE", None)
    return identity


def _tree(base: Path) -> dict[str, bytes | str | None]:
    out: dict[str, bytes | str | None] = {}
    for p in sorted(base.rglob("*")):
        if p.is_symlink():
            out[str(p)] = "-> " + os.readlink(p)
        elif p.is_file():
            out[str(p)] = p.read_bytes()
        else:
            out[str(p)] = None
    return out


def test_codex_r32_f001_lock_symlink_cannot_overwrite_unrelated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _fresh(monkeypatch)
    lock_dir = tmp_path / "project" / ".mcp-gateway"
    lock_dir.mkdir(parents=True)
    victim = tmp_path / "operator-config"
    original = b"unrelated operator configuration\n"
    victim.write_bytes(original)
    (lock_dir / "gateway.lock").symlink_to(victim)
    try:
        try:
            identity.acquire_singleton_lock(lock_dir)
        except OSError:
            pass  # Refusing an unsafe lock path is acceptable.
    finally:
        identity.release_singleton_lock()
    assert victim.read_bytes() == original, (
        "Lock acquisition overwrote its symlink target"
    )


def test_a_lock_link_to_a_missing_target_is_refused_and_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _fresh(monkeypatch)
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    # The target's directory exists: an open that followed the link with
    # O_CREAT would create the target there.
    (lock_dir / "gateway.lock").symlink_to(tmp_path / "made-by-a-follow")
    before = _tree(tmp_path)
    assert identity.acquire_singleton_lock(lock_dir) is False
    assert identity._LOCK_FD is None
    assert _tree(tmp_path) == before
    assert not (tmp_path / "made-by-a-follow").exists()


def _run_with_a_guard(target: Any, seconds: float = 10.0) -> Any:
    """Run ``target`` in a thread; fail (not hang) if it blocks."""
    import threading

    result: list[Any] = []

    def run() -> None:
        result.append(target())

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), "blocked on the lock path"
    return result[0]


@posix_only
@pytest.mark.parametrize("kind", ["fifo", "socket", "directory", "hard link"])
def test_a_lock_path_that_is_not_a_plain_file_is_refused_without_blocking(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hard link is refused too (st_nlink > 1): writing the PID would change
    a file reachable under another name the lock directory does not own."""
    import socket

    identity = _fresh(monkeypatch)
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    lock = lock_dir / "gateway.lock"
    held: list[Any] = []
    other = tmp_path / "other-name"
    if kind == "fifo":
        os.mkfifo(lock)
    elif kind == "socket":
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(lock))
        held.append(server)
    elif kind == "directory":
        lock.mkdir()
    else:
        other.write_bytes(b"another file's content\n")
        os.link(other, lock)
    try:
        before = _tree(tmp_path)
        assert (
            _run_with_a_guard(lambda: identity.acquire_singleton_lock(lock_dir))
            is False
        )
        assert identity._LOCK_FD is None
        assert _tree(tmp_path) == before
        assert _run_with_a_guard(lambda: identity.singleton_lock_held(lock_dir)) in (
            "unusable",
        )
    finally:
        for item in held:
            item.close()
        identity.release_singleton_lock()


def test_the_doctor_probe_never_follows_or_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = _fresh(monkeypatch)
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep me\n")
    (lock_dir / "gateway.lock").symlink_to(victim)
    before = _tree(tmp_path)
    assert identity.singleton_lock_held(lock_dir) == "unusable"
    assert _tree(tmp_path) == before
    (lock_dir / "gateway.lock").unlink()
    assert identity.singleton_lock_held(lock_dir) == "absent"
    assert not (lock_dir / "gateway.lock").exists()  # never created


@posix_only
def test_a_start_during_the_doctor_probe_waits_it_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """claude N-1: the probe holds the lock for an instant; a gateway starting
    then succeeds within its wait window instead of reporting "already
    running"."""
    import threading

    identity = _fresh(monkeypatch)
    lock = tmp_path / "gateway.lock"
    lock.write_text("")
    probe = open(lock, "r")
    identity._lock_fd_exclusive(probe)  # the probe's lock, held for a moment
    timer = threading.Timer(0.2, probe.close)
    timer.start()
    try:
        assert identity.acquire_singleton_lock(tmp_path) is True
        assert lock.read_text() == str(os.getpid())
    finally:
        timer.cancel()
        probe.close()
        identity.release_singleton_lock()


@posix_only
def test_a_real_second_gateway_still_fails_after_the_wait(tmp_path: Path) -> None:
    import time as time_module

    first, second = _gateways(2)
    second._CONTENDED_WAIT_SECONDS = 0.3
    try:
        assert first.acquire_singleton_lock(tmp_path)
        started = time_module.monotonic()
        assert second.acquire_singleton_lock(tmp_path) is False
        assert time_module.monotonic() - started >= 0.3
        assert (tmp_path / "gateway.lock").read_text() == str(os.getpid())
    finally:
        first.release_singleton_lock()
        second.release_singleton_lock()


def test_nothing_is_written_before_the_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gateway that loses the lock leaves the holder's PID alone."""
    (holder,) = _gateways(1)
    identity = _fresh(monkeypatch)
    monkeypatch.setattr(identity, "_CONTENDED_WAIT_SECONDS", 0.05)
    try:
        assert holder.acquire_singleton_lock(tmp_path)
        (tmp_path / "gateway.lock").write_text("4242")  # the holder's PID
        assert identity.acquire_singleton_lock(tmp_path) is False
        assert (tmp_path / "gateway.lock").read_text() == "4242"
    finally:
        holder.release_singleton_lock()


@posix_only
def test_a_link_swapped_in_between_open_and_lock_is_never_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock path becomes a link to the very file just opened: following
    it, the post-lock check would see the same inode. The entry itself is a
    link, so the lock is not held, and the retry refuses the link."""
    identity = _fresh(monkeypatch)
    lock = tmp_path / "gateway.lock"
    other = tmp_path / "elsewhere"
    real = identity._lock_fd_exclusive
    swapped: list[bool] = []

    def lock_after_a_swap(fd: Any) -> None:
        if not swapped:
            swapped.append(True)
            os.rename(lock, other)
            lock.symlink_to(other)
        real(fd)

    monkeypatch.setattr(identity, "_lock_fd_exclusive", lock_after_a_swap)
    try:
        assert identity.acquire_singleton_lock(tmp_path) is False
        assert swapped
        assert identity._LOCK_FD is None
        assert other.read_bytes() == b""  # nothing written through the link
    finally:
        identity.release_singleton_lock()


# --------------------------------------------------------------------------- #
# Board round 33 (codex F001-F003, grok F001, claude N-1): without O_NOFOLLOW
# an O_CREAT open created a dangling link's target; a lock directory replaced
# between open and lock gave two holders; a hard link added before the lock
# let the PID write reach a file named outside the lock directory. One rule:
# creation never follows a link on any platform, and after the lock is held
# every precondition is established again, from the directory down, before
# anything is written.
# --------------------------------------------------------------------------- #


def test_grok_r33_f001_dangling_lock_symlink_is_not_created_without_nofollow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    monkeypatch.setattr(identity, "_LOCK_FD", None)
    monkeypatch.setattr(identity, "_LOCK_FILE", None)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    monkeypatch.setattr(identity, "_OPEN_FLAGS", identity._OPEN_FLAGS & ~nofollow)
    monkeypatch.setattr(identity, "_PROBE_FLAGS", identity._PROBE_FLAGS & ~nofollow)
    monkeypatch.setattr(identity, "_lock_dir_fd", lambda _lock_dir: None)
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    target = tmp_path / "made-by-a-follow"
    (lock_dir / "gateway.lock").symlink_to(target)
    state = identity.singleton_lock_held(lock_dir)
    acquired = identity.acquire_singleton_lock(lock_dir)
    try:
        assert not target.exists()
        assert acquired is False
        assert state == "unusable"
        assert identity._LOCK_FD is None
    finally:
        identity.release_singleton_lock()


def test_codex_r33_f001_replaced_lock_directory_preserves_singleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _gateways(2)
    first._CONTENDED_WAIT_SECONDS = 0.01
    second._CONTENDED_WAIT_SECONDS = 0.01
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    real_lock = first._lock_fd_exclusive
    replaced = False

    def replace_directory_then_lock(fd: Any) -> None:
        nonlocal replaced
        if not replaced:
            replaced = True
            lock_dir.rename(tmp_path / "old-locks")
            lock_dir.mkdir()
        real_lock(fd)

    monkeypatch.setattr(first, "_lock_fd_exclusive", replace_directory_then_lock)
    try:
        first_acquired = first.acquire_singleton_lock(lock_dir)
        second_acquired = second.acquire_singleton_lock(lock_dir)
        assert not (first_acquired and second_acquired), (
            "Two gateways hold different lock inodes for the same lock directory"
        )
        # The retry started again from the new directory and holds its lock.
        assert first_acquired is True
        assert _holds_the_file_at(first._LOCK_FD, lock_dir / "gateway.lock")
    finally:
        first.release_singleton_lock()
        second.release_singleton_lock()


def test_codex_r33_f002_no_nofollow_fallback_does_not_create_symlink_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    monkeypatch.setattr(identity, "_LOCK_FD", None)
    monkeypatch.setattr(
        identity, "_OPEN_FLAGS", identity._OPEN_FLAGS & ~getattr(os, "O_NOFOLLOW", 0)
    )
    monkeypatch.setattr(identity, "_lock_dir_fd", lambda directory: None)
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    target = tmp_path / "unrelated-new-file"
    (lock_dir / "gateway.lock").symlink_to(target)
    try:
        identity.acquire_singleton_lock(lock_dir)
    except OSError:
        pass
    finally:
        identity.release_singleton_lock()
    assert not target.exists(), "Refusing the lock created its symlink target"


def test_codex_r33_f003_hardlink_added_before_lock_is_not_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    monkeypatch.setattr(identity, "_LOCK_FD", None)
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    lock = lock_dir / "gateway.lock"
    original = b"preserve this content\n"
    lock.write_bytes(original)
    other_name = tmp_path / "other-name"
    real_lock = identity._lock_fd_exclusive

    def add_name_then_lock(fd: Any) -> None:
        if not other_name.exists():
            os.link(lock, other_name)
        real_lock(fd)

    monkeypatch.setattr(identity, "_lock_fd_exclusive", add_name_then_lock)
    try:
        identity.acquire_singleton_lock(lock_dir)
    finally:
        identity.release_singleton_lock()
    assert other_name.read_bytes() == original, (
        "PID write changed a file with a name outside the lock directory"
    )


@pytest.mark.parametrize("dir_fd", [True, False], ids=["dir_fd", "no-dir_fd"])
@pytest.mark.parametrize("nofollow", [True, False], ids=["O_NOFOLLOW", "no-O_NOFOLLOW"])
@pytest.mark.parametrize("shape", ["dangling", "to-a-file"])
def test_a_link_at_the_lock_path_is_refused_on_every_fallback(
    shape: str,
    nofollow: bool,
    dir_fd: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With or without O_NOFOLLOW and dir_fd: refused, nothing created,
    nothing written; the probe calls it unusable."""
    from pmcp import identity

    monkeypatch.setattr(identity, "_LOCK_FD", None)
    if not nofollow:
        flag = getattr(os, "O_NOFOLLOW", 0)
        monkeypatch.setattr(identity, "_OPEN_FLAGS", identity._OPEN_FLAGS & ~flag)
        monkeypatch.setattr(identity, "_PROBE_FLAGS", identity._PROBE_FLAGS & ~flag)
    if not dir_fd:
        monkeypatch.setattr(identity, "_lock_dir_fd", lambda _d: None)
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    target = tmp_path / "target"
    if shape == "to-a-file":
        target.write_bytes(b"keep\n")
    (lock_dir / "gateway.lock").symlink_to(target)
    before = _tree(tmp_path)
    assert identity.singleton_lock_held(lock_dir) == "unusable"
    assert identity.acquire_singleton_lock(lock_dir) is False
    assert identity._LOCK_FD is None
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("dir_fd", [True, False], ids=["dir_fd", "no-dir_fd"])
@pytest.mark.parametrize("nofollow", [True, False], ids=["O_NOFOLLOW", "no-O_NOFOLLOW"])
def test_an_absent_lock_file_is_created_on_every_fallback(
    nofollow: bool, dir_fd: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    monkeypatch.setattr(identity, "_LOCK_FD", None)
    if not nofollow:
        flag = getattr(os, "O_NOFOLLOW", 0)
        monkeypatch.setattr(identity, "_OPEN_FLAGS", identity._OPEN_FLAGS & ~flag)
    if not dir_fd:
        monkeypatch.setattr(identity, "_lock_dir_fd", lambda _d: None)
    try:
        assert identity.acquire_singleton_lock(tmp_path) is True
        lock = tmp_path / "gateway.lock"
        assert lock.read_text() == str(os.getpid())
        assert os.lstat(lock).st_nlink == 1
        assert [p.name for p in tmp_path.iterdir()] == ["gateway.lock"]  # no temp left
    finally:
        identity.release_singleton_lock()


@posix_only
def test_an_exclusive_creation_race_leaves_one_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finds no lock file; before A's exclusive create, B creates and locks
    it. A's create fails (EEXIST), A starts again, and finds it held."""
    import pmcp.atomic_write as writer

    a, b = _gateways(2)
    real_open = os.open
    fired: list[bool] = []

    def open_racing(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if (
            not fired
            and flags & os.O_CREAT
            and flags & os.O_EXCL
            and os.fspath(path).endswith("gateway.lock")
        ):
            fired.append(True)
            assert b.acquire_singleton_lock(tmp_path)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(writer.os, "open", open_racing)
    try:
        assert a.acquire_singleton_lock(tmp_path) is False
        assert fired
        assert b._LOCK_FD is not None and a._LOCK_FD is None
    finally:
        monkeypatch.undo()
        a.release_singleton_lock()
        b.release_singleton_lock()


@pytest.mark.parametrize("store", ["trust", "package approvals"])
@pytest.mark.parametrize("nofollow", [True, False], ids=["O_NOFOLLOW", "no-O_NOFOLLOW"])
def test_a_store_sidecar_lock_never_creates_through_a_link(
    store: str, nofollow: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audited O_CREAT sites: the stores' ``.lock`` sidecars."""
    from pmcp import package_approvals, trust_store
    from pmcp.trust_store import TrustStoreError

    if not nofollow:
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    path = tmp_path / ("trust.json" if store == "trust" else "package_approvals.json")
    target = tmp_path / "made-by-a-follow"
    (tmp_path / (path.name + ".lock")).symlink_to(target)
    opener = (
        trust_store._open_sidecar_lock
        if store == "trust"
        else package_approvals._open_sidecar_lock
    )
    with pytest.raises(TrustStoreError):
        opener(path)
    assert not target.exists()
    # And through the stores' own lock entry point, which every read-modify-
    # write of the store takes.
    store_lock = (
        trust_store._store_lock if store == "trust" else package_approvals._store_lock
    )
    with pytest.raises(TrustStoreError), store_lock(path):
        pass
    assert not target.exists()
    (tmp_path / (path.name + ".lock")).unlink()
    fd = opener(path)
    os.close(fd)
    assert (tmp_path / (path.name + ".lock")).is_file()
    with store_lock(path):
        pass


# --------------------------------------------------------------------------- #
# Board round 34 (grok, codex, claude): the no-O_NOFOLLOW fallback created the
# lock by hard-linking a temporary file and unlinking it while still open --
# Windows refuses that delete (no FILE_SHARE_DELETE on os.open), leaving two
# names so the lock was refused forever; and FAT has no hard links. Windows now
# uses CreateFileW with FILE_FLAG_OPEN_REPARSE_POINT (OPEN_EXISTING, else
# CREATE_NEW); POSIX creates with O_CREAT|O_EXCL, which POSIX requires to fail
# on any existing name; anything else refuses to create.
# --------------------------------------------------------------------------- #


def test_grok_r34_f001_absent_lock_is_singly_linked_when_unlink_of_an_open_file_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    from pmcp.atomic_write import open_plain_file

    parent = tmp_path / "plain-lock"
    parent.mkdir()
    real_unlink = os.unlink

    def unlink(path: Any, *args: Any, **kwargs: Any) -> None:
        st = os.lstat(path)
        for entry in os.listdir("/proc/self/fd"):
            try:
                held = os.fstat(int(entry))
            except OSError:
                continue
            if (held.st_dev, held.st_ino) == (st.st_dev, st.st_ino):
                raise PermissionError(
                    errno.EACCES,
                    "The process cannot access the file because it is being used",
                )
        return real_unlink(path, *args, **kwargs)

    if not Path("/proc/self/fd").is_dir():
        pytest.skip("needs /proc/self/fd")
    monkeypatch.setattr(os, "unlink", unlink)
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = open_plain_file(
        "gateway.lock", parent=str(parent), dir_fd=None, flags=flags, mode=0o600
    )
    try:
        assert os.fstat(fd).st_nlink == 1
        assert sorted(p.name for p in parent.iterdir()) == ["gateway.lock"]
        assert os.lstat(parent / "gateway.lock").st_nlink == 1
    finally:
        os.close(fd)


def test_codex_r34_f001_windows_open_temporary_does_not_poison_singleton(
    tmp_path: Path,
) -> None:
    import errno
    from types import SimpleNamespace
    from unittest.mock import patch

    from pmcp import atomic_write, identity

    active: set[int] = set()
    windows_os = SimpleNamespace(**vars(os))

    def tracked_open(*args: Any, **kwargs: Any) -> int:
        fd = os.open(*args, **kwargs)
        active.add(fd)
        return fd

    def tracked_close(fd: int) -> None:
        active.discard(fd)
        os.close(fd)

    def windows_unlink(path: Any, **kwargs: Any) -> None:
        named = os.stat(path, follow_symlinks=False, **kwargs)
        for fd in active:
            held = os.fstat(fd)
            if (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino):
                raise PermissionError(errno.EACCES, "Open handle denies deletion")
        os.unlink(path, **kwargs)

    windows_os.open = tracked_open
    windows_os.close = tracked_close
    windows_os.unlink = windows_unlink
    flags = identity._OPEN_FLAGS & ~getattr(os, "O_NOFOLLOW", 0)
    with (
        patch.object(atomic_write, "os", windows_os),
        patch.object(identity, "_OPEN_FLAGS", flags),
        patch.object(identity, "_lock_dir_fd", lambda _: None),
        patch.object(identity, "_LOCK_FD", None),
        patch.object(identity, "_LOCK_FILE", None),
    ):
        try:
            assert identity.acquire_singleton_lock(tmp_path) is True
            assert sorted(p.name for p in tmp_path.iterdir()) == ["gateway.lock"]
            assert (tmp_path / "gateway.lock").stat().st_nlink == 1
        finally:
            identity.release_singleton_lock()


def test_codex_r34_f002_plain_lock_works_without_filesystem_hard_links(
    tmp_path: Path,
) -> None:
    import errno
    from types import SimpleNamespace
    from unittest.mock import patch

    from pmcp import atomic_write, identity

    filesystem_os = SimpleNamespace(**vars(os))

    def unsupported_link(*args: Any, **kwargs: Any) -> None:
        raise OSError(errno.EOPNOTSUPP, "Filesystem does not support hard links")

    filesystem_os.link = unsupported_link
    flags = identity._OPEN_FLAGS & ~getattr(os, "O_NOFOLLOW", 0)
    with (
        patch.object(atomic_write, "os", filesystem_os),
        patch.object(identity, "_OPEN_FLAGS", flags),
        patch.object(identity, "_lock_dir_fd", lambda _: None),
        patch.object(identity, "_LOCK_FD", None),
        patch.object(identity, "_LOCK_FILE", None),
    ):
        try:
            assert identity.acquire_singleton_lock(tmp_path) is True
            assert (tmp_path / "gateway.lock").read_text() == str(os.getpid())
        finally:
            identity.release_singleton_lock()


class _FakeKernel32:
    """Windows, modelled over a real directory tree, for the ctypes calls
    ``atomic_write._open_windows`` makes. What it models:

    * a symlink at a name is opened AS ITSELF only with
      FILE_FLAG_OPEN_REPARSE_POINT; without it the name is followed (and
      CREATE_NEW / OPEN_ALWAYS through a dangling link create the target --
      the reading the redesign must never depend on);
    * CREATE_NEW fails (ERROR_FILE_EXISTS) on any existing name, a dangling
      link included, when the name itself is the object;
    * a pipe reports FILE_TYPE_PIPE; a directory FILE_ATTRIBUTE_DIRECTORY;
    * every call is recorded with its flags, share mode and disposition.
    """

    def __init__(self) -> None:
        import ctypes

        self.calls: list[tuple[int, int, int, int]] = []
        self.error = 0
        self.kinds: dict[int, tuple[int, int]] = {}  # handle -> (attributes, type)
        self.invalid = ctypes.c_void_p(-1).value
        self.before_create_new: Any = None

    def CreateFileW(  # noqa: N802 - the Win32 name
        self,
        path: str,
        access: int,
        share: int,
        security: Any,
        disposition: int,
        flags: int,
        template: Any,
    ) -> int | None:
        import stat as stat_module

        from pmcp import atomic_write as w

        self.calls.append((disposition, flags, share, access))
        if disposition == w.CREATE_NEW and self.before_create_new is not None:
            self.before_create_new()
            self.before_create_new = None
        target = path
        as_itself = bool(flags & w.FILE_FLAG_OPEN_REPARSE_POINT)
        if not as_itself and os.path.islink(target):
            target = os.path.realpath(target)
        exists = os.path.lexists(target)
        if disposition == w.OPEN_EXISTING and not exists:
            self.error = w.ERROR_FILE_NOT_FOUND
            return self.invalid
        if disposition == w.CREATE_NEW and exists:
            self.error = w.ERROR_FILE_EXISTS
            return self.invalid
        if not exists:  # CREATE_NEW, or OPEN_ALWAYS on an absent name
            try:
                fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            except FileNotFoundError:
                self.error = 3  # ERROR_PATH_NOT_FOUND
                return self.invalid
            self.kinds[fd] = (w.FILE_ATTRIBUTE_NORMAL, w.FILE_TYPE_DISK)
            return fd
        seen = os.lstat(target)
        if stat_module.S_ISLNK(seen.st_mode):
            fd = os.open(target, os.O_PATH | os.O_NOFOLLOW)
            self.kinds[fd] = (w.FILE_ATTRIBUTE_REPARSE_POINT, w.FILE_TYPE_DISK)
        elif stat_module.S_ISDIR(seen.st_mode):
            fd = os.open(target, os.O_RDONLY)
            self.kinds[fd] = (w.FILE_ATTRIBUTE_DIRECTORY, w.FILE_TYPE_DISK)
        elif stat_module.S_ISFIFO(seen.st_mode):
            fd = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
            self.kinds[fd] = (w.FILE_ATTRIBUTE_NORMAL, 3)  # FILE_TYPE_PIPE
        else:
            mode = os.O_RDWR if access & w.GENERIC_WRITE else os.O_RDONLY
            fd = os.open(target, mode)
            self.kinds[fd] = (w.FILE_ATTRIBUTE_NORMAL, w.FILE_TYPE_DISK)
        return fd

    def GetFileInformationByHandle(self, handle: int, info: Any) -> int:  # noqa: N802
        record = info._obj
        record.dwFileAttributes = self.kinds[handle][0]
        record.nNumberOfLinks = os.fstat(handle).st_nlink
        return 1

    def GetFileType(self, handle: int) -> int:  # noqa: N802
        return self.kinds[handle][1]

    def CloseHandle(self, handle: int) -> int:  # noqa: N802
        self.kinds.pop(handle, None)
        os.close(handle)
        return 1


def _windows_layer(
    monkeypatch: pytest.MonkeyPatch, *, hard_links: bool = False
) -> _FakeKernel32:
    """Install the fake Windows layer, and Windows' delete rule (no unlink of
    an entry while a handle to it is open) and -- for FAT -- no hard links."""
    import errno
    from types import SimpleNamespace

    from pmcp import atomic_write, identity

    kernel32 = _FakeKernel32()
    layer = SimpleNamespace(
        kernel32=kernel32,
        get_last_error=lambda: kernel32.error,
        open_osfhandle=lambda handle, _flags: handle,
        invalid_handle=kernel32.invalid,
    )
    monkeypatch.setattr(atomic_write, "_WINDOWS_FILES", layer)
    monkeypatch.setattr(identity, "_lock_dir_fd", lambda _d: None)  # no dir_fd
    monkeypatch.setattr(identity, "_LOCK_FD", None)
    monkeypatch.setattr(identity, "_LOCK_FILE", None)
    windows_os = SimpleNamespace(**vars(os))

    def windows_unlink(path: Any, **kwargs: Any) -> None:
        named = os.stat(path, follow_symlinks=False)
        for handle in kernel32.kinds:
            held = os.fstat(handle)
            if (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino):
                raise PermissionError(errno.EACCES, "sharing violation")
        os.unlink(path)

    windows_os.unlink = windows_unlink
    if not hard_links:

        def no_link(*_a: Any, **_k: Any) -> None:
            raise OSError(errno.EOPNOTSUPP, "no hard links (FAT)")

        windows_os.link = no_link
    monkeypatch.setattr(atomic_write, "os", windows_os)
    return kernel32


def test_windows_creates_the_lock_with_the_no_follow_call_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact Win32 calls: OPEN_EXISTING then CREATE_NEW, each with
    FILE_FLAG_OPEN_REPARSE_POINT and sharing read, write AND delete; a second
    start opens the existing file only. On FAT (no hard links), under the
    delete rule."""
    from pmcp import atomic_write as w
    from pmcp import identity

    kernel32 = _windows_layer(monkeypatch)
    flags = w.FILE_ATTRIBUTE_NORMAL | w.FILE_FLAG_OPEN_REPARSE_POINT
    share = w.FILE_SHARE_READ | w.FILE_SHARE_WRITE | w.FILE_SHARE_DELETE
    access = w.GENERIC_READ | w.GENERIC_WRITE
    try:
        assert identity.acquire_singleton_lock(tmp_path) is True
        assert kernel32.calls == [
            (w.OPEN_EXISTING, flags, share, access),
            (w.CREATE_NEW, flags, share, access),
        ]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["gateway.lock"]
        assert (tmp_path / "gateway.lock").read_text() == str(os.getpid())
    finally:
        identity.release_singleton_lock()
    kernel32.calls.clear()
    try:
        assert identity.acquire_singleton_lock(tmp_path) is True
        assert kernel32.calls == [(w.OPEN_EXISTING, flags, share, access)]
    finally:
        identity.release_singleton_lock()


@pytest.mark.parametrize("shape", ["dangling", "to-a-file", "to-a-directory"])
def test_windows_never_creates_or_opens_through_a_reparse_point(
    shape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    _windows_layer(monkeypatch)
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    target = tmp_path / "target"
    if shape == "to-a-file":
        target.write_bytes(b"keep\n")
    elif shape == "to-a-directory":
        target.mkdir()
    (lock_dir / "gateway.lock").symlink_to(target)
    before = _tree(tmp_path)
    assert identity.acquire_singleton_lock(lock_dir) is False
    assert identity.singleton_lock_held(lock_dir) == "unusable"
    assert _tree(tmp_path) == before


def test_windows_create_new_refuses_a_reparse_point_that_appears_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPEN_EXISTING finds nothing; before CREATE_NEW a dangling link appears
    at the name. CREATE_NEW fails on it (ERROR_FILE_EXISTS), the attempt
    starts again, opens the link as itself and refuses it: nothing created."""
    from pmcp import atomic_write as w
    from pmcp import identity

    kernel32 = _windows_layer(monkeypatch)
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    target = tmp_path / "made-by-a-follow"
    kernel32.before_create_new = lambda: (lock_dir / "gateway.lock").symlink_to(target)
    assert identity.acquire_singleton_lock(lock_dir) is False
    assert not target.exists()
    assert [c[0] for c in kernel32.calls[:3]] == [
        w.OPEN_EXISTING,
        w.CREATE_NEW,
        w.OPEN_EXISTING,
    ]


@pytest.mark.parametrize("shape", ["two links", "fifo", "directory"])
def test_windows_refuses_what_is_not_a_plain_file(
    shape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    lock = lock_dir / "gateway.lock"
    if shape == "two links":
        lock.write_bytes(b"shared\n")
        os.link(lock, tmp_path / "other-name")
    elif shape == "fifo":
        os.mkfifo(lock)
    else:
        lock.mkdir()
    _windows_layer(monkeypatch)
    before = _tree(tmp_path)
    assert _run_with_a_guard(lambda: identity.acquire_singleton_lock(lock_dir)) is False
    assert _tree(tmp_path) == before


def test_windows_holding_the_lock_lets_it_be_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FILE_SHARE_DELETE: another process (an older pmcp, the operator) can
    still remove the file while a gateway holds it."""
    from pmcp import atomic_write as w
    from pmcp import identity

    kernel32 = _windows_layer(monkeypatch)
    try:
        assert identity.acquire_singleton_lock(tmp_path) is True
        assert all(call[2] & w.FILE_SHARE_DELETE for call in kernel32.calls)
    finally:
        identity.release_singleton_lock()


def test_a_platform_with_neither_no_follow_primitive_never_creates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not POSIX, not Windows: no lock file is created at all (refused); an
    existing plain one still opens."""
    from types import SimpleNamespace

    from pmcp import atomic_write, identity

    other_os = SimpleNamespace(**vars(os))
    other_os.name = "java"
    del other_os.O_NOFOLLOW
    monkeypatch.setattr(atomic_write, "os", other_os)
    monkeypatch.setattr(identity, "_LOCK_FD", None)
    monkeypatch.setattr(identity, "_lock_dir_fd", lambda _d: None)
    assert identity.acquire_singleton_lock(tmp_path) is False
    assert list(tmp_path.iterdir()) == []
    (tmp_path / "gateway.lock").write_text("")
    try:
        assert identity.acquire_singleton_lock(tmp_path) is True
    finally:
        identity.release_singleton_lock()


# claude N-1: the DEFAULT lock directory ~/.pmcp may be the operator's own
# link (dotfiles); it is followed only where the HOME gate's rules allow. An
# explicit --lock-dir is never followed (a repository can supply it).


def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from pmcp import home_identity, identity

    base = tmp_path.resolve()
    home = base / "home"
    home.mkdir()
    original_marker = home_identity.has_checkout_marker
    ambient = set(base.parents)
    monkeypatch.setattr(
        home_identity,
        "has_checkout_marker",
        lambda d: False if Path(d) in ambient else original_marker(d),
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(identity, "_LOCK_FD", None)
    monkeypatch.setattr(identity, "_LOCK_FILE", None)
    home_identity.forget_home_verdicts()
    return home


def test_an_operator_linked_dot_pmcp_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    home = _home(tmp_path, monkeypatch)
    state = home.parent / "state" / "pmcp"
    state.mkdir(parents=True)
    (home / ".pmcp").symlink_to(state, target_is_directory=True)
    try:
        assert identity.acquire_singleton_lock() is True
        assert (state / "gateway.lock").read_text() == str(os.getpid())
        assert identity.singleton_lock_held(home / ".pmcp", home_scoped=True) == "held"
    finally:
        identity.release_singleton_lock()
    assert identity.singleton_lock_held(home / ".pmcp", home_scoped=True) == "free"


@pytest.mark.parametrize("into", ["a checkout", "a link a checkout holds"])
def test_a_dot_pmcp_that_leads_into_a_checkout_is_refused(
    into: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    home = _home(tmp_path, monkeypatch)
    repo = home.parent / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "state").mkdir()
    if into == "a checkout":
        (home / ".pmcp").symlink_to(repo / "state", target_is_directory=True)
    else:
        outside = home.parent / "outside"
        outside.mkdir()
        (repo / "out").symlink_to(outside, target_is_directory=True)
        (home / ".pmcp").symlink_to(repo / "out", target_is_directory=True)
    before = _tree(home.parent)
    assert identity.acquire_singleton_lock() is False
    assert _tree(home.parent) == before
    assert identity.singleton_lock_held(home / ".pmcp", home_scoped=True) == "unusable"


def test_an_explicit_lock_dir_that_is_a_link_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    home = _home(tmp_path, monkeypatch)
    state = home.parent / "state"
    state.mkdir()
    (home.parent / "linked").symlink_to(state, target_is_directory=True)
    assert identity.acquire_singleton_lock(home.parent / "linked") is False
    assert list(state.iterdir()) == []


@pytest.mark.parametrize("shape", ["two links", "fifo"])
def test_windows_refuses_a_handle_before_it_becomes_a_descriptor(
    shape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows open judges the HANDLE (link count, file type) and closes
    it; nothing that is not a plain disk file is handed to the C runtime."""
    from pmcp import atomic_write as w

    kernel32 = _windows_layer(monkeypatch)
    converted: list[int] = []
    layer = w._WINDOWS_FILES
    real_convert = layer.open_osfhandle
    layer.open_osfhandle = lambda handle, flags: converted.append(
        handle
    ) or real_convert(handle, flags)
    lock = tmp_path / "gateway.lock"
    if shape == "two links":
        lock.write_bytes(b"shared\n")
        os.link(lock, tmp_path / "other-name")
    else:
        os.mkfifo(lock)
    with pytest.raises(w.PlainFileRefused):
        w._open_windows(layer, str(lock), "gateway.lock", write=True, create=True)
    assert converted == []
    assert kernel32.kinds == {}  # the handle was closed
