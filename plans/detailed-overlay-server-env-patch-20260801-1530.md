# Detailed plan: per-host overlay override of a shipped server's environment

> **Plan Mode was NOT active when this was produced.** Planning artifact only — no implementation has begun.

## Task

Let a private overlay point a **shipped** manifest server at a non-default endpoint (e.g. a self-hosted Firecrawl on another host) **without redeclaring the whole server entry**. Part 2 of Consiliency/pmcp#105; part 1 (`extra_env`) is PR #108.

## Research summary

`extra_env` (PR #108) makes a second, non-secret variable declarable on a `ServerConfig` and applies it in `build_install_child_env` (`src/pmcp/manifest/installer.py`, in the `own_env` block around line 521). That closes the *declaration* gap but not the *per-host* gap.

The blocker is the overlay merge in `load_manifest` — `servers.update(overlay_servers)` (`src/pmcp/manifest/loader.py:564`). Overlay entries are parsed by `_parse_server_config` into complete `ServerConfig` objects and replace the shipped entry by name. To set one URL, an operator must restate `command`, `args`, the four-platform `install` block, `env_var`, `requires_api_key`, and everything else.

**This was a deliberate decision, not an oversight.** `plans/detailed-private-manifest-overlay-20260629-0841.md:23` states: *"an overlay entry with the same name as a shipped/earlier entry **replaces** it (whole-entry replace, not deep-merge) — simplest, predictable."* Any change here must justify revisiting it.

The gap that decision did not anticipate is **silent drift**: a redeclared entry is a frozen copy. When the shipped entry later changes — a package rename, an install-command fix, a new `secret_key` namespace — the overlay keeps overriding with the stale copy and the operator gets no signal. The existing override warning (`loader.py`, in the pre-merge loop around line 560) fires on *every* override, so it cannot distinguish "intentional full replacement" from "stale copy shadowing an upstream fix."

Existing overlay behavior is covered by 8 tests in `tests/test_manifest_overlay.py`, including precedence ordering, fail-soft parsing, symlink containment, and `test_explicit_path_skips_overlays`. All must keep passing unchanged — this plan adds a mechanism, it does not alter `servers:` semantics.

## Options considered

| # | Option | Verdict |
|---|---|---|
| A | Deep-merge all fields in `servers:` | **Rejected.** Directly reverses the documented decision and silently changes behavior for existing overlay users: an entry that today fully replaces would start inheriting shipped fields. Also introduces an unanswerable question — how do you *unset* an inherited field? |
| B | New `server_env:` overlay section that patches only `extra_env` on an existing server | **Recommended.** Additive, leaves `servers:` semantics untouched, solves the motivating case exactly, and no existing overlay changes meaning. |
| C | `${VAR}` interpolation inside shipped `extra_env` values | **Rejected.** Still requires the variable in the gateway's own environment — precisely the process-global workaround #105 exists to remove. Solves discoverability, not the actual problem. |
| D | Opt-in `merge: true` marker on a `servers:` entry | **Deferred.** Preserves the default and is more general than B, but it is a broader contract (per-field merge semantics for ~25 fields, including how to unset). If a second patch use case appears, revisit — B does not foreclose it. |

Option B is the bounded change: one new overlay key, one allowed field, no change to any existing path.

## Changes

### `src/pmcp/manifest/loader.py` (modify)

- `_load_overlay_file` — modify — also parse a top-level `server_env` mapping, returning it as a third element `dict[str, dict[str, str]]` (server name → env patch). Reuse `_parse_extra_env` for each value so coercion and fail-soft warnings match `extra_env` exactly. Keep the existing per-entry try/except shape: one bad patch is skipped, siblings survive.
- `load_manifest` — modify — after the existing `servers.update(overlay_servers)` for each overlay source, apply that source's `server_env` patches. For each `(name, patch)`: if `name` is absent from `servers`, `logger.warning` naming the file and the unknown server, then skip — a patch must never conjure a server. Otherwise replace that entry with `dataclasses.replace(servers[name], extra_env={**servers[name].extra_env, **patch})`. Patching is per-key over the existing `extra_env`, so a patch that sets one variable leaves the others intact.
- Apply patches **after** the source's own `servers.update`, and within each overlay source in precedence order, so a later source can patch an entry an earlier source replaced.

`dataclasses.replace` is used rather than mutating in place because `ServerConfig` instances from the shipped parse are shared with nothing else in the process, but mutation would make the merge order-dependent in a way that is hard to test; `replace` keeps each step a pure rebind.

### `tests/test_manifest_overlay.py` (modify)

Add cases alongside the existing 8:

- `test_server_env_patches_shipped_server` — overlay supplies only `server_env: {firecrawl: {FIRECRAWL_API_URL: ...}}`; assert the shipped `command`/`args`/`env_var` survive untouched and `extra_env` carries the new value.
- `test_server_env_merges_with_shipped_extra_env` — shipped entry already has an `extra_env` key; assert the patch adds to it rather than replacing the mapping wholesale.
- `test_server_env_unknown_server_warns_and_skips` — patch names a server that does not exist; assert no entry is created and a warning naming the file is logged.
- `test_server_env_malformed_is_skipped` — non-mapping `server_env`, and a mapping whose value is a scalar; assert the rest of the overlay still loads.
- `test_server_env_precedence_across_sources` — user overlay patches a key, project overlay patches a different key on the same server; assert both survive and a same-key collision resolves to the higher-precedence source.
- `test_explicit_path_skips_server_env` — mirrors the existing `test_explicit_path_skips_overlays` guarantee.

### `src/pmcp/manifest/manifest.yaml` (modify)

- `firecrawl` — add — `extra_env: {FIRECRAWL_API_URL: "https://api.firecrawl.dev"}`, making the default endpoint explicit and giving `server_env` a documented key to patch.

**Verify before committing this one.** `firecrawl-mcp` currently defaults to the SaaS endpoint when the variable is unset; setting it explicitly must be confirmed a no-op for SaaS users (check the package's own default rather than assuming this URL string). If it cannot be confirmed, drop this change — the mechanism works without it, and a wrong default would break every SaaS Firecrawl user.

## Documentation impact

- `CHANGELOG.md` — modify — under `## [Unreleased]` / `### Added`: overlay `server_env` patches a shipped server's `extra_env` without redeclaring the entry. Pair with the `extra_env` entry from #108.
- `plans/detailed-private-manifest-overlay-20260629-0841.md` — modify — append a short note under the whole-entry-replace decision recording that `server_env` was added as a narrow exception and that `servers:` semantics are unchanged, so the next reader of that decision finds the amendment rather than believing replace is still the whole story.
- `README.md` — none. Overlay files are not documented there today; adding a first overlay reference is its own change.

## Dependencies & order

1. **#108 (`extra_env`) must merge first** — `server_env` patches a field that does not exist without it.
2. Loader change before tests can pass, but write the tests first — the unknown-server and malformed cases are the ones most likely to be missed.
3. The `manifest.yaml` firecrawl default is independent and last; it is droppable without affecting the rest.

## Verification

```bash
# Targeted
uv run pytest tests/test_manifest_overlay.py -q          # 8 existing + 6 new
uv run pytest tests/test_manifest.py -q                  # extra_env parsing unchanged

# Regression — overlay touches the loader every consumer shares
uv run pytest tests/ -q                                  # expect 2259+ passed

# Gates
uv run ruff check src/ tests/
uv run ruff format --check src/pmcp/manifest/ tests/
uv run mypy src/pmcp/manifest/loader.py
```

End-to-end check with a real overlay, which no unit test covers:

```bash
mkdir -p ~/.pmcp && cat > ~/.pmcp/manifest.yaml <<'YAML'
server_env:
  firecrawl:
    FIRECRAWL_API_URL: "http://100.84.171.76:3002"
YAML
uv run python -c "
from pmcp.manifest.loader import load_manifest
s = load_manifest().servers['firecrawl']
print(s.extra_env, s.command, s.args)   # patched URL, shipped command/args intact
"
```

Then confirm the spawned server actually receives it — start the gateway with **no** `FIRECRAWL_API_URL` exported, connect firecrawl, and scrape a URL. Success means the overlay alone reached the child process, which is the whole point of #105.

Edge cases to exercise: patch for a server excluded by policy (must not resurrect it); patch whose value is an empty string (should pass through, not be dropped as falsy); overlay containing `server_env` and a `servers:` entry for the same name (replacement applies first, then the patch).

## Acceptance criteria

- [ ] An overlay containing only `server_env` for a shipped server sets that server's `extra_env` while `command`, `args`, `install`, and `env_var` remain byte-identical to the shipped entry — proven by `uv run pytest tests/test_manifest_overlay.py -q`
- [ ] A `server_env` patch naming an unknown server creates no entry and logs a warning naming the overlay file — proven by `test_server_env_unknown_server_warns_and_skips`
- [ ] All 8 pre-existing overlay tests pass unmodified, demonstrating `servers:` whole-entry-replace semantics are unchanged — proven by `uv run pytest tests/test_manifest_overlay.py -q`
- [ ] Full suite green with no new warnings — proven by `uv run pytest tests/ -q`
- [ ] A self-hosted Firecrawl is reachable through the gateway with the URL supplied **only** by `~/.pmcp/manifest.yaml` and no variable exported into the gateway environment — proven by the end-to-end block above

## Execution Policy

- execute: effort=medium, reason=small surface but merge-order and precedence semantics are easy to get subtly wrong, and the loader is shared by all 19 `load_manifest` call sites
