"""Tests for gateway tools."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
import time
from typing import Any, cast
import types
from pathlib import Path

import pytest

from pmcp.env_store import read_env_file
from pmcp.manifest.environment import CLIInfo
from pmcp.manifest.loader import CLIAlternative, Manifest, ServerConfig
from pmcp.manifest.registry import (
    RegistryCache,
    RegistryPackage,
    RegistryRemote,
    RegistryServerEntry,
)
from pmcp.config.guidance import GuidanceConfig
from pmcp.config.loader import StartupObservation, manifest_server_to_config
from pmcp.errors import GatewayException
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import (
    GatewayTools,
    _refresh_config_unchanged,
    get_gateway_tool_definitions,
)
from pmcp.types import (
    CLIHint,
    CLIResolution,
    CatalogSearchOutput,
    DescriptionsCache,
    GeneratedServerDescriptions,
    LocalMcpServerConfig,
    McpTaskRecord,
    PrebuiltToolInfo,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    RequestState,
    RiskHint,
    ServerStatus,
    ServerStatusEnum,
    ToolInfo,
)


def test_cli_hint_serializes_compact_contract() -> None:
    hint = CLIHint(
        name="git",
        description="Git version control CLI",
        available=True,
        path="/usr/bin/git",
        check_command=["git", "--version"],
        help_command=["git", "--help"],
        examples=["git status --short", "git log --oneline -5"],
        prefer_mcp_for=["github issues"],
        reason="Available on PATH",
    )

    dumped = hint.model_dump(exclude_none=True)

    assert dumped == {
        "name": "git",
        "description": "Git version control CLI",
        "available": True,
        "path": "/usr/bin/git",
        "check_command": ["git", "--version"],
        "help_command": ["git", "--help"],
        "examples": ["git status --short", "git log --oneline -5"],
        "prefer_mcp_for": ["github issues"],
        "reason": "Available on PATH",
    }
    assert "help_output" not in dumped


def test_cli_resolution_legacy_shape_remains_valid() -> None:
    resolution = CLIResolution(
        name="git",
        path="/usr/bin/git",
        help_output="usage: git ...",
        examples=["git status --short"],
    )

    assert resolution.model_dump() == {
        "name": "git",
        "path": "/usr/bin/git",
        "description": None,
        "available": None,
        "check_command": None,
        "help_command": None,
        "help_output": "usage: git ...",
        "examples": ["git status --short"],
        "prefer_mcp_for": None,
        "reason": None,
    }


class MockClientManager:
    """Mock client manager for testing."""

    def __init__(self, tools: list[ToolInfo] | None = None) -> None:
        self._tools = {t.tool_id: t for t in (tools or [])}
        self._online_servers: set[str] = set()
        self._lazy_servers: set[str] = set()
        self._server_statuses: list[ServerStatus] = []
        self._revision_id = "test-rev"
        self._last_refresh_ts = 1234567890.0
        self.connected_configs: list[Any] = []
        self.connected_config_map: dict[str, Any] = {}
        self.connected_resolved_headers: dict[str, dict[str, str]] = {}
        self.refreshed_configs: list[Any] = []
        self.lazy_configs: list[Any] = []
        self.disconnected = False
        self.events: list[str] = []
        self.ensure_connected_calls: list[str] = []
        self.pending_requests: list[Any] = []
        self.tasks: dict[tuple[str, str], Any] = {}
        self.list_task_calls: list[str | None] = []
        self.task_request_contexts: list[dict[str, Any] | None] = []
        self.last_call_task: Any = None
        self.last_call_trace_context: Any = None

    def get_all_tools(self) -> list[ToolInfo]:
        return list(self._tools.values())

    def get_tool(self, tool_id: str) -> ToolInfo | None:
        return self._tools.get(tool_id)

    def is_server_online(self, name: str) -> bool:
        return name in self._online_servers

    def is_lazy_server(self, name: str) -> bool:
        return name in self._lazy_servers

    def get_lazy_server_names(self) -> list[str]:
        return list(self._lazy_servers)

    def add_lazy_server(self, name: str) -> None:
        self._lazy_servers.add(name)

    def set_server_online(self, name: str) -> None:
        self._online_servers.add(name)

    async def ensure_connected(self, server_name: str) -> bool:
        self.ensure_connected_calls.append(server_name)
        if server_name in self._online_servers:
            return True
        if server_name in self._lazy_servers:
            self._lazy_servers.remove(server_name)
            self._online_servers.add(server_name)
            return True
        raise ValueError(f"Unknown server: {server_name}")

    def get_all_server_statuses(self) -> list[Any]:
        return self._server_statuses

    def get_server_status(self, name: str) -> ServerStatus | None:
        for status in self._server_statuses:
            if status.name == name:
                return status
        if name in self._online_servers:
            return ServerStatus(name=name, status=ServerStatusEnum.ONLINE, tool_count=0)
        if name in self._lazy_servers:
            return ServerStatus(name=name, status=ServerStatusEnum.LAZY, tool_count=0)
        return None

    def set_server_statuses(self, statuses: list[ServerStatus]) -> None:
        self._server_statuses = statuses

    def get_registry_meta(self) -> tuple[str, float]:
        return (self._revision_id, self._last_refresh_ts)

    async def call_tool(
        self,
        tool_id: str,
        args: dict[str, Any],
        timeout_ms: int,
        *,
        task: Any = None,
        trace_context: Any = None,
    ) -> Any:
        self.last_call_task = task
        self.last_call_trace_context = trace_context
        if task is not None:
            task_record = McpTaskRecord(
                task_id="task-1",
                status="working",
                status_message="queued by SDK host",
                created_at="2026-01-02T03:04:05Z",
                updated_at="2026-01-02T03:04:06Z",
                ttl=300,
                poll_interval=2.5,
                raw={
                    "taskId": "task-1",
                    "status": "working",
                    "statusMessage": "queued by SDK host",
                    "createdAt": "2026-01-02T03:04:05Z",
                    "lastUpdatedAt": "2026-01-02T03:04:06Z",
                    "ttl": 300,
                    "pollInterval": 2.5,
                    "metadata": {"unknown": "kept"},
                },
                server_name=tool_id.split("::", 1)[0],
                tool_id=tool_id,
            )
            self.tasks[(task_record.server_name, task_record.task_id)] = task_record
            return {"task": {"taskId": "task-1", "status": "working"}}
        return {"content": [{"type": "text", "text": "result"}]}

    def get_task_record(self, server_name: str, task_id: str) -> Any:
        return self.tasks.get((server_name, task_id))

    def get_active_tasks(self, server_name: str | None = None) -> list[Any]:
        return [
            task
            for (server, _), task in self.tasks.items()
            if (server_name is None or server == server_name)
            and task.status not in {"completed", "failed", "cancelled"}
        ]

    async def cancel_active_tasks(
        self, server_name: str | None = None
    ) -> tuple[int, list[str]]:
        active = self.get_active_tasks(server_name)
        for task in active:
            task.status = "cancelled"
        return (len(active), [])

    async def list_tasks(
        self,
        server_name: str | None = None,
        cursor: str | None = None,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.list_task_calls.append(server_name)
        self.task_request_contexts.append(requestor_context)
        return {
            "tasks": [
                {
                    "task_id": task.task_id,
                    "status": task.status,
                    "status_message": task.status_message,
                    "created_at": task.created_at,
                    "updated_at": task.updated_at,
                    "ttl": task.ttl,
                    "poll_interval": task.poll_interval,
                    "server_name": task.server_name,
                    "raw": task.raw,
                }
                for task in self.tasks.values()
                if server_name is None or task.server_name == server_name
            ],
            "nextCursor": cursor,
        }

    async def get_task(
        self,
        server_name: str,
        task_id: str,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> Any:
        self.task_request_contexts.append(requestor_context)
        task = self.tasks.get((server_name, task_id))
        if task is None:
            raise KeyError(task_id)
        return task

    async def get_task_result(
        self,
        server_name: str,
        task_id: str,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.task_request_contexts.append(requestor_context)
        task = self.tasks.get((server_name, task_id))
        if task is None:
            raise KeyError(task_id)
        task.status = "completed"
        return {"result": "api_key=sk-secret", "task": task.raw}

    async def cancel_task(
        self,
        server_name: str,
        task_id: str,
        force: bool = False,
        *,
        requestor_context: dict[str, Any] | None = None,
    ) -> tuple[bool, Any, str]:
        self.task_request_contexts.append(requestor_context)
        task = self.tasks.get((server_name, task_id))
        if task is None:
            return (False, None, "Task not found")
        if task.status in {"completed", "failed", "cancelled"}:
            return (True, task, f"Task is already terminal: {task.status}")
        task.status = "cancelled"
        return (True, task, "Task cancelled")

    async def refresh(self, configs: list[Any]) -> list[str]:
        self.refreshed_configs = list(configs)
        return []

    def get_connected_configs(self) -> dict[str, Any]:
        return dict(self.connected_config_map)

    def get_connected_resolved_headers(self, name: str) -> dict[str, str] | None:
        return self.connected_resolved_headers.get(name)

    def add_connected_server(
        self,
        config: Any,
        *,
        online: bool = True,
        resolved_headers: dict[str, str] | None = None,
    ) -> None:
        """Pre-populate an already-connected server (test helper).

        ``online=False`` models a crashed server still present in ``_clients``
        with ERROR status (its reconnect attempts exhausted) — it appears in
        get_connected_configs() but is NOT ONLINE. ``resolved_headers`` records
        the auth headers the server was connected with (placeholders resolved).
        """
        self.connected_config_map[config.name] = config
        if resolved_headers is not None:
            self.connected_resolved_headers[config.name] = resolved_headers
        self._server_statuses = [
            status for status in self._server_statuses if status.name != config.name
        ]
        if online:
            self._online_servers.add(config.name)
            self._server_statuses.append(
                ServerStatus(
                    name=config.name, status=ServerStatusEnum.ONLINE, tool_count=0
                )
            )
        else:
            self._online_servers.discard(config.name)
            self._server_statuses.append(
                ServerStatus(
                    name=config.name, status=ServerStatusEnum.ERROR, tool_count=0
                )
            )

    async def disconnect_all(self) -> None:
        self.events.append("disconnect")
        self.disconnected = True
        self.connected_config_map.clear()

    def register_lazy_configs(self, configs: list[Any]) -> None:
        self.events.append("register_lazy")
        self.lazy_configs = list(configs)
        for config in configs:
            self._lazy_servers.add(config.name)

    def prune_lazy_configs(self, keep_names: set[str]) -> None:
        self.events.append("prune_lazy")
        self.lazy_configs = [c for c in self.lazy_configs if c.name in keep_names]
        for name in list(self._lazy_servers):
            if name not in keep_names:
                self._lazy_servers.discard(name)
                self._server_statuses = [
                    s
                    for s in self._server_statuses
                    if not (s.name == name and s.status == ServerStatusEnum.LAZY)
                ]

    async def connect_all(self, configs: list[Any], retry: bool = True) -> list[str]:
        self.events.append("connect")
        self.connected_configs.extend(configs)
        for config in configs:
            self._online_servers.add(config.name)
            self.connected_config_map[config.name] = config
        return []

    async def connect_server(self, config: Any, retry: bool = True) -> list[str]:
        self.events.append(f"connect_server:{config.name}")
        self.connected_configs.append(config)
        if config.name == "fails-connect":
            return [f"Failed to connect to {config.name}: boom"]
        self._online_servers.add(config.name)
        self.connected_config_map[config.name] = config
        self._lazy_servers.discard(config.name)
        self._server_statuses = [
            status for status in self._server_statuses if status.name != config.name
        ]
        self._server_statuses.append(
            ServerStatus(name=config.name, status=ServerStatusEnum.ONLINE, tool_count=0)
        )
        return []

    async def disconnect_server(
        self, name: str, force: bool = False
    ) -> tuple[bool, int, str | None]:
        self.events.append(f"disconnect_server:{name}:{force}")
        pending = self.get_pending_requests(name)
        if pending and not force:
            return (
                False,
                0,
                "Disconnect refused because this server has pending requests. "
                "Use force=true to cancel them.",
            )
        cancelled = self.cancel_pending_requests(name) if pending else 0
        self._online_servers.discard(name)
        self.connected_config_map.pop(name, None)
        self._lazy_servers.add(name)
        self._server_statuses = [
            status for status in self._server_statuses if status.name != name
        ]
        self._server_statuses.append(
            ServerStatus(name=name, status=ServerStatusEnum.LAZY, tool_count=0)
        )
        return (True, cancelled, None)

    async def restart_server(
        self, config: Any, force: bool = False
    ) -> tuple[bool, int, list[str]]:
        self.events.append(f"restart_server:{config.name}:{force}")
        disconnected, cancelled, error = await self.disconnect_server(
            config.name, force=force
        )
        if not disconnected:
            return (False, cancelled, [error or "Restart refused."])
        errors = await self.connect_server(config)
        return (not errors, cancelled, errors)

    def add_pending_request(self, server_name: str = "server") -> Any:
        request_id = len(self.pending_requests) + 1
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        request = types.SimpleNamespace(
            request_id=request_id,
            server_name=server_name,
            tool_id=f"{server_name}::tool",
            started_at=time.time(),
            last_heartbeat=time.time(),
            timeout_ms=30000,
            future=future,
        )
        self.pending_requests.append(request)
        return request

    def get_pending_requests(self, server: str | None = None) -> list[Any]:
        if server is None:
            return list(self.pending_requests)
        return [
            request
            for request in self.pending_requests
            if request.server_name == server
        ]

    def get_request_state(self, request: Any) -> RequestState:
        if request.future.cancelled():
            return RequestState.CANCELLED
        if request.future.done():
            return RequestState.COMPLETED
        return RequestState.PENDING

    def cancel_all_pending_requests(self) -> int:
        self.events.append("cancel_all")
        cancelled = 0
        for request in list(self.pending_requests):
            if not request.future.done():
                request.future.cancel()
                cancelled += 1
        self.pending_requests = []
        return cancelled

    def cancel_pending_requests(self, server: str) -> int:
        self.events.append(f"cancel_pending:{server}")
        cancelled = 0
        remaining = []
        for request in self.pending_requests:
            if request.server_name == server:
                if not request.future.done():
                    request.future.cancel()
                    cancelled += 1
            else:
                remaining.append(request)
        self.pending_requests = remaining
        return cancelled


def create_manifest_for_request_tests() -> Manifest:
    return Manifest(
        version="1.0",
        cli_alternatives={
            "git": CLIAlternative(
                name="git",
                keywords=["git", "version control"],
                check_command=["git", "--version"],
                help_command=["git", "--help"],
                description="Git CLI",
                examples=["git status --short", "git log --oneline -5"],
                prefer_mcp_for=["github issues", "pull requests"],
            )
        },
        servers={
            "playwright": ServerConfig(
                name="playwright",
                description="Browser automation",
                keywords=["browser", "automation", "playwright"],
                install={},
                command="npx",
                args=["@playwright/mcp"],
                requires_api_key=False,
            )
        },
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )


def create_git_collision_manifest() -> Manifest:
    manifest = create_manifest_for_request_tests()
    manifest.servers["git"] = ServerConfig(
        name="git",
        description="Git MCP server",
        keywords=["git", "commits", "repository"],
        install={},
        command="npx",
        args=["@cyanheads/git-mcp-server"],
        requires_api_key=False,
    )
    return manifest


def create_git_and_github_manifest() -> Manifest:
    manifest = create_manifest_for_request_tests()
    manifest.servers["github"] = ServerConfig(
        name="github",
        description="GitHub issues and pull request management",
        keywords=["github", "issues", "pull requests"],
        install={},
        command="npx",
        args=["@modelcontextprotocol/server-github"],
        requires_api_key=True,
        env_var="GITHUB_PERSONAL_ACCESS_TOKEN",
    )
    return manifest


def tenant_code_mode_config() -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name="tenant-code-mode",
        source="project",
        config=RemoteMcpServerConfig(
            type="streamable-http",
            url="https://tenant.example/mcp",
            headers={"Authorization": "Bearer ${TENANT_CODE_MODE_MCP_TOKEN}"},
        ),
    )


def create_mock_tools() -> list[ToolInfo]:
    """Create mock tools for testing."""
    return [
        ToolInfo(
            tool_id="github::create_issue",
            server_name="github",
            tool_name="create_issue",
            description="Create a new issue in a GitHub repository",
            short_description="Create a new issue in a GitHub repository",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Issue title"},
                    "body": {"type": "string", "description": "Issue body"},
                },
                "required": ["title"],
            },
            tags=["github", "git"],
            risk_hint=RiskHint.HIGH,
        ),
        ToolInfo(
            tool_id="github::list_issues",
            server_name="github",
            tool_name="list_issues",
            description="List issues in a repository",
            short_description="List issues in a repository",
            input_schema={"type": "object", "properties": {}},
            tags=["github", "git", "search"],
            risk_hint=RiskHint.LOW,
        ),
        ToolInfo(
            tool_id="jira::search_issues",
            server_name="jira",
            tool_name="search_issues",
            description="Search for Jira issues using JQL",
            short_description="Search for Jira issues using JQL",
            input_schema={"type": "object", "properties": {}},
            tags=["jira", "search"],
            risk_hint=RiskHint.LOW,
        ),
    ]


def patch_refresh_config_sources(
    monkeypatch: pytest.MonkeyPatch,
    configured: list[ResolvedServerConfig],
) -> None:
    manifest = Manifest(
        version="1.0",
        cli_alternatives={},
        servers={},
        discovery_queue_path=".mcp-gateway/discovery_queue.json",
    )
    monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)
    monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_enabled_auto_start",
        lambda **_: set(),
    )
    monkeypatch.setattr(
        "pmcp.tools.handlers.load_disabled_auto_start",
        lambda **_: set(),
    )


class TestRefreshCompatibility:
    @pytest.mark.asyncio
    async def test_refresh_registers_configured_manifest_and_provisioned_lazy_by_default(
        self, monkeypatch
    ) -> None:
        client_manager = MockClientManager()
        policy_manager = PolicyManager()
        policy_manager.is_server_allowed = lambda name: name != "denied"  # type: ignore[method-assign]
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        configured = [
            ResolvedServerConfig(
                name="configured",
                source="project",
                config=LocalMcpServerConfig(command="configured-cmd"),
            ),
            ResolvedServerConfig(
                name="denied",
                source="project",
                config=LocalMcpServerConfig(command="denied-cmd"),
            ),
        ]
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "legacy-auto": ServerConfig(
                    name="legacy-auto",
                    description="Legacy auto-start",
                    keywords=["legacy"],
                    install={},
                    command="legacy-cmd",
                    args=[],
                    auto_start=True,
                ),
                "provisioned": ServerConfig(
                    name="provisioned",
                    description="Provisioned",
                    keywords=["provisioned"],
                    install={},
                    command="provisioned-cmd",
                    args=[],
                ),
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_disabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            gateway_tools,
            "_load_provisioned_registry",
            lambda: {"provisioned": None},
        )

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        # Diff-based refresh no longer tears everything down; nothing was
        # connected and the resolution is all-lazy, so no disconnect happens.
        assert client_manager.disconnected is False
        assert client_manager.refreshed_configs == []
        assert [config.name for config in client_manager.lazy_configs] == [
            "configured",
            "legacy-auto",
            "provisioned",
        ]
        assert client_manager.connected_configs == []
        assert result.servers_seen == 4
        assert result.pending_requests_seen == 0
        assert result.pending_requests_cancelled == 0
        assert result.pending_requests_refused == 0
        assert result.pending_requests_remaining == 0

    @pytest.mark.asyncio
    async def test_refresh_connects_only_configured_auto_start(
        self, monkeypatch
    ) -> None:
        client_manager = MockClientManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        configured = [
            ResolvedServerConfig(
                name="configured",
                source="project",
                config=LocalMcpServerConfig(command="configured-cmd"),
            ),
            ResolvedServerConfig(
                name="lazy",
                source="project",
                config=LocalMcpServerConfig(command="lazy-cmd"),
            ),
        ]
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={},
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: {"configured"},
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_disabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert [config.name for config in client_manager.lazy_configs] == ["lazy"]
        assert [config.name for config in client_manager.connected_configs] == [
            "configured"
        ]

    @pytest.mark.asyncio
    async def test_refresh_legacy_manifest_auto_start_env_connects_manifest(
        self, monkeypatch
    ) -> None:
        client_manager = MockClientManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "legacy-auto": ServerConfig(
                    name="legacy-auto",
                    description="Legacy auto-start",
                    keywords=["legacy"],
                    install={},
                    command="legacy-cmd",
                    args=[],
                    auto_start=True,
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setenv("PMCP_LEGACY_MANIFEST_AUTOSTART", "1")
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_disabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert client_manager.lazy_configs == []
        assert [config.name for config in client_manager.connected_configs] == [
            "legacy-auto"
        ]

    @pytest.mark.asyncio
    async def test_refresh_provisioned_servers_remain_lazy_unless_auto_start(
        self, monkeypatch
    ) -> None:
        client_manager = MockClientManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "provisioned": ServerConfig(
                    name="provisioned",
                    description="Provisioned",
                    keywords=["provisioned"],
                    install={},
                    command="provisioned-cmd",
                    args=[],
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_disabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            gateway_tools,
            "_load_provisioned_registry",
            lambda: {"provisioned": None},
        )

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert [config.name for config in client_manager.lazy_configs] == [
            "provisioned"
        ]
        assert client_manager.connected_configs == []

    @pytest.mark.asyncio
    async def test_refresh_keeps_unchanged_lazy_server_online(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A connected server that resolves as lazy must stay online (issue #79).

        This is the discriminating case: a lazily-started server (e.g. a
        provisioned browser put into _clients by ensure_connected) resolves as
        lazy on refresh. The diff must keep it, not tear it down.
        """
        client_manager = MockClientManager()
        config = ResolvedServerConfig(
            name="browser",
            source="project",
            config=LocalMcpServerConfig(command="browser-cmd"),
        )
        client_manager.add_connected_server(config)
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        # No auto_start -> "browser" resolves as lazy on this refresh.
        patch_refresh_config_sources(monkeypatch, [config])
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert "disconnect_server:browser:True" not in client_manager.events
        assert "connect" not in client_manager.events
        assert client_manager.is_server_online("browser") is True
        assert result.servers_online == 1
        assert [c.name for c in client_manager.lazy_configs] == ["browser"]

    @pytest.mark.asyncio
    async def test_refresh_keeps_adopted_provisioned_server_online(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A runtime-adopted provisioned server must survive refresh (issue #79).

        This is the scenario that produced "105 seen, 0 online": the adopted
        config stores env resolved from os.environ
        (manifest_server_to_config), while the loader resolves the same server
        with env=None. Full-model equality would differ and tear it down; the
        diff must treat them as the same process and keep it.
        """
        monkeypatch.setenv("PROV_KEY", "secret-value")
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "prov": ServerConfig(
                    name="prov",
                    description="Provisioned w/ api key",
                    keywords=["prov"],
                    install={},
                    command="prov-cmd",
                    args=[],
                    requires_api_key=True,
                    env_var="PROV_KEY",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        client_manager = MockClientManager()
        # Connected config carries env resolved from os.environ (adopt path).
        adopted = manifest_server_to_config(manifest.servers["prov"])
        assert adopted.config.env == {"PROV_KEY": "secret-value"}
        client_manager.add_connected_server(adopted)
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start", lambda **_: set()
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_disabled_auto_start", lambda **_: set()
        )
        monkeypatch.setattr(
            gateway_tools,
            "_load_provisioned_registry",
            lambda: {"prov": "PROV_KEY"},
        )

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert "disconnect_server:prov:True" not in client_manager.events
        assert "connect" not in client_manager.events
        assert client_manager.is_server_online("prov") is True
        assert result.servers_online == 1

    @pytest.mark.asyncio
    async def test_refresh_keeps_unchanged_eager_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unchanged eager server is left running, not respawned."""
        client_manager = MockClientManager()
        config = ResolvedServerConfig(
            name="eager",
            source="project",
            config=LocalMcpServerConfig(command="eager-cmd"),
        )
        client_manager.add_connected_server(config)
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        patch_refresh_config_sources(monkeypatch, [config])
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: {"eager"},
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert "disconnect_server:eager:True" not in client_manager.events
        assert "connect" not in client_manager.events
        assert client_manager.is_server_online("eager") is True
        assert result.servers_online == 1

    @pytest.mark.asyncio
    async def test_refresh_disconnects_removed_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A server removed from config is disconnected on refresh."""
        client_manager = MockClientManager()
        client_manager.add_connected_server(
            ResolvedServerConfig(
                name="gone",
                source="project",
                config=LocalMcpServerConfig(command="gone-cmd"),
            )
        )
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        patch_refresh_config_sources(monkeypatch, [])
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert "disconnect_server:gone:True" in client_manager.events
        assert client_manager.is_server_online("gone") is False

    @pytest.mark.asyncio
    async def test_refresh_reconnects_changed_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A server whose config changed is disconnected and reconnected."""
        client_manager = MockClientManager()
        client_manager.add_connected_server(
            ResolvedServerConfig(
                name="srv",
                source="project",
                config=LocalMcpServerConfig(command="old-cmd"),
            )
        )
        new_config = ResolvedServerConfig(
            name="srv",
            source="project",
            config=LocalMcpServerConfig(command="new-cmd"),
        )
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        patch_refresh_config_sources(monkeypatch, [new_config])
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: {"srv"},
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert "disconnect_server:srv:True" in client_manager.events
        assert [c.name for c in client_manager.connected_configs] == ["srv"]
        assert client_manager.connected_config_map["srv"].config.command == "new-cmd"
        assert client_manager.is_server_online("srv") is True

    @pytest.mark.asyncio
    async def test_refresh_heals_crashed_eager_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A present-but-not-ONLINE eager server (crashed, reconnect exhausted) is
        torn down and reconnected by refresh — the recovery path must not regress."""
        client_manager = MockClientManager()
        config = ResolvedServerConfig(
            name="eager",
            source="project",
            config=LocalMcpServerConfig(command="eager-cmd"),
        )
        # In _clients with ERROR status (crashed), NOT online.
        client_manager.add_connected_server(config, online=False)
        assert client_manager.is_server_online("eager") is False
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        patch_refresh_config_sources(monkeypatch, [config])
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: {"eager"},
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        # Stale entry cleared, then reconnected.
        assert "disconnect_server:eager:True" in client_manager.events
        assert "eager" in [c.name for c in client_manager.connected_configs]
        assert client_manager.is_server_online("eager") is True

    @pytest.mark.asyncio
    async def test_refresh_prunes_removed_lazy_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A previously-lazy server that is no longer in the resolved set is pruned
        from the lazy registry so it can't be lazily started after refresh."""
        client_manager = MockClientManager()
        client_manager.add_lazy_server("stale")
        assert "stale" in client_manager.get_lazy_server_names()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        patch_refresh_config_sources(monkeypatch, [])
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is True
        assert "stale" not in client_manager.get_lazy_server_names()

    def test_refresh_config_unchanged_detects_remote_token_rotation(self) -> None:
        """_refresh_config_unchanged compares the connect-time RESOLVED headers
        against a fresh resolution, so a rotated env-store token (raw config
        unchanged) is detected as CHANGED — guarding against stale/revoked auth
        surviving a refresh (issue #79, codex+gemini finding)."""
        from pmcp.tools.handlers import _refresh_config_unchanged

        cfg = ResolvedServerConfig(
            name="remote",
            source="project",
            config=RemoteMcpServerConfig(
                type="streamable-http",
                url="https://example/mcp",
                headers={"Authorization": "Bearer ${TOKEN}"},
            ),
        )

        # Env store now resolves TOKEN to "new-secret".
        def lookup(var: str) -> str | None:
            return "new-secret" if var == "TOKEN" else None

        # Connected with the OLD secret -> rotation must read as CHANGED.
        assert (
            _refresh_config_unchanged(
                cfg,
                cfg,
                header_env_lookup=lookup,
                old_resolved_headers={"Authorization": "Bearer old-secret"},
            )
            is False
        )
        # Same secret as connect time -> unchanged, keep connected.
        assert (
            _refresh_config_unchanged(
                cfg,
                cfg,
                header_env_lookup=lookup,
                old_resolved_headers={"Authorization": "Bearer new-secret"},
            )
            is True
        )

    @pytest.mark.asyncio
    async def test_refresh_refuses_pending_requests_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        client_manager.add_pending_request("active")
        client_manager.set_server_statuses(
            [
                ServerStatus(
                    name="active",
                    status=ServerStatusEnum.ONLINE,
                    tool_count=1,
                )
            ]
        )
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        gateway_tools.set_startup_observations(
            {
                "stale": StartupObservation(
                    name="stale",
                    startup_policy="skipped",
                    startup_source="auto_start",
                )
            }
        )
        patch_refresh_config_sources(
            monkeypatch,
            [
                ResolvedServerConfig(
                    name="fresh",
                    source="project",
                    config=LocalMcpServerConfig(command="fresh-cmd"),
                )
            ],
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test"})

        assert result.ok is False
        assert result.pending_requests_seen == 1
        assert result.pending_requests_refused == 1
        assert result.pending_requests_remaining == 1
        assert "force=true" in (result.errors or [""])[0]
        assert client_manager.disconnected is False
        assert client_manager.lazy_configs == []
        assert client_manager.connected_configs == []
        assert "stale" in gateway_tools._startup_observations
        assert "fresh" not in gateway_tools._startup_observations

    @pytest.mark.asyncio
    async def test_refresh_force_cancels_before_disconnect_and_reconnect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        request = client_manager.add_pending_request("active")
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        configured = [
            ResolvedServerConfig(
                name="eager",
                source="project",
                config=LocalMcpServerConfig(command="eager-cmd"),
            )
        ]
        patch_refresh_config_sources(monkeypatch, configured)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: {"eager"},
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        result = await gateway_tools.refresh({"reason": "test", "force": True})

        assert result.ok is True
        assert request.future.cancelled()
        assert result.pending_requests_seen == 1
        assert result.pending_requests_cancelled == 1
        assert result.pending_requests_remaining == 0
        # Diff-based refresh: force still cancels pending first, then (nothing
        # was connected so no per-server disconnect) registers lazy and connects
        # the newly-eager server.
        assert client_manager.events == [
            "cancel_all",
            "register_lazy",
            "prune_lazy",
            "connect",
        ]

    @pytest.mark.asyncio
    async def test_refresh_schema_force_is_optional(self) -> None:
        refresh_tool = next(
            tool
            for tool in get_gateway_tool_definitions()
            if tool.name == "gateway.refresh"
        )

        schema = refresh_tool.input_schema
        assert "force" in schema["properties"]
        assert schema["properties"]["force"]["type"] == "boolean"
        assert schema.get("required", []) == []

    @pytest.mark.asyncio
    async def test_list_pending_survives_refused_refresh_and_clears_after_force(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        client_manager.add_pending_request("active")
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        patch_refresh_config_sources(monkeypatch, [])
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        before = await gateway_tools.list_pending({})
        refused = await gateway_tools.refresh({"reason": "test"})
        after_refused = await gateway_tools.list_pending({})
        forced = await gateway_tools.refresh({"reason": "test", "force": True})
        after_forced = await gateway_tools.list_pending({})

        assert before.total_pending == 1
        assert refused.ok is False
        assert after_refused.total_pending == 1
        assert forced.ok is True
        assert forced.pending_requests_cancelled == 1
        assert after_forced.total_pending == 0

    @pytest.mark.asyncio
    async def test_refresh_refuses_pending_lazy_start_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        pending = client_manager.add_pending_request("lazy")
        pending.tool_id = ""
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        patch_refresh_config_sources(monkeypatch, [])
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        refused = await gateway_tools.refresh({"reason": "lazy start race"})
        forced = await gateway_tools.refresh(
            {"reason": "lazy start race", "force": True}
        )

        assert refused.ok is False
        assert refused.pending_requests_refused == 1
        # Diff-based refresh with no configured/eager servers: the forced refresh
        # cancels pending then re-registers and prunes lazy configs (nothing to
        # disconnect or connect).
        assert client_manager.events == [
            "cancel_all",
            "register_lazy",
            "prune_lazy",
        ]
        assert forced.ok is True
        assert forced.pending_requests_cancelled == 1

    @pytest.mark.asyncio
    async def test_soak_health_and_pending_survive_refused_refresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        pending = client_manager.add_pending_request("active")
        client_manager.set_server_statuses(
            [
                ServerStatus(
                    name="active",
                    status=ServerStatusEnum.ONLINE,
                    tool_count=1,
                    pending_request_count=1,
                )
            ]
        )
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        gateway_tools.set_startup_observations(
            {
                "active": StartupObservation(
                    name="active",
                    startup_policy="eager",
                    startup_source="project",
                )
            }
        )
        patch_refresh_config_sources(monkeypatch, [])
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        before_health = await gateway_tools.health()
        before_pending = await gateway_tools.list_pending({})
        refused = await gateway_tools.refresh({"reason": "soak"})
        after_health = await gateway_tools.health()
        after_pending = await gateway_tools.list_pending({})

        assert before_health.servers[0].name == "active"
        assert before_health.servers[0].startup_policy == "eager"
        assert before_pending.requests[0].request_id == "active::1"
        assert refused.ok is False
        assert refused.pending_requests_seen == 1
        assert refused.pending_requests_refused == 1
        assert pending.future.cancelled() is False
        assert after_health.revision_id == "test-rev"
        assert after_health.servers[0].status == "online"
        assert after_pending.requests[0].request_id == "active::1"
        assert client_manager.events == []


class TestRefreshConfigUnchanged:
    """Unit tests for the gateway.refresh config-equality helper (issue #79)."""

    def _local(self, **kwargs: Any) -> ResolvedServerConfig:
        return ResolvedServerConfig(
            name=kwargs.pop("name", "srv"),
            source=kwargs.pop("source", "project"),
            config=LocalMcpServerConfig(**kwargs),
        )

    def test_same_process_is_unchanged(self) -> None:
        a = self._local(command="c", args=["x"])
        b = self._local(command="c", args=["x"])
        assert _refresh_config_unchanged(a, b) is True

    def test_command_change_detected(self) -> None:
        a = self._local(command="old")
        b = self._local(command="new")
        assert _refresh_config_unchanged(a, b) is False

    def test_source_difference_ignored(self) -> None:
        a = self._local(command="c", source="manifest")
        b = self._local(command="c", source="project")
        assert _refresh_config_unchanged(a, b) is True

    def test_env_mirroring_environ_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Adopt path stores env resolved from os.environ; loader stores None.
        monkeypatch.setenv("KEY", "val")
        adopted = self._local(command="c", env={"KEY": "val"})
        loader = self._local(command="c", env=None)
        assert _refresh_config_unchanged(adopted, loader) is True

    def test_genuine_env_override_change_detected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OVERRIDE", raising=False)
        a = self._local(command="c", env={"OVERRIDE": "old"})
        b = self._local(command="c", env={"OVERRIDE": "new"})
        assert _refresh_config_unchanged(a, b) is False

    def test_type_mismatch_detected(self) -> None:
        local = self._local(command="c")
        remote = ResolvedServerConfig(
            name="srv",
            source="project",
            config=RemoteMcpServerConfig(url="https://x"),
        )
        assert _refresh_config_unchanged(local, remote) is False


class TestAutomaticUpdateNoticesRemoved:
    """Consiliency/pmcp#150: the gateway no longer volunteers update notices.

    Eight attempts failed to make the notice trustworthy, because pmcp cannot
    observe which package artifact a running server is executing. The advisor
    board's conclusion was to remove the feature rather than ship a claim that
    can be wrong in both directions. `gateway.update_server` keeps reporting
    versions on request -- that path is explicitly retained.

    These tests assert the removal is COMPLETE. A half-removal is the failure
    mode: `provision` alone carried 20 of the 26 attachment sites, and a missed
    one would call a deleted method at runtime.
    """

    def test_no_notice_fields_on_public_output_models(self) -> None:
        """The removed fields are gone from every output model that carried them.

        Checked against the model fields rather than a live call: today
        `server.py` serialises with `model_dump()` and no `exclude_none`, so a
        surviving field would be emitted on the wire as an explicit null.
        """
        from pmcp.types import (
            CatalogSearchOutput,
            InvokeOutput,
            ProvisionOutput,
            SchemaCard,
        )

        assert "stale_updates" not in CatalogSearchOutput.model_fields
        for model in (SchemaCard, InvokeOutput, ProvisionOutput):
            assert "update_warning" not in model.model_fields, model.__name__

    def test_no_notice_machinery_on_gateway_tools(self) -> None:
        """The emission helpers and the background indexer are gone.

        Named explicitly so a future re-introduction has to be deliberate rather
        than accidental -- `hasattr` here is the cheap guard that the source
        grep in the PR cannot enforce over time.
        """
        gt = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )
        for attr in (
            "_get_update_warning",
            "_run_stale_index",
            "_stale_indexer_loop",
            "start_stale_indexer",
            "stop_stale_indexer",
            "_stale_check_cache",
        ):
            assert not hasattr(gt, attr), f"{attr} survived the removal"

    def test_exported_tool_descriptions_do_not_promise_notices(self) -> None:
        """The tools/list prose must not tell agents to wait for a notice.

        Board review of this PR: `gateway.update_server`'s description still read
        "Use this when invoke/describe/provision warn that a newer version is
        available" -- shipped to every client through `tools/list`, instructing
        agents to wait for notices that no longer exist.

        The identifier grep that verified this removal could not catch it,
        because it is PROSE, not a symbol. This test closes that class of gap.
        """
        from pmcp.tools.handlers import get_gateway_tool_definitions

        forbidden = (
            "warn that a newer version",
            "newer version is available",
            "update_warning",
            "stale_updates",
        )
        for tool in get_gateway_tool_definitions():
            text = (tool.description or "").lower()
            for phrase in forbidden:
                assert phrase.lower() not in text, (
                    f"{tool.name} description still promises update notices: {phrase!r}"
                )

    @pytest.mark.asyncio
    async def test_catalog_search_emits_no_stale_updates(self) -> None:
        """A real catalog_search payload carries no notice key at all."""
        gt = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )
        result = await gt.catalog_search({"query": "anything"})
        assert "stale_updates" not in result.model_dump()


class TestCatalogSearch:
    """Tests for catalog_search."""

    @pytest.fixture
    def gateway_tools(self) -> GatewayTools:
        client_manager = MockClientManager(create_mock_tools())
        client_manager.set_server_online("github")
        client_manager.set_server_online("jira")

        policy_manager = PolicyManager()

        return GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

    @pytest.mark.asyncio
    async def test_returns_all_tools_with_no_filters(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.catalog_search({})

        assert len(result.results) == 3
        assert result.total_available == 3
        assert result.truncated is False
        assert result.cli_hints == []

    @pytest.mark.asyncio
    async def test_catalog_search_queryless_does_not_probe_cli_hints(
        self, gateway_tools: GatewayTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fail_probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
            raise AssertionError("queryless catalog_search should not probe CLIs")

        monkeypatch.setattr("pmcp.tools.handlers.probe_clis", fail_probe_clis)

        result = await gateway_tools.catalog_search({})

        assert result.cli_hints == []

    def test_catalog_search_output_defaults_cli_hints_to_empty_list(self) -> None:
        output = CatalogSearchOutput(
            results=[],
            total_available=0,
            truncated=False,
        )

        assert output.cli_hints == []

    @pytest.mark.asyncio
    async def test_filters_by_server_name(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.catalog_search({"filters": {"server": "github"}})

        assert len(result.results) == 2
        assert all(r.server == "github" for r in result.results)

    @pytest.mark.asyncio
    async def test_filters_by_query(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.catalog_search({"query": "search"})

        assert len(result.results) > 0
        assert any(
            "search" in r.tool_name.lower() or "search" in r.tags
            for r in result.results
        )

    @pytest.mark.asyncio
    async def test_catalog_search_returns_cli_hints_for_matching_available_cli(
        self, gateway_tools: GatewayTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        gateway_tools._detected_cli_infos = {
            "git": CLIInfo(name="git", path="/usr/bin/git")
        }

        result = await gateway_tools.catalog_search({"query": "git"})

        assert len(result.cli_hints) == 1
        hint = result.cli_hints[0]
        assert hint.name == "git"
        assert hint.available is True
        assert hint.path == "/usr/bin/git"
        assert hint.help_command == ["git", "--help"]
        assert hint.examples == ["git status --short", "git log --oneline -5"]
        assert not hasattr(hint, "help_output")

    @pytest.mark.asyncio
    async def test_catalog_search_keeps_cli_hints_separate_from_capability_cards(
        self, gateway_tools: GatewayTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        gateway_tools._detected_cli_infos = {
            "git": CLIInfo(name="git", path="/usr/bin/git")
        }

        result = await gateway_tools.catalog_search({"query": "git"})

        assert result.cli_hints[0].name == "git"
        assert result.results
        assert all("::" in card.tool_id for card in result.results)
        assert all(card.server in {"github", "jira"} for card in result.results)
        assert all(not card.tool_id.startswith("git::") for card in result.results)

    @pytest.mark.asyncio
    async def test_catalog_search_non_matching_query_omits_cli_hints(
        self, gateway_tools: GatewayTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        gateway_tools._detected_cli_infos = {
            "git": CLIInfo(name="git", path="/usr/bin/git")
        }

        result = await gateway_tools.catalog_search({"query": "browser"})

        assert result.cli_hints == []

    @pytest.mark.asyncio
    async def test_filters_by_risk_level(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.catalog_search({"filters": {"risk_max": "low"}})

        assert all(r.risk_hint == "low" for r in result.results)

    @pytest.mark.asyncio
    async def test_respects_limit(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.catalog_search({"limit": 1})

        assert len(result.results) == 1
        assert result.truncated is True

    @pytest.mark.asyncio
    async def test_returns_capability_cards_without_schemas(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.catalog_search({})

        for card in result.results:
            assert hasattr(card, "tool_id")
            assert hasattr(card, "short_description")
            # Should not have full schema
            assert not hasattr(card, "input_schema")

    @pytest.mark.asyncio
    async def test_catalog_search_includes_compact_modern_metadata(
        self, gateway_tools: GatewayTools
    ) -> None:
        tool = gateway_tools._client_manager._tools["github::list_issues"]
        tool.title = "List Issues"
        tool.icons = [{"src": "tool.svg"}]
        tool.execution = {"taskSupport": "optional"}
        tool.schema_dialect = "https://json-schema.org/draft/2020-12/schema"

        result = await gateway_tools.catalog_search({"query": "list_issues"})

        card = result.results[0]
        assert card.title == "List Issues"
        assert card.icons == [{"src": "tool.svg"}]
        assert card.execution == {"taskSupport": "optional"}
        assert card.schema_dialect == "https://json-schema.org/draft/2020-12/schema"
        assert not hasattr(card, "output_schema")

    @pytest.mark.asyncio
    async def test_catalog_search_uses_default_schema_dialect_when_schema_omits_marker(
        self, gateway_tools: GatewayTools
    ) -> None:
        tool = gateway_tools._client_manager._tools["github::list_issues"]
        tool.input_schema = {"type": "object", "properties": {}}

        result = await gateway_tools.catalog_search({"query": "list_issues"})

        assert (
            result.results[0].schema_dialect
            == "https://json-schema.org/draft/2020-12/schema"
        )

    @pytest.mark.asyncio
    async def test_catalog_search_old_tool_metadata_is_optional(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.catalog_search({"query": "search_issues"})

        card = result.results[0]
        assert card.tool_id == "jira::search_issues"
        assert card.title is None
        assert card.icons is None
        assert card.execution is None

    @pytest.mark.asyncio
    async def test_conformance_catalog_order_without_relevance_ranking(self) -> None:
        tools = [
            ToolInfo(
                tool_id="zeta::beta",
                server_name="zeta",
                tool_name="beta",
                description="Beta",
                short_description="Beta",
                input_schema={},
                tags=[],
                risk_hint=RiskHint.LOW,
            ),
            ToolInfo(
                tool_id="alpha::gamma",
                server_name="alpha",
                tool_name="gamma",
                description="Gamma",
                short_description="Gamma",
                input_schema={},
                tags=[],
                risk_hint=RiskHint.LOW,
            ),
            ToolInfo(
                tool_id="alpha::alpha",
                server_name="alpha",
                tool_name="alpha",
                description="Alpha",
                short_description="Alpha",
                input_schema={},
                tags=[],
                risk_hint=RiskHint.LOW,
            ),
        ]
        client_manager = MockClientManager(tools)
        client_manager.set_server_online("zeta")
        client_manager.set_server_online("alpha")
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        result = await gateway_tools.catalog_search({})

        assert [card.tool_id for card in result.results] == [
            "alpha::alpha",
            "alpha::gamma",
            "zeta::beta",
        ]

    @pytest.mark.asyncio
    async def test_catalog_search_includes_offline_tenant_code_mode_cards(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager([])
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=DescriptionsCache(
                generated_at="2026-04-23T00:00:00Z",
                gateway_version="1.0.0",
                servers={
                    "tenant-code-mode": GeneratedServerDescriptions(
                        package="tenant-code-mode",
                        version="0.0.0",
                        generated_at="2026-04-23T00:00:00Z",
                        capability_summary="Tenant sandbox code-mode execution",
                        tools=[
                            PrebuiltToolInfo(
                                name="run_script",
                                description="Submit sandbox code for execution",
                                short_description="Run sandbox code",
                                tags=["sandbox", "code-mode", "artifacts"],
                                risk_hint="medium",
                            ),
                            PrebuiltToolInfo(
                                name="get_result",
                                description="Fetch sandbox execution result",
                                short_description="Fetch run result",
                                tags=["sandbox", "logs"],
                                risk_hint="low",
                            ),
                            PrebuiltToolInfo(
                                name="cancel_run",
                                description="Cancel sandbox execution",
                                short_description="Cancel run",
                                tags=["sandbox", "task"],
                                risk_hint="medium",
                            ),
                        ],
                    )
                },
            ),
        )
        gateway_tools._detected_cli_infos = {
            "git": CLIInfo(name="git", path="/usr/bin/git")
        }

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.catalog_search(
            {"query": "sandbox", "include_offline": True}
        )

        assert {card.tool_id for card in result.results} == {
            "tenant-code-mode::cancel_run",
            "tenant-code-mode::get_result",
            "tenant-code-mode::run_script",
        }
        assert result.cli_hints == []
        assert client_manager.ensure_connected_calls == []
        assert client_manager.connected_configs == []

    @pytest.mark.asyncio
    async def test_catalog_search_omits_policy_denied_tenant_code_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager(
            [
                ToolInfo(
                    tool_id="tenant-code-mode::run_script",
                    server_name="tenant-code-mode",
                    tool_name="run_script",
                    description="Submit sandbox code",
                    short_description="Submit sandbox code",
                    input_schema={"type": "object", "properties": {}},
                    tags=["sandbox"],
                    risk_hint=RiskHint.MEDIUM,
                )
            ]
        )
        client_manager.set_server_online("tenant-code-mode")
        policy_manager = PolicyManager()
        policy_manager.is_server_allowed = (  # type: ignore[method-assign]
            lambda name: name != "tenant-code-mode"
        )
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
            descriptions_cache=DescriptionsCache(
                generated_at="2026-04-23T00:00:00Z",
                gateway_version="1.0.0",
                servers={
                    "tenant-code-mode": GeneratedServerDescriptions(
                        package="tenant-code-mode",
                        version="0.0.0",
                        generated_at="2026-04-23T00:00:00Z",
                        capability_summary="Tenant sandbox code-mode execution",
                        tools=[
                            PrebuiltToolInfo(
                                name="run_script",
                                description="Submit sandbox code",
                                short_description="Submit sandbox code",
                                tags=["sandbox"],
                                risk_hint="medium",
                            )
                        ],
                    )
                },
            ),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest",
            lambda: Manifest(
                version="1.0",
                cli_alternatives={},
                servers={},
                discovery_queue_path=".mcp-gateway/discovery_queue.json",
            ),
        )

        result = await gateway_tools.catalog_search(
            {"query": "sandbox", "include_offline": True}
        )

        assert result.results == []
        assert client_manager.ensure_connected_calls == []
        assert client_manager.connected_configs == []


class TestServerLifecycleTools:
    """Tests for gateway server lifecycle tools."""

    @pytest.fixture
    def gateway_tools(self, monkeypatch: pytest.MonkeyPatch) -> GatewayTools:
        client_manager = MockClientManager()
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )
        configured = [
            ResolvedServerConfig(
                name="configured",
                source="project",
                config=LocalMcpServerConfig(command="configured-cmd"),
            ),
            ResolvedServerConfig(
                name="denied",
                source="project",
                config=LocalMcpServerConfig(command="denied-cmd"),
            ),
            ResolvedServerConfig(
                name="fails-connect",
                source="project",
                config=LocalMcpServerConfig(command="fail-cmd"),
            ),
        ]
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "manifest": ServerConfig(
                    name="manifest",
                    description="Manifest server",
                    keywords=["manifest"],
                    install={},
                    command="manifest-cmd",
                    args=[],
                ),
                "needs-key": ServerConfig(
                    name="needs-key",
                    description="Auth server",
                    keywords=["auth"],
                    install={},
                    command="auth-cmd",
                    args=[],
                    requires_api_key=True,
                    env_var="PMCP_TEST_KEY",
                ),
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_dotenv", lambda *a, **kw: False)
        policy_manager.is_server_allowed = lambda name: name != "denied"  # type: ignore[method-assign]
        return gateway_tools

    @pytest.mark.asyncio
    async def test_lifecycle_tool_schemas_are_additive(self) -> None:
        tools = {tool.name: tool for tool in get_gateway_tool_definitions()}

        assert "gateway.connect_server" in tools
        assert "gateway.disconnect_server" in tools
        assert "gateway.restart_server" in tools
        assert tools["gateway.connect_server"].input_schema["required"] == [
            "server_name"
        ]
        assert "force" not in tools["gateway.connect_server"].input_schema["properties"]
        assert (
            tools["gateway.disconnect_server"].input_schema["properties"]["force"][
                "type"
            ]
            == "boolean"
        )
        assert (
            tools["gateway.restart_server"].input_schema["properties"]["force"]["type"]
            == "boolean"
        )
        assert "force" not in tools["gateway.refresh"].input_schema.get("required", [])

    @pytest.mark.asyncio
    async def test_connect_server_already_online(
        self, gateway_tools: GatewayTools
    ) -> None:
        manager = cast(Any, gateway_tools._client_manager)
        manager.set_server_online("configured")

        result = await gateway_tools.connect_server({"server_name": "configured"})

        assert result.ok is True
        assert result.prior_status == "online"
        assert result.new_status == "online"
        assert result.cancelled_request_count == 0
        assert manager.connected_configs == []

    @pytest.mark.asyncio
    async def test_connect_server_starts_configured_and_manifest(
        self, gateway_tools: GatewayTools
    ) -> None:
        configured = await gateway_tools.connect_server({"server_name": "configured"})
        manifest = await gateway_tools.connect_server({"server_name": "manifest"})

        assert configured.ok is True
        assert manifest.ok is True
        connected_names = [
            config.name
            for config in cast(Any, gateway_tools._client_manager).connected_configs
        ]
        assert connected_names == ["configured", "manifest"]

    @pytest.mark.asyncio
    async def test_connect_server_reports_unknown_policy_and_missing_auth(
        self, gateway_tools: GatewayTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PMCP_TEST_KEY", raising=False)

        unknown = await gateway_tools.connect_server({"server_name": "unknown"})
        denied = await gateway_tools.connect_server({"server_name": "denied"})
        missing_auth = await gateway_tools.connect_server({"server_name": "needs-key"})

        assert unknown.ok is False
        assert unknown.errors == ["Unknown server: unknown"]
        assert denied.ok is False
        assert "blocked by policy" in denied.message
        assert missing_auth.ok is False
        assert "PMCP_TEST_KEY" in (missing_auth.errors or [""])[0]

    @pytest.mark.asyncio
    async def test_connect_server_reports_missing_remote_header_auth(
        self, gateway_tools: GatewayTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("REMOTE_API_TOKEN", raising=False)
        configured = [
            ResolvedServerConfig(
                name="remote-api",
                source="project",
                config=RemoteMcpServerConfig(
                    url="https://example.com/sse",
                    headers={"Authorization": "Bearer ${REMOTE_API_TOKEN}"},
                ),
            )
        ]
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)

        result = await gateway_tools.connect_server({"server_name": "remote-api"})

        assert result.ok is False
        assert result.auth_state == "missing_auth"
        assert result.missing_env_vars == ["REMOTE_API_TOKEN"]
        assert cast(Any, gateway_tools._client_manager).connected_configs == []

    @pytest.mark.asyncio
    async def test_provision_reports_missing_remote_manifest_header_auth(
        self, gateway_tools: GatewayTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("REMOTE_API_TOKEN", raising=False)
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "remote-api": ServerConfig(
                    name="remote-api",
                    description="Remote API",
                    keywords=["remote"],
                    install={},
                    command="",
                    args=[],
                    transport="streamable-http",
                    url="https://example.com/mcp",
                    headers={"Authorization": "Bearer ${REMOTE_API_TOKEN}"},
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        fake_jm = types.SimpleNamespace(start_install=pytest.fail)
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.get_job_manager", lambda: fake_jm)

        result = await gateway_tools.provision({"server_name": "remote-api"})

        assert result.ok is False
        assert result.auth_state == "missing_auth"
        assert result.missing_env_vars == ["REMOTE_API_TOKEN"]
        assert cast(Any, gateway_tools._client_manager).connected_configs == []

    @pytest.mark.asyncio
    async def test_connect_server_uses_registered_discovered_server(
        self, gateway_tools: GatewayTools
    ) -> None:
        gateway_tools._discovered_server_configs["discovered"] = ServerConfig(
            name="discovered",
            description="Discovered",
            keywords=["discovered"],
            install={},
            command="discovered-cmd",
            args=[],
        )

        result = await gateway_tools.connect_server({"server_name": "discovered"})

        assert result.ok is True
        assert cast(Any, gateway_tools._client_manager).connected_configs[-1].name == (
            "discovered"
        )

    @pytest.mark.asyncio
    async def test_disconnect_server_success_and_pending_policy(
        self, gateway_tools: GatewayTools
    ) -> None:
        manager = cast(Any, gateway_tools._client_manager)
        manager.set_server_online("configured")
        stopped = await gateway_tools.disconnect_server({"server_name": "configured"})
        manager.set_server_online("configured")
        request = manager.add_pending_request("configured")
        refused = await gateway_tools.disconnect_server({"server_name": "configured"})
        assert request.future.cancelled() is False
        forced = await gateway_tools.disconnect_server(
            {"server_name": "configured", "force": True}
        )

        assert stopped.ok is True
        assert stopped.new_status == "lazy"
        assert refused.ok is False
        assert "force=true" in refused.message
        assert forced.ok is True
        assert forced.cancelled_request_count == 1
        assert request.future.cancelled()

    @pytest.mark.asyncio
    async def test_restart_server_success_failure_and_pending_policy(
        self, gateway_tools: GatewayTools
    ) -> None:
        manager = cast(Any, gateway_tools._client_manager)
        manager.set_server_online("configured")
        restarted = await gateway_tools.restart_server({"server_name": "configured"})
        manager.set_server_online("configured")
        request = manager.add_pending_request("configured")
        refused = await gateway_tools.restart_server({"server_name": "configured"})
        assert request.future.cancelled() is False
        forced = await gateway_tools.restart_server(
            {"server_name": "configured", "force": True}
        )
        failed = await gateway_tools.restart_server({"server_name": "fails-connect"})

        assert restarted.ok is True
        assert restarted.new_status == "online"
        assert refused.ok is False
        assert forced.ok is True
        assert forced.cancelled_request_count == 1
        assert failed.ok is False
        assert failed.errors == ["Failed to connect to fails-connect: boom"]

    @pytest.mark.asyncio
    async def test_soak_force_lifecycle_cancels_only_target_server(
        self, gateway_tools: GatewayTools
    ) -> None:
        manager = cast(Any, gateway_tools._client_manager)
        manager.set_server_online("configured")
        manager.set_server_online("other")
        target = manager.add_pending_request("configured")
        other = manager.add_pending_request("other")

        refused = await gateway_tools.disconnect_server({"server_name": "configured"})

        assert refused.ok is False
        assert target.future.cancelled() is False
        assert other.future.cancelled() is False
        forced = await gateway_tools.disconnect_server(
            {"server_name": "configured", "force": True}
        )

        assert forced.ok is True
        assert forced.cancelled_request_count == 1
        assert target.future.cancelled()
        assert other.future.cancelled() is False
        assert [p.request_id for p in manager.get_pending_requests("other")] == [2]

    @pytest.mark.asyncio
    async def test_health_keeps_runtime_stopped_startup_observation(
        self, gateway_tools: GatewayTools
    ) -> None:
        manager = cast(Any, gateway_tools._client_manager)
        manager.set_server_online("configured")
        gateway_tools.set_startup_observations(
            {
                "configured": StartupObservation(
                    name="configured",
                    startup_policy="lazy",
                    startup_source="project",
                )
            }
        )

        await gateway_tools.disconnect_server({"server_name": "configured"})
        result = await gateway_tools.health()

        by_name = {server.name: server for server in result.servers}
        assert by_name["configured"].status == "lazy"
        assert by_name["configured"].startup_policy == "lazy"


class TestDescribe:
    """Tests for describe."""

    @pytest.fixture
    def gateway_tools(self) -> GatewayTools:
        client_manager = MockClientManager(create_mock_tools())
        client_manager.set_server_online("github")
        policy_manager = PolicyManager()

        return GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

    @pytest.mark.asyncio
    async def test_returns_schema_card(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.describe({"tool_id": "github::create_issue"})

        assert result.server == "github"
        assert result.tool_name == "create_issue"
        assert "GitHub" in result.description
        assert len(result.args) == 2
        assert any(a.name == "title" and a.required for a in result.args)

    @pytest.mark.asyncio
    async def test_describe_exposes_nested_array_item_schema(
        self, gateway_tools: GatewayTools
    ) -> None:
        # Mirror brightdata::search_engine_batch: an array of objects with a
        # required item field, plus a scalar arg (regression) and an
        # array-of-scalars / no-required-field edge case.
        tool = gateway_tools._client_manager._tools["github::create_issue"]
        tool.input_schema = {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"field": {"type": "string"}},
                    },
                },
                "metadata": {
                    "type": "object",
                    "properties": {"owner": {"type": "string"}},
                    "required": ["owner"],
                },
                "name": {"type": "string"},
            },
            "required": ["queries", "name"],
        }

        result = await gateway_tools.describe({"tool_id": "github::create_issue"})
        by_name = {arg.name: arg for arg in result.args}

        # Array-of-objects: item schema exposes item type + required field name/type.
        queries = by_name["queries"]
        assert queries.type == "array"
        assert queries.item_schema == {
            "type": "object",
            "required": ["query"],
            "properties": {"query": "string"},
        }

        # Array-of-scalars: item type recorded only.
        assert by_name["tags"].item_schema == {"type": "string"}

        # Array-of-objects with no required fields: falls back to all item props.
        assert by_name["filters"].item_schema == {
            "type": "object",
            "required": [],
            "properties": {"field": "string"},
        }

        # Top-level object property: summarized (no "type" key, object is implied).
        assert by_name["metadata"].item_schema == {
            "required": ["owner"],
            "properties": {"owner": "string"},
        }

        # Scalar arg unchanged.
        assert by_name["name"].type == "string"
        assert by_name["name"].item_schema is None

        placeholders = result.invoke_template.arguments
        # Nested array placeholder shows the {"query": ...} shape, not <required: array>.
        assert placeholders["queries"] == '[{"query": "<string>"}]'
        assert placeholders["tags"] == '["<string>"]'
        assert placeholders["filters"] == '[{"field": "<string>"}]'
        assert placeholders["metadata"] == '{"owner": "<string>"}'
        # Scalar placeholder is byte-identical to today's form.
        assert placeholders["name"] == "<required: string>"

    @pytest.mark.asyncio
    async def test_describe_returns_modern_tool_metadata(
        self, gateway_tools: GatewayTools
    ) -> None:
        tool = gateway_tools._client_manager._tools["github::create_issue"]
        tool.title = "Create Issue"
        tool.icons = [{"src": "issue.svg"}]
        tool.output_schema = {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        }
        tool.annotations = {"readOnlyHint": True}
        tool.execution = {"taskSupport": "optional"}
        tool.schema_dialect = "https://json-schema.org/draft/2020-12/schema"

        result = await gateway_tools.describe({"tool_id": "github::create_issue"})

        assert result.title == "Create Issue"
        assert result.icons == [{"src": "issue.svg"}]
        assert result.output_schema == {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        }
        assert result.annotations == {"readOnlyHint": True}
        assert result.execution == {"taskSupport": "optional"}
        assert result.schema_dialect == "https://json-schema.org/draft/2020-12/schema"

    @pytest.mark.asyncio
    async def test_successful_describe_carries_no_failure_feedback_hint(
        self, gateway_tools: GatewayTools
    ) -> None:
        """A successful describe must not claim a technical failure occurred.

        `_feedback_hint()` opens with "Technical failure detected", and this
        site emitted it unconditionally, so every successful describe told the
        client something had gone wrong. `invoke`'s success path already passed
        None; this one simply did not follow the convention. Describe's own
        error paths still emit the hint, which is where it belongs.
        """
        result = await gateway_tools.describe({"tool_id": "github::create_issue"})

        assert result.feedback_hint is None

    @pytest.mark.asyncio
    async def test_describe_uses_default_schema_dialect_when_schema_omits_marker(
        self, gateway_tools: GatewayTools
    ) -> None:
        tool = gateway_tools._client_manager._tools["github::create_issue"]
        tool.input_schema = {"type": "object", "properties": {}}

        result = await gateway_tools.describe({"tool_id": "github::create_issue"})

        assert result.schema_dialect == "https://json-schema.org/draft/2020-12/schema"

    @pytest.mark.asyncio
    async def test_describe_annotations_do_not_override_risk_safety_notes(
        self, gateway_tools: GatewayTools
    ) -> None:
        tool = gateway_tools._client_manager._tools["github::create_issue"]
        tool.annotations = {"readOnlyHint": True}

        result = await gateway_tools.describe({"tool_id": "github::create_issue"})

        assert result.annotations == {"readOnlyHint": True}
        assert result.safety_notes == [
            "This tool may modify data or have side effects."
        ]

    @pytest.mark.asyncio
    async def test_raises_for_unknown_tool(self, gateway_tools: GatewayTools) -> None:
        with pytest.raises(GatewayException) as exc_info:
            await gateway_tools.describe({"tool_id": "unknown::tool"})
        assert exc_info.value.code.value == "E301"  # TOOL_NOT_FOUND


class TestInvoke:
    """Tests for invoke."""

    @pytest.fixture
    def gateway_tools(self) -> GatewayTools:
        client_manager = MockClientManager(create_mock_tools())
        client_manager.set_server_online("github")
        policy_manager = PolicyManager()

        return GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

    @pytest.mark.asyncio
    async def test_calls_tool_and_returns_result(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.invoke(
            {"tool_id": "github::list_issues", "arguments": {}}
        )

        assert result.ok is True
        assert result.tool_id == "github::list_issues"

    @pytest.mark.asyncio
    async def test_invoke_audits_success_and_propagates_trace_context(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.invoke(
            {
                "tool_id": "github::list_issues",
                "arguments": {},
                "_meta": {
                    "traceparent": "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01",
                    "baggage": "tenant=test",
                    "authorization": "Bearer secret",
                },
            }
        )

        health = await gateway_tools.health()
        assert result.ok is True
        assert (
            cast(Any, gateway_tools._client_manager).last_call_trace_context is not None
        )
        assert cast(
            Any, gateway_tools._client_manager
        ).last_call_trace_context.traceparent.startswith("00-")
        assert health.audit_events is not None
        event = health.audit_events[-1]
        assert event.method == "gateway.invoke"
        assert event.tool_id == "github::list_issues"
        assert event.outcome == "success"
        assert event.trace_present is True
        assert event.error is None

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_tool(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.invoke(
            {"tool_id": "unknown::tool", "arguments": {}}
        )

        assert result.ok is False
        assert "Tool not found" in (result.errors or [])[0]
        health = await gateway_tools.health()
        assert health.audit_events is not None
        assert health.audit_events[-1].outcome == "failure"

    @pytest.mark.asyncio
    async def test_tenant_code_mode_invoke_sanitizes_trace_and_forwards_task(
        self, gateway_tools: GatewayTools
    ) -> None:
        tenant_tool = ToolInfo(
            tool_id="tenant-code-mode::run_script",
            server_name="tenant-code-mode",
            tool_name="run_script",
            description="Submit sandbox code for execution",
            short_description="Run sandbox code",
            input_schema={"type": "object", "properties": {}},
            tags=["tenant", "sandbox"],
            risk_hint=RiskHint.MEDIUM,
            execution={"taskSupport": "optional"},
        )
        manager = cast(Any, gateway_tools._client_manager)
        manager._tools[tenant_tool.tool_id] = tenant_tool
        manager.set_server_online("tenant-code-mode")

        result = await gateway_tools.invoke(
            {
                "tool_id": "tenant-code-mode::run_script",
                "arguments": {"language": "python"},
                "_meta": {
                    "traceparent": (
                        "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
                    ),
                    "tracestate": "tenant=dev",
                    "baggage": "request=hostmeta",
                    "authorization": "Bearer secret",
                },
                "trace_context": {
                    "traceparent": "Bearer should-not-win",
                    "baggage": "x" * 2048,
                },
                "task": {
                    "metadata": {"run_kind": "smoke"},
                    "ttl": 300,
                    "poll_interval": 2.5,
                    "requestor_context": {"client": "mobile"},
                },
            }
        )

        assert result.ok is True
        assert result.result is None
        assert result.task is not None
        assert result.task.task_id == "task-1"
        assert manager.last_call_task.metadata == {"run_kind": "smoke"}
        assert manager.last_call_task.ttl == 300
        assert manager.last_call_task.poll_interval == 2.5
        assert manager.last_call_task.requestor_context == {"client": "mobile"}
        assert manager.last_call_trace_context.traceparent.startswith("00-")
        assert manager.last_call_trace_context.tracestate == "tenant=dev"
        assert manager.last_call_trace_context.baggage == "request=hostmeta"

        health = await gateway_tools.health()
        assert health.audit_events is not None
        event = health.audit_events[-1]
        assert event.method == "gateway.invoke"
        assert event.server_name == "tenant-code-mode"
        assert event.tool_id == "tenant-code-mode::run_script"
        assert event.task_id == "task-1"
        assert event.trace_present is True
        assert "secret" not in str(event)

    @pytest.mark.asyncio
    async def test_soak_concurrent_lazy_invokes_share_one_connect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()
        client_manager.add_lazy_server("lazy")
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        started = asyncio.Event()
        release = asyncio.Event()
        connect_task: asyncio.Task[None] | None = None
        connect_calls = 0
        connect_lock = asyncio.Lock()

        async def do_connect() -> None:
            nonlocal connect_calls
            connect_calls += 1
            started.set()
            await release.wait()
            client_manager._lazy_servers.discard("lazy")
            client_manager._online_servers.add("lazy")
            client_manager._tools["lazy::echo"] = ToolInfo(
                tool_id="lazy::echo",
                server_name="lazy",
                tool_name="echo",
                description="Echo",
                short_description="Echo",
                input_schema={},
                tags=[],
                risk_hint=RiskHint.LOW,
            )

        async def ensure_connected(server_name: str) -> bool:
            nonlocal connect_task
            async with connect_lock:
                if client_manager.is_server_online(server_name):
                    return True
                if connect_task is None:
                    connect_task = asyncio.create_task(do_connect())
                task = connect_task
            await task
            return True

        async def call_tool(
            tool_id: str, args: dict[str, Any], timeout_ms: int
        ) -> dict[str, Any]:
            return {"content": [{"type": "text", "text": tool_id}]}

        client_manager.ensure_connected = ensure_connected  # type: ignore[method-assign]
        client_manager.call_tool = call_tool  # type: ignore[method-assign]

        first = asyncio.create_task(
            gateway_tools.invoke({"tool_id": "lazy::echo", "arguments": {}})
        )
        second = asyncio.create_task(
            gateway_tools.invoke({"tool_id": "lazy::echo", "arguments": {}})
        )
        await started.wait()
        assert connect_calls == 1
        release.set()

        results = await asyncio.gather(first, second)

        assert [result.ok for result in results] == [True, True]
        assert connect_calls == 1
        assert client_manager.is_server_online("lazy") is True


class TestHealth:
    """Tests for health."""

    @pytest.fixture
    def gateway_tools(self) -> GatewayTools:
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()

        return GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

    @pytest.mark.asyncio
    async def test_returns_health_status(self, gateway_tools: GatewayTools) -> None:
        result = await gateway_tools.health()

        assert result.revision_id == "test-rev"
        assert isinstance(result.servers, list)
        assert result.gateway_diagnostics is not None
        assert result.gateway_diagnostics.audit_enabled is True
        assert result.gateway_diagnostics.trace_context_supported is True

    @pytest.mark.asyncio
    async def test_config_admin_tools_preview_startup_policy(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / ".mcp.json"
        config_path.write_text(json.dumps({"autoStart": ["existing"]}))
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(create_mock_tools()),  # type: ignore
            policy_manager=PolicyManager(),
            project_root=tmp_path,
        )

        definitions = {tool.name for tool in get_gateway_tool_definitions()}
        preview = await gateway_tools.set_startup_policy(
            {
                "operation": "add",
                "names": ["new"],
                "path": str(config_path),
            }
        )
        policy = await gateway_tools.get_startup_policy()

        assert {
            "gateway.config_status",
            "gateway.get_startup_policy",
            "gateway.set_startup_policy",
        } <= definitions
        assert preview.changed is True
        assert preview.after_autoStart == ["existing", "new"]
        assert json.loads(config_path.read_text())["autoStart"] == ["existing"]
        assert any(source.path == str(config_path) for source in policy.sources)

    @pytest.mark.asyncio
    async def test_conformance_config_status_and_startup_policy_admin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = tmp_path / ".mcp.json"
        config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "configured": {"command": "configured-cmd"},
                    },
                    "autoStart": ["configured", "needs-key"],
                }
            )
        )
        monkeypatch.delenv("PMCP_TEST_KEY", raising=False)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest",
            lambda: Manifest(
                version="1.0",
                cli_alternatives={},
                servers={
                    "needs-key": ServerConfig(
                        name="needs-key",
                        description="Needs key",
                        keywords=["auth"],
                        install={},
                        command="needs-key-cmd",
                        args=[],
                        requires_api_key=True,
                        env_var="PMCP_TEST_KEY",
                    )
                },
                discovery_queue_path=".mcp-gateway/discovery_queue.json",
            ),
        )
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(create_mock_tools()),  # type: ignore
            policy_manager=PolicyManager(),
            project_root=tmp_path,
        )

        status = await gateway_tools.config_status()
        preview = await gateway_tools.set_startup_policy(
            {
                "operation": "add",
                "names": ["preview-only"],
                "path": str(config_path),
            }
        )
        no_op_apply = await gateway_tools.set_startup_policy(
            {
                "operation": "add",
                "names": ["configured"],
                "path": str(config_path),
                "apply": True,
                "dry_run": False,
            }
        )
        policy = await gateway_tools.get_startup_policy()

        entries = {entry.name: entry for entry in status.entries}
        assert entries["configured"].startup_policy == "eager"
        assert entries["needs-key"].startup_policy == "skipped"
        assert entries["needs-key"].startup_skip_reason == "missing_auth"
        assert "missing_auth" in entries["needs-key"].diagnostics
        assert preview.changed is True
        assert preview.dry_run is True
        assert preview.after_autoStart == ["configured", "needs-key", "preview-only"]
        assert json.loads(config_path.read_text())["autoStart"] == [
            "configured",
            "needs-key",
        ]
        assert no_op_apply.ok is True
        assert no_op_apply.changed is False
        assert [
            source.autoStart
            for source in policy.sources
            if source.path == str(config_path)
        ] == [["configured", "needs-key"]]

    @pytest.mark.asyncio
    async def test_health_includes_protocol_metadata(
        self, gateway_tools: GatewayTools
    ) -> None:
        cast(Any, gateway_tools._client_manager).set_server_statuses(
            [
                ServerStatus(
                    name="modern",
                    status=ServerStatusEnum.ONLINE,
                    tool_count=1,
                    protocol_version="2025-11-25",
                    server_capabilities={"tools": {"listChanged": True}},
                ),
                ServerStatus(
                    name="old",
                    status=ServerStatusEnum.ONLINE,
                    tool_count=1,
                ),
            ]
        )

        result = await gateway_tools.health()

        by_name = {server.name: server for server in result.servers}
        modern = by_name["modern"]
        old = by_name["old"]
        assert modern.protocol_version == "2025-11-25"
        assert modern.server_capabilities == {"tools": {"listChanged": True}}
        assert old.protocol_version is None
        assert old.server_capabilities is None

    @pytest.mark.asyncio
    async def test_conformance_health_order_auth_states_and_audit(
        self, gateway_tools: GatewayTools
    ) -> None:
        cast(Any, gateway_tools._client_manager).set_server_statuses(
            [
                ServerStatus(
                    name="zeta",
                    status=ServerStatusEnum.ERROR,
                    tool_count=0,
                    last_error=(
                        'WWW-Authenticate: Bearer resource_metadata="'
                        'https://auth.example/resource", scope="read write", '
                        'error="insufficient_scope"'
                    ),
                ),
                ServerStatus(
                    name="alpha",
                    status=ServerStatusEnum.ONLINE,
                    tool_count=1,
                    protocol_version="2025-11-25",
                    server_capabilities={"tasks": {}},
                ),
            ]
        )
        await gateway_tools.invoke(
            {
                "tool_id": "github::list_issues",
                "arguments": {},
                "_meta": {
                    "traceparent": (
                        "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
                    ),
                    "baggage": "authorization=Bearer secret",
                },
            }
        )

        result = await gateway_tools.health()

        by_name = {server.name: server for server in result.servers}
        tested_names = [
            server.name for server in result.servers if server.name in {"alpha", "zeta"}
        ]
        assert tested_names == ["alpha", "zeta"]
        assert by_name["alpha"].protocol_version == "2025-11-25"
        assert by_name["zeta"].auth_state == "insufficient_scope"
        assert by_name["zeta"].auth_challenge is not None
        assert by_name["zeta"].auth_challenge.missing_scopes == ["read", "write"]
        assert result.audit_events is not None
        event = result.audit_events[-1]
        assert event.method == "gateway.invoke"
        assert event.action == "invoke"
        assert event.tool_id == "github::list_issues"
        assert event.outcome == "success"
        assert event.latency_ms >= 0
        assert event.trace_present is True
        assert event.auth_event is None

    @pytest.mark.asyncio
    async def test_includes_error_details_for_error_servers(
        self, gateway_tools: GatewayTools
    ) -> None:
        cast(Any, gateway_tools._client_manager).set_server_statuses(
            [
                ServerStatus(
                    name="playwright",
                    status=ServerStatusEnum.ERROR,
                    tool_count=0,
                    last_error="Connection refused",
                )
            ]
        )

        result = await gateway_tools.health()

        by_name = {server.name: server for server in result.servers}
        assert by_name["playwright"].status == "error"
        assert by_name["playwright"].error == "Connection refused"

    @pytest.mark.asyncio
    async def test_merges_startup_observations_into_health(
        self, gateway_tools: GatewayTools
    ) -> None:
        cast(Any, gateway_tools._client_manager).set_server_statuses(
            [
                ServerStatus(
                    name="eager",
                    status=ServerStatusEnum.ONLINE,
                    tool_count=2,
                ),
                ServerStatus(
                    name="lazy",
                    status=ServerStatusEnum.LAZY,
                    tool_count=0,
                ),
            ]
        )
        gateway_tools.set_startup_observations(
            {
                "eager": StartupObservation(
                    name="eager",
                    startup_policy="eager",
                    startup_source="project",
                ),
                "lazy": StartupObservation(
                    name="lazy",
                    startup_policy="lazy",
                    startup_source="manifest",
                ),
            }
        )

        result = await gateway_tools.health()

        by_name = {server.name: server for server in result.servers}
        assert by_name["eager"].startup_policy == "eager"
        assert by_name["eager"].startup_source == "project"
        assert by_name["lazy"].startup_policy == "lazy"
        assert by_name["lazy"].startup_source == "manifest"

    @pytest.mark.asyncio
    async def test_health_includes_skipped_startup_entries(
        self, gateway_tools: GatewayTools
    ) -> None:
        gateway_tools.set_startup_observations(
            {
                "unknown": StartupObservation(
                    name="unknown",
                    startup_policy="skipped",
                    startup_source="auto_start",
                    startup_skip_reason="unknown_auto_start",
                ),
                "denied": StartupObservation(
                    name="denied",
                    startup_policy="skipped",
                    startup_source="configured",
                    startup_skip_reason="policy_denied",
                ),
                "needs-key": StartupObservation(
                    name="needs-key",
                    startup_policy="skipped",
                    startup_source="manifest",
                    startup_skip_reason="missing_auth",
                    startup_env_var="PMCP_TEST_KEY",
                ),
            }
        )

        result = await gateway_tools.health()

        by_name = {server.name: server for server in result.servers}
        assert by_name["unknown"].status == "offline"
        assert by_name["unknown"].startup_skip_reason == "unknown_auto_start"
        assert by_name["denied"].startup_skip_reason == "policy_denied"
        assert by_name["needs-key"].startup_skip_reason == "missing_auth"
        assert by_name["needs-key"].startup_env_var == "PMCP_TEST_KEY"

    @pytest.mark.asyncio
    async def test_health_includes_remote_header_missing_vars(
        self, gateway_tools: GatewayTools
    ) -> None:
        gateway_tools.set_startup_observations(
            {
                "remote-api": StartupObservation(
                    name="remote-api",
                    startup_policy="skipped",
                    startup_source="configured",
                    startup_skip_reason="missing_auth",
                    startup_env_var="REMOTE_API_TOKEN",
                    missing_env_vars=["REMOTE_API_TOKEN"],
                )
            }
        )

        health = await gateway_tools.health()

        by_name = {server.name: server for server in health.servers}
        assert by_name["remote-api"].missing_env_vars == ["REMOTE_API_TOKEN"]

    @pytest.mark.asyncio
    async def test_health_merges_provisioned_startup_observation(
        self, gateway_tools: GatewayTools
    ) -> None:
        gateway_tools.set_startup_observations(
            {
                "provisioned": StartupObservation(
                    name="provisioned",
                    startup_policy="lazy",
                    startup_source="manifest",
                )
            }
        )
        gateway_tools._load_provisioned_registry = lambda: {"provisioned": None}  # type: ignore[method-assign]

        result = await gateway_tools.health()

        assert len(result.servers) == 1
        assert result.servers[0].name == "provisioned"
        assert result.servers[0].status == "offline"
        assert result.servers[0].startup_policy == "lazy"

    @pytest.mark.asyncio
    async def test_refresh_replaces_startup_observations_in_health(
        self, gateway_tools: GatewayTools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        configured = [
            ResolvedServerConfig(
                name="fresh",
                source="project",
                config=LocalMcpServerConfig(command="fresh-cmd"),
            )
        ]
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={},
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        gateway_tools.set_startup_observations(
            {
                "stale": StartupObservation(
                    name="stale",
                    startup_policy="skipped",
                    startup_source="auto_start",
                    startup_skip_reason="unknown_auto_start",
                )
            }
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_disabled_auto_start",
            lambda **_: set(),
        )
        monkeypatch.setattr(gateway_tools, "_load_provisioned_registry", lambda: {})

        await gateway_tools.refresh({"reason": "test"})
        cast(Any, gateway_tools._client_manager).set_server_statuses(
            [
                ServerStatus(
                    name="fresh",
                    status=ServerStatusEnum.LAZY,
                    tool_count=0,
                )
            ]
        )

        result = await gateway_tools.health()

        by_name = {server.name: server for server in result.servers}
        assert "stale" not in by_name
        assert by_name["fresh"].startup_policy == "lazy"


class TestCapabilityAndProvision:
    @pytest.mark.asyncio
    async def test_request_capability_name_match_returns_single_candidate(
        self, monkeypatch
    ):
        """Tier-1: explicit name in query → single candidate, status='candidates'."""
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.request_capability(
            {"query": "use playwright for browser automation", "available_clis": []}
        )

        assert result.status == "candidates"
        assert result.candidates is not None
        assert len(result.candidates) == 1
        assert result.candidates[0].name == "playwright"
        assert result.candidates[0].candidate_type == "server"

    @pytest.mark.asyncio
    async def test_request_capability_category_match_returns_all_servers(
        self, monkeypatch
    ):
        """Tier-2: category query → pick_from_category with all category servers."""
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.request_capability(
            {"query": "browser automation", "available_clis": []}
        )

        assert result.status == "pick_from_category"
        assert result.category_name == "browser automation"
        assert result.candidates is not None
        assert len(result.candidates) >= 1

    @pytest.mark.asyncio
    async def test_provision_starts_configured_lazy_server(self, monkeypatch):
        tool = ToolInfo(
            tool_id="custom-browser::snapshot",
            server_name="custom-browser",
            tool_name="snapshot",
            description="Take browser snapshot",
            short_description="Take browser snapshot",
            input_schema={"type": "object", "properties": {}},
            tags=["browser"],
            risk_hint=RiskHint.LOW,
        )
        client_manager = MockClientManager([tool])
        client_manager.add_lazy_server("custom-browser")
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        configured = [
            ResolvedServerConfig(
                name="custom-browser",
                source="project",
                config=LocalMcpServerConfig(command="npx", args=["custom-browser-mcp"]),
            )
        ]

        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)

        result = await gateway_tools.provision({"server_name": "custom-browser"})

        assert result.ok is True
        assert result.status == "complete"
        assert "started from .mcp.json configuration" in result.message

    @pytest.mark.asyncio
    async def test_concurrent_provision_for_configured_lazy_server_reuses_running(
        self, monkeypatch
    ):
        tool = ToolInfo(
            tool_id="custom-browser::snapshot",
            server_name="custom-browser",
            tool_name="snapshot",
            description="Take browser snapshot",
            short_description="Take browser snapshot",
            input_schema={"type": "object", "properties": {}},
            tags=["browser"],
            risk_hint=RiskHint.LOW,
        )
        client_manager = MockClientManager([tool])
        client_manager.add_lazy_server("custom-browser")
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        configured = [
            ResolvedServerConfig(
                name="custom-browser",
                source="project",
                config=LocalMcpServerConfig(command="npx", args=["custom-browser-mcp"]),
            )
        ]

        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)

        first, second = await asyncio.gather(
            gateway_tools.provision({"server_name": "custom-browser"}),
            gateway_tools.provision({"server_name": "custom-browser"}),
        )

        assert first.ok is True
        assert second.ok is True
        assert {first.status, second.status} <= {"complete", "already_running"}
        assert client_manager.is_server_online("custom-browser") is True

    @pytest.mark.asyncio
    async def test_request_then_provision_for_configured_server(self, monkeypatch):
        """Tier-1 name match for a .mcp.json-configured server → provision succeeds."""
        tool = ToolInfo(
            tool_id="custom-browser::snapshot",
            server_name="custom-browser",
            tool_name="snapshot",
            description="Take browser snapshot",
            short_description="Take browser snapshot",
            input_schema={"type": "object", "properties": {}},
            tags=["browser"],
            risk_hint=RiskHint.LOW,
        )
        client_manager = MockClientManager([tool])
        client_manager.add_lazy_server("custom-browser")
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        configured = [
            ResolvedServerConfig(
                name="custom-browser",
                source="project",
                config=LocalMcpServerConfig(command="npx", args=["custom-browser-mcp"]),
            )
        ]

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)

        # Tier-1 sliding-window name match: "custom-browser" appears verbatim in query
        capability = await gateway_tools.request_capability(
            {"query": "use custom-browser for automation", "available_clis": []}
        )
        assert capability.status == "candidates"
        assert capability.candidates is not None
        assert capability.candidates[0].name == "custom-browser"

        provision = await gateway_tools.provision({"server_name": "custom-browser"})
        assert provision.ok is True
        assert provision.status == "complete"
        assert client_manager.is_server_online("custom-browser") is True

    @pytest.mark.asyncio
    async def test_request_capability_returns_search_guidance_when_not_found(
        self, monkeypatch
    ):
        """Tier-3: unknown query → not_available with search_registry guidance."""
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.request_capability({"query": "openbrowser mcp"})

        assert result.status == "not_available"
        assert result.search_guidance is not None
        assert "gateway.search_registry" in result.search_guidance

    @pytest.mark.asyncio
    async def test_request_capability_returns_use_cli_for_available_cli(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.request_capability(
            {"query": "git commits", "available_clis": ["git"]}
        )

        assert result.status == "use_cli"
        assert result.cli is not None
        assert result.cli.name == "git"
        assert result.cli.description == "Git CLI"
        assert result.cli.help_command == ["git", "--help"]
        assert result.cli.examples == ["git status --short", "git log --oneline -5"]
        assert result.cli.help_output is None
        assert "Bash/direct CLI" in result.message
        assert "not executing" in result.message

    @pytest.mark.asyncio
    async def test_request_capability_git_commits_prefers_cli_over_same_named_git_server(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_git_collision_manifest
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.request_capability(
            {"query": "git commits", "available_clis": ["git"]}
        )

        assert result.status == "use_cli"
        assert result.cli is not None
        assert result.cli.name == "git"

    @pytest.mark.asyncio
    async def test_request_capability_explicit_git_mcp_server_request_returns_candidate(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_git_collision_manifest
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.request_capability(
            {"query": "use git mcp server", "available_clis": ["git"]}
        )

        assert result.status == "candidates"
        assert result.candidates is not None
        assert result.candidates[0].name == "git"
        assert result.candidates[0].candidate_type == "server"

    @pytest.mark.asyncio
    async def test_request_capability_recommends_registered_tenant_code_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_configs", lambda **_: [tenant_code_mode_config()]
        )

        result = await gateway_tools.request_capability(
            {"query": "hosted sandbox code execution", "available_clis": []}
        )

        assert result.status == "candidates"
        assert result.candidates is not None
        assert result.candidates[0].name == "tenant-code-mode"
        assert result.candidates[0].candidate_type == "server"
        assert client_manager.ensure_connected_calls == []
        assert client_manager.connected_configs == []

    @pytest.mark.asyncio
    async def test_request_capability_omits_policy_denied_tenant_code_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        policy_manager.is_server_allowed = (  # type: ignore[method-assign]
            lambda name: name != "tenant-code-mode"
        )
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_configs", lambda **_: [tenant_code_mode_config()]
        )

        result = await gateway_tools.request_capability(
            {"query": "hosted sandbox code execution", "available_clis": []}
        )

        assert result.status == "not_available"
        assert client_manager.ensure_connected_calls == []
        assert client_manager.connected_configs == []

    @pytest.mark.asyncio
    async def test_request_capability_tenant_code_mode_preserves_cli_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_configs", lambda **_: [tenant_code_mode_config()]
        )

        result = await gateway_tools.request_capability(
            {"query": "git commits", "available_clis": ["git"]}
        )

        assert result.status == "use_cli"
        assert result.cli is not None
        assert result.cli.name == "git"
        assert client_manager.ensure_connected_calls == []
        assert client_manager.connected_configs == []

    @pytest.mark.asyncio
    async def test_request_capability_explicit_tenant_mcp_intent_wins_over_cli(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_configs", lambda **_: [tenant_code_mode_config()]
        )

        result = await gateway_tools.request_capability(
            {
                "query": "tenant code mode mcp server for node scripts",
                "available_clis": ["git"],
            }
        )

        assert result.status == "candidates"
        assert result.candidates is not None
        assert result.candidates[0].name == "tenant-code-mode"
        assert result.candidates[0].candidate_type == "server"
        assert client_manager.ensure_connected_calls == []
        assert client_manager.connected_configs == []

    @pytest.mark.asyncio
    async def test_sync_environment_caches_cli_probe_infos_with_paths(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        async def fake_probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
            return {"git": CLIInfo(name="git", path="/usr/bin/git")}

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.probe_clis", fake_probe_clis)

        result = await gateway_tools.sync_environment({"platform": "linux"})

        assert result.detected_clis == ["git"]
        assert gateway_tools._detected_cli_infos["git"].path == "/usr/bin/git"

    @pytest.mark.asyncio
    async def test_request_capability_reuses_cached_cli_probe_infos_without_reprobing(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        probe_calls = 0

        async def fake_probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
            nonlocal probe_calls
            probe_calls += 1
            return {"git": CLIInfo(name="git", path="/usr/bin/git")}

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.probe_clis", fake_probe_clis)

        await gateway_tools.sync_environment({"platform": "linux"})
        result = await gateway_tools.request_capability({"query": "browser automation"})

        assert probe_calls == 1
        assert result.status == "pick_from_category"

    @pytest.mark.asyncio
    async def test_request_capability_use_cli_preserves_cached_sync_environment_path(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        async def fake_probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
            return {"git": CLIInfo(name="git", path="/usr/bin/git")}

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.probe_clis", fake_probe_clis)

        await gateway_tools.sync_environment({"platform": "linux"})
        result = await gateway_tools.request_capability({"query": "git commits"})

        assert result.status == "use_cli"
        assert result.cli is not None
        assert result.cli.path == "/usr/bin/git"

    @pytest.mark.asyncio
    async def test_request_capability_probe_fallback_returns_use_cli_when_detected(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        async def fake_probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
            return {"git": CLIInfo(name="git", path="/usr/bin/git")}

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.probe_clis", fake_probe_clis)

        result = await gateway_tools.request_capability({"query": "git commits"})

        assert result.status == "use_cli"
        assert result.cli is not None
        assert result.cli.path == "/usr/bin/git"

    @pytest.mark.asyncio
    async def test_request_capability_available_clis_do_not_overwrite_cached_probe_paths(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        gateway_tools._detected_cli_infos = {
            "git": CLIInfo(name="git", path="/usr/bin/git")
        }

        async def fake_probe_clis(cli_configs: dict[str, dict]) -> dict[str, CLIInfo]:
            raise AssertionError("request_capability should not probe explicit CLIs")

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_manifest_for_request_tests
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.probe_clis", fake_probe_clis)

        await gateway_tools.request_capability(
            {"query": "browser automation", "available_clis": ["docker"]}
        )

        assert gateway_tools._detected_cli_infos["git"].path == "/usr/bin/git"

    @pytest.mark.asyncio
    async def test_request_capability_prefer_mcp_for_github_issues_suppresses_git_cli(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_git_and_github_manifest
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.request_capability(
            {"query": "github issues", "available_clis": ["git"]}
        )

        assert result.status == "candidates"
        assert result.candidates is not None
        assert result.candidates[0].name == "github"

    @pytest.mark.asyncio
    async def test_request_capability_prefer_mcp_for_pull_requests_suppresses_git_cli(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest", create_git_and_github_manifest
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.request_capability(
            {"query": "pull requests", "available_clis": ["git"]}
        )

        assert result.status != "use_cli"

    @pytest.mark.asyncio
    async def test_provision_reports_auth_metadata_for_missing_key(self, monkeypatch):
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "browser-use": ServerConfig(
                    name="browser-use",
                    description="Browser Use",
                    keywords=["browser-use"],
                    install={},
                    command="uvx",
                    args=["--from", "browser-use[cli]", "browser-use", "--mcp"],
                    requires_api_key=True,
                    env_var="OPENAI_API_KEY",
                    env_instructions="Set OPENAI_API_KEY or ANTHROPIC_API_KEY",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Prevent _check_api_key_available from loading env vars out of pmcp files on disk
        monkeypatch.setattr("pmcp.tools.handlers.load_dotenv", lambda *a, **kw: False)

        result = await gateway_tools.provision({"server_name": "browser-use"})

        assert result.ok is False
        assert result.needs_api_key is True
        assert result.auth_required is True
        assert result.auth_mode == "api_key"
        assert result.auth_state == "missing_auth"
        assert result.alternative_env_vars == ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]

    @pytest.mark.asyncio
    async def test_auth_connect_stores_credential(self, tmp_path: Path, monkeypatch):
        home = tmp_path / "home"
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "browser-use": ServerConfig(
                    name="browser-use",
                    description="Browser Use",
                    keywords=["browser-use"],
                    install={},
                    command="uvx",
                    args=["browser-use"],
                    requires_api_key=True,
                    env_var="OPENAI_API_KEY",
                    env_instructions="Set OPENAI_API_KEY",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        result = await gateway_tools.auth_connect(
            {
                "server_name": "browser-use",
                "credential": "test-token",
                "scope": "user",
            }
        )

        assert result.ok is True
        assert result.env_var == "OPENAI_API_KEY"
        assert "gateway.provision" in (result.next_step or "")
        assert result.env_path == str(home / ".config" / "pmcp" / "pmcp.env")
        assert read_env_file(Path(result.env_path or "")) == {
            "OPENAI_API_KEY": "test-token"
        }
        assert os.environ["OPENAI_API_KEY"] == "test-token"
        events = (await gateway_tools.health()).audit_events or []
        assert events[-1].auth_event == "credential_stored"
        assert "test-token" not in events[-1].model_dump_json()

    @pytest.mark.asyncio
    async def test_auth_connect_stores_credential_under_namespaced_secret_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A server with a namespaced secret_key persists under it, not the
        generic runtime env_var, avoiding collisions in the flat secret store."""
        home = tmp_path / "home"
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(create_mock_tools()),  # type: ignore
            policy_manager=PolicyManager(),
        )

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "brightdata": ServerConfig(
                    name="brightdata",
                    description="Bright Data",
                    keywords=["brightdata"],
                    install={},
                    command="npx",
                    args=["-y", "@brightdata/mcp"],
                    requires_api_key=True,
                    env_var="API_TOKEN",
                    secret_key="BRIGHTDATA_API_TOKEN",
                    env_instructions="Set API_TOKEN",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("API_TOKEN", raising=False)
        monkeypatch.delenv("BRIGHTDATA_API_TOKEN", raising=False)

        result = await gateway_tools.auth_connect(
            {
                "server_name": "brightdata",
                "credential": "bd-token",
                "scope": "user",
            }
        )

        assert result.ok is True
        # Persisted + reported under the namespaced storage key, never the
        # generic API_TOKEN.
        assert result.env_var == "BRIGHTDATA_API_TOKEN"
        assert read_env_file(Path(result.env_path or "")) == {
            "BRIGHTDATA_API_TOKEN": "bd-token"
        }
        assert os.environ["BRIGHTDATA_API_TOKEN"] == "bd-token"
        assert "API_TOKEN" not in os.environ

    @pytest.mark.asyncio
    async def test_auth_connect_runtime_env_var_override_normalizes_to_storage_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """An explicit env_var override naming the runtime var (as env_instructions
        say, "Set API_TOKEN") is normalized to the namespaced storage key rather
        than refused, and still persists namespaced."""
        home = tmp_path / "home"
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(create_mock_tools()),  # type: ignore
            policy_manager=PolicyManager(),
        )

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "brightdata": ServerConfig(
                    name="brightdata",
                    description="Bright Data",
                    keywords=["brightdata"],
                    install={},
                    command="npx",
                    args=["-y", "@brightdata/mcp"],
                    requires_api_key=True,
                    env_var="API_TOKEN",
                    secret_key="BRIGHTDATA_API_TOKEN",
                    env_instructions="Set API_TOKEN",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("API_TOKEN", raising=False)
        monkeypatch.delenv("BRIGHTDATA_API_TOKEN", raising=False)

        result = await gateway_tools.auth_connect(
            {
                "server_name": "brightdata",
                "credential": "bd-token",
                "env_var": "API_TOKEN",  # operator follows "Set API_TOKEN" guidance
                "scope": "user",
            }
        )

        assert result.ok is True
        assert result.env_var == "BRIGHTDATA_API_TOKEN"
        assert read_env_file(Path(result.env_path or "")) == {
            "BRIGHTDATA_API_TOKEN": "bd-token"
        }
        assert "API_TOKEN" not in os.environ

    @pytest.mark.asyncio
    async def test_auth_soak_local_api_key_connect_retry_and_feedback_redaction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        project = tmp_path / "project"
        project.mkdir()
        credential = r'token with spaces # "quotes" and \ slash = value'
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "needs-key": ServerConfig(
                    name="needs-key",
                    description="Needs key",
                    keywords=["auth"],
                    install={},
                    command="needs-key-cmd",
                    args=[],
                    requires_api_key=True,
                    env_var="PMCP_TEST_KEY",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )

        async def start_install(*args: object, **kwargs: object) -> str:
            return "job-auth-soak"

        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr(
            "pmcp.tools.handlers.get_job_manager",
            lambda: types.SimpleNamespace(start_install=start_install),
        )
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.chdir(project)
        monkeypatch.delenv("PMCP_TEST_KEY", raising=False)
        monkeypatch.setattr("pmcp.tools.handlers.load_dotenv", lambda *a, **kw: False)
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
            project_root=project,
        )

        missing = await gateway_tools.provision({"server_name": "needs-key"})
        connected = await gateway_tools.auth_connect(
            {
                "server_name": "needs-key",
                "credential": credential,
                "scope": "project",
            }
        )
        retry = await gateway_tools.provision({"server_name": "needs-key"})
        health = await gateway_tools.health()
        feedback = await gateway_tools.submit_feedback(
            {
                "title": "Auth soak",
                "description": "Authorization: Bearer bearer-secret-token-1234567890",
            }
        )

        assert missing.auth_state == "missing_auth"
        assert missing.missing_env_vars == ["PMCP_TEST_KEY"]
        assert connected.ok is True
        assert connected.env_path == str(project / ".env.pmcp")
        assert read_env_file(project / ".env.pmcp")["PMCP_TEST_KEY"] == credential
        assert retry.ok is True
        assert retry.status == "started"
        assert retry.job_id == "job-auth-soak"
        combined = "\n".join(
            [
                missing.model_dump_json(),
                connected.model_dump_json(),
                retry.model_dump_json(),
                health.model_dump_json(),
                feedback.model_dump_json(),
            ]
        )
        assert credential not in combined
        assert "bearer-secret-token-1234567890" not in combined
        assert "credential_stored" in combined

    @pytest.mark.asyncio
    async def test_auth_connect_project_scope_ignores_unrelated_temp_ancestor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ancestor = tmp_path / "ancestor"
        project = ancestor / "project"
        child = project / "child"
        child.mkdir(parents=True)
        (ancestor / ".git").mkdir()
        monkeypatch.setattr(
            "pmcp.config.loader.tempfile.gettempdir", lambda: str(ancestor)
        )
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )
        monkeypatch.chdir(child)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        result = await gateway_tools.auth_connect(
            {
                "server_name": "custom",
                "env_var": "OPENAI_API_KEY",
                "credential": "test-token",
                "scope": "project",
            }
        )

        assert result.ok is True
        assert result.env_path == str(child / ".env.pmcp")
        assert not (ancestor / ".env.pmcp").exists()
        assert read_env_file(child / ".env.pmcp")["OPENAI_API_KEY"] == "test-token"

    @pytest.mark.asyncio
    async def test_auth_connect_project_scope_uses_explicit_project_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "project"
        other = tmp_path / "other"
        project.mkdir()
        other.mkdir()
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
            project_root=project,
        )
        monkeypatch.chdir(other)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        result = await gateway_tools.auth_connect(
            {
                "server_name": "custom",
                "env_var": "OPENAI_API_KEY",
                "credential": "test-token",
                "scope": "project",
            }
        )

        assert result.ok is True
        assert result.env_path == str(project / ".env.pmcp")
        assert read_env_file(project / ".env.pmcp")["OPENAI_API_KEY"] == "test-token"
        assert not (other / ".env.pmcp").exists()

    @pytest.mark.asyncio
    async def test_auth_connect_rejects_invalid_explicit_env_var(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )
        monkeypatch.chdir(tmp_path)

        result = await gateway_tools.auth_connect(
            {
                "server_name": "custom",
                "env_var": "BAD-NAME",
                "credential": "test-token",
                "scope": "project",
            }
        )

        assert result.ok is False
        assert result.auth_state == "missing_auth"
        assert result.env_var == "BAD-NAME"
        assert "test-token" not in result.message
        assert not (tmp_path / ".env.pmcp").exists()

    @pytest.mark.asyncio
    async def test_auth_connect_round_trips_shell_significant_credential(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )
        credential = r'token with spaces # "quotes" and \ slash = value'
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        result = await gateway_tools.auth_connect(
            {
                "server_name": "custom",
                "env_var": "OPENAI_API_KEY",
                "credential": credential,
                "scope": "project",
            }
        )

        assert result.ok is True
        assert read_env_file(tmp_path / ".env.pmcp")["OPENAI_API_KEY"] == credential
        assert os.environ["OPENAI_API_KEY"] == credential

    @pytest.mark.asyncio
    async def test_auth_connect_rejects_newline_credential_without_injection(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("INJECTED", raising=False)

        result = await gateway_tools.auth_connect(
            {
                "server_name": "custom",
                "env_var": "OPENAI_API_KEY",
                "credential": "first\nINJECTED=second",
                "scope": "project",
            }
        )

        assert result.ok is False
        assert result.auth_state == "missing_auth"
        assert "first" not in result.message
        assert "INJECTED" not in os.environ
        assert not (tmp_path / ".env.pmcp").exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "credential",
        ["oauth-code-123", "password-secret", "Bearer token-secret", "refresh-secret"],
    )
    async def test_auth_connect_url_elicitation_refuses_credential(
        self, credential: str
    ) -> None:
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )

        result = await gateway_tools.auth_connect(
            {
                "server_name": "remote-auth",
                "auth_mode": "url_elicitation",
                "elicitation_id": "consent-1",
                "credential": credential,
                "consent_acknowledged": True,
            }
        )

        assert result.ok is False
        assert result.auth_state == "elicitation_required"
        assert "does not accept credentials" in result.message
        assert credential not in result.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "input_data",
        [
            {"server_name": "remote-auth", "auth_mode": "url_elicitation"},
            {
                "server_name": "remote-auth",
                "auth_mode": "url_elicitation",
                "elicitation_id": "consent-1",
            },
        ],
    )
    async def test_auth_connect_url_elicitation_requires_post_consent_ack(
        self, input_data: dict[str, object]
    ) -> None:
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )

        result = await gateway_tools.auth_connect(input_data)

        assert result.ok is False
        assert result.auth_state == "elicitation_required"
        assert "consent_acknowledged=true" in result.message
        assert result.next_step
        assert "auth_mode='url_elicitation'" in result.next_step

    @pytest.mark.asyncio
    async def test_auth_connect_url_elicitation_redacts_optional_url(self) -> None:
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )

        result = await gateway_tools.auth_connect(
            {
                "server_name": "remote-auth",
                "auth_mode": "url_elicitation",
                "elicitation_id": "consent-1",
                "elicitation_url": "https://auth.example/consent?code=secret",
                "consent_acknowledged": True,
            }
        )

        assert result.ok is True
        assert result.url_elicitation
        assert "secret" not in result.url_elicitation.url
        events = (await gateway_tools.health()).audit_events or []
        assert events[-1].auth_event == "url_elicitation_acknowledged"

    @pytest.mark.asyncio
    async def test_feedback_preview_includes_auth_event_without_secrets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )
        monkeypatch.chdir(tmp_path)
        await gateway_tools.auth_connect(
            {
                "server_name": "custom",
                "env_var": "OPENAI_API_KEY",
                "credential": "sk-test-secret-token-1234567890",
                "scope": "project",
            }
        )

        result = await gateway_tools.submit_feedback(
            {
                "title": "Auth diagnostic",
                "description": "Bearer bearer-secret-token",
            }
        )

        assert result.ok is True
        assert "credential_stored" in result.issue_body
        assert "sk-test-secret-token-1234567890" not in result.issue_body
        assert "bearer-secret-token" not in result.issue_body

    @pytest.mark.asyncio
    async def test_auth_connect_url_elicitation_rejects_non_loopback_http_url(
        self,
    ) -> None:
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )

        result = await gateway_tools.auth_connect(
            {
                "server_name": "remote-auth",
                "auth_mode": "url_elicitation",
                "elicitation_id": "consent-1",
                "elicitation_url": "http://auth.example/consent?code=secret",
                "consent_acknowledged": True,
            }
        )

        assert result.ok is False
        assert result.auth_state == "elicitation_required"
        assert "secret" not in result.message

    @pytest.mark.asyncio
    async def test_invoke_url_elicitation_error_is_structured(self) -> None:
        tool = ToolInfo(
            tool_id="remote-auth::login",
            server_name="remote-auth",
            tool_name="login",
            description="Login",
            short_description="Login",
            input_schema={},
            tags=[],
            risk_hint=RiskHint.LOW,
        )
        client_manager = MockClientManager([tool])

        async def raise_elicitation(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(
                '{"error":{"code":-32042,"data":{"elicitationId":"consent-1",'
                '"url":"https://auth.example/consent?code=secret"}}}'
            )

        client_manager.call_tool = raise_elicitation  # type: ignore[method-assign]
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        result = await gateway_tools.invoke(
            {"tool_id": "remote-auth::login", "arguments": {}}
        )

        assert result.ok is False
        assert result.auth_state == "elicitation_required"
        assert result.url_elicitations
        assert "secret" not in result.url_elicitations[0].url
        events = (await gateway_tools.health()).audit_events or []
        assert events[-1].auth_event == "url_elicitation_required"

    @pytest.mark.asyncio
    async def test_invoke_remote_auth_challenge_audit_event(self) -> None:
        tool = ToolInfo(
            tool_id="remote-auth::read",
            server_name="remote-auth",
            tool_name="read",
            description="Read",
            short_description="Read",
            input_schema={},
            tags=[],
            risk_hint=RiskHint.LOW,
        )
        client_manager = MockClientManager([tool])

        async def raise_challenge(*args: Any, **kwargs: Any) -> Any:
            raise ConnectionError(
                'WWW-Authenticate: Bearer resource_metadata="https://auth.example/pr"'
            )

        client_manager.call_tool = raise_challenge  # type: ignore[method-assign]
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )

        result = await gateway_tools.invoke(
            {"tool_id": "remote-auth::read", "arguments": {}}
        )

        assert result.ok is False
        assert result.auth_state == "missing_auth"
        events = (await gateway_tools.health()).audit_events or []
        assert events[-1].auth_event == "remote_auth_challenge"

    @pytest.mark.asyncio
    async def test_provision_url_elicitation_error_is_structured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()

        async def connect_all(*args: Any, **kwargs: Any) -> list[str]:
            return [
                '{"error":{"code":-32042,"data":{"elicitationId":"consent-1",'
                '"url":"https://auth.example/consent?code=secret"}}}'
            ]

        client_manager.connect_all = connect_all  # type: ignore[method-assign]
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "remote-auth": ServerConfig(
                    name="remote-auth",
                    description="Remote auth",
                    keywords=["remote"],
                    install={},
                    command="",
                    args=[],
                    requires_api_key=False,
                    transport="streamable-http",
                    url="https://mcp.example",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        result = await gateway_tools.provision({"server_name": "remote-auth"})

        assert result.ok is False
        assert result.auth_state == "elicitation_required"
        assert result.auth_mode == "url_elicitation"
        assert result.url_elicitations
        assert "secret" not in result.url_elicitations[0].url
        assert result.next_step
        assert "consent_acknowledged=true" in result.next_step

    @pytest.mark.asyncio
    async def test_connect_server_url_elicitation_error_is_structured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client_manager = MockClientManager()

        async def connect_server(*args: Any, **kwargs: Any) -> list[str]:
            return [
                '{"error":{"code":-32042,"data":{"elicitationId":"consent-1",'
                '"url":"https://auth.example/consent?refresh_token=secret"}}}'
            ]

        client_manager.connect_server = connect_server  # type: ignore[method-assign]
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        configured = [
            ResolvedServerConfig(
                name="remote-auth",
                source="project",
                config=RemoteMcpServerConfig(url="https://mcp.example"),
            )
        ]
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: configured)

        result = await gateway_tools.connect_server({"server_name": "remote-auth"})

        assert result.ok is False
        assert result.auth_state == "elicitation_required"
        assert result.url_elicitations
        assert "secret" not in result.url_elicitations[0].url
        assert result.next_step
        assert "consent_acknowledged=true" in result.next_step

    @pytest.mark.asyncio
    async def test_provision_policy_denied_has_auth_state(self) -> None:
        policy_manager = PolicyManager()
        policy_manager.is_server_allowed = lambda _name: False  # type: ignore[method-assign]
        gateway_tools = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=policy_manager,
        )

        result = await gateway_tools.provision({"server_name": "blocked"})

        assert result.ok is False
        assert result.auth_state == "policy_denied"

    @pytest.mark.asyncio
    async def test_provision_uses_discovered_server_config(self, monkeypatch):
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={},
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])

        gateway_tools._discovered_server_configs["@acme/openbrowser-mcp"] = (
            ServerConfig(
                name="@acme/openbrowser-mcp",
                description="Discovered package",
                keywords=["mcp"],
                install={
                    "linux": ["npx", "-y", "@acme/openbrowser-mcp"],
                    "mac": ["npx", "-y", "@acme/openbrowser-mcp"],
                    "wsl": ["npx", "-y", "@acme/openbrowser-mcp"],
                    "windows": ["npx", "-y", "@acme/openbrowser-mcp"],
                },
                command="npx",
                args=["-y", "@acme/openbrowser-mcp"],
                requires_api_key=False,
            )
        )

        class FakeJobManager:
            async def start_install(self, server_config, platform):
                return "job-123"

        monkeypatch.setattr(
            "pmcp.tools.handlers.get_job_manager", lambda: FakeJobManager()
        )
        monkeypatch.setattr("pmcp.tools.handlers.detect_platform", lambda: "linux")

        result = await gateway_tools.provision({"server_name": "@acme/openbrowser-mcp"})

        assert result.ok is True
        assert result.status == "started"
        assert result.job_id == "job-123"

    @pytest.mark.asyncio
    async def test_update_server_success(self, monkeypatch):
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "playwright": ServerConfig(
                    name="playwright",
                    description="Browser automation",
                    keywords=["browser"],
                    install={},
                    command="npx",
                    args=["@playwright/mcp"],
                    requires_api_key=False,
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        probe_calls: dict[str, object] = {}

        def _fake_probe(command, env=None):
            probe_calls["env"] = env
            return __import__("asyncio").sleep(0, result=(True, "ok"))

        monkeypatch.setattr(gateway_tools, "_run_update_probe_command", _fake_probe)
        monkeypatch.setattr(
            gateway_tools,
            "refresh",
            lambda input_data: __import__("asyncio").sleep(
                0,
                result=types.SimpleNamespace(ok=True),
            ),
        )

        async def fake_get_package_version(command, args, timeout=5.0):
            return ("1.2.3", "npm")

        monkeypatch.setattr(
            "pmcp.tools.handlers.get_package_version", fake_get_package_version
        )

        result = await gateway_tools.update_server({"server_name": "playwright"})

        assert result.ok is True
        assert result.server == "playwright"
        assert result.package_type == "npm"
        assert result.latest_version == "1.2.3"
        # The update probe runs the server's own package code, so it must get a
        # sanitized env (a dict), not inherit the gateway's full environment.
        assert isinstance(probe_calls["env"], dict)

    @pytest.mark.asyncio
    async def test_submit_feedback_preview_includes_telemetry_and_scrubs(
        self, monkeypatch
    ):
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

        result = await gateway_tools.submit_feedback(
            {
                "title": "Context7 failed to resolve docs",
                "description": "Error with token sk-abcdef12345678901234567890 and details.",
                "issue_type": "bug",
                "subordinate_server": "context7",
                "failed_tool_call": "context7::resolve-library-id",
                "confirm_submission": False,
            }
        )

        assert result.ok is True
        assert result.submitted is False
        assert "subordinate_server: context7" in result.issue_body
        assert "failed_tool_call: context7::resolve-library-id" in result.issue_body
        assert "pmcp_version" in result.issue_body
        assert "sk-abcdef12345678901234567890" not in result.issue_body
        assert "[REDACTED]" in result.issue_body
        assert "consent" in result.message.lower()

    @pytest.mark.asyncio
    async def test_submit_feedback_disabled(self):
        client_manager = MockClientManager(create_mock_tools())
        policy_manager = PolicyManager()
        gateway_tools = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
            guidance_config=GuidanceConfig(enable_telemetry=False),
        )

        result = await gateway_tools.submit_feedback(
            {
                "title": "Failure while invoking tool",
                "description": "test",
            }
        )

        assert result.ok is False
        assert result.submitted is False
        assert "disabled" in result.message.lower()


class TestSearchRegistryAndRegister:
    """Tests for gateway.search_registry and gateway.register_discovered_server."""

    @pytest.fixture
    def gateway_tools(self) -> GatewayTools:
        client_manager = MockClientManager()
        policy_manager = PolicyManager()
        return GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=policy_manager,
        )

    @pytest.mark.asyncio
    async def test_search_registry_returns_results_from_mcp_registry(
        self, gateway_tools: GatewayTools, monkeypatch
    ) -> None:
        fake_entries = [
            RegistryServerEntry(
                name="GitHub MCP",
                description="GitHub integration",
                packages=[
                    RegistryPackage(
                        identifier="@modelcontextprotocol/server-github",
                        transport="stdio",
                        env_vars=["GITHUB_TOKEN"],
                    )
                ],
                remotes=[
                    RegistryRemote(
                        transport="streamable-http",
                        url="https://github.example/mcp",
                        headers=["GITHUB_TOKEN"],
                    )
                ],
            ),
            RegistryServerEntry(
                name="GitHub MCP (duplicate)",
                description="GitHub integration duplicate",
                packages=[
                    RegistryPackage(
                        identifier="@modelcontextprotocol/server-github",
                        transport="stdio",
                        env_vars=["GITHUB_TOKEN"],
                    )
                ],
            ),
        ]

        async def fake_matches(
            query: str, *, limit: int = 8
        ) -> list[RegistryServerEntry]:
            return fake_entries[:limit]

        monkeypatch.setattr(
            gateway_tools,
            "_registry_matches",
            fake_matches,
        )

        result = await gateway_tools.search_registry({"query": "github"})

        assert len(result.results) == 1  # duplicate filtered out
        assert result.results[0].package == "@modelcontextprotocol/server-github"
        assert result.results[0].env_vars == ["GITHUB_TOKEN"]
        assert result.results[0].remote_headers == ["GITHUB_TOKEN"]
        assert result.results[0].url == "https://github.example/mcp"
        assert "gateway.register_discovered_server" in result.next_step

    @pytest.mark.asyncio
    async def test_request_capability_returns_registry_candidates(
        self, gateway_tools: GatewayTools, monkeypatch
    ) -> None:
        cache = RegistryCache(
            schema_version="registry-cache.v1",
            source_endpoint="https://registry.example/v0/servers",
            fetched_at="2026-06-15T00:00:00Z",
            servers=[
                RegistryServerEntry(
                    name="acme-remote",
                    description="Acme incident management",
                    packages=[
                        RegistryPackage(
                            identifier="@acme/mcp",
                            transport="streamable-http",
                            env_vars=["ACME_TOKEN"],
                            url="https://mcp.acme.example/mcp",
                        )
                    ],
                    server_card_url="https://acme.example/card.json",
                    protected_resource_metadata_url="https://acme.example/prm",
                    authorization_server_metadata_url="https://auth.acme.example/as",
                    declared_scopes=["incidents:read"],
                    declared_capabilities=["tools"],
                )
            ],
        )

        async def fake_load_registry_candidates() -> RegistryCache:
            return cache

        monkeypatch.setattr(
            gateway_tools, "_load_registry_candidates", fake_load_registry_candidates
        )

        result = await gateway_tools.request_capability(
            {"query": "Find Acme incident management"}
        )

        assert result.status == "candidates"
        assert result.candidates is not None
        candidate = result.candidates[0]
        assert candidate.source == "registry"
        assert candidate.package == "@acme/mcp"
        assert candidate.transport == "streamable-http"
        assert candidate.protected_resource_metadata_url == "https://acme.example/prm"
        assert (
            candidate.authorization_server_metadata_url
            == "https://auth.acme.example/as"
        )
        assert candidate.declared_scopes == ["incidents:read"]
        assert candidate.env_var == "ACME_TOKEN"

    @pytest.mark.asyncio
    async def test_search_registry_handles_empty_results(
        self, gateway_tools: GatewayTools, monkeypatch
    ) -> None:
        async def fake_matches(
            query: str, *, limit: int = 8
        ) -> list[RegistryServerEntry]:
            return []

        monkeypatch.setattr(gateway_tools, "_registry_matches", fake_matches)

        result = await gateway_tools.search_registry({"query": "nonexistent-xyz-tool"})

        assert result.results == []
        assert result.query == "nonexistent-xyz-tool"

    @pytest.mark.asyncio
    async def test_register_discovered_server_stores_config(
        self, gateway_tools: GatewayTools
    ) -> None:
        result = await gateway_tools.register_discovered_server(
            {
                "package": "@modelcontextprotocol/server-github",
                "server_name": "github",
                "env_vars": ["GITHUB_TOKEN"],
                "description": "GitHub MCP server",
            }
        )

        assert result.ok is True
        assert result.registered is True
        assert result.server_name == "github"
        assert "github" in gateway_tools._discovered_server_configs
        config = gateway_tools._discovered_server_configs["github"]
        assert config.command == "npx"
        assert "@modelcontextprotocol/server-github" in config.args
        assert config.requires_api_key is True
        assert config.env_var == "GITHUB_TOKEN"
        assert "gateway.provision" in (result.next_step or "")

    @pytest.mark.asyncio
    async def test_register_then_provision_flow(
        self, gateway_tools: GatewayTools, monkeypatch
    ) -> None:
        """Full agentic discovery flow: register → provision."""
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_manifest",
            lambda: Manifest(
                version="1.0",
                cli_alternatives={},
                servers={},
                discovery_queue_path=".mcp-gateway/discovery_queue.json",
            ),
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        await gateway_tools.register_discovered_server(
            {
                "package": "@modelcontextprotocol/server-github",
                "server_name": "github-ext",
                "env_vars": ["GITHUB_TOKEN"],
            }
        )

        result = await gateway_tools.provision({"server_name": "github-ext"})

        # Should fail because GITHUB_TOKEN is not set
        assert result.ok is False
        assert result.needs_api_key is True
        assert result.env_var == "GITHUB_TOKEN"

    @pytest.mark.asyncio
    async def test_remote_manifest_server_provisions_without_installer(
        self, gateway_tools: GatewayTools, monkeypatch
    ) -> None:
        """Remote manifest servers should connect directly instead of starting jobs."""
        client_manager = cast(MockClientManager, gateway_tools._client_manager)
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "excalidraw": ServerConfig(
                    name="excalidraw",
                    description="Excalidraw whiteboard",
                    keywords=["excalidraw", "whiteboard"],
                    install={},
                    command="",
                    args=[],
                    requires_api_key=False,
                    transport="streamable-http",
                    url="https://mcp.excalidraw.com",
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        fake_jm = types.SimpleNamespace(start_install=pytest.fail)

        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.get_job_manager", lambda: fake_jm)
        monkeypatch.setattr(gateway_tools, "_save_provisioned_registry", lambda: None)

        result = await gateway_tools.provision({"server_name": "excalidraw"})

        assert result.ok is True
        assert result.status == "complete"
        assert len(client_manager.connected_configs) == 1
        connected = client_manager.connected_configs[0]
        assert connected.name == "excalidraw"
        assert connected.config.type == "streamable-http"
        assert connected.config.url == "https://mcp.excalidraw.com"


class TestUpdateProbeProcessCleanup:
    """The update probe must not orphan its process tree on timeout.

    Shipped behaviour before this fix: `_run_update_probe_command` spawned
    WITHOUT `start_new_session` and awaited `wait_for(communicate(), 60)`. On
    timeout `wait_for` cancels the await but never signals the child, so a probe
    against a package that ignores its flag and runs as a server left the whole
    tree alive -- including grandchildren such as the browser `@playwright/mcp`
    launches, which holds the profile SingletonLock and breaks the next launch.

    RUN IN A CHILD INTERPRETER. Demonstrating an orphan requires spawning real
    process trees, and doing that inside the pytest process perturbs state that
    neighbouring tests measure -- `tests/runtime/test_downstream_remote.py`
    asserts on THIS process's open socket count and on the process-global
    `sse_starlette.AppStatus` latch. Running the scenario in a subprocess keeps
    the real-process proof (a mock cannot show an orphan) without touching the
    suite's shared state. Stdlib only; no test-runner plugin needed.
    """

    _SCENARIO = """
import asyncio, os, sys, tempfile

sys.path.insert(0, {src!r})
from pmcp.tools.handlers import GatewayTools
from pmcp.policy.policy import PolicyManager


class _CM:
    def is_server_online(self, name): return False
    def get_all_tools(self): return []


async def main():
    gt = GatewayTools(client_manager=_CM(), policy_manager=PolicyManager())
    fd, pidfile = tempfile.mkstemp()
    os.close(fd)
    # Parent spawns a grandchild, records its pid, then hangs -- the shape of a
    # server that ignores the probe flag. The grandchild outlives the parent
    # unless the whole process GROUP is signalled.
    script = (
        "import subprocess,sys,time;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        "open(%r,'w').write(str(p.pid));"
        "time.sleep(30)" % pidfile
    )
    real_wait_for = asyncio.wait_for

    async def fast(aw, timeout=None):
        return await real_wait_for(aw, timeout=1.5)

    import pmcp.tools.handlers as H
    H.asyncio.wait_for = fast
    try:
        await gt._run_update_probe_command([sys.executable, "-c", script])
    except BaseException:
        pass
    await asyncio.sleep(0.5)
    pid = (open(pidfile).read() or "").strip()
    os.unlink(pidfile)
    if not pid:
        print("SETUP-FAILED"); return
    print("ORPHANED" if os.path.exists("/proc/" + pid) else "REAPED")

asyncio.run(main())
"""

    def _run_scenario(self) -> str:
        import subprocess
        import sys as _sys
        from pathlib import Path as _Path

        src = str(_Path(__file__).resolve().parents[1] / "src")
        out = subprocess.run(
            [_sys.executable, "-c", self._SCENARIO.format(src=src)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return (
            (out.stdout or "").strip().splitlines()[-1]
            if out.stdout.strip()
            else out.stderr[-400:]
        )

    def test_timeout_reaps_the_whole_process_tree(self) -> None:
        result = self._run_scenario()
        assert result == "REAPED", (
            f"probe did not reap its process tree (got {result!r}): a hung probe "
            "leaves grandchildren alive"
        )

    @pytest.mark.asyncio
    async def test_normal_probe_still_returns_output(self) -> None:
        """The happy path is unchanged by the hardening (safe in-process)."""
        import sys as _sys

        gt = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
        )
        ok, output = await gt._run_update_probe_command(
            [_sys.executable, "-c", "print('probe-ok')"]
        )
        assert ok is True
        assert "probe-ok" in output


class TestUpdateServerVersionRepair:
    """A successful gateway.update_server must clear the stale-notice trail
    -- but only once the update is actually *active*, not merely fetched.

    gateway.refresh() (also called from inside update_server, for broader
    reconciliation) never touches the descriptions cache -- only
    refresh_all() (server.py's own startup/refresh path, which update_server
    does not call) does. That alone motivates writing the freshly-probed
    version into the descriptions cache on success.

    But refresh() is *also* diff-based (_refresh_config_unchanged): it
    deliberately leaves a server whose resolved command/args are unchanged
    connected and running, so it never respawns the process. A version-only
    update (`npx pkg@latest`, `uvx --refresh pkg`) never changes argv, so
    refresh() alone never restarts the target server -- the OLD package
    keeps serving requests no matter how many times refresh() runs. Board
    review caught that an earlier version of this fix wrote the new version
    into the cache (and silenced the notice) without ever actually
    restarting the server, which trades a noisy-but-honest notice for a
    silently wrong served version -- worse. update_server now explicitly
    restarts the server (reusing gateway.restart_server's own resolve +
    disconnect + connect machinery) and only bumps/persists the descriptions
    cache -- including regenerating `tools`/`generated_at` from the tool
    list that restart just gave it live -- when that restart actually
    succeeded. If the restart fails or is refused, the notice is left
    exactly alone: it is still telling the truth.
    """

    def _make_manifest_with_playwright(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> Manifest:
        manifest = Manifest(
            version="1.0",
            cli_alternatives={},
            servers={
                "playwright": ServerConfig(
                    name="playwright",
                    description="Browser automation",
                    keywords=["browser"],
                    install={},
                    command="npx",
                    args=["-y", "@playwright/mcp"],
                    requires_api_key=False,
                )
            },
            discovery_queue_path=".mcp-gateway/discovery_queue.json",
        )
        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: manifest)
        # _resolve_lifecycle_config (used by the real gateway.restart_server
        # call update_server now makes) checks configured .mcp.json entries
        # before the manifest -- return none, so "playwright" resolves via
        # the manifest fixture above instead of hitting the real filesystem.
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        return manifest

    def _make_cache(self, version: str = "0.1.0") -> DescriptionsCache:
        return DescriptionsCache(
            generated_at="2024-01-01T00:00:00",
            gateway_version="1.0.0",
            servers={
                "playwright": GeneratedServerDescriptions(
                    package="@playwright/mcp",
                    version=version,
                    generated_at="2024-01-01T00:00:00",
                    capability_summary="Browser automation",
                    tools=[],
                )
            },
        )

    def _wire_success(
        self,
        gt: GatewayTools,
        monkeypatch: pytest.MonkeyPatch,
        *,
        latest_version: str | None,
    ) -> None:
        """Wire update_server's probe + version-fetch + broad refresh().

        Deliberately does NOT stub gateway.restart_server or the
        ClientManager it drives -- that is the exact machinery under test
        here (board review: an earlier version of this fix never actually
        restarted the server). `refresh()` is a separate, broader
        reconciliation pass unrelated to this defect and is stubbed as
        before.
        """

        async def _fake_probe(command, env=None):
            return (True, "ok")

        monkeypatch.setattr(gt, "_run_update_probe_command", _fake_probe)
        monkeypatch.setattr(
            gt,
            "refresh",
            lambda input_data: __import__("asyncio").sleep(
                0, result=types.SimpleNamespace(ok=True)
            ),
        )

        async def fake_get_package_version(command, args, timeout=5.0):
            return (latest_version, "npm")

        monkeypatch.setattr(
            "pmcp.tools.handlers.get_package_version", fake_get_package_version
        )

    @pytest.mark.asyncio
    async def test_refresh_alone_does_not_restart_config_unchanged_online_server(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The invariant that made the original defect invisible.

        A version-only update never changes a server's resolved
        command/args, so refresh()'s diff-based reconcile (which exists to
        avoid needlessly respawning unrelated unchanged servers) leaves it
        connected exactly as-is -- it does not restart it. update_server
        cannot rely on calling refresh() to activate an update; it must
        restart the server explicitly.
        """
        client_manager = MockClientManager()
        config = ResolvedServerConfig(
            name="playwright",
            source="project",
            config=LocalMcpServerConfig(command="npx", args=["-y", "@playwright/mcp"]),
        )
        client_manager.add_connected_server(config)
        gt = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
        )
        patch_refresh_config_sources(monkeypatch, [config])
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_enabled_auto_start", lambda **_: {"playwright"}
        )
        monkeypatch.setattr(gt, "_load_provisioned_registry", lambda: {})

        result = await gt.refresh({"reason": "test"})

        assert result.ok is True
        assert "disconnect_server:playwright:True" not in client_manager.events
        assert "connect_server:playwright" not in client_manager.events
        assert client_manager.is_server_online("playwright") is True

    @pytest.mark.asyncio
    async def test_update_server_explicitly_restarts_the_server(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """update_server must actually restart the server, not just refresh().

        Proves the board-review fix: with refresh() fully stubbed out (see
        _wire_success), the only way disconnect/connect events can appear on
        the client manager is through update_server's own explicit restart
        call.
        """
        self._make_manifest_with_playwright(monkeypatch)
        cache = self._make_cache(version="0.1.0")
        client_manager = MockClientManager()
        gt = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=cache,
        )
        self._wire_success(gt, monkeypatch, latest_version="0.2.0")

        result = await gt.update_server({"server_name": "playwright"})

        assert result.ok is True
        assert result.restarted is True
        assert "disconnect_server:playwright:False" in client_manager.events
        assert "connect_server:playwright" in client_manager.events
        assert client_manager.is_server_online("playwright") is True

    @pytest.mark.asyncio
    async def test_update_server_bumps_descriptions_cache_version(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """On success, the descriptions-cache version is rewritten to latest."""
        self._make_manifest_with_playwright(monkeypatch)
        cache = self._make_cache(version="0.1.0")
        gt = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=cache,
        )
        self._wire_success(gt, monkeypatch, latest_version="0.2.0")

        result = await gt.update_server({"server_name": "playwright"})

        assert result.ok is True
        assert result.restarted is True
        assert result.latest_version == "0.2.0"
        assert cache.servers["playwright"].version == "0.2.0"

    @pytest.mark.asyncio
    async def test_update_server_persists_version_across_restart(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The repaired version must survive a restart, not just live in memory.

        Simulates a restart by discarding the in-process GatewayTools and
        re-loading the cache file from disk, the same way
        GatewayServer.initialize() does at startup.
        """
        from pmcp.manifest.refresher import load_descriptions_cache

        self._make_manifest_with_playwright(monkeypatch)
        cache_path = tmp_path / "descriptions.yaml"
        cache = self._make_cache(version="0.1.0")
        gt = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=cache,
            descriptions_cache_path=cache_path,
        )
        self._wire_success(gt, monkeypatch, latest_version="0.2.0")

        result = await gt.update_server({"server_name": "playwright"})
        assert result.ok is True

        reloaded = load_descriptions_cache(cache_path)
        assert reloaded is not None
        assert reloaded.servers["playwright"].version == "0.2.0"

    @pytest.mark.asyncio
    async def test_update_server_failed_probe_does_not_bump_version(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A failed update probe must not touch the recorded version."""
        self._make_manifest_with_playwright(monkeypatch)
        cache = self._make_cache(version="0.1.0")
        gt = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=cache,
        )

        async def _fake_probe_fails(command, env=None):
            return (False, "npm ERR! network timeout")

        monkeypatch.setattr(gt, "_run_update_probe_command", _fake_probe_fails)

        result = await gt.update_server({"server_name": "playwright"})

        assert result.ok is False
        assert cache.servers["playwright"].version == "0.1.0"

    @pytest.mark.asyncio
    async def test_update_server_none_latest_version_leaves_old_value(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A failed version fetch (None) must leave the old version intact.

        Writing "unknown" (or any other sentinel) would be worse than leaving
        the previous, at-least-plausible value in place.
        """
        self._make_manifest_with_playwright(monkeypatch)
        cache = self._make_cache(version="0.1.0")
        gt = GatewayTools(
            client_manager=MockClientManager(),  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=cache,
        )
        self._wire_success(gt, monkeypatch, latest_version=None)

        result = await gt.update_server({"server_name": "playwright"})

        assert result.ok is True
        assert result.restarted is True
        assert result.latest_version is None
        assert cache.servers["playwright"].version == "0.1.0"

    @pytest.mark.asyncio
    async def test_update_server_restart_failure_leaves_notice_intact(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A refused/failed restart must NOT bump the version or silence the notice.

        The package may have been fetched successfully, but if the running
        server was never restarted onto it, the gateway is still serving the
        OLD version -- the "update available" notice is still correct and
        must be left alone. `ok` must reflect that the update was not fully
        activated.
        """
        self._make_manifest_with_playwright(monkeypatch)
        cache = self._make_cache(version="0.1.0")
        client_manager = MockClientManager()
        client_manager.set_server_online("playwright")
        client_manager.add_pending_request("playwright")
        gt = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=cache,
        )
        self._wire_success(gt, monkeypatch, latest_version="0.2.0")

        # Default force=False: MockClientManager.disconnect_server refuses a
        # restart while a request is pending, exactly like real ClientManager.
        result = await gt.update_server({"server_name": "playwright"})

        assert result.ok is False
        assert result.restarted is False
        assert "could not restart" in result.message.lower()
        assert cache.servers["playwright"].version == "0.1.0"

    @pytest.mark.asyncio
    async def test_update_server_force_cancels_pending_and_activates(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """force=true cancels pending work so the restart can proceed.

        Surfaces the cancellation count on the output, mirroring
        gateway.restart_server's own cancelled_request_count.
        """
        self._make_manifest_with_playwright(monkeypatch)
        cache = self._make_cache(version="0.1.0")
        client_manager = MockClientManager()
        client_manager.set_server_online("playwright")
        request = client_manager.add_pending_request("playwright")
        gt = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=cache,
        )
        self._wire_success(gt, monkeypatch, latest_version="0.2.0")

        result = await gt.update_server({"server_name": "playwright", "force": True})

        assert result.ok is True
        assert result.restarted is True
        assert result.cancelled_request_count == 1
        assert request.future.cancelled()
        assert cache.servers["playwright"].version == "0.2.0"

    @pytest.mark.asyncio
    async def test_update_server_regenerates_tools_from_live_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Restart gives update_server a live tool list -- use it.

        GeneratedServerDescriptions.version is documented as "Package version
        when generated". Bumping only `version` while leaving `tools` at the
        pre-update snapshot would make the record lie about its own
        provenance, and would keep serving the pre-update tool list to
        offline catalog_search even after a package update that changed
        tools. Regenerate `tools`/`generated_at` together with `version`,
        from the connection the restart just made -- no extra stdio
        round-trip. `capability_summary` is deliberately left as-is (it only
        feeds the startup MCP-instructions text, not live catalog_search).
        """
        self._make_manifest_with_playwright(monkeypatch)
        cache = self._make_cache(version="0.1.0")
        old_generated_at = cache.servers["playwright"].generated_at
        old_summary = cache.servers["playwright"].capability_summary
        live_tools = [
            ToolInfo(
                tool_id="playwright::new_tool",
                server_name="playwright",
                tool_name="new_tool",
                description="A tool that only exists in the new version",
                short_description="New in 0.2.0",
                input_schema={"type": "object", "properties": {}},
                tags=["new"],
                risk_hint=RiskHint.HIGH,
            )
        ]
        client_manager = MockClientManager(live_tools)
        gt = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=cache,
        )
        self._wire_success(gt, monkeypatch, latest_version="0.2.0")

        result = await gt.update_server({"server_name": "playwright"})

        assert result.ok is True
        entry = cache.servers["playwright"]
        assert [t.name for t in entry.tools] == ["new_tool"]
        assert entry.tools[0].tags == ["new"]
        assert entry.tools[0].risk_hint == "high"
        assert entry.generated_at != old_generated_at
        # Deliberately unchanged -- see docstring.
        assert entry.capability_summary == old_summary

    @pytest.mark.asyncio
    async def test_update_server_uses_configured_override_not_manifest(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A .mcp.json override must drive the probe, not the manifest.

        Board review (round 2): update_server previously resolved its probe
        target via _get_server_config_for_update (manifest/discovered only)
        while gateway.restart_server resolves via _resolve_lifecycle_config
        (.mcp.json overrides the manifest). For a server configured in both
        places with a *different*, unpinned package, that meant probing and
        version-checking the manifest's package while restarting a
        completely different one -- and if both happened to succeed, writing
        the manifest package's "latest" into the cache for a server that was
        never actually running it. Unifying resolution around
        _resolve_lifecycle_config must make the .mcp.json override win end
        to end: the probe, the version check, AND the restarted process must
        all be the *same* configured package.
        """
        self._make_manifest_with_playwright(monkeypatch)
        configured_override = ResolvedServerConfig(
            name="playwright",
            source="project",
            config=LocalMcpServerConfig(
                command="npx", args=["-y", "different-playwright-fork"]
            ),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_configs", lambda **_: [configured_override]
        )
        cache = self._make_cache(version="0.1.0")
        client_manager = MockClientManager()
        gt = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=cache,
        )

        async def _fake_probe(command, env=None):
            return (True, "ok")

        monkeypatch.setattr(gt, "_run_update_probe_command", _fake_probe)
        monkeypatch.setattr(
            gt,
            "refresh",
            lambda input_data: __import__("asyncio").sleep(
                0, result=types.SimpleNamespace(ok=True)
            ),
        )

        async def fake_get_package_version(command, args, timeout=5.0):
            # The manifest's bare "@playwright/mcp" would resolve to a
            # DIFFERENT (wrong) answer than the .mcp.json override -- if
            # this is ever called with the manifest's args, the config
            # resolution has diverged from what actually gets restarted.
            if "different-playwright-fork" in args:
                return ("9.9.9", "npm")
            return ("0.2.0", "npm")  # manifest's package -- must not be used

        monkeypatch.setattr(
            "pmcp.tools.handlers.get_package_version", fake_get_package_version
        )

        result = await gt.update_server({"server_name": "playwright"})

        assert result.ok is True
        assert result.latest_version == "9.9.9"
        assert cache.servers["playwright"].version == "9.9.9"
        # The server that was actually restarted ran the configured override.
        assert client_manager.connected_configs[-1].config.args == [
            "-y",
            "different-playwright-fork",
        ]

    @pytest.mark.asyncio
    async def test_update_server_refuses_pinned_configured_override(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A version-pinned .mcp.json override must refuse, not silently lie.

        The exact board-review failure scenario: manifest has an unpinned
        `npx -y @playwright/mcp`, .mcp.json pins `@playwright/mcp@1.2.3`
        (README documents this pinning pattern for index-it-mcp as the
        supported override channel). Restarting the pinned server succeeds
        (it's a perfectly valid process to run) -- the bug was recording
        upstream's unrelated "latest" as if it were now running. The probe
        must never even be attempted: there is nothing @latest can do while
        the pin stands.
        """
        self._make_manifest_with_playwright(monkeypatch)
        pinned_override = ResolvedServerConfig(
            name="playwright",
            source="project",
            config=LocalMcpServerConfig(
                command="npx", args=["-y", "@playwright/mcp@1.2.3"]
            ),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_configs", lambda **_: [pinned_override]
        )
        cache = self._make_cache(version="0.1.0")
        client_manager = MockClientManager()
        gt = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=cache,
        )

        probe_calls: list[Any] = []

        async def _fake_probe(command, env=None):
            probe_calls.append(command)
            return (True, "ok")

        monkeypatch.setattr(gt, "_run_update_probe_command", _fake_probe)

        async def fake_get_package_version(command, args, timeout=5.0):
            return ("9.9.9", "npm")  # upstream's real latest -- must be ignored

        monkeypatch.setattr(
            "pmcp.tools.handlers.get_package_version", fake_get_package_version
        )

        result = await gt.update_server({"server_name": "playwright"})

        assert result.ok is False
        assert "pinned" in result.message.lower()
        assert "1.2.3" in result.message
        assert "project" in result.message.lower()
        assert probe_calls == []  # never even attempted
        assert "disconnect_server:playwright:False" not in client_manager.events
        assert cache.servers["playwright"].version == "0.1.0"

    @pytest.mark.asyncio
    async def test_update_server_refuses_pinned_docker_tag(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A ``:tag``-pinned docker image must refuse, exactly like an npm pin.

        Board review round 3: ``detect_package_type`` strips the tag off a
        docker image reference (``image = raw.split(":")[0]``), so without a
        docker branch in ``_detect_effective_version_pin`` this tool would
        ``docker pull acme/server:latest``, restart the still-pinned
        ``1.2.3`` config, and then record the freshly-pulled digest as the
        running version -- the same silent misreporting the npm pin gate
        exists to prevent, just via a different package manager.
        """
        self._make_manifest_with_playwright(monkeypatch)
        pinned_override = ResolvedServerConfig(
            name="playwright",
            source="project",
            config=LocalMcpServerConfig(
                command="docker", args=["run", "--rm", "acme/server:1.2.3"]
            ),
        )
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_configs", lambda **_: [pinned_override]
        )
        cache = self._make_cache(version="0.1.0")
        client_manager = MockClientManager()
        gt = GatewayTools(
            client_manager=client_manager,  # type: ignore
            policy_manager=PolicyManager(),
            descriptions_cache=cache,
        )

        probe_calls: list[Any] = []

        async def _fake_probe(command, env=None):
            probe_calls.append(command)
            return (True, "ok")

        monkeypatch.setattr(gt, "_run_update_probe_command", _fake_probe)

        async def fake_get_package_version(command, args, timeout=5.0):
            return ("sha256:beefbeef", "docker")  # must be ignored

        monkeypatch.setattr(
            "pmcp.tools.handlers.get_package_version", fake_get_package_version
        )

        result = await gt.update_server({"server_name": "playwright"})

        assert result.ok is False
        assert "pinned" in result.message.lower()
        assert "1.2.3" in result.message
        assert probe_calls == []  # never even attempted
        assert cache.servers["playwright"].version == "0.1.0"

    def test_detect_effective_version_pin_matrix(self):
        """Pin detection per package manager, including the not-a-pin cases."""
        from pmcp.tools.handlers import _detect_effective_version_pin as detect

        # npm: explicit version pins, @latest is not a pin, bare is not a pin.
        assert detect("npm", "npx", ["-y", "@playwright/mcp@1.2.3"]) == "1.2.3"
        assert detect("npm", "npx", ["-y", "@playwright/mcp@latest"]) is None
        assert detect("npm", "npx", ["-y", "@playwright/mcp"]) is None

        # docker: only the final path segment can carry a tag, so a registry
        # host with a port must not read as a pin of "5000".
        assert detect("docker", "docker", ["run", "acme/server:1.2.3"]) == "1.2.3"
        assert detect("docker", "docker", ["run", "acme/server:latest"]) is None
        assert detect("docker", "docker", ["run", "acme/server"]) is None
        assert detect("docker", "docker", ["run", "registry:5000/img"]) is None
        assert detect("docker", "docker", ["run", "registry:5000/img:2.0"]) == "2.0"

        # cargo: pins via a separate --version flag, both spellings.
        assert detect("cargo", "cargo", ["install", "srv", "--version", "1.0"]) == "1.0"
        assert detect("cargo", "cargo", ["install", "srv", "--version=1.0"]) == "1.0"
        assert detect("cargo", "cargo", ["install", "srv"]) is None

        # pypi/uvx inline == pin.
        assert detect("pypi", "uvx", ["srv==1.2.3"]) == "1.2.3"
        assert detect("pypi", "uvx", ["srv"]) is None


class TestInvokeErrorPaths:
    """Tests for gateway.invoke error handling paths."""

    def _make_tool(
        self,
        required_fields: list[str] | None = None,
        execution: dict[str, Any] | None = None,
    ) -> ToolInfo:
        return ToolInfo(
            tool_id="svc::do_thing",
            server_name="svc",
            tool_name="do_thing",
            description="A test tool",
            short_description="A test tool",
            input_schema={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": required_fields or [],
            },
            tags=[],
            risk_hint=RiskHint.LOW,
            execution=execution,
        )

    def _make_gateway_tools(
        self, tool: ToolInfo | None = None, call_tool_side_effect: Any = None
    ) -> GatewayTools:
        t = tool or self._make_tool()
        cm = MockClientManager([t])
        cm.set_server_online("svc")
        if call_tool_side_effect is not None:

            async def _raise(*args: Any, **kwargs: Any) -> Any:
                raise call_tool_side_effect

            cm.call_tool = _raise  # type: ignore[method-assign]
        return GatewayTools(
            client_manager=cm,  # type: ignore
            policy_manager=PolicyManager(),
        )

    @pytest.mark.asyncio
    async def test_invoke_timeout(self) -> None:
        gt = self._make_gateway_tools(call_tool_side_effect=TimeoutError())
        result = await gt.invoke({"tool_id": "svc::do_thing", "arguments": {}})
        assert result.ok is False
        assert "E303" in (result.errors or [""])[0]

    @pytest.mark.asyncio
    async def test_invoke_connection_error(self) -> None:
        gt = self._make_gateway_tools(call_tool_side_effect=ConnectionError("refused"))
        result = await gt.invoke({"tool_id": "svc::do_thing", "arguments": {}})
        assert result.ok is False
        assert "E201" in (result.errors or [""])[0]

    @pytest.mark.asyncio
    async def test_invoke_generic_exception(self) -> None:
        gt = self._make_gateway_tools(call_tool_side_effect=RuntimeError("boom"))
        result = await gt.invoke({"tool_id": "svc::do_thing", "arguments": {}})
        assert result.ok is False
        assert "E302" in (result.errors or [""])[0]
        # Message must not contain raw traceback — just the short error text
        assert len((result.errors or [""])[0]) < 2000

    @pytest.mark.asyncio
    async def test_invoke_missing_required_arg(self) -> None:
        tool = self._make_tool(required_fields=["q"])
        gt = self._make_gateway_tools(tool=tool)
        # call_tool should never be called; the check is pre-dispatch
        called = False

        async def _should_not_be_called(*args: Any, **kwargs: Any) -> Any:
            nonlocal called
            called = True
            return {}

        gt._client_manager.call_tool = _should_not_be_called  # type: ignore[method-assign]
        result = await gt.invoke({"tool_id": "svc::do_thing", "arguments": {}})
        assert result.ok is False
        assert "E304" in (result.errors or [""])[0]
        assert not called

    @pytest.mark.asyncio
    async def test_invoke_policy_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gt = self._make_gateway_tools()
        monkeypatch.setattr(gt._policy_manager, "is_tool_allowed", lambda _: False)
        result = await gt.invoke({"tool_id": "svc::do_thing", "arguments": {}})
        assert result.ok is False
        assert "E402" in (result.errors or [""])[0]

    @pytest.mark.asyncio
    async def test_tenant_code_mode_invoke_policy_denied_audit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tool = ToolInfo(
            tool_id="tenant-code-mode::run_script",
            server_name="tenant-code-mode",
            tool_name="run_script",
            description="Submit sandbox code",
            short_description="Submit sandbox code",
            input_schema={"type": "object", "properties": {}},
            tags=["tenant", "sandbox"],
            risk_hint=RiskHint.MEDIUM,
        )
        gt = self._make_gateway_tools(tool=tool)
        cast(Any, gt._client_manager).set_server_online("tenant-code-mode")
        monkeypatch.setattr(gt._policy_manager, "is_tool_allowed", lambda _: False)

        result = await gt.invoke(
            {
                "tool_id": "tenant-code-mode::run_script",
                "arguments": {},
                "trace_context": {"authorization": "Bearer should-not-log"},
            }
        )
        health = await gt.health()

        assert result.ok is False
        assert result.auth_state == "policy_denied"
        assert "E402" in (result.errors or [""])[0]
        assert health.audit_events is not None
        event = health.audit_events[-1]
        assert event.server_name == "tenant-code-mode"
        assert event.tool_id == "tenant-code-mode::run_script"
        assert event.auth_state == "policy_denied"
        assert "should-not-log" not in str(event)

    @pytest.mark.asyncio
    async def test_invoke_optional_task_returns_task_not_result(self) -> None:
        tool = self._make_tool(execution={"taskSupport": "optional"})
        gt = self._make_gateway_tools(tool=tool)

        result = await gt.invoke(
            {
                "tool_id": "svc::do_thing",
                "arguments": {},
                "task": {"metadata": {"reason": "slow"}},
            }
        )

        assert result.ok is True
        assert result.result is None
        assert result.task is not None
        assert result.task.task_id == "task-1"

    @pytest.mark.asyncio
    async def test_tasks_result_applies_output_redaction(self) -> None:
        gt = self._make_gateway_tools()
        await gt.invoke(
            {
                "tool_id": "svc::do_thing",
                "arguments": {},
                "task": {"metadata": {"reason": "slow"}},
            }
        )

        result = await gt.tasks_result(
            {
                "server_name": "svc",
                "task_id": "task-1",
                "options": {"redact_secrets": True, "max_output_chars": 100},
            }
        )

        assert result.ok is True
        assert "sk-secret" not in str(result.result)
        assert result.task is not None
        assert result.task.status == "completed"
        assert result.raw_size_estimate > 0

    @pytest.mark.asyncio
    async def test_invoke_task_metadata_redacts_by_default(self) -> None:
        tool = self._make_tool(execution={"taskSupport": "optional"})
        gt = self._make_gateway_tools(tool=tool)
        cm = cast(Any, gt._client_manager)

        async def call_tool(
            tool_id: str,
            args: dict[str, Any],
            timeout_ms: int,
            *,
            task: Any = None,
            trace_context: Any = None,
        ) -> dict[str, Any]:
            task_record = McpTaskRecord(
                task_id="task-secret",
                status="working",
                status_message="queued with sk-abcdef123456",
                raw={
                    "statusMessage": "queued with ghp_1234567890abcdef",
                    "metadata": {"token": "github_pat_1234567890abcdef"},
                },
                server_name="svc",
                tool_id=tool_id,
            )
            cm.tasks[("svc", "task-secret")] = task_record
            return {"task": {"taskId": "task-secret", "status": "working"}}

        cm.call_tool = call_tool

        result = await gt.invoke(
            {
                "tool_id": "svc::do_thing",
                "arguments": {},
                "task": {"metadata": {"reason": "slow"}},
            }
        )

        assert result.task is not None
        serialized = result.model_dump_json()
        assert "sk-abcdef123456" not in serialized
        assert "ghp_1234567890abcdef" not in serialized
        assert "github_pat_1234567890abcdef" not in serialized

    @pytest.mark.asyncio
    async def test_tasks_result_redacts_result_and_task_metadata_by_default(
        self,
    ) -> None:
        gt = self._make_gateway_tools()
        await gt.invoke(
            {
                "tool_id": "svc::do_thing",
                "arguments": {},
                "task": {"metadata": {"reason": "slow"}},
            }
        )
        cm = cast(Any, gt._client_manager)
        task = cm.tasks[("svc", "task-1")]
        task.status_message = "complete with sk-abcdef123456"
        task.raw["statusMessage"] = "complete with ghp_1234567890abcdef"
        task.raw["metadata"] = {"token": "github_pat_1234567890abcdef"}

        result = await gt.tasks_result({"server_name": "svc", "task_id": "task-1"})

        assert result.ok is True
        serialized = result.model_dump_json()
        assert "sk-secret" not in serialized
        assert "sk-abcdef123456" not in serialized
        assert "ghp_1234567890abcdef" not in serialized
        assert "github_pat_1234567890abcdef" not in serialized

    @pytest.mark.asyncio
    async def test_tasks_list_and_get_redact_task_metadata_by_default(self) -> None:
        gt = self._make_gateway_tools()
        await gt.invoke(
            {
                "tool_id": "svc::do_thing",
                "arguments": {},
                "task": {"metadata": {"reason": "slow"}},
            }
        )
        cm = cast(Any, gt._client_manager)
        task = cm.tasks[("svc", "task-1")]
        task.status_message = "queued with sk-abcdef123456"
        task.raw["statusMessage"] = "queued with ghp_1234567890abcdef"
        task.raw["metadata"] = {
            "token": "github_pat_1234567890abcdef",
            "unknown": "kept",
        }

        listed = await gt.tasks_list({"server_name": "svc"})
        got = await gt.tasks_get({"server_name": "svc", "task_id": "task-1"})

        assert listed.ok is True
        assert got.ok is True
        assert listed.tasks[0].raw["metadata"]["unknown"] == "kept"
        serialized = listed.model_dump_json() + got.model_dump_json()
        assert "sk-abcdef123456" not in serialized
        assert "ghp_1234567890abcdef" not in serialized
        assert "github_pat_1234567890abcdef" not in serialized

    @pytest.mark.asyncio
    async def test_task_emitting_surfaces_sanitize_task_metadata(self) -> None:
        tool = self._make_tool(execution={"taskSupport": "optional"})
        gt = self._make_gateway_tools(tool=tool)
        cm = cast(Any, gt._client_manager)

        async def call_tool(
            tool_id: str,
            args: dict[str, Any],
            timeout_ms: int,
            *,
            task: Any = None,
            trace_context: Any = None,
        ) -> dict[str, Any]:
            task_record = McpTaskRecord(
                task_id="task-secret",
                status="working",
                status_message="task has sk-abcdef123456",
                raw={
                    "statusMessage": "task has ghp_1234567890abcdef",
                    "metadata": {
                        "token": "github_pat_1234567890abcdef",
                        "unknown": "kept",
                    },
                },
                server_name="svc",
                tool_id=tool_id,
            )
            cm.tasks[("svc", "task-secret")] = task_record
            return {"task": {"taskId": "task-secret", "status": "working"}}

        cm.call_tool = call_tool

        outputs = [
            await gt.invoke(
                {
                    "tool_id": "svc::do_thing",
                    "arguments": {},
                    "task": {"metadata": {"reason": "slow"}},
                }
            ),
            await gt.tasks_list({"server_name": "svc"}),
            await gt.tasks_get({"server_name": "svc", "task_id": "task-secret"}),
            await gt.tasks_result({"server_name": "svc", "task_id": "task-secret"}),
        ]

        for output in outputs:
            serialized = output.model_dump_json()
            assert "sk-abcdef123456" not in serialized
            assert "ghp_1234567890abcdef" not in serialized
            assert "github_pat_1234567890abcdef" not in serialized

    @pytest.mark.asyncio
    async def test_tasks_cancel_reports_missing_task(self) -> None:
        gt = self._make_gateway_tools()

        result = await gt.tasks_cancel({"server_name": "svc", "task_id": "missing"})

        assert result.ok is False
        assert result.status == "not_found"

    @pytest.mark.asyncio
    async def test_conformance_task_gateway_route_and_audit(self) -> None:
        tool = self._make_tool(execution={"taskSupport": "optional"})
        gt = self._make_gateway_tools(tool=tool)

        invoked = await gt.invoke(
            {
                "tool_id": "svc::do_thing",
                "arguments": {},
                "task": {"metadata": {"reason": "slow"}},
            }
        )
        listed = await gt.tasks_list({"server_name": "svc"})
        got = await gt.tasks_get({"server_name": "svc", "task_id": "task-1"})
        result = await gt.tasks_result({"server_name": "svc", "task_id": "task-1"})
        cancelled = await gt.tasks_cancel({"server_name": "svc", "task_id": "task-1"})
        health = await gt.health()

        assert invoked.ok is True
        assert invoked.task is not None
        assert invoked.task.task_id == "task-1"
        assert [task.task_id for task in listed.tasks] == ["task-1"]
        assert got.task is not None
        assert got.task.task_id == "task-1"
        assert result.task is not None
        assert result.task.status == "completed"
        assert cancelled.ok is True
        assert cancelled.task is not None
        assert cancelled.task.status == "completed"
        assert health.audit_events is not None
        methods = [event.method for event in health.audit_events[-5:]]
        assert methods == [
            "gateway.invoke",
            "gateway.tasks_list",
            "gateway.tasks_get",
            "gateway.tasks_result",
            "gateway.tasks_cancel",
        ]
        assert health.audit_events[-1].task_id == "task-1"

    @pytest.mark.asyncio
    async def test_gateway_task_surfaces_preserve_sdk_metadata(self) -> None:
        tool = self._make_tool(execution={"taskSupport": "optional"})
        gt = self._make_gateway_tools(tool=tool)

        invoked = await gt.invoke(
            {
                "tool_id": "svc::do_thing",
                "arguments": {},
                "task": {"metadata": {"reason": "slow"}},
            }
        )
        listed = await gt.tasks_list({"server_name": "svc"})
        got = await gt.tasks_get({"server_name": "svc", "task_id": "task-1"})
        result = await gt.tasks_result(
            {
                "server_name": "svc",
                "task_id": "task-1",
                "options": {"redact_secrets": True},
            }
        )
        health = await gt.health()

        assert invoked.task is not None
        assert invoked.task.status_message == "queued by SDK host"
        assert invoked.task.created_at == pytest.approx(
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp()
        )
        assert invoked.task.updated_at == pytest.approx(
            datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc).timestamp()
        )
        assert invoked.task.ttl == 300
        assert invoked.task.poll_interval == 2.5
        assert invoked.task.raw["metadata"] == {"unknown": "kept"}
        assert listed.tasks[0].raw["lastUpdatedAt"] == "2026-01-02T03:04:06Z"
        assert got.task is not None
        assert got.task.task_id == "task-1"
        assert result.task is not None
        assert result.task.status == "completed"
        assert "sk-secret" not in str(result.result)
        assert health.audit_events is not None
        assert [event.task_id for event in health.audit_events[-3:]] == [
            None,
            "task-1",
            "task-1",
        ]

    @pytest.mark.asyncio
    async def test_gateway_task_surfaces_forward_requestor_context(self) -> None:
        tool = self._make_tool(execution={"taskSupport": "optional"})
        gt = self._make_gateway_tools(tool=tool)
        context = {
            "tenantId": "tenant_test",
            "actorId": "actor_test",
            "authScopes": ["run:create"],
            "testTenant": True,
        }

        await gt.invoke(
            {
                "tool_id": "svc::do_thing",
                "arguments": {},
                "task": {"requestor_context": context},
            }
        )
        await gt.tasks_list({"server_name": "svc", "requestor_context": context})
        await gt.tasks_get(
            {
                "server_name": "svc",
                "task_id": "task-1",
                "requestor_context": context,
            }
        )
        await gt.tasks_result(
            {
                "server_name": "svc",
                "task_id": "task-1",
                "requestor_context": context,
            }
        )
        await gt.tasks_cancel(
            {
                "server_name": "svc",
                "task_id": "task-1",
                "requestor_context": context,
            }
        )

        manager = cast(Any, gt._client_manager)
        assert manager.task_request_contexts == [context, context, context, context]

    @pytest.mark.asyncio
    async def test_tenant_code_mode_task_lifecycle_uses_downstream_task_ids(
        self,
    ) -> None:
        tool = ToolInfo(
            tool_id="tenant-code-mode::run_script",
            server_name="tenant-code-mode",
            tool_name="run_script",
            description="Submit sandbox code for execution",
            short_description="Run sandbox code",
            input_schema={"type": "object", "properties": {}},
            tags=["tenant", "sandbox"],
            risk_hint=RiskHint.MEDIUM,
            execution={"taskSupport": "optional"},
        )
        gt = self._make_gateway_tools(tool=tool)
        cast(Any, gt._client_manager).set_server_online("tenant-code-mode")

        invoked = await gt.invoke(
            {
                "tool_id": "tenant-code-mode::run_script",
                "arguments": {"language": "python"},
                "task": {"metadata": {"run_kind": "smoke"}},
            }
        )
        listed = await gt.tasks_list({"server_name": "tenant-code-mode"})
        got = await gt.tasks_get(
            {"server_name": "tenant-code-mode", "task_id": "task-1"}
        )
        result = await gt.tasks_result(
            {
                "server_name": "tenant-code-mode",
                "task_id": "task-1",
                "options": {"redact_secrets": True, "max_output_chars": 100},
            }
        )
        cancelled = await gt.tasks_cancel(
            {"server_name": "tenant-code-mode", "task_id": "task-1"}
        )
        missing = await gt.tasks_cancel(
            {"server_name": "tenant-code-mode", "task_id": "tenant-code-mode::local-1"}
        )
        health = await gt.health()

        assert invoked.task is not None
        assert invoked.task.task_id == "task-1"
        assert [task.task_id for task in listed.tasks] == ["task-1"]
        assert got.task is not None
        assert got.task.task_id == "task-1"
        assert result.ok is True
        assert "sk-secret" not in str(result.result)
        assert cancelled.ok is True
        assert missing.ok is False
        assert missing.status == "not_found"
        assert health.audit_events is not None
        task_events = [
            event
            for event in health.audit_events
            if event.server_name == "tenant-code-mode"
        ]
        assert [event.task_id for event in task_events[-4:]] == [
            "task-1",
            "task-1",
            "task-1",
            "tenant-code-mode::local-1",
        ]

    @pytest.mark.asyncio
    async def test_tenant_code_mode_policy_denied_blocks_task_surfaces(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tool = ToolInfo(
            tool_id="tenant-code-mode::run_script",
            server_name="tenant-code-mode",
            tool_name="run_script",
            description="Submit sandbox code",
            short_description="Submit sandbox code",
            input_schema={"type": "object", "properties": {}},
            tags=["tenant", "sandbox"],
            risk_hint=RiskHint.MEDIUM,
            execution={"taskSupport": "optional"},
        )
        gt = self._make_gateway_tools(tool=tool)
        monkeypatch.setattr(
            gt._policy_manager,
            "is_server_allowed",
            lambda name: name != "tenant-code-mode",
        )

        listed = await gt.tasks_list({"server_name": "tenant-code-mode"})
        got = await gt.tasks_get(
            {"server_name": "tenant-code-mode", "task_id": "task-1"}
        )
        result = await gt.tasks_result(
            {"server_name": "tenant-code-mode", "task_id": "task-1"}
        )
        cancelled = await gt.tasks_cancel(
            {"server_name": "tenant-code-mode", "task_id": "task-1"}
        )
        health = await gt.health()

        assert listed.ok is False
        assert got.ok is False
        assert result.ok is False
        assert cancelled.ok is False
        assert cancelled.status == "policy_denied"
        assert health.audit_events is not None
        assert [event.auth_state for event in health.audit_events[-4:]] == [
            "policy_denied",
            "policy_denied",
            "policy_denied",
            "policy_denied",
        ]
        assert "secret" not in str(health.audit_events[-4:])

    @pytest.mark.asyncio
    async def test_tenant_code_mode_policy_denied_is_omitted_from_unscoped_task_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tool = ToolInfo(
            tool_id="tenant-code-mode::run_script",
            server_name="tenant-code-mode",
            tool_name="run_script",
            description="Submit sandbox code",
            short_description="Submit sandbox code",
            input_schema={"type": "object", "properties": {}},
            tags=["tenant", "sandbox"],
            risk_hint=RiskHint.MEDIUM,
            execution={"taskSupport": "optional"},
        )
        gt = self._make_gateway_tools(tool=tool)
        manager = cast(Any, gt._client_manager)
        manager.tasks[("tenant-code-mode", "task-1")] = McpTaskRecord(
            task_id="task-1",
            status="working",
            raw={"taskId": "task-1", "status": "working"},
            server_name="tenant-code-mode",
            tool_id="tenant-code-mode::run_script",
        )
        monkeypatch.setattr(
            gt._policy_manager,
            "is_server_allowed",
            lambda name: name != "tenant-code-mode",
        )

        listed = await gt.tasks_list({})

        assert listed.ok is True
        assert listed.tasks == []
        assert manager.list_task_calls == []


class TestSanitizeError:
    """Unit tests for GatewayTools._sanitize_error."""

    def _err(self, msg: str) -> Exception:
        return Exception(msg)

    def test_strips_absolute_path(self) -> None:
        e = self._err(
            "/home/user/.local/lib/python3.10/site-packages/pkg/mod.py: no module"
        )
        result = GatewayTools._sanitize_error(e)
        assert "/home" not in result
        assert "mod.py" in result

    def test_truncates_at_400(self) -> None:
        e = self._err("x" * 500)
        result = GatewayTools._sanitize_error(e)
        assert len(result) == 400

    def test_no_path_unchanged_except_truncation(self) -> None:
        e = self._err("simple error message")
        result = GatewayTools._sanitize_error(e)
        assert result == "simple error message"

    def test_multiple_paths_all_stripped(self) -> None:
        e = self._err("/tmp/foo.py and /var/run/bar.sock failed")
        result = GatewayTools._sanitize_error(e)
        assert "/tmp" not in result
        assert "/var" not in result
        assert "foo.py" in result
        assert "bar.sock" in result

    def test_returns_string(self) -> None:
        result = GatewayTools._sanitize_error(ValueError("boom"))
        assert isinstance(result, str)

    def test_redacts_auth_challenge_secrets(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1"
        result = GatewayTools._sanitize_error(
            ValueError(
                'WWW-Authenticate: Bearer resource_metadata="'
                'https://auth.example/pr?ticket=secret-ticket", '
                f'error_description="token {jwt}"'
            )
        )

        assert "secret-ticket" not in result
        assert jwt not in result
        assert "%5BREDACTED%5D" in result or "[REDACTED]" in result

    def test_feedback_scrubbing_uses_shared_auth_sanitizer(self) -> None:
        tools = GatewayTools(MockClientManager(), PolicyManager())
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1"

        scrubbed = tools._scrub_sensitive_text(
            "operator@example.test saw "
            f"https://user:pass@example.test/cb?session=secret-session {jwt}"
        )

        assert "operator@example.test" not in scrubbed
        assert "user:pass" not in scrubbed
        assert "secret-session" not in scrubbed
        assert jwt not in scrubbed

    def test_feedback_events_are_sanitized_before_preview(self) -> None:
        tools = GatewayTools(MockClientManager(), PolicyManager())

        tools._record_feedback_event(
            "invoke_error",
            {"error": "https://user:pass@example.test/cb?jwt=secret-jwt"},
        )

        assert "user:pass" not in json.dumps(tools._feedback_events)
        assert "secret-jwt" not in json.dumps(tools._feedback_events)
