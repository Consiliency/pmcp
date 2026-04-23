"""Manifest module for dynamic capability discovery and provisioning."""

from pmcp.manifest.loader import load_manifest, Manifest
from pmcp.manifest.environment import (
    detect_platform,
    probe_clis,
    EnvironmentInfo,
)
from pmcp.manifest.matcher import CLIHintMatch, match_capability, rank_cli_hints
from pmcp.manifest.installer import install_server

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
]
