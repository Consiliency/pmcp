"""Bounded child-process CPU experiment for the real object-matching path.

The input contains BOTH delimiters, on different lines, so the missing-brace
fast path cannot hide regex work. The historical unanchored expression does
quadratic work before the newline; the current anchored matcher is linear.
No Python tracing is used to infer work done inside the C regex engine.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time

from pmcp import auth

# Predeclared: three samples per size; median process CPU, 4x input growth.
# Linear allows 8x CPU plus 1ms quantisation/noise. Quadratic predicts 16x.
SIZES = (16 << 10, 64 << 10, 256 << 10)
SAMPLES = 3
GROWTH_LIMIT = 8
CPU_NOISE_S = 0.001


def main() -> int:
    historical = sys.argv[1:] == ["--historical"]
    assert not sys.argv[1:] or historical
    if historical:
        auth._EMBEDDED_OBJECT_RE = re.compile(r"(\{.*\})")
    rows = []
    previous = None
    for size in SIZES:
        text = "{" * size + "\n}"
        samples = []
        for _ in range(SAMPLES):
            wall_start = time.perf_counter()
            cpu_start = time.process_time()
            found = auth._embedded_object_text(text)
            cpu = time.process_time() - cpu_start
            wall = time.perf_counter() - wall_start
            assert found is None
            samples.append({"wall_s": wall, "process_cpu_s": cpu})
        median = statistics.median(sample["process_cpu_s"] for sample in samples)
        rows.append({"size": len(text), "samples": samples, "median_cpu_s": median})
        if previous is not None and median > previous * GROWTH_LIMIT + CPU_NOISE_S:
            verdict = "nonlinear"
            break
        previous = median
    else:
        verdict = "linear"
    print(
        json.dumps(
            {
                "verdict": verdict,
                "historical_mutant": historical,
                "input_class": "open-line-then-close-line",
                "worker": os.environ.get("PYTEST_XDIST_WORKER", "controller"),
                "samples_per_size": SAMPLES,
                "growth_limit": GROWTH_LIMIT,
                "cpu_noise_s": CPU_NOISE_S,
                "rows": rows,
            }
        ),
        flush=True,
    )
    return 0 if verdict == "linear" else 2


if __name__ == "__main__":
    raise SystemExit(main())
