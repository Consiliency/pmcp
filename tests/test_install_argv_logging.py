"""EC-PKGID-4: every install spawn logs its rendered argv at WARNING, before it spawns.

The rendered argv is secret-safe by construction: the executable (escaped so it
cannot forge the line), the pinned ``name@resolved_version`` in the package slot,
the frozen flag literals, and ``<redacted>`` for everything else. The package slot
is found by POSITION, never by what an argument looks like -- a split credential
such as ``--token abcdef0123456789`` has a value that is a valid package name.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from pmcp.manifest.installer import (
    InstallError,
    JobManager,
    install_server,
    verify_installation,
)
from pmcp.manifest.loader import ServerConfig
from pmcp.validation import is_valid_package_name

INSTALLER_LOGGER = "pmcp.manifest.installer"
PINNED = "@scope/pkg@1.2.3"
SPLIT_SECRET = "abcdef0123456789"
COMBINED_SECRET = "sk-live-0123456789abcdef"
REGISTRY_SECRET = "hunter2registrypw"
# CR, LF, ESC (with an erase-line sequence), a C1 CSI, a bidi override and a
# zero-width space: each can overwrite, split or reorder a log line as it prints.
# The space and ``$(id)`` are for the operator who pastes the line into a shell.
HOSTILE_EXECUTABLE = "np\r\n\x1b[2K\x9b\u202e\u200bx $(id)"
# What the operator must see instead: every non-printable character as its
# backslash escape, and the whole value single-quoted so ``$(id)`` stays literal.
HOSTILE_EXECUTABLE_RENDERED = "'np\\r\\n\\x1b[2K\\x9b\\u202e\\u200bx $(id)'"


class _FakeProc:
    returncode = 0
    pid = 4242
    stdin = None
    stdout = None
    stderr = None

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


def _config(
    install_argv: list[str],
    *,
    command: str = "npx",
    args: list[str] | None = None,
    name: str = "argv-logging-test",
) -> ServerConfig:
    return ServerConfig(
        name=name,
        description="argv logging test",
        keywords=["test"],
        install={"linux": install_argv},
        command=command,
        args=args if args is not None else ["-y", PINNED],
        requires_api_key=False,
    )


def _record_spawns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    *,
    raises: BaseException | None = None,
) -> list[dict[str, object]]:
    """Replace the spawn with a recorder that snapshots the WARNINGs logged so far.

    The snapshot is taken at the moment of the spawn call, so a line logged after
    the spawn returns is not in it.
    """
    calls: list[dict[str, object]] = []

    async def _fake_exec(*argv: str, **kwargs: object) -> _FakeProc:
        calls.append(
            {
                "argv": list(argv),
                "warnings_before": [
                    r.getMessage()
                    for r in caplog.records
                    if r.name == INSTALLER_LOGGER and r.levelno == logging.WARNING
                ],
            }
        )
        if raises is not None:
            raise raises
        return _FakeProc()

    monkeypatch.setattr(
        "pmcp.manifest.installer.asyncio.create_subprocess_exec", _fake_exec
    )
    monkeypatch.setattr(JobManager, "_monitor_install", AsyncMock(return_value=None))
    return calls


def _installer_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == INSTALLER_LOGGER]


def _warnings_with(caplog: pytest.LogCaptureFixture, rendered: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == INSTALLER_LOGGER
        and r.levelno == logging.WARNING
        and r.getMessage().endswith(": " + rendered)
    ]


def _assert_logged_before_spawn(calls: list[dict[str, object]], rendered: str) -> None:
    assert len(calls) == 1, calls
    before = calls[0]["warnings_before"]
    assert isinstance(before, list)
    assert [m for m in before if m.endswith(": " + rendered)], (
        f"no WARNING ending in {rendered!r} was logged before the spawn; saw {before!r}"
    )


def _assert_line_cannot_be_forged(caplog: pytest.LogCaptureFixture) -> None:
    messages = _installer_messages(caplog)
    assert messages
    for message in messages:
        forging = [ch for ch in message if not ch.isprintable()]
        assert not forging, f"non-printable {forging!r} reached the log: {message!r}"
    # Escaped and quoted, not dropped: the operator still sees what was spawned.
    assert HOSTILE_EXECUTABLE_RENDERED in "\n".join(messages)


async def test_start_install_logs_rendered_argv_at_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=INSTALLER_LOGGER)
    calls = _record_spawns(monkeypatch, caplog)

    await JobManager.get_instance().start_install(
        _config(["npx", "-y", PINNED]), "linux"
    )

    _assert_logged_before_spawn(calls, f"npx -y {PINNED}")
    assert calls[0]["argv"] == ["npx", "-y", PINNED]
    assert _warnings_with(caplog, f"npx -y {PINNED}")
    assert "<args redacted>" not in caplog.text

    caplog.clear()
    calls = _record_spawns(monkeypatch, caplog)
    await JobManager.get_instance().start_install(
        _config([HOSTILE_EXECUTABLE, "-y", PINNED], name="evil\rname\x1b[2K"),
        "linux",
    )
    assert len(calls) == 1
    _assert_line_cannot_be_forged(caplog)
    assert [m for m in _installer_messages(caplog) if m.endswith(f" -y {PINNED}")]


async def test_legacy_install_server_logs_rendered_argv_at_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=INSTALLER_LOGGER)
    calls = _record_spawns(monkeypatch, caplog)

    await install_server(
        _config(["npx", "-y", PINNED, "--token", SPLIT_SECRET]), "linux"
    )

    rendered = f"npx -y {PINNED} <redacted> <redacted>"
    _assert_logged_before_spawn(calls, rendered)
    assert _warnings_with(caplog, rendered)
    # The legacy path used to log ' '.join(install_cmd) at INFO: no level may leak.
    assert SPLIT_SECRET not in caplog.text

    caplog.clear()
    calls = _record_spawns(monkeypatch, caplog)
    await install_server(_config([HOSTILE_EXECUTABLE, "-y", PINNED]), "linux")
    assert len(calls) == 1
    _assert_line_cannot_be_forged(caplog)


async def test_verify_installation_logs_rendered_argv_at_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=INSTALLER_LOGGER)
    calls = _record_spawns(monkeypatch, caplog)

    # verify_installation spawns [command, args[0], "--help"]: the log must show
    # that argv, not the install command.
    config = _config(["npx", "-y", PINNED], command="npx", args=[PINNED, SPLIT_SECRET])
    assert await verify_installation(config) is True

    rendered = f"npx {PINNED} <redacted>"
    assert calls[0]["argv"] == ["npx", PINNED, "--help"]
    _assert_logged_before_spawn(calls, rendered)
    assert _warnings_with(caplog, rendered)

    caplog.clear()
    calls = _record_spawns(monkeypatch, caplog)
    await verify_installation(
        _config(["npx"], command=HOSTILE_EXECUTABLE, args=[PINNED])
    )
    assert len(calls) == 1
    _assert_line_cannot_be_forged(caplog)


async def test_argv_is_logged_even_when_the_spawn_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=INSTALLER_LOGGER)
    rendered = f"no-such-npx -y {PINNED}"
    config = _config(["no-such-npx", "-y", PINNED], command="no-such-npx", args=["-y"])

    calls = _record_spawns(monkeypatch, caplog, raises=FileNotFoundError("no-such-npx"))
    manager = JobManager.get_instance()
    job_id = await manager.start_install(config, "linux")
    job = manager.get_job(job_id)
    assert job is not None and job.status == "failed"
    assert len(calls) == 1
    assert _warnings_with(caplog, rendered), "start_install"

    caplog.clear()
    calls = _record_spawns(monkeypatch, caplog, raises=FileNotFoundError("no-such-npx"))
    with pytest.raises(InstallError):
        await install_server(config, "linux")
    assert len(calls) == 1
    assert _warnings_with(caplog, rendered), "install_server"

    caplog.clear()
    calls = _record_spawns(monkeypatch, caplog, raises=FileNotFoundError("no-such-npx"))
    assert await verify_installation(config) is False
    assert len(calls) == 1
    assert _warnings_with(caplog, "no-such-npx -y <redacted>"), "verify_installation"


async def test_a_credential_bearing_argument_is_redacted_but_the_package_identity_is_not(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=INSTALLER_LOGGER)
    # The trap is real: the split credential's value IS a valid package name, so
    # any "looks like a package" rule would log it.
    assert is_valid_package_name(SPLIT_SECRET)

    argv = [
        "npx",
        "-y",
        PINNED,
        "--token",
        SPLIT_SECRET,
        f"--api-key={COMBINED_SECRET}",
        # Pinned-spec-shaped but not in the package slot: shown only by a
        # syntactic rule, never by a positional one.
        "other-pkg@9.9.9",
        "--registry",
        f"https://user:{REGISTRY_SECRET}@registry.example",
        f"--registry=https://user:{REGISTRY_SECRET}@mirror.example",
        "--quiet",
        "--yes",
    ]
    rendered = (
        f"npx -y {PINNED} <redacted> <redacted> <redacted> <redacted>"
        " --registry <redacted> --registry=<redacted> --quiet --yes"
    )
    secrets = (SPLIT_SECRET, COMBINED_SECRET, REGISTRY_SECRET, "other-pkg")

    for spawn in ("start_install", "install_server"):
        caplog.clear()
        calls = _record_spawns(monkeypatch, caplog)
        if spawn == "start_install":
            await JobManager.get_instance().start_install(_config(argv), "linux")
        else:
            await install_server(_config(argv), "linux")
        assert calls[0]["argv"] == argv, spawn
        _assert_logged_before_spawn(calls, rendered)
        for secret in secrets:
            assert secret not in caplog.text, (spawn, secret)

    # Role is positional: whatever occupies the package slot decides it. A
    # credential ahead of the package takes the slot, and a bare name in the slot
    # is not a pinned identity -- neither is shown.
    for argv, rendered in (
        (
            ["npx", "--token", SPLIT_SECRET, PINNED],
            "npx <redacted> <redacted> <redacted>",
        ),
        (["npx", "-y", SPLIT_SECRET], "npx -y <redacted>"),
    ):
        caplog.clear()
        calls = _record_spawns(monkeypatch, caplog)
        await install_server(_config(argv), "linux")
        _assert_logged_before_spawn(calls, rendered)
        assert SPLIT_SECRET not in caplog.text
