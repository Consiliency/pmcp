"""Manifest module for dynamic capability discovery and provisioning."""

from pmcp.manifest.loader import load_manifest, Manifest
from pmcp.manifest.environment import (
    detect_platform,
    probe_clis,
    EnvironmentInfo,
)
from pmcp.manifest.matcher import CLIHintMatch, match_capability, rank_cli_hints
from pmcp.manifest.installer import install_server
from pmcp.manifest.registry import (
    RegistryCache,
    RegistryPackage,
    RegistryServerEntry,
    fetch_registry_servers,
    load_registry_cache,
    save_registry_cache,
)
from pmcp.manifest.sync import RegistrySyncResult, sync_registry_to_manifest

__all__ = [
    "load_manifest",
    "Manifest",
    "detect_platform",
    "probe_clis",
    "EnvironmentInfo",
    "CLIHintMatch",
    "match_capability",
    "rank_cli_hints",
    "install_server",
    "RegistryCache",
    "RegistryPackage",
    "RegistryServerEntry",
    "fetch_registry_servers",
    "load_registry_cache",
    "save_registry_cache",
    "RegistrySyncResult",
    "sync_registry_to_manifest",
]
