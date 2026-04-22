"""Doctor command helpers for PMCP CLI."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from pmcp.auth import redact_auth_url

ENV_INTERPOLATION_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _read_user_pmcp_env() -> dict[str, str]:
    """Read user-scope PMCP env context from ~/.config/pmcp/pmcp.env."""
    path = Path.home() / ".config" / "pmcp" / "pmcp.env"
    if not path.exists():
        return {}

    parsed = dotenv_values(path)
    values: dict[str, str] = {}
    for key, value in parsed.items():
        if key is None:
            continue
        values[key] = "" if value is None else value
    return values


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

    pmcp_env = _read_user_pmcp_env()

    for server_name, server_cfg in servers.items():
        if not isinstance(server_cfg, dict):
            continue
        if server_cfg.get("type") != "remote":
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
                    f"{server_name}: remote url '{redact_auth_url(raw_url)}' is invalid. Use an absolute URL (for example https://host/sse).",
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
            metadata_parsed = urlparse(metadata_url)
            if not metadata_parsed.scheme or not metadata_parsed.netloc:
                checks.append(
                    (
                        "remote",
                        "warn",
                        f"{server_name}: {metadata_key} '{redact_auth_url(metadata_url)}' is not an absolute URL.",
                    )
                )
            else:
                checks.append(
                    (
                        "remote",
                        "ok",
                        f"{server_name}: {metadata_key} configured at {redact_auth_url(metadata_url)}.",
                    )
                )

        headers = server_cfg.get("headers")
        if not isinstance(headers, dict):
            continue

        for header_name, header_value in headers.items():
            if not isinstance(header_name, str) or not isinstance(header_value, str):
                continue

            referenced_vars = set(ENV_INTERPOLATION_PATTERN.findall(header_value))
            for var_name in sorted(referenced_vars):
                if var_name in os.environ or var_name in pmcp_env:
                    continue

                checks.append(
                    (
                        "remote",
                        "warn",
                        f"{server_name}: header '{header_name}' references ${{{var_name}}}, but {var_name} is not set in the local environment or ~/.config/pmcp/pmcp.env.",
                    )
                )

    return checks
