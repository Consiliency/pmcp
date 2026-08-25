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
2. **npm** — `_npm_package_arg` (`version_checker.py:~?`, search the symbol)
   skips only `-y` and flags. It has **no subcommand skip at all**, so `exec`,
   `run` etc. are taken as the package. This is an asymmetry with the docker
   branch, which does skip its subcommands.

The third finding (`localhost:5000/a/b:tag`) is not in the issue text; it is the
same docker root cause with a different registry host, and is included here.

**Scope discipline.** `uvx --from thing other` → `("pypi", "thing")` is
**correct** (`--from` names the package) and is out of scope. `pip`/`cargo`
branches are not implicated. This plan touches the two branches with measured
collisions and nothing else.

## Changes

### `src/pmcp/manifest/version_checker.py` (modify)

- `_split_docker_reference` — **add** — new module-private helper returning
  `(name, tag_or_none)` for an OCI reference, splitting on the last `:` **only
  when it occurs after the last `/`**. Extracted rather than inlined so the rule
  has one home and one test surface; the docker branch and any future caller
  that wants the tag both read it from here.
- `detect_package_type` docker branch — **modify** — replace
  `raw.split(":")[0]` with `_split_docker_reference(raw)[0]`.
- `_npm_package_arg` — **modify** — skip a leading npm **subcommand** token
  (`exec`, `run`, `install`, `i`, `add`, `create`, `dlx`) the same way the docker
  path already skips its own subcommands. Only when `command == "npm"`; `npx`
  takes a package directly and must not gain a skip that would swallow a package
  legitimately named `exec`. **This changes `_npm_package_arg`'s signature** — it
  needs to know which command it is scanning for. Its other caller is
  `update_server`'s pin detection (see Dependencies).

### `tests/test_version_checker.py` (modify)

- `TestDetectPackageType` — **add** — one test per collision pair asserting the
  two forms now resolve to **different** names: `registry:5000/old-image` vs
  `registry:5000/new-image`; `npm exec old-pkg` vs `npm exec new-pkg`. A test
  that only asserts one form's new value would pass against a parser that
  collapsed both to something else, so **assert the inequality**, not just the
  value.
- `TestDetectPackageType` — **add** — regression cases that must not move:
  `img:1.2`→`img`, `ghcr.io/org/img:v2`→`ghcr.io/org/img`,
  `mcp/server:latest`→`mcp/server`, `localhost:5000/a/b:tag`→`localhost:5000/a/b`,
  `npx -y @scope/pkg@1.2.3`→`@scope/pkg`, `uvx --from thing other`→`thing`.
- `TestDetectPackageType` — **add** — `npx` must still accept a package named
  `exec` (`npx -y exec` → `exec`), pinning that the subcommand skip is
  npm-only.

### `tests/test_tools.py` (modify)

- `TestUpdateServerPinDetection` (or the existing pin-detection class — search
  `_npm_package_arg` / pin) — **modify** — only if the signature change requires
  a call-site update in a test. No behavioural change intended here.

## Documentation impact

- `CHANGELOG.md` — **add** — a `### Fixed` bullet under `[Unreleased]`. This is
  user-visible twice over: a docker server on a `host:port` registry and an
  `npm exec` server both get a *different* package identity than before, so
  their cached entries fail `_same_package` once and refresh. State that
  one-time refresh explicitly, the same way 2.4.0 documented the `package_type`
  migration.
- `README.md` — **none**. The parser is internal; no documented behaviour
  changes.
- No other cross-cutting doc applies — no `ARCHITECTURE.md`/`docs/**` in this
  repo, and `llm.txt`-family files do not describe this function.

## Dependencies & order

1. `_split_docker_reference` must exist before the docker branch calls it.
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

**Mutation proof required, not optional.** For each new collision test, revert
the corresponding parser line and confirm that test fails. A collision test that
passes against the *old* parser is proving nothing — this repo has shipped that
exact class of hollow test twice in the last two phases, both times caught only
by a board.

Edge cases to exercise: a bare `:` with nothing after it; an image with a digest
(`img@sha256:…`) rather than a tag; `npm` with no subcommand at all
(`npm ['pkg']`); an npm package legitimately named `exec`.

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
      and by reverting `_split_docker_reference`'s use to confirm the test fails.
- [ ] `detect_package_type("npm", ["exec","old-pkg"])` and `...new-pkg` return
      **different** package names, while `npx -y exec` still resolves to `exec` —
      same command, same mutation requirement.
- [ ] Every pre-existing form resolves exactly as before: `img:1.2`,
      `ghcr.io/org/img:v2`, `mcp/server:latest`, `npx -y @scope/pkg@1.2.3`,
      `uvx --from thing other` — proven by the regression cases in
      `TestDetectPackageType`.
- [ ] Every `_npm_package_arg` caller is updated together — proven by
      `grep -rn '_npm_package_arg' src/ tests/` showing no call site passing the
      old signature, plus `uv run mypy src/pmcp --exclude baml_client` clean.
- [ ] Full suite, ruff, and mypy green; `CHANGELOG.md` records the fix and the
      one-time refresh it causes for affected servers.
