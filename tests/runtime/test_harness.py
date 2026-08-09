"""SL-4.1 — pin `gateway_on_spare_port` / `booted_gateway`'s isolation contract.

`harness.py` and `conftest.py` are never collected by pytest (no `test_*`
name), so a command targeting only them verifies nothing — this is the
collectable module SL-4.1 requires. Every other tests/runtime/ module
depends on these guarantees holding.

SL-5.1 adds three more sections below: the incremental `listen_stream`
reader against a real booted gateway, `RT_FIXTURE_SRC` gaining a resource,
and `booted_gateway(request_timeout=...)`.
"""

from __future__ import annotations

from tests.runtime.harness import (
    INITIALIZED_RE,
    LIVE_GATEWAY_PORT,
    booted_gateway,
    listen_stream,
    modern_post,
)


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


# --- SL-5.1: listen_stream() — an incremental SSE reader --------------------


async def test_listen_stream_yields_the_ack_frame() -> None:
    """Against a real booted gateway's `/mcp`, opening a subscription
    returns a `ListenStream` whose first `next_frame()` is the
    IF-0-P3B-4 ack, carrying the request's id as its subscriptionId."""
    from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY

    with booted_gateway() as gw:
        async with listen_stream(
            gw.base_url, notifications={"toolsListChanged": True}, request_id=91
        ) as stream:
            frame = await stream.next_frame()
            assert frame["method"] == "notifications/subscriptions/acknowledged"
            assert frame["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 91, frame
            await stream.close()


# --- SL-5.1: RT_FIXTURE_SRC gains a resource ---------------------------------


def test_rt_fixture_exposes_a_resource() -> None:
    """`RT_FIXTURE_SRC` must expose a resource (not just a tool and a
    prompt) so EC-P3B-4 has real resource content to prove
    `resources/list_changed` delivery against."""
    with booted_gateway() as gw:
        result = modern_post(gw.base_url, "resources/list")
        resources = result["result"]["resources"]
        assert resources, "RT_FIXTURE_SRC must expose at least one resource"


# --- SL-5.1: booted_gateway(request_timeout=...) -----------------------------


def test_booted_gateway_request_timeout_kwarg_reaches_the_command() -> None:
    """`request_timeout` appends `--request-timeout <n>` to the boot
    command (`src/pmcp/cli.py:232`) -- the flag the V4b regression needs to
    force a short timeout and then prove a subscription outlives it."""
    with booted_gateway(request_timeout=5) as gw:
        assert "--request-timeout" in gw.command
        idx = gw.command.index("--request-timeout")
        assert gw.command[idx + 1] == "5"


def test_booted_gateway_default_omits_request_timeout_flag() -> None:
    """No `request_timeout` given -> no flag added; `pmcp` falls back to
    its own default (60s) rather than the harness silently picking one."""
    with booted_gateway() as gw:
        assert "--request-timeout" not in gw.command


def test_booted_gateway_tolerates_no_live_gateway() -> None:
    """`booted_gateway()`'s `live_pid is None` tolerance must hold on a
    runner with no `:3344` unit -- a hard live-gateway requirement here
    would fail this whole module on CI (Execution Notes > "Everything in
    tests/runtime/ must pass on a CI runner with no live gateway")."""
    from tests.runtime.harness import _live_gateway_pid

    live_pid = _live_gateway_pid()
    with booted_gateway() as gw:
        # Either there genuinely is no live gateway (CI), or there is one
        # and the boot still succeeded without touching it -- both are
        # this fixture working as designed.
        assert gw.port != LIVE_GATEWAY_PORT
    if live_pid is None:
        assert _live_gateway_pid() is None
