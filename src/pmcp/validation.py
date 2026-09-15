"""Pure input validators for agent-reachable provisioning surfaces.

Kept dependency-free (stdlib only) so it can be imported from
``pmcp.types`` without creating an import cycle through the manifest package.
"""

from __future__ import annotations

import re

# npm allows an optional ``@scope/`` prefix. Both the scope and the name must
# start with a URL-safe character and contain only letters, digits, and
# ``. _ -``. pypi identifiers are a subset of this. A leading ``-`` (which npx
# would treat as a flag → argument injection), whitespace, path separators
# (``../``, ``/``), and shell/URL metacharacters are all rejected.
_PACKAGE_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_PACKAGE_NAME_RE = re.compile(rf"^(?:@{_PACKAGE_SEGMENT}/)?{_PACKAGE_SEGMENT}$")


def is_valid_package_name(name: str) -> bool:
    """Return True if *name* is a safe npm/pypi package identifier.

    Rejects leading dashes (which ``npx`` would treat as flags → argument
    injection), whitespace, path separators, and shell/URL metacharacters, so
    that the value can be safely placed into a list-argv install command such
    as ``["npx", "-y", name]``.
    """
    if not name or len(name) > 214:
        return False
    return bool(_PACKAGE_NAME_RE.fullmatch(name))


def version_separator_index(spec: str) -> int:
    """Index of the ``@`` separating a name from its version, or ``-1``.

    A scoped name's leading ``@`` is not a separator, so the search starts after
    index 0 for a spec that begins with one. Shared by ``parse_package_spec`` and
    the policy schema, which must agree on where a name ends.
    """
    return spec.find("@", 1) if spec.startswith("@") else spec.find("@")


def parse_package_spec(spec: str) -> tuple[str, str | None]:
    """Split ``name[@version]`` into its name and requested version.

    Splits **before** validating: ``is_valid_package_name`` rejects ``pkg@1.2.3``
    because ``@`` is legal only as a scope prefix, so only the name half can be
    validated. Raises ``ValueError`` when the name half is not a valid package
    name, or when a trailing ``@`` names no version -- reading ``pkg@`` as "no
    version requested" would stand in for ``latest``, a version nobody named.

    The version half is returned as *requested*, unvalidated: it may be a
    dist-tag such as ``latest``. Nothing it returns is fit for argv until
    ``is_valid_package_version`` has accepted the *resolved* version.
    """
    at = version_separator_index(spec)
    if at == -1:
        name, version = spec, None
    else:
        name, version = spec[:at], spec[at + 1 :]
        if not version:
            raise ValueError(f"package spec {spec!r} has an empty version")
    if not is_valid_package_name(name):
        raise ValueError(f"package spec {spec!r} does not name a valid package")
    return name, version


# SemVer 2.0.0, spelled with explicit ASCII classes: ``\d`` would admit non-ASCII
# digits. No leading zeros in numeric identifiers, no empty identifiers, and
# nothing outside ``[0-9A-Za-z.+-]`` -- so no whitespace, shell metacharacter,
# path separator or leading ``-``. An allowlist, deliberately: a denylist of
# metacharacters is only as good as the author's memory of them.
_NUMERIC = r"(?:0|[1-9][0-9]*)"
_PRERELEASE_ID = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_BUILD_ID = r"[0-9A-Za-z-]+"
_PACKAGE_VERSION_RE = re.compile(
    rf"{_NUMERIC}\.{_NUMERIC}\.{_NUMERIC}"
    rf"(?:-{_PRERELEASE_ID}(?:\.{_PRERELEASE_ID})*)?"
    rf"(?:\+{_BUILD_ID}(?:\.{_BUILD_ID})*)?"
)
_MAX_PACKAGE_VERSION_LENGTH = 256


def is_valid_package_version(version: str) -> bool:
    """Return True if *version* is one concrete SemVer version, safe for argv.

    The version this checks arrives in a registry response -- semi-trusted
    network data -- and is then composed into ``["npx", "-y", f"{name}@{version}"]``.
    Ranges and dist-tags are refused too: they pin nothing, so an approval of one
    would re-resolve at every spawn.
    """
    if not version or len(version) > _MAX_PACKAGE_VERSION_LENGTH:
        return False
    return _PACKAGE_VERSION_RE.fullmatch(version) is not None


# Environment variables that change how a subsequently spawned subprocess loads
# or executes code. Storing any of these would let a caller achieve code
# execution in the next provisioned server process, so they are rejected
# unconditionally — even if a (malicious or misconfigured) manifest declares
# one as a server's credential variable.
_DANGEROUS_ENV_VARS = frozenset(
    {
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "NODE_OPTIONS",
        "PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONBREAKPOINT",
        "PYTHONINSPECT",
        "BASH_ENV",
        "ENV",
        "IFS",
        "GIT_SSH_COMMAND",
        "GIT_EXTERNAL_DIFF",
        "PERL5LIB",
        "RUBYOPT",
    }
)

# Prefixes covering entire families of code-loading variables (LD_*, DYLD_*,
# PYTHON*), so newly added members are rejected without an explicit listing.
_DANGEROUS_ENV_PREFIXES = ("LD_", "DYLD_", "PYTHON")

# A credential-shaped variable name ends in one of these tokens. Used only as a
# fallback to preserve the explicit ``env_var`` override for servers that have
# no declared credential variable of their own.
_CREDENTIAL_NAME_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*_"
    r"(TOKEN|KEY|SECRET|SECRETS|PASSWORD|CREDENTIAL|CREDENTIALS|PAT|DSN|AUTH)$"
)


def is_dangerous_env_var(name: str) -> bool:
    """Return True if storing *name* could influence subprocess code loading."""
    upper = name.upper()
    if upper in _DANGEROUS_ENV_VARS:
        return True
    return any(upper.startswith(prefix) for prefix in _DANGEROUS_ENV_PREFIXES)


def env_var_allowed(env_var: str, declared_env_var: str | None) -> bool:
    """Return True if *env_var* is safe to store as auth for a server.

    Allows only the server's declared credential variable, or — when the server
    declares none — a credential-shaped name. Anything that could influence code
    loading in a provisioned subprocess is rejected unconditionally, taking
    precedence over the declared variable.
    """
    if not env_var or is_dangerous_env_var(env_var):
        return False
    if declared_env_var is not None:
        return env_var == declared_env_var
    return bool(_CREDENTIAL_NAME_RE.fullmatch(env_var))


# Package-manager and runtime configuration families. npm, corepack, yarn, pnpm
# and bun read these from the environment at spawn, so a value in one of them
# changes WHAT an approved `npx -y name@version` fetches -- the registry it asks
# (`npm_config_registry`), the auth it presents (`NPM_CONFIG__AUTH`) or the
# runtime flags node starts with. Matched case-insensitively: npm reads
# `npm_config_*` in any case.
_PACKAGE_MANAGER_ENV_PREFIXES = (
    "NPM_CONFIG_",
    "NODE_",
    "COREPACK_",
    "YARN_",
    "PNPM_",
    "BUN_",
)


def discovered_env_var_allowed(name: str) -> bool:
    """May a DISCOVERED server declare, or be given, the env var *name*?

    An allowlist, unlike ``env_var_allowed``'s blocklist: a discovered server's
    declared names are agent-chosen, and the declared name is exactly what
    ``auth_connect`` stores and ``build_install_child_env`` injects into the
    pinned spawn. So only a credential-shaped name is admitted, never one
    ``is_dangerous_env_var`` refuses, and never one in a package-manager or
    runtime configuration family -- even when credential-shaped, as
    ``NPM_CONFIG__AUTH`` and ``NODE_AUTH_TOKEN`` are.

    Manifest-backed servers keep ``env_var_allowed``: their declared names are
    shipped, and many (``POSTGRES_URL``) are not credential-shaped.
    """
    if not name or is_dangerous_env_var(name):
        return False
    if name.upper().startswith(_PACKAGE_MANAGER_ENV_PREFIXES):
        return False
    return bool(_CREDENTIAL_NAME_RE.fullmatch(name))
