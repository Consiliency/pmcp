"""Exercise scratch ownership using a tiny stand-in pytest subprocess."""

from __future__ import annotations

import json
import os
import select
from pathlib import Path
import signal
import subprocess
import sys
from typing import Any

import pytest


RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "pytest_temp.py"
READY_TIMEOUT = 20.0


def _default_signals() -> None:
    """Give the runner default dispositions even if this session ignores some."""
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_DFL)


def _launch_options(kwargs: dict[str, Any]) -> dict[str, Any]:
    if os.name == "posix":
        kwargs.setdefault("preexec_fn", _default_signals)
    return kwargs


def _run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(args, check=False, **_launch_options(kwargs))


def _popen(args: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
    return subprocess.Popen(args, **_launch_options(kwargs))


def _readline(child: subprocess.Popen[str], timeout: float = READY_TIMEOUT) -> str:
    assert child.stdout is not None
    ready, _, _ = select.select([child.stdout], [], [], timeout)
    if not ready:
        raise AssertionError("runner did not report ready in time")
    return child.stdout.readline().strip()


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
        "for s in handled if os.environ.get('KEEP_DISPOSITION') != '1' else []:\n"
        " if os.environ.get('HANDLE') == '1':\n"
        "  signal.signal(s, lambda *_: sys.exit(result))\n"
        " else:\n"
        "  signal.signal(s, signal.SIG_DFL)\n"
        "if os.environ.get('DETACHED') == '1':\n"
        " d = subprocess.Popen([sys.executable, os.environ['LOGGER'], os.environ['DETACHED_LOG']],"
        " start_new_session=True)\n"
        " record['detached'] = d.pid\n"
        " while not pathlib.Path(os.environ['DETACHED_LOG']).exists():\n"
        "  time.sleep(0.01)\n"
        "if os.environ.get('GRANDCHILD') == '1':\n"
        " g = subprocess.Popen([sys.executable, os.environ['LOGGER'], os.environ['GRANDCHILD_LOG']])\n"
        " record['grandchild'] = g.pid\n"
        " while not pathlib.Path(os.environ['GRANDCHILD_LOG']).exists():\n"
        "  time.sleep(0.01)\n"
        "if os.environ.get('LOCK') == '1':\n"
        " locked = base / 'locked'\n"
        " (locked / 'inner').mkdir(parents=True)\n"
        " (locked / 'inner' / 'file').write_text('x')\n"
        " os.chmod(locked / 'inner', 0)\n"
        " os.chmod(locked, 0o500)\n"
        "if os.environ.get('SYMLINK_TARGET'):\n"
        " ro = base / 'ro'\n"
        " ro.mkdir()\n"
        " (ro / 'via').symlink_to(os.environ['SYMLINK_TARGET'])\n"
        " os.chmod(ro, 0o500)\n"
        "if os.environ.get('ORPHAN') == '1':\n"
        " a = subprocess.Popen([sys.executable, '-c', 'import subprocess, sys;"
        ' p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]);'
        " print(p.pid, flush=True)'], stdout=subprocess.PIPE, text=True)\n"
        " orphan = int(a.stdout.readline())\n"
        " a.wait()\n"
        " os.kill(orphan, signal.SIGKILL)\n"
        " deadline = time.monotonic() + 3\n"
        " gone = False\n"
        " while time.monotonic() < deadline and not gone:\n"
        "  try:\n"
        "   os.kill(orphan, 0)\n"
        "   time.sleep(0.01)\n"
        "  except ProcessLookupError:\n"
        "   gone = True\n"
        " record['orphan_gone'] = gone\n"
        "if os.environ.get('NOSEARCH') == '1':\n"
        " vault = base / 'vault'\n"
        " (vault / 'sub').mkdir(parents=True)\n"
        " (vault / 'sub' / 'file').write_text('x')\n"
        " os.chmod(vault, 0o600)\n"
        "if os.environ.get('DEEP'):\n"
        " d = base\n"
        " for _ in range(int(os.environ['DEEP'])):\n"
        "  d = d / 'd'\n"
        "  os.mkdir(d)\n"
        "if os.environ.get('LOCK_PARENT') == '1':\n"
        " os.chmod(pathlib.Path(os.environ['TMPDIR']).parent.parent, 0o500)\n"
        "pathlib.Path(os.environ['RECEIPT']).write_text(json.dumps(record))\n"
        "if os.environ.get('WAIT') == '1':\n"
        " print('ready', flush=True)\n"
        " time.sleep(float(os.environ.get('WAIT_SECONDS', '60')))\n"
        "sys.exit(result)\n"
    )
    (modules / "logger.py").write_text(
        "import os, pathlib, signal, sys, time\n"
        "log = pathlib.Path(sys.argv[1])\n"
        "def note(text):\n"
        "    with log.open('a') as handle:\n"
        "        handle.write(text + '\\n')\n"
        "def scratch():\n"
        "    return 'ok' if os.path.isdir(os.environ['TMPDIR']) else 'missing'\n"
        "def on_term(*_):\n"
        "    note('term-' + scratch())\n"
        "    os._exit(0)\n"
        "signal.signal(signal.SIGTERM, on_term)\n"
        "while True:\n"
        "    note(scratch())\n"
        "    time.sleep(0.02)\n"
    )
    parent = tmp_path / "scratch-parent"
    parent.mkdir()
    return {
        **os.environ,
        "TMPDIR": str(parent),
        "PYTHONPATH": str(modules),
        "PYTEST_ADDOPTS": "",
        "RECEIPT": str(tmp_path / "receipt.json"),
        "LOGGER": str(modules / "logger.py"),
    }


@pytest.mark.parametrize("result", [0, 3])
def test_success_and_failure_remove_only_owned_scratch(
    runner_env: dict[str, str], result: int
) -> None:
    parent = Path(runner_env["TMPDIR"])
    sentinel = parent / "another-run"
    sentinel.mkdir()
    runner_env["RESULT"] = str(result)
    completed = _run(
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
        result = _run(
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
    result = _run(
        [sys.executable, str(RUNNER), "--", *args], env=runner_env, capture_output=True
    )
    assert result.returncode == 2
    assert not list(Path(runner_env["TMPDIR"]).iterdir())


def _start_waiting(runner_env: dict[str, str]) -> subprocess.Popen[str]:
    runner_env["WAIT"] = "1"
    child = _popen(
        [sys.executable, str(RUNNER), "--", "-q"],
        env=runner_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert _readline(child) == "ready"
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
    log = Path(runner_env["RECEIPT"]).with_name("grandchild.log")
    runner_env.update({"GRANDCHILD": "1", "GRANDCHILD_LOG": str(log), "RESULT": "4"})
    if interrupt:
        runner_env["HANDLE"] = "1"
        child = _start_waiting(runner_env)
    else:
        child = _popen(
            [sys.executable, str(RUNNER), "--", "-q"],
            env=runner_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    try:
        if interrupt:
            child.send_signal(signal.SIGTERM)
        child.communicate(timeout=30)
        grandchild = json.loads(Path(runner_env["RECEIPT"]).read_text())["grandchild"]
        assert child.returncode == 4
        assert not _pid_alive(grandchild)
        # Ordering oracle: its SIGTERM handler saw the scratch still present.
        text = log.read_text()
        assert "term-ok" in text
        assert "missing" not in text
        assert not list(Path(runner_env["TMPDIR"]).iterdir())
    finally:
        _stop(child, runner_env)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux subreaper")
def test_detached_descendant_stops_before_scratch_is_removed(
    runner_env: dict[str, str], tmp_path: Path
) -> None:
    log = tmp_path / "detached.log"
    runner_env.update({"DETACHED": "1", "DETACHED_LOG": str(log), "RESULT": "3"})
    child = _popen(
        [sys.executable, str(RUNNER), "--", "-q"],
        env=runner_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        child.communicate(timeout=30)
        detached = json.loads(Path(runner_env["RECEIPT"]).read_text())["detached"]
        assert child.returncode == 3
        # The runner adopted and stopped it before removing scratch.
        assert not _pid_alive(detached)
        text = log.read_text()
        assert "term-ok" in text
        assert "missing" not in text
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
    completed = _run(
        [sys.executable, str(RUNNER), "--", "-q"], env=runner_env, capture_output=True
    )
    assert completed.returncode == result, completed.stderr
    assert not list(Path(runner_env["TMPDIR"]).iterdir())


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="POSIX permissions, non-root"
)
def test_symlink_in_read_only_dir_is_removed_not_followed(
    runner_env: dict[str, str], tmp_path: Path
) -> None:
    target = tmp_path / "outside.txt"
    target.write_text("keep")
    target.chmod(0o600)
    runner_env["SYMLINK_TARGET"] = str(target)
    runner_env["RESULT"] = "3"
    completed = _run(
        [sys.executable, str(RUNNER), "--", "-q"], env=runner_env, capture_output=True
    )
    assert completed.returncode == 3, completed.stderr
    assert not list(Path(runner_env["TMPDIR"]).iterdir())
    assert target.read_text() == "keep"
    assert target.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="POSIX permissions, non-root"
)
def test_directory_without_search_permission_is_removed(
    runner_env: dict[str, str],
) -> None:
    runner_env.update({"NOSEARCH": "1", "RESULT": "3"})
    completed = _run(
        [sys.executable, str(RUNNER), "--", "-q"], env=runner_env, capture_output=True
    )
    assert completed.returncode == 3, completed.stderr
    assert not list(Path(runner_env["TMPDIR"]).iterdir())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="path length limits")
def test_very_deep_scratch_keeps_status_without_traceback(
    runner_env: dict[str, str],
) -> None:
    runner_env.update({"DEEP": "1100", "RESULT": "3"})
    try:
        completed = _run(
            [sys.executable, str(RUNNER), "--", "-q"],
            env=runner_env,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 3
        assert "Traceback" not in completed.stderr
        leftovers = list(Path(runner_env["TMPDIR"]).iterdir())
        # Removed, or retained with a notice naming it (3.10's recursive rmtree).
        assert not leftovers or (
            f"Could not remove pytest scratch {leftovers[0]}" in completed.stderr
        )
    finally:
        # Never leave a tree that recursive cleanup in later sessions cannot remove.
        for leftover in list(Path(runner_env["TMPDIR"]).iterdir()):
            _deep_remove(leftover)


def _deep_remove(path: Path) -> None:
    """Remove a tree too deep for recursive helpers on older Pythons."""
    stack = [str(path)]
    while stack:
        current = stack[-1]
        with os.scandir(current) as entries:
            subdirs = []
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    subdirs.append(entry.path)
                else:
                    os.unlink(entry.path)
        if subdirs:
            stack.extend(subdirs)
        else:
            os.rmdir(stack.pop())


@pytest.mark.skipif(os.name != "posix", reason="POSIX nohup semantics")
def test_inherited_sighup_ignore_is_kept(runner_env: dict[str, str]) -> None:
    runner_env.update({"KEEP_DISPOSITION": "1", "WAIT_SECONDS": "1"})
    runner_env["WAIT"] = "1"

    def ignore_hangup() -> None:
        _default_signals()
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

    child = _popen(
        [sys.executable, str(RUNNER), "--", "-q"],
        env=runner_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=ignore_hangup,
    )
    try:
        assert _readline(child) == "ready"
        child.send_signal(signal.SIGHUP)
        child.communicate(timeout=20)
        assert child.returncode == 0
        assert not list(Path(runner_env["TMPDIR"]).iterdir())
    finally:
        _stop(child, runner_env)


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
        completed = _run(
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
    result = _run(
        [sys.executable, str(RUNNER), "--", "--", "-q"],
        env=runner_env,
        capture_output=True,
    )
    assert result.returncode == 2
    assert not list(Path(runner_env["TMPDIR"]).iterdir())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux subreaper")
def test_adopted_orphan_is_reaped_while_pytest_runs(
    runner_env: dict[str, str],
) -> None:
    """A killed orphan must not linger as a zombie that still looks alive."""
    runner_env["ORPHAN"] = "1"
    completed = _run(
        [sys.executable, str(RUNNER), "--", "-q"], env=runner_env, capture_output=True
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(Path(runner_env["RECEIPT"]).read_text())["orphan_gone"] is True


def _run_patched(
    runner_env: dict[str, str], patch: str
) -> subprocess.CompletedProcess[str]:
    """Run the runner's main() with one module global replaced."""
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('pytest_temp', {str(RUNNER)!r})\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        f"{patch}\n"
        f"sys.argv = [{str(RUNNER)!r}, '--', '-q']\n"
        "sys.exit(m.main())\n"
    )
    return _run(
        [sys.executable, "-c", code], env=runner_env, capture_output=True, text=True
    )


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux subreaper")
def test_failed_subreaper_retains_scratch_and_keeps_status(
    runner_env: dict[str, str],
) -> None:
    runner_env["RESULT"] = "3"
    completed = _run_patched(runner_env, "m._become_subreaper = lambda: False")
    (root,) = Path(runner_env["TMPDIR"]).iterdir()
    assert completed.returncode == 3
    assert "could not become a child subreaper" in completed.stderr
    assert f"Retained pytest scratch {root}" in completed.stderr
    assert "Traceback" not in completed.stderr


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc drain")
def test_drain_failure_retains_scratch_and_keeps_status(
    runner_env: dict[str, str],
) -> None:
    runner_env["RESULT"] = "3"
    completed = _run_patched(
        runner_env,
        "def broken(*_):\n"
        "    raise PermissionError('synthetic /proc denial')\n"
        "m._drain_adopted = broken",
    )
    (root,) = Path(runner_env["TMPDIR"]).iterdir()
    assert completed.returncode == 3
    assert f"Retained pytest scratch {root}" in completed.stderr
    assert "synthetic /proc denial" in completed.stderr
    assert "Traceback" not in completed.stderr


@pytest.mark.skipif(os.name != "posix", reason="POSIX SIGCHLD semantics")
@pytest.mark.parametrize("result", [0, 3])
def test_inherited_sigchld_ignore_keeps_pytest_status(
    runner_env: dict[str, str], result: int
) -> None:
    """With SIGCHLD ignored the kernel discards statuses; a failure must not pass."""
    runner_env["RESULT"] = str(result)

    def ignore_sigchld() -> None:
        _default_signals()
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)

    completed = _run(
        [sys.executable, str(RUNNER), "--", "-q"],
        env=runner_env,
        capture_output=True,
        preexec_fn=ignore_sigchld,
    )
    assert completed.returncode == result, completed.stderr
    assert not list(Path(runner_env["TMPDIR"]).iterdir())
