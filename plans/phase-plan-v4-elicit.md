# ELICIT: URL Elicitation Contract

## Context

Phase 3 of `specs/phase-plans-v4.md` builds on IF-0-STORE-1 and tightens the
URL-mode elicitation path introduced by the v3 AUTH work. The current gateway
surface already has `AuthConnectInput.auth_mode="url_elicitation"` and rejects a
non-empty `credential`, while `parse_url_elicitation_error(...)` converts MCP
URLElicitationRequiredError payloads into display-safe `UrlElicitationInfo`
objects.

The remaining production-readiness gap is the command flow. `pmcp auth connect`
currently treats `gateway.provision` URL elicitation output as immediately
acknowledged by calling `gateway.auth_connect(... consent_acknowledged=True)`
before the user has completed the provider flow. This phase must keep URL-mode
consent out of band: show the sanitized URL and `elicitation_id`, require the
user to complete the provider flow, then record acknowledgement only through an
explicit CLI or gateway call. PMCP still must not accept OAuth codes, passwords,
refresh tokens, or any provider credential material for URL-mode elicitation.

## Interface Freeze Gates

- [x] IF-0-ELICIT-1 — `pmcp.auth.sanitize_url_elicitation_url(url)` accepts only absolute `https://` URLs and loopback `http://` URLs, returns `redact_auth_url(url)` for accepted URLs, and rejects invalid, relative, non-HTTP(S), and non-loopback `http://` URLs.
- [x] IF-0-ELICIT-2 — `pmcp.auth.parse_url_elicitation_error(payload)` emits `UrlElicitationInfo(elicitation_id, url, message, next_step)` only after IF-0-ELICIT-1 validation; emitted URLs contain no secret-bearing query values.
- [x] IF-0-ELICIT-3 — `gateway.auth_connect(auth_mode="url_elicitation")` requires `elicitation_id` and `consent_acknowledged=true`, rejects any non-empty `credential`, validates optional `elicitation_url` through IF-0-ELICIT-1, and never stores provider credentials.
- [x] IF-0-ELICIT-4 — Gateway URL-elicitation outputs from `gateway.invoke`, `gateway.provision`, and `gateway.connect_server` use `auth_state="elicitation_required"`, `auth_mode="url_elicitation"` where the response model exposes `auth_mode`, `url_elicitations`, and a `next_step` that asks for explicit post-consent acknowledgement.
- [x] IF-0-ELICIT-5 — `pmcp auth connect` displays URL-mode elicitation details without acknowledging them, and `pmcp auth acknowledge <server> --elicitation-id <id>` is the explicit CLI acknowledgement path with text and JSON output.

## Lane Index & Dependencies

- SL-0 — Shared URL elicitation contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3; Parallel-safe: no
- SL-1 — Gateway URL-mode surfacing and acknowledgement; Depends on: SL-0; Blocks: SL-2, SL-3; Parallel-safe: yes
- SL-2 — CLI display and acknowledgement flow; Depends on: SL-0, SL-1; Blocks: SL-3; Parallel-safe: yes
- SL-3 — Phase verification and closeout; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Shared URL Elicitation Contract

- **Scope**: Freeze the safe URL-mode elicitation data contract and URL validation helper used by gateway and CLI surfaces.
- **Owned files**: `src/pmcp/auth.py`, `src/pmcp/types.py`, `tests/test_auth.py`
- **Interfaces provided**: `sanitize_url_elicitation_url(url)`, `parse_url_elicitation_error(payload)`, `UrlElicitationInfo`, `AuthConnectInput.auth_mode`, `AuthConnectInput.elicitation_id`, `AuthConnectInput.elicitation_url`, `AuthConnectInput.consent_acknowledged`, `AuthConnectOutput.url_elicitation`, widened `ProvisionOutput.auth_mode` literal including `"url_elicitation"`
- **Interfaces consumed**: existing `redact_auth_url(url)`, existing `sanitize_auth_diagnostic(value)`, existing `AuthState` literal `"elicitation_required"`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add `test_sanitize_url_elicitation_url_accepts_https_and_redacts_query_secrets` covering auth-code, token, and refresh-token query values.
  - test: Add `test_sanitize_url_elicitation_url_allows_loopback_http` covering `localhost`, `127.0.0.1`, and `[::1]`.
  - test: Add `test_sanitize_url_elicitation_url_rejects_non_loopback_http_and_invalid_urls` covering relative URLs, `ftp://`, `http://auth.example/...`, and malformed URLs.
  - test: Update `test_parse_url_elicitation_error_redacts_url` and add invalid URL coverage proving rejected entries are omitted.
  - impl: Add `sanitize_url_elicitation_url(url)` near the existing auth URL helpers, using structured URL parsing rather than string prefix checks.
  - impl: Update `parse_url_elicitation_error(...)` to call the new helper and keep its existing tolerant JSON/error-payload parsing behavior.
  - impl: Widen `ProvisionOutput.auth_mode` to allow `"url_elicitation"` so gateway provisioning can expose the mode without an ad hoc field.
  - verify: `uv run pytest tests/test_auth.py -k "elicitation or url"`
  - verify: `uv run ruff check src/pmcp/auth.py src/pmcp/types.py tests/test_auth.py`

### SL-1 — Gateway URL-Mode Surfacing and Acknowledgement

- **Scope**: Make gateway URL-mode outputs and acknowledgement behavior explicit, non-secret, and post-consent only.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: `gateway.auth_connect` URL-mode acknowledgement contract; `gateway.invoke`, `gateway.provision`, and `gateway.connect_server` URL-elicitation outputs with sanitized `url_elicitations`; no credential/code/password/refresh-token acceptance through URL mode
- **Interfaces consumed**: SL-0 `sanitize_url_elicitation_url(url)`, SL-0 `parse_url_elicitation_error(payload)`, SL-0 `ProvisionOutput.auth_mode="url_elicitation"`, existing `AuthConnectInput`, existing `AuthConnectOutput`, existing `UrlElicitationInfo`, existing `_lifecycle_output(...)`, existing `ProvisionOutput.url_elicitations`, existing `LifecycleServerOutput.url_elicitations`, existing `InvokeOutput.url_elicitations`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Extend `test_auth_connect_url_elicitation_refuses_credential` to parameterize credential-like values such as OAuth codes, passwords, bearer tokens, and refresh tokens without leaking those values in the response.
  - test: Add `gateway.auth_connect` URL-mode tests proving missing `elicitation_id` or missing `consent_acknowledged=true` returns `ok=False`, `auth_state="elicitation_required"`, and a post-consent next step.
  - test: Add `gateway.auth_connect` URL-mode tests proving optional `elicitation_url` is redacted on success and non-loopback `http://` URLs are rejected.
  - test: Add provisioning and lifecycle tests proving URL elicitation errors are converted to `auth_state="elicitation_required"`, `auth_mode="url_elicitation"` where available, sanitized `url_elicitations`, and explicit acknowledgement `next_step`.
  - impl: Import and use SL-0 URL validation for the optional `elicitation_url` returned from `gateway.auth_connect`.
  - impl: Preserve the existing URL-mode credential refusal before any acknowledgement handling and keep API-key `auth_mode` behavior unchanged.
  - impl: Convert URL elicitation errors in remote provisioning/connect paths the same way `gateway.invoke` already does, without swallowing unrelated connection failures.
  - impl: Ensure all URL-mode gateway messages describe PMCP acknowledgement only, not provider consent completion or credential storage.
  - verify: `uv run pytest tests/test_tools.py -k "elicitation or auth_connect or provision or connect_server"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-2 — CLI Display and Acknowledgement Flow

- **Scope**: Stop `pmcp auth connect` from auto-acknowledging URL elicitation and add an explicit CLI acknowledgement command.
- **Owned files**: `src/pmcp/cli.py`, `tests/test_cli.py`
- **Interfaces provided**: `pmcp auth connect <server>` display-only behavior for URL elicitation; `pmcp auth acknowledge <server> --elicitation-id <id> [--elicitation-url <url>] [--json]`; text and JSON output shapes for both paths
- **Interfaces consumed**: SL-0 sanitized `UrlElicitationInfo` shape, SL-1 `gateway.auth_connect` acknowledgement contract, SL-1 `gateway.provision` URL-mode output contract, existing `_extract_tool_payload(...)`, existing `_redact_url_credentials(url)`, existing gateway connection setup in `run_auth_connect(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add parser coverage for `pmcp auth acknowledge remote-auth --elicitation-id consent-1 --json`.
  - test: Add `run_auth_connect` URL-mode tests proving it prints the sanitized URL, `elicitation_id`, and next step but does not call `gateway.auth_connect`.
  - test: Add `run_auth_connect --json` URL-mode tests proving JSON output includes the provision payload and no acknowledgement result.
  - test: Add `run_auth_acknowledge` text and JSON tests proving it calls `gateway.auth_connect` with `auth_mode="url_elicitation"`, `elicitation_id`, optional sanitized `elicitation_url`, and `consent_acknowledged=True`.
  - test: Add CLI tests proving `--credential` is rejected for URL-mode display/acknowledgement paths and credential-like strings do not appear in output.
  - impl: Add an `auth acknowledge` subcommand in `parse_args(...)` with `server_name`, required `--elicitation-id`, optional `--elicitation-url`, and `--json`.
  - impl: Refactor the gateway-call setup shared by `run_auth_connect(...)` and the new acknowledgement runner only as much as needed to avoid duplicating fragile connection code.
  - impl: In `run_auth_connect(...)`, when `gateway.provision` returns URL elicitation, print/display the provider URL details and return without calling `gateway.auth_connect`.
  - impl: Route `async_main(...)` to the new acknowledgement runner and keep existing API-key `pmcp auth connect` behavior unchanged.
  - verify: `uv run pytest tests/test_cli.py -k "auth_connect or auth_acknowledge or elicitation"`
  - verify: `uv run ruff check src/pmcp/cli.py tests/test_cli.py`

### SL-3 — Phase Verification and Closeout

- **Scope**: Verify ELICIT end to end and record the final phase state without broadening into Phase 4 redaction hardening or Phase 6 documentation release work.
- **Owned files**: `plans/phase-plan-v4-elicit.md`
- **Interfaces provided**: completed ELICIT acceptance checklist, verification results, and intentional-deviation notes
- **Interfaces consumed**: SL-0 URL validation and type contracts, SL-1 gateway behavior, SL-2 CLI behavior, Phase 3 exit criteria from `specs/phase-plans-v4.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Review lane verification results and confirm each Phase 3 exit criterion has named coverage.
  - impl: Mark this plan's interface gates and acceptance criteria complete only after implementation and verification pass.
  - impl: Record any intentional deviations, especially if the CLI acknowledgement command name changes during implementation review.
  - impl: Record that no README/SECURITY/CHANGELOG update is required in this phase unless CLI behavior changes beyond help text and tested output; roadmap-level docs remain in Phase 6.
  - verify: `uv run pytest tests/test_cli.py tests/test_tools.py tests/test_auth.py -k "elicitation or consent or auth_connect or auth_acknowledge"`
  - verify: `uv run ruff check src/pmcp/auth.py src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli.py tests/test_auth.py tests/test_tools.py tests/test_cli.py`
  - verify: `uv run ruff format --check src/pmcp/auth.py src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli.py tests/test_auth.py tests/test_tools.py tests/test_cli.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`

## Verification

Lane-specific verification:

- `uv run pytest tests/test_auth.py -k "elicitation or url"`
- `uv run ruff check src/pmcp/auth.py src/pmcp/types.py tests/test_auth.py`
- `uv run pytest tests/test_tools.py -k "elicitation or auth_connect or provision or connect_server"`
- `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`
- `uv run pytest tests/test_cli.py -k "auth_connect or auth_acknowledge or elicitation"`
- `uv run ruff check src/pmcp/cli.py tests/test_cli.py`

Whole-phase regression:

- `uv run pytest tests/test_cli.py tests/test_tools.py tests/test_auth.py -k "elicitation or consent or auth_connect or auth_acknowledge"`
- `uv run ruff check src/pmcp/auth.py src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli.py tests/test_auth.py tests/test_tools.py tests/test_cli.py`
- `uv run ruff format --check src/pmcp/auth.py src/pmcp/types.py src/pmcp/tools/handlers.py src/pmcp/cli.py tests/test_auth.py tests/test_tools.py tests/test_cli.py`
- `uv run mypy src/pmcp --exclude baml_client`

Release-bound broader checks:

- `uv run pytest -q`
- `uv build`

## Closeout Notes

- Verification passed: lane-specific pytest selections for `tests/test_auth.py`, `tests/test_tools.py`, and `tests/test_cli.py`.
- Verification passed: whole-phase regression pytest selection, ruff check, ruff format check, and mypy.
- Verification passed: release-bound `uv run pytest -q` and `uv build`.
- Intentional deviations: none; the CLI acknowledgement command is `pmcp auth acknowledge <server> --elicitation-id <id>`.
- Documentation scope: no README, SECURITY, or CHANGELOG update is required in this phase; roadmap-level docs remain Phase 6.

## Acceptance Criteria

- [x] `pmcp auth connect` no longer calls `gateway.auth_connect(auth_mode="url_elicitation", consent_acknowledged=true)` before the user completes the provider flow.
- [x] `pmcp auth connect` text output for URL-mode elicitation includes sanitized URL, `elicitation_id`, and explicit next step.
- [x] `pmcp auth connect --json` output for URL-mode elicitation includes structured sanitized URL details without an acknowledgement result.
- [x] `pmcp auth acknowledge <server> --elicitation-id <id>` records post-consent acknowledgement through `gateway.auth_connect(auth_mode="url_elicitation", consent_acknowledged=true)`.
- [x] `pmcp auth acknowledge --json` emits structured JSON for successful and failed acknowledgement results.
- [x] `gateway.auth_connect(auth_mode="url_elicitation")` rejects credentials, OAuth codes, passwords, bearer tokens, and refresh-token material through any non-empty `credential`.
- [x] URL-mode gateway and CLI outputs include sanitized URL, `elicitation_id`, and next step without secret-bearing query values.
- [x] Non-loopback `http://` elicitation URLs are rejected; loopback HTTP remains allowed for local development.
- [x] Existing API-key `gateway.auth_connect` and `pmcp auth connect` behavior remains backward compatible.
