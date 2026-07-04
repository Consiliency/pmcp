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
