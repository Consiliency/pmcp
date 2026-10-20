"""Consiliency/pmcp#444: a real-work oracle that rejects the old regex.

Both implementations run in fresh bounded children, with fixed samples and
the same CPU-growth rule. No retry, fastest-sample selection or changed wall
budget in test_linear_matching is involved. The historical mutant must be
rejected by the growth rule or its hard deadline with proof it entered the
real matching path. An import/startup timeout cannot qualify as a rejection.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("historical", [False, True], ids=["current", "historical"])
def test_object_matcher_growth_discriminates_the_historical_regex(
    historical: bool,
) -> None:
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-B", str(root / "tests/_matcher_growth_probe.py")]
    if historical:
        command.append("--historical")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root / "src"), str(root)]))
    try:
        result = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=20, check=False
        )
    except subprocess.TimeoutExpired as failure:
        # A bad matcher may consume the bounded child's whole wall budget.
        # Keep the positive implementation strict and refuse startup failures.
        output = failure.stdout or b""
        text = output.decode() if isinstance(output, bytes) else output
        receipts = [json.loads(line) for line in text.splitlines()]
        assert historical, receipts
        assert any(
            receipt.get("phase") == "sample"
            and receipt.get("historical_mutant") is True
            and receipt.get("input_class") == "open-line-then-close-line"
            and receipt.get("size", 0) >= 16 << 10
            and receipt.get("process_cpu_s", 0) > 0
            for receipt in receipts
        ), receipts
        return
    assert result.returncode == (2 if historical else 0), result.stdout[-4000:]
    receipts = [json.loads(line) for line in result.stdout.splitlines()]
    report = receipts[-1]
    assert report["phase"] == "result"
    assert report["verdict"] == ("nonlinear" if historical else "linear"), report
    assert report["historical_mutant"] is historical
    assert len(report["rows"]) >= 2
    assert all(len(row["samples"]) == 3 for row in report["rows"])
