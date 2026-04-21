# Phase roadmap v1

## Context

PMCP currently uses a packaged manifest as both a server catalog and a startup policy. A small developer-selected subset of manifest servers has `auto_start: true`, while user/project `.mcp.json` servers are registered lazily and started only when `gateway.describe`, `gateway.invoke`, or `gateway.provision` needs them. Users can disable packaged auto-start entries with `disableAutoStart`, but cannot explicitly opt selected servers into eager startup.

The desired direction is to make PMCP unopinionated about each user's downstream MCP stack: the packaged manifest should remain a catalog of provisionable defaults, and runtime startup policy should be user-owned.

## Architecture North Star

PMCP starts as a single gateway with a catalog of possible downstream servers. Downstream servers are lazy by default. Eager startup happens only when a user or project explicitly opts into it through config. Packaged manifest `auto_start` should not silently dictate process or remote connection side effects for all users.

## Assumptions

- `.mcp.json` remains the primary user/project configuration surface for downstream MCP servers.
- User config precedence remains project > user > custom for server definitions.
- Policy allow/deny checks must apply equally to lazy and eager servers.
- Missing auth for eager servers should skip connection cleanly without failing the whole gateway.
- The existing `disableAutoStart` field remains supported during migration for compatibility.
- Provisioned servers remain available after restart, but do not become eager unless explicitly listed in user startup policy.

## Non-Goals

- Do not add idle timeout shutdown in this roadmap.
- Do not remove the manifest catalog or provisioning flow.
- Do not make all `.mcp.json` entries eager by default.
- Do not change the MCP client-facing PMCP setup shape.
- Do not alter downstream tool schema/indexing semantics beyond startup policy.

## Cross-Cutting Principles

- Lazy by default: server presence means available on demand, not connected at startup.
- User intent controls eager work: only explicit local config starts downstream servers eagerly.
- Backward compatible migration: avoid surprising existing users in the first release.
- Clear observability: health/status output should distinguish lazy, auto-started, skipped, and failed servers.
- One startup policy path: both `GatewayServer.initialize()` and `gateway.refresh` should resolve eager servers through shared logic.

## Top Interface-Freeze Gates

- IF-0-CONFIG-1 — `.mcp.json` startup policy schema, including `autoStart`, compatibility treatment of `disableAutoStart`, and config precedence.
- IF-0-RESOLVER-2 — Shared startup resolver contract that turns config, manifest, provisioned registry, auth, and policy inputs into eager and lazy `ResolvedServerConfig` lists.
- IF-0-RUNTIME-3 — Gateway initialize/refresh behavior where only explicit `autoStart` entries are eagerly connected, with legacy manifest auto-start behind a compatibility path.
- IF-0-MIGRATION-4 — Documentation and release policy for deprecating developer-selected manifest `auto_start` defaults.

## Phases

### Phase 1 — Startup Policy Config Contract (CONFIG)

**Status**: Completed in `4ebb81c`.

**Objective**

Define and test the user-facing startup policy contract without changing gateway runtime behavior yet.

**Exit criteria**

- [x] `McpConfigFile` accepts `autoStart: list[str]`.
- [x] Config parsing preserves existing `disableAutoStart` behavior.
- [x] Tests cover project, user, and custom config aggregation for enabled and disabled auto-start names.
- [x] README or draft docs describe the intended semantic distinction between `mcpServers`, `autoStart`, and `disableAutoStart`.

**Scope notes**

- Add a loader helper such as `load_enabled_auto_start(...)`, parallel to `load_disabled_auto_start(...)`.
- Decide whether the canonical field is camelCase `autoStart` only, or whether snake_case aliases are accepted. Prefer camelCase to match existing `disableAutoStart`.
- Keep local/remote server config parsing unchanged except for the top-level config field.

**Non-goals**

- Do not alter `GatewayServer.initialize()` eager connection behavior in this phase.
- Do not remove manifest `auto_start` fields yet.

**Key files**

- `src/pmcp/types.py`
- `src/pmcp/config/loader.py`
- `tests/test_config_loader.py`
- `tests/test_guidance_config.py`
- `README.md`

**Depends on**

- (none)

**Produces**

- IF-0-CONFIG-1 — `.mcp.json` startup policy schema, including `autoStart`, compatibility treatment of `disableAutoStart`, and config precedence.

### Phase 2 — Shared Startup Resolver (RESOLVER)

**Status**: Completed in current working tree.

**Objective**

Create one resolver that computes lazy and eager downstream server sets from user config, manifest catalog entries, provisioned registry, auth availability, and policy allow/deny rules.

**Exit criteria**

- [x] Resolver returns separate lazy and eager config lists.
- [x] Explicit `autoStart` names can refer to configured `.mcp.json` servers or manifest-only servers.
- [x] Configured server definitions take precedence over manifest defaults when names collide.
- [x] Missing auth skips eager connection with a clear reason while keeping the server available lazily when appropriate.
- [x] Unit tests cover configured local servers, configured remote servers, manifest-only servers, unknown names, policy-denied names, and missing-auth names.

**Scope notes**

- The resolver should not perform network or process startup.
- The resolver should be usable by both server initialization and `gateway.refresh`.
- Preserve provisioned-registry behavior, but classify provisioned servers as lazy unless listed in explicit `autoStart`.
- Consider returning structured skip reasons for status/logging rather than only log strings.

**Non-goals**

- Do not implement idle shutdown or health monitor changes.
- Do not change `ClientManager.register_lazy_configs` semantics.

**Key files**

- `src/pmcp/config/loader.py`
- `src/pmcp/server.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/policy/policy.py`
- `tests/test_lazy_start.py`
- `tests/test_tools.py`

**Depends on**

- IF-0-CONFIG-1

**Produces**

- IF-0-RESOLVER-2 — Shared startup resolver contract that turns config, manifest, provisioned registry, auth, and policy inputs into eager and lazy `ResolvedServerConfig` lists.

### Phase 3 — Runtime Startup Policy Migration (RUNTIME)

**Status**: Completed in current working tree.

**Objective**

Switch gateway startup and refresh to the shared resolver so explicit user `autoStart` controls eager connections and all other configured/provisioned servers remain lazy.

**Exit criteria**

- [x] `GatewayServer.initialize()` registers lazy configs before connecting explicit eager configs.
- [x] `gateway.refresh` uses the same policy as startup.
- [x] Legacy manifest `auto_start` behavior is retained only through a compatibility path or clearly deprecated transition flag.
- [x] `gateway.health` and status output remain correct for lazy and online servers.
- [x] Existing lazy-start tests pass after being updated for the new source of eager configs.

**Scope notes**

- If retaining compatibility, choose a named flag/env/config such as `PMCP_LEGACY_MANIFEST_AUTOSTART=1` or a default-on transition option with deprecation warning.
- Ensure configured `autoStart` entries are not duplicated in lazy registration.
- Keep first-use lazy behavior unchanged: once a lazy server starts successfully, it remains up until shutdown, refresh, or disconnection.

**Non-goals**

- Do not remove manifest `auto_start` keys from `manifest.yaml` until Phase 4.
- Do not change provision/install APIs.

**Key files**

- `src/pmcp/server.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/client/manager.py`
- `tests/test_lazy_start.py`
- `tests/test_server_lifecycle.py`
- `tests/test_tools.py`

**Depends on**

- IF-0-RESOLVER-2

**Produces**

- IF-0-RUNTIME-3 — Gateway initialize/refresh behavior where only explicit `autoStart` entries are eagerly connected, with legacy manifest auto-start behind a compatibility path.

### Phase 4 — Manifest Defaults Deprecation and Docs (MIGRATION)

**Status**: Completed in current working tree.

**Objective**

Remove or neutralize developer-selected auto-start defaults and document the new user-owned startup model.

**Exit criteria**

- [x] Packaged manifest no longer causes Playwright, Context7, or any other downstream server to auto-start without user opt-in.
- [x] README examples show `autoStart` for eager startup and plain `mcpServers` for lazy availability.
- [x] `pmcp setup` output or guidance explains minimal startup versus optional presets.
- [x] Tests no longer assert developer-selected manifest auto-start defaults as required behavior.
- [x] Release notes clearly describe the migration and how to restore prior behavior.

**Scope notes**

- Prefer moving startup recommendations into docs/setup presets rather than manifest `auto_start`.
- Keep `disableAutoStart` documented as legacy compatibility if retained.
- Consider adding examples for Excalidraw as lazy and eager:
  - lazy: `mcpServers.excalidraw`
  - eager: `autoStart: ["excalidraw"]`

**Non-goals**

- Do not remove ability to auto-start servers explicitly configured by the user.
- Do not remove manifest entries for Playwright or Context7.

**Key files**

- `src/pmcp/manifest/manifest.yaml`
- `README.md`
- `src/pmcp/cli.py`
- `tests/test_manifest.py`
- `tests/test_lazy_start.py`
- `.github/workflows/test.yml`

**Depends on**

- IF-0-RUNTIME-3

**Produces**

- IF-0-MIGRATION-4 — Documentation and release policy for deprecating developer-selected manifest `auto_start` defaults.

### Phase 5 — Startup Observability and Polish (OBSERVE)

**Status**: Completed in current working tree.

**Objective**

Improve user visibility into startup policy decisions so users can understand why a server is lazy, eagerly connected, skipped, denied, or failed.

**Exit criteria**

- [x] `pmcp status --verbose` or gateway health exposes eager/lazy/skipped startup classification.
- [x] Logs include concise startup policy summaries.
- [x] Unknown `autoStart` entries produce actionable warnings.
- [x] Missing-auth eager entries show the env var/auth method without failing gateway startup.
- [x] Tests cover status/health output for lazy, eager, skipped, and policy-denied cases.

**Scope notes**

- This phase can run after the runtime behavior is stable.
- Prefer structured status fields where existing API models can accept them without breaking clients.
- If model changes are breaking, keep API changes for a later major release and use logs/docs first.

**Non-goals**

- Do not add auto-remediation or interactive prompts.
- Do not add idle timeout controls.

**Key files**

- `src/pmcp/types.py`
- `src/pmcp/tools/handlers.py`
- `src/pmcp/cli.py`
- `tests/test_cli.py`
- `tests/test_lazy_start.py`

**Depends on**

- IF-0-RUNTIME-3

**Produces**

- IF-0-OBSERVE-5 — User-visible startup classification and skip/failure reporting contract.

## Phase Dependency DAG

```text
CONFIG -> RESOLVER -> RUNTIME -> MIGRATION
                          \----> OBSERVE
```

## Execution Notes

- Phase 1 is the interface freeze for config syntax and should be completed before any runtime behavior change.
- Phase 2 can be implemented with unit-only coverage and no behavior flip, reducing migration risk.
- Phase 3 is the main behavior change and should be planned as a focused implementation phase.
- Phase 4 can follow Phase 3 immediately if backward compatibility is not required; otherwise it should wait for one release cycle.
- Phase 5 can run after Phase 3 in parallel with Phase 4 if API status changes are kept additive.

## Verification

Run these after implementation phases, not during roadmap planning:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/pmcp --exclude baml_client
uv run pytest tests/test_config_loader.py tests/test_lazy_start.py tests/test_tools.py tests/test_manifest.py -q
uv run pytest -q
```

For release-readiness after Phase 4:

```bash
uv build
pmcp status --json
pmcp setup --client claude --mode http
```
