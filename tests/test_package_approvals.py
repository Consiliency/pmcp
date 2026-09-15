"""The package-approval store and its `pmcp trust` verbs (IF-0-PKGID-2, #230).

The store answers one question for the provisioning gate -- has an operator
approved *this* package at *this* resolved version -- and every way of failing
to answer it must be a refusal. A record here is what lets `npx` run a package,
so the tests below treat the file as a security boundary, not a cache.

**Isolation is declared here, not inherited.** `tests/conftest.py` redirects
HOME for the trust store, but that fixture belongs to CONSENT, and the plan
requires this file to be safe regardless of execution order or of whether that
fixture exists. A test run that wrote to the operator's real
`~/.config/pmcp/package_approvals.json` would plant approvals that permit a real
provision, so `isolate_package_approvals` below repoints HOME itself and refuses
to run a test body if the redirect did not take.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import stat
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from pmcp import package_approvals
from pmcp.cli import async_main, parse_args
from pmcp.manifest.loader import ServerConfig
from pmcp.manifest.package_identity import PackageIdentity
from pmcp.package_approvals import (
    PackageApprovalError,
    approve_package,
    is_package_approved,
    list_package_approvals,
    package_approvals_path,
    revoke_package,
)
from pmcp.policy.policy import PolicyManager
from pmcp.provision_gate import evaluate_provision

#: Captured at import, before any fixture moves HOME.
_REAL_HOME = Path.home().resolve()
_REAL_STORE = _REAL_HOME / ".config" / "pmcp" / "package_approvals.json"


@pytest.fixture(autouse=True)
def isolate_package_approvals(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Repoint HOME for this file's own tests and prove the store moved."""
    real_store_existed = _REAL_STORE.exists()
    home = tmp_path_factory.mktemp("package-approvals-home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    store = package_approvals_path()
    if not store.is_relative_to(home.resolve()) or store.is_relative_to(_REAL_HOME):
        raise RuntimeError(
            f"Package approval store isolation failed: {store} is not under "
            f"{home}. Refusing to run a test that could write {_REAL_STORE}."
        )
    assert not store.exists()
    yield home
    assert _REAL_STORE.exists() == real_store_existed, (
        f"a test changed the operator's real store at {_REAL_STORE}"
    )


def _identity(
    name: str = "example-mcp",
    version: str = "1.2.3",
    integrity: str | None = "sha512-abc",
) -> PackageIdentity:
    return PackageIdentity(
        registry="npm", name=name, resolved_version=version, integrity=integrity
    )


def _pinned_config(identity: PackageIdentity) -> ServerConfig:
    spec = f"{identity.name}@{identity.resolved_version}"
    return ServerConfig(
        name="srv",
        description="",
        keywords=[],
        install={"linux": ["npx", "-y", spec]},
        command="npx",
        args=["-y", spec],
    )


def _run_cli(*argv: str) -> None:
    with patch("sys.argv", ["mcp-gateway", *argv]):
        args = parse_args()
    asyncio.run(async_main(args))


# ---------------------------------------------------------------------------
# Frozen (EC-PKGID-6)
# ---------------------------------------------------------------------------


def test_a_store_read_failure_denies_rather_than_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every way the store can be unreadable answers False, and the gate denies.

    Each case first records a genuine approval, so a False below is caused by
    the failure and not by an empty store.
    """
    identity = _identity()
    approve_package(identity)
    assert is_package_approved(identity) is True
    store = package_approvals_path()
    policy = PolicyManager()

    def assert_refused() -> None:
        assert is_package_approved(identity) is False
        decision = evaluate_provision(
            _pinned_config(identity), identity, source="discovered", policy=policy
        )
        assert decision.allowed is False

    # Corrupt JSON.
    good = store.read_bytes()
    store.write_text("{not json", encoding="utf-8")
    assert_refused()
    with pytest.raises(PackageApprovalError):
        list_package_approvals()

    # Valid JSON, wrong shape / unknown decision / missing field.
    for payload in (
        [],
        {"version": 1},
        {"version": 1, "records": "nope"},
        {
            "version": 1,
            "records": [dict(json.loads(good)["records"][0], decision="yes")],
        },
        {"version": 1, "records": [{"registry": "npm", "name": "example-mcp"}]},
    ):
        store.write_text(json.dumps(payload), encoding="utf-8")
        assert_refused()

    # A stored name/version that is not argv-safe is corruption, not a record --
    # and it poisons the whole store, not just itself. The genuine approval is
    # kept beside the tampered sibling, so only whole-store rejection refuses.
    for field, value in (
        ("resolved_version", "1.2.3;$(id)"),
        ("name", "example-mcp\x1b[2J"),
        ("registry", ""),
    ):
        tampered = json.loads(good)
        tampered["records"].append(dict(tampered["records"][0], **{field: value}))
        store.write_text(json.dumps(tampered), encoding="utf-8")
        assert_refused()
        with pytest.raises(PackageApprovalError):
            list_package_approvals()

    # Unreadable: a directory where the file should be.
    store.unlink()
    store.mkdir()
    assert_refused()
    store.rmdir()

    # Unusable configuration: the trust store refuses its own residency.
    store.write_bytes(good)
    assert is_package_approved(identity) is True

    def residency_refused() -> Path:
        raise package_approvals.TrustStoreError("inside checkout")

    # `context()`, never `undo()`: undo would also revert the HOME redirect.
    with monkeypatch.context() as m:
        m.setattr(package_approvals, "trust_store_path", residency_refused)
        assert_refused()
    assert is_package_approved(identity) is True

    # The gate itself must not propagate a store that raises (rule 8).
    def exploding(_identity: PackageIdentity) -> bool:
        raise OSError("disk on fire")

    with monkeypatch.context() as m:
        m.setattr("pmcp.provision_gate.is_package_approved", exploding)
        decision = evaluate_provision(
            _pinned_config(identity), identity, source="discovered", policy=policy
        )
    assert decision.allowed is False
    assert decision.reason == "not_approved"


# ---------------------------------------------------------------------------
# Store semantics
# ---------------------------------------------------------------------------


def test_an_approval_names_one_registry_name_and_resolved_version() -> None:
    identity = _identity()
    assert is_package_approved(identity) is False

    record = approve_package(identity)
    assert (record.registry, record.name, record.resolved_version) == (
        "npm",
        "example-mcp",
        "1.2.3",
    )
    assert record.decision == "approved"
    assert record.integrity == "sha512-abc"
    assert is_package_approved(identity) is True

    # A version bump is a new identity needing a new approval.
    assert is_package_approved(_identity(version="1.2.4")) is False
    # Same version, another package.
    assert is_package_approved(_identity(name="example-mcp-2")) is False
    # Same name and version from another registry.
    assert (
        is_package_approved(
            PackageIdentity("pypi", "example-mcp", "1.2.3", "sha512-abc")
        )
        is False
    )


def test_re_approving_replaces_rather_than_appends() -> None:
    approve_package(_identity(integrity=None))
    approve_package(_identity(integrity="sha512-abc"))
    records = list_package_approvals()
    assert len(records) == 1
    assert records[0].integrity == "sha512-abc"


def test_an_integrity_that_contradicts_the_record_is_not_approved() -> None:
    approve_package(_identity(integrity="sha512-abc"))
    assert is_package_approved(_identity(integrity="sha512-OTHER")) is False
    # An identity carrying no integrity cannot contradict the record.
    assert is_package_approved(_identity(integrity=None)) is True

    # A record carrying none approves whatever digest the registry reports.
    revoke_package("example-mcp")
    approve_package(_identity(integrity=None))
    assert is_package_approved(_identity(integrity="sha512-anything")) is True


def test_a_denied_record_is_not_approved() -> None:
    identity = _identity()
    approve_package(identity)
    store = package_approvals_path()
    data = json.loads(store.read_text(encoding="utf-8"))
    data["records"][0]["decision"] = "denied"
    store.write_text(json.dumps(data), encoding="utf-8")

    assert is_package_approved(identity) is False
    assert list_package_approvals()[0].decision == "denied"


def test_approve_refuses_an_identity_that_is_not_argv_safe() -> None:
    for bad in (
        _identity(version="latest"),
        _identity(version="^1.0.0"),
        _identity(version="1.0.0 --registry=evil"),
        _identity(name="-g"),
        _identity(name="a b"),
        PackageIdentity("", "example-mcp", "1.2.3", None),
    ):
        with pytest.raises(ValueError):
            approve_package(bad)
    assert not package_approvals_path().exists()


def test_revoke_drops_one_version_or_every_version() -> None:
    approve_package(_identity(version="1.0.0"))
    approve_package(_identity(version="2.0.0"))
    approve_package(_identity(name="other-mcp", version="1.0.0"))

    assert revoke_package("example-mcp", "1.0.0") is True
    assert revoke_package("example-mcp", "1.0.0") is False
    assert is_package_approved(_identity(version="2.0.0")) is True

    assert revoke_package("example-mcp") is True
    assert is_package_approved(_identity(version="2.0.0")) is False
    assert is_package_approved(_identity(name="other-mcp", version="1.0.0")) is True
    assert revoke_package("never-approved") is False


def test_the_store_is_user_scoped_beside_the_trust_store(
    isolate_package_approvals: Path,
) -> None:
    from pmcp.trust_store import trust_store_path

    assert package_approvals_path() == trust_store_path().parent / (
        "package_approvals.json"
    )
    assert package_approvals_path().is_relative_to(isolate_package_approvals.resolve())


def test_the_store_is_created_owner_only_at_every_new_component(
    isolate_package_approvals: Path,
) -> None:
    # Loosen the umask so a mode that merely *inherits* the default would show.
    previous = os.umask(0o002)
    try:
        approve_package(_identity())
    finally:
        os.umask(previous)

    store = package_approvals_path()
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    home = isolate_package_approvals.resolve()
    for component in (home / ".config", home / ".config" / "pmcp"):
        assert stat.S_IMODE(component.stat().st_mode) == 0o700, component


def test_a_failed_write_leaves_the_previous_store_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approve_package(_identity(version="1.0.0"))
    before = package_approvals_path().read_bytes()

    def broken_dump(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    with monkeypatch.context() as m, pytest.raises(OSError):
        m.setattr(package_approvals.json, "dump", broken_dump)
        approve_package(_identity(version="2.0.0"))

    assert package_approvals_path().read_bytes() == before
    leftovers = [
        p.name
        for p in package_approvals_path().parent.iterdir()
        if p.name.endswith(".tmp")
    ]
    assert leftovers == []


def test_a_directory_fsync_failure_does_not_fail_a_completed_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    store_dir = None

    def fsync(fd: int) -> None:
        if store_dir is not None and os.path.samestat(os.fstat(fd), os.stat(store_dir)):
            raise OSError(22, "EINVAL")
        real_fsync(fd)

    approve_package(_identity(version="0.0.1"))  # creates the directory
    store_dir = package_approvals_path().parent
    with monkeypatch.context() as m:
        m.setattr(package_approvals.os, "fsync", fsync)
        approve_package(_identity(version="1.0.0"))
    assert is_package_approved(_identity(version="1.0.0")) is True


def test_read_modify_write_holds_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second locker cannot get in between the read and the write.

    Without the lock a concurrent `approve` that read before a `revoke` wrote
    would write its stale snapshot back and resurrect the revoked approval.
    """
    approve_package(_identity(version="1.0.0"))
    lock_path = str(package_approvals_path()) + ".lock"
    real_write = package_approvals._write_store
    observed: list[bool] = []

    def probing_write(path: Path, records: list[object]) -> None:
        fd = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            observed.append(True)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            observed.append(False)
        finally:
            os.close(fd)
        real_write(path, records)  # type: ignore[arg-type]

    monkeypatch.setattr(package_approvals, "_write_store", probing_write)
    approve_package(_identity(version="2.0.0"))
    revoke_package("example-mcp", "1.0.0")
    assert observed == [True, True]


def test_concurrent_approve_cannot_resurrect_a_revoke() -> None:
    approve_package(_identity(version="1.0.0"))
    errors: list[BaseException] = []

    def approve_others() -> None:
        try:
            for i in range(30):
                approve_package(_identity(name=f"other-{i}", version="1.0.0"))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    thread = threading.Thread(target=approve_others)
    thread.start()
    assert revoke_package("example-mcp", "1.0.0") is True
    thread.join()

    assert errors == []
    assert is_package_approved(_identity(version="1.0.0")) is False
    assert len(list_package_approvals()) == 30


# ---------------------------------------------------------------------------
# `pmcp trust approve-package | list-packages | revoke-package`
# ---------------------------------------------------------------------------


def test_the_package_verbs_parse_beside_the_trust_verbs() -> None:
    for argv, verb in (
        (["trust", "approve-package", "pkg@1.0.0"], "approve-package"),
        (["trust", "list-packages"], "list-packages"),
        (["trust", "revoke-package", "pkg"], "revoke-package"),
        (["trust", "approve", "/tmp/x"], "approve"),
    ):
        with patch("sys.argv", ["mcp-gateway", *argv]):
            args = parse_args()
        assert args.command == "trust"
        assert args.trust_command == verb


def test_approve_package_cli_records_an_approval_the_gate_accepts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = _identity(name="@scope/example-mcp", version="2.0.0-rc.1")
    policy = PolicyManager()
    config = _pinned_config(identity)
    assert (
        evaluate_provision(config, identity, source="discovered", policy=policy).allowed
        is False
    )

    _run_cli("trust", "approve-package", "@scope/example-mcp@2.0.0-rc.1")
    assert "@scope/example-mcp@2.0.0-rc.1" in capsys.readouterr().out

    decision = evaluate_provision(config, identity, source="discovered", policy=policy)
    assert decision.allowed is True
    assert decision.reason == "package_approved"

    _run_cli("trust", "list-packages")
    listed = capsys.readouterr().out
    assert "@scope/example-mcp" in listed and "2.0.0-rc.1" in listed

    _run_cli("trust", "revoke-package", "@scope/example-mcp@2.0.0-rc.1")
    assert (
        evaluate_provision(config, identity, source="discovered", policy=policy).allowed
        is False
    )


def test_approve_package_cli_requires_one_exact_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for spec in (
        "example-mcp",
        "example-mcp@latest",
        "example-mcp@^1.0.0",
        "example-mcp@",
        "@1.0.0",
        "example-mcp@1.0.0;id",
    ):
        with pytest.raises(SystemExit) as exc:
            _run_cli("trust", "approve-package", spec)
        assert exc.value.code == 1, spec
        assert "Error:" in capsys.readouterr().err
    # A leading dash never reaches the store either (argparse reads it as a flag).
    with pytest.raises(SystemExit) as exc:
        _run_cli("trust", "approve-package", "-g@1.0.0")
    assert exc.value.code != 0
    assert not package_approvals_path().exists()


def test_list_packages_on_an_empty_or_corrupt_store(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_cli("trust", "list-packages")
    assert "No package approvals." in capsys.readouterr().out

    store = package_approvals_path()
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{broken", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _run_cli("trust", "list-packages")
    assert exc.value.code == 1


def test_revoke_package_cli_reports_nothing_to_revoke(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        _run_cli("trust", "revoke-package", "example-mcp")
    assert exc.value.code == 1
    assert "no package approval" in capsys.readouterr().err
