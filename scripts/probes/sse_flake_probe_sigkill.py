"""Single-trial probe, variant 4: SIGKILL the peer's OS process mid-response.

Variant 3 (`sse_flake_probe_serverkill.py`, in-process `task.cancel()` on
uvicorn's `Server.serve()` task) produced 0 touched races out of 10 --
cancelling the outer serve task does not reliably tear down already-accepted
connection handlers, so the client-visible TCP connection often just kept
working. This variant runs the peer in its own OS process
(`_serverkill_runner.py`) and sends it SIGKILL mid-response: the kernel then
closes every one of that process's file descriptors, including any live
client socket, which is the closest local simulation of "the peer really did
die mid-stream" available without raw packet injection.

Same protocol as the other race variants: connect, start a tool call,
concurrently kill the peer after a random delay, classify the result.
"""

from __future__ import annotations  # noqa: E402

import argparse  # noqa: E402
import asyncio  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import random  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
logging.basicConfig(level=logging.ERROR)

from pmcp.client.manager import ClientManager  # noqa: E402
from pmcp.policy.policy import PolicyManager  # noqa: E402
from pmcp.tools.handlers import GatewayTools  # noqa: E402
from pmcp.types import RemoteMcpServerConfig, ResolvedServerConfig  # noqa: E402
from tests.runtime.harness import alloc_port  # noqa: E402

RUNNER = Path(__file__).with_name("_serverkill_runner.py")
TARGET_STRING = "SSE stream ended without a response"
SIBLING_STRING = "SSE stream ended and reconnection attempts were exhausted"
AUTH_VALUE = "Bearer probe-secret-token"


def _config(name: str, url: str) -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name,
        source="custom",
        config=RemoteMcpServerConfig(
            type="streamable-http", url=url, headers={"Authorization": AUTH_VALUE}
        ),
    )


@dataclass
class TrialResult:
    trial_id: int
    kills: int
    target_hits: list[str] = field(default_factory=list)
    sibling_hits: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0


def _classify(message: str, result: TrialResult) -> None:
    if TARGET_STRING in message:
        result.target_hits.append(message)
    elif SIBLING_STRING in message:
        result.sibling_hits.append(message)
    else:
        result.other.append(message)


async def _start_peer_process(port: int) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(RUNNER),
        str(port),
        AUTH_VALUE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    for _ in range(300):
        line = await proc.stdout.readline()
        if b"READY" in line:
            return proc
        if proc.returncode is not None:
            raise RuntimeError("peer process exited before READY")
    raise RuntimeError("peer process never printed READY")


async def _run_trial(
    trial_id: int, kills: int, min_delay_ms: float, max_delay_ms: float
) -> TrialResult:
    result = TrialResult(trial_id=trial_id, kills=kills)
    start = time.monotonic()

    manager = ClientManager()
    gateway_tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())

    for i in range(kills):
        port = alloc_port()
        proc = await _start_peer_process(port)
        config = _config("probe-sigkill", f"http://127.0.0.1:{port}/mcp")
        try:
            errors = await manager.connect_server(config, retry=False)
            for err in errors:
                _classify(err, result)
            if not manager.is_server_online("probe-sigkill"):
                continue

            delay = random.uniform(min_delay_ms, max_delay_ms) / 1000.0

            async def _kill_peer() -> None:
                await asyncio.sleep(delay)
                with contextlib.suppress(ProcessLookupError):
                    proc.send_signal(signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=2.0)

            call_task = asyncio.create_task(
                gateway_tools.invoke(
                    {
                        "tool_id": "probe-sigkill::fr_echo",
                        "arguments": {"text": f"k{i}"},
                    }
                )
            )
            kill_task = asyncio.create_task(_kill_peer())
            call_result, _ = await asyncio.gather(
                call_task, kill_task, return_exceptions=True
            )

            if isinstance(call_result, BaseException):
                _classify(repr(call_result), result)
            elif not call_result.ok:
                for err in call_result.errors or []:
                    _classify(err, result)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    manager.disconnect_server("probe-sigkill", force=True), timeout=3.0
                )
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=3.0)

    with contextlib.suppress(Exception):
        await manager.disconnect_all()

    result.elapsed_s = time.monotonic() - start
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kills", type=int, default=10)
    parser.add_argument("--min-delay-ms", type=float, default=0.0)
    parser.add_argument("--max-delay-ms", type=float, default=30.0)
    parser.add_argument("--trial-id", type=int, default=0)
    args = parser.parse_args()

    result = asyncio.run(
        _run_trial(args.trial_id, args.kills, args.min_delay_ms, args.max_delay_ms)
    )
    print(json.dumps(result.__dict__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
