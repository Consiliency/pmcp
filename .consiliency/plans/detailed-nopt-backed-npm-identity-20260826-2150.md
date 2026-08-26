# Detailed plan: resolve npm package identity by calling npm's own parser

> **Revision 2 (2026-08-26, post-board).** Four seats reviewed revision 1; three
> found blocking defects, and two of them independently found the same one. This
> revision is the amended plan. Revision 1's errors are recorded inline as
> `WAS WRONG` notes rather than deleted — they are the reason several rules below
> exist, and removing them invites their reintroduction.

## Task

Close Consiliency/pmcp#195 by deriving npm package identity from npm's *actual*
argument parser (`nopt` + `@npmcli/config`) instead of hand-modelled flag tables.

Chosen over patching the four known-remaining spellings because three consecutive
board rounds each surfaced a *new* form (#180 → #192 → #194 → #195), and a fourth
round has now surfaced a fifth (the six nullable spellings hotfixed in 2.5.2).
The tables verify clean against nopt; every defect has been in the rules *around*
them. That does not converge by adding rows.

**WAS WRONG (rev 1): "exact by construction."** Verified false. Package identity
is not a pure function of argv — `npm_config_package=env-pkg npx plainbin` really
runs `env-pkg`. See "Identity inputs" below. The claim is dropped; the goal is
*exact given a stated input set, and refusal outside it*.

## Research summary

All measured on this host (node v24.13.0, npm 11.19.0) or by a board seat that
ran the real binary against a dead registry (`npm_config_registry=http://127.0.0.1:9`,
so the fetch URL in the error names exactly which package npm resolved).

**nopt is authoritative and reachable.** `nopt(types, shorthands, argv, 0).argv.remain`
returns the positional list; `types[k] = definitions[k].type` matches npm's own
`getTypesFromDefinitions` (`@npmcli/config/lib/index.js:1017`) exactly. npm
additionally installs `invalidHandler`/`unknownHandler`/`abbrevHandler`, all
verified warning-only — they cannot change `remain`. A raw nopt call is
outcome-equivalent to npm's parse.

**Real-binary differentials confirm the pipeline**, including the npm/npx split
that no single rule can model: `npx --y null probe-y` fetches the literal package
`null`, while `npm exec --y null probe-npmy` fetches `probe-npmy`. Also
`npx --frobnicate valpkg realbin` fetches **valpkg** — an unknown flag does *not*
consume its value. The shipped fail-closed tables over-refuse this class; the
resolver is strictly more accurate on it.

**Persistent child is the only viable shape.** 79 one-shot resolves = 4.02 s of
blocking work; persistent = 43 ms startup + 39 ms for all 79 (~0.5 ms each).
`detect_package_type` is **synchronous** and called from inside async coroutines
(`refresher.py:259`, `:420`) and a sync loop (`:499`), so 4 s is disqualifying.

**Blast radius.** `detect_package_type` (`version_checker.py:1475`) is imported by
`refresher.py:22` and `handlers.py:84`; `_npm_package_arg` (`version_checker.py:1092`)
is shared with `update_server`'s pin detection so the two can never disagree.
79 of 98 launchable manifest servers are `npx`.

## The three rules this plan exists to not break

Board findings, each verified. These are stated first because revision 1 broke
two of them.

### R1 — Three outcomes, never two

**WAS WRONG (rev 1):** `resolve()` returned `str | None`, and `_npm_package_arg`
fell back to the tables on *any* `None`. Two seats independently showed this is
self-contradicting: the plan demands drift *refuse* identity, but a refusal
returning `None` then hits the table scan, which maps both
`npx --global=zz-a shared-bin` and the `zz-b` form to `shared-bin` — the exact
collision the phase exists to close. Fail-open, reintroduced on the primary path.

| Outcome | Meaning | `_npm_package_arg` does |
|---|---|---|
| `Identity(spec)` | nopt named a package | return `spec` **raw**, tag intact |
| `REFUSED` | helper ran; no recoverable identity, or an untrustworthy input | return `None` — **never scan the tables** |
| `UNAVAILABLE` | no node, spawn failed, sticky-unavailable | fall through to the tables |

A sentinel type, not `None`. `REFUSED` and `UNAVAILABLE` must be
distinguishable at the type level so a future edit cannot silently merge them.

### R2 — nopt resolves flags, not npm semantics

**WAS WRONG (rev 1):** the `remain`+`package` → identity mapping was left
unspecified. A naive "skip the first token" reopens two closed issues:

- `npm run mcp` → remain `["run","mcp"]` → `mcp` → `update_server` builds
  `npx -y mcp@latest --help` (`handlers.py:~5069`) and **installs and executes a
  package named after a script**. That is #183, closed nine hours ago by the
  subcommand allowlist at `version_checker.py:77`. Same for `npm start`,
  `npm test`, `npm create foo` (npm resolves that to `create-foo`, a *different*
  package), and typos like `npm rum mcp`.
- `npm exec --package=x bin` → remain `["exec","bin"]`, package `["x"]`. Taking
  remain yields `bin` and reopens #182.

The mapping is specified once, here, and pinned by tests on the resolver-active
path:

1. `package` non-empty and all entries identical → that spec.
2. `package` has two or more *distinct* specs → `REFUSED`. Verified: `npx
   --package=aa --package=bb somebin` installs both; the union is the tool
   surface, so no single name is the identity.
3. `call` present → `REFUSED`. Verified: `npx --package=p --call='echo hi'` runs
   the shell string, not a binary from `p`; two servers sharing `p` with
   different `--call` have different tool surfaces.
4. Else `command == "npm"`: first remain token must be in
   `_NPM_SUBCOMMANDS_WITH_A_PACKAGE_OPERAND`; otherwise `REFUSED`. Identity is
   the next remain token, or `REFUSED` if absent.
5. Else `command == "npx"`: identity is `remain[0]` after the pre-scan.
6. Return the **raw** spec including any `@tag`/`@version`. `_strip_npm_tag`
   stays in `detect_package_type`. A stripped return makes a pinned server look
   unpinned to `handlers.py:306-330` and eligible to be moved to `@latest`.

### R3 — Existing regression tests run on BOTH paths

**WAS WRONG (rev 1):** "force the resolver unavailable via the fixture so
[existing npm table tests] keep testing the fallback they were written for."
On a node-ful host the resolver is the *primary* path, so the whole #182/#183
regression set would have stopped covering production. The suite would be green
while `npm run mcp` resolved to a package again.

Every existing npm identity test is **parametrized over both paths**
(resolver-active and resolver-unavailable) rather than pinned to one. At minimum
these must be asserted with the resolver **active**: `npm run mcp`, `npm start`,
`npm test`, `npm -y server-pkg` (no subcommand) → `("unknown", None)`;
`npm exec --package=pkg -- bin` → `pkg`; `npm exec pkg@1.2.3` → raw `pkg@1.2.3`.

## Identity inputs, and what is excluded

**Verified:** `npm_config_package=env-pkg-xq npx plainbin-xq` fetches `env-pkg-xq`.
Two servers with identical argv and different env run different packages. pmcp
ships per-server env (`extra_env` #108, `server_env` #109), so this is part of
the config model, not hypothetical.

The resolve request therefore carries the server's effective identity-bearing
npm env — `npm_config_package`, `npm_config_registry` — alongside argv. When any
`npm_config_*` key that can change resolution is present and not modelled,
return `REFUSED`. Naming the exclusion is mandatory: a corpus generated as
"every flag class × attached/spaced × shorthand" cannot generate this case, so
it would never have been caught by the acceptance suite.

## Changes

### `src/pmcp/manifest/_npm_resolve.js` (create)

- `resolveNpmRoot()` — add — realpath the invoked `npx`/`npm` on `PATH` and walk
  to its `lib/node_modules`; fall back to `npm root -g`. Verified correct on this
  host, which has two npm trees (`~/.npm-global` 11.19.0 and `/usr/lib` 11.6.2) —
  the walk picks the one PATH's `npx` belongs to. For volta/asdf shims the walk
  fails and the fallback is right. Note `detect_package_type` only enters the npm
  branch for bare `npx`/`npm`, which bounds this.
- `loadParser()` — add — require `nopt` and `@npmcli/config/lib/definitions` from
  that root; `types[k] = definitions[k].type`.
- `npxPreScan(argv, definitions, shorthands)` — add — faithful port of
  `npx-cli.js` lines 63–124, `switches` recomputed from the host's `definitions`
  exactly as npx-cli does.
- **Per-request `try`/`catch` returning an error response** — add — mandatory,
  not defensive style. Verified: `--__proto__=evil` and `--constructor` *throw
  inside nopt* (`shorthands[arg].split is not a function` — Object.prototype
  members leak into the shorthand lookup). Without containment, one poisoned
  `.mcp.json` kills the child and, via sticky-unavailable, disables the resolver
  for the whole process. A contained child answers the next query correctly —
  verified.
- Report the resolved **npm version** in every response — add. See the drift
  section: this, not the file hash, is the real guard.
- NDJSON request/response over stdin/stdout. Argv arrives as parsed JSON, never
  interpolated into a shell or a `-e` program.

### Drift detection — replaced, not kept

**WAS WRONG (rev 1):** a SHA-256 pin on `npx-cli.js` described as "the mechanism
that makes the one remaining hand-modelled component fail loudly." Verified
false twice over. `npx-cli.js` is **byte-identical from npm 10.8.2 through
11.19.0** — across a major bump the pin never fires. And there are *two*
hand-modelled components: the pre-scan port and the `remain`→identity mapping in
R2, which re-implements `lib/commands/exec.js` semantics and which the hash
guards not at all.

- Keep the `npx-cli.js` hash as a cheap tripwire (it costs nothing) but **do not
  present it as the guard**.
- **The guard is an npm-major-version ceiling.** The child reports its npm
  version; Python refuses identity (R1 `REFUSED`, which V7-style analysis
  confirms is safe — `_same_package` at `refresher.py:199` returns False on an
  unknown side, so refusal forces a refresh and the coarse
  `command + args` identity, never a collision) when the major exceeds the
  version the differential corpus was last run against. npm's own
  "will stop working in the next major version" warnings announce exactly the
  parse-adjacent changes that land outside `npx-cli.js`.

### `src/pmcp/manifest/npm_resolver.py` (create)

- `NpmResolver` — add — lazy spawn; `subprocess.Popen` with an explicit argv
  list, `shell=False`; a `threading.Lock` around write+read (adequate for the
  sync loop at `refresher.py:499` and the coroutine callers; no reentrancy path
  exists since the memo lookup does not recurse).
- **Read timeout: 1.0 s, implemented with a reader thread or `select`**, not a
  blocking `readline`. Revision 1 named neither the value nor the mechanism; a
  blocking pipe read cannot implement a timeout, so "a per-query read timeout"
  was unimplementable as written.
- On timeout or parse error: terminate the child and mark unavailable **before
  releasing the lock**, so a later query cannot read an orphaned response off the
  pipe and attribute it to the wrong request.
- `resolve(command, args, env)` — add — returns the R1 tri-state. Memoized on
  `(command, tuple(args), relevant_env)`.
- **No idle reaper.** Revision 1 had one *and* sticky-unavailable; a reaper kill
  is indistinguishable from a crash at the pipe, so the feature would die after
  the first idle period. Either the child lives for the process (chosen: ~40 MB,
  and it is spawned only if an npm-family server exists at all) or a reaper must
  set "respawn on next use" — a distinct state from sticky-unavailable. Sticky
  is reserved for *spawn* failure, which is the case it exists for.
- Module-level singleton + `reset_for_tests()`.

### `src/pmcp/manifest/version_checker.py` (modify)

- `_npm_package_arg` (`:1092`) — modify — R1 tri-state dispatch. `UNAVAILABLE`
  alone falls through to the table scan.
- Flag-table docstrings (`:380`–`:430`) — modify — restate as the *fallback*
  classification.
- The four flag tables — unchanged. They are the node-less path and the
  differential baseline.

### `pyproject.toml` (modify)

- package-data / `force-include` for `_npm_resolve.js` — add. `install-smoke`
  must prove it survives a wheel install; a missing data file is the
  shipped-broken class this repo has hit twice.

### Tests

- `tests/test_npm_resolver.py` (create) — child protocol (malformed line,
  timeout, crashed child, absent node, `--__proto__` poisoning); sticky-spawn
  counter; tri-state distinctness.
- `tests/test_version_checker.py` (modify) — parametrize the existing npm
  identity suite over both paths per R3; add the R2 mapping cases and the four
  #195 forms on the **resolver-active** path.

### `.consiliency/notes/differential_npm_corpus.py` (create)

Compares resolver vs fallback table vs **the real binary**. Required corpus
content — an enumeration, because "generated forms" is what let #192/#194/#195
each survive their predecessor's tests:

- every flag class × attached/spaced × shorthand/abbreviation × npm/npx;
- **every historical form** from #180, #182, #183, #192, #194, #195, 2.5.2;
- the R2 mapping cases (subcommands, multi-`--package`, `--call`);
- the env cases from "Identity inputs";
- a randomized fuzz component with a fixed seed.

## Documentation impact

- `CHANGELOG.md` — add — `### Fixed`: npm identity now comes from npm's own
  parser; states the node-less fallback and the refusal cases plainly.
- `README.md` — modify — one sentence in the version-check section: npm identity
  uses the host's own npm parser when node is available, and auto-update
  coverage is reduced without it.

## Dependencies & order

1. `_npm_resolve.js` — defines the wire contract.
2. `npm_resolver.py` — tri-state + lifecycle.
3. `version_checker.py` integration — fallback wired before any existing test is
   touched.
4. Test parametrization (R3) — **before** the corpus, so the regression set is
   already covering both paths when the corpus runs.
5. Packaging + `install-smoke`.
6. Differential corpus — acceptance evidence, not a development aid.

Node is not a hard requirement; a config whose command is `npx` implies node on
that host, so the fallback is near-unreachable for the configs that matter.

## Verification

```bash
uv run pytest -q                       # both paths, parametrized
env PATH=/usr/bin:/bin uv run pytest -q tests/test_version_checker.py -k npm
uv run python .consiliency/notes/differential_npm_corpus.py --against-binary
uv build && python -m venv /tmp/vw && /tmp/vw/bin/pip install -q dist/pmcp-*.whl \
  && /tmp/vw/bin/python -c "from pmcp.manifest.npm_resolver import get_resolver; ..."
uv run ruff check . && uv run ruff format --check . && uv run mypy src/
```

Edge cases: node absent; node present, npm absent; `npx` from a different prefix
than `npm root -g`; a hung child; a child killed mid-session; `--__proto__=evil`;
npm major above the tested ceiling; 200 concurrent resolves from threads.

## Acceptance criteria

- [ ] Zero disagreements against the real binary over the differential corpus,
      **where the corpus provably contains every historical form** (#180, #182,
      #183, #192, #194, #195, 2.5.2), the R2 mapping cases, and the env cases —
      asserted by a test that fails if any named form is absent from the
      generated set. A corpus that omits the failing form is how the last three
      fixes each passed their own suite.
- [ ] With the resolver **active**, `npm run mcp`, `npm start`, `npm test` and
      `npm -y server-pkg` return `("unknown", None)`, and
      `npm exec --package=pkg -- bin` returns `pkg` — proven on the
      resolver-active parametrization, not the fallback fixture.
- [ ] `REFUSED` and `UNAVAILABLE` are distinguishable at the type level, and a
      `REFUSED` result never reaches the table scan — proven by a test that makes
      the tables return a *known-wrong* answer and asserts `detect_package_type`
      still returns `None`.
- [ ] npx-cli drift refuses **end-to-end**: the resolver is pointed at a fixture
      npm root whose `npx-cli.js` differs by one byte, and `detect_package_type`
      refuses. Injecting `npx_cli_drift: true` into a response does not count —
      that passes even if the hash is never computed.
- [ ] With node removed from `PATH`, all 98 manifest entries resolve exactly as
      2.5.2 does — proven by a recorded before/after diff of the 98 pairs.
- [ ] One spawn attempt across ≥50 resolves on a node-less host; ≤1 across ≥50 on
      a healthy host; a hung child stalls the caller at most once, bounded by the
      1.0 s timeout.
- [ ] The helper `.js` works after installing the built wheel into a clean venv.

## Execution Policy

- execute: effort=high, reason=subprocess lifecycle plus a semantics port on the
  path that mints security-relevant package identity
