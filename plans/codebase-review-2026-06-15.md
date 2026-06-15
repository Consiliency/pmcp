# PMCP Critical Codebase Review — 2026-06-15

Reviewer: Claude Opus 4.8. Read-only audit (no code changed). Baseline: `ruff` clean,
`mypy` clean, **1807 pass / 0 real failures** (the 4 "failures" are an environment
artifact — see Appendix A). Every CRITICAL/HIGH below was reproduced at runtime or
confirmed at source. Severity reflects exploitability *given PMCP's threat model*
(it brokers untrusted sandbox output and is shared across clients).

---

## Tier 1 — Secret-redaction leaks (fix first)

The redaction layer looks complete but leaks on several real paths. The test suite
passes because its fixtures are small/clean — the leaks live in truncation/summary/
task paths the tests don't exercise.

### C1 — `summary` field is built from PRE-redaction text  ·  CRITICAL  ·  confirmed
`src/pmcp/policy/policy.py:242`. With `redact=True`, `result` is redacted but
`summary` slices `first_line` from `output_str` (the raw input). Any truncated
sandbox/tool result with a secret on line 1 returns that secret in `summary`.
Reached by `gateway.invoke` (handlers.py:1628) and `gateway.tasks_result` (handlers.py:4850).
**Fix:** build the summary from `final_str` (post-redaction), or redact the summary.

### C2 — Task `status_message` and `raw` are never redacted  ·  CRITICAL  ·  confirmed
`handlers.py:4842-4851` (tasks_result), `:1622-1632` (invoke). `process_output`
redacts the *result* only; the attached `McpTaskInfo.raw` (entire downstream task
dict) and `status_message` (verbatim) bypass redaction even with `redact_secrets=True`.
**Fix:** route `task.status_message` / `task.raw` through the same redaction path.

### H1 — `redact=True` silently truncates ALL output to 400 chars  ·  HIGH  ·  confirmed
`policy.py:196` → `redact_secrets` → `sanitize_auth_diagnostic` (auth.py) which ends
`return text[:400]`. So any redacted result is hard-capped to 400 chars while
`process_output` reports `truncated=False` and the original `raw_size` — silent data
loss + a misleading flag on the primary brokering path.
**Fix:** apply redaction regexes directly to bulk results; don't reuse the 400-char
auth-diagnostic helper for result bodies.

### H2 — Bare tokens (`sk-…`, `ghp_…`, `github_pat_…`) are NOT redacted in results  ·  HIGH  ·  confirmed
`policy.py:19-24` (`DEFAULT_REDACTION_PATTERNS`) only matches `key=value` / `key: value`
forms. A bare OpenAI/GitHub token in sandbox stdout returns verbatim under
`redact_secrets=True`. The repo already has a stronger scrubber
(`handlers.py:2983-2985`: `sk-`, `ghp_`, `github_pat_`) but it's wired only into the
*feedback/description* path, not the code-mode brokering path.
**Fix:** add the bare-token patterns to `DEFAULT_REDACTION_PATTERNS` (single source).

### H3 — Credential-bearing URL logged raw on remote-connect failure  ·  HIGH  ·  confirmed
`handlers.py:3689` (`logger.error(f"...: {e}")`) and `manager.py:1288` (reconnect).
The return path and feedback event run `self._sanitize_error(e)` one line later, but
the log line logs the raw exception. An httpx 401/403 (exactly when this fires)
stringifies the full URL incl. `?access_token=SECRET`. The sanitizer-on-return proves
the maintainers consider this secret; the log path bypasses it.
**Fix:** log `self._sanitize_error(e)` at both sites.

### M1 — `redact_secrets` defaults to **False** on brokering paths  ·  MEDIUM
`handlers.py:1601`, `:4841`. PMCP brokers an *untrusted sandbox*, yet redaction is
opt-in via model-supplied options. Omitting `options` → fully raw downstream output.
**Fix:** default redaction ON for task/code-mode servers (policy-driven server-side).

### M2 — `sanitize_auth_diagnostic` keyword set misses cookie/session/refresh/jwt  ·  MEDIUM
`auth.py:122-124` alternation lacks `session|sid|cookie|set-cookie|refresh_token|`
`client_secret|access_token|id_token|jwt|assertion|saml` — all present in the URL
query-key list but not the free-text redactor. `Set-Cookie: session=…` passes through.
**Fix:** unify the keyword set across URL and free-text redactors.

---

## Tier 2 — Concurrency & subprocess lifecycle (shared-gateway correctness)

PMCP is a shared HTTP service; these only bite under genuine multi-client concurrency,
which is the documented deployment mode. The serial single-client path is solid.

### C3 — `_lifecycle_lock` does not cover the connect paths  ·  CRITICAL  ·  confirmed gap
`manager.py:1554` (`refresh`) / `1463` (`_disconnect_all_unlocked`) hold the lock; the
connect side (`ensure_connected` 434, `_connect_singleflight` 386, `_connect_stdio` 966)
holds nothing. Client B's lazy-connect can interleave with Client A's `refresh(force)`:
B re-inserts its `ManagedClient` + tools into dicts A just `clear()`ed → **orphaned
subprocess + leaked read_task + torn tool catalog**. This is the unguarded mechanism
behind the README's own "refresh can interrupt another client" warning — and it leaks
rather than cleanly cancels. The one serialization test only gathers `refresh` vs
`refresh` (both locked); no test covers refresh vs connect.
**Fix:** acquire `_lifecycle_lock` in `_connect_singleflight` around client registration
+ index mutation.

### H4 — `_reconnect_loop` is fire-and-forget; survives shutdown  ·  HIGH  ·  confirmed
`manager.py:1248` creates the task, stores only a bool; `disconnect_all`/`shutdown`
never cancel it. A reconnect sleeping on backoff wakes *after* shutdown and spawns a
fresh subprocess nothing will reap. (Also GC-vulnerable per asyncio docs — untracked task.)
**Fix:** track reconnect tasks in a `set[asyncio.Task]`; cancel+await on teardown.

### H5 — `_read_stderr` tasks never tracked or cancelled  ·  HIGH
`manager.py:1027`, `:1607`. Every connect/adopt spawns a stderr reader that's never
stored; teardown cancels stdout `read_task` but not stderr. A killed process whose
stderr FD is held by a grandchild leaves the task blocked up to 120s holding the FD;
accumulates across reconnect churn.
**Fix:** track + cancel stderr tasks alongside `read_task`.

### M3 — `request_id` resets to 0 on reconnect → cross-client mis-cancel  ·  MEDIUM
Each reconnect builds a new `ManagedClient` with `request_id=0`. `gateway.cancel`
addresses `server::local_id`. Client A's stale `cancel("srv::3")` (from a pre-reconnect
`list_pending`) can cancel Client B's *new* request that reused id 3.
**Fix:** carry the counter across reconnects or add a per-connection epoch to the id.

### M4 — reconnect storm guard lives on the object reconnect replaces  ·  MEDIUM
`manager.py:1248-1294`. Guard set on the dying client; `finally` clears it on whatever
is in the map (the new client). Provides no protection across a generation boundary.
**Fix:** track reconnect-in-flight in a manager-level `set[str]` keyed by server name.

### L1 — `_connect_tasks` not cancelled/cleared by `disconnect_all`/`refresh`  ·  LOW
`manager.py:1522-1528` clears seven dicts but not `_connect_tasks`; a retrying connect
(sleeps up to 7s) can complete post-clear and re-register. Compounds C3.

---

## Tier 3 — Manifest, matcher, config

### M5 — Unescaped YAML f-string in the descriptions cache feeds the risk gate  ·  MEDIUM (borderline HIGH)  ·  confirmed
`refresher.py:97-124`. The cache is built by string concatenation, not `yaml.dump`.
`tool.name`, `package`, `version`, `tags` are interpolated **unescaped** (only
description fields get a 2-char escaper). Values come from untrusted downstream MCP
servers. The cached `risk_hint` is read back (handlers.py:1130-1147) and drives
`max_risk` filtering (1215) + high-risk safety-note gating (1363) — so a crafted tool
name could plant `risk_hint: "low"` on a high-risk tool. Self-healing (safe_load →
None → regenerate) and no RCE, hence MEDIUM not HIGH.
**Fix:** replace the hand-rolled writer with `yaml.safe_dump(cache_dict)`.

### M6 — Matcher ranking penalizes well-described servers  ·  MEDIUM  ·  confirmed
`matcher.py:59-82`. Score = `matches / len(server_keywords)`, so a 20-keyword server
with 2 matches (0.10) loses to a 2-keyword server with 2 matches (1.0), and can fall
below the 0.20 floor → "no match". Biases `request_capability` against the best-tagged
servers.
**Fix:** score on absolute / IDF-weighted matched-keyword count.

### Manifest staleness — the whole `@modelcontextprotocol/server-*` set  ·  MEDIUM
~15 entries point at the now-archived reference servers (`servers-archived`). Several
still install but are npm-deprecated and have first-party replacements:
`github` → `github/github-mcp-server` (remote `https://api.githubcopilot.com/mcp/`, GA);
`brave-search` → `@brave/brave-search-mcp-server`; `linear` → `@linear/mcp` (hosted);
`slack`, `postgres`, `puppeteer`, `sentry`, `gdrive`, `everart`, `aws-kb-retrieval`
archived (some unpublished → may 404). `figma` points at a single-maintainer hobby
package, not Figma's official Dev Mode MCP. **Action:** manifest audit + repoint.

### Lower-priority
- `version_checker.py:17` `_USER_AGENT` pinned at `pmcp/1.9.0` (real: 1.13.1). LOW.
- Eager `refresh_all` / `pmcp refresh` does up to ~90 *serial* network version lookups
  (10s timeout each) — worst case minutes of blocking. `asyncio.gather` w/ a cap. LOW.
- `installer.py:519` `MissingApiKeyError` tells users to edit `.env`; PMCP actually
  reads `.env.pmcp` / `~/.config/pmcp/pmcp.env`. LOW.
- `env_store.py:89-90` writes the secret file then `chmod(0o600)` — created 0664 first
  (TOCTOU window). Use `os.open(..., 0o600)`. LOW.

### Verified NOT vulnerable (good hygiene to preserve)
- Bearer compare is timing-safe (`hmac.compare_digest`, http.py:272).
- Rate limiter keys on real socket peer, not `X-Forwarded-For` — not spoofable.
- URL redaction strips basic-auth userinfo; case-insensitive query-key match.
- **No command injection** anywhere: all subprocess via argv lists, no `shell=True`;
  `${ENV_VAR}` never reaches a shell. Config via `yaml.safe_load` / `json.loads`.
- **No token passthrough** — incoming client `Authorization` is never forwarded
  downstream (downstream headers resolve from `os.environ` only). PMCP is clean on the
  spec's #1 forbidden anti-pattern.
- PMCP never executes scripts locally — `invoke` only forwards `tools/call`.

---

## Tier 4 — MCP Authorization spec compliance (research)

Spec baseline: **MCP 2025-11-25** (current stable; a 2026-07-28 RC exists, not final).
PMCP's gap is **not "no auth"** — it already emits `WWW-Authenticate: resource_metadata`
on 401, serves a Protected Resource Metadata doc, and surfaces AS/OIDC/CIMD hints. The
gap is **"discovery scaffolding present, token *validation* absent"**: `http.py` does a
static `hmac.compare_digest` of a shared secret with no signature/issuer/audience/expiry
/scope checks.

To be a real OAuth 2.1 Resource Server (prioritized):
- **P0** Validate AS-issued tokens (JWT via AS JWKS: signature, `iss`, `exp`/`nbf`;
  optional RFC 7662 introspection for opaque). Enforce **audience binding (RFC 8707)** —
  reject tokens whose `aud` ≠ PMCP's canonical resource URI. Keep the static bearer as an
  explicit, separately-named "shared-secret / single-tenant" mode.
- **P1 (hosted multi-tenant, the roadmap goal)** per-tenant credential isolation (today
  all downstream creds share one process env); session hardening (non-deterministic IDs,
  auth never session-derived, bind to user); `scope` in challenges + 403 `insufficient_scope`
  step-up (SEP-835).
- **P2** extend `sanitize_public_auth_url` to block private/link-local/loopback ranges
  (SSRF); 403 on invalid `Origin`; confirm `.well-known/oauth-protected-resource` path.
- **P3** DCR (RFC 7591) is now *demoted to MAY*; Client ID Metadata Documents are the
  forward path — don't prioritize DCR.

Refs: modelcontextprotocol.io/specification/2025-11-25/basic/authorization &
.../security_best_practices.

---

## Tier 5 — Ecosystem: servers worth adding (research)

**Consume the official MCP Registry** (`registry.modelcontextprotocol.io`, preview,
~2000 entries, REST `/v0/servers`). PMCP *is* a downstream aggregator — pull it to
self-heal the manifest against archival churn and back `request_capability` with live
lookups. Treat as preview: pin/cache, tolerate schema drift.

High-value additions (not already in the 90), prioritizing remote vendor-official
(no local provisioning — fits PMCP's lazy model):
1. GitHub official remote — `https://api.githubcopilot.com/mcp/` (supersedes stale `github`)
2. Atlassian Rovo (Jira/Confluence/JSM) — `https://mcp.atlassian.com/v1/mcp` (GA)
3. Cloudflare official remote set (~13 servers; replaces stale local `cloudflare`)
4. Sentry official remote — `https://mcp.sentry.dev/mcp`
5. Vercel — `https://mcp.vercel.com` (verified)
6. Hugging Face — `https://huggingface.co/mcp` (verified)
7. AWS official suite — `awslabs/mcp` (ECS/EKS/Serverless/CDK/Cost/Bedrock-KB)
8. Microsoft Learn/Docs (verify endpoint), Desktop Commander, Magic (21st.dev),
   shadcn/ui, Chroma, Snowflake, Databricks, Pinecone, Weaviate, Redis-official.
(Verify exact package IDs for unconfirmed entries before adding.)

---

## Appendix A — the 4 "failing" tests are an environment artifact (not a bug)

A stray `/tmp/.git` directory exists on this host. `find_project_root`
(`config/loader.py:202`) ascends from pytest's `tmp_path` (under `/tmp`) and treats
`/tmp` as the repo root, writing `.env.pmcp` to `/tmp` instead of `tmp_path`. Proof:
with `TMPDIR` outside `/tmp` all 4 pass. **But this is also a real footgun (own finding):**
project-scope `auth_connect` can silently write credentials to an unexpected ancestor
repo. Consider bounding the upward search or preferring the explicit cwd when no marker
is found at/below it. (Also: clean up the stray `/tmp/.git` on this host.)
</content>
</invoke>
