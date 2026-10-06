"""Round-8 board falsifiers (codex F001-F003), as written.

Extracted from the codex leg verbatim and combined into one module; the shared
imports appear once, and the `sys.path.insert` of the review tree is dropped
(pmcp is installed in the test environment).
"""

import hashlib
import json
import os
from pmcp import trust_store
import errno
from pmcp.env_store import set_env_value
from pmcp.atomic_write import atomic_write, read_confined


def test_double_slash_does_not_authorize_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    home = tmp_path / "home"
    store = home / ".config" / "pmcp" / "trust.json"
    store.parent.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(trust_store, "_active_project_root", checkout.resolve())
    assert checkout in trust_store._checkout_roots()

    config = checkout / ".mcp.json"
    content = b"{}"
    config.write_bytes(content)
    planted = checkout / "planted-trust.json"
    planted.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "absolute_path": str(config),
                        "content_sha256": hashlib.sha256(content).hexdigest(),
                        "scope": trust_store.PROJECT_SCOPE,
                        "decision": "approved",
                        "recorded_at": "2026-10-05T00:00:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    store.symlink_to(str(planted))
    assert not trust_store.is_approved(config, content)
    store.unlink()
    store.symlink_to("/" + str(planted))
    assert os.path.samefile(store, planted)
    assert not trust_store.is_approved(config, content)


def test_invalid_project_hops_never_rewrite_another_store(tmp_path):
    failures = []
    for kind in ("missing", "file"):
        base = tmp_path / kind
        existing = base / "existing"
        existing.mkdir(parents=True)
        store = existing / ".env.pmcp"
        before = b"KEEP1=alpha\nKEEP2=beta\n" if kind == "missing" else None
        if before is not None:
            store.write_bytes(before)
        if kind == "file":
            (base / "hop").write_bytes(b"not a directory")
        project = base / "hop" / ".." / "existing"
        try:
            os.stat(project)
        except OSError as exc:
            assert exc.errno == (errno.ENOENT if kind == "missing" else errno.ENOTDIR)
        else:
            raise AssertionError("kernel unexpectedly accepted the project path")
        try:
            set_env_value("project", "NEW", "value", project)
        except OSError:
            pass
        else:
            failures.append(f"{kind}: accepted an invalid project path")
        after = store.read_bytes() if store.exists() else None
        if after != before:
            failures.append(f"{kind}: rewrote the unrelated store: {after!r}")
    assert failures == [], failures


def test_dotdot_cannot_skip_directory_search_permission(tmp_path):
    if not hasattr(os, "O_PATH") or os.geteuid() == 0:
        import pytest

        pytest.skip("requires Linux O_PATH and an unprivileged user")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    target = tmp_path / "real.env"
    before = b"KEEP=original\n"
    target.write_bytes(before)
    store = tmp_path / ".env.pmcp"
    store.symlink_to("blocked/../real.env")
    blocked.chmod(0)
    failures = []
    try:
        try:
            with store.open("rb"):
                pass
        except OSError as exc:
            assert exc.errno == errno.EACCES
        else:
            raise AssertionError("kernel unexpectedly accepted the link")
        actions = {
            "read": lambda: read_confined(store, tmp_path),
            "write": lambda: atomic_write(store, b"NEW=value\n", confine_to=tmp_path),
        }
        for name, action in actions.items():
            try:
                action()
            except OSError as exc:
                assert exc.errno == errno.EACCES
            else:
                failures.append(name)
        assert failures == [], f"accepted without search permission: {failures}"
        assert target.read_bytes() == before
    finally:
        blocked.chmod(0o700)
