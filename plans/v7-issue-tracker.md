# PMCP v7 Issue Tracker

Maps every finding from `plans/codebase-review-2026-06-15.md` to its phase in
`specs/phase-plans-v7.md`. Status legend: ☐ open · ◐ in progress · ☑ done.
Severity from the review. "Confirmed" = reproduced at runtime or at source during review.

## Stage A — bug fixes (target: v1.14.0)

### Phase REDACT — Redaction Hardening
| ID | Sev | Status | Location | Issue | Fix |
|----|-----|--------|----------|-------|-----|
| C1 | CRITICAL | ☐ | `policy/policy.py:242` | `summary` sliced from pre-redaction `output_str`; secret on line 1 leaks even with `redact=True`. Confirmed. | Build summary from `final_str` (post-redaction). |
| C2 | CRITICAL | ☐ | `handlers.py:4842`, `:1622` | Task `status_message` + `raw` never redacted; only result body is. Confirmed. | Route task fields through `process_output`/`redact_secrets`. |
| H1 | HIGH | ☐ | `policy.py:196` → `auth.py` | `redact=True` routes through `sanitize_auth_diagnostic` → hard 400-char cap while reporting `truncated=False`. Confirmed. | Decouple redaction from the 400-char auth helper. |
| H2 | HIGH | ☐ | `policy.py:19-24` | Bare `sk-`/`ghp_`/`github_pat_` not matched (only key=value). Stronger scrubber exists unused at `handlers.py:2983`. Confirmed. | Fold bare-token patterns into one shared set. |
| H3 | HIGH | ☐ | `handlers.py:3689`, `manager.py:1288` | Credential-bearing URL logged raw on remote-connect failure; return path sanitizes, log doesn't. Confirmed. | Log `self._sanitize_error(e)` at both sites. |
| M1 | MEDIUM | ☐ | `handlers.py:1601`, `:4841` | `redact_secrets` defaults False on untrusted-sandbox broker paths. | Default ON for task/code-mode results. |
| M2 | MEDIUM | ☐ | `auth.py:122-124` | Free-text redactor misses cookie/session/refresh/jwt/client_secret/etc. Confirmed (`Set-Cookie: session=…` passes). | Extend keyword alternation. |

### Phase CONCURR — Concurrency & Lifecycle
| ID | Sev | Status | Location | Issue | Fix |
|----|-----|--------|----------|-------|-----|
| C3 | CRITICAL | ☐ | `manager.py:386/434/966` vs `1463/1554` | `_lifecycle_lock` doesn't cover connect paths → refresh-vs-lazy-connect race orphans subprocess, leaks read_task, tears catalog. Confirmed gap. | Acquire lock in `_connect_singleflight` registration. |
| H4 | HIGH | ☐ | `manager.py:1248` | `_reconnect_loop` fire-and-forget; survives shutdown, spawns unreaped subprocess; GC-vulnerable. Confirmed. | Track reconnect tasks; cancel+await on teardown. |
| H5 | HIGH | ☐ | `manager.py:1027`, `:1607` | `_read_stderr` tasks never tracked/cancelled; can block 120s holding FD across churn. | Track + cancel stderr tasks. |
| M3 | MEDIUM | ☐ | `manager.py:317/1362/2127` | `request_id` resets to 0 on reconnect → stale `gateway.cancel` hits a new request. Confirmed. | Per-connection epoch / monotonic IDs. |
| M4 | MEDIUM | ☐ | `manager.py:1248-1294` | Reconnect-storm guard lives on the replaced `ManagedClient`; no cross-generation protection. | Manager-level `set[str]` keyed by server name. |
| L1 | LOW | ☐ | `manager.py:1522` | `_connect_tasks` not cancelled/cleared by `disconnect_all`; retrying connect re-registers post-clear. | Cancel + clear in `_disconnect_all_unlocked`. |

### Phase MANIFEST — Manifest & Matcher
| ID | Sev | Status | Location | Issue | Fix |
|----|-----|--------|----------|-------|-----|
| M5 | MEDIUM(→HIGH) | ☐ | `refresher.py:97-124` | Hand-rolled YAML f-string interpolates untrusted tool name/tags/package unescaped; cached `risk_hint` drives the risk gate → inject `risk_hint: low`. Self-healing, no RCE. Confirmed. | `yaml.safe_dump(cache_dict)`. |
| M6 | MEDIUM | ☐ | `matcher.py:59-82` | Score = matches ÷ server's own keyword count → best-described servers rank worst, fall below 0.20 floor. Confirmed. | Absolute/IDF-weighted matched-keyword score. |
| STALE | MEDIUM | ☐ | `manifest.yaml` ~164-460 | ~15 archived `@modelcontextprotocol/server-*`; several deprecated w/ first-party replacements; `figma` is hobby pkg. Verified on npm. | Repoint/label per audited list. |
| L-UA | LOW | ☐ | `version_checker.py:17` | `_USER_AGENT` pinned `pmcp/1.9.0` (real 1.13.1). | Interpolate `__version__`. |
| L-SER | LOW | ☐ | `server.py:472` → `refresher.refresh_all` | Up to ~90 serial network version lookups (10s each). | `asyncio.gather` w/ cap. |
| L-ENV | LOW | ☐ | `installer.py:519` | `MissingApiKeyError` points at `.env`; PMCP reads `.env.pmcp`/`pmcp.env`. | Use `resolve_scope_path`. |
| M8 | LOW | ☐ | `version_checker.py:218-231` | `detect_package_type` only strips literal `@latest`; pinned `pkg@1.2.3` → 404, staleness check silently off. | Strip any `@<tag>` after scope. |

### Phase ENVFIX — Config & Env-Store
| ID | Sev | Status | Location | Issue | Fix |
|----|-----|--------|----------|-------|-----|
| L-TOCTOU | LOW | ☐ | `env_store.py:89-90` | Secret file created 0664 by `write_text` then `chmod(0o600)` — world-readable window. Confirmed. | `os.open(..., 0o600)` write. |
| FOOTGUN | LOW(footgun) | ☐ | `config/loader.py:202` | `find_project_root` ascends past cwd → project creds land in unexpected ancestor repo; also causes the 4 false test failures (Appendix A). Confirmed. | Bound the upward search; export resolver. |

## Stage B — features

### Phase AUTHRS — OAuth 2.1 Resource Server
| ID | Sev | Status | Issue | Spec basis |
|----|-----|--------|-------|-----------|
| AUTH-P0a | core gap | ☐ | Validate AS-issued token (sig/iss/exp/nbf); 401 on invalid/expired. Today: static `hmac.compare_digest` only. | OAuth 2.1 §5.2 |
| AUTH-P0b | core gap | ☐ | Audience binding: reject tokens whose `aud` ≠ PMCP canonical resource URI. | RFC 8707 |
| AUTH-P0c | core gap | ☐ | Keep static bearer as explicit `shared-secret`/single-tenant mode (not the only path). | — |
| AUTH-P1a | multi-tenant | ☐ | Per-tenant downstream credential isolation (today all share one process env). | roadmap goal |
| AUTH-P1b | SHOULD | ☐ | `scope` in 401 challenge + 403 `insufficient_scope` step-up. | SEP-835 |
| AUTH-P1c | VERIFY | ☐ | Session IDs non-deterministic, auth never session-derived, bound to user. | Streamable HTTP |
| AUTH-P2a | partial gap | ☐ | `sanitize_public_auth_url` block private/link-local/loopback (SSRF). | Security BP |
| AUTH-P2b | VERIFY | ☐ | 403 on invalid `Origin`. | PR #1439 |
| AUTH-P2c | VERIFY | ☐ | Confirm `.well-known/oauth-protected-resource` path serving. | RFC 9728 |
| AUTH-P3 | skip | ☐ | DCR (RFC 7591) demoted to MAY — do NOT prioritize; Client ID Metadata Documents are the forward path. | SEP-991 |

### Phase REGISTRY — Registry & Server Expansion
| ID | Status | Issue |
|----|--------|-------|
| REG-1 | ☐ | Consume `registry.modelcontextprotocol.io` `/v0/servers` (preview; pin/cache/tolerate drift); offline-safe fallback to local manifest. |
| REG-2 | ☐ | Manifest-sync reconciliation (flag renamed/archived, surface first-party replacements); no auto-install. |
| REG-3 | ☐ | Back `request_capability`/`catalog_search` with live registry lookups. |
| REG-4 | ☐ | Add remote vendor-official: GitHub remote (`api.githubcopilot.com/mcp/`), Atlassian Rovo (`mcp.atlassian.com/v1/mcp`), Cloudflare remote set, Sentry (`mcp.sentry.dev/mcp`), Vercel (`mcp.vercel.com`), Hugging Face (`huggingface.co/mcp`). |
| REG-5 | ☐ | Add verified stdio: AWS official suite (`awslabs/mcp`), Desktop Commander, Magic (21st.dev), shadcn/ui, Chroma, Snowflake, Databricks, Pinecone, Weaviate, Redis-official, Octocode. (Verify exact package IDs before adding.) |

## Verified NOT-bugs (do not "fix" — preserve)
- Timing-safe bearer compare (`http.py:272`), rate limiter keys on real socket peer (no XFF trust), URL redaction strips basic-auth userinfo, no command injection anywhere (argv lists, `safe_load`), no token passthrough (incoming `Authorization` never forwarded downstream), PMCP never executes scripts locally.
</content>
