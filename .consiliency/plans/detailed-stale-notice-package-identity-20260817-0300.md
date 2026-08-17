# Detailed plan: key the stale-update caches on package identity

## Task

Fix Consiliency/pmcp#150 structurally, after three board rounds rejected three successive patch-level attempts (PR #154, closed). The narrow #151 fix from that PR is carried forward here unchanged in intent.

The recurring defect: **the gateway compares a version recorded for one package against the upstream latest of a different package**, producing a fabricated "update available" notice or hiding a real one. Every fix so far has patched one reader while another kept serving the stale tuple.

## Research summary

Reconnaissance done inline (all files in session from PR #147 and #154). The structural findings, verified at PR #154 head `85918b1`:

**Two caches carry a package identity neither is keyed on.**

1. `self._stale_check_cache: dict[str, tuple[float, str | None, str | None]]` (`handlers.py:1088`) — keyed by **server name**, holding `(timestamp, current, latest)`. Nothing in the key or the tuple records *which package* those versions describe.
2. `self._descriptions_cache.servers[name]` — a `GeneratedServerDescriptions` carrying both `package` and `version`. It *does* record the package, but the version baseline is only ever written by `refresh_all` (manifest-only) and by `update_server` on success (`handlers.py:5394`).

**Three readers, three different contracts** — this is why patching is not converging:

| Reader | Line | TTL check | Identity check |
|---|---|---|---|
| `_run_stale_index` (background sweep) | `1347` | yes — **before** resolving the target | patched in #154 |
| `_get_update_warning` | `3790` | yes | patched in #154 |
| `catalog_search` `stale_updates` | `1698-1704` | **no** | **no** |

`catalog_search` iterates raw tuples and emits a notice for anything where `is_version_newer(current, latest)`. It is the reader that kept leaking in every round.

**Why the sweep's ordering defeats eviction.** `_run_stale_index` skips fresh entries (`1348`) *before* it resolves the effective target, so a poisoned-but-warm entry is never re-examined and never evicted — it simply survives its full 6h TTL (`1089`) while `catalog_search` serves it.

**Why the #154 "self-heal" produced permanent silence.** Re-pointing set `package=B, version=""`; `_get_update_warning` returns early on an empty version (`3782-3787`) *before* any lookup, and the sweep skips fresh entries before resolving — so no path ever observes a version for B. Reproduced at exact head: three consecutive passes, three `None`. And `update_server` writing B's version (`5394`) is then erased by the next re-point, so a real update to B stays hidden.

**Why "wait for `pmcp refresh`" is not a recovery.** `refresh_all` targets `servers or list(manifest.servers.keys())` (`refresher.py:335`) — manifest entries only. A server whose effective package comes from a `.mcp.json` override never regains a matching entry.

## Changes

### `src/pmcp/tools/handlers.py` (modify)

- `_stale_check_cache` type + initialisation (`~1088`) — **modify** — re-key from `dict[str, tuple[...]]` to `dict[tuple[str, str], tuple[float, str | None, str | None]]`, keyed by `(server_name, package_name)`. This is the core of the fix: **a cross-package entry becomes unrepresentable rather than something each reader must remember to detect**. The three readers stop needing an identity check because the key carries it.
- `_stale_cache_key` — **add** — small helper deriving `(server_name, package)` from a server name plus resolved command/args via the existing `detect_package_type`. Single place that decides identity, so the readers cannot drift apart again (the failure mode of every prior round).
- `_run_stale_index` (`~1339`) — **modify** — resolve the effective target **before** the TTL check, then look up by the composite key. Reordering is load-bearing: it is what stops a warm poisoned entry surviving unexamined.
- `_get_update_warning` (`~3713`) — **modify** — same composite-key lookup. Delete the `_notice_package_matches_cache` / `_repoint_cache_entry_to_effective_package` pair added in #154; both become unnecessary, and the second was actively harmful (it erased a good version baseline).
- `catalog_search` `stale_updates` (`~1698`) — **modify** — this is the reader that leaked in all three rounds. It must apply the **same TTL** as the other two (entries older than `_stale_check_ttl_seconds` are not emitted) and, because the key now carries the package, it can no longer attribute an A-derived tuple to B. Extract the shared predicate so all three readers provably agree.
- `update_server` success path (`~5442-5452`) — **modify** — write the repopulated entry under the composite key. Its version write to `desc_entry.version` (`5394`) stays; the erasure problem disappears with `_repoint_...`.
- `_effective_notice_target` (added in #154, `~3640`) — **keep** — the `.mcp.json`-over-manifest precedence and the `is_server_allowed` gate were accepted in every round. Unchanged.
- `update_server` drift recheck (`~5323-5350`) — **keep** — the #151 fix as reworked: recheck after the probe *and* after the version lookup, compared via `_refresh_config_unchanged` (covers `cwd` and effective `env`, not just argv). Accepted by round 3.

### `tests/test_tools.py` (modify)

Every test must assert an **observable notice outcome**. Three rounds were passed by tests that asserted a queried argument or a mutated label while the bug survived; that shape is banned here.

- **add** — `test_catalog_search_never_serves_a_cross_package_tuple`: pre-populate a warm A-derived tuple, switch config to B, assert `catalog_search().stale_updates` is empty. This is the exact case that survived rounds 1–3.
- **add** — `test_catalog_search_applies_the_same_ttl_as_other_readers`: an expired entry must not be emitted.
- **add** — `test_notice_resumes_after_package_change`: config switches A→B; assert that within the normal flow a **real notice for B is eventually emitted** (not merely that a label changed). This is the assertion whose absence let the permanent-silence bug pass.
- **add** — `test_update_server_version_survives_subsequent_warning`: after a successful override update writes B's version, a following `_get_update_warning` must not erase it. Pins the erasure codex reproduced.
- **add** — `test_local_to_remote_override_evicts_stale_tuple`: local A → remote override; assert no A notice from any reader. Round 3 found this leaking.
- **keep** — the accepted #151 tests (`aborts_when_config_changes_during_probe`, `aborts_on_env_only_drift`, `restart_consumes_the_confirmed_config`) and the accepted #150 precedence/policy/remote tests.

## Documentation impact

- `CHANGELOG.md` — **modify** — entries under `## [Unreleased]` for #150 and #151. **Mandatory**: `main` is protected and the `changelog` check fails any PR touching `src/` without it. The #150 entry must describe the actual behaviour — notices resume once a version for the new package is observed — and must **not** repeat the `pmcp refresh` recovery claim, which I verified is false for override-named packages.

## Frozen vocabulary / protocol check

`_stale_check_cache` is private in-memory state with no serialised form: it is not persisted, not in `types.py`, and not exposed in any tool schema. Re-keying it changes **no** protocol surface. `CatalogSearchOutput.stale_updates` keeps its existing type (`list[str] | None`) and message format. **No new vocabulary is introduced.**

## Dependencies & order

1. Add `_stale_cache_key`, then re-key the cache and migrate all three readers **in one commit** — a half-migrated cache mixes key shapes and would fail confusingly.
2. Reorder `_run_stale_index` (resolve before TTL) in the same commit; eviction is not what fixes it, ordering is.
3. Remove `_notice_package_matches_cache` and `_repoint_cache_entry_to_effective_package`.
4. Tests, each proven RED against the pre-fix source.
5. CHANGELOG last.

No external dependencies. No migration: the cache is in-memory and rebuilt on start.

## Verification

```bash
cd <worktree>

uv run pytest tests/test_tools.py -q -k 'catalog_search or notice or stale or update_server'

# RED proof — required; a passing test is not evidence the test works. Revert
# ONLY src/pmcp/tools/handlers.py via a targeted `git diff > patch` + `git apply -R`.
# NEVER `git checkout HEAD -- .` (it has destroyed uncommitted work in this repo).
# Each new test must fail against the pre-fix source; record the actual output.

uv run pytest -q                     # baseline 2536 + new tests, 3 skipped, 0 failed
uv run ruff check .
uv run ruff format --check src/ tests/
uv run mypy src
```

Beyond the suite, reproduce the round-3 defect by hand and confirm it is gone: drive `_get_update_warning` three consecutive times across an A→B config switch and assert a notice for B is eventually produced (the failing transcript printed `None` three times).

Edge cases: server in manifest only; `.mcp.json` only; both with the same package (must not regress — the common case); remote override; policy-denied; absent everywhere; an entry whose package cannot be detected (`detect_package_type` → `None`).

## Acceptance criteria

- [ ] No reader can emit a notice comparing versions from two different packages — proven by `test_catalog_search_never_serves_a_cross_package_tuple` and `test_local_to_remote_override_evicts_stale_tuple`, both failing when the src change is reverted.
- [ ] After a package change, notices **resume** rather than stopping permanently — proven by `test_notice_resumes_after_package_change` asserting an emitted notice, not a mutated field.
- [ ] A version baseline written by a successful `update_server` is not erased by a later notice check — proven by `test_update_server_version_survives_subsequent_warning`.
- [ ] `catalog_search` honours the same TTL as the other two readers — proven by `test_catalog_search_applies_the_same_ttl_as_other_readers`.
- [ ] Full suite ≥2536 + new tests passed / 3 skipped / 0 failed, with `ruff check`, `ruff format --check src/ tests/`, `mypy src` clean, and a `## [Unreleased]` CHANGELOG entry present.

## Execution Policy

- execute: effort=high, reason=three prior board rounds each rejected a patch at this layer; the change re-keys shared cache state read by three call sites with differing contracts, and the failure mode is a silently wrong user-facing notice.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```
