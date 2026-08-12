"""Single-trial probe for the "SSE stream ended without a response" flake.

Investigation note: .consiliency/notes/sse-stream-ended-investigation-20260812.md

Drives concurrent connect/disconnect + concurrent tool-call traffic against N
real, in-process `fake_remote` Streamable HTTP servers (the same harness
`tests/runtime/test_downstream_remote.py` uses), through a real in-process
`ClientManager`, and watches for the target error string surfacing anywhere:
in `connect_all()`'s returned error list, in a `gateway.invoke()` result's
errors, or in an uncaught exception from teardown.

Run ONE trial per process (see `run_sse_flake_probe.py`, the multi-trial
driver) — `sse_starlette.sse.AppStatus.should_exit` is a process-global latch
(see `tests/runtime/fake_remote.py`'s docstring / `run_fake_remote`'s
`finally:` block) that, once tripped by any uvicorn shutdown in this
process, poisons every SSE stream afterwards. A multi-trial *loop inside one
process* would just be re-discovering that already-fixed bug, not probing
the open one.

Usage:
    uv run python scripts/probes/sse_flake_probe.py --servers 8 --cycles 5 \
        --calls-per-cycle 20 --trial-id 0

Prints one JSON object to stdout as the last line of output. Exit code 0
means "ran without a crash" (the JSON itself reports whether the target
string was observed) -- a hard crash (uncaught exception, non-JSON output)
is a distinct, worse outcome the driver also has to handle.
"""

from __future__ import annotations  # noqa: E402

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

# The probe classifies via returned error strings / exceptions, not logs;
# INFO/DEBUG httpx+pmcp logging is pure I/O overhead here and, at the
# concurrency levels this probe needs, was materially limiting how many
# cycles fit in a trial's wall-clock budget.
logging.basicConfig(level=logging.ERROR)

# `tests.runtime.fake_remote` is only importable with the repo root on
# sys.path; running this file directly (`python scripts/probes/x.py`) puts
# `scripts/probes/` there instead, so add the root explicitly rather than
# requiring callers to invoke via `-m` or set PYTHONPATH themselves.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
    servers: int
    cycles: int
    calls_per_cycle: int
    target_hits: list[str] = field(default_factory=list)
    sibling_hits: list[str] = field(default_factory=list)
    other_connect_errors: list[str] = field(default_factory=list)
    other_invoke_errors: list[str] = field(default_factory=list)
    other_exceptions: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def hit(self) -> bool:
        return bool(self.target_hits)


async def _run_trial(
    trial_id: int, n_servers: int, cycles: int, calls_per_cycle: int
) -> TrialResult:
    result = TrialResult(
        trial_id=trial_id,
        servers=n_servers,
        cycles=cycles,
        calls_per_cycle=calls_per_cycle,
    )
    start = time.monotonic()

    async with _fake_remotes(n_servers) as remotes:
        manager = ClientManager()
        gateway_tools = GatewayTools(
            client_manager=manager, policy_manager=PolicyManager()
        )
        try:
            for cycle in range(cycles):
                configs = [
                    _config(f"probe-{i}", remotes[i].mcp_url) for i in range(n_servers)
                ]

                # Real concurrent connect: connect_all() fires one asyncio
                # task per server and gathers them -- this is the one place
                # pmcp's own lifecycle lock does NOT serialize the network
                # I/O of N servers against each other (see investigation
                # note "why connect_all, not N x connect_server").
                errors = await manager.connect_all(configs, retry=False)
                for err in errors:
                    _classify(err, result)

                online = [c.name for c in configs if manager.is_server_online(c.name)]

                # Concurrent tool-call load: every fr_echo call is a POST
                # whose response arrives over SSE (fake_remote's MCPServer
                # defaults to json_response=False), so this is what
                # exercises `_handle_sse_response` under concurrency.
                call_tasks = [
                    gateway_tools.invoke(
                        {
                            "tool_id": f"{name}::fr_echo",
                            "arguments": {"text": f"c{cycle}-{i}"},
                        }
                    )
                    for i in range(calls_per_cycle)
                    for name in [online[i % len(online)]]
                    if online
                ]
                call_results = await asyncio.gather(*call_tasks, return_exceptions=True)
                for cr in call_results:
                    if isinstance(cr, Exception):
                        _classify(str(cr), result)
                    elif not cr.ok:
                        for err in cr.errors or []:
                            _classify(err, result)

                # Concurrent disconnect of half the servers while the other
                # half is mid-reconnect-eligible-window -- interleaves
                # teardown network activity (socket close, session DELETE)
                # with the next cycle's connect activity in time, even
                # though bookkeeping is serialized by the lifecycle lock.
                half = online[: len(online) // 2]
                await asyncio.gather(
                    *(manager.disconnect_server(name, force=True) for name in half),
                    return_exceptions=True,
                )

            await manager.disconnect_all()
        except Exception as exc:  # noqa: BLE001 - probe must not crash on a hit
            _classify(str(exc), result)
            result.other_exceptions.append(repr(exc))
            try:
                await manager.disconnect_all()
            except Exception:  # noqa: BLE001
                pass

    result.elapsed_s = time.monotonic() - start
    return result


def _classify(message: str, result: TrialResult) -> None:
    if TARGET_STRING in message:
        result.target_hits.append(message)
    elif SIBLING_STRING in message:
        result.sibling_hits.append(message)
    else:
        result.other_connect_errors.append(message)


class _fake_remotes:
    """Fan out `run_fake_remote` across N ephemeral ports as one context manager."""

    def __init__(self, n: int) -> None:
        self._n = n
        self._stack: list = []

    async def __aenter__(self):
        import contextlib

        self._exit_stack = contextlib.AsyncExitStack()
        await self._exit_stack.__aenter__()
        remotes = []
        for _ in range(self._n):
            remote = await self._exit_stack.enter_async_context(
                run_fake_remote(alloc_port(), expected_auth_value=AUTH_VALUE)
            )
            remotes.append(remote)
        return remotes

    async def __aexit__(self, *exc_info):
        return await self._exit_stack.__aexit__(*exc_info)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--servers", type=int, default=8)
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--calls-per-cycle", type=int, default=20)
    parser.add_argument("--trial-id", type=int, default=0)
    args = parser.parse_args()

    result = asyncio.run(
        _run_trial(args.trial_id, args.servers, args.cycles, args.calls_per_cycle)
    )
    print(json.dumps(result.__dict__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
