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

## Changes

### `src/pmcp/tools/handlers.py` (modify)

- `_effective_server_config_for_notice` — **add** — new private helper returning `tuple[str, list[str]] | None` (command, args) for a server, applying the same `.mcp.json`-over-manifest precedence as `_load_all_configured_servers` and falling back to manifest → discovered exactly as `_get_server_config_for_update` does today. Returns `None` when nothing resolves, when the effective config is remote (no `command`), or when the command is empty. Deliberately performs **no** policy/credential/header gating — a notice is best-effort and must fail silent, not emit a lifecycle failure.
- `_run_stale_index` (call site around `handlers.py:1350`) — **modify** — replace `self._get_server_config_for_update(server_name)` + `.command`/`.args` access with the new helper; keep the existing `continue`-on-`None` behavior.
- `_get_update_warning` (call site around `handlers.py:3622`) — **modify** — same replacement; keep the existing `return None`-on-`None` behavior.
- `_get_server_config_for_update` — **keep, unmodified** — still used by `update_server`'s own manifest-oriented lookups; not deleted, and its docstring gains one line noting notice paths use the effective-config helper instead.
- `update_server` (around `handlers.py:5059`) — **modify** — after resolving `resolved_config`, pass it to the restart rather than letting `restart_server` re-resolve (see next item), closing the #151 TOCTOU window.
- `restart_server` (`handlers.py:2908`) — **modify** — accept an optional pre-resolved config so a caller that already resolved can supply it. Signature stays `async def restart_server(self, input_data: dict[str, Any])` for the public tool contract; add a keyword-only `resolved_config: ResolvedServerConfig | None = None` parameter used in place of the internal `_resolve_lifecycle_config` call when supplied. When `None`, behavior is byte-identical to today.

### `tests/test_tools.py` (modify)

- `TestUpdateServerVersionRepair` (or a sibling class if cleaner) — **add** — `test_notice_paths_use_configured_override_package`: manifest names package A, `.mcp.json` names package B for the same server; assert the version lookup driven by `_get_update_warning` targets **B**, not A.
- **add** — `test_stale_sweep_uses_configured_override_package`: same fixture, driving `_run_stale_index`; assert the `_stale_check_cache` entry was computed from B.
- **add** — `test_notice_paths_stay_silent_for_policy_denied_server`: a policy-denied server must produce no notice and must not raise — pins the deliberate no-gating design so a later "just call `_resolve_lifecycle_config`" refactor fails loudly.
- **add** — `test_notice_paths_ignore_remote_override`: a remote (URL-based) override yields no notice rather than an attribute error.
- **add** — `test_update_server_restart_uses_the_probed_config`: assert `restart_server` receives the caller's already-resolved config — i.e. resolution happens once. Pins #151.

## Documentation impact

- `CHANGELOG.md` — **modify** — add entries under `## [Unreleased]` for both fixes. **Mandatory**: `main` is protected and the required `changelog` check fails any PR touching `src/` without a `CHANGELOG.md` change.

No other cross-cutting doc applies: `README.md` documents `gateway.update_server`'s user-facing contract, which is unchanged (no new parameter, no changed output shape — `restart_server`'s new argument is internal, keyword-only, and defaulted).

## Frozen vocabulary / protocol check

`update_server`'s public tool schema (`get_gateway_tool_definitions`, around `handlers.py:613`) and `UpdateServerOutput`/`RestartServerInput` in `types.py` are the protocol surface. **No new vocabulary is introduced**: the `resolved_config` parameter is a Python keyword-only argument on an internal method, never a JSON-RPC field, and no output field is added, removed, or renamed.

## Dependencies & order

1. Add `_effective_server_config_for_notice` first — both #150 call-site changes depend on it.
2. Migrate the two notice call sites.
3. Then #151: thread the resolved config through `restart_server`. Independent of #150; ordered second only so a RED check for #150 is not entangled with a signature change.
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

- [ ] `_get_update_warning` and `_run_stale_index` both resolve a server's package through the `.mcp.json`-over-manifest precedence — proven by `uv run pytest tests/test_tools.py -q -k 'notice or stale_sweep'` passing, and failing when the src change alone is reverted.
- [ ] A policy-denied or remote-override server yields no notice and raises nothing — proven by `test_notice_paths_stay_silent_for_policy_denied_server` and `test_notice_paths_ignore_remote_override`.
- [ ] `update_server` resolves the server config exactly once; the restart consumes that same object — proven by `test_update_server_restart_uses_the_probed_config` passing, and failing when the #151 change alone is reverted.
- [ ] Full suite is 2536 + new tests passed / 3 skipped / 0 failed, with `ruff check`, `ruff format --check src/ tests/`, and `mypy src` all clean.
- [ ] `CHANGELOG.md` has entries under `## [Unreleased]` for both issues, so the required `changelog` check passes on the PR.

## Execution Policy

- execute: effort=medium, reason=two small resolution changes in a subsystem with a documented history of silent-misreport regressions; mechanical edits but the failure mode is subtle and the no-gating design decision must be preserved.

## Automation

```yaml
automation:
  suite_command: "uv run pytest -q"
```
