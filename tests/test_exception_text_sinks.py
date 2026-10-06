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

Since rev 20 (round-18 codex F001) an exception name is also a parameter
annotated with an exception type that can be a registered error, over the
whole function; and a read is also safe once the registry check passed
(`safe_exc_info(x) is not None`, or after `if safe_exc_info(x) is None:`
returned or raised). A callee an exception may be handed to is exempt only
with a reason a test enforces (`_EXEMPT_CALLEES`), never by name alone.

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
#: Callees an exception may be handed to, each with the reason its output
#: cannot carry a registered error's value -- a reason a test enforces
#: (rev 20, round-18 codex F001: no exemption by name alone):
#: - ``predicate``: defined in pmcp, annotated ``-> bool``, and every
#:   ``return`` is a boolean expression
#:   (`test_every_predicate_exemption_returns_only_booleans`);
#: - ``scanned``: defined in pmcp with the exception as an annotated
#:   exception parameter, so this scanner checks its body like any other
#:   (`test_every_scanned_exemption_takes_an_annotated_exception`);
#: - ``guarded``: defined in pmcp, and refuses a registered error's chain
#:   (``safe_exc_info(x) is None``) before it reads the exception
#:   (`test_every_guarded_exemption_checks_the_registry_first`);
#: - ``handoff``: hands the exception object on, and its result is
#:   discarded (`test_every_handoff_call_discards_its_result`);
#: - ``passthrough``: defined in pmcp, yields or returns only exception
#:   objects (plain names) and reads no text from them
#:   (`test_every_passthrough_exemption_reads_no_text`);
#: - ``fields``: defined in pmcp, and reads its exception parameter only
#:   through ``isinstance``/``type`` or an attribute in
#:   :data:`_FIELD_ATTRIBUTES` -- a status, a header map -- never its text
#:   (`test_every_fields_exemption_reads_only_its_fields`);
#: - ``reregisters``: defined in pmcp, annotated to return a registered
#:   value-bearing type (or a list or iterator of one), so its output is
#:   rendered by the registry like the input
#:   (`test_every_reregisters_exemption_returns_a_registered_type`).
_EXEMPT_CALLEES: dict[str, tuple[str, str]] = {
    "_is_protocol_version_initialize_error": ("predicate", "client/manager.py"),
    "carries_rejected_value": ("predicate", "argument_errors.py"),
    "_warn_unparseable": ("scanned", "policy/policy.py"),
    "record_rejected_arguments": ("scanned", "scoped_advisor_audit.py"),
    # parsing.py: a MarkedYAMLError's mark, for its line and column.
    "_yaml_position": ("fields", "parsing.py"),
    "_iter_leaf_exceptions": ("passthrough", "client/manager.py"),
    "parse_url_elicitation_error": ("guarded", "auth.py"),
    "pyjwt_text": ("guarded", "auth.py"),
    "_handshake_error": ("scanned", "client/manager.py"),
    # feedback_egress.py: classify a transport failure by its type, status
    # and headers only.
    "_classify_http_error": ("fields", "feedback_egress.py"),
    "_classify_transport_error": ("fields", "feedback_egress.py"),
    # tools/schema.py (Consiliency/pmcp#371): rebuild a registered error from
    # one; the result is itself registered, so every renderer treats it.
    "_rerooted": ("reregisters", "tools/schema.py"),
    "_unfolded": ("reregisters", "tools/schema.py"),
    # asyncio: the awaiting caller's own `except` is checked like any other.
    "set_exception": ("handoff", ""),
    # server.py (rev 20): marks the exception as pmcp's handler's own.
    "setattr": ("handoff", ""),
}
_NON_RENDERING_CALLEES = set(_EXEMPT_CALLEES)
#: Functions whose body may read an exception's text: the ``predicate``
#: exemptions (their output is a bool, enforced above).
_NON_RENDERING_FUNCTIONS = {
    name
    for name, (kind, _home) in _EXEMPT_CALLEES.items()
    if kind in ("predicate", "passthrough", "reregisters", "fields")
}
_LOGGING_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}

#: Value-free attributes of library exceptions (rev 22): numbers and
#: positions, never text. Every other attribute read is a sink unless it is
#: a pmcp exception's own field (:func:`_pmcp_exception_fields`).
_VALUE_FREE_ATTRIBUTES = frozenset(
    {
        "errno",
        "code",
        "status",
        "status_code",
        "returncode",
        "lineno",
        "colno",
        "pos",
        "__class__",
        # pydantic's `ValidationError.title`: the model or type name.
        "title",
    }
)


def _class_fields(cls: type) -> frozenset[str]:
    """Attribute names `cls` and its pmcp bases set on `self` (rev 23)."""
    import inspect

    names: set[str] = set()
    for klass in cls.__mro__:
        if not str(getattr(klass, "__module__", "")).startswith("pmcp"):
            continue
        try:
            tree = ast.parse(inspect.getsource(klass).lstrip())
        except (OSError, TypeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Store)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                names.add(node.attr)
    return frozenset(names)


def _handler_fields(
    handler: ast.ExceptHandler, namespace: dict[str, Any]
) -> frozenset[str]:
    """The pmcp fields an `except` binding may read: those of the classes it
    catches, only when every one of them is a pmcp exception class (rev 23,
    round-21 claude N1: `e.data` on the SDK's `MCPError` is not pmcp's)."""
    if handler.type is None:
        return frozenset()
    try:
        value = eval(  # noqa: S307 -- pmcp's own except clause, its namespace
            ast.unparse(handler.type), {**vars(builtins), **namespace}
        )
    except Exception:
        return frozenset()
    classes = value if isinstance(value, tuple) else (value,)
    if not classes or not all(
        isinstance(cls, type) and str(cls.__module__).startswith("pmcp")
        for cls in classes
    ):
        return frozenset()
    fields = [_class_fields(cls) for cls in classes]
    return frozenset.intersection(*fields) - _TEXT_ATTRIBUTES


def _binding_handler(
    use: ast.Name, parents: dict[ast.AST, ast.AST]
) -> ast.ExceptHandler | None:
    node: ast.AST = use
    while node in parents:
        node = parents[node]
        if isinstance(node, ast.ExceptHandler) and node.name == use.id:
            return node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
    return None


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
        # The renderers themselves: `argument_errors.py`, and (rev 20)
        # `sdk_rejections.py`, which rebuilds the SDK's errors and is bound
        # end to end by `test_http_transport.py`'s stdio and HTTP grids.
        and path.name not in ("argument_errors.py", "sdk_rejections.py")
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
            # Every binding, whatever it catches (rev 21, round-19 codex
            # F001): a narrow type -- `ConnectionError`, `OSError` -- can
            # still chain a registered error through `__cause__` or
            # `__context__`, and its own text can be built from it.
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


def _exception_parameters(
    function: ast.FunctionDef | ast.AsyncFunctionDef, namespace: dict[str, Any]
) -> list[str]:
    """The parameters annotated with an exception type that can hold a
    registered (value-bearing) error -- `Exception`, `BaseException`,
    `ValueError`, ... -- resolved in the module's namespace."""
    names = []
    arguments = function.args
    for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
        annotation = argument.annotation
        if annotation is None:
            continue
        try:
            value = eval(  # noqa: S307 -- pmcp's own annotation, its own namespace
                ast.unparse(annotation), {**vars(builtins), **namespace}
            )
        except Exception:
            continue
        import types as _types

        union = typing.get_origin(value) in (typing.Union, _types.UnionType)
        members = [
            item
            for item in (typing.get_args(value) if union else (value,))
            if item is not type(None)
        ]
        try:
            exceptional = bool(members) and all(
                isinstance(item, type)
                and not typing.get_args(item)
                and issubclass(item, BaseException)
                for item in members
            )
        except TypeError:
            exceptional = False
        if exceptional and _catches_validation(annotation, namespace):
            names.append(argument.arg)
    return names


def _is_registry_check(test: ast.AST, name: str, *, passed: bool) -> bool:
    """`safe_exc_info(name) is not None` (`passed`) or `... is None` (not),
    alone or as one operand of an `and`."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return any(_is_registry_check(v, name, passed=passed) for v in test.values)
    return (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.IsNot if passed else ast.Is)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
        and isinstance(test.left, ast.Call)
        and _callee(test.left) == "safe_exc_info"
        and len(test.left.args) == 1
        and isinstance(test.left.args[0], ast.Name)
        and test.left.args[0].id == name
    )


def _registry_guarded(use: ast.Name, parents: dict[ast.AST, ast.AST]) -> bool:
    """Whether `use` runs only after the registry check on its name passed:
    inside the body of `if safe_exc_info(x) is not None:`, or after an
    earlier `if safe_exc_info(x) is None: <return/raise>` in an enclosing
    block (rev 20, round-18 codex F001)."""
    node: ast.AST = use
    while node in parents:
        parent = parents[node]
        if (
            isinstance(parent, ast.If)
            and node in parent.body
            and _is_registry_check(parent.test, use.id, passed=True)
        ):
            return True
        for field in ("body", "orelse", "finalbody"):
            block = getattr(parent, field, None)
            if isinstance(block, list) and node in block:
                for earlier in block[: block.index(node)]:
                    if (
                        isinstance(earlier, ast.If)
                        and _is_registry_check(earlier.test, use.id, passed=False)
                        and _exits(earlier.body)
                    ):
                        return True
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return False
        node = parent
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
            and label.endswith(_EXEMPT_CALLEES[function.name][1])
        ):
            # Its reason is enforced by the exemption tests below.
            function.body = [ast.Pass()]
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
    functions = {
        id(node.body): node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
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
        owner = functions.get(id(body))
        if owner is not None:
            # A parameter annotated as an exception that can be a registered
            # error holds one over the whole body (rev 20).
            for name in _exception_parameters(owner, namespace):
                _widen(regions, name, _ids(body))
        for use in _own_nodes(body):
            if not (
                isinstance(use, ast.Name)
                and isinstance(use.ctx, ast.Load)
                and id(use) in regions.get(use.id, set())
            ):
                continue
            if _in_loop_iterable(use, parents):
                continue  # the loop's targets are tracked instead
            if _registry_guarded(use, parents):
                continue  # read only once the registry check passed (rev 20)
            parent = parents.get(use)
            if isinstance(parent, ast.keyword):
                parent = parents.get(parent)
            if isinstance(parent, ast.Call) and use is not parent.func:
                # `setattr` hands nothing off only when the exception is its
                # target (rev 22, round-20 claude N1).
                if _callee(parent) == "setattr" and not (
                    parent.args and parent.args[0] is use
                ):
                    pass
                elif _is_renderer_call(parent, imported, label):
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
            elif isinstance(parent, ast.Attribute):
                # An allowlist (rev 22, round-20 claude N1): an attribute is
                # safe only if it is a known value-free field, or is handed
                # straight to a renderer or an exempt callee.
                grand = parents.get(parent)
                method = isinstance(grand, ast.Call) and grand.func is parent
                handler = _binding_handler(use, parents)
                allowed = _VALUE_FREE_ATTRIBUTES - _TEXT_ATTRIBUTES
                if handler is not None:
                    allowed = allowed | _handler_fields(handler, namespace)
                if parent.attr in allowed and not method:
                    continue
                if (
                    isinstance(grand, ast.Call)
                    and not method
                    and _is_renderer_call(grand, imported, label)
                    and _callee(grand) not in ("setattr", "set_exception")
                ):
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
            # a bare statement discards it (`waits._retrieve` marks the
            # outcome retrieved): nothing is rendered
            and not isinstance(parents.get(call), (ast.Assign, ast.IfExp, ast.Expr))
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
            if keyword.arg is None and (
                any(
                    isinstance(k, ast.Constant) and k.value == "exc_info"
                    for k in ast.walk(keyword.value)
                )
                # A `**name` into a logging call can carry `exc_info` from
                # anywhere (rev 22, round-20 claude N1): only a dict display
                # with no `exc_info` key is safe.
                or (
                    callee in _LOGGING_METHODS
                    and not isinstance(keyword.value, ast.Dict)
                )
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
    # rev 21 (round-19 codex F001): a narrow handler's binding can chain a
    # registered error too.
    "narrow_handler_text": "try:\n    f()\nexcept KeyError as e:\n    log(f'{e}')\n",
    "narrow_handler_str": "try:\n    f()\nexcept ConnectionError as e:\n    parse(str(e))\n",
    # rev 23 (round-21 claude N1): a pmcp field name on an exception that is
    # not a pmcp class.
    "mcp_error_data": "from mcp.shared.exceptions import MCPError\ntry:\n    f()\nexcept MCPError as e:\n    log(str(e.data))\n",
    "any_exception_error_field": "try:\n    f()\nexcept Exception as e:\n    log(e.error.message)\n",
    # rev 22 (round-20 claude N1): the attribute check is an allowlist, a
    # `setattr` hands off unless the exception is its target, and `**` into
    # a logging call can carry `exc_info`.
    "strerror_attribute": "try:\n    f()\nexcept OSError as e:\n    log(e.strerror + str(e.filename))\n",
    "unicode_object_attribute": "try:\n    f()\nexcept UnicodeDecodeError as e:\n    log(e.object)\n",
    "setattr_value": "try:\n    f()\nexcept Exception as e:\n    setattr(obj, 'err', e)\n",
    "logging_double_star": "KW = {'exc_info': True}\nlogger.warning('x', **KW)\n",
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
    """Each value rendered with its repr (`!r`, `repr()`, `+`, `%r`,
    `.format`, a later call argument) in a message with rejection wording,
    as (line, expression)."""
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
    """A repr in a rejecting message renders the value: every such site in
    `src/pmcp` is a named exemption with its provenance, and each exemption
    must still exist. `{x}`/`%s` of `x` were triaged by hand."""
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


# --- rev 19: a pmcp-authored description is raised outside the handler ------
#
# Since rev 18 an exception whose chain holds a validation or parse error is
# rendered as its class and that error's description, never its own message.
# A pmcp message built with a renderer (`f"Invalid policy file {path}:
# {exception_text(e)}"`) and raised inside the handler of such an error would
# therefore be withheld, and the operator would lose the file and the
# refusal. pmcp builds the description in the handler and raises after it,
# so the exception chains nothing (round-17 claude F001).

_DESCRIBERS = _RENDERERS - {"safe_exc_info"}


def _calls_a_describer(node: ast.AST) -> bool:
    return any(
        isinstance(inner, ast.Call)
        and (_callee(inner) in _DESCRIBERS or _callee(inner) in _RENDERER_METHODS)
        for inner in ast.walk(node)
    )


def _handler_nodes(handler: ast.ExceptHandler) -> list[ast.AST]:
    """The handler's own statements' nodes, not those of a nested function
    or class (which run later, outside the handler)."""
    out: list[ast.AST] = []
    pending: list[ast.AST] = list(handler.body)
    while pending:
        node = pending.pop()
        out.append(node)
        if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            continue
        pending.extend(ast.iter_child_nodes(node))
    return out


def _described_raises_in_handlers(source: str) -> list[int]:
    """Lines of each `raise` inside an `except` body whose exception carries a
    renderer's output: called in the raise itself, or through a name the same
    handler bound to an expression that calls one (`failure =
    exception_text(e)`, then `raise X(f"... {failure}")` or `raise error`)."""
    lines: list[int] = []
    for handler in ast.walk(ast.parse(source)):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        nodes = _handler_nodes(handler)
        described: set[str] = set()
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                value = node.value
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if value is not None and _calls_a_describer(value):
                    described |= {t.id for t in targets if isinstance(t, ast.Name)}
        for node in nodes:
            if not isinstance(node, ast.Raise) or node.exc is None:
                continue
            names = {n.id for n in ast.walk(node.exc) if isinstance(n, ast.Name)}
            if _calls_a_describer(node.exc) or names & described:
                lines.append(node.lineno)
    return sorted(lines)


def test_no_pmcp_description_is_raised_inside_a_handler() -> None:
    """Every pmcp exception whose message holds a renderer's description is
    raised after its handler, so its own text is what the operator sees."""
    found = [
        f"{path.relative_to(path.parents[2])}:{line}"
        for path in _sources()
        for line in _described_raises_in_handlers(path.read_text())
    ]
    assert not found, found


@pytest.mark.parametrize(
    ("snippet", "flagged"),
    [
        (
            "try:\n    f()\nexcept ValueError as e:\n"
            "    raise RuntimeError(f'bad {exception_text(e)}') from e\n",
            True,
        ),
        (
            "try:\n    f()\nexcept ValueError as e:\n"
            "    raise RuntimeError(f'bad {exception_text(e)}')\n",
            True,
        ),
        (
            "try:\n    f()\nexcept ValueError as e:\n"
            "    if x:\n        raise RuntimeError(describe_model_error(e, s, a)) from None\n",
            True,
        ),
        (
            "try:\n    f()\nexcept ValueError as e:\n"
            "    error = RuntimeError('bad ' + exception_text(e))\n    raise error\n",
            True,
        ),
        (
            "try:\n    f()\nexcept ValueError as e:\n"
            "    failure = exception_text(e)\nraise RuntimeError(f'bad {failure}')\n",
            False,
        ),
        (
            "try:\n    f()\nexcept ValueError as e:\n"
            "    failure = exception_text(e)\n"
            "    raise RuntimeError(f'bad {failure}') from e\n",
            True,
        ),
        (
            "try:\n    f()\nexcept ValueError as e:\n    raise RuntimeError('fixed') from e\n",
            False,
        ),
        (
            "try:\n    f()\nexcept ValueError as e:\n"
            "    def later():\n        raise RuntimeError(exception_text(e))\n",
            False,
        ),
    ],
)
def test_the_described_raise_rule_sees_its_spellings(
    snippet: str, flagged: bool
) -> None:
    assert bool(_described_raises_in_handlers(snippet)) is flagged, snippet


# --- rev 20: every exemption's reason is enforced (round-18 codex F001) -----


def _definition(name: str, home: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    tree = ast.parse((root / home).read_text())
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(found) == 1, (name, home, len(found))
    return found[0]


def _kinds(kind: str) -> list[tuple[str, str]]:
    return [(name, home) for name, (k, home) in _EXEMPT_CALLEES.items() if k == kind]


def _boolean(node: ast.AST | None) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, bool)
    if isinstance(node, (ast.Compare, ast.UnaryOp)) and (
        not isinstance(node, ast.UnaryOp) or isinstance(node.op, ast.Not)
    ):
        return True
    if isinstance(node, ast.BoolOp):
        return all(_boolean(value) for value in node.values)
    if isinstance(node, ast.Call):
        return _callee(node) in ("isinstance", "bool", "any", "all", "callable")
    return False


def test_every_predicate_exemption_returns_only_booleans() -> None:
    assert _kinds("predicate")
    for name, home in _kinds("predicate"):
        function = _definition(name, home)
        assert (
            function.returns is not None and ast.unparse(function.returns) == "bool"
        ), name
        own = [
            node
            for node in _own_nodes(function.body)
            if not any(
                node in ast.walk(inner)
                for inner in ast.walk(function)
                if inner is not function
                and isinstance(
                    inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
                )
            )
        ]
        returns = [n for n in own if isinstance(n, ast.Return)]
        assert returns and all(_boolean(r.value) for r in returns), (
            name,
            [ast.unparse(r) for r in returns if not _boolean(r.value)],
        )


def test_every_scanned_exemption_takes_an_annotated_exception() -> None:
    """The scanner seeds an annotated exception parameter over the whole
    body, so a `scanned` callee's body is checked like any other."""
    assert _kinds("scanned")
    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    for name, home in _kinds("scanned"):
        function = _definition(name, home)
        assert _exception_parameters(function, _namespace_for(root / home)), name


def test_every_guarded_exemption_checks_the_registry_first() -> None:
    """A `guarded` callee reads its exception only after
    `safe_exc_info(x) is None` returned or raised: the scanner, run on its
    body unblanked, finds nothing."""
    assert _kinds("guarded")
    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    for name, home in _kinds("guarded"):
        function = _definition(name, home)
        assert name not in _NON_RENDERING_FUNCTIONS
        guards = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.If)
            and any(
                _is_registry_check(node.test, arg.arg, passed=False)
                for arg in function.args.args
            )
            and _exits(node.body)
        ]
        assert guards, name
        found = exception_sinks(
            (root / home).read_text(), home, _namespace_for(root / home)
        )
        inside = [
            item
            for item in found
            if function.lineno <= int(item.split(":")[1]) <= (function.end_lineno or 0)
        ]
        assert inside == [], (name, inside)


def test_every_handoff_call_discards_its_result() -> None:
    """A `handoff` callee is only ever called as a statement in pmcp."""
    names = {name for name, _ in _kinds("handoff")}
    for path in _sources():
        tree = ast.parse(path.read_text())
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for call in ast.walk(tree):
            if isinstance(call, ast.Call) and _callee(call) in names:
                assert isinstance(parents.get(call), ast.Expr), (path, call.lineno)


_PASSTHROUGH_ATTRIBUTES = {"exceptions"}


def test_every_passthrough_exemption_reads_no_text() -> None:
    assert _kinds("passthrough")
    for name, home in _kinds("passthrough"):
        function = _definition(name, home)
        for node in ast.walk(function):
            assert not isinstance(node, (ast.JoinedStr, ast.FormattedValue)), name
            if isinstance(node, ast.Call):
                assert _callee(node) not in ("str", "repr", "format", "ascii"), name
                if _callee(node) == "getattr":
                    attribute = node.args[1] if len(node.args) > 1 else None
                    assert isinstance(attribute, ast.Constant), name
                    assert attribute.value in _PASSTHROUGH_ATTRIBUTES, name
            if isinstance(node, ast.Attribute):
                assert node.attr not in _TEXT_ATTRIBUTES - _PASSTHROUGH_ATTRIBUTES, name
            if isinstance(node, (ast.Yield, ast.YieldFrom, ast.Return)) and node.value:
                assert isinstance(node.value, (ast.Name, ast.Call)), name
                if isinstance(node.value, ast.Call):
                    assert _callee(node.value) == name, name  # recursion only


def test_every_reregisters_exemption_returns_a_registered_type() -> None:
    from pmcp.argument_errors import _value_bearing_types

    assert _kinds("reregisters")
    root = Path(__file__).resolve().parents[1] / "src" / "pmcp"
    registered = _value_bearing_types()
    for name, home in _kinds("reregisters"):
        function = _definition(name, home)
        assert function.returns is not None, name
        namespace = {**vars(builtins), **_namespace_for(root / home)}
        returned = eval(ast.unparse(function.returns), namespace)  # noqa: S307
        inner = typing.get_args(returned) or (returned,)
        assert all(
            isinstance(item, type) and issubclass(item, registered) for item in inner
        ), (name, returned)


def test_the_elicitation_parser_reads_no_registered_error() -> None:
    """Round-18 codex F001's falsifier, as filed: a genuine elicitation still
    parses; neither a registered error carrying elicitation-shaped JSON nor
    its wrapper is read as one."""
    import json as _json

    from pmcp.auth import parse_url_elicitation_error

    sentinel = "SENTINEL_ELICITATION_REJECTED_9137"
    payload = _json.dumps(
        {
            "code": -32042,
            "data": {"elicitationId": sentinel, "url": "https://example.com/consent"},
        }
    )
    assert parse_url_elicitation_error(payload)[0].elicitation_id == sentinel
    try:
        jsonschema.validate(payload, {"type": "integer"})
    except jsonschema.ValidationError as rejected:
        bare = rejected
        try:
            raise RuntimeError(rejected.message) from rejected
        except RuntimeError as caught:
            wrapped = caught
    else:  # pragma: no cover
        raise AssertionError("The input must fail validation")
    observed = [parse_url_elicitation_error(error) for error in (bare, wrapped)]
    assert observed == [[], []], observed


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["bare", "wrapped"])
async def test_gateway_invoke_reads_no_elicitation_from_a_rejected_value(
    shape: str,
) -> None:
    """Round-18 codex F001 end to end, through `gateway.invoke`: a downstream
    call that fails with a registered error -- or a wrapper of one -- whose
    rejected value is elicitation-shaped JSON gets no `url_elicitations`, and
    no part of the value reaches the result."""
    import json as _json

    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools
    from pmcp.types import RiskHint, ToolInfo
    from tests.test_tools import MockClientManager

    sentinel = "SENTINEL_ELICITATION_REJECTED_9137"
    payload = _json.dumps(
        {
            "error": {
                "code": -32042,
                "data": {
                    "elicitationId": sentinel,
                    "url": f"https://example.com/{sentinel}",
                },
            }
        }
    )
    try:
        jsonschema.validate(payload, {"type": "integer"})
    except jsonschema.ValidationError as rejected:
        error: BaseException = rejected
        if shape == "wrapped":
            try:
                raise RuntimeError(rejected.message) from rejected
            except RuntimeError as caught:
                error = caught
    tool = ToolInfo(
        tool_id="remote-auth::login",
        server_name="remote-auth",
        tool_name="login",
        description="Login",
        short_description="Login",
        input_schema={},
        tags=[],
        risk_hint=RiskHint.LOW,
    )
    client_manager = MockClientManager([tool])

    async def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    client_manager.call_tool = fail  # type: ignore[method-assign]
    tools = GatewayTools(client_manager=client_manager, policy_manager=PolicyManager())  # type: ignore[arg-type]
    result = await tools.invoke({"tool_id": "remote-auth::login", "arguments": {}})
    assert result.ok is False
    assert not result.url_elicitations, result.url_elicitations
    assert result.auth_state != "elicitation_required"
    assert sentinel not in result.model_dump_json(), result.model_dump_json()


#: The attributes a ``fields`` exemption may read: an HTTP status and the
#: response's header map (`urllib.error.HTTPError`).
_FIELD_ATTRIBUTES = {"code", "headers", "status", "problem_mark", "context_mark"}


def test_every_fields_exemption_reads_only_its_fields() -> None:
    """A `fields` callee reads its exception only through `isinstance` or an
    attribute in `_FIELD_ATTRIBUTES`, and returns no text: its returns are
    numbers, `None`, or pmcp-built results (rev 21/22)."""
    assert _kinds("fields")
    # `_yaml_position` returns a mark's line and column only.
    position = _definition("_yaml_position", "parsing.py")
    assert ast.unparse(position.returns) == "tuple[int | None, int | None]"
    for name, home in _kinds("fields"):
        function = _definition(name, home)
        parameter = function.args.args[0].arg
        parents = {c: n for n in ast.walk(function) for c in ast.iter_child_nodes(n)}
        for node in ast.walk(function):
            if not (isinstance(node, ast.Name) and node.id == parameter):
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Attribute):
                assert parent.attr in _FIELD_ATTRIBUTES, (name, parent.attr)
            elif isinstance(parent, ast.Call):
                callee = _callee(parent)
                assert callee in ("isinstance", "type", "getattr"), (name, callee)
                if callee == "getattr":
                    assert isinstance(parent.args[1], ast.Constant), name
                    assert parent.args[1].value in _FIELD_ATTRIBUTES, name
            else:
                raise AssertionError((name, type(parent).__name__))


@pytest.mark.asyncio
async def test_invoke_does_not_parse_a_rejected_connection_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-19 codex F001's falsifier, as filed: a `ConnectionError` built
    from a rejected `WWW-Authenticate` string, chained to the validation
    error, gives `gateway.invoke` no auth challenge and none of the value."""
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools
    from pmcp.types import RiskHint, ToolInfo
    from tests.test_tools import MockClientManager

    sentinel = "REJECTED_SCOPE_SENTINEL_9137"
    rejected_value = f'WWW-Authenticate: Bearer scope="{sentinel}"'
    tool = ToolInfo(
        tool_id="remote-auth::login",
        server_name="remote-auth",
        tool_name="login",
        description="Login",
        short_description="Login",
        input_schema={},
        tags=[],
        risk_hint=RiskHint.LOW,
    )
    manager = MockClientManager([tool])

    async def fail(*_args: Any, **_kwargs: Any) -> Any:
        try:
            jsonschema.validate(rejected_value, {"type": "integer"})
        except jsonschema.ValidationError as rejected:
            raise ConnectionError(rejected.message) from rejected

    manager.call_tool = fail  # type: ignore[method-assign]
    policy = tmp_path / "policy.json"
    policy.write_text("{}")
    monkeypatch.setattr(GatewayTools, "_load_provisioned_registry", lambda self: {})
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager(policy))  # type: ignore[arg-type]
    result = await tools.invoke({"tool_id": "remote-auth::login", "arguments": {}})
    assert not result.ok
    assert sentinel not in result.model_dump_json(), result.model_dump_json()
    assert result.auth_challenge is None


@pytest.mark.asyncio
async def test_invoke_still_reads_a_genuine_connection_challenge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same path with an unchained `ConnectionError` -- a downstream's own
    401 -- still yields its challenge: `exception_text` is `str()` there."""
    from pmcp.policy.policy import PolicyManager
    from pmcp.tools.handlers import GatewayTools
    from pmcp.types import RiskHint, ToolInfo
    from tests.test_tools import MockClientManager

    tool = ToolInfo(
        tool_id="remote-auth::login",
        server_name="remote-auth",
        tool_name="login",
        description="Login",
        short_description="Login",
        input_schema={},
        tags=[],
        risk_hint=RiskHint.LOW,
    )
    manager = MockClientManager([tool])

    async def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError(
            '401 Unauthorized WWW-Authenticate: Bearer scope="repo read:org"'
        )

    manager.call_tool = fail  # type: ignore[method-assign]
    policy = tmp_path / "policy.json"
    policy.write_text("{}")
    monkeypatch.setattr(GatewayTools, "_load_provisioned_registry", lambda self: {})
    tools = GatewayTools(client_manager=manager, policy_manager=PolicyManager(policy))  # type: ignore[arg-type]
    result = await tools.invoke({"tool_id": "remote-auth::login", "arguments": {}})
    assert result.auth_challenge is not None, result.model_dump_json()
