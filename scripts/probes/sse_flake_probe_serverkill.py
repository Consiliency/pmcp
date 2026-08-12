"""Single-trial probe, variant 3: sever the PEER side abruptly, mid-response,
without going through any of pmcp's own disconnect code.

Rationale: variant 2 (`sse_flake_probe_interrupt.py`) raced pmcp's own
`disconnect_server` against an in-flight tool call and got 0/40 target hits
across 1,200 race attempts -- but every one of those attempts also produced
a `CancelledError` from `ClientManager.cancel_pending_requests`, called at
the *start* of `disconnect_server` before the transport (and therefore the
mcp SDK's own stream-ended handling) ever unwinds. That means the interrupt
variant was never actually testing what the target message needs: an SSE
response stream dying while pmcp is *not* the one tearing it down. This
variant builds the peer directly (bypassing `run_fake_remote`'s managed
context) and kills its serving task -- `task.cancel()`, not a graceful
`server.should_exit = True` -- at a random moment after a tool call starts,
so the peer dies as far as the client's socket is concerned without pmcp
ever calling `disconnect_server` or `cancel_pending_requests`.

`sse_starlette.sse.AppStatus.should_exit` note: `task.cancel()` does not
run uvicorn's normal shutdown signal handler (`Server.serve()`'s
`self.should_exit` is never set), so this does NOT set the process-global
latch -- verified in the smoke run below (`appstatus_before`/`appstatus_after`
in the JSON). This is deliberately not `run_fake_remote`, which calls
`server.should_exit = True` in its `finally:` and is the one place the repo
resets that latch; skipped entirely here on purpose.
"""

from __future__ import annotations  # noqa: E402

import argparse  # noqa: E402
import asyncio  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import random  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
logging.basicConfig(level=logging.ERROR)

import uvicorn  # noqa: E402
from sse_starlette.sse import AppStatus  # noqa: E402

from pmcp.client.manager import ClientManager  # noqa: E402
from pmcp.policy.policy import PolicyManager  # noqa: E402
from pmcp.tools.handlers import GatewayTools  # noqa: E402
from pmcp.types import RemoteMcpServerConfig, ResolvedServerConfig  # noqa: E402
from tests.runtime.fake_remote import build_fake_remote_app  # noqa: E402
from tests.runtime.harness import alloc_port  # noqa: E402

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
    appstatus_before: bool | None = None
    appstatus_after: bool | None = None
    elapsed_s: float = 0.0


def _classify(message: str, result: TrialResult) -> None:
    if TARGET_STRING in message:
        result.target_hits.append(message)
    elif SIBLING_STRING in message:
        result.sibling_hits.append(message)
    else:
        result.other.append(message)


async def _start_server(port: int) -> tuple[uvicorn.Server, asyncio.Task]:
    app = build_fake_remote_app(expected_auth_value=AUTH_VALUE)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="on"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.02)
    else:
        raise RuntimeError("server never started")
    return server, task


async def _run_trial(
    trial_id: int, kills: int, min_delay_ms: float, max_delay_ms: float
) -> TrialResult:
    result = TrialResult(trial_id=trial_id, kills=kills)
    result.appstatus_before = AppStatus.should_exit
    start = time.monotonic()

    manager = ClientManager()
    gateway_tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager())

    for i in range(kills):
        port = alloc_port()
        server, serve_task = await _start_server(port)
        config = _config("probe-kill", f"http://127.0.0.1:{port}/mcp")
        try:
            errors = await manager.connect_server(config, retry=False)
            for err in errors:
                _classify(err, result)
            if not manager.is_server_online("probe-kill"):
                continue

            delay = random.uniform(min_delay_ms, max_delay_ms) / 1000.0

            async def _kill_peer() -> None:
                await asyncio.sleep(delay)
                serve_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await serve_task

            call_task = asyncio.create_task(
                gateway_tools.invoke(
                    {
                        "tool_id": "probe-kill::fr_echo",
                        "arguments": {"text": f"kill-{i}"},
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
            # Best-effort teardown: pmcp's own disconnect against an
            # already-dead peer, then make sure the serve task is gone.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    manager.disconnect_server("probe-kill", force=True), timeout=3.0
                )
            if not serve_task.done():
                serve_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await serve_task

    with contextlib.suppress(Exception):
        await manager.disconnect_all()

    result.appstatus_after = AppStatus.should_exit
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
