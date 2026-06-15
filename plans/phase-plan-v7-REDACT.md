---
phase_loop_plan_version: 1
phase: REDACT
roadmap: specs/phase-plans-v7.md
roadmap_sha256: f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5
---

# REDACT: Redaction Hardening

## Context

Phase REDACT implements Phase 1 of `specs/phase-plans-v7.md`: make secret redaction a single, truncation-independent chokepoint for outbound result, summary, task, and log/error surfaces. The roadmap hash was verified from `specs/phase-plans-v7.md` as `f91921d76188ed0617760861380a48ee907bd53db51fe25f8d6c71ad91dff4d5`, and the canonical `.phase-loop/` state marks REDACT as the current unplanned phase.

Current code already has the main seams this phase should reuse: `PolicyManager.redact_secrets(...)`, `PolicyManager.process_output(...)`, `sanitize_auth_diagnostic(...)`, `GatewayTools._sanitize_error(...)`, `GatewayTools._scrub_sensitive_text(...)`, `gateway.invoke`, `gateway.tasks_result`, and the reconnect warning in `MCPClientManager._reconnect_loop(...)`. Execution should tighten those existing seams instead of adding a parallel sanitizer or new policy surface.

## Interface Freeze Gates

- [ ] IF-0-REDACT-1 - `PolicyManager.redact_secrets(output: str) -> str` is the canonical secret redaction function for outbound text. It uses one shared default pattern source, delegates URL/header/JWT/free-text diagnostics through `sanitize_auth_diagnostic(...)`, covers bare `sk-...`, `ghp_...`, and `github_pat_...` tokens, and is independent of truncation. Every REDACT-owned outbound field (`result`, `summary`, `task.status_message`, `task.raw`, gateway task results, remote connect failure messages, reconnect logs, and feedback text) either calls this function directly or calls a helper that demonstrably delegates to it. Task/code-mode result paths default to redaction on when the caller omits `redact_secrets`.

## Lane Index & Dependencies

- SL-0 - Canonical policy redactor and result shaping; Depends on: (none); Blocks: SL-1, SL-2, SL-3; Parallel-safe: no
- SL-1 - Gateway task and log redaction wiring; Depends on: SL-0; Blocks: SL-3; Parallel-safe: no
- SL-2 - Auth diagnostic keyword coverage; Depends on: SL-0; Blocks: SL-3; Parallel-safe: yes
- SL-3 - REDACT regression sweep and closeout; Depends on: SL-0, SL-1, SL-2; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Canonical Policy Redactor and Result Shaping

- **Scope**: Freeze the canonical redaction contract in `PolicyManager` and prove truncation, summary generation, and default pattern matching cannot leak known token forms.
- **Owned files**: `src/pmcp/policy/policy.py`, `tests/test_policy.py`
- **Interfaces provided**: IF-0-REDACT-1 canonical `PolicyManager.redact_secrets(output: str) -> str`, shared default redaction pattern source, redaction-after-truncation contract, redacted-summary contract, independent `redact` and `max_bytes` behavior
- **Interfaces consumed**: pre-existing auth diagnostic sanitizer, `PolicyManager.truncate_output(...)`, `PolicyManager.process_output(...)`, `GatewayPolicy.redaction.patterns`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add failing-first `tests/test_policy.py` coverage for bare `sk-...`, `ghp_...`, and `github_pat_...` tokens without key names, key/value forms, authorization headers, auth-bearing URLs, JWTs, and custom policy patterns all passing through `PolicyManager.redact_secrets(...)`.
  - test: Add a truncated-output regression where the first line contains a secret and `process_output(..., redact=True, max_bytes=...)` returns no secret in either `result` or `summary` while preserving accurate `truncated` and `raw_size` values.
  - test: Add a regression proving `redact=True` does not impose a 400-character cap and that the only output cap comes from `max_bytes` or the policy limit.
  - impl: Move the bare-token patterns currently embedded in `GatewayTools._scrub_sensitive_text(...)` into the shared policy redaction pattern source or another policy-owned constant consumed by `PolicyManager.redact_secrets(...)`.
  - impl: Build summaries from the post-redaction text, not the pre-redaction raw output, without changing truncation byte semantics beyond the required redaction/truncation decoupling.
  - verify: `uv run pytest tests/test_policy.py -k "redact or summary or secret or process_output"`
  - verify: `git diff --check -- src/pmcp/policy/policy.py tests/test_policy.py`

### SL-1 - Gateway Task and Log Redaction Wiring

- **Scope**: Route gateway task outputs, task metadata, feedback scrubbing, and remote/reconnect error messages through the canonical policy redactor without changing task response shapes.
- **Owned files**: `src/pmcp/tools/handlers.py`, `src/pmcp/client/manager.py`, `tests/test_tools.py`
- **Interfaces provided**: redacted `gateway.invoke` task metadata, redacted `gateway.tasks_result` payload and task metadata, redacted feedback text, sanitized remote connect failure messages, sanitized reconnect warning log
- **Interfaces consumed**: IF-0-REDACT-1, `GatewayTools._sanitize_error(...)`, `GatewayTools._scrub_sensitive_text(...)`, `GatewayTools.invoke(...)`, `GatewayTools.tasks_result(...)`, `McpTaskRecord.status_message`, `McpTaskRecord.raw`, `MCPClientManager._reconnect_loop(...)`
- **Parallel-safe**: no
- **Tasks**:
  - test: Add `tests/test_tools.py` regressions where `gateway.invoke` returns a task record with secrets in `task.status_message` and `task.raw`; assert the returned `InvokeOutput.task`, audit events, and serialized output contain no raw secret when options omit `redact_secrets` and when `redact_secrets=True` is explicit.
  - test: Add `gateway.tasks_result` regressions proving `result`, `summary`, `task.status_message`, and `task.raw` are redacted by default for task/code-mode paths and remain redacted when `max_output_chars` truncates the result.
  - test: Add log/error regressions for remote connect failure and reconnect warning messages containing auth-bearing URLs or tokens; assert captured logs and returned errors contain only sanitized values.
  - impl: Change `gateway.invoke` and `gateway.tasks_result` option handling so task/code-mode result paths default `redact_secrets` to true when omitted, while preserving explicit output-size options and existing response fields.
  - impl: Sanitize `McpTaskRecord` metadata before exposing it through gateway output surfaces; do not mutate internal raw task records unless an existing helper already treats them as presentation objects.
  - impl: Update `GatewayTools._scrub_sensitive_text(...)` to consume IF-0-REDACT-1 for token redaction, keeping only feedback-specific email redaction locally if still needed.
  - impl: Route `MCPClientManager._reconnect_loop(...)` failure logging at the reconnect warning through the same sanitized error text used by gateway remote connect failures.
  - verify: `uv run pytest tests/test_tools.py -k "redact or summary or tasks_result or status_message or remote connect or reconnect"`
  - verify: `git diff --check -- src/pmcp/tools/handlers.py src/pmcp/client/manager.py tests/test_tools.py`

### SL-2 - Auth Diagnostic Keyword Coverage

- **Scope**: Extend the free-text auth diagnostic redactor so IF-0-REDACT-1 inherits the roadmap-required keyword coverage for diagnostic and log strings.
- **Owned files**: `src/pmcp/auth.py`, `tests/test_auth.py`
- **Interfaces provided**: expanded auth diagnostic keyword coverage for `session`, `sid`, `cookie`, `set-cookie`, `refresh_token`, `client_secret`, `access_token`, `id_token`, `jwt`, `assertion`, and `saml`
- **Interfaces consumed**: IF-0-REDACT-1, existing URL redaction via `redact_auth_url(...)`, existing `_JWT_RE`, existing auth URL/query key redaction tests
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add focused `tests/test_auth.py` matrix coverage for the roadmap keyword list in free text, header-like text, query strings, fragments, and auth challenge diagnostics.
  - test: Include representative values for `cookie`, `set-cookie`, `client_secret`, `access_token`, `id_token`, `refresh_token`, `assertion`, `saml`, and JWT-like values, asserting each raw value is absent from `sanitize_auth_diagnostic(...)` output.
  - impl: Extend only the generic auth diagnostic regex/key sets needed for the new cases; keep diagnostic output capped and non-secret.
  - verify: `uv run pytest tests/test_auth.py -k "redaction or diagnostic or sanitize_auth"`
  - verify: `git diff --check -- src/pmcp/auth.py tests/test_auth.py`

### SL-3 - REDACT Regression Sweep and Closeout

- **Scope**: Run the REDACT verification set, confirm IF-0-REDACT-1 is fully produced, and prepare runner closeout evidence without owning additional source files.
- **Owned files**: none
- **Interfaces provided**: REDACT verification evidence, IF-0-REDACT-1 completion checklist, phase-owned dirty-path inventory
- **Interfaces consumed**: IF-0-REDACT-1, SL-0 test results, SL-1 test results, SL-2 test results, roadmap REDACT exit criteria
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm no lane-owned file overlaps another lane-owned file and no executor touched files outside the active REDACT ownership set.
  - test: Confirm `summary`, `task.status_message`, `task.raw`, remote connect failure messages, reconnect logs, bare token patterns, and auth diagnostic keyword coverage each have a failing-first regression in the lane-owned tests.
  - verify: `uv run pytest tests/test_policy.py tests/test_tools.py -k "redact or summary or task or secret"`
  - verify: `uv run pytest tests/test_auth.py -k "redaction or diagnostic or sanitize_auth"`
  - verify: `TMPDIR=/var/tmp uv run pytest`
  - verify: `uv run ruff check .`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `git status --short`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
uv run pytest tests/test_policy.py tests/test_tools.py -k "redact or summary or task or secret"
uv run pytest tests/test_auth.py -k "redaction or diagnostic or sanitize_auth"
TMPDIR=/var/tmp uv run pytest
uv run ruff check .
uv run mypy src/pmcp --exclude baml_client
git status --short
```

## Acceptance Criteria

- [ ] `summary` is built from post-redaction text; a truncated result with a secret on line 1 returns no secret in `summary`.
- [ ] `task.status_message` and `task.raw` are redacted with `redact_secrets=True` on `gateway.invoke` and `gateway.tasks_result`.
- [ ] `redact=True` no longer caps output at 400 chars; redaction and truncation are independent and `truncated`/`raw_size` remain accurate.
- [ ] Bare `sk-...`, `ghp_...`, and `github_pat_...` tokens are redacted in results via the unified pattern set.
- [ ] `_scrub_sensitive_text(...)` and `DEFAULT_REDACTION_PATTERNS` share the canonical redaction source instead of maintaining separate token pattern lists.
- [ ] Remote-connect-failure log lines and reconnect warning logs route through sanitized redaction helpers and do not expose raw URLs, bearer values, or tokens.
- [ ] `redact_secrets` defaults to ON for task/code-mode result paths without changing non-task response shapes.
- [ ] The free-text diagnostic redactor covers `session`, `sid`, `cookie`, `set-cookie`, `refresh_token`, `client_secret`, `access_token`, `id_token`, `jwt`, `assertion`, and `saml`.
- [ ] `ruff`, CI mypy baseline (`uv run mypy src/pmcp --exclude baml_client`), and full `pytest` pass with `TMPDIR=/var/tmp` for the full test run.
