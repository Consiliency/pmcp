"""Derive npm's flag-arity tables from npm's OWN config schema, and verify them.

`_npm_package_arg` must know, for every npm flag, whether the token after it is
that flag's value or the package name. Getting one entry wrong in the
"consumes nothing" direction is fail-OPEN: the flag's value becomes the package
identity, and two different servers collapse into one (Consiliency/pmcp#180).
npm has 181 flags and 40 shorthands, which is far past what anyone can recall
correctly -- #182 removed ten memory-drafted entries across four ecosystems for
exactly that reason. So the table is generated, never hand-listed.

THE SOURCE IS `@npmcli/config/lib/definitions/index.js`, NOT `npm config list`.
-----------------------------------------------------------------------------
`npm config list --json` is disqualified as a source, and this is not a style
preference. It emits the *active merged configuration*, not the definition set:

  * it omits private entries (`_auth`, `password`),
  * it adds host-specific ones (`npm-version`, a user-scoped registry),
  * `NPM_CONFIG_COLOR=always` flips `color` from a boolean to a string,
  * it omits all 40 shorthands wholesale, so `--silent` looks like an unknown
    flag rather than an alias of `--loglevel silent`.

The same generator would therefore classify differently on different hosts. The
definitions module, by contrast, declares each flag's `type` -- the real arity
declaration, independent of environment and of any default value.

WHAT `type` MEANS, AND THE TWO MEMBERS THAT ARE NOT VALUE TYPES
--------------------------------------------------------------
`type` is a SET, e.g. `color` is `['always', Boolean]`. Two members are
multiplicity/absence markers rather than value types and are dropped before
classifying:

  `null`   "unset". Dropping it is load-bearing: `yes`, `optional`,
           `production`, `workspaces` and `expect-results` are all
           `null|Boolean`, so without the drop they read as conditional and
           `--yes`/`-y` -- a real MCP launch form -- would be refused.
  `Array`  "may repeat". Measured inert on npm 11.19.0 (no definition changes
           class either way, and none is `Boolean|Array`); the generator
           reports it if that ever stops being true.

Classification, after the drop:

  Boolean alone         -> boolean-arity: consumes nothing ... except a literal
                           `true`/`false`, which npm's parser DOES take as the
                           flag's value. Verified below for all 76.
  no Boolean member     -> value: consumes the next token. This is where
                           `proxy` (`null|false|{url}`) belongs -- it has no
                           Boolean member and always consumes, including
                           `--proxy a` where it swallows the package.
  Boolean AND non-Bool  -> CONDITIONAL: arity depends on the next token's
                           content, so no single class is right. Left UNLISTED,
                           which makes the scanner refuse. Fail closed.

SHORTHANDS EXPAND; ARITY COMES FROM THE EXPANSION'S LENGTH
----------------------------------------------------------
  length >= 2  a value is baked in (`silent` -> ['--loglevel','silent'])
               => SKIP: consumes nothing, ever. NOT the same as boolean --
               a real boolean consumes a literal `true`/`false` and one of
               these does not, so collapsing the two is fail-OPEN:
                 --silent true TAIL -> remain ["true","TAIL"]
                 --global true TAIL -> remain ["TAIL"]
  length == 1  a pure rename (`reg` -> ['--registry'], `y` -> ['--yes'])
               => the target's arity.

This subsumes the hand-coded `-y` special case `_npm_package_arg` used to
carry, and makes `npm --silent exec pkg` -> `pkg` hold by construction.

Deliberately NOT a pytest: npm's schema is version-specific and CI has no npm.
Behaviour is pinned by the pure unit tests in tests/test_version_checker.py;
this script is what keeps those pins honest against a real npm.

Usage:
    python3 .consiliency/notes/derive_npm_flags.py            # emit the tables
    python3 .consiliency/notes/derive_npm_flags.py --verify   # check committed
"""

import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

# Multiplicity/absence markers, not value types. See the module docstring --
# dropping `null` is what keeps `--yes` usable.
_NOT_A_VALUE_TYPE = frozenset({"null", "Array"})

BOOLEAN, VALUE, CONDITIONAL, UNINTERPRETABLE, SKIP = (
    "boolean",
    "value",
    "conditional",
    "uninterpretable",
    "skip",
)

# `--package` is handled as a KNOWN-POSITIVE by the scanner (its value IS the
# package), so it is deliberately absent from both emitted tables; the scanner
# consults its positive table first.
_POSITIVE = frozenset({"package"})

# Read npm's definitions module and dump it as JSON, labelling each `type`
# member. Also probes npm's real parser (`nopt`) for every flag, so the
# declared classification can be checked against observed behaviour rather
# than trusted -- the type strings alone are what falsified an earlier design.
_NODE = r"""
const path = require('path');
const root = process.argv[1];
const defsPath = path.join(
  root, 'node_modules', '@npmcli', 'config', 'lib', 'definitions', 'index.js');
const m = require(defsPath);
const nopt = require(path.join(root, 'node_modules', 'nopt'));

const label = (x) => {
  if (x === null) return 'null';
  if (x === false) return 'false';
  if (x === true) return 'true';
  if (typeof x === 'string' || typeof x === 'number') return '<literal>';
  if (typeof x === 'function') return x.name || '<anon-fn>';
  if (typeof x === 'object') return x && x.Url ? 'url' : 'path';
  return '<unknown:' + typeof x + '>';
};

const types = {};
for (const [k, v] of Object.entries(m.definitions)) types[k] = v.type;
const remain = (argv) => nopt(types, m.shorthands, argv, 0).argv.remain;
// Does `--flag PROBE` swallow PROBE? Probe with an arbitrary token, with the
// two boolean literals, and -- when the type declares one -- with a real enum
// member, since that is exactly where a conditional flag reveals itself.
const consumes = (flag, probe) => !remain(['--' + flag, probe, 'TAIL']).includes(probe);

const definitions = {};
for (const [k, v] of Object.entries(m.definitions)) {
  const t = Array.isArray(v.type) ? v.type : [v.type];
  const lit = t.find((x) => typeof x === 'string' || typeof x === 'number');
  const probes = ['zzarbitrary', 'true', 'false'];
  if (lit !== undefined) probes.push(String(lit));
  definitions[k] = {
    type: t.map(label),
    short: v.short === undefined ? [] : [].concat(v.short),
    probes: Object.fromEntries(probes.map((p) => [p, consumes(k, p)])),
  };
}
console.log(JSON.stringify({
  npm_version: require(path.join(root, 'package.json')).version,
  definitions,
  shorthands: m.shorthands,
}));
"""


def npm_root() -> str:
    """The global npm install directory, i.e. where `@npmcli/config` lives."""
    out = subprocess.run(
        ["npm", "root", "-g"], capture_output=True, text=True, check=True
    )
    return str(pathlib.Path(out.stdout.strip()) / "npm")


def read_schema() -> dict:
    out = subprocess.run(
        ["node", "-e", _NODE, npm_root()],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def classify(type_labels: list[str]) -> str:
    """npm's declared `type` -> one of BOOLEAN / VALUE / CONDITIONAL."""
    members = [t for t in type_labels if t not in _NOT_A_VALUE_TYPE]
    has_boolean = "Boolean" in members
    others = [t for t in members if t != "Boolean"]
    if has_boolean and not others:
        return BOOLEAN
    if not has_boolean and others:
        return VALUE
    if has_boolean and others:
        return CONDITIONAL
    return UNINTERPRETABLE


def expected_arity(cls: str, probe_results: dict[str, bool]) -> bool | None:
    """What the declared class PLUS the true/false rule predicts, per probe.

    Returns None for CONDITIONAL (no prediction is made -- it goes unlisted).
    """
    if cls == BOOLEAN:
        return None  # handled per-probe by the caller
    if cls == VALUE:
        return True
    return None


def build(schema: dict) -> tuple[dict[str, str], list[str], list[str], list[str]]:
    """Return (flag -> class, conditional names, union-with-Boolean, unreadable)."""
    defs = schema["definitions"]
    classes: dict[str, str] = {}
    conditional: list[str] = []
    union_with_boolean: list[str] = []
    unreadable: list[str] = []

    def record(flag: str, cls: str) -> None:
        """Write one entry, refusing to silently reclassify an existing one.

        Definitions, `short` aliases and shorthands are written in that order,
        so a plain `classes[flag] = cls` would let a later source overwrite an
        earlier one. That is the fail-OPEN drift direction -- it yields a
        WRONG entry rather than a missing one -- and it would pass `--verify`
        silently because the generator and the table would agree with each
        other while both disagreeing with npm. Empty on 11.19.0; this exists
        so a future npm cannot make it non-empty quietly.
        """
        previous = classes.get(flag)
        if previous is not None and previous != cls:
            raise SystemExit(
                f"conflicting classification for {flag}: {previous} vs {cls}"
            )
        classes[flag] = cls

    name_class: dict[str, str] = {}
    for name, info in defs.items():
        cls = classify(info["type"])
        name_class[name] = cls
        if cls == UNINTERPRETABLE:
            unreadable.append(f"{name}: type={info['type']}")
            continue
        if cls == CONDITIONAL:
            # Print the PROBES too, not just the type string. `browser` is
            # declared `null|Boolean|String` and so classifies conditional,
            # but nopt shows it consuming every probe -- i.e. it behaves as a
            # value flag, and listing it as one would cost nothing in safety.
            # It is left unlisted anyway, because refusing is never the unsafe
            # direction and no MCP launch form uses `--browser`. A reader
            # re-deciding that needs the evidence in front of them.
            probes = " ".join(f"{p}={c}" for p, c in info["probes"].items())
            conditional.append(
                f"{name}: type={'|'.join(info['type'])}  nopt-consumes[{probes}]"
            )
        if cls == VALUE and "Boolean" in info["type"]:
            # Cannot happen under the current rule, but if npm ever declares a
            # value type that also carries Boolean, that is the class that
            # falsified the first design and it must not pass silently.
            union_with_boolean.append(f"{name}: type={'|'.join(info['type'])}")
        if name in _POSITIVE:
            continue
        if cls in (BOOLEAN, VALUE):
            record(f"--{name}", cls)
            for short in info["short"]:
                record(f"-{short}", cls)
                record(f"--{short}", cls)
        if cls == BOOLEAN:
            # `--no-X` negates a boolean and, like the boolean itself,
            # consumes only a literal true/false. Verified against nopt.
            record(f"--no-{name}", cls)

    for key, expansion in schema["shorthands"].items():
        if len(expansion) >= 2:
            # A value is baked into the expansion, so by the time npm parses
            # argv there is no flag left awaiting one. Crucially this is NOT
            # the same as BOOLEAN: a real boolean consumes a literal
            # `true`/`false`, and a baked-value shorthand does not. Verified:
            #   --silent true TAIL -> remain ["true","TAIL"]   (no consume)
            #   --global true TAIL -> remain ["TAIL"]          (consumes)
            # Collapsing the two let `npx --silent true <arg>` read <arg> as
            # the package when npm's package is `true` -- a wrong identity,
            # and `--silent true X` / `--silent false X` collapse onto X.
            cls = SKIP  # a value is baked into the expansion
        else:
            target = expansion[0].lstrip("-")
            if target.startswith("no-"):
                # `--no-global`, `--no-yes`: boolean-arity iff the base is a
                # boolean. Otherwise unlisted -- `--no-color` refuses, safely.
                cls = BOOLEAN if name_class.get(target[3:]) == BOOLEAN else CONDITIONAL
            else:
                cls = name_class.get(target, CONDITIONAL)
        if cls in (BOOLEAN, VALUE, SKIP):
            record(f"-{key}", cls)
            record(f"--{key}", cls)

    return classes, conditional, union_with_boolean, unreadable


def check_against_nopt(schema: dict) -> list[str]:
    """Declared class + the true/false rule must reproduce npm's own parser.

    This is the check that makes "verified against npm's parser" true rather
    than asserted. A BOOLEAN flag is predicted to consume a probe iff the probe
    is exactly `true`/`false`; a VALUE flag is predicted to consume every
    probe; a CONDITIONAL flag makes no prediction because it is unlisted.
    """
    bad: list[str] = []
    for name, info in schema["definitions"].items():
        cls = classify(info["type"])
        for probe, observed in info["probes"].items():
            if cls == BOOLEAN:
                predicted = probe in ("true", "false")
            elif cls == VALUE:
                predicted = True
            else:
                continue
            if predicted != observed:
                bad.append(
                    f"--{name} {probe}: declared={cls} predicts "
                    f"consumes={predicted}, nopt says {observed} "
                    f"(type={'|'.join(info['type'])})"
                )
    return bad


def read_committed_tables(source: pathlib.Path) -> dict[str, set[str]]:
    """Parse the two tables out of version_checker.py with `ast`.

    Deliberately NOT `import pmcp.manifest.version_checker`: that pulls in
    aiohttp/packaging/semver, so the verifier would only run inside the
    project venv -- on a host that happens to have the npm being checked. A
    drift check should run wherever npm does.

    Reading them as literals also PINS a property worth pinning: these must be
    module-level constants, not something rebuilt per call. #182 found
    `_docker_image_arg` carrying a function-local table that was rebuilt on
    every call and invisible to its verifier; this raises rather than silently
    finding nothing if that shape comes back.
    """
    import ast

    tree = ast.parse(source.read_text())
    found: dict[str, set[str]] = {}
    for node in tree.body:  # module level ONLY -- not ast.walk
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        for name in names:
            if name not in (
                "_NPM_VALUE_FLAGS",
                "_NPM_BOOLEAN_FLAGS",
                "_NPM_SKIP_FLAGS",
            ):
                continue
            call = node.value
            if (
                not isinstance(call, ast.Call)
                or not isinstance(call.func, ast.Name)
                or call.func.id != "frozenset"
            ):
                raise SystemExit(f"{name} is not a literal frozenset(...)")
            found[name] = {
                ast.literal_eval(element)
                for element in call.args[0].elts  # type: ignore[attr-defined]
            }
    missing = {
        "_NPM_VALUE_FLAGS",
        "_NPM_BOOLEAN_FLAGS",
        "_NPM_SKIP_FLAGS",
    } - found.keys()
    if missing:
        raise SystemExit(f"not module-level literals in {source}: {sorted(missing)}")
    return found


def emit(classes: dict[str, str]) -> str:
    def block(cls: str) -> str:
        entries = sorted(f for f, c in classes.items() if c == cls)
        body = "\n".join(f'        "{e}",' for e in entries)
        return "{\n" + body + "\n    }"

    return (
        f"_NPM_VALUE_FLAGS = frozenset(\n    {block(VALUE)}\n)\n\n\n"
        f"_NPM_BOOLEAN_FLAGS = frozenset(\n    {block(BOOLEAN)}\n)\n\n\n"
        f"_NPM_SKIP_FLAGS = frozenset(\n    {block(SKIP)}\n)\n"
    )


def main() -> int:
    schema = read_schema()
    classes, conditional, union, unreadable = build(schema)

    print(
        f"npm {schema['npm_version']}: "
        f"{len(schema['definitions'])} definitions, "
        f"{len(schema['shorthands'])} shorthands",
        file=sys.stderr,
    )

    # REQUIRED REPORTING. An unlisted flag refuses, which is safe -- but the
    # set must be reviewed rather than accidental.
    print(
        f"\nCONDITIONAL ARITY -> left unlisted, scanner refuses ({len(conditional)}):",
        file=sys.stderr,
    )
    for entry in conditional:
        print(f"  {entry}", file=sys.stderr)
    print(f"\nVALUE flags carrying a Boolean member ({len(union)}):", file=sys.stderr)
    for entry in union:
        print(f"  {entry}", file=sys.stderr)
    print(f"\nUNINTERPRETABLE type ({len(unreadable)}):", file=sys.stderr)
    for entry in unreadable:
        print(f"  {entry}", file=sys.stderr)

    mismatches = check_against_nopt(schema)
    print(
        f"\nnopt cross-check: {len(mismatches)} mismatch(es) over "
        f"{len(schema['definitions'])} definitions",
        file=sys.stderr,
    )
    for entry in mismatches:
        print(f"  {entry}", file=sys.stderr)

    if "--verify" not in sys.argv:
        print(emit(classes))
        return 0

    source = REPO / "src" / "pmcp" / "manifest" / "version_checker.py"
    committed_tables = read_committed_tables(source)
    print(f"\nverifying tables in {source}", file=sys.stderr)
    failures = list(mismatches)
    for label, live, committed in (
        (
            "value",
            {f for f, c in classes.items() if c == VALUE},
            committed_tables["_NPM_VALUE_FLAGS"],
        ),
        (
            "boolean",
            {f for f, c in classes.items() if c == BOOLEAN},
            committed_tables["_NPM_BOOLEAN_FLAGS"],
        ),
        (
            "skip",
            {f for f, c in classes.items() if c == SKIP},
            committed_tables["_NPM_SKIP_FLAGS"],
        ),
    ):
        for flag in sorted(live - committed):
            failures.append(f"{label}: {flag} in live npm, MISSING from table")
        for flag in sorted(committed - live):
            failures.append(f"{label}: {flag} in table, absent from live npm")

    # A flag in both tables would let the boolean branch win or lose by table
    # order rather than by npm's schema; either way it is not a classification.
    names = ("_NPM_VALUE_FLAGS", "_NPM_BOOLEAN_FLAGS", "_NPM_SKIP_FLAGS")
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            for flag in sorted(committed_tables[left] & committed_tables[right]):
                failures.append(f"{flag} is in BOTH {left} and {right}")

    if failures:
        print(f"\nFAIL: {len(failures)} discrepanc(ies)", file=sys.stderr)
        for entry in failures:
            print(f"  {entry}", file=sys.stderr)
        return 1
    print(
        f"\nOK: {len(committed_tables['_NPM_VALUE_FLAGS'])} value + "
        f"{len(committed_tables['_NPM_BOOLEAN_FLAGS'])} boolean + "
        f"{len(committed_tables['_NPM_SKIP_FLAGS'])} skip entries "
        f"match live npm {schema['npm_version']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
