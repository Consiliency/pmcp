# Detailed plan: record the installed version at spawn time, and re-check long-running servers

## Task

Fix Consiliency/pmcp#150 — the gateway fabricates "update available" notices, or hides real ones — by recording which version a server resolved to **at the moment pmcp launched it**, rather than inferring it afterwards.

Seventh attempt. Maintainer decision, after the runner-cache plan was rejected 3/3: **record at spawn**, plus **a periodic guard so a long-running server does not keep a stale baseline forever**.

## Research summary

**Why the previous six failed, in one line each.** Rounds 1–3 (PR #154) patched one notice reader while another kept serving a poisoned tuple. Round 4 (re-key the memo) still sourced `current` from `descriptions_cache.version`. Round 5 (`serverInfo.version`) is an *implementation* version — FastMCP 1.x reports the SDK's, mcp 2.x defaults it to `""`. Round 6 (runner caches) read real installed versions but could not bind a **configured server** to the **cache entry it runs**: verified on this machine, the npx cache holds `@consiliency/agent-board-mcp` at 1.2.0, 1.2.1 *and* 1.2.2 concurrently, and `npm` at three majors.

**Why spawn-time capture is different.** At `client/manager.py:1365` pmcp calls `create_subprocess_exec(local_config.command, *local_config.args, ...)`. It knows the exact argv, cwd and env of the process it is starting. Provenance is therefore **by construction** rather than by inference — there is no scan, no mtime heuristic, and no ambiguity about which cache entry or install tree is in play, because the resolution is the one pmcp itself performed. Every prior attempt tried to reconstruct this fact after the event; this records it at the only moment it is known for certain.

`ManagedClient` (`manager.py:485`) already carries per-connection state (`config`, `process`, transport handles) and is the natural home.

**The gap the maintainer identified, confirmed.** The existing background sweep (`_stale_indexer_loop`, `handlers.py:1332`) runs hourly (`_stale_index_interval_seconds = 3600`) and re-checks **upstream latest**. But a spawn-time installed version is captured only at spawn. A server started once and left running for weeks keeps that baseline indefinitely — nothing respawns it, so nothing re-reads it, and the gateway would compare a weeks-old installed version against today's upstream and be confidently wrong in the *quiet* direction (claiming an update that the operator may have already applied by restarting elsewhere, or missing drift entirely).

So spawn-time capture alone is necessary but not sufficient; the guard is part of the fix, not an optional extra.

**Existing scheduling.** `_stale_indexer_loop` already exists in-process, so the guard extends it rather than adding new machinery. `.github/workflows/maintenance.yml` is push/PR-triggered, not `schedule:`-triggered, and is a *repo* maintenance hook — wrong layer for a runtime gateway concern.

## Changes

### `src/pmcp/client/manager.py` (modify)

- `ManagedClient` — **modify** — add `installed_version: str | None = None` and `installed_version_observed_at: float | None = None`. Per-connection, discarded on disconnect, which is correct: the fact is only true of *this* process.
- `_connect_stdio` (spawn site, ~`manager.py:1365`) — **modify** — after a successful spawn, resolve the version for the argv just launched and store it on the `ManagedClient`. Best-effort: any failure leaves `None` and is logged at debug, never raises. A notice is advisory; a failed lookup must not break a connection.
- `resolve_spawned_version(command, args, *, cwd, env)` — **add** (new helper, this module or a small sibling) — given the *exact* argv pmcp used, return the resolved installed version. For `npx`, locate the cache entry created for that argument set; for `uvx`, the environment for that spec. **Unlike attempt 6 this is not a scan-and-guess**: the argv is known, so the lookup is a direct resolution of that invocation, and on any ambiguity it returns `None` rather than picking a candidate.
- `get_installed_version(server_name)` — **add** — accessor for the handlers layer; returns the value only for a currently-ONLINE server.

### `src/pmcp/tools/handlers.py` (modify)

- `_get_update_warning` — **modify** — take `current` from the client manager's spawn-time value. **No fallback to `descriptions_cache.version`** — the board's B1 finding on the last plan was that keeping it means still emitting inferred-version notices. When no spawn-time version exists, return `None` (silence).
- `_run_stale_index` — **modify** — same source, same no-fallback rule.
- `_stale_check_cache` — **modify** — record `version_source="spawn"` alongside the existing `pkg_type`, so a reader can never mistake an inferred value for an observed one.
- `_stale_indexer_loop` / `_run_stale_index` — **modify** — **the guard**. For each ONLINE server whose `installed_version_observed_at` is older than a freshness bound (new `_installed_version_max_age_seconds`, default 24h), re-resolve the installed version for that connection's argv and update it. This is what stops a long-running server keeping a weeks-old baseline. Re-resolution is a filesystem read of a known location — no respawn, no restart, no disruption to the running server.
- `update_server` success path — **modify** — after a successful restart the server has respawned, so the new spawn-time value is already correct; drop the old write of the probed upstream latest into `desc_entry.version`.

### `tests/test_client_manager.py` (modify)

- **add** — `test_spawn_records_installed_version`: a stdio spawn records a version and a timestamp on the `ManagedClient`.
- **add** — `test_spawn_version_failure_does_not_break_connection`: resolution raising leaves `None` and the connection still succeeds. This is the safety property — pins that an advisory feature cannot take down a server.
- **add** — `test_ambiguous_resolution_returns_none`: two candidate matches → `None`, not a guess. Directly pins the failure of attempt 6.

### `tests/test_tools.py` (modify)

Every test asserts an **observable notice outcome**, never a mutated field. Multiple prior tests passed while their bug survived.

- **add** — `test_notice_uses_spawn_time_version`: spawn recorded `1.0.0`, upstream `2.0.0` → notice naming both.
- **add** — `test_no_notice_without_a_spawn_time_version` (**pins B1**): no spawn-time version *and* an orderable `descriptions_cache.version` present → still **no notice**. The last plan's test omitted the orderable-fallback case and would have passed while the regression lived.
- **add** — `test_package_change_compares_the_configured_package` (**the #150 case**): manifest names A, `.mcp.json` names B, server spawned from B → comparison is B-vs-B or silence, never A-vs-B.
- **add** — `test_guard_refreshes_a_stale_spawn_time_version`: a connection whose `observed_at` is older than the bound gets re-resolved by a sweep pass, and a notice that was wrong becomes right. **This is the maintainer's requirement** and must fail without the guard.
- **add** — `test_guard_leaves_fresh_versions_alone`: within the bound, no re-resolution (no needless filesystem work every hour).

## Documentation impact

- `CHANGELOG.md` — **modify** — entry under `## [Unreleased]`. **Mandatory**: `main` is protected and the `changelog` check fails any PR touching `src/` without one. Must state that notices now compare the version the server was actually started with, that a server whose version could not be determined produces no notice, and that long-running servers are re-checked periodically.
- `README.md` — **check** whether update-notice behaviour is documented; update if so, else record `no doc footprint`.

## Frozen vocabulary / protocol check

No protocol surface changes. `ManagedClient` is internal; `_stale_check_cache` is private in-memory state with no serialised form; `CatalogSearchOutput.stale_updates` keeps its type and message format. **No new vocabulary, no new tool parameter, no schema change.**

## Dependencies & order

1. `resolve_spawned_version` + `ManagedClient` fields + spawn capture, with manager tests. Independently verifiable and inert — nothing reads the value yet, so this cannot change notice behaviour.
2. Migrate both notice readers together, removing the fallback. A partial migration leaves them disagreeing about what `current` means — the failure mode of rounds 1–3.
3. The guard in the sweep.
4. CHANGELOG last.

No new runtime dependency.

## Verification

```bash
cd <worktree>
uv run pytest tests/test_client_manager.py -q -k 'spawn or installed'
uv run pytest tests/test_tools.py -q -k 'notice or spawn or guard or stale'

# RED proof — required. Revert ONLY the src changes via a targeted
# `git diff > patch` + `git apply -R`. NEVER `git checkout HEAD -- .`
# (it has destroyed uncommitted work in this repo).
# The guard test in particular MUST fail without the guard, or it is not
# testing the maintainer's requirement.

uv run pytest -q          # baseline 2553 + new, 3 skipped, 0 failed
uv run ruff check . && uv run ruff format --check src/ tests/ && uv run mypy src
```

Then confirm against a real server, outside the suite — the point is matching reality:

```bash
# start a server through the gateway, then verify the recorded version equals
# what that package actually resolved to.
```

Edge cases: server never started (lazy) → no version → silence; remote server (no local package) → silence; spawn succeeds but resolution fails; server restarted (value must refresh); two servers sharing one package; `observed_at` exactly at the bound.

## Acceptance criteria

- [ ] A stdio spawn records the installed version and timestamp on its `ManagedClient` — proven by `test_spawn_records_installed_version`.
- [ ] A failed version resolution never breaks a connection — proven by `test_spawn_version_failure_does_not_break_connection`.
- [ ] No notice is emitted without a spawn-time version, **even when an orderable `descriptions_cache.version` exists** — proven by `test_no_notice_without_a_spawn_time_version`, failing when the src change is reverted.
- [ ] A `.mcp.json` override naming a different package than the manifest never yields a cross-package comparison — proven by `test_package_change_compares_the_configured_package`.
- [ ] A long-running server's baseline is refreshed by the periodic guard rather than pinned at spawn — proven by `test_guard_refreshes_a_stale_spawn_time_version`, failing without the guard.
- [ ] Full suite ≥2553 + new tests passed / 3 skipped / 0 failed; ruff, format, mypy clean; `## [Unreleased]` CHANGELOG entry present.

## Non-goals

- **Restarting servers to refresh a version.** The guard re-reads from disk; it never respawns a running server. Restarting to satisfy an advisory notice would be a worse cure than the disease.
- **docker / cargo.** `resolve_spawned_version` returns `None` for them, so they produce no notice — the honest degradation. 9 of 107 manifest servers are unrecognised today.
- **Lazy servers that have never started.** No spawn means no version means no notice. Called out because it is a real coverage reduction versus today's (wrong) notices.

## Open question for the board

The guard re-reads the installed version for a *running* process. If the on-disk package has been upgraded underneath a running server, the re-read reports the **new on-disk** version while the process still serves the **old** code. Is that the right reading? The alternative is to treat spawn-time as immutable for the life of the connection and instead notify "this server is running an older version than what is installed — restart it", which is arguably the more actionable message and cannot be confidently wrong. I lean toward the latter but want the board's view, since it changes what the guard is *for*.

## Execution Policy

- execute: effort=high, reason=seventh attempt at a defect class where six prior fixes each shipped a new hole; touches the connection spawn path, where a mistake breaks server startup rather than merely a notice.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```


---

## Board review — REJECTED. Do not implement as written.

Boarded before implementation (2 usable legs, both blocking). I independently reproduced the central finding before reading the verdicts.

### B1 (fatal) — "argv is authoritative provenance" is false

The plan's entire basis. `create_subprocess_exec` returns once **`npx` itself** has started — before that runner has selected, fetched or launched the underlying package. npm may resolve to a *local*, *global*, or *cache-backed* copy; only the cache branch produces an `_npx` directory at all. So `resolve_spawned_version(command, args, ...)` would still be reconstructing runner behaviour after the fact — the very scan-and-guess that sank attempt 6, relocated to the spawn path where a mistake is more expensive.

Verified independently on this machine before the verdicts arrived: a live `npx -y @eslint/mcp` process tree exposes **no** `_npx` path anywhere — not in any descendant's `cmdline`, not in `cwd`, not in open file descriptors. There is no observable link from the process pmcp started to the cache entry backing it.

```
scanning all node processes for _npx references...
  (none)
--- any process whose cwd or fds touch _npx ---
  (none)
```

So the claim "provenance is by construction rather than inference" does not hold. Knowing the argv tells pmcp what it *asked for*, not what npm *chose*.

### B2 — the A→B cross-package defect survives this plan

The plan changes only where `current` comes from. Both notice writers still obtain `latest` via `_get_server_config_for_update` (`handlers.py:3622`), which prefers the **manifest** and ignores the running `.mcp.json` override. So B's observed installed version can still be compared against **A's** upstream latest — the original #150 defect, intact.

Adding `version_source="spawn"` records provenance without *binding* it, and the server-keyed stale cache can still retain A's latest across a reconnect to B. Both sides of the comparison must come from the same effective configuration.

### What this means after seven attempts

Every approach so far has failed at the same joint: pmcp cannot observe, for a running server, **which package artifact is actually executing**. Not from the descriptions cache (upstream snapshot), not from `serverInfo` (implementation version), not from the runner caches (no server→entry binding), and not from spawn argv (npm resolves after the call returns).

The remaining honest options are narrower than any attempted so far:

- **Ask the process.** Run the server's own `--version` on the identical argv. It is the only method that observes the artifact that actually ran. Costs a subprocess per check.
- **Only what pmcp installed.** Restrict notices to servers pmcp installed itself, recording the resolved version at install time. Correct by construction, but excludes hand-configured `.mcp.json` servers — the exact case #150 is about.
- **Remove the feature.** No automatic notice can be wrong if there is no automatic notice; `gateway.update_server` still reports on demand.

Note the second and third are the only ones that cannot be confidently wrong.
