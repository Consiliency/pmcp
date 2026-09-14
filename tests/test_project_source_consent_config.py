"""The project ``.mcp.json`` applies only with the operator's consent (SL-3).

A checkout's own ``.mcp.json`` is repository-supplied: cloning a repository and
running PMCP inside it currently lets that repository declare servers, force
them to auto-start, and opt the registry into private packages -- all without
the operator ever seeing the file. That is EC-CONSENT-2 (Consiliency/pmcp#230).

Two things make this suite specific rather than decorative:

* **Five readers, not one.** The roadmap phrases the criterion in the singular
  ("an unapproved project ``.mcp.json`` is not applied"), which invites gating
  ``load_configs`` and calling it done. ``config/loader.py`` reads the project
  file at five independent sites, so there is one test per *reader*: a gate on
  one reader is not a gate.
* **Scope is the whole point.** The operator's own ``~/.mcp.json`` and an
  explicitly-supplied ``--config``/``$PMCP_CONFIG`` path are the operator
  speaking, not the repository. Gating those would break every working setup and
  fail EC-CONSENT-5, so two tests assert they still apply with no trust record
  whatsoever.

``isolate_trust_store`` (autouse, `tests/conftest.py`) redirects ``HOME`` so
nothing here can touch the developer's real store, and it deliberately approves
nothing -- every approval below is recorded explicitly by the test that needs
it, the way an operator running ``pmcp trust approve`` would.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from pmcp.config.loader import (
    load_config_sources,
    load_configs,
    load_disabled_auto_start,
    load_enabled_auto_start,
    registry_allow_private_from_config,
)

PROJECT_CONFIG = {
    "mcpServers": {"repo-server": {"command": "node", "args": ["evil.js"]}},
    "autoStart": ["repo-server"],
    "disableAutoStart": ["operator-server"],
    "allowPrivateRegistry": True,
}


@pytest.fixture(autouse=True)
def _no_ambient_custom_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """``$PMCP_CONFIG`` would add a fourth, ungated source to every reader."""
    monkeypatch.delenv("PMCP_CONFIG", raising=False)


def write_project_config(
    project_root: Path, data: dict[str, object] | None = None
) -> Path:
    """Write a project ``.mcp.json`` declaring every signal the readers consume."""
    path = project_root / ".mcp.json"
    path.write_text(json.dumps(data if data is not None else PROJECT_CONFIG))
    return path


def expected_remediation(path: Path) -> str:
    """The exact command a refusal must name, as the operator would run it."""
    return f"pmcp trust approve {path.resolve()}"


def warnings_from(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]


# --- Each of the five project read sites behaves as if the file were absent. ---


def test_unapproved_project_mcp_json_is_not_applied_by_load_configs(
    tmp_path: Path,
) -> None:
    write_project_config(tmp_path)

    configs = load_configs(project_root=tmp_path, user_config_paths=[])

    assert configs == []


def test_unapproved_project_mcp_json_is_not_applied_by_load_config_sources(
    tmp_path: Path,
) -> None:
    write_project_config(tmp_path)

    sources = load_config_sources(project_root=tmp_path, user_config_paths=[])

    project = next(source for source in sources if source.source == "project")
    # The row still names the path it refused -- an operator needs to see
    # which file was ignored -- but contributes no parsed content at all.
    assert project.config is None
    assert project.raw_data is None


def test_unapproved_project_mcp_json_is_not_applied_by_load_enabled_auto_start(
    tmp_path: Path,
) -> None:
    write_project_config(tmp_path)

    enabled = load_enabled_auto_start(project_root=tmp_path, user_config_paths=[])

    assert enabled == set()


def test_unapproved_project_mcp_json_is_not_applied_by_load_disabled_auto_start(
    tmp_path: Path,
) -> None:
    write_project_config(tmp_path)

    disabled = load_disabled_auto_start(project_root=tmp_path, user_config_paths=[])

    assert disabled == set()


def test_unapproved_project_mcp_json_does_not_set_allow_private_registry(
    tmp_path: Path,
) -> None:
    """The fifth reader, which builds its own candidate list.

    ``registry_allow_private_from_config`` does not share
    ``_iter_config_source_paths``; it assembles project/user/custom itself.
    ``None`` means "no config stated a preference", which is what an absent
    project file yields -- so the repository cannot flip the private-registry
    opt-in on the operator's behalf.
    """
    write_project_config(tmp_path)

    allowed = registry_allow_private_from_config(
        project_root=tmp_path, user_config_paths=[]
    )

    assert allowed is None


def test_unapproved_project_mcp_json_skip_logs_the_trust_approve_command(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = write_project_config(tmp_path)

    with caplog.at_level(logging.WARNING, logger="pmcp.project_consent"):
        load_configs(project_root=tmp_path, user_config_paths=[])

    messages = warnings_from(caplog)
    assert any(expected_remediation(path) in message for message in messages), (
        f"no refusal named the remediation command; saw {messages}"
    )


# --- Consent is per-bytes: approving a file is not approving the path. ---


def test_approved_project_mcp_json_is_applied(
    tmp_path: Path, approve_project_file: Callable[[Path], None]
) -> None:
    path = write_project_config(tmp_path)
    approve_project_file(path)

    configs = load_configs(project_root=tmp_path, user_config_paths=[])
    enabled = load_enabled_auto_start(project_root=tmp_path, user_config_paths=[])
    disabled = load_disabled_auto_start(project_root=tmp_path, user_config_paths=[])
    sources = load_config_sources(project_root=tmp_path, user_config_paths=[])
    allowed = registry_allow_private_from_config(
        project_root=tmp_path, user_config_paths=[]
    )

    assert [config.name for config in configs] == ["repo-server"]
    assert configs[0].source == "project"
    assert enabled == {"repo-server"}
    assert disabled == {"operator-server"}
    assert allowed is True
    project = next(source for source in sources if source.source == "project")
    assert project.config is not None
    assert project.error is None


def test_editing_an_approved_project_mcp_json_revokes_it(
    tmp_path: Path,
    approve_project_file: Callable[[Path], None],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Approval is recorded against a digest, so an edit withdraws it.

    This is the attack the gate exists to stop: get a benign file approved,
    then rewrite it. The reader must refuse the *edited* bytes even though
    the path is still the one the operator approved.
    """
    path = write_project_config(tmp_path)
    approve_project_file(path)
    assert [config.name for config in load_configs(tmp_path, [])] == ["repo-server"]

    write_project_config(
        tmp_path,
        {"mcpServers": {"swapped-in": {"command": "sh", "args": ["-c", "pwn"]}}},
    )

    with caplog.at_level(logging.WARNING, logger="pmcp.project_consent"):
        configs = load_configs(project_root=tmp_path, user_config_paths=[])

    assert configs == []
    assert any(
        expected_remediation(path) in message for message in warnings_from(caplog)
    )


def test_an_approved_project_mcp_json_is_read_exactly_once(
    tmp_path: Path, approve_project_file: Callable[[Path], None]
) -> None:
    """TOCTOU guard: the bytes that were gated are the bytes that are parsed.

    A reader that gates one read and then re-opens the path is applying
    content nobody approved -- the file can change in between. Counting the
    reads is the only way to keep a later refactor from quietly
    reintroducing that window. ``load_config_sources`` is the sharpest case:
    today it reads the same path twice (once for ``raw_data``, once to
    parse).
    """
    path = write_project_config(tmp_path)
    approve_project_file(path)
    counts = count_reads_of(path)

    with counts as reads:
        load_configs(project_root=tmp_path, user_config_paths=[])
    assert reads.total == 1, f"load_configs read the project file {reads.total}x"

    with count_reads_of(path) as reads:
        load_config_sources(project_root=tmp_path, user_config_paths=[])
    assert reads.total == 1, f"load_config_sources read the project file {reads.total}x"


# --- EC-CONSENT-5: user and custom scope are the operator, not the repository. ---


def test_user_mcp_json_is_applied_without_any_trust_record(tmp_path: Path) -> None:
    user_path = tmp_path / "user.mcp.json"
    user_path.write_text(
        json.dumps(
            {
                "mcpServers": {"user-server": {"command": "echo"}},
                "autoStart": ["user-server"],
                "disableAutoStart": ["other"],
                "allowPrivateRegistry": True,
            }
        )
    )

    configs = load_configs(project_root=None, user_config_paths=[user_path])
    enabled = load_enabled_auto_start(project_root=None, user_config_paths=[user_path])
    disabled = load_disabled_auto_start(
        project_root=None, user_config_paths=[user_path]
    )
    allowed = registry_allow_private_from_config(user_config_paths=[user_path])

    assert [config.name for config in configs] == ["user-server"]
    assert enabled == {"user-server"}
    assert disabled == {"other"}
    assert allowed is True


def test_custom_config_path_is_applied_without_any_trust_record(tmp_path: Path) -> None:
    """An explicitly-supplied path is the operator naming a file by hand.

    The same bytes in the same directory: gated as ``project``, ungated as
    ``custom``. Scope, not location, decides.
    """
    custom_path = tmp_path / ".mcp.json"
    custom_path.write_text(
        json.dumps(
            {
                "mcpServers": {"custom-server": {"command": "echo"}},
                "autoStart": ["custom-server"],
                "disableAutoStart": ["other"],
                "allowPrivateRegistry": True,
            }
        )
    )

    configs = load_configs(
        project_root=None, user_config_paths=[], custom_config_path=custom_path
    )
    enabled = load_enabled_auto_start(
        project_root=None, user_config_paths=[], custom_config_path=custom_path
    )
    disabled = load_disabled_auto_start(
        project_root=None, user_config_paths=[], custom_config_path=custom_path
    )
    allowed = registry_allow_private_from_config(
        user_config_paths=[], custom_config_path=custom_path
    )

    assert [config.name for config in configs] == ["custom-server"]
    assert enabled == {"custom-server"}
    assert disabled == {"other"}
    assert allowed is True


# --- A refused project file removes itself; it must not disable the rest. ---


def test_a_refused_project_config_falls_through_to_the_user_config(
    tmp_path: Path,
) -> None:
    """Project precedence is forfeited, not inherited.

    ``load_configs`` gives project entries priority over same-named user
    entries. Once the project file is refused, the user's own definition of
    that server must win -- a gate that merely *emptied* the project entry
    while keeping its claim on the name would leave the operator with no
    server at all.
    """
    write_project_config(tmp_path, {"mcpServers": {"shared": {"command": "repo-cmd"}}})
    user_path = tmp_path / "user.mcp.json"
    user_path.write_text(
        json.dumps({"mcpServers": {"shared": {"command": "operator-cmd"}}})
    )

    configs = load_configs(project_root=tmp_path, user_config_paths=[user_path])

    assert len(configs) == 1
    assert configs[0].source == "user"
    assert configs[0].config.type == "local"
    assert configs[0].config.command == "operator-cmd"


def test_a_refused_project_config_does_not_block_the_user_private_registry_optin(
    tmp_path: Path,
) -> None:
    write_project_config(tmp_path, {"allowPrivateRegistry": False})
    user_path = tmp_path / "user.mcp.json"
    user_path.write_text(json.dumps({"allowPrivateRegistry": True}))

    allowed = registry_allow_private_from_config(
        project_root=tmp_path, user_config_paths=[user_path]
    )

    assert allowed is True


def test_an_absent_project_mcp_json_warns_about_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The common case must stay silent.

    Most projects have no ``.mcp.json``. A gate that warned about every
    missing one would emit a refusal on nearly every startup and train
    operators to ignore the message that matters.
    """
    user_path = tmp_path / "user.mcp.json"
    user_path.write_text(json.dumps({"mcpServers": {"u": {"command": "echo"}}}))

    with caplog.at_level(logging.WARNING):
        load_configs(project_root=tmp_path, user_config_paths=[user_path])
        load_config_sources(project_root=tmp_path, user_config_paths=[user_path])
        load_enabled_auto_start(project_root=tmp_path, user_config_paths=[])
        load_disabled_auto_start(project_root=tmp_path, user_config_paths=[])

    assert warnings_from(caplog) == []


class _ReadCounter:
    """Counts every read of one path, whatever API performs it."""

    def __init__(self, target: Path) -> None:
        self.target = target.resolve()
        self.total = 0

    def note(self, path: Path) -> None:
        try:
            resolved = Path(path).resolve()
        except OSError:  # pragma: no cover - defensive
            return
        if resolved == self.target:
            self.total += 1


class count_reads_of:  # noqa: N801 - used as a context manager, reads as one
    """Tally how many times one path is opened.

    ``Path.open`` is the single choke point: ``read_bytes`` and ``read_text``
    both go through it, so counting it catches a re-read however it is spelled
    -- and counts each one exactly once, which patching all three would not.
    """

    def __init__(self, target: Path) -> None:
        self._counter = _ReadCounter(target)
        self._patch = pytest.MonkeyPatch()

    def __enter__(self) -> _ReadCounter:
        counter = self._counter
        original = Path.open

        def counting_open(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            counter.note(self)
            return original(self, *args, **kwargs)

        self._patch.setattr(Path, "open", counting_open)
        return counter

    def __exit__(self, *exc: object) -> None:
        self._patch.undo()
