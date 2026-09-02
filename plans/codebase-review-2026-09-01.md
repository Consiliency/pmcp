# PMCP Codebase Review — 2026-09-01

Reviewer: Claude (Claude Code, remote session). Read-only audit of `Consiliency/pmcp` at
`d743bc4` (v2.7.3 + unreleased CI pins). No production code was changed; this document is
the only artifact. Every CRITICAL/HIGH/MEDIUM finding below was either reproduced with a
script (listed in Appendix B) or traced end-to-end in the source and quoted with line
numbers. Findings marked *verify* were traced but not executed.

Previous review: `plans/codebase-review-2026-06-15.md`. Appendix C records which of its
items are now fixed (most of them) and which still stand.

---

## 0. How to read this document

- Section 1 is the executive summary and the ten things to do first.
- Sections 2–7 are the findings, grouped by kind and ordered by severity within each
  group. IDs are stable (`S-` security, `C-` correctness/robustness, `P-` performance,
  `M-` maintainability/architecture, `T-` tests/CI, `D-` docs/packaging) so they can be
  turned into issues.
- Each finding has **Where** (file:line at `d743bc4`), **Evidence**, **Impact**, **Fix**,
  and **Effort** (S ≤ half a day, M ≤ a few days, L = a phase).
- Section 8 is what the codebase does well and should keep doing.
- Section 9 is a suggested order of work.

Severity is relative to PMCP's own threat model (SECURITY.md): a local-first gateway that
is also run as a shared HTTP service, brokering untrusted downstream servers on behalf of
a semi-trusted, prompt-injectable agent.

---

## 1. Executive summary

PMCP is in materially better shape than in June. The redaction leaks, the unguarded
connect path, the orphaned reconnect/stderr tasks, the hand-rolled YAML cache writer, the
matcher scoring bug and the missing resource-server token validation from the June review
are all fixed, and the fixes are backed by tests and mutation evidence. Lint and type
checks are clean, coverage is 85 %, and CI on `main` is green.

The remaining risk has moved up a level, from bugs inside individual functions to the
**trust boundaries between the agent, repository-supplied configuration, and the host**:

1. **The agent can execute arbitrary npm packages.** `gateway.register_discovered_server`
   accepts any package name and `gateway.provision` runs it with `npx -y`; the policy
   allowlist checks the *server name the agent chose*, not the package (S-01).
2. **An availability check leaks every key of a project `.env` into every downstream
   server.** `_check_api_key_available` calls `load_dotenv()` on the current directory's
   `.env` as a side effect of a lookup, and the per-server environment sanitiser only
   strips PMCP-managed keys (S-02).
3. **Repository-supplied config is trusted without an approval step.** A cloned repo's
   `.pmcp/manifest.yaml` can replace any shipped server's command wholesale, and its
   `.mcp.json` and `.mcp-gateway-policy.yaml` are applied the same way, so "clone a repo
   and ask the agent to use GitHub" can run attacker-chosen code (S-03, S-11).
4. **The HTTP auth surface has four reproducible defects**: a non-ASCII `Authorization`
   byte crashes the handler with a 500 instead of a 401; the published protected-resource
   metadata advertises a Host-derived `resource`; an unauthenticated client can force a
   JWKS refetch per request with no cooldown while the rate limiter runs *after* auth; the
   JWKS fetch has no explicit timeout (aiohttp's 300 s default) under the refresh lock; and
   the metadata route skips the Host allowlist entirely (S-07 – S-10).
5. **`gateway.submit_feedback` is a prompt-injection exfiltration channel**: with
   `confirm_submission=true` (agent-settable) it posts agent-authored text to a public
   repo using the operator's ambient `GITHUB_TOKEN` (S-04).

On the correctness side, the downstream protocol handling has gaps that will show up as
"the server hangs" reports: server-initiated requests (including `ping`) are never
answered, a cancelled caller leaks a pending request forever, one invalid UTF-8 byte on
stdout tears a server down, and `gateway.cancel` never tells the downstream to stop
(C-01 – C-04). `gateway.tasks_list` without a server name fails whenever any known server
is offline (C-06).

On performance, the biggest single item is that `load_manifest()` is uncached, costs
~200 ms per call with the pure-Python YAML loader (22 ms with `CSafeLoader`), and is
called up to ~15 times inside one `catalog_search` — several seconds of event-loop
blocking per search in the worst case (P-01). The single lifecycle lock is held across an
entire connect including retry sleeps (P-02).

On the test and CI side, the suite is strong but not hermetic: one test reaches the npm
registry, one reads the developer's real home configuration, 65 tests wait on real
wall-clock sleeps, and no fixture resets the module-level caches (T-01, T-06 – T-09).
The release-workflow guard pins every action's SHA but never looks at a step's `with:`
inputs (CI-01).

### The ten things to do first

| # | Action | Findings | Effort |
|---|--------|----------|--------|
| 1 | Gate discovered-package provisioning behind an operator opt-in and bind policy to package identifiers | S-01 | M |
| 2 | Remove `load_dotenv` from availability checks; read PMCP's own files into a local dict | S-02 | S |
| 3 | Add a trust/approval store for project-scoped config (`.mcp.json`, `.pmcp/manifest.yaml`, policy file) | S-03, S-11 | M |
| 4 | Fix the HTTP/JWKS defects (bytes compare, canonical `resource` + Host check on every route, kid-miss cooldown + rate-limit-before-auth, fetch timeout) | S-07–S-10 | S |
| 5 | Make `submit_feedback` preview-only by default and stop using ambient `GITHUB_TOKEN` | S-04 | S |
| 6 | Cache the parsed manifest and switch to `CSafeLoader` | P-01 | S |
| 7 | Answer server-initiated requests, `finally`-pop pending requests, decode stdout with `errors="replace"`, send `notifications/cancelled` | C-01–C-04 | M |
| 8 | Generate tool input schemas from the pydantic models and set `extra="forbid"` | C-10, M-02 | M |
| 9 | Replace the keyword-anchored redactor with separator-anchored + shape-based patterns and a prose corpus test | S-12 | M |
| 10 | Refresh `uv.lock` (aiohttp, starlette, pyjwt, cryptography, python-dotenv have advisories) and add `pip-audit` to CI | D-01 | S |

---

## 2. Baseline

Environment: Linux sandbox, Python 3.11.15, `uv 0.8.17`, `uv sync --all-extras`.

| Check | Result |
|-------|--------|
| `ruff check src/ tests/` | clean (but no `[tool.ruff]` section: default rule set E4/E7/E9/F only) |
| `ruff format --check` | clean (119 files) |
| `mypy src/pmcp` | clean (non-strict: `ignore_missing_imports` only) |
| `mypy --strict src/pmcp` | 41 errors in 12 files (17 `type-arg`, 9 `no-any-return`, 5 `no-untyped-def`, 5 `no-untyped-call`, 3 `unused-ignore`) |
| `ruff --select B,ASYNC,S,BLE,DTZ` | ~80 `BLE001` blind excepts, 17 `S110` try/except/pass, 7 subprocess `S603/S607`, 4 `S310`, `ASYNC230` ×2 and `ASYNC221` ×1 in `cli.py`, `DTZ006` ×1, `B905` ×1, `B904` ×3 |
| `pytest tests/ --cov` | 3114 passed, 117 failed, 129 errors, 29 skipped, 431 s; **coverage 85 %** (10 428 statements) |
| CI on `main` | runs #486–#500 all green |

All 117 local failures and 129 errors are sandbox artifacts, not bugs:

- The npm identity resolver deliberately refuses to run when the gateway environment sets
  `NODE_OPTIONS` or any `npm_config_*` variable (this sandbox sets both). Re-running the
  four affected files with those variables scrubbed: **733 passed**.
- `tests/runtime/harness.py` shells out to `ss` (iproute2), which is absent here.

Two baseline observations that *are* findings: the slowest test
(`tests/test_refresher.py::TestShortCircuitUsesCompareVersions::test_short_circuit_is_a_single_compare_versions_call`)
took 70.9 s because it spawned a real `npx -y srv` and reached `registry.npmjs.org` when
identity resolution refused (T-01), and `pip-audit` against `uv.lock` reports advisories
for `aiohttp 3.13.2`, `starlette 0.50.0`, `pyjwt 2.10.1`, `cryptography 46.0.3`,
`python-dotenv 1.2.1`, `python-multipart 0.0.21`, `pygments`, `click` and `pytest` (D-01).

---

## 3. Security findings

### S-01 · Agent-chosen npm packages are executed; policy binds to the name, not the package · **HIGH** · confirmed

**Where** `src/pmcp/tools/handlers.py:5496-5571` (`register_discovered_server`),
`:4110-4127` and `:4461-4475` (`provision`), `src/pmcp/validation.py:16-30`,
`src/pmcp/manifest/installer.py:93-166`.

**Evidence** `register_discovered_server` stores
`ServerConfig(command="npx", args=["-y", package], install={...: ["npx","-y",package]})`
for any `package` that passes `is_valid_package_name` (which only blocks flags, paths and
shell metacharacters). `provision` then checks `self._policy_manager.is_server_allowed(server_name)`
— the *caller-supplied* name — and calls `job_manager.start_install(server_config, platform)`,
which is `asyncio.create_subprocess_exec(*install_cmd, ...)`. Nothing ties `server_name`
to the package and nothing asks a human. One mitigating detail, reproduced: `provision`
consults the shipped manifest *before* the discovered registry (`handlers.py:4262-4266`),
so a discovered entry cannot shadow a manifest name — registering `github` with an evil
package still installs `@modelcontextprotocol/server-github`. Any name that is **not** in
the manifest is enough, and the default policy allows every name.

Repro (`scratchpad/verify/repro_c2.py`, `JobManager.start_install` mocked):

```
server_name='internal-approved-tool' package='totally-arbitrary-evil-package'
  policy.is_server_allowed -> True (allowlist=['internal-approved-tool'])
  provision -> ok=True status=started
  ACTUAL install command JobManager.start_install would exec:
  ['npx', '-y', 'totally-arbitrary-evil-package']
```

**Impact** A prompt-injected agent (downstream tool output is untrusted by design) can
register any name that is not in the manifest with `package="<anything on npm>"` and have
the gateway execute it with the sanitised environment. With the default (allow-all)
policy no configuration is needed at all; with a restrictive policy any allowlisted
non-manifest name or wildcard suffices. `npx -y` resolves and runs unpinned code from
the public registry. This is the largest single capability the agent holds and it has no
operator gate.

**Fix** (a) Default-deny discovered-package provisioning; require an operator opt-in
(`allowDiscoveredPackages: true` in the user config or a `pmcp approve <server>` step
that writes a hash-pinned approval). (b) Make the policy able to allow/deny **package
identifiers** (`packages: {allowlist: [...]}`) and check it in `provision`. (c) Log the
exact argv at WARNING before every install spawn. (d) Consider pinning: refuse
`register_discovered_server` without a version, or resolve and record the version at
registration and pass `pkg@<version>` to `npx`.

**Effort** M.

### S-02 · An availability check loads `.env` into the gateway environment and leaks it to every downstream server · **HIGH** · reproduced

**Where** `src/pmcp/tools/handlers.py:2926-2946` (`_check_api_key_available`),
`src/pmcp/env_store.py:124-143` (`sanitized_subprocess_env`).

**Evidence**
```python
for env_path in [Path.cwd() / ".env", Path.cwd() / ".env.pmcp",
                 Path.home() / ".config" / "pmcp" / "pmcp.env"]:
    if env_path.exists():
        load_dotenv(env_path)          # mutates os.environ
        if os.environ.get(env_var):
            return True
```
and `sanitized_subprocess_env` documents its own gap: *"this removes only PMCP-managed
keys; secrets the operator exported into the shell or a plain `.env` are not sanitized."*
The check is reached from `gateway.catalog_search(query, include_offline=true)`
(via `_manifest_candidates_for_query`), `gateway.request_capability`,
`gateway.provision`, and `gateway.refresh` (`is_auth_available=self._check_api_key_available`).

Repro (`scratchpad/verify/repro_c1.py`: a temp cwd with `.env` containing
`SOME_DB_PASSWORD=hunter2` and `UNRELATED_SECRET=topsecret123`, then one call):

```
gw._check_api_key_available('SOMETHING_MISSING') -> False
os.environ has SOME_DB_PASSWORD: True = hunter2
sanitized_subprocess_env(None) has SOME_DB_PASSWORD: True = hunter2
sanitized_subprocess_env(None) has UNRELATED_SECRET: True = topsecret123
```

**Impact** After the first such call in a directory containing a generic project `.env`
(database URLs, cloud keys, anything), those keys are in `os.environ` and therefore in
the environment of *every* downstream server spawned afterwards. That defeats the
per-server credential isolation that `env_store` was built to provide, reads a file the
documentation says PMCP does not read, and does three synchronous file reads per
candidate per key on the event loop.

**Fix** Read with `dotenv_values()` into a local dict and never mutate `os.environ` at
request time; only consult PMCP's own stores (`.env.pmcp`, `pmcp.env`) and pass
`project_root` rather than `Path.cwd()`. Add a test asserting `os.environ` is unchanged
across a `catalog_search`.

**Effort** S.

### S-03 · Repository-supplied configuration is trusted with no approval step · **MEDIUM** (HIGH in shared/CI use) · confirmed by trace

**Where** `src/pmcp/manifest/loader.py:617-676, 786-821` (project overlay walk-up and
whole-entry replace), `src/pmcp/config/loader.py:216-241, 795-813` (project `.mcp.json`),
`src/pmcp/policy/policy.py:42-75` (cwd-relative policy discovery).

**Evidence** `_find_project_manifest` walks up from `Path.cwd()` to the nearest
`.pmcp/manifest.yaml` (symlink-escape checked, content trusted) and `load_manifest`
does `servers.update(overlay_servers)` — command, args, install lane and
`requires_api_key` included. Project `.mcp.json` servers are loaded with the same trust.

**Impact** Claude Code prompts before enabling project-scoped MCP servers; PMCP does not,
and it additionally lets the agent connect/provision those servers on demand. A checkout
that ships `.pmcp/manifest.yaml` redirecting `github` to `npx -y evil` turns "use the
GitHub server" into code execution. In shared-HTTP deployments the cwd is whatever the
service was started in, which makes the exposure less likely but harder to reason about.

**Fix** A trust store keyed by absolute path + content hash (`pmcp trust <file>` /
`--trust-project`), consulted before applying any project-scoped source; until then,
default to user/env overlays only and log project sources at WARNING. Document the trust
model explicitly in SECURITY.md.

**Effort** M.

### S-04 · `gateway.submit_feedback` can post agent-authored text publicly with the operator's `GITHUB_TOKEN` · **MEDIUM** · confirmed by trace

**Where** `src/pmcp/tools/handlers.py:4796-4992`.

**Evidence** `token = os.environ.get("PMCP_FEEDBACK_TOKEN") or os.environ.get("GITHUB_TOKEN")`;
with `confirm_submission=true` the handler POSTs `issue_body` (agent-supplied, ~4000
tokens, only secret/email scrubbed) plus the last six audit events to
`https://api.github.com/repos/{repository}/issues`, falling back to `gh issue create`
(the user's `gh` login). The "ask the user first" step is a sentence in the tool
description. The default repository is still `ViperJuice/pmcp`; the remote is
`Consiliency/pmcp`, and `urllib` turns a 301'd POST into a bodiless GET, so the
token path silently fails today and the `gh`/browser fallbacks take over.

**Impact** A prompt-injected agent can exfiltrate anything in its context to a public
issue under the operator's identity. It also makes blocking `urlopen()` calls on the
event loop (P-03).

**Fix** Never use ambient `GITHUB_TOKEN`; require `PMCP_FEEDBACK_TOKEN` and an explicit
`enable_feedback_submission: true`; keep the default behaviour preview-only (return the
payload and a browser URL). Fix the repository default. Move the HTTP calls off the loop.

**Effort** S.

### S-05 · Secret store writes follow symlinks and are not atomic · **MEDIUM** · reproduced

**Where** `src/pmcp/env_store.py:81-104` (`write_env_file`), `:146-158` (`set_env_value`).

**Evidence** `fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)` — no
`O_NOFOLLOW`, no regular-file check, no temp-file-and-rename. Project scope resolves to
`<project>/.env.pmcp`. Repro (`scratchpad/verify/repro_c3.py`): with
`project/.env.pmcp -> elsewhere/victim.txt` (a PEM file),
`set_env_value("project", "API_KEY", "x", project=...)` left the symlink in place and
rewrote `victim.txt` as two lines: `MIIsomeSuperSecretKeyMaterial=""` and `API_KEY=x`.

**Impact** A checkout containing a symlink `.env.pmcp -> ~/.bashrc` (or any file) gets
that target truncated and rewritten with `KEY=value` lines the first time the agent
calls `gateway.auth_connect(scope="project")` or the operator runs `pmcp secrets set`.
A crash mid-write also loses every other stored secret, and two writers (gateway + CLI)
can interleave.

**Fix** Open with `O_NOFOLLOW`, refuse non-regular files, write to a `mkstemp` file in
the same directory and `os.replace`, hold an `fcntl` lock while rewriting.

**Effort** S.

### S-06 · Orphan cleanup SIGKILLs any process whose argv matches a configured server · **MEDIUM** · reproduced

**Where** `src/pmcp/server.py:810-856` (`_kill_orphan_processes`), called unconditionally
from `:734` on every gateway start.

**Evidence** For every `/proc/<pid>` whose `(basename(argv0), args)` equals a configured
stdio server's `(basename(command), args)`, `os.kill(pid, SIGKILL)` — no parent-pid,
session, owner or environment check (the method body never touches `self`). Repro:
two processes spawned outside any `GatewayServer`/`ClientManager` with an argv that
happened to match one configured server were both found and killed:

```
Found orphan PID 2381 ...; sending SIGKILL
Found orphan PID 2382 ...; sending SIGKILL
process A terminated=True returncode=-9
process C terminated=True returncode=-9   <- unrelated process
```

**Impact** A second gateway instance (legitimate with a distinct `--lock-dir`, as the
scoped-advisor tests do), Claude Desktop/Cursor running the same server command, or an
operator's own shell process gets killed at every PMCP start. `npx`/`uvx`-launched servers
escape only because their argv0 becomes `node`/`python`.

**Fix** Record spawned PIDs in the lock directory (or stamp children with a
`PMCP_GATEWAY_ID` env var and check `/proc/<pid>/environ`) and kill only those; never
match on argv alone.

**Effort** S.

### S-07 · Unknown-`kid` tokens force a JWKS refetch per request, and rate limiting runs after auth · **MEDIUM** · reproduced

**Where** `src/pmcp/auth.py:414-422` (`AsyncJWKS.get_for_token`), `src/pmcp/transport/http.py:480-536`.

**Evidence** Repro (Appendix B, `repro_http_auth.py` §3): one warm-up plus 20
unauthenticated tokens with random `kid`s produced 21 JWKS fetches — one outbound HTTP
call per token. The `kid` is read with `jwt.get_unverified_header` before any signature
check, and `get(force_refresh=True)` bypasses the TTL unconditionally. In `handle_mcp` the
resource-server validation (line 480) precedes the rate limiter (line 529), so
`rate_limit_rpm` does not bound the storm.

**Impact** Unauthenticated JWKS-fetch amplification against the operator's authorization
server, serialised under the refresh lock so it also delays legitimate requests.

**Fix** A minimum interval between kid-miss refreshes (e.g. 60 s, one in flight),
and move the rate-limit check before authentication.

**Effort** S.

### S-08 · JWKS fetch has no explicit timeout and runs under the refresh lock · **MEDIUM** · confirmed by trace

**Where** `src/pmcp/auth.py:424-431`.

**Evidence** `aiohttp.ClientSession()` / `session.get(self._raw_url, allow_redirects=False)`
with no `ClientTimeout`; aiohttp's default total timeout is 300 s.

**Impact** A slow or black-holed JWKS endpoint stalls every resource-server request for up
to five minutes.

**Fix** `aiohttp.ClientTimeout(total=5, sock_connect=3)`; serve stale keys while a
refresh is in flight.

**Effort** S.

### S-09 · A non-ASCII byte in `Authorization` crashes the shared-secret check (500, not 401) · **MEDIUM** · reproduced

**Where** `src/pmcp/transport/http.py:475-479`.

**Evidence** `hmac.compare_digest(incoming, f"Bearer {auth_token}")` on `str`; Starlette
decodes header bytes as latin-1, and `compare_digest` raises
`TypeError: comparing strings with non-ASCII characters is not supported`. Repro §1:
`Authorization: Bearer caf\xe9` → `TypeError` escapes the handler; Starlette's
`ServerErrorMiddleware` sends a real `500 Internal Server Error` body to the client and
re-raises so uvicorn logs a full traceback, while `Bearer nope` → 401. The worker itself
survives.

**Impact** Unauthenticated log-flooding / error-rate DoS; also a silent behavioural
difference between "wrong token" and "weird token".

**Fix** Compare bytes: `hmac.compare_digest(incoming.encode("latin-1"), expected_bytes)`,
or reject non-ASCII with 401.

**Effort** S.

### S-10 · Protected-resource metadata advertises a Host-derived `resource` and skips the Host allowlist · **MEDIUM** (HIGH when `allowed_origins` is configured) · reproduced

**Where** `src/pmcp/transport/http.py:423-441` (`handle_protected_resource_metadata`),
`:342-355` (`_host_rejected`), `:467-471` (only `handle_mcp` calls the checks).

**Evidence** `"resource": str(request.url_for("mcp"))`. Repro §2: with
`resource_server_audience="https://gateway.example.com/mcp"`, `Host: evil.example.net`
yields `resource: http://evil.example.net/mcp` (scheme also wrong behind a TLS proxy).
The metadata route never calls `_host_rejected`/`_origin_rejected`, so the DNS-rebinding
defence an operator turns on with `allowed_origins` does not cover it:

```
allowed_origins=["https://gateway.example.com"]
Host: evil.example.net  POST /mcp                               -> 403 Forbidden
Host: evil.example.net  GET  /.well-known/oauth-protected-resource -> 200 {"resource": "http://evil.example.net/mcp"}
```

**Impact** RFC 9728 clients use `resource` to request tokens with that audience; PMCP then
rejects them (audience mismatch), and SECURITY.md's "audience is never derived from the
request Host header" is true for validation but not for what PMCP publishes. The pinned
`starlette 0.50.0` also carries Host-header advisories in exactly this code path (D-01).

**Fix** Publish `resource_server_audience` when configured; otherwise require an
explicit canonical URL rather than deriving one. Apply `_host_rejected` to every route,
not just `/mcp`.

**Effort** S.

### S-11 · A project-local policy file silently shadows the user's global policy · **MEDIUM** · confirmed by trace

**Where** `src/pmcp/policy/policy.py:42-47, 68-75`.

**Evidence** `for default_path in _default_policy_paths(): if default_path.exists():
self._load_policy(default_path); break` — first match wins, and the project paths come
first. The default policy is allow-all.

**Impact** An operator who restricts the gateway with `~/.claude/gateway-policy.yaml`
loses that restriction the moment the gateway runs inside a checkout that ships an
`.mcp-gateway-policy.yaml` (only an INFO log says which file was loaded).

**Fix** Intersect (a project policy may only narrow), or refuse to start when both exist
unless `--policy` names one, and log at WARNING when a project policy shadows a user one.
Ties into S-03's trust store.

**Effort** S.

### S-12 · The secret redactor mangles ordinary prose and misses common token shapes · **MEDIUM** · reproduced

**Where** `src/pmcp/policy/policy.py:20-29, 337-353`, `src/pmcp/auth.py:576-608`.

**Evidence** Repro `repro_policy.py`:

| Input | `redact_secrets` output |
|-------|-------------------------|
| `HTTP status code 200 returned` | `HTTP status code [REDACTED] returned` |
| `session expired, please retry` | `session [REDACTED], please retry` |
| `token count 512 of 4096` | `token [REDACTED] 512 of 4096` |
| `zip code 94110` | `zip code [REDACTED]` |
| `source code review complete` | `source code [REDACTED] complete` |
| `AKIAIOSFODNN7EXAMPLE`, `xoxb-…`, `AIza…`, `sk_live_…`, `glpat-…`, `-----BEGIN RSA PRIVATE KEY-----` | unchanged |

The keyword patterns accept bare whitespace as the key/value separator
(`([\s:=]+)` in `sanitize_auth_diagnostic`, `[\s]+` in `(bearer|token)[\s]+…`).

**Impact** With `redact_secrets=true` (and by default for MCP task results and
`tasks_result`), legitimate tool output is corrupted, while AWS/Slack/Google/Stripe/GitLab
tokens and PEM keys pass through verbatim. Both halves reduce trust in the guardrail.

**Fix** Split the redactor: keyword patterns require `[:=]`/quote separators (or JSON
key context) in bulk output; add shape-based patterns (curated from gitleaks/secretlint);
test against a prose corpus and a secrets corpus; keep the whitespace-tolerant form only
for the 400-char diagnostic path where it originated.

**Effort** M.

### S-13 · Rate limiter and unauthenticated endpoints · **LOW**

- `src/pmcp/transport/http.py:153-179`: `_rl_store` never evicts idle IPs (unbounded growth
  when exposed to many sources); `import time` inside the function.
- `/health` and `/metrics` are unauthenticated in every auth mode and `/health` returns the
  version and auth/rate-limit diagnostics. Document, or allow opt-in auth for `/metrics`.

### S-14 · Remote downstream client follows redirects with custom auth headers · **LOW**

`src/pmcp/client/manager.py:2374-2382`: `httpx2.AsyncClient(follow_redirects=True, headers=headers)`.
httpx strips `Authorization` on cross-origin redirects but **not** custom headers such as
`X-API-Key`; a compromised or misconfigured remote can redirect and receive them.
Consider `follow_redirects=False` (mcp 1.x's default was inherited here deliberately —
worth a conscious decision and a note).

### S-15 · Singleton lock file is unlinked on release · **LOW**

`src/pmcp/identity.py:266-270`: the classic flock/unlink race — B opens inode X, A unlinks
and exits, B locks X, C creates and locks a new inode → two gateways. Never unlink; the
lock releases with the fd.

### S-16 · Tenant id pattern admits `.` and `..` · **LOW**

`src/pmcp/remote_auth.py:12`: `^[A-Za-z0-9_.-]+$` accepts `..`, so the tenant env path
escapes one level. Read-only impact; reject `.`/`..`.

### S-17 · Small JWT validation nits · **LOW**

`src/pmcp/auth.py:465-476`: an unknown `kid` against a single-key JWKS falls back to that
key (repro §5), which hides rotation misconfiguration. `PyJWKSet.from_dict` rebuilds all
key objects on every request — cache per fetch.

### S-18 · Raw exception text still logged on two error paths · **LOW**

`src/pmcp/tools/handlers.py:4442` (`logger.error(f"Failed to connect remote server {server_name}: {e}")`)
and `src/pmcp/server.py:428` (`logger.error(f"Tool execution error: {e}")`) log the
unsanitised exception, while the return path one line later uses `_sanitize_error(e)`.
An httpx 401/403 stringifies the full URL, query string included. The reconnect path
(`manager.py:2859`) was fixed after the June review; these two were not. Log the
sanitised form.

---

## 4. Correctness and robustness findings

### C-01 · Server-initiated JSON-RPC requests (including `ping`) are never answered · **MEDIUM** · reproduced

**Where** `src/pmcp/client/manager.py:2690-2697` (stdio), `:2911-2919` (SSE/HTTP).

**Evidence** Both dispatchers resolve responses (`id` in `pending_requests`) and route
notifications (`id is None`); a frame with a `method` **and** a non-null `id` — a request
from the server to the client — falls through with a comment: *"Server->client requests
also carry a method but do have an id, and are not ours to handle here."* No code path
ever writes a `result` or `error` for such an id. Repro
(`scratchpad/verify/repro_c4.py`, fake stdio server sends
`{"jsonrpc":"2.0","id":99,"method":"ping"}` after initialize): `ping_sent: true,
got_reply: false, waited_s: 2.0`.

**Impact** MCP servers may send `ping` and expect a response; several close the session
when pings go unanswered. Servers that call `roots/list`, `sampling/createMessage` or
`elicitation/create` block forever waiting on PMCP, which then reports the *tool call* as
timed out. PMCP advertises `capabilities: {}` at initialize (`:3062-3066`), which is
consistent, but it should still answer.

**Fix** Reply `{}` to `ping`; reply JSON-RPC `-32601 Method not found` to everything
else; log at DEBUG. Add a fake-server test that sends a `ping` request.

**Effort** S.

### C-02 · A cancelled caller leaks its pending request · **MEDIUM** · reproduced

**Where** `src/pmcp/client/manager.py:2942-3019` (`_send_request`).

**Evidence** The `PendingRequest` is registered at `:2972` and removed only in
`except asyncio.TimeoutError` (`:3016-3019`). A `CancelledError` (the HTTP transport
cancels the handler task on `request_timeout`, `transport/http.py:656-661`), a
`BrokenPipeError` from `stdin.drain()`, or a failed `write_stream.send` all leave the
entry behind. Repro (`scratchpad/verify/repro_c5.py`, fake server that never answers
`tools/call`): after cancelling the `call_tool` task, `get_pending_requests()` still
holds `PendingRequest(request_id=5, tool_id='fake-hang::noop', future=<Future pending>)`.

**Impact** Phantom entries in `gateway.list_pending`, inflated `pending_request_count`,
and `disconnect_server`/`restart_server`/`refresh` refusing without `force=true` until
the read loop ends. If the downstream never answers, the entry lives until disconnect.

**Fix** `try/finally: managed.pending_requests.pop(request_id, None)` around the await;
add a test that cancels a `call_tool` task and asserts `get_pending_requests()` is empty.

**Effort** S.

### C-03 · One invalid UTF-8 byte kills a stdio server's read loop; the stderr drainer dies silently · **MEDIUM** (HIGH if it lands mid-connect) · reproduced

**Where** `src/pmcp/client/manager.py:2670` (`json.loads(line.decode())`), `:2661`
(`line.decode().strip()` in `_read_stderr`), `:2785-2801` (`_read_stdout` finally),
`:3768-3794` (`_check_server_health` checks only `returncode` for stdio).

**Evidence** `UnicodeDecodeError` is a `ValueError` but not a `json.JSONDecodeError`, so
it escapes `_handle_stdout_line` and ends `_read_stdout`. Two outcomes, both reproduced
(`scratchpad/verify/repro_c6b.py`, fake servers emitting `b"\xff\n"`):

| Bad byte arrives… | Result |
|-------------------|--------|
| after the server is ONLINE | status `error`, `last_error: stdout read error: 'utf-8' codec can't decode…`, reconnect scheduled — a full teardown for one stray Latin-1 byte |
| while still CONNECTING (e.g. right after `tools/list`) | connect **succeeds**, status stays `online`, `read_task.done() == True`, no reconnect; the next request (`tools/list`) times out at the ceiling — a zombie that the stdio health check never detects because it only looks at `process.returncode` |

In `_read_stderr` the same decode error ends the loop (`except Exception:
logger.debug`), after which stderr is never drained again; a chatty child then blocks on
a full pipe.

**Fix** `line.decode("utf-8", errors="replace")` in both readers; keep the stderr loop
alive on any per-line error; in `_read_stdout`'s `finally` treat a dead reader as a
disconnect regardless of status (or make `_check_server_health` inspect
`read_task.done()` for stdio as it already does for remote); test with `b"\xff\n"` at
both timings.

**Effort** S.

### C-04 · `gateway.cancel` and idle timeouts never notify the downstream · **MEDIUM** · confirmed by trace

**Where** `src/pmcp/client/manager.py:3839-3902` (`cancel_request`), `:1220-1237`,
`:3016-3019`.

**Evidence** Cancellation is `pending.future.cancel()` plus dict removal; nothing sends
`notifications/cancelled` (`requestId`) to the server.

**Impact** The downstream keeps running the tool (browser automation, long queries)
after the gateway has given up; resources stay busy and the next call queues behind it.

**Fix** Emit `notifications/cancelled` on `gateway.cancel`, on forced refresh/disconnect,
and on idle/ceiling timeout.

**Effort** S.

### C-05 · "Per-request" liveness is actually per-server · **MEDIUM** · confirmed by trace

**Where** `src/pmcp/client/manager.py:2754-2760`, `:2879-2882`, `:3021-3058`.

**Evidence** Any output from a server sets `last_heartbeat = now` on *every* pending
request of that server; `_await_with_idle_timeout` then keeps waiting.

**Impact** A hung call on a server that is also serving a chatty call cannot idle-time-out
until the 10-minute absolute ceiling. Comments and docs describe per-request liveness.

**Fix** Correlate progress notifications to requests via `progressToken`/`_meta`, or
document the semantics as per-server and lower the default ceiling for non-tool calls.

**Effort** M.

### C-06 · `gateway.tasks_list` without a server name fails whenever any known server is offline or task-less · **MEDIUM** · reproduced

**Where** `src/pmcp/tools/handlers.py:5954-5980`, `src/pmcp/client/manager.py:3431-3472`.

**Evidence** The handler iterates every name from `get_all_server_statuses()` (LAZY and
ERROR included) and calls `list_tasks(name, cursor)`; `_task_client` raises
`RuntimeError` for a server that is not connected or does not advertise `tasks`; the
handler's outer `except Exception` returns `ok=False`. The same `cursor` is sent to every
server and `next_cursor` is overwritten by the last one. Repro: a `ClientManager` with
one lazily registered server and nothing connected →
`tasks_list({})` returns `ok=False, errors=['Server lazy-one is not connected']`.

**Fix** Skip non-task/offline servers, aggregate per-server cursors (or require
`server_name` when a cursor is given), and add a test with one lazy and one task-capable
server.

**Effort** S.

### C-07 · Binary resources and non-text prompt content are silently dropped · **MEDIUM** · confirmed by trace

**Where** `src/pmcp/server.py:566-575` (`resources/read`, "Only text contents for now"),
`:636-648` (`prompts/get` flattens every message to `TextContent(text=content.get("text",""))`).

**Impact** A downstream image/PDF resource returns an empty list; a prompt with image or
embedded-resource content returns empty strings. `BlobResourceContents` is already
imported.

**Fix** Pass through `blob` contents and non-text prompt content types.

**Effort** S.

### C-08 · Resource-server validation requires `nbf` and allows no clock skew · **MEDIUM** · reproduced

**Where** `src/pmcp/auth.py:508-514`.

**Evidence** `options={"require": ["iss", "exp", "nbf", "aud"]}` and no `leeway`. Repro
§4: a well-formed RS256 token without `nbf` is rejected with *Token is missing the "nbf"
claim*. RFC 7519 makes `nbf` optional and most authorization servers (Auth0, many
Keycloak configs) omit it.

**Fix** Require `iss`/`exp`/`aud`; validate `nbf` when present; `leeway=30`.

**Effort** S.

### C-09 · The descriptions refresher re-spawns servers that are already connected, without their credentials · **MEDIUM** · confirmed by trace

**Where** `src/pmcp/manifest/refresher.py:344-360`, `src/pmcp/server.py:784-803`.

**Evidence** `refresh_server` builds `StdioServerParameters(command, args)` with no `env`
and opens a second `stdio_client` to list tools. `GatewayServer.initialize` calls
`refresh_all(servers=connected_names)` when no cache exists.

**Impact** Every cold start spawns a duplicate of each connected server (a second Chrome
for `@playwright/mcp`), credential-requiring servers fail the duplicate spawn and the
cache entry is skipped, and the gateway already holds the exact tool list it needs.

**Fix** Build the cache from the live `ClientManager` index at startup (as
`update_server` already does at `handlers.py:5363-5378`); when a spawn is unavoidable,
use `build_install_child_env()`.

**Effort** S.

### C-10 · Advertised tool schemas drift from the input models; unknown keys are silently ignored · **MEDIUM** · reproduced

**Where** `src/pmcp/tools/handlers.py:395-1033` vs `src/pmcp/types.py` input models.

**Evidence** Script (Appendix B) compared every `inputSchema` with the pydantic model the
handler validates with: `gateway.invoke` omits `task`, `meta`, `trace_context` (MCP task
execution is undiscoverable from the schema); the four `tasks_*` tools omit
`requestor_context`; no schema sets `additionalProperties: false` and no input model sets
`extra="forbid"`, so `{"options": {"redact_secret": true}}` or `time_out_ms` is accepted
and silently defaulted.

**Fix** Generate `inputSchema` from `Model.model_json_schema()` (one source of truth,
deletes ~640 hand-written lines) and set `extra="forbid"` on every `*Input` model with a
test that a typo is rejected. Also see M-02.

**Effort** M.

### C-11 · `print()` to stdout inside the server process · **MEDIUM** · confirmed by trace

**Where** `src/pmcp/config/guidance.py:169-170`, `src/pmcp/templates/code_snippets_loader.py:63`,
`src/pmcp/manifest/code_patterns_loader.py:66`.

**Impact** In stdio transport stdout is the JSON-RPC channel (`server.py:890`). A
malformed `~/.claude/gateway-guidance.yaml` prints two warning lines into it during
`GatewayServer.__init__`, while the client is already reading.

**Fix** `logger.warning` everywhere outside `cli.py`; add a stdio boot test with a broken
guidance file asserting stdout stays pure JSON-RPC; consider a lint rule (`T201`) scoped
to `src/pmcp` minus `cli*`.

**Effort** S.

### C-12 · `pmcp status` / `pmcp auth connect` cannot authenticate to an authenticated gateway · **MEDIUM** · confirmed by trace

**Where** `src/pmcp/cli.py:1059-1077` (`_query_running_gateway_status`), `:2444-2463`
(`_build_gateway_auth_client`).

**Evidence** Both build `RemoteMcpServerConfig(type="streamable-http", url=probe_url)` with
no `headers`; `PMCP_AUTH_TOKEN` is read only by `run_server`. `pmcp doctor` even advises
"supply credentials (PMCP_AUTH_TOKEN …)" for this case.

**Impact** Against a `shared-secret` or `resource-server` gateway (the documented shared
deployment), `pmcp status` reports the gateway unreachable and `pmcp auth connect` fails
with 401.

**Fix** Attach `Authorization: Bearer ${PMCP_AUTH_TOKEN}` (or a `--token`/`--token-file`)
to the probe config; document it.

**Effort** S.

### C-13 · `truncate_output` under-flows for caps below 100 bytes · **LOW** · reproduced

`src/pmcp/policy/policy.py:317-335`: `encoded[: max_size - 100]` with `max_size < 100`
is a negative slice, so the function returns everything *except* the last `100 − cap`
bytes:

| `max_size` | returned bytes (100 000-byte input) |
|-----------|-------------------------------------|
| 40 | 99 994 |
| 96 | 100 050 (larger than the input, after the marker) |
| 100 | 55 (correct) |

Not reachable through `gateway.invoke` (`max_output_chars` is clamped `ge=100` in
`types.py:741`, i.e. ≥ 400 bytes), but `LimitsPolicy.max_output_bytes` has no lower
bound, so an operator who sets a strict cap below 100 silently gets the whole output
back. Clamp with `max(0, …)` and give the policy field `ge=100`.

### C-14 · Redaction defaults differ between paths · **LOW**

`handlers.py:1979-1983` redacts task results by default only when *no* options object is
passed; `:6106` (`tasks_result`) defaults to `True`; `invoke` defaults to `False`.
Pick one default per surface and document it.

### C-15 · Version lookups are cached for the process lifetime · **LOW**

`src/pmcp/manifest/version_checker.py:22, 1387-1547`: `_version_cache` has no TTL and
`clear_version_cache()` has no production caller, so a long-running gateway's
`gateway.update_server` reports the first-seen `latest_version` forever. Add a TTL.

### C-16 · Smaller correctness items · **LOW**

- `handlers.py:3223-3233` `_lifecycle_output` audits with `started_at=time.monotonic()`,
  so `latency_ms` is always ~0 for lifecycle events.
- `handlers.py:1258-1262` `_sanitize_error` strips *every* `/path` including URL paths
  (`https://api.x.com/v1/items` → `https:items`), destroying diagnostics.
- `manager.py:3065` initialize `clientInfo` is `{"name": "mcp-gateway", "version": "1.0.0"}`;
  `refresher.py:39` `GATEWAY_VERSION = "1.0.0"`; use `pmcp.__version__`.
- `errors.py:110` E502's suggestion still says "in .env".
- `handlers.py:1329-1337` the provisioned registry is written non-atomically with default
  permissions; `config/loader.py:522-530` and `cli.py:1739-1748` `_atomic_write_json`
  create the temp file with mode 0600 and so silently change a user's `.mcp.json` mode.
- `config/loader.py:741-751` tool/resource ids use `::` with no validation; a downstream
  tool named `a::b` yields an id `parse_tool_id` cannot parse (`parse_tool_id` has no
  callers today).
- `cli.py:2699-2702` `main()` loads `.env.pmcp` from `Path.cwd()` only, while
  `--project` may point elsewhere (*verify with a boot test*).
- `manager.py:1024` `zip(configs, results)` without `strict=`; `:2962`
  `asyncio.get_event_loop()` inside a coroutine; `:3697` `_health_task` created via
  `hasattr` instead of `__init__`.

---

## 5. Performance and efficiency findings

Measured on this sandbox (Python 3.11, no C-accelerated YAML in use by the code).

### P-01 · `load_manifest()` is uncached, slow, and called up to ~15 times per request · **HIGH** · measured

**Where** `src/pmcp/manifest/loader.py:754-837`; 22 call sites in `src/pmcp`.

**Evidence**

| Measurement | Value |
|-------------|-------|
| `load_manifest()` wall time (107 servers, 78 KB YAML) | **198 ms** avg over 10 calls |
| same YAML via `yaml.SafeLoader` | 197 ms |
| same YAML via `yaml.CSafeLoader` (available in the venv) | **22 ms** |

Call fan-out for one `gateway.catalog_search(query, include_offline=true)`:
`catalog_search` (1 direct + 1 more for manifest candidates) → `_load_configured_servers`
→ `load_configs` → `load_manifest` (1) → `_manifest_candidates_for_query` → per
candidate (≤5) `_auth_env_options` → `load_manifest` → `_registry_candidates_for_query`
→ per registry match (≤8) `_auth_env_options` → `load_manifest`. Each call also walks up
from cwd looking for `.pmcp/manifest.yaml` and logs at INFO ("Loading manifest from …").
Every lifecycle, provision, auth_connect, refresh and config_status call pays one to
three loads; `load_configs` (called for every request that touches configuration) pays one.

Measured with a counting wrapper on `load_manifest` and the registry fetch stubbed out:

| `gateway.catalog_search(query="github", include_offline=true)` | Value |
|------------------------------------------------------------------|-------|
| `load_manifest()` calls | 4 |
| one call | 251 ms |
| wall time of the whole search | 726 ms |

So even the simple path spends most of its latency re-reading the same YAML.

**Impact** Roughly 0.7 s of pure CPU on the event loop for an ordinary search and up to
~3 s on the worst path, blocking every other client of a shared gateway; log spam.

**Fix** Cache the parsed `Manifest` keyed on `(shipped path mtime, overlay paths and
mtimes, PMCP_MANIFEST_PATH)`, invalidated on `gateway.refresh`; use
`yaml.CSafeLoader if available else SafeLoader`; pass the manifest down instead of
reloading in `_auth_env_options` / `_configured_duplicate_missing_credential` /
`_get_server_config_for_update`. Expected: ~200 ms → ~0 ms on the hot path.

**Effort** S.

### P-02 · The lifecycle lock is held across an entire connect, including retry sleeps · **MEDIUM** · reproduced

**Where** `src/pmcp/client/manager.py:1150-1200` (`ensure_connected` →
`_connect_with_lifecycle_lock`), `:1362-1382` (`_connect_with_retry`: three attempts,
two backoff sleeps of 1 s and 2 s — `RETRY_DELAYS` lists 4 s too but it is never
reached), `:2848-2855` (`_reconnect_loop`); the same lock guards `connect_server`,
`disconnect` and `refresh` (`:2848, 3110, 3245`).

**Evidence** Two lazy servers, one whose command is `/usr/bin/false` and one healthy
fake stdio server. `ensure_connected` on the healthy one, started 50 ms after the
failing one:

| | Wall time |
|-|-----------|
| healthy server, uncontended | 0.31 s |
| failing server (three attempts + backoff) | 3.26 s |
| healthy server while the failing one retries | **3.21 s** (10×) |

**Impact** One slow or failing lazy start serialises every other lazy start, disconnect,
refresh and reconnect for the whole gateway; a command that hangs instead of exiting
holds the lock for the full connect timeout per attempt. The `max_concurrent_spawns`
semaphore only helps eager `connect_all`.

**Fix** Per-server locks for connect/disconnect plus a short global lock around index
mutation and registration; keep the global lock only for `refresh`/`disconnect_all`.

**Effort** M.

### P-03 · Blocking I/O on the event loop · **MEDIUM** · confirmed by trace

| Where | What blocks |
|-------|-------------|
| `handlers.py:4860, 4890` | `urllib.request.urlopen` (5 s + 10 s timeouts) inside `submit_feedback` |
| `handlers.py:2936-2944` | up to three `load_dotenv` file reads per availability check, per candidate |
| `handlers.py:1316-1337, 2535, 2636` | provisioned-registry JSON read on every `health`/`config_status`/`refresh` |
| `handlers.py:3049`, `registry.py:656-671` | registry cache JSON parse (~2000 entries) plus rebuilding normalised haystacks on every query |
| `npm_resolver.py:372, 503` | synchronous child handshake (up to 10 s) and query (1 s) under a `threading.Lock`, called from coroutines |
| `manifest/loader.py:754` | P-01 |
| `cli.py:1509, 1638, 2168` | `open()`/`subprocess.run` in async functions (ruff `ASYNC230`/`ASYNC221`) |

**Fix** `asyncio.to_thread` for file/subprocess work that must stay synchronous;
`aiohttp` for the GitHub calls; in-memory caches with mtime checks for the small JSON
files; precompute registry haystacks once per cache load.

**Effort** M.

### P-04 · Registry lookups hit the network on every query when there is no on-disk cache, and failures are never cached · **MEDIUM** · confirmed by trace

**Where** `src/pmcp/tools/handlers.py:3047-3070`, `src/pmcp/manifest/registry.py:582-649`.

**Evidence** `_load_registry_candidates` falls back to `fetch_registry_servers` (5 s
total timeout; an incremental attempt then a full one) whenever `load_registry_cache()`
is empty; `fetch_registry_servers` deliberately does not cache a failed fetch.

**Impact** On an offline host, or before the user ever runs a registry sync, every
`catalog_search` with a query and every `request_capability` that reaches the registry
tier blocks for up to ~10 s.

**Fix** Negative cache with a short TTL (30–60 s); warm the cache in the background at
startup; surface `registry_fetch_failed` in `gateway.health`.

**Effort** S.

### P-05 · Startup and per-call overheads · **LOW**

- **CLI import time** (`python -X importtime -c "import pmcp.cli"`): 575 ms total;
  `pmcp.auth` 386 ms of which `aiohttp` 151 ms and `pmcp.types` 200 ms (pydantic model
  building). Every stdio session spawned by a client pays this. Lazy-import `aiohttp`/`jwt`
  (only needed for resource-server mode, registry and version lookups).
- **`tools/list` payload**: 26 tools, ~13 KB / ~3.3 k tokens of definitions. For a
  product whose pitch is context economy this is worth trimming (shorter descriptions;
  a tiered surface, see M-11).
- `policy.py:355-396` `process_output` does `json.dumps(indent=2)` + `json.loads` on
  every non-string result even when nothing was truncated or redacted, and measures
  `raw_size` on the pretty-printed form.
- `server.py:296-308` `jsonschema.validate()` re-checks the schema on every call;
  precompile one `Draft202012Validator` per tool at import.
- `transport/http.py:589-592` parses the body once for the method and the session manager
  parses it again (up to 10 MB).
- `auth.py:465-476` `PyJWKSet.from_dict` rebuilds every key object per request.
- `manager.py:3634-3654` `get_all_tools()` sorts the whole catalog on each call; used per
  `catalog_search`, `tools/list`, `provision` and `refresh`.
- `handlers.py:1148-1177` CLI probing (12 subprocesses) is cached forever per process —
  fine for cost, wrong for freshness (a CLI installed later is never seen).

---

## 6. Maintainability and architecture

### M-01 · Three files carry half the codebase · **MEDIUM**

`tools/handlers.py` 6 257 lines (one class, 60+ methods, 26 tool bodies plus 640 lines
of hand-written schemas), `client/manager.py` 3 902, `cli.py` 2 724 (of which
`parse_args` alone is ~670). Proposed split, keeping public import paths via re-exports:

```
pmcp/tools/
  definitions.py     get_gateway_tool_definitions (or generated, see M-02)
  base.py            GatewayTools core: __init__, _audit, _sanitize_error, trace helpers
  catalog.py         catalog_search, describe, _get_cached_tools_for_offline_servers, manifest/registry candidates
  invoke.py          invoke, cancel, list_pending
  lifecycle.py       refresh, health, config_status, startup policy, connect/disconnect/restart, _resolve_lifecycle_config
  provisioning.py    request_capability, provision, provision_status, _finalize_*, register_discovered_server, search_registry
  auth.py            auth_connect, credential helpers (_check_api_key_available, _auth_env_options, ...)
  tasks.py           tasks_list/get/result/cancel, task sanitisation
  feedback.py        submit_feedback, _build_feedback_issue, telemetry helpers
  updates.py         update_server, _detect_effective_version_pin, _run_update_probe_command
pmcp/client/
  process.py         _terminate_process_tree, stdio spawn/read loops
  transport.py       remote transport ownership (_own_remote_transport, _close_remote_transport)
  catalog.py         parse/index/reconcile helpers (_parse_*_entries, _reconcile_once, ...)
  manager.py         ClientManager façade
pmcp/cli/
  args.py, server.py, status.py, setup.py, doctor.py, secrets.py, auth.py, upgrade.py
```

`GatewayTools` would become a thin façade composing the mixins/modules; each module can
then own its tests. The 26-branch `if/elif` dispatch in `server.py:337-392` becomes a
`dict[str, Callable]` populated by the modules.

### M-02 · Tool schemas are hand-written twice · **MEDIUM**

`handlers.py:395-1033` duplicates what pydantic already knows (see C-10). Generate with
`Model.model_json_schema(mode="validation")`, post-process descriptions, and add a test
that the generated schema equals the served one. This also makes `additionalProperties`
and enums impossible to forget.

### M-03 · Duplicated helpers with diverging behaviour · **MEDIUM**

| Duplicate | Locations | Divergence |
|-----------|-----------|------------|
| tag extraction | `client/manager.py:455-476`, `manifest/refresher.py:151-171` | different keyword sets |
| risk inference | `client/manager.py:426-452` (word-boundary, honours annotations), `manifest/refresher.py:174-195` (substring) | offline tools get inflated risk (C-09) |
| `_atomic_write_json` | `config/loader.py:522`, `cli.py:1739` | identical |
| `_env_int` | `server.py:84` (comment says copied from `transport/http.py`, which no longer has one) | — |
| secret keyword sets | `auth.py:44-88`, `policy.py:20-29`, `handlers.py:389-392` (`TRACE_VALUE_DENY_PATTERN`), `validation.py:70-73` | four lists, four behaviours |
| loopback checks | `auth.py:119`, `transport/http.py:182`, `registry.py:143` | three implementations |
| project-root walk | `config/loader.py:216`, `manifest/loader.py:617` | second copy exists to avoid an import cycle (M-06) |

### M-04 · Dead code · **LOW**

`identity.get_own_identity`, `auth.fetch_json_metadata` (kept for an "interface freeze"
with no production caller, per its own docstring), `installer.install_server`,
`installer.verify_installation`, `JobManager.cleanup_old_jobs` (so `_jobs` grows for the
process lifetime), `refresher._escape_yaml_string`, `refresher._indent_multiline`,
`config.loader.parse_tool_id`. Delete or wire up.

### M-05 · Module-level mutable state and singletons · **MEDIUM**

`transport/http._rl_store`, `_metrics`, `_prom_counters`; `manifest/registry._IN_PROCESS_CACHE`,
`_IN_PROCESS_TASKS`; `version_checker._version_cache`; `installer.JobManager` (`__new__`
singleton); `npm_resolver._resolver`; `identity._LOCK_FD`. These make two gateway objects
in one process share state, complicate tests (fixtures must remember to reset each one),
and hide lifecycle (nothing closes the npm resolver child on shutdown). Hang them off
`GatewayServer`/`ClientManager` (or an explicit `Runtime` object) and thread them through.

### M-06 · Import cycle · **LOW**

`config.loader → remote_auth → env_store → config.loader`, broken only by function-level
imports in `remote_auth.py:85,110,135` and `config/loader.py:697,1021,1089,1147`. Move
`find_project_root` into a leaf module (`pmcp/paths.py`) and the cycle disappears, along
with the duplicated walk in `manifest/loader.py`.

### M-07 · Error handling and lint posture · **MEDIUM**

125 `except Exception` sites (manager 30, handlers 21, cli 11, installer 9), 17
`try/except/pass`. Many are deliberate isolation points, but the blanket form also
swallows programming errors (a `TypeError` in a parser becomes "skipped unparseable
tool"). Adopt `BLE001`/`S110` as *warnings* first, then narrow the catches that guard
untrusted input to `(ValueError, TypeError, KeyError)` plus a logged fallback. Add
`[tool.ruff.lint] select = ["E","F","B","ASYNC","BLE","DTZ","S","UP","SIM","RUF"]` with
per-file ignores, and move mypy toward `--strict` (41 errors today, all mechanical).

### M-08 · Hard-coded versions and stale identity strings · **LOW**

`"1.0.0"` in `manager.py:3065` and `refresher.py:39`; `github.com/ViperJuice/pmcp` in
`version_checker.py:24`, `handlers.py:4799`, `server.json`, `agent-manifest.yaml`,
`catalog-info.yaml`, `CONTRIBUTING.md`.

### M-09 · Comment style · **LOW** (opinion)

Many comments cite internal process artefacts ("ah board review, red-team seat",
"IF-0-P3B-2", "EC-UPDPATH-2", "board review finding 1") and reproduce the reasoning of a
past PR at length (some functions have 40-line docstrings of history). The rationale is
valuable; the process references are noise for anyone outside the phase plans. Keep the
*durable* reason ("must not read os.environ because …") and move the history to
CHANGELOG/plans links.

### M-10 · Repository hygiene · **LOW**

- `plans/` (65 files, 1.4 MB), `.consiliency/` (33), `specs/` (15), `diagnostics/`,
  `.opencode/`, `.dev-skills/` (gitignored yet two files tracked), `generated/` and
  `config/` (empty), `team_announcement.md`, `.pr_details.md` in the root. Move planning
  material under `docs/` (or a wiki) with an index, or at least out of the package root.
- `server.json` says version `0.1.0`; `agent-manifest.yaml` references `baml_src/`, and
  `.env.example`/`.mcp.json.example` still describe a `GROQ_API_KEY` for BAML matching
  that no longer exists (the README's "no API key needed" is correct).
- `.pmcp/logs` and `.mcp-gateway/` are created relative to the current working
  directory (`cli.py:44`, `server.py:128`), so running the gateway from a repo drops state
  into it; prefer XDG state/cache dirs.

### M-11 · Product-level: tier the meta-tool surface · **MEDIUM** (design)

Twenty-six meta-tools (3.3 k tokens) are now themselves a context cost, and the
admin/provisioning tools are the risky ones (S-01, S-04). A `gateway.admin` capability
gated by policy (default off in shared mode) with the six core tools
(`catalog_search`, `describe`, `invoke`, `health`, `list_pending`, `cancel`) always on
would shrink context and the attack surface at once.

### M-12 · Things the architecture gets right (keep)

- Fetch-then-swap catalog reconciliation with per-kind failure isolation and page caps.
- Transport ownership in a dedicated task for anyio cancel-scope correctness.
- Fail-closed credential relaxation (`api_key_optional_when`) and the npm identity
  resolver's tri-state design.
- The subscription sink's lost-wakeup analysis.
- Policy fail-closed on a parseable-but-invalid file (#202) and bounded limits (#207).
- The scoped-advisor audit channel (exclusive create, sequence numbers, fsynced terminal
  marker, hashes instead of raw values).

---

## 7. Tests, CI, packaging and documentation

The suite is large (79 files, ~50 k lines, 3 100+ tests, 85 % coverage) and, unusually,
carries mutation evidence for its guards. The items below are about hermeticity,
environment coupling, and the specific gaps this review found. Items T-06 to T-11,
CI-01 to CI-04 and D-05/D-06 were established by a targeted audit pass (commands and
counts quoted).

### T-01 · A unit test reaches the public npm registry · **MEDIUM** · observed

`tests/test_refresher.py::TestShortCircuitUsesCompareVersions::test_short_circuit_is_a_single_compare_versions_call`
took **70.9 s** in the baseline run and its captured stderr shows
`npm error request to https://registry.npmjs.org/srv failed`. When npm identity
resolution refuses (as it does whenever `NODE_OPTIONS`/`npm_config_*` are set),
`refresh_server` skips the freshness short-circuit and spawns the real
`npx -y srv` through `stdio_client`. The test is not marked `live`. Any test that reaches
`refresh_server` without patching `stdio_client`/`get_package_version` has the same
exposure. Fix: patch the spawn in the fixture, and add an autouse guard that fails any
non-`live` test which opens a network socket (`pytest-socket`, or a `socket.socket`
monkeypatch).

### T-02 · The runtime harness hard-depends on host binaries · **LOW** · observed

`tests/test_credential_boot.py:58-85` shells out to `ss`, `pgrep` and `curl`; on a host
without iproute2 every `tests/runtime/*` test errors with `FileNotFoundError: 'ss'`
(9 failures + 23 errors here). Skip with a clear reason when `shutil.which` fails, or
use `psutil`/`/proc/net/tcp`.

### T-03 · Environment coupling of the npm identity tests · **LOW** · observed

~100 tests fail whenever the *test runner's* environment carries `NODE_OPTIONS` or any
`npm_config_*` variable, because the resolver's process gate is deliberate. The tests
should scrub those variables in a fixture (they already know the gate exists —
`TestARefusalNeverReachesTheTables`) so a developer's shell or a CI image setting
`NODE_OPTIONS=--max-old-space-size` does not turn the suite red.

### T-04 · Tests that would have caught this review's findings · **MEDIUM**

None of the following has a test today (grep-verified):

| Missing test | Finding |
|--------------|---------|
| HTTP shared-secret with a non-ASCII `Authorization` byte returns 401 | S-09 |
| Protected-resource metadata `resource` equals the configured audience regardless of `Host` | S-10 |
| JWKS refetch count under N unknown-`kid` tokens is bounded | S-07 |
| `os.environ` unchanged across `catalog_search`/`request_capability`/`provision` | S-02 |
| `write_env_file` refuses a symlinked target and survives a crash mid-write | S-05 |
| A policy allowlist does not permit provisioning an allowlisted *name* bound to an arbitrary package | S-01 |
| Redactor leaves "status code 200" alone and catches AKIA/xoxb/AIza/sk_live/glpat/PEM shapes | S-12 |
| Downstream `ping` request receives a response | C-01 |
| Cancelling a `call_tool` task removes its pending entry | C-02 |
| A `b"\xff\n"` stdout line, mid-connect and post-connect, does not zombie or disconnect the server | C-03 |
| `notifications/cancelled` is sent on `gateway.cancel` | C-04 |
| `tasks_list` with one lazy and one task-capable server succeeds | C-06 |
| `resources/read` of a blob resource returns it | C-07 |
| RS256 token without `nbf` is accepted | C-08 |
| Stdio boot with a malformed guidance file keeps stdout pure JSON-RPC | C-11 |
| `pmcp status` against a shared-secret gateway succeeds with `PMCP_AUTH_TOKEN` | C-12 |
| `load_manifest` is called at most once per `catalog_search` | P-01 |
| `release.yml` `with:` inputs are part of the workflow guard's allowlist | CI-01 |

### T-05 · Suite structure · **LOW**

`tests/test_tools.py` (7 128 lines) and `tests/test_client_manager.py` (6 394) mirror the
two largest source files and would split naturally with M-01. CI runtime is dominated by
the credential-optionality e2e tests (10–12 s each), `test_shutdown_handles_timeout`
(10 s) and the wall-clock sleeps in T-06; `pytest-xdist` is worth trying once
T-01/T-03/T-09 are hermetic.

### T-06 · Multi-second wall-clock sleeps used as synchronisation · **MEDIUM** · confirmed

`grep -rEn "asyncio\.sleep\(|time\.sleep\(" tests/` → 82 calls, 65 with a non-zero
duration. Representative: `tests/runtime/test_subscriptions_e2e.py:212`
`await asyncio.sleep(13)` followed by `assert elapsed > 12`;
`tests/mcp2x/test_listen_over_http.py:234` `sleep(8.2)` then `assert elapsed > 8`;
`tests/test_transport_http.py:448` `sleep(10.0)` inside a mock ASGI handler;
`tests/test_manifest_provision.py:295` `sleep(2)` inside a 120 s polling loop;
`tests/runtime/harness.py:153` `time.sleep(0.5)` × 60. These deliberately prove that a
timeout exemption outlives the wrapper, but each one costs its full duration on every
run. Inject the timeout (e.g. `request_timeout=0.2`) so the proof takes milliseconds.

### T-07 · Flaky-prone timing assertions · **MEDIUM** · confirmed

`tests/test_client_manager.py:2176` `assert elapsed < 0.2` for three simulated 0.1 s
connections leaves no slack for a loaded CI runner;
`tests/test_npm_resolver.py:807-816` asserts `0.5 < first_elapsed < 5.0` and
`rest_elapsed < 0.5` against a real spawned Node child. Assert on ordering or on mocked
clocks, not on wall-clock budgets.

### T-08 · A non-`live` test file reads the developer's real home configuration · **MEDIUM** · confirmed

`tests/test_progressive_disclosure.py:30-42` calls `Path.home()` with no `monkeypatch`
in the file; its skip guards (`skip_no_playwright`, line 53) are evaluated against the
real `~/.claude/.mcp.json` at collection time and the file is not marked `live`
(only `test_integration.py`, `test_manifest_provision.py` and `test_credential_boot.py`
are). On a contributor's machine with Playwright/Context7 configured, a plain `pytest`
run performs real `ClientManager.connect_all()` against real servers. Compare
`tests/test_manifest_overlay.py:15-31`, which isolates `Path.home` correctly.

### T-09 · Module-level globals are never reset by any fixture · **MEDIUM** · confirmed

`tests/conftest.py` has **no** autouse fixtures. `manifest.registry._IN_PROCESS_CACHE`
(300 s TTL, keyed by endpoint) has 0 references in `tests/`;
`npm_resolver.reset_resolver_for_tests()` (docstring: "Tests only") has 0 callers;
`transport.http._rl_store.clear()` appears in exactly two individual tests while at least
six tests across two files populate it with different `rate_limit_rpm` values;
`JobManager._instance = None` is reset ad hoc in two files. Order dependence is
currently benign by luck. Add one autouse fixture that resets every global in M-05 (or,
better, remove the globals).

### T-10 · A permanently disabled test · **LOW** · confirmed

`tests/test_manifest.py:54-56` `@pytest.mark.skipif(True, reason="Platform detection
depends on actual environment")` — `detect_platform()` therefore has no coverage.
Parametrise `platform.system()` and `/proc/version` instead.

### T-11 · Small hermeticity items · **LOW**

- `tests/test_credential_boot.py:40` hardcodes `SPARE_PORT = 38345`, coordinated only by
  a comment with a sibling script; every other socket test uses an ephemeral port
  (`harness.alloc_port()`). Two concurrent CI jobs on one host could collide.
- Two tests assert only on mock call arguments (`test_client_manager.py:1500-1539`,
  transport-dispatch routing) — acceptable for routing, brittle to renames.
- `os.environ` handling in tests is clean (no direct assignment without restore).

### CI-01 · The release-workflow guard never inspects `with:` inputs · **MEDIUM** · confirmed

`scripts/check_workflows.py` (682 lines) contains no reference to `"with"`;
`_collect_uses` (lines 148-156) extracts only the `uses:` string and `EXPECTED_USES`
(lines 72-89) pins `owner/repo@sha`. A PR that leaves every pinned `uses:` line intact
but edits the `with:` block of `pypa/gh-action-pypi-publish` or `actions/checkout`
(`repository-url`, `packages-dir`, `persist-credentials`, `ref`) passes the SHA-pin,
allowlist, permissions, timeout and job-set-drift checks — on the one workflow that
holds `id-token: write`. The `release-diff-ack` label job is the only thing standing in
the way, and it is a human acknowledgement, not a check. Include `with:` (and `env:`)
in the release allowlist, or hash the entire step.

### CI-02 · In-repo npm install without `--ignore-scripts` under a secret-bearing job · **MEDIUM** · confirmed

`.github/actions/pipeline-bootstrap-setup/action.yml:13` runs
`npm ci --prefix scripts/pipeline-bootstrap` (package.json and lockfile live in this
repository) while the same workflow installs an external tree with
`npm install --ignore-scripts` (`pipeline-bootstrap.yml:105`). The job holds
`id-token: write`, `contents: write` and nine secrets (`SUPABASE_SERVICE_ROLE_KEY`,
`BOOTSTRAP_CLONE_TOKEN`, `WORKER_API_KEY`, …). It is reachable only by
`workflow_dispatch`/`repository_dispatch` (no `pull_request_target` anywhere — checked),
so a fork PR cannot trigger it, but a merged change to `scripts/pipeline-bootstrap/package.json`
would run its lifecycle scripts with those credentials. Use `--ignore-scripts` here too,
and drop `contents: write` (nothing in `run.mjs` pushes to this repository).

### CI-03 · Small workflow hardening items · **LOW**

- `pipeline-bootstrap.yml:154-158, 189-193` write `toJson(github.event.client_payload)`
  into `$GITHUB_OUTPUT` between a fixed heredoc delimiter (`PAYLOAD_EOF_BOOTSTRAP`); use
  a per-run random delimiter as GitHub recommends.
- `run.mjs` passes `target_branch`/`repository_full_name` to `git` as positional argv
  (no shell, good) without validating them against a ref/`owner/name` pattern.
- The job-rename bypass of `job_set_drift` does **not** exist (a rename shows up as a
  removal, `check_workflows.py:674`); noted so nobody re-audits it.

### CI-04 · Matrix, thresholds and scanners · **LOW**

- Linux-only matrix while `pyproject.toml` claims macOS and Windows and the code carries
  Windows branches (`identity.py`, `_terminate_process_tree`); add a `windows-latest`
  smoke job. Python 3.13/3.14 absent from matrix and classifiers.
- `coverage --fail-under=60` versus an actual 85 %; ratchet to 80.
- No dependency scanning; add `pip-audit` (D-01). Ruff runs with the default rule set and
  mypy non-strict (M-07).
- The release workflow re-runs the suite on tag but never checks that the tag matches
  `pyproject.toml`'s version; a mismatched tag publishes the wrong version.
- The `changelog` guard (live label lookup, fail-closed) is well designed; keep it.

### Packaging · clean

`uv lock --check` passes; no syntax above the 3.10 floor is used (`match`, `tomllib`,
`ExceptionGroup`, `typing.Self`, `StrEnum` all absent); `py.typed` ships; the wheel
contains every non-Python asset (`_npm_resolve.js`, `manifest.yaml`,
`code_patterns.yaml`, `code_examples.yaml`, `code_execution_guide.md`).

### D-01 · Locked dependencies with published advisories · **MEDIUM**

`pip-audit` on `uv export --frozen` (2026-09-01):

| Package | Locked | Advisories | Fixed in | Relevance to PMCP |
|---------|--------|-----------|----------|-------------------|
| aiohttp | 3.13.2 | ~30 (parser smuggling, DoS, cookie/redirect leaks, SNI reuse) | 3.13.4 / 3.14.x | client for JWKS, registry, version lookups |
| starlette | 0.50.0 | Host-header / `request.url` reconstruction, form limits | 1.0.1 / 1.3.x | serves `/mcp`; `request.url_for` is used for the PRM document (S-10) |
| pyjwt | 2.10.1 | `crit` header unvalidated; PyJWK alg-allowlist bypass (not reachable: PMCP passes raw keys); PyJWKClient issues (not used) | 2.12 / 2.13 | resource-server token validation |
| cryptography | 46.0.3 | name-constraint and buffer issues | 46.0.5+ | JWT signatures |
| python-dotenv | 1.2.1 | `set_key` follows symlinks (PMCP uses only `dotenv_values`/`load_dotenv`) | 1.2.2 | — |
| python-multipart, pygments, click, pytest | — | minor | — | transitive/dev |

There are no open Dependabot PRs, so the weekly `pip` entry is not keeping `uv.lock`
current for transitive pins (`uv lock --check` passes because the lock matches the
*declared* bounds, not because it is fresh). Run `uv lock --upgrade` and add
`pip-audit` to CI.

### D-02 · Docker image and compose file do not match the documented deployment · **LOW**

- `Dockerfile`: `ENTRYPOINT ["pmcp"]` with no `[http]` extra, so the container can only
  run stdio mode, while the README recommends shared HTTP mode for services;
  `uv.lock` is copied but `uv pip install --system .` ignores it (non-reproducible
  builds); `HEALTHCHECK` is commented out; the non-root user's `~/.pmcp` lock dir and the
  cwd-relative `.pmcp/logs` are not declared volumes.
- `docker-compose.yml` mounts `./.env` to `/app/.env`, but `load_dotenv()` in `cli.main`
  searches upward from the *installed package directory*, not the working directory
  (`python-dotenv` `find_dotenv` semantics, verified in the vendored source), so that
  file is only read through the S-02 side-effect path. Mount `pmcp.env` to
  `/home/appuser/.config/pmcp/pmcp.env` instead and document it.

### D-03 · Stale metadata and examples · **LOW**

- `server.json` declares `version: 0.1.0` and `ViperJuice` URLs (registry publication
  metadata is wrong for 2.7.3).
- `agent-manifest.yaml` points `prompts.source` at `baml_src/` and `generated/` (BAML
  was removed); `.env.example` and `.mcp.json.example` still ask for `GROQ_API_KEY`; the
  README correctly says no key is needed.
- `team_announcement.md` ("9 stable meta-tools", "25+ servers") and `.pr_details.md` are
  stale root files.
- `CONTRIBUTING.md` clones `ViperJuice/pmcp` and says "only Playwright and Context7 are
  auto-start servers" (auto-start now comes from `autoStart` in `.mcp.json`; the manifest
  flag is legacy behind `PMCP_LEGACY_MANIFEST_AUTOSTART`).
- `SECURITY.md` "Supported Versions" lists `2.0.x` as the active line; current is 2.7.x.

### D-04 · README vs code · **LOW**

- The README's tool count (26) matches the code. Its `.env` guidance should say
  explicitly that PMCP reads `.env.pmcp` / `~/.config/pmcp/pmcp.env` and *not* `.env`
  (today it accidentally does, S-02).
- Environment variables read by `src/pmcp` but never mentioned in the README (grep of
  both): `PMCP_TRANSPORT`, `PMCP_HOST`, `PMCP_PORT`, `PMCP_LOG_LEVEL`,
  `PMCP_REQUEST_TIMEOUT`, `PMCP_MAX_SPAWNS`, `PMCP_AUDIT_JSONL`, `PMCP_FEEDBACK_REPO`,
  `PMCP_FEEDBACK_TOKEN`, `PMCP_STATUS_SSE_URL` (a legacy alias of `PMCP_GATEWAY_URL`).
  Conversely the README still documents the retired `PMCP_KEEPALIVE_MAX_SECONDS` and
  `PMCP_MAX_KEEPALIVE_STREAMS`. Generate the env-var reference from `cli.py`'s
  `parse_args` help strings (each already names its env var) so it cannot drift.
- `SPEC_COMPLIANCE.md` names its evidence tests and all 21 references resolve; keep that
  habit.

### D-05 · Two CLI subcommands and several flags are undocumented · **MEDIUM** · confirmed

`pmcp --help` lists `auth` and `upgrade` and its own examples show `pmcp upgrade`, yet
the README has no section for either (`pmcp auth connect --credential/--env-var/--scope/
--no-provision/--json`; `pmcp upgrade --method/--restart-service/--dry-run`).
`pmcp secrets check`, `status --pending/--probe`, the top-level `--auth-token-file`,
`--max-concurrent-spawns`, `--request-timeout`, `doctor --timeout` and
`secrets set --stdin` are likewise absent. Generate the CLI reference from argparse.

### D-06 · CONTRIBUTING's module table is a v1 snapshot · **LOW**

Eight modules listed, all real; roughly 24 absent, including whole subsystems (`auth.py`,
`remote_auth.py`, `cli_commands/*`, `subscriptions.py`, `transport/http.py`,
`manifest/registry.py`, `manifest/refresher.py`, `manifest/version_checker.py`,
`manifest/npm_resolver.py`, `identity.py`, `env_store.py`, `scoped_advisor_audit.py`).

---

## 8. What is done well

- **Security regressions are turned into structure, not just fixes.** The credential
  relaxation contract (`api_key_optional_when`) fails closed at seven gates with an e2e
  matrix; the policy loader refuses to start on a parseable-but-invalid file; the npm
  identity resolver refuses rather than guesses; the auth URL classifier enumerates every
  IPv4-embedding IPv6 format with its RFC.
- **Downstream catalog handling is careful**: fetch-then-swap reconciliation, per-entry
  parse isolation, per-kind failure isolation, pagination caps, duplicate-id accounting,
  and an AST guard that keeps the publisher wiring honest.
- **Process lifecycle**: process-group kill with escalation, transport ownership in a
  dedicated task (anyio cancel scopes), tracked background tasks, storm-guarded
  reconnects, bounded stdout lines that fail one request instead of the server.
- **Operational honesty**: `gateway.health` reports npm-identity state, startup policy
  observations and auth challenges; `pmcp doctor` no longer vouches for unverified hosts.
- **CI guards** for the release path (SHA-pinned actions with a drift check, changelog
  requirement with live label lookup, install-smoke at both the floor and the ceiling of
  the declared bounds), and no `pull_request_target` anywhere.
- **Packaging is tidy**: lock in sync with the manifest, data files shipped and proven
  by an install-smoke job, `py.typed`, no syntax above the declared floor.
- **Documentation of intent**: the CHANGELOG explains *why* for every change and the
  code explains its invariants — the review's job was mostly to find the places where
  the invariants stop.

---

## 9. Suggested order of work

**Now (one release, mostly S-effort, all security or user-visible):**
S-02 (`load_dotenv` side effect), S-04 (feedback token), S-05 (secret store write),
S-07/S-08/S-09/S-10 (HTTP/JWKS), S-11 (policy precedence), C-01–C-04 (downstream
protocol), C-06 (`tasks_list`), C-08 (`nbf`), C-11 (`print` to stdout), C-12 (CLI
auth), P-01 (manifest cache + `CSafeLoader`), D-01 (lock refresh + `pip-audit`).

**Next (design work, M-effort):** S-01 and S-03 together as one "trust model" phase
(package-bound policy, approval store for project sources, `gateway.admin` tier —
M-11); C-10/M-02 schema generation; S-12 redactor rewrite with corpora; P-02 lock
granularity; P-03 blocking I/O sweep; C-05 per-request liveness; C-09 cache from the
live index.

**Later (structural):** M-01 module split (do it after the trust-model phase so the new
modules land in the right places), M-05 runtime object instead of module globals,
M-06/M-03 de-duplication, M-07 lint/mypy tightening, M-10 repository hygiene, T-* items
from Section 7.

---

## Appendix A · Baseline commands

```bash
uv sync --all-extras
uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
uv run mypy --strict src/pmcp                     # 41 errors, informational
uv run ruff check --select B,ASYNC,S,BLE,DTZ src/ # informational
uv run pytest tests/ -q --cov --cov-report=term-missing --durations=40
# sandbox-only: scrub the npm gates to see the true result for the resolver-bound files
env -u NODE_OPTIONS -u npm_config_https_proxy -u npm_config_noproxy \
  uv run pytest tests/test_npm_resolver.py tests/test_refresher.py \
  tests/test_version_checker.py tests/test_tools.py -q            # 733 passed
uv export --no-hashes --frozen --all-extras -o req.txt && uvx pip-audit -r req.txt
```

## Appendix B · Reproduction scripts

All scripts run offline against the real modules (`uv run python <script>`).

### B.1 `repro_http_auth.py` (S-07, S-08 by inspection, S-09, S-10, S-17, C-08)

```python
"""Repro for HTTP/auth findings against the real pmcp modules (no network)."""
import asyncio, json, time, uuid
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from mcp.server.lowlevel import Server
from starlette.testclient import TestClient

from pmcp.transport.http import create_http_app
from pmcp import auth as pmcp_auth

print("=== 1. shared-secret: non-ASCII byte in Authorization header (raw ASGI) ===")
app = create_http_app(Server("x"), auth_token="s3cret")

async def raw_post(app, auth_header: bytes):
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/mcp", "raw_path": b"/mcp", "query_string": b"", "root_path": "",
        "headers": [(b"host", b"127.0.0.1:3344"), (b"authorization", auth_header), (b"content-length", b"2")],
        "client": ("127.0.0.1", 5555), "server": ("127.0.0.1", 3344),
    }
    sent = {}
    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}
    async def send(msg):
        if msg["type"] == "http.response.start":
            sent["status"] = msg["status"]
    try:
        await app(scope, receive, send)
    except Exception as exc:  # what uvicorn would log as a 500 with traceback
        return f"UNHANDLED {type(exc).__name__}: {exc}"
    return sent.get("status")

print("Authorization: 'Bearer nope'  ->", asyncio.run(raw_post(app, b"Bearer nope")))
print("Authorization: 'Bearer caf\\xe9' ->", asyncio.run(raw_post(app, b"Bearer caf\xe9")))

print("\n=== 2. protected-resource metadata 'resource' derived from Host ===")
app2 = create_http_app(
    Server("x"),
    auth_mode="resource-server",
    resource_server_issuer="https://issuer.example",
    resource_server_jwks_url="https://jwks.example.com/jwks.json",
    resource_server_audience="https://gateway.example.com/mcp",
    protected_resource_metadata_url="https://gateway.example.com/.well-known/oauth-protected-resource",
)
client2 = TestClient(app2, raise_server_exceptions=False)
for host in ("gateway.example.com", "evil.example.net"):
    r = client2.get("/.well-known/oauth-protected-resource", headers={"Host": host})
    print(f"Host={host!r} ->", r.status_code, r.json().get("resource"))

print("\n=== 3. AsyncJWKS: unknown kid forces a fetch per request, no cooldown ===")
fetches = 0
async def fake_fetch(self):
    global fetches
    fetches += 1
    return {"keys": []}
pmcp_auth.AsyncJWKS._fetch = fake_fetch
async def hammer():
    j = pmcp_auth.AsyncJWKS("https://jwks.example.com/jwks.json")
    await j.get()
    for _ in range(20):
        tok = jwt.encode({"a": 1}, "k", algorithm="HS256", headers={"kid": uuid.uuid4().hex})
        await j.get_for_token(tok)
asyncio.run(hammer())
print("fetches after 1 warm-up + 20 unauthenticated unknown-kid tokens:", fetches)

print("\n=== 4. validate_resource_server_token requires 'nbf' ===")
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pub_jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key())); pub_jwk["kid"] = "k1"
jwks = {"keys": [pub_jwk]}
now = int(time.time())
claims = {"iss": "https://issuer.example", "aud": "https://gw/mcp", "exp": now + 300, "iat": now, "sub": "u"}
tok = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})
try:
    pmcp_auth.validate_resource_server_token(tok, issuer="https://issuer.example", audience="https://gw/mcp", jwks=jwks)
    print("token without nbf: ACCEPTED")
except pmcp_auth.ResourceServerAuthError as e:
    print("token without nbf: REJECTED ->", e.error, "|", e.description)

print("\n=== 5. unknown kid with a single-key JWKS falls back to that key ===")
claims["nbf"] = now
tok = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "does-not-exist"})
c = pmcp_auth.validate_resource_server_token(tok, issuer="https://issuer.example", audience="https://gw/mcp", jwks=jwks)
print("token with kid=does-not-exist against 1-key JWKS: accepted, subject =", c.subject)
```

Output observed:

```
Authorization: 'Bearer nope'  -> 401
Authorization: 'Bearer caf\xe9' -> UNHANDLED TypeError: comparing strings with non-ASCII characters is not supported
Host='gateway.example.com' -> 200 http://gateway.example.com/mcp
Host='evil.example.net' -> 200 http://evil.example.net/mcp
fetches after 1 warm-up + 20 unauthenticated unknown-kid tokens: 21
token without nbf: REJECTED -> invalid_token | Token is missing the "nbf" claim
token with kid=does-not-exist against 1-key JWKS: accepted, subject = u
```

### B.2 `repro_policy.py` (S-12, C-13)

```python
from pmcp.policy.policy import PolicyManager
pm = PolicyManager()
for s in ["HTTP status code 200 returned", "session expired, please retry",
          "token count 512 of 4096", "zip code 94110", "source code review complete"]:
    print(repr(s), "->", repr(pm.redact_secrets(s)))
# Token *shapes* only, assembled at runtime so no scanner mistakes them for real keys.
shapes = [
    "AKIAIOSFODNN7EXAMPLE",                     # AWS's documented example access key
    "xoxb-" + "0" * 12 + "-" + "a" * 16,        # Slack bot token
    "AIza" + "S" * 35,                          # Google API key
    "sk_live_" + "x" * 24,                      # Stripe secret key
    "glpat-" + "a" * 20,                        # GitLab personal access token
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...",  # PEM block
]
for s in shapes:
    print(repr(s[:40]), "->", repr(pm.redact_secrets(s)[:60]))
big = "x" * 100_000
for cap in (40, 96, 100, 1000):
    out, truncated, size = pm.truncate_output(big, max_bytes=cap)
    print(f"cap={cap}: truncated={truncated} returned_len={len(out)}")
```

### B.3 Schema drift check (C-10)

```python
from pmcp.tools.handlers import get_gateway_tool_definitions
from pmcp import types as T
mapping = {"gateway.invoke": T.InvokeInput, "gateway.tasks_list": T.TasksListInput, ...}
for t in get_gateway_tool_definitions():
    model = mapping.get(t.name)
    if model is None: continue
    props = set((t.input_schema.get("properties") or {}).keys())
    fields = set(model.model_fields)
    print(t.name, "model-only:", sorted(fields - props), "schema-only:", sorted(props - fields),
          "additionalProperties:", t.input_schema.get("additionalProperties"),
          "extra:", model.model_config.get("extra"))
```

### B.4 Manifest load cost (P-01)

```python
import time, yaml, pathlib
from pmcp.manifest.loader import load_manifest
t = time.perf_counter(); [load_manifest() for _ in range(10)]
print("load_manifest avg ms:", (time.perf_counter() - t) / 10 * 1000)
text = pathlib.Path("src/pmcp/manifest/manifest.yaml").read_text()
for L in (yaml.SafeLoader, yaml.CSafeLoader):
    t = time.perf_counter(); [yaml.load(text, Loader=L) for _ in range(5)]
    print(L.__name__, (time.perf_counter() - t) / 5 * 1000, "ms")
```

### B.5 Downstream protocol, lifecycle and catalog repros (C-01, C-02, C-03, C-06, S-06, P-01, P-02)

These drive `ClientManager` directly against a fake stdio server, with no HTTP layer and
no real npm package. The repo's own `tests/runtime/fake_stdio_server.py`
(`build_fake_stdio_downstream`) is the healthy peer; each misbehaving peer is a
40-line script that answers `initialize` and `tools/list` and then does one bad thing.

```python
# fake_ping_server.py — answers the handshake, then sends a server→client *request*
import json, queue, sys, threading, time
def send(m):
    sys.stdout.buffer.write((json.dumps(m) + "\n").encode()); sys.stdout.buffer.flush()
q: "queue.Queue[bytes]" = queue.Queue()
threading.Thread(target=lambda: [q.put(l) for l in sys.stdin.buffer], daemon=True).start()
deadline = None
while True:
    try:
        line = q.get(timeout=None if deadline is None else max(0.0, deadline - time.time()))
    except queue.Empty:
        break
    m = json.loads(line); mid, method = m.get("id"), m.get("method")
    if mid == 99 and ("result" in m or "error" in m):
        sys.stderr.write("PING ANSWERED\n"); break
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": m["params"]["protocolVersion"],
            "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "0"}}})
        send({"jsonrpc": "2.0", "id": 99, "method": "ping"}); deadline = time.time() + 2
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": []}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}})
else:
    pass
if deadline is not None:
    sys.stderr.write("PING UNANSWERED after 2 s\n")
```

```python
# driver.py — register the fake as a lazy server, connect, inspect
import asyncio, sys
from pmcp.client.manager import ClientManager
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig

async def main():
    cm = ClientManager()
    cfg = ResolvedServerConfig(name="fake", source="custom",
        config=LocalMcpServerConfig(command=sys.executable, args=["fake_ping_server.py"]))
    cm.register_lazy_configs([cfg])
    print("connected:", await cm.ensure_connected("fake"))
    await asyncio.sleep(3)                      # let the fake server's window expire
    st = next(s for s in cm.get_all_server_statuses() if s.name == "fake")
    print(st.status, st.last_error)
    await cm.disconnect_all()
asyncio.run(main())
```

Variations used for the other findings:

- **C-02** — the fake server never answers `tools/call`; wrap `cm.call_tool(...)` in
  `asyncio.wait_for(..., 0.5)`, catch the `TimeoutError`, then inspect the client's
  pending-request map: the entry for the abandoned id is still there.
- **C-03** — the fake server writes `b"\xff\n"` either immediately after `tools/list`
  (mid-connect: connect returns `True`, status stays `online`, the read task is done) or
  1.5 s after a clean connect (post-connect: status `error`, reconnect scheduled).
- **C-06** — register one lazy server that is never started, build `GatewayTools`, call
  `await tools.tasks_list({})`: `ok=False`, `errors=["Server lazy-only is not connected"]`.
- **S-06** — start two `python sleeper.py` processes outside the gateway, build a
  `ResolvedServerConfig` whose command and args match them, call
  `GatewayServer._kill_orphan_processes(None, [config])`, then `Popen.poll()`: both
  report `returncode == -9`.
- **P-01** — wrap `pmcp.manifest.loader.load_manifest` *and*
  `pmcp.tools.handlers.load_manifest` (the name is bound at import time, so patching the
  source alone misses the handler's calls) with a counter, stub
  `fetch_registry_servers`/`load_registry_cache`, run one `catalog_search`.
- **P-02** — register `bad-lazy` (`command="/usr/bin/false"`) and a healthy
  `build_fake_stdio_downstream("good-lazy", control_port=…)`; start
  `ensure_connected("bad-lazy")` as a task, sleep 50 ms, time
  `ensure_connected("good-lazy")`.

## Appendix C · Status of the 2026-06-15 review

| June item | Status at `d743bc4` |
|-----------|---------------------|
| C1 summary built from pre-redaction text | **Fixed** (`policy.py:385`) |
| C2 task `status_message`/`raw` never redacted | **Fixed** (`handlers._sanitize_task_for_output`) |
| H1 redaction hard-capped at 400 chars | **Fixed** (`max_length=None`) |
| H2 bare tokens not redacted | **Partly fixed** — `sk-`/`ghp_`/`github_pat_` added; AWS/Slack/Google/Stripe/GitLab/PEM still missing (S-12) |
| H3 credential URL logged raw on remote-connect failure | **Partly fixed** — `manager.py:2859` sanitises; `handlers.py:4442` and `server.py:428` still log the raw exception (S-18) |
| M1 `redact_secrets` defaults to False on brokering paths | **Unchanged by design** — task paths now default on, `invoke` still off (C-14) |
| M2 diagnostic keyword set misses cookie/session/… | **Fixed** (`auth.py:68-88`) |
| C3 connect path outside `_lifecycle_lock` | **Fixed** — now the lock is held too long (P-02) |
| H4 reconnect loop fire-and-forget | **Fixed** (`_reconnect_tasks`, tracked + cancelled) |
| H5 stderr tasks untracked | **Fixed** (`ManagedClient.stderr_task`) |
| M3 request id resets on reconnect | **Fixed** (manager-level `_request_counters`) |
| M4 storm guard on the replaced object | **Fixed** (`_schedule_reconnect`) |
| L1 `_connect_tasks` not cleared | **Fixed** |
| M5 hand-rolled YAML cache writer | **Fixed** (`yaml.safe_dump`; two dead helpers remain, M-04) |
| M6 matcher penalises well-described servers | **Fixed** (absolute weighted evidence) |
| Manifest staleness | **Partly** — `status`/`replacement` fields and registry sync exist; entries not re-audited here |
| `_USER_AGENT` stale version | **Fixed** (uses `__version__`); URL still `ViperJuice` |
| serial `refresh_all` lookups | **Fixed** (semaphore of 8) |
| `MissingApiKeyError` wrong path | **Fixed** (`.env.pmcp`) |
| `env_store` chmod TOCTOU | **Fixed** (`os.open(..., 0o600)`); symlink/atomicity remain (S-05) |
| Appendix A `find_project_root` ancestor footgun | **Fixed** (stops at tempdir and `$HOME`) |
| Tier 4 P0 JWT validation, audience binding | **Done** (resource-server mode) |
| Tier 4 P1 tenant isolation, SEP-835 `insufficient_scope` | **Partly** (tenant env for remote headers; 403 challenge done) |
| Tier 4 P2 private-range SSRF filter, Origin 403 | **Done** |
| Tier 5 consume the official registry | **Done** (`manifest/registry.py`, `sync.py`) |
