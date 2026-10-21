"""Reject quadratic matching on both long lines and repeated short lines.

Current code must finish every fixed-sample experiment before the hard deadline.
The historical negative alone may use the original evidenced deadline path;
the line-copying negative must complete an actual nonlinear-growth result.
Original catastrophic wall and semantic oracles remain in test_linear_matching.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys

import pytest

_CLASSES = [
    "open-line-then-close-line",
    "open-text-line-then-close-line",
    "close-lines-then-open",
    "open-lines-then-close",
    "text-open-lines-then-close",
]


# R1 calibration at16KiB: current <=0.071ms, historical >=61.8ms.
# The floor qualifies only the historical deadline receipt, not current code.
_HISTORICAL_MIN_CPU_S = 0.005
_EXCEPTION_CLASS = re.compile(
    r"^(?:[A-Za-z_][A-Za-z_0-9]*\.)*"
    r"([A-Za-z_][A-Za-z_0-9]*(?:Error|Exception)|Error|Exception|KeyboardInterrupt|SystemExit)(?::|$)"
)


def _stderr_metadata(
    stderr: str | bytes | None, returncode: int | None
) -> dict[str, object]:
    # Startup stderr can contain paths or values. Publish only identity metadata.
    data = stderr.encode() if isinstance(stderr, str) else stderr or b""
    result: dict[str, object] = {
        "returncode": returncode,
        "stderr_bytes": len(data),
        "stderr_sha256": hashlib.sha256(data).hexdigest(),
    }
    lines = data.splitlines()
    if lines and len(lines[-1]) <= 2000:
        match = _EXCEPTION_CLASS.match(lines[-1].decode(errors="replace"))
        if match is not None:
            result["stderr_exception_class"] = match.group(1)
    return result


def _failure_diagnostics(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return {
        "stdout_tail": result.stdout[-4000:],
        **_stderr_metadata(result.stderr, result.returncode),
    }


@pytest.mark.parametrize("mode", ["current", "historical", "line-copy"])
def test_object_matcher_growth_discriminates_the_historical_regex(
    mode: str,
    request: pytest.FixtureRequest,
) -> None:
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-B", str(root / "tests/_matcher_growth_probe.py")]
    if mode != "current":
        command.append("--historical" if mode == "historical" else "--line-copy-mutant")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(root / "src"), str(root)]))
    try:
        result = subprocess.run(
            command, env=env, capture_output=True, text=True, timeout=20, check=False
        )
    except subprocess.TimeoutExpired as failure:
        output = failure.stdout or b""
        text = output.decode() if isinstance(output, bytes) else output
        receipts = [json.loads(line) for line in text.splitlines()]
        diagnostics = {"receipts": receipts, **_stderr_metadata(failure.stderr, None)}
        assert mode == "historical", diagnostics
        assert any(
            receipt.get("phase") == "sample"
            and receipt.get("mode") == "historical"
            and receipt.get("historical_mutant") is True
            and receipt.get("line_copy_mutant") is False
            and receipt.get("input_class") == "open-line-then-close-line"
            and receipt.get("size", 0) >= 16 << 10
            and receipt.get("process_cpu_s", 0) >= _HISTORICAL_MIN_CPU_S
            for receipt in receipts
        ), diagnostics
        request.node.user_properties.append(
            ("matching_path", "historical-completed-sample-deadline")
        )
        return
    negative = mode != "current"
    assert result.returncode == (2 if negative else 0), _failure_diagnostics(result)
    receipts = [json.loads(line) for line in result.stdout.splitlines()]
    report = receipts[-1]
    assert report["phase"] == "result"
    assert report["mode"] == mode
    assert report["verdict"] == ("nonlinear" if negative else "linear"), report
    assert report["historical_mutant"] is (mode == "historical")
    assert report["line_copy_mutant"] is (mode == "line-copy")
    classes = report["classes"]
    names = [experiment["input_class"] for experiment in classes]
    expected = (
        _CLASSES[:1]
        if mode == "historical"
        else _CLASSES[2:]
        if mode == "line-copy"
        else _CLASSES
    )
    assert names == expected
    for experiment in classes:
        rows = experiment["rows"]
        targets = [row["target_size"] for row in rows]
        assert targets == [16 << 10, 64 << 10, 256 << 10][: len(targets)]
        assert 2 <= len(rows) <= 3
        assert all(len(row["samples"]) == 3 for row in rows)
        if negative:
            assert experiment["verdict"] == "nonlinear"
        else:
            assert experiment["verdict"] == "linear"
            assert len(rows) == 3
    request.node.user_properties.append(
        ("matching_path", mode + "-completed-cpu-growth")
    )
