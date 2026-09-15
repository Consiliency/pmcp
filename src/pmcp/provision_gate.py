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


def _spec_name(spec: str) -> str | None:
    try:
        name, _version = parse_package_spec(spec)
    except ValueError:
        return None
    return name


def _manifest_package_names(server_config: ServerConfig) -> list[str]:
    """The npm package names a manifest config names, in order, once each.

    Read from three places: the ``package`` field; the package slot of
    ``args`` when the command is npx; and the package slot of every platform
    ``install`` argv whose executable is npx. The slot is found by POSITION, as
    `_runs_exactly` finds it -- never by what an argument looks like, so an
    argument the server receives after its package is not mistaken for one. A
    slot that is not a valid package spec names nothing. Only a manifest
    config is read this way: its fields are shipped, not agent-composed.
    """
    specs: list[str] = []
    if server_config.package:
        specs.append(server_config.package)
    if _is_npx(server_config.command):
        slot = _package_slot(list(server_config.args))
        if slot is not None:
            specs.append(slot)
    for argv in server_config.install.values():
        if argv and _is_npx(argv[0]):
            slot = _package_slot(list(argv[1:]))
            if slot is not None:
                specs.append(slot)
    names: list[str] = []
    for spec in specs:
        name = _spec_name(spec)
        if name is not None and name not in names:
            names.append(name)
    return names


def _denied_manifest_package(
    server_config: ServerConfig, policy: PolicyManager
) -> str | None:
    """The first package name of a manifest config that policy denies, if any.

    A policy object without the name-level predicate answers ``"unspecified"``
    for every name, which is exactly what it answered before names were read:
    `PolicyManager` always has it, and a predicate that is present and raises
    still fails closed through rule 8.
    """
    evaluate_name = getattr(policy, "evaluate_package_name_policy", None)
    if evaluate_name is None:
        return None
    for name in _manifest_package_names(server_config):
        if evaluate_name(name) == "denied":
            return name
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
        denied_name = _denied_manifest_package(server_config, policy)
        if denied_name is not None:
            return _deny("denied", _denied_remedy(denied_name), identity)

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
