"""Is the home directory the operator's -- ONE rule, by kernel-order traversal.

**HOME is operator-owned if and only if no directory TRAVERSED while resolving
HOME -- other than HOME's own final directory -- carries a checkout marker**
(Consiliency/pmcp#372 round 23, board round 22: the patch-per-round rules did
not converge).

"Traversed" is the kernel's own walk (the rule of Consiliency/pmcp#366's chain
follower): HOME as written, component by component from ``/``; a symlink's
target spliced in as written and walked from the right directory; ``..``
applied after earlier links resolved; never ``abspath``/``normpath``/
``realpath`` first. Every directory in which a name is looked up -- including
those inside link targets -- counts; the final directory does not, so a
dotfiles repository (or a ``~/.mcp.json``) at HOME is fine however HOME is
reached. Components that do not exist end the walk: a checkout among the
directories already traversed refuses HOME; otherwise the access reports its
own error, and trust and approval decisions fail closed
(:func:`trust_home_path`).

Settled by the rule: a repository ``home -> .`` or ``HOME=<checkout>/home/..``
traverses the checkout (refused); a real directory inside a checkout (refused);
an operator link such as ``/home -> /var/home`` (allowed); HOME itself a
dotfiles repository (allowed); a missing HOME inside a checkout (refused).

Every home-scoped file is located through this module's gate
(``tests/test_store_reader_inventory.py`` forbids deriving HOME elsewhere). No
dependencies on the rest of pmcp, so any module may import it.
"""

from __future__ import annotations

import errno as _errno
import os
import stat
import sys as _sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

Identity = tuple[int, int]

#: What makes a directory a checkout: the markers project discovery uses
#: (``config.loader.find_project_root``).
CHECKOUT_MARKERS = (
    ".mcp.json",
    ".git",
    "package.json",
    "pyproject.toml",
    os.path.join(".pmcp", "manifest.yaml"),
)

#: More links than any real path has (the kernel's own limit is 40).
_MAX_LINKS = 40


def _identity(path: os.PathLike[str] | str) -> Identity | None:
    try:
        status = os.stat(path)
    except OSError:
        return None
    return (status.st_dev, status.st_ino)


def has_checkout_marker(directory: os.PathLike[str] | str) -> bool:
    for marker in CHECKOUT_MARKERS:
        try:
            os.lstat(os.path.join(directory, marker))
        except OSError:
            continue
        return True
    return False


@dataclass
class Traversal:
    """The kernel-order walk of one path."""

    #: Every directory a name was looked up in, physical, in walk order.
    traversed: list[Path] = field(default_factory=list)
    #: The physical final directory (or file); ``None`` if the walk stopped.
    final: Path | None = None
    #: The last physical directory reached (the final one, or where it stopped).
    reached: Path = Path("/")
    #: A component did not exist (``ENOENT``/``ENOTDIR``).
    missing: bool = False
    #: The walk could not continue for another reason (``EACCES``, ``ELOOP``).
    unexaminable: bool = False

    def marked(self) -> Path | None:
        """The first traversed directory that is a checkout, or ``None``."""
        return next((d for d in self.traversed if has_checkout_marker(d)), None)


def traverse(path: os.PathLike[str] | str) -> Traversal:
    """Walk ``path`` as the kernel resolves it, recording every directory used."""
    spelled = os.fspath(path)
    walk = Traversal()
    current = Path("/") if os.path.isabs(spelled) else Path(os.getcwd())
    remaining = deque(spelled.split("/"))
    links = 0
    while remaining:
        name = remaining.popleft()
        if name in ("", "."):
            continue
        walk.traversed.append(current)
        if name == "..":
            current = current.parent
            continue
        candidate = current / name
        try:
            status = os.lstat(candidate)
        except (FileNotFoundError, NotADirectoryError):
            walk.missing = True
            walk.reached = current
            return walk
        except OSError:
            walk.unexaminable = True
            walk.reached = current
            return walk
        if stat.S_ISLNK(status.st_mode):
            links += 1
            if links > _MAX_LINKS:
                walk.unexaminable = True
                walk.reached = current
                return walk
            target = os.readlink(candidate)
            if os.path.isabs(target):
                current = Path("/")
            remaining.extendleft(reversed(target.split("/")))
            continue
        if remaining and not stat.S_ISDIR(status.st_mode):
            walk.missing = True  # a file where a directory is needed: ENOTDIR
            walk.reached = current
            return walk
        current = candidate
    walk.final = current
    walk.reached = current
    return walk


def _home_walk() -> Traversal:
    return traverse(Path.home())


def home_is_operators() -> bool:
    """THE rule: no directory traversed to reach HOME (but HOME's own) is a checkout."""
    return _home_walk().marked() is None


def examinable_home() -> Path | None:
    """HOME's physical final directory while HOME is the operator's and resolves.

    ``None`` when a component is missing, the walk cannot proceed, or a
    traversed directory is a checkout: nothing derived from such a HOME is the
    operator's, and trust decisions refuse it.
    """
    walk = _home_walk()
    if walk.final is None or walk.marked() is not None:
        return None
    return walk.final


def checkout_controlling_home() -> Path | None:
    """The checkout HOME's resolution traverses, for a refusal message."""
    return _home_walk().marked()


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
    which needs HOME to be the operator's) -- AND reached, as written, through
    no checkout (the same traversal rule applied to ``directory`` itself).
    """
    identity = _identity(directory)
    if identity is None or identity not in _operator_area_identities(store_directories):
        return False
    return traverse(directory).marked() is None


def enclosing_checkouts(start: os.PathLike[str] | str):  # type: ignore[no-untyped-def]
    """Every checkout from ``start``'s physical directory up to ``/``, nearest first.

    The residency walk -- not project discovery, which stops at home. The
    operator's home directory itself (by identity) is never yielded: a dotfiles
    repository there is the operator's.
    """
    walk = traverse(start)
    home = examinable_home()
    home_identity = _identity(home) if home is not None else None
    current = walk.final if walk.final is not None else walk.reached
    while True:
        if has_checkout_marker(current) and not (
            home_identity is not None and _identity(current) == home_identity
        ):
            yield current
        if current.parent == current:
            return
        current = current.parent


# --------------------------------------------------------------------------- #
# THE gate for every home-scoped read and write (Consiliency/pmcp#372 round 22,
# board round 21 grok F001). Every file pmcp treats as the operator's because it
# sits under HOME is located through these functions, which answer only while
# HOME is the operator's; otherwise pmcp runs without the file.
# --------------------------------------------------------------------------- #

_HOME_REFUSAL = "the home directory lies inside a checkout"
#: The refusal for a trust or approval decision while HOME cannot be examined.
HOME_UNEXAMINABLE = "the home directory cannot be examined"
_WARNED_HOME = False


class HomeInsideCheckoutError(PermissionError):
    """HOME is not operator-owned: a checkout controls it."""


def _warn_home_once() -> None:
    global _WARNED_HOME
    if not _WARNED_HOME:
        _WARNED_HOME = True
        print(
            "pmcp: Ignoring the operator's files under the home directory: "
            f"{_HOME_REFUSAL}",
            file=_sys.stderr,
        )


def reset_home_warning() -> None:
    """Forget that the refusal was reported. **Test-only seam.**"""
    global _WARNED_HOME
    _WARNED_HOME = False


def operator_home() -> Path:
    """HOME, only while it is the operator's; otherwise raise (a PermissionError).

    A HOME that is missing or cannot be walked, and is not inside a checkout,
    goes through: the access that follows reports its own error.
    """
    if not home_is_operators():
        _warn_home_once()
        raise HomeInsideCheckoutError(_errno.EPERM, _HOME_REFUSAL)
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


def trust_home_path(*parts: str) -> Path:
    """``home_path`` for TRUST and APPROVAL stores: fails closed.

    Refused, with one value-free line, while HOME cannot be examined -- missing,
    or not walkable -- as well as while a checkout controls it.
    """
    path = home_path(*parts)
    if examinable_home() is None:
        print(f"pmcp: Refusing the trust stores: {HOME_UNEXAMINABLE}", file=_sys.stderr)
        raise HomeInsideCheckoutError(_errno.EPERM, HOME_UNEXAMINABLE)
    return path


def spelled_home() -> Path:
    """HOME as spelled, UNCHECKED: only for frozen import-time constants that
    the documented test seams compare by identity and never read at runtime."""
    return Path.home()
