"""Throwaway stdio MCP server used only by the `min-version-smoke` CI job.

This is the downstream end of the floor proof. `min-version-smoke` installs pmcp
pinned at exactly the `mcp` lower bound declared in `pyproject.toml`, boots the
gateway, and drives a real tool call through it to this server. Importing the
gateway's startup modules is NOT sufficient evidence for a floor — the mcp 2.x
break that motivated these guards was invisible to imports — and neither is
`/health`, which returns a hardcoded literal. Only a round trip through session
initialization, tool discovery, and invocation exercises the code an `mcp`
version bump actually lands on.

Deliberately not under `tests/`: it must stay out of the shipped wheel and out
of the pytest collection path.

The gateway SIGKILLs orphaned downstream processes at startup, matching on
`(Path(command).name, tuple(args))`. This file is therefore always launched as
`<venv>/bin/python <absolute path under a throwaway temp dir>`, a pair no
unrelated process on the runner can collide with.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("p1probe")


@mcp.tool()
def p1_echo(text: str) -> str:
    """Echo the supplied text back with a fixed, greppable prefix."""
    return f"p1-floor-ok:{text}"


if __name__ == "__main__":
    mcp.run()
