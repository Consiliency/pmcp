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


def _spawn_calls() -> list[tuple[str, ast.Call, str]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                key = (func.value.id, func.attr)
                if key in _SPAWNS:
                    found.append((rel, node, f"{key[0]}.{key[1]}"))
                elif func.attr.startswith("exec") and func.value.id == "os":
                    found.append((rel, node, f"os.{func.attr}"))
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "stdio_client":
                found.append((rel, node, "stdio_client"))
    return found


def _streams_problem(call: ast.Call, kind: str) -> str | None:
    """Why ``call`` lets a child write to pmcp's own streams, or None."""
    keywords = {k.arg: k.value for k in call.keywords if k.arg}
    if kind == "stdio_client":
        errlog = keywords.get("errlog")
        if errlog is None or ast.unparse(errlog) in ("sys.stderr", "sys.stdout"):
            return "stdio_client without a piped errlog"
        return None
    if kind in ("os.system", "os.popen", "pty.spawn") or kind.startswith("os.exec"):
        return f"{kind} always shares the terminal"
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
    def problem(source: str) -> str | None:
        call = ast.parse(source).body[0].value  # type: ignore[attr-defined]
        func = call.func
        kind = (
            "stdio_client"
            if getattr(func, "id", "") == "stdio_client"
            else f"{func.value.id}.{func.attr}"
        )
        return _streams_problem(call, kind)

    assert problem("subprocess.run(['x'])") == "stdout=inherited"
    assert (
        problem("subprocess.run(['x'], stdout=subprocess.PIPE)") == "stderr=inherited"
    )
    assert problem("subprocess.run(['x'], capture_output=True)") is None
    assert (
        problem("subprocess.run(['x'], stdout=subprocess.PIPE, stderr=sys.stderr)")
        == "stderr=sys.stderr"
    )
    assert (
        problem(
            "asyncio.create_subprocess_exec('x', stdout=asyncio.subprocess.PIPE,"
            " stderr=asyncio.subprocess.DEVNULL)"
        )
        is None
    )
    assert problem("stdio_client(params)") == "stdio_client without a piped errlog"
    assert problem("stdio_client(params, errlog=sys.stderr)") is not None
    assert problem("stdio_client(params, errlog=errlog)") is None
    assert problem("os.system('x')") is not None


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
