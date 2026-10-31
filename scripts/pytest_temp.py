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
LINUX = sys.platform.startswith("linux")
# Seconds left-over processes get after SIGTERM before SIGKILL, and after
# SIGKILL before the runner gives up and retains the scratch.
GROUP_GRACE = 5.0
PR_SET_CHILD_SUBREAPER = 36


def _become_subreaper() -> bool:
    """On Linux, adopt every orphaned descendant, including ones that left
    pytest's session with setsid, so none can outlive the scratch unseen."""
    if not LINUX:
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        return bool(libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0)
    except (OSError, AttributeError):
        return False


def _processes() -> list[tuple[int, int, int, str]]:
    """(pid, ppid, pgrp, state) for every process visible in /proc."""
    rows = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as handle:
                data = handle.read().decode("ascii", "replace")
        except OSError:
            continue
        fields = data[data.rindex(")") + 2 :].split()
        rows.append((int(name), int(fields[1]), int(fields[2]), fields[0]))
    return rows


def _leftovers(leader: int) -> tuple[bool, list[int]]:
    """Whether pytest's group still has a live member, and our live children.

    Reaps adopted zombies. The exited pytest leader stays unreaped throughout.
    """
    me = os.getpid()
    group_alive = False
    children = []
    for pid, ppid, pgrp, state in _processes():
        if pid == leader:
            continue
        if ppid == me and state in ("Z", "X"):
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            continue
        if state in ("Z", "X"):
            continue
        if pgrp == leader:
            group_alive = True
        if ppid == me:
            children.append(pid)
    return group_alive, children


def _drain(leader: int) -> bool:
    """Stop processes pytest left behind. Returns True when none remain.

    Must run while the exited pytest leader is still unreaped: its zombie keeps
    the process-group id reserved, and pids of our own unreaped children cannot
    be reused, so no signal here can reach an unrelated process.
    """
    if not LINUX:
        # No portable way to list members: signal the reserved group itself.
        # Descendants that called setsid are not tracked on this platform.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(leader, sig)
            except ProcessLookupError:
                return True
            except OSError as exc:
                print(
                    f"Could not signal pytest's process group: {exc}", file=sys.stderr
                )
            time.sleep(0.2)
        return True
    for sig in (signal.SIGTERM, signal.SIGKILL):
        signalled_group = False
        signalled: set[int] = set()
        deadline = time.monotonic() + GROUP_GRACE
        while True:
            group_alive, children = _leftovers(leader)
            if not group_alive and not children:
                return True
            if time.monotonic() >= deadline:
                break
            if group_alive and not signalled_group:
                signalled_group = True
                try:
                    os.killpg(leader, sig)
                except OSError as exc:
                    print(
                        f"Could not signal pytest's process group: {exc}",
                        file=sys.stderr,
                    )
            for pid in children:
                if pid not in signalled:
                    signalled.add(pid)
                    try:
                        os.kill(pid, sig)
                    except OSError:
                        pass
            time.sleep(0.05)
    group_alive, children = _leftovers(leader)
    return not group_alive and not children


def _wait_unreaped(child: subprocess.Popen[bytes]) -> None:
    """Wait for pytest to exit but leave it unreaped, reserving its pid/pgid."""
    os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)


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
    root: Path | None = None
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
    _become_subreaper()
    # Install the handlers before creating scratch, so no signal can leave it behind.
    for sig in handlers:
        signal.signal(sig, forward)
    try:
        root = Path(tempfile.mkdtemp(prefix="pmcp-pytest-", dir=parent))
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
            [sys.executable, "-m", "pytest", *args, f"--basetemp={scratch / 'pytest'}"],
            env=env,
            start_new_session=POSIX,
        )
        if interrupted:
            signal_child(interrupted)
        if POSIX:
            _wait_unreaped(child)
            group_drained = _drain(child.pid)
        result = child.wait()
        status = 128 - result if result < 0 else result
    finally:
        if child is not None and child.returncode is None:
            # An exception escaped before pytest finished: stop it before cleanup.
            signal_child(signal.SIGTERM)
            if POSIX:
                _wait_unreaped(child)
                group_drained = _drain(child.pid)
            child.wait()
        if child is None and interrupted:
            status = 128 + interrupted
        if root is None:
            pass
        elif options.keep_temp:
            print(f"Retained pytest evidence: {root}", file=sys.stderr)
        elif not group_drained:
            print(
                f"Retained pytest scratch {root}: processes pytest started "
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
