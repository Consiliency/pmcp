"""No child process writes to the operator's terminal directly
(Consiliency/pmcp#388 round 4).

grok F001: `pmcp refresh` spawned a manifest server through the MCP SDK's
`stdio_client`, whose default `errlog` is `sys.stderr`; the child inherited
that file descriptor and wrote past every escape pmcp applies. claude N-B:
`pmcp upgrade`'s installer and the service restart inherited both streams.
Every spawn now pipes the child's output and logs or echoes it escaped
(`pmcp.child_output`); `DisplaySafeStream.fileno` raises, so a future
inherit-by-descriptor fails loudly.

The static guard checks every spawn in `src/pmcp`; the real-process tests
run each spawn class once, with a small child that writes ESC, U+009B and
RLO to both of its streams, and read the parent's own descriptors.
"""

from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import textwrap
from typing import Any
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "pmcp"
#: Bytes a terminal acts on: ESC (with a CSI clear), U+009B in UTF-8, RLO.
_RAW = (b"\x1b", b"\xc2\x9b", b"\xe2\x80\xae")
_HOSTILE = "SENTINEL\x1b[2J\x9b2J‮"


# --- the static guard --------------------------------------------------------

_SPAWNS = {
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("asyncio", "create_subprocess_exec"),
    ("asyncio", "create_subprocess_shell"),
    ("anyio", "open_process"),
    ("anyio", "run_process"),
    ("os", "system"),
    ("os", "popen"),
    ("pty", "spawn"),
}
_SAFE_TARGETS = {"PIPE", "DEVNULL", "STDOUT"}

#: Spawns whose streams are left as they are, with the reason. None today.
_INHERITING_ALLOWED: dict[tuple[str, int], str] = {}


#: Modules whose functions spawn, by the name the code imports them under.
_SPAWN_MODULES = ("subprocess", "asyncio", "asyncio.subprocess", "anyio", "os", "pty")


def _aliases(tree: ast.AST) -> dict[str, str]:
    """``local name -> dotted target`` for every import of a spawn module or
    of a function from one (`import subprocess as sp`, `from subprocess
    import run as r`, `from asyncio import create_subprocess_exec`)."""
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _SPAWN_MODULES:
                    found[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in _SPAWN_MODULES:
            for alias in node.names:
                found[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return found


def _spawn_kind(call: ast.Call, aliases: dict[str, str]) -> str | None:
    """The spawn ``call`` makes, as ``module.function``, or None."""
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in (
        "subprocess_exec",
        "subprocess_shell",
    ):
        return f"loop.{func.attr}"  # loop.subprocess_exec(protocol, ...)
    if isinstance(func, ast.Name) and func.id == "stdio_client":
        return "stdio_client"
    dotted = _dotted_name(func)
    if dotted is None:
        return None
    head, _, rest = dotted.partition(".")
    target = aliases.get(head)
    resolved = f"{target}.{rest}" if target and rest else (target or dotted)
    module, _, function = resolved.rpartition(".")
    if module == "asyncio.subprocess":
        module = "asyncio"
    if (module, function) in _SPAWNS:
        return f"{module}.{function}"
    if module == "os" and function.startswith(("exec", "spawn", "posix_spawn", "fork")):
        return f"os.{function}"
    return None


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _dotted_name(node.value)
        return None if owner is None else f"{owner}.{node.attr}"
    return None


def _spawn_calls() -> list[tuple[str, ast.Call, str]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC).as_posix()
        aliases = _aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                kind = _spawn_kind(node, aliases)
                if kind is not None:
                    found.append((rel, node, kind))
    return found


def _detached(call: ast.Call, keywords: dict[str, ast.expr]) -> bool:
    """`start_new_session=True`, or the `**detached()` splat."""
    session = keywords.get("start_new_session")
    if isinstance(session, ast.Constant) and session.value is True:
        return True
    return any(
        k.arg is None
        and isinstance(k.value, ast.Call)
        and getattr(k.value.func, "id", getattr(k.value.func, "attr", None))
        == "detached"
        for k in call.keywords
    )


def _streams_problem(call: ast.Call, kind: str) -> str | None:
    """Why ``call``'s child can reach pmcp's terminal, or None: every spawn
    is detached from the controlling terminal (round 5), takes stdin from
    ``DEVNULL`` or a pipe it owns (round 5), and has stdout and stderr
    piped or discarded (round 4)."""
    keywords = {k.arg: k.value for k in call.keywords if k.arg}
    if kind == "stdio_client":
        # The SDK detaches the child and owns its stdin
        # (`test_the_sdks_stdio_spawn_is_detached_with_a_piped_stdin`).
        errlog = keywords.get("errlog")
        if errlog is None or ast.unparse(errlog) in ("sys.stderr", "sys.stdout"):
            return "stdio_client without a piped errlog"
        return None
    if kind in ("os.system", "os.popen", "pty.spawn") or kind.startswith(
        ("os.exec", "os.spawn", "os.posix_spawn", "os.fork")
    ):
        return f"{kind} always shares the terminal"
    if not _detached(call, keywords):
        return "not detached from the controlling terminal"
    stdin = keywords.get("stdin")
    if not (isinstance(stdin, ast.Attribute) and stdin.attr in ("PIPE", "DEVNULL")):
        return f"stdin={ast.unparse(stdin) if stdin is not None else 'inherited'}"
    if (
        isinstance(keywords.get("capture_output"), ast.Constant)
        and keywords["capture_output"].value is True
    ):
        return None
    for stream in ("stdout", "stderr"):
        value = keywords.get(stream)
        target = value.attr if isinstance(value, ast.Attribute) else None
        if target not in _SAFE_TARGETS:
            return (
                f"{stream}={ast.unparse(value) if value is not None else 'inherited'}"
            )
    return None


def test_no_spawn_lets_a_child_write_to_pmcps_streams() -> None:
    calls = _spawn_calls()
    assert len(calls) >= 10, calls  # not vacuous
    problems = {
        (rel, call.lineno): problem
        for rel, call, kind in calls
        if (problem := _streams_problem(call, kind)) is not None
    }
    assert sorted(set(problems) - set(_INHERITING_ALLOWED)) == [], problems


def test_the_spawn_guard_sees_each_shape() -> None:
    def problem(source: str, imports: str = "") -> str | None:
        tree = ast.parse(imports + "\n" + source)
        call = tree.body[-1].value  # type: ignore[attr-defined]
        kind = _spawn_kind(call, _aliases(tree))
        assert kind is not None, source
        return _streams_problem(call, kind)

    ok = "stdin=subprocess.DEVNULL, **detached()"
    assert problem(f"subprocess.run(['x'], capture_output=True, {ok})") is None
    assert problem(
        "subprocess.run(['x'], capture_output=True, stdin=subprocess.DEVNULL)"
    ) == ("not detached from the controlling terminal")
    assert problem("subprocess.run(['x'], capture_output=True, **detached())") == (
        "stdin=inherited"
    )
    assert (
        problem(
            "subprocess.run(['x'], capture_output=True, stdin=subprocess.DEVNULL,"
            " start_new_session=True)"
        )
        is None
    )
    assert problem(f"subprocess.run(['x'], {ok})") == "stdout=inherited"
    assert problem(f"subprocess.run(['x'], stdout=subprocess.PIPE, {ok})") == (
        "stderr=inherited"
    )
    assert (
        problem(
            f"subprocess.run(['x'], stdout=subprocess.PIPE, stderr=sys.stderr, {ok})"
        )
        == "stderr=sys.stderr"
    )
    assert (
        problem(
            "asyncio.create_subprocess_exec('x', stdin=asyncio.subprocess.PIPE,"
            " stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,"
            " **detached())"
        )
        is None
    )
    assert problem("stdio_client(params)") == "stdio_client without a piped errlog"
    assert problem("stdio_client(params, errlog=sys.stderr)") is not None
    assert problem("stdio_client(params, errlog=errlog)") is None
    assert problem("os.system('x')") is not None
    # Round 6, claude N-C: aliased imports and the asyncio.subprocess forms.
    assert problem("sp.run(['x'])", "import subprocess as sp") == (
        "not detached from the controlling terminal"
    )
    assert problem("r(['x'])", "from subprocess import run as r") == (
        "not detached from the controlling terminal"
    )
    assert problem("asyncio.subprocess.create_subprocess_exec('x')") is not None
    assert (
        problem("cse('x')", "from asyncio import create_subprocess_exec as cse")
        is not None
    )
    assert problem("loop.subprocess_exec(proto, 'x')") is not None
    assert problem("os.posix_spawn('x', ['x'], {})") is not None
    assert problem("os.fork()") is not None


def test_the_sdks_stdio_spawn_is_detached_with_a_piped_stdin() -> None:
    """`stdio_client` (refresh) spawns through the SDK, which starts a new
    session and pipes stdin on POSIX: read from the installed source."""
    import mcp.client.stdio as stdio

    source = Path(stdio.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    opens = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", "") == "open_process"
    ]
    assert opens, "the SDK no longer spawns through anyio.open_process"
    for call in opens:
        keywords = {k.arg: ast.unparse(k.value) for k in call.keywords}
        assert keywords.get("start_new_session") == "True", keywords
        # Not passed: anyio's own default, a pipe.
        assert keywords.get("stdin", "PIPE").endswith("PIPE"), keywords
    import inspect

    import anyio

    default = inspect.signature(anyio.open_process).parameters["stdin"].default
    assert default == subprocess.PIPE


def test_the_display_stream_hands_out_no_descriptor() -> None:
    from pmcp.display import DisplaySafeStream

    stream = DisplaySafeStream(sys.__stderr__)
    with pytest.raises(io.UnsupportedOperation):
        stream.fileno()


# --- one real process per spawn class ------------------------------------------

#: A minimal MCP stdio server: it answers initialize and tools/list, and
#: writes the hostile text to its stdout (as a log line, not a frame) and its
#: stderr first.
_SERVER = textwrap.dedent(
    """
    import json, sys
    sys.stderr.write({hostile!r} + "\\n"); sys.stderr.flush()
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        if msg["method"] == "initialize":
            result = {{"protocolVersion": msg["params"]["protocolVersion"],
                      "capabilities": {{"tools": {{}}}},
                      "serverInfo": {{"name": "hostile", "version": "1"}}}}
        elif msg["method"] == "tools/list":
            result = {{"tools": [{{"name": "t", "description": "d",
                                  "inputSchema": {{"type": "object"}}}}]}}
        else:
            result = {{}}
        sys.stdout.write(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": result}}) + "\\n")
        sys.stdout.flush()
    """
).format(hostile=_HOSTILE)

#: A fake executable (uv, systemctl): writes the hostile text to both streams.
_FAKE_TOOL = textwrap.dedent(
    """
    import sys
    sys.stdout.write({hostile!r} + "\\n")
    sys.stderr.write({hostile!r} + "\\n")
    """
).format(hostile=_HOSTILE)


def _run(
    script: str, tmp_path: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    full = {k: v for k, v in os.environ.items() if not k.startswith("PMCP_")}
    full.update(PYTHONPATH=str(SRC.parent), **env)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=tmp_path,
        env=full,
        capture_output=True,
        timeout=120,
    )


def _assert_no_raw(done: subprocess.CompletedProcess[bytes]) -> None:
    for name, data in (("stdout", done.stdout), ("stderr", done.stderr)):
        assert not any(raw in data for raw in _RAW), (name, data[-1500:])


def _hostile_manifest(tmp_path: Path) -> Path:
    server = tmp_path / "server.py"
    server.write_text(_SERVER)
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "servers:\n"
        "  hostile:\n"
        "    description: a test server\n"
        f"    command: {json.dumps(sys.executable)}\n"
        f"    args: [{json.dumps(str(server))}]\n"
        "    keywords: [hostile]\n"
    )
    return overlay


def test_grok_f001_refresh_child_stderr_reaches_no_terminal(tmp_path: Path) -> None:
    """`pmcp refresh` through real `cli.main`, in a fresh process: the child's
    stderr is piped and logged escaped, never inherited."""
    overlay = _hostile_manifest(tmp_path)
    done = _run(
        f"""
        import sys
        from pmcp import cli
        sys.argv = ["pmcp", "refresh", "--server", "hostile", "--force",
                    "--cache-dir", {str(tmp_path / "cache")!r}, "-l", "debug"]
        cli.main()
        """,
        tmp_path,
        {"PMCP_MANIFEST_PATH": str(overlay), "HOME": str(tmp_path)},
    )
    _assert_no_raw(done)
    assert b"SENTINEL" in done.stderr, done.stderr[-1500:]  # it ran, and was logged


def test_gateway_startup_refresh_child_stderr_reaches_no_terminal(
    tmp_path: Path,
) -> None:
    """The gateway's startup cache generation (no cache yet) spawns the same
    way: through `refresh_all` with the gateway's connected servers."""
    overlay = _hostile_manifest(tmp_path)
    config = tmp_path / "custom.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "hostile": {
                        "command": sys.executable,
                        "args": [str(tmp_path / "server.py")],
                    }
                },
                "autoStart": ["hostile"],
            }
        )
    )
    done = _run(
        f"""
        import asyncio, logging, sys
        from pathlib import Path
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
        from pmcp.server import GatewayServer

        async def main():
            server = GatewayServer(custom_config_path=Path({str(config)!r}),
                                   cache_dir=Path({str(tmp_path / "cache")!r}))
            await server.initialize()
            await server.shutdown()

        asyncio.run(main())
        """,
        tmp_path,
        {"PMCP_MANIFEST_PATH": str(overlay), "HOME": str(tmp_path)},
    )
    _assert_no_raw(done)
    assert b"Auto-generating descriptions cache" in done.stderr, done.stderr[-2000:]


def _fake_tool_dir(tmp_path: Path, *names: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in names:
        tool = bin_dir / name
        tool.write_text(f"#!{sys.executable}\n{_FAKE_TOOL}")
        tool.chmod(0o755)
    return bin_dir


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX semantics")
def test_upgrade_child_output_reaches_no_terminal(tmp_path: Path) -> None:
    bin_dir = _fake_tool_dir(tmp_path, "uv")
    done = _run(
        """
        import sys
        from pmcp import cli
        cli.detect_install_drift = lambda: type("D", (), {"has_drift": False})()
        sys.argv = ["pmcp", "upgrade", "--method", "uv"]
        cli.main()
        """,
        tmp_path,
        {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
        },
    )
    _assert_no_raw(done)
    assert b"SENTINEL" in done.stdout, done.stdout[-1500:]  # echoed, escaped


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX semantics")
def test_service_restart_child_output_reaches_no_terminal(tmp_path: Path) -> None:
    bin_dir = _fake_tool_dir(tmp_path, "systemctl")
    done = _run(
        """
        import types
        from pmcp import cli
        cli.Path = lambda *a: types.SimpleNamespace(exists=lambda: True)
        cli._restart_local_pmcp_service()
        """,
        tmp_path,
        {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
        },
    )
    _assert_no_raw(done)
    assert b"SENTINEL" in done.stdout, done.stdout[-1500:]


# --- claude N-A: an exception before the command runs ---------------------------


def test_an_exception_from_startup_is_escaped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pmcp import cli

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"from a store reader {_HOSTILE}")

    monkeypatch.setattr(cli, "serve_project", boom)
    monkeypatch.setattr(sys, "argv", ["pmcp", "status"])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "Fatal error" in err and "SENTINEL" in err, err
    assert not any(ch in err for ch in ("\x1b", "\x9b", "‮")), ascii(err)


# --- round 5: the controlling terminal and stdin, under a real pty -------------

#: The child: tries the controlling terminal (`/dev/tty`) and fd 0, writes
#: ESC and U+009B to each, and records what it managed in a marker file.
_TTY_CHILD = textwrap.dedent(
    """
    import os, sys
    marker = sys.argv[1]
    payload = "SENTINEL\\x1b[31m\\x9b2J\\u202e\\n".encode()
    outcome = []
    try:
        fd = os.open("/dev/tty", os.O_WRONLY)
        os.write(fd, payload)
        os.close(fd)
        outcome.append("tty-written")
    except OSError:
        outcome.append("no-tty")
    outcome.append("fd0-is-a-tty" if os.isatty(0) else "fd0-not-a-tty")
    try:
        os.write(0, payload)  # /dev/null (DEVNULL is read-write) or a pipe
    except OSError:
        pass
    with open(marker, "w") as handle:
        handle.write(" ".join(outcome))
    """
)

#: How each spawn site is driven from the parent, inside the pty session.
_PTY_DRIVERS = {
    "check_cli": (
        "from pmcp.manifest import environment\n"
        "asyncio.run(environment.check_cli('probe', CHILD))\n"
    ),
    "start_install": (
        "from pmcp.manifest import installer\n"
        "from pmcp.manifest.loader import ServerConfig\n"
        "async def run():\n"
        "    server = ServerConfig(name='memory', description='memory',\n"
        "        keywords=['memory'], install={'linux': CHILD},\n"
        "        command=CHILD[0], args=CHILD[1:], requires_api_key=False)\n"
        "    await installer.JobManager().start_install(server, 'linux')\n"
        "    for _ in range(100):\n"
        "        if os.path.exists(MARKER):\n"
        "            break\n"
        "        await asyncio.sleep(0.05)\n"
        "asyncio.run(run())\n"
    ),
    "run_echoed": ("from pmcp.child_output import run_echoed\nrun_echoed(CHILD)\n"),
    "update_probe": (
        "from pmcp.tools.handlers import GatewayTools\n"
        "tools = GatewayTools.__new__(GatewayTools)\n"
        "asyncio.run(tools._run_update_probe_command(CHILD, env=None))\n"
    ),
    "client_manager": (
        "from pmcp.client.manager import ClientManager\n"
        "from pmcp.config.loader import ResolvedServerConfig\n"
        "from pmcp.types import LocalMcpServerConfig\n"
        "async def run():\n"
        "    config = ResolvedServerConfig(name='s388', source='custom',\n"
        "        config=LocalMcpServerConfig(command=CHILD[0], args=CHILD[1:]))\n"
        "    try:\n"
        "        await asyncio.wait_for(ClientManager()._connect_stdio(config), 10)\n"
        "    except BaseException:\n"
        "        pass\n"
        "asyncio.run(run())\n"
    ),
}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty")
@pytest.mark.parametrize("site", sorted(_PTY_DRIVERS))
def test_a_child_reaches_neither_the_controlling_terminal_nor_stdin(
    site: str, tmp_path: Path
) -> None:
    """The parent runs a spawn site with a pty as its controlling terminal
    and its stdin, stdout and stderr; the child tries `/dev/tty` and fd 0.
    Nothing it writes reaches the pty (round 5, grok and codex F001)."""
    import fcntl
    import pty  # noqa: F401 - POSIX only
    import select
    import termios

    child = tmp_path / "child.py"
    child.write_text(_TTY_CHILD)
    marker = tmp_path / "marker"
    script = (
        "import asyncio, os, sys\n"
        f"CHILD = [sys.executable, {str(child)!r}, {str(marker)!r}]\n"
        f"MARKER = {str(marker)!r}\n" + _PTY_DRIVERS[site]
    )
    master, slave = os.openpty()

    def controlling_terminal() -> None:
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    env = {k: v for k, v in os.environ.items() if not k.startswith("PMCP_")}
    env.update(PYTHONPATH=str(SRC.parent), HOME=str(tmp_path))
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=tmp_path,
        env=env,
        preexec_fn=controlling_terminal,  # noqa: PLW1509 - the test's own pty
    )
    os.close(slave)
    seen = bytearray()
    try:
        while True:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                seen += data
            elif process.poll() is not None:
                break
        process.wait(timeout=60)
    finally:
        os.close(master)
    assert marker.exists(), (site, bytes(seen[-2000:]))
    outcome = marker.read_text()
    assert outcome.split() == ["no-tty", "fd0-not-a-tty"], (site, outcome)
    assert b"SENTINEL" not in seen and b"\x1b[31m" not in seen, (
        site,
        bytes(seen[-2000:]),
    )


# --- round 5, claude N-A and N-B: the stderr drain -------------------------------


class _Chunks:
    """A reader yielding ``total`` bytes of ``fill`` in chunks, then ``tail``,
    never holding the whole input."""

    def __init__(self, total: int, fill: bytes, tail: bytes) -> None:
        self.left, self.fill, self.tail = total, fill, tail

    def read1(self, size: int) -> bytes:
        if self.left > 0:
            n = min(size, self.left)
            self.left -= n
            return self.fill * n
        tail, self.tail = self.tail, b""
        return tail


def test_a_long_line_is_capped_in_bounded_memory() -> None:
    """32 MiB with no newline: one capped line with a value-free note, the
    next line intact, and memory near one cap (round 5, claude N-B: 223 MiB)."""
    import tracemalloc

    from pmcp.child_output import LINE_CAP, TRUNCATED, _render, bounded_lines

    tracemalloc.start()
    try:
        lines = list(bounded_lines(_Chunks(32 << 20, b"x", b"\nshort\n")))  # type: ignore[arg-type]
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert [(len(raw), cut) for raw, cut in lines] == [(LINE_CAP, True), (5, False)]
    assert peak < 4 * LINE_CAP + (1 << 20), peak
    assert _render(*lines[0]).endswith(TRUNCATED)
    assert _render(*lines[1]) == "short"


def test_lines_split_across_chunks_are_whole() -> None:
    import io

    from pmcp.child_output import bounded_lines

    data = b"a\r\nbb\n\nccc" + b"d" * 70000 + b"\nlast"
    got = list(bounded_lines(io.BytesIO(data)))
    assert [raw[:3] for raw, _cut in got] == [b"a\r", b"bb", b"", b"cccd"[:3], b"las"]
    assert got[3][1] is True and got[4] == (b"last", False)


@pytest.mark.asyncio
async def test_the_drain_waits_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A grandchild that keeps the pipe open: leaving the context waits for
    the drain off the loop, so the loop keeps running (round 5, claude N-A),
    and lines are logged escaped and capped."""
    import asyncio
    import logging
    import time

    from pmcp import child_output

    monkeypatch.setattr(child_output, "_DRAIN_JOIN_SECONDS", 1.0)
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("pmcp.test.drain")
    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)  # the ticker is running before the drain starts
    held: int | None = None
    try:
        async with child_output.drained_errlog("srv", logger) as errlog:
            errlog.write(f"hello {_HOSTILE}\n")
            errlog.flush()
            held = os.dup(errlog.fileno())  # a grandchild's copy of the write end
        await asyncio.sleep(0.1)
    finally:
        stop.set()
        await task
        if held is not None:
            os.close(held)
    assert max(gaps) < 0.5, max(gaps)
    text = "\n".join(
        r.getMessage() for r in caplog.records if r.name == "pmcp.test.drain"
    )
    assert "[srv] stderr: hello SENTINEL" in text, text
    assert "\x1b" not in text and "\x9b" not in text


# --- round 6, claude N-C: the audit-hook backstop ---------------------------------


def _run_probe(tmp_path: Path, source: str) -> list[tuple[str, str]]:
    """Run ``source`` as if it were pmcp code: compiled under a root the
    audit hook treats as pmcp's. Returns the violations it recorded, and
    removes them so a deliberate one does not fail the session."""
    from tests import conftest

    root = str(tmp_path) + os.sep
    probe = tmp_path / "_audit_probe.py"
    before = len(conftest.SPAWN_VIOLATIONS)
    conftest.SPAWN_AUDIT_EXTRA_ROOTS.append(root)
    try:
        exec(compile(source, str(probe), "exec"), {})  # noqa: S102 - the probe
    finally:
        conftest.SPAWN_AUDIT_EXTRA_ROOTS.remove(root)
    caught = conftest.SPAWN_VIOLATIONS[before:]
    del conftest.SPAWN_VIOLATIONS[before:]
    return caught


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX semantics")
def test_the_audit_backstop_records_an_undetached_spawn_from_pmcp_code(
    tmp_path: Path,
) -> None:
    """A spawn the static guard cannot see (here, behind `getattr`), from a
    frame the hook treats as pmcp's own source, is recorded by the session's
    audit hook. The probe lives in a temporary root, not `src/pmcp`:
    coverage would report a file there."""
    caught = _run_probe(
        tmp_path,
        "import subprocess, sys\n"
        "spawn = getattr(subprocess, 'run')\n"
        "spawn([sys.executable, '-c', 'pass'])\n",
    )
    assert len(caught) == 1, caught
    where, problem = caught[0]
    assert where.startswith("_audit_probe.py:"), where
    assert "not detached" in problem and "stdin inherited" in problem, problem
    assert "stdout inherited" in problem and "stderr inherited" in problem, problem


def test_the_audit_backstop_passes_a_detached_spawn_from_pmcp_code(
    tmp_path: Path,
) -> None:
    caught = _run_probe(
        tmp_path,
        "import subprocess, sys\n"
        "subprocess.run([sys.executable, '-c', 'pass'], stdin=subprocess.DEVNULL,\n"
        "    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)\n",
    )
    assert caught == []


# --- round 6: the drain releases everything; groups are killed whole ---------------

#: A child that starts a grandchild (recording its pid), optionally traps
#: SIGINT (recording that it ran), and then waits.
_FAMILY = textwrap.dedent(
    """
    import os, signal, subprocess, sys, time
    out = sys.argv[1]
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(os.path.join(out, "grandchild"), "w") as handle:
        handle.write(str(grandchild.pid))
    def trapped(signum, frame):
        with open(os.path.join(out, "trapped"), "w") as handle:
            handle.write("yes")
        sys.exit(130)
    signal.signal(signal.SIGINT, trapped)
    time.sleep(60)
    """
)


def _gone(pid: int, within: float = 5.0) -> bool:
    import time

    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        # A zombie still answers signal 0: reaped by init shortly.
        try:
            with open(f"/proc/{pid}/stat") as handle:
                if handle.read().split(")")[-1].split()[0] == "Z":
                    return True
        except OSError:
            return True
        time.sleep(0.05)
    return False


def _wait_for(path: Path, within: float = 10.0) -> None:
    import time

    deadline = time.monotonic() + within
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert path.exists(), path


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_ctrl_c_during_run_echoed_reaches_the_child_and_its_group(
    tmp_path: Path,
) -> None:
    """Round 6, claude N-A: a detached child no longer gets the terminal's
    SIGINT. `run_echoed` forwards it to the child's group, so the child's
    trap runs, and then kills what is left, so its grandchild is gone."""
    import signal

    child = tmp_path / "family.py"
    child.write_text(_FAMILY)
    script = (
        "import signal, sys\n"
        # A test run in the background (`cmd &`) starts with SIGINT ignored;
        # an operator's pmcp gets Python's own handler.
        "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
        "from pmcp.child_output import run_echoed\n"
        f"run_echoed([sys.executable, {str(child)!r}, {str(tmp_path)!r}])\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("PMCP_")}
    env.update(PYTHONPATH=str(SRC.parent), HOME=str(tmp_path))
    parent = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for(tmp_path / "grandchild")
        grandchild = int((tmp_path / "grandchild").read_text())
        os.kill(parent.pid, signal.SIGINT)
        parent.communicate(timeout=30)
    finally:
        if parent.poll() is None:
            parent.kill()
    assert (tmp_path / "trapped").exists(), "the child's SIGINT trap never ran"
    assert _gone(grandchild), "the child's grandchild outlived the interrupt"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_an_install_that_times_out_is_killed_with_its_group(
    tmp_path: Path,
) -> None:
    """Round 6, claude N-B: `install_server`'s timeout kills the install
    command's whole group, so a grandchild it started (an npm lifecycle
    script, a node-gyp build) does not keep running."""
    from pmcp.manifest import installer
    from pmcp.manifest.loader import ServerConfig

    child = tmp_path / "family.py"
    child.write_text(_FAMILY)
    command = [sys.executable, str(child), str(tmp_path)]
    server = ServerConfig(
        name="memory",
        description="memory",
        keywords=["memory"],
        install={"linux": command},
        command=command[0],
        args=command[1:],
        requires_api_key=False,
    )
    with pytest.raises(installer.InstallError):
        await installer.install_server(server, "linux", timeout=1.5)
    grandchild = int((tmp_path / "grandchild").read_text())
    assert _gone(grandchild), "the install's grandchild outlived its timeout"


def _open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


@pytest.mark.skipif(not Path("/proc/self/fd").exists(), reason="Linux /proc")
@pytest.mark.asyncio
async def test_the_drain_releases_its_thread_and_fds_while_a_descendant_lives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 6, codex F001: three errlog contexts, each with a descendant
    that keeps the pipe's write end open past the context. Afterwards no
    drain thread and no read end remain."""
    import logging
    import threading

    from pmcp import child_output

    monkeypatch.setattr(child_output, "_DRAIN_JOIN_SECONDS", 0.3)
    logger = logging.getLogger("pmcp.test.drain")
    baseline_fds = _open_fds()
    baseline_threads = {t.name for t in threading.enumerate()}
    descendants = []
    try:
        for _ in range(3):
            async with child_output.drained_errlog("srv", logger) as errlog:
                descendants.append(
                    subprocess.Popen(
                        [sys.executable, "-c", "import time; time.sleep(60)"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=errlog,
                        start_new_session=True,
                    )
                )
        assert all(d.poll() is None for d in descendants)  # still holding the pipe
        drains = [t for t in threading.enumerate() if t.name.startswith("pmcp-errlog-")]
        assert drains == [], drains
        # Only the loop's own worker pool may have grown (anyio's idle
        # workers, which ran the joins); no drain thread remains.
        extra = {t.name for t in threading.enumerate()} - baseline_threads
        assert extra <= {"AnyIO worker thread"}, extra
        assert _open_fds() <= baseline_fds, (_open_fds(), baseline_fds)
    finally:
        for d in descendants:
            d.kill()
            d.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_kill_tree_signals_no_group_for_an_object_that_only_has_a_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test double or adopted handle with a `pid` is killed through its
    own `kill`, never `killpg` -- even when that pid leads a real group: in
    CI a double's `pid = 4321` named a real process group, and the group
    kill ended the test run."""
    from pmcp import child_output

    leader = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        groups: list[int] = []
        monkeypatch.setattr(os, "killpg", lambda pgid, sig: groups.append(pgid))

        class _Double:
            pid = leader.pid  # a real group leader, not this object's
            returncode = None
            killed = 0

            def kill(self) -> None:
                self.killed += 1

            def terminate(self) -> None:
                self.killed += 1

        double = _Double()
        child_output.kill_tree(double)
        child_output.kill_tree(double, terminate=True)
        assert groups == []
        assert double.killed == 2
        assert leader.poll() is None
    finally:
        leader.kill()
        leader.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_kill_tree_signals_no_group_once_the_child_is_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once reaped, a child's pid can be reused as another group's id: no
    group signal then (Consiliency/pmcp#324's rule)."""
    from pmcp import child_output

    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    process.wait()
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))
    # As if the pid had been reused: a live process holds it.
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    child_output.kill_tree(process)
    # At most the membership probe (signal 0); never a real signal.
    assert [sig for _pgid, sig in signals if sig != 0] == []


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="POSIX waitid")
def test_a_child_reaped_behind_returncode_is_not_signalled_as_a_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 7, claude N-B: `returncode` unset is not enough -- on 3.10/3.11
    asyncio's watcher thread reaps before it is set. A child the kernel has
    reaped (here by a raw `waitpid`) is not signalled as a group leader."""
    from pmcp import child_output

    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    os.waitpid(process.pid, 0)  # reaped behind Popen's back: returncode None
    assert process.returncode is None
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)  # the pid reused
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)
    child_output.kill_tree(process)
    assert [sig for _pgid, sig in signals if sig != 0] == []


# --- round 7: the one kill rule; Windows; the drain under cancellation -----------

#: A leader that starts a grandchild holding its stdout and stderr, records
#: the grandchild's pid, and exits at once: `communicate()` then waits for
#: the grandchild and times out (round 7, grok F001 / codex F002).
_LEADER_EXITS_FIRST = textwrap.dedent(
    """
    import os, subprocess, sys
    out = sys.argv[1]
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
    )
    with open(os.path.join(out, "grandchild"), "w") as handle:
        handle.write(str(grandchild.pid))
    """
)


def _leader_exits_first(tmp_path: Path) -> list[str]:
    script = tmp_path / "leader.py"
    script.write_text(_LEADER_EXITS_FIRST)
    return [sys.executable, str(script), str(tmp_path)]


def _grandchild_gone(tmp_path: Path, where: str) -> None:
    """Assert the leader's grandchild is gone; if not, end it -- only after
    confirming from its cmdline that the pid is still ours -- so a failing
    test leaves nothing running."""
    import contextlib
    import signal

    pid = int((tmp_path / "grandchild").read_text())
    if _gone(pid):
        return
    try:
        ours = b"time.sleep(60)" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        ours = False
    if ours:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
    raise AssertionError(f"{where}: the grandchild outlived the kill")


def _short_waits(monkeypatch: pytest.MonkeyPatch, module: object) -> None:
    from pmcp import waits

    real = waits.bounded_wait

    async def short(aw: object, timeout: float | None) -> object:
        return await real(aw, 1.0)  # type: ignore[arg-type]

    monkeypatch.setattr(module, "bounded_wait", short)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
@pytest.mark.parametrize("probe", ["check_cli", "get_cli_help"])
async def test_a_timed_out_probe_whose_leader_exited_first_leaves_no_grandchild(
    probe: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.manifest import environment

    _short_waits(monkeypatch, environment)
    command = _leader_exits_first(tmp_path)
    if probe == "check_cli":
        await environment.check_cli("probe", command)
    else:
        await environment.get_cli_help("probe", command)
    _grandchild_gone(tmp_path, probe)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_an_install_timeout_whose_leader_exited_first_leaves_no_grandchild(
    tmp_path: Path,
) -> None:
    from pmcp.manifest import installer
    from pmcp.manifest.loader import ServerConfig

    command = _leader_exits_first(tmp_path)
    server = ServerConfig(
        name="memory",
        description="memory",
        keywords=["memory"],
        install={"linux": command},
        command=command[0],
        args=command[1:],
        requires_api_key=False,
    )
    with pytest.raises(installer.InstallError):
        await installer.install_server(server, "linux", timeout=1.0)
    _grandchild_gone(tmp_path, "install_server")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_a_stalled_install_whose_leader_exited_first_leaves_no_grandchild(
    tmp_path: Path,
) -> None:
    """`JobManager._monitor_install`'s stall path terminates through
    `_safe_terminate_process`, which skipped an exited leader."""
    import asyncio

    from pmcp.child_output import detached, track
    from pmcp.manifest import installer

    command = _leader_exits_first(tmp_path)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **detached(),
    )
    track(process)  # as every spawn site does: its group is trusted
    # The leader exits; the grandchild holds the pipes. `returncode` is set
    # at the exit (`wait()` itself, on 3.11+, waits for the pipes to close).
    deadline = asyncio.get_running_loop().time() + 10
    while process.returncode is None:
        assert asyncio.get_running_loop().time() < deadline, "the leader never exited"
        await asyncio.sleep(0.02)
    _wait_for(tmp_path / "grandchild")
    await installer.JobManager()._safe_terminate_process(process, "job", force=True)
    _grandchild_gone(tmp_path, "_safe_terminate_process")


@pytest.mark.skipif(not Path("/proc/self/fd").exists(), reason="Linux /proc")
@pytest.mark.asyncio
@pytest.mark.parametrize("at", ["body", "first-join", "second-join"])
async def test_a_cancelled_drain_releases_its_thread_and_fds(
    at: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 7, codex F001: cancellation at each await point -- the body,
    the first join, the join after the stop -- still stops the drain and
    releases its read end, with a descendant holding the pipe open."""
    import asyncio
    import logging
    import threading
    import time

    import anyio

    from pmcp import child_output

    monkeypatch.setattr(child_output, "_DRAIN_JOIN_SECONDS", 0.5)
    real_run_sync = anyio.to_thread.run_sync
    calls = 0

    async def cancelling_run_sync(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if (at == "first-join" and calls == 1) or (at == "second-join" and calls == 2):
            raise asyncio.CancelledError
        return await real_run_sync(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(anyio.to_thread, "run_sync", cancelling_run_sync)
    logger = logging.getLogger("pmcp.test.drain")
    baseline_fds = _open_fds()
    descendant: subprocess.Popen[bytes] | None = None

    async def use() -> None:
        nonlocal descendant
        async with child_output.drained_errlog("srv", logger) as errlog:
            descendant = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=errlog,
                start_new_session=True,
            )
            await asyncio.sleep(30 if at == "body" else 0)

    task = asyncio.create_task(use())
    try:
        if at == "body":
            await asyncio.sleep(0.2)
            task.cancel()
        with contextlib_suppress_cancel():
            await task
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(
            t.name.startswith("pmcp-errlog-") for t in threading.enumerate()
        ):
            await asyncio.sleep(0.05)
        assert not any(t.name.startswith("pmcp-errlog-") for t in threading.enumerate())
        assert descendant is not None and descendant.poll() is None
        assert _open_fds() <= baseline_fds, (_open_fds(), baseline_fds)
    finally:
        if descendant is not None:
            descendant.kill()
            descendant.wait()


def contextlib_suppress_cancel() -> object:
    import asyncio
    import contextlib

    return contextlib.suppress(asyncio.CancelledError)


class _SocketsOnlySelector:
    """Windows' `select` semantics: a pipe's registration is accepted, and
    the wait fails ("not a socket", WinError 10038)."""

    def __init__(self) -> None:
        pass

    def register(self, fileobj: object, events: int, data: object = None) -> None:
        return None

    def select(self, timeout: float | None = None) -> list[object]:
        raise OSError(
            10038, "An operation was attempted on something that is not a socket"
        )

    def close(self) -> None:
        pass


def _posix_peek(fd: int) -> int | None:
    """`PeekNamedPipe`'s answer, from a POSIX pipe: the bytes ready, or
    `None` at the pipe's end (readable with nothing to read)."""
    import fcntl
    import select
    import struct
    import termios

    ready, _, _ = select.select([fd], [], [], 0)
    if not ready:
        return 0
    count = struct.unpack("i", fcntl.ioctl(fd, termios.FIONREAD, b"\0\0\0\0"))[0]
    return count or None


def _windows_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    import selectors

    from pmcp import child_output

    monkeypatch.setattr(child_output, "_windows", lambda: True)
    monkeypatch.setattr(selectors, "DefaultSelector", _SocketsOnlySelector)
    monkeypatch.setattr(child_output, "_peek_pipe", _posix_peek)


@pytest.mark.skipif(sys.platform == "win32", reason="the Windows seam, run on POSIX")
@pytest.mark.asyncio
async def test_the_windows_drain_reads_a_pipe_select_rejects(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 7, codex F003: on Windows the drain does not wait with `select`
    (sockets only); it reads every line and ends at the pipe's end."""
    import logging

    from pmcp import child_output

    _windows_drain(monkeypatch)
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("pmcp.test.drain")
    async with child_output.drained_errlog("srv", logger) as errlog:
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys; [print(f'line {i}', file=sys.stderr) for i in range(50)]",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=errlog,
        )
        child.wait()
    lines = [r.getMessage() for r in caplog.records if r.name == "pmcp.test.drain"]
    assert lines == [f"[srv] stderr: line {i}" for i in range(50)], lines[:3]


@pytest.mark.skipif(not Path("/proc/self/fd").exists(), reason="Linux /proc")
@pytest.mark.asyncio
async def test_the_windows_drain_stops_at_the_deadline_while_a_descendant_holds_the_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 8, codex F004: the Windows drain's lifetime is pmcp's, not the
    downstream's. A descendant that never closes the write end does not keep
    the drain thread or its descriptors past the context's deadline."""
    import logging
    import threading
    import time

    from pmcp import child_output

    _windows_drain(monkeypatch)
    monkeypatch.setattr(child_output, "_DRAIN_JOIN_SECONDS", 0.2)
    baseline = _open_fds()
    logger = logging.getLogger("pmcp.test.drain")
    async with child_output.drained_errlog("leak", logger) as errlog:
        descendant = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=errlog,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and any(
            t.name == "pmcp-errlog-leak" for t in threading.enumerate()
        ):
            time.sleep(0.02)
        assert not any(t.name == "pmcp-errlog-leak" for t in threading.enumerate()), (
            "the Windows drain outlived its deadline"
        )
        assert descendant.poll() is None  # the writer never closed
        assert _open_fds() <= baseline
    finally:
        descendant.kill()
        descendant.wait()


def test_the_windows_reader_ends_at_the_stop_with_a_writer_that_never_closes() -> None:
    """The polling reader against a fake pipe whose writer never closes and
    never writes: it ends within a poll of the stop, and reads what was
    ready first."""
    import threading

    from pmcp import child_output

    stop = threading.Event()
    reader = child_output._PollingReader(-1, stop)
    looks = {"n": 0}

    def never_closing(fd: int) -> int:
        looks["n"] += 1
        if looks["n"] == 3:
            stop.set()
        return 0

    original = child_output._peek_pipe
    child_output._peek_pipe = never_closing  # type: ignore[assignment]
    try:
        assert reader.read1(1024) == b""
        assert reader.read1(1024) == b""
    finally:
        child_output._peek_pipe = original  # type: ignore[assignment]
    assert looks["n"] == 4  # three looks, the stop, one last look


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX semantics")
def test_the_last_bytes_are_read_when_data_and_the_stop_are_both_ready() -> None:
    """Round 7 nit: the stop does not drop what the child already wrote."""
    from pmcp.child_output import _StoppableReader, bounded_lines

    data_read, data_write = os.pipe()
    stop_read, stop_write = os.pipe()
    try:
        os.write(data_write, b"last words\n")
        os.close(stop_write)  # the stop, already signalled
        reader = _StoppableReader(data_read, stop_read)
        assert list(bounded_lines(reader)) == [(b"last words", False)]  # type: ignore[arg-type]
        reader.close()
    finally:
        for fd in (data_read, data_write, stop_read):
            with __import__("contextlib").suppress(OSError):
                os.close(fd)


class _FakeKernel32:
    """Records the Win32 calls pmcp makes; never a console control event."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        def call(*args: object) -> int:
            self.calls.append(name)
            return {"CreateJobObjectW": 77, "OpenProcess": 5}.get(name, 1)

        return call


def test_windows_interrupt_and_kill_terminate_the_job_never_a_console_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 7, claude N-A: on Windows each child goes in a Job Object at
    spawn; an interrupt waits briefly and terminates the job; nothing sends
    `GenerateConsoleCtrlEvent` (or `CTRL_BREAK_EVENT`)."""
    from pmcp import child_output

    monkeypatch.setattr(child_output, "_JOBS", {})

    kernel32 = _FakeKernel32()
    monkeypatch.setattr(child_output, "_windows", lambda: True)
    monkeypatch.setattr(child_output, "_kernel32", lambda: kernel32)
    monkeypatch.setattr(child_output, "_WINDOWS_INTERRUPT_WAIT_SECONDS", 0.01)

    class _Process:
        pid = 4242
        returncode = None
        signals: list[object] = []

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("x", timeout or 0)

        def send_signal(self, sig: object) -> None:
            self.signals.append(sig)

        def kill(self) -> None:
            self.signals.append("kill")

        def poll(self) -> None:
            return None

    process = _Process()
    child_output.track(process)
    assert kernel32.calls[:5] == [
        "CreateJobObjectW",
        "OpenProcess",
        "SetInformationJobObject",
        "AssignProcessToJobObject",
        "CloseHandle",
    ]
    child_output.interrupt_tree(process)  # type: ignore[arg-type]
    assert "TerminateJobObject" in kernel32.calls
    assert "GenerateConsoleCtrlEvent" not in kernel32.calls
    assert process.signals == []  # neither CTRL_BREAK_EVENT nor a bare kill

    kernel32.calls.clear()
    child_output.track(process)
    child_output.kill_tree(process)
    assert "TerminateJobObject" in kernel32.calls


def test_windows_without_a_job_kills_the_child_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If job assignment fails (a nested-job restriction), the kill falls
    back to the child alone."""
    from pmcp import child_output

    monkeypatch.setattr(child_output, "_JOBS", {})

    class _Refusing(_FakeKernel32):
        def __getattr__(self, name: str) -> object:
            if name == "AssignProcessToJobObject":
                return lambda *a: 0
            return super().__getattr__(name)

    monkeypatch.setattr(child_output, "_windows", lambda: True)
    monkeypatch.setattr(child_output, "_kernel32", lambda: _Refusing())

    class _Process:
        pid = 4343
        returncode = None
        killed = False

        def kill(self) -> None:
            self.killed = True

    process = _Process()
    child_output.track(process)
    child_output.kill_tree(process)
    assert process.killed


@pytest.mark.asyncio
async def test_windows_server_teardown_terminates_the_job_when_the_leader_exits_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ClientManager`'s teardown on Windows terminates the server's Job
    Object at the first signal: a leader that exits within the wait no
    longer leaves what it started running (round 7, claude N-A)."""
    from pmcp import child_output
    from pmcp.client import manager

    kernel32 = _FakeKernel32()
    monkeypatch.setattr(child_output, "_JOBS", {})
    monkeypatch.setattr(child_output, "_windows", lambda: True)
    monkeypatch.setattr(child_output, "_kernel32", lambda: kernel32)
    # No real group may be reached from the double's pid.
    monkeypatch.setattr(os, "getpgid", lambda pid: -1, raising=False)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: None, raising=False)

    class _Server:
        pid = 4242
        returncode: int | None = None
        terminated = False

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            pass

        async def wait(self) -> int:
            self.returncode = 0
            return 0

    server = _Server()
    child_output.track(server)
    await manager._terminate_process_tree(server, "srv")  # type: ignore[arg-type]
    assert "TerminateJobObject" in kernel32.calls
    assert not server.terminated


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX semantics")
@pytest.mark.asyncio
async def test_a_teardown_of_an_exited_process_signals_no_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process whose `returncode` is set has been reaped: its pid may be
    anyone's. `ClientManager`'s teardown then reaches the group only through
    the retained-group checks -- never a direct group signal, never the pid
    (round 7, claude N-B; the `returncode` half of `leader_reserved`)."""
    from pmcp.client import manager

    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)  # the pid reused
    monkeypatch.setattr(os, "getpgid", lambda pid: pid)

    class _Exited:
        pid = 4242
        returncode = 0
        signalled = False

        def terminate(self) -> None:
            self.signalled = True

        def kill(self) -> None:
            self.signalled = True

        async def wait(self) -> int:
            return 0

    exited = _Exited()
    await manager._terminate_process_tree(exited, "srv", group_pgid=4242)  # type: ignore[arg-type]
    assert [sig for _pgid, sig in signals if sig != 0] == []
    assert not exited.signalled


# --- round 8 ----------------------------------------------------------------------

#: What Windows' `signal` and `os` lack: evaluated at import, any of them
#: fails `import pmcp` there.
_POSIX_ONLY = {
    "SIGKILL", "SIGHUP", "SIGQUIT", "SIGUSR1", "SIGUSR2", "SIGCHLD", "SIGPIPE",
    "SIGALRM", "SIGSTOP", "SIGCONT", "killpg", "getpgid", "waitid", "WNOHANG",
    "WEXITED", "WNOWAIT", "P_PID", "setsid", "getuid", "geteuid", "fork",
}  # fmt: skip


def _import_time_posix_names(tree: ast.AST) -> list[tuple[int, str]]:
    """`signal.X`/`os.X` for a POSIX-only X, where it is evaluated at import:
    module and class bodies, default arguments, decorators -- not function
    bodies, which run only when called."""
    found: list[tuple[int, str]] = []

    def expressions(node: ast.AST) -> None:
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Attribute)
                and sub.attr in _POSIX_ONLY
                and isinstance(sub.value, ast.Name)
                and sub.value.id in ("signal", "os")
            ):
                found.append((sub.lineno, ast.unparse(sub)))

    def body(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for default in [*child.args.defaults, *child.args.kw_defaults]:
                    if default is not None:
                        expressions(default)
                for decorator in getattr(child, "decorator_list", []):
                    expressions(decorator)
                continue
            if isinstance(child, ast.expr):
                expressions(child)
            else:
                body(child)

    body(tree)
    return found


def test_no_posix_only_name_is_evaluated_when_pmcp_is_imported() -> None:
    """Round 8, codex/grok F001: `signal.SIGKILL` in a default argument made
    `import pmcp` raise `AttributeError` on Windows before any Windows code
    ran. No module in `src/pmcp` evaluates a POSIX-only `signal`/`os` name
    at import (the Windows CI job imports it for real)."""
    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    problems = [
        f"{path.relative_to(root)}:{line}: {text}"
        for path in sorted(root.rglob("*.py"))
        for line, text in _import_time_posix_names(ast.parse(path.read_text()))
    ]
    assert problems == []


def test_the_import_time_guard_sees_a_default_argument() -> None:
    tree = ast.parse(
        "import signal\n"
        "def kill(sig=signal.SIGKILL): pass\n"
        "def fine(): return signal.SIGKILL\n"
        "class C:\n    S = signal.SIGHUP\n"
    )
    assert [text for _line, text in _import_time_posix_names(tree)] == [
        "signal.SIGKILL",
        "signal.SIGHUP",
    ]


@pytest.mark.asyncio
async def test_windows_teardown_terminates_job_when_leader_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 8, codex F002 = gemini F001 = grok F001 (gemini's falsifier): a
    server whose leader already exited still has its Job Object terminated
    at teardown, and the handle is released -- the POSIX retained-group path
    (a no-op on Windows) no longer returns first."""
    from pmcp import child_output
    from pmcp.client import manager

    kernel32 = _FakeKernel32()
    monkeypatch.setattr(child_output, "_JOBS", {})
    monkeypatch.setattr(child_output, "_windows", lambda: True)
    monkeypatch.setattr(child_output, "_kernel32", lambda: kernel32)
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: None, raising=False)

    class _Exited:
        pid = 4242
        returncode: int | None = None

        def kill(self) -> None:
            pass

        async def wait(self) -> int:
            return 0

    server = _Exited()
    child_output.track(server)
    server.returncode = 0  # the leader exits first; a descendant lives on
    await manager._terminate_process_tree(server, "srv", group_pgid=4242)  # type: ignore[arg-type]
    assert "TerminateJobObject" in kernel32.calls
    assert child_output._JOBS == {}  # the handle released
    assert kernel32.calls.count("CloseHandle") >= 2  # the process's, then the job's


_LEADER_STAYS = textwrap.dedent(
    """
    import os, subprocess, sys, time
    out = sys.argv[1]
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
    )
    with open(os.path.join(out, "grandchild"), "w") as handle:
        handle.write(str(grandchild.pid))
    time.sleep(60)
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_a_verification_that_times_out_leaves_nothing_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 8, codex F003: `verify_installation` caught its own timeout and
    returned False with the command and its grandchild still running."""
    from pmcp.manifest import installer
    from pmcp.manifest.loader import ServerConfig

    _short_waits(monkeypatch, installer)
    script = tmp_path / "leader.py"
    script.write_text(_LEADER_STAYS)
    server = ServerConfig(
        name="memory",
        description="memory",
        keywords=["memory"],
        install={"linux": [sys.executable]},
        command=sys.executable,
        args=[str(script)],
        requires_api_key=False,
    )
    # `verify_installation` appends `--help`: the leader reads its out dir
    # from argv[1], so point argv[1] at tmp_path through the script itself.
    script.write_text(_LEADER_STAYS.replace("sys.argv[1]", repr(str(tmp_path))))
    assert await installer.verify_installation(server) is False
    _wait_for(tmp_path / "grandchild")
    _grandchild_gone(tmp_path, "verify_installation")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_a_cancelled_install_leaves_nothing_running(tmp_path: Path) -> None:
    """Round 8, codex F003: `install_server` had no cancellation cleanup; a
    cancelled caller left the install and everything it started running."""
    import asyncio

    from pmcp.manifest import installer
    from pmcp.manifest.loader import ServerConfig

    script = tmp_path / "leader.py"
    script.write_text(_LEADER_STAYS)
    command = [sys.executable, str(script), str(tmp_path)]
    server = ServerConfig(
        name="memory",
        description="memory",
        keywords=["memory"],
        install={"linux": command},
        command=command[0],
        args=command[1:],
        requires_api_key=False,
    )
    task = asyncio.create_task(installer.install_server(server, "linux", timeout=60))
    deadline = asyncio.get_running_loop().time() + 10
    while not (tmp_path / "grandchild").exists():
        assert asyncio.get_running_loop().time() < deadline
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    _grandchild_gone(tmp_path, "install_server (cancelled)")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_a_group_observed_empty_is_never_signalled_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 8, claude N-A: a retained group id is forgotten the moment its
    group is observed empty (`watch_group`, run for every stdio server), so
    a later teardown cannot reach a foreign group that took the id after a
    pid wrap -- even one whose leader has exited while members live, which
    passes the reuse check."""
    import asyncio

    from pmcp import child_output
    from pmcp.child_output import detached, track
    from pmcp.client import manager

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "pass",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        **detached(),
    )
    track(process)
    pgid = process.pid
    assert child_output.group_trusted(pgid, process)
    await asyncio.wait_for(child_output.watch_group(process, pgid), 10)
    assert not child_output.group_trusted(pgid, process)  # forgotten when empty

    # The id is handed to a foreign group: it has members, and its own
    # leader has exited (so `kill(pgid, 0)` fails -- the reuse check passes).
    sent: list[tuple[int, int]] = []

    def foreign_killpg(target: int, sig: int) -> None:
        sent.append((target, sig))

    def no_leader(target: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "killpg", foreign_killpg)
    monkeypatch.setattr(os, "kill", no_leader)
    await manager._terminate_process_tree(process, "srv", group_pgid=pgid)
    manager._kill_retained_group(pgid)
    child_output.kill_tree(process)
    assert [s for _target, s in sent if s != 0] == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX semantics")
def test_a_trusted_group_vouches_only_for_its_own_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retained id is tied to the process that leads it: a stale record of
    another child never reaches it (round 8, claude N-A)."""
    from pmcp import child_output

    class _Owner:
        pass

    mine, other = _Owner(), _Owner()
    sent: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda target, sig: sent.append(sig))
    monkeypatch.setattr(
        os, "kill", lambda target, sig: (_ for _ in ()).throw(ProcessLookupError)
    )
    child_output.trust_group(mine, 4343)
    try:
        child_output.signal_retained_group(4343, owner=other)
        assert sent == []
        child_output.signal_retained_group(4343, owner=mine)
        assert sent and sent[-1] != 0
    finally:
        child_output.forget_group(4343)


# --- round 8: on a real Windows host (the windows-smoke job) -----------------------

_WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="a real Windows host")


def _windows_alive(pid: int) -> bool:
    """Whether a Windows process still runs -- asked of the kernel, never
    with `os.kill(pid, 0)`, which on Windows terminates the process."""
    import ctypes

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong(0)
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _windows_gone(pid: int, within: float = 10.0) -> bool:
    import time

    deadline = time.monotonic() + within
    while _windows_alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


_WINDOWS_LEADER = textwrap.dedent(
    """
    import os, subprocess, sys, time
    out, stay = sys.argv[1], sys.argv[2] == "stay"
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
    )
    with open(os.path.join(out, "grandchild"), "w") as handle:
        handle.write(str(grandchild.pid))
    if stay:
        time.sleep(60)
    """
)


def _windows_leader(tmp_path: Path, stay: bool) -> subprocess.Popen[bytes]:
    from pmcp.child_output import detached, track

    script = tmp_path / "leader.py"
    script.write_text(_WINDOWS_LEADER)
    process = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path), "stay" if stay else "exit"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detached(),
    )
    track(process)
    _wait_for(tmp_path / "grandchild")
    return process


@_WINDOWS
def test_windows_kill_ends_the_child_and_its_grandchild_through_its_job(
    tmp_path: Path,
) -> None:
    """Round 7/8, claude N-A: a real Job Object -- the kill reaches what the
    child started, not only the child."""
    from pmcp import child_output

    leader = _windows_leader(tmp_path, stay=True)
    grandchild = int((tmp_path / "grandchild").read_text())
    assert leader.pid in child_output._JOBS
    child_output.kill_tree(leader)
    leader.wait(timeout=10)
    assert _windows_gone(grandchild), "the grandchild outlived the job"
    assert leader.pid not in child_output._JOBS


@_WINDOWS
@pytest.mark.asyncio
async def test_windows_teardown_ends_the_grandchild_of_a_leader_that_exited(
    tmp_path: Path,
) -> None:
    """Round 8, codex F002 = gemini F001 = grok F001, for real: the leader
    exits first; teardown still terminates its job, and the grandchild."""
    from pmcp import child_output
    from pmcp.client import manager

    leader = _windows_leader(tmp_path, stay=False)
    grandchild = int((tmp_path / "grandchild").read_text())
    leader.wait(timeout=10)
    assert _windows_alive(grandchild)
    await manager._terminate_process_tree(leader, "srv")  # type: ignore[arg-type]
    assert _windows_gone(grandchild), "the grandchild outlived the teardown"
    assert leader.pid not in child_output._JOBS


@_WINDOWS
@pytest.mark.asyncio
async def test_windows_drain_reads_every_line_and_stops_at_its_deadline(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Round 8, codex F004, for real: `PeekNamedPipe` polling drains every
    line, and the drain ends at its deadline while a descendant still holds
    the pipe's write end."""
    import logging
    import threading
    import time

    from pmcp import child_output

    monkeypatch.setattr(child_output, "_DRAIN_JOIN_SECONDS", 0.5)
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger("pmcp.test.drain")
    async with child_output.drained_errlog("win", logger) as errlog:
        writer = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys, time\n"
                "[print(f'line {i}', file=sys.stderr, flush=True) for i in range(20)]\n"
                "time.sleep(60)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=errlog,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(
            "line 19" in r.getMessage() for r in caplog.records
        ):
            await asyncio_sleep(0.05)
    try:
        lines = [r.getMessage() for r in caplog.records if r.name == "pmcp.test.drain"]
        assert lines[:20] == [f"[win] stderr: line {i}" for i in range(20)], lines[:3]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(
            t.name == "pmcp-errlog-win" for t in threading.enumerate()
        ):
            time.sleep(0.02)
        assert not any(t.name == "pmcp-errlog-win" for t in threading.enumerate())
        assert writer.poll() is None  # the writer never closed its end
    finally:
        writer.kill()
        writer.wait()


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


@_WINDOWS
def test_windows_reader_reads_the_last_bytes_at_the_stop() -> None:
    """The polling reader against a real Windows pipe: data written before
    the stop is read; then it ends, the writer still open."""
    import threading

    from pmcp.child_output import _PollingReader, bounded_lines

    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"last words\n")
        stop = threading.Event()
        stop.set()
        reader = _PollingReader(read_fd, stop)
        assert list(bounded_lines(reader)) == [(b"last words", False)]  # type: ignore[arg-type]
    finally:
        os.close(read_fd)
        os.close(write_fd)


# --- round 9 ----------------------------------------------------------------------


@pytest.mark.skipif(not Path("/dev/zero").exists(), reason="a never-empty source")
def test_the_windows_reader_stops_while_its_pipe_is_flooded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round 9, codex F001: data always available no longer outruns the
    stop. A source that is never empty (`/dev/zero`, always "ready"): once
    stopped the reader reads at most one final budget, then ends."""
    import threading

    from pmcp import child_output

    monkeypatch.setattr(child_output, "_peek_pipe", lambda fd: 1 << 20)
    zero = os.open("/dev/zero", os.O_RDONLY)
    stop = threading.Event()
    reader = child_output._PollingReader(zero, stop)
    try:
        assert reader.read1(4096)  # data, while running
        stop.set()
        final = reader.read1(1 << 20)
        assert 0 < len(final) <= child_output._FINAL_READ_BUDGET
        assert reader.read1(1 << 20) == b""  # ended, the source still full
    finally:
        os.close(zero)


def contextlib_suppress_oserror() -> Any:
    import contextlib

    return contextlib.suppress(OSError)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_the_group_watcher_never_waits_on_the_pipes(tmp_path: Path) -> None:
    """Round 9, codex F002: `process.wait()` also waits for the pipes, and a
    descendant that left the group but kept the pipes made an empty group
    stay trusted forever. The watcher forgets it within its bound."""
    import asyncio

    from pmcp import child_output
    from pmcp.child_output import detached, track

    script = tmp_path / "leader.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import subprocess, sys
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,  # leaves the group, keeps the pipes
            )
            open({str(tmp_path / "grandchild")!r}, "w").write(str(child.pid))
            """
        )
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **detached(),
    )
    track(process)
    _wait_for(tmp_path / "grandchild")
    grandchild = int((tmp_path / "grandchild").read_text())
    try:
        await asyncio.wait_for(child_output.watch_group(process, process.pid), 10)
        assert not child_output.group_trusted(process.pid, process)
    finally:
        os.kill(grandchild, signal_sigkill())


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_the_group_watcher_does_not_call_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same rule on every Python: a `wait()` that never returns (3.11+
    waits for the pipes) does not hold the watcher -- an empty group is
    forgotten regardless."""
    import asyncio

    from pmcp import child_output
    from pmcp.child_output import detached, track

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "pass",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        **detached(),
    )
    track(process)
    real_wait = process.wait
    await real_wait()

    async def never() -> int:
        await asyncio.Event().wait()
        return 0

    monkeypatch.setattr(process, "wait", never)
    await asyncio.wait_for(child_output.watch_group(process, process.pid), 10)
    assert not child_output.group_trusted(process.pid, process)


def signal_sigkill() -> int:
    import signal

    return int(signal.SIGKILL)


@pytest.mark.asyncio
async def test_a_replaced_clients_group_watcher_is_cancelled() -> None:
    """Round 9, codex F002: the per-client cleanup -- graceful and
    abandoning -- cancels the group watcher with the client's other tasks."""
    import asyncio
    from unittest.mock import MagicMock

    from pmcp.client.manager import ClientManager, ManagedClient
    from pmcp.types import ServerStatus, ServerStatusEnum

    mgr = ClientManager()
    for teardown in ("graceful", "abandon"):
        watcher = asyncio.create_task(asyncio.sleep(3600))
        status = ServerStatus(name="srv", status=ServerStatusEnum.OFFLINE, tool_count=0)
        managed = ManagedClient(config=MagicMock(), process=None, status=status)
        managed.group_watch = watcher
        if teardown == "graceful":
            await mgr._cleanup_client_io("srv", managed)
        else:
            mgr._abandon_client_io("srv", managed)
            await asyncio.sleep(0)
        await asyncio.wait([watcher], timeout=5)
        assert watcher.cancelled(), teardown
        watcher.cancel()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_kill_tree_keeps_the_retained_group_when_the_lookup_races_a_reap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 9, codex F003: the leader reaped between `leader_reserved()`
    and `getpgid()` (3.10/3.11's watcher thread) no longer drops the group:
    the retained-group checks decide, and the grandchild is killed."""
    from pmcp import child_output
    from pmcp.child_output import detached, track

    script = tmp_path / "leader.py"
    script.write_text(_LEADER_EXITS_FIRST)
    leader = subprocess.Popen(
        [sys.executable, str(script), str(tmp_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detached(),
    )
    track(leader)
    _wait_for(tmp_path / "grandchild")
    os.waitpid(leader.pid, 0)  # reaped behind the check below
    # The race: the reserved check ran before the reap.
    monkeypatch.setattr(child_output, "leader_reserved", lambda process: True)
    child_output.kill_tree(leader)
    _grandchild_gone(tmp_path, "kill_tree (lookup raced a reap)")


_STARTED_THEN_EXITS = textwrap.dedent(
    """
    import os, subprocess, sys
    out = {out!r}
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    open(os.path.join(out, "grandchild"), "w").write(str(child.pid))
    print("Server started on stdio", flush=True)
    """
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_exited_install_leader_does_not_leave_its_grandchild(
    tmp_path: Path,
) -> None:
    """Round 9, grok F001 (grok's falsifier): an install that reports its
    server started and exits, leaving the server it started, is found dead
    at the handoff -- and its grandchild is killed with its group."""
    import asyncio

    from pmcp.manifest import installer
    from pmcp.tools.handlers import GatewayTools

    script = tmp_path / "install.py"
    script.write_text(_STARTED_THEN_EXITS.format(out=str(tmp_path)))
    command = [sys.executable, str(script)]
    server = installer.ServerConfig(
        name="memory",
        description="memory",
        keywords=["memory"],
        install={"linux": command},
        command=command[0],
        args=command[1:],
        requires_api_key=False,
    )
    jobs = installer.JobManager()
    job_id = await jobs.start_install(server, "linux")
    job = jobs._jobs[job_id]
    deadline = asyncio.get_running_loop().time() + 15
    while not (job.status == "server_ready" and job.process.returncode is not None):
        assert asyncio.get_running_loop().time() < deadline, job.status
        await asyncio.sleep(0.05)
    tools = GatewayTools.__new__(GatewayTools)
    result = await tools._finalize_server_ready(job, job_id, 0.0)
    assert result.status == "failed"
    _grandchild_gone(tmp_path, "the install handoff")
    if job._handoff_deadline is not None:
        job._handoff_deadline.cancel()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_an_unadopted_server_is_killed_at_the_handoff_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 9, grok F001: the job owns a `server_ready` server until it is
    adopted; one that is never adopted is killed with its group at the
    handoff deadline."""
    import asyncio

    from pmcp.manifest import installer

    monkeypatch.setattr(installer, "HANDOFF_DEADLINE_SECONDS", 0.5)
    script = tmp_path / "install.py"
    script.write_text(
        _STARTED_THEN_EXITS.format(out=str(tmp_path)) + "import time\ntime.sleep(60)\n"
    )
    command = [sys.executable, str(script)]
    server = installer.ServerConfig(
        name="memory",
        description="memory",
        keywords=["memory"],
        install={"linux": command},
        command=command[0],
        args=command[1:],
        requires_api_key=False,
    )
    jobs = installer.JobManager()
    job_id = await jobs.start_install(server, "linux")
    job = jobs._jobs[job_id]
    deadline = asyncio.get_running_loop().time() + 15
    while job.status != "failed":
        assert asyncio.get_running_loop().time() < deadline, job.status
        await asyncio.sleep(0.05)
    assert job.error == "Server was not adopted in time"
    _grandchild_gone(tmp_path, "the unadopted handoff")


@pytest.mark.asyncio
async def test_verify_installation_requires_a_successful_run(tmp_path: Path) -> None:
    """Round 9 (grok): runnable means it ran -- a command whose `--help`
    fails is not verified."""
    from pmcp.manifest import installer

    server = installer.ServerConfig(
        name="memory",
        description="memory",
        keywords=["memory"],
        install={"linux": [sys.executable]},
        command=sys.executable,
        args=["-c", "import sys; sys.exit(3)"],
        requires_api_key=False,
    )
    assert await installer.verify_installation(server) is False


# --- round 10 ---------------------------------------------------------------------


async def _server_ready_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deadline: float
) -> Any:
    import asyncio

    from pmcp.manifest import installer

    monkeypatch.setattr(installer, "HANDOFF_DEADLINE_SECONDS", deadline)
    script = tmp_path / "install.py"
    script.write_text(
        _STARTED_THEN_EXITS.format(out=str(tmp_path)) + "import time\ntime.sleep(60)\n"
    )
    command = [sys.executable, str(script)]
    server = installer.ServerConfig(
        name="memory",
        description="memory",
        keywords=["memory"],
        install={"linux": command},
        command=command[0],
        args=command[1:],
        requires_api_key=False,
    )
    jobs = installer.JobManager()
    job_id = await jobs.start_install(server, "linux")
    job = jobs._jobs[job_id]
    limit = asyncio.get_running_loop().time() + 15
    while job.status != "server_ready":
        assert asyncio.get_running_loop().time() < limit, job.status
        await asyncio.sleep(0.02)
    return jobs, job_id, job


def _adopting_manager(monkeypatch: pytest.MonkeyPatch, hold: float, fail: bool) -> Any:
    import asyncio

    from pmcp.client.manager import ClientManager

    mgr = ClientManager()

    async def initialize(managed: Any) -> None:
        await asyncio.sleep(hold)
        if fail:
            raise RuntimeError("handshake failed")

    async def no_capabilities(managed: Any) -> tuple[int, int, int]:
        return 0, 0, 0

    monkeypatch.setattr(mgr, "_send_initialize", initialize)
    monkeypatch.setattr(mgr, "_index_capabilities", no_capabilities)
    return mgr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_the_handoff_deadline_never_kills_a_server_being_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 10, claude F001 = grok F001 (both falsifiers): the deadline
    passes while ClientManager is still adopting the server (a held
    initialize). Ownership moved before the first await, so the adopted
    server is alive, the job is complete, and one owner remains."""
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools

    jobs, job_id, job = await _server_ready_job(tmp_path, monkeypatch, deadline=0.3)
    process = job.process
    mgr = _adopting_manager(monkeypatch, hold=1.0, fail=False)
    tools = GatewayTools(client_manager=mgr, policy_manager=PolicyManager())
    try:
        result = await tools._finalize_server_ready(job, job_id, 0.0)
        assert result.status == "complete", result.error
        assert process.returncode is None, "the deadline killed a server being adopted"
        assert "memory" in mgr._clients
        assert job._handoff_deadline.cancelled()
    finally:
        await mgr.disconnect_all()
        for task in list(mgr._background_tasks):
            task.cancel()
    _grandchild_gone(tmp_path, "the adopted server's disconnect")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
@pytest.mark.asyncio
async def test_a_failed_handoff_and_a_cancelled_job_cancel_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 10 (gemini): the deadline is cancelled explicitly on the
    handoff's failure branch and by `cancel_job`, not only on success; the
    process is killed by exactly one owner."""
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools

    jobs, job_id, job = await _server_ready_job(tmp_path, monkeypatch, deadline=60)
    mgr = _adopting_manager(monkeypatch, hold=0.0, fail=True)
    tools = GatewayTools(client_manager=mgr, policy_manager=PolicyManager())
    result = await tools._finalize_server_ready(job, job_id, 0.0)
    assert result.status == "failed"
    assert job._handoff_deadline.cancelled()
    _grandchild_gone(tmp_path, "the failed handoff")

    other = tmp_path / "other"
    other.mkdir()
    jobs, job_id, job = await _server_ready_job(other, monkeypatch, deadline=60)
    await jobs.cancel_job(job_id)
    assert job._handoff_deadline.cancelled()
    if job.process is not None:
        from pmcp.child_output import kill_tree

        kill_tree(job.process)
