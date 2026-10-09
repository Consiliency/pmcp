"""Quiet logging for the overlay check's field-attribution passes.

When an overlay entry fails the parse-time check, ``loader._checked_entry``
re-runs the check with fields left out to find the one at fault. Those passes
must log nothing: the entry's own warnings come once, from the one real pass
(Consiliency/pmcp#375 board round 3). A context variable marks the passes, so
another thread's or task's records are never dropped, and each module that the
check reaches logs through ``quiet_during_attribution``. That is a logger
adapter, not a record filter: pmcp installs no record filters outside
``setup_logging`` (``tests/test_auth_operator_messages.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

ATTRIBUTION_PROBE: ContextVar[bool] = ContextVar(
    "pmcp_overlay_attribution_probe", default=False
)


class _QuietDuringAttribution(logging.LoggerAdapter):  # type: ignore[type-arg]
    """``logger``, except that nothing is logged during an attribution pass."""

    def isEnabledFor(self, level: int) -> bool:  # noqa: N802 - logging's name
        if ATTRIBUTION_PROBE.get():
            return False
        return self.logger.isEnabledFor(level)

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        return msg, kwargs


def quiet_during_attribution(name: str) -> logging.LoggerAdapter:  # type: ignore[type-arg]
    """The logger called ``name``, silent while an attribution pass runs."""
    return _QuietDuringAttribution(logging.getLogger(name), {})


@contextmanager
def attribution_pass() -> Iterator[None]:
    """Mark the enclosed code as an attribution pass (in this context only)."""
    token = ATTRIBUTION_PROBE.set(True)
    try:
        yield
    finally:
        ATTRIBUTION_PROBE.reset(token)
