"""Tests for the ``pmcp trust`` CLI verbs (Consiliency/pmcp#230, EC-TRUST-4).

Every test drives the real entry path -- ``parse_args`` over an argv, then
``async_main`` -- rather than calling the command function directly. That is
deliberate: ``async_main``'s fallthrough is "run the gateway server", so a test
that hand-built a ``Namespace`` would stay green even if the dispatch branch
were missing, while ``pmcp trust approve ...`` silently started a server.

The verb spelling is a contract, not a preference: downstream refusal messages
print the runnable string ``pmcp trust approve <absolute path>``, so
``test_the_frozen_verb_spelling_parses`` guards it at the parser.

Isolation: the store is user-scoped at ``~/.config/pmcp/trust.json``. The
``trust_home`` fixture repoints ``$HOME`` at a tmp directory and *asserts* the
redirect took before any test body runs, so a test run cannot record a real
approval on the machine it runs on.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from pmcp import trust_store
from pmcp.cli import async_main, parse_args


@pytest.fixture
def trust_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Repoint the user-scoped trust store into ``tmp_path`` and prove it moved."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    store = trust_store.trust_store_path()
    assert store.is_relative_to(home.resolve()), (
        f"trust store did not follow $HOME: {store} is outside {home}. "
        "Refusing to run -- this test would write the operator's real store."
    )
    assert not store.exists()
    yield home


def run_cli(*argv: str) -> None:
    """Run one ``pmcp`` invocation exactly as ``main()`` would dispatch it."""
    with patch("sys.argv", ["mcp-gateway", *argv]):
        args = parse_args()
    asyncio.run(async_main(args))


def test_the_frozen_verb_spelling_parses(tmp_path: Path) -> None:
    """`trust approve|list|revoke` are the spellings downstream messages name."""
    with patch("sys.argv", ["mcp-gateway", "trust", "approve", str(tmp_path / "f")]):
        approve = parse_args()
    assert approve.command == "trust"
    assert approve.trust_command == "approve"
    assert approve.path == tmp_path / "f"

    with patch("sys.argv", ["mcp-gateway", "trust", "list"]):
        listing = parse_args()
    assert (listing.command, listing.trust_command) == ("trust", "list")

    with patch("sys.argv", ["mcp-gateway", "trust", "revoke", str(tmp_path / "f")]):
        revoke = parse_args()
    assert (revoke.command, revoke.trust_command) == ("trust", "revoke")
    assert revoke.path == tmp_path / "f"


def test_approve_records_the_file_and_makes_its_content_approved(
    trust_home: Path, tmp_path: Path
) -> None:
    """`approve` writes a record the store's own predicate then honours."""
    target = tmp_path / "server.json"
    target.write_bytes(b'{"ok": true}')

    run_cli("trust", "approve", str(target))

    records = trust_store.list_records()
    assert len(records) == 1
    assert records[0].absolute_path == target.resolve()
    assert records[0].decision == "approved"
    assert trust_store.is_approved(target, b'{"ok": true}') is True
    assert trust_store.is_approved(target, b'{"ok": false}') is False


def test_approve_replaces_the_record_when_the_file_changes(
    trust_home: Path, tmp_path: Path
) -> None:
    """Re-approving edited content replaces the digest; it does not append."""
    target = tmp_path / "server.json"
    target.write_bytes(b"first")
    run_cli("trust", "approve", str(target))

    target.write_bytes(b"second")
    run_cli("trust", "approve", str(target))

    records = trust_store.list_records()
    assert len(records) == 1
    assert trust_store.is_approved(target, b"second") is True
    assert trust_store.is_approved(target, b"first") is False


def test_approve_on_a_missing_file_exits_non_zero_and_records_nothing(
    trust_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Approving what cannot be read is a refusal, not an empty approval."""
    missing = tmp_path / "not-here.json"

    with pytest.raises(SystemExit) as exc_info:
        run_cli("trust", "approve", str(missing))

    assert exc_info.value.code != 0
    assert str(missing) in capsys.readouterr().err
    assert trust_store.list_records() == []


def test_approve_records_the_absolute_path_for_a_relative_argument(
    trust_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store is keyed on the resolved path however the operator spelled it."""
    work = tmp_path / "work"
    work.mkdir()
    target = work / "server.json"
    target.write_bytes(b"payload")
    monkeypatch.chdir(work)

    run_cli("trust", "approve", "server.json")

    records = trust_store.list_records()
    assert [rec.absolute_path for rec in records] == [target.resolve()]


def test_list_names_the_path_and_the_decision(
    trust_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`list` is the operator's read of the store, so it must print both."""
    target = tmp_path / "server.json"
    target.write_bytes(b"payload")
    run_cli("trust", "approve", str(target))
    capsys.readouterr()

    run_cli("trust", "list")

    out = capsys.readouterr().out
    assert str(target.resolve()) in out
    assert "approved" in out


def test_list_on_an_empty_store_says_so_and_exits_zero(
    trust_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing approved is the normal starting state, not an error."""
    run_cli("trust", "list")

    out = capsys.readouterr().out
    assert out.strip() != ""
    assert trust_store.list_records() == []


def test_revoke_removes_the_record_so_the_content_is_no_longer_approved(
    trust_home: Path, tmp_path: Path
) -> None:
    """`revoke` returns the path to absent, which `is_approved` reports as False."""
    target = tmp_path / "server.json"
    target.write_bytes(b"payload")
    run_cli("trust", "approve", str(target))
    assert trust_store.is_approved(target, b"payload") is True

    run_cli("trust", "revoke", str(target))

    assert trust_store.list_records() == []
    assert trust_store.is_approved(target, b"payload") is False


def test_revoke_of_an_unrecorded_path_exits_non_zero_and_keeps_other_records(
    trust_home: Path, tmp_path: Path
) -> None:
    """Revoking nothing reports failure rather than a misleading success."""
    approved = tmp_path / "approved.json"
    approved.write_bytes(b"payload")
    run_cli("trust", "approve", str(approved))

    with pytest.raises(SystemExit) as exc_info:
        run_cli("trust", "revoke", str(tmp_path / "never-approved.json"))

    assert exc_info.value.code != 0
    assert [rec.absolute_path for rec in trust_store.list_records()] == [
        approved.resolve()
    ]
