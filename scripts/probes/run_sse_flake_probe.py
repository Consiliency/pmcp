"""Multi-trial driver for `sse_flake_probe.py`.

Spawns one FRESH subprocess per trial (see that module's docstring for why:
`sse_starlette.sse.AppStatus.should_exit` is a process-global latch, so
looping trials inside one process would just rediscover the already-fixed
bug rather than probe the open one), aggregates results, and reports the
observed failure rate as `hits/trials`.

Usage:
    uv run python scripts/probes/run_sse_flake_probe.py \
        --trials 40 --servers 8 --cycles 5 --calls-per-cycle 20 --jobs 4

Investigation note: .consiliency/notes/sse-stream-ended-investigation-20260812.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROBE = Path(__file__).with_name("sse_flake_probe.py")


async def _run_one(
    trial_id: int, servers: int, cycles: int, calls_per_cycle: int, sem: asyncio.Semaphore
) -> dict:
    async with sem:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(PROBE),
            "--servers",
            str(servers),
            "--cycles",
            str(cycles),
            "--calls-per-cycle",
            str(calls_per_cycle),
            "--trial-id",
            str(trial_id),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return {
            "trial_id": trial_id,
            "crashed": True,
            "returncode": proc.returncode,
            "stderr_tail": stderr.decode(errors="replace")[-4000:],
        }

    last_line = stdout.decode(errors="replace").strip().splitlines()
    if not last_line:
        return {
            "trial_id": trial_id,
            "crashed": True,
            "returncode": 0,
            "stderr_tail": stderr.decode(errors="replace")[-4000:],
            "note": "no stdout",
        }
    try:
        return json.loads(last_line[-1])
    except json.JSONDecodeError:
        return {
            "trial_id": trial_id,
            "crashed": True,
            "returncode": 0,
            "stderr_tail": stderr.decode(errors="replace")[-4000:],
            "stdout_tail": last_line[-1][-2000:],
            "note": "stdout last line was not JSON",
        }


async def main_async(args: argparse.Namespace) -> int:
    sem = asyncio.Semaphore(args.jobs)
    start = time.monotonic()
    tasks = [
        asyncio.create_task(
            _run_one(i, args.servers, args.cycles, args.calls_per_cycle, sem)
        )
        for i in range(args.trials)
    ]
    results = []
    for done in asyncio.as_completed(tasks):
        r = await done
        results.append(r)
        tag = "HIT" if r.get("target_hits") else ("SIBLING" if r.get("sibling_hits") else ("CRASH" if r.get("crashed") else "clean"))
        print(
            f"[{len(results)}/{args.trials}] trial={r.get('trial_id')} {tag} "
            f"elapsed={r.get('elapsed_s', 0):.1f}s "
            f"total_wall={time.monotonic() - start:.1f}s",
            flush=True,
        )
    elapsed = time.monotonic() - start

    hits = [r for r in results if r.get("target_hits")]
    sibling_only = [
        r for r in results if r.get("sibling_hits") and not r.get("target_hits")
    ]
    crashed = [r for r in results if r.get("crashed")]

    print()
    print(f"=== {args.trials} trials, servers={args.servers} cycles={args.cycles} "
          f"calls_per_cycle={args.calls_per_cycle} jobs={args.jobs} "
          f"wall={elapsed:.1f}s ===")
    print(f"TARGET hits ('SSE stream ended without a response'): {len(hits)}/{args.trials}")
    print(f"sibling-only hits ('...reconnection attempts were exhausted'): "
          f"{len(sibling_only)}/{args.trials}")
    print(f"probe crashes (not a flake hit, a probe bug): {len(crashed)}/{args.trials}")

    if hits:
        print("\n--- sample TARGET hit(s) ---")
        for r in hits[:5]:
            print(f"trial {r['trial_id']}: {r['target_hits'][0]}")

    if crashed:
        print("\n--- probe crashes ---")
        for r in crashed[:5]:
            print(f"trial {r['trial_id']}: rc={r.get('returncode')} note={r.get('note')}")
            print(r.get("stderr_tail", "")[-800:])

    print()
    print(json.dumps({
        "trials": args.trials,
        "servers": args.servers,
        "cycles": args.cycles,
        "calls_per_cycle": args.calls_per_cycle,
        "target_hit_count": len(hits),
        "sibling_only_count": len(sibling_only),
        "crash_count": len(crashed),
        "elapsed_s": elapsed,
    }))

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=40)
    parser.add_argument("--servers", type=int, default=8)
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--calls-per-cycle", type=int, default=20)
    parser.add_argument("--jobs", type=int, default=4, help="max concurrent trial subprocesses")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
