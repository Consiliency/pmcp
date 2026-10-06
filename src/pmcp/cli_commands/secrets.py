"""Secrets command handlers for PMCP CLI."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

from pmcp.config.loader import load_configs
from pmcp.env_store import (
    copyable_from_repository,
    resolve_project_root,
    credential_lookup,
    read_store,
    read_store_for_update,
    resolve_scope_path,
    scope_confinement,
    scope_store_name,
    store_refusal,
    set_env_value,
    validate_env_var_name,
    write_env_file,
)
from pmcp.manifest.loader import (
    credential_storage_key,
    load_manifest,
    requires_credential,
)
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


def manifest_secret_metadata(server: Any) -> tuple[dict[str, object], set[str]]:
    """A manifest server's auth metadata and remote-header env keys.

    The per-server half of ``_extract_required_keys``, shared with the overlay
    check so an entry it would fail on is skipped at parse time
    (Consiliency/pmcp#342 rev 4).
    """
    metadata: dict[str, object] = {
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
    return metadata, set(collect_remote_header_env_vars(server.headers))


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
        manifest_by_name = load_manifest(project_root=project_root).servers
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

        # The credential requirement is INTRINSIC to a configured server that
        # matches a manifest api-key server — it holds whether or not a credential
        # is currently resolved into env_map. (A fresh configured entry has no env
        # until the credential is stored; deriving the requirement only from the
        # env map would falsely report "ok" for a missing credential.) Require the
        # namespaced storage key with the legacy runtime env_var as a fallback.
        credential_var: str | None = None
        if (
            manifest_server
            and manifest_server.env_var
            and requires_credential(manifest_server, child_env=cfg.config.env)
        ):
            credential_var = manifest_server.env_var
            storage_key = credential_storage_key(manifest_server) or credential_var
            server_keys.add(storage_key)
            all_keys.add(storage_key)
            if storage_key != credential_var:
                credential_fallbacks[storage_key] = credential_var

        # Manifest-supplied non-secrets (e.g. a self-hosted base URL from
        # extra_env) are not credentials the operator must store — reported
        # missing here they'd flip `secrets check`'s ok to false for a value
        # that is already present via the manifest, not the secret store.
        # Skipped only when the configured env_map still carries the exact
        # manifest literal; a `.mcp.json` override to a different value (or a
        # genuine ${VAR} indirection, handled by the ENV_REF_PATTERN branch
        # below) is not skipped.
        manifest_extra_env = getattr(manifest_server, "extra_env", None) or {}

        for env_key, env_value in env_map.items():
            # The credential var is required via the manifest above; don't re-add
            # its bare runtime name (which would bypass the storage-key mapping).
            if env_key == credential_var:
                continue
            if (
                env_key in manifest_extra_env
                and manifest_extra_env[env_key] == env_value
            ):
                continue
            server_keys.add(env_key)
            all_keys.add(env_key)

            for pattern_match in ENV_REF_PATTERN.finditer(env_value):
                var_name = pattern_match.group(1) or pattern_match.group(2)
                if var_name and var_name != credential_var:
                    server_keys.add(var_name)
                    all_keys.add(var_name)

        if server_keys:
            per_server[cfg.name] = server_keys

    try:
        manifest_servers = list(
            load_manifest(project_root=project_root).servers.values()
        )
    except Exception:
        manifest_servers = []
    for server in manifest_servers:
        # Per server: one entry must not drop every later server's metadata
        # (Consiliency/pmcp#342 rev 4; the overlay check normally skips it first).
        try:
            manifest_metadata, header_keys = manifest_secret_metadata(server)
        except Exception:
            continue
        if manifest_metadata:
            auth_metadata_by_server.setdefault(server.name, manifest_metadata)
        if header_keys:
            per_server.setdefault(server.name, set()).update(header_keys)
            all_keys.update(header_keys)

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
    path = Path(scope_store_name(args.scope))
    # The read sits inside the same boundary as the write, and for the project
    # store it goes through the write's confined walk: a link the write would
    # refuse is refused before anything is read (read_store_for_update).
    # ValueError too: a store that is not UTF-8, a value with a newline, an
    # invalid key -- reported, never raised (store_refusal).
    try:
        # Resolving the project root can itself be refused (`missing/../x`).
        path = resolve_scope_path(args.scope, project)
        values = read_store_for_update(args.scope, path)
        existing_value = values.get(args.key)
        changed = existing_value != args.value
        path = set_env_value(args.scope, args.key, args.value, project)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "command": "secrets.set",
            "scope": args.scope,
            "error": store_refusal(path, exc),
        }

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

    source_path = Path(scope_store_name(from_scope))
    target_path = Path(scope_store_name(to_scope))
    try:
        source_path = resolve_scope_path(from_scope, project)
        target_path = resolve_scope_path(to_scope, project)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "command": "secrets.sync",
            "from_scope": from_scope,
            "to_scope": to_scope,
            "error": store_refusal(
                target_path if from_scope == "user" else source_path, exc
            ),
        }

    # Both reads go through the confined walk for a project store and sit
    # inside the reported-refusal boundary, so a leaving or non-regular store is
    # refused before it is read, never raised.
    # The SOURCE is only read, so its refusal says "read" (verb="read").
    try:
        source_values = read_store_for_update(from_scope, source_path, verb="read")
        for key in source_values:
            validate_env_var_name(key)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "command": "secrets.sync",
            "from_scope": from_scope,
            "to_scope": to_scope,
            "error": store_refusal(source_path, exc, verb="read"),
        }
    try:
        target_values = read_store_for_update(to_scope, target_path)
        for key in target_values:
            validate_env_var_name(key)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "command": "secrets.sync",
            "from_scope": from_scope,
            "to_scope": to_scope,
            "error": store_refusal(target_path, exc),
        }

    # A project store is the repository's; what it may hand to another store --
    # the user store, which loads into pmcp's environment at every start -- is
    # decided by the same rule a lookup uses (env_store.copyable_from_repository,
    # Consiliency/pmcp#372 round 5). Refused names are reported, never copied.
    refused: list[str] = []
    if from_scope == "project":
        source_values, refused = copyable_from_repository(
            source_values, source_path.name
        )

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

    try:
        write_env_file(
            target_path,
            target_values,
            confine_to=scope_confinement(to_scope, target_path),
        )
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "command": "secrets.sync",
            "from_scope": from_scope,
            "to_scope": to_scope,
            "error": store_refusal(target_path, exc),
        }

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
        "refused": sorted(refused),
        "target_key_count": len(target_values),
    }


async def run_secrets_check(args: argparse.Namespace) -> dict[str, object]:
    """Check available and missing secrets for discovered config requirements."""
    project_root = resolve_project_root(getattr(args, "project", None))
    user_path = resolve_scope_path("user")
    project_path = resolve_scope_path("project", project_root)

    # The project store is confined to the project (Consiliency/pmcp#367): a
    # link out of the checkout lists no keys and satisfies no requirement.
    user_values = read_store("user")
    project_values = read_store("project", project=project_root)
    # The diagnostic answers what the runtime will do: the same lookup a running
    # pmcp uses (env_store.credential_lookup) -- the environment first, an
    # exported empty value "unavailable" -- not the stores alone
    # (Consiliency/pmcp#372 round 5; tests/test_credential_parity.py).
    # The root the runtime answers for: the --project given, else the startup
    # load's directory -- not a re-derived project root.
    lookup = credential_lookup(getattr(args, "project", None))

    def _available(key: str) -> bool:
        return bool(lookup(key))

    (
        required_keys,
        required_by_server,
        auth_metadata_by_server,
        credential_fallbacks,
    ) = _extract_required_keys(project_root)

    def _satisfied(key: str) -> bool:
        if _available(key):
            return True
        fallback = credential_fallbacks.get(key)
        return bool(fallback and _available(fallback))

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
        # Every name the stores hold or a server requires, that the runtime's
        # lookup would answer -- an exported credential counts.
        "available_keys": sorted(
            k
            for k in set(user_values) | set(project_values) | set(required_keys)
            if _available(k)
        ),
        "missing_keys": missing_keys,
    }
