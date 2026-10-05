"""One atomic-write primitive for every user-owned file PMCP rewrites.

Every PMCP-owned file that is rewritten whole -- the credential stores
(``pmcp.env``, ``.env.pmcp``), the trust store, package approvals, the registry
cache, and the client config ``pmcp setup --write`` emits -- goes through
:func:`atomic_write`. The write is atomic: the bytes go to a temporary file in
the destination's directory, are ``fsync``-ed, and are ``os.replace``-d over
the destination, so a write that fails partway leaves the old file byte-intact
(Consiliency/pmcp#248).

**A symlinked destination is written through, not replaced.** Users keep these
files in a dotfiles repository and symlink them into place. ``os.replace`` over
the *link* would swap the link for a regular file, after which the dotfiles copy
is never updated again and the two drift apart silently -- the regression the
#248 atomic write introduced against 2.7.3, whose plain ``open(path, "w")``
followed the link. So the destination is resolved first (``os.path.realpath``,
which follows a chain of links and relative links), the temporary is created in
the **target's** directory (``os.replace`` is only atomic within one
filesystem), and the replace lands on the **target**. The link itself is never
touched.

Two shapes are decided here rather than left to fall out:

* **A dangling link creates its target**, at the requested mode. That is what
  2.7.3 did, it is the ordinary dotfiles shape (a committed link to a
  git-ignored secrets file that does not exist yet on a fresh machine), and it
  agrees with the read side, which treats a store whose link target is gone as
  *not there* (``env_store._read_env_file_strict``). Only the target FILE is
  created: if the target's *directory* does not exist the write is refused with
  ``FileNotFoundError`` naming the target, because creating directories at an
  arbitrary link destination from a config write would be surprising -- and
  2.7.3 failed there too.
* **A symlink loop is refused** with ``ELOOP``. On Python 3.10-3.12
  ``os.path.realpath`` does not raise on a loop; it returns a path that is still
  a link, and replacing that would silently break the loop and leave a regular
  file where the operator's link was.

Callers keep their own directory-creation policy (each tightens only the
directories PMCP itself creates); a link's own directory necessarily exists.
The one deliberate exception is ``config.loader._atomic_write_json``: it writes
a project ``.mcp.json`` to a key pinned by an ``O_NOFOLLOW`` open, refuses a
symlinked ``.mcp.json`` outright (``symlinked_config``), and must not re-resolve
the path, so it does not use this helper.
"""

from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path


def resolve_write_target(path: Path) -> Path:
    """The file an atomic write of ``path`` must replace: ``path`` with links followed.

    Follows a chain of symlinks, relative or absolute, to the final target, which
    may not exist yet (a dangling link). Raises ``OSError(ELOOP)`` for a loop and
    ``FileNotFoundError`` when the target's directory does not exist.
    """
    target = os.path.realpath(path)
    if os.path.islink(target):
        # realpath gave up inside a loop and handed back a path that is still a
        # link (3.10-3.12 do not raise). Replacing it would break the loop.
        raise OSError(errno.ELOOP, "Symlink loop; refusing to write", str(path))
    target_parent = os.path.dirname(target)
    if not os.path.isdir(target_parent):
        raise FileNotFoundError(
            errno.ENOENT,
            f"Cannot write {path}: the directory of its symlink target {target} "
            "does not exist; create it, or point the link somewhere that exists",
            target_parent,
        )
    return Path(target)


def atomic_write(
    path: Path,
    data: bytes,
    *,
    mode: int = 0o600,
    prefix: str = ".pmcp-",
    suffix: str = ".tmp",
) -> Path:
    """Atomically replace the file at ``path`` (or its symlink target) with ``data``.

    The file is created or replaced at ``mode``; the temporary is ``fchmod``-ed
    before any byte is written, so the content is never readable at a looser
    mode. The file is ``fsync``-ed before the replace; the directory is
    ``fsync``-ed after it, best effort (macOS returns ``EINVAL``, some network
    and overlay mounts ``ENOTSUP``, and Windows cannot open a directory) --
    the replace has already landed by then, so failing would report a failed
    write that succeeded. On any failure before the replace the destination is
    untouched and the temporary is removed.

    Returns the path actually written: the symlink target when ``path`` is a link.
    """
    target = resolve_write_target(path)
    parent = target.parent
    fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=prefix, suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), mode)
            else:  # pragma: no cover - Windows has no fchmod
                os.chmod(tmp_name, mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise

    try:
        dir_fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return target
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)
    return target
