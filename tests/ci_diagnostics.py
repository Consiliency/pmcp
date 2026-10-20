"""Opt-in controller timings. Never save test text or captured output."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

_workers: dict[str, dict[str, Any]] = {}
_report_dir: Path | None = None
_distributed = False
_expected = 1
_unattributed = 0


def pytest_configure(config: pytest.Config) -> None:
    global _report_dir, _distributed, _expected, _unattributed
    _workers.clear()
    _report_dir = None
    _expected, _unattributed = 1, 0
    _distributed = bool(config.getoption("numprocesses", default=0))
    target = os.environ.get("PMCP_CI_REPORT_DIR")
    # Collection discovery uses the same plugin without changing a run's
    # evidence. Workers stream reports to the sole recording controller.
    if not target or config.option.collectonly or hasattr(config, "workerinput"):
        return
    _report_dir = Path(target).resolve()
    _report_dir.mkdir(parents=True, exist_ok=True)
    if not _distributed:
        _worker("master")


def _worker(name: str) -> dict[str, Any]:
    return _workers.setdefault(
        name,
        {
            "worker": name,
            "collected_total": None,
            "finished": False,
            "crashed": False,
            "phases": [],
        },
    )


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_setupnodes(config: pytest.Config, specs: list[Any]) -> None:
    global _expected
    if _report_dir is not None:
        _expected = len(specs)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodeready(node: Any) -> None:
    if _report_dir is not None:
        _worker(node.gateway.id)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node: Any, ids: list[str]) -> None:
    if _report_dir is not None:
        _worker(node.gateway.id)["collected_total"] = len(ids)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: object) -> None:
    if _report_dir is not None:
        worker = _worker(node.gateway.id)
        worker["finished"], worker["crashed"] = True, error is not None


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    global _unattributed
    if _report_dir is None:
        return
    name = getattr(report, "worker_id", None) if _distributed else "master"
    # Synthetic crash reports can lack a worker id; never assign them to a
    # healthy worker or copy their failure text. JUnit still records the error.
    if name not in _workers:
        _unattributed += 1
        return
    _workers[name]["phases"].append(
        {
            "id": hashlib.sha256(report.nodeid.encode()).hexdigest(),
            "phase": report.when,
            "outcome": report.outcome,
            "seconds": report.duration,
        }
    )


def pytest_sessionfinish(session: pytest.Session) -> None:
    if _report_dir is None:
        return
    if not _distributed:
        _workers["master"].update(collected_total=session.testscollected, finished=True)
    (_report_dir / "timings.json").write_text(
        json.dumps(
            {
                "schema": 2,
                "expected_workers": _expected,
                "unattributed_reports": _unattributed,
                "workers": list(_workers.values()),
            }
        )
        + "\n"
    )
