# Detailed plan: 1Password secret references for downstream servers (Consiliency/pmcp#487)

Status: PLAN revision 2, for panel review round 2. Revision 1 (fce5598) was reviewed
by Gemini, Astra, Claude and Grok (all CHANGES-REQUIRED); section 9 maps every
round-1 finding to the change below. Owner direction 2026-11-08 (not reopened):
resolve `op://` references inside pmcp, authenticating with a **1Password service
account scoped read-only to one gateway-only vault**, whose token lives in a
credential file, never in `pmcp.env` or the gateway environment. Implementation and
any release follow panel review; a release needs owner approval.

Baseline for citations: origin/main c79d6faff55ec78cee6d6768081912f26e65493d.

## 1. Problem (from the 2026-11-08 Bright Data failure)

1. A revoked key surfaced only at the first tool call: `connect_server` reported
   `auth_state: none`, `missing_env_vars: []`.
2. Keys live in plain text in `~/.config/pmcp/pmcp.env`, rendered from 1Password
   by the dotfiles bootstrap and also loaded into the gateway by systemd
   `EnvironmentFile=`.
3. Rotation needed a gateway restart. `restart_server` rebuilds the config
   (`tools/handlers.py:2701-2724`, `3146`), but `credential_value` answers from
   `os.environ` first (`env_store.py:914-915`). User-store values are placed in
   `os.environ` once at startup and never overwritten (`env_store.py:1149-1169`,
   `cli.py:3290-3302`), and systemd values win over them.
4. Children inherit the gateway environment minus pmcp-tracked keys
   (`sanitized_subprocess_env`, `env_store.py:1731-1780`); values exported by
   systemd or the shell (Bright Data's runtime name `API_TOKEN`) reach every child.
5. `gateway.auth_connect` writes the store and `os.environ` and records the key
   (`handlers.py:4886-4954`); it does not restart the server.

## 2. Goals and non-goals

Goals:
- G1. An operator-supplied credential value may be a 1Password secret reference.
  pmcp resolves it for each spawn and passes the value only to that child.
- G2. A restart re-resolves, so a rotated key (1Password or plain store) takes
  effect without restarting the gateway.
- G3. Resolved values and the service-account token never enter the gateway's
  `os.environ`, a child (other than the dedicated `op` process for the token),
  logs, diagnostics, errors or tool output.
- G4. Every failure is typed and fail-closed: no fallback to a stale value, an
  empty value, another lookup key, another store, or the literal reference.
- G5. A credential the provider rejects is reported as an auth state, on evidence
  specific to that provider.
- G6. A migration path that cannot break a running gateway.

Non-goals (v1): remote (HTTP) header references (refused, not ignored, see 3.2);
Windows production support; 1Password Connect; desktop-app integration; managing
the vault or token.

Security boundary: this protects against secrets being inherited, logged or
returned, and against repository/agent inputs choosing vault items. It is not a
sandbox against hostile processes running as the same user, which can read the
token file directly.

## 3. Design

### 3.1 Secret zero (the service-account token)

Sources, checked once at startup, **before** `load_startup_env` reads any dotenv
or store file:
1. systemd credentials: `$CREDENTIALS_DIRECTORY/op-service-account-token`, only
   when the process was started by systemd with that variable set;
2. `~/.config/pmcp/op-service-account-token` (POSIX).

No environment-variable source (removed: it contradicted the owner decision).
Neither path present: references are unsupported on this host; any reference is
`credential_unavailable` (reason `op_not_configured`). A path present but invalid
is a startup warning and every reference fails closed (reason `token_invalid`).

Reading (both paths), by one descriptor: open with `O_NOFOLLOW` (plus a confined
walk of the parent like `atomic_write`), `fstat` that descriptor, require a regular
file owned by the gateway's uid with no group/other permission bits (0400 and 0600
both pass; systemd's credential copy is read-only), read at most 4 KiB from that
descriptor, strip one trailing newline, require the token's documented prefix
shape. Never `read_env_text` (it follows symlinks and checks neither owner nor
mode). Re-read on demand when the file's identity (device, inode, size, mtime)
changes, so an atomic token replacement is picked up without a restart.

After reading: remove `CREDENTIALS_DIRECTORY` and any `OP_SERVICE_ACCOUNT_TOKEN`
from `os.environ`. Add `OP_SERVICE_ACCOUNT_TOKEN`, `CREDENTIALS_DIRECTORY`,
`OP_CONNECT_TOKEN`, `OP_CONNECT_HOST`, `OP_CONFIG_DIR`, `OP_CACHE`, `OP_ACCOUNT`
and `OP_SESSION_*` to the never-loaded set (`env_store.py:1006`), refuse them in
`pmcp secrets set/sync` and `gateway.auth_connect`, and strip them unconditionally
in `sanitized_subprocess_env`, `child_process_env` and `build_install_child_env`
(including any `own_env` override). The token is registered in the exact-value
redaction set (3.7) as soon as it is read.

Platforms: POSIX descriptor checks only. macOS uses path 2. Windows: references
are unsupported (`credential_unavailable`, reason `platform_unsupported`) until an
ACL check exists.

### 3.2 Where references are honoured (provenance, not shape)

A reference is honoured only if it comes from an **operator-controlled source**:
- the user store `~/.config/pmcp/pmcp.env`;
- the user-scope config files (`~/.mcp.json`, `~/.claude/.mcp.json`) and configs
  named by `-c`/`PMCP_CONFIG`;
- the shipped manifest and the operator manifest overlay under `~/.pmcp/`.

Not honoured, ever: project `.env`, project `.env.pmcp`, tenant stores, project
`.mcp.json`, project manifest overlays, discovered servers, tool arguments. There
is no opt-in in v1 (revision 1's `allow_project_references` is dropped). A
reference-shaped value from those sources is refused (`credential_unavailable`,
reason `reference_not_allowed_here`) and never forwarded as a literal.

To enforce this, credential lookup and config merging carry provenance: the
lookup returns a small record `(value, source_kind, root)` instead of a bare
string, the config merge keeps a per-env-key source, and `ResolvedServerConfig`
carries a `credential_sources` map. Spawn sites consult that map; shape alone is
never enough.

Write paths:
- `gateway.auth_connect` refuses reference-shaped credentials (agents must not be
  able to point the gateway at vault items). Operators write references with the
  CLI only.
- `pmcp secrets set NAME op://...` is allowed only into the user store, only for
  names that `repository_may_supply()` accepts (`env_store.py:731-778`; excludes
  the `PMCP_` namespace, code-loading and package-manager names).
- `pmcp secrets sync` refuses to copy reference-shaped values in either
  direction.
- pmcp's own variables are never references: at startup, a reference-shaped
  value for any name pmcp reads itself (`is_pmcp_environment_name`, e.g.
  `PMCP_AUTH_TOKEN`) is refused with a value-free error; for `PMCP_AUTH_TOKEN`
  the gateway refuses to start.

Remote servers: a reference-shaped value reaching a remote header lookup is
refused with `credential_unavailable` (reason `remote_reference_unsupported`),
never sent. Migration preflight lists remote consumers of each key and refuses
to migrate a key a remote server uses.

### 3.3 Reference grammar (v1 subset)

Three classes, decided by `re.fullmatch` on the whole value after rejecting any
control, NUL, bidi or whitespace character:
- **plain**: does not start with `op://` (case-insensitive). Unchanged behaviour.
- **supported reference**: `op://<vault>/<item>/<field>` or
  `op://<vault>/<item>/<section>/<field>`. Each segment is 1-64 characters from
  `[A-Za-z0-9 _.-]` (space allowed inside, not leading or trailing), not `.` or
  `..`. No query, fragment, `%`, `@`, `:`, `$` or `\`. Vault and item may be names
  or IDs; IDs are recommended (1 read instead of 3, see 3.9).
- **reference-shaped but unsupported**: starts with `op://` and is not
  supported (queries such as `?attribute=otp`, `?ssh-format=`, file content,
  `$VAR` expansion, extra segments). Refused, `credential_unavailable`, reason
  `reference_unsupported`. Never resolved, never forwarded.

No recursive resolution: a resolved value that itself looks like a reference is
used as a literal secret only if it is plain; otherwise refused.

### 3.4 Resolution and the `op` process

New module `pmcp/secret_refs.py`:
- Executable: an absolute path chosen by the operator config (`op_path` in the
  user config) or, by default, the first of `~/.local/bin/op`, `/usr/local/bin/op`,
  `/usr/bin/op` that is a regular executable owned by root or the gateway uid and
  not writable by group/other. Never a `PATH` search, never the project cwd.
- Argv: `op read --no-newline --no-color --cache=false --config <dir> -- <ref>`.
  The 2.42.0 flags were checked with `op --help`/`op read --help`; the
  implementation PR records a live compatibility check (3.10).
- Environment: a new builder `op_subprocess_env()`, registered in `ENV_BUILDERS`
  (`tests/test_store_reader_inventory.py:650-653`): `PATH` (fixed system dirs),
  `HOME`, `OP_SERVICE_ACCOUNT_TOKEN`, `OP_CACHE=false`, and the proxy/TLS variables
  `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, `SSL_CERT_DIR` only when
  operator-supplied. Nothing else.
- Process: cwd and `--config` dir are a private 0700 directory under the user
  state dir (owner/mode checked); stdin is `/dev/null`; started through
  `spawn_owned`; stdout and stderr read concurrently with bounded readers (64 KiB
  stdout, 8 KiB stderr). Timeout (10 s), cap overflow, cancellation or shutdown:
  kill the process group, reap, discard every byte read, fail. Non-zero exit or
  empty output fails. Stdout is never truncated into a value.
- Failure classes from exit status and stderr: `auth_failed`, `not_found`,
  `rate_limited`, `network`, `timeout`, `op_missing`, `output_invalid`. Raw
  stderr and argv are never echoed; the class is.
- Concurrency: at most 2 `op` processes at once (below the spawn cap of 8,
  `cli.py:2520`); identical in-flight references are single-flighted. No cache
  across spawns (each spawn reads current values; G2).
- Values shorter than 8 characters are refused (`output_invalid`), so exact-value
  redaction (3.7) never has to match common short strings.

### 3.5 Where resolution happens

- Configs keep the reference string; a resolved value is never written onto
  `ResolvedServerConfig`, `config.env`, `os.environ` or any store.
- Resolution runs **before** an existing instance is torn down and **outside**
  the manager-wide lifecycle lock (`manager.py:1625`): the caller resolves, then
  passes a per-spawn env copy down. A failed lookup during `restart_server`
  leaves the running instance as it is and returns the typed error (no new spawn
  uses an old value, so this is not a fallback).
- Every credential-injecting spawn path uses it: `ClientManager._connect_stdio`
  (`manager.py:3244`), reconnect (`_reconnect_loop`, `manager.py:3992-4066`,
  resolves again from the stored reference), the install-and-run provision path
  (`installer.py:242-252, 451-461, 722-765`, which Bright Data uses), installer
  verification, and `update_server` (`handlers.py:5368`).
- `_connect_with_retry` does not retry typed lookup failures; total resolution
  time per spawn is bounded (20 s).

### 3.6 Rotation of plain store values (P1)

- At startup, record an origin snapshot: for each credential name, whether its
  `os.environ` value came from the external environment (present before pmcp
  ran), the user store (inserted by `_load_operator_text`), or
  `gateway.auth_connect` (inserted later).
- Precedence is unchanged: external environment > user store > project files.
- For names whose winning value came from the user store, `credential_value`
  re-reads the user store with the strict reader (`_read_env_file_strict`,
  `env_store.py:1306-1352`), identity-cached like the project files
  (`env_store.py:385-390`). Present: return it. Removed from the store: the name
  is unavailable (`missing_auth`), not the startup copy. Read failure:
  `credential_unavailable` (reason `store_unreadable`), not the startup copy.
  Explicit empty values keep their current meaning (`env_store.py:899-901`).
- Names last written by `gateway.auth_connect` keep the `os.environ` value it
  wrote until the next process start (its documented, existing behaviour).
- Lookup key order is unchanged (`secret_key` then `env_var`,
  `manifest/loader.py:313-329`), but a typed failure on the preferred key stops
  the lookup; it never falls through to the legacy key or another store.
- Tests: `BRIGHTDATA_API_TOKEN` vs legacy `API_TOKEN`; store edit in place and
  by atomic replace; removal; unreadable store; external env still wins;
  project-scope `auth_connect` still wins in-process.

### 3.7 Redaction

- A process-local registry holds the token and every resolved value, registered
  before any child is spawned, kept for the lifetime of the process (values are
  few; old values stay protected across rotation and in-flight calls).
- Exact-value redaction is **unconditional** (independent of
  `options.redact_secrets`) and runs **before** truncation and serialization in:
  `PolicyManager.process_output` including `summary` (`policy.py:974-995`),
  `resources_read`, `prompts_get`, `tasks_result`, `_sanitize_error`,
  `sanitize_auth_diagnostic`, installer job output, child stderr logging
  (`manager.py:3764`), and a `logging.Filter` on every gateway log handler
  (including the rotating file log, `cli.py:110-117`).
- Each value is matched raw and in its JSON-string-escaped form; overlapping
  values are matched longest first. Transformed forms (base64, split) are out of
  scope and documented.

### 3.8 Auth states

- `credential_unavailable` (new): a lookup or policy failure from 3.1-3.6, with a
  reason class. The server is not (re)started.
- `credential_rejected` (new, P3): set only by (a) a shipped-manifest `auth_check`
  returning the provider's documented authentication failure, or (b) a
  provider-specific classifier with a named, documented error signature for that
  provider. No generic substring heuristic; a bare 401/403 or "unauthorized" is
  never enough. Cleared by a successful check, a successful tool call, or a
  restart with a new value. Precedence: `credential_unavailable` >
  `credential_rejected` > `insufficient_scope` > `none`.
- `auth_check` is a shipped-manifest field only (refused from overlays, project
  configs, discovered servers and tool arguments). Form: HTTPS GET to a pinned
  host and path, credential in a header (never the URL), no redirects, TLS
  verified, 5 s timeout, 4 KiB response cap. Only the documented auth-failure
  status sets `credential_rejected`; timeouts, 5xx and 429 leave the state
  unchanged. Bright Data's check (`GET https://api.brightdata.com/zone/get_active_zones`
  with `Authorization: Bearer`, 401 on a bad key, as observed on 2026-11-08) is
  verified live before P3 merges.
- `AuthState`, `AuthEventKind`, `DEFAULT_AUTH_STATE_SEMANTICS` and
  `_auth_event_for_state` (`types.py:34-92`, `handlers.py:1104-1111`) are
  extended in the PR that first returns each state.

### 3.9 Rate limits

1Password service-account limits are per token per hour and per account per day
(official rate-limit docs); a name-based reference costs about 3 reads, an
ID-based one 1. Expected volume: one read per reference per spawn, restart or
reconnect. Mitigations: ID-based references recommended by `doctor` and
`migrate-op`; concurrency cap and single-flight (3.4); reconnect backoff already
bounds retries; `rate_limited` is `credential_unavailable` with no stale fallback;
`doctor` resolves references only with `--resolve`.

### 3.10 Migration and operator commands

- `pmcp secrets migrate-op --map NAME=op://... [...] (--check | --backup)`:
  refuses unless the running gateway version supports references (checked via
  the gateway's health/version endpoint) and the token is configured; resolves
  every reference first and compares with the current value (match/mismatch,
  never printing either); refuses all changes on any failure or mismatch unless
  `--accept-mismatch NAME`; refuses keys used by remote servers (3.2); rewrites
  the user store atomically with a compare-and-swap on the file identity it read
  (a concurrent rotation aborts the migration); `--backup` writes a 0600 copy
  next to the store first. One of `--check` or `--backup` is required.
- `pmcp doctor`: per server, credential source (plain / reference / external
  environment), reference class, and plain keys that could be migrated; warns
  when a manifest runtime `env_var` (e.g. `API_TOKEN`) is set in `os.environ` by
  something other than pmcp (it leaks to every child). `--resolve` performs
  lookups.
- A live compatibility check script (operator-run, value-free output) confirms
  the installed `op` honours the flags in 3.4 and that `--cache=false` reads the
  current value after an item edit.

## 4. Phases and PRs

| Phase | Content | Repo | Safe alone? |
|---|---|---|---|
| P1 | Origin snapshot and plain-store rotation (3.6); `doctor` env-leak warning; `credential_unavailable` state for `store_unreadable`. | pmcp | Yes: no references. |
| P2 | Token loading and stripping (3.1), provenance (3.2), grammar (3.3), `op` resolver (3.4), all spawn paths (3.5), unconditional redaction (3.7), refusal paths. Requires P1. | pmcp | Yes: references are honoured only when the operator writes them and installs a token. |
| P3 | `credential_rejected`, `auth_check`, Bright Data check (3.8). | pmcp | Yes. |
| P4 | `migrate-op`, `doctor --resolve`, compatibility script (3.10). | pmcp | Yes: refuses on unsupported gateways. |
| P5 | Release pmcp (owner approval); upgrade pmcp on the target hosts. | pmcp | Gate for P6. |
| P6a | Dotfiles: install the token with `systemd-creds`-style credstore path `%h/.config/credstore/op-service-account-token` and `LoadCredential=op-service-account-token` (no `:PATH`, so an absent file does not fail the unit); stop `EnvironmentFile=` for `pmcp.env`. | dotfiles | Only after P5 on that host. |
| P6b | Dotfiles: bootstrap writes references instead of values, only when the installed pmcp supports them. Canary Display-Server-01, then the fleet; retire `pmcp.env.bak` after evidence. | dotfiles | Only after P6a. |

Rollback: P6b can be reverted by restoring the backup store; P6a by restoring the
unit; pmcp releases keep reading plain values.

Each PR gets the cross-vendor panel and exact-head CI before merge.

## 5. Owner steps

1. Create the "PMCP Gateway" vault; copy the gateway keys into it, including the
   **new** Bright Data key (the `Consiliency Deploy Secrets/BrightData` item may
   still hold the revoked one). Prefer recording item IDs.
2. Create a service account with read-only access to that vault only; note the
   account tier (rate limits).
3. Approve the release (P5) and the host cutover order (P6).

## 6. Security properties to verify

- Token: read by descriptor with owner/mode checks; never in `os.environ`, a
  downstream child, a store, logs or output; only the dedicated `op` process
  receives it; redacted if it ever appears.
- References: provenance-gated; agents and repositories cannot choose vault
  items; pmcp's own variables can never be references.
- `op`: trusted absolute path, minimal env, no shell, no stdin, bounded time and
  output, killed and reaped on any abnormal end, cache off.
- Every failure is typed and fail-closed (G4).
- Known limits: same-user processes can read the token file; transformed
  secrets are not redacted; Windows unsupported.

## 7. Verification

Fake `op` executables on a controlled path (record argv/env, return fixed output,
hang, overflow, fail with each stderr class). CI never calls 1Password. Tests per
phase include: precedence and rotation cases (3.6); strict-read failure does not
return the startup copy; provenance refusals for each untrusted source;
`auth_connect`/`sync` refusals; `PMCP_AUTH_TOKEN` reference refusal; grammar
classes; token absent from child env and `os.environ`, `CREDENTIALS_DIRECTORY`
stripped; `op` timeout/overflow kill and discard; concurrency cap; resolution
outside the lock and before teardown; reconnect re-resolves; install-and-run path;
redaction before truncation, JSON-escaped forms, log filter, summary; new auth
states in every output schema; `credential_rejected` false-positive fixtures.

```bash
# dev0: affected files only, at most -n 2, after the AGENTS.md headroom check.
uv run --locked python scripts/pytest_temp.py -- tests/test_secret_refs.py tests/test_env_store.py -q
# Full suite: on ai under systemd-run MemoryMax, or hosted CI (AGENTS.md),
# with isolated HOME and evidence outside scratch (plans/verification-template.md).
```

Live check on Display-Server-01 after P6b: Bright Data through a reference;
rotate the item; `restart_server`; the new key is used without a gateway restart;
a revoked key gives `credential_rejected`.

## 8. Open questions for the panel

- Is refusing references in remote headers (rather than resolving them) the right
  v1 boundary?
- Is the default `op` executable search list acceptable, or should `op_path` be
  required?

## 9. Round-1 findings and their disposition

| Finding (seat) | Change |
|---|---|
| Rotation keyed on membership, not origin; auth_connect misdescribed (Astra 1, Grok B1, Gemini B6, Claude B7, N9) | 3.6 origin snapshot, strict re-read, no stale fallback, auth_connect semantics stated; 1.5 corrected |
| Env-var token source contradicts the decision (Astra 2, Claude B6) | Removed (3.1) |
| Token-file read checks (Astra 2, Grok B3, Gemini B4, Claude N11) | Descriptor read, owner/mode, 0400 allowed, identity reload (3.1) |
| `CREDENTIALS_DIRECTORY` leak; store files planting `OP_*` (Gemini B1) | Stripped and never-loaded (3.1) |
| Project/tenant provenance unenforceable; opt-in conflict (Astra 3, Grok B2, Claude B1) | Provenance carried to spawn; untrusted sources refused; no opt-in (3.2) |
| Agent and sync write paths as confused deputy (Claude B2, Gemini B2) | `auth_connect` and `sync` refuse references (3.2) |
| pmcp's own variables (Claude B5) | Refused; `PMCP_AUTH_TOKEN` stops startup (3.2) |
| Malformed/unsupported references passed through; remote headers (Astra 4, Grok B4, Claude B4, Gemini NB1) | Three-class grammar, refusal, remote refusal (3.2, 3.3) |
| `op` executable, lifecycle, caching, daemon (Astra 5, Gemini B5, Claude N4) | Trusted path, `--cache=false`, bounded readers, kill/reap (3.4) |
| Install-and-run provision path, reconnect, update_server (Gemini B5, Claude B3) | All spawn paths (3.5) |
| Restart outage and global lock (Claude N2) | Resolve before teardown, outside the lock (3.5) |
| Redaction off by default, after truncation, escaped forms, sinks (Gemini B3, Astra 8, Claude N6) | Unconditional, before truncation, escaped forms, log filter (3.7) |
| `credential_rejected` heuristic false positives; auth_check exfiltration (Astra 6, Grok B5, Gemini B7-B8, Claude B8, N7) | Provider-specific evidence only; shipped-manifest pinned check (3.8) |
| Phasing and release order; LoadCredential absent file (Astra 7, Grok B6, Gemini B9, Claude N10) | P5 release before P6 dotfiles; credstore without `:PATH`; migrate-op refuses unsupported gateways (3.10, 4) |
| Windows/macOS (Grok N3, Gemini B10) | POSIX only; Windows unsupported in v1 (3.1) |
| Rate limits (all seats) | 3.9 |
| Env-builder inventory test (Grok N1) | `op_subprocess_env` in `ENV_BUILDERS` (3.4) |
| Doctor leak check uses runtime name (Grok N2) | 3.10 |
| Short values vs `otp` (Gemini B3) | Queries unsupported in v1; <8 chars refused (3.3, 3.4) |
