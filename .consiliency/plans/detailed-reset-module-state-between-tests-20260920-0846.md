# Detailed plan: reset process-global module state between tests — slice B of #235

## Task
Stop cross-test pollution of module-level mutable state in `src/pmcp/` from producing ordering-dependent test outcomes (Consiliency/pmcp#235, slice B of three). Every piece of process-global state a test can mutate gets a `clear_*`/`reset_*` helper **in the module that owns it**, and one autouse fixture in `tests/conftest.py` calls those helpers around every test — the pattern `reset_dotenv_keys()` / `_reset_dotenv_provenance` already established for #229. Fixtures never reach into module globals; the reset contract lives beside the state it resets.

Slice A (cwd/filesystem isolation, PR #263) and slice C (deterministic time / #226) are separate plans. This plan touches no cwd, no clock, and no test other than the ones named below.

## Execution Policy
- execute: effort=medium, reason=mechanical helpers, but one of them cancels asyncio tasks that may belong to a closed loop and another kills a child process at teardown; the autouse fixture is inherited by every test in the suite.

## Research summary
Direct reads of every file named below, on `main` at `4461e2f`; no Explore survey (the caller's file map was verified line by line, and three of its conclusions were wrong — see *Corrections*). A throwaway pytest plugin (`pytest_sessionfinish` printing each global) was run over the full suite to see what actually leaks at session end; its findings are in *Verification* item 0 and the *Corrections* list.

**State inventory (what a test can pollute, and what consumes it):**

| Owner | State | Consumers | Reset helper today |
|---|---|---|---|
| `manifest/version_checker.py:22` | `_version_cache: dict[str, str]` — read/written by the npm/PyPI/cargo/docker lookups at `:1387-1547` | every `check_*_version` call | `clear_version_cache()` at `:2000-2002` — **exists, called by hand in `tests/test_version_checker.py` (`:1410`, `:1537`, `:1639`, `:1740`, `:1825`, `:1910-1924`), wired into no fixture** |
| `env_store.py:246` | `_PMCP_INTRODUCED_KEYS: set[str]` | `env_key_is_operator_supplied()` (`:350`) — gates `PMCP_CONFIG` in `config/loader.py:61` and `cli.py:2300/2307`, `PMCP_MANIFEST_PATH` in `manifest/loader.py:712`; and `feedback_egress.py:196` | `reset_pmcp_introduced_keys()` at `:305-314` — **exists; reset only by per-file fixtures (nine files, not three — see Corrections)** |
| `env_store.py:194` | `_DOTENV_SOURCED_KEYS` | `sanitized_subprocess_env()` (`:441`), `env_key_is_operator_supplied()` | `reset_dotenv_keys()` `:230-239`, **already autouse** via `tests/conftest.py:151 _reset_dotenv_provenance`. Not touched; it is the pattern. |
| `manifest/registry.py:26-27` | `_IN_PROCESS_CACHE: dict[key, (monotonic, RegistryCache)]`, `_IN_PROCESS_TASKS: dict[key, asyncio.Task]` — populated at `:630` and `:639`, tasks popped in a `finally` at `:634` | `fetch_registry_servers(use_in_process_cache=True)` (`:588`, the default), reached from `tools/handlers.py:3160` | **none** |
| `transport/http.py:67-68` | `_rl_store: dict[str, deque]` (per-IP sliding window) and `_rl_lock: asyncio.Lock \| None` (lazily created at `:159-160`) | `_check_rate_limit()` `:155-178` | **none** — `tests/test_transport_http.py:310` and `tests/test_http_transport.py:313` do `http_mod._rl_store.clear()` by hand, which is the "fixture reaches into the module" anti-pattern this plan removes and also proof the pollution is real |
| `transport/http.py:55-62` | `_metrics: dict[str, int]` (six counters) | `_inc()` `:134-137`; fallback renderer `:412-414` iterates `_metrics.items()` | **none** — `tests/test_transport_http.py:213` copes with a `before` delta |
| `transport/http.py:103-131` | `_prom_counters: dict[str, prometheus_client.Counter]` | `_inc()` | **not resettable state** — see Corrections; left alone |
| `manifest/npm_resolver.py:615-616` | `_resolver: NpmResolver \| None` (+ `_resolver_lock`), a lazily spawned node child (`:619-631`) | `version_checker.py:1208`, `tools/handlers.py:2689` | `reset_resolver_for_tests()` at `:634-640` — **exists (the caller's map said none), unwired; `close()` (`:568-570`) → `_terminate()` (`:306-326`) is idempotent, so a double reset is safe** |

**Constraints that shape the design:**
- `tests/test_feedback_provenance.py:201-229` asserts, by AST walk of every file under `src/pmcp` (`_src_calls_to`, `:115`), that **nothing in `src/` calls `reset_pmcp_introduced_keys`**. So there must be **no aggregating `reset_everything_for_tests()` in `src/`**; the conftest fixture composes the per-module helpers itself.
- `tests/conftest.py` has exactly three autouse fixtures — `isolate_trust_store` (`:62`), `_reset_dotenv_provenance` (`:151-165`, before-and-after, the model), `_no_live_npm_registry` (`:168-192`). Slice A (PR #263) adds a fourth, `isolate_cwd`, to the same region.
- `pyproject.toml:146-149`: `asyncio_mode = "auto"`, no `loop_scope` override, so pytest-asyncio gives each async test its own event loop. Anything bound to a loop and kept in a module global (`_rl_lock`, a `Task` in `_IN_PROCESS_TASKS`) is bound to a loop that is closed by the next test.
- `.github/workflows/test.yml:312-367`: a PR touching `src/` must add a `CHANGELOG.md` entry **or carry the `skip-changelog` label** ("if this change ships no user-visible behavior").
- prometheus_client 0.24.1 (installed): a `Counter` is registered with the global `CollectorRegistry` at construction; constructing a second one with the same name raises `ValueError: Duplicated timeseries` (verified in the venv). Its value can be zeroed through the public `Counter.reset()` ("Reset the counter to zero…", implemented as `_value.set(0)`) — a safe optional extra, but it does not change the re-registration constraint.

**Corrections to the caller's research (recorded because direct reads disproved them):**
1. `_PMCP_INTRODUCED_KEYS` does **not** pollute `sanitized_subprocess_env()`. That function (`env_store.py:415-446`) strips only `managed_secret_keys()` and `dotenv_sourced_keys()`; the docstring at `:287-292` says the two registries are kept separate precisely so `_PMCP_INTRODUCED_KEYS` never widens the subprocess strip. What it *does* pollute is the trust-bearing env-var gate: a test that records `PMCP_CONFIG`/`PMCP_POLICY`/`PMCP_MANIFEST_PATH` as PMCP-introduced (any test that loads a store file through `record_pmcp_introduced_keys`) makes every later test that exports the same variable see it **ignored** with the "Ignoring … set by a project file" warning (`config/loader.py:61`, `cli.py:2300/2307`, `manifest/loader.py:712`), plus the egress gate's cache at `feedback_egress.py:196`. Same hazard class as #229, different consumer.
2. `npm_resolver.py` **has** a reset helper: `reset_resolver_for_tests()` at `:634-640`. It closes the child under `_resolver_lock` and drops the singleton. No new helper is needed there; it just needs wiring. `close()` → `_terminate()` swaps `_proc`/`_reader` to `None` before killing, so a second call is a no-op and there is no double-kill path.
3. The per-file `reset_pmcp_introduced_keys` fixtures are in **nine** files, not three: `test_feedback_egress_gate.py:73-78`, `test_env_overlay_provenance.py:77-83`, `test_feedback_provenance.py:77-79`, `test_egress_panel_fixes.py:107-109`, `test_trust_boundaries_composition.py:248-250`, `test_feedback_egress.py:124-126`, `test_refusal_remedies.py:178-181`, `test_feedback_submission_flag.py:118-120`, `test_trust_boundaries_e2e.py:199-201`. They stay (idempotent; one also pops `TRUST_VARS`).
4. `_prom_counters` is not a resettable dict. It is an import-time table of six registry-bound `Counter` objects (`http.py:110-131`); clearing the dict would break `_inc()` for the rest of the process and re-creating the counters raises `Duplicated timeseries`. The only mutable thing is each counter's value, monotonic by contract; `Counter.reset()` is public on 0.24.1 and could zero it, but the table itself must never be cleared or rebuilt. No test asserts an absolute counter value (`test_transport_http.py:84-113`, `:213`, `test_http_transport.py:277-307`, `mcp2x/test_get_retirement.py:105-107` check presence/format). **Left alone, deliberately.**
5. `_IN_PROCESS_TASKS` mostly cleans itself: the task is popped in a `finally` (`registry.py:631-634`) the moment the awaiting coroutine resumes. An entry survives only when the test's loop is closed while the fetch is in flight, which is exactly the case where `task.cancel()` raises `RuntimeError: Event loop is closed` from `call_soon` — the helper has to tolerate that. Measured precisely (3.10.12 here; the review panel reproduced it on 3.12.12 and 3.13.12): the raise happens only for a task that has **started** — `Task.cancel` reaches `call_soon` through the waiter future's `_schedule_callbacks`, so `_fut_waiter` must be set. A task created on a loop that is closed **without ever running** has `_fut_waiter is None`; its `cancel()` returns `True` and raises nothing. The regression test below has to start the task, or it cannot fail.
6. `_rl_lock` (`http.py:68`) is state the caller's map missed. On Python >= 3.10 an `asyncio.Lock` binds to the running loop on its first contended `acquire()` and raises `RuntimeError: … is bound to a different event loop` when used from another; kept in a module global across function-scoped loops it is a latent cross-loop failure. It resets with the store.

Everything in the suite that exercises the registry fetch through the handler stubs `_registry_matches` (`tests/test_tools.py:4521-4530`) or passes `use_in_process_cache=False` (`tests/test_registry.py`, `tests/test_ssrf_registry.py`), and `tests/test_npm_resolver.py:47-52` builds and closes its **own** `NpmResolver()` instances — so the registry cache and the resolver singleton are latent hazards today rather than observed failures. They get helpers anyway: fix the class, not the instance.

**Known process globals deliberately outside this slice** (recorded so the next reader does not mistake omission for oversight): `manifest/installer.py:142 JobManager` (background install jobs — a lifecycle object, not a cache), `identity.py:133 _LOCK_FD` (the instance-lock file descriptor, acquired at `:185-242` and released by the function at `:251`; a process-level lock, not test state), and `trust_store.py:84 _active_project_root` (set through `set_active_project_root()`; the one-shot path at `cli.py:1381-1387` clears it in a `finally`, `serve` at `:2430` leaves it set for the process lifetime). Each would need its own owner-side contract and none of them showed up in the leak probe; if one starts producing ordering-dependent failures it gets its own bounded plan.

## Changes

### `src/pmcp/manifest/registry.py` (modify)
- `clear_in_process_cache()` — **add**, module-level, synchronous, placed directly after `fetch_registry_servers` (search for `def _cache_path`) — drops `_IN_PROCESS_CACHE` and `_IN_PROCESS_TASKS`. For each task in `_IN_PROCESS_TASKS.values()`: `if not task.done(): try: task.cancel() except RuntimeError: pass` (the task's loop may already be closed — Correction 5), then `.clear()` both dicts. Must not `await` anything: it is called from a synchronous fixture with no running loop. Docstring in the `reset_dotenv_keys` voice: test-only seam; production never calls it; the cache is keyed by `(endpoint, timeout, max_pages, max_response_bytes, updated_since, allow_draft_schema)` with a 300 s TTL (`REGISTRY_CACHE_TTL_SECONDS`, `:23`), so a payload one test cached for `DEFAULT_REGISTRY_ENDPOINT` is what the next test gets for the whole session.

### `src/pmcp/transport/http.py` (modify)
- `reset_rate_limit_state()` — **add**, after `_check_rate_limit` (search for `def _is_loopback_host`) — `_rl_store.clear()` and `global _rl_lock; _rl_lock = None`, so the next request re-creates the lock under the current event loop (Correction 6). Docstring names both halves and why the lock is included.
- `reset_request_metrics()` — **add**, beside `_inc` (search for `def _inc`) — `for key in _metrics: _metrics[key] = 0`. **Assign, do not `clear()`**: the fallback renderer at `handle_metrics` (`:412-414`) iterates `_metrics.items()`, and `_inc` does `_metrics[key] += 1`, so a missing key is a `KeyError` on the next request. Docstring states that `_prom_counters` is deliberately **not** touched and why (Correction 4), so the next reader does not "fix" it.

### `src/pmcp/manifest/version_checker.py`, `src/pmcp/env_store.py`, `src/pmcp/manifest/npm_resolver.py` (no change)
- `clear_version_cache()` (`version_checker.py:2000`), `reset_pmcp_introduced_keys()` (`env_store.py:305`), `reset_resolver_for_tests()` (`npm_resolver.py:634`) already exist with the right contract. Wire them; do not duplicate or rename them. (`reset_resolver_for_tests` is not renamed to match the others: `test_feedback_provenance.py`'s AST guard is name-based and a rename would invite a matching guard for nothing.)

### `tests/conftest.py` (modify)
- imports (`:41-52`) — **modify** — extend the existing `from pmcp.env_store import reset_dotenv_keys` at `:43` to `from pmcp.env_store import reset_dotenv_keys, reset_pmcp_introduced_keys` (one import statement, not a second one); add `from pmcp.manifest import npm_resolver, registry, version_checker` beside the existing `from pmcp.manifest import package_identity` (`:42`); add `from pmcp.transport import http as transport_http`. Importing `pmcp.transport.http` at conftest import time is new: it pulls starlette/mcp and registers the prometheus counters once, at collection instead of at the first HTTP test. No test asserts on `sys.modules` for these modules (checked: `tests/mcp2x/test_dependency_bounds.py:100`, `test_setup_command.py:24`, `test_security_claims*.py` are about other modules). If the implementer finds an import-order regression, fall back to importing inside the fixture body — that is the only acceptable variation.
- `_reset_process_global_state` — **add**, `@pytest.fixture(autouse=True)`, placed **after** `_no_live_npm_registry` (`:168-192`), i.e. at the end of the autouse block, to keep the textual collision with slice A's `isolate_cwd` to a single hunk. Body is the `_reset_dotenv_provenance` shape (`:151-165`): call the six helpers, `yield`, call them again. Order inside the body: `reset_pmcp_introduced_keys()`, `version_checker.clear_version_cache()`, `registry.clear_in_process_cache()`, `transport_http.reset_rate_limit_state()`, `transport_http.reset_request_metrics()`, `npm_resolver.reset_resolver_for_tests()` — the resolver last because it is the only one that can block (up to 2 s in `proc.wait(timeout=2.0)`, `npm_resolver.py:318`) and the only one that touches a process. Docstring: one line per helper naming the state, the consumer that makes it an ordering hazard, and the `tests/test_feedback_provenance.py:226` rule that keeps composition here rather than in `src/`. It must say plainly that the resolver reset re-spawns the node child for each test that resolves an npm identity (about 43 ms per spawn per `npm_resolver.py:237-238`), which is the price of a resolver whose `_sticky`/`_warned`/`_npm_version` state is never inherited from an earlier test.
- `_reset_dotenv_provenance` (`:151-165`) — **no change**. Do not fold `reset_dotenv_keys` into the new fixture; it stays the named precedent for #229 and slice A's plan cites it by line.

### `tests/test_transport_http.py` (modify)
- `TestRateLimitHttp.test_rate_limit_blocks_over_limit` (`:306-314`) — **modify** — delete the `from pmcp.transport import http as http_mod` / `# Reset store …` / `http_mod._rl_store.clear()` lines; the autouse fixture now guarantees the empty bucket. Leave the assertions.
- `TestRateLimitHttp.test_rate_limit_bucket_is_empty_at_test_start` — **add** — a two-line behavioural check that the reset holds in default file order: with `rate_limit_rpm=1`, the **first** `POST /mcp` of the test is not 429. It runs after `test_rate_limit_allows_under_limit` (`:300-304`, which leaves five timestamps in the `testclient` bucket) and after `test_rate_limit_blocks_over_limit`; without the reset the bucket is already over the limit and the first request is 429. This is the regression test for `reset_rate_limit_state()` and it never touches `_rl_store`.
- `TestMetricsEndpoint.test_request_metrics_reset_keeps_every_key` — **add** — call `transport_http.reset_request_metrics()`, then `client.get("/metrics")` with `_generate_latest` monkeypatched to `None` (the shape of `test_metrics_fallback_renderer`, `:93-102`) and assert all six `pmcp_requests_*` names still render and the endpoint is 200. Guards the assign-not-clear rule.

### `tests/test_http_transport.py` (modify)
- `test_rate_limit_uses_one_bucket_for_same_client_ip` (`:310-318`) — **modify** — delete the `from pmcp.transport import http as http_mod` and `http_mod._rl_store.clear()` lines for the same reason.

### `tests/test_registry.py` (modify)
- `test_clear_in_process_cache_drops_payloads_and_tolerates_closed_loop_tasks` — **add** — populate `registry._IN_PROCESS_CACHE` with a fake `(monotonic, RegistryCache)` entry and put into `registry._IN_PROCESS_TASKS` a task that is **started and then orphaned**: on a **separate** `loop = asyncio.new_event_loop()`, `task = loop.create_task(<coroutine that awaits asyncio.Event().wait()>)`, then `loop.run_until_complete(asyncio.sleep(0))` so the task runs one cycle and parks on its waiter (`task._fut_waiter is not None`, `not task.done()`), then `loop.close()`. **Closing without that one cycle produces a task whose `cancel()` returns `True` and never raises** (measured; see Correction 5) — a test built that way is green with or without the guard and proves nothing. Assert the precondition in the test (`assert not task.done()`), call `clear_in_process_cache()`, assert both dicts are empty and no exception escaped. Writing the globals directly is acceptable **here only**, because the subject of the test is the helper that owns them — the same licence `tests/test_version_checker.py:1906-1916` takes with `_version_cache`. Add a second, cheaper case: a done task (from `asyncio.run`) is cleared without `cancel()` being needed.

### `CHANGELOG.md` (modify — or `skip-changelog`)
- `## [Unreleased]` — **decide, do not skip the decision**: this PR touches `src/` so `test.yml:312-367` requires either a changelog entry or the `skip-changelog` label. The two helpers are test-only seams with no user-visible behaviour; the label is what the gate offers for exactly that case. **Apply `skip-changelog` on the PR** and say so in the PR body. If a reviewer prefers an entry, one line under a `### Internal` heading naming the two helpers is the fallback; do not write a user-facing "Fixed" entry for a change no user can observe.

## Documentation impact
- `tests/conftest.py` module docstring (`:1-28`) — **no change**. It is about the npm local-prefix hazard, which is slice A's subject; slice A's plan already rewrites it, and touching it here would guarantee a second textual conflict in the same file.
- `CHANGELOG.md` — see the entry above (label, not text).
- No `README.md`, `docs/**`, `CONTRIBUTING.md`, `AGENTS.md` footprint: none of them mention `conftest`, `autouse`, or the reset seams (grepped).

## Dependencies & order
1. **Land slice A (PR #263, `tests/conftest.py` + `plans/manifest.json`) first; rebase this branch onto it before implementing.** The two plans are functionally independent — no fixture here reads cwd, and `isolate_cwd` touches no module global — but both add an autouse fixture to the `:151-192` region of `tests/conftest.py` and both append an entry to `plans/manifest.json`, so whichever lands second conflicts textually. This plan is the later of the two (slice A's plan predates it and already records the same conclusion in its own `## Dependencies & order` item 4). Placing `_reset_process_global_state` at the **end** of the autouse block keeps the rebase to one trivially-resolved hunk.
2. Add the two `src/` helpers before the conftest fixture (the fixture imports them; a missing name fails collection for the whole suite, which is the loudest possible failure but not a useful one).
3. Add the two regression tests in `tests/test_transport_http.py` **before** deleting the manual `_rl_store.clear()` lines, so the bucket-is-empty test is proven red-then-green by the fixture and not by the deleted line.
4. No dependency on slice C. `isolate_trust_store` ordering is irrelevant to this fixture: none of the six helpers reads `HOME`.
5. `test_feedback_provenance.py:226` must stay green: verify neither new `src/` helper (nor anything else in `src/`) calls `reset_pmcp_introduced_keys`.

## Verification
`automation.suite_command`: `uv run pytest -q -p no:cacheprovider`

Run from the implementation worktree after `uv sync -p 3.10 --all-extras` (a fresh worktree otherwise resolves `pytest` from the system interpreter).

0. **Baseline leak probe (before any change), so the after-run has something to compare against.** Save this as `<scratchpad>/leak_probe.py` — it is never committed:
   ```python
   import sys
   def pytest_sessionfinish(session, exitstatus):
       from pmcp import env_store
       from pmcp.manifest import npm_resolver, registry, version_checker
       from pmcp.transport import http as h
       sys.stderr.write("\n".join([
           "", "=== LEAK PROBE ===",
           f"_version_cache={len(version_checker._version_cache)}",
           f"_IN_PROCESS_CACHE={len(registry._IN_PROCESS_CACHE)} _IN_PROCESS_TASKS={len(registry._IN_PROCESS_TASKS)}",
           f"_metrics={h._metrics} _rl_store={list(h._rl_store)} _rl_lock={h._rl_lock!r}",
           f"_resolver={npm_resolver._resolver!r} spawn_attempts={getattr(npm_resolver._resolver, 'spawn_attempts', None)}",
           f"_PMCP_INTRODUCED_KEYS={sorted(env_store._PMCP_INTRODUCED_KEYS)}",
           "=== END ===", ""]))
   ```
   `PYTHONPATH=<scratchpad> uv run pytest -q -p no:cacheprovider -p leak_probe 2>&1 | tail -12`. The pre-change run on this host (see the *Evidence* note at the end of this file) shows which globals are non-empty at session end. **After** the change every line must report empty/zero/`None` — the after-teardown half of the fixture is what makes that true.
1. **Unit tests for the new helpers**: `uv run pytest -q -p no:cacheprovider tests/test_registry.py -k clear_in_process_cache tests/test_transport_http.py -k "bucket_is_empty or reset_keeps_every_key"`.
2. **Mutation — rate limit**: comment out the `transport_http.reset_rate_limit_state()` calls in the fixture and run `uv run pytest -q -p no:cacheprovider tests/test_transport_http.py::TestRateLimitHttp`. `test_rate_limit_bucket_is_empty_at_test_start` must go **red** (first request 429). Restore; green.
3. **Mutation — metrics keys**: change `reset_request_metrics()` to `_metrics.clear()` and run `tests/test_transport_http.py -k "reset_keeps_every_key or fallback_renderer"`; must go red with a `KeyError` or missing `pmcp_requests_*` line. Restore; green.
4. **Mutation — closed-loop task**: change `clear_in_process_cache()` to call `task.cancel()` without the `RuntimeError` guard; the closed-loop case in `tests/test_registry.py` must go red with `RuntimeError: Event loop is closed`. Restore; green. **If the mutation stays green, the test's task never started** (it was closed before `run_until_complete(asyncio.sleep(0))`) — fix the test, not the guard. One-liner to confirm the interpreter behaves as the plan says: `uv run python -c "import asyncio; l=asyncio.new_event_loop(); t=l.create_task(asyncio.Event().wait()); l.run_until_complete(asyncio.sleep(0)); l.close(); t.cancel()"` must print `RuntimeError: Event loop is closed`.
5. **Ordering independence, without adding a plugin dependency** (neither `pytest-randomly` nor `pytest-random-order` is installed): save `<scratchpad>/reverse_order.py` containing
   ```python
   def pytest_collection_modifyitems(session, config, items):
       items.reverse()
   ```
   and run `PYTHONPATH=<scratchpad> uv run pytest -q -p no:cacheprovider -p reverse_order`. Compare the failure set with the default-order run from the same host in the same session: **the two sets must be identical** (ideally both empty; a host-specific failure such as #261's cwd precondition must appear in both or neither). Any test that fails in one order only is a pollution this plan missed — name the global in the PR, do not widen the plan silently.
6. **Test-only-seam guard**: `uv run pytest -q -p no:cacheprovider tests/test_feedback_provenance.py -k not_production_clearable` stays green.
7. **Resolver cost is bounded**: compare wall time of `uv run pytest -q -p no:cacheprovider tests/test_version_checker.py tests/test_npm_resolver.py tests/test_cli.py` before and after; the increase must be under ~10 s on this host (each re-spawn is ~43 ms; those files hold the tests that reach the real resolver; the baseline run spawned the global child exactly once for the whole session, `spawn_attempts=1`). **Pre-decided fallback if it is larger**: demote only the resolver call to a separate `scope="session"` autouse fixture that calls `reset_resolver_for_tests()` at session end — that still closes the leaked child (the observed defect) and gives up only the per-test sticky-state guarantee (the latent one); record the measured number and the demotion in the PR body. Do not drop the reset entirely.
8. **Full suite, both orders, plus lint**: `uv run pytest -q -p no:cacheprovider`; item 5; `uv run ruff check src/pmcp/manifest/registry.py src/pmcp/transport/http.py tests/conftest.py tests/test_registry.py tests/test_transport_http.py tests/test_http_transport.py && uv run ruff format --check` on the same paths.

Edge cases to cover in the helpers' tests or by inspection: `clear_in_process_cache()` with both dicts already empty (no-op); `reset_rate_limit_state()` before any request (lock is already `None`); `reset_resolver_for_tests()` when the resolver was never spawned (`_resolver is None`, no process to kill) and when a spawn *attempt* failed on a node-less host (`_proc is None`, `_sticky` set — the reset must still drop the singleton so the sticky state does not leak).

## Acceptance criteria
- [ ] After the change, the leak probe (Verification 0) reports `_version_cache=0`, `_IN_PROCESS_CACHE=0 _IN_PROCESS_TASKS=0`, every `_metrics` value `0`, `_rl_store=[]`, `_rl_lock=None`, `_resolver=None`, `_PMCP_INTRODUCED_KEYS=[]` at the end of a full-suite run.
- [ ] `tests/test_transport_http.py::TestRateLimitHttp::test_rate_limit_bucket_is_empty_at_test_start` passes in default order and fails (first request 429) when the fixture's `reset_rate_limit_state()` call is commented out (Verification 2).
- [ ] `tests/test_registry.py -k clear_in_process_cache` passes, including the case of a **started, not-done** task (`_fut_waiter` set) whose event loop is then closed, and that same case fails with `RuntimeError: Event loop is closed` when the `cancel()` guard is removed (Verification 4) — a never-started task does not satisfy this criterion.
- [ ] The full suite's failure set is identical in default and reversed collection order on the same host (Verification 5), and `tests/test_feedback_provenance.py -k not_production_clearable` is green (no `src/` caller of `reset_pmcp_introduced_keys`).
- [ ] No remaining `_rl_store.clear()` under `tests/` (`grep -rn "_rl_store" tests/` returns nothing), `_prom_counters` is untouched, and the PR carries `skip-changelog` (or an `### Internal` entry) so `test.yml`'s changelog gate passes.

## Evidence — pre-change leak probe (this host, 2026-09-20, `main` at `4461e2f`)
Full suite with the Verification-0 plugin, in a worktree under `/mnt/HC_Volume_105438154/worktrees/` (`uv sync -p 3.10 --all-extras`; `--maxfail=50` tripped at 2309 passed / 50 failed / 254 s — every failure is `tests/test_npm_resolver.py` refusing because `/mnt/HC_Volume_105438154/node_modules` is an ancestor of the worktree, i.e. slice A's ~107-test artifact, unrelated to this plan and a second reason slice A lands first). State at session end:

| Global | Observed | Reading |
|---|---|---|
| `version_checker._version_cache` | 0 entries | latent — `tests/test_version_checker.py` clears by hand |
| `registry._IN_PROCESS_CACHE` / `_IN_PROCESS_TASKS` | 0 / 0 | latent — every caller stubs above the cache or passes `use_in_process_cache=False` |
| `http._metrics` | `{'requests_total': 35, 'requests_401': 5, 'requests_403': 4, 'requests_503': 1, 'requests_429': 1, 'requests_ok': 12}` | **leaks**; tolerated by delta assertions |
| `http._rl_store` | `['testclient']` — one live bucket | **leaks**; the two hand-written `.clear()` calls exist because of it |
| `http._rl_lock` | created, `_loop=None` (never contended) | latent cross-loop hazard |
| `http._prom_counters` | 6 registry-bound counters, `requests_total=35.0` | not resettable state (Correction 4) |
| `npm_resolver._resolver` | live `NpmResolver`, child `Popen` with `returncode=None`, `spawn_attempts=1` | **leaks a running node process past session end** |
| `env_store._PMCP_INTRODUCED_KEYS` | `[]` | clean at the end only because the last recording test happened to sit in a file with a per-file reset — nine files carry one, the rest of the suite has none |

Implementation must re-run the probe on a checkout where slice A has landed (or from a cwd with a clean ancestor chain) so that the after-run covers the whole suite rather than stopping at `--maxfail`.
