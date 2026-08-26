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
               => boolean-arity, consumes nothing.
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

BOOLEAN, VALUE, CONDITIONAL, UNINTERPRETABLE = (
    "boolean",
    "value",
    "conditional",
    "uninterpretable",
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
            classes[f"--{name}"] = cls
            for short in info["short"]:
                classes[f"-{short}"] = cls
                classes[f"--{short}"] = cls
        if cls == BOOLEAN:
            # `--no-X` negates a boolean and, like the boolean itself,
            # consumes only a literal true/false. Verified against nopt.
            classes[f"--no-{name}"] = cls

    for key, expansion in schema["shorthands"].items():
        if len(expansion) >= 2:
            cls = BOOLEAN  # a value is baked into the expansion
        else:
            target = expansion[0].lstrip("-")
            if target.startswith("no-"):
                # `--no-global`, `--no-yes`: boolean-arity iff the base is a
                # boolean. Otherwise unlisted -- `--no-color` refuses, safely.
                cls = BOOLEAN if name_class.get(target[3:]) == BOOLEAN else CONDITIONAL
            else:
                cls = name_class.get(target, CONDITIONAL)
        if cls in (BOOLEAN, VALUE):
            classes[f"-{key}"] = cls
            classes[f"--{key}"] = cls

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


def emit(classes: dict[str, str]) -> str:
    def block(cls: str) -> str:
        entries = sorted(f for f, c in classes.items() if c == cls)
        body = "\n".join(f'        "{e}",' for e in entries)
        return "{\n" + body + "\n    }"

    return (
        f"_NPM_VALUE_FLAGS = frozenset(\n    {block(VALUE)}\n)\n\n\n"
        f"_NPM_BOOLEAN_FLAGS = frozenset(\n    {block(BOOLEAN)}\n)\n"
    )


def main() -> int:
    schema = read_schema()
    classes, conditional, union, unreadable = build(schema)

    print(f"npm {schema['npm_version']}: "
          f"{len(schema['definitions'])} definitions, "
          f"{len(schema['shorthands'])} shorthands", file=sys.stderr)

    # REQUIRED REPORTING. An unlisted flag refuses, which is safe -- but the
    # set must be reviewed rather than accidental.
    print(f"\nCONDITIONAL ARITY -> left unlisted, scanner refuses ({len(conditional)}):",
          file=sys.stderr)
    for entry in conditional:
        print(f"  {entry}", file=sys.stderr)
    print(f"\nVALUE flags carrying a Boolean member ({len(union)}):", file=sys.stderr)
    for entry in union:
        print(f"  {entry}", file=sys.stderr)
    print(f"\nUNINTERPRETABLE type ({len(unreadable)}):", file=sys.stderr)
    for entry in unreadable:
        print(f"  {entry}", file=sys.stderr)

    mismatches = check_against_nopt(schema)
    print(f"\nnopt cross-check: {len(mismatches)} mismatch(es) over "
          f"{len(schema['definitions'])} definitions", file=sys.stderr)
    for entry in mismatches:
        print(f"  {entry}", file=sys.stderr)

    if "--verify" not in sys.argv:
        print(emit(classes))
        return 0

    sys.path.insert(0, str(REPO / "src"))
    from pmcp.manifest import version_checker as vc

    print(f"\nverifying tables in {vc.__file__}", file=sys.stderr)
    failures = list(mismatches)
    for label, live, committed in (
        ("value", {f for f, c in classes.items() if c == VALUE}, vc._NPM_VALUE_FLAGS),
        ("boolean", {f for f, c in classes.items() if c == BOOLEAN},
         vc._NPM_BOOLEAN_FLAGS),
    ):
        for flag in sorted(live - committed):
            failures.append(f"{label}: {flag} in live npm, MISSING from table")
        for flag in sorted(committed - live):
            failures.append(f"{label}: {flag} in table, absent from live npm")

    # A flag classified one way here and the other way in the table is the
    # fail-OPEN direction if the table says boolean; report both directions.
    overlap = vc._NPM_VALUE_FLAGS & vc._NPM_BOOLEAN_FLAGS
    for flag in sorted(overlap):
        failures.append(f"{flag} is in BOTH committed tables")

    if failures:
        print(f"\nFAIL: {len(failures)} discrepanc(ies)", file=sys.stderr)
        for entry in failures:
            print(f"  {entry}", file=sys.stderr)
        return 1
    print(
        f"\nOK: {len(vc._NPM_VALUE_FLAGS)} value + "
        f"{len(vc._NPM_BOOLEAN_FLAGS)} boolean entries match live npm",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
