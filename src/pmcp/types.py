"""Type definitions for MCP Gateway using Pydantic."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# === Transport Types ===

GatewayTransport = Literal["stdio", "http"]

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


McpServerConfig = Annotated[
    LocalMcpServerConfig | RemoteMcpServerConfig,
    Field(discriminator="type"),
]


class McpConfigFile(BaseModel):
    """Structure of .mcp.json files."""

    model_config = ConfigDict(extra="ignore")

    mcpServers: dict[str, McpServerConfig] = Field(default_factory=dict)
    disableAutoStart: list[str] = Field(default_factory=list)


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


class ToolInfo(BaseModel):
    """Internal tool information."""

    tool_id: str  # Normalized: server_name::tool_name
    server_name: str
    tool_name: str
    description: str
    short_description: str  # Truncated for catalog
    input_schema: dict[str, Any]
    tags: list[str]
    risk_hint: RiskHint


class ResourceInfo(BaseModel):
    """Internal resource information."""

    resource_id: str  # Normalized: server_name::uri
    server_name: str
    uri: str
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None


class PromptArgumentInfo(BaseModel):
    """Prompt argument information."""

    name: str
    description: str | None = None
    required: bool = False


class PromptInfo(BaseModel):
    """Internal prompt information."""

    prompt_id: str  # Normalized: server_name::name
    server_name: str
    name: str
    description: str | None = None
    arguments: list[PromptArgumentInfo] | None = None


class ServerStatus(BaseModel):
    """Status of a connected server."""

    name: str
    status: ServerStatusEnum
    tool_count: int
    resource_count: int = 0
    prompt_count: int = 0
    last_error: str | None = None
    last_connected_at: float | None = None
    # Health monitoring fields
    pending_request_count: int = 0  # Number of in-flight requests
    last_activity_at: float | None = None  # Last heartbeat from server
    avg_response_time_ms: float | None = None  # Rolling average response time


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
    short_description: str
    tags: list[str]
    availability: Literal["online", "offline"]
    risk_hint: str
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
    description: str
    args: list[ArgInfo]
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

    tool_id: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    options: InvokeOptions | None = None


class InvokeOutput(BaseModel):
    """Output for gateway.invoke."""

    tool_id: str
    ok: bool
    result: Any | None = None
    truncated: bool
    summary: str | None = None
    raw_size_estimate: int
    errors: list[str] | None = None
    update_warning: str | None = None
    feedback_hint: str | None = None


class RefreshInput(BaseModel):
    """Input for gateway.refresh."""

    source: Literal["claude_config", "custom"] | None = None
    reason: str | None = None


class RefreshOutput(BaseModel):
    """Output for gateway.refresh."""

    ok: bool
    servers_seen: int
    servers_online: int
    tools_indexed: int
    revision_id: str
    errors: list[str] | None = None


class ServerHealthInfo(BaseModel):
    """Server info in health output."""

    name: str
    status: str
    tool_count: int
    error: str | None = None


class HealthOutput(BaseModel):
    """Output for gateway.health."""

    revision_id: str
    servers: list[ServerHealthInfo]
    last_refresh_ts: float


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
    auth_mode: Literal["api_key", "unknown"] | None = None
    auth_methods: list[str] | None = None
    alternative_env_vars: list[str] | None = None
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
    credential: str = Field(min_length=1, description="Secret token/API key to store")
    env_var: str | None = Field(
        default=None,
        description="Override environment variable key to store into",
    )
    scope: Literal["user", "project"] = Field(
        default="user", description="Where to store credentials"
    )


class AuthConnectOutput(BaseModel):
    """Output from gateway.auth_connect."""

    ok: bool
    server: str
    message: str
    env_var: str | None = None
    env_path: str | None = None
    next_step: str | None = None


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
