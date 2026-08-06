"""P5 gate tests: startup, install, diagnostics, and `pmcp init` (consumers 1,
2, 5, 7). Each gate is tested in both directions per plans/phase-plan-v11-P5.md:
a relaxed server must pass, and a genuinely-required server must still fail
closed. Consumers 3, 4, 6 live in tests/test_credential_gates_handlers.py
(SL-3); the seven-consumer end-to-end proof lives in
tests/test_credential_optionality_e2e.py (SL-4).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from pmcp.config.loader import StartupSkipReason, resolve_startup_configs
from pmcp.manifest.installer import MissingApiKeyError, check_api_key
from pmcp.manifest.loader import ServerConfig
from pmcp.types import LocalMcpServerConfig, ResolvedServerConfig


def _relaxable_server(
    *,
    requires_api_key: bool = True,
    env_var: str | None = "FIRECRAWL_API_KEY",
    api_key_optional_when: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> ServerConfig:
    return ServerConfig(
        name="firecrawl",
        description="firecrawl server",
        keywords=["firecrawl"],
        install={},
        command="firecrawl-mcp",
        args=[],
        auto_start=True,
        requires_api_key=requires_api_key,
        env_var=env_var,
        api_key_optional_when=api_key_optional_when or [],
        extra_env=extra_env or {},
    )


# ---------------------------------------------------------------------------
# Gate 1 — eager startup (config/loader.py resolve_startup_configs)
# ---------------------------------------------------------------------------


class TestGate1EagerStartup:
    def test_relaxed_server_becomes_eager(self) -> None:
        server = _relaxable_server(
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
        )
        result = resolve_startup_configs(
            [],
            manifest_servers={"firecrawl": server},
            enabled_auto_start={"firecrawl"},
            is_auth_available=lambda _key: False,
        )
        assert [c.name for c in result.eager_configs] == ["firecrawl"]
        assert result.skipped == []

    def test_required_server_still_skipped_missing_auth(self) -> None:
        server = _relaxable_server(api_key_optional_when=[])
        result = resolve_startup_configs(
            [],
            manifest_servers={"firecrawl": server},
            enabled_auto_start={"firecrawl"},
            is_auth_available=lambda _key: False,
        )
        assert result.eager_configs == []
        assert len(result.skipped) == 1
        assert result.skipped[0].reason == StartupSkipReason.MISSING_AUTH
        assert result.skipped[0].name == "firecrawl"

    def test_required_server_with_credential_still_eager(self) -> None:
        server = _relaxable_server(api_key_optional_when=[])
        result = resolve_startup_configs(
            [],
            manifest_servers={"firecrawl": server},
            enabled_auto_start={"firecrawl"},
            is_auth_available=lambda key: key == "FIRECRAWL_API_KEY",
        )
        assert [c.name for c in result.eager_configs] == ["firecrawl"]
        assert result.skipped == []


class TestGate1ConfiguredDuplicate:
    """Board review finding 1: a .mcp.json entry ("configured" source) whose
    name duplicates a manifest server was previously never credential-gated
    at all here — add_config's manifest_server param was always None for the
    configured-entries loop, so a genuinely-required, credential-less
    configured duplicate reached eager_configs unconditionally."""

    def test_configured_duplicate_required_and_no_credential_is_skipped(
        self,
    ) -> None:
        server = _relaxable_server(api_key_optional_when=[])
        configured = ResolvedServerConfig(
            name="firecrawl",
            source="project",
            config=LocalMcpServerConfig(command="firecrawl-mcp"),
        )
        result = resolve_startup_configs(
            [configured],
            manifest_servers={"firecrawl": server},
            enabled_auto_start={"firecrawl"},
            is_auth_available=lambda _key: False,
        )
        assert result.eager_configs == []
        assert len(result.skipped) == 1
        assert result.skipped[0].reason == StartupSkipReason.MISSING_AUTH
        assert result.skipped[0].name == "firecrawl"

    def test_configured_duplicate_relaxed_via_own_env_is_eager(self) -> None:
        server = _relaxable_server(
            api_key_optional_when=["FIRECRAWL_API_URL"],
        )
        # The configured entry's OWN env supplies the relaxer — not the
        # manifest's extra_env (which is empty here) — proving child_env,
        # not the manifest default, is what's judged.
        configured = ResolvedServerConfig(
            name="firecrawl",
            source="project",
            config=LocalMcpServerConfig(
                command="firecrawl-mcp",
                env={"FIRECRAWL_API_URL": "http://localhost:3002"},
            ),
        )
        result = resolve_startup_configs(
            [configured],
            manifest_servers={"firecrawl": server},
            enabled_auto_start={"firecrawl"},
            is_auth_available=lambda _key: False,
        )
        assert [c.name for c in result.eager_configs] == ["firecrawl"]
        assert result.skipped == []

    def test_configured_duplicate_empty_string_override_fails_closed(self) -> None:
        server = _relaxable_server(
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
        )
        # .mcp.json overrides the relaxer to an empty string — the child gets
        # a dead literal, so this must still fail closed even though the
        # manifest's own extra_env has a usable value.
        configured = ResolvedServerConfig(
            name="firecrawl",
            source="project",
            config=LocalMcpServerConfig(
                command="firecrawl-mcp",
                env={"FIRECRAWL_API_URL": ""},
            ),
        )
        result = resolve_startup_configs(
            [configured],
            manifest_servers={"firecrawl": server},
            enabled_auto_start={"firecrawl"},
            is_auth_available=lambda _key: False,
        )
        assert result.eager_configs == []
        assert len(result.skipped) == 1
        assert result.skipped[0].reason == StartupSkipReason.MISSING_AUTH


# ---------------------------------------------------------------------------
# Gate 2 — install/provision preflight (installer.check_api_key)
# ---------------------------------------------------------------------------


class TestGate2CheckApiKey:
    @pytest.mark.asyncio
    async def test_relaxed_server_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        server = _relaxable_server(
            api_key_optional_when=["FIRECRAWL_API_URL"],
            extra_env={"FIRECRAWL_API_URL": "http://localhost:3002"},
        )
        await check_api_key(server)  # must not raise

    @pytest.mark.asyncio
    async def test_required_server_without_credential_still_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        server = _relaxable_server(api_key_optional_when=[])
        with pytest.raises(MissingApiKeyError):
            await check_api_key(server)

    @pytest.mark.asyncio
    async def test_required_server_with_credential_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FIRECRAWL_API_KEY", "sk-test")
        server = _relaxable_server(api_key_optional_when=[])
        await check_api_key(server)  # must not raise


# ---------------------------------------------------------------------------
# Gate 5 — diagnostics (`pmcp secrets check` / run_secrets_check)
# ---------------------------------------------------------------------------


_OVERLAY_RELAXED = """
servers:
  cred-test:
    description: "test relaxable server"
    keywords: [cred-test]
    command: "cred-test-mcp"
    args: []
    requires_api_key: true
    env_var: CRED_TEST_API_KEY
    api_key_optional_when: ["CRED_TEST_URL"]
    extra_env:
      CRED_TEST_URL: "http://localhost:9999"
"""

_OVERLAY_REQUIRED = """
servers:
  cred-test:
    description: "test relaxable server"
    keywords: [cred-test]
    command: "cred-test-mcp"
    args: []
    requires_api_key: true
    env_var: CRED_TEST_API_KEY
    api_key_optional_when: ["CRED_TEST_URL"]
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class TestGate5SecretsCheck:
    @pytest.mark.asyncio
    async def test_relaxed_server_reports_ok_with_no_missing_keys(
        self, tmp_path: Path
    ) -> None:
        from pmcp.cli_commands.secrets import run_secrets_check

        home = tmp_path / "home"
        project = tmp_path / "project"
        overlay = tmp_path / "overlay.yaml"
        _write(home / ".config" / "pmcp" / "pmcp.env", "")
        _write(project / ".env.pmcp", "")
        _write(overlay, _OVERLAY_RELAXED)
        _write(
            project / ".mcp.json",
            json.dumps(
                {"mcpServers": {"cred-test": {"command": "cred-test-mcp", "args": []}}}
            ),
        )

        with patch.dict(
            "os.environ",
            {"HOME": str(home), "PMCP_MANIFEST_PATH": str(overlay)},
            clear=False,
        ):
            os.environ.pop("CRED_TEST_API_KEY", None)
            output = await run_secrets_check(argparse.Namespace(project=project))

        assert "CRED_TEST_API_KEY" not in output["required_keys"]
        assert "CRED_TEST_URL" not in output["required_keys"]
        assert output["missing_keys"] == []
        assert output["ok"] is True

    @pytest.mark.asyncio
    async def test_required_server_still_reports_missing(self, tmp_path: Path) -> None:
        from pmcp.cli_commands.secrets import run_secrets_check

        home = tmp_path / "home"
        project = tmp_path / "project"
        overlay = tmp_path / "overlay.yaml"
        _write(home / ".config" / "pmcp" / "pmcp.env", "")
        _write(project / ".env.pmcp", "")
        _write(overlay, _OVERLAY_REQUIRED)
        _write(
            project / ".mcp.json",
            json.dumps(
                {"mcpServers": {"cred-test": {"command": "cred-test-mcp", "args": []}}}
            ),
        )

        with patch.dict(
            "os.environ",
            {"HOME": str(home), "PMCP_MANIFEST_PATH": str(overlay)},
            clear=False,
        ):
            os.environ.pop("CRED_TEST_API_KEY", None)
            output = await run_secrets_check(argparse.Namespace(project=project))

        assert "CRED_TEST_API_KEY" in output["required_keys"]
        assert "CRED_TEST_API_KEY" in output["missing_keys"]
        assert output["ok"] is False

    # `cli.py:2578` only prints `output["ok"]`, never maps it to process exit
    # status, so a caller must assert on the returned mapping — never on a
    # subprocess exit code — to detect gate 5 regressing.
    @pytest.mark.asyncio
    async def test_ok_is_returned_field_not_exit_status(self, tmp_path: Path) -> None:
        from pmcp.cli_commands.secrets import run_secrets_check

        home = tmp_path / "home"
        project = tmp_path / "project"
        _write(home / ".config" / "pmcp" / "pmcp.env", "")
        _write(project / ".env.pmcp", "")
        _write(project / ".mcp.json", json.dumps({"mcpServers": {}}))

        with patch.dict("os.environ", {"HOME": str(home)}, clear=False):
            output = await run_secrets_check(argparse.Namespace(project=project))

        assert isinstance(output, dict)
        assert "ok" in output


# ---------------------------------------------------------------------------
# Gate 7 — `pmcp init` (run_init)
# ---------------------------------------------------------------------------


class TestGate7RunInit:
    @pytest.mark.asyncio
    async def test_relaxed_server_prints_no_credential_needed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from pmcp.cli import run_init

        home = tmp_path / "home"
        overlay = tmp_path / "overlay.yaml"
        home.mkdir()
        _write(
            overlay,
            """
servers:
  firecrawl:
    description: "Web scraping/crawling"
    keywords: [firecrawl, scrape]
    command: "firecrawl-mcp"
    args: []
    requires_api_key: true
    env_var: FIRECRAWL_API_KEY
    api_key_optional_when: ["FIRECRAWL_API_URL"]
    extra_env:
      FIRECRAWL_API_URL: "http://localhost:3002"
""",
        )
        project = tmp_path / "project"
        args = argparse.Namespace(command="init", project=project, force=False)

        def _select_firecrawl(prompt: str) -> str:
            return "y" if "firecrawl" in prompt else ""

        with patch.dict(
            "os.environ",
            {"HOME": str(home), "PMCP_MANIFEST_PATH": str(overlay)},
            clear=False,
        ):
            os.environ.pop("FIRECRAWL_API_KEY", None)
            with patch("builtins.input", side_effect=_select_firecrawl):
                await run_init(args)

        content = (project / ".mcp.json").read_text()
        assert '"firecrawl"' in content

        captured = capsys.readouterr()
        assert "No credential needed" in captured.out
        assert "FIRECRAWL_API_URL" in captured.out
        assert "pmcp secrets set" not in captured.out

    @pytest.mark.asyncio
    async def test_required_server_instruction_unchanged(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A server that still requires a credential keeps the exact
        `pmcp secrets set` instruction — byte-for-byte unchanged."""
        from pmcp.cli import run_init

        home = tmp_path / "home"
        home.mkdir()
        project = tmp_path / "project"
        args = argparse.Namespace(command="init", project=project, force=False)

        def _select_firecrawl(prompt: str) -> str:
            return "y" if "firecrawl" in prompt else ""

        with patch.dict("os.environ", {"HOME": str(home)}, clear=False):
            os.environ.pop("FIRECRAWL_API_KEY", None)
            os.environ.pop("PMCP_MANIFEST_PATH", None)
            with patch("builtins.input", side_effect=_select_firecrawl):
                await run_init(args)

        content = (project / ".mcp.json").read_text()
        assert '"firecrawl"' in content

        captured = capsys.readouterr()
        assert "pmcp secrets set FIRECRAWL_API_KEY" in captured.out
        assert "No credential needed" not in captured.out
