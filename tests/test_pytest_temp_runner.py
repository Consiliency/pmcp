"""Exercise scratch ownership using a tiny stand-in pytest subprocess."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest


RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "pytest_temp.py"


@pytest.fixture
def runner_env(tmp_path: Path) -> dict[str, str]:
    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "pytest.py").write_text(
        "import json, os, pathlib, sys, time\n"
        "base = pathlib.Path(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--basetemp=')))\n"
        "base.mkdir()\n"
        "(base / 'scratch.txt').write_text('disposable')\n"
        "record = {'base': str(base), 'tmp': os.environ['TMPDIR']}\n"
        "pathlib.Path(os.environ['RECEIPT']).write_text(json.dumps(record))\n"
        "if os.environ.get('WAIT') == '1':\n"
        " print('ready', flush=True)\n"
        " time.sleep(60)\n"
        "sys.exit(int(os.environ.get('RESULT', '0')))\n"
    )
    parent = tmp_path / "scratch-parent"
    parent.mkdir()
    return {
        **os.environ,
        "TMPDIR": str(parent),
        "PYTHONPATH": str(modules),
        "PYTEST_ADDOPTS": "",
        "RECEIPT": str(tmp_path / "receipt.json"),
    }


@pytest.mark.parametrize("result", [0, 3])
def test_success_and_failure_remove_only_owned_scratch(
    runner_env: dict[str, str], result: int
) -> None:
    parent = Path(runner_env["TMPDIR"])
    sentinel = parent / "another-run"
    sentinel.mkdir()
    runner_env["RESULT"] = str(result)
    completed = subprocess.run(
        [sys.executable, str(RUNNER), "--", "-q"], env=runner_env, capture_output=True
    )
    assert completed.returncode == result
    record = json.loads(Path(runner_env["RECEIPT"]).read_text())
    assert Path(record["base"]).parent == Path(record["tmp"]).parent
    assert not Path(record["base"]).parent.exists()
    assert list(parent.iterdir()) == [sentinel]


def test_keep_evidence_is_explicit_and_each_run_is_unique(
    runner_env: dict[str, str],
) -> None:
    roots = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--keep-temp", "--", "-q"],
            env=runner_env,
            capture_output=True,
        )
        assert result.returncode == 0
        base = Path(json.loads(Path(runner_env["RECEIPT"]).read_text())["base"])
        assert (base / "scratch.txt").read_text() == "disposable"
        assert str(base.parent).encode() in result.stderr
        if os.name == "posix":
            assert base.parent.stat().st_mode & 0o777 == 0o700
        roots.append(base.parent)
    assert roots[0] != roots[1]


@pytest.mark.parametrize("in_environment", [False, True])
def test_fixed_basetemp_rejected_before_creation(
    runner_env: dict[str, str], in_environment: bool
) -> None:
    fixed = str(Path(runner_env["TMPDIR"]) / "must-not-be-created")
    args = [] if in_environment else [f"--basetemp={fixed}"]
    if in_environment:
        runner_env["PYTEST_ADDOPTS"] = f"--basetemp={fixed}"
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--", *args], env=runner_env, capture_output=True
    )
    assert result.returncode == 2
    assert not list(Path(runner_env["TMPDIR"]).iterdir())


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal forwarding")
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_interruption_waits_for_pytest_then_cleans(
    runner_env: dict[str, str], signum: int
) -> None:
    runner_env["WAIT"] = "1"
    child = subprocess.Popen(
        [sys.executable, str(RUNNER), "--", "-q"],
        env=runner_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        child.send_signal(signum)
        child.communicate(timeout=10)
        assert child.returncode == 128 + signum
        assert not list(Path(runner_env["TMPDIR"]).iterdir())
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def test_second_separator_cannot_hide_owned_basetemp(
    runner_env: dict[str, str],
) -> None:
    result = subprocess.run(
        [sys.executable, str(RUNNER), "--", "--", "-q"],
        env=runner_env,
        capture_output=True,
    )
    assert result.returncode == 2
    assert not list(Path(runner_env["TMPDIR"]).iterdir())
