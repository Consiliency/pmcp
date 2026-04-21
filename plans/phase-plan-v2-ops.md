# OPS: Shared-Service Operations and Policy

## Context

Phase 1 froze deterministic `ClientManager` lifecycle mutation and same-server single-flight behavior. Phases 2 and 3 added explicit shared-service disruption semantics: refresh refuses active downstream work unless `force=true`, and lifecycle stop/restart operations are runtime-only controls that can affect shared downstream servers.

Phase 4 is an operations and policy phase. It should not introduce broad new APIs. The work should tighten the user-facing contract for one shared PMCP HTTP gateway used by multiple clients, make `pmcp doctor` and `pmcp status` distinguish gateway reachability from downstream server state, and document rate-limit, auth, lifecycle, and unauthenticated health/metrics expectations.

The current implementation already has streamable HTTP on `/mcp`, unauthenticated `/health` and `/metrics`, optional bearer auth on `/mcp`, per-IP rate limiting, singleton locking, live `pmcp status` probing through `gateway.health`, and startup-policy fields in health/status output. Current gaps to close are operational clarity and diagnostic consistency: README still has one shared-service sentence using "SSE mode", `pmcp doctor` docs mention an `http` check that the implementation does not yet expose as a named check, and status output can make downstream lifecycle state more explicit without changing JSON payloads.

## Interface Freeze Gates

- [x] IF-0-OPS-1 — README documents HTTP streamable transport as the supported multi-client shared-service mode, and stdio as single-process local mode; no docs refer to SSE as the preferred shared-service mode.
- [x] IF-0-OPS-2 — README or SECURITY documents that `--rate-limit` / `PMCP_RATE_LIMIT` is enforced per source IP on `/mcp`, so localhost clients and reverse-proxied clients may share one bucket unless the proxy preserves distinct client IPs.
- [x] IF-0-OPS-3 — `pmcp doctor` emits a named `http` diagnostic that probes shared gateway HTTP reachability without requiring bearer auth; the probe targets `/health` derived from `PMCP_GATEWAY_URL` or the default `http://127.0.0.1:3344/mcp`.
- [x] IF-0-OPS-4 — `pmcp doctor` continues to emit `lock`, `mode`, `remote`, and `install` diagnostics and exits nonzero only for failed checks; new HTTP reachability warnings do not print credentials or auth token values.
- [x] IF-0-OPS-5 — Human `pmcp status` text labels live gateway snapshots separately from downstream server lifecycle rows, while JSON status preserves existing `gateway.health` pass-through fields.
- [x] IF-0-OPS-6 — Shared-service docs warn that `gateway.refresh(force=true)`, `gateway.disconnect_server(force=true)`, and `gateway.restart_server(force=true)` can interrupt downstream work started by another client sharing the gateway.
- [x] IF-0-OPS-7 — `/health` and `/metrics` remain unauthenticated; SECURITY documents that they must be protected at the network layer before any non-localhost exposure.

## Lane Index & Dependencies

- SL-0 — Operational interface copy freeze; Depends on: (none); Blocks: SL-1, SL-2, SL-3; Parallel-safe: no
- SL-1 — CLI diagnostics and status wording; Depends on: SL-0; Blocks: SL-3, SL-4; Parallel-safe: yes
- SL-2 — HTTP observability contract tests; Depends on: SL-0; Blocks: SL-3, SL-4; Parallel-safe: yes
- SL-3 — Shared-service documentation and security policy; Depends on: SL-0, SL-1, SL-2; Blocks: SL-4; Parallel-safe: no
- SL-4 — Roadmap and closeout synthesis; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Operational Interface Copy Freeze

- **Scope**: Freeze the exact operational terms and diagnostic contracts used by CLI output, tests, README, and SECURITY before implementation starts.
- **Owned files**: `plans/phase-plan-v2-ops.md`
- **Interfaces provided**: terms `shared-service HTTP mode`, `single-process stdio mode`, `gateway reachability`, `downstream server lifecycle state`, `per source IP rate limit`, doctor check names `lock`, `mode`, `http`, `remote`, `install`
- **Interfaces consumed**: IF-0-SERIALIZE-1, IF-0-REFRESH-2, IF-0-LIFECYCLE-3, existing CLI command names, existing `/mcp`, `/health`, and `/metrics` routes
- **Parallel-safe**: no
- **Tasks**:
  - test: Defer assertions to SL-1, SL-2, and SL-3 because those lanes own the user-facing files.
  - impl: Confirm the plan uses HTTP, not SSE, for shared-service wording.
  - impl: Confirm doctor's new HTTP probe is reachability-only and does not require or reveal bearer auth.
  - impl: Confirm status wording changes are human-output-only; JSON remains pass-through for live snapshots.
  - verify: Review this plan for exact shared terms before starting code/docs edits.

### SL-1 — CLI Diagnostics and Status Wording

- **Scope**: Add the missing HTTP reachability diagnostic and make human status output distinguish gateway status from downstream server lifecycle state.
- **Owned files**: `src/pmcp/cli.py`, `src/pmcp/cli_commands/doctor.py`, `tests/test_cli.py`
- **Interfaces provided**: `pmcp doctor` named `http` check, `/health` probe helper or equivalent, human `pmcp status` gateway/downstream labels, CLI regression coverage for IF-0-OPS-3 through IF-0-OPS-5
- **Interfaces consumed**: existing `_get_gateway_url()`, `_query_running_gateway_status(...)`, `_load_local_mcp_json(...)`, `_is_pmcp_system_service_active()`, `collect_remote_header_diagnostics(...)`, existing live status JSON pass-through behavior
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a `run_doctor` test proving a reachable default or env-provided gateway produces `[OK] http:` with a `/health` reachability message.
  - test: Add a `run_doctor` test proving an unreachable shared gateway produces `[WARN] http:` or `[FAIL] http:` according to the frozen severity without leaking URL credentials or token values.
  - test: Add a `run_status` human-output test proving live snapshots print a gateway reachability/header line and label server rows as downstream server state.
  - test: Keep or extend JSON status tests proving live snapshot JSON preserves health fields unchanged.
  - impl: Add an HTTP health probe that derives `/health` from `PMCP_GATEWAY_URL` or the default `/mcp` gateway URL and uses the existing doctor timeout.
  - impl: Include the named `http` diagnostic in `run_doctor` output alongside `lock`, `mode`, `remote`, and `install`.
  - impl: Update human `run_status` copy only; do not rename JSON keys or reshape live snapshot payloads.
  - verify: `uv run pytest tests/test_cli.py -k "doctor or status"`
  - verify: `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_cli.py`

### SL-2 — HTTP Observability Contract Tests

- **Scope**: Lock down the existing HTTP operational surface so docs can rely on unauthenticated health/metrics, authenticated MCP traffic, and per-IP rate limiting.
- **Owned files**: `src/pmcp/transport/http.py`, `tests/test_http_transport.py`
- **Interfaces provided**: tests for unauthenticated `/health`, unauthenticated `/metrics`, bearer auth limited to `/mcp`, and per-IP `/mcp` rate limiting behavior
- **Interfaces consumed**: existing `create_http_app(...)`, `_check_rate_limit(...)`, route list for `/mcp`, `/health`, `/metrics`, existing Starlette response behavior
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or tighten route tests proving `/health` returns `ok`, `version`, and `transport: http` without bearer auth even when `auth_token` is configured.
  - test: Add or tighten route tests proving `/metrics` is present and unauthenticated.
  - test: Add a focused rate-limit test proving repeated `/mcp` requests from the same client IP share one bucket and can return HTTP 429.
  - impl: Avoid changing HTTP route behavior unless tests expose a mismatch with the frozen contract.
  - impl: If a tiny helper improves testability for health/metrics/rate-limit state reset, keep it private and additive.
  - verify: `uv run pytest tests/test_http_transport.py -k "health or metrics or rate_limit or auth"`
  - verify: `uv run ruff check src/pmcp/transport/http.py tests/test_http_transport.py`

### SL-3 — Shared-Service Documentation and Security Policy

- **Scope**: Document the shared-service operational contract, rate-limit/auth expectations, lifecycle disruption behavior, and diagnostic workflow.
- **Owned files**: `README.md`, `SECURITY.md`, `CHANGELOG.md`
- **Interfaces provided**: user-facing documentation for IF-0-OPS-1, IF-0-OPS-2, IF-0-OPS-6, IF-0-OPS-7, release note for OPS documentation/diagnostic changes
- **Interfaces consumed**: SL-0 frozen wording, SL-1 doctor/status behavior, SL-2 HTTP observability contract, Phase 2 refresh force semantics, Phase 3 lifecycle force semantics
- **Parallel-safe**: no
- **Tasks**:
  - test: Add documentation text checks only if the repo already uses docs assertions; otherwise rely on CLI and HTTP tests from SL-1 and SL-2.
  - impl: Replace the README shared-service "SSE mode" sentence with streamable HTTP shared-service wording.
  - impl: Add a concise README section explaining shared gateway state: multiple clients share downstream server connections, pending work, refresh/lifecycle disruption, startup observations, and rate-limit buckets.
  - impl: Update `pmcp doctor` docs to match actual check names and HTTP `/health` reachability behavior.
  - impl: Update SECURITY to state that per-IP rate limiting is shared by clients with the same observed source IP and that `/health` and `/metrics` stay unauthenticated by design.
  - impl: Mention bearer auth expectations for any non-localhost `/mcp` exposure and avoid suggesting auth protects `/health` or `/metrics`.
  - impl: Add a CHANGELOG entry under Unreleased if this branch is release-bound.
  - verify: `uv run ruff check README.md SECURITY.md CHANGELOG.md` only if the configured tools support markdown; otherwise verify by review plus SL-1 and SL-2 tests.

### SL-4 — Roadmap and Closeout Synthesis

- **Scope**: Mark OPS completion and record verification after the implementation and documentation lanes are done.
- **Owned files**: `specs/phase-plans-v2.md`
- **Interfaces provided**: completed Phase 4 checklist, recorded execution deviations if any
- **Interfaces consumed**: SL-1 doctor/status test results, SL-2 HTTP contract test results, SL-3 documentation updates, IF-0-OPS-1 through IF-0-OPS-7
- **Parallel-safe**: no
- **Tasks**:
  - impl: Mark Phase 4 exit criteria complete in `specs/phase-plans-v2.md` only after all producer lanes have been implemented and verified.
  - impl: Record any deviations from this plan in the final execution response rather than editing unrelated roadmap phases.
  - verify: `uv run pytest tests/test_cli.py tests/test_http_transport.py`
  - verify: `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/transport/http.py tests/test_cli.py tests/test_http_transport.py`
  - verify: `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_server.py -q` if docs/status wording changes depend on Phase 2 or Phase 3 lifecycle semantics.

## Verification

Lane-specific verification:

- `uv run pytest tests/test_cli.py -k "doctor or status"`
- `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_cli.py`
- `uv run pytest tests/test_http_transport.py -k "health or metrics or rate_limit or auth"`
- `uv run ruff check src/pmcp/transport/http.py tests/test_http_transport.py`

Whole-phase regression:

- `uv run pytest tests/test_cli.py tests/test_http_transport.py`
- `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_server.py -q` if implementation edits touch refresh/lifecycle status semantics or shared health fields.
- `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/transport/http.py tests/test_cli.py tests/test_http_transport.py`
- `uv run pytest -q` before release handoff if time permits.

Manual smoke after implementation:

- `pmcp doctor`
- `PMCP_GATEWAY_URL=http://127.0.0.1:3344/mcp pmcp doctor`
- `pmcp status --verbose`
- `pmcp status --json`

## Acceptance Criteria

- [x] README explains that streamable HTTP mode is the supported multi-client shared-service mode and stdio is single-process local mode.
- [x] README and/or SECURITY document that the `/mcp` rate limit is per observed source IP and may be shared by localhost or reverse-proxied clients.
- [x] `pmcp doctor` surfaces a named HTTP reachability diagnostic for shared-service mode and continues to report singleton lock state.
- [x] Human `pmcp status` output clearly separates PMCP gateway reachability/metadata from downstream server lifecycle state.
- [x] Lifecycle and refresh docs warn that one client can affect shared downstream servers and active work used by another client.
- [x] SECURITY keeps `/health` and `/metrics` unauthenticated by design and documents network-layer protection requirements for non-localhost exposure.
- [x] Tests cover CLI diagnostics/status wording and HTTP health/metrics/auth/rate-limit contracts changed or frozen by this phase.
