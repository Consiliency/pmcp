"""A parse error never echoes the structured text it rejected
(Consiliency/pmcp#297; rev 6, reclassified by origin in rev 7).

PyYAML renders a snippet of the input around the error mark, and a YAML tag
makes it run a constructor whose *plain* ``ValueError`` / ``KeyError`` quotes
the value (``k: !!int <value>``). So a failure is classified by where it
happened, not by its type: every parse of structured text in ``src/pmcp``
goes through ``pmcp.parsing`` (``load_yaml`` / ``load_json`` /
``load_json_file`` / ``parse_timestamp``), which turns any exception raised
while parsing into a value-free ``ParseError`` that chains nothing.

Two halves:
- **static**: no module in ``src/pmcp`` but ``parsing.py`` references a
  parser -- resolved through ``import x as y`` / ``from x import y as z`` /
  ``import datetime`` -- except the attributes named safe below (an
  allowlist, so a parser entry point nobody listed fails), nor calls a
  distinctive parser name on any receiver (``.fromisoformat``,
  ``.safe_load``, ``.load_all``, ``.raw_decode``, ...), nor ``.json()`` on a
  response; the exemptions are named, with the reason, and must all still
  exist;
- **dynamic**: a sentinel in each parser's rejected input -- syntax errors,
  every value-constructing YAML tag, undefined and duplicate anchors -- at
  every file-reading site pmcp exposes as a function, checked (in any case)
  in the log, the raised message, the traceback the gateway would print
  uncaught and stdout/stderr; plus the helpers themselves, the timestamp
  sites, and a fresh interpreter for the fatal policy path.
"""

from __future__ import annotations

import ast
import asyncio
import functools
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.test_argument_error_echo import _FAMILIES, _forbidden, _record_text

_SRC = Path(__file__).resolve().parents[1] / "src" / "pmcp"

#: The one module allowed to call a parser.
_HELPERS = "parsing.py"

#: Per resolved module (or class), the attributes that do not parse text.
#: An allowlist: any other attribute of these -- ``safe_load``, ``load_all``,
#: ``compose``, ``JSONDecoder``, ``fromisoformat``, ``strptime``, whatever a
#: later release adds -- is a parser reference.
_SAFE_ATTRIBUTES: dict[str, frozenset[str]] = {
    "json": frozenset({"dumps", "dump", "JSONDecodeError", "JSONEncoder"}),
    "yaml": frozenset(
        {
            "YAMLError",
            "MarkedYAMLError",
            "dump",
            "dump_all",
            "safe_dump",
            "safe_dump_all",
            "Dumper",
            "SafeDumper",
        }
    ),
    "tomllib": frozenset({"TOMLDecodeError"}),
    "datetime.datetime": frozenset(
        {"now", "utcnow", "fromtimestamp", "utcfromtimestamp", "min", "max"}
    ),
    "datetime.date": frozenset({"today", "fromtimestamp", "min", "max"}),
}

#: Parser entry points recognisable by name on ANY receiver -- a variable, an
#: attribute, a class the resolver cannot follow.
_PARSER_NAMES = frozenset(
    {
        "fromisoformat",
        "strptime",
        "safe_load",
        "safe_load_all",
        "load_all",
        "full_load",
        "full_load_all",
        "unsafe_load",
        "unsafe_load_all",
        "raw_decode",
        "JSONDecoder",
    }
)

#: Parser sites outside the helpers, ``file::function`` -> (kind, detail).
#: No site is exempt by name alone (rev 22, round-20 codex F001); each kind's
#: reason is enforced by a test below:
#: - ``dotenv``: python-dotenv never raises on bad input and logs the line
#:   number only (`test_dotenv_never_raises_and_logs_no_value`);
#: - ``response-decode``: a response object's ``.json()``/``.text()``. Every
#:   exception its client library raises decoding a body is registered as
#:   value-bearing (`test_every_http_client_pmcp_imports_has_its_decode_errors_registered`),
#:   and the call sits in a ``try`` whose handler catches it
#:   (`test_every_response_decode_site_is_inside_a_handler`); the sink guard
#:   checks that handler renders through the registry. End to end:
#:   `test_a_rejected_response_body_is_not_logged`.
_EXEMPT: dict[str, tuple[str, str]] = {
    "env_store.py::read_env_file": ("dotenv", "dotenv_values"),
    "cli.py::load_startup_env": ("dotenv", "load_dotenv"),
    "tools/handlers.py::_check_api_key_available": ("dotenv", "load_dotenv"),
    "manifest/version_checker.py::get_npm_version": ("response-decode", "aiohttp"),
    "manifest/version_checker.py::get_pypi_version": ("response-decode", "aiohttp"),
    "manifest/version_checker.py::get_cargo_version": ("response-decode", "aiohttp"),
    "manifest/version_checker.py::get_docker_version": ("response-decode", "aiohttp"),
    "cli.py::_probe_http_health": ("response-decode", "httpx"),
}

_DOTENV = frozenset({"dotenv_values", "load_dotenv"})


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Local name -> the dotted name it is bound to, for every import in the
    module (at any depth: a function-local ``import yaml`` counts)."""
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound[alias.asname] = alias.name
                else:
                    head = alias.name.split(".")[0]
                    bound[head] = head
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bound


def _qualified(node: ast.AST, bound: dict[str, str]) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or node.id not in bound:
        return None
    return ".".join([bound[node.id], *reversed(parts)])


#: The parser packages: an attribute of any of their SUBMODULES
#: (``yaml.loader.SafeLoader``, ``yaml.cyaml.CSafeLoader``,
#: ``yaml.constructor.SafeConstructor``, ``json.decoder.JSONDecoder``) is a
#: parser reference unless its name is safe in the package itself (rev 8,
#: round-7 claude (3)).
_PARSER_PACKAGES = ("json", "yaml", "tomllib")


def _violation(qualified: str) -> bool:
    owner, _, attr = qualified.rpartition(".")
    if owner in _SAFE_ATTRIBUTES:
        return attr not in _SAFE_ATTRIBUTES[owner]
    package = owner.split(".")[0]
    if package in _PARSER_PACKAGES and owner != package:
        return attr not in _SAFE_ATTRIBUTES[package]
    return False


def _parser_references(source: str) -> list[tuple[int, str]]:
    """``(line, what)`` for every parser reference in ``source``."""
    tree = ast.parse(source)
    bound = _aliases(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if _violation(f"{node.module}.{alias.name}"):
                    found.append(
                        (node.lineno, f"from {node.module} import {alias.name}")
                    )
        elif isinstance(node, ast.Attribute):
            qualified = _qualified(node, bound)
            if qualified is not None and _violation(qualified):
                found.append((node.lineno, qualified))
            elif node.attr in _PARSER_NAMES:
                found.append((node.lineno, f".{node.attr}"))
            elif (
                isinstance(node.value, ast.Attribute)
                and node.value.attr in _PARSER_PACKAGES
                and node.attr not in _SAFE_ATTRIBUTES[node.value.attr]
            ):
                # A parser package reached through another module's attribute
                # (``policy.yaml.load``), which the resolver cannot follow.
                found.append((node.lineno, f".{node.value.attr}.{node.attr}"))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("json", "text"):
                # With or without arguments (``resp.json(content_type=None)``);
                # ``.text()`` decodes a body too (rev 22).
                found.append((node.lineno, f".{func.attr}()"))
            elif isinstance(func, ast.Name) and (
                func.id in _DOTENV or bound.get(func.id, "").startswith("dotenv.")
            ):
                found.append((node.lineno, func.id))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            qualified = bound.get(node.id)
            if qualified is not None and _violation(qualified):
                found.append((node.lineno, qualified))
    return sorted(set(found))


def _owner(tree: ast.AST, line: int) -> str:
    functions = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    owner = min(
        (f for f in functions if f.lineno <= line <= (f.end_lineno or f.lineno)),
        key=lambda f: (f.end_lineno or f.lineno) - f.lineno,
        default=None,
    )
    return owner.name if owner else "<module>"


def _parse_sites() -> dict[str, list[str]]:
    """``file::function`` -> the parser references in it, outside the helpers."""
    found: dict[str, list[str]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        if rel == _HELPERS or "baml_client" in path.parts:
            continue
        source = path.read_text()
        tree = ast.parse(source)
        for line, what in _parser_references(source):
            found.setdefault(f"{rel}::{_owner(tree, line)}", []).append(
                f"{line}: {what}"
            )
    return found


def test_no_parser_call_outside_the_helpers() -> None:
    sites = _parse_sites()
    unexempt = {k: v for k, v in sites.items() if k not in _EXEMPT}
    assert not unexempt, unexempt
    # Every exemption still names a real call: a stale one would hide the
    # next parse added to that function.
    assert set(_EXEMPT) == set(sites), sorted(set(_EXEMPT) ^ set(sites))


@pytest.mark.parametrize(
    "snippet",
    [
        "import yaml\nyaml.safe_load(t)",
        "import yaml as y\ny.safe_load(t)",
        "from yaml import safe_load\nsafe_load(t)",
        "from yaml import safe_load as sl\nsl(t)",
        "import yaml\nyaml.load_all(t)",
        "import yaml\nyaml.compose(t)",
        "import yaml\nf = yaml.full_load",
        "import json\njson.loads(t)",
        "import json as j\nj.load(f)",
        "from json import loads\nloads(t)",
        "import json\njson.JSONDecoder().decode(t)",
        "from json import JSONDecoder\nJSONDecoder().raw_decode(t)",
        "import tomllib\ntomllib.loads(t)",
        "from datetime import datetime\ndatetime.fromisoformat(t)",
        "from datetime import datetime as dt\ndt.fromisoformat(t)",
        "import datetime\ndatetime.datetime.fromisoformat(t)",
        "import datetime\ndatetime.date.fromisoformat(t)",
        "from datetime import datetime\ndatetime.strptime(t, f)",
        "cls.fromisoformat(t)",
        "self._yaml.safe_load(t)",
        "response.json()",
        "await resp.json(content_type=None)",
        "from yaml.loader import SafeLoader\nSafeLoader(t).get_single_data()",
        "from yaml.cyaml import CSafeLoader\nCSafeLoader(t).get_single_data()",
        "from yaml.constructor import SafeConstructor\nSafeConstructor()",
        "from json.decoder import JSONDecoder\nJSONDecoder().decode(t)",
        "import yaml.loader\nyaml.loader.SafeLoader(t)",
        "policy.yaml.load(t, Loader=policy.yaml.SafeLoader)",
        "from dotenv import load_dotenv as ld\nld(p)",
        "def f():\n    import yaml\n    return yaml.safe_load(t)",
    ],
)
def test_the_parse_site_check_sees_every_spelling(snippet: str) -> None:
    """No vacuous pass: each spelling of a parser call is found."""
    assert _parser_references(snippet), snippet


@pytest.mark.parametrize(
    "snippet",
    [
        "import json\njson.dumps(v)",
        "import yaml\nyaml.safe_dump(v)",
        "from datetime import datetime, timezone\ndatetime.now(timezone.utc)",
        "import json\nexcept_types = (json.JSONDecodeError, ValueError)",
        "from pmcp.parsing import load_yaml\nload_yaml(t, source='x')",
        "from json.decoder import JSONDecodeError\nexcept_types = (JSONDecodeError,)",
        "from yaml.error import MarkedYAMLError\nisinstance(e, MarkedYAMLError)",
        "self.yaml.safe_dump(v)",
    ],
)
def test_the_parse_site_check_passes_non_parsers(snippet: str) -> None:
    assert not _parser_references(snippet), snippet


# --- dynamic ---------------------------------------------------------------------


def _yaml_bad(s: str) -> str:
    """Four ways PyYAML rejects input, each quoting it in its message."""
    return f"servers: [{s}}}\n"


def _yaml_bads(s: str) -> list[str]:
    """Every way PyYAML rejects input that we know quotes it -- syntax, and
    each tag whose constructor raises a *plain* exception (rev 7, B1)."""
    return [
        f"servers: [{s}}}\n",  # ParserError (flow sequence)
        f"a: b\n  c: {s}: d\n",  # ScannerError (mapping values)
        f'k: "{s}\n',  # ScannerError (unterminated quote)
        f"k: !{s} v\n",  # ConstructorError (the tag is the input)
        f"k: !!int {s}\n",  # ValueError: invalid literal for int() ... '<s>'
        f"k: !!int 0x{s}\n",  # ValueError (base 16)
        f"k: !!float {s}\n",  # ValueError: could not convert ... '<s lowered>'
        f"k: !!bool {s}\n",  # KeyError: '<s lowered>'
        f"k: !!timestamp {s}\n",  # AttributeError
        f"k: !!binary {s}!\n",  # ConstructorError (base64)
        f"k: !!python/object:{s} {{}}\n",  # ConstructorError (the tag)
        f"k: !!python/name:{s} ''\n",  # ConstructorError (the tag)
        f"a: &x1 [1]\nb: *{s}\n",  # ComposerError: undefined alias '<s>'
        f"a: &{s} 1\nb: &{s} 2\n",  # ComposerError: duplicate anchor '<s>'
        f"k: !!set [{s}]\n",  # ConstructorError (node kind)
        f"k: {{<<: {s}}}\n",  # ConstructorError (merge)
        # Accepted, so filtered out by `_rejects` (measured: last key wins):
        f"{s}: 1\n{s}: 2\n",  # duplicate keys
    ]


def _json_bads(s: str) -> list[str]:
    return [f'{{"k": {s}}}', f'{{"{s}": 1,}}', f"[1, 2, {s}"]


def _run(fn: Callable[[], Any]) -> tuple[str, str]:
    """(the raised error as pmcp renders it, the traceback the gateway would
    print uncaught) for `fn`. pmcp renders an exception only through
    `exception_text` -- the static guard pins that at every sink -- so that,
    not the library's own `str()`, is what reaches a caller; a message pmcp
    builds itself (`ValueError(f"... {exception_text(e)}")`) is covered by
    the same call."""
    try:
        from pmcp.argument_errors import exception_text, safe_traceback_text
    except ImportError:  # a tree without the renderer (main, for the red run)
        import traceback

        def exception_text(error: BaseException) -> str:
            return str(error)

        def safe_traceback_text(error: BaseException) -> str:
            return "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )

    try:
        fn()
    except BaseException as error:  # noqa: BLE001 -- inspected
        return exception_text(error), safe_traceback_text(error)
    return "", ""


def _rejects(parser: str, text: str) -> bool:
    """Whether the raw parser rejects ``text`` -- with ANY exception: a tag's
    constructor raises ``ValueError``/``KeyError``/``AttributeError``."""
    import yaml

    try:
        (yaml.safe_load if parser == "yaml" else json.loads)(text)
    except Exception:  # noqa: BLE001 -- any rejection counts
        return True
    return False


def _forbidden_any_case(s: str) -> set[str]:
    """``_forbidden`` of the sentinel in each case: PyYAML's float and bool
    constructors quote the value lowercased."""
    return _forbidden(s) | _forbidden(s.lower()) | _forbidden(s.upper())


def _policy(tmp: Path, content: str, suffix: str, fatal: bool) -> Callable[[], Any]:
    from pmcp.policy.policy import PolicyManager

    path = tmp / f"policy{suffix}"
    path.write_text(content)
    return lambda: PolicyManager()._parse_policy(content, path, fatal=fatal)


def _write(tmp: Path, name: str, content: str) -> Path:
    path = tmp / name
    path.write_text(content)
    return path


def _cases() -> list[tuple[str, str, Callable[[Path, str], Callable[[], Any]]]]:
    """(label, parser, builder(tmp, bad text) -> call)."""
    from pmcp import package_approvals, trust_store
    from pmcp.config import guidance
    from pmcp.config import loader as config_loader
    from pmcp.manifest import loader as manifest_loader
    from pmcp.manifest import refresher, registry
    from pmcp.manifest.code_patterns_loader import CodePatternsLoader
    from pmcp.templates.code_snippets_loader import CodeSnippetsLoader

    return [
        ("policy yaml, warn", "yaml", lambda t, c: _policy(t, c, ".yaml", False)),
        ("policy yaml, fatal", "yaml", lambda t, c: _policy(t, c, ".yaml", True)),
        ("policy json, warn", "json", lambda t, c: _policy(t, c, ".json", False)),
        ("policy json, fatal", "json", lambda t, c: _policy(t, c, ".json", True)),
        (
            "manifest overlay",
            "yaml",
            lambda t, c: lambda: manifest_loader._parse_overlay_document(
                t / "overlay.yaml", c.encode()
            ),
        ),
        (
            "manifest",
            "yaml",
            lambda t, c: lambda: manifest_loader.load_manifest(_write(t, "m.yaml", c)),
        ),
        (
            "config file",
            "json",
            lambda t, c: lambda: config_loader.parse_config_bytes(
                c.encode(), t / ".mcp.json"
            ),
        ),
        (
            "config object",
            "json",
            lambda t, c: lambda: _returned(
                config_loader._config_object_from_bytes(c.encode())
            ),
        ),
        (
            "guidance",
            "yaml",
            lambda t, c: lambda: guidance.load_guidance_config(_write(t, "g.yaml", c)),
        ),
        (
            "code patterns",
            "yaml",
            lambda t, c: lambda: CodePatternsLoader(_write(t, "p.yaml", c)),
        ),
        (
            "code snippets",
            "yaml",
            lambda t, c: lambda: CodeSnippetsLoader(_write(t, "s.yaml", c)),
        ),
        (
            "descriptions cache",
            "yaml",
            lambda t, c: lambda: refresher.load_descriptions_cache(
                _write(t, "d.yaml", c)
            ),
        ),
        (
            "registry cache",
            "json",
            lambda t, c: lambda: registry.load_registry_cache(_write(t, "r.json", c)),
        ),
        (
            "trust store",
            "json",
            lambda t, c: lambda: trust_store._read_store(_write(t, "trust.json", c)),
        ),
        (
            "package approvals",
            "json",
            lambda t, c: lambda: package_approvals._read_store(_write(t, "a.json", c)),
        ),
    ]


def _returned(value: Any) -> None:
    """A site that returns its diagnostic instead of raising: make the
    returned text the 'raised' text."""
    raise RuntimeError(repr(value))


@pytest.mark.parametrize("label", [c[0] for c in _cases()])
@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_no_parse_site_echoes_its_input(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    label: str,
    family: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    (case,) = [c for c in _cases() if c[0] == label]
    _, parser, build = case
    s = _FAMILIES[family][1]
    # Only inputs the parser rejects: a digits-only sentinel makes `{"k": 123}`
    # valid JSON, which is data, not a parse error.
    bads = [
        bad
        for bad in (_yaml_bads(s) if parser == "yaml" else _json_bads(s))
        if _rejects(parser, bad)
    ]
    assert len(bads) >= (12 if parser == "yaml" else 2), (label, family, bads)
    forbidden = _forbidden_any_case(s)
    silent: list[str] = []
    for bad in bads:
        start = len(caplog.records)
        capfd.readouterr()
        raised, shown = _run(build(tmp_path, bad))
        streams = capfd.readouterr()
        logged = "\n".join(_record_text(r) for r in caplog.records[start:])
        for channel, text in (
            ("raised", raised),
            ("traceback", shown),
            ("log", logged),
            ("stdout/stderr", streams.out + streams.err),
        ):
            assert not any(form in text for form in forbidden), (
                label,
                bad,
                channel,
                text,
            )
        everything = raised + shown + logged + streams.out + streams.err
        if "could not parse" not in everything and (
            raised or shown or streams.out.strip() or streams.err.strip()
        ):
            silent.append(bad)
    # No vacuous pass, checked after every input's leak check: for each
    # rejected input pmcp said it could not parse, or said nothing at all (a
    # site that falls back silently, e.g. a cache miss). PyYAML reading a file
    # omits the snippet but a constructor error still names the input's tag,
    # so the leak half needs every input, not just the first.
    assert not silent, (label, silent)


def test_an_uncaught_fatal_policy_error_prints_no_input(tmp_path: Path) -> None:
    """An explicit policy that does not parse is fatal: the gateway exits with
    the error uncaught, and the interpreter prints it. Its chain holds the
    YAML error, whose text quotes the file; the excepthook `import pmcp`
    installs prints it structurally."""
    s = _FAMILIES["token"][1]
    policy = tmp_path / "policy.yaml"
    policy.write_text(_yaml_bad(s))
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pmcp.policy.policy import PolicyManager; "
            "PolicyManager(policy_path=__import__('pathlib').Path(sys.argv[1]))",
            str(policy),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "HOME": str(tmp_path)},
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "could not parse YAML policy file at line 1" in result.stderr, result.stderr
    assert "Traceback (most recent call last)" in result.stderr
    assert not any(form in result.stderr + result.stdout for form in _forbidden(s)), (
        result.stderr
    )


def test_parse_text_names_only_format_class_and_position() -> None:
    import yaml

    from pmcp.argument_errors import exception_text

    s = _FAMILIES["alpha"][1]
    for bad, expected in (
        (f"servers: [{s}}}", "could not parse YAML (ParserError) at line 1, column "),
        (f"k: !{s} v", "could not parse YAML (ConstructorError) at line 1, column "),
    ):
        try:
            yaml.safe_load(bad)
        except yaml.YAMLError as error:
            # PyYAML's snippet truncates long lines; any window is the leak.
            assert any(form in str(error) for form in _forbidden(s)), str(error)
            assert exception_text(error).startswith(expected), exception_text(error)
    try:
        json.loads(f'{{"k": {s}}}')
    except json.JSONDecodeError as error:
        assert (
            exception_text(error)
            == "could not parse JSON (JSONDecodeError) at line 1, column 7"
        )


def test_a_failing_log_handler_prints_no_input(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A handler whose `emit` raises while an `except` block for a parse
    error is active: `logging.Handler.handleError` prints the active chain to
    stderr. pmcp's wrapper prints it structurally."""
    import yaml

    import pmcp  # noqa: F401 -- installs the scrubber, excepthook and handleError

    class _Broken(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            # As every stdlib handler does: a failing emit calls handleError.
            try:
                raise OSError("disk gone")
            except Exception:
                self.handleError(record)

    s = _FAMILIES["hex"][1]
    logger = logging.getLogger("pmcp.test.broken")
    handler = _Broken()
    logger.addHandler(handler)
    try:
        capfd.readouterr()
        try:
            yaml.safe_load(f"servers: [{s}}}")
        except yaml.YAMLError:
            logger.error("could not load")
        err = capfd.readouterr().err
    finally:
        logger.removeHandler(handler)
    assert "--- Logging error ---" in err
    assert "could not parse YAML (ParserError)" in err, err
    # The handler's own error was raised while the parse error was handled:
    # its message is withheld, its class kept (rev 18).
    assert "\nOSError: could not parse YAML (ParserError)" in err, err
    assert "OSError: disk gone" not in err, err  # the frame's source line is code
    assert not any(form in err for form in _forbidden(s)), err


# --- the helpers themselves (rev 7) ---------------------------------------------


def _helper_yaml_inputs(s: str) -> list[str]:
    return [
        *(bad for bad in _yaml_bads(s) if _rejects("yaml", bad)),
        "[" * 3000 + s + "]" * 3000,  # RecursionError
    ]


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_load_yaml_classifies_every_failure_by_origin(family: str) -> None:
    import yaml

    from pmcp.argument_errors import exception_text, safe_traceback_text
    from pmcp.parsing import ParseError, YAMLParseError, load_yaml

    s = _FAMILIES[family][1]
    forbidden = _forbidden_any_case(s)
    causes: set[str] = set()
    for bad in _helper_yaml_inputs(s):
        with pytest.raises(YAMLParseError) as caught:
            load_yaml(bad, source="policy file")
        error = caught.value
        # It chains nothing, so no traceback printer can reach the original.
        assert error.__cause__ is None and error.__context__ is None, bad
        assert isinstance(error, (yaml.YAMLError, ValueError, ParseError))
        for text in (
            str(error),
            repr(error),
            exception_text(error),
            safe_traceback_text(error),
            repr(error.args),
        ):
            assert not any(form in text for form in forbidden), (bad, text)
        assert str(error).startswith("could not parse YAML policy file"), str(error)
        causes.add(error.cause or "")
    # No vacuous pass: the plain exceptions the board found are among them.
    plain = {"ValueError", "KeyError", "AttributeError", "RecursionError"}
    if family == "digits":
        plain.discard("ValueError")  # `!!int <digits>` is a valid int
    assert plain <= causes, causes
    assert {"ParserError", "ScannerError", "ConstructorError"} <= causes, causes
    if family in ("alpha", "hex", "token"):
        # A name with no space or leading digit is a valid anchor name.
        assert "ComposerError" in causes, causes


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_load_json_classifies_every_failure_by_origin(family: str) -> None:
    from pmcp.argument_errors import exception_text, safe_traceback_text
    from pmcp.parsing import JSONParseError, load_json

    s = _FAMILIES[family][1]
    forbidden = _forbidden_any_case(s)
    inputs: list[Any] = [
        *(b for b in _json_bads(s) if _rejects("json", b)),
        "[" * 100000 + json.dumps(s) + "]" * 100000,  # RecursionError
        s.encode("utf-8") + b"\xff",  # UnicodeDecodeError (encoding=)
    ]
    causes: set[str] = set()
    for bad in inputs:
        with pytest.raises(JSONParseError) as caught:
            load_json(bad, source="registry response", encoding="utf-8")
        error = caught.value
        assert error.__cause__ is None and error.__context__ is None
        assert isinstance(error, json.JSONDecodeError) and error.doc == ""
        for text in (str(error), exception_text(error), safe_traceback_text(error)):
            assert not any(form in text for form in forbidden), (bad, text)
        causes.add(error.cause or "")
    assert {"RecursionError", "UnicodeDecodeError"} <= causes, causes


def test_duplicate_yaml_keys_are_data_not_a_parse_error() -> None:
    """Measured, so the sweep's `_rejects` filter drops them: PyYAML's
    safe_load keeps the last value."""
    from pmcp.parsing import load_yaml

    assert load_yaml("a: 1\na: 2\n", source="x") == {"a": 2}


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_no_timestamp_site_echoes_its_input(family: str) -> None:
    """``datetime.fromisoformat`` quotes a bad timestamp (rev 7, N1)."""
    from pmcp import package_approvals, trust_store
    from pmcp.argument_errors import exception_text, safe_traceback_text
    from pmcp.types import McpTaskInfo

    s = "2026-13-99T" + _FAMILIES[family][1]
    forbidden = _forbidden_any_case(_FAMILIES[family][1])
    calls: list[Callable[[], Any]] = [
        lambda: trust_store._decode(
            {
                "absolute_path": "/x",
                "content_sha256": "0" * 64,
                "scope": "project",
                "decision": "approved",
                "recorded_at": s,
            }
        ),
        lambda: package_approvals._decode(
            {
                "registry": "npm",
                "name": "left-pad",
                "resolved_version": "1.3.0",
                "integrity": None,
                "decision": "approved",
                "recorded_at": s,
            }
        ),
    ]
    # Since Consiliency/pmcp#298 a task timestamp pmcp cannot use is dropped,
    # not raised: it is `None`, named in `unusable_fields`, and not kept.
    task = McpTaskInfo(task_id="t", created_at=s)
    assert task.created_at is None and task.unusable_fields == ["created_at"]
    assert not any(form in task.model_dump_json() for form in forbidden)
    for call in calls:
        with pytest.raises(Exception) as caught:
            call()
        error = caught.value
        for text in (exception_text(error), safe_traceback_text(error)):
            assert not any(form in text for form in forbidden), text
    try:
        calls[0]()
    except Exception as error:  # noqa: BLE001 -- inspected
        assert "could not parse timestamp trust store record" in str(error)


def test_parse_error_is_rendered_as_its_own_text() -> None:
    """A ParseError subclasses the parser's type but is not a parser's own
    error: exception_text keeps its source label and safe_exc_info keeps the
    traceback."""
    from pmcp.argument_errors import exception_text, safe_exc_info
    from pmcp.parsing import load_yaml

    try:
        load_yaml("k: !!int nope\n", source="guidance config")
    except Exception as error:  # noqa: BLE001 -- inspected
        assert (
            exception_text(error) == "could not parse YAML guidance config (ValueError)"
        )
        assert safe_exc_info(error) is error
    else:  # pragma: no cover
        raise AssertionError("did not raise")


def test_a_thread_prints_no_input(tmp_path: Path) -> None:
    """``threading.excepthook`` prints an uncaught thread exception, chain and
    all (rev 7, N3): the wrapper `import pmcp` installs prints it
    structurally, under the interpreter's header and qualified class name;
    with no stderr it prints nothing, like the default."""
    s = _FAMILIES["token"][1]
    script = (
        "import sys, threading, yaml, pmcp\n"
        "def run():\n"
        "    try:\n"
        "        yaml.safe_load(sys.argv[1])\n"
        "    except yaml.YAMLError as e:\n"
        "        raise RuntimeError('boom') from e\n"
        "t = threading.Thread(target=run, name='parser'); t.start(); t.join()\n"
        "if len(sys.argv) > 2:\n"
        "    sys.stderr = None\n"
        "    t = threading.Thread(target=run); t.start(); t.join()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, f"servers: [{s}}}"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert "Exception in thread parser:" in result.stderr, result.stderr
    assert "yaml.parser.ParserError: could not parse YAML (ParserError)" in (
        result.stderr
    ), result.stderr
    # A wrapper of a parse error prints what it chains, never its own
    # message (rev 18).
    assert (
        "RuntimeError: could not parse YAML (ParserError) at line 1, column"
        in result.stderr
    ), result.stderr
    assert "boom" not in result.stderr, result.stderr
    assert not any(form in result.stderr for form in _forbidden(s)), result.stderr
    silent = subprocess.run(
        [sys.executable, "-c", script, f"servers: [{s}}}", "no-stderr"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert silent.returncode == 0, silent.stderr
    assert silent.stderr.count("Exception in thread") == 1, silent.stderr


@pytest.mark.parametrize("origin", ["yaml", "pydantic"])
def test_an_uncaught_chain_prints_no_input(tmp_path: Path, origin: str) -> None:
    """``sys.excepthook`` prints an uncaught exception's chain. Since rev 7 a
    pmcp parse failure chains nothing, so this pins the wrapper on the
    chains that still can hold a value: a parser's own error raised outside
    ``pmcp.parsing`` and a pydantic ``ValidationError`` (the rev 7 mutation
    run found M34, "no excepthook", surviving without it)."""
    s = _FAMILIES["token"][1]
    raise_inner = {
        "yaml": "    import yaml\n    yaml.safe_load(sys.argv[1])\n",
        "pydantic": (
            "    import pydantic\n"
            "    pydantic.TypeAdapter(int).validate_python(sys.argv[1])\n"
        ),
    }[origin]
    script = (
        "import sys, pmcp\n"
        "try:\n" + raise_inner + "except Exception as e:\n"
        "    raise RuntimeError('boom') from e\n"
    )
    arg = f"servers: [{s}}}" if origin == "yaml" else s
    result = subprocess.run(
        [sys.executable, "-c", script, arg],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert result.returncode == 1, result.stderr
    assert "Traceback (most recent call last)" in result.stderr, result.stderr
    expected = {
        "yaml": "yaml.parser.ParserError: could not parse YAML (ParserError)",
        "pydantic": "pydantic_core._pydantic_core.ValidationError: 1 validation error",
    }[origin]
    assert expected in result.stderr, result.stderr
    # The wrapper's line is its class and what it chains, once (rev 18).
    wrapper = {
        "yaml": "\nRuntimeError: could not parse YAML (ParserError) at line 1, column",
        "pydantic": "\nRuntimeError: 1 validation error for int: $: must be an integer\n",
    }[origin]
    assert wrapper in result.stderr, result.stderr
    assert "boom" not in result.stderr, result.stderr
    assert "RuntimeError: RuntimeError" not in result.stderr, result.stderr
    assert not any(form in result.stderr for form in _forbidden(s)), result.stderr


def test_an_uncaught_error_with_no_stderr_prints_nothing(tmp_path: Path) -> None:
    """The default excepthook is silent when ``sys.stderr`` is None; so is
    pmcp's wrapper (rev 7 nit), instead of raising AttributeError."""
    script = (
        "import sys, yaml, pmcp\n"
        "try:\n"
        "    yaml.safe_load('servers: [x}')\n"
        "except yaml.YAMLError as e:\n"
        "    err = RuntimeError('boom')\n"
        "    err.__cause__ = e\n"
        "sys.stderr = None\n"
        "sys.excepthook(RuntimeError, err, None)\n"
        "sys.stdout.write('survived')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "survived"
    assert result.stderr == ""


def test_an_unparseable_auth_url_port_is_not_chained_into_a_traceback() -> None:
    """Consiliency/pmcp#348's URL rule refuses with fixed registry messages;
    the one refusal built on a parser error (urllib's, which quotes the
    port) is raised `from None`, so no traceback carries the value."""
    import traceback

    from pmcp.auth import check_auth_config, sanitize_public_auth_url

    s = "zqcanaryzq"
    for call in (
        lambda: sanitize_public_auth_url(f"https://a.example.com:{s}/"),
        lambda: check_auth_config(jwks_url=f"https://a.example.com:{s}/"),
        lambda: check_auth_config(metadata_url=f"https://a.example.com:{s}/"),
    ):
        with pytest.raises(ValueError) as caught:
            call()
        assert s not in "".join(traceback.format_exception(caught.value))


# --- rev 19: pmcp's own refusals keep their words (round-17 claude F001) ----
#
# Since rev 18 a wrapper whose chain holds a validation or parse error shows
# only that error's description. pmcp's own refusals are built from
# `exception_text` and raised after their handler, so they chain nothing and
# are shown whole: the file, the description and, for a discovered policy,
# the fail-closed refusal. Each case runs the real entry point.

_R19 = _FAMILIES["alpha"][0]
_R19_SCHEMA = (
    "1 validation error for GatewayPolicy: $.tools.allowlist: must be an array"
)


def _r19_case(case: str, root: Path) -> tuple[list[str], str]:
    """Set up `case` under `root`; return the CLI arguments and the exact
    stderr line the operator must see."""
    home = root / "home"
    s = _R19
    if case == "user-policy-yaml-schema":
        path = home / ".claude" / "gateway-policy.yaml"
        path.write_text(f"tools:\n  allowlist: {s}\n")
        return [], (
            f"Fatal error: Invalid policy file {path}: {_R19_SCHEMA}. "
            "Refusing to start rather than fall back to an unrestricted gateway."
        )
    if case == "user-policy-json-schema":
        path = home / ".claude" / "gateway-policy.json"
        path.write_text(json.dumps({"tools": {"allowlist": s}}))
        return [], (
            f"Fatal error: Invalid policy file {path}: {_R19_SCHEMA}. "
            "Refusing to start rather than fall back to an unrestricted gateway."
        )
    if case == "user-policy-root-list":
        path = home / ".claude" / "gateway-policy.yaml"
        path.write_text(f"- {s}\n")
        return [], (
            f"Fatal error: Invalid policy file {path}: policy root must be an "
            "object, got list. Refusing to start rather than fall back to an "
            "unrestricted gateway."
        )
    if case == "explicit-policy-yaml-schema":
        path = root / "p.yaml"
        path.write_text(f"tools:\n  allowlist: {s}\n")
        return ["--policy", str(path)], (
            f"Fatal error: Failed to load explicit policy {path}: {_R19_SCHEMA}"
        )
    if case == "explicit-policy-json-schema":
        path = root / "p.json"
        path.write_text(json.dumps({"tools": {"allowlist": s}}))
        return ["--policy", str(path)], (
            f"Fatal error: Failed to load explicit policy {path}: {_R19_SCHEMA}"
        )
    if case == "explicit-policy-yaml-parse":
        path = root / "p.yaml"
        path.write_text(f"tools: [{s}}}\n")
        return ["--policy", str(path)], (
            f"Fatal error: Failed to load explicit policy {path}: could not "
            "parse YAML policy file at line 1, column "
        )
    if case == "explicit-policy-missing":
        path = root / "absent.yaml"
        return ["--policy", str(path)], (
            f"Fatal error: Failed to load explicit policy {path}: [Errno 2] "
        )
    if case == "trust-store-parse":
        path = home / ".config" / "pmcp" / "trust.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"records": [' + s + "}")
        path.chmod(0o600)
        return ["trust", "list"], (
            f"Error: Cannot parse trust store {path.resolve()}: could not parse "
            "JSON trust store at line 1, column 14 (JSONDecodeError)"
        )
    assert case == "auth-jwks-url", case
    return [
        "--transport",
        "http",
        "--auth-mode",
        "resource-server",
        "--oauth-jwks-url",
        f"https://a.example.com:{s}/",
    ], "error: Invalid public auth URL."


@pytest.mark.parametrize(
    "case",
    [
        "user-policy-yaml-schema",
        "user-policy-json-schema",
        "user-policy-root-list",
        "explicit-policy-yaml-schema",
        "explicit-policy-json-schema",
        "explicit-policy-yaml-parse",
        "explicit-policy-missing",
        "trust-store-parse",
        "auth-jwks-url",
    ],
)
def test_a_startup_refusal_names_the_file_and_the_refusal(
    tmp_path: Path, case: str
) -> None:
    """The real CLI: a refusal reads as pmcp wrote it -- path, description and
    consequence -- and carries nothing of the value."""
    (tmp_path / "home" / ".claude").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    args, expected = _r19_case(case, tmp_path)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PMCP_", "npm_config_", "pnpm_config_"))
    }
    env["HOME"] = str(tmp_path / "home")
    result = subprocess.run(
        [sys.executable, "-c", "from pmcp.cli import main; main()", *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=project,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode != 0, result.stderr
    lines = result.stderr.splitlines()
    assert any(line.startswith(expected) for line in lines), result.stderr
    assert not any(form in result.stderr for form in _forbidden(_R19)), result.stderr


@pytest.mark.parametrize("scope", ["explicit", "user"])
def test_a_policy_refusal_still_names_the_file_and_the_refusal(
    scope: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-17 claude F001's falsifier, as filed: every rendering of the
    refusal -- `exception_text` and the traceback's last line -- names the
    file, and the discovered policy's says it refuses to start."""
    from pmcp.argument_errors import exception_text, safe_traceback_text
    from pmcp.policy.policy import PolicyManager

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    policy = home / ".claude" / "gateway-policy.yaml"
    policy.write_text("tools:\n  allowlist: 5\n")

    with pytest.raises(ValueError) as raised:
        PolicyManager(policy if scope == "explicit" else None)
    text = exception_text(raised.value)
    last = safe_traceback_text(raised.value).rstrip("\n").rsplit("\n", 1)[-1]
    for rendered in (text, last):
        assert str(policy) in rendered, rendered
        if scope == "user":
            assert "Refusing to start" in rendered, rendered


def test_a_versioned_package_pattern_is_refused_without_its_value(
    tmp_path: Path,
) -> None:
    """A policy's `pkg@1.2.3` package pattern is refused by its list and a
    fixed reason, never with the operator's entry (rev 20: the validator
    raised `ValueError(f"package pattern {entry!r} names a version")`)."""
    from pmcp.argument_errors import exception_text
    from pmcp.policy.policy import PolicyManager

    s = _R19
    path = tmp_path / "p.yaml"
    path.write_text(f"packages:\n  denylist: ['ok-*', '{s}@1.2.3']\n")
    with pytest.raises(ValueError) as raised:
        PolicyManager(path)
    text = exception_text(raised.value)
    assert text == (
        f"Failed to load explicit policy {path}: 1 validation error for "
        "GatewayPolicy: $.packages.denylist: a package pattern names a version; "
        "package patterns match the package name only"
    ), text
    assert not any(form in text for form in _forbidden(s)), text


# --- rev 22: response decoding is a parse of downstream content -------------
#
# Round-20 codex F001: aiohttp's `ContentTypeError` (raised by `resp.json()`)
# names the rejected MIME type, and the version lookups logged it through
# `exception_text` unchanged, because the type was not registered.

#: Every top-level module pmcp imports, split into the HTTP clients and the
#: rest (rev 23). Derived from the AST and pinned exactly: a new import fails
#: `test_every_imported_module_is_classified` until it is classified. A client
#: is a module whose calls send an HTTP request and parse the response.
_HTTP_CLIENT_IMPORTS: dict[str, str] = {
    "aiohttp": "registry, version and JWKS fetches",
    "httpx": "the CLI's health probes",
    "httpx2": "remote MCP servers (pmcp's own AsyncClient)",
    "urllib": "urllib.request openers: auth metadata, npm packuments, feedback",
    "mcp": "the SDK's remote clients, over httpx2",
}
_NOT_HTTP_CLIENTS = frozenset(
    {
        # The standard library, apart from `urllib`.
        "__future__",
        "argparse",
        "ast",
        "asyncio",
        "bisect",
        "collections",
        "contextlib",
        "copy",
        "dataclasses",
        "datetime",
        "enum",
        "errno",
        "fcntl",
        "fnmatch",
        "functools",
        "getpass",
        "hashlib",
        "hmac",
        # `http.client` sends nothing for pmcp: argument_errors recognises
        # urllib's refused tunnel by its code (rev 24); it is a transport.
        "http",
        "importlib",
        "inspect",
        "ipaddress",
        "itertools",
        "json",
        "logging",
        "math",
        "msvcrt",
        "os",
        "pathlib",
        "pickle",
        "pkgutil",
        "platform",
        "queue",
        "random",
        "re",
        "resource",
        "shlex",
        "shutil",
        "signal",
        "site",
        "socket",
        "stat",
        "string",
        "subprocess",
        "sys",
        "tempfile",
        "threading",
        "time",
        "tomllib",
        "traceback",
        "types",
        "typing",
        "uuid",
        # Third-party, none of which pmcp uses to send a request: async
        # primitives, parsers and models, the server side, metrics.
        "anyio",
        "dotenv",
        "jsonschema",
        "jwt",
        "mcp_types",
        "packaging",
        "prometheus_client",
        "pydantic",
        "pydantic_core",
        "semver",
        "starlette",
        "uvicorn",
        "yaml",
    }
)

#: The transports and parsers under those clients, whose exceptions surface
#: through them: derived in `test_every_client_transport_is_registered`.
_TRANSPORTS = {"http.client", "httpcore", "httpcore2", "h11"}


def _imported_top_modules() -> set[str]:
    names: set[str] = set()
    for path in sorted(_SRC.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module.split(".")[0])
    return {name for name in names if name != "pmcp"}


def test_every_imported_module_is_classified() -> None:
    """Every module pmcp imports is an HTTP client or is not, exactly."""
    imported = _imported_top_modules()
    classified = set(_HTTP_CLIENT_IMPORTS) | _NOT_HTTP_CLIENTS
    assert imported == classified, (
        sorted(imported - classified),
        sorted(classified - imported),
    )


def _distribution_requirements(name: str) -> set[str]:
    """The installed distributions `name` requires (markers evaluated)."""
    import importlib.metadata
    import re as _re

    from packaging.requirements import Requirement

    found: set[str] = set()
    for line in importlib.metadata.requires(name) or []:
        requirement = Requirement(line)
        if requirement.marker is not None and not requirement.marker.evaluate(
            {"extra": ""}
        ):
            continue
        found.add(_re.sub(r"[-_.]+", "_", requirement.name).lower())
    return found


def test_every_client_transport_is_registered() -> None:
    """The transports under each client -- the installed requirements, at
    any depth, that define a `*ProtocolError` -- and `http.client` under
    `urllib.request` are all in the registry."""
    import importlib

    from pmcp.argument_errors import HTTP_RESPONSE_ERRORS

    pending = [name for name in _HTTP_CLIENT_IMPORTS if name != "urllib"]
    seen: set[str] = set()
    transports: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        if name not in _HTTP_CLIENT_IMPORTS and any(
            isinstance(value, type)
            and issubclass(value, BaseException)
            and "ProtocolError" in attr
            for attr, value in vars(module).items()
        ):
            transports.add(name)
        try:
            pending.extend(_distribution_requirements(name))
        except Exception:  # noqa: BLE001 -- a module with no distribution
            continue
    import urllib.request

    assert urllib.request.http.client is importlib.import_module("http.client")
    transports.add("http.client")
    assert transports == _TRANSPORTS, transports
    assert _TRANSPORTS <= set(HTTP_RESPONSE_ERRORS), set(HTTP_RESPONSE_ERRORS)
    for client in ("aiohttp", "httpx", "httpx2"):
        assert client in HTTP_RESPONSE_ERRORS, client


def _defined_exceptions(module_name: str) -> dict[str, type]:
    import importlib

    module = importlib.import_module(module_name)
    root = module_name.split(".")[0]
    defined = {
        attr: value
        for attr, value in vars(module).items()
        if isinstance(value, type)
        and issubclass(value, BaseException)
        and str(value.__module__).split(".")[0] == root
        and not attr.startswith("_")
    }
    # And every subclass of those, at any depth, wherever the package
    # defines it (rev 24: aiohttp's `UnixClientConnectorError` is not
    # exported).
    pending = list(defined.values())
    while pending:
        for sub in pending.pop().__subclasses__():
            if (
                str(sub.__module__).split(".")[0] == root
                and sub not in defined.values()
            ):
                defined.setdefault(sub.__name__, sub)
                pending.append(sub)
    return defined


def test_every_http_client_exception_is_registered() -> None:
    """Every exception class each HTTP client and transport module defines
    -- exported, or a subclass of one at any depth -- is registered (rev 25,
    round-23 ruling). There is no value-free list: text built in C or
    through a variable class (httpcore's `to_exc`, httpx's `mapped_exc`)
    cannot be proved value-free from the source, so none is assumed."""
    from pmcp.argument_errors import HTTP_RESPONSE_ERRORS, _value_bearing_types

    registered = _value_bearing_types()
    assert set(HTTP_RESPONSE_ERRORS) >= _TRANSPORTS | {"aiohttp", "httpx", "httpx2"}
    unregistered = sorted(
        (module_name, attr)
        for module_name in HTTP_RESPONSE_ERRORS
        for attr, value in _defined_exceptions(module_name).items()
        if not issubclass(value, registered)
    )
    assert not unregistered, unregistered
    # Every module that defines exceptions pmcp's clients raise is listed.
    assert set(HTTP_RESPONSE_ERRORS) == {
        "aiohttp",
        "aiohttp.http_exceptions",
        "httpx",
        "httpx2",
        "httpcore",
        "httpcore2",
        "h11",
        "http.client",
        "urllib.error",
    }


def test_every_response_decode_site_is_inside_a_handler() -> None:
    """Each `response-decode` site's `.json()`/`.text()` call sits in a `try`
    whose handler catches it (`Exception`, or a base of the decode errors)."""
    sites = _parse_sites()
    for site, (kind, _library) in _EXEMPT.items():
        if kind != "response-decode":
            continue
        rel, function = site.split("::")
        tree = ast.parse((_SRC / rel).read_text())
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        lines = {int(item.split(":")[0]) for item in sites[site]}
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("json", "text")
            and node.lineno in lines
        ]
        assert calls, site
        for call in calls:
            node: ast.AST = call
            caught = False
            while node in parents:
                parent = parents[node]
                if isinstance(parent, ast.Try) and node in parent.body:
                    caught = any(
                        h.type is not None
                        and ast.unparse(h.type) in ("Exception", "BaseException")
                        for h in parent.handlers
                    )
                    if caught:
                        break
                node = parent
            assert caught, (site, call.lineno)


def test_dotenv_never_raises_and_logs_no_value(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The `dotenv` kind's reason, measured: malformed input does not raise,
    and what python-dotenv logs carries no part of it."""
    from dotenv import dotenv_values

    caplog.set_level(logging.DEBUG)
    s = _FAMILIES["hex"][0]
    path = tmp_path / ".env"
    path.write_text(f"GOOD=1\n{s} = = '\nexport {s}\n'{s}\n")
    values = dotenv_values(path)
    assert "GOOD" in values
    logged = "\n".join(_record_text(record) for record in caplog.records)
    assert not any(form in logged for form in _forbidden(s)), logged


def _aiohttp_response(content_type: str, body: bytes) -> Any:
    import asyncio
    from types import SimpleNamespace

    import aiohttp
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    url = URL("https://registry.example/probe")
    response = aiohttp.ClientResponse(
        "GET",
        url,
        writer=None,
        continue100=None,
        timer=None,  # type: ignore[arg-type]
        request_info=aiohttp.RequestInfo(
            url, "GET", CIMultiDictProxy(CIMultiDict()), url
        ),
        traces=[],
        loop=asyncio.get_running_loop(),
        session=None,  # type: ignore[arg-type]
        stream_writer=SimpleNamespace(output_size=0),  # type: ignore[arg-type]
    )
    response.status = 200
    response._headers = CIMultiDictProxy(CIMultiDict({"Content-Type": content_type}))
    response._body = body
    return response


@pytest.mark.parametrize(
    "lookup",
    ["get_npm_version", "get_pypi_version", "get_cargo_version", "get_docker_version"],
)
@pytest.mark.parametrize("shape", ["content-type", "undecodable-body"])
def test_a_rejected_response_body_is_not_logged(
    lookup: str,
    shape: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-20 codex F001's falsifier, over all four lookups: a response
    whose MIME type aiohttp rejects (`ContentTypeError`), or whose body will
    not decode (`UnicodeDecodeError`), is logged by its class only. The
    response is aiohttp's own, with no socket opened."""
    import asyncio
    from unittest.mock import MagicMock

    import aiohttp

    from pmcp.manifest import version_checker

    sentinel = "x-rejected-private-value-9137"
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(version_checker, "_version_cache", {})

    async def exercise() -> None:
        if shape == "content-type":
            response = _aiohttp_response(f"text/{sentinel}", b"{}")
            with pytest.raises(aiohttp.ContentTypeError) as rejected:
                await response.json()
        else:
            response = _aiohttp_response(
                "application/json; charset=utf-8",
                b'{"' + sentinel.encode() + b'\xff\xfe": 1}',
            )
            with pytest.raises(UnicodeDecodeError) as rejected:
                await response.json()
        assert sentinel in repr(rejected.value) or sentinel.encode() in getattr(
            rejected.value, "object", b""
        )
        session = MagicMock()
        session.__aenter__.return_value = session
        session.get.return_value.__aenter__.return_value = response
        monkeypatch.setattr(
            version_checker.aiohttp, "ClientSession", lambda *a, **k: session
        )
        assert await getattr(version_checker, lookup)("probe-name") is None

    asyncio.run(exercise())
    logged = "\n".join(_record_text(record) for record in caplog.records)
    assert sentinel not in logged, logged
    # By class only: rev 25's one phrase for an HTTP client's error, and the
    # codec's for an undecodable body.
    described = (
        "an HTTP request failed (ContentTypeError"
        if shape == "content-type"
        else "could not decode utf-8 text (UnicodeDecodeError)"
    )
    assert any(described in record.getMessage() for record in caplog.records), logged


# --- rev 23: every HTTP client response pmcp rejects, at every call site ----
#
# Round 21 (claude, grok, codex F001): each client's protocol parser quotes
# the response bytes it rejects -- `illegal status line: bytearray(b'...')`,
# `Invalid character in chunk size: b'...'`, `BadStatusLine: HTTP/1.1 ...`.
# The grid is every malformed-response shape x every call site pmcp has
# (pinned from the AST), each driven end to end against a local server, with
# the sentinel checked in the result, `gateway.health`'s error, every log
# record at DEBUG, and every exception's text and traceback.

#: Plain words, so that pmcp's secret redaction (an opaque run of letters and
#: digits reads as a token) cannot mask a leak by coincidence (rev 24: it
#: masked `RESPONSESENTINELQ9...` in `last_error`).
_GRID_S = "rejectedresponsesentinelvaluefromtheserverzulu"


def _http_shapes(s: str) -> dict[str, bytes]:
    body = ('{"' + s + '": 1}').encode()
    return {
        "status-line": f"HTTP/1.1 2{s} OK\r\nContent-Length: 2\r\n\r\n{{}}".encode(),
        "header-line": f"HTTP/1.1 200 OK\r\n{s}\r\nContent-Length: 2\r\n\r\n{{}}".encode(),
        "chunk-size": (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
            b"Content-Type: application/json\r\n\r\n" + s.encode() + b"\r\n0\r\n\r\n"
        ),
        "truncated-body": (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: 4096\r\n\r\n" + body[:-3]
        ),
        "undecodable-body": (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n"
            + f"Content-Length: {len(body) + 2}\r\n\r\n".encode()
            + body[:-4]
            + b"\xff\xfe"
            + body[-4:]
        ),
        "reason-phrase": f"HTTP/1.1 500 {s}\r\nContent-Length: 0\r\n\r\n".encode(),
        # A redirect the client cannot follow, or follows to a host that
        # does not resolve (`.invalid`, RFC 6761): the scheme or host is the
        # response's (rev 24).
        "redirect-scheme": (
            f"HTTP/1.1 302 Found\r\nLocation: {s.lower()}://x/\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode(),
        "redirect-host": (
            f"HTTP/1.1 302 Found\r\nLocation: http://{s.lower()}.invalid/\r\n"
            "Content-Length: 0\r\n\r\n"
        ).encode(),
        # A `Location` each client's URL parser rejects (rev 26, round-24
        # codex F001: urllib parses it before pmcp's no-redirect handler,
        # and `ipaddress` raises a bare `ValueError` quoting the host).
        **{
            f"redirect-{kind}": (
                b"HTTP/1.1 302 Found\r\nLocation: "
                + location
                + b"\r\nContent-Length: 0\r\n\r\n"
            )
            for kind, location in (
                ("ipv6", f"https://[{s}]/".encode()),
                ("port", f"http://h.invalid:{s}/".encode()),
                ("authority", f"http://[::1]{s}/".encode()),
                ("encoding", b"http://h\xff" + s.encode() + b".invalid/"),
            )
        },
    }


#: The request lines the grid's server received, and the path token of the
#: site being driven.
_GRID_REQUESTS: list[bytes] = []
_GRID_TOKEN: list[str] = ["none"]
#: The URL every client is sent to for the site being driven: the local
#: server, or (proxy rows, rev 24) a host only the proxy sees.
_GRID_URL: list[str] = ["http://127.0.0.1:9/none"]


def _serve_once_per_connection(payload: bytes) -> tuple[Any, int]:
    import socket
    import threading

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)

    def loop() -> None:
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            try:
                conn.settimeout(5)
                try:
                    _GRID_REQUESTS.append(conn.recv(65536))
                except OSError:
                    pass
                conn.sendall(payload)
            except OSError:
                pass
            finally:
                conn.close()

    threading.Thread(target=loop, daemon=True).start()
    return listener, listener.getsockname()[1]


#: Every function in `src/pmcp` that sends an HTTP request -- found by the
#: client calls in it -- and the grid's driver for it.
_HTTP_CALL_PATTERNS = (
    "aiohttp.ClientSession",
    "httpx.AsyncClient",
    "httpx2.AsyncClient",
    "sse_client",
    "streamable_http_client",
    "urlopen",
    "_OPENER.open",
)


def _http_call_site_clients() -> dict[str, set[str]]:
    """Each site, and the client calls it makes."""
    sites: dict[str, set[str]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        rel = path.relative_to(_SRC).as_posix()
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and ast.unparse(node.func) in _HTTP_CALL_PATTERNS
            ):
                site = f"{rel}::{_owner(tree, node.lineno)}"
                sites.setdefault(site, set()).add(ast.unparse(node.func))
    return sites


def _http_call_sites() -> set[str]:
    return set(_http_call_site_clients())


async def _drive_version(name: str, port: int) -> Any:
    from pmcp.manifest import version_checker

    version_checker._version_cache.clear()
    return await getattr(version_checker, name)("probe-name")


async def _drive_registry(port: int) -> Any:
    from pmcp.manifest.registry import _fetch_registry_servers_uncached

    return await _fetch_registry_servers_uncached(
        _GRID_URL[0],
        timeout=10,
        max_pages=1,
        max_response_bytes=1 << 20,
    )


async def _drive_jwks(port: int) -> Any:
    from pmcp.auth import AsyncJWKS

    return await AsyncJWKS("https://1.1.1.1/jwks.json")._fetch()


async def _drive_metadata(port: int) -> Any:
    from pmcp.auth import fetch_json_metadata

    return fetch_json_metadata("https://1.1.1.1/.well-known/oauth")


async def _drive_packument(port: int) -> Any:
    from pmcp.manifest.package_identity import _fetch_packument

    return _fetch_packument("probe-name")


async def _drive_feedback_probe(port: int) -> Any:
    import time

    from pmcp.feedback_egress import _probe_repository_visibility

    return _probe_repository_visibility("o/r", "token", time.monotonic() + 600)


async def _drive_feedback_submit(port: int) -> Any:
    import time

    from pmcp.feedback_egress import FeedbackProgress, submit_feedback_issue

    return submit_feedback_issue(
        repository="o/r",
        token="token",
        title="a title",
        body="a body",
        labels=[],
        deadline=time.monotonic() + 600,
        progress=FeedbackProgress(),
    )


async def _drive_sse_probe(port: int) -> Any:
    from pmcp.cli import _probe_sse_endpoint

    return await _probe_sse_endpoint(_GRID_URL[0], 10)


async def _drive_http_probe(port: int) -> Any:
    from pmcp.cli import _probe_http_health

    return await _probe_http_health(10)


async def _drive_remote(transport: str, port: int) -> Any:
    from pmcp.client.manager import ClientManager
    from pmcp.types import RemoteMcpServerConfig, ResolvedServerConfig

    config = ResolvedServerConfig(
        name="probe",
        source="custom",
        config=RemoteMcpServerConfig(type=transport, url=_GRID_URL[0]),
    )
    manager = ClientManager()
    errors = await asyncio.wait_for(manager.connect_server(config, retry=False), 30)
    # `gateway.health` reports each server's `last_error`.
    return errors, [status.last_error for status in manager.get_all_server_statuses()]


_HTTP_SITE_DRIVERS: dict[str, list[Any]] = {
    "manifest/version_checker.py::get_npm_version": [
        functools.partial(_drive_version, "get_npm_version")
    ],
    "manifest/version_checker.py::get_pypi_version": [
        functools.partial(_drive_version, "get_pypi_version")
    ],
    "manifest/version_checker.py::get_cargo_version": [
        functools.partial(_drive_version, "get_cargo_version")
    ],
    "manifest/version_checker.py::get_docker_version": [
        functools.partial(_drive_version, "get_docker_version")
    ],
    "manifest/registry.py::_fetch_registry_servers_uncached": [_drive_registry],
    "auth.py::_fetch": [_drive_jwks],
    "auth.py::fetch_json_metadata": [_drive_metadata],
    "manifest/package_identity.py::_fetch_packument": [_drive_packument],
    "feedback_egress.py::_probe_repository_visibility": [_drive_feedback_probe],
    "feedback_egress.py::submit_feedback_issue": [_drive_feedback_submit],
    "cli.py::_probe_sse_endpoint": [_drive_sse_probe],
    "cli.py::_probe_http_health": [_drive_http_probe],
    "client/manager.py::_connect_streamable_http": [
        functools.partial(_drive_remote, "http")
    ],
    "client/manager.py::_connect_sse": [functools.partial(_drive_remote, "sse")],
}


def test_every_http_call_site_has_a_grid_driver() -> None:
    """The grid's sites are exactly the functions that send a request."""
    assert _http_call_sites() == set(_HTTP_SITE_DRIVERS), sorted(
        _http_call_sites() ^ set(_HTTP_SITE_DRIVERS)
    )


def _redirect_every_client(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    """Send every request any client makes to the local server, unchanged in
    every other way: the client's own parser reads the response."""
    import urllib.request

    import aiohttp

    import pmcp.cli as cli

    def local() -> str:
        return _GRID_URL[0]

    original_request = aiohttp.ClientSession._request

    def aiohttp_request(self: Any, method: str, str_or_url: Any, **kwargs: Any) -> Any:
        return original_request(self, method, local(), **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "_request", aiohttp_request)

    def redirect(opener: Any) -> None:
        # On the instance, from the opener's own class: robust to a test that
        # left an instance `open` behind or reloaded `urllib.request`.
        original_open = type(opener).open

        def urllib_open(fullurl: Any, *args: Any, **kwargs: Any) -> Any:
            if isinstance(fullurl, urllib.request.Request):
                fullurl.full_url = local()
            else:
                fullurl = local()
            return original_open(opener, fullurl, *args, **kwargs)

        monkeypatch.setattr(opener, "open", urllib_open)

    import pmcp.auth as auth
    import pmcp.feedback_egress as feedback_egress
    from pmcp.manifest import package_identity

    # conftest's `_no_live_npm_registry` replaced the packument opener's
    # `open`; here it is replaced again, by the redirect.
    for opener in (
        package_identity._OPENER,
        feedback_egress._OPENER,
        auth._NO_REDIRECT_OPENER,
    ):
        redirect(opener)
    # `pmcp.auth.urlopen` is the opener's `open` bound at import.
    monkeypatch.setattr(auth, "urlopen", auth._NO_REDIRECT_OPENER.open)
    monkeypatch.setattr(cli, "_get_gateway_health_url", local)


@pytest.mark.parametrize("shape", sorted(_http_shapes("x")))
def test_no_rejected_http_response_reaches_any_output(
    shape: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every malformed-response shape x every HTTP call site, end to end:
    no form of the sentinel in the result, `gateway.health`'s error, any log
    record at DEBUG, or any escaping exception's text or traceback."""
    import pmcp  # noqa: F401 - installs the scrubbers
    from pmcp.argument_errors import exception_text, safe_traceback_text

    s = _GRID_S
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HOME", str(tmp_path))
    listener, port = _serve_once_per_connection(_http_shapes(s)[shape])
    _redirect_every_client(monkeypatch, port)
    failures = []
    try:
        for site, drivers in sorted(_HTTP_SITE_DRIVERS.items()):
            for driver in drivers:
                caplog.clear()
                token = f"site{len(_GRID_REQUESTS)}x{abs(hash(site)) % 10**8}"
                _GRID_TOKEN[0] = token
                _GRID_URL[0] = f"http://127.0.0.1:{port}/{token}"
                try:
                    outcome = repr(asyncio.run(driver(port)))
                except Exception as error:  # noqa: BLE001 -- inspected
                    outcome = exception_text(error) + safe_traceback_text(error)
                if not any(token.encode() in request for request in _GRID_REQUESTS):
                    # No vacuous pass: the site's client read the response.
                    failures.append((site, "never connected", outcome[:300]))
                logs = "\n".join(_record_text(record) for record in caplog.records)
                for surface, text in (("result", outcome), ("log", logs)):
                    if any(form in text for form in _forbidden_any_case(s)):
                        failures.append((site, surface, text[:300]))
    finally:
        listener.close()
    assert not failures, failures


# --- a proxy's refusal (rev 24, round-22 claude F001) -------------------------
#
# A proxy that refuses a request answers with a status line whose reason
# phrase the clients quote: httpcore's `ProxyError("407 <reason>")`, urllib's
# `OSError("Tunnel connection failed: 407 <reason>")`. Every site whose client
# takes its proxy from the environment is driven through a refusing proxy:
# over https (a CONNECT tunnel) and http (an absolute-form request), each
# with a 407 and a 502 whose reason phrase is the sentinel.

#: Any refusal status carries a reason phrase (round 22 grok: a 403).
_PROXY_STATUSES = (403, 407, 502)


def _proxied_sites() -> set[str]:
    """The sites whose client honours `HTTP(S)_PROXY`: every one but
    aiohttp's, whose `trust_env` is off unless set
    (`test_no_aiohttp_site_takes_a_proxy`)."""
    return {
        site
        for site, clients in _http_call_site_clients().items()
        if clients != {"aiohttp.ClientSession"}
    }


def test_no_aiohttp_site_takes_a_proxy() -> None:
    """aiohttp reads no proxy from the environment unless `trust_env=True`,
    and pmcp passes neither that nor `proxy=` anywhere."""
    import inspect

    import aiohttp

    assert (
        inspect.signature(aiohttp.ClientSession.__init__)
        .parameters["trust_env"]
        .default
        is False
    )
    passed = []
    for path in sorted(_SRC.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg in ("trust_env", "proxy"):
                        passed.append(f"{path.name}:{node.lineno}")
    assert not passed, passed
    aiohttp_only = {
        site
        for site, clients in _http_call_site_clients().items()
        if clients == {"aiohttp.ClientSession"}
    }
    assert aiohttp_only and not aiohttp_only & _proxied_sites()


def _proxy_every_opener(monkeypatch: pytest.MonkeyPatch, proxy: str) -> None:
    """What each urllib opener's own `ProxyHandler` does when `HTTP(S)_PROXY`
    is set at import (it reads the environment once, when `build_opener`
    runs): proxy its http and https requests. Each opener is built by
    `build_opener` with no `ProxyHandler` of pmcp's
    (`test_every_urllib_opener_takes_the_environment_proxy`)."""
    import urllib.request

    import pmcp.auth as auth
    import pmcp.feedback_egress as feedback_egress
    from pmcp.manifest import package_identity

    for opener in (
        package_identity._OPENER,
        feedback_egress._OPENER,
        auth._NO_REDIRECT_OPENER,
    ):
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        handler.add_parent(opener)
        for scheme in ("http", "https"):
            monkeypatch.setitem(
                opener.handle_open,
                scheme,
                [handler, *opener.handle_open.get(scheme, [])],
            )


def test_every_urllib_opener_takes_the_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pmcp builds every urllib opener with `build_opener` and passes no
    `ProxyHandler`, so the default one takes `HTTP(S)_PROXY` from the
    environment, as the proxy grid models."""
    import urllib.request

    built = []
    for path in sorted(_SRC.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith(
                "build_opener"
            ):
                built.append(path.name)
                assert "ProxyHandler" not in ast.unparse(node), path.name
    assert sorted(built) == ["auth.py", "feedback_egress.py", "package_identity.py"]
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    assert any(
        isinstance(handler, urllib.request.ProxyHandler)
        for handler in urllib.request.build_opener().handlers
    )


@pytest.mark.parametrize("status", _PROXY_STATUSES)
@pytest.mark.parametrize("scheme", ["https", "http"])
def test_no_proxy_refusal_reaches_any_output(
    scheme: str,
    status: int,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every site that takes a proxy, through a proxy that refuses with the
    sentinel as its reason phrase: the grid's oracle."""
    import pmcp  # noqa: F401 - installs the scrubbers
    from pmcp.argument_errors import exception_text, safe_traceback_text

    s = _GRID_S
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HOME", str(tmp_path))
    listener, port = _serve_once_per_connection(
        f"HTTP/1.1 {status} {s}\r\nContent-Length: 0\r\n\r\n".encode()
    )
    proxy = f"http://127.0.0.1:{port}"
    for name in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, proxy)
    _redirect_every_client(monkeypatch, port)
    _proxy_every_opener(monkeypatch, proxy)
    failures = []
    try:
        for site, drivers in sorted(_HTTP_SITE_DRIVERS.items()):
            if site not in _proxied_sites():
                continue
            for driver in drivers:
                caplog.clear()
                token = f"proxy{len(_GRID_REQUESTS)}x{abs(hash(site)) % 10**8}"
                _GRID_TOKEN[0] = token
                _GRID_URL[0] = f"{scheme}://{token}.invalid/{token}"
                try:
                    outcome = repr(asyncio.run(driver(port)))
                except Exception as error:  # noqa: BLE001 -- inspected
                    outcome = exception_text(error) + safe_traceback_text(error)
                if not any(token.encode() in request for request in _GRID_REQUESTS):
                    failures.append((site, "never reached the proxy", outcome[:300]))
                logs = "\n".join(_record_text(record) for record in caplog.records)
                for surface, text in (("result", outcome), ("log", logs)):
                    if any(form in text for form in _forbidden_any_case(s)):
                        failures.append((site, surface, text[:300]))
    finally:
        listener.close()
    assert not failures, failures


# --- the links beneath a registered error (rev 24, round-22 claude N1) --------


def test_a_link_beneath_a_registered_error_prints_its_class_alone() -> None:
    """A parser that raises inside its own `except` leaves the rejected bytes
    in the context: every link of a chain that holds a registered error is
    described or printed as its class alone, beneath it, above it and beside
    it in a group."""
    import http.client

    from pmcp.argument_errors import exception_text, safe_traceback_text

    s = _GRID_S

    def registered_over_a_value() -> BaseException:
        try:
            try:
                int(s)
            except ValueError:
                raise http.client.BadStatusLine(s)  # noqa: B904 - the shape
        except http.client.BadStatusLine as error:
            return error

    beneath = registered_over_a_value()
    try:
        raise RuntimeError(f"wrapped {s}") from beneath
    except RuntimeError as error:
        above = error
    from tests.test_argument_error_echo import _exception_group

    group = _exception_group()("group", [beneath, ValueError(s)])
    for error in (beneath, above, group):
        text = exception_text(error) + safe_traceback_text(error)
        assert not any(form in text for form in _forbidden_any_case(s)), text
    assert "\nValueError\n" in safe_traceback_text(beneath)


def _client_calls() -> dict[str, Callable[[str], Any]]:
    """Each client pmcp uses, called on its own: a GET that reads and decodes
    the body and raises on an error status."""
    import urllib.request

    def via_urllib(url: str) -> Any:
        with urllib.request.build_opener().open(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def via_httpx(module_name: str) -> Callable[[str], Any]:
        def call(url: str) -> Any:
            import importlib

            module = importlib.import_module(module_name)

            async def run() -> Any:
                async with module.AsyncClient(
                    timeout=10, follow_redirects=True
                ) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.json()

            return asyncio.run(run())

        return call

    def via_aiohttp(url: str) -> Any:
        import aiohttp

        async def run() -> Any:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    response.raise_for_status()
                    return await response.json(content_type=None)

        return asyncio.run(run())

    return {
        "urllib": via_urllib,
        "httpx": via_httpx("httpx"),
        "httpx2": via_httpx("httpx2"),
        "aiohttp": via_aiohttp,
    }


@pytest.mark.parametrize("client", sorted(_client_calls()))
@pytest.mark.parametrize("shape", sorted(_http_shapes("x")))
def test_no_client_error_prints_a_rejected_response(
    shape: str, client: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Each client against each shape, the error caught and inspected
    directly: its text, its traceback (every link, beneath the registered
    error included) and a log record carrying it as `exc_info`."""
    import pmcp  # noqa: F401 - installs the scrubbers
    from pmcp.argument_errors import exception_text, safe_traceback_text

    s = _GRID_S
    caplog.set_level(logging.DEBUG)
    listener, port = _serve_once_per_connection(_http_shapes(s)[shape])
    try:
        try:
            result = _client_calls()[client](f"http://127.0.0.1:{port}/")
        except Exception as error:  # noqa: BLE001 -- inspected
            logging.getLogger("pmcp.test").error("failed", exc_info=error)
            outcome = exception_text(error) + safe_traceback_text(error)
        else:
            outcome = repr(result)
    finally:
        listener.close()
    logs = "\n".join(_record_text(record) for record in caplog.records)
    for surface, text in (("error", outcome), ("log", logs)):
        assert not any(form in text for form in _forbidden_any_case(s)), (
            surface,
            text[:400],
        )


def _proxied_client_calls(proxy: str) -> dict[str, Callable[[str], Any]]:
    """Each client pmcp uses, through `proxy`: urllib's opener and httpx's
    clients take it from the environment, aiohttp from `proxy=` (pmcp
    passes none; this covers its class)."""
    import urllib.request

    def via_urllib(url: str) -> Any:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        with opener.open(url, timeout=10) as response:
            return response.read()

    def via_httpx(module_name: str) -> Callable[[str], Any]:
        def call(url: str) -> Any:
            import importlib

            module = importlib.import_module(module_name)

            async def run() -> Any:
                async with module.AsyncClient(timeout=10, proxy=proxy) as client:
                    return (await client.get(url)).raise_for_status()

            return asyncio.run(run())

        return call

    def via_aiohttp(url: str) -> Any:
        import aiohttp

        async def run() -> Any:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=proxy) as response:
                    response.raise_for_status()
                    return await response.read()

        return asyncio.run(run())

    return {
        "urllib": via_urllib,
        "httpx": via_httpx("httpx"),
        "httpx2": via_httpx("httpx2"),
        "aiohttp": via_aiohttp,
    }


@pytest.mark.parametrize("client", ["aiohttp", "httpx", "httpx2", "urllib"])
@pytest.mark.parametrize("status", _PROXY_STATUSES)
@pytest.mark.parametrize("scheme", ["https", "http"])
def test_no_proxy_refusal_reaches_any_renderer(
    scheme: str, status: int, client: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Each client through a refusing proxy, the error caught and given to
    every renderer a site uses: `exception_text`, `safe_traceback_text`,
    `describe_exception` (bare and in a group, `gateway.health`'s shape),
    `sanitize_auth_diagnostic`, and a log record carrying it (round 22
    grok and codex)."""
    import pmcp  # noqa: F401 - installs the scrubbers
    from pmcp.argument_errors import exception_text, safe_traceback_text
    from pmcp.auth import sanitize_auth_diagnostic
    from pmcp.client.manager import describe_exception
    from tests.test_argument_error_echo import _exception_group

    s = _GRID_S
    caplog.set_level(logging.DEBUG)
    listener, port = _serve_once_per_connection(
        f"HTTP/1.1 {status} {s}\r\nContent-Length: 0\r\n\r\n".encode()
    )
    token = f"proxied{len(_GRID_REQUESTS)}"
    try:
        call = _proxied_client_calls(f"http://127.0.0.1:{port}")[client]
        with pytest.raises(Exception) as caught:
            call(f"{scheme}://{token}.invalid/{token}")
    finally:
        listener.close()
    error = caught.value
    assert any(token.encode() in request for request in _GRID_REQUESTS)
    logging.getLogger("pmcp.test").error("failed", exc_info=error)
    texts = {
        "exception_text": exception_text(error),
        "safe_traceback_text": safe_traceback_text(error),
        "describe_exception": describe_exception(error),
        "describe_exception(group)": describe_exception(
            _exception_group()("group", [error])
        ),
        "sanitize_auth_diagnostic": sanitize_auth_diagnostic(error, max_length=None),
        "log": "\n".join(_record_text(record) for record in caplog.records),
    }
    if isinstance(getattr(error, "reason", None), OSError):
        # A `URLError`'s text is its reason's: registered by origin, not
        # only through the chain (round 22 codex).
        import urllib.error

        bare = urllib.error.URLError(error.reason)
        texts["URLError, unchained"] = exception_text(bare) + safe_traceback_text(bare)
        # And the refused tunnel's `OSError` on its own, as `error.reason`
        # hands it to a caller: registered by origin (rev 25 mutant M165).
        reason = error.reason
        texts["URLError.reason"] = exception_text(reason) + safe_traceback_text(reason)
        assert str(status) in exception_text(reason), exception_text(reason)
    leaked = {
        name: text[:300]
        for name, text in texts.items()
        if any(form in text for form in _forbidden_any_case(s))
    }
    assert not leaked, leaked
    assert str(status) in texts["exception_text"], texts["exception_text"]


# --- a redirect to a host whose certificate does not match (rev 25) ----------
#
# Round 23 claude F001: after a followed redirect, `ssl`'s text names the host
# the response's `Location` chose ("certificate is not valid for '<host>'"),
# built in C and re-raised by httpcore through a variable class. The target
# host is `<sentinel>.localhost` (RFC 6761: loopback), its certificate valid
# for `localhost` only.


def _tls_mismatch_target(tmp: Path) -> tuple[Any, int, Path, str]:
    """A TLS server for `<sentinel>.localhost` with a certificate for
    `localhost`, and the CA file that signs it."""
    import datetime
    import socket
    import ssl
    import threading

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    host = f"{_GRID_S}.localhost"
    try:
        family, _, _, _, address = socket.getaddrinfo(
            host, 443, type=socket.SOCK_STREAM
        )[0]
    except OSError:
        pytest.skip("this host does not resolve *.localhost")
    now = datetime.datetime.now(datetime.timezone.utc)

    def certificate(
        subject: str, key: Any, issuer: Any, issuer_key: Any, ca: bool
    ) -> Any:
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(issuer or name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), True)
        )
        if not ca:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]), False
            )
        return builder.sign(issuer_key or key, hashes.SHA256())

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = certificate("probe-ca", ca_key, None, None, True)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = certificate("localhost", leaf_key, ca_cert.subject, ca_key, False)
    pem = serialization.Encoding.PEM
    (tmp / "ca.pem").write_bytes(ca_cert.public_bytes(pem))
    (tmp / "leaf.pem").write_bytes(leaf.public_bytes(pem))
    (tmp / "leaf.key").write_bytes(
        leaf_key.private_bytes(
            pem,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(tmp / "leaf.pem"), str(tmp / "leaf.key"))
    listener = socket.socket(family)
    listener.bind((address[0], 0))
    listener.listen(64)

    def serve(conn: Any) -> None:
        try:
            context.wrap_socket(conn, server_side=True).recv(65536)
        except Exception:  # noqa: BLE001 -- the client refuses the certificate
            pass
        finally:
            conn.close()

    def loop() -> None:
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=serve, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return listener, listener.getsockname()[1], tmp / "ca.pem", host


def _tls_client_calls(ca: Path) -> dict[str, Callable[[str], Any]]:
    import ssl

    def via_httpx(module_name: str) -> Callable[[str], Any]:
        def call(url: str) -> Any:
            import importlib

            module = importlib.import_module(module_name)

            async def run() -> Any:
                context = ssl.create_default_context(cafile=str(ca))
                async with module.AsyncClient(
                    timeout=10, verify=context, follow_redirects=True
                ) as client:
                    return (await client.get(url)).raise_for_status()

            return asyncio.run(run())

        return call

    def via_aiohttp(url: str) -> Any:
        import aiohttp

        async def run() -> Any:
            context = ssl.create_default_context(cafile=str(ca))
            async with aiohttp.ClientSession() as session:
                async with session.get(url, ssl=context) as response:
                    return await response.read()

        return asyncio.run(run())

    return {
        "httpx": via_httpx("httpx"),
        "httpx2": via_httpx("httpx2"),
        "aiohttp": via_aiohttp,
    }


@pytest.mark.parametrize(
    "client", ["httpx", "httpx2", "aiohttp", "remote-http", "remote-sse"]
)
def test_a_redirect_to_a_tls_mismatch_is_not_echoed(
    client: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redirect to `<sentinel>.localhost`, whose certificate names only
    `localhost`: each client's error, every renderer, and (for remote MCP
    servers, end to end) the connect errors and `gateway.health`'s error."""
    import pmcp  # noqa: F401 - installs the scrubbers
    from pmcp.argument_errors import exception_text, safe_traceback_text
    from pmcp.client.manager import describe_exception

    s = _GRID_S
    caplog.set_level(logging.DEBUG)
    target, port, ca, host = _tls_mismatch_target(tmp_path)
    for name in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    path = "sse" if client == "remote-sse" else "mcp"
    origin, origin_port = _serve_once_per_connection(
        (
            f"HTTP/1.1 307 Temporary Redirect\r\nLocation: https://{host}:{port}/{path}"
            "\r\nContent-Length: 0\r\n\r\n"
        ).encode()
    )
    url = f"http://127.0.0.1:{origin_port}/{path}"
    texts: dict[str, str] = {}
    try:
        if client.startswith("remote-"):
            _GRID_URL[0] = url
            errors, last = asyncio.run(_drive_remote(client.split("-")[1], origin_port))
            texts["connect errors"] = json.dumps(errors)
            texts["gateway.health error"] = json.dumps(last, default=str)
            assert "Failed to connect" in texts["connect errors"], errors
        else:
            with pytest.raises(Exception) as caught:
                _tls_client_calls(ca)[client](url)
            error = caught.value
            texts["exception_text"] = exception_text(error)
            texts["safe_traceback_text"] = safe_traceback_text(error)
            texts["describe_exception"] = describe_exception(error)
            logging.getLogger("pmcp.test").warning("failed", exc_info=error)
    finally:
        origin.close()
        target.close()
    texts["log"] = "\n".join(_record_text(record) for record in caplog.records)
    leaked = {
        name: text[:300]
        for name, text in texts.items()
        if any(form in text for form in _forbidden_any_case(s))
    }
    assert not leaked, leaked


def test_a_tunnel_refusal_is_recognised_by_origin_not_text() -> None:
    """Round 23 N1: the recognition is by origin. An `OSError` with the
    same words, raised anywhere but `http.client`'s `_tunnel`, is an
    ordinary `OSError` -- which is why pmcp never re-wraps a client error
    by its text (`OSError(str(e))` is a sink the static guard flags). The
    real refusal is the proxy rows above, through `_tunnel` itself."""
    from pmcp.argument_errors import _is_validation_error

    try:
        raise OSError("Tunnel connection failed: 403 words")
    except OSError as error:
        assert not _is_validation_error(error)


# --- an exception's origin (rev 26, round-24 codex F001) ---------------------


def test_a_builtin_raised_in_an_http_client_is_registered() -> None:
    """Any exception an HTTP client's frame raises is registered, whatever
    its type: here urllib's own `ValueError` for a URL it rejects, and
    `ipaddress`'s, reached through urllib's redirect parsing. One raised in
    pmcp's (or a test's) own frame is not, nor one a helper raises when
    pmcp, not a client, called it, nor one with no traceback."""
    import ipaddress
    import urllib.parse
    import urllib.request

    from pmcp.argument_errors import (
        _is_validation_error,
        exception_origin,
        exception_text,
    )

    s = _GRID_S
    with pytest.raises(ValueError) as from_urllib:
        urllib.request.Request(f"{s} is not a URL")
    assert exception_origin(from_urllib.value) == "http"
    assert s not in exception_text(from_urllib.value)

    class Redirects(urllib.request.HTTPRedirectHandler):
        pass

    request = urllib.request.Request("https://registry.invalid/probe")
    with pytest.raises(ValueError) as from_helper:
        Redirects().http_error_302(
            request, None, 302, "Found", {"location": f"https://[{s}]/"}
        )
    assert exception_origin(from_helper.value) == "http"
    assert s not in exception_text(from_helper.value)

    with pytest.raises(ValueError) as own:
        raise ValueError(s)
    assert exception_origin(own.value) is None
    assert not _is_validation_error(own.value)

    with pytest.raises(ValueError) as helper_from_pmcp:
        ipaddress.ip_address(s)
    assert exception_origin(helper_from_pmcp.value) is None
    with pytest.raises(ValueError):
        urllib.parse.urlsplit(f"http://[{s}]/")
    assert exception_origin(ValueError(s)) is None


def test_a_client_calling_back_into_other_code_is_not_the_clients() -> None:
    """A client that calls back (an httpx transport, an event hook) into
    code that is not its own: what that code raises is that code's."""
    import httpx

    from pmcp.argument_errors import exception_origin

    s = _GRID_S

    def handler(request: Any) -> Any:
        raise ValueError(s)

    with pytest.raises(ValueError) as caught:
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            client.get("http://callback.invalid/")
    assert exception_origin(caught.value) is None


# --- the MCP SDK's client transports (rev 26, round-24 claude F001) ---------


def _sdk_transport_handler(kind: str, s: str) -> Callable[[Any], None]:
    import time

    def reply(status: str, headers: dict[str, str], body: str) -> bytes:
        head = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        return (
            f"HTTP/1.1 {status}\r\n{head}Content-Length: {len(body)}\r\n\r\n{body}"
        ).encode()

    def handle(conn: Any) -> None:
        try:
            line = conn.recv(65536).decode("latin-1").split("\r\n", 1)[0]
            _GRID_REQUESTS.append(line.encode())
            if kind == "content-type":
                conn.sendall(reply("200 OK", {"Content-Type": f"text/{s}"}, "{}"))
            elif kind == "sse-event":
                body = f"event: {s}\ndata: {{}}\n\n"
                conn.sendall(
                    reply("200 OK", {"Content-Type": "text/event-stream"}, body)
                )
            elif line.startswith("GET"):  # legacy SSE: an endpoint elsewhere
                body = f"event: endpoint\ndata: http://{s}.example/messages\n\n"
                conn.sendall(
                    (
                        "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n"
                        + body
                    ).encode()
                )
                time.sleep(2)
        except OSError:
            pass
        finally:
            conn.close()

    return handle


@pytest.mark.parametrize(
    ("kind", "transport"),
    [
        ("content-type", "http"),
        ("content-type", "sse"),
        ("sse-event", "http"),
        ("sse-event", "sse"),
        ("sse-endpoint", "sse"),
    ],
)
def test_no_sdk_transport_rejection_reaches_any_output(
    kind: str,
    transport: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK's client transports answer a request themselves with text
    they format from the response (`Unexpected content type: text/<S>`), and
    log rejected values (`Unknown SSE event: <S>`, an endpoint on another
    origin with its traceback). Through pmcp's connect path, over both
    remote transports: the connect errors, `gateway.health`'s error, every
    log record at DEBUG and its traceback. (An `endpoint` event exists only
    on legacy SSE.)"""
    import socket
    import threading

    import pmcp  # noqa: F401 - installs the scrubbers

    s = _GRID_S
    for name in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    caplog.set_level(logging.DEBUG)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(64)
    handle = _sdk_transport_handler(kind, s)

    def loop() -> None:
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    path = "sse" if transport == "sse" else "mcp"
    _GRID_URL[0] = f"http://127.0.0.1:{listener.getsockname()[1]}/{path}"
    before = len(_GRID_REQUESTS)
    try:
        try:
            errors, last = asyncio.run(
                asyncio.wait_for(_drive_remote(transport, 0), 25)
            )
            outcome = json.dumps([errors, last], default=str)
        except Exception as error:  # noqa: BLE001 -- inspected
            from pmcp.argument_errors import exception_text, safe_traceback_text

            outcome = exception_text(error) + safe_traceback_text(error)
    finally:
        listener.close()
    assert len(_GRID_REQUESTS) > before, "never connected"
    texts = {
        "result (connect errors, gateway.health)": outcome,
        "log": "\n".join(_record_text(record) for record in caplog.records),
    }
    leaked = {
        surface: text[:400]
        for surface, text in texts.items()
        if any(form in text for form in _forbidden_any_case(s))
    }
    assert not leaked, leaked
