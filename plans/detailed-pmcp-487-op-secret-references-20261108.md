# Detailed plan: 1Password secret references for downstream servers (Consiliency/pmcp#487)

Status: PLAN, for panel review before implementation. Owner direction 2026-11-08:
resolve `op://` references inside pmcp with a **1Password service account scoped
read-only to one gateway-only vault**. Implementation and any release follow the
usual panel review; a release needs owner approval.

## 1. Problem (from the 2026-11-08 Bright Data failure)

1. A revoked key surfaced only at the first tool call: `connect_server` reported
   `auth_state: none`, `missing_env_vars: []`.
2. Keys live in plain text in `~/.config/pmcp/pmcp.env`, rendered from 1Password
   by the dotfiles bootstrap (`op read`) and loaded into the gateway process by
   systemd `EnvironmentFile=`.
3. Rotation needed a gateway restart. `restart_server` rebuilds the config
   (`tools/handlers.py:2701-2724`, `3146`), but `credential_value` reads
   `os.environ` first (`env_store.py:914-915`), and user-store values are put
   into `os.environ` once at startup with "never override" semantics
   (`env_store.py:1149-1169`; `cli.py:3290-3302`).
4. Every child inherits the whole gateway environment minus pmcp-tracked keys
   (`sanitized_subprocess_env`, `env_store.py:1731-1780`). Values exported by
   systemd or the shell, such as the generic `API_TOKEN`, reach every child.

## 2. Goals and non-goals

Goals:
- G1. A credential value may be a 1Password secret reference
  (`op://<vault>/<item>/[<section>/]<field>`). pmcp resolves it when the server
  is spawned and passes the result only to that child.
- G2. `restart_server` re-resolves (references and plain store values), so a
  rotated key takes effect without restarting the gateway.
- G3. Resolved values never enter the gateway's `os.environ`, logs, diagnostics,
  tool output or error text.
- G4. A failed lookup is a clear, typed auth error. pmcp never falls back to a
  stale or empty value.
- G5. A credential the downstream service rejects is reported as an auth state.
- G6. A migration path for existing plain-text keys, with an owner-run cutover.

Non-goals:
- Remote (HTTP) server headers in the first slice; covered in a follow-up only if
  needed (the same resolver applies).
- 1Password Connect, the desktop app integration, or interactive `op signin`.
- Managing the vault, the service account or its rotation (owner/1Password side).

## 3. Design

### 3.1 Secret zero

- A 1Password service account with read-only access to one vault (proposed name
  "PMCP Gateway") holding only gateway keys. The owner creates it.
- Its token is read from, in order:
  1. `$CREDENTIALS_DIRECTORY/op-service-account-token` (systemd `LoadCredential=`);
  2. `~/.config/pmcp/op-service-account-token`, which must be a regular file owned
     by the user with mode 0600 or stricter (checked by descriptor, as the store
     reader does);
  3. `OP_SERVICE_ACCOUNT_TOKEN` in the environment, for development only, and
     then **removed from `os.environ` at startup** so no child inherits it.
- The token is held in memory by the resolver only. It is never written to a
  store file, never put in any child environment, and never logged.

### 3.2 Where references may appear

Any credential value pmcp resolves for a stdio server:
- the user store `~/.config/pmcp/pmcp.env`, project `.env.pmcp`, tenant stores;
- a concrete value in a `.mcp.json` env block.

A value is a reference only if the WHOLE value matches the 1Password secret
reference grammar (`op://` prefix, vault/item/field segments, optional section,
optional `?attribute=` query). Anything else is passed through unchanged (no
partial interpolation, no `${...}` templating).

### 3.3 Resolution

- New module `pmcp/secret_refs.py` with `resolve_reference(ref, *, timeout)`.
- It runs `op read --no-newline <ref>` via `create_subprocess_exec` (no shell)
  with a **minimal allowlisted environment**: `PATH`, `HOME`, a private
  `OP_CONFIG_DIR` under the user state dir, and `OP_SERVICE_ACCOUNT_TOKEN`. Not
  the gateway environment.
- Bounded: 15 s timeout, output capped (64 KiB), non-zero exit or empty output
  is a failure. stderr is classified (auth failed, item not found, network,
  rate limit) and never echoed raw.
- Called from the stdio spawn path (`manager.py` `_connect_stdio`, around
  `3244-3247`) after config resolution, for each env value that is a reference.
  The resolved values are applied to the child's env dict only.
- No persistent cache. An optional in-memory cache keyed by reference with a
  short TTL (default 0 = off) may be added if `op` latency is a problem; the
  cache is cleared on `restart_server` for that server.

### 3.4 Rotation without a gateway restart

- Plain store values: keys sourced from the user store are tracked by origin
  already (`operator_managed_secret_keys`). For those keys `credential_value`
  re-reads the store (descriptor-identity cached like the project files,
  `env_store.py:385-390`) instead of trusting the startup copy in `os.environ`.
- References: resolved at every spawn, so `restart_server` always sees the
  current 1Password value.
- `gateway.auth_connect` keeps working (it writes the store, then restarts).

### 3.5 Child environment hygiene

- Keep `sanitized_subprocess_env` stripping store-sourced keys.
- The dotfiles unit stops loading `pmcp.env` with `EnvironmentFile=` (Phase 4),
  so store keys never reach the gateway's environment at all. pmcp already loads
  the user store itself.
- `doctor` warns when a credential name used by a manifest server is present in
  the process environment from outside pmcp (shell/systemd), because every child
  inherits it.

### 3.6 Errors and auth states

- Lookup failure: the server is not started. `connect_server` returns a typed
  error with `auth_state: "credential_unavailable"` (new) and an event
  `credential_lookup_failed` naming the env var and the failure class (auth,
  not found, timeout, `op` missing), never the reference's secret or raw stderr.
  The reference path itself (vault/item/field names) is not secret but is shown
  only in `doctor`, not in tool output.
- Rejected credential: new `auth_state: "credential_rejected"` and event
  `credential_rejected`, set when
  - a manifest server declares an optional `auth_check` (a cheap read-only tool
    call or HTTPS endpoint with the credential) that fails with an auth status
    at connect, or
  - a tool call on a server with `requires_api_key` fails with a recognised
    authentication error (HTTP 401/403 in the error, or provider messages such
    as "invalid token", "sign-in failed", "unauthorized"). Classification only
    upgrades `auth_state`; the tool error is still returned.
- `missing_auth` keeps its current meaning (no value at all).

### 3.7 Redaction

- Every resolved value is registered in a process-local set of known secrets.
  Logs, diagnostics, `_sanitize_error`, child stderr logging (`manager.py:3764`)
  and tool output pass through exact-value redaction for that set, in addition
  to the existing pattern redaction.
- Values shorter than 8 characters are not registered (to avoid redacting
  common words); such values are refused at resolution with a clear error.

### 3.8 Migration

- `pmcp secrets set NAME op://...` stores a reference like any value (no change).
- New `pmcp secrets migrate-op --map NAME=op://... [--map ...] [--check]`:
  resolves each reference with the service account, compares it with the
  current plain value (reports match/mismatch without printing either),
  and replaces the plain value with the reference. It writes a timestamped
  0600 backup only when `--backup` is given. `--check` changes nothing.
- `pmcp doctor` reports, per server: credential source (plain / reference /
  environment), whether a reference resolves, and plain-text keys that could be
  migrated.

## 4. Phases and PRs

| Phase | PR content | Repo |
|---|---|---|
| P1 | Rotation fix (3.4 plain values) + `doctor` env-leak warning (3.5). Independent bug fix. | pmcp |
| P2 | `secret_refs` resolver, secret-zero loading, spawn integration, `credential_unavailable`, exact-value redaction (3.1-3.3, 3.6 first half, 3.7). | pmcp |
| P3 | `credential_rejected` classification + optional manifest `auth_check`; Bright Data check (3.6 second half). | pmcp |
| P4 | `secrets migrate-op` + `doctor` reporting (3.8). | pmcp |
| P5 | Dotfiles: `LoadCredential=` for the token, drop `EnvironmentFile=` for `pmcp.env`, bootstrap writes references instead of values, per-host token install. | ViperJuice/dotfiles |
| P6 | Release (owner approval), fleet cutover on Display-Server-01 first, then others; retire `pmcp.env.bak`. | both |

Each PR gets the cross-vendor panel and exact-head CI before merge.

## 5. Owner steps (cannot be automated by agents)

1. Create the "PMCP Gateway" vault and copy the gateway keys into it, including the
   **new** Bright Data key (the `Consiliency Deploy Secrets/BrightData` item may
   still hold the revoked one).
2. Create a service account with read-only access to that vault only, and place its
   token on each gateway host (P5 provides the install command).
3. Approve the release and the per-host cutover order.

## 6. Security review points for the panel

- Token never reaches a child, the gateway `os.environ`, logs or tool output.
- `op` runs with an allowlisted environment, no shell, bounded time and output.
- Reference detection is whole-value only; a value that merely contains `op://`
  is not resolved.
- Failure modes never fall back to a stale or empty value.
- A malicious project `.env.pmcp` could reference arbitrary items in the vault
  the token can read. Mitigation: references are honoured only from the user
  store and `.mcp.json` configs the operator controls by default; project and
  tenant stores need an explicit opt-in (`allow_project_references: true` in the
  user config). Panel to confirm.
- Exact-value redaction cannot catch transformed secrets (encoded, split).

## 7. Verification (per phase)

Unit tests with a fake `op` executable on `PATH` (records its argv and env,
returns fixed output or failures): reference grammar, allowlisted env, token
never in child env, timeout, output cap, failure classes, no fallback,
re-resolution on restart, redaction of the resolved value in logs, errors and
tool output, and the rotation fix for plain store values. No real 1Password
calls in CI.

```bash
# dev0: affected files only, at most -n 2, after the AGENTS.md headroom check.
uv run --locked python scripts/pytest_temp.py -- tests/test_secret_refs.py tests/test_env_store.py -q
# Full suite: on ai under systemd-run MemoryMax, or hosted CI (AGENTS.md).
```

A live check on Display-Server-01 after release: connect brightdata through a
reference, rotate the 1Password item, `restart_server`, confirm the new key is
used without a gateway restart, and confirm `credential_rejected` with a revoked
key.
