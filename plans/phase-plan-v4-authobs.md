# AUTHOBS: Auth Observability and Operator Semantics

## Context

Phase 5 of `specs/phase-plans-v4.md` depends on the REMOTE, ELICIT, and
SAFEURL contracts. The current worktree already has a closed `AuthState`
vocabulary, safe auth URL/diagnostic helpers, URL-mode elicitation
acknowledgement, remote-header `missing_env_vars` fields on several gateway
outputs, bounded `GatewayAuditEvent` health output, CLI status/doctor
diagnostics, and feedback issue previews.

The remaining AUTHOBS gap is operator precision. Auth state values exist, but
there is no single machine-readable semantics table with one primary next
action per state. Audit events include `auth_state`, but do not explicitly
categorize the auth event that occurred, so missing credentials, stored
credentials, remote challenges, insufficient scope, URL elicitation required,
and URL elicitation acknowledgement are not distinguishable without reading
method/error prose. Gateway and CLI surfaces should also consistently expose the
same non-secret evidence fields instead of relying on ad hoc rendering.

No roadmap-builder handoff was present for this repo/branch, so this plan uses
`specs/phase-plans-v4.md` directly. `specs/phase-plans-v4.md` is tracked and
clean. This generated plan artifact is new and will be untracked until staged.

## Interface Freeze Gates

- [x] IF-0-AUTHOBS-1 - `src/pmcp/types.py` defines `AuthStateSemanticsInfo` and a default semantics map covering every `AuthState` value with `meaning`, `primary_next_action`, and `evidence_fields`; `GatewayDiagnosticsInfo.auth_state_semantics` exposes that map in `gateway.health`.
- [x] IF-0-AUTHOBS-2 - `src/pmcp/types.py` defines `AuthEventKind = Literal["missing_credential", "credential_stored", "remote_auth_challenge", "insufficient_scope", "url_elicitation_required", "url_elicitation_acknowledged", "policy_denied"]`, and `GatewayAuditEvent.auth_event: AuthEventKind | None = None` is additive and redacted.
- [x] IF-0-AUTHOBS-3 - Env/header-based missing-auth outputs use `auth_state="missing_auth"` plus sorted, de-duplicated `missing_env_vars` on `ProvisionOutput`, `LifecycleServerOutput`, `ServerHealthInfo`, `EffectiveConfigEntry`, and `InvokeOutput` where applicable; legacy single `env_var` fields remain backward compatible.
- [x] IF-0-AUTHOBS-4 - `gateway.health` server rows, gateway diagnostics, and recent audit events expose only non-secret auth evidence: `auth_state`, `auth_event`, `next_step`, `missing_env_vars`, `auth_methods`, `auth_metadata`, `auth_challenge.missing_scopes`, and URL elicitation IDs/sanitized URLs.
- [x] IF-0-AUTHOBS-5 - `pmcp status --verbose`, `pmcp doctor`, and `gateway.submit_feedback` previews consume the same structured auth evidence and shared sanitizers, not auth prose parsing, when rendering operator diagnostics.
- [x] IF-0-AUTHOBS-6 - Tests cover all auth states, all AUTHOBS auth event kinds, and common secret samples across gateway outputs, health/audit, CLI status, doctor, and feedback previews.

## Lane Index & Dependencies

- SL-0 - Auth semantics and output model contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 - Gateway auth evidence and audit events; Depends on: SL-0; Blocks: SL-2, SL-3, SL-4; Parallel-safe: yes
- SL-2 - CLI status and doctor operator rendering; Depends on: SL-0, SL-1; Blocks: SL-3, SL-4; Parallel-safe: yes
- SL-3 - Cross-surface auth observability smoke; Depends on: SL-1, SL-2; Blocks: SL-4; Parallel-safe: yes
- SL-4 - Phase verification and closeout; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Auth Semantics and Output Model Contract

- **Scope**: Freeze additive public model fields for auth state semantics, auth event categorization, and env/header missing-auth evidence.
- **Owned files**: `src/pmcp/types.py`, `tests/test_auth.py`
- **Interfaces provided**: `AuthStateSemanticsInfo`, default auth-state semantics map, `GatewayDiagnosticsInfo.auth_state_semantics`, `AuthEventKind`, `GatewayAuditEvent.auth_event`, `InvokeOutput.missing_env_vars`
- **Interfaces consumed**: existing `AuthState`, `AuthMetadataInfo`, `AuthChallengeInfo`, `UrlElicitationInfo`, `ProvisionOutput.missing_env_vars`, `LifecycleServerOutput.missing_env_vars`, `ServerHealthInfo.missing_env_vars`, `EffectiveConfigEntry.missing_env_vars`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add type/model tests proving every `AuthState` literal has one semantics entry, one primary next action, and explicit evidence fields.
  - test: Add default-shape tests proving `GatewayDiagnosticsInfo` includes auth semantics by default and older minimal model construction still works.
  - test: Add output-model compatibility tests proving `InvokeOutput.missing_env_vars` defaults to `[]` and existing `ProvisionOutput`, `LifecycleServerOutput`, `ServerHealthInfo`, and `EffectiveConfigEntry` default shapes remain backward compatible.
  - impl: Add `AuthStateSemanticsInfo` and a small default semantics provider covering `none`, `missing_auth`, `insufficient_scope`, `elicitation_required`, `policy_denied`, and `unknown`.
  - impl: Add `AuthEventKind` and optional `auth_event` to `GatewayAuditEvent` without changing required constructor fields.
  - impl: Add additive `missing_env_vars: list[str] = Field(default_factory=list)` to `InvokeOutput` so lazy-connect missing header credentials can be returned with `auth_state="missing_auth"`.
  - verify: `uv run pytest tests/test_auth.py -k "auth_state or auth_event or semantics or missing_env"`
  - verify: `uv run ruff check src/pmcp/types.py tests/test_auth.py`

### SL-1 - Gateway Auth Evidence and Audit Events

- **Scope**: Emit structured AUTHOBS evidence from gateway auth boundaries, health, audit, and feedback preview paths.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: `_audit(..., auth_event=...)`, auth-event-tagged `GatewayAuditEvent` entries, health diagnostics with auth semantics, sanitized feedback preview auth evidence, `InvokeOutput.missing_env_vars` population for env/header missing auth
- **Interfaces consumed**: SL-0 `AuthEventKind`, SL-0 `GatewayDiagnosticsInfo.auth_state_semantics`, SL-0 `InvokeOutput.missing_env_vars`, REMOTE `MissingRemoteHeaderAuthError`, SAFEURL `sanitize_auth_diagnostic(...)`, SAFEURL `parse_www_authenticate(...)`, ELICIT `parse_url_elicitation_error(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add gateway audit tests proving missing API key or remote header auth uses `auth_event="missing_credential"` and includes env var names without values.
  - test: Add `gateway.auth_connect` tests proving API-key storage emits `auth_event="credential_stored"` and URL-mode acknowledgement emits `auth_event="url_elicitation_acknowledged"` without storing or printing third-party credentials.
  - test: Add invoke/connect/provision tests proving remote `WWW-Authenticate` challenges emit `remote_auth_challenge` or `insufficient_scope` based on parsed challenge evidence.
  - test: Add URL elicitation tests proving gateway invoke/provision/connect failures emit `url_elicitation_required` with sanitized URL elicitation evidence.
  - test: Add `gateway.health` tests proving `gateway_diagnostics.auth_state_semantics` is present and health rows expose only non-secret auth evidence for missing env vars, scopes, metadata, and elicitation IDs.
  - test: Add feedback preview tests proving recent auth events are included with `auth_event`, `auth_state`, `missing_env_vars`, scopes, and elicitation IDs while bearer tokens, API keys, auth codes, JWT-looking strings, URL userinfo, and secret query values are absent.
  - impl: Extend `_audit(...)` to accept `auth_event` and pass it through to `GatewayAuditEvent`.
  - impl: Add small gateway helpers to derive auth event kinds from missing credentials, parsed auth challenges, URL elicitation results, policy denial, and auth-connect acknowledgement paths.
  - impl: Populate `InvokeOutput.missing_env_vars` for missing remote-header credentials encountered while ensuring lazy servers or dispatching calls.
  - impl: Keep health and feedback evidence additive and sanitized through the existing SAFEURL sanitizer.
  - verify: `uv run pytest tests/test_tools.py -k "auth_event or auth_state or missing_env or audit or feedback or elicitation or scope or credential"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-2 - CLI Status and Doctor Operator Rendering

- **Scope**: Render the structured AUTHOBS evidence consistently in CLI operator surfaces without changing command names.
- **Owned files**: `src/pmcp/cli.py`, `src/pmcp/cli_commands/doctor.py`, `tests/test_cli.py`
- **Interfaces provided**: `pmcp status --verbose` auth semantics rendering, `pmcp doctor` auth evidence diagnostics, JSON pass-through of `auth_event` and `auth_state_semantics`, shared sanitized CLI auth evidence rendering
- **Interfaces consumed**: SL-0 auth semantics model fields, SL-1 health diagnostics and audit events, existing `_sanitize_cli_payload(...)`, existing `_probe_http_health(...)`, existing `collect_remote_header_diagnostics(...)`, REMOTE missing-env diagnostics, SAFEURL `sanitize_auth_diagnostic(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add live `pmcp status --json` tests proving `auth_state_semantics`, `auth_event`, and `missing_env_vars` pass through sanitized and unchanged.
  - test: Add live `pmcp status --verbose` tests proving each non-`none` auth state renders one primary next action or equivalent `next=` detail without parsing error prose.
  - test: Add doctor tests proving `/health` diagnostics expose auth metadata/semantics availability and bounded audit readiness without printing secrets.
  - test: Add local doctor remote-header tests proving missing env var names and next actions are consistent with live status wording and never include env values.
  - test: Add CLI secret-sample tests for bearer tokens, API keys, auth codes, JWT-looking strings, URL userinfo, and secret query values across status and doctor output.
  - impl: Add a compact CLI helper for rendering auth evidence from structured fields (`auth_state`, `auth_event`, `missing_env_vars`, `auth_challenge`, `url_elicitations`, `next_step`) and use it in status live/fallback paths.
  - impl: Extend `_probe_http_health(...)` and doctor remote checks to surface non-secret auth evidence from health diagnostics when present.
  - impl: Preserve existing JSON field names and human output layout except for additive auth evidence.
  - verify: `uv run pytest tests/test_cli.py -k "status or doctor or auth_state or auth_event or missing_env or secret"`
  - verify: `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_cli.py`

### SL-3 - Cross-Surface Auth Observability Smoke

- **Scope**: Add offline end-to-end smoke coverage proving gateway, CLI, doctor, and feedback previews expose the same non-secret auth evidence.
- **Owned files**: `tests/test_phase4_e2e.py`
- **Interfaces provided**: AUTHOBS cross-surface regression evidence using local fakes and subprocess CLI calls
- **Interfaces consumed**: SL-0 model fields, SL-1 gateway health/audit/feedback behavior, SL-2 CLI status/doctor behavior, existing `_run_pmcp(...)` subprocess helper, existing deterministic fake gateway/client patterns
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a local fake missing-auth smoke proving `gateway.health`, recent audit events, and CLI status JSON agree on `auth_state="missing_auth"` and `missing_env_vars`.
  - test: Add a local fake insufficient-scope smoke proving missing scope names appear through gateway health/status without provider tokens.
  - test: Add a URL elicitation smoke proving sanitized elicitation URL and `elicitation_id` appear while secret query values and auth codes do not.
  - test: Add a feedback-preview smoke seeded with recent auth events proving the preview issue body contains auth event categories and omits common secret samples.
  - impl: Reuse existing local fake clients, subprocess CLI helper, and monkeypatch patterns; do not require live providers, 1Password, GitHub, or real credentials.
  - verify: `uv run pytest tests/test_phase4_e2e.py -k "auth or status or doctor or feedback or secret"`
  - verify: `uv run ruff check tests/test_phase4_e2e.py`

### SL-4 - Phase Verification and Closeout

- **Scope**: Verify AUTHOBS end to end and record the final phase state without expanding into Phase 6 release documentation or soak coverage.
- **Owned files**: `plans/phase-plan-v4-authobs.md`
- **Interfaces provided**: completed AUTHOBS acceptance checklist, verification results, documentation-impact decision, residual-risk notes
- **Interfaces consumed**: SL-0 auth semantics and output model contract, SL-1 gateway auth evidence and audit events, SL-2 CLI/doctor rendering, SL-3 cross-surface smoke, Phase 5 exit criteria from `specs/phase-plans-v4.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Review lane verification results and confirm each Phase 5 exit criterion has named coverage.
  - impl: Mark this plan's interface gates and acceptance criteria complete only after implementation and verification pass.
  - impl: Record intentional deviations, especially if the semantics map or `AuthEventKind` field names change during implementation.
  - impl: Record that no README, SECURITY, or CHANGELOG update is required in this phase unless user-visible operator wording changes materially; release documentation remains Phase 6.
  - verify: `uv run pytest tests/test_auth.py tests/test_tools.py tests/test_cli.py tests/test_phase4_e2e.py -k "auth_state or auth_event or authobs or missing_env or audit or feedback or status or doctor or elicitation or scope or secret"`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_auth.py tests/test_tools.py tests/test_cli.py tests/test_phase4_e2e.py`
  - verify: `uv run ruff format --check src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_auth.py tests/test_tools.py tests/test_cli.py tests/test_phase4_e2e.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_auth.py -k "auth_state or auth_event or semantics or missing_env"`
- `uv run ruff check src/pmcp/types.py tests/test_auth.py`
- `uv run pytest tests/test_tools.py -k "auth_event or auth_state or missing_env or audit or feedback or elicitation or scope or credential"`
- `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`
- `uv run pytest tests/test_cli.py -k "status or doctor or auth_state or auth_event or missing_env or secret"`
- `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_cli.py`
- `uv run pytest tests/test_phase4_e2e.py -k "auth or status or doctor or feedback or secret"`
- `uv run ruff check tests/test_phase4_e2e.py`

Whole-phase regression:

- `uv run pytest tests/test_auth.py tests/test_tools.py tests/test_cli.py tests/test_phase4_e2e.py -k "auth_state or auth_event or authobs or missing_env or audit or feedback or status or doctor or elicitation or scope or secret"`
- `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_auth.py tests/test_tools.py tests/test_cli.py tests/test_phase4_e2e.py`
- `uv run ruff format --check src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_auth.py tests/test_tools.py tests/test_cli.py tests/test_phase4_e2e.py`
- `uv run mypy src/pmcp --exclude baml_client`

Release-bound broader checks:

- `uv run pytest -q`
- `uv build`

## Acceptance Criteria

- [x] Each `AuthState` has documented machine-readable semantics and exactly one primary next action in gateway health diagnostics.
- [x] Gateway outputs include structured `missing_env_vars` where missing auth is caused by env/header placeholders, without exposing values.
- [x] Audit events distinguish `missing_credential`, `credential_stored`, `remote_auth_challenge`, `insufficient_scope`, `url_elicitation_required`, and `url_elicitation_acknowledged`.
- [x] `gateway.health`, `pmcp status --verbose`, `pmcp doctor`, and feedback payload previews expose consistent non-secret auth evidence.
- [x] Tests assert bearer tokens, API keys, auth codes, JWT-looking strings, URL userinfo, and secret query values do not appear in auth-related outputs.
- [x] Existing API-key credential storage, remote-header missing-auth detection, URL-mode elicitation acknowledgement, and SAFEURL sanitization behavior remain backward compatible.
- [x] AUTHOBS does not add durable audit storage, per-user authorization isolation, OAuth token exchange, or live third-party provider requirements.

## Closeout Notes

- Completed SL-0 through SL-4 in this worktree.
- Added `AuthStateSemanticsInfo`, `DEFAULT_AUTH_STATE_SEMANTICS`, `AuthEventKind`, `GatewayAuditEvent.auth_event`, and `InvokeOutput.missing_env_vars` as additive public model fields.
- Gateway auth boundaries now categorize missing credentials, stored credentials, remote challenges, insufficient scopes, URL elicitation requirements, URL elicitation acknowledgement, and policy denial in audit events.
- `pmcp status --verbose`, `pmcp doctor`, and `gateway.submit_feedback` previews render structured, sanitized auth evidence rather than parsing auth prose.
- No README, SECURITY, or CHANGELOG update was required for this phase; release documentation remains Phase 6.

Verification completed:

- `uv run pytest tests/test_auth.py -k "auth_state or auth_event or semantics or missing_env"` - passed.
- `uv run pytest tests/test_tools.py -k "auth_event or auth_state or missing_env or audit or feedback or elicitation or scope or credential"` - passed.
- `uv run pytest tests/test_cli.py -k "status or doctor or auth_state or auth_event or missing_env or secret"` - passed.
- `uv run pytest tests/test_phase4_e2e.py -k "auth or status or doctor or feedback or secret"` - passed.
- `uv run pytest tests/test_auth.py tests/test_tools.py tests/test_cli.py tests/test_phase4_e2e.py -k "auth_state or auth_event or authobs or missing_env or audit or feedback or status or doctor or elicitation or scope or secret"` - passed.
- `uv run ruff check src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_auth.py tests/test_tools.py tests/test_cli.py tests/test_phase4_e2e.py` - passed.
- `uv run ruff format --check src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_auth.py tests/test_tools.py tests/test_cli.py tests/test_phase4_e2e.py` - passed after formatting `src/pmcp/cli.py`.
- `uv run mypy src/pmcp --exclude baml_client` - passed.
- `uv run pytest -q` - passed (`1731 passed, 12 skipped, 21 deselected`).
- `uv build` - passed.
