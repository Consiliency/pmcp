"""Typed credential-lookup failures (Consiliency/pmcp#487).

A module of its own, with no pmcp imports at load time, so that every layer
that must contain the failure -- ``config.loader`` (which ``env_store``
imports), ``remote_auth``, the installer, the handlers -- can name it without
an import cycle. ``env_store`` re-exports both names.
"""

from __future__ import annotations

#: The user store exists but cannot be read. A typed answer stops the lookup
#: (and every remaining lookup key) for the server that asked; it never falls
#: back to a stale value, an empty one, a lower layer or a legacy key.
STORE_UNREADABLE = "store_unreadable"

# --- 1Password secret references (Consiliency/pmcp#487 P2, plan 3.1-3.5) ---
#: A reference from a source pmcp never resolves (the process environment,
#: ``~/.env``, a repository file, a tenant store, ...).
REFERENCE_NOT_ALLOWED_HERE = "reference_not_allowed_here"
#: A reference-shaped value outside the supported grammar (plan 3.3).
REFERENCE_UNSUPPORTED = "reference_unsupported"
#: A reference-shaped value that would be sent in a remote header (plan 3.2.7).
REMOTE_REFERENCE_UNSUPPORTED = "remote_reference_unsupported"
#: No service-account token file is present (plan 3.1).
OP_NOT_CONFIGURED = "op_not_configured"
#: A token file exists but fails the descriptor, owner, mode, size or prefix check.
TOKEN_INVALID = "token_invalid"
#: Secret references are POSIX-only.
PLATFORM_UNSUPPORTED = "platform_unsupported"
#: ``op`` failure classes (plan 3.4).
AUTH_FAILED = "auth_failed"
NOT_FOUND = "not_found"
RATE_LIMITED = "rate_limited"
NETWORK = "network"
TIMEOUT = "timeout"
OP_MISSING = "op_missing"
OP_UNTRUSTED = "op_untrusted"
OUTPUT_INVALID = "output_invalid"
#: An opted-in server names an ``OP_*`` variable in its own env (plan 3.2.4).
OP_NAME_NOT_ALLOWED = "op_name_not_allowed"
#: The server's config changed while its references were being resolved, twice
#: (plan 3.5): retryable.
CONFIG_CHANGED = "config_changed"
#: A spawn reached without its references resolved first: a pmcp bug, failed
#: closed rather than spawning with the literal reference.
RESOLUTION_REQUIRED = "resolution_required"

#: Every reason a credential lookup or a reference resolution can fail with.
REASONS = frozenset(
    {
        STORE_UNREADABLE,
        REFERENCE_NOT_ALLOWED_HERE,
        REFERENCE_UNSUPPORTED,
        REMOTE_REFERENCE_UNSUPPORTED,
        OP_NOT_CONFIGURED,
        TOKEN_INVALID,
        PLATFORM_UNSUPPORTED,
        AUTH_FAILED,
        NOT_FOUND,
        RATE_LIMITED,
        NETWORK,
        TIMEOUT,
        OP_MISSING,
        OP_UNTRUSTED,
        OUTPUT_INVALID,
        OP_NAME_NOT_ALLOWED,
        CONFIG_CHANGED,
        RESOLUTION_REQUIRED,
    }
)


class CredentialUnavailableError(Exception):
    """A credential lookup's typed, value-free failure (``credential_unavailable``).

    Raised by ``env_store.credential_value`` when the answer cannot be known
    -- the user store exists but cannot be read -- so the caller refuses the
    spawn rather than starting a server without its credential, or with the
    legacy key's. ``reason`` is one of this module's reason constants; the
    message names the reason and, when it is a plain identifier, the key,
    never a value or a path.

    Contained to the entry that asked (Consiliency/pmcp#342): a config load
    keeps that entry with its typed state and loads every other one.
    """

    def __init__(self, reason: str, key: str | None = None) -> None:
        from pmcp.argument_errors import plain_name

        # Only a known reason constant is ever carried (and shown): a reason
        # computed from config input can never smuggle text into a message.
        self.reason = reason if reason in REASONS else OUTPUT_INVALID
        reason = self.reason
        self.key = key
        where = f" for {plain_name(key)}" if key else ""
        super().__init__(f"credential_unavailable ({reason}){where}")
