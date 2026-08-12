"""Single-trial probe, variant 2: deliberately race a tool call against a
disconnect of the SAME server, instead of hoping organic load produces the
race.

Rationale (see the investigation note): `fake_remote`'s `MCPServer` is built
with no `event_store`, so `mcp.server.streamable_http`'s per-event `event_id`
(`.venv/.../mcp/server/streamable_http.py:1047-1049`) is `None` for every SSE
event it ever sends. On the client side that means `last_event_id` never
becomes non-`None` (`mcp/client/streamable_http.py:429-430`), so *any*
interruption of an in-flight SSE response -- not just one before its first
byte -- lands on the target message
("SSE stream ended without a response") rather than the reconnect-exhausted
sibling. `sse_flake_probe.py` bet on organic connection-pool/server-load
timing to produce that interruption; this variant forces it directly, to
learn whether the code path is reachable in-process at all before spending
more organic-load trial budget.

For each cycle: start a tool call, then concurrently start
`disconnect_server(name, force=True)` for the SAME server after a small
random delay, so the disconnect's transport teardown races the tool call's
still-open SSE read.
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

from pmcp.client.manager import ClientManager  # noqa: E402
from pmcp.policy.policy import PolicyManager  # noqa: E402
from pmcp.tools.handlers import GatewayTools  # noqa: E402
from pmcp.types import RemoteMcpServerConfig, ResolvedServerConfig  # noqa: E402
from tests.runtime.fake_remote import run_fake_remote  # noqa: E402
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
    races: int
    target_hits: list[str] = field(default_factory=list)
    sibling_hits: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def hit(self) -> bool:
        return bool(self.target_hits)


def _classify(message: str, result: TrialResult) -> None:
    if TARGET_STRING in message:
        result.target_hits.append(message)
    elif SIBLING_STRING in message:
        result.sibling_hits.append(message)
    else:
        result.other.append(message)


async def _run_trial(
    trial_id: int, races: int, min_delay_ms: float, max_delay_ms: float
) -> TrialResult:
    result = TrialResult(trial_id=trial_id, races=races)
    start = time.monotonic()

    async with run_fake_remote(alloc_port(), expected_auth_value=AUTH_VALUE) as remote:
        manager = ClientManager()
        gateway_tools = GatewayTools(
            client_manager=manager, policy_manager=PolicyManager()
        )
        config = _config("probe-race", remote.mcp_url)
        try:
            for i in range(races):
                errors = await manager.connect_server(config, retry=False)
                for err in errors:
                    _classify(err, result)
                if not manager.is_server_online("probe-race"):
                    continue

                delay = random.uniform(min_delay_ms, max_delay_ms) / 1000.0

                async def _delayed_disconnect() -> None:
                    await asyncio.sleep(delay)
                    with contextlib.suppress(Exception):
                        await manager.disconnect_server("probe-race", force=True)

                call_task = asyncio.create_task(
                    gateway_tools.invoke(
                        {
                            "tool_id": "probe-race::fr_echo",
                            "arguments": {"text": f"race-{i}"},
                        }
                    )
                )
                disc_task = asyncio.create_task(_delayed_disconnect())
                call_result, _ = await asyncio.gather(
                    call_task, disc_task, return_exceptions=True
                )
                if isinstance(call_result, BaseException):
                    # gather(return_exceptions=True) reports a cancelled
                    # tool-call task as a bare CancelledError here (it does
                    # not subclass Exception since Python 3.8), so this must
                    # check BaseException or a race that manifests as
                    # cancellation silently escapes classification.
                    _classify(repr(call_result), result)
                elif not call_result.ok:
                    for err in call_result.errors or []:
                        _classify(err, result)

                # Make sure this server is fully torn down before the next
                # cycle's connect, whichever path the race took.
                with contextlib.suppress(Exception):
                    await manager.disconnect_server("probe-race", force=True)

            with contextlib.suppress(Exception):
                await manager.disconnect_all()
        except Exception as exc:  # noqa: BLE001
            _classify(str(exc), result)
            with contextlib.suppress(Exception):
                await manager.disconnect_all()

    result.elapsed_s = time.monotonic() - start
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--races", type=int, default=20)
    parser.add_argument("--min-delay-ms", type=float, default=0.0)
    parser.add_argument("--max-delay-ms", type=float, default=15.0)
    parser.add_argument("--trial-id", type=int, default=0)
    args = parser.parse_args()

    result = asyncio.run(
        _run_trial(args.trial_id, args.races, args.min_delay_ms, args.max_delay_ms)
    )
    print(json.dumps(result.__dict__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
