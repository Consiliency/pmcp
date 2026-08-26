# Detailed plan: stop reading a flag's value as the package name

## Task

Close Consiliency/pmcp#182. `detect_package_type` skips **flags** but not the
**values those flags carry**, so the first non-flag token is often a flag's
argument rather than the package. Two different servers then resolve to the same
identity, and v2.4.0's identity gate — which compares exactly this name to decide
whether a cached description still describes the configured package — reads that
as a **positive confirmation** and serves the wrong package's tool descriptions.

All seven reproduce on `main` @ `75eb9c7`:

```
uvx   --python 3.12 pkg-a / pkg-b          -> both ('pypi',  '3.12')
uvx   --with requests srv-a / srv-b        -> both ('pypi',  'requests')
pip   install --index-url URL pkg-a / b    -> both ('pypi',  'https://x')
cargo run --features full srv-a / srv-b    -> both ('cargo', 'full')
docker run --env-file .env img-a / img-b   -> both ('docker','.env')
docker run --mount type=bind,… img-a / b   -> both ('docker','type=bind,src=/a')
npm   exec --package=old/new -- bin        -> both ('npm',   'bin')
```

`uvx --from thing other -> ('pypi','thing')` is correct **only by luck**:
`--from`'s value happens to be the package. That single safe flag is what made
the branch look sound in #180's research.

## Research summary

Context is in session from #180/#183, so no Explore fan-out; the branches were
read directly (`src/pmcp/manifest/version_checker.py`, `detect_package_type` and
the `_docker_image_arg` helper).

**Two models already exist in this file, and they disagree.**

- **Docker enumerates.** `_docker_image_arg` carries a `_value_flags` set —
  `-e --env -v --volume -p --publish --name --network -u --user --entrypoint
  -w --workdir --label -l --memory -m --cpus --add-host --dns --hostname -h` —
  and skips the token after each. It is the most careful branch in the file and
  **it is still incomplete**: `--env-file` and `--mount` are missing, which is
  how two of the seven collisions above happen.
- **uvx / pip / cargo do not enumerate at all.** They skip `arg.startswith("-")`
  and take the next bare token, so *every* value-carrying flag collides.

**#183 already settled this design question in this same function.** Its first
fix was a denylist of npm subcommands; the board rejected it because a denylist
**fails open** — anything unlisted became an installable package name. It was
inverted to an allowlist. Docker's `_value_flags` is the same shape: an
enumeration whose omissions fail open, and it has already been demonstrated
incomplete twice.

**So this plan does not extend the enumeration.** Extending it would fix the two
docker flags named in the issue and leave the class open — precisely the
"fix the instance, not the class" error the last three rounds caught.

## The design: three-way flag classification, fail-closed default

*This section was rewritten after board review. The original — "any bare
`--flag` before the candidate makes identity unrecoverable" — was verified to
break more than it fixed; see **Rejected** below.*

Per ecosystem, classify each flag into one of three kinds, and **default an
unlisted bare flag to unknown**:

| kind | behaviour | examples |
|---|---|---|
| **known-value** | consumes the next token | uvx `--python`, `--with`, `--index-url`; docker's existing `_value_flags` **plus** `--env-file`, `--mount`; cargo `--features`, `--target`; pip `--index-url`, `-i` |
| **known-boolean** | skip, next token is still a candidate | uvx `--quiet`, `--verbose`; docker `-i`, `-t`, `-it`, `-d`, `--rm`, `--init` |
| **known-positive** | its value **is** the package | uvx `--from`; cargo `-p`, `--package`, `--bin`; npm `--package` |
| *unlisted bare flag* | **`("unknown", None)`** | anything not classified |

**Why this is not the denylist #183 rejected.** Docker's `_value_flags` failed
open because the *default* for an unlisted flag was "skip it and take the next
token as the image" — an omission silently produced a wrong identity. Inverting
the default is the whole point: an omission now costs *auto-update for one odd
config* (safe, loud, fixable by adding an entry) instead of a *silent collision*
(unsafe, invisible). Same table, opposite failure direction.

**The honest residual:** a *wrong* entry still fails open. Classify `--pull` as
boolean when it actually takes a value and that value becomes the image. So
entries must be verified individually against each tool's documentation and
pinned by a test — never bulk-imported from memory.

### Rejected: pure fail-closed on any bare flag

*Board finding, verified.* It would have broken the canonical configs:

```
uvx    --quiet my-package --arg            -> my-package        (would become unknown)
docker run -i --rm mcp/server:latest       -> mcp/server        (would become unknown)
docker run -e KEY=val --rm ghcr.io/org/mcp -> ghcr.io/org/mcp   (would become unknown)
docker run -it --rm img                    -> img               (would become unknown)
```

`docker run -i --rm <image>` is *the* canonical docker MCP shape — this repo's
own README uses `docker run -it --rm` (`README.md:1528`) — and `-it` is a
combined short boolean no value-table would match. Under the rejected rule
essentially every real docker server became permanently unverifiable and
un-auto-updatable, and three currently-green tests
(`tests/test_version_checker.py:101-107`, `:121-127`, `:131-137`) would have
turned red, contradicting this plan's own "existing suite stays green"
criterion. That regression dwarfs the collisions it closed.

### It also dissolves the `--from` hazard

With `--python` classified as known-value, a plain **left-to-right** scan
resolves the README form deterministically: `--python` consumes `3.12`, then
`--from` yields the package. **No whole-argv `--from` scan is needed**, which
removes the hazard the board found — measured, `uvx mypkg --from other` today
correctly yields `mypkg`, and a global scan would have returned `other`: a
fail-open misidentification *introduced by the fix*, re-colliding
`uvx a --from x` with `uvx b --from x` through the new path.

Scans must still terminate at a `--` separator and at the first unambiguous
positional, since everything after the tool name belongs to the served tool.

## Changes

### `src/pmcp/manifest/version_checker.py` (modify)

- `_takes_a_value_ambiguously(arg)` — **add** — module-private predicate: true
  for a token starting `-` that is **not** `--flag=value` form and not in the
  branch's known-positive set. One home for the rule.
- `detect_package_type` uvx branch — **modify** — honour `--from <pkg>` as the
  package (already the documented pin form, see `README.md` around the
  `"args": ["--python", ...]` example); return `("unknown", None)` when any
  other bare flag precedes the candidate.
- `detect_package_type` pip branch — **modify** — same rule; keep the existing
  `install`/`upgrade` subcommand skips.
- `detect_package_type` cargo branch — **modify** — keep `-p`/`--package`/
  `--bin` as known-positive (their value *is* the package); any other bare flag
  before the candidate yields unknown.
- `_npm_package_arg` — **modify.** *Board finding: the original change list
  omitted this entirely, so the seventh collision would have shipped unfixed.*
  It currently skips `--package=old` as a flag and `--` as a flag, then returns
  `bin` (verified). Treating `--package=old` as merely "self-delimiting, so keep
  scanning" does **not** extract `old` — the scanner must read the value out of
  `--package=<pkg>` / `-p <pkg>` and return it as the package. This is the only
  one of the seven where identity is genuinely **recoverable**; the other six
  are refusals.
- `_docker_image_arg` — **modify** — keep `_value_flags` as a fast path for the
  flags it already lists, and add the same ambiguity rule for anything not in
  it, so `--env-file` and `--mount` stop consuming the image. Do **not** simply
  add those two to the set.

### `tests/test_version_checker.py` (modify)

- `TestValueFlagCollisions` — **add** — one **inequality** assertion per
  collision pair above. A value assertion would pass against a parser that
  collapses both forms onto some other shared name; only inequality is falsified
  by the collision itself. All seven are RED today.
- `TestValueFlagsFailClosed` — **add** — the ambiguous forms resolve to
  `("unknown", None)` rather than to a wrong name.
- `TestKnownPositiveValueFlags` — **add** — `uvx --from pkg`, `cargo -p pkg`,
  `cargo --bin b`, `pip install pkg`, `npx -y pkg`, `docker run img` and the
  `#180` forms all still resolve exactly as before.

### `tests/test_tools.py` (modify)

- Pin-detection matrix — **add** — a `uvx --python 3.12 pkg` server now yields
  no identity, so `update_server` refuses rather than probing. Assert **no probe
  was executed**, mirroring the `#183` test — the parse being right only matters
  if it reaches the refusal.

## Documentation impact

- `CHANGELOG.md` — **add** — a `### Fixed` bullet. User-visible three ways:
  affected servers refresh once; a server launched with an ambiguous flag form
  can no longer be auto-updated (state this plainly, it is the cost); and the
  identity gate now actually holds for the forms #180 left open.
- `README.md` — **resolved before implementing, and it changes the design.**
  The README documents (`README.md:1133-1137`) a first-party pin form:

      uvx --python 3.12 --from index-it-mcp==1.2.0 index-it-mcp

  Measured on `main` today, that resolves to **`('pypi', '3.12')`** — the Python
  *version* as the package name. So #182 is not hypothetical for uvx: it already
  mis-identifies a config this repo recommends in its own README.

  Two consequences for the design:

  1. **`--from` must be honoured wherever it appears in uvx's OWN argv** — but
     the scan must **stop at the tool-name / `--` boundary**. *Board finding.*
     uv passes everything after the tool name through to the tool, so an
     unbounded scan would mistake a *server's own* later `--from` argument for
     uvx's package identity. A naive left-to-right ambiguity rule is equally
     wrong in the other direction: it hits the bare `--python` first and returns
     unknown, breaking the documented form rather than fixing it.
  2. **The `=` spelling has a pin hazard that must be closed in the same
     change.** *Board finding, verified:* `_detect_effective_version_pin`
     (`handlers.py:319`) skips every `-`-prefixed token, so
     `--from=index-it-mcp==1.2.0` reports **no pin** while the spaced form
     `--from index-it-mcp==1.2.0` correctly reports `1.2.0`. If the detector
     starts recognising the `=` spelling for *identity* without pin detection
     learning it too, `update_server` will classify the package, see no pin, and
     probe for **latest** — updating a server the operator explicitly pinned.
     Identity and pin must share one boundary-aware uvx scan, exactly as
     `_npm_package_arg` is shared between the two.
  2. With that, the README form resolves correctly to `index-it-mcp` and needs
     **no doc change** — the fix repairs it instead of degrading it. Verify this
     exact string in the acceptance criteria rather than assuming.
- **#180 can close** if all its remaining forms are covered. Verify rather than
  assume — that issue was already narrowed once.

## Dependencies & order

1. `_takes_a_value_ambiguously` before any branch uses it.
2. All five branch edits in one commit; a half-applied rule is worse than none,
   since it makes some ecosystems fail closed and others fail open.
3. Tests written and shown **RED per node** before the implementation.

## Verification

```bash
cd /mnt/workspace/worktrees/pmcp-182-flagvalues
find src tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null

# All seven collisions gone:
PYTHONDONTWRITEBYTECODE=1 uv run python - <<'PY'
from pmcp.manifest.version_checker import detect_package_type as d
pairs = [("uvx",["--python","3.12","a"],["--python","3.12","b"]),
         ("uvx",["--with","requests","a"],["--with","requests","b"]),
         ("pip",["install","--index-url","u","a"],["install","--index-url","u","b"]),
         ("cargo",["run","--features","f","a"],["run","--features","f","b"]),
         ("docker",["run","--env-file",".e","a"],["run","--env-file",".e","b"]),
         ("docker",["run","--mount","m","a"],["run","--mount","m","b"]),
         ("npm",["exec","--package=old","--","bin"],["exec","--package=new","--","bin"])]
bad = [(c,x) for c,x,y in pairs if d(c,x) == d(c,y)]
print("still colliding:", bad or "none")
assert not bad
PY

PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/ -q
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
```

**Mutation proof, per node, output recorded.** For each collision test, revert
its branch's rule and confirm *that single test* fails — never the class. A red
class proves nothing about the specific test; that is how three hollow tests
shipped in this repo. Purge `__pycache__` and use `PYTHONDONTWRITEBYTECODE=1`
around every apply/restore: stale bytecode fabricated a false red in #184 and
can equally fabricate a false green.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```

## Execution Policy
- execute: effort=medium, reason=small surface but identity semantics are subtly wrong-prone

## Acceptance criteria

- [ ] **Six ambiguous pairs resolve to exactly `("unknown", None)`** — NOT to
      "different identities". *Board finding: the original criterion was
      logically impossible.* It demanded those pairs be simultaneously unknown
      **and** unequal, but two unknowns are equal, so no implementation could
      satisfy it. The real safety property is that **unknown never confirms
      identity**, which `_same_package` (`refresher.py:213`) already enforces —
      assert the exact unknown tuple, not inequality.
- [ ] **The one recoverable pair — `npm exec --package=old/new -- bin` — resolves
      to `old` and `new` respectively**, asserted by exact name *and* inequality.
      `--package=` is self-delimiting, so identity here is genuinely recoverable
      rather than merely refused.
- [ ] Ambiguous forms yield `("unknown", None)`, never a wrong name — proven by
      `TestValueFlagsFailClosed`.
- [ ] Every known-positive form is unchanged: `uvx --from pkg`, `cargo -p pkg`,
      `cargo --bin b`, `pip install pkg`, `npx -y pkg`, `docker run img`, and
      every `#180`/`#183` form — proven by `TestKnownPositiveValueFlags` plus
      the existing suite staying green.
- [ ] `update_server` refuses rather than probing for an ambiguous server —
      proven by a **no-probe-executed** assertion, not a parse assertion.
- [ ] The README's documented pin form resolves to the package, not the Python
      version — `detect_package_type("uvx", ["--python","3.12","--from",
      "index-it-mcp==1.2.0","index-it-mcp"])` must yield `index-it-mcp`, where
      today it yields `3.12`. RED today; this is a first-party config the repo
      recommends in its own README (`README.md:1133`), so it is a real
      user-facing defect, not a constructed one.
- [ ] Full suite, ruff, mypy green; CHANGELOG records the fix **and** the
      auto-update cost.
- [ ] **#180's status is settled explicitly** — closed if genuinely covered, or
      its remaining gap named. It has been narrowed once already; do not assume.
