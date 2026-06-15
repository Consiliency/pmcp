---
phase_loop_plan_version: 1
phase: ENVFIX
roadmap: specs/phase-plans-v7.md
roadmap_sha256: f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5
---

# ENVFIX: Config & Env-Store Footguns

## Context

Phase ENVFIX implements Phase 4 of `specs/phase-plans-v7.md`: remove the credential-file permission window in PMCP's scoped env store and bound project-root discovery so project-scope secret writes land in the intended repository rather than an unrelated ancestor marker such as `/tmp/.git`.

The roadmap hash was verified from `specs/phase-plans-v7.md` as `f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5`. Canonical `.phase-loop/` state exists and marks `ENVFIX` unplanned; it also currently marks `CONCURR` blocked on closeout classification, but ENVFIX is an independent Stage A root in the roadmap DAG. Legacy `.codex/phase-loop/` state is compatibility-only and is not an input to this plan.

Current code already has the relevant seams: `write_env_file(...)`, `set_env_value(...)`, `resolve_project_root(...)`, and `resolve_scope_path(...)` in `src/pmcp/env_store.py`; `find_project_root(...)` and config source resolution in `src/pmcp/config/loader.py`; `GatewayTools._write_secret(...)` and `gateway.auth_connect` consuming `set_env_value(...)`; and CLI secrets tests that already assert final file mode is `0600`. ENVFIX should tighten those helpers in place without changing env-file format, scope names, or existing config precedence semantics.

## Interface Freeze Gates

- [ ] IF-0-ENVFIX-1 - `write_env_file(path: Path, values: dict[str, str]) -> None` validates all keys and values before opening the target file, then writes the complete `.env` content through `os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)` plus a file descriptor wrapper so a newly created credential file is never present with permissions broader than `0600`. `find_project_root(start_dir: Path) -> Path | None` remains the project-root discovery entry point but is bounded against unrelated ancestor markers such as `/tmp/.git`; project-scope secret resolution continues through `resolve_project_root(project: Path | None = None) -> Path` and `resolve_scope_path(scope: str, project: Path | None = None) -> Path`, giving AUTHRS a reusable resolver for per-tenant storage without changing the env-store file format or scope precedence.

## Lane Index & Dependencies

- SL-0 - Atomic env-store writes; Depends on: (none); Blocks: SL-2; Parallel-safe: yes
- SL-1 - Bounded project-root resolver; Depends on: (none); Blocks: SL-2; Parallel-safe: yes
- SL-2 - ENVFIX verification and closeout; Depends on: SL-0, SL-1; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Atomic Env-Store Writes

- **Scope**: Make PMCP credential env-file writes atomic with restrictive creation permissions while preserving validation, quoting, and read-back behavior.
- **Owned files**: `src/pmcp/env_store.py`, `tests/test_secrets_command.py`
- **Interfaces provided**: atomic `0600` creation path for IF-0-ENVFIX-1; preserved `write_env_file(...)`, `set_env_value(...)`, `read_env_file(...)`, `resolve_project_root(...)`, and `resolve_scope_path(...)` call shapes; regression coverage that secret files are never created with broader permissions
- **Interfaces consumed**: existing `_validate_env_values(...)`, `_format_env_value(...)`, `dotenv_values(...)`, CLI secrets `run_secrets_set(...)` and `run_secrets_sync(...)`, current user/project scope names, and existing final-mode assertions in `tests/test_secrets_command.py`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a failing-first regression in `tests/test_secrets_command.py` that patches or observes the low-level open path for a new project `.env.pmcp` and asserts `write_env_file(...)` creates it with mode `0o600`, not a default umask-controlled mode followed by chmod.
  - test: Extend the existing secrets set/sync coverage so overwrite and merge paths still validate invalid keys and multiline values before opening or truncating the target file.
  - test: Preserve round-trip coverage for shell-significant credential values and final `0600` mode after `run_secrets_set(...)` and `run_secrets_sync(...)`.
  - impl: Replace `Path.write_text(...)` followed by `chmod(0o600)` with `os.open(...)` using `O_WRONLY | O_CREAT | O_TRUNC` and permission mode `0o600`, then write the fully formatted content through the resulting descriptor.
  - impl: Keep parent directory creation, validation order, content formatting, trailing newline behavior, and `read_env_file(...)` compatibility unchanged.
  - verify: `TMPDIR=/var/tmp uv run pytest tests/test_secrets_command.py -k "secrets and (0600 or sync or injection or round_trips)"`
  - verify: `git diff --check -- src/pmcp/env_store.py tests/test_secrets_command.py`

### SL-1 - Bounded Project-Root Resolver

- **Scope**: Bound project-root discovery and prove project-scope credential writes do not escape to an unrelated ancestor marker.
- **Owned files**: `src/pmcp/config/loader.py`, `tests/test_config_loader.py`, `tests/test_tools.py`
- **Interfaces provided**: bounded `find_project_root(start_dir: Path) -> Path | None` behavior for IF-0-ENVFIX-1; resolver coverage for `.mcp.json`, `.git`, `package.json`, and `pyproject.toml` markers inside the intended project boundary; gateway `auth_connect` regression coverage for project-scope writes
- **Interfaces consumed**: existing `find_project_root(...)` callers in config loading, startup policy resolution, CLI base-dir resolution, `env_store.resolve_project_root(...)`, `GatewayTools._write_secret(...)`, and current `gateway.auth_connect` response shape
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add `tests/test_config_loader.py` regressions for normal nested-project discovery and the Appendix-A case: when the start directory is a temp project without a local marker and an unrelated ancestor such as the temp parent has `.git`, `find_project_root(...)` must not return that unrelated ancestor.
  - test: Add a `tests/test_tools.py` `gateway.auth_connect` regression that runs from a child directory under a temp project while an unrelated ancestor marker exists, then asserts the project-scope `.env.pmcp` path is inside the intended temp project or cwd fallback, not the ancestor.
  - test: Preserve existing config precedence behavior for explicit `project_root`, user config paths, custom config paths, and relative command/cwd normalization.
  - impl: Tighten `find_project_root(...)` so ancestor markers are only trusted within the intended project search boundary and root/temp ancestor sentinels do not capture unrelated project-scope credential writes.
  - impl: Keep `find_project_root(...)` as the exported discovery entry point consumed by `env_store.resolve_project_root(...)`; if helper functions are needed, keep them private to `config/loader.py` unless required by existing callers.
  - impl: Ensure project-scope secret resolution still falls back to the resolved current working directory when no bounded project root is found.
  - verify: `TMPDIR=/var/tmp uv run pytest tests/test_config_loader.py tests/test_tools.py -k "find_project_root or auth_connect or project_env or project root"`
  - verify: `git diff --check -- src/pmcp/config/loader.py tests/test_config_loader.py tests/test_tools.py`

### SL-2 - ENVFIX Verification and Closeout

- **Scope**: Run the ENVFIX verification set, confirm IF-0-ENVFIX-1 is fully produced, and prepare runner closeout evidence without owning additional source files.
- **Owned files**: none
- **Interfaces provided**: ENVFIX verification evidence; IF-0-ENVFIX-1 completion checklist; phase-owned dirty-path inventory for `src/pmcp/env_store.py`, `tests/test_secrets_command.py`, `src/pmcp/config/loader.py`, `tests/test_config_loader.py`, and `tests/test_tools.py`
- **Interfaces consumed**: IF-0-ENVFIX-1, SL-0 atomic-write results, SL-1 bounded-resolver results, roadmap ENVFIX exit criteria
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm no lane-owned file overlaps another lane-owned file and no executor touched files outside the active ENVFIX ownership set.
  - test: Confirm atomic `0600` file creation, pre-open validation, bounded `find_project_root(...)`, project-scope `auth_connect` placement, and reusable resolver behavior each have failing-first regression coverage.
  - verify: `TMPDIR=/var/tmp uv run pytest tests/test_config_loader.py tests/test_secrets_command.py tests/test_tools.py -k "find_project_root or auth_connect or project_env or secrets or 0600 or injection"`
  - verify: `TMPDIR=/var/tmp uv run pytest`
  - verify: `uv run ruff check .`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `git status --short`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
TMPDIR=/var/tmp uv run pytest tests/test_config_loader.py tests/test_secrets_command.py tests/test_tools.py -k "find_project_root or auth_connect or project_env or secrets or 0600 or injection"
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

- [ ] `write_env_file(...)` creates or truncates credential files through `os.open(..., os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)` or an equivalent descriptor path with no write-then-chmod window.
- [ ] Credential keys and values are fully validated before opening or truncating the target env file; invalid sync/set inputs leave the previous file contents untouched.
- [ ] Existing env-store formatting, quoting, read-back behavior, user/project scope names, and scope precedence semantics remain unchanged.
- [ ] `find_project_root(...)` is bounded so project-scope secret resolution does not silently ascend to unrelated ancestor markers such as `/tmp/.git`.
- [ ] Project-scope `gateway.auth_connect` writes `.env.pmcp` to the intended project root or cwd fallback in the Appendix-A temp ancestor case without requiring `TMPDIR` outside `/tmp`.
- [ ] `resolve_project_root(...)` and `resolve_scope_path(...)` remain reusable for AUTHRS per-tenant storage planning and return non-secret filesystem paths only.
- [ ] ENVFIX target tests, full `pytest` with `TMPDIR=/var/tmp`, `ruff`, and CI mypy baseline pass.
