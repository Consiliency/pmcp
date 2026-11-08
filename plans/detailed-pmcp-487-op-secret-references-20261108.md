# Detailed plan: 1Password secret references for downstream servers (Consiliency/pmcp#487)

Status: PLAN revision 4, for panel review round 4. Revisions 1 (fce5598), 2
(17b25d9) and 3 (a8ced55) were reviewed by Gemini, Astra, Claude and Grok; section
10 maps every finding to the change below. Owner direction 2026-11-08 (not
reopened): resolve `op://` references inside pmcp, authenticating with a
**1Password service account scoped read-only to one gateway-only vault**, whose
token lives in a credential file, never in `pmcp.env` or the gateway environment.
Implementation and any release follow panel review; a release needs owner
approval.

Baseline for citations: origin/main c79d6faff55ec78cee6d6768081912f26e65493d.

## 1. Problem (from the 2026-11-08 Bright Data failure)

1. A revoked key surfaced only at the first tool call: `connect_server` reported
   `auth_state: none`, `missing_env_vars: []`.
2. Keys live in plain text in `~/.config/pmcp/pmcp.env`, rendered from 1Password
   by the dotfiles bootstrap. On the reporting host systemd also loads that file
   into the gateway's environment with `EnvironmentFile=`; two other launchers do
   the same with `set -a; source` (`hosts/workstation-mac/com.user.pmcp.plist`,
   `claude-config/pmcp-service.sh` in ViperJuice/dotfiles).
3. Rotation needed a gateway restart. `restart_server` rebuilds the config
   (`tools/handlers.py:2701-2724`, `3146`), but `credential_value` answers from
   `os.environ` first (`env_store.py:914-915`). Values loaded by systemd sit in
   `os.environ` from the start; values pmcp loads from the store are inserted once
   and never overwritten (`env_store.py:1149-1169`; `cli.py:3290-3302`), and an
   availability check can insert more later (`handlers.py:2792-2794`).
4. Values that reach the gateway's environment from outside (shell, systemd) are
   inherited by every child unless they are also user-store names:
   `sanitized_subprocess_env` strips user-store names and keys pmcp itself loaded
   (`env_store.py:1731-1780`, `1673-1679`). `API_TOKEN` is a store name on the fleet,
   so it is stripped; a generic name set only in the launcher's environment is not.
5. `gateway.auth_connect` writes the store and `os.environ` and records the key
   (`handlers.py:4886-4954`); it does not restart the server.

## 2. Goals and non-goals

Goals:
- G1. An operator-supplied credential value may be a 1Password secret reference.
  pmcp resolves it for each spawn and passes the value only to that child.
- G2. A restart re-resolves, so a rotated key (1Password or plain store) takes
  effect without restarting the gateway.
- G3. A resolved value reaches only the child it was resolved for; the
  gateway-owned service-account token reaches only the dedicated `op` process.
  Neither enters the gateway's `os.environ`, another child, logs, diagnostics,
  errors or tool output. (A child's own explicit token, 3.2.4, is separate.)
- G4. Every failure is typed and fail-closed: a lookup or resolution failure
  never falls back to a stale value, an empty value, another lookup key, another
  store, or the literal reference. (An absent key is not a failure; 3.6.)
- G5. A credential the provider rejects is reported as an auth state, on evidence
  specific to that provider.
- G6. No existing working configuration breaks on upgrade; migration cannot break
  a running gateway.

Non-goals (v1): resolving references in remote (HTTP) headers (refused, 3.2);
Windows support; 1Password Connect; desktop-app integration; managing the vault or
token.

Security boundary: this protects against secrets being inherited, logged or
returned, and against repository/agent inputs choosing vault items. It is not a
sandbox against hostile processes running as the same user, which can read the
token file directly.

## 3. Design

### 3.1 Secret zero (the service-account token)

Sources, looked up **on demand** (each time a resolution needs the token), in order:
1. the systemd credential copy `$CREDENTIALS_DIRECTORY/op-service-account-token`.
   `CREDENTIALS_DIRECTORY` is captured once, at startup, before `load_startup_env`
   reads any file, then removed from `os.environ`. systemd copies the credential
   when the unit starts, so rotating a token delivered this way needs a unit
   restart (documented);
2. the per-user credstore file `$XDG_CONFIG_HOME/credstore/op-service-account-token`
   (default `~/.config/credstore/...`, using the pinned home from
   `home_identity`; `XDG_CONFIG_HOME` only if operator-supplied). This is the file
   systemd loads for a user unit, so CLI commands (`migrate-op`,
   `doctor --resolve`) read the same token as the gateway;
3. `~/.config/pmcp/op-service-account-token` (for hosts without systemd, e.g. macOS).

No environment-variable source. A path that does not exist (including path 1
when `CREDENTIALS_DIRECTORY` exists for some other credential) is "not present"
and the next path is tried. None present: any reference pmcp would resolve is
`credential_unavailable` (reason `op_not_configured`). A file that exists but fails
the descriptor, size or prefix check (`ops_`) is `token_invalid`: resolution fails
closed and pmcp does not try the next path. `doctor` prints the paths it searched
and, when both path 1 and path 2 exist, whether their digests differ (never the
values), so an edited credstore file without a unit restart is visible. A
credstore file that is a symlink is refused by the confined walk (systemd would
follow it): documented.

Reading each path: confined walk of the parent directories (as `atomic_write`
does), then open the leaf with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC`,
`fstat` that descriptor, require a regular file owned by the gateway's uid with
no group/other bits (0400 and 0600 both pass), read at most 4 KiB from that
descriptor, strip one trailing `\n` or `\r\n`, require the documented token prefix
shape. A FIFO or device is refused without blocking. Cached by
`(st_dev, st_ino, st_size, st_mtime_ns)` of the descriptor; a change re-reads.

Never-loaded and stripped names: `OP_SERVICE_ACCOUNT_TOKEN`, `CREDENTIALS_DIRECTORY`,
`OP_CONNECT_TOKEN`, `OP_CONNECT_HOST`, `OP_CONFIG_DIR`, `OP_CACHE`, `OP_ACCOUNT`,
`OP_SESSION_*` are added to `_NEVER_LOADED` (`env_store.py:1146`), refused by
`pmcp secrets set/sync` and `gateway.auth_connect`, removed from `os.environ` at
startup, and stripped from **inherited** environment in `sanitized_subprocess_env`,
`child_process_env` and `build_install_child_env`. A server's own explicit env
(`own_env`) is governed by 3.2.4 instead. The token is registered for exact-value
redaction (3.7) as soon as it is read.

Platforms: POSIX descriptor checks only. Windows: `platform_unsupported`.

### 3.2 Where pmcp resolves references

#### 3.2.1 What pmcp resolves

pmcp resolves a reference in exactly two places:
- **Credential lookups**: the value found for a server's `secret_key`/`env_var`
  (`manifest/loader.py:313-329`, injected at `config/loader.py:1532-1543` and
  `1110-1129`, `installer.py:759`).
- **Opted-in own env**: values in a server's explicit `env` block, only for a
  server whose operator config sets `resolve_secret_refs: true` (3.2.4).

Any other `op://` value is left exactly as today (3.2.4). This keeps existing
configurations working (G6).

#### 3.2.2 Trusted sources

A reference found by a credential lookup, or in an opted-in `env` block, is
resolved only if its source is one of:
- the user store `~/.config/pmcp/pmcp.env`;
- user-scope config files (`~/.mcp.json`, `~/.claude/.mcp.json`) and configs
  named by `-c`/`PMCP_CONFIG` (operator-supplied, `env_key_is_operator_supplied`);
- the shipped manifest, the operator overlay `~/.pmcp/manifest.yaml`, and
  `$PMCP_MANIFEST_PATH` when operator-supplied.

Refused, with `credential_unavailable` (reason `reference_not_allowed_here`), never
resolved and never forwarded: the **process environment, whoever set it** (pmcp
cannot tell who did; an `EnvironmentFile=`/`source` launcher is the common case),
`~/.env`, project `.env` and `.env.pmcp`, tenant stores, project `.mcp.json`,
project manifest overlays, discovered servers, tool arguments, and values written
by `gateway.auth_connect`. `doctor` lists such keys by name and source.

#### 3.2.3 Provenance

Credential lookup returns `(value, source_kind, root)`. Config merging keeps a
per-env-key source. `Manifest.ServerConfig` gains `extra_env_sources`, updated per
key wherever `_build_manifest` merges an overlay (`manifest/loader.py:2112`,
`2170-2188`); `_merge_manifest_defaults`, `_manifest_server_to_config` and
`build_install_child_env` carry it. `ResolvedServerConfig` carries a
`credential_sources` map. Spawn sites decide from that map; shape alone never
decides.

#### 3.2.4 Existing child-side resolution

Some servers resolve their own references today: `op run` wrappers, or a server
given its own `OP_SERVICE_ACCOUNT_TOKEN` (the agent-board pattern,
`plans/agent-board-overlay.template.jsonc`). Local `env` values are passed as
written today (`config/loader.py:1110-1115`).
- Without `resolve_secret_refs: true`, pmcp keeps passing a server's own `env`
  values verbatim, including `op://` strings and an explicit
  `OP_SERVICE_ACCOUNT_TOKEN`. Only inherited environment is stripped.
- With `resolve_secret_refs: true` (operator sources only: user configs, `-c`,
  `~/.pmcp` overlay), pmcp resolves that server's `op://` env values, and
  refuses to pass an `OP_*` name in its env.
- Credential lookups (3.2.1) always resolve trusted references; before this
  feature a reference there would have reached the child as a literal key, which
  never worked, so no working configuration changes.
- P1's `doctor` lists every `op://` env value and every `OP_*` own-env key by
  server and source, so operators see the situation before P2.

#### 3.2.5 Write paths and pmcp's own variables

- `gateway.auth_connect` refuses reference-shaped credentials.
- `pmcp secrets set NAME op://...` writes only to the user store and only names
  `copy_allowed` accepts. `pmcp secrets sync` refuses reference-shaped values.
- pmcp's own variables (`is_pmcp_environment_name`, e.g. `PMCP_AUTH_TOKEN`) are
  never references: a reference-shaped value is refused with a value-free error,
  and for `PMCP_AUTH_TOKEN` the gateway refuses to start.
- `_load_operator_text` (shared by the `~/.env` load and the store load,
  `env_store.py:1118`, `1207`) takes the caller's class. It never publishes a
  reference-shaped value into `os.environ`, so no `child_process_env` probe
  inherits it; it records the skipped name with that class. A skipped `~/.env`
  reference is `home_dotenv` (refused, and it still shadows the store); a skipped
  store reference is `user_store`.
- `PMCP_AUTH_TOKEN` (and the other names pmcp reads itself) is checked from the
  store entry or environment value in the same step as the skip: a
  reference-shaped value makes `pmcp --transport http` exit non-zero before it
  binds. A plain value that interpolates to a reference-shaped string is refused
  the same way.
- `pmcp secrets set NAME op://...` uses the same gate as `migrate-op` (3.10): the
  running gateway reports `secret_refs`, and the gateway (not the CLI) does not
  report `NAME` as external or `home_dotenv` origin. User scope only.
- `gateway.auth_connect` refuses to overwrite a store entry that holds a reference.

#### 3.2.6 Consumers of a trusted credential

As with plain values today, a trusted credential is given to any server that names
it, including a consented project `.mcp.json` entry that reuses a manifest
server's name (`config/loader.py:1105-1129`) or an approved project overlay that
replaces a server (`manifest/loader.py:2164-2170`). Consent and overlay approval
remain the controls for that; this feature does not widen it. A key with no
`credential_sources` entry is untrusted (inventory test over the
`ResolvedServerConfig` constructors).

#### 3.2.7 Remote servers

A reference-shaped value that would be sent in a remote header is refused with
`credential_unavailable` (reason `remote_reference_unsupported`) and the server is
not connected. This covers both `${VAR}` lookups (`remote_auth.py:46-76`) and a
header whose final value contains `op://` anywhere (e.g. `Bearer op://...`), and the refused value is not kept
on `ManagedClient.resolved_remote_headers`. `migrate-op` refuses keys a remote
server uses.

### 3.3 Reference grammar (v1 subset)

Classification happens first, on the raw value:
- **plain**: does not start with `op://` after stripping leading ASCII
  whitespace, case-insensitively. Existing behaviour and validation unchanged
  (plain values may contain spaces as today).
- **reference-shaped**: everything else, including `" op://..."` and `OP://...`.

Reference-shaped values are then parsed with one grammar (`re.fullmatch`):
- no leading or trailing whitespace, no control, NUL or bidi characters;
- scheme `op://` in any case;
- `op://<vault>/<item>/<field>` or `op://<vault>/<item>/<section>/<field>`;
- each segment 1-64 characters from `[A-Za-z0-9 _.-]`, with spaces allowed only
  inside (not leading or trailing), not `.` or `..`;
- no query, fragment, `%`, `@`, `:`, `$`, `\`.

This grammar, and its refusal, apply only to values pmcp would resolve (3.2.1).
An own-env value of a server that has not opted in is passed through untouched
whatever its shape (3.2.4); `doctor` notes when such a value could not be opted in
because it fails this grammar (for example a segment longer than 64 characters).

Passing: a **supported reference** (examples: `op://PMCP Gateway/BrightData/credential`,
`op://abc123def456/xyz789/password`). Failing: **unsupported**, refused with
`credential_unavailable` (reason `reference_unsupported`), never resolved, never
forwarded (examples: `" op://v/i/f"`, `op://v/i/f?attribute=otp`,
`op://v/i/f?ssh-format=openssh`, `op://$ENV/i/f`, `op://v/i`, `op://v/i/s/f/x`).

No recursive resolution: a resolved value that is itself reference-shaped is
refused (`output_invalid`).

### 3.4 Resolution and the `op` process

New module `pmcp/secret_refs.py`:
- Executable: `op_path` from operator config, or the first of `/usr/bin/op`,
  `/usr/local/bin/op`, `~/.local/bin/op` that passes the trust check. Trust check
  (on every spawn): resolve symlinks; the final file and **every ancestor
  directory** are owned by root or the gateway uid and not writable by group or
  other (sticky world-writable directories refused); the file is a regular
  executable. pmcp executes the resolved, validated path (never the original alias),
  so a symlink in an unchecked directory cannot be swapped after the check. An
  invalid explicit `op_path` fails closed (`op_untrusted`); no fallback to the
  search list. Never a `PATH` search, never the project cwd.
- Argv: `op read --no-newline --no-color --cache=false --config <dir> -- <ref>`
  (flags confirmed on the installed 2.42.0 with `op --help`, `op read --help`).
- Environment: new builder `op_subprocess_env()`, added to `ENV_BUILDERS`
  (`tests/test_store_reader_inventory.py:650-653`): fixed system `PATH`, `HOME`,
  `OP_SERVICE_ACCOUNT_TOKEN`, `OP_CACHE=false`, and `HTTP_PROXY`, `HTTPS_PROXY`,
  `NO_PROXY`, `SSL_CERT_FILE`, `SSL_CERT_DIR` only when operator-supplied.
- Process: cwd and `--config` dir a private 0700 directory under the user state
  dir (owner/mode checked); stdin `/dev/null`; started with `spawn_owned`; stdout
  and stderr read concurrently with bounded readers (64 KiB, 8 KiB). Timeout
  (10 s), overflow, cancellation or shutdown: kill the process group, reap,
  discard every byte read, fail. Non-zero exit or empty output fails.
- Failure classes from exit status and stderr: `auth_failed`, `not_found`,
  `rate_limited`, `network`, `timeout`, `op_missing`, `op_untrusted`,
  `output_invalid`. Raw stderr and argv are never echoed.
- Concurrency: at most 2 `op` processes; identical in-flight references
  single-flighted (a cancelled waiter never cancels another caller's lookup);
  semaphore wait counts against the per-spawn deadline. No cache across spawns.
- Values shorter than 8 characters are refused (`output_invalid`).

### 3.5 Where resolution happens

- Configs keep reference strings; a resolved value is never written onto
  `ResolvedServerConfig`, `config.env`, `os.environ` or a store. Each spawn gets a
  fresh env dict.
- Resolution runs before teardown and outside the manager-wide lifecycle lock
  (`manager.py:1625`). The teardown site is `ClientManager.restart_server`
  (`manager.py:2096-2107`), which `update_server` reaches through
  `_restart_resolved_server`; resolution moves in front of its
  `disconnect_server`. Callers that take the lock before connecting (`connect_all`
  at `manager.py:1663`, lazy start at `1901-1903`, reconnect at `4044-4051`)
  resolve first, then take the lock, then re-check the server's config
  generation before spawning; a changed generation discards the resolution.
- A failed lookup during restart leaves the running instance up and returns the
  typed error (no new spawn uses an old value, so this is not a fallback).
- Every credential-injecting spawn path: `_connect_stdio` (`manager.py:3244`),
  reconnect (`_reconnect_loop`, resolving again from the stored reference), and
  `build_install_child_env` (`installer.py:722-765`), which covers the spawns at
  `installer.py:243` (install-and-run, Bright Data's provision path), `841`
  (`install_server`) and `907` (`verify_installation`).
- `gateway.refresh` (disconnect at `handlers.py:2248`, `connect_all` at `2265`,
  also reached from `update_server` at `5512`) resolves every config it will
  connect before its disconnect loop, and keeps a server whose lookup fails.
- Config generation: a counter per server, bumped whenever its resolved config
  is replaced (refresh, update, restart, provision). A mismatch after resolution
  releases nothing new: the caller re-resolves once, then returns a typed
  retryable error.
- Probe spawns that do not run the server (`update_server`'s update probe,
  `handlers.py:5368-5374`; `get_package_version`, `5295`, `5513-5514`;
  `verify_installation`'s `--help`, `installer.py:897-912`) omit every key that
  holds a reference: never resolved, never forwarded.
- `_connect_with_retry` and `_reconnect_loop` (`manager.py:4031-4051`) do not retry
  typed lookup failures (including `rate_limited`). Total resolution time per
  spawn is bounded (20 s).
- Static failures (grammar, source, token file, platform) are classified without
  calling `op`, in the availability check and status paths, so `connect_server`
  and status report `credential_unavailable` before any tool call.

### 3.6 Rotation of plain store values (P1)

Principle: **a lookup gives the answer a freshly started gateway would give, except
for the environment it actually started with.** The startup copy of a user-store
value is never consulted.

Origin registry: one map `name -> origin`, recorded at every insertion into
`os.environ` and for every skipped reference (3.2.5): `external` (present before
pmcp ran), `home_dotenv` (startup `~/.env` load, `cli.py:3290-3294`), `user_store`
(startup store load, `cli.py:3299-3302`), `auth_connect_project` (project-scope
`auth_connect`). A user-scope `auth_connect` writes the store and records
`user_store` (the store holds the same value), so later rotations and `migrate-op`
apply to it. The availability reload (`handlers.py:2792-2794`) is removed.

Precedence (unchanged): external > `~/.env` > user store > tenant > project files.

`credential_value(name, environ=True)`:
1. Environment layer, only when `environ=True` (the tenant-isolated lookup passes
   `False`, `remote_auth.py:128-138`, and must never see the user store):
   - origin `external` or `home_dotenv`: the `os.environ` value (static until
     restart, as today); a skipped reference of that origin is refused
     (`reference_not_allowed_here`);
   - origin `auth_connect_project`: the value it wrote, until the next start;
   - otherwise the **current** user store, read with the strict reader and parsed
     exactly as the startup load parses it, with interpolation of `${OTHER}` against
     the external environment, the static `~/.env` values and the current store
     (never stale startup copies). Classification happens after interpolation (a
     plain value that expands to a reference-shaped string is reference-shaped).
     Cached by `os.stat` and `os.lstat` identity of the store path. Outcomes:
     - present: that value (covers keys added after startup and a store created
       after startup);
     - absent, or no store file: no answer here; continue;
     - store exists but cannot be read: `credential_unavailable`
       (`store_unreadable`), which stops this lookup and the remaining lookup keys.
2. Tenant `repository`, then the project root's `.env` and `.env.pmcp`, as today.
3. Not found anywhere: `None`, and the caller tries the next lookup key
   (`secret_key` then `env_var`, `manifest/loader.py:313-329`), as today.

Explicit empty values keep their meaning (`env_store.py:899-901`). Typed failures
(`store_unreadable`, and P2's reference failures) stop the lookup in every
consumer (`config/loader.py:1120-1129`, `1536-1543`, `installer.py:758-762`), and
`config/loader.py:1216` propagates them instead of starting the server without its
credential.

A key deleted from the store therefore falls through to lower layers or the legacy
key immediately, exactly as after a restart (`tests/test_credential_parity.py`).
`doctor` and `migrate-op` flag a legacy key that sits next to its namespaced key,
so deleting one to revoke a credential is not silently undone by the other.

Tests: a readable store holding only `API_TOKEN` (the fleet layout): Bright Data
gets it; `BRIGHTDATA_API_TOKEN` added, rotated, then deleted (falls to `API_TOKEN`
if present, else `missing_auth`); a credential only in `.env.pmcp` with a store
present; a tenant `${VAR}` header with a store present; `environ=False` never reads
the store; no store file; store created after start; symlinked store edited and
target replaced; a store value interpolating a `~/.env` value; key only in
`~/.env`; key in both; external wins; user-scope `auth_connect` then store
rotation; project-scope `auth_connect` wins in-process; unreadable store; and the
suites `test_credential_parity.py`, `test_env_leak_229.py`, `test_auth.py`,
`test_store_readers_confined.py`, `test_manifest.py`, `test_config_loader.py`.

### 3.7 Redaction

- A process-local registry holds the token and every resolved value, registered
  before any child is spawned, kept for the process lifetime.
- Exact-value redaction is unconditional (independent of
  `options.redact_secrets`) and runs before truncation and serialization in:
  `PolicyManager.process_output` including `summary` (`policy/policy.py:958`),
  `resources_read`, `prompts_get`, `tasks_result`, `_sanitize_error`,
  `sanitize_auth_diagnostic` (before its other passes), update-probe output before
  it is sliced (`handlers.py:5398-5405`), installer output, child stderr logging
  (`manager.py:3764`), `last_error` (`manager.py:1890-1894`, `1917-1921`, surfaced at
  `handlers.py:2333-2334`), downstream catalog text, and a `logging.Filter` on the
  root logger that redacts the fully formatted message including `exc_text`.
- Each value is matched raw and JSON-string-escaped, longest first. Transformed
  forms (base64, split) are out of scope and documented.

### 3.8 Auth states

- `credential_unavailable` (new, P1 for `store_unreadable`, P2 for the rest).
- `credential_rejected` (new, P3): set only by (a) a shipped-manifest `auth_check`
  returning the provider's documented authentication failure, or (b) a named
  provider-specific signature. No generic substring heuristic. Cleared only by a
  successful `auth_check` or an authenticated call to that provider made with the
  same credential generation, or a restart with a new value; an in-flight result
  from an older generation never clears a newer rejection.
- Precedence: `policy_denied` > `credential_unavailable` > `credential_rejected` >
  `missing_auth` > `elicitation_required` > `insufficient_scope` > `unknown` > `none`.
- `auth_check`: shipped manifest only (refused from overlays, project configs,
  discovered servers, tool arguments). HTTPS GET to a pinned host and path,
  credential in a header (never the URL), no redirects, TLS verified, 5 s timeout,
  4 KiB response cap. Runs after a successful spawn, outside the lifecycle lock;
  it never aborts the connect. Only the documented auth-failure status sets
  `credential_rejected`; timeouts, 5xx and 429 change nothing. Bright Data:
  `GET https://api.brightdata.com/zone/get_active_zones`, `Authorization: Bearer`,
  401 on a bad key (observed 2026-11-08); verified live before P3 merges.
- `AuthState`, `AuthEventKind`, `DEFAULT_AUTH_STATE_SEMANTICS` and
  `_auth_event_for_state` (`types.py:34-92`, `handlers.py:1104-1111`) are
  extended in the PR that first returns each state.

### 3.9 Rate limits

1Password service-account limits are per token per hour and per account per day;
a name-based reference costs about 3 reads, an ID-based one 1. Expected volume:
one read per reference per spawn, restart or reconnect. Mitigations: ID-based
references recommended by `doctor` and `migrate-op`; concurrency cap and
single-flight (3.4); `rate_limited` is `credential_unavailable` with no fallback;
`doctor` resolves only with `--resolve`. The account tier is an owner input.

### 3.10 Migration and operator commands

- Health capability: P2 adds `secret_refs` to
  `GatewayDiagnosticsInfo.capabilities` (`types.py:152-180`), on both diagnostics
  objects (`/health`, `transport/http.py:514`, `656-663`; `gateway.health`,
  `server.py:294-301`), when the serving process resolves references, has a usable
  token, and none of the names `copy_allowed` accepts that were in the user store
  at startup arrived as external environment. Token source and state (never
  values) and per-name origins (names only) are reported too.
- `pmcp secrets migrate-op --map NAME=op://... [...] (--check | --backup)`:
  refuses unless the running gateway reports `secret_refs` (never the CLI's own
  version) and the CLI finds the token (3.1 path 2 or 3); resolves every
  reference first and compares with the current value (match/mismatch, never
  printing either); refuses all changes on any failure or mismatch unless
  `--accept-mismatch NAME`; refuses names with external origin and keys used by
  remote servers, and names `~/.env` shadows. The rewrite uses a cooperating-writer lock (a flock on a
  sibling lock file taken by every pmcp store writer: `secrets set/sync`,
  `auth_connect`, `migrate-op`), re-checks the file identity under the lock, then
  replaces atomically. Editors that do not take the lock are not covered
  (documented). `--backup` writes a 0600 copy first.
- `pmcp doctor`: per server, credential source and class; references refused by
  3.2; `op://` and `OP_*` values in own env (3.2.4); manifest runtime `env_var`
  names (e.g. `API_TOKEN`) present as external environment (they leak to every
  child); the gateway's `secret_refs` capability and token state from `/health`.
  `--resolve` performs lookups from the CLI.
- Live compatibility script (operator-run, value-free output): the installed `op`
  honours the 3.4 flags and `--cache=false` reads the current value after an item
  edit. Its result is a **P2 merge gate**.

## 4. Phases and PRs

| Phase | Content | Repo | Safe alone? |
|---|---|---|---|
| P1 | Origin registry and plain-store rotation (3.6); availability reload removed; `credential_unavailable`/`store_unreadable`; `doctor` leak and `op://`/`OP_*` listing. | pmcp | Yes: no references. |
| P2 | Token (3.1), provenance and resolution scope (3.2), grammar (3.3), `op` (3.4), spawn paths and placement (3.5), redaction (3.7), `secret_refs` capability, gated `secrets set`. Requires P1. Gate: live compatibility evidence. | pmcp | Yes: resolves only trusted credential lookups and opted-in servers; existing own-env `op://` values pass through as before; reference writes refuse until the serving gateway reports `secret_refs`, which on an `EnvironmentFile=` host means after P6a. |
| P3 | `credential_rejected`, `auth_check`, Bright Data check (3.8). | pmcp | Yes. |
| P4 | `migrate-op`, `doctor --resolve`, writer lock (3.10). | pmcp | Yes: refuses without the capability. |
| P5 | Release pmcp (owner approval); upgrade the target hosts. | pmcp | Gate for P6. |
| P6a | Dotfiles: install the token at `$XDG_CONFIG_HOME/credstore/op-service-account-token` (user manager) with `LoadCredential=op-service-account-token` (identifier form, so a missing file does not fail the unit); stop loading `pmcp.env` into the gateway environment in **all three** launchers (`pmcp.service` `EnvironmentFile=`, the macOS plist, `pmcp-service.sh`); restart the unit. | dotfiles | Only after P5 on that host. |
| P6b | Dotfiles: bootstrap writes references instead of values, per host, only when that host's gateway reports `secret_refs`. Canary Display-Server-01, then the fleet; retire `pmcp.env.bak` after evidence. | dotfiles | Only after P6a on that host. |

Rollback: P6b restore the backup store; P6a restore the launchers; pmcp keeps
reading plain values.

Each PR gets the cross-vendor panel and exact-head CI before merge. Docs
(`README.md`, `MIGRATING.md`, `SECURITY.md`) are updated in the PR that changes the
behaviour they describe.

## 5. Owner steps

1. Create the "PMCP Gateway" vault; copy the gateway keys into it, including the
   **new** Bright Data key (the `Consiliency Deploy Secrets/BrightData` item may
   still hold the revoked one). Prefer recording item IDs.
2. Create a service account with read-only access to that vault only; note the
   account tier (rate limits).
3. Approve the release (P5) and the host cutover order (P6).

## 6. Security properties to verify

- Token: read by non-blocking descriptor with owner/mode checks; only the
  dedicated `op` process receives it; never in `os.environ`, a downstream child's
  inherited env, a store, logs or output; redacted if it ever appears.
- References: resolved only from trusted sources for credential lookups and
  opted-in servers; the process environment is never trusted; agents and
  repositories cannot choose vault items; pmcp's own variables can never be
  references.
- `op`: trusted path with ancestor checks, minimal env, no shell, no stdin,
  bounded time and output, killed and reaped on any abnormal end, cache off.
- Every failure is typed and fail-closed (G4); existing configurations unchanged
  (G6).
- A trusted credential goes to any server that names it, as plain values do
  today (3.2.6); consent and overlay approval stay the controls.
- Known limits: same-user processes can read the token file; transformed
  secrets are not redacted; Windows unsupported; editors that bypass the writer
  lock.

## 7. Verification

Fake `op` executables in a trusted temporary directory (record argv/env, return
fixed output, hang, overflow, fail with each stderr class). CI never calls
1Password. Tests cover every case named in 3.1-3.10, including: FIFO token file;
untrusted `op` ancestor; environment reference refused with zero `op` calls;
`resolve_secret_refs` off keeps own-env `op://` and `OP_SERVICE_ACCOUNT_TOKEN`
verbatim; overlay `extra_env` provenance; grammar examples in 3.3; restart failure
leaves the running instance; config generation re-check; redaction before
truncation, escaped forms, formatted log records; inventory tests
(`tests/test_store_reader_inventory.py`, `tests/test_credential_parity.py`).

```bash
# dev0: affected files only, at most -n 2, after the AGENTS.md headroom check,
# with an isolated HOME and evidence outside scratch (plans/verification-template.md).
ev=/path/to/owned/run-evidence; mkdir -p "$ev"
HOME="$(mktemp -d)" uv run --locked python scripts/pytest_temp.py -- \
  tests/test_secret_refs.py tests/test_env_store.py tests/test_store_reader_inventory.py \
  tests/test_credential_parity.py -q --junitxml="$ev/junit.xml" > "$ev/pytest.log" 2>&1
# Full suite: on ai under systemd-run MemoryMax, or hosted CI (AGENTS.md).
```

Live check on Display-Server-01 after P6b: Bright Data through a reference;
rotate the item; `restart_server`; the new key is used without a gateway restart;
a revoked key gives `credential_rejected`.

## 8. Open questions

None outstanding. Round 2 answered both: refuse references in remote headers in
v1 (all seats), and keep a default `op` search list with root-owned paths first
and ancestor checks, `op_path` as an override (Astra, Grok, Gemini, Claude).

## 9. Decisions taken without the owner

- Existing child-side resolution keeps working (3.2.4): pmcp resolves only
  credential lookups and servers that opt in. Chosen over declaring child-side
  resolution unsupported, because it keeps G6 and needs no migration. All four
  seats agreed in round 3.
- A deleted store key falls through (3.6), rather than being terminal until
  restart. Round 3 split: Astra, Grok and Claude proposed "seen, then removed, is
  terminal"; Gemini proposed restart parity. Chosen: parity. Their shared concern
  was never answering from the stale startup copy, which 3.6 guarantees either
  way; a terminal state that a restart reverses is inconsistent, contradicts
  `tests/test_credential_parity.py`, and protects only against a legacy key the
  operator left in place, which `doctor` and `migrate-op` now flag.

## 10. Findings and their disposition

Round 1 (all four seats) is mapped in revision 2's section 9; those changes are
kept. Round 2:

| Finding (seat) | Change |
|---|---|
| Rotation: keys added after start, availability reload, `~/.env`, lstat identity, interpolation, removed-key result, loader swallowing errors (Astra B1, Gemini R2-B2, Claude R2-B1) | 3.6 rewritten: origin at every insertion, reload removed, `home_dotenv` class, stat+lstat identity, interpolation context, terminal removal, propagation |
| References through the process environment; launchers that source `pmcp.env`; capability gate (Grok B1, Claude R2-B2, Gemini NB1) | 3.2.2 refuses environment references; P6a changes all three launchers; 3.10 `secret_refs` capability gates migration and P6b |
| P2 breaks child-side resolution (Claude R2-B3) | 3.2.1, 3.2.4: resolve only lookups and opted-in servers |
| CLI cannot find the token on P6a hosts; on-demand lookup; credential snapshot (Gemini R2-B1, Claude R2-B4) | 3.1: on-demand, credstore path 2 shared by CLI and unit, snapshot documented |
| Overlay `extra_env` provenance; unclassified sources (Gemini R2-B3) | 3.2.3 `extra_env_sources`; 3.2.2 classifies environment, `~/.env`, `$PMCP_MANIFEST_PATH` |
| Grammar contradiction; leading-space spoof (Astra B2, Gemini R2-B4, Grok N1) | 3.3: classify first, one reference grammar, examples |
| Token FIFO; `op` ancestor trust; search order (Astra B3, Grok open question) | 3.1 `O_NONBLOCK`; 3.4 ancestor checks, root paths first, explicit path fails closed |
| Teardown site and lock callers (Grok B1, Astra N1, Gemini NB3) | 3.5 names `restart_server`, generation re-check, semaphore and single-flight rules |
| Don't publish references into `os.environ` (Grok N2) | 3.2.5 |
| Redaction sinks and ordering (Astra N2, Grok N3, Claude R2-N7) | 3.7 |
| `credential_rejected` clearing and precedence (Astra N3, Gemini NB2) | 3.8 |
| Real compare-and-swap (Astra N4) | 3.10 writer lock |
| Verification command; compatibility gate; docs; inventory tests (Astra N5, Gemini NB4, NB5) | 7, 3.10, 4 |
| Citations: policy, installer, `repository_may_supply`, `_NEVER_LOADED`, credstore search (Grok N3-N6) | Corrected in 3.1, 3.2.5, 3.5, 3.7, P6a |

Round 3:

| Finding (seat) | Change |
|---|---|
| Absent-from-store treated as terminal; legacy key, project and tenant lookups broken; `store_unreadable` swallowed (Astra R3-B1a, Grok B2, Gemini R3-B1, Claude R3-B1) | 3.6 rewritten: absent continues, unreadable is a typed stop that propagates; parity decision in 9 |
| Interpolation context without `~/.env` (Astra R3-B1b, Claude N6) | 3.6 |
| `environ=False` tenant lookup (Claude R3-B1d) | 3.6 step 1 |
| User-scope `auth_connect` pinning; overwriting external values (Gemini R3-B1, Claude N2) | 3.6 origins; 3.2.5 |
| Shared `_load_operator_text` class (Gemini R3-B1, Claude N6) | 3.2.5 |
| `secrets set` writes a reference too early (Grok B1) | 3.2.5, P2 row |
| Update probe and other probe spawns; `gateway.refresh`; generation (Gemini R3-B2, Claude N3, N4) | 3.5 |
| `PMCP_AUTH_TOKEN` skip vs refusal (Claude N1) | 3.2.5 |
| Grammar refusal scope (Grok N1) | 3.3 |
| Two diagnostics objects; capability name set (Grok N2, N4) | 3.10 |
| Reconnect retries; static failures before spawn (Grok N3, Claude N10) | 3.5 |
| Missing vs invalid token file; prefix; credstore digests, symlinks, search path (Grok N5, Claude N9) | 3.1 |
| G3 wording; trusted-credential consumers (Astra N3, Claude N13) | 2, 3.2.6, 6 |
| Section 1 item 4 claim (Claude N5) | 1 |
| Redaction sinks `last_error`, catalog (Claude N11) | 3.7 |
| `migrate-op` and `~/.env` shadowing (Claude N7) | 3.10 |
| Header containing `op://` (Claude N8) | 3.2.7 |
| Execute the validated `op` path (Astra N2) | 3.4 (implementation) |
| `op_path` key, state dir, lowercase proxies, rate-limit breaker (Claude N8) | Implementation, P2 |
