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
  * a subprocess run proving `faulthandler_timeout` dumps a stack and lets the
    session continue, and that the dump is scheduled strictly before the kill.

The subprocess runs pass `-o timeout=… -o timeout_method=…` explicitly: a temp
test file makes the temp directory the rootdir, so the project's
`[tool.pytest.ini_options]` never loads and a bare `--timeout=3` would certify
Linux's default method while CI ran another.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from sse_starlette.sse import AppStatus

from tests.runtime import fake_remote
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

    started = time.monotonic()
    await _bounded(_body(), _MUTANT_BOUND)
    elapsed = time.monotonic() - started
    assert elapsed < _MUTANT_BOUND, elapsed
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

    started = time.monotonic()
    try:
        await _bounded(_body(), _MUTANT_BOUND)
        elapsed = time.monotonic() - started
        assert elapsed < _MUTANT_BOUND, elapsed
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

_FAULTHANDLER_MODULE = """
import time


def test_blocks_long_enough_to_be_dumped():
    time.sleep(5)


def test_sentinel_still_runs_after_the_dump():
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

    started = time.monotonic()
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
    elapsed = time.monotonic() - started
    output = result.stdout + result.stderr

    assert result.returncode != 0, output
    assert elapsed < 15, f"{elapsed}s\n{output}"
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


def test_faulthandler_dumps_a_stack_and_the_run_continues(tmp_path: Path) -> None:
    """The zero-risk half: it kills nothing, it only prints the stacks.

    Reading the ini value cannot prove either the dump or the continuation, so
    this induces both: a blocking item under a 2 s `faulthandler_timeout`, then
    a sentinel item that must still run, with the session exiting normally.
    """
    module = _write_module(tmp_path, _FAULTHANDLER_MODULE)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(module),
            "-p",
            "no:cacheprovider",
            "-o",
            "faulthandler_timeout=2",
            "-o",
            "timeout=30",
            "-q",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=tmp_path,
    )
    output = result.stdout + result.stderr

    assert "Thread 0x" in output, output
    assert "Timeout (0:00:02)" in output, output
    assert "2 passed" in output, output
    assert result.returncode == 0, output


def test_faulthandler_timeout_is_below_the_kill_timeout(
    pytestconfig: pytest.Config,
) -> None:
    """So the stack dump always lands before the process-level kill.

    Compared numerically: `getini` hands back a float for one and a string for
    the other (`[tool.pytest.ini_options]` scalars load as strings), so an
    `isinstance(..., int)` check would be invalid either way.
    """
    faulthandler_timeout = float(pytestconfig.getini("faulthandler_timeout"))
    kill_timeout = float(pytestconfig.getini("timeout"))

    assert faulthandler_timeout > 0
    assert faulthandler_timeout < kill_timeout
    # Both must fit inside the `test` job's `timeout-minutes: 25`, or the
    # 25-minute silent cancel this change exists to replace happens anyway.
    assert kill_timeout < 25 * 60
