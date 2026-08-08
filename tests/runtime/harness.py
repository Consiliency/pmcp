"""SL-4 — deployed-wire runtime acceptance harness, shared by tests/runtime/*.

Not collected by pytest itself (no `test_*` names) — `tests/runtime/test_harness.py`
is the collectable module that pins the contract this file provides.

Provides:
  - `alloc_port()` — a real ephemeral-port allocator (`socket.bind(("127.0.0.1", 0))`);
    no test under this package may hardcode a port literal for its own gateway.
  - `booted_gateway()` — a context manager that boots an isolated gateway on a
    spare port with all six isolation controls (`--config`, `--project`,
    `--policy`, `--lock-dir`, redirected `HOME`/`XDG_CONFIG_HOME`, and a cwd
    inside the throwaway dir), waits for `/health`, and asserts the live
    `:3344` gateway's pid and child set are unchanged across the cycle
    (Execution Notes > Runtime-step safety). Reuses
    `tests.test_credential_boot._live_gateway_pid` / `_children_of` rather
    than reinventing them, per the plan.
  - `gateway_on_spare_port` — a session-scoped pytest fixture wrapping
    `booted_gateway()` with the harness's one `rt-fixture` downstream
    eagerly started, shared by the handshake-era and modern-era wire tests
    (SL-4.2, SL-4.3).
  - `modern_post()` / `modern_envelope()` / `decode_modern_response()` — build
    and send an IF-0-P2-4 modern-envelope JSON-RPC request over HTTP and
    decode the response regardless of framing (plain `application/json` or
    one `text/event-stream` `data:` frame — IF-0-P2-4, Execution Notes >
    "EC-P2-6 must go over the deployed HTTP wire").
  - `RT_FIXTURE_SRC` — a real stdio `mcp.server.MCPServer` (2.0.0) exposing
    one tool and one prompt, registered as the harness gateway's sole
    `autoStart` downstream so `prompts/list`/`prompts/get` (which pmcp only
    ever proxies from a live downstream — there is no built-in prompt) have
    real typed content to exercise.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.shared.inbound import (
    MCP_METHOD_HEADER,
    MCP_NAME_HEADER,
    MCP_PROTOCOL_VERSION_HEADER,
)
from mcp.types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY
from mcp.types.version import LATEST_MODERN_VERSION

from tests.test_credential_boot import _children_of, _live_gateway_pid

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_GATEWAY_PORT = 3344  # NEVER boot a tests/runtime/ fixture on this port.

RT_FIXTURE_SRC = '''\
from mcp.server import MCPServer

mcp = MCPServer("rt-fixture")


@mcp.tool()
def rt_echo(text: str) -> str:
    """Echo the supplied text back with a fixed, greppable prefix."""
    return f"rt-echo:{text}"


@mcp.prompt()
def rt_greeting(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"


if __name__ == "__main__":
    mcp.run()
'''


def alloc_port() -> int:
    """A real allocator: bind to an OS-assigned ephemeral port and release it.

    Ports 38344 and 38345 are already claimed by tests/test_credential_boot.py
    and its P6CLEAN sibling; this harness never hardcodes a port so it cannot
    collide with those or with itself across concurrent test runs.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _live_health_ok() -> bool:
    result = subprocess.run(
        ["curl", "-sf", f"http://127.0.0.1:{LIVE_GATEWAY_PORT}/health"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


@dataclass
class BootedGateway:
    """State of one isolated gateway booted by `booted_gateway()`."""

    base_url: str
    mcp_url: str
    port: int
    work_dir: Path
    boot_log: Path
    command: list[str]
    env: dict[str, str]
    cwd: Path
    proc: subprocess.Popen[bytes]


def _wait_for_health(proc: subprocess.Popen[bytes], boot_log: Path, port: int) -> None:
    for _ in range(60):
        result = subprocess.run(
            ["curl", "-sf", f"http://127.0.0.1:{port}/health"],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return
        if proc.poll() is not None:
            pytest.fail(f"fixture gateway exited early:\n{boot_log.read_text()}")
        time.sleep(0.5)
    pytest.fail(f"fixture gateway never became healthy:\n{boot_log.read_text()}")


@contextlib.contextmanager
def booted_gateway(
    *,
    extra_servers: dict[str, Any] | None = None,
    extra_allowlist: list[str] | None = None,
) -> Iterator[BootedGateway]:
    """Boot one fully isolated gateway with the `rt-fixture` stdio downstream
    eagerly started, on a spare port never `:3344`.

    `extra_servers` / `extra_allowlist` let a caller (e.g. SL-4.5's mcp-1.x
    downstream) add another `mcpServers` entry to the same isolated boot
    without duplicating the isolation plumbing.

    Guarantees, matching Execution Notes > "Runtime-step safety" and
    "Boot isolation requires all six controls together" verbatim:
      - never binds :3344, resolved via `alloc_port()`
      - passes all six isolation controls plus a cwd inside the throwaway dir
      - a live gateway on :3344, if one exists, has an identical pid and
        child set before and after (on a runner with none, the pid stays
        `None` throughout -- see below)
      - never deletes or moves the operator's real files — only the
        redirected `HOME`/`XDG_CONFIG_HOME` env vars change where the code
        looks, matching "Never isolate by moving or deleting files outside
        the worktree"
    """
    # A live gateway on :3344 is an operator-host fixture, not a CI one --
    # V0a/V0b in the plan's own Verification block treat an empty
    # `LIVE_PID` as a valid state (`test "$(...)" = "$LIVE_PID"` passes
    # when both sides are empty), and CI's runners have no pmcp systemd
    # unit at all. Mirror that: `None` here means "nothing to protect",
    # not "abort" -- the boot-isolation and wire-behaviour evidence this
    # harness exists for must still run on a runner with no live gateway.
    live_pid = _live_gateway_pid()
    if live_pid is not None:
        assert _live_health_ok(), (
            "live gateway health check failed BEFORE boot -- abort"
        )
    children_before = _children_of(live_pid) if live_pid is not None else []

    with tempfile.TemporaryDirectory(prefix="pmcp-rt-") as work_str:
        work = Path(work_str)
        fake_home = work / "home"
        fake_home.mkdir()
        project = work / "proj"
        project.mkdir()
        lock_dir = work / "lock"

        # Unique command basename + a fresh-mktemp arg, so
        # _kill_orphan_processes' (Path(command).name, tuple(args))
        # fingerprint (server.py) cannot collide with anything the live
        # gateway is running — same recipe as test_credential_boot.py.
        fixture_py = work / "rt_fixture_server.py"
        fixture_py.write_text(RT_FIXTURE_SRC)
        wrapper = work / "rt-fixture-server"
        wrapper.write_text(
            f'#!/bin/sh\nexec {REPO_ROOT}/.venv/bin/python {fixture_py} "$@"\n'
        )
        wrapper.chmod(0o755)

        servers: dict[str, Any] = {
            "rt-fixture": {"command": str(wrapper), "args": ["--rt-run", str(work)]}
        }
        if extra_servers:
            servers.update(extra_servers)
        auto_start = sorted({"rt-fixture", *(extra_servers or {})})
        allowlist = sorted(
            {"rt-fixture", *(extra_allowlist or []), *(extra_servers or {})}
        )

        config_path = work / "config.json"
        config_path.write_text(
            json.dumps({"mcpServers": servers, "autoStart": auto_start})
        )

        policy_path = work / "policy.yaml"
        policy_lines = "\n".join(f"    - {name}" for name in allowlist)
        policy_path.write_text(f"servers:\n  allowlist:\n{policy_lines}\n")

        boot_log = work / "gw.log"
        port = alloc_port()
        env = {
            **os.environ,
            "HOME": str(fake_home),
            "XDG_CONFIG_HOME": str(fake_home / ".config"),
        }
        # No PMCP_MANIFEST_PATH: the shipped manifest layers in additively
        # regardless of --config, which is exactly what the ~106
        # skipped/policy_denied entries in every boot log below prove.

        command = [
            f"{REPO_ROOT}/.venv/bin/pmcp",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--lock-dir",
            str(lock_dir),
            "--config",
            str(config_path),
            "--project",
            str(project),
            "--policy",
            str(policy_path),
            "-l",
            "info",
        ]
        with open(boot_log, "w") as log_fh:
            proc = subprocess.Popen(
                command,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=work,
            )
        try:
            _wait_for_health(proc, boot_log, port)
            yield BootedGateway(
                base_url=f"http://127.0.0.1:{port}",
                mcp_url=f"http://127.0.0.1:{port}/mcp",
                port=port,
                work_dir=work,
                boot_log=boot_log,
                command=command,
                env=env,
                cwd=work,
                proc=proc,
            )
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)

    # A gateway that didn't exist before this boot must not exist after it
    # either -- this harness allocates a spare port and must never leave
    # its own process listening on :3344 (`port != 3344` already backs
    # this at the transport level; this re-checks at the process level).
    live_pid_after = _live_gateway_pid()
    assert live_pid_after == live_pid, (
        "FAIL: live gateway pid on :3344 changed across the boot -- "
        f"before={live_pid} after={live_pid_after}"
    )
    if live_pid is not None:
        assert _live_health_ok(), "FAIL: live gateway died during/after the boot"
        children_after = _children_of(live_pid)
        assert children_before == children_after, (
            "FAIL: live gateway's child set changed -- "
            f"before={children_before} after={children_after}"
        )


@pytest.fixture(scope="session")
def gateway_on_spare_port() -> Iterator[BootedGateway]:
    """Session-scoped: one boot shared by every handshake-era and
    modern-era wire test (SL-4.2, SL-4.3) so each module doesn't pay a
    fresh ~1-2s subprocess boot per test."""
    with booted_gateway() as gw:
        yield gw


# For tools/call, prompts/get, resources/read only (IF-0-P2-4).
_NAME_REQUIRED_METHODS = frozenset({"tools/call", "prompts/get", "resources/read"})


def modern_envelope(
    method: str, params: dict[str, Any] | None = None, *, name: str | None = None
) -> tuple[dict[str, str], dict[str, Any]]:
    """Build the (headers, body) pair IF-0-P2-4 requires for a modern-era
    request: both `_meta` keys, `MCP-Protocol-Version`/`Mcp-Method` always,
    and `Mcp-Name` for the three named-target methods."""
    meta_params = dict(params or {})
    meta_params["_meta"] = {
        PROTOCOL_VERSION_META_KEY: LATEST_MODERN_VERSION,
        CLIENT_CAPABILITIES_META_KEY: {},
    }
    headers = {
        "Content-Type": "application/json",
        # pmcp constructs its session manager with json_response=False, which
        # answers 406 unless Accept carries both media types (IF-0-P2-4).
        "Accept": "application/json, text/event-stream",
        MCP_PROTOCOL_VERSION_HEADER: LATEST_MODERN_VERSION,
        MCP_METHOD_HEADER: method,
    }
    if method in _NAME_REQUIRED_METHODS:
        assert name is not None, f"{method} requires Mcp-Name"
        headers[MCP_NAME_HEADER] = name
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": meta_params}
    return headers, body


def decode_modern_response(response: httpx.Response) -> dict[str, Any]:
    """Decode a modern-era JSON-RPC response regardless of framing.

    pmcp sets json_response=False, so the handler runs under a 15s SSE
    deferral: a body that completes inside the window is plain
    `application/json`; otherwise the response commits to
    `text/event-stream` and the result arrives as one `event: message` /
    `data: {...}` frame. Asserting only plain JSON would make a *correct*
    gateway look broken whenever a call is slow or emits progress
    (IF-0-P2-4).
    """
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        data_lines = [
            line for line in response.text.splitlines() if line.startswith("data:")
        ]
        assert data_lines, (
            f"text/event-stream response carried no data: frame: {response.text!r}"
        )
        return json.loads(data_lines[-1][len("data:") :].strip())  # noqa: E203
    return response.json()


def modern_post(
    base_url: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    name: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """POST a modern-envelope JSON-RPC request to `<base_url>/mcp` and
    return the decoded message (regardless of response framing)."""
    headers, body = modern_envelope(method, params, name=name)
    response = httpx.post(
        f"{base_url}/mcp", headers=headers, json=body, timeout=timeout
    )
    response.raise_for_status()
    return decode_modern_response(response)


# EC-P2-2's boot-log assertion, shared so every module checks the same regex.
INITIALIZED_RE = re.compile(r"Gateway initialized: \d+/\d+ servers online")


def open_socket_fd_count() -> int:
    """Count this process's open socket file descriptors via `/proc/self/fd`
    readlink targets (`socket:[12345]`) — not psutil, which isn't a pmcp
    dependency. Used by EC-P2-7's leak proof: a growing count across
    reconnect cycles means a socket escaped `_cleanup_client`."""
    count = 0
    for fd in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        if target.startswith("socket:"):
            count += 1
    return count
