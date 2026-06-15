---
phase_loop_plan_version: 1
phase: AUTHRS
roadmap: specs/phase-plans-v7.md
roadmap_sha256: f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5
---

# AUTHRS: OAuth 2.1 Resource Server

## Context

Phase AUTHRS implements Phase 5 of `specs/phase-plans-v7.md`: turn PMCP's HTTP transport from an optional static bearer guard into an OAuth 2.1 Resource Server with RFC 8707 audience binding, while preserving the existing shared-secret behavior as an explicit single-tenant mode and isolating downstream remote-server credentials per tenant.

The roadmap hash was verified from `specs/phase-plans-v7.md` as `f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5`. Canonical `.phase-loop/` state exists and is authoritative: it currently records REDACT, CONCURR, and MANIFEST complete, ENVFIX blocked/awaiting closeout work, and AUTHRS unplanned. This plan can be written as the AUTHRS planning artifact, but AUTHRS execution must wait until the ENVFIX dependency and IF-0-ENVFIX-1 are complete in `.phase-loop/`. Legacy `.codex/phase-loop/` state is compatibility-only and is not an input to this plan.

Current code has the relevant seams in place: `create_http_app(...)` in `src/pmcp/transport/http.py` already owns `/mcp` bearer handling, PRM discovery, `WWW-Authenticate` challenges, health/metrics exclusions, rate limiting, and request metadata; `src/pmcp/auth.py` owns public auth URL sanitization, challenge parsing, and non-secret diagnostics; `src/pmcp/remote_auth.py` owns remote header placeholder resolution; and `src/pmcp/client/manager.py` is the single writer for applying resolved headers to SSE and streamable HTTP downstream clients. `pyproject.toml` does not currently declare JWT verification as a direct runtime dependency, so dependency metadata is isolated in its own preamble lane.

## Interface Freeze Gates

- [ ] IF-0-AUTHRS-1 - `create_http_app(...)` accepts `auth_mode: Literal["none", "shared-secret", "resource-server"] | None = None`, `resource_server_issuer: str | None = None`, `resource_server_jwks_url: str | None = None`, `resource_server_audience: str | None = None`, `required_scopes: list[str] | None = None`, and `allowed_origins: list[str] | None = None`. Effective mode is explicit `auth_mode` when provided, otherwise `"shared-secret"` when `auth_token` is set and `"none"` when no auth settings are set, preserving compatibility while making static bearer mode named. In `"resource-server"` mode, `/mcp` extracts `Authorization: Bearer <jwt>`, validates signature through AS JWKS, validates `iss`, `exp`, `nbf`, and `aud == resource_server_audience` where the audience defaults to the canonical PMCP `/mcp` resource URI, rejects invalid or expired tokens with 401, rejects wrong audience with 401, rejects missing required scopes at runtime with 403 plus `WWW-Authenticate: Bearer error="insufficient_scope", scope="..."`, and never applies auth to `/health` or `/metrics`. `sanitize_public_auth_url(...)` rejects private, link-local, loopback, multicast, and unspecified host addresses for public auth metadata URLs, and `/mcp` returns 403 when `allowed_origins` is configured and the request `Origin` is not an exact allowed value.
- [ ] IF-0-AUTHRS-2 - `resolve_remote_headers_for_tenant(headers: Mapping[str, str] | None, *, server_name: str, tenant_id: str | None, project_root: Path | None = None, include_process_env: bool = True) -> RemoteHeaderAuthResolution` is the remote-header entry point used by `ClientManager` for SSE and streamable HTTP servers. When `tenant_id` is `None`, it preserves existing process/project/user env lookup semantics. When `tenant_id` is present, it reads tenant-scoped env values from a path derived from IF-0-ENVFIX-1 project resolution, does not read another tenant's file, records only non-secret env var names in diagnostics, and makes cross-tenant header lookup failures deterministic and testable without live credentials.

## Lane Index & Dependencies

- SL-0 - OAuth dependency metadata; Depends on: (none); Blocks: SL-1, SL-2; Parallel-safe: no
- SL-1 - Auth helpers and tenant header resolution; Depends on: SL-0; Blocks: SL-2, SL-3, SL-4; Parallel-safe: no
- SL-2 - HTTP resource-server transport; Depends on: SL-0, SL-1; Blocks: SL-3, SL-4; Parallel-safe: no
- SL-3 - AUTHRS operator docs; Depends on: SL-1, SL-2; Blocks: SL-4; Parallel-safe: no
- SL-4 - AUTHRS verification and closeout; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - OAuth Dependency Metadata

- **Scope**: Declare the JWT verification dependency surface directly so AUTHRS does not rely on transitive packages from `mcp`.
- **Owned files**: `pyproject.toml`, `uv.lock`
- **Interfaces provided**: direct runtime dependency for JWT/JWKS verification, including crypto-backed signature verification usable by `src/pmcp/auth.py`; lockfile state matching `pyproject.toml`
- **Interfaces consumed**: current package metadata, current `uv.lock` resolver state, existing optional `http` extra behavior, and the roadmap requirement for signed JWT fixtures with no live AS
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm the pre-change dependency surface does not list a direct JWT verification dependency in `pyproject.toml` even though `uv.lock` contains transitive JWT packages through `mcp`.
  - impl: Add a direct runtime dependency such as `pyjwt[crypto]>=2.10.0` to `pyproject.toml` if the selected implementation uses PyJWT for JWKS-backed validation.
  - impl: Refresh `uv.lock` with the repo's normal uv workflow without introducing unrelated dependency updates.
  - verify: `uv lock --check` or the repo-equivalent lock consistency command after the lock is updated.
  - verify: `git diff --check -- pyproject.toml uv.lock`

### SL-1 - Auth Helpers and Tenant Header Resolution

- **Scope**: Add reusable auth validation helpers, public URL hardening, and tenant-isolated remote header resolution while keeping `client/manager.py` to the `_remote_headers` region.
- **Owned files**: `src/pmcp/auth.py`, `src/pmcp/remote_auth.py`, `src/pmcp/client/manager.py`, `tests/test_auth.py`, `tests/test_client_manager.py`
- **Interfaces provided**: `validate_resource_server_token(...)` or equivalent helper that verifies AS-issued JWTs against JWKS with issuer, expiry, not-before, and audience checks; `sanitize_public_auth_url(...)` rejection of private/link-local/loopback public metadata URLs; `resolve_remote_headers_for_tenant(...)` for IF-0-AUTHRS-2; manager integration that passes tenant context into remote header resolution without changing downstream transport APIs
- **Interfaces consumed**: SL-0 JWT dependency, REDACT's canonical auth diagnostic redactor, ENVFIX IF-0-ENVFIX-1 project/scope path resolution, existing `RemoteHeaderAuthResolution`, `MissingRemoteHeaderAuthError`, `build_remote_header_env_lookup(...)`, `read_env_file(...)`, `resolve_scope_path(...)`, `RemoteMcpServerConfig.headers`, and CONCURR's single-writer manager invariant for the `_remote_headers` region
- **Parallel-safe**: no
- **Tasks**:
  - test: Add signed-token tests in `tests/test_auth.py` using local generated keys/JWKS fixtures for valid token, invalid signature, expired token, future `nbf`, wrong issuer, missing audience, and wrong audience.
  - test: Add `sanitize_public_auth_url(...)` SSRF regressions for HTTPS loopback, private RFC1918, link-local, multicast, and unspecified IP hosts, while preserving accepted public HTTPS metadata URLs and redacted query values.
  - test: Add tenant-resolution regressions proving tenant A and tenant B read different downstream header values, tenant mode does not fall back to another tenant's file, missing placeholders report only env var names, and legacy `tenant_id=None` lookup keeps process/project/user precedence.
  - test: Add a focused `tests/test_client_manager.py` regression proving `_remote_headers(...)` or its replacement passes tenant context to remote header resolution for SSE and streamable HTTP configs without touching unrelated manager lifecycle behavior.
  - impl: Implement JWT claim validation and JWKS key selection in `src/pmcp/auth.py`, keeping returned claim data non-secret and avoiding live network calls in tests by accepting an injectable JWKS fetch/cache path.
  - impl: Harden `sanitize_public_auth_url(...)` with `ipaddress` checks for public metadata URLs while preserving loopback HTTP only for URL elicitation paths that already opt into `allow_loopback_http=True`.
  - impl: Add `resolve_remote_headers_for_tenant(...)` in `src/pmcp/remote_auth.py` and keep existing `resolve_remote_headers(...)` and `build_remote_header_env_lookup(...)` compatible for non-tenant callers.
  - impl: Update only the manager remote-header helper/call sites so remote SSE and streamable HTTP connections consume the new tenant-aware resolver, leaving CONCURR lifecycle code untouched.
  - verify: `uv run pytest tests/test_auth.py tests/test_client_manager.py -k "token or audience or tenant or remote_headers or public_auth_url or origin or scope"`
  - verify: `git diff --check -- src/pmcp/auth.py src/pmcp/remote_auth.py src/pmcp/client/manager.py tests/test_auth.py tests/test_client_manager.py`

### SL-2 - HTTP Resource-Server Transport

- **Scope**: Wire resource-server mode into the HTTP transport, including audience-bound token checks, challenge/scope semantics, shared-secret compatibility, and Origin enforcement.
- **Owned files**: `src/pmcp/transport/http.py`, `tests/test_http_transport.py`
- **Interfaces provided**: IF-0-AUTHRS-1 transport behavior; explicit `auth_mode` selection; canonical resource URI default for audience; `WWW-Authenticate` responses for unauthenticated, invalid, and insufficient-scope cases; exact-Origin rejection with 403; unchanged unauthenticated `/health` and `/metrics`
- **Interfaces consumed**: SL-1 token validation helper, SL-1 sanitized public metadata URLs, existing `normalize_auth_metadata(...)`, existing PRM route creation, existing `GatewayDiagnosticsInfo`, current shared-secret `auth_token` behavior, Starlette request/response primitives, and roadmap MCP spec target 2025-11-25
- **Parallel-safe**: no
- **Tasks**:
  - test: Add `tests/test_http_transport.py` cases for default compatibility: no auth settings remains unauthenticated, existing `auth_token` continues to require `Authorization: Bearer <token>`, and explicit `auth_mode="shared-secret"` behaves the same.
  - test: Add resource-server tests using the SL-1 signed fixtures: valid JWT reaches the session manager, missing bearer returns 401 with challenge, invalid/expired token returns 401, wrong audience returns 401, and missing required scope returns 403 with `error="insufficient_scope"` and `scope="..."`.
  - test: Add PRM/audience tests proving `resource_server_audience` defaults to the canonical `/mcp` resource URI and can be explicitly configured for RFC 8707 resource indicators.
  - test: Add Origin tests proving configured allowed origins pass exactly, absent Origin is handled according to the chosen policy, and invalid Origin returns 403 before the MCP session manager runs.
  - impl: Extend `create_http_app(...)` with the frozen AUTHRS parameters without changing existing positional call compatibility.
  - impl: Add a small mode resolver so explicit `auth_mode` wins, legacy `auth_token` maps to `shared-secret`, and invalid resource-server configuration fails closed with non-secret diagnostics.
  - impl: Replace the inline shared bearer guard with mode-specific auth handling that calls SL-1 validation helpers in resource-server mode and emits safe `WWW-Authenticate` headers.
  - impl: Keep `/health`, `/metrics`, rate-limit behavior, input size checks, rmcp pre-session GET compatibility, and PRM route registration unchanged except for new non-secret diagnostics fields when needed.
  - verify: `uv run pytest tests/test_http_transport.py -k "token or audience or resource or shared_secret or origin or scope or auth"`
  - verify: `git diff --check -- src/pmcp/transport/http.py tests/test_http_transport.py`

### SL-3 - AUTHRS Operator Docs

- **Scope**: Document the two HTTP auth modes and the remaining trust boundary without changing runtime behavior.
- **Owned files**: `README.md`, `SECURITY.md`
- **Interfaces provided**: operator-facing mode selection docs for `shared-secret` and `resource-server`; security caveat that PMCP is a Resource Server, not an Authorization Server; no-secret examples for resource URI, issuer, JWKS URL, scopes, and allowed origins
- **Interfaces consumed**: IF-0-AUTHRS-1, IF-0-AUTHRS-2, existing README HTTP security section, existing downstream authorization section, existing tenant code-mode trust model, and SECURITY's no-SSO/RBAC/billing posture
- **Parallel-safe**: no
- **Tasks**:
  - test: Review README/SECURITY snippets for secret placeholders only; examples must not include real bearer tokens, private keys, or credential payloads.
  - impl: Update README HTTP security docs to distinguish `shared-secret` single-tenant mode from `resource-server` mode and show non-secret configuration examples for issuer, JWKS URL, resource audience, required scopes, and allowed origins.
  - impl: Update SECURITY to state PMCP validates AS-issued access tokens as a Resource Server but does not provide an Authorization Server, DCR, SSO, RBAC, billing, or complete multi-tenant identity service.
  - impl: Document tenant downstream header isolation at the behavior level without exposing tenant file contents or suggesting cross-tenant credential sharing.
  - verify: `git diff --check -- README.md SECURITY.md`

### SL-4 - AUTHRS Verification and Closeout

- **Scope**: Run the AUTHRS verification set, confirm IF-0-AUTHRS-1 and IF-0-AUTHRS-2 are represented by tests and docs, and prepare runner closeout evidence without owning additional source files.
- **Owned files**: none
- **Interfaces provided**: AUTHRS verification evidence; IF-0-AUTHRS-1 and IF-0-AUTHRS-2 completion checklist; phase-owned dirty-path inventory for SL-0 through SL-3
- **Interfaces consumed**: IF-0-AUTHRS-1, IF-0-AUTHRS-2, SL-0 dependency changes, SL-1 auth/tenant results, SL-2 transport results, SL-3 docs results, roadmap AUTHRS exit criteria, and `.phase-loop/` dependency state showing REDACT, CONCURR, and ENVFIX complete before execution closeout
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm no lane-owned file overlaps another lane-owned file and no executor touched files outside the active AUTHRS ownership set.
  - test: Confirm every AUTHRS exit criterion has failing-first or contract coverage in `tests/test_auth.py`, `tests/test_http_transport.py`, or `tests/test_client_manager.py`.
  - test: Confirm REDACT's canonical sanitizer handles all new auth-error text and no auth tests assert raw token, private key, or credential values in logs or diagnostics.
  - verify: `uv run pytest tests/test_auth.py tests/test_http_transport.py tests/test_client_manager.py -k "token or audience or tenant or origin or scope or resource or remote_headers"`
  - verify: `TMPDIR=/var/tmp uv run pytest`
  - verify: `uv run ruff check .`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `uv lock --check`
  - verify: `git status --short`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
uv run pytest tests/test_auth.py tests/test_http_transport.py tests/test_client_manager.py -k "token or audience or tenant or origin or scope or resource or remote_headers"
TMPDIR=/var/tmp uv run pytest
uv run ruff check .
uv run mypy src/pmcp --exclude baml_client
uv lock --check
git status --short
```

Effective automation.suite_command:

```bash
TMPDIR=/var/tmp uv run pytest && uv run ruff check . && uv run mypy src/pmcp --exclude baml_client && uv lock --check
```

## Acceptance Criteria

- [ ] `resource-server` mode validates AS-issued JWTs with JWKS-backed signature verification, issuer validation, expiry/not-before validation, and RFC 8707 audience binding to PMCP's canonical resource URI or an explicit resource audience.
- [ ] Invalid, expired, malformed, missing, and wrong-audience access tokens fail closed with 401 and non-secret diagnostics; missing required scopes fail with 403 and `WWW-Authenticate: Bearer error="insufficient_scope", scope="..."`.
- [ ] Existing static bearer behavior remains compatible but is reachable as explicit `shared-secret` mode; `/health` and `/metrics` remain unauthenticated by AUTHRS changes.
- [ ] Tenant downstream credential resolution uses an isolated tenant-aware lookup path and tests prove tenant A cannot read tenant B's downstream header values.
- [ ] `sanitize_public_auth_url(...)` rejects private, link-local, loopback, multicast, and unspecified hosts for public auth metadata URLs, and configured invalid `Origin` values return 403 before MCP handling.
- [ ] README and SECURITY distinguish `shared-secret` from `resource-server`, state PMCP is not an Authorization Server and not multi-tenant-complete without an external AS, and avoid secret-bearing examples.
- [ ] `uv run pytest tests/test_auth.py tests/test_http_transport.py tests/test_client_manager.py -k "token or audience or tenant or origin or scope or resource or remote_headers"` passes.
- [ ] Full verification passes with `TMPDIR=/var/tmp uv run pytest`, `uv run ruff check .`, `uv run mypy src/pmcp --exclude baml_client`, and `uv lock --check`.
