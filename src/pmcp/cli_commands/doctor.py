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


def _reference_shaped(value: object) -> bool:
    """Does ``value`` look like a 1Password secret reference (``op://``)?

    Leading ASCII whitespace is ignored and the scheme is matched in any case
    (Consiliency/pmcp#487 plan 3.3): ``" OP://v/i/f"`` counts.
    """
    return isinstance(value, str) and value.lstrip(" \t\r\n\f\v").lower().startswith(
        "op://"
    )


def _manifest_name(name: object) -> str:
    from pmcp.manifest.loader import entry_log_name

    return entry_log_name(name, manifest_derived=True)


def collect_credential_diagnostics(
    project_root: Path | None = None,
) -> list[tuple[str, str, str]]:
    """Credential-layer checks for ``pmcp doctor`` (Consiliency/pmcp#487 P1).

    Names only, never a value:

    * the user store exists but cannot be read (every lookup that reaches it
      answers ``credential_unavailable``, reason ``store_unreadable``);
    * a manifest runtime ``env_var`` (``API_TOKEN``) set in pmcp's environment
      from OUTSIDE pmcp and not a user-store name: pmcp strips only
      user-store names and keys it loaded, so every server it starts
      inherits that value;
    * every ``op://`` value and every ``OP_*`` key in a server's own ``env``,
      by server and config source, and every credential lookup that answers
      with an ``op://`` value, by server and layer;
    * a credential held in more than one place -- a legacy key next to its
      namespaced key, or one key in several layers -- so deleting one entry
      is not mistaken for revoking the credential.
    """
    from pmcp.config.loader import load_config_sources
    from pmcp.env_store import (
        CredentialUnavailableError,
        credential_layers,
        credential_value,
        environment_holds,
        operator_managed_secret_keys,
        user_store_state,
    )
    from pmcp.manifest.loader import credential_lookup_keys, load_manifest

    checks: list[tuple[str, str, str]] = []

    if user_store_state() == "unreadable":
        checks.append(
            (
                "credentials",
                "fail",
                "The user store ~/.config/pmcp/pmcp.env exists but cannot be read: "
                "every server whose credential lookup reaches it reports "
                "credential_unavailable (store_unreadable) and is not started. "
                "Make it a readable regular file (mode 0600).",
            )
        )

    try:
        manifest = (
            load_manifest()
            if project_root is None
            else load_manifest(project_root=project_root)
        )
        servers = dict(manifest.servers)
    except Exception as exc:
        checks.append(
            (
                "credentials",
                "warn",
                f"Manifest unavailable for credential checks ({type(exc).__name__}).",
            )
        )
        servers = {}

    user_names = operator_managed_secret_keys()
    leaking: dict[str, list[str]] = {}
    for name, server in sorted(servers.items()):
        env_var = getattr(server, "env_var", None)
        if (
            isinstance(env_var, str)
            and environment_holds(env_var) == "external"
            and env_var not in user_names
        ):
            leaking.setdefault(env_var, []).append(_manifest_name(name))
    for env_var, names in sorted(leaking.items()):
        checks.append(
            (
                "credentials",
                "warn",
                f"{plain_name(env_var)} (the runtime variable of {', '.join(names)}) "
                "is set in pmcp's environment from outside pmcp and is not a "
                "user-store name, so every server pmcp starts inherits it. Store it "
                "with `pmcp secrets set` and remove it from the launcher's "
                "environment (EnvironmentFile=, a sourced file, the shell).",
            )
        )

    # Own env, by server and config source.
    for source in load_config_sources(project_root=project_root):
        config = source.config
        if config is None or not config.mcpServers:
            continue
        for raw_name, server_config in sorted(config.mcpServers.items()):
            env = getattr(server_config, "env", None) or {}
            for key, value in sorted(env.items()):
                what: str | None = None
                if _reference_shaped(value):
                    what = "an op:// value"
                elif isinstance(key, str) and key.upper().startswith("OP_"):
                    what = "an OP_* key"
                if what is None:
                    continue
                checks.append(
                    (
                        "credentials",
                        "warn",
                        f"{operator_safe(str(raw_name))}: env {plain_name(key)} is "
                        f"{what} in the {source.source} config; pmcp passes it to "
                        "the server verbatim.",
                    )
                )
    for name, server in sorted(servers.items()):
        extra_env = getattr(server, "extra_env", None) or {}
        for key, value in sorted(extra_env.items()):
            what = None
            if _reference_shaped(value):
                what = "an op:// value"
            elif isinstance(key, str) and key.upper().startswith("OP_"):
                what = "an OP_* key"
            if what is None:
                continue
            checks.append(
                (
                    "credentials",
                    "warn",
                    f"{_manifest_name(name)}: env {plain_name(key)} is {what} in "
                    "the manifest or an overlay; pmcp passes it to the server "
                    "verbatim.",
                )
            )

    # Credential lookups: references, and credentials held more than once.
    for name, server in sorted(servers.items()):
        keys = credential_lookup_keys(server)
        if not keys:
            continue
        label = _manifest_name(name)
        layers = {key: credential_layers(key, root=project_root) for key in keys}
        for key in keys:
            try:
                value = credential_value(key, root=project_root)
            except CredentialUnavailableError:
                break
            if value is not None:
                if _reference_shaped(value):
                    first = layers[key][0] if layers[key] else "unknown"
                    checks.append(
                        (
                            "credentials",
                            "warn",
                            f"{label}: credential {plain_name(key)} is an op:// "
                            f"reference (from {first}); this pmcp passes it to the "
                            "server as a literal.",
                        )
                    )
                break
        held = [key for key in keys if layers[key]]
        if len(held) > 1:
            parts = "; ".join(
                f"{plain_name(key)} in {', '.join(layers[key])}" for key in held
            )
            checks.append(
                (
                    "credentials",
                    "warn",
                    f"{label}: a namespaced key and its legacy key are both set "
                    f"({parts}). Deleting one does not revoke the credential: pmcp "
                    "falls back to the other.",
                )
            )
        for key in held:
            if len(layers[key]) > 1:
                checks.append(
                    (
                        "credentials",
                        "warn",
                        f"{label}: {plain_name(key)} is set in more than one place "
                        f"({', '.join(layers[key])}); the first wins. Deleting it "
                        "from one place does not revoke the credential while "
                        "another still holds it.",
                    )
                )
    return checks
