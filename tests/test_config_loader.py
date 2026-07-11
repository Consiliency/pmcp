"""Tests for config loader."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from pmcp.config.loader import (
    _manifest_server_to_config,
    build_startup_observation_snapshot,
    find_project_root,
    get_startup_policy,
    load_disabled_auto_start,
    load_enabled_auto_start,
    load_config_sources,
    load_configs,
    make_tool_id,
    manifest_server_to_config,
    parse_tool_id,
    resolve_startup_configs,
    set_startup_policy,
)
from pmcp.manifest.loader import (
    ServerConfig,
    credential_lookup_keys,
    credential_storage_key,
)
from pmcp.types import (
    RemoteMcpServerConfig,
    ResolvedServerConfig,
    StartupPolicyOperation,
)


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

    def test_find_project_root_finds_nested_project_marker(
        self, tmp_path: Path
    ) -> None:
        project = tmp_path / "project"
        nested = project / "a" / "b"
        nested.mkdir(parents=True)
        (project / "pyproject.toml").write_text("[project]\nname = 'example'\n")

        assert find_project_root(nested) == project

    def test_find_project_root_ignores_unrelated_temp_ancestor_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parent = tmp_path / "ancestor"
        project = parent / "project"
        nested = project / "child"
        nested.mkdir(parents=True)
        (parent / ".git").mkdir()
        monkeypatch.setattr(
            "pmcp.config.loader.tempfile.gettempdir", lambda: str(parent)
        )

        assert find_project_root(nested) is None

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

    def test_load_configs_honors_runtime_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default user config paths must resolve HOME at call time, not import
        time — so a patched/changed HOME is honored and a test never leaks the
        developer's real ~/.mcp.json into the loaded config set."""
        from pmcp.config.loader import default_user_config_paths

        fake_home = tmp_path / "home"
        (fake_home).mkdir()
        (fake_home / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"home-server": {"command": "home-cmd"}}})
        )
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

        # The call-time helper reflects the patched HOME...
        assert default_user_config_paths()[0] == fake_home / ".mcp.json"
        # ...and load_configs (no explicit user_config_paths) picks up ONLY that
        # user config, not any real ~/.mcp.json.
        empty_project = tmp_path / "proj"
        empty_project.mkdir()
        names = {c.name for c in load_configs(project_root=empty_project)}
        assert names == {"home-server"}

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

    def test_malformed_config_is_surfaced_but_startup_proceeds(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Malformed project config: its servers are silently dropped today.
        (tmp_path / ".mcp.json").write_text("invalid json {{{")
        # A valid user config that must still load despite the broken project file.
        user_path = tmp_path / "user.mcp.json"
        user_path.write_text(
            json.dumps({"mcpServers": {"good": {"command": "echo", "args": ["hi"]}}})
        )

        with caplog.at_level(logging.WARNING, logger="pmcp.config.loader"):
            configs = load_configs(
                project_root=tmp_path,
                user_config_paths=[user_path],
            )

        # Startup still proceeds: the valid user server loads.
        assert [c.name for c in configs] == ["good"]
        # The malformed file's failure is surfaced (path + disabled note).
        assert any(
            "malformed config" in record.message.lower()
            and str(tmp_path / ".mcp.json") in record.message
            for record in caplog.records
            if record.levelno >= logging.WARNING
        )

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

    def _local_server(self, **overrides: object) -> ServerConfig:
        base: dict[str, object] = dict(
            name="brightdata",
            description="Bright Data",
            keywords=["brightdata"],
            install={},
            command="npx",
            args=["-y", "@brightdata/mcp"],
            requires_api_key=True,
            env_var="API_TOKEN",
        )
        base.update(overrides)
        return ServerConfig(**base)  # type: ignore[arg-type]

    def test_credential_keys_prefer_namespaced_secret_key(self) -> None:
        server = self._local_server(secret_key="BRIGHTDATA_API_TOKEN")
        assert credential_storage_key(server) == "BRIGHTDATA_API_TOKEN"
        # storage key first, then the runtime env_var as a legacy fallback
        assert credential_lookup_keys(server) == ["BRIGHTDATA_API_TOKEN", "API_TOKEN"]

    def test_credential_keys_default_to_env_var(self) -> None:
        server = self._local_server()
        assert credential_storage_key(server) == "API_TOKEN"
        assert credential_lookup_keys(server) == ["API_TOKEN"]

    def test_injects_credential_from_namespaced_storage_key(self) -> None:
        server = self._local_server(secret_key="BRIGHTDATA_API_TOKEN")
        store = {"BRIGHTDATA_API_TOKEN": "tok-namespaced"}

        resolved = _manifest_server_to_config(server, store.get)

        # Resolved from the namespaced key but injected under the runtime env_var
        # the downstream @brightdata/mcp process actually reads.
        assert resolved.config.env == {"API_TOKEN": "tok-namespaced"}

    def test_injects_credential_falls_back_to_legacy_env_var(self) -> None:
        server = self._local_server(secret_key="BRIGHTDATA_API_TOKEN")
        # Pre-upgrade install: credential still stored under the legacy env_var.
        store = {"API_TOKEN": "tok-legacy"}

        resolved = _manifest_server_to_config(server, store.get)

        assert resolved.config.env == {"API_TOKEN": "tok-legacy"}

    def test_namespaced_key_wins_over_legacy_when_both_present(self) -> None:
        server = self._local_server(secret_key="BRIGHTDATA_API_TOKEN")
        store = {"BRIGHTDATA_API_TOKEN": "tok-new", "API_TOKEN": "tok-old"}

        resolved = _manifest_server_to_config(server, store.get)

        assert resolved.config.env == {"API_TOKEN": "tok-new"}

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

    def test_eager_remote_with_missing_header_placeholder_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("REMOTE_API_TOKEN", raising=False)
        config = ResolvedServerConfig(
            name="remote-api",
            source="project",
            config=RemoteMcpServerConfig(
                type="remote",
                url="https://example.com/sse",
                headers={"Authorization": "Bearer ${REMOTE_API_TOKEN}"},
            ),
        )

        resolution = resolve_startup_configs(
            [config],
            enabled_auto_start={"remote-api"},
        )
        observation = build_startup_observation_snapshot(resolution)["remote-api"]

        assert resolution.eager_configs == []
        assert resolution.skipped[0].reason.value == "missing_auth"
        assert resolution.skipped[0].env_var == "REMOTE_API_TOKEN"
        assert resolution.skipped[0].missing_env_vars == ["REMOTE_API_TOKEN"]
        assert observation.startup_env_var == "REMOTE_API_TOKEN"
        assert observation.missing_env_vars == ["REMOTE_API_TOKEN"]

    @pytest.mark.parametrize("remote_type", ["sse", "http", "streamable-http"])
    def test_eager_remote_header_detection_covers_remote_types(
        self, remote_type: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("REMOTE_API_TOKEN", raising=False)
        config = ResolvedServerConfig(
            name=f"remote-{remote_type}",
            source="project",
            config=RemoteMcpServerConfig(
                type=remote_type,  # type: ignore[arg-type]
                url="https://example.com/mcp",
                headers={"Authorization": "Bearer ${REMOTE_API_TOKEN}"},
            ),
        )

        resolution = resolve_startup_configs(
            [config],
            enabled_auto_start={config.name},
        )

        assert resolution.skipped[0].missing_env_vars == ["REMOTE_API_TOKEN"]

    def test_literal_and_present_remote_headers_remain_eager(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REMOTE_API_TOKEN", "secret-token")
        literal = ResolvedServerConfig(
            name="literal",
            source="project",
            config=RemoteMcpServerConfig(
                url="https://example.com/sse",
                headers={"X-Static": "literal"},
            ),
        )
        present = ResolvedServerConfig(
            name="present",
            source="project",
            config=RemoteMcpServerConfig(
                url="https://example.com/sse",
                headers={"Authorization": "Bearer ${REMOTE_API_TOKEN}"},
            ),
        )

        resolution = resolve_startup_configs(
            [literal, present],
            enabled_auto_start={"literal", "present"},
        )

        assert [config.name for config in resolution.eager_configs] == [
            "literal",
            "present",
        ]
        assert resolution.skipped == []

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

    def test_source_aware_policy_reports_paths_and_conflicts(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {"known": {"command": "node"}},
                    "autoStart": ["known", "stale", "conflict"],
                    "disableAutoStart": ["conflict"],
                }
            )
        )

        sources = load_config_sources(project_root=tmp_path, user_config_paths=[])
        policy = get_startup_policy(
            project_root=tmp_path,
            user_config_paths=[],
            known_server_names={"known"},
        )

        assert sources[0].source == "project"
        assert sources[0].path == tmp_path / ".mcp.json"
        assert policy.sources[0].autoStart == ["known", "stale", "conflict"]
        assert {diagnostic.code for diagnostic in policy.diagnostics} == {
            "auto_start_disabled_conflict",
            "stale_auto_start",
            "stale_disable_auto_start",
        }

    def test_startup_policy_preview_and_apply_preserves_unrelated_keys(
        self, tmp_path: Path
    ) -> None:
        config_path = tmp_path / ".mcp.json"
        config_path.write_text(
            json.dumps(
                {
                    "mcpServers": {"existing": {"command": "node"}},
                    "autoStart": ["existing"],
                    "unrelated": {"keep": True},
                }
            )
        )

        preview = set_startup_policy(
            StartupPolicyOperation(
                operation="add",
                names=["new", "existing"],
                source="project",
            ),
            project_root=tmp_path,
            user_config_paths=[],
        )
        assert preview.changed is True
        assert preview.after_autoStart == ["existing", "new"]
        assert json.loads(config_path.read_text())["autoStart"] == ["existing"]

        applied = set_startup_policy(
            StartupPolicyOperation(
                operation="add",
                names=["new", "existing"],
                source="project",
                dry_run=False,
                apply=True,
            ),
            project_root=tmp_path,
            user_config_paths=[],
        )

        written = json.loads(config_path.read_text())
        assert applied.changed is True
        assert written["autoStart"] == ["existing", "new"]
        assert written["unrelated"] == {"keep": True}
