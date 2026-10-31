#!/usr/bin/env python3
"""Run pytest in this interpreter with unique, explicitly owned scratch space."""

from __future__ import annotations

import argparse
import errno
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
# Unreaped waits keep pytest's pid and process-group id reserved while
# left-over processes are signalled (os.waitid reached macOS in 3.13).
HAVE_WAITID = hasattr(os, "waitid") and hasattr(os, "WNOWAIT")
# Seconds left-over processes get after SIGTERM before SIGKILL, and after
# SIGKILL before the runner gives up and retains the scratch.
GROUP_GRACE = 5.0
POLL = 0.01
PR_SET_CHILD_SUBREAPER = 36
EXITED_CODES = {
    getattr(os, name)
    for name in ("CLD_EXITED", "CLD_KILLED", "CLD_DUMPED")
    if hasattr(os, name)
}


def _become_subreaper() -> bool:
    """On Linux, adopt every orphaned descendant, including ones that left
    pytest's session with setsid, so none can outlive the scratch unseen."""
    if not LINUX:
        return False
    try:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        return bool(libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0)
    except (ImportError, OSError, AttributeError):
        return False


def _status(result: int) -> int:
    return 128 - result if result < 0 else result


def _live(predicate: Callable[[int, int, int], bool]) -> list[int]:
    """Pids of live (non-zombie) processes in /proc matching (pid, ppid, pgrp)."""
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as handle:
                data = handle.read().decode("ascii", "replace")
        except OSError:
            continue
        fields = data[data.rindex(")") + 2 :].split()
        pid, state = int(name), fields[0]
        if state not in ("Z", "X") and predicate(pid, int(fields[1]), int(fields[2])):
            found.append(pid)
    return found


def _await_exit_unreaped(pid: int, adopting: bool) -> None:
    """Wait for pytest to exit without reaping it.

    While waiting, reap adopted orphans as soon as they exit, so a killed test
    process does not linger as a zombie that still looks alive to the tests.
    """
    # getattr: only reached when HAVE_WAITID (absent on macOS before 3.13).
    waitid: Callable[..., Any] = getattr(os, "waitid")
    peek = os.WEXITED | os.WNOHANG | getattr(os, "WNOWAIT")
    while True:
        if adopting:
            while True:
                info = waitid(getattr(os, "P_ALL"), 0, peek)
                if info is None or info.si_code not in EXITED_CODES:
                    break
                if info.si_pid == pid:
                    return
                try:
                    os.waitpid(info.si_pid, 0)
                except ChildProcessError:
                    pass
        else:
            info = waitid(getattr(os, "P_PID"), pid, peek)
            # macOS reports a stopped child even with WEXITED: exits only.
            if info is not None and info.si_code in EXITED_CODES:
                return
        time.sleep(POLL)


def _drain_group(pgid: int) -> bool:
    """Stop members of pytest's process group. Returns True when none remain.

    Run while the exited leader is unreaped (HAVE_WAITID), its zombie keeps the
    group id reserved, so no signal can reach an unrelated group. Membership
    can only be observed on Linux; elsewhere the group is signalled blindly.
    """

    def alive() -> bool:
        return bool(_live(lambda pid, _ppid, pgrp: pgrp == pgid and pid != pgid))

    for sig in (signal.SIGTERM, signal.SIGKILL):
        if LINUX and not alive():
            return True
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        deadline = time.monotonic() + (GROUP_GRACE if LINUX else 0.2)
        while time.monotonic() < deadline:
            if LINUX and not alive():
                return True
            time.sleep(POLL)
    return not alive() if LINUX else True


def _drain_adopted() -> bool:
    """Stop every remaining descendant. Returns True once none remain.

    The runner is a subreaper and pytest is already reaped, so every live
    descendant is a child of the runner or below one. The kernel reporting no
    children at all (ECHILD) is therefore the proof; /proc is used only to
    find children to signal, and pids of unreaped children cannot be reused.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        signalled: set[int] = set()
        deadline = time.monotonic() + GROUP_GRACE
        while True:
            try:
                while os.waitpid(-1, os.WNOHANG)[0]:
                    pass
            except ChildProcessError:
                return True
            if time.monotonic() >= deadline:
                break
            me = os.getpid()
            for pid in _live(lambda _pid, ppid, _pgrp: ppid == me):
                if pid not in signalled:
                    signalled.add(pid)
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
            time.sleep(POLL)
    return False


def _finish(child: subprocess.Popen[bytes], adopting: bool) -> tuple[int, str]:
    """Wait for pytest, stop what it left behind; return (status, retain reason)."""
    if not POSIX:
        return _status(child.wait()), ""  # Windows: pytest itself only
    if HAVE_WAITID:
        _await_exit_unreaped(child.pid, adopting)
        if adopting:
            # Reserved group id: one TERM to its members, then confirm below.
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            group_done = True
        else:
            group_done = _drain_group(child.pid)
        status = _status(child.wait())
    else:
        # No unreaped wait (macOS before 3.13): once pytest is reaped its group
        # id could be reused, so only check for it (signal 0) and never signal.
        status = _status(child.wait())
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            group_done = True
        except OSError:
            group_done = False
        else:
            group_done = False
    if adopting:
        if not _drain_adopted():
            return status, "processes pytest started did not exit"
        return status, ""
    if LINUX:
        return (
            status,
            "could not become a child subreaper, so detached descendants cannot be confirmed stopped",
        )
    if not group_done:
        return status, "processes in pytest's group may still be running"
    return status, ""


def _finish_safely(
    child: subprocess.Popen[bytes], adopting: bool, stop: Callable[[], None]
) -> tuple[int, str]:
    try:
        return _finish(child, adopting)
    except Exception as exc:
        reason = (
            f"could not confirm pytest's processes exited ({type(exc).__name__}: {exc})"
        )
        if child.returncode is None and child.poll() is None:
            stop()
        return _status(child.wait()), reason


def _remove_owned(root: Path) -> None:
    """Remove the owned scratch, restoring owner permissions tests may have dropped.

    Only directories inside the owned root are chmodded; symlinks are removed,
    never followed or chmodded. A failure that remains is raised.
    """

    owned = os.path.abspath(root)
    granted: set[str] = set()

    def grant(target: str) -> None:
        target = os.path.abspath(target)
        if target in granted:
            return  # granting again cannot help; let the caller's error stand
        if os.path.commonpath([owned, target]) != owned or os.path.islink(target):
            raise PermissionError(f"not owned scratch: {target}")
        granted.add(target)
        mode = stat.S_IMODE(os.lstat(target).st_mode) | stat.S_IRWXU
        try:
            os.chmod(target, mode, follow_symlinks=False)
        except NotImplementedError as exc:
            # Linux raises this only when the target is a symlink: refuse.
            raise PermissionError(f"refusing to chmod a symlink: {target}") from exc
        except OSError as exc:
            if exc.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
                raise PermissionError(f"refusing to chmod: {target}") from exc
            raise

    def retry(func: Callable[..., Any], path: str, error: Any) -> None:
        exc = error[1] if isinstance(error, tuple) else error
        if isinstance(exc, FileNotFoundError):
            return  # already removed, e.g. by a recursive retry below
        if not isinstance(exc, PermissionError):
            raise exc
        if os.path.abspath(path) != owned:
            # Removing or listing an entry needs write and search permission
            # on its parent, which is always an owned directory here.
            grant(os.path.dirname(path))
        if func in (os.unlink, os.remove, os.rmdir):
            try:
                func(path)  # unlink never follows a symlink
            except FileNotFoundError:
                pass
        elif os.path.islink(path) or not os.path.isdir(path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        elif os.path.abspath(path) in granted:
            raise exc  # already granted once: do not recurse again
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

    def stop_pytest() -> None:
        signal_child(signal.SIGKILL if POSIX else signal.SIGTERM)

    signums = [signal.SIGINT, signal.SIGTERM]
    if POSIX:
        signums.append(signal.SIGHUP)
    # Leave an inherited SIG_IGN alone (nohup, background jobs): pytest inherits
    # it too, so the run keeps the protection its caller asked for.
    handlers = {
        sig: handler
        for sig in signums
        if (handler := signal.getsignal(sig)) is not signal.SIG_IGN
    }
    adopting = _become_subreaper()
    if LINUX and not adopting:
        print(
            "pytest_temp: could not become a child subreaper; scratch will be "
            "retained because detached descendants cannot be confirmed stopped",
            file=sys.stderr,
        )
    retain_reason = ""
    # An inherited SIGCHLD ignore makes the kernel discard exit statuses, so a
    # failing pytest would look like success; use the default while running.
    if POSIX:
        handlers.setdefault(signal.SIGCHLD, signal.getsignal(signal.SIGCHLD))
    # Install the handlers before creating scratch, so no signal can leave it behind.
    for sig in handlers:
        signal.signal(
            sig, signal.SIG_DFL if sig == getattr(signal, "SIGCHLD", None) else forward
        )
    try:
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
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    *args,
                    f"--basetemp={scratch / 'pytest'}",
                ],
                env=env,
                start_new_session=POSIX,
            )
            if interrupted:
                signal_child(interrupted)
            status, retain_reason = _finish_safely(child, adopting, stop_pytest)
        finally:
            if child is not None and child.returncode is None:
                # An exception escaped before pytest finished: stop it first.
                stop_pytest()
                status, retain_reason = _finish_safely(child, adopting, stop_pytest)
                retain_reason = (
                    retain_reason or "the runner failed before pytest finished"
                )
            if root is None:
                pass
            elif options.keep_temp:
                note = f" (also: {retain_reason})" if retain_reason else ""
                print(f"Retained pytest evidence: {root}{note}", file=sys.stderr)
            elif retain_reason:
                print(
                    f"Retained pytest scratch {root}: {retain_reason}", file=sys.stderr
                )
            else:
                try:
                    _remove_owned(root)
                except Exception as exc:  # e.g. RecursionError on a very deep tree
                    print(
                        f"Could not remove pytest scratch {root}: "
                        f"{type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
    finally:
        # Restore last, so a second interrupt cannot abort the shutdown midway.
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
