# PMCP — Phase Plan v9

> How to use this document: save to `specs/phase-plans-v9.md`, then run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-v9-<alias>.md`), then `/claude-execute-phase <alias>` to build it.

---

## Context

With v8/1.14.2 the codebase is clean (BAML fully removed, 1925 tests green, on
`main @ 3c19f3e`). v9 clears the remaining deferred items and adds a standing
discipline: keeping PMCP current with the MCP protocol.

Three themes:

**MCP spec currency (SPECCURRENCY).** PMCP targets MCP revision **2025-11-25**,
which is still the *current stable* revision (verified 2026-06-25). PMCP is
broadly compliant, but a handful of cheap MUST/SHOULD items from 2025-11-25 are
worth verifying and folding in: HTTP 403 on invalid `Origin` (PR #1439),
input-validation errors returned as tool-execution errors not protocol errors
(SEP-1303), `insufficient_scope` 403 step-up (SEP-835), JSON Schema 2020-12 as
the default schema dialect (SEP-1613), tool/resource/prompt icon passthrough
(SEP-973), and the `Implementation.description` field. More importantly, the
**draft (next) revision is a breaking overhaul** that lands squarely on PMCP's
core surfaces: a stateless protocol (removes the `initialize` handshake and the
`Mcp-Session-Id` Streamable-HTTP session), **tasks moved out of core into an
`io.modelcontextprotocol/tasks` extension** (removes `tasks/list`, replaces the
blocking `tasks/result` with `tasks/get` polling, adds `tasks/update`, allows
unsolicited task handles), Multi Round-Trip Requests replacing server-initiated
requests, a required `resultType` field, a `server/discover` RPC, `CacheableResult`
(`ttlMs`/`cacheScope`), DCR deprecated in favor of Client ID Metadata Documents,
and SSE resumability removed. Because PMCP brokers tasks (`gateway.tasks_*`) and
runs Streamable HTTP, the draft will require real migration work later. v9 does
**not build to the draft** (it is not GA); it establishes a tracked
`SPEC_COMPLIANCE.md` + a written draft-impact/migration assessment so the team is
not surprised when the draft ships.

**Registry incremental sync (REGSYNC).** v8 added a full-fetch registry client.
The MCP Registry API supports `?updated_since=<RFC3339>` delta sync. v9 adds it:
persist a last-sync timestamp, fetch only changed servers, merge into the cache.
This pays off once a scheduled/background refresh exists (re-pulling the full
~9,650-server list hourly is wasteful).

**Private registry + draft-schema support (PRIVREG).** Add an opt-in flag,
**default OFF**, that enables (a) private/custom registry endpoints and (b)
tolerance for draft/non-GA registry `server.json` schema fields. Aimed at
developers debugging their own new private MCP servers against PMCP. Flag OFF =
today's behavior (public registry only, GA schema fields only).

Raw material to reuse: `transport/http.py` (PRM/`WWW-Authenticate`/Origin
handling, the v8 resource-server auth), `tools/handlers.py` (the `gateway.*`
meta-tools incl. `tasks_*` and result envelopes), `manifest/registry.py` +
`manifest/sync.py` (the v8 registry client to extend), and `config/loader.py`
(the existing `${ENV_VAR}` + flag config surface).

## Architecture North Star

```
  transport/http.py    SPECCURRENCY: 403 invalid Origin, insufficient_scope 403
       │               step-up; (draft watch: stateless / no Mcp-Session-Id)
       ▼
  tools/handlers.py    SPECCURRENCY: input-validation→tool-error, JSON Schema
       │               2020-12 dialect, icon passthrough; (draft watch: tasks
       │               extension — tasks/list removed, tasks/result→get polling)
       ▼
  SPEC_COMPLIANCE.md   SPECCURRENCY: per-requirement compliance vs 2025-11-25 +
       │               draft impact/migration assessment + tracking checklist
       ▼
  manifest/registry.py REGSYNC: updated_since delta fetch + last-sync timestamp
  manifest/sync.py     PRIVREG: private endpoints + draft-schema tolerance (flag,
       │               default OFF)
       ▼
  v1.15.0 release gate
```

## Assumptions (fail-loud if wrong)

1. MCP **2025-11-25** is still the current stable revision at execution time; the
   draft is not yet GA. If a new stable revision has shipped, re-scope SPECCURRENCY
   against it before planning.
2. PMCP already implements PRM (RFC 9728), `WWW-Authenticate` challenge, and v8
   OAuth 2.1 resource-server auth; SPECCURRENCY fills gaps, not greenfield auth.
3. The registry API still exposes `?updated_since=<RFC3339>` with the same
   `metadata.nextCursor` pagination and `server.json` envelope verified in v8.
4. Private-registry support is **opt-in, default OFF**; with the flag off PMCP's
   observable behavior is unchanged (public registry, GA schema fields only).
5. Tasks remain *experimental* in 2025-11-25; the draft task extension is a
   forward concern documented, not implemented, in v9.
6. Baseline is green: `main @ 3c19f3e`, v1.14.2, 1925 tests, `TMPDIR` outside `/tmp`.

## Non-Goals

- **No building to the draft revision.** Stateless protocol, session removal,
  the tasks extension, MRTR, `server/discover`, `CacheableResult` — assessed and
  tracked, not implemented, until the draft is GA.
- No new gateway meta-tool; no local script execution; PMCP still only brokers.
- No Authorization Server / DCR / SSO / RBAC / billing.
- PRIVREG does not auto-install private servers or weaken auth; it only widens
  discovery metadata when explicitly enabled.
- No removal or renaming of existing `gateway.*` tools or response fields.

## Cross-Cutting Principles

1. **Adversarial, real-fixture tests.** Every change ships a regression test that
   FAILS on the current HEAD and passes after. Registry tests use recorded
   fixtures; spec tests assert the exact MUST behavior (e.g. a bad `Origin`
   returns 403).
2. **Cite the source.** Each spec-compliance change names its SEP/PR in code
   comment + test + `SPEC_COMPLIANCE.md`, so currency is auditable.
3. **Default-safe opt-in.** PRIVREG's flag defaults OFF; flag-off behavior is
   byte-for-byte the current behavior, asserted by test.
4. **Additive compatibility.** No existing response field changes shape; new
   fields (icons, `resultType` readiness, cache hints) are additive.
5. **`manifest/registry.py` and `tools/handlers.py` are single-writer files.**
   Phases touching them are sequenced; the owning phase is named in Scope notes.
6. **Run the suite with `TMPDIR` outside `/tmp`.**

## Phase Dependency DAG

```
  SPECCURRENCY ──┐
                 ├──────────────► RELEASE
  REGSYNC ──┬────┘
            └──► PRIVREG ────────► RELEASE
```

SPECCURRENCY and REGSYNC are independent parallel roots (disjoint files:
transport/handlers/docs vs manifest/registry). PRIVREG runs after REGSYNC (both
edit `manifest/registry.py`). RELEASE gates all three → v1.15.0.

## Top Interface-Freeze Gates

1. **IF-0-SPECCURRENCY-1** — PMCP satisfies the folded-in 2025-11-25 MUST/SHOULD
   items (403 on invalid `Origin`; input-validation errors as tool-execution
   errors; `insufficient_scope` 403 step-up with `scope`; JSON Schema 2020-12
   advertised as the default dialect; tool/resource/prompt icons passed through
   discovery), and `SPEC_COMPLIANCE.md` records per-requirement status plus a
   draft-revision impact/migration assessment and a tracking checklist.
2. **IF-0-REGSYNC-1** — The registry client supports incremental sync: a
   persisted last-sync timestamp drives a `?updated_since=<RFC3339>` fetch whose
   deltas merge into the cache, falling back to a full fetch when no timestamp or
   on server error; the cache records `last_synced_at`.
3. **IF-0-PRIVREG-1** — A single opt-in config flag (default OFF) gates both
   private/custom registry endpoint configuration and draft/non-GA `server.json`
   schema-field tolerance; with the flag off, registry behavior is identical to
   pre-v9 (public registry, GA fields only).
4. **IF-0-RELEASE-1** — `__version__ == "1.15.0"`, CHANGELOG documents the spec
   fold-ins / registry sync / private-registry flag, `SPEC_COMPLIANCE.md` is
   linked from README, and the full release gate passes.

## Phases

### Phase 1 — MCP Spec Currency (SPECCURRENCY)

**Objective**
Fold in the cheap unmet 2025-11-25 MUST/SHOULD items and stand up a tracked
`SPEC_COMPLIANCE.md` that also assesses the breaking draft revision's impact on
PMCP's transport and task-brokering surfaces.

**Exit criteria**
- [ ] Streamable HTTP returns **HTTP 403** for an invalid/disallowed `Origin`
  header; a request with a bad `Origin` is rejected (test fails on HEAD). [PR #1439]
- [ ] Tool input-validation failures are returned as **tool-execution errors**
  (result with `isError`/error envelope) rather than JSON-RPC protocol errors, so
  the model can self-correct. [SEP-1303]
- [ ] Insufficient scope at runtime returns **403** with
  `WWW-Authenticate: error="insufficient_scope", scope="…"`. [SEP-835]
- [ ] PMCP advertises **JSON Schema 2020-12** as the default schema dialect where
  it surfaces schema dialect metadata. [SEP-1613]
- [ ] Tool/resource/prompt **icons** (when provided by a downstream server) are
  passed through `gateway.catalog_search`/`describe` discovery metadata. [SEP-973]
- [ ] `SPEC_COMPLIANCE.md` exists with: target revision (2025-11-25), a
  per-requirement compliance table, a **draft-revision impact/migration
  assessment** (stateless/no-session transport, tasks→extension with `tasks/list`
  removed and `tasks/result`→`tasks/get` polling + `tasks/update`, MRTR,
  `resultType`, `server/discover`, `CacheableResult`, DCR→CIMD), and a tracking
  checklist for adopting the next stable revision.
- [ ] `ruff`, `mypy`, full `pytest` (TMPDIR outside `/tmp`) green.

**Scope notes**
- Lanes: (a) `transport/http.py` — Origin 403 + `insufficient_scope` 403 step-up
  (owns transport); (b) `tools/handlers.py` — input-validation→tool-error, JSON
  Schema 2020-12 dialect advertisement, icon passthrough (owns handlers); (c)
  `SPEC_COMPLIANCE.md` doc + draft impact assessment + tracking checklist; (d)
  tests in `tests/test_http_transport.py` / `tests/test_tools.py`.
- `tools/handlers.py` is single-writer; this phase owns it (no other v9 phase
  edits it).
- Verify each item against current PMCP behavior first — some may already be
  satisfied (e.g. v8 may already 403 on bad Origin); record "already compliant"
  in the doc rather than adding redundant code.

**Non-goals**
- No draft-revision implementation (stateless transport, task extension, MRTR).
- No change to the v8 OAuth 2.1 resource-server validation beyond the scope 403.

**Key files**
- `src/pmcp/transport/http.py`
- `src/pmcp/tools/handlers.py`
- `SPEC_COMPLIANCE.md`
- `README.md`
- `tests/test_http_transport.py`
- `tests/test_tools.py`

**Depends on**
- (none)

**Produces**
- IF-0-SPECCURRENCY-1

### Phase 2 — Registry Incremental Sync (REGSYNC)

**Objective**
Add `updated_since` delta sync to the registry client so a refresh fetches only
servers changed since the last sync and merges them into the cache.

**Exit criteria**
- [ ] The registry cache persists a `last_synced_at` RFC3339 timestamp.
- [ ] When a timestamp exists, the client issues `GET /v0/servers?updated_since=…`
  and **merges** returned entries (add/update; dedup to latest) into the cached
  set rather than replacing it (test with a recorded delta fixture, fails on HEAD).
- [ ] No timestamp (cold cache) or a server/HTTP error falls back to the existing
  full paginated fetch; failure degrades to the current cache, never crashes.
- [ ] Pagination (`metadata.nextCursor`) and the size bound from v8 still apply to
  the delta fetch.
- [ ] `ruff`, `mypy`, full `pytest` green; tests use recorded fixtures (no live
  network).

**Scope notes**
- Lanes: (a) `manifest/registry.py` — `updated_since` request + `last_synced_at`
  read/write in the cache structure (owns registry.py for this phase); (b)
  `manifest/sync.py` — delta merge/reconcile into the cached manifest; (c) tests
  in `tests/test_registry.py` / `tests/test_manifest.py` with a recorded
  full+delta fixture pair.
- `manifest/registry.py` is single-writer; **REGSYNC owns it before PRIVREG.**

**Non-goals**
- No scheduler/cron for background refresh (that is the trigger this enables, not
  part of this phase); no change to when refresh is invoked.
- No private-registry endpoints (that is PRIVREG).

**Key files**
- `src/pmcp/manifest/registry.py`
- `src/pmcp/manifest/sync.py`
- `tests/test_registry.py`
- `tests/test_manifest.py`

**Depends on**
- (none)

**Produces**
- IF-0-REGSYNC-1

### Phase 3 — Private Registry & Draft-Schema Flag (PRIVREG)

**Objective**
Add an opt-in, default-OFF flag enabling private/custom registry endpoints and
draft/non-GA `server.json` schema-field tolerance, for developers debugging their
own private MCP servers — without changing default behavior.

**Exit criteria**
- [ ] A single config flag (env var + config field), default **OFF**, gates the
  feature; with it off, registry behavior is byte-identical to pre-v9 (public
  registry only, GA schema fields only) — asserted by a flag-off regression test.
- [ ] With the flag ON, PMCP can be pointed at a configured private/custom
  registry endpoint (in addition to or instead of the public one) and parses its
  `/v0/servers` response.
- [ ] With the flag ON, the parser tolerates draft/non-GA `server.json` schema
  fields (preserves unknown fields in `raw`, surfaces known draft fields) instead
  of dropping or erroring on them; with the flag OFF, unknown/draft fields are
  ignored as today.
- [ ] Private endpoints and draft tolerance are documented as a debugging feature
  with an explicit "not for production discovery" caveat.
- [ ] `ruff`, `mypy`, full `pytest` green; tests cover both flag states.

**Scope notes**
- Lanes: (a) `config/loader.py` — the opt-in flag + private endpoint config
  (owns config); (b) `manifest/registry.py` — flag-gated private endpoint fetch +
  draft-schema-tolerant parsing (owns registry.py; **sequences after REGSYNC**);
  (c) docs + tests in `tests/test_registry.py` / `tests/test_config_loader.py`
  covering flag ON and OFF.
- `manifest/registry.py` single-writer: PRIVREG depends on REGSYNC so the two
  registry-client changes serialize.

**Non-goals**
- No auth changes; private registries reuse existing remote-header/env handling.
- No auto-install or auto-connect of private servers; discovery metadata only.
- No promotion of draft schema fields to first-class typed fields (tolerate, do
  not standardize on them).

**Key files**
- `src/pmcp/config/loader.py`
- `src/pmcp/manifest/registry.py`
- `tests/test_registry.py`
- `tests/test_config_loader.py`
- `README.md`

**Depends on**
- REGSYNC

**Produces**
- IF-0-PRIVREG-1

### Phase 4 — Release Gate v1.15.0 (RELEASE)

**Objective**
Cut v1.15.0: version bump, CHANGELOG, README/`SPEC_COMPLIANCE.md` wiring, and the
full release gate.

**Exit criteria**
- [ ] `__version__` and `pyproject.toml` bumped to `1.15.0`; `uv.lock` synced.
- [ ] CHANGELOG `[1.15.0]` entry covers: MCP 2025-11-25 spec fold-ins (with SEP/PR
  references), registry `updated_since` incremental sync, and the opt-in
  private-registry/draft-schema flag (default off).
- [ ] README links `SPEC_COMPLIANCE.md` and documents the private-registry flag.
- [ ] Full release gate passes: `ruff check`, `ruff format --check`, `mypy
  src/pmcp`, `pytest -q` (TMPDIR outside `/tmp`), `uv build`, `git diff --check`.

**Scope notes**
- Decompose into 3 lanes: (a) version bump + CHANGELOG; (b) README/docs wiring
  (SPEC_COMPLIANCE link + private-registry flag docs); (c) final release-gate
  verification run.
- Introduces no new behavior; it is the cut + closeout gate. The human reviews
  the diff and pushes/tags (CI publishes on tag) — no `git push`/publish inside
  the phase.

**Non-goals**
- No `git push` / PyPI publish inside the phase.
- No new features.

**Key files**
- `src/pmcp/__init__.py`
- `pyproject.toml`
- `CHANGELOG.md`
- `README.md`
- `uv.lock`

**Depends on**
- SPECCURRENCY
- REGSYNC
- PRIVREG

**Produces**
- IF-0-RELEASE-1

## Execution Notes

- **Planning**: `/claude-plan-phase <ALIAS>` per phase. `SPECCURRENCY` and
  `REGSYNC` share no DAG ancestor (disjoint files) → plan and execute them
  concurrently. `PRIVREG` runs after `REGSYNC` merges (shared
  `manifest/registry.py`). `RELEASE` runs last.
- **Run it**: `phase-loop run --roadmap specs/phase-plans-v9.md --max-phases 10
  --full-phase --closeout-mode commit --observe` (commit mode so phases
  auto-commit and the loop advances; do NOT use default manual mode, which parks
  at `awaiting_phase_closeout`). Pass `--roadmap` explicitly — multiple
  `specs/phase-plans-v*.md` exist, so the bare runner would report
  `ambiguous_roadmap_selection`.
- **Critical path**: `REGSYNC → PRIVREG → RELEASE` (the longest chain).
  SPECCURRENCY runs parallel to REGSYNC and re-converges at RELEASE.
- **Single-writer files across phases**: `manifest/registry.py` (REGSYNC then
  PRIVREG — REGSYNC owns it first); `tools/handlers.py` (SPECCURRENCY only).
- **Verification reminder**: per-phase checks use `ruff check`; the RELEASE gate
  adds `ruff format --check` — keep formatting clean as you go.

## Acceptance Criteria

- [ ] PMCP satisfies the folded-in 2025-11-25 MUST/SHOULD items, and
  `SPEC_COMPLIANCE.md` tracks per-requirement status plus a draft-revision
  migration assessment — each with adversarial tests / cited SEPs.
- [ ] Registry refresh can sync incrementally via `updated_since`, merging deltas
  into the cache and degrading to a full fetch on cold cache or error.
- [ ] An opt-in default-OFF flag enables private/custom registries and
  draft-schema tolerance for debugging, with flag-off behavior provably unchanged.
- [ ] Full release gate passes and `__version__ == 1.15.0`.

## Verification

Run after the relevant phases merge (TMPDIR outside `/tmp`):

```bash
# SPECCURRENCY
uv run pytest tests/test_http_transport.py tests/test_tools.py -k "origin or scope or insufficient or input_validation or schema_dialect or icon"

# REGSYNC
uv run pytest tests/test_registry.py tests/test_manifest.py -k "updated_since or incremental or delta or last_synced or merge"

# PRIVREG
uv run pytest tests/test_registry.py tests/test_config_loader.py -k "private or draft_schema or flag_off or flag_on"

# Whole-roadmap release gate (RELEASE)
TMPDIR=/var/tmp uv run ruff check src/ tests/
TMPDIR=/var/tmp uv run ruff format --check src/ tests/
uv run mypy src/pmcp
TMPDIR=/var/tmp uv run pytest -q
uv build
git diff --check
```
</content>
