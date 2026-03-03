"""Secrets command handlers for PMCP CLI."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from dotenv import dotenv_values

from pmcp.config.loader import find_project_root, load_configs
from pmcp.types import LocalMcpServerConfig

ENV_REF_PATTERN = re.compile(
    r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))"
)


def _get_user_env_path() -> Path:
    """Return user-scope PMCP env path."""
    return Path.home() / ".config" / "pmcp" / "pmcp.env"


def _resolve_project_root(project: Path | None) -> Path:
    """Resolve project root for project-scope secrets."""
    if project:
        return project.resolve()

    discovered = find_project_root(Path.cwd())
    if discovered:
        return discovered

    return Path.cwd().resolve()


def _get_project_env_path(project: Path | None) -> Path:
    """Return project-scope PMCP env path."""
    return _resolve_project_root(project) / ".env.pmcp"


def _resolve_scope_path(scope: str, project: Path | None) -> Path:
    """Resolve env file path for a secret scope."""
    if scope == "user":
        return _get_user_env_path()
    if scope == "project":
        return _get_project_env_path(project)
    raise ValueError(f"Unsupported secret scope: {scope}")


def _read_env_file(path: Path) -> dict[str, str]:
    """Read .env key/value pairs from path."""
    if not path.exists():
        return {}

    parsed = dotenv_values(path)
    values: dict[str, str] = {}
    for key, value in parsed.items():
        if value is None:
            values[key] = ""
        else:
            values[key] = value
    return values


def _format_env_value(value: str) -> str:
    """Format a value for safe .env writing."""
    if value == "":
        return '""'

    needs_quotes = any(ch.isspace() for ch in value) or any(
        ch in value for ch in ["#", '"', "'", "\\"]
    )
    if not needs_quotes:
        return value

    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    """Write key/value pairs to .env file and lock permissions to 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"{key}={_format_env_value(val)}" for key, val in values.items()]
    content = "\n".join(lines)
    if content:
        content += "\n"

    path.write_text(content)
    path.chmod(0o600)


def _mask(value: str) -> str:
    """Return a redacted representation for secret values."""
    if not value:
        return ""
    return "*" * min(8, len(value))


def _extract_required_keys(
    project_root: Path,
) -> tuple[list[str], dict[str, list[str]]]:
    """Extract required env keys from discovered MCP server configs."""
    configs = load_configs(project_root=project_root)

    per_server: dict[str, set[str]] = {}
    all_keys: set[str] = set()
    for cfg in configs:
        if not isinstance(cfg.config, LocalMcpServerConfig):
            continue

        env_map = cfg.config.env or {}
        server_keys: set[str] = set()
        for env_key, env_value in env_map.items():
            server_keys.add(env_key)
            all_keys.add(env_key)

            for pattern_match in ENV_REF_PATTERN.finditer(env_value):
                var_name = pattern_match.group(1) or pattern_match.group(2)
                if var_name:
                    server_keys.add(var_name)
                    all_keys.add(var_name)

        if server_keys:
            per_server[cfg.name] = server_keys

    server_required = {
        server_name: sorted(keys) for server_name, keys in per_server.items()
    }
    return sorted(all_keys), server_required


async def run_secrets_set(args: argparse.Namespace) -> dict[str, object]:
    """Set one secret in user or project PMCP env file."""
    path = _resolve_scope_path(args.scope, getattr(args, "project", None))
    values = _read_env_file(path)

    existing_value = values.get(args.key)
    changed = existing_value != args.value
    values[args.key] = args.value

    _write_env_file(path, values)

    return {
        "ok": True,
        "command": "secrets.set",
        "scope": args.scope,
        "path": str(path),
        "key": args.key,
        "changed": changed,
        "value": _mask(args.value),
    }


async def run_secrets_sync(args: argparse.Namespace) -> dict[str, object]:
    """Sync secrets from one scope file to another."""
    from_scope = args.from_scope
    to_scope = args.to_scope
    project = getattr(args, "project", None)

    if from_scope == to_scope:
        return {
            "ok": False,
            "command": "secrets.sync",
            "error": "Source and target scopes must differ",
            "from_scope": from_scope,
            "to_scope": to_scope,
        }

    source_path = _resolve_scope_path(from_scope, project)
    target_path = _resolve_scope_path(to_scope, project)

    source_values = _read_env_file(source_path)
    target_values = _read_env_file(target_path)

    added: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []

    for key, value in source_values.items():
        if key not in target_values:
            target_values[key] = value
            added.append(key)
        elif target_values[key] != value and args.overwrite:
            target_values[key] = value
            updated.append(key)
        else:
            skipped.append(key)

    _write_env_file(target_path, target_values)

    return {
        "ok": True,
        "command": "secrets.sync",
        "from_scope": from_scope,
        "to_scope": to_scope,
        "source_path": str(source_path),
        "target_path": str(target_path),
        "overwrite": bool(args.overwrite),
        "added": sorted(added),
        "updated": sorted(updated),
        "skipped": sorted(skipped),
        "target_key_count": len(target_values),
    }


async def run_secrets_check(args: argparse.Namespace) -> dict[str, object]:
    """Check available and missing secrets for discovered config requirements."""
    project_root = _resolve_project_root(getattr(args, "project", None))
    user_path = _get_user_env_path()
    project_path = _get_project_env_path(project_root)

    user_values = _read_env_file(user_path)
    project_values = _read_env_file(project_path)

    effective = dict(user_values)
    effective.update(project_values)

    required_keys, required_by_server = _extract_required_keys(project_root)
    missing_keys = sorted(
        key for key in required_keys if key not in effective or not effective[key]
    )

    return {
        "ok": len(missing_keys) == 0,
        "command": "secrets.check",
        "project_root": str(project_root),
        "user_scope": {
            "path": str(user_path),
            "exists": user_path.exists(),
            "keys": sorted(user_values.keys()),
        },
        "project_scope": {
            "path": str(project_path),
            "exists": project_path.exists(),
            "keys": sorted(project_values.keys()),
        },
        "required_keys": required_keys,
        "required_by_server": required_by_server,
        "available_keys": sorted(k for k, v in effective.items() if v),
        "missing_keys": missing_keys,
    }
