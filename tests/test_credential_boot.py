"""P5 SL-4.7: live spare-port boot proof for credential optionality.

Boots a second, fully isolated PMCP gateway on a spare port (never :3344,
where the operator's live gateway runs) and proves IF-0-P5-4 on a real
spawned child process: a manifest-only server declaring
`api_key_optional_when` starts EAGERLY with no credential set anywhere, and
`/proc/<pid>/environ` shows the relaxer present and the credential var
absent.

Isolation recipe copied verbatim from P6CLEAN's verified fix (commit
cbe98be, `plans/phase-plan-v11-p6clean.md`) — do not re-derive it. Two
mistakes are baked in as regression guards below: `--config` alone does NOT
bound resolution (the shipped manifest layers in additively), and redirected
`HOME` alone is ALSO not sufficient on its own to prove isolation — the
`--policy` allowlist is what actually narrows the kill set
(`_kill_orphan_processes`, `src/pmcp/server.py:683-700`, fed by
`resolution.lazy_configs + resolution.eager_configs`, `server.py:604`).

Marked `@pytest.mark.live` — excluded from the default `-m 'not live'` run
(`pyproject.toml`). Run explicitly:
    uv run pytest -m live tests/test_credential_boot.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_GATEWAY_PORT = 3344  # NEVER boot the test fixture on this port.
SPARE_PORT = 38345  # Distinct from P6CLEAN's 38344 to avoid any collision.

FIXTURE_SERVER_SRC = """\
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("p5-fixture")


@mcp.tool()
def ping(value: str) -> str:
    return f"p5-pong:{value}"


if __name__ == "__main__":
    mcp.run()
"""


def _live_gateway_pid() -> str | None:
    """PID listening on :3344, resolved from the socket — never by name-match
    (pgrep -f self-matches the invoking shell's command line)."""
    result = subprocess.run(
        ["ss", "-ltnpH", f"sport = :{LIVE_GATEWAY_PORT}"],
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"pid=(\d+)", result.stdout)
    return match.group(1) if match else None


def _children_of(pid: str) -> list[str]:
    result = subprocess.run(
        ["pgrep", "-P", pid], capture_output=True, text=True, check=False
    )
    # pgrep exits 1 on zero matches, which is a valid state, not a failure.
    return sorted(line for line in result.stdout.splitlines() if line)


def _live_health_ok() -> bool:
    result = subprocess.run(
        ["curl", "-sf", f"http://127.0.0.1:{LIVE_GATEWAY_PORT}/health"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


@pytest.mark.live
def test_relaxed_manifest_server_boots_eager_with_no_credential() -> None:
    live_pid = _live_gateway_pid()
    assert live_pid, "FAIL: could not resolve live gateway pid on :3344 — abort"
    assert _live_health_ok(), "live gateway health check failed BEFORE boot — abort"
    children_before = _children_of(live_pid)

    with tempfile.TemporaryDirectory(prefix="pmcp-p5-boot-") as work_str:
        work = Path(work_str)
        fake_home = work / "home"
        fake_home.mkdir()
        project = work / "proj"
        project.mkdir()
        lock_dir = work / "lock"

        # Fixture wrapper script — unique command basename + a fresh-mktemp
        # arg, so _kill_orphan_processes' (Path(command).name, tuple(args))
        # fingerprint (server.py:700) cannot collide with anything the live
        # gateway is running.
        fixture_py = work / "p5_fixture_server.py"
        fixture_py.write_text(FIXTURE_SERVER_SRC)
        wrapper = work / "p5-fixture-server"
        wrapper.write_text(
            f'#!/bin/sh\nexec {REPO_ROOT}/.venv/bin/python {fixture_py} "$@"\n'
        )
        wrapper.chmod(0o755)

        # Manifest overlay: ONE server, declaring the relaxable credential.
        # FIRECRAWL-style field, but a dedicated var name so this test never
        # depends on (or collides with) the shipped firecrawl entry.
        overlay = work / "manifest-overlay.yaml"
        overlay.write_text(
            f"""
servers:
  p5-fixture:
    description: "P5 live-boot fixture"
    keywords: [p5-fixture]
    command: "{wrapper}"
    args: ["--p5-run", "{work}"]
    install: {{}}
    requires_api_key: true
    env_var: P5_FIXTURE_API_KEY
    api_key_optional_when: ["P5_FIXTURE_URL"]
    extra_env:
      P5_FIXTURE_URL: "http://localhost:9999"
"""
        )

        # Config: no mcpServers entries at all — autoStart names the manifest
        # server directly, exercising the manifest-loop eager path (the one
        # config/loader.py:1038 gates on).
        config_path = work / "config.json"
        config_path.write_text(
            json.dumps({"mcpServers": {}, "autoStart": ["p5-fixture"]})
        )

        # Fixture-only policy — the real second isolation layer.
        policy_path = work / "policy.yaml"
        policy_path.write_text("servers:\n  allowlist:\n    - p5-fixture\n")

        gw_log = work / "gw.log"
        env = {
            **os.environ,
            "HOME": str(fake_home),
            "XDG_CONFIG_HOME": str(fake_home / ".config"),
            "PMCP_MANIFEST_PATH": str(overlay),
        }
        # Absent everywhere — the whole point of the test.
        env.pop("P5_FIXTURE_API_KEY", None)

        gw_proc = subprocess.Popen(
            [
                f"{REPO_ROOT}/.venv/bin/pmcp",
                "--transport",
                "http",
                "--host",
                "127.0.0.1",
                "--port",
                str(SPARE_PORT),
                "--lock-dir",
                str(lock_dir),
                "--config",
                str(config_path),
                "--project",
                str(project),
                "--policy",
                str(policy_path),
            ],
            stdout=open(gw_log, "w"),
            stderr=subprocess.STDOUT,
            env=env,
        )
        try:
            _wait_for_health(gw_proc, gw_log)

            log_text = gw_log.read_text()
            summary_match = re.search(
                r"Startup policy summary: eager=(\d+), lazy=(\d+), "
                r"skipped=(\d+), policy_denied=(\d+)",
                log_text,
            )
            assert summary_match, f"no startup summary found in log:\n{log_text}"
            eager, lazy, skipped, policy_denied = (
                int(g) for g in summary_match.groups()
            )
            # The fixture is the ONLY server that reaches eager+lazy — this is
            # what actually proves isolation, NOT "server counts in the
            # hundreds" (the shipped manifest's 106 entries always
            # contribute, correctly, to skipped/policy_denied).
            assert eager + lazy == 1, (
                f"expected exactly 1 server in eager+lazy, got "
                f"eager={eager} lazy={lazy} (isolation likely broken): "
                f"{log_text}"
            )
            assert skipped >= 1 and policy_denied >= 1, (
                "expected the shipped 106-server manifest to still "
                f"contribute policy_denied entries: skipped={skipped} "
                f"policy_denied={policy_denied}"
            )

            # gateway.invoke round-trip — the functional proof. /health only
            # proves the HTTP server answers (transport/http.py hardcodes
            # "ok": True); it is NOT proof the fixture is actually running.
            asyncio.run(_invoke_fixture_ping())

            # IF-0-P5-4 on a live process: the spawned child's real
            # environment carries the relaxer and NOT the credential.
            child_pid = _find_fixture_child_pid(str(work))
            assert child_pid, "could not find spawned fixture child process"
            child_environ = Path(f"/proc/{child_pid}/environ").read_bytes()
            child_env = dict(
                item.split("=", 1)
                for item in child_environ.decode(errors="replace").split("\0")
                if "=" in item
            )
            assert child_env.get("P5_FIXTURE_URL") == "http://localhost:9999"
            assert "P5_FIXTURE_API_KEY" not in child_env
        finally:
            gw_proc.send_signal(signal.SIGTERM)
            try:
                gw_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                gw_proc.kill()
                gw_proc.wait(timeout=10)

    # Live gateway must be exactly as it was — before AND after.
    assert _live_health_ok(), "FAIL: live gateway died during/after the boot test"
    children_after = _children_of(live_pid)
    assert children_before == children_after, (
        "FAIL: live gateway's child set changed — "
        f"before={children_before} after={children_after}"
    )


def _wait_for_health(gw_proc: subprocess.Popen, gw_log: Path) -> None:
    for _ in range(60):
        result = subprocess.run(
            ["curl", "-sf", f"http://127.0.0.1:{SPARE_PORT}/health"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return
        if gw_proc.poll() is not None:
            pytest.fail(f"fixture gateway exited early:\n{gw_log.read_text()}")
        time.sleep(0.5)
    pytest.fail(f"fixture gateway never became healthy:\n{gw_log.read_text()}")


async def _invoke_fixture_ping() -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = f"http://127.0.0.1:{SPARE_PORT}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool_names = {t.name for t in (await session.list_tools()).tools}
            assert "gateway.invoke" in tool_names, sorted(tool_names)
            result = await session.call_tool(
                "gateway.invoke",
                {
                    "tool_id": "p5-fixture::ping",
                    "arguments": {"value": "p5"},
                },
            )
            body = "".join(getattr(c, "text", "") for c in result.content)
            assert "p5-pong:p5" in body, f"downstream call failed: {body!r}"


def _find_fixture_child_pid(work_dir: str) -> str | None:
    """Resolve the spawned fixture's PID via /proc/*/cmdline containing the
    unique work-dir path — safe from self-matching since this string never
    appears in the pytest process's own argv."""
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        cmdline_path = proc_dir / "cmdline"
        try:
            cmdline = cmdline_path.read_bytes()
        except OSError:
            continue
        if work_dir.encode() in cmdline and b"p5_fixture_server.py" in cmdline:
            return proc_dir.name
    return None
