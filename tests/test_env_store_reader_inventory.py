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
    "plain .env": "load_dotenv() discovery of `.env` from pmcp's own module dir",
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


def _calls() -> Counter[tuple[str, str, str]]:
    found: Counter[tuple[str, str, str]] = Counter()

    def visit(node: ast.AST, scope: list[str], rel: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = [*scope, child.name]
            if isinstance(child, ast.Call):
                func = child.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else None
                )
                if name in PRIMITIVES:
                    found[(rel, ".".join(scope) or "<module>", name)] += 1
            visit(child, inner, rel)

    for source in sorted(SRC.rglob("*.py")):
        rel = source.relative_to(SRC).as_posix()
        visit(ast.parse(source.read_text(encoding="utf-8")), [], rel)
    return found


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


def test_every_link_following_reader_is_documented() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    guide = (ROOT / "MIGRATING.md").read_text(encoding="utf-8")
    classes_in_code = {cls for _count, cls in INVENTORY.values()}
    for cls, phrase in FOLLOWS_A_PROJECT_LINK.items():
        assert cls in classes_in_code, f"documented class {cls!r} has no call site"
        assert phrase in changelog, f"CHANGELOG.md does not name {phrase!r}"
        assert phrase in guide, f"MIGRATING.md does not name {phrase!r}"
    count = len(FOLLOWS_A_PROJECT_LINK)
    assert (
        f"{['Zero', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight'][count]} readers"
        in changelog
    )
    assert (
        f"{['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight'][count]} other readers"
        in changelog.lower()
    )
    assert (
        "consiliency/pmcp#367" in changelog.lower() and "Consiliency/pmcp#367" in guide
    )


def test_the_inventory_scan_sees_each_primitive() -> None:
    """Positive control: the AST scan finds every primitive shape it pins."""
    names = {callee for _rel, _scope, callee in _calls()}
    assert names == PRIMITIVES
