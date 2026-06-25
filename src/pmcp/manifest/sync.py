"""Read-only MCP Registry to manifest reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field

from pmcp.manifest.loader import Manifest
from pmcp.manifest.registry import (
    RegistryCache,
    RegistryServerEntry,
    _server_identity,
)


@dataclass
class RegistrySyncResult:
    """Classified registry entries without mutating the manifest."""

    added: list[RegistryServerEntry] = field(default_factory=list)
    renamed: list[RegistryServerEntry] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    replaced: list[RegistryServerEntry] = field(default_factory=list)
    unchanged: list[RegistryServerEntry] = field(default_factory=list)


def _norm(value: str) -> str:
    return value.lower().replace("_", "-").replace(" ", "-")


def merge_registry_delta(base: RegistryCache, delta: RegistryCache) -> RegistryCache:
    """Merge an incremental registry delta into a base cache.

    Delta entries add new servers and replace existing ones matched by server
    identity; base servers not present in the delta are preserved. The merged
    set is deduplicated to the latest published version. Returns a new
    ``RegistryCache``; neither input is mutated.
    """
    merged_by_identity: dict[str, RegistryServerEntry] = {
        _server_identity(entry): entry for entry in base.servers
    }
    for entry in delta.servers:
        key = _server_identity(entry)
        existing = merged_by_identity.get(key)
        # Apply the delta entry unless it would downgrade a known-latest base
        # entry to a non-latest one.
        if (
            existing is None
            or entry.registry_meta.is_latest
            or not existing.registry_meta.is_latest
        ):
            merged_by_identity[key] = entry
    return RegistryCache(
        schema_version=base.schema_version,
        source_endpoint=delta.source_endpoint or base.source_endpoint,
        fetched_at=delta.fetched_at,
        servers=list(merged_by_identity.values()),
        diagnostics=list(delta.diagnostics),
        last_synced_at=delta.last_synced_at or base.last_synced_at,
    )


def sync_registry_to_manifest(
    manifest: Manifest, registry: RegistryCache
) -> RegistrySyncResult:
    """Classify registry entries against local manifest metadata.

    This function intentionally returns discovery metadata only. It does not
    install, auto-connect, or mutate the input manifest.
    """
    result = RegistrySyncResult()
    manifest_by_name = {
        _norm(name): server for name, server in manifest.servers.items()
    }
    manifest_by_package = {
        server.package: server
        for server in manifest.servers.values()
        if server.package is not None
    }

    for entry in registry.servers:
        normalized_name = _norm(entry.name)
        packages = [pkg.identifier for pkg in entry.packages]
        package_match = next(
            (
                manifest_by_package[pkg]
                for pkg in packages
                if pkg in manifest_by_package
            ),
            None,
        )
        name_match = manifest_by_name.get(normalized_name)

        if name_match is not None:
            if getattr(name_match, "status", None) in {
                "archived",
                "archived_reference_package",
            }:
                result.archived.append(name_match.name)
            else:
                result.unchanged.append(entry)
            continue

        if package_match is not None:
            result.renamed.append(entry)
            continue

        replaced = False
        for server in manifest.servers.values():
            replacement = getattr(server, "replacement", None)
            if replacement and _norm(str(replacement)) == normalized_name:
                result.replaced.append(entry)
                replaced = True
                break
        if replaced:
            continue
        result.added.append(entry)

    return result
