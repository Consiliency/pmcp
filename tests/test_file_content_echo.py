"""Content read from a file pmcp does not own never reaches an output raw.

Consiliency/pmcp#297 implementation, round 1 F001: a checkout's
``.env.pmcp`` held a quoted key with a tab, a bell and an ANSI escape, and
three messages printed it unchanged, through ``print``, which neither the
log scrubber nor the exception-text guards see.

The rule, derived over all of ``src/pmcp``: a value that comes from a
file-content parser (the dotenv readers, the JSON, YAML and TOML loaders,
and every function that returns what one of them read) reaches no output --
``print``, ``sys.stderr.write``/``sys.stdout.write``, ``click.echo``, a rich
console, a logger, ``warnings.warn`` or a raised exception's arguments --
except through a renderer that shows no text of it (:data:`_SANITIZERS`):
``plain_name`` (a name only when it is a plain identifier), ``describe_value``
(a value's structure), ``operator_safe`` (every non-printable escaped, then
shell-quoted) and the like.

The walk is interprocedural over ``src/pmcp`` by function name: a function
whose return carries a parser's output is a source; a function whose
parameter reaches an output is an output for that parameter; and a call
passes taint from an argument to its result unless the callee is known not
to (a ``src/pmcp`` function whose parameter never reaches its return, or a
value-free builtin). Every site it reports is fixed or listed in
:data:`_REVIEWED` with its reason; the table holds no stale row.
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "pmcp"

#: Calls whose result is a file's content, parsed or not.
_PARSERS = frozenset(
    {
        "dotenv_values",
        "parse_variables",
        "load_json",
        "load_json_file",
        "load_yaml",
        "json.loads",
        "json.load",
        "yaml.safe_load",
        "yaml.load",
        "yaml.safe_load_all",
        "tomllib.loads",
        "tomllib.load",
        "toml.loads",
        "toml.load",
    }
)

#: Renderers that show no text of what they are given, and builtins whose
#: result carries none.
_SANITIZERS = frozenset(
    {
        "plain_name",
        "describe_value",
        "operator_safe",
        "exception_text",
        "safe_traceback_text",
        "class_only_traceback_text",
        "entry_log_name",
        # Refuses any control character and any non-canonical host.
        "sanitize_public_auth_url",
        "len",
        "bool",
        "type",
        "isinstance",
        "id",
        "hash",
        "callable",
        "startswith",
        "endswith",
        "isidentifier",
        "isdigit",
        "isalpha",
        "isalnum",
        "is_file",
        "is_dir",
        "exists",
    }
)

#: Direct-output calls, by dotted name or method name on any receiver.
_DIRECT_OUTPUT = frozenset(
    {"print", "sys.stderr.write", "sys.stdout.write", "click.echo", "click.secho"}
)
_CONSOLE_OWNERS = frozenset({"console", "err_console", "_console", "stderr_console"})


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _dotted(node.value)
        return None if owner is None else f"{owner}.{node.attr}"
    return None


def _short(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _direct_sink(call: ast.Call) -> str | None:
    """The direct output a call writes to, or None."""
    dotted = _dotted(call.func)
    if dotted in _DIRECT_OUTPUT:
        return dotted
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "print":
        owner = _dotted(func.value) or ""
        if owner.split(".")[-1] in _CONSOLE_OWNERS:
            return f"{owner}.print"
    return None


@dataclass
class _Function:
    path: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    params: list[str] = field(default_factory=list)
    offset: int = 0  # 1 for a method's self/cls


def _functions(root: Path) -> dict[str, list[_Function]]:
    found: dict[str, list[_Function]] = {}
    for path in sorted(root.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("__") and node.name.endswith("__"):
                    # Bound by name, every class's __init__ would be one.
                    continue
                args = node.args
                params = [a.arg for a in [*args.posonlyargs, *args.args]]
                offset = 1 if params[:1] in (["self"], ["cls"]) else 0
                params += [a.arg for a in args.kwonlyargs]
                found.setdefault(node.name, []).append(
                    _Function(rel, node, params, offset)
                )
    return found


class _Model:
    """The interprocedural facts, to a fixpoint."""

    def __init__(self, root: Path = SRC) -> None:
        self.functions = _functions(root)
        #: Functions whose return carries file content with no tainted input.
        self.sources: set[str] = set()
        #: name -> parameters whose value reaches its return.
        self.passes: dict[str, set[str]] = {}
        #: name -> parameters whose value reaches an output.
        self.sinks: dict[str, set[str]] = {}
        changed = True
        while changed:
            changed = False
            for name, defs in self.functions.items():
                for fn in defs:
                    walk = _Walk(self, fn, set())
                    if walk.returns_taint and name not in self.sources:
                        self.sources.add(name)
                        changed = True
                    for param in fn.params[fn.offset :]:
                        walk = _Walk(self, fn, {param}, sources=False)
                        if walk.returns_taint and param not in self.passes.get(
                            name, set()
                        ):
                            self.passes.setdefault(name, set()).add(param)
                            changed = True
                        if walk.outputs and param not in self.sinks.get(name, set()):
                            self.sinks.setdefault(name, set()).add(param)
                            changed = True

    def known(self, name: str | None) -> bool:
        return name is not None and name in self.functions

    def bound(self, name: str, call: ast.Call) -> Iterator[tuple[str, ast.AST]]:
        """(parameter, argument) pairs of a call to a ``src/pmcp`` function."""
        for fn in self.functions[name]:
            positional = fn.params[fn.offset :]
            for index, arg in enumerate(call.args):
                if isinstance(arg, ast.Starred):
                    for param in positional[index:]:
                        yield param, arg
                    break
                if index < len(positional):
                    yield positional[index], arg
            for keyword in call.keywords:
                if keyword.arg is None:
                    for param in positional:
                        yield param, keyword.value
                elif keyword.arg in fn.params:
                    yield keyword.arg, keyword.value


class _Walk:
    """One function's taint, from ``seeds`` (and the parser sources)."""

    def __init__(
        self, model: _Model, fn: _Function, seeds: set[str], *, sources: bool = True
    ) -> None:
        self.model, self.fn, self.use_sources = model, fn, sources
        self.tainted = set(seeds)
        self.outputs: list[tuple[int, str]] = []
        self.returns_taint = False
        body = fn.node
        changed = True
        while changed:
            changed = False
            for node in self._nodes(body):
                targets: list[ast.AST] = []
                value: ast.AST | None = None
                if isinstance(node, ast.Assign):
                    targets, value = node.targets, node.value
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value:
                    targets, value = [node.target], node.value
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                    targets, value = [node.target], node.iter
                elif isinstance(node, ast.withitem) and node.optional_vars:
                    targets, value = [node.optional_vars], node.context_expr
                elif isinstance(node, ast.NamedExpr):
                    targets, value = [node.target], node.value
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _CONTAINER_STORES
                ):
                    # container.append(tainted): the container carries it.
                    targets = [node.func.value]
                    value = ast.Tuple(
                        elts=[*node.args, *(k.value for k in node.keywords)],
                        ctx=ast.Load(),
                    )
                if value is None or not self.taint(value):
                    continue
                for target in targets:
                    for name in self._names(target):
                        if name not in self.tainted:
                            self.tainted.add(name)
                            changed = True
        for node in self._nodes(body):
            if isinstance(node, ast.Return) and node.value is not None:
                if self.taint(node.value):
                    self.returns_taint = True
            elif isinstance(node, ast.Call):
                sink = _direct_sink(node)
                values = [*node.args, *(k.value for k in node.keywords)]
                if sink is not None:
                    if any(self.taint(v) for v in values):
                        self.outputs.append((node.lineno, sink))
                    continue
                name = _short(node)
                if self.model.known(name) and name in self.model.sinks:
                    assert name is not None
                    for param, arg in self.model.bound(name, node):
                        if param in self.model.sinks[name] and self.taint(arg):
                            self.outputs.append((node.lineno, f"{name}({param})"))

    def _nodes(self, root: ast.AST) -> Iterator[ast.AST]:
        """``root``'s nodes, not descending into nested functions or classes."""
        stack = list(ast.iter_child_nodes(root))
        while stack:
            node = stack.pop()
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                continue
            yield node
            stack.extend(ast.iter_child_nodes(node))

    @staticmethod
    def _names(target: ast.AST) -> Iterator[str]:
        if isinstance(target, ast.Name):
            yield target.id
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                yield from _Walk._names(item)
        elif isinstance(target, ast.Starred):
            yield from _Walk._names(target.value)
        elif isinstance(target, ast.Attribute):
            # self.x = tainted: that attribute carries it, not all of self.
            dotted = _dotted(target)
            if dotted is not None:
                yield dotted
        elif isinstance(target, ast.Subscript):
            # d[k] = tainted: the container carries it.
            yield from _Walk._names(target.value)

    def taint(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.tainted
        if isinstance(node, ast.Attribute):
            dotted = _dotted(node)
            if dotted is not None and dotted in self.tainted:
                return True
            return self.taint(node.value)
        if isinstance(node, (ast.Compare, ast.Constant, ast.Lambda)):
            return False
        if isinstance(node, ast.BoolOp) and all(
            isinstance(v, ast.Compare) for v in node.values
        ):
            return False
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(
            node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
        ):
            parts = (
                [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            )
            return any(self.taint(p) for p in parts)
        return any(self.taint(child) for child in ast.iter_child_nodes(node))

    def _call(self, call: ast.Call) -> bool:
        dotted = _dotted(call.func)
        name = _short(call)
        if name in _SANITIZERS:
            return False
        if self.use_sources and (dotted in _PARSERS or name in _PARSERS):
            return True
        values = [*call.args, *(k.value for k in call.keywords)]
        method = isinstance(call.func, ast.Attribute)
        if self.model.known(name) and not (method and name in _PASS_THROUGH):
            assert name is not None
            if self.use_sources and name in self.model.sources:
                return True
            passes = self.model.passes.get(name, set())
            return any(
                param in passes and self.taint(arg)
                for param, arg in self.model.bound(name, call)
            )
        if name not in _PASS_THROUGH:
            # Any other callee ends the trace: a model or dataclass built from
            # the content is past validation, where D9's config-field table
            # (tests/test_catalog_overlay_discovery.py) governs it.
            return False
        receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
        return any(self.taint(v) for v in values) or (
            receiver is not None and self.taint(receiver)
        )


#: Callees outside ``src/pmcp`` whose result carries their input's text:
#: string, path and container operations.
_PASS_THROUGH = frozenset(
    {
        "str",
        "repr",
        "format",
        "format_map",
        "Path",
        "PurePath",
        "fspath",
        "join",
        "strip",
        "lstrip",
        "rstrip",
        "split",
        "rsplit",
        "splitlines",
        "partition",
        "rpartition",
        "lower",
        "upper",
        "casefold",
        "title",
        "replace",
        "removeprefix",
        "removesuffix",
        "expandtabs",
        "ljust",
        "rjust",
        "center",
        "encode",
        "decode",
        "get",
        "items",
        "values",
        "keys",
        "pop",
        "popitem",
        "copy",
        "list",
        "tuple",
        "set",
        "frozenset",
        "dict",
        "sorted",
        "reversed",
        "enumerate",
        "zip",
        "next",
        "iter",
        "getattr",
        "resolve",
        "relative_to",
        "with_name",
        "with_suffix",
        "joinpath",
        "read_text",
    }
)

_CONTAINER_STORES = frozenset(
    {"append", "extend", "add", "update", "insert", "setdefault", "appendleft"}
)


@functools.cache
def _model(root: Path = SRC) -> _Model:
    return _Model(root)


def _reported(root: Path = SRC) -> set[tuple[str, str, str]]:
    """Every (path, function, output) where file content reaches an output."""
    model = _model(root)
    found: set[tuple[str, str, str]] = set()
    for defs in model.functions.values():
        for fn in defs:
            for _line, sink in _Walk(model, fn, set()).outputs:
                found.add((fn.path, fn.node.name, sink))
    return found


#: Sites the walk reports that show no repository-controlled text, each with
#: its reason. Fail-closed: a new site fails until it is made value-free or
#: listed here; a row that no longer matches fails too.
_GATEWAY_ANSWER = (
    "the running gateway's own tool answer or status snapshot, parsed from "
    "its response: the text an MCP client receives for the same call, held "
    "to the gateway's rules (D9 for config fields), not a file's content"
)
_REVIEWED: dict[tuple[str, str, str], str] = {
    ("cli.py", "run_status", "print"): _GATEWAY_ANSWER
    + "; the in-process branch is reported only because the walk is "
    "flow-insensitive and reuses the names `s` and `p`",
    ("cli.py", "run_auth_connect", "print"): _GATEWAY_ANSWER,
    ("cli.py", "run_auth_acknowledge", "print"): _GATEWAY_ANSWER,
    ("cli.py", "run_update", "print"): _GATEWAY_ANSWER,
    ("cli.py", "_run_trust_list", "print"): (
        "the operator's own trust store under ~/.config/pmcp: a decision is a "
        "fixed word and a digest is hex, both checked when the store is read; "
        "the approved path is shown through `operator_safe`"
    ),
    ("cli.py", "_run_trust_list_packages", "print"): (
        "the operator's own package-approval store: names and versions are "
        "re-validated when it is read (`_read_store_and_stale`)"
    ),
}


def test_no_file_content_reaches_an_output_raw() -> None:
    found = _reported()
    assert sorted(found - set(_REVIEWED)) == []
    assert sorted(set(_REVIEWED) - found) == []


if __name__ == "__main__":  # pragma: no cover - a listing for review
    for row in sorted(_reported()):
        print(row)
    pytest.main([__file__])


# --- the walk itself, on a synthetic tree -------------------------------------

_SYNTHETIC = """
import sys

import click
from dotenv import dotenv_values

from pmcp.argument_errors import plain_name
from pmcp.parsing import load_json


def shown(path):
    for key in dotenv_values(path):
        print(f"Ignoring {key}")


def named_safely(path):
    for key in dotenv_values(path):
        print(f"Ignoring {plain_name(key)}")


def writes(message):
    sys.stderr.write(message)


def through_a_helper(path):
    data = load_json(open(path).read(), source="x")
    writes(f"bad entry {data['name']}")


def read_values(path):
    return dotenv_values(path)


def through_a_source(path):
    for key in read_values(path):
        click.echo(key)


def described(path):
    data = load_json(open(path).read(), source="x")
    print(f"{len(data)} entries")
"""


def test_the_walk_reports_file_content_at_every_direct_output(tmp_path: Path) -> None:
    """A unit of the guard: ``print``, ``sys.stderr.write`` through a helper,
    and ``click.echo`` of a derived source are reported; a value rendered by
    ``plain_name`` or reduced to its length is not."""
    (tmp_path / "synthetic.py").write_text(_SYNTHETIC)
    assert _reported(tmp_path) == {
        ("synthetic.py", "shown", "print"),
        ("synthetic.py", "through_a_helper", "writes(message)"),
        ("synthetic.py", "through_a_source", "click.echo"),
    }


# --- the messages (round 1 F001) ----------------------------------------------

#: Keys a repository's quoted dotenv entry can hold: a tab, a bell, an ANSI
#: escape. Dummy text only.
_HOSTILE_KEYS = (
    "PMCP_SENTINEL297KEY\tA",
    "SENTINEL297KEY\x07B",
    "SENTINEL297KEY\x1b[31mC",
)


@pytest.mark.parametrize("key", _HOSTILE_KEYS)
@pytest.mark.parametrize(
    "describe",
    [
        "describe_ignored_store_env_var",
        "describe_unresolved_store_value",
        "describe_uncopied_store_entry",
    ],
)
def test_a_store_message_never_echoes_a_key_that_is_not_plain(
    describe: str, key: str
) -> None:
    from pmcp import env_store
    from pmcp.argument_errors import NOT_A_PLAIN_NAME

    render = getattr(env_store, describe)
    args = (key, ".env.pmcp") + (("a reason",) if "uncopied" in describe else ())
    text = render(*args)
    assert "SENTINEL297KEY" not in text, text
    assert not any(c in text for c in "\t\x07\x1b"), text
    assert NOT_A_PLAIN_NAME in text, text
    plain = render(*(("PMCP_PLAIN_NAME",) + args[1:]))
    assert "PMCP_PLAIN_NAME" in plain, plain


def test_an_operator_file_names_a_plain_home_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``_load_operator_text``'s refusal still names a plain key."""
    from pmcp import env_store

    monkeypatch.delenv("Home", raising=False)
    env_store._load_operator_text("Home=/elsewhere\n", "pmcp.env")
    assert "Ignoring Home in pmcp.env" in capsys.readouterr().err


@pytest.mark.parametrize("raw", ["ok", "x\x07", "\x1b[2J"])
def test_plain_name(raw: str) -> None:
    from pmcp.argument_errors import NOT_A_PLAIN_NAME, plain_name

    assert plain_name(raw) == (raw if raw == "ok" else NOT_A_PLAIN_NAME)


_PROBE = """
import sys
from pathlib import Path
project = Path(sys.argv[1])
import os
os.chdir(project)
from pmcp import env_store
env_store.serve_project_root(project)
env_store.credential_value("ANY_DUMMY_NAME", root=project)
env_store.copyable_from_repository(
    env_store.read_store("project", project=project), ".env.pmcp", frozenset()
)
"""


def test_a_rejected_repository_store_key_is_not_echoed_to_stderr(
    tmp_path: Path,
) -> None:
    """Round 1 F001, as filed: a cloned checkout's ``.env.pmcp`` with a key
    pmcp rejects (``PMCP_`` namespace), quoted so it carries a tab and a
    bell, and a binding whose ``${...}`` reference pmcp refuses. Neither
    the "Ignoring" lines nor ``pmcp secrets sync``'s "Not copying" lines
    echo either key."""
    import os
    import subprocess
    import sys

    sentinel = "SENTINEL297KEY"
    home = tmp_path / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    project = tmp_path / "proj"
    (project / ".git").mkdir(parents=True)
    (project / ".env.pmcp").write_text(
        f"'PMCP_{sentinel}\\tA\\x07'=dummy\n'{sentinel}_B\\tB'=${{NOT_DEFINED_HERE}}\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("PMCP_")}
    env.update(HOME=str(home), PYTHONPATH=str(SRC.parent))
    done = subprocess.run(
        [sys.executable, "-c", _PROBE, str(project)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    assert "Ignoring" in done.stderr or "Not copying" in done.stderr, done.stderr
    assert sentinel not in done.stderr, [
        line for line in done.stderr.splitlines() if sentinel in line
    ]
    assert "\x07" not in done.stderr


def test_doctor_names_a_config_key_through_operator_safe(tmp_path: Path) -> None:
    """``pmcp doctor``'s remote checks name a ``.mcp.json`` key as written,
    but escaped: a checkout's file may hold an ANSI escape in it."""
    from pmcp.cli_commands.doctor import collect_remote_header_diagnostics

    config = {"mcpServers": {"evil\x1b[2Jname": {"type": "http", "url": " "}}}
    checks = collect_remote_header_diagnostics(config, tmp_path)
    assert checks, checks
    assert not any("\x1b" in message for _, _, message in checks), checks
