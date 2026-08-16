# Detailed plan: unify update_server config resolution across notice paths and the probe window

## Task

Close pmcp issues #150 and #151, the two residuals filed during the PR #147 board review.

**#150** — `gateway.update_server` resolves its target through `_resolve_lifecycle_config` (which gives `.mcp.json` precedence over the manifest), but the two *notice* paths still use the manifest-only `_get_server_config_for_update`: the background stale sweep (`handlers.py:1350`) and `_get_update_warning` (`handlers.py:3622`). Once the 6h stale-check TTL expires, an override naming a **different package** than the manifest entry compares unrelated packages — resurfacing a false "update available" notice or hiding a real one.

**#151** — `update_server` resolves config once for the probe (around `handlers.py:5059`) and then `restart_server` independently re-resolves (`handlers.py:2914`) after a probe with a 60s timeout. A config edit landing inside that window can probe package A and restart package B, then persist A's version for B — the same silent-misreport class #147 existed to eliminate.

## Research summary

Reconnaissance was done inline (files already in session from the #147 work) rather than via Explore teammates, per the skill's "skip when context is already in session" rule.

The load-bearing finding is that **`_resolve_lifecycle_config` is not a drop-in replacement for the notice paths.** Read at `handlers.py:3577+`, it returns `tuple[ResolvedServerConfig | None, LifecycleServerOutput | None]` and produces *failure outputs* for three conditions: policy denial (`is_server_allowed`), missing remote-header env vars (`_missing_remote_header_env_vars`), and a configured-duplicate credential gap (`_configured_duplicate_missing_credential`). Those are correct for a lifecycle **action**, but wrong for a best-effort **notice** — a policy-denied or credential-gapped server should simply produce no notice, not a suppressed one for an unrelated reason, and the notice paths have no channel for a `LifecycleServerOutput`.

The precedence itself comes from `_load_all_configured_servers` (`handlers.py`, immediately after `_resolve_lifecycle_config`), which is a thin wrapper over `load_configs(project_root=..., custom_config_path=...)` plus `filter_self_references`. That is the piece the notice paths actually need.

Both notice call sites consume only `.command` and `.args` (verified at `handlers.py:1350-1356` and `handlers.py:3622-3625`). `ResolvedServerConfig` (`types.py:319`) wraps a `McpServerConfig` union; the local arm `LocalMcpServerConfig` (`types.py:178`) carries `command`/`args`, while a remote arm carries neither — so a remote override must resolve to "no notice", matching today's behavior when `server_config.command` is falsy.

For #151, `restart_server` (`handlers.py:2908`) resolves and then calls `self._client_manager.restart_server(config, force=...)`. The `ClientManager` method takes an already-resolved config, so threading the caller's config through avoids the second resolution without duplicating the handler's policy/credential gating.

## Board review — three blocking defects found, plan revised

This plan was reviewed by the advisor panel (grok-4.6 AGREE, codex DISAGREE, gemini DISAGREE) before implementation. Two legs independently found the same policy-gating contradiction; codex found two further defects. **All three were verified against source and the plan below is the revised version.** Recorded here because the original design would have shipped an incomplete fix.

**B1 — the notice fix was half a fix (codex).** Changing only the *latest* lookup to the configured package leaves *current* as `GeneratedServerDescriptions.version`, which was generated from the **manifest** package (`refresher.py`). So manifest package A at `10.0.0` vs configured package B at `2.0.0` still compares unrelated packages and can hide B's real update. `GeneratedServerDescriptions` carries a `package` field (`types.py:1365`) that records which package the entry describes — the original plan never consulted it. A test asserting "B was queried" would have passed while the bug survived.

**B2 — the #151 fix could restart the wrong package (codex).** `update_server` calls `self.refresh(...)` *after* the probe (`handlers.py:5167`), which reloads config and applies an edit to B. Passing the stale probed A into the restart would then disconnect B and reconnect A — worse than the TOCTOU it fixes — and would bypass restart-time credential gating.

**B3 — the policy-silence criterion was unsatisfiable (codex + gemini).** `_load_all_configured_servers` returns entries *before* policy filtering (its own docstring says so). A helper doing no gating would return command/args for a denied server, so `test_notice_paths_stay_silent_for_policy_denied_server` could not pass as specified, and denied servers would leak notices.

## Changes

### `src/pmcp/tools/handlers.py` (modify)

- `_effective_notice_target` — **add** — new private helper returning `tuple[str, list[str]] | None` (command, args). Applies `.mcp.json`-over-manifest precedence via `_load_all_configured_servers`, falling back to manifest → discovered as `_get_server_config_for_update` does today. Returns `None` when: nothing resolves; the effective config is remote (no `command`); the command is empty; **or the server is not `self._policy_manager.is_server_allowed(...)`** — a pure predicate (`policy.py:104`), so this is the side-effect-free eligibility check B3 requires, without importing `_resolve_lifecycle_config`'s failure-output machinery. Credential/header gaps are deliberately *not* checked: they do not change which package is installed, and a notice for a credential-gapped server is still truthful.
- `_notice_package_matches_cache` — **add** — returns whether the effective config's detected package equals the `package` recorded on the descriptions-cache entry. Fixes **B1**: `_get_update_warning` and `_run_stale_index` must compare like with like. Uses the existing `detect_package_type` to derive the effective package name.
- `_run_stale_index` (call site around `handlers.py:1350`) — **modify** — resolve through `_effective_notice_target`; **skip the server entirely when the package identity does not match** the cache entry, rather than writing a cross-package `(current, latest)` pair. Keeps the existing `continue`-on-`None`.
- `_get_update_warning` (call site around `handlers.py:3622`) — **modify** — same resolution and the same identity guard; return `None` on mismatch (fail silent — a wrong notice is worse than none). Keeps the existing `return None`-on-`None`.
- `_get_server_config_for_update` — **keep, unmodified** — still used by `update_server`'s manifest-oriented lookups; docstring gains one line pointing notice paths at the new helper.
- `update_server` (around `handlers.py:5059`) — **modify** — fixes **B2**. After the probe and `refresh()`, **re-resolve** the config and compare it against the probed snapshot (command + args). If it changed, abort with `ok=False` and a message naming the drift, and mutate **no** cache or stale-check state. Only when unchanged, pass the (now confirmed-current) config to the restart. This closes #151's window by *detecting* the edit rather than by pinning a stale config through it.
- `restart_server` (`handlers.py:2908`) — **modify** — add a keyword-only `resolved_config: ResolvedServerConfig | None = None`, used in place of the internal `_resolve_lifecycle_config` call when supplied. `update_server` passes the re-resolved-and-confirmed config. When `None`, behavior is byte-identical to today. Note per gemini: this is safe *because* `update_server` performs the same gating first; the parameter must stay internal and never reach the public tool schema.

### `tests/test_tools.py` (modify)

Tests assert **observable outcomes** (was a notice emitted? what did the cache record?), never merely "package B was queried" — per the board, an argument-plumbing assertion passes while the underlying bug survives.

- `TestUpdateServerVersionRepair` (or a sibling class) — **add** — `test_notice_paths_use_configured_override_package`: manifest names A, `.mcp.json` names B, cache entry's `package` is B; assert `_get_update_warning` reports B's real update.
- **add** — `test_notice_suppressed_when_cache_package_mismatches_config` (**B1**): cache entry describes manifest package A at `10.0.0`; effective config is package B at `2.0.0`. Assert **no notice** is emitted and no `_stale_check_cache` entry pairs A's version with B's latest. Without the identity guard this produces a wrong notice — the exact hidden/false-notice failure #150 describes.
- **add** — `test_stale_sweep_skips_package_identity_mismatch`: same fixture driving `_run_stale_index`; assert the server is skipped rather than cached with a cross-package pair.
- **add** — `test_notice_paths_stay_silent_for_policy_denied_server` (**B3**): a policy-denied server present in `.mcp.json` yields no notice and raises nothing. Pins the `is_server_allowed` check; without it the helper returns command/args and the notice leaks.
- **add** — `test_notice_paths_ignore_remote_override`: a remote (URL-based) override yields no notice rather than an `AttributeError`.
- **add** — `test_update_server_aborts_when_config_changes_during_probe` (**B2**): resolve A, then mutate the config to B before the restart. Assert `ok is False`, the message names the drift, and **no** cache/stale-check mutation occurred. Without the re-resolve-and-compare, the old code restarts A over B's config.
- **add** — `test_update_server_restart_consumes_the_confirmed_config`: unchanged-config happy path — assert `restart_server` receives the caller's config and resolution is not repeated.

## Documentation impact

- `CHANGELOG.md` — **modify** — add entries under `## [Unreleased]` for both fixes. **Mandatory**: `main` is protected and the required `changelog` check fails any PR touching `src/` without a `CHANGELOG.md` change.

No other cross-cutting doc applies: `README.md` documents `gateway.update_server`'s user-facing contract, which is unchanged (no new parameter, no changed output shape — `restart_server`'s new argument is internal, keyword-only, and defaulted).

## Frozen vocabulary / protocol check

`update_server`'s public tool schema (`get_gateway_tool_definitions`, around `handlers.py:613`) and `UpdateServerOutput`/`RestartServerInput` in `types.py` are the protocol surface. **No new vocabulary is introduced**: the `resolved_config` parameter is a Python keyword-only argument on an internal method, never a JSON-RPC field, and no output field is added, removed, or renamed.

## Dependencies & order

1. Add `_effective_notice_target` (with the `is_server_allowed` gate) and `_notice_package_matches_cache` first — both #150 call-site changes depend on them.
2. Migrate the two notice call sites, including the identity guard.
3. Then #151: re-resolve-and-compare in `update_server`, plus the optional `resolved_config` parameter on `restart_server`. Independent of #150; ordered second so a RED check for #150 is not entangled with a signature change.
4. CHANGELOG last, so the entry describes what actually landed.

No external/blocking dependencies — no migrations, no config changes, no upstream coordination.

## Verification

```bash
cd /home/viperjuice/code/pmcp

# Targeted first (fast loop)
uv run pytest tests/test_tools.py -q -k 'notice or stale_sweep or update_server or restart'

# RED proof — required by this repo's standard; a passing test is not evidence
# the test works. Revert ONLY the src change, keep tests, observe failures,
# restore. Do NOT use `git checkout HEAD -- .` (it has destroyed uncommitted
# work in this repo before); revert with a targeted `git diff > patch` +
# `git apply -R` on src/pmcp/tools/handlers.py only.
uv run pytest tests/test_tools.py -q -k 'notice or stale_sweep'      # expect RED without the #150 fix
uv run pytest tests/test_tools.py -q -k 'restart_uses_the_probed'    # expect RED without the #151 fix

# Full gates
uv run pytest -q                       # expect 2536 + new tests passed, 3 skipped, 0 failed
uv run ruff check .
uv run ruff format --check src/ tests/
uv run mypy src
```

Edge cases to exercise: server present in manifest only (unchanged behavior); present in `.mcp.json` only; present in both with the *same* package (must stay silent — this is the common case and must not regress); remote override; policy-denied server; server absent everywhere.

## Acceptance criteria

- [ ] `_get_update_warning` and `_run_stale_index` resolve a server's package through the `.mcp.json`-over-manifest precedence — proven by `uv run pytest tests/test_tools.py -q -k 'notice or stale_sweep'` passing, and failing when the src change alone is reverted.
- [ ] A notice is never emitted from a cross-package comparison: when the descriptions-cache entry's `package` differs from the effective config's package, both notice paths stay silent — proven by `test_notice_suppressed_when_cache_package_mismatches_config` and `test_stale_sweep_skips_package_identity_mismatch`.
- [ ] A policy-denied or remote-override server yields no notice and raises nothing — proven by `test_notice_paths_stay_silent_for_policy_denied_server` and `test_notice_paths_ignore_remote_override`.
- [ ] A config edit landing between the probe and the restart aborts the update with `ok=False` and mutates no cache state — proven by `test_update_server_aborts_when_config_changes_during_probe`, failing when the #151 change alone is reverted.
- [ ] Full suite is 2536 + new tests passed / 3 skipped / 0 failed, with `ruff check`, `ruff format --check src/ tests/`, and `mypy src` all clean.
- [ ] `CHANGELOG.md` has entries under `## [Unreleased]` for both issues, so the required `changelog` check passes on the PR.

## Execution Policy

- execute: effort=medium, reason=two small resolution changes in a subsystem with a documented history of silent-misreport regressions; mechanical edits but the failure mode is subtle and the no-gating design decision must be preserved.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```
