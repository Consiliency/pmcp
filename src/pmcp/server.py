"""MCP Gateway Server."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, Literal

import jsonschema
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.types import (
    BlobResourceContents,
    CallToolRequestParams,
    CallToolResult,
    ContentBlock,
    GetPromptRequestParams,
    GetPromptResult,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    PaginatedRequestParams,
    Prompt,
    PromptArgument,
    PromptMessage,
    ReadResourceRequestParams,
    ReadResourceResult,
    Resource,
    TextContent,
    TextResourceContents,
    Tool,
)

from pmcp.client.manager import ClientManager
from pmcp.config.guidance import GuidanceConfig, load_guidance_config
from pmcp.config.loader import (
    StartupSkipReason,
    build_startup_observation_snapshot,
    is_legacy_manifest_auto_start_enabled,
    load_configs,
    load_disabled_auto_start,
    load_enabled_auto_start,
    resolve_startup_configs,
    summarize_startup_resolution,
)
from pmcp.identity import (
    filter_self_references,
    acquire_singleton_lock,
    release_singleton_lock,
)
from pmcp.errors import ErrorCode, GatewayException
from pmcp.manifest.loader import load_manifest
from pmcp.manifest.refresher import (
    get_cache_path,
    load_descriptions_cache,
    refresh_all,
)
from pmcp.policy.policy import PolicyManager
from pmcp.scoped_advisor_audit import (
    SCOPED_ADVISOR_AUDIT_CAPABILITY,
    ScopedAdvisorAudit,
    ScopedAdvisorAuditError,
)
from pmcp.subscriptions import BusCatalogEventSink
from pmcp.summary import generate_capability_summary
from pmcp.tools.handlers import GatewayTools, get_gateway_tool_definitions
from pmcp.types import (
    DescriptionsCache,
    GatewayDiagnosticsInfo,
    InvokeInput,
    LocalMcpServerConfig,
    ResolvedServerConfig,
)

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, minimum: int) -> int:
    """Read a positive int from the environment, clamped to ``minimum``.

    Small local copy of `transport/http.py`'s `_env_int` (SL-4's file, not
    imported from here to avoid a cross-lane import during P3B's parallel
    execution) -- same behaviour: an absent or unparsable value falls back
    to `default`, a parsable one is floored at `minimum`.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


class GatewayServer:
    """MCP Gateway Server."""

    def __init__(
        self,
        project_root: Path | None = None,
        custom_config_path: Path | None = None,
        policy_path: Path | None = None,
        cache_dir: Path | None = None,
        guidance_config_path: Path | None = None,
        host: str = "127.0.0.1",
        port: int = 3344,
        lock_dir: Path | str | None = None,
        audit_jsonl: Path | str | None = None,
        auth_token: str | None = None,
        max_concurrent_spawns: int = 8,
        rate_limit_rpm: int = 0,
        request_timeout: int = 60,
        auth_mode: Literal["none", "shared-secret", "resource-server"] | None = None,
        resource_server_issuer: str | None = None,
        resource_server_jwks_url: str | None = None,
        resource_server_audience: str | None = None,
        required_scopes: list[str] | None = None,
        allowed_origins: list[str] | None = None,
    ) -> None:
        self._project_root = project_root
        self._custom_config_path = custom_config_path
        self._cache_dir = cache_dir or Path(".mcp-gateway")
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._max_concurrent_spawns = max_concurrent_spawns
        self._rate_limit_rpm = rate_limit_rpm
        self._request_timeout = request_timeout
        self._auth_mode = auth_mode
        self._resource_server_issuer = resource_server_issuer
        self._resource_server_jwks_url = resource_server_jwks_url
        self._resource_server_audience = resource_server_audience
        self._required_scopes = required_scopes
        self._allowed_origins = allowed_origins
        # Lock directory - None means use global default (~/.pmcp)
        self._lock_dir: Path | None = Path(lock_dir) if lock_dir else None

        # Initialize policy manager
        self._policy_manager = PolicyManager(policy_path)
        self._scoped_advisor_audit: ScopedAdvisorAudit | None = None
        self._audit_jsonl = Path(audit_jsonl) if audit_jsonl is not None else None
        if audit_jsonl is not None:
            if not self._policy_manager.is_scoped_advisor_policy():
                raise ValueError(
                    "--audit-jsonl requires the exact explicit scoped-advisor policy"
                )

        # Initialize guidance config
        self._guidance_config: GuidanceConfig = load_guidance_config(
            guidance_config_path
        )
        logger.info(f"Guidance level: {self._guidance_config.level}")

        # IF-0-P3B-2: bus, sink, and listen handler are constructed here,
        # before ClientManager -- `_create_server` (which registers the
        # handler on `Server`) runs later, so a bus created there would be
        # invisible to a ClientManager already built.
        self._subscription_bus = InMemorySubscriptionBus()
        self._catalog_events = BusCatalogEventSink(self._subscription_bus)
        self._listen_handler = ListenHandler(
            self._subscription_bus,
            max_subscriptions=_env_int(
                "PMCP_MAX_LISTEN_STREAMS", 64, minimum=1
            ),
        )

        # Initialize client manager
        # TODO(P3B/SL-3): pass catalog_events=self._catalog_events once
        # ClientManager accepts it (IF-0-P3B-2) -- SL-3 (client/manager.py)
        # lands the constructor kwarg in a sibling lane of this same phase;
        # this file must not touch client/manager.py itself. Until that
        # merges, ClientManager uses its null sink and no catalog mutation
        # publishes -- SL-3's own tests cover that gap, not this lane's.
        self._client_manager = ClientManager(
            max_tools_per_server=self._policy_manager.get_max_tools_per_server(),
            max_concurrent_spawns=self._max_concurrent_spawns,
            project_root=project_root,
        )

        # Initialize gateway tools handler
        self._gateway_tools = GatewayTools(
            client_manager=self._client_manager,
            policy_manager=self._policy_manager,
            project_root=project_root,
            custom_config_path=custom_config_path,
            guidance_config=self._guidance_config,
        )
        if self._audit_jsonl is not None:
            self._scoped_advisor_audit = ScopedAdvisorAudit(
                self._audit_jsonl, policy_digest=self._policy_manager.policy_digest
            )
            self._policy_manager.activate_scoped_advisor()
        self._gateway_tools.set_transport_diagnostics(
            GatewayDiagnosticsInfo(
                transport="gateway",
                capabilities=(
                    [SCOPED_ADVISOR_AUDIT_CAPABILITY]
                    if self._scoped_advisor_audit is not None
                    else []
                ),
                policy_digest=(
                    self._policy_manager.policy_digest
                    if self._scoped_advisor_audit is not None
                    else None
                ),
            )
        )

        # Server will be created after initialization with capability summary
        self._server: Server | None = None
        self._capability_summary: str = ""

        # Pre-built descriptions cache
        self._descriptions_cache: DescriptionsCache | None = None

    def _create_server(self, instructions: str | None = None) -> None:
        """Create the MCP server with optional capability instructions."""
        self._server = Server(
            "mcp-gateway",
            instructions=instructions,
            on_list_tools=self._handle_list_tools,
            on_call_tool=self._handle_call_tool,
            on_list_resources=self._handle_list_resources,
            on_read_resource=self._handle_read_resource,
            on_list_prompts=self._handle_list_prompts,
            on_get_prompt=self._handle_get_prompt,
            on_subscriptions_listen=self._listen_handler,
        )

    def _record_scoped_invocation(
        self,
        *,
        gateway_tool: str,
        terminal_status: str,
        arguments: dict[str, Any] | None,
        result: Any,
    ) -> None:
        if self._scoped_advisor_audit is None:
            return
        self._scoped_advisor_audit.record_invocation(
            gateway_tool=gateway_tool,
            terminal_status=terminal_status,
            arguments=arguments,
            result=result,
        )

    def _require_scoped_audit(self) -> None:
        if self._scoped_advisor_audit is not None:
            self._scoped_advisor_audit.require_available()

    async def _handle_list_tools(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        """Adapter for `tools/list`: wraps the bare-list body into a typed result."""

        async def list_tools() -> list[Tool]:
            self._require_scoped_audit()
            return sorted(
                (
                    tool
                    for tool in get_gateway_tool_definitions()
                    if self._policy_manager.is_gateway_tool_allowed(tool.name)
                ),
                key=lambda tool: tool.name,
            )

        return ListToolsResult(tools=await list_tools())

    def _find_gateway_tool(self, name: str) -> Tool | None:
        """Look up a gateway tool definition by name, for input-schema validation."""
        return next(
            (tool for tool in get_gateway_tool_definitions() if tool.name == name),
            None,
        )

    async def _handle_call_tool(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: CallToolRequestParams,
    ) -> CallToolResult:
        """Adapter for `tools/call`.

        Restores the behaviour the removed 1.x ``call_tool`` decorator supplied:
        argument unpacking from typed params, tool input-schema validation
        (2.0.0's low-level ``Server`` does none), and wrapping the domain
        body's bare ``list[TextContent]`` into a ``CallToolResult``.
        """
        name = params.name
        arguments = params.arguments or {}

        tool = self._find_gateway_tool(name)
        if tool is not None:
            try:
                jsonschema.validate(instance=arguments, schema=tool.input_schema)
            except jsonschema.ValidationError as e:
                return CallToolResult(
                    is_error=True,
                    content=[
                        TextContent(
                            type="text", text=f"Input validation error: {e.message}"
                        )
                    ],
                )

        async def call_tool(name: str, arguments: dict[str, Any]) -> list[ContentBlock]:
            try:
                result: Any
                self._require_scoped_audit()

                if not self._policy_manager.is_gateway_tool_allowed(name):
                    payload = {
                        "error": True,
                        "message": f"Gateway tool blocked by policy: {name}",
                    }
                    self._record_scoped_invocation(
                        gateway_tool=name,
                        terminal_status="denied",
                        arguments=arguments,
                        result=payload,
                    )
                    return [
                        TextContent(type="text", text=json.dumps(payload, indent=2))
                    ]

                if self._scoped_advisor_audit is not None and name == "gateway.invoke":
                    scoped_input = InvokeInput.model_validate(arguments)
                    if scoped_input.run_correlation_id is None:
                        raise ValueError(
                            "scoped advisor invoke requires run, seat, and evidence correlations"
                        )

                if name == "gateway.catalog_search":
                    result = await self._gateway_tools.catalog_search(arguments)
                elif name == "gateway.describe":
                    result = await self._gateway_tools.describe(arguments)
                elif name == "gateway.invoke":
                    result = await self._gateway_tools.invoke(arguments)
                elif name == "gateway.refresh":
                    result = await self._gateway_tools.refresh(arguments)
                elif name == "gateway.connect_server":
                    result = await self._gateway_tools.connect_server(arguments)
                elif name == "gateway.disconnect_server":
                    result = await self._gateway_tools.disconnect_server(arguments)
                elif name == "gateway.restart_server":
                    result = await self._gateway_tools.restart_server(arguments)
                elif name == "gateway.health":
                    result = await self._gateway_tools.health()
                elif name == "gateway.config_status":
                    result = await self._gateway_tools.config_status()
                elif name == "gateway.get_startup_policy":
                    result = await self._gateway_tools.get_startup_policy()
                elif name == "gateway.set_startup_policy":
                    result = await self._gateway_tools.set_startup_policy(arguments)
                elif name == "gateway.request_capability":
                    result = await self._gateway_tools.request_capability(arguments)
                elif name == "gateway.sync_environment":
                    result = await self._gateway_tools.sync_environment(arguments)
                elif name == "gateway.provision":
                    result = await self._gateway_tools.provision(arguments)
                elif name == "gateway.update_server":
                    result = await self._gateway_tools.update_server(arguments)
                elif name == "gateway.auth_connect":
                    result = await self._gateway_tools.auth_connect(arguments)
                elif name == "gateway.submit_feedback":
                    result = await self._gateway_tools.submit_feedback(arguments)
                elif name == "gateway.provision_status":
                    result = await self._gateway_tools.provision_status(arguments)
                elif name == "gateway.list_pending":
                    result = await self._gateway_tools.list_pending(arguments)
                elif name == "gateway.cancel":
                    result = await self._gateway_tools.cancel(arguments)
                elif name == "gateway.tasks_list":
                    result = await self._gateway_tools.tasks_list(arguments)
                elif name == "gateway.tasks_get":
                    result = await self._gateway_tools.tasks_get(arguments)
                elif name == "gateway.tasks_result":
                    result = await self._gateway_tools.tasks_result(arguments)
                elif name == "gateway.tasks_cancel":
                    result = await self._gateway_tools.tasks_cancel(arguments)
                elif name == "gateway.search_registry":
                    result = await self._gateway_tools.search_registry(arguments)
                elif name == "gateway.register_discovered_server":
                    result = await self._gateway_tools.register_discovered_server(
                        arguments
                    )
                else:
                    raise ValueError(f"Unknown tool: {name}")

                # Convert Pydantic model to dict if needed
                if hasattr(result, "model_dump"):
                    result = result.model_dump()

                terminal_status = "success"
                if isinstance(result, dict) and result.get("ok") is False:
                    terminal_status = (
                        "denied"
                        if result.get("auth_state") == "policy_denied"
                        else "failure"
                    )
                self._record_scoped_invocation(
                    gateway_tool=name,
                    terminal_status=terminal_status,
                    arguments=arguments,
                    result=result,
                )

                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            except ScopedAdvisorAuditError:
                logger.error("Scoped advisor audit channel failed")
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "error": True,
                                "message": "Scoped advisor audit channel failed",
                            }
                        ),
                    )
                ]
            except Exception as e:
                logger.error(f"Tool execution error: {e}")
                try:
                    failure_status = (
                        "denied"
                        if isinstance(e, GatewayException)
                        and e.code == ErrorCode.E402_TOOL_DENIED
                        else "failure"
                    )
                    self._record_scoped_invocation(
                        gateway_tool=name,
                        terminal_status=failure_status,
                        arguments=arguments,
                        result={"error_type": type(e).__name__},
                    )
                except ScopedAdvisorAuditError:
                    logger.error("Scoped advisor audit channel failed")
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "error": True,
                                    "message": "Scoped advisor audit channel failed",
                                }
                            ),
                        )
                    ]
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"error": True, "message": str(e)[:400]}),
                    )
                ]

        content = await call_tool(name, arguments)
        return CallToolResult(content=content)

    async def _handle_list_resources(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: PaginatedRequestParams | None,
    ) -> ListResourcesResult:
        """Adapter for `resources/list`: proxies downstream servers + L3 guidance."""

        async def list_resources() -> list[Resource]:
            self._require_scoped_audit()
            resources = self._client_manager.get_all_resources()
            # Filter by policy
            allowed_resources = [
                r
                for r in resources
                if self._policy_manager.is_resource_allowed(r.resource_id)
            ]
            resource_list = [
                Resource(
                    uri=r.uri,
                    name=r.name or r.uri,
                    description=r.description,
                    mime_type=r.mime_type,
                )
                for r in allowed_resources
            ]

            # Add L3 guidance resource if enabled
            if (
                self._guidance_config.include_methodology_resource
                and self._policy_manager.is_resource_allowed(
                    "pmcp::pmcp://guidance/code-execution"
                )
            ):
                resource_list.append(
                    Resource(
                        uri="pmcp://guidance/code-execution",
                        name="Code Execution Guide",
                        description="Comprehensive guide for using PMCP with code execution patterns",
                        mime_type="text/markdown",
                    )
                )

            return sorted(resource_list, key=lambda resource: str(resource.uri))

        return ListResourcesResult(resources=await list_resources())

    async def _handle_read_resource(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: ReadResourceRequestParams,
    ) -> ReadResourceResult:
        """Adapter for `resources/read`."""

        async def read_resource(
            uri: str,
        ) -> list[TextResourceContents | BlobResourceContents]:
            self._require_scoped_audit()
            # Find resource by URI
            uri_str = str(uri)

            # Check if it's our L3 guidance resource
            if uri_str == "pmcp://guidance/code-execution":
                if not self._policy_manager.is_resource_allowed(
                    "pmcp::pmcp://guidance/code-execution"
                ):
                    raise ValueError(f"Resource blocked by policy: {uri_str}")
                if not self._guidance_config.include_methodology_resource:
                    raise ValueError("Code execution guidance resource is disabled")

                # Read the guidance markdown file
                guidance_path = (
                    Path(__file__).parent / "resources" / "code_execution_guide.md"
                )
                if not guidance_path.exists():
                    raise ValueError("Code execution guide not found")

                with open(guidance_path) as f:
                    content = f.read()

                return [
                    TextResourceContents(
                        uri=uri_str,
                        mime_type="text/markdown",
                        text=content,
                    )
                ]

            # Otherwise, proxy to downstream servers
            resources = self._client_manager.get_all_resources()
            resource_info = next((r for r in resources if r.uri == uri_str), None)

            if not resource_info:
                raise ValueError(f"Unknown resource: {uri_str}")

            # Check policy
            if not self._policy_manager.is_resource_allowed(resource_info.resource_id):
                raise ValueError(f"Resource blocked by policy: {uri_str}")

            result = await self._client_manager.read_resource(resource_info.resource_id)
            contents = result.get("contents", [])

            # Convert to TextResourceContents
            return [
                TextResourceContents(
                    uri=c.get("uri", uri_str),
                    mime_type=c.get("mimeType"),
                    text=c.get("text", ""),
                )
                for c in contents
                if "text" in c  # Only text contents for now
            ]

        return ReadResourceResult(contents=await read_resource(params.uri))

    async def _handle_list_prompts(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: PaginatedRequestParams | None,
    ) -> ListPromptsResult:
        """Adapter for `prompts/list`: proxies downstream servers."""

        async def list_prompts() -> list[Prompt]:
            self._require_scoped_audit()
            prompts = self._client_manager.get_all_prompts()
            # Filter by policy
            allowed_prompts = [
                p
                for p in prompts
                if self._policy_manager.is_prompt_allowed(p.prompt_id)
            ]
            prompts_list = [
                Prompt(
                    name=p.prompt_id,  # Use full ID for uniqueness
                    description=p.description,
                    arguments=[
                        PromptArgument(
                            name=arg.name,
                            description=arg.description,
                            required=arg.required,
                        )
                        for arg in (p.arguments or [])
                    ]
                    if p.arguments
                    else None,
                )
                for p in allowed_prompts
            ]
            return sorted(prompts_list, key=lambda prompt: prompt.name)

        return ListPromptsResult(prompts=await list_prompts())

    async def _handle_get_prompt(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: GetPromptRequestParams,
    ) -> GetPromptResult:
        """Adapter for `prompts/get`. The body already returns `GetPromptResult`
        directly, so this adapter only unpacks typed params."""

        async def get_prompt(
            name: str, arguments: dict[str, str] | None = None
        ) -> GetPromptResult:
            self._require_scoped_audit()
            # name is the prompt_id (server::name format)
            # Check policy
            if not self._policy_manager.is_prompt_allowed(name):
                raise ValueError(f"Prompt blocked by policy: {name}")

            result = await self._client_manager.get_prompt(name, arguments)

            # Convert result to GetPromptResult
            messages = result.get("messages", [])
            return GetPromptResult(
                description=result.get("description"),
                messages=[
                    PromptMessage(
                        role=m.get("role", "user"),
                        content=TextContent(
                            type="text", text=m.get("content", {}).get("text", "")
                        ),
                    )
                    for m in messages
                ],
            )

        return await get_prompt(params.name, params.arguments)

    async def initialize(self) -> None:
        """Initialize connections to downstream servers and generate capability summary."""
        logger.info("Initializing MCP Gateway...")

        # Load pre-built descriptions cache
        cache_path = get_cache_path(self._cache_dir)
        self._descriptions_cache = load_descriptions_cache(cache_path)

        if self._descriptions_cache:
            logger.info(
                f"Loaded pre-built descriptions for {len(self._descriptions_cache.servers)} servers"
            )
            # Update GatewayTools with loaded cache for offline discovery
            self._gateway_tools._descriptions_cache = self._descriptions_cache
        else:
            logger.info("No pre-built descriptions cache found")

        # Load configs from .mcp.json files
        configs = load_configs(
            project_root=self._project_root,
            custom_config_path=self._custom_config_path,
        )

        # Filter out the gateway itself to prevent recursive connection
        # Uses command-based detection, not just name matching
        configs = filter_self_references(configs)
        manifest = None
        manifest_servers = {}
        try:
            manifest = load_manifest()
            manifest_servers = manifest.servers
        except Exception as e:
            logger.warning(f"Failed to load manifest startup configs: {e}")

        enabled_auto_start = load_enabled_auto_start(
            project_root=self._project_root,
            custom_config_path=self._custom_config_path,
        )
        disabled_auto_start = load_disabled_auto_start(
            project_root=self._project_root,
            custom_config_path=self._custom_config_path,
        )

        resolution = resolve_startup_configs(
            configs,
            manifest_servers=manifest_servers,
            enabled_auto_start=enabled_auto_start,
            disabled_auto_start=disabled_auto_start,
            is_server_allowed=self._policy_manager.is_server_allowed,
            is_auth_available=lambda env_var: bool(os.environ.get(env_var)),
            legacy_manifest_auto_start=is_legacy_manifest_auto_start_enabled(),
        )
        self._gateway_tools.set_startup_observations(
            build_startup_observation_snapshot(resolution)
        )

        counts = summarize_startup_resolution(resolution)
        logger.info(
            "Startup policy summary: "
            f"eager={counts['eager']}, lazy={counts['lazy']}, "
            f"skipped={counts['skipped']}, policy_denied={counts['policy_denied']}, "
            f"missing_auth={counts['missing_auth']}, "
            f"unknown_auto_start={counts['unknown_auto_start']}"
        )
        for skipped in resolution.skipped:
            if skipped.reason == StartupSkipReason.MISSING_AUTH:
                logger.info(
                    f"Skipping startup entry '{skipped.name}' from {skipped.source}: "
                    f"missing_auth; set {skipped.env_var} to enable eager startup"
                )
            elif skipped.reason == StartupSkipReason.UNKNOWN_AUTO_START:
                logger.info(
                    f"Skipping startup entry '{skipped.name}' from {skipped.source}: "
                    "unknown_auto_start; add a matching mcpServers entry or remove it from autoStart"
                )
            else:
                logger.info(
                    f"Skipping startup entry '{skipped.name}' from {skipped.source}: "
                    f"{skipped.reason.value}"
                )

        # Kill any orphan processes from a previous PMCP crash before registering servers
        self._kill_orphan_processes(resolution.lazy_configs + resolution.eager_configs)

        # Register lazy configs FIRST (before connecting auto-start)
        self._client_manager.register_lazy_configs(resolution.lazy_configs)

        # Connect ONLY auto-start servers eagerly
        errors: list[str] = []
        if resolution.eager_configs:
            errors = await self._client_manager.connect_all(resolution.eager_configs)
            if errors:
                logger.warning(
                    f"Some auto-start servers failed to connect: {len(errors)} errors"
                )

        # Start health monitor for heartbeat tracking
        self._client_manager.start_health_monitor()

        statuses = self._client_manager.get_all_server_statuses()
        online = sum(1 for s in statuses if s.status.value == "online")
        tools = [
            tool
            for tool in self._client_manager.get_all_tools()
            if self._policy_manager.is_server_allowed(tool.server_name)
            and self._policy_manager.is_tool_allowed(tool.tool_id)
        ]

        logger.info(
            f"Gateway initialized: {online}/{len(statuses)} servers online, {len(tools)} tools indexed"
        )

        # Generate capability summary for MCP instructions
        # Try pre-built cache first, then LLM, then template
        logger.info("Generating capability summary...")
        if self._policy_manager.scoped_advisor_active:
            self._capability_summary = (
                "PMCP scoped advisor research: only approved Firecrawl and Bright "
                "Data tools are available. Use gateway.catalog_search, "
                "gateway.describe, and correlated gateway.invoke calls."
            )
        else:
            self._capability_summary = await generate_capability_summary(
                tools,
                cache=self._descriptions_cache,
                include_code_guidance=self._guidance_config.include_mcp_instructions,
                custom_instructions=self._guidance_config.custom_instructions,
                provisionable_categories=manifest.get_category_summary()
                if manifest
                else None,
            )

        # If no cache and we have tools, auto-generate cache for next time
        if not self._descriptions_cache and tools and manifest:
            logger.info("Auto-generating descriptions cache for future startups...")
            try:
                # Only cache for connected servers (auto_start ones)
                connected_names = [
                    s.name for s in statuses if s.status.value == "online"
                ]
                self._descriptions_cache = await refresh_all(
                    manifest=manifest,
                    cache_path=cache_path,
                    servers=connected_names,
                )
                # Update GatewayTools with newly generated cache
                self._gateway_tools._descriptions_cache = self._descriptions_cache
                logger.info(
                    f"Cached descriptions for {len(self._descriptions_cache.servers)} servers"
                )
            except Exception as e:
                logger.warning(f"Failed to auto-generate cache: {e}")

        logger.debug("Capability summary:\n%s", self._capability_summary)

        # Start background stale-version indexer (precomputes warnings for low latency)
        self._gateway_tools.start_stale_indexer()

        # Create MCP server with capability instructions
        self._create_server(instructions=self._capability_summary)

    def _kill_orphan_processes(
        self,
        configs: list[ResolvedServerConfig],
        _proc_path: Path = Path("/proc"),
    ) -> None:
        """Kill orphan stdio server processes left over from a previous PMCP crash.

        Scans /proc (Linux only) for processes whose argv0 + args match any configured
        local stdio server. Matching processes are sent SIGKILL immediately since their
        state cannot be adopted.
        """
        if sys.platform != "linux":
            return
        own_pid = os.getpid()
        fingerprints: dict[tuple, str] = {}
        for cfg in configs:
            if isinstance(cfg.config, LocalMcpServerConfig):
                key = (Path(cfg.config.command).name, tuple(cfg.config.args))
                fingerprints[key] = cfg.name
        if not fingerprints:
            return
        for entry in _proc_path.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == own_pid:
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except (PermissionError, FileNotFoundError, ProcessLookupError):
                continue
            parts = [p for p in raw.split(b"\x00") if p]
            if not parts:
                continue
            argv0 = Path(parts[0].decode(errors="replace")).name
            args = tuple(p.decode(errors="replace") for p in parts[1:])
            if (argv0, args) in fingerprints:
                server_name = fingerprints[(argv0, args)]
                logger.warning(
                    f"Found orphan PID {pid} matching server '{server_name}'; sending SIGKILL"
                )
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    logger.warning(f"Insufficient permissions to kill orphan PID {pid}")

    async def run(self, transport: str = "stdio") -> None:
        """Run the MCP server with specified transport.

        Args:
            transport: "stdio" (default) or "http"
        """
        if transport == "http":
            await self._run_http()
        else:
            await self._run_stdio()

    async def _run_stdio(self) -> None:
        """Run with stdio transport (default behavior)."""
        from mcp.server.stdio import stdio_server

        # Acquire singleton lock to prevent multiple gateway instances
        # Uses self._lock_dir (None = global lock at ~/.pmcp)
        if not acquire_singleton_lock(self._lock_dir):
            if self._scoped_advisor_audit is not None:
                self._scoped_advisor_audit.complete()
            logger.error(
                "Another gateway instance is already running. "
                "Only one gateway should run at a time to prevent recursive spawning."
            )
            raise RuntimeError("Another gateway instance is already running")

        try:
            await self.initialize()

            if self._server is None:
                raise RuntimeError("Server not initialized after initialization")

            async with stdio_server() as (read_stream, write_stream):
                logger.info("MCP Gateway server started (stdio)")
                await self._server.run(
                    read_stream,
                    write_stream,
                    self._server.create_initialization_options(),
                )
        finally:
            await self.shutdown()

    async def _run_http(self) -> None:
        """Run with HTTP/SSE transport."""
        import uvicorn

        from pmcp.transport.http import create_http_app

        # Acquire singleton lock to prevent multiple gateway instances
        # Uses self._lock_dir (None = global lock at ~/.pmcp)
        if not acquire_singleton_lock(self._lock_dir):
            if self._scoped_advisor_audit is not None:
                self._scoped_advisor_audit.complete()
            logger.error(
                "Another gateway instance is already running. "
                "Only one gateway should run at a time to prevent recursive spawning."
            )
            raise RuntimeError("Another gateway instance is already running")

        try:
            await self.initialize()

            if self._server is None:
                raise RuntimeError("Server not initialized after initialization")

            app = create_http_app(
                self._server,
                auth_token=self._auth_token,
                auth_mode=self._auth_mode,
                rate_limit_rpm=self._rate_limit_rpm,
                request_timeout=self._request_timeout,
                resource_server_issuer=self._resource_server_issuer,
                resource_server_jwks_url=self._resource_server_jwks_url,
                resource_server_audience=self._resource_server_audience,
                required_scopes=self._required_scopes,
                allowed_origins=self._allowed_origins,
            )
            logger.info(
                f"MCP Gateway server started (http://{self._host}:{self._port})"
            )

            config = uvicorn.Config(
                app,
                host=self._host,
                port=self._port,
                log_level="warning",
            )
            server = uvicorn.Server(config)
            await server.serve()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Shutdown the server."""
        logger.info("Shutting down MCP Gateway...")
        # IF-0-P3B-2: close open listen streams first. `close()` is sync and
        # only closes memory object streams owned by their own handler
        # tasks (no `disconnect_all`/`ClientManager` interaction, no
        # cancel-scope crossing), so ordering it first here does not widen
        # the anyio shutdown debt (Execution Notes > Decision 3). On a real
        # process exit this runs from a `finally` after the transport has
        # already ended, so the graceful `SubscriptionsListenResult` frame
        # may not reach the wire -- that is spec-legal (an abrupt transport
        # close carries no response); EC-P3B-2's server-close half is proven
        # deterministically in-process instead (test_listen_registration.py).
        self._listen_handler.close()
        self._gateway_tools.stop_stale_indexer()
        try:
            await asyncio.wait_for(self._client_manager.disconnect_all(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Shutdown timed out, forcing disconnect")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        finally:
            # Always release singleton lock
            release_singleton_lock()
            if self._scoped_advisor_audit is not None:
                self._scoped_advisor_audit.complete()
        logger.info("MCP Gateway shut down")
