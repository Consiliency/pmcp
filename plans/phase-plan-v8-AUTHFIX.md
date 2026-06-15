---
phase_loop_plan_version: 1
phase: AUTHFIX
roadmap: specs/phase-plans-v8.md
roadmap_sha256: 3e07c3a7e8ca6399747a99b81f7575b58dc48ed79af11ef428715a78784342b7
---

# AUTHFIX: Auth Hardening

## Context

Phase AUTHFIX implements Phase 2 of `specs/phase-plans-v8.md`: make OAuth 2.1 Resource Server mode fail closed around a configured canonical resource URI, async cached JWKS, an operator algorithm allowlist, safe JWKS URL validation, and status-correct auth failures.

The roadmap hash was verified from `specs/phase-plans-v8.md` as `3e07c3a7e8ca6399747a99b81f7575b58dc48ed79af11ef428715a78784342b7`. Canonical `.phase-loop/` state marks `AUTHFIX` as the current unplanned phase after COMPLETE committed cleanly; legacy `.codex/phase-loop/` state is compatibility-only and is not authoritative for this run.

Current code has the unsafe seams the roadmap calls out: `validate_resource_server_token(...)` builds a blocking `PyJWKClient` per request, accepts the token header algorithm as the allowed algorithm, and `create_http_app(...)` falls back from `resource_server_audience` to `request.url_for("mcp")`, making Host-derived audience validation possible. `sanitize_public_auth_url(...)` and the existing `PyJWKSet.from_dict(...)` path are the reusable boundaries to extend.

`resource_server_audience` remains the public `create_http_app(...)` keyword for this phase, but its meaning is frozen as the configured canonical resource URI. In `resource-server` mode it is required, never derived from the inbound request, and is the value advertised in `WWW-Authenticate` when protected-resource metadata is not configured.

## Interface Freeze Gates

- [ ] IF-0-AUTHFIX-1 - `validate_resource_server_token(...)` validates `aud` against the configured canonical resource URI supplied through `resource_server_audience`; `resource-server` mode rejects startup without that value; JWKS signature verification uses an async `AsyncJWKS` helper with aiohttp, TTL caching, and an `asyncio.Lock`; `jwt.decode(...)` uses an operator allowlist defaulting to `("RS256", "ES256")` and never the token header as the allowlist; `resource_server_jwks_url` is accepted only when `sanitize_public_auth_url(...)` confirms an HTTPS public host; invalid token and unknown-kid-after-refresh paths return 401, JWKS fetch/connectivity paths return 503, insufficient scope remains 403, and diagnostics do not leak bearer tokens or raw JWKS URLs.

## Lane Index & Dependencies

- SL-0 - Token validation and async JWKS helper; Depends on: (none); Blocks: SL-1, SL-2; Parallel-safe: yes
- SL-1 - HTTP resource-server fail-closed wiring; Depends on: SL-0; Blocks: SL-2; Parallel-safe: no
- SL-2 - AUTHFIX verification and reducer closeout; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Token Validation and Async JWKS Helper

- **Scope**: Move network JWKS fetching out of synchronous token validation, add async TTL-cached JWKS retrieval, and lock token decoding to an operator algorithm allowlist.
- **Owned files**: `src/pmcp/auth.py`, `src/pmcp/remote_auth.py`, `tests/test_auth.py`
- **Interfaces provided**: `AsyncJWKS` with aiohttp fetch, TTL cache, and `asyncio.Lock`; synchronous `validate_resource_server_token(...)` over pre-fetched `jwks`; `allowed_algorithms` decode contract defaulting to `("RS256", "ES256")`; sanitized Resource Server auth diagnostics; IF-0-AUTHFIX-1 token/JWKS validation surface
- **Interfaces consumed**: pre-existing `sanitize_public_auth_url(...)`, `sanitize_auth_diagnostic(...)`, `ResourceServerAuthError`, `ResourceServerTokenClaims`, `_select_jwk_key(...)`, `_claim_scopes(...)`, `PyJWKSet.from_dict(...)`, PyJWT `InvalidTokenError` hierarchy, aiohttp client APIs
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add failing-first `tests/test_auth.py` coverage proving `validate_resource_server_token(...)` rejects a token signed with a header-selected algorithm outside the configured allowlist even when the JWK matches.
  - test: Add signed-fixture regressions for multi-audience and wrong-audience tokens proving only the configured canonical resource URI is accepted.
  - test: Add async `AsyncJWKS` tests with mocked aiohttp responses proving first fetch populates cache, concurrent cold-cache callers coalesce behind one lock-protected fetch, cached calls do not refetch before TTL expiry, and unknown `kid` triggers one forced refresh before returning 401.
  - test: Add JWKS failure diagnostics tests proving aiohttp/connectivity failures are classified for 503 handling and sanitized messages omit bearer tokens and raw secret-bearing URL query values.
  - impl: Introduce `AsyncJWKS` in `src/pmcp/auth.py`; validate its URL with `sanitize_public_auth_url(...)` without loopback-http allowance, fetch JSON with aiohttp, cap response size, require a JWKS object with `keys`, cache it by monotonic TTL, and guard cold/forced refresh with `asyncio.Lock`.
  - impl: Keep `validate_resource_server_token(...)` synchronous over supplied `jwks`, remove the `PyJWKClient` per-request network path, add `allowed_algorithms` with default `("RS256", "ES256")`, and pass that allowlist directly to `jwt.decode(...)` instead of using the token header algorithm.
  - impl: Preserve `ResourceServerAuthError("insufficient_scope", ...)` behavior, map malformed/expired/wrong-issuer/wrong-audience/unknown-kid errors to `invalid_token`, and add a non-secret JWKS-unavailable classification for HTTP 503 wiring.
  - impl: Audit `src/pmcp/remote_auth.py` for auth diagnostic reuse only if AUTHFIX changes shared diagnostic names; leave it unchanged if no symbol move is needed.
  - verify: `uv run pytest tests/test_auth.py -k "resource_server or jwks or audience or canonical or alg or sanitize"`
  - verify: `git diff --check -- src/pmcp/auth.py src/pmcp/remote_auth.py tests/test_auth.py`

### SL-1 - HTTP Resource-Server Fail-Closed Wiring

- **Scope**: Wire `create_http_app(...)` to require the canonical resource URI, validate JWKS URL at startup, use async JWKS verification per request, and return status-correct Resource Server challenges.
- **Owned files**: `src/pmcp/transport/http.py`, `tests/test_http_transport.py`, `tests/test_transport_http.py`
- **Interfaces provided**: `create_http_app(..., auth_mode="resource-server", resource_server_audience=<canonical_resource_uri>)` fail-closed startup contract; request-independent `_resource_audience()` value; async JWKS-to-token validation path; 401/403/503 auth response mapping; IF-0-AUTHFIX-1 HTTP Resource Server surface
- **Interfaces consumed**: SL-0 `AsyncJWKS`, SL-0 synchronous `validate_resource_server_token(...)`, SL-0 JWKS-unavailable classification, pre-existing `sanitize_public_auth_url(...)`, Starlette `Request`, `WWW-Authenticate` challenge generation, existing shared-secret and protected-resource metadata behavior
- **Parallel-safe**: no
- **Tasks**:
  - test: Update `tests/test_http_transport.py` so resource-server startup without `resource_server_audience` raises `ValueError`, and so no test expects fallback to `http://testserver/mcp` from `request.url_for("mcp")`.
  - test: Add a Host-spoof regression where a token for a spoofed request host is rejected while a token for the configured canonical resource URI is accepted.
  - test: Add startup tests rejecting `resource_server_jwks_url` values with `http://`, loopback, private, link-local, multicast, unspecified, or malformed hosts, using only redacted diagnostics.
  - test: Add request tests proving invalid token and unknown-kid-after-refresh return 401, insufficient scope returns 403, JWKS fetch/connectivity failure returns 503, and neither response body nor challenge headers leak `resource_server_jwks_url` query secrets.
  - test: Add or adjust `tests/test_transport_http.py` coverage only where shared `create_http_app(...)` construction needs the new required resource-server argument; keep shared-secret and unauthenticated transport tests unchanged.
  - impl: In `create_http_app(...)`, require `resource_server_issuer`, `resource_server_jwks_url`, and `resource_server_audience` when `auth_mode="resource-server"`; validate `resource_server_jwks_url` with `sanitize_public_auth_url(...)`; instantiate one `AsyncJWKS` cache for the app; and remove request-derived audience fallback.
  - impl: In `handle_mcp(...)`, await the app-level JWKS cache, call `validate_resource_server_token(...)` with `jwks` and the configured algorithm allowlist, preserve `request.scope["pmcp.auth"]`, and map auth classifications to 401, 403, or 503 without logging raw tokens or raw JWKS URLs.
  - impl: Keep `WWW-Authenticate` `resource` anchored to the configured canonical resource URI when no protected-resource metadata URL is configured; preserve existing protected-resource metadata behavior when metadata is configured.
  - verify: `uv run pytest tests/test_http_transport.py tests/test_transport_http.py -k "resource_server or jwks or audience or canonical or 401 or 403 or 503 or auth_mode or shared_secret"`
  - verify: `git diff --check -- src/pmcp/transport/http.py tests/test_http_transport.py tests/test_transport_http.py`

### SL-2 - AUTHFIX Verification and Reducer Closeout

- **Scope**: Verify AUTHFIX as one Resource Server contract, confirm IF-0-AUTHFIX-1 is fully produced, and record whether execution touched only phase-owned files.
- **Owned files**: none
- **Interfaces provided**: AUTHFIX verification evidence; IF-0-AUTHFIX-1 completion checklist; phase-owned dirty-path inventory for runner closeout
- **Interfaces consumed**: IF-0-AUTHFIX-1; SL-0 token/JWKS tests and implementation; SL-1 HTTP wiring tests and implementation; roadmap AUTHFIX exit criteria
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm no lane-owned file overlaps another lane-owned file and no executor touched files outside `src/pmcp/auth.py`, `src/pmcp/remote_auth.py`, `tests/test_auth.py`, `src/pmcp/transport/http.py`, `tests/test_http_transport.py`, and `tests/test_transport_http.py` for implementation.
  - test: Confirm the public `create_http_app(...)` keyword remains `resource_server_audience`, but behavior treats it as the required configured canonical resource URI and never derives it from `Host` or request URL state.
  - verify: `uv run pytest tests/test_auth.py tests/test_http_transport.py tests/test_transport_http.py -k "resource_server or jwks or audience or canonical or alg or 401 or 403 or 503"`
  - verify: `TMPDIR=/var/tmp uv run ruff check src/ tests/`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `TMPDIR=/var/tmp uv run pytest -q`
  - verify: `git diff --check`
  - verify: `git status --short`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-2: work-unit=`phase_reducer`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
uv run pytest tests/test_auth.py tests/test_http_transport.py tests/test_transport_http.py -k "resource_server or jwks or audience or canonical or alg or 401 or 403 or 503"
TMPDIR=/var/tmp uv run ruff check src/ tests/
uv run mypy src/pmcp --exclude baml_client
TMPDIR=/var/tmp uv run pytest -q
git diff --check
git status --short
```

## Acceptance Criteria

- [ ] `resource-server` mode fails closed at startup unless `resource_server_audience` is configured as the canonical resource URI; request URL and `Host` header data are never used to derive token audience.
- [ ] A token minted for another resource is rejected even when the request `Host` is spoofed toward that resource, while a token for the configured canonical resource URI is accepted.
- [ ] `resource_server_jwks_url` must be HTTPS and public-host only; private, link-local, loopback, multicast, unspecified, malformed, and non-HTTPS URLs fail startup with non-secret diagnostics.
- [ ] JWKS fetching uses aiohttp through an app-level `AsyncJWKS` cache with TTL reuse and an `asyncio.Lock` anti-stampede path; token validation no longer constructs blocking `PyJWKClient` instances on the request path.
- [ ] `jwt.decode(...)` receives an operator allowlist defaulting to `("RS256", "ES256")`; the token `alg` header is never used as the allowed algorithm list.
- [ ] `InvalidTokenError` and unknown-kid-after-refresh paths return 401, JWKS fetch/connectivity failures return 503, insufficient scope remains 403, and no response or log-oriented diagnostic leaks bearer tokens or raw JWKS URL secrets.
- [ ] Shared-secret and no-auth HTTP behavior are unchanged; protected-resource metadata behavior is preserved.
- [ ] `ruff`, mypy, and full `pytest` pass with `TMPDIR=/var/tmp` for commands that need a temporary directory outside `/tmp`.
