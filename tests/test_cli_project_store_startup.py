"""The REAL `pmcp` entry point never reads a repo-controlled .env.pmcp around the walk.

Round 4 on Consiliency/pmcp#366 (claude seat F001): `main()` loads
`<cwd>/.env.pmcp` at startup, before any subcommand, so `pmcp secrets set`,
`pmcp secrets sync` and `pmcp auth connect` run from the project root followed a
leaving link (a raw `PermissionError` traceback naming the absolute path, or the
outside file loaded into the environment) and hung on a fifo -- while every
earlier test called the functions directly. These tests run the console entry
point in a subprocess, from cwd = the project root, under a timeout.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="symlinks, fifos and sockets: POSIX"
)

REFUSAL = "refusing to write .env.pmcp: it is a symlink that leaves the project"
NOT_REGULAR = "refusing to write .env.pmcp: it is not a regular file"
LOAD_REFUSAL = (
    "pmcp: refusing to load .env.pmcp: it is a symlink that leaves the project"
)
LOAD_NOT_REGULAR = "pmcp: refusing to load .env.pmcp: it is not a regular file"
PROBE = "R4_STARTUP_PROBE"

#: Run the real `main()` with argv, but replace the subcommand dispatch with a
#: report of whether startup loaded the probe -- the only way to observe what
#: the startup load put into the process environment.
_PROBE_MAIN = (
    "import json, os, sys\n"
    "import pmcp.cli as cli\n"
    "async def _report(args):\n"
    f"    print(json.dumps({{'probe': os.environ.get({PROBE!r})}}))\n"
    "cli.async_main = _report\n"
    "sys.argv = ['pmcp', 'secrets', 'set', '--scope', 'project', 'K', 'v']\n"
    "cli.main()\n"
)


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    base = Path(os.path.realpath(tmp_path))
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    project = base / "project"
    (project / ".git").mkdir(parents=True)
    outside = base / "outside"
    outside.mkdir()
    victim = outside / "victim"
    victim.write_text(f"{PROBE}=outside\n", encoding="utf-8")
    return {
        "base": base,
        "home": home,
        "project": project,
        "outside": outside,
        "victim": victim,
    }


def _env(lay: dict[str, Path]) -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items() if not k.startswith("PMCP") and k != PROBE
    }
    env["HOME"] = str(lay["home"])
    # A closed port: `auth connect` must fail fast at the gateway, not hang.
    env["PMCP_GATEWAY_URL"] = "http://127.0.0.1:9/mcp"
    return env


def _run(
    lay: dict[str, Path], args: list[str], *, code: str | None = None
) -> subprocess.CompletedProcess[str]:
    argv = (
        [sys.executable, "-c", code]
        if code is not None
        else [sys.executable, "-m", "pmcp.cli", *args]
    )
    try:
        return subprocess.run(
            argv,
            cwd=lay["project"],
            env=_env(lay),
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"`pmcp {' '.join(args)}` hung on a repo-controlled .env.pmcp")


def _json(proc: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert proc.returncode == 0, proc.stderr[-800:]
    out = json.loads(proc.stdout)
    assert isinstance(out, dict)
    return out


def _value_free(lay: dict[str, Path], proc: subprocess.CompletedProcess[str]) -> None:
    both = proc.stdout + proc.stderr
    assert str(lay["base"]) not in both, both[-800:]
    assert "Traceback" not in both, both[-800:]
    assert "outside" not in proc.stdout


SET = ["secrets", "set", "--scope", "project", "K", "v"]
SYNC = ["secrets", "sync", "--from-scope", "user", "--to-scope", "project"]
AUTH = ["auth", "connect", "someserver", "--credential", "x"]


# --------------------------------------------------------------------------- #
# A link that leaves the project: never read at startup, refused by the command.
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores mode 000")
@pytest.mark.parametrize("command", [SET, SYNC], ids=["secrets set", "secrets sync"])
def test_a_leaving_link_to_a_mode_000_file_is_a_reported_refusal(
    command: list[str], layout: dict[str, Path]
) -> None:
    os.chmod(layout["victim"], 0)
    os.symlink(layout["victim"], layout["project"] / ".env.pmcp")
    try:
        proc = _run(layout, command)
    finally:
        os.chmod(layout["victim"], 0o600)
    out = _json(proc)
    assert out["ok"] is False and out["error"] == REFUSAL
    assert LOAD_REFUSAL in proc.stderr
    _value_free(layout, proc)
    assert os.path.islink(layout["project"] / ".env.pmcp")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores mode 000")
def test_auth_connect_with_a_leaving_link_to_a_mode_000_file_does_not_crash_at_startup(
    layout: dict[str, Path],
) -> None:
    os.chmod(layout["victim"], 0)
    os.symlink(layout["victim"], layout["project"] / ".env.pmcp")
    try:
        proc = _run(layout, AUTH)
    finally:
        os.chmod(layout["victim"], 0o600)
    assert LOAD_REFUSAL in proc.stderr
    assert "Permission denied" not in proc.stderr
    assert str(layout["base"]) not in proc.stdout + proc.stderr


def test_a_readable_file_outside_is_not_loaded_into_the_environment(
    layout: dict[str, Path],
) -> None:
    os.symlink(layout["victim"], layout["project"] / ".env.pmcp")
    proc = _run(layout, SET, code=_PROBE_MAIN)
    assert _json(proc) == {"probe": None}
    assert LOAD_REFUSAL in proc.stderr


@pytest.mark.parametrize(
    "shape",
    ["a regular store", "a relative link inside", "a dangling link inside"],
)
def test_a_store_inside_the_project_still_loads_at_startup(
    shape: str, layout: dict[str, Path]
) -> None:
    """Positive control: the refusal is the link's direction, not loading itself."""
    project = layout["project"]
    if shape == "a regular store":
        (project / ".env.pmcp").write_text(f"{PROBE}=inside\n", encoding="utf-8")
        expected: str | None = "inside"
    elif shape == "a relative link inside":
        (project / "conf").mkdir()
        (project / "conf" / "s.env").write_text(f"{PROBE}=inside\n", encoding="utf-8")
        os.symlink("conf/s.env", project / ".env.pmcp")
        expected = "inside"
    else:
        os.symlink("conf/not-yet.env", project / ".env.pmcp")
        (project / "conf").mkdir()
        expected = None
    proc = _run(layout, SET, code=_PROBE_MAIN)
    assert _json(proc) == {"probe": expected}
    assert "refusing" not in proc.stderr


# --------------------------------------------------------------------------- #
# Non-regular stores: refused at startup and by the command, never hung on.
# --------------------------------------------------------------------------- #


def _fifo(lay: dict[str, Path]) -> None:
    os.mkfifo(lay["project"] / ".env.pmcp")


def _link_to_fifo(lay: dict[str, Path]) -> None:
    os.mkfifo(lay["project"] / "pipe")
    os.symlink("pipe", lay["project"] / ".env.pmcp")


def _link_to_socket(lay: dict[str, Path]) -> None:
    here = os.getcwd()
    os.chdir(lay["project"])
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind("sock")
        sock.close()
    finally:
        os.chdir(here)
    os.symlink("sock", lay["project"] / ".env.pmcp")


NON_REGULAR: dict[str, Callable[[dict[str, Path]], None]] = {
    "a fifo": _fifo,
    "a link to a fifo": _link_to_fifo,
    "a link to a socket": _link_to_socket,
}


@pytest.mark.parametrize("command", [SET, SYNC], ids=["secrets set", "secrets sync"])
@pytest.mark.parametrize("shape", list(NON_REGULAR), ids=list(NON_REGULAR))
def test_a_non_regular_store_is_refused_without_hanging(
    shape: str, command: list[str], layout: dict[str, Path]
) -> None:
    NON_REGULAR[shape](layout)
    proc = _run(layout, command)
    out = _json(proc)
    assert out["ok"] is False and out["error"] == NOT_REGULAR
    assert LOAD_NOT_REGULAR in proc.stderr
    _value_free(layout, proc)


@pytest.mark.parametrize("shape", list(NON_REGULAR), ids=list(NON_REGULAR))
def test_auth_connect_does_not_hang_on_a_non_regular_store(
    shape: str, layout: dict[str, Path]
) -> None:
    NON_REGULAR[shape](layout)
    proc = _run(layout, AUTH)
    assert LOAD_NOT_REGULAR in proc.stderr
    assert str(layout["base"]) not in proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# The board's falsifier, as written.
# --------------------------------------------------------------------------- #


def _run_cli(project: Path, home: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PMCP")}
    env["HOME"] = str(home)
    try:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pmcp.cli",
                "secrets",
                "set",
                "--scope",
                "project",
                "K",
                "v",
            ],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("`pmcp secrets set` hung on a repo-controlled .env.pmcp")


@pytest.mark.skipif(os.geteuid() == 0, reason="posix, non-root")
def test_board_r4_f001_cli_secrets_set_refuses_a_leaving_or_fifo_store_from_the_project_cwd(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    victim = tmp_path / "outside" / "victim"
    victim.parent.mkdir()
    victim.write_text("ORIGINAL=1\n", encoding="utf-8")

    # 1. A link that leaves the project, to a mode-000 file (round-3 F001's shape).
    os.chmod(victim, 0)
    os.symlink(victim, project / ".env.pmcp")
    try:
        proc = _run_cli(project, home)
    finally:
        os.chmod(victim, 0o600)
    assert proc.returncode == 0, proc.stderr[-400:]
    out = json.loads(proc.stdout)
    assert out["ok"] is False and out["error"] == REFUSAL
    assert str(tmp_path) not in proc.stdout + proc.stderr

    # 2. A fifo at the store path: refused, never hung.
    os.unlink(project / ".env.pmcp")
    os.mkfifo(project / ".env.pmcp")
    proc = _run_cli(project, home)
    assert proc.returncode == 0, proc.stderr[-400:]
    out = json.loads(proc.stdout)
    assert out["ok"] is False and out["error"] == NOT_REGULAR
