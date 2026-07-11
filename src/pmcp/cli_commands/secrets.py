"""Secrets command handlers for PMCP CLI."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from pmcp.config.loader import load_configs
from pmcp.env_store import (
    read_env_file,
    resolve_project_root,
    resolve_scope_path,
    set_env_value,
    validate_env_var_name,
    write_env_file,
)
from pmcp.manifest.loader import load_manifest
from pmcp.remote_auth import collect_remote_header_env_vars
from pmcp.types import LocalMcpServerConfig, RemoteMcpServerConfig

ENV_REF_PATTERN = re.compile(
    r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))"
)


def _mask(value: str) -> str:
    """Return a redacted representation for secret values."""
    if not value:
        return ""
    return "*" * min(8, len(value))


def _extract_required_keys(
    project_root: Path,
) -> tuple[
    list[str], dict[str, list[str]], dict[str, dict[str, object]], dict[str, str]
]:
    """Extract required env keys from discovered MCP server configs.

    Returns (all_keys, per_server, auth_metadata, credential_fallbacks) where
    credential_fallbacks maps a required namespaced storage key to the legacy
    runtime env_var that also satisfies it — so `pmcp secrets check` accepts a
    credential stored under either the namespaced secret_key or the legacy key.
    """
    configs = load_configs(project_root=project_root)

    try:
        manifest_by_name = load_manifest().servers
    except Exception:
        manifest_by_name = {}

    per_server: dict[str, set[str]] = {}
    all_keys: set[str] = set()
    auth_metadata_by_server: dict[str, dict[str, object]] = {}
    credential_fallbacks: dict[str, str] = {}
    for cfg in configs:
        if isinstance(cfg.config, RemoteMcpServerConfig):
            remote_header_keys = set(collect_remote_header_env_vars(cfg.config.headers))
            if remote_header_keys:
                per_server[cfg.name] = remote_header_keys
                all_keys.update(remote_header_keys)
            remote_metadata: dict[str, object] = {
                key: value
                for key, value in {
                    "protected_resource_metadata_url": cfg.config.protected_resource_metadata_url,
                    "authorization_server_metadata_url": cfg.config.authorization_server_metadata_url,
                    "oidc_issuer_url": cfg.config.oidc_issuer_url,
                    "oidc_discovery_url": cfg.config.oidc_discovery_url,
                    "client_id_metadata_document_url": cfg.config.client_id_metadata_document_url,
                    "declared_scopes": cfg.config.declared_scopes,
                    "supports_url_elicitation": cfg.config.supports_url_elicitation,
                }.items()
                if value
            }
            if remote_metadata:
                auth_metadata_by_server[cfg.name] = remote_metadata
            continue
        if not isinstance(cfg.config, LocalMcpServerConfig):
            continue

        manifest_server = manifest_by_name.get(cfg.name)
        env_map = cfg.config.env or {}
        server_keys: set[str] = set()
        for env_key, env_value in env_map.items():
            # A credentialed server that stores under a namespaced secret_key is
            # satisfied by the storage key; require that (not the runtime env_var)
            # with the legacy env_var as an accepted fallback.
            secret_key = manifest_server.secret_key if manifest_server else None
            required_key = env_key
            if manifest_server and manifest_server.env_var == env_key and secret_key:
                required_key = secret_key
                credential_fallbacks[required_key] = env_key
            server_keys.add(required_key)
            all_keys.add(required_key)

            for pattern_match in ENV_REF_PATTERN.finditer(env_value):
                var_name = pattern_match.group(1) or pattern_match.group(2)
                if var_name:
                    server_keys.add(var_name)
                    all_keys.add(var_name)

        if server_keys:
            per_server[cfg.name] = server_keys

    try:
        manifest = load_manifest()
        for server in manifest.servers.values():
            manifest_metadata: dict[str, object] = {
                key: value
                for key, value in {
                    "protected_resource_metadata_url": server.protected_resource_metadata_url,
                    "authorization_server_metadata_url": server.authorization_server_metadata_url,
                    "oidc_issuer_url": server.oidc_issuer_url,
                    "oidc_discovery_url": server.oidc_discovery_url,
                    "client_id_metadata_document_url": server.client_id_metadata_document_url,
                    "declared_scopes": server.declared_scopes,
                    "supports_url_elicitation": server.supports_url_elicitation,
                }.items()
                if value
            }
            if manifest_metadata:
                auth_metadata_by_server.setdefault(server.name, manifest_metadata)
            server_keys = set(collect_remote_header_env_vars(server.headers))
            if server_keys:
                per_server.setdefault(server.name, set()).update(server_keys)
                all_keys.update(server_keys)
    except Exception:
        pass

    server_required = {
        server_name: sorted(keys) for server_name, keys in per_server.items()
    }
    return (
        sorted(all_keys),
        server_required,
        auth_metadata_by_server,
        credential_fallbacks,
    )


async def run_secrets_set(args: argparse.Namespace) -> dict[str, object]:
    """Set one secret in user or project PMCP env file."""
    project = getattr(args, "project", None)
    path = resolve_scope_path(args.scope, project)
    values = read_env_file(path)

    existing_value = values.get(args.key)
    changed = existing_value != args.value

    path = set_env_value(args.scope, args.key, args.value, project)

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

    source_path = resolve_scope_path(from_scope, project)
    target_path = resolve_scope_path(to_scope, project)

    source_values = read_env_file(source_path)
    target_values = read_env_file(target_path)
    for key in source_values:
        validate_env_var_name(key)
    for key in target_values:
        validate_env_var_name(key)

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

    write_env_file(target_path, target_values)

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
    project_root = resolve_project_root(getattr(args, "project", None))
    user_path = resolve_scope_path("user")
    project_path = resolve_scope_path("project", project_root)

    user_values = read_env_file(user_path)
    project_values = read_env_file(project_path)

    effective = dict(user_values)
    effective.update(project_values)

    (
        required_keys,
        required_by_server,
        auth_metadata_by_server,
        credential_fallbacks,
    ) = _extract_required_keys(project_root)

    def _satisfied(key: str) -> bool:
        if effective.get(key):
            return True
        fallback = credential_fallbacks.get(key)
        return bool(fallback and effective.get(fallback))

    missing_keys = sorted(key for key in required_keys if not _satisfied(key))

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
        "auth_metadata_by_server": auth_metadata_by_server,
        "available_keys": sorted(k for k, v in effective.items() if v),
        "missing_keys": missing_keys,
    }
