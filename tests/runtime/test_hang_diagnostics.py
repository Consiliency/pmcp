"""Stage-1 diagnostics for Consiliency/pmcp#200 — proof that a hang now fails
fast and names itself.

Five `test (3.x)` jobs in the week to 2026-09-01 stalled inside
`tests/runtime/test_emitter_harness.py` and were killed by the job's
`timeout-minutes: 25`. GitHub reports a timed-out job as *cancelled*, not
failed, so the flake has never produced a red X, a traceback, or one line of
evidence about which await is stuck. This module does not fix that hang — the
root cause is still unknown. It proves the instrumentation that will capture
it.

"The suite no longer hangs" passes on unchanged `main`, so it is not a
criterion. Each test below induces a hang deterministically and asserts the
diagnostic fires, mutant-style (`.consiliency/evidence/mutation-217.md`):

  * two mutant servers for `run_fake_remote`'s bounded stop sequence — one
    whose `serve()` never returns, and one that **swallows `CancelledError`**.
    The second is the one that matters: `asyncio.wait_for` does not bound a
    cancellation-resistant task (it cancels, then waits for the cancellation to
    finish), so a `wait_for`-based stop passes the first mutant and hangs on
    this one exactly as hard as the bare `await task` it replaced.
  * a subprocess pytest run whose **fixture teardown** blocks, in both
    directions — with the plugin, and with `-p no:timeout`. Teardown coverage is
    the whole point: pytest prints `PASSED` when the test function returns,
    *before* teardown, so the likeliest hang site is after the last `PASSED`
    line in those five logs.
  * two subprocess runs proving the ordering of the thread-stack dumps: a slow
    test that is still running Python is failed by pytest-timeout (under the
    GIL) before faulthandler fires, and a wedge pytest-timeout cannot reach is
    dumped by faulthandler, which then ends the process. faulthandler sits
    ABOVE the kill because its GIL-free dump of a running thread segfaulted CI.
  * an **async** hang — the shape the real one is believed to have — proving the
    watchdog in `_hang_watchdog.py` names the awaiting coroutine. Both
    `faulthandler` and `pytest-timeout` dump the *thread* stack, and a suspended
    coroutine has no frames there, so those two alone bottom out in
    `epoll.poll()` and name nothing. Proven in both directions, with and
    without the plugin.

The subprocess runs pass `-o timeout=… -o timeout_method=…` explicitly: a temp
test file makes the temp directory the rootdir, so the project's
`[tool.pytest.ini_options]` never loads and a bare `--timeout=3` would certify
Linux's default method while CI ran another.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from sse_starlette.sse import AppStatus

from tests.runtime import _hang_watchdog, fake_remote
from tests.runtime._hang_watchdog import DUMP_SENTINEL
from tests.runtime.harness import alloc_port

# 2 x (SERVE_STOP_TIMEOUT + CANCEL_GRACE), the acceptance bound: the stop
# sequence waits at most SERVE_STOP_TIMEOUT, then at most CANCEL_GRACE, then
# raises no matter what.
_MUTANT_BOUND = 2 * (fake_remote.SERVE_STOP_TIMEOUT + fake_remote.CANCEL_GRACE)


async def _bounded(coro: Any, limit: float) -> Any:
    """Run `coro` under a hard bound that never awaits a pending task.

    A regression in the stop sequence must fail this file, not hang it — and
    `asyncio.wait_for` cannot promise that (see the module docstring).
    """
    task = asyncio.ensure_future(coro)
    done, pending = await asyncio.wait({task}, timeout=limit)
    if pending:
        task.cancel()
        await asyncio.wait({task}, timeout=5.0)
        raise AssertionError(f"the test body did not finish within {limit}s")
    return next(iter(done)).result()


class _NeverStops:
    """`serve()` reports started, then never returns — but does honour a
    cancel. Models a uvicorn graceful drain that never completes (this
    harness constructs `uvicorn.Config` without `timeout_graceful_shutdown`,
    so the drain is unbounded)."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.started = False
        self.should_exit = False

    async def serve(self, sockets: Any = None) -> None:
        self.started = True
        await asyncio.Event().wait()


class _SwallowsCancellation:
    """`serve()` catches the `CancelledError` and keeps running.

    Empirically (plan rev 2) such a coroutine survives `wait_for`, an outer
    `wait_for` guard, and `asyncio.run`'s own shutdown. `escape` is the test's
    way to retire the orphan afterwards, so pytest-asyncio's loop teardown —
    which cancels and *gathers* lingering tasks — does not itself hang on it.
    """

    instances: list[_SwallowsCancellation] = []

    def __init__(self, config: Any) -> None:
        self.config = config
        self.started = False
        self.should_exit = False
        self.escape = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.swallowed = False
        _SwallowsCancellation.instances.append(self)

    async def serve(self, sockets: Any = None) -> None:
        self.task = asyncio.current_task()
        self.started = True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.swallowed = True
        await self.escape.wait()


async def test_a_server_task_that_never_finishes_raises_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On unchanged `main` this hangs forever on `await task`."""
    monkeypatch.setattr(fake_remote.uvicorn, "Server", _NeverStops)
    port = alloc_port()

    async def _body() -> None:
        with pytest.raises(RuntimeError) as excinfo:
            async with fake_remote.run_fake_remote(port, expected_auth_value="x"):
                pass
        message = str(excinfo.value)
        assert "did not stop" in message
        assert str(port) in message
        assert "server.started=True" in message
        assert "server.should_exit=True" in message
        assert "AppStatus.should_exit=" in message
        # This mutant does honour the cancel, so the diagnostic must say so
        # rather than claiming the task survived.
        assert "SURVIVED cancellation" not in message

    # `_bounded` already raises on the same bound, so an elapsed assertion
    # could never be the first thing to fail.
    await _bounded(_body(), _MUTANT_BOUND)
    # The latch reset used to sit *after* `await task`, so the new diagnostic
    # raise would have skipped it and poisoned the next test.
    assert AppStatus.should_exit is False


async def test_a_serve_task_that_swallows_cancellation_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mutant a `wait_for`-based stop sequence cannot survive."""
    _SwallowsCancellation.instances.clear()
    monkeypatch.setattr(fake_remote.uvicorn, "Server", _SwallowsCancellation)
    port = alloc_port()

    async def _body() -> None:
        with pytest.raises(RuntimeError) as excinfo:
            async with fake_remote.run_fake_remote(port, expected_auth_value="x"):
                pass
        message = str(excinfo.value)
        assert "did not stop" in message
        assert str(port) in message
        assert "SURVIVED cancellation" in message
        assert "server.started=True" in message
        assert "AppStatus.should_exit=" in message

    try:
        # `_bounded` already raises on the same bound.
        await _bounded(_body(), _MUTANT_BOUND)
        assert AppStatus.should_exit is False
        assert [s.swallowed for s in _SwallowsCancellation.instances] == [True]
    finally:
        # Retire the orphan the mutant deliberately created. Without this it is
        # still pending at loop teardown, where pytest-asyncio cancels and
        # gathers lingering tasks — and this one ignores the first cancel.
        for server in _SwallowsCancellation.instances:
            server.escape.set()
            if server.task is not None:
                await asyncio.wait({server.task}, timeout=5.0)
        _SwallowsCancellation.instances.clear()


_TEARDOWN_HANG_MODULE = """
import time

import pytest


@pytest.fixture
def blocks_on_the_way_out():
    yield
    time.sleep(30)


def test_passes_then_hangs_in_teardown(blocks_on_the_way_out):
    assert True
"""

# A slow test that is still RUNNING Python: pytest-timeout's handler can run,
# so it must fail the item before faulthandler's GIL-free dump ever fires.
_SLOW_TEST_MODULE = """
import time


def test_runs_past_the_kill_timeout():
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        sum(range(1000))


def test_sentinel_still_runs_after_the_kill():
    assert True
"""

# A wedge pytest-timeout cannot reach: SIGALRM is blocked, so its handler never
# runs -- the stand-in for a thread stuck in C or holding the GIL. Only the
# faulthandler last resort can report this one, and it must end the process.
_WEDGED_MODULE = """
import signal
import time


def test_wedged_where_the_signal_cannot_land():
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
    time.sleep(30)


def test_sentinel_must_not_run_after_the_exit():
    assert True
"""


def _write_module(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "test_induced.py"
    path.write_text(body)
    return path


def test_the_timeout_plugin_is_active_and_covers_teardown(
    tmp_path: Path, pytestconfig: pytest.Config
) -> None:
    """A fixture whose *teardown* blocks for 30 s must be killed in seconds.

    The settings are passed with `-o` on purpose: the temp file makes
    `tmp_path` the rootdir, so this project's `[tool.pytest.ini_options]` is
    never read. `timeout_method` is taken from the resolved project config, so
    this certifies the method that actually ships — not Linux's default.
    """
    module = _write_module(tmp_path, _TEARDOWN_HANG_MODULE)
    method = str(pytestconfig.getini("timeout_method"))

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-p",
            "no:cacheprovider",
            "-o",
            "timeout=3",
            "-o",
            f"timeout_method={method}",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )
    output = result.stdout + result.stderr

    # If the plugin had failed to kill the 30s teardown, the child session
    # would have PASSED and printed no timeout text. `timeout=60` on the
    # subprocess is the hang guard.
    assert result.returncode != 0, output
    assert "imeout" in output, output


def test_a_teardown_hang_is_not_caught_without_the_plugin(tmp_path: Path) -> None:
    """The negative half: prove the kill above came from the plugin.

    No `-o timeout=…` here — those options do not exist once the plugin is
    disabled, and pytest would exit fast on the unknown ini key, which would
    satisfy nothing.
    """
    module = _write_module(tmp_path, _TEARDOWN_HANG_MODULE)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-p",
            "no:cacheprovider",
            "-p",
            "no:timeout",
            "-q",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=tmp_path,
    )
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            process.communicate(timeout=10)
    finally:
        process.kill()
        process.communicate()


def _run_ladder(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    """The committed ladder, scaled down: kill at 2 s, last-resort dump at 4 s."""
    module = _write_module(tmp_path, body)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-p",
            "no:cacheprovider",
            "-o",
            "timeout=2",
            "-o",
            "timeout_method=signal",
            "-o",
            "faulthandler_timeout=4",
            "-o",
            "faulthandler_exit_on_timeout=true",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=tmp_path,
    )


def test_a_slow_running_test_is_killed_cleanly_before_faulthandler_fires(
    tmp_path: Path,
) -> None:
    """A test still running Python is failed by pytest-timeout, under the GIL.

    faulthandler's watchdog walks a running thread's frames without the GIL;
    on 2026-10-01 that walk segfaulted four CI jobs (exit 139) when a slow test
    crossed a faulthandler threshold that sat BELOW the kill. With the dump
    above the kill, a slow test must instead fail as an ordinary item, the
    faulthandler timer must be cancelled, and the session must carry on.
    """
    result = _run_ladder(tmp_path, _SLOW_TEST_MODULE)
    output = result.stdout + result.stderr

    assert "Timeout" in output and "1 failed, 1 passed" in output, output
    assert "Timeout (0:00:04)" not in output, output  # faulthandler never fired
    assert result.returncode == 1, output


def test_a_wedge_the_kill_cannot_reach_is_dumped_and_ends_the_run(
    tmp_path: Path,
) -> None:
    """Fail closed: a hang pytest-timeout cannot interrupt still ends red.

    Without `faulthandler_exit_on_timeout` this would dump and then sit until
    the job's cap -- the silent cancel Consiliency/pmcp#200 exists to
    replace. With it, the stacks are printed and the process exits non-zero,
    so the sentinel after the wedge never runs.
    """
    result = _run_ladder(tmp_path, _WEDGED_MODULE)
    output = result.stdout + result.stderr

    assert "Timeout (0:00:04)!" in output, output
    assert "Thread 0x" in output or "Current thread 0x" in output, output
    assert "test_wedged_where_the_signal_cannot_land" in output, output
    assert "passed" not in output, output
    assert result.returncode != 0, output


def _test_job_timeout_minutes(workflow_text: str) -> int:
    """The `test` job's `timeout-minutes` in .github/workflows/test.yml."""
    import yaml

    jobs = yaml.safe_load(workflow_text)["jobs"]
    value = jobs["test"]["timeout-minutes"]
    assert isinstance(value, int) and not isinstance(value, bool)
    return value


def test_faulthandler_is_a_last_resort_above_the_kill_timeout(
    pytestconfig: pytest.Config,
) -> None:
    """The committed ordering: kill first, GIL-free dump only after it.

    `faulthandler_timeout` below `timeout` is the configuration that segfaulted
    CI: any slow-but-running test crossing it got a GIL-free walk of a moving
    frame chain. Above the kill, pytest-timeout fails the item first (and its
    failure cancels the faulthandler timer), so faulthandler only fires for a
    thread the kill could not reach -- and then it must exit, not continue.

    Compared numerically: `getini` hands back a float for one and a string for
    the other (`[tool.pytest.ini_options]` scalars load as strings).
    """
    faulthandler_timeout = float(pytestconfig.getini("faulthandler_timeout"))
    kill_timeout = float(pytestconfig.getini("timeout"))

    assert pytestconfig.getini("timeout_method") == "signal"
    assert 0 < kill_timeout < faulthandler_timeout
    assert pytestconfig.getini("faulthandler_exit_on_timeout") is True
    # Both must fit inside the `test` job's cap, or the silent job-cap cancel
    # this change exists to replace happens anyway. Read the real cap rather
    # than restating it, so the two cannot drift apart.
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/test.yml"
    cap = _test_job_timeout_minutes(workflow.read_text())
    assert faulthandler_timeout < cap * 60


# The shape the real hang is believed to have: a coroutine suspended on an
# await, reached *through an async generator*. `_tap()` in
# `test_emitter_harness.py:65` is an async generator wrapped around the SSE read
# stream, and it is the prime suspect, so the generator hop is the part that
# matters -- not merely nested coroutines.
_ASYNC_HANG_MODULE = """
import asyncio


async def _tap_like_generator():
    await asyncio.Event().wait()
    yield 1


async def _drives_the_generator():
    async for _ in _tap_like_generator():
        break


async def test_hangs_inside_an_async_generator():
    await _drives_the_generator()
"""

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_async_hang(tmp_path: Path, *, with_watchdog: bool) -> str:
    """Run the async-hang module in a subprocess, with or without the plugin.

    cwd is the repo root, not `tmp_path`: `-p tests.runtime._hang_watchdog` is
    an import path and would not resolve from the temp directory. The module
    file still lives under `tmp_path`, so the temp dir is the rootdir and the
    project ini does not load -- hence the explicit `-o` settings.
    """
    module = tmp_path / "test_async_induced.py"
    module.write_text(_ASYNC_HANG_MODULE)
    plugin = ["-p", "tests.runtime._hang_watchdog"] if with_watchdog else []
    env = dict(os.environ, PMCP_ASYNC_STACK_DUMP_AFTER="2")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(module), *plugin]
        + [
            "-p",
            "no:cacheprovider",
            "-o",
            "asyncio_mode=auto",
            "-o",
            "timeout=8",
            "-o",
            "timeout_method=signal",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=_REPO_ROOT,
        env=env,
    )
    return result.stdout + result.stderr


def test_an_async_hang_names_the_awaiting_coroutine(tmp_path: Path) -> None:
    """The gap this watchdog exists to close.

    Without it, a suspended coroutine killed by pytest-timeout reports only
    `epoll.poll()` -- which cannot tell `_tap()` from `connect_server()` from
    `disconnect_all()`, and those are the exact three candidates #200 has.
    """
    output = _run_async_hang(tmp_path, with_watchdog=True)

    assert "Timeout" in output, output
    assert DUMP_SENTINEL in output, output
    # The task's own coroutine -- what `Task.print_stack()` can reach.
    assert "test_hangs_inside_an_async_generator" in output, output
    # And the frames underneath it, which `print_stack()` cannot: a suspended
    # coroutine's `cr_frame.f_back` is None, so the walk down `cr_await` /
    # `ag_await` is the only way to these.
    assert "_drives_the_generator" in output, output
    assert "_tap_like_generator" in output, output
    assert "[await chain]" in output, output


def test_without_the_watchdog_an_async_hang_names_only_the_selector(
    tmp_path: Path,
) -> None:
    """The negative half: prove the frames above came from the watchdog.

    Same hang, same timeout, no plugin. pytest-timeout still fires -- so this is
    not a case of nothing happening -- but the traceback bottoms out in the
    selector and the awaiting coroutine is invisible.
    """
    output = _run_async_hang(tmp_path, with_watchdog=False)

    assert "Timeout" in output, output
    assert DUMP_SENTINEL not in output, output
    assert "[await chain]" not in output, output
    # The epitaph from the CI logs: the thread stack parked in the selector.
    assert "select" in output or "poll" in output, output
    # The frames that only the watchdog can reach are absent.
    assert "_tap_like_generator" not in output, output


async def test_the_watchdog_is_wired_into_this_package(
    request: pytest.FixtureRequest,
) -> None:
    """The subprocess proofs certify the plugin's logic but not the wiring.

    This asserts the `conftest.py` re-export is live in a normal run: the hook
    recorded *this* item, and the autouse async fixture handed the watchdog the
    loop this test is actually running on.
    """
    state = _hang_watchdog._current

    assert state is not None
    assert state["nodeid"] == request.node.nodeid
    assert state["loop"] is asyncio.get_running_loop()
    assert state["dumped"] is False
    # Started lazily, on the first item rather than at import.
    assert _hang_watchdog._thread is not None
    assert _hang_watchdog._thread.daemon is True
