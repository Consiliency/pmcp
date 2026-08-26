# Detailed plan: resolve npm package identity by calling npm's own parser

## Task

Close Consiliency/pmcp#195 by replacing the hand-modelled npm flag tables in
`detect_package_type` with a call to npm's *actual* argument parser (`nopt` +
`@npmcli/config`), so package identity is exact by construction rather than
re-derived by hand.

Chosen over patching the four known-remaining spellings because three
consecutive board rounds on this parser each surfaced a *new* form (#180 → #192
→ #194 → #195). The tables themselves verify clean against nopt; every defect
has been in the rules *around* them — which literals a flag consumes, how
attached values behave, how `npx` rewrites argv before nopt ever sees it. That
is a modelling problem, and it does not converge by adding rows.

## Research summary

All of the following was measured on this host (node v24.13.0, npm 11.19.0),
not read from documentation.

**nopt is reachable and authoritative.** `require(<npm root>/npm/node_modules/nopt)`
and `.../npm/node_modules/@npmcli/config/lib/definitions` both resolve. Feeding
argv to `nopt(types, shorthands, argv, 0)` returns `argv.remain` — the exact
positional list — plus the parsed `package` option. All three #195 collision
forms resolve correctly with zero modelling:

```
["exec","--loglevel","silly","server-a"] -> remain ["exec","server-a"]
["exec","--global=zz-a","shared-bin"]    -> remain ["exec","zz-a","shared-bin"]
["exec","-n","null","A"]                 -> remain ["exec","A"]
["exec","--package=x","bin"]             -> remain ["exec","bin"], package ["x"]
```

**There is no native "resolve but do not run" npm path.** `npm exec --dry-run`
still attempts a fetch and exits non-zero; `npm/lib/commands/exec.js` exposes no
parse-only entry point. Calling nopt directly is the available primitive.

**`npx`'s divergence lives in `<npm root>/npm/bin/npx-cli.js`, not in nopt.**
Lines 9–124 are a self-contained argv pre-scan that runs *before* npm's parser:
it rewrites `-p=`/`--p=` to `--package=`, rewrites `--shell` to `--script-shell`,
rewrites `--no-install` to `--yes=false`, expands shorthands, **removes**
`--npm`/`--node-arg`/`-n` together with their values, and inserts `--` before the
first positional. Its `switches` set is *computed from the host's own
`definitions`* (every entry whose `type` is or includes `Boolean`), plus a small
literal set. It is not exported, so it cannot be required — but its data inputs
can be recomputed from the same source it uses.

**Cost is only acceptable if the child is persistent.** Measured:

| Shape | Cost |
|---|---|
| `node -e ''` bare startup | 26 ms |
| one-shot resolve, `NPM_ROOT` preset | 50 ms |
| one-shot resolve, `npm root -g` inside | 166 ms |
| **79 one-shot resolves** (the bundled manifest's npx count) | **4.02 s** |
| persistent child: startup | 43 ms |
| persistent child: **79 queries** | **39 ms total** (~0.5 ms each) |

4 s of blocking work is not acceptable: `detect_package_type` is **synchronous**
and is called from inside async coroutines at `refresher.py:259` and
`refresher.py:420`, and in a sync per-server loop at `refresher.py:499`. The
persistent-child shape costs one 43 ms spawn per process and is the only variant
that fits the existing sync signature without touching every call site.

**Blast radius.** `detect_package_type` (`version_checker.py:1475`) is imported
by `refresher.py:22` and `handlers.py:84`; `_npm_package_arg`
(`version_checker.py:1092`) is shared with `update_server`'s pin detection so the
two can never disagree. 79 of the manifest's 98 launchable servers are `npx`, so
the npm path is the dominant one — a regression here is not a corner case.

## Changes

### `src/pmcp/manifest/_npm_resolve.js` (create)

- `resolveNpmRoot()` — add — locate npm's library root from the *invoked
  command* rather than globally: `realpath` the `npx`/`npm` on `PATH` and walk to
  its `lib/node_modules`, falling back to `npm root -g`. A config that says
  `npx` must be parsed by the npm that `npx` belongs to, not by whichever npm
  happens to be global.
- `loadParser()` — add — `require` `nopt` and `@npmcli/config/lib/definitions`
  from that root; build the `types` map from `definitions[k].type`.
- `npxPreScan(argv, definitions, shorthands)` — add — faithful port of
  `npx-cli.js` lines 63–124, with `switches` recomputed from the host's
  `definitions` exactly as npx-cli does. Ported, not invented: the file is read
  verbatim and every branch is reproduced.
- `NPX_CLI_SHA256` — add — the SHA-256 of the `npx-cli.js` the port was written
  against, plus a startup check that hashes the host's copy. **A mismatch does
  not fail; it sets `"npx_cli_drift": true` on every npx response**, which the
  Python side treats as "cannot trust the npx pre-scan" and refuses identity
  rather than guessing. This is the mechanism that makes the one remaining
  hand-modelled component *fail loudly* instead of silently, which is precisely
  what the previous three rounds lacked.
- Newline-delimited JSON request/response loop over stdin/stdout — add — one
  request per line, `{"command": "npx"|"npm", "args": [...]}`; one response per
  line, `{"remain": [...], "package": [...]|null, "npx_cli_drift": bool}`.
  Argv arrives as parsed JSON, never as a shell string and never interpolated
  into a `-e` program.

### `src/pmcp/manifest/npm_resolver.py` (create)

- `NpmResolver` — add — owns the child process. Lazy spawn on first query;
  `subprocess.Popen` with an explicit argv list, `shell=False`, pipes for
  stdin/stdout, stderr captured; a `threading.Lock` around write+readline so the
  sync API is safe from the several async call sites; a per-query read timeout;
  `atexit` shutdown plus an idle reaper so a long-lived gateway does not hold a
  ~40 MB node process forever.
- `NpmResolver.resolve(command, args)` — add — returns `str | None`. Memoized on
  `(command, tuple(args))`, so a repeated refresh over the same manifest costs
  nothing after the first pass.
- `_UNAVAILABLE` sentinel — add — set on spawn failure (no node, no npm root,
  helper crash, timeout, malformed response). Once set, **no further spawn is
  attempted for the process lifetime** — a host without node must not pay 79
  failed spawns.
- Module-level singleton + `reset_for_tests()` — add — so tests can force both
  the available and unavailable branches deterministically.

### `src/pmcp/manifest/version_checker.py` (modify)

- `_npm_package_arg` (`:1092`) — modify — try `NpmResolver.resolve` first. On a
  usable answer, return it. On `None`/unavailable, **fall through to the existing
  table scan unchanged**. The tables stay as the fallback path, not as dead code:
  they are the behaviour on a node-less host, and that behaviour is exactly what
  ships in 2.5.1 today, so the worst case is no regression.
- Module docstring for the flag tables (`:380`–`:430`) — modify — restate them as
  the *fallback* classification, with a pointer to the resolver as the primary.
  The existing text presents them as authoritative; leaving that would mislead
  the next reader into extending the wrong thing.
- `_NPM_POSITIVE_FLAGS` (`:378`), `_NPM_VALUE_FLAGS` (`:430`),
  `_NPM_BOOLEAN_FLAGS` (`:554`), `_NPM_BOOLEAN_LITERALS` (`:762`) — unchanged.
  Deliberate: removing them would delete the node-less fallback, and the
  regression suite that pins them is the only cross-check the resolver has.

### `pyproject.toml` (modify)

- package-data / `force-include` for `_npm_resolve.js` — add — the helper must
  ship in the wheel. **`install-smoke` must prove this**: a `pip install` of the
  built wheel followed by an import-and-resolve. A missing data file is exactly
  the shipped-broken class this repo has hit twice before.

### `tests/test_npm_resolver.py` (create)

- `TestResolverContract` — add — the child protocol: malformed line, timeout,
  crashed child, absent node. Each must degrade to the table, never raise.
- `TestUnavailableIsSticky` — add — assert exactly one spawn attempt across N
  queries after a failure.
- `TestDriftRefuses` — add — with `npx_cli_drift: true`, an `npx` argv resolves
  to `None` (refuse), while an `npm` argv still resolves (the drift only affects
  the pre-scan).

### `tests/test_version_checker.py` (modify)

- `TestNpmIdentityViaNopt` — add — the four #195 forms plus the five original
  #180 collisions, asserted through `detect_package_type` with the resolver
  active.
- Existing npm table tests — modify — force the resolver unavailable via the
  fixture so they keep testing the fallback they were written for. **Not
  deleting them is the point**: they are the differential baseline.

### `.consiliency/notes/differential_npm_corpus.py` (create)

- Corpus runner — add — generates argv forms (every flag class × attached/spaced
  × shorthand/abbreviation × npm/npx) and compares three answers: the resolver,
  the fallback table, and **the real binary**. Prints disagreements. This is the
  check that would have caught all four #195 forms; the previous rounds compared
  against type strings and passed.

## Documentation impact

- `CHANGELOG.md` — add — one `### Fixed` entry under `[Unreleased]`: identity for
  npm-family commands now comes from npm's own parser; states the node-less
  fallback plainly and closes the #195 gap note added in 2.5.1.
- `README.md` — modify — the version-check/auto-update section gains one
  sentence: npm identity resolution uses the host's own npm parser when node is
  available. Users need to know a node-less host has reduced auto-update
  coverage.

## Dependencies & order

1. `_npm_resolve.js` first — it defines the wire contract everything else codes
   against.
2. `npm_resolver.py` second, against that contract.
3. `version_checker.py` integration third; the fallback must be wired before any
   existing test is touched.
4. Packaging + `install-smoke` fourth. **Before** the differential corpus, so the
   corpus runs against a shape that actually ships.
5. Differential corpus last — it is the acceptance evidence, not a development
   aid.

No blocking external dependency. Node is *not* added as a hard requirement: the
fallback covers its absence, and a config whose command is `npx` implies node on
that host by definition, so the fallback is close to unreachable in practice for
the configs that matter.

## Verification

```bash
# Full suite, both resolver branches
uv run pytest -q
uv run pytest -q tests/test_npm_resolver.py tests/test_version_checker.py

# The node-less path must be green with node hidden
env PATH=/usr/bin:/bin uv run pytest -q tests/test_version_checker.py -k npm

# Differential corpus against the real binary
uv run python .consiliency/notes/differential_npm_corpus.py --against-binary

# Packaging: the helper must survive a wheel install
uv build && python -m venv /tmp/vw && /tmp/vw/bin/pip install -q dist/pmcp-*.whl \
  && /tmp/vw/bin/python -c "from pmcp.manifest.npm_resolver import get_resolver; \
     print(get_resolver().resolve('npx', ['-y','@modelcontextprotocol/server-github']))"

# No manifest regression
uv run python -c "…resolve all 98 manifest entries, assert 0 unknown…"

uv run ruff check . && uv run ruff format --check . && uv run mypy src/
```

Edge cases to exercise explicitly: node absent; node present but npm absent;
`npx` on PATH from a different prefix than `npm root -g`; a child that hangs;
a child killed mid-session; `npx_cli_drift`; 200 concurrent resolves from
threads.

## Acceptance criteria

- [ ] All four #195 forms resolve to the same package the real binary runs,
      proven by `differential_npm_corpus.py --against-binary` reporting zero
      disagreements over the generated corpus — not by a hand-written table test.
- [ ] With node removed from `PATH`, the full npm test set passes and
      `detect_package_type` returns exactly what 2.5.1 returns for all 98
      manifest entries — proven by a recorded before/after diff of the 98 pairs.
- [ ] A single spawn attempt occurs across ≥50 resolves on a node-less host, and
      ≤1 spawn across ≥50 resolves on a healthy host — proven by a spawn counter
      in `TestUnavailableIsSticky`.
- [ ] `npx_cli_drift` causes `npx` identity to be refused rather than guessed,
      proven by `TestDriftRefuses`.
- [ ] The helper `.js` is present and functional after installing the built
      wheel into a clean venv, proven by the `install-smoke` command above.

## Execution Policy

- execute: effort=high, reason=subprocess lifecycle plus a parser port on the
  path that mints security-relevant package identity
