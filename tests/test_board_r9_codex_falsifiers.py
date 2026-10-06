"""Round-9 board falsifiers (codex F001-F003), as written.

Extracted from the codex leg verbatim and combined into one module; the shared
imports appear once.
"""

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
from pmcp import trust_store
import errno
from pmcp.atomic_write import atomic_write


def test_deep_checkout_cannot_approve_itself(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    config = checkout / ".mcp.json"
    content = b"{}"
    config.write_bytes(content)
    directory = checkout
    while len(os.fsencode(directory)) < 3800:
        directory = directory / ("d" * 19)
        directory.mkdir()
    planted = directory / "trust.json"
    planted.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "absolute_path": str(config),
                        "content_sha256": hashlib.sha256(content).hexdigest(),
                        "scope": "user",
                        "decision": "approved",
                        "recorded_at": "2026-10-05T00:00:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    home = tmp_path / "home"
    store = home / ".config" / "pmcp" / "trust.json"
    store.parent.mkdir(parents=True)
    store.symlink_to(planted)
    assert store.read_bytes() == planted.read_bytes()
    with (
        patch.object(Path, "home", return_value=home),
        patch.object(trust_store, "_checkout_roots", return_value=(checkout,)),
    ):
        assert not trust_store.is_approved(config, content)


def test_total_symlink_limit_cannot_overwrite_unreadable_store(tmp_path):
    directory = tmp_path / "real-config"
    directory.mkdir()
    alias = tmp_path / "config"
    alias.symlink_to(directory, target_is_directory=True)
    target = directory / "target.env"
    before = b"KEEP=original\n"
    target.write_bytes(before)
    names = ["pmcp.env"] + [f"hop{i}" for i in range(1, 40)]
    for name, destination in zip(names, names[1:] + ["target.env"]):
        (directory / name).symlink_to(destination)
    store = alias / "pmcp.env"
    try:
        store.read_bytes()
    except OSError as exc:
        assert exc.errno == errno.ELOOP
    else:
        raise AssertionError("kernel unexpectedly accepted 41 symlink hops")
    assert not store.exists()
    try:
        atomic_write(store, b"NEW=value\n", confine_to=None)
    except OSError:
        pass
    assert target.read_bytes() == before


def test_tempfile_uses_kernel_resolved_parent(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b" / "inner").mkdir(parents=True)
    (tmp_path / "b" / "nested").mkdir()
    (tmp_path / "a" / "jump").symlink_to(
        tmp_path / "b" / "inner", target_is_directory=True
    )
    target = tmp_path / "b" / "nested" / "target.env"
    target.write_bytes(b"KEEP=original\n")
    store = tmp_path / "pmcp.env"
    store.symlink_to("a/jump/../nested/target.env")
    assert store.read_bytes() == b"KEEP=original\n"
    os.close(os.open(store, os.O_WRONLY))
    atomic_write(store, b"NEW=value\n", confine_to=None)
    assert target.read_bytes() == b"NEW=value\n"
    assert store.is_symlink()
