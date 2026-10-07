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
