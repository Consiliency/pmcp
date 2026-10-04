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

#: Named exemptions, ``file::function`` -> reason. Each must still exist.
_EXEMPT: dict[str, str] = {
    # python-dotenv never raises on bad input; it logs "python-dotenv could
    # not parse statement starting at line N" -- the line number only
    # (measured on python-dotenv 1.x).
    "env_store.py::read_env_file": "dotenv_values",
    "cli.py::load_startup_env": "load_dotenv",
    "tools/handlers.py::_check_api_key_available": "load_dotenv",
    # A response object's `.json()`: JSON has no constructors, so its
    # failures are JSONDecodeError (fixed vocabulary; the `doc` attribute is
    # rendered by exception_text as `could not parse JSON ...`),
    # RecursionError, or aiohttp's ContentTypeError (the server's MIME type).
    # Every one is caught and rendered via exception_text or swallowed.
    "manifest/version_checker.py::get_npm_version": ".json() (aiohttp)",
    "manifest/version_checker.py::get_pypi_version": ".json() (aiohttp)",
    "manifest/version_checker.py::get_cargo_version": ".json() (aiohttp)",
    "manifest/version_checker.py::get_docker_version": ".json() (aiohttp)",
    "cli.py::_probe_http_health": ".json() (httpx), error swallowed",
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
            if isinstance(func, ast.Attribute) and func.attr == "json":
                # With or without arguments (``resp.json(content_type=None)``).
                found.append((node.lineno, ".json()"))
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
    assert "OSError: disk gone" in err, err
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
    assert "RuntimeError: boom" in result.stderr
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
    assert "RuntimeError: boom" in result.stderr, result.stderr
    expected = {
        "yaml": "yaml.parser.ParserError: could not parse YAML (ParserError)",
        "pydantic": "pydantic_core._pydantic_core.ValidationError: 1 validation error",
    }[origin]
    assert expected in result.stderr, result.stderr
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
