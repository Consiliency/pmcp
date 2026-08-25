# Detailed plan: fix detect_package_type's package-identity collisions

## Task

Close Consiliency/pmcp#180. `detect_package_type` returns a package **name**
that is not unique for several legal command forms, so two genuinely different
packages resolve to the same identity. v2.4.0's identity gate compares those
names to decide whether a cached description still describes the configured
package, so for the affected forms `_same_package` **confirms identity across a
real package swap** and the freshness short-circuit serves the wrong package's
tool descriptions — the exact defect UPDPATH exists to close, unmet for those
command shapes.

## Research summary

Context was already in session from the UPDPATH phase and its board rounds, so
no Explore fan-out; the parser and every caller were read directly.

`detect_package_type` (`src/pmcp/manifest/version_checker.py:265`, ~60 lines of
branch logic) has five callers, in **two classes** that want different things
from it:

- **Version lookup** — `get_package_version` (`version_checker.py:402`) uses the
  name to hit a registry. A wrong name yields a failed or wrong lookup.
- **Identity comparison** (new in 2.4.0) — `refresher.py:259`, `:420`, `:499`
  and `handlers.py:5019` feed the name into `_same_package`. A wrong name that
  happens to be *stable across two different packages* is far worse: it reads as
  a positive confirmation.

Measured collisions on `main` @ `f5ea5ec`:

| command | resolves to |
|---|---|
| `docker run registry:5000/old-image` | `("docker", "registry")` |
| `docker run registry:5000/new-image` | `("docker", "registry")` |
| `docker run localhost:5000/a/b:tag` | `("docker", "localhost")` |
| `npm exec old-pkg` | `("npm", "exec")` |
| `npm exec new-pkg` | `("npm", "exec")` |

Two distinct root causes, and they are **not** the same bug:

1. **Docker** — the branch does `image = raw.split(":")[0]`
   (`version_checker.py:~317`), treating the first `:` as the tag separator. In
   an OCI reference a `:` only introduces a tag when it appears **after the last
   `/`**; before that it is a registry `host:port`. Note `_docker_image_arg`
   already correctly skips docker subcommands (`version_checker.py:365`) and a
   long `_value_flags` set, so the *selection* of the image token is sound — only
   the tag-stripping is wrong. Confirmed still-correct forms that must not
   regress: `img:1.2` → `img`, `ghcr.io/org/img:v2` → `ghcr.io/org/img`,
   `mcp/server:latest` → `mcp/server`.
2. **npm** — `_npm_package_arg` (`version_checker.py:57`) skips only `-y` and
   flags. It has **no subcommand skip at all**, so `exec`, `run` etc. are taken
   as the package. This is an asymmetry with the docker branch, which does skip
   its subcommands.

The third finding (`localhost:5000/a/b:tag`) is not in the issue text; it is the
same docker root cause with a different registry host, and is included here.

**The correct docker rule already exists in this file.** `_docker_image_tag`
(`version_checker.py:377`) splits the **last path segment** on its **first**
colon, with a docstring that names this exact case: *"a registry host with a
port (`registry:5000/img:1.2.3`) does not read as a tag of `5000`."* It is what
`update_server`'s pin detection uses (`handlers.py:323`). So the docker fix is
**not** a new rule — it is making `detect_package_type` use the rule the file
already has, which is why this plan no longer introduces a second helper.

**Scope correction (board finding, verified).** An earlier draft of this plan
claimed the `pip`/`cargo` branches "are not implicated" and that `uvx` was fine.
**That was false.** Every excluded branch collides, by the same root cause —
value-carrying flags whose values are not skipped:

```
uvx   --python 3.12 pkg-a / pkg-b        -> both ('pypi', '3.12')
uvx   --with requests srv-a / srv-b      -> both ('pypi', 'requests')
pip   install --index-url URL pkg-a/b    -> both ('pypi', 'URL')
cargo run --features full srv-a/b        -> both ('cargo', 'full')
```

`uvx --from thing other` → `thing` is correct only by luck: `--from`'s value
*is* the package. That one safe flag is what made the branch look sound.
Filed as **Consiliency/pmcp#182** rather than folded in — expanding this plan
from two branches to five, with a new shared `_value_flags` design, would turn a
bounded fix into a parser redesign and discard the review that scoping bought.
`uvx` is arguably the *most* consequential case (common MCP launcher, `--with`
is a normal pattern), so #182 should not sit long.

## Changes

### `src/pmcp/manifest/version_checker.py` (modify)

- `_docker_image_name` — **add** — module-private helper returning the image
  **name** from an OCI reference, defined immediately beside the existing
  `_docker_image_tag` (`version_checker.py:377`) as its paired inverse — the
  same design as `_strip_npm_tag`/`_npm_tag` (`version_checker.py:25-54`). It
  must be **the exact complement** of `_docker_image_tag`: strip any `@digest`
  first (`@` cannot appear in an OCI name, so `partition("@")` is unambiguous),
  then split the last path segment on its **first** colon. Two independent homes
  for this rule is the failure class the file's own docstrings exist to prevent.
  - *Board finding, verified:* an earlier draft of this plan specified "split on
    the last `:` when it occurs after the last `/`". Run against
    `img:1.2@sha256:abc` that yields `img:1.2@sha256` where today's code
    correctly yields `img` — **a regression**, because the last colon belongs to
    the digest. It also diverged from `_docker_image_tag`, which returns
    `1.2@sha256:abc` for the same input. Digest-first is what reconciles them.
- `detect_package_type` docker branch — **modify** — replace
  `raw.split(":")[0]` with `_docker_image_name(raw)`.
- `_npm_package_arg` — **modify** — skip **one leading** npm subcommand token
  (`exec`, `run`, `install`, `i`, `add`, `create`, `dlx`), and only when
  `command == "npm"`.
  - **Skip-once, leading-only — not the docker loop's shape.** Docker skips
    subcommand tokens *anywhere* in `args` (`version_checker.py:361-366`).
    Copying that here would swallow real packages: `npm install i` → `None`
    (`i` is a genuine npm package) and `npm exec exec` → `None`. The plan text
    previously said both "leading" and "the same way the docker path does";
    those contradict, and leading-only is correct.
  - `npx` must not gain the skip: it takes a package directly, so `npx -y exec`
    must still resolve to `exec`.
  - **This changes `_npm_package_arg`'s signature** — it needs the command it is
    scanning for. Do **not** give it a defaulted parameter: a defaulted call site
    would silently keep the old behaviour, which is the opposite of what a shared
    scan needs. Its other caller is `update_server`'s pin detection (see
    Dependencies).

### `src/pmcp/tools/handlers.py` (modify)

- `_detect_effective_version_pin` npm branch — **modify** — pass `command`
  through to `_npm_package_arg` (`handlers.py:307`). The function already
  receives `command` (`handlers.py:272`), so no plumbing is needed.
  - **This is a behaviour change, not a mechanical update.** Today
    `npm exec pkg@1.2` pin-detects on `exec`, so `_npm_tag("exec")` returns
    `None` and **a real pin is missed**. After the fix the pin is found. That is
    a fix, and it needs its own test and a CHANGELOG line — an earlier draft of
    this plan wrongly said "no behavioural change intended here."

### `tests/test_version_checker.py` (modify)

- `TestDetectPackageType` — **add** — one test per collision pair asserting the
  two forms now resolve to **different** names: `registry:5000/old-image` vs
  `registry:5000/new-image`; `npm exec old-pkg` vs `npm exec new-pkg`. A test
  that only asserts one form's new value would pass against a parser that
  collapsed both to something else, so **assert the inequality**, not just the
  value.
- `TestDetectPackageType` — **add** — **fix** cases (currently wrong, must be RED
  first): `localhost:5000/a/b:tag` → `localhost:5000/a/b`, and
  `img:1.2@sha256:abc` → `img`. *An earlier draft filed the `localhost` case
  under "must not move", which is wrong — it resolves to `localhost` today, and
  an executor honouring "resolves exactly as before" literally would enshrine
  the bug.*
- `TestDetectPackageType` — **add** — genuine regression cases that must not
  move: `img:1.2`→`img`, `ghcr.io/org/img:v2`→`ghcr.io/org/img`,
  `mcp/server:latest`→`mcp/server`, `npx -y @scope/pkg@1.2.3`→`@scope/pkg`,
  `uvx --from thing other`→`thing`, and `registry:5000` (bare host:port, no
  path) → `registry` — with no `/`, that genuinely *is* image `registry` tag
  `5000` under the OCI grammar, so it is correct today and must stay.
- `TestDetectPackageType` — **add** — skip-once pins: `npx -y exec` → `exec`
  (skip is npm-only) **and** `npm install i` → `i` / `npm exec exec` → `exec`
  (skip fires once, not repeatedly).
- `TestDetectPackageType` — **add** — `_docker_image_name` and
  `_docker_image_tag` are complements: for each docker form in this class,
  assert the name and tag together reconstruct the reference's meaning, so the
  two helpers cannot drift apart.

### `tests/test_tools.py` (modify)

- The pin-detection class (search `_detect_effective_version_pin` / pin) —
  **add** — `npm exec pkg@1.2` now detects the pin `1.2` where it previously
  returned `None`. This is a real behaviour fix and needs its own assertion,
  proved RED first. Also pin the docker digest form, since `_docker_image_name`
  landing beside `_docker_image_tag` touches that path.

## Documentation impact

- `CHANGELOG.md` — **add** — a `### Fixed` bullet under `[Unreleased]` covering
  **three** user-visible effects: (a) a docker server on a `host:port` registry
  and (b) an `npm exec` server each get a *different* package identity than
  before, so their cached entries fail `_same_package` once and refresh — state
  that one-time refresh explicitly, as 2.4.0 did for the `package_type`
  migration; and (c) `npm exec pkg@1.2` now has its version pin **detected**
  where it was previously missed, which changes `update_server`'s behaviour for
  those servers.
- Note the limitation that remains: **Consiliency/pmcp#182** — uvx/pip/cargo
  collide identically via unskipped flag values, and `uvx --with` is a more
  realistic trigger than anything fixed here. The CHANGELOG should not imply the
  identity gate is now total.
- `README.md` — **none**. The parser is internal; no documented behaviour
  changes.
- No other cross-cutting doc applies — no `ARCHITECTURE.md`/`docs/**` in this
  repo, and `llm.txt`-family files do not describe this function.

## Dependencies & order

1. `_docker_image_name` must exist, defined beside `_docker_image_tag`, before
   the docker branch calls it. Write them as a reconciled pair in one edit — if
   they are touched separately they can drift, which is the whole reason this
   plan stopped introducing a second independent helper.
2. **`_npm_package_arg`'s signature change is the one cross-file hazard.** It is
   deliberately shared: its docstring says `update_server`'s pin detection uses
   *the exact same scan* so the two cannot disagree about which argument is "the
   package". Find every caller (`grep -rn '_npm_package_arg' src/ tests/`) and
   update them together in one commit — a partial update leaves the pin
   detection and the type detection disagreeing, which is the precise failure
   that comment exists to prevent.
3. Tests are written **before** the parser changes and must be shown RED
   individually (per-test, not per-command — a class-level run can hide a
   non-discriminating test behind a red classmate).

## Verification

```bash
cd /mnt/workspace/worktrees/pmcp-180-parser

# The collisions, gone:
uv run python -c "
from pmcp.manifest.version_checker import detect_package_type as d
assert d('docker',['run','registry:5000/old-image']) != d('docker',['run','registry:5000/new-image'])
assert d('npm',['exec','old-pkg']) != d('npm',['exec','new-pkg'])
print('collisions resolved')"

# Nothing that worked before moved:
uv run pytest tests/test_version_checker.py tests/test_tools.py tests/test_refresher.py -q

# Full gate:
uv run pytest tests/ -q          # baseline on main: 2700 passed, 3 skipped
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
```

**Mutation proof required, and its output must be recorded.** For each new
collision test, revert the corresponding parser line and confirm that test
fails, then restore. A collision test that passes against the *old* parser is
proving nothing — this repo has shipped that exact class of hollow test twice in
the last two phases, both times caught only by a board, and in one of those the
hollow test was written *by the same agent that had just documented the failure
mode*. Performing the proof is not sufficient: **paste the failing output into
the handoff**, per test, so the evidence outlives the session that produced it.

Edge cases to exercise: a bare `:` with nothing after it (`img:`); a digest with
no tag (`img@sha256:…`); **tag and digest together (`img:1.2@sha256:…` — the
form that broke the first draft of this plan)**; a bare `host:port` with no path
(`registry:5000`, which correctly stays `registry`); `npm` with no subcommand
(`npm ['pkg']`); an npm package legitimately named `exec` or `i`.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```

## Execution Policy
- execute: effort=medium, reason=small surface but the identity semantics are subtly wrong-prone

## Acceptance criteria

- [ ] `detect_package_type("docker", ["run","registry:5000/old-image"])` and
      `...new-image` return **different** package names — proven by
      `uv run pytest tests/test_version_checker.py::TestDetectPackageType -q`
      and by reverting the docker branch to `raw.split(":")[0]` to confirm the
      test fails, with that output recorded.
- [ ] `detect_package_type("npm", ["exec","old-pkg"])` and `...new-pkg` return
      **different** package names, while `npx -y exec` → `exec`,
      `npm install i` → `i`, and `npm exec exec` → `exec` all hold — same
      command, same recorded-mutation requirement.
- [ ] **Digest forms resolve correctly, not merely differently:**
      `img:1.2@sha256:abc` → `img` (unchanged from today — the first draft of
      this plan regressed it), `img@sha256:abc` → `img`, and
      `registry:5000/img@sha256:abc` → `registry:5000/img`.
- [ ] `_docker_image_name` and `_docker_image_tag` are complements — for every
      docker form under test, name and tag agree about where the reference
      splits. Proven by the paired assertions in `TestDetectPackageType`, which
      is what stops the two rules drifting as they had begun to.
- [ ] Every pre-existing form resolves exactly as before: `img:1.2`,
      `ghcr.io/org/img:v2`, `mcp/server:latest`, `registry:5000`,
      `npx -y @scope/pkg@1.2.3`, `uvx --from thing other`.
- [ ] `npm exec pkg@1.2` now pin-detects `1.2` where it previously returned
      `None` — proven by a test in `tests/test_tools.py`, RED first.
- [ ] Every `_npm_package_arg` caller is updated together, with **no defaulted
      parameter** — proven by `grep -rn '_npm_package_arg' src/ tests/` showing
      both call sites passing the command explicitly, plus
      `uv run mypy src/pmcp --exclude baml_client` clean.
- [ ] Full suite, ruff, and mypy green; `CHANGELOG.md` records all three
      user-visible effects and does not imply the identity gate is now total
      (Consiliency/pmcp#182 remains open).
