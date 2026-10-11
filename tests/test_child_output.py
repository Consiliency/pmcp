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


def test_kill_tree_signals_no_group_for_an_object_that_only_has_a_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A test double or adopted handle with a `pid` is killed through its
    own `kill`, never `killpg`: in CI a double's `pid = 4321` named a real
    process group, and the group kill ended the test run."""
    from pmcp import child_output

    groups: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: groups.append(pgid))

    class _Double:
        pid = os.getpid()
        returncode = None
        killed = False

        def kill(self) -> None:
            self.killed = True

        def terminate(self) -> None:
            self.killed = True

    double = _Double()
    child_output.kill_tree(double)
    child_output.kill_tree(double, terminate=True)
    assert groups == []
    assert double.killed


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
    groups: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: groups.append(pgid))
    child_output.kill_tree(process)
    assert groups == []
