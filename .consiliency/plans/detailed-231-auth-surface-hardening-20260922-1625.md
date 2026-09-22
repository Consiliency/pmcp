# Detailed plan: harden the HTTP auth surface — JWKS amplifier (S-07), JWKS fetch timeout (S-08), metadata Host reflection (S-10)

## Task
Fix the remainder of the 2026-09-01 codebase review finding tracked in Consiliency/pmcp#231: three defects on the HTTP auth surface (the gateway's trust boundary), where the dangerous direction is failing OPEN.

- **S-07** — unauthenticated outbound-fetch amplifier: an unknown `kid` forces an outbound JWKS refresh with no cooldown, and rate limiting runs *after* auth, so an unauthenticated caller drives repeated outbound fetches with random `kid`s.
- **S-08** — the JWKS fetch has no `ClientTimeout` and runs while holding `_refresh_lock`, so a slow/hanging JWKS endpoint stalls every waiter on that lock and blocks the event loop.
- **S-10** — the OAuth protected-resource-metadata endpoint returns a Host-derived `resource` and its route never checks the Host allowlist, so a request with an attacker-supplied `Host` gets that Host advertised back as the resource.

S-09 is already fixed and merged in #281 — out of scope here.

## Research summary
All three findings were confirmed by reading the current tree at commit `af67207` (branch `plan/231-auth-surface`).

- `AsyncJWKS` lives in `src/pmcp/auth.py`: `__init__` at `auth.py:380`, `get()` at `auth.py:401` (double-checked locking on `_refresh_lock`), `get_for_token()` at `auth.py:414` (calls `self.get(force_refresh=True)` at `auth.py:421` for any unknown `kid`), `_fetch()` at `auth.py:424` (opens `aiohttp.ClientSession()` at `auth.py:426` with **no** `ClientTimeout`). `get()` holds `_refresh_lock` (acquired `auth.py:405`) across the whole `_fetch()` call.
- In `src/pmcp/transport/http.py`, `handle_mcp` runs `_host_rejected` (`http.py:517`) and then the resource-server auth block (`http.py:534`) which calls `resource_jwks.get_for_token(token)` (`http.py:545`); per-IP rate limiting runs only **after** that at `http.py:583`, and only when `rate_limit_rpm > 0`. So the amplifier is reachable pre-rate-limit, unauthenticated.
- `handle_protected_resource_metadata` (`http.py:469`) sets `"resource": str(request.url_for("mcp"))` (`http.py:472`) — Host-derived. Its route is registered at `http.py:743-750`, only when `auth_metadata.protected_resource_metadata_url` is set, and never calls `_host_rejected`. `_host_rejected` (`http.py:388`) returns `False` when `allowed_origins is None` (the local-first default), and `_resource_audience()` (`http.py:405`) returns `resource_server_audience or ""`.
- **#211 (`56a555c` "fix(auth): stop vouching for URLs a downstream server supplied")** touched `auth.py`, `SECURITY.md`, `CHANGELOG.md` and `tests/test_auth.py` — it did **not** touch the metadata handler; the Host-derived `resource` predates it (introduced in the AUTHRS commit `3c2ad0b`). #211 is therefore a *principle* constraint, not a code overlap. Its principle (`SECURITY.md:87-96`): PMCP does not resolve/verify server-supplied URLs, so it "stop[s] claiming otherwise — a server-supplied URL is relayed unverified and presented as such" (`url_verified`/`verified_urls`/`resource_metadata_url_verified` default to unverified). Separately, `SECURITY.md:34-36` already asserts the token audience "is bound to the operator-configured canonical resource URI (`resource_server_audience`, per RFC 8707) and is never derived from the request Host header." The metadata endpoint's Host-derived `resource` violated the spirit of that same principle.
- Test harness: `tests/test_auth.py` tests `AsyncJWKS` by monkeypatching `jwks._fetch` and counting calls (`test_async_jwks_caches_and_coalesces_fetches:374`, `test_async_jwks_unknown_kid_forces_one_refresh:396`). `tests/test_transport_http.py` builds the app via a `_make_app` helper (patches `StreamableHTTPSessionManager`) and drives it with a Starlette `TestClient` (`test_well_known_protected_resource_metadata_is_public:169`).

## Design decisions (made explicitly)

### S-07 — global forced-refresh cooldown (not a per-kid negative cache, not reorder-before-auth)
**Decision:** add a per-`AsyncJWKS` cooldown that bounds *forced* refreshes to at most one per window (default 10 s). In `get_for_token`, only call `get(force_refresh=True)` when `time.monotonic() - self._last_forced_refresh >= self._forced_refresh_cooldown_seconds`; set the timestamp *before* awaiting the refresh. During the cooldown an unknown `kid` returns the currently-cached JWKS (no outbound fetch).

**Why not the alternatives:**
- A **per-`kid` negative cache** does not stop the attack: the amplifier sends *random* `kid`s, each one unknown and absent from the negative cache, so each still forces a fetch.
- **Moving rate limiting ahead of auth** is insufficient and broad: rate limiting is per-IP and optional (only when `rate_limit_rpm > 0`), so it bounds nothing in the default config, and reordering changes semantics for every auth path.

**Failure mode when the cooldown trips:** an unknown `kid` arriving during an active cooldown is served the cached JWKS; `validate_resource_server_token` then raises `ResourceServerAuthError("invalid_token", "No matching JWK found.")` → `handle_mcp` returns **401** (`http.py:576`). The client retries; the next window's first unknown `kid` forces a fresh fetch.

**Cannot lock out a legitimate key rotation:** the *first* unknown `kid` in each window always forces one refresh, which picks up rotated keys. The cooldown only suppresses *additional* fetches within a short window (10 s ≪ the 300 s cache TTL), so a rotated key is picked up within one cooldown period at worst — strictly better than the existing TTL-bounded staleness. The only degraded case is a genuinely-new key arriving during a window whose one refresh already fired without it: a transient 401, self-healing on the next request after the window. This is the correct fail-closed direction (reject an unverifiable token), not a lockout.

### S-08 — total `ClientTimeout` on the fetch
**Decision:** add `aiohttp.ClientTimeout(total=self._fetch_timeout_seconds)` (default 5 s) to the `ClientSession`/`get` in `_fetch`. Use **total**, not connect-only: the fetch already caps size (`max_bytes`) but a malicious endpoint can drip bytes under that cap or stall after connect, so only a total bound covers connect + header wait + body read.

**Applied where / relationship to the lock:** the timeout *is* what bounds the lock hold — `_fetch` runs entirely inside `get()`'s `_refresh_lock`, so bounding `_fetch` to ≤5 s bounds every waiter's latency to ≈5 s. (The framing "shorter than the lock hold" is inverted: there is no independent lock-hold budget; the fetch timeout defines it.) 5 s is generous for a well-behaved HTTPS JWKS endpoint (typically <500 ms) yet collapses a hung endpoint's damage.

**What happens to waiters on timeout:** aiohttp raises `asyncio.TimeoutError`; `_fetch`'s `except Exception` re-raises it as `ResourceServerJWKSUnavailable`. `_jwks`/`_expires_at` are updated only *after* a successful `_fetch`, so a timeout leaves the cache untouched and the lock released by `async with`. The next waiter re-enters `get()`, re-checks `now < self._expires_at`: if a prior successful fetch left a still-valid cache it **serves stale-but-valid keys**; otherwise it attempts its own fetch (also bounded) and, failing, `handle_mcp` returns **503** (`http.py:565-570`). No hang, no event-loop stall.

### S-10 — return a non-Host-derived (operator-configured) `resource`, not "reject the Host outright"
**Decision:** stop deriving `resource` from the request. Compute a canonical resource from operator config in the app closure and return it verbatim: `resource_server_audience` when set (the RFC 8707 canonical identifier, and the exact value tokens are validated against), else the origin of the operator-configured `protected_resource_metadata_url` (`<scheme>://<netloc>/mcp`), else `""`. Never touch `request`.

**Why not "reject a disallowed Host outright" (call `_host_rejected`):** `_host_rejected` returns `False` whenever `allowed_origins is None`, which is the local-first default. So reject-outright would still reflect the attacker Host in the default configuration — it fails open exactly where it matters. Returning an operator-configured value fixes S-10 unconditionally, in every config.

**What #211 constrains, and how this respects it:** the endpoint must never advertise an attacker-supplied value as though PMCP stands behind it — advertising an attacker-supplied Host as the vouched `resource` is precisely the "we verified this" implication #211 removed for server-supplied URLs. This fix is *stronger* than #211's relay-but-mark-unverified remedy: because the endpoint has its own operator-configured canonical resource, it simply never emits the untrusted value, so there is no unverified attacker value to mark and no "verified" claim is reintroduced. It also aligns the metadata endpoint with the existing `SECURITY.md:34-36` promise that the canonical resource "is never derived from the request Host header" — today the token path honors that and the metadata path violates it; this closes the divergence. **The endpoint is allowed to imply:** "the operator configured this resource identifier." **It must not imply:** anything about an attacker-supplied Host — it must not echo it at all.

## Corrections to the triage description
- S-07: `get_for_token` is defined at `auth.py:414`; `auth.py:421` is the `get(force_refresh=True)` *call site* inside it, not the definition. Both the def and the call site are load-bearing for the fix.
- S-08: confirmed accurate — `_fetch` at `auth.py:424`, `ClientSession()` at `auth.py:426`, no `ClientTimeout`, run under `_refresh_lock`.
- S-10: confirmed accurate — `handle_protected_resource_metadata` at `http.py:469`, `resource` at `http.py:472`, route at `http.py:743-750`, `_host_rejected` only in `handle_mcp` at `http.py:517`. Added nuance: `_host_rejected` no-ops when `allowed_origins is None`, which is why reject-outright would fail open by default.
- Additional nuance not in the triage: the metadata route only exists when `protected_resource_metadata_url` is configured (`http.py:740`), and it can be configured while `resource_server_audience` is unset (the `test_transport_http.py` harness does exactly this) — hence the fallback in the S-10 decision.

## Changes

### `src/pmcp/auth.py` (modify)
- `AsyncJWKS.__init__` (~`auth.py:380`) — modify — add keyword params `forced_refresh_cooldown_seconds: float = 10.0` and `fetch_timeout_seconds: float = 5.0`; store as `self._forced_refresh_cooldown_seconds`, `self._fetch_timeout_seconds`, and init `self._last_forced_refresh: float = float("-inf")`. (S-07, S-08)
- `AsyncJWKS.get_for_token` (`auth.py:414`) — modify — gate the `self.get(force_refresh=True)` call on `time.monotonic() - self._last_forced_refresh >= self._forced_refresh_cooldown_seconds`, setting `self._last_forced_refresh = <now>` before the await; on cooldown, return the already-fetched cached `jwks`. (S-07)
- `AsyncJWKS._fetch` (`auth.py:424`) — modify — construct `aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._fetch_timeout_seconds))` (equivalently pass `timeout=` to `session.get`). Leave the existing redirect refusal, size cap, and JSON validation unchanged. (S-08)

### `src/pmcp/transport/http.py` (modify)
- Add a closure helper near `_resource_audience` (~`http.py:405`) — add — `_canonical_resource()` returning `resource_server_audience` if set, else `f"{p.scheme}://{p.netloc}/mcp"` from `urlparse(protected_resource_metadata_url)` when it has a scheme+netloc, else `""`. Computed only from operator config captured in the closure — never from `request`. (S-10)
- `handle_protected_resource_metadata` (`http.py:472`) — modify — replace `"resource": str(request.url_for("mcp"))` with `"resource": _canonical_resource()`. (S-10)

## Documentation impact
- `CHANGELOG.md` — modify — append three bullets under the existing `### Security` heading in `## [Unreleased]` (the block already has `Added`/`Removed`/`Security`/`Fixed`/`Changed`), one per finding, each phrased `see #231` (never a closing keyword next to the number). Describe: (S-07) unknown-`kid` forced refreshes are cooldown-limited so an unauthenticated caller cannot amplify outbound JWKS fetches; (S-08) the JWKS fetch is bounded by a total timeout so a hung endpoint cannot stall the refresh lock / event loop; (S-10) the protected-resource-metadata `resource` is now the operator-configured canonical resource, never the request Host.
- `SECURITY.md` — modify — extend the existing (uncited) async-JWKS sentence at `SECURITY.md:40-42` to state the fetch is bounded by a total timeout and that repeated unknown-`kid` refreshes are cooldown-limited; you may add one sentence that the protected-resource-metadata `resource` is operator-configured and never Host-derived (consistent with the audience claim at `SECURITY.md:34-36`). **Do not** attach a `[C-NN]` citation unless you add a matching real test node to the ledger. After editing, run `scripts/check_security_claims.py` and confirm it still reports OK. If you choose to add a new ledger claim, cite the new acceptance-test node ids added below.

## Dependencies & order
Independent changes; any order. `auth.py` (S-07, S-08) and `http.py` (S-10) do not depend on each other. Doc edits last, after tests are green, so the CHANGELOG/SECURITY wording matches what shipped.

## Verification
Run from the worktree; a fresh worktree needs `uv sync -p 3.10` first or `uv run` silently uses system pytest. Subset runs must pass `--cov-fail-under=0`. Do not touch `tests/conftest.py`.

1. Add permanent acceptance tests (the temporary probes below were confirmed red on the current tree, then deleted):
   - S-07 → `tests/test_auth.py`: two `get_for_token` calls with two *distinct* unknown-`kid` tokens (build with `jwt.encode({}, "x", algorithm="HS256", headers={"kid": ...})`) against a `_fetch` monkeypatched to count calls; assert `calls <= 2`. Keep the existing `test_async_jwks_unknown_kid_forces_one_refresh` (`calls == 2` for a *single* unknown `kid`) — the fix preserves it because the first forced refresh is always allowed.
   - S-08 → `tests/test_auth.py`: `asyncio.start_server` on `127.0.0.1:0` with a handler that `await asyncio.sleep(30)`; build `AsyncJWKS(...)`, set `jwks._raw_url = "http://127.0.0.1:<port>/jwks"` (bypasses URL sanitization), assert `pytest.raises(ResourceServerJWKSUnavailable)` around `asyncio.wait_for(jwks._fetch(), timeout=8)` — the internal 5 s timeout must fire before the 8 s outer bound.
   - S-10 → `tests/test_transport_http.py`: reuse the `_make_app`/metadata pattern, GET `/.well-known/oauth-protected-resource` with `headers={"host": "evil.example"}`, assert `"evil.example" not in response.json()["resource"]`.
2. Targeted runs:
   ```bash
   uv run pytest tests/test_auth.py -k "jwks or fetch or kid" --cov-fail-under=0 -p no:cacheprovider -q
   uv run pytest tests/test_transport_http.py -k "metadata or resource or host" --cov-fail-under=0 -p no:cacheprovider -q
   ```
3. Full files (guards against regressions in the surrounding suites):
   ```bash
   uv run pytest tests/test_auth.py tests/test_transport_http.py --cov-fail-under=0 -p no:cacheprovider -q
   ```
4. `python3 scripts/check_security_claims.py` → expect `OK`.
5. `python3 scripts/check_plan_consistency.py plans/phase-plan-v13-*.md` → expect `blocking inconsistencies: 0` (do **not** edit `specs/phase-plans-v13.md` or any `plans/phase-plan-v13-*.md`).
6. Validate node ids before trusting a failing run: `uv run pytest <file> --collect-only -q | grep <name>`.

## Acceptance criteria
- [ ] **S-07** — proven by `uv run pytest tests/test_auth.py -k s07 --cov-fail-under=0 -p no:cacheprovider`. Measured on the current defective tree: `AssertionError: unknown kids each forced a fetch: calls=3` (two distinct unknown kids each forced a fetch). Post-fix mutation for the implementer: in the fixed `get_for_token`, delete the `self._last_forced_refresh = <now>` assignment (so the timestamp never advances and every unknown kid passes the cooldown gate) — re-run and confirm the test goes red with `calls=3`, verify the edit landed with `git diff --stat src/pmcp/auth.py`, then restore.
- [ ] **S-08** — proven by `uv run pytest tests/test_auth.py -k s08 --cov-fail-under=0 -p no:cacheprovider`. Measured on the current defective tree: the test hung on `_fetch` and `asyncio.wait_for` raised `asyncio.exceptions.TimeoutError` at the 8 s bound (run wall-time 8.20 s) — no internal timeout. Post-fix mutation: change the `ClientTimeout(total=self._fetch_timeout_seconds)` value to `total=60` (larger than the 8 s outer bound) — re-run and confirm it goes red with a `TimeoutError` again, verify with `git diff --stat src/pmcp/auth.py`, then restore.
- [ ] **S-10** — proven by `uv run pytest tests/test_transport_http.py -k s10 --cov-fail-under=0 -p no:cacheprovider`. Measured on the current defective tree: `AssertionError: metadata reflected attacker Host: resource='http://evil.example/mcp'`. Post-fix mutation: revert `handle_protected_resource_metadata`'s `resource` line to `str(request.url_for("mcp"))` — re-run with the forged `Host` and confirm `resource` again contains `evil.example`, verify with `git diff --stat src/pmcp/transport/http.py`, then restore.
- [ ] `scripts/check_security_claims.py` reports OK and `scripts/check_plan_consistency.py plans/phase-plan-v13-*.md` reports `blocking inconsistencies: 0` after the change.

## Execution Policy
- execute: effort=medium, reason=security-sensitive auth/concurrency code (JWKS refresh coalescing under a lock, trust-boundary metadata) — small edits but the failure direction is fail-open, so verify each mutation is red for the right reason.
