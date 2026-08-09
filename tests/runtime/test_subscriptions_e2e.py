"""SL-5.2 — EC-P3B-4: the phase's headline end-to-end subscription
acceptance, and the deployed-wire regression for the `request_timeout`
truncation measured spike 2 found.

Two subscriptions are opened over one booted gateway's deployed `/mcp`:

- A, filtered to all three kinds (tools + resources + prompts). The
  roadmap requires the client receive the *corresponding* notification for
  a catalog mutation -- a tools-only assertion would let a broken
  prompts/resources publisher ship, so every mutation below is checked
  against all three kinds on A.
- B, filtered to tools only, held open concurrently. This is the
  filter-negative, kept on a *separate* subscription deliberately: folding
  it into A would make "resources never arrives" double as this phase's
  only resources coverage, which would pass against a completely broken
  resources publisher.

The catalog mutation is driven by real `gateway.connect_server` /
`gateway.disconnect_server` / `gateway.refresh` tool calls over the same
deployed wire (`modern_post`) against a *second* fixture downstream that
starts configured and allowlisted but not auto-started, so
`gateway.connect_server` is a genuine first connect rather than a no-op --
`booted_gateway`'s `extra_servers_no_autostart` (SL-5.1) exists for exactly
this. No test in this module calls `note_*`, `flush`, or `bus.publish`;
the catalog mutation is the only trigger, which is what EC-P3B-4 requires.

`gateway.refresh` is diff-based (`tools/handlers.py`, "Diff-based
refresh"): a server that is lazy (never in `autoStart`) and currently
disconnected is left alone by a bare refresh -- it is not proactively
reconnected. To exercise refresh's own reconnect path honestly (rather
than defeating the diff logic by pre-connecting), this module rewrites
the isolated boot's on-disk `config.json` between the disconnect and
refresh steps to add the second downstream to `autoStart`, modelling an
operator who adds an autoStart entry and refreshes.

The gateway is booted with `request_timeout=5` (SL-5.1's harness keyword)
and an explicit sleep pushes the refresh step's notification past t=12s --
IF-0-P3B-3's `subscriptions/listen` exemption from the `request_timeout`
wrapper is what keeps the stream alive that long; on pre-P3B code (or a
regression that drops the exemption) this module fails outright because
the connection is truncated at t=5s.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

from mcp.shared.subscriptions import SUBSCRIPTION_ID_META_KEY
from mcp.types import CallToolResult

from tests.runtime.harness import (
    REPO_ROOT,
    RT_FIXTURE_SRC,
    ListenStream,
    booted_gateway,
    listen_stream,
    modern_post,
)

_SECOND_DOWNSTREAM = "rt-fixture-2"

_ALL_THREE = {
    "notifications/tools/list_changed",
    "notifications/resources/list_changed",
    "notifications/prompts/list_changed",
}
_TOOLS_ONLY = {"notifications/tools/list_changed"}


def _call_tool(base_url: str, tool_name: str, arguments: dict) -> CallToolResult:
    """Drive one `gateway.*` tool call over the deployed wire and require
    it to have actually succeeded -- both the JSON-RPC layer (no `error`
    key) and the CallToolResult layer (`isError` is not True), so a
    lifecycle failure reported only via `isError` can't slip past a bare
    `"error" not in raw` check."""
    raw = modern_post(
        base_url,
        "tools/call",
        {"name": tool_name, "arguments": arguments},
        name=tool_name,
    )
    assert "error" not in raw, raw
    result = CallToolResult.model_validate(raw["result"])
    assert result.is_error is not True, result
    return result


async def _read_until(
    stream: ListenStream, wanted: set[str], *, budget: float, log: list[dict]
) -> dict[str, dict]:
    """Read frames off `stream` until every method in `wanted` has been
    seen at least once (or `budget` elapses), recording *every* frame
    read -- including ones outside `wanted` -- into `log` so a caller can
    later assert a negative (a kind that must never arrive) over the full
    run, not just the window this particular call watched."""
    deadline = time.monotonic() + budget
    found: dict[str, dict] = {}
    while len(found) < len(wanted):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        frame = await stream.next_frame(timeout=remaining)
        log.append(frame)
        method = frame.get("method")
        if method in wanted and method not in found:
            found[method] = frame
    missing = wanted - found.keys()
    assert not missing, (
        f"timed out after {budget}s waiting for {missing}; "
        f"frames seen so far: {[f.get('method') for f in log]}"
    )
    return found


def _assert_stamped(frames: dict[str, dict], subscription_id: int) -> None:
    for method, frame in frames.items():
        assert frame["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == subscription_id, (
            method,
            frame,
        )


async def test_connect_disconnect_refresh_each_deliver_all_three_kinds() -> None:
    with tempfile.TemporaryDirectory(prefix="pmcp-rt2-") as fixture_dir:
        fixture_path = Path(fixture_dir) / "rt_fixture2.py"
        fixture_path.write_text(RT_FIXTURE_SRC)
        second_server = {
            _SECOND_DOWNSTREAM: {
                "command": str(REPO_ROOT / ".venv" / "bin" / "python"),
                "args": [str(fixture_path)],
            }
        }

        with booted_gateway(
            request_timeout=5, extra_servers_no_autostart=second_server
        ) as gw:
            start = time.monotonic()
            a_log: list[dict] = []
            b_log: list[dict] = []

            async with (
                listen_stream(
                    gw.base_url,
                    notifications={
                        "toolsListChanged": True,
                        "resourcesListChanged": True,
                        "promptsListChanged": True,
                    },
                    request_id=101,
                ) as sub_a,
                listen_stream(
                    gw.base_url,
                    notifications={"toolsListChanged": True},
                    request_id=202,
                ) as sub_b,
            ):
                ack_a = await sub_a.next_frame()
                assert ack_a["method"] == "notifications/subscriptions/acknowledged"
                assert ack_a["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 101

                ack_b = await sub_b.next_frame()
                assert ack_b["method"] == "notifications/subscriptions/acknowledged"
                assert ack_b["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 202

                # --- connect_server: a real first connect --------------
                # rt-fixture-2 is configured and allowlisted but never
                # auto-started (extra_servers_no_autostart), so this is a
                # genuine connect, not a no-op against an already-online
                # server.
                _call_tool(
                    gw.base_url,
                    "gateway.connect_server",
                    {"server_name": _SECOND_DOWNSTREAM},
                )
                frames_a = await _read_until(sub_a, _ALL_THREE, budget=10.0, log=a_log)
                _assert_stamped(frames_a, 101)
                frames_b = await _read_until(sub_b, _TOOLS_ONLY, budget=10.0, log=b_log)
                _assert_stamped(frames_b, 202)

                # --- disconnect_server -----------------------------------
                _call_tool(
                    gw.base_url,
                    "gateway.disconnect_server",
                    {"server_name": _SECOND_DOWNSTREAM},
                )
                frames_a = await _read_until(sub_a, _ALL_THREE, budget=10.0, log=a_log)
                _assert_stamped(frames_a, 101)
                frames_b = await _read_until(sub_b, _TOOLS_ONLY, budget=10.0, log=b_log)
                _assert_stamped(frames_b, 202)

                # --- gateway.refresh --------------------------------------
                # Refresh is diff-based and leaves an already-disconnected,
                # never-autoStart server alone. Add it to autoStart on disk
                # so refresh's diff sees it as newly eager and reconnects
                # it -- the realistic "operator adds an autoStart entry and
                # refreshes" path, rather than defeating the diff logic.
                config_path = gw.work_dir / "config.json"
                config = json.loads(config_path.read_text())
                config["autoStart"] = sorted({*config["autoStart"], _SECOND_DOWNSTREAM})
                config_path.write_text(json.dumps(config))

                # Sleep well past request_timeout=5 before the mutation
                # whose notification this test measures -- a notification
                # that still arrives afterward can only be explained by
                # subscriptions/listen surviving the request_timeout
                # wrapper (IF-0-P3B-3), not by the read completing before
                # the timeout fired.
                await asyncio.sleep(13)

                _call_tool(gw.base_url, "gateway.refresh", {})
                frames_a = await _read_until(sub_a, _ALL_THREE, budget=10.0, log=a_log)
                _assert_stamped(frames_a, 101)
                frames_b = await _read_until(sub_b, _TOOLS_ONLY, budget=10.0, log=b_log)
                _assert_stamped(frames_b, 202)

                elapsed = time.monotonic() - start
                assert elapsed > 12, (
                    f"refresh's notification landed at {elapsed:.1f}s from "
                    "subscription open -- expected > 12s, which is the "
                    "deployed-wire proof that request_timeout=5 did not "
                    "truncate the stream"
                )

            # Filter-negative, over the WHOLE run (all three mutations):
            # B is tools-only and must never have received a resources or
            # prompts notification -- kept separate from A specifically so
            # this isn't A's only resources/prompts coverage.
            b_methods = {frame.get("method") for frame in b_log}
            assert "notifications/resources/list_changed" not in b_methods, b_log
            assert "notifications/prompts/list_changed" not in b_methods, b_log
