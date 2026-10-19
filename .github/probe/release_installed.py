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
    import pmcp.client.manager  # noqa: F401
    import pmcp.server  # noqa: F401
    import pmcp.config.loader  # noqa: F401

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
            assert json.loads(connected.content[0].text)["ok"] is True
            response = await session.call_tool(
                "gateway.invoke",
                {"tool_id": "p1probe::p1_echo", "arguments": {"text": "release"}},
            )
            assert not response.is_error
            text = "\n".join(getattr(x, "text", "") for x in response.content)
            assert json.loads(text)["ok"] is True and "p1-floor-ok:release" in text


if __name__ == "__main__":
    installed()
    rejection()
    asyncio.run(asyncio.wait_for(downstream(), timeout=40))
    print(
        "Exact installed archive: provenance, startup, helper, rejection and downstream passed"
    )
