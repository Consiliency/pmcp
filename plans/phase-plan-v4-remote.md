# REMOTE: Remote Header Auth Detection

## Context

Phase 2 of `specs/phase-plans-v4.md` builds on IF-0-STORE-1 from the STORE
phase. Local API-key writes now have one env-store path, but remote MCP headers
still expand `${ENV_VAR}` placeholders inside `src/pmcp/client/manager.py` and
currently turn missing placeholders into empty strings. That means PMCP can try
SSE or Streamable HTTP connections with malformed headers such as an empty
`Authorization` value, then report the result as a generic remote connection
failure.

The implementation should preserve literal non-placeholder headers and make
remote header credential gaps structured across gateway tools and CLI operator
surfaces. Missing remote header placeholders must report env var names only, not
values.

## Interface Freeze Gates

- [x] IF-0-REMOTE-1 — `pmcp.remote_auth.resolve_remote_headers(headers, env_lookup)` resolves `${ENV_VAR}` placeholders anywhere in remote header values and returns a non-secret result with `resolved_headers`, `missing_env_vars`, and `referenced_env_vars_by_header`.
- [x] IF-0-REMOTE-2 — `pmcp.remote_auth.build_remote_header_env_lookup(project_root=None)` checks live `os.environ` plus PMCP user/project env stores for non-empty values without logging or returning values to outputs.
- [x] IF-0-REMOTE-3 — `ClientManager._connect_sse(...)` and `ClientManager._connect_streamable_http(...)` fail before constructing the MCP transport context when required remote header placeholders are missing.
- [x] IF-0-REMOTE-4 — `ProvisionOutput` and `LifecycleServerOutput` expose `missing_env_vars: list[str]` alongside `auth_state="missing_auth"` for remote header-placeholder failures from `gateway.provision` and `gateway.connect_server`.
- [x] IF-0-REMOTE-5 — `ServerHealthInfo` and `EffectiveConfigEntry` expose `missing_env_vars: list[str]` for operator status/config surfaces without secret values.
- [x] IF-0-REMOTE-6 — `pmcp doctor`, `pmcp secrets check`, and `pmcp status` all use the same remote-header placeholder detection semantics as the gateway and transport paths.

## Lane Index & Dependencies

- SL-0 — Shared remote-header auth contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 — Config and startup missing-auth classification; Depends on: SL-0; Blocks: SL-3, SL-4, SL-5; Parallel-safe: yes
- SL-2 — Client transport fail-fast guard; Depends on: SL-0; Blocks: SL-3, SL-5; Parallel-safe: yes
- SL-3 — Gateway provision/connect/status surfacing; Depends on: SL-0, SL-1, SL-2; Blocks: SL-4, SL-5; Parallel-safe: no
- SL-4 — CLI doctor, secrets, and status surfacing; Depends on: SL-0, SL-1, SL-3; Blocks: SL-5; Parallel-safe: no
- SL-5 — Phase verification and closeout; Depends on: SL-0, SL-1, SL-2, SL-3, SL-4; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Shared Remote-Header Auth Contract

- **Scope**: Add one non-secret resolver for remote header placeholders and the additive output fields that later lanes surface.
- **Owned files**: `src/pmcp/remote_auth.py`, `src/pmcp/types.py`, `tests/test_auth.py`
- **Interfaces provided**: `RemoteHeaderAuthResolution`, `MissingRemoteHeaderAuthError`, `REMOTE_HEADER_ENV_PATTERN`, `resolve_remote_headers(headers, env_lookup)`, `collect_remote_header_env_vars(headers)`, `build_remote_header_env_lookup(project_root=None)`, `missing_env_vars` fields on `ProvisionOutput`, `LifecycleServerOutput`, `ServerHealthInfo`, and `EffectiveConfigEntry`
- **Interfaces consumed**: IF-0-STORE-1 `read_env_file(...)`, `resolve_scope_path(...)`, existing `os.environ`, existing `RemoteMcpServerConfig.headers`, existing `AuthState` literal `"missing_auth"`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add helper tests proving `${REMOTE_API_TOKEN}` and embedded `Bearer ${REMOTE_API_TOKEN}` values resolve when env lookup returns non-empty values.
  - test: Add helper tests proving missing placeholders produce sorted, de-duplicated `missing_env_vars` and keep literal non-placeholder headers unchanged.
  - test: Add helper tests proving outputs and exception messages contain env var names but not resolved secret values.
  - test: Add env lookup tests proving process env and PMCP user/project env stores satisfy placeholders without exposing values.
  - impl: Create `src/pmcp/remote_auth.py` with a single braced-placeholder regex for remote headers, explicit result dataclass, and a custom missing-auth exception that carries only server name and env var names.
  - impl: Resolve placeholders throughout a header value, not only when the entire value is `${VAR}`.
  - impl: Treat empty string values as missing credentials so PMCP does not send empty secret-bearing headers.
  - impl: Add additive `missing_env_vars: list[str] = Field(default_factory=list)` fields to the gateway/health/config status output models.
  - verify: `uv run pytest tests/test_auth.py -k "remote_header or missing_env or auth"`
  - verify: `uv run ruff check src/pmcp/remote_auth.py src/pmcp/types.py tests/test_auth.py`

### SL-1 — Config and Startup Missing-Auth Classification

- **Scope**: Teach config/startup resolution to classify configured remote servers with missing header placeholders before eager startup tries to connect them.
- **Owned files**: `src/pmcp/config/loader.py`, `tests/test_config_loader.py`
- **Interfaces provided**: `StartupSkip.missing_env_vars`, `StartupObservation.missing_env_vars`, configured remote startup skips with `reason=StartupSkipReason.MISSING_AUTH`, preserved `startup_env_var` as the first missing var for backward compatibility
- **Interfaces consumed**: SL-0 `collect_remote_header_env_vars(...)`, `resolve_remote_headers(...)`, `build_remote_header_env_lookup(...)`, `missing_env_vars` output model fields, existing `resolve_startup_configs(...)`, existing `build_startup_observation_snapshot(...)`, existing remote config coercion for `remote`, `sse`, `http`, and `streamable-http`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add config-loader tests for a configured `remote` server in `autoStart` whose `Authorization: Bearer ${REMOTE_API_TOKEN}` placeholder is missing, asserting it is skipped with `missing_auth`, `startup_env_var="REMOTE_API_TOKEN"`, and `missing_env_vars=["REMOTE_API_TOKEN"]`.
  - test: Add parameterized coverage for `sse`, `http`, and `streamable-http` configured remotes using the same placeholder detection path.
  - test: Add tests proving literal headers and present placeholders leave the remote eligible for lazy/eager startup as before.
  - impl: Extend `StartupSkip` and `StartupObservation` with `missing_env_vars` while preserving existing fields and summaries.
  - impl: In `resolve_startup_configs(...)`, evaluate remote header placeholders for configured eager remote servers and skip them before connection when required values are missing.
  - impl: Preserve manifest API-key `env_var` behavior and do not change local process env handling.
  - verify: `uv run pytest tests/test_config_loader.py -k "remote or startup or missing_auth or header"`
  - verify: `uv run ruff check src/pmcp/config/loader.py tests/test_config_loader.py`

### SL-2 — Client Transport Fail-Fast Guard

- **Scope**: Replace direct header interpolation in the client manager with the shared resolver and stop SSE/Streamable HTTP transport construction when placeholders are missing.
- **Owned files**: `src/pmcp/client/manager.py`, `tests/test_client_manager.py`
- **Interfaces provided**: fail-fast remote transport behavior for both SSE and Streamable HTTP; no empty `Authorization`, `X-API-Key`, or other placeholder-backed headers are sent when env vars are missing
- **Interfaces consumed**: SL-0 `resolve_remote_headers(...)`, `build_remote_header_env_lookup(...)`, `MissingRemoteHeaderAuthError`, existing `RemoteMcpServerConfig`, existing `sse_client(...)`, existing `streamablehttp_client(...)`, existing `connect_all(...)` error collection
- **Parallel-safe**: yes
- **Tasks**:
  - test: Update current SSE and Streamable HTTP header interpolation tests to use embedded `Bearer ${PMCP_TEST_TOKEN}` values and assert literal headers are preserved.
  - test: Add SSE missing-placeholder tests proving `sse_client(...)` is not called and no header with an empty value is sent.
  - test: Add Streamable HTTP missing-placeholder tests proving `streamablehttp_client(...)` is not called and no header with an empty value is sent.
  - test: Add coverage for multiple missing placeholders across multiple headers with de-duplicated env var names in the raised error.
  - impl: Remove `_ENV_VAR_HEADER_PATTERN` and `_interpolate_header_value(...)` from `manager.py` or reduce them to direct wrappers around SL-0 helpers.
  - impl: Resolve headers immediately before the remote transport context is created and raise `MissingRemoteHeaderAuthError(config.name, missing_env_vars)` if any placeholders are missing.
  - impl: Ensure `connect_all(...)` and server status error paths preserve the missing env var names but not values.
  - verify: `uv run pytest tests/test_client_manager.py -k "remote and (header or sse or streamable)"`
  - verify: `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py`

### SL-3 — Gateway Provision/Connect/Status Surfacing

- **Scope**: Preflight remote header placeholders in gateway lifecycle and provisioning paths and return structured missing-auth outputs before connecting.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: `gateway.provision` and `gateway.connect_server` return `auth_state="missing_auth"` with `missing_env_vars` for remote header placeholders; `gateway.health` and `gateway.config_status` propagate `missing_env_vars`
- **Interfaces consumed**: SL-0 resolver and output fields, SL-1 startup observations, SL-2 `MissingRemoteHeaderAuthError`, existing `manifest_server_to_config(...)`, existing `_lifecycle_output(...)`, existing `ProvisionOutput`, existing `_check_api_key_available(...)`, existing auth metadata helpers
- **Parallel-safe**: no
- **Tasks**:
  - test: Add `gateway.connect_server` tests for configured remote servers with missing `Authorization: Bearer ${REMOTE_API_TOKEN}`, asserting `ok=False`, `auth_state="missing_auth"`, `missing_env_vars=["REMOTE_API_TOKEN"]`, and no client-manager connect call.
  - test: Add `gateway.provision` tests for remote manifest servers with missing header placeholders, asserting the same structured missing-auth fields and no installer job.
  - test: Add success tests proving present remote header credentials still allow manifest remote provisioning and configured remote connection.
  - test: Add `gateway.health` and `gateway.config_status` tests proving remote-header missing vars appear on server/status entries without credential values.
  - impl: Add a small gateway preflight helper that inspects `RemoteMcpServerConfig.headers` using SL-0 helpers and converts missing placeholders into `ProvisionOutput` or `LifecycleServerOutput`.
  - impl: Run that preflight for configured remote servers, remote manifest servers, and discovered remote server configs before `connect_all(...)`.
  - impl: Catch SL-2 `MissingRemoteHeaderAuthError` as a defensive fallback and convert it to `auth_state="missing_auth"` with `missing_env_vars`.
  - impl: Keep existing manifest `requires_api_key/env_var` behavior unchanged and additive.
  - verify: `uv run pytest tests/test_tools.py -k "remote or provision or connect_server or config_status or health or missing_auth"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-4 — CLI Doctor, Secrets, and Status Surfacing

- **Scope**: Route CLI operator surfaces through the shared remote-header detection path so they report missing env var names consistently without values.
- **Owned files**: `src/pmcp/cli.py`, `src/pmcp/cli_commands/doctor.py`, `src/pmcp/cli_commands/secrets.py`, `tests/test_cli.py`, `tests/test_secrets_command.py`
- **Interfaces provided**: `pmcp doctor` diagnostics for `remote`, `sse`, `http`, and `streamable-http`; `pmcp secrets check` remote header requirements in `required_keys`, `required_by_server`, and `missing_keys`; `pmcp status` JSON/human output for `missing_env_vars`
- **Interfaces consumed**: SL-0 resolver and env lookup, SL-1 config/startup fields, SL-3 live health/config status fields, existing `run_status(...)`, existing `collect_remote_header_diagnostics(...)`, existing `run_secrets_check(...)`
- **Parallel-safe**: no
- **Tasks**:
  - test: Update existing doctor tests to use the shared helper path and add coverage for `sse`, `http`, and `streamable-http` remote types, not only `type="remote"`.
  - test: Add doctor tests proving project/user PMCP env stores satisfy remote header placeholders and secret values are absent from output.
  - test: Add `pmcp secrets check` tests proving remote header placeholders contribute to `required_keys`, `required_by_server`, and `missing_keys`.
  - test: Add `pmcp status --json` and verbose human tests for live snapshots and local fallback output that include `missing_env_vars` names without values.
  - impl: Replace `cli_commands.doctor.ENV_INTERPOLATION_PATTERN` with SL-0 helper usage and keep URL/metadata diagnostics behavior intact.
  - impl: Extend `_extract_required_keys(...)` in `secrets.py` so remote header placeholders are treated as required secret keys while preserving public auth metadata reporting.
  - impl: In `run_status(...)`, surface `missing_env_vars` from live health snapshots and compute local fallback missing remote header vars from loaded configs when no live gateway is reachable.
  - verify: `uv run pytest tests/test_cli.py -k "doctor or status or remote_header or missing_env"`
  - verify: `uv run pytest tests/test_secrets_command.py -k "secrets_check or remote_header or missing"`
  - verify: `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/cli_commands/secrets.py tests/test_cli.py tests/test_secrets_command.py`

### SL-5 — Phase Verification and Closeout

- **Scope**: Verify REMOTE end to end and record the final state without broadening into URL elicitation or redaction-hardening phases.
- **Owned files**: `plans/phase-plan-v4-remote.md`
- **Interfaces provided**: completed REMOTE acceptance checklist, verification results, and any intentional-deviation notes
- **Interfaces consumed**: SL-0 shared resolver/output fields, SL-1 startup classification, SL-2 transport guard, SL-3 gateway behavior, SL-4 CLI behavior, Phase 2 exit criteria from `specs/phase-plans-v4.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Review lane verification results and confirm each Phase 2 exit criterion has named coverage.
  - impl: Mark this plan's interface gates and acceptance criteria complete only after implementation and verification pass.
  - impl: Record any intentional deviations, especially if the shared helper lives in a different existing module after implementation review.
  - impl: Document that no user-facing docs change is required for this phase unless CLI output semantics change beyond structured field additions.
  - verify: `uv run pytest tests/test_auth.py tests/test_client_manager.py tests/test_config_loader.py tests/test_tools.py tests/test_cli.py tests/test_secrets_command.py -k "remote or header or missing_auth or missing_env or secret or status or doctor"`
  - verify: `uv run ruff check src/pmcp/remote_auth.py src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/config/loader.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_client_manager.py tests/test_config_loader.py tests/test_tools.py tests/test_cli.py tests/test_secrets_command.py`
  - verify: `uv run ruff format --check src/pmcp/remote_auth.py src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/config/loader.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_client_manager.py tests/test_config_loader.py tests/test_tools.py tests/test_cli.py tests/test_secrets_command.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_auth.py -k "remote_header or missing_env or auth"`
- `uv run ruff check src/pmcp/remote_auth.py src/pmcp/types.py tests/test_auth.py`
- `uv run pytest tests/test_config_loader.py -k "remote or startup or missing_auth or header"`
- `uv run ruff check src/pmcp/config/loader.py tests/test_config_loader.py`
- `uv run pytest tests/test_client_manager.py -k "remote and (header or sse or streamable)"`
- `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py`
- `uv run pytest tests/test_tools.py -k "remote or provision or connect_server or config_status or health or missing_auth"`
- `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`
- `uv run pytest tests/test_cli.py -k "doctor or status or remote_header or missing_env"`
- `uv run pytest tests/test_secrets_command.py -k "secrets_check or remote_header or missing"`
- `uv run ruff check src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/cli_commands/secrets.py tests/test_cli.py tests/test_secrets_command.py`

Whole-phase regression:

- `uv run pytest tests/test_auth.py tests/test_client_manager.py tests/test_config_loader.py tests/test_tools.py tests/test_cli.py tests/test_secrets_command.py -k "remote or header or missing_auth or missing_env or secret or status or doctor"`
- `uv run ruff check src/pmcp/remote_auth.py src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/config/loader.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_client_manager.py tests/test_config_loader.py tests/test_tools.py tests/test_cli.py tests/test_secrets_command.py`
- `uv run ruff format --check src/pmcp/remote_auth.py src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/config/loader.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_client_manager.py tests/test_config_loader.py tests/test_tools.py tests/test_cli.py tests/test_secrets_command.py`
- `uv run mypy src/pmcp --exclude baml_client`

Release-bound broader checks:

- `uv run pytest -q`
- `uv build`

## Acceptance Criteria

- [x] `${ENV_VAR}` placeholders in remote `headers` are resolved through one helper that also reports missing variables.
- [x] Missing placeholder variables return `auth_state="missing_auth"` and `missing_env_vars` from `gateway.provision`.
- [x] Missing placeholder variables return `auth_state="missing_auth"` and `missing_env_vars` from `gateway.connect_server`.
- [x] PMCP does not send empty `Authorization`, `X-API-Key`, or other placeholder-backed headers caused by missing env vars.
- [x] Literal non-placeholder remote headers are preserved unchanged.
- [x] `pmcp doctor` surfaces missing remote header credentials for `remote`, `sse`, `http`, and `streamable-http` configs without printing values.
- [x] `pmcp secrets check` includes remote header placeholder env vars in required and missing key output without printing values.
- [x] `pmcp status` can surface missing remote header credential names in JSON and human output without printing values.
- [x] SSE remote paths have coverage for present and missing credential placeholders.
- [x] Streamable HTTP remote paths have coverage for present and missing credential placeholders.
- [x] Existing manifest `requires_api_key/env_var` auth behavior remains backward compatible.

## Execution Results

- Implemented shared remote-header placeholder resolution in `src/pmcp/remote_auth.py`.
- Wired missing remote header credential names through startup classification, client transport fail-fast guards, gateway lifecycle/provision/health/config output, and CLI doctor/secrets/status surfaces.
- No user-facing docs change required beyond additive structured fields and existing CLI output semantics.
- Verification passed:
  - `uv run pytest tests/test_auth.py tests/test_client_manager.py tests/test_config_loader.py tests/test_tools.py tests/test_cli.py tests/test_secrets_command.py -k "remote or header or missing_auth or missing_env or secret or status or doctor"`
  - `uv run ruff check src/pmcp/remote_auth.py src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/config/loader.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_client_manager.py tests/test_config_loader.py tests/test_tools.py tests/test_cli.py tests/test_secrets_command.py`
  - `uv run ruff format --check src/pmcp/remote_auth.py src/pmcp/types.py src/pmcp/client/manager.py src/pmcp/config/loader.py src/pmcp/tools/handlers.py src/pmcp/cli.py src/pmcp/cli_commands/doctor.py src/pmcp/cli_commands/secrets.py tests/test_auth.py tests/test_client_manager.py tests/test_config_loader.py tests/test_tools.py tests/test_cli.py tests/test_secrets_command.py`
  - `uv run mypy src/pmcp --exclude baml_client`
