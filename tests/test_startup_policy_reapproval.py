"""Setting the startup policy carries the operator's trust approval forward (#253).

``pmcp startup add/set --source project`` rewrites the project ``.mcp.json``
through ``config.loader.set_startup_policy``. v13 trust approval is
content-keyed (``trust_store.is_approved(path, content)`` matches a SHA-256 of
the exact bytes), so that rewrite changes the bytes and would silently
invalidate the operator's own prior ``pmcp trust approve <.mcp.json>`` -- the
next startup then refuses the file. The owner decision is RE-RECORD: when the
pre-write bytes were approved, re-record an ``approved`` decision for the exact
new bytes, keyed on the opened descriptor's identity -- the resolved key is
accepted only when it names the same file (``st_dev``, ``st_ino``) the
descriptor holds open, so a swap or unlink is refused rather than mis-bound.

Each test drives the real ``set_startup_policy`` + ``trust_store``. The consent
races the cross-vendor panel surfaced each have a falsifier here:

* **byte-content TOCTOU** -- a post-write substitution of the file must not be
  what gets approved (``test_a_post_write_byte_substitution_is_not_approved``);
* **path-identity at record** -- a post-write symlink swap of the target must
  not transfer the approval to another file, and a symlinked ``.mcp.json`` is
  refused up front
  (``test_a_post_write_symlink_swap_does_not_transfer_and_a_symlink_is_refused``);
* **capture-vs-resolve ordering** -- a swap of the target *between* reading the
  input and resolving the key is refused, not bound
  (``test_a_capture_vs_resolve_swap_does_not_transfer_approval``);
* **unlink after open** -- a file removed once the descriptor is open is refused,
  not resurrected and approved at its stale key
  (``test_an_unlink_after_open_is_refused_not_resurrected``).

The accepted residual (a swap racing the atomic write itself, or a symlinked
final component on a platform without ``O_NOFOLLOW``) is documented in
``SECURITY.md`` C-38 as an accepted limitation, not a fail-safe; it is not tested
as closed because it is not.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from pmcp import cli, trust_store
from pmcp.config import loader
from pmcp.config.loader import load_configs, set_startup_policy
from pmcp.trust_store import TrustStoreError
from pmcp.types import StartupPolicyOperation


@pytest.fixture(autouse=True)
def _no_ambient_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ambient ``$PMCP_CONFIG``/``$PMCP_MANIFEST_PATH`` would add a source."""
    monkeypatch.delenv("PMCP_CONFIG", raising=False)
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)


# --------------------------------------------------------------------------
# Helpers -- deliberately NOT prefixed `test_`, so pytest does not collect them.
# --------------------------------------------------------------------------


def _write_json(path: Path, data: dict[str, object]) -> bytes:
    """Write ``data`` the way an operator's editor would and return the bytes."""
    payload = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    path.write_bytes(payload)
    return payload


def _rewrite_oracle(input_bytes: bytes, names: list[str]) -> bytes:
    """The exact bytes an ``add`` of ``names`` must write, computed INDEPENDENTLY
    of the writer -- never a disk re-read -- mirroring the loader's own
    ``json.dumps(data, indent=2) + "\\n"`` with ``autoStart`` mutated in place."""
    data = json.loads(input_bytes)
    current = set(data.get("autoStart") or [])
    data["autoStart"] = sorted(current | set(names))
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


def _add(project_root: Path, *names: str) -> object:
    return set_startup_policy(
        StartupPolicyOperation(
            operation="add",
            names=list(names),
            source="project",
            apply=True,
            dry_run=False,
        ),
        project_root=project_root,
        user_config_paths=[],
    )


def _approve(path: Path) -> None:
    """Record an approval for the file's current bytes, as an operator would."""
    trust_store.record(
        path, path.read_bytes(), trust_store.PROJECT_SCOPE, trust_store.APPROVED
    )


# --------------------------------------------------------------------------
# 1. Happy path: the approval is carried forward and the next load applies it.
# --------------------------------------------------------------------------


def test_happy_path_carries_the_approval_forward_and_the_next_load_applies_it(
    tmp_path: Path,
) -> None:
    mcp_json = tmp_path / ".mcp.json"
    _write_json(
        mcp_json,
        {
            "mcpServers": {"demo": {"command": "node", "args": ["demo.js"]}},
            "autoStart": [],
        },
    )
    _approve(mcp_json)

    preview = _add(tmp_path, "demo")

    assert preview.changed is True
    assert preview.after_autoStart == ["demo"]
    assert preview.approval_carried_forward is True
    # The approval now matches the exact NEW on-disk bytes.
    assert trust_store.is_approved(mcp_json, mcp_json.read_bytes()) is True
    # And the consent-gated loader applies the server with no refusal.
    configs = load_configs(project_root=tmp_path, user_config_paths=[])
    assert [c.name for c in configs] == ["demo"]
    assert configs[0].source == "project"


# --------------------------------------------------------------------------
# 2. A file that was never approved is not auto-approved by the write.
# --------------------------------------------------------------------------


def test_an_unapproved_file_is_not_auto_approved(tmp_path: Path) -> None:
    mcp_json = tmp_path / ".mcp.json"
    _write_json(
        mcp_json,
        {"mcpServers": {"demo": {"command": "node"}}, "autoStart": []},
    )
    # No approval recorded.

    preview = _add(tmp_path, "demo")

    assert preview.changed is True
    assert preview.approval_carried_forward is False
    assert trust_store.is_approved(mcp_json, mcp_json.read_bytes()) is False


# --------------------------------------------------------------------------
# 3. A dry run records nothing.
# --------------------------------------------------------------------------


def test_a_dry_run_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_json = tmp_path / ".mcp.json"
    original = _write_json(
        mcp_json,
        {"mcpServers": {"demo": {"command": "node"}}, "autoStart": []},
    )
    _approve(mcp_json)

    calls: list[Path] = []
    real_rr = trust_store.record_resolved

    def spy_record_resolved(
        resolved_path: Path, content: bytes, scope: str, decision: str
    ):
        calls.append(resolved_path)
        return real_rr(resolved_path, content, scope, decision)

    monkeypatch.setattr(trust_store, "record_resolved", spy_record_resolved)

    preview = set_startup_policy(
        StartupPolicyOperation(
            operation="add", names=["demo"], source="project", apply=False, dry_run=True
        ),
        project_root=tmp_path,
        user_config_paths=[],
    )

    assert preview.approval_carried_forward is False
    assert calls == []  # record_resolved never invoked on a dry run
    assert mcp_json.read_bytes() == original  # file untouched
    # The would-be new bytes were never approved.
    new_bytes = _rewrite_oracle(original, ["demo"])
    assert trust_store.is_approved(mcp_json, new_bytes) is False


# --------------------------------------------------------------------------
# 4. Byte-substitution (post-write TOCTOU): the WRITER'S returned bytes are what
#    is approved, keyed on the pinned path -- never a disk re-read.
# --------------------------------------------------------------------------


def test_a_post_write_byte_substitution_is_not_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_json = tmp_path / ".mcp.json"
    original = _write_json(
        mcp_json,
        {"mcpServers": {"demo": {"command": "node"}}, "autoStart": []},
    )
    _approve(mcp_json)

    attacker = b'{"mcpServers": {"evil": {"command": "sh"}}, "autoStart": ["evil"]}\n'

    # A concurrent process replaces the file immediately AFTER the writer returns.
    real_awj = loader._atomic_write_json

    def substituting_write(path: Path, data: dict) -> bytes:
        out = real_awj(path, data)
        mcp_json.write_bytes(attacker)
        return out

    monkeypatch.setattr(loader, "_atomic_write_json", substituting_write)

    # Spy the canonical-key record; forbid the re-resolving one.
    captured: dict[str, object] = {}
    real_rr = trust_store.record_resolved

    def spy_record_resolved(
        resolved_path: Path, content: bytes, scope: str, decision: str
    ):
        captured["path"] = resolved_path
        captured["content"] = content
        return real_rr(resolved_path, content, scope, decision)

    def forbid_record(*args: object, **kwargs: object):
        raise AssertionError(
            "set_startup_policy must record via record_resolved(pinned_key, ...), "
            "never the re-resolving record()"
        )

    monkeypatch.setattr(trust_store, "record_resolved", spy_record_resolved)
    monkeypatch.setattr(trust_store, "record", forbid_record)

    preview = _add(tmp_path, "demo")

    oracle = _rewrite_oracle(original, ["demo"])
    assert preview.approval_carried_forward is True
    # Recorded against the pinned canonical path, with the writer-returned bytes.
    assert captured["path"] == mcp_json.resolve()
    assert captured["content"] == oracle
    # The substituted attacker bytes are NOT approved; the writer's bytes are.
    assert trust_store.is_approved(mcp_json, attacker) is False
    assert trust_store.is_approved(mcp_json, oracle) is True


# --------------------------------------------------------------------------
# 5. Symlink-substitution (post-write path-identity race) + up-front refusal.
# --------------------------------------------------------------------------


def test_a_post_write_symlink_swap_does_not_transfer_and_a_symlink_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_json = tmp_path / ".mcp.json"
    _write_json(
        mcp_json,
        {"mcpServers": {"demo": {"command": "node"}}, "autoStart": []},
    )
    _approve(mcp_json)

    other = tmp_path / "other.json"  # a previously-UNAPPROVED file

    real_awj = loader._atomic_write_json
    written: dict[str, bytes] = {}

    def swapping_write(path: Path, data: dict) -> bytes:
        out = real_awj(path, data)
        written["bytes"] = out
        # Attacker replaces target.path with a symlink to `other`, whose contents
        # are made equal to the just-written bytes.
        other.write_bytes(out)
        mcp_json.unlink()
        os.symlink(other, mcp_json)
        return out

    monkeypatch.setattr(loader, "_atomic_write_json", swapping_write)

    preview = _add(tmp_path, "demo")

    assert preview.approval_carried_forward is True
    out = written["bytes"]
    # Consent was recorded against the pinned path, NOT transferred to `other`.
    assert trust_store.is_approved(other, out) is False
    # Nor is it reachable by resolving the now-symlinked path to `other`.
    assert trust_store.is_approved(mcp_json, out) is False

    # A symlinked `.mcp.json` presented UP FRONT is refused, and nothing written.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    real_target = fresh / "real.json"
    real_before = _write_json(
        real_target, {"mcpServers": {"demo": {"command": "node"}}, "autoStart": []}
    )
    link = fresh / ".mcp.json"
    os.symlink(real_target, link)

    refused = _add(fresh, "demo")
    assert refused.ok is False
    assert [d.code for d in refused.diagnostics] == ["invalid_source"]
    assert "symlink" in refused.diagnostics[0].message.lower()
    assert real_target.read_bytes() == real_before  # the pointed-at file untouched


# --------------------------------------------------------------------------
# 6. Checkout-resident store: a TrustStoreError from record_resolved becomes a
#    diagnostic, ok stays True, nothing raises.
#
# A genuinely checkout-resident store makes BOTH is_approved (fail-closed False)
# and record raise/refuse together, so `was_approved` would be False and the
# record branch never reached. The branch under test is the post-#252 contract
# that record_resolved raises TrustStoreError on an unusable store; we induce
# exactly that, with a normally-approved file, so the try/except -> diagnostic
# path is exercised deterministically.
# --------------------------------------------------------------------------


def test_a_record_failure_yields_a_diagnostic_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_json = tmp_path / ".mcp.json"
    _write_json(
        mcp_json,
        {"mcpServers": {"demo": {"command": "node"}}, "autoStart": []},
    )
    _approve(mcp_json)

    def raising_record_resolved(*args: object, **kwargs: object):
        raise TrustStoreError("store resides inside the checkout enclosing the file")

    monkeypatch.setattr(trust_store, "record_resolved", raising_record_resolved)

    preview = _add(tmp_path, "demo")  # must NOT raise

    assert preview.ok is True
    assert preview.changed is True
    assert preview.approval_carried_forward is False
    assert [d.code for d in preview.diagnostics] == ["approval_not_carried_forward"]
    # The file was still written -- the operator's edit is not lost.
    assert json.loads(mcp_json.read_bytes())["autoStart"] == ["demo"]


# --------------------------------------------------------------------------
# 7. Capture-vs-resolve substitution (panel finding, codex): a swap of the
#    target between capturing its bytes and resolving its key must not bind a
#    different, separately-approved file.
# --------------------------------------------------------------------------


def test_a_capture_vs_resolve_swap_does_not_transfer_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_json = tmp_path / ".mcp.json"  # file A
    input_bytes = _write_json(
        mcp_json,
        {"mcpServers": {"demo": {"command": "node"}}, "autoStart": []},
    )
    # File B currently holds the PLANNED-REWRITE bytes O, but its stored approval
    # is for A's input bytes I (not O).
    rewrite = _rewrite_oracle(input_bytes, ["demo"])  # bytes O
    other = tmp_path / "other.json"  # file B
    other.write_bytes(rewrite)
    trust_store.record(
        other, input_bytes, trust_store.PROJECT_SCOPE, trust_store.APPROVED
    )

    moved = tmp_path / ".mcp.json.moved"

    # The swap must land in the window BETWEEN the descriptor's identity being
    # captured (os.fstat, right after open) and the key being resolved. The
    # helper reads the descriptor with an explicit os.read between those two
    # steps, so arm after load_config_sources' own gate read and fire on the
    # first armed os.read, AFTER it returns the real bytes:
    #   * correct code: fstat already captured file A's identity, so after the
    #     swap `resolve(target)` names B, os.stat(B) != fstat(A) -> refused, B
    #     never bound;
    #   * a resolve-without-identity-check binds B (which carries a stored
    #     approval for the captured bytes) and wrongly approves B's contents.
    state = {"armed": False, "fired": False}
    real_load = loader.load_config_sources

    def load_and_arm(*a: object, **k: object):
        result = real_load(*a, **k)
        state["armed"] = True
        return result

    monkeypatch.setattr(loader, "load_config_sources", load_and_arm)

    real_read = os.read

    def read_and_swap(fd: int, n: int) -> bytes:
        data = real_read(fd, n)
        if state["armed"] and not state["fired"]:
            state["fired"] = True
            # Rename A aside (keeps its inode alive for the open descriptor) and
            # point the target path at B -- AFTER the bytes are read, BEFORE the
            # key is resolved.
            os.rename(mcp_json, moved)
            os.symlink(other, mcp_json)
        return data

    monkeypatch.setattr(os, "read", read_and_swap)

    _add(tmp_path, "demo")

    assert state["fired"] is True  # the swap really happened during the operation
    # B's contents O must NOT be approved: the resolved key was verified against
    # the opened descriptor's inode, so the swap is refused rather than bound.
    assert trust_store.is_approved(other, other.read_bytes()) is False
    assert trust_store.is_approved(other, rewrite) is False


# --------------------------------------------------------------------------
# 8. The startup rewrite and `pmcp trust approve` share ONE scope constant --
#    resolved at use, so a same-value divergence is still caught.
# --------------------------------------------------------------------------


def test_startup_and_trust_approve_share_one_scope_constant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Redirect the single source of truth to a sentinel. If either site copied a
    # literal instead of reading `trust_store.PROJECT_SCOPE` at use time, that
    # site records the old value and diverges -- caught even when the copied
    # literal equals the original (string interning made an `is` check useless).
    monkeypatch.setattr(trust_store, "PROJECT_SCOPE", "SENTINEL_SHARED_SCOPE")

    # `pmcp trust approve <file>`.
    approved = tmp_path / "approved.json"
    approved.write_bytes(b"{}\n")
    cli._run_trust_approve(argparse.Namespace(path=str(approved)))
    approve_rec = next(
        r for r in trust_store.list_records() if r.absolute_path == approved.resolve()
    )

    # Startup-policy carry-forward re-record.
    mcp_json = tmp_path / ".mcp.json"
    _write_json(
        mcp_json,
        {"mcpServers": {"demo": {"command": "node"}}, "autoStart": []},
    )
    _approve(mcp_json)
    _add(tmp_path, "demo")
    startup_rec = next(
        r for r in trust_store.list_records() if r.absolute_path == mcp_json.resolve()
    )

    assert approve_rec.scope == "SENTINEL_SHARED_SCOPE"
    assert startup_rec.scope == "SENTINEL_SHARED_SCOPE"


# --------------------------------------------------------------------------
# 9. Unlink-between-open-and-resolve boundary (codex finding on /proc pathnames):
#    a file removed after the descriptor is opened is refused, not resurrected
#    and approved at its stale key.
# --------------------------------------------------------------------------


def test_an_unlink_after_open_is_refused_not_resurrected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_json = tmp_path / ".mcp.json"
    _write_json(
        mcp_json,
        {"mcpServers": {"demo": {"command": "node"}}, "autoStart": []},
    )
    _approve(mcp_json)  # the operator HAD approved it, so a stale-key lookup hits

    state = {"armed": False, "fired": False}
    real_load = loader.load_config_sources

    def load_and_arm(*a: object, **k: object):
        result = real_load(*a, **k)
        state["armed"] = True
        return result

    monkeypatch.setattr(loader, "load_config_sources", load_and_arm)

    real_read = os.read

    def read_then_unlink(fd: int, n: int) -> bytes:
        data = real_read(fd, n)
        if state["armed"] and not state["fired"]:
            state["fired"] = True
            mcp_json.unlink()  # gone after the descriptor is open, before resolve
        return data

    monkeypatch.setattr(os, "read", read_then_unlink)

    preview = _add(tmp_path, "demo")

    assert state["fired"] is True
    # The identity check refuses rather than writing/approving at a stale key.
    assert preview.ok is False
    assert [d.code for d in preview.diagnostics] == ["unpinnable_config"]
    # The file was NOT resurrected, and nothing is approved for that path.
    assert not mcp_json.exists()
    assert (
        trust_store.is_approved(
            mcp_json, _rewrite_oracle(b'{"autoStart": []}', ["demo"])
        )
        is False
    )
