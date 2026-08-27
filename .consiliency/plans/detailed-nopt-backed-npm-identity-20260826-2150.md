# Detailed plan: name an npm package only when npm's own parser makes it certain

> **Revision 3 (2026-08-27).** Rev 1 and rev 2 were both boarded; each round found
> new blocking defects, and the surface kept growing (workspace bin resolution,
> `.npmrc`, per-server `cwd`, `libnpmexec` skew *within* npm 11.x). The operator
> chose to **narrow the scope and fail closed** rather than keep modelling npm.
> Rev 1/rev 2 errors are kept inline as `WAS WRONG` — they are why these rules
> exist.

## Task

Close Consiliency/pmcp#195. Replace the hand-modelled npm flag tables with a
narrow, fail-closed resolver that uses npm's own parser (`nopt` +
`@npmcli/config`) to answer exactly one question:

> **Can we name this server's package with certainty?**

If yes, return it. If anything at all is unusual, **refuse**. Refusal is safe and
cheap: `_same_package` (`refresher.py:199`) returns False on an unknown side, so
a refusal forces a refresh and falls back to the coarse `command + args`
identity, which is unique per config and never collides. It does not block a
server from launching.

**Why narrow.** Five defects in this parser (#180 → #192 → #194 → #195 → the
2.5.2 nullable-spelling fix) were all in the rules *around* the tables. Three
board rounds on rev 1/rev 2 each found a further way a *confident* answer is
wrong. Chasing full fidelity means re-implementing npm's resolution order —
workspace/local/global bin search, three levels of `.npmrc`, cwd, `libnpmexec`
version skew — which is the same hand-modelling at larger scale.

**Measured cost of narrowing: essentially zero.** Of the manifest's 79 npm-family
servers, **78 use the plain `npx -y <pkg>` shape**. None sets `cwd`, none sets
`npm_config_*`, none uses `--call`, multiple `--package`, or workspace flags.

## Research summary

Measured on this host (node v24.13.0, npm 11.19.0) or by board seats running the
real binary against a dead registry (`npm_config_registry=http://127.0.0.1:9`, so
the fetch URL in npm's error names the resolved package without installing).

**nopt is authoritative.** `types[k] = definitions[k].type` matches npm's own
`getTypesFromDefinitions` (`@npmcli/config/lib/index.js:1017`); npm's
`invalidHandler`/`unknownHandler`/`abbrevHandler` are all warning-only and cannot
change `remain`. Two nopt majors (8.1.0, 9.0.0) parse the whole edge set
identically.

**The resolver beats the shipped tables where they over-refuse:**
`npx --pack zz bin` → `zz`, `npx --yes=maybe tok bin2` → `maybe`,
`npx --frobnicate valpkg realbin` → `valpkg` (an unknown flag does *not* consume
its value). All confirmed against the real binary.

**Persistent child is the only viable shape.** 79 one-shot resolves = 4.02 s;
persistent = 43 ms startup + ~0.5 ms/query. `detect_package_type` is
**synchronous** and called from inside async coroutines (`refresher.py:259`,
`:420`) and a sync loop (`:499`).

## Findings that shaped this revision

Each was verified by a board seat running it.

**F1 — `remain[0]` is `"exec"`, not the package.** `npx-cli.js:7` does
`process.argv.splice(2, 0, 'exec')` and the pre-scan loop starts at `i = 3` *on
that basis*. A faithful port yields `remain = ["exec", "probe-a"]` for
`npx -y probe-a`. **`exec` is a real published package** (registry returns HTTP
200), so rev 2's rule would have made `update_server` probe
`npx -y exec@latest --help` for all 79 npx servers — the #183 class, at fleet
scale.

**F2 — an invalid spec becomes the package `undefined`.** `npm exec -- --flag-thing`
and `npx --package="" somebin` both fetch `/undefined` from the real binary, and
**`undefined` exists on the registry**. Rev 2's rules 1 and 4 would mint
`--flag-thing` and `""` respectively — the latter collapsing every such server
onto one degenerate key.

**F3 — `.npmrc` is an identity input the resolver never sees.** A server `cwd`
containing a project root with `.npmrc` setting `package=rcfile-pkg` makes
`npx plainbin` fetch `rcfile-pkg`. User and global `.npmrc` apply regardless of
cwd. Rev 2's env rule gives zero protection against this.

**F4 — the npm-major ceiling guards at the wrong granularity.**
`npx --name foo probe-a` fetches `probe-a` on npm 11.19.0 but `foo` on npm
11.6.2 — 22 config definitions were added *within* major 11, several
value-consuming, and nopt itself went 8.1.0 → 9.0.0 inside an npm minor.
`npx-cli.js` is hash-identical across both, confirming rev 2's own point that the
hash never fires.

**F5 — rev 2's acceptance criterion 3 was satisfiable by a broken
implementation.** A seat wrote a `_npm_package_arg` whose REFUSED path calls the
table scan through a def-time import binding, plus the test exactly as worded —
**it passed.** Two stacked holes: monkeypatching the module attribute misses the
def-time binding, and the chosen refusal case is one the tables *also* refuse, so
`is None` held while the table scan demonstrably ran.

**F6 — `--call` was unreachable.** Rev 2 ordered `--package` (rule 1) before
`--call` (rule 3), so `npx --package=p --call='echo hi'` returned `p` and never
reached the refusal. npm's own documented shape.

**F7 — `dlx` is not an npm subcommand.** `npm dlx probe` → `Unknown command`. It
sits in the allowlist at `version_checker.py:78` (it is pnpm/yarn), so the
resolver would mint identity for a server that can never launch.

## The resolution contract

### Step 1 — Gate. Refuse before parsing if any of these hold

Cheap, purely local, and each closes a class the board proved is real:

| Condition | Why |
|---|---|
| command is not bare `npx`/`npm` | a full path never reaches this branch today |
| any `npm_config_*` key in effective env, **case-insensitive** | npm matches `/^npm_config_/i`; `NPM_CONFIG_PACKAGE` verified to win over argv |
| the server sets `cwd` | project `.npmrc` and workspace/local bin resolution both key off it (F3) |
| `--call` / `-c` present with a non-empty value | the tool surface is the shell string, not a package (F6); the definition default is `''`, so test the value, not the key |
| two or more *distinct* `--package` values | npm installs both; the union is the tool surface |
| any workspace flag (`--workspace`, `-w`, `--workspaces`, `-ws`) | selects a bin from the workspace, not the registry |
| the child's spawn-time self-test failed | see "Drift" |

### Step 2 — Parse

Build argv as npx itself does — `["exec", *args]` — run the pre-scan for `npx`
only, then `nopt`. Identity is the first `remain` token **after the leading
`"exec"`**; if absent, refuse (F1).

For `npm`, the first remain token must be in
`_NPM_SUBCOMMANDS_WITH_A_PACKAGE_OPERAND` **minus `dlx`** (F7); otherwise refuse.
This preserves #183: `npm run mcp`, `npm start`, `npm test`, `npm create foo`,
`npm rum mcp` and bare `npm -y pkg` all refuse.

A single `--package` value outranks the positional (this is #182).

### Step 3 — Validate

Run the candidate through the host's own `npm-package-arg`. Empty spec, or no
valid name, → refuse (F2).

Return the spec **raw**, tag intact — `_strip_npm_tag` stays in
`detect_package_type`, because `handlers.py:306-330` needs the suffix to tell a
pinned server from an unpinned one.

### Step 4 — Tri-state, and what falls back

| Outcome | Meaning | `_npm_package_arg` does |
|---|---|---|
| `Identity(spec)` | steps 1–3 all passed | return `spec` raw |
| `REFUSED` | any gate tripped, or an in-flight request timed out / the child died / the response failed schema | return `None` — **never scan the tables** |
| `UNAVAILABLE` | **spawn** failed: no node, no npm root | fall through to the tables |

**WAS WRONG (rev 1):** `str | None` with fallback on any `None`. That
contradicted the plan's own refusal rule — a refusal hit the table scan and
reminted the collision.

**WAS WRONG (rev 2):** it said both "on timeout or parse error, mark unavailable"
and "sticky is reserved for spawn failure." Resolved: an input-caused child death
(OOM/abort — the in-child `try`/`catch` cannot contain those, and real npx dies
at `npx-cli.js:89` on `--__proto__=evil`) must classify as `REFUSED` for the
in-flight request, with the *child* moving to respawn-once. Sticky
`UNAVAILABLE` is only ever set by a failed spawn.

## Drift — behavioural, not version-based

**WAS WRONG (rev 1):** SHA-256 pin on `npx-cli.js`. Byte-identical 10.8.2 →
11.19.0; never fires.
**WAS WRONG (rev 2):** npm-major ceiling. F4 proves parse behaviour changes
within a major.

The child runs a small **invariant corpus at spawn** — the step-2/3 cases —
against the host's own nopt and definitions. Any failure ⇒ the resolver reports
`UNAVAILABLE`-with-reason for its whole lifetime **and logs once at WARNING**, so
a future npm that changes resolution degrades loudly to the tables instead of
answering wrongly. The `npx-cli.js` hash stays as a free tripwire but is
documented as *not* the guard.

The child also re-stats the npm root's `package.json` version per resolve and
respawns on change, so an in-place npm upgrade cannot leave a require-cached
parser answering with stale definitions.

## Changes

### `src/pmcp/manifest/_npm_resolve.js` (create)

- `resolveNpmRoot()` — realpath the invoked `npx`/`npm` on `PATH`, walk to
  `lib/node_modules`; fall back to `npm root -g`. Verified correct on a host with
  two npm trees.
- `loadParser()` — require `nopt`, `@npmcli/config/lib/definitions`, and
  `npm-package-arg` from that root.
- `npxPreScan(argv, definitions, shorthands)` — port of `npx-cli.js` 63–124 with
  `switches` recomputed from the host's `definitions`, applied to
  `["exec", *args]` with the same index basis (F1).
- `selfTest()` — the invariant corpus, run once at spawn.
- **Per-request `try`/`catch`** — mandatory. `--__proto__=evil` and
  `--constructor` throw inside nopt (Object.prototype leaking into the shorthand
  lookup); a contained child answers the next query correctly, verified.
- Exit on stdin `end`, so a parent crash cannot orphan the child.
- NDJSON over stdin/stdout. Argv arrives as parsed JSON — never a shell string,
  never interpolated into `-e`.

### `src/pmcp/manifest/npm_resolver.py` (create)

- `NpmResolver` — lazy spawn, `Popen` with an explicit argv list and
  `shell=False`, a `threading.Lock` around write+read, **1.0 s timeout via a
  reader thread or `select`** (a blocking `readline` cannot implement one —
  rev 2 named neither value nor mechanism). On timeout/death: terminate and mark
  respawn-once **before releasing the lock**, so a later query cannot read an
  orphaned response and attribute it to the wrong request.
- `resolve(command, args, env, cwd)` — returns the tri-state. Memoized on
  `(command, args, relevant_env, cwd)`.
- No idle reaper (rev 1 had one *and* sticky-unavailable, so the feature died
  after the first idle period).

### `src/pmcp/manifest/version_checker.py` (modify)

- `detect_package_type(command, args, env=..., cwd=...)` and
  `_npm_package_arg(args, command, env, cwd)` — **modify the signatures.**
  **WAS WRONG (rev 2):** env was declared an identity input but no production
  signature carried it, so `resolve(..., env)` could not implement the exclusion
  and every caller would have passed nothing. Two seats caught it independently.
  Env/cwd are **required** parameters, not defaulted — a defaulted `None` is the
  same silent fail-open this plan exists to remove, and making them required
  turns every unconverted call site into a type error rather than a wrong answer.
- Flag-table docstrings — restate as the node-less fallback.
- The four flag tables — unchanged.

### Call sites (modify)

`refresher.py:259`, `:420`, `:499`; `handlers.py:~5048` (`update_server`) and
`:315` (pin detection); `version_checker.py:1654` (`get_package_version`). Each
already has the server config in hand; each must pass its effective env
(`sanitized_subprocess_env` overlay) and `cwd`.

### `pyproject.toml` (modify)

package-data / `force-include` for `_npm_resolve.js`; `install-smoke` proves it
survives a wheel install.

### Tests

- `tests/test_npm_resolver.py` — child protocol; each gate in step 1; tri-state
  distinctness; sticky-vs-respawn; `--__proto__` containment; self-test failure.
- `tests/test_version_checker.py` — **parametrize the existing npm identity suite
  over both paths.** **WAS WRONG (rev 2):** it moved them onto the fallback
  fixture, so on any node-ful host the #182/#183 regression set would have
  stopped covering production.

### `.consiliency/notes/differential_npm_corpus.py` (create)

Compares resolver vs fallback tables vs **the real binary**. Must contain, by
name: every historical form (#180, #182, #183, #192, #194, #195, 2.5.2); every
step-1 gate; F1's `npx -y pkg`; F2's `npm exec -- --flag-thing` and
`--package=""`; F6's `--package` + `--call`; F7's `npm dlx x`; `npx --` and
`npx -y` with no operand; scoped names; plus a fixed-seed fuzz component.

## Documentation impact

- `CHANGELOG.md` — `### Fixed`: npm identity now comes from npm's own parser
  where it is certain, and is **refused** otherwise; states the refusal gates and
  the node-less fallback plainly, and that refusal reduces auto-update coverage
  for unusual configs rather than guessing.
- `README.md` — one sentence in the version-check section.

## Dependencies & order

1. `_npm_resolve.js` (wire contract). 2. `npm_resolver.py`. 3. Signature change +
call sites — **before** any test edit, so an unconverted caller fails to compile.
4. Test parametrization. 5. Packaging + `install-smoke`. 6. Differential corpus.

## Verification

```bash
uv run pytest -q
env PATH=/usr/bin:/bin uv run pytest -q tests/test_version_checker.py -k npm
uv run python .consiliency/notes/differential_npm_corpus.py --against-binary
uv build && python -m venv /tmp/vw && /tmp/vw/bin/pip install -q dist/pmcp-*.whl && \
  /tmp/vw/bin/python -c "from pmcp.manifest.npm_resolver import get_resolver; ..."
uv run ruff check . && uv run ruff format --check . && uv run mypy src/
```

## Acceptance criteria

- [ ] **All 79 manifest npm servers resolve to the same package the real binary
      runs**, proven by `differential_npm_corpus.py --against-binary` reporting
      zero disagreements — and the run must show a non-zero resolved count, so a
      refuse-everything implementation cannot pass. (F1 would have made all 79
      resolve to `exec`; this is the criterion that catches it.)
- [ ] Every named corpus form yields a **recorded three-way verdict**
      (resolver / tables / binary), with "binary made no fetch" and "binary
      crashed" as first-class expected outcomes. Membership in the corpus is not
      enough — rev 2's wording passed if a form was present but never compared.
- [ ] **A `REFUSED` result never reaches the table scan**, proven by a **call
      counter on the table-scan entry point asserting zero invocations**, for at
      least one input per step-1 gate, each chosen so the un-poisoned tables
      return a *non-None wrong* answer. Rev 2's monkeypatch wording was shown to
      pass against an implementation where the scan demonstrably ran (F5).
- [ ] With the resolver active, `npm run mcp`, `npm start`, `npm test`,
      `npm -y server-pkg` and `npm dlx x` return `("unknown", None)`; `npm exec
      --package=pkg -- bin` returns `pkg`; `npm exec pkg@1.2.3` returns raw
      `pkg@1.2.3`.
- [ ] Passing an unconverted call site (no env/cwd) is a **type error**, not a
      silent pass — proven by `mypy` failing on a deliberately reverted caller.
- [ ] A failing spawn-time self-test degrades to the tables **and logs once at
      WARNING** — proven end-to-end against a fixture npm root, not by injecting
      a flag into a response.
- [ ] With node removed from `PATH`, all 98 manifest entries resolve exactly as
      2.5.2 does — recorded before/after diff of the 98 pairs.
- [ ] One spawn attempt across ≥50 resolves on a node-less host; a hung child
      stalls a caller at most once, bounded by the 1.0 s timeout.
- [ ] The helper `.js` works after installing the built wheel into a clean venv.

## Non-goals, named rather than implied

- Workspace / local / global **bin** resolution (`libnpmexec` searches these
  before the registry). Refused via the workspace-flag and `cwd` gates.
- `.npmrc` at project, user, or global level. The project case is refused via the
  `cwd` gate; **user and global `.npmrc` remain unguarded** — a `package=` or
  `registry=` line there changes resolution and the resolver cannot see it. This
  is the one known hole left open, and it is stated here rather than discovered
  later.
- Auto-update for any refused config. It degrades to coarse identity; it does not
  break.

## Execution Policy

- execute: effort=high, reason=subprocess lifecycle and a signature change across
  six call sites on the path that mints security-relevant package identity
