"""Is the home directory the operator's -- judged on a PLAIN HOME only.

Re-implementing the kernel's path resolution kept losing corners (relative
HOME, ``..`` through an unsearchable directory, Windows separators, a missing
component followed by ``..``), so the degree of freedom is removed instead
(Consiliency/pmcp#372 round 24). Only the operator sets HOME; a repository
cannot. So:

1. **HOME must be plain**: absolute for the platform, no ``.`` or ``..``
   component, and the system resolves it (``os.stat`` succeeds). Anything
   else is not the operator's: every home-scoped file and every trust and
   approval decision is refused, with one value-free line asking for a plain
   absolute HOME. (HOME unset falls back to the passwd entry, as pathlib does;
   that value passes the same test.)
2. **Every link on the way.** Every symlink met while resolving HOME, at any
   depth -- a link in HOME's spelling, a link inside that link's target, and
   so on, with ``..`` in a target applied after the earlier links -- must be
   held by a physical directory that is not a checkout and lies inside none
   (Consiliency/pmcp#372 round 27: an operator ``alias -> <checkout>/home``
   with a repository ``home -> .`` landed HOME on the checkout root). The
   links are collected by one walk (:func:`_walk`) that only records them;
   ``os.path.realpath`` stays the oracle for where HOME lands, and a walk that
   fails in any way, or ends anywhere else, refuses.
3. **Physical ancestors.** No physical ancestor of ``realpath(HOME)`` --
   HOME itself excluded, so a dotfiles repository AT home, reached through the
   operator's own links, is fine -- is a checkout.
4. **A filesystem or drive root is never a checkout**: a repository cannot put
   files there, and container images (``COPY . /``) often do.

A checkout is a directory holding one of :data:`CHECKOUT_MARKERS`.

Every home-scoped file is located through this module's gate
(``tests/test_store_reader_inventory.py`` forbids deriving HOME elsewhere), so
no writer creates anything under a HOME that is not the operator's. No
dependencies on the rest of pmcp, so any module may import it.
"""

from __future__ import annotations

import errno as _errno
import os
import os as _os_module  # the real module: its environ keys the spelling memo
import stat
import sys as _sys
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import cast

Identity = tuple[int, int]

#: What makes a directory a checkout: the markers project discovery uses
#: (``config.loader.find_project_root``). Never at a filesystem or drive root.
CHECKOUT_MARKERS = (
    ".mcp.json",
    ".git",
    "package.json",
    "pyproject.toml",
    os.path.join(".pmcp", "manifest.yaml"),
)


def _identity(path: os.PathLike[str] | str) -> Identity | None:
    try:
        status = os.stat(path)
    except OSError:
        return None
    return (status.st_dev, status.st_ino)


def _is_root(directory: str) -> bool:
    return os.path.dirname(directory) == directory


def has_checkout_marker(directory: os.PathLike[str] | str) -> bool:
    """Does ``directory`` hold a checkout marker? A root never counts."""
    spelled = os.fspath(directory)
    if _is_root(spelled):
        return False
    for marker in CHECKOUT_MARKERS:
        try:
            os.lstat(os.path.join(spelled, marker))
        except OSError:
            continue
        return True
    return False


def _marked_from(directory: str, reads: list[_Read] | None = None) -> str | None:
    """The first checkout at ``directory`` or above it, physically, or ``None``.

    With ``reads``, every directory examined is recorded with its signature
    (see :func:`_judge`)."""
    current = directory
    while not _is_root(current):
        if reads is not None:
            reads.append(("d", current, _directory_signature(current)))
        if has_checkout_marker(current):
            return current
        current = os.path.dirname(current)
    return None


def _is_plain(spelled: str) -> bool:
    """Absolute for the platform, with no ``.`` or ``..`` component."""
    if not os.path.isabs(spelled):
        return False
    separators = {os.path.sep, os.path.altsep} - {None}
    parts = [spelled]
    for separator in separators:
        parts = [piece for part in parts for piece in part.split(separator)]
    return not any(part in (".", "..") for part in parts)


#: (memo key, spelling); see :func:`_home_spelling`.
_SPELLING: tuple[tuple[object, ...], str] | None = None


def _home_spelling() -> str:
    """HOME as spelled. pathlib drops ``.`` components, so the raw value
    ``expanduser`` reads (``HOME``; ``USERPROFILE`` on Windows; else the
    passwd entry) is used when it is the one pathlib read.

    Memoized on the environment values pathlib reads (and the path modules in
    use) while one of them is set; the passwd fallback is read every time."""
    global _SPELLING
    env = _os_module.environ
    key = (
        id(Path),
        id(os),
        env.get("HOME"),
        env.get("USERPROFILE"),
        env.get("HOMEDRIVE"),
        env.get("HOMEPATH"),
    )
    memo = _SPELLING
    if memo is not None and memo[0] == key:
        return memo[1]
    home = os.fspath(Path.home())
    raw = os.path.expanduser("~")
    spelled = raw if os.fspath(Path(raw)) == home else home
    _SPELLING = (key, spelled) if (key[2] or key[3]) else None
    return spelled


class _Verdict:
    """The judgement of one spelling: its physical directory, or why not."""

    def __init__(self, real: str | None, checkout: str | None = None) -> None:
        self.real = real
        self.checkout = checkout


#: One thing a verdict read: ``(kind, path, what it saw)``.
_Read = tuple[str, str, object]

#: Verdicts by (spelling, path module, marker predicate) -> (reads, verdict).
#: A hit is revalidated against every read before it is used (:func:`_judge`).
_VERDICTS: dict[tuple[str, int, int], tuple[tuple[_Read, ...], _Verdict]] = {}
_VERDICTS_MAX = 256


def _signature(path: str, *, follow: bool) -> tuple[object, ...]:
    """What a later read must see for a verdict built on this one to stand:
    the entry's identity and type, or the error reading it. No timestamp:
    a filesystem need not update one (Windows reports creation time as
    ``st_ctime``; network filesystems may be coarse), so none is evidence."""
    try:
        status = os.stat(path) if follow else os.lstat(path)
    except OSError as exc:
        return ("error", exc.errno)
    # A Windows reparse point (a junction, a mount point) is a redirect the
    # mode does not show: its tag is part of what the entry is.
    tag = getattr(status, "st_reparse_tag", 0) or 0
    return (status.st_dev, status.st_ino, status.st_mode, tag)


def _is_redirect(seen: tuple[object, ...]) -> bool:
    """A symlink, or any reparse point (a junction is a directory by mode)."""
    return stat.S_ISLNK(cast(int, seen[2])) or bool(seen[3])


def _directory_signature(directory: str) -> object:
    """A directory examined for markers: its identity, and each marker path
    looked up directly (present, with its identity, or absent)."""
    return (
        _signature(directory, follow=True),
        tuple(
            _signature(os.path.join(directory, marker), follow=False)
            for marker in CHECKOUT_MARKERS
        ),
    )


def _still_seen(reads: tuple[_Read, ...]) -> bool:
    for kind, path, seen in reads:
        if kind == "l":
            now: object = _signature(path, follow=False)
        elif kind == "s":
            now = _signature(path, follow=True)
        elif kind == "r":
            now = os.path.realpath(path)
        elif kind == "k":
            now = _link_text(path)
        else:
            now = _directory_signature(path)
        if now != seen:
            return False
    return True


def forget_home_verdicts() -> None:
    """Drop every cached verdict. Trust and approval decisions call this
    first, so they always judge afresh."""
    global _SPELLING
    _VERDICTS.clear()
    _SPELLING = None


def _judge(spelled: str) -> _Verdict:
    """Rules 1-3 for ``spelled``; ``real`` is ``None`` unless all pass.

    Cached per process: a verdict is reused only while everything it read
    still reads the same -- each prefix's ``lstat`` (identity and type),
    HOME's ``stat``, every ``realpath`` through a link, and for every
    directory examined for markers its identity and a direct ``lstat`` of
    each marker path. No timestamp is ever the evidence that a marker is
    absent (Consiliency/pmcp#372 round 26). A spelling that is not plain, or
    that the system cannot resolve, is judged every time.
    """
    key = (spelled, id(os), id(has_checkout_marker))
    hit = _VERDICTS.get(key)
    if hit is not None and _still_seen(hit[0]):
        return hit[1]
    reads: list[_Read] = []
    verdict, cacheable = _judge_afresh(spelled, reads)
    if cacheable:
        if len(_VERDICTS) >= _VERDICTS_MAX:
            _VERDICTS.clear()
        _VERDICTS[key] = (tuple(reads), verdict)
    else:
        _VERDICTS.pop(key, None)
    return verdict


#: More links than any real path has (the kernel's own limit is 40).
_MAX_LINKS = 40


def _link_text(path: str) -> object:
    try:
        return os.readlink(path)
    except OSError as exc:
        return ("error", exc.errno)


def _root_of(path: str) -> str:
    current = path
    while True:
        parent = os.path.dirname(current)
        if parent == current:
            return current
        current = parent


def _components(path: str) -> list[str]:
    separators = {os.path.sep, os.path.altsep} - {None}
    parts = [path]
    for separator in separators:
        parts = [piece for part in parts for piece in part.split(separator)]
    return [part for part in parts if part not in ("", ".")]


def _walk(spelled: str, reads: list[_Read]) -> tuple[str, list[str]] | None:
    """Follow ``spelled`` as the kernel does, only to COLLECT the links on the
    way: the physical directory holding each one, at every depth.

    Returns ``(where it ended, holders)``, or ``None`` when the walk cannot
    follow (an error, a non-directory in the middle, too many links). Every
    entry looked up is recorded with its signature and every link with its
    text, so a cached verdict re-reads the whole chain. The caller compares
    the end with ``os.path.realpath``, which stays the oracle.
    """
    root = _root_of(spelled)
    current = root
    remaining = deque(_components(spelled[len(root) :]))
    holders: list[str] = []
    links = 0
    while remaining:
        name = remaining.popleft()
        if name == "..":
            current = os.path.dirname(current)
            continue
        candidate = os.path.join(current, name)
        seen = _signature(candidate, follow=False)
        if seen[0] == "error":
            return None
        reads.append(("l", candidate, seen))
        mode = cast(int, seen[2])
        if _is_redirect(seen):
            links += 1
            target = _link_text(candidate)
            if links > _MAX_LINKS or not isinstance(target, str):
                return None
            if target.startswith("\\\\?\\") and not target.startswith("\\\\?\\UNC\\"):
                # A junction's text names its target in the NT namespace.
                target = target[4:]
            reads.append(("k", candidate, target))
            holders.append(current)
            if os.path.isabs(target):
                current = _root_of(target)
                target = target[len(current) :]
            remaining.extendleft(reversed(_components(target)))
            continue
        if remaining and not stat.S_ISDIR(mode):
            return None
        current = candidate
    return current, holders


def _same_spelling(walked: str, real: str) -> bool:
    """Do the walk's end and ``realpath`` name the same path? Compared after
    the platform's own separator and case folding (the walk keeps a written
    ``/`` on Windows; it never leaves a ``.`` or ``..`` to fold)."""
    fold = os.path.normcase
    return fold(os.path.normpath(walked)) == fold(os.path.normpath(real))


def _judge_afresh(spelled: str, reads: list[_Read]) -> tuple[_Verdict, bool]:
    if not _is_plain(spelled):
        return _Verdict(None), False
    stat_seen = _signature(spelled, follow=True)
    if stat_seen[0] == "error":
        return _Verdict(None), False
    reads.append(("s", spelled, stat_seen))
    walked = _walk(spelled, reads)
    if walked is None:
        return _Verdict(None), False
    end, holders = walked
    real = os.path.realpath(spelled)
    if holders:
        # Where the links lead is read again on every reuse. With none, the
        # unchanged non-link entries already fix ``real``.
        reads.append(("r", spelled, real))
    # The walk's end has no link left in it, so ``realpath`` of it is only
    # its canonical spelling (a leading ``//`` folds as it does for HOME).
    if not _same_spelling(os.path.realpath(end), real):
        # The walk and the system disagree on where HOME lands: refuse.
        return _Verdict(None), False
    for directory in dict.fromkeys(holders):
        # Judged where the holder physically is: whatever the walk does not
        # model as a link (a bind mount, a reparse point, a magic link)
        # still resolves here. Unresolvable, refused.
        physical = os.path.realpath(directory)
        reads.append(("r", directory, physical))
        if _signature(physical, follow=True)[0] == "error":
            return _Verdict(None), False
        holder = _marked_from(physical, reads)
        if holder is not None:
            return _Verdict(None, holder), True
    holder = _marked_from(os.path.dirname(real), reads)
    if holder is not None:
        return _Verdict(None, holder), True
    return _Verdict(real), True


def home_is_operators() -> bool:
    """Rules 1-4 hold for HOME."""
    return _judge(_home_spelling()).real is not None


def examinable_home() -> Path | None:
    """HOME's physical directory while HOME is the operator's, else ``None``."""
    real = _judge(_home_spelling()).real
    return None if real is None else Path(real)


def checkout_controlling_home() -> Path | None:
    """The checkout that makes HOME not the operator's, for a refusal message."""
    holder = _judge(_home_spelling()).checkout
    return None if holder is None else Path(holder)


def is_home(directory: os.PathLike[str] | str) -> bool:
    """Is ``directory`` the operator's home itself (by identity)?"""
    home = examinable_home()
    identity = _identity(directory)
    return home is not None and identity is not None and identity == _identity(home)


def _operator_area_identities(store_directories: bool) -> set[Identity]:
    """Home, its physical ancestors and (``store_directories``) home's real
    ``.config`` / ``.config/pmcp`` -- empty unless HOME is the operator's."""
    home = examinable_home()
    if home is None:
        return set()
    found = {i for d in (home, *home.parents) if (i := _identity(d)) is not None}
    if store_directories:
        current = home
        for name in (".config", "pmcp"):
            current = current / name
            try:
                status = os.lstat(current)  # a link here is not the store's own
            except OSError:
                break
            if not stat.S_ISDIR(status.st_mode) or has_checkout_marker(current):
                break
            found.add((status.st_dev, status.st_ino))
    return found


def is_operator_owned(
    directory: os.PathLike[str] | str, *, store_directories: bool = True
) -> bool:
    """Is ``directory`` the operator's own?

    By identity one of the operator's areas (:func:`_operator_area_identities`,
    which needs HOME to be the operator's) -- AND its spelling passes the same
    rules as HOME's: plain, no link a checkout holds, no checkout above it.
    """
    identity = _identity(directory)
    if identity is None or identity not in _operator_area_identities(store_directories):
        return False
    spelled = os.fspath(directory)
    if not os.path.isabs(spelled):
        spelled = os.path.join(os.getcwd(), spelled)
    return _judge(spelled).real is not None


def enclosing_checkouts(start: os.PathLike[str] | str) -> Iterator[Path]:
    """Every checkout from ``start``'s physical directory up to the root, nearest first.

    The residency walk -- not project discovery, which stops at home. The
    operator's home directory itself (by identity) is never yielded: a dotfiles
    repository there is the operator's. A root is never a checkout.
    """
    home = examinable_home()
    home_identity = _identity(home) if home is not None else None
    current = os.path.realpath(os.fspath(start))
    while not _is_root(current):
        if has_checkout_marker(current) and not (
            home_identity is not None and _identity(current) == home_identity
        ):
            yield Path(current)
        current = os.path.dirname(current)


# --------------------------------------------------------------------------- #
# THE gate for every home-scoped read and write (Consiliency/pmcp#372 round 22,
# board round 21 grok F001). Every file pmcp treats as the operator's because it
# sits under HOME is located through these functions, which answer only while
# HOME is the operator's; otherwise pmcp runs without the file.
# --------------------------------------------------------------------------- #

_HOME_REFUSAL = "the home directory lies inside a checkout"
#: The refusal while HOME is not a plain absolute path the system resolves.
HOME_NOT_PLAIN = (
    "HOME is not a plain absolute path the system resolves; "
    "set HOME to a plain absolute path"
)
_WARNED_HOME = False


class HomeInsideCheckoutError(PermissionError):
    """HOME is not the operator's: a checkout controls it, or it is not plain.
    ``strerror`` says which."""


def _refusal() -> str | None:
    verdict = _judge(_home_spelling())
    if verdict.real is not None:
        return None
    return _HOME_REFUSAL if verdict.checkout is not None else HOME_NOT_PLAIN


def reset_home_warning() -> None:
    """Forget that the refusal was reported. **Test-only seam.**"""
    global _WARNED_HOME
    _WARNED_HOME = False


def operator_home() -> Path:
    """HOME, only while it is the operator's; otherwise raise (a PermissionError)."""
    global _WARNED_HOME
    reason = _refusal()
    if reason is not None:
        if not _WARNED_HOME:
            _WARNED_HOME = True
            print(
                f"pmcp: Ignoring the operator's files under the home directory: {reason}",
                file=_sys.stderr,
            )
        raise HomeInsideCheckoutError(_errno.EPERM, reason)
    return Path.home()


def optional_operator_home() -> Path | None:
    """HOME, only while it is the operator's; otherwise ``None`` (one warning)."""
    try:
        return operator_home()
    except HomeInsideCheckoutError:
        return None


def home_path(*parts: str) -> Path:
    """``operator_home() / parts`` -- raises while HOME is not the operator's."""
    return operator_home().joinpath(*parts)


def optional_home_path(*parts: str) -> Path | None:
    """``optional_operator_home() / parts``, or ``None``."""
    home = optional_operator_home()
    return None if home is None else home.joinpath(*parts)


def spelled_home() -> Path:
    """HOME as spelled, UNCHECKED: only for frozen import-time constants that
    the documented test seams compare by identity and never read at runtime."""
    return Path.home()
