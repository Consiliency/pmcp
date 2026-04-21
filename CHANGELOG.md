# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Packaged manifest entries no longer mark Playwright or Context7 for automatic
  eager startup. Downstream servers remain lazy by default and can be eagerly
  started by adding their names to top-level `.mcp.json` `autoStart`.
- README and `pmcp setup` guidance now describe the user-owned startup model:
  `mcpServers` provides lazy availability, while `autoStart` opts selected
  servers into eager startup.

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
