"""write_env_file writes atomically: an interrupted write never truncates the store.

See Consiliency/pmcp#248. The store once opened with ``O_TRUNC`` and wrote in a
separate step, so a write that failed partway (ENOSPC, a quota, a kill signal)
left the file truncated and its other entries gone. The write is now
write-to-temp + ``os.replace``; a failure before the rename leaves the existing
file byte-intact and removes the partial temp.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pmcp.env_store import read_env_file, write_env_file


def _temps(parent: Path) -> list[Path]:
    return [p for p in parent.iterdir() if p.name.startswith(".pmcp-env-")]


def test_an_interrupted_write_leaves_the_existing_store_intact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failure mid-write must not truncate or lose the store's existing entries.

    Inject the failure at ``os.fsync`` -- reached after the temp is written but
    BEFORE ``os.replace`` commits it. The atomic write aborts, the destination is
    never touched, and the partial temp is cleaned up. (Under the old
    ``O_TRUNC`` write, which never called ``fsync``, the write would instead
    succeed and clobber the file -- so this same test would not raise, which is
    the regression it pins.)
    """
    store = tmp_path / "store" / ".env"
    write_env_file(store, {"KEEP": "original-secret"})
    assert read_env_file(store) == {"KEEP": "original-secret"}

    real_fsync = os.fsync

    def boom(fd: int) -> None:
        raise OSError("simulated interrupted write")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        write_env_file(store, {"REPLACEMENT": "new-secret"})
    monkeypatch.setattr(os, "fsync", real_fsync)

    # The existing store is byte-intact -- not truncated, not the new content.
    assert read_env_file(store) == {"KEEP": "original-secret"}
    # No partial temp file was left behind.
    assert _temps(store.parent) == []


def test_a_successful_write_replaces_content_at_0600_with_no_temp_left(
    tmp_path: Path,
) -> None:
    """The happy path still replaces content, at mode 0600, leaving no temp."""
    store = tmp_path / "store" / ".env"
    write_env_file(store, {"A": "1"})
    write_env_file(store, {"B": "2"})

    assert read_env_file(store) == {"B": "2"}
    assert (os.stat(store).st_mode & 0o777) == 0o600
    assert _temps(store.parent) == []
