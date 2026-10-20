"""Consiliency/pmcp#444: a real-work oracle that rejects the old regex.

Both implementations run in fresh bounded children, with fixed samples and
the same CPU-growth rule. No retry, fastest-sample selection or changed wall
budget in test_linear_matching is involved. The historical mutant must be
rejected by the growth rule, rather than merely timing out during import.
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
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == (2 if historical else 0), result.stdout[-4000:]
    report = json.loads(result.stdout)
    assert report["verdict"] == ("nonlinear" if historical else "linear"), report
    assert report["historical_mutant"] is historical
    assert len(report["rows"]) >= 2
    assert all(len(row["samples"]) == 3 for row in report["rows"])
