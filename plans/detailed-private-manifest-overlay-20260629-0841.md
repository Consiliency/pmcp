# Detailed plan: private/custom MCP manifest overlay

> **Plan Mode was NOT active when this was produced.** Planning artifact only — no implementation has begun.

## Task
Let users who built their own MCP servers add private manifest entries (provisionable definitions: `keywords`, `install`/`command`/`args`, `requires_api_key`/`env_var`, `transport`, `auto_start`, remote `url`/`headers`, etc.) as a customization **without editing the shipped `src/pmcp/manifest/manifest.yaml`**, so their servers get first-class treatment in `gateway.request_capability` keyword matching, `gateway.catalog_search` `manifest_candidates`/`include_offline` discovery, `gateway.provision`, and `refresh` startup resolution. Backward compatible; ships as **v1.18.0**.

## Research summary
- `load_manifest(manifest_path=None)` (`manifest/loader.py:358-393`) loads ONLY the packaged `manifest.yaml`; **no caching**, and the `open()`/`yaml.safe_load()` (`:366-367`) have **no try/except**. It builds `Manifest(version, cli_alternatives, servers, discovery_queue_path)` from `data.get(...)` keys.
- All **19 `load_manifest()` call sites** (handlers ×13, cli ×2, config/loader, refresher ×2, secrets, server) call it with **no args**, so centralizing overlay discovery+merge inside `load_manifest()` reaches every consumer with **zero call-site changes**.
- Consumers all read `manifest.servers`/`get_server`: `_manifest_candidates_for_query` (`handlers.py:1284`, gated by `is_server_allowed`), `request_capability` (`:3192,3243,3257,3475,3578,3670`), `provision` (`:3062,3895,4302,4832`), `refresh` → `resolve_startup_configs(manifest_servers=manifest.servers, is_server_allowed=…, is_auth_available=self._check_api_key_available)` (`:2054,2078-2079`). **No code reads `manifest.yaml` directly or hardcodes server-name sets** outside the loader — so a merged `servers` dict flows everywhere and is policy/auth-gated identically.
- `_parse_server_config(name, data)` (`loader.py:~302-355`) tolerates partials (`.get` with defaults; coerces bad types). `ServerConfig` (`:32-62`): required `name, description, keywords, install, command, args`; the rest optional with defaults (`requires_api_key=False`, `transport="local"` auto-upgraded to `streamable-http` when `url` present, etc.).
- Precedence precedent (`config/loader.py`): `DEFAULT_USER_CONFIG_PATHS` (`:36`), `find_project_root` (`:202`, walks up for `.mcp.json`/`.git`/`package.json`/`pyproject.toml`, stops at tempdir), merge is **first-seen-wins** in order project → user → custom.
- **Layering constraint:** `config/loader.py:613` imports `load_manifest`, so `manifest/loader.py` **must not import `config/loader`** (circular). Project-root discovery must be replicated locally (small, marker-based).

## Design decisions (precedence + behavior)
- **Overlay sources**, lowest→highest precedence (later overrides earlier, by server name):
  1. shipped `manifest.yaml` (base)
  2. user `~/.pmcp/manifest.yaml`
  3. project `<project>/.pmcp/manifest.yaml` (nearest ancestor of cwd containing `.pmcp/manifest.yaml`)
  4. `PMCP_MANIFEST_PATH` (explicit env override — **wins all**)
  Rationale: more-specific/more-explicit wins. (Note: this makes the explicit env path *highest*, unlike `PMCP_CONFIG` which is lowest in config/loader — deliberate; documented. Flag at review if config-parity is preferred instead.)
- **Override allowed:** an overlay entry with the same name as a shipped/earlier entry **replaces** it (whole-entry replace, not deep-merge) — simplest, predictable.
- **Overlay applies only on the default path:** `load_manifest()` applies overlays only when called with `manifest_path is None` (all 19 callers). An explicit `manifest_path` loads just that file (preserves deterministic test/explicit behavior).
- **Fail-soft:** a missing overlay file is skipped silently; a malformed overlay (bad YAML / OSError / bad entry) logs a `warning` naming the file and is skipped per-file, with per-entry try/except so one bad entry doesn't drop the whole file. The shipped manifest load is unchanged (its failure is a real bug, not user input). Never crashes any of the 19 callers — same robustness bar as #84.
- `cli_alternatives` merged with the same precedence (cheap, consistent).

## Changes

### `src/pmcp/manifest/loader.py` (modify)
- `DEFAULT_USER_MANIFEST_PATHS` — add — module constant `[Path.home() / ".pmcp" / "manifest.yaml"]` (mirrors `DEFAULT_USER_CONFIG_PATHS`).
- `_find_project_manifest()` — add — walk up from `Path.cwd()` for the nearest ancestor containing `.pmcp/manifest.yaml` (stop at filesystem root and `tempfile.gettempdir()`); return that path or `None`. Replicates `find_project_root`'s walk locally to avoid the circular import; keyed directly on the `.pmcp/manifest.yaml` marker.
- `_overlay_manifest_paths()` — add — return existing overlay file paths as `list[tuple[str, Path]]` in precedence order `[("user", ~/.pmcp/manifest.yaml), ("project", <proj>/.pmcp/manifest.yaml), ("env", $PMCP_MANIFEST_PATH)]`, filtering to files that exist.
- `_load_overlay_file(path)` — add — fail-soft: `try` open+`yaml.safe_load`; on `OSError`/`yaml.YAMLError`/non-dict, `logger.warning` + return `({}, {})`. Parse `servers`/`cli_alternatives` per-entry, each in its own try/except (warn+skip bad entry), reusing `_parse_server_config`/`_parse_cli_alternative`. Returns `(servers_dict, cli_alts_dict)`.
- `load_manifest(manifest_path=None)` — modify — capture `apply_overlays = manifest_path is None` BEFORE defaulting. After building the shipped `servers`/`cli_alternatives` dicts, if `apply_overlays`: for each `(label, path)` from `_overlay_manifest_paths()`, merge its servers/cli_alts over the accumulators (overlay wins by key; `logger.info` count per source). Construct `Manifest` from the merged dicts. Keep the existing explicit-path branch behavior unchanged. Update the final log line to note overlay counts when applied.

### `tests/test_manifest_overlay.py` (create)
- `test_user_overlay_adds_provisionable_server` — `monkeypatch` HOME to a tmp dir with `~/.pmcp/manifest.yaml` defining a private server with keywords; assert it appears in `load_manifest().servers` and that `_manifest_candidates_for_query`/`request_capability` keyword match surfaces it (drive via `GatewayTools` with a real `PolicyManager`, or assert `Manifest.search_by_keyword`).
- `test_project_overrides_user_overrides_shipped` — same-named server in shipped vs user vs project tmp files; assert the project definition wins (e.g. distinct `command`).
- `test_env_path_override_wins` — `monkeypatch.setenv("PMCP_MANIFEST_PATH", …)`; assert its entry wins over user/project.
- `test_malformed_overlay_is_skipped` — write invalid YAML to `~/.pmcp/manifest.yaml`; assert `load_manifest()` still returns the shipped servers (non-empty) and does not raise.
- `test_malformed_entry_skipped_rest_loaded` — overlay with one bad entry + one good; assert good one loaded, bad one skipped, no raise.
- `test_explicit_path_skips_overlays` — call `load_manifest(tmp_manifest)` with HOME/project overlays present; assert overlays are NOT applied (only the explicit file's servers).

### `README.md` (modify)
- Add a "Private manifest overlay" subsection under `## Configuration` (near Config Discovery ~`:862`): the three overlay locations, `PMCP_MANIFEST_PATH`, precedence (project > user > shipped, env wins), a minimal example private entry (local + remote), and a security note. Cross-reference that this answers "can users add their own private manifest items."

### `CHANGELOG.md` (modify)
- Under `## [Unreleased]` `### Added`: private/custom manifest overlay (`~/.pmcp/manifest.yaml`, `<project>/.pmcp/manifest.yaml`, `PMCP_MANIFEST_PATH`) merged over the shipped manifest, fail-soft, participating in request_capability/catalog_search/provision/refresh. (Promote to `## [1.18.0]` at release.)

## Documentation impact
- `README.md` — modify — document the new overlay capability + precedence + security (above).
- `CHANGELOG.md` — modify — Added entry for v1.18.0.
- No other cross-cutting docs apply (no schema/openapi/AGENTS change; overlay reuses the existing `ServerConfig` schema — **no new manifest vocabulary introduced**).

## Dependencies & order
1. Constants + `_find_project_manifest` + `_overlay_manifest_paths` + `_load_overlay_file` (leaf helpers).
2. Wire into `load_manifest` (uses 1).
3. Tests (use 2).
4. Docs. No external/migration dependencies.

## Verification
```bash
cd /home/viperjuice/code/pmcp
.venv/bin/python -m pytest tests/test_manifest_overlay.py -v
# Guard the 19 consumers + existing manifest behavior:
.venv/bin/python -m pytest tests/test_manifest.py tests/test_manifest_provision.py tests/test_offline_discovery.py tests/test_tools.py -q
.venv/bin/python -m pytest -q                      # full suite (baseline 2094 passed)
.venv/bin/python -m ruff check src/pmcp/manifest/loader.py tests/test_manifest_overlay.py
.venv/bin/python -m ruff format --check src/pmcp/manifest/loader.py tests/test_manifest_overlay.py
.venv/bin/python -m mypy src/pmcp/manifest/loader.py
# Manual smoke (proves end-to-end discovery surfacing):
mkdir -p ~/.pmcp && cat > ~/.pmcp/manifest.yaml <<'YAML'
servers:
  my-private:
    description: "My private server"
    keywords: [myprivate, internal widget]
    command: "npx"
    args: ["-y", "@me/my-mcp"]
    requires_api_key: true
    env_var: MY_TOKEN
YAML
.venv/bin/python -c "from pmcp.manifest.loader import load_manifest; m=load_manifest(); print('my-private' in m.servers, len(m.servers))"
rm ~/.pmcp/manifest.yaml
```
Edge cases: missing overlay (silently skipped); bad YAML (warn, shipped still loads); explicit `manifest_path` ignores overlays; project discovery stops at tempdir (no infinite walk); a server present in shipped + overlay resolves to the overlay definition.

## Acceptance criteria
- [ ] A server defined only in `~/.pmcp/manifest.yaml` is present in `load_manifest().servers` and is returned by `_manifest_candidates_for_query` for a query matching its keywords (with a permissive `PolicyManager`).
- [ ] For a same-named server, `<project>/.pmcp/manifest.yaml` wins over `~/.pmcp/manifest.yaml` which wins over the shipped entry; `PMCP_MANIFEST_PATH` wins over all.
- [ ] A malformed overlay file causes a logged warning and `load_manifest()` still returns the full shipped server set without raising (and one bad entry doesn't drop a valid sibling).
- [ ] `load_manifest(<explicit path>)` applies NO overlays (returns only that file's servers).
- [ ] Full suite + ruff (lint+format) + mypy pass; the 19 call sites are unchanged.
