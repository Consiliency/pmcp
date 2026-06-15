---
phase_loop_plan_version: 1
phase: REGFIX
roadmap: specs/phase-plans-v8.md
roadmap_sha256: 3e07c3a7e8ca6399747a99b81f7575b58dc48ed79af11ef428715a78784342b7
---

# REGFIX: Registry & Discovery Correctness

## Context

Phase REGFIX implements Phase 4 of `specs/phase-plans-v8.md`: make MCP Registry discovery remote-aware, latest-only, paginated, async, bounded, cached on a stable path, and aligned with project-scoped remote-header credentials.

The roadmap hash was verified from `specs/phase-plans-v8.md` as `3e07c3a7e8ca6399747a99b81f7575b58dc48ed79af11ef428715a78784342b7`. Canonical `.phase-loop/` state marks COMPLETE as complete at commit `f80acc135634cc82b7142ce641d9ea82380d4d58` and REGFIX as unplanned; legacy `.codex/phase-loop/` state is compatibility-only and is not authoritative for this run. REGFIX depends on COMPLETE because both phases touch `src/pmcp/tools/handlers.py` and `src/pmcp/client/manager.py`; that dependency is satisfied in canonical state.

Current code has the target seams described by the roadmap: `RegistryServerEntry` is a dataclass in `src/pmcp/manifest/registry.py`, registry fetch still uses synchronous `urllib.request.urlopen`, the default registry cache is cwd-relative `.mcp-gateway/registry-cache.json`, `GatewayTools.search_registry(...)` still re-parses raw registry payloads returned by `_query_mcp_registry(...)`, `_legacy_query_mcp_registry(...)` remains as a dead direct-fetch path, `_unknown_service` treats the leading capitalized word in a natural-language query as an unknown service, and `ClientManager` remote-header resolution does not receive the project root that `GatewayTools.auth_connect(...)` uses when storing project-scoped credentials.

## Interface Freeze Gates

- [ ] IF-0-REGFIX-1 - `RegistryServerEntry` carries `remotes[]` and outer `_meta` status / official `isLatest`; `fetch_registry_servers(...)` is async, aiohttp-backed, in-process cached, size-bounded, cursor-paginated through `metadata.nextCursor`, latest-only by `?version=latest` or `isLatest` filtering, deduplicated by stable server/package identity, and degrades to local manifest/cache candidates without crashing; registry cache files resolve under a stable cache base rather than the current working directory; `gateway.search_registry` and request-capability registry candidates consume typed `RegistryServerEntry` values instead of re-parsing `.raw`; `_legacy_query_mcp_registry(...)` is removed; `_unknown_service` does not classify the first capitalized word of a sentence such as `Search the web` as an unknown service; and `gateway.auth_connect(scope="project")` writes to the same project root that `ClientManager` remote-header reads use during remote connect.

## Lane Index & Dependencies

- SL-0 - Registry model, async fetch, cache, and sync tests; Depends on: (none); Blocks: SL-1, SL-3; Parallel-safe: yes
- SL-1 - Discovery handler registry consumption and unknown-service matching; Depends on: SL-0; Blocks: SL-3; Parallel-safe: no
- SL-2 - Project-root remote auth read path; Depends on: (none); Blocks: SL-3; Parallel-safe: yes
- SL-3 - REGFIX verification and reducer closeout; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Registry Model, Async Fetch, Cache, and Sync Tests

- **Scope**: Update the manifest-layer registry contract so remote registry entries, outer metadata, pagination, latest filtering, deduplication, stable cache paths, and offline-safe fetch failures are represented once and tested with recorded registry fixtures.
- **Owned files**: `src/pmcp/manifest/registry.py`, `src/pmcp/manifest/sync.py`, `src/pmcp/types.py`, `tests/test_registry.py`, `tests/test_manifest.py`, `tests/fixtures/registry/v0_servers_page1.json`, `tests/fixtures/registry/v0_servers_page2.json`
- **Interfaces provided**: IF-0-REGFIX-1 registry model/fetch/cache surface; `RegistryRemote` metadata for streamable-http and sse registry remotes; `RegistryServerMeta` outer status and isLatest metadata; async `fetch_registry_servers(...)`; stable registry cache path helper; deduplicated latest registry cache semantics; typed registry data available to handler lanes
- **Interfaces consumed**: pre-existing `RegistryServerEntry`, `RegistryPackage`, `RegistryCache`, `sync_registry_to_manifest(...)`, `SearchRegistryResult`, `CapabilityCandidate`, aiohttp client APIs already available in the project, MCP Registry `/v0/servers` response shape with `servers[]` and `metadata.nextCursor`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Replace the inline registry payload in `tests/test_registry.py` with recorded `/v0/servers` page fixtures under `tests/fixtures/registry/`, including a remote-only entry with `remotes[]`, outer `_meta` status, `official.isLatest`, a duplicate older version, and a second page reached by `metadata.nextCursor`.
  - test: Add failing-first coverage proving a remote-only registry entry parses to `RegistryServerEntry.remotes[0]` with transport, URL, and placeholder header metadata, while unknown remote fields and outer `_meta` are preserved without leaking secret values in diagnostics.
  - test: Add async fetch coverage proving aiohttp pagination follows `metadata.nextCursor`, requests latest-only data through `version=latest` when the endpoint supports it, caps page count and response bytes, coalesces concurrent in-process fetch callers through the registry cache, deduplicates duplicate server/package identities to the latest `isLatest` entry, and returns local cache/empty diagnostics instead of raising on network failures.
  - test: Add cache-path coverage proving default registry cache IO is anchored under a stable PMCP cache base, not relative to `Path.cwd()`, and that explicit `cache_path` arguments still round-trip deterministically.
  - test: Extend `tests/test_manifest.py` sync/classification coverage so registry entries with remotes and `_meta` still classify as added, renamed, replaced, archived, or unchanged without mutating the manifest.
  - impl: Add dataclasses for registry remotes and outer metadata, parse `server.remotes[]` plus outer `_meta`, keep `packages[]` compatibility, and surface additive `remotes` / registry metadata fields on public typed outputs in `src/pmcp/types.py` without changing existing response field shapes.
  - impl: Convert `fetch_registry_servers(...)` to an async aiohttp implementation with bounded JSON reads, cursor pagination, latest filtering/deduplication, an in-process TTL cache keyed by endpoint/options, and non-secret diagnostics for failures and explicit caps.
  - impl: Replace the cwd-relative `DEFAULT_REGISTRY_CACHE` behavior with a stable cache-path helper while preserving explicit `cache_path` arguments for tests and operator overrides.
  - impl: Keep `sync_registry_to_manifest(...)` read-only and adjust only enough to consume the richer typed registry entries.
  - verify: `uv run pytest tests/test_registry.py tests/test_manifest.py -k "registry or remotes or latest or paginat or cache or sync"`
  - verify: `git diff --check -- src/pmcp/manifest/registry.py src/pmcp/manifest/sync.py src/pmcp/types.py tests/test_registry.py tests/test_manifest.py tests/fixtures/registry/v0_servers_page1.json tests/fixtures/registry/v0_servers_page2.json`

### SL-1 - Discovery Handler Registry Consumption and Unknown-Service Matching

- **Scope**: Make gateway discovery consume typed registry entries directly, preserve additive remote metadata in search/capability outputs, remove dead direct-registry parsing, and stop skipping manifest category matching for leading sentence words.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`, `tests/test_offline_discovery.py`
- **Interfaces provided**: IF-0-REGFIX-1 handler/discovery surface; typed `gateway.search_registry` result construction from `RegistryServerEntry`; request-capability registry candidates with remote URLs and metadata; `_unknown_service` matching that ignores the first title-cased sentence word; project-scope `auth_connect` write-side regression coverage for R8
- **Interfaces consumed**: SL-0 `RegistryServerEntry.remotes`, SL-0 registry metadata fields, SL-0 async `fetch_registry_servers(...)` / cache helper, pre-existing `GatewayTools._registry_matches(...)`, `GatewayTools._registry_candidate_for_entry(...)`, `GatewayTools._load_registry_candidates(...)`, `GatewayTools.auth_connect(...)`, `CapabilityCandidate`, `SearchRegistryResult`, manifest category matching
- **Parallel-safe**: no
- **Tasks**:
  - test: Update `tests/test_tools.py` registry search tests to monkeypatch typed `RegistryServerEntry` instances instead of raw dictionaries, proving `gateway.search_registry` deduplicates packages, preserves remote URL/transport/header metadata, exposes status/isLatest diagnostics, and never re-parses `.raw` to build results.
  - test: Add request-capability coverage proving a remote-only registry candidate is returned from typed registry metadata with `source="registry"`, remote transport, package or URL identity, auth metadata, declared scopes/capabilities, and API-key availability based on remote header placeholders.
  - test: Add a failing-first `_unknown_service` regression where `gateway.request_capability(query="Search the web")` category-matches the packaged manifest instead of skipping to registry/not-available just because `Search` is capitalized at the start of the sentence; keep a non-leading unknown PascalCase service query on the registry fallback path.
  - test: Add or extend `tests/test_offline_discovery.py` coverage proving cached tools and typed registry candidates still coexist when `include_offline=True` and registry fetch degrades to local cache.
  - test: Add an R8 write-side assertion that `gateway.auth_connect(scope="project", project_root=<explicit>)` stores the credential under the explicit project root even when cwd contains an unrelated project marker.
  - impl: Replace `_query_mcp_registry(...)` raw payload return behavior with typed registry-entry matching or remove it after migrating `search_registry(...)`; build `SearchRegistryResult` and registry candidates from `RegistryServerEntry` fields directly.
  - impl: Remove `_legacy_query_mcp_registry(...)` and its direct registry endpoint/import path; keep registry IO routed through the SL-0 parser/cache/fetch helper.
  - impl: Make `_load_registry_candidates(...)` await or otherwise bridge the SL-0 async fetch path without blocking the event loop, and degrade to the local cache/empty candidate list with non-secret diagnostics when live registry fetch fails.
  - impl: Change `_unknown_service` so the first token of a natural-language sentence is not treated as a service name candidate solely because it is capitalized; preserve unknown-service detection for later PascalCase tokens and known-server normalization.
  - verify: `uv run pytest tests/test_tools.py tests/test_offline_discovery.py -k "search_registry or registry_candidates or unknown_service or include_offline or auth_connect or project_root"`
  - verify: `git diff --check -- src/pmcp/tools/handlers.py tests/test_tools.py tests/test_offline_discovery.py`

### SL-2 - Project-Root Remote Auth Read Path

- **Scope**: Thread the explicit project root from `GatewayServer` into `ClientManager` and remote-header resolution so project-scoped credentials written through `gateway.auth_connect` are read by remote streamable-http and SSE connects.
- **Owned files**: `src/pmcp/client/manager.py`, `src/pmcp/server.py`, `tests/test_client_manager.py`, `tests/test_server.py`
- **Interfaces provided**: IF-0-REGFIX-1 remote-header project-root read contract; `ClientManager(project_root=...)` constructor behavior; `_remote_headers(..., project_root=...)` helper behavior; `GatewayServer(project_root=...)` forwarding into both `GatewayTools` and `ClientManager`
- **Interfaces consumed**: pre-existing `build_remote_header_env_lookup(project_root)`, `resolve_remote_headers_for_tenant(..., project_root=...)`, `set_env_value(scope, key, value, project_root)`, `GatewayTools.auth_connect(...)`, `RemoteMcpServerConfig.headers`, `ClientManager._connect_sse(...)`, `ClientManager._connect_streamable_http(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add failing-first `tests/test_client_manager.py` coverage proving `_remote_headers(...)` and remote connect paths read project-scoped env files from an explicit project root instead of cwd-derived or global locations; include streamable-http and SSE remote configs.
  - test: Extend tenant-context remote-header tests so adding `project_root` does not break tenant-isolated resolution or process-env precedence.
  - test: Add `tests/test_server.py` initialization coverage proving `GatewayServer(project_root=...)` passes the same root to its `GatewayTools` and `ClientManager` instances.
  - impl: Add an optional `project_root: Path | None` to `ClientManager.__init__`, store it, and pass it through `_remote_headers(...)` from `_connect_sse(...)` and `_connect_streamable_http(...)`.
  - impl: Add an optional `project_root` keyword to `_remote_headers(...)`, forwarding it to `resolve_remote_headers_for_tenant(...)`; preserve current default behavior when no project root is configured.
  - impl: Update `GatewayServer.__init__(project_root=...)` to construct `ClientManager(..., project_root=project_root)` so handler write-side and manager read-side use the same root.
  - verify: `uv run pytest tests/test_client_manager.py tests/test_server.py -k "remote_headers or project_root or GatewayServer"`
  - verify: `git diff --check -- src/pmcp/client/manager.py src/pmcp/server.py tests/test_client_manager.py tests/test_server.py`

### SL-3 - REGFIX Verification and Reducer Closeout

- **Scope**: Verify the registry, discovery, and remote-auth fixes together, confirm IF-0-REGFIX-1 is fully produced, and inventory dirty paths for runner closeout.
- **Owned files**: none
- **Interfaces provided**: REGFIX verification evidence; IF-0-REGFIX-1 completion checklist; phase-owned dirty-path inventory for runner closeout
- **Interfaces consumed**: IF-0-REGFIX-1; SL-0 registry model/fetch/cache implementation and tests; SL-1 discovery handler implementation and tests; SL-2 project-root remote-auth implementation and tests; roadmap REGFIX exit criteria
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm no lane-owned file overlaps another lane-owned file and no executor touched files outside the REGFIX owned-file inventory.
  - test: Confirm `RegistryServerEntry` remote and metadata fields are additive and existing gateway response fields keep their shape.
  - test: Confirm tests use recorded registry fixtures or mocked aiohttp responses only; no live registry network dependency is introduced.
  - verify: `uv run pytest tests/test_registry.py tests/test_manifest.py tests/test_offline_discovery.py tests/test_tools.py tests/test_client_manager.py tests/test_server.py -k "registry or remotes or latest or paginat or project_root or unknown_service"`
  - verify: `TMPDIR=/var/tmp uv run ruff check src/ tests/`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `TMPDIR=/var/tmp uv run pytest -q`
  - verify: `git diff --check`
  - verify: `git status --short`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-3: work-unit=`phase_reducer`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
uv run pytest tests/test_registry.py tests/test_manifest.py tests/test_offline_discovery.py tests/test_tools.py tests/test_client_manager.py tests/test_server.py -k "registry or remotes or latest or paginat or project_root or unknown_service"
TMPDIR=/var/tmp uv run ruff check src/ tests/
uv run mypy src/pmcp --exclude baml_client
TMPDIR=/var/tmp uv run pytest -q
git diff --check
git status --short
```

## Acceptance Criteria

- [ ] `RegistryServerEntry` carries parsed `remotes[]` entries with streamable-http/sse URL and placeholder-header metadata, plus preserved outer `_meta` status and official `isLatest`; a remote-only registry entry becomes a usable typed candidate.
- [ ] Registry listing is latest-only and deduplicated, using `?version=latest` and/or `isLatest` filtering so duplicate server/package entries do not reach gateway search outputs.
- [ ] `fetch_registry_servers(...)` is async, aiohttp-backed, bounded by page count and response size, paginates through `metadata.nextCursor`, caches in process, and degrades to local cache/empty diagnostics without crashing on network or parse failures.
- [ ] Registry cache files default to a stable PMCP cache base rather than the current working directory, while explicit cache paths remain supported for tests/operators.
- [ ] `gateway.search_registry` and request-capability registry candidates consume typed `RegistryServerEntry` values directly, preserve remote/auth metadata, and no longer depend on dead raw re-parsing or `_legacy_query_mcp_registry(...)`.
- [ ] `gateway.auth_connect(scope="project")` and `ClientManager` remote-header reads resolve the same explicit project root; with `--project <path>` different from cwd, store-then-connect succeeds for remote-header auth.
- [ ] `_unknown_service` does not classify the first title-cased word in a sentence as an unknown service, so queries such as `Search the web` can still category-match the manifest; later unknown service names still route toward registry guidance.
- [ ] New tests use recorded registry fixtures or mocked aiohttp responses only, never live registry network calls.
- [ ] `ruff`, mypy, and full `pytest` pass with `TMPDIR=/var/tmp` for commands that need a temporary directory outside `/tmp`.
