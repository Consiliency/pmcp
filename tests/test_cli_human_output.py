"""No CLI command prints a character a terminal acts on (Consiliency/pmcp#388
round 3).

Round 3's findings (grok F001, codex F001) were print sites the earlier
rounds had not patched: `pmcp status`'s local fallback and `pmcp config
status` printing an approved `.mcp.json` server name, `pmcp logs` printing a
repository's `.pmcp/logs/gateway.log`, and `pmcp auth connect` printing a
peer's elicitation. The fix is one choke point: `cli.main` writes stderr,
and a subcommand's human stdout, through `pmcp.display.DisplaySafeStream`.
The gateway's own stdout (MCP frames on stdio) and `--json` output are not
touched.

This module checks the boundary for every subcommand the parser defines
(derived, so a new one must be listed), each through a fake command that
prints a hostile value, and then the real input sources the findings used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from pmcp import cli

#: ESC (with a CSI clear), BEL, the one-byte CSI U+009B, RLO and a tag
#: character: each a class a terminal acts on.
HOSTILE = "SENTINEL\x1b[2J\x07\x9b2J‮\U000e0041"
_RAW = ("\x1b", "\x07", "\x9b", "‮", "\U000e0041")


def _parser() -> argparse.ArgumentParser:
    """The real argument parser, captured from `cli.parse_args`."""
    captured: list[argparse.ArgumentParser] = []
    original = argparse.ArgumentParser.parse_args

    def grab(self: argparse.ArgumentParser, *a: Any, **k: Any) -> Any:
        captured.append(self)
        raise SystemExit(0)

    argparse.ArgumentParser.parse_args = grab  # type: ignore[method-assign]
    try:
        cli.parse_args()
    except SystemExit:
        pass
    finally:
        argparse.ArgumentParser.parse_args = original  # type: ignore[method-assign]
    return captured[0]


def _leaf_commands() -> Iterator[tuple[tuple[str, ...], bool]]:
    """(path, has --json) of every runnable subcommand."""

    def walk(
        parser: argparse.ArgumentParser, prefix: tuple[str, ...]
    ) -> Iterator[tuple[tuple[str, ...], bool]]:
        nested = [
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        ]
        if not nested and prefix:
            yield prefix, any("--json" in a.option_strings for a in parser._actions)
        for action in nested:
            for name, sub in action.choices.items():
                yield from walk(sub, (*prefix, name))

    yield from walk(_parser(), ())


#: Arguments each subcommand needs to parse, and the `pmcp.cli` function its
#: dispatch calls (async or not).
_COMMANDS: dict[tuple[str, ...], tuple[list[str], str]] = {
    ("refresh",): ([], "run_refresh"),
    ("update",): (["x"], "run_update"),
    ("status",): ([], "run_status"),
    ("logs",): ([], "run_logs"),
    ("init",): ([], "run_init"),
    ("capabilities",): ([], "run_capabilities"),
    ("setup",): ([], "run_setup"),
    ("config", "status"): ([], "run_config"),
    ("config", "startup-policy"): ([], "run_config"),
    ("config", "set-startup-policy"): (["add", "x"], "run_config"),
    ("guidance",): ([], "run_guidance"),
    ("doctor",): ([], "run_doctor"),
    ("upgrade",): ([], "run_upgrade"),
    ("secrets", "set"): (["K", "v"], "run_secrets_set"),
    ("secrets", "sync"): ([], "run_secrets_sync"),
    ("secrets", "check"): ([], "run_secrets_check"),
    ("auth", "connect"): (["x"], "run_auth_connect"),
    ("auth", "acknowledge"): (["x", "--elicitation-id", "e"], "run_auth_acknowledge"),
    ("trust", "approve"): (["p"], "run_trust"),
    ("trust", "list"): ([], "run_trust"),
    ("trust", "revoke"): (["p"], "run_trust"),
    ("trust", "approve-package"): (["a@1.0.0"], "run_trust"),
    ("trust", "list-packages"): ([], "run_trust"),
    ("trust", "revoke-package"): (["a@1.0.0"], "run_trust"),
}


def test_every_subcommand_is_listed() -> None:
    assert {path for path, _json in _leaf_commands()} == set(_COMMANDS)


def _run_main(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["pmcp", *argv])
    try:
        cli.main()
    except SystemExit:
        pass


def _fake(name: str, write: Callable[[], None]) -> Callable[..., Any]:
    if asyncio.iscoroutinefunction(getattr(cli, name)):

        async def run_async(*_args: Any, **_kwargs: Any) -> Any:
            write()
            return {"ok": True}

        return run_async

    def run(*_args: Any, **_kwargs: Any) -> Any:
        write()
        return {"ok": True}

    return run


def _hostile_writes() -> None:
    print(f"name {HOSTILE}")
    print(f"error {HOSTILE}", file=sys.stderr)
    sys.stdout.write(f"raw {HOSTILE}\n")


def _assert_escaped(text: str) -> None:
    assert "SENTINEL" in text, text
    assert not any(ch in text for ch in _RAW), ascii(text)


@pytest.mark.parametrize("path", sorted(_COMMANDS), ids="-".join)
def test_a_subcommand_prints_nothing_raw(
    path: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Human mode: whatever the command prints, to stdout or stderr, by
    `print` or `write`, arrives escaped."""
    extra, target = _COMMANDS[path]
    monkeypatch.setattr(cli, target, _fake(target, _hostile_writes))
    _run_main([*path, *extra], monkeypatch)
    out, err = capsys.readouterr()
    if path[0] in cli._JSON_ONLY_COMMANDS:
        # Its stdout is JSON by contract (`json.dumps`, ASCII-escaped): left
        # alone, like `--json`. Its stderr is escaped like every command's.
        assert "\x9b" in out
    else:
        _assert_escaped(out)
    _assert_escaped(err)
    assert sys.stdout is not None and not getattr(
        sys.stdout, "pmcp_display_safe", False
    )


@pytest.mark.parametrize(
    "path", sorted(p for p, has_json in _leaf_commands() if has_json), ids="-".join
)
def test_json_output_is_unchanged(
    path: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--json`: stdout is exactly what the command wrote."""
    extra, target = _COMMANDS[path]
    document = json.dumps({"name": HOSTILE}, ensure_ascii=False)
    monkeypatch.setattr(cli, target, _fake(target, lambda: print(document)))
    _run_main([*path, *extra, "--json"], monkeypatch)
    out, _err = capsys.readouterr()
    assert out == document + "\n"


def test_the_gateways_stdout_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    """No subcommand: the gateway. Its stdout carries MCP frames on the
    stdio transport, and every byte written is the byte sent -- including
    one a terminal would act on, which is the frame's own content."""
    frame = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {"text": HOSTILE}}, ensure_ascii=False
    )
    seen: list[Any] = []

    async def serve(_args: Any) -> None:
        seen.append(sys.stdout)
        sys.stdout.write(frame + "\n")
        sys.stdout.flush()
        sys.stdout.buffer.write(frame.encode() + b"\n")

    monkeypatch.setattr(cli, "run_server", serve)
    _run_main([], monkeypatch)
    out, _err = capsysbinary.readouterr()
    assert out == (frame + "\n").encode() * 2
    assert not getattr(seen[0], "pmcp_display_safe", False)


def test_human_output_is_decided_by_command_and_flags() -> None:
    def ns(**kwargs: Any) -> argparse.Namespace:
        return argparse.Namespace(**kwargs)

    assert cli.human_output(ns(command="status", json=False))
    assert not cli.human_output(ns(command="status", json=True))
    assert not cli.human_output(ns(command=None))
    assert not cli.human_output(ns(command="secrets"))
    assert cli.human_output(ns(command="logs"))


# --- the findings' real input sources -------------------------------------------


def _approved_config(
    tmp_path: Path, approve: Callable[[Path], None], name: str = HOSTILE
) -> Path:
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    config = project / ".mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {name: {"command": "echo", "args": ["x"]}}})
    )
    approve(config)
    return project


def test_grok_f001_status_local_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    approve_project_file: Callable[[Path], None],
) -> None:
    project = _approved_config(tmp_path, approve_project_file)

    async def no_gateway(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(cli, "_query_running_gateway_status", no_gateway)
    _run_main(["status", "--project", str(project)], monkeypatch)
    out, err = capsys.readouterr()
    _assert_escaped(out)
    assert not any(ch in err for ch in _RAW), ascii(err)


def test_grok_f001_config_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    approve_project_file: Callable[[Path], None],
) -> None:
    project = _approved_config(tmp_path, approve_project_file)
    _run_main(["--project", str(project), "config", "status"], monkeypatch)
    out, err = capsys.readouterr()
    _assert_escaped(out + err)


def test_grok_f001_auth_connect_prints_a_peers_elicitation_escaped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The gateway's answer carries a downstream peer's elicitation."""

    class _Manager:
        async def connect_all(self, _configs: Any) -> list[Any]:
            return []

        async def disconnect_all(self, *_a: Any, **_k: Any) -> None:
            return None

    answer = {
        "auth_state": "elicitation_required",
        "message": f"open {HOSTILE}",
        "url_elicitations": [
            {
                "url": f"https://auth.example.com/{HOSTILE}",
                "elicitation_id": HOSTILE,
                "next_step": f"call {HOSTILE}",
            }
        ],
    }

    async def call(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return answer

    monkeypatch.setattr(cli, "_build_gateway_auth_client", lambda _a: (_Manager(), {}))
    monkeypatch.setattr(cli, "_call_gateway_tool", call)
    _run_main(["auth", "connect", "srv"], monkeypatch)
    out, err = capsys.readouterr()
    _assert_escaped(out)
    assert "Elicitation ID" in out and "Next step" in out, out
    assert not any(ch in err for ch in _RAW), ascii(err)


@pytest.mark.parametrize("follow", [False, True], ids=["plain", "follow"])
def test_codex_f001_logs_prints_a_repository_log_escaped(
    follow: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`pmcp logs` reads `.pmcp/logs/gateway.log` from the working directory,
    which a checkout can ship."""
    log = Path(".pmcp/logs/gateway.log")
    log.parent.mkdir(parents=True)
    log.write_text(f"[INFO] first {HOSTILE}\n")
    argv = ["logs", "--tail", "5"]
    if follow:
        argv.append("--follow")
        sleeps = 0

        async def sleep(_seconds: float) -> None:
            nonlocal sleeps
            sleeps += 1
            if sleeps == 1:
                with log.open("a") as handle:
                    handle.write(f"[INFO] later {HOSTILE}\n")
                return
            raise KeyboardInterrupt

        monkeypatch.setattr(cli.asyncio, "sleep", sleep)
    _run_main(argv, monkeypatch)
    out, _err = capsys.readouterr()
    _assert_escaped(out)
    assert "first" in out
    if follow:
        assert "later" in out


def test_logs_follow_mode_runs_through_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cli.asyncio` is the module `run_logs` sleeps on (the follow test's
    seam)."""
    assert cli.asyncio is asyncio


# --- the auth-URL checks: every non-printable character (round 3) -------------


def _non_printable() -> list[str]:
    chars = [chr(c) for c in range(0x10000) if not (0xD800 <= c <= 0xDFFF)]
    chars += [chr(c) for c in range(0xE0000, 0xE0080)]  # tag characters
    return [ch for ch in chars if not ch.isprintable()]


def test_a_public_auth_url_refuses_every_non_printable_character() -> None:
    from pmcp.auth import AuthMessage, render_auth_message, sanitize_public_auth_url

    refused = render_auth_message(AuthMessage.PUBLIC_URL_CONTROL_CHARACTER)
    for ch in _non_printable():
        with pytest.raises(ValueError) as caught:
            sanitize_public_auth_url(f"https://auth.example.com/a{ch}b")
        assert str(caught.value) == refused, hex(ord(ch))


@pytest.mark.parametrize("provenance", ["remote", "operator"])
def test_an_elicitation_url_refuses_c1_bidi_and_tags(provenance: str) -> None:
    from pmcp.auth import sanitize_url_elicitation_url

    for ch in ("\x9b", "\x85", "‮", "​", " ", "\U000e0041"):
        with pytest.raises(ValueError):
            sanitize_url_elicitation_url(
                f"https://auth.example.com/x{ch}",
                provenance=provenance,  # type: ignore[arg-type]
            )


def test_a_peers_elicitation_with_a_non_printable_id_or_url_is_dropped() -> None:
    from pmcp.auth import parse_url_elicitation_error

    def payload(elicitation_id: str, url: str) -> dict[str, Any]:
        return {
            "error": {
                "code": -32042,
                "message": "m",
                "data": {
                    "elicitations": [{"elicitationId": elicitation_id, "url": url}]
                },
            }
        }

    good = "https://auth.example.com/consent"
    assert parse_url_elicitation_error(payload("abc", good))
    assert parse_url_elicitation_error(payload("a\x9bc", good)) == []
    assert parse_url_elicitation_error(payload("abc", good + "\x9b2J")) == []
