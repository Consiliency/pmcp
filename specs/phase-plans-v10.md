# PMCP — Phase Plan v10 (Code-Review Remediation)

> How to use this document: save to `specs/phase-plans-v10.md`, then run `/claude-plan-phase <ALIAS>` to produce the lane-level plan for each phase (→ `plans/phase-plan-v10-<alias>.md`), then `/claude-execute-phase <alias>` to build it.

---

## Context

A complete five-subsystem code review of PMCP v1.18.0 (commit `1c907db`) found a well-built, defensively-written codebase (no `shell=True`, timing-safe auth, strict JWT, fail-soft parsing, 81% coverage, ruff+mypy clean) with risk concentrated in two themes: **failure-recovery paths that are thinner than the happy paths**, and **security features that are fully implemented and tested but never wired to the running server**. This roadmap remediates the verified findings without re-architecting the healthy core.

The single highest-impact finding was verified by execution: **stdio auto-reconnect is broken** — killing a downstream stdio server triggers a self-referential task cancellation in `_cleanup_client` (`_cancel_background_tasks` cancels the currently-running connect task) that raises `RecursionError`, so a crashed server never recovers. Every existing reconnect test mocks the connect path, which is why it was missed. Remote (SSE/HTTP) servers never even schedule a reconnect. Recovering these paths (P1) ships first and independently.

The security findings split into: **unwired defenses** (OAuth resource-server mode and Origin/DNS-rebinding validation exist and are tested but `server.py` never passes the params — P2); **agent-reachable code-exec surfaces** (an unvalidated package name reaches `npx -y <name>`; `auth_connect` stores an arbitrary env var like `LD_PRELOAD` — P3); **local credential exposure** (`secrets set` on argv, world-traversable secret dir, case-insensitive policy — P4); and **DoS/SSRF hardening** across the transport and registry HTTP paths (P5A/P5B). P6 sweeps the remaining robustness/quality items.

Each phase is independently shippable as a patch/minor release (v1.18.x → v1.19.0) via the existing flow (bump `pyproject.toml`+`__init__.py`+`uv.lock`, promote CHANGELOG, tag `vX.Y.Z` → `release.yml` → PyPI).

---

## Assumptions (fail-loud if wrong)

1. The five call-site conventions and public gateway tool schemas stay stable; fixes are internal behavior, not protocol changes (no breaking changes to `mcp__pmcp__*` tool inputs/outputs).
2. The reconnect `RecursionError` reproduces deterministically (it did: kill a real stdio server → `_cleanup_client` → recursion) and is fixed by making task cancellation current-task-safe — not by a deeper redesign of the task registry.
3. Native Windows remains a supported target (per pyproject classifiers); any new signal/lock/fs code keeps the existing `hasattr`/`sys.platform` guards.
4. The manifest-overlay and provisioning trust model is "same as `.mcp.json`" (local-machine trust); hardening adds validation/warnings, not a new sandbox.
5. `release.yml` + PyPI trusted publishing keep working; the validator runtime (`phase-loop`) is installed.

---

## Non-Goals

- No architectural refactor of the 5386-line `handlers.py` or the 2586-line `manager.py` — targeted fixes only.
- No new transport, no new auth backend beyond wiring the existing resource-server mode.
- No change to manifest-overlay precedence or the private-registry opt-in model.
- No attempt to sandbox provisioned subprocesses (out of scope; validation + confirmation only).
- No coverage-percentage target; add tests for the fixed paths, don't chase a number.

---

## Cross-Cutting Principles

1. **Preserve the verified strengths — never regress them:** no `shell=True`/list-argv exec only; `hmac.compare_digest`; strict JWT (`alg=none` rejected, `iss/exp/nbf/aud` required, algorithms allowlisted); `_sanitize_error` on caller-facing errors; bounded audit/feedback buffers; fail-soft manifest/registry parsing; hardened singleton lock; process-group reaping; shielded idle-timeout.
2. **Fail-closed on security decisions.** A validation/wiring change defaults to the safe posture (reject unknown Origin, reject non-conforming package name, don't store a non-declared env var) rather than the permissive one.
3. **Every phase ships green:** full suite passes, `ruff check` + `ruff format --check` + `mypy src/pmcp/` clean, plus a CHANGELOG entry and version bump. Run `ruff format` before pushing (CI checks `--check`).
4. **Add a real (non-mocked) test for each recovery/security fix** — the reconnect bug proves that mocking the path under test hides the bug.
5. **Backward compatible:** new CLI flags/env vars are additive with safe defaults; no existing invocation changes behavior except where a finding requires it (documented in CHANGELOG).

---

## Phase Dependency DAG

```
  P1  Reconnect & failure-path recovery (manager.py)
   │
   ▼
  P6  Robustness & quality sweep            (P6 after P1)

  P2  Auth / Origin wiring (server+cli+http)
   │
   ├──────────────► P4  Local credential hardening   (P4 after P2)
   │
   └──────────────► P5A DoS hardening (http.py)       (P5A after P2)

  P3  Agent-reachable code-exec validation   parallel root (types+handlers)
  P5B SSRF / registry hardening (auth+registry)  parallel root

  Parallel roots (start immediately): P1, P2, P3, P5B
  After P1: P6.   After P2: P4, P5A.
  Critical path length = 2 phases (P2 → P4 | P5A, or P1 → P6).
```

---

## Top Interface-Freeze Gates

These are the narrowest contracts that unblock downstream phases. `/claude-plan-phase` concretizes each exact signature when it plans the owning phase.

1. **IF-0-P1-1** — `manager.py` task-cancellation + client-lifecycle contract is finalized: `_cancel_background_tasks(...)` never cancels `asyncio.current_task()` (new default-safe behavior), and connect error-paths remove the client from `_clients` and cancel its stderr task. Consumed by **P6** (which adds `_tasks` pruning / `disconnect_server` locking on the same file).
2. **IF-0-P2-1** — `GatewayServer.__init__` gains `auth_mode`, `resource_server_issuer`, `resource_server_jwks_url`, `resource_server_audience`, `required_scopes: list[str] | None`, `allowed_origins: list[str] | None` (all optional, safe defaults), plus the CLI flag/env names (`--auth-mode`, `--oauth-issuer`, `--oauth-jwks-url`, `--oauth-audience`, `--required-scope`, `--allowed-origin` / `PMCP_ALLOWED_ORIGINS`). Consumed by **P4** (also edits `cli.py`) and **P5A** (also edits `http.py`).
3. **IF-0-P3-1** — a shared validation surface: `is_valid_package_name(name) -> bool` (strict npm/pypi name regex, rejects leading `-`) and `env_var_allowed_for_server(env_var, server_config) -> bool`. Consumed within **P3**; published day-1 so the handlers lane and the types/schema lane start against it.

---

## Phases

### Phase 1 — Reconnect & failure-path recovery (P1)

**Objective**
Make downstream servers actually recover from transient failure: fix the stdio reconnect `RecursionError` self-cancel, add remote (SSE/HTTP) auto-reconnect parity, and clean up failed connects so no stale ERROR client or leaked stderr task remains.

**Exit criteria**
- [ ] A real (non-mocked) integration test kills a live stdio server subprocess and asserts the manager returns it to `ONLINE` within the reconnect backoff — the test fails on today's code (`RecursionError`) and passes after the fix.
- [ ] `_cancel_background_tasks` never cancels `asyncio.current_task()` (unit test asserts calling it from within a tracked task does not cancel that task).
- [ ] A dropped remote (SSE/HTTP) server schedules a reconnect (parity with stdio); test drives `_read_sse`'s finally path.
- [ ] A connect that fails at `initialize` leaves no entry in `_clients` and no live stderr reader task (test asserts both).
- [ ] Full suite + ruff + mypy green; CHANGELOG "Fixed" entry.

**Scope notes**
- Single-writer file: `src/pmcp/client/manager.py` — all three fixes edit it; keep them in one lane to avoid conflicts. Second lane owns `tests/` (new `test_client_manager_reconnect.py` integration + unit tests) and can start against the fix's intended behavior.
- Lane A (`manager.py`): (1) exclude `current_task()` in `_cancel_background_tasks` and/or scope `_cleanup_client` to the old client's read/stderr tasks; (2) mirror `_read_stdout`'s `_schedule_reconnect` into `_read_sse`'s finally; (3) pop `_clients`/`_cleanup_client` + cancel the stderr task on both connect error paths.
- Lane B (`tests/`): reuse the real stdio server at `diagnostics/issue-79-1b/slow_server.py` as the crash target.
- Preserve process-group reaping and the shielded idle-timeout (do not touch those code paths).

**Non-goals**
- `_tasks` unbounded growth and the `disconnect_server` lock window (deferred to P6 — same file, after this lands).

**Key files**
- src/pmcp/client/manager.py
- tests/test_client_manager*.py (new integration test)

**Depends on**
- (none)

**Produces**
- IF-0-P1-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/client/manager.py`, `tests/`
- evidence paths: `plans/phase-plan-v10-p1.md`
- redaction posture: `metadata_only`

---

### Phase 2 — Auth / Origin wiring (P2)

**Objective**
Connect the already-implemented, already-tested OAuth resource-server auth mode and Origin/Host validation to the running server so operators can actually enable them; default posture rejects cross-origin browser requests (DNS-rebinding defense).

**Exit criteria**
- [ ] `GatewayServer` + CLI accept and pass `auth_mode`, `resource_server_*`, `required_scopes`, and `allowed_origins` to `create_http_app` (test asserts a resource-server-configured server actually validates a JWT and rejects an unsigned/aud-mismatched token end-to-end).
- [ ] With no explicit config, a cross-origin browser `Origin` header is rejected 403 on `/mcp` (test), while same-origin / no-Origin (non-browser) requests pass; `Host` is validated.
- [ ] `pmcp --help` documents the new flags; `PMCP_ALLOWED_ORIGINS` env honored.
- [ ] No regression to `none` / `shared-secret` modes (existing auth tests pass unchanged).
- [ ] Full suite + ruff + mypy green; CHANGELOG "Added/Fixed" + README auth section.

**Scope notes**
- Publish IF-0-P2-1 (the `GatewayServer.__init__` signature + flag names) on day 1 so the three lanes start against the contract.
- Lane A (`server.py` + wiring): thread params through `GatewayServer.__init__`, `run_http`, and the `create_http_app(...)` call (currently only `auth_token/rate_limit_rpm/request_timeout`).
- Lane B (`cli.py`): add the `--auth-mode`/`--oauth-*`/`--required-scope`/`--allowed-origin` flags + env; single-writer on `cli.py` for this phase (P4 edits `cli.py` after this freeze).
- Lane C (`http.py`): choose a safe default for `allowed_origins` (reject cross-origin browser Origins) and add `Host` validation; `http.py` single-writer for this phase (P5A edits it after).
- Decide default-origin policy explicitly (loopback binds still need DNS-rebinding defense).

**Non-goals**
- The keepalive-stream DoS and body-size cap in `http.py` (P5A, after this).

**Key files**
- src/pmcp/server.py
- src/pmcp/cli.py
- src/pmcp/transport/http.py

**Depends on**
- (none)

**Produces**
- IF-0-P2-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/server.py`, `src/pmcp/cli.py`, `src/pmcp/transport/http.py`, `README.md`
- evidence paths: `plans/phase-plan-v10-p2.md`
- redaction posture: `metadata_only`

---

### Phase 3 — Agent-reachable code-exec validation (P3)

**Objective**
Close the two paths where agent/registry-supplied data reaches code execution: validate the package identifier before it becomes `npx -y <name>`, and stop `auth_connect` from storing an arbitrary process-affecting env var (`LD_PRELOAD`/`NODE_OPTIONS`/`PATH`). Also scrub all feedback fields and lock the provision handoff (same file, same security theme).

**Exit criteria**
- [ ] `register_discovered_server`/`provision` reject a package name that isn't a valid npm/pypi identifier or that starts with `-` (test: `-g`, `../evil`, `a b` rejected; `@scope/name`, `name` accepted); the exact install command is surfaced for confirmation before execution.
- [ ] `auth_connect` refuses to write an `env_var` not declared by the target server (or not on an allowlist) — test asserts `LD_PRELOAD` is rejected, the server's declared `env_var` is accepted.
- [ ] `submit_feedback` runs `_scrub_sensitive_text` over `title`, `failed_tool_call`, and `subordinate_server` (test asserts a secret in each is redacted before the payload is built).
- [ ] `provision_status` guards the `server_ready → adopt_process` handoff with a per-job lock/CAS and does not re-run a full `refresh` on repeat polls of a finished job (test: two concurrent polls adopt once).
- [ ] Full suite + ruff + mypy green; CHANGELOG "Security/Fixed".

**Scope notes**
- Publish IF-0-P3-1 (`is_valid_package_name`, `env_var_allowed_for_server`) day 1.
- Single-writer file: `src/pmcp/tools/handlers.py` (all four items touch it) — one handlers lane; a second lane owns `src/pmcp/types.py` (add the `pattern=` to `RegisterDiscoveredServerInput.package`) + the shared validator module + `tests/`.
- Disjoint from P1/P2/P5 files → this is a parallel root.

**Non-goals**
- Sandboxing the provisioned subprocess; the fix is validate + confirm, not isolate.

**Key files**
- src/pmcp/tools/handlers.py
- src/pmcp/types.py
- src/pmcp/manifest/ (validator helper, if placed there)

**Depends on**
- (none)

**Produces**
- IF-0-P3-1

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/tools/handlers.py`, `src/pmcp/types.py`
- evidence paths: `plans/phase-plan-v10-p3.md`
- redaction posture: `metadata_only`

---

### Phase 4 — Local credential hardening (P4)

**Objective**
Stop leaking local credentials: take `secrets set` off the argv, create the secret directory `0700`, and resolve the case-insensitive policy match. Fold in the remaining `cli.py` UX findings (status eager-connect, doctor 401 disambiguation, env-override sentinels).

**Exit criteria**
- [ ] `pmcp secrets set <key>` reads the value via `getpass`/`--stdin` (positional value optional); test asserts no value on argv.
- [ ] The secret directory (`~/.config/pmcp` / project) is created mode `0700` (test asserts dir bits, not just the `0600` file).
- [ ] Policy allow/deny matching is case-sensitive (or the case-insensitivity is confirmed intentional and documented) — test pins the decision.
- [ ] `pmcp status` with no gateway does NOT eagerly connect every server (defaults to the lazy/no-connect view); a flag opts into active probing.
- [ ] `doctor` distinguishes 401/403 ("gateway up, needs auth") from unreachable; explicit `--flag` overrides the env-override magic-number sentinels (`==8`/`==60`).
- [ ] Full suite + ruff + mypy green; CHANGELOG.

**Scope notes**
- Lanes are file-disjoint → 3 parallel lanes: (a) `cli.py` (secrets stdin + status/doctor/sentinels), (b) `env_store.py` (0700 dir), (c) `policy.py` (case decision).
- `cli.py` is single-writer here; depends on P2 having frozen the CLI auth-flag surface so the two `cli.py` edits don't collide.

**Non-goals**
- `secrets sync --dry-run` and other UX niceties (fold into P6 if time permits, else defer).

**Key files**
- src/pmcp/cli.py
- src/pmcp/env_store.py
- src/pmcp/policy/policy.py

**Depends on**
- P2

**Produces**
- (none)

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/cli.py`, `src/pmcp/env_store.py`, `src/pmcp/policy/policy.py`
- evidence paths: `plans/phase-plan-v10-p4.md`
- redaction posture: `metadata_only`

---

### Phase 5A — Transport DoS hardening (P5A)

**Objective**
Bound the HTTP transport's resource usage: cap and time-limit the unauthenticated pre-session keepalive SSE streams, and enforce the body-size limit against chunked/unadvertised-length POSTs.

**Exit criteria**
- [ ] Concurrent pre-session keepalive streams are capped and have an idle deadline (test: N+1th stream is rejected or the stream closes after the deadline).
- [ ] A chunked POST exceeding the 10 MB body cap is rejected while reading (test with no/false `content-length`), not just on the header.
- [ ] `/health` + `/metrics` exposure decision documented (optionally bindable/gated on non-loopback).
- [ ] Full suite + ruff + mypy green; CHANGELOG.

**Scope notes**
- Decompose into 2 lanes: (a) `src/pmcp/transport/http.py` implementation (keepalive-stream cap + idle deadline + streaming body-size enforcement) as the single writer of that file, and (b) `tests/` covering both limits (disjoint file, starts against the intended behavior).
- Depends on P2 (which also edits `http.py` for Origin/Host); take the file after that freeze.

**Non-goals**
- Rate-limiter proxy/`X-Forwarded-For` handling (LOW; fold into P6 or defer).

**Key files**
- src/pmcp/transport/http.py

**Depends on**
- P2

**Produces**
- (none)

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/transport/http.py`
- evidence paths: `plans/phase-plan-v10-p5a.md`
- redaction posture: `metadata_only`

---

### Phase 5B — SSRF & registry hardening (P5B)

**Objective**
Harden the outbound HTTP paths: disable redirects on JWKS/metadata fetches (SSRF), guard the registry response size during read (OOM), require https + a host allowlist for the private registry endpoint, and make the registry cache write atomic with tight perms.

**Exit criteria**
- [ ] JWKS fetch (`auth.py` aiohttp) and `fetch_json_metadata` (`auth.py` urlopen) use `allow_redirects=False` / a no-redirect opener and re-validate any target (test: a 302 to an internal host is not followed).
- [ ] The registry client enforces the 2 MB cap during read (streamed / `Content-Length` pre-check), not after `resp.read()` (test: oversized response aborts early).
- [ ] With `PMCP_REGISTRY_ALLOW_PRIVATE` on, a non-https or non-allowlisted `PMCP_REGISTRY_PRIVATE_ENDPOINT` is rejected (test).
- [ ] Registry cache is written via temp + `os.replace` (atomic) with restrictive perms (test).
- [ ] Full suite + ruff + mypy green; CHANGELOG.

**Scope notes**
- File-disjoint from all other phases → parallel root (depends on none).
- 2 lanes: (a) `src/pmcp/auth.py` (redirect hardening), (b) `src/pmcp/manifest/registry.py` (size guard + endpoint allowlist + atomic cache).

**Non-goals**
- Version-checker URL escaping (`version_checker.py`) — grouped into P6.

**Key files**
- src/pmcp/auth.py
- src/pmcp/manifest/registry.py

**Depends on**
- (none)

**Produces**
- (none)

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/auth.py`, `src/pmcp/manifest/registry.py`
- evidence paths: `plans/phase-plan-v10-p5b.md`
- redaction posture: `metadata_only`

---

### Phase 6 — Robustness & quality sweep (P6)

**Objective**
Clear the remaining MED/LOW robustness and quality findings across the manager, manifest overlay, config loader, and version checker, and de-flake the sleep-based subprocess tests.

**Exit criteria**
- [ ] `_tasks` registry evicts terminal (completed/failed/cancelled) records (TTL/LRU); `disconnect_server` inspects/cancels pending state under `_lifecycle_lock`; dead `managed.request_id` removed; `background_task` done-callback uses `pop(t, None)` (tests where practical).
- [ ] Manifest overlay logs a prominent warning when an overlay entry shadows a shipped server by name, and `_find_project_manifest` keeps discovery within the resolved project root (no out-of-tree symlink follow).
- [ ] Malformed `.mcp.json` parse errors are surfaced in `pmcp status` (not silently swallowed in `load_configs`); `find_project_root` does not resolve to `$HOME`.
- [ ] Version-check URLs are `urllib.parse.quote`-escaped for pypi/cargo/docker.
- [ ] `test_monitor_reads_stderr` and `test_monitor_keeps_last_20_lines` poll job state instead of `asyncio.sleep(0.3)` (no wall-clock race).
- [ ] Full suite + ruff + mypy green; CHANGELOG.

**Scope notes**
- File-disjoint lanes → up to 5 parallel: (a) `manager.py` (after P1's IF-0-P1-1 — task/lifecycle stable), (b) `manifest/loader.py`, (c) `config/loader.py`, (d) `manifest/version_checker.py` + misc, (e) `tests/` de-flake.
- Only lane (a) shares a file (`manager.py`) with a prior phase (P1) → the P1 dependency; the other lanes have no cross-phase file conflict and could technically start earlier, but are grouped here as the cleanup release.

**Non-goals**
- Any behavior change to the manifest-overlay precedence or the gateway tool schemas.

**Key files**
- src/pmcp/client/manager.py
- src/pmcp/manifest/loader.py
- src/pmcp/config/loader.py
- src/pmcp/manifest/version_checker.py
- tests/test_manifest.py

**Depends on**
- P1

**Produces**
- (none)

**Spec closeout policy**
- schema: `spec_delta_closeout.v1`
- decision: `no_spec_delta`
- target surfaces: `src/pmcp/client/manager.py`, `src/pmcp/manifest/loader.py`, `src/pmcp/config/loader.py`, `src/pmcp/manifest/version_checker.py`, `tests/`
- evidence paths: `plans/phase-plan-v10-p6.md`
- redaction posture: `metadata_only`

---

## Execution Notes

- **Planning**: `/claude-plan-phase <ALIAS>` per phase. Parallel roots with no shared DAG ancestor can be planned concurrently: **P1, P2, P3, P5B** all start immediately.
- **Execution**: `/claude-execute-phase <alias>` after each plan is approved. **P4** and **P5A** unblock once **P2** merges (they take `cli.py` / `http.py` after P2's interface freeze). **P6** unblocks once **P1** merges (shared `manager.py`).
- **Critical path**: 2 phases deep — `P2 → P4` (or `P2 → P5A`), and `P1 → P6`. With enough executors, wall-clock ≈ two phase cycles.
- **Single-writer files across phases**: `cli.py` = P2 then P4; `http.py` = P2 then P5A; `manager.py` = P1 then P6; `handlers.py` = P3 only. Respect these owners to prevent cross-phase serialization/merge conflicts.
- **Release cadence**: ship each phase (or a small batch) as its own tagged release. Suggested: P1 → v1.18.1 (critical fix); P2 → v1.19.0 (new auth flags); P3/P4/P5A/P5B → v1.19.x security patches; P6 → v1.19.x. Adjust to taste; the DAG, not the versioning, governs order.

---

## Acceptance Criteria

- [ ] A killed downstream stdio server auto-recovers to ONLINE (the `RecursionError` is gone), and a dropped remote server reconnects — both proven by non-mocked integration tests.
- [ ] An operator can enable OAuth resource-server auth and Origin validation via CLI/env and see them enforced end-to-end; the default posture rejects cross-origin browser requests.
- [ ] An invalid/hostile package name is rejected before any `npx` execution, and `auth_connect` cannot store a non-declared process-affecting env var.
- [ ] `secrets set` never places a credential on argv; the secret directory is `0700`.
- [ ] Outbound JWKS/metadata/registry fetches don't follow redirects to internal hosts and can't OOM the gateway; the transport bounds keepalive streams and chunked bodies.
- [ ] Full suite green (≥ current 2100 passing), ruff (lint+format) + mypy clean, at every phase's merge; the two known-flaky monitor tests are deterministic.

## Verification

```bash
# Per phase (and at the end): the standing green bar
cd /home/viperjuice/code/pmcp
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src/ tests/ && .venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/pmcp/

# P1 — reconnect actually recovers (this command FAILS on today's code, PASSES after P1)
.venv/bin/python -m pytest tests/ -k "reconnect and stdio" -v

# P2 — auth/Origin are reachable and enforced
.venv/bin/python -m pytest tests/ -k "resource_server or origin or dns_rebind" -v

# P3 — code-exec surfaces validated
.venv/bin/python -m pytest tests/ -k "package_name or auth_connect_env or feedback_scrub" -v

# P4 — no secret on argv, 0700 dir
.venv/bin/python -m pytest tests/ -k "secrets and (stdin or mode or dir)" -v

# P5A/P5B — DoS + SSRF bounds
.venv/bin/python -m pytest tests/ -k "keepalive_cap or chunked_body or redirect or registry_size" -v

# End-to-end smoke: gateway still starts, health reports version, tools list
.venv/bin/python -m pmcp --version
```
