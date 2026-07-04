"""P3 remediation — agent-reachable code-execution validation.

Covers four confirmed findings:
1. Package-name validation before ``npx -y <name>`` (argument injection).
2. auth_connect env-var allowlist (LD_PRELOAD / NODE_OPTIONS / PATH → code exec).
3. submit_feedback scrubbing of title / failed_tool_call / subordinate_server.
4. provision_status server_ready→adopt handoff runs exactly once (TOCTOU).
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from pmcp.manifest.loader import ServerConfig
from pmcp.policy.policy import PolicyManager
from pmcp.tools import handlers as handlers_module
from pmcp.tools.handlers import GatewayTools
from pmcp.types import RegisterDiscoveredServerInput, SubmitFeedbackInput
from pmcp.validation import (
    env_var_allowed,
    is_dangerous_env_var,
    is_valid_package_name,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MinimalClientManager:
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

    async def connect_all(self, configs, retry=True):
        return []


def _make_gateway() -> GatewayTools:
    return GatewayTools(
        client_manager=_MinimalClientManager(),  # type: ignore[arg-type]
        policy_manager=PolicyManager(),
    )


# ---------------------------------------------------------------------------
# Finding 1 — package-name validation
# ---------------------------------------------------------------------------


class TestPackageNameValidation:
    @pytest.mark.parametrize("name", ["-g", "../evil", "a b", "x;rm", "", "  ", "/etc"])
    def test_rejects_unsafe(self, name):
        assert is_valid_package_name(name) is False

    @pytest.mark.parametrize(
        "name",
        [
            "name",
            "@scope/name",
            "name-with-dashes",
            "@modelcontextprotocol/server-github",
            "left_pad",
            "pkg.js",
        ],
    )
    def test_accepts_valid(self, name):
        assert is_valid_package_name(name) is True

    @pytest.mark.parametrize("name", ["-g", "../evil", "a b", "x;rm", ""])
    def test_pydantic_field_rejects(self, name):
        with pytest.raises(ValidationError):
            RegisterDiscoveredServerInput.model_validate(
                {"package": name, "server_name": "s"}
            )

    @pytest.mark.asyncio
    async def test_register_echoes_install_command(self):
        gateway = _make_gateway()
        out = await gateway.register_discovered_server(
            {"package": "@scope/pkg", "server_name": "demo"}
        )
        assert out.ok is True
        # The resolved list-argv command is surfaced for caller confirmation.
        assert out.install_command == ["npx", "-y", "@scope/pkg"]
        assert "npx -y @scope/pkg" in out.message


# ---------------------------------------------------------------------------
# Finding 2 — auth_connect env-var allowlist
# ---------------------------------------------------------------------------


class TestEnvVarAllowlist:
    @pytest.mark.parametrize(
        "name",
        [
            "LD_PRELOAD",
            "LD_LIBRARY_PATH",
            "NODE_OPTIONS",
            "PATH",
            "PYTHONPATH",
            "PYTHONBREAKPOINT",
            "DYLD_INSERT_LIBRARIES",
        ],
    )
    def test_dangerous_vars_flagged(self, name):
        assert is_dangerous_env_var(name) is True
        # Never allowed, even if a server were to declare it.
        assert env_var_allowed(name, name) is False

    def test_declared_var_allowed(self):
        assert env_var_allowed("GITHUB_TOKEN", "GITHUB_TOKEN") is True

    def test_non_declared_var_rejected(self):
        assert env_var_allowed("OTHER_TOKEN", "GITHUB_TOKEN") is False

    def test_credential_shaped_fallback(self):
        assert env_var_allowed("BRAVE_API_KEY", None) is True
        assert env_var_allowed("RANDOMNAME", None) is False

    @pytest.mark.asyncio
    async def test_auth_connect_rejects_ld_preload(self):
        gateway = _make_gateway()
        write_secret = MagicMock()
        gateway._write_secret = write_secret  # type: ignore[method-assign]

        assert "LD_PRELOAD" not in os.environ
        out = await gateway.auth_connect(
            {
                "server_name": "whatever",
                "credential": "malicious",
                "env_var": "LD_PRELOAD",
            }
        )

        assert out.ok is False
        assert "not permitted" in out.message
        # Nothing was persisted, and process env was not poisoned.
        write_secret.assert_not_called()
        assert "LD_PRELOAD" not in os.environ

    @pytest.mark.asyncio
    async def test_auth_connect_accepts_declared_env_var(self, monkeypatch):
        gateway = _make_gateway()
        # Discovered servers never enter the manifest; the declared credential
        # variable must still be resolved from the discovered-server registry.
        declared = "FAKE_SERVER_TOKEN_XYZ"
        gateway._discovered_server_configs["disc"] = ServerConfig(
            name="disc",
            description="d",
            keywords=["disc"],
            install={"linux": ["npx", "-y", "disc"]},
            command="npx",
            args=["-y", "disc"],
            requires_api_key=True,
            env_var=declared,
        )
        write_secret = MagicMock(return_value="/tmp/fake.env")
        gateway._write_secret = write_secret  # type: ignore[method-assign]
        monkeypatch.delenv(declared, raising=False)

        try:
            out = await gateway.auth_connect(
                {"server_name": "disc", "credential": "s3cret"}
            )
            assert out.ok is True
            assert out.env_var == declared
            write_secret.assert_called_once()
            assert os.environ.get(declared) == "s3cret"
        finally:
            os.environ.pop(declared, None)


# ---------------------------------------------------------------------------
# Finding 3 — feedback scrubbing across all caller-supplied fields
# ---------------------------------------------------------------------------


class TestFeedbackScrub:
    def test_secret_redacted_in_all_fields(self):
        gateway = _make_gateway()
        secret = "sk-abcdef123456"
        parsed = SubmitFeedbackInput(
            title=f"Crash while using {secret} token",
            description="benign description",
            subordinate_server=f"srv-{secret}",
            failed_tool_call=f"call with {secret}",
        )

        title, body = gateway._build_feedback_issue(parsed, "owner/repo")

        # The raw secret must not survive into the public issue payload.
        assert secret not in title
        assert secret not in body
        # And the telemetry lines that echo those fields are present + scrubbed.
        assert "subordinate_server:" in body
        assert "failed_tool_call:" in body


# ---------------------------------------------------------------------------
# Finding 4 — provision_status handoff runs exactly once under concurrency
# ---------------------------------------------------------------------------


class TestProvisionStatusHandoff:
    @pytest.mark.asyncio
    async def test_concurrent_polls_adopt_once(self, monkeypatch):
        gateway = _make_gateway()

        process = SimpleNamespace(returncode=None)
        job = SimpleNamespace(
            id="job-1",
            server_name="srv",
            status="server_ready",
            progress=90,
            output_lines=["line"],
            started_at=0.0,
            process=process,
            error=None,
        )

        job_manager = SimpleNamespace(get_job=lambda _id: job)
        monkeypatch.setattr(handlers_module, "get_job_manager", lambda: job_manager)
        monkeypatch.setattr(
            handlers_module,
            "load_manifest",
            lambda: SimpleNamespace(
                get_server=lambda _n: SimpleNamespace(env_var=None)
            ),
        )
        monkeypatch.setattr(
            handlers_module, "manifest_server_to_config", lambda cfg: cfg
        )
        gateway._register_provisioned_server = MagicMock()  # type: ignore[method-assign]

        adopt_count = 0

        async def fake_adopt(name, proc, cfg):
            nonlocal adopt_count
            adopt_count += 1
            # Yield so the racing poll reaches the lock while we hold it.
            await asyncio.sleep(0.01)

        gateway._client_manager.adopt_process = AsyncMock(  # type: ignore[attr-defined]
            side_effect=fake_adopt
        )

        results = await asyncio.gather(
            gateway.provision_status({"job_id": "job-1"}),
            gateway.provision_status({"job_id": "job-1"}),
        )

        # Adopted exactly once despite two concurrent server_ready polls.
        assert adopt_count == 1
        assert all(r.status == "complete" for r in results)
        assert job.status == "complete"

        # A later poll of the finalized job must not adopt again.
        again = await gateway.provision_status({"job_id": "job-1"})
        assert again.status == "complete"
        assert adopt_count == 1
