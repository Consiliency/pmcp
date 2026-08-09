"""SL-5.4 — the AST honesty guard for IF-0-P3B-1's "self-scheduling is the
correctness mechanism, not the flush call sites" claim (Execution Notes >
Decision 2).

`ClientManager` mutates `self._tools` / `self._resources` / `self._prompts`
from exactly five publishing methods (`_index_tools`, `_index_resources`,
`_index_prompts`, `_remove_server_indexes`, `_disconnect_all_unlocked`) plus
the one deliberately-exempt `__init__` (no bus/sink exists at construction
-- see `plans/phase-plan-v11-P3B.md`, Context > "The publisher gap").
Rather than trusting that partition to stay accurate as `client/manager.py`
grows, this module parses the file with `ast` and fails, naming the
offending function, if a write to one of the three catalog dicts ever
appears anywhere else. This is what keeps EC-P3B-4 true *after* the phase,
not just on the day it was written -- modelled on P5's
`test_credential_predicate_guard.py`.

The two sets below are separate constants with a comment on the exemption
specifically so that silencing a future failure by widening the allowlist
is a visible, greppable edit rather than an invisible one.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANAGER_PATH = REPO_ROOT / "src" / "pmcp" / "client" / "manager.py"

_TARGET_ATTRS = {"_tools", "_resources", "_prompts"}

# The five methods allowed to write to a catalog dict -- each of them must
# also call a matching `self._catalog_events.note_*` (checked below).
_PUBLISHING_METHODS = {
    "_index_tools",
    "_index_resources",
    "_index_prompts",
    "_remove_server_indexes",
    "_disconnect_all_unlocked",
}

# The ONE exempt writer: `__init__`'s annotated empty-dict construction.
# No bus, no sink, and no subscriber can exist yet at that point (IF-0-P3B-1),
# so publishing there is not just unnecessary but impossible to do honestly.
# Widening this set to silence a failure is exactly the mistake this test
# exists to catch -- do not add a name here without a comment explaining why
# the new writer genuinely cannot publish.
_EXEMPT_METHODS = {"__init__"}


def _self_attr_name(node: ast.AST) -> str | None:
    """If `node` is `self._tools` / `self._resources` / `self._prompts`
    (an `ast.Attribute` on `ast.Name('self')`), return the attr name."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
        and node.attr in _TARGET_ATTRS
    ):
        return node.attr
    return None


class _WriteFinder(ast.NodeVisitor):
    """Walks the module tracking the enclosing function for every node, and
    records: (a) every write to a catalog dict, attributed to its enclosing
    function; (b) every `self._catalog_events.note_*(...)` call, also
    attributed to its enclosing function."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []
        self.note_calls: dict[str, set[str]] = {}
        self._func_stack: list[str] = []

    def _current_func(self) -> str:
        assert self._func_stack, (
            "a write to a catalog dict outside any function -- module-level "
            "state is not this guard's model and should not exist"
        )
        return self._func_stack[-1]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._check_write_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # Required for __init__'s annotated `self._tools: dict[...] = {}`
        # (client/manager.py:497-499) to be matched at all -- without this
        # visitor method the exemption above would be vacuous, because the
        # one write __init__ makes would simply never be seen.
        self._check_write_target(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_write_target(node.target)
        self.generic_visit(node)

    def _check_write_target(self, target: ast.AST) -> None:
        attr = _self_attr_name(target)
        if attr is not None:
            self.writes.append((self._current_func(), attr))
            return
        if isinstance(target, ast.Subscript):
            attr = _self_attr_name(target.value)
            if attr is not None:
                self.writes.append((self._current_func(), attr))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {"pop", "clear"}:
            attr = _self_attr_name(func.value)
            if attr is not None:
                self.writes.append((self._current_func(), attr))
        if (
            isinstance(func, ast.Attribute)
            and func.attr
            in {"note_tools_changed", "note_resources_changed", "note_prompts_changed"}
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "_catalog_events"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
            and self._func_stack
        ):
            self.note_calls.setdefault(self._func_stack[-1], set()).add(func.attr)
        self.generic_visit(node)


def _parse_manager() -> _WriteFinder:
    tree = ast.parse(MANAGER_PATH.read_text())
    finder = _WriteFinder()
    finder.visit(tree)
    return finder


def test_ast_guard_actually_finds_writes() -> None:
    """A guard that silently matches nothing is worse than no guard --
    pin that the walk finds the writes we know are there, so a refactor of
    this test file itself can't turn it into a vacuous pass."""
    finder = _parse_manager()
    assert finder.writes, "AST walk over client/manager.py found zero writes"
    found_functions = {func for func, _attr in finder.writes}
    # Every publishing method must actually appear as a writer -- otherwise
    # the offender-detection below would be trivially satisfied by a
    # manager.py that stopped writing to the catalogs altogether.
    assert _PUBLISHING_METHODS <= found_functions, (
        f"expected writes from {_PUBLISHING_METHODS}, saw {found_functions}"
    )
    # __init__'s write must be seen too, specifically via ast.AnnAssign
    # (its `self._tools: dict[...] = {}` is annotated) -- without that
    # visitor method the exemption in the test below would be vacuous: it
    # would never fire because __init__ would never appear as a writer at
    # all. This is the exact hole the plan's ast.AnnAssign requirement
    # exists to close.
    assert "__init__" in found_functions, (
        "AST walk never saw __init__ write to a catalog dict -- "
        "visit_AnnAssign is broken and the __init__ exemption is vacuous"
    )


def test_every_catalog_write_is_in_a_publishing_mutator_or_init() -> None:
    """The honesty guard itself: every write to `self._tools` /
    `self._resources` / `self._prompts` must be inside one of the five
    publishing mutators, or `__init__` (the one named, commented exemption).
    A new mutation path added anywhere else fails here with the offending
    function's name."""
    finder = _parse_manager()
    offenders = [
        (func, attr)
        for func, attr in finder.writes
        if func not in _PUBLISHING_METHODS and func not in _EXEMPT_METHODS
    ]
    assert not offenders, (
        "write(s) to a catalog dict outside the five publishing mutators "
        f"(or the exempt __init__): {offenders} -- either route the write "
        "through a publishing mutator, or widen _PUBLISHING_METHODS with a "
        "comment explaining why the new site is safe to skip"
    )


def test_each_publishing_mutator_calls_a_note_method() -> None:
    """Every one of the five publishing mutators must call at least one
    `self._catalog_events.note_*` -- a mutator that writes to a catalog dict
    but calls no `note_*` is exactly the listener-with-no-publishers defect
    this phase exists to prevent."""
    finder = _parse_manager()
    for method in sorted(_PUBLISHING_METHODS):
        assert method in finder.note_calls, (
            f"{method} writes to a catalog dict but never calls a "
            "self._catalog_events.note_* method"
        )


def test_init_writes_no_note_call() -> None:
    """`__init__` is exempt precisely because no sink/bus/subscriber exists
    yet -- pin that it is not accidentally calling `note_*` either (which
    would mean a real bus publish before `BusCatalogEventSink` is built,
    the ordering IF-0-P3B-2 exists to prevent)."""
    finder = _parse_manager()
    assert finder.note_calls.get("__init__", set()) == set()
