"""Opt-in CI phase timings. No test text, arguments or captured output is saved."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

_phases: list[dict[str, object]] = []
_report_dir: Path | None = None
_worker = "master"


def pytest_configure(config: pytest.Config) -> None:
    global _report_dir, _worker
    target = os.environ.get("PMCP_CI_REPORT_DIR")
    if target is None:
        return
    # Only workers record phases under xdist, avoiding controller duplicates.
    if config.getoption("numprocesses", default=0) and not hasattr(
        config, "workerinput"
    ):
        return
    _report_dir = Path(target).resolve()
    _report_dir.mkdir(parents=True, exist_ok=True)
    _worker = os.environ.get("PYTEST_XDIST_WORKER", "master")


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if _report_dir is not None:
        _phases.append(
            {
                "id": hashlib.sha256(report.nodeid.encode()).hexdigest(),
                "phase": report.when,
                "outcome": report.outcome,
                "seconds": report.duration,
            }
        )


def pytest_sessionfinish(session: pytest.Session) -> None:
    if _report_dir is not None:
        (_report_dir / f"timing-{_worker}.json").write_text(
            json.dumps(
                {
                    "worker": _worker,
                    "selected": session.testscollected,
                    "phases": _phases,
                }
            )
            + "\n"
        )
