"""Policy Layer - Handles allow/deny lists, output caps, and secret redaction."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml

from pmcp.project_consent import log_refusal, read_and_gate
from pmcp.types import (
    GatewayPolicy,
    PackagePolicy,
    PromptPolicy,
    ResourcePolicy,
    ServerPolicy,
    ToolPolicy,
)
from pmcp.auth import (
    REDACTED,
    Span,
    apply_redaction_spans,
    collect_redaction_spans,
)

if TYPE_CHECKING:
    # Annotation only. `pmcp.manifest`'s package `__init__` imports the loader and
    # installer, so a runtime import here would put the whole manifest package on
    # the import path of every policy consumer.
    from pmcp.manifest.package_identity import PackageIdentity

logger = logging.getLogger(__name__)

#: A package verdict. Tri-state on purpose -- see `evaluate_package_policy`.
PackageVerdict = Literal["denied", "allowed", "unspecified"]

#: The four allow/deny sections share a shape but not a base class. Spelled out
#: as a union rather than as `Any` so a fifth section added to `GatewayPolicy`
#: without a composition rule is a type error rather than a silent hole.
_ListPolicy = ServerPolicy | ToolPolicy | ResourcePolicy | PromptPolicy

#: Closed on purpose: `_composed_limit` reaches its fields by name, and a typo
#: would otherwise return the wrong limit or raise at runtime.
_LimitField = Literal["max_tools_per_server", "max_output_bytes", "max_output_tokens"]

#: `process_output` redacts BEFORE it truncates, over the first
#: ``max_bytes + _REDACTION_WINDOW_SLACK`` characters of the output, so the cut
#: never lands inside a credential the redactor has not yet seen whole
#: (Consiliency/pmcp#234). The slack is the longest keyed value the redactor
#: can be asked to match across the cut: a 4096-bit RSA PEM block is ~3.2 KB,
#: a JWT with generous claims a few KB, a SAML assertion under ~16 KB. The
#: cost is bounded by it (~1 ms per KB on this host), and the residual is a
#: single value longer than the slack straddling the cap -- not a credential
#: shape. The window's far edge is never emitted: see `process_output`.
_REDACTION_WINDOW_SLACK = 16384

DEFAULT_REDACTION_PATTERNS = [
    # Common secret patterns (case-insensitive). The separator mirrors the
    # engine's: `:`, `=`, `=>`, `:=`, or `==` followed directly by the value,
    # on the same line (`[ \t]*`, not `[\s]*`, which let `token:\nthe` -- a
    # sentence ending in the keyword -- redact the first word of the next
    # line; `token == expected` is a comparison on this surface too).
    r"(api[_-]?key|apikey)[^\S\r\n]*(?:=>|:=|==(?![^\S\r\n]|=)|[:=](?!=))[^\S\r\n]*(?![\"'])([^\s\"']*[^\s\"'\\])",
    # Not after `:` or `.`: `arn:…:secret:Name` names a secret, it is not one.
    r"(?<![A-Za-z0-9:])(secret|password|passwd|pwd)[^\S\r\n]*(?:=>|:=|==(?![^\S\r\n]|=)|[:=](?!=))[^\S\r\n]*(?![\"'])([^\s\"']*[^\s\"'\\])",
    # `token` needs a real separator: the pre-#234 `(bearer|token)\s+…` form
    # redacted the word after "token" in prose ("token bucket"). Bearer values
    # are handled unconditionally by `sanitize_auth_diagnostic`.
    r"\btoken[^\S\r\n]*(?:=>|:=|==(?![^\S\r\n]|=)|[:=](?!=))[^\S\r\n]*(?![\"'])([^\s\"']*[^\s\"'\\])",
    r"(aws_secret|aws_access)[^\S\r\n]*(?:=>|:=|==(?![^\S\r\n]|=)|[:=](?!=))[^\S\r\n]*(?![\"'])([^\s\"']*[^\s\"'\\])",
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
#: The home-relative tails, named once so the factory below and the frozen
#: default cannot drift apart.
_USER_POLICY_TAILS = (
    Path(".claude") / "gateway-policy.yaml",
    Path(".claude") / "gateway-policy.json",
)


def default_user_policy_paths() -> list[Path]:
    """The operator locations, with `Path.home()` read at CALL time.

    Mirrors `config.loader.default_user_config_paths`. Resolving at call time is
    what makes a changed `HOME` -- the test suite's isolation, or a re-homed
    process -- take effect; the frozen attribute below cannot
    (Consiliency/pmcp#262).
    """
    return [Path.home() / tail for tail in _USER_POLICY_TAILS]


class _FrozenDefault(tuple[Path, ...]):
    """Marker type for the two untouched defaults.

    A PLAIN tuple would be unsafe as the sentinel: CPython returns the SAME
    object from `tuple(t)` when `t` is already a tuple, so a caller normalising
    with `USER_POLICY_PATHS = tuple(USER_POLICY_PATHS)` would still satisfy an
    identity check and have its pin silently ignored in favour of the live home.
    Constructing this subclass always copies, so any caller-supplied value --
    list, tuple, or a copy of the default -- compares as replaced.
    """

    __slots__ = ()


# Do NOT read this frozen value at runtime: it captures `Path.home()` at import
# time. Read `_effective_user_policy_paths()` instead. It stays a module
# attribute because `monkeypatch.setattr` on it is a documented test seam, and
# the resolvers below key on OBJECT IDENTITY -- if this attribute is still this
# exact object, the live home is used; if a caller replaced it, that caller's
# value is used verbatim and the live home is never consulted. Immutable, so an
# in-place mutation raises instead of being silently ignored.
USER_POLICY_PATHS: Sequence[Path] = _FrozenDefault(default_user_policy_paths())
_FROZEN_USER_POLICY_PATHS = USER_POLICY_PATHS

# Same contract: patched -> used verbatim; untouched -> derived from the
# allowlist in force, so an entry can never be searched-but-unrecognised or
# recognised-but-unsearched.
DEFAULT_POLICY_PATHS: Sequence[Path] = _FrozenDefault(
    (*PROJECT_POLICY_PATHS, *USER_POLICY_PATHS)
)
_FROZEN_DEFAULT_POLICY_PATHS = DEFAULT_POLICY_PATHS


def _resolve_against_cwd(paths: Sequence[Path]) -> list[Path]:
    cwd = Path.cwd()
    return [path if path.is_absolute() else cwd / path for path in paths]


def _effective_user_policy_paths() -> Sequence[Path]:
    """The allowlist in force: the caller's if patched, else the live home.

    IDENTITY, not equality -- a patched list that happens to equal the default
    is still the caller's, and must not be quietly replaced by the live home.
    """
    if USER_POLICY_PATHS is _FROZEN_USER_POLICY_PATHS:
        return default_user_policy_paths()
    return USER_POLICY_PATHS


def _default_policy_paths() -> list[Path]:
    """Resolve the search list against the *current* working directory.

    Read the module attribute at call time so a monkeypatched list is honoured.
    When it is untouched the list is DERIVED from the allowlist in force, so the
    searched user entries and the ungated user entries are always the same set.
    """
    if DEFAULT_POLICY_PATHS is _FROZEN_DEFAULT_POLICY_PATHS:
        return _resolve_against_cwd(
            [*PROJECT_POLICY_PATHS, *_effective_user_policy_paths()]
        )
    return _resolve_against_cwd(DEFAULT_POLICY_PATHS)


def _user_policy_paths() -> set[Path]:
    """The ungated locations, resolved the same way, also read at call time.

    Both lists must be read at call time or the scope test is decided by import
    order: a test that patches `USER_POLICY_PATHS` to a temporary file would
    otherwise be measured against the real `~/.claude` entries captured when this
    module was first imported.
    """
    return set(_resolve_against_cwd(_effective_user_policy_paths()))


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

    def _package_verdict(self, section: PackagePolicy, name: str) -> PackageVerdict:
        """Evaluate ONE policy's package lists. Deliberately not `_section_allows`.

        `_section_allows` answers `True` when nothing matches; here nothing
        matching is `"unspecified"`. An allowlist miss is `"unspecified"` too, not
        `"denied"`: only an explicit denylist match may outrank a recorded
        approval or the manifest exemption.

        The glob is matched against the name alone. Matching `name@version` as
        well would let a package author satisfy an allowlist through the
        version they publish (`evil@1.0.0-mcp` against `*-mcp`).
        """
        if section.denylist and self._matches_any(name, section.denylist):
            return "denied"
        if section.allowlist and self._matches_any(name, section.allowlist):
            return "allowed"
        return "unspecified"

    def evaluate_package_policy(
        self, identity: PackageIdentity | None
    ) -> PackageVerdict:
        """The composed policy verdict for a resolved package (IF-0-PKGID-1).

        **Tri-state, and `"unspecified"` is not permission.** A package no list
        matches -- including every package under a policy with no `packages`
        section -- is `"unspecified"`, which the provisioning gate refuses. The
        boolean predicates above default to `True`; reading that default as an
        allowlist match is how every discovered package would provision.

        **Composed by an explicit rule, never by `and`** (IF-0-CONSENT-2). All
        three verdicts are truthy strings, so `user and project` returns the
        project's verdict and a project `"allowed"` would overturn a user
        `"denied"`. Instead: if either side is `"denied"` the result is
        `"denied"`; otherwise it is the user's verdict. A project `"allowed"`
        never grants on its own -- it can only fail to deny. There is no shared
        `compose` helper, so a tri-state verdict cannot be routed through the
        boolean rule by accident.

        `None` names nothing a list could match, so it is `"unspecified"`; what
        an unresolvable identity means is the gate's decision, not policy's.
        """
        if identity is None:
            return "unspecified"
        return self.evaluate_package_name_policy(identity.name)

    def has_package_denylist(self) -> bool:
        """Does either policy carry a non-empty ``packages.denylist``?

        The provisioning gate asks this when a manifest entry's packages cannot
        be read: with a denylist in force that entry cannot be checked against
        it, and is refused; with none, nothing is lost by not reading it.
        """
        if self._policy.packages.denylist:
            return True
        return bool(
            self._project_policy is not None and self._project_policy.packages.denylist
        )

    def evaluate_package_name_policy(self, name: str) -> PackageVerdict:
        """The composed policy verdict for a package NAME, with no identity.

        For the package names a trusted manifest config spells out, where there
        is no resolved version to build a `PackageIdentity` from -- and none is
        needed, since globs match the name alone. `evaluate_package_policy`
        delegates here, so the two cannot drift: the same lists, the same
        denied-wins composition described there.
        """
        user = self._package_verdict(self._policy.packages, name)
        if self._project_policy is None:
            return user
        project = self._package_verdict(self._project_policy.packages, name)
        if user == "denied" or project == "denied":
            return "denied"
        return user

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
        self,
        output: str,
        max_bytes: int | None = None,
        *,
        original_size: int | None = None,
    ) -> tuple[str, bool, int]:
        """
        Truncate output to max size.

        ``original_size`` is the size to report when ``output`` is already a
        window of a larger text (see `process_output`); the marker then names
        the real size, and a window that fits under the cap but dropped text
        still counts as truncated and is cut where any text is.

        Returns: (result, truncated, original_size)
        """
        max_size = max_bytes or self.get_max_output_bytes()
        if original_size is None:
            original_size = len(output.encode("utf-8"))

        if original_size <= max_size:
            return (output, False, original_size)
        # A window that already fits still takes the same cut (`max_size -
        # 100`, room for the marker) so the cap holds wherever the text came
        # from; slicing a short window is a no-op.

        # Truncate to max bytes, being careful with UTF-8
        encoded = output.encode("utf-8")
        truncated_bytes = encoded[: max_size - 100]  # Leave room for message

        # Decode, ignoring incomplete characters at the end
        truncated_str = truncated_bytes.decode("utf-8", errors="ignore")

        # Add truncation indicator
        truncated_str += self._truncation_marker(original_size, max_size)

        return (truncated_str, True, original_size)

    @staticmethod
    def _truncation_marker(original_size: int, max_size: int) -> str:
        return (
            f"\n\n[... OUTPUT TRUNCATED: {original_size} bytes -> {max_size} bytes ...]"
        )

    def redact_secrets(self, output: str) -> str:
        """Redact secrets from output.

        The engine's spans and the operator's pattern spans are all collected
        over the same, unmodified ``output`` and applied in one step, so the
        operator's patterns never see -- and never depend on -- the engine's
        rewriting (Consiliency/pmcp#234).
        """
        return apply_redaction_spans(output, self.redaction_spans(output))

    def _pattern_matches(self, text: str) -> bool:
        """Does any operator (or default) pattern match ``text``? Asked of a
        percent-decoded query value so an encoded token cannot evade a
        pattern written for its decoded shape (main decoded before matching)."""
        return any(regex.search(text) for regex in self._redaction_regexes)

    def redaction_spans(self, output: str) -> list[Span]:
        """Every redaction either surface would make to ``output``, as spans."""
        spans: list[Span] = collect_redaction_spans(
            output, covers=self._pattern_matches
        )
        for regex in self._redaction_regexes:
            for match in regex.finditer(output):
                full_match = match.group(0)
                # A `key<sep>value` match keeps its key: split at the first
                # separator (: or =) that has a value after it. A separator
                # with nothing but separators after it is base64 padding
                # (`dXNlcjpwYXNzd29yZA==`), and splitting there kept the whole
                # secret and replaced the `=`.
                for i, char in enumerate(full_match):
                    if char in ":=" and full_match[i + 1 :].strip(" \t:="):
                        spans.append(
                            (match.start() + i + 1, match.end(), " " + REDACTED)
                        )
                        break
                else:
                    spans.append((match.start(), match.end(), REDACTED))
        return spans

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

        if redact:
            # Redact BEFORE the cut, over a window that reaches past the cap
            # by `_REDACTION_WINDOW_SLACK`: the redactor then sees every value
            # the cut could land in whole, and the cut can only ever shorten a
            # `[REDACTED]` (Consiliency/pmcp#234). Cutting first left the
            # first characters of a credential -- a shape no rule recognises.
            #
            # Invariant: no character from past the cap is emitted except
            # inside a span. The window's far edge is a second cut, and it is
            # not redacted either; so the text kept is the cap, extended only
            # to the end of any span that starts before it. Rev 5 applied the
            # spans to the whole window and returned it when redaction had
            # shrunk it under the cap -- edge included.
            max_size = max_bytes or self.get_max_output_bytes()
            window = output_str[: max_size + _REDACTION_WINDOW_SLACK]
            # A structured result is serialised first and the window of the
            # serialised text redacted: JSON text INSIDE a leaf arrives with
            # escaped quotes the keyed rules do not read -- scoped out to
            # Consiliency/pmcp#290; the bar here is never worse than main.
            spans = self.redaction_spans(window)
            keep = len(
                window.encode("utf-8")[:max_size].decode("utf-8", errors="ignore")
            )
            for start, end, _ in sorted(spans):
                if start < keep < end:
                    keep = end
            head = window[:keep]
            final_str, truncated, _ = self.truncate_output(
                apply_redaction_spans(
                    head, [span for span in spans if span[1] <= keep]
                ),
                max_bytes,
                original_size=raw_size,
            )
        else:
            final_str, truncated, _ = self.truncate_output(output_str, max_bytes)

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
