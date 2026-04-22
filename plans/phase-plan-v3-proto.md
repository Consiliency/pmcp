# PROTO: Protocol Version and Metadata Alignment

## Context

Phase 1 of `specs/phase-plans-v3.md` brings PMCP's downstream client and gateway surfaces up to current MCP protocol expectations while preserving compatibility with older servers. Today `ClientManager._send_initialize(...)` sends `protocolVersion: "2024-11-05"`, `ServerStatus` has no negotiated protocol or capability fields, and `ToolInfo`, `ResourceInfo`, and `PromptInfo` only retain older core fields. `gateway.catalog_search`, `gateway.describe`, `gateway.health`, and `pmcp status` can stay backward compatible by adding optional fields only.

The implementation should follow the MCP 2025-11-25 lifecycle and tools contracts: initialize negotiates a single protocol version; tools may include `title`, `icons`, `outputSchema`, `annotations`, and `execution.taskSupport`; JSON Schema defaults to 2020-12 when `$schema` is absent; and annotations remain untrusted hints.

## Interface Freeze Gates

- [x] IF-0-PROTO-1 — `ClientManager._send_initialize(...)` sends a preferred protocol version of `2025-11-25`, records the negotiated `protocolVersion` returned by the server, and falls back or preserves compatibility for `2024-11-05`, `2025-03-26`, and `2025-06-18` servers.
- [x] IF-0-PROTO-2 — `ServerStatus` and `ServerHealthInfo` add optional `protocol_version: str | None` and `server_capabilities: dict[str, Any] | None` fields; existing required status fields remain unchanged.
- [x] IF-0-PROTO-3 — `ToolInfo` preserves modern MCP tool metadata additively with optional `title`, `icons`, `output_schema`, `annotations`, `execution`, `schema_dialect`, and `raw_metadata` fields; existing `input_schema`, `description`, `tags`, and `risk_hint` semantics remain unchanged.
- [x] IF-0-PROTO-4 — `ResourceInfo`, `PromptInfo`, and `PromptArgumentInfo` preserve optional modern display and metadata fields where present, including `title`, `icons`, `annotations`, and `raw_metadata`.
- [x] IF-0-PROTO-5 — `CapabilityCard` and `SchemaCard` expose richer tool metadata additively; `gateway.catalog_search` remains compact and schema-free, while `gateway.describe` exposes `output_schema`, `annotations`, `execution`, `schema_dialect`, `title`, and `icons`.
- [x] IF-0-PROTO-6 — `gateway.health`, live `pmcp status --json`, and human `pmcp status --verbose` surface negotiated protocol version without requiring every server to be connected or current-protocol capable.
- [x] IF-0-PROTO-7 — Tests cover old-protocol initialize responses, current-protocol responses, missing optional metadata, unknown extra fields, JSON Schema dialect defaults, and additive gateway/CLI output compatibility.

## Lane Index & Dependencies

- SL-0 — Protocol and metadata type contract; Depends on: (none); Blocks: SL-1, SL-2, SL-3; Parallel-safe: no
- SL-1 — Downstream negotiation and indexing; Depends on: SL-0; Blocks: SL-2, SL-3, SL-4; Parallel-safe: no
- SL-2 — Gateway metadata surfaces; Depends on: SL-0, SL-1; Blocks: SL-4; Parallel-safe: yes
- SL-3 — CLI protocol status surfaces; Depends on: SL-0, SL-1; Blocks: SL-4; Parallel-safe: yes
- SL-4 — Documentation impact and roadmap closeout; Depends on: SL-0, SL-1, SL-2, SL-3; Blocks: (none); Parallel-safe: no

## Lanes

### SL-0 — Protocol and Metadata Type Contract

- **Scope**: Add optional type fields that freeze the protocol and metadata contract without changing existing required fields.
- **Owned files**: `src/pmcp/types.py`
- **Interfaces provided**: `ServerStatus.protocol_version`, `ServerStatus.server_capabilities`, `ServerHealthInfo.protocol_version`, `ServerHealthInfo.server_capabilities`, `ToolInfo.title`, `ToolInfo.icons`, `ToolInfo.output_schema`, `ToolInfo.annotations`, `ToolInfo.execution`, `ToolInfo.schema_dialect`, `ToolInfo.raw_metadata`, `ResourceInfo.title`, `ResourceInfo.icons`, `ResourceInfo.annotations`, `ResourceInfo.raw_metadata`, `PromptArgumentInfo.title`, `PromptArgumentInfo.raw_metadata`, `PromptInfo.title`, `PromptInfo.icons`, `PromptInfo.annotations`, `PromptInfo.raw_metadata`, `CapabilityCard.title`, `CapabilityCard.icons`, `CapabilityCard.execution`, `CapabilityCard.schema_dialect`, `SchemaCard.title`, `SchemaCard.icons`, `SchemaCard.output_schema`, `SchemaCard.annotations`, `SchemaCard.execution`, `SchemaCard.schema_dialect`
- **Interfaces consumed**: existing `ToolInfo`, `ResourceInfo`, `PromptInfo`, `PromptArgumentInfo`, `ServerStatus`, `CapabilityCard`, `SchemaCard`, `ServerHealthInfo`, and `HealthOutput` field compatibility
- **Parallel-safe**: no
- **Tasks**:
  - test: Defer behavioral assertions to SL-1, SL-2, and SL-3 because those lanes own the manager, gateway, and CLI test files.
  - impl: Add optional fields to the Pydantic models with `None` defaults so existing constructors and serialized callers remain compatible.
  - impl: Represent `icons`, `annotations`, `execution`, `raw_metadata`, `server_capabilities`, and schemas as `dict[str, Any]` or `list[dict[str, Any]]` rather than introducing speculative strict models.
  - impl: Use `schema_dialect: str = "https://json-schema.org/draft/2020-12/schema"` for tool input/output metadata when no explicit `$schema` exists.
  - verify: `uv run ruff check src/pmcp/types.py`

### SL-1 — Downstream Negotiation and Indexing

- **Scope**: Update downstream connection initialization and indexing so PMCP records negotiated protocol version and preserves modern tool/resource/prompt metadata for stdio, remote stream, and adopted-process paths.
- **Owned files**: `src/pmcp/client/manager.py`, `tests/test_client_manager.py`
- **Interfaces provided**: `SUPPORTED_PROTOCOL_VERSIONS`, `PREFERRED_PROTOCOL_VERSION`, `ClientManager._send_initialize(...)` negotiated-version recording, centralized tool/resource/prompt indexing helpers, populated protocol and metadata fields from SL-0
- **Interfaces consumed**: SL-0 optional type fields, existing `_send_request(...)`, existing `ManagedClient.status`, existing `_connect_stdio(...)`, `_connect_remote_stream(...)`, and `adopt_process(...)` connection flows
- **Parallel-safe**: no
- **Tasks**:
  - test: Add an initialize test proving the client sends `protocolVersion: "2025-11-25"` first and stores the returned `protocolVersion` on both `ManagedClient.status.protocol_version` and the public server status.
  - test: Add compatibility tests for initialize responses returning `2024-11-05`, `2025-03-26`, and `2025-06-18`, proving connection setup continues and records the returned version.
  - test: Add a current-protocol `tools/list` indexing test with `title`, `icons`, `outputSchema`, `annotations`, `execution: {"taskSupport": "optional"}`, and unknown extra fields, proving the known fields and `raw_metadata` survive.
  - test: Add a missing-optional-fields test proving old tool payloads still construct `ToolInfo` with existing fields and no new required metadata.
  - test: Add a JSON Schema dialect default test proving absent `$schema` yields the 2020-12 default and explicit `$schema` is preserved.
  - test: Add resource, prompt, and prompt-argument metadata tests for `title`, `icons`, `annotations`, and unknown extra fields.
  - impl: Add `PREFERRED_PROTOCOL_VERSION = "2025-11-25"` and an ordered `SUPPORTED_PROTOCOL_VERSIONS` tuple including `2025-11-25`, `2025-06-18`, `2025-03-26`, and `2024-11-05`.
  - impl: Update `_send_initialize(...)` to capture the initialize result, validate or tolerate the returned `protocolVersion`, record it on `managed.status.protocol_version`, and record returned `capabilities` on `managed.status.server_capabilities`.
  - impl: Keep initialize fallback conservative: if a legacy server rejects the preferred version with a clear protocol-version initialize error, retry once with `2024-11-05`; otherwise preserve the existing exception behavior.
  - impl: Extract duplicated stdio/remote/adopted-process indexing into private helpers such as `_index_tools(...)`, `_index_resources(...)`, and `_index_prompts(...)` so all transport paths preserve metadata consistently.
  - impl: Treat annotations as untrusted metadata only; do not change `risk_hint` or policy decisions based solely on them in this phase.
  - verify: `uv run pytest tests/test_client_manager.py -k "initialize or protocol or metadata or schema_dialect or resources or prompts"`
  - verify: `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py`

### SL-2 — Gateway Metadata Surfaces

- **Scope**: Surface negotiated protocol and modern tool metadata through gateway tool outputs while keeping existing `catalog_search`, `describe`, and `health` clients compatible.
- **Owned files**: `src/pmcp/tools/handlers.py`, `tests/test_tools.py`
- **Interfaces provided**: additive `CapabilityCard` population, additive `SchemaCard` population, additive `ServerHealthInfo` protocol fields
- **Interfaces consumed**: SL-0 output fields, SL-1 populated `ToolInfo` and `ServerStatus` metadata, existing `GatewayTools.catalog_search(...)`, `GatewayTools.describe(...)`, `GatewayTools.health(...)`
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a `catalog_search` test proving capability cards can include compact `title`, `icons`, `execution`, and `schema_dialect` fields while still omitting full input/output schemas.
  - test: Add a `catalog_search` compatibility test proving old `ToolInfo` fixtures without metadata still serialize with existing fields.
  - test: Add a `describe` test proving output schema, annotations, execution, schema dialect, title, and icons are returned for a modern tool.
  - test: Add a `describe` safety test proving annotations are surfaced but do not override existing risk-based safety notes in this phase.
  - test: Add a `health` test proving protocol version and server capabilities are present when manager status includes them and absent/null-compatible when not.
  - impl: Populate new `CapabilityCard` fields from `ToolInfo` without adding schema bodies to catalog results.
  - impl: Populate new `SchemaCard` fields from `ToolInfo`, including `output_schema` and modern metadata.
  - impl: Populate `ServerHealthInfo.protocol_version` and `server_capabilities` from `ServerStatus`.
  - verify: `uv run pytest tests/test_tools.py -k "catalog_search or describe or health or protocol or metadata"`
  - verify: `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`

### SL-3 — CLI Protocol Status Surfaces

- **Scope**: Update `pmcp status` to preserve protocol metadata in JSON and show concise protocol details in verbose human output.
- **Owned files**: `src/pmcp/cli.py`, `tests/test_cli.py`
- **Interfaces provided**: `pmcp status --json` server entries with `protocol_version` and `server_capabilities`, `pmcp status --verbose` human protocol display
- **Interfaces consumed**: SL-0 `ServerStatus` and live gateway status fields, SL-1 local fallback manager statuses, SL-2 live `gateway.health` output
- **Parallel-safe**: yes
- **Tasks**:
  - test: Add a live JSON status test proving `protocol_version` and `server_capabilities` from `gateway.health` pass through unchanged.
  - test: Add a live verbose human status test proving connected servers display concise protocol version text when present.
  - test: Add a local fallback JSON status test proving `ServerStatus.protocol_version` and `server_capabilities` are included after direct manager connection.
  - test: Add a compatibility test proving status output remains valid when protocol fields are missing.
  - impl: Include protocol fields in local fallback JSON server entries.
  - impl: In live human status, append `protocol=<version>` only under `--verbose` and only when present.
  - impl: In local fallback human status, append the same verbose protocol detail without changing non-verbose output.
  - verify: `uv run pytest tests/test_cli.py -k "status and (protocol or json or verbose or live)"`
  - verify: `uv run ruff check src/pmcp/cli.py tests/test_cli.py`

### SL-4 — Documentation Impact and Roadmap Closeout

- **Scope**: Record the user-visible protocol/metadata behavior and close the roadmap/plan checklists after implementation and verification.
- **Owned files**: `README.md`, `CHANGELOG.md`, `specs/phase-plans-v3.md`, `plans/phase-plan-v3-proto.md`
- **Interfaces provided**: documentation for protocol negotiation, metadata preservation, JSON Schema dialect defaults, and completed PROTO acceptance checklist
- **Interfaces consumed**: SL-1 negotiated protocol behavior, SL-2 gateway metadata outputs, SL-3 CLI status outputs, verification results from all lanes
- **Parallel-safe**: no
- **Tasks**:
  - impl: Add a README note in the gateway/status or progressive-disclosure section explaining that PMCP negotiates current MCP protocol versions while preserving older server compatibility.
  - impl: Document that `gateway.catalog_search` stays compact and `gateway.describe` carries richer metadata including output schema, annotations, execution/task support, icons, and schema dialect.
  - impl: Add an Unreleased CHANGELOG entry for protocol negotiation and metadata preservation if this branch is release-bound.
  - impl: Mark Phase 1 exit criteria complete in `specs/phase-plans-v3.md` only after implementation and verification complete.
  - impl: Mark this plan's interface gates and acceptance criteria complete and record any execution deviations.
  - verify: `uv run ruff check README.md CHANGELOG.md specs/phase-plans-v3.md plans/phase-plan-v3-proto.md` is not applicable; manually review markdown formatting instead.

## Verification

Lane-specific verification:

- `uv run ruff check src/pmcp/types.py`
- `uv run pytest tests/test_client_manager.py -k "initialize or protocol or metadata or schema_dialect or resources or prompts"`
- `uv run ruff check src/pmcp/client/manager.py tests/test_client_manager.py`
- `uv run pytest tests/test_tools.py -k "catalog_search or describe or health or protocol or metadata"`
- `uv run ruff check src/pmcp/tools/handlers.py tests/test_tools.py`
- `uv run pytest tests/test_cli.py -k "status and (protocol or json or verbose or live)"`
- `uv run ruff check src/pmcp/cli.py tests/test_cli.py`

Whole-phase regression:

- `uv run pytest tests/test_client_manager.py tests/test_tools.py tests/test_cli.py`
- `uv run pytest tests/test_http_transport.py tests/test_transport_http.py tests/test_server.py -q` if protocol/status metadata affects live gateway HTTP outputs.
- `uv run ruff check src/ tests/`
- `uv run ruff format --check src/ tests/`
- `uv run mypy src/pmcp --exclude baml_client`
- `uv run pytest -q` before release handoff if time permits.

## Acceptance Criteria

- [x] Downstream initialize sends the current preferred protocol version and records the negotiated protocol version for stdio, SSE, streamable HTTP, and adopted-process connections.
- [x] Servers responding with older supported protocol versions still connect and preserve existing tool/resource/prompt behavior.
- [x] Tool metadata preserves `title`, `icons`, `outputSchema`, `annotations`, `execution.taskSupport`, unknown extra fields, and JSON Schema dialect information without changing policy decisions.
- [x] Resource, resource template where represented, prompt, and prompt-argument metadata preserve modern display fields and raw metadata where available.
- [x] `gateway.catalog_search` remains compact and backward compatible while exposing only small additive metadata fields.
- [x] `gateway.describe` exposes richer metadata and output schema additively.
- [x] `gateway.health` and `pmcp status` can show negotiated protocol versions without breaking older live snapshots or local fallback status.
- [x] Tests cover old-protocol servers, current-protocol servers, missing optional fields, unknown extra fields, and schema dialect defaults.
- [x] Documentation explicitly treats annotations as untrusted hints and notes the 2020-12 JSON Schema default.

## Execution Notes

- Verification preflight deviation: `scripts/preflight.sh` is not present in this repo, so phase verification used the documented `uv run` commands directly.
- Existing `pmcp status --verbose` local fallback intentionally avoids eager downstream connection; live verbose status and JSON local fallback surface protocol metadata without forcing every server to connect.
