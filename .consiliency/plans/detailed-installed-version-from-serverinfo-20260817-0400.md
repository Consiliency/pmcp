# Detailed plan: probe the installed version directly from `initialize`

## Task

Fix Consiliency/pmcp#150 at its root, after four rejected attempts (PR #154 closed; the re-keying plan boarded 3/3 DISAGREE).

The board's decisive finding: **nothing in pmcp ever observes an installed version.** `get_package_version` returns *upstream latest* only, and `GeneratedServerDescriptions.version` is itself an upstream snapshot taken at describe time. So the "update available" comparison is upstream-latest-at-describe-time vs upstream-latest-now, which is only meaningful while the described package and the configured package are the same — the exact assumption #150 breaks.

Maintainer decision: **probe the installed version directly**, rather than continuing to infer it.

## Research summary

The probe already exists in the protocol and pmcp is discarding it.

`_send_initialize` (`client/manager.py`) reads the `initialize` result and stores `protocolVersion` into `managed.status.protocol_version` and `capabilities` into `managed.status.server_capabilities` — then ignores the rest of the same dict. That dict contains `serverInfo`.

Verified against the installed `mcp` library:

```
InitializeResult fields: ['meta', 'protocol_version', 'capabilities', 'server_info', 'instructions']
Implementation  fields: ['name', 'title', 'version', 'description', 'website_url', 'icons']
InitializeResult.server_info required: True
```

`serverInfo` is a **required** field carrying `name` and `version`. Every conforming server reports its own version on every connect, and pmcp throws it away. That is the installed-version source the previous four attempts lacked, and it needs no new subprocess, no new network call, and no new failure mode: it is a field already present in a handshake pmcp already performs and already parses.

Two consequences that shape the design:

- **The value is authoritative for what is *running*, not what is on disk.** It is reported by the live process, so it answers exactly the question the notice asks ("is the thing serving me out of date?") and is immune to the config-drift class of bug that #150/#151 are about — a restarted server re-reports.
- **It is self-declared and free-form.** The spec does not constrain the string to semver, and a server may report something `is_version_newer` cannot parse. The design must degrade to *silence*, never to a fabricated comparison.

`ServerStatus` (`types.py:429`) is the natural home: it already holds `protocol_version` and `server_capabilities` from the same handshake.

## Changes

### `src/pmcp/types.py` (modify)

- `ServerStatus` — **modify** — add `server_version: str | None = None` (and `server_title: str | None = None` if trivially available), beside the existing `protocol_version`/`server_capabilities` from the same handshake. Defaulted, so every existing construction site is unaffected.

### `src/pmcp/client/manager.py` (modify)

- `_send_initialize` — **modify** — after the existing `capabilities` block, read `result.get("serverInfo")` and store `version` (as `str`, when present and non-empty) into `managed.status.server_version`. Defensive: `serverInfo` is spec-required but this is untrusted downstream input, so a missing/malformed value leaves the field `None` rather than raising. This is the only place the value is captured; it refreshes automatically on every connect and reconnect.

### `src/pmcp/tools/handlers.py` (modify)

- `_installed_version(server_name)` — **add** — returns the live `server_version` for an ONLINE server via the client manager, else `None`. Single accessor so no caller re-derives it.
- `_get_update_warning` — **modify** — take `current` from `_installed_version(...)` when the server is online. Falls back to today's `descriptions_cache.version` **only** when the server is offline *and* the cache entry's `package` matches the effective config's package (the check added in #154, which was accepted in every round). Otherwise return `None`.
- `_run_stale_index` — **modify** — same source for `current`, same fallback rule.
- `catalog_search` `stale_updates` (`~1698`) — **modify** — the reader that leaked in all three prior rounds. It must apply the same TTL as the other two readers, which it currently does not. Extract the emit predicate so all three readers provably share one contract.
- `_stale_check_cache` — **modify** — store the version *source* alongside the tuple (`"installed" | "described"`), so a described-fallback entry can never be mistaken for an observed one by a later reader.
- `_notice_package_matches_cache` — **keep**, now only guarding the offline fallback path.
- `_repoint_cache_entry_to_effective_package` — **delete** — this was the #154 "self-heal" that set `version=""` and produced permanent silence, and that erased a good baseline written by `update_server`. With a real installed version there is nothing to re-point.
- `_effective_notice_target` — **keep** unchanged (precedence + `is_server_allowed`; accepted in every round).
- `update_server` drift recheck — **keep** unchanged (the #151 fix: recheck after probe *and* after version lookup, compared via `_refresh_config_unchanged`).

### `tests/test_tools.py` (modify)

Every test asserts an **observable notice outcome**. Three rounds were passed by tests asserting a queried argument or a mutated label while the bug survived; that shape is banned.

- **add** — `test_notice_uses_installed_version_from_server_info`: server reports `1.0.0` via `serverInfo`, upstream latest is `2.0.0` → a notice is emitted naming `1.0.0 -> 2.0.0`.
- **add** — `test_no_notice_when_installed_equals_latest`: reports `2.0.0`, latest `2.0.0` → silence.
- **add** — `test_package_change_does_not_fabricate_a_notice` (**the #150 case**): config switches A→B, server reports B's real version → the comparison is B-vs-B, and either a correct B notice or silence is emitted — never an A-vs-B comparison.
- **add** — `test_notice_resumes_after_package_change`: the criterion the permanent-silence bug failed. After an A→B switch, a real notice for B **is** emitted once the server reports its version. Asserts the emitted string, not a field.
- **add** — `test_unparseable_server_version_stays_silent`: server reports `"nightly-2026-08-17"` → no notice, no exception.
- **add** — `test_missing_server_info_stays_silent`: server omits `serverInfo` → no notice.
- **add** — `test_offline_fallback_requires_package_match`: offline server, cache describes A, config names B → silence.
- **add** — `test_catalog_search_applies_the_same_ttl`: expired entry is not emitted.
- **keep** — accepted #151 tests and the accepted #150 precedence/policy/remote tests.

### `tests/test_client_manager.py` (modify — or the nearest existing manager test module)

- **add** — `test_initialize_captures_server_version`: a handshake reporting `serverInfo.version` populates `ServerStatus.server_version`; a handshake omitting it leaves `None`.

## Documentation impact

- `CHANGELOG.md` — **modify** — entry under `## [Unreleased]`. **Mandatory**: `main` is protected and the `changelog` check fails any PR touching `src/` without it. Must state plainly that update notices now compare the **running server's reported version** against upstream latest, and that a server which does not report a usable version produces no notice.
- `README.md` — **modify if** it documents `gateway.health`/`describe` output shape, since `ServerStatus` gains a public field. Check before writing; if the field is not surfaced in documented output, state `no doc footprint` instead.

## Frozen vocabulary / protocol check

`ServerStatus` is a **public output model** (surfaced by `gateway.health`), so adding `server_version` is an additive schema change: new optional field, no field renamed, removed, or retyped. `serverInfo` is read from the existing `initialize` handshake — pmcp already parses `protocolVersion` and `capabilities` from the same result — so **no new protocol vocabulary is introduced** and no new request is sent.

## Dependencies & order

1. `ServerStatus.server_version` first — the capture site depends on the field existing.
2. Capture in `_send_initialize`, with its manager test. Independently verifiable before any notice logic changes.
3. `_installed_version` accessor, then migrate the three readers together — a partial migration leaves readers disagreeing about what `current` means, which is the failure mode of every prior round.
4. Delete `_repoint_cache_entry_to_effective_package`.
5. Tests, each proven RED.
6. CHANGELOG last.

No external dependencies; no migration (all state is in-memory and rebuilt on connect).

## Verification

```bash
cd <worktree>

uv run pytest tests/ -q -k 'server_version or notice or stale or catalog_search or update_server'

# RED proof — required. Revert ONLY the src changes via a targeted
# `git diff > patch` + `git apply -R`. NEVER `git checkout HEAD -- .`
# (it has destroyed uncommitted work in this repo).

uv run pytest -q                     # baseline 2536 + new tests, 3 skipped, 0 failed
uv run ruff check .
uv run ruff format --check src/ tests/
uv run mypy src
```

Then reproduce the round-3 defect by hand and confirm it is gone: drive `_get_update_warning` three consecutive times across an A→B config switch with the server reporting B's version, and confirm a correct notice appears (the failing transcript printed `None` three times).

Edge cases: server online vs offline; `serverInfo` absent; version unparseable by `is_version_newer`; remote server (does it report `serverInfo`? — confirm, and if so this works identically); server never connected (lazy); policy-denied; package undetectable.

## Acceptance criteria

- [ ] `ServerStatus.server_version` is populated from the `initialize` handshake and left `None` when the server omits or malforms it — proven by `test_initialize_captures_server_version`.
- [ ] Update notices compare the **running server's reported version** against upstream latest — proven by `test_notice_uses_installed_version_from_server_info` and `test_no_notice_when_installed_equals_latest`.
- [ ] A `.mcp.json` override naming a different package than the manifest never produces a cross-package comparison — proven by `test_package_change_does_not_fabricate_a_notice`.
- [ ] Notices **resume** after a package change rather than stopping permanently — proven by `test_notice_resumes_after_package_change` asserting the emitted notice string.
- [ ] An unusable or absent reported version yields silence, never a fabricated or crashing comparison — proven by `test_unparseable_server_version_stays_silent` and `test_missing_server_info_stays_silent`.
- [ ] Full suite ≥2536 + new tests passed / 3 skipped / 0 failed; `ruff check`, `ruff format --check src/ tests/`, `mypy src` clean; `## [Unreleased]` CHANGELOG entry present.

## Open question for the board

The offline fallback keeps the old described-version comparison when a server is not running. That is strictly weaker evidence than an installed version and is the residue of the original bug class. Is retaining it worth it for offline coverage, or should an offline server simply produce no update notice at all — trading coverage for the guarantee that **every notice pmcp emits is backed by an observed installed version**? The simpler contract may be the more honest one.

## Execution Policy

- execute: effort=high, reason=four prior attempts rejected in this subsystem; touches the connect handshake plus three notice readers, and the failure mode is a silently wrong user-facing claim.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```

---

## Board review — REJECTED (3/3 DISAGREE). Do not implement as written.

Boarded before implementation. All three legs blocked. Verified against source and reproduced; recorded so the next attempt starts from facts rather than from my framing.

### B1 (fatal) — `serverInfo.version` is NOT an installed-package version

The plan's premise. It is wrong, and **this repo already documented it** — `plans/phase-plan-v11-PG.md:44`, which I failed to find before writing the plan:

> "1.x FastMCP defaulted it to the *mcp library* version (a probe against the current build reports `{"name": "pangram", "version": "1.29.0"}` — plainly wrong, it is not this package's version). `MCPServer.__init__` defaults `version: str = ""`, so a literal port reports an empty string."

MCP defines `serverInfo.version` as an *implementation* version, unconstrained and self-declared. FastMCP 1.x reports the SDK version; mcp 2.x defaults it to `""`. Comparing that against an npm/PyPI registry version can fabricate or hide updates just as readily as the bug being fixed.

My empirical check was not wrong, but it was not sufficient: context7 reports `4.0.2` and `npm view @upstash/context7-mcp version` is also `4.0.2`. One well-behaved server proves the value *can* match, not that it *must*. A single confirming example is not an identity guarantee, and I over-read it.

### B2 — package identity is still unbound

`_effective_notice_target` reloads current *disk* config, while `server_version` describes the *connected process*, whose connect-time config is separately available (`manager.py:2732`). After an unrefreshed edit, an online A process can still be compared against B's latest. Conversely, after reconnecting as B, the server-scoped stale cache can retain A's tuple — and the plan's `"installed" | "described"` marker records neither package nor connection identity, so it does not close this.

### B3 — "unparseable version means silence" is false

`is_version_newer` (`version_checker.py:436`) extracts digits and never reports a parse failure. Reproduced:

```
is_version_newer('nightly',           '2.0.0') -> True
is_version_newer('release-channel-a', '2.0.0') -> True
is_version_newer('build-1',           '2.0.0') -> True
is_version_newer('',                  '2.0.0') -> True   # the mcp 2.x default!
is_version_newer('1.29.0',            '2.0.0') -> True   # the FastMCP SDK-version case
```

Each emits a notice. And my proposed test used `'nightly-2026-08-17'`, which returns `False` — it would have **passed by accident** while the criterion it claimed to prove was false. That is the fourth time a test of mine would have passed while its bug survived; the pattern is mine, not the board's.

Note the two worst cases are exactly B1's: an empty version (mcp 2.x default) and an SDK version (FastMCP 1.x) both compare as "older" and would produce a fabricated notice for every such server.

### What this means for #150

Direct probing is still the right instinct, but `serverInfo.version` is the wrong probe. Any fix needs a version whose **identity is bound to the package being compared**. Candidates, none yet assessed:

- Query the package manager for the actually-installed version (`npm ls`, `uv pip show`, `cargo`, `docker image inspect`) — real identity, but a new subprocess per server and per-ecosystem handling.
- Record the version resolved at install/update time as provenance, and compare only that.
- Narrow the feature: only emit notices for servers pmcp installed itself, where provenance is known.

Any of these needs `is_version_newer` to gain a fail-closed parse mode first (B3), or it will fabricate notices regardless of the source.

### Status

Plan rejected, not implemented. #151's fix remains independent and accepted.
