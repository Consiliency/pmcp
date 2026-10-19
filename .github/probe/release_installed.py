"""Focused behavior of an exact installed archive, outside every checkout."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import sys

import pmcp
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from starlette.testclient import TestClient

from pmcp.manifest import npm_resolver
from pmcp.transport.http import create_http_app


def installed() -> None:
    prefix = Path(sys.prefix).resolve()
    assert Path(pmcp.__file__).resolve().is_relative_to(prefix)
    assert importlib.metadata.version("pmcp") == sys.argv[1]
    for module in ("pmcp.client.manager", "pmcp.server", "pmcp.config.loader"):
        importlib.import_module(module)

    assert npm_resolver._HELPER.is_file()
    result = npm_resolver.get_resolver().resolve(
        "npx", ["-y", "left-pad"], env={}, cwd=None
    )
    assert result.is_identity and result.spec == "left-pad"


def rejection() -> None:
    canary = "RELEASE-REJECTED-CANARY"
    modern = "2026-07-28"
    meta = {
        "io.modelcontextprotocol/protocolVersion": modern,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "mcp-protocol-version": modern,
    }
    app = create_http_app(Server("release-probe"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for method, params in [
            (canary, {"_meta": meta}),
            (
                "tools/call",
                {"_meta": meta, "name": {canary: canary}, "arguments": canary},
            ),
        ]:
            response = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": method, "params": params},
                headers={**headers, "mcp-method": method},
            )
            assert response.status_code >= 400
            assert "error" in response.json()
            assert canary not in response.text and canary not in str(
                dict(response.headers)
            )


async def downstream() -> None:
    root = Path.cwd()
    (root / "lock").mkdir()
    (root / "xdg").mkdir()
    (root / "config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "p1probe": {
                        "command": sys.executable,
                        "args": [str(root / "p1_probe_server.py")],
                    }
                }
            }
        )
    )
    (root / "policy.yaml").write_text('servers:\n  allowlist: ["p1probe"]\n')
    env = {**os.environ, "HOME": str(root), "XDG_CONFIG_HOME": str(root / "xdg")}
    args = [
        "--config",
        str(root / "config.json"),
        "--project",
        str(root),
        "--policy",
        str(root / "policy.yaml"),
        "--lock-dir",
        str(root / "lock"),
    ]
    async with stdio_client(
        StdioServerParameters(
            command=str(Path(sys.prefix) / "bin/pmcp"), args=args, env=env
        )
    ) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            connected = await session.call_tool(
                "gateway.connect_server", {"server_name": "p1probe"}
            )
            assert not connected.is_error
            connect_text = "\n".join(getattr(x, "text", "") for x in connected.content)
            assert json.loads(connect_text)["ok"] is True
            response = await session.call_tool(
                "gateway.invoke",
                {"tool_id": "p1probe::p1_echo", "arguments": {"text": "release"}},
            )
            assert not response.is_error
            text = "\n".join(getattr(x, "text", "") for x in response.content)
            assert json.loads(text)["ok"] is True and "p1-floor-ok:release" in text
            owned = owned_probe_processes(root / "p1_probe_server.py")
            assert owned, "real downstream process was not observed"
    survivors = {pid for pid, started in owned.items() if process_start(pid) == started}
    if survivors:
        # Fail normal shutdown even when emergency cleanup is necessary.
        for pid in survivors:
            if process_start(pid) == owned[pid]:
                os.kill(pid, 9)
        raise AssertionError("downstream outlived normal gateway shutdown")


def process_start(pid: int) -> str | None:
    try:
        return (
            (Path("/proc") / str(pid) / "stat")
            .read_text()
            .rsplit(")", 1)[1]
            .split()[19]
        )
    except (FileNotFoundError, ProcessLookupError):
        return None


def owned_probe_processes(script: Path) -> dict[int, str]:
    result = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            if os.fsencode(script) in (proc / "cmdline").read_bytes().split(b"\0"):
                start = process_start(int(proc.name))
                if start is not None:
                    result[int(proc.name)] = start
        except (FileNotFoundError, ProcessLookupError):
            continue
    return result


if __name__ == "__main__":
    installed()
    rejection()
    asyncio.run(asyncio.wait_for(downstream(), timeout=40))
    print(
        "Exact installed archive: provenance, startup, helper, rejection and downstream passed"
    )
