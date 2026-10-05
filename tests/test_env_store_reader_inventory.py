"""Every reader of a credential store is inventoried, and the link-followers are documented.

Round 6 on Consiliency/pmcp#366: the list of readers that still follow a
project ``.env.pmcp`` link (Consiliency/pmcp#367, stays open) was written from
memory and missed four. This pins the list to the code: every call in
``src/pmcp`` to a store-reading primitive is enumerated by AST, each call site
is classified here, and every class that follows a project link must be named,
with its exact phrase, in both CHANGELOG.md and MIGRATING.md. A new reader
fails this test until it is classified -- and, if it follows a link, documented.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "pmcp"

#: The primitives that open a dotenv store by path.
PRIMITIVES = {
    "read_env_file",
    "read_env_text",
    "_read_env_file_strict",
    "load_dotenv",
    "dotenv_values",
}

#: Classes of reader that FOLLOW a project-store link, and the exact phrase each
#: is documented under (CHANGELOG.md and MIGRATING.md must both contain it).
FOLLOWS_A_PROJECT_LINK = {
    "remote-header auth": "remote-header auth",
    "tenant store": "the tenant store `.pmcp/tenants/<id>/pmcp.env`",
    "credential-availability check": "the gateway's credential-availability check",
    "env stripping": "env stripping",
    "feedback gate": "the feedback gate's planted-key check",
    "secrets check": "`pmcp secrets check`",
}

#: Call sites that do NOT follow a project-store link, and why.
NOT_A_PROJECT_LINK_FOLLOWER = {
    "primitive": "the reader itself; classified at its callers",
    "user store only": "reads only the operator's own ~/.config/pmcp/pmcp.env",
    "confined": "reads the project store through atomic_write.read_confined",
    "plain .env": (
        "load_dotenv() discovery of `.env` upward from pmcp's own install "
        "location; a residual of its own (documented, Consiliency/pmcp#372)"
    ),
}

#: (file, enclosing qualname, callee) -> (number of calls, class).
INVENTORY: dict[tuple[str, str, str], tuple[int, str]] = {
    ("remote_auth.py", "build_remote_header_env_lookup", "read_env_file"): (
        2,
        "remote-header auth",
    ),
    ("remote_auth.py", "resolve_remote_headers_for_tenant", "read_env_file"): (
        1,
        "tenant store",
    ),
    ("tools/handlers.py", "GatewayTools._check_api_key_available", "read_env_text"): (
        1,
        "credential-availability check",
    ),
    ("tools/handlers.py", "GatewayTools._check_api_key_available", "load_dotenv"): (
        1,
        "credential-availability check",
    ),
    ("env_store.py", "managed_secret_keys", "read_env_file"): (2, "env stripping"),
    ("env_store.py", "managed_secret_keys_strict", "_read_env_file_strict"): (
        2,
        "feedback gate",
    ),
    ("cli_commands/secrets.py", "run_secrets_check", "read_env_file"): (
        2,
        "secrets check",
    ),
    ("env_store.py", "read_env_file", "read_env_text"): (1, "primitive"),
    ("env_store.py", "read_env_file", "dotenv_values"): (1, "primitive"),
    ("env_store.py", "_read_env_file_strict", "read_env_file"): (1, "primitive"),
    ("env_store.py", "read_store_for_update", "read_env_file"): (1, "user store only"),
    ("env_store.py", "read_store_for_update", "dotenv_values"): (1, "confined"),
    ("cli_commands/doctor.py", "_read_user_pmcp_env", "read_env_file"): (
        1,
        "user store only",
    ),
    ("cli.py", "load_startup_env", "load_dotenv"): (2, "plain .env"),
    ("cli.py", "_load_project_store_at_startup", "load_dotenv"): (1, "confined"),
}


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Local names bound to a primitive by import: ``from x import y as z``."""
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in PRIMITIVES:
                    bound[alias.asname or alias.name] = alias.name
    return bound


def _scan() -> tuple[Counter[tuple[str, str, str]], list[str]]:
    """Every call to a primitive, by its real name, and every non-call use of one.

    Calls are matched through import aliases (``dotenv_values as _dv``) and as
    attributes (``dotenv.dotenv_values``). Any other reference to a primitive --
    assigning it, passing it, rebinding it -- would let a call escape the scan,
    so it is reported separately and must not exist.
    """
    found: Counter[tuple[str, str, str]] = Counter()
    escapes: list[str] = []

    def primitive_of(expr: ast.expr, bound: dict[str, str]) -> str | None:
        if isinstance(expr, ast.Name):
            return bound.get(expr.id)
        if isinstance(expr, ast.Attribute) and expr.attr in PRIMITIVES:
            return expr.attr
        return None

    def visit(node: ast.AST, scope: list[str], rel: str, bound: dict[str, str]) -> None:
        """Handle ``node`` ITSELF, then every child (a call's callee excluded)."""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = [*scope, node.name]
        children = list(ast.iter_child_nodes(node))
        if isinstance(node, ast.Call):
            name = primitive_of(node.func, bound)
            if name is not None:
                found[(rel, ".".join(scope) or "<module>", name)] += 1
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                if primitive_of(arg, bound) is not None:
                    escapes.append(f"{rel}:{arg.lineno} passes a store reader")
            # A plain callee is accounted for above; do not re-visit it as a use.
            # A computed callee (`partial(reader)(...)`) is visited normally.
            if isinstance(node.func, (ast.Name, ast.Attribute)):
                children = [c for c in children if c is not node.func]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            if primitive_of(node.value, bound) is not None:
                escapes.append(f"{rel}:{node.lineno} rebinds a store reader")
        for child in children:
            visit(child, scope, rel, bound)

    for source in sorted(SRC.rglob("*.py")):
        rel = source.relative_to(SRC).as_posix()
        tree = ast.parse(source.read_text(encoding="utf-8"))
        bound = _aliases(tree)
        bound.update({p: p for p in PRIMITIVES if p not in bound})
        visit(tree, [], rel, bound)
    return found, escapes


def _calls() -> Counter[tuple[str, str, str]]:
    return _scan()[0]


def test_no_store_reader_escapes_the_scan() -> None:
    assert _scan()[1] == []


def test_the_scan_resolves_aliases_and_flags_escapes(tmp_path: Path) -> None:
    """Positive control on a synthetic module (round 7 N-1)."""
    import textwrap

    module = textwrap.dedent(
        """
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
    )
    global SRC
    real_src = SRC
    (tmp_path / "sneaky.py").write_text(module, encoding="utf-8")
    SRC = tmp_path
    try:
        found, escapes = _scan()
    finally:
        SRC = real_src
    assert found == Counter(
        {
            ("sneaky.py", "sneaky", "dotenv_values"): 1,
            ("sneaky.py", "sneaky", "load_dotenv"): 1,
            ("sneaky.py", "sneaky", "read_env_file"): 1,
        }
    )
    assert len(escapes) == 3, escapes


def test_every_store_reader_is_inventoried_and_classified() -> None:
    found = dict(_calls())
    pinned = {key: count for key, (count, _cls) in INVENTORY.items()}
    assert found == pinned, (
        "a store-reading call site changed: classify it in INVENTORY, and if it "
        "follows a project .env.pmcp link, document it in CHANGELOG.md and "
        f"MIGRATING.md. found - pinned: {set(found.items()) - set(pinned.items())}; "
        f"pinned - found: {set(pinned.items()) - set(found.items())}"
    )
    for key, (_count, cls) in INVENTORY.items():
        assert cls in FOLLOWS_A_PROJECT_LINK or cls in NOT_A_PROJECT_LINK_FOLLOWER, key


def _norm(text: str) -> str:
    return " ".join(text.split())


def _between(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i : text.index(end, i + len(start))]


#: The short name each class goes by in running prose.
SHORT_NAMES = {
    "remote-header auth": "remote-header auth",
    "tenant store": "the tenant store",
    "credential-availability check": "the gateway's credential-availability check",
    "env stripping": "env stripping",
    "feedback gate": "the feedback gate's planted-key check",
    "secrets check": "`pmcp secrets check`",
}


def test_every_link_following_reader_is_documented() -> None:
    """Each class is named in EVERY place the residual is stated, not just somewhere.

    The CHANGELOG's #248 entry; MIGRATING.md's section "What changed" paragraph;
    and its Known-issues item for Consiliency/pmcp#367.
    """
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    guide = (ROOT / "MIGRATING.md").read_text(encoding="utf-8")
    places = {
        "CHANGELOG #248 entry": _between(
            changelog, "- **A symlinked `pmcp.env` is written through", "\n- **"
        ),
        "MIGRATING What changed": _between(
            guide,
            "### A project `.env.pmcp` that is a symlink leaving the project is refused",
            "**What to do.**",
        ),
        "MIGRATING Known issues": _between(
            guide, "- **Six readers still follow a project `.env.pmcp`", "\n- **"
        ),
    }
    classes_in_code = {cls for _count, cls in INVENTORY.values()}
    assert set(SHORT_NAMES) == set(FOLLOWS_A_PROJECT_LINK)
    for cls in FOLLOWS_A_PROJECT_LINK:
        assert cls in classes_in_code, f"documented class {cls!r} has no call site"
        for where, text in places.items():
            assert _norm(SHORT_NAMES[cls]) in _norm(text), (
                f"{where} does not name {SHORT_NAMES[cls]!r}"
            )
    for where in ("CHANGELOG #248 entry", "MIGRATING Known issues"):
        assert FOLLOWS_A_PROJECT_LINK["tenant store"] in places[where], where
    # The Known-issues item is a LIST: each class is its own bullet, so a class
    # mentioned only in passing elsewhere in the item does not count.
    known = _norm(places["MIGRATING Known issues"])
    for cls, name in SHORT_NAMES.items():
        assert f"- {name}" in known, f"Known issues has no bullet for {cls!r}"
    for where, text in places.items():
        assert "Consiliency/pmcp#367" in text, where
        assert "Six" in text or "six" in text.lower(), where
    assert (
        "can be sent as a header to a remote server the repository configures"
        in _norm(places["CHANGELOG #248 entry"])
    )
    assert (
        "can be sent as a header to a remote server the repository configures"
        in _norm(places["MIGRATING Known issues"])
    )


def test_the_inventory_scan_sees_each_primitive() -> None:
    """Positive control: the AST scan finds every primitive shape it pins."""
    names = {callee for _rel, _scope, callee in _calls()}
    assert names == PRIMITIVES
