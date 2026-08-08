"""SL-4.1 — pin `gateway_on_spare_port` / `booted_gateway`'s isolation contract.

`harness.py` and `conftest.py` are never collected by pytest (no `test_*`
name), so a command targeting only them verifies nothing — this is the
collectable module SL-4.1 requires. Every other tests/runtime/ module
depends on these guarantees holding.
"""

from __future__ import annotations

from tests.runtime.harness import INITIALIZED_RE, LIVE_GATEWAY_PORT, booted_gateway


def test_boot_never_binds_the_live_gateway_port() -> None:
    with booted_gateway() as gw:
        assert gw.port != LIVE_GATEWAY_PORT
        assert gw.port > 0


def test_boot_command_carries_all_six_isolation_controls() -> None:
    with booted_gateway() as gw:
        command = gw.command
        assert "--config" in command
        assert "--project" in command
        assert "--policy" in command
        assert "--lock-dir" in command
        assert gw.env.get("HOME") == str(gw.work_dir / "home")
        assert gw.env.get("XDG_CONFIG_HOME") == str(gw.work_dir / "home" / ".config")
        # The sixth control: a cwd inside the throwaway dir, independent of
        # --project and HOME (`_find_project_manifest()` walks from
        # `Path.cwd()`).
        assert gw.cwd == gw.work_dir
        assert str(gw.cwd).startswith("/tmp") or "pmcp-rt-" in str(gw.cwd)


def test_boot_log_reports_initialized_count() -> None:
    with booted_gateway() as gw:
        log_text = gw.boot_log.read_text()
        assert INITIALIZED_RE.search(log_text), log_text
        assert "Fatal error" not in log_text


def test_live_gateway_pid_and_children_are_unchanged_across_a_full_cycle() -> None:
    """`booted_gateway()` itself snapshots the live gateway's pid and child
    set before entry and re-asserts both are unchanged on exit (Execution
    Notes > Runtime-step safety); a completed setup/teardown cycle here is
    the proof that assertion held for a real boot+shutdown, not just an
    import."""
    with booted_gateway():
        pass
