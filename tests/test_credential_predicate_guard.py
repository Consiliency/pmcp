"""Centralization guard for `requires_api_key` (P5, SL-4.1).

This test is a tripwire, not a proof: it fails when a new gate reads
`server.requires_api_key` (or its dynamic equivalents) directly instead of
going through `credential_requirement`/`requires_credential`
(src/pmcp/manifest/loader.py). A read reached through a variable alias (e.g.
`f = operator.attrgetter("requires_api_key")`) evades all checks here — every
gate also carries a behavioural fail-closed test in
tests/test_credential_gates_startup.py / tests/test_credential_gates_handlers.py,
which is what actually proves EC-P5-2.

Four checks, per plans/phase-plan-v11-P5.md:

1. Every `ast.Attribute` load of `requires_api_key` under src/pmcp/ must have
   an enclosing function on the allowlist below (the CapabilityCandidate
   wire-field consumers — a naive "only credential_requirement may read the
   attribute" rule cannot pass, because these four reads are intentionally
   preserved).
1a. No call anywhere under src/pmcp/ passes `os.environ` (or
   `os.environ.copy()`) as `child_env` to `credential_requirement` /
   `requires_credential` — clause 2a of IF-0-P5-1 forbids it.
2. No dynamic read (`getattr(x, "requires_api_key", ...)`, `x["requires_api_key"]`,
   `x.get("requires_api_key")`) outside manifest/loader.py — this closes the
   string-literal bypass that would defeat check 1.
3. Every allowlisted (file, function) pair still exists and still contains a
   matching attribute read, so a stale entry cannot silently widen the guard
   after the code it named is deleted or renamed.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "pmcp"

# (file path relative to src/pmcp, enclosing function name, why it's exempt)
# These read CapabilityCandidate.requires_api_key — the *wire field* fed by
# _get_server_env_metadata's effective value (IF-0-P5-3) — not a manifest
# ServerConfig's declared value. AST cannot distinguish the two by type, so
# the exemption is explicit and reviewed by hand.
#
# _configured_duplicate_missing_credential and _get_server_env_metadata each
# additionally have ONE attribute read of manifest_server.requires_api_key in
# their remote-configured-duplicate branch: extra_env cannot reach a remote
# connection, so that branch deliberately reads the DECLARED requirement
# only, bypassing credential_requirement()'s relaxation logic on purpose
# (Consiliency/pmcp#114 board review finding 1, remote variant).
ALLOWLISTED_ATTRIBUTE_READS: set[tuple[str, str]] = {
    ("tools/handlers.py", "_sort_key"),
    ("tools/handlers.py", "request_capability"),
    ("tools/handlers.py", "_configured_duplicate_missing_credential"),
    ("tools/handlers.py", "_get_server_env_metadata"),
}

# File exempted from check 2 in full: the centralization point every other
# gate routes through — duck-types over manifest ServerConfig,
# discovered-server configs, and None, so it necessarily reads the string
# literal dynamically.
DYNAMIC_READ_ALLOWED_FILE = "manifest/loader.py"

# (file path, enclosing function) pairs — OUTSIDE DYNAMIC_READ_ALLOWED_FILE —
# individually allowed one dynamic read each, reviewed by hand.
DYNAMIC_READ_ALLOWLIST: set[tuple[str, str]] = {
    # Same remote-configured-duplicate exemption as the two attribute reads
    # above: extra_env cannot reach a remote connection, so this branch
    # deliberately reads the declared requirement only, via getattr since
    # manifest_server is typed Any here (Consiliency/pmcp#114 board review
    # finding 1, remote variant).
    ("config/loader.py", "_eager_requires_credential"),
}


def _iter_py_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _rel(path: Path) -> str:
    return str(path.relative_to(SRC_ROOT))


class _FuncStackVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.func_stack: list[str] = []
        self.attribute_reads: list[tuple[int, str]] = []
        self.dynamic_reads: list[tuple[int, str]] = []
        self.child_env_os_environ_calls: list[int] = []

    def _current_func(self) -> str:
        return self.func_stack[-1] if self.func_stack else "<module>"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "requires_api_key" and isinstance(node.ctx, ast.Load):
            self.attribute_reads.append((node.lineno, self._current_func()))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        sl = node.slice
        if isinstance(sl, ast.Constant) and sl.value == "requires_api_key":
            self.dynamic_reads.append((node.lineno, self._current_func()))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # getattr(x, "requires_api_key", ...)
        if isinstance(node.func, ast.Name) and node.func.id == "getattr":
            if (
                len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "requires_api_key"
            ):
                self.dynamic_reads.append((node.lineno, self._current_func()))
        # x.get("requires_api_key")
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "requires_api_key"
        ):
            self.dynamic_reads.append((node.lineno, self._current_func()))

        # child_env=os.environ / os.environ.copy() passed to
        # credential_requirement / requires_credential
        callee_name = None
        if isinstance(node.func, ast.Name):
            callee_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee_name = node.func.attr
        if callee_name in ("credential_requirement", "requires_credential"):
            for kw in node.keywords:
                if kw.arg != "child_env":
                    continue
                value = kw.value
                if _is_os_environ_expr(value):
                    self.child_env_os_environ_calls.append(node.lineno)

        self.generic_visit(node)


def _is_os_environ_expr(node: ast.expr) -> bool:
    """True for `os.environ` or `os.environ.copy()`."""
    target = node
    if isinstance(target, ast.Call) and isinstance(target.func, ast.Attribute):
        if target.func.attr == "copy":
            target = target.func.value
    return (
        isinstance(target, ast.Attribute)
        and target.attr == "environ"
        and isinstance(target.value, ast.Name)
        and target.value.id == "os"
    )


def _scan_all_files() -> dict[str, _FuncStackVisitor]:
    results: dict[str, _FuncStackVisitor] = {}
    for path in _iter_py_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        visitor = _FuncStackVisitor()
        visitor.visit(tree)
        results[_rel(path)] = visitor
    return results


class TestManifestReadsAllowlist:
    """Check 1: every ast.Attribute load of requires_api_key must be inside
    an allowlisted (file, function)."""

    def test_no_unallowlisted_attribute_reads(self) -> None:
        scan = _scan_all_files()
        violations: list[str] = []
        for rel_path, visitor in scan.items():
            for lineno, func_name in visitor.attribute_reads:
                if (rel_path, func_name) not in ALLOWLISTED_ATTRIBUTE_READS:
                    violations.append(f"{rel_path}:{lineno} in {func_name}()")
        assert violations == [], (
            "Found requires_api_key attribute read(s) outside the allowlist "
            "— route through credential_requirement()/requires_credential() "
            f"instead, or add a reviewed allowlist entry: {violations}"
        )


class TestChildEnvMisuse:
    """Check 1a: no call passes os.environ (or a copy of it) as child_env."""

    def test_no_os_environ_passed_as_child_env(self) -> None:
        scan = _scan_all_files()
        violations: list[str] = []
        for rel_path, visitor in scan.items():
            for lineno in visitor.child_env_os_environ_calls:
                violations.append(f"{rel_path}:{lineno}")
        assert violations == [], (
            "child_env=os.environ (or a copy) reintroduces the env-strip "
            f"inversion — see IF-0-P5-1 clause 2a: {violations}"
        )


class TestDynamicReads:
    """Check 2: getattr/subscript/.get bypasses of the string literal are
    only permitted inside manifest/loader.py."""

    def test_no_dynamic_reads_outside_loader(self) -> None:
        scan = _scan_all_files()
        violations: list[str] = []
        for rel_path, visitor in scan.items():
            if rel_path == DYNAMIC_READ_ALLOWED_FILE:
                continue
            for lineno, func_name in visitor.dynamic_reads:
                if (rel_path, func_name) in DYNAMIC_READ_ALLOWLIST:
                    continue
                violations.append(f"{rel_path}:{lineno} in {func_name}()")
        assert violations == [], (
            "Found a dynamic ('requires_api_key' string-literal) read "
            f"outside manifest/loader.py and outside the reviewed "
            f"allowlist: {violations}"
        )

    def test_loader_dynamic_read_exists(self) -> None:
        """Sanity check that the exemption isn't vacuous — loader.py really
        does read requires_api_key dynamically (inside credential_requirement)."""
        scan = _scan_all_files()
        visitor = scan[DYNAMIC_READ_ALLOWED_FILE]
        assert any(
            func_name == "credential_requirement"
            for _lineno, func_name in visitor.dynamic_reads
        )


class TestAllowlistLiveness:
    """Check 3: every allowlisted entry still exists and still contains a
    matching read, so it cannot silently widen the guard after the code it
    named is deleted or renamed."""

    def test_allowlisted_entries_still_present(self) -> None:
        scan = _scan_all_files()
        for rel_path, func_name in ALLOWLISTED_ATTRIBUTE_READS:
            assert rel_path in scan, f"Allowlisted file {rel_path} no longer exists"
            visitor = scan[rel_path]
            matching = [
                fn for _lineno, fn in visitor.attribute_reads if fn == func_name
            ]
            assert matching, (
                f"Allowlist entry ({rel_path}, {func_name}) no longer "
                "corresponds to any requires_api_key read — remove the stale "
                "entry so the guard doesn't silently widen"
            )

    def test_dynamic_read_allowlist_entries_still_present(self) -> None:
        scan = _scan_all_files()
        for rel_path, func_name in DYNAMIC_READ_ALLOWLIST:
            assert rel_path in scan, f"Allowlisted file {rel_path} no longer exists"
            visitor = scan[rel_path]
            matching = [fn for _lineno, fn in visitor.dynamic_reads if fn == func_name]
            assert matching, (
                f"Dynamic-read allowlist entry ({rel_path}, {func_name}) no "
                "longer corresponds to any requires_api_key dynamic read — "
                "remove the stale entry so the guard doesn't silently widen"
            )
