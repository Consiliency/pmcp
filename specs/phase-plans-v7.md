# PMCP — Phase Plan v7

> How to use this document: save to `specs/phase-plans-v7.md`, then run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-v7-<alias>.md`), then `/claude-execute-phase <alias>` to build it.

---

## Context

The 2026-06-15 critical codebase review (`plans/codebase-review-2026-06-15.md`)
found PMCP fundamentally sound — `ruff`/`mypy` clean, 1807 tests passing, no
command injection, timing-safe bearer compare, no token passthrough — but
surfaced two classes of work.

**Class 1 — confirmed bugs the green test suite cannot catch.** The secret-redaction
layer *looks* complete but leaks on the truncation/summary/task paths the fixtures
never exercise (a truncated result's `summary` is built from pre-redaction text; a
task's `status_message`/`raw` skip redaction entirely; `redact=True` silently caps
output to 400 chars; bare `sk-…`/`ghp_…` tokens aren't matched). Separately, the
shared-gateway concurrency model has an incomplete lock boundary: the connect/lazy-start
paths run entirely outside `_lifecycle_lock`, so a second client's connect can race
another client's `refresh(force)` and orphan a subprocess, leak background tasks, or
tear the tool catalog. Manifest drift (the archived `@modelcontextprotocol/server-*`
set), an unescaped YAML cache feeding the risk gate, a matcher that penalizes
well-described servers, and a `find_project_root` that ascends too far round out the
defects.

**Class 2 — the architectural gap v6 already flagged.** PMCP advertises OAuth 2.1
discovery (PRM doc, `WWW-Authenticate` challenge) but validates a *static shared
secret* — no signature/issuer/audience/expiry checks. To host a multi-tenant tenant
code-mode server (the v6 north star), PMCP must become a real OAuth 2.1 Resource
Server with audience binding (RFC 8707) and per-tenant credential isolation, and
should consume the official MCP Registry to self-heal its manifest.

This roadmap sequences the bug fixes first (additive, backward-compatible, shippable
as v1.14.0) and then the two feature phases (new auth semantics + registry), each
gated by a narrow interface freeze. Raw material to reuse: the stronger scrubber
already at `handlers.py:2983` (`_scrub_sensitive_text`), the existing PRM/challenge
scaffolding in `transport/http.py`, the scoped env-store in `env_store.py`, and the
manifest/matcher machinery in `manifest/`.

---

## Architecture North Star

```
        client(s)  ──►  PMCP gateway  ──►  downstream MCP servers
                         │                  (lazy, incl. tenant code-mode)
   ┌─────────────────────┼───────────────────────────────────┐
   │  transport/http.py  │  OAuth 2.1 Resource Server         │  ← AUTHRS
   │   • validate AS token: sig/iss/exp/AUD (RFC 8707)        │
   │   • static-bearer = explicit single-tenant mode          │
   ├─────────────────────┼───────────────────────────────────┤
   │  policy/policy.py    │  ONE canonical redact() + truncate │  ← REDACT
   │   • every outbound field (result/summary/task/log)        │
   ├─────────────────────┼───────────────────────────────────┤
   │  client/manager.py   │  lifecycle lock covers connect;    │  ← CONCURR
   │   • all background tasks tracked + cancelled on teardown   │
   │   • per-tenant downstream credential resolution            │  ← AUTHRS
   ├─────────────────────┼───────────────────────────────────┤
   │  manifest/ + registry│  matcher scoring + MCP Registry sync│ ← MANIFEST/REGISTRY
   └─────────────────────┴───────────────────────────────────┘
```

---

## Assumptions (fail-loud if wrong)

1. The four "failing" auth tests are the `/tmp/.git` environment artifact (Appendix A
   of the review), not code regressions — verified: they pass with `TMPDIR` outside
   `/tmp`. The roadmap treats baseline as green.
2. The findings in `plans/codebase-review-2026-06-15.md` are accurate at the cited
   `file:line`; each was reproduced at runtime or confirmed at source.
3. PMCP must stay backward compatible: existing `gateway.*` tools and response fields
   keep working; redaction-on-by-default and new auth modes are additive/opt-in at the
   transport layer.
4. The OAuth 2.1 / RFC 8707 work targets MCP spec revision **2025-11-25** (current
   stable); the 2026-07-28 release candidate is tracked, not built to.
5. The official MCP Registry (`registry.modelcontextprotocol.io`) is in preview and may
   change schema; REGISTRY consumes it defensively (pin/cache, tolerate drift).

---

## Non-Goals

- No new gateway meta-tool and no local script execution — PMCP still only brokers.
- No SSO/RBAC/billing/tenant-identity service; AUTHRS makes PMCP a Resource Server, not
  an Authorization Server.
- No live hosted infrastructure or real cloud credentials required for any test.
- No new transport layer; no streaming-log transport.
- REGISTRY does not auto-install servers; it only refreshes discovery metadata.

---

## Cross-Cutting Principles

1. **Redact at one chokepoint.** There is a single canonical redaction function; every
   outbound field routes through it. No path constructs user-facing text from raw
   downstream output.
2. **Tests must fail on `main` first.** Every bug-fix exit criterion ships a regression
   test that reproduces the defect before the fix and passes after.
3. **`client/manager.py` is a single-writer file.** Phases/lanes touching it serialize;
   the owning lane is named explicitly to prevent cross-lane corruption.
4. **Additive compatibility.** No existing response field changes shape; new behavior is
   defaulted-safe or opt-in.
5. **Run the suite with `TMPDIR` outside `/tmp`** so the env artifact never masks a real
   regression.
6. **Secrets never reach logs.** Diagnostic/log/error paths use the same sanitizer as
   return paths.

---

## Phase Dependency DAG

```
  REDACT    CONCURR    MANIFEST    ENVFIX       ← four independent roots (parallel)
     │         │           │          │
     └────┬────┴───────────┼──────────┘
          ▼                │
       AUTHRS  (after REDACT, CONCURR, ENVFIX)
          │                │
          └────────┬───────┘
                   ▼
               REGISTRY  (after MANIFEST, AUTHRS)
```

Stage A (REDACT, CONCURR, MANIFEST, ENVFIX) is four parallel roots → cut v1.14.0.
Stage B (AUTHRS → REGISTRY) is the feature critical path.

---

## Top Interface-Freeze Gates

These gates are the narrowest contracts that unblock downstream phases.
`/claude-plan-phase` concretizes each (exact signature/schema) when it plans the
owning phase.

1. **IF-0-REDACT-1** — A single canonical `redact_secrets(text) -> str` (and its
   pattern set) that is truncation-independent, covers bare tokens (`sk-`, `ghp_`,
   `github_pat_`) and key=value forms, and is the one function every outbound
   field (result, summary, `task.status_message`, `task.raw`, connect-failure logs)
   calls. Redaction defaults ON for task/code-mode results.
2. **IF-0-CONCURR-1** — `_lifecycle_lock` covers the connect/registration paths, and a
   manager-level `set[asyncio.Task]` tracks every background task (reconnect, stderr
   reader, in-flight connect) so all are cancelled+awaited on teardown; request IDs
   carry a per-connection epoch so a stale `gateway.cancel` cannot hit a new request.
3. **IF-0-MANIFEST-1** — Matcher scores on absolute/IDF-weighted matched-keyword count
   (not division by the server's own keyword count); the descriptions cache is written
   via `yaml.safe_dump`; manifest entries carry a `status`/transport shape that
   distinguishes active first-party from archived community servers.
4. **IF-0-ENVFIX-1** — Scoped credential writes are atomic+0600 (`os.open`, no
   write-then-chmod window) and `find_project_root` is bounded so project-scope writes
   land in the intended repo root, exposed as a resolver other phases can reuse.
5. **IF-0-AUTHRS-1** — Transport validates AS-issued tokens (signature, `iss`,
   `exp`/`nbf`, and `aud` == PMCP's canonical resource URI per RFC 8707) when in
   `resource-server` mode; the legacy static bearer remains as an explicitly-named
   `shared-secret` single-tenant mode. Invalid/expired → 401; wrong audience → reject.
6. **IF-0-AUTHRS-2** — A per-tenant downstream credential resolution interface
   (replacing the single shared process-env header lookup) so each tenant's downstream
   headers resolve from an isolated scope.
7. **IF-0-REGISTRY-1** — A registry client that reads `registry.modelcontextprotocol.io`
   `/v0/servers`, plus a manifest-sync path that reconciles cached registry entries
   against the local manifest and backs `gateway.request_capability` with live lookups.

---

## Phases

### Phase 1 — Redaction Hardening (REDACT)

**Objective**
Make secret redaction a single, truncation-independent chokepoint that every outbound
field routes through, closing the confirmed leak paths (C1, C2, H1, H2, H3, M1, M2).

**Exit criteria**
- [ ] `summary` is built from post-redaction text; a truncated result with a secret on
  line 1 returns no secret in `summary` (regression test fails on `main`). [C1]
- [ ] `task.status_message` and `task.raw` are redacted with `redact_secrets=True` on
  `gateway.invoke` and `gateway.tasks_result` (regression test). [C2]
- [ ] `redact=True` no longer caps output at 400 chars; redaction and truncation are
  independent and `truncated`/`raw_size` are accurate (regression test). [H1]
- [ ] Bare `sk-…`, `ghp_…`, `github_pat_…` tokens are redacted in results via the unified
  pattern set; `_scrub_sensitive_text` and `DEFAULT_REDACTION_PATTERNS` share one source
  (regression test). [H2]
- [ ] Remote-connect-failure log lines route through `_sanitize_error` at
  `handlers.py:3689` and `manager.py:1288` (regression/assert no raw URL in logs). [H3]
- [ ] `redact_secrets` defaults to ON for task/code-mode result paths. [M1]
- [ ] The free-text diagnostic redactor covers `session|sid|cookie|set-cookie|refresh_token|`
  `client_secret|access_token|id_token|jwt|assertion|saml`. [M2]
- [ ] `ruff`, CI mypy baseline (`uv run mypy src/pmcp --exclude baml_client`), and full `pytest` (TMPDIR outside `/tmp`) green.

**Scope notes**
- Decompose into ≥3 lanes with disjoint ownership: (a) `policy/policy.py` — unify the
  pattern set, add bare-token patterns, decouple redaction from the 400-char auth
  helper, redact summary; (b) `tools/handlers.py` + `client/manager.py:1288` — wire
  task/log redaction through the canonical function (single-writer note: this lane
  touches manager.py line 1288 only; coordinate with CONCURR if co-scheduled); (c)
  `auth.py` — extend `sanitize_auth_diagnostic` keyword set; (d) `tests/test_policy.py`
  + `tests/test_tools.py` — failing-first regression tests.
- Lane (a) publishes the `redact_secrets` signature as IF-0-REDACT-1 on day 1 so lanes
  (b)/(c)/(d) build against the contract.

**Non-goals**
- No change to truncation byte/char semantics beyond decoupling it from redaction.
- No new policy config surface.

**Key files**
- `src/pmcp/policy/policy.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/auth.py`
- `src/pmcp/client/manager.py`
- `tests/test_policy.py`
- `tests/test_tools.py`

**Depends on**
- (none)

**Produces**
- IF-0-REDACT-1

---

### Phase 2 — Concurrency & Lifecycle Hardening (CONCURR)

**Objective**
Close the shared-gateway concurrency defects in `client/manager.py`: extend the
lifecycle lock over the connect paths, track and cancel all background tasks, and make
request-ID/cancel correlation safe across reconnects (C3, H4, H5, M3, M4, L1).

**Exit criteria**
- [ ] `refresh(force)`/`disconnect_all` serialize against concurrent
  `ensure_connected`/`connect_server`; a test that gathers refresh vs lazy-connect
  shows no orphaned subprocess and no torn catalog (fails on `main`). [C3]
- [ ] Reconnect, stderr-reader, and in-flight connect tasks are tracked in a
  `set[asyncio.Task]` and cancelled+awaited by `disconnect_all`/`shutdown`; a test
  asserts no task/subprocess survives shutdown. [H4, H5, L1]
- [ ] Request IDs carry a per-connection epoch (or monotonic-across-reconnect) so a
  stale `gateway.cancel("srv::N")` from a prior generation cannot cancel a new
  request (regression test). [M3]
- [ ] Reconnect-storm guard is keyed by server name at manager level, not on the
  replaced `ManagedClient` (regression test across a crash/reconnect cycle). [M4]
- [ ] `ruff`, `mypy`, full `pytest` (TMPDIR outside `/tmp`) green; no new flakes under
  repeated runs.

**Scope notes**
- `client/manager.py` is a **single-writer file** — its two implementation lanes
  serialize. Sequence: lane-lock (lifecycle-lock coverage of `_connect_singleflight`/
  `_connect_stdio` registration) → lane-tasks (background-task set + teardown
  cancellation + request-ID epoch + reconnect-guard). Lane-tests
  (`tests/test_client_manager.py`, `tests/test_server_lifecycle.py`) owns the test
  files and runs in parallel against the published invariant.
- Publish the task-set + epoch invariant as IF-0-CONCURR-1 early so the test lane and
  AUTHRS's per-tenant header lane plan against it.

**Non-goals**
- No redesign of the reconnect/backoff policy beyond correctness.
- No change to the singleton-lock scheme.

**Key files**
- `src/pmcp/client/manager.py`
- `src/pmcp/server.py`
- `tests/test_client_manager.py`
- `tests/test_server_lifecycle.py`

**Depends on**
- (none)

**Produces**
- IF-0-CONCURR-1

---

### Phase 3 — Manifest & Matcher Correctness (MANIFEST)

**Objective**
Fix the YAML-cache injection into the risk gate, the matcher ranking bias, and the
manifest drift off archived reference servers, plus the low-severity manifest cleanups
(M5, M6, stale repoint, user-agent, serial lookups, env-path message, M8).

**Exit criteria**
- [ ] The descriptions cache is written via `yaml.safe_dump` (no hand-rolled f-string);
  a tool name containing quotes/newlines cannot inject a sibling `risk_hint` key
  (regression test feeding a hostile tool name through the cache round-trip). [M5]
- [ ] Matcher scores on absolute/IDF-weighted matched-keyword count; a well-described
  (many-keyword) server with a precise multi-keyword match outranks a sparse one and
  stays above threshold (regression test). [M6]
- [ ] Archived `@modelcontextprotocol/server-*` entries are repointed to current
  first-party servers or labeled, with a documented audit of the ~15 entries (github →
  github remote, brave, linear, sentry remote, etc.). [stale-manifest]
- [ ] `_USER_AGENT` interpolates `pmcp.__version__`. [LOW]
- [ ] `refresh_all` / `pmcp refresh` version lookups run via `asyncio.gather` with a
  concurrency cap instead of fully serial. [LOW]
- [ ] `MissingApiKeyError` names the file PMCP actually reads (`.env.pmcp` /
  `~/.config/pmcp/pmcp.env`) via `resolve_scope_path`. [LOW]
- [ ] `detect_package_type` strips any `@<tag>` suffix, not just literal `@latest`. [M8]
- [ ] `ruff`, `mypy`, full `pytest` green.

**Scope notes**
- Four disjoint-file lanes (high parallelism): (a) `manifest/refresher.py` — yaml.safe_dump;
  (b) `manifest/matcher.py` — scoring; (c) `manifest/manifest.yaml` — repoint/label
  (single-writer: the manifest file); (d) `manifest/version_checker.py` +
  `manifest/installer.py` — user-agent, gather, env-path message, tag-strip.
- Tests split across `tests/test_refresher.py`, `tests/test_manifest.py`,
  `tests/test_install_command.py`.

**Non-goals**
- No live registry consumption here (that is REGISTRY); repointing is manual/curated.
- No change to install transport mechanics beyond the version-detection fix.

**Key files**
- `src/pmcp/manifest/manifest.yaml`
- `src/pmcp/manifest/refresher.py`
- `src/pmcp/manifest/matcher.py`
- `src/pmcp/manifest/version_checker.py`
- `src/pmcp/manifest/installer.py`
- `tests/test_manifest.py`
- `tests/test_refresher.py`

**Depends on**
- (none)

**Produces**
- IF-0-MANIFEST-1

---

### Phase 4 — Config & Env-Store Footguns (ENVFIX)

**Objective**
Eliminate the credential-file TOCTOU and the `find_project_root` over-ascent so scoped
secrets are written atomically with restrictive perms to the intended repo root
(env_store TOCTOU + the Appendix-A footgun).

**Exit criteria**
- [ ] `write_env_file` creates the secret file already 0600 via `os.open(..., O_CREAT|
  O_WRONLY|O_TRUNC, 0o600)` with no world-readable window (regression test asserts mode
  at no point exceeds 0600).
- [ ] `find_project_root` is bounded so project-scope `auth_connect` writes to the
  intended cwd/repo and does not silently ascend to an unrelated ancestor marker; a
  test reproducing the `/tmp/.git` ancestor case passes without `TMPDIR` juggling.
- [ ] A reusable scope-path resolver is exposed for AUTHRS to build per-tenant storage on.
- [ ] `ruff`, `mypy`, full `pytest` green.

**Scope notes**
- Two disjoint-file lanes: (a) `env_store.py` — atomic 0600 write; (b)
  `config/loader.py` — bounded `find_project_root` + resolver export. Tests in
  `tests/test_config_loader.py`, `tests/test_secrets_command.py`.
- This also removes the environment artifact that masks 4 tests (Appendix A), so CI no
  longer depends on `TMPDIR` placement.

**Non-goals**
- No change to the env-store file format or scope precedence semantics.

**Key files**
- `src/pmcp/env_store.py`
- `src/pmcp/config/loader.py`
- `tests/test_config_loader.py`
- `tests/test_secrets_command.py`

**Depends on**
- (none)

**Produces**
- IF-0-ENVFIX-1

---

### Phase 5 — OAuth 2.1 Resource Server (AUTHRS)

**Objective**
Turn PMCP from a static-shared-secret guard into a real OAuth 2.1 Resource Server:
validate AS-issued tokens with audience binding (RFC 8707), keep the static bearer as
an explicit single-tenant mode, and isolate downstream credentials per tenant — the
multi-tenant prerequisite v6 flagged.

**Exit criteria**
- [ ] In `resource-server` mode, the transport validates JWTs via the AS JWKS
  (signature, `iss`, `exp`/`nbf`) and rejects tokens whose `aud` ≠ PMCP's canonical
  resource URI; invalid/expired → 401, wrong audience → reject (tests with signed
  fixtures; no live AS). [P0]
- [ ] The legacy static bearer is preserved behind an explicit `shared-secret` mode flag
  and is no longer the only path; mode selection is documented and tested.
- [ ] Downstream credential resolution is per-tenant: each tenant's remote headers
  resolve from an isolated scope rather than one shared process env (test proves tenant
  A cannot read tenant B's downstream headers). [P1]
- [ ] `scope` is included in the 401 challenge and insufficient scope at runtime returns
  403 `WWW-Authenticate: error="insufficient_scope"`. [P1]
- [ ] `sanitize_public_auth_url` blocks private/link-local/loopback ranges (SSRF) and the
  transport returns 403 on invalid `Origin`. [P2]
- [ ] README/SECURITY distinguish `shared-secret` vs `resource-server` modes and state
  PMCP alone is not multi-tenant-complete without an external AS.
- [ ] `ruff`, `mypy`, full `pytest` green.

**Scope notes**
- ≥4 lanes: (a) token validation (JWKS fetch+cache, JWT verify) in `transport/http.py`
  + `remote_auth.py`/`auth.py`; (b) audience binding + mode flag + challenge/scope
  semantics in `transport/http.py`; (c) per-tenant credential resolution in
  `remote_auth.py` + `client/manager.py` (single-writer manager.py — depends on
  IF-0-CONCURR-1; this lane owns the `_remote_headers` region); (d) SSRF/Origin
  hardening in `auth.py` + transport; (e) docs `README.md`/`SECURITY.md` + tests
  `tests/test_auth.py`/`tests/test_http_transport.py`.
- Reuses ENVFIX's scope resolver (IF-0-ENVFIX-1) for per-tenant storage and routes all
  new auth-error text through REDACT's canonical redactor (IF-0-REDACT-1).
- Publish the token-validation contract (IF-0-AUTHRS-1) and per-tenant resolution
  interface (IF-0-AUTHRS-2) early so REGISTRY's remote-server lane plans against them.

**Non-goals**
- No Authorization Server, no DCR (now MAY/legacy — skip), no SSO/RBAC/billing.
- No building to the 2026-07-28 release candidate.

**Key files**
- `src/pmcp/transport/http.py`
- `src/pmcp/remote_auth.py`
- `src/pmcp/auth.py`
- `src/pmcp/client/manager.py`
- `README.md`
- `SECURITY.md`
- `tests/test_auth.py`
- `tests/test_http_transport.py`

**Depends on**
- REDACT
- CONCURR
- ENVFIX

**Produces**
- IF-0-AUTHRS-1
- IF-0-AUTHRS-2

---

### Phase 6 — MCP Registry Consumption & Server Expansion (REGISTRY)

**Objective**
Consume the official MCP Registry to self-heal the manifest against archival churn and
back `gateway.request_capability` with live lookups, and add the high-value
vendor-official (remote-first) servers the review identified.

**Exit criteria**
- [ ] A registry client reads `registry.modelcontextprotocol.io` `/v0/servers` with
  timeouts, caching, and schema-drift tolerance (preview); offline/failure modes degrade
  to the local manifest (tests use a recorded/mock registry response, no live calls).
- [ ] A manifest-sync path reconciles registry entries against the local manifest
  (flags renamed/archived, surfaces first-party replacements) without auto-installing.
- [ ] `gateway.request_capability` / `catalog_search` can surface registry-backed
  candidates alongside the local manifest, respecting AUTHRS resource/audience semantics
  for remote vendor-official servers.
- [ ] The high-value remote vendor-official servers are added (GitHub remote, Atlassian
  Rovo, Cloudflare remote set, Sentry remote, Vercel, Hugging Face) plus verified
  stdio additions, each with correct transport and placeholder headers (no secrets).
- [ ] `ruff`, `mypy`, full `pytest` green; `uv build`; `git diff --check`.

**Scope notes**
- ≥4 lanes: (a) registry client/loader (new module under `manifest/`); (b) manifest-sync
  reconciliation; (c) new server entries in `manifest.yaml` (single-writer); (d)
  `request_capability`/`catalog_search` live-lookup integration in `tools/handlers.py`;
  (e) docs + tests (`tests/test_manifest.py`, `tests/test_offline_discovery.py`,
  `tests/test_tools.py`, `README.md`, `CHANGELOG.md`).
- Depends on MANIFEST's matcher/schema (IF-0-MANIFEST-1) and AUTHRS's resource/audience
  contract (IF-0-AUTHRS-1) for remote-server auth shape.

**Non-goals**
- No auto-install of registry servers; discovery metadata only.
- No private-registry support; no building to registry schemas not yet GA.

**Key files**
- `src/pmcp/manifest/manifest.yaml`
- `src/pmcp/manifest/loader.py`
- `src/pmcp/manifest/refresher.py`
- `src/pmcp/tools/handlers.py`
- `tests/test_manifest.py`
- `tests/test_offline_discovery.py`
- `README.md`
- `CHANGELOG.md`

**Depends on**
- MANIFEST
- AUTHRS

**Produces**
- IF-0-REGISTRY-1

---

## Execution Notes

- **Planning**: `/claude-plan-phase <ALIAS>` for each phase. The four Stage-A roots —
  `REDACT`, `CONCURR`, `MANIFEST`, `ENVFIX` — share no DAG ancestor and can be planned
  and executed concurrently. `AUTHRS` plans after REDACT/CONCURR/ENVFIX merge;
  `REGISTRY` after MANIFEST/AUTHRS merge.
- **Execution**: `/claude-execute-phase <alias>` after each plan is approved.
- **Critical path**: `CONCURR → AUTHRS → REGISTRY` (the longest chain; REDACT and ENVFIX
  also feed AUTHRS but are lighter). Stage A alone is one parallel wave → cut **v1.14.0**
  before starting Stage B.
- **Parallel branches**: all four Stage-A phases run as one wave; `MANIFEST` then also
  feeds `REGISTRY` at the end.
- **Single-writer files across phases**: `client/manager.py` is touched by REDACT (line
  1288 only), CONCURR (lifecycle/tasks), and AUTHRS (per-tenant headers) — sequence
  CONCURR before AUTHRS, and land REDACT's one-line log fix inside the CONCURR window or
  rebase it. `manifest.yaml` is touched by MANIFEST (repoint) and REGISTRY (additions) —
  MANIFEST owns it first, REGISTRY rebases.

---

## Acceptance Criteria

- [ ] No outbound field (result, summary, `task.status_message`, `task.raw`, logs) can
  carry a secret when redaction is requested or defaulted-on — proven by regression
  tests that fail on the pre-v7 `main`.
- [ ] Under concurrent multi-client load, `refresh(force)` against a lazy-connect leaves
  no orphaned subprocess, no leaked task, and no torn catalog.
- [ ] PMCP validates AS-issued tokens with audience binding in `resource-server` mode and
  isolates downstream credentials per tenant, while the static-bearer single-tenant mode
  still works.
- [ ] The manifest no longer ships archived/abandoned reference servers as if current,
  and PMCP can refresh discovery metadata from the official registry offline-safely.
- [ ] Full release verification passes before the v1.14.0 (Stage A) and subsequent
  (Stage B) version bumps.

---

## Verification

Run after the relevant phases merge (TMPDIR outside `/tmp` until ENVFIX lands):

```bash
# Redaction (REDACT)
uv run pytest tests/test_policy.py tests/test_tools.py -k "redact or summary or task or secret"

# Concurrency & lifecycle (CONCURR)
uv run pytest tests/test_client_manager.py tests/test_server_lifecycle.py -k "refresh or reconnect or cancel or shutdown or concurrent"

# Manifest & matcher (MANIFEST)
uv run pytest tests/test_manifest.py tests/test_refresher.py -k "matcher or cache or version or stale"

# Config & env-store (ENVFIX)
TMPDIR=/var/tmp uv run pytest tests/test_config_loader.py tests/test_secrets_command.py

# Auth (AUTHRS)
uv run pytest tests/test_auth.py tests/test_http_transport.py -k "token or audience or tenant or origin or scope"

# Registry (REGISTRY)
uv run pytest tests/test_manifest.py tests/test_offline_discovery.py tests/test_tools.py -k "registry or capability or catalog"

# Whole-roadmap release gate
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
uv run pytest -q
uv build
git diff --check
```
</content>
