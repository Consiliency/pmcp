"""Config Loader - Discovers and merges MCP server configs from Claude config files."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pmcp.types import (
    LocalMcpServerConfig,
    McpConfigFile,
    McpServerConfig,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pmcp.manifest.loader import ServerConfig as ManifestServerConfig

logger = logging.getLogger(__name__)

DEFAULT_USER_CONFIG_PATHS = [
    Path.home() / ".mcp.json",
    Path.home() / ".claude" / ".mcp.json",
]


def _coerce_server_entry(config: object) -> dict[str, Any] | None:
    """Coerce legacy MCP server entries into typed local/remote records."""
    if not isinstance(config, dict):
        return None

    coerced: dict[str, Any] = dict(config)
    entry_type = coerced.get("type")
    command = coerced.get("command")
    url = coerced.get("url")

    if isinstance(entry_type, str):
        if entry_type in {"local", "remote", "sse", "http", "streamable-http"}:
            if entry_type == "local":
                if "command" not in coerced:
                    coerced["command"] = ""
                elif not isinstance(coerced.get("command"), str):
                    return None
            else:
                if not (isinstance(url, str) and url):
                    return None
            return coerced
        return None

    # Legacy explicit local command form.
    if isinstance(command, str) and command:
        coerced["type"] = "local"
        return coerced

    # Legacy remote URL form with no type.
    if isinstance(url, str) and url:
        coerced["type"] = "remote"
        return coerced

    # Partial local override (for manifest default merge).
    has_local_override = any(key in coerced for key in ("args", "cwd", "env"))
    if command is None and has_local_override:
        coerced["type"] = "local"
        coerced["command"] = ""
        return coerced

    return None


def find_project_root(start_dir: Path) -> Path | None:
    """Find project root by looking for .mcp.json or common project markers."""
    current = start_dir.resolve()

    while current != current.parent:
        # Check for .mcp.json
        if (current / ".mcp.json").exists():
            return current
        # Check for common project markers
        if (
            (current / ".git").exists()
            or (current / "package.json").exists()
            or (current / "pyproject.toml").exists()
        ):
            return current
        current = current.parent

    return None


def parse_json_file(file_path: Path) -> McpConfigFile | None:
    """Safely parse a JSON config file."""
    try:
        if not file_path.exists():
            return None
        content = file_path.read_text()
        data = json.loads(content)

        raw_servers = data.get("mcpServers")
        if isinstance(raw_servers, dict):
            filtered_servers: dict[str, dict[str, object]] = {}
            skipped_count = 0
            for name, config in raw_servers.items():
                coerced = _coerce_server_entry(config)
                if coerced is None:
                    skipped_count += 1
                    continue
                filtered_servers[name] = coerced

            if skipped_count > 0:
                logger.info(
                    f"Skipping {skipped_count} invalid MCP server entries in {file_path}"
                )
            data["mcpServers"] = filtered_servers

        return McpConfigFile.model_validate(data)
    except Exception as e:
        logger.warning(f"Failed to parse config file {file_path}: {e}")
        return None


def _merge_manifest_defaults(
    name: str,
    config: LocalMcpServerConfig,
    manifest_servers: dict[str, "ManifestServerConfig"] | None,
) -> LocalMcpServerConfig | None:
    """Merge a partial config with manifest defaults when possible."""
    if config.command:
        return config

    if not manifest_servers:
        logger.warning(
            f"Skipping server '{name}' - command missing and manifest unavailable"
        )
        return None

    manifest_server = manifest_servers.get(name)
    if not manifest_server:
        logger.warning(
            f"Skipping server '{name}' - command missing and no manifest default found"
        )
        return None

    merged = config.model_copy(deep=True)
    merged.command = manifest_server.command
    merged.args = [*manifest_server.args, *config.args]
    return merged


def normalize_server_config(
    config: LocalMcpServerConfig, base_path: Path
) -> LocalMcpServerConfig:
    """Normalize server config (resolve relative paths)."""
    normalized = config.model_copy()

    # Resolve relative command paths
    if normalized.command and not Path(normalized.command).is_absolute():
        # Check if it's a relative path (contains / or \) vs bare command
        if "/" in normalized.command or "\\" in normalized.command:
            normalized.command = str((base_path / normalized.command).resolve())

    # Resolve cwd if relative
    if normalized.cwd and not Path(normalized.cwd).is_absolute():
        normalized.cwd = str((base_path / normalized.cwd).resolve())

    return normalized


def make_tool_id(server_name: str, tool_name: str) -> str:
    """Generate a unique tool ID from server name and tool name."""
    return f"{server_name}::{tool_name}"


def parse_tool_id(tool_id: str) -> tuple[str, str] | None:
    """Parse tool ID back to (server_name, tool_name)."""
    parts = tool_id.split("::")
    if len(parts) != 2:
        return None
    return (parts[0], parts[1])


def load_configs(
    project_root: Path | None = None,
    user_config_paths: Sequence[Path] | None = None,
    custom_config_path: Path | None = None,
) -> list[ResolvedServerConfig]:
    """
    Load and merge MCP configs from multiple sources.

    Precedence: project > user > custom (project overrides user on name collision)
    """
    configs: list[ResolvedServerConfig] = []
    seen_servers: set[str] = set()
    manifest_servers: dict[str, ManifestServerConfig] | None = None

    try:
        from pmcp.manifest.loader import load_manifest

        manifest_servers = load_manifest().servers
    except Exception as e:
        logger.debug(f"Manifest defaults unavailable during config load: {e}")

    def build_resolved_config(
        name: str,
        config: McpServerConfig,
        source: Literal["project", "user", "custom"],
        base_path: Path,
    ) -> ResolvedServerConfig | None:
        if isinstance(config, RemoteMcpServerConfig):
            resolved_config: McpServerConfig = config
        else:
            normalized = normalize_server_config(config, base_path)
            local_merged = _merge_manifest_defaults(name, normalized, manifest_servers)
            if not local_merged:
                return None
            resolved_config = local_merged
        return ResolvedServerConfig(
            name=name,
            source=cast(Literal["project", "user", "custom", "manifest"], source),
            config=resolved_config,
        )

    # 1. Load project config (highest priority)
    resolved_project_root = project_root or find_project_root(Path.cwd())
    if resolved_project_root:
        project_config_path = resolved_project_root / ".mcp.json"
        project_config = parse_json_file(project_config_path)

        if project_config and project_config.mcpServers:
            logger.info(f"Loaded project config from {project_config_path}")
            for name, config in project_config.mcpServers.items():
                if name not in seen_servers:
                    resolved = build_resolved_config(
                        name,
                        config,
                        "project",
                        resolved_project_root,
                    )
                    if resolved:
                        configs.append(resolved)
                        seen_servers.add(name)

    # 2. Load user configs
    user_paths = (
        list(user_config_paths)
        if user_config_paths is not None
        else DEFAULT_USER_CONFIG_PATHS
    )
    for user_path in user_paths:
        user_config = parse_json_file(user_path)

        if user_config and user_config.mcpServers:
            logger.info(f"Loaded user config from {user_path}")
            for name, config in user_config.mcpServers.items():
                if name not in seen_servers:
                    resolved = build_resolved_config(
                        name,
                        config,
                        "user",
                        user_path.parent,
                    )
                    if resolved:
                        configs.append(resolved)
                        seen_servers.add(name)
                else:
                    logger.debug(
                        f"Skipping user server '{name}' - already defined in project config"
                    )

    # 3. Load custom config (if specified via env or option)
    resolved_custom_path = custom_config_path
    if not resolved_custom_path:
        env_path = os.environ.get("PMCP_CONFIG")
        if env_path:
            resolved_custom_path = Path(env_path)

    if resolved_custom_path:
        custom_config = parse_json_file(resolved_custom_path)

        if custom_config and custom_config.mcpServers:
            logger.info(f"Loaded custom config from {resolved_custom_path}")
            for name, config in custom_config.mcpServers.items():
                if name not in seen_servers:
                    resolved = build_resolved_config(
                        name,
                        config,
                        "custom",
                        resolved_custom_path.parent,
                    )
                    if resolved:
                        configs.append(resolved)
                        seen_servers.add(name)

    logger.info(
        f"Loaded {len(configs)} server configs from {len(seen_servers)} unique servers"
    )
    return configs


def load_disabled_auto_start(
    project_root: Path | None = None,
    user_config_paths: Sequence[Path] | None = None,
    custom_config_path: Path | None = None,
) -> set[str]:
    """Load disableAutoStart lists from all config sources."""
    disabled: set[str] = set()

    # Check project config
    resolved_project_root = project_root or find_project_root(Path.cwd())
    if resolved_project_root:
        project_config = parse_json_file(resolved_project_root / ".mcp.json")
        if project_config and project_config.disableAutoStart:
            disabled.update(project_config.disableAutoStart)

    # Check user configs
    user_paths = (
        list(user_config_paths)
        if user_config_paths is not None
        else DEFAULT_USER_CONFIG_PATHS
    )
    for user_path in user_paths:
        user_config = parse_json_file(user_path)
        if user_config and user_config.disableAutoStart:
            disabled.update(user_config.disableAutoStart)

    # Check custom config
    resolved_custom_path = custom_config_path
    if not resolved_custom_path:
        env_path = os.environ.get("PMCP_CONFIG")
        if env_path:
            resolved_custom_path = Path(env_path)

    if resolved_custom_path:
        custom_config = parse_json_file(resolved_custom_path)
        if custom_config and custom_config.disableAutoStart:
            disabled.update(custom_config.disableAutoStart)

    if disabled:
        logger.info(f"Auto-start disabled for: {', '.join(sorted(disabled))}")

    return disabled


def manifest_server_to_config(server: "ManifestServerConfig") -> ResolvedServerConfig:
    """Convert a manifest ServerConfig to a ResolvedServerConfig.

    Args:
        server: Server configuration from manifest.yaml

    Returns:
        ResolvedServerConfig compatible with ClientManager
    """
    if server.url:
        remote_type = server.transport
        if remote_type == "local":
            remote_type = "streamable-http"
        return ResolvedServerConfig(
            name=server.name,
            source="manifest",
            config=RemoteMcpServerConfig(
                type=cast(
                    Literal["remote", "sse", "http", "streamable-http"],
                    remote_type,
                ),
                url=server.url,
                headers=server.headers,
            ),
        )

    # Build env dict if server requires API key
    env: dict[str, str] | None = None
    if server.env_var:
        env_value = os.environ.get(server.env_var, "")
        if env_value:
            env = {server.env_var: env_value}

    return ResolvedServerConfig(
        name=server.name,
        source="manifest",
        config=LocalMcpServerConfig(
            command=server.command,
            args=server.args,
            env=env,
        ),
    )
