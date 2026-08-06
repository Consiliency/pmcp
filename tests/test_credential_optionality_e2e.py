"""P5 SL-4.2/4.3: seven-consumer end-to-end proof (EC-P5-2, EC-P5-3).

Each test in the "relaxed" class exercises all seven gates against ONE
fixture whose `firecrawl` entry carries `api_key_optional_when:
["FIRECRAWL_API_URL"]` and whose overlay supplies that URL, with
FIRECRAWL_API_KEY absent everywhere — asserting POSITIVE outcomes, not
merely the absence of error markers.

Each test in the "fail closed" class runs a server that looks relaxable but
isn't (four distinct reasons) through the same seven gates and asserts every
one still demands the credential.

"shipped" tests assert the manifest actually shipped in SL-4.4 carries the
field and that shipping it alone relaxes nothing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from pmcp.cli import run_init
from pmcp.cli_commands.secrets import run_secrets_check
from pmcp.config.loader import StartupSkipReason, resolve_startup_configs
from pmcp.manifest.installer import MissingApiKeyError, check_api_key
from pmcp.manifest.loader import load_manifest, requires_credential
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools
from pmcp.types import ServerStatus


class _MockClientManager:
    """Minimal client manager stub covering connect_server/provision needs."""

    def __init__(self) -> None:
        self._online: set[str] = set()
        self.connected_configs: list[Any] = []

    def is_server_online(self, name: str) -> bool:
        return name in self._online

    def is_lazy_server(self, name: str) -> bool:
        return False

    def get_server_status(self, name: str) -> ServerStatus | None:
        return None

    def get_all_server_statuses(self) -> list[ServerStatus]:
        return []

    def get_registry_meta(self) -> tuple[str, float]:
        return ("test-rev", 0.0)

    async def connect_server(self, config: Any) -> list[str]:
        self.connected_configs.append(config)
        return []


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Isolate overlay/env-file discovery: fresh HOME, no project manifest, no
    .mcp.json, no PMCP_MANIFEST_PATH by default. Returns (workdir, home)."""
    home = tmp_path / "home"
    home.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.chdir(workdir)
    return workdir, home


RELAXED_OVERLAY = """
servers:
  firecrawl:
    description: "Web scraping/crawling"
    keywords: [firecrawl, scraping, crawl, extract]
    install:
      mac: ["true"]
      linux: ["true"]
      wsl: ["true"]
      windows: ["true"]
    command: "firecrawl-mcp"
    args: []
    requires_api_key: true
    env_var: FIRECRAWL_API_KEY
    api_key_optional_when: ["FIRECRAWL_API_URL"]
    extra_env:
      FIRECRAWL_API_URL: "http://localhost:3002"
"""


async def _run_all_seven_gates(
    workdir: Path,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> dict[str, Any]:
    """Run every one of the seven consumers against the current overlay and
    return their outcomes for the caller to assert on."""
    manifest = load_manifest()
    server = manifest.get_server("firecrawl")
    assert server is not None

    outcomes: dict[str, Any] = {}

    # Gate 1 — eager startup.
    startup = resolve_startup_configs(
        [],
        manifest_servers={"firecrawl": server},
        enabled_auto_start={"firecrawl"},
        is_auth_available=lambda _key: False,
    )
    outcomes["startup"] = startup

    # Gate 2 — install/provision preflight.
    try:
        await check_api_key(server)
        outcomes["check_api_key_raised"] = False
    except MissingApiKeyError:
        outcomes["check_api_key_raised"] = True

    # Gates 3, 4, 6 — lifecycle connect, provisioning, capability discovery.
    tools = GatewayTools(
        client_manager=_MockClientManager(),  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
    )
    outcomes["connect"] = await tools.connect_server({"server_name": "firecrawl"})
    outcomes["provision"] = await tools.provision({"server_name": "firecrawl"})
    outcomes["request_capability"] = await tools.request_capability(
        {"query": "firecrawl"}
    )

    # Gate 5 — diagnostics. run_secrets_check only reports on servers that
    # appear in a configured .mcp.json (matches the existing behaviour
    # test_run_secrets_check_reports_missing_namespaced_credential pins), so
    # a bare configured entry for firecrawl is required for this gate to
    # evaluate it at all.
    _write(
        workdir / ".mcp.json",
        json.dumps({"mcpServers": {"firecrawl": {"command": "firecrawl-mcp"}}}),
    )
    outcomes["secrets_check"] = await run_secrets_check(
        argparse.Namespace(project=workdir)
    )

    # Gate 7 — pmcp init.
    init_project = workdir / "init"
    init_project.mkdir()

    def _select_firecrawl(prompt: str) -> str:
        return "y" if "firecrawl" in prompt else ""

    with patch("builtins.input", side_effect=_select_firecrawl):
        await run_init(
            argparse.Namespace(command="init", project=init_project, force=False)
        )
    outcomes["init_mcp_json"] = json.loads((init_project / ".mcp.json").read_text())
    outcomes["init_stdout"] = capsys.readouterr().out

    return outcomes


class TestRelaxedDirectionAllSevenGates:
    """EC-P5-3: FIRECRAWL_API_KEY absent everywhere; positive outcomes at
    every gate."""

    @pytest.mark.asyncio
    async def test_all_seven_gates_pass_with_no_credential(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workdir, home = _isolate(tmp_path, monkeypatch)
        overlay = tmp_path / "overlay.yaml"
        _write(overlay, RELAXED_OVERLAY)
        monkeypatch.setenv("PMCP_MANIFEST_PATH", str(overlay))
        _write(workdir / ".env.pmcp", "")
        _write(home / ".config" / "pmcp" / "pmcp.env", "")

        outcomes = await _run_all_seven_gates(workdir, home, monkeypatch, capsys)

        # Gate 1 — present in eager_configs, absent from skipped.
        assert [c.name for c in outcomes["startup"].eager_configs] == ["firecrawl"]
        assert outcomes["startup"].skipped == []

        # Gate 2 — did not raise.
        assert outcomes["check_api_key_raised"] is False

        # Gate 3 — connect does not report missing_auth.
        assert outcomes["connect"].auth_state != "missing_auth"

        # Gate 4 — provision actually succeeds (ok is True), not merely
        # "didn't report needing a key". The fixture carries a real install
        # command (`true`) so this exercises the full success path rather
        # than accidentally passing via an unrelated InstallError.
        assert outcomes["provision"].needs_api_key is not True
        assert outcomes["provision"].auth_state != "missing_auth"
        assert outcomes["provision"].ok is True, outcomes["provision"].message
        assert outcomes["provision"].status == "started"

        # Gate 5 — ok is True; neither the credential nor the relaxer is
        # reported missing (read the returned field, never an exit code).
        secrets_output = outcomes["secrets_check"]
        assert secrets_output["ok"] is True
        assert "FIRECRAWL_API_KEY" not in secrets_output["missing_keys"]
        assert "FIRECRAWL_API_URL" not in secrets_output["missing_keys"]

        # Gate 6 — catalog_search/request_capability candidate reports no key
        # required and no auth_connect recommendation.
        capability = outcomes["request_capability"]
        assert capability.candidates[0].requires_api_key is False
        assert "auth_connect" not in (capability.recommendation or "")

        # Gate 7 — pmcp init did not need to prompt for a credential; the
        # server made it into the generated config, and the printed guidance
        # names the relaxer instead of instructing `pmcp secrets set`.
        assert "firecrawl" in outcomes["init_mcp_json"]["mcpServers"]
        assert "No credential needed" in outcomes["init_stdout"]
        assert "pmcp secrets set" not in outcomes["init_stdout"]


SHARED_SERVER_ENV_DECLARE = """
servers:
  firecrawl:
    description: "Web scraping/crawling"
    keywords: [firecrawl, scraping, crawl, extract]
    command: "firecrawl-mcp"
    args: []
    requires_api_key: true
    env_var: FIRECRAWL_API_KEY
    api_key_optional_when: ["FIRECRAWL_API_URL"]
"""


class TestTwoPartyServerEnvPatchAlsoRelaxes:
    """The relaxer may arrive via an overlay's `server_env:` patch (the
    operator's action) layered onto a manifest entry that merely declares the
    field (the author's action) — two independent parties, per the design
    rationale in plans/phase-plan-v11-P5.md."""

    @pytest.mark.asyncio
    async def test_server_env_patch_relaxes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workdir, home = _isolate(tmp_path, monkeypatch)
        # Author's declaration lives in the user overlay (lower precedence).
        _write(home / ".pmcp" / "manifest.yaml", SHARED_SERVER_ENV_DECLARE)
        # Operator's URL arrives via a project overlay's server_env patch
        # (higher precedence than user, applied after).
        _write(
            workdir / ".pmcp" / "manifest.yaml",
            """
server_env:
  firecrawl:
    FIRECRAWL_API_URL: "http://localhost:3002"
""",
        )

        manifest = load_manifest()
        server = manifest.get_server("firecrawl")
        assert server is not None
        assert server.extra_env.get("FIRECRAWL_API_URL") == "http://localhost:3002"
        assert requires_credential(server) is False


FAIL_CLOSED_CASES: list[tuple[str, str]] = [
    (
        "no_api_key_optional_when",
        """
servers:
  firecrawl:
    description: "Web scraping/crawling"
    keywords: [firecrawl, scraping, crawl, extract]
    command: "firecrawl-mcp"
    args: []
    requires_api_key: true
    env_var: FIRECRAWL_API_KEY
""",
    ),
    (
        "relaxer_declared_but_url_unset",
        """
servers:
  firecrawl:
    description: "Web scraping/crawling"
    keywords: [firecrawl, scraping, crawl, extract]
    command: "firecrawl-mcp"
    args: []
    requires_api_key: true
    env_var: FIRECRAWL_API_KEY
    api_key_optional_when: ["FIRECRAWL_API_URL"]
""",
    ),
    (
        "relaxer_names_its_own_credential",
        """
servers:
  firecrawl:
    description: "Web scraping/crawling"
    keywords: [firecrawl, scraping, crawl, extract]
    command: "firecrawl-mcp"
    args: []
    requires_api_key: true
    env_var: FIRECRAWL_API_KEY
    api_key_optional_when: ["FIRECRAWL_API_KEY"]
""",
    ),
]


class TestFailClosedDirectionAllSevenGates:
    """EC-P5-2: four servers that look relaxable but aren't, each still
    fail closed at all seven gates."""

    @pytest.mark.parametrize("case_name,overlay_yaml", FAIL_CLOSED_CASES)
    @pytest.mark.asyncio
    async def test_fail_closed_at_all_seven_gates(
        self,
        case_name: str,
        overlay_yaml: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workdir, home = _isolate(tmp_path, monkeypatch)
        overlay = tmp_path / "overlay.yaml"
        _write(overlay, overlay_yaml)
        monkeypatch.setenv("PMCP_MANIFEST_PATH", str(overlay))
        _write(workdir / ".env.pmcp", "")
        _write(home / ".config" / "pmcp" / "pmcp.env", "")

        outcomes = await _run_all_seven_gates(workdir, home, monkeypatch, capsys)

        assert outcomes["startup"].eager_configs == [], case_name
        assert len(outcomes["startup"].skipped) == 1, case_name
        assert (
            outcomes["startup"].skipped[0].reason == StartupSkipReason.MISSING_AUTH
        ), case_name

        assert outcomes["check_api_key_raised"] is True, case_name

        assert outcomes["connect"].auth_state == "missing_auth", case_name
        assert outcomes["provision"].needs_api_key is True, case_name

        secrets_output = outcomes["secrets_check"]
        assert secrets_output["ok"] is False, case_name
        assert "FIRECRAWL_API_KEY" in secrets_output["missing_keys"], case_name

        capability = outcomes["request_capability"]
        assert capability.candidates[0].requires_api_key is True, case_name

        # Gate 7 — the credential is still genuinely required, so the
        # existing `pmcp secrets set` instruction is unchanged.
        assert "pmcp secrets set FIRECRAWL_API_KEY" in outcomes["init_stdout"], (
            case_name
        )
        assert "No credential needed" not in outcomes["init_stdout"], case_name

    @pytest.mark.asyncio
    async def test_relaxer_present_only_in_os_environ_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The fourth fail-closed reason: the relaxer is set only in
        os.environ (e.g. a bare shell export), never in the manifest's
        extra_env. This is the env-strip-inversion guard's e2e proof."""
        workdir, home = _isolate(tmp_path, monkeypatch)
        overlay = tmp_path / "overlay.yaml"
        _write(
            overlay,
            """
servers:
  firecrawl:
    description: "Web scraping/crawling"
    keywords: [firecrawl, scraping, crawl, extract]
    command: "firecrawl-mcp"
    args: []
    requires_api_key: true
    env_var: FIRECRAWL_API_KEY
    api_key_optional_when: ["FIRECRAWL_API_URL"]
""",
        )
        monkeypatch.setenv("PMCP_MANIFEST_PATH", str(overlay))
        monkeypatch.setenv("FIRECRAWL_API_URL", "http://localhost:3002")
        _write(workdir / ".env.pmcp", "")
        _write(home / ".config" / "pmcp" / "pmcp.env", "")

        outcomes = await _run_all_seven_gates(workdir, home, monkeypatch, capsys)

        assert outcomes["startup"].eager_configs == []
        assert outcomes["check_api_key_raised"] is True
        assert outcomes["connect"].auth_state == "missing_auth"
        assert outcomes["provision"].needs_api_key is True
        assert outcomes["secrets_check"]["ok"] is False
        assert outcomes["request_capability"].candidates[0].requires_api_key is True


class TestShippedManifestCarriesTheField:
    """SL-4.3: proves the shipped line exists, and that shipping it alone
    relaxes nothing for an existing install."""

    def test_shipped_firecrawl_declares_relaxer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate(tmp_path, monkeypatch)  # no overlay of any kind

        manifest = load_manifest()
        server = manifest.get_server("firecrawl")

        assert server is not None
        assert server.api_key_optional_when == ["FIRECRAWL_API_URL"]

    def test_shipped_firecrawl_still_requires_credential_with_empty_extra_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _isolate(tmp_path, monkeypatch)

        manifest = load_manifest()
        server = manifest.get_server("firecrawl")

        assert server is not None
        assert server.extra_env == {}
        assert requires_credential(server) is True
