# Detailed plan: read the installed version from the npx / uv runner caches

## Task

Fix Consiliency/pmcp#150 — the gateway fabricates "update available" notices, or hides real ones — by comparing against the version that is **actually installed** rather than an inferred one.

This is the sixth attempt. The five previous ones are documented (PR #154 closed after three rejected rounds; two plan artifacts each rejected 3/3). Maintainer decision: **probe the package manager directly**.

## Research summary

**The blocking discovery, which kills the naive form of this plan.** Every server in the manifest launches through an *ephemeral runner*, not an installer:

```
npx -> npm  : 79 servers
uvx -> pypi : 19 servers
(unknown)   :  9 servers
```

`npx` and `uvx` fetch into a cache and discard; they do not install into any tree that `npm ls` or `uv pip show` can see. Verified:

```
$ npm ls @upstash/context7-mcp
/home/viperjuice/code/pmcp
└── (empty)
```

So the obvious implementation — shell out to `npm ls` / `uv pip show` — **returns nothing for the servers pmcp actually runs**. Had I written that, it would have silenced every notice, which is precisely the failure mode of attempt #4.

**What does work: the runner caches are on disk and are queryable.**

*npx* (`~/.npm/_npx/<hash>/`): each entry has a `package.json` naming the package it was created for, and the resolved package under `node_modules/`. Extracted real versions:

```
@brightdata/mcp    -> 2.11.1
@twilio-alpha/mcp  -> 0.7.0
@eslint/mcp        -> 0.3.8
```

The directory name is a hash of the *argument set*, so pmcp cannot compute it — it must scan and match on the dependency name. Measured cost: **0.36s for 114 entries**, filesystem reads only, no subprocess and no network.

**Why matching on `dependencies` cannot collide with a transitive dependency** (the obvious objection, checked before planning): every npx cache entry declares **exactly one** dependency — the package npx was invoked for — while its `node_modules/` holds the full transitive closure. Sampled six entries, all `len(dependencies) == 1`, against a `node_modules` of 212 packages in one of them. So the match must be against `dependencies`, never `node_modules`; matching the latter would resolve any transitive package and is the trap to avoid.

*uv* (`uv cache dir`/`environments-v2/<a>/<b>/`): a normal venv whose `lib/python*/site-packages/*.dist-info` directories carry name and version (`sse_starlette-3.4.6.dist-info`). Note the layout is **two levels deep**, not one — my first probe assumed one and found nothing.

**Why this source is different from the five that failed.** It is bound to the package identity by construction: the cache entry names the package, and the version is read from that package's own metadata. Every prior attempt compared a version whose provenance was unrelated to the configured package — an upstream snapshot (`descriptions_cache.version`), or the MCP `serverInfo.version` (an *implementation* version; FastMCP 1.x reported the SDK's, mcp 2.x defaults it to `""`).

**Prerequisite already landed.** #155 (merged, `58b1a02`) made `is_version_newer` fail closed, so an unreadable version now yields silence rather than a fabricated notice, and npm/Cargo order by SemVer while Python keeps PEP 440. Without it any version source would fabricate notices regardless.

## Changes

### `src/pmcp/manifest/installed_version.py` (create)

New module — this is genuinely new capability with no existing home, and keeping it out of `version_checker.py` keeps "what is installed" separate from "what does the registry offer".

- `installed_npm_version(package_name, *, cache_root=None)` — **add** — scan `~/.npm/_npx/*/package.json`, match `dependencies` against *package_name*, read `node_modules/<pkg>/package.json` `version`. Returns `str | None`. `cache_root` injectable for tests.
- `installed_pypi_version(package_name, *, cache_root=None)` — **add** — scan `<uv cache>/environments-v2/*/*/lib/python*/site-packages/*.dist-info`, match the normalised distribution name (PEP 503: lowercase, `-`/`_`/`.` collapse), return its version.
- `installed_version(package_type, package_name)` — **add** — dispatch on type; `None` for `docker`/`cargo`/`unknown` (out of scope, see Non-goals).
- Module-level cache keyed by `(package_type, package_name)` with a short TTL — **add** — the scan is cheap but the notice paths run per-request; do not re-walk the tree on every `describe`.

All lookups are **best-effort**: any `OSError`, malformed JSON, or missing path returns `None`, never raises. A notice is an advisory, not a correctness-critical path.

### `src/pmcp/tools/handlers.py` (modify)

- `_get_update_warning` — **modify** — take `current` from `installed_version(...)` when available; keep the existing `descriptions_cache` value only as a fallback, gated by the package-identity check already present. When neither yields an orderable version, return `None` (silence).
- `_run_stale_index` — **modify** — same source and same fallback rule.
- `_stale_check_cache` — **modify** — record the version *source* (`"installed" | "described"`) in the memo alongside the existing `pkg_type`, so a reader can tell an observed version from an inferred one and a later change of policy does not have to guess.
- `update_server` success path — **modify** — after a successful restart, prefer the freshly-read installed version over the probed upstream latest when writing `desc_entry.version`, so the recorded baseline is what is running.

### `tests/test_installed_version.py` (create)

Fixture-driven against a **synthetic cache tree** (`tmp_path`), never the developer's real cache — a test that reads `~/.npm` passes or fails based on what happens to be on the machine.

- **add** — `test_npm_version_read_from_npx_cache`: synthetic `_npx/<hash>/package.json` + `node_modules/<pkg>/package.json` → correct version.
- **add** — `test_npm_scan_matches_the_right_entry`: three entries, only one matching → no cross-contamination.
- **add** — `test_pypi_version_read_from_uv_environment`: synthetic two-level env with a `.dist-info` → correct version.
- **add** — `test_pypi_name_normalisation`: `my_pkg` config name matches `my-pkg` dist-info per PEP 503.
- **add** — `test_missing_cache_returns_none` / `test_malformed_json_returns_none` — best-effort contract.
- **add** — `test_layout_change_is_detected`: assert against the *observed* layout (two-level for uv, `dependencies` key for npx). If a future runner changes its layout this must fail loudly rather than silently returning `None` forever, which would look identical to "no update available".

### `tests/test_tools.py` (modify)

- **add** — `test_notice_uses_installed_version_when_available`: installed `1.0.0`, upstream `2.0.0` → notice naming both.
- **add** — `test_notice_silent_when_installed_version_unavailable`: no cache entry, no orderable fallback → no notice, no exception.
- **add** — `test_package_change_compares_the_configured_package` (**the #150 case**): manifest names A, `.mcp.json` names B, cache holds B's real version → comparison is B-vs-B, never A-vs-B.

## Documentation impact

- `CHANGELOG.md` — **modify** — entry under `## [Unreleased]`. **Mandatory**: `main` is protected and the `changelog` check fails any PR touching `src/` without one. Must say plainly that notices now compare the installed version, and that servers whose installed version cannot be determined produce no notice.
- `README.md` — **check** whether it documents the update-notice behaviour; if so, update, else record `no doc footprint`.

## Frozen vocabulary / protocol check

No protocol surface changes. `installed_version` is internal; `_stale_check_cache` is private in-memory state with no serialised form; `CatalogSearchOutput.stale_updates` keeps its type and message format. **No new vocabulary.**

## Dependencies & order

1. `installed_version.py` plus its tests first — independently verifiable with zero risk to existing behaviour.
2. Then migrate the two notice readers together; a partial migration leaves them disagreeing about what `current` means, which is the failure mode of rounds 1–3 on PR #154.
3. Then the `update_server` baseline write.
4. CHANGELOG last.

No new runtime dependency: both probes are stdlib filesystem + `json`.

## Verification

```bash
cd <worktree>
uv run pytest tests/test_installed_version.py -q
uv run pytest tests/test_tools.py -q -k 'notice or stale or installed'

# RED proof — required. Revert ONLY the src changes with a targeted
# `git diff > patch` + `git apply -R`. NEVER `git checkout HEAD -- .`
# (it has destroyed uncommitted work in this repo).

uv run pytest -q          # baseline 2553 + new, 3 skipped, 0 failed
uv run ruff check . && uv run ruff format --check src/ tests/ && uv run mypy src
```

Then verify against the **real** cache manually, outside the suite, since the whole point is matching reality:

```bash
uv run python -c "from pmcp.manifest.installed_version import installed_version; \
print(installed_version('npm','@brightdata/mcp'))"   # expect 2.11.1
```

Edge cases: package absent from cache; multiple cache entries for one package (pick the newest by mtime — document the choice); scoped npm names; PEP 503 normalisation; unreadable cache dir (permissions); `uv cache dir` failing.

## Acceptance criteria

- [ ] `installed_version('npm', '@brightdata/mcp')` returns the version present in the npx cache, proven against a synthetic tree and confirmed once by hand against the real cache.
- [ ] A server whose installed version cannot be determined produces **no** notice — proven by `test_notice_silent_when_installed_version_unavailable`.
- [ ] A `.mcp.json` override naming a different package than the manifest compares the **configured** package's installed version — proven by `test_package_change_compares_the_configured_package`, failing when the src change is reverted.
- [ ] Full suite ≥2553 + new tests passed / 3 skipped / 0 failed; ruff, format, mypy clean; `## [Unreleased]` CHANGELOG entry present.

## Non-goals

- **docker and cargo.** 9 of 107 servers are unrecognised and none are docker/cargo in the manifest today. `installed_version` returns `None` for them, so they simply produce no notice — the honest degradation.
- **Making the cache authoritative.** If a user runs a server through a pinned global install rather than the runner cache, this reads the cache entry, which may not be what ran. Bounded by the identity check and by failing closed; noted as a residual rather than solved.

## Open question for the board

When several npx cache entries contain the same package at different versions (different arg sets, e.g. with and without a flag), which is "installed"? The plan picks newest-by-mtime. The alternative is to return `None` on ambiguity — fewer notices, but every notice provably correct. Given five attempts have failed toward the "fabricated notice" side, the conservative option may be right.

## Execution Policy

- execute: effort=high, reason=sixth attempt at a defect class where five prior fixes each shipped a new hole; touches shared notice paths and introduces a new filesystem-dependent module.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```
