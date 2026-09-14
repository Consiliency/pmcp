"""Policy Layer - Handles allow/deny lists, output caps, and secret redaction."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

import yaml

from pmcp.project_consent import log_refusal, read_and_gate
from pmcp.types import (
    GatewayPolicy,
    PromptPolicy,
    ResourcePolicy,
    ServerPolicy,
    ToolPolicy,
)
from pmcp.auth import sanitize_auth_diagnostic

logger = logging.getLogger(__name__)

#: The four allow/deny sections share a shape but not a base class. Spelled out
#: as a union rather than as `Any` so a fifth section added to `GatewayPolicy`
#: without a composition rule is a type error rather than a silent hole.
_ListPolicy = ServerPolicy | ToolPolicy | ResourcePolicy | PromptPolicy

#: Closed on purpose: `_composed_limit` reaches its fields by name, and a typo
#: would otherwise return the wrong limit or raise at runtime.
_LimitField = Literal["max_tools_per_server", "max_output_bytes", "max_output_tokens"]

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
PROJECT_POLICY_PATHS = [
    Path(".mcp-gateway-policy.yaml"),
    Path(".mcp-gateway-policy.json"),
]

#: The operator-supplied locations. Membership here -- not absoluteness, and not
#: "somewhere under `$HOME`" -- is what makes a discovered path ungated
#: (IF-0-CONSENT-2, Consiliency/pmcp#230). Both rejected alternatives fail open: a checkout
#: lives at an absolute path once discovery resolves it, and checkouts routinely
#: live under `$HOME` (`~/code/...`), so either rule would hand a repository file
#: the operator's trust -- S-11 through the back door.
#:
#: Anything discovered that is *not* one of these is project-scoped and must pass
#: the consent gate. That direction is the fail-closed one: an unrecognised entry
#: is treated as repository-supplied, never as operator-supplied.
USER_POLICY_PATHS = [
    Path.home() / ".claude" / "gateway-policy.yaml",
    Path.home() / ".claude" / "gateway-policy.json",
]

DEFAULT_POLICY_PATHS = [*PROJECT_POLICY_PATHS, *USER_POLICY_PATHS]


def _resolve_against_cwd(paths: list[Path]) -> list[Path]:
    cwd = Path.cwd()
    return [path if path.is_absolute() else cwd / path for path in paths]


def _default_policy_paths() -> list[Path]:
    """Resolve `DEFAULT_POLICY_PATHS` against the *current* working directory.

    Read the module attribute at call time so a monkeypatched list is honoured.
    """
    return _resolve_against_cwd(DEFAULT_POLICY_PATHS)


def _user_policy_paths() -> set[Path]:
    """The ungated locations, resolved the same way, also read at call time.

    Both lists must be read at call time or the scope test is decided by import
    order: a test that patches `USER_POLICY_PATHS` to a temporary file would
    otherwise be measured against the real `~/.claude` entries captured when this
    module was first imported.
    """
    return set(_resolve_against_cwd(USER_POLICY_PATHS))


def _effective_redaction_patterns(policy: GatewayPolicy) -> list[str]:
    """The patterns one policy actually applies.

    An empty `patterns` list has always meant "the defaults", not "redact
    nothing"; making that substitution explicit is what lets the two sets be
    unioned without the empty list swallowing the defaults.
    """
    return list(policy.redaction.patterns) or list(DEFAULT_REDACTION_PATTERNS)


class PolicyManager:
    """Manages gateway policy including allow/deny lists, limits, and redaction."""

    def __init__(self, policy_path: Path | None = None) -> None:
        self._policy = GatewayPolicy()
        #: An approved project-scoped policy, composed *conjunctively* with
        #: `_policy` by every predicate below. `None` means there is none -- a
        #: project policy that was absent, refused, or unparseable is
        #: indistinguishable from here, deliberately: none of those may
        #: contribute anything (IF-0-CONSENT-2).
        self._project_policy: GatewayPolicy | None = None
        self._user_policy_loaded = False
        self._redaction_regexes: list[re.Pattern[str]] = []
        self._explicit_policy = policy_path is not None
        self._scoped_advisor_active = False

        if policy_path:
            self._policy = (
                self._read_policy_file(policy_path, fatal=True) or self._policy
            )
        else:
            self._discover_policies()

        self._compile_redaction_patterns()

    def _discover_policies(self) -> None:
        """Find the user base policy and the candidate project overlay.

        The loop no longer `break`s at the first existing file. That break was
        S-11: `DEFAULT_POLICY_PATHS` lists the project-relative names first, so a
        `.mcp-gateway-policy.yaml` committed to a repository ended discovery
        before the operator's `~/.claude` policy was ever looked at -- silently
        replacing it, with the allow-all `GatewayPolicy()` as the fallback if the
        repository file happened not to parse.

        Now each scope contributes at most one file: the first existing
        user-scoped path is the base, the first existing project-scoped path is
        the *candidate* overlay. First-match-wins survives **within** a scope, so
        a refused or unparseable project policy does not fall through to the next
        project name -- falling through would let a repository ship a decoy
        `.yaml` beside the `.json` it actually wanted applied.
        """
        user_paths = _user_policy_paths()
        user_candidate: Path | None = None
        project_candidate: Path | None = None

        for candidate in _default_policy_paths():
            if candidate in user_paths:
                if user_candidate is None and candidate.exists():
                    user_candidate = candidate
            elif project_candidate is None and candidate.exists():
                project_candidate = candidate

        # The user policy first, so a project-policy warning below can say
        # truthfully whether anything is still in effect.
        if user_candidate is not None:
            policy = self._read_policy_file(user_candidate, fatal=False)
            if policy is not None:
                self._policy = policy
                self._user_policy_loaded = True

        if project_candidate is not None:
            self._load_project_policy(project_candidate)

    def _load_project_policy(self, policy_path: Path) -> None:
        """Admit a project policy only if the operator approved these bytes.

        `read_and_gate` performs exactly one read and hands back what it hashed;
        those are the bytes parsed here. Re-opening `policy_path` would parse
        content nobody approved, which is the entire window the gate closes.

        A refusal returns before any parsing, so an unapproved policy is never
        read into the composed result at all -- not even far enough to be
        rejected by Consiliency/pmcp#202's fail-closed validation. That ordering is
        load-bearing: gating after parsing would turn a repository file into a
        startup crash, which is a denial of service the operator never consented
        to either.
        """
        content, decision = read_and_gate(policy_path, "project_policy")
        if content is None:
            log_refusal(decision, logger)
            return

        # Consiliency/pmcp#202's fail-closed rule applies to an approved project policy
        # exactly as it does to a user one: parses-but-invalid still refuses to
        # start. Consent decides whether the file is read, not whether it is
        # allowed to be wrong.
        self._project_policy = self._parse_policy(content, policy_path, fatal=False)

    def _read_policy_file(
        self, policy_path: Path, *, fatal: bool
    ) -> GatewayPolicy | None:
        """Read an **ungated** policy file -- `--policy`, or a user-scoped one.

        Project-scoped files never reach this method: their bytes come from the
        consent gate, and a second read here would defeat it.
        """
        try:
            content = policy_path.read_text()
        except Exception as e:
            if fatal:
                raise ValueError(
                    f"Failed to load explicit policy {policy_path}: {e}"
                ) from e
            self._warn_unparseable(policy_path, e)
            return None
        return self._parse_policy(content, policy_path, fatal=fatal)

    def _warn_unparseable(self, policy_path: Path, error: Exception) -> None:
        """The one warning for a discovered file we could not turn into a policy.

        The tail of the message is conditional because the unconditional form was
        capable of lying: once a user policy can be loaded *and* a project policy
        skipped, "the gateway is running unrestricted" would be false while the
        operator's own policy was in force.
        """
        consequence = (
            "Continuing with the user-scoped policy only."
            if self._user_policy_loaded
            else "No policy is in effect: the gateway is running unrestricted."
        )
        logger.warning(
            f"Could not parse policy file {policy_path}: {error}. {consequence}"
        )

    def _parse_policy(
        self, content: str | bytes, policy_path: Path, *, fatal: bool
    ) -> GatewayPolicy | None:
        """Parse and validate policy content.

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

        ``content`` may be ``bytes``: a project policy's bytes come from the
        consent gate and are parsed as handed over, never decoded-and-re-read.
        Both parsers accept bytes, so undecodable content raises inside the
        parse step and takes the warn path like any other unreadable file.
        """
        try:
            if policy_path.suffix in (".yaml", ".yml"):
                data = yaml.safe_load(content)
            else:
                data = json.loads(content)
        except Exception as e:
            if fatal:
                raise ValueError(
                    f"Failed to load explicit policy {policy_path}: {e}"
                ) from e
            self._warn_unparseable(policy_path, e)
            return None

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

        logger.info(f"Loaded policy from {policy_path}")
        return policy

    def _compile_redaction_patterns(self) -> None:
        """Compile the union of the two **effective** pattern sets.

        "Effective" is `patterns or DEFAULT_REDACTION_PATTERNS` -- the same
        substitution that has always applied per policy. Unioning the *raw*
        lists instead would widen: an operator who never wrote a `redaction`
        section has an empty list, so `[] + ["<project pattern>"]` is truthy and
        `DEFAULT_REDACTION_PATTERNS` stops applying. A repository file would then
        reduce secret redaction across the whole gateway by naming one pattern --
        the S-11 class arriving through redaction.
        """
        self._redaction_regexes = []

        patterns = _effective_redaction_patterns(self._policy)
        if self._project_policy is not None:
            seen = set(patterns)
            for pattern in _effective_redaction_patterns(self._project_policy):
                if pattern not in seen:
                    seen.add(pattern)
                    patterns.append(pattern)

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

    def _section_allows(self, section: _ListPolicy, value: str) -> bool:
        """Evaluate ONE policy's allow/deny lists for one identifier."""
        denylist = section.denylist
        allowlist = section.allowlist

        # Check denylist first
        if denylist and self._matches_any(value, denylist):
            return False

        # If allowlist is specified, the value must be in it
        if allowlist:
            return self._matches_any(value, allowlist)

        return True

    def is_server_allowed(self, server_name: str) -> bool:
        """Check if server is allowed by **both** policies (IF-0-CONSENT-2).

        Conjunctive, evaluated separately per policy -- never by intersecting
        the glob *strings*. Intersection is wrong in both directions: user
        `github*` against project `github-mcp` has an empty string intersection
        although both admit `github-mcp`, and a denylist has no meaningful
        intersection at all. Conjunction also gives denylist union for free: a
        name either side denies is denied.
        """
        if not self._section_allows(self._policy.servers, server_name):
            return False
        if self._project_policy is None:
            return True
        return self._section_allows(self._project_policy.servers, server_name)

    def is_tool_allowed(self, tool_id: str) -> bool:
        """Check if tool is allowed by both policies. See `is_server_allowed`."""
        if not self._section_allows(self._policy.tools, tool_id):
            return False
        if self._project_policy is None:
            return True
        return self._section_allows(self._project_policy.tools, tool_id)

    def is_gateway_tool_allowed(self, tool_name: str) -> bool:
        """Check a PMCP-owned gateway control against both policies.

        Composed like every other boolean predicate: a project policy may take
        gateway controls away, and may never hand one back.
        """
        if not self._section_allows(self._policy.gateway_tools, tool_name):
            return False
        if self._project_policy is None:
            return True
        return self._section_allows(self._project_policy.gateway_tools, tool_name)

    @property
    def explicit_policy(self) -> bool:
        return self._explicit_policy

    @property
    def policy_digest(self) -> str:
        """Hash **both** policies, so a project policy is never invisible.

        Telemetry that reported only the user policy while the gateway enforced
        something narrower would make the composed behaviour unattributable. With
        no project policy the payload is byte-identical to the pre-composition
        one, so the digest an operator with no project file sees does not move.
        """
        payload = self._policy.model_dump_json(exclude_none=False)
        if self._project_policy is not None:
            payload += "\n" + self._project_policy.model_dump_json(exclude_none=False)
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
        if not self._section_allows(self._policy.resources, resource_id):
            return False
        if self._project_policy is None:
            return True
        return self._section_allows(self._project_policy.resources, resource_id)

    def is_prompt_allowed(self, prompt_id: str) -> bool:
        """Check if prompt is allowed by policy.

        Args:
            prompt_id: Prompt ID in format "server_name::name"
        """
        if not self._section_allows(self._policy.prompts, prompt_id):
            return False
        if self._project_policy is None:
            return True
        return self._section_allows(self._project_policy.prompts, prompt_id)

    def _composed_limit(self, field: _LimitField) -> int:
        """`min()` over the user's EFFECTIVE value and the project's EXPLICIT one.

        The asymmetry is the whole rule, and both symmetric alternatives are
        wrong in opposite directions:

        * `min()` over both **effective** values lets a project policy that never
          mentions `limits` drag a deliberately raised operator limit back to
          Pydantic's defaults (100 / 50000 / 4000) -- a file changing a limit by
          saying nothing about it.
        * `min()` over both **explicitly-set** values *widens*, which is worse. If
          the operator never set `max_tools_per_server` its effective value is
          100; if an approved project policy sets 1000, the only explicitly-set
          value is the project's, and `min()` over that single term returns 1000.
          A repository file would have raised the operator's ceiling -- S-11
          arriving through the limits path.

        So the user's effective value is always a term, and only the project's
        *unset* fields are excluded. `model_fields_set` is what distinguishes
        "set to the default" from "never mentioned"; note `limits: {}` sets
        nothing, so it contributes nothing.
        """
        user_value: int = getattr(self._policy.limits, field)
        project = self._project_policy
        if project is None or field not in project.limits.model_fields_set:
            return user_value
        project_value: int = getattr(project.limits, field)
        return min(user_value, project_value)

    def get_max_tools_per_server(self) -> int:
        """Get max tools per server limit."""
        return self._composed_limit("max_tools_per_server")

    def get_max_output_bytes(self) -> int:
        """Get max output bytes."""
        return self._composed_limit("max_output_bytes")

    def get_max_output_tokens(self) -> int:
        """Get max output tokens (rough estimate)."""
        return self._composed_limit("max_output_tokens")

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
