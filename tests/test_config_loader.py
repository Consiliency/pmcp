"""Tests for config loader."""

from __future__ import annotations

import json
from pathlib import Path


from pmcp.config.loader import (
    load_disabled_auto_start,
    load_enabled_auto_start,
    load_configs,
    make_tool_id,
    manifest_server_to_config,
    parse_tool_id,
)
from pmcp.manifest.loader import ServerConfig


class TestMakeToolId:
    """Tests for make_tool_id."""

    def test_creates_tool_id(self) -> None:
        assert make_tool_id("github", "create_issue") == "github::create_issue"
        assert make_tool_id("my-server", "my-tool") == "my-server::my-tool"


class TestParseToolId:
    """Tests for parse_tool_id."""

    def test_parses_valid_tool_ids(self) -> None:
        result = parse_tool_id("github::create_issue")
        assert result == ("github", "create_issue")

    def test_returns_none_for_invalid(self) -> None:
        assert parse_tool_id("invalid") is None
        assert parse_tool_id("too::many::parts") is None
        assert parse_tool_id("") is None


class TestLoadConfigs:
    """Tests for load_configs."""

    def test_loads_project_config(self, tmp_path: Path) -> None:
        # Create project config
        project_config = {
            "mcpServers": {
                "test-server": {
                    "command": "node",
                    "args": ["server.js"],
                }
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(project_config))

        configs = load_configs(
            project_root=tmp_path,
            user_config_paths=[],  # No user configs
        )

        assert len(configs) == 1
        assert configs[0].name == "test-server"
        assert configs[0].source == "project"
        cfg = configs[0].config
        assert cfg.type == "local"
        assert cfg.command == "node"
        assert cfg.args == ["server.js"]

    def test_merges_configs_with_precedence(self, tmp_path: Path) -> None:
        # Create project config
        project_config = {
            "mcpServers": {
                "shared-server": {"command": "project-cmd"},
                "project-only": {"command": "project-only-cmd"},
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(project_config))

        # Create user config
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        user_config = {
            "mcpServers": {
                "shared-server": {"command": "user-cmd"},  # Should be overridden
                "user-only": {"command": "user-only-cmd"},
            }
        }
        user_config_path = user_dir / "user.mcp.json"
        user_config_path.write_text(json.dumps(user_config))

        configs = load_configs(
            project_root=tmp_path,
            user_config_paths=[user_config_path],
        )

        assert len(configs) == 3

        # Project 'shared-server' should take precedence
        shared = next(c for c in configs if c.name == "shared-server")
        assert shared.source == "project"
        assert shared.config.type == "local"
        assert shared.config.command == "project-cmd"

        # Both unique servers should be present
        assert any(c.name == "project-only" for c in configs)
        assert any(c.name == "user-only" for c in configs)

    def test_handles_missing_files(self, tmp_path: Path) -> None:
        configs = load_configs(
            project_root=tmp_path / "nonexistent",
            user_config_paths=[tmp_path / "nonexistent.json"],
        )
        assert len(configs) == 0

    def test_handles_invalid_json(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text("invalid json {{{")

        configs = load_configs(
            project_root=tmp_path,
            user_config_paths=[],
        )
        assert len(configs) == 0

    def test_normalizes_relative_paths(self, tmp_path: Path) -> None:
        project_config = {
            "mcpServers": {
                "test-server": {
                    "command": "./bin/server",
                    "cwd": "./data",
                }
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(project_config))

        configs = load_configs(
            project_root=tmp_path,
            user_config_paths=[],
        )

        cfg = configs[0].config
        assert cfg.type == "local"
        assert cfg.command == str(tmp_path / "bin" / "server")
        assert cfg.cwd == str(tmp_path / "data")

    def test_keeps_remote_entries(self, tmp_path: Path) -> None:
        project_config = {
            "mcpServers": {
                "gateway": {
                    "type": "sse",
                    "url": "http://127.0.0.1:3344/sse",
                },
                "local": {
                    "command": "node",
                    "args": ["server.js"],
                },
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(project_config))

        configs = load_configs(
            project_root=tmp_path,
            user_config_paths=[],
        )

        assert len(configs) == 2
        gateway = next(c for c in configs if c.name == "gateway")
        assert gateway.config.type == "sse"
        assert gateway.config.url == "http://127.0.0.1:3344/sse"

        local = next(c for c in configs if c.name == "local")
        assert local.config.type == "local"
        assert local.config.command == "node"

    def test_remote_entries_preserve_optional_auth_metadata(
        self, tmp_path: Path
    ) -> None:
        project_config = {
            "mcpServers": {
                "remote-auth": {
                    "type": "remote",
                    "url": "https://mcp.example/mcp",
                    "protected_resource_metadata_url": "https://mcp.example/.well-known/oauth-protected-resource",
                    "authorization_server_metadata_url": "https://auth.example/.well-known/oauth-authorization-server",
                    "oidc_issuer_url": "https://issuer.example",
                    "declared_scopes": ["read"],
                    "supports_url_elicitation": True,
                    "future_field": "ignored",
                }
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(project_config))

        configs = load_configs(project_root=tmp_path, user_config_paths=[])

        cfg = configs[0].config
        assert cfg.type == "remote"
        assert (
            cfg.protected_resource_metadata_url
            == "https://mcp.example/.well-known/oauth-protected-resource"
        )
        assert cfg.declared_scopes == ["read"]
        assert cfg.supports_url_elicitation is True

    def test_coerces_legacy_url_entry_to_remote(self, tmp_path: Path) -> None:
        project_config = {
            "mcpServers": {
                "gateway": {
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer test"},
                }
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(project_config))

        configs = load_configs(
            project_root=tmp_path,
            user_config_paths=[],
        )

        assert len(configs) == 1
        assert configs[0].name == "gateway"
        assert configs[0].config.type == "remote"
        assert configs[0].config.url == "https://example.com/mcp"

    def test_converts_remote_manifest_server_to_remote_config(self) -> None:
        server = ServerConfig(
            name="excalidraw",
            description="Excalidraw whiteboard",
            keywords=["excalidraw"],
            install={},
            command="",
            args=[],
            transport="streamable-http",
            url="https://mcp.excalidraw.com",
        )

        resolved = manifest_server_to_config(server)

        assert resolved.name == "excalidraw"
        assert resolved.source == "manifest"
        assert resolved.config.type == "streamable-http"
        assert resolved.config.url == "https://mcp.excalidraw.com"

    def test_merges_manifest_defaults_for_partial_server_config(
        self, tmp_path: Path
    ) -> None:
        project_config = {
            "mcpServers": {
                "playwright": {
                    "args": ["--cdp-endpoint", "http://localhost:9222"],
                }
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(project_config))

        configs = load_configs(
            project_root=tmp_path,
            user_config_paths=[],
        )

        assert len(configs) == 1
        assert configs[0].name == "playwright"
        cfg = configs[0].config
        assert cfg.type == "local"
        assert cfg.command == "npx"
        assert cfg.args == [
            "-y",
            "@playwright/mcp@latest",
            "--cdp-endpoint",
            "http://localhost:9222",
        ]

    def test_skips_partial_server_without_manifest_default(
        self, tmp_path: Path
    ) -> None:
        project_config = {
            "mcpServers": {
                "custom-server": {
                    "args": ["--debug"],
                }
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(project_config))

        configs = load_configs(
            project_root=tmp_path,
            user_config_paths=[],
        )

        assert configs == []


class TestLoadAutoStartPolicy:
    """Tests for startup policy aggregation from config files."""

    def test_loads_enabled_auto_start_from_all_config_sources(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"autoStart": ["project-server", "shared-server"]})
        )

        user_config_path = tmp_path / "user.mcp.json"
        user_config_path.write_text(
            json.dumps({"autoStart": ["user-server", "shared-server"]})
        )

        custom_config_path = tmp_path / "custom.mcp.json"
        custom_config_path.write_text(
            json.dumps({"autoStart": ["custom-server", "shared-server"]})
        )

        enabled = load_enabled_auto_start(
            project_root=tmp_path,
            user_config_paths=[user_config_path],
            custom_config_path=custom_config_path,
        )

        assert enabled == {
            "project-server",
            "user-server",
            "custom-server",
            "shared-server",
        }

    def test_auto_start_policy_lists_are_unioned_not_overridden(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared-server": {"command": "project-cmd"},
                    },
                    "autoStart": ["project-server"],
                }
            )
        )

        user_config_path = tmp_path / "user.mcp.json"
        user_config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared-server": {"command": "user-cmd"},
                    },
                    "autoStart": ["shared-server", "user-server"],
                }
            )
        )

        configs = load_configs(
            project_root=tmp_path,
            user_config_paths=[user_config_path],
        )
        enabled = load_enabled_auto_start(
            project_root=tmp_path,
            user_config_paths=[user_config_path],
        )

        shared = next(c for c in configs if c.name == "shared-server")
        assert shared.source == "project"
        assert shared.config.type == "local"
        assert shared.config.command == "project-cmd"
        assert enabled == {"project-server", "shared-server", "user-server"}

    def test_loads_disabled_auto_start_from_all_config_sources(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"disableAutoStart": ["project-server", "shared-server"]})
        )

        user_config_path = tmp_path / "user.mcp.json"
        user_config_path.write_text(
            json.dumps({"disableAutoStart": ["user-server", "shared-server"]})
        )

        custom_config_path = tmp_path / "custom.mcp.json"
        custom_config_path.write_text(
            json.dumps({"disableAutoStart": ["custom-server", "shared-server"]})
        )

        disabled = load_disabled_auto_start(
            project_root=tmp_path,
            user_config_paths=[user_config_path],
            custom_config_path=custom_config_path,
        )

        assert disabled == {
            "project-server",
            "user-server",
            "custom-server",
            "shared-server",
        }
