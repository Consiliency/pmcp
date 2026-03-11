"""Gateway Tool Implementations."""

from __future__ import annotations

import logging
import os
import re
import json
import asyncio
import time
import platform
import shutil
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import quote_plus
from urllib.request import urlopen
from urllib.parse import urlencode

from dotenv import load_dotenv
from mcp.types import Tool
from pmcp import __version__ as PMCP_VERSION

from pmcp.client.manager import ClientManager
from pmcp.config.guidance import GuidanceConfig
from pmcp.config.loader import load_configs, manifest_server_to_config
from pmcp.errors import ErrorCode, GatewayException, make_error
from pmcp.identity import filter_self_references
from pmcp.manifest.code_patterns_loader import get_code_hint
from pmcp.manifest.environment import detect_platform, probe_clis
from pmcp.templates.code_snippets_loader import get_code_snippet
from pmcp.manifest.installer import (
    MissingApiKeyError,
    get_job_manager,
    InstallError,
)
from pmcp.manifest.loader import load_manifest
from pmcp.manifest.version_checker import (
    detect_package_type,
    get_package_version,
    is_version_newer,
)
from pmcp.policy.policy import PolicyManager
from pmcp.types import (
    ArgInfo,
    AuthConnectInput,
    AuthConnectOutput,
    CancelInput,
    CancelOutput,
    CapabilityCandidate,
    CapabilityCard,
    CapabilityRequestInput,
    CapabilityResolution,
    CatalogSearchInput,
    CatalogSearchOutput,
    DescribeInput,
    DescriptionsCache,
    HealthOutput,
    InvokeInput,
    InvokeOutput,
    InvokeTemplate,
    ListPendingInput,
    ListPendingOutput,
    PendingRequestInfo,
    ProvisionInput,
    ProvisionJobStatus,
    ProvisionOutput,
    ProvisionStatusInput,
    RefreshInput,
    RefreshOutput,
    RegisterDiscoveredServerInput,
    RegisterDiscoveredServerOutput,
    RiskHint,
    SchemaCard,
    SearchRegistryInput,
    SearchRegistryOutput,
    SearchRegistryResult,
    ServerHealthInfo,
    SyncEnvironmentInput,
    SyncEnvironmentOutput,
    SubmitFeedbackInput,
    SubmitFeedbackOutput,
    ToolInfo,
    UpdateServerInput,
    UpdateServerOutput,
    LocalMcpServerConfig,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
)

from pmcp.manifest.loader import Manifest, ServerConfig

logger = logging.getLogger(__name__)

FEEDBACK_TOKEN_LIMIT = 4000

# Risk level ordering for filtering
RISK_ORDER = {"low": 1, "medium": 2, "high": 3, "unknown": 4}


def get_gateway_tool_definitions() -> list[Tool]:
    """Get MCP tool definitions for the gateway."""
    return [
        Tool(
            name="gateway.catalog_search",
            description=(
                "Search for available tools across all connected MCP servers. "
                "Returns compact capability cards without full schemas. "
                "Use filters to narrow results by server, tags, or risk level. "
                "Set include_offline=True to also discover provisionable servers not yet running. "
                "This is the primary tool discovery entry point."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to match against tool names, descriptions, and tags",
                    },
                    "filters": {
                        "type": "object",
                        "properties": {
                            "server": {
                                "type": "string",
                                "description": "Filter to tools from a specific server",
                            },
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Filter to tools with any of these tags",
                            },
                            "risk_max": {
                                "type": "string",
                                "enum": ["low", "medium", "high"],
                                "description": "Maximum risk level to include",
                            },
                        },
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                        "description": "Maximum number of results to return",
                    },
                    "include_offline": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include tools from offline servers",
                    },
                },
            },
        ),
        Tool(
            name="gateway.describe",
            description=(
                "Get detailed information about a specific tool, including its arguments and constraints. "
                "Use this before invoking a tool to understand its requirements."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tool_id": {
                        "type": "string",
                        "description": 'The tool ID in format "server_name::tool_name"',
                    },
                },
                "required": ["tool_id"],
            },
        ),
        Tool(
            name="gateway.invoke",
            description=(
                "Invoke a tool on a downstream MCP server. "
                "Arguments are validated against the tool schema before execution. "
                "Output is automatically truncated if too large."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tool_id": {
                        "type": "string",
                        "description": 'The tool ID in format "server_name::tool_name"',
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Arguments to pass to the tool (must match tool schema)",
                    },
                    "options": {
                        "type": "object",
                        "properties": {
                            "timeout_ms": {
                                "type": "integer",
                                "minimum": 1000,
                                "maximum": 300000,
                                "default": 30000,
                                "description": "Timeout in milliseconds",
                            },
                            "max_output_chars": {
                                "type": "integer",
                                "minimum": 100,
                                "maximum": 100000,
                                "description": "Maximum output characters (truncated if exceeded)",
                            },
                            "redact_secrets": {
                                "type": "boolean",
                                "default": False,
                                "description": "Redact detected secrets from output",
                            },
                        },
                    },
                },
                "required": ["tool_id"],
            },
        ),
        Tool(
            name="gateway.refresh",
            description=(
                "Reload backend MCP server configurations and reconnect. "
                "Use this when new MCP servers have been configured or to recover from connection errors."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["claude_config", "custom"],
                        "description": "Config source to reload from",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for refresh (for logging)",
                    },
                },
            },
        ),
        Tool(
            name="gateway.health",
            description=(
                "Get the health status of the gateway and all connected MCP servers. "
                "Shows server status, tool counts, and last refresh time."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="gateway.request_capability",
            description=(
                "Find and auto-provision the right tool for a task — describe what you need in natural language. "
                "Examples: 'scrape a website', 'search Slack messages', 'query Postgres', 'browse the web'. "
                "Matches against installed CLIs and 90+ provisionable MCP servers; starts the server automatically if needed. "
                "Prefer this over gateway.provision when you don't already know the exact server name."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description of the capability needed (e.g., 'I need to scrape a website', 'browser automation')",
                    },
                    "available_clis": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: CLIs known to be available in the environment",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="gateway.sync_environment",
            description=(
                "Sync environment information from the host. "
                "Detects the platform (mac/wsl/linux/windows) and probes for installed CLIs. "
                "This information is used to prefer CLIs over MCP servers when matching capabilities."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "enum": ["mac", "wsl", "linux", "windows"],
                        "description": "Override detected platform (optional)",
                    },
                    "detected_clis": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Override detected CLIs (optional)",
                    },
                },
            },
        ),
        Tool(
            name="gateway.provision",
            description=(
                "Provision (install and start) a specific MCP server from the manifest. "
                "Use this after reviewing candidates from gateway.request_capability. "
                "Returns immediately with a job_id for tracking. "
                "Poll gateway.provision_status to check progress. "
                "Use gateway.request_capability instead if you don't know the exact server name."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "description": "Name of the server to provision (from manifest)",
                    },
                },
                "required": ["server_name"],
            },
        ),
        Tool(
            name="gateway.update_server",
            description=(
                "Update a subordinate MCP server package to latest version and reconnect it. "
                "Use this when invoke/describe/provision warn that a newer version is available."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "description": "Name of server to update",
                    }
                },
                "required": ["server_name"],
            },
        ),
        Tool(
            name="gateway.auth_connect",
            description=(
                "Store credentials for a server and make them available to provisioning. "
                "Use this when gateway.provision reports missing authentication."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "server_name": {
                        "type": "string",
                        "description": "Server name that needs authentication",
                    },
                    "credential": {
                        "type": "string",
                        "description": "API key, token, or subscription credential to store",
                    },
                    "env_var": {
                        "type": "string",
                        "description": "Optional explicit environment variable key",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["user", "project"],
                        "default": "user",
                        "description": "Where to store the credential",
                    },
                },
                "required": ["server_name", "credential"],
            },
        ),
        Tool(
            name="gateway.submit_feedback",
            description=(
                "Prepare and optionally submit a PMCP feedback issue to GitHub. "
                "By default returns an exact preview payload; set confirm_submission=true to submit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Issue title",
                    },
                    "description": {
                        "type": "string",
                        "description": "Issue details (technical data only)",
                    },
                    "issue_type": {
                        "type": "string",
                        "enum": ["bug", "feature_request"],
                        "default": "bug",
                    },
                    "subordinate_server": {
                        "type": "string",
                        "description": "Subordinate MCP server involved (if known)",
                    },
                    "failed_tool_call": {
                        "type": "string",
                        "description": "Specific failed tool call (if known)",
                    },
                    "confirm_submission": {
                        "type": "boolean",
                        "default": False,
                        "description": "Set true only after user confirms submission",
                    },
                },
                "required": ["title", "description"],
            },
        ),
        Tool(
            name="gateway.provision_status",
            description=(
                "Check the status of a running server installation. "
                "Use after gateway.provision returns a job_id. "
                "Returns progress percentage, output log, and final tools when complete."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Job ID from gateway.provision response",
                    },
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="gateway.list_pending",
            description=(
                "List all pending tool invocations with health status. "
                "Shows elapsed time, heartbeat age, and current state for each request. "
                "Use this to monitor long-running operations before deciding to cancel."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "Filter to pending requests on a specific server (optional)",
                    },
                },
            },
        ),
        Tool(
            name="gateway.cancel",
            description=(
                "Cancel a pending tool invocation. "
                "By default, refuses to cancel healthy requests (recent heartbeat). "
                "Use force=true to cancel anyway. "
                "Use gateway.list_pending first to see request IDs and health status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": 'Request ID in format "server_name::local_id" from gateway.list_pending',
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": "Force cancel even if request is healthy (has recent heartbeat)",
                    },
                },
                "required": ["request_id"],
            },
        ),
        Tool(
            name="gateway.search_registry",
            description=(
                "Search the public MCP Registry for external servers not in the local manifest. "
                "Use this when gateway.request_capability returns not_available. "
                "Returns package names and metadata; call gateway.register_discovered_server then gateway.provision to install."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description of the capability needed",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 5,
                        "description": "Maximum number of results to return",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="gateway.register_discovered_server",
            description=(
                "Register an externally-discovered MCP server package so it can be provisioned. "
                "Call this after gateway.search_registry to register the chosen package, "
                "then call gateway.provision to install and start it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": "npm package identifier (e.g. '@modelcontextprotocol/server-github')",
                    },
                    "server_name": {
                        "type": "string",
                        "description": "Logical name for this server (e.g. 'github') used with gateway.provision",
                    },
                    "env_vars": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Required environment variable names (e.g. ['GITHUB_TOKEN'])",
                    },
                    "description": {
                        "type": "string",
                        "description": "Short description of the server's purpose",
                    },
                },
                "required": ["package", "server_name"],
            },
        ),
    ]


class GatewayTools:
    """Gateway tool handler implementations."""

    def __init__(
        self,
        client_manager: ClientManager,
        policy_manager: PolicyManager,
        project_root: Path | None = None,
        custom_config_path: Path | None = None,
        guidance_config: GuidanceConfig | None = None,
        descriptions_cache: DescriptionsCache | None = None,
    ) -> None:
        self._client_manager = client_manager
        self._policy_manager = policy_manager
        self._project_root = project_root
        self._custom_config_path = custom_config_path
        self._guidance_config = guidance_config
        self._descriptions_cache = descriptions_cache
        self._detected_clis: set[str] | None = None
        self._platform: str | None = None
        self._discovered_server_configs: dict[str, ServerConfig] = {}
        self._stale_check_cache: dict[str, tuple[float, str | None, str | None]] = {}
        self._stale_check_ttl_seconds = 6 * 60 * 60
        self._stale_index_interval_seconds = 60 * 60  # Re-index every hour
        self._stale_index_task: asyncio.Task[None] | None = None
        self._feedback_events: list[dict[str, Any]] = []
        self._provisioned_registry: dict[str, str | None] = (
            self._load_provisioned_registry()
        )

    @property
    def _provisioned_registry_path(self) -> Path:
        return Path.home() / ".config" / "pmcp" / "provisioned.json"

    def _load_provisioned_registry(self) -> dict[str, str | None]:
        """Load the persisted provisioned-server registry from disk."""
        path = self._provisioned_registry_path
        if not path.exists():
            return {}
        try:
            with open(path) as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if isinstance(k, str)}
        except Exception as e:
            logger.warning(f"Could not load provisioned registry: {e}")
            return {}

    def _save_provisioned_registry(self) -> None:
        """Persist the provisioned-server registry to disk."""
        path = self._provisioned_registry_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(self._provisioned_registry, f)
        except Exception as e:
            logger.warning(f"Could not save provisioned registry: {e}")

    def _register_provisioned_server(
        self, server_name: str, env_var: str | None
    ) -> None:
        """Record a successfully provisioned server in the registry."""
        self._provisioned_registry[server_name] = env_var
        self._save_provisioned_registry()

    # === Stale Version Indexer ===

    def start_stale_indexer(self) -> None:
        """Start the background stale-version indexing task."""
        if self._stale_index_task is None or self._stale_index_task.done():
            self._stale_index_task = asyncio.create_task(self._stale_indexer_loop())
            logger.info("Started stale-version indexer background task")

    def stop_stale_indexer(self) -> None:
        """Stop the background stale-version indexing task."""
        if self._stale_index_task and not self._stale_index_task.done():
            self._stale_index_task.cancel()
            self._stale_index_task = None
            logger.debug("Stopped stale-version indexer background task")

    async def _stale_indexer_loop(self) -> None:
        """Background loop: precompute stale version checks for all known servers."""
        while True:
            try:
                await self._run_stale_index()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"Stale indexer pass failed: {e}")
            try:
                await asyncio.sleep(self._stale_index_interval_seconds)
            except asyncio.CancelledError:
                break

    async def _run_stale_index(self) -> None:
        """One pass: fetch latest versions for all servers in the descriptions cache."""
        if not self._descriptions_cache:
            return
        now = time.time()
        servers = list(self._descriptions_cache.servers.items())
        for server_name, server_desc in servers:
            # Skip if cache entry is still fresh
            cached = self._stale_check_cache.get(server_name)
            if cached and (now - cached[0]) < self._stale_check_ttl_seconds:
                continue
            server_config = self._get_server_config_for_update(server_name)
            if not server_config or not server_config.command:
                continue
            try:
                latest, _ = await get_package_version(
                    server_config.command, server_config.args, timeout=5.0
                )
                self._stale_check_cache[server_name] = (
                    now,
                    server_desc.version,
                    latest,
                )
                if latest and is_version_newer(server_desc.version, latest):
                    logger.info(
                        f"Update available for '{server_name}': "
                        f"{server_desc.version} -> {latest}"
                    )
            except Exception as e:
                logger.debug(f"Stale check failed for '{server_name}': {e}")

    async def _ensure_server_for_tool(self, tool_id: str) -> bool:
        """Ensure server is connected for a tool, triggering lazy-start if needed.

        Args:
            tool_id: Tool ID in format "server::tool_name"

        Returns:
            True if server is ready, False if connection failed
        """
        # Parse server name from tool_id
        if "::" not in tool_id:
            return False
        server_name = tool_id.split("::")[0]

        # Check if server needs lazy-start
        if self._client_manager.is_lazy_server(server_name):
            logger.info(f"Triggering lazy-start for server: {server_name}")
            return await self._client_manager.ensure_connected(server_name)

        # Server is either online or in error state
        return self._client_manager.is_server_online(server_name)

    def _get_cached_tools_for_offline_servers(self) -> list[ToolInfo]:
        """Get tool info from cache for servers that are not currently online.

        Returns ToolInfo objects for offline servers so they can be
        included in catalog_search results when include_offline=True.
        """
        if not self._descriptions_cache:
            return []

        cached_tools: list[ToolInfo] = []

        for server_name, server_desc in self._descriptions_cache.servers.items():
            # Skip servers that are already online (live tools take precedence)
            if self._client_manager.is_server_online(server_name):
                continue

            # Check policy allows this server
            if not self._policy_manager.is_server_allowed(server_name):
                continue

            # Convert cached PrebuiltToolInfo to ToolInfo
            for tool in server_desc.tools:
                tool_id = f"{server_name}::{tool.name}"

                # Check policy allows this tool
                if not self._policy_manager.is_tool_allowed(tool_id):
                    continue

                # Convert risk_hint string to RiskHint enum
                risk = RiskHint.UNKNOWN
                if tool.risk_hint == "low":
                    risk = RiskHint.LOW
                elif tool.risk_hint == "medium":
                    risk = RiskHint.MEDIUM
                elif tool.risk_hint == "high":
                    risk = RiskHint.HIGH

                cached_tools.append(
                    ToolInfo(
                        tool_id=tool_id,
                        server_name=server_name,
                        tool_name=tool.name,
                        description=tool.description,
                        short_description=tool.short_description,
                        tags=tool.tags,
                        risk_hint=risk,
                        input_schema={},  # Not available in cache
                    )
                )

        return cached_tools

    async def catalog_search(self, input_data: dict[str, Any]) -> CatalogSearchOutput:
        """gateway.catalog_search - Search for available tools."""
        parsed = CatalogSearchInput.model_validate(input_data)

        tools = self._client_manager.get_all_tools()

        # Filter by policy
        tools = [t for t in tools if self._policy_manager.is_tool_allowed(t.tool_id)]

        # Filter by server online status OR merge cached tools
        if parsed.include_offline:
            # Merge cached tools for offline servers
            cached_tools = self._get_cached_tools_for_offline_servers()
            # Build set of existing tool_ids to avoid duplicates
            existing_ids = {t.tool_id for t in tools}
            # Add cached tools that aren't already present
            for ct in cached_tools:
                if ct.tool_id not in existing_ids:
                    tools.append(ct)
        else:
            # Default: only online servers
            tools = [
                t for t in tools if self._client_manager.is_server_online(t.server_name)
            ]

        total_available = len(tools)

        # Filter by server name
        if parsed.filters and parsed.filters.server:
            tools = [t for t in tools if t.server_name == parsed.filters.server]

        # Filter by tags
        if parsed.filters and parsed.filters.tags:
            filter_tags = [tag.lower() for tag in parsed.filters.tags]
            tools = [t for t in tools if any(tag in t.tags for tag in filter_tags)]

        # Filter by max risk level
        if parsed.filters and parsed.filters.risk_max:
            max_risk = RISK_ORDER.get(parsed.filters.risk_max, 4)
            tools = [
                t for t in tools if RISK_ORDER.get(t.risk_hint.value, 4) <= max_risk
            ]

        # Text search (if query provided) - word-based matching
        if parsed.query:
            query_words = parsed.query.lower().split()
            tools = [
                t
                for t in tools
                if any(
                    word in t.tool_name.lower()
                    or word in t.short_description.lower()
                    or any(word in tag for tag in t.tags)
                    for word in query_words
                )
            ]

        # Sort by relevance (if query) or alphabetically
        if parsed.query:
            query_lower = parsed.query.lower()

            def sort_key(t: Any) -> tuple[int, int, str]:
                exact = t.tool_name.lower() == query_lower
                starts = t.tool_name.lower().startswith(query_lower)
                return (0 if exact else 1, 0 if starts else 1, t.tool_name)

            tools.sort(key=sort_key)
        else:
            tools.sort(key=lambda t: t.tool_name)

        # Apply limit
        truncated = len(tools) > parsed.limit
        tools = tools[: parsed.limit]

        # Convert to capability cards
        results = []
        for t in tools:
            # Get code hint if guidance enabled
            code_hint = None
            if self._guidance_config and self._guidance_config.include_code_hints:
                code_hint = get_code_hint(t.tool_id, t.tool_name, t.short_description)
                # Trim to max length if configured
                if code_hint and len(code_hint) > self._guidance_config.max_hint_length:
                    code_hint = code_hint[: self._guidance_config.max_hint_length]

            results.append(
                CapabilityCard(
                    tool_id=t.tool_id,
                    server=t.server_name,
                    tool_name=t.tool_name,
                    short_description=t.short_description,
                    tags=t.tags,
                    availability="online"
                    if self._client_manager.is_server_online(t.server_name)
                    else "offline",
                    risk_hint=t.risk_hint.value,
                    code_hint=code_hint,
                )
            )

        # Collect stale-update notices from precomputed cache (no network call)
        stale_updates: list[str] | None = None
        if self._stale_check_cache:
            stale = [
                f"Update available for '{sn}': {current} -> {latest}. "
                f"Call gateway.update_server(server_name='{sn}') to update."
                for sn, (_, current, latest) in self._stale_check_cache.items()
                if current and latest and is_version_newer(current, latest)
            ]
            if stale:
                stale_updates = stale

        return CatalogSearchOutput(
            results=results,
            total_available=total_available,
            truncated=truncated,
            stale_updates=stale_updates,
        )

    async def describe(self, input_data: dict[str, Any]) -> SchemaCard:
        """gateway.describe - Get detailed info about a tool."""
        parsed = DescribeInput.model_validate(input_data)

        tool_info = self._client_manager.get_tool(parsed.tool_id)

        if not tool_info:
            # Tool not in registry - check if from lazy server
            if "::" in parsed.tool_id:
                server_name = parsed.tool_id.split("::")[0]
                if self._client_manager.is_lazy_server(server_name):
                    # Connect server to get tool info
                    connected = await self._ensure_server_for_tool(parsed.tool_id)
                    if connected:
                        tool_info = self._client_manager.get_tool(parsed.tool_id)

            if not tool_info:
                raise GatewayException(
                    ErrorCode.E301_TOOL_NOT_FOUND,
                    details={"tool_id": parsed.tool_id},
                )

        if not self._policy_manager.is_tool_allowed(parsed.tool_id):
            raise GatewayException(
                ErrorCode.E402_TOOL_DENIED,
                details={"tool_id": parsed.tool_id},
            )

        update_warning = await self._get_update_warning(tool_info.server_name)
        self._record_feedback_event(
            "invoke_attempt",
            {
                "tool_id": parsed.tool_id,
                "server": tool_info.server_name,
            },
        )

        # Extract args from schema
        args: list[ArgInfo] = []
        schema = tool_info.input_schema
        properties = schema.get("properties", {})
        required = schema.get("required", [])

        for name, prop in properties.items():
            prop_type = prop.get("type", "unknown")
            description = prop.get("description", "")

            args.append(
                ArgInfo(
                    name=name,
                    type=str(prop_type),
                    required=name in required,
                    short_description=description[:200] if description else "",
                    examples=prop.get("examples"),
                )
            )

        # Generate safety notes based on risk
        safety_notes: list[str] = []
        if tool_info.risk_hint.value == "high":
            safety_notes.append("This tool may modify data or have side effects.")

        # Build invoke template for direct invocation
        arg_placeholders: dict[str, str] = {}
        for arg in args:
            if arg.required:
                arg_placeholders[arg.name] = f"<required: {arg.type}>"
            else:
                arg_placeholders[arg.name] = f"<optional: {arg.type}>"

        invoke_template = InvokeTemplate(
            tool_id=tool_info.tool_id,
            arguments=arg_placeholders,
        )

        # Get code snippet if guidance enabled
        code_snippet = None
        if self._guidance_config and self._guidance_config.include_code_snippets:
            # Try static template first, fallback to LLM generation for dynamic tools
            code_snippet = get_code_snippet(
                tool_info.tool_id,
                max_lines=self._guidance_config.max_snippet_lines,
                tool_info=tool_info,
                use_llm_fallback=True,  # Enable LLM generation for tools without templates
            )

        self._record_feedback_event(
            "describe",
            {"tool_id": parsed.tool_id, "server": tool_info.server_name},
        )

        return SchemaCard(
            server=tool_info.server_name,
            tool_name=tool_info.tool_name,
            description=tool_info.description,
            args=args,
            safety_notes=safety_notes if safety_notes else None,
            invoke_template=invoke_template,
            code_snippet=code_snippet,
            update_warning=update_warning,
            feedback_hint=self._feedback_hint(),
        )

    async def invoke(self, input_data: dict[str, Any]) -> InvokeOutput:
        """gateway.invoke - Call a downstream tool."""
        parsed = InvokeInput.model_validate(input_data)

        # Check if tool exists in registry
        tool_info = self._client_manager.get_tool(parsed.tool_id)

        if not tool_info:
            # Tool not in registry - check if it might be from a lazy server
            # that hasn't connected yet (no tools indexed)
            if "::" in parsed.tool_id:
                server_name = parsed.tool_id.split("::")[0]
                if self._client_manager.is_lazy_server(server_name):
                    # Try to connect the lazy server first
                    connected = await self._ensure_server_for_tool(parsed.tool_id)
                    if connected:
                        # Re-check for tool after connection
                        tool_info = self._client_manager.get_tool(parsed.tool_id)

            if not tool_info:
                error = make_error(
                    ErrorCode.E301_TOOL_NOT_FOUND,
                    tool_id=parsed.tool_id,
                )
                return InvokeOutput(
                    tool_id=parsed.tool_id,
                    ok=False,
                    truncated=False,
                    raw_size_estimate=0,
                    errors=[error.model_dump_json()],
                    feedback_hint=self._feedback_hint(),
                )

        # Check policy
        if not self._policy_manager.is_tool_allowed(parsed.tool_id):
            error = make_error(
                ErrorCode.E402_TOOL_DENIED,
                tool_id=parsed.tool_id,
            )
            return InvokeOutput(
                tool_id=parsed.tool_id,
                ok=False,
                truncated=False,
                raw_size_estimate=0,
                errors=[error.model_dump_json()],
                feedback_hint=self._feedback_hint(),
            )

        update_warning = await self._get_update_warning(tool_info.server_name)

        # Call the tool
        timeout_ms = 30000
        if parsed.options:
            timeout_ms = parsed.options.timeout_ms
        try:
            result = await self._client_manager.call_tool(
                parsed.tool_id, parsed.arguments, timeout_ms
            )

            # Process output (truncate, redact)
            max_bytes = None
            if parsed.options and parsed.options.max_output_chars:
                max_bytes = parsed.options.max_output_chars * 4  # Rough bytes estimate

            redact = parsed.options.redact_secrets if parsed.options else False

            processed = self._policy_manager.process_output(
                result, redact=redact, max_bytes=max_bytes
            )

            return InvokeOutput(
                tool_id=parsed.tool_id,
                ok=True,
                result=processed["result"],
                truncated=processed["truncated"],
                summary=processed["summary"],
                raw_size_estimate=processed["raw_size"],
                update_warning=update_warning,
                feedback_hint=None,
            )

        except TimeoutError:
            self._record_feedback_event(
                "invoke_failure",
                {"tool_id": parsed.tool_id, "reason": "timeout"},
            )
            error = make_error(
                ErrorCode.E303_TOOL_TIMEOUT,
                tool_id=parsed.tool_id,
                timeout_ms=timeout_ms,
            )
            return InvokeOutput(
                tool_id=parsed.tool_id,
                ok=False,
                truncated=False,
                raw_size_estimate=0,
                errors=[error.model_dump_json()],
                update_warning=update_warning,
                feedback_hint=self._feedback_hint(),
            )

        except ConnectionError as e:
            self._record_feedback_event(
                "invoke_failure",
                {
                    "tool_id": parsed.tool_id,
                    "reason": "connection_error",
                    "error": str(e),
                },
            )
            error = make_error(
                ErrorCode.E201_SERVER_OFFLINE,
                message=str(e),
                tool_id=parsed.tool_id,
            )
            return InvokeOutput(
                tool_id=parsed.tool_id,
                ok=False,
                truncated=False,
                raw_size_estimate=0,
                errors=[error.model_dump_json()],
                update_warning=update_warning,
                feedback_hint=self._feedback_hint(),
            )

        except Exception as e:
            self._record_feedback_event(
                "invoke_failure",
                {"tool_id": parsed.tool_id, "reason": "exception", "error": str(e)},
            )
            error = make_error(
                ErrorCode.E302_TOOL_EXECUTION_FAILED,
                message=str(e),
                tool_id=parsed.tool_id,
            )
            return InvokeOutput(
                tool_id=parsed.tool_id,
                ok=False,
                truncated=False,
                raw_size_estimate=0,
                errors=[error.model_dump_json()],
                update_warning=update_warning,
                feedback_hint=self._feedback_hint(),
            )

    async def refresh(self, input_data: dict[str, Any]) -> RefreshOutput:
        """gateway.refresh - Reload backend configs and reconnect."""
        parsed = RefreshInput.model_validate(input_data)

        logger.info(f"Refresh requested: {parsed.reason or 'manual refresh'}")

        try:
            # Reload configs from .mcp.json files
            configs = load_configs(
                project_root=self._project_root,
                custom_config_path=self._custom_config_path,
            )

            # Filter out the gateway itself to prevent recursive connection
            # Uses command-based detection, not just name matching
            configs = filter_self_references(configs)
            seen_servers = {c.name for c in configs}

            # Load manifest and add auto-start servers (if not already configured)
            try:
                manifest = load_manifest()
                auto_start_servers = manifest.get_auto_start_servers()

                for server in auto_start_servers:
                    if server.name in seen_servers:
                        logger.debug(
                            f"Skipping manifest server '{server.name}' - already in .mcp.json"
                        )
                        continue

                    # Skip servers that require API keys if not set (checks all pmcp env stores)
                    if server.requires_api_key and server.env_var:
                        if not self._check_api_key_available(server.env_var):
                            logger.info(
                                f"Skipping auto-start server '{server.name}' - "
                                f"missing {server.env_var}"
                            )
                            continue

                    # Add manifest server to configs
                    configs.append(manifest_server_to_config(server))
                    seen_servers.add(server.name)
                    logger.info(f"Added auto-start server from manifest: {server.name}")

            except Exception as e:
                logger.warning(f"Failed to load manifest auto-start servers: {e}")

            # Re-add previously provisioned servers (issue #45 fix)
            try:
                provisioned = self._load_provisioned_registry()
                for prov_name, prov_env_var in provisioned.items():
                    if prov_name in seen_servers:
                        continue
                    # Skip if required API key is gone
                    if prov_env_var and not self._check_api_key_available(prov_env_var):
                        logger.info(
                            f"Skipping provisioned server '{prov_name}' - missing {prov_env_var}"
                        )
                        continue
                    try:
                        prov_manifest = load_manifest()
                        prov_config = prov_manifest.get_server(prov_name)
                        if prov_config:
                            configs.append(manifest_server_to_config(prov_config))
                            seen_servers.add(prov_name)
                            logger.info(
                                f"Re-added provisioned server from registry: {prov_name}"
                            )
                    except Exception as inner_e:
                        logger.warning(
                            f"Could not re-add provisioned server '{prov_name}': {inner_e}"
                        )
            except Exception as e:
                logger.warning(f"Failed to restore provisioned servers: {e}")

            # Filter by policy
            allowed_configs = [
                c for c in configs if self._policy_manager.is_server_allowed(c.name)
            ]

            # Reconnect
            errors = await self._client_manager.refresh(allowed_configs)

            revision_id, _ = self._client_manager.get_registry_meta()
            statuses = self._client_manager.get_all_server_statuses()

            return RefreshOutput(
                ok=len(errors) == 0,
                servers_seen=len(configs),
                servers_online=sum(1 for s in statuses if s.status.value == "online"),
                tools_indexed=len(self._client_manager.get_all_tools()),
                revision_id=revision_id,
                errors=errors if errors else None,
            )

        except Exception as e:
            return RefreshOutput(
                ok=False,
                servers_seen=0,
                servers_online=0,
                tools_indexed=0,
                revision_id="error",
                errors=[str(e)],
            )

    async def health(self) -> HealthOutput:
        """gateway.health - Get gateway health status."""
        revision_id, last_refresh_ts = self._client_manager.get_registry_meta()
        statuses = self._client_manager.get_all_server_statuses()
        known_names = {s.name for s in statuses}

        # Include provisioned servers that are not currently tracked (e.g. after restart)
        provisioned = self._load_provisioned_registry()
        extra = [
            ServerHealthInfo(name=prov_name, status="offline", tool_count=0)
            for prov_name in provisioned
            if prov_name not in known_names
        ]

        return HealthOutput(
            revision_id=revision_id,
            servers=[
                ServerHealthInfo(
                    name=s.name,
                    status=s.status.value,
                    tool_count=s.tool_count,
                    error=s.last_error
                    if s.status.value == "error" and s.last_error
                    else None,
                )
                for s in statuses
            ]
            + extra,
            last_refresh_ts=last_refresh_ts,
        )

    def _check_api_key_available(self, env_var: str | None) -> bool:
        """Check if an API key is available in environment or any pmcp env store."""
        if not env_var:
            return False

        # Check live environment first (fast path)
        if os.environ.get(env_var):
            return True

        # Check all env stores in priority order: project-local .env, then pmcp files
        for env_path in [
            Path.cwd() / ".env",
            Path.cwd() / ".env.pmcp",
            Path.home() / ".config" / "pmcp" / "pmcp.env",
        ]:
            if env_path.exists():
                load_dotenv(env_path)
                if os.environ.get(env_var):
                    return True

        return False

    def _check_any_api_key_available(self, env_vars: list[str]) -> bool:
        """Check if any auth env var is available."""
        return any(self._check_api_key_available(env_var) for env_var in env_vars)

    def _auth_env_options(self, server_name: str, env_var: str | None) -> list[str]:
        """Return acceptable auth env vars for a server."""
        options: list[str] = []
        if env_var:
            options.append(env_var)
        # Browser Use supports either OpenAI or Anthropic provider credentials.
        if server_name == "browser-use":
            for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]:
                if key not in options:
                    options.append(key)
        return options

    def _auth_methods_for_server(self, server_name: str) -> list[str]:
        """Return supported auth methods for UX hints."""
        if server_name == "browser-use":
            return ["api_key", "subscription_token"]
        return ["api_key"]

    def _write_secret(self, scope: str, key: str, value: str) -> Path:
        """Persist a secret in PMCP env storage."""
        if scope == "project":
            path = Path.cwd() / ".env.pmcp"
        else:
            path = Path.home() / ".config" / "pmcp" / "pmcp.env"

        path.parent.mkdir(parents=True, exist_ok=True)

        values: dict[str, str] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"')

        values[key] = value
        body = "\n".join(f"{k}={v}" for k, v in sorted(values.items())) + "\n"
        path.write_text(body)
        return path

    def _normalize_token(self, value: str) -> str:
        """Normalize user query tokens for matching/discovery."""
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    def _query_mcp_registry(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Query the MCP Registry and return raw server entries."""
        normalized = self._normalize_token(query)
        if not normalized:
            return []

        try:
            url = (
                "https://registry.modelcontextprotocol.io/v0.1/servers"
                f"?search={quote_plus(normalized)}&limit={limit}"
            )
            with urlopen(url, timeout=5) as resp:  # nosec B310
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        return payload.get("servers", []) if isinstance(payload, dict) else []

    def _load_configured_servers(self) -> dict[str, ResolvedServerConfig]:
        """Load user/project-configured servers that are allowed by policy."""
        configs = load_configs(
            project_root=self._project_root,
            custom_config_path=self._custom_config_path,
        )
        configs = filter_self_references(configs)

        configured: dict[str, ResolvedServerConfig] = {}
        for config in configs:
            if self._policy_manager.is_server_allowed(config.name):
                configured[config.name] = config
        return configured

    def _keywords_for_config_server(self, config: ResolvedServerConfig) -> list[str]:
        """Build lightweight keywords for a configured server entry."""
        keywords: list[str] = [config.name, "mcp", "server"]
        if isinstance(config.config, LocalMcpServerConfig):
            if config.config.command:
                keywords.append(config.config.command)
            keywords.extend(config.config.args)
        elif isinstance(config.config, RemoteMcpServerConfig):
            keywords.extend(["remote", "sse", "http", "api"])
            keywords.append(config.config.url)

        # Deduplicate while preserving order
        deduped: list[str] = []
        seen: set[str] = set()
        for keyword in keywords:
            keyword_str = str(keyword).strip().lower()
            if keyword_str and keyword_str not in seen:
                seen.add(keyword_str)
                deduped.append(keyword_str)
        return deduped

    def _build_manifest_with_config_servers(
        self,
        manifest: Manifest,
        configured_servers: dict[str, ResolvedServerConfig],
    ) -> Manifest:
        """Create a manifest view that includes config-only server entries."""
        merged_servers = dict(manifest.servers)

        for name, config in configured_servers.items():
            if name in merged_servers:
                continue

            command = ""
            args: list[str] = []
            description = f"User-configured MCP server '{name}'"

            if isinstance(config.config, LocalMcpServerConfig):
                command = config.config.command
                args = list(config.config.args)
                if command:
                    description += f" (local command: {command})"
            elif isinstance(config.config, RemoteMcpServerConfig):
                description += f" (remote URL: {config.config.url})"

            merged_servers[name] = ServerConfig(
                name=name,
                description=description,
                keywords=self._keywords_for_config_server(config),
                install={},
                command=command,
                args=args,
                requires_api_key=False,
            )

        return Manifest(
            version=manifest.version,
            cli_alternatives=dict(manifest.cli_alternatives),
            servers=merged_servers,
            discovery_queue_path=manifest.discovery_queue_path,
        )

    def _get_server_env_metadata(
        self,
        server_name: str,
        manifest: Manifest,
        configured_servers: dict[str, ResolvedServerConfig],
    ) -> tuple[bool, str | None, str | None]:
        """Get API-key metadata for a server candidate."""
        manifest_server = manifest.get_server(server_name)
        if manifest_server:
            return (
                manifest_server.requires_api_key,
                manifest_server.env_var,
                manifest_server.env_instructions,
            )

        # No explicit API key metadata for plain .mcp.json entries.
        return (False, None, None)

    def _get_server_config_for_update(self, server_name: str) -> ServerConfig | None:
        """Resolve server config from manifest or discovered candidates."""
        manifest = load_manifest()
        server_config = manifest.get_server(server_name)
        if server_config:
            return server_config
        return self._discovered_server_configs.get(server_name)

    async def _get_update_warning(self, server_name: str) -> str | None:
        """Best-effort stale version warning for a server."""
        server_config = self._get_server_config_for_update(server_name)
        if not server_config or not server_config.command:
            return None

        # Require a known local version to compare against.
        current_version: str | None = None
        if self._descriptions_cache and server_name in self._descriptions_cache.servers:
            current_version = self._descriptions_cache.servers[server_name].version
        if not current_version:
            return None

        now = time.time()
        cached = self._stale_check_cache.get(server_name)
        if cached and (now - cached[0]) < self._stale_check_ttl_seconds:
            latest_cached = cached[2]
            if latest_cached and is_version_newer(current_version, latest_cached):
                return (
                    f"Update available for '{server_name}': {current_version} -> {latest_cached}. "
                    f"Call gateway.update_server with server_name='{server_name}'."
                )
            return None

        latest, _pkg_type = await get_package_version(
            server_config.command, server_config.args, timeout=3.0
        )
        self._stale_check_cache[server_name] = (now, current_version, latest)

        if latest and is_version_newer(current_version, latest):
            return (
                f"Update available for '{server_name}': {current_version} -> {latest}. "
                f"Call gateway.update_server with server_name='{server_name}'."
            )
        return None

    async def _run_update_probe_command(self, command: list[str]) -> tuple[bool, str]:
        """Run an update probe command and return (ok, output)."""
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60.0)
        output = (
            stdout.decode("utf-8", errors="replace")
            + "\n"
            + stderr.decode("utf-8", errors="replace")
        ).strip()
        return (process.returncode == 0, output)

    def _telemetry_enabled(self) -> bool:
        """Whether feedback telemetry workflow is enabled."""
        if self._guidance_config is None:
            return True
        return self._guidance_config.enable_telemetry

    def _feedback_hint(self) -> str | None:
        """Guidance for agentic frameworks on feedback submission flow."""
        if not self._telemetry_enabled():
            return None
        return (
            "Technical failure detected. If your framework supports ask-user/question tools, "
            "ask consent before submission. Then call gateway.submit_feedback with the exact "
            "title/description payload you will send and show it verbatim to the user first. "
            "Warning: submission may use model tokens and sends technical data to GitHub. "
            "Do not include personal data, credentials, or secrets."
        )

    def _record_feedback_event(self, event_type: str, details: dict[str, Any]) -> None:
        """Record compact event history for feedback telemetry context."""
        event = {
            "ts": int(time.time()),
            "event_type": event_type,
            "details": details,
        }
        self._feedback_events.append(event)
        if len(self._feedback_events) > 12:
            self._feedback_events = self._feedback_events[-12:]

    def _truncate_token_text(
        self, text: str, token_limit: int = FEEDBACK_TOKEN_LIMIT
    ) -> str:
        """Approximate token truncation using whitespace-delimited tokens."""
        tokens = text.split()
        if len(tokens) <= token_limit:
            return text
        return " ".join(tokens[:token_limit])

    def _scrub_sensitive_text(self, text: str) -> str:
        """Remove obvious secrets and personal identifiers from feedback text."""
        scrubbed = text
        patterns = [
            (r"sk-[A-Za-z0-9_-]{20,}", "[REDACTED_API_KEY]"),
            (r"github_pat_[A-Za-z0-9_]{20,}", "[REDACTED_GITHUB_TOKEN]"),
            (r"ghp_[A-Za-z0-9]{20,}", "[REDACTED_GITHUB_TOKEN]"),
            (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}", "[REDACTED_EMAIL]"),
        ]
        for pattern, replacement in patterns:
            scrubbed = re.sub(pattern, replacement, scrubbed)
        return scrubbed

    def _build_feedback_issue(
        self,
        parsed: SubmitFeedbackInput,
        issue_repo: str,
    ) -> tuple[str, str]:
        """Build issue title/body with telemetry template."""
        safe_description = self._truncate_token_text(
            self._scrub_sensitive_text(parsed.description)
        )
        events_json = json.dumps(self._feedback_events[-6:], indent=2)
        subordinate = parsed.subordinate_server or "unknown"
        failed_call = parsed.failed_tool_call or "unknown"

        title_prefix = (
            "[Feedback][Bug]" if parsed.issue_type == "bug" else "[Feedback][Feature]"
        )
        issue_title = f"{title_prefix} {parsed.title.strip()}"
        issue_title = issue_title[:160]

        body = (
            "## Privacy Notice\n"
            "- Technical data only. No personal data should be included.\n"
            "- Data is submitted to GitHub and may be publicly visible depending on repository settings.\n"
            "- Payload is limited to approximately 4000 tokens.\n\n"
            "## User Report\n"
            f"{safe_description}\n\n"
            "## Telemetry\n"
            f"- pmcp_version: {PMCP_VERSION}\n"
            f"- platform: {platform.platform()}\n"
            f"- python_version: {platform.python_version()}\n"
            f"- subordinate_server: {subordinate}\n"
            f"- failed_tool_call: {failed_call}\n"
            f"- target_repository: {issue_repo}\n\n"
            "## Recent PMCP Events\n"
            "```json\n"
            f"{events_json}\n"
            "```\n"
        )
        return issue_title, body

    async def request_capability(
        self, input_data: dict[str, Any]
    ) -> CapabilityResolution:
        """gateway.request_capability - Request a capability by natural language.

        Returns ranked candidates for Claude Code to choose from.
        Use gateway.provision to actually install/start the chosen server.
        """
        parsed = CapabilityRequestInput.model_validate(input_data)
        self._record_feedback_event("capability_request", {"query": parsed.query})

        # Load manifest
        manifest = load_manifest()
        configured_servers = self._load_configured_servers()
        merged_manifest = self._build_manifest_with_config_servers(
            manifest, configured_servers
        )

        # Get detected CLIs (from input or probe)
        if parsed.available_clis:
            detected_clis = list(parsed.available_clis)
        elif self._detected_clis is not None:
            detected_clis = list(self._detected_clis)
        else:
            # Probe environment if not yet done
            platform = detect_platform()
            cli_configs = {
                name: {
                    "check_command": cli.check_command,
                    "help_command": cli.help_command,
                }
                for name, cli in manifest.cli_alternatives.items()
            }
            detected_cli_infos = await probe_clis(cli_configs)
            detected_clis = list(detected_cli_infos.keys())
            self._detected_clis = set(detected_clis)
            self._platform = platform

        # Get running servers
        running_servers = [
            s.name
            for s in self._client_manager.get_all_server_statuses()
            if s.status.value == "online"
        ]

        # --- Tier 1: explicit server name match ---
        # Match by normalizing server names and query word groups (strips hyphens,
        # underscores, spaces) so "bright data" → "brightdata", "brave-search" →
        # "bravesearch", etc. Does NOT use keywords to avoid matching generic
        # capability words like "browser" or "search".
        query_lower = parsed.query.lower()
        query_words = query_lower.split()
        norm_to_server = {
            n.lower().replace("-", "").replace("_", "").replace(" ", ""): n
            for n in merged_manifest.servers
        }
        name_match: str | None = None
        for window_size in (3, 2, 1):
            for i in range(len(query_words) - window_size + 1):
                window = (
                    "".join(query_words[i : i + window_size])
                    .replace("-", "")
                    .replace("_", "")
                )
                if window in norm_to_server:
                    name_match = norm_to_server[window]
                    break
            if name_match:
                break

        if name_match:
            requires_api_key, env_var, env_instructions = self._get_server_env_metadata(
                name_match, manifest, configured_servers
            )
            api_key_available = self._check_api_key_available(env_var)
            candidate = CapabilityCandidate(
                name=name_match,
                candidate_type="server",
                relevance_score=1.0,
                reasoning=f"Explicit name match for '{name_match}' in query.",
                requires_api_key=requires_api_key,
                api_key_available=api_key_available,
                env_var=env_var,
                env_instructions=env_instructions,
                is_running=name_match in running_servers,
            )
            msg = f"Matched '{name_match}' by name."
            if requires_api_key:
                if api_key_available:
                    msg += f" API key ({env_var}) is already set — ready to provision."
                else:
                    msg += (
                        f" Requires API key ({env_var}). "
                        f"Set it or call gateway.auth_connect, then gateway.provision."
                    )
            else:
                msg += " No API key required. Call gateway.provision to install."
            return CapabilityResolution(
                status="candidates",
                message=msg,
                candidates=[candidate],
                recommendation=f"Call gateway.provision(server_name='{name_match}')",
            )

        # --- Tier 2: category keyword match ---
        category_result = manifest.get_servers_in_category(parsed.query)
        if category_result:
            cat_name, cat_servers = category_result

            # Build enriched candidates for every server in the category
            all_candidates: list[CapabilityCandidate] = []
            for scfg in cat_servers:
                requires_api_key, env_var, env_instructions = (
                    self._get_server_env_metadata(
                        scfg.name, manifest, configured_servers
                    )
                )
                api_key_available = self._check_api_key_available(env_var)
                all_candidates.append(
                    CapabilityCandidate(
                        name=scfg.name,
                        candidate_type="server",
                        relevance_score=1.0,
                        reasoning=scfg.description,
                        requires_api_key=requires_api_key,
                        api_key_available=api_key_available,
                        env_var=env_var,
                        env_instructions=env_instructions,
                        is_running=scfg.name in running_servers,
                    )
                )

            # Sort: no-key-required first, then key-available, then key-missing
            def _sort_key(c: CapabilityCandidate) -> int:
                if not c.requires_api_key:
                    return 0
                if c.api_key_available:
                    return 1
                return 2

            all_candidates.sort(key=_sort_key)

            # Build a human-readable message grouping the three tiers
            free = [c for c in all_candidates if not c.requires_api_key]
            key_ready = [
                c for c in all_candidates if c.requires_api_key and c.api_key_available
            ]
            key_missing = [
                c
                for c in all_candidates
                if c.requires_api_key and not c.api_key_available
            ]

            parts: list[str] = [
                f"{len(all_candidates)} options in '{cat_name}' category. "
                "Review and call gateway.provision(server_name='...') with your choice."
            ]
            if free:
                names = ", ".join(c.name for c in free)
                parts.append(f"No API key required: {names}.")
            if key_ready:
                names = ", ".join(f"{c.name} ({c.env_var} ✓)" for c in key_ready)
                parts.append(f"API key already set — ready to provision: {names}.")
            if key_missing:
                names = ", ".join(f"{c.name} (needs {c.env_var})" for c in key_missing)
                parts.append(
                    f"Requires API key (not set): {names}. "
                    "Provide the key or call gateway.auth_connect first."
                )

            return CapabilityResolution(
                status="pick_from_category",
                message=" ".join(parts),
                candidates=all_candidates,
                category_name=cat_name,
                recommendation=(
                    "Choose based on your needs and API key availability. "
                    "Call gateway.provision(server_name='<chosen>') to install."
                ),
            )

        # --- Tier 3: no match ---
        logger.info(f"Unmatched capability request: {parsed.query}")
        self._record_feedback_event(
            "capability_unmatched", {"query": parsed.query, "path": "no_match"}
        )
        return CapabilityResolution(
            status="not_available",
            message=f"No matching capability found for: {parsed.query}",
            logged_for_discovery=True,
            search_guidance=(
                f'Call gateway.search_registry(query="{parsed.query}") '
                "to search the public MCP Registry for external servers. "
                "Then call gateway.register_discovered_server and gateway.provision to install."
            ),
        )

    async def provision(self, input_data: dict[str, Any]) -> ProvisionOutput:
        """gateway.provision - Start background installation of an MCP server."""
        parsed = ProvisionInput.model_validate(input_data)
        server_name = parsed.server_name
        update_warning = await self._get_update_warning(server_name)
        self._record_feedback_event("provision_attempt", {"server": server_name})

        configured_servers = self._load_configured_servers()

        # Check if already running
        if self._client_manager.is_server_online(server_name):
            tools = [
                t
                for t in self._client_manager.get_all_tools()
                if t.server_name == server_name
            ]
            return ProvisionOutput(
                ok=True,
                server=server_name,
                status="already_running",
                message=f"Server '{server_name}' is already running with {len(tools)} tools.",
                new_tools=[
                    CapabilityCard(
                        tool_id=t.tool_id,
                        server=t.server_name,
                        tool_name=t.tool_name,
                        short_description=t.short_description,
                        tags=t.tags,
                        availability="online",
                        risk_hint=t.risk_hint.value,
                    )
                    for t in tools[:10]
                ],
                update_warning=update_warning,
                feedback_hint=None,
            )

        # User/project configured servers are lazy-started via ClientManager.
        if server_name in configured_servers:
            try:
                connected = await self._client_manager.ensure_connected(server_name)
            except ValueError:
                connected = False

            if connected:
                tools = [
                    t
                    for t in self._client_manager.get_all_tools()
                    if t.server_name == server_name
                ]
                return ProvisionOutput(
                    ok=True,
                    server=server_name,
                    status="complete",
                    message=f"Server '{server_name}' started from .mcp.json configuration with {len(tools)} tools.",
                    new_tools=[
                        CapabilityCard(
                            tool_id=t.tool_id,
                            server=t.server_name,
                            tool_name=t.tool_name,
                            short_description=t.short_description,
                            tags=t.tags,
                            availability="online",
                            risk_hint=t.risk_hint.value,
                        )
                        for t in tools[:10]
                    ],
                    update_warning=update_warning,
                    feedback_hint=None,
                )

            return ProvisionOutput(
                ok=False,
                server=server_name,
                status="failed",
                message=(
                    f"Server '{server_name}' is configured but could not be started. "
                    "Run gateway.refresh and check gateway.health for connection errors."
                ),
                update_warning=update_warning,
                feedback_hint=self._feedback_hint(),
            )

        # Load manifest
        manifest = load_manifest()
        server_config = manifest.get_server(server_name)

        if not server_config:
            server_config = self._discovered_server_configs.get(server_name)

        if not server_config:
            return ProvisionOutput(
                ok=False,
                server=server_name,
                status="failed",
                message=(
                    f"Server '{server_name}' not found in manifest or .mcp.json configuration."
                ),
                update_warning=update_warning,
                feedback_hint=self._feedback_hint(),
            )

        # Check API key if required
        if server_config.requires_api_key and server_config.env_var:
            auth_env_options = self._auth_env_options(
                server_name, server_config.env_var
            )
            if not self._check_any_api_key_available(auth_env_options):
                return ProvisionOutput(
                    ok=False,
                    server=server_name,
                    status="failed",
                    message=(
                        f"Server '{server_name}' requires authentication. "
                        f"Set one of: {', '.join(auth_env_options)}"
                    ),
                    needs_api_key=True,
                    env_var=server_config.env_var,
                    env_instructions=server_config.env_instructions,
                    auth_required=True,
                    auth_mode="api_key",
                    auth_methods=self._auth_methods_for_server(server_name),
                    alternative_env_vars=auth_env_options,
                    update_warning=update_warning,
                    feedback_hint=self._feedback_hint(),
                )

        # Start background installation
        platform = getattr(self, "_platform", None) or detect_platform()
        job_manager = get_job_manager()

        try:
            job_id = await job_manager.start_install(server_config, platform)

            return ProvisionOutput(
                ok=True,
                server=server_name,
                status="started",
                job_id=job_id,
                message=f"Installation started for '{server_name}'. Poll gateway.provision_status('{job_id}') for progress.",
                update_warning=update_warning,
                feedback_hint=None,
            )

        except MissingApiKeyError as e:
            self._record_feedback_event(
                "provision_failure",
                {
                    "server": server_name,
                    "reason": "missing_api_key",
                    "env_var": e.env_var,
                },
            )
            return ProvisionOutput(
                ok=False,
                server=server_name,
                status="failed",
                message=f"Server '{server_name}' requires API key.",
                needs_api_key=True,
                env_var=e.env_var,
                env_instructions=e.env_instructions,
                auth_required=True,
                auth_mode="api_key",
                auth_methods=self._auth_methods_for_server(server_name),
                update_warning=update_warning,
                feedback_hint=self._feedback_hint(),
            )

        except InstallError as e:
            self._record_feedback_event(
                "provision_failure",
                {"server": server_name, "reason": "install_error", "error": str(e)},
            )
            return ProvisionOutput(
                ok=False,
                server=server_name,
                status="failed",
                message=str(e),
                update_warning=update_warning,
                feedback_hint=self._feedback_hint(),
            )

        except Exception as e:
            logger.error(f"Failed to start provisioning {server_name}: {e}")
            self._record_feedback_event(
                "provision_failure",
                {"server": server_name, "reason": "exception", "error": str(e)},
            )
            return ProvisionOutput(
                ok=False,
                server=server_name,
                status="failed",
                message=f"Failed to start provisioning '{server_name}': {e}",
                update_warning=update_warning,
                feedback_hint=self._feedback_hint(),
            )

    async def auth_connect(self, input_data: dict[str, Any]) -> AuthConnectOutput:
        """gateway.auth_connect - Store auth credentials for a server."""
        parsed = AuthConnectInput.model_validate(input_data)
        server_name = parsed.server_name

        manifest = load_manifest()
        server_config = manifest.get_server(server_name)
        env_var = parsed.env_var or (server_config.env_var if server_config else None)

        if not env_var:
            return AuthConnectOutput(
                ok=False,
                server=server_name,
                message=(
                    f"No auth env var is known for server '{server_name}'. "
                    "Pass env_var explicitly or add auth metadata to manifest."
                ),
            )

        path = self._write_secret(parsed.scope, env_var, parsed.credential)
        os.environ[env_var] = parsed.credential

        return AuthConnectOutput(
            ok=True,
            server=server_name,
            message=(
                f"Stored credential for '{server_name}' in {parsed.scope} scope. "
                "You can now call gateway.provision."
            ),
            env_var=env_var,
            env_path=str(path),
            next_step=f"gateway.provision(server_name='{server_name}')",
        )

    async def submit_feedback(self, input_data: dict[str, Any]) -> SubmitFeedbackOutput:
        """gateway.submit_feedback - Prepare/submit PMCP feedback issue."""
        parsed = SubmitFeedbackInput.model_validate(input_data)
        repository = os.environ.get("PMCP_FEEDBACK_REPO", "ViperJuice/pmcp")

        warning = (
            "Submission sends technical telemetry to GitHub and may consume model tokens. "
            "Send technical data only; never include personal data or secrets."
        )

        if not self._telemetry_enabled():
            return SubmitFeedbackOutput(
                ok=False,
                submitted=False,
                repository=repository,
                repository_visibility="unknown",
                issue_title=parsed.title,
                issue_body=parsed.description,
                warning=warning,
                message="Feedback telemetry is disabled in guidance config (enable_telemetry=false).",
            )

        self._record_feedback_event(
            "feedback_prepare",
            {
                "issue_type": parsed.issue_type,
                "subordinate_server": parsed.subordinate_server,
                "failed_tool_call": parsed.failed_tool_call,
            },
        )

        issue_title, issue_body = self._build_feedback_issue(parsed, repository)

        if not parsed.confirm_submission:
            return SubmitFeedbackOutput(
                ok=True,
                submitted=False,
                repository=repository,
                repository_visibility="public",
                issue_title=issue_title,
                issue_body=issue_body,
                warning=warning,
                message=(
                    "Preview generated. Ask the user for consent using your question tool, "
                    "show this exact issue payload, then call again with confirm_submission=true."
                ),
            )

        token = os.environ.get("PMCP_FEEDBACK_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            try:
                from urllib.request import Request

                repo_visibility = "unknown"
                try:
                    repo_req = Request(
                        f"https://api.github.com/repos/{repository}",
                        headers={
                            "Accept": "application/vnd.github+json",
                            "Authorization": f"Bearer {token}",
                            "X-GitHub-Api-Version": "2022-11-28",
                        },
                        method="GET",
                    )
                    with urlopen(repo_req, timeout=5) as repo_resp:  # nosec B310
                        repo_info = json.loads(repo_resp.read().decode("utf-8"))
                    private_flag = bool(repo_info.get("private"))
                    repo_visibility = "private" if private_flag else "public"
                except Exception:
                    repo_visibility = "unknown"

                issue_api = f"https://api.github.com/repos/{repository}/issues"
                payload = json.dumps(
                    {
                        "title": issue_title,
                        "body": issue_body,
                        "labels": [
                            "pmcp-feedback",
                            "authenticated-feedback",
                            parsed.issue_type,
                        ],
                    }
                ).encode("utf-8")
                req = Request(
                    issue_api,
                    data=payload,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {token}",
                        "X-GitHub-Api-Version": "2022-11-28",
                        "Content-Type": "application/json",
                    },
                    method="POST",
                )
                with urlopen(req, timeout=10) as resp:  # nosec B310
                    created = json.loads(resp.read().decode("utf-8"))

                issue_url = created.get("html_url")
                issue_number = created.get("number")
                self._record_feedback_event(
                    "feedback_submitted",
                    {
                        "repository": repository,
                        "issue_url": issue_url,
                        "authenticated": True,
                    },
                )
                return SubmitFeedbackOutput(
                    ok=True,
                    submitted=True,
                    repository=repository,
                    repository_visibility=cast(
                        Literal["public", "private", "unknown"], repo_visibility
                    ),
                    issue_title=issue_title,
                    issue_body=issue_body,
                    issue_url=issue_url,
                    issue_number=issue_number,
                    authenticated=True,
                    warning=warning,
                    message=(
                        "Feedback issue submitted successfully. "
                        "Share issue_url with the user so they can review/delete it if desired."
                    ),
                )
            except Exception as e:
                logger.warning(f"Authenticated feedback submission failed: {e}")

        if shutil.which("gh"):
            cmd = [
                "gh",
                "issue",
                "create",
                "--repo",
                repository,
                "--title",
                issue_title,
                "--body",
                issue_body,
                "--label",
                "pmcp-feedback",
                "--label",
                parsed.issue_type,
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                issue_url = stdout.decode("utf-8", errors="replace").strip()
                self._record_feedback_event(
                    "feedback_submitted",
                    {
                        "repository": repository,
                        "issue_url": issue_url,
                        "authenticated": False,
                    },
                )
                return SubmitFeedbackOutput(
                    ok=True,
                    submitted=True,
                    repository=repository,
                    repository_visibility="public",
                    issue_title=issue_title,
                    issue_body=issue_body,
                    issue_url=issue_url,
                    authenticated=False,
                    warning=warning,
                    message=(
                        "Feedback issue submitted via gh CLI. "
                        "Share issue_url with the user so they can review/delete it if desired."
                    ),
                )
            logger.warning(
                "gh issue create failed: "
                + stderr.decode("utf-8", errors="replace").strip()
            )

        browser_url = f"https://github.com/{repository}/issues/new?" + urlencode(
            {"title": issue_title, "body": issue_body}
        )
        return SubmitFeedbackOutput(
            ok=True,
            submitted=False,
            repository=repository,
            repository_visibility="public",
            issue_title=issue_title,
            issue_body=issue_body,
            issue_url=browser_url,
            warning=warning,
            message=(
                "Could not auto-submit. Open issue_url to submit manually. "
                "Share with user so they can edit/remove content before posting."
            ),
        )

    async def update_server(self, input_data: dict[str, Any]) -> UpdateServerOutput:
        """gateway.update_server - Update a subordinate MCP package and reconnect."""
        parsed = UpdateServerInput.model_validate(input_data)
        server_name = parsed.server_name

        server_config = self._get_server_config_for_update(server_name)
        if not server_config:
            return UpdateServerOutput(
                ok=False,
                server=server_name,
                package_type="unknown",
                message=f"Server '{server_name}' not found in manifest or discovered servers.",
            )

        package_type, package_name = detect_package_type(
            server_config.command, server_config.args
        )
        if package_type == "unknown" or not package_name:
            return UpdateServerOutput(
                ok=False,
                server=server_name,
                package_type="unknown",
                message=(
                    f"Could not determine package manager for '{server_name}'. "
                    "Only npm (npx) and pypi (uvx) servers are supported."
                ),
            )

        if package_type == "npm":
            update_cmd = ["npx", "-y", f"{package_name}@latest", "--help"]
        else:
            update_cmd = ["uvx", "--refresh", package_name, "--help"]

        try:
            ok, output = await self._run_update_probe_command(update_cmd)
        except TimeoutError:
            return UpdateServerOutput(
                ok=False,
                server=server_name,
                package_type=package_type,
                package_name=package_name,
                message="Update probe timed out after 60 seconds.",
            )
        except Exception as e:
            return UpdateServerOutput(
                ok=False,
                server=server_name,
                package_type=package_type,
                package_name=package_name,
                message=f"Failed to run update probe: {e}",
            )

        if not ok:
            short_output = output[-300:] if output else "no output"
            return UpdateServerOutput(
                ok=False,
                server=server_name,
                package_type=package_type,
                package_name=package_name,
                message=f"Update command failed: {short_output}",
            )

        refresh_result = await self.refresh({"reason": f"update_server:{server_name}"})
        latest_version, _ = await get_package_version(
            server_config.command, server_config.args, timeout=5.0
        )
        self._stale_check_cache.pop(server_name, None)

        message = f"Updated '{server_name}' ({package_type}:{package_name}) and refreshed gateway connections."
        if not refresh_result.ok:
            message += " Some servers failed to reconnect; inspect gateway.health."

        return UpdateServerOutput(
            ok=True,
            server=server_name,
            package_type=package_type,
            package_name=package_name,
            refreshed=refresh_result.ok,
            latest_version=latest_version,
            message=message,
        )

    async def search_registry(self, input_data: dict[str, Any]) -> SearchRegistryOutput:
        """gateway.search_registry - Search public MCP Registry for external servers."""
        parsed = SearchRegistryInput.model_validate(input_data)
        self._record_feedback_event("registry_search", {"query": parsed.query})

        raw_entries = self._query_mcp_registry(parsed.query, limit=parsed.limit * 2)

        results: list[SearchRegistryResult] = []
        seen_packages: set[str] = set()
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            server_info = entry.get("server", {})
            packages = server_info.get("packages", [])
            if not packages:
                continue
            pkg = packages[0]
            package_name = str(pkg.get("identifier", "")).strip()
            if not package_name or package_name in seen_packages:
                continue
            seen_packages.add(package_name)

            description = str(server_info.get("description", "")).strip()
            transport = (
                pkg.get("transport", {}).get("type")
                if isinstance(pkg.get("transport"), dict)
                else None
            )

            env_var_defs = pkg.get("environmentVariables", []) or []
            env_vars = [
                str(ev.get("name", "")).strip()
                for ev in env_var_defs
                if isinstance(ev, dict) and ev.get("name")
            ]

            results.append(
                SearchRegistryResult(
                    name=server_info.get("name", package_name),
                    package=package_name,
                    description=description,
                    transport=transport,
                    env_vars=env_vars,
                )
            )
            if len(results) >= parsed.limit:
                break

        return SearchRegistryOutput(
            query=parsed.query,
            results=results,
            next_step=(
                "Call gateway.register_discovered_server(package=<package>, server_name=<name>) "
                "then gateway.provision(server_name=<name>) to install."
            ),
        )

    async def register_discovered_server(
        self, input_data: dict[str, Any]
    ) -> RegisterDiscoveredServerOutput:
        """gateway.register_discovered_server - Register an external server for provisioning."""
        parsed = RegisterDiscoveredServerInput.model_validate(input_data)
        server_name = parsed.server_name
        package = parsed.package

        requires_api_key = len(parsed.env_vars) > 0
        # Primary env var used for availability checks and auth_connect prompt
        env_var = parsed.env_vars[0] if parsed.env_vars else None
        # Build instructions for any additional required env vars
        extra_vars = parsed.env_vars[1:] if len(parsed.env_vars) > 1 else []
        env_instructions: str | None = None
        if extra_vars:
            vars_list = ", ".join(f"${v}" for v in extra_vars)
            env_instructions = (
                f"Also set {vars_list} — call gateway.auth_connect for each, "
                "or export them before calling gateway.provision."
            )

        self._discovered_server_configs[server_name] = ServerConfig(
            name=server_name,
            description=parsed.description or f"Discovered MCP package: {package}",
            keywords=["mcp", "discovered", server_name, package],
            install={
                "mac": ["npx", "-y", package],
                "linux": ["npx", "-y", package],
                "wsl": ["npx", "-y", package],
                "windows": ["npx", "-y", package],
            },
            command="npx",
            args=["-y", package],
            requires_api_key=requires_api_key,
            env_var=env_var,
            env_instructions=env_instructions,
        )

        self._record_feedback_event(
            "server_registered",
            {"server_name": server_name, "package": package},
        )

        return RegisterDiscoveredServerOutput(
            ok=True,
            server_name=server_name,
            registered=True,
            message=(
                f"Registered '{server_name}' (package: {package}). "
                "Call gateway.provision to install and start it."
            ),
            next_step=f"gateway.provision(server_name='{server_name}')",
        )

    async def provision_status(self, input_data: dict[str, Any]) -> ProvisionJobStatus:
        """gateway.provision_status - Check status of a running installation."""
        import time

        try:
            parsed = ProvisionStatusInput.model_validate(input_data)
            job_id = parsed.job_id

            job_manager = get_job_manager()
            job = job_manager.get_job(job_id)

            if not job:
                return ProvisionJobStatus(
                    job_id=job_id,
                    server="unknown",
                    status="not_found",
                    progress=0,
                    message=f"Job '{job_id}' not found. It may have expired.",
                )

            # Copy job state to avoid race conditions with monitor task
            job_status = job.status
            job_progress = job.progress
            job_error = job.error
            job_server_name = job.server_name
            job_output_lines = list(job.output_lines)  # Copy the list
            elapsed = time.time() - job.started_at

            logger.debug(
                f"provision_status: job={job_id} status={job_status} progress={job_progress}"
            )

            # If server_ready, perform handoff to ClientManager
            if job_status == "server_ready":
                process = job.process
                if not process or process.returncode is not None:
                    # Process died before handoff
                    job.status = "failed"
                    job.error = "Server process exited before handoff"
                    return ProvisionJobStatus(
                        job_id=job_id,
                        server=job_server_name,
                        status="failed",
                        progress=job_progress,
                        message=f"Server process for '{job_server_name}' exited unexpectedly",
                        output_tail=job_output_lines[-5:],
                        elapsed_seconds=elapsed,
                        error="Process exited before handoff",
                    )

                try:
                    # Build config from manifest
                    manifest = load_manifest()
                    server_config = manifest.get_server(job_server_name)
                    if not server_config:
                        raise ValueError(
                            f"Server '{job_server_name}' not found in manifest"
                        )

                    resolved_config = manifest_server_to_config(server_config)

                    # Adopt the process into ClientManager
                    await self._client_manager.adopt_process(
                        job_server_name, process, resolved_config
                    )

                    # Mark job complete and clear process reference
                    job.status = "complete"
                    job.process = None

                    # Persist to provisioned registry so refresh() can reconnect it
                    env_var = server_config.env_var if server_config else None
                    self._register_provisioned_server(job_server_name, env_var)

                    # Get the new tools
                    tools = [
                        t
                        for t in self._client_manager.get_all_tools()
                        if t.server_name == job_server_name
                    ]

                    return ProvisionJobStatus(
                        job_id=job_id,
                        server=job_server_name,
                        status="complete",
                        progress=100,
                        message=f"Server '{job_server_name}' installed and connected with {len(tools)} tools.",
                        output_tail=job_output_lines[-5:],
                        elapsed_seconds=elapsed,
                        new_tools=[
                            CapabilityCard(
                                tool_id=t.tool_id,
                                server=t.server_name,
                                tool_name=t.tool_name,
                                short_description=t.short_description,
                                tags=t.tags,
                                availability="online",
                                risk_hint=t.risk_hint.value,
                            )
                            for t in tools[:10]
                        ]
                        if tools
                        else None,
                    )

                except Exception as e:
                    logger.error(
                        f"Handoff failed for {job_server_name}: {e}", exc_info=True
                    )
                    job.status = "failed"
                    job.error = f"Handoff failed: {e}"
                    # Kill the orphaned process
                    if process and process.returncode is None:
                        try:
                            process.kill()
                        except Exception:
                            pass
                    job.process = None

                    return ProvisionJobStatus(
                        job_id=job_id,
                        server=job_server_name,
                        status="failed",
                        progress=job_progress,
                        message=f"Failed to connect to '{job_server_name}': {e}",
                        output_tail=job_output_lines[-5:],
                        elapsed_seconds=elapsed,
                        error=str(e),
                    )

            # If complete (from non-npx install), trigger refresh to connect the server
            if job_status == "complete":
                tools = []
                refresh_error = None

                try:
                    await self.refresh({"reason": f"Provisioned {job_server_name}"})
                    # Get the new tools
                    tools = [
                        t
                        for t in self._client_manager.get_all_tools()
                        if t.server_name == job_server_name
                    ]
                except Exception as e:
                    logger.error(f"Failed to refresh after install: {e}")
                    refresh_error = str(e)

                message = f"Server '{job_server_name}' installed"
                if tools:
                    message += f" and connected with {len(tools)} tools."
                elif refresh_error:
                    message += f" but refresh failed: {refresh_error}"
                else:
                    message += " but no tools found. Try gateway.refresh manually."

                return ProvisionJobStatus(
                    job_id=job_id,
                    server=job_server_name,
                    status="complete",
                    progress=100,
                    message=message,
                    output_tail=job_output_lines[-5:],
                    elapsed_seconds=elapsed,
                    new_tools=[
                        CapabilityCard(
                            tool_id=t.tool_id,
                            server=t.server_name,
                            tool_name=t.tool_name,
                            short_description=t.short_description,
                            tags=t.tags,
                            availability="online",
                            risk_hint=t.risk_hint.value,
                        )
                        for t in tools[:10]
                    ]
                    if tools
                    else None,
                    error=refresh_error,
                )

            # For other statuses, return current state
            status_messages = {
                "pending": f"Preparing to install '{job_server_name}'...",
                "installing": f"Installing '{job_server_name}'... ({job_progress}%)",
                "server_ready": f"Server '{job_server_name}' starting, connecting...",
                "failed": f"Installation failed: {job_error}",
                "timeout": f"Installation timed out: {job_error}",
            }

            return ProvisionJobStatus(
                job_id=job_id,
                server=job_server_name,
                status=job_status,
                progress=job_progress,
                message=status_messages.get(job_status, f"Status: {job_status}"),
                output_tail=job_output_lines[-5:],
                elapsed_seconds=elapsed,
                error=job_error,
            )

        except Exception as e:
            logger.error(f"provision_status handler failed: {e}", exc_info=True)
            # Return a safe error response instead of crashing
            return ProvisionJobStatus(
                job_id=input_data.get("job_id", "unknown"),
                server="unknown",
                status="failed",
                progress=0,
                message=f"Error checking status: {e}",
                error=str(e),
            )

    async def sync_environment(
        self, input_data: dict[str, Any]
    ) -> SyncEnvironmentOutput:
        """gateway.sync_environment - Sync environment info from host."""
        parsed = SyncEnvironmentInput.model_validate(input_data)

        # Use provided or detect platform
        if parsed.platform:
            platform = parsed.platform
        else:
            platform = detect_platform()

        # Use provided or probe CLIs
        if parsed.detected_clis:
            detected_clis = set(parsed.detected_clis)
        else:
            manifest = load_manifest()
            # Build CLI configs dict for probing
            cli_configs = {
                name: {
                    "check_command": cli.check_command,
                    "help_command": cli.help_command,
                }
                for name, cli in manifest.cli_alternatives.items()
            }
            detected_cli_infos = await probe_clis(cli_configs)
            detected_clis = set(detected_cli_infos.keys())

        # Store for future use
        self._platform = platform
        self._detected_clis = detected_clis

        return SyncEnvironmentOutput(
            platform=platform,
            detected_clis=list(detected_clis),
            message=f"Environment synced: {platform} with {len(detected_clis)} CLIs detected.",
        )

    async def list_pending(self, input_data: dict[str, Any]) -> ListPendingOutput:
        """gateway.list_pending - List pending tool invocations with health status."""
        import time
        from datetime import datetime, timezone

        parsed = ListPendingInput.model_validate(input_data)

        pending_requests = self._client_manager.get_pending_requests(parsed.server)
        now = time.time()

        requests: list[PendingRequestInfo] = []
        for req in pending_requests:
            state = self._client_manager.get_request_state(req)
            requests.append(
                PendingRequestInfo(
                    request_id=f"{req.server_name}::{req.request_id}",
                    server_name=req.server_name,
                    tool_id=req.tool_id,
                    started_at_iso=datetime.fromtimestamp(
                        req.started_at, tz=timezone.utc
                    ).isoformat(),
                    elapsed_seconds=now - req.started_at,
                    timeout_ms=req.timeout_ms,
                    state=state.value,
                    last_heartbeat_seconds_ago=now - req.last_heartbeat,
                )
            )

        return ListPendingOutput(
            requests=requests,
            total_pending=len(requests),
        )

    async def cancel(self, input_data: dict[str, Any]) -> CancelOutput:
        """gateway.cancel - Cancel a pending tool invocation."""
        parsed = CancelInput.model_validate(input_data)

        (
            status,
            message,
            was_stalled,
            elapsed,
        ) = await self._client_manager.cancel_request(parsed.request_id, parsed.force)

        return CancelOutput(
            request_id=parsed.request_id,
            status=status,
            message=message,
            was_stalled=was_stalled,
            elapsed_seconds=elapsed,
        )
