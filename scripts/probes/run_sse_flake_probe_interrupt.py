"""Multi-trial driver for `sse_flake_probe_interrupt.py` -- see that module's
docstring for the hypothesis under test. One fresh subprocess per trial for
the same reason as `run_sse_flake_probe.py` (the `AppStatus.should_exit`
process-global latch).

Usage:
    uv run python scripts/probes/run_sse_flake_probe_interrupt.py \
        --trials 50 --races 30 --jobs 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROBE = Path(__file__).with_name("sse_flake_probe_interrupt.py")


async def _run_one(trial_id: int, races: int, min_delay: float, max_delay: float, sem: asyncio.Semaphore) -> dict:
    async with sem:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(PROBE),
            "--races", str(races),
            "--min-delay-ms", str(min_delay),
            "--max-delay-ms", str(max_delay),
            "--trial-id", str(trial_id),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return {"trial_id": trial_id, "crashed": True, "returncode": proc.returncode,
                "stderr_tail": stderr.decode(errors="replace")[-3000:]}
    lines = stdout.decode(errors="replace").strip().splitlines()
    if not lines:
        return {"trial_id": trial_id, "crashed": True, "returncode": 0,
                "stderr_tail": stderr.decode(errors="replace")[-3000:], "note": "no stdout"}
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"trial_id": trial_id, "crashed": True, "returncode": 0,
                "stdout_tail": lines[-1][-1000:], "note": "not JSON"}


async def main_async(args: argparse.Namespace) -> int:
    sem = asyncio.Semaphore(args.jobs)
    start = time.monotonic()
    tasks = [
        asyncio.create_task(_run_one(i, args.races, args.min_delay_ms, args.max_delay_ms, sem))
        for i in range(args.trials)
    ]
    results = []
    for done in asyncio.as_completed(tasks):
        r = await done
        results.append(r)
        tag = "HIT" if r.get("target_hits") else ("SIBLING" if r.get("sibling_hits") else ("CRASH" if r.get("crashed") else "clean"))
        other_n = len(r.get("other") or [])
        print(f"[{len(results)}/{args.trials}] trial={r.get('trial_id')} {tag} other_errs={other_n} "
              f"elapsed={r.get('elapsed_s', 0):.2f}s total_wall={time.monotonic()-start:.1f}s", flush=True)

    hits = [r for r in results if r.get("target_hits")]
    crashed = [r for r in results if r.get("crashed")]
    total_races = args.trials * args.races
    print()
    print(f"=== {args.trials} trials x {args.races} races = {total_races} race attempts, "
          f"delay=[{args.min_delay_ms},{args.max_delay_ms}]ms jobs={args.jobs} "
          f"wall={time.monotonic()-start:.1f}s ===")
    print(f"TARGET hits: {len(hits)}/{args.trials} trials")
    print(f"probe crashes: {len(crashed)}/{args.trials}")
    if hits:
        print("--- sample hits ---")
        for r in hits[:5]:
            print(f"trial {r['trial_id']}: {r['target_hits'][0]}")
    if crashed:
        for r in crashed[:3]:
            print(f"crash trial {r['trial_id']}: {r.get('stderr_tail','')[-600:]}")
    print(json.dumps({
        "trials": args.trials, "races": args.races, "total_race_attempts": total_races,
        "target_hit_trials": len(hits), "crash_trials": len(crashed),
        "elapsed_s": time.monotonic() - start,
    }))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trials", type=int, default=20)
    p.add_argument("--races", type=int, default=30)
    p.add_argument("--min-delay-ms", type=float, default=0.0)
    p.add_argument("--max-delay-ms", type=float, default=15.0)
    p.add_argument("--jobs", type=int, default=8)
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
