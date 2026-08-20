# Detailed plan: close the update_server TOCTOU window

## Task

Fix Consiliency/pmcp#151. `gateway.update_server` resolves the target server's config, runs an update probe with a **60-second** timeout, and then calls `self.restart_server(...)`, which resolves the config **again**. A config edit landing inside that window means pmcp can probe and install package A, restart onto package B, and persist A's version bookkeeping for B.

Severity rose with 2.2.0. #159 removed the automatic update notices, so `gateway.update_server` is now the **sole** update path — there is no second mechanism around a config-resolution race here.

## Research summary

Verified against source at `c326a4c` (post-2.2.0). Line numbers below were read in this session; #159 shifted everything in `handlers.py` by ~193 lines, so the numbers in the issue text are stale.

**The two reads.**

| # | Site | What it feeds |
|---|---|---|
| 1 | `update_server` → `_resolve_lifecycle_config` (`handlers.py:4938`) | the probe command/args/env, and the version bookkeeping persisted afterwards |
| 2 | `restart_server` → `_resolve_lifecycle_config` (`handlers.py:2827`), reached from `update_server` at `5058` | what actually gets restarted |

Between them: `await self._run_update_probe_command(...)` (`5018`), bounded at 60s.

**Correction to the issue's own fix sketch — verified, not assumed.** The issue says threading the resolved config through to the restart "would mean either duplicating [policy checks, credential-gap detection, and remote-header validation] or accepting their loss." That is **wrong**. All three checks live inside `_resolve_lifecycle_config` itself (`handlers.py:3237-3295`): policy at `3238`, remote-header env vars at `3253`, configured-duplicate credential gap at `3268`. Read #1 already performs every one of them.

What `restart_server` adds *beyond* the resolver is only: input parsing (`force`), `prior_status`, active-task accounting before/after, the `_client_manager.restart_server` call, and `_lifecycle_output` shaping. That is why the helper extraction below is cheap and loses nothing.

**The compare is not vacuous — checked because it would have invalidated the design.** Neither `load_configs` (`config/loader.py:754`) nor `load_manifest` (`manifest/loader.py:754`) is `lru_cache`/`@cache`-decorated, and neither module holds a `_cache`/`_memo` at module level. Both re-read from disk on every call. Consequence: resolve#2 genuinely observes a mid-probe edit, and **the race is reachable through both the `.mcp.json` branch and the manifest branch** — not just the former.

**Blast radius of the extraction.** `restart_server` has exactly two callers: the tool dispatch at `server.py:350` and `update_server` at `handlers.py:5058`. Nothing in the CLI calls it. Carving out a private helper cannot affect anything else.

**`ResolvedServerConfig`** (`types.py:319`) is a plain pydantic `BaseModel` with `name`, `source`, `config` — so `==` is structural equality, no custom comparator needed.

## The contract this fix establishes

> The config restarted onto is the config that was probed.

It is explicitly **not** "the config on disk at the instant the restart completes." An edit landing after the comparison applies on the *next* update — that is correct behaviour, not a residual race. State this in the PR so a reviewer does not read the remaining (unavoidable) gap as an incomplete fix.

## Changes

### `src/pmcp/tools/handlers.py` (modify)

- `_restart_resolved_server` — **add** — private helper holding the post-resolve body of `restart_server`: active-task accounting, the `_client_manager.restart_server(config, force=...)` call, and `_lifecycle_output` shaping. Signature `(self, server_name: str, config: ResolvedServerConfig, *, force: bool, prior_status: str) -> LifecycleServerOutput`.
- `restart_server` — **modify** — becomes resolve + delegate to the helper. **Behaviour must be byte-identical**; this is a pure extraction and any observable change is a bug in the refactor.
**Board correction — the check must precede `refresh()`, not merely precede the restart.** My first implementation placed the guard immediately before the restart, which two reviewers independently blocked. Between the probe and the restart, `update_server` calls `await self.refresh(...)` — and `refresh()` is itself a **diff-based config reconcile**: it re-reads the config and disconnects/reconnects any server whose definition changed. So a mid-probe edit could cause `refresh()` to **activate the freshly-fetched package on its own**, after which the guard would report "fetched but NOT activated" — a false statement.

Worse, it defeats the test I had planned. `refresh()` activates through `disconnect_server` + `connect_all`, **not** `restart_server`, so the planned assertion "`_client_manager.restart_server` was never called" would have passed while the bug survived. That is the exact failure mode this plan's test section warns about, and I walked into it.

The guard therefore sits immediately after the probe's success check and **before** `refresh()`. The tests must assert the target's actual connect/disconnect state, not just that `restart_server` was skipped.

- `update_server` — **modify** — after the probe returns:
  1. **Recompute `prior_status`.** The pre-probe value is up to 60 seconds stale. Use the fresh value for resolve#2 and the helper.
  2. Resolve #2 via `_resolve_lifecycle_config`. On failure, return `UpdateServerOutput(ok=False, ...)` exactly as the pre-probe failure path does. This also covers *server deleted from config mid-probe* for free.
  3. Compare resolve#2 against resolve#1 using the **existing `_refresh_config_unchanged(old, new)`** (`handlers.py:177`).

     **Correction to this plan, found while implementing — the original said "strict full-model `==`, including `source`", and that was wrong.** `handlers.py` already carries a battle-tested predicate for exactly this question ("do these two describe the same downstream process?"), and it deliberately does *not* use full-model equality. Its docstring records why, and both reasons apply here:

     * **`env` is compared by *effective* override only** — entries that genuinely differ from `os.environ`. Naive full-`env` comparison "would spuriously tear down running provisioned servers on every refresh (issue #79)". A full-model `==` in `update_server` would reintroduce that class as **spurious refusals of legitimate updates**.
     * **`source` is excluded deliberately** — the same effective server can present as `manifest` or as a configured source. My original plan called over-refusal on a source flip "rare and fails closed". That was a judgement made without reading this function; the codebase had already decided the question, with a linked issue.

     Reusing it also keeps **one** definition of "same server" instead of a second, subtly different one. `update_server` only reaches the comparison for local servers (remote returns early — no local package to update), so only the `LocalMcpServerConfig` branch is exercised; the header-rotation logic is inert here and needs no arguments.
  4. On mismatch — refuse. Return `ok=False` with a message saying the configuration changed during the update and the package was **fetched but not activated**, and do **not** restart, do **not** persist any version bookkeeping.
  5. On match — call `_restart_resolved_server` with **resolve#2's config object**. Since it is equal to #1 the choice is immaterial by construction; using the compared object is what makes the window exactly zero rather than one event-loop tick.
- The comment block at `4924-4936` — **rewrite**. It currently asserts *"Resolving once through restart_server's own resolver makes that divergence impossible: both calls are the same lookup against the same inputs, so they can't disagree."* The premise "same inputs" is exactly what a mid-probe edit breaks. The issue names this comment specifically.
- The comment at `5055` referencing "the same resolve+disconnect+connect machinery gateway.restart_server" — **review and correct** if it still implies a single resolve.

**Sweep for the same claim elsewhere.** Grep comments, docstrings **and exported tool descriptions** for any surviving "resolved once" / "cannot disagree" wording. This is the exact gap class codex caught on #159: a symbol grep does not see prose, and `get_gateway_tool_definitions()` output ships to every client.

### `tests/test_tools.py` (modify)

- **add** — `test_update_server_refuses_when_config_changes_during_probe`. Monkeypatch `_run_update_probe_command` to **edit the config on disk** and then return success, exercising the real loader path (which also smokes out any caching that would make the compare vacuous). Assert **observables**, not internals:
  - `_client_manager.restart_server` was **never called**
  - `ok is False` and the message names the configuration change
  - **the descriptions-cache version was not persisted** — this is the assertion that matters, because the bug class is "persist A's bookkeeping for B". A test that only checks `ok is False` would pass against a fix that still writes the wrong version.
- **add** — `test_update_server_restarts_when_config_is_unchanged`. The happy path is byte-identical to today: restart happens, version persists, output fields unchanged.
- **add** — `test_update_server_refuses_when_server_removed_during_probe`. Resolve#2 fails outright rather than differing.
- Existing `update_server` and `restart_server` tests — **keep unchanged and passing.** The extraction is behaviour-preserving; if any existing test needs editing, that is evidence the refactor changed behaviour and must be re-examined rather than accommodated.

**Prove the mismatch test RED against `c326a4c` before the fix lands.** Several tests in this series have passed while their bug survived, by asserting a mutated argument rather than an observable outcome. A test that cannot fail is worse than no test.

### `CHANGELOG.md` (modify)

`### Fixed` under `## [Unreleased]`. **Mandatory** — `main` is protected and the `changelog` check fails any PR touching `src/`. Note the raised severity: `update_server` is the only update path as of 2.2.0.

## Documentation impact

`README.md` — none expected; it documents `gateway.update_server`'s behaviour, which is unchanged on the happy path. Verify with a grep for update/restart wording rather than asserting it; a new refusal mode is user-visible if the README enumerates failure cases.

## Frozen vocabulary / protocol check

**No protocol change.** No new field on any output model — the refusal uses the existing `ok` and `message`. `RestartServerInput` is untouched; the new parameter is on a *private* method, not the tool schema. This matters: 2.2.0 just removed four public fields, and this fix must not add any back.

## Dependencies & order

1. Extract `_restart_resolved_server` and re-point `restart_server`. Full suite must pass **before** any behaviour change — that proves the extraction is inert.
2. Add resolve#2 + compare + refusal in `update_server`.
3. Rewrite the false comments; sweep for the same claim in prose and tool descriptions.
4. Tests, then CHANGELOG.

No new dependency. No migration.

## Verification

```bash
cd /mnt/workspace/worktrees/pmcp-151-toctou
uv run pytest tests/test_tools.py -q -k 'update_server or restart_server'
uv run pytest -q          # 2548 baseline + 3 new; 0 failed
uv run ruff check . && uv run ruff format --check src/ tests/ && uv run mypy src

# The comment/prose sweep the identifier grep cannot do:
grep -rn 'resolved once\|resolve once\|cannot disagree\|can.t disagree\|same inputs' src/
```

Edge cases: server removed from config mid-probe (covered); a remote server (no local package — `update_server` returns early, so resolve#2 is never reached); `force=True` interaction with the fresh `prior_status`; a probe that times out (returns before resolve#2, so no comparison happens — confirm that path is unchanged).

## Acceptance criteria

- [ ] A config edit during the probe window causes a refusal, proven by `test_update_server_refuses_when_config_changes_during_probe` asserting the restart never happened **and** no version was persisted — and proven RED against `c326a4c`.
- [ ] The unchanged-config path is behaviour-identical, proven by the existing `update_server`/`restart_server` tests passing **without modification**.
- [ ] No comment, docstring, or exported tool description still claims the config is resolved once or cannot disagree — proven by the prose sweep returning nothing.
- [ ] Full suite passes, 0 failures; ruff, format, mypy clean; `### Fixed` CHANGELOG entry present.

## Non-goals

- **Locking the config file.** Out of scope and wrong shape: pmcp does not own `.mcp.json`, and the operator's editor is not going to honour an advisory lock.
- **Making the restart atomic with respect to disk.** Impossible without owning the file. The contract stated above is the achievable guarantee.
- **Changing `restart_server`'s public behaviour.** The extraction is inert by construction.

## Execution Policy

- execute: effort=medium, reason=small diff, but it refactors a lifecycle path with two callers and adds a refusal branch to the only remaining update path; a behaviour change smuggled in by the extraction would be invisible to the new tests.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```
