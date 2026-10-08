"""The authoritative inventory of credential-store readers, derived from the AST.

Consiliency/pmcp#367: every ``src/pmcp`` reader of a credential store goes
through ONE entry point -- ``env_store.read_store``, and
``env_store.load_store`` for a load into the process environment --
which picks a confined or a followed read from the store's scope. No call site
may choose its own read mode. This is the one inventory: the documented-list
inventory Consiliency/pmcp#366 added (``tests/test_env_store_reader_inventory.py``,
which pinned the readers that still followed a link) is folded in here, now that
that list is empty.

The inventory is not a hand-written list of readers. It walks every module under
``src/pmcp`` and records, per function (methods by ``Class.name``, nested
functions and lambdas folded into the function that holds them):

* **low-level reads** -- a call to ``read_env_file``, ``read_env_text``,
  ``_read_env_file_strict`` or ``read_confined``, or to ``load_dotenv`` /
  ``dotenv_values`` with a PATH (positional, ``dotenv_path=``, or none at all,
  which discovers one) rather than ``stream=``;
* **dotenv parses** -- any ``load_dotenv`` / ``dotenv_values`` call, path or
  stream;
* **raw opens of a store** -- ``open`` / ``os.open`` / ``.open`` / ``.read_text``
  / ``.read_bytes`` in a function that names a store file (``.env.pmcp``,
  ``pmcp.env``, ``.env`` or ``tenants``).

and fails when any of them sits outside the entry point -- a stream parse
included: only the entry point hands text to ``load_dotenv``, because only it
drops the variables a repository file may not set. Import aliases
(``from dotenv import load_dotenv as ld``) and attribute calls
(``dotenv.load_dotenv``, ``env_store.read_env_file``) resolve to the same name.

There are NO exemptions, and ``test_there_are_no_exemptions`` keeps it so. The
last one -- ``cli.load_startup_env``'s ``load_dotenv(dotenv_path)``, a ``.env``
discovered by walking up from where pmcp is INSTALLED -- was removed on review
(Consiliency/pmcp#372 round 1): an install in a ``.venv`` inside a checkout walks
up into that checkout, so the file it reaches is the repository's. It is now
loaded through ``load_store`` like every other repository-controlled
file.

Two more rules from the same review round live here because they guard the same
reads:

* **Absence is only ENOENT.** One rule in one place:
  ``atomic_write.is_absent``, and Consiliency/pmcp#366's AST ban on yes/no
  existence checks in the store modules (``tests/test_store_path_resolution.py``).
* **Every environment variable ``src/pmcp`` reads is classified** in
  ``env_store`` as path-and-trust (a repository file may not set it), gated by
  provenance at its use, or operational. The set is derived from the AST, so a
  new variable fails here until someone decides which it is.
"""

from __future__ import annotations

import ast
import functools
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "pmcp"

#: The entry point and the helpers it is built from. Reads below these are the
#: implementation of the one rule; anywhere else they are a bypass.
ENTRY_POINT = frozenset({"read_store", "load_store"})
ENTRY_POINT_INTERNALS = frozenset(
    {
        ("pmcp.env_store", "read_store"),
        ("pmcp.env_store", "load_store"),
        ("pmcp.env_store", "load_discovered_dotenv"),
        ("pmcp.env_store", "_build_root_entry"),
        ("pmcp.env_store", "_repository_values"),
        ("pmcp.env_store", "read_store_for_update"),
        ("pmcp.env_store", "_read_confined_text"),
        ("pmcp.env_store", "_read_user_text"),
        ("pmcp.env_store", "read_env_file"),
        ("pmcp.env_store", "read_env_text"),
        ("pmcp.env_store", "_read_env_file_strict"),
        ("pmcp.env_store", "_load_operator_text"),
    }
)

LOW_LEVEL_READERS = frozenset(
    {"read_env_file", "read_env_text", "_read_env_file_strict", "read_confined"}
)
DOTENV_PARSERS = frozenset({"load_dotenv", "dotenv_values"})
#: Readers whose bare reference (not a call) is itself a bypass: a call
#: through ``r = read_env_file`` or ``map(load_dotenv, ...)`` escapes the scan.
ESCAPABLE = frozenset(
    {"read_env_file", "read_env_text", "_read_env_file_strict", "read_confined"}
    | {"load_dotenv", "dotenv_values"}
)
RAW_OPENS = frozenset({"open", "read_text", "read_bytes"})
STORE_NAMES = (".env.pmcp", "pmcp.env", ".env", "tenants")

#: (module, function, sink) -> a measured reason. Empty, and asserted empty.
EXEMPT: dict[tuple[str, str, str], str] = {}


@dataclass
class FunctionFacts:
    low_level: set[str] = field(default_factory=set)
    #: Each path-based parse as written, ``load_dotenv(dotenv_path)``, so an
    #: exemption names one exact call, not every call of that function.
    path_parses: list[str] = field(default_factory=list)
    stream_parses: set[str] = field(default_factory=set)
    raw_opens: set[str] = field(default_factory=set)
    entry_calls: set[str] = field(default_factory=set)
    names_a_store: bool = False
    #: A reader referenced WITHOUT being called -- assigned, passed, rebound --
    #: so a later call through the new name would escape the scan.
    escapes: list[str] = field(default_factory=list)


def _module_name(path: Path) -> str:
    rel = path.relative_to(SRC.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


class _Walker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.stack: list[str] = []
        self.facts: dict[str, FunctionFacts] = defaultdict(FunctionFacts)
        self.depth = 0
        self.callees: set[int] = set()

    # -- scope ------------------------------------------------------------- #
    def _current(self) -> FunctionFacts:
        return self.facts[".".join(self.stack) if self.stack else "<module>"]

    def _enter(self, node: ast.AST, name: str) -> None:
        self.stack.append(name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Nested functions are folded into the outermost function that holds
        # them, so a reader cannot hide its read in an inner helper.
        self.depth += 1
        try:
            if self.depth > 1:
                self.generic_visit(node)
            else:
                self._enter(node, node.name)
        finally:
            self.depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    # -- imports ----------------------------------------------------------- #
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    # -- facts ------------------------------------------------------------- #
    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and any(
            name in node.value for name in STORE_NAMES
        ):
            # Docstrings mention stores freely; only code that builds a path
            # matters, and a docstring is an Expr statement handled below.
            self._current().names_a_store = True

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return  # a docstring or bare string: not a path
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        name = self.aliases.get(node.id, node.id)
        if name in ESCAPABLE and id(node) not in self.callees:
            self._current().escapes.append(f"{name} referenced at line {node.lineno}")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in ESCAPABLE and id(node) not in self.callees:
            self._current().escapes.append(
                f"{node.attr} referenced at line {node.lineno}"
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.callees.add(id(node.func))
        func = node.func
        if isinstance(func, ast.Name):
            name = self.aliases.get(func.id, func.id)
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            name = ""
        facts = self._current()
        if name in LOW_LEVEL_READERS:
            facts.low_level.add(name)
        if name in DOTENV_PARSERS:
            by_stream = any(kw.arg == "stream" for kw in node.keywords)
            by_path = bool(node.args) or any(
                kw.arg == "dotenv_path" for kw in node.keywords
            )
            if by_path or not by_stream:
                spelled = ", ".join(
                    [ast.unparse(arg) for arg in node.args]
                    + [ast.unparse(kw) for kw in node.keywords]
                )
                facts.path_parses.append(f"{name}({spelled})")
            else:
                facts.stream_parses.add(name)
        if name in RAW_OPENS:
            facts.raw_opens.add(name)
        if name in ENTRY_POINT:
            facts.entry_calls.add(name)
        self.generic_visit(node)


def inventory(sources: dict[str, str]) -> dict[tuple[str, str], FunctionFacts]:
    """``{(module, function): facts}`` for every function with a store fact."""
    result: dict[tuple[str, str], FunctionFacts] = {}
    for module, source in sources.items():
        walker = _Walker()
        walker.visit(_parse(source))
        for function, facts in walker.facts.items():
            # Methods: the class is part of the stack; keep "Class.method".
            result[(module, function)] = facts
    return result


def bypasses(inv: dict[tuple[str, str], FunctionFacts]) -> list[str]:
    found: list[str] = []
    used_exemptions: set[tuple[str, str, str]] = set()
    for (module, function), facts in sorted(inv.items()):
        if (module, function) in ENTRY_POINT_INTERNALS:
            continue
        sinks = sorted(facts.low_level) + facts.path_parses
        if facts.names_a_store:
            sinks += sorted(facts.raw_opens)
        for sink in sinks:
            key = (module, function, sink)
            if key in EXEMPT and key not in used_exemptions:
                used_exemptions.add(key)  # one call only: a second is a bypass
                continue
            shown = sink if "(" in sink else f"{sink}()"
            found.append(f"{module}:{function} reads a store with {shown}")
        for escape in facts.escapes:
            found.append(f"{module}:{function} lets a reader escape: {escape}")
        if facts.stream_parses:
            found.append(f"{module}:{function} parses dotenv text outside load_store")
    for key in sorted(set(EXEMPT) - used_exemptions):
        found.append(f"exemption {key} no longer matches anything: drop it")
    return found


@functools.lru_cache(maxsize=None)
def _parse(source: str) -> ast.Module:
    """``ast.parse``, once per distinct source per process: the inventories
    walk every module of ``src`` many times, and no walker mutates a tree
    (Consiliency/pmcp#372: suite time)."""
    return ast.parse(source)


@functools.lru_cache(maxsize=1)
def _src_source_items() -> tuple[tuple[str, str], ...]:
    return tuple(
        (_module_name(path), path.read_text(encoding="utf-8"))
        for path in sorted(SRC.rglob("*.py"))
    )


def _src_sources() -> dict[str, str]:
    # A fresh dict each call: callers may edit their copy.
    return dict(_src_source_items())


# --------------------------------------------------------------------------- #
# The tests.
# --------------------------------------------------------------------------- #


def test_no_function_in_src_reads_a_store_around_the_entry_point() -> None:
    assert bypasses(inventory(_src_sources())) == []


def test_the_inventory_sees_the_readers_it_governs() -> None:
    """Not vacuous: the walk finds the entry point's callers and its internals.

    Every function that calls the entry point is a store reader the rule now
    governs. The walk must see at least the modules the issue named --
    remote-header auth, the CLI, doctor, secrets, the gateway handlers and the
    env store itself -- or it is not looking at the tree.
    """
    inv = inventory(_src_sources())
    callers = {key for key, facts in inv.items() if facts.entry_calls}
    modules = {module for module, _ in callers}
    assert {
        "pmcp.cli",
        "pmcp.cli_commands.secrets",
        "pmcp.tools.handlers",
        "pmcp.env_store",
    } <= modules, sorted(callers)
    internals_with_reads = {
        key
        for key in ENTRY_POINT_INTERNALS
        if inv.get(key) and inv[key].low_level | inv[key].stream_parses
    }
    assert internals_with_reads, "the walk found no read inside the entry point"


def test_the_entry_point_internals_all_exist() -> None:
    """A renamed internal must not leave a stale allowance behind."""
    inv = inventory(_src_sources())
    missing = sorted(key for key in ENTRY_POINT_INTERNALS if key not in inv)
    assert missing == []


def test_the_walker_flags_every_bypass_shape() -> None:
    """The walker itself, against a synthetic module holding each bypass."""
    source = """
import os
from dotenv import load_dotenv as ld, dotenv_values
from pmcp import env_store
from pmcp.env_store import read_env_file as ref, load_store

def follows_project(root):
    return ref(root / ".env.pmcp")

def attribute_call(root):
    return env_store.read_env_text(root / ".env.pmcp")

def aliased_path_load():
    ld("x")

def discovered_load():
    ld()

def opens_tenant(root):
    return open(root / ".pmcp" / "tenants" / "t" / "pmcp.env").read()

def reads_text(root):
    return (root / ".env.pmcp").read_text()

def stream_without_entry(text):
    return dotenv_values(stream=text)

def hidden_in_a_nested_helper(root):
    def inner():
        return env_store.read_confined(root / ".env.pmcp", root)
    return inner()

class Gateway:
    def check(self):
        return [lambda: env_store._read_env_file_strict(".env.pmcp")]

def stream_of_entry_text(root):
    ld(stream="X=1")

def fine(root):
    load_store("project", path=root / ".env.pmcp")
"""
    found = bypasses(inventory({"pmcp.synthetic": source}))
    flagged = {
        line.split(" ")[0] for line in found if line.startswith("pmcp.synthetic")
    }
    assert flagged == {
        "pmcp.synthetic:follows_project",
        "pmcp.synthetic:attribute_call",
        "pmcp.synthetic:aliased_path_load",
        "pmcp.synthetic:discovered_load",
        "pmcp.synthetic:opens_tenant",
        "pmcp.synthetic:reads_text",
        "pmcp.synthetic:stream_without_entry",
        "pmcp.synthetic:hidden_in_a_nested_helper",
        "pmcp.synthetic:Gateway.check",
        "pmcp.synthetic:stream_of_entry_text",
    }, found


def test_there_are_no_exemptions() -> None:
    """Every read in src goes through the entry point: nothing is exempt."""
    inv = inventory(_src_sources())
    matched = sorted(
        (module, function, sink)
        for (module, function), facts in inv.items()
        for sink in [*facts.low_level, *facts.path_parses]
        if (module, function) not in ENTRY_POINT_INTERNALS
    )
    assert EXEMPT == {}
    assert matched == []


def test_the_startup_env_load_is_flagged_if_it_loads_by_path_again() -> None:
    """The F001 shape: discovery handed straight to ``load_dotenv(path)``."""
    source = """
from dotenv import find_dotenv, load_dotenv

def load_startup_env(dotenv_path=None):
    load_dotenv(dotenv_path)
    load_dotenv(find_dotenv())
"""
    assert bypasses(inventory({"pmcp.cli": source})) == [
        "pmcp.cli:load_startup_env reads a store with load_dotenv(dotenv_path)",
        "pmcp.cli:load_startup_env reads a store with load_dotenv(find_dotenv())",
    ]


# --------------------------------------------------------------------------- #
# Every environment variable src/pmcp reads is classified (grok F001).
# --------------------------------------------------------------------------- #

#: Calls that read the environment implicitly, and the variables each consults.
IMPLICIT_ENV_READS = {
    "home": {"HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"},
    "expanduser": {"HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"},
    "gettempdir": {"TMPDIR", "TEMP", "TMP"},
    "mkstemp": {"TMPDIR", "TEMP", "TMP"},
    "mkdtemp": {"TMPDIR", "TEMP", "TMP"},
    "NamedTemporaryFile": {"TMPDIR", "TEMP", "TMP"},
    "TemporaryDirectory": {"TMPDIR", "TEMP", "TMP"},
    "which": {"PATH", "PATHEXT"},
}


def _is_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or (
        isinstance(node, ast.Name) and node.id == "environ"
    )


def _env_reading_helpers(sources: dict[str, str]) -> set[str]:
    """Functions that read the environment by the name a PARAMETER holds."""
    helpers: set[str] = set()
    for source in sources.values():
        for fn in ast.walk(_parse(source)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
            for node in ast.walk(fn):
                arg = None
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if (
                        node.func.attr in ("get", "pop")
                        and _is_environ(node.func.value)
                    ) or node.func.attr == "getenv":
                        arg = node.args[0] if node.args else None
                elif isinstance(node, ast.Subscript) and _is_environ(node.value):
                    arg = node.slice
                if isinstance(arg, ast.Name) and arg.id in params:
                    helpers.add(fn.name)
    return helpers


def env_reads(sources: dict[str, str]) -> dict[str, set[str]]:
    """``{variable: {"module:function", ...}}`` for every literal environment read.

    ``os.environ.get/pop/setdefault(K)``, ``os.environ[K]``, ``K in os.environ``,
    ``os.getenv(K)`` and ``environ.get(K)`` on a mapping named ``environ`` (the
    feedback gate's parameter), with ``K`` a string literal or a module-level
    string constant; plus the variables an implicit reader consults.
    """
    reads: dict[str, set[str]] = defaultdict(set)
    helpers = _env_reading_helpers(sources)
    for module, source in sources.items():
        tree = _parse(source)
        consts: dict[str, str] = {}
        for stmt in tree.body:
            targets = (
                stmt.targets
                if isinstance(stmt, ast.Assign)
                else [stmt.target]
                if isinstance(stmt, ast.AnnAssign)
                else []
            )
            value = getattr(stmt, "value", None)
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                for target in targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = value.value

        def key(node: ast.AST) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.Name):
                return consts.get(node.id)
            return None

        def visit(node: ast.AST, where: str) -> None:
            for child in ast.iter_child_nodes(node):
                inner = where
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inner = f"{module}:{child.name}"
                name: str | None = None
                if isinstance(child, ast.Call):
                    func = child.func
                    attr = (
                        func.attr
                        if isinstance(func, ast.Attribute)
                        else (func.id if isinstance(func, ast.Name) else "")
                    )
                    if (
                        isinstance(func, ast.Attribute)
                        and attr in ("get", "pop", "setdefault")
                        and _is_environ(func.value)
                        and child.args
                    ):
                        name = key(child.args[0])
                    elif attr == "getenv" and child.args:
                        name = key(child.args[0])
                    elif attr in helpers and child.args:
                        # A helper that reads the environment by its argument
                        # (server._env_int): the literal at the call site.
                        name = key(child.args[0])
                    for implied in IMPLICIT_ENV_READS.get(attr, ()):
                        reads[implied].add(inner)
                elif isinstance(child, ast.Subscript) and _is_environ(child.value):
                    name = key(child.slice)
                elif (
                    isinstance(child, ast.Compare)
                    and len(child.ops) == 1
                    and isinstance(child.ops[0], (ast.In, ast.NotIn))
                    and _is_environ(child.comparators[0])
                ):
                    name = key(child.left)
                if name is not None and name.isupper():
                    reads[name].add(inner)
                visit(child, inner)

        visit(tree, f"{module}:<module>")
    return reads


def test_every_environment_variable_pmcp_reads_is_classified() -> None:
    from pmcp import env_store

    classes = (
        env_store.PATH_AND_TRUST_ENV_VARS,
        env_store.PROVENANCE_GATED_ENV_VARS,
        env_store.OPERATIONAL_ENV_VARS,
    )
    for a in range(len(classes)):
        for b in range(a + 1, len(classes)):
            assert not classes[a] & classes[b]
    classified = set().union(*classes)
    reads = env_reads(_src_sources())
    unclassified = {k: sorted(v) for k, v in reads.items() if k not in classified}
    assert unclassified == {}, (
        "classify each in env_store: PATH_AND_TRUST_ENV_VARS if it decides a path, "
        "a root, who may connect or whom to trust; PROVENANCE_GATED_ENV_VARS if "
        "its use is gated by env_key_is_operator_supplied; else OPERATIONAL"
    )
    # Every implicit reader's variables are path-and-trust.
    for implied in IMPLICIT_ENV_READS.values():
        assert implied <= env_store.PATH_AND_TRUST_ENV_VARS


def test_every_provenance_gated_variable_is_gated_where_it_is_read() -> None:
    """A gated variable is only read in a function that asks its provenance."""
    from pmcp import env_store

    sources = _src_sources()
    reads = env_reads(sources)
    gate_calls: set[str] = set()
    for module, source in sources.items():
        for node in ast.walk(_parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = {
                    n.func.id
                    if isinstance(n.func, ast.Name)
                    else getattr(n.func, "attr", "")
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call)
                }
                if names & {"env_key_is_operator_supplied", "untrusted"}:
                    gate_calls.add(f"{module}:{node.name}")
    for variable in env_store.PROVENANCE_GATED_ENV_VARS:
        assert reads.get(variable), f"{variable} is never read: drop it"
        assert reads[variable] <= gate_calls, (variable, reads[variable] - gate_calls)


def test_the_env_scan_sees_each_read_shape() -> None:
    source = (
        "import os, shutil, tempfile\n"
        "from pathlib import Path\n"
        "K = 'PMCP_CONST'\n"
        "def f(environ):\n"
        "    os.environ.get('PMCP_A'); os.environ['PMCP_B']; 'PMCP_C' in os.environ\n"
        "    os.getenv('PMCP_D'); environ.get(K); Path.home(); shutil.which('x')\n"
        "    tempfile.gettempdir()\n"
        "def _env_n(name):\n"
        "    return os.environ.get(name)\n"
        "def g():\n"
        "    _env_n('PMCP_E')\n"
    )
    assert set(env_reads({"pmcp.x": source})) == {
        "PMCP_A",
        "PMCP_B",
        "PMCP_C",
        "PMCP_D",
        "PMCP_E",
        "PMCP_CONST",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "PATH",
        "PATHEXT",
        "TMPDIR",
        "TEMP",
        "TMP",
    }


# --------------------------------------------------------------------------- #
# Every spawn's environment comes from a builder (Consiliency/pmcp#372 round 2).
# --------------------------------------------------------------------------- #

#: Calls that start a process. ``StdioServerParameters`` is the MCP SDK's
#: description of one (it spawns from it, with ``env=`` or its own default).
SPAWN_CALLS = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "create_subprocess_exec",
        "create_subprocess_shell",
        "system",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "posix_spawn",
        "posix_spawnp",
        "run_process",
        "open_process",
        "StdioServerParameters",
    }
)
#: The env builders. ``build_install_child_env`` and ``sanitized_subprocess_env``
#: both build on ``env_store.child_process_env``.
ENV_BUILDERS = frozenset(
    {"child_process_env", "sanitized_subprocess_env", "build_install_child_env"}
)


#: Modules whose functions start a process, by the attribute names that do.
_SPAWNING_MODULES = {
    "subprocess": {"run", "Popen", "call", "check_call", "check_output"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    "os": {n for n in SPAWN_CALLS if n == "system" or n.startswith(("exec", "spawn"))}
    | {"posix_spawn", "posix_spawnp"},
    "anyio": {"run_process", "open_process"},
    "mcp": {"StdioServerParameters"},
}


def _spawn_aliases(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """``(module aliases, bare spawn names)`` bound by this module's imports.

    ``import subprocess as sp`` binds ``sp`` -> ``subprocess``; ``from
    subprocess import run as r`` binds the bare name ``r`` to a spawn.
    """
    modules: dict[str, str] = {m: m for m in _SPAWNING_MODULES}
    bare: set[str] = {"StdioServerParameters"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _SPAWNING_MODULES:
                    modules[alias.asname or root] = root
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            for alias in node.names:
                if root in _SPAWNING_MODULES and alias.name in _SPAWNING_MODULES[root]:
                    bare.add(alias.asname or alias.name)
    return modules, bare


def _is_spawn(
    call: ast.Call, aliases: tuple[dict[str, str], set[str]] | None = None
) -> bool:
    modules, bare = aliases if aliases is not None else ({}, {"StdioServerParameters"})
    modules = {**{m: m for m in _SPAWNING_MODULES}, **modules}
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in bare
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in ("subprocess_exec", "subprocess_shell"):
        return True  # loop.subprocess_exec / loop.subprocess_shell
    if func.attr == "StdioServerParameters":
        return True
    owner = func.value.id if isinstance(func.value, ast.Name) else ""
    real = modules.get(owner)
    if real is None:
        # An unknown owner: the unambiguous names still count.
        return func.attr in (
            "Popen",
            "create_subprocess_exec",
            "create_subprocess_shell",
        )
    return func.attr in _SPAWNING_MODULES[real]


def _builder_call(node: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    return name in ENV_BUILDERS


def spawn_sites(sources: dict[str, str]) -> list[tuple[str, str, bool]]:
    """``(module, function, env_from_a_builder)`` for every spawn in ``sources``.

    ``env=`` must be a call to a builder, or a name the same function assigned
    from one. A spawn without ``env=`` inherits the process environment
    unexamined, and the SDK's ``StdioServerParameters`` without ``env=`` uses
    its own default -- both fail.
    """
    sites: list[tuple[str, str, bool]] = []
    for module, source in sources.items():
        tree = _parse(source)
        aliases = _spawn_aliases(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            built: set[str] = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Assign) and _builder_call(node.value):
                    built |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and _is_spawn(node, aliases):
                    env = next((k.value for k in node.keywords if k.arg == "env"), None)
                    ok = _builder_call(env) or (
                        isinstance(env, ast.Name) and env.id in built
                    )
                    sites.append((module, fn.name, ok))
    return sites


def test_every_spawn_takes_its_environment_from_a_builder() -> None:
    sites = spawn_sites(_src_sources())
    assert len(sites) >= 10, sites  # not vacuous: the tree has at least this many
    assert [s for s in sites if not s[2]] == []


def test_the_spawn_scan_sees_each_shape() -> None:
    source = (
        "import asyncio, os, subprocess\n"
        "from mcp import StdioServerParameters\n"
        "def bare():\n"
        "    subprocess.run(['x'])\n"
        "async def inherits():\n"
        "    await asyncio.create_subprocess_exec('x', env=os.environ)\n"
        "def sdk_default():\n"
        "    StdioServerParameters(command='x', args=[])\n"
        "def system():\n"
        "    os.system('x')\n"
        "def aliased():\n"
        "    import subprocess as sp\n"
        "    sp.run(['x'])\n"
        "def from_import():\n"
        "    from subprocess import run as r\n"
        "    r(['x'])\n"
        "async def on_loop(loop):\n"
        "    await loop.subprocess_exec(object, 'x')\n"
        "def not_a_spawn():\n"
        "    asyncio.run(main())\n"
        "def fine():\n"
        "    subprocess.Popen(['x'], env=child_process_env())\n"
        "def fine_by_name():\n"
        "    env = sanitized_subprocess_env(None)\n"
        "    subprocess.run(['x'], env=env)\n"
    )
    assert spawn_sites({"pmcp.x": source}) == [
        ("pmcp.x", "bare", False),
        ("pmcp.x", "inherits", False),
        ("pmcp.x", "sdk_default", False),
        ("pmcp.x", "system", False),
        ("pmcp.x", "aliased", False),
        ("pmcp.x", "from_import", False),
        ("pmcp.x", "on_loop", False),
        ("pmcp.x", "fine", True),
        ("pmcp.x", "fine_by_name", True),
    ]


# --------------------------------------------------------------------------- #
# Every credential lookup goes through env_store.credential_value.
# --------------------------------------------------------------------------- #

#: Dynamic-name environment reads that are not credential lookups, and why.
#: Asserted exact.
NOT_A_CREDENTIAL_LOOKUP = {
    ("pmcp.manifest.loader", "_canonical_server"): (
        "probes that each credential lookup key can be read from the "
        "environment at all, so an overlay entry that would raise in "
        "credential_value is dropped at load (Consiliency/pmcp#342); the value "
        "is discarded"
    ),
    ("pmcp.server", "_env_int"): (
        "reads a pmcp tuning variable by the name its caller passes; the "
        "classification inventory sees each call's literal"
    ),
    ("pmcp.tools.handlers", "_refresh_config_unchanged"): (
        "compares a config's env overrides with the environment a spawn inherits"
    ),
}


def _is_called(scope: ast.AST, attr: ast.Attribute) -> bool:
    return any(isinstance(n, ast.Call) and n.func is attr for n in ast.walk(scope))


def dynamic_environ_reads(sources: dict[str, str]) -> set[tuple[str, str]]:
    """``(module, outermost function)`` reading ``os.environ``/``getenv`` by a non-literal name."""
    found: set[tuple[str, str]] = set()
    for module, source in sources.items():
        if module == "pmcp.env_store":
            continue
        tree = _parse(source)
        for fn in tree.body:
            targets = (
                [fn]
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                else [
                    m
                    for m in getattr(fn, "body", [])
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
            )
            for target in targets:
                for node in ast.walk(target):
                    key = None
                    if isinstance(node, ast.Call) and isinstance(
                        node.func, ast.Attribute
                    ):
                        if node.func.attr == "get" and _is_environ(node.func.value):
                            key = node.args[0] if node.args else None
                        elif node.func.attr == "getenv":
                            key = node.args[0] if node.args else None
                    elif (
                        isinstance(node, ast.Subscript)
                        and isinstance(node.ctx, ast.Load)
                        and _is_environ(node.value)
                    ):
                        key = node.slice
                    if (
                        isinstance(node, ast.Attribute)
                        and node.attr in ("get", "getenv")
                        and (_is_environ(node.value) or node.attr == "getenv")
                        and not _is_called(target, node)
                    ):
                        # `os.environ.get` handed on as a lookup function.
                        found.add((module, target.name))
                    if key is not None and not isinstance(key, ast.Constant):
                        if isinstance(key, ast.Name) and key.id.isupper():
                            continue  # a module constant: a literal name
                        found.add((module, target.name))
    return found


def test_every_credential_lookup_goes_through_credential_value() -> None:
    """A credential a project file supplies is in the map, not ``os.environ``:
    a lookup that read ``os.environ`` by name would silently miss it."""
    assert dynamic_environ_reads(_src_sources()) == set(NOT_A_CREDENTIAL_LOOKUP)


def test_the_credential_lookup_scan_sees_each_shape() -> None:
    source = (
        "import os\n"
        "K = 'PMCP_K'\n"
        "def by_get(k):\n"
        "    return os.environ.get(k)\n"
        "def by_index(k):\n"
        "    return os.environ[k]\n"
        "def by_getenv(k):\n"
        "    return os.getenv(k)\n"
        "def handed_on(f):\n"
        "    return f(os.environ.get)\n"
        "def literal():\n"
        "    return os.environ.get('PMCP_X'), os.environ[K]\n"
        "def write(k, v):\n"
        "    os.environ[k] = v\n"
    )
    assert dynamic_environ_reads({"pmcp.x": source}) == {
        ("pmcp.x", "by_get"),
        ("pmcp.x", "by_index"),
        ("pmcp.x", "by_getenv"),
        ("pmcp.x", "handed_on"),
    }


def test_the_walker_flags_a_reader_that_escapes_by_reference() -> None:
    """Folded in from Consiliency/pmcp#366's inventory (its round 7 N-1)."""
    source = """
from dotenv import dotenv_values as _dv
import dotenv
import functools
from pmcp import env_store

def sneaky(root):
    a = _dv(root / ".env.pmcp")
    b = dotenv.load_dotenv(root / ".env.pmcp")
    c = env_store.read_env_file(root)
    r = _dv
    map(_dv, [root])
    functools.partial(dotenv.dotenv_values)(root)
    return a, b, c, r
"""
    found = bypasses(inventory({"pmcp.sneaky": source}))
    escapes = [line for line in found if "escape" in line]
    reads = [line for line in found if "reads a store" in line]
    assert len(escapes) == 3, found
    assert len(reads) == 3, found


# --------------------------------------------------------------------------- #
# One gate for credential VALUES (Consiliency/pmcp#372 round 3): nothing reads
# the credential map, or a store's values, except env_store.credential_value.
# --------------------------------------------------------------------------- #

#: Calls whose result is a store's ``{name: value}``.
STORE_VALUE_PRODUCERS = frozenset({"read_store", "repository_values"})
#: Reading a value out of such a dict. Names alone (``set(d)``, ``sorted(d)``,
#: ``d.keys()``, ``k in d``) are fine: they are not credentials.
VALUE_READS = frozenset({"get", "items", "values", "pop", "setdefault", "copy"})
#: env_store functions that may touch the map itself, and why. Asserted exact.
MAP_TOUCHERS = {
    "_root_entry": "reads a root's entry for THE gate (credential_value)",
    "load_store": "stores a root's entry built by _build_root_entry",
    "reset_repo_credentials": "test-only clear",
}


def _outermost_functions(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append(node)
        elif isinstance(node, ast.ClassDef):
            found.extend(_outermost_functions(node))
    return found


def credential_value_bypasses(sources: dict[str, str]) -> list[str]:
    found: list[str] = []
    for module, source in sources.items():
        tree = _parse(source)
        for fn in _outermost_functions(tree):
            bound: set[str] = set()
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Call)
                    and getattr(
                        node.value.func, "id", getattr(node.value.func, "attr", "")
                    )
                    in STORE_VALUE_PRODUCERS
                ):
                    bound |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            for node in ast.walk(fn):
                where = f"{module}:{fn.name}:{getattr(node, 'lineno', '?')}"
                # The map itself.
                if isinstance(node, ast.Name) and node.id == "_REPO_CREDENTIALS":
                    if not (module == "pmcp.env_store" and fn.name in MAP_TOUCHERS):
                        found.append(f"{where} touches the credential map")
                    continue
                if not bound:
                    continue
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in bound
                    and node.func.attr in VALUE_READS
                ):
                    found.append(
                        f"{where} reads {node.func.value.id}.{node.func.attr}()"
                    )
                elif (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.ctx, ast.Load)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in bound
                ):
                    found.append(f"{where} reads {node.value.id}[...]")
                elif isinstance(node, ast.Call):
                    func = node.func
                    name = getattr(func, "id", getattr(func, "attr", ""))
                    if name in ("dict", "update", "ChainMap") and any(
                        isinstance(a, ast.Name) and a.id in bound for a in node.args
                    ):
                        found.append(f"{where} copies a store's values via {name}()")
                elif isinstance(node, ast.Dict) and any(
                    k is None and isinstance(v, ast.Name) and v.id in bound
                    for k, v in zip(node.keys, node.values)
                ):
                    found.append(f"{where} copies a store's values via {{**...}}")
    return found


def test_only_credential_value_reads_a_credential() -> None:
    assert credential_value_bypasses(_src_sources()) == []


def test_the_map_touchers_all_exist() -> None:
    tree = _parse(_src_sources()["pmcp.env_store"])
    names = {f.name for f in _outermost_functions(tree)}
    assert set(MAP_TOUCHERS) <= names


def test_the_credential_value_scan_sees_each_shape() -> None:
    source = (
        "from pmcp.env_store import read_store, repository_values, _REPO_CREDENTIALS\n"
        "def by_get(p):\n"
        "    values = repository_values('project', project=p)\n"
        "    return values.get('K')\n"
        "def by_index(p):\n"
        "    values = read_store('project', project=p)\n"
        "    return values['K']\n"
        "def by_items(p):\n"
        "    values = read_store('user')\n"
        "    return [v for _, v in values.items()]\n"
        "def by_copy(p):\n"
        "    values = read_store('user')\n"
        "    merged = dict(values)\n"
        "    return {**values}\n"
        "def by_map():\n"
        "    return _REPO_CREDENTIALS.get('K')\n"
        "def names_only(p):\n"
        "    values = read_store('user')\n"
        "    return sorted(values), 'K' in values, credential_value('K', repository=values)\n"
    )
    hits = credential_value_bypasses({"pmcp.x": source})
    flagged = {h.split(":")[1] for h in hits}
    assert flagged == {"by_get", "by_index", "by_items", "by_copy", "by_map"}, hits


# --------------------------------------------------------------------------- #
# Nothing moves from one store into another unchecked (Consiliency/pmcp#372
# round 5): a function that reads a store under one scope and writes a store
# under another must pass the values through env_store.copyable_from_repository.
# --------------------------------------------------------------------------- #

STORE_READERS_BY_SCOPE = frozenset(
    {"read_store_for_update", "read_store", "repository_values"}
)
STORE_WRITERS = frozenset({"write_env_file", "set_env_value"})


def _scope_expr(call: ast.Call) -> str | None:
    """The scope a store call names, as source text (first argument)."""
    if call.args:
        return ast.unparse(call.args[0])
    return None


def _writer_scopes(call: ast.Call) -> set[str]:
    name = getattr(call.func, "id", getattr(call.func, "attr", ""))
    if name == "set_env_value":
        return {s for s in [_scope_expr(call)] if s}
    scopes: set[str] = set()
    for kw in call.keywords:
        if (
            kw.arg == "confine_to"
            and isinstance(kw.value, ast.Call)
            and getattr(kw.value.func, "id", getattr(kw.value.func, "attr", ""))
            == "scope_confinement"
        ):
            scopes |= {s for s in [_scope_expr(kw.value)] if s}
    return scopes or {"?"}


def unchecked_store_copies(sources: dict[str, str]) -> list[str]:
    found: list[str] = []
    for module, source in sources.items():
        for fn in _outermost_functions(_parse(source)):
            calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
            names = {getattr(c.func, "id", getattr(c.func, "attr", "")) for c in calls}
            read_scopes = {
                s
                for c in calls
                if getattr(c.func, "id", getattr(c.func, "attr", ""))
                in STORE_READERS_BY_SCOPE
                for s in [_scope_expr(c)]
                if s and s != "'user'"
            }
            write_scopes: set[str] = set()
            for c in calls:
                if getattr(c.func, "id", getattr(c.func, "attr", "")) in STORE_WRITERS:
                    write_scopes |= _writer_scopes(c)
            if not write_scopes or not read_scopes:
                continue
            crossing = read_scopes - write_scopes
            if crossing and "copyable_from_repository" not in names:
                found.append(
                    f"{module}:{fn.name} writes a store from {sorted(crossing)} "
                    "without copyable_from_repository"
                )
    return found


def test_every_cross_store_copy_passes_the_gate() -> None:
    assert unchecked_store_copies(_src_sources()) == []


def test_the_copy_scan_sees_each_shape() -> None:
    source = (
        "def bare_sync(a, b, p):\n"
        "    src = read_store_for_update(a, p)\n"
        "    write_env_file(p, src, confine_to=scope_confinement(b, p))\n"
        "def via_set(a, p):\n"
        "    for k, v in read_store('project', project=p).items():\n"
        "        set_env_value('user', k, v)\n"
        "def checked(a, b, p):\n"
        "    src, _ = copyable_from_repository(read_store_for_update(a, p), 'x')\n"
        "    write_env_file(p, src, confine_to=scope_confinement(b, p))\n"
        "def same_store(scope, p):\n"
        "    values = read_store_for_update(scope, p)\n"
        "    write_env_file(p, values, confine_to=scope_confinement(scope, p))\n"
    )
    flagged = {
        line.split(" ")[0] for line in unchecked_store_copies({"pmcp.x": source})
    }
    assert flagged == {"pmcp.x:bare_sync", "pmcp.x:via_set"}


# --------------------------------------------------------------------------- #
# Every credential consumer answers for the project it serves
# (Consiliency/pmcp#372 round 9). A lookup with no root answers for the SERVED
# root (env_store.serve_project_root: --project, else the discovered root), so
# a consumer that knows a more specific root -- a gateway's project_root, an
# install child's, a config load's -- must pass it. Each call to
# credential_value passes ``root=``, each call to credential_lookup passes a
# project, and -- since round 10 -- each call to a function that FORWARDS its
# own project parameter to one of them (derived to a fixed point:
# consumer_gates) passes that parameter too, or the function holding the call
# is listed here with its reason.
# --------------------------------------------------------------------------- #

#: ``(module, function)`` -> why it answers for the served root. Asserted exact.
#: Empty since round 10: the last entry, ``config.loader._credential_value``
#: (``manifest_server_to_config``'s lookup), now takes the caller's project.
SERVED_ROOT_CONSUMERS: dict[tuple[str, str], str] = {}

#: The two lookups and the parameter that names the project:
#: ``name -> (parameter, positional index or None for keyword-only)``.
_BASE_GATES: dict[str, tuple[str, int | None]] = {
    "credential_value": ("root", None),
    "credential_lookup": ("project", 0),
    # A tenant or project store's values for credential_value(repository=...):
    # the store of the project the caller serves (board round 10 codex F001).
    "repository_values": ("project", None),
}


def _qualified_functions(
    tree: ast.AST, prefix: str = ""
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((prefix + node.name, node))
        elif isinstance(node, ast.ClassDef):
            found.extend(_qualified_functions(node, f"{prefix}{node.name}."))
    return found


def _parameters(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, int | None]:
    positional = [a.arg for a in fn.args.posonlyargs + fn.args.args]
    found: dict[str, int | None] = {name: i for i, name in enumerate(positional)}
    found.update({a.arg: None for a in fn.args.kwonlyargs})
    return found


def _called_name(func: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(func, ast.Name):
        return aliases.get(func.id, func.id)
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _passed(call: ast.Call, gate: tuple[str, int | None]) -> ast.AST | None:
    """The expression a call passes for the gated parameter, if any."""
    name, index = gate
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    if index is not None and len(call.args) > index:
        return call.args[index]
    return None


def _module_trees(sources: dict[str, str]) -> list[tuple[str, ast.AST, dict[str, str]]]:
    trees = []
    for module, source in sources.items():
        tree = _parse(source)
        aliases = {
            alias.asname or alias.name: alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        trees.append((module, tree, aliases))
    return trees


def consumer_gates(sources: dict[str, str]) -> dict[str, tuple[str, int | None]]:
    """The lookups, plus every function that FORWARDS its own project to one.

    Derived, to a fixed point: a function that passes one of its own
    parameters as a gated function's project is itself gated on that
    parameter (``build_remote_header_env_lookup(project_root)``,
    ``collect_remote_header_diagnostics(config, project_root)``,
    ``manifest_server_to_config(server, project_root)``, ...). A caller that
    omits it answers for the served root without saying so -- the shape of
    ``pmcp doctor --project B`` judging B's headers against A (board round 9,
    grok F001).
    """
    gates = dict(_BASE_GATES)
    trees = _module_trees(sources)
    changed = True
    while changed:
        changed = False
        for _module, tree, aliases in trees:
            for qualified, fn in _qualified_functions(tree):
                name = qualified.rsplit(".", 1)[-1]
                if name in gates:
                    continue
                params = _parameters(fn)
                for node in ast.walk(fn):
                    if not isinstance(node, ast.Call):
                        continue
                    called = _called_name(node.func, aliases)
                    if called not in gates or called == name:
                        continue
                    value = _passed(node, gates[called])
                    if isinstance(value, ast.Name) and value.id in params:
                        index = params[value.id]
                        if index is not None and qualified != name:
                            index -= 1  # a method's own self
                        gates[name] = (value.id, index)
                        changed = True
                        break
    return gates


def root_less_consumers(sources: dict[str, str]) -> set[tuple[str, str]]:
    """``(module, function)`` of each call site that names no project."""
    gates = consumer_gates(sources)
    found: set[tuple[str, str]] = set()
    for module, tree, aliases in _module_trees(sources):
        for name, fn in _qualified_functions(tree):
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                called = _called_name(node.func, aliases)
                if called is None or called not in gates:
                    continue
                if _passed(node, gates[called]) is None:
                    found.add((module, name))
    return found


def test_every_credential_consumer_answers_for_the_project_it_serves() -> None:
    assert root_less_consumers(_src_sources()) == set(SERVED_ROOT_CONSUMERS)


def test_the_consumer_scan_sees_each_shape() -> None:
    source = (
        "from pmcp.env_store import credential_value as cv, credential_lookup\n"
        "from pmcp import env_store\n"
        "def bare(k):\n"
        "    return cv(k)\n"
        "def by_attribute(k):\n"
        "    return env_store.credential_value(k)\n"
        "def lookup_for_nobody():\n"
        "    return credential_lookup()\n"
        "class Tools:\n"
        "    def check(self, k):\n"
        "        return bool(cv(k))\n"
        "    def fine(self, k):\n"
        "        return cv(k, root=self._project_root)\n"
        "def fine_lookup(p):\n"
        "    return credential_lookup(p)(k) or credential_lookup(project=p)(k)\n"
        "def nested(k):\n"
        "    return [x for x in map(lambda key: cv(key), [k])]\n"
        # A wrapper that forwards its own project is gated on it, and so is a
        # wrapper of that wrapper; a caller that omits it is flagged.
        "def headers_for(config, project_root=None):\n"
        "    return credential_lookup(project_root)\n"
        "def doctor(config, project_root=None):\n"
        "    return headers_for(config, project_root)\n"
        "def doctor_for_nobody(config):\n"
        "    return doctor(config)\n"
        "def doctor_by_keyword(config, p):\n"
        "    return doctor(config, project_root=p)\n"
        "class Jobs:\n"
        "    def start(self, server, project_root=None):\n"
        "        return headers_for(server, project_root)\n"
        "def start_for_nobody(jobs, s):\n"
        "    return jobs.start(s)\n"
        "def start_for_one(jobs, s, p):\n"
        "    return jobs.start(s, p)\n"
        # The tenant/project store reader: the caller's project, named.
        "def tenant_for_nobody(t):\n"
        "    return repository_values('tenant', tenant_id=t)\n"
        "def tenant_for_one(t, p):\n"
        "    return repository_values('tenant', project=p, tenant_id=t)\n"
    )
    sources = {"pmcp.x": source}
    assert consumer_gates(sources)["doctor"] == ("project_root", 1)
    assert consumer_gates(sources)["start"] == ("project_root", 1)
    assert root_less_consumers(sources) == {
        ("pmcp.x", "bare"),
        ("pmcp.x", "by_attribute"),
        ("pmcp.x", "lookup_for_nobody"),
        ("pmcp.x", "Tools.check"),
        ("pmcp.x", "nested"),
        ("pmcp.x", "doctor_for_nobody"),
        ("pmcp.x", "start_for_nobody"),
        ("pmcp.x", "tenant_for_nobody"),
    }


# --------------------------------------------------------------------------- #
# One root per process (Consiliency/pmcp#372 round 12, board round 11 codex
# F001): ``pmcp --project B`` started inside A paired A's manifest overlay --
# found by walking up from the working directory -- with B's credentials. Every
# project-scoped input (credentials, tenant stores, ``.mcp.json``, the manifest
# overlay, the project policy) follows the served root or an explicit project.
# Deriving a project from the working directory happens in ONE function, which
# sets the served root; any other use of the working directory is listed here
# with the reason it is not a project-scoped input.
# --------------------------------------------------------------------------- #

#: Calls that read the working directory, or walk up from one for a project.
CWD_CALLS = frozenset({"cwd", "getcwd", "find_project_root"})

#: ``(module, function)`` -> why it may read the working directory. Asserted
#: exact.
CWD_READERS = {
    ("pmcp.env_store", "_discover_project_root"): (
        "THE derivation of a project from where pmcp started: its answer is "
        "the served root (serve_project_root)"
    ),
    ("pmcp.home_identity", "is_operator_owned"): (
        "a RELATIVE directory is judged by its absolute spelling, as the "
        "kernel resolves it; reads no project input"
    ),
    ("pmcp.env_store", "resolve_project_root"): (
        "an explicit RELATIVE --project is joined to the working directory, "
        "as the operator typed it"
    ),
    ("pmcp.trust_store", "_capture_launch_directory"): (
        "the residency guard also refuses a store inside the directory pmcp was "
        "LAUNCHED in, captured once and never re-read (Consiliency/pmcp#372 "
        "round 18); adding roots only refuses more"
    ),
    ("pmcp.manifest.environment", "get_environment_info"): (
        "reports the working directory as a fact about the environment; no "
        "project input is read from it"
    ),
    ("pmcp.manifest.npm_resolver", "_has_local_prefix"): (
        "npm itself resolves from the spawn's working directory, so the "
        "shadow check must look where npm will"
    ),
}


def cwd_readers(sources: dict[str, str]) -> set[tuple[str, str]]:
    """``(module, function)`` of every function that reads the working directory."""
    found: set[tuple[str, str]] = set()
    for module, source in sources.items():
        tree = _parse(source)
        for name, fn in _qualified_functions(tree):
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                called = getattr(func, "attr", getattr(func, "id", None))
                if called in CWD_CALLS:
                    found.add((module, name))
                    break
    return found


def test_only_the_served_root_derivation_reads_the_working_directory() -> None:
    assert cwd_readers(_src_sources()) == set(CWD_READERS)


def test_the_cwd_scan_sees_each_shape() -> None:
    source = (
        "import os\n"
        "from pathlib import Path\n"
        "from pmcp.config.loader import find_project_root\n"
        "def by_path():\n"
        "    return Path.cwd() / '.mcp.json'\n"
        "def by_os():\n"
        "    return os.getcwd()\n"
        "def by_walk():\n"
        "    return find_project_root(Path('.'))\n"
        "class Policy:\n"
        "    def discover(self):\n"
        "        return [p for p in [Path.cwd()]]\n"
        "def fine(root):\n"
        "    return root / '.mcp.json'\n"
    )
    assert cwd_readers({"pmcp.x": source}) == {
        ("pmcp.x", "by_path"),
        ("pmcp.x", "by_os"),
        ("pmcp.x", "by_walk"),
        ("pmcp.x", "Policy.discover"),
    }


# --------------------------------------------------------------------------- #
# Two questions, two values (Consiliency/pmcp#372 round 13, board round 12
# codex F001). ``project_scope_root`` classifies config SOURCES -- its ``None``
# means "the home directory: no project config source here". Fed to a
# credential lookup, that ``None`` meant "the served project", so an explicitly
# named home project got another project's credential. A value derived from a
# classification helper never reaches a credential lookup's project parameter.
# --------------------------------------------------------------------------- #

#: Functions whose answer classifies a config source, not a credential root.
CLASSIFIERS = frozenset({"project_scope_root", "_project_scope_root"})


def classification_leaks(sources: dict[str, str]) -> list[str]:
    """Every call that hands a classifier's answer to a credential lookup."""
    gates = consumer_gates(sources)
    found: list[str] = []
    for module, tree, aliases in _module_trees(sources):
        for name, fn in _qualified_functions(tree):
            classified: set[str] = set()
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Call)
                    and _called_name(node.value.func, aliases) in CLASSIFIERS
                ):
                    classified |= {
                        t.id for t in node.targets if isinstance(t, ast.Name)
                    }
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                called = _called_name(node.func, aliases)
                if called is None or called not in gates:
                    continue
                value = _passed(node, gates[called])
                direct = (
                    isinstance(value, ast.Call)
                    and _called_name(value.func, aliases) in CLASSIFIERS
                )
                named = isinstance(value, ast.Name) and value.id in classified
                if direct or named:
                    found.append(f"{module}:{name}:{node.lineno} {called}")
    return found


def test_no_classification_reaches_a_credential_lookup() -> None:
    assert classification_leaks(_src_sources()) == []


def test_the_classification_scan_sees_each_shape() -> None:
    source = (
        "from pmcp.env_store import credential_value, project_scope_root\n"
        "def by_name(k, project_root):\n"
        "    resolved = project_scope_root(project_root)\n"
        "    return credential_value(k, root=resolved)\n"
        "def inline(k, project_root):\n"
        "    return credential_value(k, root=project_scope_root(project_root))\n"
        "def through_a_wrapper(k, project_root):\n"
        "    resolved = _project_scope_root(project_root)\n"
        "    return lookup_for(resolved)\n"
        "def lookup_for(root):\n"
        "    return lambda k: credential_value(k, root=root)\n"
        "def fine(k, project_root):\n"
        "    resolved = project_scope_root(project_root)\n"
        "    if resolved:\n"
        "        pass\n"
        "    return credential_value(k, root=project_root)\n"
    )
    flagged = {line.split(":")[1] for line in classification_leaks({"pmcp.x": source})}
    assert flagged == {"by_name", "inline", "through_a_wrapper"}


# --------------------------------------------------------------------------- #
# No credential answer before the user store is in the environment
# (Consiliency/pmcp#372 round 15, board round 14 codex F001). Every credential
# read goes through credential_value (the inventories above), so the rule is
# held at that one gate: its first statement is ensure_startup_load(), and the
# startup load it runs never re-chooses the served root (round 14).
# --------------------------------------------------------------------------- #


def _function(module: str, name: str) -> ast.FunctionDef:
    tree = _parse(_src_sources()[module])
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _first_statement_calls(fn: ast.FunctionDef, callee: str) -> bool:
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # the docstring
    first = body[0] if body else None
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Call)
        and getattr(first.value.func, "id", None) == callee
    )


def test_the_credential_gate_loads_the_user_store_first() -> None:
    fn = _function("pmcp.env_store", "credential_value")
    assert _first_statement_calls(fn, "ensure_startup_load")


def test_the_startup_load_never_rechooses_the_served_root() -> None:
    fn = _function("pmcp.cli", "load_startup_env")
    called = {
        getattr(node.func, "id", getattr(node.func, "attr", None))
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
    }
    assert "ensure_served_project_root" in called
    assert "serve_project_root" not in called
    assert "set_default_root" not in called


# --------------------------------------------------------------------------- #
# A long-lived object binds its project when it is built (Consiliency/pmcp#372
# round 16, board round 15 claude F001). An object that takes a project_root
# holds configs and endpoints across calls; it resolves a CONCRETE root in
# __init__ (env_store.bind_project_root) and hands exactly that to every
# credential lookup and project input it reads, so a chdir after construction
# cannot pair its endpoint with another project's credential.
# --------------------------------------------------------------------------- #

#: The long-lived objects, derived below: classes whose __init__ takes a
#: project_root and that read credentials or project inputs. Asserted exact.
BOUND_OBJECTS = {
    ("pmcp.server", "GatewayServer"),
    ("pmcp.tools.handlers", "GatewayTools"),
    ("pmcp.client.manager", "ClientManager"),
    ("pmcp.policy.policy", "PolicyManager"),
}


def _is_self_root(node: ast.AST | None) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "_project_root"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def bound_object_violations(sources: dict[str, str]) -> tuple[set, list[str]]:
    gates = consumer_gates(sources)
    objects: set[tuple[str, str]] = set()
    found: list[str] = []
    for module, tree, aliases in _module_trees(sources):
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            init = next(
                (
                    n
                    for n in cls.body
                    if isinstance(n, ast.FunctionDef) and n.name == "__init__"
                ),
                None,
            )
            if init is None or "project_root" not in _parameters(init):
                continue
            objects.add((module, cls.name))
            binds = any(
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    _is_self_root(t)
                    for t in (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                )
                and isinstance(node.value, ast.Call)
                and _called_name(node.value.func, aliases) == "bind_project_root"
                for node in ast.walk(init)
            )
            if not binds:
                found.append(f"{module}:{cls.name} does not bind its project root")
            for method in cls.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if method.name == "__init__":
                    continue
                for node in ast.walk(method):
                    if not isinstance(node, ast.Call):
                        continue
                    called = _called_name(node.func, aliases)
                    if called is None or called not in gates:
                        continue
                    if not _is_self_root(_passed(node, gates[called])):
                        found.append(
                            f"{module}:{cls.name}.{method.name}:{node.lineno} "
                            f"{called} is not handed self._project_root"
                        )
    return objects, found


def test_every_long_lived_object_binds_and_uses_one_root() -> None:
    objects, found = bound_object_violations(_src_sources())
    assert objects == BOUND_OBJECTS
    assert found == []


def test_the_bound_object_scan_sees_each_shape() -> None:
    source = (
        "from pmcp.env_store import bind_project_root, credential_value\n"
        "class Unbound:\n"
        "    def __init__(self, project_root=None):\n"
        "        self._project_root = project_root\n"
        "class Rebinds:\n"
        "    def __init__(self, project_root=None):\n"
        "        self._project_root = bind_project_root(project_root)\n"
        "    def check(self, k):\n"
        "        return credential_value(k, root=None)\n"
        "class Good:\n"
        "    def __init__(self, project_root=None):\n"
        "        self._project_root = bind_project_root(project_root)\n"
        "    def check(self, k):\n"
        "        return credential_value(k, root=self._project_root)\n"
    )
    objects, found = bound_object_violations({"pmcp.x": source})
    assert objects == {("pmcp.x", n) for n in ("Unbound", "Rebinds", "Good")}
    assert {line.split(" ")[0].split(":")[1].split(".")[0] for line in found} == {
        "Unbound",
        "Rebinds",
    }


# --------------------------------------------------------------------------- #
# The residency guard is a project-scoped input too (Consiliency/pmcp#372 round
# 17, boards round 16 grok/codex F001): a gateway built in A whose cwd moved to
# B judged A's files with only the cwd arm, so a trust store resident in A
# approved A's own .mcp.json. Every approval read names the project being read
# -- the bound root in a long-lived object -- and the guard also walks up from
# the file being approved; the launch checkout stays an extra refusal arm.
# --------------------------------------------------------------------------- #

#: Approval reads and the parameter that names the project being read.
RESIDENCY_GATES: dict[str, tuple[str, int | None]] = {
    "read_and_gate": ("project_root", None),
    "gate_bytes": ("project_root", None),
    "is_approved": ("project_root", None),
    "is_approved_resolved": ("project_root", None),
    "is_package_approved": ("project_root", None),
    "package_approvals_path": ("project_root", None),
}

#: ``(module, function)`` -> why it may read approvals naming no project.
#: Asserted exact.
UNSCOPED_APPROVAL_READS = {
    ("pmcp.package_approvals", "approve_package"): (
        "`pmcp approve-package`, the operator's own verb: judged by the "
        "served root and the launch checkout, as every `pmcp trust` verb is"
    ),
    ("pmcp.package_approvals", "revoke_package"): "the operator's own verb",
    ("pmcp.package_approvals", "list_package_approvals"): "the operator's own verb",
}


def unscoped_approval_reads(sources: dict[str, str]) -> tuple[set, list[str]]:
    found: set[tuple[str, str]] = set()
    unbound: list[str] = []
    bound_classes = {cls for _m, cls in BOUND_OBJECTS}
    for module, tree, aliases in _module_trees(sources):
        for name, fn in _qualified_functions(tree):
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                called = _called_name(node.func, aliases)
                if called not in RESIDENCY_GATES:
                    continue
                value = _passed(node, RESIDENCY_GATES[called])
                if value is None:
                    found.add((module, name))
                elif name.split(".")[0] in bound_classes and not _is_self_root(value):
                    unbound.append(f"{module}:{name}:{node.lineno} {called}")
    return found, unbound


def test_every_approval_read_names_the_project_it_reads() -> None:
    found, unbound = unscoped_approval_reads(_src_sources())
    assert found == set(UNSCOPED_APPROVAL_READS)
    assert unbound == []


def test_the_guard_judges_the_approved_files_own_checkout() -> None:
    fn = _function("pmcp.trust_store", "is_approved_resolved")
    calls = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "trust_store_path"
    ]
    assert calls and all(
        any(
            k.arg == "also"
            and isinstance(k.value, ast.Call)
            and getattr(k.value.func, "id", None) == "judged_roots"
            for k in c.keywords
        )
        for c in calls
    )


def test_the_approval_scan_sees_each_shape() -> None:
    source = (
        "from pmcp.project_consent import read_and_gate\n"
        "def unscoped(p):\n"
        "    return read_and_gate(p, 'project_policy')\n"
        "def scoped(p, root):\n"
        "    return read_and_gate(p, 'project_policy', project_root=root)\n"
        "class PolicyManager:\n"
        "    def load(self, p):\n"
        "        return read_and_gate(p, 'project_policy', project_root=None)\n"
    )
    found, unbound = unscoped_approval_reads({"pmcp.x": source})
    assert found == {("pmcp.x", "unscoped")}
    assert [line.split(" ")[0].split(":")[1] for line in unbound] == [
        "PolicyManager.load"
    ]


# --------------------------------------------------------------------------- #
# Home is compared by identity, never spelling (Consiliency/pmcp#372 round 19,
# board round 18 codex F001): HOME spelled through a link made a launch
# directory holding a link to that home look like home's ancestor. In the
# trust, residency and store code, nothing derived from Path.home() is compared
# or walked up by path; pmcp.home_identity does it by (st_dev, st_ino).
# --------------------------------------------------------------------------- #

HOME_SENSITIVE_MODULES = (
    "pmcp.trust_store",
    "pmcp.package_approvals",
    "pmcp.env_store",
    "pmcp.project_consent",
    "pmcp.atomic_write",
    "pmcp.config.loader",
    "pmcp.manifest.loader",
)


def _mentions_home(node: ast.AST, home_names: set[str]) -> bool:
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "home"
        ):
            return True
        if isinstance(sub, ast.Name) and sub.id in home_names:
            return True
    return False


def home_spelling_comparisons(sources: dict[str, str]) -> list[str]:
    found: list[str] = []
    for module in HOME_SENSITIVE_MODULES:
        tree = _parse(sources[module])
        for name, fn in _qualified_functions(tree):
            home_names = {
                t.id
                for node in ast.walk(fn)
                if isinstance(node, ast.Assign) and _mentions_home(node.value, set())
                for t in node.targets
                if isinstance(t, ast.Name)
            }
            for node in ast.walk(fn):
                compared = (
                    isinstance(node, ast.Compare)
                    # `x is None` asks whether something is set, not where.
                    and not all(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops)
                    and any(
                        _mentions_home(side, home_names)
                        for side in (node.left, *node.comparators)
                    )
                )
                walked = (
                    isinstance(node, ast.Attribute)
                    and node.attr in ("parents", "parent")
                    and _mentions_home(node.value, home_names)
                )
                relative = (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("is_relative_to", "relative_to", "samefile")
                    and (
                        _mentions_home(node.func.value, home_names)
                        or any(_mentions_home(a, home_names) for a in node.args)
                    )
                )
                if compared or walked or relative:
                    found.append(f"{module}:{name}:{node.lineno}")
    return found


def test_home_is_compared_by_identity_only() -> None:
    assert home_spelling_comparisons(_src_sources()) == []


def test_the_home_scan_sees_each_shape() -> None:
    source = (
        "from pathlib import Path\n"
        "def eq(p):\n"
        "    return p == Path.home().resolve()\n"
        "def named(p):\n"
        "    home = Path.home()\n"
        "    return p in home.parents\n"
        "def rel(p):\n"
        "    return p.is_relative_to(Path.home())\n"
        "def fine(p):\n"
        "    return Path.home() / '.config'\n"
    )
    sources = {m: "" for m in HOME_SENSITIVE_MODULES}
    sources["pmcp.trust_store"] = source
    flagged = {line.split(":")[1] for line in home_spelling_comparisons(sources)}
    assert flagged == {"eq", "named", "rel"}


# --------------------------------------------------------------------------- #
# Residency is not project discovery (Consiliency/pmcp#372 round 20, boards
# round 19 grok/codex F001). find_project_root stops at the home directory, so
# reusing it for residency hid a checkout that encloses home. The residency
# modules never call project discovery or classification; they walk up to /
# themselves (trust_store._enclosing_checkouts).
# --------------------------------------------------------------------------- #

RESIDENCY_MODULES = (
    "pmcp.trust_store",
    "pmcp.package_approvals",
    "pmcp.project_consent",
    "pmcp.home_identity",
)
PROJECT_DISCOVERY = frozenset(
    {
        "find_project_root",
        "project_scope_root",
        "_project_scope_root",
        "resolve_project_root",
        "_discover_project_root",
    }
)


def discovery_in_residency(sources: dict[str, str]) -> list[str]:
    found: list[str] = []
    for module, tree, aliases in _module_trees(sources):
        if module not in RESIDENCY_MODULES:
            continue
        for name, fn in _qualified_functions(tree):
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and _called_name(node.func, aliases) in PROJECT_DISCOVERY
                ):
                    found.append(f"{module}:{name}:{node.lineno}")
    return found


def test_residency_never_uses_project_discovery() -> None:
    assert discovery_in_residency(_src_sources()) == []


def test_the_residency_discovery_scan_sees_each_shape() -> None:
    source = (
        "from pmcp.config.loader import find_project_root\n"
        "def walk(p):\n"
        "    return find_project_root(p)\n"
        "def classify(p):\n"
        "    from pmcp.env_store import project_scope_root\n"
        "    return project_scope_root(p)\n"
        "def fine(p):\n"
        "    return p.parent\n"
    )
    flagged = {
        line.split(":")[1]
        for line in discovery_in_residency({"pmcp.trust_store": source})
    }
    assert flagged == {"walk", "classify"}


# --------------------------------------------------------------------------- #
# One gate for every home-scoped read (Consiliency/pmcp#372 round 22, board
# round 21 grok F001): with a repository-shipped `home -> .`, the user config,
# the user manifest overlay and the base policy were read from the checkout as
# the operator's. Every HOME-derived path goes through pmcp.home_identity's
# gate (home_path, optional_home_path, operator_home, optional_operator_home),
# which answers only while HOME is operator-owned. Nothing else derives HOME;
# spelled_home is allowed only for module-level documentation constants.
# --------------------------------------------------------------------------- #


def home_derivations(sources: dict[str, str]) -> list[str]:
    found: list[str] = []
    for module, source in sources.items():
        if module == "pmcp.home_identity":
            continue
        tree = _parse(source)
        in_function: set[int] = set()
        for _name, fn in _qualified_functions(tree):
            in_function |= {id(n) for n in ast.walk(fn)}
        for node in ast.walk(tree):
            where = f"{module}:{getattr(node, 'lineno', '?')}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "home":
                    found.append(f"{where} .home()")
                elif (
                    node.func.attr == "expanduser"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and str(node.args[0].value).startswith("~")
                ):
                    found.append(f"{where} expanduser('~')")
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", None))
                == "spelled_home"
                and id(node) in in_function
            ):
                found.append(f"{where} spelled_home() at runtime")
            if (
                isinstance(node, (ast.Subscript, ast.Call))
                and "HOME"
                in {
                    c.value
                    for c in ast.walk(node)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)
                }
                and "environ" in ast.unparse(node)
                and not ast.unparse(node).startswith(("_NEVER_LOADED",))
            ):
                found.append(f"{where} HOME read from the environment")
    return found


def test_home_is_derived_only_by_the_gate() -> None:
    assert home_derivations(_src_sources()) == []


def test_the_home_derivation_scan_sees_each_shape() -> None:
    source = (
        "import os\n"
        "from pathlib import Path\n"
        "CONST = spelled_home() / '.x'\n"
        "def a():\n"
        "    return Path.home() / '.mcp.json'\n"
        "def b():\n"
        "    return os.path.expanduser('~/.claude')\n"
        "def c():\n"
        "    return os.environ.get('HOME')\n"
        "def d():\n"
        "    return spelled_home() / '.y'\n"
        "def fine():\n"
        "    return home_path('.config')\n"
    )
    found = home_derivations({"pmcp.x": source})
    lines = sorted(int(line.split(":")[1].split(" ")[0]) for line in found)
    assert lines == [5, 7, 9, 11]


# --------------------------------------------------------------------------- #
# Nothing remembers a HOME-derived answer (Consiliency/pmcp#372 round 30,
# board round 29 codex F001: the pinned user store skipped the gate). A gate's
# answer -- a path under HOME, or a verdict about HOME -- may be used, never
# kept: not in a module global, an attribute, a default argument or a cached
# function. The one remembered home-scoped location is a ``HomePin``, which
# judges its HOME again on every use. home_identity's own verdict cache
# revalidates every read before reuse and is exempt by construction.
# --------------------------------------------------------------------------- #

#: Calls whose result is a HOME-derived path or verdict.
HOME_GATE_CALLS = frozenset(
    {
        "home_path",
        "optional_home_path",
        "operator_home",
        "optional_operator_home",
        "examinable_home",
        "home_is_operators",
        "is_home",
        "is_operator_owned",
        "pin_user_store_path",
        "default_user_config_paths",
        "default_user_policy_paths",
        "_effective_user_policy_paths",
        "_user_policy_paths",
        "_default_policy_paths",
        "trust_store_path",
        "package_approvals_path",
        "default_registry_cache_path",
        "_uv_tool_dir",
        "resolve_scope_path",
    }
)
_CACHING_DECORATORS = frozenset({"cache", "lru_cache", "cached_property"})


def _gate_calls(node: ast.AST) -> list[str]:
    names = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name in HOME_GATE_CALLS:
                names.append(name)
    return names


def _is_pin(value: ast.AST | None) -> bool:
    return (
        isinstance(value, ast.Call)
        and (getattr(value.func, "id", None) or getattr(value.func, "attr", None))
        == "HomePin"
    )


def home_memos(sources: dict[str, str]) -> list[str]:
    found: list[str] = []
    for module, source in sources.items():
        if module == "pmcp.home_identity":
            continue
        tree = _parse(source)
        for node in tree.body:  # module level
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                if _gate_calls(node.value) and not _is_pin(node.value):
                    found.append(f"{module}:{node.lineno} module global")
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = {
                getattr(d, "id", None)
                or getattr(d, "attr", None)
                or getattr(getattr(d, "func", None), "id", None)
                or getattr(getattr(d, "func", None), "attr", None)
                for d in fn.decorator_list
            }
            if decorators & _CACHING_DECORATORS and _gate_calls(fn):
                found.append(f"{module}:{fn.lineno} cached {fn.name}")
            for default in [*fn.args.defaults, *fn.args.kw_defaults]:
                if default is not None and _gate_calls(default):
                    found.append(f"{module}:{fn.lineno} default argument of {fn.name}")
            globals_ = {
                name
                for sub in ast.walk(fn)
                if isinstance(sub, ast.Global)
                for name in sub.names
            }
            for sub in ast.walk(fn):
                if not isinstance(sub, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    continue
                value = sub.value
                if value is None or not _gate_calls(value) or _is_pin(value):
                    continue
                targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                for target in targets:
                    if isinstance(target, ast.Attribute):
                        found.append(
                            f"{module}:{sub.lineno} kept in {ast.unparse(target)}"
                        )
                    elif isinstance(target, ast.Name) and target.id in globals_:
                        found.append(
                            f"{module}:{sub.lineno} kept in global {target.id}"
                        )
    return found


def test_no_home_derived_answer_is_remembered() -> None:
    assert home_memos(_src_sources()) == []


def test_the_home_memo_scan_sees_each_shape() -> None:
    source = (
        "import functools\n"
        "KEPT = home_path('.x')\n"
        "PIN = HomePin('.y')\n"
        "_G = None\n"
        "def a():\n"
        "    global _G\n"
        "    _G = optional_home_path('.z')\n"
        "def b(self):\n"
        "    self.where = pin_user_store_path()\n"
        "@functools.lru_cache(maxsize=1)\n"
        "def c():\n"
        "    return default_user_config_paths()\n"
        "def d(where=operator_home()):\n"
        "    return where\n"
        "def e():\n"
        "    global _G\n"
        "    _G = HomePin('.w')\n"
        "def fine():\n"
        "    return home_path('.config')\n"
    )
    found = home_memos({"pmcp.x": source})
    lines = sorted(int(line.split(":")[1].split(" ")[0]) for line in found)
    assert lines == [2, 7, 9, 11, 13]
