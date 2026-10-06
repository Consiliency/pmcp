"""Every reader of a repository-controlled credential store is confined to the project.

Consiliency/pmcp#367. Consiliency/pmcp#366 confined the startup load and every
command that rewrites a project ``.env.pmcp``; these readers still read THROUGH
a link to a file outside the project:

* remote-header auth (``${VAR}`` headers, so an outside file's value could reach
  an ``Authorization`` header) -- the gateway config load, ``pmcp status`` and
  ``pmcp doctor``, and the header a remote connection actually sends;
* the repository-controlled tenant store ``.pmcp/tenants/<id>/pmcp.env``;
* the gateway credential-availability check (it loads the file into the
  gateway's own environment);
* the feedback gate's planted-key check;
* ``managed_secret_keys`` (spawn-time credential checks and env stripping);
* ``pmcp secrets check``.

Each is driven through its real entry point -- a subprocess of the console
command where one exists, with an isolated HOME and a closed gateway port -- and
must not let the outside file's value reach a header, the environment or the
output. A refused store reads as empty with one value-free warning (the feedback
gate fails closed instead), and the legitimate shapes keep working: a regular
file, a link inside the project, a subdirectory link, the user store through a
dotfiles link, and no store at all.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pmcp import env_store, feedback_egress
from pmcp.client.manager import ClientManager, _remote_headers
from pmcp.config.loader import StartupSkipReason, resolve_startup_configs
from pmcp.remote_auth import (
    MissingRemoteHeaderAuthError,
    resolve_remote_headers_for_tenant,
)
from pmcp.tools.handlers import GatewayTools
from pmcp.types import RemoteMcpServerConfig, ResolvedServerConfig

pytestmark = pytest.mark.skipif(os.name != "posix", reason="symlinks and fifos: POSIX")

VAR = "LEAK_VAR_367"
OUTSIDE_VALUE = "outside-secret-367"
INSIDE_VALUE = "inside-ok-367"
USER_VALUE = "dotfiles-ok-367"
TENANT = "acme"

READ_REFUSAL = "pmcp: refusing to read .env.pmcp: it is a symlink"
READ_NOT_REGULAR = "pmcp: refusing to read .env.pmcp: it is not a regular file"
LOAD_REFUSAL = "pmcp: refusing to load .env.pmcp: it is a symlink"
TENANT_REFUSAL = "pmcp: refusing to read pmcp.env: it is a symlink"


# --------------------------------------------------------------------------- #
# Layout and store shapes.
# --------------------------------------------------------------------------- #


@pytest.fixture
def lay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    base = Path(os.path.realpath(tmp_path))
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    project = base / "project"
    (project / ".git").mkdir(parents=True)
    outside = base / "outside"
    outside.mkdir()
    (outside / "victim").write_text(f"{VAR}={OUTSIDE_VALUE}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(VAR, raising=False)
    monkeypatch.chdir(project)
    return {"base": base, "home": home, "project": project, "outside": outside}


def _link_out(store: Path, lay: dict[str, Path]) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(os.path.relpath(lay["outside"] / "victim", store.parent), store)


def _abs_link_out(store: Path, lay: dict[str, Path]) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(lay["outside"] / "victim", store)


def _fifo(store: Path, lay: dict[str, Path]) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(store)


def _link_inside_late(store: Path, lay: dict[str, Path]) -> None:
    _link_inside(store, lay)


#: Since Consiliency/pmcp#366 round 9 a repository store is never read through
#: a symlink of any kind -- one into the project included.
REFUSED: dict[str, Callable[[Path, dict[str, Path]], None]] = {
    "link-out": _link_out,
    "absolute-link-out": _abs_link_out,
    "link-inside": _link_inside_late,
    "fifo": _fifo,
}


def _regular(store: Path, lay: dict[str, Path]) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(f"{VAR}={INSIDE_VALUE}\n", encoding="utf-8")


def _link_inside(store: Path, lay: dict[str, Path]) -> None:
    """A link to a file in a subdirectory of the project."""
    real = lay["project"] / "conf" / "secrets" / "pmcp.env"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(f"{VAR}={INSIDE_VALUE}\n", encoding="utf-8")
    store.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(os.path.relpath(real, store.parent), store)


LEGIT: dict[str, Callable[[Path, dict[str, Path]], None]] = {
    "regular": _regular,
}


def _project_store(lay: dict[str, Path]) -> Path:
    return lay["project"] / ".env.pmcp"


def _tenant_store(lay: dict[str, Path]) -> Path:
    return lay["project"] / ".pmcp" / "tenants" / TENANT / "pmcp.env"


def _user_store_via_dotfiles(lay: dict[str, Path]) -> None:
    """``~/.config/pmcp/pmcp.env`` linked into a dotfiles checkout outside HOME."""
    dotfiles = lay["base"] / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "pmcp.env").write_text(f"{VAR}={USER_VALUE}\n", encoding="utf-8")
    os.symlink(dotfiles / "pmcp.env", lay["home"] / ".config" / "pmcp" / "pmcp.env")


def _no_outside_value(*texts: object) -> None:
    for text in texts:
        assert OUTSIDE_VALUE not in str(text), str(text)[-800:]


# --------------------------------------------------------------------------- #
# Subprocess helpers: the real console entry point, isolated HOME, closed port.
# --------------------------------------------------------------------------- #


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _cli(
    lay: dict[str, Path], args: list[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("PMCP") and k != VAR and k != "COVERAGE_PROCESS_START"
    }
    env["HOME"] = str(lay["home"])
    # Never the live gateway on 3344: a port nothing listens on.
    env["PMCP_GATEWAY_URL"] = f"http://127.0.0.1:{_closed_port()}/mcp"
    try:
        return subprocess.run(
            [sys.executable, "-m", "pmcp.cli", *args],
            cwd=cwd or lay["project"],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"`pmcp {' '.join(args)}` hung on a repo-controlled store")


def _remote_config_file(
    lay: dict[str, Path], url: str = "https://example.invalid/mcp"
) -> Path:
    cfg = lay["base"] / "explicit-config.json"
    cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote367": {
                        "type": "http",
                        "url": url,
                        "headers": {"Authorization": f"Bearer ${{{VAR}}}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return cfg


def _remote(url: str = "https://example.invalid/mcp") -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name="remote367",
        source="custom",
        config=RemoteMcpServerConfig(
            type="streamable-http",
            url=url,
            headers={"Authorization": f"Bearer ${{{VAR}}}"},
        ),
    )


# --------------------------------------------------------------------------- #
# 1. Remote-header auth: the header a connection SENDS, on the wire.
# --------------------------------------------------------------------------- #


class _Capture(BaseHTTPRequestHandler):
    seen: list[str] = []

    def _record(self) -> None:
        _Capture.seen.append(self.headers.get("Authorization", ""))
        self.send_response(500)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_GET = do_DELETE = _record  # noqa: N815

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def capture_server() -> Iterator[str]:
    _Capture.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    finally:
        server.shutdown()
        server.server_close()


def _connect(project: Path, url: str) -> BaseException | None:
    manager = ClientManager(project_root=project)

    async def run() -> BaseException | None:
        try:
            await asyncio.wait_for(manager._connect_streamable_http(_remote(url)), 20)
        except BaseException as exc:  # noqa: BLE001 - the 500 fails the connect
            return exc
        finally:
            await manager.disconnect_all()
        return None

    return asyncio.run(run())


@pytest.mark.parametrize("shape", list(REFUSED), ids=list(REFUSED))
def test_a_remote_connection_never_sends_an_outside_store_value(
    shape: str,
    lay: dict[str, Path],
    capture_server: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    REFUSED[shape](_project_store(lay), lay)
    exc = _connect(lay["project"], capture_server)
    assert isinstance(exc, MissingRemoteHeaderAuthError), repr(exc)
    assert _Capture.seen == [], "a request reached the server"
    err = capfd.readouterr().err
    assert (READ_NOT_REGULAR if shape == "fifo" else READ_REFUSAL) in err
    _no_outside_value(err, exc)
    assert str(lay["base"]) not in err


@pytest.mark.parametrize("shape", list(LEGIT), ids=list(LEGIT))
def test_a_remote_connection_sends_a_legitimate_project_store_value(
    shape: str, lay: dict[str, Path], capture_server: str
) -> None:
    LEGIT[shape](_project_store(lay), lay)
    _connect(lay["project"], capture_server)
    assert _Capture.seen and set(_Capture.seen) == {f"Bearer {INSIDE_VALUE}"}


def test_a_remote_connection_sends_the_user_store_value_through_a_dotfiles_link(
    lay: dict[str, Path], capture_server: str
) -> None:
    _user_store_via_dotfiles(lay)
    _connect(lay["project"], capture_server)
    assert _Capture.seen and set(_Capture.seen) == {f"Bearer {USER_VALUE}"}


# --------------------------------------------------------------------------- #
# 2. The gateway config load: an eager remote server with a ${VAR} header.
# --------------------------------------------------------------------------- #


def _startup(project: Path):  # type: ignore[no-untyped-def]
    return resolve_startup_configs(
        [_remote()], enabled_auto_start=["remote367"], project_root=project
    )


@pytest.mark.parametrize("shape", list(REFUSED), ids=list(REFUSED))
def test_the_config_load_skips_a_header_only_an_outside_store_could_fill(
    shape: str, lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    REFUSED[shape](_project_store(lay), lay)
    resolution = _startup(lay["project"])
    assert resolution.eager_configs == []
    [skip] = resolution.skipped
    assert skip.reason == StartupSkipReason.MISSING_AUTH
    assert skip.missing_env_vars == [VAR]
    _no_outside_value(resolution, capfd.readouterr().err)


@pytest.mark.parametrize("shape", [*LEGIT, "missing"])
def test_the_config_load_keeps_a_legitimate_store(
    shape: str, lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    if shape != "missing":
        LEGIT[shape](_project_store(lay), lay)
    resolution = _startup(lay["project"])
    if shape == "missing":
        assert [s.reason for s in resolution.skipped] == [
            StartupSkipReason.MISSING_AUTH
        ]
        assert "refusing" not in capfd.readouterr().err
    else:
        assert [c.name for c in resolution.eager_configs] == ["remote367"]
        assert resolution.skipped == []


# --------------------------------------------------------------------------- #
# 3. The CLI (`pmcp status`) and `pmcp doctor`, as subprocesses.
# --------------------------------------------------------------------------- #


def _status(
    lay: dict[str, Path],
) -> tuple[dict[str, object], subprocess.CompletedProcess[str]]:
    proc = _cli(lay, ["status", "--json", "--config", str(_remote_config_file(lay))])
    assert proc.returncode == 0, proc.stderr[-800:]
    out = json.loads(proc.stdout)
    [server] = [s for s in out["servers"] if s["name"] == "remote367"]
    return server, proc


@pytest.mark.parametrize("shape", list(REFUSED), ids=list(REFUSED))
def test_pmcp_status_reports_the_header_missing_for_an_outside_store(
    shape: str, lay: dict[str, Path]
) -> None:
    REFUSED[shape](_project_store(lay), lay)
    server, proc = _status(lay)
    assert server["missing_env_vars"] == [VAR]
    assert (READ_NOT_REGULAR if shape == "fifo" else READ_REFUSAL) in proc.stderr
    _no_outside_value(proc.stdout, proc.stderr)
    assert str(lay["base"] / "outside") not in proc.stdout + proc.stderr


@pytest.mark.parametrize("shape", list(LEGIT), ids=list(LEGIT))
def test_pmcp_status_resolves_a_legitimate_store(
    shape: str, lay: dict[str, Path]
) -> None:
    LEGIT[shape](_project_store(lay), lay)
    server, proc = _status(lay)
    assert server["missing_env_vars"] == []
    assert "refusing" not in proc.stderr


def _doctor(lay: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    (lay["project"] / ".mcp.json").write_text(
        _remote_config_file(lay).read_text(encoding="utf-8"), encoding="utf-8"
    )
    return _cli(lay, ["doctor", "--timeout", "0.5"])


@pytest.mark.parametrize("shape", list(REFUSED), ids=list(REFUSED))
def test_pmcp_doctor_reports_the_header_missing_for_an_outside_store(
    shape: str, lay: dict[str, Path]
) -> None:
    REFUSED[shape](_project_store(lay), lay)
    proc = _doctor(lay)
    assert f"missing_env={VAR}" in proc.stdout, proc.stdout[-800:] + proc.stderr[-800:]
    assert (READ_NOT_REGULAR if shape == "fifo" else READ_REFUSAL) in proc.stderr
    _no_outside_value(proc.stdout, proc.stderr)


@pytest.mark.parametrize("shape", list(LEGIT), ids=list(LEGIT))
def test_pmcp_doctor_resolves_a_legitimate_store(
    shape: str, lay: dict[str, Path]
) -> None:
    LEGIT[shape](_project_store(lay), lay)
    proc = _doctor(lay)
    assert f"missing_env={VAR}" not in proc.stdout, proc.stdout[-800:]
    assert "refusing" not in proc.stderr


def test_pmcp_doctor_resolves_the_user_store_through_a_dotfiles_link(
    lay: dict[str, Path],
) -> None:
    _user_store_via_dotfiles(lay)
    proc = _doctor(lay)
    assert f"missing_env={VAR}" not in proc.stdout, proc.stdout[-800:]


# --------------------------------------------------------------------------- #
# 4. The tenant store `.pmcp/tenants/<id>/pmcp.env`.
# --------------------------------------------------------------------------- #


def _tenant_headers(project: Path):  # type: ignore[no-untyped-def]
    return resolve_remote_headers_for_tenant(
        {"Authorization": f"Bearer ${{{VAR}}}"},
        server_name="remote367",
        tenant_id=TENANT,
        project_root=project,
    )


def _pmcp_dir_link_out(lay: dict[str, Path]) -> None:
    """`.pmcp` itself is a link out: the tenant file is a regular file outside."""
    real = lay["outside"] / "dot-pmcp" / "tenants" / TENANT
    real.mkdir(parents=True)
    (real / "pmcp.env").write_text(f"{VAR}={OUTSIDE_VALUE}\n", encoding="utf-8")
    os.symlink("../outside/dot-pmcp", lay["project"] / ".pmcp")


TENANT_REFUSED: dict[str, Callable[[dict[str, Path]], None]] = {
    "link-out": lambda lay: _link_out(_tenant_store(lay), lay),
    "absolute-link-out": lambda lay: _abs_link_out(_tenant_store(lay), lay),
    "fifo": lambda lay: _fifo(_tenant_store(lay), lay),
    "pmcp-dir-link-out": _pmcp_dir_link_out,
}


@pytest.mark.parametrize("shape", list(TENANT_REFUSED), ids=list(TENANT_REFUSED))
def test_a_tenant_store_outside_the_project_fills_no_header(
    shape: str, lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    TENANT_REFUSED[shape](lay)
    resolution = _tenant_headers(lay["project"])
    assert resolution.missing_env_vars == [VAR]
    with pytest.raises(MissingRemoteHeaderAuthError):
        _remote_headers(
            "remote367", _remote().config, tenant_id=TENANT, project_root=lay["project"]
        )  # type: ignore[arg-type]
    err = capfd.readouterr().err
    expected = (
        "pmcp: refusing to read pmcp.env: it is not a regular file"
        if shape == "fifo"
        else TENANT_REFUSAL
    )
    assert err.count(expected) == 1, err
    _no_outside_value(resolution, err)


@pytest.mark.parametrize("shape", [*LEGIT, "missing"])
def test_a_legitimate_tenant_store_fills_the_header(
    shape: str, lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    if shape != "missing":
        LEGIT[shape](_tenant_store(lay), lay)
    resolution = _tenant_headers(lay["project"])
    if shape == "missing":
        assert resolution.missing_env_vars == [VAR]
        assert "refusing" not in capfd.readouterr().err
    else:
        assert resolution.resolved_headers == {
            "Authorization": f"Bearer {INSIDE_VALUE}"
        }


# --------------------------------------------------------------------------- #
# 5. The gateway credential-availability check.
# --------------------------------------------------------------------------- #


def _available() -> bool:
    return GatewayTools._check_api_key_available(object(), VAR)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", [".env.pmcp", ".env"])
@pytest.mark.parametrize("shape", list(REFUSED), ids=list(REFUSED))
def test_the_credential_check_never_loads_an_outside_store(
    shape: str, name: str, lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    REFUSED[shape](lay["project"] / name, lay)
    assert _available() is False
    assert VAR not in os.environ
    err = capfd.readouterr().err
    reason = "it is not a regular file" if shape == "fifo" else ("it is a symlink")
    assert f"pmcp: refusing to load {name}: {reason}" in err
    _no_outside_value(err, dict(os.environ))


@pytest.mark.parametrize("shape", list(LEGIT), ids=list(LEGIT))
def test_the_credential_check_loads_a_legitimate_store(
    shape: str, lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    LEGIT[shape](_project_store(lay), lay)
    assert _available() is True
    # A credential, held apart from the gateway's environment (round 2).
    assert env_store.credential_value(VAR) == INSIDE_VALUE
    assert VAR not in os.environ


def test_the_credential_check_refuses_a_subdirectory_link_into_the_project(
    lay: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Consiliency/pmcp#366 round 9: no symlink in a repository store at all."""
    _regular(_project_store(lay), lay)
    sub = lay["project"] / "pkg"
    sub.mkdir()
    os.symlink("../.env.pmcp", sub / ".env.pmcp")
    monkeypatch.chdir(sub)
    assert _available() is False
    assert env_store.credential_value(VAR) is None
    assert "pmcp: refusing to load .env.pmcp: it is a symlink" in capfd.readouterr().err


def test_the_credential_check_loads_the_user_store_through_a_dotfiles_link(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _user_store_via_dotfiles(lay)
    assert _available() is True
    assert os.environ[VAR] == USER_VALUE
    monkeypatch.delenv(VAR)


# --------------------------------------------------------------------------- #
# 6. The feedback gate fails closed on a refused store.
# --------------------------------------------------------------------------- #

_TOKEN = "ghp_" + "A" * 36


def _decide(project: Path) -> feedback_egress.FeedbackEgressDecision:
    return feedback_egress.evaluate_feedback_egress(
        telemetry_enabled=True,
        submission_enabled=True,
        confirm_submission=True,
        environ={"PMCP_FEEDBACK_TOKEN": _TOKEN},
        project_root=project,
    )


@pytest.mark.parametrize("shape", list(REFUSED), ids=list(REFUSED))
def test_the_feedback_gate_fails_closed_on_an_outside_store(
    shape: str, lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    REFUSED[shape](_project_store(lay), lay)
    decision = _decide(lay["project"])
    assert decision.submit_allowed is False
    assert decision.reason == "gate_error"
    _no_outside_value(decision, capfd.readouterr().err)


@pytest.mark.parametrize("shape", [*LEGIT, "missing"])
def test_the_feedback_gate_still_reads_a_legitimate_store(
    shape: str, lay: dict[str, Path]
) -> None:
    if shape != "missing":
        LEGIT[shape](_project_store(lay), lay)
    decision = _decide(lay["project"])
    # The store holds no feedback token, so nothing is planted and the gate
    # reaches its ordinary verdict instead of a lookup failure.
    assert decision.reason != "gate_error"


def test_the_feedback_gate_sees_a_token_planted_in_a_regular_store(
    lay: dict[str, Path],
) -> None:
    _project_store(lay).write_text(f"PMCP_FEEDBACK_TOKEN={_TOKEN}\n", encoding="utf-8")
    decision = _decide(lay["project"])
    assert decision.submit_allowed is False
    assert decision.reason == "untrusted_token"


# --------------------------------------------------------------------------- #
# 7. `managed_secret_keys`: spawn-time credential checks and env stripping.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("shape", list(REFUSED), ids=list(REFUSED))
def test_managed_secret_keys_reads_nothing_from_an_outside_store(
    shape: str,
    lay: dict[str, Path],
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    REFUSED[shape](_project_store(lay), lay)
    assert env_store.managed_secret_keys(lay["project"]) == set()
    # The spawn environment: the operator's own exported value is inherited,
    # untouched by an outside file's key list.
    monkeypatch.setenv(VAR, "operator-exported")
    child = env_store.sanitized_subprocess_env(None, lay["project"])
    assert child[VAR] == "operator-exported"
    err = capfd.readouterr().err
    assert err.count("pmcp: refusing to read .env.pmcp") == 1, err
    _no_outside_value(err, child)


@pytest.mark.parametrize("shape", list(LEGIT), ids=list(LEGIT))
def test_managed_secret_keys_reads_a_legitimate_store(
    shape: str, lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    LEGIT[shape](_project_store(lay), lay)
    assert env_store.managed_secret_keys(lay["project"]) == {VAR}
    monkeypatch.setenv(VAR, "managed-and-loaded")
    assert VAR not in env_store.sanitized_subprocess_env(None, lay["project"])


def test_managed_secret_keys_follows_the_user_store_dotfiles_link(
    lay: dict[str, Path],
) -> None:
    _user_store_via_dotfiles(lay)
    assert env_store.managed_secret_keys(lay["project"]) == {VAR}


# --------------------------------------------------------------------------- #
# 8. `pmcp secrets check`, as a subprocess.
# --------------------------------------------------------------------------- #


def _secrets_check(
    lay: dict[str, Path], cwd: Path | None = None
) -> tuple[dict[str, object], subprocess.CompletedProcess[str]]:
    proc = _cli(lay, ["secrets", "check"], cwd=cwd)
    out = json.loads(proc.stdout)
    assert isinstance(out, dict)
    return out, proc


@pytest.mark.parametrize("shape", list(REFUSED), ids=list(REFUSED))
def test_pmcp_secrets_check_lists_nothing_from_an_outside_store(
    shape: str, lay: dict[str, Path]
) -> None:
    REFUSED[shape](_project_store(lay), lay)
    out, proc = _secrets_check(lay)
    assert out["project_scope"]["keys"] == []  # type: ignore[index]
    assert (READ_NOT_REGULAR if shape == "fifo" else READ_REFUSAL) in proc.stderr
    if shape != "fifo":
        assert LOAD_REFUSAL in proc.stderr
    assert "Traceback" not in proc.stderr
    _no_outside_value(proc.stdout, proc.stderr)
    assert VAR not in proc.stdout


@pytest.mark.parametrize("shape", [*LEGIT, "missing"])
def test_pmcp_secrets_check_lists_a_legitimate_store(
    shape: str, lay: dict[str, Path]
) -> None:
    if shape != "missing":
        LEGIT[shape](_project_store(lay), lay)
    out, proc = _secrets_check(lay)
    expected = [] if shape == "missing" else [VAR]
    assert out["project_scope"]["keys"] == expected  # type: ignore[index]
    assert "refusing" not in proc.stderr


def test_pmcp_secrets_check_lists_the_user_store_through_a_dotfiles_link(
    lay: dict[str, Path],
) -> None:
    _user_store_via_dotfiles(lay)
    out, proc = _secrets_check(lay)
    assert out["user_scope"]["keys"] == [VAR]  # type: ignore[index]
    assert "refusing" not in proc.stderr


# --------------------------------------------------------------------------- #
# The entry point itself: one warning per store and reason; strict raises.
# --------------------------------------------------------------------------- #


def test_a_refused_store_warns_once_per_process(
    lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    _link_out(_project_store(lay), lay)
    for _ in range(3):
        assert env_store.read_store("project", project=lay["project"]) == {}
    assert capfd.readouterr().err.count(READ_REFUSAL) == 1


def test_a_strict_read_raises_the_refusal(lay: dict[str, Path]) -> None:
    _link_out(_project_store(lay), lay)
    with pytest.raises(PermissionError, match="it is a symlink"):
        env_store.read_store("project", project=lay["project"], strict=True)


def test_a_missing_store_is_silent_in_both_modes(
    lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    assert env_store.read_store("project", project=lay["project"]) == {}
    assert env_store.read_store("project", project=lay["project"], strict=True) == {}
    assert (
        env_store.read_store("tenant", project=lay["project"], tenant_id=TENANT) == {}
    )
    assert capfd.readouterr().err == ""


def test_the_user_scope_takes_no_path(lay: dict[str, Path]) -> None:
    with pytest.raises(ValueError):
        env_store.read_store("user", path=_project_store(lay))  # type: ignore[call-overload]


# --------------------------------------------------------------------------- #
# Round 1 of Consiliency/pmcp#372, F001: the startup `.env` is discovered by
# walking up from where pmcp is INSTALLED. Installed in a `.venv` inside a
# checkout (`uv run pmcp`, `pip install -e`), that walk reaches the checkout's
# own `.env` -- which is then read confined, like every other checkout file.
# --------------------------------------------------------------------------- #

_HEADER_RUNNER = """
import json, os, sys
import pmcp
from pmcp.cli import load_startup_env
from pmcp.remote_auth import build_remote_header_env_lookup, resolve_remote_headers
assert pmcp.__file__.startswith(sys.argv[1]), pmcp.__file__
load_startup_env()
r = resolve_remote_headers(
    {"Authorization": "Bearer ${%s}"}, build_remote_header_env_lookup(None)
)
from pmcp.env_store import credential_value
print(json.dumps({
    "env": os.environ.get(%r),
    "credential": credential_value(%r),
    "header": r.resolved_headers,
}))
""" % (VAR, VAR, VAR)


def _install_pmcp_in(site: Path) -> None:
    import pmcp

    shutil.copytree(
        Path(pmcp.__file__).parent,
        site / "pmcp",
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def _run_installed(
    lay: dict[str, Path], site: Path, argv: list[str]
) -> subprocess.CompletedProcess[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("PMCP") and k != VAR and k != "COVERAGE_PROCESS_START"
    }
    env.update(
        HOME=str(lay["home"]),
        PYTHONPATH=str(site),
        PMCP_GATEWAY_URL=f"http://127.0.0.1:{_closed_port()}/mcp",
    )
    try:
        return subprocess.run(
            [sys.executable, *argv],
            cwd=lay["project"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{argv} hung on the checkout's .env")


def _checkout_venv(lay: dict[str, Path]) -> Path:
    site = lay["project"] / ".venv" / "lib" / "python3" / "site-packages"
    _install_pmcp_in(site)
    (site / "runme.py").write_text(_HEADER_RUNNER, encoding="utf-8")
    return site


def _header_probe(
    lay: dict[str, Path], site: Path
) -> tuple[dict[str, object], subprocess.CompletedProcess[str]]:
    proc = _run_installed(lay, site, [str(site / "runme.py"), str(site)])
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout.strip().splitlines()[-1]), proc


@pytest.mark.parametrize("shape", ["link-out", "absolute-link-out"])
def test_pmcp_installed_in_the_checkout_never_sends_an_outside_env_value(
    shape: str, lay: dict[str, Path]
) -> None:
    REFUSED[shape](lay["project"] / ".env", lay)
    site = _checkout_venv(lay)
    out, proc = _header_probe(lay, site)
    assert out["env"] is None
    assert out["header"] == {"Authorization": f"Bearer ${{{VAR}}}"}
    assert "pmcp: refusing to load .env: it is a symlink" in proc.stderr
    _no_outside_value(proc.stdout, proc.stderr)


def test_pmcp_installed_in_the_checkout_does_not_hang_on_a_fifo_env(
    lay: dict[str, Path],
) -> None:
    _fifo(lay["project"] / ".env", lay)
    site = _checkout_venv(lay)
    proc = _run_installed(lay, site, ["-m", "pmcp.cli", "secrets", "check"])
    assert "pmcp: refusing to load .env: it is not a regular file" in proc.stderr
    assert "Traceback" not in proc.stderr
    json.loads(proc.stdout)


@pytest.mark.parametrize("shape", list(LEGIT), ids=list(LEGIT))
def test_pmcp_installed_in_the_checkout_still_loads_a_legitimate_env(
    shape: str, lay: dict[str, Path]
) -> None:
    LEGIT[shape](lay["project"] / ".env", lay)
    site = _checkout_venv(lay)
    out, proc = _header_probe(lay, site)
    # The checkout's .env is a repository file: a credential, never in the
    # process environment (round 2); the header still resolves from it.
    assert out["env"] is None
    assert out["credential"] == INSIDE_VALUE
    assert out["header"] == {"Authorization": f"Bearer {INSIDE_VALUE}"}
    assert "refusing" not in proc.stderr


def test_a_user_install_still_loads_the_home_env_its_walk_reaches(
    lay: dict[str, Path],
) -> None:
    """MIGRATING.md documents it: a ``uv tool``/``pip --user`` install's walk
    reaches ``~/.env``, outside any project, and that file still loads."""
    site = lay["home"] / ".local" / "lib" / "python3" / "site-packages"
    _install_pmcp_in(site)
    (site / "runme.py").write_text(_HEADER_RUNNER, encoding="utf-8")
    (lay["home"] / ".env").write_text(f"{VAR}={USER_VALUE}\n", encoding="utf-8")
    out, proc = _header_probe(lay, site)
    assert out["env"] == USER_VALUE
    assert out["header"] == {"Authorization": f"Bearer {USER_VALUE}"}


# --------------------------------------------------------------------------- #
# Round 1 N-1: a tenant id made only of dots names no tenant's store.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tenant", [".", "..", "..."])
def test_a_tenant_id_of_only_dots_is_refused(tenant: str, lay: dict[str, Path]) -> None:
    (lay["project"] / ".pmcp" / "tenants").mkdir(parents=True)
    (lay["project"] / ".pmcp" / "pmcp.env").write_text(
        f"{VAR}={INSIDE_VALUE}\n", encoding="utf-8"
    )
    (lay["project"] / ".pmcp" / "tenants" / "pmcp.env").write_text(
        f"{VAR}={INSIDE_VALUE}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="only of dots"):
        resolve_remote_headers_for_tenant(
            {"Authorization": f"Bearer ${{{VAR}}}"},
            server_name="remote367",
            tenant_id=tenant,
            project_root=lay["project"],
        )


@pytest.mark.parametrize("tenant", ["a.b", ".hidden", "t1", "x-y_z"])
def test_a_tenant_id_with_dots_and_more_is_accepted(
    tenant: str, lay: dict[str, Path]
) -> None:
    _regular(lay["project"] / ".pmcp" / "tenants" / tenant / "pmcp.env", lay)
    resolution = resolve_remote_headers_for_tenant(
        {"Authorization": f"Bearer ${{{VAR}}}"},
        server_name="remote367",
        tenant_id=tenant,
        project_root=lay["project"],
    )
    assert resolution.resolved_headers == {"Authorization": f"Bearer {INSIDE_VALUE}"}


# --------------------------------------------------------------------------- #
# Round 1 N-2: one warning per (store, reason, identity) per load cycle.
# --------------------------------------------------------------------------- #


def test_two_refused_stores_with_the_same_message_each_warn(
    lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    for tenant in ("t1", "t2"):
        _link_out(lay["project"] / ".pmcp" / "tenants" / tenant / "pmcp.env", lay)
        env_store.read_store("tenant", project=lay["project"], tenant_id=tenant)
        env_store.read_store("tenant", project=lay["project"], tenant_id=tenant)
    assert capfd.readouterr().err.count(TENANT_REFUSAL) == 2


def test_a_store_that_changes_warns_again(
    lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    store = _project_store(lay)
    _link_out(store, lay)
    env_store.read_store("project", project=lay["project"])
    store.unlink()
    (lay["outside"] / "other").write_text(f"{VAR}={OUTSIDE_VALUE}\n", encoding="utf-8")
    os.symlink("../outside/other", store)
    env_store.read_store("project", project=lay["project"])
    env_store.read_store("project", project=lay["project"])
    assert capfd.readouterr().err.count(READ_REFUSAL) == 2


def test_each_config_load_cycle_reports_a_still_refused_store_once(
    lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    _link_out(_project_store(lay), lay)
    for _ in range(2):
        _startup(lay["project"])
        env_store.managed_secret_keys(lay["project"])
        env_store.managed_secret_keys(lay["project"])
    assert capfd.readouterr().err.count(READ_REFUSAL) == 2


def test_a_store_replaced_in_place_warns_again(
    lay: dict[str, Path], capfd: pytest.CaptureFixture[str]
) -> None:
    """Same path, same reason, a new file: the identity tells them apart."""
    store = _project_store(lay)
    os.mkfifo(store)
    env_store.read_store("project", project=lay["project"])
    env_store.read_store("project", project=lay["project"])
    store.unlink()
    os.mkfifo(store)
    env_store.read_store("project", project=lay["project"])
    assert capfd.readouterr().err.count(READ_NOT_REGULAR) == 2


# --------------------------------------------------------------------------- #
# Round 1, grok F001: a repository-controlled store may not set a variable pmcp
# reads to decide paths or trust. With HOME unset, a regular `.env.pmcp` that
# supplied HOME moved the user store onto a link the clone shipped.
# --------------------------------------------------------------------------- #


def _retarget_layout(lay: dict[str, Path]) -> Path:
    """A clone shipping `fakehome/.config/pmcp/pmcp.env -> ../../../../outside/victim`
    and a REGULAR `.env.pmcp` that sets HOME to that fake home."""
    fake_home = lay["project"] / "fakehome"
    (fake_home / ".config" / "pmcp").mkdir(parents=True)
    os.symlink(
        os.path.relpath(lay["outside"] / "victim", fake_home / ".config" / "pmcp"),
        fake_home / ".config" / "pmcp" / "pmcp.env",
    )
    _project_store(lay).write_text(f"HOME={fake_home}\n", encoding="utf-8")
    return fake_home


def test_a_project_store_cannot_retarget_the_user_store_through_home(
    lay: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from pmcp import cli
    from pmcp.remote_auth import build_remote_header_env_lookup

    _retarget_layout(lay)
    # HOME unset; the password database (simulated, never the real one) names
    # the operator's empty home -- the case override=False fills.
    operator_home = lay["home"]
    monkeypatch.delenv("HOME")
    monkeypatch.setattr(
        Path,
        "home",
        classmethod(lambda cls: Path(os.environ.get("HOME", str(operator_home)))),
    )
    cli.load_startup_env(dotenv_path=str(lay["base"] / "no-such.env"))

    assert "HOME" not in os.environ
    assert env_store.resolve_scope_path("user") == (
        operator_home / ".config" / "pmcp" / "pmcp.env"
    )
    assert build_remote_header_env_lookup(lay["project"])(VAR) is None
    assert _available() is False
    assert VAR not in os.environ
    err = capfd.readouterr().err
    assert "pmcp: Ignoring HOME in .env.pmcp: a project file supplies" in err
    _no_outside_value(err, dict(os.environ))
    assert str(lay["project"] / "fakehome") not in err


def test_the_user_store_path_is_pinned_before_any_repository_file_loads(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belt and braces: even a HOME that changes after startup moves nothing."""
    from pmcp import cli

    cli.load_startup_env(dotenv_path=str(lay["base"] / "no-such.env"))
    pinned = env_store.resolve_scope_path("user")
    monkeypatch.setenv("HOME", str(lay["project"] / "fakehome"))
    assert env_store.resolve_scope_path("user") == pinned


#: Variables a repository file might set to steer pmcp, its libraries or its
#: children -- classified ones, a mixed-case proxy, and ones nothing in pmcp reads.
_STEERING_SAMPLES = [
    "HTTPS_PROXY",
    "Http_Proxy",
    "hTTp_pRoxy",
    "All_Proxy",
    "SSLKEYLOGFILE",
    "NETRC",
    "XDG_CACHE_HOME",
    "PMCP_AUTH_TOKEN",
    "TMPDIR",
    "LD_PRELOAD",
    "NODE_OPTIONS",
    "UV_INDEX_URL",
    "PYTHONPATH",
]

_ENV_PROBE = (
    "import json, os, sys\n"
    "from pmcp import cli\n"
    "from pmcp.env_store import credential_value\n"
    "cli.load_startup_env(dotenv_path=sys.argv[1])\n"
    "print(json.dumps({\n"
    "    'env': {k: os.environ.get(k) for k in sys.argv[2:]},\n"
    "    'credential': {k: credential_value(k) for k in sys.argv[2:]},\n"
    "}))\n"
)


def _probe_env(
    lay: dict[str, Path], dotenv_arg: str, cwd: Path | None = None
) -> tuple[dict[str, dict[str, str | None]], subprocess.CompletedProcess[str]]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in {s.upper() for s in _STEERING_SAMPLES}
        and not k.startswith("PMCP")
        and k != VAR
    }
    env["HOME"] = str(lay["home"])
    proc = subprocess.run(
        [sys.executable, "-c", _ENV_PROBE, dotenv_arg, *_STEERING_SAMPLES, VAR],
        cwd=cwd or lay["project"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout.strip().splitlines()[-1]), proc


@pytest.mark.parametrize("name", [".env.pmcp", ".env"])
def test_no_repository_file_value_reaches_the_process_environment(
    name: str, lay: dict[str, Path]
) -> None:
    """Round 2: whatever a checkout file names -- a classified variable, a proxy
    in any case, a key-log file, a variable only a child reads -- none of it is
    in pmcp's environment; only its credential reaches a credential lookup."""
    body = "".join(f"{k}=from-the-checkout\n" for k in _STEERING_SAMPLES)
    (lay["project"] / name).write_text(body + f"{VAR}={INSIDE_VALUE}\n")
    # `.env` arrives as the discovered file, `.env.pmcp` as the cwd store.
    dotenv_arg = str(lay["project"] / ".env") if name == ".env" else "/nonexistent"
    out, proc = _probe_env(lay, dotenv_arg)
    assert out["env"] == dict.fromkeys([*_STEERING_SAMPLES, VAR])
    assert out["credential"][VAR] == INSIDE_VALUE
    # A name pmcp itself reads (any case, any *_proxy) is never a credential.
    for key in (
        "HTTPS_PROXY",
        "Http_Proxy",
        "hTTp_pRoxy",
        "All_Proxy",
        "SSLKEYLOGFILE",
        "NETRC",
        "XDG_CACHE_HOME",
        "PMCP_AUTH_TOKEN",
    ):
        assert out["credential"][key] is None, key
        assert f"pmcp: Ignoring {key} in {name}:" in proc.stderr
    assert "from-the-checkout" not in proc.stderr


def test_the_operator_home_env_still_loads_into_the_environment(
    lay: dict[str, Path],
) -> None:
    """N-A: the ``~/.env`` a ``uv tool``/``pip --user`` install's walk finds is
    outside every project -- the operator's own -- and loads as before, proxy
    and CA bundle included, with no warning."""
    (lay["home"] / ".env").write_text(
        "HTTPS_PROXY=http://corp-proxy:3128\n"
        "SSL_CERT_FILE=/etc/corp/ca.pem\n"
        f"{VAR}={USER_VALUE}\n"
    )
    out, proc = _probe_env(lay, str(lay["home"] / ".env"), cwd=lay["home"])
    assert out["env"]["HTTPS_PROXY"] == "http://corp-proxy:3128"
    assert out["env"][VAR] == USER_VALUE
    assert "Ignoring" not in proc.stderr
    assert "refusing" not in proc.stderr


def test_the_credential_check_keeps_checkout_values_out_of_the_environment(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    (lay["project"] / ".env").write_text(
        f"HTTPS_PROXY=http://127.0.0.1:9\n{VAR}={INSIDE_VALUE}\n"
    )
    assert _available() is True
    assert "HTTPS_PROXY" not in os.environ
    assert VAR not in os.environ
    assert env_store.credential_value("HTTPS_PROXY") is None


def test_the_operator_user_store_may_still_set_them(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    (lay["home"] / ".config" / "pmcp" / "pmcp.env").write_text(
        f"XDG_CACHE_HOME={lay['home'] / 'cache'}\n"
    )
    env_store.load_store("user")
    assert os.environ["XDG_CACHE_HOME"] == str(lay["home"] / "cache")
    monkeypatch.delenv("XDG_CACHE_HOME")


def test_a_repository_file_parses_as_load_dotenv_would(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quoting, comments, `export`, interpolation and a multi-line value survive."""
    for key in ("A367", "B367", "C367"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    _project_store(lay).write_text(
        "# comment\n"
        "export A367='one two'\n"
        "XDG_DATA_HOME=/x\n"
        'B367="multi\nline"\n'
        "C367=${A367}-z\n"
    )
    env_store.load_store("project", path=_project_store(lay))
    assert env_store.credential_value("A367") == "one two"
    assert env_store.credential_value("B367") == "multi\nline"
    assert env_store.credential_value("C367") == "one two-z"
    assert env_store.credential_value("XDG_DATA_HOME") is None
    for key in ("A367", "B367", "C367", "XDG_DATA_HOME"):
        assert key not in os.environ


# --------------------------------------------------------------------------- #
# Round 1, codex F001: absence is only ENOENT. An existing store behind an
# ancestor this process cannot search is NOT "no store".
# --------------------------------------------------------------------------- #

needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root searches a mode-000 directory"
)


@pytest.fixture
def locked(lay: dict[str, Path]) -> Iterator[Path]:
    """`<base>/locked/project` holding a store, with `locked` unsearchable."""
    gate = lay["base"] / "locked"
    project = gate / "project"
    (project / ".git").mkdir(parents=True)
    (project / ".env.pmcp").write_text(f"PMCP_FEEDBACK_TOKEN={_TOKEN}\n")
    (project / ".pmcp" / "tenants" / TENANT).mkdir(parents=True)
    os.chmod(gate, 0o600)
    try:
        yield project
    finally:
        os.chmod(gate, 0o700)


@needs_non_root
def test_the_feedback_gate_denies_a_store_behind_an_unsearchable_ancestor(
    locked: Path,
) -> None:
    decision = _decide(locked)
    assert decision.submit_allowed is False
    assert decision.reason == "gate_error"


@needs_non_root
def test_every_strict_reader_raises_for_a_store_behind_an_unsearchable_ancestor(
    locked: Path,
) -> None:
    with pytest.raises(PermissionError):
        env_store.managed_secret_keys_strict(locked)
    with pytest.raises(PermissionError):
        env_store.read_store("project", project=locked, strict=True)
    with pytest.raises(PermissionError):
        env_store.read_store("tenant", project=locked, tenant_id=TENANT, strict=True)
    with pytest.raises(PermissionError):
        env_store.read_store_for_update("project", locked / ".env.pmcp")


@needs_non_root
def test_a_lenient_reader_warns_rather_than_calling_it_absent(
    locked: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    assert env_store.read_store("project", project=locked) == {}
    assert "pmcp: refusing to read .env.pmcp: Permission denied" in (
        capfd.readouterr().err
    )


@needs_non_root
def test_every_write_raises_for_a_store_behind_an_unsearchable_ancestor(
    locked: Path, lay: dict[str, Path]
) -> None:
    from pmcp.atomic_write import atomic_write

    with pytest.raises(PermissionError):
        env_store.set_env_value("project", "K367", "v", project=locked)
    with pytest.raises(PermissionError):
        atomic_write(locked / "x", b"x", confine_to=locked)
    with pytest.raises(PermissionError):
        atomic_write(locked / "x", b"x", confine_to=None)


@needs_non_root
def test_the_user_store_behind_an_unsearchable_home_is_not_absent(
    lay: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Lenient readers warn once and read empty -- pmcp still starts; strict
    readers (the feedback gate, a rewrite) raise."""
    from pmcp import cli

    gate = lay["base"] / "lockedhome"
    home = gate / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    (home / ".config" / "pmcp" / "pmcp.env").write_text(f"{VAR}=x\n")
    monkeypatch.setenv("HOME", str(home))
    os.chmod(gate, 0o600)
    try:
        assert env_store.read_store("user") == {}
        assert env_store.read_store("user") == {}
        assert env_store.managed_secret_keys(lay["project"]) == set()
        cli.load_startup_env(dotenv_path=str(lay["base"] / "no-such.env"))
        assert VAR not in os.environ
        err = capfd.readouterr().err
        assert err.count("pmcp: refusing to read pmcp.env: Permission denied") == 1
        assert err.count("pmcp: refusing to load pmcp.env: Permission denied") == 1
        with pytest.raises(PermissionError):
            env_store.read_store("user", strict=True)
        with pytest.raises(PermissionError):
            env_store.managed_secret_keys_strict(lay["project"])
        with pytest.raises(PermissionError):
            env_store.read_store_for_update("user", home / ".config/pmcp/pmcp.env")
        with pytest.raises(PermissionError):
            env_store.set_env_value("user", "K367", "v")
        assert _decide(lay["project"]).reason == "gate_error"
    finally:
        os.chmod(gate, 0o700)


# --------------------------------------------------------------------------- #
# The no-dir_fd fallback (Windows): since Consiliency/pmcp#366's kernel-order
# walker accepts nested real directories there, a tenant store reads; any link
# on the way is still refused.
# --------------------------------------------------------------------------- #


@pytest.fixture
def no_dir_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    from pmcp import atomic_write as atomic_write_module

    monkeypatch.setattr(atomic_write_module, "_DIR_FD_SUPPORTED", False)


def test_the_fallback_reads_a_regular_tenant_store(
    lay: dict[str, Path], no_dir_fd: None
) -> None:
    _regular(_tenant_store(lay), lay)
    resolution = _tenant_headers(lay["project"])
    assert resolution.resolved_headers == {"Authorization": f"Bearer {INSIDE_VALUE}"}


@pytest.mark.parametrize("shape", ["link-out", "link-inside"])
def test_the_fallback_refuses_a_linked_tenant_store(
    shape: str,
    lay: dict[str, Path],
    no_dir_fd: None,
    capfd: pytest.CaptureFixture[str],
) -> None:
    {"link-out": _link_out, "link-inside": _link_inside}[shape](_tenant_store(lay), lay)
    resolution = _tenant_headers(lay["project"])
    assert resolution.missing_env_vars == [VAR]
    assert "pmcp: refusing to read pmcp.env" in capfd.readouterr().err


# --------------------------------------------------------------------------- #
# Round 2, claude F001: the proxy filter was case-sensitive; httpx/urllib take
# any *_proxy in any case, and SSLKEYLOGFILE/NETRC were missing. With no
# repository value in the environment, none of it can be set.
# --------------------------------------------------------------------------- #

_PROXY_CHILD = r"""
import sys, httpx
from pmcp.cli import load_startup_env
load_startup_env()
try:
    httpx.get("http://remote-mcp.example.test/mcp",
              headers={"Authorization": "Bearer USER-SECRET-372"}, timeout=5)
except Exception:
    pass
print(httpx.create_ssl_context().keylog_filename)
"""


@pytest.mark.parametrize(
    "name", ["HTTP_PROXY", "Http_Proxy", "hTTp_pRoxy", "All_Proxy", "all_proxy"]
)
def test_a_project_store_cannot_set_a_proxy_in_any_case(
    name: str, lay: dict[str, Path]
) -> None:
    seen: list[bytes] = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(15)
    port = srv.getsockname()[1]

    def serve() -> None:
        try:
            conn, _ = srv.accept()
            seen.append(conn.recv(65536))
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            conn.close()
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    _project_store(lay).write_text(
        f"{name}=http://127.0.0.1:{port}\n"
        f"SSLKEYLOGFILE={lay['project'] / 'keys.log'}\n"
        f"NETRC={lay['project'] / 'netrc'}\n"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.lower().endswith("_proxy") and k not in ("SSLKEYLOGFILE", "NETRC")
    }
    env["HOME"] = str(lay["home"])
    proc = subprocess.run(
        [sys.executable, "-c", _PROXY_CHILD],
        cwd=lay["project"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    srv.close()
    assert proc.returncode == 0, proc.stderr[-800:]
    assert not any(b"USER-SECRET-372" in s for s in seen), (
        f"{name} from a project .env.pmcp proxied an Authorization header"
    )
    assert proc.stdout.strip().splitlines()[-1] == "None", "SSLKEYLOGFILE took effect"
    assert f"pmcp: Ignoring {name} in .env.pmcp:" in proc.stderr


@pytest.mark.parametrize(
    "key",
    [
        "Path",
        "home",
        "HoMe",
        "hTTps_pRoxy",
        "foo_proxy",
        "sslkeylogfile",
        "Netrc",
        "pmcp_auth_token",
        "Pmcp_Manifest_Path",
        "pmcp_log_level",
    ],
)
def test_a_name_pmcp_reads_is_recognised_in_any_case(key: str) -> None:
    """Windows' environment is case-insensitive and urllib takes any *_proxy."""
    assert env_store.is_pmcp_environment_name(key)


def test_an_ordinary_credential_name_is_not_a_pmcp_name() -> None:
    for key in ("BRAVE_API_KEY", "GITHUB_PERSONAL_ACCESS_TOKEN", "PROXY_TOKEN"):
        assert not env_store.is_pmcp_environment_name(key)


def test_credential_value_never_answers_a_pmcp_name_from_a_repository_file(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("Https_Proxy", raising=False)
    _project_store(lay).write_text("Https_Proxy=http://x\nhome=/x\n")
    env_store.load_store("project", path=_project_store(lay))
    # The map holds them; the one lookup never answers them.
    assert env_store.repo_credential_names() == {"Https_Proxy", "home"}
    assert env_store.credential_value("Https_Proxy") is None
    assert env_store.credential_value("home") is None


# --------------------------------------------------------------------------- #
# Every legitimate credential path still works with a project-store credential
# that is NOT in the environment (round 2, coordinator item 5).
# --------------------------------------------------------------------------- #


@pytest.fixture
def brave_in_project_store(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> str:
    from pmcp import cli

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    _project_store(lay).write_text("BRAVE_API_KEY=brave-from-project-372\n")
    cli.load_startup_env(dotenv_path=str(lay["base"] / "no-such.env"))
    assert "BRAVE_API_KEY" not in os.environ
    return "brave-from-project-372"


def test_a_server_spawn_gets_its_project_store_credential(
    brave_in_project_store: str, lay: dict[str, Path]
) -> None:
    from pmcp.manifest.installer import build_install_child_env
    from pmcp.manifest.loader import load_manifest

    server = load_manifest().get_server("brave-search")
    assert server is not None
    child = build_install_child_env(server, lay["project"])
    assert child["BRAVE_API_KEY"] == brave_in_project_store


def test_the_provision_gate_sees_a_project_store_credential(
    brave_in_project_store: str,
) -> None:
    from pmcp.manifest.installer import check_api_key
    from pmcp.manifest.loader import load_manifest

    server = load_manifest().get_server("brave-search")
    assert server is not None
    asyncio.run(check_api_key(server))  # raises MissingApiKeyError if not seen


def test_a_manifest_config_carries_its_project_store_credential(
    brave_in_project_store: str,
) -> None:
    from pmcp.config.loader import _manifest_server_to_config, _credential_value
    from pmcp.manifest.loader import load_manifest

    server = load_manifest().get_server("brave-search")
    assert server is not None
    config = _manifest_server_to_config(server, _credential_value)
    assert config.config.env["BRAVE_API_KEY"] == brave_in_project_store  # type: ignore[union-attr]


def test_auth_availability_and_the_credential_check_see_it(
    brave_in_project_store: str,
) -> None:
    assert env_store.credential_value("BRAVE_API_KEY") == brave_in_project_store
    assert GatewayTools._check_api_key_available(object(), "BRAVE_API_KEY") is True  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The absence rule at its source (codex r1 F001): the confined read and the
# existence helpers, called directly -- #366's strict project-root resolution
# now raises first on the reader paths, so these pin the primitives themselves.
# --------------------------------------------------------------------------- #


@needs_non_root
def test_read_confined_raises_for_a_root_behind_an_unsearchable_ancestor(
    lay: dict[str, Path],
) -> None:
    from pmcp.atomic_write import read_confined

    gate = lay["base"] / "locked2"
    root = gate / "root"
    root.mkdir(parents=True)
    (root / ".env.pmcp").write_text("K=v\n")
    os.chmod(gate, 0o600)
    try:
        with pytest.raises(PermissionError):
            read_confined(root / ".env.pmcp", root, verb="read")
    finally:
        os.chmod(gate, 0o700)


@needs_non_root
def test_is_absent_calls_only_enoent_and_enotdir_absent(
    lay: dict[str, Path],
) -> None:
    """The one absence rule (``atomic_write.is_absent``, Consiliency/pmcp#366)."""
    from pmcp.atomic_write import is_absent

    regular = lay["base"] / "regular"
    regular.write_text("x")
    assert is_absent(lay["base"] / "missing") is True
    assert is_absent(regular / "below-a-file") is True  # ENOTDIR
    gate = lay["base"] / "locked3"
    (gate / "x").mkdir(parents=True)
    os.chmod(gate, 0o600)
    try:
        with pytest.raises(PermissionError):
            is_absent(gate / "x")
    finally:
        os.chmod(gate, 0o700)


# --------------------------------------------------------------------------- #
# Round 3 on Consiliency/pmcp#372.
# --------------------------------------------------------------------------- #

# claude F001: whether a startup-found `.env` is the operator's is decided by
# where it is relative to HOME, not by project marker files.


def _markerless_checkout_venv(lay: dict[str, Path]) -> tuple[Path, Path]:
    """A `requirements.txt` checkout from an archive: no .git, no pyproject."""
    checkout = lay["base"] / "archive-checkout"
    checkout.mkdir()
    (checkout / "requirements.txt").write_text("pmcp\n")
    site = checkout / ".venv" / "lib" / "python3" / "site-packages"
    _install_pmcp_in(site)
    (site / "runme.py").write_text(_HEADER_RUNNER, encoding="utf-8")
    return checkout, site


_CHILD_STEERING = (
    "import json, os, subprocess, sys\n"
    "import pmcp\n"
    "assert pmcp.__file__.startswith(sys.argv[1]), pmcp.__file__\n"
    "from pmcp.cli import load_startup_env, _is_pmcp_system_service_active\n"
    "load_startup_env()\n"
    "seen = {}\n"
    "def spy(*a, **k):\n"
    "    seen.update(k.get('env') or {})\n"
    "    raise FileNotFoundError\n"
    "subprocess.run = spy\n"
    "import shutil; shutil.which = lambda n: '/usr/bin/' + n\n"
    "_is_pmcp_system_service_active()\n"
    "keys = ['LD_PRELOAD', 'Https_Proxy', sys.argv[2]]\n"
    "print(json.dumps({'env': {k: os.environ.get(k) for k in keys},"
    " 'child': {k: seen.get(k) for k in keys}}))\n"
)


@pytest.mark.parametrize("shape", ["regular", "link-out"])
def test_a_markerless_checkout_env_is_a_repository_file(
    shape: str, lay: dict[str, Path]
) -> None:
    checkout, site = _markerless_checkout_venv(lay)
    body = f"LD_PRELOAD={checkout}/x.so\nHttps_Proxy=http://127.0.0.1:9\n{VAR}=v\n"
    if shape == "regular":
        (checkout / ".env").write_text(body)
    else:
        (lay["outside"] / "steer").write_text(body)
        os.symlink("../outside/steer", checkout / ".env")
    (site / "steer.py").write_text(_CHILD_STEERING)
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() not in ("LD_PRELOAD", "HTTPS_PROXY", VAR)
        and not k.startswith("PMCP")
    }
    env.update(HOME=str(lay["home"]), PYTHONPATH=str(site))
    proc = subprocess.run(
        [sys.executable, str(site / "steer.py"), str(site), VAR],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["env"] == {"LD_PRELOAD": None, "Https_Proxy": None, VAR: None}
    assert out["child"] == {"LD_PRELOAD": None, "Https_Proxy": None, VAR: None}


def test_an_env_in_an_ancestor_of_home_is_the_operators(lay: dict[str, Path]) -> None:
    """A user install's walk can pass HOME's parents; a `.env` there is yours."""
    site = lay["home"] / ".local" / "lib" / "python3" / "site-packages"
    _install_pmcp_in(site)
    (site / "runme.py").write_text(_HEADER_RUNNER, encoding="utf-8")
    (lay["base"] / ".env").write_text(f"{VAR}={USER_VALUE}\n")
    out, proc = _header_probe(lay, site)
    assert out["env"] == USER_VALUE
    assert "Ignoring" not in proc.stderr and "refusing" not in proc.stderr


# grok F001: no interpolation across the trust boundary.


@pytest.fixture
def operator_secrets(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    secrets = {
        "PMCP_AUTH_TOKEN": "operator-token-372",
        "GITHUB_TOKEN": "user-store-github-372",
    }
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)
    (lay["home"] / ".config" / "pmcp" / "pmcp.env").write_text(
        "USER_ONLY_SECRET=user-only-372\n"
    )
    for key in ("LEAK", "FROM_USER", "FROM_STORE", "HTTP_PROXY", "SSLKEYLOGFILE"):
        monkeypatch.delenv(key, raising=False)
    return {**secrets, "USER_ONLY_SECRET": "user-only-372"}


@pytest.mark.parametrize(
    "template",
    [
        "${PMCP_AUTH_TOKEN}",
        "${GITHUB_TOKEN}",
        "${USER_ONLY_SECRET}",
        "pre-${GITHUB_TOKEN}-post",
        "${NOT_IN_FILE:-x}${GITHUB_TOKEN}",
        "${A:-${GITHUB_TOKEN}}",
    ],
)
def test_a_repository_file_cannot_launder_an_operator_secret(
    template: str,
    lay: dict[str, Path],
    operator_secrets: dict[str, str],
    capfd: pytest.CaptureFixture[str],
) -> None:
    from pmcp.remote_auth import build_remote_header_env_lookup, resolve_remote_headers

    _project_store(lay).write_text(f"LEAK={template}\n")
    env_store.load_store("project", project=lay["project"])
    lookup = build_remote_header_env_lookup(lay["project"])
    resolved = resolve_remote_headers({"Authorization": "Bearer ${LEAK}"}, lookup)
    tenant_dir = lay["project"] / ".pmcp" / "tenants" / TENANT
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "pmcp.env").write_text(f"LEAK={template}\n")
    tenant = resolve_remote_headers_for_tenant(
        {"Authorization": "Bearer ${LEAK}"},
        server_name="r",
        tenant_id=TENANT,
        project_root=lay["project"],
    )
    # An unexpandable binding is absent, not an emptied or partial value.
    if "${NOT_IN_FILE:-x}" not in template and ":-${" not in template:
        assert "LEAK" not in env_store.repo_credential_names()
    for secret in operator_secrets.values():
        assert secret not in str(env_store.credential_value("LEAK"))
        assert secret not in str(lookup("LEAK"))
        assert secret not in resolved.resolved_headers["Authorization"]
        assert secret not in tenant.resolved_headers["Authorization"]
    _no_outside_value(capfd.readouterr().err)


def test_a_repository_file_expands_its_own_earlier_keys(lay: dict[str, Path]) -> None:
    _project_store(lay).write_text(
        "BASE372=https://api.example\n"
        "URL372=${BASE372}/v1\n"
        "DEF372=${MISSING372:-fallback}\n"
        "LITERAL372=$BASE372\n"
        "LATER372=${AFTER372}\n"
        "AFTER372=x\n"
    )
    env_store.load_store("project", project=lay["project"])
    assert env_store.credential_value("URL372") == "https://api.example/v1"
    assert env_store.credential_value("DEF372") == "fallback"
    assert env_store.credential_value("LITERAL372") == "$BASE372"
    # Only EARLIER keys, as python-dotenv expands: a forward reference is
    # outside what the file has defined at that point.
    assert env_store.credential_value("LATER372") is None


def test_an_unexpandable_value_is_ignored_with_one_value_free_line(
    lay: dict[str, Path],
    operator_secrets: dict[str, str],
    capfd: pytest.CaptureFixture[str],
) -> None:
    _project_store(lay).write_text("LEAK=${GITHUB_TOKEN}\n")
    env_store.load_store("project", project=lay["project"])
    err = capfd.readouterr().err
    assert err.count("pmcp: Ignoring LEAK in .env.pmcp: its value refers to") == 1
    assert "GITHUB_TOKEN" not in err


# codex F001 / grok F001: every header fallback goes through the one gate.


@pytest.mark.parametrize(
    "name", ["PMCP_AUTH_TOKEN", "HTTP_PROXY", "Https_Proxy", "SSLKEYLOGFILE", "home"]
)
def test_no_header_resolves_a_pmcp_name_from_a_repository_store(
    name: str, lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.remote_auth import build_remote_header_env_lookup, resolve_remote_headers

    monkeypatch.delenv(name, raising=False)
    _project_store(lay).write_text(f"{name}=repo-value-372\n")
    tenant_dir = lay["project"] / ".pmcp" / "tenants" / TENANT
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "pmcp.env").write_text(f"{name}=repo-value-372\n")
    env_store.load_store("project", project=lay["project"])
    headers = {"Authorization": "Bearer ${%s}" % name}
    project_result = resolve_remote_headers(
        headers, build_remote_header_env_lookup(lay["project"])
    )
    for include in (True, False):
        tenant_result = resolve_remote_headers_for_tenant(
            headers,
            server_name="remote",
            tenant_id=TENANT,
            project_root=lay["project"],
            include_process_env=include,
        )
        assert tenant_result.missing_env_vars == [name]
    assert project_result.missing_env_vars == [name]


# codex F002: precedence by membership.


def test_an_exported_empty_value_is_not_filled_from_a_repository_file(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp.manifest.installer import MissingApiKeyError, check_api_key
    from pmcp.manifest.loader import ServerConfig
    from pmcp.remote_auth import build_remote_header_env_lookup

    monkeypatch.setenv("BRAVE_API_KEY", "")
    _project_store(lay).write_text("BRAVE_API_KEY=repo-value-372\n")
    env_store.load_store("project", project=lay["project"])
    assert env_store.credential_value("BRAVE_API_KEY") is None
    assert build_remote_header_env_lookup(lay["project"])("BRAVE_API_KEY") is None
    server = ServerConfig(
        name="brave-search",
        description="probe",
        keywords=[],
        install={},
        command="python",
        args=[],
        requires_api_key=True,
        env_var="BRAVE_API_KEY",
    )
    with pytest.raises(MissingApiKeyError):
        asyncio.run(check_api_key(server))
    assert GatewayTools._check_api_key_available(object(), "BRAVE_API_KEY") is False  # type: ignore[arg-type]


def test_the_user_store_decides_by_membership_over_a_project_store(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One documented order for every lookup, headers included: environment,
    user store, then project -- and an empty user entry is "unavailable",
    never a fall-through to the project file."""
    from pmcp.remote_auth import build_remote_header_env_lookup

    monkeypatch.delenv("MEMBER372", raising=False)
    user = lay["home"] / ".config" / "pmcp" / "pmcp.env"
    _project_store(lay).write_text("MEMBER372=project\n")
    user.write_text("MEMBER372=\n")
    assert build_remote_header_env_lookup(lay["project"])("MEMBER372") is None
    user.write_text("MEMBER372=user\n")
    assert build_remote_header_env_lookup(lay["project"])("MEMBER372") == "user"
    user.write_text("OTHER372=x\n")
    assert build_remote_header_env_lookup(lay["project"])("MEMBER372") == "project"


def test_the_feedback_gate_denies_a_dangling_project_store_link(
    lay: dict[str, Path],
) -> None:
    """A symlinked project store is refused whatever it points at (or doesn't)."""
    os.symlink("absent-target", _project_store(lay))
    decision = _decide(lay["project"])
    assert decision.submit_allowed is False
    assert decision.reason == "gate_error"


def test_the_user_store_path_is_pinned_even_when_no_env_is_discovered(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pmcp import cli

    monkeypatch.setattr(cli, "find_dotenv", lambda: "")
    cli.load_startup_env()
    pinned = env_store.resolve_scope_path("user")
    assert pinned == lay["home"] / ".config" / "pmcp" / "pmcp.env"
    monkeypatch.setenv("HOME", str(lay["project"] / "fakehome"))
    assert env_store.resolve_scope_path("user") == pinned


def test_a_project_root_that_cannot_be_found_warns_or_raises(
    lay: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Locating the project store can itself fail -- here the working directory
    was removed. A lenient reader warns once and reads empty; a strict one raises."""
    gone = lay["base"] / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    assert env_store.read_store("project") == {}
    assert "pmcp: refusing to read .env.pmcp:" in capfd.readouterr().err
    with pytest.raises(OSError):
        env_store.read_store("project", strict=True)


def test_a_rewrite_refuses_a_user_store_that_is_not_a_regular_file(
    lay: dict[str, Path],
) -> None:
    """The read before a rewrite is strict for the user store too: a fifo at
    ``~/.config/pmcp/pmcp.env`` is refused, never replaced by a fresh file."""
    import argparse

    from pmcp.cli_commands.secrets import run_secrets_set

    store = lay["home"] / ".config" / "pmcp" / "pmcp.env"
    os.mkfifo(store)
    out = asyncio.run(
        run_secrets_set(
            argparse.Namespace(scope="user", key="K372", value="v", project=None)
        )
    )
    assert out["ok"] is False
    assert str(out["error"]).startswith("refusing to write pmcp.env:")
    assert stat.S_ISFIFO(os.lstat(store).st_mode)


def test_a_tenant_lookup_follows_the_documented_order(
    lay: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment, user store, tenant store, then the project store -- or, with
    include_process_env=False, the tenant store alone. The user store is read
    directly, so this holds without a startup load too."""
    monkeypatch.delenv("ORDER372", raising=False)
    user = lay["home"] / ".config" / "pmcp" / "pmcp.env"
    tenant = _tenant_store(lay)
    tenant.parent.mkdir(parents=True)

    def header(include: bool = True) -> str:
        return resolve_remote_headers_for_tenant(
            {"X": "${ORDER372}"},
            server_name="r",
            tenant_id=TENANT,
            project_root=lay["project"],
            include_process_env=include,
        ).resolved_headers["X"]

    _project_store(lay).write_text("ORDER372=project\n")
    env_store.load_store("project", project=lay["project"])
    assert header() == "project"
    tenant.write_text("ORDER372=tenant\n")
    assert header() == "tenant"
    user.write_text("ORDER372=user\n")
    assert header() == "user"
    assert header(include=False) == "tenant"
    monkeypatch.setenv("ORDER372", "env")
    assert header() == "env"
    assert header(include=False) == "tenant"
