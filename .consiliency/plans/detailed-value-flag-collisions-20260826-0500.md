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

## The design: `--flag=value` is safe, bare `--flag value` is not

An argv scanner cannot know whether `--foo bar` means "flag `--foo` with value
`bar`" or "boolean flag `--foo`, then positional `bar`" without a per-tool table
of which flags take values. That table is the thing that keeps being incomplete.

What it *can* know, without any table:

- `--flag=value` is **self-delimiting**. The value cannot be mistaken for a
  positional, so a token after it is genuinely positional.
- A bare `--flag` is **ambiguous**. The next token may or may not belong to it.

So: **when a bare `--flag` precedes the candidate token, identity is not
recoverable** — return `("unknown", None)`. Under the identity gate, unknown
means *cannot confirm → refresh*, which is safe; and `update_server` already
refuses cleanly on unknown before building any probe (`handlers.py`, the
`package_type == "unknown"` guard), which is the #183 outcome.

**The cost, stated plainly:** a server launched as `uvx --python 3.12 pkg`
becomes unverifiable and refreshes every time, and `update_server` will refuse
to auto-update it. That is a real regression in convenience for a legitimate
config. It is accepted because the alternative is what ships today: two
different packages sharing one identity, with the wrong descriptions served
indefinitely and no signal. Failing closed is loud and recoverable; failing open
is silent.

**Known-safe value flags stay enumerated** where the value *is* the package —
`uvx --from`, `pip --index-url` is not one, `cargo -p/--package/--bin`. These
are kept as explicit positive cases, not as an exhaustion attempt.

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

  1. **`--from` must be honoured wherever it appears**, not only as the first
     flag. A naive left-to-right ambiguity rule would hit the bare `--python`
     first and return unknown — breaking the documented form rather than fixing
     it. Scan for `--from` (and `--from=`) across the whole argv before applying
     the ambiguity rule.
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

- [ ] All seven pairs in the table resolve to **different** identities — proven
      by `TestValueFlagCollisions`, each node mutation-proved with recorded
      output, all seven RED today.
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
