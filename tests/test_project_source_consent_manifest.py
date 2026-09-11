"""The project manifest overlay requires consent (SL-2, Consiliency/pmcp#230).

A checkout's own ``.pmcp/manifest.yaml`` used to replace a shipped server's
``command`` wholesale and to *insert* servers that never shipped -- so "clone a
repo and use GitHub" was code execution the operator never approved (S-03, and
the EC-CONSENT-6 seam: ``manifest.get_server(name)`` is the exact predicate
``tools/handlers.py`` uses to decide a server is manifest-backed).

The scope boundary is the point of the lane, and half these tests defend it:
only the **project** overlay is gated. ``~/.pmcp/manifest.yaml`` and
``$PMCP_MANIFEST_PATH`` are the operator's own files, not the repository's, and
must keep working with no trust record at all.

No fixture here approves anything implicitly. The autouse ``isolate_trust_store``
(``tests/conftest.py``) redirects ``HOME`` and deliberately records nothing, so a
refusal test that passes is agreeing with an empty store, not with a fixture.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from pmcp import trust_store
from pmcp.manifest import loader as loader_module
from pmcp.manifest.loader import load_manifest

#: A command no shipped server has, so "the overlay was applied" is unambiguous.
SENTINEL_COMMAND = "consent-sentinel-command"

#: A server name absent from the shipped manifest, for the insertion cases.
ADDED_SERVER = "consent-added-server"


@pytest.fixture(autouse=True)
def _isolate_overlay_discovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Neither overlay source is ambient: no env path, and a project-less cwd.

    ``HOME`` is already redirected by the autouse ``isolate_trust_store``, and
    ``Path.home()`` follows it on POSIX, so ``~/.pmcp/manifest.yaml`` is out of
    reach unless a test writes one. Each test opts back into exactly the source
    it is about.
    """
    monkeypatch.delenv("PMCP_MANIFEST_PATH", raising=False)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _replacement_yaml(server_name: str, command: str = SENTINEL_COMMAND) -> str:
    """An overlay that replaces ``server_name`` wholesale."""
    return f"""
servers:
  {server_name}:
    description: "overlay replacement"
    keywords: [consentkw]
    command: "{command}"
    args: []
"""


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def _make_project_overlay(tmp_path: Path, body: str) -> Path:
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    _write(overlay, body)
    return overlay


# --- an unapproved project overlay contributes nothing ------------------------


def test_unapproved_overlay_does_not_replace_shipped_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """EC-CONSENT-1: the shipped ``command`` survives an unapproved overlay."""
    shipped = load_manifest()
    name = next(iter(shipped.servers))
    shipped_command = shipped.servers[name].command

    overlay = _make_project_overlay(tmp_path, _replacement_yaml(name))
    monkeypatch.chdir(overlay.parent.parent)

    entry = load_manifest().get_server(name)
    assert entry is not None
    assert entry.command == shipped_command
    assert entry.command != SENTINEL_COMMAND


def test_unapproved_overlay_cannot_add_a_new_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """EC-CONSENT-6: ``servers.update`` also *inserts*, so addition is gated too."""
    overlay = _make_project_overlay(tmp_path, _replacement_yaml(ADDED_SERVER))
    monkeypatch.chdir(overlay.parent.parent)

    assert ADDED_SERVER not in load_manifest().servers


def test_an_added_server_from_an_unapproved_overlay_is_not_manifest_backed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``get_server`` is the manifest-backed predicate; it must answer ``None``.

    ``tools/handlers.py`` decides a server is manifest-backed by this call, so an
    overlay-inserted entry visible here would hand PKGID's default-deny exemption
    to a repository-supplied name.
    """
    overlay = _make_project_overlay(tmp_path, _replacement_yaml(ADDED_SERVER))
    monkeypatch.chdir(overlay.parent.parent)

    assert load_manifest().get_server(ADDED_SERVER) is None


def test_unapproved_overlay_server_env_patch_is_not_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ``server_env`` patch is a second way in, and is refused with the rest.

    It cannot create a server, but it can point a shipped server's environment at
    an attacker-chosen endpoint, which is the same class of change.
    """
    shipped = load_manifest()
    name = next(iter(shipped.servers))

    overlay = _make_project_overlay(
        tmp_path,
        f"""
server_env:
  {name}:
    CONSENT_PATCHED_URL: "http://attacker.internal:3002"
""",
    )
    monkeypatch.chdir(overlay.parent.parent)

    entry = load_manifest().get_server(name)
    assert entry is not None
    assert "CONSENT_PATCHED_URL" not in entry.extra_env


def test_unapproved_overlay_skip_logs_the_trust_approve_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The refusal is actionable: one WARNING naming the runnable command."""
    overlay = _make_project_overlay(tmp_path, _replacement_yaml(ADDED_SERVER))
    monkeypatch.chdir(overlay.parent.parent)

    with caplog.at_level(logging.WARNING, logger="pmcp.manifest.loader"):
        load_manifest()

    expected = f"pmcp trust approve {overlay.resolve()}"
    naming_the_overlay = [m for m in _warnings(caplog) if str(overlay.resolve()) in m]
    assert len(naming_the_overlay) == 1, naming_the_overlay
    assert expected in naming_the_overlay[0]


def test_an_unreadable_project_overlay_is_refused_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Unreadable is a refusal, and it must not also log the parser's own warning.

    Two warnings for one file would train an operator to skim them.
    """
    overlay = tmp_path / "proj" / ".pmcp" / "manifest.yaml"
    overlay.mkdir(parents=True)  # a directory: exists, cannot be read as bytes
    monkeypatch.chdir(overlay.parent.parent)

    with caplog.at_level(logging.WARNING, logger="pmcp.manifest.loader"):
        manifest = load_manifest()  # must not raise

    assert len(manifest.servers) > 0  # shipped servers still load
    naming_the_overlay = [m for m in _warnings(caplog) if str(overlay.resolve()) in m]
    assert len(naming_the_overlay) == 1, naming_the_overlay
    assert f"pmcp trust approve {overlay.resolve()}" in naming_the_overlay[0]


# --- an approved project overlay behaves exactly as it used to ----------------


def test_approved_overlay_is_applied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    approve_project_file: Callable[[Path], None],
) -> None:
    """Consent restores the old behaviour, replacement and insertion alike."""
    shipped = load_manifest()
    name = next(iter(shipped.servers))

    overlay = _make_project_overlay(
        tmp_path,
        f"""
servers:
  {name}:
    description: "overlay replacement"
    keywords: [consentkw]
    command: "{SENTINEL_COMMAND}"
    args: []
  {ADDED_SERVER}:
    description: "overlay addition"
    keywords: [consentkw]
    command: "{SENTINEL_COMMAND}"
    args: []
""",
    )
    approve_project_file(overlay)
    monkeypatch.chdir(overlay.parent.parent)

    manifest = load_manifest()
    assert manifest.servers[name].command == SENTINEL_COMMAND
    assert manifest.get_server(ADDED_SERVER) is not None


def test_editing_an_approved_overlay_revokes_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    approve_project_file: Callable[[Path], None],
) -> None:
    """EC-CONSENT-4: approval is of bytes, so the next edit needs a new approval."""
    shipped = load_manifest()
    name = next(iter(shipped.servers))
    shipped_command = shipped.servers[name].command

    overlay = _make_project_overlay(tmp_path, _replacement_yaml(name))
    approve_project_file(overlay)
    monkeypatch.chdir(overlay.parent.parent)

    assert load_manifest().servers[name].command == SENTINEL_COMMAND

    overlay.write_text(_replacement_yaml(name, command="edited-after-approval"))

    with caplog.at_level(logging.WARNING, logger="pmcp.manifest.loader"):
        entry = load_manifest().get_server(name)

    assert entry is not None
    assert entry.command == shipped_command
    assert any(
        f"pmcp trust approve {overlay.resolve()}" in m for m in _warnings(caplog)
    )


def test_the_parsed_bytes_are_the_gated_bytes_not_a_second_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    approve_project_file: Callable[[Path], None],
) -> None:
    """The loader must parse what the gate returned, never re-open the path.

    A loader that gates one read and opens the file again approves bytes nobody
    parses. Nothing in the suite above can see that -- on disk and in the gate the
    bytes are identical -- so this test makes them differ: the file is rewritten
    the instant the gate returns. A correct loader still applies the approved
    command; a re-reading one picks up the sentinel nobody approved.
    """
    shipped = load_manifest()
    name = next(iter(shipped.servers))

    overlay = _make_project_overlay(tmp_path, _replacement_yaml(name, "approved-cmd"))
    approve_project_file(overlay)
    monkeypatch.chdir(overlay.parent.parent)

    real_read_and_gate = loader_module.read_and_gate

    def rewrite_behind_the_gate(path: Path, kind: str) -> object:
        result = real_read_and_gate(path, kind)  # type: ignore[arg-type]
        overlay.write_text(_replacement_yaml(name, SENTINEL_COMMAND))
        return result

    monkeypatch.setattr(loader_module, "read_and_gate", rewrite_behind_the_gate)

    assert load_manifest().servers[name].command == "approved-cmd"


# --- scope boundary: the operator's own files are never gated (EC-CONSENT-5) ---


def test_user_scope_overlay_is_applied_without_any_trust_record(
    tmp_path: Path,
) -> None:
    """``~/.pmcp/manifest.yaml`` is the operator's own file, not the repository's."""
    _write(Path.home() / ".pmcp" / "manifest.yaml", _replacement_yaml(ADDED_SERVER))

    assert trust_store.list_records() == []

    entry = load_manifest().get_server(ADDED_SERVER)
    assert entry is not None
    assert entry.command == SENTINEL_COMMAND


def test_env_scope_manifest_path_is_applied_without_any_trust_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``$PMCP_MANIFEST_PATH`` is chosen by the operator's environment, not a repo."""
    env_overlay = tmp_path / "env-manifest.yaml"
    _write(env_overlay, _replacement_yaml(ADDED_SERVER))
    monkeypatch.setenv("PMCP_MANIFEST_PATH", str(env_overlay))

    assert trust_store.list_records() == []

    entry = load_manifest().get_server(ADDED_SERVER)
    assert entry is not None
    assert entry.command == SENTINEL_COMMAND
