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

        self.reason = reason
        self.key = key
        where = f" for {plain_name(key)}" if key else ""
        super().__init__(f"credential_unavailable ({reason}){where}")
