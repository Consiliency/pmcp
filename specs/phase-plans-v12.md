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
- [ ] EC-UPDPATH-3 — `refresh_all`'s by-name cache lookup carries package identity, so a caller assembling the pair itself cannot bypass EC-UPDPATH-1. **Routing the pair through `refresh_server` is not sufficient** *(amended 2026-08-24 after board review)*: when `refresh_server` returns `None` or raises, `refresh_all` returns the existing entry (`refresher.py:358-367`), and its final merge re-adds every cached entry missing from the new set (`:376-378`). An identity-mismatched entry must not survive either path back into the saved cache — a failed regeneration may drop the entry, but must never write the wrong package's descriptions back.
- [ ] EC-UPDPATH-6 — **`check_staleness` gets the same identity gate.** It is a third site (`refresher.py:393`, comparison at `:422` after TRISTATE moved it onto `compare_versions`) pairing `existing_cache.servers` with `manifest.get_server(name)` by name and comparing versions only, and it backs `pmcp refresh --check-versions`. Without this, a cache for `old-package@2.0.0` against a config for `new-package@1.0.0` reports "All cached descriptions are up to date" at the CLI. Proven by a CLI-level regression test, not just a unit test.
- [ ] EC-UPDPATH-7 — A cache entry whose `package` is **empty or absent** (written before 2.2.1; `load_descriptions_cache` defaults it to `""` at `refresher.py:70`, so it arrives as an empty string and cannot crash) **forces a refresh**. The gate must read an unknown side as *cannot confirm identity → refresh*, never as *cannot compare → skip the check*. Proven by a test that fails if the guard is phrased as "compare only when both sides are known" — that phrasing is the natural one to reach for, and it is the same fail-open collapse as `not is_version_newer(...)`, which shipped three times (#155, #156, #163) before TRISTATE made it unrepresentable.
- [ ] EC-UPDPATH-4 — The environment contract across the probe window is **documented and pinned by two tests**, one per class of change. *Amended 2026-08-24 after board review; the original wording was empirically false — see the amendment note in Scope notes.*
  - **4a — A config-driven change to the child environment is refused, not silently applied.** A manifest credential is resolved into `LocalMcpServerConfig.env` at load time (`config/loader.py:1025`), so rotating it during the probe window makes the recheck's child env differ from the probe's, and `update_server` returns `ok=False` without restarting (`tools/handlers.py:5090-5165`). This is the 2.2.1 TOCTOU guard behaving as designed: the guarantee is *"the config restarted onto is the config that was probed,"* and the operator's rotation applies on the next update.
  - **4b — A genuinely ambient variable is live at spawn, not frozen to a probe snapshot.** Both sides of the guard derive from one `stripped_base`, so an ambient change cancels out by construction and never causes a refusal; `sanitized_subprocess_env` (`env_store.py:138`) then calls `os.environ.copy()` at spawn time. Connect, refresh and auto-reconnect reach the same function, so the documented invariant here is the *uniformity*.
- [ ] EC-UPDPATH-5 — Full suite, ruff, mypy green; CHANGELOG records both fixes.

**Scope notes**
- Decompose into 3 lanes. Lane A owns `src/pmcp/manifest/refresher.py` (identity gate, EC-1/2/3). Lane B owns `src/pmcp/tools/handlers.py` plus docs (ambient contract, EC-4). Lane C owns `tests/`.
- Lanes A and B are fully disjoint by file and can run concurrently; only Lane A is blocked on TRISTATE.
- **EC-UPDPATH-4 encodes a decision, and the roadmap is making it.** #162 asks whether to freeze the ambient environment, refuse on drift, or document current behaviour. This roadmap chooses **document**, and that choice stands. What changed on 2026-08-24 is *what there is to document*, not the decision.

  **Amendment — the original EC-4 was factually wrong.** It asserted that "a credential rotated during the probe window is picked up by the restarted server." Verified empirically at v2.3.0: it is not. A manifest credential lives in `LocalMcpServerConfig.env`, so rotating it makes the probe and recheck child environments differ and `update_server` **refuses** with `ok=False` and no restart. The refusal is deliberate — it is the 2.2.1 TOCTOU guard, which itself landed after board review — so "document current behaviour" now means documenting *both* halves: config-driven changes are refused (4a), ambient ones are live at spawn (4b). A test written to the original wording could only have passed by choosing a variable that avoids the credential path, which would have made the criterion vacuous.
- The cross-ecosystem instance of EC-1 is **partly** already fixed, and the original claim was too broad *(amended 2026-08-24 after board review)*. An npm cache against a **docker** config is handled: a version and a digest are incomparable, so `compare_versions` returns `"incomparable"`, which is not `"not_newer"`, and the short-circuit does not fire. (2.2.1 achieved this through `are_versions_comparable`; TRISTATE deleted that function and folded the property into `compare_versions`.) But **npm ↔ pypi ↔ cargo is not handled**: those all produce orderable release versions, and `GeneratedServerDescriptions` stores `package` as a bare name with no ecosystem (`types.py:1359-1367`), so npm `foo@1.0.0` against pypi `foo@1.0.0` reads as the same package. Closing this requires the cached entry to carry its package **type**, not just its name; an absent type on a pre-existing cache reads as unknown and therefore forces a refresh under EC-UPDPATH-7, which makes the migration safe by construction. Verify the docker case rather than re-planning it; the same-ecosystem and cross-release-ecosystem cases are both in scope.

**Non-goals**
- Changing `gateway.update_server`'s probe, restart gating, or pin refusal. 2.2.1 settled those.
- Freezing the ambient environment (see the decision above). If that becomes desirable it is a separate change threading a captured env through `ClientManager`'s spawn path.

**Key files**
- src/pmcp/manifest/refresher.py
- src/pmcp/tools/handlers.py
- src/pmcp/types.py (cached entry gains its package type — added 2026-08-24)
- src/pmcp/cli.py (`--check-versions` drops `cache_path` — added 2026-08-24)
- tests/test_refresher.py
- tests/test_tools.py
- tests/test_cli.py
- README.md
- CHANGELOG.md

**Depends on**
- TRISTATE

**Produces**
- (none)

### Post-execution amendments — UPDPATH (2026-08-24)

- **EC-UPDPATH-7 needed no code path of its own.** The empty-`package` case falls
  out of `_same_package` for free: the predicate rejects any of its four
  arguments being `None`, `""` or `"unknown"` in one loop, and
  `load_descriptions_cache` renders an absent `package` as `""`, so the same arm
  that covers an absent `package_type` covers it. There is no empty-package
  branch anywhere in `refresher.py`, and the criterion is pinned by
  `TestUnknownPackageForcesRefresh::test_empty_cached_package_forces_refresh`
  rather than by dedicated code.
- **The gate landed *inside* the short-circuit conjunction, after the version
  fetch — not strictly before it.** EC-UPDPATH-2 is satisfied in the sense that
  matters: the `detect_package_type` / `pkg_name` resolution moved above the
  early return (exactly one call remains in `refresh_server`), so the comparison
  can consult identity at all. But the `_same_package` call is the second
  conjunct of the existing `if version and … and compare_versions(…) ==
  "not_newer"`, so `get_package_version` still runs first on every non-forced
  path. SL-1 reported that gating *before* the fetch broke
  `TestUpToDateShortCircuit`'s call-count assertion — an identity mismatch would
  short-circuit past the fetch entirely and the version lookup would never
  happen. As placed, the fetch is not skipped and no existing assertion moved.
  A future lane that wants to save the fetch on a mismatch must expect that test
  to need rewriting, not just the guard to move.
- **Two operator-visible consequences the plan did not anticipate.** Both are in
  the CHANGELOG under `[Unreleased]`:
  1. `pmcp refresh --check-versions` now reports an **unclassifiable local
     server** (`node /opt/srv.js`, `python -m thing`) as stale on **every** run,
     printing `local: 1.0.0 -> unknown` and the "Run 'pmcp refresh --force' to
     update." footer permanently — a refresh does not settle it, because the
     next check still cannot classify the package. Such servers were previously
     skipped in silence. This follows unavoidably from "cannot confirm
     identity → refresh"; the plan chose that semantics deliberately but did not
     trace it to this output.
  2. `refresh_all` now **drops** an unclassifiable server's cached entry when
     regeneration fails, where it previously retained it. The plan explicitly
     sanctioned dropping on failure ("dropping the entry on a failed
     regeneration is acceptable"), but reasoned about it only for a *confirmed*
     mismatch. For a `node`/`python` server there is no evidence of a mismatch,
     only an inability to confirm one, so a transient startup failure now costs
     cached descriptions that were probably still accurate.
- **EC-UPDPATH-3 needed a `refresh_all`-local record, not just a gated pair.**
  Setting `existing = None` for a mismatched server stops the callee from
  short-circuiting, but does nothing about the final merge loop, which re-adds
  every cached entry missing from the new set straight from `existing_servers`.
  `refresh_all` therefore keeps a `mismatched: set[str]` and excludes those names
  from the merge. The plan named both failure paths correctly; what it did not
  say is that closing them requires state the gate itself cannot carry.
- **The plan's fixture-update prediction was right, and is easy to misread as
  wrong.** `git diff --numstat main..HEAD -- tests/` shows **zero** deleted
  lines, which looks like no existing test was touched. Four pre-existing
  fixtures did need `package_type="npm"` added — three in `TestCheckStaleness`
  (as the plan predicted) and one in
  `TestShortCircuitUsesCompareVersions::test_short_circuit_is_a_single_compare_versions_call`
  (which the plan named but listed under a different class) — and every one of
  them was a pure insertion. `TestUpToDateShortCircuit` stayed **green** with no
  fixture change: both of its tests assert the short-circuit does *not* fire, so
  an extra always-`False` conjunct cannot change their outcome. That also means
  those two tests do **not** discriminate a degenerate gate; the one that does
  is `test_short_circuit_is_a_single_compare_versions_call`'s `result is
  existing`, exactly as the plan said.
- **Correction (pre-merge board): "needed nothing" was true for staying green
  and false for staying load-bearing — the phase hollowed out two pre-existing
  regression guards.** `TestUpToDateShortCircuit`'s fixtures carried no
  `package_type`, so under the new gate `_same_package` refused on the unknown
  type and the short-circuit's `and` chain **never reached
  `compare_versions`**. Both tests still passed, but for the identity reason,
  pinning nothing about the version comparison they exist to guard — the
  unorderable/incomparable fail-open of Consiliency/pmcp#156 and #164.

  Demonstrated, not argued: rewriting the short-circuit to
  `compare_versions(...) != "newer"` — so an `incomparable` pair reads as up to
  date, which is precisely that fail-open — left the **entire suite green**
  (172 passed across the two files). A phase whose purpose is to close a
  fail-open had silently removed the only coverage of a different one, and
  neither the lane, the orchestrator, nor the plan-stage board caught it; the
  adversarial seat of the pre-merge board did.

  Fixed by making both fixtures confirm identity so the version path is
  actually exercised: `package_type="npm"` for the unorderable case, and the
  incomparable case rebuilt as a **same-ecosystem** docker pair (a cached tag
  against a fetched digest) — the original npm-cache-vs-docker-config pairing is
  now refused on ecosystem alone by the very gate this phase added, so it could
  never have reached `compare_versions` again. That cross-ecosystem case keeps
  its own coverage in `TestPackageIdentityGate`. Both tests now fail under the
  mutant and pass without it.

  **The general lesson, which outlives this phase:** adding an early conjunct to
  an existing condition can silently retire every test downstream of it. Those
  tests keep passing, so nothing signals the loss. When a gate is inserted ahead
  of an existing check, every test that reached the old check through the new
  gate's position must be re-proved load-bearing by mutation, not by re-running
  it green.
- **A third write site for package identity was missed until the pre-merge
  board.** `gateway.update_server` mutates the cached entry in place
  (`tools/handlers.py`), setting `version`, `tools` and `generated_at` but not
  `package` or `package_type`. Two consequences, both closed here. A legacy
  entry passing through `update_server` stayed permanently unverifiable even
  though `update_server` had just classified its package. Worse, the entry could
  end up carrying the **new** package's version and live tool list under the
  **old** package's label — after which `_same_package` would *confirm* identity
  against the stale label and the short-circuit would serve the wrong package's
  descriptions, which is the exact defect this phase exists to close, reachable
  in three operator steps (swap the config, `update_server`, revert). Fixed by
  writing `package` and `package_type` alongside the version, pinned by
  `test_update_server_relabels_the_entry_with_the_package_it_describes`. The
  lane's audit found two write sites and the plan repeated it; the count was
  three.
- **The CLI split's group boundary was unpinned.** The test asserting a swap is
  reported checked only that the server name appeared in the output — and the
  name appears in *both* groups, so it passed whichever group the entry landed
  in. A classifiable identity mismatch at an equal version is actionable
  (`--force` settles it) and must sit in the stale group *with* the remedy;
  routing it to the "could not confirm" group left the suite green. Now pinned
  on the rendered line and the remedy's presence.
- **EC-UPDPATH-4 was docstring-only, as planned, and the docstring is now pinned
  by a test.** `handlers.py`'s diff for this phase is the `update_server`
  docstring and nothing else. `test_update_server_docstring_states_both_probe_window_env_contracts`
  asserts both halves are stated, so an edit that collapses them back into a
  single "live environment" sentence fails the suite rather than passing review.
- **`docs/` was not created.** SL-3 owned `docs/**` and deliberately left it
  absent; the probe-window environment contract landed in `README.md` under
  "Subordinate MCP Updates", which is where a user will actually meet it.
- **The docs-catalog rescan helper is absent on this host and its history is
  already incomplete for v12.** `_shared/scaffold_docs_catalog.py` does not
  exist, so SL-3 audited `.claude/docs-catalog.json` by hand. That was the right
  outcome independently: the helper scans a fixed set of roots plus a
  `KNOWN_FILES` list that does **not** include `SPEC_COMPLIANCE.md`, so a rescan
  would have dropped that root-level entry — Consiliency/pmcp#171, confirmed
  against the helper's source rather than assumed. Separately, both earlier v12
  phases left `touched_by_phases` empty on the files they edited (TRISTATE and
  FANOUT are absent from `CHANGELOG.md`'s list, and `specs/phase-plans-v12.md`
  carried an empty list despite holding FANOUT's own amendment block), so that
  field is not a reliable history for v12. UPDPATH's aliases were added; the
  missing ones were **not** backfilled, because there is no way to verify from
  the catalog alone which files each phase actually touched.

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
