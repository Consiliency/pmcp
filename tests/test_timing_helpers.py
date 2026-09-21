"""The frozen contract of `tests/_timing.py`.

Slice C2 converts ~12 files onto these helpers, so the clauses it relies on --
`timeout=0` means "check now", a raising predicate is not swallowed, and an
unmet `Rendezvous` fails by name rather than hanging -- are pinned here.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests._timing import Rendezvous, eventually, eventually_sync


class TestEventually:
    async def test_it_returns_the_first_truthy_value(self) -> None:
        seen = 0

        def predicate() -> int:
            nonlocal seen
            seen += 1
            return seen if seen >= 3 else 0

        assert await eventually(predicate, interval=0.001) == 3
        assert seen == 3

    async def test_an_async_predicate_is_awaited(self) -> None:
        async def predicate() -> str:
            return "ready"

        assert await eventually(predicate) == "ready"

    async def test_a_raising_predicate_propagates_immediately(self) -> None:
        """Not swallowed until the deadline: the deadline is 30s here."""

        async def predicate() -> bool:
            raise ValueError("boom")

        started = asyncio.get_running_loop().time()
        with pytest.raises(ValueError, match="boom"):
            await eventually(predicate, timeout=30.0)
        assert asyncio.get_running_loop().time() - started < 1.0

    async def test_timeout_zero_checks_once_without_sleeping(self) -> None:
        """The frozen "must already be true" form C2 uses."""
        calls = 0

        def predicate() -> bool:
            nonlocal calls
            calls += 1
            return False

        with pytest.raises(AssertionError):
            await eventually(predicate, timeout=0)
        assert calls == 1, "timeout=0 must evaluate exactly once"

    async def test_timeout_zero_returns_an_already_true_value(self) -> None:
        assert await eventually(lambda: "now", timeout=0) == "now"

    async def test_the_message_names_the_condition(self) -> None:
        with pytest.raises(AssertionError, match="the queue never drained"):
            await eventually(
                lambda: False, timeout=0.02, message="the queue never drained"
            )


class TestEventuallySync:
    def test_it_returns_the_first_truthy_value(self) -> None:
        seen = 0

        def predicate() -> int:
            nonlocal seen
            seen += 1
            return seen if seen >= 2 else 0

        assert eventually_sync(predicate, interval=0.001) == 2

    def test_timeout_zero_checks_once_without_sleeping(self) -> None:
        started = time.monotonic()
        with pytest.raises(AssertionError):
            eventually_sync(lambda: False, timeout=0, interval=5.0)
        assert time.monotonic() - started < 1.0


class TestRendezvous:
    def test_it_can_be_built_outside_the_loop_that_later_drives_it(self) -> None:
        """A gate constructed with no loop running works in the next one.

        Since 3.10 an `asyncio.Event` binds to a loop lazily, on first use, so
        this holds -- but it is the property callers depend on (a Rendezvous is
        often built in sync setup), and it would break if the implementation
        ever captured a loop in `__init__`.
        """
        gate = Rendezvous(2, timeout=1.0)

        async def body() -> None:
            await asyncio.wait_for(asyncio.gather(gate.arrive(), gate.arrive()), 5.0)

        asyncio.run(body())
        assert gate.arrived == 2

    async def test_all_parties_are_released_once_the_last_arrives(self) -> None:
        gate = Rendezvous(3)
        await asyncio.wait_for(
            asyncio.gather(gate.arrive(), gate.arrive(), gate.arrive()), 5.0
        )
        assert gate.arrived == 3

    async def test_a_lone_party_fails_by_name_rather_than_hanging(self) -> None:
        gate = Rendezvous(3, timeout=0.05)
        with pytest.raises(
            AssertionError, match="only 1 of 3 parties were in flight together"
        ):
            await gate.arrive()

    async def test_the_timeout_is_an_asyncio_timeout_not_the_builtin(self) -> None:
        """On 3.10 `asyncio.TimeoutError is TimeoutError` is False.

        Catching the builtin would let the raw timeout escape, so the failure
        would not name the parties. The assertion above proves the message; this
        pins *why* the catch must be spelled `asyncio.TimeoutError`.
        """
        gate = Rendezvous(2, timeout=0.05)
        with pytest.raises(AssertionError):
            await gate.arrive()
        # A raw asyncio.TimeoutError escaping would not be an AssertionError.

    async def test_a_single_party_gate_releases_itself(self) -> None:
        gate = Rendezvous(1, timeout=0.05)
        await asyncio.wait_for(gate.arrive(), 5.0)
        assert gate.arrived == 1
