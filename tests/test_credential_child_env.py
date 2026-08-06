"""P5 SL-4.6: IF-0-P5-4 child-environment consistency invariant.

For any server, ``credential_requirement(server).relaxed_by is not None``
implies that name is present with the same non-empty value in all three
child-env constructions: path A (``build_install_child_env``), path B
(``sanitized_subprocess_env(_manifest_server_to_config(...).config.env)``),
and path C (the configured-duplicate merge, ``_merge_manifest_defaults``).

Run under a configuration where the relaxer is ALSO a PMCP-managed secret
key (registered in a tmp ``pmcp.env``) — that is the configuration
``sanitized_subprocess_env`` strips from ``os.environ`` before applying
``own_env``. This test fails on a predicate that reads ``os.environ`` and on
any future refactor that reorders the ``managed_secret_keys`` strip after
``own_env`` is applied.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from pmcp.config.loader import _manifest_server_to_config, _merge_manifest_defaults
from pmcp.env_store import sanitized_subprocess_env
from pmcp.manifest.installer import build_install_child_env
from pmcp.manifest.loader import ServerConfig, credential_requirement
from pmcp.types import LocalMcpServerConfig


class TestRealSymbolsRealArity:
    """A first assertion that imports all three symbols by name and calls each
    with its real arity, so a rename or signature change breaks loudly
    instead of the test silently testing nothing."""

    def test_build_install_child_env_arity(self) -> None:
        sig = inspect.signature(build_install_child_env)
        assert list(sig.parameters) == ["server_config"]

    def test_manifest_server_to_config_requires_two_args(self) -> None:
        sig = inspect.signature(_manifest_server_to_config)
        assert list(sig.parameters) == ["server", "env_lookup"]
        server = _relaxed_server()
        with pytest.raises(TypeError):
            _manifest_server_to_config(server)  # type: ignore[call-arg]

    def test_merge_manifest_defaults_arity(self) -> None:
        sig = inspect.signature(_merge_manifest_defaults)
        assert list(sig.parameters) == ["name", "config", "manifest_servers"]


def _relaxed_server(**overrides: object) -> ServerConfig:
    base: dict[str, object] = dict(
        name="firecrawl",
        description="firecrawl",
        keywords=["firecrawl"],
        install={},
        command="npx",
        args=["-y", "firecrawl-mcp"],
        requires_api_key=True,
        env_var="FIRECRAWL_API_KEY",
        api_key_optional_when=["FIRECRAWL_API_URL"],
        extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
    )
    base.update(overrides)
    return ServerConfig(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _register_relaxer_as_managed_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Register FIRECRAWL_API_URL as a PMCP-managed secret key in a tmp
    pmcp.env — the exact configuration sanitized_subprocess_env strips from
    os.environ. Also puts a DIFFERENT value in os.environ directly, to prove
    the predicate/child-env never derive their answer from os.environ."""
    home = tmp_path / "home"
    (home / ".config" / "pmcp").mkdir(parents=True)
    (home / ".config" / "pmcp" / "pmcp.env").write_text(
        "FIRECRAWL_API_URL=http://localhost:3002\n"
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FIRECRAWL_API_URL", "http://localhost:3002")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


class TestChildEnvConsistencyAcrossPaths:
    def test_relaxed_by_present_identically_in_paths_a_and_b(self) -> None:
        server = _relaxed_server()
        requirement = credential_requirement(server)
        assert requirement.relaxed_by == "FIRECRAWL_API_URL"

        # Path A — build_install_child_env already sanitizes; do not wrap.
        path_a_env = build_install_child_env(server)

        # Path B — sanitized_subprocess_env(_manifest_server_to_config(...).config.env)
        # env_lookup intentionally resolves nothing ({}.get), isolating this
        # assertion to the extra_env carriage, not credential resolution.
        resolved = _manifest_server_to_config(server, {}.get)
        assert isinstance(resolved.config, LocalMcpServerConfig)
        path_b_env = sanitized_subprocess_env(resolved.config.env)

        assert path_a_env.get("FIRECRAWL_API_URL") == "http://localhost:3002"
        assert path_b_env.get("FIRECRAWL_API_URL") == "http://localhost:3002"

        # The managed-secret-key strip must not have leaked into either path
        # (own_env is applied AFTER the strip in both, per IF-0-P5-4).
        assert path_a_env.get("FIRECRAWL_API_KEY") is None
        assert path_b_env.get("FIRECRAWL_API_KEY") is None

    def test_path_c_configured_duplicate_no_override_relaxes(self) -> None:
        server = _relaxed_server()
        config = LocalMcpServerConfig(command="npx", args=["-y", "firecrawl-mcp"])

        merged = _merge_manifest_defaults("firecrawl", config, {"firecrawl": server})

        assert merged is not None
        assert merged.env is not None
        assert merged.env.get("FIRECRAWL_API_URL") == "http://localhost:3002"
        requirement = credential_requirement(server, child_env=merged.env)
        assert requirement.required is False
        assert requirement.relaxed_by == "FIRECRAWL_API_URL"

    def test_path_c_empty_string_override_fails_closed(self) -> None:
        """.mcp.json sets the relaxer to '' — the merge keeps the configured
        value (a genuine override per _merge_manifest_defaults), the child
        gets a dead literal, and the predicate must report required=True."""
        server = _relaxed_server()
        config = LocalMcpServerConfig(
            command="npx",
            args=["-y", "firecrawl-mcp"],
            env={"FIRECRAWL_API_URL": ""},
        )

        merged = _merge_manifest_defaults("firecrawl", config, {"firecrawl": server})

        assert merged is not None
        assert merged.env == {"FIRECRAWL_API_URL": ""}
        requirement = credential_requirement(server, child_env=merged.env)
        assert requirement.required is True
        assert requirement.relaxed_by is None

    def test_path_c_unexpanded_placeholder_override_fails_closed(self) -> None:
        """.mcp.json sets the relaxer to an unexpanded ${VAR} placeholder —
        same fail-closed outcome as the empty-string case."""
        server = _relaxed_server()
        config = LocalMcpServerConfig(
            command="npx",
            args=["-y", "firecrawl-mcp"],
            env={"FIRECRAWL_API_URL": "${FIRECRAWL_API_URL}"},
        )

        merged = _merge_manifest_defaults("firecrawl", config, {"firecrawl": server})

        assert merged is not None
        assert merged.env == {"FIRECRAWL_API_URL": "${FIRECRAWL_API_URL}"}
        requirement = credential_requirement(server, child_env=merged.env)
        assert requirement.required is True
        assert requirement.relaxed_by is None
