"""Manifest provisioning test suite.

Fast unit tier (~300 parametrized cases, <10s):
- Schema completeness for all 99 manifest servers
- Install command consistency (linux[0] == server.command)
- Mocked provision routing for all servers

Live smoke tier (opt-in, ``pytest -m live``):
- Actually runs start_install() for no-API-key servers
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pmcp.manifest.installer import JobManager
from pmcp.manifest.loader import load_manifest
from pmcp.policy.policy import PolicyManager
from pmcp.tools.handlers import GatewayTools

# ---------------------------------------------------------------------------
# Module-level manifest load — shared across all parametrized cases
# ---------------------------------------------------------------------------

_manifest = load_manifest()
_all_servers = list(_manifest.servers.values())
_api_key_servers = [s for s in _all_servers if s.requires_api_key]
_no_key_servers = [s for s in _all_servers if not s.requires_api_key]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MinimalClientManager:
    """Minimal client manager — no online servers, no tools."""

    def get_all_tools(self):
        return []

    def get_tool(self, tool_id):
        return None

    def is_server_online(self, name):
        return False

    def is_lazy_server(self, name):
        return False

    def get_all_server_statuses(self):
        return []

    def get_registry_meta(self):
        return ("test-rev", 0.0)

    async def call_tool(self, tool_id, args, timeout_ms):
        return {"content": [{"type": "text", "text": ""}]}

    async def refresh(self, configs):
        return []


def _make_gateway_tools() -> GatewayTools:
    return GatewayTools(
        client_manager=_MinimalClientManager(),  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
    )


# ---------------------------------------------------------------------------
# Class 1 — Schema completeness
# ---------------------------------------------------------------------------


class TestManifestSchemaCompleteness:
    """Every server entry must be well-formed."""

    @pytest.mark.parametrize("server", _all_servers, ids=lambda s: s.name)
    def test_description_non_empty(self, server):
        assert isinstance(server.description, str) and server.description.strip()

    @pytest.mark.parametrize("server", _all_servers, ids=lambda s: s.name)
    def test_keywords_non_empty(self, server):
        assert isinstance(server.keywords, list) and len(server.keywords) > 0

    @pytest.mark.parametrize("server", _all_servers, ids=lambda s: s.name)
    def test_install_dict_non_empty(self, server):
        assert isinstance(server.install, dict) and len(server.install) > 0

    @pytest.mark.parametrize("server", _all_servers, ids=lambda s: s.name)
    def test_linux_platform_present(self, server):
        assert "linux" in server.install, (
            f"Server '{server.name}' missing 'linux' key in install"
        )

    @pytest.mark.parametrize("server", _all_servers, ids=lambda s: s.name)
    def test_command_non_empty(self, server):
        assert isinstance(server.command, str) and server.command.strip()

    @pytest.mark.parametrize("server", _all_servers, ids=lambda s: s.name)
    def test_args_is_list(self, server):
        assert isinstance(server.args, list)

    @pytest.mark.parametrize("server", _api_key_servers, ids=lambda s: s.name)
    def test_api_key_servers_have_env_var(self, server):
        assert server.env_var, (
            f"Server '{server.name}' requires_api_key=True but env_var is empty"
        )

    @pytest.mark.parametrize("server", _api_key_servers, ids=lambda s: s.name)
    def test_api_key_servers_have_env_instructions(self, server):
        assert server.env_instructions, (
            f"Server '{server.name}' requires_api_key=True but env_instructions is empty"
        )


# ---------------------------------------------------------------------------
# Class 2 — Install command consistency
# ---------------------------------------------------------------------------


class TestManifestInstallCommandConsistency:
    """server.install['linux'][0] must equal server.command."""

    @pytest.mark.parametrize("server", _all_servers, ids=lambda s: s.name)
    def test_linux_install_first_token_matches_command(self, server):
        linux_cmd = server.install["linux"]
        assert linux_cmd, f"Server '{server.name}': linux install list is empty"
        assert linux_cmd[0] == server.command, (
            f"Server '{server.name}': install['linux'][0]={linux_cmd[0]!r} "
            f"!= command={server.command!r}"
        )


# ---------------------------------------------------------------------------
# Class 3 — Provision routing (mocked)
# ---------------------------------------------------------------------------


class TestProvisionRoutingAllServers:
    """Provision routing returns correct shape for every server — no I/O."""

    @pytest.mark.parametrize("server", _no_key_servers, ids=lambda s: s.name)
    async def test_provision_no_key_server(self, server, monkeypatch):
        """No-API-key servers should start installation and return status='started'."""
        tools = _make_gateway_tools()

        fake_jm = MagicMock()
        fake_jm.start_install = AsyncMock(return_value="fake-job-id")

        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: _manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr("pmcp.tools.handlers.get_job_manager", lambda: fake_jm)

        result = await tools.provision({"server_name": server.name})

        assert result.ok is True, (
            f"[{server.name}] provision failed: {result.message}"
        )
        assert result.status == "started", (
            f"[{server.name}] expected status='started', got {result.status!r}"
        )
        assert result.job_id == "fake-job-id"
        assert result.server == server.name

    @pytest.mark.parametrize("server", _api_key_servers, ids=lambda s: s.name)
    async def test_provision_missing_api_key(self, server, monkeypatch):
        """API-key servers with no key set should return auth_required=True."""
        tools = _make_gateway_tools()

        monkeypatch.setattr("pmcp.tools.handlers.load_manifest", lambda: _manifest)
        monkeypatch.setattr("pmcp.tools.handlers.load_configs", lambda **_: [])
        monkeypatch.setattr(
            "pmcp.tools.handlers.load_dotenv", lambda *a, **kw: False
        )
        # Force all env-var checks to miss
        monkeypatch.setattr(
            tools,
            "_check_any_api_key_available",
            lambda env_vars: False,
        )

        result = await tools.provision({"server_name": server.name})

        assert result.ok is False, (
            f"[{server.name}] expected ok=False when API key missing"
        )
        assert result.auth_required is True, (
            f"[{server.name}] expected auth_required=True"
        )
        assert result.needs_api_key is True, (
            f"[{server.name}] expected needs_api_key=True"
        )
        assert result.env_var == server.env_var, (
            f"[{server.name}] env_var mismatch: {result.env_var!r} != {server.env_var!r}"
        )
        assert result.status == "failed", (
            f"[{server.name}] expected status='failed', got {result.status!r}"
        )


# ---------------------------------------------------------------------------
# Class 4 — Live smoke tests (opt-in: pytest -m live)
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestLiveProvisionSmokeTest:
    """Actually install no-API-key servers end-to-end.

    Run with::

        pytest tests/test_manifest_provision.py -m live -v
    """

    @pytest.fixture(autouse=True)
    def reset_job_manager(self):
        """Ensure a clean JobManager singleton for each test."""
        JobManager._instance = None
        yield
        jm = JobManager._instance
        if jm is not None:
            for job in jm.get_all_jobs():
                if job.process is not None:
                    try:
                        job.process.terminate()
                    except Exception:
                        pass
        JobManager._instance = None

    @pytest.mark.parametrize("server", _no_key_servers, ids=lambda s: s.name)
    @pytest.mark.timeout(130)
    async def test_live_install(self, server):
        """Start install and wait up to 120 s for server_ready or complete."""
        from pmcp.manifest.environment import detect_platform
        from pmcp.manifest.installer import get_job_manager

        platform = detect_platform()
        jm = get_job_manager()
        job_id = await jm.start_install(server, platform)

        deadline = asyncio.get_event_loop().time() + 120
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2)
            job = jm.get_job(job_id)
            assert job is not None
            if job.status in ("server_ready", "complete"):
                break
            if job.status in ("failed", "timeout"):
                pytest.fail(
                    f"[{server.name}] install ended with status={job.status!r}: "
                    f"{job.error or '(no error message)'}"
                )
        else:
            job = jm.get_job(job_id)
            pytest.fail(
                f"[{server.name}] install timed out after 120s "
                f"(last status={job.status if job else 'unknown'})"
            )

        # Cleanup — terminate process
        if job and job.process is not None:
            try:
                job.process.terminate()
            except Exception:
                pass
