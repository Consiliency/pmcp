"""Non-secret remote header authentication helpers."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

REMOTE_HEADER_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class RemoteHeaderAuthResolution:
    """Resolved remote headers plus non-secret placeholder metadata."""

    resolved_headers: dict[str, str]
    missing_env_vars: list[str] = field(default_factory=list)
    referenced_env_vars_by_header: dict[str, list[str]] = field(default_factory=dict)


class MissingRemoteHeaderAuthError(Exception):
    """Raised when a remote server header references unavailable credentials."""

    def __init__(self, server_name: str, missing_env_vars: list[str]) -> None:
        self.server_name = server_name
        self.missing_env_vars = sorted(set(missing_env_vars))
        env_names = ", ".join(self.missing_env_vars)
        super().__init__(
            f"Remote server '{server_name}' requires authentication. "
            f"Set missing environment variable(s): {env_names}"
        )


def collect_remote_header_env_vars(headers: Mapping[str, str] | None) -> list[str]:
    """Return sorted env var names referenced by remote header placeholders."""
    if not headers:
        return []
    names: set[str] = set()
    for value in headers.values():
        if isinstance(value, str):
            names.update(REMOTE_HEADER_ENV_PATTERN.findall(value))
    return sorted(names)


def resolve_remote_headers(
    headers: Mapping[str, str] | None,
    env_lookup: Callable[[str], str | None],
) -> RemoteHeaderAuthResolution:
    """Resolve `${VAR}` placeholders in remote headers without exposing values."""
    if not headers:
        return RemoteHeaderAuthResolution(resolved_headers={})

    resolved_headers: dict[str, str] = {}
    referenced_env_vars_by_header: dict[str, list[str]] = {}
    missing: set[str] = set()

    for header_name, header_value in headers.items():
        referenced = REMOTE_HEADER_ENV_PATTERN.findall(header_value)
        if referenced:
            referenced_env_vars_by_header[header_name] = sorted(set(referenced))

        resolved = header_value
        for env_var in sorted(set(referenced)):
            value = env_lookup(env_var)
            if value:
                resolved = resolved.replace(f"${{{env_var}}}", value)
            else:
                missing.add(env_var)
        resolved_headers[header_name] = resolved

    return RemoteHeaderAuthResolution(
        resolved_headers=resolved_headers,
        missing_env_vars=sorted(missing),
        referenced_env_vars_by_header=referenced_env_vars_by_header,
    )


def build_remote_header_env_lookup(
    project_root: Path | None = None,
) -> Callable[[str], str | None]:
    """Build a lookup over process env plus PMCP user/project env stores.

    The project store is read confined to the project (``read_store``): a
    ``.env.pmcp`` linked out of the checkout contributes nothing, so a file the
    repository points at can never fill a ``${VAR}`` header (Consiliency/pmcp#367).
    """
    from pmcp.env_store import credential_value, read_store, repository_values

    user_values = read_store("user")
    project_values = repository_values("project", project=project_root)

    def lookup(env_var: str) -> str | None:
        # One gate (env_store.credential_value): the process environment, the
        # user store, then repository values -- by membership, and never a name
        # pmcp reads from its own environment from a repository source
        # (Consiliency/pmcp#372 round 3).
        return credential_value(
            env_var, operator=user_values, repository=project_values
        )

    return lookup


def resolve_remote_headers_for_tenant(
    headers: Mapping[str, str] | None,
    *,
    server_name: str,
    tenant_id: str | None,
    project_root: Path | None = None,
    include_process_env: bool = True,
) -> RemoteHeaderAuthResolution:
    """Resolve remote headers using tenant-isolated credentials when tenant_id is set."""
    if tenant_id is None:
        return resolve_remote_headers(
            headers, build_remote_header_env_lookup(project_root)
        )

    from pmcp.env_store import credential_value, read_store, repository_values

    # Repository-controlled: confined to the project root (Consiliency/pmcp#367),
    # expanded within the file only, read through the one gate. The order, by
    # presence: the environment, the user store, the tenant store, then the
    # project store's credentials loaded at startup -- or, with
    # include_process_env=False, the tenant store alone.
    tenant_values = repository_values(
        "tenant", project=project_root, tenant_id=tenant_id
    )
    user_values = read_store("user") if include_process_env else None

    def lookup(env_var: str) -> str | None:
        return credential_value(
            env_var,
            environ=include_process_env,
            operator=user_values,
            startup_files=include_process_env,
            repository=tenant_values,
        )

    return resolve_remote_headers(headers, lookup)
