"""Actual subprocess/xdist reporting, including producer failures and crashes."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ci_reports_runtime", ROOT / "scripts/ci_reports.py"
)
assert spec and spec.loader
reports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reports)


def produce(
    tmp_path: Path, source: str, *, workers: int = 2
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    # Copy the exact plugin/package shape used by CI. No checkout PYTHONPATH.
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").touch()
    shutil.copy2(ROOT / "tests/ci_diagnostics.py", tests / "ci_diagnostics.py")
    (tests / "test_producer.py").write_text(source)
    raw = tmp_path / "raw"
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS"}
    }
    env["PMCP_CI_REPORT_DIR"] = str(raw)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "-q",
            "-n",
            str(workers),
            "--dist",
            "loadfile",
            "--max-worker-restart=0",
            "-p",
            "tests.ci_diagnostics",
            "--junitxml=" + str(raw / "junit.xml"),
            "-o",
            "addopts=",
            "--basetemp=" + str(tmp_path / "subprocess-tmp"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return raw, result


def test_real_python_module_startup_and_per_worker_counts(tmp_path: Path) -> None:
    raw, result = produce(tmp_path, "def test_one(): pass\ndef test_two(): pass\n")
    assert result.returncode == 0, result.stdout + result.stderr
    timing = reports.sanitize_timings(raw)
    assert timing["complete"] is True
    assert sum(w["selected"] for w in timing["workers"]) == 2
    assert sorted(w["selected"] for w in timing["workers"]) == [0, 2]
    assert all(w["collected_total"] == 2 for w in timing["workers"])
    # R9's aligned discovery cannot overwrite the existing run evidence.
    before = (raw / "timings.json").read_bytes()
    env = {**os.environ, "PMCP_CI_REPORT_DIR": str(raw)}
    discovery = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/",
            "-p",
            "tests.ci_diagnostics",
            "--collect-only",
            "-q",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=30,
    )
    assert discovery.returncode == 0
    assert (raw / "timings.json").read_bytes() == before


def test_real_setup_call_skip_and_teardown_outcomes_keep_errors_private(
    tmp_path: Path,
) -> None:
    raw, result = produce(
        tmp_path,
        """import pytest
@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError("REJECTED-CANARY-TEARDOWN")
@pytest.fixture
def broken_setup(broken_teardown):
    raise RuntimeError("REJECTED-CANARY-SETUP")
def test_setup(broken_setup): pass
def test_call(broken_teardown): assert False, "REJECTED-CANARY-CALL"
def test_skip(broken_teardown): pytest.skip("REJECTED-CANARY-SKIP")
""",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert b"REJECTED-CANARY" in (raw / "junit.xml").read_bytes()
    safe, count = reports.sanitize_junit((raw / "junit.xml").read_bytes())
    assert count >= 3 and b"REJECTED-CANARY" not in safe
    cases = ET.fromstring(safe).findall("testcase")
    assert sum(c.find("error") is not None for c in cases) >= 3
    assert reports.sanitize_timings(raw)["complete"] is True


def test_crashed_worker_retains_received_phases_and_is_incomplete(
    tmp_path: Path,
) -> None:
    raw, result = produce(
        tmp_path,
        """import os
def test_a_completed(): pass
def test_b_crash(): os._exit(17)
def test_c_unexecuted(): pass
""",
    )
    assert result.returncode != 0
    timing = reports.sanitize_timings(raw)
    assert timing["complete"] is False
    crashed = [w for w in timing["workers"] if w["crashed"]]
    assert len(crashed) == 1
    assert crashed[0]["completed"] >= 1
    assert crashed[0]["selected"] >= 1
    reports.sanitize_junit((raw / "junit.xml").read_bytes())


def test_recorder_state_resets_when_disabled_or_collecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from tests import ci_diagnostics as plugin

    monkeypatch.setenv("PMCP_CI_REPORT_DIR", str(tmp_path))
    config = SimpleNamespace(
        getoption=lambda *a, **k: 0, option=SimpleNamespace(collectonly=False)
    )
    plugin.pytest_configure(cast(pytest.Config, config))
    assert plugin._report_dir == tmp_path
    plugin._workers["master"]["phases"].append({"id": "old"})
    monkeypatch.delenv("PMCP_CI_REPORT_DIR")
    plugin.pytest_configure(cast(pytest.Config, config))
    assert plugin._report_dir is None and plugin._workers == {}
