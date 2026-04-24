"""Tests for policy manager."""

from __future__ import annotations

import json
from pathlib import Path


from pmcp.policy.policy import PolicyManager


class TestServerAllowDeny:
    """Tests for server allow/deny lists."""

    def test_allows_all_by_default(self) -> None:
        policy = PolicyManager()
        assert policy.is_server_allowed("any-server") is True
        assert policy.is_server_allowed("another-server") is True

    def test_denies_servers_on_denylist(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "servers": {
                        "denylist": ["blocked-*", "dangerous"],
                    }
                }
            )
        )

        policy = PolicyManager(policy_path)
        assert policy.is_server_allowed("blocked-server") is False
        assert policy.is_server_allowed("blocked-anything") is False
        assert policy.is_server_allowed("dangerous") is False
        assert policy.is_server_allowed("allowed-server") is True

    def test_only_allows_servers_on_allowlist(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "servers": {
                        "allowlist": ["github", "jira"],
                    }
                }
            )
        )

        policy = PolicyManager(policy_path)
        assert policy.is_server_allowed("github") is True
        assert policy.is_server_allowed("jira") is True
        assert policy.is_server_allowed("slack") is False

    def test_tenant_code_mode_server_policy_isolated(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "servers": {
                        "allowlist": ["tenant-code-mode"],
                    }
                }
            )
        )

        policy = PolicyManager(policy_path)
        assert policy.is_server_allowed("tenant-code-mode") is True
        assert policy.is_server_allowed("github") is False

        policy_path.write_text(
            json.dumps(
                {
                    "servers": {
                        "denylist": ["tenant-code-mode"],
                    }
                }
            )
        )
        denied_policy = PolicyManager(policy_path)
        assert denied_policy.is_server_allowed("tenant-code-mode") is False
        assert denied_policy.is_server_allowed("github") is True


class TestToolAllowDeny:
    """Tests for tool allow/deny lists."""

    def test_allows_all_by_default(self) -> None:
        policy = PolicyManager()
        assert policy.is_tool_allowed("github::create_issue") is True

    def test_supports_glob_patterns(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "tools": {
                        "denylist": ["*::delete_*", "dangerous::*"],
                    }
                }
            )
        )

        policy = PolicyManager(policy_path)
        assert policy.is_tool_allowed("github::delete_repo") is False
        assert policy.is_tool_allowed("jira::delete_issue") is False
        assert policy.is_tool_allowed("dangerous::anything") is False
        assert policy.is_tool_allowed("github::create_issue") is True

    def test_tenant_code_mode_tool_policy_patterns_are_narrow(
        self, tmp_path: Path
    ) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "tools": {
                        "allowlist": ["tenant-code-mode::get_*"],
                        "denylist": ["tenant-code-mode::run_script"],
                    }
                }
            )
        )

        policy = PolicyManager(policy_path)
        assert policy.is_tool_allowed("tenant-code-mode::get_result") is True
        assert policy.is_tool_allowed("tenant-code-mode::run_script") is False
        assert policy.is_tool_allowed("github::get_issue") is False

        policy_path.write_text(
            json.dumps(
                {
                    "tools": {
                        "denylist": ["tenant-code-mode::*"],
                    }
                }
            )
        )
        denied_policy = PolicyManager(policy_path)
        assert denied_policy.is_tool_allowed("tenant-code-mode::get_result") is False
        assert denied_policy.is_tool_allowed("github::get_issue") is True


class TestResourceAllowDeny:
    """Tests for resource allow/deny lists."""

    def test_allows_all_by_default(self) -> None:
        policy = PolicyManager()
        assert policy.is_resource_allowed("github::file://readme.md") is True
        assert policy.is_resource_allowed("jira::jira://issue/123") is True

    def test_denies_resources_on_denylist(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "resources": {
                        "denylist": ["*::file://*.env", "secrets::*"],
                    }
                }
            )
        )

        policy = PolicyManager(policy_path)
        assert policy.is_resource_allowed("github::file://.env") is False
        assert policy.is_resource_allowed("any::file://config.env") is False
        assert policy.is_resource_allowed("secrets::anything") is False
        assert policy.is_resource_allowed("github::file://readme.md") is True

    def test_only_allows_resources_on_allowlist(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "resources": {
                        "allowlist": ["docs::*", "public::*"],
                    }
                }
            )
        )

        policy = PolicyManager(policy_path)
        assert policy.is_resource_allowed("docs::file://readme.md") is True
        assert policy.is_resource_allowed("public::https://example.com") is True
        assert policy.is_resource_allowed("private::file://secret.txt") is False


class TestPromptAllowDeny:
    """Tests for prompt allow/deny lists."""

    def test_allows_all_by_default(self) -> None:
        policy = PolicyManager()
        assert policy.is_prompt_allowed("github::create_issue") is True
        assert policy.is_prompt_allowed("jira::summarize") is True

    def test_denies_prompts_on_denylist(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "prompts": {
                        "denylist": ["*::dangerous_*", "admin::*"],
                    }
                }
            )
        )

        policy = PolicyManager(policy_path)
        assert policy.is_prompt_allowed("github::dangerous_action") is False
        assert policy.is_prompt_allowed("jira::dangerous_prompt") is False
        assert policy.is_prompt_allowed("admin::anything") is False
        assert policy.is_prompt_allowed("github::create_issue") is True

    def test_only_allows_prompts_on_allowlist(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "prompts": {
                        "allowlist": ["safe::*", "approved::*"],
                    }
                }
            )
        )

        policy = PolicyManager(policy_path)
        assert policy.is_prompt_allowed("safe::any_prompt") is True
        assert policy.is_prompt_allowed("approved::summarize") is True
        assert policy.is_prompt_allowed("unapproved::something") is False


class TestOutputTruncation:
    """Tests for output truncation."""

    def test_does_not_truncate_small_outputs(self) -> None:
        policy = PolicyManager()
        result, truncated, original_size = policy.truncate_output("short output")

        assert result == "short output"
        assert truncated is False
        assert original_size == 12

    def test_truncates_large_outputs(self) -> None:
        policy = PolicyManager()
        large_output = "x" * 100000
        result, truncated, original_size = policy.truncate_output(large_output, 1000)

        assert len(result) < 1000
        assert truncated is True
        assert original_size == 100000
        assert "[... OUTPUT TRUNCATED" in result


class TestSecretRedaction:
    """Tests for secret redaction."""

    def test_redacts_common_patterns(self) -> None:
        policy = PolicyManager()

        input_text = """
            API_KEY=sk-1234567890
            password: mysecretpassword
            Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9
        """

        redacted = policy.redact_secrets(input_text)

        assert "sk-1234567890" not in redacted
        assert "mysecretpassword" not in redacted
        assert "[REDACTED]" in redacted

    def test_redacts_auth_urls_and_authorization_headers(self) -> None:
        policy = PolicyManager()

        redacted = policy.redact_secrets(
            "Authorization: Bearer token-value "
            "https://user:pass@example.test/callback?code=secret-code&state=ok"
        )

        assert "token-value" not in redacted
        assert "user:pass" not in redacted
        assert "secret-code" not in redacted

    def test_redacts_shared_auth_samples_and_custom_policy_patterns(
        self, tmp_path: Path
    ) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps({"redaction": {"patterns": [r"custom_secret=[^\s]+"]}})
        )
        policy = PolicyManager(policy_path)
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.N2QwODhmM2I4OTc1"

        redacted = policy.redact_secrets(
            "Bearer bearer-token custom_secret=private "
            f"https://user:pass@example.test/cb?ticket=ticket-secret {jwt}"
        )

        for leaked in [
            "bearer-token",
            "private",
            "user:pass",
            "ticket-secret",
            jwt,
        ]:
            assert leaked not in redacted
        assert "[REDACTED]" in redacted

    def test_redacts_tenant_code_mode_sandbox_outputs(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "redaction": {
                        "patterns": [
                            r"TENANT_CODE_MODE_[A-Z_]+=[^\s]+",
                            r"artifact_token=[^\s]+",
                        ]
                    }
                }
            )
        )
        policy = PolicyManager(policy_path)
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZW5hbnQifQ.N2QwODhmM2I4OTc1"

        redacted = policy.redact_secrets(
            "Authorization: Bearer tenant-bearer "
            "api_key=sk-tenant "
            f"jwt={jwt} "
            "https://tenant.example/artifacts/run-1?token=artifact-secret "
            "TENANT_CODE_MODE_MCP_TOKEN=stored-secret "
            "artifact_token=artifact-value"
        )

        for leaked in [
            "tenant-bearer",
            "sk-tenant",
            jwt,
            "artifact-secret",
            "stored-secret",
            "artifact-value",
        ]:
            assert leaked not in redacted

    def test_process_output_redacts_and_truncates_sandbox_logs(
        self, tmp_path: Path
    ) -> None:
        policy_path = tmp_path / "policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "redaction": {
                        "patterns": [
                            r"TENANT_CODE_MODE_MCP_TOKEN=[^\s]+",
                        ]
                    }
                }
            )
        )
        policy = PolicyManager(policy_path)
        output = (
            "run summary\n"
            "TENANT_CODE_MODE_MCP_TOKEN=tenant-secret\n"
            "Authorization: Bearer hidden-token\n" + ("sandbox log line\n" * 40)
        )

        processed = policy.process_output(output, redact=True, max_bytes=220)

        assert processed["truncated"] is True
        assert processed["raw_size"] > 220
        assert "tenant-secret" not in processed["result"]
        assert "hidden-token" not in processed["result"]
        assert "OUTPUT TRUNCATED" in processed["result"]


class TestYamlPolicyLoading:
    """Tests for YAML policy loading."""

    def test_loads_yaml_policy(self, tmp_path: Path) -> None:
        policy_path = tmp_path / "policy.yaml"
        policy_path.write_text(
            """
servers:
  denylist:
    - blocked-server
limits:
  max_output_bytes: 10000
"""
        )

        policy = PolicyManager(policy_path)
        assert policy.is_server_allowed("blocked-server") is False
        assert policy.get_max_output_bytes() == 10000
