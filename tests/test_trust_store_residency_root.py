"""SEAL SL-7: the checkout-residency guard keys on the SERVED project root.

See Consiliency/pmcp#251, #230. The trust store refuses to live inside "the
checkout being judged" so a repository cannot ship an approval for its own
``.mcp.json`` (EC-TRUST-5). But that guard resolved the checkout from
``Path.cwd()``, so it asked "is the store inside the checkout *this process's
cwd* is in" rather than "inside the repo the gateway is SERVING". Consequence,
reproduced on this base: ``pmcp serve --project <checkout>`` launched from any
*other* directory did not refuse a store planted inside that checkout, and the
config loader returned the checkout's own server -- EC-TRUST-5 reopened,
cwd-dependent.

The fix binds a process-scoped "active project root" that ``pmcp serve --project
X`` / ``pmcp status --project X`` set at startup (``set_active_project_root``);
``trust_store`` then judges residency against the served root **in addition to**
the cwd walk, and against cwd alone when nothing is bound (so the ``pmcp trust``
verbs, which legitimately run inside a checkout, keep using cwd).

These tests drive the *real* consumers -- ``load_configs`` and the ``run_status``
CLI handler -- not ``_checkout_roots`` alone, and every refusal carries a
positive control so "refused" is never confused with "nothing was approved".
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pmcp import cli, trust_store
from pmcp.config.loader import load_configs
from pmcp.trust_store import TrustStoreError

#: A project ``.mcp.json`` a hostile repository would like the gateway to run.
_PWNED = json.dumps(
    {"mcpServers": {"repo-server": {"command": "sh", "args": ["-c", "echo pwned"]}}}
)


@pytest.fixture(autouse=True)
def _reset_active_project_root() -> Iterator[None]:
    """Contain the process-global these tests bind, so none can leak to another.

    ``set_active_project_root`` is a module global on purpose: the residency
    guard is reached through ``is_approved`` deep inside the config loader,
    below any signature a served root could be threaded through. These tests set
    it the way ``pmcp serve --project X`` does; without this reset an escaped
    binding would make a later test judge residency against a stale root. The
    production CLI clears it itself (``run_status`` in a ``finally``; a fresh
    ``pmcp serve`` process rebinds), so this only guards the test process.
    """
    trust_store.set_active_project_root(None)
    try:
        yield
    finally:
        trust_store.set_active_project_root(None)


def _checkout(tmp_path: Path, name: str = "checkout") -> Path:
    """A directory every marker-based walk-up here treats as a clone."""
    path = tmp_path / name
    (path / ".git").mkdir(parents=True, exist_ok=True)
    return path


def _repo_mcp_json(checkout: Path) -> Path:
    """The project config the repository ships and would like applied."""
    path = checkout / ".mcp.json"
    path.write_text(_PWNED, encoding="utf-8")
    return path


def _ship_an_approval_inside(
    checkout: Path, target: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Record a real, matching approval into a store that lives inside ``checkout``.

    Written by ``trust_store.record`` itself, from a working directory *outside*
    the checkout, because that is the only way the production writer produces
    these bytes with no served root bound (a repository ships bytes, not a
    call). The record is genuine and matches ``target`` exactly, so a later
    refusal is attributable to residency and not to an absent or stale record.
    """
    home = checkout / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(outside)
    trust_store.record(target, target.read_bytes(), "project", trust_store.APPROVED)
    store = trust_store.trust_store_path()
    assert store.is_relative_to(checkout.resolve())
    return store


# --------------------------------------------------------------------------- #
# The fix: serving a checkout refuses that checkout's own store, from anywhere.
# --------------------------------------------------------------------------- #


def test_serving_a_project_refuses_its_checkout_resident_store_from_elsewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``serve --project <checkout>`` from another directory must refuse it.

    This is the reopened hole. The store holds a real ``approved`` record for
    the checkout's own ``.mcp.json``; the launch directory is *outside* the
    checkout, so the old cwd walk found no checkout and let the record grant.
    With the served root bound, the store is refused, ``is_approved`` fails
    closed, the loader drops the server, and the operator sees the consent
    gate's remediation line.
    """
    checkout = _checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    config = _repo_mcp_json(checkout)
    _ship_an_approval_inside(checkout, config, outside, monkeypatch)

    # Launched from outside the checkout, serving the checkout.
    monkeypatch.chdir(outside)
    trust_store.set_active_project_root(checkout)

    # The store is now genuinely refused, naming the served checkout -- not
    # answered as "no record".
    with pytest.raises(TrustStoreError) as raised:
        trust_store.trust_store_path()
    assert str(checkout.resolve()) in str(raised.value)
    assert trust_store.is_approved(config, config.read_bytes()) is False

    # The real consumer drops the repo's server ...
    with caplog.at_level(logging.WARNING):
        names = [
            c.name for c in load_configs(project_root=checkout, user_config_paths=[])
        ]
    assert "repo-server" not in names

    # ... and what surfaces to the operator is the consent gate's remediation,
    # the SAME message an absent record produces (that ambiguity is SL-5's to
    # document; here we pin the exact text).
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert "it has not been approved" in warning
    assert "pmcp trust approve" in warning


def test_serving_still_loads_an_operator_store_outside_the_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Positive control: a normal operator store keeps working under ``--project``.

    The operator's store lives under a home OUTSIDE any repository and holds a
    legitimate approval for the served checkout's ``.mcp.json``. Binding the
    served root must not turn this into a refusal -- the residency rule binds
    where the store *lives*, and this store lives nowhere near the checkout.
    """
    checkout = _checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    operator_home = tmp_path / "operator-home"
    operator_home.mkdir()
    config = _repo_mcp_json(checkout)

    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.chdir(outside)
    trust_store.record(config, config.read_bytes(), "project", trust_store.APPROVED)

    trust_store.set_active_project_root(checkout)

    # Not refused: the store is outside the served checkout.
    store = trust_store.trust_store_path()
    assert not store.is_relative_to(checkout.resolve())
    assert trust_store.is_approved(config, config.read_bytes()) is True

    granted = [
        c.name for c in load_configs(project_root=checkout, user_config_paths=[])
    ]
    assert "repo-server" in granted


def test_serving_one_project_still_refuses_a_store_resident_in_the_launch_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The served root is checked IN ADDITION to cwd, never instead of it.

    ``pmcp serve --project X`` launched from inside a *second* checkout ``Y``
    whose store resolves into ``Y`` (the dotfiles-symlink shape TRUST treats as
    hostile) must still refuse ``Y``'s store -- a store keyed by path can carry
    an approval for ``X/.mcp.json``. Keying residency only on the served root
    would reopen exactly this. The union closes it.
    """
    served = _checkout(tmp_path, "served")
    launch = _checkout(tmp_path, "launch")
    launch_home = launch / "home"
    launch_home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # The served checkout's config, approved by a store that lives in the
    # LAUNCH checkout (written from outside, with no root bound).
    served_config = _repo_mcp_json(served)
    monkeypatch.setenv("HOME", str(launch_home))
    monkeypatch.chdir(outside)
    trust_store.record(
        served_config, served_config.read_bytes(), "project", trust_store.APPROVED
    )
    assert trust_store.trust_store_path().is_relative_to(launch.resolve())

    # Launched from inside the launch checkout, serving the other one.
    monkeypatch.chdir(launch)
    trust_store.set_active_project_root(served)

    with pytest.raises(TrustStoreError) as raised:
        trust_store.trust_store_path()
    # Refused for residency in the LAUNCH checkout, which the cwd arm caught.
    assert str(launch.resolve()) in str(raised.value)
    names = [c.name for c in load_configs(project_root=served, user_config_paths=[])]
    assert "repo-server" not in names


# --------------------------------------------------------------------------- #
# SL-8: serving a SUBDIRECTORY of a checkout must judge the whole checkout.
# --------------------------------------------------------------------------- #


def test_serving_a_subdirectory_refuses_a_store_resident_in_the_enclosing_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``serve --project <checkout>/app`` must refuse a store resident in <checkout>.

    The reopened subdirectory hole. SL-7 bound the served root but only appended
    it *verbatim*, so the guard judged residency against ``<checkout>/app`` alone
    and never walked up to ``<checkout>``. A store resident in the enclosing
    checkout -- not in the served subdirectory -- that approves
    ``<checkout>/app/.mcp.json`` was therefore accepted, and the repository
    self-approved (EC-TRUST-5, subdirectory-dependent; Consiliency/pmcp#251,
    #230).

    The served dir carries its own ``.mcp.json`` -- that is the payload -- so the
    enclosing checkout must be reached by walking up from the served root's
    *parent*: ``find_project_root(<checkout>/app)`` stops at the served dir's own
    marker and would discover nothing above it. The store here holds a genuine,
    matching ``approved`` record, so the refusal is attributable to residency and
    not to an absent or stale record.
    """
    checkout = _checkout(tmp_path)
    app = checkout / "app"
    app.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # The payload the served subdirectory ships, and the store -- resident in the
    # ENCLOSING checkout, not in the served subdir -- that approves it.
    app_config = app / ".mcp.json"
    app_config.write_text(_PWNED, encoding="utf-8")
    store = _ship_an_approval_inside(checkout, app_config, outside, monkeypatch)
    assert not store.is_relative_to(app.resolve())

    # Launched from outside, serving the subdirectory.
    monkeypatch.chdir(outside)
    trust_store.set_active_project_root(app)

    # Refused for residency in the ENCLOSING checkout -- named, not answered as
    # "no record".
    with pytest.raises(TrustStoreError) as raised:
        trust_store.trust_store_path()
    assert str(checkout.resolve()) in str(raised.value)
    assert trust_store.is_approved(app_config, app_config.read_bytes()) is False

    names = [c.name for c in load_configs(project_root=app, user_config_paths=[])]
    assert "repo-server" not in names


def test_a_bare_serve_from_a_subdirectory_refuses_a_store_in_the_enclosing_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cwd arm must walk up too: no served root, cwd is a checkout SUBDIR.

    With nothing bound -- a bare ``pmcp serve`` (no ``--project``) or any
    ``pmcp trust`` verb -- residency rests entirely on the cwd walk. When cwd is
    ``<checkout>/app`` and that subdirectory carries its own ``.mcp.json`` (the
    payload), ``find_project_root(cwd)`` stops at ``<checkout>/app`` and, before
    this fix, never reached the enclosing ``<checkout>``. A store planted in the
    enclosing checkout was therefore accepted and the repository self-approved
    ``<checkout>/app/.mcp.json`` from the everyday developer working directory
    (EC-TRUST-5, cwd-subdirectory; Consiliency/pmcp#251, #230). This is the
    sibling of the served-subdirectory hole on the ``_active_project_root is
    None`` path; the mutation that reverts the cwd arm to a single
    ``find_project_root(cwd)`` must turn it RED.
    """
    checkout = _checkout(tmp_path)
    app = checkout / "app"
    app.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    app_config = app / ".mcp.json"
    app_config.write_text(_PWNED, encoding="utf-8")
    # The store lives in the ENCLOSING checkout (checkout/home), not in the
    # served subdir; it holds a genuine, matching approval for the payload.
    store = _ship_an_approval_inside(checkout, app_config, outside, monkeypatch)
    assert not store.is_relative_to(app.resolve())

    # No served root bound; the process simply runs from the subdirectory.
    monkeypatch.chdir(app)
    assert trust_store._active_project_root is None

    with pytest.raises(TrustStoreError) as raised:
        trust_store.trust_store_path()
    assert str(checkout.resolve()) in str(raised.value)
    assert trust_store.is_approved(app_config, app_config.read_bytes()) is False

    names = [c.name for c in load_configs(project_root=app, user_config_paths=[])]
    assert "repo-server" not in names


# --------------------------------------------------------------------------- #
# A served root outside any checkout: the served root itself stays a boundary.
# --------------------------------------------------------------------------- #


def test_serving_a_dir_outside_any_checkout_refuses_a_store_resident_in_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The served root stays a boundary when it lies inside no checkout.

    ``find_project_root`` finds no enclosing git checkout above a bare served
    directory, so the walk-up arm adds nothing here -- the refusal rests entirely
    on the served root being kept *verbatim*. A store resident in the served dir
    itself must still be refused, or a project served from outside any checkout
    could self-approve. (This is the case the mutation -- dropping only the
    walk-up arm -- must leave green: the verbatim served root is untouched.)
    """
    served = tmp_path / "served-plain"
    served.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    config = served / ".mcp.json"
    config.write_text(_PWNED, encoding="utf-8")
    # A real, matching approval in a store resident in the served dir itself.
    store = _ship_an_approval_inside(served, config, outside, monkeypatch)
    assert store.is_relative_to(served.resolve())

    monkeypatch.chdir(outside)
    trust_store.set_active_project_root(served)

    with pytest.raises(TrustStoreError) as raised:
        trust_store.trust_store_path()
    assert str(served.resolve()) in str(raised.value)
    assert trust_store.is_approved(config, config.read_bytes()) is False
    names = [c.name for c in load_configs(project_root=served, user_config_paths=[])]
    assert "repo-server" not in names


def test_serving_a_dir_outside_any_checkout_loads_an_operator_store_outside_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Operator control for the outside-any-checkout case: a store outside loads.

    Binding a served root that lies in no checkout must not turn a legitimate
    operator store -- living outside the served dir -- into a refusal. The
    residency rule binds where the store *lives*, and this store lives nowhere
    near the served directory.
    """
    served = tmp_path / "served-plain"
    served.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    operator_home = tmp_path / "operator-home"
    operator_home.mkdir()
    config = served / ".mcp.json"
    config.write_text(_PWNED, encoding="utf-8")

    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.chdir(outside)
    trust_store.record(config, config.read_bytes(), "project", trust_store.APPROVED)

    trust_store.set_active_project_root(served)

    store = trust_store.trust_store_path()
    assert not store.is_relative_to(served.resolve())
    assert trust_store.is_approved(config, config.read_bytes()) is True
    granted = [c.name for c in load_configs(project_root=served, user_config_paths=[])]
    assert "repo-server" in granted


# --------------------------------------------------------------------------- #
# The CLI verbs keep using cwd (no served root is ever bound for them).
# --------------------------------------------------------------------------- #


def test_trust_approve_verb_inside_a_checkout_uses_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``pmcp trust approve`` run inside a checkout still works -- required.

    The verb binds no served root, so residency is judged from cwd alone.
    Running it from inside a checkout, with a normal operator home OUTSIDE the
    checkout, must approve the file -- exactly the everyday case. Driven through
    the real CLI handler, which proves the verb path never touches the served
    root.
    """
    checkout = _checkout(tmp_path)
    operator_home = tmp_path / "operator-home"
    operator_home.mkdir()
    config = _repo_mcp_json(checkout)

    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.chdir(checkout)

    cli._run_trust_approve(argparse.Namespace(path=str(config)))

    # The verb never bound a served root ...
    assert trust_store._active_project_root is None
    # ... and it recorded a usable approval from inside the checkout.
    out = capsys.readouterr().out
    assert f"Approved {config.resolve()}" in out
    assert trust_store.is_approved(config, config.read_bytes()) is True


def test_trust_approve_verb_still_refuses_a_checkout_resident_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cwd arm is unchanged: a store inside cwd's checkout is still refused.

    ``pmcp trust approve`` cannot be used to write into a checkout-resident
    store, because ``record`` resolves through ``trust_store_path``, which the
    cwd walk refuses. This is the pre-existing behaviour the fix must not erode.
    """
    checkout = _checkout(tmp_path)
    store_home = checkout / "home"
    store_home.mkdir()
    config = _repo_mcp_json(checkout)

    monkeypatch.setenv("HOME", str(store_home))
    monkeypatch.chdir(checkout)

    # `record` resolves through `trust_store_path`, whose cwd arm refuses the
    # checkout-resident store; the verb handler lets that error propagate (the
    # dispatcher, `run_trust`, is what maps it onto a non-zero exit).
    with pytest.raises(TrustStoreError) as raised:
        cli._run_trust_approve(argparse.Namespace(path=str(config)))
    assert str(checkout.resolve()) in str(raised.value)


# --------------------------------------------------------------------------- #
# The wiring: the `run_status` CLI handler actually binds the served root.
# --------------------------------------------------------------------------- #


async def test_run_status_binds_the_served_root_and_refuses_the_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Drive the real ``run_status`` handler, so the ``cli.py`` binding is exercised.

    The mechanism tests set the served root themselves; this one does not -- it
    calls ``run_status(--project checkout)`` from outside the checkout and
    relies on the handler to bind the root. If the binding line were deleted,
    the cwd walk would find no checkout, the checkout-resident store would grant,
    and ``repo-server`` would appear. It must not. The handler also clears the
    binding in its ``finally``.
    """
    checkout = _checkout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    config = _repo_mcp_json(checkout)
    _ship_an_approval_inside(checkout, config, outside, monkeypatch)
    monkeypatch.chdir(outside)

    args = argparse.Namespace(
        json=False,
        server=None,
        pending=False,
        verbose=False,
        probe=False,
        project=checkout,
        config=None,
        policy=None,
        log_level="warn",
    )
    with patch(
        "pmcp.cli._query_running_gateway_status", new=AsyncMock(return_value=None)
    ):
        await cli.run_status(args)

    out = capsys.readouterr().out
    assert "repo-server" not in out
    assert "No MCP servers configured" in out
    # The one-shot handler cleared the binding it set.
    assert trust_store._active_project_root is None
