"""Policy Layer - Handles allow/deny lists, output caps, and secret redaction."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from pmcp.types import GatewayPolicy
from pmcp.auth import sanitize_auth_diagnostic

logger = logging.getLogger(__name__)

DEFAULT_REDACTION_PATTERNS = [
    # Common secret patterns (case-insensitive)
    r"(api[_-]?key|apikey)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
    r"(secret|password|passwd|pwd)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
    r"(bearer|token)[\s]+[a-zA-Z0-9._-]+",
    r"(aws_secret|aws_access)[\s]*[:=][\s]*[\"']?([^\s\"']+)",
    r"\bsk-[A-Za-z0-9_-]{6,}\b",
    r"\bghp_[A-Za-z0-9_]{10,}\b",
    r"\bgithub_pat_[A-Za-z0-9_]{10,}\b",
]

# Search order for an auto-discovered policy. The project-local entries are kept
# RELATIVE on purpose: they are resolved against `Path.cwd()` when a
# `PolicyManager` is constructed, not when this module is imported. Storing them
# pre-joined froze the working directory as of import, so a gateway that changed
# directory before constructing its manager looked for a policy in the wrong
# place and silently found none -- an unrestricted gateway with no warning at all
# (Consiliency/pmcp#202).
#
# The list stays a module attribute so `monkeypatch.setattr` on
# `pmcp.policy.policy.DEFAULT_POLICY_PATHS` remains a working test seam; absolute
# entries pass through the resolver unchanged.
DEFAULT_POLICY_PATHS = [
    Path(".mcp-gateway-policy.yaml"),
    Path(".mcp-gateway-policy.json"),
    Path.home() / ".claude" / "gateway-policy.yaml",
    Path.home() / ".claude" / "gateway-policy.json",
]


def _default_policy_paths() -> list[Path]:
    """Resolve `DEFAULT_POLICY_PATHS` against the *current* working directory.

    Read the module attribute at call time so a monkeypatched list is honoured.
    """
    cwd = Path.cwd()
    return [path if path.is_absolute() else cwd / path for path in DEFAULT_POLICY_PATHS]


class PolicyManager:
    """Manages gateway policy including allow/deny lists, limits, and redaction."""

    def __init__(self, policy_path: Path | None = None) -> None:
        self._policy = GatewayPolicy()
        self._redaction_regexes: list[re.Pattern[str]] = []
        self._explicit_policy = policy_path is not None
        self._scoped_advisor_active = False

        if policy_path:
            self._load_policy(policy_path, fatal=True)
        else:
            # Try default locations
            for default_path in _default_policy_paths():
                if default_path.exists():
                    self._load_policy(default_path, fatal=False)
                    break

        self._compile_redaction_patterns()

    def _load_policy(self, policy_path: Path, *, fatal: bool) -> None:
        """Load policy from file.

        Reading/parsing and validating are separate steps because an
        auto-discovered policy treats them differently (Consiliency/pmcp#202):

        * a file that cannot be read, or that the parser *rejects*, could be
          anything -- a half-written file, an unrelated ``.json`` dropped at the
          repo root, a merge conflict. That warns and continues, which is the
          long-standing deliberate behaviour.
        * a file that parses without raising but is not a valid policy is
          unmistakably *a policy with a mistake in it*. Falling back to
          ``GatewayPolicy()`` there is indefensible -- that default is allow-all,
          so a restrictive policy on disk would stop applying. That refuses to
          start.

        Note that "parses without raising" includes a list root, a scalar root
        and an empty YAML file: ``yaml.safe_load`` returns ``list``, ``str`` and
        ``None`` for those without raising. They are schema-invalid, not
        unparseable, so they are fatal.
        """
        try:
            content = policy_path.read_text()

            if policy_path.suffix in (".yaml", ".yml"):
                data = yaml.safe_load(content)
            else:
                data = json.loads(content)
        except Exception as e:
            if fatal:
                raise ValueError(
                    f"Failed to load explicit policy {policy_path}: {e}"
                ) from e
            logger.warning(
                f"Could not parse policy file {policy_path}: {e}. "
                "No policy is in effect: the gateway is running unrestricted."
            )
            return

        try:
            if not isinstance(data, dict):
                raise ValueError(
                    f"policy root must be an object, got {type(data).__name__}"
                )
            policy = GatewayPolicy.model_validate(data)
        except Exception as e:
            if fatal:
                raise ValueError(
                    f"Failed to load explicit policy {policy_path}: {e}"
                ) from e
            raise ValueError(
                f"Invalid policy file {policy_path}: {e}. "
                "Refusing to start rather than fall back to an unrestricted gateway."
            ) from e

        self._policy = policy
        logger.info(f"Loaded policy from {policy_path}")

    def _compile_redaction_patterns(self) -> None:
        """Compile redaction regex patterns."""
        self._redaction_regexes = []

        # Use default patterns if none specified
        patterns = self._policy.redaction.patterns or DEFAULT_REDACTION_PATTERNS

        for pattern in patterns:
            try:
                self._redaction_regexes.append(re.compile(pattern, re.IGNORECASE))
            except re.error as e:
                logger.warning(f"Invalid redaction pattern '{pattern}': {e}")

    def _matches_any(self, value: str, patterns: list[str]) -> bool:
        """Check if value matches any glob pattern.

        Matching is case-SENSITIVE: server/tool/resource/prompt IDs are treated
        as case-sensitive everywhere else in the gateway, so an allow/deny glob
        must match the exact case (e.g. a deny of ``Secret*`` does not match
        ``secretserver``). ``fnmatchcase`` is used instead of ``fnmatch`` so the
        behavior is stable across case-insensitive host filesystems.
        """
        return any(fnmatch.fnmatchcase(value, p) for p in patterns)

    def is_server_allowed(self, server_name: str) -> bool:
        """Check if server is allowed by policy."""
        denylist = self._policy.servers.denylist
        allowlist = self._policy.servers.allowlist

        # Check denylist first
        if denylist and self._matches_any(server_name, denylist):
            return False

        # If allowlist is specified, server must be in it
        if allowlist:
            return self._matches_any(server_name, allowlist)

        return True

    def is_tool_allowed(self, tool_id: str) -> bool:
        """Check if tool is allowed by policy."""
        denylist = self._policy.tools.denylist
        allowlist = self._policy.tools.allowlist

        # Check denylist first
        if denylist and self._matches_any(tool_id, denylist):
            return False

        # If allowlist is specified, tool must be in it
        if allowlist:
            return self._matches_any(tool_id, allowlist)

        return True

    def is_gateway_tool_allowed(self, tool_name: str) -> bool:
        """Check a PMCP-owned gateway control against the explicit policy."""
        denylist = self._policy.gateway_tools.denylist
        allowlist = self._policy.gateway_tools.allowlist
        if denylist and self._matches_any(tool_name, denylist):
            return False
        if allowlist:
            return self._matches_any(tool_name, allowlist)
        return True

    @property
    def explicit_policy(self) -> bool:
        return self._explicit_policy

    @property
    def policy_digest(self) -> str:
        payload = self._policy.model_dump_json(exclude_none=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def is_scoped_advisor_policy(self) -> bool:
        """Return whether the loaded policy is the exact safe advisor profile."""
        required_controls = {
            "gateway.health",
            "gateway.catalog_search",
            "gateway.describe",
            "gateway.invoke",
        }
        if not self._explicit_policy:
            return False
        if set(self._policy.gateway_tools.allowlist) != required_controls:
            return False
        if self._policy.gateway_tools.denylist:
            return False
        if set(self._policy.servers.allowlist) != {"firecrawl", "brightdata"}:
            return False
        if self._policy.servers.denylist or self._policy.tools.denylist:
            return False
        if self._policy.resources.allowlist or self._policy.resources.denylist != ["*"]:
            return False
        if self._policy.prompts.allowlist or self._policy.prompts.denylist != ["*"]:
            return False
        allowed_patterns = {
            "firecrawl::*search*",
            "firecrawl::*scrape*",
            "firecrawl::*crawl*",
            "firecrawl::*map*",
            "firecrawl::*extract*",
            "brightdata::*search*",
            "brightdata::*scrape*",
            "brightdata::*crawl*",
            "brightdata::*query*",
            "brightdata::*fetch*",
            "brightdata::*unlocker*",
        }
        configured = set(self._policy.tools.allowlist)
        return bool(configured) and configured <= allowed_patterns

    @property
    def scoped_advisor_active(self) -> bool:
        """Return whether the audited scoped-advisor session is active."""
        return self._scoped_advisor_active

    def activate_scoped_advisor(self) -> None:
        """Activate scoped behavior after the policy and audit sink are ready."""
        if not self.is_scoped_advisor_policy():
            raise ValueError("scoped advisor activation requires the exact policy")
        self._scoped_advisor_active = True

    def is_resource_allowed(self, resource_id: str) -> bool:
        """Check if resource is allowed by policy.

        Args:
            resource_id: Resource ID in format "server_name::uri"
        """
        denylist = self._policy.resources.denylist
        allowlist = self._policy.resources.allowlist

        # Check denylist first
        if denylist and self._matches_any(resource_id, denylist):
            return False

        # If allowlist is specified, resource must be in it
        if allowlist:
            return self._matches_any(resource_id, allowlist)

        return True

    def is_prompt_allowed(self, prompt_id: str) -> bool:
        """Check if prompt is allowed by policy.

        Args:
            prompt_id: Prompt ID in format "server_name::name"
        """
        denylist = self._policy.prompts.denylist
        allowlist = self._policy.prompts.allowlist

        # Check denylist first
        if denylist and self._matches_any(prompt_id, denylist):
            return False

        # If allowlist is specified, prompt must be in it
        if allowlist:
            return self._matches_any(prompt_id, allowlist)

        return True

    def get_max_tools_per_server(self) -> int:
        """Get max tools per server limit."""
        return self._policy.limits.max_tools_per_server

    def get_max_output_bytes(self) -> int:
        """Get max output bytes."""
        return self._policy.limits.max_output_bytes

    def get_max_output_tokens(self) -> int:
        """Get max output tokens (rough estimate)."""
        return self._policy.limits.max_output_tokens

    def truncate_output(
        self, output: str, max_bytes: int | None = None
    ) -> tuple[str, bool, int]:
        """
        Truncate output to max size.

        Returns: (result, truncated, original_size)
        """
        max_size = max_bytes or self.get_max_output_bytes()
        original_size = len(output.encode("utf-8"))

        if original_size <= max_size:
            return (output, False, original_size)

        # Truncate to max bytes, being careful with UTF-8
        encoded = output.encode("utf-8")
        truncated_bytes = encoded[: max_size - 100]  # Leave room for message

        # Decode, ignoring incomplete characters at the end
        truncated_str = truncated_bytes.decode("utf-8", errors="ignore")

        # Add truncation indicator
        truncated_str += (
            f"\n\n[... OUTPUT TRUNCATED: {original_size} bytes -> {max_size} bytes ...]"
        )

        return (truncated_str, True, original_size)

    def redact_secrets(self, output: str) -> str:
        """Redact secrets from output."""
        result = sanitize_auth_diagnostic(output, max_length=None)

        for regex in self._redaction_regexes:

            def replace_match(match: re.Match[str]) -> str:
                full_match = match.group(0)
                # Find the separator (: or =)
                for i, char in enumerate(full_match):
                    if char in ":=":
                        return full_match[: i + 1] + " [REDACTED]"
                return "[REDACTED]"

            result = regex.sub(replace_match, result)

        return result

    def process_output(
        self,
        output: Any,
        *,
        redact: bool = False,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """
        Process output: truncate and optionally redact.

        Returns dict with: result, truncated, raw_size, summary
        """
        # Convert to string
        if isinstance(output, str):
            output_str = output
        else:
            output_str = json.dumps(output, indent=2)

        raw_size = len(output_str.encode("utf-8"))

        # Truncate first
        truncated_str, truncated, _ = self.truncate_output(output_str, max_bytes)

        # Redact if requested
        final_str = self.redact_secrets(truncated_str) if redact else truncated_str

        # Generate summary if truncated
        summary: str | None = None
        if truncated:
            lines = output_str.count("\n") + 1
            summary_source = final_str if redact else output_str
            first_line = summary_source.split("\n")[0][:100] if summary_source else ""
            summary = f'Output was {raw_size} bytes ({lines} lines). First line: "{first_line}..."'

        # Try to parse back to object if original was not string
        result: Any = final_str
        if not isinstance(output, str):
            try:
                result = json.loads(final_str)
            except json.JSONDecodeError:
                # Keep as string if truncation broke JSON
                pass

        return {
            "result": result,
            "truncated": truncated,
            "raw_size": raw_size,
            "summary": summary,
        }
