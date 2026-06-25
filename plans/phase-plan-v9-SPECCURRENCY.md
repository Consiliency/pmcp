---
phase_loop_plan_version: 1
phase: SPECCURRENCY
roadmap: specs/phase-plans-v9.md
roadmap_sha256: 663a49e8c6cce4b7bcfb50206a5a3d1ffbbe743926320a3d74df02711fbd9159
---

# SPECCURRENCY: MCP Spec Currency

## Context

Phase SPECCURRENCY implements Phase 1 of `specs/phase-plans-v9.md`: fold in the cheap unmet MCP 2025-11-25 MUST/SHOULD items and add a tracked `SPEC_COMPLIANCE.md` covering current compliance plus the draft revision migration impact.

The roadmap hash was verified from `specs/phase-plans-v9.md` as `663a49e8c6cce4b7bcfb50206a5a3d1ffbbe743926320a3d74df02711fbd9159`. Canonical `.phase-loop/` state marks `SPECCURRENCY` as the current unplanned phase; legacy `.codex/phase-loop/` state is compatibility-only and is not authoritative for this run. The MCP documentation site still labels `2025-11-25` as the latest stable specification, while draft pages describe the next revision as not yet finalized.

Current code already has several SPECCURRENCY seams in place: `create_http_app(...)` rejects disallowed `Origin` with HTTP 403 when `allowed_origins` is configured; Resource Server insufficient scope returns 403 with `WWW-Authenticate` error and scope; `ToolInfo` defaults schema dialect metadata to JSON Schema 2020-12; `ClientManager` indexes tool/resource/prompt icons; and `gateway.catalog_search` plus `gateway.describe` already pass tool icons and schema dialect through. Execution should verify these as current behavior first, then make only missing contract changes and document already-compliant items rather than duplicating code.

`tools/handlers.py` remains a single-writer file for this phase. Lanes touching MCP-facing output are split so `tools/handlers.py`, `src/pmcp/server.py`, and transport code have disjoint owners, while the docs lane depends on all findings before writing the compliance table.

## Interface Freeze Gates

- [ ] IF-0-SPECCURRENCY-1 - PMCP satisfies the folded-in 2025-11-25 MUST/SHOULD items: Streamable HTTP rejects invalid configured `Origin` values with HTTP 403; Resource Server insufficient scope returns HTTP 403 with `WWW-Authenticate` `error="insufficient_scope"` and `scope`; gateway tool input-validation failures are surfaced as tool-execution errors instead of JSON-RPC protocol failures; JSON Schema 2020-12 is advertised as the default schema dialect when downstream schemas omit `$schema`; downstream tool/resource/prompt icons are indexed and tool icons pass through `gateway.catalog_search` and `gateway.describe`; MCP server implementation metadata is assessed against the installed SDK surface; and `SPEC_COMPLIANCE.md` records per-requirement status, source citations, draft-revision impact, and a next-stable tracking checklist.

## Lane Index & Dependencies

- SL-0 - Streamable HTTP Origin and scope contracts; Depends on: (none); Blocks: SL-3, SL-4; Parallel-safe: yes
- SL-1 - Tool metadata and invocation validation contracts; Depends on: (none); Blocks: SL-3, SL-4; Parallel-safe: yes
- SL-2 - Server tool-error envelope and implementation metadata; Depends on: (none); Blocks: SL-3, SL-4; Parallel-safe: yes
- SL-3 - SPEC_COMPLIANCE and README integration; Depends on: SL-0, SL-1, SL-2; Blocks: SL-4; Parallel-safe: no
- SL-4 - SPECCURRENCY reducer verification; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 - Streamable HTTP Origin and Scope Contracts

- **Scope**: Verify and repair the Streamable HTTP transport behavior for invalid `Origin` and Resource Server insufficient-scope challenges.
- **Owned files**: `src/pmcp/transport/http.py`, `tests/test_http_transport.py`
- **Interfaces provided**: HTTP 403 invalid-origin contract; HTTP 403 `insufficient_scope` Resource Server challenge contract; transport compliance evidence for IF-0-SPECCURRENCY-1
- **Interfaces consumed**: pre-existing `create_http_app(...)`, `allowed_origins`, `validate_resource_server_token(...)`, `ResourceServerAuthError`, Starlette request/response behavior, roadmap SPECCURRENCY exit criteria
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or tighten failing-first coverage proving a request with `Origin: https://evil.example` is rejected with HTTP 403 before `StreamableHTTPSessionManager.handle_request(...)` is called when `allowed_origins=["https://app.example"]`.
  - test: Add or tighten Resource Server coverage proving `ResourceServerAuthError("insufficient_scope", ...)` returns HTTP 403 and `WWW-Authenticate` includes `error="insufficient_scope"` plus the required `scope` value.
  - impl: Leave existing transport behavior unchanged when tests prove it already satisfies the contract; otherwise patch only the specific status/header mapping needed in `src/pmcp/transport/http.py`.
  - verify: `uv run pytest tests/test_http_transport.py -k "origin or insufficient or scope or resource_server"`
  - verify: `git diff --check -- src/pmcp/transport/http.py tests/test_http_transport.py`

### SL-1 - Tool Metadata and Invocation Validation Contracts

- **Scope**: Verify and repair tool discovery metadata for schema dialect/icons and gateway invocation validation behavior owned by `tools/handlers.py`.
- **Owned files**: `src/pmcp/client/manager.py`, `src/pmcp/tools/handlers.py`, `src/pmcp/types.py`, `tests/test_tools.py`
- **Interfaces provided**: JSON Schema 2020-12 default dialect propagation; downstream tool icon passthrough through `gateway.catalog_search` and `gateway.describe`; gateway invocation input-validation result contract; tool metadata compliance evidence for IF-0-SPECCURRENCY-1
- **Interfaces consumed**: pre-existing `DEFAULT_SCHEMA_DIALECT`, `_schema_dialect(...)`, `ToolInfo`, `CapabilityCard`, `SchemaCard`, `GatewayTools.catalog_search(...)`, `GatewayTools.describe(...)`, `GatewayTools.invoke(...)`, `ErrorCode.E304_INVALID_ARGUMENTS`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add or tighten coverage proving a downstream tool schema with no `$schema` is exposed by `gateway.catalog_search` and `gateway.describe` with `https://json-schema.org/draft/2020-12/schema`.
  - test: Add or tighten coverage proving downstream tool `icons` are indexed by `ClientManager` and returned unchanged by `gateway.catalog_search` and `gateway.describe` without adding full schemas to catalog cards.
  - test: Add a failing-first invocation-validation regression proving missing required tool arguments return a tool-execution error payload through `gateway.invoke` and do not call the downstream tool.
  - impl: Preserve existing metadata defaults and passthrough when tests show compliance; otherwise repair only `ToolInfo`/card/schema-card fields and the `GatewayTools` serialization paths needed for the contract.
  - impl: Keep validation failures in the tool-result surface with `ok=False`, structured `E304_INVALID_ARGUMENTS` details, and no JSON-RPC protocol exception from `GatewayTools.invoke(...)`.
  - verify: `uv run pytest tests/test_tools.py -k "schema_dialect or icons or invalid_arguments or missing_required or catalog_search or describe or invoke"`
  - verify: `git diff --check -- src/pmcp/client/manager.py src/pmcp/tools/handlers.py src/pmcp/types.py tests/test_tools.py`

### SL-2 - Server Tool-Error Envelope and Implementation Metadata

- **Scope**: Ensure top-level MCP tool handler exceptions are represented as tool execution errors and assess server implementation metadata against the installed MCP SDK surface.
- **Owned files**: `src/pmcp/server.py`, `tests/test_server.py`, `tests/test_baseline_constraints.py`
- **Interfaces provided**: MCP `call_tool` error-envelope contract for validation exceptions; MCP server implementation metadata assessment; IF-0-SPECCURRENCY-1 server-surface evidence
- **Interfaces consumed**: pre-existing `GatewayServer._create_server(...)`, `GatewayServer._setup_handlers(...)`, `get_gateway_tool_definitions()`, `mcp.server.Server`, `mcp.types.TextContent`, MCP SDK `Implementation` model exposed by the installed dependency
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a failing-first server handler regression that calls a gateway tool with invalid top-level input and asserts the MCP call returns a tool-level error payload, not an uncaught exception or JSON-RPC protocol failure.
  - test: Add a metadata assessment test for `GatewayServer._create_server(...)` documenting whether the installed MCP SDK exposes an `Implementation.description` field; if it does, assert PMCP sets a concise implementation description, and if it does not, assert `SPEC_COMPLIANCE.md` records the SDK-surface limitation instead of inventing unsupported metadata.
  - impl: Adjust `GatewayServer._setup_handlers(...)` error handling only as needed so validation exceptions stay in the tool result surface and downstream clients can self-correct.
  - impl: Pass implementation description metadata through `Server(...)` only if the installed MCP SDK supports that field; otherwise leave runtime code unchanged and feed the limitation to SL-3 documentation.
  - verify: `uv run pytest tests/test_server.py tests/test_baseline_constraints.py -k "call_tool or validation or implementation or description or mcp_gateway"`
  - verify: `git diff --check -- src/pmcp/server.py tests/test_server.py tests/test_baseline_constraints.py`

### SL-3 - SPEC_COMPLIANCE and README Integration

- **Scope**: Write the compliance artifact and README link after consuming transport, tool metadata, invocation, and server metadata findings.
- **Owned files**: `SPEC_COMPLIANCE.md`, `README.md`
- **Interfaces provided**: `SPEC_COMPLIANCE.md` current-stable compliance table; draft-revision impact and migration assessment; next-stable tracking checklist; README compliance link; documented production evidence for IF-0-SPECCURRENCY-1
- **Interfaces consumed**: SL-0 transport evidence; SL-1 tool metadata and validation evidence; SL-2 server error-envelope and implementation metadata evidence; roadmap draft-impact list; MCP 2025-11-25 spec, changelog, and draft documentation
- **Parallel-safe**: no
- **Tasks**:
  - test: Check whether `README.md` currently links a compliance artifact; if not, prepare the smallest README insertion that points operators to `SPEC_COMPLIANCE.md` without reshaping unrelated docs.
  - impl: Create `SPEC_COMPLIANCE.md` with target revision `2025-11-25`, source links, per-requirement status rows for Origin 403, insufficient-scope step-up, tool validation errors, JSON Schema 2020-12, icons, and implementation metadata.
  - impl: Add a draft-revision impact section covering stateless/no-session transport, tasks moving to `io.modelcontextprotocol/tasks`, `tasks/list` removal, `tasks/result` to `tasks/get` polling, `tasks/update`, unsolicited task handles, MRTR, required `resultType`, `server/discover`, `CacheableResult`, DCR to Client ID Metadata Documents, and SSE resumability removal.
  - impl: Add a next-stable tracking checklist that names the owner-facing PMCP surfaces to revisit when the draft becomes stable: `src/pmcp/transport/http.py`, `src/pmcp/tools/handlers.py`, `src/pmcp/client/manager.py`, task APIs, and release notes.
  - impl: Link `SPEC_COMPLIANCE.md` from the README's protocol/metadata discussion without changing unrelated README sections.
  - verify: `test -s SPEC_COMPLIANCE.md`
  - verify: `rg -n "2025-11-25|SEP-1303|SEP-835|SEP-973|SEP-1613|draft|stateless|tasks/get|CacheableResult|server/discover" SPEC_COMPLIANCE.md`
  - verify: `rg -n "SPEC_COMPLIANCE" README.md`
  - verify: `git diff --check -- SPEC_COMPLIANCE.md README.md`

### SL-4 - SPECCURRENCY Reducer Verification

- **Scope**: Verify the full phase, confirm IF-0-SPECCURRENCY-1 is produced, and inventory dirty paths for runner closeout.
- **Owned files**: none
- **Interfaces provided**: SPECCURRENCY verification evidence; IF-0-SPECCURRENCY-1 completion checklist; phase-owned dirty-path inventory
- **Interfaces consumed**: IF-0-SPECCURRENCY-1; SL-0 transport results; SL-1 tool metadata and validation results; SL-2 server result-envelope and metadata results; SL-3 compliance documentation; roadmap SPECCURRENCY exit criteria
- **Parallel-safe**: no
- **Tasks**:
  - test: Confirm no lane-owned file overlaps another lane-owned file and no implementation touched paths outside the union of SL-0, SL-1, SL-2, and SL-3 owned files.
  - test: Confirm `SPEC_COMPLIANCE.md` records already-compliant items explicitly where tests prove current HEAD already satisfied the contract.
  - verify: `uv run pytest tests/test_http_transport.py tests/test_tools.py tests/test_server.py tests/test_baseline_constraints.py -k "origin or insufficient or scope or input_validation or invalid_arguments or schema_dialect or icons or implementation or description"`
  - verify: `TMPDIR=/var/tmp uv run ruff check src/ tests/`
  - verify: `uv run mypy src/pmcp`
  - verify: `TMPDIR=/var/tmp uv run pytest -q`
  - verify: `git diff --check`
  - verify: `git status --short`

## Execution Policy

- work-unit defaults: work-unit=`lane_execute`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`
- SL-4: work-unit=`phase_reducer`, effort=`medium`, unsupported=`inherit_default`, inherit-default=`true`

## Verification

Lane-specific verification commands are listed under each lane. Whole-phase verification is:

```bash
uv run pytest tests/test_http_transport.py tests/test_tools.py tests/test_server.py tests/test_baseline_constraints.py -k "origin or insufficient or scope or input_validation or invalid_arguments or schema_dialect or icons or implementation or description"
TMPDIR=/var/tmp uv run ruff check src/ tests/
uv run mypy src/pmcp
TMPDIR=/var/tmp uv run pytest -q
git diff --check
git status --short
```

## Acceptance Criteria

- [ ] Requests with an invalid configured `Origin` are rejected with HTTP 403 before MCP request handling runs.
- [ ] Resource Server insufficient-scope failures return HTTP 403 with `WWW-Authenticate` containing `error="insufficient_scope"` and the required `scope` value.
- [ ] Gateway invocation input-validation failures are returned as tool-execution error results that clients can inspect and self-correct from, not as JSON-RPC protocol failures.
- [ ] JSON Schema 2020-12 is the default dialect advertised for tool schemas that omit `$schema`, and `gateway.catalog_search` plus `gateway.describe` expose that dialect consistently.
- [ ] Downstream tool icons are indexed and passed through `gateway.catalog_search` and `gateway.describe`; resource and prompt icon indexing remains covered or documented as pre-existing support.
- [ ] MCP server implementation metadata is either populated when the installed SDK supports the field or explicitly documented in `SPEC_COMPLIANCE.md` as blocked by the SDK surface.
- [ ] `SPEC_COMPLIANCE.md` exists, cites MCP 2025-11-25 sources, records per-requirement status, includes the draft-revision impact/migration assessment, and includes a tracking checklist for the next stable revision.
- [ ] `README.md` links `SPEC_COMPLIANCE.md` without unrelated README restructuring.
- [ ] `ruff`, mypy, and full `pytest` pass with `TMPDIR=/var/tmp` for commands that need a temporary directory outside `/tmp`.
