# Detailed plan: fix gateway.describe nested-schema fidelity (#87) + index-it-mcp stdio transport & pilot docs (#89), ship v1.19.1

> **Plan Mode was NOT active** when this was produced — planning artifact only, no implementation begun.

## Task
Fix two confirmed, PMCP-owned bugs and ship as a **v1.19.1** patch:
- **#87** — `gateway.describe` collapses nested array/object argument schemas to bare `"array"`/`"object"`, so agents can't see item shape/required item fields and waste calls guessing (e.g. `brightdata::search_engine_batch` needs `{"queries":[{"query":"..."}]}`).
- **#89 (PMCP-owned slice only)** — the shipped `index-it-mcp` manifest entry launches `serve` (HTTP/admin) where PMCP's local path needs a **stdio** child; plus document how to pass non-secret operational env + pin the version for a fleet pilot. The readiness contract, repo-registration/bootstrap UX, and Code-Index-MCP-repo work are **out of scope** (deferred cross-repo effort).

## Research summary
Verified via two Explore passes:
- `describe` (`handlers.py:1567-1579`) builds each `ArgInfo` from only `prop.get("type"/"description"/"examples")` — it never reads `prop["items"]` or nested `properties`/`required`. The raw downstream JSON Schema (with `items`/`properties`) is already present on `ToolInfo.input_schema` (`client/manager.py:1120`), just unread. `ArgInfo` (`types.py:645-652`) is consumed **only** by `describe()`; no `__all__`, no test asserts an exhaustive field set (`test_tools.py:2104-2111` checks `len(args)` only), and `get_code_snippet` doesn't read `ArgInfo` fields — so **adding an optional field is backward-compatible**.
- The `index-it-mcp` manifest entry (`manifest.yaml:1081-1091`) uses `serve` in `command`/`args` and all four `install` platforms; it's the only manifest entry ending in a subcommand. `index-it-mcp stdio` is a real CLI command (Code-Index-MCP `mcp_server.cli` click group registers both `serve` and `stdio`).
- **The manifest schema has no `env:` block** — `_parse_server_config` (`loader.py:313-362`) parses only `env_var`/`env_instructions`; overlay entries share this parser. Per-entry `env` is available only through `.mcp.json` `mcpServers` (`LocalMcpServerConfig.env`), applied at `client/manager.py:1276-1290` (`env = os.environ.copy(); env.update(local_config.env)` → `create_subprocess_exec(env=env)`). **No test** currently asserts config `env` reaches the subprocess.
- README overlay/Configuration docs: "Private manifest overlay" heading at `README.md:900` (example `920-937`, precedence `909-915`); `.mcp.json` `env` example under "Adding Custom Servers" `958-964`.

## Changes

### `src/pmcp/types.py` (modify)
- `ArgInfo` — modify — add one optional field `item_schema: dict[str, Any] | None = None` (default None → backward-compatible). Holds a **compact** nested summary: for an array property, `{"type": "<item type>", "required": [...], "properties": {name: type, ...}}` from `prop["items"]`; for an object property, `{"required": [...], "properties": {name: type, ...}}`. Not full JSON Schema — one level deep, field name→type only.

### `src/pmcp/tools/handlers.py` (modify)
- `_summarize_arg_schema(prop: dict) -> tuple[str, dict | None, str]` — add (module-level or private method) — returns `(type_str, item_schema, placeholder)`. For `type == "array"` with an object `items`: `type_str="array"`, `item_schema` = compact item summary, `placeholder` = a nested example string, e.g. `[{"query": "<string>"}]` (built from the item's required fields, falling back to all item props if none required). For `type == "object"` with nested `properties`: summarize its props/required; placeholder = `{"<field>": "<type>", ...}`. For scalars: unchanged behavior (`item_schema=None`, placeholder = existing `<required|optional: {type}>`).
- `describe` arg-extraction loop (`handlers.py:1567-1579`) — modify — call `_summarize_arg_schema(prop)`, pass `item_schema` into `ArgInfo(...)`, and use the returned nested `placeholder` when building `arg_placeholders` (the `invoke_template`) for array/object args (keep the existing `<required|optional: type>` form for scalars). Reason: surface item shape + required item field names so agents invoke correctly first try.

### `src/pmcp/manifest/manifest.yaml` (modify)
- `index-it-mcp` entry (`~1085-1090`) — modify — replace `serve` with `stdio` in `command`/`args` (`["--from","index-it-mcp","index-it-mcp","stdio"]`) and in all four `install` platforms (mac/linux/wsl/windows). Reason: PMCP's local process path needs a stdio MCP child, not the HTTP/admin `serve` surface.

### `tests/test_tools.py` (modify)
- Add `test_describe_exposes_nested_array_item_schema` — add — build a tool whose `input_schema` mirrors `search_engine_batch` (`queries: {type: array, items: {type: object, properties: {query: {type: string}}, required: [query]}}`); assert the returned `ArgInfo` for `queries` has `item_schema` exposing item type `object` + required field `query` (name+type), and that the `invoke_template.arguments["queries"]` placeholder shows the nested `{"query": ...}` shape (not bare `<required: array>`).

### `tests/test_manifest.py` (modify)
- Add `test_index_it_mcp_uses_stdio_transport` — add — `load_manifest().get_server("index-it-mcp")`; assert `args[-1] == "stdio"` and `"serve" not in args`, and each `install[platform][-1] == "stdio"`. Mirrors `test_manifest_server_config_structure` (`~128`). Regression guard for the transport mode.

### `tests/test_client_manager.py` (modify)
- Add `test_local_config_env_reaches_subprocess` — add — connect a local server whose config carries an `env` dict (via a mocked `create_subprocess_exec`), assert the `env=` kwarg passed to the subprocess includes those keys merged over `os.environ`. Fills the recon-found gap and satisfies #89 AC3 "tested way to pass env". (Reuse the mock-process patterns already in this file.)

### `CHANGELOG.md` (modify)
- Add `## [1.19.1] - 2026-07-06` under `[Unreleased]` — **Fixed:** `gateway.describe` now exposes nested array/object item schemas + required item fields (issue #87); the shipped `index-it-mcp` manifest entry launches the `stdio` transport instead of `serve` (issue #89). **Docs:** README pilot config for provisioning `index-it-mcp` with pinned version + operational env via `.mcp.json`.

### `README.md` (modify)
- Add an `index-it-mcp` pilot subsection (insert after the overlay example, `~937`, or co-located with the `.mcp.json` env example, `~964`) — a copyable **`.mcp.json` `mcpServers`** entry pinning `index-it-mcp==<approved-version>` with the operational env block (`MCP_ALLOWED_ROOTS`, `SEMANTIC_SEARCH_ENABLED`, `SEMANTIC_DEFAULT_PROFILE`, `SEMANTIC_EMBEDDING_BASE_URL`, `QDRANT_URL`, etc. from the issue's Suggested Starting Config). State that PMCP passes `.mcp.json`/overlay-spawned local `env` **verbatim** into the child process (`_connect_stdio`), that pinning `==<version>` avoids PyPI release-line drift, and that env must go via `.mcp.json` (the manifest/overlay schema carries no `env:` block — call this out so operators don't put `env:` in an overlay expecting it to apply).

### `pyproject.toml`, `src/pmcp/__init__.py`, `uv.lock` (modify)
- version — modify — bump `1.19.0` → `1.19.1` (`uv lock` to update `uv.lock`).

## Documentation impact
- `README.md` — modify — index-it-mcp pilot config (above); the answer to #89 AC3/AC5-docs.
- `CHANGELOG.md` — modify — `[1.19.1]` entry (above).
- No other cross-cutting docs. **Frozen-vocabulary note:** `ArgInfo` gains only an *optional* field — no existing field renamed/removed; the describe output contract is additive.

## Deferred follow-ups (explicitly NOT in this plan)
- Per-entry `env:` support in the manifest/overlay schema (would let a single overlay carry the pilot env instead of `.mcp.json`) — a schema change; note as a possible v1.20 enhancement.
- #89 readiness contract (`ready`/`index_unavailable`/`safe_fallback`), repo-registration/bootstrap UX, and Code-Index-MCP-repo CLI/docs — a separate cross-repo effort.

## Dependencies & order
1. `types.py` ArgInfo field before `handlers.py` uses it.
2. `manifest.yaml` edit before its regression test.
3. Concerns A (#87: types/handlers/test) and B (#89: manifest/env-test/docs) are independent — either order.
4. Version bump + CHANGELOG last (release step). No external/migration deps.

## Execution Policy
- execute: effort=low, reason=bounded bug-fixes + docs; the only non-mechanical bit is the one-level schema-summary in `_summarize_arg_schema`.

## Verification
```bash
cd /home/viperjuice/code/pmcp
# Targeted
.venv/bin/python -m pytest tests/test_tools.py -k "describe" -v
.venv/bin/python -m pytest tests/test_manifest.py -k "index_it_mcp or stdio" tests/test_client_manager.py -k "env_reaches" -v
# Full gate (baseline 2187 passed) — must stay green
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src/ tests/ && .venv/bin/python -m ruff format --check src/ tests/
.venv/bin/python -m mypy src/pmcp/
# Release sanity
grep -m1 '^version' pyproject.toml; grep __version__ src/pmcp/__init__.py   # both 1.19.1
awk -v v="1.19.1" '$0 ~ "^## \\["v"\\]"{f=1;next} f&&/^## \[/{exit} f' CHANGELOG.md | head   # notes extractable
```
Edge cases to cover in tests: array whose `items` is a scalar (`{type: array, items: {type: string}}`) → placeholder `["<string>"]`, `item_schema` records item type only; array-of-objects with NO required item fields → placeholder uses all item props; a plain scalar arg → behavior unchanged (regression).

## Acceptance criteria
- [ ] `gateway.describe` on a tool with `queries: array<object{query:string, required}>` returns an `ArgInfo` whose `item_schema` shows item type `object` + required field `query`, and whose `invoke_template` placeholder shows the nested `{"query": ...}` shape (not bare `<required: array>`). Scalar-arg describe output is unchanged.
- [ ] `load_manifest().get_server("index-it-mcp")` has `args[-1] == "stdio"` (and no `"serve"`), for `command`/`args` and all four install platforms.
- [ ] A local server config `env` dict is passed to the spawned subprocess (`create_subprocess_exec(env=...)` includes the keys) — proven by test.
- [ ] README contains a copyable `.mcp.json` `index-it-mcp` pilot entry with pinned version + the operational env keys, and states env goes via `.mcp.json` (not the manifest/overlay schema).
- [ ] Full suite passes (≥2187), ruff (lint+format) + mypy clean; `pyproject.toml`/`__init__.py`/`uv.lock` all report `1.19.1`; `## [1.19.1]` CHANGELOG section is extractable by the release-notes awk.
