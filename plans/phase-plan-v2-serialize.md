# SERIALIZE: Shared State Serialization

## Context

Phase 1 hardens `ClientManager` shared state for multi-client HTTP gateway use. The current manager mutates `_clients`, `_tools`, `_resources`, `_prompts`, `_servers`, and `_lazy_configs` from lazy start, bulk connect, refresh, reconnect, cleanup, and disconnect paths without a common lifecycle boundary. Read methods already return list snapshots for collection-style outputs, but same-server connect paths can still duplicate connection attempts, and `disconnect_all()` currently iterates `_clients.items()` directly while awaiting shutdown work.

This phase should preserve public tool payloads and public `ClientManager` method names. The implementation target is private synchronization: a manager-level lifecycle lock for global state mutation plus per-server single-flight connection coordination for lazy start, provision/connect, bulk connect, and reconnect.

## Interface Freeze Gates

- [x] IF-0-SERIALIZE-1 — `ClientManager.ensure_connected(server_name)` is same-server single-flight: concurrent calls for one lazy server perform at most one downstream `_connect_with_retry(config)` attempt and all callers return the same success/failure outcome.
- [x] IF-0-SERIALIZE-2 — `ClientManager.connect_all(configs, retry=...)` remains cross-server concurrent while same-name configs and concurrent provision/connect paths serialize through the same per-server connection boundary.
- [x] IF-0-SERIALIZE-3 — `ClientManager.disconnect_all()` and `ClientManager.refresh(configs)` run under a global lifecycle mutation lock, iterate stable snapshots, and cannot raise because `_clients` changes while shutdown awaits.
- [x] IF-0-SERIALIZE-4 — `_clients`, `_tools`, `_resources`, `_prompts`, `_servers`, and `_lazy_configs` registry replacement/removal happens inside explicit lifecycle mutation helpers or lock-owned sections; normal downstream tool calls do not hold the global lifecycle lock while waiting for a response.
- [x] IF-0-SERIALIZE-5 — Read-only snapshot methods keep their existing signatures and return snapshot containers: `get_all_tools()`, `get_all_resources()`, `get_all_prompts()`, `get_all_server_statuses()`, `get_lazy_server_names()`, and `get_registry_meta()`.
- [x] IF-0-SERIALIZE-6 — Existing public gateway payloads, tool IDs, health fields, startup policy fields, provisioning output, and refresh output shapes remain unchanged in this phase.

## Lane Index & Dependencies

- SL-0 — Manager synchronization contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 — ClientManager concurrency tests; Depends on: SL-0; Blocks: SL-4, SL-5; Parallel-safe: yes
- SL-2 — Lazy-start race tests; Depends on: SL-0; Blocks: SL-4, SL-5; Parallel-safe: yes
- SL-3 — Tool/provision compatibility tests; Depends on: SL-0; Blocks: SL-4, SL-5; Parallel-safe: yes
- SL-4 — Phase integration review; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: SL-5; Parallel-safe: no
- SL-5 — Documentation impact and closeout; Depends on: SL-0, SL-1, SL-2, SL-3, SL-4; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Manager Synchronization Contract

- **Scope**: Implement private lifecycle and per-server synchronization in `ClientManager` without changing public method names or payload shapes.
- **Owned files**: `src/pmcp/client/manager.py`
- **Interfaces provided**: `ClientManager._lifecycle_lock`, per-server connect single-flight state such as `ClientManager._server_connect_locks` or `ClientManager._connect_tasks`, lock-owned registry mutation semantics for IF-0-SERIALIZE-1 through IF-0-SERIALIZE-5
- **Interfaces consumed**: existing `ClientManager.connect_all(...)`, `ClientManager.register_lazy_configs(...)`, `ClientManager.ensure_connected(...)`, `ClientManager._connect_with_retry(...)`, `ClientManager._connect_server(...)`, `ClientManager._cleanup_client(...)`, `ClientManager.disconnect_all()`, `ClientManager.refresh(...)`, `ClientManager._reconnect_loop(...)`, `ClientManager.adopt_process(...)`, existing `ManagedClient.reconnecting`
- **Parallel-safe**: no
- **Tasks**:
  - test: Use SL-1 and SL-2 tests to pin same-server single-flight, stable disconnect snapshots, and read snapshot behavior before or alongside implementation.
  - impl: Add private lifecycle synchronization initialized in `ClientManager.__init__`, using one manager-level `asyncio.Lock` for global lifecycle mutation and per-server coordination for connect attempts.
  - impl: Route same-server connection attempts from `ensure_connected(...)`, `connect_all(...)`, and `_reconnect_loop(...)` through one private helper that deduplicates concurrent work for the same server while preserving cross-server concurrency.
  - impl: Keep `_spawn_semaphore` as the process-spawn resource cap; do not treat it as a state consistency lock.
  - impl: Make `disconnect_all()` snapshot `list(self._clients.items())` under the lifecycle boundary before awaiting per-client shutdown, then clear `_clients`, `_tools`, `_resources`, `_prompts`, and `_servers` coherently.
  - impl: Make `refresh(configs)` acquire the lifecycle mutation boundary for disconnect/reconnect state replacement without holding the lock around unrelated downstream tool invokes.
  - impl: Ensure `_cleanup_client(...)`, reconnect, and replacement connect paths remove stale tools/resources/prompts for the affected server so catalog snapshots cannot expose stale entries after reconnect.
  - impl: Keep `get_tool(...)`, `get_all_tools()`, `get_resource(...)`, `get_all_resources()`, `get_prompt_info(...)`, `get_all_prompts()`, `get_server_status(...)`, `get_all_server_statuses()`, `get_registry_meta()`, and `is_server_online(...)` signature-compatible; only strengthen snapshot behavior where needed.
  - verify: `uv run ruff check src/pmcp/client/manager.py`

### SL-1 — ClientManager Concurrency Tests

- **Scope**: Add direct manager tests for connect single-flight, stable disconnect snapshots, refresh serialization, and read-only snapshots.
- **Owned files**: `tests/test_client_manager.py`
- **Interfaces provided**: regression coverage for IF-0-SERIALIZE-2, IF-0-SERIALIZE-3, IF-0-SERIALIZE-4, and IF-0-SERIALIZE-5
- **Interfaces consumed**: private synchronization behavior from SL-0, existing `ClientManager`, `ManagedClient`, `PendingRequest`, `ServerStatus`, `ServerStatusEnum`, current async mock patterns in `TestParallelConnections`, `TestDisconnectAll`, `TestCleanupClient`, and reconnect tests
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a same-name `connect_all(...)` race test where duplicate configs for one server result in one underlying connect attempt while distinct server names still run concurrently.
  - test: Add a concurrent `connect_all(...)` or provision-style connection test where two callers for the same server observe the already-running or already-online result after the first succeeds.
  - test: Add a `disconnect_all()` stable-snapshot test where `_clients` changes while an awaited shutdown step is in progress and no `RuntimeError: dictionary changed size during iteration` occurs.
  - test: Add a `refresh(...)` serialization test proving two concurrent refresh/disconnect cycles do not interleave state clearing and reconnect state publication.
  - test: Add snapshot assertions for read methods that return lists or registry metadata so callers cannot mutate manager-owned collection containers.
  - impl: Adjust only tests in this lane; production synchronization belongs to SL-0.
  - verify: `uv run pytest tests/test_client_manager.py -k "connect_all or disconnect_all or refresh or snapshot or reconnect"`
  - verify: `uv run ruff check tests/test_client_manager.py`

### SL-2 — Lazy-Start Race Tests

- **Scope**: Cover concurrent lazy-start semantics from gateway-facing paths without changing startup policy behavior.
- **Owned files**: `tests/test_lazy_start.py`
- **Interfaces provided**: regression coverage for IF-0-SERIALIZE-1 and lazy-start portions of IF-0-SERIALIZE-5
- **Interfaces consumed**: private same-server single-flight behavior from SL-0, existing `ClientManager.register_lazy_configs(...)`, `ClientManager.ensure_connected(...)`, `GatewayTools.invoke(...)`, `GatewayTools.describe(...)`, existing lazy server mock patterns
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a direct `ensure_connected("same-server")` race test where multiple concurrent callers for one lazy server trigger exactly one `_connect_with_retry(config)` call.
  - test: Add a failure race test where concurrent lazy-start callers all return `False`, the lazy server status becomes `ERROR`, and duplicate retries are not started by each caller.
  - test: Add a post-success race assertion that `_lazy_configs` removes the server exactly once and later `ensure_connected(...)` returns `True` from the online status path.
  - test: Add or extend invoke/describe lazy-start tests so an unknown tool path that triggers lazy start still works with the single-flight helper.
  - impl: Adjust only tests in this lane; production synchronization belongs to SL-0.
  - verify: `uv run pytest tests/test_lazy_start.py -k "ensure_connected or lazy_start or invoke or describe"`
  - verify: `uv run ruff check tests/test_lazy_start.py`

### SL-3 — Tool/Provision Compatibility Tests

- **Scope**: Preserve gateway tool behavior while proving configured lazy-server provision/connect paths do not duplicate starts.
- **Owned files**: `tests/test_tools.py`
- **Interfaces provided**: compatibility coverage for IF-0-SERIALIZE-2 and IF-0-SERIALIZE-6
- **Interfaces consumed**: `GatewayTools.provision(...)`, `GatewayTools.refresh(...)`, `GatewayTools.health(...)`, mock client manager behavior in `tests/test_tools.py`, public refresh/provision output models
- **Parallel-safe**: yes
- **Tasks**:
  - test: Extend or add configured lazy-server provision tests so two provision calls for the same server either share the same connect result or the second observes the server as already running.
  - test: Confirm `gateway.refresh` output shape remains unchanged while underlying manager refresh serialization is exercised through the mock interface.
  - test: Confirm health/catalog/status tests that use `MockClientManager` do not need public field changes for this phase.
  - impl: Update `MockClientManager` only if needed to model new same-server behavior in tests; do not add public fields to gateway output.
  - verify: `uv run pytest tests/test_tools.py -k "provision or refresh or health or catalog"`
  - verify: `uv run ruff check tests/test_tools.py`

### SL-4 — Phase Integration Review

- **Scope**: Review the synchronization implementation against lock-ordering, stale-index, and public-compatibility risks after all producer lanes land.
- **Owned files**: `plans/phase-plan-v2-serialize.md`
- **Interfaces provided**: final implementation review notes and any recorded execution deviations from this plan
- **Interfaces consumed**: SL-0 synchronization code, SL-1 concurrency tests, SL-2 lazy-start tests, SL-3 gateway compatibility tests, IF-0-SERIALIZE-1 through IF-0-SERIALIZE-6
- **Parallel-safe**: no
- **Tasks**:
  - test: Review the final lane outputs against this plan's validation checklist before whole-phase verification.
  - impl: If execution discovers a better private helper name or lock primitive, record that deviation in this plan or the execution closeout without changing public contracts.
  - impl: Check that no lane introduced a second writer for `src/pmcp/client/manager.py` outside SL-0.
  - impl: Check that lock ordering is consistent: per-server connect coordination must not deadlock with the global lifecycle mutation lock during refresh, disconnect, reconnect, or lazy start.
  - impl: Check that no downstream tool invocation path holds the global lifecycle lock while waiting for a normal tool response.
  - verify: `uv run pytest tests/test_client_manager.py tests/test_lazy_start.py tests/test_tools.py`

### SL-5 — Documentation Impact and Closeout

- **Scope**: Consciously handle documentation and roadmap impact after implementation proves public payloads remain unchanged.
- **Owned files**: `README.md`, `CHANGELOG.md`, `specs/phase-plans-v2.md`
- **Interfaces provided**: documentation-impact decision and roadmap status update if the phase is executed to completion
- **Interfaces consumed**: SL-0 through SL-4 outcomes, IF-0-SERIALIZE-6, final verification results
- **Parallel-safe**: no
- **Tasks**:
  - test: Review README/shared-service wording only if implementation changes user-visible lifecycle behavior; otherwise record that no docs change is required because public tool payloads and commands are unchanged.
  - impl: If the phase is completed, mark Phase 1 exit criteria in `specs/phase-plans-v2.md` using the existing roadmap style.
  - impl: Add a CHANGELOG entry only if this work is being prepared for a release branch or user-visible concurrency behavior should be called out.
  - impl: Do not document private lock names unless they become part of a developer-facing maintenance note.
  - verify: `uv run pytest tests/test_client_manager.py tests/test_lazy_start.py tests/test_tools.py`
  - verify: `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py tests/test_lazy_start.py tests/test_tools.py`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_client_manager.py -k "connect_all or disconnect_all or refresh or snapshot or reconnect"`
- `uv run pytest tests/test_lazy_start.py -k "ensure_connected or lazy_start or invoke or describe"`
- `uv run pytest tests/test_tools.py -k "provision or refresh or health or catalog"`
- `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py tests/test_lazy_start.py tests/test_tools.py`

Whole-phase regression:

- `uv run pytest tests/test_client_manager.py tests/test_lazy_start.py tests/test_tools.py`
- `uv run pytest tests/test_server_lifecycle.py tests/test_http_transport.py tests/test_cli.py` if the implementation touches gateway startup or HTTP shared-service behavior indirectly.
- `uv run pytest` before release handoff if time permits.

## Acceptance Criteria

- [x] Concurrent `ensure_connected("same-server")` calls perform at most one downstream connect attempt and all callers receive the same success or failure result.
- [x] Concurrent provision/connect paths for the same server are single-flight or return an already-running result after the first succeeds.
- [x] `connect_all(...)` preserves cross-server parallelism while deduplicating same-server connection work.
- [x] `disconnect_all()` iterates stable snapshots and cannot fail because `_clients` changes during awaited shutdown work.
- [x] `refresh(...)` and global disconnect/reconnect state changes are serialized by a clear lifecycle lock boundary.
- [x] `_tools`, `_resources`, `_prompts`, `_servers`, `_clients`, and `_lazy_configs` are updated coherently during connect, reconnect, refresh, cleanup, and disconnect.
- [x] Read-only status/catalog methods return snapshot containers without exposing mutable internal collections.
- [x] Normal downstream tool invokes do not hold the global lifecycle mutation lock for the duration of the tool call.
- [x] Public tool payloads, gateway health/status shapes, refresh output, provisioning output, and startup policy fields remain backward compatible.
- [x] Tests cover same-server lazy-start races, same-server connect races, stable disconnect snapshots, refresh serialization, and read snapshot behavior.

## Execution Closeout

- Implemented per-server single-flight with private `_connect_tasks` rather than per-server locks.
- Added a manager lifecycle lock for `disconnect_all()` and `refresh()` boundaries; normal tool/resource/prompt calls remain lock-free while awaiting downstream responses.
- Documentation impact: no README or CHANGELOG change was needed for this phase because public gateway payloads and commands were unchanged.
