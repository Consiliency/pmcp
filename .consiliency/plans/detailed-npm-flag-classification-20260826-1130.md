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

**npm exposes its own config schema, so the enumeration can be mechanical.**
`npm config list --json` returns **181 keys with typed default values**, from
which arity follows: a `bool` default is a boolean flag, anything else takes a
value. Measured: 70 boolean-typed, 111 value-typed. Spot-checked against known
truth — `loglevel`→value, `registry`→value, `global`→bool, `prefix`→value,
`workspace`→value: all correct.

This matters because #182 found **ten entries drafted from memory** across the
other four ecosystems that could not be verified against real `--help`; all ten
were removed. npm has ~181 flags — far past what recall can cover — so a
mechanically-derived table is not a nicety here, it is the only defensible
method.

**Two limits of that source, found before planning rather than during:**

1. **`--silent` is absent from the config dump entirely.** It is an alias for
   `--loglevel=silent`, not a config key. Aliases must come from a second
   source (`npm help npm`'s option list, or npm's documented alias table), or
   `npm --silent exec pkg` — a currently-pinned form — regresses.
2. **41 keys have `null` defaults**, where boolean-vs-value is not inferable
   from the default alone. These must be classified from a second source or
   left **unlisted**, which under a fail-closed default means "refuse". Leaving
   them unlisted is safe; guessing is not.

**The pinned ordering that must survive** (`tests/test_version_checker.py`):

```
npm --silent exec pkg  -> pkg     npm install i    -> i
npm exec pkg           -> pkg     npm exec exec    -> exec
```

## Design: extend #182's three-way classification to npm

Same shape as the other four ecosystems, same fail-closed default:

| kind | behaviour |
|---|---|
| **known-value** | consumes the next token (`--loglevel`, `--registry`, `--prefix`, `--workspace`, …) |
| **known-boolean** | skip; next token still a candidate (`--global`, `--silent`, `--save-dev`, …) |
| **known-positive** | its value **is** the package (`--package`) — already implemented |
| *unlisted bare flag* | **`("unknown", None)`** |

`--flag=value` stays self-delimiting and safe, as elsewhere.

**Table provenance is the deliverable, not the table.** Generate it with a
committed script that reads `npm config list --json`, plus an explicit,
individually-justified alias list for the handful the dump omits. Anything
neither derivable nor justified stays **unlisted** — costing auto-update for
that form, never correctness.

**The honest residual, same as #182's:** a *wrong* entry still fails open.
Classify a value-flag as boolean and its value becomes the package. Mechanical
derivation is what makes wrong entries unlikely; it does not make them
impossible, so each classification the script cannot derive must be pinned by
its own test.

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
  `npm config list --json`, emits the two sets, and **re-verifies** a committed
  table against live npm so drift is detectable later. Deliberately not a
  pytest: npm's flags are version-specific and CI has no npm.
- It must **print** the keys it could not classify (the 41 `null`-default ones
  and any alias-only flags) rather than silently omitting them, so the
  unlisted set is a reviewed decision rather than an accident.

### `tests/test_version_checker.py` (modify)

- `TestNpmValueFlagCollisions` — **add** — inequality assertions for the three
  reproduced pairs. All RED today.
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

- [ ] The three reproduced pairs satisfy `d(a) != d(b)` **or**
      `d(a) == ("unknown", None)`, with each pair's exact value pinned — RED
      today, mutation-proved per node with recorded output.
- [ ] **`npm --silent exec pkg` → `pkg`** still holds. `--silent` is absent
      from `npm config list --json` (it aliases `--loglevel=silent`), so this
      is the single most likely regression and gets its own criterion.
- [ ] `npm install i` → `i`, `npm exec exec` → `exec`, `npm exec pkg` → `pkg`
      unchanged; `npx -y exec` → `exec` unchanged.
- [ ] An unlisted bare flag yields `("unknown", None)`, never a wrong name.
- [ ] **The table is generated, not recalled**, and the generator is committed.
      It must print the keys it could not classify — the 41 `null`-default ones
      and any alias-only flags — so the unlisted set is reviewed rather than
      accidental. #182 removed ten memory-drafted entries; none may be
      reintroduced here.
- [ ] Every manifest server still resolves (98/98 at 2.5.0) — proven by a scan,
      not assumed.
- [ ] Full suite, ruff, mypy green; CHANGELOG records the fix **and** the
      auto-update cost.
- [ ] **#180's status is decided explicitly** — closed if the gate now holds
      for ordinary npm commands, or its remaining gap named. It has been
      narrowed twice; do not close it by assumption a third time.
