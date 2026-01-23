# PMCP Gateway Orchestration Fix Plan (Specification)

## Purpose
This specification describes the **exact implementation plan** to address the orchestration shortcomings identified in the PMCP gateway for multi-client usage (Claude Code, Codex, Gemini CLI). It is intentionally detailed so an implementation agent can execute it with minimal ambiguity. It favors **repurposing existing code** and only adds new code where needed.

## Scope
The plan covers the following gaps:
1. **Gateway transport is stdio-only** (no shared HTTP/SSE daemon mode).
2. **Eager downstream server startup** (no lazy start).
3. **Doc mismatch**: README claims 9 tools; implementation exposes 10 tools.
4. **Singleton lock scope** is per cache dir, so multiple project dirs can run parallel gateways.
5. **Cross-client documentation** lacks Codex/Gemini CLI minimal config guidance.

The plan is divided into **spec phases**, each of which enumerates file-level and symbol-level changes.

---

## Phase 0: Baseline inventory & non-functional constraints

### Constraints (must remain true)
- **Do not remove stdio transport.** Keep it as default for backwards compatibility.
- Maintain **gateway tool surface** (`gateway.*`) as the only exposed tool set.
- Continue to use **policy enforcement** on gateway tools/resources/prompts.
- Favor **reuse** of existing gateway bootstrap, config, and client manager logic.

### Non-goals
- Changing downstream server protocol (still uses MCP stdio in children).
- Changing tool schemas or tool output formats unless required for compatibility.

---

## Phase 1: Add HTTP transport (shared gateway daemon mode)

### Rationale
The gateway currently runs only on stdio, which forces one gateway process per client session. This leads to **duplicate downstream servers** and resource contention. Adding an HTTP transport mode allows multiple clients to connect to a **single long-lived gateway** process.

### Files / Symbols to modify or add

#### 1. `src/pmcp/server.py`
- **Modify `GatewayServer.run()`** to optionally start an HTTP transport server **instead of** stdio.
  - Current method: `GatewayServer.run()` uses `mcp.server.stdio.stdio_server()` exclusively.
  - Change: Add a transport selection branch based on CLI/config (see Phase 2 for config options).
  - Keep existing stdio flow as default.
- **Add new method** `GatewayServer.run_http()` (or `GatewayServer._run_http()`) that:
  - Reuses existing server initialization (`initialize()`), tool handlers, and policy logic.
  - Uses the MCP SDK HTTP/SSE transport (whichever is supported by the Python MCP SDK in use).
  - **Reuses** existing `Server` instance (`self._server`) and its handlers. Avoid code duplication.

#### 2. `src/pmcp/cli.py`
- **Modify argument parser** to support a transport mode (e.g., `--transport stdio|http`) and HTTP binding options (e.g., `--host`, `--port`).
  - Update `run_server()` to select between `GatewayServer.run()` (stdio) and `GatewayServer.run_http()` based on CLI args and environment overrides.
- **Add environment overrides** to parallel existing behavior (e.g., `PMCP_TRANSPORT`, `PMCP_HOST`, `PMCP_PORT`).

#### 3. `src/pmcp/types.py`
- **Add a `GatewayTransport` literal or enum** (e.g., `Literal["stdio", "http"]`).
  - Rationale: centralized typing for CLI/config handling.

#### 4. (Optional) `src/pmcp/config/loader.py`
- **No changes required** unless a config file format is introduced for transport settings. Prefer CLI/env for now to avoid migration complexity.

#### 5. Documentation updates
- **`README.md`**
  - Add a short section: “HTTP Daemon Mode” with example start command (e.g., `pmcp --transport http --host 127.0.0.1 --port 3344`).
  - Explain that **multiple clients can point to the same daemon** for deduped downstream servers.

### Implementation notes
- **Prefer reuse** of `GatewayServer.initialize()` and the existing `_server` handlers; do not fork separate tool definitions.
- Use the MCP SDK’s **canonical HTTP transport** for Python (confirm its module path before implementation).

---

## Phase 2: Fix lazy-start semantics for downstream servers

### Rationale
`GatewayServer.initialize()` currently **connects all servers** eagerly via `ClientManager.connect_all()`. This contradicts progressive disclosure and increases startup cost.

### Target behavior
- **Auto-start servers** should still start at boot (as designed).
- **Non-auto-start servers** (from config) should be connected **on first use** (e.g., on `gateway.invoke`, `gateway.describe`, or `gateway.catalog_search` if include_offline is false).

### Files / Symbols to modify or add

#### 1. `src/pmcp/server.py`
- **Modify `GatewayServer.initialize()`** to:
  - Load configs as today.
  - Separate configs into:
    - `auto_start_configs` (manifest auto-start servers + any config explicitly marked auto-start if such a field exists in config in the future).
    - `lazy_configs` (everything else).
  - Call `ClientManager.connect_all()` **only** on `auto_start_configs`.
  - Store `lazy_configs` in `GatewayServer` for later use (e.g., `self._lazy_configs`).

#### 2. `src/pmcp/tools/handlers.py`
- **Modify `GatewayTools.invoke()`** to ensure the target server is connected before invoking:
  - If the tool’s server is offline, call a new helper to connect the server on-demand.
- **Modify `GatewayTools.describe()`** similarly when a tool is requested from an offline server.
- **Modify `GatewayTools.catalog_search()`**:
  - Option A (preferred minimal change): Keep current behavior; it only searches already-indexed tools (connected servers). This preserves behavior but means catalog won’t show tools from offline servers.
  - Option B (expanded): If `include_offline` is true, include tool entries from cached descriptions or configuration. This requires description cache support (see Phase 3 optional).

#### 3. `src/pmcp/client/manager.py`
- **Add new method** `ensure_connected(server_name: str) -> None`:
  - If server is already online/connecting, no-op.
  - Else, look up a stored config and call `_connect_server()`.
  - **Note**: This requires storing configs in `ClientManager` for later use (see below).
- **Modify `connect_all()`** to retain a mapping of server name → config (e.g., `self._configs_by_name`).
  - This mapping is used by `ensure_connected`.

#### 4. `src/pmcp/types.py`
- **No changes required** unless new config state types are needed (prefer minimal additions).

### Reasoning for modifications
- Reuses existing `_connect_server()` logic and avoids introducing a new execution path for downstream connections.
- Keeps resource indexing centralized in `ClientManager` as currently designed.

---

## Phase 3: Fix doc mismatches (tool count, client config guidance)

### Rationale
Docs claim 9 tools but gateway exposes 10. Also lacks Codex/Gemini client config guidance, creating cross-client adoption risk.

### Files / Symbols to modify

#### 1. `README.md`
- **Update “9 meta-tools”** to “10 meta-tools”.
- **Add tool list entries** for:
  - `gateway.list_pending`
  - `gateway.cancel`
- **Add a new section** “Client Configuration Examples” with minimal config snippets for:
  - Claude Code (existing snippet; keep).
  - Codex (add a minimal MCP config pointing to pmcp gateway).
  - Gemini CLI (add a minimal MCP config pointing to pmcp gateway).
- **Add a note** that Codex/Gemini paths and formats are **client-specific** and must be verified in each client’s docs if not confirmed in repo.

### Reasoning
This is a purely documentation change that aligns docs with implementation and improves cross-client onboarding without altering runtime behavior.

---

## Phase 4: Improve singleton lock scope (prevent multi-gateway duplication)

### Rationale
Singleton lock is currently based on the gateway’s cache directory, so different project directories can run concurrent gateways, which can each spawn their own downstream servers.

### Target behavior
- Provide a **global lock default** to ensure one gateway process per user (unless explicitly overridden).
- Allow opt-out by explicitly passing a custom lock/cache dir in CLI.

### Files / Symbols to modify

#### 1. `src/pmcp/identity.py`
- **Modify `acquire_singleton_lock()`** to default to `Path.home() / ".pmcp"` **unless explicitly provided** by caller.
  - This is already the default when `lock_dir is None`, but the current caller passes a cache dir (project-local).

#### 2. `src/pmcp/server.py`
- **Modify `GatewayServer.run()`** to pass `None` (or a dedicated lock dir parameter) instead of `self._cache_dir` to `acquire_singleton_lock()`.
  - This allows the lock to become globally scoped by default.
- **Optional**: Add a new constructor parameter `lock_dir: Path | None` if you want to preserve custom control separate from cache directory.

#### 3. `src/pmcp/cli.py`
- **Add CLI flag** `--lock-dir` (optional) to override the default global lock location.
- Pass `lock_dir` through to `GatewayServer`.

### Reasoning
Reuses existing lock mechanism but changes the default scope to reduce duplication without removing explicit override capability.

---

## Phase 5 (Optional): Cache-backed offline discovery for lazy servers

### Rationale
If lazy-start is implemented, `catalog_search` currently only sees tools from **connected servers**. The repo already has a descriptions cache mechanism (manifest refresher), which can be leveraged to show offline tools for better discovery.

### Files / Symbols to modify

#### 1. `src/pmcp/server.py`
- **Already loads** descriptions cache via `load_descriptions_cache()`.
- **No new changes required** unless you choose to expose cached tool metadata to the gateway tool layer.

#### 2. `src/pmcp/tools/handlers.py`
- **Modify `GatewayTools.catalog_search()`** to include cached tool info when `include_offline` is true.
  - This requires passing the descriptions cache (or a lightweight index) from `GatewayServer` to `GatewayTools` during initialization.
  - Keep “online only” default behavior unchanged.

#### 3. `src/pmcp/types.py`
- **No new types** unless a lightweight `CachedToolInfo` is needed; prefer reusing existing description cache structures.

### Reasoning
This keeps discovery usable while still delaying actual server startup until needed.

---

## Phase 6: Tests and validation updates

### Files / Symbols to modify or add

#### 1. `tests/test_config_loader.py`
- **No change required** (existing precedence tests remain valid).

#### 2. Add new tests (suggested)
- **`tests/test_gateway_lazy_start.py`**
  - Validate that `GatewayServer.initialize()` only connects auto-start servers.
  - Validate that `GatewayTools.invoke()` connects a lazy server on first use.
- **`tests/test_gateway_transport.py`**
  - Validate that CLI accepts `--transport http` and dispatches to HTTP runner.
  - This can be a unit test with mocking rather than an integration test.
- **`tests/test_singleton_lock.py`**
  - Validate that the lock uses global default unless `--lock-dir` is provided.

### Reasoning
Tests prevent regressions in key orchestration behaviors without requiring live MCP servers.

---

## Deliverables checklist

### Code changes (expected)
- `src/pmcp/server.py`
  - Add HTTP transport support and lazy-start orchestration.
  - Modify lock behavior to be global by default.
- `src/pmcp/cli.py`
  - Add transport and lock-dir flags and environment overrides.
- `src/pmcp/client/manager.py`
  - Add config registry and `ensure_connected()` to support lazy-start.
- `src/pmcp/types.py`
  - Add transport typing where appropriate.
- `README.md`
  - Fix tool count + add list_pending/cancel.
  - Add multi-client configuration guidance for Claude/Codex/Gemini.

### Optional changes
- `src/pmcp/tools/handlers.py`
  - Use cached descriptions for offline catalog discovery.

---

## Implementation notes for the agent
- Favor **reusing** existing code paths (`ClientManager._connect_server()` and `GatewayServer.initialize()`).
- Keep **stdio** as the default transport to avoid breaking existing users.
- Keep the **tool surface stable**; do not add/remove gateway tools as part of this plan.
- Avoid speculative config file paths for Codex/Gemini; if not confirmed, explicitly mark in docs as “verify in client docs.”

---

## Acceptance criteria
- A single HTTP daemon can serve multiple clients without spawning duplicate downstream servers.
- Gateway can run in stdio mode as before.
- Non-auto-start servers do not start until first use.
- README tool list matches implementation.
- Docs include explicit minimal client config pointers for Claude/Codex/Gemini (with verification caveat if needed).
- Default singleton lock prevents multiple simultaneous gateways for the same user.
