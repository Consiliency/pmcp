# Detailed plan: name an npm package only when npm's own parser makes it certain

> **Revision 6 (2026-08-27) — final; implement from this.** Rev 5 boarded 3
> DISAGREE. Its gate, read literally, would have **refused every npm server on
> every host** (twice over). Those are fail-safe defects that surface on the
> first test run, unlike the fail-dangerous ones of rounds 1–3 — so this revision
> goes to implementation and the **diff** gets boarded, not the plan.
>
> **Revision 5 (2026-08-27).** The rev-3 CLI seats returned late with findings
> rev 4 had not covered — two DISAGREE. The worst: a self-test failure was
> specified to fall back to the tables, which is a **fail-open** in exactly the
> situation that proves the host parser cannot be trusted. Also two of the plan's
> own verification commands were verified to prove nothing.
>
> **Revision 4 (2026-08-27).** Rev 3 boarded PARTIALLY AGREE with **six blocking
> findings, four of them verified confident-wrong answers**. The root cause was
> structural: rev 3's gate was a *denylist of unusual things*, so every round
> found another unusual thing. Rev 4 flips it to an **allowlist of plain things**
> — see "Step 1". That is a class fix, not a fourth round of instances.
>
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

If yes, return it. Otherwise **refuse**.

**Why refusal is safe — corrected.** Rev 3 said the coarse `command + args`
fallback identity is "unique per config and never collides." **That is false**,
and a seat verified it: two refused servers with identical argv and different env
*do* share the coarse string. Refusal is safe for a different reason — a refusal
yields `("unknown", None)` (`version_checker.py:1553`), the cache stores
`package_type="unknown"`, and `_same_package` treats `"unknown"` as
unidentified (`refresher.py:226-229`), so **every** comparison fails closed
regardless of the coarse string. The poisoned *type*, not the string, is what
makes it safe.

**The honest cost of refusing**, also verified: a refused server re-connects and
regenerates its descriptions on **every** refresh cycle and is listed
permanently stale by `check_staleness` (`refresher.py:512`). That is a cost, not
a correctness problem, and it is why the allowlist is drawn to cover the plain
shape rather than drawn as tightly as possible.

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

### Step 1 — Refuse unless everything is on an allowlist

**WAS WRONG (rev 3):** this was a denylist — "refuse if `npm_config_*` in env, or
`cwd` set, or `--call`, or …". Four verified holes followed, each producing a
*confident wrong answer*, because every `npm_config_*` option is equally
settable as a **command-line flag** and the denylist covered only env:

- `npx --userconfig $RC probe` → fetched `rc-pkg` (the rc file's `package=`).
- `npx --prefix $DIR probe` → same, via `$DIR/.npmrc`.
- `npx --registry=… probe` → same *name*, different registry namespace; and
  `update_server` builds its probe **without** the server's flags
  (`handlers.py:~5069`), so a private-registry name gets probed against public
  npmjs.org, where it is squattable.

nopt returns the parsed config keys directly, so the allowlist is nearly free
and closes keys npm has not invented yet:

| Requirement | Rationale |
|---|---|
| command is bare `npx`/`npm` | a full path never reaches this branch |
| **every parsed nopt config key is in `{yes, package}`** | anything else may redirect resolution. 78 of 79 shipped servers parse to `{yes}` alone, so the allowlist costs nothing |
| the server's **env OVERLAY** sets no key matching `npm_config_*` (case-insensitive), `PATH`, `HOME`, `NODE_PATH`, `NODE_OPTIONS`, `PREFIX`, or `NVM_*` | npm matches `/^npm_config_/i`; a server-set `PATH` selects *which npm runs* (11.6.2 vs 11.19.0 disagree on `npx --name foo probe`); and `HOME` relocates `~/.npmrc`, so `HOME=/x` with `/x/.npmrc` containing `package=other` redirects resolution entirely |
| walking up from the **effective** cwd (server `cwd`, else `os.getcwd()`), npm would set **no local prefix** — i.e. no ancestor contains `package.json` **or** a `node_modules` directory | This is npm's own rule, read from `@npmcli/config/lib/index.js:695-716`: `hasPackageJson \|\| await dirExists(p, 'node_modules')`. Verified: with pmcp's cwd inside a node project, `npx probe` resolved via that project's `.npmrc`; with `node_modules/.bin/<name>` present, npx ran the **local bin with no registry fetch at all** |
| the spawn-time self-test passed, and the `npx-cli.js` hash matches | see "Drift" |

Anything else: `REFUSED`.

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

Run the candidate through the host's own `npm-package-arg` and **require
`type in {"tag", "version", "range"}`**.

**WAS WRONG (rev 3):** "empty spec, or no valid name, → refuse" was not enough.
Verified: `npx -y myalias-zz@npm:left-pad` runs **`left-pad`**, but npa returns
`{type: "alias", name: "myalias-zz"}` — a perfectly valid name — and
`_strip_npm_tag` yields `myalias-zz`. So an alias mints a confident wrong
identity, version checks query a squattable name, and swapping the alias target
(`a@npm:x` → `a@npm:y`) leaves the identity unchanged so `_same_package`
confirms TRUE and serves x's descriptions for y. That is the #180 class,
reopened through the "certain" path. `git`, `remote`, `file` and `directory`
specs already refuse under the old wording because npa gives them
`name: undefined`; `alias` is the one type with a name that is not the package
npm fetches.

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
against the host's own nopt and definitions. Any failure ⇒ the resolver returns
**`REFUSED` for every query** for its whole lifetime, and logs once at WARNING.

**WAS WRONG (rev 3 and rev 4):** this said `UNAVAILABLE`-with-reason, which
means *fall back to the tables*. The red-team seat correctly called that a
fail-open: a failed self-test is precisely the evidence that the host's parser
behaves in a way this code does not model, and responding by consulting the
known-incomplete 2.5.2 tables is the worst available choice. `UNAVAILABLE` is
reserved for **spawn** failure — the case where we learned nothing about npm,
only that node is missing. A hash mismatch takes the same `REFUSED` path — there is no `UNAVAILABLE`-with-reason state; that phrasing was
rev 3's and is gone. The `npx-cli.js` hash stays as a tripwire **with a specified firing action**: a
mismatch produces the same `UNAVAILABLE`-with-reason + WARNING as a failed
self-test. Rev 3 called it a tripwire but never said what it does on mismatch,
which made it decorative. It matters because the self-test *is* tautological with
respect to the ported pre-scan — port and expectations are frozen by the same
author — so the hash is the only thing that can detect pre-scan drift. The
self-test's expected values are frozen literals, never recomputed from the code
under test.

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
- **Emit a `{"ready": true, "npmVersion": …, "selfTest": …}` handshake record
  before accepting any query**, and the parent must consume it before its first
  write. The self-test runs at spawn over the same NDJSON stdout as queries, so
  without a handshake the first `resolve()` can read a self-test line as its own
  answer — a wrong `Identity`, the same mis-attribution class as the timeout case
  the plan already names. Terminate the child before releasing the lock on a
  **protocol violation** too, not only on hang or death.
- Resolve `nopt`, `definitions` and `npm-package-arg` with
  `createRequire(<resolved npx-cli.js path>)` so the modules always come from the
  same npm installation the pre-scan was read from.
- The per-request `try`/`catch` must wrap **the pre-scan as well as nopt** (real
  npx dies inside the pre-scan at `npx-cli.js:89` on `--__proto__=evil`), and a
  poisoned argv must produce `REFUSED` for that request **without** flipping the
  process to sticky `UNAVAILABLE`.
- `npxPreScan`'s `switches` set must be live `definitions` **plus npx-cli's own
  hardcoded extras** (`quiet`, `q`, `help`, `h`, `version`, `v`, `no-install`).
  Dropping them lets a later `--package=` outrank the positional because no `--`
  is inserted.
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
  `(command, tuple(args), frozenset(gate_relevant_env.items()), cwd)` — a raw
  dict is unhashable, and "relevant_env" must be defined as *exactly the keys the
  gate inspects*, or two configs differing only in an ungated key share a cache
  entry.
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

**WAS WRONG (rev 3):** it listed six call sites and said each "already has the
server config in hand." A seat applied the signatures in a scratch copy and ran
mypy: the six surface, but two of them are inside *other* functions that have no
config in hand, so the change cascades:

- `version_checker.py:1673` sits inside `get_package_version(command, args,
  timeout)` → that signature must grow too → its callers at `refresher.py:266`,
  `:291`, `:503` (three sites rev 3 never listed; the ones it did list are
  different calls) → and ~10 `fake_get_package_version(command, args,
  timeout=5.0)` monkeypatches in `tests/test_tools.py`.
- `handlers.py:315` sits inside `_detect_effective_version_pin(package_type,
  command, args)` → that signature and its caller at `~:5070` grow.

`mypy src/` never sees the test fakes — those fail at pytest runtime only. The
implementer must treat the closure above as the work item, not the six.

Each converted site passes its effective env (the `sanitized_subprocess_env`
overlay) and the effective cwd. **Note which type actually carries cwd:** the
manifest's `ServerConfig` has `extra_env` and **no** `cwd`, so the refresher path
passes `cwd=None` and the gate falls back to the process cwd;
`LocalMcpServerConfig` is the type that carries one. `get_package_version` is
also called independently at `handlers.py:5242`, after update probing — that
caller is part of the closure too.

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

**Safety first — the harness must be hermetic.** A dead registry stops npm
*fetching* but **not executing**: `libnpmexec` runs a matching local or global
binary and returns into execution *before* any registry fetch. Running all 79
manifest names plus fuzz cases against an ordinary environment would **launch
installed server code**. Each oracle invocation runs with an empty temporary
`cwd`, a temporary `HOME`, a temporary `prefix` and `cache`, and a bounded
process-tree kill on exit.

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
# NOT `PATH=/usr/bin:/bin` -- verified on this host that path still contains
# node, npm and npx (and excludes uv, so the command is not even runnable).
# Build a genuinely node-less PATH that still has uv:
NODELESS=$(mktemp -d) && ln -s "$(command -v uv)" "$NODELESS/uv" && \
  env PATH="$NODELESS" uv run pytest -q tests/test_version_checker.py -k npm
uv run python .consiliency/notes/differential_npm_corpus.py --against-binary
# The smoke must ACTUALLY EXERCISE the packaged .js and assert an identity.
# `...` is a valid Python no-op: rev 3's version imported the lazy resolver and
# never loaded the helper, so the criterion was unproven.
uv build && python -m venv /tmp/vw && /tmp/vw/bin/pip install -q dist/pmcp-*.whl && \
  /tmp/vw/bin/python -c "
from pmcp.manifest.npm_resolver import get_resolver
r = get_resolver().resolve('npx', ['-y', 'left-pad'], env={}, cwd=None)
assert r.is_identity and r.spec == 'left-pad', r
print('helper OK:', r.spec)"
uv run ruff check . && uv run ruff format --check . && uv run mypy src/
```

## Acceptance criteria

- [ ] **All 79 manifest npm servers resolve** to the same package the real binary
      runs, with zero disagreements — *and* every form on this required-to-resolve
      list also resolves (not refuses), matching the binary: `npx -y <pkg>`,
      `npx <pkg>`, `npm exec <pkg>`, `npm exec --package=<pkg> -- <bin>`,
      `npx -y <pkg>@1.2.3`, `npx -y @scope/<pkg>`.
      **The dead-registry env goes to the binary only.** The corpus observes npm
      via `npm_config_registry=http://127.0.0.1:9`, but the gate refuses any
      `npm_config_*` — so feeding that env to `resolve()` makes the child refuse
      every input and the criterion unsatisfiable (or invites an implementer to
      punch a hole in the gate). AC1 counts the **child's `Identity` results**,
      not combined `detect_package_type` answers.
      **Also required:** at least one case where the **tables are wrong and the
      resolver matches the binary** (`npx --pack zz bin`, `npx --yes=maybe tok`,
      `npx --frobnicate valpkg realbin`). Without it the 79 plain manifest
      servers can go green on the tables alone, proving nothing about the
      resolver.
      **WAS WRONG (rev 3):** "zero disagreements AND a non-zero resolved count"
      is satisfied by a regex that resolves one form and refuses everything else,
      because a refusal is not a disagreement. Naming the list is what makes the
      criterion bite.
- [ ] Every named corpus form yields a **recorded three-way verdict**
      (resolver / tables / binary), with "binary made no fetch" and "binary
      crashed" as first-class expected outcomes. Membership in the corpus is not
      enough — rev 2's wording passed if a form was present but never compared.
- [ ] **A `REFUSED` result never reaches the table scan**, asserted on the
      **result**: for every input that trips an allowlist requirement, and for
      which the un-poisoned tables return a non-`None` *wrong* answer X,
      `_npm_package_arg(...) is None` and `detect_package_type(...) ==
      ("unknown", None)`.
      Required for the gates where the 2.5.2 tables *do* return a non-`None`
      wrong answer — verified: `--userconfig /tmp/rc probe` and
      `--registry http://x probe` both yield `('npm','probe')`, as does the
      alias case. **Not** required for two-distinct-`--package`, where the tables
      already return `('unknown', None)`; rev 4 demanded it for *every* gate,
      which the red-team seat showed no implementation can satisfy.
      **WAS WRONG (rev 3):** it specified a monkeypatched call counter *and
      dropped rev 2's result assertion*. A seat then passed it with an
      implementation whose REFUSED path calls the scan through a def-time alias
      and returns the scan's wrong answer — the counter saw zero because the
      monkeypatch has exactly the def-time blindness rev 3 claimed to close, and
      nothing checked the result. Assert the result; a counter may supplement it
      only if instrumented *inside* the scan function rather than monkeypatched.
- [ ] With the resolver active, `npm run mcp`, `npm start`, `npm test`,
      `npm -y server-pkg` and `npm dlx x` return `("unknown", None)`; `npm exec
      --package=pkg -- bin` returns `pkg`; `npm exec pkg@1.2.3` returns raw
      `pkg@1.2.3`.
- [ ] Passing an unconverted call site (no env/cwd) is a **type error**, not a
      silent pass — proven by `mypy` failing on a deliberately reverted caller.
- [ ] A failing spawn-time self-test **refuses** — `_npm_package_arg(...) is
      None`, `detect_package_type(...) == ("unknown", None)`, the table scan is
      **not** reached, and exactly one WARNING is logged. Proven end-to-end
      against a fixture npm root, not by injecting a flag into a response.
      **WAS WRONG (rev 5):** the Drift section was corrected to refuse but this
      criterion still said "degrades to the tables" — the same fail-open, left
      behind in the acceptance contract by the repair that removed it from the
      design.
- [ ] With node removed from `PATH`, all 98 manifest entries resolve exactly as
      2.5.2 does — recorded before/after diff of the 98 pairs.
- [ ] One spawn attempt across ≥50 resolves on a node-less host; a hung child
      stalls a caller at most once, bounded by the 1.0 s timeout.
- [ ] The helper `.js` works after installing the built wheel into a clean venv.

## Non-goals, named rather than implied

- Workspace / local / global **bin** resolution (`libnpmexec` searches these
  before the registry). Refused via the workspace-flag and `cwd` gates.
- `.npmrc` at user or global level. **WAS WRONG (rev 3):** it claimed the project
  case was "refused via the `cwd` gate" — false, because that gate read only the
  *server's* `cwd` while npm resolves from the *process* cwd when none is set.
  Rev 4's effective-cwd requirement closes the project case. **User and global
  `.npmrc` remain unguarded**: a `package=` or `registry=` line there changes
  resolution and the resolver cannot see it. That is the one known hole left
  open, stated here rather than discovered later.
- Auto-update for any refused config. It degrades to coarse identity; it does not
  break.

## Execution Policy

- execute: effort=high, reason=subprocess lifecycle and a signature change across
  six call sites on the path that mints security-relevant package identity
