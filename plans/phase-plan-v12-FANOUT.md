---
phase_loop_plan_version: 1
phase: FANOUT
roadmap: specs/phase-plans-v12.md
roadmap_sha256: 806fb51f50a493d1422c54e8d9ded14385525fcd2d3267fa4d39ed4f624ea2b3
---

# FANOUT: Downstream catalog reconciliation and fan-out

## Context

**The gateway's catalog is index-backed, not proxied.** `gateway.invoke` resolves through `ClientManager.get_tool` → `self._tools` (`manager.py:2698`), populated only by `_index_tools` (`:1167`), which runs from `_index_capabilities` (`:1289`) at connect and refresh. A client told "the catalog changed" refetches *the gateway's* catalog, not the downstream server's.

**The sink already fires from index mutation — verified, and it decides the design.** Both `_index_tools`/`_index_resources`/`_index_prompts` (`:1215`, `:1245`, `:1286`) and `_remove_server_indexes` (`:997`, `:999`, `:1001`) call `self._catalog_events.note_*`. So the correct edge for this phase is **downstream `list_changed` → schedule a re-index of that server**, after which the existing mutators publish for free. `src/pmcp/subscriptions.py` needs no change; the roadmap's Lane B ("subscriptions.py accepting downstream-originated events") is a no-op and is not a lane here.

**Downstream notifications reach the dispatch and are dropped.** A JSON-RPC notification carries no `id`, so it fails the `msg_id is not None and msg_id in managed.pending_requests` gate and falls through with **no `else` branch**. This happens in **two** places, not one:

- `_handle_stdout_line` (`:1760`) — stdio servers.
- the SSE loop inside `_read_sse` (`:1965`, gate at `:1986`) — remote servers.

Each also carries its own `TODO(post-P3B)` where a JSON-RPC `error` object's `code` and `data` are discarded (`:1782`, `:2000`).

**Three hazards this phase must design against, each verified:**

1. **Self-deadlock.** `_index_capabilities` awaits `_send_request` (`:1292`), and those futures are resolved by the very read loop that received the notification — `pending.future.set_result` at `:1791` and `:2010`. Reconciling inline makes the loop await a future only it can resolve. This deadlocks immediately, not occasionally.
2. **Notification storm on an unchanged catalog.** The `note_*` calls above are **unconditional**: `_remove_server_indexes` publishes if it removed anything, and `_index_*` publishes if it indexed anything. A naive remove-then-reindex therefore emits events on *every* downstream `list_changed` even when nothing moved — exactly the spam EC-FANOUT-4 forbids. Reconciliation must suppress the sink while it churns and publish once, only on a real difference.
3. **The harness only covers one transport.** `tests/runtime/fake_remote.py` serves a Starlette/`FastMCP` app over HTTP, so tests built on it exercise `_read_sse` alone. The stdio path needs its own emitter or it ships untested.

**Filtering is type-only.** `SubscriptionFilter` in the installed SDK carries `tools_list_changed`, `prompts_list_changed`, `resources_list_changed`, `resource_subscriptions` — **no server dimension**. Per-origin routing is a roadmap Non-Goal and is not expressible; EC-FANOUT-3 in this plan is the two-transport criterion, not a per-server filter.

## Interface Freeze Gates

- [ ] IF-0-FANOUT-1 — The downstream-event contract in `src/pmcp/client/manager.py`: the mapping from downstream method name (`notifications/tools/list_changed`, `notifications/resources/list_changed`, `notifications/prompts/list_changed`) to a per-server reconcile request; the **reconcile-then-publish** ordering; the **suppress-while-churning, publish-once-if-changed** rule; and the guarantee that an unrecognised `notifications/*` method is a no-op that neither raises nor terminates the read loop. Published by SL-1.1 on day 1 as the scheduler entry point plus its docstring contract, with the body unimplemented.
- [ ] IF-0-FANOUT-2 — The test-harness emitter API: how a test tells a fake downstream server to emit a given `notifications/*` frame, for **both** transports. Published by SL-2.1 on day 1 so SL-3 writes tests without waiting on SL-2's body.

## Lane Index & Dependencies

SL-1 — Dispatch recognition and coalesced reconciliation
  Depends on: (none)
  Blocks: SL-3, SL-4
  Parallel-safe: yes

SL-2 — Two-transport emitter harness
  Depends on: (none)
  Blocks: SL-3, SL-4
  Parallel-safe: yes

SL-3 — Behavioural test suite
  Depends on: SL-1, SL-2
  Blocks: SL-4
  Parallel-safe: yes

SL-4 — Documentation & spec reconciliation
  Depends on: SL-1, SL-2, SL-3
  Blocks: (none)
  Parallel-safe: no

## Lanes

### SL-1 — Dispatch recognition and coalesced reconciliation

- **Scope**: Recognise downstream `notifications/*` in both dispatch paths, schedule a coalesced per-server re-index that publishes only on a real change, and preserve JSON-RPC `code`/`data` in both error branches.
- **Owned files**: `src/pmcp/client/manager.py`
- **Interfaces provided**: the reconcile scheduler entry point (IF-0-FANOUT-1), typed downstream errors at the `ClientManager` boundary
- **Interfaces consumed**: (none)
- **Parallel-safe**: yes — sole writer of `manager.py`. The roadmap's Lane A/C branch partition is **not** used: those branches live in two different functions and are adjacent hunks, so a partition is a merge-conflict trap. One lane owns the file and sequences the work internally.

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-1.1 | test | — | `src/pmcp/client/manager.py` | IF-0-FANOUT-1 shape | `uv run pytest tests/test_client_manager.py -q -k reconcile_contract` |
| SL-1.2 | impl | SL-1.1 | `src/pmcp/client/manager.py` | — | `uv run pytest tests/test_client_manager.py -q -k reconcile_contract` |
| SL-1.3 | test | SL-1.2 | `src/pmcp/client/manager.py` | scheduler is spawned and coalesced; publish-once | `uv run pytest tests/test_client_manager.py -q -k reconcile` |
| SL-1.4 | impl | SL-1.3 | `src/pmcp/client/manager.py` | — | `uv run pytest tests/test_client_manager.py -q` |
| SL-1.5 | impl | SL-1.4 | `src/pmcp/client/manager.py` | — | `uv run pytest tests/test_client_manager.py -q` |
| SL-1.6 | impl | SL-1.5 | `src/pmcp/client/manager.py` | — | `uv run pytest -q` |
| SL-1.7 | verify | SL-1.6 | `src/pmcp/client/manager.py` | all | `uv run pytest -q && uv run ruff check . && uv run mypy src` |

- **SL-1.1** pins IF-0-FANOUT-1's shape before it exists: the scheduler entry point is importable with the frozen signature, and an unrecognised `notifications/*` method is a no-op. Its RED is symbol absence, which is the correct failure for an unpublished freeze — unlike the behavioural tests in SL-3, where an import error would mean the test is not exercising its defect.
- **SL-1.2** publishes the freeze: signature plus docstring contract, body unimplemented. Commit it alone; it is what unblocks SL-3.
- **SL-1.4** implements the scheduler. It **must** `asyncio.create_task` (or equivalent) — never `await` — from the dispatch path. Cite the reason in the code: `_index_capabilities` awaits `_send_request` (`:1292`) whose futures resolve at `:1791`/`:2010`, inside the very loop that would be awaiting. Coalesce to **one in-flight reconcile per server**; a further notification while one is running sets a re-run flag rather than spawning a second task. Reconciliation is `_remove_server_indexes(name)` then `_index_capabilities(managed)`.
- **SL-1.5** adds suppress-then-publish-once. The `note_*` calls inside `_remove_server_indexes` and `_index_*` are unconditional, so reconciliation must silence the sink for its duration, compare the server's catalog before and after, and notify only for the kinds that actually differ. Comparing *counts* is not sufficient — a rename leaves the count unchanged; compare the identifier sets.
- **SL-1.6** wires recognition into **both** dispatch paths and preserves `code`/`data` in **both** error branches, replacing both `TODO(post-P3B)` markers. An unrecognised `notifications/*` must be a silent no-op. Scope the typed errors to the `ClientManager` boundary — `gateway.invoke` maps every exception to `E302` via `str(e)`, and changing that is a `gateway.*` contract change this roadmap forbids; SL-4 files the follow-up.

### SL-2 — Two-transport emitter harness

- **Scope**: Give tests a way to make a fake downstream server emit an arbitrary `notifications/*` frame, over **both** stdio and HTTP.
- **Owned files**: `tests/runtime/fake_remote.py`, `tests/runtime/fake_stdio_server.py`, `tests/runtime/test_emitter_harness.py`
- **Interfaces provided**: the emitter API (IF-0-FANOUT-2)
- **Interfaces consumed**: (none)
- **Parallel-safe**: yes

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-2.1 | test | — | `tests/runtime/test_emitter_harness.py` | IF-0-FANOUT-2 shape; emitter reaches the dispatch on both transports | `uv run pytest tests/runtime -q -k emitter` |
| SL-2.2 | impl | SL-2.1 | `tests/runtime/fake_remote.py`, `tests/runtime/fake_stdio_server.py` | — | `uv run pytest tests/runtime -q -k emitter` |
| SL-2.3 | impl | SL-2.2 | `tests/runtime/fake_remote.py`, `tests/runtime/fake_stdio_server.py` | — | `uv run pytest tests/runtime -q` |
| SL-2.4 | verify | SL-2.3 | `tests/runtime/**` | all | `uv run pytest tests/runtime -q` |

- **SL-2.1** pins IF-0-FANOUT-2's shape in a harness test SL-2 owns, so SL-3's files stay untouched by this lane. **SL-2.2** publishes the emitter signatures.
- `tests/runtime/fake_stdio_server.py` is **new** — the existing harness is HTTP-only, so the stdio dispatch path has no emitter today. A minimal script that speaks JSON-RPC on stdout and emits a notification on cue is enough; it does not need to be a real MCP server beyond `initialize` and `tools/list`.
- **SL-2.3** must leave `run_fake_remote`'s entry-side `AppStatus.should_exit = False` reset intact (added in 2.2.1, `fake_remote.py:~96`). Reintroducing a teardown-only reset re-opens the #158 cross-test poisoning.

### SL-3 — Behavioural test suite

- **Scope**: Prove the catalog is actually reconciled — not merely that a notification arrived — on both transports.
- **Owned files**: `tests/runtime/test_downstream_remote.py`, `tests/test_client_manager.py`, `tests/runtime/test_downstream_stdio.py`
- **Interfaces provided**: (none)
- **Interfaces consumed**: the reconcile scheduler (IF-0-FANOUT-1), the emitter API (IF-0-FANOUT-2)
- **Parallel-safe**: yes

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Tests owned | Test command |
|---|---|---|---|---|---|
| SL-3.1 | test | SL-1.1, SL-2.1 | `tests/runtime/test_downstream_stdio.py`, `tests/runtime/test_downstream_remote.py` | catalog freshness, both transports | `uv run pytest tests/runtime -q -k downstream_notification` |
| SL-3.2 | test | SL-3.1 | `tests/test_client_manager.py` | storm suppression, unknown-method no-op, typed errors | `uv run pytest tests/test_client_manager.py -q` |
| SL-3.3 | test | SL-3.2 | `tests/runtime/test_downstream_remote.py` | no self-deadlock | `uv run pytest tests/runtime -q -k deadlock` |
| SL-3.4 | verify | SL-3.3 | `tests/**` | all | `uv run pytest -q` |

- **SL-3.1 is the criterion that catches a fake fix.** Assert the **catalog**, not the notification: after a real downstream emission, a tool the server added is returned by `gateway.catalog_search` and is invocable via `gateway.invoke`, and a tool it removed is gone from both. A test that only asserts a notification arrived passes against a forward-to-sink implementation that never re-indexes.
- **SL-3.3** must issue its unrelated request **to the same downstream** whose reader handled the notification. A request to a *different* server does not exercise the deadlock — the loops are per-connection.
- Every test in SL-3.1–SL-3.3 must be demonstrated RED against `main` before its implementation lands, and the RED reason must be the defect it names, not an import error.

### SL-docs — Documentation & spec reconciliation

- **Scope**: Refresh the docs catalog, document downstream fan-out as new client-visible behaviour, file the `gateway.invoke` typed-error follow-up, and amend the roadmap where this phase's lane hints proved wrong.
- **Owned files**: `CHANGELOG.md`, `README.md`, `.claude/docs-catalog.json`, `specs/phase-plans-v12.md`, `docs/**`
- **Interfaces provided**: (none)
- **Interfaces consumed**: (none)
- **Parallel-safe**: no (terminal)
- **Depends on**: SL-1, SL-2, SL-3

**Tasks**:

| Task ID | Type | Depends on | Files in scope | Action |
|---|---|---|---|---|
| SL-docs.1 | docs | SL-1.7, SL-2.4, SL-3.4 | `.claude/docs-catalog.json` | Rescan via `_shared/scaffold_docs_catalog.py --rescan`. If the helper is absent, record "docs-catalog rescan helper unavailable; manual catalog audit" in the commit message and proceed. |
| SL-docs.2 | docs | SL-docs.1 | `CHANGELOG.md`, `README.md`, per catalog | Document downstream fan-out as new client-visible behaviour and typed downstream errors as an internal fix. State the guarantee precisely: the catalog is reconciled **before** the notification, so a client that refetches sees the change. |
| SL-docs.3 | docs | SL-docs.2 | — | File a follow-up issue: `gateway.invoke` collapses every downstream error to `E302` via `str(e)`, so typed JSON-RPC errors stop at the `ClientManager` boundary and never reach an MCP client. Surfacing them is a `gateway.*` contract change and out of scope here. |
| SL-docs.4 | docs | SL-docs.3 | `specs/phase-plans-v12.md` | Append `### Post-execution amendments` under Phase 3: the roadmap's Lane B (`subscriptions.py`) was a no-op because `_index_*` and `_remove_server_indexes` already publish to the sink; and the Lane A/C branch partition was unavailable because the two error branches live in two different functions. |
| SL-docs.5 | verify | SL-docs.4 | — | Repo doc linters if configured; otherwise no-op. `uv run ruff format --check src/ tests/`. |

## Execution Notes

- **Single-writer files**: `src/pmcp/client/manager.py` → **SL-1 only**. `tests/runtime/fake_remote.py` and `tests/runtime/fake_stdio_server.py` → SL-2 only. `tests/runtime/test_downstream_remote.py`, `tests/runtime/test_downstream_stdio.py`, `tests/test_client_manager.py` → SL-3 only. `CHANGELOG.md` and `specs/phase-plans-v12.md` → SL-docs only. **SL-3 owns `test_downstream_remote.py`; SL-2 must not add tests to it** — SL-2's own emitter check lives in `tests/runtime/test_emitter_harness.py`, which SL-2 owns.
- **The roadmap's lane decomposition is superseded, deliberately.** It proposed Lane A (dispatch) / Lane B (subscriptions.py) / Lane C (error branches) / Lane D (tests). Lane B is a no-op — verified, the sink already fires from index mutation. Lanes A and C cannot be partitioned by branch because the branches live in two different functions and are adjacent hunks. This plan uses one `manager.py` lane, a harness lane, and a test lane. SL-docs.4 amends the roadmap.
- **Known destructive changes**: both `TODO(post-P3B)` comment blocks removed from `manager.py` (`:1782`, `:2000`) as their content is implemented. Nothing else is deleted.
- **Expected add/add conflicts**: none — `tests/runtime/fake_stdio_server.py` is created once, by SL-2.
- **SL-0 re-exports**: not applicable; no package-level re-export is added.
- **Do not reintroduce a teardown-only `AppStatus` reset.** `fake_remote.py` clears `sse_starlette.sse.AppStatus.should_exit` on **entry** as of 2.2.1. That fix closed #158, where a server stopped by an earlier test file poisoned every SSE stream that followed. SL-2 owns this file; a regression here reappears as an unrelated flaky test in another module.
- **Stale-base guidance** (verbatim): Lane teammates working in isolated worktrees do not see sibling-lane merges automatically. If a lane finds its worktree base is pre-SL-1.1 or pre-SL-2.1, it MUST stop and report rather than committing — the orchestrator will re-spawn or rebase. Silent `git reset --hard` or `git checkout HEAD~N -- …` in a stale worktree produces commits that destroy peer-lane work on `--no-ff` merge.
- **This is the roadmap's riskiest phase and the only one changing client-visible behaviour.** Run it alone rather than alongside UPDPATH.

## Execution Policy

- work-unit defaults: effort=medium, reason=concurrency and cross-transport behaviour rather than mechanical edits
- SL-1: effort=high, reason=a spawned coalesced reconcile against a read loop that resolves its own futures is subtly deadlock-prone
- SL-2: effort=low
- SL-3: effort=medium, reason=each test must be RED for the defect it names and not merely for an import error
- SL-4: effort=minimal, reason=docs sweep only

## Spec Closeout Plan

- schema: `spec_delta_closeout.v1`
- decision: `roadmap_amendment`
- target surfaces: `specs/phase-plans-v12.md`
- evidence paths: `plans/phase-plan-v12-FANOUT.md`
- redaction posture: `metadata_only`
- downstream handling: `roadmap amendment`

## Acceptance Criteria

- [ ] EC-FANOUT-1, EC-FANOUT-9 — proven by `uv run pytest tests/runtime -q -k downstream_notification`, asserting the catalog through `gateway.catalog_search` and `gateway.invoke`, RED against `main`
- [ ] EC-FANOUT-2 — proven by `uv run pytest tests/runtime -q -k removed_tool_disappears`
- [ ] EC-FANOUT-3 — proven by `uv run pytest tests/runtime/test_downstream_stdio.py tests/runtime/test_downstream_remote.py -q -k downstream_notification`, which must cover both transports independently
- [ ] EC-FANOUT-4 — proven by `uv run pytest tests/test_client_manager.py -q -k unchanged_catalog_publishes_nothing`
- [ ] EC-FANOUT-5 — proven by `uv run pytest tests/test_client_manager.py -q -k 'resources_list_changed or prompts_list_changed or unknown_notification'`
- [ ] EC-FANOUT-6 — proven by `uv run pytest tests/runtime -q -k deadlock`, issuing the unrelated request to the same downstream
- [ ] EC-FANOUT-7 — proven by `uv run pytest tests/test_client_manager.py -q -k typed_downstream_error`, covering both dispatch paths
- [ ] EC-FANOUT-8 — proven by `uv run pytest -q && uv run ruff check . && uv run ruff format --check src/ tests/ && uv run mypy src`, plus a CHANGELOG entry documenting fan-out as client-visible

## Verification

```bash
# Full gates after all lanes merge.
uv run pytest -q
uv run ruff check . && uv run ruff format --check src/ tests/ && uv run mypy src

# The criterion that catches a fake fix: assert the CATALOG, not the notification.
uv run pytest tests/runtime -q -k 'downstream_notification or removed_tool_disappears'

# Both transports, independently. Neither may be skipped.
uv run pytest tests/runtime/test_downstream_stdio.py -q
uv run pytest tests/runtime/test_downstream_remote.py -q

# Storm suppression and the unknown-method no-op.
uv run pytest tests/test_client_manager.py -q -k 'unchanged_catalog or unknown_notification'

# Self-deadlock: an unrelated request on the SAME downstream still completes.
uv run pytest tests/runtime -q -k deadlock

# #158 guard -- the entry-side AppStatus reset must survive this phase.
uv run python -c "
import inspect, tests.runtime.fake_remote as fr
src = inspect.getsource(fr.run_fake_remote.__wrapped__ if hasattr(fr.run_fake_remote,'__wrapped__') else fr.run_fake_remote)
before = src.index('server.serve()')
assert 'AppStatus.should_exit = False' in src[:before], 'entry-side latch reset lost; #158 will regress'
print('  entry-side AppStatus reset intact')"
```

Edge cases to exercise: a downstream emitting `list_changed` during `connect_all`; a notification arriving after the subscription closed; a downstream that emits on every request (storm suppression); a rename that leaves the tool **count** unchanged but the identifier set different (count-based diffing passes this wrongly); a downstream that emits `list_changed` and then fails `tools/list` during reconciliation (the catalog must not be left half-removed); and an unrecognised `notifications/*` method.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```
