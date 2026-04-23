"""Type definitions for MCP Gateway using Pydantic."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# === Transport Types ===

GatewayTransport = Literal["stdio", "http"]

AuthState = Literal[
    "none",
    "missing_auth",
    "insufficient_scope",
    "elicitation_required",
    "policy_denied",
    "unknown",
]

AuthEventKind = Literal[
    "missing_credential",
    "credential_stored",
    "remote_auth_challenge",
    "insufficient_scope",
    "url_elicitation_required",
    "url_elicitation_acknowledged",
    "policy_denied",
]


class AuthStateSemanticsInfo(BaseModel):
    """Machine-readable operator semantics for an auth state."""

    meaning: str
    primary_next_action: str
    evidence_fields: list[str] = Field(default_factory=list)


DEFAULT_AUTH_STATE_SEMANTICS: dict[AuthState, AuthStateSemanticsInfo] = {
    "none": AuthStateSemanticsInfo(
        meaning="No auth action is currently required.",
        primary_next_action="No operator auth action is required.",
        evidence_fields=[],
    ),
    "missing_auth": AuthStateSemanticsInfo(
        meaning="A required credential or credential-backed header is unavailable.",
        primary_next_action="Provide the missing credential with gateway.auth_connect or configure the named environment variable.",
        evidence_fields=["missing_env_vars", "auth_methods", "auth_metadata"],
    ),
    "insufficient_scope": AuthStateSemanticsInfo(
        meaning="A credential was present but lacks one or more required scopes.",
        primary_next_action="Grant the missing scopes with the upstream provider, then retry.",
        evidence_fields=["auth_challenge.missing_scopes", "auth_metadata"],
    ),
    "elicitation_required": AuthStateSemanticsInfo(
        meaning="The remote server requires an out-of-band URL consent flow.",
        primary_next_action="Open the sanitized URL, complete consent, then acknowledge the elicitation with gateway.auth_connect.",
        evidence_fields=["url_elicitations", "url_elicitation"],
    ),
    "policy_denied": AuthStateSemanticsInfo(
        meaning="PMCP policy refused the requested auth-sensitive action.",
        primary_next_action="Review PMCP policy configuration before retrying.",
        evidence_fields=["next_step"],
    ),
    "unknown": AuthStateSemanticsInfo(
        meaning="PMCP could not classify the auth condition.",
        primary_next_action="Inspect sanitized diagnostics and retry after resolving the reported condition.",
        evidence_fields=["error", "next_step"],
    ),
}


class TraceContextInfo(BaseModel):
    """OpenTelemetry-style trace context accepted by PMCP-owned surfaces."""

    traceparent: str | None = None
    tracestate: str | None = None
    baggage: str | None = None


class GatewayAuditEvent(BaseModel):
    """Redacted structured audit event for gateway action boundaries."""

    timestamp: float
    method: str
    action: str
    outcome: Literal["success", "failure", "refused"]
    latency_ms: int
    server_name: str | None = None
    tool_id: str | None = None
    resource_id: str | None = None
    prompt_id: str | None = None
    task_id: str | None = None
    protocol_version: str | None = None
    auth_state: AuthState = "none"
    auth_event: AuthEventKind | None = None
    error: str | None = None
    trace_present: bool = False


class GatewayDiagnosticsInfo(BaseModel):
    """Safe gateway/proxy diagnostics for health, status, and doctor output."""

    transport: str = "stdio"
    audit_enabled: bool = True
    audit_buffer_size: int = 0
    trace_context_supported: bool = True
    trace_context_keys: list[str] = Field(
        default_factory=lambda: ["traceparent", "tracestate", "baggage"]
    )
    protocol_version_visible: bool = True
    header_compatibility: dict[str, str] = Field(default_factory=dict)
    session_compatibility: dict[str, str] = Field(default_factory=dict)
    auth_metadata_present: bool = False
    rate_limit_enabled: bool = False
    rate_limit_rpm: int | None = None
    auth_state_semantics: dict[AuthState, AuthStateSemanticsInfo] = Field(
        default_factory=lambda: DEFAULT_AUTH_STATE_SEMANTICS.copy()
    )


class AuthMetadataInfo(BaseModel):
    """Non-secret authorization metadata and discovery hints."""

    protected_resource_metadata_url: str | None = None
    authorization_server_metadata_url: str | None = None
    oidc_issuer_url: str | None = None
    oidc_discovery_url: str | None = None
    client_id_metadata_document_url: str | None = None
    declared_scopes: list[str] = Field(default_factory=list)
    granted_scopes: list[str] = Field(default_factory=list)
    missing_scopes: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class AuthChallengeInfo(BaseModel):
    """Parsed non-secret details from an authorization challenge."""

    scheme: str | None = None
    resource_metadata_url: str | None = None
    scope: str | None = None
    missing_scopes: list[str] = Field(default_factory=list)
    error: str | None = None
    error_description: str | None = None


class UrlElicitationInfo(BaseModel):
    """URL-mode elicitation details safe to display to users."""

    elicitation_id: str
    url: str
    message: str | None = None
    next_step: str | None = None


# === Config Types ===


class _DictLikeModel(BaseModel):
    """Provide lightweight dict-style compatibility for config models."""

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


class LocalMcpServerConfig(_DictLikeModel):
    """Local process-backed MCP server configuration."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["local"] = "local"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[str, str] | None = None


class RemoteMcpServerConfig(_DictLikeModel):
    """Remote MCP server configuration."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["remote", "sse", "http", "streamable-http"] = "remote"
    url: str
    headers: dict[str, str] | None = None
    protected_resource_metadata_url: str | None = None
    authorization_server_metadata_url: str | None = None
    oidc_issuer_url: str | None = None
    oidc_discovery_url: str | None = None
    client_id_metadata_document_url: str | None = None
    declared_scopes: list[str] = Field(default_factory=list)
    supports_url_elicitation: bool = False


McpServerConfig = Annotated[
    LocalMcpServerConfig | RemoteMcpServerConfig,
    Field(discriminator="type"),
]


class McpConfigFile(BaseModel):
    """Structure of .mcp.json files."""

    model_config = ConfigDict(extra="ignore")

    mcpServers: dict[str, McpServerConfig] = Field(default_factory=dict)
    autoStart: list[str] = Field(default_factory=list)
    disableAutoStart: list[str] = Field(default_factory=list)


ConfigSourceName = Literal["project", "user", "custom"]


class ConfigSourceInfo(BaseModel):
    """Source metadata for a discovered MCP config file."""

    source: ConfigSourceName
    path: str
    exists: bool = False
    error: str | None = None


class EffectiveConfigEntry(BaseModel):
    """Effective per-server configuration and startup status."""

    name: str
    status: str
    startup_policy: Literal["eager", "lazy", "skipped", "unknown"]
    startup_source: str | None = None
    source: (
        ConfigSourceName | Literal["manifest", "provisioned", "discovered"] | None
    ) = None
    source_path: str | None = None
    startup_skip_reason: str | None = None
    startup_env_var: str | None = None
    missing_env_vars: list[str] = Field(default_factory=list)
    auth_state: AuthState = "none"
    configured: bool = False
    manifest: bool = False
    provisioned: bool = False
    discovered: bool = False
    diagnostics: list[str] = Field(default_factory=list)


class ConfigStatusOutput(BaseModel):
    """Output for effective configuration administration status."""

    entries: list[EffectiveConfigEntry]
    sources: list[ConfigSourceInfo] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class StartupPolicyDiagnostic(BaseModel):
    """Non-secret diagnostic for startup policy administration."""

    code: str
    message: str
    source: ConfigSourceName | None = None
    path: str | None = None
    server_name: str | None = None


class StartupPolicySource(BaseModel):
    """Persisted startup policy lists from one config source."""

    source: ConfigSourceName
    path: str
    exists: bool
    autoStart: list[str] = Field(default_factory=list)
    disableAutoStart: list[str] = Field(default_factory=list)
    error: str | None = None


class StartupPolicyOperation(BaseModel):
    """Input for previewing or applying autoStart mutations."""

    operation: Literal["add", "remove", "set"]
    names: list[str] = Field(default_factory=list)
    source: ConfigSourceName | None = None
    path: str | None = None
    dry_run: bool = True
    apply: bool = False


class StartupPolicyPreview(BaseModel):
    """Preview/apply result for a startup policy mutation."""

    ok: bool
    source: ConfigSourceName | None = None
    path: str | None = None
    changed: bool = False
    dry_run: bool = True
    before_autoStart: list[str] = Field(default_factory=list)
    after_autoStart: list[str] = Field(default_factory=list)
    diagnostics: list[StartupPolicyDiagnostic] = Field(default_factory=list)
    message: str
    next_step: str | None = None


class StartupPolicyOutput(BaseModel):
    """Read-only persisted startup policy output."""

    sources: list[StartupPolicySource]
    diagnostics: list[StartupPolicyDiagnostic] = Field(default_factory=list)


class ResolvedServerConfig(BaseModel):
    """A server config resolved from a config file."""

    name: str
    source: Literal["project", "user", "custom", "manifest"]
    config: McpServerConfig


# === Registry Types ===


class RiskHint(str, Enum):
    """Risk level hint for tools."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class ServerStatusEnum(str, Enum):
    """Server connection status."""

    ONLINE = "online"
    OFFLINE = "offline"
    CONNECTING = "connecting"
    ERROR = "error"
    LAZY = "lazy"  # Registered but not yet connected (on-demand)


class RequestState(str, Enum):
    """State of a pending request."""

    PENDING = "pending"  # Awaiting response
    ACTIVE = "active"  # Received partial output (heartbeat)
    STALLED = "stalled"  # No heartbeat for threshold period
    COMPLETED = "completed"  # Successfully resolved
    CANCELLED = "cancelled"  # User cancelled
    TIMEOUT = "timeout"  # Hard timeout reached


TaskSupportMode = Literal["forbidden", "optional", "required"]
McpTaskStatus = Literal[
    "working",
    "input_required",
    "completed",
    "failed",
    "cancelled",
]


class ToolInfo(BaseModel):
    """Internal tool information."""

    tool_id: str  # Normalized: server_name::tool_name
    server_name: str
    tool_name: str
    title: str | None = None
    description: str
    short_description: str  # Truncated for catalog
    input_schema: dict[str, Any]
    icons: list[dict[str, Any]] | None = None
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    schema_dialect: str = "https://json-schema.org/draft/2020-12/schema"
    raw_metadata: dict[str, Any] | None = None
    tags: list[str]
    risk_hint: RiskHint


class ResourceInfo(BaseModel):
    """Internal resource information."""

    resource_id: str  # Normalized: server_name::uri
    server_name: str
    uri: str
    name: str | None = None
    title: str | None = None
    description: str | None = None
    mime_type: str | None = None
    icons: list[dict[str, Any]] | None = None
    annotations: dict[str, Any] | None = None
    raw_metadata: dict[str, Any] | None = None


class PromptArgumentInfo(BaseModel):
    """Prompt argument information."""

    name: str
    title: str | None = None
    description: str | None = None
    required: bool = False
    raw_metadata: dict[str, Any] | None = None


class PromptInfo(BaseModel):
    """Internal prompt information."""

    prompt_id: str  # Normalized: server_name::name
    server_name: str
    name: str
    title: str | None = None
    description: str | None = None
    arguments: list[PromptArgumentInfo] | None = None
    icons: list[dict[str, Any]] | None = None
    annotations: dict[str, Any] | None = None
    raw_metadata: dict[str, Any] | None = None


class ServerStatus(BaseModel):
    """Status of a connected server."""

    name: str
    status: ServerStatusEnum
    tool_count: int
    resource_count: int = 0
    prompt_count: int = 0
    protocol_version: str | None = None
    server_capabilities: dict[str, Any] | None = None
    last_error: str | None = None
    last_connected_at: float | None = None
    # Health monitoring fields
    pending_request_count: int = 0  # Number of in-flight requests
    last_activity_at: float | None = None  # Last heartbeat from server
    avg_response_time_ms: float | None = None  # Rolling average response time


class McpTaskInfo(BaseModel):
    """Public view of a downstream MCP task."""

    task_id: str
    status: McpTaskStatus | str | None = None
    status_message: str | None = None
    created_at: float | None = None
    updated_at: float | None = None
    ttl: int | None = None
    poll_interval: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class McpTaskRecord(McpTaskInfo):
    """Transient task registry record tracked by PMCP."""

    server_name: str
    tool_id: str | None = None
    local_request_id: str | None = None
    requestor_context: dict[str, Any] | None = None


class TaskMetadataInput(BaseModel):
    """Task metadata for task-augmented tool invocation."""

    enabled: bool = True
    metadata: dict[str, Any] | None = None
    ttl: int | None = None
    poll_interval: float | None = None
    requestor_context: dict[str, Any] | None = None


class TasksListInput(BaseModel):
    """Input for gateway.tasks_list."""

    server_name: str | None = None
    cursor: str | None = None


class TasksListOutput(BaseModel):
    """Output for gateway.tasks_list."""

    ok: bool
    tasks: list[McpTaskInfo] = Field(default_factory=list)
    next_cursor: str | None = None
    errors: list[str] | None = None


class TasksGetInput(BaseModel):
    """Input for gateway.tasks_get."""

    server_name: str = Field(min_length=1)
    task_id: str = Field(min_length=1)


class TasksGetOutput(BaseModel):
    """Output for gateway.tasks_get."""

    ok: bool
    task: McpTaskInfo | None = None
    errors: list[str] | None = None


class TasksResultInput(BaseModel):
    """Input for gateway.tasks_result."""

    server_name: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    options: InvokeOptions | None = None


class TasksResultOutput(BaseModel):
    """Output for gateway.tasks_result."""

    ok: bool
    task: McpTaskInfo | None = None
    result: Any | None = None
    truncated: bool = False
    summary: str | None = None
    raw_size_estimate: int = 0
    errors: list[str] | None = None


class TasksCancelInput(BaseModel):
    """Input for gateway.tasks_cancel."""

    server_name: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    force: bool = False


class TasksCancelOutput(BaseModel):
    """Output for gateway.tasks_cancel."""

    ok: bool
    task: McpTaskInfo | None = None
    status: str
    message: str
    errors: list[str] | None = None


# === Gateway Tool Input/Output Types ===


class CatalogFilters(BaseModel):
    """Filters for catalog search."""

    server: str | None = None
    tags: list[str] | None = None
    risk_max: Literal["low", "medium", "high"] | None = None


class CatalogSearchInput(BaseModel):
    """Input for gateway.catalog_search."""

    query: str | None = None
    filters: CatalogFilters | None = None
    limit: int = Field(default=20, ge=1, le=100)
    include_offline: bool = False


class CapabilityCard(BaseModel):
    """Compact tool representation for catalog results."""

    tool_id: str
    server: str
    tool_name: str
    title: str | None = None
    short_description: str
    tags: list[str]
    availability: Literal["online", "offline"]
    risk_hint: str
    icons: list[dict[str, Any]] | None = None
    execution: dict[str, Any] | None = None
    schema_dialect: str | None = None
    code_hint: str | None = (
        None  # L1: Ultra-terse code pattern hint (e.g., "loop", "filter")
    )


class CatalogSearchOutput(BaseModel):
    """Output for gateway.catalog_search."""

    results: list[CapabilityCard]
    total_available: int
    truncated: bool
    stale_updates: list[str] | None = None


class DescribeInput(BaseModel):
    """Input for gateway.describe."""

    tool_id: str = Field(min_length=1)


class ArgInfo(BaseModel):
    """Argument information for schema card."""

    name: str
    type: str
    required: bool
    short_description: str
    examples: list[Any] | None = None


class InvokeTemplate(BaseModel):
    """Template for invoking a tool via gateway.invoke."""

    tool_id: str
    arguments: dict[str, str]  # arg_name -> description placeholder


class SchemaCard(BaseModel):
    """Detailed tool information for describe output."""

    server: str
    tool_name: str
    title: str | None = None
    description: str
    icons: list[dict[str, Any]] | None = None
    args: list[ArgInfo]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    schema_dialect: str | None = None
    constraints: list[str] | None = None
    safety_notes: list[str] | None = None
    # Direct invocation template
    invoke_as: str = "gateway.invoke"
    invoke_template: InvokeTemplate | None = None
    # L2: Minimal code example (3-4 lines, opt-in via guidance config)
    code_snippet: str | None = None
    update_warning: str | None = None
    feedback_hint: str | None = None


class InvokeOptions(BaseModel):
    """Options for tool invocation."""

    timeout_ms: int = Field(default=30000, ge=1000, le=300000)
    max_output_chars: int | None = Field(default=None, ge=100, le=100000)
    redact_secrets: bool = False


class InvokeInput(BaseModel):
    """Input for gateway.invoke."""

    model_config = ConfigDict(populate_by_name=True)

    tool_id: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    task: TaskMetadataInput | None = None
    options: InvokeOptions | None = None
    trace_context: TraceContextInfo | None = None
    meta: dict[str, Any] | None = Field(default=None, alias="_meta")


class InvokeOutput(BaseModel):
    """Output for gateway.invoke."""

    tool_id: str
    ok: bool
    result: Any | None = None
    task: McpTaskInfo | None = None
    truncated: bool
    summary: str | None = None
    raw_size_estimate: int
    errors: list[str] | None = None
    update_warning: str | None = None
    feedback_hint: str | None = None
    missing_env_vars: list[str] = Field(default_factory=list)
    auth_state: AuthState = "none"
    next_step: str | None = None
    auth_methods: list[str] | None = None
    auth_metadata: AuthMetadataInfo | None = None
    auth_challenge: AuthChallengeInfo | None = None
    url_elicitations: list[UrlElicitationInfo] | None = None


class RefreshInput(BaseModel):
    """Input for gateway.refresh."""

    source: Literal["claude_config", "custom"] | None = None
    reason: str | None = None
    force: bool = False


class RefreshOutput(BaseModel):
    """Output for gateway.refresh."""

    ok: bool
    servers_seen: int
    servers_online: int
    tools_indexed: int
    revision_id: str
    errors: list[str] | None = None
    pending_requests_seen: int = 0
    pending_requests_cancelled: int = 0
    pending_requests_refused: int = 0
    pending_requests_remaining: int = 0
    mcp_tasks_seen: int = 0
    mcp_tasks_cancelled: int = 0
    mcp_tasks_refused: int = 0
    mcp_tasks_remaining: int = 0


class ConnectServerInput(BaseModel):
    """Input for gateway.connect_server."""

    server_name: str = Field(min_length=1, description="Server to connect")


class DisconnectServerInput(BaseModel):
    """Input for gateway.disconnect_server."""

    server_name: str = Field(min_length=1, description="Server to disconnect")
    force: bool = False


class RestartServerInput(BaseModel):
    """Input for gateway.restart_server."""

    server_name: str = Field(min_length=1, description="Server to restart")
    force: bool = False


class LifecycleServerOutput(BaseModel):
    """Output for gateway server lifecycle controls."""

    ok: bool
    server: str
    action: Literal["connect", "disconnect", "restart"]
    prior_status: str
    new_status: str
    cancelled_request_count: int = 0
    active_task_count: int = 0
    cancelled_task_count: int = 0
    message: str
    errors: list[str] | None = None
    missing_env_vars: list[str] = Field(default_factory=list)
    auth_state: AuthState = "none"
    next_step: str | None = None
    auth_methods: list[str] | None = None
    auth_metadata: AuthMetadataInfo | None = None
    auth_challenge: AuthChallengeInfo | None = None
    url_elicitations: list[UrlElicitationInfo] | None = None


class ServerHealthInfo(BaseModel):
    """Server info in health output."""

    name: str
    status: str
    tool_count: int
    protocol_version: str | None = None
    server_capabilities: dict[str, Any] | None = None
    error: str | None = None
    startup_policy: Literal["eager", "lazy", "skipped", "unknown"] | None = None
    startup_source: str | None = None
    startup_skip_reason: str | None = None
    startup_env_var: str | None = None
    missing_env_vars: list[str] = Field(default_factory=list)
    auth_state: AuthState = "none"
    next_step: str | None = None
    auth_methods: list[str] | None = None
    auth_metadata: AuthMetadataInfo | None = None
    auth_challenge: AuthChallengeInfo | None = None
    url_elicitations: list[UrlElicitationInfo] | None = None


class HealthOutput(BaseModel):
    """Output for gateway.health."""

    revision_id: str
    servers: list[ServerHealthInfo]
    last_refresh_ts: float
    gateway_diagnostics: GatewayDiagnosticsInfo | None = None
    audit_events: list[GatewayAuditEvent] | None = None


# === Pending Request Monitoring Types ===


class ListPendingInput(BaseModel):
    """Input for gateway.list_pending."""

    server: str | None = None  # Filter by server (optional)


class PendingRequestInfo(BaseModel):
    """Public view of a pending request."""

    request_id: str  # Global unique ID (server::local_id)
    server_name: str
    tool_id: str
    started_at_iso: str  # ISO timestamp
    elapsed_seconds: float
    timeout_ms: int
    state: str  # RequestState value
    last_heartbeat_seconds_ago: float
    task_id: str | None = None
    task_status: str | None = None


class ListPendingOutput(BaseModel):
    """Output for gateway.list_pending."""

    requests: list[PendingRequestInfo]
    total_pending: int


class CancelInput(BaseModel):
    """Input for gateway.cancel."""

    request_id: str = Field(min_length=1)  # Format: "server_name::local_id"
    force: bool = False  # Force cancel even if heartbeat is recent


class CancelOutput(BaseModel):
    """Output for gateway.cancel."""

    request_id: str
    status: str  # "cancelled", "not_found", "already_complete", "refused"
    message: str
    was_stalled: bool  # True if request had no recent heartbeat
    elapsed_seconds: float | None = None


# === Policy Types ===


class ServerPolicy(BaseModel):
    """Server allow/deny policy."""

    allowlist: list[str] = Field(default_factory=list)
    denylist: list[str] = Field(default_factory=list)


class ToolPolicy(BaseModel):
    """Tool allow/deny policy."""

    allowlist: list[str] = Field(default_factory=list)  # Glob patterns
    denylist: list[str] = Field(default_factory=list)  # Glob patterns


class ResourcePolicy(BaseModel):
    """Resource allow/deny policy."""

    allowlist: list[str] = Field(default_factory=list)  # Glob patterns (server::uri)
    denylist: list[str] = Field(default_factory=list)  # Glob patterns


class PromptPolicy(BaseModel):
    """Prompt allow/deny policy."""

    allowlist: list[str] = Field(default_factory=list)  # Glob patterns (server::name)
    denylist: list[str] = Field(default_factory=list)  # Glob patterns


class LimitsPolicy(BaseModel):
    """Resource limits policy."""

    max_tools_per_server: int = 100
    max_output_bytes: int = 50000  # 50KB
    max_output_tokens: int = 4000


class RedactionPolicy(BaseModel):
    """Secret redaction policy."""

    patterns: list[str] = Field(default_factory=list)  # Regex patterns


class GatewayPolicy(BaseModel):
    """Complete gateway policy."""

    servers: ServerPolicy = Field(default_factory=ServerPolicy)
    tools: ToolPolicy = Field(default_factory=ToolPolicy)
    resources: ResourcePolicy = Field(default_factory=ResourcePolicy)
    prompts: PromptPolicy = Field(default_factory=PromptPolicy)
    limits: LimitsPolicy = Field(default_factory=LimitsPolicy)
    redaction: RedactionPolicy = Field(default_factory=RedactionPolicy)


# === Capability Request Types ===


class CapabilityRequestInput(BaseModel):
    """Input for gateway.request_capability."""

    query: str = Field(min_length=1, description="Natural language capability request")
    available_clis: list[str] | None = Field(
        default=None,
        description="Optional: CLIs known to be available in the environment",
    )


class CLIResolution(BaseModel):
    """CLI alternative resolution details."""

    name: str
    path: str | None = None
    help_output: str | None = None
    examples: list[str] | None = None


class CapabilityCandidate(BaseModel):
    """A single capability candidate from BAML matching."""

    name: str
    candidate_type: Literal["cli", "server"]
    relevance_score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    requires_api_key: bool = False
    api_key_available: bool = False  # True if key found in .env
    env_var: str | None = None
    env_instructions: str | None = None
    # Status hints
    is_installed: bool = False  # True if CLI is installed or server is running
    is_running: bool = False  # True if server is already connected


class CapabilityMatchResponse(BaseModel):
    """Response from gateway.request_capability with ranked candidates."""

    candidates: list[CapabilityCandidate]
    recommendation: str
    # Convenience: top candidate details
    top_candidate: CapabilityCandidate | None = None


class CapabilityResolution(BaseModel):
    """Result of capability resolution (legacy single-result mode)."""

    status: Literal[
        "use_cli",  # CLI available - use via Bash
        "available",  # MCP server already running with matching tools
        "provisioned",  # MCP server was installed and started
        "needs_api_key",  # MCP server exists but needs API key
        "not_available",  # No matching capability found
        "candidates",  # Single explicit match - call gateway.provision
        "pick_from_category",  # Multiple options in a category - caller should choose
    ]
    message: str

    # For candidates / pick_from_category status
    candidates: list[CapabilityCandidate] | None = None
    recommendation: str | None = None
    category_name: str | None = None  # Set when status="pick_from_category"

    # For use_cli status
    cli: CLIResolution | None = None

    # For available/provisioned status
    server: str | None = None
    new_tools: list[CapabilityCard] | None = None

    # For needs_api_key status
    env_var: str | None = None
    env_path: str | None = None
    env_instructions: str | None = None

    # For not_available status
    logged_for_discovery: bool = False
    search_guidance: str | None = None


class SearchRegistryResult(BaseModel):
    """A single result from the MCP registry search."""

    name: str
    package: str
    description: str
    transport: str | None = None
    env_vars: list[str] = Field(default_factory=list)
    server_card_url: str | None = None
    declared_capabilities: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)


class SearchRegistryInput(BaseModel):
    """Input for gateway.search_registry."""

    query: str = Field(
        min_length=1, description="Natural language capability description"
    )
    limit: int = Field(default=5, ge=1, le=20)


class SearchRegistryOutput(BaseModel):
    """Output for gateway.search_registry."""

    query: str
    results: list[SearchRegistryResult]
    next_step: str


class RegisterDiscoveredServerInput(BaseModel):
    """Input for gateway.register_discovered_server."""

    package: str = Field(
        min_length=1,
        description="npm package identifier (e.g. '@modelcontextprotocol/server-github')",
    )
    server_name: str = Field(
        min_length=1, description="Logical name for this server (e.g. 'github')"
    )
    env_vars: list[str] = Field(
        default_factory=list, description="Required environment variable names"
    )
    description: str = Field(
        default="", description="Short description of the server's purpose"
    )


class RegisterDiscoveredServerOutput(BaseModel):
    """Output for gateway.register_discovered_server."""

    ok: bool
    server_name: str
    registered: bool
    message: str
    next_step: str | None = None


class ProvisionInput(BaseModel):
    """Input for gateway.provision - install and start a specific server."""

    server_name: str = Field(
        min_length=1, description="Name of the server to provision from manifest"
    )


class ProvisionOutput(BaseModel):
    """Output from gateway.provision."""

    ok: bool
    server: str
    message: str
    # Job tracking for async installs
    job_id: str | None = None
    status: Literal["already_running", "started", "complete", "failed"] = "complete"
    # Tools (only populated when status is already_running or complete)
    new_tools: list[CapabilityCard] | None = None
    # If provisioning failed due to API key requirement
    needs_api_key: bool = False
    env_var: str | None = None
    env_instructions: str | None = None
    auth_required: bool = False
    auth_mode: Literal["api_key", "url_elicitation", "unknown"] | None = None
    auth_methods: list[str] | None = None
    alternative_env_vars: list[str] | None = None
    missing_env_vars: list[str] = Field(default_factory=list)
    auth_state: AuthState = "none"
    next_step: str | None = None
    auth_metadata: AuthMetadataInfo | None = None
    auth_challenge: AuthChallengeInfo | None = None
    url_elicitations: list[UrlElicitationInfo] | None = None
    update_warning: str | None = None
    feedback_hint: str | None = None


class SubmitFeedbackInput(BaseModel):
    """Input for gateway.submit_feedback."""

    title: str = Field(min_length=8, max_length=160)
    description: str = Field(min_length=1)
    issue_type: Literal["bug", "feature_request"] = Field(default="bug")
    subordinate_server: str | None = None
    failed_tool_call: str | None = None
    confirm_submission: bool = False


class SubmitFeedbackOutput(BaseModel):
    """Output for gateway.submit_feedback."""

    ok: bool
    submitted: bool
    repository: str
    repository_visibility: Literal["public", "private", "unknown"] = "unknown"
    issue_title: str
    issue_body: str
    issue_url: str | None = None
    issue_number: int | None = None
    authenticated: bool = False
    warning: str | None = None
    message: str


class UpdateServerInput(BaseModel):
    """Input for gateway.update_server."""

    server_name: str = Field(min_length=1, description="Server to update")


class UpdateServerOutput(BaseModel):
    """Output for gateway.update_server."""

    ok: bool
    server: str
    package_type: Literal["npm", "pypi", "cargo", "docker", "unknown"]
    package_name: str | None = None
    refreshed: bool = False
    latest_version: str | None = None
    message: str


class AuthConnectInput(BaseModel):
    """Input for gateway.auth_connect - save auth credentials for a server."""

    server_name: str = Field(
        min_length=1, description="Server requiring authentication"
    )
    credential: str | None = Field(
        default=None, min_length=1, description="Secret token/API key to store"
    )
    env_var: str | None = Field(
        default=None,
        description="Override environment variable key to store into",
    )
    scope: Literal["user", "project"] = Field(
        default="user", description="Where to store credentials"
    )
    auth_mode: Literal["api_key", "url_elicitation"] = "api_key"
    elicitation_id: str | None = None
    elicitation_url: str | None = None
    consent_acknowledged: bool = False


class AuthConnectOutput(BaseModel):
    """Output from gateway.auth_connect."""

    ok: bool
    server: str
    message: str
    env_var: str | None = None
    env_path: str | None = None
    next_step: str | None = None
    auth_state: AuthState = "none"
    url_elicitation: UrlElicitationInfo | None = None


class ProvisionStatusInput(BaseModel):
    """Input for gateway.provision_status - check job progress."""

    job_id: str = Field(min_length=1, description="Job ID from provision response")


class ProvisionJobStatus(BaseModel):
    """Output from gateway.provision_status."""

    job_id: str
    server: str
    status: Literal[
        "pending",
        "installing",
        "server_ready",
        "complete",
        "failed",
        "timeout",
        "not_found",
    ]
    progress: int = Field(ge=0, le=100, description="Progress percentage 0-100")
    message: str
    output_tail: list[str] = Field(
        default_factory=list, description="Last 5 lines of output"
    )
    elapsed_seconds: float = 0.0
    # Only populated when status is complete
    new_tools: list[CapabilityCard] | None = None
    error: str | None = None


class SyncEnvironmentInput(BaseModel):
    """Input for gateway.sync_environment."""

    platform: Literal["mac", "wsl", "linux", "windows"] | None = None
    detected_clis: list[str] | None = None


class SyncEnvironmentOutput(BaseModel):
    """Output for gateway.sync_environment."""

    platform: str
    detected_clis: list[str]
    message: str


# === Pre-built Descriptions Types ===


class PrebuiltToolInfo(BaseModel):
    """Serializable tool info for description cache."""

    name: str
    description: str
    short_description: str
    tags: list[str]
    risk_hint: str  # "low", "medium", "high"


class GeneratedServerDescriptions(BaseModel):
    """Pre-generated descriptions for a single server."""

    package: str  # e.g., "@playwright/mcp"
    version: str  # Package version when generated
    generated_at: str  # ISO timestamp
    capability_summary: str  # L1: For MCP instructions
    tools: list[PrebuiltToolInfo]  # L2: Tool cards


class DescriptionsCache(BaseModel):
    """Structure of .mcp-gateway/descriptions.yaml cache file."""

    generated_at: str  # ISO timestamp
    gateway_version: str
    servers: dict[str, GeneratedServerDescriptions]
