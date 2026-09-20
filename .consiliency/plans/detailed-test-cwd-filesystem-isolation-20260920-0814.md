# Detailed plan: isolate the test suite's filesystem context (cwd + ancestor chain) — #261 and the ~107-test npm artifact

## Task
Close the *filesystem-context* half of the hermeticity work in Consiliency/pmcp#235 — the part where a test's **cwd and its ancestor chain** leak the developer's real filesystem into assertions:

- **#261** — `tests/test_refusal_remedies.py::test_a_consent_refusal_reaches_the_operator_as_one_warning_naming_its_remedy` sees **two** consent-refusal warnings instead of one, the extra naming the developer's real `~/.pmcp/manifest.yaml`.
- **The ~107-test npm-identity artifact** — on a host where an ancestor of the effective cwd contains `package.json` or `node_modules`, ~107 npm-identity tests flip from resolving to refusing.

This is **slice A of three**. #235 exceeds the bounded-plan threshold (~4 concerns / ~35 files), so it is split; slices B (module-state resets) and C (deterministic time / #226) get their own plans. See *Out of scope* below.

## Execution Policy
- execute: effort=medium, reason=small diff but subtle ordering/opt-out semantics in an autouse fixture that every test inherits

## Research summary
Three Explore surveys plus direct reads. The two symptoms share one root: **production walks up from the effective cwd, and no fixture isolates cwd.**

- **#261's real mechanism** (not what the issue guessed): `manifest/loader.py::_find_project_manifest` anchors on `current = Path.cwd().resolve()` (`:633`) and stops at `home_root = Path.home().resolve()` (`:637`) — a guard added by #243 so a startup under `$HOME` doesn't prompt the operator to approve their own config. `tests/conftest.py:62 isolate_trust_store` is autouse and redirects `HOME` to a fake sibling of `tmp_path`, so `home_root` is **not an ancestor of the real cwd**; the guard never fires, the walk climbs past the real `/home/<user>`, finds the real `~/.pmcp/manifest.yaml`, and gates it as a **project** overlay (hence "Ignoring *project* manifest overlay"). **The isolation fixture causes the failure.** The walk already stops at `temp_root` (`:636`), so starting under tempdir fixes it. It is *not* import-time capture: `DEFAULT_USER_MANIFEST_PATHS` (`manifest/loader.py:27`) has no consumers, and `_overlay_manifest_paths()` (`:684`) resolves `Path.home()` at call time deliberately.
- **The npm walk is correct and must not change.** `npm_resolver.py::_has_local_prefix` (`:158`) iterates `(start, *start.parents)` (`~:178-186`) with **no** temp or home stop — deliberately, because its docstring replicates npm's own rule (`@npmcli/config/lib/index.js:695-716`, verified empirically). `/tmp/package.json` really would give npm a local prefix. Adding a stop would diverge pmcp from npm and weaken an S-01-adjacent guarantee. **The fix belongs in the test layer.**
- **Two distinct pollution entry points**, which is why this reproduces inconsistently: (1) tests that `monkeypatch.chdir(tmp_path)` walk up through `/tmp` — `conftest.py:14-16` records `/tmp/package.json` *and* `/tmp/node_modules` present on the dev host for #195; (2) tests that do **not** chdir walk up from the rootdir — in a `/mnt/<volume>/worktrees/...` checkout that reaches a `node_modules` at the volume root, which is the observed ~107-failure case. This host is currently clean at both `/tmp` and every ancestor of the repo, so the artifact is invisible here and glaring elsewhere.
- **No cwd-isolation fixture exists.** `tests/conftest.py` has exactly three autouse fixtures — `isolate_trust_store` (`:62`), `_reset_dotenv_provenance` (`:151`), `_no_live_npm_registry` (`:168`) — and none chdir. The `/tmp` hazard is captured only as a module docstring (`:3-28`): "documentation is not isolation."
- **Two constraints any autouse chdir must respect**: `conftest.py:89-92` records that `test_registry.py::test_default_cache_path_is_not_cwd_relative` uses `tmp_path` as its **stand-in for cwd** and asserts a `~`-derived path is not under it (this is why the fake home is a *sibling* of `tmp_path`, measured not stylistic); and `conftest.py:18-22` states a test asserting a refusal "must not rely on cwd unless cwd IS its subject." Both need an opt-out.
- **Already closed, do not plan work for it**: #235's "mark or stub the network test" (T-01) is done suite-wide — `conftest.py:169` `_no_live_npm_registry` is autouse and its finalizer (`:188-191`) *fails* any test reaching `registry.npmjs.org`; `:196` `fake_npm_registry` stubs the packument fetch; the only real-network tests (`test_integration.py:219/281/341`) carry `@pytest.mark.live` + `skip_no_servers` with the marker registered at `pyproject.toml:188`.

## Changes

### `tests/conftest.py` (modify)
- `isolate_cwd` — **add** — a new **autouse** fixture that `monkeypatch.chdir(...)` into a per-test directory under `tmp_path` (a dedicated child, e.g. `tmp_path / "cwd"`, so `tmp_path` itself stays usable as a test's own subject). This makes `_find_project_manifest`'s existing `temp_root` stop (`manifest/loader.py:636`) fire immediately, which is the whole #261 fix. Must be ordered **after** `isolate_trust_store` so the fake home is already established.
- `isolate_cwd` opt-out — **add** — honour a marker (e.g. `@pytest.mark.real_cwd`) that skips the chdir, for the two documented cases: a test whose **subject is cwd** (`test_registry.py::test_default_cache_path_is_not_cwd_relative`, per `conftest.py:89-92`) and refusal tests that deliberately rely on cwd (`conftest.py:18-22`). Register the marker in `pyproject.toml` alongside `live`.
- `assert_clean_ancestor_chain` — **add** — a **session-scoped autouse** guard that walks the ancestors of the effective test cwd (and of `tmp_path`'s root) for `package.json` / `node_modules` and, if any is found, fails **once** with a message naming the exact polluting path and explaining the consequence ("~107 npm-identity tests will invert to refusals"). This converts today's silent 107-failure cascade into one actionable error. It does **not** modify the production walk.
- module docstring (`:3-28`) — **modify** — replace the "documentation is not isolation" caveat with a pointer to the two new fixtures, keeping the #195 history note.

### `pyproject.toml` (modify)
- `[tool.pytest.ini_options] markers` — **add** — register `real_cwd` (mirroring how `live` is registered at `:188`) so the opt-out marker does not warn.

### Out of scope (deliberately — separate bounded plans)
- **Slice B — module-state resets**: new `clear_*` helpers for `registry.py:26/27 _IN_PROCESS_CACHE/_IN_PROCESS_TASKS`, `transport/http.py:55/67/103 _metrics/_rl_store/_prom_counters`, `npm_resolver.py:615 _resolver`; wire existing `clear_version_cache()` (`version_checker.py:2000`) and `reset_pmcp_introduced_keys()` (`env_store.py:305`, currently reset only in three per-file fixtures) into an autouse fixture.
- **Slice C — deterministic time / #226**: a `poll_until`/`eventually` helper (none exists), the #226 barrier fix (`test_client_manager.py:2156`, assertion `:2176`; sibling `:2235`), and the wall-clock threshold class.
- **#262** — policy discovery freezes `Path.home()` at import (`policy/policy.py:83-88`); latent, filed separately.
- **`npm_resolver._has_local_prefix`** — must NOT be changed; it faithfully replicates npm.

## Documentation impact
- `tests/conftest.py` module docstring — modify — as above; it is the suite's canonical hermeticity note and would otherwise describe a hazard that is now fixed.
- `CHANGELOG.md` — **none**. Test-only isolation change with no user-visible behaviour change; production code is untouched. (Conscious decision: the `changelog` CI gate requires an entry only for `src/` changes, and this plan touches no `src/`.)

## Dependencies & order
1. Register the `real_cwd` marker **before** the fixture uses it, or the opt-out warns.
2. `isolate_cwd` must run **after** `isolate_trust_store` (fixture ordering), since the fake home must exist before the chdir for the `$HOME`-vs-cwd relationship to be the intended one.
3. The ancestor guard is independent and can land first — it is the cheapest way to make the ~107-failure mode self-explaining even before the chdir lands.
4. No dependency on slices B or C; all three are file-disjoint.

## Verification
`automation.suite_command`: `uv run pytest -q -p no:cacheprovider`

1. **#261 closes**: `uv run pytest -q -p no:cacheprovider "tests/test_refusal_remedies.py::test_a_consent_refusal_reaches_the_operator_as_one_warning_naming_its_remedy"` passes **on this host, which has a real `~/.pmcp/manifest.yaml`** (it fails on `main` today — that is the red-then-green).
2. **Mutation for #261**: remove the `chdir` from `isolate_cwd` → that test goes red again with the two-warning assertion (`assert 2 == 1`), proving the chdir is what closes it.
3. **Ancestor guard fires**: create `$(python -c 'import tempfile;print(tempfile.gettempdir())')/package.json`, run any npm-identity test, and assert the suite fails with the single named guard message rather than ~107 scattered refusals; remove the file and confirm green. (Reproduces the exact condition `conftest.py:14-16` documents.)
4. **Opt-out preserved**: `uv run pytest -q -p no:cacheprovider tests/test_registry.py::test_default_cache_path_is_not_cwd_relative` passes — the cwd-subject test must be unaffected (this is the `conftest.py:89-92` regression that a naive autouse chdir would cause).
5. **No regression**: full suite on this host shows **no new failures** versus the pre-change baseline captured on the same host in the same run session. Expect the count to *drop* if any ancestor is polluted.
6. `uv run ruff check tests/conftest.py` and `uv run ruff format --check tests/conftest.py`.

## Acceptance criteria
- [ ] `test_a_consent_refusal_reaches_the_operator_as_one_warning_naming_its_remedy` passes on a host that HAS a real `~/.pmcp/manifest.yaml`, and removing the `chdir` from `isolate_cwd` turns it red again.
- [ ] With `package.json` planted at the tempdir root, the suite fails with ONE guard message naming that path — not a cascade of npm-identity refusals.
- [ ] `test_registry.py::test_default_cache_path_is_not_cwd_relative` still passes (cwd-subject opt-out honoured), and `real_cwd` is registered in `pyproject.toml`.
- [ ] `src/` is untouched — in particular `npm_resolver._has_local_prefix` is unchanged, preserving fidelity to npm's local-prefix rule.
- [ ] Full suite shows no new failures against a same-host baseline.
