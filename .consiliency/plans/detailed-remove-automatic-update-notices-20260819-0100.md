# Detailed plan: remove the automatic update notices

## Task

Close Consiliency/pmcp#150 by removing the feature that produces the defect, rather than attempting a ninth fix.

Maintainer decision, taken after the advisor board rejected the eighth attempt 3/3 and explicitly recommended removal. `gateway.update_server` keeps working unchanged — it reports what it finds when the operator asks it to update something. What goes away is the gateway *volunteering* a claim it cannot substantiate.

## Research summary

**Why removal rather than repair.** Eight attempts failed at one joint: pmcp cannot observe which package artifact a running server is executing. Not from `descriptions_cache.version` (an upstream snapshot written at describe time), not from `serverInfo.version` (an *implementation* version — FastMCP 1.x reports the SDK's, mcp 2.x defaults to `""`), not from the npx/uv runner caches (no server→entry binding; one package was observed cached at 1.2.0, 1.2.1 and 1.2.2 concurrently), and not from spawn argv (npm resolves *after* `create_subprocess_exec` returns; a live `npx` tree exposes no `_npx` path in any descendant).

The board's verdict on the only remaining method — running the package's own `--version` — was that it is disqualifying: it is a **full launch** of the server's argv with `sanitized_subprocess_env` injecting **that server's real credentials**, on a schedule. For a manifest entry using `@latest` it could execute newly published code with those credentials before the operator chose to update. Measured: probing one flag-ignoring server spawned 7 processes and opened 5 sockets. codex left exactly one door open — notices would be defensible *"unless a server exposes trustworthy version metadata without"* execution. Nothing in MCP does today.

**The surface to remove, enumerated from source.**

Emission sites (3 tool outputs):
- `catalog_search` — builds `stale_updates` from `_stale_check_cache` (`handlers.py:1695-1709`), attached at `1718`.
- `describe` — `update_warning = await self._get_update_warning(...)` (`handlers.py:1750`), attached at `1832`.
- `invoke` — same call at `1982`, attached at six return paths (`2069, 2104, 2153, 2188, 2238`).

Machinery:
- `_stale_check_cache` (`1093`), `_stale_index_interval_seconds` (`1097`), `_stale_index_task` (`1098`)
- `start_stale_indexer` / `stop_stale_indexer` (`1319`, `1325`), called from `server.py:808` and `server.py:967`
- `_stale_indexer_loop` (`1332`), `_run_stale_index` (`1346`), `_get_update_warning` (`3630`)
- `_effective_notice_target`, `_notice_package_matches_cache` — added during the #154 attempts, used only by the above

**Public schema fields** — this is a protocol-surface change, not an internal cleanup:
- `CatalogSearchOutput.stale_updates` (`types.py:639`)
- `SchemaCard.update_warning` (`691`)
- `InvokeOutput.update_warning` (`754`)
- `ProvisionOutput.update_warning` (`1204`)

**Correction, traced before boarding:** `provision` is a **fourth emission site**, not a stray field. It calls `_get_update_warning` at `handlers.py:4188` and attaches `update_warning` at **ten** further return paths (`4200, 4228, 4250, 4277, 4298, 4326, 4339, 4358, 4387, 4411`).

Total attachment points are therefore **16**, not the six this plan first documented: `describe` 1, `invoke` 5, `provision` 10. A removal that stops at `describe`/`invoke` leaves `provision` calling a deleted method — the exact half-removal failure this plan warns about, which I nearly committed.

**Documentation:** `README.md:480` states these tools "may return `update_warning` when a newer package version is detected."

## Changes

### `src/pmcp/tools/handlers.py` (modify)

- `_get_update_warning` — **delete**.
- `_run_stale_index`, `_stale_indexer_loop`, `start_stale_indexer`, `stop_stale_indexer` — **delete**.
- `_stale_check_cache`, `_stale_index_interval_seconds`, `_stale_index_task`, `_stale_check_ttl_seconds` — **delete**.
- `_effective_notice_target`, `_notice_package_matches_cache` — **delete**. Second correction found while boarding: these have **zero references anywhere in `src/` or `tests/`**. The plan said "used only by the above", which was wrong — they were never wired in at all, and are already-dead residue from the abandoned #154 attempts. Deleting them is unrelated to this feature's behaviour.
- `catalog_search` — **modify** — drop the `stale_updates` construction and stop passing it.
- `describe` / `invoke` / `provision` — **modify** — drop the `_get_update_warning` calls and every `update_warning=` argument. Counts matter here: `describe` 1, `invoke` 5, `provision` 10 — **16 attachment points**. Missing one leaves a call to a deleted method.
- `update_server` — **modify** — remove writes to `_stale_check_cache`, keep everything else. Its own probe-and-report behaviour is explicitly retained.

### `src/pmcp/server.py` (modify)

- `start_stale_indexer()` call (`~808`) and `stop_stale_indexer()` call (`~967`) — **delete**. These are the only external callers; leaving them would break startup.

### `src/pmcp/types.py` (modify)

- `CatalogSearchOutput.stale_updates`, `SchemaCard.update_warning`, `InvokeOutput.update_warning`, `ProvisionOutput.update_warning` — **delete**.

**Removing a field from a public output model is the load-bearing decision in this plan.** A client reading `update_warning` gets `None` today when there is no notice, so absence is already a normal case; but a client doing `"update_warning" in result` would see a behaviour change. See the open question.

### `tests/` (modify)

- `tests/test_tools.py` — **delete** the tests that assert notice emission (the `TestStaleIndexer` class and the notice tests added across the #154 attempts). Keep every `update_server` test: that path is unchanged and must be proven so.
- **add** — `test_no_update_warning_field_in_outputs`: `describe`, `invoke` and `catalog_search` outputs do not carry the removed fields. Asserts the removal is complete rather than partial — a half-removal that leaves one emission site is the failure mode to guard.
- **add** — `test_no_background_indexer_task_is_started`: booting the gateway creates no stale-index task. Pins that the loop is gone rather than merely unused.
- **add** — `test_update_server_still_reports_versions`: the on-demand path still works. This is what the maintainer is keeping and it must not regress silently.

### `CHANGELOG.md` (modify)

Entry under `## [Unreleased]`. **Mandatory** — `main` is protected and the `changelog` check fails any PR touching `src/`. Must be a `### Removed` entry naming the removed fields, since this is a breaking change to tool output, and must say plainly *why*: the gateway could not determine which version a server was actually running, so the notice could be wrong in both directions.

### `README.md` (modify)

Line ~480 — **delete** the sentence promising `update_warning`, and state that update information comes from `gateway.update_server` on request.

## Frozen vocabulary / protocol check

This **does** change the protocol surface: four fields are removed from three tool outputs. They are removals of optional fields that are already `None` in the common case, and no field is renamed or retyped. No new vocabulary is introduced. Flagged explicitly because every prior plan in this series could honestly claim "no protocol change" and this one cannot.

## Dependencies & order

1. Remove the emission sites first (`catalog_search`, `describe`, `invoke`) — the schema fields cannot go while writers remain.
2. Remove the background machinery and the `server.py` lifecycle calls together — a deleted method with a live caller breaks startup.
3. Remove the schema fields.
4. Delete the obsolete tests; add the three new ones.
5. README, then CHANGELOG last.

No new dependency. No migration: `_stale_check_cache` is in-memory and rebuilt per process.

**Blast radius confirmed by grep:** every reference to the deleted machinery lives in `src/pmcp/tools/handlers.py` — nothing else in `src/` touches it. But **15 test references** depend on `_stale_check_cache`, so the test deletions are a larger share of this change than the source deletions, and each must be classified as "asserts the removed feature" (delete) or "asserts `update_server`" (keep).

## Verification

```bash
cd <worktree>
uv run pytest tests/test_tools.py -q -k 'update_server or catalog_search or describe or invoke'
uv run pytest -q            # baseline 2555 minus deleted tests plus 3 new; 0 failed
uv run ruff check . && uv run ruff format --check src/ tests/ && uv run mypy src

# Completeness greps — a partial removal is the failure mode here:
grep -rn '_stale_check_cache\|_stale_index\|_get_update_warning\|update_warning\|stale_updates' src/ || echo "clean"
```

Then boot the gateway and confirm `describe`/`invoke`/`catalog_search` return successfully with no notice fields, and that `gateway.update_server` still reports a version.

Edge cases: `ProvisionOutput.update_warning` may have a writer outside the three known sites; the six `invoke` return paths; any test asserting the *absence* of a warning that would now fail on a missing attribute.

## Acceptance criteria

- [ ] No code path emits an update notice — proven by `test_no_update_warning_field_in_outputs` and by the completeness grep returning nothing.
- [ ] No background indexer task is created at startup — proven by `test_no_background_indexer_task_is_started`.
- [ ] `gateway.update_server` still probes and reports versions on request — proven by `test_update_server_still_reports_versions` and by the retained `update_server` tests passing unchanged.
- [ ] Full suite passes with 0 failures; ruff, format, mypy clean; `### Removed` CHANGELOG entry present; README no longer promises `update_warning`.

## Non-goals

- **Changing `gateway.update_server`.** Its probe, restart-gating and pin refusal all stay. Only its writes to the deleted cache are touched.
- **Deprecating rather than removing.** Considered and rejected as the default: keeping the fields as permanently-`None` would preserve a schema promise pmcp no longer fulfils. Raised as the open question below.

## Open question for the board

Removing four public output fields is a breaking change for any consumer reading them. Three options, and I want the board's view rather than my own default:

1. **Remove outright** (this plan). Cleanest; breaks a consumer doing `"update_warning" in result`.
2. **Retain the fields, always `None`.** Non-breaking, but leaves a documented field that can never be populated — arguably worse, since it promises a capability that no longer exists.
3. **Remove the emission, retain the fields for one release with a deprecation note.** Slower, and only worthwhile if there is evidence of external consumers.

pmcp is at 2.1.1 and these fields are documented in the README, so a consumer plausibly reads them. My inclination is (1) with a clear `### Removed` entry, because a field that is structurally always-`None` is a worse contract than an absent one — but this is a judgement about consumers I cannot see.

## Execution Policy

- execute: effort=medium, reason=mechanical deletion across a known surface, but it spans four models, six `invoke` return paths and two server lifecycle calls, where a partial removal breaks startup or leaves a dangling reference.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```
