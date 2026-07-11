"""Tests for pure startup config resolution."""

from __future__ import annotations

import os

import pytest

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
    secret_key: str | None = None,
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
        secret_key=secret_key,
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


def test_provisioned_manifest_server_is_lazy_by_default_and_eager_when_enabled() -> (
    None
):
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


def test_eager_manifest_server_started_when_namespaced_key_available() -> None:
    """Eager gating must resolve through the server's lookup keys: a credential
    present only under the namespaced storage key must NOT skip the server as
    MISSING_AUTH."""
    manifest = {
        "brightdata": manifest_server(
            "brightdata",
            requires_api_key=True,
            env_var="API_TOKEN",
            secret_key="BRIGHTDATA_API_TOKEN",
        )
    }

    # Auth resolver reports availability only for the namespaced storage key.
    result = resolve_startup_configs(
        [],
        manifest_servers=manifest,
        enabled_auto_start={"brightdata"},
        is_auth_available=lambda key: key == "BRIGHTDATA_API_TOKEN",
    )

    assert result.skipped == []
    assert names(result.eager_configs) == ["brightdata"]


def test_eager_manifest_server_env_resolves_namespaced_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The eager startup config must INJECT the runtime env_var, resolved from
    the namespaced storage key — not merely pass classification. Regression:
    the config previously carried env=None, so an eager namespaced server spawned
    without its credential (os.environ holds the storage key, not the runtime
    env_var)."""
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "bd-secret")

    manifest = {
        "brightdata": manifest_server(
            "brightdata",
            requires_api_key=True,
            env_var="API_TOKEN",
            secret_key="BRIGHTDATA_API_TOKEN",
        )
    }

    result = resolve_startup_configs(
        [],
        manifest_servers=manifest,
        enabled_auto_start={"brightdata"},
        is_auth_available=lambda key: bool(os.environ.get(key)),
    )

    assert names(result.eager_configs) == ["brightdata"]
    # The runtime env_var is injected with the value stored under the namespaced
    # key, so _connect_stdio spawns @brightdata/mcp with API_TOKEN present.
    assert result.eager_configs[0].config.env == {"API_TOKEN": "bd-secret"}


def test_eager_manifest_server_skipped_when_no_lookup_key_available() -> None:
    """When neither the namespaced key nor the legacy env_var is available, the
    eager server is still skipped as MISSING_AUTH."""
    manifest = {
        "brightdata": manifest_server(
            "brightdata",
            requires_api_key=True,
            env_var="API_TOKEN",
            secret_key="BRIGHTDATA_API_TOKEN",
        )
    }

    result = resolve_startup_configs(
        [],
        manifest_servers=manifest,
        enabled_auto_start={"brightdata"},
        is_auth_available=lambda _key: False,
    )

    assert names(result.eager_configs) == []
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == StartupSkipReason.MISSING_AUTH
    assert result.skipped[0].env_var == "API_TOKEN"


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
