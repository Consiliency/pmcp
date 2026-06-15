---
phase_loop_plan_version: 1
phase: COMPLETE
roadmap: specs/phase-plans-v8.md
roadmap_sha256: 3e07c3a7e8ca6399747a99b81f7575b58dc48ed79af11ef428715a78784342b7
---

# COMPLETE: Complete the Half-Done Sweeps

## Context

Phase COMPLETE implements Phase 1 of `specs/phase-plans-v8.md`: finish the REDACT and CONCURR gaps found after v7 by routing the remaining task-emitting endpoints through the existing task sanitizer and by putting the reconnect connect path under `_lifecycle_lock` without holding the lock across retry backoff sleeps.

The roadmap hash was verified from `specs/phase-plans-v8.md` as `3e07c3a7e8ca6399747a99b81f7575b58dc48ed79af11ef428715a78784342b7`. Canonical `.phase-loop/` state marks COMPLETE as the current unplanned phase; legacy `.codex/phase-loop/` state is not authoritative for this run.

Current code already has the correct reusable seams: `GatewayTools._sanitize_task_for_output(...)` redacts `McpTaskInfo.status_message` and recursive `raw` values, `gateway.invoke` and `gateway.tasks_result` already use it on task output, and `ClientManager._lifecycle_lock` already protects normal connect, lazy connect, refresh, and disconnect paths. Execution should extend those seams rather than add another sanitizer or redesign reconnect policy.

## Interface Freeze Gates

- [ ] IF-0-COMPLETE-1 - Every task-emitting handler (`invoke`, `tasks_result`, `tasks_list`, `tasks_get`) routes its returned task through `GatewayTools._sanitize_task_for_output(...)`; `_lifecycle_lock` covers the `_reconnect_loop` call to `_connect_singleflight(...)`; reconnect backoff sleeps occur before lock acquisition so one unreachable server cannot hold `_lifecycle_lock` across `5.0`, `15.0`, or `30.0` second retry delays.

## Lane Index & Dependencies

- SL-0 - Task endpoint redaction completion; Depends on: (none); Blocks: SL-2; Parallel-safe: yes
- SL-1 - Reconnect lifecycle lock completion; Depends on: (none); Blocks: SL-2; Parallel-safe: yes
- SL-2 - COMPLETE verification and reducer closeout; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Task Endpoint Redaction Completion

- **Scope**: Route `gateway.tasks_list` and `gateway.tasks_get` task outputs through the existing task sanitizer and prove every task-emitting gateway endpoint redacts task metadata.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: IF-0-COMPLETE-1 redactor call-site coverage for `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and `gateway.invoke`; structural task-emitting endpoint redaction regression
- **Interfaces consumed**: pre-existing `GatewayTools._sanitize_task_for_output(...)`, `GatewayTools._sanitize_task_raw(...)`, `GatewayTools.invoke(...)`, `GatewayTools.tasks_result(...)`, `McpTaskInfo`, `McpTaskRecord`, `PolicyManager.redact_secrets(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a failing-first `tests/test_tools.py` regression where task records returned by `gateway.tasks_list` and `gateway.tasks_get` contain secrets in `status_message` and nested `raw` values; assert serialized outputs contain no raw `sk-...`, `ghp_...`, or `github_pat_...` tokens.
  - test: Add a structural completeness regression over `invoke`, `tasks_list`, `tasks_get`, and `tasks_result` proving every task-emitting success output returns sanitized task metadata.
  - test: Extend the metadata preservation regression so non-secret SDK fields such as timestamps, `ttl`, `poll_interval`, and unknown raw metadata remain available after sanitization.
  - impl: In `GatewayTools.tasks_list(...)`, sanitize each `McpTaskInfo` presentation object before appending it to `TasksListOutput.tasks`; preserve filtering, cursor behavior, and internal task records.
  - impl: In `GatewayTools.tasks_get(...)`, sanitize the returned task before placing it in `TasksGetOutput.task`; preserve the existing policy-denied and error response shapes.
  - verify: `uv run pytest tests/test_tools.py -k "tasks_list or tasks_get or redact or task_metadata or task_surfaces"`
  - verify: `git diff --check -- src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-1 - Reconnect Lifecycle Lock Completion

- **Scope**: Protect reconnect-time `_connect_singleflight(...)` with `_lifecycle_lock` while keeping retry sleeps and already-online checks outside the lock.
- **Owned files**: `src/pmcp/client/manager.py`, `tests/test_client_manager.py`
- **Interfaces provided**: IF-0-COMPLETE-1 reconnect lock-scope contract; reconnect connect path serialized with refresh/disconnect/lazy connect; backoff sleeps outside `_lifecycle_lock`
- **Interfaces consumed**: pre-existing `ClientManager._lifecycle_lock`, `ClientManager._connect_singleflight(...)`, `ClientManager.refresh(...)`, `ClientManager.disconnect_all(...)`, `ClientManager.ensure_connected(...)`, `ClientManager._reconnect_loop(...)`, `ManagedClient.reconnecting`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a failing-first `tests/test_client_manager.py` regression where `_reconnect_loop(...)` and `refresh(...)` race; assert reconnect cannot create an orphaned subprocess or stale `_clients` entry outside the lifecycle lock.
  - test: Add a lock-scope regression proving `_reconnect_loop(...)` does not hold `_lifecycle_lock` while awaiting retry backoff sleeps, so another server's lazy start, refresh, or disconnect can enter the lifecycle section during the delay.
  - test: Keep existing reconnect storm guard assertions for clearing `ManagedClient.reconnecting` on success, failure, and already-online exits.
  - impl: Wrap only the `_connect_singleflight(config)` call in `_reconnect_loop(...)` with `async with self._lifecycle_lock`; leave `asyncio.sleep(delay)`, status inspection, logging, and final reconnecting cleanup outside the lock.
  - impl: Re-check the target managed client's online status inside or immediately before the locked reconnect attempt if needed to avoid reconnecting after a concurrent refresh already restored the server.
  - verify: `uv run pytest tests/test_client_manager.py -k "reconnect or refresh or lifecycle or lazy"`
  - verify: `git diff --check -- src/pmcp/client/manager.py tests/test_client_manager.py`

### SL-2 - COMPLETE Verification and Reducer Closeout

- **Scope**: Verify both COMPLETE fixes together, confirm IF-0-COMPLETE-1 is fully produced, and record whether execution touched only phase-owned files.
- **Owned files**: none
- **Interfaces provided**: COMPLETE verification evidence; IF-0-COMPLETE-1 completion checklist; phase-owned dirty-path inventory for runner closeout
- **Interfaces consumed**: IF-0-COMPLETE-1; SL-0 task endpoint redaction tests and implementation; SL-1 reconnect lifecycle tests and implementation; roadmap COMPLETE exit criteria
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm no lane-owned file overlaps another lane-owned file and no executor touched files outside `src/pmcp/tools/handlers.py`, `tests/test_tools.py`, `src/pmcp/client/manager.py`, and `tests/test_client_manager.py` for implementation.
  - test: Confirm `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and `gateway.invoke` each have redaction coverage for task metadata, and reconnect has coverage for both serialization and no-lock-during-backoff behavior.
  - verify: `uv run pytest tests/test_tools.py tests/test_client_manager.py -k "tasks_list or tasks_get or redact or reconnect or refresh or lifecycle"`
  - verify: `TMPDIR=/var/tmp uv run ruff check src/ tests/`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `TMPDIR=/var/tmp uv run pytest -q`
  - verify: `git diff --check`
  - verify: `git status --short`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-2: work-unit=`phase_reducer`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
uv run pytest tests/test_tools.py tests/test_client_manager.py -k "tasks_list or tasks_get or redact or reconnect or refresh or lifecycle"
TMPDIR=/var/tmp uv run ruff check src/ tests/
uv run mypy src/pmcp --exclude baml_client
TMPDIR=/var/tmp uv run pytest -q
git diff --check
git status --short
```

## Acceptance Criteria

- [ ] `gateway.tasks_list` and `gateway.tasks_get` route returned tasks through `GatewayTools._sanitize_task_for_output(...)`; secrets in `status_message` and nested `raw` values are absent from serialized outputs.
- [ ] A structural regression proves every task-emitting handler (`invoke`, `tasks_result`, `tasks_list`, `tasks_get`) sanitizes returned task metadata.
- [ ] Non-secret task metadata and SDK fields remain preserved on task surfaces after presentation-time sanitization.
- [ ] `_reconnect_loop(...)` acquires `_lifecycle_lock` for its `_connect_singleflight(...)` reconnect attempt and remains serialized with refresh, disconnect, and lazy connect lifecycle sections.
- [ ] `_reconnect_loop(...)` does not hold `_lifecycle_lock` while sleeping between reconnect attempts; unrelated lifecycle operations can proceed during retry backoff.
- [ ] `ruff`, mypy, and full `pytest` pass with `TMPDIR=/var/tmp` for commands that need a temporary directory outside `/tmp`.
