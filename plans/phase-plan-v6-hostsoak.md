# HOSTSOAK: Host Integration Soak and Release Gate

## Context

Phase 5 of `specs/phase-plans-v6.md` is the release gate for PMCP's tenant
code-mode host-readiness work. The phase must prove that PMCP can broker a
separate tenant code-mode MCP server through existing gateway discovery,
describe, invoke, task, policy, redaction, and docs surfaces without owning the
sandbox runtime.

The current baseline already contains staged v6 artifacts and in-flight
implementation from earlier HOST phases:

- `specs/phase-plans-v6.md`, `specs/tenant-code-mode-host-contract.md`, and
  `plans/phase-plan-v6-hostcontract.md`,
  `plans/phase-plan-v6-hostreg.md`, `plans/phase-plan-v6-hostmeta.md`, and
  `plans/phase-plan-v6-hostpolicy.md` are staged.
- `README.md`, `SECURITY.md`, `src/pmcp/auth.py`,
  `src/pmcp/tools/handlers.py`, `tests/test_auth.py`,
  `tests/test_client_manager.py`, `tests/test_integration.py`,
  `tests/test_manifest.py`, `tests/test_policy.py`, `tests/test_tools.py`, and
  `uv.lock` have unstaged changes at planning time.
- Existing tenant-shaped coverage already appears in `tests/test_manifest.py`,
  `tests/test_client_manager.py`, `tests/test_tools.py`,
  `tests/test_policy.py`, `tests/test_auth.py`, and
  `tests/test_integration.py`.

HOSTSOAK should be a reducer phase over those outputs. It should add a
deterministic final soak matrix, fill release docs, and reconcile the roadmap
only after the contract is proven. If the soak exposes a missing host contract,
that is a blocker for HOSTCONTRACT, HOSTREG, HOSTMETA, or HOSTPOLICY rather
than permission to add a new PMCP execution subsystem.

This phase must not depend on live hosted sandbox infrastructure, real cloud
credentials, the companion server's final package name, a PMCP-owned sandbox
runtime, or version/publish work. `pyproject.toml`, `src/pmcp/__init__.py`, and
release publishing are downstream of this soak unless the user explicitly asks
for a version bump.

## Interface Freeze Gates

- [x] IF-0-HOSTSOAK-1 - A deterministic mock tenant code-mode MCP fixture
  exercises discovery, describe, invoke, task polling, result retrieval,
  cancellation, policy denial, and redaction through existing PMCP gateway
  surfaces.
- [x] IF-0-HOSTSOAK-2 - End-to-end tests prove a mobile or no-local-shell
  client can discover a hosted tenant code-mode path from PMCP and submit a
  task-capable sandbox run through `gateway.invoke` without local CLI execution
  or live infrastructure.
- [x] IF-0-HOSTSOAK-3 - README contains a concise operator and user flow for
  PMCP plus the companion tenant code-mode MCP server, with PMCP described as
  broker and the companion server described as execution authority.
- [x] IF-0-HOSTSOAK-4 - CHANGELOG records the host integration capability with
  precise wording that PMCP brokers tenant code-mode execution but does not run
  scripts itself.
- [x] IF-0-HOSTSOAK-5 - The full release verification matrix passes before any
  version bump or publish step is attempted.
- [x] IF-0-HOSTSOAK-6 - The v6 roadmap and this plan are reconciled with final
  evidence, and any new runtime-contract blocker is routed back to the earlier
  phase that owns the contract.

## Lane Index & Dependencies

- SL-0 - Baseline reconciliation and soak scope freeze; Depends on: (none); Blocks: SL-1, SL-2, SL-3; Parallel-safe: no
- SL-1 - Dedicated mock tenant soak suite; Depends on: SL-0; Blocks: SL-2, SL-3, SL-4, SL-5; Parallel-safe: no
- SL-2 - Conditional shared contract gap repair; Depends on: SL-0, SL-1; Blocks: SL-3, SL-4, SL-5; Parallel-safe: no
- SL-3 - README and CHANGELOG release docs; Depends on: SL-0, SL-1, SL-2; Blocks: SL-4, SL-5; Parallel-safe: no
- SL-4 - Release verification matrix; Depends on: SL-1, SL-2, SL-3; Blocks: SL-5; Parallel-safe: no
- SL-5 - Roadmap and plan closeout reducer; Depends on: SL-0, SL-1, SL-2, SL-3, SL-4; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Baseline Reconciliation and Soak Scope Freeze

- **Scope**: Freeze the release-gate baseline by reconciling staged HOST phase
  artifacts, unstaged earlier-phase edits, and HOSTSOAK's no-new-runtime
  boundary before adding final soak coverage.
- **Owned files**: none; read-only survey of `specs/phase-plans-v6.md`,
  `specs/tenant-code-mode-host-contract.md`,
  `plans/phase-plan-v6-hostcontract.md`, `plans/phase-plan-v6-hostreg.md`,
  `plans/phase-plan-v6-hostmeta.md`, `plans/phase-plan-v6-hostpolicy.md`,
  `README.md`, `SECURITY.md`, `CHANGELOG.md`, `src/pmcp/client/manager.py`,
  `src/pmcp/tools/handlers.py`, `src/pmcp/policy/policy.py`,
  `src/pmcp/auth.py`, `tests/test_manifest.py`,
  `tests/test_client_manager.py`, `tests/test_tools.py`,
  `tests/test_policy.py`, `tests/test_auth.py`, and
  `tests/test_integration.py`
- **Interfaces provided**: HOSTSOAK gap list, dirty-file conflict map,
  predecessor-gate checklist, no-live-infrastructure boundary,
  no-version-bump boundary
- **Interfaces consumed**: IF-0-HOSTCONTRACT-1, IF-0-HOSTREG-2,
  IF-0-HOSTMETA-3, IF-0-HOSTPOLICY-4, Phase 5 exit criteria from
  `specs/phase-plans-v6.md`, current tenant-code-mode tests and docs
- **Parallel-safe**: no
- **Tasks**:
  - test: Record `git status --short` and path-scoped status for all HOSTSOAK
    key files before editing, especially files with staged and unstaged
    changes.
  - test: Confirm the staged HOSTCONTRACT, HOSTREG, HOSTMETA, and HOSTPOLICY
    plan closeouts and current source/tests satisfy their advertised producer
    interfaces before treating HOSTSOAK as execution-ready.
  - test: Confirm the host contract still says PMCP brokers execution and the
    companion tenant server owns sandbox execution, tenant authorization,
    durable logs, artifacts, and isolation.
  - test: Identify any gaps that would require a new gateway contract,
    manifest semantics, metadata field, or policy/auth behavior. Mark those as
    blockers instead of expanding HOSTSOAK.
  - impl: Record the conflict map in execution closeout so shared dirty files
    from earlier phases are preserved and not reverted.
  - verify: `git status --short`
  - verify: `git status --short -- specs/phase-plans-v6.md specs/tenant-code-mode-host-contract.md plans/phase-plan-v6-hostcontract.md plans/phase-plan-v6-hostreg.md plans/phase-plan-v6-hostmeta.md plans/phase-plan-v6-hostpolicy.md`
  - verify: `rg -n "tenant-code-mode|tenant code-mode|run_script|gateway.tasks_result|policy_denied|redaction|execution authority|broker" README.md SECURITY.md CHANGELOG.md specs/tenant-code-mode-host-contract.md tests src/pmcp`

### SL-1 - Dedicated Mock Tenant Soak Suite

- **Scope**: Add the final deterministic HOSTSOAK test module that exercises
  the hosted code-mode contract end to end through PMCP fakes and gateway
  tools.
- **Owned files**: `tests/test_phase6_tenant_code_mode.py`
- **Interfaces provided**: deterministic mock tenant server fixture, final soak
  discovery/describe/invoke/task/result/cancel test, no-local-shell hosted path
  test, policy denial and redaction soak test
- **Interfaces consumed**: SL-0 gap list, `GatewayTools`,
  `DescriptionsCache`, `GeneratedServerDescriptions`, `PrebuiltToolInfo`,
  `ToolInfo`, `McpTaskRecord`, `PolicyManager`, current `gateway.describe`,
  `gateway.request_capability`, `gateway.catalog_search`, `gateway.invoke`,
  `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and
  `gateway.tasks_cancel` behavior
- **Parallel-safe**: no
- **Tasks**:
  - test: Add a local fake tenant manager that advertises
    `tenant-code-mode::run_script`, `tenant-code-mode::get_result`, and
    `tenant-code-mode::cancel_run`, reports server task capability, stores
    downstream task IDs, returns deterministic task states, and never calls a
    live server or subprocess.
  - test: Add a discovery and describe soak covering
    `gateway.request_capability`, `gateway.catalog_search(include_offline=true)`,
    and `gateway.describe` for hosted sandbox/code-mode language with
    `available_clis=[]`.
  - test: Add a no-local-shell flow proving the client can discover the
    configured hosted tenant server and submit a task-capable sandbox run
    through `gateway.invoke` using non-secret task metadata and trace context.
  - test: Add task lifecycle assertions for `working`, `input_required`,
    `completed`, and `cancelled`, using downstream tenant task IDs with
    `gateway.tasks_list`, `gateway.tasks_get`, `gateway.tasks_result`, and
    `gateway.tasks_cancel`.
  - test: Add policy-denied and redaction assertions proving tenant server/tool
    denial happens before dispatch, sandbox-shaped result secrets are redacted
    and truncated, and audit/diagnostic strings do not contain token values.
  - impl: Keep the fixture self-contained in the new phase-six test file unless
    reuse is clearly necessary; do not move existing shared test helpers during
    soak.
  - impl: Do not mark the test `live`; it must run under normal local and CI
    pytest without external infrastructure.
  - verify: `uv run pytest tests/test_phase6_tenant_code_mode.py -q`
  - verify: `git diff --check -- tests/test_phase6_tenant_code_mode.py`

### SL-2 - Conditional Shared Contract Gap Repair

- **Scope**: Repair only real shared-surface gaps exposed by the dedicated soak
  suite, preserving earlier HOSTREG, HOSTMETA, and HOSTPOLICY edits.
- **Owned files**: `src/pmcp/client/manager.py`,
  `src/pmcp/tools/handlers.py`, `src/pmcp/types.py`,
  `src/pmcp/policy/policy.py`, `src/pmcp/auth.py`,
  `tests/test_client_manager.py`, `tests/test_tools.py`,
  `tests/test_policy.py`, `tests/test_auth.py`, `tests/test_manifest.py`,
  `tests/test_integration.py`
- **Interfaces provided**: any necessary generic host-side fix for tenant
  discovery, describe, invoke, task, policy, auth, redaction, or diagnostics
- **Interfaces consumed**: SL-1 failing assertions, existing HOSTREG/HOSTMETA
  and HOSTPOLICY tests, `ClientManager`, `GatewayTools`, `PolicyManager`,
  remote header placeholder and auth diagnostic helpers
- **Parallel-safe**: no
- **Tasks**:
  - test: Before editing shared files, inspect current diffs for each file in
    this lane and preserve existing tenant registration, metadata, policy,
    auth, and docs work.
  - test: If the soak fails because a test selector is wrong or matches zero
    tests, fix the selector or test naming rather than treating that as a
    runtime regression.
  - test: If the soak exposes a behavior gap already owned by a previous
    phase, add or adjust the narrow existing regression in the matching test
    file before changing runtime code.
  - impl: Prefer tests-only changes when existing code satisfies the contract.
  - impl: If source changes are required, keep them generic and additive inside
    the existing manager, handler, policy, or auth helper. Do not add a new
    `gateway.run_code`, `pmcp execute`, sandbox runtime, tenant identity model,
    durable artifact store, or release-only branch.
  - impl: Leave `uv.lock` untouched unless a command in this phase truly
    changes dependencies; HOSTSOAK should not add dependencies.
  - verify: `uv run pytest tests/test_tools.py -k "tenant_code_mode or tasks_result or policy_denied or request_capability or catalog_search"`
  - verify: `uv run pytest tests/test_policy.py -k "tenant_code_mode or redacts or process_output"`
  - verify: `uv run pytest tests/test_manifest.py -k "tenant_code_mode"`
  - verify: `uv run pytest tests/test_client_manager.py -k "tenant_code_mode or preserves_trace_context"`
  - verify: `uv run pytest tests/test_auth.py -k "tenant_code_mode or remote_header or auth_redaction or env_lookup"`
  - verify: `uv run pytest tests/test_integration.py -k "tenant_code_mode"`
  - verify: `git diff --check -- src/pmcp/client/manager.py src/pmcp/tools/handlers.py src/pmcp/types.py src/pmcp/policy/policy.py src/pmcp/auth.py tests/test_client_manager.py tests/test_tools.py tests/test_policy.py tests/test_auth.py tests/test_manifest.py tests/test_integration.py`

### SL-3 - README and CHANGELOG Release Docs

- **Scope**: Synthesize the proven host integration into concise public docs
  and changelog wording for release readiness.
- **Owned files**: `README.md`, `CHANGELOG.md`
- **Interfaces provided**: operator/user tenant code-mode flow, release note
  wording, no-PMCP-runtime claim, no-version-bump decision
- **Interfaces consumed**: SL-0 predecessor checklist, SL-1 soak results, SL-2
  repairs if any, `specs/tenant-code-mode-host-contract.md`, existing README
  tenant registration/task/policy docs, existing SECURITY trust-boundary
  language
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm README already or newly explains the user flow from
    one-PMCP-connection discovery through `gateway.invoke` task submission and
    `gateway.tasks_*` lifecycle checks.
  - test: Confirm README examples use placeholders such as
    `${TENANT_CODE_MODE_MCP_TOKEN}` and `${TENANT_CODE_MODE_TENANT_ID}` without
    secret values.
  - test: Confirm README does not imply PMCP runs scripts, provides tenant
    isolation, persists sandbox logs/artifacts, or adds a general code
    execution transport.
  - test: Confirm SECURITY remains consistent with README; edit SECURITY only
    in SL-2 if a real shared contract gap requires it, not as release-docs
    churn.
  - impl: Add or refine a concise README operator/user flow only if the current
    README is insufficient after earlier phases.
  - impl: Add a `[Unreleased]` CHANGELOG entry for tenant code-mode host
    integration that says PMCP brokers discovery, invocation, task lifecycle,
    policy, redaction, and docs, but does not run scripts itself.
  - impl: Do not bump `pyproject.toml` or publish from this lane.
  - verify: `rg -n "tenant code-mode|tenant-code-mode|gateway.invoke|gateway.tasks_|broker|execution authority|does not run scripts|CHANGELOG" README.md CHANGELOG.md SECURITY.md`
  - verify: `git diff --check -- README.md CHANGELOG.md`

### SL-4 - Release Verification Matrix

- **Scope**: Run the targeted and whole-phase release-gate commands needed to
  prove HOSTSOAK before version bump or publish.
- **Owned files**: none
- **Interfaces provided**: HOSTSOAK verification result set, residual risk or
  blocker list, version-bump gate decision
- **Interfaces consumed**: SL-1 soak suite, SL-2 shared repairs, SL-3 docs,
  roadmap whole-release verification matrix
- **Parallel-safe**: no
- **Tasks**:
  - test: Run the new dedicated soak test first so failures stay scoped to
    HOSTSOAK.
  - test: Run the existing tenant-related targeted slices for tools, policy,
    manifest, client manager, auth, and integration coverage.
  - test: Run the full release matrix after targeted tests pass: ruff, format
    check, mypy, full pytest, build, and diff whitespace check.
  - impl: Record exact command outcomes for SL-5 closeout. If a full-suite
    failure is unrelated to HOSTSOAK, record the failing command and evidence
    without hiding it.
  - verify: `uv run pytest tests/test_phase6_tenant_code_mode.py -q`
  - verify: `uv run pytest tests/test_tools.py -k "request_capability or catalog_search or tasks or tenant or code_mode"`
  - verify: `uv run pytest tests/test_policy.py -k "policy or redaction or tenant"`
  - verify: `uv run pytest tests/test_integration.py -k "task or remote or tenant"`
  - verify: `uv run ruff check src/ tests/`
  - verify: `uv run ruff format --check src/ tests/`
  - verify: `uv run mypy src/pmcp --exclude baml_client`
  - verify: `uv run pytest -q`
  - verify: `uv build`
  - verify: `git diff --check`

### SL-5 - Roadmap and Plan Closeout Reducer

- **Scope**: Reduce all test, docs, and verification evidence into the v6
  roadmap and HOSTSOAK execution closeout without introducing new product
  behavior.
- **Owned files**: `specs/phase-plans-v6.md`,
  `plans/phase-plan-v6-hostsoak.md`
- **Interfaces provided**: completed HOSTSOAK checklist, final v6 roadmap
  reconciliation, execution evidence summary, next-step decision for version
  bump or blocker rollback
- **Interfaces consumed**: SL-0 baseline, SL-1 soak test results, SL-2 repair
  decisions, SL-3 docs/changelog result, SL-4 verification outcomes, Phase 5
  exit criteria from `specs/phase-plans-v6.md`
- **Parallel-safe**: no
- **Tasks**:
  - test: Map every Phase 5 exit criterion to a specific test, docs section,
    changelog entry, or release verification command.
  - test: Confirm no HOSTSOAK change added live-infrastructure requirements,
    new public gateway contracts, PMCP-owned script execution, version bump, or
    publish behavior.
  - impl: Mark the HOSTSOAK roadmap criteria and top interface gate complete
    only after SL-4 passes. If a gate fails, leave it unchecked and record the
    blocking phase.
  - impl: Add an execution closeout section to this plan with the command
    evidence, any skipped or blocked checks, and the precise next command.
  - impl: Keep `specs/phase-plans-v6.md` edits scoped to HOSTSOAK
    reconciliation; do not rewrite earlier phase prose unless the blocker is
    explicitly routed back.
  - verify: `rg -n "IF-0-HOSTSOAK|Host Integration Soak|Release Verification|tenant code-mode|brokers execution|does not run scripts" specs/phase-plans-v6.md plans/phase-plan-v6-hostsoak.md README.md CHANGELOG.md`
  - verify: `git diff --check -- specs/phase-plans-v6.md plans/phase-plan-v6-hostsoak.md`

## Verification

Lane-specific verification:

- `git status --short`
- `git status --short -- specs/phase-plans-v6.md specs/tenant-code-mode-host-contract.md plans/phase-plan-v6-hostcontract.md plans/phase-plan-v6-hostreg.md plans/phase-plan-v6-hostmeta.md plans/phase-plan-v6-hostpolicy.md`
- `rg -n "tenant-code-mode|tenant code-mode|run_script|gateway.tasks_result|policy_denied|redaction|execution authority|broker" README.md SECURITY.md CHANGELOG.md specs/tenant-code-mode-host-contract.md tests src/pmcp`
- `uv run pytest tests/test_phase6_tenant_code_mode.py -q`
- `uv run pytest tests/test_tools.py -k "tenant_code_mode or tasks_result or policy_denied or request_capability or catalog_search"`
- `uv run pytest tests/test_policy.py -k "tenant_code_mode or redacts or process_output"`
- `uv run pytest tests/test_manifest.py -k "tenant_code_mode"`
- `uv run pytest tests/test_client_manager.py -k "tenant_code_mode or preserves_trace_context"`
- `uv run pytest tests/test_auth.py -k "tenant_code_mode or remote_header or auth_redaction or env_lookup"`
- `uv run pytest tests/test_integration.py -k "tenant_code_mode"`
- `rg -n "tenant code-mode|tenant-code-mode|gateway.invoke|gateway.tasks_|broker|execution authority|does not run scripts|CHANGELOG" README.md CHANGELOG.md SECURITY.md`
- `rg -n "IF-0-HOSTSOAK|Host Integration Soak|Release Verification|tenant code-mode|brokers execution|does not run scripts" specs/phase-plans-v6.md plans/phase-plan-v6-hostsoak.md README.md CHANGELOG.md`
- `git diff --check -- tests/test_phase6_tenant_code_mode.py README.md CHANGELOG.md specs/phase-plans-v6.md plans/phase-plan-v6-hostsoak.md`

Whole-phase release verification:

- `uv run pytest tests/test_phase6_tenant_code_mode.py -q`
- `uv run pytest tests/test_tools.py -k "request_capability or catalog_search or tasks or tenant or code_mode"`
- `uv run pytest tests/test_policy.py -k "policy or redaction or tenant"`
- `uv run pytest tests/test_integration.py -k "task or remote or tenant"`
- `uv run ruff check src/ tests/`
- `uv run ruff format --check src/ tests/`
- `uv run mypy src/pmcp --exclude baml_client`
- `uv run pytest -q`
- `uv build`
- `git diff --check`

No `scripts/preflight.sh` command is part of this plan; PMCP's prior phase
work uses the direct `uv` verification commands listed above.

## Acceptance Criteria

- [x] `tests/test_phase6_tenant_code_mode.py` exists and contains a
  deterministic non-live tenant code-mode fixture covering discovery, describe,
  invoke, task polling, result retrieval, cancellation, policy denial, and
  redaction.
- [x] A mobile/no-local-shell test proves PMCP can recommend and invoke the
  hosted tenant code-mode path with `available_clis=[]` and without CLI
  execution.
- [x] Existing tenant tests in `tests/test_manifest.py`,
  `tests/test_client_manager.py`, `tests/test_tools.py`,
  `tests/test_policy.py`, `tests/test_auth.py`, and
  `tests/test_integration.py` still pass after the soak layer is added.
- [x] README documents the concise PMCP plus companion tenant server flow using
  placeholder credentials and preserving the broker/execution-authority
  boundary.
- [x] CHANGELOG has an Unreleased entry for tenant code-mode host integration
  that does not claim PMCP runs scripts.
- [x] Full release verification passes: targeted tenant tests, `ruff check`,
  `ruff format --check`, `mypy`, full `pytest`, `uv build`, and
  `git diff --check`.
- [x] `specs/phase-plans-v6.md` and this plan record HOSTSOAK completion or a
  concrete blocker routed back to HOSTCONTRACT, HOSTREG, HOSTMETA, or
  HOSTPOLICY.

## Execution Closeout

Completed lanes:

- SL-0: Baseline reconciled. Existing v6 HOST artifacts and dirty shared files
  were preserved. No new PMCP-owned runtime, gateway tool, version bump, or
  live infrastructure dependency was added.
- SL-1: Added `tests/test_phase6_tenant_code_mode.py` with a deterministic fake
  tenant code-mode server covering discovery, describe, invoke, task list/get,
  task result redaction, cancellation, policy denial, and no-local-shell
  discovery with `available_clis=[]`.
- SL-2: No shared source repair was required after the soak passed.
- SL-3: README already contained the tenant operator/user flow; wording was
  tightened to say PMCP does not run scripts itself. CHANGELOG gained an
  Unreleased HOSTSOAK entry.
- SL-4: Release verification was run. Targeted tenant checks, `ruff check`,
  `ruff format --check`, `mypy`, full pytest, `uv build`, and
  `git diff --check` passed.
- SL-5: Roadmap and plan closeout recorded this evidence.

Verification evidence:

- `uv run pytest tests/test_phase6_tenant_code_mode.py -q`: 2 passed.
- `uv run pytest tests/test_tools.py -k "tenant_code_mode or tasks_result or policy_denied or request_capability or catalog_search"`:
  35 passed, 101 deselected.
- `uv run pytest tests/test_policy.py -k "tenant_code_mode or redacts or process_output"`:
  7 passed, 14 deselected.
- `uv run pytest tests/test_manifest.py -k "tenant_code_mode"`: 3 passed,
  75 deselected.
- `uv run pytest tests/test_client_manager.py -k "tenant_code_mode or preserves_trace_context"`:
  2 passed, 93 deselected.
- `uv run pytest tests/test_auth.py -k "tenant_code_mode or remote_header or auth_redaction or env_lookup"`:
  9 passed, 27 deselected.
- `uv run pytest tests/test_integration.py -k "tenant_code_mode"`: 1 passed,
  6 deselected.
- `uv run pytest tests/test_tools.py -k "request_capability or catalog_search or tasks or tenant or code_mode"`:
  34 passed, 102 deselected.
- `uv run pytest tests/test_policy.py -k "policy or redaction or tenant"`:
  21 passed.
- `uv run pytest tests/test_integration.py -k "task or remote or tenant"`:
  1 passed, 6 deselected.
- `uv run ruff check src/ tests/`: passed.
- `uv run ruff format --check src/ tests/`: passed after formatting
  `src/pmcp/summary/generator.py` and
  `src/pmcp/summary/template_fallback.py`.
- `uv run mypy src/pmcp --exclude baml_client`: passed.
- `uv run pytest -q`: 1797 passed, 12 skipped, 21 deselected.
- `uv build`: passed, producing `dist/pmcp-1.13.0.tar.gz` and
  `dist/pmcp-1.13.0-py3-none-any.whl`.
- `git diff --check`: passed.

Blocked verification: none.

Next phase: none - v6 roadmap complete.

Next command: none - roadmap complete.
