"""Is a directory the operator's home -- decided by file identity, never spelling.

``HOME`` can be spelled through a symlink, and a directory can link to it, so a
path comparison can both miss the real home and mistake a link for it
(Consiliency/pmcp#372 round 19, board round 18 codex F001: a launch directory
holding a link to an externally spelled home was taken for an ancestor of home,
dropped from the residency guard, and a ``trust.json`` planted in it approved
the served project). Every home comparison in the trust, residency and store
code asks this module, which compares ``(st_dev, st_ino)`` only:

* the home directory and its PHYSICAL ancestors -- walked from
  ``os.path.realpath(home)``, never from the spelling ``HOME`` holds;
* and, for the residency guard, the directories on the default store's own
  physical path -- home's ``.config`` and ``.config/pmcp`` -- each only while it
  is a real directory, not a link: a store linked elsewhere is judged by where
  it resolves, so a link into a checkout is still refused.

No dependencies on the rest of pmcp, so the store, trust and config modules can
all import it without a cycle.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

Identity = tuple[int, int]


def _identity(path: os.PathLike[str] | str, *, follow: bool = True) -> Identity | None:
    try:
        status = os.stat(path) if follow else os.lstat(path)
    except OSError:
        return None
    return (status.st_dev, status.st_ino)


def physical_home(home: Path | None = None) -> Path:
    """The home directory as the kernel resolves it (``home``: default ``Path.home()``)."""
    return Path(os.path.realpath(home if home is not None else Path.home()))


def home_and_ancestor_identities(home: Path | None = None) -> set[Identity]:
    """``(st_dev, st_ino)`` of the home directory and each of its PHYSICAL ancestors."""
    real = physical_home(home)
    found: set[Identity] = set()
    for directory in (real, *real.parents):
        identity = _identity(directory)
        if identity is not None:
            found.add(identity)
    return found


def default_store_directory_identities(home: Path | None = None) -> set[Identity]:
    """Home's ``.config`` and ``.config/pmcp``, each only while a REAL directory.

    Judged with ``lstat``: a link at either name contributes nothing (nor does
    anything below it), so a store linked into another directory is judged by
    where it lands.
    """
    real = physical_home(home)
    found: set[Identity] = set()
    current = real
    for name in (".config", "pmcp"):
        current = current / name
        try:
            status = os.lstat(current)
        except OSError:
            break
        if not stat.S_ISDIR(status.st_mode):  # a link, or not a directory
            break
        found.add((status.st_dev, status.st_ino))
    return found


def is_home(directory: os.PathLike[str] | str, home: Path | None = None) -> bool:
    """Is ``directory`` the home directory itself (by identity)?"""
    identity = _identity(directory)
    return identity is not None and identity == _identity(physical_home(home))


def is_home_or_above(
    directory: os.PathLike[str] | str, home: Path | None = None
) -> bool:
    """Is ``directory`` the home directory or one of its physical ancestors?"""
    identity = _identity(directory)
    return identity is not None and identity in home_and_ancestor_identities(home)


def is_operators_own_area(
    directory: os.PathLike[str] | str, home: Path | None = None
) -> bool:
    """Home, a physical ancestor of it, or a directory of the default store's path.

    The residency guard's exemption: none of these is ever a repository's
    boundary, so the operator's own store keeps working wherever pmcp starts.
    """
    identity = _identity(directory)
    if identity is None:
        return False
    return identity in home_and_ancestor_identities(
        home
    ) or identity in default_store_directory_identities(home)


# --------------------------------------------------------------------------- #
# Operator ownership (Consiliency/pmcp#372 round 21, boards round 20 grok and
# codex F001). Being home, an ancestor of home or the default store's directory
# is not enough: a checkout can make a path LOOK like the operator's -- a
# repository-shipped `home -> .` with HOME=<checkout>/home resolves home to the
# checkout itself; a checkout that encloses home owns home's ancestors. A path
# is the operator's only when no checkout controls it.
# --------------------------------------------------------------------------- #

#: What makes a directory a checkout: the markers project discovery uses
#: (``config.loader.find_project_root``).
CHECKOUT_MARKERS = (
    ".mcp.json",
    ".git",
    "package.json",
    "pyproject.toml",
    os.path.join(".pmcp", "manifest.yaml"),
)


def has_checkout_marker(directory: os.PathLike[str] | str) -> bool:
    for marker in CHECKOUT_MARKERS:
        try:
            os.lstat(os.path.join(directory, marker))
        except OSError:
            continue
        return True
    return False


def _lexical_prefixes(path: os.PathLike[str] | str) -> list[Path]:
    """``/``, ``/a``, ``/a/b``, ... of ``path`` AS WRITTEN (absolute, not resolved)."""
    absolute = Path(os.path.abspath(path))
    return [*reversed(absolute.parents), absolute]


def _marked_at_or_above(resolved: Path) -> bool:
    """Is any directory from ``resolved`` up to ``/`` a checkout (no exceptions)?"""
    current = resolved
    while True:
        if has_checkout_marker(current):
            return True
        if current.parent == current:
            return False
        current = current.parent


def home_is_clean(home: Path | None = None) -> bool:
    """Does HOME, as written, reach home without a link that resolves into a checkout?

    The condition for treating a repository AT home (a dotfiles repository) as
    the operator's rather than as a checkout: a repository-shipped
    ``<checkout>/home -> .`` makes home resolve to the checkout itself.
    """
    spelled = home if home is not None else Path.home()
    for prefix in _lexical_prefixes(spelled):
        if os.path.islink(prefix) and _marked_at_or_above(
            Path(os.path.realpath(prefix))
        ):
            return False
    return True


def enclosing_checkouts(start: os.PathLike[str] | str):  # type: ignore[no-untyped-def]
    """Every checkout from ``start`` (resolved) up to ``/``, nearest first.

    The residency walk -- not project discovery, which stops at home. The home
    directory itself is skipped only while HOME is clean (:func:`home_is_clean`):
    a dotfiles repository at home is the operator's; a checkout home merely
    resolves to is not.
    """
    try:
        current = Path(os.path.realpath(start))
    except (OSError, ValueError):
        return
    skip_home = home_is_clean()
    while True:
        if has_checkout_marker(current) and not (skip_home and is_home(current)):
            yield current
        if current.parent == current:
            return
        current = current.parent


def inside_a_checkout(path: os.PathLike[str] | str) -> bool:
    """Does a checkout enclose ``path`` along its UNRESOLVED spelling?

    Each component as written, from ``/``: if the directory it names -- or the
    target a link there resolves to -- lies inside a checkout, so does the path.
    """
    for prefix in _lexical_prefixes(path):
        try:
            if any(True for _ in enclosing_checkouts(prefix)):
                return True
        except OSError:
            return True  # cannot establish: not the operator's
    return False


def is_operator_owned(
    directory: os.PathLike[str] | str,
    *,
    store_directories: bool = True,
    home: Path | None = None,
) -> bool:
    """THE predicate: is ``directory`` the operator's own?

    Both must hold: (1) by identity it is home, a physical ancestor of home, or
    (``store_directories``) home's real ``.config`` / ``.config/pmcp``; and (2)
    no checkout encloses it along its unresolved spelling
    (:func:`inside_a_checkout`). Used for the residency exemption, the startup
    ``.env`` rule and the user store.
    """
    identity = _identity(directory)
    if identity is None:
        return False
    areas = home_and_ancestor_identities(home)
    if store_directories:
        areas |= default_store_directory_identities(home)
    if identity not in areas:
        return False
    return not inside_a_checkout(directory)
