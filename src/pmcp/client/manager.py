"""MCP Client Manager - Manages connections to downstream MCP servers."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
import logging
import os
import random
import string
import time
from collections import deque
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, TypeVar

import mcp.types as mcp_types
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.message import SessionMessage

from pmcp.auth import sanitize_auth_diagnostic
from pmcp.config.loader import make_tool_id
from pmcp.remote_auth import (
    MissingRemoteHeaderAuthError,
    resolve_remote_headers_for_tenant,
)
from pmcp.types import (
    LocalMcpServerConfig,
    McpTaskInfo,
    McpTaskRecord,
    PromptArgumentInfo,
    PromptInfo,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    RequestState,
    ResourceInfo,
    RiskHint,
    ServerStatus,
    ServerStatusEnum,
    TaskMetadataInput,
    TaskSupportMode,
    ToolInfo,
    TraceContextInfo,
)

resource_module: ModuleType | None

try:
    import resource as resource_module

    HAS_RESOURCE = True
except ImportError:
    resource_module = None
    HAS_RESOURCE = False

logger = logging.getLogger(__name__)
_TaskT = TypeVar("_TaskT", bound=asyncio.Task[Any])


def _is_cancel_scope_task_mismatch_error(exc: BaseException) -> bool:
    """Return True for benign anyio cancel-scope task mismatch during shutdown."""
    msg = str(exc).lower()
    return (
        "cancel scope" in msg
        and "different task" in msg
        and "entered" in msg
        and "exit" in msg
    )


# Heartbeat thresholds for health monitoring
HEARTBEAT_WARN_THRESHOLD = 60.0  # Warn if no activity for 60s
HEARTBEAT_STALL_THRESHOLD = 120.0  # Mark as stalled after 120s
HEALTH_CHECK_INTERVAL = 30.0  # Background health check every 30s

# Connection retry settings
MAX_CONNECTION_RETRIES = 3
RETRY_DELAYS = [1.0, 2.0, 4.0]  # Exponential backoff delays in seconds
PREFERRED_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = (
    PREFERRED_PROTOCOL_VERSION,
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

# Memory monitoring
MEMORY_LOG_INTERVAL = 60.0  # Log memory every 60s
MEMORY_WARN_THRESHOLD_MB = 1024  # Warn if process uses > 1GB

# Stdio read limit for downstream MCP server stdout (bytes per JSON-RPC line).
# asyncio's StreamReader default is 64 KiB, which truncates real-world tool
# responses (page scrapes, screenshots, large file reads) into an opaque
# "disconnected unexpectedly". 10 MiB covers realistic responses; override via
# PMCP_STDIO_READ_LIMIT for hosts that need larger or smaller caps.
DEFAULT_STDIO_READ_LIMIT = 10 * 1024 * 1024


def _stdio_read_limit() -> int:
    raw = os.environ.get("PMCP_STDIO_READ_LIMIT")
    if not raw:
        return DEFAULT_STDIO_READ_LIMIT
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "PMCP_STDIO_READ_LIMIT=%r is not an integer; using default %d",
            raw,
            DEFAULT_STDIO_READ_LIMIT,
        )
        return DEFAULT_STDIO_READ_LIMIT
    if value <= 0:
        logger.warning(
            "PMCP_STDIO_READ_LIMIT=%d must be positive; using default %d",
            value,
            DEFAULT_STDIO_READ_LIMIT,
        )
        return DEFAULT_STDIO_READ_LIMIT
    return value


def _get_memory_usage_mb() -> float:
    """Get current process memory usage in MB."""
    try:
        if HAS_RESOURCE and resource_module is not None:
            # ru_maxrss is in KB on Linux, bytes on macOS
            usage = resource_module.getrusage(resource_module.RUSAGE_SELF)
            import sys

            if sys.platform == "darwin":
                return usage.ru_maxrss / 1024 / 1024
            return usage.ru_maxrss / 1024
        # Fallback: read from /proc
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception as e:
        logger.debug(f"memory usage parse error: {e}")
    return 0.0


def _get_system_memory_pct() -> int:
    """Get system memory usage percentage."""
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
            total = meminfo.get("MemTotal", 1)
            available = meminfo.get("MemAvailable", total)
            used_pct = int((total - available) * 100 / total)
            return used_pct
    except Exception as e:
        logger.debug(f"system memory check error: {e}")
        return 0


def _generate_revision_id() -> str:
    """Generate a revision ID for cache invalidation."""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"rev-{int(time.time() * 1000)}-{suffix}"


def _infer_risk_hint(tool_name: str, description: str) -> RiskHint:
    """Infer risk level from tool name/description."""
    low_risk_patterns = ["read", "get", "list", "search", "query", "fetch", "describe"]
    high_risk_patterns = [
        "delete",
        "remove",
        "drop",
        "execute",
        "run",
        "write",
        "create",
        "update",
        "modify",
        "send",
        "post",
        "put",
    ]

    combined = f"{tool_name} {description}".lower()

    for pattern in high_risk_patterns:
        if pattern in combined:
            return RiskHint.HIGH

    for pattern in low_risk_patterns:
        if pattern in combined:
            return RiskHint.LOW

    return RiskHint.MEDIUM


def _extract_tags(server_name: str, tool_name: str, description: str) -> list[str]:
    """Extract tags from tool name/description."""
    tags: set[str] = {server_name}

    categories: dict[str, list[str]] = {
        "database": ["db", "sql", "query", "table", "database"],
        "file": ["file", "directory", "folder", "path"],
        "git": ["git", "commit", "branch", "repository", "repo"],
        "http": ["http", "api", "request", "fetch", "url"],
        "search": ["search", "find", "grep", "filter"],
        "code": ["code", "function", "class", "symbol"],
    }

    combined = f"{tool_name} {description}".lower()

    for category, keywords in categories.items():
        for keyword in keywords:
            if keyword in combined:
                tags.add(category)
                break

    return list(tags)


def _truncate_description(description: str, max_length: int = 100) -> str:
    """Truncate description for catalog display."""
    if not description:
        return ""
    if len(description) <= max_length:
        return description
    return description[: max_length - 3] + "..."


def _raw_metadata(
    payload: dict[str, Any], known_fields: set[str]
) -> dict[str, Any] | None:
    metadata = {key: value for key, value in payload.items() if key not in known_fields}
    return metadata or None


def _schema_dialect(*schemas: dict[str, Any] | None) -> str:
    for schema in schemas:
        if schema and isinstance(schema.get("$schema"), str):
            return schema["$schema"]
    return DEFAULT_SCHEMA_DIALECT


def _is_protocol_version_initialize_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "protocol" in message
        and ("version" in message or PREFERRED_PROTOCOL_VERSION in message)
        and (
            "initialize" in message or "unsupported" in message or "invalid" in message
        )
    )


def _remote_headers(
    server_name: str,
    config: RemoteMcpServerConfig,
    *,
    tenant_id: str | None = None,
) -> dict[str, str] | None:
    """Return remote transport headers with env-var placeholders expanded."""
    if not config.headers:
        return None
    resolution = resolve_remote_headers_for_tenant(
        config.headers,
        server_name=server_name,
        tenant_id=tenant_id,
    )
    if resolution.missing_env_vars:
        raise MissingRemoteHeaderAuthError(server_name, resolution.missing_env_vars)
    return resolution.resolved_headers


def _trace_context_payload(
    trace_context: TraceContextInfo | dict[str, Any] | None,
) -> dict[str, str]:
    if trace_context is None:
        return {}
    parsed = (
        trace_context
        if isinstance(trace_context, TraceContextInfo)
        else TraceContextInfo.model_validate(trace_context)
    )
    return parsed.model_dump(exclude_none=True)


@dataclass
class PendingRequest:
    """Metadata for tracking a pending tool invocation."""

    request_id: int
    server_name: str
    tool_id: str  # Empty for non-tool requests (initialize, tools/list)
    started_at: float  # time.time() when request started
    last_heartbeat: float  # time.time() of last activity
    timeout_ms: int  # Configured timeout
    future: asyncio.Future[Any]
    task_id: str | None = None
    task_status: str | None = None


@dataclass
class ManagedClient:
    """A managed connection to a downstream MCP server."""

    config: ResolvedServerConfig
    process: asyncio.subprocess.Process | None = None
    is_remote: bool = False
    sse_exit_stack: AsyncExitStack | None = None
    write_stream: Any | None = None
    status: ServerStatus = field(
        default_factory=lambda: ServerStatus(
            name="",
            status=ServerStatusEnum.OFFLINE,
            tool_count=0,
        )
    )
    request_id: int = 0
    pending_requests: dict[int, PendingRequest] = field(default_factory=dict)
    read_task: asyncio.Task[None] | None = None
    # Health monitoring: rolling window of response times for avg calculation
    response_times: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    # Reconnect storm guard: True while a _reconnect_loop task is in flight
    reconnecting: bool = False


class ClientManager:
    """Manages connections to downstream MCP servers."""

    def __init__(
        self, max_tools_per_server: int = 100, max_concurrent_spawns: int = 8
    ) -> None:
        self._clients: dict[str, ManagedClient] = {}
        self._tools: dict[str, ToolInfo] = {}
        self._resources: dict[str, ResourceInfo] = {}
        self._prompts: dict[str, PromptInfo] = {}
        self._servers: dict[str, ServerStatus] = {}
        self._lazy_configs: dict[str, ResolvedServerConfig] = {}  # On-demand configs
        self._revision_id: str = _generate_revision_id()
        self._last_refresh_ts: float = time.time()
        self._max_tools_per_server = max_tools_per_server
        self._spawn_semaphore = asyncio.Semaphore(max_concurrent_spawns)
        self._lifecycle_lock = asyncio.Lock()
        self._connect_tasks: dict[str, asyncio.Task[None]] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._background_task_servers: dict[asyncio.Task[Any], str | None] = {}
        self._reconnect_tasks: dict[str, asyncio.Task[None]] = {}
        self._request_counters: dict[str, int] = {}
        self._tasks: dict[tuple[str, str], McpTaskRecord] = {}

    async def connect_all(
        self, configs: list[ResolvedServerConfig], retry: bool = True
    ) -> list[str]:
        """Connect to all configured servers in parallel.

        Args:
            configs: List of server configurations
            retry: Whether to retry failed connections with exponential backoff

        Returns:
            List of error messages for failed connections
        """
        if not configs:
            return []

        async with self._lifecycle_lock:
            return await self._connect_all_unlocked(configs, retry=retry)

    async def _connect_all_unlocked(
        self, configs: list[ResolvedServerConfig], retry: bool = True
    ) -> list[str]:
        """Connect to all configured servers while caller owns lifecycle lock."""
        if not configs:
            return []

        # Connect to all servers concurrently, sharing work for duplicate names.
        tasks_by_name: dict[str, asyncio.Task[None]] = {}
        tasks: list[asyncio.Task[None]] = []
        for config in configs:
            task = tasks_by_name.get(config.name)
            if task is None:
                task = self._track_background_task(
                    asyncio.create_task(self._connect_singleflight(config, retry)),
                    config.name,
                )
                tasks_by_name[config.name] = task
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect errors from failed connections
        errors: list[str] = []
        for config, result in zip(configs, results):
            if isinstance(result, Exception):
                error_msg = f"Failed to connect to {config.name}: {result}"
                logger.error(error_msg)
                errors.append(error_msg)

        self._revision_id = _generate_revision_id()
        self._last_refresh_ts = time.time()

        return errors

    async def _connect_singleflight(
        self, config: ResolvedServerConfig, retry: bool = True
    ) -> None:
        """Share concurrent connection attempts for the same server name."""
        name = config.name
        status = self._servers.get(name)
        if status is not None and status.status == ServerStatusEnum.ONLINE:
            return

        task = self._connect_tasks.get(name)
        if task is None:
            if retry:
                task = asyncio.create_task(self._connect_with_retry(config))
            else:
                task = asyncio.create_task(self._connect_server(config))
            task = self._track_background_task(task, name)
            self._connect_tasks[name] = task

        try:
            await task
        finally:
            if self._connect_tasks.get(name) is task:
                self._connect_tasks.pop(name, None)

    def _track_background_task(
        self, task: _TaskT, server_name: str | None = None
    ) -> _TaskT:
        self._background_tasks.add(task)
        self._background_task_servers[task] = server_name
        task.add_done_callback(self._background_tasks.discard)
        task.add_done_callback(self._background_task_servers.pop)
        return task

    async def _cancel_background_tasks(
        self,
        *,
        server_name: str | None = None,
        exclude: set[asyncio.Task[Any]] | None = None,
    ) -> None:
        exclude = exclude or set()
        tasks = [
            task
            for task in self._background_tasks
            if task not in exclude
            and not task.done()
            and (
                server_name is None
                or self._background_task_servers.get(task) == server_name
                or task is self._reconnect_tasks.get(server_name)
                or task is self._connect_tasks.get(server_name)
            )
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.difference_update(task for task in tasks if task.done())
        for task in tasks:
            if task.done():
                self._background_task_servers.pop(task, None)

    def _next_request_id(self, server_name: str) -> int:
        request_id = self._request_counters.get(server_name, 0) + 1
        self._request_counters[server_name] = request_id
        return request_id

    def register_lazy_configs(self, configs: list[ResolvedServerConfig]) -> None:
        """Register configs for lazy (on-demand) server connections.

        These servers won't connect until first use via ensure_connected().

        Args:
            configs: List of server configurations to register for lazy start
        """
        for config in configs:
            name = config.name
            if name in self._clients:
                logger.debug(
                    f"Server {name} already connected, skipping lazy registration"
                )
                continue

            self._lazy_configs[name] = config
            # Create LAZY status entry so server appears in status listings
            self._servers[name] = ServerStatus(
                name=name,
                status=ServerStatusEnum.LAZY,
                tool_count=0,
            )
            logger.info(f"Registered lazy server: {name}")

    async def ensure_connected(self, server_name: str) -> bool:
        """Ensure a server is connected, triggering lazy-start if needed.

        Args:
            server_name: Name of the server to ensure is connected

        Returns:
            True if server is online, False if connection failed

        Raises:
            ValueError: If server is not registered (neither connected nor lazy)
        """
        async with self._lifecycle_lock:
            if self.is_server_online(server_name):
                return True

            if server_name not in self._lazy_configs:
                if server_name not in self._servers:
                    raise ValueError(f"Unknown server: {server_name}")
                return False

            config = self._lazy_configs[server_name]
            logger.info(f"Lazy-starting server: {server_name}")
            task = self._connect_tasks.get(server_name)
            if task is None:
                task = self._track_background_task(
                    asyncio.create_task(self._connect_with_lifecycle_lock(config)),
                    server_name,
                )
                self._connect_tasks[server_name] = task

        try:
            await task
            async with self._lifecycle_lock:
                self._lazy_configs.pop(server_name, None)
            return True
        except Exception as e:
            logger.error(f"Failed to lazy-start {server_name}: {e}")
            async with self._lifecycle_lock:
                if server_name in self._servers:
                    self._servers[server_name].status = ServerStatusEnum.ERROR
                    self._servers[server_name].last_error = str(e)
            return False
        finally:
            async with self._lifecycle_lock:
                if self._connect_tasks.get(server_name) is task:
                    self._connect_tasks.pop(server_name, None)

    async def _connect_with_lifecycle_lock(self, config: ResolvedServerConfig) -> None:
        async with self._lifecycle_lock:
            await self._connect_with_retry(config)

    async def connect_server(
        self, config: ResolvedServerConfig, retry: bool = True
    ) -> list[str]:
        """Connect one server through same-server single-flight startup."""
        async with self._lifecycle_lock:
            try:
                await self._connect_singleflight(config, retry=retry)
                if self.is_server_online(config.name):
                    self._lazy_configs.pop(config.name, None)
                self._revision_id = _generate_revision_id()
                self._last_refresh_ts = time.time()
                return []
            except Exception as e:
                if config.name in self._servers:
                    self._servers[config.name].status = ServerStatusEnum.ERROR
                    self._servers[config.name].last_error = str(e)
                return [f"Failed to connect to {config.name}: {e}"]

    def cancel_pending_requests(self, server: str) -> int:
        """Cancel pending requests for one server and return newly cancelled count."""
        managed = self._clients.get(server)
        if not managed:
            return 0

        cancelled = 0
        for request_id, pending in list(managed.pending_requests.items()):
            if not pending.future.done():
                pending.future.cancel()
                cancelled += 1
            managed.pending_requests.pop(request_id, None)
        managed.status.pending_request_count = len(managed.pending_requests)
        if cancelled:
            logger.warning(
                f"Force-cancelled {cancelled} pending requests for server {server}"
            )
        return cancelled

    async def disconnect_server(
        self, name: str, force: bool = False
    ) -> tuple[bool, int, str | None]:
        """Disconnect one server, refusing active requests unless forced."""
        pending_requests = self.get_pending_requests(name)
        active_tasks = self.get_active_tasks(name)
        if (pending_requests or active_tasks) and not force:
            return (
                False,
                0,
                "Disconnect refused because this server has pending requests or active MCP tasks. "
                "Use gateway.list_pending to inspect them or retry with force=true.",
            )

        cancelled = self.cancel_pending_requests(name) if pending_requests else 0
        if active_tasks:
            for task in active_tasks:
                ok, _task, message = await self.cancel_task(
                    name, task.task_id, force=True
                )
                if not ok:
                    return (False, cancelled, message)

        async with self._lifecycle_lock:
            managed = self._clients.get(name)
            if not managed:
                status = self._servers.get(name)
                if status is not None:
                    status.status = (
                        ServerStatusEnum.LAZY
                        if name in self._lazy_configs
                        else ServerStatusEnum.OFFLINE
                    )
                    status.tool_count = 0
                    status.resource_count = 0
                    status.prompt_count = 0
                    status.pending_request_count = 0
                return (True, cancelled, None)

            config = managed.config
            managed.status.status = ServerStatusEnum.OFFLINE
            managed.status.pending_request_count = 0

            if managed.read_task and not managed.read_task.done():
                managed.read_task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(managed.read_task), timeout=1.0
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                except Exception:
                    pass

            try:
                if managed.is_remote:
                    if managed.sse_exit_stack is not None:
                        try:
                            await managed.sse_exit_stack.aclose()
                        except RuntimeError as e:
                            if _is_cancel_scope_task_mismatch_error(e):
                                logger.debug(
                                    f"[{name}] Ignoring SSE shutdown cancel-scope mismatch: {e}"
                                )
                            else:
                                raise
                elif managed.process and managed.process.returncode is None:
                    managed.process.terminate()
                    try:
                        await asyncio.wait_for(managed.process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        managed.process.kill()
                        try:
                            await asyncio.wait_for(managed.process.wait(), timeout=3.0)
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"[{name}] Process PID={managed.process.pid} did not exit "
                                "after SIGKILL (possible D-state / uninterruptible I/O wait)"
                            )
            except Exception as e:
                logger.warning(f"Error disconnecting from {name}: {e}")
                return (False, cancelled, str(e))

            await self._cancel_background_tasks(server_name=name)
            self._connect_tasks.pop(name, None)
            self._reconnect_tasks.pop(name, None)
            self._clients.pop(name, None)
            self._remove_server_indexes(name)
            if config is not None and config.source in {"project", "user", "custom"}:
                self._lazy_configs[name] = config
            self._servers[name] = ServerStatus(
                name=name,
                status=ServerStatusEnum.LAZY
                if name in self._lazy_configs
                else ServerStatusEnum.OFFLINE,
                tool_count=0,
            )
            self._revision_id = _generate_revision_id()
            self._last_refresh_ts = time.time()
            return (True, cancelled, None)

    async def restart_server(
        self, config: ResolvedServerConfig, force: bool = False
    ) -> tuple[bool, int, list[str]]:
        """Restart one server by disconnecting then connecting the same config."""
        disconnected, cancelled, error = await self.disconnect_server(
            config.name, force
        )
        if not disconnected:
            return (False, cancelled, [error or "Restart refused."])

        errors = await self.connect_server(config)
        return (len(errors) == 0, cancelled, errors)

    def is_lazy_server(self, name: str) -> bool:
        """Check if server is registered for lazy start but not yet connected."""
        return name in self._lazy_configs

    def get_lazy_server_names(self) -> list[str]:
        """Get list of servers registered for lazy start."""
        return list(self._lazy_configs.keys())

    async def _connect_with_retry(self, config: ResolvedServerConfig) -> None:
        """Connect to a server with exponential backoff retry."""
        last_error: Exception | None = None

        for attempt in range(MAX_CONNECTION_RETRIES):
            try:
                await self._connect_server(config)
                return  # Success
            except Exception as e:
                last_error = e
                if attempt < MAX_CONNECTION_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    logger.warning(
                        f"Connection to {config.name} failed (attempt {attempt + 1}/"
                        f"{MAX_CONNECTION_RETRIES}), retrying in {delay}s: {e}"
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted
        if last_error:
            raise last_error

    async def _connect_server(self, config: ResolvedServerConfig) -> None:
        """Connect to a single MCP server."""
        if isinstance(config.config, RemoteMcpServerConfig):
            if config.config.type in ("http", "streamable-http"):
                await self._connect_streamable_http(config)
            else:
                await self._connect_sse(config)
            return

        await self._connect_stdio(config)

    def _remove_server_indexes(self, name: str) -> None:
        """Remove catalog entries owned by one server."""
        for tool_id, tool in list(self._tools.items()):
            if tool.server_name == name:
                self._tools.pop(tool_id, None)
        for resource_id, resource in list(self._resources.items()):
            if resource.server_name == name:
                self._resources.pop(resource_id, None)
        for prompt_id, prompt in list(self._prompts.items()):
            if prompt.server_name == name:
                self._prompts.pop(prompt_id, None)
        for key in list(self._tasks):
            if key[0] == name:
                self._tasks.pop(key, None)

    def _server_supports_tasks(self, managed: ManagedClient) -> bool:
        capabilities = managed.status.server_capabilities or {}
        return "tasks" in capabilities and capabilities.get("tasks") is not False

    def _tool_task_support(self, tool_info: ToolInfo) -> TaskSupportMode:
        support = (tool_info.execution or {}).get("taskSupport")
        if support in {"optional", "required"}:
            return support  # type: ignore[return-value]
        return "forbidden"

    def _task_wire_metadata(
        self, task: TaskMetadataInput | dict[str, Any] | None
    ) -> dict[str, Any]:
        if task is None:
            return {}
        parsed = (
            task
            if isinstance(task, TaskMetadataInput)
            else TaskMetadataInput.model_validate(task)
        )
        payload: dict[str, Any] = {}
        if parsed.metadata:
            payload["metadata"] = parsed.metadata
        if parsed.ttl is not None:
            payload["ttl"] = parsed.ttl
        if parsed.poll_interval is not None:
            payload["pollInterval"] = parsed.poll_interval
        if parsed.requestor_context:
            payload["requestorContext"] = parsed.requestor_context
        return payload

    def _task_request_params(
        self,
        *,
        task_id: str | None = None,
        cursor: str | None = None,
        requestor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if task_id is not None:
            payload["taskId"] = task_id
        if cursor:
            payload["cursor"] = cursor
        if requestor_context:
            payload["task"] = {"requestorContext": requestor_context}
        return payload

    def _extract_task_payload(self, result: dict[str, Any]) -> dict[str, Any] | None:
        task = result.get("task")
        if isinstance(task, dict):
            return task
        if isinstance(result.get("taskId"), str):
            return result
        return None

    def _task_info_from_payload(self, payload: dict[str, Any]) -> McpTaskInfo | None:
        task_id = payload.get("taskId") or payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return None
        status_message = payload.get("statusMessage", payload.get("status_message"))
        poll_interval = payload.get("pollInterval", payload.get("poll_interval"))
        return McpTaskInfo(
            task_id=task_id,
            status=payload.get("status"),
            status_message=status_message if isinstance(status_message, str) else None,
            created_at=payload.get("createdAt", payload.get("created_at")),
            updated_at=payload.get(
                "updatedAt",
                payload.get(
                    "updated_at",
                    payload.get("lastUpdatedAt", payload.get("last_updated_at")),
                ),
            ),
            ttl=payload.get("ttl"),
            poll_interval=poll_interval,
            raw=payload,
        )

    def _record_task(
        self,
        server_name: str,
        task_info: McpTaskInfo,
        *,
        tool_id: str | None = None,
        requestor_context: dict[str, Any] | None = None,
    ) -> McpTaskRecord:
        existing = self._tasks.get((server_name, task_info.task_id))
        record = McpTaskRecord(
            task_id=task_info.task_id,
            status=task_info.status,
            status_message=task_info.status_message,
            created_at=task_info.created_at
            if existing is None
            else existing.created_at,
            updated_at=task_info.updated_at or time.time(),
            ttl=task_info.ttl,
            poll_interval=task_info.poll_interval,
            raw=task_info.raw,
            server_name=server_name,
            tool_id=tool_id or (existing.tool_id if existing else None),
            requestor_context=requestor_context
            or (existing.requestor_context if existing else None),
        )
        self._tasks[(server_name, task_info.task_id)] = record
        return record

    def _terminal_task(self, task: McpTaskRecord) -> bool:
        return task.status in {"completed", "failed", "cancelled"}

    def get_task_record(self, server_name: str, task_id: str) -> McpTaskRecord | None:
        return self._tasks.get((server_name, task_id))

    def get_tracked_tasks(self, server_name: str | None = None) -> list[McpTaskRecord]:
        return sorted(
            [
                task
                for (server, _), task in self._tasks.items()
                if server_name is None or server == server_name
            ],
            key=lambda task: (task.server_name, task.task_id),
        )

    def get_active_tasks(self, server_name: str | None = None) -> list[McpTaskRecord]:
        return [
            task
            for task in self.get_tracked_tasks(server_name)
            if not self._terminal_task(task)
        ]

    async def cancel_active_tasks(
        self, server_name: str | None = None
    ) -> tuple[int, list[str]]:
        cancelled = 0
        errors: list[str] = []
        for task in list(self.get_active_tasks(server_name)):
            ok, _record, message = await self.cancel_task(
                task.server_name, task.task_id, force=True
            )
            if ok:
                cancelled += 1
            else:
                errors.append(message)
        return cancelled, errors

    def _index_tools(self, name: str, tools: list[dict[str, Any]]) -> int:
        indexed = 0
        known_fields = {
            "name",
            "title",
            "description",
            "inputSchema",
            "outputSchema",
            "icons",
            "annotations",
            "execution",
        }
        for tool in tools:
            if indexed >= self._max_tools_per_server:
                logger.warning(
                    f"Server {name} has more than {self._max_tools_per_server} tools, truncating"
                )
                break

            tool_name = tool["name"]
            tool_id = make_tool_id(name, tool_name)
            description = tool.get("description", "")
            input_schema = tool.get("inputSchema", {})
            output_schema = tool.get("outputSchema")

            tool_info = ToolInfo(
                tool_id=tool_id,
                server_name=name,
                tool_name=tool_name,
                title=tool.get("title"),
                description=description,
                short_description=_truncate_description(description),
                input_schema=input_schema,
                icons=tool.get("icons"),
                output_schema=output_schema,
                annotations=tool.get("annotations"),
                execution=tool.get("execution"),
                schema_dialect=_schema_dialect(input_schema, output_schema),
                raw_metadata=_raw_metadata(tool, known_fields),
                tags=_extract_tags(name, tool_name, description),
                risk_hint=_infer_risk_hint(tool_name, description),
            )

            self._tools[tool_id] = tool_info
            indexed += 1
        return indexed

    def _index_resources(self, name: str, resources: list[dict[str, Any]]) -> int:
        known_fields = {
            "uri",
            "name",
            "title",
            "description",
            "mimeType",
            "icons",
            "annotations",
        }
        for resource in resources:
            uri = resource.get("uri", "")
            resource_id = f"{name}::{uri}"
            resource_info = ResourceInfo(
                resource_id=resource_id,
                server_name=name,
                uri=uri,
                name=resource.get("name"),
                title=resource.get("title"),
                description=resource.get("description"),
                mime_type=resource.get("mimeType"),
                icons=resource.get("icons"),
                annotations=resource.get("annotations"),
                raw_metadata=_raw_metadata(resource, known_fields),
            )
            self._resources[resource_id] = resource_info
        return len(resources)

    def _index_prompts(self, name: str, prompts: list[dict[str, Any]]) -> int:
        known_prompt_fields = {
            "name",
            "title",
            "description",
            "arguments",
            "icons",
            "annotations",
        }
        known_arg_fields = {"name", "title", "description", "required"}
        for prompt in prompts:
            prompt_name = prompt.get("name", "")
            prompt_id = f"{name}::{prompt_name}"
            arguments = None
            if prompt.get("arguments"):
                arguments = [
                    PromptArgumentInfo(
                        name=arg.get("name", ""),
                        title=arg.get("title"),
                        description=arg.get("description"),
                        required=arg.get("required", False),
                        raw_metadata=_raw_metadata(arg, known_arg_fields),
                    )
                    for arg in prompt["arguments"]
                ]
            prompt_info = PromptInfo(
                prompt_id=prompt_id,
                server_name=name,
                name=prompt_name,
                title=prompt.get("title"),
                description=prompt.get("description"),
                arguments=arguments,
                icons=prompt.get("icons"),
                annotations=prompt.get("annotations"),
                raw_metadata=_raw_metadata(prompt, known_prompt_fields),
            )
            self._prompts[prompt_id] = prompt_info
        return len(prompts)

    async def _index_capabilities(self, managed: ManagedClient) -> tuple[int, int, int]:
        name = managed.config.name

        tools_result = await self._send_request(managed, "tools/list", {})
        indexed = self._index_tools(name, tools_result.get("tools", []))

        resources_task = self._send_request(managed, "resources/list", {})
        prompts_task = self._send_request(managed, "prompts/list", {})
        listing_results = await asyncio.gather(
            resources_task, prompts_task, return_exceptions=True
        )

        resource_count = 0
        resources_result = listing_results[0]
        if isinstance(resources_result, BaseException):
            logger.debug(f"Server {name} doesn't support resources: {resources_result}")
        else:
            resource_count = self._index_resources(
                name, resources_result.get("resources", [])
            )

        prompt_count = 0
        prompts_result = listing_results[1]
        if isinstance(prompts_result, BaseException):
            logger.debug(f"Server {name} doesn't support prompts: {prompts_result}")
        else:
            prompt_count = self._index_prompts(name, prompts_result.get("prompts", []))

        return indexed, resource_count, prompt_count

    async def _connect_stdio(self, config: ResolvedServerConfig) -> None:
        """Connect to a local stdio MCP server."""
        name = config.name

        # Clean up any existing live connection before spawning a replacement
        if name in self._clients:
            existing = self._clients[name]
            logger.warning(
                f"[{name}] Existing live connection found; cleaning up before reconnect"
            )
            await self._cleanup_client(name, existing)
        else:
            self._remove_server_indexes(name)

        # Initialize status
        status = ServerStatus(
            name=name,
            status=ServerStatusEnum.CONNECTING,
            tool_count=0,
        )
        self._servers[name] = status

        if not isinstance(config.config, LocalMcpServerConfig):
            raise ValueError(f"Server {name} has unsupported local config type")

        local_config = config.config

        if not local_config.command:
            raise ValueError(
                f"Server {name} missing command - only stdio transport supported"
            )

        logger.info(f"Connecting to MCP server: {name}")

        # Build environment
        env = os.environ.copy()
        if local_config.env:
            env.update(local_config.env)

        # Spawn process (semaphore caps concurrent spawns to avoid FD exhaustion)
        async with self._spawn_semaphore:
            process = await asyncio.create_subprocess_exec(
                local_config.command,
                *local_config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=local_config.cwd,
                env=env,
                limit=_stdio_read_limit(),
            )

        managed = ManagedClient(
            config=config,
            process=process,
            status=status,
        )
        self._clients[name] = managed

        # Start reading stderr in background
        if process.stderr:
            self._track_background_task(
                asyncio.create_task(self._read_stderr(name, process.stderr)),
                name,
            )

        try:
            # Start reading stdout
            managed.read_task = self._track_background_task(
                asyncio.create_task(self._read_stdout(name, managed)),
                name,
            )

            # Initialize connection
            await self._send_initialize(managed)

            indexed, resource_count, prompt_count = await self._index_capabilities(
                managed
            )

            # Update status
            status.status = ServerStatusEnum.ONLINE
            status.tool_count = indexed
            status.resource_count = resource_count
            status.prompt_count = prompt_count
            status.last_connected_at = time.time()

            logger.info(
                f"Connected to {name}: {indexed} tools, "
                f"{resource_count} resources, {prompt_count} prompts indexed"
            )

        except Exception as e:
            status.status = ServerStatusEnum.ERROR
            status.last_error = str(e)
            if managed.read_task and not managed.read_task.done():
                managed.read_task.cancel()
                try:
                    await asyncio.shield(managed.read_task)
                except (asyncio.CancelledError, Exception):
                    pass
            if process.returncode is None:
                process.kill()
            raise

    async def _connect_sse(self, config: ResolvedServerConfig) -> None:
        """Connect to a remote SSE MCP server."""
        if not isinstance(config.config, RemoteMcpServerConfig):
            raise ValueError(f"Server {config.name} has unsupported remote config type")

        remote_config = config.config
        headers = _remote_headers(config.name, remote_config)
        await self._connect_remote_stream(
            config,
            sse_client(remote_config.url, headers=headers),
            transport_name="SSE",
        )

    async def _connect_streamable_http(self, config: ResolvedServerConfig) -> None:
        """Connect to a remote streamable-HTTP MCP server."""
        if not isinstance(config.config, RemoteMcpServerConfig):
            raise ValueError(f"Server {config.name} has unsupported remote config type")

        remote_config = config.config
        headers = _remote_headers(config.name, remote_config)
        await self._connect_remote_stream(
            config,
            streamablehttp_client(remote_config.url, headers=headers),
            transport_name="streamable HTTP",
        )

    async def _connect_remote_stream(
        self,
        config: ResolvedServerConfig,
        transport_context: Any,
        *,
        transport_name: str,
    ) -> None:
        """Connect to a remote MCP server using a read/write stream transport."""
        name = config.name

        if name in self._clients:
            existing = self._clients[name]
            logger.warning(
                f"[{name}] Existing live connection found; cleaning up before reconnect"
            )
            await self._cleanup_client(name, existing)
        else:
            self._remove_server_indexes(name)

        status = ServerStatus(
            name=name,
            status=ServerStatusEnum.CONNECTING,
            tool_count=0,
        )
        self._servers[name] = status

        logger.info(f"Connecting to remote MCP server via {transport_name}: {name}")

        remote_stack = AsyncExitStack()
        transport = await remote_stack.enter_async_context(transport_context)
        read_stream, write_stream = transport[:2]

        managed = ManagedClient(
            config=config,
            process=None,
            is_remote=True,
            sse_exit_stack=remote_stack,
            write_stream=write_stream,
            status=status,
        )
        self._clients[name] = managed

        try:
            managed.read_task = self._track_background_task(
                asyncio.create_task(self._read_sse(name, managed, read_stream)),
                name,
            )

            await self._send_initialize(managed)

            indexed, resource_count, prompt_count = await self._index_capabilities(
                managed
            )

            status.status = ServerStatusEnum.ONLINE
            status.tool_count = indexed
            status.resource_count = resource_count
            status.prompt_count = prompt_count
            status.last_connected_at = time.time()

            logger.info(
                f"Connected to {name}: {indexed} tools, "
                f"{resource_count} resources, {prompt_count} prompts indexed"
            )

        except Exception as e:
            status.status = ServerStatusEnum.ERROR
            status.last_error = str(e)
            await remote_stack.aclose()
            raise

    async def _read_stderr(self, name: str, stderr: asyncio.StreamReader) -> None:
        """Read stderr from a server process."""
        try:
            while True:
                try:
                    line = await asyncio.wait_for(stderr.readline(), timeout=120.0)
                except asyncio.TimeoutError:
                    logger.debug(f"[{name}] stderr readline timed out, continuing")
                    continue
                if not line:
                    break
                logger.debug(f"[{name}] stderr: {line.decode().strip()}")
        except Exception as e:
            logger.debug(f"[{name}] stderr reader error: {e}")

    async def _read_stdout(self, name: str, managed: ManagedClient) -> None:
        """Read JSON-RPC messages from stdout."""
        if not managed.process or not managed.process.stdout:
            return

        read_failure_reason: str | None = None
        try:
            while True:
                line = await managed.process.stdout.readline()
                if not line:
                    # EOF - server process has exited
                    break

                # UPDATE heartbeat on ANY output from server
                now = time.time()
                managed.status.last_activity_at = now

                try:
                    message = json.loads(line.decode())
                    msg_id = message.get("id")
                    if msg_id is not None and msg_id in managed.pending_requests:
                        pending = managed.pending_requests.pop(msg_id)
                        pending.last_heartbeat = now  # Update request heartbeat

                        # Track response time
                        elapsed_ms = (now - pending.started_at) * 1000
                        managed.response_times.append(elapsed_ms)
                        if managed.response_times:
                            managed.status.avg_response_time_ms = sum(
                                managed.response_times
                            ) / len(managed.response_times)

                        # Update pending count
                        managed.status.pending_request_count = len(
                            managed.pending_requests
                        )

                        if "error" in message:
                            pending.future.set_exception(
                                Exception(
                                    message["error"].get("message", "Unknown error")
                                )
                            )
                        else:
                            pending.future.set_result(message.get("result", {}))
                except json.JSONDecodeError:
                    # Non-JSON output still counts as heartbeat for all pending
                    for req in managed.pending_requests.values():
                        req.last_heartbeat = now
                    logger.debug(f"[{name}] Non-JSON output: {line.decode().strip()}")
        except asyncio.LimitOverrunError as e:
            limit = _stdio_read_limit()
            read_failure_reason = (
                f"stdout line exceeded {limit}-byte read limit "
                f"(set PMCP_STDIO_READ_LIMIT to raise)"
            )
            logger.warning(f"[{name}] {read_failure_reason}: {e}")
        except Exception as e:
            read_failure_reason = f"stdout read error: {e}"
            logger.warning(f"[{name}] {read_failure_reason}")
        finally:
            # Mark server as offline when stdout closes
            # Only warn if status was ONLINE (unexpected disconnect)
            # If status is already OFFLINE, it's a graceful shutdown
            if managed.status.status == ServerStatusEnum.ONLINE:
                detail = read_failure_reason or "process exited"
                logger.warning(f"Server {name} disconnected unexpectedly: {detail}")
                managed.status.status = ServerStatusEnum.ERROR
                managed.status.last_error = (
                    read_failure_reason or "Server process exited"
                )
                # Schedule auto-reconnect if we have the config (storm guard: only one task)
                if managed.config is not None and not managed.reconnecting:
                    managed.reconnecting = True
                    self._schedule_reconnect(name, managed.config)
            else:
                logger.debug(f"Server {name} disconnected (graceful shutdown)")
            # Cancel any pending requests
            for request_id, pending in list(managed.pending_requests.items()):
                if not pending.future.done():
                    pending.future.set_exception(
                        ConnectionError(f"Server {name} disconnected")
                    )
            managed.pending_requests.clear()
            managed.status.pending_request_count = 0

    def _schedule_reconnect(self, name: str, config: ResolvedServerConfig) -> None:
        """Schedule one reconnect task per server across client replacement."""
        task = self._reconnect_tasks.get(name)
        if task is not None and not task.done():
            return
        task = asyncio.create_task(
            self._reconnect_loop(name, config),
            name=f"reconnect-{name}",
        )
        self._reconnect_tasks[name] = task
        self._track_background_task(task, name)

        def clear_reconnect(done: asyncio.Task[None]) -> None:
            if self._reconnect_tasks.get(name) is done:
                self._reconnect_tasks.pop(name, None)

        task.add_done_callback(clear_reconnect)

    async def _reconnect_loop(self, name: str, config: ResolvedServerConfig) -> None:
        """Attempt to reconnect a crashed server with exponential back-off.

        Tries up to 3 times with 5 s / 15 s / 30 s delays. Gives up if another
        caller has already brought the server back online.
        """
        delays = [5.0, 15.0, 30.0]
        try:
            for attempt, delay in enumerate(delays, start=1):
                await asyncio.sleep(delay)
                # If someone else already reconnected (e.g. manual refresh), stop.
                managed = self._clients.get(name)
                if managed and managed.status.status == ServerStatusEnum.ONLINE:
                    logger.debug(
                        f"[{name}] already online; skipping reconnect attempt {attempt}"
                    )
                    return
                logger.info(f"[{name}] reconnect attempt {attempt}/{len(delays)} ...")
                try:
                    await self._connect_singleflight(config)
                    logger.info(f"[{name}] reconnected successfully")
                    return
                except Exception as e:
                    safe_error = sanitize_auth_diagnostic(e)
                    logger.warning(
                        f"[{name}] reconnect attempt {attempt} failed: {safe_error}"
                    )
            logger.error(
                f"[{name}] all reconnect attempts failed; server remains offline"
            )
        finally:
            self._reconnect_tasks.pop(name, None)
            if managed := self._clients.get(name):
                managed.reconnecting = False

    async def _read_sse(
        self, name: str, managed: ManagedClient, read_stream: Any
    ) -> None:
        """Read JSON-RPC messages from an SSE stream."""
        try:
            async for message in read_stream:
                now = time.time()
                managed.status.last_activity_at = now

                if isinstance(message, Exception):
                    for req in managed.pending_requests.values():
                        req.last_heartbeat = now
                    raise message

                payload = message.message.model_dump(
                    by_alias=True,
                    mode="json",
                    exclude_none=True,
                )
                msg_id = payload.get("id")
                if msg_id is not None and msg_id in managed.pending_requests:
                    pending = managed.pending_requests.pop(msg_id)
                    pending.last_heartbeat = now

                    elapsed_ms = (now - pending.started_at) * 1000
                    managed.response_times.append(elapsed_ms)
                    if managed.response_times:
                        managed.status.avg_response_time_ms = sum(
                            managed.response_times
                        ) / len(managed.response_times)

                    managed.status.pending_request_count = len(managed.pending_requests)

                    if "error" in payload:
                        pending.future.set_exception(
                            Exception(payload["error"].get("message", "Unknown error"))
                        )
                    else:
                        pending.future.set_result(payload.get("result", {}))
        except Exception as e:
            logger.debug(f"[{name}] SSE read error: {e}")
        finally:
            if managed.status.status == ServerStatusEnum.ONLINE:
                logger.warning(f"Server {name} disconnected unexpectedly")
                managed.status.status = ServerStatusEnum.ERROR
                managed.status.last_error = "SSE connection closed"
            else:
                logger.debug(f"Server {name} disconnected (graceful shutdown)")

            for request_id, pending in list(managed.pending_requests.items()):
                if not pending.future.done():
                    pending.future.set_exception(
                        ConnectionError(f"Server {name} disconnected")
                    )
            managed.pending_requests.clear()
            managed.status.pending_request_count = 0

    async def _send_request(
        self,
        managed: ManagedClient,
        method: str,
        params: dict[str, Any],
        tool_id: str = "",
        timeout_ms: int = 30000,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for response."""
        request_id = self._next_request_id(managed.config.name)
        managed.request_id = request_id
        now = time.time()

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        # Create PendingRequest with metadata for health monitoring
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        pending = PendingRequest(
            request_id=request_id,
            server_name=managed.config.name,
            tool_id=tool_id,
            started_at=now,
            last_heartbeat=now,
            timeout_ms=timeout_ms,
            future=future,
        )
        managed.pending_requests[request_id] = pending
        managed.status.pending_request_count = len(managed.pending_requests)

        # Send request
        if managed.is_remote:
            if managed.write_stream is None:
                raise RuntimeError("Remote stream not connected")
            msg = mcp_types.JSONRPCMessage.model_validate(request)
            await managed.write_stream.send(SessionMessage(msg))
        else:
            if not managed.process or not managed.process.stdin:
                raise RuntimeError("Process not running")

            data = json.dumps(request) + "\n"
            managed.process.stdin.write(data.encode())
            await managed.process.stdin.drain()

        # Wait for response with timeout
        try:
            result = await asyncio.wait_for(future, timeout=timeout_ms / 1000.0)
            return result
        except asyncio.TimeoutError:
            managed.pending_requests.pop(request_id, None)
            managed.status.pending_request_count = len(managed.pending_requests)
            raise TimeoutError(f"Request {method} timed out")

    async def _send_initialize(self, managed: ManagedClient) -> None:
        """Send initialize handshake."""
        params = {
            "protocolVersion": PREFERRED_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "mcp-gateway", "version": "1.0.0"},
        }
        try:
            result = await self._send_request(managed, "initialize", params)
            requested_protocol_version = PREFERRED_PROTOCOL_VERSION
        except Exception as exc:
            if not _is_protocol_version_initialize_error(exc):
                raise
            legacy_params = {**params, "protocolVersion": "2024-11-05"}
            result = await self._send_request(managed, "initialize", legacy_params)
            requested_protocol_version = "2024-11-05"

        protocol_version = result.get("protocolVersion")
        if isinstance(protocol_version, str):
            managed.status.protocol_version = protocol_version
            if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
                logger.debug(
                    "Server %s negotiated unrecognized protocol version %s",
                    managed.config.name,
                    protocol_version,
                )
        else:
            managed.status.protocol_version = requested_protocol_version

        capabilities = result.get("capabilities")
        if isinstance(capabilities, dict):
            managed.status.server_capabilities = capabilities

        # Send initialized notification (no response expected)
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        if managed.is_remote:
            if managed.write_stream is None:
                raise RuntimeError("Remote stream not connected")
            msg = mcp_types.JSONRPCMessage.model_validate(notification)
            await managed.write_stream.send(SessionMessage(msg))
        elif managed.process and managed.process.stdin:
            data = json.dumps(notification) + "\n"
            managed.process.stdin.write(data.encode())
            await managed.process.stdin.drain()

    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        async with self._lifecycle_lock:
            await self._disconnect_all_unlocked()

    async def _disconnect_all_unlocked(self) -> None:
        """Disconnect from all servers while caller owns the lifecycle boundary."""
        # Stop health monitor if running
        self.stop_health_monitor()

        clients = list(self._clients.items())
        for name, managed in clients:
            try:
                logger.info(f"Disconnecting from {name}")

                # Mark as disconnecting BEFORE canceling read task to avoid
                # false "disconnected unexpectedly" warnings
                managed.status.status = ServerStatusEnum.OFFLINE

                # Cancel pending requests first
                for request_id, pending in list(managed.pending_requests.items()):
                    if not pending.future.done():
                        pending.future.cancel()
                managed.pending_requests.clear()
                managed.status.pending_request_count = 0

                # Cancel read task
                if managed.read_task:
                    managed.read_task.cancel()
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(managed.read_task), timeout=1.0
                        )
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass

                # Close transport
                if managed.is_remote:
                    if managed.sse_exit_stack is not None:
                        try:
                            await managed.sse_exit_stack.aclose()
                        except RuntimeError as e:
                            if _is_cancel_scope_task_mismatch_error(e):
                                logger.debug(
                                    f"[{name}] Ignoring SSE shutdown cancel-scope mismatch: {e}"
                                )
                            else:
                                raise
                elif managed.process and managed.process.returncode is None:
                    managed.process.terminate()
                    try:
                        await asyncio.wait_for(managed.process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        managed.process.kill()
                        try:
                            await asyncio.wait_for(managed.process.wait(), timeout=3.0)
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"[{name}] Process PID={managed.process.pid} did not exit "
                                f"after SIGKILL (possible D-state / uninterruptible I/O wait)"
                            )
            except Exception as e:
                logger.warning(f"Error disconnecting from {name}: {e}")

        current = asyncio.current_task()
        exclude = {current} if current is not None else set()
        await self._cancel_background_tasks(exclude=exclude)
        self._connect_tasks.clear()
        self._reconnect_tasks.clear()
        self._clients.clear()
        self._tools.clear()
        self._resources.clear()
        self._prompts.clear()
        self._tasks.clear()
        self._servers.clear()
        self._lazy_configs.clear()

    async def _cleanup_client(self, name: str, managed: ManagedClient) -> None:
        """Cancel a client's read task, kill its process, and remove it from registries.

        Safe to call on any managed client regardless of state. All exceptions are
        suppressed so callers always complete successfully.
        """
        if managed.read_task and not managed.read_task.done():
            managed.read_task.cancel()
            try:
                await asyncio.shield(managed.read_task)
            except (asyncio.CancelledError, Exception):
                pass
        await self._cancel_background_tasks(server_name=name)
        if managed.process and managed.process.returncode is None:
            managed.process.kill()
            try:
                await asyncio.wait_for(managed.process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{name}] Process did not exit after SIGKILL in _cleanup_client"
                )
        self._clients.pop(name, None)
        self._servers.pop(name, None)
        self._remove_server_indexes(name)

    async def refresh(self, configs: list[ResolvedServerConfig]) -> list[str]:
        """Refresh connections (disconnect + reconnect)."""
        async with self._lifecycle_lock:
            await self._disconnect_all_unlocked()
            return await self._connect_all_unlocked(configs)

    async def adopt_process(
        self,
        name: str,
        process: asyncio.subprocess.Process,
        config: ResolvedServerConfig,
    ) -> None:
        """Adopt an already-running subprocess as a managed MCP client.

        Used when npx-based servers start during installation.
        The process must have stdin/stdout pipes available.

        Args:
            name: Server name
            process: Running subprocess with stdin/stdout pipes
            config: Server configuration

        Raises:
            RuntimeError: If process is not running or missing pipes
            Exception: If MCP initialization fails
        """
        # Validate process state
        if process.returncode is not None:
            raise RuntimeError(f"Process for {name} has already exited")
        if not process.stdin:
            raise RuntimeError(f"Process for {name} has no stdin pipe")
        if not process.stdout:
            raise RuntimeError(f"Process for {name} has no stdout pipe")

        logger.info(f"Adopting process for MCP server: {name}")

        # Initialize status
        status = ServerStatus(
            name=name,
            status=ServerStatusEnum.CONNECTING,
            tool_count=0,
        )
        self._servers[name] = status

        managed = ManagedClient(
            config=config,
            process=process,
            status=status,
        )
        self._clients[name] = managed

        # Start reading stderr in background (if available)
        if process.stderr:
            self._track_background_task(
                asyncio.create_task(self._read_stderr(name, process.stderr)),
                name,
            )

        try:
            # Start reading stdout for JSON-RPC responses
            managed.read_task = self._track_background_task(
                asyncio.create_task(self._read_stdout(name, managed)),
                name,
            )

            # Initialize MCP connection
            await self._send_initialize(managed)

            indexed, resource_count, prompt_count = await self._index_capabilities(
                managed
            )

            # Update status
            status.status = ServerStatusEnum.ONLINE
            status.tool_count = indexed
            status.resource_count = resource_count
            status.prompt_count = prompt_count
            status.last_connected_at = time.time()

            # Update revision
            self._revision_id = _generate_revision_id()
            self._last_refresh_ts = time.time()

            logger.info(f"Adopted {name}: {indexed} tools indexed")

        except Exception as e:
            status.status = ServerStatusEnum.ERROR
            status.last_error = str(e)
            await self._cleanup_client(name, managed)
            raise

    async def call_tool(
        self,
        tool_id: str,
        args: dict[str, Any],
        timeout_ms: int = 30000,
        *,
        task: TaskMetadataInput | dict[str, Any] | None = None,
        trace_context: TraceContextInfo | dict[str, Any] | None = None,
    ) -> Any:
        """Call a tool on a downstream server."""
        tool_info = self._tools.get(tool_id)
        if not tool_info:
            raise ValueError(f"Unknown tool: {tool_id}")

        managed = self._clients.get(tool_info.server_name)
        if (
            not managed
            or (not managed.is_remote and managed.process is None)
            or (managed.is_remote and managed.write_stream is None)
        ):
            raise RuntimeError(f"Server {tool_info.server_name} is not connected")

        if managed.status.status != ServerStatusEnum.ONLINE:
            raise RuntimeError(
                f"Server {tool_info.server_name} is {managed.status.status.value}"
            )

        support = self._tool_task_support(tool_info)
        task_requested = task is not None
        if support == "required":
            task_requested = True
        if task_requested and support == "forbidden":
            raise RuntimeError(f"Tool {tool_id} does not support MCP task execution")
        if task_requested and not self._server_supports_tasks(managed):
            raise RuntimeError(
                f"Server {tool_info.server_name} does not advertise MCP task support"
            )

        params: dict[str, Any] = {"name": tool_info.tool_name, "arguments": args}
        trace_meta = _trace_context_payload(trace_context)
        if trace_meta:
            params["_meta"] = {**params.get("_meta", {}), **trace_meta}
        requestor_context: dict[str, Any] | None = None
        if task_requested:
            parsed_task = (
                task
                if isinstance(task, TaskMetadataInput)
                else TaskMetadataInput.model_validate(task or {})
            )
            if not parsed_task.enabled and support != "required":
                task_requested = False
            else:
                params["task"] = self._task_wire_metadata(parsed_task)
                requestor_context = parsed_task.requestor_context

        # Send tool call with metadata for health monitoring
        result = await self._send_request(
            managed,
            "tools/call",
            params,
            tool_id=tool_id,
            timeout_ms=timeout_ms,
        )
        if task_requested and isinstance(result, dict):
            task_payload = self._extract_task_payload(result)
            if task_payload is not None:
                task_info = self._task_info_from_payload(task_payload)
                if task_info is not None:
                    self._record_task(
                        tool_info.server_name,
                        task_info,
                        tool_id=tool_id,
                        requestor_context=requestor_context,
                    )

        return result

    def _task_client(self, server_name: str) -> ManagedClient:
        managed = self._clients.get(server_name)
        if (
            not managed
            or (not managed.is_remote and managed.process is None)
            or (managed.is_remote and managed.write_stream is None)
        ):
            raise RuntimeError(f"Server {server_name} is not connected")
        if not self._server_supports_tasks(managed):
            raise RuntimeError(
                f"Server {server_name} does not advertise MCP task support"
            )
        return managed

    async def list_tasks(
        self,
        server_name: str | None = None,
        cursor: str | None = None,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Proxy downstream tasks/list and update the transient task registry."""
        servers = [server_name] if server_name else sorted(self._clients)
        all_tasks: list[dict[str, Any]] = []
        next_cursor: str | None = None
        for name in servers:
            managed = self._task_client(name)
            params = self._task_request_params(
                cursor=cursor,
                requestor_context=requestor_context,
            )
            result = await self._send_request(managed, "tasks/list", params)
            for payload in result.get("tasks", []):
                if not isinstance(payload, dict):
                    continue
                task_info = self._task_info_from_payload(payload)
                if task_info is None:
                    continue
                record = self._record_task(name, task_info)
                all_tasks.append(record.model_dump())
            next_cursor = result.get("nextCursor") or result.get("next_cursor")
        return {"tasks": all_tasks, "nextCursor": next_cursor}

    async def get_task(
        self,
        server_name: str,
        task_id: str,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> McpTaskInfo:
        """Proxy downstream tasks/get and update the transient task registry."""
        managed = self._task_client(server_name)
        record = self.get_task_record(server_name, task_id)
        result = await self._send_request(
            managed,
            "tasks/get",
            self._task_request_params(
                task_id=task_id,
                requestor_context=requestor_context
                or (record.requestor_context if record is not None else None),
            ),
        )
        payload = self._extract_task_payload(result) or result
        task_info = self._task_info_from_payload(payload)
        if task_info is None:
            raise KeyError(f"Task not found: {server_name}::{task_id}")
        return self._record_task(server_name, task_info)

    async def get_task_result(
        self,
        server_name: str,
        task_id: str,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Proxy downstream tasks/result and update task metadata when returned."""
        managed = self._task_client(server_name)
        record = self.get_task_record(server_name, task_id)
        result = await self._send_request(
            managed,
            "tasks/result",
            self._task_request_params(
                task_id=task_id,
                requestor_context=requestor_context
                or (record.requestor_context if record is not None else None),
            ),
        )
        task_payload = self._extract_task_payload(result)
        if task_payload is not None:
            task_info = self._task_info_from_payload(task_payload)
            if task_info is not None:
                self._record_task(server_name, task_info)
        else:
            await self.get_task(
                server_name,
                task_id,
                requestor_context=requestor_context
                or (record.requestor_context if record is not None else None),
            )
        return result

    async def cancel_task(
        self,
        server_name: str,
        task_id: str,
        force: bool = False,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> tuple[bool, McpTaskInfo | None, str]:
        """Proxy downstream tasks/cancel with idempotent local terminal handling."""
        record = self.get_task_record(server_name, task_id)
        if record is not None and self._terminal_task(record):
            return (True, record, f"Task is already terminal: {record.status}")
        if record is None:
            return (False, None, f"Task not found: {server_name}::{task_id}")

        managed = self._task_client(server_name)
        params = self._task_request_params(
            task_id=task_id,
            requestor_context=requestor_context or record.requestor_context,
        )
        params["force"] = force
        result = await self._send_request(managed, "tasks/cancel", params)
        payload = self._extract_task_payload(result) or result
        task_info = self._task_info_from_payload(payload)
        if task_info is None:
            task_info = McpTaskInfo(
                task_id=task_id,
                status="cancelled",
                updated_at=time.time(),
                raw=result,
            )
        return (True, self._record_task(server_name, task_info), "Task cancelled")

    async def read_resource(self, resource_id: str, timeout_ms: int = 30000) -> Any:
        """Read a resource from a downstream server."""
        resource_info = self._resources.get(resource_id)
        if not resource_info:
            raise ValueError(f"Unknown resource: {resource_id}")

        managed = self._clients.get(resource_info.server_name)
        if (
            not managed
            or (not managed.is_remote and managed.process is None)
            or (managed.is_remote and managed.write_stream is None)
        ):
            raise RuntimeError(f"Server {resource_info.server_name} is not connected")

        if managed.status.status != ServerStatusEnum.ONLINE:
            raise RuntimeError(
                f"Server {resource_info.server_name} is {managed.status.status.value}"
            )

        result = await self._send_request(
            managed,
            "resources/read",
            {"uri": resource_info.uri},
            timeout_ms=timeout_ms,
        )

        return result

    async def get_prompt(
        self,
        prompt_id: str,
        arguments: dict[str, str] | None = None,
        timeout_ms: int = 30000,
    ) -> Any:
        """Get a prompt from a downstream server."""
        prompt_info = self._prompts.get(prompt_id)
        if not prompt_info:
            raise ValueError(f"Unknown prompt: {prompt_id}")

        managed = self._clients.get(prompt_info.server_name)
        if (
            not managed
            or (not managed.is_remote and managed.process is None)
            or (managed.is_remote and managed.write_stream is None)
        ):
            raise RuntimeError(f"Server {prompt_info.server_name} is not connected")

        if managed.status.status != ServerStatusEnum.ONLINE:
            raise RuntimeError(
                f"Server {prompt_info.server_name} is {managed.status.status.value}"
            )

        params: dict[str, Any] = {"name": prompt_info.name}
        if arguments:
            params["arguments"] = arguments

        result = await self._send_request(
            managed,
            "prompts/get",
            params,
            timeout_ms=timeout_ms,
        )

        return result

    def get_tool(self, tool_id: str) -> ToolInfo | None:
        """Get tool info by ID."""
        return self._tools.get(tool_id)

    def get_all_tools(self) -> list[ToolInfo]:
        """Get all tools."""
        return sorted(self._tools.values(), key=lambda tool: tool.tool_id)

    def get_resource(self, resource_id: str) -> ResourceInfo | None:
        """Get resource info by ID."""
        return self._resources.get(resource_id)

    def get_all_resources(self) -> list[ResourceInfo]:
        """Get all resources."""
        return sorted(
            self._resources.values(), key=lambda resource: resource.resource_id
        )

    def get_prompt_info(self, prompt_id: str) -> PromptInfo | None:
        """Get prompt info by ID."""
        return self._prompts.get(prompt_id)

    def get_all_prompts(self) -> list[PromptInfo]:
        """Get all prompts."""
        return sorted(self._prompts.values(), key=lambda prompt: prompt.prompt_id)

    def get_server_status(self, name: str) -> ServerStatus | None:
        """Get server status."""
        return self._servers.get(name)

    def get_all_server_statuses(self) -> list[ServerStatus]:
        """Get all server statuses."""
        return sorted(self._servers.values(), key=lambda status: status.name)

    def get_registry_meta(self) -> tuple[str, float]:
        """Get registry metadata (revision_id, last_refresh_ts)."""
        return (self._revision_id, self._last_refresh_ts)

    def is_server_online(self, name: str) -> bool:
        """Check if server is online."""
        status = self._servers.get(name)
        return status is not None and status.status == ServerStatusEnum.ONLINE

    # === Health Monitoring Methods ===

    def start_health_monitor(self) -> None:
        """Start the background health monitoring task."""
        if not hasattr(self, "_health_task") or self._health_task is None:
            self._health_task: asyncio.Task[None] | None = self._track_background_task(
                asyncio.create_task(self._health_monitor_loop())
            )
            logger.info("Started health monitor background task")

    def stop_health_monitor(self) -> None:
        """Stop the health monitoring task."""
        if hasattr(self, "_health_task") and self._health_task:
            self._health_task.cancel()
            self._health_task = None
            logger.debug("Stopped health monitor background task")

    async def _health_monitor_loop(self) -> None:
        """Background task to monitor server and request health."""
        last_memory_log = 0.0
        while True:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL)
                now = time.time()

                # Periodic memory logging
                if now - last_memory_log >= MEMORY_LOG_INTERVAL:
                    proc_mem = _get_memory_usage_mb()
                    sys_mem_pct = _get_system_memory_pct()
                    server_count = len(self._clients)

                    # Count child processes
                    child_count = 0
                    for managed in self._clients.values():
                        if managed.process and managed.process.returncode is None:
                            child_count += 1

                    log_msg = (
                        f"[TELEMETRY] pmcp: {proc_mem:.1f}MB | "
                        f"system: {sys_mem_pct}% | "
                        f"servers: {server_count} ({child_count} alive)"
                    )

                    if proc_mem > MEMORY_WARN_THRESHOLD_MB:
                        logger.warning(f"{log_msg} - HIGH MEMORY")
                    elif sys_mem_pct > 80:
                        logger.warning(f"{log_msg} - SYSTEM MEMORY HIGH")
                    else:
                        logger.info(log_msg)

                    last_memory_log = now

                for name, managed in self._clients.items():
                    if not self._check_server_health(name, managed):
                        continue

                    # Check for stalled requests
                    for req_id, pending in list(managed.pending_requests.items()):
                        elapsed_since_heartbeat = now - pending.last_heartbeat

                        if elapsed_since_heartbeat > HEARTBEAT_STALL_THRESHOLD:
                            logger.warning(
                                f"Request {name}::{req_id} stalled "
                                f"(no heartbeat for {elapsed_since_heartbeat:.0f}s)"
                            )
                        elif elapsed_since_heartbeat > HEARTBEAT_WARN_THRESHOLD:
                            logger.info(
                                f"Request {name}::{req_id} slow "
                                f"(no heartbeat for {elapsed_since_heartbeat:.0f}s)"
                            )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Health monitor error: {e}")

    def _check_server_health(self, name: str, managed: ManagedClient) -> bool:
        """Check server transport health, preserving status error strings."""
        if managed.is_remote:
            if managed.read_task and managed.read_task.done():
                if managed.status.status != ServerStatusEnum.ERROR:
                    logger.warning(f"Server {name} remote stream disconnected")
                    managed.status.status = ServerStatusEnum.ERROR
                    managed.status.last_error = "Remote stream disconnected"
                return False

            if managed.write_stream is None:
                if managed.status.status != ServerStatusEnum.ERROR:
                    managed.status.status = ServerStatusEnum.ERROR
                    managed.status.last_error = "Remote stream unavailable"
                return False

            return True

        if managed.process:
            returncode = managed.process.returncode
            if returncode is not None:
                logger.warning(f"Server {name} process exited with code {returncode}")
                managed.status.status = ServerStatusEnum.ERROR
                managed.status.last_error = f"Process exited: {returncode}"
                return False

        return True

    def get_pending_requests(self, server: str | None = None) -> list[PendingRequest]:
        """Get all pending requests, optionally filtered by server."""
        result: list[PendingRequest] = []
        for name, managed in sorted(self._clients.items()):
            if server and name != server:
                continue
            result.extend(list(managed.pending_requests.values()))
        return sorted(
            result, key=lambda pending: (pending.server_name, pending.request_id)
        )

    def cancel_all_pending_requests(self) -> int:
        """Cancel all pending requests and return the number newly cancelled."""
        cancelled = 0
        for _, managed in list(self._clients.items()):
            for request_id, pending in list(managed.pending_requests.items()):
                if not pending.future.done():
                    pending.future.cancel()
                    cancelled += 1
                managed.pending_requests.pop(request_id, None)
            managed.status.pending_request_count = len(managed.pending_requests)
        if cancelled:
            logger.warning(f"Force-cancelled {cancelled} pending requests")
        return cancelled

    def get_request_state(self, pending: PendingRequest) -> RequestState:
        """Determine current state of a pending request."""
        now = time.time()
        elapsed = now - pending.started_at
        heartbeat_age = now - pending.last_heartbeat

        if pending.future.done():
            if pending.future.cancelled():
                return RequestState.CANCELLED
            return RequestState.COMPLETED
        if elapsed * 1000 > pending.timeout_ms:
            return RequestState.TIMEOUT
        if heartbeat_age > HEARTBEAT_STALL_THRESHOLD:
            return RequestState.STALLED
        if heartbeat_age > HEARTBEAT_WARN_THRESHOLD:
            return RequestState.ACTIVE  # Still active but slow
        return RequestState.PENDING

    async def cancel_request(
        self, request_id: str, force: bool = False
    ) -> tuple[str, str, bool, float | None]:
        """
        Cancel a pending request.

        Args:
            request_id: Format "server_name::local_id"
            force: Force cancel even if heartbeat is recent

        Returns:
            (status, message, was_stalled, elapsed_seconds)
            - status: "cancelled", "not_found", "already_complete", "refused"
        """
        # Parse request_id format "server_name::local_id"
        if "::" not in request_id:
            return (
                "not_found",
                f"Invalid request_id format: {request_id}",
                False,
                None,
            )

        server_name, local_id_str = request_id.rsplit("::", 1)
        try:
            local_id = int(local_id_str)
        except ValueError:
            return ("not_found", f"Invalid local_id: {local_id_str}", False, None)

        managed = self._clients.get(server_name)
        if not managed:
            return ("not_found", f"Server not found: {server_name}", False, None)

        pending = managed.pending_requests.get(local_id)
        if not pending:
            return ("not_found", f"Request not found: {request_id}", False, None)

        if pending.future.done():
            return ("already_complete", "Request already completed", False, None)

        now = time.time()
        elapsed = now - pending.started_at
        heartbeat_age = now - pending.last_heartbeat
        was_stalled = heartbeat_age > HEARTBEAT_STALL_THRESHOLD

        # Safety check: refuse to cancel healthy long-running requests unless forced
        if not force and not was_stalled and elapsed < pending.timeout_ms / 1000:
            return (
                "refused",
                f"Request is healthy (heartbeat {heartbeat_age:.0f}s ago). "
                f"Use force=true to cancel anyway.",
                False,
                elapsed,
            )

        # Cancel the request
        pending.future.cancel()
        managed.pending_requests.pop(local_id, None)
        managed.status.pending_request_count = len(managed.pending_requests)
        logger.info(
            f"Cancelled request {request_id} (stalled={was_stalled}, elapsed={elapsed:.1f}s)"
        )

        return ("cancelled", "Request cancelled successfully", was_stalled, elapsed)
