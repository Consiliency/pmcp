"""Doctor command helpers for PMCP CLI."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pmcp.auth import (
    is_verified_public_auth_url,
    redact_auth_url,
    sanitize_auth_diagnostic,
    sanitize_public_auth_url,
)
from pmcp.argument_errors import plain_name
from pmcp.provision_gate import operator_safe
from pmcp.remote_auth import build_remote_header_env_lookup, resolve_remote_headers


def collect_remote_header_diagnostics(
    config_data: dict | None,
    project_root: Path | None = None,
) -> list[tuple[str, str, str]]:
    """Validate remote server URL/header interpolation in local config.

    ``project_root`` is the project whose ``.mcp.json`` this is (``pmcp doctor
    --project``); its headers are judged against that project's credentials,
    never the launch directory's (Consiliency/pmcp#372 round 9, grok F001).
    """
    checks: list[tuple[str, str, str]] = []

    if not isinstance(config_data, dict):
        return checks

    servers = config_data.get("mcpServers")
    if not isinstance(servers, dict):
        return checks

    env_lookup = build_remote_header_env_lookup(project_root)

    for raw_name, server_cfg in servers.items():
        if not isinstance(server_cfg, dict):
            continue
        # A `.mcp.json` key is named as the operator wrote it (D9), but
        # through `operator_safe`: a checkout's file may hold a tab, a bell or
        # an ANSI escape in it (Consiliency/pmcp#297 implementation, F001).
        server_name = operator_safe(str(raw_name))
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

        # A parse failure is caught here, at the parse site: `urlparse` (and
        # `.port`) raise a ValueError whose message quotes the URL -- control
        # characters and all -- and the CLI would print it (Consiliency/pmcp#388
        # round 2, codex F001). It reads as the same fixed "is invalid" line.
        try:
            parsed = urlparse(raw_url.strip())
            _ = parsed.port
            well_formed = bool(parsed.scheme and parsed.netloc)
        except ValueError:
            well_formed = False
        if not well_formed:
            checks.append(
                (
                    "remote",
                    "fail",
                    f"{server_name}: remote url {operator_safe(sanitize_auth_diagnostic(raw_url))} is invalid. Use an absolute URL (for example https://host/sse).",
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
                        f"{server_name}: {metadata_key} {operator_safe(redact_auth_url(metadata_url))} is invalid: {sanitize_auth_diagnostic(exc)}",
                    )
                )
            else:
                # `sanitize_public_auth_url` succeeding means the URL's form is
                # valid and its host is not a private address literal -- not
                # that pmcp knows where it points. Reporting a bare `[OK] ...
                # configured at <name>` claimed the latter (#211).
                qualifier = (
                    ""
                    if is_verified_public_auth_url(metadata_url)
                    else " (host not verified; pmcp does not resolve names)"
                )
                checks.append(
                    (
                        "remote",
                        "ok",
                        f"{server_name}: {metadata_key} configured at "
                        f"{operator_safe(safe_metadata_url)}{qualifier}.",
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
        for raw_header, referenced_vars in sorted(
            resolution.referenced_env_vars_by_header.items()
        ):
            header_name = operator_safe(str(raw_header))
            for raw_var in referenced_vars:
                if raw_var not in resolution.missing_env_vars:
                    continue
                var_name = plain_name(raw_var)
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
