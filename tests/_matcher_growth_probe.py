"""Fixed CPU-growth experiments on the real object-matching path.

Every input contains both delimiters without a same-line pair, so neither the
missing-brace fast path nor a successful early match can hide scanning work.
The historical regex and a line-copying scanner are isolated negative controls.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
from collections.abc import Callable

from pmcp import auth

SIZES = (16 << 10, 64 << 10, 256 << 10)
SAMPLES = 3
GROWTH_LIMIT = 8
CPU_NOISE_S = 0.001
INPUTS: tuple[tuple[str, Callable[[int], str]], ...] = (
    ("open-line-then-close-line", lambda size: "{" * size + "\n}"),
    ("open-text-line-then-close-line", lambda size: "{a" * (size // 2) + "\n}"),
    ("close-lines-then-open", lambda size: "}\n" * (size // 2) + "{"),
    ("open-lines-then-close", lambda size: "{\n" * (size // 2) + "}"),
    ("text-open-lines-then-close", lambda size: "a{\n" * (size // 3) + "}"),
)


def _line_copy_mutant(text: str) -> str | None:
    """Same matching semantics; repeated remainder copies are quadratic."""
    if "{" not in text or "}" not in text:
        return None
    remaining = text
    while remaining:
        line, _, remaining = remaining.partition("\n")
        start, end = line.find("{"), line.rfind("}")
        if start >= 0 and end > start:
            return line[start : end + 1]
    return None


def main() -> int:
    modes = {
        (): "current",
        ("--historical",): "historical",
        ("--line-copy-mutant",): "line-copy",
    }
    mode = modes[tuple(sys.argv[1:])]
    historical = mode == "historical"
    if historical:
        auth._EMBEDDED_OBJECT_RE = re.compile(r"(\{.*\})")
    elif mode == "line-copy":
        auth._embedded_object_text = _line_copy_mutant
    experiments = []
    # The historical regex is discriminated by the original long-line class;
    # line-copying needs repeated lines. Current code must pass every class.
    inputs = INPUTS[:1] if historical else INPUTS[2:] if mode == "line-copy" else INPUTS
    for input_class, build in inputs:
        rows = []
        previous = None
        for size in SIZES:
            text = build(size)
            assert "{" in text and "}" in text
            identity = {
                "mode": mode,
                "historical_mutant": historical,
                "line_copy_mutant": mode == "line-copy",
                "input_class": input_class,
                "size": len(text),
                "target_size": size,
            }
            print(json.dumps({"phase": "matching", **identity}), flush=True)
            samples = []
            for _ in range(SAMPLES):
                wall_start = time.perf_counter()
                cpu_start = time.process_time()
                found = auth._embedded_object_text(text)
                cpu = time.process_time() - cpu_start
                wall = time.perf_counter() - wall_start
                assert found is None
                samples.append({"wall_s": wall, "process_cpu_s": cpu})
                # Only a completed semantic-checked matcher call emits sample.
                print(
                    json.dumps({"phase": "sample", **identity, **samples[-1]}),
                    flush=True,
                )
            median = statistics.median(sample["process_cpu_s"] for sample in samples)
            rows.append(
                {
                    "size": len(text),
                    "target_size": size,
                    "samples": samples,
                    "median_cpu_s": median,
                }
            )
            if previous is not None and median > previous * GROWTH_LIMIT + CPU_NOISE_S:
                verdict = "nonlinear"
                break
            previous = median
        else:
            verdict = "linear"
        experiments.append(
            {"input_class": input_class, "verdict": verdict, "rows": rows}
        )
        if verdict == "nonlinear" and mode != "line-copy":
            break
    verdict = (
        "nonlinear"
        if any(row["verdict"] == "nonlinear" for row in experiments)
        else "linear"
    )
    print(
        json.dumps(
            {
                "phase": "result",
                "mode": mode,
                "historical_mutant": historical,
                "line_copy_mutant": mode == "line-copy",
                "worker": os.environ.get("PYTEST_XDIST_WORKER", "controller"),
                "verdict": verdict,
                "classes": experiments,
            }
        ),
        flush=True,
    )
    return 2 if verdict == "nonlinear" else 0


if __name__ == "__main__":
    raise SystemExit(main())
