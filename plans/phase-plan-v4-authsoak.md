# AUTHSOAK: Auth Soak, Docs, and Release Gate

## Context

Phase 6 of `specs/phase-plans-v4.md` is the v4 production auth release gate. It
depends on the completed STORE, REMOTE, ELICIT, SAFEURL, and AUTHOBS contracts
and should not introduce new public gateway tools, CLI commands, model fields,
or auth semantics unless deterministic soak coverage exposes a production
readiness gap that must be fed back into an earlier contract.

The current repo already has focused coverage for env-store writes, remote
header placeholder detection, URL-mode acknowledgement, public auth URL
validation, redaction, auth challenge parsing, structured auth states, bounded
audit events, `pmcp status --verbose`, `pmcp doctor`, feedback previews, and an
AUTHOBS end-to-end smoke in `tests/test_phase4_e2e.py`. Phase 6 should connect
those pieces into one offline third-party auth matrix, update operator-facing
documentation, and record release evidence only after verification passes.

`specs/phase-plans-v4.md` is tracked and clean at planning time. This generated
plan artifact is new and will be untracked until staged. The worktree also
contains existing Phase 5 implementation and plan edits; execution must preserve
those changes and treat IF-0-AUTHOBS-5 as a prerequisite contract.

## Interface Freeze Gates

- [x] IF-0-AUTHSOAK-1 - Phase 6 consumes IF-0-STORE-1, IF-0-REMOTE-2, IF-0-ELICIT-3, IF-0-SAFEURL-4, and IF-0-AUTHOBS-5 as frozen contracts; any runtime source change is limited to a contract-preserving defect fix discovered by the soak tests.
- [x] IF-0-AUTHSOAK-2 - Offline deterministic coverage models missing API key, present API key, missing remote bearer header, remote `WWW-Authenticate`, insufficient scope, URL elicitation required, URL elicitation acknowledgement, and malicious auth URLs without live providers or external credentials.
- [x] IF-0-AUTHSOAK-3 - The local API-key smoke proves `gateway.provision` reports missing auth, `gateway.auth_connect` stores an opaque credential through the shared env store, retrying the gateway flow succeeds or advances without missing auth, and no credential value appears in outputs, audit events, feedback previews, status, or doctor text.
- [x] IF-0-AUTHSOAK-4 - Remote header and downstream challenge smoke proves gateway outputs, `gateway.health`, CLI status, doctor, and feedback previews surface `auth_state`, `auth_event`, `missing_env_vars`, `auth_challenge`, `auth_metadata`, URL elicitation IDs, and `next_step` as non-secret structured evidence.
- [x] IF-0-AUTHSOAK-5 - `README.md` and `SECURITY.md` document supported auth modes, non-goals, URL-mode expectations, env-store scope guidance, redaction limits, and HTTP exposure expectations in operator-facing language.
- [x] IF-0-AUTHSOAK-6 - `CHANGELOG.md`, `specs/phase-plans-v4.md`, and this plan record production auth hardening and release verification evidence only after the full release gate passes.

## Lane Index & Dependencies

- SL-0 - Auth boundary matrix primitives; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: yes
- SL-1 - Gateway and client fake auth matrix; Depends on: SL-0; Blocks: SL-2, SL-3, SL-4; Parallel-safe: yes
- SL-2 - CLI and end-to-end auth smoke; Depends on: SL-0, SL-1; Blocks: SL-3, SL-4; Parallel-safe: yes
- SL-3 - Operator docs and changelog; Depends on: SL-0, SL-1, SL-2; Blocks: SL-4; Parallel-safe: no
- SL-4 - Release verification and closeout; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Auth Boundary Matrix Primitives

- **Scope**: Freeze helper-level coverage for the auth inputs Phase 6 will reuse in gateway, CLI, and release smoke tests.
- **Owned files**: `tests/test_auth.py`, `tests/test_transport_http.py`
- **Interfaces provided**: offline helper evidence for malicious auth URL rejection/redaction, `WWW-Authenticate` challenge parsing, insufficient-scope metadata, URL-mode elicitation URL sanitization, and unauthenticated `/health` and `/metrics` exposure expectations
- **Interfaces consumed**: existing `sanitize_auth_diagnostic(...)`, `sanitize_url_elicitation_url(...)`, `sanitize_public_auth_url(...)`, `parse_www_authenticate(...)`, `normalize_auth_metadata(...)`, `fetch_json_metadata(...)`, `create_http_app(...)`, IF-0-SAFEURL-4, IF-0-ELICIT-3
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or consolidate parameterized matrix tests for malicious auth URLs containing userinfo, auth codes, bearer tokens, JWT-looking strings, and roadmap auth query keys across URL elicitation, auth metadata, and `WWW-Authenticate` parsing.
  - test: Add helper-level insufficient-scope and remote challenge cases proving missing scopes and public metadata survive while secret-bearing descriptions and query values are redacted.
  - test: Add HTTP transport assertions proving `/health` and `/metrics` remain unauthenticated by PMCP, `/mcp` bearer auth remains required when configured, and protected-resource metadata output does not expose `PMCP_AUTH_TOKEN` or caller auth headers.
  - impl: Encode the auth matrix using existing helper APIs and `TestClient` patterns; do not broaden accepted URL schemes, secret detectors, or HTTP auth policy.
  - impl: If a helper test exposes a frozen-contract defect, keep the corrective source patch minimal and record the feedback path in SL-4 before changing public behavior.
  - verify: `uv run pytest tests/test_auth.py tests/test_transport_http.py -k "auth or metadata or www_authenticate or elicitation or url or token or jwt or redact or health or metrics"`
  - verify: `uv run ruff check tests/test_auth.py tests/test_transport_http.py`

### SL-1 - Gateway and Client Fake Auth Matrix

- **Scope**: Add gateway/client offline fakes for local API-key, remote header, remote challenge, insufficient scope, URL elicitation, and env-store scope behavior.
- **Owned files**: `tests/test_tools.py`, `tests/test_client_manager.py`, `tests/test_secrets_command.py`
- **Interfaces provided**: gateway/client matrix evidence for IF-0-AUTHSOAK-2 and IF-0-AUTHSOAK-3, including no-secret assertions on gateway outputs, audit events, and feedback previews
- **Interfaces consumed**: SL-0 helper evidence, existing `MockClientManager`, `GatewayTools`, `Manifest`, `ServerConfig`, `ResolvedServerConfig`, `RemoteMcpServerConfig`, `MissingRemoteHeaderAuthError`, `read_env_file(...)`, IF-0-STORE-1, IF-0-REMOTE-2, IF-0-AUTHOBS-5
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a local API-key smoke that starts with `gateway.provision` returning `auth_state="missing_auth"`, calls `gateway.auth_connect` with a project or user-scope credential, retries the gateway flow, and asserts the credential value is absent from the result, env path display, health audit events, and feedback previews.
  - test: Add present API-key/env-store coverage proving credentials with shell-significant characters are read through the shared env store and do not require process-env-only setup.
  - test: Add remote header fakes proving missing bearer header placeholders fail before transport calls, while present placeholder values are sent only to the fake transport and never appear in structured diagnostics.
  - test: Add gateway invoke/provision/connect fakes for remote `WWW-Authenticate`, insufficient scope, URL elicitation required, URL elicitation acknowledgement, and malicious auth URLs.
  - test: Add `pmcp secrets check` coverage tying remote header placeholders and local API-key required vars into the same required/missing key report without values.
  - impl: Reuse existing deterministic fakes, monkeypatches, manifest objects, and temp HOME/project env stores; do not require 1Password, live provider accounts, network access, or real third-party credentials.
  - verify: `uv run pytest tests/test_tools.py tests/test_client_manager.py tests/test_secrets_command.py -k "auth or credential or secret or env_var or missing_env or header or www_authenticate or elicitation or scope or feedback"`
  - verify: `uv run ruff check tests/test_tools.py tests/test_client_manager.py tests/test_secrets_command.py`

### SL-2 - CLI and End-to-End Auth Smoke

- **Scope**: Prove the fake auth matrix reaches CLI/status/doctor and subprocess smoke surfaces with the same structured non-secret evidence.
- **Owned files**: `tests/test_cli.py`, `tests/test_phase4_e2e.py`
- **Interfaces provided**: cross-surface smoke evidence for IF-0-AUTHSOAK-3 and IF-0-AUTHSOAK-4 using local subprocess CLI helpers and live/fallback status snapshots
- **Interfaces consumed**: SL-0 helper evidence, SL-1 gateway/client matrix results, existing `_run_pmcp(...)` subprocess helper, existing `run_status(...)` and `run_doctor(...)` test patterns, IF-0-REMOTE-2, IF-0-ELICIT-3, IF-0-AUTHOBS-5
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add CLI status JSON and verbose tests proving missing remote bearer headers, remote challenges, insufficient scope, and URL elicitation evidence pass through as structured fields without parsing auth prose.
  - test: Add doctor tests proving remote header missing-auth, auth metadata diagnostics, URL elicitation next steps, and malicious auth URL samples render sanitized values and never print env values or credential samples.
  - test: Add an end-to-end fake auth smoke in `tests/test_phase4_e2e.py` that exercises provision -> auth connect -> retry for a local API-key flow using temp HOME/project state and asserts no credential leak in stdout, stderr, health audit events, or feedback preview text.
  - test: Add an end-to-end remote auth smoke covering missing remote header and remote `WWW-Authenticate`/insufficient-scope states through gateway health plus CLI/status-facing payloads.
  - impl: Reuse existing subprocess `python -m pmcp` setup, temp HOME/project directories, patched live-gateway snapshots, and local gateway fakes; keep all normal CI behavior offline and deterministic.
  - verify: `uv run pytest tests/test_cli.py tests/test_phase4_e2e.py -k "auth or credential or secret or missing_env or status or doctor or elicitation or scope or feedback or redact"`
  - verify: `uv run ruff check tests/test_cli.py tests/test_phase4_e2e.py`

### SL-3 - Operator Docs and Changelog

- **Scope**: Update operator documentation and release notes from the completed auth matrix without changing runtime behavior.
- **Owned files**: `README.md`, `SECURITY.md`, `CHANGELOG.md`
- **Interfaces provided**: documented auth modes, non-goals, URL-mode workflow, env-store scope guidance, redaction limits, HTTP exposure expectations, and release-bound production auth hardening note
- **Interfaces consumed**: SL-0 helper matrix findings, SL-1 gateway/client matrix findings, SL-2 CLI/E2E smoke findings, Phase 6 docs exit criteria, existing README auth/status/secrets/doctor sections, existing SECURITY threat model and limitations
- **Parallel-safe**: no
- **Tasks**:
  - test: Build a docs coverage checklist from IF-0-AUTHSOAK-5 and mark any missing README/SECURITY topic as a documentation failure before editing.
  - impl: Tighten `README.md` auth documentation so supported API-key, remote auth discovery, remote header env placeholders, URL-mode acknowledgement, env-store user/project scope, and non-goals are easy for operators to find.
  - impl: Tighten `SECURITY.md` so HTTP exposure, unauthenticated `/health` and `/metrics`, bearer auth on `/mcp`, redaction best-effort limits, no third-party OAuth token storage, and no cross-user credential isolation are explicit.
  - impl: Add or confirm a `CHANGELOG.md` Unreleased entry for production third-party auth hardening if this branch is release-bound; do not duplicate the existing 1.11.0 historical release notes.
  - verify: `rg -n "Auth And Elicitation|URL-mode|auth_connect|env-store|project scope|PMCP_AUTH_TOKEN|/health|/metrics|Redaction|refresh token|third-party" README.md SECURITY.md CHANGELOG.md`
  - verify: `rg -n "missing_auth|insufficient_scope|elicitation_required|policy_denied|no per-user|best-effort|Unreleased" README.md SECURITY.md CHANGELOG.md`

### SL-4 - Release Verification and Closeout

- **Scope**: Run the full release gate, then record Phase 6 completion and residual risks only after producer lanes and docs have passed.
- **Owned files**: `specs/phase-plans-v4.md`, `plans/phase-plan-v4-authsoak.md`
- **Interfaces provided**: completed AUTHSOAK acceptance checklist, release verification evidence, residual risk notes, and roadmap closeout state
- **Interfaces consumed**: SL-0 helper matrix results, SL-1 gateway/client matrix results, SL-2 CLI/E2E smoke results, SL-3 documentation updates, IF-0-AUTHSOAK-1 through IF-0-AUTHSOAK-6, roadmap release verification commands
- **Parallel-safe**: no
- **Tasks**:
  - test: Review every Phase 6 exit criterion and map it to named tests, docs lines, or explicit release verification output before marking anything complete.
  - impl: Mark this plan's interface gates and acceptance criteria complete only after lane-specific and whole-phase verification pass.
  - impl: Update `specs/phase-plans-v4.md` Phase 6 checklist to completed only after release evidence is available, and record any residual production auth risks without expanding PMCP into an OAuth provider.
  - impl: If any soak test exposes a runtime contract gap, stop the closeout, document the gap here, and route the fix back through the owning earlier-phase contract before publishing.
  - verify: `uv run pytest tests/test_auth.py tests/test_tools.py tests/test_client_manager.py tests/test_cli.py tests/test_secrets_command.py tests/test_transport_http.py tests/test_phase4_e2e.py -k "auth or credential or secret or env_var or missing_env or header or www_authenticate or elicitation or scope or redact or status or doctor or feedback"`
  - verify: `uv run ruff check src/ tests/`
  - verify: `uv run ruff format --check src/ tests/`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `uv run pytest -q`
  - verify: `uv build`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_auth.py tests/test_transport_http.py -k "auth or metadata or www_authenticate or elicitation or url or token or jwt or redact or health or metrics"`
- `uv run ruff check tests/test_auth.py tests/test_transport_http.py`
- `uv run pytest tests/test_tools.py tests/test_client_manager.py tests/test_secrets_command.py -k "auth or credential or secret or env_var or missing_env or header or www_authenticate or elicitation or scope or feedback"`
- `uv run ruff check tests/test_tools.py tests/test_client_manager.py tests/test_secrets_command.py`
- `uv run pytest tests/test_cli.py tests/test_phase4_e2e.py -k "auth or credential or secret or missing_env or status or doctor or elicitation or scope or feedback or redact"`
- `uv run ruff check tests/test_cli.py tests/test_phase4_e2e.py`
- `rg -n "Auth And Elicitation|URL-mode|auth_connect|env-store|project scope|PMCP_AUTH_TOKEN|/health|/metrics|Redaction|refresh token|third-party" README.md SECURITY.md CHANGELOG.md`
- `rg -n "missing_auth|insufficient_scope|elicitation_required|policy_denied|no per-user|best-effort|Unreleased" README.md SECURITY.md CHANGELOG.md`

Whole-phase regression and release gate:

- `uv run pytest tests/test_auth.py tests/test_tools.py tests/test_client_manager.py tests/test_cli.py tests/test_secrets_command.py tests/test_transport_http.py tests/test_phase4_e2e.py -k "auth or credential or secret or env_var or missing_env or header or www_authenticate or elicitation or scope or redact or status or doctor or feedback"`
- `uv run ruff check src/ tests/`
- `uv run ruff format --check src/ tests/`
- `uv run mypy src/pmcp --exclude baml_client`
- `uv run pytest -q`
- `uv build`

Release verification evidence recorded on 2026-04-23:

- `uv run pytest tests/test_auth.py tests/test_transport_http.py -k "auth or metadata or www_authenticate or elicitation or url or token or jwt or redact or health or metrics"` - 56 passed, 14 deselected.
- `uv run pytest tests/test_tools.py tests/test_client_manager.py tests/test_secrets_command.py -k "auth or credential or secret or env_var or missing_env or header or www_authenticate or elicitation or scope or feedback"` - 46 passed, 166 deselected.
- `uv run pytest tests/test_cli.py tests/test_phase4_e2e.py -k "auth or credential or secret or missing_env or status or doctor or elicitation or scope or feedback or redact"` - 60 passed, 44 deselected.
- `uv run pytest tests/test_auth.py tests/test_tools.py tests/test_client_manager.py tests/test_cli.py tests/test_secrets_command.py tests/test_transport_http.py tests/test_phase4_e2e.py -k "auth or credential or secret or env_var or missing_env or header or www_authenticate or elicitation or scope or redact or status or doctor or feedback"` - 163 passed, 223 deselected.
- `uv run ruff check src/ tests/` - passed.
- `uv run ruff format --check src/ tests/` - passed after formatting edited tests.
- `uv run mypy src/pmcp --exclude baml_client` - passed.
- `uv run pytest -q` - 1742 passed, 12 skipped, 21 deselected.
- `uv build` - built `dist/pmcp-1.12.0.tar.gz` and `dist/pmcp-1.12.0-py3-none-any.whl` after the release version bump.
- Docs coverage `rg` probes passed for auth modes, URL-mode, env-store scope, HTTP exposure, redaction, non-goals, and release-note terms.

Runtime feedback: the soak matrix found one frozen-contract redaction defect:
`bearer=` query values were not classified as auth-bearing query secrets.
`src/pmcp/auth.py` now includes `bearer` in `AUTH_SECRET_QUERY_KEYS`; no public
auth semantics, URL schemes, gateway tools, CLI commands, or model fields were
changed.

Manual/local smoke before version bump or publish:

- `pmcp secrets set PMCP_TEST_TOKEN test-token --scope project`
- `pmcp secrets check --project .`
- `pmcp status --json --pending`
- `pmcp status --verbose --pending`
- `pmcp doctor`

## Acceptance Criteria

- [x] Local fake downstream coverage models missing API key, present API key, missing remote bearer header, remote `WWW-Authenticate`, insufficient scope, URL elicitation required, URL elicitation acknowledgement, and malicious auth URLs.
- [x] End-to-end smoke covers provision -> auth connect -> retry for local API-key flows without leaking the credential.
- [x] End-to-end smoke covers remote header missing-auth and remote challenge states through gateway and CLI/status surfaces.
- [x] README and SECURITY document supported auth modes, non-goals, URL-mode expectations, env-store scope guidance, redaction limits, and HTTP exposure expectations.
- [x] CHANGELOG records production auth hardening when release-bound.
- [x] Full release verification passes before version bump or publish.
- [x] No normal CI test requires external credentials, live third-party providers, 1Password, GitHub auth, or cloud accounts.
- [x] AUTHSOAK does not broaden PMCP into an OAuth provider, enterprise identity broker, refresh-token store, DPoP/WIF implementation, or cross-user authorization layer.
