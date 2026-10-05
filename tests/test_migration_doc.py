"""MIGRATING.md stays true to the CHANGELOG and to the code (see Consiliency/pmcp#351).

What a wrong guide fails on:

* **Sections.** Every bolded title in the CHANGELOG's first ``### Upgrade notes``
  block has a ``###`` section of the same name; every breaking-change section
  answers the four questions, and its *What to do* shows something to run or
  write (code), not prose alone.
* **Commands.** Every ``pmcp`` invocation in any code span or block -- at line
  start, inside a ``for``/``if``, after ``&&``/``;``/``|``/``$(`` -- names real
  subcommands and flags in the parser ``pmcp.cli.parse_args`` builds, with valid
  choices, and every one in a ``bash`` block parses.
* **Quoted output.** Every message, log line, error and code the guide quotes
  for 3.0 exists in the source: matched against every string template in
  ``src/pmcp`` (literals and f-strings, placeholders as wildcards) and the
  ``AuthMessage`` registry, never against a list kept here. An ``OLD`` → ``NEW``
  rename must name a NEW the code has and an OLD it no longer has. Text quoted
  from 2.7.3 sits in a 2.7.3 table column or a block tagged ``quote: 2.7.3``.
* **Gate claims.** Every ``gate-case`` comment runs through the real
  ``tools/call`` validator with the stated outcome, every outcome is quoted in
  the guide, and a ``tools-call-rejected`` snippet fails for the stated reason
  at the stated path.
* **Versions.** Every ``pmcp==`` pin is the previous release (the newest dated
  2.x heading in the CHANGELOG, also a git tag when tags are present), every
  ``pmcp<N`` names the major being upgraded to, and ``pmcp.__version__`` is
  either that previous release (unreleased main) or in the new major.
* **Log grep.** Each alternative of the checklist's log ``grep`` matches a real
  WARNING template rendered through pmcp's log format, and every WARNING
  template that reports something pmcp ignored, or a spawn, matches the grep.
* **Snippets.** Every YAML/JSON block is tagged and parses through its real
  loader with the stated outcome.
* **Rollback.** The rollback section's ``rollback-table`` has exactly one row per
  breaking-change section, each starting "Safe on 2.7.3" or "Reverse:". Every
  "Reverse:" row is one whose behaviour on 2.7.3 was verified, and its cell
  must contain that section's pinned phrases in ``_REVERSE_STEPS``: what 2.7.3
  does and the step (with code) that undoes the migration.
* **Runs.** Tagged ``run:`` blocks are executed: the approval recipes against a
  scratch checkout with the real CLI, the backup recipe under bash and zsh.
* **2.7.3 facts.** Behaviour of 2.7.3 cannot be re-derived from this tree, so the
  few claims about it that a reader acts on were verified against 2.7.3 from
  PyPI (Consiliency/pmcp#364) and are pinned as sentences in ``_FACTS_273``.

``test_seeded_wrong_guides_fail`` feeds in the seven wrong guides that passed
this test's first version in review of Consiliency/pmcp#364, plus three more
(a log grep that misses ignored pins, an invented refusal message, a backup
that copies symlinks as links), plus two from the round-3 board (a rollback
that drops or "safes" the task-unit reversal) and six from round 4 (rollback
rows that reverse the wrong way or name no step), and two from round 5 (a task
row that applies both reversals to every server, a pin example 2.7.3 ignores);
each must fail.
"""

from __future__ import annotations

import argparse
import ast
import functools
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
MIGRATING = ROOT / "MIGRATING.md"
CHANGELOG = ROOT / "CHANGELOG.md"
SRC = ROOT / "src" / "pmcp"

_FOUR_QUESTIONS = (
    "**Am I affected?**",
    "**What changed",
    "**What to do",
    "**How to verify",
)

#: Claims about 2.7.3 that a reader acts on, verified by running pmcp 2.7.3 from
#: PyPI on 2026-10-04 (Consiliency/pmcp#364). (section, sentence fragment);
#: whitespace is normalised before comparing.
_FACTS_273: tuple[tuple[str, str], ...] = (
    (
        "New `packages:` policy section",
        "2.7.3 **refuses to start** on a policy that has a `packages:` key",
    ),
    ("Rolling back to 2.7.3", "2.7.3 does not know the key and **refuses to start**"),
    (
        "Rolling back to 2.7.3",
        "| `packages:` in any policy | **2.7.3 refuses to start.**",
    ),
    ("Rolling back to 2.7.3", "Ignored silently. The servers run unpinned again"),
    ("Rolling back to 2.7.3", "Every key from `.env` reaches every spawned server."),
    (
        "A symlinked `.mcp.json` is no longer edited",
        "2.7.3 replaced your symlink with a regular file.",
    ),
    (
        "Spawned servers no longer inherit the keys pmcp loaded from `.env`",
        "pmcp still loads `.env` into its own environment, but it now strips every key it loaded that way",
    ),
)


# --------------------------------------------------------------------------
# Reading the guide
# --------------------------------------------------------------------------


def _guide() -> str:
    return MIGRATING.read_text(encoding="utf-8")


def _upgrade_note_titles() -> list[str]:
    text = CHANGELOG.read_text(encoding="utf-8")
    start = text.index("\n### Upgrade notes\n")
    body = text[start + len("\n### Upgrade notes\n") :]
    end = re.search(r"^#{2,3} ", body, flags=re.MULTILINE)
    block = body[: end.start()] if end else body
    titles = re.findall(r"^- \*\*(.+?)\*\*", block, flags=re.MULTILINE)
    return [title.rstrip(".") for title in titles]


def _headings(text: str, level: str = "###") -> list[str]:
    return re.findall(rf"^{level} (.+?)\s*$", text, flags=re.MULTILINE)


def _section(text: str, title: str, level: str = "###") -> str:
    start = text.index(f"\n{level} {title}\n")
    rest = text[start + 1 :]
    end = re.search(r"^#{2,3} ", rest[len(level) + 1 :], flags=re.MULTILINE)
    return rest[: end.start() + len(level) + 1] if end else rest


def _without_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _fences(text: str) -> list[tuple[str, str, str]]:
    """(tag comment or '', language, dedented body) for every fenced block."""
    out = []
    for match in re.finditer(
        r"(?:^[ \t]*<!-- (?P<tag>[^\n]*?) -->[ \t]*\n)?"
        r"^[ \t]*```(?P<lang>[a-z]*)\n(?P<body>.*?)^[ \t]*```",
        text,
        flags=re.MULTILINE | re.S,
    ):
        out.append(
            (
                match.group("tag") or "",
                match.group("lang"),
                textwrap.dedent(match.group("body")),
            )
        )
    return out


def _prose(text: str) -> str:
    """The text with fenced blocks and comments removed."""
    no_fences = re.sub(
        r"^[ \t]*```[a-z]*\n.*?^[ \t]*```", "", text, flags=re.MULTILINE | re.S
    )
    return _without_comments(no_fences)


def _spans(text: str) -> list[str]:
    """Inline code spans outside fences, with soft line breaks joined."""
    return [
        re.sub(r"\s*\n\s*", " ", span)
        for span in re.findall(r"(?<!`)`([^`]+)`(?!`)", _prose(text))
    ]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# --------------------------------------------------------------------------
# Checks (each returns a list of problems; empty means the guide passes)
# --------------------------------------------------------------------------


def _check_sections(text: str) -> list[str]:
    problems = []
    headings = _headings(text)
    for title in _upgrade_note_titles():
        if title not in headings:
            problems.append(f"no section for Upgrade-notes item {title!r}")
            continue
        if title.startswith("Known issues in"):
            continue
        section = _section(text, title)
        for question in _FOUR_QUESTIONS:
            if question not in section:
                problems.append(f"{title!r} is missing {question}")
        todo = re.search(
            r"\*\*What to do[.?]?\*\*(.*?)(?=\*\*How to verify|\Z)", section, re.S
        )
        if todo and not re.search(r"`[^`]+`|```", todo.group(1)):
            problems.append(f"{title!r}: 'What to do' shows nothing to run or write")
    for name in ("Upgrade checklist", "Rolling back to 2.7.3"):
        if name not in _headings(text, "##"):
            problems.append(f"no '## {name}' section")
    return problems


def _check_facts(text: str) -> list[str]:
    problems = []
    for title, fact in _FACTS_273:
        level = "##" if title in _headings(text, "##") else "###"
        try:
            section = _section(text, title, level)
        except ValueError:
            problems.append(f"no section {title!r} for a pinned 2.7.3 fact")
            continue
        if _norm(fact) not in _norm(section):
            problems.append(
                f"{title!r} no longer states the verified 2.7.3 fact: {fact!r}"
            )
    return problems


# -- commands ----------------------------------------------------------------


class _Captured(Exception):
    pass


@functools.lru_cache(maxsize=1)
def _parser_cached() -> argparse.ArgumentParser:
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


_PLACEHOLDER = re.compile(r"<[^<>\s][^<>]*>")
# `pmcp` as a command word: at the start, or after whitespace, a shell operator,
# `$(`, a backquote or `do`/`then`; followed by whitespace or the end.
_INVOCATION = re.compile(r"(?:^|(?<=[\s;|&(`]))pmcp(?=\s|$)")
_CUT = re.compile(r"\s(?:\d?>|\||&|;)|\)|\s#|;\s*(?:fi|done)\b")


def _invocations(code: str) -> list[str]:
    """Every ``pmcp …`` command in a piece of code, cut at the first shell operator."""
    found = []
    for line in code.splitlines():
        for match in _INVOCATION.finditer(line):
            if line[: match.start()].endswith("-u "):
                continue  # `journalctl -u pmcp` names the unit, not the command
            rest = re.sub(r"\$\([^()]*\)", "X", line[match.start() :])
            found.append(_CUT.split(rest, maxsplit=1)[0].strip())
    return found


def _argv(command: str) -> list[str]:
    tokens = shlex.split(_PLACEHOLDER.sub("X", command), comments=True)
    assert tokens and tokens[0] == "pmcp", command
    return tokens[1:]


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


_NO_VALUE = (
    argparse._StoreTrueAction,
    argparse._StoreFalseAction,
    argparse._CountAction,
    argparse._HelpAction,
    argparse._VersionAction,
)


def _structure_problems(argv: list[str]) -> list[str]:
    current = _parser_cached()
    path = ["pmcp"]
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("-") and token != "-":
            flag, _, inline_value = token.partition("=")
            action = current._option_string_actions.get(flag)
            if action is None:
                return [f"{' '.join(path)} has no option {flag!r}"]
            takes_value = action.nargs != 0 and not isinstance(action, _NO_VALUE)
            value = inline_value
            if takes_value and not inline_value:
                i += 1
                if i >= len(argv):
                    return [f"{flag} needs a value"]
                value = argv[i]
            if takes_value and action.choices is not None and value != "X":
                if value not in action.choices:
                    return [f"{flag} {value!r} is not one of {sorted(action.choices)}"]
        else:
            # A positional where the parser expects a subcommand must be one.
            subs = _subparsers(current)
            if subs and token not in subs:
                return [f"{' '.join(path)} has no subcommand {token!r}"]
            if token in subs:
                current = subs[token]
                path.append(token)
        i += 1
    return []


def _code_blocks_with_commands(text: str) -> tuple[list[str], list[str]]:
    """(commands in bash/sh blocks, commands in other code: spans and other fences)."""
    bash: list[str] = []
    other: list[str] = []
    for _tag, lang, body in _fences(text):
        if lang in ("bash", "sh", ""):
            bash.extend(_invocations(body))
    for span in _spans(text):
        if not re.match(r"pmcp \d", span):  # `pmcp 3.0.0` is --version output
            other.extend(_invocations(span))
    return bash, other


def _check_commands(text: str) -> list[str]:
    problems = []
    bash, other = _code_blocks_with_commands(text)
    for command in bash + other:
        for problem in _structure_problems(_argv(command)):
            problems.append(f"`{command}`: {problem}")
    for command in bash:
        argv = _argv(command)
        try:
            with mock.patch("sys.stderr"):
                _parser_cached().parse_args(argv)
        except SystemExit as exit_:
            if exit_.code != 0:
                problems.append(f"`{command}` does not parse")
    return problems


# -- quoted output -------------------------------------------------------------

_STAR = "\x00"


def _render(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append(_STAR)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _render(node.left), _render(node.right)
        return None if left is None or right is None else left + right
    return None


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                ids.add(id(body[0].value))
    return ids


@functools.lru_cache(maxsize=1)
def _templates() -> tuple[str, ...]:
    """Every string the package can emit, placeholders as wildcards."""
    from pmcp.auth import auth_messages

    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if id(node) in docstrings or not isinstance(
                node, (ast.Constant, ast.JoinedStr, ast.BinOp)
            ):
                continue
            rendered = _render(node)
            if rendered and len(rendered.strip(_STAR)) >= 3:
                found.add(re.sub(r"%[-#0-9.]*[sdrfi]|\{[^{}]*\}", _STAR, rendered))
    for message in auth_messages().values():
        found.add(re.sub(r"\{[^{}]*\}", _STAR, str(message)))
    for data in _shipped_data():
        found.update(
            line.strip()
            for line in data.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return tuple(found)


def _shipped_data() -> list[Path]:
    return sorted(p for ext in ("*.yaml", "*.yml", "*.json") for p in SRC.rglob(ext))


@functools.lru_cache(maxsize=1)
def _identifiers() -> str:
    files = [*SRC.rglob("*.py"), *_shipped_data()]
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def _wild_quote(quote: str) -> str:
    quote = quote.replace("…", _STAR)
    return _PLACEHOLDER.sub(_STAR, quote)


def _could_be_equal(a: str, b: str) -> bool:
    """Is there one string both wildcard patterns match? (`_STAR` matches anything.)"""
    n, m = len(a), len(b)

    @functools.lru_cache(maxsize=None)
    def go(i: int, j: int) -> bool:
        if i == n and j == m:
            return True
        if i < n and a[i] == _STAR:
            return go(i + 1, j) or (j < m and go(i, j + 1))
        if j < m and b[j] == _STAR:
            return go(i, j + 1) or (i < n and go(i + 1, j))
        return i < n and j < m and a[i] == b[j] and go(i + 1, j + 1)

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4 * (n + m) + 100))
    return go(0, 0)


def _literal_length(pattern: str) -> int:
    return len(pattern.replace(_STAR, ""))


def _matches_source(quote: str) -> bool:
    """Could ``quote`` be output of some template in the source?

    Both sides may hold wildcards (the quote's ``…`` and ``<placeholder>``, the
    template's interpolations). A template must carry at least ten literal
    characters (or the whole quote's) so a bare ``f"{a}: {b}"`` matches nothing.
    """
    # A quote may be an excerpt of a line: anything may come before or after.
    wild = _STAR + _wild_quote(quote) + _STAR
    quote_parts = [p for p in wild.split(_STAR) if len(p) >= 6]
    need = min(10, _literal_length(wild))
    for template in _templates():
        if _literal_length(template) < need:
            continue
        template_parts = [p for p in template.split(_STAR) if len(p) >= 6]
        if not (
            any(p.strip() in wild for p in template_parts)
            or any(p.strip() in template for p in quote_parts)
        ):
            continue
        if _could_be_equal(wild, template):
            return True
    return False


_PREFIXES = re.compile(
    r"^(?:\[(?:WARNING|ERROR|INFO|PINNED|FAILED)\] |\[[0-9T:-]+\] \[[A-Z]+\] |Error: |Fatal error: |(?:WARN )?[a-z]+(?:_[a-z]+)+: )"
)
_MESSAGE_START = re.compile(r"^[A-Z\[][A-Za-z\[]")
_CODE_IDENTIFIER = re.compile(
    r"^(?:[a-z][a-z0-9]*(?:_[a-z0-9]+)+|PUBLIC_URL_[A-Z_]+|PMCP_[A-Z_]+|audit\.[a-z]+)$"
)


def _strip_prefixes(line: str) -> tuple[str, list[str]]:
    codes: list[str] = []
    while True:
        match = _PREFIXES.match(line)
        if not match:
            return line, codes
        prefix = match.group(0)
        code = re.match(r"(?:WARN )?([a-z]+(?:_[a-z]+)+): ", prefix)
        if code:
            codes.append(code.group(1))
        line = line[len(prefix) :]


def _is_message(quote: str) -> bool:
    return (
        bool(_MESSAGE_START.match(quote)) and " " in quote.strip() and len(quote) >= 12
    )


def _table_cells_273(text: str) -> set[str]:
    """Spans in a table column whose header names 2.7.3 (text quoted from 2.7.3)."""
    exempt: set[str] = set()
    header: list[str] | None = None
    for line in text.splitlines():
        if not line.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = cells
            continue
        for name, cell in zip(header, cells):
            if "2.7.3" in name:
                exempt.update(re.findall(r"`([^`]+)`", cell))
    return exempt


def _quotes(text: str) -> list[str]:
    """Message-like and code-like quotes the guide makes about 3.0."""
    exempt = _table_cells_273(text)
    quotes = [s for s in _spans(text) if s not in exempt]
    for tag, lang, body in _fences(text):
        if lang == "text" and "quote: 2.7.3" not in tag:
            quotes.extend(line for line in body.splitlines() if line.strip())
    return quotes


def _check_quotes(text: str) -> list[str]:
    problems = []
    renames = re.findall(r"`([^`]+)` → `([^`]+)`", _prose(text).replace("\n", " "))
    renamed_old = {re.sub(r"\s+", " ", old) for old, _ in renames}
    for old, new in renames:
        old, new = _norm(old), _norm(new)
        name_like = re.fullmatch(r"[\w:.]+", new) is not None
        if name_like:
            if not re.search(rf"(?<![\w:]){re.escape(new)}(?![\w])", _identifiers()):
                problems.append(
                    f"renamed-to name {new!r} is not in the source or shipped data"
                )
            if re.search(rf"(?<![\w:]){re.escape(old)}(?![\w])", _identifiers()):
                problems.append(
                    f"renamed-from name {old!r} is still in the source or shipped data"
                )
            continue
        if not _matches_source(new):
            problems.append(f"rename target {new!r} is not a message the code has")
        if _matches_exact(old):
            problems.append(f"renamed-from {old!r} is still a message the code has")
    for quote in _quotes(text):
        quote = _norm(quote).strip()
        if quote in renamed_old or quote.startswith("Input validation error"):
            continue  # renames are judged above; gate messages by the gate cases
        body, codes = _strip_prefixes(quote)
        for code in codes:
            if not re.search(rf"\b{re.escape(code)}\b", _identifiers()):
                problems.append(f"quoted code {code!r} is not in the source")
        if _CODE_IDENTIFIER.match(body):
            if not re.search(rf"\b{re.escape(body)}\b", _identifiers()):
                problems.append(f"quoted identifier {body!r} is not in the source")
        elif (
            _is_message(body) or (codes and len(body.split()) >= 3)
        ) and not _matches_source(body):
            problems.append(f"quoted message {body!r} matches nothing in src/pmcp")
    return problems


def _matches_exact(message: str) -> bool:
    return any(_STAR not in t and _norm(t) == message for t in _templates())


# -- gate --------------------------------------------------------------------


def _gate_error(call: dict[str, Any]) -> jsonschema.ValidationError | None:
    from pmcp.tools.handlers import get_gateway_tool_definitions
    from pmcp.tools.schema import gate_error_for

    tool = next(t for t in get_gateway_tool_definitions() if t.name == call["name"])
    # The error the server reports, not jsonschema's raw choice: a nullable
    # argument's refusal is folded first (Consiliency/pmcp#369).
    return gate_error_for(call["arguments"], tool.input_schema)


def _check_gate(text: str) -> list[str]:
    problems = []
    cases = re.findall(r"<!-- gate-case: (\{.*?\}) => (.*?) -->", text)
    if len(cases) < 5:
        problems.append("expected the gate table's cases as gate-case comments")
    prose = _norm(_prose(text))
    outcomes = set()
    for raw, expected in cases:
        error = _gate_error(json.loads(raw))
        got = "accepted" if error is None else error.message
        outcomes.add(got)
        if got != expected:
            problems.append(
                f"gate case {raw} gives {got!r}, the guide says {expected!r}"
            )
        elif expected != "accepted" and expected not in prose:
            problems.append(f"gate outcome {expected!r} is not quoted in the guide")
    exempt = _table_cells_273(text)
    for span in _spans(text):
        span = _norm(span)
        if span in exempt:
            continue
        match = re.match(r"Input validation error: (.+)$", span)
        if match and match.group(1) != "…" and match.group(1) not in outcomes:
            problems.append(f"{span!r} is not produced by any gate case")
    return problems


# -- versions ----------------------------------------------------------------


def _previous_release() -> str:
    for version in re.findall(
        r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d\d-\d\d",
        CHANGELOG.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    ):
        if not version.startswith(_target_major() + "."):
            return version
    raise AssertionError("no previous release heading in CHANGELOG.md")


def _target_major() -> str:
    match = re.match(r"# Upgrading from (\d+)\.x to (\d+)\.0", _guide())
    assert match, "MIGRATING.md title must read 'Upgrading from N.x to M.0'"
    return match.group(2)


def _check_versions(text: str) -> list[str]:
    import pmcp

    problems = []
    previous, target = _previous_release(), _target_major()
    for pinned in re.findall(r"pmcp==([0-9][0-9.]*)", text):
        if pinned != previous:
            problems.append(
                f"pin pmcp=={pinned} is not the previous release {previous}"
            )
    for bound in re.findall(r"pmcp<([0-9]+)", text):
        if bound != target:
            problems.append(
                f"bound pmcp<{bound} is not the major being upgraded to ({target})"
            )
    if not (pmcp.__version__ == previous or pmcp.__version__.split(".")[0] == target):
        problems.append(
            f"__version__ {pmcp.__version__} is neither {previous} nor {target}.x"
        )
    for printed in re.findall(r"pmcp (\d+\.\d+\.\d+)`", text):
        if printed.split(".")[0] != target:
            problems.append(f"the guide shows `pmcp {printed}` for the new release")
    return problems


# -- log grep ----------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _warning_templates() -> tuple[str, ...]:
    found = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        returns: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Return) and inner.value is not None:
                        rendered = _render(inner.value)
                        if rendered:
                            returns[node.name] = rendered
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "warning"
                and node.args
            ):
                continue
            arg = node.args[0]
            rendered = _render(arg)
            if (
                rendered is None
                and isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Name)
            ):
                rendered = returns.get(arg.func.id)
            if rendered:
                found.add(re.sub(r"%[-#0-9.]*[sdrfi]", _STAR, rendered))
    return tuple(found)


def _log_line(message: str) -> str:
    from pmcp import cli

    root = logging.getLogger()
    before = list(root.handlers)
    cli.setup_logging("info", log_to_file=False)
    try:
        handler = next(h for h in root.handlers if h not in before)
        assert handler.formatter is not None
        record = logging.LogRecord(
            "pmcp", logging.WARNING, __file__, 0, message, None, None
        )
        return handler.formatter.format(record)
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)


def _log_grep(text: str) -> str | None:
    for _tag, lang, body in _fences(text):
        for line in body.splitlines():
            match = re.search(r"grep -E '(\\\[WARNING\\\][^']*)' \S*gateway\.log", line)
            if lang == "bash" and match:
                return match.group(1)
    return None


def _check_log_grep(text: str) -> list[str]:
    pattern = _log_grep(text)
    if pattern is None:
        return ["the checklist has no `grep -E '\\[WARNING\\] (…)' …gateway.log` line"]
    problems = []
    regex = re.compile(pattern)
    templates = _warning_templates()
    lines = [_log_line(t.replace(_STAR, "X")) for t in templates]
    group = re.search(r"\((.*)\)", pattern)
    for alternative in group.group(1).split("|") if group else []:
        if not any(_could_be_equal(alternative + _STAR, t) for t in templates):
            problems.append(
                f"log grep alternative {alternative!r} matches no WARNING pmcp logs"
            )
    for template, line in zip(templates, lines):
        if re.match(
            r"(Ignoring |Spawning |Installing |Starting install job |Verifying installation of |Running update probe)",
            template,
        ):
            if not regex.search(line):
                problems.append(
                    f"log grep misses the WARNING {template.replace(_STAR, '…')[:70]!r}"
                )
    return problems


# -- snippets ----------------------------------------------------------------

_SNIPPET_TAG = re.compile(r"^snippet: (?P<kind>[a-z-]+)(?P<attrs>.*)$")


def _snippets(text: str) -> list[tuple[str, dict[str, str], str]]:
    out: list[tuple[str, dict[str, str], str]] = []
    for tag, lang, body in _fences(text):
        if lang not in ("yaml", "json"):
            continue
        match = _SNIPPET_TAG.match(tag)
        if match is None:
            out.append(("untagged", {}, body))
            continue
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', match.group("attrs")))
        out.append((match.group("kind"), attrs, body))
    return out


def _check_snippet(
    kind: str, attrs: dict[str, str], body: str, tmp: Path, text: str
) -> list[str]:
    try:
        if kind == "policy":
            _load_policy(body, tmp)
        elif kind == "policy-invalid":
            try:
                _load_policy(body, tmp)
            except ValueError as error:
                if "names a version" not in str(error):
                    return [f"invalid policy fails for another reason: {error}"]
            else:
                return ["a policy the guide calls invalid loads"]
        elif kind == "mcp-json":
            _load_mcp_json(body, tmp)
        elif kind == "guidance":
            _load_guidance(body, tmp)
        elif kind == "manifest-overlay":
            _load_overlay(body)
        elif kind == "tools-call-accepted":
            refusal = _gate_error(json.loads(body))
            if refusal is not None:
                return [
                    f"call the guide says is accepted is refused: {refusal.message}"
                ]
        elif kind == "tools-call-rejected":
            refusal = _gate_error(json.loads(body))
            if refusal is None:
                return ["call the guide says is refused is accepted"]
            path = ".".join(str(p) for p in refusal.absolute_path)
            if refusal.message != attrs.get("reason") or path != attrs.get("path"):
                return [
                    f"refused for {refusal.message!r} at {path!r}, the guide says "
                    f"{attrs.get('reason')!r} at {attrs.get('path')!r}"
                ]
            if attrs["reason"] not in _norm(_prose(text)):
                return [
                    f"the refusal reason {attrs['reason']!r} is not quoted in the guide"
                ]
        else:
            return [f"untagged or unknown snippet kind {kind!r}"]
    except Exception as error:  # noqa: BLE001 - every failure is a problem to report
        return [f"{kind} snippet does not load: {type(error).__name__}: {error}"]
    return []


def _load_policy(body: str, tmp: Path) -> None:
    from pmcp.policy.policy import PolicyManager

    path = tmp / "policy.yaml"
    path.write_text(body, encoding="utf-8")
    PolicyManager(path)


def _load_mcp_json(body: str, tmp: Path) -> None:
    from pmcp.config.loader import load_configs

    data = json.loads(body)
    path = tmp / "mcp.json"
    path.write_text(body, encoding="utf-8")
    project = tmp / "empty-project"
    project.mkdir(exist_ok=True)
    names = {
        c.name
        for c in load_configs(
            project_root=project, user_config_paths=[], custom_config_path=path
        )
    }
    assert set(data["mcpServers"]) <= names, (set(data["mcpServers"]), names)


def _load_guidance(body: str, tmp: Path) -> None:
    from pmcp.config.guidance import GuidanceConfig, load_guidance_config

    data = yaml.safe_load(body)
    GuidanceConfig(**data["guidance"])
    path = tmp / "gateway-guidance.yaml"
    path.write_text(body, encoding="utf-8")
    config = load_guidance_config(path)
    for key, value in data["guidance"].items():
        assert getattr(config, key) == value


def _load_overlay(body: str) -> None:
    from pmcp.manifest import loader

    overlay = Path.home() / ".pmcp" / "manifest.yaml"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(body, encoding="utf-8")
    loader.clear_manifest_cache()
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect(level=logging.WARNING)
    log = logging.getLogger("pmcp.manifest")
    log.addHandler(handler)
    try:
        manifest = loader.load_manifest()
        assert not records, [r.getMessage() for r in records]
        for server, version in (
            yaml.safe_load(body).get("server_version") or {}
        ).items():
            assert any(
                arg.endswith(f"@{version}") for arg in manifest.servers[server].args
            )
    finally:
        log.removeHandler(handler)
        overlay.unlink()
        loader.clear_manifest_cache()


def _check_snippets(text: str, tmp: Path) -> list[str]:
    problems = []
    snippets = _snippets(text)
    if len(snippets) < 8:
        problems.append("expected at least eight tagged YAML/JSON snippets")
    for index, (kind, attrs, body) in enumerate(snippets):
        work = tmp / f"snippet-{index}"
        work.mkdir()
        problems.extend(
            f"snippet {index} ({kind}): {p}"
            for p in _check_snippet(kind, attrs, body, work, text)
        )
    return problems


# -- runs ----------------------------------------------------------------------


def _run_block(text: str, name: str) -> str:
    for tag, _lang, body in _fences(text):
        if tag == f"run: {name}":
            return body
    raise AssertionError(f"no block tagged 'run: {name}'")


def _shim(tmp: Path) -> Path:
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "pmcp"
    shim.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -m pmcp "$@"\n', encoding="utf-8"
    )
    shim.chmod(0o755)
    return bin_dir


def _sandbox_env(home: Path, bin_dir: Path | None = None) -> dict[str, str]:
    path = os.pathsep.join(
        p for p in (str(bin_dir) if bin_dir else "", "/usr/bin", "/bin") if p
    )
    return {
        "HOME": str(home),
        "PATH": path,
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        "LANG": "C.UTF-8",
    }


def _check_approve_run(text: str, tmp: Path) -> list[str]:
    """The approval recipes, run as written, approve the files the guide names."""
    problems = []
    bin_dir = _shim(tmp)
    for name in ("approve", "approve-loop"):
        home = tmp / f"home-{name}"
        repo = tmp / f"repo-{name}"
        (repo / ".pmcp").mkdir(parents=True)
        home.mkdir()
        (repo / ".mcp.json").write_text('{"mcpServers": {}}\n', encoding="utf-8")
        (repo / ".mcp-gateway-policy.yaml").write_text(
            "servers: {}\n", encoding="utf-8"
        )
        (repo / ".pmcp" / "manifest.yaml").write_text("servers: {}\n", encoding="utf-8")
        script = _run_block(text, name).replace("/path/to/repo", str(repo))
        subprocess.run(
            ["bash", "-c", script],
            cwd=repo,
            env=_sandbox_env(home, bin_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        listed = subprocess.run(
            [sys.executable, "-m", "pmcp", "trust", "list"],
            cwd=tmp,
            env=_sandbox_env(home),
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
        for rel in (".mcp.json", ".mcp-gateway-policy.yaml", ".pmcp/manifest.yaml"):
            if not re.search(
                rf"^approved .*{re.escape(str(repo / rel))}$",
                listed,
                flags=re.MULTILINE,
            ):
                problems.append(f"'run: {name}' did not approve {rel}")
    return problems


def _check_backup_run(text: str, tmp: Path) -> list[str]:
    """The backup recipe copies contents (through symlinks) under bash and zsh."""
    problems = []
    script = _run_block(text, "backup")
    shells = [s for s in ("bash", "zsh") if shutil.which(s)]
    for shell in shells:
        home = tmp / f"home-{shell}"
        (home / ".claude").mkdir(parents=True)
        (home / "dot" / "pmcp").mkdir(parents=True)
        (home / ".config").mkdir()
        (home / "dot" / "mcp.json").write_text('{"user": true}\n', encoding="utf-8")
        (home / ".mcp.json").symlink_to(home / "dot" / "mcp.json")
        (home / ".claude" / ".mcp.json").write_text(
            '{"claude": true}\n', encoding="utf-8"
        )
        (home / ".claude" / "gateway-policy.yaml").write_text(
            "servers: {}\n", encoding="utf-8"
        )
        (home / "dot" / "pmcp" / "pmcp.env").write_text(
            'A_TOKEN="1"\n', encoding="utf-8"
        )
        (home / ".config" / "pmcp").symlink_to(home / "dot" / "pmcp")
        result = subprocess.run(
            [shell, "-c", script],
            env=_sandbox_env(home),
            capture_output=True,
            text=True,
            timeout=60,
        )
        backup = home / "pmcp-backup-2.7.3"
        expected = {
            ".mcp.json": '{"user": true}\n',
            ".claude/.mcp.json": '{"claude": true}\n',
            ".claude/gateway-policy.yaml": "servers: {}\n",
            ".config/pmcp/pmcp.env": 'A_TOKEN="1"\n',
        }
        if result.returncode != 0:
            problems.append(
                f"backup under {shell} exited {result.returncode}: {result.stderr.strip()}"
            )
        for rel, content in expected.items():
            copy = backup / rel
            if (
                copy.is_symlink()
                or not copy.is_file()
                or copy.read_text(encoding="utf-8") != content
            ):
                problems.append(
                    f"backup under {shell} did not copy the contents of ~/{rel}"
                )
    return problems


# -- rollback ------------------------------------------------------------------

#: Sections whose migration step 2.7.3 reads differently, verified by running
#: 2.7.3 against the migrated form (Consiliency/pmcp#364), each with the exact
#: phrases its "Reverse:" cell must contain: what 2.7.3 does, and the step that
#: undoes the migration (every step phrase carries a code span). A row that says
#: the opposite -- divide instead of multiply, keep instead of remove, "merges"
#: instead of "instead of" -- loses its phrase and fails. Any other row may be
#: "Safe on 2.7.3", but a "Reverse:" row for a section not listed here fails
#: until its step is pinned here too.
_REVERSE_STEPS: dict[str, tuple[str, ...]] = {
    "Project files need approval": (
        "2.7.3 uses the project file *instead of* yours",
        "Move the project file aside (`mv .mcp-gateway-policy.yaml .mcp-gateway-policy.yaml.3x`)",
    ),
    "New `packages:` policy section": (
        "remove every `packages:` section",
        "or 2.7.3 refuses to start",
    ),
    "Feedback submission is off by default": (
        "then `GITHUB_TOKEN`",
        "then a `gh` CLI on the gateway's `PATH` using its stored login",
        "run `pmcp guidance --telemetry off` before you restart on 2.7.3",
        "unset `PMCP_FEEDBACK_TOKEN` and `GITHUB_TOKEN`",
        "keep `gh` off the gateway's `PATH`",
    ),
    "Auth responses changed": ("must match the 2.7.3 texts and `500`s again",),
    "The `tools/call` gate enforces the schemas pmcp advertises": (
        "don't send an explicit `null` for an optional argument; 2.7.3 rejects it",
    ),
    "Task `ttl` and `poll_interval` are seconds in pmcp and milliseconds on the wire": (
        "do **one** of these per downstream server, never both",
        "**A spec-conforming (third-party) server** reads milliseconds",
        "a migrated `ttl: 300` keeps a task for 0.3 s",
        "Multiply by 1000 again (`ttl: 300000`, `poll_interval: 2500`) in the callers of that server",
        "**A pmcp tenant server built to the old seconds contract that you switched to milliseconds for 3.0**: Switch it back to reading and returning seconds, and keep its callers sending seconds (`ttl: 300`)",
    ),
    "Manifest version pins": (
        "2.7.3 ignores `version:` and `server_version:` silently",
        "put it in that server's `args` in `~/.mcp.json`",
        '(for example `"args": ["-y", "firecrawl-mcp@3.25.5"]`)',
    ),
    "Agent-facing hints": (
        "see `try/catch` and `playwright::browser_screenshot` again",
        "Match both.",
    ),
    "Error text names the real failure": (
        "2.7.3 logs only `unhandled errors in a TaskGroup (1 sub-exception)`",
        "Match both.",
    ),
}


def _breaking_sections(text: str) -> list[str]:
    start = text.index("\n## Breaking changes\n")
    end = re.search(r"^## ", text[start + 2 :], flags=re.MULTILINE)
    body = text[start : start + 2 + end.start()] if end else text[start:]
    return [t for t in _headings(body) if not t.startswith("Known issues in")]


def _rollback_rows(text: str) -> list[tuple[str, str, str]]:
    """(link text, anchor, status cell) for each row of the rollback table."""
    start = text.find("<!-- rollback-table -->")
    if start == -1:
        return []
    rows: list[tuple[str, str, str]] = []
    for line in text[start:].splitlines()[1:]:
        if not line.startswith("|"):
            if rows:
                break
            continue
        match = re.match(r"\| \[(.+?)\]\(#([^)]+)\) \| (.+) \|$", line)
        if match:
            rows.append((match.group(1), match.group(2), match.group(3)))
    return rows


def _anchor(title: str) -> str:
    """GitHub's heading anchor."""
    slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
    return slug


def _check_rollback(text: str) -> list[str]:
    """Every breaking change says what rolling back to 2.7.3 does to the
    migrated form: either it is safe there, or how to reverse it."""
    problems = []
    rows = _rollback_rows(text)
    if not rows:
        return ["no '<!-- rollback-table -->' table in the rollback section"]
    titles = [title for title, _anchor_, _status in rows]
    for title in _breaking_sections(text):
        if titles.count(title) != 1:
            problems.append(f"rollback table needs exactly one row for {title!r}")
    for title, anchor, status in rows:
        if title not in _breaking_sections(text):
            problems.append(f"rollback row {title!r} names no breaking-change section")
        if anchor != _anchor(title):
            problems.append(
                f"rollback row {title!r} links #{anchor}, not #{_anchor(title)}"
            )
        if not status.startswith(("Safe on 2.7.3", "Reverse:")):
            problems.append(
                f"rollback row {title!r} must start 'Safe on 2.7.3' or 'Reverse:'"
            )
        steps = _REVERSE_STEPS.get(title)
        if status.startswith("Reverse:") and steps is None:
            problems.append(
                f"rollback row {title!r} reverses with no pinned step in _REVERSE_STEPS"
            )
        if steps is None:
            continue
        if not status.startswith("Reverse:"):
            problems.append(
                f"rollback row {title!r} must say how to reverse it on 2.7.3"
            )
        cell = _norm(status)
        for phrase in steps:
            if _norm(phrase) not in cell:
                problems.append(f"rollback row {title!r} no longer says {phrase!r}")
        if not any("`" in phrase for phrase in steps):
            problems.append(f"rollback row {title!r}: no pinned step names code")
    return problems


# -- everything ----------------------------------------------------------------


def _all_problems(text: str, tmp: Path, *, runs: bool = True) -> list[str]:
    problems = (
        _check_sections(text)
        + _check_facts(text)
        + _check_commands(text)
        + _check_quotes(text)
        + _check_gate(text)
        + _check_versions(text)
        + _check_log_grep(text)
        + _check_rollback(text)
        + _check_snippets(text, tmp / "snippets")
    )
    if runs:
        problems += _check_approve_run(text, tmp / "approve") + _check_backup_run(
            text, tmp / "backup"
        )
    return problems


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


@pytest.fixture
def work(tmp_path: Path) -> Path:
    for name in ("snippets", "approve", "backup"):
        (tmp_path / name).mkdir()
    return tmp_path


def test_the_changelog_has_upgrade_notes_to_cover() -> None:
    titles = _upgrade_note_titles()
    assert len(titles) >= 19, titles
    assert any(t.startswith("Known issues in") for t in titles)


@pytest.mark.parametrize(
    "check",
    [
        _check_sections,
        _check_facts,
        _check_commands,
        _check_quotes,
        _check_gate,
        _check_versions,
        _check_log_grep,
        _check_rollback,
    ],
    ids=lambda f: f.__name__.removeprefix("_check_"),
)
def test_the_guide_passes(check: Callable[[str], list[str]]) -> None:
    assert check(_guide()) == []


def test_the_guide_snippets_load(work: Path) -> None:
    assert _check_snippets(_guide(), work / "snippets") == []


def test_the_approval_recipes_approve(work: Path) -> None:
    assert _check_approve_run(_guide(), work / "approve") == []


def test_the_backup_recipe_copies_contents_in_bash_and_zsh(work: Path) -> None:
    assert _check_backup_run(_guide(), work / "backup") == []


def test_the_guide_shows_commands_anywhere() -> None:
    bash, other = _code_blocks_with_commands(_guide())
    assert len(bash) >= 20 and len(other) >= 15
    assert any("then pmcp trust approve" in line for line in _guide().splitlines())
    assert 'pmcp trust approve "$PWD/$f"' in bash


def test_the_previous_release_is_tagged_when_tags_exist() -> None:
    tags = subprocess.run(
        ["git", "tag", "-l", "v*"], cwd=ROOT, capture_output=True, text=True
    ).stdout.split()
    if not tags:
        pytest.skip("no git tags in this checkout")
    assert f"v{_previous_release()}" in tags


def test_the_guide_links_from_changelog_readme_and_security() -> None:
    for doc in ("CHANGELOG.md", "README.md", "SECURITY.md"):
        assert "(MIGRATING.md)" in (ROOT / doc).read_text(encoding="utf-8"), doc


def _seeded_wrong_guides(text: str) -> dict[str, str]:
    """The wrong guides that passed the first version of this test (Consiliency/pmcp#364)."""

    def env_nothing(t: str) -> str:
        section = _section(
            t, "Spawned servers no longer inherit the keys pmcp loaded from `.env`"
        )
        todo = re.search(r"\*\*What to do\.\*\*.*?(?=\*\*How to verify)", section, re.S)
        assert todo
        return t.replace(
            section, section.replace(todo.group(0), "**What to do.** Nothing.\n\n")
        )

    def rejected_for_another_reason(t: str) -> str:
        old = '{"name": "gateway.invoke", "arguments": {"tool_id": "github::search_repositories", "arguments": {}, "task": {"enabled": 1, "ttl": 300}}}'
        new = '{"name": "gateway.invoke", "arguments": {"arguments": {}, "task": {"enabled": true, "ttl": 5}}}'
        assert old in t
        return t.replace(old, new)

    mutations: dict[str, Callable[[str], str]] = {
        "a-loop-subcommand-typo": lambda t: t.replace(
            'then pmcp trust approve "$PWD/$f"', 'then pmcp trust aprove "$PWD/$f"'
        ),
        "b-auth-rename-reversed": lambda t: t.replace(
            "`Missing bearer token.` → `Empty token.`",
            "`Empty token.` → `Missing bearer token.`",
        ),
        "c-rollback-pins-the-new-release": lambda t: t.replace(
            "'pmcp==2.7.3'", "'pmcp==3.0.0'", 1
        ),
        "d-approve-step-revokes": lambda t: t.replace(
            'pmcp trust approve "$PWD/.mcp.json"',
            'pmcp trust revoke "$PWD/.mcp.json"',
            1,
        ),
        "e-rejected-for-the-wrong-reason": rejected_for_another_reason,
        "f-2.7.3-starts-normally": lambda t: t.replace(
            "2.7.3 does not know the key and **refuses to start**",
            "2.7.3 ignores the key and starts normally",
        ),
        "g-env-what-to-do-is-nothing": env_nothing,
        # Round 3 (Consiliency/pmcp#364): the rollback forgets the task units.
        "k-rollback-keeps-migrated-task-units": lambda t: re.sub(
            r"\n\| \[Task `ttl` and `poll_interval` are seconds[^\n]*", "", t
        ),
        "l-rollback-calls-task-units-safe": lambda t: t.replace(
            "| Reverse: 2.7.3 sends `ttl` and `poll_interval`",
            "| Safe on 2.7.3: it sends `ttl` and `poll_interval`",
        ),
        # Round 4 (Consiliency/pmcp#364): rows that reverse the wrong way.
        "m-rollback-divides-task-units": lambda t: t.replace(
            "Multiply by 1000 again (`ttl: 300000`, `poll_interval: 2500`)",
            "Divide by 1000 again (`ttl: 0.3`, `poll_interval: 0.0025`)",
        ),
        "n-rollback-keeps-packages": lambda t: t.replace(
            "Reverse: remove every `packages:` section (step 1 above), or 2.7.3 refuses to start.",
            "Reverse: keep every `packages:` section; 2.7.3 ignores it.",
        ),
        "o-rollback-says-policies-merge": lambda t: t.replace(
            "2.7.3 uses the project file *instead of* yours",
            "2.7.3 merges the project file with yours",
        ),
        "p-rollback-keeps-sending-null": lambda t: t.replace(
            "don't send an explicit `null` for an optional argument; 2.7.3 rejects it",
            "keep sending an explicit `null` for an optional argument; 2.7.3 accepts it",
        ),
        "q-rollback-reverse-without-a-step": lambda t: re.sub(
            r"(\| \[Feedback submission is off by default\]\([^)]*\) \| )[^\n]*",
            r"\1Reverse: something. The enable_feedback_submission key is ignored, as is `confirm_submission=true`. |",
            t,
        ),
        # Round 5 (Consiliency/pmcp#364): grok's row that applies both steps to
        # every server, and claude's pin example 2.7.3 does not read.
        "s-rollback-task-units-both-steps": lambda t: re.sub(
            r"(\| \[Task `ttl` and `poll_interval`[^|]*\| )[^\n]*",
            lambda m: m.group(1)
            + "Reverse: 2.7.3 sends `ttl` and `poll_interval` to the server unchanged, "
            "and MCP reads them as milliseconds, so a migrated `ttl: 300` keeps a task "
            "for 0.3 s, not five minutes. Multiply by 1000 again (`ttl: 300000`, "
            "`poll_interval: 2500`) in the callers of that server. Switch a tenant "
            "server you changed back to reading and returning seconds. |",
            t,
        ),
        "t-rollback-pin-example-uses-ignored-key": lambda t: t.replace(
            '"args": ["-y", "firecrawl-mcp@3.25.5"]', '"version": "3.25.5"', 1
        ),
        "r-rollback-feedback-only-github-token": lambda t: t.replace(
            "run `pmcp guidance --telemetry off` before you restart on 2.7.3",
            "unset `GITHUB_TOKEN` before you restart on 2.7.3",
        ),
        # Beyond the seven: the review's F004 and F006 shapes, and an invented message.
        "h-log-grep-misses-ignored-pins": lambda t: t.replace(
            "\\[WARNING\\] (Ignoring |Spawning ",
            "\\[WARNING\\] (Ignoring project|Ignoring PMCP_|Spawning ",
        ),
        "i-invented-refusal-message": lambda t: t.replace(
            "refusing to edit a symlinked .mcp.json at …`",
            "refusing to replace a linked .mcp.json at …`",
        ),
        "j-backup-glob-and-cp-a": lambda t: t.replace(
            'cp -RLp "$f" "$dest"', 'cp -a "$f" "$dest"'
        ),
    }
    out = {}
    for name, mutate in mutations.items():
        wrong = mutate(text)
        assert wrong != text, f"seeded mutation {name} no longer applies"
        out[name] = wrong
    return out


@pytest.mark.parametrize("name", sorted(_seeded_wrong_guides(_guide())))
def test_seeded_wrong_guides_fail(name: str, work: Path) -> None:
    wrong = _seeded_wrong_guides(_guide())[name]
    runs = name.startswith(("d-", "j-"))
    assert _all_problems(wrong, work, runs=runs), f"the wrong guide {name} passed"


def _function(source: str, name: str, namespace: dict[str, Any]) -> Any:
    node = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    node.decorator_list = []
    exec("from __future__ import annotations\n" + ast.unparse(node), namespace)
    return namespace[name]


def test_rollback_restores_task_duration_contract() -> None:
    """Codex's falsifier from the round-3 board on Consiliency/pmcp#364: run the
    real seconds-to-wire boundary from HEAD and from v2.7.3, and require the
    rollback section to tell a migrated caller to restore milliseconds."""
    from types import SimpleNamespace

    old = subprocess.run(
        ["git", "show", "v2.7.3:src/pmcp/client/manager.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if old.returncode != 0:
        pytest.skip("v2.7.3 is not in this checkout")
    rollback = _guide().split("## Rolling back to 2.7.3\n", 1)[1]
    current_source = (SRC / "client" / "manager.py").read_text(encoding="utf-8")
    types_source = (SRC / "types.py").read_text(encoding="utf-8")
    scale = next(
        ast.literal_eval(node.value)
        for node in ast.parse(types_source).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "MS_PER_SECOND" for t in node.targets
        )
    )
    namespace: dict[str, Any] = {
        "TaskMetadataInput": SimpleNamespace,
        "MS_PER_SECOND": scale,
    }
    _function(types_source, "task_seconds_to_wire", namespace)
    current = _function(current_source, "_task_wire_metadata", namespace)
    previous = _function(old.stdout, "_task_wire_metadata", namespace)
    migrated = SimpleNamespace(
        ttl=300, poll_interval=2.5, metadata=None, requestor_context=None
    )
    assert current(None, migrated) == {"ttl": 300000, "pollInterval": 2500.0}
    assert previous(None, migrated) == {"ttl": 300, "pollInterval": 2.5}
    # On 2.7.3 the caller must send what 3.0 puts on the wire for the same
    # duration, so the rollback row must name exactly those values.
    wire = current(None, migrated)
    row = next(
        (status for title, _a, status in _rollback_rows(_guide()) if "ttl" in title),
        "",
    )
    expected = (
        f"`ttl: {wire['ttl']}`",
        f"`poll_interval: {wire['pollInterval']:g}`",
    )
    assert "## Rolling back to 2.7.3" in _guide() and rollback
    for literal in expected:
        assert literal in row, (
            "Rollback must restore task caller units: on 2.7.3 send "
            f"{literal}, what 3.0 sends for ttl 300 s / poll_interval 2.5 s."
        )


def _task_rollback_branches() -> tuple[str, str]:
    """The task-units rollback cell, split into its spec-server and tenant branches."""
    row = next(
        status
        for title, _anchor_, status in _rollback_rows(_guide())
        if title.startswith("Task `ttl` and `poll_interval`")
    )
    spec_label = "**A spec-conforming (third-party) server**"
    tenant_label = "**A pmcp tenant server"
    assert spec_label in row and tenant_label in row, row
    spec = row[row.index(spec_label) : row.index(tenant_label)]
    tenant = row[row.index(tenant_label) :]
    return spec, tenant


def test_task_rollback_preserves_duration_on_each_kind_of_downstream() -> None:
    """Grok's round-5 falsifier on Consiliency/pmcp#364, as a passing property.

    Run 2.7.3's real ``_task_wire_metadata`` on the value each rollback branch
    tells the caller to send, and read the wire value the way that branch's
    server does: a spec-conforming server in milliseconds, a tenant switched
    back to seconds in seconds. Each must keep the five minutes a 3.0
    ``ttl: 300`` asked for. Applying the spec branch's value to a seconds
    tenant (both steps at once) would keep it 300000 s, which is why the row
    must say "never both".
    """
    from types import SimpleNamespace

    old = subprocess.run(
        ["git", "show", "v2.7.3:src/pmcp/client/manager.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if old.returncode != 0:
        pytest.skip("v2.7.3 is not in this checkout")
    forward = _function(
        old.stdout, "_task_wire_metadata", {"TaskMetadataInput": SimpleNamespace}
    )
    spec, tenant = _task_rollback_branches()

    def sent(branch: str) -> int:
        # The value the branch tells callers to send: inside its "(`ttl: N`"
        # instruction, not the `ttl: 300` it quotes as the problem.
        match = re.search(r"(?:again|sending seconds) \(`ttl: (\d+)`", branch)
        assert match, branch
        caller = SimpleNamespace(
            ttl=int(match.group(1)),
            poll_interval=None,
            metadata=None,
            requestor_context=None,
        )
        return int(forward(None, caller)["ttl"])

    assert sent(spec) / 1000 == 300, "spec-server branch must keep 300 s"
    assert sent(tenant) == 300, "seconds-tenant branch must keep 300 s"
    both = sent(spec)  # the spec value read as seconds by a reverted tenant
    assert both != 300
    row = next(
        status
        for title, _anchor_, status in _rollback_rows(_guide())
        if title.startswith("Task `ttl` and `poll_interval`")
    )
    assert "never both" in row
