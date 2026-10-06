"""User-scoped record of which packages, at which resolved versions, may run.

Consiliency/pmcp#230 (IF-0-PKGID-2). The provisioning gate
(`pmcp.provision_gate`) consults this store before `npx` is allowed to fetch
and execute a package an agent discovered. A record names an identity --
``(registry, name, resolved_version)`` -- never a server name, because the
server name is the agent-chosen label S-01 showed policy could be tricked by.

**A version bump is a new identity.** One record per
``(registry, name, resolved_version)``; approving ``pkg@1.2.3`` says nothing
about ``pkg@1.2.4``. That is the entire point of pinning at registration.

The store's properties are TRUST's (`pmcp.trust_store`), taken rather than
re-derived, and each one exists because its absence was exploitable there:

* **Residency is consumed, not re-litigated.** The file sits beside the trust
  store, at ``trust_store_path().parent / "package_approvals.json"``, so the
  rule that a checkout may not host its own approvals applies unchanged.
* **Every read failure is a refusal.** ``is_package_approved`` never raises; a
  corrupt, unreadable, misplaced or tampered store answers ``False``. The
  operator verbs raise loudly instead, because an empty listing would be a
  misleading way to report a broken store.
* **Writes are atomic, durable where the platform allows, and serialized.** A
  concurrent approve must not write back a snapshot that resurrects an
  approval a revoke just removed.
* **Nothing is group-writable, even transiently.** Missing directories are
  created ``0o700`` at creation, one component at a time.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any


from pmcp import atomic_write as _atomic_write_module
from pmcp.atomic_write import (
    atomic_write,
    falls_back_to_pathname,
    is_absent,
    make_store_dirs,
    open_final_directory,
    resolve_write_target,
)
from pmcp.trust_store import (
    TrustStoreError,
    refuse_checkout_resident,
    trust_store_path,
)
from pmcp.validation import (
    is_valid_package_name,
    is_valid_package_version,
    matches_package_version_grammar,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Annotation only: `pmcp.manifest`'s package `__init__` imports the loader
    # and installer, which this store has no business pulling in at runtime.
    from pmcp.manifest.package_identity import PackageIdentity

APPROVED = "approved"
DENIED = "denied"

#: Closed, as in the trust store. ``"denied"`` is readable -- a record carrying
#: it refuses -- but reserved: no shipped verb writes it (TRUST amendment 1).
DECISIONS = frozenset({APPROVED, DENIED})

PACKAGE_APPROVALS_FILENAME = "package_approvals.json"

_STORE_VERSION = 1


class PackageApprovalError(TrustStoreError):
    """The package approval store is unusable, or cannot be parsed.

    A `TrustStoreError`, so `pmcp trust` reports it the same way it reports a
    broken trust store.
    """


@dataclass(frozen=True)
class PackageApproval:
    """One decision about one package at one resolved version."""

    registry: str
    name: str
    resolved_version: str
    integrity: str | None
    decision: str
    recorded_at: datetime


def package_approvals_path() -> Path:
    """Where the store lives. Raises ``TrustStoreError`` if checkout-resident.

    The directory is the trust store's, whose residency ``trust_store_path``
    checks. Writes follow a symlinked store's final link chain
    (``pmcp.atomic_write``), and reads always did, so where that chain lands is
    checked too -- found by the writer's own chain follower, judged by file
    identity -- or a ``package_approvals.json`` linked into a judged checkout
    would be a checkout-resident store reached through its final component.
    """
    path = trust_store_path().parent / PACKAGE_APPROVALS_FILENAME
    try:
        if is_absent(path.parent):
            # A fresh install: no store, no link to follow. Judge where it will
            # be created (the residency walk steps up across plain names).
            fd, target = None, os.fspath(path)
        else:
            fd = None
            if _atomic_write_module._DIR_FD_SUPPORTED:
                # The directory the writer will actually write in, reached hop
                # by hop (no pathname grows), judged by identity from its
                # descriptor -- unless the shared rule says to go by pathname
                # (no O_PATH and a directory that may be searched, not listed).
                try:
                    fd, _name = open_final_directory(path)
                except OSError as exc:
                    if not falls_back_to_pathname(exc):
                        raise
            if fd is None:
                target = resolve_write_target(path)
    except OSError as exc:
        raise TrustStoreError(
            f"Cannot resolve {path.name}: {os.strerror(exc.errno) if exc.errno else exc}"
        ) from exc
    try:
        refuse_checkout_resident(
            target if fd is None else path, "Package approvals", dir_fd=fd
        )
    finally:
        if fd is not None:
            os.close(fd)
    return path


def _require_identity_fields(registry: Any, name: Any, version: Any) -> None:
    """Refuse a record whose values could not safely appear in argv.

    Applied on write AND on read: a stored name or version is later composed
    into the pinned install argv and printed to an operator, so a hand-edited
    store that plants ``1.0.0;$(id)`` is corruption, not a record.
    """
    if not isinstance(registry, str) or not registry:
        raise ValueError(
            f"package registry must be a non-empty string, not {registry!r}"
        )
    if not isinstance(name, str) or not is_valid_package_name(name):
        raise ValueError(f"not a valid package name: {name!r}")
    if not isinstance(version, str) or not is_valid_package_version(version):
        raise ValueError(
            f"not one exact package version: {version!r} "
            "(ranges and dist-tags pin nothing)"
        )


def _decode(entry: Any) -> PackageApproval:
    """Decode one stored entry, refusing anything malformed."""
    if not isinstance(entry, dict):
        raise PackageApprovalError("Package approval entry is not an object")
    try:
        registry = entry["registry"]
        name = entry["name"]
        version = entry["resolved_version"]
        integrity = entry["integrity"]
        decision = entry["decision"]
        recorded_at = entry["recorded_at"]
    except KeyError as exc:
        raise PackageApprovalError(f"Package approval entry is missing {exc}") from exc

    try:
        _require_identity_fields(registry, name, version)
    except ValueError as exc:
        raise PackageApprovalError(f"Invalid package approval entry: {exc}") from exc
    if integrity is not None and not isinstance(integrity, str):
        raise PackageApprovalError("Package approval integrity is not a string")
    if decision not in DECISIONS:
        raise PackageApprovalError(f"Unknown package decision: {decision!r}")
    try:
        parsed_at = datetime.fromisoformat(str(recorded_at))
    except ValueError as exc:
        raise PackageApprovalError(
            f"Unparseable package approval timestamp: {exc}"
        ) from exc

    return PackageApproval(
        registry=registry,
        name=name,
        resolved_version=version,
        integrity=integrity,
        decision=decision,
        recorded_at=parsed_at,
    )


def _read_store(path: Path) -> list[PackageApproval]:
    """Every record in the store, or raise ``PackageApprovalError``.

    An absent store is empty: an operator who has approved nothing is the
    normal starting state. A stale record (``_is_stale_record``) is left out.
    """
    return _read_store_and_stale(path)[0]


def _read_store_and_stale(
    path: Path,
) -> tuple[list[PackageApproval], list[tuple[str, str]]]:
    """``_read_store``, plus the ``(name, version)`` of each stale record left out."""
    if is_absent(path):  # only ENOENT/ENOTDIR; ELOOP, EACCES ... are raised
        return [], []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PackageApprovalError(
            f"Cannot read package approvals {path}: {exc}"
        ) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise PackageApprovalError(
            f"Cannot parse package approvals {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise PackageApprovalError(f"Package approvals {path} is not a JSON object")
    entries = data.get("records")
    if not isinstance(entries, list):
        raise PackageApprovalError(f"Package approvals {path} has no records list")
    records: list[PackageApproval] = []
    stale: list[tuple[str, str]] = []
    for entry in entries:
        if _is_stale_record(entry):
            # Recorded before the exact-version check followed npm's version
            # rules (tarball suffix, core-part bound): it approves nothing now. Leaving it out (rather than
            # refusing the whole store) keeps every other approval working; the
            # next write drops it. Every OTHER invalid record -- including one
            # with a tarball-shaped version and any other defect -- still
            # fails the store in `_decode`.
            logger.warning(
                "Ignoring package approval %r@%r in %s: npm does not read this "
                "version as a registry version (a tarball spec or a dist-tag), "
                "so it approves nothing; re-approve the package at a registry "
                "version",
                entry["name"],
                entry["resolved_version"],
                path,
            )
            stale.append((entry["name"], entry["resolved_version"]))
            continue
        records.append(_decode(entry))
    return records, stale


def _is_stale_record(entry: Any) -> bool:
    """A record that is valid in every respect except npm's version rules.

    Its version must match the SemVer grammar (what the store accepted before
    those rules) and be refused now -- npm reads it as a tarball spec or a
    dist-tag, not a registry version; with the version replaced, the whole
    entry must decode. Anything else is corruption, left to ``_decode``.
    """
    if not isinstance(entry, dict):
        return False
    version = entry.get("resolved_version")
    if (
        not isinstance(version, str)
        or not matches_package_version_grammar(version)
        or is_valid_package_version(version)
    ):
        return False
    try:
        _decode({**entry, "resolved_version": "0.0.0"})
    except PackageApprovalError:
        return False
    return True


def _ensure_store_dir(parent: Path) -> None:
    """Create every missing directory component ``0o700`` at creation.

    Same reasoning as `trust_store._ensure_store_dir`: `mkdir(parents=True)`
    ignores `mode` for intermediates, and create-then-chmod leaves a window in
    which another account in the group can plant a forged store. A directory
    that already exists is never tightened -- it is the operator's.
    """
    # Walked as the kernel would; only the plain tail of directories that do
    # not exist yet is created, each at 0o700 (restrictive AT CREATION),
    # relative to the last directory the walk reached. Never treated as absent
    # and created when the path cannot be resolved for another reason.
    for created in make_store_dirs(parent):
        with contextlib.suppress(OSError):
            # Only for a umask that stripped owner bits; never loosens.
            os.chmod(created, 0o700)


def _write_store(path: Path, records: list[PackageApproval]) -> None:
    """Replace the store atomically, mode ``0o600``."""
    payload = {
        "version": _STORE_VERSION,
        "records": [
            {
                "registry": rec.registry,
                "name": rec.name,
                "resolved_version": rec.resolved_version,
                "integrity": rec.integrity,
                "decision": rec.decision,
                "recorded_at": rec.recorded_at.isoformat(),
            }
            for rec in records
        ],
    }
    parent = path.parent
    _ensure_store_dir(parent)

    # Atomic, mode 0600, through a symlinked store rather than over it, with the
    # directory fsync-ed best effort: a crash after a revoke must not resurrect
    # the withdrawn approval, but a platform that cannot fsync a directory must
    # not turn a completed write into an error (pmcp.atomic_write).
    text = json.dumps(payload, indent=2) + "\n"
    # confine_to=None: beside the trust store, resolved and refused if
    # checkout-resident by package_approvals_path().
    atomic_write(
        path,
        text.encode("utf-8"),
        confine_to=None,
        mode=0o600,
        prefix=".package-approvals-",
    )


@contextlib.contextmanager
def _store_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock for one read-modify-write.

    Without it, an approve that read before a revoke wrote puts the revoked
    record back. POSIX-only; elsewhere it degrades to no serialization, which
    is still correct for a single process.
    """
    _ensure_store_dir(path.parent)
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        yield
        return
    fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _key(registry: str, name: str, version: str) -> tuple[str, str, str]:
    return (registry, name, version)


def approve_package(identity: PackageIdentity) -> PackageApproval:
    """Record an approval for *identity*, replacing any earlier decision for it.

    Raises ``ValueError`` for an identity whose name or version is not safe for
    argv, and ``TrustStoreError`` if the store is unusable -- writing over a
    store that could not be read would discard the decisions in it.
    """
    _require_identity_fields(
        identity.registry, identity.name, identity.resolved_version
    )
    entry = PackageApproval(
        registry=identity.registry,
        name=identity.name,
        resolved_version=identity.resolved_version,
        integrity=identity.integrity or None,
        decision=APPROVED,
        recorded_at=datetime.now(timezone.utc),
    )
    target = _key(entry.registry, entry.name, entry.resolved_version)
    store = package_approvals_path()
    with _store_lock(store):
        records = [
            rec
            for rec in _read_store(store)
            if _key(rec.registry, rec.name, rec.resolved_version) != target
        ]
        records.append(entry)
        _write_store(store, records)
    return entry


def is_package_approved(identity: PackageIdentity) -> bool:
    """Has an operator approved exactly this identity?

    ``True`` only for an ``"approved"`` record with the same registry, name and
    resolved version -- and, when both the record and *identity* carry an
    integrity digest, the same digest: a registry that now reports different
    bytes for a version the operator approved is not describing what they
    approved.

    Never raises. Every I/O, parse, residency and validation failure is
    ``False``, because a caller reading an exception as permission would be
    granted it by a corrupt file.
    """
    try:
        target = _key(identity.registry, identity.name, identity.resolved_version)
        for rec in _read_store(package_approvals_path()):
            if _key(rec.registry, rec.name, rec.resolved_version) != target:
                continue
            if rec.decision != APPROVED:
                return False
            if (
                rec.integrity is not None
                and identity.integrity is not None
                and rec.integrity != identity.integrity
            ):
                return False
            return True
        return False
    except Exception:  # noqa: BLE001 -- fail closed; see docstring
        return False


def revoke_package(name: str, version: str | None = None) -> bool:
    """Drop the records for *name* (at *version*, or at every version).

    Returns whether anything was removed. Revoking returns the identity to
    *absent*, which the gate already refuses. A stale record
    (``_is_stale_record``) matching *name* (and *version*) counts as removed;
    like every write, this one also drops stale records for other names. Raises ``TrustStoreError`` if the
    store is unusable.
    """
    store = package_approvals_path()
    with _store_lock(store):
        records, stale = _read_store_and_stale(store)
        kept = [
            rec
            for rec in records
            if not (
                rec.name == name
                and (version is None or rec.resolved_version == version)
            )
        ]
        stale_hit = any(
            stale_name == name and (version is None or stale_version == version)
            for stale_name, stale_version in stale
        )
        if len(kept) == len(records) and not stale_hit:
            return False
        _write_store(store, kept)  # a stale record is never written back
    return True


def list_package_approvals() -> list[PackageApproval]:
    """Every record, in insertion order. Raises ``TrustStoreError`` if unusable."""
    return _read_store(package_approvals_path())
