"""Record `main`'s oracle over the redaction differential corpora
(Consiliency/pmcp#234). Run it from this checkout against a MAIN tree:

  PYTHONPATH=<main checkout>/src python tests/fixtures/regen_redaction_main_oracle.py \
      <main checkout>/src tests/fixtures/redaction_main_oracle.b64

then copy the output to `.consiliency/plans/detailed-234-redactor-main-oracle.b64`
(the two stay `cmp`-identical). Not collected by pytest.

Offline and deterministic (seeded corpora, gzip mtime=0, sorted keys, base64
wrapped at 76). Keys:
  grammar[i]     tests/_redaction_grammar.py's observe() code for row i of
                 corpus(2) (tier 1 is its prefix);
  grammar_fingerprint  {"1": ..., "2": ...}: fingerprint() of each tier;
  dict[i]        pieces main's process_output removed from the serialised
                 result of _dict_corpus() row i;
  dict_types[i]  / fuzz_types[i]: type name of process_output(obj)['result']
                 for the dict and JSON-fuzz corpora.
"""

import ast
import base64
import gzip
import importlib.util
import io
import json
import multiprocessing
import random
import re
import string
import sys
import uuid
from pathlib import Path

import pmcp
from pmcp.auth import sanitize_auth_diagnostic
from pmcp.policy.policy import PolicyManager

MAIN_SRC = str(Path(sys.argv[1]).absolute())
assert pmcp.__file__.startswith(MAIN_SRC), (pmcp.__file__, MAIN_SRC)
TESTS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "_redaction_grammar", TESTS / "_redaction_grammar.py"
)
G = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(G)

tree = ast.parse((TESTS / "test_redaction.py").read_text())
FUNCS = {"_dict_corpus", "_json_fuzz_corpus"}


def keep(n):
    if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        t = n.targets[0] if isinstance(n, ast.Assign) else n.target
        return isinstance(t, ast.Name) and t.id.startswith("_DIFF_")
    return isinstance(n, ast.FunctionDef) and n.name in FUNCS


ns = {"random": random, "json": json, "re": re, "string": string, "uuid": uuid}
body = [n for n in tree.body if keep(n)]
assert {n.name for n in body if isinstance(n, ast.FunctionDef)} == FUNCS
exec(compile(ast.Module(body=body, type_ignores=[]), "c", "exec"), ns)
TOK = ns["_DIFF_TOKEN"]
pm = PolicyManager()
ROWS = G.corpus(2)


def _po(obj):
    return pm.process_output(obj, redact=True, max_bytes=G.BIG)["result"]


def observe(i):
    return G.observe(
        ROWS[i],
        lambda t: sanitize_auth_diagnostic(t, max_length=None),
        pm.redact_secrets,
        _po,
    )


def rem(a, b):
    return sorted(set(TOK.findall(a)) - set(TOK.findall(b)))


if __name__ == "__main__":
    with multiprocessing.get_context("fork").Pool(20) as pool:
        grammar = pool.map(observe, range(len(ROWS)), chunksize=500)
    out = {
        "grammar": grammar,
        "grammar_fingerprint": {
            "1": G.fingerprint(G.corpus(1)),
            "2": G.fingerprint(ROWS),
        },
        "dict": [],
        "dict_types": [],
        "fuzz_types": [],
    }
    for obj, *_ in ns["_dict_corpus"]():
        r = pm.process_output(obj, redact=True)["result"]
        out["dict"].append(
            rem(
                json.dumps(obj, indent=2),
                r if isinstance(r, str) else json.dumps(r, indent=2),
            )
        )
        out["dict_types"].append(type(r).__name__)
    for obj in ns["_json_fuzz_corpus"]():
        out["fuzz_types"].append(
            type(pm.process_output(obj, redact=True)["result"]).__name__
        )
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:
        g.write(json.dumps(out, sort_keys=True, separators=(",", ":")).encode())
    Path(sys.argv[2]).write_text(base64.encodebytes(buf.getvalue()).decode())
    print(
        "recorded from",
        pmcp.__file__,
        {k: len(v) for k, v in out.items()},
        "tier1",
        len(G.corpus(1)),
        "dict_types",
        {t: out["dict_types"].count(t) for t in sorted(set(out["dict_types"]))},
        "fuzz_types",
        {t: out["fuzz_types"].count(t) for t in sorted(set(out["fuzz_types"]))},
    )
