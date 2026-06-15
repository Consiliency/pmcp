---
phase_loop_plan_version: 1
phase: REGISTRY
roadmap: specs/phase-plans-v7.md
roadmap_sha256: f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5
---

# REGISTRY: MCP Registry Consumption & Server Expansion

## Context

Phase REGISTRY implements Phase 6 of `specs/phase-plans-v7.md`: consume the official MCP Registry as discovery metadata, reconcile that metadata against PMCP's local manifest without auto-installing servers, surface registry-backed candidates through `gateway.request_capability` and `gateway.catalog_search`, and add the high-value remote vendor-official servers called out by the roadmap.

The roadmap hash was verified from `specs/phase-plans-v7.md` as `f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5`. Canonical `.phase-loop/` state exists and is authoritative: it currently records REDACT, CONCURR, and MANIFEST complete, ENVFIX blocked on a non-human concurrent-dispatch/closeout issue, AUTHRS unplanned, and REGISTRY unplanned. This artifact is the REGISTRY planning output only; REGISTRY execution must wait until `.phase-loop/` records MANIFEST and AUTHRS complete. Legacy `.codex/phase-loop/` files are compatibility artifacts only and are not used to supersede canonical state.

Current code already has a transitional registry surface in `src/pmcp/tools/handlers.py`: `gateway.search_registry`, `gateway.register_discovered_server`, and `_query_mcp_registry(...)` use a direct registry query and in-memory discovered server configs. `src/pmcp/manifest/loader.py` parses the local manifest and already carries remote metadata such as `transport`, `url`, `headers`, protected-resource metadata URLs, declared scopes, server cards, declared capabilities, discovery diagnostics, and raw discovery metadata. REGISTRY turns these seams into a reusable manifest-layer registry cache and sync path so gateway tools can consume the same offline-safe source of truth.

## Interface Freeze Gates

- [ ] IF-0-REGISTRY-1 - `src/pmcp/manifest/registry.py` provides `RegistryServerEntry`, `RegistryPackage`, `RegistryCache`, `fetch_registry_servers(endpoint: str = "https://registry.modelcontextprotocol.io/v0/servers", *, timeout: float = 5.0) -> RegistryCache`, `load_registry_cache(cache_path: Path | None = None) -> RegistryCache | None`, and `save_registry_cache(cache: RegistryCache, cache_path: Path | None = None) -> None`. Fetching uses the official `/v0/servers` endpoint, records `fetched_at`, source endpoint, schema version, and non-secret diagnostics, tolerates preview schema drift by preserving unknown fields in `raw`, and never raises network or schema failures past the caller when a cache or local manifest fallback is available. `src/pmcp/manifest/sync.py` provides `sync_registry_to_manifest(manifest: Manifest, registry: RegistryCache) -> RegistrySyncResult`, where sync results classify `added`, `renamed`, `archived`, `replaced`, and `unchanged` server metadata without auto-installing. `CapabilityCandidate` and `CatalogSearchOutput` can carry registry-backed candidates with source, transport, package/server-card metadata, remote auth metadata, and no secret values; `gateway.request_capability` and `gateway.catalog_search` merge local manifest, configured servers, and registry cache candidates while respecting AUTHRS resource/audience metadata for remote vendor-official servers.

## Lane Index & Dependencies

- SL-0 - Registry client and cache; Depends on: (none); Blocks: SL-1, SL-2; Parallel-safe: yes
- SL-1 - Manifest sync and curated server entries; Depends on: SL-0; Blocks: SL-2, SL-3, SL-4; Parallel-safe: no
- SL-2 - Gateway registry surfacing; Depends on: SL-0, SL-1; Blocks: SL-3, SL-4; Parallel-safe: no
- SL-3 - REGISTRY docs and release notes; Depends on: SL-1, SL-2; Blocks: SL-4; Parallel-safe: no
- SL-4 - REGISTRY verification and closeout; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Registry Client and Cache

- **Scope**: Move registry fetching and cache persistence into a manifest-layer module with recorded responses in tests and no live-network test dependency.
- **Owned files**: `src/pmcp/manifest/registry.py`, `src/pmcp/manifest/__init__.py`, `tests/test_registry.py`
- **Interfaces provided**: IF-0-REGISTRY-1 registry dataclasses and cache helpers; `/v0/servers` fetcher with timeout, fallback, schema-drift diagnostics, and raw metadata preservation; recorded/mock registry fixtures for downstream lanes
- **Interfaces consumed**: existing stdlib URL fetching style in `GatewayTools._query_mcp_registry(...)`, existing `ServerConfig` discovery metadata concepts, roadmap MCP Registry endpoint requirement, and the no-live-calls test constraint
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add `tests/test_registry.py` coverage with recorded/mock `/v0/servers` payloads for vendor remote packages, stdio packages, duplicate package identifiers, unknown fields, missing optional fields, malformed entries, timeout, and stale-cache fallback.
  - test: Assert no test reaches the live registry and all diagnostics are non-secret strings.
  - impl: Add registry dataclasses and parse helpers that normalize names, packages, transport type, environment variable names, server card URL, capabilities, remote URLs, OAuth/PRM metadata, and raw metadata without assuming the preview schema is stable.
  - impl: Implement `fetch_registry_servers(...)`, `load_registry_cache(...)`, and `save_registry_cache(...)` with deterministic JSON cache output under the existing `.mcp-gateway` cache area unless a test supplies an explicit path.
  - impl: Export only the stable registry types/functions from `src/pmcp/manifest/__init__.py` if needed by callers.
  - verify: `uv run pytest tests/test_registry.py -k "registry or cache or schema or timeout"`
  - verify: `git diff --check -- src/pmcp/manifest/registry.py src/pmcp/manifest/__init__.py tests/test_registry.py`

### SL-1 - Manifest Sync and Curated Server Entries

- **Scope**: Reconcile registry metadata with the local manifest schema and update the single-writer manifest with the roadmap's high-value vendor-official entries.
- **Owned files**: `src/pmcp/manifest/sync.py`, `src/pmcp/manifest/loader.py`, `src/pmcp/manifest/manifest.yaml`, `tests/test_manifest.py`
- **Interfaces provided**: IF-0-REGISTRY-1 manifest reconciliation result; loader support for any new registry/source/status fields needed by sync and gateway tools; curated remote-first and stdio additions with placeholder headers only
- **Interfaces consumed**: SL-0 `RegistryCache`, MANIFEST IF-0-MANIFEST-1 status/transport shape, AUTHRS IF-0-AUTHRS-1 resource/audience metadata, existing `Manifest`, `ServerConfig`, `_parse_server_config(...)`, `manifest_server_to_config(...)`, and existing manifest audit tests
- **Parallel-safe**: no
- **Tasks**:
  - test: Add `tests/test_manifest.py` coverage proving registry sync flags renamed, archived, replaced, added, and unchanged entries without mutating the input manifest and without generating install commands for remote-only servers.
  - test: Add loader regressions for new registry/source/status metadata, remote URLs, placeholder `headers`, protected-resource metadata URLs, authorization-server metadata URLs, declared scopes, server card URL, declared capabilities, and discovery diagnostics.
  - test: Add manifest fixture coverage for GitHub remote, Atlassian Rovo, Cloudflare remote set, Sentry remote, Vercel, Hugging Face, and the verified stdio additions selected by the executor from recorded registry metadata.
  - impl: Add `sync_registry_to_manifest(...)` and a small result model that classifies registry entries against local manifest names, packages, status metadata, and replacements while keeping all output read-only discovery metadata.
  - impl: Extend `ServerConfig` parsing only for fields required to preserve registry-backed source/status/replacement metadata; keep existing YAML compatibility and default behavior unchanged.
  - impl: Update `src/pmcp/manifest/manifest.yaml` as the single writer for curated REGISTRY server additions, using remote transport and `${ENV_VAR}` placeholders where credentials are needed and never embedding secret values.
  - verify: `uv run pytest tests/test_manifest.py -k "registry or manifest or remote or status or sync or server_card"`
  - verify: `git diff --check -- src/pmcp/manifest/sync.py src/pmcp/manifest/loader.py src/pmcp/manifest/manifest.yaml tests/test_manifest.py`

### SL-2 - Gateway Registry Surfacing

- **Scope**: Make `gateway.request_capability`, `gateway.catalog_search`, `gateway.search_registry`, and discovered-server registration consume the manifest-layer registry cache and expose registry candidates without auto-installing.
- **Owned files**: `src/pmcp/tools/handlers.py`, `src/pmcp/types.py`, `tests/test_tools.py`, `tests/test_offline_discovery.py`
- **Interfaces provided**: IF-0-REGISTRY-1 gateway integration; registry-backed `CapabilityCandidate` metadata; catalog output that can include registry candidate cards alongside local/cached tools; updated search/register flow backed by the shared registry parser
- **Interfaces consumed**: SL-0 registry client/cache, SL-1 sync result and manifest metadata, AUTHRS IF-0-AUTHRS-1 remote resource/audience fields, existing `CapabilityCandidate`, `CapabilityResolution`, `CatalogSearchOutput`, `SearchRegistryResult`, `SearchRegistryInput`, `_build_manifest_with_config_servers(...)`, `_get_server_env_metadata(...)`, `request_capability(...)`, and `catalog_search(...)`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add `tests/test_tools.py` coverage where an unknown explicit service query returns registry-backed candidates instead of only `not_available` search guidance when the cache has a relevant match.
  - test: Add category/query coverage proving local manifest candidates remain preferred when stronger, registry candidates are included with `source="registry"` metadata when relevant, and policy-denied servers are omitted.
  - test: Add remote vendor-official coverage proving `gateway.request_capability` and `catalog_search` preserve transport, package/server-card, protected-resource metadata URL, authorization-server metadata URL, declared scopes, and placeholder header names without resolving or printing credential values.
  - test: Update `tests/test_offline_discovery.py` so cached tool cards and registry candidate cards coexist without inflating live tool counts or hiding CLI hints.
  - impl: Replace direct `_query_mcp_registry(...)` parsing with SL-0 registry parsing while retaining the public `gateway.search_registry` and `gateway.register_discovered_server` contracts.
  - impl: Extend `CapabilityCandidate`, `SearchRegistryResult`, or `CatalogSearchOutput` with optional registry/source fields only; keep existing required fields and status literals compatible.
  - impl: Merge registry-backed candidates into `request_capability(...)` after configured/local explicit-name matching and before final `not_available`, and merge registry cards into `catalog_search(...)` when query/include options request discoverable offline candidates.
  - impl: Ensure `gateway.provision` still requires an explicit selected server and does not auto-install or auto-connect registry results.
  - verify: `uv run pytest tests/test_tools.py tests/test_offline_discovery.py -k "registry or capability or catalog or search_registry or register_discovered"`
  - verify: `git diff --check -- src/pmcp/tools/handlers.py src/pmcp/types.py tests/test_tools.py tests/test_offline_discovery.py`

### SL-3 - REGISTRY Docs and Release Notes

- **Scope**: Document registry-backed discovery, curated vendor-official entries, and the no-auto-install safety boundary.
- **Owned files**: `README.md`, `CHANGELOG.md`
- **Interfaces provided**: operator docs for registry cache/fallback behavior; user-facing description of registry-backed candidates in request and catalog flows; release-note entry for REGISTRY behavior
- **Interfaces consumed**: IF-0-REGISTRY-1, SL-1 curated manifest entries, SL-2 gateway behavior, existing README discovery/provisioning sections, and existing changelog style
- **Parallel-safe**: no
- **Tasks**:
  - test: Review examples to ensure they use placeholder environment variable names only and do not imply that PMCP auto-installs registry results.
  - impl: Update README discovery/provisioning docs to explain local manifest vs registry-backed candidates, cache fallback, remote vendor-official auth metadata, and the explicit `gateway.provision` boundary.
  - impl: Update CHANGELOG with the REGISTRY feature summary and compatibility notes.
  - verify: `git diff --check -- README.md CHANGELOG.md`

### SL-4 - REGISTRY Verification and Closeout

- **Scope**: Run REGISTRY verification, confirm IF-0-REGISTRY-1 is fully represented, and prepare runner closeout evidence without owning additional source files.
- **Owned files**: none
- **Interfaces provided**: REGISTRY verification evidence; IF-0-REGISTRY-1 completion checklist; phase-owned dirty-path inventory for SL-0 through SL-3
- **Interfaces consumed**: IF-0-REGISTRY-1, SL-0 registry client/cache results, SL-1 manifest sync and curated entries, SL-2 gateway surfacing, SL-3 docs, roadmap REGISTRY exit criteria, and `.phase-loop/` dependency state showing MANIFEST and AUTHRS complete before execution closeout
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm no lane-owned file overlaps another lane-owned file and no executor touched files outside the active REGISTRY ownership set.
  - test: Confirm every REGISTRY exit criterion has focused regression or contract coverage in `tests/test_registry.py`, `tests/test_manifest.py`, `tests/test_tools.py`, or `tests/test_offline_discovery.py`.
  - test: Confirm all registry tests use recorded/mock responses and no verification command needs live registry access or credentials.
  - verify: `uv run pytest tests/test_registry.py tests/test_manifest.py tests/test_offline_discovery.py tests/test_tools.py -k "registry or capability or catalog or sync or remote or server_card"`
  - verify: `TMPDIR=/var/tmp uv run pytest`
  - verify: `uv run ruff check .`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `uv build`
  - verify: `git diff --check`
  - verify: `git status --short`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
uv run pytest tests/test_registry.py tests/test_manifest.py tests/test_offline_discovery.py tests/test_tools.py -k "registry or capability or catalog or sync or remote or server_card"
TMPDIR=/var/tmp uv run pytest
uv run ruff check .
uv run mypy src/pmcp --exclude baml_client
uv build
git diff --check
git status --short
```

Effective automation.suite_command:

```bash
TMPDIR=/var/tmp uv run pytest && uv run ruff check . && uv run mypy src/pmcp --exclude baml_client && uv build && git diff --check
```

## Acceptance Criteria

- [ ] Registry fetching reads `https://registry.modelcontextprotocol.io/v0/servers` with a bounded timeout, writes/reads a deterministic local cache, tolerates preview schema drift, and falls back to the cache or local manifest when the network or schema is unavailable.
- [ ] Registry tests use recorded/mock responses only and include timeout, malformed entry, unknown field, duplicate package, remote package, and stdio package cases.
- [ ] Manifest sync reconciles registry entries against the local manifest, classifies renamed/archived/replaced/added/unchanged entries, and never auto-installs or auto-connects registry servers.
- [ ] `manifest.yaml` includes the high-value remote vendor-official entries named by the roadmap plus verified stdio additions, with correct transport, URL/package metadata, protected-resource/auth metadata where available, and placeholder headers only.
- [ ] `gateway.request_capability` and `gateway.catalog_search` can surface registry-backed candidates alongside local manifest/configured candidates while preserving local matches, policy filtering, and AUTHRS resource/audience metadata for remote servers.
- [ ] `gateway.search_registry` and `gateway.register_discovered_server` keep their public contracts but use the shared registry parser/cache path so search, registration, request, and catalog behavior agree.
- [ ] README and CHANGELOG document registry-backed discovery, cache/offline behavior, curated remote entries, and the explicit no-auto-install boundary.
- [ ] REGISTRY target tests, full `pytest` with `TMPDIR=/var/tmp`, `ruff`, CI mypy baseline, `uv build`, and `git diff --check` pass.
