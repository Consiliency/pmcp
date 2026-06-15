---
phase_loop_plan_version: 1
phase: MANIFEST
roadmap: specs/phase-plans-v7.md
roadmap_sha256: f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5
---

# MANIFEST: Manifest & Matcher Correctness

## Context

Phase MANIFEST implements Phase 3 of `specs/phase-plans-v7.md`: close the manifest cache YAML injection path, fix matcher ranking bias, repoint or label archived reference-server entries, and land the low-severity manifest/versioning cleanups that do not require live registry consumption.

The roadmap hash was verified from `specs/phase-plans-v7.md` as `f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5`. Canonical `.phase-loop/` state exists and marks `MANIFEST` unplanned; the same state currently marks `CONCURR` blocked on closeout classification, but MANIFEST is an independent Stage A root in the roadmap DAG. Legacy `.codex/phase-loop/` state is compatibility-only and is not an input to this plan.

Current code already has the relevant seams: `save_descriptions_cache(...)` and `refresh_all(...)` in `src/pmcp/manifest/refresher.py`, `_keyword_match_score(...)` and `_keyword_match(...)` in `src/pmcp/manifest/matcher.py`, the curated catalog in `src/pmcp/manifest/manifest.yaml`, package/version detection and `_USER_AGENT` in `src/pmcp/manifest/version_checker.py`, and API-key error reporting in `src/pmcp/manifest/installer.py`. REGISTRY owns live registry consumption later; MANIFEST must keep the catalog audit curated and offline.

## Interface Freeze Gates

- [ ] IF-0-MANIFEST-1 - Manifest cache writes use structured `yaml.safe_dump(...)` over a plain Python mapping so hostile server, tool, description, tag, or `risk_hint` text cannot inject sibling YAML keys. Manifest matching scores by absolute/IDF-weighted matched keyword evidence instead of dividing by each server's own keyword count, so precise multi-keyword matches from well-described servers outrank sparse generic entries and remain above threshold. `src/pmcp/manifest/manifest.yaml` entries distinguish active first-party/current servers from archived or community replacements with an explicit `status`/transport shape that downstream REGISTRY can consume without reinterpreting legacy archived `@modelcontextprotocol/server-*` packages.

## Lane Index & Dependencies

- SL-0 - Cache serialization and refresh concurrency; Depends on: (none); Blocks: SL-3; Parallel-safe: yes
- SL-1 - Matcher scoring and manifest audit; Depends on: (none); Blocks: SL-3; Parallel-safe: no
- SL-2 - Version checker and install diagnostics; Depends on: (none); Blocks: SL-3; Parallel-safe: yes
- SL-3 - MANIFEST verification and closeout; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Cache Serialization and Refresh Concurrency

- **Scope**: Replace hand-built descriptions-cache YAML with structured serialization and make refresh version lookups bounded-concurrent without changing cache schema semantics.
- **Owned files**: `src/pmcp/manifest/refresher.py`, `tests/test_refresher.py`
- **Interfaces provided**: structured descriptions-cache writer for IF-0-MANIFEST-1; hostile cache round-trip regression coverage; bounded-concurrent `refresh_all(...)` version lookup behavior
- **Interfaces consumed**: existing `DescriptionsCache`, `GeneratedServerDescriptions`, `PrebuiltToolInfo`, `load_descriptions_cache(...)`, `save_descriptions_cache(...)`, `refresh_server(...)`, `get_package_version(...)`, and current `.mcp-gateway/descriptions.yaml` field names
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a failing-first cache round-trip regression where server names, tool names, descriptions, tags, and `risk_hint` values include quotes, colons, newlines, and text resembling sibling YAML keys; assert `load_descriptions_cache(...)` returns only the original structured values and no injected `risk_hint` or server key.
  - test: Add a `refresh_all(...)` regression using multiple stale servers and an async mock for version lookup to prove lookups run through `asyncio.gather` or equivalent bounded concurrency instead of serial awaits, without making live network calls.
  - impl: Change `save_descriptions_cache(...)` to build a plain nested mapping and write it via `yaml.safe_dump(..., sort_keys=False, allow_unicode=True)`, preserving the existing top-level fields and cache load compatibility.
  - impl: Keep comments optional; do not reintroduce hand-built YAML fragments for any user-controlled field.
  - impl: Add a small concurrency cap around refresh/version checks so `pmcp refresh` avoids fully serial version lookups while retaining deterministic cache output.
  - verify: `uv run pytest tests/test_refresher.py -k "cache or yaml or refresh or version"`
  - verify: `git diff --check -- src/pmcp/manifest/refresher.py tests/test_refresher.py`

### SL-1 - Matcher Scoring and Manifest Audit

- **Scope**: Fix manifest matcher ranking bias and curate the archived reference-server entries in the single-writer manifest file.
- **Owned files**: `src/pmcp/manifest/matcher.py`, `src/pmcp/manifest/manifest.yaml`, `tests/test_manifest.py`
- **Interfaces provided**: IF-0-MANIFEST-1 matcher scoring contract; explicit active/archived/community manifest status and transport shape; curated audit coverage for archived `@modelcontextprotocol/server-*` entries
- **Interfaces consumed**: existing `Manifest`, `ServerConfig`, `_keyword_match_score(...)`, `_keyword_match(...)`, `match_capability(...)`, `Manifest.search_by_keyword(...)`, `Manifest.get_servers_in_category(...)`, and current manifest loader schema compatibility
- **Parallel-safe**: no
- **Tasks**:
  - test: Add a failing-first matcher regression where a well-described server with several precise matched keywords outranks a sparse server with a shorter keyword list, and assert the result stays above the minimum match threshold.
  - test: Add category/search regressions for generic keywords such as `api` so common terms alone do not drown out specific multi-keyword evidence.
  - test: Add manifest regression coverage for the curated archived/community entries named in the roadmap audit, including GitHub remote, Brave, Linear, Sentry remote, and other `@modelcontextprotocol/server-*` references that are repointed or explicitly labeled.
  - impl: Replace the current `matches / len(keywords)` scorer with an absolute matched-keyword evidence score weighted by inverse keyword frequency or an equivalent local IDF calculation over manifest entries; keep matching deterministic and offline.
  - impl: Preserve existing CLI preference behavior while ensuring server confidence reflects absolute matched evidence rather than the server's own keyword-count denominator.
  - impl: Update `manifest.yaml` so archived first-party package names are either repointed to current first-party remote/current packages or labeled with explicit status/transport metadata that the existing loader can parse and downstream REGISTRY can distinguish.
  - impl: Keep live registry discovery out of scope; every manifest edit in this lane is curated static data.
  - verify: `uv run pytest tests/test_manifest.py -k "keyword or matcher or manifest or archived or status"`
  - verify: `git diff --check -- src/pmcp/manifest/matcher.py src/pmcp/manifest/manifest.yaml tests/test_manifest.py`

### SL-2 - Version Checker and Install Diagnostics

- **Scope**: Land the low-severity manifest/versioning fixes outside the cache and matcher files.
- **Owned files**: `src/pmcp/manifest/version_checker.py`, `src/pmcp/manifest/installer.py`, `tests/test_install_command.py`, `tests/test_version_checker.py`
- **Interfaces provided**: version lookup user-agent derived from `pmcp.__version__`; package-name tag stripping for any `@<tag>` suffix; `MissingApiKeyError` message that names the actual PMCP env file path
- **Interfaces consumed**: existing `_USER_AGENT`, `detect_package_type(...)`, `get_cargo_version(...)`, `MissingApiKeyError`, `check_api_key(...)`, `resolve_scope_path(...)`, and existing install command tests
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add `tests/test_version_checker.py` coverage proving `_USER_AGENT` interpolates the installed `pmcp.__version__` rather than a stale literal.
  - test: Add `detect_package_type(...)` regressions for npm package arguments with arbitrary dist-tags or versions, including unscoped `pkg@beta`, `pkg@1.2.3`, scoped `@org/pkg@beta`, and scoped `@org/pkg@1.2.3`, while preserving scoped package names without tags.
  - test: Add `tests/test_install_command.py` coverage asserting missing API-key errors name `.env.pmcp` or `~/.config/pmcp/pmcp.env` through the resolver path the command actually reads.
  - impl: Build `_USER_AGENT` from `pmcp.__version__` at import time without adding network calls or package metadata lookups.
  - impl: Replace literal `@latest` stripping with a package-name parser that removes any trailing dist-tag/version while preserving npm scope prefixes.
  - impl: Adjust `MissingApiKeyError` or its construction site so the visible non-secret error text includes the resolved PMCP env file path.
  - verify: `uv run pytest tests/test_version_checker.py tests/test_install_command.py -k "user_agent or package_type or tag or api_key or env"`
  - verify: `git diff --check -- src/pmcp/manifest/version_checker.py src/pmcp/manifest/installer.py tests/test_install_command.py tests/test_version_checker.py`

### SL-3 - MANIFEST Verification and Closeout

- **Scope**: Run the MANIFEST verification set, confirm IF-0-MANIFEST-1 is fully produced, and prepare runner closeout evidence without owning additional source files.
- **Owned files**: none
- **Interfaces provided**: MANIFEST verification evidence; IF-0-MANIFEST-1 completion checklist; phase-owned dirty-path inventory for `src/pmcp/manifest/refresher.py`, `tests/test_refresher.py`, `src/pmcp/manifest/matcher.py`, `src/pmcp/manifest/manifest.yaml`, `tests/test_manifest.py`, `src/pmcp/manifest/version_checker.py`, `src/pmcp/manifest/installer.py`, `tests/test_install_command.py`, and `tests/test_version_checker.py`
- **Interfaces consumed**: IF-0-MANIFEST-1, SL-0 cache/concurrency results, SL-1 matcher/manifest audit results, SL-2 version/install results, roadmap MANIFEST exit criteria
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm no lane-owned file overlaps another lane-owned file and no executor touched files outside the active MANIFEST ownership set.
  - test: Confirm hostile YAML cache input, matcher ranking bias, archived manifest entries, `_USER_AGENT`, bounded refresh version lookups, env-path diagnostics, and arbitrary tag stripping each have failing-first regression coverage.
  - verify: `uv run pytest tests/test_manifest.py tests/test_refresher.py tests/test_install_command.py tests/test_version_checker.py -k "matcher or cache or version or stale or user_agent or package_type or api_key"`
  - verify: `TMPDIR=/var/tmp uv run pytest`
  - verify: `uv run ruff check .`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `git status --short`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
uv run pytest tests/test_manifest.py tests/test_refresher.py tests/test_install_command.py tests/test_version_checker.py -k "matcher or cache or version or stale or user_agent or package_type or api_key"
TMPDIR=/var/tmp uv run pytest
uv run ruff check .
uv run mypy src/pmcp --exclude baml_client
git status --short
```

Effective automation.suite_command:

```bash
TMPDIR=/var/tmp uv run pytest && uv run ruff check . && uv run mypy src/pmcp --exclude baml_client
```

## Acceptance Criteria

- [ ] `save_descriptions_cache(...)` writes the descriptions cache via `yaml.safe_dump(...)` over structured data; hostile names/descriptions/tags cannot inject sibling keys after a save/load round trip.
- [ ] Matcher scores use absolute/IDF-weighted matched-keyword evidence rather than division by each server's own keyword count; precise multi-keyword matches from well-described servers outrank sparse generic entries.
- [ ] Generic high-frequency keywords alone do not produce high-confidence category or server matches.
- [ ] Archived `@modelcontextprotocol/server-*` manifest entries are repointed to current first-party servers or explicitly labeled with status/transport metadata, with regression coverage for the curated audit set.
- [ ] `_USER_AGENT` in version lookups includes `pmcp.__version__`.
- [ ] `refresh_all(...)` / `pmcp refresh` version lookups run with bounded concurrency instead of fully serial awaits.
- [ ] `MissingApiKeyError` names the actual PMCP env file path resolved for `.env.pmcp` or `~/.config/pmcp/pmcp.env` without exposing secret values.
- [ ] `detect_package_type(...)` strips arbitrary npm `@<tag>` or version suffixes while preserving scoped package names.
- [ ] MANIFEST target tests, full `pytest` with `TMPDIR=/var/tmp`, `ruff`, and CI mypy baseline pass.
