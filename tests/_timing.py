"""Deterministic waiting primitives for the test suite.

The rule these helpers exist to enforce:

* **Lower bounds on a real timer are safe.** ``await asyncio.sleep(8.2)``
  followed by ``assert elapsed > 8`` cannot fail — a sleep never returns
  early — so such assertions are documentation, not flake risk.
* **Upper bounds on elapsed wall time are the flaky class.** ``assert
  elapsed < 0.2`` fails on a loaded CI runner even when the code is correct
  (see Consiliency/pmcp#226). Replace one with the deterministic property it
  was standing in for: wait for that property with :func:`eventually`, and
  prove concurrency with :class:`Rendezvous`.
* **``timeout=`` on everything here is a hang guard, never an assertion.**
  It exists so a broken test fails loudly instead of hanging the suite. Never
  tighten one to make it "measure" something.

:func:`eventually` sleeps with :func:`asyncio.sleep`, so it must not be called
from inside a test that patches ``asyncio.sleep`` globally.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Awaitable, Callable, TypeVar

__all__ = ["Rendezvous", "eventually", "eventually_sync"]

T = TypeVar("T")

Predicate = Callable[[], "T | Awaitable[T]"]


async def eventually(
    predicate: Predicate,
    *,
    timeout: float = 5.0,
    interval: float = 0.01,
    message: str | None = None,
) -> Any:
    """Poll ``predicate`` until it returns a truthy value; return that value.

    ``predicate`` may be sync or async and takes no arguments. It is evaluated
    at least once *before* the deadline is consulted, so ``timeout=0`` means
    "must already be true": evaluate once, return the value if truthy,
    otherwise raise without ever sleeping.

    An exception raised by ``predicate`` propagates immediately — it is not
    swallowed until the deadline.

    Raises ``AssertionError`` when the deadline passes with no truthy value.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return result
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(message or f"condition not met within {timeout}s")
        await asyncio.sleep(interval)


def eventually_sync(
    predicate: Callable[[], T],
    *,
    timeout: float = 30.0,
    interval: float = 0.5,
    message: str | None = None,
) -> T:
    """Blocking twin of :func:`eventually`, for subprocess/thread polling.

    Same contract: the predicate is evaluated at least once before the
    deadline is consulted, so ``timeout=0`` is "check now".
    """
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise AssertionError(message or f"condition not met within {timeout}s")
        time.sleep(interval)


class Rendezvous:
    """Prove that ``parties`` coroutines are in flight *at the same time*.

    Each participant awaits :meth:`arrive`. Nobody is released until the
    ``parties``-th arrival, so a serial implementation — which cannot get a
    second participant to the gate while the first is parked — fails with a
    named assertion instead of hanging.

    ``timeout`` is the hang guard, not a measurement: it bounds how long a
    single participant waits for the others before declaring the gate unmet.
    """

    def __init__(self, parties: int, *, timeout: float = 2.0) -> None:
        if parties < 1:
            raise ValueError("parties must be >= 1")
        self.parties = parties
        self.timeout = timeout
        self.arrived = 0
        # Safe to build outside a running loop: since 3.10 an Event binds to a
        # loop lazily, on first use (`asyncio.mixins._LoopBoundMixin._get_loop`),
        # not at construction. It is therefore bound by whichever loop first
        # calls `arrive()` -- one Rendezvous still belongs to one loop.
        self._event = asyncio.Event()

    async def arrive(self) -> None:
        """Register this participant and block until all of them have."""
        self.arrived += 1
        if self.arrived >= self.parties:
            self._event.set()
            return
        try:
            await asyncio.wait_for(self._event.wait(), self.timeout)
        except asyncio.TimeoutError:
            # NOT the builtin TimeoutError: on Python 3.10 (the project floor)
            # asyncio.TimeoutError is a distinct class, and catching the
            # builtin would let the raw timeout escape unnamed.
            raise AssertionError(
                f"only {self.arrived} of {self.parties} parties were in flight together"
            ) from None
