"""Tests for the private/custom manifest overlay in load_manifest()."""

from __future__ import annotations

from pathlib import Path

import pytest

from pmcp.manifest.loader import load_manifest
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools


@pytest.fixture(autouse=True)
def _isolate_overlay_env(monkeypatch, tmp_path):
    """Isolate overlay discovery: HOME → tmp, cwd → tmp, no env override.

    Each test opts back in by creating the specific overlay files it needs.
    Without this, tests could pick up a real ~/.pmcp/manifest.yaml or a
    PMCP_MANIFEST_PATH from the developer's environment.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    # cwd defaults to a project-less dir so _find_project_manifest() returns None
    # unless a test explicitly chdir's somewhere with a .pmcp/manifest.yaml.
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return tmp_path


class _StubClientManager:
    """Minimal ClientManager stub: no servers running."""

    def get_all_server_statuses(self) -> list:
        return []


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_user_overlay_adds_provisionable_server(monkeypatch, tmp_path):
    """A server defined only in ~/.pmcp/manifest.yaml surfaces in the manifest."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        """
servers:
  my-private:
    description: "My private internal server"
    keywords: [myprivatewidget, internalthing]
    command: "npx"
    args: ["-y", "@me/my-mcp"]
    requires_api_key: true
    env_var: MY_TOKEN
""",
    )

    manifest = load_manifest()

    assert "my-private" in manifest.servers
    server = manifest.servers["my-private"]
    assert server.command == "npx"
    assert server.requires_api_key is True
    assert server.env_var == "MY_TOKEN"

    # Surfaces via keyword search.
    _, matching_servers = manifest.search_by_keyword("myprivatewidget")
    assert any(s.name == "my-private" for s in matching_servers)

    # Surfaces as a provision candidate with a permissive policy.
    gateway_tools = GatewayTools(
        client_manager=_StubClientManager(),  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
    )
    candidates = gateway_tools._manifest_candidates_for_query(
        "myprivatewidget",
        manifest=manifest,
        configured_servers={},
        exclude_servers=set(),
    )
    assert any(c.name == "my-private" for c in candidates)


def test_project_overrides_user_overrides_shipped(monkeypatch, tmp_path):
    """project > user > shipped for a same-named server (whole-entry replace)."""
    # Pick a real shipped server name so we override the base too.
    shipped = load_manifest()
    shipped_name = next(iter(shipped.servers))

    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        f"""
servers:
  {shipped_name}:
    description: "user override"
    keywords: [userkw]
    command: "user-command"
    args: []
""",
    )

    project_dir = tmp_path / "proj"
    _write(
        project_dir / ".pmcp" / "manifest.yaml",
        f"""
servers:
  {shipped_name}:
    description: "project override"
    keywords: [projectkw]
    command: "project-command"
    args: []
""",
    )
    monkeypatch.chdir(project_dir)

    manifest = load_manifest()
    assert manifest.servers[shipped_name].command == "project-command"

    # Without the project file, the user override should win over shipped.
    monkeypatch.chdir(tmp_path / "work")
    manifest_user = load_manifest()
    assert manifest_user.servers[shipped_name].command == "user-command"


def test_env_path_override_wins(monkeypatch, tmp_path):
    """PMCP_MANIFEST_PATH wins over user and project overlays."""
    shipped = load_manifest()
    shipped_name = next(iter(shipped.servers))

    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        f"""
servers:
  {shipped_name}:
    description: "user"
    keywords: [u]
    command: "user-command"
    args: []
""",
    )

    project_dir = tmp_path / "proj"
    _write(
        project_dir / ".pmcp" / "manifest.yaml",
        f"""
servers:
  {shipped_name}:
    description: "project"
    keywords: [p]
    command: "project-command"
    args: []
""",
    )
    monkeypatch.chdir(project_dir)

    env_manifest = tmp_path / "env-manifest.yaml"
    _write(
        env_manifest,
        f"""
servers:
  {shipped_name}:
    description: "env"
    keywords: [e]
    command: "env-command"
    args: []
""",
    )
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(env_manifest))

    manifest = load_manifest()
    assert manifest.servers[shipped_name].command == "env-command"


def test_malformed_overlay_is_skipped(monkeypatch, tmp_path):
    """Malformed overlay YAML logs a warning and shipped manifest still loads."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        "servers: [this is not: valid: yaml: at all\n  - broken",
    )

    manifest = load_manifest()  # must not raise
    assert len(manifest.servers) > 0  # shipped servers still present


def test_malformed_entry_skipped_rest_loaded(monkeypatch, tmp_path):
    """One bad entry is skipped while a valid sibling in the same file loads."""
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        """
servers:
  bad-entry: "this should be a mapping, not a string"
  good-entry:
    description: "valid"
    keywords: [goodkw]
    command: "good-command"
    args: []
""",
    )

    manifest = load_manifest()  # must not raise
    assert "good-entry" in manifest.servers
    assert manifest.servers["good-entry"].command == "good-command"
    assert "bad-entry" not in manifest.servers


def test_explicit_path_skips_overlays(monkeypatch, tmp_path):
    """load_manifest(<explicit path>) applies no overlays even when they exist."""
    # User overlay present — must be ignored for an explicit path.
    _write(
        Path.home() / ".pmcp" / "manifest.yaml",
        """
servers:
  user-only:
    description: "user"
    keywords: [u]
    command: "user-command"
    args: []
""",
    )

    explicit = tmp_path / "explicit.yaml"
    _write(
        explicit,
        """
version: "1.0"
servers:
  explicit-only:
    description: "explicit"
    keywords: [x]
    command: "explicit-command"
    args: []
""",
    )

    manifest = load_manifest(explicit)
    assert "explicit-only" in manifest.servers
    assert "user-only" not in manifest.servers
    assert len(manifest.servers) == 1
