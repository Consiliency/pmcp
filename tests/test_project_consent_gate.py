"""Tests for the project-source consent gate (Consiliency/pmcp#230, IF-0-CONSENT-1).

The gate is the single decision surface every project-source loader calls. Its
whole value is that no loader hashes, reads twice, or formats a refusal on its
own, so these tests assert the properties a loader is *relying* on:

* absence is never assent, and a post-approval edit is never assent either;
* **every** failure -- an unreadable source, a store that raises -- is a
  refusal, because a caller might read an exception as permission;
* ``read_and_gate`` opens the path **exactly once**, which is the entire TOCTOU
  defence: a gate that read once and let the caller re-open would be checking
  bytes nobody ever parses.

Store isolation comes from the autouse fixture in ``tests/conftest.py``; no test
here (or in any sibling lane) may touch the developer's real ``~/.config/pmcp``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import subprocess
import sys

import pytest

from pmcp import trust_store
from pmcp.project_consent import (
    ConsentDecision,
    ProjectSourceKind,
    gate_bytes,
    log_refusal,
    read_and_gate,
)

KINDS: tuple[ProjectSourceKind, ...] = (
    "project_manifest",
    "project_mcp_json",
    "project_policy",
)

#: The closed reason vocabulary IF-0-CONSENT-1 freezes. A reason outside it is a
#: contract break even when the boolean happens to be right: SL-2/3/4 branch on
#: these strings, and a fifth value would reach them as an unhandled case.
REASONS = {"approved", "no_record", "content_changed", "unreadable"}


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A project-supplied file, in a checkout-shaped directory."""
    path = tmp_path / "checkout" / ".pmcp" / "manifest.yaml"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"servers:\n  github:\n    command: npx\n")
    return path


def _approve(path: Path) -> None:
    """Approve exactly the bytes currently on disk, as an operator would."""
    trust_store.record(path, path.read_bytes(), "project", trust_store.APPROVED)


def test_an_unrecorded_path_is_refused() -> None:
    """No record is a refusal -- absence is never assent."""
    # Built here rather than from the fixture so every kind gets its own file.
    for kind in KINDS:
        path = Path(__file__).parent / "does-not-matter.yaml"
        decision = gate_bytes(path, b"anything at all", kind)

        assert decision.allowed is False
        assert decision.reason == "no_record"
        assert decision.kind == kind
        assert decision.remediation == f"pmcp trust approve {path.resolve()}"


def test_an_unrecorded_path_is_refused_by_read_and_gate(source: Path) -> None:
    """The read-and-gate entry point refuses an unrecorded file too."""
    content, decision = read_and_gate(source, "project_manifest")

    assert content is None
    assert decision.allowed is False
    assert decision.reason == "no_record"


def test_content_change_after_approval_is_not_approved(source: Path) -> None:
    """Approval binds bytes, not a path: one appended byte revokes it."""
    _approve(source)

    content, decision = read_and_gate(source, "project_manifest")
    assert decision.allowed is True
    assert decision.reason == "approved"
    assert decision.remediation == ""
    assert content == source.read_bytes()

    source.write_bytes(source.read_bytes() + b"\n")

    content, decision = read_and_gate(source, "project_manifest")
    assert content is None
    assert decision.allowed is False
    # Distinguished from `no_record` on purpose: an operator who approved this
    # file and then sees a refusal needs to know the file changed under them,
    # not that their approval never landed.
    assert decision.reason == "content_changed"
    assert decision.remediation == f"pmcp trust approve {source.resolve()}"


def test_an_unreadable_source_is_refused_not_raised(tmp_path: Path) -> None:
    """An unreadable path is a refusal, never an exception."""
    missing = tmp_path / "checkout" / ".mcp.json"
    a_directory = tmp_path / "checkout" / "dir.json"
    a_directory.mkdir(parents=True)

    for path in (missing, a_directory):
        content, decision = read_and_gate(path, "project_mcp_json")

        assert content is None
        assert decision.allowed is False
        assert decision.reason == "unreadable"
        assert decision.remediation == f"pmcp trust approve {path.resolve()}"


def test_a_store_error_is_refused_not_raised(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any exception out of the trust store is a refusal.

    ``is_approved`` promises never to raise, so this is defence in depth: the
    gate must not rely on that promise, because a caller that saw the exception
    would have been granted trust by a broken store.
    """
    _approve(source)  # would be allowed if the store answered

    def explode(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("trust store is on fire")

    monkeypatch.setattr(trust_store, "is_approved", explode)
    monkeypatch.setattr(trust_store, "list_records", explode)

    content, decision = read_and_gate(source, "project_manifest")

    assert content is None
    assert decision.allowed is False
    assert decision.reason in REASONS
    assert decision.remediation == f"pmcp trust approve {source.resolve()}"

    assert gate_bytes(source, source.read_bytes(), "project_manifest").allowed is False


def test_read_and_gate_opens_the_path_exactly_once(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The TOCTOU guard: one read, gated, returned.

    Counting is filtered to the target because the trust store opens its own
    file during the same call. Only ``Path.open`` is counted -- ``read_bytes``
    goes through it, so counting both would score one logical read as two and
    the assertion would be untrue of a correct implementation.
    """
    _approve(source)
    target = source.resolve()
    opens: list[Path] = []
    real_open = Path.open

    def counting_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.resolve() == target:
            opens.append(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)

    content, decision = read_and_gate(source, "project_manifest")

    assert decision.allowed is True
    assert content == b"servers:\n  github:\n    command: npx\n"
    assert len(opens) == 1, f"expected exactly one read of {target}, got {len(opens)}"


def test_read_and_gate_returns_none_bytes_when_refused(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bytes are returned only when allowed -- every refusal returns ``None``.

    A gate that returned the bytes alongside ``allowed=False`` would let a
    caller that checked the wrong field parse a file the operator refused.
    """
    content, decision = read_and_gate(source, "project_manifest")
    assert decision.allowed is False and content is None

    _approve(source)
    content, decision = read_and_gate(source, "project_manifest")
    assert decision.allowed is True and content == source.read_bytes()

    monkeypatch.setattr(trust_store, "is_approved", lambda *_a, **_k: False)
    content, decision = read_and_gate(source, "project_manifest")
    assert decision.allowed is False and content is None


def test_remediation_is_the_absolute_path_trust_approve_command(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal names a command an operator can paste, from any cwd.

    A relative path would name a command whose meaning depends on where the
    operator happens to stand, so the gate always resolves.
    """
    monkeypatch.chdir(source.parent)
    decision = gate_bytes(Path("manifest.yaml"), b"contents", "project_manifest")

    assert decision.allowed is False
    prefix = "pmcp trust approve "
    assert decision.remediation.startswith(prefix)
    named = Path(decision.remediation[len(prefix) :])
    assert named.is_absolute()
    assert named == source.resolve()
    assert decision.path == source.resolve()

    _approve(source)
    allowed = gate_bytes(source, source.read_bytes(), "project_manifest")
    assert allowed.allowed is True
    # Empty only when allowed: there is nothing to remediate.
    assert allowed.remediation == ""


def test_log_refusal_emits_one_warning_naming_the_remediation(
    source: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One WARNING per refusal, carrying the runnable command."""
    logger = logging.getLogger("pmcp.test.consent")
    _content, decision = read_and_gate(source, "project_manifest")

    with caplog.at_level(logging.WARNING, logger="pmcp.test.consent"):
        log_refusal(decision, logger)

    records = [rec for rec in caplog.records if rec.name == "pmcp.test.consent"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert f"pmcp trust approve {source.resolve()}" in message
    assert str(source.resolve()) in message

    caplog.clear()
    _approve(source)
    _content, allowed = read_and_gate(source, "project_manifest")
    with caplog.at_level(logging.WARNING, logger="pmcp.test.consent"):
        log_refusal(allowed, logger)
    assert [rec for rec in caplog.records if rec.name == "pmcp.test.consent"] == []


def test_consent_decision_is_frozen(source: Path) -> None:
    """The decision a loader branches on cannot be edited after the fact."""
    decision = gate_bytes(source, b"contents", "project_manifest")

    assert isinstance(decision, ConsentDecision)
    with pytest.raises(Exception):  # noqa: B017 - dataclasses raises FrozenInstanceError
        decision.allowed = True  # type: ignore[misc]


@pytest.mark.parametrize(
    "module",
    ["pmcp.config.loader", "pmcp.trust_store", "pmcp.project_consent"],
)
def test_each_consent_module_imports_first_in_a_clean_interpreter(module: str) -> None:
    """No import cycle, whichever of the three is imported first.

    CONSENT made `pmcp.config.loader` import `pmcp.project_consent`, which imports
    `pmcp.trust_store`, which imported `pmcp.config.loader` at module scope — a
    cycle that made `import pmcp.config.loader` fail outright in a clean
    interpreter.

    The suite could not see it: `tests/conftest.py` imports `trust_store` at
    collection time, so by the time any test reaches `config.loader` the cycle is
    already resolved. It surfaced only in a subprocess that imported
    `config.loader` first. Hence a real subprocess per module here rather than an
    in-process import, which would prove nothing.
    """
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"importing {module} first failed:\n{result.stderr}"
