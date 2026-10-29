#!/usr/bin/env python3
"""Run pytest in this interpreter with unique, explicitly owned scratch space."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile


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
        "/var/tmp" if os.name == "posix" else tempfile.gettempdir()
    )
    root = Path(tempfile.mkdtemp(prefix="pmcp-pytest-", dir=parent))
    child: subprocess.Popen[bytes] | None = None
    interrupted = 0

    def forward(signum: int, _frame: object) -> None:
        nonlocal interrupted
        interrupted = signum
        if child is not None and child.poll() is None:
            try:
                child.send_signal(signum)
            except OSError as exc:
                # Do not delete a directory while a child may still be using it.
                print(f"Could not forward signal to pytest: {exc}", file=sys.stderr)

    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
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
            start_new_session=os.name == "posix",
        )
        if interrupted:
            forward(interrupted, None)
        result = child.wait()
        return (
            128 + interrupted
            if interrupted
            else (128 - result if result < 0 else result)
        )
    finally:
        # Wait for the runner before removing its working files, even on exceptions.
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if options.keep_temp:
            print(f"Retained pytest evidence: {root}", file=sys.stderr)
        else:
            shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())
