"""Config Loader - Discovers and merges MCP server configs from Claude config files."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pmcp.types import (
    ConfigSourceInfo,
    ConfigSourceName,
    LocalMcpServerConfig,
    McpConfigFile,
    McpServerConfig,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    StartupPolicyDiagnostic,
    StartupPolicyOperation,
    StartupPolicyOutput,
    StartupPolicyPreview,
    StartupPolicySource,
)
from pmcp.remote_auth import build_remote_header_env_lookup, resolve_remote_headers

if TYPE_CHECKING:
    from pmcp.manifest.loader import ServerConfig as ManifestServerConfig

logger = logging.getLogger(__name__)

# Documents the default user config locations. Do NOT read this frozen list at
# runtime — it captures Path.home() at import time, so a changed/monkeypatched
# HOME (e.g. in tests, or a re-homed process) would be ignored. Use
# default_user_config_paths() instead, which resolves Path.home() at call time
# (mirrors manifest/loader.py's overlay path handling).
DEFAULT_USER_CONFIG_PATHS = [
    Path.home() / ".mcp.json",
    Path.home() / ".claude" / ".mcp.json",
]


def default_user_config_paths() -> list[Path]:
    """User-level config paths, resolved from Path.home() at call time."""
    return [
        Path.home() / ".mcp.json",
        Path.home() / ".claude" / ".mcp.json",
    ]


class StartupSkipReason(str, Enum):
    """Reason a server was excluded during startup resolution."""

    POLICY_DENIED = "policy_denied"
    MISSING_AUTH = "missing_auth"
    UNKNOWN_AUTO_START = "unknown_auto_start"
    UNKNOWN_PROVISIONED = "unknown_provisioned"


@dataclass(frozen=True)
class StartupSkip:
    """A server skipped by startup resolution with machine-readable context."""

    name: str
    reason: StartupSkipReason
    source: Literal["configured", "manifest", "auto_start", "provisioned"]
    env_var: str | None = None
    missing_env_vars: list[str] | None = None


@dataclass(frozen=True)
class StartupResolution:
    """Resolved lazy/eager startup groups plus skipped entries."""

    lazy_configs: list[ResolvedServerConfig]
    eager_configs: list[ResolvedServerConfig]
    skipped: list[StartupSkip]


@dataclass(frozen=True)
class StartupObservation:
    """Observed startup policy details for health/status output."""

    name: str
    startup_policy: Literal["eager", "lazy", "skipped", "unknown"]
    startup_source: str | None = None
    startup_skip_reason: str | None = None
    startup_env_var: str | None = None
    missing_env_vars: list[str] | None = None


StartupObservationSnapshot = dict[str, StartupObservation]


@dataclass(frozen=True)
class LoadedConfigSource:
    """Parsed config plus source/path metadata."""

    source: ConfigSourceName
    path: Path
    exists: bool
    config: McpConfigFile | None = None
    raw_data: dict[str, Any] | None = None
    error: str | None = None

    def info(self) -> ConfigSourceInfo:
        return ConfigSourceInfo(
            source=self.source,
            path=str(self.path),
            exists=self.exists,
            error=self.error,
        )


def build_startup_observation_snapshot(
    resolution: StartupResolution,
) -> StartupObservationSnapshot:
    """Convert a startup resolution into a name-keyed observation snapshot."""
    snapshot: StartupObservationSnapshot = {}
    for config in resolution.eager_configs:
        snapshot[config.name] = StartupObservation(
            name=config.name,
            startup_policy="eager",
            startup_source=config.source,
        )
    for config in resolution.lazy_configs:
        snapshot[config.name] = StartupObservation(
            name=config.name,
            startup_policy="lazy",
            startup_source=config.source,
        )
    for skipped in resolution.skipped:
        snapshot[skipped.name] = StartupObservation(
            name=skipped.name,
            startup_policy="skipped",
            startup_source=skipped.source,
            startup_skip_reason=skipped.reason.value,
            startup_env_var=skipped.env_var,
            missing_env_vars=skipped.missing_env_vars or [],
        )
    return snapshot


def summarize_startup_resolution(resolution: StartupResolution) -> dict[str, int]:
    """Return concise counters for startup/refresh policy logs."""
    return {
        "eager": len(resolution.eager_configs),
        "lazy": len(resolution.lazy_configs),
        "skipped": len(resolution.skipped),
        "policy_denied": sum(
            1
            for skipped in resolution.skipped
            if skipped.reason == StartupSkipReason.POLICY_DENIED
        ),
        "missing_auth": sum(
            1
            for skipped in resolution.skipped
            if skipped.reason == StartupSkipReason.MISSING_AUTH
        ),
        "unknown_auto_start": sum(
            1
            for skipped in resolution.skipped
            if skipped.reason == StartupSkipReason.UNKNOWN_AUTO_START
        ),
    }


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
    temp_root = Path(tempfile.gettempdir()).resolve()
    home_root = Path.home().resolve()

    while current != current.parent:
        if current == temp_root:
            return None
        # $HOME is already covered by the user config source; treating it as a
        # project root would double-attribute ~/.mcp.json (project + user).
        if current == home_root:
            return None
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


def _iter_config_source_paths(
    project_root: Path | None = None,
    user_config_paths: Sequence[Path] | None = None,
    custom_config_path: Path | None = None,
) -> list[tuple[ConfigSourceName, Path]]:
    paths: list[tuple[ConfigSourceName, Path]] = []
    resolved_project_root = project_root or find_project_root(Path.cwd())
    if resolved_project_root:
        paths.append(("project", resolved_project_root / ".mcp.json"))

    user_paths = (
        list(user_config_paths)
        if user_config_paths is not None
        else default_user_config_paths()
    )
    paths.extend(("user", path) for path in user_paths)

    resolved_custom_path = custom_config_path
    if not resolved_custom_path:
        env_path = os.environ.get("PMCP_CONFIG")
        if env_path:
            resolved_custom_path = Path(env_path)
    if resolved_custom_path:
        paths.append(("custom", resolved_custom_path))

    return paths


def _read_config_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text())
    except Exception as exc:
        return None, f"invalid_json: {exc}"
    if not isinstance(data, dict):
        return None, "config_root_not_object"
    return data, None


def _parse_config_or_warn(path: Path) -> McpConfigFile | None:
    """Parse a config file, surfacing a WARNING when it exists but is malformed.

    ``parse_json_file`` returns ``None`` both for a missing file and for a
    malformed one, so on the hot ``load_configs`` path a broken ``.mcp.json``
    would silently disable all of its servers. This wrapper keeps startup
    fail-soft while logging the path and reason at WARNING so ``pmcp status``
    and operators can see that the file's servers were dropped.
    """
    if not path.exists():
        return None
    config = parse_json_file(path)
    if config is None:
        _raw, error = _read_config_object(path)
        logger.warning(
            f"Ignoring malformed config {path} "
            f"({error or 'invalid_mcp_config'}); its servers are disabled"
        )
    return config


def load_config_sources(
    project_root: Path | None = None,
    user_config_paths: Sequence[Path] | None = None,
    custom_config_path: Path | None = None,
) -> list[LoadedConfigSource]:
    """Load MCP config files with source/path attribution."""
    sources: list[LoadedConfigSource] = []
    for source, path in _iter_config_source_paths(
        project_root=project_root,
        user_config_paths=user_config_paths,
        custom_config_path=custom_config_path,
    ):
        raw_data, error = _read_config_object(path)
        config = None
        if raw_data is not None:
            config = parse_json_file(path)
            if config is None:
                error = error or "invalid_mcp_config"
        sources.append(
            LoadedConfigSource(
                source=source,
                path=path,
                exists=path.exists(),
                config=config,
                raw_data=raw_data,
                error=error,
            )
        )
    return sources


def get_startup_policy(
    project_root: Path | None = None,
    user_config_paths: Sequence[Path] | None = None,
    custom_config_path: Path | None = None,
    known_server_names: Collection[str] = (),
) -> StartupPolicyOutput:
    """Return persisted autoStart/disableAutoStart entries by source."""
    sources = load_config_sources(project_root, user_config_paths, custom_config_path)
    known = set(known_server_names)
    output_sources: list[StartupPolicySource] = []
    diagnostics: list[StartupPolicyDiagnostic] = []

    for source in sources:
        auto_start = list(source.config.autoStart) if source.config else []
        disabled = list(source.config.disableAutoStart) if source.config else []
        output_sources.append(
            StartupPolicySource(
                source=source.source,
                path=str(source.path),
                exists=source.exists,
                autoStart=auto_start,
                disableAutoStart=disabled,
                error=source.error,
            )
        )
        if source.error:
            diagnostics.append(
                StartupPolicyDiagnostic(
                    code="invalid_source",
                    message=source.error,
                    source=source.source,
                    path=str(source.path),
                )
            )
        for name in sorted(set(auto_start) & set(disabled)):
            diagnostics.append(
                StartupPolicyDiagnostic(
                    code="auto_start_disabled_conflict",
                    message=f"{name} is listed in both autoStart and disableAutoStart",
                    source=source.source,
                    path=str(source.path),
                    server_name=name,
                )
            )
        if known:
            for name in sorted(set(auto_start) - known):
                diagnostics.append(
                    StartupPolicyDiagnostic(
                        code="stale_auto_start",
                        message=f"{name} is listed in autoStart but no server definition was found",
                        source=source.source,
                        path=str(source.path),
                        server_name=name,
                    )
                )
            for name in sorted(set(disabled) - known):
                diagnostics.append(
                    StartupPolicyDiagnostic(
                        code="stale_disable_auto_start",
                        message=f"{name} is listed in disableAutoStart but no server definition was found",
                        source=source.source,
                        path=str(source.path),
                        server_name=name,
                    )
                )
    return StartupPolicyOutput(sources=output_sources, diagnostics=diagnostics)


def _select_policy_source(
    operation: StartupPolicyOperation,
    sources: Sequence[LoadedConfigSource],
) -> LoadedConfigSource | None:
    if operation.path:
        target = Path(operation.path).expanduser()
        for source in sources:
            if source.path == target:
                return source
        return LoadedConfigSource(
            source=operation.source or "custom",
            path=target,
            exists=target.exists(),
            config=None,
            raw_data=None,
        )
    if not operation.source:
        return None
    candidates = [source for source in sources if source.source == operation.source]
    existing = [source for source in candidates if source.exists]
    if len(existing) == 1:
        return existing[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as tmp_file:
        json.dump(data, tmp_file, indent=2)
        tmp_file.write("\n")
        tmp_path = Path(tmp_file.name)
    tmp_path.replace(path)


def set_startup_policy(
    operation: StartupPolicyOperation,
    project_root: Path | None = None,
    user_config_paths: Sequence[Path] | None = None,
    custom_config_path: Path | None = None,
) -> StartupPolicyPreview:
    """Preview or apply a source-scoped autoStart mutation."""
    sources = load_config_sources(project_root, user_config_paths, custom_config_path)
    target = _select_policy_source(operation, sources)
    if target is None:
        return StartupPolicyPreview(
            ok=False,
            dry_run=operation.dry_run,
            diagnostics=[
                StartupPolicyDiagnostic(
                    code="ambiguous_source",
                    message="Select exactly one startup policy source or path",
                    source=operation.source,
                    path=operation.path,
                )
            ],
            message="No startup policy change was applied.",
        )

    raw_data, error = _read_config_object(target.path)
    if error:
        return StartupPolicyPreview(
            ok=False,
            source=target.source,
            path=str(target.path),
            dry_run=operation.dry_run,
            diagnostics=[
                StartupPolicyDiagnostic(
                    code="invalid_source",
                    message=error,
                    source=target.source,
                    path=str(target.path),
                )
            ],
            message="No startup policy change was applied.",
        )
    data = raw_data if raw_data is not None else {}

    names = sorted({name for name in operation.names if name})
    before = data.get("autoStart", [])
    if before is None:
        before = []
    if not isinstance(before, list) or not all(
        isinstance(name, str) for name in before
    ):
        return StartupPolicyPreview(
            ok=False,
            source=target.source,
            path=str(target.path),
            dry_run=operation.dry_run,
            diagnostics=[
                StartupPolicyDiagnostic(
                    code="invalid_auto_start",
                    message="autoStart must be a list of strings",
                    source=target.source,
                    path=str(target.path),
                )
            ],
            message="No startup policy change was applied.",
        )

    current = set(before)
    if operation.operation == "add":
        updated = current | set(names)
    elif operation.operation == "remove":
        updated = current - set(names)
    else:
        updated = set(names)
    after = sorted(updated)
    changed = sorted(current) != after
    should_write = operation.apply and not operation.dry_run and changed
    if should_write:
        data["autoStart"] = after
        _atomic_write_json(target.path, data)

    message = "Startup policy preview generated."
    if operation.apply and operation.dry_run:
        message = "Dry run only; set dry_run=false with apply=true to write."
    elif operation.apply and changed:
        message = "Startup policy updated."
    elif not changed:
        message = "No startup policy change needed."

    return StartupPolicyPreview(
        ok=True,
        source=target.source,
        path=str(target.path),
        changed=changed,
        dry_run=not should_write,
        before_autoStart=sorted(current),
        after_autoStart=after,
        message=message,
        next_step='gateway.refresh(reason="startup_policy_changed")'
        if should_write
        else None,
    )


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
        project_config = _parse_config_or_warn(project_config_path)

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
        else default_user_config_paths()
    )
    for user_path in user_paths:
        user_config = _parse_config_or_warn(user_path)

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
        custom_config = _parse_config_or_warn(resolved_custom_path)

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
        else default_user_config_paths()
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


def load_enabled_auto_start(
    project_root: Path | None = None,
    user_config_paths: Sequence[Path] | None = None,
    custom_config_path: Path | None = None,
) -> set[str]:
    """Load autoStart lists from all config sources."""
    enabled: set[str] = set()

    # Check project config
    resolved_project_root = project_root or find_project_root(Path.cwd())
    if resolved_project_root:
        project_config = parse_json_file(resolved_project_root / ".mcp.json")
        if project_config and project_config.autoStart:
            enabled.update(project_config.autoStart)

    # Check user configs
    user_paths = (
        list(user_config_paths)
        if user_config_paths is not None
        else default_user_config_paths()
    )
    for user_path in user_paths:
        user_config = parse_json_file(user_path)
        if user_config and user_config.autoStart:
            enabled.update(user_config.autoStart)

    # Check custom config
    resolved_custom_path = custom_config_path
    if not resolved_custom_path:
        env_path = os.environ.get("PMCP_CONFIG")
        if env_path:
            resolved_custom_path = Path(env_path)

    if resolved_custom_path:
        custom_config = parse_json_file(resolved_custom_path)
        if custom_config and custom_config.autoStart:
            enabled.update(custom_config.autoStart)

    if enabled:
        logger.info(f"Auto-start enabled for: {', '.join(sorted(enabled))}")

    return enabled


def is_legacy_manifest_auto_start_enabled(
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return true when legacy manifest auto-start compatibility is enabled."""
    values = env if env is not None else os.environ
    return values.get("PMCP_LEGACY_MANIFEST_AUTOSTART") == "1"


def _coerce_manifest_servers(
    manifest_servers: Mapping[str, "ManifestServerConfig"]
    | Iterable["ManifestServerConfig"]
    | None,
) -> dict[str, "ManifestServerConfig"]:
    if manifest_servers is None:
        return {}
    if isinstance(manifest_servers, Mapping):
        return dict(manifest_servers)
    return {server.name: server for server in manifest_servers}


def _manifest_server_to_config(
    server: "ManifestServerConfig",
    env_lookup: Callable[[str], str | None],
) -> ResolvedServerConfig:
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
                protected_resource_metadata_url=server.protected_resource_metadata_url,
                authorization_server_metadata_url=server.authorization_server_metadata_url,
                oidc_issuer_url=server.oidc_issuer_url,
                oidc_discovery_url=server.oidc_discovery_url,
                client_id_metadata_document_url=server.client_id_metadata_document_url,
                declared_scopes=server.declared_scopes,
                supports_url_elicitation=server.supports_url_elicitation,
            ),
        )

    # Build env dict if server requires API key
    env: dict[str, str] | None = None
    if server.env_var:
        env_value = env_lookup(server.env_var)
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


def manifest_server_to_config(server: "ManifestServerConfig") -> ResolvedServerConfig:
    """Convert a manifest ServerConfig to a ResolvedServerConfig.

    Args:
        server: Server configuration from manifest.yaml

    Returns:
        ResolvedServerConfig compatible with ClientManager
    """
    return _manifest_server_to_config(server, os.environ.get)


def resolve_startup_configs(
    configured_configs: Sequence[ResolvedServerConfig],
    manifest_servers: Mapping[str, "ManifestServerConfig"]
    | Iterable["ManifestServerConfig"]
    | None = None,
    enabled_auto_start: Collection[str] = (),
    disabled_auto_start: Collection[str] = (),
    provisioned_server_names: Collection[str] = (),
    is_server_allowed: Callable[[str], bool] = lambda _name: True,
    is_auth_available: Callable[[str], bool] = lambda _env_var: True,
    legacy_manifest_auto_start: bool = False,
    project_root: Path | None = None,
) -> StartupResolution:
    """Classify already-loaded server definitions into lazy/eager startup groups."""
    manifest_by_name = _coerce_manifest_servers(manifest_servers)
    enabled = set(enabled_auto_start)
    disabled = set(disabled_auto_start)
    provisioned = set(provisioned_server_names)

    lazy_configs: list[ResolvedServerConfig] = []
    eager_configs: list[ResolvedServerConfig] = []
    skipped: list[StartupSkip] = []

    configured_names: set[str] = set()
    classified_names: set[str] = set()
    remote_header_env_lookup = build_remote_header_env_lookup(project_root)

    def add_config(
        config: ResolvedServerConfig,
        *,
        eager: bool,
        source: Literal["configured", "manifest", "provisioned"],
        manifest_server: "ManifestServerConfig" | None = None,
    ) -> None:
        if not is_server_allowed(config.name):
            skipped.append(
                StartupSkip(
                    name=config.name,
                    reason=StartupSkipReason.POLICY_DENIED,
                    source=source,
                )
            )
            classified_names.add(config.name)
            return

        if eager and manifest_server and manifest_server.requires_api_key:
            env_var = manifest_server.env_var
            if env_var and not is_auth_available(env_var):
                skipped.append(
                    StartupSkip(
                        name=config.name,
                        reason=StartupSkipReason.MISSING_AUTH,
                        source=source,
                        env_var=env_var,
                    )
                )
                classified_names.add(config.name)
                return

        if eager and isinstance(config.config, RemoteMcpServerConfig):
            resolution = resolve_remote_headers(
                config.config.headers,
                remote_header_env_lookup,
            )
            if resolution.missing_env_vars:
                skipped.append(
                    StartupSkip(
                        name=config.name,
                        reason=StartupSkipReason.MISSING_AUTH,
                        source=source,
                        env_var=resolution.missing_env_vars[0],
                        missing_env_vars=resolution.missing_env_vars,
                    )
                )
                classified_names.add(config.name)
                return

        if eager:
            eager_configs.append(config)
        else:
            lazy_configs.append(config)
        classified_names.add(config.name)

    for config in configured_configs:
        if config.name in configured_names:
            continue
        configured_names.add(config.name)
        add_config(
            config,
            eager=config.name in enabled and config.name not in disabled,
            source="configured",
        )

    for name, server in manifest_by_name.items():
        if name in configured_names or name in classified_names:
            continue

        eager = name in enabled and name not in disabled
        if legacy_manifest_auto_start and server.auto_start and name not in disabled:
            eager = True

        config = _manifest_server_to_config(server, lambda _env_var: None)
        source: Literal["manifest", "provisioned"] = (
            "provisioned" if name in provisioned else "manifest"
        )
        add_config(config, eager=eager, source=source, manifest_server=server)

    known_names = configured_names | set(manifest_by_name)
    for name in sorted(enabled - known_names):
        skipped.append(
            StartupSkip(
                name=name,
                reason=StartupSkipReason.UNKNOWN_AUTO_START,
                source="auto_start",
            )
        )

    for name in sorted(provisioned - known_names):
        skipped.append(
            StartupSkip(
                name=name,
                reason=StartupSkipReason.UNKNOWN_PROVISIONED,
                source="provisioned",
            )
        )

    return StartupResolution(
        lazy_configs=lazy_configs,
        eager_configs=eager_configs,
        skipped=skipped,
    )
