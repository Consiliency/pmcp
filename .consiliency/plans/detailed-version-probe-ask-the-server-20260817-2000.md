# Detailed plan: ask the server its own version

## Task

Fix Consiliency/pmcp#150 — the gateway fabricates "update available" notices, or hides real ones — by asking each server to report its own version, using the identical command pmcp uses to launch it.

Eighth attempt. Maintainer decision after the spawn-provenance plan was rejected: **ask the process**, accepting a subprocess per check.

## Research summary

**Why the previous seven failed, at one joint.** pmcp cannot observe *which package artifact a running server is executing*. Not from `descriptions_cache.version` (an upstream snapshot), not from `serverInfo.version` (an *implementation* version — FastMCP 1.x reports the SDK's, mcp 2.x defaults to `""`), not from the npx/uv runner caches (no server→entry binding; verified one package cached at 1.2.0, 1.2.1 and 1.2.2 concurrently), and not from spawn argv — verified independently that a live `npx -y @eslint/mcp` tree exposes **no** `_npx` path in any descendant's `cmdline`, `cwd` or fds, because npm resolves *after* `create_subprocess_exec` returns.

Running the package's own `--version` is the only method that observes the artifact that actually runs, because the artifact answers for itself.

**Measured coverage — 12 npm servers sampled from the manifest, real invocations:**

```
OK   @aashari/mcp-server-atlassian-confluence -> 3.3.0          (3s)
OK   @aashari/mcp-server-atlassian-jira       -> 3.3.0          (4s)
OK   @apify/actors-mcp-server                 -> 0.14.3         (7s)
OK   @azure-devops/mcp                        -> 2.9.0          (5s)
OK   @browserbasehq/mcp-server-browserbase    -> Version 2.4.3  (14s)
OK   @dynatrace-oss/dynatrace-mcp-server      -> 2.1.2          (1s)
none @azure/mcp, @benborla29/mcp-server-mysql, @brightdata/mcp,
     @cloudflare/mcp-server-cloudflare, @elastic/mcp-server-elasticsearch
none @circleci/mcp-server-circleci            -> HIT THE 90s CAP
---- 6/12 parsed a version (50%)
```

**Three findings that shape the design, all measured rather than assumed:**

1. **50% coverage.** Half the servers yield nothing. Those must produce *no notice* — which is still strictly better than today, where any server can receive a fabricated one.

2. **A server can ignore `--version` and just run.** `@circleci/mcp-server-circleci` hit the 90s cap; `@eslint/mcp` printed *"ESLint MCP server is running"* and **exited 0**. So exit status is not a usability signal, and an unbounded probe would hang a sweep. A timeout is load-bearing.

3. **Loose parsing would fabricate versions.** `firecrawl-mcp --version` emits only npm deprecation noise containing `koa-router@14.0.0` — exactly what a permissive regex grabs. Verified that strict whole-line matching (optionally `Version `-prefixed, optionally `v`-prefixed) extracts `4.0.2` and `0.0.79` while rejecting both the koa-router string and the "server is running" line.

**Cost model, from the measured latencies.** Median 5s, max 90s. A 20s timeout costs ~82s for the 12 sampled and truncates one. Extrapolated over all 98 npm+pypi servers at the 4s median, a serial pass is **~390s**. So bounded concurrency and per-server caching are *required*, not optimisations.

**Prerequisite already landed.** #155 (merged, `58b1a02`) made `is_version_newer` fail closed, so an unparseable version yields silence rather than a fabricated notice.

## Changes

### `src/pmcp/manifest/version_probe.py` (create)

Separate module: this is subprocess execution, distinct from `version_checker.py`'s registry lookups.

- `_VERSION_LINE_RE` — **add** — `^(?:[Vv]ersion\s+)?v?(\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?)\s*$`, matched **whole-line** against each output line. Anchoring is the load-bearing part: it is what rejects `koa-router@14.0.0`.
- `parse_version_output(text)` — **add** — first line matching the anchored pattern, else `None`. Pure function, trivially testable against the real outputs captured above.
- `probe_server_version(command, args, *, cwd, env, timeout)` — **add** — run `<command> <args...> --version` with the **same argv, cwd and sanitised env** pmcp uses to launch the server, capture stdout+stderr, kill the process group on timeout (servers that ignore the flag keep running), and return `parse_version_output(...)`. Returns `None` on timeout, non-zero exit, or unparseable output. Never raises.
- `_PROBE_TIMEOUT_S = 20` — **add** — from the cost model: covers 11 of 12 sampled, truncates only the server that ignores the flag entirely.
- Result cache keyed by `(command, tuple(args))` with a TTL — **add** — a probe costs a process, so it must not run per-request. Cache `None` results too, or an unsupported server is re-probed every sweep forever.

### `src/pmcp/tools/handlers.py` (modify)

- `_get_update_warning` — **modify** — take `current` from `probe_server_version(...)` using the **effective** config (`_effective_notice_target`, which honours `.mcp.json` over the manifest). **No fallback** to `descriptions_cache.version`; when the probe yields nothing, return `None`.
- `_run_stale_index` — **modify** — same source, same no-fallback rule; this is where probes are actually paid for, on the existing hourly loop rather than on request paths.
- **`latest` must come from the same effective config** — **modify** — the board's B2 finding on the last plan: both notice writers still took `latest` via `_get_server_config_for_update`, which prefers the manifest and ignores the override, so B's installed version could still be compared against A's latest. Both sides now derive from one resolution.
- Bounded concurrency in the sweep — **add** — an `asyncio.Semaphore` (start at 4) so a pass over 98 servers does not spawn 98 processes. With the measured 4s median this puts a full pass near ~100s of wall time, off the request path.
- `_stale_check_cache` — **modify** — record `version_source="probe"` beside the existing `pkg_type`, so an observed version can never be confused with an inferred one.

### `tests/test_version_probe.py` (create)

- **add** — `test_parses_bare_version` / `test_parses_version_prefixed`: `4.0.2` and `Version 0.0.79`, the two real shapes observed.
- **add** — `test_rejects_npm_deprecation_noise`: the **verbatim** firecrawl output containing `koa-router@14.0.0` → `None`. Pins the fabrication case.
- **add** — `test_rejects_server_started_message`: the verbatim `@eslint/mcp` line → `None`, **and** notes it exited 0, so exit status must not be treated as success.
- **add** — `test_timeout_returns_none_and_kills_process_group`: a fake command that ignores the flag and sleeps → `None` within the bound, no orphan left. Pins the circleci behaviour.
- **add** — `test_probe_uses_the_same_argv_and_env`: assert the spawn call receives the caller's exact command/args/cwd/env. If the probe ran a *different* command than the server, it would measure the wrong artifact — the failure of attempts 4-7.
- **add** — `test_none_results_are_cached`: an unsupported server is probed once, not every sweep.

### `tests/test_tools.py` (modify)

Every test asserts an **observable notice outcome**, never a mutated field — several prior tests passed while their bug survived.

- **add** — `test_notice_uses_probed_version`: probe returns `1.0.0`, upstream `2.0.0` → notice naming both.
- **add** — `test_no_notice_when_probe_yields_nothing`: probe returns `None` **while an orderable `descriptions_cache.version` exists** → still no notice. The orderable-fallback case is what the last two plans' tests omitted.
- **add** — `test_probe_and_latest_use_the_same_effective_config` (**pins B2**): manifest names A, `.mcp.json` names B → the probe runs B's argv **and** `latest` is fetched for B. Fails if either side reverts to the manifest.
- **add** — `test_sweep_bounds_probe_concurrency`: N servers, semaphore of 4 → never more than 4 concurrent probes.

## Documentation impact

- `CHANGELOG.md` — **modify** — entry under `## [Unreleased]`. **Mandatory**: `main` is protected and the `changelog` check fails any PR touching `src/` without one. Must state that notices now compare the version the server reports for itself, and that a server which does not report one produces no notice — an explicit, honest coverage reduction.
- `README.md` — **check** whether update-notice behaviour is documented; update if so, else record `no doc footprint`.

## Frozen vocabulary / protocol check

No protocol surface changes. `version_probe` is internal; `_stale_check_cache` is private in-memory state with no serialised form; `CatalogSearchOutput.stale_updates` keeps its type and message format. **No new vocabulary, no new tool parameter, no schema change.**

## Dependencies & order

1. `version_probe.py` + its tests first — pure and independently verifiable, changes no notice behaviour.
2. Migrate both notice readers together, including the `latest`-side fix. A partial migration leaves them disagreeing about what is being compared, which is the failure mode of rounds 1-3.
3. Concurrency bound in the sweep.
4. CHANGELOG last.

No new runtime dependency (stdlib `asyncio.create_subprocess_exec`).

## Verification

```bash
cd <worktree>
uv run pytest tests/test_version_probe.py -q
uv run pytest tests/test_tools.py -q -k 'notice or probe or stale'

# RED proof — required. Revert ONLY the src changes via a targeted
# `git diff > patch` + `git apply -R`. NEVER `git checkout HEAD -- .`
# (it has destroyed uncommitted work in this repo).

uv run pytest -q          # baseline 2553 + new, 3 skipped, 0 failed
uv run ruff check . && uv run ruff format --check src/ tests/ && uv run mypy src
```

Then confirm against real servers, outside the suite — the whole point is matching reality:

```bash
uv run python -c "import asyncio; from pmcp.manifest.version_probe import probe_server_version; \
print(asyncio.run(probe_server_version('npx', ['-y','@upstash/context7-mcp'], cwd=None, env=None, timeout=20)))"
# expect 4.0.2 ; and @eslint/mcp must yield None, not a false positive
```

Edge cases: server ignores the flag and runs (circleci); exits 0 with no version (eslint); emits npm noise (firecrawl); a version appearing mid-line in prose; remote servers (no local command → no probe); lazy servers never started; probe binary missing.

## Acceptance criteria

- [ ] `parse_version_output` extracts `4.0.2` and `Version 0.0.79`, and returns `None` for the verbatim firecrawl and eslint outputs — proven by `test_version_probe.py`, and confirmed once by hand against the live packages.
- [ ] A server that ignores `--version` is bounded by the timeout, yields `None`, and leaves no orphan process — proven by `test_timeout_returns_none_and_kills_process_group`.
- [ ] No notice is emitted when the probe yields nothing, **even when an orderable `descriptions_cache.version` exists** — proven by `test_no_notice_when_probe_yields_nothing`, failing when the src change is reverted.
- [ ] Both the probed version and the upstream `latest` derive from the same effective config, so a `.mcp.json` override never produces a cross-package comparison — proven by `test_probe_and_latest_use_the_same_effective_config`.
- [ ] Full suite ≥2553 + new tests passed / 3 skipped / 0 failed; ruff, format, mypy clean; `## [Unreleased]` CHANGELOG entry present.

## Non-goals

- **Raising coverage above what servers report.** ~50% is the measured ceiling for this method. The other half produce no notice; that is the honest degradation and is not worked around.
- **Probing on the request path.** Probes run only on the background sweep, behind the cache. A `describe` call must never spawn a process.
- **docker / cargo.** No probe; `None` → no notice.

## Measured: the startup side effects are real, not hypothetical

Probing `@circleci/mcp-server-circleci --version` — one of the servers that ignores the flag — was measured while running:

```
t=3s   pids=7  open_sockets=5
t=6s   pids=6  open_sockets=4
t=9s   pids=5  open_sockets=4
t=12s  pids=5  open_sockets=4
```

**Seven processes and five open network sockets**, sustained until killed. That is a real MCP server starting up and connecting outward, caused by an advisory update check, on a schedule, for a server that yields no version anyway.

(By contrast `@eslint/mcp` had exited before the 10s sample — it prints its message and stops, so the cost there is bounded.)

This materially strengthens the case against the approach: the servers that pay this cost are exactly the ones that give nothing back. Any implementation must at minimum avoid probing a server twice once it is known not to answer — but that only bounds the cost, it does not remove the first probe's side effects, and a cache expiry re-incurs them.

## Open question for the board

The probe runs `<command> <args> --version`, i.e. **it starts the package**. For a server that ignores the flag this briefly runs a real server process that is then killed. Is that acceptable for something as trivial as an update notice — particularly for a server with side effects at startup (opening a browser, connecting to a database, writing state)? If not, the honest conclusion may be that no safe automatic probe exists and the feature should be removed rather than made 50% correct. I want the board's view, because this is the strongest argument against the whole approach and it is not one I can settle from the code.

## Execution Policy

- execute: effort=high, reason=eighth attempt at a defect class where seven prior fixes each shipped a new hole; introduces subprocess execution on a background loop, where the failure modes are hangs and orphaned processes rather than a wrong string.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```
