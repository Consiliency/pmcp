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
import errno
import hashlib
import json
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pmcp.home_identity import (
    enclosing_checkouts,
    has_checkout_marker,
    is_operator_owned,
)
from pmcp.atomic_write import (
    atomic_write,
    falls_back_to_pathname,
    is_absent,
    make_store_dirs,
    open_directory,
    resolve_write_target,
)

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


#: The directory pmcp was LAUNCHED in, captured once -- at import, or at the
#: first residency judgement after the test-only reset -- as ``(absolute path,
#: (st_dev, st_ino))``, and never re-read from the working directory
#: (Consiliency/pmcp#372 round 18, board round 17 codex F001: re-reading the
#: cwd on every call let a later ``chdir`` drop the launch checkout from the
#: guard, so approvals stored inside it started granting the served project's
#: config and packages).
_LAUNCH_DIRECTORY: tuple[Path, tuple[int, int] | None] | None = None


def _capture_launch_directory() -> tuple[Path, tuple[int, int] | None]:
    global _LAUNCH_DIRECTORY
    if _LAUNCH_DIRECTORY is None:
        try:
            where = Path(os.getcwd())
            status = os.stat(where)
            _LAUNCH_DIRECTORY = (where, (status.st_dev, status.st_ino))
        except OSError:
            _LAUNCH_DIRECTORY = (Path(os.path.abspath(os.curdir)), None)
    return _LAUNCH_DIRECTORY


def reset_launch_directory() -> None:
    """Forget the captured launch directory. **Test-only seam.**"""
    global _LAUNCH_DIRECTORY
    _LAUNCH_DIRECTORY = None


def _is_checkout(root: Path) -> bool:
    """Does ``root`` carry a checkout marker?"""
    return has_checkout_marker(root)


def _boundaries(root: Path) -> list[Path]:
    """The residency boundaries one root contributes -- the same rule for every root.

    Every checkout enclosing ``root`` (``root`` itself included when it carries
    a marker). With none, ``root`` itself -- a plain directory pmcp reads a
    project from is that project's boundary -- unless it is the home directory
    or an ancestor of it, where the operator's own store lives (Consiliency/pmcp
    #372 round 18, board round 17 grok F001: a markerless bound root added
    nothing, so a store linked into it approved its own packages and policy,
    while ``--project`` on the same directory refused it).
    """
    enclosing = list(_enclosing_checkouts(root))
    if enclosing:
        return enclosing
    # By FILE IDENTITY (pmcp.home_identity), never path spelling: home and its
    # physical ancestors, plus the default store's own real directories
    # (home's .config and .config/pmcp), are the operator's (Consiliency/pmcp
    # #372 round 19, board round 18 codex F001 and claude N-1).
    if is_operator_owned(root):
        return []
    return [root]


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
    """Every checkout at or above ``start``, resolved, nearest first -- up to ``/``.

    The RESIDENCY walk, deliberately not project discovery
    (Consiliency/pmcp#372 round 20, boards round 19 grok/codex F001):
    ``find_project_root`` stops at the home directory and the temp root, so a
    checkout that ENCLOSES the home directory was invisible here -- with HOME
    at ``<checkout>/home``, a repository-shipped ``HOME/.config/pmcp/
    trust.json`` approved ``HOME/app/.mcp.json``. This walk goes all the way to
    ``/``, through and above home, on the kernel-resolved path. The one
    directory it never counts is the home directory ITSELF (by identity): a
    home kept under version control -- a dotfiles repository -- is the
    operator's, and counting it would refuse every operator store. Shared by
    ``_checkout_roots`` and ``assert_store_outside_path_checkout`` so the walk
    cannot drift between them.
    """
    # The walk lives in pmcp.home_identity (Consiliency/pmcp#372 round 21),
    # where operator ownership is decided with it.
    yield from enclosing_checkouts(start)


def _checkout_roots(also: tuple[Path, ...] = ()) -> tuple[Path, ...]:
    """Resolved checkout roots the store's residency is judged against.

    The store is refused if it resolves inside **any** of these. They are the
    deduped union of:

    * the project root the gateway was told to SERVE
      (``set_active_project_root``, bound by ``pmcp serve --project X``), kept
      *verbatim* -- a store resident in a served directory that lies inside no
      checkout must still be refused;
    * every checkout ENCLOSING the served root; and
    * every checkout ENCLOSING the directory pmcp was LAUNCHED in (captured
      once; Consiliency/pmcp#372 round 18); and
    * the file being approved and the reader's bound project (``also``).

    Every root follows one rule (``_boundaries``): its enclosing checkouts, else
    the root itself unless it is the home directory or above.

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

    # ONE rule for every root the guard receives (``_boundaries``): its
    # enclosing checkouts, else the root itself unless it is the home
    # directory or above it (Consiliency/pmcp#372 round 18).
    #
    # The served root, and the checkouts enclosing it. Walking UP matters: a
    # subdirectory of a checkout normally carries its own `.mcp.json`, so a
    # single lookup would stop there and never reach the real checkout.
    if _active_project_root is not None:
        for boundary in _boundaries(_active_project_root):
            _add(boundary)
    # The LAUNCH directory, captured once and never re-read from the cwd
    # (EC-TRUST-5 cwd-subdirectory; round 18 codex F001).
    for boundary in _boundaries(_capture_launch_directory()[0]):
        _add(boundary)
    # The roots pmcp is reading project inputs from for THIS judgement
    # (round 17, board round 16 F001): the directory of the file being
    # approved and the project root its reader is bound to. A gateway built in
    # A that later runs with its cwd in B still judges A's files against A.
    # Adding roots only refuses more.
    for root in also:
        for boundary in _boundaries(root):
            _add(boundary)
    return tuple(roots)


def trust_store_path(*, also: tuple[Path, ...] = ()) -> Path:
    """Resolved path of the user-scoped trust store.

    Raises ``TrustStoreError`` if the store would land inside a checkout being
    judged -- the served project root, any checkout enclosing it, and any
    checkout enclosing the current directory (``_checkout_roots``) -- directly,
    or through a symlink anywhere in its path.
    Symlinks are resolved *before* the comparison, which is the only reason a
    planted ``~/.config/pmcp -> ./vendor`` is caught.
    """
    path = (Path.home() / ".config" / "pmcp" / "trust.json").resolve()
    refuse_checkout_resident(path, "Trust store", also=also)
    return path


def refuse_checkout_resident(
    path: Path | str,
    label: str,
    *,
    dir_fd: int | None = None,
    also: tuple[Path, ...] = (),
) -> None:
    """Raise ``TrustStoreError`` if ``path``'s directory lies inside a judged checkout.

    By FILE IDENTITY, never path strings (``//x`` and ``/x`` are one directory
    but compare unequal), and FAIL CLOSED: if residency cannot be established
    for any reason, the store is refused. ``dir_fd``, when given, is an open
    descriptor of the store's directory (the writer's own chain follower hands
    one over), judged instead of ``path``'s. See :func:`_resident_checkout`.
    """
    name = os.path.basename(os.fspath(path))
    try:
        checkout = _resident_checkout(path, _checkout_roots(also), dir_fd=dir_fd)
    except OSError as exc:
        raise TrustStoreError(
            f"{label} {name}: cannot establish that it lies outside every "
            f"checkout being judged ({os.strerror(exc.errno) if exc.errno else exc}); "
            "refusing it."
        ) from exc
    if checkout is not None:
        # A boundary is a checkout when it carries a project marker; otherwise
        # it is a plain directory pmcp reads a project from (round 18).
        kind = "checkout" if _is_checkout(checkout) else "directory"
        raise TrustStoreError(
            f"{label} {name} resolves inside the {kind} at {checkout}. "
            f"A store inside a project's {kind} lets it approve its own "
            "content; move it under a home directory outside the project."
        )


#: More ancestors than any real path has; past it, residency is unknown.
_MAX_ANCESTORS = 4096


def _resident_checkout(
    path: Path | str, checkouts: tuple[Path, ...], *, dir_fd: int | None = None
) -> Path | None:
    """The checkout whose root is an ancestor of ``path``'s directory, or ``None``.

    Walked by DESCRIPTORS where the platform has them, never by growing a path
    string (``a/../..`` past ``PATH_MAX`` fails with ``ENAMETOOLONG``, which an
    earlier walk read as "outside"): from the store's directory, ``..`` relative
    to each directory reached, comparing ``(st_dev, st_ino)`` with every
    checkout root's, until a directory is its own parent. Without ``dir_fd`` /
    ``O_DIRECTORY`` (Windows) the store's directory is resolved strictly and its
    ``parents`` are compared by the same identity (Windows fills ``st_dev`` /
    ``st_ino`` from the volume serial and file index). A checkout root that does
    not exist (a served project not created yet) is skipped. A store directory
    not created yet is judged by the nearest existing directory above it, found
    by stepping only across plain names. Any other error raises: residency is
    then unknown, and the caller refuses.
    """
    roots = []
    for checkout in checkouts:
        if is_absent(checkout):
            continue  # nothing can live inside a root that does not exist
        st = os.stat(checkout)
        roots.append(((st.st_dev, st.st_ino), checkout))
    if not roots:
        return None

    def match(st: os.stat_result) -> Path | None:
        for ident, checkout in roots:
            if (st.st_dev, st.st_ino) == ident:
                return checkout
        return None

    by_fd = hasattr(os, "O_DIRECTORY") and bool(os.supports_dir_fd)
    if by_fd:
        try:
            return _resident_by_descriptor(path, match, dir_fd)
        except OSError as exc:
            # Without O_PATH a directory the user may search but not list
            # (0311) refuses the descriptor walk the kernel's lookup would
            # pass: judge by pathname identity instead (the shared rule).
            if not falls_back_to_pathname(exc):
                raise
            if dir_fd is not None:
                path = resolve_write_target(path)  # the store's real directory
    return _resident_by_pathname(path, match)


def _existing_directory(path: Path | str) -> str:
    """The store's directory, or the nearest existing one above it across plain
    names only (a store directory not created yet)."""
    current = os.path.dirname(os.fspath(path)) or os.curdir
    while True:
        try:
            os.stat(current)
            return current
        except FileNotFoundError:
            name = os.path.basename(current)
            parent = os.path.dirname(current)
            if name in ("", os.curdir, os.pardir) or parent == current:
                raise
            current = parent


def _resident_by_pathname(
    path: Path | str, match: Callable[[os.stat_result], Path | None]
) -> Path | None:
    """Strictly resolve the store's directory; compare it and each parent by
    ``(st_dev, st_ino)`` -- needs only search permission, and is the form used
    where descriptors are unavailable (Windows fills these from the file index)."""
    real = Path(os.path.realpath(_existing_directory(path), strict=True))
    for ancestor in (real, *real.parents):
        found = match(os.stat(ancestor))
        if found is not None:
            return found
    return None


def _resident_by_descriptor(
    path: Path | str,
    match: Callable[[os.stat_result], Path | None],
    dir_fd: int | None,
) -> Path | None:
    """Walk up with ``..`` relative to each directory reached, by descriptor."""
    fd = (
        os.dup(dir_fd)
        if dir_fd is not None
        else open_directory(_existing_directory(path))
    )
    try:
        for _ in range(_MAX_ANCESTORS):
            here = os.fstat(fd)
            found = match(here)
            if found is not None:
                return found
            up = open_directory(os.pardir, dir_fd=fd)
            above = os.fstat(up)
            os.close(fd)
            fd = up
            if (above.st_dev, above.st_ino) == (here.st_dev, here.st_ino):
                return None  # the filesystem root, checked on the previous turn
        raise OSError(errno.ELOOP, "Too many ancestors to establish residency")
    finally:
        os.close(fd)


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
    if is_absent(path):  # only ENOENT/ENOTDIR; ELOOP, EACCES ... are raised
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

    # Write a temp file beside the store and rename it into place. `os.replace`
    # is atomic, so a reader never observes a half-written store and an
    # interrupted write leaves the previous one intact rather than truncated.
    # The DIRECTORY is fsync-ed too (best effort): the rename is only durable
    # once the directory entry is synced, so a crash right after a revoke must
    # not leave the pre-revoke store on disk and resurrect the approval the
    # operator just withdrew -- the invariant the lock protects against a race,
    # reached through power loss instead. `path` is already fully resolved by
    # `trust_store_path()`, so the helper's symlink-following is the identity
    # here and the residency check above judged the file actually written.
    text = json.dumps(payload, indent=2) + "\n"
    # confine_to=None: the operator's own ~/.config/pmcp, already resolved and
    # refused if checkout-resident (C-15).
    atomic_write(
        path, text.encode("utf-8"), confine_to=None, mode=0o600, prefix=".trust-"
    )


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
    # Walked as the kernel would; only the plain tail of directories that do
    # not exist yet is created, each at 0o700 (restrictive AT CREATION),
    # relative to the last directory the walk reached. Never treated as absent
    # and created when the path cannot be resolved for another reason.
    for created in make_store_dirs(parent):
        with contextlib.suppress(OSError):
            # Only for a umask that stripped owner bits; never loosens.
            os.chmod(created, 0o700)


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


def judged_roots(target: Path, project_root: Path | None) -> tuple[Path, ...]:
    """The roots a judgement of ``target`` adds to the residency guard.

    The directory of the file being approved, and the project root its reader
    is bound to (``None``: none beyond the file's own). See
    :func:`_checkout_roots`.
    """
    roots = [target.parent]
    if project_root is not None:
        roots.append(Path(project_root))
    return tuple(roots)


def is_approved(
    path: Path, content: bytes, *, project_root: Path | None = None
) -> bool:
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
        return is_approved_resolved(
            Path(path).resolve(), content, project_root=project_root
        )
    except Exception:  # noqa: BLE001 -- fail closed; see docstring
        return False


def is_approved_resolved(
    resolved_path: Path, content: bytes, *, project_root: Path | None = None
) -> bool:
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
        # Residency is judged against the approved file's own checkout and the
        # reader's bound project too, never the working directory alone.
        records = _read_store(
            trust_store_path(also=judged_roots(resolved_path, project_root))
        )
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
        if _resident_checkout(store, (checkout,)) is not None:
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


# The launch directory, captured at import (see _LAUNCH_DIRECTORY).
_capture_launch_directory()
