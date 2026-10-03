"""Every place `src/pmcp` turns an exception into text goes through the
value-free renderers (Consiliency/pmcp#297).

A pydantic or jsonschema `ValidationError` renders the rejected value, so
wherever pmcp renders an exception that may be one -- a response field, a
log line or its traceback, an audit or audit-event field -- it must use
`exception_text` / `safe_exc_info` (or a renderer built on them). This test
enforces that statically, over every module in `src/pmcp`, the CLI included
(rev 6: `pmcp refresh` and the config commands read files and downstreams).

The scanner tracks exceptions through a function, not just an `except`
body (rev 3; the rev 2 board seat found 25 constructs rev 2's scanner
missed). Within each function (and the module body), an **exception name**
is:

- an `except` clause's name, if a type it catches -- resolved in the
  module's own namespace -- is a base of pydantic's or jsonschema's
  `ValidationError` (an unresolvable type counts as one);
- a name assigned from `<x>.exception(...)` anywhere in the value
  (`fut.exception()`, `t.exception() if ... else None`);
- a name `isinstance`-tested anywhere in a condition against such a type;
- a name bound from `gather(..., return_exceptions=True)`, and a loop
  variable over any exception name or over `<x>.exceptions`;
- an alias of any of these (`last = e`), wherever it is later used.

A **use** of an exception name is safe only as: an argument to a renderer
(by its bare name, imported from `pmcp` -- `self._sanitize_error` is the one
method), `type()`/`isinstance()`, `raise`, a comparison or truthiness test,
an alias to a plain name, a loop's iterable, or an attribute that carries no
text. Anything else -- an f-string, `str()`/`repr()`, a `%` argument, a
store into an attribute or container, a return, a text-bearing attribute
(`args`, `errors()`, jsonschema's `path`/`json_path`, `__notes__`, ...), or
an unknown callee -- is a sink. Also sinks, anywhere: `exc_info=` not
through `safe_exc_info`, `logger.exception`, `logging.exception`,
`makeRecord`/`handle`, `sys.exc_info()`/`sys.exception()`, and every
`traceback` renderer.
"""

from __future__ import annotations

import ast
import re
import builtins
import importlib
import typing
from pathlib import Path
from typing import Any

import json

import jsonschema
import pydantic
import yaml
import pytest

#: Exceptions whose own text can carry the input they rejected: validation
#: errors, and (rev 6) the parse errors of every structured-text parser pmcp
#: uses -- the same set `exception_text` renders structurally.
_VALIDATION_ERRORS = (
    pydantic.ValidationError,
    jsonschema.ValidationError,
    yaml.YAMLError,
    json.JSONDecodeError,
)

#: Renderers, called by their bare name.
_RENDERERS = {
    "exception_text",
    "safe_exc_info",
    "safe_traceback_text",
    "describe_exception",
    "message_text",
    "sanitize_auth_diagnostic",
    "describe_argument_error",
    "describe_schema_error",
    "describe_model_error",
    "model_error_path",
    "schema_error_path",
    "schema_error_keyword",
}
#: Where a renderer that is not in `pmcp.argument_errors` is defined; its own
#: body is scanned like any other.
_RENDERER_HOMES = {
    "describe_exception": "client/manager.py",
    "sanitize_auth_diagnostic": "auth.py",
}
#: The one renderer called as a method.
_RENDERER_METHODS = {"_sanitize_error"}
#: Callees that receive an exception and do not render its text, each read:
_NON_RENDERING_CALLEES = {
    # manager.py: a boolean predicate over the message.
    "_is_protocol_version_initialize_error",
    # manager.py: hands the exception to the awaiting connect caller, whose
    # own `except` is checked here like any other.
    "set_exception",
    # policy.py: renders its `error` argument with `exception_text`.
    "_warn_unparseable",
    # scoped_advisor_audit.py (#296): records path and keyword only.
    "record_rejected_arguments",
    # parsing.py (rev 7): reads a MarkedYAMLError's mark line and column only.
    "_yaml_position",
    # server.py (rev 12): a predicate, whether a message or `data` carries
    # what the chain rejected; it renders nothing.
    "carries_rejected_value",
    # manager.py: flattens a group into leaves; its callers render each leaf
    # with `exception_text` (`describe_exception`).
    "_iter_leaf_exceptions",
    # auth.py: see `_NON_RENDERING_FUNCTIONS`.
    "parse_url_elicitation_error",
}
#: Functions that read an exception's text to parse it and return no text.
_NON_RENDERING_FUNCTIONS = {
    # auth.py: finds a JSON-RPC -32042 payload in `args[0]` / `str()` and
    # returns structured `UrlElicitationInfo` (URLs the server sent).
    "parse_url_elicitation_error",
    # manager.py: a predicate (above).
    "_is_protocol_version_initialize_error",
    # manager.py: yields leaves; never renders.
    "_iter_leaf_exceptions",
}
#: Attributes that carry an exception's text or the rejected value.
_TEXT_ATTRIBUTES = {
    "args",
    "message",
    "errors",
    "json",
    "instance",
    "validator_value",
    "context",
    "cause",
    "schema",
    "path",
    "relative_path",
    "absolute_path",
    "json_path",
    "schema_path",
    "__notes__",
    "doc",
    "msg",
    "exceptions",
    "__cause__",
    "__context__",
    "__traceback__",
    "__str__",
    "__repr__",
    "__dict__",
    "add_note",
}
_TRACEBACK_RENDERERS = {
    "format_exc",
    "format_exception",
    "format_exception_only",
    "print_exc",
    "print_exception",
    "TracebackException",
}


def _sources() -> list[Path]:
    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if not any(part in ("baml_client",) for part in path.relative_to(root).parts)
        and path.name != "argument_errors.py"
    ]


def _namespace_for(path: Path) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1] / "src"
    parts = path.relative_to(root).with_suffix("").parts
    name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
    return dict(vars(importlib.import_module(name)))


def _snippet_namespace(tree: ast.Module) -> dict[str, Any]:
    """Every import in the source -- function-local ones too -- executed,
    over the builtins."""
    namespace: dict[str, Any] = {}
    imports = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Import) or (isinstance(n, ast.ImportFrom) and n.level == 0)
    ]
    for statement in imports:
        try:
            exec(  # noqa: S102 -- the source's own import statements
                compile(
                    ast.Module(body=[statement], type_ignores=[]), "<imports>", "exec"
                ),
                namespace,
            )
        except ImportError:
            continue  # a platform-only module (`msvcrt`); its names stay unresolved
    return namespace


def _catches_validation(node: Any, namespace: dict[str, Any]) -> bool:
    """Whether an `except`/`isinstance` type expression can match a
    validation error. Resolved in the module's namespace; unresolvable
    counts as yes."""
    if node is None:
        return True
    try:
        # The expression is an `except`/`isinstance` type taken from pmcp's
        # own source (or a test snippet above), evaluated in that module's
        # namespace to resolve imports and aliases -- as #296's
        # `_raised_exceptions` does. No external input reaches it.
        value = eval(ast.unparse(node), {**vars(builtins), **namespace})  # noqa: S307
    except Exception:
        return True
    pending, classes = [value], []
    while pending:
        item = pending.pop()
        if isinstance(item, tuple):
            pending.extend(item)
        elif typing.get_args(item) and not isinstance(item, type):
            pending.extend(typing.get_args(item))  # `int | float`
        else:
            classes.append(item)
    for cls in classes:
        if not isinstance(cls, type):
            return True
        if any(issubclass(error, cls) for error in _VALIDATION_ERRORS):
            return True
    return False


def _callee(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_renderer_call(call: ast.Call, imported: set[str], label: str) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        if func.id in ("type", "isinstance"):
            return True
        if func.id in _RENDERERS:
            home = _RENDERER_HOMES.get(func.id)
            return func.id in imported or (home is not None and label.endswith(home))
        return func.id in _NON_RENDERING_CALLEES
    if isinstance(func, ast.Attribute):
        if (
            func.attr in _RENDERER_METHODS
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
        ):
            return True
        return func.attr in _NON_RENDERING_CALLEES
    return False


def _scopes(tree: ast.Module) -> list[list[ast.stmt]]:
    """Each function's body, and the module's own statements."""
    bodies: list[list[ast.stmt]] = [
        [
            s
            for s in tree.body
            if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
    ]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bodies.append(node.body)
    return bodies


def _own_nodes(body: list[ast.AST]) -> list[ast.AST]:
    """Nodes of `body`, including nested functions and lambdas -- a closure
    sees the enclosing scope's names (rev 4: a `def` inside an `except`) --
    but not nested classes. A nested function is also scanned as its own
    scope; findings are de-duplicated."""
    found: list[ast.AST] = []
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        found.append(node)
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.ClassDef):
                pending.append(child)
    return found


def _is_context_exception(node: ast.AST) -> bool:
    """`context["exception"]` / `context.get("exception")`: an asyncio loop
    exception handler's exception (rev 4)."""
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, ast.Constant) and key.value == "exception"
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "exception"
    )


#: A name's region: the ids of the nodes where it holds an exception, or
#: `None` for the whole scope.
_Regions = dict[str, "set[int]"]


def _widen(regions: _Regions, name: str, region: set[int]) -> bool:
    before = regions.get(name, set())
    after = before | region
    if after == before and name in regions:
        return False
    regions[name] = after
    return True


def _ids(nodes: list[ast.AST]) -> set[int]:
    return {id(node) for node in _own_nodes(nodes)}


def _isinstance_names(
    test: ast.AST, namespace: dict[str, Any], *, positive: bool
) -> set[str]:
    """Names `test` narrows to a possible validation error: `isinstance(x, T)`
    alone or under `and` (positive), or `not isinstance(x, T)` alone or under
    `or` (negative)."""
    if positive and isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return set().union(
            *(_isinstance_names(v, namespace, positive=True) for v in test.values)
        )
    if not positive and isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return set().union(
            *(_isinstance_names(v, namespace, positive=False) for v in test.values)
        )
    if not positive:
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            return _isinstance_names(test.operand, namespace, positive=True)
        return set()
    if (
        isinstance(test, ast.Call)
        and _callee(test) == "isinstance"
        and len(test.args) == 2
        and isinstance(test.args[0], ast.Name)
        and _catches_validation(test.args[1], namespace)
    ):
        return {test.args[0].id}
    return set()


def _exits(body: list[ast.stmt]) -> bool:
    return bool(body) and isinstance(
        body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break)
    )


def _loop_targets(node: ast.AST, names: _Regions) -> set[str]:
    """Targets of a loop that receive an exception: position-mapped through
    a literal sequence of tuples, `zip(...)` and `enumerate(...)`; every
    target of an opaque iterable that is an exception name or `.exceptions`."""
    iterable, target = node.iter, node.target  # type: ignore[attr-defined]

    def mentions(expr: ast.AST) -> bool:
        return any(
            (isinstance(n, ast.Name) and n.id in names)
            or (isinstance(n, ast.Attribute) and n.attr == "exceptions")
            for n in ast.walk(expr)
        )

    columns: list[ast.AST] | None = None
    if isinstance(iterable, ast.Call) and _callee(iterable) == "zip":
        columns = list(iterable.args)
    elif (
        isinstance(iterable, ast.Call)
        and _callee(iterable) == "enumerate"
        and iterable.args
    ):
        columns = [ast.Constant(0), iterable.args[0]]
    elif isinstance(iterable, (ast.Tuple, ast.List)) and all(
        isinstance(e, ast.Tuple) for e in iterable.elts
    ):
        width = {len(e.elts) for e in iterable.elts}  # type: ignore[attr-defined]
        if len(width) == 1:
            n = width.pop()
            columns = [
                ast.Tuple([e.elts[i] for e in iterable.elts], ast.Load())
                for i in range(n)
            ]  # type: ignore[attr-defined]
    if (
        columns is not None
        and isinstance(target, ast.Tuple)
        and len(target.elts) == len(columns)
    ):
        return {
            t.id
            for t, column in zip(target.elts, columns)
            if isinstance(t, ast.Name) and mentions(column)
        }
    if mentions(iterable):
        return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
    return set()


def _exception_regions(body: list[ast.stmt], namespace: dict[str, Any]) -> _Regions:
    regions: _Regions = {}
    nodes = _own_nodes(body)
    everywhere = {id(node) for node in nodes}
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}

    def block_of(node: ast.AST) -> list[ast.AST] | None:
        if node in body:
            return list(body)
        parent = parents.get(node)
        for field in ("body", "orelse", "finalbody", "handlers"):
            statements = getattr(parent, field, None)
            if isinstance(statements, list) and node in statements:
                return statements
        return None

    def after(node: ast.AST) -> set[int]:
        block = block_of(node)
        return _ids(block[block.index(node) + 1 :]) if block is not None else set()

    for node in nodes:
        if isinstance(node, ast.ExceptHandler) and node.name:
            if _catches_validation(node.type, namespace):
                _widen(regions, node.name, _ids(node.body))
        elif isinstance(node, (ast.If, ast.While)):
            for name in _isinstance_names(node.test, namespace, positive=True):
                _widen(regions, name, _ids(node.body))
            for name in _isinstance_names(node.test, namespace, positive=False):
                _widen(regions, name, _ids(node.orelse))
                if _exits(node.body):
                    _widen(regions, name, after(node))
        elif isinstance(node, ast.IfExp):
            for name in _isinstance_names(node.test, namespace, positive=True):
                _widen(regions, name, _ids([node.body]))
            for name in _isinstance_names(node.test, namespace, positive=False):
                _widen(regions, name, _ids([node.orelse]))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            calls = [c for c in ast.walk(node.value) if isinstance(c, ast.Call)]
            if (
                _is_context_exception(node.value)
                or any(
                    _callee(c) == "exception" and isinstance(c.func, ast.Attribute)
                    for c in calls
                )
                or any(
                    _callee(c) == "gather"
                    and any(
                        k.arg == "return_exceptions"
                        and not (
                            isinstance(k.value, ast.Constant) and k.value.value is False
                        )
                        for k in c.keywords
                    )
                    for c in calls
                )
            ):
                for t in targets:
                    if isinstance(t, ast.Name):
                        _widen(regions, t.id, everywhere)

    def held(use: ast.Name) -> bool:
        return id(use) in regions.get(use.id, set())

    changed = True
    while changed:
        changed = False
        for node in nodes:
            if isinstance(node, ast.Assign):
                sources = [
                    n
                    for n in ([node.value] if isinstance(node.value, ast.Name) else [])
                    + (
                        [node.value.value]
                        if isinstance(node.value, ast.Subscript)
                        and isinstance(node.value.value, ast.Name)
                        else []
                    )
                    if n.id in regions and held(n)
                ]
                if sources:
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            # An alias escapes its region: held everywhere.
                            changed |= _widen(regions, t.id, everywhere)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                live = {k: v for k, v in regions.items()}
                for name in _loop_targets(node, live):
                    changed |= _widen(regions, name, _ids(node.body))
            elif isinstance(node, ast.comprehension):
                for name in _loop_targets(node, regions):
                    changed |= _widen(regions, name, everywhere)
    # Narrowing the other way: in the `else` of `isinstance(x, T)`, and after
    # `if isinstance(x, T): raise/return`, `x` is not an exception.
    for node in nodes:
        if isinstance(node, ast.If):
            for name in _isinstance_names(node.test, namespace, positive=True):
                if name in regions:
                    regions[name] -= _ids(node.orelse)
                    if _exits(node.body):
                        regions[name] -= after(node)
    return regions


def _in_loop_iterable(use: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    child, node = use, parents.get(use)
    while node is not None:
        if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            return child is node.iter
        if isinstance(node, (ast.stmt, ast.Lambda)):
            return False
        child, node = node, parents.get(node)
    return False


def exception_sinks(
    source: str, label: str, namespace: dict[str, Any] | None = None
) -> list[str]:
    """Every exception-to-text sink in `source` (see the module docstring)."""
    tree = ast.parse(source)
    namespace = {**(namespace or {}), **_snippet_namespace(tree)}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            function.name in _NON_RENDERING_FUNCTIONS
        ):
            function.body = [ast.Pass()]
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pmcp")
        for alias in node.names
    }
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    found: list[str] = []
    for function in ast.walk(tree):
        if (
            isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            and function.name in _RENDERERS
            and not label.endswith(_RENDERER_HOMES.get(function.name, "\0"))
        ):
            found.append(
                f"{label}:{function.lineno}: shadows the renderer {function.name}"
            )
    for body in _scopes(tree):
        regions = _exception_regions(body, namespace)
        for use in _own_nodes(body):
            if not (
                isinstance(use, ast.Name)
                and isinstance(use.ctx, ast.Load)
                and id(use) in regions.get(use.id, set())
            ):
                continue
            if _in_loop_iterable(use, parents):
                continue  # the loop's targets are tracked instead
            parent = parents.get(use)
            if isinstance(parent, ast.keyword):
                parent = parents.get(parent)
            if isinstance(parent, ast.Call) and use is not parent.func:
                if _is_renderer_call(parent, imported, label):
                    continue
            elif isinstance(parent, (ast.Raise, ast.Compare, ast.BoolOp, ast.UnaryOp)):
                continue
            elif (
                isinstance(parent, (ast.If, ast.While, ast.Assert))
                and use is parent.test
            ):
                continue
            elif isinstance(parent, ast.IfExp) and use is parent.test:
                continue
            elif isinstance(parent, ast.Assign) and parent.value is use:
                if all(isinstance(t, ast.Name) for t in parent.targets):
                    continue
            elif isinstance(parent, ast.Subscript) and parent.value is use:
                grand = parents.get(parent)
                if (
                    isinstance(grand, ast.Assign)
                    and grand.value is parent
                    and all(isinstance(t, ast.Name) for t in grand.targets)
                ):
                    continue
                if isinstance(grand, (ast.Raise, ast.Compare)):
                    continue
                if isinstance(grand, ast.Call) and _is_renderer_call(
                    grand, imported, label
                ):
                    continue
            elif isinstance(parent, (ast.For, ast.AsyncFor, ast.comprehension)) and any(
                n is use for n in ast.walk(parent.iter)
            ):
                continue
            elif (
                isinstance(parent, ast.Attribute)
                and parent.attr not in _TEXT_ATTRIBUTES
            ):
                grand = parents.get(parent)
                if not (isinstance(grand, ast.Call) and grand.func is parent):
                    continue
            found.append(f"{label}:{use.lineno}: {type(parent).__name__} uses {use.id}")
    for node in ast.walk(tree):
        if _is_context_exception(node):
            parent = parents.get(node)
            if isinstance(parent, ast.Assign) and all(
                isinstance(t, ast.Name) for t in parent.targets
            ):
                continue  # tracked as an exception name
            if isinstance(parent, ast.Call) and _is_renderer_call(
                parent, imported, label
            ):
                continue
            if isinstance(
                parent, (ast.Compare, ast.BoolOp, ast.If, ast.UnaryOp, ast.Raise)
            ):
                continue
            found.append(f"{label}:{node.lineno}: context['exception'] used as text")
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        callee = _callee(call)
        if (
            callee == "exception"
            and (call.args or call.keywords)
            and not any(k.arg == "timeout" for k in call.keywords)
        ):
            found.append(f"{label}:{call.lineno}: logger.exception renders a traceback")
        if (
            callee == "exception"
            and isinstance(call.func, ast.Attribute)
            and not call.args
            and all(k.arg == "timeout" for k in call.keywords)
            and not isinstance(parents.get(call), (ast.Assign, ast.IfExp))
        ):
            found.append(f"{label}:{call.lineno}: .exception() used inline")
        if callee in _TRACEBACK_RENDERERS:
            found.append(f"{label}:{call.lineno}: traceback.{callee}")
        if (
            callee in ("exc_info", "exception")
            and isinstance(call.func, ast.Attribute)
            and (isinstance(call.func.value, ast.Name) and call.func.value.id == "sys")
        ):
            found.append(f"{label}:{call.lineno}: sys.{callee}()")
        if callee in ("makeRecord", "handle") and isinstance(call.func, ast.Attribute):
            found.append(f"{label}:{call.lineno}: {callee} builds a record by hand")
        for keyword in call.keywords:
            if keyword.arg == "exc_info" and not (
                isinstance(keyword.value, ast.Call)
                and _callee(keyword.value) == "safe_exc_info"
            ):
                found.append(
                    f"{label}:{call.lineno}: exc_info= not through safe_exc_info"
                )
            if keyword.arg is None and any(
                isinstance(k, ast.Constant) and k.value == "exc_info"
                for k in ast.walk(keyword.value)
            ):
                found.append(f"{label}:{call.lineno}: exc_info passed through **")
    return list(dict.fromkeys(found))


def test_no_exception_reaches_text_except_through_the_renderer() -> None:
    sources = _sources()
    assert len(sources) > 40, len(sources)
    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    found = [
        sink
        for path in sources
        for sink in exception_sinks(
            path.read_text(), str(path.relative_to(root)), _namespace_for(path)
        )
    ]
    assert found == [], "\n".join(found)
    # Not vacuous: the analysis tracked exceptions through every module.
    tracked = sum(
        len(_exception_regions(body, _namespace_for(path)))
        for path in sources
        for body in _scopes(ast.parse(path.read_text()))
    )
    assert tracked > 100, tracked


#: Every construct the rev 2 board seat fed rev 2's scanner (42; `except*`
#: needs Python 3.11, this suite runs 3.10), plus the `_connect_with_retry`
#: regression it found surviving every test. Each must be flagged.
_FLAGGED = {
    "alias_used_after_except": "last=None\nfor i in r:\n    try:\n        f()\n    except Exception as e:\n        last = e\nlog(f'{last}')\n",
    "attr_store_then_render": "try:\n    f()\nexcept Exception as e:\n    self.last_error = e\nlog(f'{self.last_error}')\n",
    "subscript_store": "try:\n    f()\nexcept Exception as e:\n    errs[name] = e\nlog(str(errs[name]))\n",
    "import_alias_except": "from pydantic import ValidationError as PVE\ntry:\n    f()\nexcept PVE as e:\n    log(f'{e}')\n",
    "custom_base_except": "try:\n    f()\nexcept (KeyError, TypeError, PydanticValidationError) as e:\n    log(f'{e}')\n",
    "isinstance_boolop": "def g(r):\n    if isinstance(r, Exception) and r:\n        log(f'{r}')\n",
    "isinstance_negative": "def g(r):\n    if not isinstance(r, BaseException):\n        return\n    log(f'{r}')\n",
    "isinstance_valueerror": "def g(r):\n    if isinstance(r, ValueError):\n        log(f'{r}')\n",
    "gather_results_loop": "async def g():\n    rs = await asyncio.gather(*ts, return_exceptions=True)\n    for r in rs:\n        log(f'{r}')\n",
    "task_exception_inline": "def g(t):\n    log(f'{t.exception()}')\n",
    "task_exception_ifexp": "def g(t):\n    exc = t.exception() if not t.cancelled() else None\n    log(f'{exc}')\n",
    "sys_exc_info": "import sys\ntry:\n    f()\nexcept Exception:\n    log(str(sys.exc_info()[1]))\n",
    "sys_exception": "import sys\ntry:\n    f()\nexcept Exception:\n    log(str(sys.exception()))\n",
    "format_exception_only": "import sys, traceback\ntry:\n    f()\nexcept Exception:\n    log(''.join(traceback.format_exception_only(*sys.exc_info()[:2])))\n",
    "tb_exception_obj": "import sys, traceback\ntry:\n    f()\nexcept Exception:\n    log(''.join(traceback.TracebackException(*sys.exc_info()).format()))\n",
    "jsonschema_path": "try:\n    v()\nexcept Exception as e:\n    log(f'bad at {list(e.path)}')\n",
    "jsonschema_json_path": "try:\n    v()\nexcept Exception as e:\n    log('bad at ' + e.json_path)\n",
    "jsonschema_relative_path": "try:\n    v()\nexcept Exception as e:\n    log(e.relative_path)\n",
    "notes": "try:\n    v()\nexcept Exception as e:\n    log(e.__notes__)\n",
    "jsondecode_doc": "try:\n    v()\nexcept ValueError as e:\n    log(e.doc)\n",
    "exc_info_kwargs_splat": "try:\n    f()\nexcept Exception:\n    logger.error('x', **{'exc_info': True})\n",
    "logger_handle_make_record": "import sys\ntry:\n    f()\nexcept Exception:\n    logger.handle(logger.makeRecord('n', 40, 'f', 1, 'x', None, sys.exc_info()))\n",
    "name_shadow_renderer": "def describe_exception(e):\n    return str(e)\n",
    "renderer_attr_on_other_obj": "try:\n    f()\nexcept Exception as e:\n    log(other.type(e))\n",
    "e_str_method": "try:\n    f()\nexcept Exception as e:\n    log(e.__str__())\n",
    "getattr_args": "try:\n    f()\nexcept Exception as e:\n    log(getattr(e, 'message', ''))\n",
    "vars_e": "try:\n    f()\nexcept Exception as e:\n    log(vars(e))\n",
    "e_errors_call": "try:\n    f()\nexcept Exception as e:\n    log(e.errors())\n",
    "e_json_call": "try:\n    f()\nexcept Exception as e:\n    log(e.json())\n",
    "future_exception_timeout": "def g(fut):\n    exc = fut.exception(timeout=0)\n    log(f'{exc}')\n",
    "warnings_warn": "import warnings\ntry:\n    f()\nexcept Exception as e:\n    warnings.warn(f'{e}')\n",
    "percent_arg": "try:\n    f()\nexcept Exception as e:\n    logger.warning('x %s', e)\n",
    "repr": "try:\n    f()\nexcept Exception as e:\n    x = repr(e)\n",
    "chained_raise_text": "try:\n    f()\nexcept Exception as e:\n    raise RuntimeError(f'wrap {e}') from e\n",
    "chained_raise_repr": "try:\n    f()\nexcept Exception as e:\n    raise RuntimeError('wrap %r' % (e,)) from e\n",
    "logging_exception_module": "import logging\ntry:\n    f()\nexcept Exception:\n    logging.exception('x')\n",
    "logger_exception_noargs": "try:\n    f()\nexcept Exception:\n    logger.exception(msg) if 0 else None\n",
    "print_exc": "import traceback\ntry:\n    f()\nexcept Exception:\n    traceback.print_exc()\n",
    "starred_args": "try:\n    f()\nexcept Exception as e:\n    log(*e.args)\n",
    "exc_group_loop_var": "try:\n    f()\nexcept BaseExceptionGroup as eg:\n    for x in eg.exceptions:\n        log(f'{x}')\n",
    "lambda_capture": "try:\n    f()\nexcept Exception as e:\n    cb = lambda: str(e)\n",
    "return_exception": "def g():\n    try:\n        f()\n    except Exception as e:\n        return e\n",
    "truncated_copy": "try:\n    f()\nexcept Exception as e:\n    raise RuntimeError(str(e)[:200]) from e\n",
    "closure_in_except": (
        "try:\n    f()\nexcept Exception as e:\n    def inner():\n        log(f'{e}')\n    later(inner)\n"
    ),
    "loop_exception_handler": (
        "def handler(loop, context):\n    log(f\"{context['exception']}\")\n"
    ),
    "loop_exception_handler_alias": (
        "def handler(loop, context):\n    exc = context.get('exception')\n    log(str(exc))\n"
    ),
    "connect_with_retry_regression": (
        "async def _connect_with_retry(self, config):\n"
        "    last_error = None\n"
        "    for attempt in range(3):\n"
        "        try:\n"
        "            await self._connect_server(config)\n"
        "            return\n"
        "        except Exception as e:\n"
        "            last_error = e\n"
        "    if last_error:\n"
        "        logger.warning(f'giving up: {last_error}')\n"
        "        raise last_error\n"
    ),
}


@pytest.mark.parametrize("label", sorted(_FLAGGED))
def test_the_scanner_flags_each_construct(label: str) -> None:
    """The static check is only as wide as its rules: each fires."""
    assert exception_sinks(_FLAGGED[label], label), label


def test_the_scanner_passes_the_renderers() -> None:
    clean = (
        "from pmcp.argument_errors import exception_text, safe_exc_info\n"
        "try:\n    f()\nexcept Exception as e:\n"
        "    last = e\n"
        "    log(f'{exception_text(e)}', exc_info=safe_exc_info(e))\n"
        "    if e.code == 1 or isinstance(e, KeyError):\n        raise\n"
        "    raise X() from e\n"
        "try:\n    f()\nexcept KeyError as e:\n    log(f'{e}')\n"
        "def g(fut):\n    exc = fut.exception()\n    if exc is not None:\n"
        "        log(exception_text(exc))\n        raise exc\n"
    )
    assert exception_sinks(clean, "clean") == []


# --- rev 12: a value pmcp rejects by hand is never rendered with repr ---------

#: Rejection wording: a message carrying one of these, built with `!r`,
#: `repr(...)` or `%r` of a non-constant, is a hand-rendered rejection.
#: Rev 13 (round-12 claude): bad, wrong, failed, cannot, unrecognised and
#: not found added.
_REJECTION_WORDS = re.compile(
    r"(?i)(invalid|unusable|unexpected|malformed|reject|refus|ignor|unsupported"
    r"|unknown|unsafe|not a valid|not permitted|not allowed|must be|expected"
    r"|illegal|disallowed|forbidden|denied|blocked|skipping|unparseable"
    r"|unreadable|repeated|not an? |does not |must match|bad|wrong|fail"
    r"|cannot|can't|unrecogni[sz]|not found)"
)


def _is_repr_call(node: ast.AST) -> bool:
    """`repr(<non-constant>)` or `ascii(<non-constant>)`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("repr", "ascii")
        and bool(node.args)
        and not isinstance(node.args[0], ast.Constant)
    )


_PERCENT = re.compile(
    r"%(?:\((?P<key>[^)]*)\))?[#0\- +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?(?P<conv>[a-zA-Z%])"
)


def _percent_repr_targets(text: str, args: list[ast.AST]) -> list[ast.AST]:
    """The arguments a `%`-format renders with `%r` or `%a`, by position (or
    by key, for a single mapping argument)."""
    targets: list[ast.AST] = []
    position = 0
    for match in _PERCENT.finditer(text):
        conv = match.group("conv")
        if conv == "%":
            continue
        if match.group("key") is not None:
            if conv in "ra" and len(args) == 1 and isinstance(args[0], ast.Dict):
                for k, v in zip(args[0].keys, args[0].values):
                    if isinstance(k, ast.Constant) and k.value == match.group("key"):
                        targets.append(v)
            continue
        if conv in "ra" and position < len(args):
            targets.append(args[position])
        position += 1
    return [t for t in targets if not isinstance(t, ast.Constant)]


def _format_repr_targets(
    text: str, args: list[ast.AST], keywords: dict[str, ast.AST]
) -> list[ast.AST]:
    """The arguments a `str.format` renders with `!r` or `!a`."""
    import string

    targets: list[ast.AST] = []
    auto = 0
    try:
        fields = list(string.Formatter().parse(text))
    except ValueError:
        return []
    for _, name, _, conversion in fields:
        if name is None:
            continue
        head = name.split(".")[0].split("[")[0]
        if head == "":
            index: int | str = auto
            auto += 1
        elif head.isdigit():
            index = int(head)
        else:
            index = head
        if conversion not in ("r", "a"):
            continue
        if isinstance(index, int) and index < len(args):
            targets.append(args[index])
        elif isinstance(index, str) and index in keywords:
            targets.append(keywords[index])
    return [t for t in targets if not isinstance(t, ast.Constant)]


def _constant_text(node: ast.AST) -> str:
    """The literal text of a string expression: a constant, an f-string's
    constant parts, or the constant operands of a `+` chain."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_constant_text(v) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _constant_text(node.left) + _constant_text(node.right)
    return ""


def _repr_sites(source: str) -> list[tuple[int, str]]:
    """Each value in `source` rendered with its repr inside a message
    carrying rejection wording, as (line, the rendered expression). Rev 13
    covers every spelling Python has (round-12 claude):

    - an f-string field with `!r`, or holding `repr(x)`;
    - `"..." + repr(x)` (a `+` chain);
    - `"... %r ..." % x`, and `"... %s ..." % repr(x)`;
    - `"... {!r} ...".format(x)`, and `.format(repr(x))`;
    - a call whose first argument is the message: `%r` in it, or `repr(x)`
      as a later argument (`logger.warning("bad %s", repr(x))`).
    """
    tree = ast.parse(source)
    sites: list[tuple[int, str]] = []

    def add(node: ast.AST, value: ast.AST) -> None:
        sites.append((getattr(node, "lineno", 0), ast.unparse(value)))

    def reprs_in(node: ast.AST) -> list[ast.AST]:
        return [n.args[0] for n in ast.walk(node) if _is_repr_call(n)]  # type: ignore[attr-defined]

    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            if not _REJECTION_WORDS.search(_constant_text(node)):
                continue
            for v in node.values:
                if not isinstance(v, ast.FormattedValue) or isinstance(
                    v.value, ast.Constant
                ):
                    continue
                if v.conversion in (ord("r"), ord("a")):
                    add(node, v.value)
                elif _is_repr_call(v.value):
                    add(node, v.value.args[0])  # type: ignore[attr-defined]
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            if isinstance(getattr(node, "_parent_add", None), ast.BinOp):
                continue
            if _REJECTION_WORDS.search(_constant_text(node)):
                operands = []
                stack: list[ast.AST] = [node]
                while stack:
                    current = stack.pop()
                    if isinstance(current, ast.BinOp) and isinstance(
                        current.op, ast.Add
                    ):
                        current.left._parent_add = current  # type: ignore[attr-defined]
                        current.right._parent_add = current  # type: ignore[attr-defined]
                        stack += [current.left, current.right]
                    else:
                        operands.append(current)
                for operand in operands:
                    if _is_repr_call(operand):
                        add(node, operand.args[0])  # type: ignore[attr-defined]
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            text = _constant_text(node.left)
            if not _REJECTION_WORDS.search(text):
                continue
            values = (
                node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
            )
            for value in _percent_repr_targets(text, values):
                add(node, value)
            for value in values:
                for inner in reprs_in(value):
                    add(node, inner)
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "format"
                and _REJECTION_WORDS.search(_constant_text(func.value))
            ):
                text = _constant_text(func.value)
                arguments = list(node.args) + [k.value for k in node.keywords]
                keywords = {k.arg: k.value for k in node.keywords if k.arg}
                for value in _format_repr_targets(text, list(node.args), keywords):
                    add(node, value)
                for value in arguments:
                    for inner in reprs_in(value):
                        add(node, inner)
            elif node.args and _REJECTION_WORDS.search(_constant_text(node.args[0])):
                text = _constant_text(node.args[0])
                if isinstance(node.args[0], ast.JoinedStr):
                    continue  # its fields are the JoinedStr branch's
                for value in _percent_repr_targets(text, list(node.args[1:])):
                    add(node, value)
                for value in node.args[1:]:
                    for inner in reprs_in(value):
                        add(node, inner)
    return sites


def _repr_rejections(source: str) -> list[int]:
    """Lines in `source` that render a non-constant with its repr inside a
    message carrying rejection wording (`_repr_sites`)."""
    return sorted({line for line, _ in _repr_sites(source)})


#: Sites whose repr'd value is the operator's own (config, environment, CLI
#: arguments, pmcp's own stores) or pmcp's own child: outside Consiliency/
#: pmcp#297's caller/downstream class, and tracked in Consiliency/pmcp#315.
#: A downstream's or a caller's value is described with `describe_value`.
#: Rev 13 (round-12 claude): keyed by SITE -- the module, the function and
#: the rendered expression -- not by function, so a new repr inside an
#: exempt function is a new key, and fails until triaged.
_REPR_REJECTION_EXEMPT: dict[str, str] = {
    "cli.py::_exact_package_spec::spec": "operator CLI argument",
    "cli.py::_run_trust_revoke_package::args.spec": "operator CLI argument",
    "client/manager.py::_request_ceiling_ms::raw": "operator environment",
    "client/manager.py::_stdio_read_limit::raw": "operator environment",
    "config/loader.py::registry_allow_private_from_config::value": "operator config",
    "manifest/npm_resolver.py::_query_locked::status": "pmcp's own resolver child",
    "manifest/npm_resolver.py::resolve::command": "manifest/config command",
    "manifest/package_identity.py::_fetch_packument::name": (
        "an accepted, validated package name; the failure is the registry fetch's"
    ),
    "manifest/refresher.py::check_staleness::cfg_name": "configured package",
    "manifest/refresher.py::check_staleness::desc.package": "cached package",
    "package_approvals.py::_decode::decision": "pmcp's own approval store",
    "package_approvals.py::_read_store_and_stale::entry['name']": (
        "pmcp's own approval store"
    ),
    "package_approvals.py::_read_store_and_stale::entry['resolved_version']": (
        "pmcp's own approval store"
    ),
    "package_approvals.py::_require_identity_fields::name": (
        "pmcp's own approval store"
    ),
    "package_approvals.py::_require_identity_fields::registry": (
        "pmcp's own approval store"
    ),
    "provision_gate.py::evaluate_provision::getattr(server_config, 'name', None)": (
        "a resolved server config's name (operator/manifest config)"
    ),
    "trust_store.py::_decode::decision": "pmcp's own trust store",
    "trust_store.py::record_resolved::decision": "pmcp's own trust store",
    "validation.py::parse_package_spec::spec": "config/CLI/manifest package spec",
}


def _repr_site_keys() -> set[str]:
    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        if "baml_client" in path.parts:
            continue
        source = path.read_text()
        tree = ast.parse(source)
        functions = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        counts: dict[str, int] = {}
        for line, expression in sorted(_repr_sites(source)):
            owners = [
                f for f in functions if f.lineno <= line <= (f.end_lineno or f.lineno)
            ]
            owner = (
                min(owners, key=lambda f: (f.end_lineno or f.lineno) - f.lineno).name
                if owners
                else "<module>"
            )
            key = f"{path.relative_to(root).as_posix()}::{owner}::{expression}"
            counts[key] = counts.get(key, 0) + 1
            found.add(key if counts[key] == 1 else f"{key}#{counts[key]}")
    return found


def test_no_rejected_value_is_rendered_with_repr() -> None:
    """Round-11 codex P1's class, as a static rule (rev 12; rev 13 widened
    it to every repr spelling and keyed it by site): a repr in a message
    that rejects something renders the value itself. Every such site in
    `src/pmcp` is a named exemption with its provenance (none is a caller's
    or a downstream's value), and every exemption must still exist. The rule
    cannot see `{x}` or `%s` of `x` itself; those sites were triaged by hand
    (the plan's table)."""
    found = _repr_site_keys()
    assert found == set(_REPR_REJECTION_EXEMPT), (
        sorted(found - set(_REPR_REJECTION_EXEMPT)),
        sorted(set(_REPR_REJECTION_EXEMPT) - found),
    )


@pytest.mark.parametrize(
    "snippet, flagged",
    [
        ('f"unusable cursor ({raw!r})"', True),
        ('f"invalid value {repr(v)}"', True),
        ('logger.warning("Ignoring %s: got %r", k, v)', True),
        # rev 13 (round-12 claude): every other spelling, and the new words.
        ('"invalid: " + repr(x)', True),
        ('"invalid: " + name + " " + repr(x)', True),
        ('"invalid: %r" % x', True),
        ('"invalid: %r and %r" % (x, y)', True),
        ('"invalid: %s" % repr(x)', True),
        ('"invalid {!r}".format(x)', True),
        ('"invalid {}".format(repr(x))', True),
        ('logger.warning("invalid %s", repr(x))', True),
        ('logger.warning("bad cursor: %r", x)', True),
        ('f"wrong value {x!r}"', True),
        ('f"lookup failed for {x!r}"', True),
        ('f"cannot use {x!r}"', True),
        ('f"unrecognised {x!r}"', True),
        ('f"not found: {x!r}"', True),
        ('"invalid: " + str(x)', False),
        ('"invalid: %s" % x', False),
        ('"connected to %r" % x', False),
        ('logger.debug("fetch failed for %s: %s", name, repr(e))', True),
        ('logger.debug("fetch failed for %s: %r", name, exception_text(e))', True),
        ('logger.debug("fetch failed for %r: %s", "pkg", exception_text(e))', False),
        ('"invalid %(v)r" % {"v": x}', True),
        ('"invalid {0} {1!r}".format(a, b)', True),
        ('"invalid {0!r}".format("const")', False),
        ('f"invalid {x!a}"', True),
        ('f"unusable cursor ({describe_value(raw)})"', False),
        ('f"connected to {name!r}"', False),
        ("f\"invalid {'x'!r}\"", False),
    ],
)
def test_the_repr_rejection_rule_sees_its_spellings(
    snippet: str, flagged: bool
) -> None:
    assert bool(_repr_rejections(snippet)) is flagged, snippet
