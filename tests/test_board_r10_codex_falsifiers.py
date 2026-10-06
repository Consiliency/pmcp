"""Round-10 board falsifiers (codex F001-F002), as written."""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pmcp import trust_store
from pmcp.atomic_write import atomic_write


def test_existing_approval_survives_without_posix_directory_flags(tmp_path):
    home = tmp_path / "home"
    store = home / ".config" / "pmcp" / "trust.json"
    store.parent.mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    config = checkout / ".mcp.json"
    content = b"{}"
    config.write_bytes(content)
    store.write_text(
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
    windows = SimpleNamespace(
        **{
            name: getattr(os, name)
            for name in dir(os)
            if name not in {"O_DIRECTORY", "O_PATH"}
        }
    )
    windows.name = "nt"
    windows.supports_dir_fd = set()
    with (
        patch.object(Path, "home", return_value=home),
        patch.object(trust_store, "_checkout_roots", return_value=(checkout,)),
    ):
        assert trust_store.is_approved(config, content)
        with patch.object(trust_store, "os", windows):
            assert trust_store.is_approved(config, content)


def test_kernel_resolvable_chain_does_not_grow_past_path_max(tmp_path):
    (tmp_path / "d").mkdir()
    target = tmp_path / "target.env"
    before = b"KEEP=original\n"
    target.write_bytes(before)
    hop = tmp_path / "hop"
    hop.symlink_to("d/../" * 450 + "target.env")
    store = tmp_path / "pmcp.env"
    store.symlink_to("d/../" * 450 + "hop")
    assert store.read_bytes() == before
    fd = os.open(store, os.O_WRONLY)
    os.close(fd)
    after = before + b"NEW=value\n"
    atomic_write(store, after, confine_to=None)
    assert target.read_bytes() == after
    assert store.is_symlink() and hop.is_symlink()
