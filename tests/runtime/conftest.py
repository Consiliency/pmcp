"""Fixture wiring for tests/runtime/.

pytest only discovers fixtures from conftest.py (or modules explicitly
imported into it) — a fixture defined in a plain helper module like
`harness.py` is invisible to the collector otherwise. Re-exporting it here
is what makes `gateway_on_spare_port` usable by every test module in this
directory without a per-module import.
"""

from __future__ import annotations

from tests.runtime.harness import gateway_on_spare_port  # noqa: F401
