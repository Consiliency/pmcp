# PMCP Phase Plans v12 — Close v11's deferrals; make the update contract structural

## Context

v11 shipped the mcp 2.x migration and the client-facing subscription surface. It closed deliberately, with two things named as future work in its own Non-Goals and TODOs:

- **Downstream notification fan-out.** `subscriptions/listen` publishes the *gateway's own* catalog changes — `ClientManager` mutates its tool/resource/prompt indexes and `BusCatalogEventSink` turns that into `notifications/*/list_changed`. What it does **not** do is proxy a *downstream server's* own `notifications/*` upward. A downstream server that adds a tool at runtime is invisible to a subscribed client. v11 P3B's Non-goals say so explicitly: *"Proxying downstream servers' `notifications/*` up to clients (future roadmap)."*
- **Typed downstream errors.** Two `TODO(post-P3B)` markers in `src/pmcp/client/manager.py` record that a JSON-RPC `error` object's `code` and `data` are dropped, exposing only `message`. Deferred twice now, in P3B and again after.

Separately, releases 2.2.0 and 2.2.1 exposed a structural weakness that cost five review rounds and seven confirmed defects on one PR. `is_version_newer` **fails closed**: `False` means *either* "up to date" *or* "cannot be ordered". Every consumer writing `not is_version_newer(...)` collapses those, and that same defect has now been fixed three separate times (#155, #156, #163). The lint written to police it was bypassed by reviewers four times, because a syntactic check cannot prove a dataflow property. The recorded conclusion is #164: a boolean cannot express three outcomes.

**Thesis:** finish what v11 named as deferred, and replace the fail-closed boolean with a representation that makes its failure mode unrepresentable rather than merely detectable.

## Architecture North Star

```
  client (subscribed)
      ▲
      │ notifications/tools/list_changed
      │
  ┌───┴──────────────────────────────────────────┐
  │ SubscriptionBus            (subscriptions.py)│
  └───▲──────────────────────────────────────────┘
      │ CatalogEventSink
      │
  ┌───┴──────────────────────────────────────────┐
  │ ClientManager                    (manager.py)│
  │                                              │
  │  gateway-owned mutations ────► sink   [v11]  │
  │  connect / disconnect / refresh              │
  │                                              │
  │  downstream notifications/* ──► sink [v12]   │
  │  read loop, currently DROPS them             │
  └───▲──────────────────────────────────────────┘
      │ JSON-RPC over stdio / streamable HTTP
  downstream MCP servers
```

The v12 addition is one edge: the downstream read loop already parses every inbound message and routes responses to pending futures. Inbound `notifications/*` fall through to nothing. They need to reach the same sink the gateway's own mutations already use, so the client-facing surface is unchanged.

## Assumptions (fail-loud if wrong)

1. `SubscriptionBus` and `CatalogEventSink` can carry a downstream-originated event without schema change — the client-visible payload is the same `notifications/*/list_changed` regardless of who caused it. **This says nothing about whether the catalog behind that payload is correct; see FANOUT's reconciliation requirement, which is the phase's real content.**
2. ~~The downstream read loop sees notification frames at all.~~ **Verified during review, and it is no longer an assumption.** `_handle_stdout_line` (`manager.py:1760`) parses every frame; a notification carries no `id`, so `msg_id is not None` is false and it falls through with no `else` — seen, then dropped. The SSE loop (`manager.py:~1986`) has the *same* shape. Both are confirmed insertion points, and **there are two of them, not one** — a fan-out wired into only the stdio path leaves every remote server silent. The remote path is reachable too: mcp 2.x opens the standalone GET stream for server-initiated messages on `notifications/initialized` (SDK `streamable_http.py:566`), which pmcp sends in `_send_initialize` (`manager.py:2185`). **Cited, not assumed — no phase-zero probe needed.** Note `tests/runtime/fake_remote.py` drives only the remote loop, so the stdio path needs its own test.
3. `refresher.py` and `handlers.py` remain the only consumers of the version-comparison API. A new consumer appearing mid-roadmap changes the migration surface.
4. No downstream server in the manifest depends on the gateway silently dropping its notifications.

## Non-Goals

- **Changing any `gateway.*` tool schema.** 2.2.0 removed four output fields; v12 adds and removes none. Client-visible tool contracts are unchanged.
- **Subscription filtering by originating server.** A client subscribed to `tools/list_changed` gets one event when the catalog changes, not one per downstream server. Per-origin routing is a later question.
- **Re-litigating the removal of automatic update notices.** #150 closed after eight failed attempts; the analysis is committed under `.consiliency/plans/`. v12 does not reinstate them.
- **#77 (Telnyx + Algolia manifest entries).** Blocked on external vendors publishing official servers; nothing in this repo unblocks it.
- **Rewriting `is_version_orderable`'s callers outside `refresher.py`.** There are none today; if one appears, it joins UPDPATH rather than expanding TRISTATE.

## Cross-Cutting Principles

- **A test must fail against the bug it names.** Every acceptance criterion below is provable by a test demonstrated RED before the fix. This is not ceremony: on #163, three separate tests passed while the defect they were written for survived, and two of those were mine.
- **Prefer making a failure unrepresentable over detecting it.** The lint in #163 took four attempts and was still unsound. Where a type can carry the distinction, use the type.
- **Verify a "missing" item against the code, not against roadmap prose.** v11 P6CLEAN records planning work for a feature that already existed. Inherited here deliberately.
- **Fail closed on ambiguity, but never by guessing.** #163 rejected both a fail-closed rule that broke CalVer and a "promotion" heuristic that fabricated updates. Incomparable is a legitimate answer; inventing an ordering is not.

## Phases

### Phase 1 — Tri-state version comparison (TRISTATE)

**Objective**
Replace the fail-closed boolean `is_version_newer` with a tri-state result so "incomparable" cannot be collapsed into "up to date" by negation, and delete the syntactic lint that exists only to police that collapse.

**Exit criteria**
- [ ] EC-TRISTATE-1 — `compare_versions(current, latest, package_type)` returns one of exactly three values (`newer`, `not_newer`, `incomparable`) and is the **only** classification path.
- [ ] EC-TRISTATE-2 — Both `refresher.py` call sites consume the tri-state directly, and the "already up to date" short-circuit fires only on an explicit `not_newer`. Proven by a test that is RED when the short-circuit treats `incomparable` as up-to-date.
- [ ] EC-TRISTATE-3 — **`is_version_newer` and `are_versions_comparable` are deleted outright**, along with `test_no_unguarded_negation_of_is_version_newer`. Not kept as wrappers and policed — *deleted*. A function that does not exist cannot be negated, which is the difference between unrepresentable and merely detectable. They may exist as intra-phase migration shims; the criterion is their removal by phase end.
- [ ] EC-TRISTATE-4 — `compare_versions` is pinned against the existing 28-value × 7-type corpus in its own right: every pair classified `incomparable` is ordered in neither direction, and the test fails when drift is injected. Additionally, an **ordinal** `newer` (SemVer / PEP 440 lanes) must reverse to `not_newer`, while a **digest** difference is `newer` in *both* directions and is exempt — a digest is an identity, not an ordinal. (This is no longer a *wrapper-agreement* test — with EC-TRISTATE-3 there are no wrappers to agree with.)

  *Amended 2026-08-23 during execution review.* The original wording required universal reversal, which is false for the digest lane, and the corpus asserted only the safety direction — a mutant making the SemVer lane return `newer` in both directions passed all seven corpus tests. Antisymmetry is now asserted separately and scoped to the ordinal lanes.
- [ ] EC-TRISTATE-5 — Full suite, ruff, mypy green; CHANGELOG records the new API and the lint's removal.

**Scope notes**
- Decompose into 3 lanes. Lane A owns `src/pmcp/manifest/version_checker.py` (the tri-state and the two wrappers). Lane B owns `src/pmcp/manifest/refresher.py` (both call sites). Lane C owns `tests/` (corpus, drift, lint deletion).
- **Publish the `Literal` return type as an intra-phase freeze on day 1** so Lane B and Lane C start against the contract instead of waiting for Lane A's body.
- Lanes A and B share no file. Lane B is the only writer of `refresher.py` in this phase, which is why UPDPATH must follow rather than run beside it.
- **The wrappers are deleted, and this reverses the first draft.** That draft kept `is_version_newer` and policed it with a test asserting no `src/` call site negates it. The panel overturned it on the roadmap's own principle, and the argument is decisive: **that replacement test is itself syntactic.** It misses `f = is_version_newer; not f(...)`, `x == False`, and `if is_version_newer(...): ... else: <treat as up to date>` — the last of which reproduces the collapse with no negation at all. That is exactly the #163 lesson ("a syntactic check cannot prove a dataflow property") applied to the lint's own replacement; a smaller lint inherits the same unsoundness with fewer lines. Retention also cost a drift corpus maintained for a function nothing calls.
- **The compatibility argument does not hold.** pmcp's public surface is its MCP tool schemas, not this internal module. Grep-verified: the only `src/` consumers today are `refresher.py:235` and `:435`, both migrated by EC-TRISTATE-2, leaving zero callers. `is_version_orderable` stays — it answers a genuinely unary question and is correctly scoped as a non-goal.
- The known limitations documented in the existing lint (early-exit guards, reassigned values, closure scope) are the reason it is being deleted rather than extended. Do not port them forward.

**Non-goals**
- Changing what any comparison *decides* — `1.0.0-rc1 < 1.0.0`, digest canonicalisation, and CalVer ordering all keep their current answers. This is a representation change, not a semantics change.
- Removing `is_version_orderable`. It answers a genuinely unary question; #163 rewrote its docstring to say it must not guard a negation.

**Key files**
- src/pmcp/manifest/version_checker.py
- src/pmcp/manifest/refresher.py
- tests/test_version_checker.py
- tests/test_refresher.py
- CHANGELOG.md

**Depends on**
- (none)

**Produces**
- IF-0-TRISTATE-1 — `compare_versions(current: str, latest: str, package_type: str | None) -> Literal["newer", "not_newer", "incomparable"]`. This is the whole contract: after this phase there is no boolean form to keep in agreement with it.

**Post-execution amendments (2026-08-23)**
- The Scope notes above proposed 3 lanes split by file — Lane A on `version_checker.py`, Lane B on `refresher.py`, Lane C on `tests/` — but that split did not survive the decision to delete the wrappers outright (EC-TRISTATE-3) rather than keep them as policed shims. Deleting `is_version_newer`/`are_versions_comparable` and migrating `refresher.py`'s two call sites off them is one atomic change: a task-level reducer gate between "delete" and "migrate" would trip a lane `cycle` diagnostic (each waits on the other to land first — the wrappers can't go while a caller still uses them, and the caller can't move to `compare_versions` while treating the deletion as a separate, later lane), and splitting them into two lanes instead would trip `overlapping_write_ownership` on `version_checker.py`, since the migration lane would need to observe the same file the deletion lane is editing to confirm no call sites remain. The phase executed as one working lane covering `version_checker.py` and `refresher.py` together, with tests alongside.

### Phase 2 — Update-path identity and environment contracts (UPDPATH)

**Objective**
Close the two remaining correctness gaps in the update path: a refresh short-circuit that ignores which *package* the cache describes, and an undocumented contract about which *environment* a restarted server receives.

**Exit criteria**
- [ ] EC-UPDPATH-1 — `refresh_server`'s up-to-date short-circuit refuses to fire when the cached entry's package differs from the configured package, proven by a test that is RED today: a cache for `old-package@1.0.0` against a config for `new-package@1.0.0` currently returns the stale descriptions.
- [ ] EC-UPDPATH-2 — Package identity is resolved **before** the short-circuit, not after it, so the comparison can consult it. The `pkg_name`/`detect_package_type` resolution currently sits below the early return.
- [ ] EC-UPDPATH-3 — `refresh_all`'s by-name cache lookup carries package identity, so a caller assembling the pair itself cannot bypass EC-UPDPATH-1.
- [ ] EC-UPDPATH-6 — **`check_staleness` gets the same identity gate.** It is a third site (`refresher.py:426`) pairing `existing_cache.servers` with `manifest.get_server(name)` by name and comparing versions only, and it backs `pmcp refresh --check-versions`. Without this, a cache for `old-package@2.0.0` against a config for `new-package@1.0.0` reports "All cached descriptions are up to date" at the CLI. Proven by a CLI-level regression test, not just a unit test.
- [ ] EC-UPDPATH-7 — A cache entry whose `package` field is **absent** (written before 2.2.1) does not crash and does not silently pass the identity gate; it forces a refresh.
- [ ] EC-UPDPATH-4 — The ambient-environment contract is **documented and pinned by a test**: a credential rotated during the probe window is picked up by the restarted server rather than frozen to the probe's snapshot. The docs frame this as **"`update_server` follows the same live-environment contract as every other spawn path"** — connect, refresh and auto-reconnect all use live `os.environ` — so the documented invariant is the *uniformity*, not a probe-window special case.
- [ ] EC-UPDPATH-5 — Full suite, ruff, mypy green; CHANGELOG records both fixes.

**Scope notes**
- Decompose into 3 lanes. Lane A owns `src/pmcp/manifest/refresher.py` (identity gate, EC-1/2/3). Lane B owns `src/pmcp/tools/handlers.py` plus docs (ambient contract, EC-4). Lane C owns `tests/`.
- Lanes A and B are fully disjoint by file and can run concurrently; only Lane A is blocked on TRISTATE.
- **EC-UPDPATH-4 encodes a decision, and the roadmap is making it.** #162 asks whether to freeze the ambient environment, refuse on drift, or document current behaviour. This roadmap chooses **document**: freezing would pin a credential the operator has deliberately rotated, and refusing would fail an update for a change the operator wanted. If the panel disagrees, this criterion changes shape — it is the one place v12 decides rather than discovers.
- The cross-ecosystem instance of EC-1 (an npm cache against a docker config) is **already fixed** by `are_versions_comparable` in 2.2.1 — a version and a digest are incomparable. Only the same-ecosystem case survives. Do not re-plan the part that works; verify it first.

**Non-goals**
- Changing `gateway.update_server`'s probe, restart gating, or pin refusal. 2.2.1 settled those.
- Freezing the ambient environment (see the decision above). If that becomes desirable it is a separate change threading a captured env through `ClientManager`'s spawn path.

**Key files**
- src/pmcp/manifest/refresher.py
- src/pmcp/tools/handlers.py
- tests/test_refresher.py
- tests/test_tools.py
- README.md
- CHANGELOG.md

**Depends on**
- TRISTATE

**Produces**
- (none)

### Phase 3 — Downstream catalog reconciliation and fan-out (FANOUT)

**Objective**
When a downstream server announces its catalog changed, **re-fetch and reconcile that server's entries in `ClientManager`'s indexes, then publish** — so a client that acts on the notification sees the new catalog. Closes the gap v11 P3B named in its Non-goals, and stops discarding JSON-RPC error `code`/`data` in the same dispatch paths.

**Exit criteria**
- [ ] EC-FANOUT-1 — A downstream server emitting `notifications/tools/list_changed` causes the gateway to **re-index that server** and only then publish, proven by a **real downstream emission** through the fake-remote harness. The test must assert the *catalog* changed — a tool the server added is now invocable and a tool it removed is gone — not merely that a notification arrived. A test calling the publisher directly does not satisfy this, for the same reason v11 EC-P3B-4 said so.
- [ ] EC-FANOUT-2 — Reconciliation **removes** entries the downstream server dropped, not just adds new ones. `_index_*` only adds or overwrites, so reconciliation must pair `_remove_server_indexes(name)` with a re-index; a test asserts a removed downstream tool disappears from `gateway.catalog_search` and from `invoke`.
- [ ] EC-FANOUT-3 — Both dispatch paths are wired: **stdio** (`_handle_stdout_line`) and **SSE/remote** (`_read_sse`). A test proves fan-out for each transport independently; covering one and not the other is the expected failure.
- [ ] EC-FANOUT-4 — Publishing is suppressed when reconciliation finds **no actual change**, so a chatty downstream server cannot spam subscribed clients with `list_changed` for a catalog that did not move.
- [ ] EC-FANOUT-5 — The same holds for `resources/list_changed` and `prompts/list_changed`, and an unrecognised `notifications/*` method is a no-op that neither raises nor kills the read loop.
- [ ] EC-FANOUT-6 — Reconciliation runs as a **spawned, per-server-coalesced background task**, never inline in the dispatch path. This is not a style preference: `_index_capabilities` awaits `_send_request` (`manager.py:1292`), and those futures are resolved by the very read loop that received the notification (`pending.future.set_result` at **`:1791`** and **`:2010`** — the two dispatch functions). An inline await deadlocks **instantly**. Coalescing bounds a downstream that spams `list_changed` to one in-flight re-index per server. Proven by a test that emits a notification and asserts an unrelated request **on that same downstream** still completes.
- [ ] EC-FANOUT-9 — **After a real downstream emission, a client fetching through the gateway sees the change.** A tool the downstream server added is returned by `gateway.catalog_search` and is invocable via `gateway.invoke`. This is the criterion that catches a forward-to-sink implementation, which would satisfy every notification-shaped criterion above while the gateway still served the old catalog — `get_tool` reads `self._tools` (`manager.py:2698`), populated only by `_index_tools`. It is also the only criterion that catches the inline-await deadlock in EC-FANOUT-6, because a deadlocked re-index never refreshes the catalog.
- [ ] EC-FANOUT-7 — JSON-RPC `error` objects preserve `code` and `data` alongside `message` in **both** dispatch paths, replacing both `TODO(post-P3B)` markers. Scoped to the `ClientManager` boundary: `gateway.invoke` maps every exception to `E302` through `str(e)`, and surfacing typed errors to MCP clients would change a `gateway.*` contract, which this roadmap forbids. A follow-up issue records that gap rather than smuggling it in.
- [ ] EC-FANOUT-8 — Full suite, ruff, mypy green; CHANGELOG documents downstream fan-out as new client-visible behaviour and the typed errors as an internal fix.

**Scope notes**
- **The blocking defect the panel found, recorded so it is not re-introduced.** The first draft routed a downstream notification straight to `CatalogEventSink`. `ClientManager`'s indexes back `catalog_search`, `describe`, and `invoke`, so a client told "the catalog changed" would refetch and get the *old* catalog — and a removed tool would still be invocable, because `_index_*` never deletes. That is a notification asserting something the gateway has not done: the same silent-misreport class that ended in removing update notices entirely (#150). **Reconcile first, publish second, and publish only if something moved.**
- Decompose into 4 lanes. Lane A owns the two dispatch paths in `src/pmcp/client/manager.py` (recognising notification frames). Lane B owns reconciliation in `manager.py` (`_remove_server_indexes` + `_index_capabilities`, and the did-anything-change comparison). Lane C owns the JSON-RPC error branches. Lane D owns `tests/`.
- **Lanes A, B and C all write `manager.py`.** A branch-level partition was considered and is **rejected**: A and C sit in the same `if/else` in both dispatch paths, and B is called from A's new branch. Serialize A → B → C within the phase, or give one lane the whole file and use the others for tests and review. Do not discover this at merge.
- **Publish the downstream-event shape as an intra-phase freeze on day 1** — which downstream method maps to which sink call, and the no-op guarantee for unrecognised methods — so Lane D writes tests immediately.
- **Assumption 2 is now a day-1 RED test, not an assumption.** Emit from a real peer on **each** transport and assert the read loop observes the frame. Stdio is confirmed by inspection; the SSE path is the one that could be swallowed by the SDK before the loop sees it. Do this before building on it.
- `tests/runtime/fake_remote.py` is the natural emitter. It clears the `sse_starlette` `AppStatus` latch on entry as of 2.2.1 — do not reintroduce a teardown-only reset.
- Fully disjoint from TRISTATE and UPDPATH by file, so it can be planned and executed concurrently with both.

**Non-goals**
- **Per-origin subscription filtering.** The first draft had an exit criterion requiring notifications to be filtered by *originating server*, which contradicted this roadmap's own Non-Goals and is not expressible: `CatalogEventSink` carries an event kind, not a server identity. Type-level filtering — established by v11 P3B — is what holds, and it is what the tests assert.
- Proxying downstream `notifications/progress` or logging notifications. Catalog-change notifications only.
- Surfacing typed JSON-RPC errors through `gateway.invoke` (see EC-FANOUT-7).
- Changing the client-facing subscription API.

**Key files**
- src/pmcp/client/manager.py
- src/pmcp/subscriptions.py
- tests/runtime/fake_remote.py
- tests/runtime/test_downstream_remote.py
- tests/test_client_manager.py
- CHANGELOG.md

**Depends on**
- (none)

**Produces**
- IF-0-FANOUT-1 — The downstream-event contract: which downstream `notifications/*` method maps to which `CatalogEventSink` call, the reconcile-then-publish ordering, the suppress-if-unchanged rule, and the no-op guarantee for unrecognised methods.

### Post-execution amendments (2026-08-23)

- **Lane B was a no-op.** The plan's `src/pmcp/subscriptions.py` in Key files implied a new publish path there. There wasn't one to add: `_index_*` and `_remove_server_indexes` already call `self._catalog_events.note_*` unconditionally, so once reconciliation calls them in the right order — remove, re-index, compare, publish-if-changed — the existing `note_*` → `SubscriptionBus` wiring publishes for free. `subscriptions.py` has zero diff across the phase.
- **The Lane A/C branch partition described in Scope notes was never actually available**, for a more specific reason than "A and C sit in the same `if/else`": the two `TODO(post-P3B)` error branches this phase closed live in *two different functions* (`_handle_stdout_line` and the `_read_sse` loop), one per transport, not one shared branch. Serializing A → B → C within the same file, as the plan already recommended, is what actually happened; there was no branch-level split to reject at merge time because none existed to consider.
- **SL-1's owned-files list omitted a test file.** SL-1 (Lanes A+B) is source-only by its own accounting, but its RED tests for `_handle_downstream_notification` and the reconcile scheduler had nowhere else to go and landed in `tests/test_client_manager.py` — the file SL-3 (Lane D) owned — as two clearly-named additions (`TestDownstreamReconcileScheduler` and its reconciliation-behaviour siblings). This did not collide with SL-3's own additions to the same file; it is recorded here as a gap in the plan's owned-files split, not as lane drift.

## Top Interface-Freeze Gates

- **IF-0-TRISTATE-1** — `compare_versions(current: str, latest: str, package_type: str | None) -> Literal["newer", "not_newer", "incomparable"]`, the sole classification path. `is_version_newer` and `are_versions_comparable` are deleted by phase end (EC-TRISTATE-3), so there is no boolean form to keep in agreement. UPDPATH's Lane A consumes this.
- **IF-0-FANOUT-1** — The downstream-event contract: which downstream `notifications/*` method maps to which `CatalogEventSink` call, the **reconcile-then-publish** ordering, the **suppress-if-unchanged** rule, and the no-op guarantee for unrecognised methods. FANOUT's Lanes B and D consume this on day 1.

## Phase Dependency DAG

```
  TRISTATE ──► UPDPATH

  FANOUT        (root — parallel with both, disjoint file set)


  Critical path:  TRISTATE → UPDPATH
  Concurrent:     FANOUT may be planned and executed alongside TRISTATE and UPDPATH.
                  UPDPATH Lane B (handlers.py, ambient contract) is NOT blocked on
                  TRISTATE and may start with FANOUT; only Lane A is.
```

## Execution Notes

Plan each phase with `/claude-plan-phase <ALIAS>`, then execute with `/claude-execute-phase <alias>`:

- `TRISTATE` and `FANOUT` have no shared DAG ancestor and no shared file, so they can be planned and executed **concurrently**.
- `UPDPATH` waits on `TRISTATE` only because its Lane A rewrites the same short-circuit block. Its Lane B is independent and can be pulled forward if wall-clock matters.
- `FANOUT` is the largest and the only phase changing client-visible behaviour. If capacity forces a choice, it is the one to run alone.

**Decision the panel should confirm or overturn:** EC-UPDPATH-4 documents rather than freezes the ambient environment. That is the one criterion in this roadmap encoding a judgement call rather than a defect.

## Verification

```bash
# Whole-roadmap acceptance, after the last phase merges.
uv run pytest -q                                   # 2568 baseline + new; 0 failed
uv run ruff check . && uv run ruff format --check src/ tests/ && uv run mypy src

# TRISTATE: the collapse is unrepresentable, and the lint is gone.
# With EC-TRISTATE-3 the function is GONE, so the check is import-time, not a
# grep and not a lint. Two earlier drafts of this line were wrong: one named a
# test file that has never existed, and a narrowed grep still matched docstring
# lines at `version_checker.py:560` and `:614`. Absence is the assertion.
uv run python -c "
import pmcp.manifest.version_checker as v
assert not hasattr(v, 'is_version_newer'), 'the negatable boolean is back'
assert not hasattr(v, 'are_versions_comparable'), 'the pair wrapper is back'
print('  wrappers absent')"
uv run pytest tests/test_version_checker.py -q -k 'compare_versions or corpus'

# UPDPATH: package identity gates the short-circuit.
uv run pytest tests/test_refresher.py -q -k 'package_identity or short_circuit'

# FANOUT: a REAL downstream emission RECONCILES the catalog and then notifies.
# The assertion that matters is the catalog, not the notification -- a passing
# notification test over a stale catalog is the defect this phase exists to
# avoid. Both transports must be covered.
uv run pytest tests/runtime/test_downstream_remote.py tests/test_client_manager.py \
  -q -k 'downstream_notification or reconcile'
uv run pytest -q -k 'fanout_stdio or fanout_sse'   # neither transport may be skipped

# Install smoke — mypy cannot catch a missing runtime dep here
# (ignore_missing_imports = true) and ruff does not resolve imports.
uv build && python3 -m venv /tmp/v12 && /tmp/v12/bin/pip install dist/*.whl && /tmp/v12/bin/pmcp --version
```

Edge cases to exercise: a downstream server emitting a notification *during* `connect_all`; a notification arriving after the client's subscription closed; a downstream that emits `list_changed` on every request (the suppress-if-unchanged path, EC-FANOUT-4); re-indexing that issues `tools/list` back to the connection whose reader is mid-dispatch (EC-FANOUT-6, the self-deadlock); `compare_versions` against the 28-value × 7-type corpus with drift injected into each wrapper; a cache entry whose `package` field is absent (pre-2.2.1 caches on disk).
