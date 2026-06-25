# MCP Specification Compliance

Target stable revision: `2025-11-25`

PMCP tracks current MCP compliance here so gateway behavior, tests, and operator
docs stay aligned when the upstream specification changes. The current stable
revision is documented in the MCP specification changelog:

- https://modelcontextprotocol.io/specification/2025-11-25/changelog
- https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
- https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- https://modelcontextprotocol.io/specification/draft/changelog

## Current-Stable Requirements

| Requirement | Status | PMCP surface | Evidence |
|-------------|--------|--------------|----------|
| Streamable HTTP rejects invalid configured `Origin` values with HTTP 403 before MCP request handling. | Compliant | `src/pmcp/transport/http.py` | `tests/test_http_transport.py::test_allowed_origins_rejects_invalid_origin_before_mcp_handler` |
| Resource Server insufficient-scope challenges return HTTP 403 and include `WWW-Authenticate` with `error="insufficient_scope"` and `scope` per SEP-835. | Compliant | `src/pmcp/transport/http.py` | `tests/test_http_transport.py::test_resource_server_insufficient_scope_gets_403_challenge` |
| Gateway tool input-validation failures surface as tool-execution errors instead of JSON-RPC protocol failures per SEP-1303. | Compliant | `src/pmcp/tools/handlers.py`, `src/pmcp/server.py` | `tests/test_tools.py::test_invoke_missing_required_arg`; `tests/test_server.py::test_lifecycle_tools_are_routed_through_call_tool_handler` |
| Tool schemas that omit `$schema` advertise JSON Schema 2020-12 as the default dialect per SEP-1613. | Compliant | `src/pmcp/client/manager.py`, `src/pmcp/tools/handlers.py`, `src/pmcp/types.py` | `tests/test_tools.py::test_catalog_search_uses_default_schema_dialect_when_schema_omits_marker`; `tests/test_tools.py::test_describe_uses_default_schema_dialect_when_schema_omits_marker` |
| Downstream tool/resource/prompt `icons` metadata is indexed, and tool icons pass through `gateway.catalog_search` and `gateway.describe` per SEP-973. | Compliant | `src/pmcp/client/manager.py`, `src/pmcp/tools/handlers.py` | `tests/test_tools.py::test_catalog_search_includes_compact_modern_metadata`; `tests/test_tools.py::test_describe_returns_modern_tool_metadata` |
| MCP server implementation metadata is populated when supported by the installed SDK. | SDK-limited | `src/pmcp/server.py` | `tests/test_baseline_constraints.py::test_mcp_sdk_implementation_description_surface_is_documented` records that the installed MCP SDK exposes `name`, `version`, `title`, `websiteUrl`, and `icons`, but no `description` field. PMCP therefore does not invent unsupported implementation metadata. |

## Draft Revision Impact

The draft revision after `2025-11-25` is not treated as a stable PMCP contract
yet. The draft changelog calls out the following migration areas for PMCP to
review when they become stable:

- Stateless/no-session Streamable HTTP removes protocol-level sessions and the
  `Mcp-Session-Id` header. Revisit `src/pmcp/transport/http.py`.
- Task support moves to `io.modelcontextprotocol/tasks`; `tasks/list` is
  removed, `tasks/result` changes to `tasks/get` polling, `tasks/update` is
  added, unsolicited task handles are allowed, MRTR is introduced, and
  `resultType` becomes required. Track SEP-2663 and SEP-2322, then revisit
  PMCP task APIs and release notes.
- `server/discover` becomes the up-front discovery RPC for supported protocol
  versions, capabilities, and identity. Track SEP-2575, then revisit gateway
  server metadata.
- `CacheableResult` changes result caching semantics. Revisit brokered result
  handling and cache boundaries for SEP-2549.
- Dynamic Client Registration changes toward Client ID Metadata Documents.
  Revisit auth setup and deployment docs.
- SSE resumability is removed. Revisit transport compatibility notes.

## Next-Stable Tracking Checklist

- [ ] Re-check `src/pmcp/transport/http.py` against stable stateless transport
  and SSE resumability changes.
- [ ] Re-check `src/pmcp/tools/handlers.py` for task result, polling, metadata,
  and `CacheableResult` changes.
- [ ] Re-check `src/pmcp/client/manager.py` for icons, schema dialect, and
  discovery metadata changes.
- [ ] Re-check task APIs for `io.modelcontextprotocol/tasks`, `tasks/get`,
  `tasks/update`, MRTR, unsolicited handles, and required `resultType`.
- [ ] Re-check release notes for any stable `server/discover`, authorization,
  and migration guidance operators must follow.
