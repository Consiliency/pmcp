"""Tests for pure startup config resolution."""

from __future__ import annotations

from pmcp.config.loader import (
    StartupSkipReason,
    resolve_startup_configs,
)
from pmcp.manifest.loader import ServerConfig
from pmcp.types import (
    LocalMcpServerConfig,
    RemoteMcpServerConfig,
    ResolvedServerConfig,
)


def local_config(
    name: str,
    *,
    source: str = "project",
    command: str = "echo",
) -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name,
        source=source,  # type: ignore[arg-type]
        config=LocalMcpServerConfig(command=command),
    )


def remote_config(name: str) -> ResolvedServerConfig:
    return ResolvedServerConfig(
        name=name,
        source="project",
        config=RemoteMcpServerConfig(url="https://example.com/mcp"),
    )


def manifest_server(
    name: str,
    *,
    command: str = "npx",
    auto_start: bool = False,
    requires_api_key: bool = False,
    env_var: str | None = None,
) -> ServerConfig:
    return ServerConfig(
        name=name,
        description=f"{name} server",
        keywords=[name],
        install={},
        command=command,
        args=["server"],
        auto_start=auto_start,
        requires_api_key=requires_api_key,
        env_var=env_var,
    )


def names(configs: list[ResolvedServerConfig]) -> list[str]:
    return [config.name for config in configs]


def test_configured_local_server_is_lazy_unless_enabled() -> None:
    config = local_config("configured")

    lazy = resolve_startup_configs([config])
    eager = resolve_startup_configs([config], enabled_auto_start={"configured"})

    assert names(lazy.lazy_configs) == ["configured"]
    assert lazy.eager_configs == []
    assert eager.lazy_configs == []
    assert names(eager.eager_configs) == ["configured"]


def test_configured_remote_server_is_preserved_when_enabled() -> None:
    config = remote_config("remote")

    result = resolve_startup_configs([config], enabled_auto_start={"remote"})

    assert names(result.eager_configs) == ["remote"]
    assert result.eager_configs[0] is config
    assert result.eager_configs[0].config.type == "remote"


def test_manifest_only_explicit_auto_start_is_eager() -> None:
    manifest = {"manifest-only": manifest_server("manifest-only")}

    result = resolve_startup_configs(
        [],
        manifest_servers=manifest,
        enabled_auto_start={"manifest-only"},
    )

    assert result.lazy_configs == []
    assert names(result.eager_configs) == ["manifest-only"]
    assert result.eager_configs[0].source == "manifest"


def test_configured_server_wins_over_manifest_collision() -> None:
    configured = local_config("shared", command="configured-cmd")
    manifest = {"shared": manifest_server("shared", command="manifest-cmd")}

    result = resolve_startup_configs(
        [configured],
        manifest_servers=manifest,
        enabled_auto_start={"shared"},
    )

    assert names(result.eager_configs) == ["shared"]
    assert result.eager_configs[0] is configured
    assert result.eager_configs[0].config.type == "local"
    assert result.eager_configs[0].config.command == "configured-cmd"


def test_provisioned_manifest_server_is_lazy_by_default_and_eager_when_enabled() -> None:
    manifest = {"playwright": manifest_server("playwright")}

    lazy = resolve_startup_configs(
        [],
        manifest_servers=manifest,
        provisioned_server_names={"playwright"},
    )
    eager = resolve_startup_configs(
        [],
        manifest_servers=manifest,
        enabled_auto_start={"playwright"},
        provisioned_server_names={"playwright"},
    )

    assert names(lazy.lazy_configs) == ["playwright"]
    assert lazy.eager_configs == []
    assert eager.lazy_configs == []
    assert names(eager.eager_configs) == ["playwright"]


def test_unknown_auto_start_and_provisioned_names_are_skipped() -> None:
    result = resolve_startup_configs(
        [],
        manifest_servers={},
        enabled_auto_start={"missing-auto"},
        provisioned_server_names={"missing-provisioned"},
    )

    assert result.lazy_configs == []
    assert result.eager_configs == []
    assert [(skip.name, skip.reason) for skip in result.skipped] == [
        ("missing-auto", StartupSkipReason.UNKNOWN_AUTO_START),
        ("missing-provisioned", StartupSkipReason.UNKNOWN_PROVISIONED),
    ]


def test_policy_denied_server_is_excluded_with_skip_reason() -> None:
    result = resolve_startup_configs(
        [local_config("denied")],
        is_server_allowed=lambda name: name != "denied",
    )

    assert result.lazy_configs == []
    assert result.eager_configs == []
    assert len(result.skipped) == 1
    assert result.skipped[0].name == "denied"
    assert result.skipped[0].reason == StartupSkipReason.POLICY_DENIED


def test_missing_auth_eager_manifest_server_is_skipped() -> None:
    manifest = {
        "needs-key": manifest_server(
            "needs-key",
            requires_api_key=True,
            env_var="NEEDS_KEY",
        )
    }

    result = resolve_startup_configs(
        [],
        manifest_servers=manifest,
        enabled_auto_start={"needs-key"},
        is_auth_available=lambda _env_var: False,
    )

    assert result.lazy_configs == []
    assert result.eager_configs == []
    assert len(result.skipped) == 1
    assert result.skipped[0].name == "needs-key"
    assert result.skipped[0].reason == StartupSkipReason.MISSING_AUTH
    assert result.skipped[0].env_var == "NEEDS_KEY"


def test_missing_auth_lazy_manifest_server_remains_lazy() -> None:
    manifest = {
        "needs-key": manifest_server(
            "needs-key",
            requires_api_key=True,
            env_var="NEEDS_KEY",
        )
    }

    result = resolve_startup_configs(
        [],
        manifest_servers=manifest,
        is_auth_available=lambda _env_var: False,
    )

    assert names(result.lazy_configs) == ["needs-key"]
    assert result.eager_configs == []
    assert result.skipped == []


def test_legacy_manifest_auto_start_compatibility_can_make_manifest_eager() -> None:
    manifest = {"legacy": manifest_server("legacy", auto_start=True)}

    current = resolve_startup_configs([], manifest_servers=manifest)
    legacy = resolve_startup_configs(
        [],
        manifest_servers=manifest,
        legacy_manifest_auto_start=True,
    )

    assert names(current.lazy_configs) == ["legacy"]
    assert current.eager_configs == []
    assert legacy.lazy_configs == []
    assert names(legacy.eager_configs) == ["legacy"]
