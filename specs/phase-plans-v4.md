# Phase roadmap v4

## Context

PMCP 1.11.0 completed the v3 protocol-current roadmap: protocol negotiation,
modern MCP metadata preservation, task brokering, structured auth/elicitation
state reporting, observability, setup/config administration, and a conformance
release gate.

The follow-up production-readiness concern is narrower: make third-party
authentication safe and operable enough for production use. The v3 AUTH phase
correctly established PMCP's boundary as an auth-state reporter and local
credential broker, not an OAuth provider. The remaining gaps are hardening and
operator correctness: API-key secret writes should use one safe env-store path,
remote header credentials should fail early when missing, URL-mode elicitation
should require explicit post-consent acknowledgement, auth URLs and metadata
should be constrained more tightly, and tests should model real downstream auth
failure modes.

## Architecture North Star

PMCP should be a production-safe third-party MCP auth broker. It should store
opaque local API-key credentials safely, detect missing remote header/env
credentials before malformed connection attempts, preserve and report public
OAuth/OIDC discovery metadata, guide URL-based consent out of band, refuse
third-party OAuth codes and refresh tokens, and provide non-secret operational
evidence across gateway tools, CLI, health/status, doctor, audit events, and
tests.

PMCP should remain explicitly outside the role of authorization server,
enterprise identity broker, refresh-token store, DPoP/WIF implementation, or
cross-user authorization layer.

## Assumptions

- Existing v3 auth states and public fields remain backward compatible and
  additive.
- API-key credentials are opaque strings stored in PMCP env stores or supplied
  through process environment variables.
- Remote MCP servers may use `headers` with `${ENV_VAR}` placeholders for bearer
  tokens or tenant headers.
- URL-mode elicitation is an out-of-band consent/authorization flow owned by the
  downstream provider, not PMCP.
- Production HTTP exposure still requires external network controls and
  `PMCP_AUTH_TOKEN`; `/health` and `/metrics` remain unauthenticated by PMCP.
- Normal tests should use deterministic fakes and local HTTP/TestClient behavior,
  not live providers or real third-party credentials.

## Non-Goals

- Do not implement OAuth authorization-code exchange, token refresh, PKCE, DPoP,
  enterprise SSO, Cross-App Access, or workload identity federation.
- Do not store third-party OAuth refresh tokens or provider session cookies.
- Do not add per-user downstream credential isolation in this roadmap.
- Do not require 1Password or live cloud credentials for CI.
- Do not change the existing gateway tool names.
- Do not remove existing local API-key auth behavior.

## Cross-Cutting Principles

- One credential store implementation: gateway tools and CLI commands should not
  format `.env` files differently.
- Fail before leaking: detect missing auth placeholders before opening remote
  connections where possible.
- Acknowledge after consent: URL-mode elicitation must not mark consent complete
  before the user completes the provider flow.
- Public metadata only: discovery URLs, scopes, issuers, and diagnostics may be
  reported; tokens, codes, userinfo, secrets, and refresh material must not.
- Structured first: agents and UIs should act on fields such as `auth_state`,
  `auth_mode`, `missing_env_vars`, `auth_methods`, `auth_metadata`, and
  `next_step`, not parse prose.
- Docs and tests must state PMCP's auth boundary as clearly as the code enforces
  it.

## Top Interface-Freeze Gates

- IF-0-STORE-1 — PMCP uses one validated, permissioned env-store implementation
  for API-key credential reads/writes from gateway and CLI surfaces.
- IF-0-REMOTE-2 — Remote downstream auth placeholders are resolved or reported as
  structured missing auth before PMCP sends malformed header credentials.
- IF-0-ELICIT-3 — URL-mode elicitation is an explicit out-of-band flow with
  post-consent acknowledgement and no credential/code acceptance through PMCP.
- IF-0-SAFEURL-4 — Auth URLs, metadata URLs, diagnostics, and redaction behavior
  are constrained for production-safe display and logging.
- IF-0-AUTHOBS-5 — Auth state semantics, audit events, health/status, doctor,
  and feedback diagnostics provide enough non-secret evidence for operators.
- IF-0-AUTHSOAK-6 — A local third-party auth test matrix and documentation gate
  prove the production auth boundary before release.

## Phases

### Phase 1 — Credential Store Hardening (STORE)

**Objective**

Unify API-key credential storage behind a single safe env-store implementation
used by both `gateway.auth_connect` and `pmcp secrets`.

**Exit criteria**

- [ ] `gateway.auth_connect` and `pmcp secrets set/sync` share the same env-file
  read/write/format helpers.
- [ ] Env var names are validated with a strict shell-compatible pattern before
  writing.
- [ ] Env files written by PMCP are chmodded to `0600`.
- [ ] Credentials containing spaces, `#`, quotes, backslashes, and `=` round-trip
  without corrupting the env file.
- [ ] Newline-bearing credentials are rejected unless a deliberately documented
  multiline format is added.
- [ ] Tests prove credential content cannot inject additional env vars.

**Scope notes**

- Likely lanes:
  - Shared env-store module and caller migration.
  - Validation and injection/regression tests.
- Prefer moving helpers out of `src/pmcp/cli_commands/secrets.py` rather than
  duplicating its safer writer in gateway code.
- Preserve current user/project scope paths.

**Non-goals**

- Do not encrypt local env files in this phase.
- Do not add 1Password integration to runtime credential storage.
- Do not rotate or revoke provider credentials.

**Key files**

- `src/pmcp/tools/handlers.py`
- `src/pmcp/cli_commands/secrets.py`
- `src/pmcp/types.py`
- `tests/test_tools.py`
- `tests/test_secrets_command.py`
- `tests/test_auth.py`

**Depends on**

- (none)

**Produces**

- IF-0-STORE-1 — PMCP uses one validated, permissioned env-store implementation
  for API-key credential reads/writes from gateway and CLI surfaces.

### Phase 2 — Remote Header Auth Detection (REMOTE)

**Objective**

Make remote MCP auth placeholders explicit and structured so missing
third-party credentials are reported before PMCP attempts malformed SSE or
Streamable HTTP connections.

**Exit criteria**

- [ ] `${ENV_VAR}` placeholders in remote `headers` are resolved through a helper
  that also reports missing variables.
- [ ] Missing placeholder variables return `auth_state="missing_auth"` and a
  structured list of missing env vars from `gateway.provision` and
  `gateway.connect_server`.
- [ ] PMCP does not send empty `Authorization` headers caused by missing env vars.
- [ ] `pmcp doctor`, `pmcp secrets check`, and `pmcp status` can surface missing
  remote header credentials without printing values.
- [ ] SSE and Streamable HTTP remote paths have coverage for present and missing
  credential placeholders.

**Scope notes**

- Likely lanes:
  - Client/config auth-placeholder detection.
  - Gateway/CLI surfacing and tests.
- Treat any header placeholder as potentially secret, but especially
  `Authorization`, `X-API-Key`, and provider-specific token headers.
- Preserve literal non-placeholder headers.

**Non-goals**

- Do not invent a provider-specific auth schema for every remote server.
- Do not persist remote bearer tokens unless they are explicitly stored through
  the API-key/env-store flow.

**Key files**

- `src/pmcp/client/manager.py`
- `src/pmcp/config/loader.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/cli_commands/doctor.py`
- `src/pmcp/cli_commands/secrets.py`
- `tests/test_client_manager.py`
- `tests/test_tools.py`
- `tests/test_config_loader.py`
- `tests/test_secrets_command.py`

**Depends on**

- IF-0-STORE-1

**Produces**

- IF-0-REMOTE-2 — Remote downstream auth placeholders are resolved or reported as
  structured missing auth before PMCP sends malformed header credentials.

### Phase 3 — URL Elicitation Contract (ELICIT)

**Objective**

Correct URL-mode elicitation so PMCP shows an out-of-band provider URL first and
records acknowledgement only after the user explicitly confirms completion.

**Exit criteria**

- [ ] `pmcp auth connect` no longer auto-acknowledges URL-mode elicitation before
  the user completes the provider flow.
- [ ] A clear CLI acknowledgement path exists for URL-mode elicitation, with JSON
  and text output.
- [ ] `gateway.auth_connect(auth_mode="url_elicitation")` continues to reject
  credentials, OAuth codes, passwords, and refresh-token material.
- [ ] URL-mode output includes sanitized URL, `elicitation_id`, and next step
  without secret-bearing query values.
- [ ] Non-loopback `http://` elicitation URLs are rejected; loopback HTTP remains
  allowed for local development if needed.

**Scope notes**

- Likely lanes:
  - Gateway URL-mode input/output contract and URL validation.
  - CLI command UX and tests.
- Keep the flow explicit: show URL, user completes provider consent, user calls
  acknowledgement command or confirms interactively, then retry provision/invoke.

**Non-goals**

- Do not open a browser automatically unless a later UX decision adds that
  behavior.
- Do not exchange authorization codes or store provider refresh tokens.

**Key files**

- `src/pmcp/auth.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/cli.py`
- `src/pmcp/types.py`
- `tests/test_auth.py`
- `tests/test_tools.py`
- `tests/test_cli.py`

**Depends on**

- IF-0-STORE-1

**Produces**

- IF-0-ELICIT-3 — URL-mode elicitation is an explicit out-of-band flow with
  post-consent acknowledgement and no credential/code acceptance through PMCP.

### Phase 4 — Auth URL, Metadata, and Redaction Safety (SAFEURL)

**Objective**

Tighten production safety for auth-bearing URLs, public auth metadata, challenge
parsing, and diagnostic redaction.

**Exit criteria**

- [ ] Redaction covers bearer tokens, API keys, auth codes, JWT-looking strings,
  URL userinfo, and common auth-bearing query keys including `session`, `sid`,
  `jwt`, `assertion`, `saml`, and `ticket`.
- [ ] Public metadata fetches do not forward credentials, cookies, or caller auth
  headers.
- [ ] Metadata and elicitation URL validation rejects invalid, relative, or
  non-HTTPS non-loopback URLs.
- [ ] `WWW-Authenticate` parsing handles quoted values, missing scopes,
  insufficient scope, resource metadata URLs, and sanitized error descriptions.
- [ ] Gateway errors, feedback hints, audit events, status, doctor, and HTTP
  diagnostics share the same auth sanitizer.

**Scope notes**

- Likely lanes:
  - Auth helper hardening and parser tests.
  - Redaction integration across gateway/CLI/transport surfaces.
- Keep redaction best-effort but broad enough for realistic third-party auth
  diagnostics.

**Non-goals**

- Do not promise perfect secret detection for arbitrary provider-specific text.
- Do not fetch provider metadata from live services in normal CI.

**Key files**

- `src/pmcp/auth.py`
- `src/pmcp/policy/policy.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/cli.py`
- `src/pmcp/transport/http.py`
- `tests/test_auth.py`
- `tests/test_policy.py`
- `tests/test_tools.py`
- `tests/test_cli.py`
- `tests/test_transport_http.py`

**Depends on**

- IF-0-ELICIT-3
- IF-0-REMOTE-2

**Produces**

- IF-0-SAFEURL-4 — Auth URLs, metadata URLs, diagnostics, and redaction behavior
  are constrained for production-safe display and logging.

### Phase 5 — Auth Observability and Operator Semantics (AUTHOBS)

**Objective**

Make auth states and operator diagnostics precise enough for agents, CLIs, and
humans to act without parsing prose or seeing secrets.

**Exit criteria**

- [ ] Each auth state has documented semantics and one primary next action.
- [ ] Gateway outputs include structured `missing_env_vars` or equivalent
  non-secret fields where missing auth is env/header-based.
- [ ] Audit events distinguish missing credential, credential stored, remote auth
  challenge, insufficient scope, URL elicitation required, and URL elicitation
  acknowledged.
- [ ] `gateway.health`, `pmcp status --verbose`, `pmcp doctor`, and feedback
  payload previews expose non-secret auth evidence consistently.
- [ ] Tests assert common secret samples do not appear in auth-related outputs.

**Scope notes**

- Likely lanes:
  - Structured auth fields and audit events.
  - CLI/docs/operator output integration.
- Prefer additive output fields with defaults so older clients continue to parse
  responses.

**Non-goals**

- Do not add per-client or per-user authorization isolation.
- Do not make audit logs durable beyond the existing bounded diagnostics model.

**Key files**

- `src/pmcp/types.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/cli.py`
- `src/pmcp/cli_commands/doctor.py`
- `tests/test_tools.py`
- `tests/test_cli.py`
- `tests/test_phase4_e2e.py`

**Depends on**

- IF-0-REMOTE-2
- IF-0-ELICIT-3
- IF-0-SAFEURL-4

**Produces**

- IF-0-AUTHOBS-5 — Auth state semantics, audit events, health/status, doctor,
  and feedback diagnostics provide enough non-secret evidence for operators.

### Phase 6 — Auth Soak, Docs, and Release Gate (AUTHSOAK)

**Objective**

Prove the third-party auth boundary end to end with local fakes, update operator
documentation, and close production readiness only after verification evidence is
available.

**Exit criteria**

- [ ] Local fake downstream coverage models missing API key, present API key,
  missing remote bearer header, remote `WWW-Authenticate`, insufficient scope,
  URL elicitation required, and malicious auth URLs.
- [ ] End-to-end smoke covers provision -> auth connect -> retry for local API-key
  flows without leaking the credential.
- [ ] End-to-end smoke covers remote header missing-auth and remote challenge
  states through gateway and CLI/status surfaces.
- [ ] README and SECURITY document supported auth modes, non-goals, URL-mode
  expectations, env-store scope guidance, redaction limits, and HTTP exposure
  expectations.
- [ ] CHANGELOG records production auth hardening when release-bound.
- [ ] Full release verification passes before version bump or publish.

**Scope notes**

- Likely lanes:
  - Local fake auth matrix and E2E tests.
  - README/SECURITY/CHANGELOG/release closeout.
- Keep all normal tests offline and deterministic.
- Use live third-party providers only as optional manual evidence outside CI.

**Non-goals**

- Do not require external credentials in CI.
- Do not broaden the roadmap into general identity provider support.

**Key files**

- `tests/test_auth.py`
- `tests/test_tools.py`
- `tests/test_client_manager.py`
- `tests/test_cli.py`
- `tests/test_secrets_command.py`
- `tests/test_transport_http.py`
- `tests/test_phase4_e2e.py`
- `README.md`
- `SECURITY.md`
- `CHANGELOG.md`
- `specs/phase-plans-v4.md`

**Depends on**

- IF-0-STORE-1
- IF-0-REMOTE-2
- IF-0-ELICIT-3
- IF-0-SAFEURL-4
- IF-0-AUTHOBS-5

**Produces**

- IF-0-AUTHSOAK-6 — A local third-party auth test matrix and documentation gate
  prove the production auth boundary before release.

## Phase Dependency DAG

```text
STORE -> REMOTE -> SAFEURL -> AUTHOBS -> AUTHSOAK
   \        \          ^         ^
    \------> ELICIT ---/---------/
```

## Execution Notes

- Phase 1 should run first because every later auth path depends on the shared
  credential-store contract.
- Phase 2 and Phase 3 can be planned after Phase 1. They touch different primary
  flows and can be implemented in parallel if `handlers.py` and `cli.py`
  ownership is split carefully.
- Phase 4 should wait for both remote auth detection and URL elicitation so URL
  and redaction constraints cover all auth-bearing inputs.
- Phase 5 should wait for the core behavior to freeze; otherwise status, doctor,
  and audit semantics may churn.
- Phase 6 is the release gate. It should not define new runtime contracts unless
  a test exposes a production-readiness gap that must feed back into an earlier
  phase.
- Suggested first next command:
  `codex-plan-phase specs/phase-plans-v4.md Phase 1`

## Verification

Run these after implementation phases, not during roadmap planning:

```bash
uv run pytest tests/test_auth.py tests/test_tools.py tests/test_secrets_command.py -k "auth or credential or secret or env_var or injection or redact"
uv run pytest tests/test_client_manager.py tests/test_config_loader.py tests/test_tools.py -k "remote or header or auth or missing_env"
uv run pytest tests/test_cli.py tests/test_tools.py tests/test_auth.py -k "elicitation or consent or auth_connect"
uv run pytest tests/test_transport_http.py tests/test_policy.py tests/test_cli.py tests/test_tools.py -k "redact or metadata or www_authenticate or token or code"
uv run pytest tests/test_phase4_e2e.py tests/test_cli.py tests/test_tools.py -k "auth or credential or elicitation or scope or policy"
```

Whole-roadmap release verification:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
uv run pytest -q
uv build
```

Manual/local smoke before auth-hardening release:

```bash
pmcp secrets set PMCP_TEST_TOKEN test-token --scope project
pmcp secrets check --project .
pmcp status --json --pending
pmcp status --verbose --pending
pmcp doctor
```
