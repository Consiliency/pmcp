# AUTH: Authorization and Elicitation Modernization

## Context

Phase 3 of `specs/phase-plans-v3.md` aligns PMCP's auth and credential UX with MCP `2025-11-25` authorization and URL-mode elicitation while preserving existing local secret workflows. Phase 1 already records negotiated protocol versions and preserves modern metadata, and Phase 2 already keeps task and pending semantics coherent. AUTH should build on those contracts without weakening older server compatibility.

Current PMCP auth behavior is mostly local API-key oriented. `gateway.provision` reports `auth_required`, `auth_mode="api_key"`, `auth_methods`, and `alternative_env_vars` for manifest servers with missing env vars. `gateway.auth_connect` stores a credential in PMCP env storage and returns a `gateway.provision(...)` next step. `gateway.health`, live `pmcp status`, `pmcp doctor`, `/health`, `/metrics`, and CLI auth commands expose some missing-auth and redaction behavior, but they do not yet model MCP authorization discovery, insufficient scope, policy denial, or URL-mode elicitation as first-class states.

The MCP `2025-11-25` authorization spec requires clients to use OAuth 2.0 Protected Resource Metadata for authorization server discovery, supports Authorization Server Metadata and OpenID Connect Discovery, and recommends Client ID Metadata Documents where supported. The MCP `2025-11-25` elicitation spec adds URL mode for out-of-band sensitive flows and defines `URLElicitationRequiredError` code `-32042` with URL-mode elicitation entries. PMCP should surface those as structured next steps and never log, persist, or print bearer tokens, API keys, auth codes, URL userinfo, or third-party secrets.

## Interface Freeze Gates

- [x] IF-0-AUTH-1 — PMCP represents downstream auth state with a closed public state vocabulary: `none`, `missing_auth`, `insufficient_scope`, `elicitation_required`, `policy_denied`, and `unknown`, plus optional non-secret `next_step`, `auth_methods`, scope names, metadata URLs, and URL-mode elicitation summaries.
- [x] IF-0-AUTH-2 — Manifest and configured-server auth metadata remain backward compatible with existing `requires_api_key`, `env_var`, and `env_instructions`, while additively supporting remote authorization discovery hints: protected resource metadata URL, authorization server metadata URL, OIDC issuer/discovery URL, client ID metadata document URL, declared scopes, and URL-mode elicitation support.
- [x] IF-0-AUTH-3 — `gateway.provision`, `gateway.connect_server`, `gateway.health`, `gateway.auth_connect`, and `gateway.invoke` report missing auth, insufficient scope, URL-mode elicitation required, and policy denied as distinct structured states without collapsing them into opaque connection failures.
- [x] IF-0-AUTH-4 — `gateway.auth_connect` keeps the existing env-store credential path for API-key flows and additively supports URL-mode elicitation by accepting an elicitation identifier/URL consent acknowledgement without accepting third-party credentials through the gateway tool.
- [x] IF-0-AUTH-5 — Remote HTTP auth discovery follows MCP authorization expectations: protected resource metadata can be discovered from `WWW-Authenticate: ... resource_metadata=...` or well-known metadata URLs, authorization server metadata can be resolved through OAuth metadata or OIDC discovery, and discovery failures are reported as non-secret diagnostics.
- [x] IF-0-AUTH-6 — PMCP redacts secrets and auth-bearing URLs in logs, gateway outputs, status, doctor output, feedback diagnostics, and HTTP diagnostics; `/health` and `/metrics` remain unauthenticated unless a separate security decision changes that contract.
- [x] IF-0-AUTH-7 — Tests cover missing auth, insufficient/expired scope, policy denied, URL-mode elicitation, local env-store fallback, auth discovery metadata, refusal paths, redaction, and unchanged legacy API-key behavior.

## Lane Index & Dependencies

- SL-0 — Auth state and metadata contracts; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4, SL-5; Parallel-safe: no
- SL-1 — Auth discovery and safe redaction helpers; Depends on: SL-0; Blocks: SL-2, SL-3, SL-4, SL-5; Parallel-safe: no
- SL-2 — Gateway auth and elicitation surfaces; Depends on: SL-0, SL-1; Blocks: SL-3, SL-4, SL-5; Parallel-safe: no
- SL-3 — CLI status, doctor, and auth command UX; Depends on: SL-0, SL-1, SL-2; Blocks: SL-5; Parallel-safe: no
- SL-4 — HTTP transport authorization metadata behavior; Depends on: SL-0, SL-1, SL-2; Blocks: SL-5; Parallel-safe: yes
- SL-5 — End-to-end, docs, and roadmap closeout; Depends on: SL-0, SL-1, SL-2, SL-3, SL-4; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Auth State and Metadata Contracts

- **Scope**: Define the shared auth-state, discovery, and elicitation models that every later lane consumes without changing existing required response fields.
- **Owned files**: `src/pmcp/types.py`, `src/pmcp/manifest/loader.py`, `src/pmcp/config/loader.py`, `tests/test_manifest.py`, `tests/test_config_loader.py`
- **Interfaces provided**: `AuthState`, `AuthMetadataInfo`, `AuthChallengeInfo`, `UrlElicitationInfo`, `AuthConnectInput` URL-mode fields, additive auth fields on `ProvisionOutput`, `LifecycleServerOutput`, `ServerHealthInfo`, `InvokeOutput`, and manifest/config auth metadata fields
- **Interfaces consumed**: existing `requires_api_key`, `env_var`, `env_instructions`, `auth_required`, `auth_mode`, `auth_methods`, `alternative_env_vars`, `StartupSkipReason.MISSING_AUTH`, `StartupSkipReason.POLICY_DENIED`, Phase 1 `protocol_version`, and Phase 2 `InvokeOutput`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add model tests proving legacy `ProvisionOutput`, `ServerHealthInfo`, and `AuthConnectInput` payloads still validate without new auth fields.
  - test: Add manifest loader tests proving old API-key server entries still parse and new optional auth metadata fields round-trip without becoming required.
  - test: Add config loader tests proving remote `.mcp.json` entries can carry optional auth discovery hints while unknown extra fields remain ignored where that is the existing contract.
  - impl: Add a closed `AuthState` literal or enum with `none`, `missing_auth`, `insufficient_scope`, `elicitation_required`, `policy_denied`, and `unknown`.
  - impl: Add compact metadata models for protected resource metadata URL, authorization server metadata URL, OIDC issuer/discovery URL, Client ID Metadata Document URL, declared scopes, granted scopes, missing scopes, and non-secret discovery diagnostics.
  - impl: Add `UrlElicitationInfo` fields for `elicitation_id`, redacted `url`, `message`, and `next_step`, explicitly excluding credential, token, code, and userinfo fields.
  - impl: Extend gateway output models with defaulted or optional auth fields only, so old clients continue to parse responses.
  - impl: Extend `ServerConfig` and config typing with optional auth discovery metadata while preserving the current env-var API-key fields.
  - verify: `uv run pytest tests/test_manifest.py tests/test_config_loader.py -k "auth or api_key or remote or metadata"`
  - verify: `uv run ruff check src/pmcp/types.py src/pmcp/manifest/loader.py src/pmcp/config/loader.py tests/test_manifest.py tests/test_config_loader.py`

### SL-1 — Auth Discovery and Safe Redaction Helpers

- **Scope**: Centralize non-secret auth discovery and redaction behavior so gateway, CLI, and transport code do not each invent their own parsing rules.
- **Owned files**: `src/pmcp/auth.py`, `tests/test_auth.py`, `src/pmcp/policy/policy.py`, `tests/test_policy.py`
- **Interfaces provided**: auth discovery helpers, `WWW-Authenticate` challenge parser for `resource_metadata`, well-known protected-resource URL builder, OAuth/OIDC metadata fetch result shape, URL-mode elicitation error parser, auth URL redaction helper, auth diagnostic sanitizer
- **Interfaces consumed**: SL-0 auth metadata models, existing `PolicyManager.redact_secrets(...)`, existing default redaction patterns, standard `httpx` or stdlib HTTP client already available in the project
- **Parallel-safe**: no
- **Tasks**:
  - test: Add parser tests for `WWW-Authenticate` challenges containing `resource_metadata`, missing/invalid challenge parameters, and multiple auth schemes.
  - test: Add discovery URL tests for root and path-scoped `/.well-known/oauth-protected-resource` locations derived from remote MCP endpoint URLs.
  - test: Add metadata normalization tests for OAuth Authorization Server Metadata, OIDC Discovery metadata, Client ID Metadata Document support, scopes, and non-secret discovery errors.
  - test: Add URL-mode elicitation error parser tests for JSON-RPC code `-32042`, multiple elicitations, invalid URLs, and missing `elicitationId`.
  - test: Extend redaction tests to cover bearer tokens, API keys, auth codes, URL query secrets, URL userinfo, `Authorization` header values, and elicitation URLs containing sensitive query parameters.
  - impl: Create a small auth helper module rather than adding more unrelated parsing code to `GatewayTools` or `cli.py`.
  - impl: Implement conservative fetch helpers with explicit timeouts, no credential forwarding, and sanitized error strings; return diagnostics instead of raising raw transport exceptions to gateway outputs.
  - impl: Treat all discovered metadata as untrusted input and preserve only non-secret fields needed for next-step reporting.
  - impl: Reuse or extend `PolicyManager.redact_secrets(...)` so gateway and CLI redaction share one behavior for strings and structured diagnostics.
  - verify: `uv run pytest tests/test_auth.py tests/test_policy.py -k "auth or redacts or redact or elicitation or metadata"`
  - verify: `uv run ruff check src/pmcp/auth.py src/pmcp/policy/policy.py tests/test_auth.py tests/test_policy.py`

### SL-2 — Gateway Auth and Elicitation Surfaces

- **Scope**: Teach gateway tools to expose structured auth states, URL-mode elicitation next steps, and non-secret diagnostics while preserving existing API-key credential storage semantics.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`, `tests/test_manifest_provision.py`
- **Interfaces provided**: structured auth outputs from `gateway.provision`, `gateway.connect_server`, `gateway.health`, `gateway.auth_connect`, and `gateway.invoke`; URL-mode `gateway.auth_connect` flow; sanitized gateway error strings; missing-auth/insufficient-scope/policy-denied health states
- **Interfaces consumed**: SL-0 models, SL-1 discovery/redaction helpers, existing `GatewayTools._auth_env_options(...)`, `GatewayTools._auth_methods_for_server(...)`, `GatewayTools._write_secret(...)`, manifest API-key metadata, startup observations, policy manager, and Phase 2 invocation/task outputs
- **Parallel-safe**: no
- **Tasks**:
  - test: Add `gateway.provision` tests proving legacy manifest API-key failures still return `needs_api_key=True`, `auth_required=True`, `auth_mode="api_key"`, and env-var hints.
  - test: Add `gateway.provision` and `gateway.connect_server` tests proving policy-denied servers return `auth_state="policy_denied"` separately from missing credentials.
  - test: Add remote server tests proving `401` with protected-resource metadata is surfaced as `auth_state="missing_auth"` with sanitized metadata URLs and next steps instead of an opaque connection error.
  - test: Add insufficient-scope tests proving `WWW-Authenticate` scope challenges or downstream auth errors become `auth_state="insufficient_scope"` with missing scope names and no token values.
  - test: Add `gateway.invoke` tests proving JSON-RPC `URLElicitationRequiredError` returns `auth_state="elicitation_required"` with URL-mode elicitation summaries and does not treat the tool call as successful output.
  - test: Add `gateway.auth_connect` tests for existing credential storage, explicit `env_var`, URL-mode elicitation acknowledgement, refusal to accept credential material for URL-mode flows, and unknown-server failure.
  - test: Add `gateway.health` tests proving missing auth, insufficient scope, policy denied, and elicitation required are distinguishable in server rows while old health fields remain unchanged.
  - test: Add regression tests proving gateway messages, errors, feedback events, and update warnings redact credentials, authorization codes, URL userinfo, and auth-bearing query parameters.
  - impl: Extend gateway tool definitions for `gateway.auth_connect` with URL-mode fields from SL-0 while keeping `credential` valid for API-key mode.
  - impl: Route local env-store API-key flows through the existing `_write_secret(...)` path and return the same `gateway.provision(...)` style next step.
  - impl: For URL-mode elicitation, return a consent/next-step response that points the user out of band and never asks the caller to paste third-party credentials into PMCP.
  - impl: Update provision/connect/invoke error handling to call SL-1 auth parsers before falling back to generic sanitized errors.
  - impl: Update health rows from startup observations and recent connection/auth errors so missing auth, insufficient scope, policy denied, and elicitation required remain machine-readable.
  - impl: Harden `_sanitize_error(...)` or route it through SL-1 sanitization so absolute paths, URL userinfo, tokens, and auth query parameters are not printed.
  - verify: `uv run pytest tests/test_tools.py tests/test_manifest_provision.py -k "auth or credential or scope or elicitation or policy_denied or missing_api_key or redacts"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py tests/test_manifest_provision.py`

### SL-3 — CLI Status, Doctor, and Auth Command UX

- **Scope**: Make CLI status, doctor, and auth commands display the new structured auth states and next steps without leaking secrets or introducing interactive gateway-tool prompts.
- **Owned files**: `src/pmcp/cli.py`, `src/pmcp/cli_commands/doctor.py`, `src/pmcp/cli_commands/secrets.py`, `tests/test_cli.py`, `tests/test_secrets_command.py`
- **Interfaces provided**: live `pmcp status` auth-state rendering, `pmcp doctor` auth diagnostics, `pmcp auth connect` URL-mode handling, redacted URL/secret display helpers, secrets-check coverage for auth metadata
- **Interfaces consumed**: SL-0 auth fields on health/provision/auth-connect outputs, SL-1 redaction helpers, SL-2 gateway output contract, existing `pmcp secrets` env-store behavior, existing `/health` probing behavior
- **Parallel-safe**: no
- **Tasks**:
  - test: Add live status tests proving JSON output passes through auth fields unchanged and text output distinguishes `missing_auth`, `insufficient_scope`, `policy_denied`, and `elicitation_required`.
  - test: Add doctor tests proving remote auth metadata diagnostics report missing env vars, unresolved authorization metadata, insufficient-scope hints, and URL-mode next steps without printing secret values.
  - test: Add doctor tests proving `/health` reachability remains unauthenticated and URL credentials in `PMCP_GATEWAY_URL` are redacted.
  - test: Add auth command tests proving existing API-key prompt/storage behavior still works, `--credential` is not echoed, and `--json` does not include secret values.
  - test: Add auth command tests proving URL-mode elicitation prints a sanitized URL and elicitation ID next step while refusing to accept a third-party secret value for that mode.
  - test: Add secrets command tests proving `secrets check` can report configured auth metadata and missing env vars without dumping stored secret values.
  - impl: Replace local CLI-only URL redaction with the SL-1 helper or delegate to it, keeping `_redact_url_credentials(...)` as a compatibility wrapper if existing tests import it.
  - impl: Update status rendering to show auth state and non-secret next steps in text output while preserving current JSON structure plus additive fields.
  - impl: Update doctor remote diagnostics to understand configured auth metadata and protected-resource/OIDC discovery diagnostics where available.
  - impl: Update `run_auth_connect(...)` to branch between API-key credential storage and URL-mode elicitation acknowledgement based on gateway responses and explicit CLI flags.
  - impl: Ensure CLI logging and exceptions route through sanitized diagnostics before printing.
  - verify: `uv run pytest tests/test_cli.py tests/test_secrets_command.py -k "auth or doctor or status or secret or redact or scope or elicitation"`
  - verify: `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/cli_commands/secrets.py tests/test_cli.py tests/test_secrets_command.py`

### SL-4 — HTTP Transport Authorization Metadata Behavior

- **Scope**: Keep PMCP's own HTTP transport contract explicit while adding safe support for MCP authorization metadata responses and diagnostics where PMCP acts as a protected resource.
- **Owned files**: `src/pmcp/transport/http.py`, `tests/test_transport_http.py`, `tests/test_http_transport.py`
- **Interfaces provided**: sanitized `401` behavior, optional protected-resource metadata endpoint/headers when PMCP bearer-token auth is configured, unchanged unauthenticated `/health` and `/metrics`, transport-level auth diagnostics
- **Interfaces consumed**: SL-0 auth metadata settings, SL-1 auth URL/redaction helpers, existing `auth_token` guard, existing rate limiting/body-size behavior, existing rmcp compatibility branches
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add HTTP tests proving `/health` and `/metrics` remain unauthenticated with `auth_token` configured.
  - test: Add HTTP tests proving `/mcp` still requires bearer auth when `auth_token` is configured and the response does not include the configured token.
  - test: Add HTTP tests for optional `WWW-Authenticate` protected-resource metadata headers when metadata is configured, including sanitized URL output.
  - test: Add HTTP tests for well-known protected-resource metadata response shape if PMCP exposes one for its own HTTP endpoint.
  - test: Add regression tests proving size-limit, rate-limit, timeout, and rmcp compatibility behavior remain ordered as expected around auth checks.
  - impl: Keep existing bearer-token comparison timing-safe and avoid logging full authorization headers.
  - impl: If PMCP exposes protected-resource metadata for its own gateway endpoint, keep it additive and non-secret, with configured issuer/authorization server locations only.
  - impl: Do not require auth on `/health` or `/metrics`; document that firewall or reverse proxy controls remain the recommended protection.
  - verify: `uv run pytest tests/test_transport_http.py tests/test_http_transport.py -k "auth or metadata or health or metrics or 401 or protected"`
  - verify: `uv run ruff check src/pmcp/transport/http.py tests/test_transport_http.py tests/test_http_transport.py`

### SL-5 — End-to-End, Docs, and Roadmap Closeout

- **Scope**: Validate cross-surface auth behavior, document supported flows and limitations, and close the AUTH checklist only after every producer lane has finished.
- **Owned files**: `tests/test_phase4_e2e.py`, `README.md`, `SECURITY.md`, `CHANGELOG.md`, `specs/phase-plans-v3.md`, `plans/phase-plan-v3-auth.md`
- **Interfaces provided**: end-to-end auth smoke tests, user-facing auth flow documentation, security guidance, completed AUTH acceptance checklist
- **Interfaces consumed**: SL-0 auth contracts, SL-1 redaction and discovery helpers, SL-2 gateway auth states and URL-mode surfaces, SL-3 CLI rendering and doctor behavior, SL-4 HTTP transport metadata behavior, verification results from all lanes
- **Parallel-safe**: no
- **Tasks**:
  - test: Add end-to-end smoke proving a missing API-key server reports missing auth through provision, auth connect stores the credential through env-store fallback, and provisioning can be retried without leaking the credential.
  - test: Add end-to-end smoke proving a remote auth challenge with protected resource metadata appears as structured auth state in gateway output and live status.
  - test: Add end-to-end smoke proving URL-mode elicitation returns a redacted out-of-band next step and does not accept or print third-party credentials.
  - test: Add end-to-end smoke proving policy-denied and insufficient-scope paths are distinct from missing auth in status/doctor outputs.
  - impl: Document PMCP's supported auth modes: local env-store API keys, remote authorization discovery diagnostics, and URL-mode elicitation next steps.
  - impl: Document non-goals and limits: PMCP is not an authorization server, does not implement enterprise SSO/Cross-App Access/DPoP/WIF, and does not store third-party refresh tokens without a later encrypted storage contract.
  - impl: Update SECURITY.md with redaction guarantees, `/health` and `/metrics` unauthenticated contract, and guidance against pasting OAuth codes or third-party credentials into gateway tools.
  - impl: Add a CHANGELOG entry for structured auth states and URL-mode elicitation if this branch is release-bound.
  - impl: Mark Phase 3 exit criteria complete in `specs/phase-plans-v3.md` only after implementation and verification complete.
  - impl: Mark this plan's interface gates and acceptance criteria complete and record execution deviations.
  - verify: `uv run pytest tests/test_phase4_e2e.py -k "auth or credential or elicitation or scope or policy"`
  - verify: Manually review markdown formatting in `README.md`, `SECURITY.md`, `CHANGELOG.md`, `specs/phase-plans-v3.md`, and `plans/phase-plan-v3-auth.md`.

## Verification

Lane-specific verification:

- `uv run pytest tests/test_manifest.py tests/test_config_loader.py -k "auth or api_key or remote or metadata"`
- `uv run ruff check src/pmcp/types.py src/pmcp/manifest/loader.py src/pmcp/config/loader.py tests/test_manifest.py tests/test_config_loader.py`
- `uv run pytest tests/test_auth.py tests/test_policy.py -k "auth or redacts or redact or elicitation or metadata"`
- `uv run ruff check src/pmcp/auth.py src/pmcp/policy/policy.py tests/test_auth.py tests/test_policy.py`
- `uv run pytest tests/test_tools.py tests/test_manifest_provision.py -k "auth or credential or scope or elicitation or policy_denied or missing_api_key or redacts"`
- `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py tests/test_manifest_provision.py`
- `uv run pytest tests/test_cli.py tests/test_secrets_command.py -k "auth or doctor or status or secret or redact or scope or elicitation"`
- `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/cli_commands/secrets.py tests/test_cli.py tests/test_secrets_command.py`
- `uv run pytest tests/test_transport_http.py tests/test_http_transport.py -k "auth or metadata or health or metrics or 401 or protected"`
- `uv run ruff check src/pmcp/transport/http.py tests/test_transport_http.py tests/test_http_transport.py`
- `uv run pytest tests/test_phase4_e2e.py -k "auth or credential or elicitation or scope or policy"`

Whole-phase regression:

- `uv run pytest tests/test_tools.py tests/test_manifest_provision.py tests/test_cli.py tests/test_secrets_command.py tests/test_transport_http.py tests/test_http_transport.py tests/test_phase4_e2e.py -q`
- `uv run pytest tests/test_client_manager.py tests/test_server.py tests/test_lazy_start.py tests/test_startup_resolver.py -q` because AUTH consumes protocol metadata, health/status, and startup observation contracts.
- `uv run ruff check src/ tests/`
- `uv run ruff format --check src/ tests/`
- `uv run mypy src/pmcp --exclude baml_client`
- `uv run pytest -q` before release handoff if time permits.

## Acceptance Criteria

- [x] PMCP recognizes and reports authorization metadata for OAuth Protected Resource Metadata, Authorization Server Metadata, OpenID Connect Discovery, and Client ID Metadata Documents where available.
- [x] Missing auth, insufficient scope, URL-mode elicitation required, and policy denied are distinct structured states in gateway outputs, health/status, and doctor output.
- [x] Existing API-key env-store flows through `gateway.auth_connect`, `pmcp auth connect`, and `pmcp secrets` remain backward compatible.
- [x] URL-mode elicitation produces an out-of-band next step with sanitized URL and `elicitation_id`, and PMCP refuses to accept third-party credentials through the gateway tool for that flow.
- [x] MCP auth discovery failures are reported as sanitized diagnostics rather than raw connection exceptions containing secrets or auth-bearing URLs.
- [x] `/health` and `/metrics` remain unauthenticated, and `/mcp` bearer-token behavior remains timing-safe and non-leaking.
- [x] Logs, gateway outputs, status, doctor output, feedback diagnostics, and release diagnostics redact bearer tokens, API keys, auth codes, URL userinfo, and sensitive query parameters.
- [x] Tests cover missing auth, expired/insufficient scope, URL-mode elicitation, local env-store fallback, policy-denied refusal paths, auth metadata discovery, and legacy API-key behavior.
- [x] README and SECURITY.md document supported auth modes, non-goals, URL-mode safety expectations, and the unauthenticated `/health` and `/metrics` contract.
