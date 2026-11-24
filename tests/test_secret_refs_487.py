"""1Password secret references for downstream servers (Consiliency/pmcp#487 P2).

Plan sections 3.1-3.5, 3.7 and 3.10 (the ``secret_refs`` capability and the
gated ``pmcp secrets set``), checked against plan section 6 (security
properties) and section 7 (verification).

Never calls the real ``op`` or 1Password: every resolution goes through a FAKE
``op`` executable written into a private temporary directory, which records
its argv, environment, working directory and stdin, and answers each
reference from a canned table (a value, a failure with a given stderr, a hang,
an overflow). No credential is real.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from pmcp import cli, credential_state as cs, env_store, secret_refs, secret_registry
from pmcp.cli_commands import secrets
from pmcp.config.loader import load_configs, manifest_server_to_config
from pmcp.credential_state import CredentialUnavailableError
from pmcp.manifest.installer import build_install_child_env, resolve_install_secrets
from pmcp.manifest.loader import load_manifest
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="secret references are POSIX-only (plan 3.1)"
)

TOKEN = "ops_" + "T0k3n" * 12  # fake service-account token
NAMESPACED = "BRIGHTDATA_API_TOKEN"
LEGACY = "API_TOKEN"
REF = "op://PMCP Gateway/BrightData/credential"
REF_ID = "op://abc123def456/xyz789/password"
VALUE = "resolved-brightdata-key-0001"


# --------------------------------------------------------------------------- #
# The fake op and the host
# --------------------------------------------------------------------------- #

FAKE_OP = r"""#!{python}
import json, os, sys, time
HERE = {here!r}
cfg = json.load(open(os.path.join(HERE, "table.json")))
ref = sys.argv[-1]
try:
    stdin_data = sys.stdin.read()
except Exception:
    stdin_data = "<unreadable>"
record = {{
    "argv": sys.argv[1:],
    "env": dict(os.environ),
    "cwd": os.getcwd(),
    "stdin": stdin_data,
    "start": time.time(),
    "pid": os.getpid(),
}}
entry = cfg.get(ref, cfg.get("*", {{}}))
time.sleep(entry.get("sleep", 0))
if entry.get("hang"):
    with open(os.path.join(HERE, "hung.%d" % os.getpid()), "w") as f:
        f.write("x")
    time.sleep(3600)
if entry.get("overflow"):
    sys.stdout.write("x" * entry["overflow"])
    sys.stdout.flush()
record["end"] = time.time()
with open(os.path.join(HERE, "calls.jsonl"), "a") as f:
    f.write(json.dumps(record) + "\n")
if "stderr" in entry:
    sys.stderr.write(entry["stderr"])
if "stdout" in entry:
    sys.stdout.write(entry["stdout"])
sys.exit(entry.get("exit", 0))
"""


@dataclass
class FakeOp:
    directory: Path
    path: Path

    def table(self, entries: dict[str, dict[str, Any]]) -> None:
        (self.directory / "table.json").write_text(json.dumps(entries))

    def calls(self) -> list[dict[str, Any]]:
        log = self.directory / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]


@dataclass
class Host:
    home: Path
    root: Path
    store: Path
    op: FakeOp

    def write_store(self, text: str) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(text)

    def write_token(
        self, text: str = TOKEN + "\n", *, where: str = "credstore"
    ) -> Path:
        path = {
            "credstore": self.home / ".config" / "credstore",
            "pmcp_config": self.home / ".config" / "pmcp",
        }[where] / secret_refs.TOKEN_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(0o600)
        return path


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Host:
    base = Path(os.path.realpath(tmp_path))
    base.chmod(0o700)
    home = base / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    root = base / "project"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(root)
    for key in (NAMESPACED, LEGACY, "CANARY_SECRET", "OP_SERVICE_ACCOUNT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    bindir = _private_dir(base / "opbin")
    script = bindir / "op"
    script.write_text(FAKE_OP.format(python=sys.executable, here=str(bindir)))
    script.chmod(0o700)
    op = FakeOp(bindir, script)
    op.table({"*": {"stdout": VALUE}})
    # The fake op lives in a private directory: the trust walk checks it and
    # every ancestor up to the test's own scratch root (a CI runner's
    # /var/tmp is sticky world-writable, which the real walk refuses).
    secret_refs._test_overrides["op_search_paths"] = (str(script),)
    secret_refs._test_overrides["trust_root"] = str(base)
    secret_refs._test_overrides["state_dir"] = str(base / "opstate")
    return Host(
        home=home,
        root=root,
        store=home / ".config" / "pmcp" / "pmcp.env",
        op=op,
    )


def _start(host: Host) -> None:
    cli.load_startup_env(dotenv_path=str(host.home / ".env"))


def _brightdata() -> Any:
    server = load_manifest().get_server("brightdata")
    assert server is not None
    return server


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _local(
    env: dict[str, str],
    sources: dict[str, str] | None = None,
    *,
    opted: bool = False,
    name: str = "srv",
) -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name,
        source="user",
        config=LocalMcpServerConfig(command="true", env=env),
        credential_sources=sources or {},
        resolve_secret_refs=opted,
    )


# --------------------------------------------------------------------------- #
# Grammar (plan 3.3)
# --------------------------------------------------------------------------- #

SUPPORTED = [
    "op://PMCP Gateway/BrightData/credential",
    "op://abc123def456/xyz789/password",
    "OP://v/i/f",
    "op://v/i/section/f",
    "op://v_1/i.2/f-3",
]
UNSUPPORTED = [
    " op://v/i/f",
    "op://v/i/f?attribute=otp",
    "op://v/i/f?ssh-format=openssh",
    "op://$ENV/i/f",
    "op://v/i",
    "op://v/i/s/f/x",
    "op://v/i/f ",
    "op://v/ i/f",
    "op://v/i /f",
    "op://./i/f",
    "op://v/../f",
    "op://v/i/f#frag",
    "op://v/i%20x/f",
    "op://user@v/i/f",
    "op://v:1/i/f",
    "op://v\\x/i/f",
    "op://v/i/f\n",
    "op://v/i/\x00f",
    "op://v/i/‮f",
    "op://v/" + "a" * 65 + "/f",
]


@pytest.mark.parametrize("value", SUPPORTED)
def test_supported_references(value: str) -> None:
    assert secret_refs.is_reference_shaped(value)
    assert secret_refs.parse_reference(value) == value


@pytest.mark.parametrize("value", UNSUPPORTED)
def test_unsupported_reference_shaped_values(value: str) -> None:
    assert secret_refs.is_reference_shaped(value)
    assert secret_refs.parse_reference(value) is None


def test_a_64_character_segment_is_the_limit() -> None:
    assert secret_refs.parse_reference("op://v/" + "a" * 64 + "/f")


@pytest.mark.parametrize("value", ["plain value", "xop://v/i/f", "", "op:/v/i/f"])
def test_plain_values(value: str) -> None:
    assert not secret_refs.is_reference_shaped(value)


# --------------------------------------------------------------------------- #
# The token (plan 3.1)
# --------------------------------------------------------------------------- #


def test_no_token_file_is_op_not_configured(host: Host) -> None:
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.read_service_account_token()
    assert raised.value.reason == cs.OP_NOT_CONFIGURED


def test_the_token_is_never_taken_from_the_environment(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", TOKEN)
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.read_service_account_token()
    assert raised.value.reason == cs.OP_NOT_CONFIGURED


def test_the_credstore_token_is_read_and_one_newline_stripped(host: Host) -> None:
    host.write_token(TOKEN + "\r\n")
    read = secret_refs.read_service_account_token()
    assert read.token == TOKEN
    assert read.source == "credstore"
    assert TOKEN not in repr(read)


def test_paths_are_searched_in_order(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_token(TOKEN.replace("T0k3n", "PMCPc"), where="pmcp_config")
    assert secret_refs.read_service_account_token().source == "pmcp_config"
    host.write_token()
    assert secret_refs.read_service_account_token().source == "credstore"
    creds = _private_dir(host.home.parent / "creds")
    (creds / secret_refs.TOKEN_FILE_NAME).write_text(TOKEN.replace("T0k3n", "SYSTD"))
    (creds / secret_refs.TOKEN_FILE_NAME).chmod(0o400)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    secret_refs.capture_startup_environment()
    assert "CREDENTIALS_DIRECTORY" not in os.environ  # removed once captured
    read = secret_refs.read_service_account_token()
    assert read.source == "systemd_credential"
    assert "SYSTD" in read.token


def test_credentials_directory_without_our_credential_is_not_present(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    creds = _private_dir(host.home.parent / "creds")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    secret_refs.capture_startup_environment()
    host.write_token()
    assert secret_refs.read_service_account_token().source == "credstore"


def test_credentials_directory_is_captured_only_once(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_refs.capture_startup_environment()  # nothing there at startup
    creds = _private_dir(host.home.parent / "creds")
    (creds / secret_refs.TOKEN_FILE_NAME).write_text(TOKEN)
    (creds / secret_refs.TOKEN_FILE_NAME).chmod(0o600)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    secret_refs.capture_startup_environment()  # a later setter is not trusted
    with pytest.raises(CredentialUnavailableError):
        secret_refs.read_service_account_token()


@pytest.mark.parametrize(
    "content",
    ["", "not-a-token", "ops_", "ops_has space", "ops_" + "a" * 5000, "ops_x\n\n"],
)
def test_a_bad_token_file_is_token_invalid_and_the_next_path_is_not_tried(
    host: Host, content: str
) -> None:
    host.write_token(content)
    host.write_token(where="pmcp_config")  # a good one later in the order
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.read_service_account_token()
    assert raised.value.reason == cs.TOKEN_INVALID


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660, 0o644])
def test_group_or_other_bits_are_token_invalid(host: Host, mode: int) -> None:
    host.write_token().chmod(mode)
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.read_service_account_token()
    assert raised.value.reason == cs.TOKEN_INVALID


@pytest.mark.parametrize("mode", [0o400, 0o600])
def test_owner_only_modes_pass(host: Host, mode: int) -> None:
    host.write_token().chmod(mode)
    assert secret_refs.read_service_account_token().token == TOKEN


def test_a_fifo_token_file_is_refused_without_blocking(host: Host) -> None:
    path = host.home / ".config" / "credstore" / secret_refs.TOKEN_FILE_NAME
    path.parent.mkdir(parents=True)
    os.mkfifo(path, 0o600)
    started = time.monotonic()
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.read_service_account_token()
    assert raised.value.reason == cs.TOKEN_INVALID
    assert time.monotonic() - started < 5


def test_a_fifo_swapped_in_after_the_walk_never_blocks(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk's ``lstat`` saw a regular file; a FIFO took its place before
    the open. ``O_NONBLOCK`` keeps the open from blocking, and ``fstat`` of
    the descriptor refuses it (plan 3.1)."""
    from pmcp import atomic_write

    directory = _private_dir(host.home / ".config" / "credstore")
    regular = directory / "regular"
    regular.write_text(TOKEN)
    fifo = directory / secret_refs.TOKEN_FILE_NAME
    os.mkfifo(fifo, 0o600)

    def swapped_walk(
        path: Path, confine_to: Path, label: str, verb: str = "write"
    ) -> Any:
        return atomic_write._Confined(str(directory), fifo.name, os.lstat(regular), [])

    monkeypatch.setattr(atomic_write, "_walk_confined", swapped_walk)
    started = time.monotonic()
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.read_service_account_token()
    assert raised.value.reason == cs.TOKEN_INVALID
    assert time.monotonic() - started < 5


def test_a_symlinked_token_file_is_refused(host: Host) -> None:
    real = host.home / "real-token"
    real.write_text(TOKEN)
    real.chmod(0o600)
    path = host.home / ".config" / "credstore" / secret_refs.TOKEN_FILE_NAME
    path.parent.mkdir(parents=True)
    path.symlink_to(real)
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.read_service_account_token()
    assert raised.value.reason == cs.TOKEN_INVALID


def test_a_symlinked_parent_directory_is_refused(host: Host) -> None:
    elsewhere = _private_dir(host.home / "elsewhere")
    (elsewhere / secret_refs.TOKEN_FILE_NAME).write_text(TOKEN)
    (elsewhere / secret_refs.TOKEN_FILE_NAME).chmod(0o600)
    (host.home / ".config" / "credstore").symlink_to(elsewhere)
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.read_service_account_token()
    assert raised.value.reason == cs.TOKEN_INVALID


def test_the_token_is_cached_by_identity_and_reread_on_change(host: Host) -> None:
    path = host.write_token()
    assert secret_refs.read_service_account_token().token == TOKEN
    other = TOKEN.replace("T0k3n", "R0t4t")
    replacement = path.with_name("next")
    replacement.write_text(other)
    replacement.chmod(0o600)
    os.replace(replacement, path)
    assert secret_refs.read_service_account_token().token == other


def test_the_token_is_registered_for_redaction_as_soon_as_read(host: Host) -> None:
    host.write_token()
    secret_refs.read_service_account_token()
    assert TOKEN not in secret_registry.redact_registered(f"x {TOKEN} y")


def test_token_status_and_path_report_are_value_free(host: Host) -> None:
    assert secret_refs.token_status() == ("op_not_configured", None)
    host.write_token()
    assert secret_refs.token_status() == ("usable", "credstore")
    report = json.dumps(secret_refs.token_path_report())
    assert TOKEN not in report and "usable" in report


def test_platform_unsupported(host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(secret_refs, "_platform_supported", lambda: False)
    host.write_token()
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.read_service_account_token()
    assert raised.value.reason == cs.PLATFORM_UNSUPPORTED


# --------------------------------------------------------------------------- #
# The op executable (plan 3.4)
# --------------------------------------------------------------------------- #


def test_the_fake_op_is_trusted_and_its_real_path_is_executed(host: Host) -> None:
    alias = host.op.directory / "op-alias"
    alias.symlink_to(host.op.path)
    secret_refs._test_overrides["op_search_paths"] = (str(alias),)
    assert secret_refs.trusted_op_executable() == os.path.realpath(host.op.path)


def test_an_untrusted_ancestor_is_refused(host: Host) -> None:
    open_dir = host.op.directory.parent / "world"
    open_dir.mkdir()
    open_dir.chmod(0o777)
    inner = _private_dir(open_dir / "bin")
    exe = inner / "op"
    exe.write_bytes(host.op.path.read_bytes())
    exe.chmod(0o700)
    secret_refs._test_overrides["op_search_paths"] = (str(exe),)
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.trusted_op_executable()
    assert raised.value.reason == cs.OP_UNTRUSTED


def test_a_sticky_world_writable_ancestor_is_refused(host: Host) -> None:
    sticky = host.op.directory.parent / "sticky"
    sticky.mkdir()
    sticky.chmod(0o1777)
    exe = sticky / "op"
    exe.write_bytes(host.op.path.read_bytes())
    exe.chmod(0o700)
    secret_refs._test_overrides["op_search_paths"] = (str(exe),)
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.trusted_op_executable()
    assert raised.value.reason == cs.OP_UNTRUSTED


@pytest.mark.parametrize("mode", [0o720, 0o702, 0o600])
def test_a_writable_or_non_executable_file_is_refused(host: Host, mode: int) -> None:
    host.op.path.chmod(mode)
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.trusted_op_executable()
    assert raised.value.reason == cs.OP_UNTRUSTED


def test_a_real_root_owned_binary_passes_the_real_walk() -> None:
    real = os.path.realpath("/bin/sh")
    if not os.path.exists(real):
        pytest.skip("no /bin/sh")
    assert secret_refs._check_trusted_executable(real)


def test_an_explicit_op_path_never_falls_back(host: Host) -> None:
    secret_refs.set_operator_op_path(str(host.op.directory / "missing-op"))
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.trusted_op_executable()
    assert raised.value.reason == cs.OP_MISSING
    host.op.path.chmod(0o722)
    secret_refs.set_operator_op_path(str(host.op.path))
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.trusted_op_executable()
    assert raised.value.reason == cs.OP_UNTRUSTED


def test_no_op_anywhere_is_op_missing(host: Host) -> None:
    secret_refs._test_overrides["op_search_paths"] = (str(host.home / "nope"),)
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.trusted_op_executable()
    assert raised.value.reason == cs.OP_MISSING


def test_op_path_comes_only_from_operator_configs(host: Host) -> None:
    (host.root / ".mcp.json").write_text(json.dumps({"opPath": "/project/op"}))
    user = host.home / ".mcp.json"
    user.write_text(json.dumps({"op_path": str(host.op.path)}))
    load_configs(project_root=host.root, user_config_paths=[user])
    assert secret_refs._OPERATOR_OP_PATH == str(host.op.path)
    load_configs(project_root=host.root, user_config_paths=[])
    assert secret_refs._OPERATOR_OP_PATH is None  # the project's is ignored


# --------------------------------------------------------------------------- #
# Running op (plan 3.4)
# --------------------------------------------------------------------------- #


def test_op_argv_env_cwd_and_stdin(host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    host.write_token()
    monkeypatch.setenv("CANARY_SECRET", "gateway-only-canary-value")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    env_store.record_env_origin("HTTPS_PROXY", "external")
    deadline = time.monotonic() + 10
    assert _run(secret_refs.resolve_reference(REF, deadline)) == VALUE
    (call,) = host.op.calls()
    state = os.path.realpath(host.op.directory.parent / "opstate")
    assert call["argv"] == [
        "read",
        "--no-newline",
        "--no-color",
        "--cache=false",
        "--config",
        state,
        "--",
        REF,
    ]
    assert os.path.realpath(call["cwd"]) == state
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o700
    assert call["stdin"] == ""
    env = call["env"]
    # Only op's own builder: nothing of the gateway's environment.
    allowed = {"PATH", "HOME", "OP_SERVICE_ACCOUNT_TOKEN", "OP_CACHE", "HTTPS_PROXY"}
    assert set(env) - {"LC_CTYPE", "PWD", "SHLVL", "_"} <= allowed
    assert env["PATH"] == secret_refs.OP_FIXED_PATH
    assert env["OP_SERVICE_ACCOUNT_TOKEN"] == TOKEN
    assert env["OP_CACHE"] == "false"
    assert "CANARY_SECRET" not in env
    # The token reaches only op: never the gateway's own environment.
    assert TOKEN not in os.environ.values()
    assert "OP_SERVICE_ACCOUNT_TOKEN" not in env_store.child_process_env()


def test_a_proxy_not_exported_by_the_operator_is_not_passed(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_token()
    monkeypatch.setenv("HTTPS_PROXY", "http://planted.invalid:1")
    env_store.record_pmcp_introduced_keys(["HTTPS_PROXY"])
    _run(secret_refs.resolve_reference(REF, time.monotonic() + 10))
    assert "HTTPS_PROXY" not in host.op.calls()[0]["env"]


@pytest.mark.parametrize(
    ("stderr", "reason"),
    [
        ("[ERROR] 401 Unauthorized: invalid token", cs.AUTH_FAILED),
        ('[ERROR] "BrightData" isn\'t an item in the "PMCP" vault', cs.NOT_FOUND),
        ("[ERROR] 429 Too Many Requests: rate limit exceeded", cs.RATE_LIMITED),
        ("[ERROR] dial tcp: lookup my.1password.com: no such host", cs.NETWORK),
    ],
)
def test_failure_classes_never_echo_stderr(
    host: Host, stderr: str, reason: str
) -> None:
    host.write_token()
    host.op.table({REF: {"exit": 1, "stderr": stderr + " SECRET-ish-detail"}})
    with pytest.raises(CredentialUnavailableError) as raised:
        _run(secret_refs.resolve_reference(REF, time.monotonic() + 10))
    assert raised.value.reason == reason
    assert "SECRET-ish" not in str(raised.value) and REF not in str(raised.value)


@pytest.mark.parametrize(
    "entry",
    [
        {"stdout": ""},
        {"stdout": "short"},
        {"stdout": "two\nlines-value"},
        {"stdout": "op://v/i/f-again"},
        {"overflow": 64 * 1024 + 10},
    ],
)
def test_bad_output_is_output_invalid(host: Host, entry: dict[str, Any]) -> None:
    host.write_token()
    host.op.table({REF: entry})
    with pytest.raises(CredentialUnavailableError) as raised:
        _run(secret_refs.resolve_reference(REF, time.monotonic() + 10))
    assert raised.value.reason == cs.OUTPUT_INVALID


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().split()[2] != "Z"
    except OSError:
        return True


def test_a_hang_times_out_and_the_process_is_killed(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_token()
    host.op.table({REF: {"hang": True}})
    monkeypatch.setattr(secret_refs, "OP_TIMEOUT_SECONDS", 1.5)
    with pytest.raises(CredentialUnavailableError) as raised:
        _run(secret_refs.resolve_reference(REF, time.monotonic() + 10))
    assert raised.value.reason == cs.TIMEOUT
    hung = list(host.op.directory.glob("hung.*"))
    assert hung
    pid = int(hung[0].name.split(".")[1])
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def test_at_most_two_op_processes_at_once(host: Host) -> None:
    host.write_token()
    host.op.table({"*": {"stdout": VALUE, "sleep": 0.4}})
    refs = [f"op://v/item{i}/f" for i in range(5)]

    async def main() -> list[str]:
        deadline = time.monotonic() + 15
        return await asyncio.gather(
            *(secret_refs.resolve_reference(r, deadline) for r in refs)
        )

    assert _run(main()) == [VALUE] * 5
    calls = host.op.calls()
    assert len(calls) == 5
    events = sorted([(c["start"], 1) for c in calls] + [(c["end"], -1) for c in calls])
    running = peak = 0
    for _when, delta in events:
        running += delta
        peak = max(peak, running)
    assert peak <= 2


def test_identical_references_are_single_flighted(host: Host) -> None:
    host.write_token()
    host.op.table({"*": {"stdout": VALUE, "sleep": 0.3}})

    async def main() -> list[str]:
        deadline = time.monotonic() + 10
        return await asyncio.gather(
            *(secret_refs.resolve_reference(REF, deadline) for _ in range(4))
        )

    assert _run(main()) == [VALUE] * 4
    assert len(host.op.calls()) == 1


def test_a_cancelled_waiter_never_cancels_another_callers_lookup(host: Host) -> None:
    host.write_token()
    host.op.table({"*": {"stdout": VALUE, "sleep": 0.5}})

    async def main() -> str:
        deadline = time.monotonic() + 10
        first = asyncio.ensure_future(secret_refs.resolve_reference(REF, deadline))
        second = asyncio.ensure_future(secret_refs.resolve_reference(REF, deadline))
        await asyncio.sleep(0.1)
        first.cancel()
        return await second

    assert _run(main()) == VALUE
    assert len(host.op.calls()) == 1


def test_no_cache_across_spawns(host: Host) -> None:
    host.write_token()
    _run(secret_refs.resolve_reference(REF, time.monotonic() + 10))
    host.op.table({"*": {"stdout": "rotated-value-0002"}})
    assert (
        _run(secret_refs.resolve_reference(REF, time.monotonic() + 10))
        == "rotated-value-0002"
    )
    assert len(host.op.calls()) == 2


def test_resolved_values_are_registered_for_redaction(host: Host) -> None:
    host.write_token()
    _run(secret_refs.resolve_reference(REF, time.monotonic() + 10))
    assert secret_registry.redact_registered(f"a{VALUE}b") == "a[REDACTED]b"


# --------------------------------------------------------------------------- #
# Provenance and scope (plan 3.2)
# --------------------------------------------------------------------------- #


def test_a_user_store_reference_resolves_for_the_credential_lookup(host: Host) -> None:
    host.write_token()
    host.write_store(f"{NAMESPACED}={REF}\n")
    _start(host)
    assert NAMESPACED not in os.environ  # never published (plan 3.2.5)
    assert env_store.skipped_references() == {NAMESPACED: "user_store"}
    config = manifest_server_to_config(_brightdata(), host.root)
    assert config.config.env[LEGACY] == REF  # the config keeps the reference
    assert config.credential_sources == {LEGACY: "user_store"}
    overlay = _run(secret_refs.resolve_spawn_env(config))
    assert overlay == {LEGACY: VALUE}
    assert config.config.env[LEGACY] == REF  # never written onto the config
    assert VALUE not in os.environ.values()


def test_an_environment_reference_is_refused_with_zero_op_calls(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_token()
    monkeypatch.setenv(NAMESPACED, REF)  # e.g. EnvironmentFile=pmcp.env
    _start(host)
    with pytest.raises(CredentialUnavailableError) as raised:
        manifest_server_to_config(_brightdata(), host.root)
    assert raised.value.reason == cs.REFERENCE_NOT_ALLOWED_HERE
    assert host.op.calls() == []


def test_a_home_dotenv_reference_is_refused_and_still_shadows_the_store(
    host: Host,
) -> None:
    host.write_token()
    (host.home / ".env").write_text(f"{NAMESPACED}={REF}\n")
    host.write_store(f"{NAMESPACED}=plain-store-value\n")
    _start(host)
    assert NAMESPACED not in os.environ
    with pytest.raises(CredentialUnavailableError) as raised:
        manifest_server_to_config(_brightdata(), host.root)
    assert raised.value.reason == cs.REFERENCE_NOT_ALLOWED_HERE
    assert host.op.calls() == []


def test_a_project_store_reference_is_refused(host: Host) -> None:
    host.write_token()
    (host.root / ".env.pmcp").write_text(f"{NAMESPACED}={REF}\n")
    _start(host)
    with pytest.raises(CredentialUnavailableError) as raised:
        manifest_server_to_config(_brightdata(), host.root)
    assert raised.value.reason == cs.REFERENCE_NOT_ALLOWED_HERE
    assert host.op.calls() == []


def test_a_store_value_that_interpolates_to_a_reference_is_refused(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_token()
    monkeypatch.setenv("SOME_REF", REF)
    host.write_store(f"{NAMESPACED}=${{SOME_REF}}\n")
    _start(host)
    with pytest.raises(CredentialUnavailableError) as raised:
        manifest_server_to_config(_brightdata(), host.root)
    assert raised.value.reason == cs.REFERENCE_NOT_ALLOWED_HERE


@pytest.mark.parametrize("bad", ["op://v/i", " op://v/i/f", "op://v/i/f?attribute=otp"])
def test_an_unsupported_store_reference_is_refused_statically(
    host: Host, bad: str
) -> None:
    host.write_token()
    host.write_store(f"{NAMESPACED}='{bad}'\n")  # quoted: dotenv keeps spaces
    _start(host)
    with pytest.raises(CredentialUnavailableError) as raised:
        manifest_server_to_config(_brightdata(), host.root)
    assert raised.value.reason == cs.REFERENCE_UNSUPPORTED
    assert host.op.calls() == []


def test_without_a_token_a_store_reference_is_op_not_configured(host: Host) -> None:
    host.write_store(f"{NAMESPACED}={REF}\n")
    _start(host)
    with pytest.raises(CredentialUnavailableError) as raised:
        manifest_server_to_config(_brightdata(), host.root)
    assert raised.value.reason == cs.OP_NOT_CONFIGURED


def test_own_env_without_opt_in_passes_verbatim(host: Host) -> None:
    host.write_token()
    user = host.home / ".mcp.json"
    user.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "agentboard": {
                        "command": "true",
                        "env": {
                            "DB_URL": REF,
                            "OP_SERVICE_ACCOUNT_TOKEN": "ops_child_own_token",
                        },
                    }
                }
            }
        )
    )
    _start(host)
    (config,) = [
        c
        for c in load_configs(project_root=host.root, user_config_paths=[user])
        if c.name == "agentboard"
    ]
    assert config.credential_unavailable is None
    assert secret_refs.spawn_plan(config).empty
    assert config.config.env == {
        "DB_URL": REF,
        "OP_SERVICE_ACCOUNT_TOKEN": "ops_child_own_token",
    }
    env = env_store.sanitized_subprocess_env(config.config.env, host.root)
    assert env["DB_URL"] == REF
    assert env["OP_SERVICE_ACCOUNT_TOKEN"] == "ops_child_own_token"


def test_an_opted_in_user_config_resolves_its_own_env(host: Host) -> None:
    host.write_token()
    user = host.home / ".mcp.json"
    user.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "svc": {
                        "command": "true",
                        "resolveSecretRefs": True,
                        "env": {"DB_URL": REF_ID, "PLAIN": "x"},
                    }
                }
            }
        )
    )
    _start(host)
    (config,) = [
        c
        for c in load_configs(project_root=host.root, user_config_paths=[user])
        if c.name == "svc"
    ]
    assert config.resolve_secret_refs
    assert config.credential_sources == {
        "DB_URL": "user_config",
        "PLAIN": "user_config",
    }
    assert _run(secret_refs.resolve_spawn_env(config)) == {"DB_URL": VALUE}


def test_an_opted_in_server_may_not_name_an_op_variable(host: Host) -> None:
    host.write_token()
    user = host.home / ".mcp.json"
    user.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "svc": {
                        "command": "true",
                        "resolve_secret_refs": True,
                        "env": {"DB_URL": REF, "OP_SERVICE_ACCOUNT_TOKEN": "ops_x"},
                    }
                }
            }
        )
    )
    _start(host)
    (config,) = [
        c
        for c in load_configs(project_root=host.root, user_config_paths=[user])
        if c.name == "svc"
    ]
    assert config.credential_unavailable == cs.OP_NAME_NOT_ALLOWED


def test_a_project_config_cannot_opt_in(host: Host, approve_project_file: Any) -> None:
    host.write_token()
    project = host.root / ".mcp.json"
    project.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "svc": {
                        "command": "true",
                        "resolveSecretRefs": True,
                        "env": {"DB_URL": REF},
                    }
                }
            }
        )
    )
    approve_project_file(project)
    _start(host)
    configs = [
        c
        for c in load_configs(project_root=host.root, user_config_paths=[])
        if c.name == "svc"
    ]
    assert configs, "the approved project config loads"
    (config,) = configs
    assert not config.resolve_secret_refs
    assert secret_refs.spawn_plan(config).empty  # passed verbatim, never resolved


def test_overlay_extra_env_provenance(host: Host, approve_project_file: Any) -> None:
    user_overlay = host.home / ".pmcp" / "manifest.yaml"
    user_overlay.parent.mkdir(parents=True)
    user_overlay.write_text(
        "server_env:\n  brightdata:\n    FROM_USER: op://v/user/f\n"
    )
    project_overlay = host.root / ".pmcp" / "manifest.yaml"
    project_overlay.parent.mkdir(parents=True)
    project_overlay.write_text(
        "server_env:\n  brightdata:\n    FROM_PROJECT: op://v/project/f\n"
    )
    approve_project_file(project_overlay)
    server = load_manifest(project_root=host.root).get_server("brightdata")
    assert server is not None
    assert server.entry_origin == "shipped_manifest"
    assert server.extra_env_sources["FROM_USER"] == "operator_overlay"
    assert server.extra_env_sources["FROM_PROJECT"] == "project_overlay"


def test_a_project_overlay_extra_env_reference_is_refused_for_an_opted_in_server(
    host: Host, approve_project_file: Any
) -> None:
    host.write_token()
    project_overlay = host.root / ".pmcp" / "manifest.yaml"
    project_overlay.parent.mkdir(parents=True)
    project_overlay.write_text(
        "server_env:\n  brightdata:\n    FROM_PROJECT: op://v/project/f\n"
    )
    approve_project_file(project_overlay)
    user = host.home / ".mcp.json"
    user.write_text(
        json.dumps(
            {"mcpServers": {"brightdata": {"type": "local", "resolveSecretRefs": True}}}
        )
    )
    _start(host)
    (config,) = [
        c
        for c in load_configs(project_root=host.root, user_config_paths=[user])
        if c.name == "brightdata"
    ]
    assert config.credential_sources["FROM_PROJECT"] == "project_overlay"
    assert config.credential_unavailable == cs.REFERENCE_NOT_ALLOWED_HERE


def test_a_key_with_no_source_entry_is_never_resolved() -> None:
    config = _local({"K": REF})
    assert secret_refs.spawn_plan(config).empty


@pytest.mark.parametrize(
    "kind",
    sorted(secret_refs.SOURCE_KINDS - secret_refs.TRUSTED_SOURCES) + ["bogus"],
)
def test_every_untrusted_source_kind_is_refused(kind: str) -> None:
    with pytest.raises(CredentialUnavailableError) as raised:
        secret_refs.spawn_plan(_local({"K": REF}, {"K": kind}))
    assert raised.value.reason == cs.REFERENCE_NOT_ALLOWED_HERE


def test_resolved_server_config_constructors_inventory() -> None:
    """Plan 3.2.6: every ``ResolvedServerConfig(...)`` in ``src/pmcp``, by its
    innermost function. Those that put a credential lookup's answer into a
    local ``env`` pass ``credential_sources``; the rest carry no looked-up
    credential (a remote config, the gateway's own endpoint, a disconnect
    placeholder). A key with no entry is never resolved -- it is untrusted.
    A new constructor fails this test until it is classified here."""
    import ast

    src = Path(__file__).resolve().parent.parent / "src" / "pmcp"
    sites: set[tuple[str, str, bool]] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self, path: str) -> None:
            self.path = path
            self.stack: list[str] = []

        def _fn(self, node: Any) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = _fn
        visit_AsyncFunctionDef = _fn

        def visit_Call(self, node: ast.Call) -> None:
            if getattr(node.func, "id", None) == "ResolvedServerConfig":
                has = any(k.arg == "credential_sources" for k in node.keywords)
                sites.add((self.path, self.stack[-1] if self.stack else "", has))
            self.generic_visit(node)

    for path in sorted(src.rglob("*.py")):
        Visitor(path.relative_to(src).as_posix()).visit(ast.parse(path.read_text()))
    assert sites == {
        # Lookup-carrying local configs: provenance recorded.
        ("config/loader.py", "_manifest_server_to_config", True),
        ("config/loader.py", "build_resolved_config", True),
        ("manifest/installer.py", "_install_spawn_config", True),
        # Remote configs (references refused in headers, plan 3.2.7).
        ("config/loader.py", "_manifest_server_to_config", False),
        ("config/loader.py", "build_resolved_config", False),
        # The gateway's own endpoint and its probes: no downstream credential.
        ("cli.py", "run_update", False),
        ("cli.py", "_query_running_gateway_status", False),
        ("cli.py", "_build_gateway_auth_client", False),
        # Disconnect placeholders: nothing is spawned from them.
        ("tools/handlers.py", "_resolve_lifecycle_target", False),
    }


# --------------------------------------------------------------------------- #
# Spawn paths (plan 3.5)
# --------------------------------------------------------------------------- #


def _manager(host: Host) -> Any:
    from pmcp.client.manager import ClientManager

    return ClientManager(project_root=host.root)


def test_a_spawn_without_its_resolution_fails_closed(host: Host) -> None:
    manager = _manager(host)
    config = _local({"K": REF}, {"K": "user_store"})
    with pytest.raises(CredentialUnavailableError) as raised:
        _run(manager._connect_stdio(config))
    assert raised.value.reason == cs.RESOLUTION_REQUIRED


def test_the_child_gets_the_value_and_nothing_else_does(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_token()
    manager = _manager(host)
    config = _local({"K": REF, "P": "plain"}, {"K": "user_store"})
    seen: dict[str, Any] = {}

    async def fake_spawn(start: Any) -> Any:
        raise RuntimeError("stop after env")

    import pmcp.client.manager as manager_module

    real_env = manager_module.sanitized_subprocess_env

    def capture(own: Any, project: Any = None) -> dict[str, str]:
        seen["own"] = dict(own or {})
        return real_env(own, project)

    monkeypatch.setattr(manager_module, "sanitized_subprocess_env", capture)
    monkeypatch.setattr(manager_module, "spawn_owned", fake_spawn)
    errors = _run(manager.connect_server(config, retry=False))
    assert seen["own"] == {"K": VALUE, "P": "plain"}
    assert config.config.env == {"K": REF, "P": "plain"}
    assert VALUE not in os.environ.values()
    assert all(VALUE not in e for e in errors)


def test_resolution_runs_outside_the_lifecycle_lock(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_token()
    manager = _manager(host)
    config = _local({"K": REF}, {"K": "user_store"})
    held: list[bool] = []
    real = secret_refs.resolve_spawn_env

    async def watched(cfg: Any, **kw: Any) -> dict[str, str]:
        held.append(manager._lifecycle_lock.locked())
        return await real(cfg, **kw)

    import pmcp.client.manager as manager_module

    monkeypatch.setattr(manager_module._secret_refs, "resolve_spawn_env", watched)

    async def no_spawn(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("no spawn")

    monkeypatch.setattr(manager, "_connect_stdio", no_spawn)

    async def main() -> None:
        await manager.connect_server(config, retry=False)
        await manager.connect_all([config], retry=False)
        await manager.restart_server(config)

    _run(main())
    assert held and not any(held)


def test_a_restart_whose_lookup_fails_leaves_the_running_instance(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_token()
    host.op.table({REF: {"exit": 1, "stderr": "401 Unauthorized"}})
    manager = _manager(host)
    config = _local({"K": REF}, {"K": "user_store"})
    disconnects: list[str] = []

    async def disconnect(name: str, force: bool = False) -> Any:
        disconnects.append(name)
        return (True, 0, None)

    monkeypatch.setattr(manager, "disconnect_server", disconnect)
    ok, _cancelled, errors = _run(manager.restart_server(config))
    assert not ok
    assert disconnects == []  # teardown never started
    assert "credential_unavailable (auth_failed)" in errors[0]


def test_a_config_change_during_resolution_re_resolves_then_is_typed(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_token()
    manager = _manager(host)
    config = _local({"K": REF}, {"K": "user_store"})
    calls = {"n": 0}
    real = secret_refs.resolve_spawn_env

    async def racing(cfg: Any, **kw: Any) -> dict[str, str]:
        calls["n"] += 1
        result = await real(cfg, **kw)
        manager.bump_config_generation(cfg.name)  # replaced meanwhile
        return result

    import pmcp.client.manager as manager_module

    monkeypatch.setattr(manager_module._secret_refs, "resolve_spawn_env", racing)
    spawned: list[str] = []

    async def no_spawn(cfg: Any, secrets: Any = None) -> None:
        spawned.append(cfg.name)

    monkeypatch.setattr(manager, "_connect_stdio", no_spawn)
    errors = _run(manager.connect_server(config, retry=False))
    assert calls["n"] == 2  # re-resolved once
    assert spawned == []  # nothing released
    assert any(cs.CONFIG_CHANGED in e for e in errors)


def test_a_typed_failure_is_not_retried(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(host)
    attempts: list[int] = []

    async def failing(cfg: Any, secrets: Any = None) -> None:
        attempts.append(1)
        raise CredentialUnavailableError(cs.RATE_LIMITED)

    monkeypatch.setattr(manager, "_connect_server", failing)
    with pytest.raises(CredentialUnavailableError):
        _run(manager._connect_with_retry(_local({})))
    assert attempts == [1]


def test_reconnect_stops_on_a_typed_lookup_failure(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pmcp.client.manager as manager_module

    manager = _manager(host)
    prepared: list[int] = []

    async def failing_prepare(cfg: Any) -> Any:
        prepared.append(1)
        raise CredentialUnavailableError(cs.RATE_LIMITED)

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(manager, "prepare_spawn", failing_prepare)
    monkeypatch.setattr(manager_module.asyncio, "sleep", no_sleep)
    _run(manager._reconnect_loop("srv", _local({"K": REF}, {"K": "user_store"})))
    assert prepared == [1]


def test_install_child_env_resolves_or_fails_closed(host: Host) -> None:
    host.write_token()
    host.write_store(f"{NAMESPACED}={REF}\n")
    _start(host)
    with pytest.raises(CredentialUnavailableError) as raised:
        build_install_child_env(_brightdata(), host.root)
    assert raised.value.reason == cs.RESOLUTION_REQUIRED
    secrets_ = _run(resolve_install_secrets(_brightdata(), host.root))
    env = build_install_child_env(_brightdata(), host.root, secrets=secrets_)
    assert env[LEGACY] == VALUE
    assert NAMESPACED not in env


def test_install_child_env_without_references_is_unchanged(host: Host) -> None:
    host.write_store(f"{NAMESPACED}=plain-key\n")
    _start(host)
    assert build_install_child_env(_brightdata(), host.root)[LEGACY] == "plain-key"
    assert _run(resolve_install_secrets(_brightdata(), host.root)) == {}
    assert host.op.calls() == []


def test_inherited_op_names_are_stripped_but_own_env_is_kept(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("OP_SERVICE_ACCOUNT_TOKEN", "OP_CONNECT_TOKEN", "OP_SESSION_abc"):
        monkeypatch.setenv(name, "ops_inherited_should_not_pass")
    env = env_store.child_process_env()
    assert not any(n.startswith("OP_") for n in env)
    env = env_store.sanitized_subprocess_env({"OP_SERVICE_ACCOUNT_TOKEN": "own"})
    assert env["OP_SERVICE_ACCOUNT_TOKEN"] == "own"
    assert "OP_CONNECT_TOKEN" not in env


def test_startup_removes_op_names_and_never_loads_them_from_files(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", TOKEN)
    monkeypatch.setenv("OP_SESSION_my", "x")
    host.write_store("OP_CONNECT_HOST=https://connect.invalid\nOP_ACCOUNT=acct\n")
    _start(host)
    assert not any(n.startswith("OP_") for n in os.environ)


# --------------------------------------------------------------------------- #
# Remote headers (plan 3.2.7)
# --------------------------------------------------------------------------- #


def test_a_reference_in_a_remote_header_is_refused(host: Host) -> None:
    from pmcp.remote_auth import resolve_remote_headers

    resolution = resolve_remote_headers(
        {"Authorization": "Bearer ${MY_KEY}", "X-Lit": "Bearer op://v/i/f"},
        lambda key: REF if key == "MY_KEY" else None,
    )
    assert resolution.unavailable_env_vars["MY_KEY"] == cs.REMOTE_REFERENCE_UNSUPPORTED
    assert "<header:X-Lit>" in resolution.unavailable_env_vars
    assert "X-Lit" not in resolution.resolved_headers
    assert all("op://" not in v.lower() for v in resolution.resolved_headers.values())


# --------------------------------------------------------------------------- #
# Redaction (plan 3.7)
# --------------------------------------------------------------------------- #

SECRET = 'sec"ret\\value-ü-12345'


def test_process_output_redacts_before_truncation_and_in_the_summary() -> None:
    from pmcp.policy.policy import PolicyManager

    secret_registry.register_secret_value(SECRET)
    text = "a" * 90 + SECRET + "b" * 400
    out = PolicyManager().process_output(text, redact=False, max_bytes=100)
    assert out["truncated"]
    rendered = json.dumps(out)
    for fragment in (SECRET, SECRET[:6], "ret\\\\value"):
        assert fragment not in rendered
    payload = {"nested": [SECRET, {"k": f"x{SECRET}y"}]}
    out = PolicyManager().process_output(payload, redact=False)
    assert SECRET not in json.dumps(out["result"])
    assert "[REDACTED]" in json.dumps(out["result"])


def test_escaped_forms_are_matched() -> None:
    secret_registry.register_secret_value(SECRET)
    for form in (json.dumps(SECRET), json.dumps(SECRET, ensure_ascii=False)):
        assert form[1:-1] not in secret_registry.redact_registered(f"<{form}>")


def test_short_values_are_never_registered() -> None:
    secret_registry.register_secret_value("short")
    assert secret_registry.redact_registered("short") == "short"


def test_diagnostics_and_exception_text_redact() -> None:
    from pmcp.argument_errors import exception_text
    from pmcp.auth import sanitize_auth_diagnostic

    secret_registry.register_secret_value(VALUE)
    assert VALUE not in sanitize_auth_diagnostic(f"failed with {VALUE}")
    assert VALUE not in exception_text(RuntimeError(f"boom {VALUE}"))


def test_formatted_log_records_are_redacted(caplog: pytest.LogCaptureFixture) -> None:
    secret_registry.install_log_redaction()
    secret_registry.register_secret_value(VALUE)
    stream_out: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            stream_out.append(self.format(record))

    handler = Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("pmcp.test.secret_refs.child")
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        log.warning("value %s here", VALUE)
        log.warning(f"f-string {VALUE}")
        try:
            raise RuntimeError(f"in an exception {VALUE}")
        except RuntimeError:
            log.exception("failed")
    finally:
        log.removeHandler(handler)
    joined = "\n".join(stream_out)
    assert VALUE not in joined
    assert joined.count("[REDACTED]") >= 3
    assert any(
        isinstance(f, secret_registry.SecretValueFilter)
        for f in logging.getLogger().filters
    )


def test_downstream_catalog_text_is_redacted() -> None:
    from pmcp.client.manager import _parse_tool_entries

    secret_registry.register_secret_value(VALUE)
    entries = _parse_tool_entries(
        "srv",
        [{"name": "t", "description": f"uses {VALUE}", "inputSchema": {}}],
        100,
    )
    assert VALUE not in json.dumps([e[1].model_dump() for e in entries])


# --------------------------------------------------------------------------- #
# Write paths (plan 3.2.5)
# --------------------------------------------------------------------------- #


def _tools(host: Host) -> Any:
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools

    client_manager = MagicMock()
    client_manager.get_all_tools.return_value = []
    client_manager.is_server_online.return_value = False
    client_manager.get_all_server_statuses.return_value = []
    client_manager.get_server_status.return_value = None
    return GatewayTools(
        client_manager=client_manager,
        policy_manager=PolicyManager(),
        project_root=host.root,
    )


def test_auth_connect_refuses_a_reference(host: Host) -> None:
    _start(host)
    out = _run(
        _tools(host).auth_connect(
            {"server_name": "brightdata", "credential": REF, "scope": "user"}
        )
    )
    assert not out.ok and "op://" in out.message
    assert not host.store.exists() or REF not in host.store.read_text()


def test_auth_connect_never_overwrites_a_stored_reference(host: Host) -> None:
    host.write_store(f"{NAMESPACED}={REF}\n")
    _start(host)
    out = _run(
        _tools(host).auth_connect(
            {
                "server_name": "brightdata",
                "credential": "plain-new-value",
                "scope": "user",
            }
        )
    )
    assert not out.ok
    assert REF in host.store.read_text()


def test_auth_connect_refuses_op_names(host: Host) -> None:
    _start(host)
    reason = _tools(host)._auth_connect_reference_refusal(
        "user", "OP_SERVICE_ACCOUNT_TOKEN", TOKEN
    )
    assert reason is not None and TOKEN not in reason


def _set_args(host: Host, key: str, value: str, scope: str = "user") -> Any:
    return argparse.Namespace(key=key, value=value, scope=scope, project=host.root)


def _health(**report: Any) -> dict[str, Any]:
    capable = report.pop("capable", True)
    return {
        "gateway_diagnostics": {
            "capabilities": ["secret_refs"] if capable else [],
            "secret_refs": {
                "capable": capable,
                "token_state": "usable",
                "shadowed_names": [],
                "store_names_from_environment_names": [],
                **report,
            },
        }
    }


def _patch_health(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    async def fake(timeout_s: float = 5.0) -> Any:
        return payload

    monkeypatch.setattr(secrets, "fetch_gateway_health", fake)


@pytest.mark.parametrize(
    ("payload", "scope", "key", "value"),
    [
        (None, "user", NAMESPACED, REF),  # no gateway
        (_health(capable=False), "user", NAMESPACED, REF),
        (_health(), "project", NAMESPACED, REF),
        (_health(), "user", NAMESPACED, "op://v/i"),
        (_health(shadowed_names=[NAMESPACED]), "user", NAMESPACED, REF),
        (
            _health(store_names_from_environment_names=[NAMESPACED]),
            "user",
            NAMESPACED,
            REF,
        ),
        (
            {
                "gateway_diagnostics": {
                    "capabilities": ["secret_refs"],
                    "secret_refs": {"capable": True},
                }
            },
            "user",
            NAMESPACED,
            REF,
        ),  # names hidden (HTTP auth on)
        (_health(), "user", "UV_INDEX_URL", REF),  # not a credential name
        (_health(), "user", "PMCP_AUTH_TOKEN", REF),
        (_health(), "user", "OP_SERVICE_ACCOUNT_TOKEN", TOKEN),
    ],
)
def test_secrets_set_refuses_a_reference_until_the_gateway_reports_it(
    host: Host,
    monkeypatch: pytest.MonkeyPatch,
    payload: Any,
    scope: str,
    key: str,
    value: str,
) -> None:
    _start(host)
    _patch_health(monkeypatch, payload)
    out = _run(secrets.run_secrets_set(_set_args(host, key, value, scope)))
    assert out["ok"] is False
    assert not host.store.exists() or value not in host.store.read_text()


def test_secrets_set_writes_a_reference_when_the_gateway_reports_it(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    _start(host)
    _patch_health(monkeypatch, _health())
    out = _run(secrets.run_secrets_set(_set_args(host, NAMESPACED, REF)))
    assert out["ok"] is True
    assert f"{NAMESPACED}={REF}" in host.store.read_text().replace('"', "").replace(
        "'", ""
    )


def test_secrets_set_of_a_plain_value_needs_no_gateway(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    _start(host)
    _patch_health(monkeypatch, None)
    out = _run(secrets.run_secrets_set(_set_args(host, NAMESPACED, "plain-value")))
    assert out["ok"] is True


def test_secrets_sync_refuses_references(host: Host) -> None:
    (host.root / ".env.pmcp").write_text(f"{NAMESPACED}={REF}\nOTHER_API_KEY=plain\n")
    _start(host)
    out = _run(
        secrets.run_secrets_sync(
            argparse.Namespace(
                from_scope="project",
                to_scope="user",
                project=host.root,
                overwrite=False,
            )
        )
    )
    assert NAMESPACED in out["refused"]
    assert REF not in host.store.read_text()


# --------------------------------------------------------------------------- #
# The secret_refs capability (plan 3.10)
# --------------------------------------------------------------------------- #


def test_capability_requires_a_usable_token(host: Host) -> None:
    _start(host)
    assert not secret_refs.secret_refs_health(include_names=True)["capable"]
    host.write_token()
    report = secret_refs.secret_refs_health(include_names=True)
    assert report["capable"] and report["token_source"] == "credstore"
    assert TOKEN not in json.dumps(report)


def test_capability_is_withheld_when_the_store_is_loaded_into_the_environment(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    host.write_token()
    host.write_store(f"{NAMESPACED}=plain\n")
    monkeypatch.setenv(NAMESPACED, "plain")  # EnvironmentFile=pmcp.env
    _start(host)
    report = secret_refs.secret_refs_health(include_names=True)
    assert not report["capable"]
    assert report["store_names_from_environment_names"] == [NAMESPACED]
    assert "store_names_from_environment_names" not in secret_refs.secret_refs_health(
        include_names=False
    )


def test_health_endpoint_reports_the_capability_per_request(host: Host) -> None:
    from starlette.testclient import TestClient

    from pmcp.transport.http import create_http_app

    _start(host)
    server = MagicMock()
    app = create_http_app(server)
    with TestClient(app) as client:
        body = client.get("/health").json()["gateway_diagnostics"]
        assert "secret_refs" not in body["capabilities"]
        host.write_token()
        body = client.get("/health").json()["gateway_diagnostics"]
        assert "secret_refs" in body["capabilities"]
        assert body["secret_refs"]["token_state"] == "usable"
        assert "shadowed_names" in body["secret_refs"]


# --------------------------------------------------------------------------- #
# pmcp's own variables (plan 3.2.5)
# --------------------------------------------------------------------------- #


def test_a_reference_auth_token_stops_the_http_gateway(
    host: Host, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PMCP_AUTH_TOKEN", REF)
    with pytest.raises(SystemExit) as exited:
        cli._refuse_reference_auth_token(
            argparse.Namespace(transport="http", auth_token=REF)
        )
    assert exited.value.code == 1


def test_a_store_reference_auth_token_stops_the_http_gateway(host: Host) -> None:
    host.write_store(f"PMCP_AUTH_TOKEN={REF}\n")
    _start(host)
    assert "PMCP_AUTH_TOKEN" not in os.environ
    with pytest.raises(SystemExit):
        cli._refuse_reference_auth_token(
            argparse.Namespace(transport="http", auth_token=None)
        )


def test_other_pmcp_variables_are_never_references(
    host: Host, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PMCP_GATEWAY_URL", "op://v/i/f")
    _start(host)
    assert "PMCP_GATEWAY_URL" not in os.environ
    assert "op://v/i/f" not in capsys.readouterr().err
