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
    #: Variables whose lookup gave a typed failure, with its reason
    #: (``credential_unavailable``, Consiliency/pmcp#487). Each is also in
    #: ``missing_env_vars``, so a caller that knows only "missing" still
    #: refuses to connect; a caller that knows the state reports it.
    unavailable_env_vars: dict[str, str] = field(default_factory=dict)


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

    from pmcp.credential_state import CredentialUnavailableError

    resolved_headers: dict[str, str] = {}
    referenced_env_vars_by_header: dict[str, list[str]] = {}
    missing: set[str] = set()
    unavailable: dict[str, str] = {}

    for header_name, header_value in headers.items():
        referenced = REMOTE_HEADER_ENV_PATTERN.findall(header_value)
        if referenced:
            referenced_env_vars_by_header[header_name] = sorted(set(referenced))

        resolved = header_value
        for env_var in sorted(set(referenced)):
            # A typed failure is contained to this variable: the header keeps
            # its placeholder, the server is not connected, and no other
            # server's lookup is affected (Consiliency/pmcp#342, #487).
            try:
                value = env_lookup(env_var)
            except CredentialUnavailableError as exc:
                unavailable[env_var] = exc.reason
                missing.add(env_var)
                continue
            if value:
                resolved = resolved.replace(f"${{{env_var}}}", value)
            else:
                missing.add(env_var)
        resolved_headers[header_name] = resolved

    return RemoteHeaderAuthResolution(
        resolved_headers=resolved_headers,
        missing_env_vars=sorted(missing),
        referenced_env_vars_by_header=referenced_env_vars_by_header,
        unavailable_env_vars=unavailable,
    )


def build_remote_header_env_lookup(
    project_root: Path | None = None,
) -> Callable[[str], str | None]:
    """Build a lookup over process env plus PMCP user/project env stores.

    The project store is read confined to the project (``read_store``): a
    ``.env.pmcp`` linked out of the checkout contributes nothing, so a file the
    repository points at can never fill a ``${VAR}`` header (Consiliency/pmcp#367).
    """
    from pmcp.env_store import credential_lookup

    # The one runtime lookup (env_store.credential_lookup): the process
    # environment, the user store, then the project store -- by membership, and
    # never a name a repository may not supply from a repository source.
    return credential_lookup(project_root)


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

    from pmcp.env_store import (
        credential_value,
        repository_values,
        resolve_project_root,
    )

    # Repository-controlled: confined to the project root (Consiliency/pmcp#367),
    # expanded within the file only, read through the one gate. The order, by
    # presence: the environment, the user store, the tenant store, then the
    # project store's credentials loaded at startup -- or, with
    # include_process_env=False, the tenant store alone.
    tenant_values = repository_values(
        "tenant", project=project_root, tenant_id=tenant_id
    )
    root = None if project_root is None else resolve_project_root(project_root)
    # The one gate (env_store.credential_value) runs the startup load itself,
    # so the user store is in the environment before any answer.

    def lookup(env_var: str) -> str | None:
        # Non-default arguments, deliberately: a caller that asks for tenant
        # isolation (include_process_env=False) gets the tenant store alone --
        # no environment, no user store, no startup project credentials.
        return credential_value(
            env_var,
            environ=include_process_env,
            startup_files=include_process_env,
            repository=tenant_values,
            root=root,
        )

    return resolve_remote_headers(headers, lookup)
