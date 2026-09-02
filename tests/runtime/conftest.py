"""Fixture wiring for tests/runtime/.

pytest only discovers fixtures from conftest.py (or modules explicitly
imported into it) — a fixture defined in a plain helper module like
`harness.py` is invisible to the collector otherwise. Re-exporting it here
is what makes `gateway_on_spare_port` usable by every test module in this
directory without a per-module import.
"""

from __future__ import annotations

from tests.runtime.harness import gateway_on_spare_port  # noqa: F401

# Consiliency/pmcp#200: the async-stack watchdog. Re-exported here for the
# same reason `gateway_on_spare_port` is -- pytest only discovers fixtures and
# hooks from `conftest.py` (or modules explicitly imported into it). The
# implementation lives in its own module so a subprocess can load it as a
# plugin with `-p tests.runtime._hang_watchdog`, which is how
# `test_hang_diagnostics.py` proves it fires; a test file under `tmp_path`
# takes the temp directory as rootdir and would never load this file.
#
# Scoped to `tests/runtime/` rather than `tests/`: all five CI stalls happened
# here, the slowest item in this package is 26.16 s against the watchdog's 60 s
# threshold, and at `tests/` scope the two ~60.2 s progressive-disclosure tests
# would trip a spurious dump on every single run.
from tests.runtime._hang_watchdog import (  # noqa: F401,E402
    _record_running_loop,
    pytest_runtest_logfinish,
    pytest_runtest_logstart,
)
