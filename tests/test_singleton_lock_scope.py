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
        assert holder._holds_the_file_at(holder._LOCK_FD, lock)
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
