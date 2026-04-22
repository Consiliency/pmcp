# SAFEURL: Auth URL, Metadata, and Redaction Safety

## Context

Phase 4 of `specs/phase-plans-v4.md` depends on the REMOTE and ELICIT phases.
Current worktree state already has shared env-store, remote-header missing-auth,
and URL-mode elicitation contracts in place, including `pmcp.auth.redact_auth_url`,
`sanitize_url_elicitation_url`, `sanitize_auth_diagnostic`,
`parse_www_authenticate`, `normalize_auth_metadata`, and `fetch_json_metadata`.

The remaining SAFEURL gap is to make those helpers broad and authoritative
enough for production diagnostics. Redaction currently covers common bearer,
token, API-key, code, URL userinfo, and URL query patterns, but it does not yet
cover all roadmap query keys or standalone JWT-looking strings. Metadata URL
normalization mostly redacts rather than rejects invalid public auth URLs, and
`WWW-Authenticate` parsing is still regex-based enough to miss quoted edge
cases. Gateway errors, feedback telemetry, CLI/doctor output, HTTP auth
challenge headers, and policy output redaction should consume the same sanitizer
instead of maintaining separate redaction behavior.

`specs/phase-plans-v4.md` is currently tracked and clean. The generated plan
artifact is new and will be untracked until staged.

## Interface Freeze Gates

- [x] IF-0-SAFEURL-1 — `pmcp.auth.AUTH_SECRET_QUERY_KEYS` includes `session`, `sid`, `jwt`, `assertion`, `saml`, and `ticket`, and `redact_auth_url(url)` strips URL userinfo plus redacts every auth-bearing query value without preserving fragments.
- [x] IF-0-SAFEURL-2 — `pmcp.auth.sanitize_auth_diagnostic(value)` redacts bearer tokens, API keys, auth codes, JWT-looking strings, URL userinfo, and auth-bearing URL query values through one shared implementation used by gateway, CLI, policy, feedback, audit, status, doctor, and HTTP diagnostics.
- [x] IF-0-SAFEURL-3 — `pmcp.auth.sanitize_public_auth_url(url, *, allow_loopback_http=False)` rejects invalid, relative, non-HTTP(S), and non-HTTPS non-loopback URLs, returning a redacted absolute URL for accepted public metadata or elicitation URLs.
- [x] IF-0-SAFEURL-4 — `pmcp.auth.normalize_auth_metadata(...)` and `fetch_json_metadata(...)` expose only public metadata fields, validate all metadata URLs through IF-0-SAFEURL-3, and never forward caller credentials, cookies, or caller auth headers when fetching metadata.
- [x] IF-0-SAFEURL-5 — `pmcp.auth.parse_www_authenticate(header)` parses quoted challenge values, `scope`, `missing_scope`, `error="insufficient_scope"`, `resource_metadata`, and sanitized `error_description` without leaking query secrets or invalid metadata URLs.
- [x] IF-0-SAFEURL-6 — Gateway errors, feedback previews, audit events, CLI status/doctor text, HTTP challenge/metadata diagnostics, and policy `redact_secrets` output all route auth-bearing text through IF-0-SAFEURL-2.

## Lane Index & Dependencies

- SL-0 — Shared auth safety contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 — Policy redaction integration; Depends on: SL-0; Blocks: SL-5; Parallel-safe: yes
- SL-2 — Gateway diagnostics integration; Depends on: SL-0; Blocks: SL-5; Parallel-safe: yes
- SL-3 — HTTP metadata and challenge safety; Depends on: SL-0; Blocks: SL-5; Parallel-safe: yes
- SL-4 — CLI and doctor diagnostics integration; Depends on: SL-0; Blocks: SL-5; Parallel-safe: yes
- SL-5 — Phase verification and closeout; Depends on: SL-0, SL-1, SL-2, SL-3, SL-4; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Shared Auth Safety Contract

- **Scope**: Harden the shared auth URL validation, redaction, metadata normalization, metadata fetching, and `WWW-Authenticate` parsing helpers.
- **Owned files**: `src/pmcp/auth.py`, `tests/test_auth.py`
- **Interfaces provided**: `AUTH_SECRET_QUERY_KEYS`, `redact_auth_url(url)`, `sanitize_auth_diagnostic(value)`, `sanitize_public_auth_url(url, allow_loopback_http=False)`, `sanitize_url_elicitation_url(url)`, `normalize_auth_metadata(...)`, `fetch_json_metadata(url, timeout=...)`, `parse_www_authenticate(header)`
- **Interfaces consumed**: existing `AuthMetadataInfo`, `AuthChallengeInfo`, `UrlElicitationInfo`, existing URL-mode elicitation contract from IF-0-ELICIT-3, existing REMOTE missing-auth contract from IF-0-REMOTE-2
- **Parallel-safe**: no
- **Tasks**:
  - test: Add redaction tests for `session`, `sid`, `jwt`, `assertion`, `saml`, and `ticket` query keys, URL userinfo, URL fragments, bearer tokens, API-key assignments, auth-code assignments, and standalone JWT-looking strings.
  - test: Add URL validation tests proving public metadata URLs accept `https://` and loopback `http://` only when allowed, while relative URLs, malformed URLs, `ftp://`, and non-loopback `http://` are rejected.
  - test: Add metadata normalization tests proving invalid metadata URLs are omitted or reported only through sanitized diagnostics, while public scopes, issuer, authorization-server URLs, and client metadata document URLs remain intact when valid.
  - test: Add metadata fetch tests using a fake opener or monkeypatched `urlopen` proving requests send only safe PMCP-owned headers such as `Accept: application/json` and do not forward `Authorization`, `Cookie`, or caller-supplied auth headers.
  - test: Add `WWW-Authenticate` tests for quoted commas, escaped quotes if supported by the parser, missing `scope`, `missing_scope`, `error="insufficient_scope"`, invalid `resource_metadata`, and secret-bearing `error_description`.
  - impl: Extend `AUTH_SECRET_QUERY_KEYS` with the roadmap-required query keys and any local aliases already implied by tests.
  - impl: Add a reusable public auth URL validation helper and route `sanitize_url_elicitation_url(...)`, `normalize_auth_metadata(...)`, `parse_www_authenticate(...)`, and `fetch_json_metadata(...)` through it.
  - impl: Replace ad hoc diagnostic substitutions with a single helper path that redacts URL values first, then headers/assignments, then JWT-looking strings, truncating at the existing diagnostic limit.
  - impl: Replace the regex-only `WWW-Authenticate` parameter parser with a small quoted-string-aware parser or standard-library-compatible parsing helper.
  - verify: `uv run pytest tests/test_auth.py -k "redact or metadata or www_authenticate or elicitation or url or token or jwt"`
  - verify: `uv run ruff check src/pmcp/auth.py tests/test_auth.py`

### SL-1 — Policy Redaction Integration

- **Scope**: Make policy-managed output redaction consume the shared auth sanitizer while preserving configured policy regex behavior.
- **Owned files**: `src/pmcp/policy/policy.py`, `tests/test_policy.py`
- **Interfaces provided**: `PolicyManager.redact_secrets(...)` coverage for shared auth sanitizer output plus configured policy patterns
- **Interfaces consumed**: SL-0 `sanitize_auth_diagnostic(value)`, existing `DEFAULT_REDACTION_PATTERNS`, existing `GatewayPolicy.redaction.patterns`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add policy redaction tests for JWT-looking strings, roadmap query keys, URL userinfo, bearer tokens, and configured custom policy regexes in the same output payload.
  - test: Add regression coverage proving policy redaction does not reintroduce values that SL-0 already replaced with `[REDACTED]`.
  - impl: Keep `PolicyManager.redact_secrets(...)` calling the shared auth sanitizer before configured regexes.
  - impl: Adjust default policy patterns only if SL-0 coverage shows a pattern still leaks after shared sanitization.
  - verify: `uv run pytest tests/test_policy.py -k "redact or secret or token or jwt"`
  - verify: `uv run ruff check src/pmcp/policy/policy.py tests/test_policy.py`

### SL-2 — Gateway Diagnostics Integration

- **Scope**: Route gateway error strings, auth challenges, feedback telemetry, and audit event errors through the shared SAFEURL sanitizer.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: sanitized gateway errors, `auth_challenge`, `feedback_hint` context, feedback issue previews, and `GatewayAuditEvent.error` values across invoke/provision/connect/status paths
- **Interfaces consumed**: SL-0 `sanitize_auth_diagnostic(value)`, SL-0 `parse_www_authenticate(header)`, SL-0 `normalize_auth_metadata(...)`, existing `_sanitize_error(...)`, existing `_auth_challenge_from_message(...)`, existing `_scrub_sensitive_text(...)`, existing `GatewayAuditEvent`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add gateway invoke/connect/provision failure tests with `WWW-Authenticate` headers containing quoted values, `resource_metadata` query secrets, `error_description` secrets, JWT-looking text, and auth-bearing URLs.
  - test: Add health/audit tests proving `GatewayAuditEvent.error`, `ServerHealthInfo.error`, `auth_challenge.error_description`, and gateway error output omit token/code/JWT/query secret samples.
  - test: Add feedback preview tests proving `_scrub_sensitive_text(...)` or its replacement redacts the same auth samples as SL-0 before rendering issue bodies and recent event JSON.
  - impl: Keep `_sanitize_error(...)` as the single gateway boundary wrapper and ensure every gateway exception/error path uses it before returning output, recording feedback events, or appending audit events.
  - impl: Replace `_scrub_sensitive_text(...)` internals with SL-0 sanitizer plus any existing email/provider-token redaction that is not auth-specific.
  - impl: Ensure `_auth_challenge_from_message(...)` returns sanitized challenge metadata and never includes invalid public metadata URLs.
  - verify: `uv run pytest tests/test_tools.py -k "redact or metadata or www_authenticate or auth_challenge or audit or feedback or token or code"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-3 — HTTP Metadata and Challenge Safety

- **Scope**: Constrain the HTTP transport's protected-resource metadata route and `WWW-Authenticate` header generation to validated public auth metadata.
- **Owned files**: `src/pmcp/transport/http.py`, `tests/test_transport_http.py`
- **Interfaces provided**: validated `WWW-Authenticate` `resource_metadata` header, public protected-resource metadata endpoint payload, and transport diagnostics that do not expose configured secrets
- **Interfaces consumed**: SL-0 `normalize_auth_metadata(...)`, SL-0 `sanitize_public_auth_url(...)`, existing `create_http_app(...)`, existing unauthenticated `/health` and `/metrics` behavior
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add transport tests proving configured metadata URLs with auth-bearing query keys are redacted in `WWW-Authenticate` headers and metadata JSON.
  - test: Add transport tests proving invalid, relative, non-HTTP(S), and non-loopback `http://` metadata URLs do not create a metadata route or auth challenge header.
  - test: Add transport tests proving `/health` and metadata diagnostics do not include `auth_token`, caller `Authorization`, cookies, or query secret values.
  - impl: Use only normalized/validated `AuthMetadataInfo` values when building `_auth_headers()` and the protected-resource metadata route.
  - impl: Avoid creating a metadata route when the configured metadata URL is rejected by SL-0 validation.
  - impl: Preserve existing unauthenticated `/health` and `/metrics` semantics.
  - verify: `uv run pytest tests/test_transport_http.py -k "auth or metadata or www_authenticate or token or redact"`
  - verify: `uv run ruff check src/pmcp/transport/http.py tests/test_transport_http.py`

### SL-4 — CLI and Doctor Diagnostics Integration

- **Scope**: Make CLI status, doctor probes, and auth command display use shared auth URL/redaction semantics without changing command names.
- **Owned files**: `src/pmcp/cli.py`, `src/pmcp/cli_commands/doctor.py`, `tests/test_cli.py`
- **Interfaces provided**: `_redact_url_credentials(url)` as a wrapper over the shared sanitizer, sanitized doctor remote URL and metadata diagnostics, sanitized `pmcp status` live/local output, sanitized auth connect/acknowledge display
- **Interfaces consumed**: SL-0 `redact_auth_url(url)`, SL-0 `sanitize_auth_diagnostic(value)`, SL-0 `sanitize_public_auth_url(url, allow_loopback_http=False)`, existing `collect_remote_header_diagnostics(...)`, existing `run_status(...)`, existing `run_doctor(...)`, existing URL elicitation CLI flow from IF-0-ELICIT-3
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add CLI status tests proving live gateway errors and local fallback diagnostics redact URL userinfo, roadmap query keys, JWT-looking strings, bearer tokens, and auth-code values.
  - test: Add doctor tests proving invalid metadata URLs are warned with sanitized values and non-loopback `http://` metadata URLs are rejected or warned consistently with SL-0 validation.
  - test: Add auth connect/acknowledge display tests proving URL elicitation URLs with `session`, `sid`, `jwt`, `assertion`, `saml`, and `ticket` values are redacted.
  - impl: Route `_redact_url_credentials(...)`, probe exception display, doctor remote URL diagnostics, and status error rendering through SL-0 sanitizer helpers.
  - impl: Replace doctor metadata URL absolute-only checks with SL-0 public auth URL validation, while keeping existing remote server URL checks intact unless they are auth metadata URLs.
  - impl: Preserve existing text and JSON output shapes except for safer redacted values and diagnostics.
  - verify: `uv run pytest tests/test_cli.py -k "redact or doctor or status or auth_connect or auth_acknowledge or metadata or token"`
  - verify: `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_cli.py`

### SL-5 — Phase Verification and Closeout

- **Scope**: Verify SAFEURL end to end and record the final phase state without expanding into Phase 5 observability semantics or Phase 6 release documentation.
- **Owned files**: `plans/phase-plan-v4-safeurl.md`
- **Interfaces provided**: completed SAFEURL acceptance checklist, verification results, and documentation-impact decision
- **Interfaces consumed**: SL-0 shared auth safety contract, SL-1 policy behavior, SL-2 gateway diagnostics, SL-3 HTTP behavior, SL-4 CLI/doctor behavior, Phase 4 exit criteria from `specs/phase-plans-v4.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Review lane verification results and confirm each Phase 4 exit criterion has named coverage.
  - impl: Mark this plan's interface gates and acceptance criteria complete only after implementation and verification pass.
  - impl: Record intentional deviations, especially if `sanitize_public_auth_url(...)` is named differently or invalid metadata URLs are omitted with diagnostics instead of raising at every callsite.
  - impl: Record that no README, SECURITY, or CHANGELOG update is required in this phase unless user-visible CLI text changes beyond redaction and validation diagnostics; roadmap-level docs remain Phase 6.
  - verify: `uv run pytest tests/test_transport_http.py tests/test_policy.py tests/test_cli.py tests/test_tools.py tests/test_auth.py -k "redact or metadata or www_authenticate or token or code or jwt"`
  - verify: `uv run ruff check src/pmcp/auth.py src/pmcp/policy/policy.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/transport/http.py tests/test_auth.py tests/test_policy.py tests/test_tools.py tests/test_cli.py tests/test_transport_http.py`
  - verify: `uv run ruff format --check src/pmcp/auth.py src/pmcp/policy/policy.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/transport/http.py tests/test_auth.py tests/test_policy.py tests/test_tools.py tests/test_cli.py tests/test_transport_http.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_auth.py -k "redact or metadata or www_authenticate or elicitation or url or token or jwt"`
- `uv run ruff check src/pmcp/auth.py tests/test_auth.py`
- `uv run pytest tests/test_policy.py -k "redact or secret or token or jwt"`
- `uv run ruff check src/pmcp/policy/policy.py tests/test_policy.py`
- `uv run pytest tests/test_tools.py -k "redact or metadata or www_authenticate or auth_challenge or audit or feedback or token or code"`
- `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`
- `uv run pytest tests/test_transport_http.py -k "auth or metadata or www_authenticate or token or redact"`
- `uv run ruff check src/pmcp/transport/http.py tests/test_transport_http.py`
- `uv run pytest tests/test_cli.py -k "redact or doctor or status or auth_connect or auth_acknowledge or metadata or token"`
- `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py tests/test_cli.py`

Whole-phase regression:

- `uv run pytest tests/test_transport_http.py tests/test_policy.py tests/test_cli.py tests/test_tools.py tests/test_auth.py -k "redact or metadata or www_authenticate or token or code or jwt"`
- `uv run ruff check src/pmcp/auth.py src/pmcp/policy/policy.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/transport/http.py tests/test_auth.py tests/test_policy.py tests/test_tools.py tests/test_cli.py tests/test_transport_http.py`
- `uv run ruff format --check src/pmcp/auth.py src/pmcp/policy/policy.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/transport/http.py tests/test_auth.py tests/test_policy.py tests/test_tools.py tests/test_cli.py tests/test_transport_http.py`
- `uv run mypy src/pmcp --exclude baml_client`

Release-bound broader checks:

- `uv run pytest -q`
- `uv build`

## Acceptance Criteria

- [x] Redaction covers bearer tokens, API keys, auth codes, JWT-looking strings, URL userinfo, and common auth-bearing query keys including `session`, `sid`, `jwt`, `assertion`, `saml`, and `ticket`.
- [x] Public metadata fetches validate the target URL and do not forward credentials, cookies, or caller auth headers.
- [x] Metadata and elicitation URL validation rejects invalid, relative, non-HTTP(S), and non-HTTPS non-loopback URLs.
- [x] `WWW-Authenticate` parsing handles quoted values, missing scopes, insufficient scope, resource metadata URLs, and sanitized error descriptions.
- [x] Gateway errors, feedback hints/previews, audit events, status, doctor, and HTTP diagnostics share the same auth sanitizer.
- [x] Existing REMOTE missing-auth and ELICIT URL acknowledgement behavior remains backward compatible.
- [x] No live third-party providers or real third-party credentials are required for tests.

## Closeout Notes

- Completed SL-0 through SL-5 in topological order without parallel workers.
- Implemented the planned `sanitize_public_auth_url(...)` interface name as written.
- Invalid metadata URLs are omitted from normalized metadata and surfaced only through sanitized diagnostics; call sites that build HTTP auth headers or metadata routes use the normalized values.
- No README, SECURITY, or CHANGELOG update is required in this phase. User-visible text changes are limited to safer redacted values and validation diagnostics; roadmap-level documentation remains Phase 6.
- Verification passed:
  - `uv run pytest tests/test_auth.py -k "redact or metadata or www_authenticate or elicitation or url or token or jwt"`
  - `uv run pytest tests/test_policy.py -k "redact or secret or token or jwt"`
  - `uv run pytest tests/test_tools.py -k "redact or metadata or www_authenticate or auth_challenge or audit or feedback or token or code"`
  - `uv run pytest tests/test_transport_http.py -k "auth or metadata or www_authenticate or token or redact"`
  - `uv run pytest tests/test_cli.py -k "redact or doctor or status or auth_connect or auth_acknowledge or metadata or token"`
  - `uv run pytest tests/test_transport_http.py tests/test_policy.py tests/test_cli.py tests/test_tools.py tests/test_auth.py -k "redact or metadata or www_authenticate or token or code or jwt"`
  - `uv run ruff check src/pmcp/auth.py src/pmcp/policy/policy.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/transport/http.py tests/test_auth.py tests/test_policy.py tests/test_tools.py tests/test_cli.py tests/test_transport_http.py`
  - `uv run ruff format --check src/pmcp/auth.py src/pmcp/policy/policy.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/transport/http.py tests/test_auth.py tests/test_policy.py tests/test_tools.py tests/test_cli.py tests/test_transport_http.py`
  - `uv run mypy src/pmcp --exclude baml_client`
