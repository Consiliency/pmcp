# v7 Code Review Findings — 2026-06-15

Review of `git diff a276e9a..HEAD` (the autonomously phase-loop-generated v7 work).
High-effort multi-agent review; most findings empirically confirmed by the finders.
**Recommendation: fix HIGH + MEDIUM before bumping to 1.14.0 or pushing.**

Two findings are especially notable: a phase left its *own* target only partly
fixed (REDACT missed two endpoints; CONCURR missed the reconnect path).

## HIGH — fix before release

| ID | Location | Issue |
|----|----------|-------|
| R1 | `handlers.py:4862` (tasks_list), `:4918` (tasks_get) | **Incomplete REDACT.** The redaction sweep wired `invoke` + `tasks_result` through `_sanitize_task_for_output` but left `tasks_list` and `tasks_get` returning `task.status_message`/`raw` verbatim — the exact C2 secret-leak class the phase existed to close. |
| R2 | `client/manager.py:1386` | **Incomplete CONCURR.** Every connect path was wrapped in `_lifecycle_lock` *except* `_reconnect_loop`, which calls `_connect_singleflight` with no lock — the C3 race (orphaned subprocess / zombie `_clients` entry) the phase existed to fix, still live on the reconnect path. |
| R3 | `transport/http.py:211` | **RFC 8707 audience bypass.** When `resource_server_audience` is unset, the audience is derived from `request.url_for("mcp")` → the client-controlled Host header. A same-issuer token minted for a *different* resource is accepted by spoofing `Host`. (Latent: `server.py` doesn't wire resource-server mode yet, but it's armed the moment anyone enables it.) |
| R4 | `auth.py:211` | **JWKS fetch blocks the event loop + refetches per request.** `PyJWKClient(jwks_url)` is built fresh each call and does a *synchronous* `urllib.urlopen` (default 30s) directly on the asyncio loop → a slow IdP stalls the whole gateway; also defeats JWKS caching. |
| R5 | `handlers.py:2518` | **Registry fetch blocks the event loop.** `catalog_search`/`request_capability` → `_load_registry_candidates` → `fetch_registry_servers()` (blocking `urlopen`, 5s) on the loop, on *every* call when the cwd-relative cache is missing (never saved/memoized). |
| R6 | `manifest/matcher.py:93` | **Matcher rescoring regression.** New `matched_weight/3.0` scoring drops real queries below the 0.2 threshold against the *shipped* manifest: `database sql`, `headless browser`, `chrome automation` now return no match; `postgres database` mis-matches `fetch`. (M6's fix overcorrected.) |

## MEDIUM

| ID | Location | Issue |
|----|----------|-------|
| R7 | `client/manager.py:555` | `_lifecycle_lock` is now held across the full connect + retry backoff (`RETRY_DELAYS` sleeps + transport timeout). One unreachable server serializes/starves every other lazy-start, disconnect, and refresh for seconds–minutes. |
| R8 | `handlers.py` auth_connect / `:2511` | **Write/read project-root asymmetry.** `auth_connect` now writes secrets to `self._project_root` (`--project`), but the manager still *reads* them via cwd-discovery. With `--project ≠ cwd`, `auth_connect` reports success but the remote connection fails with missing-header. Backward-compat regression. |
| R9 | `auth.py:217`,`:226` | JWKS/kid errors (`PyJWKClientError`) and an unfiltered token `alg` (HS256 token + RSA-key kid → `TypeError`) aren't subclasses of `InvalidTokenError`, so they escape the `except` → HTTP **500** instead of 401, possibly leaking `jwks_url`. No alg allowlist. |
| R10 | `handlers.py:3292` | `_unknown_service` PascalCase check dropped the `if i > 0` guard that skipped the leading word; queries like "Search the web" / "Database tools" now bypass manifest category matching. |
| R11 | `transport/http.py:191` | `resource_server_jwks_url` isn't constrained to `https://` or checked against private/link-local hosts (unlike `sanitize_public_auth_url`) → operator/config SSRF + plaintext JWKS retrieval. |

## CLEANUP (low)

- `handlers.py:2599` — dead `_legacy_query_mcp_registry` (never called).
- `handlers.py:4382` — `search_registry` throws away the typed `RegistryServerEntry` and re-parses `.raw` by hand (two parsers for one schema).
- `manifest/registry.py:222` — re-implements the cache-IO trio already in `refresher.py` (only JSON-vs-YAML differs).
- `auth.py:275` — `sanitize_auth_diagnostic` rebuilds its alternation regex per call on the redaction hot path; should be module-level like `_JWT_RE`.

## Verified NOT bugs (good)
- No signature-verification bypass / no `alg=none` / no HS-RS key confusion (PyJWT refuses the RSA key as an HMAC secret). `aud`/`iss`/`exp`/`nbf` all required.
- No token passthrough; incoming client `Authorization` never forwarded downstream.
- `env_store` `os.open(0o600)` is a genuine improvement (no fd leak — `fdopen` owns it).
- `sanitize_public_auth_url` private-host guard is a deliberate SSRF hardening.
- `registry.py` degrades to local manifest on fetch failure (no startup crash).
</content>
