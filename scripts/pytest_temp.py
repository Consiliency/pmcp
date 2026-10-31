#!/usr/bin/env python3
"""Run pytest in this interpreter with unique, explicitly owned scratch space."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

POSIX = os.name == "posix"
# Seconds a surviving process-group member gets after SIGTERM before SIGKILL,
# and after SIGKILL before the runner gives up and retains the scratch.
GROUP_GRACE = 5.0


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _drain_group(pgid: int) -> bool:
    """Stop members of pytest's process group left after pytest itself exits.

    POSIX does not reuse a process-group id while any member is alive, so a
    signal here reaches only pytest's own descendants that stayed in its group.
    Returns True when no member remains.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not _group_alive(pgid):
            return True
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        except OSError as exc:
            print(f"Could not signal pytest's process group: {exc}", file=sys.stderr)
        deadline = time.monotonic() + GROUP_GRACE
        while time.monotonic() < deadline:
            if not _group_alive(pgid):
                return True
            time.sleep(0.05)
    return not _group_alive(pgid)


def _remove_owned(root: Path) -> None:
    """Remove the owned scratch, restoring owner permissions tests may have dropped.

    Symlinks are never followed or chmodded; a failure that remains is raised.
    """

    owned = os.path.abspath(root)

    def grant(target: str) -> None:
        target = os.path.abspath(target)
        if os.path.commonpath([owned, target]) != owned or os.path.islink(target):
            raise PermissionError(f"not owned scratch: {target}")
        os.chmod(target, stat.S_IMODE(os.lstat(target).st_mode) | stat.S_IRWXU)

    def retry(func: Callable[..., Any], path: str, error: Any) -> None:
        exc = error[1] if isinstance(error, tuple) else error
        if not isinstance(exc, PermissionError) or os.path.islink(path):
            raise exc
        if func in (os.unlink, os.remove, os.rmdir):
            # Removing an entry needs write and search permission on its parent.
            grant(os.path.dirname(path))
            func(path)
        else:
            # The directory itself could not be listed or opened.
            grant(path)
            _rmtree(path, retry)

    _rmtree(str(root), retry)


def _rmtree(path: str, handler: Callable[..., None]) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=handler)
    else:
        shutil.rmtree(path, onerror=handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="retain scratch as required run evidence",
    )
    parser.add_argument(
        "pytest_args", nargs=argparse.REMAINDER, help="pytest arguments after --"
    )
    options = parser.parse_args()
    args = options.pytest_args
    if args[:1] == ["--"]:
        args = args[1:]
    if "--" in args:
        parser.error(
            "use ./ prefixes for literal test paths; a second -- hides the owned --basetemp"
        )
    try:
        configured_args = shlex.split(os.environ.get("PYTEST_ADDOPTS", ""))
    except ValueError as exc:
        parser.error(str(exc))
    if any(
        arg == "--basetemp" or arg.startswith("--basetemp=")
        for arg in args + configured_args
    ):
        parser.error("the runner owns --basetemp; remove the fixed path")

    parent = os.environ.get("TMPDIR") or (
        "/var/tmp" if POSIX else tempfile.gettempdir()
    )
    root = Path(tempfile.mkdtemp(prefix="pmcp-pytest-", dir=parent))
    child: subprocess.Popen[bytes] | None = None
    interrupted = 0
    status = 1

    def signal_child(signum: int) -> None:
        if child is None or child.returncode is not None:
            return
        try:
            if POSIX:
                # start_new_session made pytest a group leader: reach its workers too.
                os.killpg(child.pid, signum)
            else:
                # Windows Popen.send_signal accepts only SIGTERM and CTRL events.
                child.terminate()
        except ProcessLookupError:
            pass
        except (OSError, ValueError) as exc:
            print(f"Could not forward signal to pytest: {exc}", file=sys.stderr)

    def forward(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = signum
        signal_child(signum)

    signums = [signal.SIGINT, signal.SIGTERM]
    if POSIX:
        signums.append(signal.SIGHUP)
    handlers = {sig: signal.getsignal(sig) for sig in signums}
    group_drained = True
    try:
        for sig in handlers:
            signal.signal(sig, forward)
        scratch = root / "tmp"
        scratch.mkdir()
        env = {
            **os.environ,
            "TMPDIR": str(scratch),
            "TEMP": str(scratch),
            "TMP": str(scratch),
        }
        print(f"pytest scratch: {root}", file=sys.stderr, flush=True)
        child = subprocess.Popen(
            [sys.executable, "-m", "pytest", *args, f"--basetemp={root / 'pytest'}"],
            env=env,
            start_new_session=POSIX,
        )
        if interrupted:
            signal_child(interrupted)
        result = child.wait()
        status = 128 - result if result < 0 else result
        if POSIX:
            group_drained = _drain_group(child.pid)
    finally:
        if child is not None and child.returncode is None:
            # An exception escaped before pytest finished: stop it before cleanup.
            signal_child(signal.SIGTERM)
            child.wait()
            if POSIX:
                group_drained = _drain_group(child.pid)
        if child is None and interrupted:
            status = 128 + interrupted
        if options.keep_temp:
            print(f"Retained pytest evidence: {root}", file=sys.stderr)
        elif not group_drained:
            print(
                f"Retained pytest scratch {root}: processes in pytest's group "
                "did not exit",
                file=sys.stderr,
            )
        else:
            try:
                _remove_owned(root)
            except OSError as exc:
                print(f"Could not remove pytest scratch {root}: {exc}", file=sys.stderr)
        # Restore last, so a second interrupt cannot abort the removal midway.
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
