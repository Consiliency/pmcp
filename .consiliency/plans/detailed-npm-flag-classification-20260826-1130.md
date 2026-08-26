# Detailed plan: convert npm to fail-closed flag classification

## Task

Close Consiliency/pmcp#180's remaining residual. `_npm_package_arg` is the one
ecosystem still **failing open** on unlisted flags: it skips anything starting
`-` and takes the next bare token, so a flag's *value* becomes the package
name. Reproduced on `2.5.0`:

```
npm exec --loglevel silly a / b        -> both ('npm', 'silly')
npm exec --registry https://r a / b    -> both ('npm', 'https://r')
npx -y --registry https://r a / b      -> both ('npm', 'https://r')
```

Two different servers share one identity, and v2.4.0's gate reads that as a
**positive confirmation**, serving the wrong package's tool descriptions. #182
converted uvx, pip, cargo and docker to three-way classification with a
fail-closed default; npm was deliberately deferred because it needs its boolean
globals enumerated, and that enumeration is this plan's real content.

## Research summary

Context is in session from #180/#182/#183; the branch was read directly
(`_npm_package_arg`, `src/pmcp/manifest/version_checker.py`).

**npm exposes its real option schema — but NOT via `npm config list --json`.**
*This section was rewritten after board review; the original source and arity
model were both wrong. See **Rejected** below.*

The authoritative source is npm's installed config package:

```
@npmcli/config/lib/definitions/index.js
  -> definitions  (181 entries, each carrying a real `type`)
  -> shorthands   (40 entries, each EXPANDING to its underlying flags)
```

`type` is the actual arity declaration, not an inference from a default value.
Measured on npm 11.19.0:

```
color      type = always|Boolean      <- union: boolean AND value-accepting
global     type = Boolean
loglevel   type = silent|error|warn|notice|http|info|verbose|silly
package    type = String|Array
```

`shorthands` solves the alias problem outright rather than by hand-listing:

```
shorthands.silent = ["--loglevel","silent"]
shorthands.q      = ["--loglevel","warn"]
```

So `--silent` is not a missing key needing a second source — it is a shorthand
that *expands*, and expansion is the correct model for all 40.

### Rejected: `npm config list --json` + arity-from-default-type

*Board finding, verified — the original plan was built on this and it is
unsound two ways.*

1. **The arity model was wrong.** The plan defined boolean flags as consuming
   nothing. npm's parser (`nopt`) consumes a following `true`/`false`, and a
   union type like `always|Boolean` accepts a value. Verified: under the
   original design both of these **still collide**, which is the exact defect
   the phase exists to close:

   ```
   npm exec --global false a / b   -> both ('npm','false')
   npm exec --color always a / b   -> both ('npm','always')
   ```

   `npm exec --color always --help` exits 0, so this is a legal form.

2. **The source was not a schema.** `config list --json` emits the *active
   merged configuration*, not the definition set: it omits private entries
   (`_auth`, `password`), adds host-specific ones (`npm-version`, a user-scoped
   registry), and changes with the environment — `NPM_CONFIG_COLOR=always`
   flips `color` from boolean to string. Removing the user config took the
   count 181 → 180. **The same generator would classify differently on
   different hosts**, which is disqualifying for a committed table.

The 41 `null`-default keys the original plan planned to leave unlisted are also
a non-issue under the real source: `type` is declared regardless of default.

**The pinned ordering that must survive** (`tests/test_version_checker.py`):

```
npm --silent exec pkg  -> pkg     npm install i    -> i
npm exec pkg           -> pkg     npm exec exec    -> exec
```

## Design: expand shorthands, then classify by npm's declared `type`

*Rewritten after board review — the two-way boolean/value split was falsified.*

**Step 1 — expand shorthands first**, with arity taken from the expansion's
*length*, a rule verified against npm's parser:

| expansion | arity |
|---|---|
| length ≥ 2 — a value is baked in (`silent` → `["--loglevel","silent"]`, `q` → `["--loglevel","warn"]`) | **boolean-arity**: consumes nothing |
| length 1 — a pure rename (`reg` → `["--registry"]`, `w` → `["--workspace"]`, `y` → `["--yes"]`, `g` → `["--global"]`) | **the target's arity** |

All 40 come from `shorthands`, plus the 19 `short` fields on definitions, so no
alias is hand-listed and none is missed. *Board finding: the config dump omits
shorthands **wholesale**, not just `--silent` — a 40-fold larger hole than the
original plan admitted, but closed by a second mechanical read of the same
module rather than by curation.* This subsumes the existing hand-coded `-y`
special case, which should be retired into it, and makes the pinned
`npm --silent exec pkg` → `pkg` hold by construction.

**Step 2 — classify the expanded flag by its declared `type`**, which is a
*set*, not a scalar:

**`null` in a type list means "unset", not a value type — drop it first.**
Without that, `yes`, `optional`, `production`, `workspaces` and
`expect-results` (all `null|Boolean`) read as mixed, and `--yes`/`-y` — a real
MCP launch form — would be refused. Measured: naive `x != Boolean` gives 7
mixed; dropping `null` gives the correct classification.

| declared type (after dropping `null`) | behaviour |
|---|---|
| `Boolean` alone | skip; the next token is still a candidate |
| no Boolean member (`String`, `Url`, `Path`, an enum, an Array, `false\|{}`) | **consumes the next token** |
| Boolean **and** a non-Boolean member | **unlisted → refuse** — arity is conditional and no single class is right |
| `--package` | known-positive: its value **is** the package |
| not in `definitions` at all | **`("unknown", None)`** |

**The conditional set is exactly `{color, browser}` — verified against npm's own
parser (`nopt` + npm's type map), not from the type strings.** A board seat
proposed `{browser, color, proxy}`; `proxy` is `null|false|{}` with no `Boolean`
member and **always consumes**, including `--proxy a` where it swallows the
package, so it is a *value* flag and mis-grouping it would have left that form
refusing unnecessarily:

```
--proxy http://p a  -> remain ["exec","a"]     consumes
--proxy false a     -> remain ["exec","a"]     consumes
--proxy a           -> remain ["exec"]         consumes the package
--color always a    -> remain ["exec","a"]     consumes
--color a           -> remain ["exec","a"]     does NOT consume  <- conditional
--browser a         -> remain ["exec"]         consumes
```

`browser` (`null|Boolean|String`) is genuinely mixed and stays unlisted.

**A Boolean flag still consumes a literal `true`/`false`.** npm's parser
accepts `--global false`, so a bare `true`/`false` following a Boolean flag is
its value, not a positional. Verified: without this, `npm exec --global false a`
and `... b` both resolve to `('npm','false')`.

The rule for anything ambiguous is unchanged from #182: **fail closed**. An
omission costs auto-update for one form; a wrong entry serves the wrong
package's descriptions.

**Table provenance is the deliverable, not the table.** A committed generator
reads `@npmcli/config`'s `definitions` and `shorthands` and emits the sets.
Nothing is hand-listed — #182 removed ten memory-drafted entries across four
ecosystems for exactly this reason, and npm's 181 flags are far past recall.

**The honest residual:** a wrong entry still fails open, and npm's schema is
version-specific — a future npm could add a flag this table does not know,
which then refuses (safe) or, if the type declaration changes meaning,
misclassifies (unsafe). The verifier exists to make that drift detectable.

## Changes

### `src/pmcp/manifest/version_checker.py` (modify)

- `_NPM_VALUE_FLAGS`, `_NPM_BOOLEAN_FLAGS` — **add** — module-level frozensets,
  generated (see below), each entry carrying `--` and any documented short
  alias. Module-level, not function-local: #182 found `_docker_image_arg`'s
  table rebuilt on every call and invisible to the verifier.
- `_npm_package_arg` — **modify** — consult the tables and fail closed on an
  unlisted bare flag, exactly as the uvx/pip/cargo/docker scanners now do.
  Keep the one-shot leading-subcommand skip and the `--package` positive
  handling unchanged.

### `.consiliency/notes/derive_npm_flags.py` (create)

- Generator + verifier, mirroring `verify_tables.py` from #182 — reads
  `@npmcli/config`'s **`definitions` and `shorthands`**, not
  `npm config list --json`, and re-verifies a committed table against live npm
  so drift is detectable. Deliberately not a pytest: npm's schema is
  version-specific and CI has no npm.
- It must **print** every flag it classifies as consuming a value *because of a
  union with Boolean* (`always|Boolean`), since that class is what falsified
  the first design and is the least obvious to a reader.
- It must **print** anything in `definitions` whose `type` it cannot interpret,
  rather than silently omitting it — an unlisted flag refuses, which is safe,
  but the set must be reviewed rather than accidental.

### `tests/test_version_checker.py` (modify)

- `TestNpmValueFlagCollisions` — **add** — inequality assertions for the three
  reproduced pairs, **plus the two the board found surviving the original
  design**: `npm exec --global false a/b` (Boolean consuming a literal
  `true`/`false`) and `npm exec --color always a/b` (a `Boolean` union that
  accepts a value). All five RED today; the last two would have shipped
  colliding.
- `TestNpmFailsClosed` — **add** — an unlisted bare flag yields
  `("unknown", None)`.
- `TestNpmPinnedOrderingSurvives` — **add** — the four forms above, plus
  `npm --silent exec pkg` specifically, since `--silent` is the alias case the
  config dump misses and therefore the most likely regression.

## Documentation impact

- `CHANGELOG.md` — **add** — a `### Fixed` bullet: the collision closes, and
  a server launched with a flag npm's own config does not describe can no
  longer be auto-updated. Same cost sentence #182 used; do not soften it.
- `README.md` — **none**; no documented npm form changes. Verify the README's
  npm examples still resolve rather than assuming.

## Dependencies & order

1. Generate and review the tables **before** touching the scanner — the table
   is the risky artifact, not the code.
2. Scanner change and tables in one commit; a half-applied rule leaves npm
   partly fail-open, which is worse than uniformly fail-open because it looks
   fixed.
3. Tests RED per node before the implementation.

## Verification

```bash
cd /mnt/workspace/worktrees/pmcp-180-npm
find src tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null

# The three collisions, gone:
PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'
from pmcp.manifest.version_checker import detect_package_type as d
pairs=[("npm",["exec","--loglevel","silly","a"],["exec","--loglevel","silly","b"]),
       ("npm",["exec","--registry","https://r","a"],["exec","--registry","https://r","b"]),
       ("npx",["-y","--registry","https://r","a"],["-y","--registry","https://r","b"])]
bad=[(c,d(c,x)) for c,x,y in pairs if d(c,x)==d(c,y) and d(c,x)!=("unknown",None)]
print("still colliding:", bad or "none"); assert not bad
PY

# The pinned ordering, intact:
PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'
from pmcp.manifest.version_checker import detect_package_type as d
for a,exp in [(["--silent","exec","pkg"],"pkg"),(["exec","pkg"],"pkg"),
              (["install","i"],"i"),(["exec","exec"],"exec")]:
    got=d("npm",a)[1]; assert got==exp, f"{a} -> {got}, expected {exp}"
print("pinned ordering intact")
PY

python3 .consiliency/notes/derive_npm_flags.py --verify   # table vs live npm
PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/ -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
```

**Mutation proof, per node, with an import-provenance guard.** #182 established
that without a provenance check every mutant falsely survives while the
unmutated worktree serves the imports — assert
`version_checker.__file__` resolves inside the mutated copy before trusting any
result. Purge `__pycache__` and use `PYTHONDONTWRITEBYTECODE=1`: stale bytecode
fabricated a false RED in #184 and can equally fabricate a false GREEN.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```

## Execution Policy
- execute: effort=medium, reason=small scanner change but the table is large and a wrong entry fails open

## Acceptance criteria

- [ ] **Five** pairs satisfy `d(a) != d(b)` **or** `d(a) == ("unknown", None)`,
      each pair's exact value pinned — the three originally reproduced, plus
      `npm exec --global false a/b` and `npm exec --color always a/b`.

      *These last two are the acceptance set's whole point.* A board seat
      **implemented the original plan faithfully in a copy** and demonstrated
      that all three original collision checks pass, the pinned orderings pass,
      the fails-closed check passes — **and `npm exec --color always a/b` still
      returns `('npm','always')` for both**. The suite was structurally blind to
      the exact entry the method got wrong. An acceptance set that cannot see
      the defect its own method manufactures is not an acceptance set.

      `--color always` pins to `("unknown", None)` under the corrected table
      (conditional arity → unlisted → refuse). RED today, mutation-proved per
      node with recorded output.
- [ ] **`npm --silent exec pkg` → `pkg`** still holds. `--silent` is absent
      from `npm config list --json` (it aliases `--loglevel=silent`), so this
      is the single most likely regression and gets its own criterion.
- [ ] `npm install i` → `i`, `npm exec exec` → `exec`, `npm exec pkg` → `pkg`
      unchanged; `npx -y exec` → `exec` unchanged.
- [ ] An unlisted bare flag yields `("unknown", None)`, never a wrong name.
- [ ] **The table is generated from `@npmcli/config`'s `definitions` and
      `shorthands`**, not from `npm config list --json` and not from recall,
      and the generator is committed. *Board finding: `config list --json` is
      the active merged config, not a schema — it omits private keys, adds
      host-specific ones, and `NPM_CONFIG_COLOR=always` flips `color` from
      boolean to string, so the same generator classifies differently on
      different hosts.* The generator must print every union-with-Boolean flag
      and anything whose `type` it cannot interpret. #182 removed ten
      memory-drafted entries; none may be reintroduced.
- [ ] **All 40 shorthands expand**, verified against `shorthands` rather than
      hand-listed, with arity from expansion length (≥2 ⇒ boolean, 1 ⇒ target's
      arity). `--silent` → `--loglevel silent`, `-q` → `--loglevel warn`,
      `-w` → `--workspace` (consumes), `-y` → `--yes` (boolean). This makes
      `npm --silent exec pkg` → `pkg` hold by construction, and the existing
      hand-coded `-y` special case is **retired into it**, not left alongside.
- [ ] **The conditional-arity set is derived and pinned as exactly
      `{color, browser}`** — verified against npm's own parser, not from type
      strings. `proxy` is `null|false|{}` with no Boolean member and **always**
      consumes (including `--proxy a`, swallowing the package), so it is a
      value flag; a board seat grouped it as mixed, which would have refused a
      form that is unambiguous. Any future flag the generator classifies as
      conditional must be reported, not silently unlisted.
- [ ] **`null` is dropped from type lists before classifying.** It means
      "unset", not a value type. Without this, `yes`, `optional`, `production`,
      `workspaces` and `expect-results` read as conditional and `--yes`/`-y` —
      a real MCP launch form — is refused.
- [ ] Every manifest server still resolves (98/98 at 2.5.0) — proven by a scan,
      not assumed.
- [ ] Full suite, ruff, mypy green; CHANGELOG records the fix **and** the
      auto-update cost.
- [ ] **#180's status is decided explicitly** — closed if the gate now holds
      for ordinary npm commands, or its remaining gap named. It has been
      narrowed twice; do not close it by assumption a third time.
