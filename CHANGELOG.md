# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Downstream tool calls are no longer killed by a fixed wall-clock deadline
  (issue #79, symptom 1a). `timeout_ms` is now an **inactivity (idle) timeout**:
  a call survives as long as the downstream MCP server keeps producing output
  (including JSON progress notifications, which now count toward per-request
  liveness in both the stdio and SSE readers). An absolute backstop caps total
  wall-clock time so a chatty-but-never-completing call cannot hang forever —
  configurable via `PMCP_REQUEST_CEILING_MS` (default 600000ms / 10 min). This
  unblocks legitimately long browser/automation operations driven through the
  gateway.

### Added
- `gateway.catalog_search` with `include_offline=true` now surfaces
  manifest-only provisionable servers in a dedicated `manifest_candidates` field
  when the query matches a manifest server by name or keyword but no cached
  tools exist yet (issue #78). Candidates carry machine-readable next-action
  metadata (`provisionable`, `provision_tool`, `request_capability_tool`,
  `auth_tool`, `requires_api_key`, `api_key_available`, `env_var`) so an agent
  can provision the exact server instead of falling back to a plain web search.
- Manifest: `brightdata` keywords extended with `web research`, `current web`,
  `page fetch`, and `external research` so research-oriented prompts match
  without the exact brand name.

### Changed
- `gateway.request_capability` tool description now states it **recommends** a
  server to provision (and that `gateway.provision` does the actual install/
  start), matching the implementation — it previously claimed to auto-provision
  (issue #78).

## [1.16.0] - 2026-06-25

### Added
- Manifest: 2026 vendor-official servers — Okta (identity/IAM), Zapier
  (automation meta-connector), Shopify Dev and Square (commerce), Snowflake
  Cortex (data platform), Pinecone (vector DB), Azure DevOps, and the GitLab Duo
  remote MCP. Adds Identity, Automation, E-commerce, and Data-Platform discovery
  coverage.
- Manifest (Tier 2): Elasticsearch, Chroma, Redis, Databricks (managed remote),
  Storybook, and Cloudinary — rounding out search/vector, data-platform,
  frontend, and media coverage.

### Changed
- Manifest: `mongodb` now points at the official `mongodb-mcp-server`
  (mongodb-js) instead of the community `mongodb-lens`, covering Atlas admin in
  addition to queries.

## [1.15.0] - 2026-06-25

### Added
- `SPEC_COMPLIANCE.md` tracks PMCP against the current stable MCP revision
  (`2025-11-25`) with a per-requirement compliance table, a draft-revision
  migration assessment (stateless transport, the `io.modelcontextprotocol/tasks`
  extension, `server/discover`, `CacheableResult`, DCR→Client ID Metadata
  Documents, SSE resumability), and a next-stable tracking checklist. PMCP is
  confirmed compliant with the folded-in current-stable items (403 on invalid
  `Origin` per PR #1439, `insufficient_scope` 403 step-up per SEP-835,
  input-validation as tool-execution errors per SEP-1303, JSON Schema 2020-12
  default dialect per SEP-1613, tool/resource/prompt icon passthrough per
  SEP-973).
- MCP Registry incremental sync. The registry client persists `last_synced_at`
  and accepts `updated_since` to fetch only changed servers
  (`?version=latest&updated_since=…` with cursor pagination), merging deltas into
  the cache via `merge_registry_delta`. A failed incremental attempt degrades to
  a full fetch, and a failed full fetch degrades to the prior cache. Default
  full-fetch callers are unchanged.
- Opt-in private-registry support (`PMCP_REGISTRY_ALLOW_PRIVATE`, default off).
  When enabled with `PMCP_REGISTRY_PRIVATE_ENDPOINT`, PMCP discovers from a
  private/custom registry and tolerates draft/non-GA `server.json` schema fields,
  surfacing all versions — a debugging aid for developers building their own
  private MCP servers, not for production discovery. With the flag off,
  discovery behavior is unchanged.

## [1.14.2] - 2026-06-24

### Removed
- Removed the unused BAML/LLM machinery entirely. Outbound LLM calls were retired
  long ago in favor of a pure-Python capability router, leaving the `baml-py`
  dependency, the generated `baml_client`, `baml_src/`, the retired
  `llm_summarizer` stub, and the dead `use_llm` / `use_llm_fallback` code paths as
  vestigial. Dropping them removes a heavy dependency (no more native binary) and
  the per-release client-regeneration burden. Capability summaries and code
  snippets are unchanged: cache → template for summaries, static templates for
  snippets. The `llm` optional-dependency extra is gone.

## [1.14.1] - 2026-06-23

### Fixed
- Regenerated the vendored `baml_client` against the pinned `baml-py` (0.222.0).
  The client had been generated for 0.219.0 and raised a version-incompatibility
  error under the shipped runtime, so the LLM capability summarizer silently fell
  back to template summaries since 1.13.x. It now loads BAML correctly, and guard
  tests assert the generated client matches the installed `baml-py` so the drift
  cannot recur unnoticed.

### Changed
- Bumped `baml-py` to 0.222.0 and CI `actions/checkout` to v7 (dependabot #63, #75).

## [1.14.0] - 2026-06-15

### Added
- Tenant code-mode host integration (v7 HOSTSOAK). PMCP brokers discovery,
  invocation, downstream task lifecycle, policy, redaction, and operator
  guidance for a companion tenant code-mode MCP server, but does not run
  scripts itself.
- OAuth 2.1 Resource Server auth mode (opt-in, fail-closed). When configured,
  PMCP validates access-token signatures against an async, TTL-cached JWKS and
  binds the `aud` claim to an explicitly-configured canonical resource URI
  (RFC 8707). The legacy static bearer remains the default single-tenant mode.
- MCP Registry-backed discovery metadata with a deterministic local cache.
  `gateway.request_capability`, `gateway.catalog_search`, and
  `gateway.search_registry` surface registry candidates — including remote
  (streamable-http/sse) servers — with transport, package, and remote auth
  metadata, while preserving the explicit `gateway.register_discovered_server`
  and `gateway.provision` boundary.
- Curated registry-backed vendor entries for GitHub, Atlassian Rovo,
  Cloudflare, Sentry, Vercel, and Hugging Face using placeholder header names
  only.

### Fixed
- Secret redaction now covers every task-emitting gateway surface. `gateway.invoke`,
  `gateway.tasks_result`, `gateway.tasks_list`, and `gateway.tasks_get` route
  returned task `status_message`/`raw` through the canonical redactor; truncation
  summaries are built from post-redaction text; bare `sk-`/`ghp_`/`github_pat_`
  tokens are redacted; and redaction is decoupled from the 400-char diagnostic cap
  so large results are no longer silently truncated. Redaction defaults on for
  task/code-mode results.
- Shared-gateway concurrency hardening. The downstream connect/reconnect paths now
  acquire the lifecycle lock (no longer racing `refresh`/`disconnect` into orphaned
  subprocesses or a torn tool catalog), background tasks are tracked and cancelled
  on teardown, request IDs carry a per-connection epoch so a stale `gateway.cancel`
  cannot hit a new request, and the lock is no longer held across reconnect backoff.
- Resource-server auth no longer derives the token audience from the client Host
  header, fetches JWKS via a blocking call on the event loop, or returns HTTP 500
  for JWKS/algorithm errors; algorithms are restricted to an operator allowlist and
  `jwks_url` must be https on a public host.
- Capability matcher scoring no longer drops common queries (`database sql`,
  `headless browser`, `postgres database`) below the match threshold against the
  shipped manifest.
- Registry client now models remote servers, deduplicates to the latest version,
  paginates, bounds the response size, fetches asynchronously, and uses a stable
  cache path; project-scope credential writes and downstream header reads resolve
  the same project root.

## [1.13.1] - 2026-05-06

### Fixed
- Stdio downstream MCP servers no longer surface a misleading "disconnected
  unexpectedly" warning when a tool response exceeds asyncio's default 64 KiB
  per-line read limit. PMCP now spawns child processes with a 10 MiB stdout
  read limit (overridable via `PMCP_STDIO_READ_LIMIT`) so realistic responses
  from `brightdata::scrape_batch`, large `playwright` screenshots/DOM dumps,
  `fetch` on long pages, and similar tools are returned to the caller intact
  instead of triggering a phantom reconnect cycle.
- The stdout reader now distinguishes `LimitOverrunError` and other read
  failures from normal process exit, logs the cause at WARNING, and propagates
  it into `ServerStatus.last_error` so `gateway.health` and `pmcp status`
  report the actual failure mode instead of "Server process exited".

## [1.13.0] - 2026-04-23

### Added
- PMCP discovery now exposes compact native CLI guidance for installed CLIs
  through `gateway.request_capability` (`status="use_cli"`) and
  `gateway.catalog_search` (`cli_hints`) so the model can switch to
  Bash/direct CLI without a second PMCP discovery step.

### Changed
- Clarified the CLI-first discovery contract in docs and tests: PMCP provides
  help commands and curated examples, does not execute the recommended shell
  command, and does not add a general `pmcp invoke` transport for native CLIs.

## [1.12.0] - 2026-04-23

### Added
- Added an offline AUTHSOAK release-gate matrix for local API-key auth,
  remote bearer-header placeholders, remote auth challenges, insufficient
  scopes, URL-mode elicitation, malicious auth URLs, and non-secret
  status/doctor/feedback evidence.

### Changed
- Tightened operator auth documentation for env-store scope selection, remote
  header placeholders, URL-mode non-goals, redaction limits, and HTTP endpoint
  exposure expectations.

### Fixed
- Redacted `bearer=` query parameter values anywhere auth URLs are sanitized or
  rendered in diagnostics.

## [1.11.0] - 2026-04-22

### Added
- Downstream MCP initialization now prefers protocol version `2025-11-25`,
  records negotiated protocol versions and server capabilities, and preserves
  compatibility with older supported protocol versions.
- Tool, resource, and prompt indexing now preserves modern MCP metadata
  additively, including titles, icons, output schemas, annotations,
  execution/task support hints, unknown raw metadata, and JSON Schema dialects.
- `gateway.invoke` can request downstream MCP task-augmented execution for
  task-capable tools, and required-task tools are routed through task metadata
  automatically.
- Added `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and
  `gateway.tasks_cancel` for gateway-safe downstream MCP task brokering.
- Added structured downstream auth state reporting for missing auth,
  insufficient scope, policy denial, and URL-mode elicitation, with safe
  authorization metadata discovery hints.
- Added additive gateway observability models for trace context, bounded
  structured audit events, and gateway transport diagnostics.
- `gateway.health` can now include safe `gateway_diagnostics` and recent
  redacted `audit_events`; `pmcp status --verbose` renders those diagnostics
  when a live gateway reports them.
- Streamable HTTP now reports safe `/health` transport diagnostics and tolerates
  `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, and trace context headers.
- Added CONFIG administration: `gateway.config_status`,
  `gateway.get_startup_policy`, and `gateway.set_startup_policy` expose
  source-attributed startup policy/status, preview-only default `autoStart`
  edits, explicit atomic apply, and non-secret stale/conflict diagnostics.
- `pmcp setup` now supports named profiles: `local-stdio`,
  `shared-local-http`, `authenticated-shared-http`, and `ci`.
- Registry and manifest discovery metadata can carry read-only package,
  server-card, capability, and diagnostic hints without changing provisioning
  semantics.

### Changed
- `gateway.catalog_search`, `gateway.describe`, `gateway.health`, and
  `pmcp status` can surface negotiated protocol and richer metadata without
  requiring older servers or clients to provide the new optional fields.
- Refresh, disconnect, and restart now account for active MCP tasks separately
  from PMCP pending requests and refuse active work by default.
- `gateway.auth_connect`, `pmcp status`, `pmcp doctor`, and HTTP 401 responses
  now share stricter redaction for bearer tokens, API keys, auth codes, URL
  userinfo, and sensitive query parameters.
- Tool/resource/prompt/server snapshots, pending requests, task lists, MCP
  server-facing lists, and catalog tie-breakers now use stable public ordering.

### Release Verification
- CONFORM release-gate coverage now exercises old-protocol fake payloads and
  current-protocol fake payloads across `2024-11-05`, `2025-03-26`,
  `2025-06-18`, and `2025-11-25` protocol responses.
- Local conformance tests cover modern tool/resource/prompt metadata
  preservation, task brokering, required-task capability refusal, structured
  auth and URL-mode elicitation states, trace context, audit events,
  startup-policy preview/apply behavior, and deterministic gateway/server
  ordering.
- Streamable HTTP smoke verifies `/mcp`, unauthenticated `/health` and
  `/metrics`, bearer auth, draft header tolerance, trace headers, rate-limit
  diagnostics, and existing rmcp/Codex compatibility paths with local
  Starlette/TestClient utilities only.
- Full release evidence for this gate passed locally: targeted conformance
  tests, whole phase regression, broader shared-service regression, full
  `pytest`, `ruff check`, `ruff format --check`, `mypy`, `uv build`, and local
  `pmcp status`, `pmcp doctor`, and `pmcp setup --profile ...` smoke commands.

## [1.10.0] - 2026-04-21

### Added
- `gateway.connect_server`, `gateway.disconnect_server`, and
  `gateway.restart_server` provide runtime-only lifecycle controls for known
  downstream servers with structured status output.
- `gateway.health` now includes optional startup policy fields for eager, lazy,
  skipped, policy-denied, missing-auth, and unknown `autoStart` decisions.
- `pmcp status --verbose` displays live startup policy details when available,
  including missing-auth environment variable names without exposing secret
  values.
- Startup and refresh logs now include concise policy summary counts and
  actionable messages for unknown `autoStart` and missing-auth skips.
- `pmcp doctor` now includes a named `http` check that probes gateway `/health`
  reachability without requiring bearer auth.
- Bounded multi-client soak coverage now exercises concurrent lazy invokes,
  same-server single-flight startup, refresh and lifecycle refusal/cancellation,
  health/list-pending/status visibility, and local HTTP shared-service smoke
  paths.

### Changed
- `gateway.refresh` now refuses by default while downstream requests are pending
  and reports pending-request counters. Passing `force=true` cancels those
  requests before refresh proceeds.
- `gateway.disconnect_server` and `gateway.restart_server` use the same
  target-server pending-request policy: refuse by default and cancel only that
  server's pending requests when `force=true`.
- Packaged manifest entries no longer mark Playwright or Context7 for automatic
  eager startup. Downstream servers remain lazy by default and can be eagerly
  started by adding their names to top-level `.mcp.json` `autoStart`.
- README and `pmcp setup` guidance now describe the user-owned startup model:
  `mcpServers` provides lazy availability, while `autoStart` opts selected
  servers into eager startup.
- README and SECURITY now document shared-service HTTP mode, per-source-IP
  `/mcp` rate-limit buckets, lifecycle disruption behavior, and unauthenticated
  `/health` and `/metrics` expectations.

### Migration
- To restore the previous eager startup behavior for the common browser/docs
  stack, add:
  ```json
  {
    "autoStart": ["playwright", "context7"],
    "mcpServers": {}
  }
  ```

## [1.9.2] - 2026-04-14

### Fixed
- **`gateway.request_capability` false-positive category matches** (closes #56):
  - Bug 1 — Generic keywords (e.g. "api") inflated category scores when multiple
    servers in one category each carried the same generic term. Replaced per-server
    frequency counting with category-span IDF weighting: a keyword appearing across
    N distinct categories gets weight 1.0 / 0.7 / 0.3 / 0.1 for N = 1 / 2 / 3 / 4+.
  - Bug 2 — Any non-zero score returned a category match. Added a minimum score
    threshold of 0.5 so pure generic-keyword overlap (e.g. three "api" hits × 0.1 = 0.3)
    falls through to `not_available` + `search_registry` guidance.
  - Bug 3 — Queries naming a specific unknown service (e.g. "Hostinger") still
    returned an unrelated category. Added a pre-check in `request_capability`:
    PascalCase words (non-first position) not matching any manifest server name
    cause Tier 2 to be skipped entirely, surfacing `not_available` immediately.

## [1.9.1] - 2026-04-13

### Added
- **`py.typed` marker** (`src/pmcp/py.typed`) — PEP 561 compliance; downstream
  projects using mypy/pyright now resolve PMCP types without `ignore_missing_imports`.
- **PyPI classifiers**: added `Operating System :: POSIX :: Linux`,
  `Operating System :: MacOS`, `Operating System :: Microsoft :: Windows`,
  and `Typing :: Typed`.
- **SECURITY.md**: documents threat model, known limitations, responsible disclosure
  process, and production hardening checklist.

### Fixed
- **Timing-safe auth token comparison**: replaced `!=` string equality with
  `hmac.compare_digest` to prevent timing oracle attacks on Bearer tokens.
- **Prometheus counter registration**: counters now registered at module import;
  fallback dict renderer kept in sync via `_inc()` helper so metrics are always
  visible in `generate_latest()` output.
- **Reconnect storm guard**: added `reconnecting: bool` flag to `ManagedClient`;
  prevents multiple concurrent `_reconnect_loop` tasks from spawning when a server
  exits rapidly.
- **HTTP request timeout**: tool invocations now wrapped in `asyncio.wait_for`
  (default 60 s, configurable via `--request-timeout` / `PMCP_REQUEST_TIMEOUT`);
  returns HTTP 504 on timeout.
- **Payload size limit**: `Content-Length > 10 MB` rejected with HTTP 413 before
  the body is read.
- **Windows signal handling**: `loop.add_signal_handler()` (POSIX-only) now
  guarded by `sys.platform != "win32"`; falls back to `signal.signal()`.
- CI mypy/ruff failures introduced by hardening changes.

## [1.9.0] - 2026-04-12

### Added
- **Production hardening**: authentication middleware, structured audit logging,
  sliding-window rate limiter (per-IP, configurable via env vars), and memory-leak
  fix for `_rl_store` cleanup.
- **Backstage catalog**: `catalog-info.yaml` and standard repo layout for
  Backstage/portal registration.
- **Consiliency maintenance trigger**: GitHub Actions workflow for scheduled
  maintenance worker.

### Fixed
- **rmcp/Codex HTTP transport compatibility** (closes #51): keep-alive SSE for
  session-less GETs; HTTP 202 for `notifications/initialized` without session ID;
  `_NullResponse` ASGI double-send guard.

## [1.8.1] - 2026-03-12

### Fixed
- README: corrected `pmcp setup` example to use `--mode http` (was `--mode sse`)
  and updated `pmcp doctor` comment to reflect HTTP transport.
- Test suite: resolved pre-existing failures (health isolation mock, subprocess
  PYTHONPATH, browser-invoke skip markers, ruff lint/format drift).
- Removed stale `TestBAMLSummarization` integration test (`generate_capability_summary`
  no longer makes outbound LLM calls since v1.8.0).

## [1.8.0] - 2026-03-11

### Changed
- **Transport**: Replaced deprecated SSE transport (`/sse`, `type: "sse"`) with MCP
  streamable-HTTP transport (`/mcp`, `type: "http"`). Eliminates the race condition
  where tool calls arrived before SSE session initialization completed.
  Update `~/.mcp.json`: `{"type":"http","url":"http://127.0.0.1:3344/mcp"}`
- **Capability routing**: Removed all outbound BAML/Groq LLM calls from
  `gateway.request_capability`. Replaced with three-tier pure-Python router:
  (1) sliding-window name match → single candidate, (2) category keyword match →
  all servers sorted by API-key availability, (3) not_available + search guidance.
  No API key required. New `pick_from_category` status added.
- `pmcp setup --mode sse` now generates `type: "http"` config (transport migration).

### Added
- **Background stale-version indexer**: pre-populates version check cache hourly so
  `catalog_search stale_updates` and `update_warning` fields are zero-latency.
- `stale_updates` field in `catalog_search` output listing servers with available updates.

### Fixed
- `fetch` manifest entry corrected: `@modelcontextprotocol/server-fetch` (404 on npm)
  → `uvx mcp-server-fetch` (PyPI).
- `pmcp doctor` and `pmcp setup` updated to probe/generate `/mcp` endpoint.

## [1.7.0] - 2026-03-08

### Added
- Background stale-version indexer task (see 1.8.0 above for details; released together).
- `stale_updates` field in `CatalogSearchOutput`.

### Changed
- Removed BAML outbound LLM calls from `request_capability` (see 1.8.0 above).

## [1.3.0] - 2025-01-23

### Added

- **Advanced LLM Features Documentation**: Comprehensive README section explaining optional Groq-powered capabilities
  - Semantic capability matching (vs keyword fallback)
  - LLM-generated tool summaries (vs static templates)
  - Dynamic code snippet generation
  - Step-by-step setup guide with Groq API key

- **Progressive Disclosure Integration Tests**: New test suite (`test_progressive_disclosure.py`)
  - Tests for all 8 workflow scenarios (Context7 + Playwright)
  - Coverage for search → describe → invoke workflow
  - Verification of naive prompt tool discovery

### Changed

- **Installation instructions**: Updated to prioritize `uv` as recommended package manager
- **baml-py dependency**: Updated to 0.215.2 for BAML compatibility

### Fixed

- BAML client version mismatch that prevented LLM features from working

## [1.1.0] - 2025-12-30

### Added

- **Code Execution Guidance System**: Multi-layered progressive disclosure to encourage models to use code patterns
  - **L0 (MCP Instructions)**: Brief philosophy about code execution (~30 tokens)
  - **L1 (Capability Cards)**: Ultra-terse code pattern hints during search (~8-12 tokens/card)
  - **L2 (Schema Cards)**: Optional code examples in tool details (~40-80 tokens/schema, opt-in)
  - **L3 (Methodology Resource)**: Full code execution guide (lazy-loaded via resource)

- **Guidance Configuration**: `~/.claude/gateway-guidance.yaml` for customization
  - Three levels: `off`, `minimal` (default), `standard`
  - Token budget estimation (~200 tokens in minimal mode)
  - Per-layer control for fine-grained configuration

- **Code Pattern Hints**: Keyword-based matching for common patterns
  - `loop` - For batch operations (navigate, create, update, list)
  - `filter` - For search/query operations that return many results
  - `if/else` - For conditional logic based on tool results
  - `try/catch` - For error-prone operations (invoke, execute, provision)
  - `poll` - For status checking and waiting operations

- **Code Snippet Templates**: 25+ static examples for common tools
  - Playwright browser automation
  - File system operations
  - GitHub API calls
  - Database queries
  - Optional LLM-generated examples via BAML for dynamic tools

- **CLI Commands**: New `pmcp guidance` command
  - `pmcp guidance` - Show current configuration and status
  - `pmcp guidance --show-budget` - Display token cost estimates

- **Comprehensive Tests**: 48 new test cases for guidance system
  - Configuration loading and validation
  - Token budget estimation
  - Pattern hint matching
  - Code snippet template loading
  - 86% test coverage for guidance modules

### Changed

- **MCP Server Instructions**: Updated to include code execution philosophy
- **Summary Templates**: Enhanced with progressive disclosure messaging
- **BAML Prompts**: Updated to emphasize code execution patterns

### Technical Details

- Token budget optimized: ~200 tokens in minimal mode (80% reduction vs naive approach)
- Hybrid static/LLM approach: Static templates for manifest tools, LLM generation for dynamic tools
- Graceful degradation: System works without BAML or missing template files
- No breaking changes: All existing functionality preserved

## [1.0.0] - 2025-12-29

### Added

- **MCP Gateway Server**: Meta-server that aggregates multiple MCP servers behind a single connection
- **Progressive Tool Discovery**: 9 gateway tools instead of exposing all downstream tools directly
  - `gateway.catalog_search` - Search available tools with filters
  - `gateway.describe` - Get detailed tool schemas
  - `gateway.invoke` - Call tools on downstream servers
  - `gateway.health` - Check server status
  - `gateway.refresh` - Reload server configurations
  - `gateway.request_capability` - Natural language capability matching
  - `gateway.sync_environment` - Detect available CLIs
  - `gateway.provision` - Install MCP servers on demand
  - `gateway.provision_status` - Track installation progress

- **BAML-Powered Capability Matching**: Intelligent matching of user requests to available CLIs or MCP servers
- **CLI Preference**: Prefers installed CLIs (git, docker, etc.) over MCP servers when appropriate
- **Dynamic Server Provisioning**: Install and connect to MCP servers at runtime via npx/uvx
- **Process Handoff**: Seamless adoption of npx-started servers into the gateway
- **Auto-Start Servers**: Playwright and Context7 servers start automatically
- **Server Manifest**: Curated list of 25+ MCP servers with install instructions
- **Policy Management**: Server/tool allowlists, denylists, and output processing

### Technical Details

- Pure Python implementation using `asyncio`
- JSON-RPC over stdio for MCP communication
- Supports both npm (npx) and Python (uvx) MCP servers
- Environment variable support for API keys via `.env` files
