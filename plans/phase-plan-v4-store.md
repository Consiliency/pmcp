# STORE: Credential Store Hardening

## Context

Phase 1 of `specs/phase-plans-v4.md` hardens PMCP's local API-key credential
storage before later third-party auth phases build on it. The current code has
two env-store paths: `pmcp secrets` uses parser-backed helpers in
`src/pmcp/cli_commands/secrets.py` that quote values and chmod files to `0600`,
while `GatewayTools.auth_connect` uses `GatewayTools._write_secret(...)` in
`src/pmcp/tools/handlers.py`, which parses lines manually, writes unquoted
values, does not chmod, and can corrupt credentials containing shell-significant
characters.

The implementation should keep the existing user and project scope locations:
user scope remains `~/.config/pmcp/pmcp.env`; project scope remains
`<project>/.env.pmcp` for CLI paths and `Path.cwd()/.env.pmcp` for current
gateway behavior unless a caller supplies an explicit project path.

## Interface Freeze Gates

- [x] IF-0-STORE-1 — PMCP uses `pmcp.env_store` as the only write path for API-key credential files from gateway and CLI surfaces.
- [x] IF-0-STORE-2 — `pmcp.env_store.validate_env_var_name(name)` accepts only shell-compatible env var names matching `^[A-Za-z_][A-Za-z0-9_]*$` before any write.
- [x] IF-0-STORE-3 — `pmcp.env_store.write_env_file(path, values)` creates parent directories, writes parseable dotenv content, and chmods the target file to `0600`.
- [x] IF-0-STORE-4 — `pmcp.env_store.set_env_value(scope, key, value, project=None)` preserves existing user/project scope paths and returns the written `Path`.
- [x] IF-0-STORE-5 — newline-bearing credential values are rejected with a clear validation error; no multiline credential format is introduced in Phase 1.
- [x] IF-0-STORE-6 — credentials containing spaces, `#`, quotes, backslashes, and `=` round-trip through the shared reader and cannot inject extra env vars.

## Lane Index & Dependencies

- SL-0 — Shared env-store contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3; Parallel-safe: no
- SL-1 — CLI secrets migration and regression tests; Depends on: SL-0; Blocks: SL-3; Parallel-safe: yes
- SL-2 — Gateway auth-connect migration and regression tests; Depends on: SL-0; Blocks: SL-3; Parallel-safe: yes
- SL-3 — Phase verification and closeout; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Shared Env-Store Contract

- **Scope**: Extract the credential env-file reader, writer, path resolver, and validation rules into one reusable module without changing public command names.
- **Owned files**: `src/pmcp/env_store.py`, `src/pmcp/types.py`, `tests/test_auth.py`
- **Interfaces provided**: `ENV_VAR_NAME_PATTERN`, `validate_env_var_name(name)`, `resolve_scope_path(scope, project=None)`, `read_env_file(path)`, `write_env_file(path, values)`, `set_env_value(scope, key, value, project=None)`, newline rejection behavior, `AuthConnectInput.env_var` validation if model-level validation is added
- **Interfaces consumed**: existing `python-dotenv.dotenv_values`, existing user path `~/.config/pmcp/pmcp.env`, existing project path `<project>/.env.pmcp`, existing `find_project_root(...)`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add model/helper tests proving valid names such as `OPENAI_API_KEY` and `_PMCP_TOKEN` pass, while `1TOKEN`, `BAD-NAME`, `BAD.NAME`, `BAD NAME`, empty strings, and names containing `=` fail before writes.
  - test: Add shared round-trip tests for values containing spaces, `#`, single quotes, double quotes, backslashes, and `=` using `read_env_file(...)` after `write_env_file(...)`.
  - test: Add injection regression tests proving values such as `first\nINJECTED=second` and keys such as `GOOD=bad` are rejected and do not create additional env vars.
  - test: Add permission tests proving newly written files are chmodded to `0600`, including files created under missing parent directories.
  - impl: Move the safer parsing, formatting, scope-path, and chmod behavior from `src/pmcp/cli_commands/secrets.py` into `src/pmcp/env_store.py`.
  - impl: Add strict env-var name validation and newline rejection in the shared write/set path.
  - impl: Keep value formatting parseable by `python-dotenv`; do not add a multiline format in this phase.
  - impl: If validation is added to `AuthConnectInput`, keep it additive to existing fields and preserve default `auth_mode="api_key"`.
  - verify: `uv run pytest tests/test_auth.py -k "auth_connect or env_store or env_var or credential or injection"`
  - verify: `uv run ruff check src/pmcp/env_store.py src/pmcp/types.py tests/test_auth.py`

### SL-1 — CLI Secrets Migration and Regression Tests

- **Scope**: Route `pmcp secrets set/sync/check` through the shared env-store module while preserving current CLI output shapes.
- **Owned files**: `src/pmcp/cli_commands/secrets.py`, `tests/test_secrets_command.py`
- **Interfaces provided**: `run_secrets_set(...)`, `run_secrets_sync(...)`, `run_secrets_check(...)` using the shared env-store implementation
- **Interfaces consumed**: SL-0 `resolve_scope_path(...)`, `read_env_file(...)`, `write_env_file(...)`, `set_env_value(...)`, `validate_env_var_name(...)`, existing `_extract_required_keys(...)`, existing CLI argparse fields
- **Parallel-safe**: yes
- **Tasks**:
  - test: Update existing `test_run_secrets_set_writes_project_env_0600` to assert the shared writer output still includes the requested key, returns the same `path`, and masks the value.
  - test: Add `pmcp secrets set` tests for credentials containing spaces, `#`, quotes, backslashes, and `=`, then read back through the shared reader.
  - test: Add `pmcp secrets set` and `pmcp secrets sync` tests proving newline-bearing values are rejected and cannot inject additional env vars.
  - test: Add invalid key tests proving CLI handlers reject non-shell-compatible env var names before writing the target env file.
  - impl: Replace local `_get_user_env_path`, `_get_project_env_path`, `_resolve_scope_path`, `_read_env_file`, `_format_env_value`, and `_write_env_file` helpers with imports from `pmcp.env_store`, keeping private wrappers only if tests or local readability require them.
  - impl: Ensure `run_secrets_sync(...)` validates every source and target key before writing the merged target file.
  - impl: Preserve current `run_secrets_check(...)` behavior for user/project paths, required keys, auth metadata, available keys, and missing keys.
  - verify: `uv run pytest tests/test_secrets_command.py -k "secrets or env_var or credential or injection or chmod"`
  - verify: `uv run ruff check src/pmcp/cli_commands/secrets.py tests/test_secrets_command.py`

### SL-2 — Gateway Auth-Connect Migration and Regression Tests

- **Scope**: Replace `GatewayTools._write_secret(...)` with the shared env-store path so gateway credential writes match `pmcp secrets`.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: `gateway.auth_connect` credential writes with strict env-var validation, safe dotenv quoting, newline rejection, chmod `0600`, and unchanged success output fields
- **Interfaces consumed**: SL-0 `set_env_value(...)`, `validate_env_var_name(...)`, existing `AuthConnectInput`, existing manifest `ServerConfig.env_var`, existing `GatewayTools.auth_connect(...)` response contract, existing `os.environ[env_var]` runtime availability update
- **Parallel-safe**: yes
- **Tasks**:
  - test: Update `test_auth_connect_stores_credential` to avoid monkeypatching the old `_write_secret(...)` helper and instead assert the shared store writes a parseable env file.
  - test: Add `gateway.auth_connect` tests for explicit `env_var` values containing invalid characters, proving the call returns a structured failure and does not write an env file.
  - test: Add `gateway.auth_connect` tests for credentials containing spaces, `#`, quotes, backslashes, and `=`, proving they round-trip through the shared reader and update `os.environ`.
  - test: Add newline injection regression tests proving `gateway.auth_connect` rejects newline-bearing credentials and does not create an injected env var.
  - impl: Import and call `set_env_value(...)` from `pmcp.env_store` in `auth_connect`.
  - impl: Remove or reduce `GatewayTools._write_secret(...)`; if a compatibility wrapper remains, make it delegate directly to the shared helper.
  - impl: Convert shared env-store validation errors into `AuthConnectOutput(ok=False, auth_state="missing_auth", ...)` or an existing gateway-safe failure shape without printing credential values.
  - impl: Keep the existing behavior that a successful API-key write updates `os.environ[env_var]` for immediate provisioning retry.
  - verify: `uv run pytest tests/test_tools.py -k "auth_connect or credential or env_var or injection or missing_auth"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-3 — Phase Verification and Closeout

- **Scope**: Prove the CLI and gateway now share one credential-store contract and record the phase outcome without expanding into later auth phases.
- **Owned files**: `plans/phase-plan-v4-store.md`
- **Interfaces provided**: completed STORE acceptance checklist and execution notes
- **Interfaces consumed**: SL-0 shared env-store contract and verification results, SL-1 CLI behavior, SL-2 gateway behavior, Phase 1 exit criteria from `specs/phase-plans-v4.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Review SL-0 through SL-2 verification output and confirm each Phase 1 exit criterion has a named test.
  - impl: Mark this plan's interface gates and acceptance criteria complete only after implementation and verification pass.
  - impl: Record any intentional deviations, especially if `AuthConnectInput` model-level validation is skipped in favor of write-path validation.
  - verify: `uv run pytest tests/test_auth.py tests/test_tools.py tests/test_secrets_command.py -k "auth or credential or secret or env_var or injection or redact"`
  - verify: `uv run ruff check src/pmcp/env_store.py src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_tools.py tests/test_secrets_command.py`
  - verify: `uv run ruff format --check src/pmcp/env_store.py src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_tools.py tests/test_secrets_command.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_auth.py -k "auth_connect or env_store or env_var or credential or injection"`
- `uv run ruff check src/pmcp/env_store.py src/pmcp/types.py tests/test_auth.py`
- `uv run pytest tests/test_secrets_command.py -k "secrets or env_var or credential or injection or chmod"`
- `uv run ruff check src/pmcp/cli_commands/secrets.py tests/test_secrets_command.py`
- `uv run pytest tests/test_tools.py -k "auth_connect or credential or env_var or injection or missing_auth"`
- `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

Whole-phase regression:

- `uv run pytest tests/test_auth.py tests/test_tools.py tests/test_secrets_command.py -k "auth or credential or secret or env_var or injection or redact"`
- `uv run ruff check src/pmcp/env_store.py src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_tools.py tests/test_secrets_command.py`
- `uv run ruff format --check src/pmcp/env_store.py src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_tools.py tests/test_secrets_command.py`
- `uv run mypy src/pmcp --exclude baml_client`

Release-bound broader checks:

- `uv run pytest -q`
- `uv build`

## Acceptance Criteria

- [x] `gateway.auth_connect` and `pmcp secrets set/sync/check` share the same env-file read/write/format helpers.
- [x] Env var names are validated with `^[A-Za-z_][A-Za-z0-9_]*$` before writing from gateway and CLI credential surfaces.
- [x] Env files written by PMCP are chmodded to `0600`.
- [x] Credentials containing spaces, `#`, quotes, backslashes, and `=` round-trip without corrupting the env file.
- [x] Newline-bearing credentials are rejected; no multiline credential format is added in Phase 1.
- [x] Tests prove credential content cannot inject additional env vars.
- [x] Current user scope `~/.config/pmcp/pmcp.env` and project scope `.env.pmcp` paths remain backward compatible.
- [x] Gateway and CLI failure outputs do not include secret credential values when validation fails.

## Execution Notes

- Completed SL-0 through SL-3 on 2026-04-22.
- Shared credential storage now lives in `src/pmcp/env_store.py`; CLI and gateway API-key writes call `set_env_value(...)`/`write_env_file(...)` instead of maintaining separate env-file writers.
- `AuthConnectInput` model-level validation was intentionally left unchanged. Env-var and newline validation happen in the shared write path so `gateway.auth_connect` can convert validation failures into its existing structured `AuthConnectOutput(ok=False, auth_state="missing_auth", ...)` shape without exposing credential values.
- Named coverage added:
  - `test_env_store_validates_env_var_names`
  - `test_env_store_round_trips_shell_significant_values`
  - `test_env_store_rejects_injection_before_write`
  - `test_run_secrets_set_writes_project_env_0600`
  - `test_run_secrets_set_round_trips_shell_significant_values`
  - `test_run_secrets_set_rejects_injection_before_write`
  - `test_run_secrets_sync_rejects_injection_before_write`
  - `test_run_secrets_sync_rejects_invalid_keys_before_write`
  - `test_auth_connect_stores_credential`
  - `test_auth_connect_rejects_invalid_explicit_env_var`
  - `test_auth_connect_round_trips_shell_significant_credential`
  - `test_auth_connect_rejects_newline_credential_without_injection`
- Verification completed:
  - `uv run pytest tests/test_auth.py -k "auth_connect or env_store or env_var or credential or injection"` — passed
  - `uv run pytest tests/test_secrets_command.py -k "secrets or env_var or credential or injection or chmod"` — passed
  - `uv run pytest tests/test_tools.py -k "auth_connect or credential or env_var or injection or missing_auth"` — passed
  - `uv run pytest tests/test_auth.py tests/test_tools.py tests/test_secrets_command.py -k "auth or credential or secret or env_var or injection or redact"` — passed
  - `uv run ruff check src/pmcp/env_store.py src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_tools.py tests/test_secrets_command.py` — passed
  - `uv run ruff format --check src/pmcp/env_store.py src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_tools.py tests/test_secrets_command.py` — passed
  - `uv run mypy src/pmcp --exclude baml_client` — passed
  - `uv run pytest -q` — passed, 1669 passed, 12 skipped, 21 deselected
  - `uv build` — passed
