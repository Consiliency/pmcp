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
# Board round 30, codex F001: shutdown unlinked the remembered lock path with
# no check -- after HOME was refused and `.pmcp` redirected, it deleted another
# directory's gateway.lock. Shutdown now always closes what it holds and
# removes only the file this process created, and only while the HOME the
# default lock was taken under is still the operator's.
# --------------------------------------------------------------------------- #


def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from pmcp import home_identity, identity

    base = tmp_path.resolve()
    home = base / "parent" / "home"
    home.mkdir(parents=True)
    original_marker = home_identity.has_checkout_marker
    ambient = set(base.parents)
    monkeypatch.setattr(
        home_identity,
        "has_checkout_marker",
        lambda d: False if Path(d) in ambient else original_marker(d),
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(identity, "_LOCK_FILE", None)
    monkeypatch.setattr(identity, "_LOCK_FD", None)
    monkeypatch.setattr(identity, "_LOCK_IDENTITY", None)
    monkeypatch.setattr(identity, "_LOCK_DIR_FD", None)
    monkeypatch.setattr(identity, "_LOCK_PIN", None)
    home_identity.forget_home_verdicts()
    return home


def test_codex_r30_f001_lock_release_rechecks_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import home_identity, identity

    home = _isolated_home(tmp_path, monkeypatch)
    base = home.parent.parent
    try:
        assert home_identity.home_is_operators()
        assert identity.acquire_singleton_lock()
        (home.parent / ".git").mkdir()
        assert not home_identity.home_is_operators()
        victim_dir = base / "unrelated"
        victim_dir.mkdir()
        victim = victim_dir / "gateway.lock"
        victim.write_bytes(b"unrelated lock")
        lock_dir = home / ".pmcp"
        lock_dir.rename(home / ".held-lock")
        lock_dir.symlink_to(victim_dir, target_is_directory=True)
        assert not home_identity.home_is_operators()
        identity.release_singleton_lock()
        assert identity._LOCK_FD is None
        assert victim.is_file(), (
            "Shutdown followed a refused HOME path and deleted another lock"
        )
        assert victim.read_bytes() == b"unrelated lock"
    finally:
        if identity._LOCK_FD is not None:
            identity._LOCK_FD.close()
        home_identity.forget_home_verdicts()
        home_identity.reset_home_warning()


def test_shutdown_removes_the_lock_it_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    home = _isolated_home(tmp_path, monkeypatch)
    assert identity.acquire_singleton_lock()
    held = identity._LOCK_FD
    lock = home / ".pmcp" / "gateway.lock"
    assert lock.is_file()
    identity.release_singleton_lock()
    assert held is not None and held.closed
    assert not lock.exists()
    assert identity._LOCK_DIR_FD is None


@pytest.mark.parametrize("dir_fd", [True, False], ids=["dir_fd", "no-dir_fd"])
def test_a_lock_replaced_before_shutdown_is_left_alone(
    dir_fd: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same path, new inode: not this process's file, so not removed."""
    from pmcp import identity

    home = _isolated_home(tmp_path, monkeypatch)
    if not dir_fd:
        monkeypatch.setattr(identity, "_hold_lock_directory", lambda *a: None)
    assert identity.acquire_singleton_lock()
    lock = home / ".pmcp" / "gateway.lock"
    replacement = home / ".pmcp" / "gateway.lock.new"
    replacement.write_bytes(b"another process's lock")
    os.replace(replacement, lock)
    held = identity._LOCK_FD
    identity.release_singleton_lock()
    assert held is not None and held.closed
    assert lock.read_bytes() == b"another process's lock"


def test_a_refused_home_removes_nothing_but_closes_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import identity

    home = _isolated_home(tmp_path, monkeypatch)
    assert identity.acquire_singleton_lock()
    held, directory = identity._LOCK_FD, identity._LOCK_DIR_FD
    lock = home / ".pmcp" / "gateway.lock"
    (home.parent / ".git").mkdir()
    identity.release_singleton_lock()
    assert held is not None and held.closed
    assert lock.is_file()  # left behind: no removal under a refused HOME
    if directory is not None:
        with pytest.raises(OSError):
            os.fstat(directory)  # the held directory was closed too
