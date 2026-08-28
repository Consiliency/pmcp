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
declaration, independent of any default value. Independent of the environment
too, with ONE measured exception handled under HOST-ENUMERATED TYPES below.

WHAT `type` MEANS, AND THE TWO MEMBERS THAT ARE NOT VALUE TYPES
--------------------------------------------------------------
`type` is a SET, e.g. `color` is `['always', Boolean]`. Two members are
multiplicity/absence markers rather than value types and are dropped before
classifying:

  `null`   "unset". Dropping it is load-bearing: `yes`, `optional`,
           `production`, `workspaces` and `expect-results` are all
           `null|Boolean`, so without the drop they read as conditional and
           `--yes`/`-y` -- a real MCP launch form -- would be refused.
           The drop is for CLASSIFICATION only; the fourth table below needs
           the bit back, so it is read off the RAW type labels.
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

HOST-ENUMERATED TYPES: MEMBERS THAT ARE MACHINE FACTS (#193)
------------------------------------------------------------
One definition's `type` is not declared -- it is enumerated from the host.
`local-address` is `null` plus every address `os.networkInterfaces()` reports,
51 members on this machine and a different set on the next one. Classifying off
the members therefore made `--verify` green on one machine and red on another
with identical npm and identical source, and a drift check with false positives
gets ignored -- which is how real drift ships.

The failure is not merely "a different address list". `getLocalAddresses()`
CATCHES a `networkInterfaces()` throw and returns exactly `[null]`; the member
rule above strips `null` and calls the remainder UNINTERPRETABLE, which is the
report #193 was filed with. So an IP-presence test cannot detect this class --
on the very host that has the bug there is no IP to find.

The fix normalises the CLASS and changes nothing else:

  * detection happens in the NODE script, the only place the raw members still
    exist -- the serializer maps every string member to '<literal>', so no
    Python-side predicate can tell 51 addresses from `loglevel`'s 8 fixed
    words. Signals: `typeDescription === 'IP Address'` (npm's own label, and
    the one that survives the `[null]` case) or a `net.isIP` member scan.
  * `classify(..., host_enumerated=True)` returns VALUE regardless of members.
    `--local-address` consumes the next token on every host; only the member
    list varies, and it carries no classification information.
  * the flag is NOT exempted from `--verify`. Skipping it would blind the check
    to a real arity change on the flag most likely to drift; `--verify` reports
    which flags it normalised instead, so the behaviour is visible without
    removing anything from the comparison.

If npm ever renames the label AND the enumeration fails on the same host,
detection stops and the flag falls back to member classification --
UNINTERPRETABLE, i.e. a loud red verify rather than a silent wrong answer.
That is the correct direction and is stated here so it is not read as a gap.

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

THE FOURTH TABLE: WHICH BOOLEAN SPELLINGS ALSO CONSUME A LITERAL `null`
-----------------------------------------------------------------------
A boolean flag consumes a literal `true`/`false` as its value. A boolean whose
declared type ALSO carries `null` consumes a literal `null` as well, and `null`
is a real published npm package -- so the wrong answer in either direction
mints a wrong package identity. The rule, applied to the boolean table:

  a spelling is nullable iff its resolution target -- after shorthand
  expansion and after stripping a leading `no-` -- is a definition whose RAW
  declared type includes `null`.

On npm 11.19.0 that is five definitions (`yes`, `optional`, `production`,
`workspaces`, `expect-results`) but EIGHTEEN spellings, because `y`, `ws`,
`n` and `no` are shorthands (`n` and `no` both expand to `--no-yes`) and every
short alias/shorthand key is emitted in both its `-x` and `--x` form. A
hand-written version of this table shipped in 2.5.1 with twelve entries and so
got `--y -ws -n --n -no --no` wrong. Hence: generated, and cross-checked at
the SPELLING level against nopt (`--verify`), because the definition-level
probe alone would not have caught a spelling omission.

Deliberately NOT a pytest: npm's schema is version-specific and CI has no npm.
Behaviour is pinned by the pure unit tests in tests/test_version_checker.py;
this script is what keeps those pins honest against a real npm. Its own
classification logic IS pytested there, against a RECORDED `read_schema()`
(`--record-schema`, frozen in tests/fixtures/npm/schema.json) rather than a
live one -- a maintainer-only check is an unrun check.

Usage:
    python3 .consiliency/notes/derive_npm_flags.py            # emit the tables
    python3 .consiliency/notes/derive_npm_flags.py --verify   # check committed
    python3 .consiliency/notes/derive_npm_flags.py --record-schema PATH
                                                  # freeze read_schema() output
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
const net = require('net');
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

// Is this definition's `type` enumerated from the HOST rather than declared?
// This question can only be answered HERE. `label` below maps every string or
// number member to the literal '<literal>', so by the time the type array
// reaches Python no real address survives and no Python-side predicate can
// distinguish `local-address`'s 51 machine addresses from `loglevel`'s 8 fixed
// words. Two independent signals, either sufficient:
//   * npm's own `typeDescription` label for the class ('IP Address'). Primary,
//     because it is the one signal that still holds when the enumeration FAILS:
//     `getLocalAddresses()` catches a `networkInterfaces()` throw and returns
//     exactly `[null]`, and a member scan sees nothing at all in that case.
//   * a `net.isIP` scan over the raw members, as a backstop for a future
//     host-derived type npm does not label. Note `net.isIP` rejects a
//     link-local address carrying a `%zone` suffix, which is the other reason
//     the label is primary rather than the scan.
const hostEnumerated = (v, t) =>
  v.typeDescription === 'IP Address' ||
  t.some((x) => typeof x === 'string' && net.isIP(x) !== 0);

const definitions = {};
for (const [k, v] of Object.entries(m.definitions)) {
  const t = Array.isArray(v.type) ? v.type : [v.type];
  const lit = t.find((x) => typeof x === 'string' || typeof x === 'number');
  // `null` is in the probe list because a nullable boolean takes it as its
  // VALUE while every other boolean leaves it as the package name -- and
  // `null` is a real published package, so this is the probe that keeps the
  // nullable table honest instead of merely internally consistent.
  const probes = ['zzarbitrary', 'true', 'false', 'null'];
  if (lit !== undefined) probes.push(String(lit));
  definitions[k] = {
    type: t.map(label),
    // Emitted so the CI-side tests -- which have neither npm nor node -- can
    // assert on the real strings that drive the detection above, instead of
    // on a hand-written guess at what they say.
    typeDescription: v.typeDescription === undefined ? null : v.typeDescription,
    hostEnumerated: hostEnumerated(v, t),
    short: v.short === undefined ? [] : [].concat(v.short),
    probes: Object.fromEntries(probes.map((p) => [p, consumes(k, p)])),
  };
}

// The same question asked of every SPELLING rather than every definition:
// does `npm exec <spelling> null zz` resolve to `zz` (null consumed) or to
// `null` (null is the package)? The 2.5.1 defect was a spelling omission with
// a correct definition set, so a definition-level probe would have missed it.
const nullSpellings = {};
const probeSpelling = (flag) => {
  nullSpellings[flag] = !remain(['exec', flag, 'null', 'zz']).includes('null');
};
for (const [k, v] of Object.entries(m.definitions)) {
  probeSpelling('--' + k);
  probeSpelling('--no-' + k);
  for (const s of (v.short === undefined ? [] : [].concat(v.short))) {
    probeSpelling('-' + s);
    probeSpelling('--' + s);
  }
}
for (const key of Object.keys(m.shorthands)) {
  probeSpelling('-' + key);
  probeSpelling('--' + key);
}

console.log(JSON.stringify({
  npm_version: require(path.join(root, 'package.json')).version,
  definitions,
  shorthands: m.shorthands,
  null_spellings: nullSpellings,
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


def _host_enumerated(info: dict) -> bool:
    """The node side's verdict for one definition, defaulting to False.

    `.get` rather than `[...]` so a schema recorded before this field existed
    still classifies by members instead of raising.
    """
    return bool(info.get("hostEnumerated", False))


def host_enumerated_flags(schema: dict) -> list[str]:
    """Definition names whose `type` npm builds from this machine."""
    return sorted(
        name for name, info in schema["definitions"].items() if _host_enumerated(info)
    )


def classify(type_labels: list[str], *, host_enumerated: bool = False) -> str:
    """npm's declared `type` -> one of BOOLEAN / VALUE / CONDITIONAL.

    `host_enumerated` short-circuits to VALUE REGARDLESS of the members,
    including the bare `["null"]` case. A host-enumerated type's members are
    facts about the machine -- `local-address` is npm's own network addresses
    -- so they carry no classification information and vary between two hosts
    running identical npm. The classification does not vary: `--local-address`
    consumes the next token everywhere.

    The `["null"]` case is not hypothetical and is why this cannot be a
    member-filtering rule: `getLocalAddresses()` catches a
    `networkInterfaces()` failure and returns exactly `[null]`, which the
    member rule below strips to nothing and calls UNINTERPRETABLE. That is
    precisely the false drift report Consiliency/pmcp#193 filed.
    """
    if host_enumerated:
        return VALUE
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


def build(
    schema: dict,
) -> tuple[dict[str, str], dict[str, bool], list[str], list[str], list[str]]:
    """Return (flag -> class, flag -> nullable, conditional, union, unreadable)."""
    defs = schema["definitions"]
    classes: dict[str, str] = {}
    nullable: dict[str, bool] = {}
    conditional: list[str] = []
    union_with_boolean: list[str] = []
    unreadable: list[str] = []

    def record(flag: str, cls: str, takes_null: bool = False) -> None:
        """Write one entry, refusing to silently reclassify an existing one.

        Definitions, `short` aliases and shorthands are written in that order,
        so a plain `classes[flag] = cls` would let a later source overwrite an
        earlier one. That is the fail-OPEN drift direction -- it yields a
        WRONG entry rather than a missing one -- and it would pass `--verify`
        silently because the generator and the table would agree with each
        other while both disagreeing with npm. Empty on 11.19.0; this exists
        so a future npm cannot make it non-empty quietly.

        The same applies to nullability, which is written twice for `-y`/`--y`
        (once from `yes`'s `short`, once from the `y` shorthand): agreeing
        today is not a reason to let a future npm disagree in silence.
        """
        previous = classes.get(flag)
        if previous is not None and previous != cls:
            raise SystemExit(
                f"conflicting classification for {flag}: {previous} vs {cls}"
            )
        previous_null = nullable.get(flag)
        if previous_null is not None and previous_null != takes_null:
            raise SystemExit(
                f"conflicting nullability for {flag}: {previous_null} vs {takes_null}"
            )
        classes[flag] = cls
        nullable[flag] = takes_null

    # Nullability is read off the RAW type labels, BEFORE `classify` drops
    # `null` as a non-value marker: the drop is what makes `--yes` a boolean
    # at all, and this table is precisely the bit the drop throws away.
    name_nullable: dict[str, bool] = {
        name: "null" in info["type"] for name, info in defs.items()
    }

    name_class: dict[str, str] = {}
    for name, info in defs.items():
        cls = classify(info["type"], host_enumerated=_host_enumerated(info))
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
        # Only a BOOLEAN can be nullable: a VALUE flag consumes the next token
        # whatever it is, so `null` is never a special case for it.
        takes_null = cls == BOOLEAN and name_nullable[name]
        if cls in (BOOLEAN, VALUE):
            record(f"--{name}", cls, takes_null)
            for short in info["short"]:
                record(f"-{short}", cls, takes_null)
                record(f"--{short}", cls, takes_null)
        if cls == BOOLEAN:
            # `--no-X` negates a boolean and, like the boolean itself,
            # consumes only a literal true/false -- plus `null` when the base
            # definition is nullable. Verified against nopt.
            record(f"--no-{name}", cls, takes_null)

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
            base = None
        else:
            target = expansion[0].lstrip("-")
            if target.startswith("no-"):
                # `--no-global`, `--no-yes`: boolean-arity iff the base is a
                # boolean. Otherwise unlisted -- `--no-color` refuses, safely.
                base = target[3:]
                cls = BOOLEAN if name_class.get(base) == BOOLEAN else CONDITIONAL
            else:
                base = target
                cls = name_class.get(target, CONDITIONAL)
        # `n` and `no` both expand to `--no-yes`, so stripping the `no-` is
        # what makes `-n`/`--n`/`-no`/`--no` inherit `yes`'s nullability --
        # the four spellings 2.5.1's hand-written table missed outright.
        takes_null = cls == BOOLEAN and bool(base) and name_nullable.get(base, False)
        if cls in (BOOLEAN, VALUE, SKIP):
            record(f"-{key}", cls, takes_null)
            record(f"--{key}", cls, takes_null)

    return classes, nullable, conditional, union_with_boolean, unreadable


def check_against_nopt(schema: dict) -> list[str]:
    """Declared class + the true/false rule must reproduce npm's own parser.

    This is the check that makes "verified against npm's parser" true rather
    than asserted. A BOOLEAN flag is predicted to consume a probe iff the probe
    is exactly `true`/`false` -- or `null`, when the declared type carries
    `null`; a VALUE flag is predicted to consume every probe; a CONDITIONAL
    flag makes no prediction because it is unlisted.
    """
    bad: list[str] = []
    for name, info in schema["definitions"].items():
        cls = classify(info["type"], host_enumerated=_host_enumerated(info))
        for probe, observed in info["probes"].items():
            if cls == BOOLEAN:
                predicted = probe in ("true", "false") or (
                    probe == "null" and "null" in info["type"]
                )
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


_TABLE_NAMES = (
    "_NPM_VALUE_FLAGS",
    "_NPM_BOOLEAN_FLAGS",
    "_NPM_NULLABLE_BOOLEAN_FLAGS",
    "_NPM_SKIP_FLAGS",
)

# The nullable table is a SUBSET of the boolean table by construction, so it is
# excluded from the pairwise-disjointness check below and gets a containment
# check instead.
_DISJOINT_NAMES = (
    "_NPM_VALUE_FLAGS",
    "_NPM_BOOLEAN_FLAGS",
    "_NPM_SKIP_FLAGS",
)


def check_null_spellings(
    schema: dict, classes: dict[str, str], live_nullable: set[str]
) -> list[str]:
    """The derived nullable table must equal what nopt does, SPELLING by SPELLING.

    `check_against_nopt` asks the question of each definition; this asks it of
    each spelling, which is where 2.5.1 went wrong -- the five nullable
    definitions were right and six of their eighteen spellings were missing.
    Restricted to the boolean table on purpose: a VALUE flag consumes `null`
    like it consumes anything else, and a spelling that is not in any table
    makes the scanner refuse rather than predict.
    """
    observed = schema["null_spellings"]
    booleans = {f for f, c in classes.items() if c == BOOLEAN}
    bad: list[str] = []
    for flag in sorted(booleans):
        if flag not in observed:
            # Every boolean spelling this generator emits is enumerable in the
            # node probe; if one is not, the two enumerations have drifted.
            bad.append(f"{flag}: boolean table entry never probed against nopt")
            continue
        predicted = flag in live_nullable
        if predicted != observed[flag]:
            bad.append(
                f"npm exec {flag} null zz: derived nullable={predicted}, "
                f"nopt consumes null={observed[flag]}"
            )
    return bad


def read_committed_tables(source: pathlib.Path) -> dict[str, set[str]]:
    """Parse the four tables out of version_checker.py with `ast`.

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
            if name not in _TABLE_NAMES:
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
    missing = set(_TABLE_NAMES) - found.keys()
    if missing:
        raise SystemExit(f"not module-level literals in {source}: {sorted(missing)}")
    return found


def nullable_table(classes: dict[str, str], nullable: dict[str, bool]) -> set[str]:
    """The boolean spellings that also consume a literal `null`."""
    return {f for f, c in classes.items() if c == BOOLEAN and nullable.get(f)}


def emit(classes: dict[str, str], nullable: dict[str, bool]) -> str:
    def render(entries: set[str] | list[str]) -> str:
        body = "\n".join(f'        "{e}",' for e in sorted(entries))
        return "{\n" + body + "\n    }"

    def block(cls: str) -> str:
        return render([f for f, c in classes.items() if c == cls])

    return (
        f"_NPM_VALUE_FLAGS = frozenset(\n    {block(VALUE)}\n)\n\n\n"
        f"_NPM_BOOLEAN_FLAGS = frozenset(\n    {block(BOOLEAN)}\n)\n\n\n"
        "_NPM_NULLABLE_BOOLEAN_FLAGS = frozenset(\n    "
        f"{render(nullable_table(classes, nullable))}\n)\n\n\n"
        f"_NPM_SKIP_FLAGS = frozenset(\n    {block(SKIP)}\n)\n"
    )


_FIXTURE_README = (
    "RECORDED OUTPUT of read_schema() in .consiliency/notes/derive_npm_flags.py, "
    "captured against a real npm on a maintainer machine. CI has no npm and no "
    "node, so tests that would otherwise be maintainer-only mock read_schema() "
    "with this. Regenerate with: python3 .consiliency/notes/derive_npm_flags.py "
    "--record-schema tests/fixtures/npm/schema.json. Ignored by the generator, "
    "which reads only npm_version/definitions/shorthands/null_spellings. NOTE "
    "local-address's `type` here is this machine's address list reduced to "
    "'<literal>' labels; its LENGTH is a host fact and the tests patch it."
)


def record_schema(dest: pathlib.Path) -> int:
    """Freeze `read_schema()` to JSON so the CI tests can run without npm.

    The tests this feeds are the ones that would otherwise only ever run on a
    maintainer's machine -- and a check that never runs in CI is the same
    ignored check that #193 is about.
    """
    payload = {"_README": _FIXTURE_README, **read_schema()}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"recorded {dest} ({dest.stat().st_size} bytes)", file=sys.stderr)
    return 0


def main() -> int:
    if "--record-schema" in sys.argv:
        return record_schema(
            pathlib.Path(sys.argv[sys.argv.index("--record-schema") + 1])
        )

    schema = read_schema()
    classes, nullable, conditional, union, unreadable = build(schema)
    live_nullable = nullable_table(classes, nullable)

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

    # NOT an exemption. These flags stay in the comparison below; what is
    # normalised is their CLASS, which is host-independent even though their
    # declared members are not. Deliberately prints no member count -- that is
    # the host fact, and printing it would make this line differ between two
    # machines running identical npm, which is the bug being fixed.
    normalised = host_enumerated_flags(schema)
    print(
        f"\nHOST-ENUMERATED type -> normalised to {VALUE}, still verified "
        f"({len(normalised)}):",
        file=sys.stderr,
    )
    for name in normalised:
        print(f"  --{name}", file=sys.stderr)

    mismatches = check_against_nopt(schema)
    print(
        f"\nnopt cross-check: {len(mismatches)} mismatch(es) over "
        f"{len(schema['definitions'])} definitions",
        file=sys.stderr,
    )
    for entry in mismatches:
        print(f"  {entry}", file=sys.stderr)

    null_mismatches = check_null_spellings(schema, classes, live_nullable)
    print(
        f"\nnullable spellings: {len(live_nullable)} derived, "
        f"{len(null_mismatches)} mismatch(es) over "
        f"{len(schema['null_spellings'])} spellings probed with a literal `null`",
        file=sys.stderr,
    )
    for entry in null_mismatches:
        print(f"  {entry}", file=sys.stderr)
    print(f"  derived: {' '.join(sorted(live_nullable))}", file=sys.stderr)

    if "--verify" not in sys.argv:
        print(emit(classes, nullable))
        return 0

    source = REPO / "src" / "pmcp" / "manifest" / "version_checker.py"
    committed_tables = read_committed_tables(source)
    print(f"\nverifying tables in {source}", file=sys.stderr)
    failures = list(mismatches) + null_mismatches
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
            "nullable",
            live_nullable,
            committed_tables["_NPM_NULLABLE_BOOLEAN_FLAGS"],
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
    for i, left in enumerate(_DISJOINT_NAMES):
        for right in _DISJOINT_NAMES[i + 1 :]:
            for flag in sorted(committed_tables[left] & committed_tables[right]):
                failures.append(f"{flag} is in BOTH {left} and {right}")

    # The nullable table is exempt from the disjointness check because it is a
    # SUBSET of the boolean table -- so pin that instead. A nullable entry that
    # is not a boolean entry is dead code: `_npm_package_arg` only consults it
    # from inside the boolean branch, so it would never be reached.
    for flag in sorted(
        committed_tables["_NPM_NULLABLE_BOOLEAN_FLAGS"]
        - committed_tables["_NPM_BOOLEAN_FLAGS"]
    ):
        failures.append(
            f"{flag} is in _NPM_NULLABLE_BOOLEAN_FLAGS but not _NPM_BOOLEAN_FLAGS"
        )

    if failures:
        print(f"\nFAIL: {len(failures)} discrepanc(ies)", file=sys.stderr)
        for entry in failures:
            print(f"  {entry}", file=sys.stderr)
        return 1
    print(
        f"\nOK: {len(committed_tables['_NPM_VALUE_FLAGS'])} value + "
        f"{len(committed_tables['_NPM_BOOLEAN_FLAGS'])} boolean "
        f"({len(committed_tables['_NPM_NULLABLE_BOOLEAN_FLAGS'])} of them nullable) + "
        f"{len(committed_tables['_NPM_SKIP_FLAGS'])} skip entries "
        f"match live npm {schema['npm_version']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
