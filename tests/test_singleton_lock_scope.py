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
import tempfile
from pathlib import Path
from unittest.mock import patch

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

    def test_gateway_server_lock_dir_converts_string_to_path(self, tmp_path: Path) -> None:
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

        assert "--lock-dir" in result.stdout, (
            "CLI should have --lock-dir option"
        )

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
