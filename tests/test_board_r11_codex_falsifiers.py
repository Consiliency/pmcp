"""Round-11 board falsifier (codex F001), as written (the review tree's
`sys.path.insert` dropped; pmcp is installed here)."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import SkipTest
from unittest.mock import patch

from pmcp import atomic_write as writer, package_approvals, trust_store


def test_no_o_path_preserves_existing_package_approval(tmp_path):
    if not writer._DIR_FD_SUPPORTED or os.geteuid() == 0:
        raise SkipTest("requires an unprivileged POSIX directory walk")
    home = tmp_path / "home"
    store = home / ".config" / "pmcp" / "package_approvals.json"
    store.parent.mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    target = vault / "approvals.json"
    payload = json.dumps(
        {
            "version": 1,
            "records": [
                {
                    "registry": "npm",
                    "name": "example-mcp",
                    "resolved_version": "1.2.3",
                    "integrity": None,
                    "decision": "approved",
                    "recorded_at": "2026-10-05T00:00:00+00:00",
                }
            ],
        }
    ).encode()
    target.write_bytes(payload)
    store.symlink_to(target)
    identity = SimpleNamespace(
        registry="npm",
        name="example-mcp",
        resolved_version="1.2.3",
        integrity=None,
    )
    no_o_path = SimpleNamespace(
        **{name: getattr(os, name) for name in dir(os) if name != "O_PATH"}
    )
    with (
        patch.object(Path, "home", return_value=home),
        patch.object(trust_store, "_checkout_roots", return_value=(checkout,)),
        patch.object(trust_store, "os", no_o_path),
        patch.object(writer, "_O_PATH", 0),
    ):
        assert package_approvals.is_package_approved(identity)
        vault.chmod(0o311)
        try:
            assert store.read_bytes() == payload
            writer.atomic_write(store, payload, confine_to=None)
            assert store.is_symlink() and target.read_bytes() == payload
            assert package_approvals.is_package_approved(identity), (
                "A readable, atomically writable approval must remain usable "
                "when its directory permits search and write but not listing"
            )
        finally:
            vault.chmod(0o700)
