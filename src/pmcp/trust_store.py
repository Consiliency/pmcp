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

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

APPROVED = "approved"
DENIED = "denied"

#: The scope literal recorded by operator-facing project approvals. Both
#: ``pmcp trust approve`` (``cli._run_trust_approve``) and
#: ``config.loader.set_startup_policy``'s carry-forward re-record read it at
#: their record call sites, so the two provably share one constant rather than
#: two literals that could drift.
#: Its value is the historical ``"user"`` scope, kept byte-for-byte: ``scope`` is
#: descriptive metadata that ``is_approved`` ignores when matching (it keys on
#: resolved path + content SHA + decision), so preserving the literal leaves
#: every existing record and its tests unchanged.
PROJECT_SCOPE = "user"

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


#: The project root the gateway was told to SERVE, or ``None`` when nothing set
#: it. ``pmcp serve --project X`` binds this once at startup
#: (``set_active_project_root``); the residency guard then keys on it instead of
#: walking up from ``Path.cwd()``. It exists because the guard's question --
#: "does the store resolve inside the checkout being judged?" -- was silently
#: answered against the *launch directory's* checkout, not the *served* one: a
#: ``pmcp serve --project <checkout>`` run from any other directory therefore
#: failed to refuse a store planted inside that checkout, reopening EC-TRUST-5
#: cwd-dependently (see Consiliency/pmcp#251, #230). A process global, not a
#: threaded argument, because the guard is reached through ``is_approved`` deep
#: inside the config loader; the alternative is a signature change to every
#: public store verb and its ~50 call sites.
_active_project_root: Path | None = None


def set_active_project_root(root: Path | None) -> None:
    """Bind the residency check to the project the gateway is SERVING.

    ``pmcp serve --project X`` (and ``pmcp status --project X``) calls this once,
    before any config is loaded, so the checkout-residency guard in
    ``trust_store_path`` keys on the *served* checkout rather than on wherever
    the process was launched. A store that resolves inside the served checkout
    is then refused no matter the launch directory -- closing the cwd-dependent
    hole.

    Passing ``None`` clears the binding and restores the ``cwd``-derived
    behaviour, which every non-serving caller relies on: the ``pmcp trust``
    verbs legitimately run inside a checkout and MUST keep using ``cwd``, and a
    bare ``pmcp serve`` with no ``--project`` keeps discovering its root from the
    launch directory exactly as before. The value is resolved eagerly so the
    guard compares two already-resolved paths.
    """
    global _active_project_root
    _active_project_root = root.resolve() if root is not None else None


def _enclosing_checkouts(start: Path) -> Iterator[Path]:
    """Every checkout at or above ``start``, resolved, nearest first.

    Walks up from ``start`` via ``find_project_root``, then chains
    ``root.parent`` upward so an intermediate marker -- a subdirectory's own
    ``.mcp.json`` -- cannot stop the walk short of the real checkout. Terminates
    when ``find_project_root`` returns ``None`` (its temp/home guards) or at the
    filesystem root (``parent == enclosing``). Shared by ``_checkout_roots`` (the
    residency guard's served and cwd arms) and by
    ``assert_store_outside_path_checkout`` (the approve verb's guard -- the
    checkout enclosing the path being approved, Consiliency/pmcp#252) so the walk
    cannot drift between them. ``record`` itself stays unguarded, so a store a
    repository *ships* can still be planted in tests and shown refused.
    """
    # Imported here, not at module scope, to break an import cycle introduced
    # when CONSENT landed: pmcp.config.loader now imports pmcp.project_consent,
    # which imports this module. At module scope that made `import
    # pmcp.config.loader` fail outright in a clean interpreter. The residency
    # check only needs the project root at call time.
    from pmcp.config.loader import find_project_root

    current: Path | None = start
    while current is not None:
        enclosing = find_project_root(current)
        if enclosing is None:
            return
        yield enclosing.resolve()
        parent = enclosing.parent
        current = parent if parent != enclosing else None


def _checkout_roots() -> tuple[Path, ...]:
    """Resolved checkout roots the store's residency is judged against.

    The store is refused if it resolves inside **any** of these. They are the
    deduped union of:

    * the project root the gateway was told to SERVE
      (``set_active_project_root``, bound by ``pmcp serve --project X``), kept
      *verbatim* -- a store resident in a served directory that lies inside no
      checkout must still be refused;
    * every checkout ENCLOSING the served root; and
    * every checkout ENCLOSING ``Path.cwd()``.

    Both the served root and cwd are walked UP to the enclosing checkout, not
    judged against the single directory they name. ``find_project_root`` stops at
    the first marker it sees, and a subdirectory of a checkout normally carries
    its own ``.mcp.json`` -- the very payload being judged -- so a single lookup
    would stop at that subdirectory and never reach the real checkout. A store
    planted in the enclosing checkout would then escape the guard while the
    repository self-approves ``<repo>/app/.mcp.json`` -- whether the gateway was
    pointed at the subdirectory with ``serve --project <repo>/app`` (the served
    arm) or simply launched from inside it with a bare ``pmcp serve`` or a
    ``pmcp trust`` verb (the cwd arm). Both are EC-TRUST-5 (subdirectory-
    dependent; Consiliency/pmcp#251, #230); closing one arm and not the other
    leaves the hole open on the everyday developer cwd, so both arms walk up. The
    walk chains ``root.parent`` upward, so an intermediate ``.mcp.json`` between
    the subdirectory and the real checkout cannot hide it.

    The served arm additionally keeps the served root *verbatim* -- its walk
    starts at the served root's *parent*, because the served root's own
    ``.mcp.json`` would otherwise stop that walk at the served root, and a
    project served from outside any checkout has no enclosing checkout yet its
    own store must still be refused. The cwd arm needs no verbatim entry: a bare
    working directory that is not itself a checkout is not a "checkout being
    judged".

    Each source, alone, leaves a hole the others close. Keying only on the served
    root would reopen the launch-checkout hole -- ``serve --project X`` from
    inside a second checkout ``Y`` whose committed store resolves into ``Y`` (the
    dotfiles-symlink shape
    ``test_a_checkout_resident_store_is_refused_through_a_symlink`` treats as
    hostile), carrying an approval for ``X/.mcp.json``; keying only on cwd
    reopens the served-from-elsewhere hole. The union keeps all of them closed,
    and adding roots is strictly more-refusing: it can never turn a refusal into
    an acceptance.

    With nothing served bound -- every ``pmcp trust`` verb, and a bare
    ``pmcp serve`` -- only the cwd walk applies, so ``pmcp trust approve`` run
    inside a checkout keeps working (its store lives in the operator's home,
    outside the checkout).
    """
    roots: list[Path] = []

    def _add(candidate: Path | None) -> None:
        if candidate is None:
            return
        resolved = candidate.resolve()
        if resolved not in roots:
            roots.append(resolved)

    if _active_project_root is not None:
        # The served root itself is always a boundary (a served dir inside no
        # checkout must still be refused); then every checkout enclosing it,
        # walked from the PARENT so the served dir's own `.mcp.json` cannot stop
        # the walk at the served root.
        _add(_active_project_root)
        for enclosing in _enclosing_checkouts(_active_project_root.parent):
            _add(enclosing)

    # The cwd arm walks up too: cwd may itself be a checkout subdirectory
    # carrying the payload `.mcp.json`, so a single lookup would stop there and
    # miss the enclosing checkout (the bare `pmcp serve` and `pmcp trust` verb
    # case, EC-TRUST-5 cwd-subdirectory).
    for enclosing in _enclosing_checkouts(Path.cwd()):
        _add(enclosing)
    return tuple(roots)


def trust_store_path() -> Path:
    """Resolved path of the user-scoped trust store.

    Raises ``TrustStoreError`` if the store would land inside a checkout being
    judged -- the served project root, any checkout enclosing it, and any
    checkout enclosing the current directory (``_checkout_roots``) -- directly,
    or through a symlink anywhere in its path.
    Symlinks are resolved *before* the comparison, which is the only reason a
    planted ``~/.config/pmcp -> ./vendor`` is caught.
    """
    path = (Path.home() / ".config" / "pmcp" / "trust.json").resolve()
    for checkout in _checkout_roots():
        if path.is_relative_to(checkout):
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
    _ensure_store_dir(parent)

    # Write a sibling temp file and rename it into place. `os.replace` is
    # atomic, so a reader never observes a half-written store and an
    # interrupted write leaves the previous one intact rather than truncated.
    fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=".trust-", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as store_file:
            json.dump(payload, store_file, indent=2)
            store_file.write("\n")
            store_file.flush()
            os.fsync(store_file.fileno())
        os.replace(tmp_name, path)
        # fsync the DIRECTORY too, not just the file. `os.replace` is atomic
        # against readers, but the rename itself is only durable once the
        # directory entry is synced -- so a crash right after a revoke can leave
        # the pre-revoke store on disk and resurrect the approval the operator
        # just withdrew. That is the same invariant the lock protects against a
        # race, reached through power loss instead.
        # Best effort, and deliberately so. Directory fsync is unsupported on
        # some platforms and filesystems (macOS returns EINVAL, NFS and some
        # overlay mounts ENOTSUP), and `os.open` on a directory fails outright
        # on Windows. The replace has ALREADY succeeded by this point, so
        # raising here would report a failed `record`/`revoke` for a write that
        # landed -- telling an operator their revoke did not take when it did is
        # worse than losing a durability guarantee the platform cannot give.
        try:
            dir_fd = os.open(parent, os.O_RDONLY)
        except OSError:
            pass
        else:
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
            finally:
                os.close(dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _ensure_store_dir(parent: Path) -> None:
    """Create the store's directory, tightening it only if we created it.

    Both ``_store_lock`` and ``_write_store`` need the directory to exist, and
    whichever runs first is the one that created it. Deciding "did PMCP create
    this?" independently in each is how a 0o700 became a 0o775: the lock's
    ``mkdir`` ran first, so ``_write_store`` then saw an existing directory and
    skipped the chmod, and a fresh install got the umask default instead. One
    helper, one decision. As before, never tighten a directory the user already
    had (mirrors ``env_store.write_env_file``).
    """
    if parent.exists():
        return
    # Create every missing component RESTRICTIVE AT CREATION, one at a time.
    # `mkdir(parents=True, mode=0o700)` is not enough on two counts, both
    # measured: the intermediates are created with the default mode because
    # pathlib deliberately ignores `mode` for parents (mimicking `mkdir -p`), so
    # a freshly created `~/.config` lands 0o775 under umask 002; and creating
    # loosely and tightening afterwards leaves a window in which another account
    # in the user's group can insert a forged `trust.json` that a later
    # `record()` will read and carry forward. `mkdir`'s mode is applied by the
    # kernel at creation and umask can only remove bits, never add them.
    missing: list[Path] = []
    probe = parent
    while not probe.exists():
        missing.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent
    for component in reversed(missing):
        component.mkdir(mode=0o700, exist_ok=True)
        try:
            # Only for a pathological umask that stripped owner bits from the
            # mode above; it can never loosen a directory beyond 0o700.
            os.chmod(component, 0o700)
        except OSError:
            pass


@contextlib.contextmanager
def _store_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive lock for one read-modify-write of the store.

    ``record`` and ``revoke`` both read every record, change one, and write the
    whole set back. Without a lock two concurrent operations interleave into a
    lost update: if ``record`` reads a snapshot containing an approval, a
    ``revoke`` then removes it, and ``record`` writes its stale snapshot back,
    **the revoked approval is silently restored**. An operator's decision to
    withdraw trust must not be undone by a concurrent approve.

    The lock is advisory and POSIX-only; where ``fcntl`` is unavailable this
    degrades to no serialization rather than failing the operation, which
    matches the store's read path -- callers that cannot get a guarantee still
    get correct single-process behaviour.
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
        return is_approved_resolved(Path(path).resolve(), content)
    except Exception:  # noqa: BLE001 -- fail closed; see docstring
        return False


def is_approved_resolved(resolved_path: Path, content: bytes) -> bool:
    """Is ``content`` approved for the ALREADY-RESOLVED canonical ``resolved_path``?

    Identical to ``is_approved`` except that ``resolved_path`` is used as the
    lookup key VERBATIM -- it is never passed through ``.resolve()`` again. This
    is the surface a caller uses once it has pinned a canonical key from an
    ``O_NOFOLLOW``-opened descriptor: re-resolving here would re-follow the path,
    and an attacker who swapped the path to point at a different,
    separately-approved file between the pin and this lookup could then key the
    lookup on that other file. Pinning the key once and reusing it verbatim is
    what closes that path-identity race (see ``config.loader.set_startup_policy``).

    Never raises, for the same fail-closed reason as ``is_approved``.
    """
    try:
        records = _read_store(trust_store_path())
        digest = hashlib.sha256(content).hexdigest()
        for rec in records:
            if rec.absolute_path == resolved_path:
                return rec.decision == APPROVED and rec.content_sha256 == digest
        return False
    except Exception:  # noqa: BLE001 -- fail closed; see docstring
        return False


def assert_store_outside_path_checkout(path: Path) -> None:
    """Refuse if the store resolves inside a checkout enclosing ``path``.

    ``trust_store_path`` already refuses a store resident in the served root or
    the cwd checkout, but ``pmcp trust approve`` run from OUTSIDE the approved
    file's checkout would otherwise write into a store resident in THAT checkout
    and print "Approved" -- and ``serve --project`` then refuses the same store
    forever. The approve verb calls this so approve and serve agree
    (Consiliency/pmcp#252). Raises ``TrustStoreError`` naming the checkout; a
    store outside every enclosing checkout is left alone. This guards the verb,
    not ``record`` itself: ``record`` stays a primitive, so a store a repository
    *ships* (never written through the verb) can still be planted and shown to
    be refused on the read side.
    """
    store = trust_store_path()
    approved = Path(path).resolve()
    for checkout in _enclosing_checkouts(approved.parent):
        if store.is_relative_to(checkout):
            raise TrustStoreError(
                f"Trust store {store} resolves inside the checkout at {checkout} "
                f"that contains {approved}. A checkout-resident store lets a "
                "repository approve its own content; move it under a home "
                "directory outside the repository."
            )


def record(path: Path, content: bytes, scope: str, decision: str) -> TrustRecord:
    """Record a decision about ``path``'s exact ``content``, replacing any prior.

    ``decision`` must be one of ``DECISIONS``; anything else raises
    ``ValueError`` rather than being stored, so a typo cannot become a decision
    that no reader recognises. Raises ``TrustStoreError`` if the store is
    unusable (checkout-resident, unreadable, or corrupt) -- writing over a store
    that could not be read would silently discard existing decisions.
    """
    return record_resolved(Path(path).resolve(), content, scope, decision)


def record_resolved(
    resolved_path: Path, content: bytes, scope: str, decision: str
) -> TrustRecord:
    """Record a decision keyed on the ALREADY-RESOLVED canonical ``resolved_path``.

    Identical to ``record`` -- same decision/scope validation, the same
    residency/lock/validation guard via ``trust_store_path``, the same
    replace-any-prior-for-this-key semantics -- except that ``resolved_path`` is
    stored VERBATIM as the record's key, never re-resolved. A caller that pinned
    a canonical key from an ``O_NOFOLLOW``-opened descriptor records against that
    exact key, so the approval it writes cannot be redirected to a different file
    by a path swapped underneath a second ``.resolve()`` (path-identity race; see
    ``config.loader.set_startup_policy``).
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
        absolute_path=resolved_path,
        content_sha256=hashlib.sha256(content).hexdigest(),
        scope=scope,
        decision=decision,
        recorded_at=datetime.now(timezone.utc),
    )

    # Read and write under one lock: a concurrent revoke between the read and
    # the write would otherwise be silently undone by this stale snapshot.
    with _store_lock(store):
        records = [
            rec
            for rec in _read_store(store)
            if rec.absolute_path != entry.absolute_path
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
    target = Path(path).resolve()

    with _store_lock(store):
        records = _read_store(store)
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
