#!/usr/bin/env python3
"""Three-way differential: the resolver vs the flag tables vs the REAL npm binary.

Consiliency/pmcp#195. The hand-written flag tables have been repaired five times
and each repair was found by someone running the real binary, not by reading the
code. This harness makes that the routine check: for every form below it records
a **three-way verdict** -- what `_npm_resolve.js` says, what
`_npm_package_arg_from_tables` says, and what package npm *actually fetches*.

    uv run python .consiliency/notes/differential_npm_corpus.py --against-binary

Pass condition (and it is a real condition, not "a verdict was recorded"):

  * **zero unsafe rows.** A row is unsafe unless the resolver either matches the
    binary or REFUSES. A recorded disagreement is a failure, not a result.
  * a row where the binary made **no fetch** or **crashed** requires a refusal:
    there is no package to name, so naming one is a guess.
  * every named form is present AND compared. Membership alone passed an earlier
    wording while a form was never actually run.

======================================================================
SAFETY -- read before changing anything here
======================================================================

A dead registry stops npm **fetching**. It does **not** stop npm **executing**:
`libnpmexec` runs a matching local or global binary and returns into execution
*before* any registry fetch. Running 79 real manifest package names against an
ordinary environment would therefore **launch installed server code**.

Every oracle invocation is hermetic:

  * an **empty temporary cwd**, so no `node_modules/.bin` is in scope;
  * a temporary **HOME**, so `~/.npmrc` cannot apply;
  * a temporary **prefix**, so the global `$PREFIX/etc/npmrc` and the global
    `bin` cannot apply -- this is the one that stops execution of an installed
    server;
  * a temporary **cache**;
  * a dead **registry** (`http://127.0.0.1:9`);
  * `start_new_session=True` and a **process-group kill** on timeout, so a child
    that spawns its own children cannot outlive the run.

The temp base must itself have **no npm local prefix** -- a `package.json` or
`node_modules` in any ancestor. `/tmp` frequently fails this (the development
host for #195 has `/tmp/package.json`), which would put `/tmp/node_modules/.bin`
back in scope and defeat the whole arrangement, so the base is checked and the
run aborts if no clean base exists.

**The dead-registry env goes to the BINARY ONLY.** The resolver's step-1 gate
refuses any `npm_config_*`, so feeding this env to `resolve()` would make the
child refuse every input and make the whole comparison vacuous.

The fuzz alphabet deliberately excludes `--call`/`-c`/`--shell`/`--script-shell`:
`npx --call='X'` can execute X locally with no fetch at all, so a fuzzer minting
random values for it would execute random strings. The one `--call` case in the
corpus is a fixed, named F6 regression case whose command never runs, because
the install fails against the dead registry first.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from pmcp.manifest import version_checker  # noqa: E402
from pmcp.manifest.npm_resolver import (  # noqa: E402
    _has_local_prefix,
    get_resolver,
)

DEAD_REGISTRY = "http://127.0.0.1:9"
FETCH_URL = re.compile(re.escape(DEAD_REGISTRY) + r"/(\S+?)(?:\s|'|\"|$)")


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    label: str
    command: str
    args: tuple[str, ...]
    # Why the case is in the corpus. Printed with the row, so a future reader
    # knows which defect a regression here would reopen.
    origin: str


def named_cases() -> list[Case]:
    """Every historical form and every gate, by name."""
    return [
        # -- F1: `remain[0]` is the spliced `exec`, not the package -----------
        Case("F1-plain-npx", "npx", ("-y", "probe-a"), "F1 exec-splice"),
        Case("F1-no-yes", "npx", ("probe-a",), "F1 exec-splice"),
        # -- #180: an unrecognised flag's VALUE became the package ------------
        Case("180-loglevel", "npm", ("exec", "--loglevel", "silly", "aaa"), "#180"),
        Case(
            "180-registry-npm",
            "npm",
            ("exec", "--registry", "https://r", "aaa"),
            "#180",
        ),
        Case(
            "180-registry-npx", "npx", ("-y", "--registry", "https://r", "aaa"), "#180"
        ),
        Case(
            "180-docker-shaped", "npm", ("exec", "--not-a-real-npm-flag", "aaa"), "#180"
        ),
        # -- #182: `--package`'s value IS the package --------------------------
        Case("182-package-eq", "npm", ("exec", "--package=pkg-a", "--", "bin"), "#182"),
        Case(
            "182-package-spaced",
            "npm",
            ("exec", "--package", "pkg-a", "--", "bin"),
            "#182",
        ),
        Case(
            "182-package-twice",
            "npm",
            ("exec", "--package", "a", "--package", "b", "--", "bin"),
            "#182",
        ),
        Case(
            "182-package-twice-same",
            "npm",
            ("exec", "--package", "a", "--package", "a", "--", "bin"),
            "#182",
        ),
        # -- #183: a subcommand operand is not a registry package -------------
        Case("183-run", "npm", ("run", "mcp"), "#183"),
        Case("183-start", "npm", ("start",), "#183"),
        Case("183-test", "npm", ("test",), "#183"),
        Case("183-create", "npm", ("create", "foo"), "#183"),
        Case("183-typo", "npm", ("rum", "mcp"), "#183"),
        Case("183-bare", "npm", ("-y", "server-pkg"), "#183"),
        # #183 reopened by the `--package` rule: the subcommand allowlist used
        # to be skipped entirely when `--package` was present, so every one of
        # these minted `pkg-a` for a command line that runs a package.json
        # SCRIPT (board review on the diff).
        Case(
            "183-run-package",
            "npm",
            ("run", "--package=pkg-a", "--", "bin"),
            "#183+--package",
        ),
        Case(
            "183-start-package", "npm", ("start", "--package=pkg-a"), "#183+--package"
        ),
        Case("183-test-package", "npm", ("test", "--package=pkg-a"), "#183+--package"),
        Case(
            "183-create-package",
            "npm",
            ("create", "--package=pkg-a", "--", "bin"),
            "#183+--package",
        ),
        Case(
            "183-dlx-package",
            "npm",
            ("dlx", "--package=pkg-a", "--", "bin"),
            "#183+--package",
        ),
        Case(
            "183-typo-package",
            "npm",
            ("rum", "--package=pkg-a", "--", "bin"),
            "#183+--package",
        ),
        Case("183-nosub-package", "npm", ("--package=pkg-a",), "#183+--package"),
        # ...while npx has no subcommand, so this one must still RESOLVE.
        Case(
            "req-npx-package-only",
            "npx",
            ("--package=probe-req", "--", "bin"),
            "required",
        ),
        # -- #192 / #194 / 2.5.2: boolean arity and baked-value shorthands ----
        Case(
            "192-global-true",
            "npm",
            ("exec", "--global", "true", "aaa"),
            "#192 boolean literal",
        ),
        Case(
            "194-silent-baked",
            "npm",
            ("exec", "--silent=true", "aaa"),
            "#194 baked value",
        ),
        Case(
            "194-silent-spaced", "npx", ("--silent", "true", "aaa"), "#194 baked value"
        ),
        Case("252-yes-null", "npm", ("exec", "--yes", "null", "aaa"), "2.5.2 nullable"),
        Case("252-yes-null-npx", "npx", ("--yes", "null", "aaa"), "2.5.2 nullable"),
        Case(
            "252-global-null",
            "npm",
            ("exec", "--global", "null", "aaa"),
            "2.5.2 nullable",
        ),
        # -- #195: the tables' own wrong answers the resolver beats -----------
        Case("195-pack", "npx", ("--pack", "zz", "bin"), "#195 unknown flag"),
        Case(
            "195-yes-eq-maybe",
            "npx",
            ("--yes=maybe", "tok", "bin2"),
            "#195 invalid value",
        ),
        Case(
            "195-frobnicate",
            "npx",
            ("--frobnicate", "valpkg", "realbin"),
            "#195 unknown flag",
        ),
        # -- F2: an invalid spec makes npm fetch `undefined`, which EXISTS ----
        Case("F2-flag-thing", "npm", ("exec", "--", "--flag-thing"), "F2 undefined"),
        Case("F2-empty-package", "npx", ("--package=",), "F2 undefined"),
        # -- F6: `--call` must not be outranked by `--package` ----------------
        Case(
            "F6-package-and-call",
            "npx",
            ("--package=pkg-a", "--call=echo hi"),
            "F6 --call",
        ),
        # -- F7: `dlx` is pnpm/yarn spelling; npm has no such subcommand ------
        Case("F7-dlx", "npm", ("dlx", "x"), "F7 dlx"),
        # -- step-1 flag gates -------------------------------------------------
        Case(
            "gate-userconfig", "npx", ("--userconfig", "/dev/null", "probe-a"), "gate"
        ),
        Case("gate-prefix", "npx", ("--prefix", "/dev/null", "probe-a"), "gate"),
        Case("gate-cache", "npx", ("--cache", "/dev/null", "probe-a"), "gate"),
        Case("gate-workspace", "npm", ("exec", "-w", "ws", "probe-a"), "gate"),
        # -- step-3 npa validation --------------------------------------------
        Case("npa-alias", "npx", ("-y", "myalias-zz@npm:left-pad"), "npa alias"),
        Case("npa-git", "npx", ("-y", "github:owner/repo"), "npa git"),
        Case("npa-file", "npx", ("-y", "./local-thing"), "npa directory"),
        # -- no operand at all -------------------------------------------------
        Case("empty-dashdash", "npx", ("--",), "no operand"),
        Case("empty-yes", "npx", ("-y",), "no operand"),
        Case("empty-argv", "npx", (), "no operand"),
        # -- scoped names -------------------------------------------------------
        Case("scoped-plain", "npx", ("-y", "@scope-zz/pkg-a"), "scoped"),
        Case("scoped-versioned", "npx", ("-y", "@scope-zz/pkg-a@1.2.3"), "scoped"),
        Case("versioned", "npx", ("-y", "pkg-a@1.2.3"), "version spec"),
        # -- the required-to-resolve list (AC1) --------------------------------
        Case("req-npx-y", "npx", ("-y", "probe-req"), "required"),
        Case("req-npx-bare", "npx", ("probe-req",), "required"),
        Case("req-npm-exec", "npm", ("exec", "probe-req"), "required"),
        Case(
            "req-npm-exec-package",
            "npm",
            ("exec", "--package=probe-req", "--", "bin"),
            "required",
        ),
        Case("req-versioned", "npx", ("-y", "probe-req@1.2.3"), "required"),
        Case("req-scoped", "npx", ("-y", "@scope-zz/probe-req"), "required"),
        # -- the one manifest entry with a flag AFTER the package --------------
        Case(
            "manifest-flag-after-package",
            "npx",
            ("-y", "@supabase/mcp-server-supabase", "--project-ref", "X"),
            "pre-scan `--` insertion",
        ),
    ]


def manifest_cases() -> list[Case]:
    """Every npm-family server in the shipped manifest."""
    import yaml

    data = yaml.safe_load((REPO / "src/pmcp/manifest/manifest.yaml").read_text())
    servers = data["servers"] if isinstance(data, dict) and "servers" in data else data
    items = servers.values() if isinstance(servers, dict) else servers
    cases = []
    for name, entry in (
        servers.items() if isinstance(servers, dict) else enumerate(items)
    ):
        if entry.get("command") in ("npx", "npm"):
            cases.append(
                Case(
                    f"manifest:{name}",
                    entry["command"],
                    tuple(entry.get("args", [])),
                    "manifest",
                )
            )
    return cases


# Flags safe to mint values for. `call`/`c`/`shell`/`script-shell` are excluded
# on purpose -- see the module docstring.
_FUZZ_FLAGS = [
    "--yes",
    "-y",
    "--no-install",
    "--global",
    "-g",
    "--offline",
    "--silent",
    "-s",
    "-q",
    "--loglevel",
    "--registry",
    "--userconfig",
    "--prefix",
    "--cache",
    "--package",
    "-p",
    "--workspace",
    "-w",
    "--color",
    "--local",
    "--frobnicate",
    "--pack",
    "--not-a-flag",
    "--",
    "--zzz",
    "--no-global",
]
_FUZZ_VALUES = ["true", "false", "null", "silly", "always", "maybe", "ws", "1"]


def fuzz_cases(count: int, seed: int) -> list[Case]:
    """Random argv over a SAFE alphabet.

    The seed is randomised per run and printed. A fixed seed let an
    implementation with no parser in it at all -- a dictionary keyed on
    `tuple(args)` plus one substring check -- pass the acceptance criteria
    jointly.
    """
    rng = random.Random(seed)
    cases = []
    for i in range(count):
        args: list[str] = []
        for _ in range(rng.randint(1, 5)):
            roll = rng.random()
            if roll < 0.45:
                args.append(rng.choice(_FUZZ_FLAGS))
            elif roll < 0.60:
                args.append(f"{rng.choice(_FUZZ_FLAGS)}={rng.choice(_FUZZ_VALUES)}")
            elif roll < 0.75:
                args.append(rng.choice(_FUZZ_VALUES))
            else:
                args.append(f"zz-{rng.randrange(16**6):06x}")
        command = rng.choice(["npx", "npm"])
        if command == "npm":
            args.insert(0, rng.choice(["exec", "x", "install", "run", "dlx"]))
        cases.append(Case(f"fuzz-{i:03d}", command, tuple(args), f"fuzz seed={seed}"))
    return cases


# ---------------------------------------------------------------------------
# The three oracles
# ---------------------------------------------------------------------------

BinaryVerdict = tuple[Literal["fetch", "no-fetch", "crash", "timeout"], str | None]


def _clean_base() -> Path:
    """A directory whose ancestors set NO npm local prefix."""
    candidates = [Path("/mnt/workspace"), Path(tempfile.gettempdir()), Path.home()]
    for candidate in candidates:
        if candidate.is_dir() and _has_local_prefix(str(candidate)) is None:
            return candidate
    raise SystemExit(
        "REFUSING TO RUN: every candidate temp base sits inside an npm local "
        "prefix (a package.json or node_modules in an ancestor). npm would then "
        "search that prefix's node_modules/.bin before the registry and could "
        "EXECUTE an installed package. Tried: " + ", ".join(str(c) for c in candidates)
    )


def ask_the_binary(case: Case, base: Path, timeout: float = 90.0) -> BinaryVerdict:
    """What package does the real npm actually fetch? Hermetically."""
    sandbox = Path(tempfile.mkdtemp(prefix="npmdiff-", dir=str(base)))
    cwd = sandbox / "cwd"
    home = sandbox / "home"
    prefix = sandbox / "prefix"
    cache = sandbox / "cache"
    for directory in (cwd, home, prefix, cache):
        directory.mkdir(parents=True)

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "npm_config_registry": DEAD_REGISTRY,
        "npm_config_prefix": str(prefix),
        "npm_config_cache": str(cache),
        "npm_config_userconfig": str(home / ".npmrc"),
        "npm_config_globalconfig": str(prefix / "etc" / "npmrc"),
        # `fetch_retries=0` alone. Do NOT also shrink
        # `fetch_retry_maxtimeout`: npm validates `minTimeout <= maxTimeout`
        # and aborts with "minTimeout is greater than maxTimeout" BEFORE any
        # fetch, which silently turns every row into a no-fetch and makes the
        # whole comparison vacuous.
        "npm_config_fetch_retries": "0",
        "npm_config_update_notifier": "false",
        "npm_config_audit": "false",
        "npm_config_fund": "false",
        "npm_config_progress": "false",
    }
    proc = None
    try:
        proc = subprocess.Popen(
            [case.command, *case.args],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.communicate()
            return ("timeout", None)
        blob = f"{out}\n{err}"
        match = FETCH_URL.search(blob)
        if match:
            raw = match.group(1).rstrip(".,;:")
            return ("fetch", urllib.parse.unquote(raw))
        if proc.returncode and proc.returncode < 0:
            return ("crash", f"signal {-proc.returncode}")
        return ("no-fetch", f"exit {proc.returncode}")
    except OSError as exc:
        return ("crash", str(exc))
    finally:
        if proc is not None and proc.poll() is None:  # pragma: no cover
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                pass
        shutil.rmtree(sandbox, ignore_errors=True)


def ask_the_resolver(case: Case) -> tuple[str, str | None]:
    """`(status, npa name)`. The dead-registry env is NOT passed here."""
    result = get_resolver().resolve(case.command, list(case.args), {}, None)
    if not result.is_identity:
        return (result.status, result.reason)
    # Compare on the npa NAME: the binary's fetch URL carries the name, while
    # the resolver returns the spec raw with its tag intact by design.
    spec = result.spec or ""
    name = version_checker._strip_npm_tag(spec)
    return ("IDENTITY", name)


def ask_the_tables(case: Case) -> str | None:
    raw = version_checker._npm_package_arg_from_tables(list(case.args), case.command)
    return None if raw is None else version_checker._strip_npm_tag(raw)


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


@dataclass
class Row:
    case: Case
    resolver: tuple[str, str | None]
    tables: str | None
    binary: BinaryVerdict = ("no-fetch", "not run")
    verdict: str = ""
    unsafe: bool = False
    notes: list[str] = field(default_factory=list)


def judge(row: Row) -> Row:
    status, value = row.resolver
    kind, detail = row.binary

    if status != "IDENTITY":
        row.verdict = "REFUSED"
        # A refusal is always safe. Record whether it COST anything, so a
        # implementation that refuses everything is visible rather than green.
        if kind == "fetch":
            row.notes.append(f"cost: binary would fetch {detail!r}")
        return row

    if kind != "fetch":
        # No package was fetched, so there is no package to name. Naming one is
        # a guess, and this is the direction that has produced every defect.
        row.verdict = f"UNSAFE (resolver named {value!r}; binary {kind}: {detail})"
        row.unsafe = True
        return row

    if value == detail:
        row.verdict = "MATCH"
        if row.tables != value:
            row.notes.append(f"tables disagree: {row.tables!r} (resolver beats tables)")
        return row

    row.verdict = f"UNSAFE (resolver {value!r} != binary {detail!r})"
    row.unsafe = True
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--against-binary",
        action="store_true",
        help="run the real npm binary as the third oracle (hermetically)",
    )
    parser.add_argument("--fuzz", type=int, default=60)
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="fuzz seed; RANDOM per run by default, and always printed",
    )
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--json", type=Path, default=None)
    options = parser.parse_args()

    seed = (
        options.seed
        if options.seed is not None
        else int.from_bytes(os.urandom(4), "big")
    )
    cases = named_cases() + manifest_cases() + fuzz_cases(options.fuzz, seed)

    probe = get_resolver().resolve("npx", ["-y", "left-pad"], {}, None)
    print(f"resolver probe: {probe.status} {probe.spec or probe.reason}")
    if not probe.is_identity:
        print(
            "\nThe resolver is not operational here, so there is nothing to "
            "compare. On a node-less host that is expected and the corpus is "
            "not meaningful; on a node-ful host it means the drift tripwire "
            "fired.",
            file=sys.stderr,
        )
        return 2

    print(f"fuzz seed: {seed}  (re-run with --seed {seed})")
    print(
        f"cases: {len(cases)}  (named={len(named_cases())} "
        f"manifest={len(manifest_cases())} fuzz={options.fuzz})"
    )

    rows = [Row(case, ask_the_resolver(case), ask_the_tables(case)) for case in cases]

    if options.against_binary:
        base = _clean_base()
        print(f"hermetic base: {base}  registry: {DEAD_REGISTRY}")
        with ThreadPoolExecutor(max_workers=options.jobs) as pool:
            verdicts = list(pool.map(lambda row: ask_the_binary(row.case, base), rows))
        for row, verdict in zip(rows, verdicts):
            row.binary = verdict
    else:
        print("(binary oracle skipped -- pass --against-binary)")

    rows = [judge(row) for row in rows]

    print()
    print(f"{'case':38} {'resolver':26} {'tables':22} binary")
    print("-" * 118)
    for row in rows:
        status, value = row.resolver
        resolver_cell = value if status == "IDENTITY" else status
        kind, detail = row.binary
        binary_cell = detail if kind == "fetch" else f"<{kind}: {detail}>"
        marker = "!!" if row.unsafe else "  "
        print(
            f"{marker}{row.case.label:36} {str(resolver_cell)[:25]:26} "
            f"{str(row.tables)[:21]:22} {str(binary_cell)[:40]}"
        )
        for note in row.notes:
            print(f"      - {note}")

    unsafe = [row for row in rows if row.unsafe]
    identities = [row for row in rows if row.resolver[0] == "IDENTITY"]
    matches = [row for row in rows if row.verdict == "MATCH"]
    beats = [row for row in matches if row.tables != row.resolver[1]]
    manifest_rows = [row for row in rows if row.case.label.startswith("manifest:")]
    manifest_identities = [
        row for row in manifest_rows if row.resolver[0] == "IDENTITY"
    ]
    manifest_match = [row for row in manifest_rows if row.verdict == "MATCH"]

    print()
    print(f"rows                     : {len(rows)}")
    print(f"resolver IDENTITY        : {len(identities)}")
    print(f"resolver == binary       : {len(matches)}")
    print(f"resolver beats the tables: {len(beats)}")
    print(f"manifest npm servers     : {len(manifest_rows)}")
    print(f"  ...resolving           : {len(manifest_identities)}")
    print(f"  ...matching the binary : {len(manifest_match)}")
    required_rows = manifest_rows + [
        row for row in rows if row.case.label.startswith("req-")
    ]
    required_ok = [row for row in required_rows if row.verdict == "MATCH"]
    print(f"AC1 required-to-resolve  : {len(required_rows)}")
    print(f"  ...IDENTITY & matching : {len(required_ok)}")
    print(f"UNSAFE rows              : {len(unsafe)}")

    if options.json:
        options.json.write_text(
            json.dumps(
                [
                    {
                        "label": row.case.label,
                        "command": row.case.command,
                        "args": list(row.case.args),
                        "origin": row.case.origin,
                        "resolver": list(row.resolver),
                        "tables": row.tables,
                        "binary": list(row.binary),
                        "verdict": row.verdict,
                        "unsafe": row.unsafe,
                    }
                    for row in rows
                ],
                indent=2,
            )
        )

    if unsafe:
        print("\nFAIL: unsafe rows above (marked !!).")
        return 1

    # AC1 has a POSITIVE half, and "zero unsafe rows" does not enforce it: a
    # refusal is always judged safe, so a resolver that refused EVERY input
    # scored zero unsafe rows and passed -- which is the one outcome AC1 names
    # as unacceptable ("all 79 manifest npm servers resolve ... and every form
    # on this required-to-resolve list also resolves (not refuses), matching the
    # binary"). Enforced here rather than left to the eye (board review on the
    # diff).
    if options.against_binary:
        required = manifest_rows + [
            row for row in rows if row.case.label.startswith("req-")
        ]
        if not required:
            print("\nFAIL: the required-to-resolve set is empty.")
            return 1
        not_resolving = [row for row in required if row.resolver[0] != "IDENTITY"]
        not_matching = [
            row
            for row in required
            if row.resolver[0] == "IDENTITY" and row.verdict != "MATCH"
        ]
        if not_resolving or not_matching:
            print("\nFAIL: rows that AC1 requires to resolve did not.")
            for row in not_resolving:
                print(f"  refused : {row.case.label}: {row.resolver[1]}")
            for row in not_matching:
                print(f"  mismatch: {row.case.label}: {row.verdict}")
            return 1
        if not beats:
            # Without a single case where the tables are WRONG and the resolver
            # is right, the 79 plain manifest servers could go green on the
            # tables alone -- proving nothing about the resolver.
            print("\nFAIL: no case where the resolver beats the tables.")
            return 1
        print(
            f"\nPASS: zero unsafe rows; {len(required)} required rows all "
            f"IDENTITY and matching the binary ({len(manifest_rows)} manifest + "
            f"{len(required) - len(manifest_rows)} required-to-resolve); "
            f"{len(beats)} rows where the resolver beats the tables."
        )
        return 0
    print("\nPASS: zero unsafe rows (binary oracle not run).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
