"""User-scoped store of approval records for content PMCP is about to trust.

This module publishes the contract downstream consent and package-identity work
codes against (`IF-0-TRUST-1`, Consiliency/pmcp#230). It wires no caller: it
records decisions and answers one question, `is_approved`.

Three properties are load-bearing, and each exists because the obvious
alternative is exploitable:

* **The predicate judges bytes, not a path.** ``is_approved(path, content)``
  hashes the bytes the caller is *about to apply* and never reads ``path``. A
  path-based check is defeated by swapping the file between the check and the
  use; passing the bytes closes that window by construction.
* **Every failure is a refusal.** A corrupt, unreadable, or missing store makes
  ``is_approved`` return ``False``. It never raises to a caller that might read
  an exception as permission. The other verbs *do* raise, loudly: they are
  operator-facing, and a silent failure there would hide a broken store.
* **The store may not live inside the checkout being judged.** A
  checkout-resident store lets a repository ship an approval for its own
  content, which would make "absence is never assent" decorative. The check
  resolves symlinks first, so a symlinked ``~/.config/pmcp`` pointing into the
  repository is refused too.

That residency rule binds *where the store file lives*, never which paths may be
recorded. ``record(path, ...)`` takes the path of the file being approved, which
is normally inside a checkout -- that is the whole use case.

The store sits beside the credential store (``env_store.resolve_scope_path``) at
``~/.config/pmcp/trust.json``, mode ``0o600``, and holds exactly one record per
absolute path: re-approving replaces, it does not append a history.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmcp.config.loader import find_project_root

APPROVED = "approved"
DENIED = "denied"

#: The closed decision vocabulary. Anything else is rejected at ``record`` time
#: rather than stored: a store that can hold a decision no reader understands
#: is a store whose meaning depends on the reader.
DECISIONS = frozenset({APPROVED, DENIED})

_STORE_VERSION = 1


class TrustStoreError(RuntimeError):
    """The trust store cannot be used as configured, or cannot be parsed."""


@dataclass(frozen=True)
class TrustRecord:
    """One decision about one file's exact content."""

    absolute_path: Path
    content_sha256: str
    scope: str
    decision: str
    recorded_at: datetime


def _checkout_root() -> Path | None:
    """Resolved root of the checkout the process is working in, if any."""
    root = find_project_root(Path.cwd())
    return root.resolve() if root else None


def trust_store_path() -> Path:
    """Resolved path of the user-scoped trust store.

    Raises ``TrustStoreError`` if the store would land inside the current
    checkout -- directly, or through a symlink anywhere in its path. Symlinks
    are resolved *before* the comparison, which is the only reason a planted
    ``~/.config/pmcp -> ./vendor`` is caught.
    """
    path = (Path.home() / ".config" / "pmcp" / "trust.json").resolve()
    checkout = _checkout_root()
    if checkout is not None and path.is_relative_to(checkout):
        raise TrustStoreError(
            f"Trust store {path} resolves inside the checkout at {checkout}. "
            "A checkout-resident store lets a repository approve its own "
            "content; move it under a home directory outside the repository."
        )
    return path


def _decode(entry: Any) -> TrustRecord:
    """Decode one stored entry, refusing anything malformed.

    Strict on purpose: a half-understood entry is rejected as a whole-store
    parse failure, which every caller turns into a refusal.
    """
    if not isinstance(entry, dict):
        raise TrustStoreError("Trust store entry is not an object")
    try:
        absolute_path = entry["absolute_path"]
        content_sha256 = entry["content_sha256"]
        scope = entry["scope"]
        decision = entry["decision"]
        recorded_at = entry["recorded_at"]
    except KeyError as exc:
        raise TrustStoreError(f"Trust store entry is missing {exc}") from exc

    if decision not in DECISIONS:
        raise TrustStoreError(f"Unknown trust decision: {decision!r}")

    try:
        parsed_at = datetime.fromisoformat(str(recorded_at))
    except ValueError as exc:
        raise TrustStoreError(f"Unparseable trust timestamp: {exc}") from exc

    return TrustRecord(
        absolute_path=Path(str(absolute_path)),
        content_sha256=str(content_sha256),
        scope=str(scope),
        decision=str(decision),
        recorded_at=parsed_at,
    )


def _read_store(path: Path) -> list[TrustRecord]:
    """Read every record from the store, or raise ``TrustStoreError``.

    An absent store is empty, not an error -- a user who has approved nothing is
    the normal starting state.
    """
    if not path.exists():
        return []

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TrustStoreError(f"Cannot read trust store {path}: {exc}") from exc

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise TrustStoreError(f"Cannot parse trust store {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise TrustStoreError(f"Trust store {path} is not a JSON object")
    entries = data.get("records")
    if not isinstance(entries, list):
        raise TrustStoreError(f"Trust store {path} has no records list")

    return [_decode(entry) for entry in entries]


def _write_store(path: Path, records: list[TrustRecord]) -> None:
    """Write the store, creating it mode 0o600.

    Mirrors ``env_store.write_env_file``: tighten only a directory PMCP itself
    created, never a pre-existing one.
    """
    payload = {
        "version": _STORE_VERSION,
        "records": [
            {
                "absolute_path": str(rec.absolute_path),
                "content_sha256": rec.content_sha256,
                "scope": rec.scope,
                "decision": rec.decision,
                "recorded_at": rec.recorded_at.isoformat(),
            }
            for rec in records
        ],
    }

    parent = path.parent
    parent_created = not parent.exists()
    parent.mkdir(parents=True, exist_ok=True)
    if parent_created:
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as store_file:
        json.dump(payload, store_file, indent=2)
        store_file.write("\n")


def is_approved(path: Path, content: bytes) -> bool:
    """Is ``content`` approved to be applied as ``path``?

    ``content`` is the bytes the caller is about to use, not a promise about
    what is on disk; this function never reads ``path``. The answer is ``True``
    only for a record whose decision is ``"approved"`` AND whose digest matches
    these exact bytes -- a ``"denied"`` record, a stale digest, and no record at
    all are all ``False``.

    Never raises. Every I/O, parse, and configuration failure answers ``False``,
    because a caller that treated an exception as permission would be granted
    trust by a corrupt file.
    """
    try:
        records = _read_store(trust_store_path())
        target = Path(path).resolve()
        digest = hashlib.sha256(content).hexdigest()
        for rec in records:
            if rec.absolute_path == target:
                return rec.decision == APPROVED and rec.content_sha256 == digest
        return False
    except Exception:  # noqa: BLE001 -- fail closed; see docstring
        return False


def record(path: Path, content: bytes, scope: str, decision: str) -> TrustRecord:
    """Record a decision about ``path``'s exact ``content``, replacing any prior.

    ``decision`` must be one of ``DECISIONS``; anything else raises
    ``ValueError`` rather than being stored, so a typo cannot become a decision
    that no reader recognises. Raises ``TrustStoreError`` if the store is
    unusable (checkout-resident, unreadable, or corrupt) -- writing over a store
    that could not be read would silently discard existing decisions.
    """
    if decision not in DECISIONS:
        raise ValueError(
            f"Unsupported trust decision: {decision!r} "
            f"(expected one of {sorted(DECISIONS)})"
        )
    if not scope:
        raise ValueError("Trust scope must be a non-empty string")

    store = trust_store_path()
    entry = TrustRecord(
        absolute_path=Path(path).resolve(),
        content_sha256=hashlib.sha256(content).hexdigest(),
        scope=scope,
        decision=decision,
        recorded_at=datetime.now(timezone.utc),
    )

    records = [
        rec for rec in _read_store(store) if rec.absolute_path != entry.absolute_path
    ]
    records.append(entry)
    _write_store(store, records)
    return entry


def revoke(path: Path) -> bool:
    """Drop any record for ``path``. Returns whether one was removed.

    Revoking returns the path to *absent*, which is not the same as recording a
    ``"denied"`` decision -- use ``record(..., decision="denied")`` to say no
    explicitly. Raises ``TrustStoreError`` if the store is unusable.
    """
    store = trust_store_path()
    records = _read_store(store)
    target = Path(path).resolve()

    kept = [rec for rec in records if rec.absolute_path != target]
    if len(kept) == len(records):
        return False

    _write_store(store, kept)
    return True


def list_records() -> list[TrustRecord]:
    """Every record in the store, in insertion order.

    Raises ``TrustStoreError`` if the store is unusable. This surface is
    operator-facing -- unlike ``is_approved``, an empty list here would be a
    misleading way to report a broken store, and it cannot be misread as
    permission by anything.
    """
    return _read_store(trust_store_path())
