"""What an overlay does with every field in every value shape, generated from the code.

Consiliency/pmcp#342 (board rounds 1 and 2 on Consiliency/pmcp#375): the docs'
statements about which overlay fields are accepted or skipped drifted twice from
the code. This module generates the behaviour, so a test can hold every
documented claim to it instead of to a hand-checked list.

For every ``ServerConfig`` and ``CLIAlternative`` field (except ``name``), every
value shape in ``SHAPES`` (written as YAML text, so the YAML-special types are
real: dates, timestamps, ``!!binary``, ``!!set``) and, for servers, an entry
with and without a ``url``, it loads a user overlay holding one entry and
records ``skipped`` or ``loaded`` with the value the loaded entry holds.

It needs only ``load_manifest``, so it runs on any tree:

    PYTHONPATH=<tree>/src python tests/overlay_field_table.py > table.tsv
"""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# (shape, YAML text of the value)
SHAPES: list[tuple[str, str]] = [
    ("null", "null"),
    ("int", "5"),
    ("bool", "true"),
    ("float", "1.5"),
    ("str", '"zz-text"'),
    ("list-str", '["a", "b"]'),
    ("list-int", "[1]"),
    ("dict", "{a: b}"),
    ("date", "2026-10-04"),
    ("datetime", "2026-10-04T12:00:00Z"),
    ("binary", "!!binary aGVsbG8="),
    ("set", "!!set {a: null, b: null}"),
    ("nested", "[{a: [1]}]"),
]
# The entry each field value is placed in (the field overrides the base).
SERVER_BASES: dict[str, str] = {
    "local": "command: npx\n    keywords: [zzt]",
    "url": "url: https://example.invalid/mcp\n    keywords: [zzt]",
}
CLI_BASE = 'keywords: [zzt]\n    check_command: ["git", "--version"]'


def _fields() -> tuple[list[str], list[str]]:
    from pmcp.manifest.loader import CLIAlternative, ServerConfig

    servers = [f.name for f in dataclasses.fields(ServerConfig) if f.name != "name"]
    clis = [f.name for f in dataclasses.fields(CLIAlternative) if f.name != "name"]
    return servers, clis


def _document(
    kind: str, base: str, field_name: str, value: str, name: str = "zzt"
) -> str:
    lines = [
        line for line in base.split("\n    ") if not line.startswith(field_name + ":")
    ]
    body = "\n    ".join([*lines, f"{field_name}: {value}"])
    section = "servers" if kind == "server" else "cli_alternatives"
    return f"{section}:\n  {name}:\n    {body}\n"


def _describe(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def outcome(
    kind: str,
    field_name: str,
    value: str,
    base: str,
    home_root: Path,
    name: str = "zzt",
) -> str:
    """One entry's outcome: a ``kind`` (``server``/``cli``) entry called ``name``
    on ``base`` (``local``/``url``/``cli``) with ``field_name`` set to the YAML
    text ``value``, as a user overlay.

    ``loaded <repr of the kept value>``, or ``skipped [<who>] <reason>``: the
    skip WARNING's own text, where ``<who>`` is ``name not shown`` or
    ``shipped name shown`` and ``<reason>`` is what follows the overlay path
    (the field it names and the error kind)."""
    from pmcp.manifest.loader import clear_manifest_cache, load_manifest

    base_text = CLI_BASE if kind == "cli" else SERVER_BASES[base]
    home = Path(tempfile.mkdtemp(dir=home_root))
    (home / ".pmcp").mkdir()
    overlay = home / ".pmcp" / "manifest.yaml"
    overlay.write_text(
        _document(kind, base_text, field_name, value, name), encoding="utf-8"
    )
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect(level=logging.WARNING)
    log = logging.getLogger("pmcp.manifest")
    saved_propagate = log.propagate
    log.addHandler(handler)
    log.propagate = False
    saved_home, saved_cwd = os.environ.get("HOME"), os.getcwd()
    try:
        os.environ["HOME"] = str(home)
        os.chdir(home)
        clear_manifest_cache()
        manifest = load_manifest()
        entries = manifest.servers if kind == "server" else manifest.cli_alternatives
        skips = [
            r.getMessage()
            for r in records
            if r.getMessage().startswith("Skipping invalid")
        ]
        entry = entries.get(name)
        if not skips:
            if entry is None:
                return "skipped [no skip warning]"
            return "loaded " + _describe(getattr(entry, field_name))
        if len(skips) != 1:
            return f"skipped [{len(skips)} skip warnings]"
        message = skips[0]
        who = (
            "name not shown" if "(name not shown)" in message else "shipped name shown"
        )
        return f"skipped [{who}] {message.split(f'{overlay}: ', 1)[-1]}"
    finally:
        log.removeHandler(handler)
        log.propagate = saved_propagate
        os.chdir(saved_cwd)
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home
        clear_manifest_cache()


def generate(home_root: Path) -> list[tuple[str, str, str, str, str]]:
    """Rows of (kind, field, shape, base, outcome) for every field x shape x base."""
    server_fields, cli_fields = _fields()
    plan = [("server", f, b) for f in server_fields for b in SERVER_BASES]
    plan += [("cli", f, "cli") for f in cli_fields]
    return [
        (
            kind,
            field_name,
            shape,
            base,
            outcome(kind, field_name, value, base, home_root),
        )
        for kind, field_name, base in plan
        for shape, value in SHAPES
    ]


def check_claims(claims: list[dict], home_root: Path, side: str) -> list[str]:
    """Every row of every claim against its expected ``side`` (``branch`` or
    ``main``) outcome; returns the mismatches."""
    wrong = []
    for claim in claims:
        expected = claim[side]
        for row in claim["rows"]:
            kind, field_name, value, base = row[:4]
            name = row[4] if len(row) > 4 else "zzt"
            got = outcome(kind, field_name, value, base, home_root, name)
            if not got.startswith(expected):
                wrong.append(
                    f"{claim['id']}: {kind}/{field_name}={value} ({base}) {got} != {expected}"
                )
    return wrong


def main() -> None:
    """``python overlay_field_table.py`` prints the table; ``--claims main``
    (or ``branch``) checks the documented claims in
    ``test_catalog_overlay_discovery.DOC_CLAIMS`` against that side."""
    import ast

    os.environ.pop("PMCP_MANIFEST_PATH", None)
    root = Path(tempfile.mkdtemp(dir=os.environ.get("TMPDIR")))
    if sys.argv[1:2] == ["--claims"]:
        module = Path(__file__).with_name("test_catalog_overlay_discovery.py")
        source = module.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Run only the claim definitions (they use no pmcp import), so this
        # works with PYTHONPATH at another tree's src.
        wanted = {"_ANY", "_NOT_A_STRING", "DOC_CLAIMS"}
        namespace: dict[str, Any] = {"Any": Any}
        for node in tree.body:
            target = (
                node.target
                if isinstance(node, (ast.AnnAssign, ast.AugAssign))
                else None
            )
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in wanted:
                exec(ast.get_source_segment(source, node), namespace)  # noqa: S102
        claims = namespace["DOC_CLAIMS"]
        wrong = check_claims(claims, root, sys.argv[2])
        rows = sum(len(c["rows"]) for c in claims)
        print(
            f"{len(claims)} claims, {rows} rows, {len(wrong)} mismatches on {sys.argv[2]}"
        )
        for line in wrong:
            print(line)
        return
    for row in generate(root):
        print("\t".join(row))


if __name__ == "__main__":
    sys.exit(main())
