# PMCP — Phase Plan v8

> How to use this document: save to `specs/phase-plans-v8.md`, then run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-v8-<alias>.md`), then `/claude-execute-phase <alias>` to build it.

---

## Context

v7 (REDACT, CONCURR, MANIFEST, ENVFIX, AUTHRS, REGISTRY) shipped autonomously via
the phase-loop and passed its gate (1892 tests, ruff/mypy clean), but a high-effort
post-review of `git diff a276e9a..HEAD` plus authoritative external research
(`plans/v7-code-review-findings.md`) found **11 real defects** the green suite could
not catch — because the same agent wrote both the code and its tests, using synthetic
fixtures and happy paths.

Two defects are phases that only half-fixed their own target:
- **REDACT** routed `invoke`/`tasks_result` through the redactor but left `tasks_list`
  and `tasks_get` emitting `status_message`/`raw` verbatim (the C2 leak it existed to
  close).
- **CONCURR** put `_lifecycle_lock` on every connect path *except* `_reconnect_loop`
  (the C3 race it existed to fix), and made the lock too coarse (held across retry
  backoff).

The new auth surface (AUTHRS) has confirmed flaws, now backed by research against the
MCP 2025-11-25 spec and PyJWT 2.10.x:
- Audience is derived from the client-controlled `Host` header when no audience is
  configured — an RFC 8707 audience-confusion bypass. The spec requires validating
  `aud` against a **configured canonical resource URI**, never a request-derived value.
- JWKS is fetched with a **blocking** `PyJWKClient` constructed per request on the
  asyncio loop (gateway-wide DoS + no caching). Correct pattern: async aiohttp fetch
  with a TTL cache + `asyncio.Lock`, then `jwt.decode` with an operator alg allowlist.
- JWKS/alg errors return 500 (not 401), and `jwks_url` has no https/SSRF guard.

The REGISTRY parser is materially broken (research-confirmed against the live API):
it **drops `remotes[]` entirely** (so the remote vendor-official servers — the whole
point — come back empty), returns **all versions** (duplicate entries) instead of
`isLatest`, has **no pagination** (sees 30 of ~9,650 servers), blocks the event loop on
fetch, and uses a cwd-relative cache path. The matcher rescoring overcorrected and now
drops real queries (`database sql`, `headless browser`) below threshold against the
shipped manifest. A secret write/read project-root asymmetry silently breaks remote
auth under `--project`.

This roadmap remediates all of it toward a clean **v1.14.0** cut, and — critically —
closes the testing gap that let these slip: every fix ships an **adversarial test that
fails on the current HEAD**, matcher tests run against the **real manifest**, and
network/error paths are exercised with mocked failures.

Raw material to reuse: `_sanitize_task_for_output` (handlers.py — the existing redactor
to extend to two more endpoints), `sanitize_public_auth_url` (auth.py — the SSRF guard
to reuse for `jwks_url`), `PyJWKSet.from_dict` + the existing `jwks=` path (auth.py —
the pre-fetched-keys path to unify on), and aiohttp 3.13.2 (already a dependency).

## Architecture North Star

```
  transport/http.py   AUTHFIX: aud == configured canonical_resource_uri (RFC 8707),
       │              fail-closed; jwks_url https+public; 401/503 status mapping
       ▼
  auth.py             AUTHFIX: AsyncJWKS (aiohttp + TTL cache + Lock); jwt.decode with
       │              operator alg allowlist (never token alg); PyJWKClientError→401/503
       ▼
  policy + handlers   COMPLETE: every task-emitting endpoint (invoke, tasks_result,
       │              tasks_list, tasks_get) routes through the one redactor
       ▼
  client/manager.py   COMPLETE: _lifecycle_lock covers reconnect too; released across
       │              backoff. REGFIX: per-project secret resolution (R8)
       ▼
  manifest/registry   REGFIX: async+cached+bounded fetch, remotes[] modeled, dedup to
  manifest/matcher    isLatest, pagination. MATCHFIX: scoring real queries clear
```

## Assumptions (fail-loud if wrong)

1. The findings in `plans/v7-code-review-findings.md` and the three research reports in
   this conversation are accurate at the cited `file:line` / spec section.
2. Baseline is green with `TMPDIR` outside `/tmp` (the `/tmp/.git` artifact); v8 work is
   measured against HEAD = `c810536` (post-v7, post-format-fix).
3. PyJWT 2.10.1 (`pyjwt[crypto]>=2.10.0`) and aiohttp 3.13.2 are installed; `PyJWKClient`
   is blocking with no async variant; `PyJWKSet.from_dict` is available.
4. The MCP Registry read API (`GET https://registry.modelcontextprotocol.io/v0/servers`)
   is public, cursor-paginated (`metadata.nextCursor`, `limit≤100`), supports
   `?version=latest`, and carries `remotes[]` + outer `_meta.…official.isLatest`.
5. Resource-server auth stays **opt-in**; default deployments keep shared-secret/none.
6. Backward compatibility holds: no existing `gateway.*` response field changes shape.

## Non-Goals

- No new gateway meta-tool; no local script execution.
- No Authorization Server / DCR / SSO / RBAC / billing.
- No auto-install of registry servers (discovery metadata only).
- Not wiring resource-server mode on by default; it remains config-gated and fail-closed.
- No new public gateway contract beyond the additive registry `remotes` data.

## Cross-Cutting Principles

1. **Adversarial, real-fixture tests.** Every fix ships a regression test that FAILS on
   the current HEAD and passes after. Matcher tests use the **real** `manifest.yaml`;
   network/error paths use mocked failures; completeness is asserted structurally
   (e.g. "every task-emitting endpoint redacts", "every connect path locks").
2. **Fail closed on auth config.** Resource-server mode without a configured canonical
   resource URI is a startup error, never a silent Host-derived fallback.
3. **No blocking I/O on the event loop.** JWKS and registry fetches are async + cached.
4. **`client/manager.py` and `tools/handlers.py` are single-writer files.** Phases that
   touch them are sequenced; the owning phase is named in Scope notes.
5. **Additive compatibility.** Existing response fields keep their shape; new behavior is
   opt-in or defaulted-safe.
6. **Run the suite with `TMPDIR` outside `/tmp`.**

## Phase Dependency DAG

```
  COMPLETE ──┐
  AUTHFIX ───┤
  MATCHFIX ──┤
             ├──► REGFIX ──► RELEASE
  COMPLETE ──┘ (REGFIX after COMPLETE: shared handlers.py)
                                  ▲
  AUTHFIX, MATCHFIX ──────────────┘ (RELEASE gates all four fix phases)
```

COMPLETE, AUTHFIX, MATCHFIX are three independent parallel roots (disjoint files).
REGFIX waits on COMPLETE (both edit `handlers.py`). RELEASE gates everything → v1.14.0.

## Top Interface-Freeze Gates

1. **IF-0-COMPLETE-1** — Every task-emitting handler (`invoke`, `tasks_result`,
   `tasks_list`, `tasks_get`) routes its returned task through `_sanitize_task_for_output`;
   `_lifecycle_lock` covers the reconnect connect path and is NOT held across retry
   backoff sleeps. Frozen as: the redactor call-site set + the lock-scope contract.
2. **IF-0-AUTHFIX-1** — `validate_resource_server_token` validates `aud` against an
   explicitly-configured `canonical_resource_uri` (reject otherwise; no Host-derived
   fallback), verifies the signature via an async TTL-cached JWKS, restricts algorithms
   to an operator allowlist, and maps errors to 401 (bad token) / 503 (JWKS unreachable);
   `jwks_url` must be https + public-host. Resource-server mode fails closed without the
   canonical URI.
3. **IF-0-MATCHFIX-1** — `_keyword_match_score` / threshold so that representative
   queries (`database sql`, `headless browser`, `chrome automation`, `postgres database`)
   resolve to the correct server against the real `manifest.yaml`.
4. **IF-0-REGFIX-1** — `RegistryServerEntry` carries `remotes[]` and `_meta` (status /
   isLatest); `fetch_registry_servers` is async, size-bounded, paginated
   (`metadata.nextCursor`), dedup-to-latest, with a stable (non-cwd) cache path; secret
   write and read resolve the same project root.
5. **IF-0-RELEASE-1** — `__version__ == "1.14.0"`, CHANGELOG covers the v7+v8 host /
   auth / registry work, and the full release gate (ruff check + format, mypy, pytest,
   build, diff --check) passes.

## Phases

### Phase 1 — Complete the Half-Done Sweeps (COMPLETE)

**Objective**
Finish REDACT and CONCURR: route the two missed task-emitting endpoints through the
redactor, and put `_lifecycle_lock` on the reconnect path without over-serializing.

**Exit criteria**
- [ ] `gateway.tasks_list` and `gateway.tasks_get` route returned tasks through
  `_sanitize_task_for_output`; a secret in `status_message`/`raw` is redacted (test fails
  on HEAD). [R1]
- [ ] A structural test asserts EVERY task-emitting handler redacts (so a future endpoint
  can't silently skip it). [R1, completeness]
- [ ] `_reconnect_loop`'s `_connect_singleflight` call acquires `_lifecycle_lock`; a test
  gathering a reconnect against `refresh(force)` shows no orphaned subprocess / zombie
  `_clients` entry (fails on HEAD). [R2]
- [ ] `_lifecycle_lock` is not held across `RETRY_DELAYS` sleeps; one unreachable server
  does not block other servers' lazy-start/disconnect/refresh for the backoff window
  (test with a slow mock transport). [R7]
- [ ] `ruff`, `mypy`, full `pytest` (TMPDIR outside `/tmp`) green.

**Scope notes**
- Lanes: (a) `handlers.py` redaction of `tasks_list`/`tasks_get` + completeness test
  (owns the handlers task-emit region); (b) `manager.py` reconnect-lock + lock-scope
  (owns `manager.py`); (c) regression tests in `test_tools.py` / `test_client_manager.py`.
- `handlers.py` and `manager.py` are single-writer; this phase OWNS both first so REGFIX
  (which also edits `handlers.py`) can sequence after it.

**Non-goals**
- No redactor pattern changes (that was REDACT); only call-site coverage.
- No reconnect/backoff policy redesign beyond lock scope.

**Key files**
- `src/pmcp/tools/handlers.py`
- `src/pmcp/client/manager.py`
- `tests/test_tools.py`
- `tests/test_client_manager.py`

**Depends on**
- (none)

**Produces**
- IF-0-COMPLETE-1

### Phase 2 — Auth Hardening (AUTHFIX)

**Objective**
Make the OAuth 2.1 Resource Server correct: configured-canonical-URI audience binding,
async cached JWKS, an operator algorithm allowlist, correct error status, and a
guarded `jwks_url`.

**Exit criteria**
- [ ] `aud` is validated against an explicitly-configured `canonical_resource_uri`;
  resource-server mode without it is a startup error; the `request.url_for(...)`
  Host-derived fallback is removed. A token minted for another resource + spoofed `Host`
  is rejected (test fails on HEAD). [R3]
- [ ] JWKS is fetched via aiohttp with a TTL cache + `asyncio.Lock` (anti-stampede),
  reused across requests; no blocking `urlopen` on the event loop; cold-cache fetch does
  not stall concurrent requests (test with a mock slow JWKS). [R4]
- [ ] `jwt.decode` uses an operator-configured algorithm allowlist (default e.g.
  `["RS256","ES256"]`), never the token's `alg` header. [R9]
- [ ] `InvalidTokenError`→401, JWKS-unreachable (`PyJWKClientConnectionError`/aiohttp
  errors)→503, unknown-kid-after-refresh→401; no path returns 500 or leaks `jwks_url`
  (tests for each). [R9]
- [ ] `resource_server_jwks_url` must be https and a public host (reuse
  `sanitize_public_auth_url`); a private/link-local/http URL is rejected at startup. [R11]
- [ ] No token passthrough regression; `ruff`, `mypy`, full `pytest` green.

**Scope notes**
- Lanes: (a) `auth.py` — `AsyncJWKS` helper (aiohttp + TTL + Lock), unify on
  `PyJWKSet.from_dict`, alg allowlist, exception→status mapping, hoist the per-call
  diagnostic regex to module level; (b) `transport/http.py` — required
  `canonical_resource_uri` config + fail-closed, audience validation, `jwks_url`
  https/public guard, 401/503 wiring; (c) `remote_auth.py` touch-ups if needed;
  (d) tests `test_auth.py` / `test_http_transport.py` with signed fixtures + mocked
  failures. Disjoint from COMPLETE's files → parallel root.

**Non-goals**
- Not enabling resource-server mode by default in `server.py`.
- No OIDC discovery / DCR / introspection beyond JWT+JWKS validation.

**Key files**
- `src/pmcp/auth.py`
- `src/pmcp/transport/http.py`
- `src/pmcp/remote_auth.py`
- `tests/test_auth.py`
- `tests/test_http_transport.py`

**Depends on**
- (none)

**Produces**
- IF-0-AUTHFIX-1

### Phase 3 — Matcher Scoring Regression (MATCHFIX)

**Objective**
Re-tune the capability matcher so real queries match the right server against the
shipped manifest, with regression tests on the real manifest instead of the synthetic
fixture that hid the bug.

**Exit criteria**
- [ ] `_keyword_match_score` / threshold so `database sql`, `sql query`,
  `headless browser`, `chrome automation`, `browser scraping`, and `postgres database`
  each resolve to the correct server against the real `manifest.yaml` (tests fail on
  HEAD). [R6]
- [ ] The `*-remote` duplicate entries no longer halve a server's weight below threshold.
- [ ] A regression test loads the REAL `manifest.yaml` (not `create_test_manifest()`) and
  asserts a table of query→expected-server.
- [ ] `ruff`, `mypy`, full `pytest` green.

**Scope notes**
- Lanes: (a) `manifest/matcher.py` scoring/threshold fix (owns matcher.py);
  (b) real-manifest regression tests in `test_manifest.py`. Disjoint file → parallel root.
- Consider IDF weighting normalized so absolute matched-keyword strength clears the floor;
  validate against the real manifest, not synthetic weights = 1.0.

**Non-goals**
- No change to the manifest schema or server entries (that's REGFIX).
- No new ranking algorithm beyond fixing the regression.

**Key files**
- `src/pmcp/manifest/matcher.py`
- `tests/test_manifest.py`

**Depends on**
- (none)

**Produces**
- IF-0-MATCHFIX-1

### Phase 4 — Registry & Discovery Correctness (REGFIX)

**Objective**
Make the registry client actually consume the registry (remotes, latest-only,
paginated, async, bounded, stable cache) and fix the discovery-handler regressions
(secret project-root asymmetry, leading-word category skip, dead/duplicated parsing).

**Exit criteria**
- [ ] `RegistryServerEntry` carries `remotes[]` (streamable-http/sse URLs + headers) and
  surfaces outer `_meta` status/`isLatest`; a remote-only registry entry parses to a
  usable candidate (test fails on HEAD). [R5b]
- [ ] Listing dedups to latest (`?version=latest` or `isLatest` filter) — no duplicate
  server entries. [R5c]
- [ ] `fetch_registry_servers` is async (aiohttp), size-bounded, and paginates via
  `metadata.nextCursor` (or caps explicitly and logs the cap); failure degrades to the
  local manifest without crashing. [R5a, R5d]
- [ ] Registry fetch never runs on the event loop synchronously; the result is cached
  in-process; the cache file path is anchored to a stable base dir (not cwd-relative). [R5a]
- [ ] `gateway.auth_connect` secret writes and the manager's remote-header reads resolve
  the SAME project root; with `--project <path>≠cwd`, store-then-connect succeeds
  (test fails on HEAD). [R8]
- [ ] `_unknown_service` no longer treats a leading capitalized word as a service name;
  queries like "Search the web" still category-match the manifest. [R10]
- [ ] Dead `_legacy_query_mcp_registry` removed; `search_registry` consumes the typed
  `RegistryServerEntry` instead of re-parsing `.raw`; the registry cache-IO reuses a
  shared helper rather than duplicating `refresher.py`. [cleanup]
- [ ] `ruff`, `mypy`, full `pytest` green; new tests use a recorded registry fixture.

**Scope notes**
- Lanes: (a) `manifest/registry.py` + `manifest/sync.py` + `types.py` — async/cached/
  bounded/paginated fetch, `remotes[]`/`_meta` model, dedup, stable cache path (owns the
  registry modules); (b) `handlers.py` discovery — R8, R10, typed `search_registry`,
  dead-code removal (owns the handlers discovery region; sequences after COMPLETE which
  owns handlers); (c) `manager.py`/`remote_auth.py` read-side for R8 (sequences after
  COMPLETE which owns manager.py); (d) tests with a recorded `/v0/servers` fixture.
- `handlers.py` and `manager.py` are single-writer; **REGFIX must run after COMPLETE.**

**Non-goals**
- No auto-install or live-registry dependency in tests (recorded fixture only).
- No incremental `updated_since` sync engine yet (note as a future follow-up).

**Key files**
- `src/pmcp/manifest/registry.py`
- `src/pmcp/manifest/sync.py`
- `src/pmcp/types.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/client/manager.py`
- `tests/test_manifest.py`
- `tests/test_offline_discovery.py`
- `tests/test_tools.py`

**Depends on**
- COMPLETE

**Produces**
- IF-0-REGFIX-1

### Phase 5 — Release Gate v1.14.0 (RELEASE)

**Objective**
Cut v1.14.0: bump version, write the CHANGELOG for the v7 host/auth/registry work and
the v8 remediation, reconcile the issue trackers, and pass the full release gate.

**Exit criteria**
- [ ] `__version__` bumped to `1.14.0`; `uv.lock` synced.
- [ ] CHANGELOG `[1.14.0]` entry covers: tenant code-mode host integration (v7), OAuth 2.1
  resource-server auth with audience binding, MCP Registry consumption with remotes, and
  the redaction/concurrency/matcher fixes — with precise "brokers, does not execute"
  wording preserved.
- [ ] `plans/v7-issue-tracker.md` and `plans/v7-code-review-findings.md` reconciled
  (R1–R11 + cleanups marked resolved with their commits).
- [ ] Full release gate passes: `ruff check`, `ruff format --check`, `mypy src/pmcp
  --exclude baml_client`, `pytest -q` (TMPDIR outside `/tmp`), `uv build`,
  `git diff --check`.
- [ ] README operator docs mention the resource-server auth mode and the registry-backed
  discovery (opt-in, fail-closed).

**Scope notes**
- Lanes: (a) version bump + CHANGELOG + tracker reconciliation; (b) README/docs sweep;
  (c) final release-gate verification run. Docs and version files are largely disjoint.
- This phase introduces no new behavior — it is the cut + closeout gate.

**Non-goals**
- No `git push` / PyPI publish inside the phase — the human reviews the diff and pushes.
- No new features.

**Key files**
- `src/pmcp/__init__.py`
- `CHANGELOG.md`
- `README.md`
- `plans/v7-issue-tracker.md`
- `uv.lock`

**Depends on**
- COMPLETE
- AUTHFIX
- MATCHFIX
- REGFIX

**Produces**
- IF-0-RELEASE-1

## Execution Notes

- **Planning**: `/claude-plan-phase <ALIAS>` per phase. `COMPLETE`, `AUTHFIX`, and
  `MATCHFIX` share no DAG ancestor (disjoint files) → plan and execute them concurrently.
- **Execution**: `/claude-execute-phase <alias>` after each plan. `REGFIX` runs after
  `COMPLETE` merges (shared `handlers.py`/`manager.py`). `RELEASE` runs last.
- **Run it**: `phase-loop run --roadmap specs/phase-plans-v8.md --max-phases 10
  --full-phase --closeout-mode commit --observe` (commit mode so phases auto-commit and
  the loop advances — do NOT use the default manual mode, which parks at
  `awaiting_phase_closeout`).
- **Critical path**: `COMPLETE → REGFIX → RELEASE` (the longest chain). AUTHFIX and
  MATCHFIX finish in parallel and only re-converge at RELEASE.
- **Single-writer files across phases**: `tools/handlers.py` (COMPLETE then REGFIX) and
  `client/manager.py` (COMPLETE then REGFIX) — COMPLETE owns both first; REGFIX rebases.
- **Verification reminder**: the phase-loop's per-phase check uses `ruff check`; the
  RELEASE gate adds `ruff format --check` — keep formatting clean as you go.

## Acceptance Criteria

- [ ] All 11 review findings (R1–R11) + the registry remotes/dedup/pagination gaps + the
  four cleanups are fixed, each with an adversarial test that fails on pre-v8 HEAD.
- [ ] No secret can leave via any task-emitting endpoint; no connect path is lock-free;
  the resource server validates audience against a configured canonical URI and never
  blocks the event loop on JWKS.
- [ ] The registry client returns deduplicated, remote-aware, paginated candidates and
  degrades offline-safely; the matcher resolves representative queries against the real
  manifest.
- [ ] Full release gate passes and `__version__ == 1.14.0`.

## Verification

Run after the relevant phases merge (TMPDIR outside `/tmp`):

```bash
# COMPLETE
uv run pytest tests/test_tools.py tests/test_client_manager.py -k "tasks_list or tasks_get or redact or reconnect or refresh or lifecycle"

# AUTHFIX
uv run pytest tests/test_auth.py tests/test_http_transport.py -k "audience or canonical or jwks or alg or 401 or 503 or resource_server"

# MATCHFIX (against the real manifest)
uv run pytest tests/test_manifest.py -k "matcher or real_manifest or score or threshold"

# REGFIX
uv run pytest tests/test_manifest.py tests/test_offline_discovery.py tests/test_tools.py -k "registry or remotes or latest or paginat or project_root or unknown_service"

# Whole-roadmap release gate (RELEASE)
TMPDIR=/var/tmp uv run ruff check src/ tests/
TMPDIR=/var/tmp uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
TMPDIR=/var/tmp uv run pytest -q
uv build
git diff --check
```
</content>
