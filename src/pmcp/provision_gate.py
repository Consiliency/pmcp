"""The one decision `provision` consults before an install may spawn.

Consiliency/pmcp#230 (IF-0-PKGID-1), closing review finding S-01: policy
checked the agent-chosen *server name* while `provision` ran the agent-chosen
*package* with `npx -y`, so allowlisting `internal-approved-tool` executed
`npx -y totally-arbitrary-evil-package`. This gate decides on the package's
resolved identity instead, and on nothing the agent can relabel.

**The decision order is frozen**, and every step fails closed:

1. the composed package policy says ``"denied"`` -> deny (``denied``) -- for
   the resolved identity, or, for a manifest lookup (which has none), for any
   package name the trusted manifest config names;
2. the config came from the shipped manifest -> allow (``manifest_backed``);
3. no usable identity -> deny (``unresolvable_identity``);
4. the argv does not run exactly ``name@resolved_version`` -> deny
   (``unpinned_configured_argv``);
5. an operator approval is recorded for the identity -> allow
   (``package_approved``);
6. the composed package policy says ``"allowed"`` -> allow (``policy_allowed``);
7. anything else, ``"unspecified"`` included -> deny (``not_approved``);
8. any exception along the way -> deny.

Rule 4 precedes BOTH allow rules, and that placement is the rule. An approval
of ``pkg@1.2.3`` attached to an argv of ``npx -y pkg`` approves nothing: npx
re-resolves ``latest`` at every spawn, so the bytes that run are not the bytes
approved. An earlier revision checked the pin after rules 5 and 6, which had
already returned -- S-01 again, for exactly the servers an operator trusted.

``source`` is an argument, never read off ``server_config``: the caller sets it
from the lookup that produced the config, because every field on a discovered
config was composed from agent input.
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from pathlib import PurePath
from typing import TYPE_CHECKING, Literal

from pmcp.package_approvals import is_package_approved
from pmcp.validation import (
    is_valid_package_name,
    is_valid_package_version,
    normalized_executable_name,
    parse_package_spec,
)

if TYPE_CHECKING:
    # Annotation only, as in `pmcp.policy.policy`: the manifest package's
    # `__init__` pulls in the loader and installer.
    from pmcp.manifest.loader import ServerConfig
    from pmcp.manifest.package_identity import PackageIdentity
    from pmcp.policy.policy import PolicyManager

logger = logging.getLogger(__name__)

ProvisionSource = Literal["manifest", "configured", "discovered"]

#: The closed reason vocabulary, one per rule (rule 8 reuses ``not_approved``).
PROVISION_REASONS = frozenset(
    {
        "denied",
        "manifest_backed",
        "unresolvable_identity",
        "unpinned_configured_argv",
        "package_approved",
        "policy_allowed",
        "not_approved",
    }
)

APPROVE_PACKAGE_COMMAND = "pmcp trust approve-package"

#: Flags that may precede the package in a pinned `npx` argv. An allowlist, and
#: short on purpose: `-p`/`--package` installs a *second* package before the
#: named one runs, and `--registry`/`--call` change what runs, so an argv that
#: carries anything else before the package slot is not pinned to the identity.
_NPX_LEADING_FLAGS = frozenset({"-y", "--yes", "-q", "--quiet"})
_NPX_EXECUTABLES = frozenset({"npx", "npx.cmd", "npx.exe"})
#: npx's package selectors. Each installs the package it names before anything
#: runs, and may be repeated; ``--package=X`` is the joined spelling.
_NPX_PACKAGE_OPTIONS = frozenset({"-p", "--package"})
_NPX_PACKAGE_OPTION_PREFIX = "--package="


@dataclass(frozen=True)
class ProvisionDecision:
    """The gate's answer. ``remedy`` is ``None`` exactly when ``allowed``."""

    allowed: bool
    reason: str
    remedy: str | None
    identity: PackageIdentity | None


def operator_safe(value: str) -> str:
    """Render an agent- or registry-controlled value for an operator's terminal.

    The same two-step rule as `pmcp.project_consent._operator_safe` (kept private
    there, so restated here rather than imported across a phase boundary):
    escape every non-printable character -- C0/C1 controls, bidi overrides,
    zero-width characters -- so the line cannot rewrite itself as it prints;
    THEN ``shlex.quote`` unconditionally, so a pasted line expands nothing. The
    order matters: CONSENT shipped a version that used ``repr()`` instead of
    quoting when a control character was present, and ``repr()`` picks double
    quotes, inside which a shell still expands ``$(...)``.
    """
    escaped = "".join(ch if ch.isprintable() else repr(ch)[1:-1] for ch in value)
    return shlex.quote(escaped)


def approve_package_command(identity: PackageIdentity) -> str:
    """The exact, paste-safe command that approves *identity*."""
    return (
        f"{APPROVE_PACKAGE_COMMAND} "
        f"{operator_safe(f'{identity.name}@{identity.resolved_version}')}"
    )


def _identity_is_argv_safe(identity: PackageIdentity) -> bool:
    """Would this identity's name and version be safe to compose into argv?

    `resolve_package_identity` and registration both validate, but the gate
    does not rely on its callers: an identity it cannot pin is no identity.
    """
    return (
        bool(identity.registry)
        and is_valid_package_name(identity.name)
        and is_valid_package_version(identity.resolved_version)
    )


def _package_slot(args: list[str]) -> str | None:
    """The package slot of an `npx` argument list, or ``None`` if it has none.

    Structural: the first argument that is not an allowlisted leading flag.
    Anything after it belongs to the server, whatever it looks like.
    """
    for arg in args:
        if arg in _NPX_LEADING_FLAGS:
            continue
        return arg
    return None


def _runs_exactly(args: list[str], spec: str) -> bool:
    """Is *spec* the package slot of an `npx` argument list?

    Structural, not a substring search: the first argument that is not an
    allowlisted leading flag must BE the spec. Anything after it belongs to the
    server. ``["-y", "other", "pkg@1.2.3"]`` runs ``other``, and
    ``["-p", "evil", "-y", "pkg@1.2.3"]`` installs ``evil`` first; neither is
    pinned to ``pkg@1.2.3``.
    """
    slot = _package_slot(args)
    return slot is not None and slot == spec


def _is_npx(executable: str) -> bool:
    return PurePath(executable).name in _NPX_EXECUTABLES


def _config_runs_exactly(server_config: ServerConfig, spec: str) -> bool:
    """Do the spawn argv AND every install argv run exactly *spec* under npx?

    Both, because both spawn: `client/manager.py` starts the server with
    ``command`` + ``args``, and `start_install` runs the platform's ``install``
    argv. Pinning one and not the other approves ``pkg@1.2.3`` and runs
    ``latest``. The executable must be npx: an approval is for an npm identity,
    and ``bash pkg@1.2.3`` merely spells its name.
    """
    if not _is_npx(server_config.command) or not _runs_exactly(
        list(server_config.args), spec
    ):
        return False
    for argv in server_config.install.values():
        if not argv:
            continue
        if not _is_npx(argv[0]) or not _runs_exactly(list(argv[1:]), spec):
            return False
    return True


def _is_npx_named(executable: str) -> bool:
    """Is *executable* npx under any platform spelling (``NPX.CMD``, ``npx.bat``)?

    For READING a trusted manifest entry's packages only, where recognising
    more spellings can only add names a denylist may match. The pin check keeps
    the strict `_is_npx`: there a wider match would accept argv as pinned.
    """
    return normalized_executable_name(executable) == "npx"


def _npx_selected_specs(args: list[str]) -> tuple[list[str], str | None]:
    """Every package spec an `npx` argument list selects, and what stopped it.

    Read left to right until the positional slot: the allowlisted leading flags
    are skipped; ``-p X``, ``--package X`` and ``--package=X`` each select X,
    any number of times; ``--`` ends the options, and the argument after it is
    the slot. The slot is taken by POSITION, as `_package_slot` takes it, and
    everything after it belongs to the server.

    What the slot holds depends on whether a selector came first. Without one
    it is the package spec, and is returned. With one it is the COMMAND npx
    runs from the selected packages -- ``npx --package=pkg node server.js``
    runs ``node`` and fetches no package of that name -- so it is neither a
    package nor something unreadable, and is not returned.

    The second value is the first argument that could not be placed, or
    ``None``: any other option (``--registry X``, ``-c``, ``--call=...``) --
    pmcp does not know whether it swallows the next argument, so nothing after
    it can be read -- a selector with no value, or one whose value begins with
    ``-``. What was selected before it is still returned.
    """
    specs: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in _NPX_LEADING_FLAGS:
            index += 1
        elif arg in _NPX_PACKAGE_OPTIONS:
            if index + 1 >= len(args) or args[index + 1].startswith("-"):
                return specs, arg
            specs.append(args[index + 1])
            index += 2
        elif arg.startswith(_NPX_PACKAGE_OPTION_PREFIX):
            specs.append(arg[len(_NPX_PACKAGE_OPTION_PREFIX) :])
            index += 1
        elif arg == "--":
            if index + 1 < len(args) and not specs:
                specs.append(args[index + 1])
            return specs, None
        elif arg.startswith("-"):
            return specs, arg
        else:
            if not specs:
                specs.append(arg)
            return specs, None
    return specs, None


def _spec_name(spec: str) -> str | None:
    try:
        name, _version = parse_package_spec(spec)
    except ValueError:
        return None
    return name


def _manifest_package_names(
    server_config: ServerConfig,
) -> tuple[list[str], str | None]:
    """The npm package names a manifest config names, and what hid the rest.

    Names, in order and once each, from: the ``package`` field; and, for
    ``args`` when the command is npx and for every platform ``install`` argv
    whose executable is npx, every spec `_npx_selected_specs` reads: every
    package selector's value, and the positional slot only when no selector
    came before it -- after a selector the slot is the command npx runs, not a
    package. Only a manifest config is read this way: its fields are shipped,
    not agent-composed.

    The second value is the first npx argument that stopped the reading, or
    that selected something that is not a valid package spec (``github:x/y``,
    ``./dir``) -- in either case npx may fetch a package no name here stands
    for. ``None`` when every selected package was named. The ``package`` field
    is metadata rather than argv, so an unparseable one names nothing and hides
    nothing.
    """
    specs: list[str] = []
    undetermined: str | None = None

    def read(npx_args: list[str]) -> None:
        nonlocal undetermined
        selected, stopped_at = _npx_selected_specs(npx_args)
        for spec in selected:
            if _spec_name(spec) is None and undetermined is None:
                undetermined = spec
        specs.extend(selected)
        if stopped_at is not None and undetermined is None:
            undetermined = stopped_at

    if _is_npx_named(server_config.command):
        read(list(server_config.args))
    for argv in server_config.install.values():
        if argv and _is_npx_named(argv[0]):
            read(list(argv[1:]))

    names: list[str] = []
    for spec in ([server_config.package] if server_config.package else []) + specs:
        name = _spec_name(spec)
        if name is not None and name not in names:
            names.append(name)
    return names, undetermined


def _manifest_denial(server_config: ServerConfig, policy: PolicyManager) -> str | None:
    """The remedy for refusing a manifest config under rule 1, or ``None``.

    Refused when policy denies any package name the config names, or -- only
    when a ``packages.denylist`` is in force -- when some package npx would
    fetch could not be named, since that one cannot be checked against it.
    Without a denylist an unreadable entry loses nothing, and is left alone.

    A policy object without the name-level predicates answers as it did before
    names were read: every name ``"unspecified"``, no denylist. `PolicyManager`
    always has both, and a predicate that is present and raises still fails
    closed through rule 8.
    """
    names, undetermined = _manifest_package_names(server_config)
    evaluate_name = getattr(policy, "evaluate_package_name_policy", None)
    if evaluate_name is not None:
        for name in names:
            if evaluate_name(name) == "denied":
                return _denied_remedy(name)
    has_denylist = getattr(policy, "has_package_denylist", None)
    if undetermined is not None and has_denylist is not None and has_denylist():
        return _undetermined_remedy(undetermined)
    return None


def _deny(
    reason: str, remedy: str, identity: PackageIdentity | None
) -> ProvisionDecision:
    return ProvisionDecision(
        allowed=False, reason=reason, remedy=remedy, identity=identity
    )


def _allow(reason: str, identity: PackageIdentity | None) -> ProvisionDecision:
    return ProvisionDecision(
        allowed=True, reason=reason, remedy=None, identity=identity
    )


def _denied_remedy(name: str) -> str:
    # Rule 1 outranks every approval, so offering `approve-package` here would
    # advertise a command that cannot work. Takes a NAME: a manifest denial has
    # no identity.
    return (
        f"Package {operator_safe(name)} is on a packages.denylist in the "
        "gateway policy, which overrides any recorded approval. To allow it, "
        "remove it from the denylist in the operator's policy file "
        "(~/.claude/gateway-policy.yaml, or an approved project "
        ".mcp-gateway-policy.yaml)."
    )


def _undetermined_remedy(argument: str) -> str:
    # No name, so nothing to take off a denylist; say what hid it instead.
    return (
        "The packages this manifest entry's npx command would fetch could not be "
        f"determined: pmcp cannot place the argument {operator_safe(argument)}. "
        "A packages.denylist is in force in the gateway policy, and it cannot be "
        "checked against a package that cannot be named. To start this server, "
        "remove that argument from the entry, or remove the packages.denylist "
        "from the operator's policy file (~/.claude/gateway-policy.yaml, or an "
        "approved project .mcp-gateway-policy.yaml)."
    )


def _unresolvable_remedy(server_config: ServerConfig) -> str:
    # No identity means no name@version to approve; name the lookup instead.
    package = server_config.package
    subject = (
        f"package {operator_safe(package)}" if package else "this server's package"
    )
    return (
        f"The npm registry lookup for {subject} did not resolve to one exact, "
        "valid version, so there is no package identity to approve. Check the "
        "package name, then register the server again with "
        "gateway.register_discovered_server."
    )


def _unpinned_remedy(identity: PackageIdentity) -> str:
    spec = operator_safe(f"{identity.name}@{identity.resolved_version}")
    return (
        f"Pin the version: the server must run as npx -y {spec}, with that exact "
        "version in both its args and its install command. Set it in .mcp.json, "
        "or register the server again, then provision."
    )


def _evaluate(
    server_config: ServerConfig,
    identity: PackageIdentity | None,
    source: ProvisionSource,
    policy: PolicyManager,
) -> ProvisionDecision:
    verdict = policy.evaluate_package_policy(identity)

    # (1) Deny always wins -- over the manifest exemption and any approval.
    if identity is not None and verdict == "denied":
        return _deny("denied", _denied_remedy(identity.name), identity)
    # A manifest lookup carries no identity, so without this a denylisted
    # manifest package provisioned through rule 2. The names come from the
    # trusted config and need no registry lookup.
    if source == "manifest":
        manifest_remedy = _manifest_denial(server_config, policy)
        if manifest_remedy is not None:
            return _deny("denied", manifest_remedy, identity)

    # (2) Only a manifest LOOKUP exempts; nothing on the config can claim it.
    if source == "manifest":
        return _allow("manifest_backed", identity)

    # (3) Nothing resolvable, nothing to approve.
    if identity is None or not _identity_is_argv_safe(identity):
        return _deny(
            "unresolvable_identity", _unresolvable_remedy(server_config), identity
        )

    # (4) BEFORE any allow: an approval of name@version is worthless attached to
    # an argv that re-resolves to something else at spawn.
    spec = f"{identity.name}@{identity.resolved_version}"
    if not _config_runs_exactly(server_config, spec):
        return _deny("unpinned_configured_argv", _unpinned_remedy(identity), identity)

    # (5) The operator approved this exact identity.
    if is_package_approved(identity):
        return _allow("package_approved", identity)

    # (6) The policy names it. Only the exact verdict "allowed" grants.
    if verdict == "allowed":
        return _allow("policy_allowed", identity)

    # (7) "unspecified" is not permission.
    return _deny("not_approved", approve_package_command(identity), identity)


def _fallback_remedy(
    server_config: ServerConfig, identity: PackageIdentity | None
) -> str:
    """A refusal message for rule 8 that itself cannot raise."""
    try:
        if identity is not None and _identity_is_argv_safe(identity):
            return approve_package_command(identity)
        return _unresolvable_remedy(server_config)
    except Exception:  # noqa: BLE001 -- the refusal must still be returned
        return "The provisioning gate could not evaluate this package; it was refused."


def evaluate_provision(
    server_config: ServerConfig,
    identity: PackageIdentity | None,
    *,
    source: ProvisionSource,
    policy: PolicyManager,
) -> ProvisionDecision:
    """May an install for *server_config* spawn? See the module docstring.

    Never raises (rule 8): any exception reading the policy or the approval
    store is a refusal, because a caller that let one propagate could be written
    to treat it as permission.
    """
    try:
        return _evaluate(server_config, identity, source, policy)
    except Exception as exc:  # noqa: BLE001 -- fail closed; see docstring
        logger.warning(
            "Provisioning gate failed closed for %r: %s",
            getattr(server_config, "name", None),
            exc,
        )
        return _deny(
            "not_approved", _fallback_remedy(server_config, identity), identity
        )
