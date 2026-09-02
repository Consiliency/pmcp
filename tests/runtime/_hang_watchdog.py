"""Async-stack watchdog for Consiliency/pmcp#200.

`faulthandler_timeout` and `pytest-timeout` both dump the *thread* stack, and
for the hang this repo is chasing that stack says nothing. A suspended
coroutine's frames do not live on any thread stack — they live in `Task`
objects — so the main thread is parked in the selector and the traceback
bottoms out there. Reproduced on this branch, verbatim, with a test that
awaits an `Event` through two coroutine frames:

    asyncio/base_events.py:2019: in _run_once
        event_list = self._selector.select(timeout)
    selectors.py:452: in select
    >   fd_event_list = self._selector.poll(timeout, max_ev)
    E   Failed: Timeout (>3.0s) from pytest-timeout.

`epoll.poll` — the stuck await is invisible. Without this module #200's
diagnostics would turn a 25-minute silent cancel into a 700-second failure
that still could not tell `_tap()` from `connect_server()` from
`disconnect_all()`, which is the entire question the change exists to answer.

So: a watchdog thread that, when the current test item has been running too
long, dumps every pending `asyncio.Task` on that item's event loop.

Three ordered thresholds, each strictly below the next:

    60 s   this watchdog        -> async stacks, names the awaiting coroutine
    120 s  faulthandler_timeout -> every thread's stack, the run continues
    700 s  pytest-timeout       -> the item fails and the run moves on
    25 min the job's cap        -> what used to happen instead of all of this

60 s is the threshold because the slowest item in `tests/runtime` is 26.16 s
(`--durations=25`, 2026-09-02), so this has >2x headroom over anything here
and cannot fire spuriously.

**Why the walk below is not just `Task.print_stack()`.** For a *suspended*
coroutine `Task.get_stack()` returns a single frame — the coroutine's own
`cr_frame`, whose `f_back` is `None` because a suspended coroutine is not on
anyone's stack. That names the task's outermost function and nothing beneath
it. The awaited chain is reachable only through `cr_await` / `ag_await`, so
this module walks it explicitly. That walk is *required*, not a refinement:
the prime suspect is `_tap()` in `test_emitter_harness.py:65`, which is an
**async generator** wrapped around the SSE read stream. `print_stack()` would
report the task that drives it; only the `ag_frame`/`ag_await` branch names
`_tap` itself.

Both are printed: `print_stack()` for the familiar rendering, then the labelled
await chain that actually locates the hang.

**This must never fail.** A watchdog that can raise is worse than no watchdog,
because it converts a diagnosable hang into a confusing error. Every path here
is wrapped and swallows; the worst case is silence.

Lives in its own module rather than directly in `conftest.py` so a subprocess
can load it as a plugin with `-p tests.runtime._hang_watchdog`. A test file
written into a `tmp_path` takes the temp directory as rootdir and never loads
this package's `conftest.py`, so the mutant proof in
`test_hang_diagnostics.py` could not otherwise exercise it — the same rootdir
trap that the stage-1 `-o timeout=...` settings had to work around.
"""

from __future__ import annotations

import asyncio
import linecache
import os
import sys
import threading
import time
from typing import Any

import pytest

# Grep-able marker. The mutant proof asserts on this exact string, in both
# directions, so it must not be reworded casually.
DUMP_SENTINEL = "[async-stacks]"

# Overridable so the mutant proof can induce a dump in seconds instead of
# waiting a minute. Not an ini option: this is a debugging knob, not
# configuration, and the default is what every real run uses.
_ENV_VAR = "PMCP_ASYNC_STACK_DUMP_AFTER"
_DEFAULT_DUMP_AFTER = 60.0

_POLL_INTERVAL = 0.5
_MAX_CHAIN_FRAMES = 40

_lock = threading.Lock()
_current: dict[str, Any] | None = None
_thread: threading.Thread | None = None


def _dump_after() -> float:
    try:
        return float(os.environ.get(_ENV_VAR, _DEFAULT_DUMP_AFTER))
    except (TypeError, ValueError):
        return _DEFAULT_DUMP_AFTER


def _frame_lines(obj: Any) -> list[str]:
    """Walk `cr_await` / `ag_await` / `gi_yieldfrom` from a task's coroutine
    down to whatever it is ultimately suspended on.

    Handles async generators explicitly (`ag_frame`, `ag_await`) — see the
    module docstring; that branch exists for `_tap()`.

    The walk stops at a bare `Future` (an `Event.wait()`, a socket read), which
    has no frame of its own. It is cycle-guarded and length-capped, so a
    self-referential chain cannot spin here.
    """
    lines: list[str] = []
    seen: set[int] = set()
    while obj is not None and len(lines) < _MAX_CHAIN_FRAMES:
        if id(obj) in seen:
            lines.append("      ... cycle in the await chain, stopping")
            break
        seen.add(id(obj))

        # A nested Task/Future in the chain: step into its coroutine if it has
        # one, so an awaited task does not end the walk.
        if isinstance(obj, asyncio.Task):
            inner = obj.get_coro()
            if inner is not None and id(inner) not in seen:
                lines.append(f"      -- awaiting {obj!r}")
                obj = inner
                continue

        frame = (
            getattr(obj, "cr_frame", None)
            or getattr(obj, "ag_frame", None)
            or getattr(obj, "gi_frame", None)
        )
        if frame is not None:
            code = frame.f_code
            lines.append(
                f'      File "{code.co_filename}", line {frame.f_lineno}, '
                f"in {code.co_name}"
            )
            source = linecache.getline(code.co_filename, frame.f_lineno).strip()
            if source:
                lines.append(f"        {source}")

        nxt = (
            getattr(obj, "cr_await", None)
            or getattr(obj, "ag_await", None)
            or getattr(obj, "gi_yieldfrom", None)
        )
        if nxt is None:
            nxt = _step_through_opaque(obj)
        if nxt is None:
            if frame is None:
                lines.append(f"      -- suspended on {obj!r}")
            break
        obj = nxt
    return lines


def _step_through_opaque(obj: Any) -> Any:
    """Get past an awaitable that exposes no frame and no `*_await`.

    `async for x in agen()` suspends on an `async_generator_asend`, whose only
    public attributes are `close`/`send`/`throw` — it has no `ag_frame` and no
    `ag_await`, so the walk above dead-ends one hop short of the generator.
    That is not an edge case here: it is precisely the shape of `_tap()`, the
    prime suspect, so without this hop the branch that motivated the whole
    watchdog would stop just before naming its target. Verified: the object's
    referents are exactly `[async_generator]`.

    Deliberately narrow — the first referent carrying a coroutine/generator
    frame, and nothing else — and fully guarded, because `gc.get_referents` on
    an arbitrary object is only a heuristic.
    """
    try:
        import gc

        for ref in gc.get_referents(obj):
            if (
                getattr(ref, "ag_frame", None) is not None
                or getattr(ref, "cr_frame", None) is not None
                or getattr(ref, "gi_frame", None) is not None
            ):
                return ref
    except Exception:  # pragma: no cover - diagnostics only
        return None
    return None


def _pending_tasks(loop: asyncio.AbstractEventLoop) -> list[asyncio.Task[Any]]:
    """`asyncio.all_tasks` snapshots a WeakSet the running loop is mutating, so
    from this thread it can raise `RuntimeError: Set changed size during
    iteration`. Retry a few times, then give up quietly."""
    for _ in range(5):
        try:
            return [t for t in asyncio.all_tasks(loop) if not t.done()]
        except RuntimeError:
            time.sleep(0.05)
    return []


def _render(nodeid: str, elapsed: float, loop: Any) -> str:
    out: list[str] = [
        "",
        f"{DUMP_SENTINEL} {nodeid} has been running {elapsed:.1f}s "
        f"(threshold {_dump_after():.1f}s) -- see Consiliency/pmcp#200",
    ]
    if loop is None:
        out.append(
            f"{DUMP_SENTINEL} no event loop was recorded for this item; "
            "if it is a sync test there are no async stacks to show."
        )
        return "\n".join(out) + "\n"

    tasks = _pending_tasks(loop)
    out.append(f"{DUMP_SENTINEL} {len(tasks)} pending task(s) on {loop!r}:")
    for task in tasks:
        out.append(f"--- {task!r}")
        try:
            import io

            buf = io.StringIO()
            task.print_stack(file=buf)
            out.append(buf.getvalue().rstrip())
        except Exception as exc:  # pragma: no cover - diagnostics only
            out.append(f"    <print_stack failed: {exc!r}>")
        try:
            chain = _frame_lines(task.get_coro())
        except Exception as exc:  # pragma: no cover - diagnostics only
            chain = [f"      <await-chain walk failed: {exc!r}>"]
        if chain:
            out.append("    [await chain] (what print_stack cannot reach):")
            out.extend(chain)
    return "\n".join(out) + "\n"


def _emit(text: str) -> None:
    # One write, so the dump cannot interleave with the main thread's output.
    try:
        sys.stderr.write(text)
        sys.stderr.flush()
    except Exception:  # pragma: no cover - diagnostics only
        pass


def _watch() -> None:
    while True:
        time.sleep(_POLL_INTERVAL)
        try:
            with _lock:
                state = _current
                if state is None or state["dumped"]:
                    continue
                elapsed = time.monotonic() - state["started"]
                if elapsed < _dump_after():
                    continue
                # Flip inside the lock so this fires at most once per item.
                state["dumped"] = True
                nodeid, loop = state["nodeid"], state["loop"]
            _emit(_render(nodeid, elapsed, loop))
        except Exception:  # pragma: no cover - a watchdog must never raise
            pass


def _ensure_thread() -> None:
    global _thread
    if _thread is not None:
        return
    # Started lazily on the first item, so `--collect-only` spawns nothing.
    # Daemon and never joined: it must not hold the interpreter open.
    _thread = threading.Thread(
        target=_watch, name="pmcp-async-stack-watchdog", daemon=True
    )
    _thread.start()


def pytest_runtest_logstart(nodeid: str, location: Any) -> None:
    """Fires before setup, so the window covers setup, call **and teardown**.

    Teardown coverage is the point: pytest prints `PASSED` when the test
    function returns, before teardown, and pytest-asyncio's loop teardown is
    itself a prime suspect.
    """
    global _current, _thread
    try:
        with _lock:
            _current = {
                "nodeid": nodeid,
                "started": time.monotonic(),
                "loop": None,
                "dumped": False,
            }
            if _thread is None:
                _ensure_thread()
    except Exception:  # pragma: no cover - a watchdog must never raise
        pass


def pytest_runtest_logfinish(nodeid: str, location: Any) -> None:
    global _current
    try:
        with _lock:
            _current = None
    except Exception:  # pragma: no cover - a watchdog must never raise
        pass


@pytest.fixture(autouse=True)
async def _record_running_loop() -> Any:
    """Hand the watchdog thread the loop this item is running on.

    It has to come from `asyncio.get_running_loop()` inside an **async**
    fixture. There is no way to reach a running loop from the watchdog thread
    itself, and the sync-context alternatives do not work:
    `get_event_loop_policy().get_event_loop()` raises
    `RuntimeError('no running event loop')` here.

    Async and autouse is safe for the sync tests in this package too —
    `asyncio_mode = "auto"` gives them a loop as well, verified.
    """
    try:
        loop = asyncio.get_running_loop()
        with _lock:
            if _current is not None:
                _current["loop"] = loop
    except Exception:  # pragma: no cover - a watchdog must never raise
        pass
    yield
