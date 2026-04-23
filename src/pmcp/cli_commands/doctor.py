"""Doctor command helpers for PMCP CLI."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pmcp.auth import (
    redact_auth_url,
    sanitize_auth_diagnostic,
    sanitize_public_auth_url,
)
from pmcp.remote_auth import build_remote_header_env_lookup, resolve_remote_headers


def _read_user_pmcp_env() -> dict[str, str]:
    """Read user-scope PMCP env context from ~/.config/pmcp/pmcp.env."""
    path = Path.home() / ".config" / "pmcp" / "pmcp.env"
    if not path.exists():
        return {}

    from pmcp.env_store import read_env_file

    return read_env_file(path)


def collect_remote_header_diagnostics(
    config_data: dict | None,
) -> list[tuple[str, str, str]]:
    """Validate remote server URL/header interpolation in local config."""
    checks: list[tuple[str, str, str]] = []

    if not isinstance(config_data, dict):
        return checks

    servers = config_data.get("mcpServers")
    if not isinstance(servers, dict):
        return checks

    env_lookup = build_remote_header_env_lookup()

    for server_name, server_cfg in servers.items():
        if not isinstance(server_cfg, dict):
            continue
        if server_cfg.get("type") not in {"remote", "sse", "http", "streamable-http"}:
            continue

        raw_url = server_cfg.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            checks.append(
                (
                    "remote",
                    "fail",
                    f"{server_name}: remote server is missing a valid url field.",
                )
            )
            continue

        parsed = urlparse(raw_url.strip())
        if not parsed.scheme or not parsed.netloc:
            checks.append(
                (
                    "remote",
                    "fail",
                    f"{server_name}: remote url '{sanitize_auth_diagnostic(raw_url)}' is invalid. Use an absolute URL (for example https://host/sse).",
                )
            )

        for metadata_key in (
            "protected_resource_metadata_url",
            "authorization_server_metadata_url",
            "oidc_issuer_url",
            "oidc_discovery_url",
            "client_id_metadata_document_url",
        ):
            metadata_url = server_cfg.get(metadata_key)
            if not isinstance(metadata_url, str) or not metadata_url:
                continue
            try:
                safe_metadata_url = sanitize_public_auth_url(metadata_url)
            except ValueError as exc:
                checks.append(
                    (
                        "remote",
                        "warn",
                        f"{server_name}: {metadata_key} '{redact_auth_url(metadata_url)}' is invalid: {sanitize_auth_diagnostic(exc)}",
                    )
                )
            else:
                checks.append(
                    (
                        "remote",
                        "ok",
                        f"{server_name}: {metadata_key} configured at {safe_metadata_url}.",
                    )
                )

        headers = server_cfg.get("headers")
        if not isinstance(headers, dict):
            continue

        string_headers = {
            key: value
            for key, value in headers.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        resolution = resolve_remote_headers(string_headers, env_lookup)
        for header_name, referenced_vars in sorted(
            resolution.referenced_env_vars_by_header.items()
        ):
            for var_name in referenced_vars:
                if var_name not in resolution.missing_env_vars:
                    continue
                checks.append(
                    (
                        "remote",
                        "warn",
                        f"{server_name}: auth=missing_auth missing_env={var_name} "
                        f"next=gateway.auth_connect(server_name='{server_name}'); "
                        f"header '{header_name}' references ${{{var_name}}}, but "
                        f"{var_name} is not set in the local environment or PMCP env stores.",
                    )
                )

    return checks
