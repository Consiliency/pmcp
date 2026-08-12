"""Standalone runner for `sse_flake_probe_serverkill.py`: serves
`build_fake_remote_app()` via uvicorn in its OWN process, so the parent
probe can SIGKILL it -- the OS then forcibly closes every fd including any
live client connections, which is what an in-process `task.cancel()` could
not reliably do (see `sse_flake_probe_serverkill.py`'s docstring for why
that in-process attempt produced 0 races touched).

Usage: python _serverkill_runner.py <port> <auth-value>
Prints "READY" to stdout once listening.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn

from tests.runtime.fake_remote import build_fake_remote_app


def main() -> None:
    port = int(sys.argv[1])
    auth_value = sys.argv[2]
    app = build_fake_remote_app(expected_auth_value=auth_value)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="on"
    )
    server = uvicorn.Server(config)

    import asyncio

    async def _run() -> None:
        serve_task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        print("READY", flush=True)
        await serve_task

    asyncio.run(_run())


if __name__ == "__main__":
    main()
