# HOSTPOLICY: Host Policy and Operator Guardrails

## Context

Phase 4 of `specs/phase-plans-v6.md` defines PMCP's host-side safety posture
for exposing a separate tenant code-mode MCP server. PMCP remains the gateway,
policy broker, lifecycle controller, output processor, and operator diagnostic
surface. The companion tenant code-mode server remains responsible for sandbox
execution, tenant authorization, durable logs, artifacts, and isolation.

Prerequisite artifacts are present in the current staged baseline:

- `specs/tenant-code-mode-host-contract.md` freezes PMCP as broker and the
  companion tenant server as execution authority.
- `plans/phase-plan-v6-hostcontract.md` and
  `plans/phase-plan-v6-hostreg.md` are staged and define the contract and
  registration assumptions HOSTPOLICY consumes.
- `plans/phase-plan-v6-hostmeta.md` is staged, while current unstaged edits in
  `README.md`, `src/pmcp/tools/handlers.py`, and `tests/test_tools.py` contain
  in-flight HOSTREG/HOSTMETA tenant registration and task-lifecycle work.

Baseline risk: the worktree is intentionally dirty. HOSTPOLICY execution must
inspect current diffs before editing shared files, preserve existing staged v6
artifacts and unstaged HOSTREG/HOSTMETA source/doc/test changes, and avoid
restaging unrelated work. `specs/phase-plans-v6.md` is staged as a new file, so
it is protected in the index but still not committed.

HOSTPOLICY must not implement SSO, RBAC, billing, tenant identity, sandbox
runtime behavior, durable artifact retention, or broader HTTP authentication.
It should use the existing `PolicyManager`, env-store/auth diagnostics,
gateway lifecycle tools, task tools, `/health`, `/metrics`, truncation, and
redaction surfaces.

## Interface Freeze Gates

- [x] IF-0-HOSTPOLICY-1 - README and SECURITY explicitly distinguish PMCP host
  responsibilities from the companion tenant server's sandbox execution,
  tenant authorization, artifact retention, and isolation responsibilities.
- [x] IF-0-HOSTPOLICY-2 - Tenant policy examples use existing
  `servers`, `tools`, `resources`, `limits`, and `redaction` policy fields to
  allow or deny `tenant-code-mode` and `tenant-code-mode::*` without widening
  access to unrelated downstream MCP servers.
- [x] IF-0-HOSTPOLICY-3 - Tenant auth examples use env placeholders such as
  `${TENANT_CODE_MODE_MCP_TOKEN}` and `${TENANT_CODE_MODE_TENANT_ID}`,
  env-store / `gateway.auth_connect` guidance, and non-secret diagnostics that
  report field or env-var names without printing tenant token values.
- [x] IF-0-HOSTPOLICY-4 - Operator docs name the hosted-deployment guardrails:
  `/mcp` bearer auth, rate limits, `/health` and `/metrics` network exposure,
  task cancellation/disruption rules, transient task state, artifact retention
  boundaries, and residual risks.
- [x] IF-0-HOSTPOLICY-5 - Regression tests cover tenant-shaped server and tool
  policy denial paths plus sandbox-like secret redaction through
  `gateway.invoke`, `gateway.tasks_result`, policy output processing, and auth
  diagnostics.

## Lane Index & Dependencies

- SL-0 - HOSTPOLICY baseline and conflict map; Depends on: (none); Blocks: SL-1, SL-2, SL-3, SL-4; Parallel-safe: no
- SL-1 - Policy model and output-redaction regressions; Depends on: SL-0; Blocks: SL-2, SL-4, SL-5; Parallel-safe: yes
- SL-2 - Gateway policy-denied tenant surfaces; Depends on: SL-0, SL-1; Blocks: SL-4, SL-5; Parallel-safe: no
- SL-3 - Tenant auth placeholder and diagnostic regressions; Depends on: SL-0; Blocks: SL-4, SL-5; Parallel-safe: yes
- SL-4 - README and SECURITY operator guardrails; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: SL-5; Parallel-safe: no
- SL-5 - Phase verification and closeout; Depends on: SL-0, SL-1, SL-2, SL-3, SL-4; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - HOSTPOLICY Baseline and Conflict Map

- **Scope**: Freeze the execution baseline by mapping HOSTPOLICY exit criteria
  to current docs, policy/auth code, gateway handler behavior, tests, and the
  dirty shared-file state.
- **Owned files**: none; read-only survey of `specs/phase-plans-v6.md`,
  `specs/tenant-code-mode-host-contract.md`,
  `plans/phase-plan-v6-hostcontract.md`, `plans/phase-plan-v6-hostreg.md`,
  `plans/phase-plan-v6-hostmeta.md`, `README.md`, `SECURITY.md`,
  `src/pmcp/policy/policy.py`, `src/pmcp/tools/handlers.py`,
  `src/pmcp/auth.py`, `src/pmcp/remote_auth.py`, `tests/test_policy.py`,
  `tests/test_auth.py`, and `tests/test_tools.py`
- **Interfaces provided**: shared-file conflict map, HOSTPOLICY exit-criterion
  checklist, no-new-tenant-identity boundary, policy/auth/doc gap list
- **Interfaces consumed**: IF-0-HOSTCONTRACT-1, IF-0-HOSTREG-2,
  IF-0-HOSTMETA-3, current `GatewayPolicy`, `PolicyManager`,
  `GatewayTools.invoke(...)`, `gateway.tasks_*`, remote header placeholder
  resolution, env-store behavior, current README tenant registration/task docs,
  current SECURITY trust-model language
- **Parallel-safe**: no
- **Tasks**:
  - test: Record `git status --short` and path-scoped status for
    `README.md`, `SECURITY.md`, `src/pmcp/tools/handlers.py`,
    `tests/test_tools.py`, `tests/test_policy.py`, and `tests/test_auth.py`
    before editing shared files.
  - test: Confirm the host contract still states that PMCP brokers execution
    and the companion tenant server owns sandbox execution, tenant auth,
    artifacts, logs, and isolation.
  - test: Map each HOSTPOLICY roadmap exit criterion to an existing docs,
    source, or test surface; identify whether any runtime behavior gap is real
    before changing handlers.
  - impl: Record any overlap with in-flight HOSTREG/HOSTMETA edits in the
    executor closeout; preserve those edits instead of reverting or rewriting
    them.
  - verify: `git status --short`
  - verify: `rg -n "tenant-code-mode|Policy File|gateway.auth_connect|/health|/metrics|rate-limit|tasks_cancel|redaction|policy_denied" README.md SECURITY.md src/pmcp/policy/policy.py src/pmcp/tools/handlers.py tests/test_policy.py tests/test_auth.py tests/test_tools.py`

### SL-1 - Policy Model and Output-Redaction Regressions

- **Scope**: Prove existing policy primitives can express tenant server/tool
  guardrails and redact sandbox-shaped secrets without adding a tenant-specific
  policy model.
- **Owned files**: `src/pmcp/policy/policy.py`, `tests/test_policy.py`
- **Interfaces provided**: tenant-shaped server allow/deny contract,
  tenant-shaped tool allow/deny contract, sandbox-output redaction contract,
  output processing redaction/truncation proof
- **Interfaces consumed**: SL-0 gap list, `GatewayPolicy.servers`,
  `GatewayPolicy.tools`, `GatewayPolicy.resources`, `GatewayPolicy.limits`,
  `GatewayPolicy.redaction`, `PolicyManager.is_server_allowed(...)`,
  `PolicyManager.is_tool_allowed(...)`, `PolicyManager.redact_secrets(...)`,
  `PolicyManager.process_output(...)`, `sanitize_auth_diagnostic(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add `tests/test_policy.py` cases showing a policy can allow only
    `tenant-code-mode`, deny `tenant-code-mode`, deny
    `tenant-code-mode::*`, and allow a narrow pattern such as
    `tenant-code-mode::get_*` without granting unrelated server/tool access.
  - test: Add redaction cases for sandbox-like outputs containing bearer
    headers, API keys, JWTs, auth-bearing artifact URLs, `TENANT_CODE_MODE_*`
    env values, and custom tenant secret patterns.
  - test: Add `process_output(..., redact=True, max_bytes=...)` coverage for
    sandbox-shaped logs so truncation and redaction compose without leaking the
    secret fragments named by the test.
  - impl: Prefer tests-only changes if existing `PolicyManager` behavior
    passes. If a real gap appears, update only the generic policy/redaction
    helper needed to satisfy tenant-shaped tests; do not add tenant-specific
    policy fields.
  - verify: `uv run pytest tests/test_policy.py -k "tenant_code_mode or redacts or process_output"`
  - verify: `git diff --check -- src/pmcp/policy/policy.py tests/test_policy.py`

### SL-2 - Gateway Policy-Denied Tenant Surfaces

- **Scope**: Prove gateway tools consistently refuse policy-denied tenant
  servers and tenant tools before connection, provisioning, invocation, task
  result retrieval, or catalog exposure leaks useful details.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: tenant server denial behavior, tenant tool denial
  behavior, policy-denied audit/auth-state behavior, offline catalog filtering,
  request-capability filtering, task-surface refusal behavior
- **Interfaces consumed**: SL-1 policy contract, current
  `GatewayTools.catalog_search(...)`, `GatewayTools.request_capability(...)`,
  `GatewayTools.provision(...)`, lifecycle config resolution,
  `GatewayTools.invoke(...)`, `GatewayTools.describe(...)`,
  `GatewayTools.tasks_list(...)`, `GatewayTools.tasks_get(...)`,
  `GatewayTools.tasks_result(...)`, `GatewayTools.tasks_cancel(...)`,
  `PolicyManager.is_server_allowed(...)`, `PolicyManager.is_tool_allowed(...)`,
  `PolicyManager.process_output(...)`, `GatewayAuditEvent.auth_state`
- **Parallel-safe**: no, because this lane owns files with current unstaged
  HOSTREG/HOSTMETA edits
- **Tasks**:
  - test: Before editing, inspect current diffs for `src/pmcp/tools/handlers.py`
    and `tests/test_tools.py`; preserve existing tenant registration,
    capability, task, and audit tests.
  - test: Add a configured remote `tenant-code-mode` server plus a policy that
    denies that server; assert `gateway.request_capability` and
    `gateway.catalog_search(include_offline=true)` do not recommend or expose
    tenant cards, and do not connect, provision, or invoke the server.
  - test: Add tenant-specific `gateway.provision`, `gateway.connect_server`,
    `gateway.disconnect_server`, or `gateway.restart_server` coverage proving
    server denial returns `auth_state="policy_denied"` and only non-secret
    diagnostics.
  - test: Add a tenant tool denial regression for
    `tenant-code-mode::run_script` proving `gateway.invoke` returns the
    existing `E402` / `policy_denied` path before dispatch and records a
    sanitized audit event.
  - test: If a denied server can still have task records in memory, add
    `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and
    `gateway.tasks_cancel` regressions proving policy denial blocks further
    task operations or returns an explicit, non-secret refusal.
  - test: Extend tenant-shaped `gateway.tasks_result` coverage so
    sandbox-like result payloads are redacted and truncated through
    `PolicyManager.process_output(...)` without leaking token values in output
    or audit events.
  - impl: Prefer tests-only changes if existing gateway behavior satisfies the
    denial and redaction contracts.
  - impl: If a real bypass appears, add the narrowest generic server-policy
    check to the affected existing gateway path. Do not add a new gateway tool,
    tenant identity model, or sandbox execution branch.
  - verify: `uv run pytest tests/test_tools.py -k "tenant_code_mode or policy_denied or invoke_policy_denied or tasks_result or provision_policy_denied"`
  - verify: `git diff --check -- src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-3 - Tenant Auth Placeholder and Diagnostic Regressions

- **Scope**: Prove tenant auth guidance can rely on existing remote header
  placeholders, env-store lookup, auth-connect direction, and diagnostic
  redaction without printing tenant token values.
- **Owned files**: `src/pmcp/auth.py`, `src/pmcp/remote_auth.py`,
  `tests/test_auth.py`
- **Interfaces provided**: tenant remote-header placeholder contract,
  non-secret missing-env diagnostics, tenant auth diagnostic redaction contract,
  env-store lookup proof
- **Interfaces consumed**: SL-0 gap list,
  `resolve_remote_headers(...)`, `MissingRemoteHeaderAuthError`,
  `build_remote_header_env_lookup(...)`, `sanitize_auth_diagnostic(...)`,
  `redact_auth_url(...)`, `sanitize_url_elicitation_url(...)`,
  `read_env_file(...)`, `write_env_file(...)`, `validate_env_var_name(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add tenant-specific remote-header coverage using
    `Authorization: Bearer ${TENANT_CODE_MODE_MCP_TOKEN}` and
    `X-Tenant-ID: ${TENANT_CODE_MODE_TENANT_ID}`; assert resolved headers are
    used internally while errors/status expose only env var names.
  - test: Add missing-placeholder coverage proving
    `MissingRemoteHeaderAuthError("tenant-code-mode", ...)` reports sorted
    missing env vars and does not include any sample token value.
  - test: Add auth diagnostic redaction coverage for tenant callback URLs,
    artifact URLs, Authorization headers, OAuth-like codes, JWTs, and
    `TENANT_CODE_MODE_*` token strings.
  - test: Confirm env-store lookup precedence remains process, project, then
    user scope and that docs can safely refer to `gateway.auth_connect` or
    env-store by name without printing values.
  - impl: Prefer tests-only changes if existing auth helpers pass. If a real
    gap appears, update the generic auth redaction or remote-header helper only;
    do not add tenant-specific credential storage.
  - verify: `uv run pytest tests/test_auth.py -k "tenant_code_mode or remote_header or auth_redaction or env_lookup"`
  - verify: `git diff --check -- src/pmcp/auth.py src/pmcp/remote_auth.py tests/test_auth.py`

### SL-4 - README and SECURITY Operator Guardrails

- **Scope**: Synthesize the tested policy, auth, and lifecycle behavior into
  concise operator docs for safely brokering a tenant code-mode server through
  PMCP.
- **Owned files**: `README.md`, `SECURITY.md`
- **Interfaces provided**: tenant host responsibility wording, tenant policy
  examples, tenant auth/env-placeholder guidance, hosted operator guardrails,
  residual-risk statement
- **Interfaces consumed**: SL-0 conflict map, SL-1 policy/redaction results,
  SL-2 gateway denial and task-surface behavior, SL-3 auth placeholder and
  diagnostic behavior, `specs/tenant-code-mode-host-contract.md`, existing
  README tenant registration/task lifecycle text, existing SECURITY threat
  model and known limitations
- **Parallel-safe**: no, because this lane is the docs reducer for all producer
  lanes and owns files with current unstaged tenant docs
- **Tasks**:
  - test: Before editing, inspect staged and unstaged README/SECURITY diffs;
    preserve HOSTCONTRACT/HOSTREG/HOSTMETA wording already present.
  - impl: Add a tenant policy example near the README policy section that shows
    allowing only `tenant-code-mode`, denying `tenant-code-mode::*` or selected
    high-risk tools, bounding output, and adding a custom redaction pattern
    without granting unrelated servers.
  - impl: Add tenant auth guidance that uses env placeholders,
    `TENANT_CODE_MODE_MCP_TOKEN`, `TENANT_CODE_MODE_TENANT_ID`,
    env-store / `gateway.auth_connect` direction, and diagnostic expectations
    that mention only env var names or field names.
  - impl: Add operator guardrail wording for hosted deployments: require
    `/mcp` bearer auth, tune `--rate-limit` / `PMCP_RATE_LIMIT`, keep
    `/health` and `/metrics` behind network controls, understand refresh and
    lifecycle cancellation effects, use downstream task IDs with
    `gateway.tasks_cancel`, and keep artifact/log retention in the companion
    tenant server.
  - impl: Update SECURITY known limitations or hardening checklist to state
    that PMCP alone is not multi-tenant production isolation and that tenant
    code-mode hosting still requires companion-server and deployment controls.
  - impl: Do not introduce `gateway.run_code`, `pmcp execute`, SSO, RBAC,
    billing, tenant identity, durable artifact storage, or live hosted service
    requirements.
  - verify: `rg -n "tenant-code-mode|TENANT_CODE_MODE_MCP_TOKEN|TENANT_CODE_MODE_TENANT_ID|gateway.auth_connect|policy_denied|/health|/metrics|rate-limit|gateway.tasks_cancel|artifact retention|multi-tenant" README.md SECURITY.md`
  - verify: `git diff --check -- README.md SECURITY.md`

### SL-5 - Phase Verification and Closeout

- **Scope**: Reduce the policy, gateway, auth, and docs outputs into a
  phase-ready HOSTPOLICY handoff.
- **Owned files**: `plans/phase-plan-v6-hostpolicy.md`
- **Interfaces provided**: completed HOSTPOLICY checklist, verification
  summary, shared-file preservation notes, HOSTSOAK handoff notes
- **Interfaces consumed**: SL-0 baseline/conflict map, SL-1 policy/redaction
  results, SL-2 gateway denial results, SL-3 auth diagnostics, SL-4 docs,
  Phase 4 exit criteria from `specs/phase-plans-v6.md`,
  IF-0-HOSTPOLICY-1 through IF-0-HOSTPOLICY-5
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm every HOSTPOLICY roadmap exit criterion maps to passing
    tests, docs, or an explicit no-op source decision.
  - test: Confirm no lane adds SSO, RBAC, billing, tenant identity, sandbox
    runtime behavior, durable artifact storage, new HTTP auth semantics, or a
    new code-execution gateway tool.
  - test: Confirm docs consume all producer-lane findings and do not race with
    source/test changes.
  - impl: Mark IF-0-HOSTPOLICY gates complete only after targeted tests, docs
    review, and diff checks pass.
  - impl: Record that HOSTSOAK consumes the final policy examples, denial
    tests, auth/redaction coverage, and operator-risk docs for release-gate
    e2e verification.
  - verify: `uv run pytest tests/test_policy.py tests/test_auth.py tests/test_tools.py -k "tenant_code_mode or policy_denied or redacts or remote_header or auth_redaction or tasks_result"`
  - verify: `uv run ruff check src/pmcp/policy src/pmcp/tools src/pmcp/auth.py src/pmcp/remote_auth.py tests/test_policy.py tests/test_auth.py tests/test_tools.py`
  - verify: `uv run ruff format --check src/pmcp/policy src/pmcp/tools src/pmcp/auth.py src/pmcp/remote_auth.py tests/test_policy.py tests/test_auth.py tests/test_tools.py`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `git diff --check`

## Verification

Lane-specific verification:

- `git status --short`
- `rg -n "tenant-code-mode|Policy File|gateway.auth_connect|/health|/metrics|rate-limit|tasks_cancel|redaction|policy_denied" README.md SECURITY.md src/pmcp/policy/policy.py src/pmcp/tools/handlers.py tests/test_policy.py tests/test_auth.py tests/test_tools.py`
- `uv run pytest tests/test_policy.py -k "tenant_code_mode or redacts or process_output"`
- `uv run pytest tests/test_tools.py -k "tenant_code_mode or policy_denied or invoke_policy_denied or tasks_result or provision_policy_denied"`
- `uv run pytest tests/test_auth.py -k "tenant_code_mode or remote_header or auth_redaction or env_lookup"`
- `rg -n "tenant-code-mode|TENANT_CODE_MODE_MCP_TOKEN|TENANT_CODE_MODE_TENANT_ID|gateway.auth_connect|policy_denied|/health|/metrics|rate-limit|gateway.tasks_cancel|artifact retention|multi-tenant" README.md SECURITY.md`
- `git diff --check -- src/pmcp/policy/policy.py src/pmcp/tools/handlers.py src/pmcp/auth.py src/pmcp/remote_auth.py tests/test_policy.py tests/test_auth.py tests/test_tools.py README.md SECURITY.md`

Whole-phase regression:

- `uv run pytest tests/test_policy.py tests/test_auth.py tests/test_tools.py -k "tenant_code_mode or policy_denied or redacts or remote_header or auth_redaction or tasks_result"`
- `uv run ruff check src/pmcp/policy src/pmcp/tools src/pmcp/auth.py src/pmcp/remote_auth.py tests/test_policy.py tests/test_auth.py tests/test_tools.py`
- `uv run ruff format --check src/pmcp/policy src/pmcp/tools src/pmcp/auth.py src/pmcp/remote_auth.py tests/test_policy.py tests/test_auth.py tests/test_tools.py`
- `uv run mypy src/pmcp --exclude baml_client`
- `git diff --check`

No live hosted tenant service, cloud credential, companion package publication,
SSO provider, RBAC provider, billing system, or sandbox runtime is required for
HOSTPOLICY.

## Acceptance Criteria

- [x] README and SECURITY distinguish PMCP host policy duties from the
  companion tenant server's sandbox execution, tenant authorization, artifact
  retention, and isolation duties.
- [x] Policy examples demonstrate how to allow or deny `tenant-code-mode` and
  `tenant-code-mode::*` without exposing unrelated downstream MCP servers.
- [x] Auth guidance uses env placeholders and env-store /
  `gateway.auth_connect` flows without printing tenant token values.
- [x] Operator docs cover `/mcp` bearer auth, rate limits, `/health` and
  `/metrics` exposure, task cancellation, transient task records, artifact
  retention boundaries, and hosted residual risks.
- [x] Tests cover tenant-shaped server and tool policy denial paths.
- [x] Tests cover redaction of sandbox-like bearer tokens, API keys, JWTs,
  artifact URLs, and tenant token fields in policy output processing and auth
  diagnostics.
- [x] HOSTPOLICY introduces no new PMCP code-execution gateway tool, tenant
  identity layer, SSO/RBAC/billing behavior, sandbox runtime, or durable
  artifact store.

## Closeout Notes

- HOSTPOLICY used existing `GatewayPolicy`, `PolicyManager`, env-store,
  remote-header placeholder, lifecycle, invoke, and task surfaces. The only
  runtime changes are generic server-policy checks before live catalog exposure,
  configured capability recommendations, and downstream task operations, plus
  generic tenant-id auth diagnostic redaction.
- In-flight HOSTREG/HOSTMETA edits in `README.md`,
  `src/pmcp/tools/handlers.py`, and `tests/test_tools.py` were preserved and
  extended rather than reverted.
- HOSTSOAK should consume the final tenant policy examples, denied catalog /
  capability / lifecycle / invoke / task regressions, tenant auth placeholder
  and diagnostic redaction tests, and README/SECURITY operator-risk guidance for
  deterministic release-gate e2e verification.
