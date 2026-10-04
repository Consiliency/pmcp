"""MIGRATING.md stays true to the CHANGELOG and to the code (see Consiliency/pmcp#351).

Three things are pinned here:

* every bolded title in the CHANGELOG's first ``### Upgrade notes`` block has a
  section of the same name in MIGRATING.md, and every breaking-change section
  answers the four questions the guide promises;
* every ``pmcp`` command the guide shows is real: its subcommands and flags
  exist in the parser ``pmcp.cli.parse_args`` builds, a flag's value is one of
  its choices, and every complete command in a ``bash`` block parses;
* every YAML or JSON block is tagged with the model it claims to be, and parses
  through that model's real loader with the outcome the guide states.

The first ``### Upgrade notes`` heading is used whatever release heading it sits
under, so the test holds on main's ``[Unreleased]`` block and on the dated
release block alike.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shlex
import textwrap
from pathlib import Path
from typing import Any
from unittest import mock

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
MIGRATING = ROOT / "MIGRATING.md"
CHANGELOG = ROOT / "CHANGELOG.md"

_FOUR_QUESTIONS = (
    "**Am I affected?**",
    "**What changed",
    "**What to do",
    "**How to verify",
)


def _migrating() -> str:
    return MIGRATING.read_text(encoding="utf-8")


def _upgrade_note_titles() -> list[str]:
    text = CHANGELOG.read_text(encoding="utf-8")
    start = text.index("\n### Upgrade notes\n")
    body = text[start + len("\n### Upgrade notes\n") :]
    end = re.search(r"^#{2,3} ", body, flags=re.MULTILINE)
    block = body[: end.start()] if end else body
    titles = re.findall(r"^- \*\*(.+?)\*\*", block, flags=re.MULTILINE)
    return [title.rstrip(".") for title in titles]


def _headings(level: str = "###") -> list[str]:
    return re.findall(rf"^{level} (.+?)\s*$", _migrating(), flags=re.MULTILINE)


def _section(title: str) -> str:
    text = _migrating()
    start = text.index(f"\n### {title}\n")
    rest = text[start + 1 :]
    end = re.search(r"^#{2,3} ", rest[4:], flags=re.MULTILINE)
    return rest[: end.start() + 4] if end else rest


# --------------------------------------------------------------------------
# Upgrade notes <-> sections
# --------------------------------------------------------------------------


def test_the_changelog_has_upgrade_notes_to_cover() -> None:
    titles = _upgrade_note_titles()
    assert len(titles) >= 19, titles
    assert any(t.startswith("Known issues in") for t in titles)


@pytest.mark.parametrize("title", _upgrade_note_titles())
def test_every_upgrade_note_has_a_section(title: str) -> None:
    assert title in _headings(), (
        f"CHANGELOG Upgrade notes item {title!r} has no '### {title}' section "
        "in MIGRATING.md"
    )


@pytest.mark.parametrize(
    "title",
    [t for t in _upgrade_note_titles() if not t.startswith("Known issues in")],
)
def test_every_breaking_change_section_answers_the_four_questions(title: str) -> None:
    section = _section(title)
    for question in _FOUR_QUESTIONS:
        assert question in section, f"{title!r} is missing {question}"


def test_the_guide_links_from_changelog_readme_and_security() -> None:
    for doc in ("CHANGELOG.md", "README.md", "SECURITY.md"):
        assert "(MIGRATING.md)" in (ROOT / doc).read_text(encoding="utf-8"), doc


def test_the_guide_covers_rolling_back_and_the_checklist() -> None:
    assert "Upgrade checklist" in _headings("##")
    assert "Rolling back to 2.7.3" in _headings("##")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


class _Captured(Exception):
    pass


def _parser() -> argparse.ArgumentParser:
    """The parser ``pmcp.cli.parse_args`` builds, captured before it parses."""
    from pmcp import cli

    def capture(self: argparse.ArgumentParser, *args: Any, **kwargs: Any) -> Any:
        raise _Captured(self)

    with mock.patch.object(argparse.ArgumentParser, "parse_args", capture):
        try:
            cli.parse_args()
        except _Captured as captured:
            parser = captured.args[0]
            assert isinstance(parser, argparse.ArgumentParser)
            return parser
    raise AssertionError("pmcp.cli.parse_args never called parse_args")


_SHELL_STOP = {"|", "||", "&&", ";", ">", ">>", "2>&1", "2>/dev/null", "&"}
_PLACEHOLDER = re.compile(r"<[^<>\s][^<>]*>")


def _tokens(command: str) -> list[str]:
    """``pmcp …`` argv up to the first shell operator, placeholders filled in."""
    command = _PLACEHOLDER.sub("X", command)
    # Cut at the first redirection, pipe or list operator outside the argv.
    command = re.split(r"\s(?:\d?>|\||&|;)", command, maxsplit=1)[0]
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;>")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    out: list[str] = []
    for token in lexer:
        if token in _SHELL_STOP or token.startswith(("2>", ">", "|", "&", ";")):
            break
        out.append(token)
    assert out and out[0] == "pmcp", command
    return out[1:]


def _bash_commands() -> list[str]:
    commands: list[str] = []
    for block in re.findall(
        r"^[ \t]*```bash\n(.*?)^[ \t]*```", _migrating(), flags=re.MULTILINE | re.S
    ):
        for line in textwrap.dedent(block).splitlines():
            line = line.strip()
            if line.startswith("pmcp ") or line == "pmcp":
                commands.append(line)
    return commands


def _inline_commands() -> list[str]:
    spans = re.findall(r"(?<!`)`(pmcp(?: [^`]*)?)`(?!`)", _migrating())
    return [span for span in spans if span == "pmcp" or span.startswith("pmcp ")]


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _check_structure(parser: argparse.ArgumentParser, argv: list[str]) -> None:
    """Every subcommand and flag in ``argv`` exists where it is used."""
    current = parser
    path = ["pmcp"]
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("-") and token != "-":
            flag, _, inline_value = token.partition("=")
            assert flag in current._option_string_actions, (
                f"{' '.join(path)} has no option {flag!r}"
            )
            action = current._option_string_actions[flag]
            takes_value = action.nargs != 0 and not isinstance(
                action,
                (
                    argparse._StoreTrueAction,
                    argparse._StoreFalseAction,
                    argparse._CountAction,
                    argparse._HelpAction,
                    argparse._VersionAction,
                ),
            )
            if takes_value and not inline_value:
                i += 1
                assert i < len(argv), f"{flag} needs a value"
                value = argv[i]
            else:
                value = inline_value
            if takes_value and action.choices is not None and value != "X":
                assert value in action.choices, (
                    f"{flag} {value!r} not in {action.choices}"
                )
        else:
            subs = _subparsers(current)
            if token in subs:
                current = subs[token]
                path.append(token)
        i += 1


def test_the_guide_shows_commands() -> None:
    assert len(_bash_commands()) >= 15
    assert len(_inline_commands()) >= 15


@pytest.mark.parametrize("command", _bash_commands() + _inline_commands())
def test_every_pmcp_command_names_real_subcommands_and_flags(command: str) -> None:
    _check_structure(_parser(), _tokens(command))


@pytest.mark.parametrize("command", _bash_commands())
def test_every_complete_pmcp_command_parses(command: str) -> None:
    argv = _tokens(command)
    try:
        _parser().parse_args(argv)
    except SystemExit as exit_:
        assert exit_.code == 0, f"argparse rejected: pmcp {' '.join(argv)}"


def test_the_command_check_catches_a_wrong_flag() -> None:
    with pytest.raises(AssertionError):
        _check_structure(_parser(), _tokens("pmcp trust approve --force X"))
    with pytest.raises(AssertionError):
        _check_structure(
            _parser(), _tokens("pmcp guidance --feedback-submission maybe")
        )


# --------------------------------------------------------------------------
# Snippets
# --------------------------------------------------------------------------

_SNIPPET = re.compile(
    r"^[ \t]*<!-- snippet: (?P<kind>[a-z-]+) -->[ \t]*\n"
    r"(?P<indent>[ \t]*)```(?P<lang>yaml|json)\n(?P<body>.*?)^[ \t]*```",
    flags=re.MULTILINE | re.S,
)


def _snippets() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in _SNIPPET.finditer(_migrating()):
        body = textwrap.dedent(match.group("body"))
        found.append((match.group("kind"), body))
    return found


def test_every_yaml_and_json_block_is_tagged() -> None:
    fences = re.findall(r"^[ \t]*```(?:yaml|json)\n", _migrating(), flags=re.MULTILINE)
    assert len(fences) == len(_snippets()), (
        "every ```yaml/```json block in MIGRATING.md needs a "
        "'<!-- snippet: KIND -->' line directly above it"
    )
    assert len(fences) >= 8


def _check_policy(body: str, tmp_path: Path) -> None:
    from pmcp.policy.policy import PolicyManager

    path = tmp_path / "policy.yaml"
    path.write_text(body, encoding="utf-8")
    PolicyManager(path)


def _check_mcp_json(body: str, tmp_path: Path) -> None:
    from pmcp.config.loader import load_configs

    data = json.loads(body)
    path = tmp_path / "mcp.json"
    path.write_text(body, encoding="utf-8")
    project = tmp_path / "empty-project"
    project.mkdir()
    names = {
        c.name
        for c in load_configs(
            project_root=project, user_config_paths=[], custom_config_path=path
        )
    }
    assert set(data["mcpServers"]) <= names, (set(data["mcpServers"]), names)


def _check_guidance(body: str, tmp_path: Path) -> None:
    from pmcp.config.guidance import GuidanceConfig, load_guidance_config

    data = yaml.safe_load(body)
    GuidanceConfig(**data["guidance"])  # raises on a bad key or type
    path = tmp_path / "gateway-guidance.yaml"
    path.write_text(body, encoding="utf-8")
    config = load_guidance_config(path)
    for key, value in data["guidance"].items():
        assert getattr(config, key) == value


def _check_manifest_overlay(
    body: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from pmcp.manifest import loader

    overlay = Path.home() / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(body, encoding="utf-8")
    loader.clear_manifest_cache()
    try:
        with caplog.at_level(logging.WARNING, logger="pmcp.manifest"):
            manifest = loader.load_manifest()
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert not warnings, warnings
        for server, version in (
            yaml.safe_load(body).get("server_version") or {}
        ).items():
            assert any(
                arg.endswith(f"@{version}") for arg in manifest.servers[server].args
            )
    finally:
        overlay.unlink()
        loader.clear_manifest_cache()


def _gate(body: str) -> None:
    from pmcp.server import GATE_VALIDATOR
    from pmcp.tools.handlers import get_gateway_tool_definitions

    call = json.loads(body)
    tool = next(t for t in get_gateway_tool_definitions() if t.name == call["name"])
    jsonschema.validate(
        instance=call["arguments"], schema=tool.input_schema, cls=GATE_VALIDATOR
    )


@pytest.mark.parametrize(
    ("kind", "body"),
    _snippets(),
    ids=[f"{i}-{kind}" for i, (kind, _) in enumerate(_snippets())],
)
def test_every_snippet_parses_through_its_real_loader(
    kind: str, body: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    if kind == "policy":
        _check_policy(body, tmp_path)
    elif kind == "policy-invalid":
        with pytest.raises(ValueError, match="names a version"):
            _check_policy(body, tmp_path)
    elif kind == "mcp-json":
        _check_mcp_json(body, tmp_path)
    elif kind == "guidance":
        _check_guidance(body, tmp_path)
    elif kind == "manifest-overlay":
        _check_manifest_overlay(body, tmp_path, caplog)
    elif kind == "tools-call-accepted":
        _gate(body)
    elif kind == "tools-call-rejected":
        with pytest.raises(jsonschema.ValidationError):
            _gate(body)
    else:
        pytest.fail(f"unknown snippet kind {kind!r}")
