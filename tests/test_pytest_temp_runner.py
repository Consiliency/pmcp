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
        "import json, os, pathlib, signal, subprocess, sys, tempfile, time\n"
        "base = pathlib.Path([a.split('=', 1)[1] for a in sys.argv if a.startswith('--basetemp=')][-1])\n"
        "base.mkdir()\n"
        "(base / 'scratch.txt').write_text('disposable')\n"
        "record = {'base': str(base), 'tmp': os.environ['TMPDIR'],"
        " 'gettempdir': tempfile.gettempdir(), 'pid': os.getpid()}\n"
        "result = int(os.environ.get('RESULT', '0'))\n"
        "handled = [s for s in (signal.SIGINT, signal.SIGTERM, getattr(signal, 'SIGHUP', None)) if s]\n"
        "for s in handled:\n"
        " if os.environ.get('HANDLE') == '1':\n"
        "  signal.signal(s, lambda *_: sys.exit(result))\n"
        " else:\n"
        "  signal.signal(s, signal.SIG_DFL)\n"
        "if os.environ.get('DETACHED') == '1':\n"
        " code = ('import os, pathlib, sys, time\\n'\n"
        "         'log = pathlib.Path(sys.argv[1])\\n'\n"
        "         'while True:\\n'\n"
        "         '    with log.open(\"a\") as f:\\n'\n"
        '         \'        f.write("ok\\\\n" if os.path.isdir(os.environ["TMPDIR"]) else "missing\\\\n")\\n\'\n'
        "         '    time.sleep(0.02)\\n')\n"
        " d = subprocess.Popen([sys.executable, '-c', code, os.environ['DETACHED_LOG']],"
        " start_new_session=True)\n"
        " record['detached'] = d.pid\n"
        " while not pathlib.Path(os.environ['DETACHED_LOG']).exists():\n"
        "  time.sleep(0.01)\n"
        "if os.environ.get('GRANDCHILD') == '1':\n"
        " g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        " record['grandchild'] = g.pid\n"
        "if os.environ.get('LOCK') == '1':\n"
        " locked = base / 'locked'\n"
        " (locked / 'inner').mkdir(parents=True)\n"
        " (locked / 'inner' / 'file').write_text('x')\n"
        " os.chmod(locked / 'inner', 0)\n"
        " os.chmod(locked, 0o500)\n"
        "if os.environ.get('LOCK_PARENT') == '1':\n"
        " os.chmod(pathlib.Path(os.environ['TMPDIR']).parent.parent, 0o500)\n"
        "pathlib.Path(os.environ['RECEIPT']).write_text(json.dumps(record))\n"
        "if os.environ.get('WAIT') == '1':\n"
        " print('ready', flush=True)\n"
        " time.sleep(60)\n"
        "sys.exit(result)\n"
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
    # tmp_path must sit under tempfile.gettempdir(): pmcp's project-root walk
    # stops there, so it cannot climb into the shared parent directory.
    base, tmp = Path(record["base"]), Path(record["tmp"])
    assert base.parent == tmp
    assert base.is_relative_to(Path(record["gettempdir"]))
    assert not tmp.parent.exists()
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
        root = base.parent.parent
        assert (base / "scratch.txt").read_text() == "disposable"
        assert str(root).encode() in result.stderr
        if os.name == "posix":
            assert root.stat().st_mode & 0o777 == 0o700
        roots.append(root)
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


def _start_waiting(runner_env: dict[str, str]) -> subprocess.Popen[str]:
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
    except BaseException:
        _stop(child, runner_env)
        raise
    return child


def _stop(child: subprocess.Popen[str], runner_env: dict[str, str]) -> None:
    """Never leave the runner or its stand-in pytest behind after a failure."""
    if child.poll() is None:
        child.kill()
    receipt = Path(runner_env["RECEIPT"])
    if receipt.exists():
        record = json.loads(receipt.read_text())
        for key in ("pid", "grandchild", "detached"):
            if key in record and _pid_alive(record[key]):
                os.kill(record[key], signal.SIGKILL)
    child.communicate(timeout=10)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


POSIX_SIGNALS = (
    [signal.SIGINT, signal.SIGTERM, signal.SIGHUP] if os.name == "posix" else []
)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal forwarding")
@pytest.mark.parametrize("signum", POSIX_SIGNALS)
def test_interruption_waits_for_pytest_then_cleans(
    runner_env: dict[str, str], signum: int
) -> None:
    child = _start_waiting(runner_env)
    try:
        child.send_signal(signum)
        child.communicate(timeout=20)
        # The stand-in keeps the default disposition, so it dies by the signal.
        assert child.returncode == 128 + signum
        assert not list(Path(runner_env["TMPDIR"]).iterdir())
    finally:
        _stop(child, runner_env)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal forwarding")
@pytest.mark.parametrize("signum", POSIX_SIGNALS)
def test_interrupted_pytest_exit_status_is_preserved(
    runner_env: dict[str, str], signum: int
) -> None:
    runner_env["HANDLE"] = "1"
    runner_env["RESULT"] = "3"
    child = _start_waiting(runner_env)
    try:
        child.send_signal(signum)
        child.communicate(timeout=20)
        assert child.returncode == 3
        assert not list(Path(runner_env["TMPDIR"]).iterdir())
    finally:
        _stop(child, runner_env)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
@pytest.mark.parametrize("interrupt", [False, True])
def test_group_members_stop_before_scratch_is_removed(
    runner_env: dict[str, str], interrupt: bool
) -> None:
    runner_env["GRANDCHILD"] = "1"
    runner_env["RESULT"] = "4"
    if interrupt:
        runner_env["HANDLE"] = "1"
        child = _start_waiting(runner_env)
        child.send_signal(signal.SIGTERM)
        child.communicate(timeout=30)
    else:
        child = subprocess.Popen(
            [sys.executable, str(RUNNER), "--", "-q"],
            env=runner_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        child.communicate(timeout=30)
    try:
        grandchild = json.loads(Path(runner_env["RECEIPT"]).read_text())["grandchild"]
        assert child.returncode == 4
        assert not _pid_alive(grandchild)
        assert not list(Path(runner_env["TMPDIR"]).iterdir())
    finally:
        _stop(child, runner_env)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux subreaper")
def test_detached_descendant_stops_before_scratch_is_removed(
    runner_env: dict[str, str], tmp_path: Path
) -> None:
    log = tmp_path / "detached.log"
    runner_env.update({"DETACHED": "1", "DETACHED_LOG": str(log), "RESULT": "3"})
    child = subprocess.Popen(
        [sys.executable, str(RUNNER), "--", "-q"],
        env=runner_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        child.communicate(timeout=30)
        detached = json.loads(Path(runner_env["RECEIPT"]).read_text())["detached"]
        assert child.returncode == 3
        # The runner adopted and stopped it, so it never saw scratch missing.
        assert not _pid_alive(detached)
        assert "missing" not in log.read_text()
        assert not list(Path(runner_env["TMPDIR"]).iterdir())
    finally:
        _stop(child, runner_env)


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="POSIX permissions, non-root"
)
@pytest.mark.parametrize("result", [0, 3])
def test_locked_scratch_is_removed_without_changing_status(
    runner_env: dict[str, str], result: int
) -> None:
    runner_env["LOCK"] = "1"
    runner_env["RESULT"] = str(result)
    completed = subprocess.run(
        [sys.executable, str(RUNNER), "--", "-q"], env=runner_env, capture_output=True
    )
    assert completed.returncode == result, completed.stderr
    assert not list(Path(runner_env["TMPDIR"]).iterdir())


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="POSIX permissions, non-root"
)
def test_cleanup_failure_keeps_status_and_names_retained_path(
    runner_env: dict[str, str],
) -> None:
    parent = Path(runner_env["TMPDIR"])
    runner_env["LOCK_PARENT"] = "1"
    runner_env["RESULT"] = "3"
    try:
        completed = subprocess.run(
            [sys.executable, str(RUNNER), "--", "-q"],
            env=runner_env,
            capture_output=True,
            text=True,
        )
        # The parent is not owned by the run, so it is never chmodded back.
        assert parent.stat().st_mode & 0o777 == 0o500
    finally:
        parent.chmod(0o700)
    (root,) = parent.iterdir()
    assert completed.returncode == 3
    assert f"Could not remove pytest scratch {root}" in completed.stderr
    assert "Traceback" not in completed.stderr


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
